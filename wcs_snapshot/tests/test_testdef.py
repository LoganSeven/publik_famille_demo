import datetime
import decimal
import io
import json
import os
import time
import xml.etree.ElementTree as ET
from unittest import mock

import pytest
import responses
from django.utils.timezone import make_aware, now

from wcs import fields, workflow_tests
from wcs.admin.settings import UserFieldsFormDef
from wcs.admin.tests import TestsAfterJob
from wcs.blocks import BlockDef
from wcs.carddef import CardDef
from wcs.data_sources import NamedDataSource
from wcs.formdef import FormDef
from wcs.qommon.http_request import HTTPRequest
from wcs.qommon.storage import Equal
from wcs.qommon.upload_storage import PicklableUpload
from wcs.testdef import TestDef, TestDefXmlProxy, TestError, TestResults, WebserviceResponse
from wcs.workflows import Workflow, WorkflowVariablesFieldsFormDef
from wcs.wscalls import NamedWsCall

from .utilities import clean_temporary_pub, create_temporary_pub


@pytest.fixture
def pub():
    pub = create_temporary_pub()

    req = HTTPRequest(None, {'SCRIPT_NAME': '/', 'SERVER_NAME': 'example.net'})
    pub.set_app_dir(req)
    pub.write_cfg()

    CardDef.wipe()
    FormDef.wipe()
    BlockDef.wipe()
    WebserviceResponse.wipe()
    NamedWsCall.wipe()
    return pub


def teardown_module(module):
    clean_temporary_pub()


def test_testdef_formdef_wipe(pub):
    formdef = FormDef()
    formdef.name = 'test title'
    formdef.store()

    testdef = TestDef.create_from_formdata(formdef, formdef.data_class()())
    testdef.store()

    test_results = TestResults()
    test_results.object_type = formdef.get_table_name()
    test_results.object_id = formdef.id
    test_results.timestamp = now()
    test_results.reason = ''
    test_results.store()

    carddef = CardDef()
    carddef.name = 'test title'
    carddef.store()

    testdef = TestDef.create_from_formdata(carddef, carddef.data_class()())
    testdef.store()

    test_results = TestResults()
    test_results.object_type = carddef.get_table_name()
    test_results.object_id = carddef.id
    test_results.timestamp = now()
    test_results.reason = ''
    test_results.store()

    assert TestDef.count() == 2
    assert workflow_tests.WorkflowTests.count() == 2
    assert TestResults.count() == 2

    FormDef.wipe()
    assert TestDef.count() == 1
    assert workflow_tests.WorkflowTests.count() == 1
    assert TestResults.count() == 1

    CardDef.wipe()
    assert TestDef.count() == 0
    assert workflow_tests.WorkflowTests.count() == 0
    assert TestResults.count() == 0


