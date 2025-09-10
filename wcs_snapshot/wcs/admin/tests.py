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

import collections
import copy
import json
import uuid
import xml.etree.ElementTree as ET

from django.template.loader import render_to_string
from django.utils.timezone import now
from psycopg2.errors import UndefinedColumn  # pylint: disable=no-name-in-module
from quixote import get_publisher, get_request, get_response, get_session, redirect
from quixote.directory import Directory
from quixote.html import TemplateIO, htmltext

from wcs.admin import utils
from wcs.backoffice.management import FormBackofficeEditPage, FormBackOfficeStatusPage
from wcs.backoffice.pagination import pagination_links
from wcs.backoffice.snapshots import SnapshotDirectory, SnapshotsDirectory
from wcs.carddef import CardDef
from wcs.formdef import FormDef
from wcs.forms.common import FormStatusPage
from wcs.qommon import _, misc, template
from wcs.qommon.afterjobs import AfterJob
from wcs.qommon.errors import TraversalError
from wcs.qommon.form import (
    CheckboxWidget,
    ComputedExpressionWidget,
    DateTimeWidget,
    FileWidget,
    Form,
    JsonpSingleSelectWidget,
    OptGroup,
    RadiobuttonsWidget,
    SingleSelectWidget,
    StringWidget,
    TextWidget,
    WidgetDict,
    WidgetList,
)
from wcs.qommon.storage import Contains
from wcs.sql_criterias import Equal, NotEqual, NotNull, Null, StrictNotEqual
from wcs.testdef import TestDef, TestError, TestResult, TestResults, WebserviceResponse
from wcs.workflow_tests import WorkflowTestError


