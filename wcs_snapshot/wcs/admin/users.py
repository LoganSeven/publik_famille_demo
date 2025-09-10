# w.c.s. - web application for online forms
# Copyright (C) 2005-2010  Entr'ouvert
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, see <http://www.gnu.org/licenses/>.

from quixote import get_publisher, get_request, get_response, get_session, redirect
from quixote.directory import Directory
from quixote.html import TemplateIO, htmltext

import wcs.sql_criterias as st
from wcs.backoffice.pagination import pagination_links
from wcs.qommon import _, errors, force_str, get_cfg, ident, misc, template
from wcs.qommon.form import (
    CheckboxWidget,
    EmailWidget,
    Form,
    HtmlWidget,
    SingleSelectWidget,
    StringWidget,
    WidgetList,
)
from wcs.qommon.ident.idp import is_idp_managing_user_attributes, is_idp_managing_user_roles


class UserUI:
    def __init__(self, user):
        self.user = user

    def form(self):
        ident_methods = get_cfg('identification', {}).get('methods', [])
        users_cfg = get_cfg('users', {})

        form = Form(enctype='multipart/form-data')
        # do not display user attribute fields if the site has been set to get
        # them filled by SAML requests
        if not is_idp_managing_user_attributes():
            formdef = get_publisher().user_class.get_formdef()
            if not formdef or not get_publisher().has_user_fullname_config():
                form.add(StringWidget, 'name', title=_('Name'), required=True, size=30, value=self.user.name)
            if not formdef or not users_cfg.get('field_email'):
                form.add(
                    EmailWidget, 'email', title=_('Email'), required=False, size=30, value=self.user.email
                )
            if formdef:
                formdef.add_fields_to_form(form, form_data=self.user.form_data)
            form.add(CheckboxWidget, 'is_admin', title=_('Administrator account'), value=self.user.is_admin)

        roles = list(get_publisher().role_class.select(order_by='name'))
        if roles and not is_idp_managing_user_roles():
            form.add(
                WidgetList,
                'roles',
                title=_('Roles'),
                element_type=SingleSelectWidget,
                value=self.user.roles,
                add_element_label=_('Add Role'),
                element_kwargs={
                    'render_br': False,
                    'options': [(None, '---', None)]
                    + [(x.id, x.name, x.id) for x in roles if not x.is_internal()],
                },
            )

        for klass in [x for x in ident.get_method_classes() if x.key in ident_methods]:
            if klass.method_admin_widget:
                value = klass().get_value(self.user)
                form.add(klass.method_admin_widget, 'method_%s' % klass.key, required=False, value=value)

        form.add_submit('submit', _('Submit'))
        form.add_submit('cancel', _('Cancel'))
        return form

    def submit_form(self, form):
        if not self.user:
            self.user = get_publisher().user_class()
        for f in ('name', 'email', 'is_admin', 'roles'):
            widget = form.get_widget(f)
            if widget:
                setattr(self.user, f, widget.parse())
        if not is_idp_managing_user_attributes():
            formdef = get_publisher().user_class.get_formdef()
            if formdef:
                data = formdef.get_data(form)
                self.user.set_attributes_from_formdata(data)
                self.user.form_data = data

        # user is stored first so it get an id; necessary for some ident
        # methods
        self.user.store()

        ident_methods = get_cfg('identification', {}).get('methods', [])
        for klass in [x for x in ident.get_method_classes() if x.key in ident_methods]:
            widget = form.get_widget('method_%s' % klass.key)
            if widget:
                klass().submit(self.user, widget)

            # XXX: and store!
            # XXX 2: but pay attention to errors set on widget (think
            # "duplicate username") (and the calling method will also
            # have to check this)


