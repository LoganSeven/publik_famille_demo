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

from quixote import get_publisher, get_response, redirect
from quixote.directory import Directory
from quixote.html import TemplateIO, htmltext

from wcs.formdef import FormDef
from wcs.qommon import _, errors, get_cfg
from wcs.qommon.form import CheckboxWidget, Form, HtmlWidget, StringWidget, TextWidget, WidgetList
from wcs.roles import get_user_roles


class RoleUI:
    def __init__(self, role):
        self.role = role
        if self.role is None:
            self.role = get_publisher().role_class()

    def get_form(self):
        form = Form(enctype='multipart/form-data')
        form.add(StringWidget, 'name', title=_('Role Name'), required=True, size=30, value=self.role.name)
        form.add(
            TextWidget,
            'details',
            title=_('Role Details'),
            required=False,
            cols=40,
            rows=5,
            value=self.role.details,
        )
        form.add(
            WidgetList,
            'emails',
            title=_('Role Emails'),
            element_type=StringWidget,
            value=self.role.emails,
            add_element_label=_('Add Email'),
            element_kwargs={'render_br': False, 'size': 30},
        )
        form.add(
            CheckboxWidget,
            'emails_to_members',
            title=_('Propage emails to all users holding the role'),
            value=self.role.emails_to_members,
        )
        form.add(
            CheckboxWidget,
            'allows_backoffice_access',
            title=_('Users holding the role can access to backoffice'),
            value=self.role.allows_backoffice_access,
        )
        form.add_submit('submit', _('Submit'))
        form.add_submit('cancel', _('Cancel'))
        return form

    def submit_form(self, form):
        if self.role:
            role = self.role
        else:
            role = get_publisher().role_class(name=form.get_widget('name').parse())

        name = form.get_widget('name').parse()
        role_names = [x.name for x in get_publisher().role_class.select() if x.id != role.id]
        if name in role_names:
            form.get_widget('name').set_error(_('This name is already used.'))
            raise ValueError()

        for f in ('name', 'details', 'emails_to_members', 'allows_backoffice_access'):
            setattr(role, f, form.get_widget(f).parse())
        role.emails = [x for x in form.get_widget('emails').parse() or [] if x]
        role.store()


