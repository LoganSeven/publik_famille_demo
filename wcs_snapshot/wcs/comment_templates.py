# w.c.s. - web application for online forms
# Copyright (C) 2005-2022  Entr'ouvert
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

from collections import defaultdict

from quixote import get_publisher

import wcs.sql
from wcs.categories import CommentTemplateCategory
from wcs.qommon import _, get_logger
from wcs.qommon.form import OptGroup
from wcs.qommon.misc import get_dependencies_from_template
from wcs.qommon.storage import StoredObjectMixin
from wcs.qommon.xml_storage import XmlObjectMixin


class CommentTemplate(wcs.sql.SqlCommentTemplate, StoredObjectMixin, XmlObjectMixin):
    _names = 'comment-templates'
    xml_root_node = 'comment-template'
    backoffice_class = 'wcs.admin.comment_templates.CommentTemplatePage'
    verbose_name = _('Comment template')
    verbose_name_plural = _('Comment templates')

    id = None
    name = None
    slug = None
    documentation = None
    comment = None
    attachments = []
    category_id = None

    # declarations for serialization
    XML_NODES = [
        ('name', 'str'),
        ('slug', 'str'),
        ('description', 'str'),  # legacy
        ('documentation', 'str'),
        ('comment', 'str'),
        ('attachments', 'str_list'),
    ]

    def __init__(self, name=None):
        self.name = name

    def migrate(self):
        changed = False
        if getattr(self, 'description', None):  # 2024-04-07
            self.documentation = getattr(self, 'description')
            self.description = None
            changed = True
        if changed:
            self.store(comment=_('Automatic update'), snapshot_store_user=False)
        return changed

    @property
    def category(self):
        return CommentTemplateCategory.get(self.category_id, ignore_errors=True)

    @category.setter
    def category(self, category):
        if category:
            self.category_id = category.id
        elif self.category_id:
            self.category_id = None

    def get_admin_url(self):
        base_url = get_publisher().get_backoffice_url()
        return '%s/workflows/comment-templates/%s/' % (base_url, self.id)

    def store(self, comment=None, snapshot_store_user=True, application=None, *args, **kwargs):
        assert not self.is_readonly()
        if self.slug is None:
            # set slug if it's not yet there
            self.slug = self.get_new_slug()
        super().store(*args, **kwargs)
        if get_publisher().snapshot_class:
            get_publisher().snapshot_class.snap(
                instance=self, store_user=snapshot_store_user, comment=comment, application=application
            )

    def get_places_of_use(self):
        from wcs.workflows import Workflow

        for workflow in Workflow.select(ignore_errors=True, ignore_migration=True):
            for item in workflow.get_all_items():
                if item.key != 'register-comment':
                    continue
                if item.comment_template == self.slug:
                    yield item

    def is_in_use(self):
        return any(self.get_places_of_use())

    @classmethod
    def get_as_options_list(cls):
        def get_option(mt):
            option = [mt.slug, mt.name, mt.slug]
            if get_publisher().get_backoffice_root().is_accessible('workflows'):
                option.append({'data-goto-url': mt.get_admin_url()})
            return option

        comment_templates_by_category_names = defaultdict(list)
        for comment_template in cls.select(order_by='name'):
            name = ''
            if comment_template.category:
                name = comment_template.category.name
            comment_templates_by_category_names[name].append(comment_template)
        category_names = list(comment_templates_by_category_names.keys())
        if len(category_names) == 1 and category_names[0] == '':
            # no category found
            return [get_option(mt) for mt in comment_templates_by_category_names['']]
        options = []
        # sort categories
        category_names = sorted(category_names)
        # comment template without categories at the end
        if category_names[0] == '':
            category_names = category_names[1:] + ['']
        # group by category name
        for name in category_names:
            options.append(OptGroup(name or _('Without category')))
            options.extend([get_option(mt) for mt in comment_templates_by_category_names[name]])
        return options

    @classmethod
    def get_by_slug(cls, slug, ignore_errors=True):
        comment_template = super().get_by_slug(slug, ignore_errors=ignore_errors)
        if comment_template is None:
            get_logger().warning("comment template '%s' does not exist" % slug)
        return comment_template

    def get_dependencies(self):
        yield self.category
        for string in self.get_computed_strings():
            yield from get_dependencies_from_template(string)

    def get_computed_strings(self):
        yield self.comment

    @classmethod
    def has_admin_access(cls, user=None):
        return CommentTemplateCategory.has_global_access()

    def export_to_xml(self, include_id=False):
        root = super().export_to_xml(include_id=include_id)
        CommentTemplateCategory.object_category_xml_export(self, root, include_id=include_id)
        return root

    @classmethod
    def import_from_xml_tree(cls, tree, include_id=False, **kwargs):
        comment_template = super().import_from_xml_tree(tree, include_id=include_id, **kwargs)
        CommentTemplateCategory.object_category_xml_import(comment_template, tree, include_id=include_id)
        comment_template.migrate()
        return comment_template