class UserPage(Directory):
    _q_exports = ['', 'edit', 'delete', 'token']

    def __init__(self, component):
        self.user = get_publisher().user_class.get(component)
        self.user_ui = UserUI(self.user)
        get_response().breadcrumb.append((component + '/', self.user.display_name))

    def _q_index(self):
        get_response().set_title('%s - %s' % (_('User'), self.user.display_name))
        r = TemplateIO(html=True)
        r += htmltext('<div id="appbar">')
        r += htmltext('<h2>%s</h2>') % self.user.display_name
        r += htmltext('<span class="actions">')
        r += self.get_actions()
        r += htmltext('</span>')
        r += htmltext('</div>')
        users_cfg = get_cfg('users', {})

        if self.user.deleted_timestamp:
            r += htmltext('<div class="warningnotice">')
            r += str(
                _('Marked as deleted on %(date)s.')
                % {'date': misc.localstrftime(self.user.deleted_timestamp)}
            )
            r += htmltext('</div>')

        r += htmltext('<div class="splitcontent-left">')

        r += htmltext('<div class="bo-block">')
        r += htmltext('<h3>%s</h3>') % _('Profile')

        r += htmltext('<div class="form">')

        if not get_publisher().has_user_fullname_config():
            r += htmltext('<div class="title">%s</div>') % _('Name')
            r += htmltext('<div class="StringWidget content">%s</div>') % self.user.name

        if not users_cfg.get('field_email') and self.user.email:
            r += htmltext('<div class="title">%s</div>') % _('Email')
            r += htmltext('<div class="StringWidget content">%s</div>') % self.user.email

        formdef = self.user.get_formdef()
        if formdef:
            if self.user.form_data:
                for field in formdef.fields:
                    if not hasattr(field, 'get_view_value'):
                        continue
                    value = self.user.form_data.get(field.id)
                    r += htmltext('<div class="title">')
                    r += field.label
                    r += htmltext('</div>')
                    r += htmltext('<div class="StringWidget content">')
                    if value is None:
                        r += htmltext('<i>%s</i>') % _('Not set')
                    else:
                        r += field.get_view_value(value)
                    r += htmltext('</div>')

        r += htmltext('</div>')

        r += htmltext('</div>')  # bo-block
        r += htmltext('</div>')  # splitcontent-left

        r += htmltext('<div class="splitcontent-right">')
        if not self.user.is_active:
            r += htmltext('<div class="infonotice">%s</div>') % _('This user is not active.')
        r += htmltext('<div class="bo-block">')
        r += htmltext('<h3>%s</h3>') % _('Roles')

        if self.user.roles or self.user.is_admin:
            r += htmltext('<div class="form">')
            r += htmltext('<div class="StringWidget content"><ul>')
            if self.user.is_admin:
                r += htmltext('<li><strong>%s</strong></li>') % _('Administrator account')
            for k in self.user.roles or []:
                try:
                    r += htmltext('<li>%s</li>') % get_publisher().role_class.get(k).name
                except KeyError:
                    # removed role ?
                    r += htmltext('<li><em>')
                    r += str(_('Unknown role (%s)') % k)
                    r += htmltext('</em></li>')
            r += htmltext('</ul></div>')
            r += htmltext('</div>')

        r += htmltext('</div>')  # bo-block

        if self.user.lasso_dump:
            import lasso

            identity = lasso.Identity.newFromDump(self.user.lasso_dump)
            server = misc.get_lasso_server()
            if len(identity.providerIds) and server:
                r += htmltext('<div class="bo-block" id="saml-details">')
                r += htmltext('<h3>%s</h3>') % _('SAML Details')
                r += htmltext('<div class="StringWidget content"><ul>')
                for pid in identity.providerIds:
                    provider = server.getProvider(pid)
                    label = misc.get_provider_label(provider)
                    if label:
                        label = '%s (%s)' % (label, pid)
                    else:
                        label = pid
                    federation = identity.getFederation(pid)
                    r += htmltext('<li>')
                    r += str(_('Account federated with %s') % label)
                    r += htmltext('<br />')
                    if federation.localNameIdentifier:
                        r += str(_('local: ') + federation.localNameIdentifier.content)
                    if federation.remoteNameIdentifier:
                        r += str(_('remote: ') + federation.remoteNameIdentifier.content)
                    r += htmltext('</li>')
                r += htmltext('</ul></div>')

                if get_cfg('debug', {}).get('debug_mode', False):
                    r += htmltext('<h4>%s</h4>') % _('Lasso Identity Dump')
                    r += htmltext('<pre>%s</pre>') % self.user.lasso_dump
                r += htmltext('</div>')  # bo-block

        r += htmltext('</div>')  # splitcontent-right
        return r.getvalue()

    def get_actions(self):
        ident_methods = get_cfg('identification', {}).get('methods', [])
        r = TemplateIO(html=True)

        if is_idp_managing_user_attributes() and not is_idp_managing_user_roles():
            r += htmltext('<a href="edit">%s</a>') % _('Manage Roles')
        elif not (is_idp_managing_user_attributes() and is_idp_managing_user_roles()):
            r += htmltext('<a href="edit">%s</a>') % _('Edit')
        r += htmltext('<a href="delete" rel="popup">%s</a>') % _('Delete')

        for method in ident_methods:
            try:
                actions = ident.get_method_user_directory(method, self.user).get_actions()
            except AttributeError:
                continue
            for action_url, action_label in actions:
                r += htmltext('<a href="%s/%s">%s</a>') % (method, action_url, action_label)

        return r.getvalue()

    def edit(self):
        form = self.user_ui.form()
        if form.get_widget('cancel').parse():
            return redirect('.')

        if form.get_widget('roles') and form.get_widget('roles').get_widget('add_element').parse():
            form.clear_errors()
            display_form = True
        else:
            display_form = not form.is_submitted() or form.has_errors()

        if display_form:
            get_response().breadcrumb.append(('edit', _('Edit')))
            get_response().set_title(_('Edit User'))
            r = TemplateIO(html=True)
            r += htmltext('<h2>%s</h2>') % _('Edit User')
            r += form.render()
            return r.getvalue()

        self.user_ui.submit_form(form)
        return redirect('.')

    def delete(self):
        form = Form(enctype='multipart/form-data')
        form.widgets.append(HtmlWidget('<p>%s</p>' % _('You are about to irrevocably delete this user.')))
        form.add_submit('delete', _('Delete'))
        form.add_submit('cancel', _('Cancel'))
        if form.get_widget('cancel').parse():
            return redirect('.')
        if not form.is_submitted() or form.has_errors():
            get_response().breadcrumb.append(('delete', _('Delete')))
            get_response().set_title(_('Delete User'))
            r = TemplateIO(html=True)
            r += htmltext('<h2>%s %s</h2>') % (_('Deleting User:'), self.user.name)
            r += form.render()
            return r.getvalue()

        ident_methods = get_cfg('identification', {}).get('methods', [])
        for klass in [x for x in ident.get_method_classes() if x.key in ident_methods]:
            if hasattr(klass, 'delete'):
                klass().delete(self.user)
        self.user.remove_self()
        return redirect('..')

    def _q_lookup(self, component):
        ident_methods = get_cfg('identification', {}).get('methods', [])
        if component in ident_methods:
            get_response().breadcrumb.append((component + '/', None))
            return ident.get_method_user_directory(component, self.user)


