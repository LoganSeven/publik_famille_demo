# w.c.s. - web application for online forms
# Copyright (C) 2005-2016  Entr'ouvert
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

import datetime
import json
import time
import urllib.parse
import xml.etree.ElementTree as ET

from django.utils.encoding import force_str
from quixote import get_publisher, get_request

from wcs.api_utils import MissingSecret, get_secret_and_orig, sign_url
from wcs.workflows import WorkflowStatusItem, XmlSerialisable, register_item_class

from ..qommon import _
from ..qommon.afterjobs import AfterJob
from ..qommon.form import CompositeWidget, ComputedExpressionWidget, SingleSelectWidget, WidgetListAsTable
from ..qommon.ident.idp import is_idp_managing_user_attributes
from ..qommon.misc import JSONEncoder, http_patch_request
from ..qommon.publisher import get_cfg


def user_ws_url(user_uuid):
    idps = get_cfg('idp', {})
    entity_id = list(idps.values())[0]['metadata_url']
    base_url = entity_id.split('idp/saml2/metadata')[0]
    url = urllib.parse.urljoin(base_url, '/api/users/%s/' % user_uuid)
    get_secret_and_orig(url)  # early check remote is known
    return url


class ProfileUpdateRowWidget(CompositeWidget):
    def __init__(self, name, value=None, **kwargs):
        CompositeWidget.__init__(self, name, value, **kwargs)
        if not value:
            value = {}

        fields = []
        users_cfg = get_cfg('users', {})
        user_formdef = get_publisher().user_class.get_formdef()
        if not user_formdef or not get_publisher().has_user_fullname_config():
            fields.append(('__name', _('Name'), '__name'))
        if not user_formdef or not users_cfg.get('field_email'):
            fields.append(('__email', _('Email'), '__email'))
        if user_formdef and user_formdef.fields:
            for field in user_formdef.fields:
                if field.varname:
                    fields.append((field.varname, field.label, field.varname))
        fields = sorted(fields, key=lambda f: f[1])

        self.add(
            SingleSelectWidget,
            name='field_id',
            title=_('Field'),
            value=value.get('field_id'),
            options=[(None, '', '')] + fields,
            **kwargs,
        )
        self.add(ComputedExpressionWidget, name='value', title=_('Value'), value=value.get('value'))

    def _parse(self, request):
        if self.get('value') and self.get('field_id'):
            self.value = {'value': self.get('value'), 'field_id': self.get('field_id')}
        else:
            self.value = None


class ProfileUpdateTableWidget(WidgetListAsTable):
    readonly = False

    def __init__(self, name, **kwargs):
        super().__init__(name, element_type=ProfileUpdateRowWidget, **kwargs)


class FieldNode(XmlSerialisable):
    node_name = 'field'

    def __init__(self, rule=None):
        rule = rule or {}
        self.field_id = rule.get('field_id') or ''
        self.value = rule.get('value') or ''

    def as_dict(self):
        return {'field_id': self.field_id, 'value': self.value}

    def get_parameters(self):
        return ('field_id', 'value')


class UpdateUserAfterJob(AfterJob):
    label = _('Updating user profile')

    def __init__(self, formdata, user, url, payload):
        super().__init__()
        self.user_id = user.id
        self.payload = payload
        self.url = url
        self.formdef_class = formdata.formdef.__class__
        self.formdef_id = formdata.formdef.id
        self.formdata_id = formdata.id

    def execute(self):
        secret, orig = get_secret_and_orig(self.url)
        url = sign_url(self.url + '?orig=%s' % orig, secret)
        dummy, status, data, dummy = http_patch_request(
            url, self.payload, headers={'Content-type': 'application/json'}
        )
        if status != 200:
            user = get_publisher().user_class.get(self.user_id)
            user_uuid = user.nameid
            with get_publisher().error_context(
                status=status, response_data=force_str(data), user=str(user), user_uuid=user_uuid
            ):
                formdata = self.formdef_class.get(self.formdef_id).data_class().get(self.formdata_id)
                msg = _('Failed to update user profile on identity provider (%s)') % status
                get_publisher().record_error(msg, formdata=formdata)


