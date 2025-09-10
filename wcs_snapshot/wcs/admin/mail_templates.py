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

from quixote import get_publisher, get_request, get_response, redirect
from quixote.directory import Directory
from quixote.html import TemplateIO, htmltext

from wcs.admin import utils
from wcs.admin.categories import MailTemplateCategoriesDirectory, get_categories
from wcs.admin.documentable import DocumentableMixin
from wcs.backoffice.applications import ApplicationsDirectory
from wcs.backoffice.snapshots import SnapshotsDirectory
from wcs.categories import MailTemplateCategory
from wcs.mail_templates import MailTemplate
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


class MailTemplatesDirectory(Directory):
    _q_exports = ['', 'new', 'categories', ('import', 'p_import'), ('application', 'applications_dir')]
    do_not_call_in_templates = True
    categories = MailTemplateCategoriesDirectory()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.applications_dir = ApplicationsDirectory(MailTemplate)

    def is_accessible(self, user=None):
        return MailTemplateCategory.has_global_access() or any(MailTemplateCategory.select_for_user())

    def _q_traverse(self, path):
        if not self.is_accessible():
            raise errors.AccessForbiddenError()
        get_response().breadcrumb.append(('mail-templates/', _('Mail Templates')))
        return super()._q_traverse(path)

    def _q_lookup(self, component):
        return MailTemplatePage(component)

    def _q_index(self):
        from wcs.applications import Application

        get_response().set_title(_('Mail Templates'))
        get_response().add_javascript(['popup.js'])
        mail_templates = MailTemplate.select(order_by='name')
        Application.populate_objects(mail_templates)
        context = {
            'view': self,
            'applications': Application.select_for_object_type(MailTemplate.xml_root_node),
            'elements_label': MailTemplate.verbose_name_plural,
            'has_sidebar': True,
            'is_global_accessible_categories': get_publisher()
            .get_backoffice_root()
            .is_accessible('categories'),
        }
        context.update(self.get_list_context(mail_templates))
        return template.QommonTemplateResponse(
            templates=['wcs/backoffice/mail-templates.html'],
            context=context,
            is_django_native=True,
        )

    def get_list_context(self, mail_templates):
        categories = MailTemplateCategory.select_for_user()
        MailTemplateCategory.sort_by_position(categories)
        if categories:
            if MailTemplateCategory.has_global_access():
                categories.append(MailTemplateCategory(_('Misc')))
            for category in categories:
                category.mail_templates = [
                    x for x in mail_templates if str(x.category_id) == str(category.id)
                ]
        return {
            'mail_templates': mail_templates,
            'categories': categories,
        }

    def new(self):
        form = Form(enctype='multipart/form-data')
        form.add(StringWidget, 'name', title=_('Name'), required=True, size=50)
        category_options = get_categories(MailTemplateCategory)
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
            mail_template = MailTemplate(name=form.get_widget('name').parse())
            if form.get_widget('category_id'):
                mail_template.category_id = form.get_widget('category_id').parse()
            mail_template.store()
            return redirect('%s/edit' % mail_template.id)

        get_response().breadcrumb.append(('new', _('New Mail Template')))
        get_response().set_title(_('New Mail Template'))
        r = TemplateIO(html=True)
        r += htmltext('<h2>%s</h2>') % _('New Mail Template')
        r += form.render()
        return r.getvalue()

    def p_import(self):
        form = Form(enctype='multipart/form-data')
        import_title = _('Import Mail Template')

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
        r += htmltext('<p>%s</p>') % _('You can install a new mail template by uploading a file.')
        r += form.render()
        return r.getvalue()

    def import_submit(self, form):
        fp = form.get_widget('file').parse().fp

        error, reason = False, ''
        try:
            mail_template = MailTemplate.import_from_xml(fp, check_deprecated=True)
            get_session().add_message(_('This mail template has been successfully imported.'), level='info')
        except ValueError:
            error = True

        if not error and not MailTemplateCategory.has_global_access():
            management_roles = {x.id for x in getattr(mail_template.category, 'management_roles', None) or []}
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
        known_slugs = {x.slug: x.id for x in MailTemplate.select(ignore_migration=True, ignore_errors=True)}
        if mail_template.slug in known_slugs:
            mail_template.slug = None  # a new one will be set in .store()
        mail_template.store()
        return redirect('%s/' % mail_template.id)


