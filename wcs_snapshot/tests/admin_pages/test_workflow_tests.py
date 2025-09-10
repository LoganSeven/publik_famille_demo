import datetime
import json

import pytest
from django.utils.html import escape
from django.utils.timezone import make_aware

from wcs import fields, workflow_tests
from wcs.blocks import BlockDef
from wcs.carddef import CardDef
from wcs.data_sources import NamedDataSource
from wcs.formdef import FormDef
from wcs.qommon.http_request import HTTPRequest
from wcs.sql import AnyFormData
from wcs.testdef import TestDef, TestResults, WebserviceResponse
from wcs.wf.create_formdata import Mapping
from wcs.wf.form import WorkflowFormFieldsFormDef
from wcs.workflow_traces import TestWorkflowTrace
from wcs.workflows import Workflow, WorkflowBackofficeFieldsFormDef, WorkflowCriticalityLevel

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
    FormDef.wipe()
    CardDef.wipe()
    WebserviceResponse.wipe()
    return pub


def teardown_module(module):
    clean_temporary_pub()


def test_workflow_tests_options(pub):
    create_superuser(pub)
    user = pub.user_class(name='test user')
    user.email = 'test@example.com'
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

    resp = app.get('/backoffice/forms/1/tests/%s/workflow/' % testdef.id)
    resp = resp.click('Options')

    resp.form['agent'] = user.test_uuid
    resp = resp.form.submit('submit').follow()

    testdef = TestDef.get(testdef.id)
    assert testdef.agent_id == user.test_uuid


def test_workflow_tests_edit_actions(pub):
    create_superuser(pub)
    user = pub.user_class(name='test user')
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
    testdef.agent_id = user.test_uuid
    testdef.store()

    app = login(get_app(pub))

    resp = app.get('/backoffice/forms/1/tests/%s/' % testdef.id)
    resp = resp.click('Workflow tests')

    assert 'There are no workflow test actions yet.' in resp.text
    assert len(resp.pyquery('.biglist li')) == 0

    option_labels = [x[2] for x in resp.form['type'].options]
    assert (
        option_labels.index('Card creation')
        < option_labels.index('Card edition')
        < option_labels.index('Email send')
        < option_labels.index('Form status')
        < option_labels.index('Move forward in time')
        < option_labels.index('Simulate click on action button')
    )

    # add workflow test action through sidebar form
    resp.form['type'] = 'button-click'
    resp = resp.form.submit('submit').follow()

    assert 'There are no workflow test actions yet.' not in resp.text
    assert len(resp.pyquery('.biglist li')) == 1
    assert resp.pyquery('.biglist li .label').text() == 'Simulate click on action button'
    assert 'not configured' in resp.text

    resp = resp.click(href=r'^1/$')
    resp = resp.form.submit('cancel').follow()

    resp = resp.click(href=r'^1/$')
    resp.form['button_name$choice'] = 'Accept'
    resp = resp.form.submit('submit').follow()

    assert 'not configured' not in resp.text
    assert [x.text for x in resp.pyquery('ul li.workflow-test-action span.type')] == [
        'Click on "Accept" by backoffice user',
    ]

    resp = resp.click('Duplicate').follow()
    assert [x.text for x in resp.pyquery('ul li.workflow-test-action span.type')] == [
        'Click on "Accept" by backoffice user',
        'Click on "Accept" by backoffice user',
    ]

    testdef = TestDef.get(testdef.id)
    action1, action2 = testdef.workflow_tests.actions
    assert action1.uuid != action2.uuid

    resp = resp.click(href=r'^1/$', index=0)
    resp.form['button_name$choice'] = 'Reject'
    resp = resp.form.submit('submit').follow()

    assert [x.text for x in resp.pyquery('ul li.workflow-test-action span.type')] == [
        'Click on "Reject" by backoffice user',
        'Click on "Accept" by backoffice user',
    ]

    resp = resp.click('Duplicate', index=0).follow()
    assert [x.text for x in resp.pyquery('ul li.workflow-test-action span.type')] == [
        'Click on "Reject" by backoffice user',
        'Click on "Reject" by backoffice user',
        'Click on "Accept" by backoffice user',
    ]

    resp = resp.click('Delete', index=0)
    resp = resp.form.submit('submit').follow()
    assert [x.text for x in resp.pyquery('ul li.workflow-test-action span.type')] == [
        'Click on "Reject" by backoffice user',
        'Click on "Accept" by backoffice user',
    ]

    # simulate invalid action
    testdef = TestDef.get(testdef.id)
    testdef.workflow_tests.actions[0].key = 'xxx'
    testdef.store()

    resp = app.get('/backoffice/forms/1/tests/%s/workflow/' % testdef.id)
    assert [x.text for x in resp.pyquery('ul li.workflow-test-action span.type')] == [
        'Click on "Accept" by backoffice user',
    ]


def test_workflow_tests_action_button_click(pub):
    create_superuser(pub)
    user = pub.user_class(name='test user')
    user.test_uuid = '42'
    user.store()

    workflow = Workflow(name='Workflow One')
    new_status = workflow.add_status(name='New status')

    display_form = new_status.add_action('form', id='form')
    display_form.varname = 'bar'
    display_form.formdef = WorkflowFormFieldsFormDef(item=display_form)

    commentable = new_status.add_action('commentable', id='commentable')

    workflow.store()

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.workflow_id = workflow.id
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.workflow_tests.actions = [
        workflow_tests.ButtonClick(id='1', button_name='Button 4', who='submitter'),
    ]
    testdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/1/tests/%s/workflow/' % testdef.id)
    assert escape('Click on "Button 4" by submitter') in resp.text

    resp = app.get('/backoffice/forms/1/tests/%s/workflow/1/' % testdef.id)
    assert resp.form['button_name$choice'].options == [('__other', True, 'Other:')]

    jump = new_status.add_action('choice')
    jump.label = 'Button 1'
    jump.status = new_status.id

    jump = new_status.add_action('choice')
    jump.label = 'Button 2'
    jump.status = new_status.id

    jump = new_status.add_action('choice')
    jump.label = 'Button with {{ template }}'
    jump.status = new_status.id

    jump = new_status.add_action('choice')
    jump.label = 'Button no target status'

    commentable.button_label = 'Add comment'

    workflow.add_global_action('Action 1')

    interactive_action = workflow.add_global_action('Interactive action (should not be shown)')
    interactive_action.add_action('form')

    workflow.store()

    resp = app.get('/backoffice/forms/1/tests/%s/workflow/1/' % testdef.id)
    assert resp.form['button_name$choice'].options == [
        ('Action 1', False, 'Action 1'),
        ('Add comment', False, 'Add comment'),
        ('Button 1', False, 'Button 1'),
        ('Button 2', False, 'Button 2'),
        ('__other', True, 'Other:'),
    ]
    assert resp.form['button_name$other'].value == 'Button 4'

    display_form.hide_submit_button = False
    workflow.store()

    resp = app.get('/backoffice/forms/1/tests/%s/workflow/1/' % testdef.id)
    assert resp.form['button_name$choice'].options == [
        ('Action 1', False, 'Action 1'),
        ('Add comment', False, 'Add comment'),
        ('Button 1', False, 'Button 1'),
        ('Button 2', False, 'Button 2'),
        ('Submit', False, 'Submit'),
        ('__other', True, 'Other:'),
    ]

    resp.form['button_name$choice'] = 'Submit'
    resp = resp.form.submit('submit').follow()

    assert escape('Click on "Submit" by submitter') in resp.text

    resp = app.get('/backoffice/forms/1/tests/%s/workflow/1/' % testdef.id)
    assert resp.form['who'].options == [
        ('submitter', True, None),
        ('other', False, None),
    ]

    testdef.agent_id = user.test_uuid
    testdef.store()

    resp = app.get('/backoffice/forms/1/tests/%s/workflow/1/' % testdef.id)
    assert resp.form['who'].options == [
        ('receiver', False, None),
        ('submitter', True, None),
        ('other', False, None),
    ]

    resp.form['button_name$choice'] = 'Button 1'
    resp.form['who'] = 'receiver'
    resp = resp.form.submit('submit').follow()

    assert escape('Click on "Button 1" by backoffice user') in resp.text

    resp = app.get('/backoffice/forms/1/tests/%s/workflow/1/' % testdef.id)
    resp.form['who'] = 'other'
    resp.form['who_id'] = user.test_uuid
    resp = resp.form.submit('submit').follow()

    assert escape('Click on "Button 1" by test user') in resp.text

    user.remove_self()
    resp = app.get('/backoffice/forms/1/tests/%s/workflow/' % testdef.id)
    assert escape('Click on "Button 1" by missing user') in resp.text

    user.store()
    resp = app.get('/backoffice/forms/1/tests/%s/workflow/1/' % testdef.id)
    resp.form['who'] = 'receiver'
    resp = resp.form.submit('submit').follow()


