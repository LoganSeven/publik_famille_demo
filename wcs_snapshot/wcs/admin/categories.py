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

from quixote import get_publisher, get_request, get_response, get_session, redirect
from quixote.directory import Directory
from quixote.html import TemplateIO, htmltext

from wcs.admin import utils
from wcs.backoffice.applications import ApplicationsDirectory
from wcs.backoffice.snapshots import SnapshotsDirectory
from wcs.blocks import BlockDef
from wcs.carddef import CardDef, get_cards_graph
from wcs.categories import (
    BlockCategory,
    CardDefCategory,
    Category,
    CommentTemplateCategory,
    DataSourceCategory,
    MailTemplateCategory,
    WorkflowCategory,
)
from wcs.comment_templates import CommentTemplate
from wcs.data_sources import NamedDataSource
from wcs.formdef import FormDef
from wcs.mail_templates import MailTemplate
from wcs.qommon import _, misc, template
from wcs.qommon.errors import AccessForbiddenError, TraversalError
from wcs.qommon.form import (
    FileWidget,
    Form,
    HtmlWidget,
    SingleSelectWidget,
    StringWidget,
    WidgetList,
    WysiwygTextWidget,
)
from wcs.workflows import Workflow


def get_categories(category_class):
    options = sorted(
        (misc.simplify(x.name), str(x.id), x.name, str(x.id)) for x in category_class.select_for_user()
    )
    options = [x[1:] for x in options]
    if options and category_class.has_global_access():
        options = [(None, '---', '')] + options
    return options


class CategoryUI:
    category_class = Category
    management_roles_hint_text = _('Roles allowed to create, edit and delete forms.')

    def __init__(self, category):
        self.category = category
        if self.category is None:
            self.category = self.category_class()

    def get_form(self, new=False):
        form = Form(enctype='multipart/form-data')
        form.add(
            StringWidget, 'name', title=_('Category Name'), required=True, size=30, value=self.category.name
        )
        form.add(
            WysiwygTextWidget,
            'description',
            title=_('Description'),
            cols=80,
            rows=10,
            value=self.category.description,
        )
        if self.category_class is Category:
            form.add(
                StringWidget,
                'redirect_url',
                size=32,
                title=_('URL Redirection'),
                hint=_('If set, redirect the site category page to the given URL instead of the site home.'),
                value=self.category.redirect_url,
            )

        if not new:
            # include permission fields
            roles = list(get_publisher().role_class.select(order_by='name'))
            if 'management_roles' in [x[0] for x in self.category_class.XML_NODES]:
                form.add(
                    WidgetList,
                    'management_roles',
                    title=_('Management Roles'),
                    element_type=SingleSelectWidget,
                    value=self.category.management_roles,
                    add_element_label=_('Add Role'),
                    element_kwargs={
                        'render_br': False,
                        'options': [(None, '---', None)]
                        + [(x, x.name, x.id) for x in roles if not x.is_internal()],
                    },
                    hint=self.management_roles_hint_text,
                )
            if 'export_roles' in [x[0] for x in self.category_class.XML_NODES]:
                form.add(
                    WidgetList,
                    'export_roles',
                    title=_('Export Roles'),
                    element_type=SingleSelectWidget,
                    value=self.category.export_roles,
                    add_element_label=_('Add Role'),
                    element_kwargs={
                        'render_br': False,
                        'options': [(None, '---', None)]
                        + [(x, x.name, x.id) for x in roles if not x.is_internal()],
                    },
                    hint=_('Roles allowed to export data.'),
                )
            if 'statistics_roles' in [x[0] for x in self.category_class.XML_NODES]:
                form.add(
                    WidgetList,
                    'statistics_roles',
                    title=_('Statistics Roles'),
                    element_type=SingleSelectWidget,
                    value=self.category.statistics_roles,
                    add_element_label=_('Add Role'),
                    element_kwargs={
                        'render_br': False,
                        'options': [(None, '---', None)]
                        + [(x, x.name, x.id) for x in roles if not x.is_internal()],
                    },
                    hint=_('Roles with access to the statistics page.'),
                )

        form.add_submit('submit', _('Submit'))
        form.add_submit('cancel', _('Cancel'))
        return form

    def submit_form(self, form):
        self.category.name = form.get_widget('name').parse()

        name = form.get_widget('name').parse()
        category_names = [x.name for x in self.category_class.select() if x.id != self.category.id]
        if name in category_names:
            form.get_widget('name').set_error(_('This name is already used.'))
            raise ValueError()

        for attribute in (
            'description',
            'redirect_url',
            'management_roles',
            'export_roles',
            'statistics_roles',
        ):
            widget = form.get_widget(attribute)
            if widget:
                setattr(self.category, attribute, widget.parse())

        self.category.store()


