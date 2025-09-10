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
import datetime
import http
import io
import json
import socket
import urllib.parse
import uuid
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from functools import cached_property

import freezegun
import requests
from django.core.handlers.wsgi import WSGIRequest
from django.utils.timezone import now
from quixote import get_publisher, get_request, get_session, get_session_manager
from urllib3 import HTTPResponse

from wcs import sql
from wcs.carddef import CardDef
from wcs.compat import CompatHTTPRequest
from wcs.data_sources import get_object
from wcs.fields import Field, PageField
from wcs.formdef import FormDef
from wcs.qommon.form import FileWithPreviewWidget, Form, get_selection_error_text
from wcs.qommon.misc import classproperty
from wcs.qommon.storage import Equal
from wcs.qommon.template import TemplateError
from wcs.qommon.xml_storage import XmlStorableObject
from wcs.wf.create_formdata import CreateFormdataWorkflowStatusItem
from wcs.wf.external_workflow import ExternalWorkflowGlobalAction
from wcs.wf.form import WorkflowFormFieldsFormDef
from wcs.workflows import WorkflowStatusItem

from .qommon import _


class TestError(Exception):
    action_uuid = ''

    def __init__(self, msg, error=None, details=None, field_id='', dependency_uuid=''):
        self.msg = msg
        self.error = error or msg
        self.details = details or []
        self.field_id = field_id
        self.dependency_uuid = dependency_uuid

    # prevent pytest from trying to collect this class (#75521)
    __test__ = False


class TestDefXmlProxy(XmlStorableObject):
    xml_root_node = 'testdef'
    _names = 'testdef'
    readonly = True

    # prevent pytest from trying to collect this class
    __test__ = False

    _webservice_responses = []

    @classproperty
    def XML_NODES(self):
        json_to_xml_types = {
            'varchar': 'str',
            'boolean': 'bool',
            'jsonb': 'jsonb',
            'timestamptz': 'datetime',
            'text[]': 'str_list',
        }
        excluded_fields = ['id']
        extra_fields = [
            ('_webservice_responses', 'webservice_responses'),
            ('workflow_tests', 'workflow_tests'),
        ]

        return [
            (field, json_to_xml_types[kind])
            for field, kind in sql.TestDef._table_static_fields
            if field not in excluded_fields
        ] + extra_fields

    def export_jsonb_to_xml(self, element, attribute_name, **kwargs):
        element.text = json.dumps(getattr(self, attribute_name), indent=2, sort_keys=True)

    def import_jsonb_from_xml(self, element, **kwargs):
        return json.loads(element.text)

    def export_workflow_tests_to_xml(self, element, attribute_name, include_id=False):
        workflow_tests = self.workflow_tests.export_to_xml(include_id=include_id)
        if include_id and workflow_tests.get('id'):
            element.set('id', workflow_tests.get('id'))

        for subelement in workflow_tests:
            element.append(subelement)

    def import_workflow_tests_from_xml(self, element, include_id=False):
        from wcs.workflow_tests import WorkflowTests

        return WorkflowTests.import_from_xml_tree(element, include_id=include_id)

    def export_webservice_responses_to_xml(self, element, attribute_name, include_id=False):
        for response in self._webservice_responses:
            element.append(response.export_to_xml(include_id=include_id))

    def import_webservice_responses_from_xml(self, element, include_id=False):
        return [
            WebserviceResponse.import_from_xml_tree(response, include_id=include_id) for response in element
        ]


