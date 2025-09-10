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
import xml.etree.ElementTree as ET

from quixote import get_publisher, get_request

from wcs.qommon import _
from wcs.qommon.misc import simplify, xml_node_text
from wcs.qommon.storage import Equal, NotEqual, StorableObject


class CustomView(StorableObject):
    _names = 'custom-views'

    title = None
    slug = None
    user_id = None
    role_id = None
    visibility = 'owner'
    formdef_type = None
    formdef_id = None
    is_default = False
    columns = None
    filters = None
    group_by = None
    order_by = None
    author_id = None

    xml_root_node = 'custom_view'

    def migrate(self):
        changed = False
        # 2024-04-10
        if self.columns and 'submission_agent' in [x['id'] for x in self.columns['list']]:
            self.columns['list'] = [
                {'id': x['id'].replace('submission_agent', 'submission-agent')} for x in self.columns['list']
            ]
            changed = True
        if changed:
            self.store()

    @property
    def user(self):
        return get_publisher().user_class.get(self.user_id)

    @user.setter
    def user(self, value):
        self.user_id = str(value.id)

    @property
    def author(self):
        return get_publisher().user_class.get(self.author_id, ignore_errors=True)

    @author.setter
    def author(self, value):
        self.author_id = str(value.id)

    @property
    def formdef(self):
        from wcs.carddef import CardDef
        from wcs.formdef import FormDef

        if self.formdef_type == 'formdef':
            return FormDef.cached_get(self.formdef_id)
        return CardDef.cached_get(self.formdef_id)

    @formdef.setter
    def formdef(self, value):
        self.formdef_id = str(value.id)
        self.formdef_type = value.xml_root_node

    def remove_self(self):
        super().remove_self()
        try:
            formdef = self.formdef
        except KeyError:
            pass
        else:
            view_digest_key = 'custom-view:%s' % self.get_url_slug()
            store_message = _('Deletion of custom view (%s)') % self.title
            if view_digest_key in (formdef.digest_templates or {}):
                del formdef.digest_templates[view_digest_key]
                formdef.store(comment=store_message)
            elif self.visibility != 'owner':
                # a snapshot will be stored only if there are changes
                formdef.store(comment=store_message)

    @classmethod
    def select_shared_for_formdef(cls, formdef):
        return cls.select(
            [
                Equal('formdef_type', formdef.xml_root_node),
                Equal('formdef_id', str(formdef.id)),
                NotEqual('visibility', 'owner'),
            ],
            order_by=['slug', 'visibility'],
        )

    def match(self, user, formdef):
        if self.formdef_type != formdef.xml_root_node:
            return False
        if self.formdef_id != str(formdef.id):
            return False
        if self.visibility == 'owner' and (
            user is None or not hasattr(user, 'id') or self.user_id != str(user.id)
        ):
            return False
        if self.visibility == 'role' and (user is None or self.role_id not in user.get_roles()):
            return False
        return True

    def set_from_qs(self, qs):
        parsed_qs = urllib.parse.parse_qsl(qs)
        self.columns = {
            'list': [
                {'id': key} for (key, value) in parsed_qs if value == 'on' and not key.startswith('filter-')
            ],
        }

        columns_order = [x[1] for x in parsed_qs if x[0] == 'columns-order']
        if columns_order:
            field_order = columns_order[0].split(',')

            def field_position(x):
                if x['id'] in field_order:
                    return field_order.index(x['id'])
                return 9999

            self.columns['list'].sort(key=field_position)

        order_by = [x[1] for x in parsed_qs if x[0] == 'order_by']
        if order_by:
            self.order_by = order_by[0]

        self.filters = {key: value for (key, value) in parsed_qs if key.startswith('filter')}

    def ensure_slug(self):
        if self.slug:
            return
        clauses = [
            Equal('formdef_type', self.formdef_type),
            Equal('formdef_id', self.formdef_id),
        ]
        if self.visibility == 'owner':
            clauses += [
                Equal('visibility', self.visibility),
                Equal('user_id', self.user_id),
            ]
        existing_slugs = {x.slug for x in self.select(clauses)}
        base_slug = simplify(self.title)
        if base_slug.startswith('user-'):
            # prevent a slug starting with user- as it's used in URLs
            base_slug = 'userx-' + base_slug[5:]

        # prevent conflicts with system view names
        from wcs.backoffice.data_management import CardPage
        from wcs.backoffice.management import FormPage

        reserved_slugs = [
            x if isinstance(x, str) else x[0] for x in FormPage._q_exports + CardPage._q_exports
        ] + ['ics']

        if base_slug in reserved_slugs:
            base_slug = 'x-' + base_slug

        self.slug = base_slug
        i = 2
        while self.slug in existing_slugs:
            self.slug = '%s-%s' % (base_slug, i)
            i += 1

    def get_url_slug(self):
        if self.visibility == 'owner':
            return 'user-%s' % self.slug
        return self.slug

    def store(self, *args, **kwargs):
        self.ensure_slug()
        return super().store(*args, **kwargs)

    def get_columns(self):
        if self.columns and 'list' in self.columns:
            return [x['id'] for x in self.columns['list']]
        return []

    def get_filter(self):
        return self.filters.get('filter')

    def get_status_filter_operator(self):
        return self.filters.get('filter-operator', 'eq')

    def get_filters_dict(self):
        return self.filters

    def get_default_filters(self):
        return [key[7:] for key in self.filters if key.startswith('filter-')]

    def get_criterias(self, formdef=None, compile_templates=False, keep_templates=False):
        from wcs.backoffice.management import FormPage

        if formdef is not None:
            assert str(formdef.id) == self.formdef_id
        else:
            formdef = self.formdef

        form_page = FormPage(formdef=formdef, view=self, update_breadcrumbs=False)
        criterias = form_page.get_view_criterias(
            custom_view=self,
            compile_templates=compile_templates,
            keep_templates=keep_templates,
        )

        from wcs.forms.backoffice import FormDefUI

        selected_filter = self.get_filter()
        selected_status_filter_operator = self.get_status_filter_operator()
        if selected_filter:
            criterias.extend(
                FormDefUI(formdef).get_status_criterias(
                    selected_filter,
                    selected_status_filter_operator,
                    user=get_request().user if get_request() else None,
                )
            )

        return criterias

    def export_to_xml(self, include_id=False):
        root = ET.Element(self.xml_root_node)
        fields = [
            'title',
            'slug',
            'visibility',
            'filters',
            'is_default',
            'columns',
            'order_by',
            'group_by',
        ]
        for attribute in fields:
            if getattr(self, attribute, None) is not None:
                val = getattr(self, attribute)
                el = ET.SubElement(root, attribute)
                if attribute == 'columns':
                    for field_dict in self.columns.get('list') or []:
                        if not isinstance(field_dict, dict):
                            continue
                        for k, v in sorted(field_dict.items()):
                            ET.SubElement(el, k).text = str(v)
                elif isinstance(val, dict):
                    for k, v in sorted(val.items()):
                        ET.SubElement(el, k).text = str(v)
                else:
                    el.text = str(val)

        if self.visibility == 'role' and self.role_id:
            from wcs.workflows import get_role_name_and_slug

            try:
                role_name, role_slug = get_role_name_and_slug(self.role_id)
            except KeyError:
                # skip broken/missing roles
                return None
            sub = ET.SubElement(root, 'role')
            if role_slug:
                sub.attrib['slug'] = role_slug
            if include_id:
                sub.attrib['role_id'] = str(self.role_id)
            sub.text = role_name

        return root

    def init_with_xml(self, elem, include_id=False):
        fields = [
            'title',
            'slug',
            'visibility',
            'filters',
            'is_default',
            'columns',
            'order_by',
            'group_by',
        ]
        for attribute in fields:
            el = elem.find(attribute)
            if el is None:
                continue
            if attribute == 'is_default':
                self.is_default = bool(xml_node_text(el).lower() == 'true')
            elif attribute == 'filters':
                v = {}
                for e in el:
                    v[e.tag] = xml_node_text(e)
                setattr(self, attribute, v)
            elif attribute == 'columns':
                v = []
                for e in el:
                    v.append({e.tag: xml_node_text(e)})
                setattr(self, attribute, {'list': v})
            else:
                setattr(self, attribute, xml_node_text(el))

        self.role_id = get_publisher().role_class.get_role_by_node(elem.find('role'), include_id=include_id)

    def get_dependencies(self):
        if self.visibility == 'role' and self.role_id:
            yield get_publisher().role_class.get(self.role_id, ignore_errors=True)