def test_testdef_export_to_xml(pub):
    formdef = FormDef()
    formdef.name = 'test title'
    formdef.fields = [
        fields.ItemsField(id='1', label='Test', items=['foo', 'bar', 'baz']),
        fields.BoolField(id='2', label='Check', varname='check'),
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.data['1'] = ['foo', 'baz']
    formdata.data['2'] = True

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.workflow_tests.actions = [
        workflow_tests.AssertStatus(status_name='End status'),
    ]
    testdef.name = 'test'
    testdef.expected_error = 'xxx'
    testdef.store()

    response = WebserviceResponse()
    response.testdef_id = testdef.id
    response.name = 'Fake response'
    response.store()

    testdef_xml = ET.tostring(testdef.export_to_xml())
    TestDef.wipe()
    workflow_tests.WorkflowTests.wipe()
    WebserviceResponse.wipe()

    testdef2 = TestDef.import_from_xml(io.BytesIO(testdef_xml), formdef)
    testdef2.store()
    assert testdef2.name == 'test'
    assert testdef2.object_type == 'formdefs'
    assert testdef2.object_id == str(formdef.id)
    assert testdef2.data == {'fields': {'1': ['foo', 'baz'], '2': True}}
    assert testdef2.expected_error == 'xxx'
    assert testdef2.is_in_backoffice is False

    assert len(testdef2.workflow_tests.actions) == 1
    assert testdef2.workflow_tests.actions[0].status_name == 'End status'

    assert len(testdef2.get_webservice_responses()) == 1
    assert testdef2.get_webservice_responses()[0].name == 'Fake response'

    # check storage of temporary object used during import is forbidden
    testdef_xml = TestDefXmlProxy()
    with pytest.raises(AssertionError):
        testdef_xml.store()


def test_testdef_result_clean(pub, freezer):
    def make_result(formdef_id, success):
        test_results = TestResults()
        test_results.object_type = 'formdefs'
        test_results.object_id = formdef_id
        test_results.timestamp = now()
        test_results.success = success
        test_results.reason = 'xxx'
        test_results.store()

    # FormDef 1
    freezer.move_to('2024-01-25 12:00')
    for i in range(19):
        make_result(formdef_id='1', success=True)

    # add incomplete result
    make_result(formdef_id='1', success=None)

    # FormDef 2
    freezer.move_to('2024-01-10 12:00')
    for i in range(14):
        make_result(formdef_id='2', success=True)

    # add incomplete result
    make_result(formdef_id='2', success=None)

    for i in range(15):
        freezer.move_to('2024-01-15 12:%s' % i)
        make_result(formdef_id='2', success=True)

    # FormDef 3
    freezer.move_to('2024-01-10 12:00')
    for i in range(15):
        make_result(formdef_id='3', success=False)

    freezer.move_to('2024-01-11 12:00')
    make_result(formdef_id='3', success=True)

    freezer.move_to('2024-01-12 12:00')
    for i in range(5):
        make_result(formdef_id='3', success=False)

    freezer.move_to('2024-01-25 12:00')
    for i in range(10):
        make_result(formdef_id='3', success=False)

    freezer.move_to('2024-02-01 12:00')
    TestResults.clean()

    # no deletion for FormDef 1
    results_formdef1 = TestResults.select(clause=[Equal('object_id', '1')])
    assert len(results_formdef1) == 20

    # 10 most recent results were kept for FormDef 2
    results_formdef2 = TestResults.select(clause=[Equal('object_id', '2')])
    assert len(results_formdef2) == 10
    assert all(x.timestamp.day == 15 for x in results_formdef2)

    # all recently failed results were kept for FormDef 3, including last success
    results_formdef3 = TestResults.select(clause=[Equal('object_id', '3')])
    assert len(results_formdef3) == 16
    assert len([x for x in results_formdef3 if x.success]) == 1
    assert len([x for x in results_formdef3 if x.timestamp.day == 12]) == 5
    assert len([x for x in results_formdef3 if x.timestamp.day == 25]) == 10


def test_testdef_create_from_formdata_boolean(pub):
    formdef = FormDef()
    formdef.name = 'test title'
    formdef.fields = [fields.BoolField(id='1', label='Check', varname='check')]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()

    testdef = TestDef.create_from_formdata(formdef, formdata)
    assert testdef.build_formdata(formdef, include_fields=True).data == {}

    formdata.data['1'] = None
    testdef = TestDef.create_from_formdata(formdef, formdata)
    assert testdef.build_formdata(formdef, include_fields=True).data['1'] is None

    formdata.data['1'] = True
    testdef = TestDef.create_from_formdata(formdef, formdata)
    assert testdef.build_formdata(formdef, include_fields=True).data['1'] is True

    formdata.data['1'] = False
    testdef = TestDef.create_from_formdata(formdef, formdata)
    assert testdef.build_formdata(formdef, include_fields=True).data['1'] is False


def test_testdef_create_from_formdata_computed_field(pub):
    formdef = FormDef()
    formdef.name = 'test title'
    formdef.fields = [
        fields.ComputedField(id='1', label='Computed', varname='foo', value_template='{{ 1.2|decimal }}')
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.data['1'] = decimal.Decimal('1.2')

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.store()

    assert testdef.data['fields'] == {}


def test_testdef_create_from_formdata_users_datasource(pub):
    real_user = pub.user_class(name='real user')
    real_user.email = 'real@example.com'
    real_user.store()

    datasource = NamedDataSource(name='foo')
    datasource.data_source = {'type': 'wcs:users'}
    datasource.store()

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.fields = [
        fields.ItemField(id='1', label='Bar', varname='bar', data_source={'type': 'foo'}),
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.data['1'] = str(real_user.id)

    # reference to real user is cleared when creating test
    testdef = TestDef.create_from_formdata(formdef, formdata)
    assert testdef.data['fields']['1'] is None

    real_user.remove_self()
    testdef = TestDef.create_from_formdata(formdef, formdata)
    assert testdef.data['fields']['1'] is None


def test_testdef_create_from_formdata_field_inside_block(pub):
    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.FileField(id='1', label='File', varname='foo', max_file_size='1ko'),
        fields.NumericField(id='2', label='Numeric', varname='numeric'),
    ]
    block.store()

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.fields = [
        fields.BlockField(id='1', label='Block Data', varname='blockdata', block_slug='foobar'),
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()

    upload = PicklableUpload('test.pdf', 'application/pdf', 'ascii')
    upload.receive([b'first line', b'second line'])
    formdata.data['1'] = {'data': [{'1': upload, '2': decimal.Decimal('0')}]}

    testdef = TestDef.create_from_formdata(formdef, formdata)
    assert testdef.data['fields']['1'] == [
        {
            'foo': {
                'content': 'Zmlyc3QgbGluZXNlY29uZCBsaW5l',
                'content_is_base64': True,
                'content_type': 'application/pdf',
                'field_id': '1',
                'filename': 'test.pdf',
            },
            'numeric': '0',
        }
    ]


def test_page_post_conditions(pub):
    formdef = FormDef()
    formdef.name = 'test title'
    formdef.fields = [
        fields.TitleField(id='0', label='Title'),
        fields.PageField(
            id='1',
            label='1st page',
            post_conditions=[
                {'condition': {'type': 'django', 'value': 'form_var_text == "a"'}, 'error_message': 'Error'}
            ],
        ),
        fields.StringField(id='2', label='Text', varname='text'),
        fields.PageField(
            id='3',
            label='2nd page',
            post_conditions=[
                {'condition': {'type': 'django', 'value': 'form_var_text2 == "a"'}, 'error_message': 'Error'}
            ],
        ),
        fields.StringField(id='4', label='Text', varname='text2'),
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.receipt_time = make_aware(datetime.datetime(2021, 1, 1, 0, 0))
    formdata.data['2'] = 'a'
    formdata.data['4'] = 'a'

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.run(formdef)

    formdata.data['4'] = 'b'
    testdef = TestDef.create_from_formdata(formdef, formdata)
    with pytest.raises(TestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'Page 2 post condition was not met (form_var_text2 == "a").'

    formdata.data['2'] = 'b'
    testdef = TestDef.create_from_formdata(formdef, formdata)
    with pytest.raises(TestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'Page 1 post condition was not met (form_var_text == "a").'


def test_page_post_condition_invalid(pub):
    formdef = FormDef()
    formdef.name = 'test title'
    formdef.fields = [
        fields.PageField(
            id='0',
            label='1st page',
            post_conditions=[{'condition': {'type': 'django', 'value': '{{}'}, 'error_message': 'Error'}],
        ),
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.receipt_time = make_aware(datetime.datetime(2021, 1, 1, 0, 0))

    testdef = TestDef.create_from_formdata(formdef, formdata)
    with pytest.raises(TestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'Failed to evaluate page 1 post condition.'


def test_session_variables(pub):
    user = pub.user_class(name='test user')
    user.email = 'test@example.com'
    user.store()

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.fields = [
        fields.PageField(
            id='1',
            label='1st page',
            post_conditions=[
                {
                    'condition': {'type': 'django', 'value': 'session_hash_id|length == 40'},
                    'error_message': 'Error',
                }
            ],
        ),
        fields.PageField(
            id='2',
            label='2nd page',
            post_conditions=[
                {
                    'condition': {'type': 'django', 'value': 'session_user_email == "test@example.com"'},
                    'error_message': 'Error',
                }
            ],
        ),
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.user = user

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.run(formdef)

    formdata.user = None

    testdef = TestDef.create_from_formdata(formdef, formdata)
    with pytest.raises(TestError) as excinfo:
        testdef.run(formdef)
    assert (
        str(excinfo.value) == 'Page 2 post condition was not met (session_user_email == "test@example.com").'
    )


def test_field_conditions(pub):
    formdef = FormDef()
    formdef.name = 'test title'
    formdef.fields = [
        fields.StringField(id='1', label='Text', varname='text'),
        fields.StringField(
            id='2',
            label='Text with condition',
            varname='text_cond',
            required='optional',
            condition={'type': 'django', 'value': 'form_var_text == "a"'},
        ),
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.receipt_time = make_aware(datetime.datetime(2021, 1, 1, 0, 0))
    formdata.data['1'] = 'a'
    formdata.data['2'] = 'xxx'

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.run(formdef)

    formdata.data['1'] = 'b'
    testdef = TestDef.create_from_formdata(formdef, formdata)
    with pytest.raises(TestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'Tried to fill field "Text with condition" but it is hidden.'

    formdata.data['2'] = None
    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.run(formdef)

    formdata.data['1'] = 'a'
    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.run(formdef)


def test_field_conditions_boolean(pub):
    formdef = FormDef()
    formdef.name = 'test title'
    formdef.fields = [
        fields.BoolField(id='1', label='Check', varname='check'),
        fields.StringField(
            id='2',
            label='Text with condition',
            varname='text_cond',
            condition={'type': 'django', 'value': 'form_var_check == True'},
        ),
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.receipt_time = make_aware(datetime.datetime(2021, 1, 1, 0, 0))
    formdata.data['1'] = False
    formdata.data['2'] = None

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.run(formdef)

    formdata.data['2'] = 'xxx'
    testdef = TestDef.create_from_formdata(formdef, formdata)
    with pytest.raises(TestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'Tried to fill field "Text with condition" but it is hidden.'

    formdata.data['1'] = True
    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.run(formdef)


def test_multi_page_condition(pub):
    formdef = FormDef()
    formdef.name = 'test title'
    formdef.fields = [
        fields.PageField(id='0', label='1st page'),
        fields.StringField(id='1', label='Text', varname='text'),
        fields.PageField(
            id='2',
            label='2nd page',
            condition={'type': 'django', 'value': 'form_var_text == "a"'},
            post_conditions=[
                {'condition': {'type': 'django', 'value': 'form_var_text2'}, 'error_message': ''}
            ],
        ),
        fields.StringField(id='3', label='Text 2', varname='text2', required='optional'),
        fields.PageField(id='4', label='3rd page', condition={'type': 'django', 'value': 'form_var_text'}),
        fields.StringField(id='5', label='Text 3', varname='text3'),
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.receipt_time = make_aware(datetime.datetime(2021, 1, 1, 0, 0))
    formdata.data['1'] = 'a'
    formdata.data['3'] = 'xxx'
    formdata.data['5'] = 'yyy'

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.run(formdef)

    formdata.data['1'] = 'b'
    testdef = TestDef.create_from_formdata(formdef, formdata)
    with pytest.raises(TestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'Tried to fill field "Text 2" on page 2 but page was not shown.'

    formdata.data['3'] = None
    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.run(formdef)

    formdata.data['1'] = 'a'
    testdef = TestDef.create_from_formdata(formdef, formdata)
    with pytest.raises(TestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'Page 2 post condition was not met (form_var_text2).'


def test_validation_string_field(pub):
    formdef = FormDef()
    formdef.name = 'test title'
    formdef.fields = [
        fields.StringField(id='1', label='String field digits', validation={'type': 'digits'}),
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.receipt_time = make_aware(datetime.datetime(2021, 1, 1, 0, 0))
    formdata.data['1'] = '1'

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.run(formdef)

    formdata.data['1'] = 'xxx'
    testdef = TestDef.create_from_formdata(formdef, formdata)
    with pytest.raises(TestError) as excinfo:
        testdef.run(formdef)
    assert (
        str(excinfo.value)
        == 'Invalid value "xxx" for field "String field digits": You should enter digits only, for example: 123.'
    )


def test_validation_required_field(pub):
    formdef = FormDef()
    formdef.name = 'test title'
    formdef.fields = [
        fields.StringField(id='1', label='Text', required='optional'),
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.receipt_time = make_aware(datetime.datetime(2021, 1, 1, 0, 0))

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.run(formdef)
    assert testdef.result.missing_required_fields == []

    formdef.fields[0].required = 'required'
    testdef.run(formdef)
    assert testdef.result.missing_required_fields == ['Text']


def test_validation_item_field(pub):
    formdef = FormDef()
    formdef.name = 'test title'
    formdef.fields = [fields.ItemField(id='1', label='Test', items=['foo', 'bar', 'baz'])]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.receipt_time = make_aware(datetime.datetime(2021, 1, 1, 0, 0))
    formdata.data['1'] = 'foo'

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.run(formdef)

    formdata.data['1'] = 'xxx'
    testdef = TestDef.create_from_formdata(formdef, formdata)
    with pytest.raises(TestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'Invalid value "xxx" for field "Test": invalid value selected'

    # no check on invalid value for field with data source
    formdef.fields[0].data_source = {'type': 'jsonvalue', 'value': json.dumps({})}
    formdef.store()

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.run(formdef)

    formdata.data['1'] = None
    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.run(formdef)
    assert testdef.result.missing_required_fields == ['Test']


def test_validation_item_field_inside_block(pub):
    block = BlockDef()
    block.name = 'foobar'
    block.fields = [fields.ItemField(id='1', label='Test', items=['foo', 'bar', 'baz'])]
    block.store()

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.fields = [
        fields.BlockField(
            id='1', label='Block Data', varname='blockdata', block_slug='foobar', max_items='3'
        ),
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.receipt_time = make_aware(datetime.datetime(2021, 1, 1, 0, 0))
    formdata.data['1'] = {'data': [{'1': 'foo'}]}

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.run(formdef)

    formdata.data['1'] = {'data': [{'1': 'xxx'}]}
    testdef = TestDef.create_from_formdata(formdef, formdata)
    with pytest.raises(TestError) as excinfo:
        testdef.run(formdef)
    assert (
        str(excinfo.value) == 'Empty value for field "Test" (of field "Block Data"): invalid value selected'
    )

    # no check on invalid value for field with data source
    block.fields[0].data_source = {'type': 'jsonvalue', 'value': json.dumps({})}
    block.store()
    formdef.refresh_from_storage()

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.run(formdef)

    # ignore required field errors
    formdata.data['1'] = {'data': [{'1': None}]}
    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.run(formdef)
    assert testdef.result.missing_required_fields == ['foobar']


def test_validation_optional_field_inside_required_block(pub):
    block = BlockDef()
    block.name = 'foobar'
    block.fields = [fields.StringField(id='1', label='Test', required='optional')]
    block.store()

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.fields = [
        fields.BlockField(
            id='1', label='Block Data', varname='blockdata', block_slug='foobar', max_items='3'
        ),
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.receipt_time = make_aware(datetime.datetime(2021, 1, 1, 0, 0))
    formdata.data['1'] = {'data': [{'1': 'foo'}]}

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.run(formdef)
    assert testdef.result.missing_required_fields == []

    formdata.data['1'] = {'data': [{'1': None}]}
    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.run(formdef)
    assert testdef.result.missing_required_fields == ['foobar']


def test_item_field_display_value(pub):
    formdef = FormDef()
    formdef.name = 'test title'
    formdef.fields = [
        fields.PageField(
            id='0',
            label='1st page',
            post_conditions=[
                {'condition': {'type': 'django', 'value': 'form_var_item == "foo"'}, 'error_message': ''}
            ],
        ),
        fields.ItemField(id='1', label='Test', items=['foo', 'bar', 'baz'], varname='item'),
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.receipt_time = make_aware(datetime.datetime(2021, 1, 1, 0, 0))
    formdata.data['1'] = 'foo'

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.run(formdef)


def test_item_field_structured_value(pub):
    data_source = {
        'type': 'jsonvalue',
        'value': json.dumps(
            [{'id': '1', 'text': 'un', 'more': 'foo'}, {'id': '2', 'text': 'deux', 'more': 'bar'}]
        ),
    }

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.fields = [
        fields.PageField(
            id='0',
            label='1st page',
            post_conditions=[
                {'condition': {'type': 'django', 'value': 'form_var_item_more == "bar"'}, 'error_message': ''}
            ],
        ),
        fields.ItemField(id='1', label='Test', varname='item', data_source=data_source),
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.receipt_time = make_aware(datetime.datetime(2021, 1, 1, 0, 0))
    formdata.data = {
        '1': '2',
        '1_display': 'deux',
        '1_structured': {'id': '2', 'text': 'deux', 'more': 'bar'},
    }

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.run(formdef)

    # change in data source doesn't affect test
    formdef.fields[1].data_source = {
        'type': 'jsonvalue',
        'value': json.dumps([{'id': '2', 'text': 'deux', 'more': 'foo'}]),
    }
    testdef.run(formdef)

    formdef.fields[1].data_source = data_source = {'type': 'jsonvalue', 'value': json.dumps([])}
    testdef.run(formdef)

    # unavailable remote data source is not called
    formdef.fields[1].data_source = {'type': 'json', 'value': 'https://example.net'}
    with responses.RequestsMock():
        testdef.run(formdef)


def test_item_field_structured_value_inside_block(pub):
    data_source = {
        'type': 'jsonvalue',
        'value': json.dumps(
            [{'id': '1', 'text': 'un', 'more': 'foo'}, {'id': '2', 'text': 'deux', 'more': 'bar'}]
        ),
    }

    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.ItemField(id='1', label='Test', varname='item', data_source=data_source),
    ]
    block.store()

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.fields = [
        fields.PageField(
            id='0',
            label='1st page',
            post_conditions=[
                {
                    'condition': {'type': 'django', 'value': 'form_var_blockdata_0_item_more == "bar"'},
                    'error_message': '',
                }
            ],
        ),
        fields.BlockField(
            id='1', label='Block Data', varname='blockdata', block_slug='foobar', max_items='3'
        ),
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.receipt_time = make_aware(datetime.datetime(2021, 1, 1, 0, 0))
    formdata.data['1'] = {
        'data': [
            {
                '1': '2',
                '1_display': 'deux',
                '1_structured': {'id': '2', 'text': 'deux', 'more': 'bar'},
            }
        ]
    }

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.run(formdef)

    # change in data source doesn't affect test
    formdef.fields[1].block.fields[0].data_source = {
        'type': 'jsonvalue',
        'value': json.dumps([{'id': '2', 'text': 'deux', 'more': 'foo'}]),
    }
    testdef.run(formdef)

    formdef.fields[1].block.fields[0].data_source = {'type': 'jsonvalue', 'value': json.dumps([])}
    testdef.run(formdef)

    # unavailable remote data source is not called
    formdef.fields[1].block.fields[0].data_source = {'type': 'json', 'value': 'https://example.net'}
    with responses.RequestsMock():
        testdef.run(formdef)


def test_item_field_card_data_source_live(pub):
    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'card title'
    carddef.digest_templates = {'default': '{{ form_var_name }}'}
    carddef.fields = [fields.StringField(id='0', label='Name', varname='name')]
    carddef.store()

    carddata = carddef.data_class()()
    carddata.data['0'] = 'xxx'
    carddata.just_created()
    carddata.store()

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.fields = [
        fields.PageField(
            id='0',
            label='1st page',
            post_conditions=[
                {
                    'condition': {'type': 'django', 'value': 'form_var_foo_live_var_name == "abc"'},
                    'error_message': 'Error',
                },
            ],
        ),
        fields.ItemField(
            id='1',
            label='Foo',
            varname='foo',
            data_source={'type': 'carddef:card-title'},
            display_mode='autocomplete',
        ),
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.receipt_time = make_aware(datetime.datetime(2021, 1, 1, 0, 0))
    formdata.data['1'] = str(carddata.id)

    testdef = TestDef.create_from_formdata(formdef, formdata)
    assert testdef.data['fields'] == {'1': 'xxx'}

    with pytest.raises(TestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'Page 1 post condition was not met (form_var_foo_live_var_name == "abc").'

    carddata.data['0'] = 'abc'
    carddata.just_created()
    carddata.store()

    testdef = TestDef.create_from_formdata(formdef, formdata)
    assert testdef.data['fields'] == {'1': 'abc'}

    testdef.run(formdef)

    # legacy access by id
    testdef.data['fields']['1'] = str(carddata.id)
    testdef.run(formdef)


def test_item_field_users_data_source_live(pub):
    user_formdef = UserFieldsFormDef(pub)
    user_formdef.fields = [
        fields.StringField(id='1', label='first_name', varname='first_name'),
    ]
    user_formdef.store()

    test_user = pub.user_class(name='test user 1')
    test_user.email = 'test@example.com'
    test_user.test_uuid = '42'
    test_user.form_data = {'1': 'Test'}
    test_user.store()

    datasource = NamedDataSource(name='foo')
    datasource.data_source = {'type': 'wcs:users'}
    datasource.store()

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.fields = [
        fields.PageField(
            id='0',
            label='1st page',
            post_conditions=[
                {
                    'condition': {
                        'type': 'django',
                        'value': 'form_var_bar_live_var_first_name == "Test 42"',
                    },
                    'error_message': 'Error',
                },
            ],
        ),
        fields.ItemField(id='1', label='Bar', varname='bar', data_source={'type': 'foo'}),
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.data['1'] = str(test_user.id)

    testdef = TestDef.create_from_formdata(formdef, formdata)
    assert testdef.data['fields']['1'] == '42'

    with pytest.raises(TestError):
        testdef.run(formdef)

    test_user.form_data['1'] = 'Test 42'
    test_user.store()

    testdef.run(formdef)

    test_user.remove_self()
    with pytest.raises(TestError):
        testdef.run(formdef)


def test_validation_items_field(pub):
    formdef = FormDef()
    formdef.name = 'test title'
    formdef.fields = [fields.ItemsField(id='1', label='Test', items=['foo', 'bar', 'baz'])]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.receipt_time = make_aware(datetime.datetime(2021, 1, 1, 0, 0))
    formdata.data['1'] = ['foo', 'baz']

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.run(formdef)

    # no check on invalid value
    formdata.data['1'] = ['foo', 'xxx']
    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.run(formdef)

    formdata.data['1'] = []
    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.run(formdef)
    assert testdef.result.missing_required_fields == ['Test']


def test_items_field_card_data_source(pub):
    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'card title'
    carddef.digest_templates = {'default': '{{ form_var_name }}'}
    carddef.fields = [fields.StringField(id='0', label='Name', varname='name')]
    carddef.store()

    carddata = carddef.data_class()()
    carddata.data['0'] = 'xxx'
    carddata.just_created()
    carddata.store()

    carddata2 = carddef.data_class()()
    carddata2.data['0'] = 'yyy'
    carddata2.just_created()
    carddata2.store()

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.fields = [
        fields.PageField(
            id='0',
            label='1st page',
            post_conditions=[
                {
                    'condition': {
                        'type': 'django',
                        'value': 'form_var_foo_live|getlist:"var_name"|join:"," == "xxx,yyy"',
                    },
                    'error_message': 'Error',
                },
            ],
        ),
        fields.ItemsField(
            id='1',
            label='Foo',
            varname='foo',
            data_source={'type': 'carddef:card-title'},
        ),
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.data['1'] = [str(carddata.id), str(carddata2.id)]

    testdef = TestDef.create_from_formdata(formdef, formdata)
    assert testdef.data['fields'] == {'1': ['xxx', 'yyy']}

    testdef.run(formdef)

    # legacy access by id
    testdef.data['fields']['1'] = [str(carddata.id), str(carddata2.id)]
    testdef.run(formdef)


def test_validation_email_field(pub):
    formdef = FormDef()
    formdef.name = 'test title'
    formdef.fields = [
        fields.EmailField(id='1', label='Test'),
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.receipt_time = make_aware(datetime.datetime(2021, 1, 1, 0, 0))
    formdata.data['1'] = 'test@entrouvert.com'

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.run(formdef)

    formdata.data['1'] = 'xxx'
    testdef = TestDef.create_from_formdata(formdef, formdata)
    with pytest.raises(TestError) as excinfo:
        testdef.run(formdef)
    assert (
        str(excinfo.value)
        == 'Invalid value "xxx" for field "Test": You should enter a valid email address, for example name@example.com.'
    )


def test_validation_boolean_field(pub):
    formdef = FormDef()
    formdef.name = 'test title'
    formdef.fields = [
        fields.BoolField(id='1', label='Test'),
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.receipt_time = make_aware(datetime.datetime(2021, 1, 1, 0, 0))
    formdata.data['1'] = False

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.run(formdef)

    formdef.fields[0].required = 'required'
    testdef.run(formdef)
    assert testdef.result.missing_required_fields == ['Test']

    formdata.data['1'] = True
    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.run(formdef)


def test_validation_date_field(pub):
    formdef = FormDef()
    formdef.name = 'test title'
    formdef.fields = [
        fields.DateField(id='1', label='Test'),
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.receipt_time = make_aware(datetime.datetime(2021, 1, 1, 0, 0))
    formdata.data['1'] = time.strptime('2022-07-19', '%Y-%m-%d')

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.run(formdef)

    formdata.data['1'] = time.strptime('1312-01-01', '%Y-%m-%d')
    testdef = TestDef.create_from_formdata(formdef, formdata)
    with pytest.raises(TestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'Invalid value "1312-01-01" for field "Test": You should enter a valid date.'


def test_validation_map_field(pub):
    formdef = FormDef()
    formdef.name = 'test title'
    formdef.fields = [
        fields.PageField(
            id='0',
            label='1st page',
            post_conditions=[
                {
                    'condition': {'type': 'django', 'value': 'form_var_map == "1.0;2.0"'},
                    'error_message': 'Error',
                }
            ],
        ),
        fields.MapField(id='1', label='Map', varname='map'),
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.receipt_time = make_aware(datetime.datetime(2021, 1, 1, 0, 0))
    formdata.data['1'] = '1.0;2.0'

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.run(formdef)


def test_validation_file_field(pub):
    formdef = FormDef()
    formdef.name = 'test title'
    formdef.fields = [
        fields.FileField(id='1', label='File', varname='foo', max_file_size='1ko'),
        fields.StringField(
            id='2',
            label='Text',
            varname='text',
            condition={'type': 'django', 'value': 'form_var_foo == "hop.pdf"'},
        ),
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.receipt_time = make_aware(datetime.datetime(2021, 1, 1, 0, 0))

    upload = PicklableUpload('test.pdf', 'application/pdf', 'ascii')
    upload.receive([b'first line', b'second line'])
    formdata.data['1'] = upload

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.run(formdef)

    # test against empty value
    formdata.data['1'] = None
    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.run(formdef)
    assert testdef.result.missing_required_fields == ['File']

    # test with filename that will negate next field condition
    upload = PicklableUpload('hop.pdf', 'application/pdf', 'ascii')
    upload.receive([b'first line', b'second line'])
    formdata.data['1'] = upload
    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.run(formdef)
    assert testdef.result.missing_required_fields == ['Text']

    formdata.data['2'] = 'xxx'
    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.run(formdef)
    assert testdef.result.missing_required_fields == []

    pub.site_options.set('options', 'blacklisted-file-types', 'application/pdf')
    testdef = TestDef.create_from_formdata(formdef, formdata)
    with pytest.raises(TestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'Invalid value "hop.pdf" for field "File": forbidden file type'


def test_validation_block_field(pub):
    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.StringField(id='1', label='Text', varname='text'),
        fields.StringField(id='2', label='Hop'),
    ]
    block.store()

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.fields = [
        fields.PageField(
            id='0',
            label='1st page',
            post_conditions=[
                {
                    'condition': {'type': 'django', 'value': 'form_var_blockdata_1_text == "a"'},
                    'error_message': '',
                }
            ],
        ),
        fields.BlockField(
            id='1', label='Block Data', varname='blockdata', block_slug='foobar', max_items='3'
        ),
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.receipt_time = make_aware(datetime.datetime(2021, 1, 1, 0, 0))
    formdata.data['1'] = {'data': [{'1': 'b'}, {'1': 'a'}]}

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.run(formdef)
    assert testdef.result.missing_required_fields == ['Hop', 'Hop']

    formdata.data['1'] = {'data': [{'1': 'a', '2': 'z'}, {'1': 'b', '2': 'z'}]}
    testdef = TestDef.create_from_formdata(formdef, formdata)
    with pytest.raises(TestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'Page 1 post condition was not met (form_var_blockdata_1_text == "a").'

    formdata.data['1'] = {'data': [{'1': 'b', '2': 'z'}, {'1': 'a', '2': 'z'}]}
    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.run(formdef)


def test_validation_time_range_field(pub):
    data_source = NamedDataSource(name='free range agenda')
    data_source.slug = 'chrono_ds_free_range_foobar'
    data_source.external = 'agenda'
    data_source.data_source = {
        'type': 'json',
        'value': 'http://chrono.example.net/api/agenda/free-range/datetimes/',
    }
    data_source.store()

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.fields = [
        fields.PageField(
            id='0',
            label='1st page',
            post_conditions=[
                {
                    'condition': {
                        'type': 'django',
                        'value': 'form_var_slot_start_datetime == "2025-06-27 10:30"',
                    },
                    'error_message': 'Error',
                }
            ],
        ),
        fields.TimeRangeField(
            id='1', label='Slot', varname='slot', data_source={'type': 'chrono_ds_free_range_foobar'}
        ),
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data['1'] = {
        'start_datetime': '2025-06-27 10:30',
        'end_datetime': '2025-06-27 11:00',
    }

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.store()

    with pytest.raises(TestError) as excinfo:
        testdef.run(formdef)
    assert 'Invalid value' in str(excinfo.value)

    response = WebserviceResponse()
    response.testdef_id = testdef.id
    response.name = 'Fake response'
    response.url = 'http://chrono.example.net/api/agenda/free-range/datetimes/'
    response.payload = json.dumps(
        {
            'data': [
                {
                    'id': '2025-06-27',
                    'text': 'Fri 27 jui 2025',
                    'opening_hours': [
                        {'hour': '10:30', 'status': 'free'},
                        {'hour': '11:00', 'status': 'closed'},
                    ],
                    'disabled': False,
                },
            ]
        }
    )
    response.store()

    testdef.run(formdef)


def test_computed_field_support(pub):
    formdef = FormDef()
    formdef.name = 'test title'
    formdef.fields = [
        fields.PageField(
            id='0',
            label='1st page',
            post_conditions=[
                {
                    'condition': {
                        'type': 'django',
                        'value': 'form_var_foo == "xxx" and form_var_bar == "hop"',
                    },
                    'error_message': '',
                }
            ],
        ),
        fields.ComputedField(
            id='1',
            label='Computed (frozen)',
            varname='foo',
            value_template='{% firstof form_var_text "xxx" %}',
            freeze_on_initial_value=True,
        ),
        fields.ComputedField(
            id='2', label='Computed (live)', varname='bar', value_template='{% firstof form_var_text "xxx" %}'
        ),
        fields.StringField(id='3', label='Text', varname='text'),
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.receipt_time = make_aware(datetime.datetime(2021, 1, 1, 0, 0))
    formdata.data['1'] = 'zzz'
    formdata.data['3'] = 'hop'

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.run(formdef)

    formdef.fields[1].value_template = '{% for %}'
    testdef = TestDef.create_from_formdata(formdef, formdata)
    with pytest.raises(TestError) as excinfo:
        testdef.run(formdef)
    assert (
        str(excinfo.value)
        == 'Page 1 post condition was not met (form_var_foo == "xxx" and form_var_bar == "hop").'
    )


def test_computed_field_support_complex_data(pub):
    formdef = FormDef()
    formdef.name = 'test title'
    formdef.fields = [
        fields.PageField(
            id='0',
            label='1st page',
            post_conditions=[
                {'condition': {'type': 'django', 'value': 'form_var_foo|length == 3'}, 'error_message': ''}
            ],
        ),
        fields.ComputedField(
            id='1',
            label='Computed',
            varname='foo',
            value_template='{{form_objects|first|get:"form_var_items_raw"}}',
        ),
        fields.ItemsField(id='2', label='Items', varname='items', required='optional'),
    ]
    formdef.store()

    submitted_formdata = formdef.data_class()()
    submitted_formdata.just_created()
    submitted_formdata.receipt_time = make_aware(datetime.datetime(2021, 1, 1, 0, 0))
    submitted_formdata.data['2'] = ['a', 'bc']
    submitted_formdata.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.receipt_time = make_aware(datetime.datetime(2021, 1, 1, 0, 0))

    testdef = TestDef.create_from_formdata(formdef, formdata)
    with pytest.raises(TestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'Page 1 post condition was not met (form_var_foo|length == 3).'

    # access to formdata of current formdef is forbidden
    submitted_formdata.data['2'] = ['a', 'b', 'c']
    submitted_formdata.store()

    with pytest.raises(TestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'Page 1 post condition was not met (form_var_foo|length == 3).'

    # create second formdef
    other_formdef = FormDef()
    other_formdef.name = 'test title 2'
    other_formdef.fields = [
        fields.ItemsField(id='2', label='Items', varname='items', required='optional'),
    ]
    other_formdef.store()

    submitted_formdata = other_formdef.data_class()()
    submitted_formdata.just_created()
    submitted_formdata.data['2'] = ['a', 'b', 'c']
    submitted_formdata.store()

    formdef.fields[1].value_template = '{{forms|objects:"test-title-2"|first|get:"form_var_items_raw"}}'

    testdef.run(formdef)


def test_computed_field_support_webservice(pub, http_requests):
    wscall = NamedWsCall()
    wscall.name = 'Hello world'
    wscall.request = {'url': 'http://remote.example.net/json'}
    wscall.store()

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.fields = [
        fields.PageField(
            id='0',
            label='1st page',
            post_conditions=[
                {
                    'condition': {'type': 'django', 'value': 'form_var_computed_foo == "bar"'},
                    'error_message': '',
                }
            ],
        ),
        fields.ComputedField(
            id='1',
            label='Computed',
            varname='computed',
            value_template='{{ webservice.hello_world }}',
            freeze_on_initial_value=True,
        ),
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.receipt_time = make_aware(datetime.datetime(2021, 1, 1, 0, 0))

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.store()
    testdef.run(formdef)

    assert len(testdef.result.sent_requests) == 1
    assert testdef.result.sent_requests[0]['method'] == 'GET'
    assert testdef.result.sent_requests[0]['url'] == 'http://remote.example.net/json'

    response = WebserviceResponse()
    response.testdef_id = testdef.id
    response.name = 'Fake response'
    response.url = 'http://remote.example.net/json'
    response.payload = '{"foo": "bar"}'
    response.store()

    del testdef.result
    testdef.run(formdef)

    assert len(testdef.result.sent_requests) == 1
    assert testdef.result.sent_requests[0]['url'] == 'http://remote.example.net/json'
    assert testdef.result.sent_requests[0]['webservice_response_id'] == response.id

    response.payload = '{"foo": "baz"}'
    response.store()

    del testdef.result
    with pytest.raises(TestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'Page 1 post condition was not met (form_var_computed_foo == "bar").'
    assert len(testdef.result.sent_requests) == 1
    assert testdef.result.sent_requests[0]['url'] == 'http://remote.example.net/json'
    assert testdef.result.sent_requests[0]['webservice_response_id'] == response.id

    response.url = 'http://example.com/json'
    response.store()

    del testdef.result
    testdef.run(formdef)
    assert len(testdef.result.sent_requests) == 1
    assert testdef.result.sent_requests[0]['url'] == 'http://remote.example.net/json'
    assert testdef.result.sent_requests[0]['webservice_response_id'] is None

    response.url = None
    response.store()

    del testdef.result
    testdef.run(formdef)
    assert len(testdef.result.sent_requests) == 1
    assert testdef.result.sent_requests[0]['url'] == 'http://remote.example.net/json'
    assert testdef.result.sent_requests[0]['webservice_response_id'] is None

    testdef = TestDef.create_from_formdata(formdef, formdata)
    with mock.patch('wcs.testdef.MockWebserviceResponseAdapter._send', side_effect=KeyError('missing key')):
        with pytest.raises(TestError):
            testdef.run(formdef)

    assert len(testdef.result.sent_requests) == 0
    assert testdef.result.recorded_errors == [
        "Unexpected error when mocking webservice call for url http://remote.example.net/json: 'missing key'."
    ]


def test_computed_field_value_too_long(pub):
    formdef = FormDef()
    formdef.name = 'test title'
    formdef.fields = [
        fields.PageField(
            id='0',
            label='1st page',
            post_conditions=[
                {
                    'condition': {'type': 'django', 'value': 'not form_var_computed'},
                    'error_message': '',
                }
            ],
        ),
        fields.ComputedField(
            id='1',
            label='Computed',
            varname='computed',
            value_template='{% token_decimal length=9999 %}',
        ),
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.receipt_time = make_aware(datetime.datetime(2021, 1, 1, 0, 0))

    testdef = TestDef.create_from_formdata(formdef, formdata)
    with pytest.raises(TestError):
        testdef.run(formdef)

    formdef.fields[1].value_template = '{% token_decimal length=100000 %}'
    formdef.store()

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.run(formdef)


def test_computed_field_forms_template_access(pub):
    formdef = FormDef()
    formdef.name = 'test title'
    formdef.fields = [
        fields.PageField(
            id='0',
            label='1st page',
            post_conditions=[
                {
                    'condition': {'type': 'django', 'value': 'form_var_computed == 1'},
                    'error_message': 'Not enough chars.',
                }
            ],
        ),
        fields.ComputedField(
            id='1',
            label='Computed',
            varname='computed',
            value_template='{{ forms|objects:"test-title-2"|count }}',
            freeze_on_initial_value=True,
        ),
    ]
    formdef.store()

    formdef2 = FormDef()
    formdef2.name = 'test title 2'
    formdef2.store()

    formdata = formdef2.data_class()()
    formdata.just_created()
    formdata.store()

    testdef = TestDef.create_from_formdata(formdef, formdef.data_class()())
    testdef.run(formdef)

    formdef.fields[1].value_template = (
        '{{ forms|objects:"test-title"|filter_by:"unknown"|filter_value:"xxx"|count }}'
    )
    formdef.store()

    testdef = TestDef.create_from_formdata(formdef, formdata)
    with pytest.raises(TestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'Page 1 post condition was not met (form_var_computed == 1).'
    assert testdef.result.recorded_errors == ['Invalid filter "unknown"']


def test_computed_field_query_parameters(pub):
    formdef = FormDef()
    formdef.name = 'test title'
    formdef.fields = [
        fields.PageField(
            id='0',
            label='1st page',
            post_conditions=[
                {
                    'condition': {'type': 'django', 'value': 'form_var_computed == "abc"'},
                    'error_message': 'Not enough chars.',
                }
            ],
        ),
        fields.ComputedField(
            id='1',
            label='Computed',
            varname='computed',
            value_template='{{ request.GET.param1 }}',
            freeze_on_initial_value=True,
        ),
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()

    testdef = TestDef.create_from_formdata(formdef, formdata)
    with pytest.raises(TestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'Page 1 post condition was not met (form_var_computed == "abc").'

    testdef.query_parameters = {'param1': 'abc'}
    testdef.run(formdef)

    formdef.fields.extend(
        [
            fields.PageField(
                id='2',
                label='2nd page',
                post_conditions=[
                    {
                        'condition': {'type': 'django', 'value': 'form_var_computed_2 == "abc"'},
                        'error_message': 'Not enough chars.',
                    }
                ],
            ),
            fields.ComputedField(
                id='3',
                label='Computed 2',
                varname='computed_2',
                value_template='{{ request.GET.param1 }}',
                freeze_on_initial_value=True,
            ),
        ]
    )
    formdef.store()

    with pytest.raises(TestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'Page 2 post condition was not met (form_var_computed_2 == "abc").'

    # hide first page
    formdef.fields[0].condition = {'type': 'django', 'value': 'False'}
    formdef.store()

    testdef.run(formdef)


def test_numeric_field_support(pub):
    formdef = FormDef()
    formdef.name = 'test title'
    formdef.fields = [
        fields.PageField(
            id='0',
            label='1st page',
            post_conditions=[
                {'condition': {'type': 'django', 'value': 'form_var_foo == 13.12'}, 'error_message': ''}
            ],
        ),
        fields.NumericField(
            id='1', label='Numeric', varname='foo', restrict_to_integers=False, min_value=decimal.Decimal(10)
        ),
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.data['1'] = decimal.Decimal(13.12)

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.store()
    testdef.run(formdef)

    formdata.data['1'] = decimal.Decimal(9)
    testdef = TestDef.create_from_formdata(formdef, formdata)

    with pytest.raises(TestError) as excinfo:
        testdef.run(formdef)
    assert (
        str(excinfo.value)
        == 'Invalid value "9" for field "Numeric": You should enter a number greater than or equal to 10.'
    )

    formdata.data['1'] = decimal.Decimal(42)
    testdef = TestDef.create_from_formdata(formdef, formdata)

    with pytest.raises(TestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'Page 1 post condition was not met (form_var_foo == 13.12).'


def test_expected_error(pub):
    formdef = FormDef()
    formdef.name = 'test title'
    formdef.fields = [
        fields.PageField(
            id='0',
            label='1st page',
            post_conditions=[
                {
                    'condition': {'type': 'django', 'value': 'form_var_text|length > 5'},
                    'error_message': 'Not enough chars.',
                }
            ],
        ),
        fields.StringField(id='1', label='Text', varname='text', validation={'type': 'digits'}),
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.receipt_time = make_aware(datetime.datetime(2021, 1, 1, 0, 0))
    formdata.data['1'] = '123456'

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.run(formdef)

    formdata.data['1'] = '1'
    testdef = TestDef.create_from_formdata(formdef, formdata)
    with pytest.raises(TestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'Page 1 post condition was not met (form_var_text|length > 5).'

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.expected_error = 'Not enough chars.'
    testdef.run(formdef)

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.expected_error = 'Other error.'
    with pytest.raises(TestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'Expected error "Other error." but got error "Not enough chars." instead.'

    formdata.data['1'] = 'abcdef'
    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.expected_error = 'You should enter digits only, for example: 123.'
    testdef.run(formdef)


def test_expected_error_conditional_field(pub):
    formdef = FormDef()
    formdef.name = 'test title'
    formdef.fields = [
        fields.StringField(id='1', label='Text', varname='text'),
        fields.StringField(
            id='2',
            label='Text 2',
            varname='text2',
            validation={'type': 'digits'},
            condition={'type': 'django', 'value': 'form_var_text == "a"'},
        ),
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.receipt_time = make_aware(datetime.datetime(2021, 1, 1, 0, 0))
    formdata.data['1'] = 'a'
    formdata.data['2'] = 'b'

    testdef = TestDef.create_from_formdata(formdef, formdata)
    with pytest.raises(TestError) as excinfo:
        testdef.run(formdef)
    assert (
        str(excinfo.value)
        == 'Invalid value "b" for field "Text 2": You should enter digits only, for example: 123.'
    )

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.expected_error = 'You should enter digits only, for example: 123.'
    testdef.run(formdef)

    formdata.data['1'] = 'b'
    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.expected_error = 'You should enter digits only, for example: 123.'
    with pytest.raises(TestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == (
        'Expected error "You should enter digits only, for example: 123." but got error "Tried to fill field "Text 2" '
        'but it is hidden." instead.'
    )


def test_expected_error_templated_string(pub):
    formdef = FormDef()
    formdef.name = 'test title'
    formdef.fields = [
        fields.PageField(
            id='0',
            label='1st page',
            post_conditions=[
                {
                    'condition': {'type': 'django', 'value': 'form_var_text|length > 5'},
                    'error_message': 'Number is too short: only {{ form_var_text|length }} digits.',
                }
            ],
        ),
        fields.StringField(id='1', label='Text', varname='text', validation={'type': 'digits'}),
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data['1'] = '123456'

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.run(formdef)

    formdata.data['1'] = '12'
    testdef = TestDef.create_from_formdata(formdef, formdata)
    with pytest.raises(TestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'Page 1 post condition was not met (form_var_text|length > 5).'

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.expected_error = 'Number is too short: only 2 digits.'
    testdef.run(formdef)


def test_is_in_backoffice(pub):
    formdef = FormDef()
    formdef.name = 'test title'
    formdef.fields = [
        fields.PageField(
            id='0',
            label='1st page',
            post_conditions=[
                {
                    'condition': {'type': 'django', 'value': 'not is_in_backoffice'},
                    'error_message': 'Must not be in backoffice',
                }
            ],
        ),
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.receipt_time = make_aware(datetime.datetime(2021, 1, 1, 0, 0))

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.run(formdef)

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.is_in_backoffice = True
    with pytest.raises(TestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'Page 1 post condition was not met (not is_in_backoffice).'

    testdef.is_in_backoffice = False
    testdef.run(formdef)


def test_testdef_submission_agent(pub):
    test_user = pub.user_class(name='test user 1')
    test_user.email = 'test@example.com'
    test_user.test_uuid = '42'
    test_user.store()

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.fields = [
        fields.PageField(
            id='0',
            label='1st page',
            post_conditions=[
                {
                    'condition': {
                        'type': 'django',
                        'value': 'form_submission_agent_email == "test@example.com"',
                    },
                    'error_message': 'Error',
                }
            ],
        ),
    ]
    formdef.store()

    testdef = TestDef.create_from_formdata(formdef, formdef.data_class()())
    with pytest.raises(TestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value).startswith('Page 1 post condition was not met')

    testdef.submission_agent_uuid = test_user.test_uuid
    testdef.run(formdef)


def test_webservice_response_match_request(pub, http_requests):
    formdef = FormDef()
    formdef.name = 'test title'
    formdef.fields = [
        fields.ComputedField(
            id='1',
            label='Computed',
            varname='computed',
            value_template='{{ webservice.hello_world }}',
            freeze_on_initial_value=True,
        ),
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.store()

    wscall = NamedWsCall()
    wscall.name = 'Hello world'
    wscall.request = {
        'url': 'http://remote.example.net/json',
        'method': 'POST',
        'qs_data': {'foo': 'bar'},
        'post_data': {
            'foo2': 'bar2',
            'foo3': '{{ 42 }}',
        },
    }
    wscall.store()

    response = WebserviceResponse()
    response.testdef_id = testdef.id
    response.name = 'Fake response'
    response.url = 'http://remote.example.net/json'
    response.payload = '{}'
    response.store()

    testdef.run(formdef)
    assert testdef.result.sent_requests[0]['webservice_response_id'] == response.id

    if not pub.site_options.has_section('variables'):
        pub.site_options.add_section('variables')
    pub.site_options.set('variables', 'portal_url', 'http://remote.example.net/')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)

    response.url = '{{ portal_url }}json'
    response.store()

    testdef.run(formdef)
    assert testdef.result.sent_requests[0]['webservice_response_id'] == response.id

    # method restriction
    response.method = 'GET'
    response.store()

    del testdef.result
    testdef.run(formdef)
    assert testdef.result.sent_requests[0]['webservice_response_id'] is None
    assert testdef.result.sent_requests[0]['response_mismatch_reasons'] == {
        '1': 'Method does not match (expected GET, was POST).'
    }

    response.method = 'POST'
    response.store()

    del testdef.result
    testdef.run(formdef)
    assert testdef.result.sent_requests[0]['webservice_response_id'] == response.id

    # query string restriction
    response.qs_data = {
        'foo': 'zzz',
        'xxx': 'yyy',
    }
    response.store()

    del testdef.result
    testdef.run(formdef)
    assert testdef.result.sent_requests[0]['webservice_response_id'] is None
    assert testdef.result.sent_requests[0]['response_mismatch_reasons'] == {
        '1': 'Wrong value for query string parameter foo (expected zzz, was bar).'
    }

    response.qs_data['foo'] = 'bar'
    response.store()

    del testdef.result
    testdef.run(formdef)
    assert testdef.result.sent_requests[0]['webservice_response_id'] is None
    assert testdef.result.sent_requests[0]['response_mismatch_reasons'] == {
        '1': 'Expected parameter xxx not found in query string.'
    }

    del response.qs_data['xxx']
    response.store()

    del testdef.result
    testdef.run(formdef)
    assert testdef.result.sent_requests[0]['webservice_response_id'] == response.id

    # post data restriction
    response.post_data = {
        'foo2': 'zzz',
        'xxx': 'yyy',
    }
    response.store()

    del testdef.result
    testdef.run(formdef)
    assert testdef.result.sent_requests[0]['webservice_response_id'] is None
    assert testdef.result.sent_requests[0]['response_mismatch_reasons'] == {
        '1': 'Wrong value for request body parameter foo2 (expected zzz, was bar2).'
    }

    response.post_data['foo2'] = 'bar2'
    response.store()

    del testdef.result
    testdef.run(formdef)
    assert testdef.result.sent_requests[0]['webservice_response_id'] is None
    assert testdef.result.sent_requests[0]['response_mismatch_reasons'] == {
        '1': 'Expected parameter xxx not found in request body.'
    }

    del response.post_data['xxx']
    response.store()

    del testdef.result
    testdef.run(formdef)
    assert testdef.result.sent_requests[0]['webservice_response_id'] == response.id

    response.post_data = {
        'foo3': '42',
    }
    response.store()

    del testdef.result
    testdef.run(formdef)
    assert testdef.result.sent_requests[0]['webservice_response_id'] is None

    response.post_data = {
        'foo3': '{{ 42 }}',
    }
    response.store()

    del testdef.result
    testdef.run(formdef)
    assert testdef.result.sent_requests[0]['webservice_response_id'] == response.id


def test_frozen_submission_datetime(pub):
    formdef = FormDef()
    formdef.name = 'test title'
    formdef.fields = [
        fields.PageField(
            id='0',
            label='1st page',
            post_conditions=[
                {
                    'condition': {'type': 'django', 'value': 'now < "2018-11-17"|date'},
                    'error_message': 'Too old',
                }
            ],
        ),
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.receipt_time = make_aware(datetime.datetime(2021, 1, 1, 0, 0))

    testdef = TestDef.create_from_formdata(formdef, formdata)
    assert testdef.frozen_submission_datetime == formdata.receipt_time

    with pytest.raises(TestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'Page 1 post condition was not met (now < "2018-11-17"|date).'

    testdef.frozen_submission_datetime = make_aware(datetime.datetime(2018, 1, 1, 0, 0))
    testdef.run(formdef)


def test_testdef_workflow_options(pub):
    workflow = Workflow(name='Workflow One')
    workflow.add_status(name='New status')
    workflow.variables_formdef = WorkflowVariablesFieldsFormDef(workflow=workflow)
    workflow.variables_formdef.fields = [
        fields.StringField(id='1', label='Foo', varname='foo'),
    ]
    workflow.store()

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.workflow_id = workflow.id
    formdef.fields = [
        fields.PageField(
            id='0',
            label='1st page',
            post_conditions=[
                {
                    'condition': {'type': 'django', 'value': 'form_option_foo == "abc"'},
                    'error_message': 'Error',
                }
            ],
        ),
    ]
    formdef.store()

    testdef = TestDef.create_from_formdata(formdef, formdef.data_class()())

    with pytest.raises(TestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'Page 1 post condition was not met (form_option_foo == "abc").'

    testdef.workflow_options = {'1': 'abc'}
    testdef.run(formdef)

    formdef.workflow_options = {'foo': 'abc'}
    testdef.workflow_options = None
    testdef.run(formdef)

    testdef.workflow_options = {'1': 'def'}
    with pytest.raises(TestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'Page 1 post condition was not met (form_option_foo == "abc").'

    workflow.variables_formdef = None
    workflow.store()

    with pytest.raises(TestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'Page 1 post condition was not met (form_option_foo == "abc").'


def test_testdef_workflow_options_card_dependency(pub):
    workflow = Workflow(name='Card Workflow')
    workflow.add_status(name='New status')
    workflow.variables_formdef = WorkflowVariablesFieldsFormDef(workflow=workflow)
    workflow.variables_formdef.fields = [fields.StringField(id='1', label='Foo', varname='foo')]
    workflow.store()

    carddef = CardDef()
    carddef.name = 'test dependency'
    carddef.workflow_id = workflow.id
    carddef.digest_templates = {'default': '{{ form_var_name }}'}
    carddef.fields = [
        fields.StringField(
            id='1',
            label='Name',
            varname='name',
            condition={'type': 'django', 'value': 'form_option_foo == "bla"'},
        )
    ]
    carddef.workflow_options = {'foo': 'bla'}
    carddef.store()

    carddata = carddef.data_class()()
    carddata.just_created()
    carddata.data = {'1': 'abc'}

    dependency_testdef = TestDef.create_from_formdata(carddef, carddata)
    dependency_testdef.store()

    workflow = Workflow(name='Workflow One')
    workflow.add_status(name='New status')
    workflow.variables_formdef = WorkflowVariablesFieldsFormDef(workflow=workflow)
    workflow.variables_formdef.fields = [
        fields.ItemField(
            id='1',
            label='Foo',
            varname='foo',
            data_source={'type': 'carddef:test-dependency'},
        ),
    ]
    workflow.store()

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.workflow_id = workflow.id
    formdef.fields = [
        fields.PageField(
            id='0',
            label='1st page',
            post_conditions=[
                {
                    'condition': {'type': 'django', 'value': 'form_option_foo == "abc"'},
                    'error_message': 'Error',
                }
            ],
        ),
    ]
    formdef.store()

    testdef = TestDef.create_from_formdata(formdef, formdef.data_class()())
    testdef.name = 'First test'
    testdef.dependencies = [dependency_testdef.uuid]
    testdef.store()

    TestsAfterJob.run_tests(formdef, reason='xxx')

    assert TestResults.count() == 1

    test_results = TestResults.select()[0]
    assert test_results.success is False
    assert len(test_results.results) == 1

    result = test_results.results[0]
    assert result.error == 'Page 1 post condition was not met (form_option_foo == "abc").'

    testdef.workflow_options = {
        '1': 'abc',
        '1_display': 'abc',
        '1_structured': {'id': 1, 'name': 'abc', 'text': 'abc'},
    }
    testdef.store()

    TestResults.wipe()
    TestsAfterJob.run_tests(formdef, reason='xxx')

    assert TestResults.count() == 1

    test_results = TestResults.select()[0]
    assert test_results.success is True
    assert len(test_results.results) == 1


def test_testdef_dependencies(pub):
    formdef = FormDef()
    formdef.name = 'test dependency'
    formdef.store()

    dependency_testdef = TestDef.create_from_formdata(formdef, formdef.data_class()())
    dependency_testdef.store()

    formdef = FormDef()
    formdef.name = 'test dependent'
    formdef.fields = [
        fields.PageField(
            id='0',
            label='1st page',
            post_conditions=[
                {
                    'condition': {'type': 'django', 'value': 'forms|objects:"test-dependency"|count == 1'},
                    'error_message': 'Missing form dependency',
                }
            ],
        ),
    ]
    formdef.store()

    testdef = TestDef.create_from_formdata(formdef, formdef.data_class()())
    testdef.store()

    TestsAfterJob.run_tests(formdef, reason='xxx')

    assert TestResults.count() == 1

    test_results = TestResults.select()[0]
    assert test_results.success is False
    assert len(test_results.results) == 1

    result = test_results.results[0]
    assert result.error == 'Page 1 post condition was not met (forms|objects:"test-dependency"|count == 1).'

    testdef.dependencies = [dependency_testdef.uuid]
    testdef.store()

    TestResults.wipe()
    TestsAfterJob.run_tests(formdef, reason='xxx')

    assert TestResults.count() == 1

    test_results = TestResults.select()[0]
    assert test_results.success is True
    assert len(test_results.results) == 1

    dependency_testdef.expected_error = 'xxx'
    dependency_testdef.store()

    TestResults.wipe()
    TestsAfterJob.run_tests(formdef, reason='xxx')

    assert TestResults.count() == 1

    test_results = TestResults.select()[0]
    assert test_results.success is False
    assert (
        test_results.results[0].error
        == 'Error in dependency: Expected error "xxx" but test completed with success.'
    )

    TestDef.remove_object(dependency_testdef.id)

    TestResults.wipe()
    TestsAfterJob.run_tests(formdef, reason='xxx')

    test_results = TestResults.select()[0]
    assert test_results.success is False
    assert test_results.results[0].error == 'Missing test dependency.'


def test_testdef_dependencies_chain(pub):
    formdef = FormDef()
    formdef.name = 'dependency 1'
    formdef.store()

    dependency1_testdef = TestDef.create_from_formdata(formdef, formdef.data_class()())
    dependency1_testdef.store()

    formdef = FormDef()
    formdef.name = 'dependency 2'
    formdef.store()

    dependency2_testdef = TestDef.create_from_formdata(formdef, formdef.data_class()())
    dependency2_testdef.store()

    dependency3_testdef = TestDef.create_from_formdata(formdef, formdef.data_class()())
    dependency3_testdef.store()

    formdef = FormDef()
    formdef.name = 'test dependent'
    formdef.fields = [
        fields.PageField(
            id='0',
            label='1st page',
            post_conditions=[
                {
                    'condition': {'type': 'django', 'value': 'forms|objects:"dependency-1"|count == 1'},
                    'error_message': 'Missing form dependency 1',
                },
                {
                    'condition': {'type': 'django', 'value': 'forms|objects:"dependency-2"|count == 2'},
                    'error_message': 'Missing form dependency 2',
                },
            ],
        ),
    ]
    formdef.store()

    testdef = TestDef.create_from_formdata(formdef, formdef.data_class()())
    testdef.dependencies = [dependency1_testdef.uuid]
    testdef.store()

    TestsAfterJob.run_tests(formdef, reason='xxx')

    test_results = TestResults.select()[0]
    assert test_results.success is False
    assert (
        test_results.results[0].error
        == 'Page 1 post condition was not met (forms|objects:"dependency-2"|count == 2).'
    )

    dependency1_testdef.dependencies = [dependency2_testdef.uuid, dependency3_testdef.uuid]
    dependency1_testdef.store()

    TestResults.wipe()
    TestsAfterJob.run_tests(formdef, reason='xxx')

    test_results = TestResults.select()[0]
    assert test_results.success is True
    assert len(test_results.results) == 1

    # add loop
    dependency3_testdef.dependencies = [dependency1_testdef.uuid]
    dependency3_testdef.store()

    TestResults.wipe()
    TestsAfterJob.run_tests(formdef, reason='xxx')

    test_results = TestResults.select()[0]
    assert test_results.success is False
    assert test_results.results[0].error == 'Error in dependency: Loop in dependencies.'

    # add same test twice, but no loop
    testdef.dependencies = [dependency1_testdef.uuid, dependency2_testdef.uuid]
    testdef.store()

    dependency1_testdef.dependencies = [dependency2_testdef.uuid]
    dependency1_testdef.store()

    TestResults.wipe()
    TestsAfterJob.run_tests(formdef, reason='xxx')

    test_results = TestResults.select()[0]
    assert test_results.success is True
    assert len(test_results.results) == 1


def test_testdef_dependencies_card_datasource(pub):
    carddef = CardDef()
    carddef.name = 'test dependency'
    carddef.digest_templates = {'default': '{{ form_var_name }}'}
    carddef.fields = [fields.StringField(id='1', label='Name', varname='name')]
    carddef.store()

    carddata = carddef.data_class()()
    carddata.just_created()
    carddata.data = {'1': 'abc'}

    dependency_testdef = TestDef.create_from_formdata(carddef, carddata)
    dependency_testdef.store()

    formdef = FormDef()
    formdef.name = 'test dependent'
    formdef.fields = [
        fields.PageField(
            id='0',
            label='1st page',
            post_conditions=[
                {
                    'condition': {'type': 'django', 'value': 'form_var_foo_live_var_name == "abc"'},
                    'error_message': 'xxx',
                }
            ],
        ),
        fields.ItemField(
            id='1',
            label='Foo',
            varname='foo',
            data_source={'type': 'carddef:test-dependency'},
        ),
    ]
    formdef.store()

    testdef = TestDef.create_from_formdata(formdef, formdef.data_class()())
    testdef.store()

    TestsAfterJob.run_tests(formdef, reason='xxx')

    test_results = TestResults.select()[0]
    assert test_results.success is False
    assert test_results.results[0].error.startswith('Page 1 post condition was not met')

    testdef.data = {'fields': {'1': 'abc'}}
    testdef.store()

    TestResults.wipe()
    TestsAfterJob.run_tests(formdef, reason='xxx')

    test_results = TestResults.select()[0]
    assert test_results.success is False
    assert test_results.results[0].error.startswith('Page 1 post condition was not met')

    testdef.dependencies = [dependency_testdef.uuid]
    testdef.store()

    TestResults.wipe()
    TestsAfterJob.run_tests(formdef, reason='xxx')

    test_results = TestResults.select()[0]
    assert test_results.success is True


def test_testdef_dependencies_user_filter(pub):
    user = pub.user_class(name='test user 1')
    user.email = 'test@example.com'
    user.store()

    carddef = CardDef()
    carddef.name = 'test dependency'
    carddef.digest_templates = {'default': '{{ form_var_name }}'}
    carddef.fields = [fields.StringField(id='1', label='Name', varname='name')]
    carddef.store()

    carddata = carddef.data_class()()
    carddata.just_created()
    carddata.data = {'1': 'abc'}
    carddata.user = user

    dependency_testdef = TestDef.create_from_formdata(carddef, carddata)
    dependency_testdef.store()

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.fields = [
        fields.StringField(
            id='1',
            label='Test',
            condition={'type': 'django', 'value': 'cards|objects:"test-dependency"|current_user|count'},
        ),
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.user = user
    formdata.data['1'] = 'foo'

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.name = 'Current test'
    testdef.dependencies = [dependency_testdef.uuid]
    testdef.store()

    TestsAfterJob.run_tests(formdef, reason='xxx')

    test_results = TestResults.select()[0]
    assert test_results.success is True