class TestDef(sql.TestDef):
    _names = 'testdef'

    # prevent pytest from trying to collect this class
    __test__ = False

    uuid = ''
    name = ''
    object_type = None  # (formdef, carddef, etc.)
    object_id = None

    data = None  # (json export of formdata, carddata, etc.)
    query_parameters = None
    is_in_backoffice = False
    expected_error = None
    user_uuid = None  # defines form_user
    submission_agent_uuid = None  # defines form_submission_agent
    agent_id = None  # receiver user, to handle form after submission
    frozen_submission_datetime = None
    dependencies = None
    workflow_options = None

    user = None
    coverage = None

    ignored_field_types = (
        'subtitle',
        'title',
        'comment',
        'computed',
        'table',
        'table-select',
        'tablerows',
        'ranked-items',
    )
    backoffice_class = 'wcs.admin.tests.TestPage'

    xml_root_node = TestDefXmlProxy.xml_root_node
    get_table_name = TestDefXmlProxy.get_table_name
    is_readonly = TestDefXmlProxy.is_readonly

    def __init__(self):
        self.uuid = str(uuid.uuid4())
        self.query_parameters = {}
        self.dependencies = []
        self.workflow_options = {}

    def __str__(self):
        return self.name

    def __repr__(self):
        return '<%s %r id:%s>' % (self.__class__.__name__, self.name, self.id)

    @cached_property
    def formdef(self):
        klass = FormDef if self.object_type == 'formdefs' else CardDef
        return klass.get(self.object_id)

    @property
    def workflow_tests(self):
        from wcs.workflow_tests import WorkflowTests

        if hasattr(self, '_workflow_tests'):
            return self._workflow_tests

        workflow_tests_list = WorkflowTests.select([Equal('testdef_id', self.id)])
        self.workflow_tests = workflow_tests_list[0] if workflow_tests_list else WorkflowTests()
        return self._workflow_tests

    @workflow_tests.setter
    def workflow_tests(self, value):
        self._workflow_tests = value
        self._workflow_tests.testdef = self

    def get_webservice_responses(self):
        if hasattr(self, '_webservice_responses'):
            # this attribute is set by import/export, and should be used in snapshot context
            return self._webservice_responses
        return WebserviceResponse.select([Equal('testdef_id', self.id)], order_by='name')

    def get_admin_url(self):
        base_url = get_publisher().get_backoffice_url()
        objects_dir = 'forms' if self.object_type == 'formdefs' else 'cards'
        return '%s/%s/%s/tests/%s/' % (base_url, objects_dir, self.object_id, self.id)

    def get_test_dependencies(self):
        return TestDef.select([sql.Contains('uuid', self.dependencies)], order_by='name')

    def get_test_dependencies_recursively(self, seen_uuids=None, include_self=True):
        seen_uuids = seen_uuids or set()

        if self.uuid in seen_uuids:
            return

        seen_uuids.add(self.uuid)

        if include_self:
            yield self

        for dependency in self.get_test_dependencies():
            yield from dependency.get_test_dependencies_recursively(seen_uuids)

    def get_last_dependencies_results(self):
        dependencies = self.get_test_dependencies()
        results = TestResult.select([sql.Contains('test_id', [x.id for x in dependencies])])

        result_lists = TestResults.select([sql.Contains('id', [x.test_results_id for x in results])])
        result_lists_by_id = {x.id: x for x in result_lists}

        results.sort(key=lambda x: result_lists_by_id[x.test_results_id].timestamp, reverse=True)

        last_result_by_test_id = {}
        for result in results:
            if result.test_id not in last_result_by_test_id:
                last_result_by_test_id[result.test_id] = result

        return list(last_result_by_test_id.values())

    def get_last_test_result(self, objectdef):
        test_results = objectdef.get_last_test_results()
        if not test_results:
            return

        results = [x for x in test_results.results if x.test_id == self.id and x.formdata_id]
        if not results:
            return

        return results[0]

    def get_possibly_created_formdefs(self, seen_formdefs=None):
        formdefs = {dependency.formdef for dependency in self.get_test_dependencies_recursively()}

        for formdef in formdefs.copy():
            formdefs.update(self.get_possibly_created_formdefs_frow_workflow(formdef, formdefs))

        return formdefs

    def get_possibly_created_formdefs_frow_workflow(self, formdef, seen_formdefs):
        for item in formdef.workflow.get_all_items():
            if isinstance(item, (CreateFormdataWorkflowStatusItem, ExternalWorkflowGlobalAction)):
                if not item.formdef or item.formdef in seen_formdefs:
                    continue

                seen_formdefs.add(item.formdef)

                yield item.formdef
                yield from self.get_possibly_created_formdefs_frow_workflow(item.formdef, seen_formdefs)

    def get_workflow_options(self):
        if not self.workflow_options or not self.formdef.workflow.variables_formdef:
            return

        form_data = self.deserialize_form_data(self.formdef.workflow.variables_formdef, self.workflow_options)
        self.formdef.set_variable_options(form_data)
        return self.formdef.workflow_options

    @contextmanager
    def freeze_submission_datetime(self):
        submission_datetime = self.frozen_submission_datetime or now()

        freezer = freezegun.freeze_time(submission_datetime, tz_offset=submission_datetime.utcoffset())
        try:
            self.fake_datetime = freezer.start()
            yield freezer
        finally:
            try:
                freezer.stop()
            except IndexError:
                # in case freezer has been stopped by outside code
                pass

    def store(self, comment=None):
        super().store()

        self.workflow_tests.testdef_id = self.id
        self.workflow_tests.store()

        if hasattr(self, '_webservice_responses'):
            # first store after import, attach webservice responses and delete old ones on snapshot restore
            response_ids = {x.id for x in self._webservice_responses}
            for response in WebserviceResponse.select([Equal('testdef_id', self.id)]):
                if response.id not in response_ids:
                    response.remove_self()

            for response in self._webservice_responses:
                response.testdef_id = self.id
                response.store()
            del self._webservice_responses

        if get_publisher().snapshot_class:
            get_publisher().snapshot_class.snap(instance=self, comment=comment)

    @classmethod
    def remove_object(cls, id):
        super().remove_object(id)
        from wcs.workflow_tests import WorkflowTests

        workflow_tests_list = WorkflowTests.select([Equal('testdef_id', id)])
        for workflow_tests in workflow_tests_list:
            workflow_tests.remove_self()

        responses = WebserviceResponse.select([Equal('testdef_id', id)])
        for response in responses:
            response.remove_self()

    @classmethod
    def select_for_objectdef(cls, objectdef, order_by=None):
        return cls.select(
            [Equal('object_type', objectdef.get_table_name()), Equal('object_id', str(objectdef.id))],
            order_by=order_by,
        )

    @staticmethod
    def get_or_create_test_user(user):
        users = get_publisher().test_user_class.select([Equal('email', user.email)])
        if len(users):
            return users[0], False

        user.id = None
        user.test_uuid = str(uuid.uuid4())
        user.store()
        return user, True

    @staticmethod
    def serialize_from_datasource(field, value):
        data_source = field.get_real_data_source()
        if not data_source:
            return value

        data_source_type = data_source.get('type', '')
        if data_source_type == 'wcs:users':
            try:
                user = get_publisher().test_user_class.get(value)
            except KeyError:
                value = None
            else:
                value = user.test_uuid
        elif data_source_type.startswith('carddef:'):
            data_source = get_object(data_source)
            value = data_source.get_display_value(value)

        return value

    @classmethod
    def serialize_form_data(cls, formdef, form_data):
        field_data = {}
        for field in formdef.fields:
            if field.key in cls.ignored_field_types:
                continue

            if field.id in form_data:
                value = form_data[field.id]

                if value and hasattr(field, 'get_real_data_source'):
                    if isinstance(value, list):
                        value = [cls.serialize_from_datasource(field, x) for x in value]
                    else:
                        value = cls.serialize_from_datasource(field, value)

                if value is not None and hasattr(field, 'get_json_value'):
                    value = field.get_json_value(value, include_file_content=True)

                field_data[field.id] = value

            for suffix in ('display', 'structured'):
                key = '%s_%s' % (field.id, suffix)
                if key in form_data:
                    field_data[key] = form_data[key]

        return field_data

    @classmethod
    def create_from_formdata(cls, formdef, formdata, add_workflow_tests=False):
        testdef = cls()
        testdef.object_type = formdef.get_table_name()
        testdef.object_id = str(formdef.id)
        testdef.is_in_backoffice = formdata.backoffice_submission
        testdef.frozen_submission_datetime = formdata.receipt_time

        testdef.data = {
            'fields': cls.serialize_form_data(formdef, formdata.data),
        }

        if formdata.user:
            user, dummy = cls.get_or_create_test_user(copy.deepcopy(formdata.user))
            testdef.user_uuid = user.test_uuid

        if add_workflow_tests:
            testdef.store()
            testdef.workflow_tests.add_actions_from_formdata(formdata)

        return testdef

    @staticmethod
    def deserialize_from_datasource(field, value):
        data_source = field.get_real_data_source()
        if not data_source:
            return value

        data_source_type = data_source.get('type', '')
        if data_source_type == 'wcs:users':
            try:
                user = get_publisher().test_user_class.select([Equal('test_uuid', value)])[0]
            except IndexError:
                value = None
            else:
                value = str(user.id)
        elif data_source_type.startswith('carddef:'):  # and isinstance(value, dict):
            data_source = get_object(data_source)
            card = data_source.get_card_structured_value_by_id(value)
            if card:
                value = str(card.get('id'))

        return value

    @classmethod
    def deserialize_field_value(cls, field, value):
        if value is not None:
            value = field.from_json_value(value)

        if value and hasattr(field, 'get_real_data_source'):
            if isinstance(value, list):
                value = [cls.deserialize_from_datasource(field, x) for x in value]
            else:
                value = cls.deserialize_from_datasource(field, value)

        return value

    @classmethod
    def deserialize_form_data(cls, formdef, form_data):
        form_data = copy.deepcopy(form_data)
        for field in formdef.fields:
            if field.id not in form_data:
                continue

            form_data[field.id] = cls.deserialize_field_value(field, form_data[field.id])

        return form_data

    @staticmethod
    def get_test_user(user_uuid):
        if not user_uuid:
            return

        try:
            return get_publisher().test_user_class.select([Equal('test_uuid', user_uuid)])[0]
        except IndexError:
            pass

    def build_formdata(self, objectdef, include_fields=False):
        formdata = objectdef.data_class()()
        formdata.backoffice_submission = self.is_in_backoffice

        self.user = self.get_test_user(self.user_uuid)
        if self.user:
            formdata.user_id = self.user.id

        submission_agent = self.get_test_user(self.submission_agent_uuid)
        if submission_agent:
            formdata.submission_agent_id = submission_agent.id

        if include_fields:
            for field in objectdef.fields:
                if field.id not in self.data['fields']:
                    continue

                value = self.data['fields'].get(field.id)
                value = self.deserialize_field_value(field, value)

                self.add_value_to_form_data(field, formdata.data, value)

        return formdata

    @contextmanager
    def use_test_objects(self, results=None):
        base_user_class = get_publisher().user_class
        original_test_result_ids = get_publisher().allowed_test_result_ids
        original_test_formdefs = get_publisher().test_formdefs
        original_workflow_options = get_publisher().workflow_options_forced_value
        real_http_adapter = getattr(get_publisher(), '_http_adapter', None)

        results = results if results is not None else [self.result]

        try:
            get_publisher().user_class = get_publisher().test_user_class
            get_publisher().allowed_test_result_ids = [x.id for x in results]
            get_publisher().test_formdefs = self.get_possibly_created_formdefs()
            get_publisher().workflow_options_forced_value = self.get_workflow_options()
            get_publisher()._http_adapter = MockWebserviceResponseAdapter(self)
            yield
        finally:
            get_publisher().user_class = base_user_class
            get_publisher().allowed_test_result_ids = original_test_result_ids
            get_publisher().test_formdefs = original_test_formdefs
            get_publisher().workflow_options_forced_value = original_workflow_options
            get_publisher()._http_adapter = real_http_adapter

    @contextmanager
    def fake_request(self):
        def record_error(error_summary=None, exception=None, *args, **kwargs):
            self.result.recorded_errors.append(str(error_summary or exception))

        real_record_error = get_publisher().record_error

        true_request = get_publisher().get_request()
        wsgi_request = WSGIRequest(
            {
                'REQUEST_METHOD': 'POST',
                'SERVER_NAME': get_publisher().tenant.hostname,
                'SERVER_PORT': 80,
                'SCRIPT_NAME': '',
                'wsgi.input': io.StringIO(),
            }
        )
        fake_request = CompatHTTPRequest(wsgi_request)
        fake_request.is_in_backoffice_forced_value = self.is_in_backoffice
        fake_request.query_parameters_forced_value = self.query_parameters

        fake_token = get_publisher().token_class()
        fake_token.store = lambda: None

        try:
            get_publisher()._set_request(fake_request)
            fake_request.session = get_session_manager().new_session(None)
            fake_request.session.create_token = lambda *args, **kwargs: fake_token
            get_publisher().record_error = record_error
            yield
        finally:
            get_publisher()._set_request(true_request)
            get_publisher().record_error = real_record_error

    @cached_property
    def result(self):
        return TestResult(self)

    def run(self, objectdef, seen_uuids=None):
        self.exception = None
        self.used_webservice_responses = []
        with (
            self.fake_request(),
            self.use_test_objects(),
            self.freeze_submission_datetime(),
        ):
            try:
                self._run(objectdef, seen_uuids)
            except TestError as e:
                if not self.expected_error:
                    raise e

                if e.error != self.expected_error:
                    raise TestError(
                        _('Expected error "%(expected_error)s" but got error "%(error)s" instead.')
                        % {'expected_error': self.expected_error, 'error': e.error},
                        field_id=e.field_id,
                    )
            else:
                if self.expected_error:
                    raise TestError(
                        _('Expected error "%s" but test completed with success.') % self.expected_error
                    )

    def run_dependencies(self, seen_uuids):
        seen_uuids = seen_uuids or set()

        if self.uuid in seen_uuids:
            raise TestError(_('Loop in dependencies.'))

        seen_uuids.add(self.uuid)

        for testdef_uuid in self.dependencies:
            try:
                testdef = TestDef.select([Equal('uuid', testdef_uuid)])[0]
            except IndexError:
                raise TestError(_('Missing test dependency.'))

            testdef.result.id = self.result.id

            try:
                testdef.run(testdef.formdef, seen_uuids.copy())
            except TestError as e:
                if e.dependency_uuid:
                    raise e

                raise TestError(_('Error in dependency: %s') % e, dependency_uuid=testdef.uuid)

            testdef.formdata.store()

    def _run(self, objectdef, seen_uuids):
        self.run_dependencies(seen_uuids)

        self.formdata = formdata = self.build_formdata(objectdef)
        formdata.test_result_id = self.result.id
        get_request()._user = formdata.user

        get_publisher().reset_formdata_state()
        get_publisher().substitutions.feed(get_request())
        get_publisher().substitutions.feed(objectdef)
        get_publisher().substitutions.feed(formdata)
        get_publisher().substitutions.feed(formdata.user)
        get_publisher().substitutions.feed(get_session())

        self.run_form_fill(objectdef, formdata.data)
        formdata.just_created()
        if self.workflow_tests.actions:
            formdata.store()
            self.workflow_tests.run(formdata)

    def run_form_fill(self, objectdef, form_data=None, edit_action=None):
        form_data = form_data if form_data is not None else {}

        self.form = Form(action='#')

        fields = []
        fields_by_page = {}
        for field in objectdef.fields:
            if field.key == 'page':
                fields = fields_by_page[field] = []
                continue
            fields.append(field)

        self.single_page_form = False
        if not fields_by_page:  # form without pages
            self.single_page_form = True
            fields_by_page[PageField()] = fields

        if edit_action and edit_action.operation_mode in ('single', 'partial'):
            edit_pages = edit_action.get_edit_pages(list(fields_by_page))
            if not edit_pages:
                raise TestError(_('Page to edit was not found.'))

            fields_by_page = {page: fields_by_page[page] for page in edit_pages}

        previous_page = None
        for i, (page, fields) in enumerate(fields_by_page.items(), 1):
            page.index = i

            if previous_page:
                self.evaluate_page_conditions(previous_page, form_data, objectdef)

            if page and not page.is_visible(form_data, objectdef):
                if self.coverage:
                    for field in [page] + fields:
                        if field.key not in TestDef.ignored_field_types:
                            self.coverage['fields'][field.id]['hidden'].append(self.id)

                fields_with_data = [
                    field for field in fields if self.data['fields'].get(field.id) is not None
                ]
                if fields_with_data:
                    raise TestError(
                        _('Tried to fill field "%(label)s" on page %(no)d but page was not shown.')
                        % {'label': fields_with_data[0].label, 'no': page.index},
                        field_id=page.id,
                    )
                continue

            if self.coverage:
                self.coverage['fields'][page.id]['visible'].append(self.id)

            self.fill_page_fields(fields, page, form_data, objectdef)
            previous_page = page

            # remove access to query string as it is limited to first page
            get_request().query_parameters_forced_value = None

        if previous_page:  # evaluate last page post conditions
            self.evaluate_page_conditions(previous_page, form_data, objectdef)

    @staticmethod
    def has_remote_data_source(field):
        if not hasattr(field, 'get_real_data_source'):
            return False

        real_data_source = field.get_real_data_source()
        if not real_data_source:
            return False

        ds_type = real_data_source.get('type', '')
        return not bool(ds_type.startswith('carddef:') or ds_type == 'wcs:users')

    def fill_page_fields(self, fields, page, form_data, objectdef):
        self.handle_computed_fields(fields, form_data)
        for field in fields:
            if field.key in self.ignored_field_types:
                continue

            if not field.is_visible(form_data, objectdef):
                if self.coverage:
                    self.coverage['fields'][field.id]['hidden'].append(self.id)

                if self.data['fields'].get(field.id) is not None:
                    if self.single_page_form:
                        field_info = _('"%s"') % field.label
                    else:
                        field_info = _('"%(label)s" on page %(no)d') % {
                            'label': field.label,
                            'no': page.index,
                        }
                    raise TestError(
                        _('Tried to fill field %s but it is hidden.') % field_info,
                        field_id=field.id,
                    )
                continue

            if self.coverage:
                self.coverage['fields'][field.id]['visible'].append(self.id)

            # make sure to never request remote data source
            if self.has_remote_data_source(field):
                field.data_source = None
                field.had_data_source = True
            elif hasattr(field, 'block'):
                for x in field.block.fields:
                    if self.has_remote_data_source(field):
                        x.data_source = None
                        x.had_data_source = True

            value = self.data['fields'].get(field.id)
            value = self.deserialize_field_value(field, value)

            self.run_widget_validation(field, value)

            self.add_value_to_form_data(field, form_data, value)

            if isinstance(objectdef, WorkflowFormFieldsFormDef):
                objectdef.item.update_workflow_data(self.formdata, form_data, allow_legacy_storage=False)

            get_publisher().substitutions.invalidate_cache()

        self.handle_computed_fields(fields, form_data, exclude_frozen=True)

        if isinstance(objectdef, WorkflowFormFieldsFormDef):
            objectdef.item.update_workflow_data(
                self.formdata, form_data, submit=True, allow_legacy_storage=False
            )
            self.formdata.store()

    def add_value_to_form_data(self, field, form_data, value):
        if field.key in ('item', 'items') and (field.data_source or hasattr(field, 'had_data_source')):
            # add values without requesting data source
            form_data[field.id] = value
            for suffix in ('display', 'structured'):
                key = '%s_%s' % (field.id, suffix)
                if key in self.data['fields']:
                    form_data[key] = self.data['fields'][key]
        else:
            field.set_value(form_data, value)

    def evaluate_page_conditions(self, page, form_data, objectdef):
        for post_condition in page.post_conditions or []:
            condition = post_condition.get('condition', {})
            try:
                if not Field.evaluate_condition(form_data, objectdef, condition, record_errors=False):
                    error_message = WorkflowStatusItem.compute(post_condition.get('error_message'))
                    raise TestError(
                        _('Page %(no)d post condition was not met (%(condition)s).')
                        % {'no': page.index, 'condition': condition.get('value')},
                        error=error_message,
                        field_id=page.id,
                    )
            except RuntimeError:
                raise TestError(
                    _('Failed to evaluate page %d post condition.') % page.index, field_id=page.id
                )

    def run_widget_validation(self, field, value):
        widget = field.add_to_form(self.form)

        if isinstance(widget, FileWithPreviewWidget):
            widget.get_value_from_token = False

        widget.set_value(value)
        widget.transfer_form_value(get_publisher().get_request())

        widget._parsed = False
        widget.parse()

        widget = TestDef.get_error_widget(widget, self)
        if not widget:
            return

        field_label = _('"%s"') % field.label

        if getattr(widget, 'is_subwidget', False):
            value = widget.value
            field = widget.field
            field_label = _('"%(subfield)s" (of field %(field)s)') % {
                'subfield': field.label,
                'field': field_label,
            }

        if field.convert_value_to_str:
            value = field.convert_value_to_str(value)

        error_msg = _('Invalid value "%s"') % value if value else _('Empty value')
        raise TestError(
            _('%(error)s for field %(label)s: %(details)s')
            % {
                'error': error_msg,
                'label': field_label,
                'details': widget.error,
            },
            error=widget.error,
            field_id=field.id,
        )

    def handle_computed_fields(self, fields, form_data, exclude_frozen=False):
        for field in fields:
            if field.key != 'computed':
                continue
            if exclude_frozen and field.freeze_on_initial_value:
                continue

            with get_publisher().complex_data():
                try:
                    value = WorkflowStatusItem.compute(field.value_template, raises=True, allow_complex=True)
                except TemplateError:
                    continue
                else:
                    value = get_publisher().get_cached_complex_data(value)

                if isinstance(value, str) and len(value) > 10000:
                    value = None

                form_data[field.id] = value
                get_publisher().substitutions.invalidate_cache()

    @staticmethod
    def widget_has_real_error(widget, testdef):
        if widget.error == widget.REQUIRED_ERROR:
            if testdef:
                label = widget.block.name if hasattr(widget, 'block') else widget.field.label
                testdef.result.missing_required_fields.append(label)
            return False

        ignore_invalid_selection = bool(
            widget.error == get_selection_error_text()
            and (widget.field.data_source or hasattr(widget.field, 'had_data_source'))
        )
        if ignore_invalid_selection:
            return False

        return True

    @classmethod
    def get_error_widget(cls, widget, testdef=None):
        if not widget.has_error():
            return

        if widget.field.key == 'block' and (not widget.error or widget.error == widget.REQUIRED_ERROR):
            widget.error = None
            return cls.get_error_subwidget(widget, testdef)

        if cls.widget_has_real_error(widget, testdef):
            return widget

    @classmethod
    def get_error_subwidget(cls, widget, testdef):
        for widget in widget.get_widgets():
            widget.is_subwidget = True

            if widget.error and cls.widget_has_real_error(widget, testdef):
                return widget

            if hasattr(widget, 'get_widgets'):
                widget = TestDef.get_error_subwidget(widget, testdef)
                if widget:
                    return widget

    def add_to_coverage(self, workflow_item):
        if self.coverage:
            self.coverage['workflow'][workflow_item.parent.id][workflow_item.id].append(self.id)

    def export_to_xml(self, include_id=False):
        testdef_xml = TestDefXmlProxy(id=str(self.id))
        for field, dummy in TestDefXmlProxy.XML_NODES:  # pylint: disable=not-an-iterable
            if field == '_webservice_responses':
                testdef_xml._webservice_responses = self.get_webservice_responses()
            else:
                setattr(testdef_xml, field, getattr(self, field))

        return testdef_xml.export_to_xml(include_id=include_id)

    @classmethod
    def import_from_xml(cls, fd, formdef, include_id=False):
        try:
            tree = ET.parse(fd)
        except Exception:
            raise ValueError
        return cls.import_from_xml_tree(tree, formdef, include_id=include_id)

    @classmethod
    def import_from_xml_tree(cls, tree, formdef=None, include_id=False, **kwargs):
        testdef_xml = TestDefXmlProxy.import_from_xml_tree(tree, include_id)

        if not formdef:
            klass = FormDef if testdef_xml.object_type == 'formdefs' else CardDef
            formdef = klass.get(testdef_xml.object_id)

        testdef = TestDef.create_from_formdata(formdef, formdef.data_class()())
        testdef.id = int(testdef_xml.id) if testdef_xml.id else None

        for field, dummy in TestDefXmlProxy.XML_NODES:  # pylint: disable=not-an-iterable
            if field in ('object_type', 'object_id'):
                continue

            if hasattr(testdef_xml, field):
                setattr(testdef, field, getattr(testdef_xml, field))

        return testdef

    def get_dependencies(self):
        if self.user_uuid:
            try:
                user = get_publisher().test_user_class.select([Equal('test_uuid', self.user_uuid)])[0]
            except IndexError:
                pass
            else:
                yield user
                yield from user.get_dependencies()
        if self.agent_id:
            try:
                user = get_publisher().test_user_class.select([Equal('test_uuid', self.agent_id)])[0]
            except IndexError:
                pass
            else:
                yield user
                yield from user.get_dependencies()
        yield from self.workflow_tests.get_dependencies()

        for dependency in self.get_test_dependencies():
            yield dependency.formdef