class MailTemplatePage(Directory, DocumentableMixin):
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
            self.mail_template = instance or MailTemplate.get(component)
        except KeyError:
            raise errors.TraversalError()
        if not self.mail_template.category and not MailTemplateCategory.has_global_access():
            raise errors.AccessForbiddenError()
        if self.mail_template.category and not self.mail_template.category.is_managed_by_user():
            raise errors.AccessForbiddenError()
        get_response().breadcrumb.append((component + '/', self.mail_template.name))
        self.snapshots_dir = SnapshotsDirectory(self.mail_template)
        self.documented_object = self.mail_template
        self.documented_element = self.mail_template

    def _q_index(self):
        from wcs.applications import Application

        get_response().set_title(self.mail_template.name)
        if not self.mail_template.is_readonly():
            Application.load_for_object(self.mail_template)
        get_response().add_javascript(['popup.js'])
        return template.QommonTemplateResponse(
            templates=['wcs/backoffice/mail-template.html'],
            context={'view': self, 'mail_template': self.mail_template, 'has_sidebar': True},
            is_django_native=True,
        )

    def snapshot_info_block(self):
        return utils.snapshot_info_block(snapshot=self.mail_template.snapshot_object)

    def get_form(self):
        form = Form(enctype='multipart/form-data', use_tabs=True)
        form.add(StringWidget, 'name', title=_('Name'), required=True, size=30, value=self.mail_template.name)
        category_options = get_categories(MailTemplateCategory)
        if category_options:
            form.add(
                SingleSelectWidget,
                'category_id',
                title=_('Category'),
                options=category_options,
                value=self.mail_template.category_id,
            )
        form.add(
            StringWidget,
            'subject',
            title=_('Subject'),
            required=True,
            size=40,
            value=self.mail_template.subject,
            validation_function=ComputedExpressionWidget.validate_template,
        )
        form.add(
            TextWidget,
            'body',
            title=_('Body'),
            value=self.mail_template.body,
            cols=80,
            rows=15,
            require=True,
            validation_function=ComputedExpressionWidget.validate_template,
        )

        if self.mail_template.slug and not self.mail_template.is_in_use():
            form.add(
                SlugWidget,
                'slug',
                value=self.mail_template.slug,
                advanced=True,
            )

        form.add(
            WidgetList,
            'attachments',
            title=_('Attachments (templates)'),
            element_type=StringWidget,
            value=self.mail_template.attachments,
            add_element_label=_('Add attachment'),
            element_kwargs={'render_br': False, 'size': 50},
            advanced=True,
        )

        if not self.mail_template.is_readonly():
            form.add_submit('submit', _('Submit'))
        form.add_submit('cancel', _('Cancel'))
        return form

    def submit_form(self, form):
        name = form.get_widget('name').parse()
        slug_widget = form.get_widget('slug')
        if slug_widget:
            slug = form.get_widget('slug').parse()

        for mail_template in MailTemplate.select():
            if mail_template.id == self.mail_template.id:
                continue
            if slug_widget and slug == mail_template.slug:
                slug_widget.set_error(_('This value is already used.'))
        if form.has_errors():
            raise ValueError()

        self.mail_template.name = name
        if form.get_widget('category_id'):
            self.mail_template.category_id = form.get_widget('category_id').parse()
        self.mail_template.subject = form.get_widget('subject').parse()
        self.mail_template.body = form.get_widget('body').parse()
        self.mail_template.attachments = form.get_widget('attachments').parse()
        if slug_widget:
            self.mail_template.slug = slug
        self.mail_template.store()

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
        get_response().set_title(_('Edit Mail Template'))
        r = TemplateIO(html=True)
        r += htmltext('<h2>%s</h2>') % _('Edit Mail Template')
        r += form.render()
        r += get_publisher().substitutions.get_substitution_html_table()

        return r.getvalue()

    def delete(self):
        form = Form(enctype='multipart/form-data')
        if not self.mail_template.is_in_use():
            form.widgets.append(
                HtmlWidget('<p>%s</p>' % _('You are about to irrevocably delete this mail template.'))
            )
            form.add_submit('delete', _('Delete'))
        else:
            form.widgets.append(
                HtmlWidget('<p>%s</p>' % _('This mail template is still used, it cannot be deleted.'))
            )
        form.add_submit('cancel', _('Cancel'))
        if form.get_widget('cancel').parse():
            return redirect('.')
        if not form.is_submitted() or form.has_errors():
            get_response().breadcrumb.append(('delete', _('Delete')))
            get_response().set_title(_('Delete Mail Template'))
            r = TemplateIO(html=True)
            r += htmltext('<h2>%s %s</h2>') % (_('Deleting Mail Template:'), self.mail_template.name)
            r += form.render()
            return r.getvalue()

        get_publisher().snapshot_class.snap_deletion(self.mail_template)
        self.mail_template.remove_self()
        return redirect('..')

    def export(self):
        return misc.xml_response(
            self.mail_template,
            filename='mail-template-%s.wcs' % self.mail_template.slug,
            content_type='application/x-wcs-mail-template',
        )

    def duplicate(self):
        form = Form(enctype='multipart/form-data')
        name_widget = form.add(StringWidget, 'name', title=_('Name'), required=True, size=30)
        form.add_submit('submit', _('Submit'))
        form.add_submit('cancel', _('Cancel'))
        if form.get_widget('cancel').parse():
            return redirect('.')

        if not form.is_submitted():
            original_name = self.mail_template.name
            new_name = '%s %s' % (original_name, _('(copy)'))
            names = [x.name for x in MailTemplate.select()]
            no = 2
            while new_name in names:
                new_name = _('%(name)s (copy %(no)d)') % {'name': original_name, 'no': no}
                no += 1
            name_widget.set_value(new_name)

        if not form.is_submitted() or form.has_errors():
            get_response().set_title(_('Duplicate Mail Template'))
            r = TemplateIO(html=True)
            get_response().breadcrumb.append(('duplicate', _('Duplicate')))
            r += htmltext('<h2>%s</h2>') % _('Duplicate Mail Template')
            r += form.render()
            return r.getvalue()

        self.mail_template.id = None
        self.mail_template.slug = None
        self.mail_template.name = form.get_widget('name').parse()
        self.mail_template.store()
        return redirect('../%s/' % self.mail_template.id)

    def usage(self):
        get_request().disable_error_notifications = True
        get_request().ignore_session = True
        get_response().raw = True

        usage = {}

        for item in self.mail_template.get_places_of_use():
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
