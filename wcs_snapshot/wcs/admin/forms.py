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

import datetime
import difflib
import html
import io
import xml.etree.ElementTree as ET
from collections import defaultdict

from django.utils.html import strip_tags
from quixote import get_publisher, get_request, get_response, get_session, redirect
from quixote.directory import AccessControlled, Directory
from quixote.html import TemplateIO, htmlescape, htmltext

from wcs.backoffice.applications import ApplicationsDirectory
from wcs.backoffice.deprecations import DeprecationsDirectory
from wcs.backoffice.snapshots import SnapshotsDirectory
from wcs.carddef import CardDef
from wcs.categories import Category
from wcs.fields import PageField
from wcs.formdef import FormDef
from wcs.formdef_base import (
    DRAFTS_DEFAULT_LIFESPAN,
    DRAFTS_DEFAULT_MAX_PER_USER,
    FormdefImportError,
    FormdefImportRecoverableError,
)
from wcs.formdef_jobs import UpdateDigestAfterJob
from wcs.forms.common import TempfileDirectoryMixin
from wcs.forms.root import qrcode
from wcs.qommon import _, force_str, misc, pgettext_lazy, template
from wcs.qommon.afterjobs import AfterJob
from wcs.qommon.errors import AccessForbiddenError, TraversalError
from wcs.qommon.form import (
    CheckboxesWidget,
    CheckboxWidget,
    DateTimeWidget,
    FileWidget,
    Form,
    HtmlWidget,
    IntWidget,
    OptGroup,
    SingleSelectWidget,
    SlugWidget,
    StringWidget,
    UrlWidget,
    WcsExtraStringWidget,
    WidgetList,
    WysiwygTextWidget,
    get_rich_text_widget_class,
)
from wcs.roles import get_user_roles, logged_users_role
from wcs.sql_criterias import Contains, Equal, GreaterOrEqual, Null, StrictNotEqual
from wcs.workflows import Workflow

from . import utils
from .blocks import BlocksDirectory
from .categories import CategoriesDirectory, get_categories
from .data_sources import NamedDataSourcesDirectory
from .documentable import DocumentableMixin
from .fields import FieldDefPage, FieldsDirectory
from .logged_errors import LoggedErrorsDirectory


def is_global_accessible(section):
    return get_publisher().get_backoffice_root().is_global_accessible(section)


class FormDefUI:
    formdef_class = FormDef
    category_class = Category
    section = 'forms'

    def __init__(self, formdef):
        self.formdef = formdef

    def get_categories(self):
        return get_categories(self.category_class)

    @classmethod
    def get_workflows(cls, formdef_category=None):
        default_workflow = cls.formdef_class.get_default_workflow()
        t = sorted(
            (misc.simplify(x.name), x.category.name if x.category else '', str(x.id), x.name, str(x.id))
            for x in Workflow.select()
            if x.possible_status
        )
        workflows_by_category_names = defaultdict(list)
        for x in t:
            workflows_by_category_names[x[1]].append(x[2:])
        category_names = list(workflows_by_category_names.keys())
        if len(category_names) == 1 and category_names[0] == '':
            # no category found
            return [(None, default_workflow.name, '')] + [x[2:] for x in t]
        options = []
        options.append((None, default_workflow.name, ''))
        # first options, workflows with the same category name as updated formdef
        if formdef_category and formdef_category.name in workflows_by_category_names:
            options.append(OptGroup(formdef_category.name))
            options.extend(workflows_by_category_names[formdef_category.name])
        # then other categories
        for name in sorted(category_names):
            if not name:
                continue
            if formdef_category and formdef_category.name == name:
                continue
            options.append(OptGroup(name))
            options.extend(workflows_by_category_names[name])
        # and workflows without category
        options.append(OptGroup(_('Without category')))
        options.extend(workflows_by_category_names[''])
        return options

    def new_form_ui(self):
        form = Form(enctype='multipart/form-data')
        if self.formdef:
            formdef = self.formdef
        else:
            formdef = self.formdef_class()
        form.add(
            StringWidget, 'name', title=_('Name'), required=True, size=40, value=formdef.name, maxlength=250
        )
        categories = self.get_categories()
        if categories:
            form.add(
                SingleSelectWidget,
                'category_id',
                title=_('Category'),
                value=formdef.category_id,
                options=categories,
            )
        workflows = self.get_workflows()
        if len(workflows) > 1:
            form.add(
                SingleSelectWidget,
                'workflow_id',
                title=_('Workflow'),
                value=formdef.workflow_id,
                options=workflows,
                **{'data-autocomplete': 'true'},
            )
        if not formdef.is_readonly():
            form.add_submit('submit', _('Submit'))
        form.add_submit('cancel', _('Cancel'))
        return form

    def submit_form(self, form):
        if self.formdef:
            formdef = self.formdef
        else:
            formdef = self.formdef_class()

        name = form.get_widget('name').parse()
        formdefs_name = [
            x.name
            for x in self.formdef_class.select(ignore_errors=True, lightweight=True)
            if x.id != formdef.id
        ]
        if name in formdefs_name:
            form.get_widget('name').set_error(_('This name is already used.'))
            raise ValueError()

        for f in (
            'name',
            'confirmation',
            'category_id',
            'disabled',
            'enable_tracking_codes',
            'workflow_id',
            'disabled_redirection',
            'publication_date',
            'expiration_date',
        ):
            widget = form.get_widget(f)
            if widget:
                setattr(formdef, f, widget.parse())

        if not formdef.fields:
            formdef.fields = []

        formdef.store()

        return formdef


class FormFieldDefPage(FieldDefPage):
    section = 'forms'
    deletion_extra_warning_message = _(
        'Warning: this field data will be permanently deleted from existing forms.'
    )

    def get_deletion_extra_warning(self):
        if not self.objectdef.data_class().count([StrictNotEqual('status', 'draft')]):
            return None
        return {'level': 'warning', 'message': self.deletion_extra_warning_message}


class FormFieldsDirectory(FieldsDirectory):
    field_def_page_class = FormFieldDefPage
    field_var_prefix = 'form_var_'
    readonly_message = _('This form is readonly.')

    def index_bottom(self):
        if self.objectdef.is_readonly():
            return
        if hasattr(self.objectdef, 'disabled') and self.objectdef.disabled:
            r = TemplateIO(html=True)
            r += htmltext('<div class="warningnotice">')
            r += str(_('This form is currently disabled.'))
            if hasattr(self.objectdef, 'disabled_redirection') and self.objectdef.disabled_redirection:
                r += htmltext(' (<a href="%s">') % self.objectdef.disabled_redirection
                r += str(_('redirection'))
                r += htmltext('</a>)')
            r += htmltext(' <a href="../enable?back=fields">%s</a>') % _('Enable')
            r += htmltext('</div>')
            return r.getvalue()