class TestResults(sql.TestResults):
    _names = 'test_results'

    object_type = None  # (formdef, carddef, etc.)
    object_id = None
    timestamp = None
    success = None
    reason = None  # reason for tests execution
    coverage = None

    def __init__(self):
        self.coverage = {
            'fields': collections.defaultdict(lambda: {'visible': [], 'hidden': []}),
            'workflow': collections.defaultdict(lambda: collections.defaultdict(list)),
        }

    def get_admin_url(self):
        base_url = get_publisher().get_backoffice_url()
        objects_dir = 'forms' if self.object_type == 'formdefs' else 'cards'
        return '%s/%s/%s/tests/results/%s/' % (base_url, objects_dir, self.object_id, self.id)

    @cached_property
    def results(self):
        return TestResult.select([Equal('test_results_id', self.id)], order_by='test_id')

    @classmethod
    def clean(cls, publisher=None, **kwargs):
        test_results_by_formdef = collections.defaultdict(list)
        for test_results in cls.select(order_by='-timestamp'):
            test_results_by_formdef[(test_results.object_id, test_results.object_type)].append(test_results)

        deletion_timestamp_by_formdef = {}
        for formdef_key, test_results in test_results_by_formdef.items():
            success = False
            test_results_count = 0
            for test_results in test_results:
                if test_results.success is None:
                    continue

                test_results_count += 1

                if (
                    success
                    and test_results_count > 10
                    and test_results.timestamp < now() - datetime.timedelta(days=14)
                ):
                    break

                success |= test_results.success
            else:
                continue

            deletion_timestamp_by_formdef[formdef_key] = test_results.timestamp

        for (object_id, object_type), deletion_timestamp in deletion_timestamp_by_formdef.items():
            TestResults.wipe(
                clause=[
                    sql.LessOrEqual('timestamp', deletion_timestamp),
                    sql.Equal('object_id', str(object_id)),
                    sql.Equal('object_type', object_type),
                ]
            )

    def get_all_coverable_items(self, formdef):
        excluded_items = [
            'addattachment',
        ]

        return [
            item
            for status in formdef.workflow.possible_status or []
            for item in status.items or []
            if item.key not in excluded_items
        ]

    def set_coverage_percent(self, formdef):
        fields_count = len(self.coverage['fields'])
        covered_fields_count = len([f for f, data in self.coverage['fields'].items() if data['visible']])

        self.coverage['percent_fields'] = (
            int(covered_fields_count / fields_count * 100) if fields_count else 100
        )

        if 'workflow' not in self.coverage:
            return

        items_count = len(self.get_all_coverable_items(formdef))
        covered_items_count = sum(len(items) for items in self.coverage['workflow'].values())

        self.coverage['percent_workflow'] = (
            int(covered_items_count / items_count * 100) if items_count else 100
        )


