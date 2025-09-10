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

import collections
import itertools
import uuid
import xml.etree.ElementTree as ET
from contextlib import contextmanager

from quixote import get_publisher, get_response

from wcs.sql import SqlBlockDef

from . import data_sources, fields
from .formdata import FormData
from .qommon import _, misc
from .qommon.errors import UnknownReferencedErrorMixin
from .qommon.storage import Equal, StorableObject, StoredObjectMixin
from .qommon.substitution import CompatibilityNamesDict
from .qommon.template import Template
from .qommon.xml_storage import PostConditionsXmlMixin


class BlockdefImportError(Exception):
    def __init__(self, msg, msg_args=None, details=None):
        self.msg = msg
        self.msg_args = msg_args or ()
        self.details = details


class BlockdefImportUnknownReferencedError(UnknownReferencedErrorMixin, BlockdefImportError):
    pass


class BlockDef(SqlBlockDef, PostConditionsXmlMixin, StoredObjectMixin):
    _names = 'blockdefs'
    backoffice_class = 'wcs.admin.blocks.BlockDirectory'
    category_class = 'wcs.categories.BlockCategory'
    xml_root_node = 'block'
    verbose_name = _('Block of fields')
    verbose_name_plural = _('Blocks of fields')
    var_prefixes = ['block']
    fields_count_total_soft_limit = 30
    fields_count_total_hard_limit = 60
    may_appear_in_frontoffice = True

    name = None
    slug = None
    fields = None
    digest_template = None
    category_id = None
    documentation = None
    post_conditions = None

    SLUG_DASH = '_'

    # declarations for serialization
    TEXT_ATTRIBUTES = ['name', 'slug', 'digest_template', 'documentation']

    def __init__(self, name=None, **kwargs):
        super().__init__(**kwargs)
        self.name = name
        self.fields = []

    @property
    def category(self):
        from wcs.categories import BlockCategory

        return BlockCategory.get(self.category_id, ignore_errors=True)

    @category.setter
    def category(self, category):
        if category:
            self.category_id = category.id
        elif self.category_id:
            self.category_id = None

    def migrate(self):
        changed = False
        for f in self.fields or []:
            changed |= f.migrate()
        if changed:
            self.store(comment=_('Automatic update'), snapshot_store_user=False)

    def store(
        self,
        comment=None,
        *,
        snapshot_store_user=True,
        application=None,
        application_ignore_change=False,
        **kwargs,
    ):
        from wcs.carddef import CardDef
        from wcs.formdef import FormDef

        assert not self.is_readonly()
        if self.slug is None:
            # set slug if it's not yet there
            self.slug = self.get_new_slug()

        object_only = kwargs.pop('object_only', False)
        super().store(**kwargs)
        if object_only:
            return
        if get_publisher().snapshot_class:
            get_publisher().snapshot_class.snap(
                instance=self,
                comment=comment,
                store_user=snapshot_store_user,
                application=application,
                application_ignore_change=application_ignore_change,
            )

        # update relations
        for objdef in itertools.chain(
            FormDef.select(ignore_errors=True, ignore_migration=True),
            CardDef.select(ignore_errors=True, ignore_migration=True),
        ):
            for field in objdef.get_all_fields():
                if field.key == 'block' and field.block_slug == self.slug:
                    objdef.store()

                    if get_response():
                        from wcs.admin.tests import TestsAfterJob

                        context = _('in field block "%s"') % field.label
                        get_publisher().add_after_job(
                            TestsAfterJob(objdef, reason='%s (%s)' % (comment, context))
                        )
                    break

    def get_new_field_id(self):
        return 'bf%s' % str(uuid.uuid4())

    def get_admin_url(self):
        base_url = get_publisher().get_backoffice_url()
        return '%s/forms/blocks/%s/' % (base_url, self.id)

    def get_field_admin_url(self, field):
        return self.get_admin_url() + '%s/' % field.id

    def get_data_fields(self):
        return [field for field in self.fields or [] if not field.is_no_data_field]

    def get_widget_fields(self):
        return [field for field in self.fields or [] if isinstance(field, fields.WidgetField)]

    def get_display_value(self, value):
        if not self.digest_template:
            return self.name

        from .variables import LazyBlockDataVar

        context = CompatibilityNamesDict({'block_var': LazyBlockDataVar(self.fields, value)})
        # for backward compatibility it is also possible to use <slug>_var_<whatever>
        context[self.slug.replace('-', '_') + '_var'] = context['block_var']
        return Template(self.digest_template, autoescape=False).render(context)

    def get_substitution_counter_variables(self, index):
        return CompatibilityNamesDict(
            {
                'block_counter': {
                    'index0': index,
                    'index': index + 1,
                }
            }
        )

    def get_dependencies(self):
        yield self.category
        for field in self.fields or []:
            yield from field.get_dependencies()
        post_conditions = self.post_conditions or []
        for post_condition in post_conditions:
            condition = post_condition.get('condition') or {}
            if condition.get('type') == 'django':
                yield from misc.get_dependencies_from_template(condition.get('value'))

    def export_to_xml(self, include_id=False):
        root = ET.Element(self.xml_root_node)
        if include_id and self.id:
            root.attrib['id'] = str(self.id)
        for text_attribute in list(self.TEXT_ATTRIBUTES):
            if not hasattr(self, text_attribute) or not getattr(self, text_attribute):
                continue
            ET.SubElement(root, text_attribute).text = getattr(self, text_attribute)

        from wcs.categories import BlockCategory

        BlockCategory.object_category_xml_export(self, root, include_id=include_id)

        fields = ET.SubElement(root, 'fields')
        for field in self.fields or []:
            fields.append(field.export_to_xml(include_id=True))

        self.post_conditions_export_to_xml(root, include_id=include_id)

        return root

    @classmethod
    def import_from_xml(
        cls,
        fd,
        include_id=False,
        check_datasources=True,
        check_deprecated=False,
        ignore_missing_dependencies=False,
    ):
        try:
            tree = ET.parse(fd)
        except Exception:
            raise ValueError()
        blockdef = cls.import_from_xml_tree(
            tree,
            include_id=include_id,
            check_datasources=check_datasources,
            check_deprecated=check_deprecated,
            ignore_missing_dependencies=ignore_missing_dependencies,
        )

        if blockdef.slug:
            try:
                cls.get_on_index(blockdef.slug, 'slug', ignore_migration=True)
            except KeyError:
                pass
            else:
                blockdef.slug = blockdef.get_new_slug()

        return blockdef

    @classmethod
    def import_from_xml_tree(
        cls,
        tree,
        include_id=False,
        check_datasources=True,
        check_deprecated=False,
        ignore_missing_dependencies=False,
        **kwargs,
    ):
        from wcs.backoffice.deprecations import DeprecatedElementsDetected, DeprecationsScan

        blockdef = cls()
        if tree.find('name') is None or not tree.find('name').text:
            raise BlockdefImportError(_('Missing name'))

        # if the tree we get is actually a ElementTree for real, we get its
        # root element and go on happily.
        if not ET.iselement(tree):
            tree = tree.getroot()

        if tree.tag != cls.xml_root_node:
            raise BlockdefImportError(
                _('Provided XML file is invalid, it starts with a <%(seen)s> tag instead of <%(expected)s>')
                % {'seen': tree.tag, 'expected': cls.xml_root_node}
            )

        if include_id and tree.attrib.get('id'):
            blockdef.id = tree.attrib.get('id')
        for text_attribute in list(cls.TEXT_ATTRIBUTES):
            value = tree.find(text_attribute)
            if value is None or value.text is None:
                continue
            setattr(blockdef, text_attribute, misc.xml_node_text(value))

        from wcs.fields.base import get_field_class_by_type

        blockdef.fields = []
        unknown_field_types = set()
        for field in tree.find('fields'):
            try:
                field_o = get_field_class_by_type(field.findtext('type'))()
            except KeyError:
                field_type = field.findtext('type')
                unknown_field_types.add(field_type)
                continue
            field_o.init_with_xml(field, include_id=True)
            blockdef.fields.append(field_o)

        from wcs.categories import BlockCategory

        BlockCategory.object_category_xml_import(blockdef, tree, include_id=include_id)

        post_conditions_node = tree.find('post_conditions')
        blockdef.post_conditions_init_with_xml(post_conditions_node, include_id=include_id)

        unknown_datasources = set()
        if check_datasources:
            from wcs.carddef import CardDef

            # check if datasources are defined
            for field in blockdef.fields:
                data_source = getattr(field, 'data_source', None)
                if data_source:
                    data_source_id = data_source.get('type')
                    if isinstance(data_sources.get_object(data_source), data_sources.StubNamedDataSource):
                        unknown_datasources.add(data_source_id)
                    elif data_source_id and data_source_id.startswith('carddef:'):
                        parts = data_source_id.split(':')
                        # check if carddef exists
                        url_name = parts[1]
                        try:
                            CardDef.get_by_urlname(url_name)
                        except KeyError:
                            unknown_datasources.add(data_source_id)
                            continue

                        if len(parts) == 2 or parts[2] == '_with_user_filter':
                            continue

                        lookup_criterias = [
                            Equal('formdef_type', 'carddef'),
                            Equal('visibility', 'datasource'),
                            Equal('slug', parts[2]),
                        ]
                        try:
                            get_publisher().custom_view_class.select(lookup_criterias)[0]
                        except IndexError:
                            unknown_datasources.add(data_source_id)

        if (unknown_field_types or unknown_datasources) and not ignore_missing_dependencies:
            details = collections.defaultdict(set)
            if unknown_field_types:
                details[_('Unknown field types')].update(unknown_field_types)
            if unknown_datasources:
                details[_('Unknown datasources')].update(unknown_datasources)
            raise BlockdefImportUnknownReferencedError(_('Unknown referenced objects'), details=details)

        if check_deprecated:
            # check for deprecated elements
            job = DeprecationsScan()
            try:
                job.check_deprecated_elements_in_object(blockdef)
            except DeprecatedElementsDetected as e:
                raise BlockdefImportError(str(e))

        return blockdef

    def get_json_export_dict(self, include_id=False):
        root = {}
        if include_id and self.id:
            root['id'] = str(self.id)
        for attr in self.TEXT_ATTRIBUTES:
            root[attr] = getattr(self, attr)
        root['fields'] = []
        if self.fields:
            for field in self.fields:
                root['fields'].append(field.export_to_json(include_id=include_id))
        return root

    def get_usage_fields(self):
        from wcs.formdef_base import get_formdefs_of_all_kinds

        for formdef in get_formdefs_of_all_kinds():
            for field in formdef.fields:
                if field.key == 'block' and field.block_slug == self.slug:
                    field.formdef = formdef
                    yield field

    def get_usage_formdefs(self):
        from wcs.formdef_base import get_formdefs_of_all_kinds

        for formdef in get_formdefs_of_all_kinds():
            for field in formdef.fields:
                if field.key == 'block' and field.block_slug == self.slug:
                    yield formdef
                    break

    def is_used(self):
        return any(self.get_usage_formdefs())

    @contextmanager
    def evaluation_context(self, value, row_index):
        from .variables import LazyBlockDataVar

        context = self.get_substitution_counter_variables(row_index)
        context['block_var'] = LazyBlockDataVar(self.fields, value)
        with get_publisher().substitutions.temporary_feed(context):
            yield

    def i18n_scan(self):
        location = 'forms/blocks/%s/' % self.id
        for field in self.fields or []:
            yield from field.i18n_scan(base_location=location)
        for post_condition in self.post_conditions or []:
            yield location, None, post_condition.get('error_message')

    def get_all_fields(self):
        return self.fields

    def data_class(self):
        return type('fake_formdata', (FormData,), {'_formdef': self})


class FileBlockDef(StorableObject, PostConditionsXmlMixin):
    # legacy class for migration
    _names = 'blockdefs'
    _reset_class = False