class CardDefCategoryUI(CategoryUI):
    category_class = CardDefCategory
    management_roles_hint_text = _('Roles allowed to create, edit and delete card models.')


class WorkflowCategoryUI(CategoryUI):
    category_class = WorkflowCategory
    management_roles_hint_text = _('Roles allowed to create, edit and delete workflows.')


class BlockCategoryUI(CategoryUI):
    category_class = BlockCategory
    management_roles_hint_text = _(
        'Roles allowed to create, edit and delete blocks of fields. '
        'Note this also requires a management role on forms.'
    )


class MailTemplateCategoryUI(CategoryUI):
    category_class = MailTemplateCategory
    management_roles_hint_text = _('Roles allowed to create, edit and delete mail templates.')


class CommentTemplateCategoryUI(CategoryUI):
    category_class = CommentTemplateCategory
    management_roles_hint_text = _('Roles allowed to create, edit and delete comment templates.')


class DataSourceCategoryUI(CategoryUI):
    category_class = DataSourceCategory
    management_roles_hint_text = _('Roles allowed to create, edit and delete data sources.')


class CategoryPage(Directory):
    category_class = Category
    category_ui_class = CategoryUI
    object_class = FormDef
    usage_title = _('Forms in this category')
    empty_message = _('No form associated to this category.')
    _q_exports = [
        '',
        'edit',
        'export',
        'delete',
        'description',
        ('history', 'snapshots_dir'),
    ]
    do_not_call_in_templates = True

    def __init__(self, component, instance=None):
        try:
            self.category = instance or self.category_class.get(component)
        except KeyError:
            raise TraversalError()
        self.category_ui = self.category_ui_class(self.category)
        self.snapshots_dir = SnapshotsDirectory(self.category)
        get_response().breadcrumb.append((component + '/', self.category.name))

    def _q_index(self):
        from wcs.applications import Application

        get_response().set_title(self.category.name)
        get_response().add_javascript(['popup.js'])
        if not self.category.is_readonly():
            Application.load_for_object(self.category)
        return template.QommonTemplateResponse(
            templates=['wcs/backoffice/category.html'],
            context={'view': self, 'category': self.category, 'has_sidebar': True},
            is_django_native=True,
        )

    def snapshot_info_block(self):
        return utils.snapshot_info_block(snapshot=self.category.snapshot_object)

    def last_modification_block(self):
        return utils.last_modification_block(obj=self.category)

    def get_formdefs(self):
        formdefs = self.object_class.select(order_by='name')
        return [x for x in formdefs if x.category_id == str(self.category.id)]

    def edit(self):
        form = self.category_ui.get_form()
        if form.get_submit() == 'cancel':
            return redirect('..')

        if form.get_submit() == 'submit' and not form.has_errors():
            try:
                self.category_ui.submit_form(form)
            except ValueError:
                pass
            else:
                return redirect('..')

        get_response().breadcrumb.append(('edit', _('Edit')))
        get_response().set_title(_('Edit Category'))
        r = TemplateIO(html=True)
        r += htmltext('<h2>%s</h2>') % _('Edit Category')
        r += form.render()
        return r.getvalue()

    def delete(self):
        form = Form(enctype='multipart/form-data')
        form.widgets.append(HtmlWidget('<p>%s</p>' % _('You are about to irrevocably delete this category.')))
        form.add_submit('delete', _('Delete'))
        form.add_submit('cancel', _('Cancel'))
        if form.get_widget('cancel').parse():
            return redirect('.')
        if not form.is_submitted() or form.has_errors():
            get_response().breadcrumb.append(('delete', _('Delete')))
            get_response().set_title(_('Delete Category'))
            r = TemplateIO(html=True)
            r += htmltext('<h2>%s %s</h2>') % (_('Deleting Category:'), self.category.name)
            r += form.render()
            return r.getvalue()

        get_publisher().snapshot_class.snap_deletion(self.category)
        self.category.remove_self()
        return redirect('..')

    def description(self):
        displayed_text = self.category.description

        form = Form(enctype='multipart/form-data')
        form.add(
            WysiwygTextWidget, 'description', title=_('Description'), value=displayed_text, cols=80, rows=10
        )
        form.add_submit('submit', _('Submit'))
        form.add_submit('cancel', _('Cancel'))

        if form.get_submit() == 'cancel':
            return redirect('.')

        if form.is_submitted() and not form.has_errors():
            self.category.description = form.get_widget('description').parse()
            self.category.store()
            return redirect('.')

        get_response().breadcrumb.append(('description', _('Description')))
        get_response().set_title(_('Edit Category Description'))
        r = TemplateIO(html=True)
        r += htmltext('<h2>%s</h2>') % _('Edit Category Description')
        r += form.render()
        return r.getvalue()

    def export(self):
        return misc.xml_response(
            self.category,
            filename=f'category-{self.category.slug}.wcs',
            content_type='text/xml',
        )