class TestResult(sql.TestResult):
    _names = 'test_result'

    test_results_id = None
    test_id = None
    test_name = ''
    error = ''
    recorded_errors = None
    missing_required_fields = None
    sent_requests = None
    workflow_test_action_uuid = ''
    error_details = None
    error_field_id = ''
    dependency_uuid = ''

    def __init__(self, testdef):
        self.test_id = testdef.id
        self.test_name = str(testdef)
        self.recorded_errors = []
        self.missing_required_fields = []
        self.sent_requests = []
        self.error_details = []

    @property
    def has_details(self):
        return bool(
            self.recorded_errors
            or self.missing_required_fields
            or self.sent_requests
            or self.error_details
            or self.error_field_id
            or self.dependency_uuid
        )

    def get_workflow_test_action(self, testdef):
        if not self.workflow_test_action_uuid or not testdef:
            return

        try:
            action = [x for x in testdef.workflow_tests.actions if x.uuid == self.workflow_test_action_uuid][
                0
            ]
        except IndexError:
            return

        action.url = testdef.get_admin_url() + 'workflow/#%s' % action.id
        return action

    def get_error_field(self, formdef):
        if not self.error_field_id:
            return

        try:
            field = [x for x in formdef.fields if x.id == self.error_field_id][0]
        except IndexError:
            return

        field.url = formdef.get_field_admin_url(field)
        return field

    @cached_property
    def dependency(self):
        try:
            return TestDef.select([Equal('uuid', self.dependency_uuid)])[0]
        except IndexError:
            pass


