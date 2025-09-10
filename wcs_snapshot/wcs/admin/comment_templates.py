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

from quixote import get_publisher, get_request, get_response, redirect
from quixote.directory import Directory
from quixote.html import TemplateIO, htmltext

from wcs.admin import utils
from wcs.admin.categories import CommentTemplateCategoriesDirectory, get_categories
from wcs.admin.documentable import DocumentableMixin
from wcs.backoffice.applications import ApplicationsDirectory
from wcs.backoffice.snapshots import SnapshotsDirectory
from wcs.categories import CommentTemplateCategory
from wcs.comment_templates import CommentTemplate
from wcs.qommon import _, errors, misc, template
from wcs.qommon.form import (
    ComputedExpressionWidget,
    FileWidget,
    Form,
    HtmlWidget,
    SingleSelectWidget,
    SlugWidget,
    StringWidget,
    TextWidget,
    WidgetList,
    get_session,
)


class CommentTemplatesDirectory(Directory):
    _q_exports = ['', 'new', 'categories', ('import', 'p_import'), ('application', 'applications_dir')]
    do_not_call_in_templates = True
    categories = CommentTemplateCategoriesDirectory()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.applications_dir = ApplicationsDirectory(CommentTemplate)

    def is_accessible(self, user=None):
        return CommentTemplateCategory.has_global_access() or any(CommentTemplateCategory.select_for_user())

    def _q_traverse(self, path):
        if not self.is_accessible():
            raise errors.AccessForbiddenError()
        get_response().breadcrumb.append(('comment-templates/', _('Comment Templates')))
        return super()._q_traverse(path)

    def _q_lookup(self, component):
        return CommentTemplatePage(component)

    def _q_index(self):
        from wcs.applications import Application

        get_response().set_title(_('Comment Templates'))
        get_response().add_javascript(['popup.js'])
        comment_templates = CommentTemplate.select(order_by='name')
        Application.populate_objects(comment_templates)
        context = {
            'view': self,
            'applications': Application.select_for_object_type(CommentTemplate.xml_root_node),
            'elements_label': CommentTemplate.verbose_name_plural,
            'has_sidebar': True,
            'is_global_accessible_categories': get_publisher()
            .get_backoffice_root()
            .is_accessible('categories'),
        }
        context.update(self.get_list_context(comment_templates))
        return template.QommonTemplateResponse(
            templates=['wcs/backoffice/comment-templates.html'],
            context=context,
            is_django_native=True,
        )

    def get_list_context(self, comment_templates):
        categories = CommentTemplateCategory.select_for_user()
        CommentTemplateCategory.sort_by_position(categories)
        if categories:
            if CommentTemplateCategory.has_global_access():
                categories.append(CommentTemplateCategory(_('Misc')))
            for category in categories:
                category.comment_templates = [
                    x for x in comment_templates if str(x.category_id) == str(category.id)
                ]
        return {
            'comment_templates': comment_templates,
            'categories': categories,
        }

    def new(self):
        form = Form(enctype='multipart/form-data')
        form.add(StringWidget, 'name', title=_('Name'), required=True, size=50)
        category_options = get_categories(CommentTemplateCategory)
        if category_options:
            form.add(
                SingleSelectWidget,
                'category_id',
                title=_('Category'),
                options=category_options,
            )
        form.add_submit('submit', _('Add'))
        form.add_submit('cancel', _('Cancel'))
        if form.get_widget('cancel').parse():
            return redirect('.')

        if form.is_submitted() and not form.has_errors():
            comment_template = CommentTemplate(name=form.get_widget('name').parse())
            if form.get_widget('category_id'):
                comment_template.category_id = form.get_widget('category_id').parse()
            comment_template.store()
            return redirect('%s/edit' % comment_template.id)

        get_response().breadcrumb.append(('new', _('New Comment Template')))
        get_response().set_title(_('New Comment Template'))
        r = TemplateIO(html=True)
        r += htmltext('<h2>%s</h2>') % _('New Comment Template')
        r += form.render()
        return r.getvalue()

    def p_import(self):
        form = Form(enctype='multipart/form-data')
        import_title = _('Import Comment Template')

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
        r += htmltext('<p>%s</p>') % _('You can install a new comment template by uploading a file.')
        r += form.render()
        return r.getvalue()

    def import_submit(self, form):
        fp = form.get_widget('file').parse().fp

        error, reason = False, ''
        try:
            comment_template = CommentTemplate.import_from_xml(fp, check_deprecated=True)
            get_session().add_message(
                _('This comment template has been successfully imported.'), level='info'
            )
        except ValueError:
            error = True

        if not error and not CommentTemplateCategory.has_global_access():
            management_roles = {
                x.id for x in getattr(comment_template.category, 'management_roles', None) or []
            }
            user_roles = set(get_request().user.get_roles())
            if not user_roles.intersection(management_roles):
                error = True
                reason = _('unauthorized category')

        if error:
            if reason:
                msg = _('Invalid File (%s)') % reason
            else:
                msg = _('Invalid File')
            form.set_error('file', msg)
            raise ValueError()

        # check slug unicity
        known_slugs = {
            x.slug: x.id for x in CommentTemplate.select(ignore_migration=True, ignore_errors=True)
        }
        if comment_template.slug in known_slugs:
            comment_template.slug = None  # a new one will be set in .store()
        comment_template.store()
        return redirect('%s/' % comment_template.id)


