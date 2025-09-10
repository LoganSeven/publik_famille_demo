import datetime
import decimal
import os
import time
from unittest import mock

import pytest
from django.utils.html import escape
from django.utils.timezone import make_aware
from webtest import Upload

from wcs import fields, workflow_tests
from wcs.admin.settings import UserFieldsFormDef
from wcs.blocks import BlockDef
from wcs.carddef import CardDef
from wcs.data_sources import NamedDataSource
from wcs.formdef import FormDef
from wcs.qommon.afterjobs import AfterJob
from wcs.qommon.http_request import HTTPRequest
from wcs.qommon.upload_storage import PicklableUpload
from wcs.testdef import TestDef, TestResults, WebserviceResponse
from wcs.wf.create_formdata import Mapping
from wcs.wf.form import WorkflowFormFieldsFormDef
from wcs.workflow_tests import WorkflowTests
from wcs.workflows import Workflow, WorkflowBackofficeFieldsFormDef, WorkflowVariablesFieldsFormDef
from wcs.wscalls import NamedWsCall

from ..utilities import clean_temporary_pub, create_temporary_pub, get_app, login
from .test_all import create_superuser


@pytest.fixture
def pub():
    pub = create_temporary_pub()

    req = HTTPRequest(None, {'SCRIPT_NAME': '/', 'SERVER_NAME': 'example.net'})
    pub.set_app_dir(req)
    pub.cfg['identification'] = {'methods': ['password']}
    pub.write_cfg()

    pub.user_class.wipe()
    pub.test_user_class.wipe()
    BlockDef.wipe()
    CardDef.wipe()
    FormDef.wipe()
    WebserviceResponse.wipe()
    NamedWsCall.wipe()
    return pub


def teardown_module(module):
    clean_temporary_pub()


def test_tests_page(pub):
    create_superuser(pub)

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.fields = [
        fields.StringField(id='1', label='Text', varname='text'),
    ]
    formdef.store()

    app = login(get_app(pub))
    resp = app.get(formdef.get_admin_url())
    resp = resp.click('Tests')
    assert 'There are no tests yet.' in resp.text

    resp = resp.click('New')
    resp.form['name'] = 'First test'
    resp = resp.form.submit()

    users = pub.test_user_class.select()
    assert len(users) == 0

    resp = resp.follow()
    assert 'Edit test data' in resp.text

    resp.form['f1'] = 'abcdefg'
    resp = resp.form.submit('submit')
    assert resp.location == 'http://example.net/backoffice/forms/1/tests/1/'

    resp = resp.follow()
    assert 'First test' in resp.text
    assert 'abcdefg' in resp.text
    assert 'This test is empty' not in resp.text

    resp = app.get('/backoffice/forms/1/tests/')
    assert 'First test' in resp.text
    assert 'no tests yet' not in resp.text

    resp = resp.click('New')
    resp.form['name'] = 'A second test'
    # submit but skip redirection to edit page
    resp.form.submit()

    resp = app.get('/backoffice/forms/1/tests/')
    assert resp.text.index('A second test') < resp.text.index('First test')

    resp = resp.click('A second test')
    assert 'This test is empty' in resp.text

    resp = resp.click('History')
    assert 'Creation (empty)' in resp.text

    # test run with empty test is allowed
    app.get('/backoffice/forms/1/tests/results/run').follow()


def test_tests_page_breadcrumb(pub):
    create_superuser(pub)

    formdef = FormDef()
    formdef.name = 'Short title'
    formdef.store()

    app = login(get_app(pub))

    resp = app.get(formdef.get_admin_url() + 'tests/')
    assert 'Short title' in resp.text

    formdef.name = 'This is a long title'
    formdef.store()

    resp = app.get(formdef.get_admin_url() + 'tests/')
    assert 'This is a long…' in resp.text


def test_tests_page_creation_from_formdata(pub):
    user = create_superuser(pub)

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.fields = [
        fields.StringField(id='1', label='Text', varname='text'),
    ]
    formdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/1/tests/new')
    assert 'creation_mode' not in resp.form.fields
    assert 'formdata' not in resp.form.fields

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.receipt_time = make_aware(datetime.datetime(2021, 1, 1, 0, 0))
    formdata.data['1'] = 'abcdefg'
    formdata.user_id = user.id
    formdata.store()

    resp = app.get('/backoffice/forms/%s/tests/new' % formdef.id)
    resp.form['name'] = 'First test'
    resp.form['creation_mode'] = 'formdata'
    resp.form['formdata'].select(text='1-1 - admin - 2021-01-01 00:00')
    resp = resp.form.submit().follow()
    assert 'First test' in resp.text
    assert 'abcdefg' in resp.text

    users = pub.test_user_class.select()
    assert len(users) == 1
    test_user = users[0]

    testdef = TestDef.select()[0]
    assert testdef.user_uuid == test_user.test_uuid
    assert testdef.agent_id is None
    assert not testdef.is_in_backoffice
    assert testdef.frozen_submission_datetime == formdata.receipt_time

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.receipt_time = make_aware(datetime.datetime(2022, 1, 1, 0, 0))
    formdata.data['1'] = 'hijklmn'
    formdata.backoffice_submission = True
    formdata.store()

    resp = app.get('/backoffice/forms/1/tests/new')
    resp.form['name'] = 'Second test'
    resp.form['creation_mode'] = 'formdata'
    resp.form['formdata'].select(text='1-2 - Unknown User - 2022-01-01 00:00')
    resp = resp.form.submit().follow()
    assert 'Second test' in resp.text
    assert 'hijklmn' in resp.text

    testdef = TestDef.select()[1]
    assert not testdef.user_uuid
    assert testdef.is_in_backoffice


def test_tests_page_deprecated_fields(pub):
    formdef = FormDef()
    formdef.name = 'test title'
    formdef.fields = [
        fields.TableField(id='1', label='Table'),
    ]
    formdef.store()

    create_superuser(pub)
    app = login(get_app(pub))

    resp = app.get(formdef.get_admin_url())
    resp = resp.click('Tests')
    assert 'Run' not in resp.text
    assert 'deprecated fields' in resp.text


def test_tests_import_export(pub):
    user = create_superuser(pub)

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.fields = [fields.StringField(id='1', varname='test field', label='Test')]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.receipt_time = make_aware(datetime.datetime(2021, 1, 1, 0, 0))
    formdata.data['1'] = 'a'
    formdata.user_id = user.id
    formdata.store()

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.workflow_tests.actions = [
        workflow_tests.ButtonClick(id='1', button_name='Go to end status'),
        workflow_tests.AssertStatus(id='2', status_name='End status'),
    ]
    testdef.name = 'First test'
    testdef.store()

    response = WebserviceResponse()
    response.testdef_id = testdef.id
    response.name = 'Response xxx'
    response.store()

    app = login(get_app(pub))

    resp = app.get('/backoffice/forms/1/tests/%s/' % testdef.id)
    export_resp = resp.click('Export')
    assert 'filename=test-first-test.wcs' in export_resp.headers['content-disposition']

    resp = resp.click('Delete')
    resp = resp.form.submit().follow()
    assert 'First test' not in resp.text
    assert WorkflowTests.count() == 0
    assert WebserviceResponse.count() == 0

    resp = resp.click('Import')
    resp.form['file'] = Upload('export.wcs', export_resp.body)
    resp = resp.form.submit().follow()
    assert TestDef.count() == 1
    assert WorkflowTests.count() == 1
    assert WebserviceResponse.count() == 1
    assert 'First test' in resp.text
    assert escape('Test "First test" has been successfully imported.') in resp.text

    imported_testdef = TestDef.select()[0]
    assert imported_testdef.name == testdef.name
    assert imported_testdef.data == testdef.data

    resp = resp.click('Import')
    resp.form['file'] = Upload('export.wcs', export_resp.body)
    resp = resp.form.submit().follow()
    assert TestDef.count() == 2
    assert WorkflowTests.count() == 2
    assert WebserviceResponse.count() == 2
    assert len(resp.pyquery('li a:contains("First test")')) == 2
    assert escape('Test "First test" has been successfully imported.') in resp.text

    resp = resp.click('Import')
    resp.form['file'] = Upload('export.wcs', b'invalid')
    resp = resp.form.submit()
    assert 'Invalid File' in resp.text

    formdef2 = FormDef()
    formdef2.name = 'test title'
    formdef2.store()

    resp = app.get('/backoffice/forms/%s/tests/' % formdef2.id)
    resp = resp.click('Import')

    resp.form['file'] = Upload('export.wcs', export_resp.body)
    resp = resp.form.submit().follow()
    assert len(TestDef.select_for_objectdef(formdef2)) == 1
    assert len(resp.pyquery('li a:contains("First test")')) == 1


def test_tests_delete_no_workflow_tests(pub):
    create_superuser(pub)

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.store()

    testdef = TestDef.create_from_formdata(formdef, formdef.data_class()())
    testdef.name = 'First test'
    # simulate old testdef without workflow tests attached
    testdef.workflow_tests = mock.MagicMock()
    testdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/1/tests/%s/' % testdef.id)

    resp = resp.click('Delete')
    resp = resp.form.submit().follow()
    assert 'First test' not in resp.text


def test_tests_status_page(pub):
    user = create_superuser(pub)

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.fields = [fields.StringField(id='1', varname='test_field', label='Test Field')]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.receipt_time = make_aware(datetime.datetime(2021, 1, 1, 0, 0))
    formdata.data['1'] = 'This is a test'
    formdata.user_id = user.id
    formdata.store()

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.name = 'First test'
    testdef.store()

    app = login(get_app(pub))

    resp = app.get('/backoffice/forms/1/tests/')
    resp = resp.click('First test')
    assert 'Test Field' in resp.text
    assert 'This is a test' in resp.text

    # check access to other form views is forbidden
    app.get('/backoffice/forms/1/tests/%s/inspect' % testdef.id, status=404)


def test_tests_status_page_block_field(pub):
    create_superuser(pub)

    block = BlockDef()
    block.name = 'foobar'
    block.fields = [fields.ItemField(id='1', label='Test item', items=['foo', 'bar', 'baz'])]
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
    testdef.name = 'First test'
    testdef.store()

    app = login(get_app(pub))

    resp = app.get('/backoffice/forms/1/tests/%s/' % testdef.id)
    assert resp.pyquery('div.field-type-block div.field-type-item p.label').text() == 'Test item'
    assert resp.pyquery('div.field-type-block div.field-type-item .value').text() == 'foo'