class UpdateUserProfileStatusItem(WorkflowStatusItem):
    description = _('User Profile Update')
    key = 'update_user_profile'
    category = 'user-action'

    fields = None

    def get_parameters(self):
        return ('fields', 'condition')

    def add_parameters_widgets(self, form, parameters, prefix='', formdef=None, **kwargs):
        super().add_parameters_widgets(form, parameters, prefix=prefix, formdef=formdef, **kwargs)
        if 'fields' in parameters:
            form.add(
                ProfileUpdateTableWidget, '%sfields' % prefix, title=_('Profile Update'), value=self.fields
            )

    def fields_export_to_xml(self, item, include_id=False):
        if not self.fields:
            return

        fields_node = ET.SubElement(item, 'fields')
        for field in self.fields:
            fields_node.append(FieldNode(field).export_to_xml(include_id=include_id))

        return fields_node

    def fields_init_with_xml(self, elem, include_id=False, snapshot=False):
        fields = []
        if elem is None:
            return
        for field_xml_node in elem.findall('field'):
            field_node = FieldNode()
            field_node.init_with_xml(field_xml_node, include_id=include_id, snapshot=snapshot)
            fields.append(field_node.as_dict())
        if fields:
            self.fields = fields

    def get_computed_strings(self):
        yield from super().get_computed_strings()
        for field in self.fields or []:
            yield field.get('value')

    def perform(self, formdata):
        if not self.fields:
            return
        user = formdata.get_user()
        if not user:
            return
        get_publisher().substitutions.feed(formdata)
        new_data = {}
        for field in self.fields:
            new_data[field.get('field_id')] = self.compute(field.get('value'))

        user_formdef = get_publisher().user_class.get_formdef()
        new_user_data = {}
        for field in user_formdef.fields:
            if field.varname in new_data:
                field_value = new_data.get(field.varname)
                if field and field.convert_value_from_anything:
                    try:
                        field_value = field.convert_value_from_anything(field_value)
                    except ValueError as e:
                        get_publisher().record_error(exception=e, context=_('Profile'), notify=True)
                        # invalid attribute, do not update it
                        del new_data[field.varname]
                        continue
                new_user_data[field.id] = field_value
                # also change initial value to the converted one, as the
                # initial dictionary is used when sending the profile changes
                # to the identity provider.
                new_data[field.varname] = field_value

        if '__name' in new_data:
            user.name = str(new_data.get('__name'))
        if '__email' in new_data:
            user.email = str(new_data.get('__email'))
        if not user.form_data and new_user_data:
            user.form_data = {}
        if new_user_data:
            user.form_data.update(new_user_data)
        if user.form_data:
            user.set_attributes_from_formdata(user.form_data)
        user.store()

        if user.name_identifiers and is_idp_managing_user_attributes():
            self.perform_idp(user, new_data, formdata)

    def perform_idp(self, user, new_data, formdata):
        user_uuid = user.name_identifiers[0]
        try:
            url = user_ws_url(user_uuid)
        except MissingSecret as e:
            get_publisher().record_error(exception=e, context=_('Profile'), notify=True)
            return

        payload = new_data.copy()
        for k, v in payload.items():
            # fix date fields to be datetime.date
            if isinstance(v, time.struct_time):
                payload[k] = datetime.date(*v[:3])

        if '__email' in new_data:
            payload['email'] = new_data.get('__email')

        payload = json.dumps(payload, cls=JSONEncoder)

        job = UpdateUserAfterJob(formdata, user, url, payload)
        if get_request():
            get_publisher().add_after_job(job)
        else:
            job.id = job.DO_NOT_STORE
            job.execute()


register_item_class(UpdateUserProfileStatusItem)