class CommentTemplatePage(Directory, DocumentableMixin):
    _q_exports = [
        '',
        'edit',
        'delete',
        'duplicate',
        'export',
        'usage',
        ('history', 'snapshots_dir'),
        ('update-documentation', 'update_documentation'),
    ]
    do_not_call_in_templates = True

    def __init__(self, component, instance=None):
        try:
            self.comment_template = instance or CommentTemplate.get(component)
        except KeyError:
            raise errors.TraversalError()
        if not self.comment_template.category and not CommentTemplateCategory.has_global_access():
            raise errors.AccessForbiddenError()
        if self.comment_template.category and not self.comment_template.category.is_managed_by_user():
            raise errors.AccessForbiddenError()
        get_response().breadcrumb.append((component + '/', self.comment_template.name))
        self.snapshots_dir = SnapshotsDirectory(self.comment_template)
        self.documented_object = self.comment_template
        self.documented_element = self.comment_template

    def _q_index(self):
        from wcs.applications import Application

        get_response().set_title(self.comment_template.name)
        if not self.comment_template.is_readonly():
            Application.load_for_object(self.comment_template)
        get_response().add_javascript(['popup.js'])
        return template.QommonTemplateResponse(
            templates=['wcs/backoffice/comment-template.html'],
            context={'view': self, 'comment_template': self.comment_template, 'has_sidebar': True},
            is_django_native=True,
        )

    def snapshot_info_block(self):
        return utils.snapshot_info_block(snapshot=self.comment_template.snapshot_object)

    def get_form(self):
        form = Form(enctype='multipart/form-data', use_tabs=True)
        form.add(
            StringWidget, 'name', title=_('Name'), required=True, size=30, value=self.comment_template.name
        )
        category_options = get_categories(CommentTemplateCategory)
        if category_options:
            form.add(
                SingleSelectWidget,
                'category_id',
                title=_('Category'),
                options=category_options,
                value=self.comment_template.category_id,
            )

        form.add(
            TextWidget,
            'comment',
            title=_('Comment'),
            value=self.comment_template.comment,
            cols=80,
            rows=15,
            require=True,
            validation_function=ComputedExpressionWidget.validate_template,
        )

        if self.comment_template.slug and not self.comment_template.is_in_use():
            form.add(
                SlugWidget,
                'slug',
                value=self.comment_template.slug,
                advanced=True,
            )

        form.add(
            WidgetList,
            'attachments',
            title=_('Attachments (templates)'),
            element_type=StringWidget,
            value=self.comment_template.attachments,
            add_element_label=_('Add attachment'),
            element_kwargs={'render_br': False, 'size': 50},
            advanced=True,
        )

        if not self.comment_template.is_readonly():
            form.add_submit('submit', _('Submit'))
        form.add_submit('cancel', _('Cancel'))
        return form

    def submit_form(self, form):
        name = form.get_widget('name').parse()
        slug_widget = form.get_widget('slug')
        if slug_widget:
            slug = form.get_widget('slug').parse()

        for comment_template in CommentTemplate.select():
            if comment_template.id == self.comment_template.id:
                continue
            if slug_widget and slug == comment_template.slug:
                slug_widget.set_error(_('This value is already used.'))
        if form.has_errors():
            raise ValueError()

        self.comment_template.name = name
        if form.get_widget('category_id'):
            self.comment_template.category_id = form.get_widget('category_id').parse()
        self.comment_template.comment = form.get_widget('comment').parse()
        self.comment_template.attachments = form.get_widget('attachments').parse()
        if slug_widget:
            self.comment_template.slug = slug
        self.comment_template.store()

    def edit(self):
        form = self.get_form()
        if form.get_submit() == 'cancel':
            return redirect('.')

        if form.get_submit() == 'submit' and not form.has_errors():
            try:
                self.submit_form(form)
            except ValueError:
                pass
            else:
                return redirect('.')

        get_response().breadcrumb.append(('edit', _('Edit')))
        get_response().set_title(_('Edit Comment Template'))
        r = TemplateIO(html=True)
        r += htmltext('<h2>%s</h2>') % _('Edit Comment Template')
        r += form.render()
        r += get_publisher().substitutions.get_substitution_html_table()

        return r.getvalue()

    def delete(self):
        form = Form(enctype='multipart/form-data')
        if not self.comment_template.is_in_use():
            form.widgets.append(
                HtmlWidget('<p>%s</p>' % _('You are about to irrevocably delete this comment template.'))
            )
            form.add_submit('delete', _('Delete'))
        else:
            form.widgets.append(
                HtmlWidget('<p>%s</p>' % _('This comment template is still used, it cannot be deleted.'))
            )
        form.add_submit('cancel', _('Cancel'))
        if form.get_widget('cancel').parse():
            return redirect('.')
        if not form.is_submitted() or form.has_errors():
            get_response().breadcrumb.append(('delete', _('Delete')))
            get_response().set_title(_('Delete Comment Template'))
            r = TemplateIO(html=True)
            r += htmltext('<h2>%s %s</h2>') % (_('Deleting Comment Template:'), self.comment_template.name)
            r += form.render()
            return r.getvalue()

        get_publisher().snapshot_class.snap_deletion(self.comment_template)
        self.comment_template.remove_self()
        return redirect('..')

    def export(self):
        return misc.xml_response(
            self.comment_template,
            filename='comment-template-%s.wcs' % self.comment_template.slug,
            content_type='application/x-wcs-comment-template',
        )

    def duplicate(self):
        form = Form(enctype='multipart/form-data')
        name_widget = form.add(StringWidget, 'name', title=_('Name'), required=True, size=30)
        form.add_submit('submit', _('Submit'))
        form.add_submit('cancel', _('Cancel'))
        if form.get_widget('cancel').parse():
            return redirect('.')

        if not form.is_submitted():
            original_name = self.comment_template.name
            new_name = '%s %s' % (original_name, _('(copy)'))
            names = [x.name for x in CommentTemplate.select()]
            no = 2
            while new_name in names:
                new_name = _('%(name)s (copy %(no)d)') % {'name': original_name, 'no': no}
                no += 1
            name_widget.set_value(new_name)

        if not form.is_submitted() or form.has_errors():
            get_response().set_title(_('Duplicate Comment Template'))
            r = TemplateIO(html=True)
            get_response().breadcrumb.append(('duplicate', _('Duplicate')))
            r += htmltext('<h2>%s</h2>') % _('Duplicate Comment Template')
            r += form.render()
            return r.getvalue()

        self.comment_template.id = None
        self.comment_template.slug = None
        self.comment_template.name = form.get_widget('name').parse()
        self.comment_template.store()
        return redirect('../%s/' % self.comment_template.id)

    def usage(self):
        get_request().disable_error_notifications = True
        get_request().ignore_session = True
        get_response().raw = True

        usage = {}

        for item in self.comment_template.get_places_of_use():
            usage[item.get_admin_url()] = (
                f'{item.get_workflow().name} - {item.parent.name} - {item.render_as_line()}'
            )

        r = TemplateIO(html=True)
        if usage:
            for source_url, source_name in usage.items():
                r += htmltext(f'<li><a href="{source_url}">%s</a></li>\n') % source_name
        else:
            r += htmltext('<li class="list-item-no-usage"><p>%s</p></li>') % _('No usage detected.')
        return r.getvalue()