class TestEditPage(FormBackofficeEditPage):
    filling_templates = ['wcs/backoffice/testdata_filling.html']
    edit_mode_submit_label = _('Save data')
    edit_mode_cancel_url = '..'

    def __init__(self, objectdef, testdef, filled, **kwargs):
        self.formdef_class = objectdef.__class__
        super().__init__(objectdef.url_name, update_breadcrumbs=False, **kwargs)
        self.testdef = testdef
        self.edited_data = filled
        self.edited_data.data['edited_testdef_id'] = self.testdef.id

        get_request().is_in_backoffice_forced_value = self.testdef.is_in_backoffice
        get_request().edited_test_id = self.testdef.id

        self._q_exports.append(('mark-as-failing', 'mark_as_failing'))
        self._q_exports.append(('submission-settings', 'submission_settings'))
        self._q_exports.append(('query-parameters', 'query_parameters'))
        self._q_exports.append(('submission-date', 'submission_date'))
        self._q_exports.append('dependencies')
        self._q_exports.append(('workflow-options', 'workflow_options'))
        self._q_exports.append(('reset-workflow-options', 'reset_workflow_options'))

    def _q_traverse(self, path):
        get_response().breadcrumb.append(('edit-data/', _('Edit data')))

        with self.testdef.use_test_objects(results=self.testdef.get_last_dependencies_results()):
            return super()._q_traverse(path)

    def _q_index(self, *args, **kwargs):
        if get_request().form.get('previous-page-id') is None:
            get_request().query_parameters_forced_value = self.testdef.query_parameters

        original_user = get_request().user
        try:
            if self.testdef.user:
                get_request()._user = self.testdef.user
                get_publisher().substitutions.feed(self.testdef.user)
            with (
                self.testdef.use_test_objects(results=self.get_test_results()),
                self.testdef.freeze_submission_datetime() as freezer,
            ):
                self.freezer = freezer
                return super()._q_index(*args, **kwargs)
        finally:
            get_request()._user = original_user

    def get_test_results(self):
        return self.testdef.get_last_dependencies_results()

    def submitted_existing(self, form):
        # stop freezer to have correct timestamp in snapshot
        self.freezer.stop()
        return super().submitted_existing(form)

    def create_form(self, *args, **kwargs):
        form = super().create_form(*args, **kwargs)
        form.attrs['data-live-url'] = self.testdef.get_admin_url() + 'edit-data/live'
        return form

    def modify_filling_context(self, context, *args, **kwargs):
        super().modify_filling_context(context, *args, **kwargs)

        form = context['html_form']
        if form.get_submit() == 'submit':
            self.testdef.expected_error = None

        get_response().filter['sidebar'] = self.get_test_sidebar(form)

    def live(self):
        with self.testdef.freeze_submission_datetime():
            return super().live()

    def get_test_sidebar(self, form):
        dependencies = self.testdef.get_test_dependencies()
        results = TestResult.select([Contains('test_id', [x.id for x in dependencies])])
        test_ids_with_results = {x.test_id for x in results}
        for dependency in dependencies:
            dependency.has_test_results = bool(dependency.id in test_ids_with_results)
            dependency.indirect_dependencies = list(
                dependency.get_test_dependencies_recursively(include_self=False)
            )

        context = {
            'testdef': self.testdef,
            'dependencies': dependencies,
            'mark_as_failing_form': self.get_mark_as_failing_form(form),
            'user': TestDef.get_test_user(self.testdef.user_uuid),
            'submission_agent': TestDef.get_test_user(self.testdef.submission_agent_uuid),
        }
        return render_to_string('wcs/backoffice/test_edit_sidebar.html', context=context)

    def get_mark_as_failing_form(self, form):
        errors = form.global_error_messages or []

        if not errors and not form.has_errors():
            return

        for widget in form.widgets:
            if not hasattr(widget, 'field'):
                continue
            if widget.field.key in TestDef.ignored_field_types:
                continue
            if widget.is_hidden:
                continue

            widget = TestDef.get_error_widget(widget)
            if widget:
                errors.append(widget.error)

        if len(errors) != 1:
            return

        form = Form(enctype='multipart/form-data', action='mark-as-failing', use_tokens=False)
        form.add_hidden('error', errors[0])
        form.test_error = errors[0]

        magictoken = get_request().form.get('magictoken')
        form.add_hidden('magictoken', magictoken)

        form.add_submit('submit', _('Mark as failing'))
        return form

    def mark_as_failing(self):
        if not get_request().get_method() == 'POST':
            raise TraversalError()

        magictoken = get_request().form.get('magictoken')
        edited_data = self.get_transient_formdata(magictoken)

        testdef = TestDef.create_from_formdata(self.formdef, edited_data)
        self.testdef.data = testdef.data

        self.testdef.expected_error = get_request().form.get('error')
        self.testdef.store(comment=_('Mark test as failing'))
        return redirect('..')

    def submission_settings(self):
        form = Form(enctype='multipart/form-data')
        user_options = [('', '---', '')] + [
            (x.test_uuid, str(x), x.test_uuid)
            for x in get_publisher().test_user_class.select(order_by='name')
        ]
        form.add(
            SingleSelectWidget,
            'user',
            title=_('Associated User'),
            value=self.testdef.user_uuid or '',
            options=user_options,
            **{'data-autocomplete': 'true'},
        )
        form.add(
            CheckboxWidget,
            'backoffice_submission',
            value=self.testdef.is_in_backoffice,
            title=_('Backoffice submission'),
            attrs={'data-dynamic-display-parent': 'true'},
        )
        form.add(
            SingleSelectWidget,
            'submission_agent',
            title=_('Submission Agent'),
            value=self.testdef.submission_agent_uuid or '',
            options=user_options,
            attrs={
                'data-dynamic-display-child-of': 'backoffice_submission',
                'data-dynamic-display-checked': 'true',
            },
            **{'data-autocomplete': 'true'},
        )

        form.add_submit('submit', _('Submit'))
        form.add_submit('cancel', _('Cancel'))

        if form.get_widget('cancel').parse():
            return redirect('.')
        if not form.is_submitted() or form.has_errors():
            get_response().breadcrumb.append(('submission-settings', _('Submission settings')))
            r = TemplateIO(html=True)
            r += htmltext('<h2>%s</h2>') % (_('Submission settings'))
            r += form.render()
            return r.getvalue()

        self.testdef.user_uuid = form.get_widget('user').parse()
        self.testdef.is_in_backoffice = form.get_widget('backoffice_submission').parse()
        self.testdef.submission_agent_uuid = form.get_widget('submission_agent').parse()
        self.testdef.store(comment=_('Change submission settings'))
        return redirect('.')

    def query_parameters(self):
        form = Form(enctype='multipart/form-data')
        form.add(
            WidgetDict,
            'query_parameters',
            title=_('Add query parameters'),
            hint=_('These parameters will be used in request.GET variables.'),
            value=self.testdef.query_parameters,
            element_value_type=StringWidget,
        )

        form.add_submit('submit', _('Submit'))
        form.add_submit('cancel', _('Cancel'))

        if form.get_widget('cancel').parse():
            return redirect('.')
        if not form.is_submitted() or form.has_errors():
            get_response().breadcrumb.append(('query-parameters', _('Edit query parameters')))
            r = TemplateIO(html=True)
            r += htmltext('<h2>%s</h2>') % (_('Edit query parameters'))
            r += form.render()
            return r.getvalue()

        self.testdef.query_parameters = form.get_widget('query_parameters').parse() or {}
        self.testdef.store(comment=_('Change in query parameters'))
        return redirect('.')

    def submission_date(self):
        form = Form(enctype='multipart/form-data')
        form.add(
            DateTimeWidget,
            'frozen_submission_datetime',
            title=_('Submission date'),
            value=self.testdef.frozen_submission_datetime,
            use_datetime_object=True,
        )

        form.add_submit('submit', _('Submit'))
        form.add_submit('cancel', _('Cancel'))

        if form.get_widget('cancel').parse():
            return redirect('.')
        if not form.is_submitted() or form.has_errors():
            get_response().breadcrumb.append(('submission-date', _('Change submission date')))
            r = TemplateIO(html=True)
            r += htmltext('<h2>%s</h2>') % (_('Change submission date'))
            r += form.render()
            return r.getvalue()

        self.testdef.frozen_submission_datetime = form.get_widget('frozen_submission_datetime').parse()
        self.testdef.store(comment=_('Change in submission date'))
        return redirect('.')

    def get_testdef_options(self):
        testdefs = TestDef.select([NotEqual('uuid', self.testdef.uuid)], order_by='name')

        formdef_labels = {}
        for key, klass in [('formdefs', FormDef), ('carddefs', CardDef)]:
            formdef_labels[key] = {
                str(x.id): x.name
                for x in klass.select(
                    [Contains('id', [x.object_id for x in testdefs if x.object_type == key])]
                )
            }

        testdefs_by_formdef = collections.defaultdict(list)
        for x in testdefs:
            testdefs_by_formdef[formdef_labels[x.object_type][x.object_id]].append(x)

        options = []
        for label, testdefs in sorted(testdefs_by_formdef.items(), key=lambda x: x[0]):
            options.append(OptGroup(label))
            for testdef in testdefs:
                options.append((testdef.uuid, str(testdef), testdef.uuid))

        return options

    def dependencies(self):
        form = Form(enctype='multipart/form-data')
        form.add(
            WidgetList,
            'dependencies',
            element_type=SingleSelectWidget,
            value=self.testdef.dependencies,
            element_kwargs={
                'render_br': False,
                'options': [(None, '---', None)] + self.get_testdef_options(),
            },
        )

        form.add_submit('submit', _('Submit'))
        form.add_submit('cancel', _('Cancel'))

        if form.get_widget('cancel').parse():
            return redirect('.')
        if form.get_submit() != 'submit' or form.has_errors():
            get_response().breadcrumb.append(('dependencies', _('Edit dependencies')))
            r = TemplateIO(html=True)
            r += htmltext('<h2>%s</h2>') % (_('Edit dependencies'))
            r += form.render()
            return r.getvalue()

        self.testdef.dependencies = form.get_widget('dependencies').parse() or []
        self.testdef.store(comment=_('Change in dependencies'))
        return redirect('.')

    def workflow_options(self):
        if not self.formdef.workflow.variables_formdef:
            raise TraversalError()

        form = Form(enctype='multipart/form-data')

        form_data = TestDef.deserialize_form_data(
            self.formdef.workflow.variables_formdef, self.testdef.workflow_options
        )
        self.formdef.workflow.variables_formdef.add_fields_to_form(form, form_data=form_data)

        form.add_submit('submit', _('Submit'))
        form.add_submit('cancel', _('Cancel'))

        if form.get_widget('cancel').parse():
            return redirect('.')
        if form.get_submit() != 'submit' or form.has_errors():
            get_response().breadcrumb.append(('options', _('Form options')))
            r = TemplateIO(html=True)
            r += htmltext('<h2>%s</h2>') % (_('Override form options'))
            r += form.render()
            return r.getvalue()

        form_data = self.formdef.workflow.variables_formdef.get_data(form)
        self.testdef.workflow_options = TestDef.serialize_form_data(
            self.formdef.workflow.variables_formdef, form_data
        )
        self.testdef.store(comment=_('Change in overridden form options'))
        return redirect('.')

    def reset_workflow_options(self):
        self.testdef.workflow_options.clear()
        self.testdef.store(comment=_('Change in overridden form options'))
        return redirect('.')


