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

import xml.etree.ElementTree as ET

from quixote import get_publisher, get_request
from quixote.html import htmltext

import wcs.sql
from wcs.sql_criterias import Equal

from .qommon import _
from .qommon.misc import simplify, xml_node_text
from .qommon.storage import StorableObject, StoredObjectMixin
from .qommon.substitution import Substitutions
from .qommon.xml_storage import XmlObjectMixin


class CategoryImportError(Exception):
    pass


class Category(wcs.sql.SqlCategory, StoredObjectMixin, XmlObjectMixin):
    _names = 'categories'
    objects_type = 'forms'
    xml_root_node = 'category'
    backoffice_class = 'wcs.admin.categories.CategoryPage'
    backoffice_base_url = 'forms/categories/'
    global_access_sections = ['forms']
    verbose_name = _('Category of forms')
    verbose_name_plural = _('Categories')

    name = None
    url_name = None
    description = None
    position = None
    redirect_url = None

    _export_roles = None
    _statistics_roles = None
    _management_roles = None

    # declarations for serialization
    XML_NODES = [
        ('name', 'str'),
        ('url_name', 'str'),
        ('description', 'str'),
        ('redirect_url', 'str'),
        ('position', 'int'),
        ('export_roles', 'roles'),
        ('statistics_roles', 'roles'),
        ('management_roles', 'roles'),
    ]

    def __init__(self, name=None):
        StorableObject.__init__(self)
        self.name = name

    @classmethod
    def get_object_class(cls):
        from .formdef import FormDef

        return FormDef

    @classmethod
    def get_by_urlname(cls, url_name, ignore_errors=False):
        objects = [x for x in cls.select() if x.url_name == url_name]
        if objects:
            return objects[0]
        if ignore_errors:
            return None
        raise KeyError()

    get_by_slug = get_by_urlname

    @property
    def slug(self):
        return self.url_name

    @slug.setter
    def slug(self, value):
        self.url_name = value

    @classmethod
    def has_urlname(cls, url_name):
        objects = [x for x in cls.select() if x.url_name == url_name]
        if objects:
            return True
        return False

    def get_admin_url(self):
        return '%s/%s%s/' % (get_publisher().get_backoffice_url(), self.backoffice_base_url, self.id)

    def store(
        self, *args, comment=None, snapshot_store_user=True, application=None, store_snapshot=True, **kwargs
    ):
        if not self.url_name:
            existing_slugs = {
                x.url_name: True for x in self.select(ignore_migration=True, ignore_errors=True)
            }
            base_slug = simplify(self.name)
            if base_slug in get_publisher().root_directory_class._q_exports:
                base_slug = 'cat-%s' % base_slug
            self.url_name = base_slug
            i = 2
            while self.url_name in existing_slugs:
                self.url_name = '%s-%s' % (base_slug, i)
                i += 1
        super().store(*args, **kwargs)
        if get_publisher().snapshot_class and store_snapshot:
            get_publisher().snapshot_class.snap(
                instance=self, comment=comment, store_user=snapshot_store_user, application=application
            )

    @classmethod
    def has_admin_access(cls, user=None):
        backoffice_root = get_publisher().get_backoffice_root()
        for section in cls.global_access_sections:
            if backoffice_root.is_global_accessible(section):
                return True
        return False

    def is_managed_by_user(self):
        if self.has_admin_access():
            return True
        user_roles = set(get_request().user.get_roles())
        management_roles = {x.id for x in self.management_roles or []}  # noqa pylint: disable=not-an-iterable
        return bool(user_roles.intersection(management_roles))

    @classmethod
    def has_global_access(cls):
        global_access = False
        backoffice_root = get_publisher().get_backoffice_root()
        for section in cls.global_access_sections:
            global_access |= backoffice_root.is_global_accessible(section)
        return global_access

    @classmethod
    def select_for_user(cls):
        global_access = cls.has_global_access()
        user_roles = set(get_request().user.get_roles()) if get_request().user else []

        def filter_function(category):
            if global_access:
                return True
            management_roles = {x.id for x in category.management_roles or []}
            return bool(user_roles.intersection(management_roles))

        return [x for x in cls.select() if filter_function(x)]

    @classmethod
    def sort_by_position(cls, categories):
        # move categories with no defined position to the end
        categories.sort(key=lambda x: x.position if x and x.position is not None else 10000)

    def remove_self(self):
        for obj in self.get_object_class().select([Equal('category_id', str(self.id))]):
            obj.category_id = None
            obj.store()
        super().remove_self()

    def get_substitution_variables(self, minimal=False):
        d = {
            'category_name': self.name,
            'category_id': self.url_name,
            'category_slug': self.url_name,
        }
        if not minimal:
            d.update(
                {
                    'category_description': self.description,
                }
            )
        return d

    def get_url(self):
        base_url = get_publisher().get_frontoffice_url()
        return '%s/%s/' % (base_url, self.url_name)

    def get_description_html_text(self):
        if not self.description:
            return None
        text = self.description
        if text[0] != '<':
            text = '<p>%s</p>' % text
        return htmltext(text)

    def has_permission(self, permission_name, user):
        if user.is_admin:
            return True
        permission_roles = getattr(self, '%s_roles' % permission_name, None) or []
        if not permission_roles:
            return True
        user_roles = set(user.get_roles()) if user else set()
        return bool(user_roles.intersection([x.id for x in permission_roles]))

    @classmethod
    def object_category_xml_export(cls, obj, root, include_id):
        if obj.category:
            elem = ET.SubElement(root, 'category')
            elem.attrib['slug'] = str(obj.category.slug)
            elem.text = obj.category.name
            if include_id:
                elem.attrib['category_id'] = str(obj.category.id)

    @classmethod
    def object_category_xml_import(cls, obj, tree, include_id):
        if tree.find('category') is None:
            return
        category_node = tree.find('category')
        if include_id and category_node.attrib.get('category_id'):
            category_id = str(category_node.attrib.get('category_id'))
            if cls.has_key(category_id):
                obj.category_id = category_id
        elif category_node.attrib.get('slug'):
            category = cls.get_by_slug(category_node.attrib.get('slug'), ignore_errors=True)
            if category:
                obj.category_id = category.id
        else:
            # legacy fallback to name lookup
            category = xml_node_text(category_node)
            for c in cls.select():
                if c.name == category:
                    obj.category_id = c.id
                    break

    @property
    def export_roles(self):
        return self._export_roles() if callable(self._export_roles) else self._export_roles

    @export_roles.setter
    def export_roles(self, value):
        self._export_roles = value

    @property
    def statistics_roles(self):
        return self._statistics_roles() if callable(self._statistics_roles) else self._statistics_roles

    @statistics_roles.setter
    def statistics_roles(self, value):
        self._statistics_roles = value

    @property
    def management_roles(self):
        return self._management_roles() if callable(self._management_roles) else self._management_roles

    @management_roles.setter
    def management_roles(self, value):
        self._management_roles = value

    def get_dependencies(self):
        for attr in ('export_roles', 'statistics_roles', 'management_roles'):
            yield from getattr(self, attr, None) or []

    def i18n_scan(self):
        location = '%s:%s' % (self.xml_root_node, self.id)
        yield location, None, self.name
        yield location, None, self.description