class CardDefCategoryPage(CategoryPage):
    category_class = CardDefCategory
    category_ui_class = CardDefCategoryUI
    object_class = CardDef
    usage_title = _('Card models in this category')
    empty_message = _('No card model associated to this category.')
    _q_exports = CategoryPage._q_exports + ['svg']

    def svg(self):
        response = get_response()
        response.set_content_type('image/svg+xml')
        show_orphans = get_request().form.get('show-orphans') == 'on'
        return get_cards_graph(category=self.category, show_orphans=show_orphans)


class WorkflowCategoryPage(CategoryPage):
    category_class = WorkflowCategory
    category_ui_class = WorkflowCategoryUI
    object_class = Workflow
    usage_title = _('Workflows in this category')
    empty_message = _('No workflow associated to this category.')


class BlockCategoryPage(CategoryPage):
    category_class = BlockCategory
    category_ui_class = BlockCategoryUI
    object_class = BlockDef
    usage_title = _('Blocks in this category')
    empty_message = _('No block associated to this category.')


class MailTemplateCategoryPage(CategoryPage):
    category_class = MailTemplateCategory
    category_ui_class = MailTemplateCategoryUI
    object_class = MailTemplate
    usage_title = _('Mail templates in this category')
    empty_message = _('No mail template associated to this category.')


class CommentTemplateCategoryPage(CategoryPage):
    category_class = CommentTemplateCategory
    category_ui_class = CommentTemplateCategoryUI
    object_class = CommentTemplate
    usage_title = _('Comment templates in this category')
    empty_message = _('No comment template associated to this category.')


class DataSourceCategoryPage(CategoryPage):
    category_class = DataSourceCategory
    category_ui_class = DataSourceCategoryUI
    object_class = NamedDataSource
    usage_title = _('Data sources in this category')
    empty_message = _('No data source associated to this category.')


