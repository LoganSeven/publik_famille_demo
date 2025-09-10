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

from quixote import get_publisher, get_request, get_response, get_session, get_session_manager, redirect
from quixote.directory import Directory
from quixote.html import TemplateIO, htmltext

from wcs.qommon.admin.texts import TextsDirectory

from . import _, errors, get_cfg
from .form import Form, HtmlWidget, PasswordEntryWidget
from .ident.password_accounts import PasswordAccount


# This module depends upon the following protocol from the user class:
#
# protocol User:
#   def can_go_in_admin(self): User -> boolean
#   def can_go_in_backoffice(self): User -> boolean
#   def get_formdef(self): User -> an object responding to the FormDef protocol
#
# protocol FormDef:
#    fields = list of object responding to the Field protocol
#    def add_field_to_form(self, form, form_data): FormDef -> quixote.form.Form -> dict -> void
#       "add the fields in the form definition to a Quixote HTML form"
#
# protocol Field:
#    id = identifier of the field in an HTML form
#
class MyspaceDirectory(Directory):
    _q_exports = ['', 'profile', 'new', 'password', 'remove']

    def _q_traverse(self, path):
        if get_publisher().get_site_option('idp_account_url', 'variables'):
            return redirect(get_publisher().get_site_option('idp_account_url', 'variables'))
        get_response().breadcrumb.append(('myspace/', _('My Space')))
        return Directory._q_traverse(self, path)

    def _q_index(self):
        user = get_request().user
        if not user:
            raise errors.AccessUnauthorizedError()

        get_response().set_title(_('My Space'))
        r = TemplateIO(html=True)

        if user.can_go_in_admin() or user.can_go_in_backoffice():
            root_url = get_publisher().get_root_url()
            r += htmltext('<p id="profile-links">')
            r += htmltext('<a href="%sbackoffice/">%s</a>') % (root_url, _('back office'))
            r += htmltext('</p>')

        ident_methods = get_cfg('identification', {}).get('methods', ['idp']) or []
        passwords_cfg = get_cfg('passwords', {})

        formdef = user.get_formdef()
        if formdef:
            r += htmltext('<h3>%s</h3>') % _('My Profile')

            r += TextsDirectory.get_html_text('top-of-profile')

            if user.form_data:
                r += htmltext('<ul>')
                for field in formdef.fields:
                    if not hasattr(field, 'get_view_value'):
                        continue
                    value = user.form_data.get(field.id)
                    r += htmltext('<li>')
                    r += field.label
                    r += ' : '
                    r += field.get_view_value(value)
                    r += htmltext('</li>')
                r += htmltext('</ul>')
            else:
                r += htmltext('<p>%s</p>') % _('Empty profile')

        r += htmltext('<p class="command"><a href="profile" rel="popup">%s</a></p>') % _('Edit My Profile')

        if 'password' in ident_methods and passwords_cfg.get('can_change', False):
            r += htmltext('<p class="command"><a href="password" rel="popup">%s</a></p>') % _(
                'Change My Password'
            )

        r += htmltext('<p class="command"><a href="remove" rel="popup">%s</a></p>') % _('Remove My Account')

        return r.getvalue()

    def profile(self):
        user = get_request().user
        if not user:
            raise errors.AccessUnauthorizedError()

        form = Form(enctype='multipart/form-data')
        formdef = user.get_formdef()
        formdef.add_fields_to_form(form, form_data=user.form_data)

        form.add_submit('submit', _('Apply Changes'))
        form.add_submit('cancel', _('Cancel'))

        if form.get_submit() == 'cancel':
            return redirect('.')

        if form.is_submitted() and not form.has_errors():
            self.profile_submit(form, formdef)
            return redirect('.')

        get_response().set_title(_('Edit Profile'))
        return form.render()

    def profile_submit(self, form, formdef):
        user = get_request().user
        data = formdef.get_data(form)

        user.set_attributes_from_formdata(data)
        user.form_data = data

        user.store()

    def password(self):
        ident_methods = get_cfg('identification', {}).get('methods', ['idp']) or []
        if 'password' not in ident_methods:
            raise errors.TraversalError()

        user = get_request().user
        if not user:
            raise errors.AccessUnauthorizedError()

        form = Form(enctype='multipart/form-data')
        passwords_cfg = get_cfg('passwords', {})
        form.add(
            PasswordEntryWidget,
            'new_password',
            title=_('New Password'),
            required=True,
            confirmation=True,
            min_length=passwords_cfg.get('min_length', 0),
            max_length=passwords_cfg.get('max_length', 0),
            count_uppercase=passwords_cfg.get('count_uppercase', 0),
            count_lowercase=passwords_cfg.get('count_lowercase', 0),
            count_digit=passwords_cfg.get('count_digit', 0),
            count_special=passwords_cfg.get('count_special', 0),
            formats=['cleartext'],
        )
        form.add_submit('submit', _('Change Password'))
        form.add_submit('cancel', _('Cancel'))

        if form.get_submit() == 'cancel':
            return redirect('.')

        if form.is_submitted() and not form.has_errors():
            passwords_cfg = get_cfg('passwords', {})
            account = PasswordAccount.get(get_session().username)
            account.hashing_algo = passwords_cfg.get('hashing_algo')
            account.set_password(form.get_widget('new_password').parse()['cleartext'])
            account.store()
            return redirect('.')

        get_response().set_title(_('Change Password'))
        return form.render()

    def remove(self):
        user = get_request().user
        if not user:
            raise errors.AccessUnauthorizedError()

        form = Form(enctype='multipart/form-data')
        form.widgets.append(
            HtmlWidget('<p>%s</p>' % _('Are you really sure you want to remove your account?'))
        )
        form.add_submit('submit', _('Remove my account'))
        form.add_submit('cancel', _('Cancel'))

        if form.get_submit() == 'cancel':
            return redirect('.')

        if form.is_submitted() and not form.has_errors():
            user = get_request().user
            try:
                account = PasswordAccount.get_by_user_id(user.id)
                account.remove_self()
            except KeyError:
                pass
            get_session_manager().expire_session()
            return redirect(get_publisher().get_root_url())

        get_response().set_title(_('Removing Account'))
        return form.render()


TextsDirectory.register('top-of-profile', _('Text on top of the profile page'))