class TestPage(FormBackOfficeStatusPage):
    _q_exports_orig = ['', 'download']
    _q_extra_exports = [
        'delete',
        'export',
        'edit',
        ('edit-data', 'edit_data'),
        'duplicate',
        ('workflow', 'workflow_tests'),
        ('webservice-responses', 'webservice_responses'),
        ('history', 'snapshots_dir'),
    ]

    def __init__(self, component, objectdef=None, instance=None):
        try:
            self.testdef = instance or TestDef.get(component)
        except KeyError:
            raise TraversalError()

        objectdef = objectdef or self.testdef.formdef

        filled = self.testdef.build_formdata(objectdef, include_fields=True)
        super().__init__(objectdef, filled)

        from wcs.admin.workflow_tests import WorkflowTestsDirectory

        self.workflow_tests = WorkflowTestsDirectory(self.testdef, self.formdef)
        self.webservice_responses = WebserviceResponseDirectory(self.testdef)
        self.snapshots_dir = SnapshotsDirectory(self.testdef)

    @property
    def edit_data(self):
        return TestEditPage(self.formdef, testdef=self.testdef, filled=self.filled)

    def _q_index(self):
        get_response().add_javascript(['select2.js'])
        return super()._q_index()

    def _q_traverse(self, path):
        get_response().breadcrumb.append((str(self.testdef.id) + '/', str(self.testdef)))
        return super(FormStatusPage, self)._q_traverse(path)

    def should_fold_summary(self, mine, request_user):
        return False

    def get_extra_context_bar(self, parent=None):
        if self.testdef.is_readonly():
            r = TemplateIO(html=True)
            r += htmltext('<div class="infonotice"><p>%s</p></div>') % _('This test is readonly.')
            r += utils.snapshot_info_block(self.testdef.snapshot_object)
            r += htmltext('<h3>%s</h3>') % _('Navigation')
            r += htmltext(
                '<li><a class="button button-paragraph" href="webservice-responses/">%s</a></li>'
            ) % _('Webservice responses')
            r += htmltext('</h3>')
            return r.getvalue()

        return render_to_string('wcs/backoffice/test_sidebar.html', context={})

    def status(self):
        r = TemplateIO(html=True)
        r += htmltext('<div id="appbar">')
        r += htmltext('<h2>%s</h2>') % self.testdef
        r += htmltext('<span class="actions">')
        if not self.testdef.is_readonly():
            r += htmltext('<a href="edit-data/">%s</a>') % _('Edit data')
        r += htmltext('<a href="workflow/">%s</a>') % _('Workflow tests')
        r += htmltext('</span>')
        r += htmltext('</div>')
        if self.testdef.expected_error:
            r += htmltext('<div class="infonotice"><p>%s</p></div>') % (
                _('This test is expected to fail on error "%s".') % self.testdef.expected_error
            )
        if self.testdef.data['fields']:
            r += self.receipt(always_include_user=True, mine=False)
        else:
            r += htmltext('<div class="infonotice"><p>%s</p></div>') % _('This test is empty.')
        return r.getvalue()

    def delete(self):
        form = Form(enctype='multipart/form-data')
        form.add_submit('delete', _('Delete'))
        form.add_submit('cancel', _('Cancel'))

        if form.get_widget('cancel').parse():
            return redirect('.')
        if not form.is_submitted() or form.has_errors():
            get_response().breadcrumb.append(('delete', _('Delete')))
            r = TemplateIO(html=True)
            r += htmltext('<h2>%s %s</h2>') % (_('Deleting Test:'), self.testdef)
            r += form.render()
            return r.getvalue()

        get_publisher().snapshot_class.snap_deletion(self.testdef)
        TestDef.remove_object(self.testdef.id)
        return redirect('..')

    def export(self):
        return misc.xml_response(
            self.testdef, filename='test-%s.wcs' % misc.simplify(self.testdef.name), include_id=False
        )

    def edit(self):
        form = Form(enctype='multipart/form-data')
        form.add(StringWidget, 'name', title=_('Name'), required=True, size=50, value=self.testdef.name)
        form.add_submit('submit', _('Submit'))
        form.add_submit('cancel', _('Cancel'))

        if form.get_widget('cancel').parse():
            return redirect('.')
        if not form.is_submitted() or form.has_errors():
            get_response().breadcrumb.append(('edit', _('Edit test')))
            r = TemplateIO(html=True)
            r += htmltext('<h2>%s</h2>') % (_('Edit test'))
            r += form.render()
            return r.getvalue()

        self.testdef.name = form.get_widget('name').parse()
        self.testdef.store(comment=_('Change in options'))
        return redirect('.')

    def duplicate(self):
        form = Form(enctype='multipart/form-data')
        name_widget = form.add(StringWidget, 'name', title=_('Name'), required=True, size=30)
        form.add_submit('submit', _('Submit'))
        form.add_submit('cancel', _('Cancel'))
        if form.get_widget('cancel').parse():
            return redirect('.')

        if not form.is_submitted():
            original_name = self.testdef.name
            new_name = '%s %s' % (original_name, _('(copy)'))
            names = [x.name for x in TestDef.select_for_objectdef(self.formdef)]
            no = 2
            while new_name in names:
                new_name = _('%(name)s (copy %(no)d)') % {'name': original_name, 'no': no}
                no += 1
            name_widget.set_value(new_name)

        if not form.is_submitted() or form.has_errors():
            get_response().set_title(_('Duplicate test'))
            r = TemplateIO(html=True)
            get_response().breadcrumb.append(('duplicate', _('Duplicate')))
            r += htmltext('<h2>%s</h2>') % _('Duplicate test')
            r += form.render()
            return r.getvalue()

        self.testdef.name = form.get_widget('name').parse()
        self.testdef.uuid = str(uuid.uuid4())
        self.testdef = TestDef.import_from_xml_tree(self.testdef.export_to_xml(), self.formdef)
        self.testdef.store(comment=_('Creation (from duplication)'))

        return redirect(self.testdef.get_admin_url())