def test_workflow_tests_action_assert_status(pub):
    create_superuser(pub)

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.workflow_tests.actions = [
        workflow_tests.AssertStatus(id='1', status_name='Deleted status'),
    ]
    testdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/1/tests/%s/workflow/1/' % testdef.id)

    assert resp.form['status_name'].options == [
        ('Just Submitted', False, 'Just Submitted'),
        ('New', False, 'New'),
        ('Rejected', False, 'Rejected'),
        ('Accepted', False, 'Accepted'),
        ('Finished', False, 'Finished'),
        ('Deleted status (not available)', False, 'Deleted status (not available)'),
    ]


def test_workflow_tests_action_skip_time(pub):
    create_superuser(pub)

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.workflow_tests.actions = [
        workflow_tests.SkipTime(id='1'),
    ]
    testdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/1/tests/%s/workflow/1/' % testdef.id)

    resp.form['seconds'] = '1 day 1 hour 1 minute'
    resp = resp.form.submit('submit').follow()

    assert TestDef.get(testdef.id).workflow_tests.actions[0].seconds == 25 * 60 * 60 + 60

    resp = app.get('/backoffice/forms/1/tests/%s/workflow/1/' % testdef.id)
    assert resp.form['seconds'].value == '1 day, 1 hour and 1 minute'

    resp = resp.form.submit('submit').follow()
    assert TestDef.get(testdef.id).workflow_tests.actions[0].seconds == 25 * 60 * 60 + 60


def test_workflow_tests_action_assert_email(pub):
    create_superuser(pub)

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.workflow_tests.actions = [
        workflow_tests.AssertEmail(id='1'),
    ]
    testdef.store()

    app = login(get_app(pub))

    resp = app.get('/backoffice/forms/1/tests/%s/workflow/' % testdef.id)
    assert 'not configured' not in resp.text
    assert 'Email to' not in resp.text

    # empty configuration is allowed
    resp = resp.click(href=r'^1/$')
    resp = resp.form.submit('submit').follow()

    resp = resp.click(href=r'^1/$')
    resp.form['subject_strings$element0'] = 'abc'
    resp.form['body_strings$element0'] = 'def'
    resp = resp.form.submit('submit').follow()

    assert 'Email to' not in resp.text

    assert_email = TestDef.get(testdef.id).workflow_tests.actions[0]
    assert assert_email.subject_strings == ['abc']
    assert assert_email.body_strings == ['def']

    resp = resp.click(href=r'^1/$')
    resp.form['addresses$element0'] = 'test@entrouvert.com'
    resp = resp.form.submit('submit').follow()

    assert escape('Email to "test@entrouvert.com"') in resp.text

    assert_email.addresses = ['a@entrouvert.com', 'b@entrouvert.com', 'c@entrouvert.com']
    assert_email.parent.store()

    resp = app.get('/backoffice/forms/1/tests/%s/workflow/' % testdef.id)
    assert escape('Email to "a@entrouvert.com" (+2)') in resp.text

    assert_email.addresses = []
    assert_email.subject_strings = ['Hello your form has been submitted']
    assert_email.parent.store()

    resp = app.get('/backoffice/forms/1/tests/%s/workflow/' % testdef.id)
    assert escape('Subject must contain "Hello your form has been su(…)"') in resp.text

    assert_email.subject_strings = []
    assert_email.body_strings = ['Hello your form has been submitted']
    assert_email.parent.store()

    resp = app.get('/backoffice/forms/1/tests/%s/workflow/' % testdef.id)
    assert escape('Body must contain "Hello your form has been su(…)"') in resp.text


def test_workflow_tests_action_assert_sms(pub):
    create_superuser(pub)

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.workflow_tests.actions = [
        workflow_tests.AssertSMS(id='1'),
    ]
    testdef.store()

    app = login(get_app(pub))

    resp = app.get('/backoffice/forms/1/tests/%s/workflow/' % testdef.id)
    assert 'not configured' not in resp.text
    assert 'SMS to' not in resp.text

    # empty configuration is allowed
    resp = resp.click(href=r'^1/$')
    resp = resp.form.submit('submit').follow()

    resp = resp.click(href=r'^1/$')
    resp.form['phone_numbers$element0'] = '0123456789'
    resp.form['body'] = 'Hello your form has been submitted'
    resp = resp.form.submit('submit').follow()

    assert 'SMS to 0123456789' in resp.text

    assert_sms = TestDef.get(testdef.id).workflow_tests.actions[0]
    assert assert_sms.phone_numbers == ['0123456789']
    assert assert_sms.body == 'Hello your form has been submitted'

    assert_sms.phone_numbers = ['0123456789', '0123456781', '0123456782']
    assert_sms.parent.store()

    resp = app.get('/backoffice/forms/1/tests/%s/workflow/' % testdef.id)
    assert escape('SMS to 0123456789 (+2)') in resp.text

    assert_sms.phone_numbers = []
    assert_sms.parent.store()

    resp = app.get('/backoffice/forms/1/tests/%s/workflow/' % testdef.id)
    assert 'Hello your form has been su(…)' in resp.text


def test_workflow_tests_action_assert_anonymise(pub):
    create_superuser(pub)

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.workflow_tests.actions = [
        workflow_tests.AssertAnonymise(id='1'),
    ]
    testdef.store()

    app = login(get_app(pub))

    resp = app.get('/backoffice/forms/1/tests/%s/workflow/' % testdef.id)
    assert resp.pyquery('span.biglistitem--content')  # <span>, not <a>


def test_workflow_tests_action_assert_redirect(pub):
    create_superuser(pub)

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.workflow_tests.actions = [
        workflow_tests.AssertRedirect(id='1'),
    ]
    testdef.store()

    app = login(get_app(pub))

    resp = app.get('/backoffice/forms/1/tests/%s/workflow/' % testdef.id)
    assert 'not configured' in resp.text

    resp = resp.click(href=r'^1/$')
    resp.form['url'] = 'http://example.com'
    resp = resp.form.submit('submit').follow()

    assert 'not configured' not in resp.text
    assert 'http://example.com' in resp.text


def test_workflow_tests_action_assert_history_message(pub):
    create_superuser(pub)

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.workflow_tests.actions = [
        workflow_tests.AssertHistoryMessage(id='1'),
    ]
    testdef.store()

    app = login(get_app(pub))

    resp = app.get('/backoffice/forms/1/tests/%s/workflow/' % testdef.id)
    assert 'not configured' in resp.text

    resp = resp.click(href=r'^1/$')
    resp.form['message_strings$element0'] = 'Hello your form'
    resp = resp.form.submit('message_strings$add_element')

    resp.form['message_strings$element1'] = 'has been submitted'
    resp = resp.form.submit('submit').follow()

    assert 'not configured' not in resp.text
    assert 'Hello your form, has been s(…)' in resp.text

    # check legacy message attribute
    testdef.workflow_tests.actions = [
        workflow_tests.AssertHistoryMessage(id='1', message='Hello your form has been submitted'),
    ]
    testdef.store()

    resp = app.get('/backoffice/forms/1/tests/%s/workflow/' % testdef.id)
    assert 'not configured' not in resp.text
    assert 'Hello your form has been su(…)' in resp.text

    resp = resp.click(href=r'^1/$')
    assert resp.form['message_strings$element0'].value == 'Hello your form has been submitted'


def test_workflow_tests_action_assert_alert(pub):
    create_superuser(pub)

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.workflow_tests.actions = [
        workflow_tests.AssertAlert(id='1'),
    ]
    testdef.store()

    app = login(get_app(pub))

    resp = app.get('/backoffice/forms/1/tests/%s/workflow/' % testdef.id)
    assert 'not configured' in resp.text

    resp = resp.click(href=r'^1/$')
    resp.form['message'] = 'Hello your form has been submitted'
    resp = resp.form.submit('submit').follow()

    assert 'not configured' not in resp.text
    assert 'Hello your form has been su(…)' in resp.text


