# w.c.s. - web application for online forms
# Copyright (C) 2005-2012  Entr'ouvert
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

from quixote import get_publisher, get_request

from wcs.api_utils import MissingSecret, get_secret_and_orig, sign_url
from wcs.roles import get_user_roles
from wcs.workflows import WorkflowStatusItem, get_role_dependencies, register_item_class

from ..qommon import _
from ..qommon.afterjobs import AfterJob
from ..qommon.form import SingleSelectWidgetWithOther
from ..qommon.ident.idp import is_idp_managing_user_attributes
from ..qommon.misc import http_delete_request, http_post_request
from ..qommon.publisher import get_cfg


def roles_ws_url(role_uuid, user_uuid):
    idps = get_cfg('idp', {})
    entity_id = list(idps.values())[0]['metadata_url']
    base_url = entity_id.split('idp/saml2/metadata')[0]
    url = urllib.parse.urljoin(
        base_url, '/api/roles/%s/members/%s/' % (urllib.parse.quote(role_uuid), urllib.parse.quote(user_uuid))
    )
    return url


def sign_ws_url(url):
    secret, orig = get_secret_and_orig(url)
    url += '?orig=%s' % orig
    return sign_url(url, secret)


class RoleMixin:
    def role_id_export_to_xml(self, item, include_id=False):
        self._role_export_to_xml('role_id', item, include_id=include_id)

    def role_id_init_with_xml(self, elem, include_id=False, snapshot=False):
        self._role_init_with_xml('role_id', elem, include_id=include_id, snapshot=snapshot)

    def get_role_id_parameter_view_value(self):
        return self.get_line_details()

    def get_line_details(self):
        if not self.role_id:
            return _('not configured')
        role = get_publisher().role_class.get(self.role_id, ignore_errors=True)
        if role is not None:
            return role.name
        return _('unknown - %s') % self.role_id

    def get_dependencies(self):
        yield from get_role_dependencies([self.role_id])


class AddRoleAfterJob(AfterJob):
    label = _('Adding role')

    def __init__(self, url, role_id, user_id, formdata):
        super().__init__()
        self.url = url
        self.role_id = role_id
        self.user_id = user_id
        self.formdef_class = formdata.formdef.__class__
        self.formdef_id = formdata.formdef.id
        self.formdata_id = formdata.id

    def execute(self):
        signed_url = sign_ws_url(self.url)
        dummy, status, dummy, dummy = http_post_request(signed_url)
        if status != 201:
            role = get_publisher().role_class.get(self.role_id)
            user = get_publisher().user_class.get(self.user_id)
            formdata = self.formdef_class.get(self.formdef_id).data_class().get(self.formdata_id)
            get_publisher().record_error(
                _('Failed to add role %(role)r to user %(user)r') % {'role': role, 'user': user},
                formdata=formdata,
            )


class RemoveRoleAfterJob(AddRoleAfterJob):
    label = _('Removing role')

    def execute(self):
        signed_url = sign_ws_url(self.url)
        # pylint: disable=unused-variable
        response, status, data, auth_header = http_delete_request(signed_url)
        if status != 200:
            role = get_publisher().role_class.get(self.role_id)
            user = get_publisher().user_class.get(self.user_id)
            formdata = self.formdef_class.get(self.formdef_id).data_class().get(self.formdata_id)
            get_publisher().record_error(
                _('Failed to remove role %(role)r from user %(user)r') % {'role': role, 'user': user},
                formdata=formdata,
            )


def clean_afterjobs(role_id, user_id):
    get_publisher().after_jobs = [
        x
        for x in get_publisher().after_jobs or []
        if not isinstance(x, AddRoleAfterJob) or (x.role_id, x.user_id) != (role_id, user_id)
    ]