def test_tests_status_page_image_field(pub):
    create_superuser(pub)

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.fields = [fields.FileField(id='1', label='File', varname='foo', max_file_size='1ko')]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.receipt_time = make_aware(datetime.datetime(2021, 1, 1, 0, 0))

    upload = PicklableUpload('test.jpeg', 'image/jpeg')
    with open(os.path.join(os.path.dirname(__file__), '..', 'image-with-gps-data.jpeg'), 'rb') as jpg:
        upload.receive([jpg.read()])

    formdata.data['1'] = upload
    formdata.store()

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.name = 'First test'
    testdef.store()

    app = login(get_app(pub))

    resp = app.get('/backoffice/forms/1/tests/%s/' % testdef.id)
    assert 'download?f=1&thumbnail=1' in resp.text

    resp = app.get('/backoffice/forms/1/tests/%s/download?f=1&thumbnail=1' % testdef.id)
    resp.follow(status=404)


def test_tests_history_page(pub):
    user = create_superuser(pub)

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.fields = [fields.StringField(id='1', varname='test_field', label='Test Field')]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.receipt_time = make_aware(datetime.datetime(2021, 1, 1, 0, 0))
    formdata.data['1'] = 'This is a test'
    formdata.user_id = user.id
    formdata.store()

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.name = 'Test 1'
    testdef.workflow_tests.actions = [
        workflow_tests.ButtonClick(id='1', button_name='xxx'),
    ]
    # create one snapshot
    testdef.store()

    response = WebserviceResponse()
    response.testdef_id = testdef.id
    response.name = 'Fake response'
    response.url = 'http://example.com/json'
    response.payload = '{"foo": "bar"}'
    response.store()

    # create second snapshot
    testdef.name = 'Test 2'
    testdef.store()

    # create third snapshot
    testdef.name = 'Test 3'
    testdef.store()

    app = login(get_app(pub))

    resp = app.get('/backoffice/forms/1/tests/%s/' % testdef.id)
    resp = resp.click('History')
    assert [x.attrib['class'] for x in resp.pyquery.find('.snapshots-list tr')] == [
        'new-day',
        'collapsed initially-collapsed',
        'collapsed initially-collapsed',
    ]

    # export snapshot
    resp_export = resp.click('Export', index=1)
    assert resp_export.content_type == 'application/x-wcs-snapshot'
    assert '>Test 2<' in resp_export.text

    # view snapshot
    view_resp = resp.click('View', index=1)
    assert '<h2>Test 2</h2>' in view_resp.text
    assert 'Options' not in resp.text
    assert 'Delete' not in resp.text
    assert 'Edit' not in resp.text

    resp = view_resp.click('Workflow tests')
    assert 'Simulate click on action button' in resp.text
    assert 'Add' not in resp.text
    assert 'Delete' not in resp.text
    assert 'Duplicate' not in resp.text

    resp = resp.click(href=r'^1/$')
    assert '>Submit<' not in resp.text

    resp = view_resp.click('Webservice responses')
    assert 'New' not in resp.text
    assert 'Remove' not in resp.text
    assert 'Duplicate' not in resp.text

    resp = resp.click('Fake response')
    assert 'Edit webservice response' in resp.text
    assert '>Submit<' not in resp.text

    # restore as new
    assert TestDef.count() == 1
    assert WorkflowTests.count() == 1
    assert WebserviceResponse.count() == 1

    resp = view_resp.click('Restore version')
    resp.form['action'] = 'as-new'
    resp = resp.form.submit('submit').follow()

    assert TestDef.count() == 2
    assert WorkflowTests.count() == 2
    assert WebserviceResponse.count() == 2
    assert '<h2>Test 2</h2>' in resp.text

    # restore as current
    resp = view_resp.click('Restore version')
    resp.form['action'] = 'overwrite'
    resp = resp.form.submit('submit').follow()

    assert TestDef.count() == 2
    assert WorkflowTests.count() == 2
    assert WebserviceResponse.count() == 2
    assert '<h2>Test 2</h2>' in resp.text

    # restore first version as current, making sure webservice response is deleted
    resp = resp.click('History')
    resp = resp.click('Restore', index=2)
    resp.form['action'] = 'overwrite'
    resp = resp.form.submit('submit').follow()

    assert TestDef.count() == 2
    assert WorkflowTests.count() == 2
    assert WebserviceResponse.count() == 1
    assert '<h2>Test 1</h2>' in resp.text


def test_tests_edit(pub):
    create_superuser(pub)

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.fields = [fields.StringField(id='1', label='Text', varname='text')]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.receipt_time = make_aware(datetime.datetime(2021, 1, 1, 0, 0))
    formdata.data = {'1': 'xxx'}
    formdata.store()

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.name = 'First test'
    testdef.store()

    app = login(get_app(pub))

    resp = app.get('/backoffice/forms/1/tests/%s/' % testdef.id)
    assert 'First test' in resp.text

    resp = resp.click('Options')
    resp.form['name'] = 'Second test'
    resp = resp.form.submit('submit').follow()
    assert 'Second test' in resp.text