def test_workflow_tests_action_assert_criticality(pub):
    create_superuser(pub)

    workflow = Workflow(name='Workflow One')
    workflow.add_status(name='New status')
    workflow.store()

    formdef = FormDef()
    formdef.workflow_id = workflow.id
    formdef.name = 'test title'
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.workflow_tests.actions = [
        workflow_tests.AssertCriticality(id='1'),
    ]
    testdef.store()

    app = login(get_app(pub))

    resp = app.get('/backoffice/forms/1/tests/%s/workflow/' % testdef.id)
    assert 'not configured' in resp.text

    resp = resp.click(href=r'^1/$')
    assert 'Workflow has no criticality levels.' in resp.text

    workflow.criticality_levels = [
        WorkflowCriticalityLevel(name='green'),
        WorkflowCriticalityLevel(name='red'),
    ]
    workflow.store()

    resp = app.get('/backoffice/forms/1/tests/%s/workflow/1/' % testdef.id)
    resp.form['level_id'].select(text='green')
    resp = resp.form.submit('submit').follow()

    assert 'not configured' not in resp.text
    assert escape('Criticality is "green"') in resp.text


def test_workflow_tests_action_assert_backoffice_field(pub):
    create_superuser(pub)

    workflow = Workflow(name='Workflow One')
    workflow.add_status(name='New status')

    workflow.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(workflow)
    workflow.backoffice_fields_formdef.fields = [
        fields.StringField(id='bo1', label='Text'),
        fields.StringField(id='bo2', label='Text 2'),
    ]
    workflow.store()

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.workflow = workflow
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.workflow_tests.actions = [
        workflow_tests.AssertBackofficeFieldValues(id='1'),
    ]
    testdef.store()

    app = login(get_app(pub))

    resp = app.get('/backoffice/forms/1/tests/%s/workflow/1/' % testdef.id)
    assert resp.form['fields$element0$field_id'].options == [
        ('', False, ''),
        ('bo1', False, 'Text - Text (line)'),
        ('bo2', False, 'Text 2 - Text (line)'),
    ]

    resp.form['fields$element0$field_id'] = 'bo2'
    resp.form['fields$element0$value$value_template'] = 'xxx'
    resp = resp.form.submit('submit').follow()
    assert resp.pyquery('.biglistitem--content-details').text() == 'Text 2'

    assert_backoffice_field_values = TestDef.get(testdef.id).workflow_tests.actions[0]
    assert assert_backoffice_field_values.fields == [
        {'field_id': 'bo2', 'value': 'xxx'},
    ]

    resp = app.get('/backoffice/forms/1/tests/%s/workflow/1/' % testdef.id)
    assert resp.form['fields$element0$field_id'].value == 'bo2'
    assert resp.form['fields$element0$value$value_template'].value == 'xxx'
    resp = resp.form.submit('fields$add_element')
    resp.form['fields$element1$field_id'] = 'bo1'
    resp.form['fields$element1$value$value_template'] = 'yyy'
    resp = resp.form.submit('submit').follow()
    assert_backoffice_field_values = TestDef.get(testdef.id).workflow_tests.actions[0]
    assert assert_backoffice_field_values.fields == [
        {'field_id': 'bo2', 'value': 'xxx'},
        {'field_id': 'bo1', 'value': 'yyy'},
    ]
    assert resp.pyquery('.biglistitem--content-details').text() == 'Text 2, Text'

    resp = app.get('/backoffice/forms/1/tests/%s/workflow/1/' % testdef.id)
    resp.form['fields$element0$value$value_template'] = '{{ True }}'
    resp = resp.form.submit('submit').follow()

    assert_backoffice_field_values = TestDef.get(testdef.id).workflow_tests.actions[0]
    assert assert_backoffice_field_values.fields == [
        {'field_id': 'bo2', 'value': '{{ True }}'},
        {'field_id': 'bo1', 'value': 'yyy'},
    ]

    resp = app.get('/backoffice/forms/1/tests/%s/workflow/1/' % testdef.id)
    resp.form['fields$element0$value$value_template'] = '{{ [invalid }}'
    resp = resp.form.submit('submit')
    assert 'syntax error in Django template' in resp.text


def test_workflow_tests_action_assert_webservice_call(pub):
    create_superuser(pub)

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.workflow_tests.actions = [
        workflow_tests.AssertWebserviceCall(id='1'),
    ]
    testdef.store()

    app = login(get_app(pub))

    resp = app.get('/backoffice/forms/1/tests/%s/workflow/1/' % testdef.id)
    assert 'you must define corresponding webservice response' in resp.text

    resp = resp.click('Add webservice response')
    assert 'There are no webservice responses yet.' in resp.text

    response = WebserviceResponse()
    response.testdef_id = testdef.id
    response.name = 'Fake response'
    response.store()

    response2 = WebserviceResponse()
    response2.testdef_id = testdef.id
    response2.name = 'Fake response 2'
    response2.store()

    response3 = WebserviceResponse()
    response3.testdef_id = testdef.id + 1
    response3.name = 'Other response'
    response3.store()

    resp = app.get('/backoffice/forms/1/tests/%s/workflow/1/' % testdef.id)
    assert resp.form['webservice_response_uuid'].options == [
        (str(response.uuid), False, 'Fake response'),
        (str(response2.uuid), False, 'Fake response 2'),
    ]

    resp.form['webservice_response_uuid'] = response.uuid
    resp = resp.form.submit('submit').follow()

    assert 'Fake response' in resp.text
    assert 'Broken' not in resp.text

    assert_webservice_call = TestDef.get(testdef.id).workflow_tests.actions[0]
    assert assert_webservice_call.webservice_response_uuid == response.uuid

    response.remove_self()
    resp = app.get('/backoffice/forms/1/tests/%s/workflow/' % testdef.id)

    assert 'Broken, missing webservice response' in resp.text
    assert 'Fake response' not in resp.text