class TestsDirectory(Directory):
    _q_exports = ['', 'new', ('import', 'p_import'), 'results', ('test-users', 'test_users')]
    section = 'tests'

    def __init__(self, objectdef):
        self.objectdef = objectdef
        self.results = TestResultsDirectory(objectdef)
        self.test_users = TestUsersDirectory()

    def _q_traverse(self, path):
        last_page_path, last_page_label = get_response().breadcrumb.pop()
        last_page_label = misc.ellipsize(last_page_label, 15, '…')
        get_response().breadcrumb.append((last_page_path, last_page_label))

        get_response().breadcrumb.append(('tests/', _('Tests')))
        return super()._q_traverse(path)

    def _q_lookup(self, component):
        return TestPage(component, self.objectdef)

    def _q_index(self):
        context = {
            'testdefs': TestDef.select_for_objectdef(self.objectdef, order_by='name'),
            'has_deprecated_fields': any(
                x.key in ('table', 'table-select', 'tablerows', 'ranked-items') for x in self.objectdef.fields
            ),
            'has_sidebar': True,
        }
        get_response().add_javascript(['popup.js'])
        get_response().set_title(_('Tests'))
        return template.QommonTemplateResponse(
            templates=['wcs/backoffice/tests.html'], context=context, is_django_native=True
        )

    def new(self):
        form = Form(enctype='multipart/form-data')
        form.add(StringWidget, 'name', title=_('Name'), required=True, size=50)

        formdata_options = [
            (
                x.id,
                '%s - %s - %s'
                % (x.id_display, x.user or _('Unknown User'), misc.localstrftime(x.receipt_time)),
            )
            for x in self.objectdef.data_class().select(
                [StrictNotEqual('status', 'draft'), Null('anonymised')], order_by='-receipt_time'
            )
        ]

        if formdata_options:
            creation_options = [
                ('empty', _('Fill data manually'), 'empty'),
                ('formdata', _('Import data from form'), 'formdata'),
                ('formdata-wf', _('Import data from form (and initialise workflow tests)'), 'formdata-wf'),
            ]
            form.add(
                RadiobuttonsWidget,
                'creation_mode',
                options=creation_options,
                value='empty',
                attrs={'data-dynamic-display-parent': 'true'},
            )
            form.add(
                SingleSelectWidget,
                'formdata',
                required=False,
                options=formdata_options,
                hint=_('Form is only used for initial data alimentation, no link is kept with created test.'),
                attrs={
                    'data-dynamic-display-child-of': 'creation_mode',
                    'data-dynamic-display-value-in': 'formdata|formdata-wf',
                },
                **{'data-autocomplete': 'true'},
            )

        form.add_submit('submit', _('Submit'))
        form.add_submit('cancel', _('Cancel'))

        if form.get_widget('cancel').parse():
            return redirect('.')

        if not form.is_submitted() or form.has_errors():
            get_response().breadcrumb.append(('new', _('New')))
            get_response().set_title(_('New test'))
            r = TemplateIO(html=True)
            r += htmltext('<h2>%s</h2>') % _('New test')
            r += form.render()
            return r.getvalue()

        creation_mode_widget = form.get_widget('creation_mode')
        if not creation_mode_widget or creation_mode_widget.parse() == 'empty':
            testdef = TestDef.create_from_formdata(self.objectdef, self.objectdef.data_class()())
            testdef.name = form.get_widget('name').parse()
            testdef.store(comment=_('Creation (empty)'))
            return redirect(testdef.get_admin_url() + 'edit-data/')

        formdata_id = form.get_widget('formdata').parse()
        formdata = self.objectdef.data_class().get(formdata_id)

        testdef = TestDef.create_from_formdata(
            self.objectdef,
            formdata,
            add_workflow_tests=bool(creation_mode_widget.parse() == 'formdata-wf'),
        )
        testdef.name = form.get_widget('name').parse()
        testdef.store(comment=_('Creation (from formdata)'))
        return redirect(testdef.get_admin_url())

    def p_import(self):
        form = Form(enctype='multipart/form-data')

        form.add(FileWidget, 'file', title=_('File'), required=True)
        form.add_submit('submit', _('Import Test'))
        form.add_submit('cancel', _('Cancel'))

        if form.get_submit() == 'cancel':
            return redirect('.')

        if form.is_submitted() and not form.has_errors():
            try:
                return self.import_submit(form)
            except ValueError:
                pass

        get_response().breadcrumb.append(('import', _('Import')))
        get_response().set_title(_('Import Test'))
        r = TemplateIO(html=True)
        r += htmltext('<h2>%s</h2>') % _('Import Test')
        r += form.render()
        return r.getvalue()

    def import_submit(self, form):
        fp = form.get_widget('file').parse().fp

        try:
            testdef = TestDef.import_from_xml(fp, self.objectdef)
        except ValueError as e:
            form.set_error('file', _('Invalid File'))
            raise e

        testdef.store(comment=_('Creation (from import)'))
        get_session().add_message(_('Test "%s" has been successfully imported.') % testdef.name, level='info')
        return redirect('.')


class CustomFormBackOfficeStatusPage(FormBackOfficeStatusPage):
    def __init__(self, objectdef, filled, testdef):
        self.testdef = testdef
        super().__init__(objectdef, filled)
        get_publisher().substitutions.get_context_variables()  # populate cache while we see test objects

    def test_tool_result(self, form):
        with self.testdef.use_test_objects():
            return super().test_tool_result(form)

    def inspect_tracing(self):
        with self.testdef.use_test_objects():
            return super().inspect_tracing()

    def inspect_variables(self):
        with self.testdef.use_test_objects():
            return super().inspect_variables()


