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

import copy
import datetime
import json
import urllib.parse
import xml.etree.ElementTree as ET

from quixote import get_publisher

from wcs.api_utils import get_secret_and_orig, sign_url
from wcs.sql_criterias import ArrayContains, Equal, IEqual, Intersects, Not, Null, Or

from .qommon import _, get_cfg
from .qommon.misc import get_formatted_phone, http_get_page, http_post_request, simplify
from .qommon.substitution import Substitutions, invalidate_substitution_cache
from .qommon.template import Template, TemplateError
from .qommon.xml_storage import XmlStorableObject


class User(XmlStorableObject):
    _names = 'users'
    xml_root_node = 'user'

    name = None
    email = None
    roles = None
    is_active = True
    is_admin = False
    form_data = None  # dumping ground for custom fields
    preferences = None
    test_uuid = None

    verified_fields = None
    name_identifiers = None
    lasso_dump = None
    deleted_timestamp = None

    last_seen = None
    is_api_user = False

    default_search_result_template = """{{ user_email|default:"" }}
{% if user_var_phone %} 📞 {{ user_var_phone }}{% endif %}
{% if user_var_mobile %} 📱 {{ user_var_mobile }}{% endif %}
{% if user_var_address or user_var_zipcode or user_var_city %} 📨{% endif %}
{% if user_var_address %} {{ user_var_address }}{% endif %}
{% if user_var_zipcode %} {{ user_var_zipcode }}{% endif %}
{% if user_var_city %} {{ user_var_city }}{% endif %}"""

    backoffice_class = 'wcs.admin.tests.TestUserPage'

    XML_NODES = [
        # fields to be included in xml export
        ('name', 'str'),
        ('email', 'str'),
        ('roles', 'ds_roles'),
        ('is_admin', 'bool'),
        ('name_identifiers', 'str_list'),
        ('test_uuid', 'str'),
        ('form_data', 'form_data'),
    ]

    def __init__(self, name=None):
        XmlStorableObject.__init__(self)
        self.name = name
        self.name_identifiers = []
        self.verified_fields = []
        self.roles = []

    def get_formatted_phone(self, country_code=None, field_keys=None):
        users_cfg = get_cfg('users', {})
        for field_phone_key in field_keys or ('field_mobile', 'field_phone'):
            field_phone = users_cfg.get(field_phone_key)
            if field_phone:
                phone = self.form_data.get(field_phone)
                if phone:
                    return get_formatted_phone(phone, country_code)

    @invalidate_substitution_cache
    def store(self, *args, **kwargs):
        return super().store(*args, **kwargs)

    @classmethod
    def get_formdef(cls):
        from .admin.settings import UserFieldsFormDef

        return UserFieldsFormDef.singleton()

    @classmethod
    def get_fields(cls):
        formdef = cls.get_formdef()
        return formdef.fields or []

    @property
    def ascii_name(self):
        return simplify(self.get_display_name(), space=' ')

    def get_display_name(self):
        if self.name:
            return self.name
        if self.email:
            return self.email
        return str(_('Unknown User'))

    display_name = property(get_display_name)

    def __str__(self):
        return self.display_name

    @property
    def nameid(self):
        return self.name_identifiers[0] if self.name_identifiers else None

    def get_roles(self):
        return (self.roles or []) + ['_user:%s' % self.id]

    def get_roles_objects(self, role_prefetch=None):
        roles = []
        for role in self.get_roles():
            try:
                if role_prefetch:
                    if str(role) in role_prefetch:
                        roles.append(role_prefetch[str(role)])
                else:
                    roles.append(get_publisher().role_class.get(role))
            except KeyError:  # role has been deleted
                pass
        return roles

    def set_attributes_from_formdata(self, formdata):
        users_cfg = get_cfg('users', {})

        if formdata.get('email'):
            self.email = formdata.get('email')

        field_email = users_cfg.get('field_email')
        if field_email:
            self.email = formdata.get(field_email)

        if users_cfg.get('fullname_template'):
            # apply template
            user_ctx = self.get_substitution_variables(prefix='')
            template = users_cfg.get('fullname_template')
            try:
                self.name = Template(template, autoescape=False, raises=True).render(user_ctx).strip()
            except TemplateError:
                self.name = '!template error! (%s)' % self.id
        else:
            # legacy mode, list of field IDs
            field_name_values = users_cfg.get('field_name')
            if isinstance(field_name_values, str):  # it was a string in previous versions
                field_name_values = [field_name_values]

            if field_name_values:
                self.name = ' '.join([formdata.get(x) for x in field_name_values if formdata.get(x)])

    def can_go_in_admin(self):
        return self.is_admin

    def can_go_in_backoffice(self, role_prefetch=None):
        if self.is_admin:
            return True

        for role in self.get_roles_objects(role_prefetch=role_prefetch):
            if role.allows_backoffice_access:
                return True
        return False

    def can_go_in_backoffice_section(self, section):
        return getattr(get_publisher().get_backoffice_root(), section).is_accessible(user=self)

    def can_go_in_backoffice_forms(self):
        return self.can_go_in_backoffice_section('forms')

    def can_go_in_backoffice_cards(self):
        return self.can_go_in_backoffice_section('cards')

    def can_go_in_backoffice_workflows(self):
        return self.can_go_in_backoffice_section('workflows')

    def add_roles(self, roles):
        if not self.roles:
            self.roles = []
        self.roles.extend(roles)

    @classmethod
    def get_users_with_role(cls, role_id):
        # this will be slow with the pickle backend as there is no index
        # suitable for Intersects()
        return cls.select([Null('deleted_timestamp'), Intersects('roles', [str(role_id)])])

    @classmethod
    def get_users_with_roles(
        cls, included_roles=None, excluded_roles=None, include_disabled_users=False, order_by=None
    ):
        criterias = [Null('deleted_timestamp')]
        if included_roles:
            criterias.append(ArrayContains('roles', [str(r) for r in included_roles]))
        if excluded_roles:
            criterias.append(Not(Intersects('roles', [str(r) for r in excluded_roles])))
        if not include_disabled_users:
            criterias.append(Equal('is_active', True))
        return cls.select(criterias, order_by=order_by)

    @classmethod
    def get_user_with_roles(
        cls, user_id, included_roles=None, excluded_roles=None, include_disabled_users=False, order_by=None
    ):
        criterias = [Null('deleted_timestamp')]
        try:
            if 0 < int(user_id) < 2**31:
                # user_id may refer to the id column
                criterias.append(
                    Or([Equal('id', int(user_id)), Intersects('name_identifiers', [str(user_id)])])
                )
        except ValueError:
            criterias.append(Intersects('name_identifiers', [user_id]))  # user_id considered as name_id

        if not include_disabled_users:
            criterias.append(Equal('is_active', True))
        if included_roles:
            criterias.append(ArrayContains('roles', [str(r) for r in included_roles]))
        if excluded_roles:
            criterias.append(Not(Intersects('roles', [str(r) for r in excluded_roles])))
        qs = cls.select(criterias, order_by=order_by, limit=1)
        return qs[0] if qs else None

    @classmethod
    def get_users_with_name_identifier(cls, name_identifier):
        return cls.select([Null('deleted_timestamp'), Intersects('name_identifiers', [name_identifier])])

    @classmethod
    def get_users_with_email(cls, email, include_disabled_users=False):
        criterias = [Null('deleted_timestamp'), IEqual('email', email)]
        if not include_disabled_users:
            criterias.append(Equal('is_active', True))
        return cls.select(criterias)

    @classmethod
    def get_users_with_name(cls, name, include_disabled_users=False):
        criterias = [Null('deleted_timestamp'), Equal('name', name)]
        if not include_disabled_users:
            criterias.append(Equal('is_active', True))
        return cls.select(criterias)

    def get_substitution_variables(self, prefix='session_', role_prefetch=None):
        d = {
            prefix + 'user': self,
            prefix + 'user_display_name': self.display_name,
            prefix + 'user_email': self.email,
        }
        formdef = self.get_formdef()
        if formdef:
            from .formdata import get_dict_with_varnames

            data = get_dict_with_varnames(formdef.fields, self.form_data)
            for k, v in data.items():
                d[prefix + 'user_' + k] = v

        d[prefix + 'user_admin_access'] = self.can_go_in_admin()
        d[prefix + 'user_backoffice_access'] = self.can_go_in_backoffice(role_prefetch=role_prefetch)
        for i, name_identifier in enumerate(self.name_identifiers):
            if i == 0:
                d[prefix + 'user_nameid'] = name_identifier
            d[prefix + 'user_name_identifier_%d' % i] = name_identifier
        return d

    @classmethod
    def lookup_by_string(cls, user_value):
        # lookup a single user using some string identifier, it can either be
        # an email address or a uuid (name id).
        if not user_value:
            return None
        if '@' in user_value:
            users = cls.get_users_with_email(user_value)
        else:
            users = cls.get_users_with_name_identifier(user_value)
        if not users:
            return None
        # hopefully the list has a single item but sort it on id to get a
        # stable value in case there are multiple items.
        users.sort(key=lambda x: x.id)
        return users[0]

    @classmethod
    def get_substitution_variables_list(cls, prefix='session_'):
        formdef = cls.get_formdef()
        if not formdef:
            return []
        variables = []
        for field in formdef.fields:
            # we only advertise fields with a varname, as they can be
            # considered stable
            if field.varname:
                variables.append(
                    (
                        _('User'),
                        prefix + 'user_var_' + field.varname,
                        _('Session User Field: %s') % field.label,
                    )
                )
        return variables

    def __getattr__(self, attr):
        if attr in self.__dict__:
            return self.__dict__[attr]

        formdef = self.get_formdef()
        if formdef:
            # lookup based on field varname
            field = [x for x in formdef.fields if x.varname == attr]
            if not field:  # lookup on field id
                field = [x for x in formdef.fields if str(x.id) == '_' + attr]
            if field:
                if 'form_data' not in self.__dict__:
                    return None
                return self.__dict__['form_data'].get(field[0].id)

        if attr[0] == 'f' and (self.__dict__['form_data'] and attr[0] == 'f' and self.__dict__['form_data']):
            return self.__dict__['form_data'].get(attr[1:])

        raise AttributeError()

    def get_json_export_dict(self, full=False):
        data = {
            'id': self.id,
            'name': self.display_name,
        }
        if self.email:
            data['email'] = self.email
        if self.name_identifiers:
            data['NameID'] = self.name_identifiers
        if full:
            formdef = self.get_formdef()
            if formdef:
                for field in formdef.fields:
                    if field.varname:
                        value = self.form_data.get(field.id)
                        if hasattr(field, 'get_json_value') and value is not None:
                            data[field.varname] = field.get_json_value(value)
                        else:
                            data[field.varname] = value
        return data

    def export_form_data_to_xml(self, element, attribute_name, **kwargs):
        formdef = self.get_formdef()
        if not formdef:
            return

        for field in formdef.fields:
            item = ET.SubElement(element, 'item')
            ET.SubElement(item, 'name').text = field.id

            value = self.form_data.get(field.id)
            if field.convert_value_to_str:
                value = field.convert_value_to_str(value)
            elif not isinstance(value, str):
                continue

            ET.SubElement(item, 'value').text = value

    def import_form_data_from_xml(self, element, **kwargs):
        if element is None:
            return

        formdef = self.get_formdef()
        if not formdef:
            return

        data = {}
        fields = formdef.get_all_fields_dict()
        for item in element.findall('item'):
            key = item.find('name').text

            if not key in fields:
                continue

            value = item.find('value')
            if value is None:
                continue

            value = value.text
            if fields[key].convert_value_from_str:
                value = fields[key].convert_value_from_str(value)

            data[key] = value

        return data

    def set_deleted(self):
        self.deleted_timestamp = datetime.datetime.now()
        self.store()

    # django-compatibility properties and methods, useful in shared code/templates
    @property
    def is_anonymous(self):
        return False

    @property
    def is_authenticated(self):
        return True

    @property
    def is_superuser(self):
        return self.is_admin

    def get_full_name(self):
        return self.display_name

    def get_preference(self, pref_name):
        return (self.preferences or {}).get(pref_name)

    def update_preferences(self, prefs):
        if not self.preferences:
            self.preferences = {}
        self.preferences.update(prefs)
        self.store()

    @classmethod
    def update_attributes_from_formdata(cls, job=None):
        for user in cls.select([Null('deleted_timestamp')], iterator=True):
            orig_dict = copy.copy(user.__dict__)
            user.set_attributes_from_formdata(user.form_data or {})
            if orig_dict != user.__dict__:
                user.store()

    @classmethod
    def get_idp_api_users(cls):
        idps = get_cfg('idp', {})
        if idps:
            entity_id = list(idps.values())[0].get('metadata_url')
            if entity_id:
                idp_url = entity_id.split('idp/saml2/metadata')[0]
                return urllib.parse.urljoin(idp_url, '/api/users/')
        return None

    @classmethod
    def sync_users(cls, *args, **kwargs):
        base_url = cls.get_idp_api_users()
        if not base_url:
            return

        # get list of known user UUUIDs
        user_class = get_publisher().user_class
        user_uuids = user_class.get_user_uuids()
        if not user_uuids:
            return

        secret, orig = get_secret_and_orig(base_url)

        # call synchronisation API to get deleted users
        url = sign_url(urllib.parse.urljoin(base_url, 'synchronization/') + '?orig=%s' % orig, secret)
        dummy, status, data, dummy = http_post_request(
            url,
            body=json.dumps({'known_uuids': list(set(user_uuids))}),
            headers={'Accept': 'application/json', 'Content-type': 'application/json'},
        )
        if status != 200:
            get_publisher().record_error(_('Failed to call keepalive API (status: %s)') % status)
            return

        data_json = json.loads(data)
        if data_json.get('err') != 0:
            get_publisher().record_error(_('Failed to call keepalive API (response: %r)') % data_json)
            return

        unknown_uuids = data_json.get('unknown_uuids') or []
        deletion_ratio = len(unknown_uuids) / len(user_uuids)
        if deletion_ratio > 0.05:  # higher than 5%, something definitely went wrong
            get_publisher().record_error(
                _('Deletion ratio is abnormally high (%.1f%%), aborting unknown users deletion')
                % (deletion_ratio * 100)
            )
            return

        for uuid in unknown_uuids:
            for user in user_class.get_users_with_name_identifier(uuid):
                if not (set(user.name_identifiers) - set(user_uuids)):
                    user.set_deleted()

        # call users API to get recently modified users
        from wcs.ctl.management.commands.hobo_notify import Command as CmdHoboNotify

        cmd_notify = CmdHoboNotify()
        pub = get_publisher()
        url = base_url + '?modified__gt=%s' % urllib.parse.quote(
            str(datetime.datetime.now() - datetime.timedelta(seconds=2 * 86400))
        )
        while url:
            url = sign_url(url + '&orig=%s' % orig, secret)
            dummy, status, data, dummy = http_get_page(
                url,
                headers={'Accept': 'application/json', 'Content-type': 'application/json'},
            )
            if status != 200:
                get_publisher().record_error(_('Failed to call users API (status: %s)') % status)
                return
            data_json = json.loads(data)
            if 'results' not in data_json:
                get_publisher().record_error(_('Failed to call users API (response: %r)') % data_json)
                return
            url = data_json.get('next')
            cmd_notify.provision_user(
                pub, issuer=None, action='provision', data=data_json['results'], with_roles=False
            )

    @classmethod
    def keepalive_users(cls, *args, **kwargs):
        # get API URL
        base_url = cls.get_idp_api_users()
        if not base_url:
            return

        # get list of active UUIDs
        from wcs.carddef import CardDef

        user_uuids = get_publisher().user_class.get_formdef_keepalive_user_uuids()
        user_ids = set()
        for carddef in CardDef.select(ignore_errors=True):
            if not (carddef and carddef.user_support):
                continue
            for carddata in carddef.data_class().select(ignore_errors=True):
                if carddata.user_id:
                    user_ids.add(carddata.user_id)
        for user in get_publisher().user_class().get_ids(user_ids, ignore_errors=True):
            user_uuids.extend(user.name_identifiers)

        secret, orig = get_secret_and_orig(base_url)
        url = sign_url(urllib.parse.urljoin(base_url, 'synchronization/') + '?orig=%s' % orig, secret)

        # call API
        status = http_post_request(
            url,
            body=json.dumps({'known_uuids': list(set(user_uuids)), 'keepalive': True}),
            headers={'Accept': 'application/json', 'Content-type': 'application/json'},
        )[1]
        if status != 200:
            get_publisher().record_error(_('Failed to call keepalive API (status: %s)') % status)

    def get_admin_url(self):
        return '%s/forms/test-users/%s/' % (get_publisher().get_backoffice_url(), self.id)

    @property
    def slug(self):
        return self.test_uuid

    @classmethod
    def get_by_slug(cls, slug, **kwargs):
        try:
            return cls.select([Equal('test_uuid', slug)])[0]
        except IndexError:
            return None

    def get_dependencies(self):
        from wcs.workflows import get_role_dependencies

        yield from get_role_dependencies(self.roles)


Substitutions.register(
    'session_user_display_name',
    category=_('User'),
    comment=_('Session User Display Name'),
)
Substitutions.register('session_user_email', category=_('User'), comment=_('Session User Email'))
Substitutions.register_dynamic_source(User)