def test_workflow_tests_action_fill_form(pub):
    create_superuser(pub)

    data_source = {
        'type': 'jsonvalue',
        'value': json.dumps(
            [{'id': 'a', 'text': 'A', 'more': 'foo'}, {'id': 'b', 'text': 'B', 'more': 'bar'}]
        ),
    }

    test_user = pub.user_class(name='test user 1')
    test_user.email = 'test@example.com'
    test_user.test_uuid = '42'
    test_user.store()

    datasource = NamedDataSource(name='foo')
    datasource.data_source = {'type': 'wcs:users'}
    datasource.store()

    block = BlockDef()
    block.name = 'foobar'
    block.fields = [fields.StringField(id='1', label='Text inside block', varname='text')]
    block.store()

    workflow = Workflow(name='Workflow One')
    new_status = workflow.add_status(name='New status')
    end_status = workflow.add_status(name='End status')

    display_form = new_status.add_action('form', id='form')
    display_form.varname = 'foo'
    display_form.hide_submit_button = False
    display_form.formdef = WorkflowFormFieldsFormDef(item=display_form)
    display_form.formdef.fields = [
        fields.TitleField(id='0', label='The title'),
        fields.StringField(id='1', label='Text'),
        fields.StringField(id='2', label='Hidden', condition={'type': 'django', 'value': 'False'}),
        fields.BoolField(id='3', label='Bool'),
        fields.ItemField(id='4', label='Item', data_source=data_source),
        fields.ItemsField(id='5', label='Items', items=['a', 'b', 'c']),
        fields.DateField(id='6', label='Date'),
        fields.NumericField(id='7', label='Number'),
        fields.BlockField(
            id='8', label='Block Data', varname='blockdata', block_slug='foobar', max_items='3'
        ),
        fields.StringField(id='9', label='Hidden 2', condition={'type': 'django', 'value': 'False'}),
        fields.ItemField(id='10', label='User', data_source={'type': 'foo'}),
    ]

    display_form = end_status.add_action('form', id='form')
    display_form.varname = 'bar'
    display_form.formdef = WorkflowFormFieldsFormDef(item=display_form)

    # global action with form, should not appear in choices
    global_action = workflow.add_global_action('Add information')
    display_form = global_action.add_action('form', id='_display_form')
    display_form.varname = 'not-supported'
    display_form.formdef = WorkflowFormFieldsFormDef(item=display_form)
    display_form.formdef.fields = [
        fields.StringField(id='1', label='Test', varname='test'),
    ]

    workflow.store()

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.workflow = workflow
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.workflow_tests.actions = [
        workflow_tests.FillForm(id='1'),
        workflow_tests.ButtonClick(id='2'),
    ]
    testdef.store()

    app = login(get_app(pub))

    resp = app.get('/backoffice/forms/1/tests/%s/workflow/1/' % testdef.id)
    assert resp.form['form_action_id'].options == [
        ('1-form', False, 'New status - foo'),
        ('2-form', False, 'End status - bar'),
    ]

    resp.form['form_action_id'] = '1-form'
    resp = resp.form.submit('submit').follow()

    assert 'The title' in resp.text
    assert resp.form['f10'].options == [(str(test_user.id), False, 'test user 1')]

    # hidden fields are not displayed
    assert not 'f2' in resp.form.fields
    assert not 'f9' in resp.form.fields

    # add block field line
    resp = resp.form.submit('f8$add_element')

    resp.form['f1'] = 'Hello'
    resp.form['f3'].checked = True
    resp.form['f4'] = 'b'
    resp.form['f5$elementa'].checked = True
    resp.form['f5$elementc'].checked = True
    resp.form['f6'] = '2024-01-01'
    resp.form['f7'] = 42
    resp.form['f8$element0$f1'] = 'Hello again'
    resp.form['f8$element1$f1'] = 'Hello still'
    resp.form['f10'] = test_user.id
    resp = resp.form.submit('submit').follow()

    assert 'New status - foo' in resp.text

    testdef = TestDef.get(testdef.id)
    assert testdef.workflow_tests.actions[0].form_data == {
        '1': 'Hello',
        '3': True,
        '4': 'b',
        '4_display': 'B',
        '4_structured': {'id': 'b', 'more': 'bar', 'text': 'B'},
        '5': ['a', 'c'],
        '5_display': 'a, c',
        '5_structured': None,
        '6': '2024-01-01',
        '7': '42',
        '8': [{'text': 'Hello again'}, {'text': 'Hello still'}],
        '8_display': 'foobar, foobar',
        '10': '42',
        '10_display': 'test user 1',
        '10_structured': {
            'id': test_user.id,
            'text': 'test user 1',
            'user_admin_access': False,
            'user_backoffice_access': False,
            'user_display_name': 'test user 1',
            'user_email': 'test@example.com',
        },
    }

    resp = app.get('/backoffice/forms/1/tests/%s/workflow/1/fields/' % testdef.id)
    assert resp.form['f1'].value == 'Hello'
    assert resp.form['f3'].checked is True
    assert resp.form['f4'].value == 'b'
    assert resp.form['f5$elementa'].checked is True
    assert resp.form['f5$elementb'].checked is False
    assert resp.form['f5$elementc'].checked is True
    assert resp.form['f6'].value == '2024-01-01'
    assert resp.form['f7'].value == '42'
    assert resp.form['f8$element0$f1'].value == 'Hello again'

    # fields path for other actions is forbidden
    app.get('/backoffice/forms/1/tests/%s/workflow/2/fields/' % testdef.id, status=404)

    del new_status.items[0]
    workflow.store()

    resp = app.get('/backoffice/forms/1/tests/%s/workflow/' % testdef.id)
    assert 'Broken, missing form action' in resp.text


def test_workflow_tests_action_fill_form_feed_result(pub):
    create_superuser(pub)

    carddef = CardDef()
    carddef.name = 'Baz'
    carddef.digest_templates = {'default': 'plop'}
    carddef.fields = [
        fields.StringField(id='1', label='Text'),
    ]
    carddef.store()

    carddata = carddef.data_class()()
    carddata.just_created()
    carddata.data = {'1': 'test'}
    carddata.store()

    custom_view = pub.custom_view_class()
    custom_view.title = 'custom'
    custom_view.formdef = carddef
    custom_view.visibility = 'datasource'
    custom_view.filters = {'filter-1': True, 'filter-1-value': '{{ form_var_bo1 }}'}
    custom_view.store()

    workflow = Workflow(name='Workflow One')
    workflow.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(workflow)
    workflow.backoffice_fields_formdef.fields = [
        fields.StringField(id='1', label='Text', varname='bo1'),
    ]

    new_status = workflow.add_status(name='New status')

    set_backoffice_fields = new_status.add_action('set-backoffice-fields')
    set_backoffice_fields.fields = [
        {'field_id': '1', 'value': 'test'},
    ]

    display_form = new_status.add_action('form', id='form')
    display_form.varname = 'foo'
    display_form.formdef = WorkflowFormFieldsFormDef(item=display_form)
    display_form.formdef.fields = [
        fields.ItemField(id='1', label='Card', data_source={'type': 'carddef:baz:custom'}),
    ]

    workflow.store()

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.workflow = workflow
    formdef.store()

    testdef = TestDef.create_from_formdata(formdef, formdef.data_class()())
    testdef.workflow_tests.actions = [
        workflow_tests.AssertStatus(id='1', status_name='New status'),
        workflow_tests.FillForm(id='2'),
    ]
    testdef.store()

    app = login(get_app(pub))

    resp = app.get('/backoffice/forms/1/tests/%s/workflow/2/' % testdef.id)
    resp.form['form_action_id'] = '1-form'
    resp = resp.form.submit('submit').follow()

    assert resp.form['f1'].options == [('', False, '---')]

    # feed previous result while there is none
    resp = app.get('/backoffice/forms/1/tests/%s/workflow/2/' % testdef.id)
    resp.form['feed_last_test_result'] = True
    resp = resp.form.submit('submit').follow()

    assert 'Last test result could no be used' in resp.text

    assert resp.form['f1'].options == [('', False, '---')]

    # create test result
    resp = app.get('/backoffice/forms/1/tests/results/run').follow()

    resp = app.get('/backoffice/forms/1/tests/%s/workflow/2/fields/' % testdef.id)

    assert resp.form['f1'].options == [('1', False, 'plop')]

    display_form.formdef.fields[0].display_mode = 'autocomplete'
    workflow.store()

    resp = app.get('/backoffice/forms/1/tests/%s/workflow/2/fields/' % testdef.id)

    autocomplete_resp = app.get(resp.pyquery('select#form_f1').attr('data-select2-url'))
    assert autocomplete_resp.json == {'data': [{'id': 1, 'text': 'plop'}]}

    # create incomplete result
    testdef = TestDef.get(testdef.id)
    testdef.dependencies = ['xxx']
    testdef.store()

    resp = app.get('/backoffice/forms/1/tests/results/run').follow()
    assert 'Missing test dependency.' in resp.text

    resp = app.get('/backoffice/forms/1/tests/%s/workflow/2/fields/' % testdef.id)
    assert 'Last test result could no be used' in resp.text


def test_workflow_tests_action_fill_form_dependencies(pub):
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

    workflow = Workflow(name='Workflow One')
    workflow.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(workflow)
    workflow.backoffice_fields_formdef.fields = [
        fields.StringField(id='1', label='Text', varname='bo1'),
    ]

    new_status = workflow.add_status(name='New status')

    display_form = new_status.add_action('form', id='form')
    display_form.varname = 'foo'
    display_form.formdef = WorkflowFormFieldsFormDef(item=display_form)
    display_form.formdef.fields = [
        fields.ItemField(id='1', label='Card', data_source={'type': 'carddef:test-dependency'}),
    ]

    workflow.store()

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.workflow_id = workflow.id
    formdef.store()

    testdef = TestDef.create_from_formdata(formdef, formdef.data_class()())
    testdef.name = 'Current test'
    testdef.workflow_tests.actions = [
        workflow_tests.FillForm(id='1', form_action_id='1-form'),
    ]
    testdef.store()

    app = login(get_app(pub))

    resp = app.get('/backoffice/forms/1/tests/%s/workflow/1/fields/' % testdef.id)

    assert resp.form['f1'].options == [
        ('1', False, 'def'),
    ]

    testdef.dependencies = [dependency_testdef.uuid]
    testdef.store()

    resp = app.get('/backoffice/forms/1/tests/%s/workflow/1/fields/' % testdef.id)

    assert resp.form['f1'].options == [
        ('', False, '---'),
    ]

    # generate test result of dependency
    app.get('/backoffice/cards/%s/tests/results/run' % carddef.id).follow()

    resp = app.get('/backoffice/forms/1/tests/%s/workflow/1/fields/' % testdef.id)
    assert resp.form['f1'].options == [
        ('1', False, 'abc'),
    ]

    resp.form['f1'] = '1'
    resp.form.submit('submit').follow()

    testdef = TestDef.get(testdef.id)
    assert testdef.workflow_tests.actions[0].form_data == {
        '1': 'abc',
        '1_display': 'abc',
        '1_structured': {'id': 1, 'name': 'abc', 'text': 'abc'},
    }

    # check autocompletion
    display_form.formdef.fields[0].display_mode = 'autocomplete'
    workflow.store()

    resp = app.get('/backoffice/forms/1/tests/%s/workflow/1/fields/' % testdef.id)
    assert resp.form['f1'].options == []

    resp = app.get(resp.pyquery('select#form_f1').attr('data-select2-url'))
    assert resp.json['data'] == [
        {'id': 1, 'text': 'abc'},
    ]


