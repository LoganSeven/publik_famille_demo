# w.c.s. - web application for online forms
# Copyright (C) 2005-2014  Entr'ouvert
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
import xml.etree.ElementTree as ET

from quixote import get_publisher

from .misc import xml_node_text
from .storage import Contains, Equal, Or, StorableObject


class XmlObjectMixin:
    XML_NODES = []

    def export_to_xml(self, include_id=False):
        root = ET.Element(self.xml_root_node)
        if include_id and self.id:
            root.attrib['id'] = str(self.id)
        for attribute in self.XML_NODES:
            attribute_name, attribute_type = attribute
            if not getattr(self, attribute_name, None):
                continue
            element = ET.SubElement(root, attribute_name)
            export_method = getattr(self, 'export_%s_to_xml' % attribute_type)
            export_method(element, attribute_name, include_id=include_id)
        return root

    def export_str_to_xml(self, element, attribute_name, **kwargs):
        element.text = getattr(self, attribute_name)

    def export_int_to_xml(self, element, attribute_name, **kwargs):
        element.text = str(getattr(self, attribute_name))

    def export_bool_to_xml(self, element, attribute_name, **kwargs):
        element.text = 'true' if getattr(self, attribute_name) else 'false'

    def export_datetime_to_xml(self, element, attribute_name, **kwargs):
        element.text = getattr(self, attribute_name).isoformat()

    def export_str_list_to_xml(self, element, attribute_name, **kwargs):
        for item in getattr(self, attribute_name, None) or []:
            ET.SubElement(element, 'item').text = item

    def export_to_xml_string(self, include_id=False):
        x = self.export_to_xml(include_id=include_id)
        ET.indent(x)
        return ET.tostring(x)

    def export_roles_to_xml(self, element, attribute_name, include_id=False, **kwargs):
        for role in getattr(self, attribute_name, None) or []:
            sub = ET.SubElement(element, 'role')
            if include_id:
                sub.attrib['role-id'] = role.id
            sub.attrib['role-slug'] = role.slug
            sub.text = role.name

    @classmethod
    def import_from_xml(cls, fd, include_id=False, check_deprecated=False):
        try:
            tree = ET.parse(fd)
        except Exception:
            raise ValueError()
        return cls.import_from_xml_tree(tree, include_id=include_id, check_deprecated=check_deprecated)

    @classmethod
    def import_from_xml_tree(cls, tree, include_id=False, check_deprecated=False, **kwargs):
        obj = cls()

        # if the tree we get is actually a ElementTree for real, we get its
        # root element and go on happily.
        if not ET.iselement(tree):
            tree = tree.getroot()

        if tree.tag not in (cls.xml_root_node, cls._names):
            # note: cls._names is allowed for compatibility with legacy files.
            raise ValueError('root element mismatch (%s vs %s)' % (tree.tag, cls.xml_root_node))

        if include_id and tree.attrib.get('id'):
            obj.id = tree.attrib.get('id')

        for attribute in cls.XML_NODES:
            attribute_name, attribute_type = attribute
            element = tree.find(attribute_name)
            if element is None:
                continue
            import_method = getattr(obj, 'import_%s_from_xml' % attribute_type)
            setattr(
                obj,
                attribute_name,
                import_method(element, include_id=include_id),
            )
        return obj

    def import_str_from_xml(self, element, **kwargs):
        return xml_node_text(element)

    def import_int_from_xml(self, element, **kwargs):
        return int(element.text)

    def import_bool_from_xml(self, element, **kwargs):
        return bool(element.text == 'true')

    def import_datetime_from_xml(self, element, **kwargs):
        return datetime.datetime.strptime(element.text[:19], '%Y-%m-%dT%H:%M:%S')

    def import_str_list_from_xml(self, element, **kwargs):
        value = []
        for item in element.findall('item'):
            value.append(item.text)
        return value

    def import_roles_from_xml(self, element, include_id=False, **kwargs):
        criterias = []
        for sub in element:
            if sub.tag != 'role':
                continue
            if include_id and 'role-id' in sub.attrib:
                criterias.append(Equal('id', sub.attrib['role-id']))
            elif 'role-slug' in sub.attrib:
                criterias.append(Equal('slug', sub.attrib['role-slug']))
            else:
                role_name = xml_node_text(sub)
                if role_name:
                    criterias.append(Equal('name', role_name))
        if not criterias:
            return []

        def lazy_roles():
            return get_publisher().role_class.select([Or(criterias)], order_by='name')

        return lazy_roles

    def import_ds_roles_from_xml(self, element, include_id=False, **kwargs):
        imported_roles = self.import_roles_from_xml(element, include_id=include_id, **kwargs)
        if callable(imported_roles):
            imported_roles = imported_roles()
        role_ids = [x.id for x in imported_roles]
        for sub in element:
            if sub.tag == 'item':  # legacy support for <item>{id}</item>
                role_ids.append(xml_node_text(sub))
        return role_ids

    def export_ds_roles_to_xml(self, element, attribute_name, include_id=False, **kwargs):
        for role in get_publisher().role_class.select(
            [Contains('id', getattr(self, attribute_name, None) or [])]
        ):
            sub = ET.SubElement(element, 'role')
            sub.attrib['role-id'] = role.id  # always include id
            sub.attrib['role-slug'] = role.slug
            sub.text = role.name

    def export_kv_data_to_xml(self, element, attribute_name, **kwargs):
        for key, value in getattr(self, attribute_name).items():
            item = ET.SubElement(element, 'item')
            ET.SubElement(item, 'name').text = key
            ET.SubElement(item, 'value').text = value

    def import_kv_data_from_xml(self, element, **kwargs):
        if element is None:
            return

        data = {}
        for item in element.findall('item'):
            key = item.find('name').text
            value = item.find('value').text or ''
            data[key] = value

        return data


class XmlStorableObject(XmlObjectMixin, StorableObject):
    @classmethod
    def storage_load(cls, fd):
        return cls.import_from_xml(fd, include_id=True, check_deprecated=False)

    @classmethod
    def storage_dumps(cls, object):
        return object.export_to_xml_string(include_id=True)


class PostConditionsXmlMixin:
    def post_conditions_init_with_xml(self, node, include_id=False, snapshot=False):
        if node is None:
            return
        self.post_conditions = []
        for post_condition_node in node.findall('post_condition'):
            if post_condition_node.findall('condition/type'):
                condition = {
                    'type': xml_node_text(post_condition_node.find('condition/type')),
                    'value': xml_node_text(post_condition_node.find('condition/value')),
                }
            else:
                continue
            self.post_conditions.append(
                {
                    'condition': condition,
                    'error_message': xml_node_text(post_condition_node.find('error_message')),
                }
            )

    def post_conditions_export_to_xml(self, node, include_id=False):
        if not self.post_conditions:
            return

        conditions_node = ET.SubElement(node, 'post_conditions')
        for post_condition in self.post_conditions:
            post_condition_node = ET.SubElement(conditions_node, 'post_condition')
            condition_node = ET.SubElement(post_condition_node, 'condition')
            ET.SubElement(condition_node, 'type').text = str(
                (post_condition['condition'] or {}).get('type') or ''
            )
            ET.SubElement(condition_node, 'value').text = str(
                (post_condition['condition'] or {}).get('value') or ''
            )
            ET.SubElement(post_condition_node, 'error_message').text = str(
                post_condition['error_message'] or ''
            )