class WebserviceResponseError(Exception):
    pass


class MockWebserviceResponseAdapter(requests.adapters.HTTPAdapter):
    def __init__(self, testdef, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.testdef = testdef
        self.inside_test_run = hasattr(self.testdef, 'used_webservice_responses')

    def send(self, request, *args, **kwargs):
        try:
            return self._send(request, *args, **kwargs)
        except WebserviceResponseError as e:
            raise requests.RequestException(str(e))
        except Exception as e:
            # Webservice call can happen through templates which catch all exceptions.
            # Record error to ensure we have a trace nonetheless.
            get_publisher().record_error(
                _('Unexpected error when mocking webservice call for url %(url)s: %(error)s.')
                % {'url': request.url.split('?')[0], 'error': str(e)}
            )
            raise e

    def _send(self, request, *args, **kwargs):
        request_info = {
            'url': request.url.split('?')[0],
            'method': request.method,
            'webservice_response_id': None,
            'forbidden_method': False,
            'response_mismatch_reasons': {},
        }
        self.testdef.result.sent_requests.append(request_info)

        for response in self.testdef.get_webservice_responses():
            if not response.is_configured():
                continue

            mismatch_reason = response.get_mismatch_reason(request)
            if not mismatch_reason:
                break
            if mismatch_reason == 'url':
                continue

            request_info['response_mismatch_reasons'][response.id] = mismatch_reason
        else:
            if request.method != 'GET' and self.inside_test_run:
                request_info['forbidden_method'] = True
                raise WebserviceResponseError(str(_('method must be GET')))
            return super().send(request, *args, **kwargs)

        request_info['webservice_response_id'] = response.id
        if self.inside_test_run:
            self.testdef.used_webservice_responses.append(response)

        headers = {
            'Content-Type': 'application/json',
        }

        raw_response = HTTPResponse(
            status=response.status_code,
            body=io.BytesIO(response.payload.encode()),
            headers=headers,
            original_response=self.make_original_response(headers),
            preload_content=False,
        )

        return self.build_response(request, raw_response)

    def make_original_response(self, headers):
        dummy_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        original_response = http.client.HTTPResponse(sock=dummy_socket)

        original_headers = http.client.HTTPMessage()
        for k, v in headers.items():
            original_headers.add_header(k, v)
        original_response.msg = original_headers

        return original_response


class WebserviceResponse(XmlStorableObject):
    _names = 'webservice-response'
    xml_root_node = 'webservice-response'

    uuid = None
    testdef_id = None
    name = ''
    payload = None
    url = None
    status_code = 200
    qs_data = None
    method = ''
    post_data = None

    XML_NODES = [
        ('uuid', 'str'),
        ('testdef_id', 'int'),
        ('name', 'str'),
        ('payload', 'str'),
        ('url', 'str'),
        ('status_code', 'int'),
        ('qs_data', 'kv_data'),
        ('method', 'str'),
        ('post_data', 'kv_data'),
    ]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.uuid = str(uuid.uuid4())
        self.qs_data = {}

    def __str__(self):
        return self.name

    def is_configured(self):
        return self.payload is not None and self.url

    @classmethod
    def create_from_evolution_part(cls, testdef, part):
        response = cls()
        response.testdef_id = testdef.id
        response.url = part.url.split('?')[0]
        response.name = response.url
        response.status = part.status

        try:
            payload = json.loads(part.data)
        except json.JSONDecodeError:
            return
        response.payload = json.dumps(payload, indent=2)

        parsed_url = urllib.parse.urlparse(part.url)
        query_string = urllib.parse.parse_qs(parsed_url.query)
        for param, values in query_string.items():
            if len(values) > 1:
                # multiple values are not supported
                continue

            if param in ('algo', 'orig', 'nonce', 'timestamp', 'signature'):
                # ignore internal authentication parameters
                continue

            response.qs_data[param] = values[0]

        response.store()
        return response

    def get_mismatch_reason(self, request):
        url = WorkflowStatusItem.compute(self.url)
        if request.url.split('?')[0] != url:
            return 'url'

        if self.method and request.method != self.method:
            return _('Method does not match (expected %(expected_method)s, was %(method)s).') % {
                'expected_method': self.method,
                'method': request.method,
            }

        parsed_url = urllib.parse.urlparse(request.url)
        query_string = urllib.parse.parse_qs(parsed_url.query)
        for param, value in (self.qs_data or {}).items():
            if param not in query_string:
                return _('Expected parameter %s not found in query string.') % param

            if value not in query_string[param]:
                return _(
                    'Wrong value for query string parameter %(param)s (expected %(expected_value)s, was %(value)s).'
                ) % {'param': param, 'expected_value': value, 'value': query_string[param][0]}

        try:
            request_data = json.loads(request.body)
        except (TypeError, ValueError):
            request_data = {}

        for param, value in (self.post_data or {}).items():
            with get_publisher().complex_data():
                value = WorkflowStatusItem.compute(value, allow_complex=True)
                value = get_publisher().get_cached_complex_data(value)

            if param not in request_data:
                return _('Expected parameter %s not found in request body.') % param

            if request_data[param] != value:
                return _(
                    'Wrong value for request body parameter %(param)s (expected %(expected_value)s, was %(value)s).'
                ) % {'param': param, 'expected_value': value, 'value': request_data[param]}