def test_tests_edit_data(pub):
    user = create_superuser(pub)

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.fields = [
        fields.PageField(id='0', label='1st page'),
        fields.StringField(id='1', label='Text 1', varname='text1'),
        fields.PageField(id='2', label='2nd page'),
        fields.StringField(id='3', label='Text 2', varname='text2'),
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.receipt_time = make_aware(datetime.datetime(2021, 1, 1, 0, 0))
    formdata.data['1'] = 'test 1'
    formdata.data['3'] = 'test 2'
    formdata.user_id = user.id
    formdata.store()

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.name = 'First test'
    testdef.store()

    app = login(get_app(pub))

    resp = app.get('/backoffice/forms/1/tests/%s/' % testdef.id)
    assert 'test 1' in resp.text
    assert 'test 2' in resp.text

    resp = resp.click('Edit data')
    resp.form['f1'] = 'test 3'
    resp = resp.form.submit('submit')
    assert 'Save data' in resp.text
    resp = resp.form.submit('submit').follow()  # change nothing on second page
    assert 'test 1' not in resp.text
    assert 'test 3' in resp.text
    assert 'test 2' in resp.text

    resp = resp.click('Edit data')
    resp.form['f1'] = 'test 4'
    resp = resp.form.submit('submit')
    resp = resp.form.submit('cancel')
    assert resp.location.endswith('/backoffice/forms/1/tests/%s/' % testdef.id)
    resp = resp.follow()
    assert 'test 4' not in resp.text
    assert 'test 3' in resp.text
    assert 'test 2' in resp.text


def test_tests_edit_data_multiple_pages(pub):
    create_superuser(pub)

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.fields = [
        fields.PageField(id='0', label='1st page'),
        fields.StringField(id='1', label='Text 1', varname='text1'),
        fields.PageField(
            id='2',
            label='2nd page',
            condition={'type': 'django', 'value': 'form_var_text1 == "my text 1"'},
        ),
        fields.StringField(id='3', label='Text 2', varname='text2'),
    ]
    formdef.store()

    testdef = TestDef.create_from_formdata(formdef, formdef.data_class()())
    testdef.name = 'First test'
    testdef.store()

    app = login(get_app(pub))

    resp = app.get('/backoffice/forms/1/tests/%s/edit-data/' % testdef.id)
    resp.form['f1'] = 'my text 1'
    resp = resp.form.submit('submit')
    resp.form['f3'] = 'my text 2'
    resp = resp.form.submit('submit').follow()
    assert 'my text 1' in resp.text
    assert 'my text 2' in resp.text

    resp = resp.click('Edit data')
    resp.form['f1'] = 'other text'
    resp = resp.form.submit('submit').follow()
    assert 'other text' in resp.text
    assert 'my text 2' not in resp.text


def test_tests_edit_data_mark_as_failing(pub):
    create_superuser(pub)

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
        fields.CommentField(id='2', label='comment field'),
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.receipt_time = make_aware(datetime.datetime(2021, 1, 1, 0, 0))
    formdata.data['1'] = '12345'
    formdata.store()

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.name = 'First test'
    testdef.store()

    app = login(get_app(pub))

    resp = app.get('/backoffice/forms/1/tests/%s/' % testdef.id)
    resp = resp.click('Edit data')
    assert 'Mark as failing' not in resp.text

    resp.form['f1'] = '123456'
    resp = resp.form.submit('submit').follow()
    assert '123456' in resp.text

    resp = resp.click('Edit data')
    assert 'Mark as failing' not in resp.text

    # two errors on page
    resp.form['f1'] = '123a'
    resp = resp.form.submit('submit')
    assert 'Mark as failing' not in resp.text

    # one error
    resp.form['f1'] = '1234'
    resp = resp.form.submit('submit')
    assert 'If test should fail on error "Not enough chars.", click button below.' in resp.text
    assert 'Mark as failing' in resp.text

    # other error
    resp.forms[0]['f1'] = 'abcdefg'
    resp = resp.forms[0].submit('submit')
    assert (
        'If test should fail on error "You should enter digits only, for example: 123.", click button below.'
        in resp.text
    )
    assert 'Mark as failing' in resp.text

    # click mark as failing button
    resp = resp.forms[1].submit().follow()
    assert 'abcdefg' in resp.text
    assert (
        escape('This test is expected to fail on error "You should enter digits only, for example: 123.".')
        in resp.text
    )

    resp = resp.click('Edit data')
    assert (
        'This test is expected to fail on error "You should enter digits only, for example: 123.".'
        in resp.text
    )

    resp.form['f1'] = '1234567'
    resp = resp.form.submit('submit').follow()
    assert 'This test is expected to fail' not in resp.text

    # only post is allowed
    app.get('/backoffice/forms/1/tests/%s/edit-data/mark-as-failing' % testdef.id, status=404)


def test_tests_edit_data_mark_as_failing_hidden_error(pub):
    create_superuser(pub)

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.fields = [
        fields.StringField(id='0', label='Text', varname='text', validation={'type': 'digits'}),
        fields.StringField(
            id='1',
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
    formdata.data['0'] = 'not-digits'
    formdata.data['1'] = 'also-not-digits'
    formdata.store()

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.name = 'First test'
    testdef.store()

    app = login(get_app(pub))

    resp = app.get('/backoffice/forms/1/tests/%s/edit-data/' % testdef.id)
    resp = resp.form.submit('submit')
    assert (
        'If test should fail on error "You should enter digits only, for example: 123.", click button below.'
        in resp.text
    )


def test_tests_edit_data_mark_as_failing_required_field(pub):
    create_superuser(pub)

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.fields = [
        fields.StringField(id='1', label='Text', varname='text'),
        fields.StringField(id='2', label='Text 2', varname='text2', validation={'type': 'digits'}),
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.receipt_time = make_aware(datetime.datetime(2021, 1, 1, 0, 0))
    formdata.store()

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.name = 'First test'
    testdef.store()

    app = login(get_app(pub))

    resp = app.get('/backoffice/forms/1/tests/%s/' % testdef.id)
    resp = resp.click('Edit data')
    assert 'Mark as failing' not in resp.text

    # required field errors cannot be used to mark test as failing
    resp = resp.form.submit('submit')
    assert resp.pyquery('#form_error_f1').text() == 'required field'
    assert resp.pyquery('#form_error_f2').text() == 'required field'
    assert 'Mark as failing' not in resp.text

    # mark as failing button can appear even when missing required field
    resp.form['f2'] = 'abc'
    resp = resp.form.submit('submit')
    assert resp.pyquery('#form_error_f1').text() == 'required field'
    assert 'Mark as failing' in resp.text
    assert (
        'If test should fail on error "You should enter digits only, for example: 123.", click button below.'
        in resp.text
    )


def test_tests_edit_data_backoffice_submission(pub):
    create_superuser(pub)

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
    formdata.data['1'] = '12345'
    formdata.store()

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.name = 'First test'
    testdef.store()

    app = login(get_app(pub))

    resp = app.get('/backoffice/forms/1/tests/%s/' % testdef.id)
    resp = resp.click('Edit data')

    assert 'Current submission mode is frontoffice.' in resp.text
    assert 'submission agent' not in resp.text

    resp = resp.form.submit('submit').follow()

    resp = resp.click('Edit data')
    assert 'Current submission mode is frontoffice.' in resp.text

    resp = resp.click('Edit submission settings')
    resp.form['backoffice_submission'] = True
    resp = resp.form.submit().follow()

    assert 'Current submission mode is backoffice.' in resp.text
    assert 'No submission agent.' in resp.text

    resp = resp.click('Edit submission settings')
    resp.form['submission_agent'] = test_user.test_uuid
    resp = resp.form.submit().follow()

    assert 'Current submission mode is backoffice.' in resp.text
    assert 'Submission agent: test user 1' in resp.text

    resp = resp.form.submit('submit')
    assert 'Must not be in backoffice' in resp.text

    resp = resp.click('Edit submission settings')
    resp.form['backoffice_submission'] = False
    resp = resp.form.submit().follow()

    assert 'Current submission mode is frontoffice.' in resp.text
    resp = resp.form.submit('submit').follow()

    # check test passes
    resp = app.get('/backoffice/forms/1/tests/results/run').follow()
    assert 'Success!' in resp.text


@pytest.mark.parametrize('formdef_class', [FormDef, CardDef])
def test_tests_edit_data_live_url(formdef_class, pub):
    create_superuser(pub)

    formdef = formdef_class()
    formdef.name = 'test title'
    formdef.fields = [
        fields.StringField(id='1', label='Test', varname='foo'),
        fields.StringField(
            id='2',
            label='Condi',
            varname='bar',
            required='required',
            condition={'type': 'django', 'value': 'form_var_foo == "ok"'},
        ),
        fields.StringField(
            id='3',
            label='Condi 2',
            varname='bar2',
            required='required',
            condition={'type': 'django', 'value': 'form_var_foo and is_in_backoffice'},
        ),
    ]
    formdef.store()
    formdef.data_class().wipe()

    app = login(get_app(pub))
    resp = app.get(formdef.get_admin_url() + 'tests/')

    resp = resp.click('New')
    resp.form['name'] = 'Test'
    resp = resp.form.submit().follow()

    resp.form['f1'] = 'ok'
    live_url = resp.html.find('form').attrs['data-live-url']
    live_resp = app.post(live_url, params=resp.form.submit_fields())
    assert live_resp.json['result']['2']['visible'] is True
    assert live_resp.json['result']['3']['visible'] is False

    resp = resp.click('Edit submission settings')
    resp.form['backoffice_submission'] = True
    resp = resp.form.submit().follow()

    resp.form['f1'] = 'nok'
    live_resp = app.post(live_url, params=resp.form.submit_fields())
    assert live_resp.json['result']['2']['visible'] is False
    assert live_resp.json['result']['3']['visible'] is True


def test_tests_edit_data_numeric_field_inside_block(pub):
    create_superuser(pub)

    block = BlockDef()
    block.name = 'foobar'
    block.fields = [fields.NumericField(id='1', label='Numeric', varname='foo')]
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
    formdata.data['1'] = {'data': [{'1': decimal.Decimal(42)}]}

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.store()

    app = login(get_app(pub))
    resp = app.get(testdef.get_admin_url() + 'edit-data/')

    resp = resp.click('Edit submission settings')
    resp.form['backoffice_submission'] = True
    resp = resp.form.submit().follow()

    resp = resp.form.submit('submit').follow()


def test_tests_edit_data_numeric_field_0_value(pub):
    create_superuser(pub)

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.fields = [fields.NumericField(id='1', label='Numeric', varname='foo')]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data['1'] = decimal.Decimal(0)

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.store()

    app = login(get_app(pub))
    resp = app.get(testdef.get_admin_url() + 'edit-data/')
    resp = resp.form.submit('submit').follow()


def test_tests_edit_data_change_user(pub):
    create_superuser(pub)
    user = pub.test_user_class(name='new user')
    user.email = 'new@example.com'
    user.test_uuid = '42'
    user.store()

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.name = 'First test'
    testdef.store()

    app = login(get_app(pub))

    resp = app.get(testdef.get_admin_url() + 'edit-data/')
    assert 'No associated user.' in resp.text

    resp = resp.click('Edit submission settings')
    resp.form['user'] = user.test_uuid
    resp = resp.form.submit('submit').follow()
    assert 'Associated user: new user.' in resp.text

    resp = resp.click('Edit submission settings')
    resp.form['user'] = ''
    resp = resp.form.submit('submit').follow()
    assert 'No associated user.' in resp.text


def test_tests_edit_data_query_parameters(pub):
    create_superuser(pub)

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.fields = [
        fields.PageField(id='0', label='1st page'),
        fields.CommentField(id='1', label='{{ request.GET.param1 }}'),
        fields.PageField(id='2', label='1st page'),
        fields.CommentField(id='3', label='{{ request.GET.param1 }}'),
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.name = 'First test'
    testdef.store()

    app = login(get_app(pub))

    resp = app.get(testdef.get_admin_url() + 'edit-data/')
    assert resp.pyquery('.comment-field').text() == ''

    resp = resp.click('Edit parameters')
    resp.form['query_parameters$element0key'] = 'param1'
    resp.form['query_parameters$element0value'] = 'Value 1'
    resp = resp.form.submit('submit').follow()

    assert resp.pyquery('.comment-field').text() == 'Value 1'
    assert 'param1: Value 1' in resp.text

    # go to next page, parameter value is not available anymore
    resp = resp.form.submit('submit')
    assert resp.pyquery('.comment-field').text() == ''

    # hide first page
    formdef.fields[0].condition = {'type': 'django', 'value': 'False'}
    formdef.store()

    resp = app.get(testdef.get_admin_url() + 'edit-data/')
    assert resp.pyquery('.comment-field').text() == 'Value 1'

    # check single page form
    formdef.fields = [
        fields.CommentField(id='1', label='{{ request.GET.param1 }}'),
    ]
    formdef.store()

    resp = app.get(testdef.get_admin_url() + 'edit-data/')
    assert resp.pyquery('.comment-field').text() == 'Value 1'

    # remove parameters
    resp = resp.click('Edit parameters')
    resp.form['query_parameters$element0key'] = ''
    resp.form['query_parameters$element0value'] = ''
    resp = resp.form.submit('submit').follow()

    assert resp.pyquery('.comment-field').text() == ''


def test_tests_edit_data_submission_date(pub):
    create_superuser(pub)

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.fields = [
        fields.PageField(
            id='0',
            label='1st page',
            post_conditions=[
                {
                    'condition': {'type': 'django', 'value': 'now < "2018-11-17"|date'},
                    'error_message': 'Too old, go back in time',
                }
            ],
        ),
        fields.CommentField(id='1', label='Now template value: {{ now }}'),
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.frozen_submission_datetime = None
    testdef.name = 'First test'
    testdef.store()

    app = login(get_app(pub))

    resp = app.get(testdef.get_admin_url() + 'edit-data/')
    assert 'No frozen submission date' in resp.text

    # try to submit form
    resp = resp.form.submit('submit')
    assert 'Too old, go back in time' in resp.text

    resp = resp.click('Change date')
    resp.form['frozen_submission_datetime$date'] = '2018-01-01'
    resp.form['frozen_submission_datetime$time'] = '12:00'
    resp = resp.form.submit('submit').follow()

    assert 'No frozen submission date' not in resp.text
    assert 'Submission date: 2018-01-01 12:00' in resp.text
    assert 'Now template value: 2018-01-01 12:00' in resp.text

    # submitting form again does not trigger error
    resp.form.submit('submit').follow()

    # make sure snapshot timestamp is not in the past
    snapshot = pub.snapshot_class.get_latest('testdef', testdef.id)
    assert snapshot.comment == 'Change in test data'
    assert snapshot.timestamp.year > 2018


def test_tests_edit_data_submission_date_live(pub):
    create_superuser(pub)

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.fields = [
        fields.StringField(id='1', label='Text', varname='text'),
        fields.CommentField(id='2', label='{{ form_var_text }} {{ today }}'),
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data['1'] = 'abc'

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.frozen_submission_datetime = datetime.datetime(2025, 5, 27, 10, 00)
    testdef.name = 'First test'
    testdef.store()

    app = login(get_app(pub))

    resp = app.get(testdef.get_admin_url() + 'edit-data/')
    assert 'abc 2025-05-27' in resp.text

    resp.form['f1'] = 'def'
    live_resp = app.post(testdef.get_admin_url() + 'edit-data/live', resp.form.submit_fields())
    assert live_resp.json['result']['2']['content'] == '<p>def 2025-05-27</p>'


def test_tests_edit_data_dependencies(pub):
    create_superuser(pub)

    formdef = FormDef()
    formdef.name = 'Form dependency'
    formdef.store()

    dependency_testdef = TestDef.create_from_formdata(formdef, formdef.data_class()())
    dependency_testdef.name = 'Form test'
    dependency_testdef.store()

    carddef = CardDef()
    carddef.name = 'Card dependency'
    carddef.store()

    dependency_testdef = TestDef.create_from_formdata(carddef, carddef.data_class()())
    dependency_testdef.name = 'Card test'
    dependency_testdef.store()

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.store()

    testdef = TestDef.create_from_formdata(formdef, formdef.data_class()())
    testdef.name = 'Current test'
    testdef.store()

    app = login(get_app(pub))

    resp = app.get(testdef.get_admin_url() + 'edit-data/')

    assert 'Form test' not in resp.text
    assert 'Card test' not in resp.text

    resp = resp.click('Edit dependencies')

    assert [x.attrib.get('label', x.text) for x in resp.pyquery('select').find('*')] == [
        '---',
        'Card dependency',
        'Card test',
        'Form dependency',
        'Form test',
    ]

    resp.form['dependencies$element0'].select(text='Card test')
    resp = resp.form.submit('dependencies$add_element')

    resp.form['dependencies$element1'].select(text='Form test')
    resp = resp.form.submit('submit').follow()

    assert resp.pyquery('div#dependencies li:nth-child(1)').text() == 'Card test (never ran)'
    assert resp.pyquery('div#dependencies li:nth-child(2)').text() == 'Form test (never ran)'

    # generate test result of card dependency
    app.get('/backoffice/cards/%s/tests/results/run' % carddef.id).follow()

    resp = app.get(testdef.get_admin_url() + 'edit-data/')

    assert resp.pyquery('div#dependencies li:nth-child(1)').text() == 'Card test'
    assert resp.pyquery('div#dependencies li:nth-child(2)').text() == 'Form test (never ran)'

    resp = resp.click('Card test')
    assert 'Card test' in resp.text

    nested_testdef = TestDef.create_from_formdata(carddef, carddef.data_class()())
    nested_testdef.name = 'Nested dependency'
    nested_testdef.store()

    dependency_testdef.dependencies = [nested_testdef.uuid]
    dependency_testdef.store()

    resp = app.get(testdef.get_admin_url() + 'edit-data/')

    assert resp.pyquery('div#dependencies li:nth-child(1) a')[0].text == 'Card test'
    assert resp.pyquery('div#dependencies li:nth-child(1) a')[1].text == 'Nested dependency'
    assert resp.pyquery('div#dependencies li:nth-child(2)').text() == 'Form test (never ran)'


def test_tests_edit_data_dependencies_card_datasource(pub):
    create_superuser(pub)

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

    carddata = carddef.data_class()()
    carddata.just_created()
    carddata.data = {'1': 'def'}
    carddata.store()

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.fields = [
        fields.ItemField(
            id='1',
            label='Foo',
            varname='foo',
            data_source={'type': 'carddef:test-dependency'},
        ),
    ]
    formdef.store()

    testdef = TestDef.create_from_formdata(formdef, formdef.data_class()())
    testdef.name = 'Current test'
    testdef.store()

    app = login(get_app(pub))

    resp = app.get(testdef.get_admin_url() + 'edit-data/')
    assert resp.form['f1'].options == [
        ('1', False, 'def'),
    ]

    testdef.dependencies = [dependency_testdef.uuid]
    testdef.store()

    # real card is not visible anymore
    resp = app.get(testdef.get_admin_url() + 'edit-data/')
    assert resp.form['f1'].options == [
        ('', False, '---'),
    ]

    # generate test result of dependency
    app.get('/backoffice/cards/%s/tests/results/run' % carddef.id).follow()

    resp = app.get(testdef.get_admin_url() + 'edit-data/')
    assert resp.form['f1'].options == [
        ('1', False, 'abc'),
    ]

    dependency_testdef.data['fields']['1'] = 'ghi'
    dependency_testdef.store()

    # generate new test result of dependency
    app.get('/backoffice/cards/%s/tests/results/run' % carddef.id).follow()

    resp = app.get(testdef.get_admin_url() + 'edit-data/')
    assert resp.form['f1'].options == [
        ('2', False, 'ghi'),
    ]

    # create new dependency
    dependency_testdef.id = None
    dependency_testdef.data['fields']['1'] = 'klm'
    dependency_testdef.store()

    app.get('/backoffice/cards/%s/tests/results/run' % carddef.id).follow()
    resp = app.get(testdef.get_admin_url() + 'edit-data/')
    assert resp.form['f1'].options == [
        ('3', False, 'ghi'),
        ('4', False, 'klm'),
    ]

    # check autocompletion
    formdef.fields[0].display_mode = 'autocomplete'
    formdef.store()

    resp = app.get(testdef.get_admin_url() + 'edit-data/')
    assert resp.form['f1'].options == []

    resp = app.get(resp.pyquery('select#form_f1').attr('data-select2-url'))
    assert resp.json['data'] == [
        {'id': 3, 'text': 'ghi'},
        {'id': 4, 'text': 'klm'},
    ]


def test_tests_edit_data_dependencies_user_filter(pub):
    admin_user = create_superuser(pub)

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

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.name = 'Current test'
    testdef.dependencies = [dependency_testdef.uuid]
    testdef.store()

    app = login(get_app(pub))

    resp = app.get(testdef.get_admin_url() + 'edit-data/')
    assert 'f1' not in resp.form.fields

    # generate test result of dependency
    app.get('/backoffice/cards/%s/tests/results/run' % carddef.id).follow()

    resp = app.get(testdef.get_admin_url() + 'edit-data/')
    resp.form['f1'] = 'xxx'

    resp = resp.form.submit('submit').follow()

    # make sure snapshot has correct user
    snapshot = pub.snapshot_class.get_latest('testdef', testdef.id)
    assert snapshot.user.id == admin_user.id


def test_tests_edit_data_users_data_source(pub):
    admin_user = create_superuser(pub)

    user_formdef = UserFieldsFormDef(pub)
    user_formdef.fields = [
        fields.StringField(id='1', label='first_name', varname='first_name'),
        fields.StringField(id='2', label='last_name', varname='last_name'),
    ]
    user_formdef.store()

    normal_user = pub.user_class(name='normal')
    normal_user.email = 'normal@example.com'
    normal_user.form_data = {'1': 'Normal', '2': 'Doe'}
    normal_user.store()

    test_user = pub.user_class(name='test user 1')
    test_user.email = 'test@example.com'
    test_user.test_uuid = '42'
    test_user.form_data = {'1': 'Jon', '2': 'Doe'}
    test_user.store()

    test_user2 = pub.user_class(name='test user 2')
    test_user2.email = 'test2@example.com'
    test_user2.test_uuid = '43'
    test_user2.form_data = {'1': 'Jane', '2': 'Doe'}
    test_user2.store()

    datasource = NamedDataSource(name='foo')
    datasource.data_source = {'type': 'wcs:users'}
    datasource.store()

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.fields = [
        fields.ItemField(id='1', label='Bar', varname='bar', data_source={'type': 'foo'}),
        fields.StringField(
            id='2',
            label='Test',
            varname='test',
            condition={'type': 'django', 'value': 'form_var_bar_live_var_first_name == "Jane"'},
        ),
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.name = 'First test'
    testdef.store()

    app = login(get_app(pub))

    resp = app.get(testdef.get_admin_url() + 'edit-data/')
    assert resp.form['f1'].options == [
        (str(test_user.id), False, 'test user 1'),
        (str(test_user2.id), False, 'test user 2'),
    ]

    resp.form['f1'] = test_user.id
    live_resp = app.post(testdef.get_admin_url() + 'edit-data/live', resp.form.submit_fields())
    assert live_resp.json['result']['2']['visible'] is False

    resp.form['f1'] = test_user2.id
    live_resp = app.post(testdef.get_admin_url() + 'edit-data/live', resp.form.submit_fields())
    assert live_resp.json['result']['2']['visible'] is True

    resp = resp.form.submit('submit')
    assert resp.pyquery('#form_error_f2').text() == 'required field'

    resp.form['f2'] = 'xxx'
    resp = resp.form.submit('submit').follow()

    # make sure snapshot has correct user
    snapshot = pub.snapshot_class.get_latest('testdef', testdef.id)
    assert snapshot.user.id == admin_user.id


def test_tests_edit_data_users_data_workflow_options(pub):
    create_superuser(pub)

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
        fields.StringField(
            id='1',
            label='Test Field',
            varname='test',
            condition={'type': 'django', 'value': 'form_option_foo == "abc"'},
        ),
    ]
    formdef.store()

    testdef = TestDef.create_from_formdata(formdef, formdef.data_class()())
    testdef.name = 'First test'
    testdef.store()

    app = login(get_app(pub))
    resp = app.get(testdef.get_admin_url() + 'edit-data/')

    assert 'Test Field' not in resp.text

    formdef.workflow_options = {'foo': 'abc'}
    formdef.store()

    resp = app.get(testdef.get_admin_url() + 'edit-data/')

    assert 'Test Field' in resp.text

    resp = resp.click('Override options')
    resp.form['f1'] = 'def'
    resp = resp.form.submit('submit').follow()

    assert 'Override options' not in resp.text
    assert 'Test Field' not in resp.text

    resp = resp.click('View options')
    assert resp.form['f1'].value == 'def'

    resp.form['f1'] = 'abc'
    resp = resp.form.submit('submit').follow()

    assert 'Test Field' in resp.text

    resp = resp.click('View options')
    resp.form['f1'] = 'ghi'
    resp = resp.form.submit('submit').follow()

    assert 'Test Field' not in resp.text

    resp = resp.click('Reset options').follow()

    assert 'Test Field' in resp.text

    workflow.variables_formdef = None
    workflow.store()

    resp = app.get(testdef.get_admin_url() + 'edit-data/')
    assert 'Override options' not in resp.text


def test_tests_edit_data_users_data_workflow_options_card_dependency(pub):
    create_superuser(pub)

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
        fields.StringField(
            id='1',
            label='Test Field',
            varname='test',
            condition={'type': 'django', 'value': 'form_option_foo == "abc"'},
        ),
    ]
    formdef.store()

    testdef = TestDef.create_from_formdata(formdef, formdef.data_class()())
    testdef.name = 'First test'
    testdef.dependencies = [dependency_testdef.uuid]
    testdef.store()

    app = login(get_app(pub))

    # generate test result of dependency
    app.get('/backoffice/cards/%s/tests/results/run' % carddef.id).follow()

    resp = app.get(testdef.get_admin_url() + 'edit-data/')

    assert 'Test Field' not in resp.text

    resp = resp.click('Override options')
    resp.form['f1'].select(text='abc')
    resp = resp.form.submit('submit').follow()

    assert 'Test Field' in resp.text

    resp = resp.click('View options')
    assert resp.form['f1'].value == '1'


def test_tests_result_edit_data_sent_requests(pub, http_requests):
    create_superuser(pub)

    wscall = NamedWsCall()
    wscall.name = 'Hello world'
    wscall.request = {'url': 'http://remote.example.net/json'}
    wscall.store()

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.fields = [
        fields.CommentField(id='1', label='{{ webservice.hello_world }}'),
    ]
    formdef.store()

    testdef = TestDef.create_from_formdata(formdef, formdef.data_class()())
    testdef.name = 'First test'
    testdef.store()

    app = login(get_app(pub))
    resp = app.get(testdef.get_admin_url() + 'edit-data/')

    assert escape("{'foo': 'bar'}") in resp.text

    response = WebserviceResponse()
    response.testdef_id = testdef.id
    response.name = 'Response xxx'
    response.url = 'http://remote.example.net/json'
    response.payload = '{"foo": "other"}'
    response.method = 'GET'
    response.store()

    resp = app.get(testdef.get_admin_url() + 'edit-data/')

    assert escape("{'foo': 'other'}") in resp.text

    wscall.request['method'] = 'POST'
    wscall.store()

    resp = app.get(testdef.get_admin_url() + 'edit-data/')

    assert escape("{'foo': 'bar'}") in resp.text


def test_tests_result_edit_data_session_user(pub, http_requests):
    create_superuser(pub)

    test_user = pub.user_class(name='new user')
    test_user.email = 'new@example.com'
    test_user.test_uuid = '42'
    test_user.store()

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.fields = [
        fields.CommentField(id='1', label='{{ session_user_email }}'),
    ]
    formdef.store()

    testdef = TestDef.create_from_formdata(formdef, formdef.data_class()())
    testdef.name = 'First test'
    testdef.store()

    app = login(get_app(pub))
    resp = app.get(testdef.get_admin_url() + 'edit-data/')

    assert 'admin@example.com' in resp.text

    testdef.user_uuid = test_user.test_uuid
    testdef.store()

    resp = app.get(testdef.get_admin_url() + 'edit-data/')

    assert 'new@example.com' in resp.text


def test_tests_manual_run(pub):
    user = create_superuser(pub)

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.fields = [
        fields.StringField(id='1', label='String', varname='string'),
    ]
    formdef.store()

    app = login(get_app(pub))

    resp = app.get(formdef.get_admin_url() + 'tests/')
    resp = resp.click('Test results')
    assert 'No test results yet.' in resp.text
    assert 'Run tests' not in resp.text

    # create test
    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.receipt_time = make_aware(datetime.datetime(2021, 1, 1, 0, 0))
    formdata.data['1'] = 'a'
    formdata.user_id = user.id
    formdata.store()

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.name = 'First test'
    testdef.store()

    resp = app.get('/backoffice/forms/1/tests/results/')
    assert 'No test results yet.' in resp.text

    resp = resp.click('Run tests')
    result = TestResults.select()[-1]
    assert resp.location == 'http://example.net/backoffice/forms/1/tests/results/%s/' % result.id

    resp = resp.follow()
    assert 'Started by: Manual run.' in resp.text
    assert len(resp.pyquery('tr')) == 1
    assert 'Success!' in resp.text
    assert 'Display details' not in resp.text

    resp = resp.click('First test')
    assert 'Edit data' in resp.text

    resp = app.get('/backoffice/forms/1/tests/results/')
    assert 'No test results yet.' not in resp.text
    assert len(resp.pyquery('tr')) == 1
    assert len(resp.pyquery('span.test-success')) == 1
    assert len(resp.pyquery('span.test-failure')) == 0

    # add required field
    formdef.fields.append(fields.StringField(id='2', label='String field', varname='string'))
    formdef.store()

    resp = app.get('/backoffice/forms/1/tests/')  # run from test listing page
    resp = resp.click('Run tests')
    result = TestResults.select()[-1]
    assert resp.location == 'http://example.net/backoffice/forms/1/tests/results/%s/' % result.id

    resp = app.get('/backoffice/forms/1/tests/results/')
    assert len(resp.pyquery('tr')) == 2
    assert len(resp.pyquery('span.test-success')) == 2

    resp = resp.click('#%s' % result.id)
    assert 'Started by: Manual run.' in resp.text
    assert 'Success!' in resp.text

    resp = resp.click('Display details')
    assert 'String field' in resp.text

    # add validation to first field
    formdef.fields[0].validation = {'type': 'digits'}
    formdef.store()

    resp = app.get('/backoffice/forms/1/tests/results/')
    resp = resp.click('Run tests')
    result = TestResults.select()[-1]
    assert resp.location == 'http://example.net/backoffice/forms/1/tests/results/%s/' % result.id

    resp = app.get('/backoffice/forms/1/tests/results/')
    assert len(resp.pyquery('tr')) == 3
    assert len(resp.pyquery('span.test-success')) == 2
    assert len(resp.pyquery('span.test-failure')) == 1

    resp = resp.click('#%s' % result.id)
    assert 'Started by: Manual run.' in resp.text
    assert 'Success!' not in resp.text
    assert 'You should enter digits only, for example: 123.' in resp.text
    assert 'Display inspect' in resp.text
    assert len(resp.pyquery('td.name a')) == 1

    resp = resp.click('Run tests again')
    resp = app.get('/backoffice/forms/1/tests/results/')
    assert len(resp.pyquery('tr')) == 4

    TestDef.remove_object(testdef.id)
    resp = app.get('/backoffice/forms/1/tests/results/%s/' % result.id)
    assert 'Display inspect' not in resp.text
    assert len(resp.pyquery('td.name a')) == 0

    # simulate still running result, it should not appear
    result.success = None
    result.store()

    resp = app.get('/backoffice/forms/1/tests/results/')
    assert '#%s' % result.id not in resp.text

    # access unknown test result
    app.get('/backoffice/forms/1/tests/results/42/', status=404)


def test_tests_manual_run_crash(pub):
    create_superuser(pub)
    AfterJob.wipe()

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.store()

    app = login(get_app(pub))

    # create test
    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.name = 'First test'
    testdef.store()

    resp = app.get('/backoffice/forms/1/tests/results/')
    assert 'No test results yet.' in resp.text

    with pytest.raises(ValueError):
        with mock.patch('wcs.testdef.TestDef.run', side_effect=ValueError):
            resp = resp.click('Run tests')

    # a test result is created
    assert TestResults.count() == 1

    # but it does not appear on results page
    resp = app.get('/backoffice/forms/1/tests/results/')
    assert 'No test results yet.' in resp.text

    resp = app.get('/backoffice/forms/1/')
    resp = resp.click('change title')
    resp.form['name'] = 'new title'
    with mock.patch('wcs.testdef.TestDef.run', side_effect=ValueError):
        resp = resp.form.submit().follow()

    # a test result is created
    assert AfterJob.count() == 1
    assert TestResults.count() == 2

    # but it does not appear on results page
    resp = app.get('/backoffice/forms/1/tests/results/')
    assert 'No test results yet.' in resp.text


def test_tests_manual_run_dependencies(pub):
    create_superuser(pub)

    formdef = FormDef()
    formdef.name = 'test dependency'
    formdef.store()

    dependency_testdef = TestDef.create_from_formdata(formdef, formdef.data_class()())
    dependency_testdef.name = 'First test'
    dependency_testdef.store()

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.store()

    testdef = TestDef.create_from_formdata(formdef, formdef.data_class()())
    testdef.name = 'Second test'
    testdef.dependencies = [dependency_testdef.uuid]
    testdef.store()

    app = login(get_app(pub))

    resp = app.get('/backoffice/forms/%s/tests/results/run' % formdef.id).follow()
    assert 'Success!' in resp.text
    assert 'Display inspect' in resp.text

    dependency_testdef.expected_error = 'xxx'
    dependency_testdef.store()

    resp = app.get('/backoffice/forms/%s/tests/results/run' % formdef.id).follow()
    assert 'Success!' not in resp.text
    assert 'Display inspect' not in resp.text
    assert 'Error in dependency: Expected error' in resp.text

    resp = resp.click('Display details')
    assert 'Dependency with error' in resp.text

    resp = resp.click('First test')
    assert 'First test' in resp.text


def test_tests_result_recorded_errors(pub):
    create_superuser(pub)

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.fields = [
        fields.ComputedField(
            id='1',
            label='Computed',
            varname='computed',
            value_template='{{ forms|objects:"test-title"|filter_by:"unknown"|filter_value:"xxx"|count }}',
            freeze_on_initial_value=True,
        ),
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.receipt_time = make_aware(datetime.datetime(2021, 1, 1, 0, 0))
    formdata.store()

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.name = 'First test'
    testdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/1/tests/results/run').follow()
    resp = resp.click('Display details')

    assert 'Missing required fields' not in resp.text
    assert 'Recorded errors:' in resp.text
    assert escape('Invalid filter "unknown"') in resp.text


def test_tests_result_sent_requests(pub, http_requests):
    create_superuser(pub)

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
    formdata.store()

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.name = 'First test'
    testdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/1/tests/results/run').follow()

    assert 'Success!' in resp.text
    assert http_requests.count() == 1
    http_requests.empty()

    resp = resp.click('Display details')

    assert 'Sent requests:' in resp.text
    assert 'GET http://remote.example.net/json' in resp.text
    assert 'Used webservice response:' not in resp.text

    response = WebserviceResponse()
    response.testdef_id = testdef.id
    response.name = 'Response xxx'
    response.url = 'http://remote.example.net/json'
    response.payload = '{"foo": "wrong"}'
    response.store()

    resp = app.get('/backoffice/forms/1/tests/results/run').follow()

    assert 'Success!' not in resp.text
    assert http_requests.count() == 0

    resp = resp.click('Display details')
    result_url = resp.request.url

    assert 'Sent requests:' in resp.text
    assert 'GET http://remote.example.net/json' in resp.text
    assert 'Used webservice response:' in resp.text

    resp = resp.click('Response xxx')
    assert 'Edit webservice response' in resp.text

    response.remove_self()
    resp = app.get(result_url)

    assert 'Used webservice response:' in resp.text
    assert 'Response xxx' not in resp.text
    assert 'deleted' in resp.text

    wscall.request['method'] = 'POST'
    wscall.store()

    resp = app.get('/backoffice/forms/1/tests/results/run').follow()

    assert 'Success!' not in resp.text
    assert http_requests.count() == 0

    resp = resp.click('Display details')

    assert 'Sent requests:' in resp.text
    assert 'POST http://remote.example.net/json' in resp.text
    assert 'Request was blocked since it is not a GET request.' in resp.text
    assert 'Recorded errors:' in resp.text
    assert 'error in HTTP request to remote.example.net (method must be GET)' in resp.text

    resp = resp.click('You can create corresponding webservice response here.')
    assert 'Webservice responses' in resp.text

    TestDef.remove_object(testdef.id)
    resp = app.get(result_url)

    assert 'Used webservice response:' in resp.text
    assert 'deleted' in resp.text


def test_tests_result_sent_requests_mismatch_reason(pub, http_requests):
    create_superuser(pub)

    wscall = NamedWsCall()
    wscall.name = 'Hello world'
    wscall.request = {
        'url': 'http://remote.example.net/json',
        'method': 'POST',
    }
    wscall.store()

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

    testdef = TestDef.create_from_formdata(formdef, formdef.data_class()())
    testdef.name = 'First test'
    testdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/1/tests/results/run').follow()

    response = WebserviceResponse()
    response.testdef_id = testdef.id
    response.name = 'Response xxx'
    response.method = 'GET'
    response.url = 'http://remote.example.net/json'
    response.payload = '{}'
    response.store()

    response2 = WebserviceResponse()
    response2.testdef_id = testdef.id
    response2.name = 'Response wrong url'
    response2.url = 'http://remote.example.net/json2'
    response2.payload = '{}'
    response2.store()

    resp = app.get('/backoffice/forms/1/tests/results/run')
    result_url = resp.location

    resp = resp.follow().click('Display details')

    assert 'Sent requests:' in resp.text
    assert 'POST http://remote.example.net/json' in resp.text
    assert 'Request was not mocked.' in resp.text
    assert 'Method does not match (expected GET, was POST).' in resp.text
    assert 'Response xxx' in resp.text
    assert 'Response wrong url' not in resp.text

    resp = resp.click('Response xxx')
    assert 'Edit webservice response' in resp.text

    response.remove_self()
    resp = app.get(result_url)
    resp = resp.click('Display details')

    assert 'Response xxx' not in resp.text


def test_tests_result_error_field(pub):
    create_superuser(pub)

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.fields = [
        fields.StringField(id='0', label='Text Field', varname='text', validation={'type': 'digits'}),
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.data['0'] = 'not-digits'

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.name = 'First test'
    testdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/1/tests/results/run')
    result_url = resp.location
    resp = resp.follow()

    assert escape('Invalid value "not-digits" for field "Text Field"') in resp.text

    resp = resp.click('Display details')

    assert 'Field linked to error:' in resp.text
    assert 'deleted' not in resp.text

    resp = resp.click('Text Field')

    assert resp.pyquery('h2').text() == 'Text Field'

    formdef.fields = []
    formdef.store()

    resp = app.get(result_url)
    resp = resp.click('Display details')

    assert 'Text Field' not in resp.text
    assert 'deleted' in resp.text


def test_tests_result_inspect(pub):
    create_superuser(pub)

    role = pub.role_class(name='test role')
    role.store()

    test_user = pub.user_class(name='new user')
    test_user.email = 'new@example.com'
    test_user.test_uuid = '42'
    test_user.roles = [role.id]
    test_user.store()

    test_user2 = pub.user_class(name='user 2')
    test_user2.test_uuid = '43'
    test_user2.store()

    workflow = Workflow(name='Workflow One')
    workflow.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(workflow)
    workflow.backoffice_fields_formdef.fields = [
        fields.StringField(id='1', label='Text BO', varname='text_bo'),
    ]

    new_status = workflow.add_status(name='New status')
    end_status = workflow.add_status(name='End status')

    set_backoffice_fields = new_status.add_action('set-backoffice-fields')
    set_backoffice_fields.fields = [{'field_id': '1', 'value': 'goodbye'}]

    wscall = new_status.add_action('webservice_call')
    wscall.url = 'http://example.com/json'
    wscall.varname = 'test_webservice'
    wscall.qs_data = {'a': 'b'}

    dispatch = new_status.add_action('dispatch')
    dispatch.dispatch_type = 'manual'
    dispatch.role_key = '_receiver'
    dispatch.role_id = role.id

    display_form = new_status.add_action('form', id='form')
    display_form.varname = 'foo'
    display_form.by = ['_receiver']
    display_form.formdef = WorkflowFormFieldsFormDef(item=display_form)
    display_form.formdef.fields = [
        fields.StringField(id='1', label='Text', varname='wf_text'),
    ]

    register_comment = new_status.add_action('register-comment')
    register_comment.comment = 'Hello'
    register_comment.attachments = ['{{ form_var_foo_raw }}']

    target_formdef = FormDef()
    target_formdef.name = 'To create'
    target_formdef.fields = [fields.StringField(id='1', label='Text', varname='text')]
    target_formdef.store()

    create_formdata = new_status.add_action('create_formdata', id='create_formdata')
    create_formdata.varname = 'created_formdata'
    create_formdata.formdef_slug = target_formdef.url_name
    create_formdata.mappings = [
        Mapping(field_id='1', expression='xxx'),
    ]

    jump = new_status.add_action('choice')
    jump.label = 'Go to end status'
    jump.status = end_status.id
    jump.by = [role.id]

    workflow.store()

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.fields = [
        fields.StringField(id='0', label='Text Field', varname='text'),
        fields.FileField(id='2', label='File', varname='foo', max_file_size='1ko'),
    ]
    formdef.workflow = workflow
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.data['0'] = 'hello'

    upload = PicklableUpload('test.pdf', 'application/pdf', 'ascii')
    upload.receive([b'first line', b'second line'])
    formdata.data['2'] = upload

    carddef = CardDef()
    carddef.name = 'Card dependency'
    carddef.store()

    dependency_testdef = TestDef.create_from_formdata(carddef, carddef.data_class()())
    dependency_testdef.name = 'Card test'
    dependency_testdef.user_uuid = test_user2.test_uuid
    dependency_testdef.store()

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.name = 'First test'
    testdef.agent_id = test_user.test_uuid
    testdef.user_uuid = test_user2.test_uuid
    testdef.is_in_backoffice = True
    testdef.dependencies = [dependency_testdef.uuid]
    testdef.workflow_tests.actions = [
        workflow_tests.FillForm(
            form_action_id='%s-%s' % (new_status.id, display_form.id),
            form_data={'1': 'Hello'},
        ),
        workflow_tests.ButtonClick(id='1', button_name='Go to end status', who='receiver'),
    ]
    testdef.store()

    response = WebserviceResponse()
    response.testdef_id = testdef.id
    response.name = 'Fake response'
    response.url = 'http://example.com/json'
    response.payload = '{"foo": "bar"}'
    response.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/%s/tests/results/run' % formdef.id)
    resp = resp.follow()
    resp = resp.click('Display inspect')

    assert 'form_var_text' in resp.text
    assert 'form_var_text_bo' in resp.text
    assert 'form_workflow_data_test_webservice_response_foo' in resp.text
    assert 'form_workflow_data_foo_var_wf_text' not in resp.text
    assert 'form_links_created_formdata' in resp.text
    assert resp.pyquery('div#inspect-functions .value').text() == 'test role'

    assert [x.text_content() for x in resp.pyquery('div#inspect-timeline a')] == [
        'New status',
        'Backoffice Data',
        'Webservice',
        'Function/Role Linking',
        'History Message',
        'New Form Creation',
        'Created form - To create #1-1',
        'Action button - Manual Jump Go to end status',
    ]

    resp.form['django-condition'] = 'form_var_text == "hello"'
    resp = resp.form.submit()
    assert 'Condition result' in resp.text
    assert 'result-true' in resp.text

    resp.form['django-condition'] = 'form_var_text_bo == "goodbye"'
    resp = resp.form.submit()
    assert 'Condition result' in resp.text
    assert 'result-true' in resp.text

    resp.form['django-condition'] = 'form_submission_backoffice'
    resp = resp.form.submit()
    assert 'Condition result' in resp.text
    assert 'result-true' in resp.text

    resp.form['django-condition'] = 'form_workflow_form_foo_0_var_wf_text == "Hello"'
    resp = resp.form.submit()
    assert 'Condition result' in resp.text
    assert 'result-true' in resp.text

    resp.form['django-condition'] = 'cards|objects:"card-dependency"|filter_by_user:form_user|count == 1'
    resp = resp.form.submit()
    assert 'Condition result' in resp.text
    assert 'result-true' in resp.text


def test_tests_result_inspect_multiple_tests(pub):
    create_superuser(pub)

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.fields = [
        fields.StringField(id='0', label='Text Field', varname='text'),
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.data['0'] = 'First test string'

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.name = 'First test'
    testdef.store()

    formdata.data['0'] = 'Second test string'

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.name = 'Second test'
    testdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/1/tests/results/run')
    result_url = resp.location

    resp = resp.follow()
    resp = resp.click('Display inspect', index=0)

    assert 'First test string' in resp.text

    resp = app.get(result_url)
    resp = resp.click('Display inspect', index=1)

    assert 'Second test string' in resp.text


def test_tests_result_coverage(pub):
    create_superuser(pub)

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.fields = [
        fields.PageField(id='1', label='1st page', varname='page1'),
        fields.TitleField(id='0', label='Title'),
        fields.StringField(id='2', label='No condition', varname='no_condition'),
        fields.StringField(
            id='3',
            label='Sometimes hidden',
            condition={'type': 'django', 'value': 'form_var_no_condition == "abc"'},
        ),
        fields.StringField(
            id='4',
            label='Always visible',
            condition={'type': 'django', 'value': 'True'},
        ),
    ]
    formdef.store()

    testdef = TestDef.create_from_formdata(formdef, formdef.data_class()())
    testdef.name = 'Test field hidden'
    testdef.store()

    app = login(get_app(pub))

    resp = app.get('/backoffice/forms/1/tests/results/run').follow()
    assert 'Fields coverage: 75%' in resp.text

    resp = resp.click('details', href='coverage')

    assert len(resp.pyquery('.coverage')) == 4
    assert len(resp.pyquery('.field')) == 3
    assert len(resp.pyquery('.covered')) == 3
    assert len(resp.pyquery('.not-covered')) == 1

    assert resp.pyquery('.coverage--info:eq(0)').text() == 'No condition, always displayed.'
    assert resp.pyquery('.coverage--info:eq(1)').text() == 'No condition, always displayed.'
    assert resp.pyquery('.coverage--info:eq(2)').text().splitlines() == [
        'Never displayed.',
        'Hidden in tests:',
        'Test field hidden',
    ]
    assert resp.pyquery('.coverage--info:eq(3)').text().splitlines() == [
        'Displayed in tests:',
        'Test field hidden',
        'Never hidden.',
    ]

    formdata = formdef.data_class()()
    formdata.data['2'] = 'abc'

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.name = 'test field visible'
    testdef.store()

    resp = app.get('/backoffice/forms/1/tests/results/run').follow()
    assert 'Fields coverage: 100%' in resp.text

    resp = resp.click('details', href='coverage')

    assert len(resp.pyquery('.coverage')) == 4
    assert len(resp.pyquery('.covered')) == 4
    assert len(resp.pyquery('.not-covered')) == 0

    assert resp.pyquery('.coverage--info:eq(2)').text().splitlines() == [
        'Displayed in tests:',
        'test field visible',
        'Hidden in tests:',
        'Test field hidden',
    ]

    TestDef.remove_object(testdef.id)
    resp = app.get(resp.request.url)

    assert resp.pyquery('.coverage--info:eq(2)').text().splitlines() == [
        'Displayed in tests:',
        'deleted test',
        'Hidden in tests:',
        'Test field hidden',
    ]

    # hide page
    formdef.fields[0].condition = {'type': 'django', 'value': 'False'}
    formdef.store()

    resp = app.get('/backoffice/forms/1/tests/results/run').follow()
    assert 'Fields coverage: 0%' in resp.text

    resp = resp.click('details', href='coverage')

    assert len(resp.pyquery('.covered')) == 0
    assert len(resp.pyquery('.not-covered')) == 4


def test_tests_run_order(pub):
    create_superuser(pub)

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.fields = [
        fields.StringField(id='1', label='String', varname='string', validation={'type': 'digits'})
    ]
    formdef.store()

    app = login(get_app(pub))

    formdata = formdef.data_class()()
    formdata.just_created()

    formdata.data['1'] = 'a'
    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.name = 'Failing test'
    testdef.store()

    formdata.data['1'] = '1'
    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.name = 'Passing test'
    testdef.store()

    testdef.id = None
    testdef.name = 'Another passing test'
    testdef.store()

    resp = app.get('/backoffice/forms/1/tests/results/')
    resp = resp.click('Run tests').follow()
    assert resp.text.count('Success!') == 2
    assert resp.text.count('You should enter digits only, for example: 123.') == 1
    assert (
        resp.text.index('Failing test')
        < resp.text.index('Another passing test')
        < resp.text.index('Passing test')
    )


def test_tests_duplicate(pub):
    user = create_superuser(pub)

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.fields = [fields.StringField(id='1', varname='test field', label='Test')]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.receipt_time = make_aware(datetime.datetime(2021, 1, 1, 0, 0))
    formdata.data['1'] = 'abcdefg'
    formdata.user_id = user.id
    formdata.store()

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.name = 'First test'
    testdef.workflow_tests.actions = [
        workflow_tests.ButtonClick(id='1', button_name='Go to end status'),
        workflow_tests.AssertStatus(id='2', status_name='End status'),
    ]
    testdef.store()

    response = WebserviceResponse()
    response.testdef_id = testdef.id
    response.name = 'Response xxx'
    response.store()

    testdef.workflow_tests.actions.append(
        workflow_tests.AssertWebserviceCall(id='3', webservice_response_uuid=response.uuid),
    )
    testdef.store()

    app = login(get_app(pub))

    assert TestDef.count() == 1
    assert WorkflowTests.count() == 1
    assert WebserviceResponse.count() == 1

    resp = app.get('/backoffice/forms/1/tests/%s/' % testdef.id)
    resp = resp.click('Duplicate')
    resp = resp.form.submit().follow()

    assert 'First test (copy)' in resp.text
    assert 'abcdefg' in resp.text
    assert TestDef.count() == 2
    assert WorkflowTests.count() == 2
    assert WebserviceResponse.count() == 2

    testdef1, testdef2 = TestDef.select(order_by='id')
    assert testdef1.uuid != testdef2.uuid

    testdef1.workflow_tests.actions[0].button_name = 'Changed'
    testdef1.store()

    response = testdef1.get_webservice_responses()[0]
    response.name = 'Changed'
    response.store()

    testdef1, testdef2 = TestDef.select(order_by='id')
    assert testdef1.workflow_tests.actions[0].button_name == 'Changed'
    assert testdef2.workflow_tests.actions[0].button_name == 'Go to end status'
    assert testdef1.get_webservice_responses()[0].name == 'Changed'
    assert testdef2.get_webservice_responses()[0].name == 'Response xxx'
    assert testdef1.workflow_tests.actions[2].details_label == 'Changed'
    assert testdef2.workflow_tests.actions[2].details_label == 'Response xxx'

    resp = app.get('/backoffice/forms/1/tests/%s/' % testdef.id)
    resp = resp.click('Duplicate')
    resp = resp.form.submit().follow()

    assert 'First test (copy 2)' in resp.text
    assert 'abcdefg' in resp.text
    assert TestDef.count() == 3


def test_form_with_test_duplicate(pub):
    user = create_superuser(pub)

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.fields = [fields.StringField(id='1', varname='test field', label='Test')]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.data['1'] = 'abcdefg'
    formdata.user_id = user.id
    formdata.store()

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.name = 'First test'
    testdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/1/')
    resp = resp.click('Duplicate')
    resp = resp.form.submit().follow()
    assert resp.pyquery('#appbar h2').text() == 'test title (copy)'


def test_tests_page_with_empty_map_field(pub):
    create_superuser(pub)

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.fields = [fields.MapField(id='1', label='Map', varname='map')]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.receipt_time = make_aware(datetime.datetime(2021, 1, 1, 0, 0))
    formdata.data['1'] = None
    formdata.store()

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.name = 'First test'
    testdef.store()

    app = login(get_app(pub))

    resp = app.get('/backoffice/forms/1/tests/%s/' % testdef.id)
    assert 'First test' in resp.text


def test_tests_card(pub):
    create_superuser(pub)

    carddef = CardDef()
    carddef.name = 'test title'
    carddef.fields = [
        fields.StringField(id='1', label='Text', varname='text'),
    ]
    carddef.store()

    app = login(get_app(pub))
    resp = app.get(carddef.get_admin_url())
    resp = resp.click('Tests')
    assert 'There are no tests yet.' in resp.text

    resp = resp.click('New')
    resp.form['name'] = 'First test'
    resp = resp.form.submit().follow()
    assert 'Edit test data' in resp.text

    resp.form['f1'] = 'abcdefg'
    resp = resp.form.submit('submit')

    testdef = TestDef.select()[0]
    assert resp.location == 'http://example.net/backoffice/cards/%s/tests/%s/' % (carddef.id, testdef.id)

    resp = resp.follow()
    assert 'First test' in resp.text
    assert 'abcdefg' in resp.text
    assert 'This test is empty' not in resp.text

    resp = app.get('/backoffice/cards/%s/tests/' % carddef.id)
    assert 'First test' in resp.text
    assert 'no tests yet' not in resp.text

    resp = resp.click('Run tests')
    result = TestResults.select()[-1]
    assert resp.location == 'http://example.net/backoffice/cards/%s/tests/results/%s/' % (
        carddef.id,
        result.id,
    )

    resp = resp.follow()
    assert len(resp.pyquery('tr')) == 1
    assert 'Success!' in resp.text

    resp = app.get('/backoffice/cards/%s/tests/results/' % carddef.id)
    assert len(resp.pyquery('span.test-success')) == 1


def test_tests_exclude_self(pub):
    create_superuser(pub)

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.fields = [
        fields.PageField(
            id='0',
            label='1st page',
            post_conditions=[
                {
                    'condition': {'type': 'django', 'value': 'form_objects|exclude_self|first'},
                    'error_message': 'No form exists',
                }
            ],
        ),
    ]
    formdef.store()

    submitted_formdata = formdef.data_class()()
    submitted_formdata.just_created()
    submitted_formdata.receipt_time = make_aware(datetime.datetime(2021, 1, 1, 0, 0))
    submitted_formdata.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.receipt_time = make_aware(datetime.datetime(2021, 1, 1, 0, 0))

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/1/tests/new')
    resp.form['name'] = 'First test'
    resp = resp.form.submit().follow()
    assert 'Edit test data' in resp.text

    resp = resp.form.submit('submit')
    assert 'No form exists' in resp.text


def test_tests_webservice_response(pub):
    create_superuser(pub)

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.name = 'First test'
    testdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/1/tests/%s/' % testdef.id)

    resp = resp.click('Webservice response')
    assert 'There are no webservice responses yet.' in resp.text

    resp = resp.click('New')
    resp.form['name'] = 'Test response'
    resp = resp.form.submit().follow()

    resp = resp.form.submit('submit')
    assert resp.pyquery('.error').text() == 'required field required field'

    resp = app.get('/backoffice/forms/1/tests/%s/webservice-responses/' % testdef.id)
    assert 'Test response' in resp.text
    assert 'There are no webservice responses yet.' not in resp.text
    assert '(not configured)' in resp.text

    resp = resp.click('Test response')
    resp.form['url$value_template'] = 'http://example.com/'
    resp.form['payload'] = '{"a": "b"}'
    resp.form['status_code'] = '400'
    resp.form['qs_data$element0key'] = 'foo'
    resp.form['method'] = 'POST (JSON)'
    resp.form['post_data$element0key'] = 'bar'
    resp = resp.form.submit('submit').follow()

    assert 'Test response' in resp.text
    assert '(not configured)' not in resp.text

    response = testdef.get_webservice_responses()[0]
    assert response.name == 'Test response'
    assert response.url == 'http://example.com/'
    assert response.payload == '{"a": "b"}'
    assert response.status_code == 400
    assert response.qs_data == {'foo': ''}
    assert response.method == 'POST'
    assert response.post_data == {'bar': ''}

    resp = resp.click('Duplicate').follow()
    assert 'Test response' in resp.text
    assert 'not configured' not in resp.text
    assert 'Test response (copy)' in resp.text

    new_response = testdef.get_webservice_responses()[1]
    assert new_response.name == 'Test response (copy)'
    assert new_response.url == 'http://example.com/'
    assert new_response.payload == '{"a": "b"}'
    assert new_response.uuid != response.uuid

    resp = resp.click('Remove', href=new_response.id)
    resp = resp.form.submit().follow()

    assert 'Test response (copy)' not in resp.text

    resp = resp.click('Test response')
    resp.form['payload'] = '{"a"}'
    resp = resp.form.submit()

    assert "Invalid JSON: Expecting ':' delimiter: line 1 column 5 (char 4)" in resp.text

    resp.form['url$value_template'] = '{{ [invalid }}'
    resp.form['payload'] = '{}'
    resp = resp.form.submit()

    assert 'syntax error in Django template' in resp.text

    resp = app.get('/backoffice/forms/1/tests/%s/webservice-responses/' % testdef.id)
    resp = resp.click('Import from other test')
    resp = resp.form.submit()

    assert resp.pyquery('div.error').text() == 'required field'

    testdef2 = TestDef.create_from_formdata(formdef, formdata)
    testdef.name = 'Second test'
    testdef2.store()

    resp = app.get('/backoffice/forms/1/tests/%s/webservice-responses/' % testdef2.id)
    assert 'Test response' not in resp.text

    resp = resp.click('Import from other test')
    resp.form['testdef_id'] = testdef.id
    resp = resp.form.submit().follow()

    assert 'Test response' in resp.text
    assert len(testdef.get_webservice_responses()) == 1
    assert len(testdef2.get_webservice_responses()) == 1


def test_tests_test_users_management(pub):
    create_superuser(pub)

    role = pub.role_class(name='test role')
    role.store()

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.store()

    user_formdef = UserFieldsFormDef(pub)
    user_formdef.fields = [
        fields.StringField(id='1', label='first_name', varname='first_name'),
        fields.StringField(id='2', label='last_name', varname='last_name'),
        fields.EmailField(id='3', label='email', varname='email'),
        fields.DateField(id='4', label='birthdate', varname='birthdate', required='optional'),
        fields.BoolField(id='5', label='bool_attr', varname='bool_attr', required='optional'),
        fields.FileField(id='6', label='file_attr', varname='file_attr', required='optional'),
    ]
    user_formdef.store()
    pub.cfg['users'][
        'fullname_template'
    ] = '{{ user_var_first_name|default:"" }} {{ user_var_last_name|default:"" }}'
    pub.cfg['users']['field_email'] = '3'
    pub.cfg['emails'] = {'check_domain_with_dns': False}
    pub.write_cfg()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/%s/tests/' % formdef.id)

    resp = resp.click('Test users')
    assert 'There are no test users yet.' in resp.text

    assert resp.pyquery('.breadcrumbs a')[-1].attrib['href'] == '/backoffice/forms/1/tests/test-users/'

    resp = resp.click('New')
    resp.form['name'] = 'User test'
    resp = resp.form.submit().follow()

    assert 'There are no test users yet.' not in resp.text

    resp = resp.click('User test')
    resp.form['roles$element0'] = role.id
    resp.form['f1'] = 'Jon'
    resp.form['f2'] = 'Doe'
    resp.form['f3'] = 'jon@example.com'
    resp.form['f4'] = '2024-05-27'
    resp.form['f5'].checked = True

    resp = resp.form.submit('submit').follow()

    user = pub.test_user_class.select()[0]
    assert user.name == 'User test'
    assert user.email == 'jon@example.com'
    assert user.roles == [role.id]
    assert len(user.name_identifiers[0]) == 32
    assert user.form_data['1'] == 'Jon'
    assert user.form_data['2'] == 'Doe'
    assert user.form_data['3'] == 'jon@example.com'
    assert user.form_data['4'] == time.strptime('2024-05-27', '%Y-%m-%d')
    assert user.form_data['5'] is True

    upload = PicklableUpload('test.jpeg', 'image/jpeg')
    with open(os.path.join(os.path.dirname(__file__), '..', 'image-with-gps-data.jpeg'), 'rb') as jpg:
        upload.receive([jpg.read()])

    real_user = pub.user_class(name='new user')
    real_user.email = 'jane@example.com'
    real_user.roles = [role.id]
    real_user.form_data = {
        '1': 'Jane',
        '2': 'Doe',
        '3': 'jane@example.com',
        '4': time.strptime('2024-05-28', '%Y-%m-%d'),
        '5': True,
        '6': upload,
    }
    real_user.store()

    resp = resp.click('New')
    resp.form['name'] = 'User test 2'
    resp.form['creation_mode'] = 'copy'
    resp.form['user_id'].force_value(real_user.id)
    resp = resp.form.submit().follow()

    user = pub.test_user_class.select(order_by='id')[1]
    assert user.name == 'User test 2'
    assert user.email == 'jane@example.com'
    assert user.roles == [role.id]
    assert user.form_data['1'] == 'Jane'
    assert user.form_data['2'] == 'Doe'
    assert user.form_data['3'] == 'jane@example.com'
    assert user.form_data['4'] == time.strptime('2024-05-28', '%Y-%m-%d')
    assert user.form_data['5'] is True
    assert user.form_data['6'].base_filename == 'test.jpeg'

    resp = resp.click('User test 2')
    assert resp.pyquery('title').text() == 'User test 2 | wcs'
    resp = resp.form.submit('cancel').follow()

    resp = resp.click('New')
    resp.form['name'] = 'User test 3'
    resp.form['creation_mode'] = 'copy'
    resp.form['user_id'].force_value(real_user.id)
    resp = resp.form.submit()

    assert 'A test user with this email already exists.' in resp.text

    resp = app.get('/backoffice/forms/test-users/')
    resp = resp.click('User test 2')
    resp.form['f3'] = 'jon@example.com'
    resp = resp.form.submit('submit')

    assert 'A test user with this email already exists.' in resp.text

    user_test_2_export_resp = resp.click('Export')

    resp = app.get('/backoffice/forms/test-users/')
    resp = resp.click('Remove', href=str(user.id))
    resp = resp.form.submit().follow()

    assert 'User test 2' not in resp.text

    resp = resp.click('Import')
    resp.form['file'] = Upload('export.wcs', user_test_2_export_resp.body)
    resp = resp.form.submit().follow()

    assert 'Test users have been successfully imported.' in resp.text
    assert 'User test 2' in resp.text
    assert pub.test_user_class.count() == 2

    user = pub.test_user_class.select(order_by='id')[1]
    assert user.name == 'User test 2'
    assert user.email == 'jane@example.com'
    assert user.roles == [role.id]
    assert user.form_data['1'] == 'Jane'
    assert user.form_data['2'] == 'Doe'
    assert user.form_data['3'] == 'jane@example.com'
    assert user.form_data['4'] == time.strptime('2024-05-28', '%Y-%m-%d')
    assert user.form_data['5'] is True
    assert user.form_data['6'] is None  # file is not included in import/export

    global_export_resp = resp.click('Export')

    user.remove_self()
    assert pub.test_user_class.count() == 1

    resp = resp.click('Import')
    resp.form['file'] = Upload('export.wcs', global_export_resp.body)
    resp = resp.form.submit().follow()

    assert 'Some already existing users were not imported.' in resp.text
    assert 'User test 2' in resp.text
    assert pub.test_user_class.count() == 2

    # creation from copy with no user specified creates empty user
    resp = resp.click('New')
    resp.form['name'] = 'User test 3'
    resp.form['creation_mode'] = 'copy'
    resp = resp.form.submit().follow()

    assert pub.test_user_class.count() == 3


def test_tests_test_users_management_different_formdef(pub):
    create_superuser(pub)

    user_formdef = UserFieldsFormDef(pub)
    user_formdef.fields = [
        fields.StringField(id='1', label='first_name', varname='first_name'),
        fields.StringField(id='2', label='last_name', varname='last_name'),
        fields.StringField(id='3', label='email', varname='email'),
    ]
    user_formdef.store()
    pub.cfg['users'][
        'fullname_template'
    ] = '{{ user_var_first_name|default:"" }} {{ user_var_last_name|default:"" }}'
    pub.cfg['users']['field_email'] = '3'
    pub.write_cfg()

    test_user = pub.test_user_class(name='Test User')
    test_user.email = 'jane@example.com'
    test_user.test_uuid = '42'
    test_user.form_data = {
        '1': 'Jane',
        '2': 'Doe',
        '3': 'jane@example.com',
    }
    test_user.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/test-users/')
    export_resp = resp.click('Export')

    # remove email field
    user_formdef.fields = [
        fields.StringField(id='1', label='first_name', varname='first_name'),
        fields.StringField(id='2', label='last_name', varname='last_name'),
    ]
    user_formdef.store()

    test_user.remove_self()
    assert pub.test_user_class.count() == 0

    resp = resp.click('Import')
    resp.form['file'] = Upload('export.wcs', export_resp.body)
    resp = resp.form.submit().follow()

    assert pub.test_user_class.count() == 1
    assert 'Test User' in resp.text


def test_tests_test_users_history_page(pub):
    create_superuser(pub)

    test_user = pub.test_user_class(name='Test User')
    test_user.email = 'jane@example.com'
    test_user.test_uuid = '42'
    test_user.form_data = {
        '1': 'Jane',
        '2': 'Doe',
        '3': 'jane@example.com',
    }
    # create one snapshot
    test_user.store()

    # create second snapshot
    test_user.name = 'Modified User'
    test_user.store()

    app = login(get_app(pub))

    resp = app.get('/backoffice/forms/test-users/%s/' % test_user.id)
    resp = resp.click('History')
    assert [x.attrib['class'] for x in resp.pyquery.find('.snapshots-list tr')] == [
        'new-day',
        'collapsed initially-collapsed',
    ]

    # export snapshot
    resp_export = resp.click('Export', index=1)
    assert resp_export.content_type == 'application/x-wcs-snapshot'
    assert '>Test User<' in resp_export.text

    # view snapshot
    resp = resp.click('View', index=1)
    assert resp.form['name'].value == 'Test User'
    assert '>Submit<' not in resp.text

    # restore
    assert pub.test_user_class.count() == 1

    resp = resp.click('Restore version')
    assert 'Restore as a new item' not in resp.text
    resp = resp.form.submit('submit').follow()

    assert pub.test_user_class.count() == 1

    test_user = pub.test_user_class.get(test_user.id)
    assert test_user.name == 'Test User'