class RolePage(Directory):
    _q_exports = ['', 'edit', 'delete']

    def __init__(self, component):
        try:
            self.role = get_publisher().role_class.get(component)
        except KeyError:
            raise errors.TraversalError()
        self.role_ui = RoleUI(self.role)
        get_response().breadcrumb.append((component + '/', self.role.name))

    def _q_index(self):
        get_response().set_title(self.role.name)

        r = TemplateIO(html=True)
        r += htmltext('<div id="appbar">')
        r += htmltext('<h2>%s</h2>') % self.role.name
        r += htmltext('<span class="actions">')
        r += htmltext('<a href="delete" rel="popup">%s</a>') % _('Delete')
        r += htmltext('<a href="edit">%s</a>') % _('Edit')
        r += htmltext('</span>')
        r += htmltext('</div>')

        if self.role.details or self.role.emails:
            r += htmltext('<div class="bo-block">')
            if self.role.details:
                r += htmltext('<p>%s</p>') % self.role.details

            if self.role.emails:
                r += str(_('Emails:'))
                r += htmltext('<ul>')
                for email in self.role.emails:
                    r += htmltext('<li>%s</li>') % email
                r += htmltext('</ul>')
            r += htmltext('</div>')

        if self.role.emails_to_members or self.role.allows_backoffice_access:
            r += htmltext('<div class="bo-block">')
            r += htmltext('<h3>%s</h3>') % _('Options')
            r += htmltext('<ul>')
            if self.role.emails_to_members:
                r += htmltext('<li>%s</li>') % _(
                    'Holders of this role will receive all emails adressed to the role.'
                )
            if self.role.allows_backoffice_access:
                r += htmltext('<li>%s</li>') % _('Holders of this role are granted access to the backoffice.')
            r += htmltext('</ul>')
            r += htmltext('</div>')

        # list forms in two columns,
        #  - 1 forms where this role is affected by the workflow
        #  - 2 forms where the sender is this role
        formdefs = FormDef.select(order_by='name', lightweight=True)
        workflow_formdefs = [x for x in formdefs if x.is_of_concern_for_role_id(self.role.id)]
        sender_formdefs = [x for x in formdefs if self.role.id in (x.roles or [])]

        r += htmltext('<div class="splitcontent-left">')
        r += htmltext('<div class="bo-block">')
        r += htmltext('<h3>%s</h3>') % _('Forms handled by this role')
        r += htmltext('<ul>')
        for formdef in workflow_formdefs:
            r += htmltext('<li><a href="../../forms/%s">') % formdef.id
            r += formdef.name
            r += htmltext('</a></li>')
        if not workflow_formdefs:
            r += htmltext('<li>%s</li>') % _('no form associated to this role')
        r += htmltext('</ul>')
        r += htmltext('</div>')
        r += htmltext('</div>')

        r += htmltext('<div class="splitcontent-right">')
        r += htmltext('<div class="bo-block">')
        r += htmltext('<h3>%s</h3>') % _('Forms private to this role')
        r += htmltext('<ul>')
        for formdef in sender_formdefs:
            r += htmltext('<li><a href="../../forms/%s">') % formdef.id
            r += formdef.name
            r += htmltext('</a></li>')
        if not sender_formdefs:
            r += htmltext('<li>%s</li>') % _('no form associated to this role')
        r += htmltext('</ul>')
        r += htmltext('</div>')
        r += htmltext('</div>')
        return r.getvalue()

    def edit(self):
        form = self.role_ui.get_form()
        if form.get_widget('cancel').parse():
            return redirect('.')

        if form.is_submitted() and not form.has_errors():
            try:
                self.role_ui.submit_form(form)
            except ValueError:
                pass
            else:
                return redirect('.')

        get_response().breadcrumb.append(('edit', _('Edit')))
        get_response().set_title(_('Edit Role'))
        r = TemplateIO(html=True)
        r += htmltext('<h2>%s</h2>') % _('Edit Role')
        r += form.render()
        return r.getvalue()

    def delete(self):
        form = Form(enctype='multipart/form-data')
        form.widgets.append(HtmlWidget('<p>%s</p>' % _('You are about to irrevocably delete this role.')))
        form.add_submit('delete', _('Delete'))
        form.add_submit('cancel', _('Cancel'))
        if form.get_widget('cancel').parse():
            return redirect('.')
        if not form.is_submitted() or form.has_errors():
            get_response().breadcrumb.append(('delete', _('Delete')))
            get_response().set_title(_('Delete Role'))
            r = TemplateIO(html=True)
            r += htmltext('<h2>%s %s</h2>') % (_('Deleting Role:'), self.role.name)
            r += form.render()
            return r.getvalue()

        self.role.remove_self()
        return redirect('..')


class RolesDirectory(Directory):
    _q_exports = ['', 'new']

    def _q_traverse(self, path):
        get_response().breadcrumb.append(('roles/', _('Roles')))
        get_response().set_backoffice_section('roles')
        return super()._q_traverse(path)

    def is_visible(self, *args):
        return not (get_cfg('sp', {}).get('idp-manage-roles'))

    def _q_index(self):
        get_response().set_title(_('Roles'))
        r = TemplateIO(html=True)

        r += htmltext('<div id="appbar">')
        r += htmltext('<h2>%s</h2>') % _('Roles')
        r += htmltext('<span class="actions">')
        r += htmltext('<a class="new-item" rel="popup" href="new">%s</a>') % _('New Role')
        r += htmltext('</span>')
        r += htmltext('</div>')

        r += htmltext('<div class="explanation bo-block">')
        r += htmltext('<p>%s</p>') % _('Roles are useful for two different things:')
        r += htmltext('<ol>')
        r += htmltext(' <li>%s</li>') % _('To know who will receive and manage a given type of form.')
        r += htmltext(' <li>%s</li>') % _('To know who can fill a given type of form.')
        r += htmltext('</ol>')
        r += htmltext('</div>')

        r += htmltext('<ul class="biglist">')
        for role_infos in get_user_roles():
            role_id, role_name = role_infos[:2]
            r += htmltext('<li>')
            r += htmltext('<strong class="label"><a href="%s/">%s</a></strong>') % (role_id, role_name)
            r += htmltext('</li>')
        r += htmltext('</ul>')
        return r.getvalue()

    def new(self):
        get_response().breadcrumb.append(('new', _('New')))
        role_ui = RoleUI(None)
        form = role_ui.get_form()
        if form.get_widget('cancel').parse():
            return redirect('.')

        if form.is_submitted() and not form.has_errors():
            try:
                role_ui.submit_form(form)
            except ValueError:
                pass
            else:
                return redirect('.')

        get_response().set_title(_('New Role'))
        r = TemplateIO(html=True)
        r += htmltext('<h2>%s</h2>') % _('New Role')
        r += form.render()
        return r.getvalue()

    def _q_lookup(self, component):
        return RolePage(component)