class CardDefCategory(Category):
    _names = 'carddef_categories'
    objects_type = 'cards'
    xml_root_node = 'carddef_category'
    backoffice_class = 'wcs.admin.categories.CardDefCategoryPage'
    backoffice_base_url = 'cards/categories/'
    global_access_sections = ['cards']
    verbose_name = _('Category of card models')

    # declarations for serialization
    XML_NODES = [
        ('name', 'str'),
        ('url_name', 'str'),
        ('description', 'str'),
        ('position', 'int'),
        ('export_roles', 'roles'),
        ('management_roles', 'roles'),
    ]

    @classmethod
    def get_object_class(cls):
        from .carddef import CardDef

        return CardDef


class WorkflowCategory(Category):
    _names = 'workflow_categories'
    objects_type = 'workflows'
    xml_root_node = 'workflow_category'
    backoffice_class = 'wcs.admin.categories.WorkflowCategoryPage'
    backoffice_base_url = 'workflows/categories/'
    global_access_sections = ['workflows']
    verbose_name = _('Category of workflows')

    # declarations for serialization
    XML_NODES = [
        ('name', 'str'),
        ('url_name', 'str'),
        ('description', 'str'),
        ('position', 'int'),
        ('management_roles', 'roles'),
    ]

    @classmethod
    def get_object_class(cls):
        from .workflows import Workflow

        return Workflow