class TestResultPage(Directory):
    _q_exports = ['', 'inspect', ('inspect-tool', 'inspect_tool')]

    def __init__(self, component, formdef):
        self.formdef = formdef

        try:
            self.result = TestResult.get(component)
        except KeyError:
            raise TraversalError()

        try:
            self.testdef = TestDef.get(self.result.test_id)
        except KeyError:
            self.testdef = None
        else:
            self.testdef.result = self.result

    def _q_traverse(self, path):
        get_response().breadcrumb.append(
            (str(self.result.id) + '/', _('Details of %(test_name)s') % {'test_name': self.result.test_name})
        )
        return super()._q_traverse(path)

    def _q_index(self):
        context = {
            'result': self.result,
            'testdef': self.testdef,
            'workflow_test_action': self.result.get_workflow_test_action(self.testdef),
            'error_field': self.result.get_error_field(self.formdef),
        }

        if self.testdef:
            self.add_webservice_response_objects()

        return template.QommonTemplateResponse(
            templates=['wcs/backoffice/test-result-detail.html'],
            context=context,
            is_django_native=True,
        )

    def add_webservice_response_objects(self):
        responses_by_id = {x.id: x for x in self.testdef.get_webservice_responses()}

        for request in self.result.sent_requests:
            if request['webservice_response_id']:
                request['webservice_response'] = responses_by_id.get(request['webservice_response_id'])

            for response_id in request['response_mismatch_reasons'].copy():
                response = responses_by_id.get(response_id)
                if not response:
                    continue

                request['response_mismatch_reasons'][response] = request['response_mismatch_reasons'].pop(
                    response_id
                )

    def inspect(self):
        with self.testdef.use_test_objects():
            formdata = self.formdef.data_class().get(self.result.formdata_id)
            return CustomFormBackOfficeStatusPage(self.formdef, formdata, self.testdef).inspect()

    def inspect_tool(self):
        with self.testdef.use_test_objects():
            formdata = self.formdef.data_class().get(self.result.formdata_id)
            return CustomFormBackOfficeStatusPage(self.formdef, formdata, self.testdef).inspect_tool()


class TestResultsPage(Directory):
    _q_exports = ['', ('fields-coverage', 'fields_coverage'), ('workflow-coverage', 'workflow_coverage')]

    def __init__(self, component, objectdef):
        try:
            self.test_results = TestResults.get(component)
        except KeyError:
            raise TraversalError()

        self.objectdef = objectdef

    def _q_traverse(self, path):
        get_response().breadcrumb.append(
            (str(self.test_results.id) + '/', _('Result #%s') % self.test_results.id)
        )
        return super()._q_traverse(path)

    def _q_lookup(self, component):
        return TestResultPage(component, self.objectdef)

    def _q_index(self):
        get_response().add_javascript(['popup.js'])

        testdefs = TestDef.select_for_objectdef(self.objectdef)
        testdefs_by_id = {x.id: x for x in testdefs}
        for result in self.test_results.results:
            if result.test_id in testdefs_by_id:
                result.testdef = testdefs_by_id[result.test_id]
                result.test_name = testdefs_by_id[result.test_id].name

        self.test_results.results.sort(key=lambda x: (bool(not x.error), x.test_name))

        return template.QommonTemplateResponse(
            templates=['wcs/backoffice/test-result.html'],
            context={'test_results': self.test_results},
            is_django_native=True,
        )

    def fields_coverage(self):
        get_response().breadcrumb.append(('fields-coverage', _('Fields coverage')))

        testdefs = TestDef.select_for_objectdef(self.objectdef)
        testdefs_by_id = {x.id: x for x in testdefs}

        fields = []
        for field in self.objectdef.fields:
            field_coverage = self.test_results.coverage['fields'].get(field.id)
            if not field_coverage:
                continue

            field.visible_in_tests = [testdefs_by_id.get(test_id) for test_id in field_coverage['visible']]
            field.hidden_in_tests = [testdefs_by_id.get(test_id) for test_id in field_coverage['hidden']]
            field.css_class = 'covered' if field.visible_in_tests else 'not-covered'

            fields.append(field)

        return template.QommonTemplateResponse(
            templates=['wcs/backoffice/test-results-fields-coverage.html'],
            context={'fields': fields},
            is_django_native=True,
        )

    def workflow_coverage(self):
        get_response().breadcrumb.append(('workflow-coverage', _('Workflow coverage')))

        testdefs = TestDef.select_for_objectdef(self.objectdef)
        testdefs_by_id = {x.id: x for x in testdefs}

        workflow_coverage = self.test_results.coverage['workflow']
        items = self.test_results.get_all_coverable_items(self.objectdef)
        for item in items:
            item.performed_in_tests = [
                testdefs_by_id.get(test_id)
                for test_id in workflow_coverage.get(item.parent.id, {}).get(item.id, [])
            ]
            item.css_class = 'covered' if item.performed_in_tests else 'not-covered'

        return template.QommonTemplateResponse(
            templates=['wcs/backoffice/test-results-workflow-coverage.html'],
            context={'items': items},
            is_django_native=True,
        )


class TestResultsDirectory(Directory):
    _q_exports = ['', 'run']
    section = 'test_results'

    def __init__(self, objectdef):
        self.objectdef = objectdef

    def _q_traverse(self, path):
        get_response().breadcrumb.append(('results/', _('Test results')))
        get_response().set_title('%s - %s' % (self.objectdef.name, _('Test results')))
        return super()._q_traverse(path)

    def _q_lookup(self, component):
        return TestResultsPage(component, self.objectdef)

    def _q_index(self):
        criterias = [
            Equal('object_type', self.objectdef.get_table_name()),
            Equal('object_id', str(self.objectdef.id)),
        ]

        offset = misc.get_int_or_400(get_request().form.get('offset', 0))
        limit = misc.get_int_or_400(get_request().form.get('limit', 25))
        total_count = TestResults.count(criterias)

        context = {
            'test_results': TestResults.select(
                [NotNull('success'), *criterias], offset=offset, limit=limit, order_by='-id'
            ),
            'has_testdefs': bool(TestDef.count(criterias)),
            'pagination_links': pagination_links(offset, limit, total_count, load_js=False),
        }
        return template.QommonTemplateResponse(
            templates=['wcs/backoffice/test-results.html'], context=context, is_django_native=True
        )

    def run(self):
        test_results = TestsAfterJob.run_tests(self.objectdef, _('Manual run.'))
        return redirect(test_results.get_admin_url())


