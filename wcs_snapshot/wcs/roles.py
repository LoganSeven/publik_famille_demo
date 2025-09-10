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

import json
import urllib.parse
import xml.etree.ElementTree as ET

from quixote import get_publisher
from quixote.html import htmltext

from .qommon import _, get_cfg, misc
from .qommon.storage import StorableObject


class Role(StorableObject):
    _names = 'roles'
    xml_root_node = 'role'

    name = None
    uuid = None
    slug = None
    internal = False
    details = None
    emails = None
    emails_to_members = False
    allows_backoffice_access = False

    TEXT_ATTRIBUTES = ['name', 'uuid', 'slug', 'details', 'emails']
    BOOLEAN_ATTRIBUTES = ['internal', 'emails_to_members', 'allows_backoffice_access']

    def __init__(self, name=None, id=None):
        StorableObject.__init__(self, id=id)
        self.name = name

    def __eq__(self, other):
        return bool(self.__class__ is other.__class__ and self.id == other.id)

    def migrate(self):
        pass

    def store(self):
        if self.slug is None:
            # set slug if it's not yet there
            self.slug = self.get_new_slug()
        super().store()
        self.adjust_permissions()

    def adjust_permissions(self):
        if get_publisher().has_site_option('give-all-permissions-to-first-role') and self.count() == 1:
            if not get_publisher().cfg.get('admin-permissions'):
                from wcs.admin.settings import SettingsDirectory

                get_publisher().cfg['admin-permissions'] = {
                    k[0]: [str(self.id)] for k in SettingsDirectory.get_admin_permission_sections()
                }
                get_publisher().write_cfg()
            if not self.allows_backoffice_access:
                self.allows_backoffice_access = True
                self.store()

    def get_emails(self):
        emails = self.emails or []
        if not self.emails_to_members:
            return emails
        users_with_roles = get_publisher().user_class.get_users_with_role(self.id)
        emails.extend([x.email for x in users_with_roles if x.email and x.is_active])
        return emails

    def is_internal(self):
        return self.internal

    def get_substitution_variables(self, prefix=''):
        data = {}
        data[prefix + 'name'] = self.name
        data[prefix + 'details'] = self.details or ''
        data[prefix + 'emails'] = ', '.join(self.emails or [])
        data[prefix + 'uuid'] = self.uuid
        return data

    def get_json_export_dict(self):
        return {
            'name': self.name,
            'text': self.name,  # generic key
            'allows_backoffice_access': self.allows_backoffice_access,
            'emails': [email for email in self.emails or []],
            'details': self.details or '',
            'emails_to_members': self.emails_to_members,
            'slug': self.slug,
            'id': self.id,
        }

    def export_to_xml(self, include_id=False):
        root = ET.Element(self.xml_root_node)
        if include_id and self.id:
            root.attrib['id'] = str(self.id)
        for text_attribute in list(self.TEXT_ATTRIBUTES):
            if not hasattr(self, text_attribute) or not getattr(self, text_attribute):
                continue
            ET.SubElement(root, text_attribute).text = getattr(self, text_attribute)
        for boolean_attribute in self.BOOLEAN_ATTRIBUTES:
            if not hasattr(self, boolean_attribute):
                continue
            value = getattr(self, boolean_attribute)
            if value:
                value = 'true'
            else:
                value = 'false'
            ET.SubElement(root, boolean_attribute).text = value
        return root

    def export_for_application(self):
        return (json.dumps({'name': self.name, 'slug': self.slug, 'uuid': self.uuid}), 'application/json')

    @classmethod
    def import_from_xml(cls, fd, include_id=False):
        try:
            tree = ET.parse(fd)
        except Exception:
            raise ValueError()

        role = cls()

        # if the tree we get is actually a ElementTree for real, we get its
        # root element and go on happily.
        if not ET.iselement(tree):
            tree = tree.getroot()

        if include_id and tree.attrib.get('id'):
            role.id = tree.attrib.get('id')
        for text_attribute in list(cls.TEXT_ATTRIBUTES):
            value = tree.find(text_attribute)
            if value is None or value.text is None:
                continue
            setattr(role, text_attribute, misc.xml_node_text(value))

        for boolean_attribute in cls.BOOLEAN_ATTRIBUTES:
            value = tree.find(boolean_attribute)
            if value is None:
                continue
            setattr(role, boolean_attribute, value.text == 'true')

        return role

    @classmethod
    def resolve(cls, uuid=None, slug=None, name=None):
        if uuid:
            try:
                return cls.get_on_index(uuid, 'uuid')
            except KeyError:
                pass
            try:
                return cls.get(uuid)
            except KeyError:
                pass
            try:
                return cls.get_on_index(uuid, 'slug')
            except KeyError:
                pass
        if slug:
            try:
                return cls.get_on_index(slug, 'slug')
            except KeyError:
                pass
        if name:
            for role in cls.select():
                if role.name == name:
                    return role
        return None

    def get_as_inline_html(self):
        from .qommon.ident.idp import is_idp_managing_user_roles

        if not (is_idp_managing_user_roles() and self.uuid):
            return self.name

        idps = get_cfg('idp', {})
        entity_id = list(idps.values())[0]['metadata_url']
        base_url = entity_id.split('idp/saml2/metadata')[0]
        url = urllib.parse.urljoin(base_url, '/manage/roles/uuid:%s/' % self.uuid)

        return htmltext('<a href="%(url)s">%(name)s</a>') % {'url': url, 'name': self.name}

    @classmethod
    def get_role_by_node(cls, role_node, include_id=False):
        value = misc.xml_node_text(role_node)
        if value is None:
            return None
        if value.startswith('_') or value == 'logged-users':
            return value

        if include_id:
            role_id = role_node.attrib.get('role_id')
            if role_id and cls.get(role_id, ignore_errors=True):
                return role_id

        role_slug = role_node.attrib.get('slug')
        role = cls.resolve(uuid=None, slug=role_slug, name=value)
        if role:
            return role.id

        return None


def logged_users_role():
    volatile_role = Role.volatile()
    volatile_role.id = 'logged-users'
    volatile_role.name = _('Logged Users')
    return volatile_role


def get_user_roles():
    t = sorted(
        (misc.simplify(x.name), x.id, x.name, x.id)
        for x in get_publisher().role_class.select()
        if not x.is_internal()
    )
    return [x[1:] for x in t]