class UsersDirectory(Directory):
    _q_exports = ['', 'new']

    def _q_traverse(self, path):
        get_response().breadcrumb.append(('users/', _('Users')))
        get_response().set_backoffice_section('users')
        return super()._q_traverse(path)

    def _q_index(self):
        get_response().set_title(_('Users'))
        r = TemplateIO(html=True)

        limit = int(
            get_request().form.get('limit', get_publisher().get_site_option('default-page-size') or 20)
        )
        offset = int(get_request().form.get('offset', 0))

        checked_roles = None
        if get_request().form.get('filter'):
            checked_roles = get_request().form.get('role', [])
            if isinstance(checked_roles, str):
                checked_roles = [checked_roles]

        if checked_roles:
            # optimize query by removing the roles criterias if they are all
            # checked
            possible_roles = ['admin', 'none']
            possible_roles.extend(get_publisher().role_class.keys())
            if set(possible_roles) == set(checked_roles):
                checked_roles = None

        criterias = [st.Null('deleted_timestamp')]

        total_count = get_publisher().user_class.count(criterias)

        # declarative criteria to only get checked roles
        if checked_roles:
            roles_criterias = []
            if 'admin' in checked_roles:
                roles_criterias.append(st.Equal('is_admin', True))
            if 'none' in checked_roles:
                roles_criterias.append(st.And([st.Equal('is_admin', False), st.Equal('roles', [])]))
            other_roles = [x for x in checked_roles if x not in ('admin', 'none')]
            if other_roles:
                roles_criterias.append(st.Intersects('roles', other_roles))
            criterias.append(st.Or(roles_criterias))

        query = get_request().form.get('q')
        if query:
            criterias.append(st.Or([st.ILike('name', query), st.ILike('email', query)]))

        if len(criterias) > 1:
            filtered_count = get_publisher().user_class.count(criterias)
            if filtered_count < offset:
                # reset offset if we are past the number of elements
                offset = 0
                get_request().form['offset'] = 0
        else:
            filtered_count = total_count

        users = get_publisher().user_class.select(
            order_by='name', clause=criterias, offset=offset, limit=limit
        )

        r += htmltext('<div id="listing">')
        r += htmltext('<ul>')
        r += htmltext('<li>%s %s</li>') % (_('Total number of users:'), total_count)

        if len(criterias) > 1:
            r += htmltext('<li>%s %s</li>') % (_('Number of filtered users:'), filtered_count)

        r += htmltext('</ul>')

        r += htmltext('<ul class="biglist">')
        for user in users:
            user_classes = ['biglistitem']
            if not user.is_active:
                user_classes.append('user-inactive')
            if user.is_admin:
                user_classes.append('user-is-admin')
            elif user.roles:
                user_classes.append('user-has-roles')
            else:
                user_classes.append('simple-user')
            r += htmltext('<li class="%s">' % ' '.join(user_classes))
            r += htmltext('<a class="biglistitem--content" href="%s/">%s</a>') % (
                user.id,
                user.display_name,
            )
            r += htmltext('</li>')
        r += htmltext('</ul>')

        r += pagination_links(offset, limit, filtered_count)
        r += htmltext('</div>')

        if get_request().form.get('ajax') == 'true':
            get_response().raw = True
            return r.getvalue()

        ident_methods = get_cfg('identification', {}).get('methods', [])

        r2 = TemplateIO(html=True)
        r2 += htmltext('<div id="appbar">')
        r2 += htmltext('<h2>%s</h2>') % _('Users')

        info_notice = None
        if not ident_methods:
            info_notice = _('An authentification system must be configured before creating users.')
        elif ident_methods == ['idp'] and len(get_cfg('idp', {}).items()) == 0:
            info_notice = _('SAML support must be setup before creating users.')
        else:
            # if attributes are managed by the identity provider, do not expose
            # the possibility to create users, as only the roles field would
            # be shown, and the creation would fail on missing fields.
            if not is_idp_managing_user_attributes():
                r2 += htmltext('<span class="actions">')
                r2 += htmltext('<a class="new-item" href="new">%s</a>') % _('New User')
                r2 += htmltext('</span>')
            get_response().filter['sidebar'] = self.get_sidebar(offset, limit)

        r2 += htmltext('</div>')

        if info_notice:
            r2 += htmltext('<div class="infonotice"><p>%s</p></div>') % info_notice

        r2 += r.getvalue()

        return r2.getvalue()

    def get_sidebar(self, offset=None, limit=None):
        r = TemplateIO(html=True)

        get_response().add_javascript(['wcs.listing.js'])
        r += htmltext('<form id="listing-settings">')

        if offset or limit:
            if not offset:
                offset = 0
            r += htmltext('<input type="hidden" name="offset" value="%s"/>') % offset

        if limit:
            r += htmltext('<input type="hidden" name="limit" value="%s"/>') % limit

        r += htmltext('<h3>%s</h3>') % _('Search')
        if get_request().form.get('q'):
            q = get_request().form.get('q')
            r += htmltext('<input name="q" value="%s">') % force_str(q)
        else:
            r += htmltext('<input name="q">')
        r += htmltext('<button>%s</button>') % _('Search')

        r += htmltext('<h3>%s</h3>') % _('Filter on Roles')
        r += htmltext('<input type="hidden" name="filter" value="true"/>')
        r += htmltext('<ul>')
        roles = [('admin', _('Administrator account'))]
        for role in get_publisher().role_class.select():
            roles.append((role.id, role.name))
        roles.append(('none', _('None')))

        checked_roles = get_request().form.get('role', [])
        if not checked_roles and not get_request().form.get('filter'):
            # take everything as default
            checked_roles = [str(x[0]) for x in roles]

        for role_id, role_title in roles:
            checked = ''
            if str(role_id) in checked_roles:
                checked = 'checked'
            r += htmltext('<li><label><input type="checkbox" name="role" value="%s"%s/>%s</label></li>') % (
                role_id,
                checked,
                role_title,
            )
        r += htmltext('</ul>')
        r += htmltext('<button>%s</button>') % _('Filter')
        r += htmltext('</form>')
        return r.getvalue()

    def new(self):
        get_response().breadcrumb.append(('new', _('New')))

        ident_methods = get_cfg('identification', {}).get('methods', [])
        if not ident_methods:
            return template.error_page(
                _('An authentification system must be configured before creating users.')
            )
        if ident_methods == ['idp'] and len(get_cfg('idp', {}).items()) == 0:
            return template.error_page(_('SAML support must be setup before creating users.'))
        if is_idp_managing_user_attributes():
            raise errors.TraversalError()

        # XXX: user must be logged in to get here
        user = get_publisher().user_class()
        user_ui = UserUI(user)
        first_user = not (get_publisher().user_class.exists())
        if first_user:
            user.is_admin = first_user
        form = user_ui.form()
        if form.get_widget('cancel').parse():
            return redirect('.')

        if not form.is_submitted() or form.has_errors():
            get_response().set_title(_('New User'))
            r = TemplateIO(html=True)
            r += htmltext('<h2>%s</h2>') % _('New User')
            r += form.render()
            return r.getvalue()

        user_ui.submit_form(form)
        if first_user:
            req = get_request()
            if req.user:
                user_ui.user.name_identifiers = req.user.name_identifiers
                user_ui.user.lasso_dump = req.user.lasso_dump
                user_ui.user.store()
            get_session().set_user(user_ui.user.id)
        return redirect('.')

    def _q_lookup(self, component):
        try:
            return UserPage(component)
        except KeyError:
            raise errors.TraversalError()