class TestsAfterJob(AfterJob):
    def __init__(self, objectdef, reason, snapshot=None, triggered_by='', **kwargs):
        super().__init__(
            objectdef_class=objectdef.__class__,
            objectdef_id=objectdef.id,
            reason=str(reason or ''),
            snapshot_id=snapshot.id if snapshot else None,
            triggered_by=triggered_by,
            **kwargs,
        )

    @staticmethod
    def is_same_results(results, other_results):
        if not (results and other_results):
            return

        if results.coverage['percent_fields'] != other_results.coverage.get('percent_fields'):
            return

        if len(results.results) != len(other_results.results):
            return

        for result, other_result in zip(results.results, other_results.results):
            if result.error != other_result.error:
                return

        return True

    def execute(self):
        try:
            objectdef = self.kwargs['objectdef_class'].get(self.kwargs['objectdef_id'])
        except KeyError:
            return
        reason = self.kwargs['reason']

        try:
            results = self.run_tests(objectdef, reason, self.kwargs.get('triggered_by', ''))
        except UndefinedColumn:
            # ignore results when formdef has changed while tests were running
            return

        if not results:
            return

        last_test_results = objectdef.get_last_test_results(
            [NotEqual('id', results.id), NotNull('success')], order_by='-timestamp'
        )
        if self.is_same_results(results, last_test_results):
            TestResults.remove_object(results.id)
            return

        if self.kwargs['snapshot_id'] is not None:
            snapshot = get_publisher().snapshot_class.get(self.kwargs['snapshot_id'])
            snapshot.test_results_id = results.id
            snapshot.store()

    @staticmethod
    def run_tests(objectdef, reason, triggered_by=''):
        testdefs = TestDef.select_for_objectdef(objectdef, order_by='id')
        if not testdefs:
            return

        if triggered_by == 'workflow-change' and not any(x.workflow_tests.actions for x in testdefs):
            return

        test_results = TestResults()
        test_results.object_type = objectdef.get_table_name()
        test_results.object_id = objectdef.id
        test_results.timestamp = now()
        test_results.reason = str(reason)
        test_results.store()

        for test in testdefs:
            test.result.test_results_id = test_results.id
            test.coverage = test_results.coverage
            test.result.store()

            exception = None
            try:
                test.run(objectdef)
            except WorkflowTestError as e:
                test.result.error = _('Workflow error: %s') % e
                exception = e
            except TestError as e:
                test.result.error = str(e)
                exception = e

            if exception:
                test.result.workflow_test_action_uuid = exception.action_uuid
                test.result.error_details = exception.details
                test.result.error_field_id = exception.field_id
                test.result.dependency_uuid = exception.dependency_uuid

            if hasattr(test, 'formdata'):
                test.formdata.store()
                test.result.formdata_id = test.formdata.id

            test.result.store()

        test_results.success = not any(test.result.error for test in testdefs)
        test_results.set_coverage_percent(objectdef)
        test_results.store()

        return test_results


class WebserviceResponsePage(Directory):
    _q_exports = ['', 'delete', 'duplicate']

    def __init__(self, component, testdef):
        self.testdef = testdef
        try:
            self.webservice_response = [x for x in testdef.get_webservice_responses() if x.id == component][0]
        except IndexError:
            raise TraversalError()

    def _q_index(self):
        form = Form(enctype='multipart/form-data')
        form.add(
            StringWidget, 'name', size=50, title=_('Name'), required=True, value=self.webservice_response.name
        )

        form.add(
            ComputedExpressionWidget,
            'url',
            title=_('URL'),
            required=True,
            value=self.webservice_response.url,
            size=80,
        )

        def validate_json(value):
            try:
                json.loads(value)
            except ValueError as e:
                raise ValueError(_('Invalid JSON: %s') % e)

        form.add(
            TextWidget,
            'payload',
            title=_('Response payload (JSON)'),
            required=True,
            value=self.webservice_response.payload,
            validation_function=validate_json,
        )

        form.add(
            RadiobuttonsWidget,
            'status_code',
            title=_('Response status code'),
            required=True,
            options=[200, 204, 400, 401, 403, 404, 500, 502, 503],
            value=self.webservice_response.status_code,
            extra_css_class='widget-inline-radio',
        )

        form.add(
            WidgetDict,
            'qs_data',
            title=_('Restrict to query string data'),
            value=self.webservice_response.qs_data or {},
            element_value_type=StringWidget,
            allow_empty_values=True,
            value_for_empty_value='',
        )
        methods = collections.OrderedDict(
            [
                ('', _('Any')),
                ('GET', _('GET')),
                ('POST', _('POST (JSON)')),
                ('PUT', _('PUT (JSON)')),
                ('PATCH', _('PATCH (JSON)')),
                ('DELETE', _('DELETE (JSON)')),
            ]
        )
        form.add(
            RadiobuttonsWidget,
            'method',
            title=_('Restrict to method'),
            options=list(methods.items()),
            value=self.webservice_response.method,
            attrs={'data-dynamic-display-parent': 'true'},
            extra_css_class='widget-inline-radio',
        )
        form.add(
            WidgetDict,
            'post_data',
            title=_('Restrict to POST data'),
            value=self.webservice_response.post_data or {},
            element_value_type=ComputedExpressionWidget,
            allow_empty_values=True,
            value_for_empty_value='',
            attrs={
                'data-dynamic-display-child-of': 'method',
                'data-dynamic-display-value-in': '|'.join(
                    [
                        str(_(methods['POST'])),
                        str(_(methods['PUT'])),
                        str(_(methods['PATCH'])),
                        str(_(methods['DELETE'])),
                    ]
                ),
            },
        )

        if not self.testdef.is_readonly():
            form.add_submit('submit', _('Submit'))
        form.add_submit('cancel', _('Cancel'))
        form.add_media()

        if form.get_widget('cancel').parse():
            return redirect('.')

        if form.get_submit() != 'submit' or form.has_errors():
            get_response().breadcrumb.append(('edit', _('Edit webservice response')))
            r = TemplateIO(html=True)
            r += htmltext('<h2>%s</h2>') % (_('Edit webservice response'))
            r += form.render()
            return r.getvalue()

        self.webservice_response.name = form.get_widget('name').parse()
        self.webservice_response.payload = form.get_widget('payload').parse()
        self.webservice_response.url = form.get_widget('url').parse()
        self.webservice_response.status_code = form.get_widget('status_code').parse()
        self.webservice_response.qs_data = form.get_widget('qs_data').parse()
        self.webservice_response.method = form.get_widget('method').parse()
        self.webservice_response.post_data = form.get_widget('post_data').parse()
        self.webservice_response.store()
        self.testdef.store(comment=_('Change webservice response "%s"') % self.webservice_response.name)

        return redirect('..')

    def delete(self):
        form = Form(enctype='multipart/form-data')
        form.add_submit('delete', _('Delete'))
        form.add_submit('cancel', _('Cancel'))

        if form.get_widget('cancel').parse():
            return redirect('.')
        if not form.is_submitted() or form.has_errors():
            get_response().breadcrumb.append(('delete', _('Delete')))
            r = TemplateIO(html=True)
            r += htmltext('<h2>%s %s</h2>') % (_('Deleting:'), self.webservice_response)
            r += form.render()
            return r.getvalue()

        self.webservice_response.remove_self()
        return redirect('..')

    def duplicate(self):
        new_webservice_response = copy.deepcopy(self.webservice_response)
        new_webservice_response.id = None
        new_webservice_response.uuid = str(uuid.uuid4())
        new_webservice_response.name = '%s %s' % (new_webservice_response.name, _('(copy)'))
        new_webservice_response.store()
        self.testdef.store(
            comment=_('Duplication of webservice response "%s"') % self.webservice_response.name
        )
        return redirect('..')