def test_workflow_tests_action_fill_form_conditional_fields(pub):
    create_superuser(pub)

    workflow = Workflow(name='Workflow One')
    new_status = workflow.add_status(name='New status')

    display_form = new_status.add_action('form')
    display_form.varname = 'foo'
    display_form.formdef = WorkflowFormFieldsFormDef(item=display_form)
    display_form.formdef.fields = [
        fields.StringField(id='1', varname='text', label='Text'),
        fields.StringField(
            id='2',
            label='Conditional',
            condition={'type': 'django', 'value': 'form_workflow_form_foo_var_text == "show"'},
        ),
    ]

    workflow.store()

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.workflow = workflow
    formdef.store()

    testdef = TestDef.create_from_formdata(formdef, formdef.data_class()())
    testdef.workflow_tests.actions = [
        workflow_tests.FillForm(id='1', form_action_id='1-1'),
    ]
    testdef.store()

    app = login(get_app(pub))

    resp = app.get('/backoffice/forms/1/tests/%s/workflow/1/fields/' % testdef.id)

    assert not resp.pyquery('div[style="display: none"][data-field-id="1"]')
    assert resp.pyquery('div[style="display: none"][data-field-id="2"]')

    resp.form['f1'] = 'still hidden'
    resp.form.submit('submit').follow()

    resp = app.get('/backoffice/forms/1/tests/%s/workflow/1/fields/' % testdef.id)

    assert not resp.pyquery('div[style="display: none"][data-field-id="1"]')
    assert resp.pyquery('div[style="display: none"][data-field-id="2"]')

    resp.form['f1'] = 'show'
    resp = resp.form.submit('submit')

    assert 'There were errors processing your form.' in resp.text
    assert not resp.pyquery('div[style="display: none"][data-field-id="2"]')

    resp.form['f2'] = 'xxx'
    resp.form.submit('submit').follow()

    resp = app.get('/backoffice/forms/1/tests/%s/workflow/1/fields/' % testdef.id)

    assert resp.form['f1'].value == 'show'
    assert resp.form['f2'].value == 'xxx'

    # check live
    live_url = resp.html.find('form').attrs['data-live-url']
    live_resp = app.post(live_url, params=resp.form.submit_fields())
    assert live_resp.json['result']['2']['visible'] is True

    resp.form['f1'] = 'hide'
    live_resp = app.post(live_url, params=resp.form.submit_fields())
    assert live_resp.json['result']['2']['visible'] is False


def test_workflow_tests_action_fill_form_conditional_fields_feed_result(pub):
    create_superuser(pub)

    carddef = CardDef()
    carddef.name = 'My card'
    carddef.digest_templates = {'default': 'plop'}
    carddef.fields = [
        fields.StringField(id='1', label='Text'),
    ]
    carddef.store()

    workflow = Workflow(name='Workflow One')
    new_status = workflow.add_status(name='New status')

    create_carddata = new_status.add_action('create_carddata')
    create_carddata.formdef_slug = 'my-card'
    create_carddata.mappings = [
        Mapping(field_id='1', expression='abc'),
    ]

    display_form = new_status.add_action('form')
    display_form.varname = 'foo'
    display_form.formdef = WorkflowFormFieldsFormDef(item=display_form)
    display_form.formdef.fields = [
        fields.BoolField(id='1', varname='bool', label='Bool'),
        fields.StringField(
            id='2',
            label='Conditional',
            condition={
                'type': 'django',
                'value': 'not form_workflow_form_foo_var_bool and cards|objects:"my-card"|count',
            },
        ),
    ]

    workflow.store()

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.workflow = workflow
    formdef.store()

    testdef = TestDef.create_from_formdata(formdef, formdef.data_class()())
    testdef.workflow_tests.actions = [
        workflow_tests.FillForm(id='1', form_action_id='1-2', feed_last_test_result=True),
    ]
    testdef.store()

    app = login(get_app(pub))

    resp = app.get('/backoffice/forms/1/tests/%s/workflow/1/fields/' % testdef.id)
    assert resp.pyquery('div[style="display: none"][data-field-id="2"]')

    live_url = resp.html.find('form').attrs['data-live-url']
    live_resp = app.post(live_url, params=resp.form.submit_fields())
    assert live_resp.json['result']['2']['visible'] is False

    resp.form.submit('submit').follow()

    # create test result
    resp = app.get('/backoffice/forms/1/tests/results/run').follow()

    resp = app.get('/backoffice/forms/1/tests/%s/workflow/1/fields/' % testdef.id)
    assert not resp.pyquery('div[style="display: none"][data-field-id="2"]')

    live_url = resp.html.find('form').attrs['data-live-url']
    live_resp = app.post(live_url, params=resp.form.submit_fields())
    assert live_resp.json['result']['2']['visible'] is True

    resp = resp.form.submit('submit')
    assert 'There were errors processing your form.' in resp.text

    resp.form['f2'] = 'xxx'
    resp.form.submit('submit').follow()

    resp = app.get('/backoffice/forms/1/tests/%s/workflow/1/fields/' % testdef.id)
    assert resp.form['f2'].value == 'xxx'


def test_workflow_tests_action_assert_form_creation(pub):
    create_superuser(pub)

    workflow = Workflow(name='Workflow One')
    new_status = workflow.add_status(name='New status')
    workflow.store()

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.workflow_id = workflow.id
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.workflow_tests.actions = [
        workflow_tests.AssertFormCreation(id='1'),
    ]
    testdef.store()

    app = login(get_app(pub))

    resp = app.get('/backoffice/forms/1/tests/%s/workflow/1/' % testdef.id)
    assert 'Workflow has no form creation action.' in resp.text

    target_formdef = FormDef()
    target_formdef.name = 'To create'
    target_formdef.store()

    create_formdata = new_status.add_action('create_formdata')
    create_formdata.formdef_slug = target_formdef.url_name

    target_formdef = FormDef()
    target_formdef.name = 'To create 2'
    target_formdef.fields = [
        fields.StringField(id='1', label='Text'),
        fields.StringField(id='2', label='Text 2'),
    ]
    target_formdef.store()

    create_formdata = new_status.add_action('create_formdata')
    create_formdata.formdef_slug = target_formdef.url_name
    workflow.store()

    resp = app.get('/backoffice/forms/1/tests/%s/workflow/1/' % testdef.id)
    assert set(resp.form['formdef_slug'].options) == {
        ('to-create', False, 'To create'),
        ('to-create-2', False, 'To create 2'),
    }

    resp.form['formdef_slug'] = 'to-create-2'
    resp = resp.form.submit('submit')

    assert 'This action is configured in two steps.' in resp.text
    assert 'Leaving the field blank will empty the value' not in resp.text

    resp.form['mappings$element0$field_id'] = '1'
    resp.form['mappings$element0$expression$value_template'] = 'abc'

    resp = resp.form.submit('mappings$add_element')
    assert 'This action is configured in two steps.' not in resp.text

    resp.form['mappings$element1$field_id'] = '2'
    resp.form['mappings$element1$expression$value_template'] = 'def'

    resp = resp.form.submit('submit').follow()
    assert 'To create 2' in resp.text

    assert_form_creation = TestDef.get(testdef.id).workflow_tests.actions[0]
    assert assert_form_creation.mappings[0].field_id == '1'
    assert assert_form_creation.mappings[0].expression == 'abc'
    assert assert_form_creation.mappings[1].field_id == '2'
    assert assert_form_creation.mappings[1].expression == 'def'

    target_formdef.remove_self()
    resp = app.get('/backoffice/forms/1/tests/%s/workflow/' % testdef.id)

    assert 'To create 2' not in resp.text
    assert 'Broken, missing form' in resp.text