class AddRoleWorkflowStatusItem(RoleMixin, WorkflowStatusItem):
    description = _('Role Addition')
    key = 'add_role'
    category = 'user-action'

    role_id = None

    def get_parameters(self):
        return ('role_id', 'condition')

    def add_parameters_widgets(self, form, parameters, prefix='', formdef=None, **kwargs):
        super().add_parameters_widgets(form, parameters, prefix=prefix, formdef=formdef, **kwargs)
        if 'role_id' in parameters:
            form.add(
                SingleSelectWidgetWithOther,
                '%srole_id' % prefix,
                title=_('Role to Add'),
                value=str(self.role_id) if self.role_id else None,
                options=[(None, '----', None)] + get_user_roles(),
            )

    def perform(self, formdata):
        if not self.role_id:
            return
        role_id = self.get_computed_role_id(self.role_id)
        if not role_id:
            return
        if not formdata.user_id:
            # we can't work on anonymous forms
            return
        user = get_publisher().user_class.get(formdata.user_id)
        self.perform_local(user, formdata, role_id)
        if user.name_identifiers and is_idp_managing_user_attributes():
            self.perform_idp(user, formdata, role_id)

    def perform_local(self, user, formdata, role_id):
        if not user.roles:
            user.roles = []
        if role_id not in user.roles:
            user.roles.append(role_id)
        user.store()
        request = get_request()
        if request and request.user and request.user.id == user.id:
            # if we changed the currently logged in user, we update it with the
            # changes.
            request._user = user

    def perform_idp(self, user, formdata, role_id):
        role = get_publisher().role_class.get(role_id)
        role_uuid = role.uuid or role.slug
        user_uuid = user.name_identifiers[0]
        try:
            url = roles_ws_url(role_uuid, user_uuid)
        except MissingSecret as e:
            get_publisher().record_error(exception=e, context=self.description, notify=True)
            return

        clean_afterjobs(role.id, user.id)
        get_publisher().add_after_job(AddRoleAfterJob(url, role.id, user.id, formdata))


register_item_class(AddRoleWorkflowStatusItem)


class RemoveRoleWorkflowStatusItem(RoleMixin, WorkflowStatusItem):
    description = _('Role Removal')
    key = 'remove_role'
    category = 'user-action'

    role_id = None

    def get_parameters(self):
        return ('role_id', 'condition')

    def add_parameters_widgets(self, form, parameters, prefix='', formdef=None, **kwargs):
        super().add_parameters_widgets(form, parameters, prefix=prefix, formdef=formdef, **kwargs)
        if 'role_id' in parameters:
            form.add(
                SingleSelectWidgetWithOther,
                '%srole_id' % prefix,
                title=_('Role to Remove'),
                value=str(self.role_id) if self.role_id else None,
                options=[(None, '----', None)] + get_user_roles(),
            )

    def perform(self, formdata):
        if not self.role_id:
            return
        role_id = self.get_computed_role_id(self.role_id)
        if not role_id:
            return
        if not formdata.user_id:
            # we can't work on anonymous forms
            return
        user = get_publisher().user_class.get(formdata.user_id)
        self.perform_local(user, formdata, role_id)
        if user.name_identifiers and is_idp_managing_user_attributes():
            self.perform_idp(user, formdata, role_id)

    def perform_local(self, user, formdata, role_id):
        if user.roles and role_id in user.roles:
            user.roles.remove(role_id)
            user.store()
            request = get_request()
            if request and request.user and request.user.id == user.id:
                # if we changed the currently logged in user, we update it
                # with the changes.
                request._user = user

    def perform_idp(self, user, formdata, role_id):
        role = get_publisher().role_class.get(role_id)
        role_uuid = role.uuid or role.slug
        user_uuid = user.name_identifiers[0]
        try:
            url = roles_ws_url(role_uuid, user_uuid)
        except MissingSecret as e:
            get_publisher().record_error(exception=e, context=self.description, notify=True)
            return

        clean_afterjobs(role.id, user.id)
        get_publisher().add_after_job(RemoveRoleAfterJob(url, role.id, user.id, formdata))


register_item_class(RemoveRoleWorkflowStatusItem)