class WebserviceResponseDirectory(Directory):
    _q_exports = ['', 'new', ('import', 'p_import')]

    def __init__(self, testdef):
        self.testdef = testdef

    def _q_traverse(self, path):
        get_response().breadcrumb.append(('webservice-responses/', _('Webservice responses')))
        return super()._q_traverse(path)

    def _q_lookup(self, component):
        return WebserviceResponsePage(component, self.testdef)

    def _q_index(self):
        context = {
            'webservice_responses': self.testdef.get_webservice_responses(),
            'has_sidebar': bool(not self.testdef.is_readonly()),
            'testdef': self.testdef,
        }
        get_response().add_javascript(['popup.js'])
        get_response().set_title(_('Webservice responses'))
        return template.QommonTemplateResponse(
            templates=['wcs/backoffice/test-webservice-responses.html'],
            context=context,
            is_django_native=True,
        )

    def new(self):
        form = Form(enctype='multipart/form-data')
        form.add(StringWidget, 'name', title=_('Name'), required=True, size=50)

        form.add_submit('submit', _('Submit'))
        form.add_submit('cancel', _('Cancel'))

        if form.get_widget('cancel').parse():
            return redirect('.')

        if not form.is_submitted() or form.has_errors():
            get_response().breadcrumb.append(('new', _('New')))
            get_response().set_title(_('New webservice response'))
            r = TemplateIO(html=True)
            r += htmltext('<h2>%s</h2>') % _('New webservice response')
            r += form.render()
            return r.getvalue()

        webservice_response = WebserviceResponse()
        webservice_response.testdef_id = self.testdef.id
        webservice_response.name = form.get_widget('name').parse()
        webservice_response.store()
        self.testdef.store(comment=_('New webservice response "%s"') % webservice_response.name)

        return redirect(self.testdef.get_admin_url() + 'webservice-responses/%s/' % webservice_response.id)

    def p_import(self):
        form = Form(enctype='multipart/form-data')

        testdef_options = [
            (x.id, x, x.id)
            for x in TestDef.select_for_objectdef(self.testdef.formdef)
            if x.id != self.testdef.id
        ]
        form.add(
            SingleSelectWidget,
            'testdef_id',
            required=True,
            options=[(None, '---', None)] + testdef_options,
            **{'data-autocomplete': 'true'},
        )

        form.add_submit('submit', _('Import'))
        form.add_submit('cancel', _('Cancel'))

        if form.get_submit() == 'cancel':
            return redirect('.')

        if not form.is_submitted() or form.has_errors():
            get_response().breadcrumb.append(('import', _('Import')))
            get_response().set_title(_('Import webservice responses'))
            r = TemplateIO(html=True)
            r += htmltext('<h2>%s</h2>') % _('Import webservice responses')
            r += form.render()
            return r.getvalue()

        testdef_id = form.get_widget('testdef_id').parse()
        testdef = TestDef.get(testdef_id)

        for response in testdef.get_webservice_responses():
            response.id = None
            response.testdef_id = self.testdef.id
            response.store()

        return redirect('.')


class TestUserSnapshotDirectory(SnapshotDirectory):
    allow_restore_as_new = False


class TestUserPage(Directory):
    _q_exports = ['', 'delete', 'export', ('history', 'snapshots_dir')]

    def __init__(self, component, instance=None):
        try:
            self.user = instance or get_publisher().test_user_class.get(component)
        except IndexError:
            raise TraversalError()

        self.snapshots_dir = SnapshotsDirectory(self.user)
        self.snapshots_dir.snapshot_directory_class = TestUserSnapshotDirectory

    def _q_traverse(self, path):
        get_response().breadcrumb.append((str(self.user.id) + '/', self.user.name))
        get_response().set_title(self.user.name)
        return super()._q_traverse(path)

    def _q_index(self):
        form = Form(enctype='multipart/form-data')

        formdef = get_publisher().user_class.get_formdef()
        form.add(
            StringWidget, 'name', title=_('Test user label'), required=True, size=30, value=self.user.name
        )
        roles = list(get_publisher().role_class.select(order_by='name'))
        form.add(
            WidgetList,
            'roles',
            title=_('Roles'),
            element_type=SingleSelectWidget,
            value=self.user.roles,
            add_element_label=_('Add Role'),
            element_kwargs={
                'render_br': False,
                'options': [(None, '---', None)]
                + [(x.id, x.name, x.id) for x in roles if not x.is_internal()],
            },
        )
        formdef.add_fields_to_form(form, form_data=self.user.form_data)

        if not self.user.is_readonly():
            form.add_submit('submit', _('Submit'))
        form.add_submit('cancel', _('Cancel'))
        form.add_media()

        if form.get_widget('cancel').parse():
            return redirect('..')

        if form.get_submit() == 'submit' and not form.has_errors():
            formdef = get_publisher().user_class.get_formdef()
            data = formdef.get_data(form)
            self.user.set_attributes_from_formdata(data)
            self.user.form_data = data

            if get_publisher().test_user_class.count(
                [Equal('email', self.user.email), StrictNotEqual('id', self.user.id)]
            ):
                form.add_global_errors([_('A test user with this email already exists.')])
            else:
                self.user.name = form.get_widget('name').parse()
                self.user.roles = form.get_widget('roles').parse()
                self.user.store(comment=_('Change in attribute values'))

                return redirect('..')

        if self.user.is_readonly():
            r = TemplateIO(html=True)
            r += htmltext('<div class="infonotice"><p>%s</p></div>') % _('This user is readonly.')
            r += utils.snapshot_info_block(self.user.snapshot_object)
            get_response().filter['sidebar'] = r.getvalue()

        r = TemplateIO(html=True)
        r += htmltext('<div id="appbar">')
        r += htmltext('<h2>%s</h2>') % (_('Edit test user'))
        if not self.user.is_readonly():
            r += htmltext('<span class="actions">')
            r += htmltext('<a href="export">%s</a>') % _('Export')
            r += htmltext('<a href="history/">%s</a>') % _('History')
            r += htmltext('</span>')
        r += htmltext('</div>')
        r += form.render()
        return r.getvalue()

    def delete(self):
        form = Form(enctype='multipart/form-data')
        form.add_submit('delete', _('Delete'))
        form.add_submit('cancel', _('Cancel'))

        if form.get_widget('cancel').parse():
            return redirect('.')
        if not form.is_submitted() or form.has_errors():
            get_response().breadcrumb.append(('delete', _('Delete')))
            r = TemplateIO(html=True)
            r += htmltext('<h2>%s %s</h2>') % (_('Deleting:'), self.user)
            r += form.render()
            return r.getvalue()

        self.user.remove_object(self.user.id)
        return redirect('..')

    def export(self):
        return misc.xml_response(self.user, filename='test-user-%s.wcs' % self.user.name, include_id=False)