class CategoriesDirectory(Directory):
    _q_exports = [
        '',
        'new',
        ('import', 'p_import'),
        'update_order',
        ('application', 'applications_dir'),
        ('by-slug', 'by_slug'),
    ]

    base_section = 'forms'
    category_class = Category
    category_ui_class = CategoryUI
    category_page_class = CategoryPage
    category_explanation = _('Categories are used to sort the different forms.')

    do_not_call_in_templates = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.applications_dir = ApplicationsDirectory(self.category_class)
        self.by_slug = utils.BySlugDirectory(self.category_class)

    def _q_index(self):
        from wcs.applications import Application

        get_response().add_javascript(['biglist.js', 'qommon.wysiwyg.js', 'popup.js'])
        get_response().set_title(_('Categories'))
        categories = self.category_class.select()
        self.category_class.sort_by_position(categories)
        Application.populate_objects(categories)
        return template.QommonTemplateResponse(
            templates=['wcs/backoffice/categories.html'],
            context={
                'view': self,
                'categories': categories,
                'applications': Application.select_for_object_type(self.category_class.xml_root_node),
                'elements_label': self.category_class.verbose_name_plural,
                'has_sidebar': True,
            },
            is_django_native=True,
        )

    def update_order(self):
        request = get_request()
        new_order = request.form['order'].strip(';').split(';')
        categories = self.category_class.select()
        categories_by_id = {}
        for cat in categories:
            categories_by_id[str(cat.id)] = cat
        new_order = [o for o in new_order if o in categories_by_id]
        for i, o in enumerate(new_order):
            categories_by_id[o].position = i + 1
            categories_by_id[o].store(store_snapshot=False)
        return 'ok'

    def new(self):
        get_response().breadcrumb.append(('new', _('New')))
        category_ui = self.category_ui_class(None)
        form = category_ui.get_form(new=True)
        if form.get_widget('cancel').parse():
            return redirect('.')

        if form.is_submitted() and not form.has_errors():
            try:
                category_ui.submit_form(form)
            except ValueError:
                pass
            else:
                return redirect('.')

        get_response().set_title(_('New Category'))
        r = TemplateIO(html=True)
        r += htmltext('<h2>%s</h2>') % _('New Category')
        r += form.render()
        return r.getvalue()

    def p_import(self):
        form = Form(enctype='multipart/form-data')
        import_title = _('Import category')

        form.add(FileWidget, 'file', title=_('File'), required=True)
        form.add_submit('submit', import_title)
        form.add_submit('cancel', _('Cancel'))

        if form.get_submit() == 'cancel':
            return redirect('.')

        if form.is_submitted() and not form.has_errors():
            try:
                return self.import_submit(form)
            except ValueError:
                pass

        get_response().breadcrumb.append(('import', _('Import')))
        get_response().set_title(import_title)
        r = TemplateIO(html=True)
        r += htmltext('<h2>%s</h2>') % import_title
        r += htmltext('<p>%s</p>') % _('You can install a new category by uploading a file.')
        r += form.render()
        return r.getvalue()

    def import_submit(self, form):
        fp = form.get_widget('file').parse().fp

        try:
            category = self.category_class.import_from_xml(fp, check_deprecated=True)
            get_session().add_message(_('This category has been successfully imported.'), level='info')
        except ValueError as e:
            form.set_error('file', _('Invalid File'))
            raise e

        try:
            # check slug unicity
            self.category_class.get_by_slug(category.slug)
        except KeyError:
            pass
        else:
            category.url_name = None  # a new one will be set in .store()
        category.store()
        return redirect('%s/' % category.id)

    def _q_lookup(self, component):
        return self.category_page_class(component)

    def _q_traverse(self, path):
        if not get_publisher().get_backoffice_root().is_global_accessible(self.base_section):
            raise AccessForbiddenError()
        get_response().breadcrumb.append(('categories/', _('Categories')))
        return super()._q_traverse(path)


class CardDefCategoriesDirectory(CategoriesDirectory):
    base_section = 'cards'
    category_class = CardDefCategory
    category_ui_class = CardDefCategoryUI
    category_page_class = CardDefCategoryPage
    category_explanation = _('Categories are used to sort the different card models.')


class WorkflowCategoriesDirectory(CategoriesDirectory):
    base_section = 'workflows'
    category_class = WorkflowCategory
    category_ui_class = WorkflowCategoryUI
    category_page_class = WorkflowCategoryPage
    category_explanation = _('Categories are used to sort the different workflows.')


class BlockCategoriesDirectory(CategoriesDirectory):
    base_section = 'forms'
    category_class = BlockCategory
    category_ui_class = BlockCategoryUI
    category_page_class = BlockCategoryPage
    category_explanation = _('Categories are used to sort the different blocks.')


class MailTemplateCategoriesDirectory(CategoriesDirectory):
    base_section = 'workflows'
    category_class = MailTemplateCategory
    category_ui_class = MailTemplateCategoryUI
    category_page_class = MailTemplateCategoryPage
    category_explanation = _('Categories are used to sort the different mail templates.')


class CommentTemplateCategoriesDirectory(CategoriesDirectory):
    base_section = 'workflows'
    category_class = CommentTemplateCategory
    category_ui_class = CommentTemplateCategoryUI
    category_page_class = CommentTemplateCategoryPage
    category_explanation = _('Categories are used to sort the different comment templates.')


class DataSourceCategoriesDirectory(CategoriesDirectory):
    base_section = 'workflows'
    category_class = DataSourceCategory
    category_ui_class = DataSourceCategoryUI
    category_page_class = DataSourceCategoryPage
    category_explanation = _('Categories are used to sort the different data sources.')