class BlockCategory(Category):
    _names = 'block_categories'
    objects_type = 'blocks'
    xml_root_node = 'block_category'
    backoffice_class = 'wcs.admin.categories.BlockCategoryPage'
    backoffice_base_url = 'forms/blocks/categories/'
    global_access_sections = ['forms']
    verbose_name = _('Category of blocks')

    # declarations for serialization
    XML_NODES = [
        ('name', 'str'),
        ('url_name', 'str'),
        ('description', 'str'),
        ('position', 'int'),
        ('management_roles', 'roles'),
    ]

    @classmethod
    def get_object_class(cls):
        from .blocks import BlockDef

        return BlockDef


class MailTemplateCategory(Category):
    _names = 'mail_template_categories'
    objects_type = 'mail_template'
    xml_root_node = 'mail_template_category'
    backoffice_class = 'wcs.admin.categories.MailTemplateCategoryPage'
    backoffice_base_url = 'workflows/mail-templates/categories/'
    global_access_sections = ['workflows']
    verbose_name = _('Category of mail templates')

    # declarations for serialization
    XML_NODES = [
        ('name', 'str'),
        ('url_name', 'str'),
        ('description', 'str'),
        ('position', 'int'),
        ('management_roles', 'roles'),
    ]

    @classmethod
    def get_object_class(cls):
        from .mail_templates import MailTemplate

        return MailTemplate


class CommentTemplateCategory(Category):
    _names = 'comment_template_categories'
    objects_type = 'comment_template'
    xml_root_node = 'comment_template_category'
    backoffice_class = 'wcs.admin.categories.CommentTemplateCategoryPage'
    backoffice_base_url = 'workflows/comment-templates/categories/'
    global_access_sections = ['workflows']
    verbose_name = _('Category of comment templates')

    # declarations for serialization
    XML_NODES = [
        ('name', 'str'),
        ('url_name', 'str'),
        ('description', 'str'),
        ('position', 'int'),
        ('management_roles', 'roles'),
    ]

    @classmethod
    def get_object_class(cls):
        from .comment_templates import CommentTemplate

        return CommentTemplate


class DataSourceCategory(Category):
    _names = 'data_source_categories'
    objects_type = 'data_source'
    xml_root_node = 'data_source_category'
    backoffice_class = 'wcs.admin.categories.DataSourceCategoryPage'
    backoffice_base_url = 'forms/data-sources/categories/'
    global_access_sections = ['forms', 'cards', 'workflows']
    verbose_name = _('Category of data sources')

    # declarations for serialization
    XML_NODES = [
        ('name', 'str'),
        ('url_name', 'str'),
        ('description', 'str'),
        ('position', 'int'),
        ('management_roles', 'roles'),
    ]

    @classmethod
    def get_object_class(cls):
        from .data_sources import NamedDataSource

        return NamedDataSource


Substitutions.register('category_name', category=_('General'), comment=_('Category Name'))
Substitutions.register('category_description', category=_('General'), comment=_('Category Description'))
Substitutions.register('category_id', category=_('General'), comment=_('Category Identifier'))