def test_workflow_tests_action_assert_card_creation(pub):
    create_superuser(pub)

    workflow = Workflow(name='Workflow One')
    new_status = workflow.add_status(name='New status')
    workflow.store()

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.workflow_id = workflow.id
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.workflow_tests.actions = [
        workflow_tests.AssertCardCreation(id='1'),
    ]
    testdef.store()

    app = login(get_app(pub))

    resp = app.get('/backoffice/forms/1/tests/%s/workflow/1/' % testdef.id)
    assert 'Workflow has no card creation action.' in resp.text

    target_carddef = CardDef()
    target_carddef.name = 'To create'
    target_carddef.store()

    create_carddata = new_status.add_action('create_carddata')
    create_carddata.formdef_slug = target_carddef.url_name
    workflow.store()

    resp = app.get('/backoffice/forms/1/tests/%s/workflow/1/' % testdef.id)
    assert resp.form['formdef_slug'].options == [
        ('to-create', False, 'To create'),
    ]


def test_workflow_tests_action_assert_user_can_view(pub):
    create_superuser(pub)

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.store()

    testdef = TestDef.create_from_formdata(formdef, formdef.data_class()())
    testdef.workflow_tests.actions = [
        workflow_tests.AssertUserCanView(id='1'),
    ]
    testdef.store()

    app = login(get_app(pub))

    resp = app.get('/backoffice/forms/1/tests/%s/workflow/' % testdef.id)
    assert 'not configured' in resp.text

    resp = resp.click(href=r'^1/$')
    assert 'There are no test users.' in resp.text

    test_user = pub.test_user_class(name='Test User')
    test_user.test_uuid = '42'
    test_user.store()

    resp = app.get('/backoffice/forms/1/tests/%s/workflow/1/' % testdef.id)
    resp.form['user_uuid'] = test_user.test_uuid
    resp = resp.form.submit('submit').follow()

    assert 'not configured' not in resp.text
    assert 'Test User' in resp.text

    test_user.remove_self()
    resp = app.get('/backoffice/forms/1/tests/%s/workflow/' % testdef.id)

    assert 'Broken, missing user' in resp.text


def test_workflow_tests_action_edit_form(pub):
    create_superuser(pub)

    test_user = pub.user_class(name='test user 1')
    test_user.email = 'test@example.com'
    test_user.test_uuid = '42'
    test_user.store()

    workflow = Workflow(name='Workflow One')
    new_status = workflow.add_status(name='New status')

    editable = new_status.add_action('editable')
    editable.label = 'Go to form edit'

    workflow.store()

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.fields = [
        fields.PageField(id='1', label='1st page'),
        fields.StringField(id='2', label='Text'),
        fields.PageField(id='3', label='2nd page'),
        fields.BoolField(id='4', label='Bool'),
    ]
    formdef.workflow = workflow
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {
        '2': 'abc',
        '4': True,
    }

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.workflow_tests.actions = [
        workflow_tests.EditForm(id='1'),
        workflow_tests.AssertEmail(id='2'),
    ]
    testdef.store()

    app = login(get_app(pub))

    resp = app.get('/backoffice/forms/1/tests/%s/workflow/1/' % testdef.id)

    resp.form['edit_action_id'].select(text='Go to form edit (in status New status)')
    resp = resp.form.submit('submit').follow()

    assert 'sidebar' not in resp.text
    assert '1st page' in resp.text
    assert resp.form['f2'].value == 'abc'

    resp.form['f2'] = 'def'
    resp = resp.form.submit('submit')

    assert '2nd page' in resp.text
    assert resp.form['f4'].checked is True

    resp.form['f4'] = False
    resp = resp.form.submit('submit').follow()

    assert escape('"Go to form edit" by submitter') in resp.text

    testdef = TestDef.get(testdef.id)
    assert testdef.workflow_tests.actions[0].form_data == {
        '2': 'def',
        '4': False,
    }

    resp = resp.click('Edit form')
    assert resp.form['edit_action_id'].options == [
        ('1-1', True, 'Go to form edit (in status New status)'),
    ]

    resp.form['who'] = 'other'
    resp.form['who_id'] = test_user.test_uuid
    resp = resp.form.submit('submit').follow()

    assert resp.form['f2'].value == 'def'

    resp = resp.form.submit('submit')

    assert resp.form['f4'].checked is False

    resp = resp.form.submit('submit').follow()

    assert escape('"Go to form edit" by test user 1') in resp.text

    del new_status.items[0]
    workflow.store()

    resp = app.get('/backoffice/forms/1/tests/%s/workflow/' % testdef.id)
    assert 'Broken, missing edit action' in resp.text

    # edit-form path for other actions is forbidden
    app.get('/backoffice/forms/1/tests/%s/workflow/2/edit-form/' % testdef.id, status=404)


def test_workflow_tests_action_edit_form_live_fields(pub):
    create_superuser(pub)

    workflow = Workflow(name='Workflow One')
    new_status = workflow.add_status(name='New status')

    new_status.add_action('editable')

    workflow.store()

    formdef = FormDef()
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
    ]
    formdef.workflow = workflow
    formdef.store()

    testdef = TestDef.create_from_formdata(formdef, formdef.data_class()())
    testdef.workflow_tests.actions = [
        workflow_tests.EditForm(id='1'),
    ]
    testdef.store()

    app = login(get_app(pub))

    resp = app.get('/backoffice/forms/1/tests/%s/workflow/1/' % testdef.id)

    resp.form['edit_action_id'].select(text='Edit Form (in status New status)')
    resp = resp.form.submit('submit').follow()

    resp.form['f1'] = 'ok'
    live_url = resp.html.find('form').attrs['data-live-url']
    live_resp = app.post(live_url, params=resp.form.submit_fields())
    assert live_resp.json['result']['2']['visible'] is True

    resp.form['f1'] = 'nok'
    live_resp = app.post(live_url, params=resp.form.submit_fields())
    assert live_resp.json['result']['2']['visible'] is False


def test_workflow_tests_action_edit_form_operation_modes(pub):
    create_superuser(pub)

    workflow = Workflow(name='Workflow One')
    new_status = workflow.add_status(name='New status')

    editable = new_status.add_action('editable')
    editable.operation_mode = 'single'
    editable.page_identifier = 'page1'

    workflow.store()

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.fields = [
        fields.PageField(id='1', label='1st page', varname='page1'),
        fields.StringField(id='2', label='Text'),
        fields.PageField(id='3', label='2nd page', varname='page2'),
        fields.BoolField(id='4', label='Bool'),
        fields.PageField(id='5', label='3rd page', varname='page3'),
        fields.StringField(id='6', label='Text 2'),
    ]
    formdef.workflow = workflow
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {
        '2': 'abc',
        '4': True,
        '6': 'def',
    }

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.workflow_tests.actions = [
        workflow_tests.EditForm(id='1'),
    ]
    testdef.store()

    app = login(get_app(pub))

    resp = app.get('/backoffice/forms/1/tests/%s/workflow/1/' % testdef.id)

    resp.form['edit_action_id'].select(text='Edit Form (in status New status)')
    resp = resp.form.submit('submit').follow()

    resp.form['f2'] = 'xxx'
    resp = resp.form.submit('submit').follow()

    assert escape('"Edit Form" by submitter') in resp.text

    testdef = TestDef.get(testdef.id)
    assert testdef.workflow_tests.actions[0].form_data == {
        '2': 'xxx',
    }

    editable.operation_mode = 'partial'
    editable.page_identifier = 'page2'
    workflow.store()

    resp = app.get('/backoffice/forms/1/tests/%s/workflow/1/edit-form/' % testdef.id)

    resp.form['f4'] = False
    resp = resp.form.submit('submit')

    resp.form['f6'] = 'yyy'
    resp = resp.form.submit('submit').follow()

    testdef = TestDef.get(testdef.id)
    assert testdef.workflow_tests.actions[0].form_data == {
        '4': False,
        '6': 'yyy',
    }