class OptionsDirectory(Directory):
    category_class = Category
    category_empty_choice = _('Select a category for this form')
    section = 'forms'
    backoffice_submission_options_label = _('Backoffice submission')

    _q_exports = [
        'confirmation',
        'tracking_code',
        'online_status',
        'captcha',
        'description',
        'keywords',
        'category',
        'management',
        'appearance',
        'templates',
        ('backoffice-submission', 'backoffice_submission'),
    ]

    def __init__(self, formdef, formdefui):
        self.formdef = formdef
        self.changed = False
        self.formdefui = formdefui

    def confirmation(self):
        form = Form(enctype='multipart/form-data')
        form.add(
            CheckboxWidget,
            'confirmation',
            title=_('Include confirmation page'),
            value=self.formdef.confirmation,
        )
        return self.handle(form, _('Confirmation Page'))

    def tracking_code(self):
        form = Form(enctype='multipart/form-data')

        form.widgets.append(HtmlWidget(htmltext('<h3>%s</h3>') % _('Draft')))
        widget = form.add(
            WcsExtraStringWidget,
            'drafts_lifespan',
            title=_('Lifespan of drafts (in days)'),
            value=self.formdef.drafts_lifespan,
            hint=_('By default drafts are removed after %s days.') % DRAFTS_DEFAULT_LIFESPAN,
        )

        def check_lifespan(value):
            try:
                return bool(int(value) >= 2 and int(value) <= 100)
            except (ValueError, TypeError):
                return False

        widget.validation_function = check_lifespan
        widget.validation_function_error_message = _('Lifespan must be between 2 and 100 days.')

        widget = form.add(
            WcsExtraStringWidget,
            'drafts_max_per_user',
            title=_('Maximum number of drafts per user (between 2 and 100)'),
            value=self.formdef.drafts_max_per_user,
            hint=_('%s drafts per user by default') % DRAFTS_DEFAULT_MAX_PER_USER,
        )

        def check_max_per_user(value):
            try:
                return bool(int(value) >= 2 and int(value) <= 100)
            except (ValueError, TypeError):
                return False

        widget.validation_function = check_max_per_user
        widget.validation_function_error_message = _('Maximum must be between 2 and 100 drafts.')

        form.widgets.append(HtmlWidget(htmltext('<h3>%s</h3>') % _('Tracking Code')))
        form.add(
            CheckboxWidget,
            'enable_tracking_codes',
            title=_('Enable support for tracking codes'),
            value=self.formdef.enable_tracking_codes,
            attrs={'data-dynamic-display-parent': 'true'},
        )
        verify_fields = [(None, '---', None)]
        for field in self.formdef.fields:
            if field.key in ('string', 'date', 'email', 'computed'):
                verify_fields.append((field.id, field.label, field.id))
        form.add(
            WidgetList,
            'tracking_code_verify_fields',
            title=_('Fields to check after entering the tracking code'),
            element_type=SingleSelectWidget,
            value=self.formdef.tracking_code_verify_fields,
            add_element_label=_('Add verification Field'),
            element_kwargs={'render_br': False, 'options': verify_fields},
            hint=_('Only text, date, email and computed fields can be used.'),
            attrs={
                'data-dynamic-display-child-of': 'enable_tracking_codes',
                'data-dynamic-display-checked': 'true',
            },
        )

        return self.handle(form, _('Form Tracking'))

    def captcha(self):
        form = Form(enctype='multipart/form-data')
        form.add(
            CheckboxWidget,
            'has_captcha',
            title=_('Prepend a CAPTCHA page for anonymous users'),
            value=self.formdef.has_captcha,
        )
        return self.handle(form, _('CAPTCHA'))

    def management(self):
        form = Form(enctype='multipart/form-data')
        form.add(
            CheckboxesWidget,
            'management_sidebar_items',
            title=_('Sidebar elements'),
            options=[(x[0], x[1], x[0]) for x in self.formdef.get_management_sidebar_available_items()],
            value=self.formdef.get_management_sidebar_items(),
            inline=False,
        )
        form.add(
            CheckboxWidget,
            'skip_from_360_view',
            title=_('Skip from per user view'),
            value=self.formdef.skip_from_360_view,
        )
        form.add(
            IntWidget,
            'old_but_non_anonymised_warning',
            title=_('Warn if there are old forms that were not anonymised (number of days)'),
            value=self.formdef.old_but_non_anonymised_warning,
        )

        return self.handle(form, _('Management'))

    def backoffice_submission(self):
        form = Form(enctype='multipart/form-data')

        submission_user_association_options = [
            ('none', _('No user'), 'none'),
            ('any', _('Any user (optional)'), 'any'),
            ('any-required', _('Any user (required)'), 'any-required'),
            ('roles', _('User with appropriate role'), 'roles'),
        ]
        submission_user_association_options = [
            x
            for x in submission_user_association_options
            if x[0] in self.formdef.submission_user_association_available_options
        ]
        submission_user_association_widget = form.add(
            SingleSelectWidget,
            'submission_user_association',
            title=_('Submission user assocation'),
            value=self.formdef.submission_user_association,
            options=submission_user_association_options,
        )
        submission_sidebar_items_widget = form.add(
            CheckboxesWidget,
            'submission_sidebar_items',
            title=_('Sidebar elements'),
            options=[(x[0], x[1], x[0]) for x in self.formdef.get_submission_sidebar_available_items()],
            value=self.formdef.get_submission_sidebar_items(),
            inline=False,
        )

        if form.is_submitted() and not form.has_errors():
            submission_user_association = submission_user_association_widget.parse()
            submission_sidebar_items = submission_sidebar_items_widget.parse() or []
            if (
                submission_user_association in ('any-required', 'roles')
                and 'user' not in submission_sidebar_items
            ):
                submission_sidebar_items_widget.set_error(
                    _('As a user is required its selection must be kept in the sidebar.')
                )

        return self.handle(form, self.backoffice_submission_options_label)

    def online_status(self):
        form = Form(enctype='multipart/form-data')
        form.add(CheckboxWidget, 'disabled', title=_('Disable access to form'), value=self.formdef.disabled)
        form.add(
            StringWidget,
            'disabled_redirection',
            title=_('If disabled, redirect to this URL'),
            size=40,
            hint=_(
                'Redirection will only be performed if the form is disabled and a URL is given. '
                'Common variables are available with the {{variable}} syntax.'
            ),
            value=self.formdef.disabled_redirection,
        )
        form.add(
            DateTimeWidget,
            'publication_date',
            title=_('Publication Date'),
            value=self.formdef.publication_date,
        )
        form.add(
            DateTimeWidget, 'expiration_date', title=_('Expiration Date'), value=self.formdef.expiration_date
        )
        return self.handle(form, _('Online Status'))

    def description(self):
        form = Form(enctype='multipart/form-data')
        form.add(
            get_rich_text_widget_class(self.formdef.description, usage='formdef-description'),
            'description',
            title=_('Description'),
            value=self.formdef.description,
        )
        return self.handle(form, _('Description'))

    def keywords(self):
        form = Form(enctype='multipart/form-data')
        form.add(
            StringWidget,
            'keywords',
            title=_('Keywords'),
            value=self.formdef.keywords,
            size=50,
            hint=_('Keywords need to be separated with commas.'),
        )
        return self.handle(form, _('Keywords'))

    def category(self):
        categories = self.formdefui.get_categories()
        form = Form(enctype='multipart/form-data')
        if not categories:
            form.widgets.append(HtmlWidget('<p>%s</p>' % _('There are not yet any category.')))
            return self.handle(form, _('Category'), omit_submit=True)
        form.widgets.append(HtmlWidget('<p>%s</p>' % self.category_empty_choice))
        form.add(
            SingleSelectWidget,
            'category_id',
            title=_('Category'),
            value=self.formdef.category_id,
            options=list(categories),
        )
        return self.handle(form, _('Category'))

    def appearance(self):
        form = Form(enctype='multipart/form-data')
        form.add(
            StringWidget,
            'appearance_keywords',
            title=_('Appearance keywords'),
            value=self.formdef.appearance_keywords,
            size=50,
            hint=_(
                'Serie of keywords to alter form appearance using CSS or '
                'custom templates, separated by spaces.'
            ),
        )
        return self.handle(form, _('Appearance'))

    def get_templates_form(self):
        form = Form(enctype='multipart/form-data')
        form.add(
            StringWidget,
            'digest_template',
            title=_('Digest'),
            value=self.formdef.default_digest_template,
            size=50,
        )
        form.add(
            WysiwygTextWidget,
            'lateral_template',
            title=_('Lateral Block'),
            value=self.formdef.lateral_template,
        )
        form.add(
            WysiwygTextWidget,
            'submission_lateral_template',
            title=_('Submission Lateral Block'),
            value=self.formdef.submission_lateral_template,
        )
        return form

    def templates(self):
        form = self.get_templates_form()
        result = self.handle(form, _('Templates'))
        if self.changed and self.formdef.data_class().count():
            get_publisher().add_after_job(UpdateDigestAfterJob(formdefs=[self.formdef]))
            if isinstance(self.formdef, CardDef):
                get_session().add_message(
                    _('Existing cards will be updated in the background.'), level='info'
                )
            else:
                get_session().add_message(
                    _('Existing forms will be updated in the background.'), level='info'
                )
        return result

    def handle(self, form, title, omit_submit=False):
        if not self.formdef.is_readonly() and not omit_submit:
            form.add_submit('submit', _('Submit'))
        form.add_submit('cancel', _('Cancel'))
        if form.get_widget('cancel').parse():
            return redirect('..')

        if form.is_submitted() and not form.has_errors():
            attrs = [
                'confirmation',
                'disabled',
                'enable_tracking_codes',
                'tracking_code_verify_fields',
                'disabled_redirection',
                'publication_date',
                'expiration_date',
                'has_captcha',
                'description',
                'keywords',
                'category_id',
                'skip_from_360_view',
                'old_but_non_anonymised_warning',
                'appearance_keywords',
                'include_download_all_button',
                'digest_template',
                'lateral_template',
                'id_template',
                'submission_lateral_template',
                'drafts_lifespan',
                'drafts_max_per_user',
                'management_sidebar_items',
                'submission_sidebar_items',
                'submission_user_association',
                'history_pane_default_mode',
            ]
            for attr in attrs:
                widget = form.get_widget(attr)
                if widget:
                    if hasattr(self, 'clean_%s' % attr):
                        has_error = getattr(self, 'clean_%s' % attr)(form)
                        if has_error:
                            continue
                    new_value = widget.parse()
                    if attr == 'submission_user_association':
                        # keep user_support option in sync (only relevant for cards)
                        self.formdef.user_support = 'optional' if new_value != 'none' else None
                    if attr == 'management_sidebar_items':
                        new_value = set(new_value or [])
                        if new_value == self.formdef.get_default_management_sidebar_items():
                            new_value = {'__default__'}
                    if attr == 'submission_sidebar_items':
                        new_value = set(new_value or [])
                        if new_value == self.formdef.get_default_submission_sidebar_items():
                            new_value = {'__default__'}
                    if attr == 'digest_template':
                        if self.formdef.default_digest_template != new_value:
                            self.changed = True
                            if not self.formdef.digest_templates:
                                self.formdef.digest_templates = {}
                            self.formdef.digest_templates['default'] = new_value
                    elif attr == 'id_template':
                        if self.formdef.id_template != new_value:
                            self.changed = True
                            self.formdef.id_template = new_value
                    else:
                        if getattr(self.formdef, attr, None) != new_value:
                            setattr(self.formdef, attr, new_value)
            if not form.has_errors():
                self.formdef.store(comment=_('Changed "%s" parameters') % title)
                return redirect('..')

        get_response().set_title(self.formdef.name)
        r = TemplateIO(html=True)
        r += htmltext('<h2>%s</h2>') % title
        r += form.render()
        return r.getvalue()

    def clean_digest_template(self, form):
        if not isinstance(self.formdef, CardDef):
            return False

        widget = form.get_widget('digest_template')
        new_value = widget.parse()
        if new_value:
            return False

        if any(self.formdef.usage_in_formdefs()):
            widget.set_error(
                _('Can not empty digest template: this card model is used as data source in some forms.')
            )
            return True

        return False

    def _q_traverse(self, path):
        get_response().breadcrumb.append((path[0] + '/', self.formdef.name))
        return super()._q_traverse(path)


