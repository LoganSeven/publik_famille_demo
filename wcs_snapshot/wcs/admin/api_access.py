# w.c.s. - web application for online forms
# Copyright (C) 2005-2020  Entr'ouvert
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

import urllib.parse
import uuid

from quixote import get_publisher, get_response, redirect
from quixote.directory import Directory
from quixote.html import TemplateIO, htmltext

from wcs.qommon import _, errors, template
from wcs.qommon.form import (
    CheckboxWidget,
    Form,
    HtmlWidget,
    SingleSelectWidget,
    StringWidget,
    TextWidget,
    WidgetList,
)
from wcs.qommon.publisher import get_cfg
from wcs.sql import ApiAccess


class ApiAccessUI:
    def __init__(self, api_access):
        self.api_access = api_access
        if self.api_access is None:
            self.api_access = ApiAccess()

    def get_form(self):
        form = Form(enctype='multipart/form-data')
        form.add(StringWidget, 'name', title=_('Name'), required=True, size=30, value=self.api_access.name)
        form.add(
            TextWidget,
            'description',
            title=_('Description'),
            cols=40,
            rows=5,
            value=self.api_access.description,
        )
        form.add(
            StringWidget,
            'access_identifier',
            title=_('Access identifier'),
            required=True,
            size=30,
            value=self.api_access.access_identifier,
        )
        form.add(
            StringWidget,
            'access_key',
            title=_('Access key'),
            required=True,
            size=30,
            value=self.api_access.access_key or str(uuid.uuid4()),
        )
        form.add(
            CheckboxWidget,
            'restrict_to_anonymised_data',
            title=_('Restrict to anonymised data'),
            value=self.api_access.restrict_to_anonymised_data,
        )
        roles = list(get_publisher().role_class.select(order_by='name'))
        form.add(
            WidgetList,
            'roles',
            title=_('Roles'),
            element_type=SingleSelectWidget,
            value=self.api_access.roles,
            add_element_label=_('Add Role'),
            element_kwargs={
                'render_br': False,
                'options': [(None, '---', None)] + [(x, x.name, x.id) for x in roles if not x.is_internal()],
            },
            hint=_('Roles given with this access'),
        )

        form.add_submit('submit', _('Submit'))
        form.add_submit('cancel', _('Cancel'))
        return form

    def submit_form(self, form):
        name = form.get_widget('name').parse()
        access_identifier = form.get_widget('access_identifier').parse()

        for api_access in ApiAccess.select():
            if api_access.id == self.api_access.id:
                continue
            if name == api_access.name:
                form.get_widget('name').set_error(_('This name is already used.'))
            if access_identifier and access_identifier == api_access.access_identifier:
                form.get_widget('access_identifier').set_error(_('This value is already used.'))
        if form.has_errors():
            raise ValueError()

        self.api_access.name = name
        self.api_access.access_identifier = access_identifier
        for attribute in ('description', 'access_key', 'restrict_to_anonymised_data', 'roles'):
            setattr(self.api_access, attribute, form.get_widget(attribute).parse())
        self.api_access.store()


class ApiAccessPage(Directory):
    _q_exports = [
        '',
        'edit',
        'delete',
    ]

    def __init__(self, component, instance=None):
        try:
            self.api_access = instance or ApiAccess.get(component)
        except KeyError:
            raise errors.TraversalError()
        self.api_access_ui = ApiAccessUI(self.api_access)
        get_response().breadcrumb.append((component + '/', self.api_access.name))

    def _q_index(self):
        get_response().set_title(self.api_access.name)
        return template.QommonTemplateResponse(
            templates=['wcs/backoffice/api_access.html'],
            context={'view': self, 'api_access': self.api_access},
        )

    def edit(self):
        form = self.api_access_ui.get_form()
        if form.get_submit() == 'cancel':
            return redirect('.')

        if form.get_submit() == 'submit' and not form.has_errors():
            try:
                self.api_access_ui.submit_form(form)
            except ValueError:
                pass
            else:
                return redirect('../%s/' % self.api_access.id)

        get_response().breadcrumb.append(('edit', _('Edit')))
        get_response().set_title(_('Edit API access'))
        r = TemplateIO(html=True)
        r += htmltext('<h2>%s</h2>') % _('Edit API access')
        r += form.render()
        return r.getvalue()

    def delete(self):
        form = Form(enctype='multipart/form-data')
        form.widgets.append(
            HtmlWidget('<p>%s</p>' % _('You are about to irrevocably delete this API access.'))
        )
        form.add_submit('delete', _('Delete'))
        form.add_submit('cancel', _('Cancel'))
        if form.get_widget('cancel').parse():
            return redirect('..')
        if not form.is_submitted() or form.has_errors():
            get_response().breadcrumb.append(('delete', _('Delete')))
            get_response().set_title(_('Delete API access'))
            r = TemplateIO(html=True)
            r += htmltext('<h2>%s %s</h2>') % (_('Deleting API access:'), self.api_access.name)
            r += form.render()
            return r.getvalue()

        self.api_access.remove_self()
        return redirect('..')


class ApiAccessDirectory(Directory):
    _q_exports = ['', 'new']

    def _q_traverse(self, path):
        get_response().breadcrumb.append(('api-access/', _('API access')))
        get_response().set_backoffice_section('api-access')
        return super()._q_traverse(path)

    def _q_index(self):
        get_response().set_title(_('API access'))

        api_manage_url = None
        idps = get_cfg('idp', {})
        if idps:
            entity_id = list(idps.values())[0]['metadata_url']
            if 'idp/saml2/metadata' in entity_id:
                base_url = entity_id.split('idp/saml2/metadata')[0]
                api_manage_url = urllib.parse.urljoin(base_url, '/manage/api-clients/')

        return template.QommonTemplateResponse(
            templates=['wcs/backoffice/api_accesses.html'],
            context={
                'view': self,
                'api_accesses': [x for x in ApiAccess.select(order_by='name') if not x.idp_api_client],
                'api_manage_url': api_manage_url,
            },
        )

    def new(self):
        get_response().breadcrumb.append(('new', _('New')))
        api_access_ui = ApiAccessUI(None)
        form = api_access_ui.get_form()
        if form.get_widget('cancel').parse():
            return redirect('.')

        if form.get_submit() == 'submit' and not form.has_errors():
            try:
                api_access_ui.submit_form(form)
            except ValueError:
                pass
            else:
                return redirect('.')

        get_response().set_title(_('New API access'))
        r = TemplateIO(html=True)
        r += htmltext('<h2>%s</h2>') % _('New API access')
        r += form.render()
        return r.getvalue()

    def _q_lookup(self, component):
        return ApiAccessPage(component)