def test_workflow_tests_action_edit_form_feed_result(pub):
    create_superuser(pub)

    carddef = CardDef()
    carddef.name = 'My card'
    carddef.fields = [
        fields.StringField(id='1', label='Text'),
    ]
    carddef.store()

    workflow = Workflow(name='Workflow One')
    workflow.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(workflow)
    workflow.backoffice_fields_formdef.fields = [
        fields.StringField(id='bo1', label='Text', varname='bo1'),
    ]

    new_status = workflow.add_status(name='New status')

    set_backoffice_fields = new_status.add_action('set-backoffice-fields')
    set_backoffice_fields.fields = [
        {'field_id': 'bo1', 'value': 'test'},
    ]

    create_carddata = new_status.add_action('create_carddata')
    create_carddata.formdef_slug = 'my-card'
    create_carddata.mappings = [
        Mapping(field_id='1', expression='abc'),
    ]

    new_status.add_action('editable')

    workflow.store()

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.workflow = workflow
    formdef.fields = [
        fields.StringField(id='1', label='Text (no condition)'),
        fields.StringField(
            id='2', label='Text (if backoffice field)', condition={'type': 'django', 'value': 'form_var_bo1'}
        ),
        fields.StringField(
            id='3',
            label='Text (if card)',
            condition={'type': 'django', 'value': 'cards|objects:"my-card"|count'},
        ),
    ]
    formdef.store()

    testdef = TestDef.create_from_formdata(formdef, formdef.data_class()())
    testdef.workflow_tests.actions = [
        workflow_tests.EditForm(id='1', edit_action_id='1-3', feed_last_test_result=True),
    ]
    testdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/1/tests/%s/workflow/1/edit-form/' % testdef.id)

    assert 'Text (no condition)' in resp.text
    assert 'Text (if backoffice field)' not in resp.text
    assert 'Text (if card)' not in resp.text

    resp.form['f1'] = 'xxx'
    resp.form.submit('submit').follow()

    # create test result
    app.get('/backoffice/forms/1/tests/results/run').follow()

    resp = app.get('/backoffice/forms/1/tests/%s/workflow/1/edit-form/' % testdef.id)

    assert 'Text (no condition)' in resp.text
    assert 'Text (if backoffice field)' in resp.text
    assert 'Text (if card)' in resp.text
    assert resp.form['f1'].value == 'xxx'

    resp.form['f1'] = 'a'
    resp.form['f2'] = 'b'
    resp.form['f3'] = 'c'
    resp.form.submit('submit').follow()

    resp = app.get('/backoffice/forms/1/tests/%s/workflow/1/edit-form/' % testdef.id)

    assert resp.form['f1'].value == 'a'
    assert resp.form['f2'].value == 'b'
    assert resp.form['f3'].value == 'c'

    resp = app.get('/backoffice/forms/1/tests/%s/workflow/1/' % testdef.id)
    resp.form['feed_last_test_result'] = False
    resp = resp.form.submit('submit').follow()

    assert 'Text (no condition)' in resp.text
    assert 'Text (if backoffice field)' not in resp.text
    assert 'Text (if card)' not in resp.text


def test_workflow_tests_actions_reorder(pub):
    create_superuser(pub)

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.name = 'First test'
    testdef.workflow_tests.actions = [
        workflow_tests.ButtonClick(id='0', button_name='First'),
        workflow_tests.ButtonClick(id='1', button_name='Second'),
        workflow_tests.ButtonClick(id='2', button_name='Third'),
        workflow_tests.ButtonClick(id='3', button_name='Fourth'),
    ]
    testdef.store()

    app = login(get_app(pub))
    url = '/backoffice/forms/%s/tests/%s/workflow/update_order' % (formdef.id, testdef.id)

    # missing element in params: do nothing
    resp = app.get(url + '?order=0;3;1;2;')
    assert resp.json == {'success': 'ko'}

    # missing order in params: do nothing
    resp = app.get(url + '?element=0')
    assert resp.json == {'success': 'ko'}

    resp = app.get(url + '?order=0;3;1;2;&element=3')
    assert resp.json == {'success': 'ok'}
    testdef = TestDef.get(testdef.id)
    assert [x.id for x in testdef.workflow_tests.actions] == ['0', '3', '1', '2']

    # unknown id: ignored
    resp = app.get(url + '?order=0;1;2;3;4;&element=3')
    assert resp.json == {'success': 'ok'}
    testdef = TestDef.get(testdef.id)
    assert [x.id for x in testdef.workflow_tests.actions] == ['0', '1', '2', '3']

    # missing id: do nothing
    resp = app.get(url + '?order=0;3;1;&element=3')
    assert resp.json == {'success': 'ko'}
    testdef = TestDef.get(testdef.id)
    assert [x.id for x in testdef.workflow_tests.actions] == ['0', '1', '2', '3']


def test_workflow_tests_run(pub):
    create_superuser(pub)

    role = pub.role_class(name='test role')
    role.store()

    test_user = pub.user_class(name='test user')
    test_user.email = 'test@example.com'
    test_user.test_uuid = '42'
    test_user.roles = [role.id]
    test_user.store()

    workflow = Workflow(name='Workflow One')
    new_status = workflow.add_status(name='New status')

    sendmail = new_status.add_action('sendmail')
    sendmail.to = ['test@example.org']
    sendmail.subject = 'Hello'
    sendmail.body = 'abc'

    jump = new_status.add_action('choice')
    jump.label = 'Loop on status'
    jump.status = new_status.id
    jump.by = [role.id]

    workflow.store()

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.workflow_id = workflow.id
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.agent_id = test_user.test_uuid
    testdef.workflow_tests.actions = [
        workflow_tests.ButtonClick(id='1', button_name='Loop on status', who='receiver'),
    ]
    testdef.store()

    app = login(get_app(pub))

    resp = app.get('/backoffice/forms/1/tests/results/')
    resp = resp.click('Run tests').follow()

    assert len(resp.pyquery('tr')) == 1
    assert 'Success!' in resp.text

    # change button label
    jump.label = 'xxx'
    workflow.store()

    resp = app.get('/backoffice/forms/1/tests/results/')
    resp = resp.click('Run tests').follow()

    assert escape('Workflow error: Button "Loop on status" is not displayed.') in resp.text

    resp = resp.click('Display details')

    assert 'Form status when error occured: New status' in resp.text
    assert resp.pyquery('li#test-action').text() == 'Test action: Simulate click on action button'
    assert (
        resp.pyquery('li#test-action a').attr('href')
        == 'http://example.net/backoffice/forms/1/tests/%s/workflow/#1' % testdef.id
    )

    testdef.workflow_tests.actions = []
    testdef.store()

    resp = app.get(resp.request.url)
    assert 'Form status when error occured: New status' in resp.text
    assert resp.pyquery('li#test-action').text() == 'Test action: deleted'

    testdef.workflow_tests.actions = [
        workflow_tests.AssertEmail(id='1', body_strings=['def']),
    ]
    testdef.store()

    resp = app.get('/backoffice/forms/1/tests/results/')
    resp = resp.click('Run tests').follow()
    assert escape('No sent email matches expected criterias.') in resp.text

    resp = resp.click('Display details')
    assert 'Form status when error occured: New status' in resp.text
    assert escape('Sent email: body does not contain "def" (was "abc")') in resp.text
    assert resp.pyquery('li#test-action').text() == 'Test action: Email send'


def test_workflow_tests_run_saved_formdata(pub):
    role = pub.role_class(name='test role')
    role.store()

    user = create_superuser(pub)
    user.roles.append(role.id)
    user.store()

    workflow = Workflow(name='Workflow One')
    new_status = workflow.add_status(name='New status')
    end_status = workflow.add_status(name='End status')

    sendmail = new_status.add_action('sendmail')
    sendmail.to = ['test@example.org']
    sendmail.subject = 'Hello'
    sendmail.body = 'abc'

    jump = new_status.add_action('choice')
    jump.label = 'Button 1'
    jump.status = end_status.id
    jump.by = [role.id]

    workflow.store()

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.workflow_id = workflow.id
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.workflow_tests.actions = [
        workflow_tests.AssertEmail(id='1'),
    ]
    testdef.store()

    app = login(get_app(pub))

    resp = app.get('/backoffice/forms/1/tests/results/')
    resp = resp.click('Run tests').follow()

    assert 'Success!' in resp.text

    # test formdata is hidden from global listing
    resp = app.get('/backoffice/management/listing')
    assert len(resp.pyquery('table tbody tr')) == 0

    # which means it is not saved into wcs_all_forms table
    assert AnyFormData.count() == 0

    # test formdata is hidden from formdef listing
    resp = app.get('/backoffice/management/%s/' % formdef.url_name)
    assert len(resp.pyquery('table tbody tr')) == 0

    # which means it is hidden from sql methods
    assert formdef.data_class().count() == 0
    assert list(formdef.data_class().select_iterator()) == []

    # check test formdata really exists
    test_results = TestResults.select()[0]
    formdata_id = test_results.results[0].formdata_id

    # not in real formdata db
    with pytest.raises(KeyError):
        formdef.data_class().get(formdata_id)

    # but in test formdata db
    with testdef.use_test_objects():
        formdef.data_class().get(formdata_id)

    assert TestWorkflowTrace.count() == 1

    # clearing test results deletes test formdata
    TestResults.remove_object(test_results.id)

    with pytest.raises(KeyError):
        formdef.data_class().get(formdata_id)
    assert TestWorkflowTrace.count() == 0