class WorkflowRoleDirectory(Directory):
    def __init__(self, formdef):
        self.formdef = formdef

    def _q_lookup(self, component):
        if component not in self.formdef.workflow.roles:
            raise TraversalError()

        if not self.formdef.workflow_roles:
            self.formdef.workflow_roles = {}
        role_id = self.formdef.workflow_roles.get(component)

        options = [(None, '---', None)]
        options.extend(get_user_roles())
        form = Form(enctype='multipart/form-data')
        form.add(SingleSelectWidget, 'role_id', value=role_id, options=options)
        if not self.formdef.is_readonly():
            form.add_submit('submit', _('Submit'))
        form.add_submit('cancel', _('Cancel'))
        if form.get_widget('cancel').parse():
            return redirect('.')

        if not form.is_submitted() or form.has_errors():
            get_response().breadcrumb.append(('role/%s' % component, _('Workflow Role')))
            get_response().set_title(title=self.formdef.name)
            r = TemplateIO(html=True)
            r += htmltext('<h2>%s</h2>') % _('Role')
            r += htmltext('<p>%s</p>') % self.formdef.workflow.roles.get(component)
            r += form.render()
            return r.getvalue()

        self.formdef.workflow_roles[component] = form.get_widget('role_id').parse()
        self.formdef.store(comment=_('Change in function "%s"') % self.formdef.workflow.roles.get(component))
        if self.formdef.data_class().exists():
            # instruct formdef to update its security rules
            get_publisher().add_after_job(FunctionChangeAfterJob(self.formdef))
        return redirect('..')


