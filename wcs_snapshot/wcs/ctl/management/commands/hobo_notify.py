# w.c.s. - web application for online forms
# Copyright (C) 2005-2023  Entr'ouvert
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

import json
import sys

from quixote import get_publisher

from wcs.admin.settings import UserFieldsFormDef
from wcs.qommon import _, force_str
from wcs.qommon.publisher import get_cfg, get_publisher_class

from . import TenantCommand


class Command(TenantCommand):
    def add_arguments(self, parser):
        parser.add_argument('notification', metavar='NOTIFICATION', type=str)

    def handle(self, **options):
        notification = self.load_notification(options.get('notification'))
        if not self.check_valid_notification(notification):
            sys.exit(1)

        for tenant in get_publisher_class().get_tenants():
            pub = get_publisher_class().create_publisher(register_tld_names=False)
            pub.set_tenant_by_hostname(tenant.hostname)
            self.process_notification(notification, publisher=pub)

    @classmethod
    def load_notification(cls, notification):
        if notification == '-':
            # get environment definition from stdin
            return json.load(sys.stdin)

        with open(notification) as fd:
            return json.load(fd)

    @classmethod
    def check_valid_notification(cls, notification):
        return (
            isinstance(notification, dict)
            and notification['@type'] in ['provision', 'deprovision']
            and 'objects' in notification
            and 'audience' in notification
            and isinstance(notification['audience'], list)
            and isinstance(notification['objects'], dict)
            and '@type' in notification['objects']
            and 'data' in notification['objects']
            and isinstance(notification['objects']['data'], list)
        )

    @classmethod
    def process_notification(cls, notification, publisher=None):
        publisher = publisher or get_publisher()
        action = notification['@type']
        audience = notification['audience']
        full = notification['full'] if 'full' in notification else False
        issuer = notification.get('issuer')

        # Verify tenant is in audience
        entity_id = get_cfg('sp', {}).get('saml2_providerid')
        if not entity_id or entity_id not in audience:
            return

        t = notification['objects']['@type']
        # Now provision/deprovision
        getattr(cls, 'provision_' + t)(publisher, issuer, action, notification['objects']['data'], full=full)

    @classmethod
    def check_valid_role(cls, o):
        return 'uuid' in o and 'name' in o and 'emails' in o and 'emails_to_members' in o and 'slug' in o

    @classmethod
    def provision_role(cls, publisher, issuer, action, data, full=False):
        uuids = set()
        for o in data:
            if 'uuid' not in o:
                raise KeyError('role without uuid')
            uuid = force_str(o['uuid'])
            uuids.add(uuid)
            slug = None
            name = None
            if action == 'provision':
                if not cls.check_valid_role(o):
                    raise ValueError('invalid role')
                slug = force_str(o['slug'])
                details = force_str(o.get('details', '')) or None
                name = force_str(o['name'])
                emails = [force_str(email) for email in o['emails']]
                emails_to_members = o['emails_to_members']
            # Find existing role
            role = get_publisher().role_class.resolve(uuid, slug, name)
            if not role:
                if action != 'provision':
                    continue
                role = get_publisher().role_class(id=uuid)
            if action == 'provision':
                # Provision/rename
                role.name = name
                role.uuid = uuid
                role.slug = slug
                role.emails = emails
                role.details = details
                role.emails_to_members = emails_to_members
                if role.slug.startswith('_'):
                    role.internal = True
                role.store()
            elif action == 'deprovision':
                # Deprovision
                role.remove_self()
        # All roles have been sent
        if full and action == 'provision':
            for role in get_publisher().role_class.select(ignore_errors=True):
                if role and role.uuid not in uuids:
                    role.remove_self()

    @classmethod
    def check_valid_user(cls, o, with_roles=True):
        return (
            'uuid' in o
            and 'email' in o
            and 'first_name' in o
            and 'last_name' in o
            and ('roles' in o or with_roles is False)
        )

    @classmethod
    def provision_user(cls, publisher, issuer, action, data, full=False, with_roles=True):
        formdef = UserFieldsFormDef(publisher=publisher)
        User = publisher.user_class

        if full:
            raise NotImplementedError('full is not supported for users')

        for o in data:
            try:
                if action == 'provision':
                    if not cls.check_valid_user(o, with_roles=with_roles):
                        raise ValueError('invalid user')
                    uuid = o['uuid']
                    users = User.get_users_with_name_identifier(uuid)
                    if len(users) > 1:
                        raise Exception('duplicate users')
                    if users:
                        user = users[0]
                    else:
                        user = User(uuid)
                    user.form_data = user.form_data or {}
                    for field in formdef.fields:
                        if not field.id.startswith('_'):
                            continue
                        field_value = o.get(field.id[1:])
                        if field.convert_value_from_anything:
                            try:
                                field_value = field.convert_value_from_anything(field_value)
                            except ValueError as e:
                                publisher.record_error(exception=e, context=_('Provisionning'), notify=True)
                                continue
                        user.form_data[field.id] = field_value
                    user.name_identifiers = [uuid]
                    # reset roles
                    user.is_active = o.get('is_active', True)
                    user.is_admin = o.get('is_superuser', False)
                    if with_roles:
                        user.roles = []
                        for role_ref in o.get('roles', []):
                            role = get_publisher().role_class.resolve(role_ref['uuid'])
                            if role and role.id not in user.roles:
                                user.add_roles([role.id])
                    user.set_attributes_from_formdata(user.form_data)
                    user.store()
                    # verify we did not produce a doublon
                    users = User.get_users_with_name_identifier(uuid)
                    for doublon in users:
                        if int(doublon.id) < int(user.id):  # we are not the first so backoff
                            user.remove_self()
                            break
                elif action == 'deprovision':
                    if 'uuid' not in o:
                        raise KeyError('user without uuid')
                    users = User.get_users_with_name_identifier(o['uuid'])
                    for user in users:
                        user.set_deleted()
            except Exception as e:
                publisher.record_error(exception=e, context=_('Provisionning'), notify=True)