def test_workflow_tests_run_webservice_call(pub):
    create_superuser(pub)

    workflow = Workflow(name='Workflow One')
    new_status = workflow.add_status(name='New status')

    wscall = new_status.add_action('webservice_call')
    wscall.url = 'http://example.com/json'

    workflow.store()

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.workflow_id = workflow.id
    formdef.store()

    testdef = TestDef.create_from_formdata(formdef, formdef.data_class()())
    testdef.store()

    response = WebserviceResponse()
    response.testdef_id = testdef.id
    response.name = 'Fake response'
    response.url = 'http://example.com/json'
    response.payload = '{}'
    response.store()

    testdef.workflow_tests.actions = [
        workflow_tests.AssertWebserviceCall(webservice_response_uuid=response.uuid),
    ]
    testdef.store()

    app = login(get_app(pub))

    resp = app.get('/backoffice/forms/1/tests/results/run').follow()
    assert 'Success!' in resp.text

    wscall.response_type = 'attachment'
    workflow.store()

    resp = app.get('/backoffice/forms/1/tests/results/run').follow()
    assert 'Workflow error: Webservice response Fake response was not used.' in resp.text


def test_workflow_tests_history_message_multiple_tests(pub):
    create_superuser(pub)

    workflow = Workflow(name='Workflow One')
    new_status = workflow.add_status(name='New status')

    register_comment = new_status.add_action('register-comment')
    register_comment.comment = 'Hello'

    register_comment = new_status.add_action('register-comment')
    register_comment.comment = 'Goodbye'

    workflow.store()

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.workflow_id = workflow.id
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()

    # create two identical tests
    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.workflow_tests.actions = [
        workflow_tests.AssertHistoryMessage(message='Hello'),
        workflow_tests.AssertHistoryMessage(message='Goodbye'),
    ]
    testdef.store()

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.workflow_tests.actions = [
        workflow_tests.AssertHistoryMessage(message='Hello'),
        workflow_tests.AssertHistoryMessage(message='Goodbye'),
    ]
    testdef.store()

    app = login(get_app(pub))

    resp = app.get('/backoffice/forms/1/tests/results/run').follow()
    assert resp.text.count('Success') == 2


def test_workfow_tests_creation_from_formdata(pub):
    create_superuser(pub)

    workflow = Workflow(name='Workflow One')
    new_status = workflow.add_status(name='New status')
    end_status = workflow.add_status(name='End status')

    sendmail = new_status.add_action('sendmail')
    sendmail.to = ['test@example.org']
    sendmail.subject = 'Hello'
    sendmail.body = 'abc'

    jump = new_status.add_action('jump')
    jump.status = end_status.id

    workflow.store()

    formdef = FormDef()
    formdef.workflow_id = workflow.id
    formdef.name = 'test title'
    formdef.store()

    app = login(get_app(pub))

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.receipt_time = make_aware(datetime.datetime(2022, 1, 1, 0, 0))
    formdata.store()
    formdata.perform_workflow()
    formdata.store()

    resp = app.get('/backoffice/forms/%s/tests/new' % formdef.id)
    resp.form['name'] = 'First test'
    resp.form['creation_mode'] = 'formdata-wf'
    resp.form['formdata'].select(text='1-1 - Unknown User - 2022-01-01 00:00')
    resp = resp.form.submit('submit').follow()

    testdef = TestDef.select()[0]
    assert len(testdef.workflow_tests.actions) == 2
    assert testdef.workflow_tests.actions[0].key == 'assert-status'
    assert testdef.workflow_tests.actions[0].status_name == 'End status'
    assert testdef.workflow_tests.actions[1].key == 'assert-email'

    resp = resp.click('Workflow tests')
    assert [x.text for x in resp.pyquery('ul li.workflow-test-action span.label')] == [
        'Form status',
        'Email send',
    ]

    resp = resp.click('Delete', index=0)
    resp = resp.form.submit('submit').follow()

    assert [x.text for x in resp.pyquery('ul li.workflow-test-action span.label')] == ['Email send']


def test_workflow_tests_result_coverage(pub):
    create_superuser(pub)

    workflow = Workflow(name='Workflow One')
    new_status = workflow.add_status(name='New')
    accepted_status = workflow.add_status(name='Accepted')
    rejected_status = workflow.add_status(name='Rejected')

    jump = new_status.add_action('choice')
    jump.label = 'Accept'
    jump.status = accepted_status.id
    jump.by = ['_submitter']

    register_comment = accepted_status.add_action('register-comment')
    register_comment.comment = 'Accepted'

    jump = new_status.add_action('choice')
    jump.label = 'Reject'
    jump.status = rejected_status.id
    jump.by = ['_submitter']

    register_comment = rejected_status.add_action('register-comment')
    register_comment.comment = 'Rejected'

    workflow.store()

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.workflow_id = workflow.id
    formdef.store()

    testdef = TestDef.create_from_formdata(formdef, formdef.data_class()())
    testdef.name = 'Test Accepted'
    testdef.workflow_tests.actions = [
        workflow_tests.ButtonClick(id='1', button_name='Accept', who='submitter'),
    ]
    testdef.store()

    app = login(get_app(pub))

    resp = app.get('/backoffice/forms/1/tests/results/run').follow()
    assert 'Workflow coverage: 50%' in resp.text

    resp = resp.click('details', href='workflow-coverage')

    assert len(resp.pyquery('.coverage')) == 7
    assert len(resp.pyquery('.covered')) == 2
    assert len(resp.pyquery('.not-covered')) == 2

    # New status
    assert resp.pyquery('.coverage--info:eq(0)').text() == 'Stasus are not considered in coverage.'
    # Accept button
    assert resp.pyquery('.coverage--info:eq(1)').text().splitlines() == [
        'Performed in tests:',
        'Test Accepted',
    ]
    # Rejected button
    assert resp.pyquery('.coverage--info:eq(2)').text() == 'Never performed.'
    # Accepted status
    assert resp.pyquery('.coverage--info:eq(3)').text() == 'Stasus are not considered in coverage.'
    # History message "Accepted"
    assert resp.pyquery('.coverage--info:eq(4)').text().splitlines() == [
        'Performed in tests:',
        'Test Accepted',
    ]
    # Rejected status
    assert resp.pyquery('.coverage--info:eq(5)').text() == 'Stasus are not considered in coverage.'
    # History message "Rejected"
    assert resp.pyquery('.coverage--info:eq(6)').text() == 'Never performed.'

    testdef = TestDef.create_from_formdata(formdef, formdef.data_class()())
    testdef.name = 'Test Rejected'
    testdef.workflow_tests.actions = [
        workflow_tests.ButtonClick(id='1', button_name='Reject', who='submitter'),
    ]
    testdef.store()

    resp = app.get('/backoffice/forms/1/tests/results/run').follow()
    assert 'Workflow coverage: 100%' in resp.text

    resp = resp.click('details', href='workflow-coverage')

    assert len(resp.pyquery('.coverage')) == 7
    assert len(resp.pyquery('.covered')) == 4
    assert len(resp.pyquery('.not-covered')) == 0

    # Rejected button
    assert resp.pyquery('.coverage--info:eq(2)').text().splitlines() == [
        'Performed in tests:',
        'Test Rejected',
    ]
    # History message "Rejected"
    assert resp.pyquery('.coverage--info:eq(6)').text().splitlines() == [
        'Performed in tests:',
        'Test Rejected',
    ]

    TestDef.remove_object(testdef.id)
    resp = app.get(resp.request.url)

    assert resp.pyquery('.coverage--info:eq(2)').text().splitlines() == [
        'Performed in tests:',
        'deleted test',
    ]