class FormDefPage(Directory, TempfileDirectoryMixin, DocumentableMixin):
    do_not_call_in_templates = True
    _q_exports = [
        '',
        'fields',
        'delete',
        'duplicate',
        'export',
        'anonymise',
        'enable',
        'workflow',
        'role',
        ('workflow-variables', 'workflow_variables'),
        ('workflow-status-remapping', 'workflow_status_remapping'),
        'roles',
        'title',
        'options',
        'overwrite',
        'qrcode',
        'information',
        'inspect',
        'tempfile',
        'tests',
        ('public-url', 'public_url'),
        ('backoffice-submission-roles', 'backoffice_submission_roles'),
        ('logged-errors', 'logged_errors_dir'),
        ('history', 'snapshots_dir'),
        ('update-documentation', 'update_documentation'),
    ]

    formdef_class = FormDef
    formdef_export_prefix = 'form'
    formdef_ui_class = FormDefUI
    formdef_default_workflow = '_default'
    section = 'forms'
    options_directory_class = OptionsDirectory
    fields_directory_class = FormFieldsDirectory

    delete_message = _('You are about to irrevocably delete this form.')
    delete_title = _('Deleting Form:')
    duplicate_title = _('Duplicate Form')
    overwrite_message = _('You can replace this form by uploading a file or by pointing to a form URL.')
    overwrite_success_message = _(
        'The form has been successfully overwritten. '
        'Do note it kept its existing address and role and workflow parameters.'
    )
    backoffice_submission_role_label = _('Backoffice Submission Roles')
    backoffice_submission_role_description = (
        _('Select the roles that will be allowed to fill out forms of this kind in the backoffice.'),
    )
    formdef_template_name = 'wcs/backoffice/formdef.html'
    inspect_template_name = 'wcs/backoffice/formdef-inspect.html'

    def __init__(self, component, instance=None):
        from .tests import TestsDirectory

        try:
            self.formdef = instance or self.formdef_class.get(component)
        except KeyError:
            raise TraversalError()
        self.formdefui = self.formdef_ui_class(self.formdef)
        if component:
            get_response().breadcrumb.append((component + '/', self.formdef.name))
        self.fields = self.fields_directory_class(self.formdef)
        self.role = WorkflowRoleDirectory(self.formdef)
        self.options = self.options_directory_class(self.formdef, self.formdefui)
        self.tests = TestsDirectory(self.formdef)
        self.logged_errors_dir = LoggedErrorsDirectory(
            parent_dir=self, formdef_class=self.formdef_class, formdef_id=self.formdef.id
        )
        self.snapshots_dir = SnapshotsDirectory(self.formdef)
        self.documented_object = self.formdef
        self.documented_element = self.formdef

    def add_option_line(self, link, label, current_value, popup=True):
        return htmltext(
            '<li class="%(klass)s"><a rel="%(popup)s" href="%(link)s">'
            '<span class="label">%(label)s</span> '
            '<span class="value">%(current_value)s</span>'
            '</a></li>'
            % {
                'klass': link.replace('/', '-'),
                'popup': 'popup' if popup else '',
                'link': link,
                'label': label,
                'current_value': htmlescape(current_value),
            }
        )

    def _q_index(self):
        from wcs.applications import Application

        get_response().set_title(title=self.formdef.name)
        get_response().add_javascript(
            ['popup.js', 'widget_list.js', 'qommon.wysiwyg.js', 'qommon.fileupload.js', 'select2.js']
        )

        user = get_request().user
        if not self.formdef.is_readonly():
            Application.load_for_object(self.formdef)
        context = {
            'test_results': self.formdef.get_last_test_results(),
            'has_qrcode': bool(qrcode is not None),
            'view': self,
            'formdef': self.formdef,
            'form_preview': self.get_preview(),  # get media
            'has_sidebar': True,
            'include_management_link': bool(user.is_admin or self.formdef.is_of_concern_for_user(user)),
            'include_submission_link': self.formdef.has_creation_permission(user),
            'options': self.get_option_lines(),
            'has_captcha_option': get_publisher().has_site_option('formdef-captcha-option'),
            'has_appearance_keywords': get_publisher().has_site_option('formdef-appearance-keywords'),
        }
        return template.QommonTemplateResponse(
            templates=[self.formdef_template_name],
            context=context,
            is_django_native=True,
        )

    def snapshot_info_block(self):
        return utils.snapshot_info_block(snapshot=self.formdef.snapshot_object)

    def last_modification_block(self):
        return utils.last_modification_block(obj=self.formdef)

    def errors_block(self):
        return LoggedErrorsDirectory.errors_block(
            formdef_class=self.formdef_class, formdef_id=self.formdef.id
        )

    def get_option_lines(self):
        if self.formdef.description:
            description_line = misc.ellipsize(html.unescape(strip_tags(self.formdef.description)), 200)
        else:
            description_line = pgettext_lazy('description', 'None')
        options = {
            'description': self.add_option_line(
                'options/description',
                _('Description'),
                description_line,
            ),
            'keywords': self.add_option_line(
                'options/keywords',
                _('Keywords'),
                self.formdef.keywords and self.formdef.keywords or pgettext_lazy('keywords', 'None'),
            ),
            'category': self.add_option_line(
                'options/category',
                _('Category'),
                self.formdef.category_id
                and self.formdef.category
                and self.formdef.category.name
                or pgettext_lazy('category', 'None'),
            ),
            'user_roles': self.add_option_line(
                'roles', _('User Roles'), self.get_roles_label_and_auth_context()
            ),
            'backoffice_submission_roles': self.add_option_line(
                'backoffice-submission-roles',
                self.backoffice_submission_role_label,
                self._get_roles_label('backoffice_submission_roles'),
            ),
            'confirmation': self.add_option_line(
                'options/confirmation',
                _('Confirmation Page'),
                self.formdef.confirmation
                and pgettext_lazy('confirmation page', 'Enabled')
                or pgettext_lazy('confirmation page', 'Disabled'),
            ),
            'management': self.add_option_line(
                'options/management',
                _('Management'),
                (
                    _('Custom')
                    if (
                        self.formdef.skip_from_360_view
                        or self.formdef.management_sidebar_items
                        not in ({'__default__'}, self.formdef.get_default_management_sidebar_items())
                    )
                    else _('Default')
                ),
            ),
            'tracking_code': self.add_option_line(
                'options/tracking_code',
                _('Form Tracking'),
                self.formdef.enable_tracking_codes
                and pgettext_lazy('tracking code', 'Enabled')
                or pgettext_lazy('tracking code', 'Disabled'),
            ),
            'captcha': self.add_option_line(
                'options/captcha',
                _('CAPTCHA for anonymous users'),
                self.formdef.has_captcha
                and pgettext_lazy('captcha', 'Enabled')
                or pgettext_lazy('captcha', 'Disabled'),
            ),
            'appearance': self.add_option_line(
                'options/appearance',
                _('Appearance'),
                self.formdef.appearance_keywords
                and self.formdef.appearance_keywords
                or pgettext_lazy('appearance', 'Standard'),
            ),
            'backoffice_submission_options': self.add_option_line(
                'options/backoffice-submission',
                self.options_directory_class.backoffice_submission_options_label,
                (
                    _('Custom')
                    if (
                        self.formdef.submission_sidebar_items
                        not in ({'__default__'}, self.formdef.get_default_submission_sidebar_items())
                        or self.formdef.submission_user_association
                        != self.formdef.__class__.submission_user_association
                    )
                    else _('Default')
                ),
            ),
        }
        unknown_wf = self.formdef.workflow.id == Workflow.get_unknown_workflow().id
        if get_publisher().get_backoffice_root().is_accessible('workflows') and not unknown_wf:
            # custom option line to also include a link to the workflow itself.
            options['workflow'] = htmltext(
                '<li><a rel="popup" href="%(link)s">'
                '<span class="label">%(label)s</span> '
                '<span class="value offset">%(current_value)s</span>'
                '</a>'
                '<a class="extra-link" title="%(title)s" href="%(workflow_url)s">↗</a>'
                '</li>'
            ) % {
                'link': 'workflow',
                'label': _('Workflow'),
                'title': _('Open workflow page'),
                'workflow_url': self.formdef.workflow.get_admin_url(),
                'current_value': self.formdef.workflow.name or '-',
            }
        else:
            options['workflow'] = self.add_option_line(
                'workflow', _('Workflow'), self.formdef.workflow and self.formdef.workflow.name or '-'
            )

        options['workflow_options'] = ''
        if self.formdef.workflow and self.formdef.workflow.variables_formdef:
            options['workflow_options'] = self.add_option_line('workflow-variables', _('Options'), '')

        options['workflow_roles_list'] = []
        if self.formdef.workflow.roles:
            for wf_role_id, wf_role_label, role_label in self.get_workflow_roles_elements():
                options['workflow_roles_list'].append(
                    self.add_option_line('role/%s' % wf_role_id, htmlescape(wf_role_label), role_label)
                )

        if (
            self.formdef.default_digest_template
            or self.formdef.lateral_template
            or self.formdef.submission_lateral_template
            or self.formdef.id_template
        ):
            template_status = pgettext_lazy('template', 'Custom')
        else:
            template_status = pgettext_lazy('template', 'None')
        options['templates'] = self.add_option_line(
            'options/templates', _('Templates'), template_status, popup=False
        )

        online_status = pgettext_lazy('online status', 'Active')
        if self.formdef.disabled:
            # manually disabled
            online_status = pgettext_lazy('online status', 'Disabled')
            if self.formdef.disabled_redirection:
                online_status = _('Redirected')
        elif self.formdef.is_disabled():
            # disabled by date
            online_status = pgettext_lazy('online status', 'Inactive by date')
        options['online_status'] = self.add_option_line(
            'options/online_status', _('Online Status'), online_status
        )
        return options

    def get_workflow_roles_elements(self):
        if not self.formdef.workflow_roles:
            self.formdef.workflow_roles = {}
        workflow_roles = list((self.formdef.workflow.roles or {}).items())
        workflow_roles.sort(key=lambda x: '' if x[0] == '_receiver' else misc.simplify(x[1]))
        for wf_role_id, wf_role_label in workflow_roles:
            role_id = self.formdef.workflow_roles.get(wf_role_id)
            if role_id:
                try:
                    role = get_publisher().role_class.get(role_id)
                    role_label = role.name
                except KeyError:
                    # removed role ?
                    role_label = _('Unknown role (%s)') % role_id
            else:
                role_label = '-'
            yield (wf_role_id, wf_role_label, role_label)

    def _get_roles_label(self, attribute):
        if getattr(self.formdef, attribute):
            roles = []
            for x in getattr(self.formdef, attribute):
                if x == logged_users_role().id:
                    roles.append(logged_users_role().name)
                else:
                    try:
                        roles.append(get_publisher().role_class.get(x).name)
                    except KeyError:
                        # removed role ?
                        roles.append(_('Unknown role (%s)') % x)
            value = htmltext(', ').join([str(x) for x in roles])
        else:
            value = pgettext_lazy('roles', 'None')
        return value

    def get_roles_label_and_auth_context(self):
        value = self._get_roles_label('roles')
        if self.formdef.required_authentication_contexts:
            auth_contexts = get_publisher().get_supported_authentication_contexts()
            value += ' (%s)' % ', '.join(
                [
                    str(auth_contexts.get(x))
                    for x in self.formdef.required_authentication_contexts
                    if auth_contexts.get(x)
                ]
            )
        return value

    def public_url(self):
        get_response().set_title(title=self.formdef.name)
        get_response().breadcrumb.append(('public-url', _('Public URL')))
        r = TemplateIO(html=True)
        r += htmltext('<h2>%s</h2>' % _('Public URL'))
        r += htmltext('<div>')
        r += htmltext('<p>%s</p>') % _('The public URL of this form is:')
        url = self.formdef.get_url()
        r += htmltext('<a href="%s">%s</a>') % (url, url)
        r += htmltext('</div>')
        return r.getvalue()

    def qrcode(self):
        get_response().set_title(title=self.formdef.name)
        get_response().breadcrumb.append(('qrcode', _('QR Code')))
        r = TemplateIO(html=True)
        r += htmltext('<h2>%s</h2>' % _('QR Code'))
        r += htmltext('<div id="qrcode">')
        r += htmltext('<img width="410px" height="410px" src="%sqrcode" alt=""/>' % self.formdef.get_url())
        r += htmltext('<a href="%sqrcode?download">%s</a></p>') % (self.formdef.get_url(), _('Download'))
        r += htmltext('</div>')
        return r.getvalue()

    def _roles_selection(self, title, attribute, description=None, include_logged_users_role=True):
        form = Form(enctype='multipart/form-data')
        options = [(None, '---', None)]
        if include_logged_users_role:
            options.append((logged_users_role().id, logged_users_role().name, logged_users_role().id))
        options += get_user_roles()
        form.add(
            WidgetList,
            'roles',
            element_type=SingleSelectWidget,
            value=getattr(self.formdef, attribute),
            add_element_label=_('Add Role'),
            element_kwargs={'render_br': False, 'options': options},
        )
        if attribute == 'roles':
            # additional options
            form.add(
                CheckboxWidget,
                'only_allow_one',
                title=_('Only allow one form per user'),
                hint=_(
                    'This option concerns logged in users only. Form access must be restricted for this to be effective.'
                ),
                value=self.formdef.only_allow_one,
            )
            form.add(
                CheckboxWidget,
                'always_advertise',
                title=_('Advertise to unlogged users'),
                value=self.formdef.always_advertise,
            )
            auth_contexts = get_publisher().get_supported_authentication_contexts()
            if auth_contexts:
                form.add(
                    CheckboxesWidget,
                    'required_authentication_contexts',
                    title=_('Required authentication contexts'),
                    value=self.formdef.required_authentication_contexts,
                    options=list(auth_contexts.items()),
                )

        if not self.formdef.is_readonly():
            form.add_submit('submit', _('Submit'))
        form.add_submit('cancel', _('Cancel'))
        if form.get_widget('cancel').parse():
            return redirect('.')

        if not form.is_submitted() or form.has_errors():
            get_response().breadcrumb.append(('roles', title))
            get_response().set_title(title=self.formdef.name)
            r = TemplateIO(html=True)
            r += htmltext('<h2>%s</h2>') % title
            if description:
                r += htmltext('<p>%s</p>') % description
            r += form.render()
            return r.getvalue()

        roles = form.get_widget('roles').parse() or []
        setattr(self.formdef, attribute, [x for x in roles if x])
        for extra in ('required_authentication_contexts', 'only_allow_one', 'always_advertise'):
            extra_widget = form.get_widget(extra)
            if extra_widget:
                old_value = getattr(self.formdef, extra, None)
                setattr(self.formdef, extra, extra_widget.parse())
                if old_value != getattr(self.formdef, extra, None):
                    self.formdef.store(comment=_('Changed "%s" parameter') % extra_widget.get_title())
                    if extra == 'only_allow_one' and not roles:
                        get_session().add_message(
                            _(
                                'The single form option concerns logged in users only, '
                                'however this form is accessible anonymously. '
                                'Consider adding a sender role.'
                            ),
                            level='warning',
                        )

        self.formdef.store(comment=_('Change of %s') % title)
        return redirect('.')

    def roles(self):
        return self._roles_selection(
            title=_('User Roles'),
            attribute='roles',
            description=_('Select the roles that can access this form.'),
        )

    def backoffice_submission_roles(self):
        return self._roles_selection(
            title=self.backoffice_submission_role_label,
            attribute='backoffice_submission_roles',
            include_logged_users_role=False,
            description=self.backoffice_submission_role_description,
        )

    def title(self):
        form = Form(enctype='multipart/form-data')
        kwargs = {}
        if self.formdef.url_name == misc.simplify(self.formdef.name, force_letter_first=True):
            # if name and url name are in sync, keep them that way
            kwargs['data-slug-sync'] = 'url_name'
        form.add(
            StringWidget,
            'name',
            title=_('Name'),
            required=True,
            size=40,
            value=self.formdef.name,
            maxlength=250,
            **kwargs,
        )

        disabled_url_name = bool(self.formdef.data_class().count())
        kwargs = {}
        if disabled_url_name:
            kwargs['readonly'] = 'readonly'
        form.add(
            SlugWidget,
            'url_name',
            title=_('Identifier in URLs'),
            value=self.formdef.url_name,
            **kwargs,
        )
        if not self.formdef.is_readonly():
            form.add_submit('submit', _('Submit'))
        form.add_submit('cancel', _('Cancel'))
        if form.get_widget('cancel').parse():
            return redirect('.')

        if form.is_submitted() and not form.has_errors():
            new_name = form.get_widget('name').parse()
            new_url_name = form.get_widget('url_name').parse()
            formdefs = [
                x
                for x in self.formdef_class.select(ignore_errors=True, lightweight=True)
                if x.id != self.formdef.id
            ]
            if new_name in [x.name for x in formdefs]:
                form.get_widget('name').set_error(_('This name is already used.'))
            if new_url_name in [x.url_name for x in formdefs]:
                form.get_widget('url_name').set_error(_('This identifier is already used.'))
            if not form.has_errors():
                self.formdef.name = new_name
                self.formdef.url_name = new_url_name
                self.formdef.store(comment=_('Change of title / URL'))
                return redirect('.')

        if disabled_url_name:
            form.widgets.append(
                HtmlWidget(
                    '<p>%s<br>'
                    % _('The form identifier should not be modified as there is already some data.')
                )
            )
            form.widgets.append(
                HtmlWidget(
                    '<a href="" class="change-nevertheless">%s</a></p>'
                    % _('I understand the danger, make it editable nevertheless.')
                )
            )

        get_response().breadcrumb.append(('title', _('Title')))
        get_response().set_title(title=self.formdef.name)
        r = TemplateIO(html=True)
        r += htmltext('<h2>%s</h2>') % _('Title')
        r += form.render()
        return r.getvalue()

    def workflow(self):
        form = Form(enctype='multipart/form-data')
        workflows = self.formdef_ui_class.get_workflows(formdef_category=self.formdef.category)
        form.add(
            SingleSelectWidget,
            'workflow_id',
            value=self.formdef.workflow_id,
            options=workflows,
            **{'data-autocomplete': 'true'},
        )
        if not self.formdef.is_readonly():
            form.add_submit('submit', _('Submit'))
        form.add_submit('cancel', _('Cancel'))
        if form.get_widget('cancel').parse():
            return redirect('.')

        if not form.is_submitted() or form.has_errors():
            get_response().breadcrumb.append(('workflow', _('Workflow')))
            get_response().set_title(title=self.formdef.name)
            r = TemplateIO(html=True)
            r += htmltext('<h2>%s</h2>') % _('Workflow')
            r += htmltext('<p>%s</p>') % _('Select the workflow that will handle those forms.')
            r += form.render()
            return r.getvalue()

        from wcs.applications import Application

        Application.load_for_object(self.formdef)

        workflow_id = form.get_widget('workflow_id').parse() or self.formdef_default_workflow

        if self.formdef.applications:
            # always remap for formdef part of applications
            return redirect('workflow-status-remapping?new=%s' % workflow_id)
        if self.formdef.data_class().count([StrictNotEqual('status', 'draft')]):
            # there are existing formdata, status will have to be mapped
            return redirect('workflow-status-remapping?new=%s' % workflow_id)

        job = WorkflowChangeJob(
            formdef=self.formdef,
            new_workflow_id=workflow_id,
            status_mapping={},
            user_id=get_session().user,
        )
        job.store()
        get_publisher().add_after_job(job)
        return redirect(job.get_processing_url())

    def has_remapping_jobs(self):
        return bool(
            [
                x
                for x in AfterJob.select(
                    [Equal('class_name', 'WorkflowChangeJob'), Contains('status', ['registered', 'running'])]
                )
                if x.kwargs['formdef_class'] is self.formdef.__class__
                and x.kwargs['formdef_id'] == self.formdef.id
            ]
        )

    def workflow_status_remapping(self):
        if self.has_remapping_jobs():
            get_response().breadcrumb.append(('workflow-status-remapping', _('Workflow Status Remapping')))
            return template.error_page(_('A workflow change is already running.'))

        try:
            new_workflow = Workflow.get(get_request().form.get('new'))
        except KeyError:
            get_response().breadcrumb.append(('workflow-status-remapping', _('Workflow Status Remapping')))
            return template.error_page(_('Invalid target workflow.'))

        if get_request().get_method() == 'GET':
            get_request().form = None  # do not be considered submitted already
        new_workflow_status = [('', '')] + [(x.id, x.name) for x in new_workflow.possible_status]
        form = Form(enctype='multipart/form-data')
        for status in self.formdef.workflow.possible_status:
            default = status.id
            if default not in [x.id for x in new_workflow.possible_status]:
                default = ''
            form.add(
                SingleSelectWidget,
                'mapping-%s' % status.id,
                title=status.name,
                value=default,
                options=new_workflow_status,
                required=True,
            )

        if self.formdef.workflow.id == '_unknown':
            form.add(
                SingleSelectWidget,
                'mapping',
                title=_('Status'),
                options=new_workflow_status,
                required=True,
            )

        form.add_submit('submit', _('Submit'))
        form.add_submit('cancel', _('Cancel'))
        if form.get_widget('cancel').parse():
            return redirect('.')

        if not form.is_submitted() or form.has_errors():
            get_response().breadcrumb.append(('workflow-status-remapping', _('Workflow Change')))
            get_response().set_title(title=self.formdef.name)
            r = TemplateIO(html=True)
            r += htmltext('<h2 class="workflow-change--title">%s</h2>') % _('Workflow Change')
            if self.formdef.workflow.id == '_unknown':
                r += htmltext('<p>')
                r += str(_('The current workflow configuration is broken; remapping will apply to all data.'))
                r += htmltext('</p>')
            else:
                r += htmltext('<p class="workflow-change--subtitle">')
                r += htmltext(
                    _(
                        'From <a href="%(old_workflow_url)s">%(old_workflow_name)s</a>'
                        ' to <a href="%(new_workflow_url)s">%(new_workflow_name)s</a>.'
                    )
                ) % {
                    'old_workflow_url': self.formdef.workflow.get_admin_url(),
                    'old_workflow_name': self.formdef.workflow.name,
                    'new_workflow_url': new_workflow.get_admin_url(),
                    'new_workflow_name': new_workflow.name,
                }
                r += htmltext('</p>')

            old_workflow_backoffice_data = set()
            if (
                self.formdef.workflow.backoffice_fields_formdef
                and self.formdef.workflow.backoffice_fields_formdef.fields
            ):
                old_workflow_backoffice_data = {
                    f'{x.id}/{x.key}'
                    for x in self.formdef.workflow.backoffice_fields_formdef.fields
                    if not x.is_no_data_field
                }
            new_workflow_backoffice_data = set()
            if new_workflow.backoffice_fields_formdef and new_workflow.backoffice_fields_formdef.fields:
                new_workflow_backoffice_data = {
                    f'{x.id}/{x.key}'
                    for x in new_workflow.backoffice_fields_formdef.fields
                    if not x.is_no_data_field
                }
            if not old_workflow_backoffice_data.issubset(new_workflow_backoffice_data):
                r += htmltext('<div class="warningnotice"><p>%s</p>') % _(
                    'The workflow removes or changes backoffice fields, you should review the '
                    'changes carefully as some data will be lost.'
                )
                r += htmltext('<ul>')
                change_field_ids = [
                    x.split('/')[0]
                    for x in old_workflow_backoffice_data.difference(new_workflow_backoffice_data)
                ]
                for field in self.formdef.workflow.backoffice_fields_formdef.fields:
                    if field.id in change_field_ids:
                        r += htmltext('<li>%s - %s</li>') % (field.unhtmled_label, field.get_type_label())
                r += htmltext('</ul>')
                r += htmltext('</div>')

            r += htmltext('<div class="section">')
            r += htmltext('<h2>%s</h2>') % _('Status mapping')
            r += htmltext('<div>')
            r += form.render()
            r += htmltext('</div>')
            r += htmltext('</div>')
            return r.getvalue()

        status_mapping = {}
        for status in self.formdef.workflow.possible_status:
            status_mapping[status.id] = form.get_widget('mapping-%s' % status.id).parse()

        if self.formdef.workflow.id == '_unknown':
            status_mapping['_all'] = form.get_widget('mapping').parse()

        if self.has_remapping_jobs():
            # handle unlikely case of mapping job appearing concurrently
            return self.workflow_status_remapping()

        job = WorkflowChangeJob(
            formdef=self.formdef,
            new_workflow_id=new_workflow.id,
            status_mapping=status_mapping,
            user_id=get_session().user,
        )
        job.store()
        get_publisher().add_after_job(job)
        return redirect(job.get_processing_url())

    def get_preview(self):
        form = Form(action='#', use_tokens=False)
        form.attrs['data-backoffice-preview'] = 'true'
        form.attrs['data-js-features'] = 'true'
        on_page = 0
        get_request().backoffice_form_preview = True
        for field in self.formdef.fields:
            if getattr(field, 'add_to_form', None):
                try:
                    get_request().disable_error_notifications = True
                    field.add_to_form(form)
                except Exception as e:
                    form.widgets.append(
                        HtmlWidget(
                            htmltext('<div class="errornotice"><p>%s (%s)</p></div>')
                            % (_('Error previewing field.'), e)
                        )
                    )
                finally:
                    get_request().disable_error_notifications = False
            else:
                if field.key == 'page':
                    if on_page:
                        form.widgets.append(HtmlWidget('</fieldset>'))
                    form.widgets.append(HtmlWidget('<fieldset class="formpage">'))
                    on_page += 1
                    form.widgets.append(
                        HtmlWidget('<legend><span>%s — %s</span> ' % (_('Page #%s:') % on_page, field.label))
                    )
                    form.widgets.append(
                        HtmlWidget(
                            '<a class="pk-button" href="%s">%s</a>'
                            % ('fields/pages/%s/' % field.id, _('edit page fields'))
                        )
                    )
                    form.widgets.append(HtmlWidget('</legend>'))

        if on_page:
            form.widgets.append(HtmlWidget('</fieldset>'))

        r = TemplateIO(html=True)
        r += htmltext('<div class="form-preview">')
        r += form.render()
        r += htmltext('</div>')
        get_request().backoffice_form_preview = False
        return r.getvalue()

    def duplicate(self):
        form = Form(enctype='multipart/form-data')
        name_widget = form.add(StringWidget, 'name', title=_('Name'), required=True, size=30, maxlength=250)
        form.add_submit('submit', _('Submit'))
        form.add_submit('cancel', _('Cancel'))
        if form.get_widget('cancel').parse():
            return redirect('.')

        if not form.is_submitted():
            original_name = self.formdefui.formdef.name
            new_name = '%s %s' % (original_name, _('(copy)'))
            names = [x.name for x in self.formdef_class.select(lightweight=True)]
            no = 2
            while new_name in names:
                new_name = _('%(name)s (copy %(no)d)') % {'name': original_name, 'no': no}
                no += 1
            name_widget.set_value(new_name)

        if not form.is_submitted() or form.has_errors():
            get_response().set_title(title=self.duplicate_title)
            r = TemplateIO(html=True)
            get_response().breadcrumb.append(('duplicate', _('Duplicate')))
            r += htmltext('<h2>%s</h2>') % self.duplicate_title
            r += form.render()
            return r.getvalue()

        return self.duplicate_submit(form)

    def duplicate_submit(self, form):
        from wcs.testdef import TestDef

        testdefs = TestDef.select_for_objectdef(self.formdefui.formdef)

        self.formdefui.formdef.name = form.get_widget('name').parse()
        self.formdefui.formdef.id = None
        self.formdefui.formdef.url_name = None
        self.formdefui.formdef.table_name = None
        self.formdefui.formdef.disabled = True
        self.formdefui.formdef.store()

        for testdef in testdefs:
            testdef = TestDef.import_from_xml_tree(testdef.export_to_xml(), self.formdefui.formdef)
            testdef.store()

        return redirect('../%s/' % self.formdefui.formdef.id)

    def get_check_deletion_message(self):
        from wcs import sql

        criterias = [
            Equal('formdef_id', self.formdefui.formdef.id),
            StrictNotEqual('status', 'draft'),
            Equal('is_at_endpoint', False),
            Null('anonymised'),
        ]
        if sql.AnyFormData.count(criterias):
            return _('Deletion is not possible as there are open forms.')

    def delete(self):
        form = Form(enctype='multipart/form-data')
        check_count_message = self.get_check_deletion_message()
        if check_count_message:
            form.widgets.append(HtmlWidget('<p>%s</p>' % check_count_message))
        else:
            form.widgets.append(HtmlWidget('<p>%s</p>' % self.delete_message))
            criterias = [StrictNotEqual('status', 'draft'), Null('anonymised')]
            if self.formdef.data_class().count(criterias):
                form.widgets.append(
                    HtmlWidget(
                        htmltext('<div class="warningnotice"><p>%s</p></div>')
                        % _('Beware submitted forms will also be deleted.')
                    )
                )
            form.add_submit('delete', _('Delete'))
        form.add_submit('cancel', _('Cancel'))
        if form.get_widget('cancel').parse() or (form.is_submitted() and check_count_message):
            return redirect('.')
        if not form.is_submitted() or form.has_errors():
            get_response().breadcrumb.append(('delete', _('Delete')))
            get_response().set_title(title=self.delete_title)
            r = TemplateIO(html=True)
            r += htmltext('<h2>%s %s</h2>') % (self.delete_title, self.formdef.name)
            r += form.render()
            return r.getvalue()

        criterias = [
            Equal('formdef_type', self.formdef_class.xml_root_node),
            Equal('formdef_id', str(self.formdef.id)),
        ]
        for view in get_publisher().custom_view_class.select(criterias):
            view.remove_self()
        get_publisher().snapshot_class.snap_deletion(self.formdef)
        self.formdef.remove_self()
        return redirect('..')

    def overwrite(self):
        form = Form(enctype='multipart/form-data', use_tokens=False)
        form.add(FileWidget, 'file', title=_('File'), required=False)
        form.add(UrlWidget, 'url', title=_('Address'), required=False, size=50)
        form.add_hidden('new_formdef', required=False)
        form.add_hidden('force', required=False)
        form.add_submit('submit', _('Submit'))
        form.add_submit('cancel', _('Cancel'))
        if form.get_widget('cancel').parse():
            return redirect('.')

        if form.is_submitted() and not form.has_errors():
            try:
                return self.overwrite_submit(form)
            except ValueError:
                pass

        get_response().breadcrumb.append(('overwrite', _('Overwrite')))
        get_response().set_title(title=_('Overwrite'))
        r = TemplateIO(html=True)
        r += htmltext('<h2>%s</h2>') % _('Overwrite')
        r += htmltext('<p>%s</p>') % self.overwrite_message
        r += form.render()
        return r.getvalue()

    def overwrite_submit(self, form):
        if form.get_widget('file').parse():
            fp = form.get_widget('file').parse().fp
        elif form.get_widget('new_formdef').parse():
            fp = io.StringIO(form.get_widget('new_formdef').parse())
        elif form.get_widget('url').parse():
            url = form.get_widget('url').parse()
            try:
                fp = misc.urlopen(url)
            except misc.ConnectionError as e:
                form.set_error('url', _('Error loading form (%s).') % str(e))
                raise ValueError()
        else:
            form.set_error('file', _('You have to enter a file or a URL.'))
            raise ValueError()

        error, reason = False, None
        try:
            new_formdef = self.formdef_class.import_from_xml(fp, include_id=True, check_deprecated=True)
        except FormdefImportError as e:
            error = True
            reason = _(e.msg) % e.msg_args
            if hasattr(e, 'render'):
                form.add_global_errors([e.render()])
            elif e.details:
                reason += ' [%s]' % e.details
        except ValueError:
            error = True

        if error:
            if reason:
                msg = _('Invalid File (%s)') % reason
            else:
                msg = _('Invalid File')
            if form.get_widget('url').parse():
                form.set_error('url', msg)
            else:
                form.set_error('file', msg)
            raise ValueError()

        # it's been through the summary page, or there is no data yet
        if (
            not self.formdef.data_class().count([StrictNotEqual('status', 'draft')])
            or form.get_widget('force').parse()
        ):
            # doing it!
            return self.overwrite_by_formdef(new_formdef)

        return self.overwrite_warning_summary(new_formdef)

    def overwrite_by_formdef(self, new_formdef):
        incompatible_field_ids = self.get_incompatible_field_ids(new_formdef)
        if incompatible_field_ids:
            # if there are incompatible field ids, remove them first
            self.formdef.fields = [x for x in self.formdef.fields if x.id not in incompatible_field_ids]
            self.formdef.store(comment=_('Overwritten (removal of incompatible fields)'))

        # keep current formdef id, url_name, and sql table name
        new_formdef.id = self.formdef.id
        new_formdef.url_name = self.formdef.url_name
        new_formdef.table_name = self.formdef.table_name
        # keep currently assigned category and workflow
        new_formdef.category_id = self.formdef.category_id
        new_formdef.workflow_id = self.formdef.workflow_id
        new_formdef.workflow_options = self.formdef.workflow_options
        # keep currently assigned roles
        new_formdef.workflow_roles = self.formdef.workflow_roles
        new_formdef.backoffice_submission_roles = self.formdef.backoffice_submission_roles
        new_formdef.roles = self.formdef.roles

        # remove existing shared views
        for view in get_publisher().custom_view_class.select():
            if view.match(user=None, formdef=self.formdef):
                view.remove_self()

        self.formdef = new_formdef
        self.formdef.store(comment=_('Overwritten'))
        self.formdef.finish_tests_xml_import()
        get_publisher().add_after_job(UpdateDigestAfterJob(formdefs=[self.formdef]))
        get_session().add_message(self.overwrite_success_message, level='info')
        return redirect('.')

    def get_incompatible_field_ids(self, new_formdef):
        incompatible_field_ids = []
        current_fields = {}
        for field in self.formdef.fields:
            current_fields[field.id] = field

        for field in new_formdef.fields:
            current_field = current_fields.get(field.id)
            if current_field and current_field.key != field.key:
                incompatible_field_ids.append(field.id)

        return incompatible_field_ids

    def overwrite_warning_summary(self, new_formdef):
        get_response().set_title(title=_('Overwrite'))
        get_response().breadcrumb.append(('overwrite', _('Overwrite')))
        r = TemplateIO(html=True)

        r += htmltext('<h2>%s - %s</h2>') % (_('Overwrite'), _('Summary of changes'))

        current_fields_list = [str(x.id) for x in self.formdef.fields]
        new_fields_list = [str(x.id) for x in new_formdef.fields]

        current_fields = {}
        new_fields = {}
        for field in self.formdef.fields:
            current_fields[field.id] = field
        for field in new_formdef.fields:
            new_fields[field.id] = field

        table = TemplateIO(html=True)
        table += htmltext('<table id="table-diff">')

        def ellipsize_html(field):
            return misc.ellipsize(field.unhtmled_label, 60)

        display_warning = False

        for diffinfo in difflib.ndiff(current_fields_list, new_fields_list):
            if diffinfo[0] == '?':
                # detail line, ignored
                continue
            field_id = diffinfo[2:].split()[0]
            current_field = current_fields.get(field_id)
            new_field = new_fields.get(field_id)

            current_label = (
                htmltext('%s - %s') % (ellipsize_html(current_field), current_field.get_type_label())
                if current_field
                else ''
            )
            new_label = (
                htmltext('%s - %s') % (ellipsize_html(new_field), new_field.get_type_label())
                if new_field
                else ''
            )

            if diffinfo[0] == ' ':
                # unchanged line
                if current_field and new_field and current_field.key != new_field.key:
                    # different datatypes
                    if current_field.is_no_data_field:
                        # but current field doesn't hold data, not a problem
                        table += htmltext('<tr class="added-field"><td class="indicator">+</td>')
                        current_label = ''
                    elif new_field.is_no_data_field:
                        # new field won't hold data, but old data will be removed
                        table += htmltext('<tr class="removed-field"><td class="indicator">-</td>')
                        new_label = ''
                        display_warning = True
                    else:
                        # and real incompatibility, data will need to be wiped out.
                        table += htmltext('<tr class="type-change"><td class="indicator">!</td>')
                        display_warning = True
                elif (
                    current_field
                    and new_field
                    and ET.tostring(current_field.export_to_xml()) != ET.tostring(new_field.export_to_xml())
                ):
                    # same type, but changes within field
                    table += htmltext('<tr class="modified-field"><td class="indicator">~</td>')
                else:
                    table += htmltext('<tr><td class="indicator"></td>')
            elif diffinfo[0] == '-':
                # removed field
                table += htmltext('<tr class="removed-field"><td class="indicator">-</td>')
                display_warning = True
            elif diffinfo[0] == '+':
                # added field
                table += htmltext('<tr class="added-field"><td class="indicator">+</td>')
            table += htmltext('<td>%s</td> <td>%s</td></tr>') % (current_label, new_label)
        table += htmltext('</table>')

        if display_warning:
            r += htmltext('<div class="errornotice"><p>%s</p></div>') % _(
                'The form removes or changes fields, you should review the '
                'changes carefully as some data will be lost.'
            )

        r += htmltext('<div class="section">')
        r += htmltext('<div id="form-diff">')
        r += table.getvalue()

        r += htmltext('<div id="legend">')
        r += htmltext('<table>')
        r += htmltext('<tr class="added-field"><td class="indicator">+</td><td>%s</td></tr>') % (
            _('Added field')
        )
        r += htmltext('</table>')
        r += htmltext('<table>')
        r += htmltext('<tr class="removed-field"><td class="indicator">-</td><td>%s</td></tr>') % (
            _('Removed field')
        )
        r += htmltext('</table>')
        r += htmltext('<table>')
        r += htmltext('<tr class="modified-field"><td class="indicator">~</td><td>%s</td></tr>') % (
            _('Modified field')
        )
        r += htmltext('<table>')
        r += htmltext('<tr class="type-change"><td class="indicator">!</td><td>%s</td></tr>') % (
            _('Incompatible field')
        )
        r += htmltext('</table>')
        r += htmltext('</div>')  # .legend

        get_request().method = 'GET'
        get_request().form = {}
        form = Form(enctype='multipart/form-data', use_tokens=False)
        if display_warning:
            form.add(CheckboxWidget, 'force', title=_('Overwrite despite data loss'))
        else:
            form.add_hidden('force', 'ok')
        form.add_hidden('new_formdef', force_str(ET.tostring(new_formdef.export_to_xml(include_id=True))))
        form.add_submit('submit', _('Submit'))
        form.add_submit('cancel', _('Cancel'))
        r += form.render()
        r += htmltext('</div>')  # #form-diff
        r += htmltext('</div>')  # .section

        return r.getvalue()

    def export(self):
        return misc.xml_response(
            self.formdef,
            filename='%s-%s.wcs' % (self.formdef_export_prefix, self.formdef.url_name),
            content_type='application/x-wcs-form',
            include_tests=True,
        )

    def enable(self):
        self.formdef.disabled = False
        self.formdef.store(comment=_('Enable'))
        if get_request().form.get('back') == 'fields':
            return redirect('fields/')
        return redirect('.')

    def workflow_variables(self):
        if not self.formdef.workflow.variables_formdef:
            raise TraversalError()
        get_response().set_title(title=_('Options'))

        form = Form(enctype='multipart/form-data')
        form.attrs['data-js-features'] = 'true'
        self.formdef.workflow.variables_formdef.add_fields_to_form(
            form, form_data=self.formdef.get_variable_options_for_form()
        )
        for field in self.formdef.workflow.variables_formdef.fields:
            if getattr(field, 'default_value', None) is None:
                continue
            form_widget = form.get_widget(f'f{field.id}')
            if form_widget:
                form_widget.hint = (form_widget.hint + ' ') if form_widget.hint else ''
                form_widget.hint += _('Default value: %s') % field.default_value
        form.add_submit('submit', _('Submit'))
        form.add_submit('cancel', _('Cancel'))

        if form.get_widget('cancel').parse():
            return redirect('.')

        if form.is_submitted() and not form.has_errors():
            form_data = self.formdef.workflow.variables_formdef.get_data(form)
            self.formdef.set_variable_options(form_data)
            self.formdef.store(comment=_('Change in workflow variables'))
            return redirect('.')

        r = TemplateIO(html=True)
        r += htmltext('<h2>%s</h2>') % _('Options')
        r += form.render()
        return r.getvalue()

    def inspect(self):
        get_response().set_title(self.formdef.name)
        get_response().breadcrumb.append(('inspect', _('Inspector')))
        return self.render_inspect()

    def render_inspect(self):
        context = {'formdef': self.formdef, 'view': self, 'has_sidebar': self.formdef.is_readonly()}
        if self.formdef.workflow.variables_formdef:
            context['workflow_options'] = {}
            variables_form_data = self.formdef.get_variable_options_for_form()
            for field in self.formdef.workflow.variables_formdef.fields:
                if not hasattr(field, 'get_view_value'):  # inhert
                    context['workflow_options'][field.label] = '__%s__' % field.key
                    continue
                context['workflow_options'][field.label] = htmltext('%s') % field.get_view_value(
                    variables_form_data.get(field.id)
                )
        page = None
        for field in self.formdef.fields:
            if field.key == 'page':
                page = field
            field.on_page = page
        context['workflow_roles'] = list(self.get_workflow_roles_elements())
        context['backoffice_submission_roles'] = self._get_roles_label('backoffice_submission_roles')
        if self.formdef.tracking_code_verify_fields:
            context['tracking_code_verify_fields_labels'] = ', '.join(
                [
                    x.label
                    for x in self.formdef.fields
                    if str(x.id) in self.formdef.tracking_code_verify_fields
                ]
            )
        if hasattr(self.formdef, '_custom_views'):
            # loaded from snapshot
            custom_views = self.formdef._custom_views
        else:
            custom_views = []
            for view in get_publisher().custom_view_class.select_shared_for_formdef(self.formdef):
                custom_views.append(view)
        for view in custom_views:
            view.digest_template = (self.formdef.digest_templates or {}).get(
                'custom-view:%s' % view.get_url_slug()
            )
            if view.visibility == 'role':
                role_id = view.role_id
                if role_id:
                    try:
                        role = get_publisher().role_class.get(role_id)
                        role_label = role.name
                    except KeyError:
                        # removed role ?
                        role_label = _('Unknown role (%s)') % role_id
                else:
                    role_label = '-'
                view.role = role_label

        context['custom_views'] = sorted(custom_views, key=lambda x: getattr(x, 'title'))
        context['is_carddef'] = isinstance(self.formdef, CardDef)

        if not hasattr(self.formdef, 'snapshot_object'):
            deprecations = DeprecationsDirectory()
            context['deprecations'] = deprecations.get_deprecations(
                f'{self.formdef.xml_root_node}:{self.formdef.id}'
            )
            context['deprecation_metadata'] = deprecations.metadata

            receipt_time_criteria = GreaterOrEqual(
                'receipt_time',
                datetime.datetime.now() - datetime.timedelta(days=self.formdef.get_drafts_lifespan()),
            )

            temp_drafts = defaultdict(int)
            for formdata in self.formdef.data_class().select_iterator(
                clause=[Equal('status', 'draft'), receipt_time_criteria], itersize=200
            ):
                page_id = formdata.page_id if formdata.page_id is not None else '_unknown'
                temp_drafts[page_id] += 1

            total_drafts = sum(temp_drafts.values()) if temp_drafts else 0
            drafts = {}
            special_page_index_mapping = {
                '_first_page': -1000,  # first
                '_unknown': 1000,  # last
                '_confirmation_page': 999,  # second to last
            }
            if total_drafts:
                for page_id, page_index in special_page_index_mapping.items():
                    try:
                        page_total = temp_drafts.pop(page_id)
                    except KeyError:
                        page_total = 0
                    drafts[page_id] = {'total': page_total, 'field': None, 'page_index': page_index}
                for page_id, page_total in temp_drafts.items():
                    for index, field in enumerate(self.formdef.iter_fields(with_backoffice_fields=False)):
                        if page_id == field.id and isinstance(field, PageField):
                            drafts[page_id] = {
                                'total': page_total,
                                'field': field,
                                'page_index': index,
                            }
                            break
                    else:
                        drafts['_unknown']['total'] += page_total

                for draft_data in drafts.values():
                    draft_data['percent'] = 100 * draft_data['total'] / total_drafts

                total_formdata = self.formdef.data_class().count([receipt_time_criteria])
                context['drafts'] = sorted(drafts.items(), key=lambda x: x[1]['page_index'])
                context['percent_submitted_formdata'] = 100 * (total_formdata - total_drafts) / total_formdata
                context['total_formdata'] = total_formdata

            context['total_drafts'] = total_drafts

        return template.QommonTemplateResponse(
            templates=[self.inspect_template_name],
            context=context,
            is_django_native=True,
        )

    def snapshot_info_inspect_block(self):
        return utils.snapshot_info_block(
            snapshot=self.formdef.snapshot_object, url_name='inspect', url_prefix='../'
        )