class TestUsersDirectory(Directory):
    _q_exports = ['', 'new', 'export', ('import', 'p_import')]

    def _q_traverse(self, path):
        get_response().breadcrumb.append(('test-users/', _('Test users')))
        return super()._q_traverse(path)

    def _q_lookup(self, component):
        return TestUserPage(component)

    def _q_index(self):
        context = {
            'users': get_publisher().test_user_class.select(),
            'has_sidebar': True,
        }
        get_response().add_javascript(['popup.js', 'select2.js'])
        get_response().set_title(_('Test users'))
        return template.QommonTemplateResponse(
            templates=['wcs/backoffice/test-users.html'],
            context=context,
            is_django_native=True,
        )

    def new(self):
        form = Form(enctype='multipart/form-data')
        form.add(StringWidget, 'name', title=_('Name'), required=True, size=50)

        creation_options = [
            ('empty', _('Empty user'), 'empty'),
            ('copy', _('Copy existing user'), 'copy'),
        ]
        form.add(
            RadiobuttonsWidget,
            'creation_mode',
            options=creation_options,
            value='empty',
            attrs={'data-dynamic-display-parent': 'true'},
        )
        form.attrs['data-js-features'] = 'true'
        form.add(
            JsonpSingleSelectWidget,
            'user_id',
            url='/api/users/',
            attrs={
                'data-dynamic-display-child-of': 'creation_mode',
                'data-dynamic-display-value-in': 'copy',
            },
        )

        form.add_submit('submit', _('Submit'))
        form.add_submit('cancel', _('Cancel'))

        if form.get_widget('cancel').parse():
            return redirect('.')

        if form.is_submitted() and not form.has_errors():
            user_id = form.get_widget('user_id').parse()
            if form.get_widget('creation_mode').parse() == 'empty' or not user_id:
                user = get_publisher().user_class()
                user.test_uuid = str(uuid.uuid4())
                user.name_identifiers = [uuid.uuid4().hex]
            else:
                user = get_publisher().user_class.get(user_id)
                user, created = TestDef.get_or_create_test_user(user)
                if not created:
                    form.get_widget('user_id').set_error(_('A test user with this email already exists.'))

            if not form.has_errors():
                user.name = form.get_widget('name').parse()
                user.store(comment=_('Creation'))
                return redirect('.')

        get_response().breadcrumb.append(('new', _('New')))
        get_response().set_title(_('New test user'))
        r = TemplateIO(html=True)
        r += htmltext('<h2>%s</h2>') % _('New test user')
        r += form.render()
        return r.getvalue()

    def export(self):
        root = ET.Element('test-users')
        for user in get_publisher().test_user_class.select():
            root.append(user.export_to_xml(include_id=False))
        ET.indent(root)
        get_response().set_content_type('text/xml')
        get_response().set_header('content-disposition', 'attachment; filename=test_users.wcs')
        return '<?xml version="1.0"?>\n' + ET.tostring(root).decode('utf-8')

    def p_import(self):
        form = Form(enctype='multipart/form-data')

        form.add(FileWidget, 'file', title=_('File'), required=True)
        form.add_submit('submit', _('Import'))
        form.add_submit('cancel', _('Cancel'))

        if form.get_submit() == 'cancel':
            return redirect('.')

        if form.is_submitted() and not form.has_errors():
            try:
                return self.import_submit(form)
            except ValueError:
                pass

        get_response().breadcrumb.append(('import', _('Import')))
        get_response().set_title(_('Import test users'))
        r = TemplateIO(html=True)
        r += htmltext('<h2>%s</h2>') % _('Import test users')
        r += form.render()
        return r.getvalue()

    def import_submit(self, form):
        fp = form.get_widget('file').parse().fp
        try:
            tree = ET.parse(fp)
        except Exception:
            form.set_error('file', _('Invalid File'))
            raise ValueError

        existing_users = get_publisher().test_user_class.select()
        existing_uuids = {x.test_uuid for x in existing_users}
        existing_emails = {x.email for x in existing_users}

        users = []
        users_were_ignored = False
        for sub in tree.findall('user') or [tree]:
            try:
                user = get_publisher().user_class.import_from_xml_tree(sub)
            except Exception:
                form.set_error('file', _('Invalid File'))
                raise ValueError

            if user.test_uuid in existing_uuids or user.email in existing_emails:
                users_were_ignored = True
                continue

            users.append(user)

        for user in users:
            user.store(comment=_('Creation (from import)'))

        if users_were_ignored:
            get_session().add_message(_('Some already existing users were not imported.'), level='warning')
        else:
            get_session().add_message(_('Test users have been successfully imported.'), level='success')

        return redirect('.')