class NamedDataSourcesDirectoryInForms(NamedDataSourcesDirectory):
    pass


class FormsDirectory(AccessControlled, Directory):
    do_not_call_in_templates = True

    _q_exports = [
        '',
        'new',
        ('import', 'p_import'),
        'blocks',
        'categories',
        ('data-sources', 'data_sources'),
        ('application', 'applications_dir'),
        ('test-users', 'test_users'),
        ('by-slug', 'by_slug'),
    ]

    by_slug = utils.BySlugDirectory(klass=FormDef)
    category_class = Category
    categories = CategoriesDirectory()
    blocks = BlocksDirectory()
    data_sources = NamedDataSourcesDirectoryInForms()
    formdef_class = FormDef
    formdef_page_class = FormDefPage
    formdef_ui_class = FormDefUI

    section = 'forms'
    top_title = _('Forms')
    index_template_name = 'wcs/backoffice/forms.html'
    import_title = _('Import Form')
    import_submit_label = _('Import Form')
    import_paragraph = _('You can install a new form by uploading a file or by pointing to the form URL.')
    import_loading_error_message = _('Error loading form (%s).')
    import_success_message = _('This form has been successfully imported. Do note it is disabled by default.')
    import_error_message = _(
        'Imported form contained errors and has been automatically fixed, '
        'you should nevertheless check everything is ok. '
        'Do note it is disabled by default.'
    )
    import_slug_change = _(
        'The form identifier (%(slug)s) was already used by another form. '
        'A new one has been generated (%(newslug)s).'
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.applications_dir = ApplicationsDirectory(self.formdef_class)

    @property
    def test_users(self):
        from wcs.admin.tests import TestUsersDirectory

        return TestUsersDirectory()

    def _q_traverse(self, path):
        get_response().breadcrumb.append(('%s/' % self.section, self.top_title))
        get_response().set_backoffice_section(self.section)
        return super()._q_traverse(path)

    def is_accessible(self, user=Ellipsis, traversal=False):
        if is_global_accessible(self.section):
            return True

        # check for access to specific categories
        if user is Ellipsis:
            user = get_request().user
        user_roles = set(user.get_roles())
        for category in self.category_class.select():
            management_roles = {x.id for x in getattr(category, 'management_roles') or []}
            if management_roles and user_roles.intersection(management_roles):
                return True

        return False

    def _q_index(self):
        from wcs.applications import Application

        get_response().set_title(title=self.top_title)
        get_response().add_javascript(['widget_list.js', 'select2.js', 'popup.js'])

        context = {
            'view': self,
            'has_roles': bool(get_publisher().role_class.count()),
            'applications': Application.select_for_object_type(self.formdef_class.xml_root_node),
            'elements_label': self.formdef_class.verbose_name_plural,
            'has_sidebar': True,
        }
        formdefs = self.formdef_class.select(order_by='name', ignore_errors=True, lightweight=True)
        Application.populate_objects(formdefs)
        context.update(self.get_list_context(formdefs))
        context.update(self.get_extra_index_context_data())

        return template.QommonTemplateResponse(
            templates=[self.index_template_name], context=context, is_django_native=True
        )

    def get_list_context(self, formdefs):
        global_access = is_global_accessible(self.section)

        categories = self.category_class.select_for_user()
        self.category_class.sort_by_position(categories)
        categories.append(self.category_class(_('Misc')))

        has_form_with_category_set = False
        for category in categories:
            if not global_access and not category.id:
                continue
            l2 = [x for x in formdefs if str(x.category_id) == str(category.id)]
            l2 = [x for x in l2 if not x.disabled or (x.disabled and x.disabled_redirection)] + [
                x for x in l2 if x.disabled and not x.disabled_redirection
            ]
            category.objects = l2
            if category.objects and category.id:
                has_form_with_category_set = True

        if not has_form_with_category_set:
            # no form with a category set, do not display "Misc" title
            categories[-1].name = None

        return {
            'objects': formdefs,
            'categories': categories,
        }

    def get_extra_index_context_data(self):
        return {
            'is_global_accessible_forms': is_global_accessible('forms'),
            'is_global_accessible_categories': get_publisher()
            .get_backoffice_root()
            .is_accessible('categories'),
        }

    def new(self):
        get_response().breadcrumb.append(('new', _('New')))
        if not (get_publisher().role_class.exists()):
            return template.error_page(self.section, _('You first have to define roles.'))
        formdefui = self.formdef_ui_class(None)
        form = formdefui.new_form_ui()
        if form.get_widget('cancel').parse():
            return redirect('.')

        if form.is_submitted() and not form.has_errors():
            try:
                formdef = formdefui.submit_form(form)
                formdef.disabled = True
                formdef.store()
            except ValueError:
                pass
            else:
                return redirect(str(formdef.id) + '/')

        get_response().set_title(title=_('New Form'))
        r = TemplateIO(html=True)
        r += htmltext('<h2>%s</h2>') % _('New Form')
        r += form.render()
        return r.getvalue()

    def _q_lookup(self, component):
        directory = self.formdef_page_class(component)
        if not directory.formdef.has_admin_access(get_request().user):
            raise AccessForbiddenError()
        return directory

    def p_import(self):
        form = Form(enctype='multipart/form-data')

        form.add(FileWidget, 'file', title=_('File'), required=False)
        form.add(UrlWidget, 'url', title=_('Address'), required=False, size=50)
        form.add_submit('submit', self.import_submit_label)
        form.add_submit('cancel', _('Cancel'))

        if form.get_submit() == 'cancel':
            return redirect('.')

        if form.is_submitted() and not form.has_errors():
            try:
                return self.import_submit(form)
            except ValueError:
                pass

        get_response().breadcrumb.append(('import', _('Import')))
        get_response().set_title(title=self.import_title)
        r = TemplateIO(html=True)
        r += htmltext('<h2>%s</h2>') % self.import_title
        r += htmltext('<p>%s</p>') % self.import_paragraph
        r += form.render()
        return r.getvalue()

    def import_submit(self, form):
        self.imported_formdef = None
        url = None
        if form.get_widget('file').parse():
            fp = form.get_widget('file').parse().fp
        elif form.get_widget('url').parse():
            url = form.get_widget('url').parse()
            try:
                fp = misc.urlopen(url)
            except misc.ConnectionError as e:
                form.set_error('url', self.import_loading_error_message % str(e))
                raise ValueError()
        else:
            form.set_error('file', _('You have to enter a file or a URL.'))
            raise ValueError()

        error, reason = False, None
        try:
            try:
                formdef = self.formdef_class.import_from_xml(fp, check_deprecated=True)
                get_session().add_message(self.import_success_message, level='info')
            except FormdefImportRecoverableError:
                fp.seek(0)
                formdef = self.formdef_class.import_from_xml(fp, fix_on_error=True)
                get_session().add_message(self.import_error_message, level='info')
        except FormdefImportError as e:
            error = True
            reason = _(e.msg) % e.msg_args
            if hasattr(e, 'render'):
                form.add_global_errors([e.render()])
            elif e.details:
                reason += ' [%s]' % e.details
        except ValueError:
            error = True

        if not error:
            global_access = is_global_accessible(self.section)
            if not global_access:
                management_roles = {x.id for x in getattr(formdef.category, 'management_roles', None) or []}
                user_roles = set(get_request().user.get_roles())
                if not user_roles.intersection(management_roles):
                    error = True
                    reason = _('unauthorized category')

        if error:
            if reason:
                msg = _('Invalid File (%s)') % reason
            else:
                msg = _('Invalid File')
            if url:
                form.set_error('url', msg)
            else:
                form.set_error('file', msg)
            raise ValueError()

        if hasattr(formdef, '_import_orig_slug'):
            get_session().add_message(
                '%s %s'
                % (
                    get_session().message['message'],
                    self.import_slug_change
                    % {'slug': formdef._import_orig_slug, 'newslug': formdef.url_name},
                ),
                level='warning',
            )

        self.imported_formdef = formdef
        formdef.disabled = True
        if url:
            formdef.import_source_url = url
        formdef.store()
        formdef.finish_tests_xml_import()
        return redirect('%s/' % formdef.id)


class WorkflowChangeJob(AfterJob):
    def __init__(self, formdef, new_workflow_id, status_mapping, user_id):
        super().__init__(
            label=_('Updating data for new workflow'),
            formdef_class=formdef.__class__,
            formdef_id=formdef.id,
            new_workflow_id=new_workflow_id,
            status_mapping=status_mapping,
            user_id=user_id,
        )

    def execute(self):
        formdef = self.kwargs['formdef_class'].get(self.kwargs['formdef_id'])
        workflow = Workflow.get(self.kwargs['new_workflow_id'])
        formdef.change_workflow(workflow, self.kwargs['status_mapping'], user_id=self.kwargs.get('user_id'))

    def done_action_url(self):
        formdef = self.kwargs['formdef_class'].get(self.kwargs['formdef_id'])
        return formdef.get_admin_url()

    def done_action_label(self):
        return _('Back')


class FunctionChangeAfterJob(AfterJob):
    label = _('Reindexing data after function change')

    def __init__(self, formdef):
        super().__init__()
        self.formdef_class = formdef.__class__
        self.formdef_id = formdef.id

    def execute(self):
        formdef = self.formdef_class.get(self.formdef_id)
        formdef.data_class().rebuild_security()
