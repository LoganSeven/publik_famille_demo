import datetime
import io
import json
import os
import uuid
from unittest import mock

import pytest
from django.utils.timezone import make_aware, now
from quixote.http_request import Upload as QuixoteUpload
from webtest import Upload

from wcs import fields, workflow_tests
from wcs.blocks import BlockDef
from wcs.carddef import CardDef
from wcs.formdef import FormDef
from wcs.qommon.form import UploadedFile
from wcs.qommon.http_request import HTTPRequest
from wcs.qommon.upload_storage import PicklableUpload
from wcs.sql import TransientData
from wcs.testdef import TestDef, TestResults, WebserviceResponse
from wcs.wf.create_formdata import Mapping
from wcs.wf.form import WorkflowFormFieldsFormDef
from wcs.wf.jump import JumpWorkflowStatusItem, _apply_timeouts
from wcs.workflow_tests import WorkflowTestError
from wcs.workflows import Workflow, WorkflowBackofficeFieldsFormDef, WorkflowCriticalityLevel

from .backoffice_pages.test_all import create_user
from .utilities import clean_temporary_pub, create_temporary_pub, get_app, login


@pytest.fixture
def pub():
    pub = create_temporary_pub()

    req = HTTPRequest(None, {'SCRIPT_NAME': '/', 'SERVER_NAME': 'example.net'})
    pub.set_app_dir(req)
    pub.cfg['identification'] = {'methods': ['password']}
    pub.write_cfg()

    pub.site_options.set('wscall-secrets', 'remote.example.net', 'yyy')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)

    pub.user_class.wipe()
    pub.test_user_class.wipe()
    pub.role_class.wipe()
    CardDef.wipe()
    FormDef.wipe()
    BlockDef.wipe()
    Workflow.wipe()
    return pub


def teardown_module(module):
    clean_temporary_pub()


@pytest.fixture(autouse=True)
def attach_results_on_test_run(monkeypatch):
    original_run = TestDef.run

    def mocked_run(self, objectdef):
        self.store()

        test_results = TestResults()
        test_results.object_type = objectdef.get_table_name()
        test_results.object_id = objectdef.id
        test_results.timestamp = now()
        test_results.reason = ''
        test_results.store()

        self.coverage = test_results.coverage
        self.result.test_results_id = test_results.id
        self.result.store()

        original_run(self, objectdef)

        test_results.set_coverage_percent(objectdef)
        test_results.store()

    monkeypatch.setattr(TestDef, 'run', mocked_run)


def test_workflow_tests_ignore_unsupported_items(pub, monkeypatch):
    workflow = Workflow(name='Workflow One')
    new_status = workflow.add_status(name='New status')
    end_status = workflow.add_status(name='End status')

    jump = new_status.add_action('jump')
    jump.status = end_status.id

    workflow.store()

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.workflow_id = workflow.id
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.workflow_tests.actions = [
        workflow_tests.AssertStatus(status_name='End status'),
    ]
    testdef.run(formdef)

    monkeypatch.delattr(JumpWorkflowStatusItem, 'perform_in_tests')
    with pytest.raises(WorkflowTestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'Form should be in status "End status" but is in status "New status".'


def test_workflow_tests_no_actions(pub):
    workflow = Workflow(name='Workflow One')
    workflow.add_status(name='New status')

    workflow.store()

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.workflow_id = workflow.id
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.workflow_tests.actions = []

    with mock.patch('wcs.workflow_tests.WorkflowTests.run') as mocked_run:
        testdef.run(formdef)
    mocked_run.assert_not_called()


def test_workflow_tests_action_not_configured(pub):
    workflow = Workflow(name='Workflow One')
    workflow.add_status(name='New status')

    workflow.store()

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.workflow_id = workflow.id
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.workflow_tests.actions = [
        workflow_tests.ButtonClick(),
    ]

    with mock.patch('wcs.workflow_tests.ButtonClick.perform') as mocked_perform:
        testdef.run(formdef)
    mocked_perform.assert_not_called()

    testdef.workflow_tests.actions = [
        workflow_tests.ButtonClick(button_name='xxx', who='submitter'),
    ]

    with mock.patch('wcs.workflow_tests.ButtonClick.perform') as mocked_perform:
        testdef.run(formdef)
    mocked_perform.assert_called_once()


def test_workflow_tests_new_action_id(pub):
    wf_tests = workflow_tests.WorkflowTests()

    for i in range(15):
        wf_tests.add_action(workflow_tests.ButtonClick)

    assert [x.id for x in wf_tests.actions] == [str(i) for i in range(1, 16)]


def test_workflow_tests_button_click(pub):
    role = pub.role_class(name='test role')
    role.store()
    user = pub.user_class(name='test user')
    user.test_uuid = '42'
    user.roles = [role.id]
    user.store()

    workflow = Workflow(name='Workflow One')
    new_status = workflow.add_status(name='New status')
    end_status = workflow.add_status(name='End status')

    jump = new_status.add_action('choice')
    jump.label = 'Go to end status'
    jump.status = end_status.id
    jump.by = [role.id]

    workflow.store()

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.workflow_id = workflow.id
    formdef.fields = [
        fields.StringField(id='1', label='Text', varname='text'),
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.data['1'] = 'end status'

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.agent_id = user.test_uuid
    testdef.workflow_tests.actions = [
        workflow_tests.ButtonClick(button_name='Go to end status', who='receiver'),
        workflow_tests.AssertStatus(status_name='End status'),
    ]
    testdef.run(formdef)

    # templated button label
    jump.label = 'Go to {{ form_var_text }}'
    workflow.store()
    formdef.refresh_from_storage()

    testdef.run(formdef)

    # change jump target status
    other_status = workflow.add_status(name='Other status')
    jump.status = other_status.id
    workflow.store()
    formdef.refresh_from_storage()

    with pytest.raises(WorkflowTestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'Form should be in status "End status" but is in status "Other status".'

    # hide button from test user
    other_role = pub.role_class(name='test role 2')
    other_role.store()
    jump.by = [other_role.id]
    workflow.store()
    formdef.refresh_from_storage()

    with pytest.raises(WorkflowTestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'Button "Go to end status" is not displayed.'

    # change button label
    jump.by = [role.id]
    jump.label = 'Go to xxx'
    workflow.store()
    formdef.refresh_from_storage()

    with pytest.raises(WorkflowTestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'Button "Go to end status" is not displayed.'


def test_workflow_tests_button_click_set_session_user(pub):
    role = pub.role_class(name='test role')
    role.store()
    user = pub.user_class(name='test user')
    user.test_uuid = '42'
    user.roles = [role.id]
    user.store()

    workflow = Workflow(name='Workflow One')
    new_status = workflow.add_status(name='New status')
    end_status = workflow.add_status(name='End status')

    jump = new_status.add_action('choice')
    jump.label = 'Go to end status'
    jump.status = end_status.id
    jump.by = ['logged-users']

    alert = end_status.add_action('displaymsg')
    alert.message = 'Alert!'
    alert.condition = {'type': 'django', 'value': 'session_user|has_role:"%s"' % role.name}

    workflow.store()

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.workflow_id = workflow.id
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.agent_id = user.test_uuid
    testdef.workflow_tests.actions = [
        workflow_tests.ButtonClick(button_name='Go to end status', who='receiver'),
        workflow_tests.AssertStatus(status_name='End status'),
        workflow_tests.AssertAlert(message='Alert!'),
    ]
    testdef.run(formdef)

    alert.condition = {'type': 'django', 'value': 'session_user|has_role:"xxx"'}
    workflow.store()
    formdef.refresh_from_storage()

    with pytest.raises(WorkflowTestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'No alert matching message.'


def test_workflow_tests_button_click_global_action(pub):
    role = pub.role_class(name='test role')
    role.store()
    user = pub.user_class(name='test user')
    user.test_uuid = '42'
    user.roles = [role.id]
    user.store()

    workflow = Workflow(name='Workflow One')
    workflow.add_status(name='New status')
    end_status = workflow.add_status(name='End status')

    global_action = workflow.add_global_action('Go to end status')
    global_action.triggers[0].roles = [role.id]

    sendmail = global_action.add_action('sendmail')
    sendmail.to = ['test@example.org']
    sendmail.subject = 'In new status'
    sendmail.body = 'xxx'

    jump = global_action.add_action('jump')
    jump.status = end_status.id

    workflow.store()

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.workflow_id = workflow.id
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.agent_id = user.test_uuid

    testdef.workflow_tests.actions = [
        workflow_tests.AssertStatus(status_name='End status'),
    ]

    with pytest.raises(WorkflowTestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'Form should be in status "End status" but is in status "New status".'

    testdef.workflow_tests.actions = [
        workflow_tests.ButtonClick(button_name='Go to end status', who='receiver'),
        workflow_tests.AssertEmail(),
        workflow_tests.AssertStatus(status_name='End status'),
    ]
    testdef.run(formdef)

    # hide button from test user
    user.roles = []
    user.store()

    with pytest.raises(WorkflowTestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'Button "Go to end status" is not displayed.'


def test_workflow_tests_button_click_who(pub):
    role = pub.role_class(name='test role')
    role.store()
    agent_user = pub.user_class(name='agent user')
    agent_user.test_uuid = '42'
    agent_user.roles = [role.id]
    agent_user.store()
    other_role = pub.role_class(name='other test role')
    other_role.store()
    other_user = pub.user_class(name='other user')
    other_user.test_uuid = '43'
    other_user.roles = [other_role.id]
    other_user.store()

    workflow = Workflow(name='Workflow One')
    new_status = workflow.add_status(name='New status')
    jump_by_unknown = workflow.add_status(name='Jump by unknown')
    jump_by_receiver = workflow.add_status(name='Jump by receiver')
    jump_by_submitter = workflow.add_status(name='Jump by submitter')
    jump_by_other_user = workflow.add_status(name='Jump by other user')

    jump = new_status.add_action('choice')
    jump.label = 'Go to next status'
    jump.status = jump_by_unknown.id
    jump.by = ['unknown']

    receiver_jump = new_status.add_action('choice')
    receiver_jump.label = 'Go to next status'
    receiver_jump.status = jump_by_receiver.id
    receiver_jump.by = ['_receiver']

    submitter_jump = new_status.add_action('choice')
    submitter_jump.label = 'Go to next status'
    submitter_jump.status = jump_by_submitter.id
    submitter_jump.by = ['_submitter']

    other_user_jump = new_status.add_action('choice')
    other_user_jump.label = 'Go to next status'
    other_user_jump.status = jump_by_other_user.id
    other_user_jump.by = [other_role.id]

    workflow.store()

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.workflow_id = workflow.id
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.agent_id = agent_user.test_uuid
    testdef.workflow_tests.actions = [
        workflow_tests.ButtonClick(button_name='Go to next status', who='receiver'),
        workflow_tests.AssertStatus(status_name='Jump by receiver'),
    ]
    testdef.run(formdef)

    testdef.agent_id = None
    with pytest.raises(WorkflowTestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'Broken, missing user'

    testdef.workflow_tests.actions = [
        workflow_tests.ButtonClick(button_name='Go to next status', who='submitter'),
        workflow_tests.AssertStatus(status_name='Jump by submitter'),
    ]
    testdef.run(formdef)

    testdef.workflow_tests.actions = [
        workflow_tests.ButtonClick(button_name='Go to next status', who='other', who_id=other_user.test_uuid),
        workflow_tests.AssertStatus(status_name='Jump by other user'),
    ]
    testdef.run(formdef)

    other_user.remove_self()
    with pytest.raises(WorkflowTestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'Broken, missing user'

    # submitter is anonymous
    submitter_jump.by = ['logged-users']
    workflow.store()
    formdef.refresh_from_storage()

    testdef.workflow_tests.actions = [
        workflow_tests.ButtonClick(button_name='Go to next status', who='submitter'),
        workflow_tests.AssertStatus(status_name='Jump by submitter'),
    ]
    with pytest.raises(WorkflowTestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'Button "Go to next status" is not displayed.'

    # not anonymous submitter
    submitter_user = pub.user_class(name='submitter user')
    submitter_user.email = 'test@example.com'
    submitter_user.store()

    formdata.user = submitter_user

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.agent_id = agent_user.test_uuid
    testdef.workflow_tests.actions = [
        workflow_tests.ButtonClick(button_name='Go to next status', who='submitter'),
        workflow_tests.AssertStatus(status_name='Jump by submitter'),
    ]
    testdef.run(formdef)


def test_workflow_tests_automatic_jump(pub):
    workflow = Workflow(name='Workflow One')
    new_status = workflow.add_status(name='New status')
    end_status = workflow.add_status(name='End status')

    jump = new_status.add_action('jump')
    jump.status = end_status.id

    workflow.store()

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.workflow_id = workflow.id
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.workflow_tests.actions = [
        workflow_tests.AssertStatus(status_name='End status'),
    ]
    testdef.run(formdef)

    new_end_status = workflow.add_status(name='New end status')

    jump = end_status.add_action('jump')
    jump.status = new_end_status.id

    workflow.store()
    formdef.refresh_from_storage()

    with pytest.raises(WorkflowTestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'Form should be in status "End status" but is in status "New end status".'


def test_workflow_tests_automatic_jump_condition(pub):
    workflow = Workflow(name='Workflow One')
    new_status = workflow.add_status(name='New status')
    frog_status = workflow.add_status(name='Frog status')
    bear_status = workflow.add_status(name='Bear status')

    jump = new_status.add_action('jump')
    jump.status = frog_status.id
    jump.condition = {'type': 'django', 'value': 'form_var_animal == "frog"'}

    jump = new_status.add_action('jump')
    jump.status = bear_status.id
    jump.condition = {'type': 'django', 'value': 'form_var_animal == "bear"'}

    workflow.store()

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.workflow_id = workflow.id
    formdef.fields = [
        fields.StringField(id='1', label='Text', varname='animal'),
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.data['1'] = 'frog'

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.workflow_tests.actions = [
        workflow_tests.AssertStatus(status_name='Frog status'),
    ]
    testdef.run(formdef)

    testdef.data['fields']['1'] = 'bear'

    with pytest.raises(WorkflowTestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'Form should be in status "Frog status" but is in status "Bear status".'


def test_workflow_tests_automatic_jump_timeout(pub, freezer):
    # When testing jump condition `form_receipt_datetime|age_in_days >= 1` we
    # skip time for 2 hours and check that the jump was not done : after 22h
    # we would skip to the next day, making the test fail.
    # freezing time before 22h
    freezer.move_to(now().replace(hour=10))
    workflow = Workflow(name='Workflow One')
    new_status = workflow.add_status(name='New status')
    stalled_status = workflow.add_status(name='Stalled')

    workflow.store()

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.workflow_id = workflow.id
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()

    testdef = TestDef.create_from_formdata(formdef, formdata)

    # no jumps configured, try skipping time anyway
    testdef.workflow_tests.actions = [
        workflow_tests.SkipTime(seconds=119 * 60),
    ]
    testdef.run(formdef)

    # configure jump
    jump = new_status.add_action('jump')
    jump.status = stalled_status.id
    jump.timeout = 120 * 60  # 2 hours
    jump.mode = 'timeout'
    jump.condition = {'type': 'django', 'value': 'form_receipt_datetime|age_in_days >= 1'}

    sendmail = new_status.add_action('sendmail')
    sendmail.to = ['test@example.org']
    sendmail.subject = 'In new status'
    sendmail.body = 'xxx'

    workflow.store()
    formdef.refresh_from_storage()

    testdef.workflow_tests.actions = [
        workflow_tests.AssertStatus(status_name='New status'),
        workflow_tests.SkipTime(seconds=119 * 60),
        workflow_tests.AssertStatus(status_name='New status'),
        workflow_tests.SkipTime(seconds=60),
        workflow_tests.AssertStatus(status_name='New status'),
        workflow_tests.SkipTime(seconds=24 * 60 * 60),
        workflow_tests.AssertStatus(status_name='Stalled'),
    ]
    testdef.run(formdef)

    jump.condition = {'type': 'django', 'value': 'form_receipt_datetime|age_in_hours >= 1'}
    workflow.store()
    formdef.refresh_from_storage()

    with pytest.raises(WorkflowTestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'Form should be in status "New status" but is in status "Stalled".'

    testdef.workflow_tests.actions = [
        workflow_tests.AssertStatus(status_name='New status'),
        workflow_tests.SkipTime(seconds=119 * 60),
        workflow_tests.AssertStatus(status_name='New status'),
        workflow_tests.SkipTime(seconds=60),
        workflow_tests.AssertStatus(status_name='Stalled'),
    ]
    testdef.run(formdef)


@pytest.mark.freeze_time('2024-02-19 12:00')
def test_workflow_tests_automatic_jump_timeout_after_form(pub):
    workflow = Workflow(name='Workflow One')
    new_status = workflow.add_status(name='New status')
    end_status = workflow.add_status(name='End status')

    alert = new_status.add_action('displaymsg')
    alert.message = 'Hello'

    jump = new_status.add_action('jump')
    jump.status = end_status.id
    jump.timeout = 60 * 60  # 1 hour
    jump.mode = 'timeout'
    jump.condition = {'type': 'django', 'value': 'now > form_receipt_datetime|add_hours:1'}

    workflow.store()

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.workflow_id = workflow.id
    formdef.store()

    testdef = TestDef.create_from_formdata(formdef, formdef.data_class()())
    testdef.workflow_tests.actions = [
        workflow_tests.AssertStatus(status_name='New status'),
        workflow_tests.AssertAlert(message='Hello'),
        workflow_tests.SkipTime(seconds=3 * 60 * 60),
        workflow_tests.AssertStatus(status_name='End status'),
    ]
    testdef.store()

    testdef.run(formdef)


@pytest.mark.freeze_time('2024-02-19 12:00')
def test_workflow_tests_global_action_timeout(pub):
    workflow = Workflow(name='Workflow One')
    new_status = workflow.add_status(name='New status')
    end_status = workflow.add_status(name='End status')

    global_action = workflow.add_global_action('Go to end status')
    trigger = global_action.append_trigger('timeout')
    trigger.anchor = 'creation'
    trigger.timeout = 1

    jump = global_action.add_action('jump')
    jump.status = end_status.id

    # add choice so that new_status is not flagged as endpoint
    choice = new_status.add_action('choice')
    choice.label = 'Go to end status'
    choice.status = end_status.id

    workflow.store()

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.workflow_id = workflow.id
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.workflow_tests.actions = [
        workflow_tests.AssertStatus(status_name='New status'),
        workflow_tests.SkipTime(seconds=60 * 60),  # 1 hour
        workflow_tests.AssertStatus(status_name='New status'),
        workflow_tests.SkipTime(seconds=24 * 60 * 60),  # 1 day
        workflow_tests.AssertStatus(status_name='End status'),
    ]

    testdef.run(formdef)

    # ensure mocks were cleared
    assert formdef.data_class().select() == []

    trigger.anchor = '1st-arrival'
    workflow.store()
    formdef.refresh_from_storage()

    testdef.run(formdef)

    trigger.anchor = 'latest-arrival'
    workflow.store()
    formdef.refresh_from_storage()

    testdef.run(formdef)

    trigger.anchor = 'template'
    trigger.anchor_template = '{{ form_receipt_date|date:"Y-m-d" }}'
    workflow.store()
    formdef.refresh_from_storage()

    testdef.run(formdef)

    trigger.anchor = 'finalized'
    workflow.store()
    formdef.refresh_from_storage()

    with pytest.raises(WorkflowTestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'Form should be in status "End status" but is in status "New status".'

    # remove choice so new status becomes endpoint
    new_status.items = [x for x in new_status.items if x.id != choice.id]
    workflow.store()
    formdef.refresh_from_storage()

    testdef.run(formdef)

    trigger.anchor = 'anonymisation'
    workflow.store()
    formdef.refresh_from_storage()

    with pytest.raises(WorkflowTestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'Form should be in status "End status" but is in status "New status".'

    new_status.add_action('anonymise')
    workflow.store()
    formdef.refresh_from_storage()

    testdef.run(formdef)


@mock.patch('wcs.qommon.emails.send_email')
def test_workflow_tests_sendmail(mocked_send_email, pub):
    role = pub.role_class(name='test role')
    role.store()
    user = pub.user_class(name='test user')
    user.test_uuid = '42'
    user.roles = [role.id]
    user.store()

    workflow = Workflow(name='Workflow One')
    new_status = workflow.add_status(name='New status')
    end_status = workflow.add_status(name='End status')

    sendmail = new_status.add_action('sendmail')
    sendmail.to = ['test@example.org', 'test2@example.org']
    sendmail.subject = 'In new status'
    sendmail.body = 'xxx'

    jump = new_status.add_action('choice')
    jump.label = 'Go to end status'
    jump.status = end_status.id
    jump.by = [role.id]

    sendmail = end_status.add_action('sendmail')
    sendmail.to = ['test@example.org']
    sendmail.subject = 'In  end\xa0status'
    sendmail.body = 'yyy \n\n xxx'

    workflow.store()

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.workflow_id = workflow.id
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.agent_id = user.test_uuid
    testdef.workflow_tests.actions = [
        workflow_tests.AssertEmail(
            addresses=['test@example.org'], subject_strings=['In new status'], body_strings=['xxx']
        ),
        workflow_tests.ButtonClick(button_name='Go to end status', who='receiver'),
        workflow_tests.AssertStatus(status_name='End status', who='receiver'),
        workflow_tests.AssertEmail(subject_strings=['In end status'], body_strings=['yyy xxx']),
    ]

    testdef.run(formdef)
    mocked_send_email.assert_not_called()

    testdef.workflow_tests.actions[-1].subject_strings[0] = 'In  end\xa0status'
    testdef.run(formdef)

    testdef.workflow_tests.actions.append(workflow_tests.AssertEmail())

    with pytest.raises(WorkflowTestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'No email was sent.'

    testdef.workflow_tests.actions = [
        workflow_tests.AssertEmail(subject_strings=['bla'], body_strings=['xxx']),
    ]

    with pytest.raises(WorkflowTestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'No sent email matches expected criterias.'
    assert 'Sent email: subject does not contain "bla" (was "In new status")' in excinfo.value.details

    testdef.workflow_tests.actions = [
        workflow_tests.AssertEmail(body_strings=['xxx', 'bli']),
    ]

    with pytest.raises(WorkflowTestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'No sent email matches expected criterias.'
    assert 'Sent email: body does not contain "bli" (was "xxx")' in excinfo.value.details

    testdef.workflow_tests.actions = [
        workflow_tests.AssertEmail(addresses=['test@example.org', 'other@example.org']),
    ]

    with pytest.raises(WorkflowTestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'No sent email matches expected criterias.'
    assert (
        'Sent email: was not addressed to other@example.org (recipients were test2@example.org, test@example.org)'
        in excinfo.value.details
    )

    testdef.workflow_tests.actions = [
        workflow_tests.ButtonClick(button_name='Go to end status', who='receiver'),
        workflow_tests.AssertEmail(subject_strings=['In new status'], body_strings=['xxx']),
        workflow_tests.AssertEmail(subject_strings=['end status'], body_strings=['yyy']),
    ]

    with pytest.raises(WorkflowTestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'No sent email matches expected criterias.'
    assert (
        'Sent email: subject does not contain "In new status" (was "In end status")' in excinfo.value.details
    )


def test_workflow_tests_sendmail_multiple_statuses(pub):
    role = pub.role_class(name='test role')
    role.emails_to_members = True
    role.store()

    role2 = pub.role_class(name='test role 2')
    role2.emails_to_members = True
    role2.store()

    user = pub.user_class(name='test user')
    user.test_uuid = '1'
    user.email = 'test@example.org'
    user.roles = [role.id, role2.id]
    user.store()

    user = pub.user_class(name='test user')
    user.test_uuid = '2'
    user.email = 'test2@example.org'
    user.roles = [role2.id]
    user.store()

    workflow = Workflow(name='Workflow One')
    new_status = workflow.add_status(name='New status')
    end_status = workflow.add_status(name='End status')

    sendmail = new_status.add_action('sendmail')
    sendmail.to = [role.id]
    sendmail.subject = 'In new status'
    sendmail.body = 'xxx'

    jump = new_status.add_action('jump')
    jump.status = end_status.id

    sendmail = end_status.add_action('sendmail')
    sendmail.to = [role2.id]
    sendmail.subject = 'In end status'
    sendmail.body = 'xxx'

    workflow.store()

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.workflow_id = workflow.id
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.agent_id = user.test_uuid
    testdef.workflow_tests.actions = [
        workflow_tests.AssertEmail(addresses=['test@example.org'], subject_strings=['In new status']),
        workflow_tests.AssertEmail(
            addresses=['test@example.org', 'test2@example.org'], subject_strings=['In end status']
        ),
    ]
    testdef.store()

    testdef.run(formdef)


def test_workflow_tests_sendmail_multiple_emails(pub):
    workflow = Workflow(name='Workflow One')
    new_status = workflow.add_status(name='New status')

    sendmail = new_status.add_action('sendmail')
    sendmail.to = ['test@example.org']
    sendmail.subject = 'Hello'
    sendmail.body = 'xxx'

    sendmail = new_status.add_action('sendmail')
    sendmail.to = ['test@example.org']
    sendmail.subject = 'Goodbye'
    sendmail.body = 'yyy'

    workflow.store()

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.workflow_id = workflow.id
    formdef.store()

    testdef = TestDef.create_from_formdata(formdef, formdef.data_class()())
    testdef.workflow_tests.actions = [
        workflow_tests.AssertEmail(subject_strings=['Goodbye']),
    ]
    testdef.store()

    testdef.run(formdef)

    testdef.workflow_tests.actions = [
        workflow_tests.AssertEmail(subject_strings=['Goodbye'], body_strings=['xxx']),
    ]
    with pytest.raises(WorkflowTestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'No sent email matches expected criterias.'
    assert excinfo.value.details == [
        'Sent email #1: subject does not contain "Goodbye" (was "Hello")',
        'Sent email #2: body does not contain "xxx" (was "yyy")',
        'Form status when error occured: New status',
    ]


def test_workflow_tests_sms(pub):
    pub.cfg['sms'] = {'sender': 'xxx', 'passerelle_url': 'http://passerelle.invalid/'}

    workflow = Workflow(name='Workflow One')
    new_status = workflow.add_status(name='New status')

    sendsms = new_status.add_action('sendsms')
    sendsms.to = ['0123456789']
    sendsms.body = 'Hello\n How are you'

    workflow.store()

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.workflow_id = workflow.id
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.workflow_tests.actions = [
        workflow_tests.AssertSMS(),
    ]

    testdef.run(formdef)

    testdef.workflow_tests.actions = [
        workflow_tests.AssertSMS(phone_numbers=['0123456789'], body='Hello How are you'),
    ]

    testdef.run(formdef)

    testdef.workflow_tests.actions.append(workflow_tests.AssertSMS())

    with pytest.raises(WorkflowTestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'No SMS was sent.'

    testdef.workflow_tests.actions = [
        workflow_tests.AssertSMS(phone_numbers=['0612345678']),
    ]

    with pytest.raises(WorkflowTestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'No sent SMS matches expected criterias.'
    assert 'Sent SMS: was not addressed to 0612345678 (recipients were 0123456789)' in excinfo.value.details

    testdef.workflow_tests.actions = [
        workflow_tests.AssertSMS(body='Goodbye'),
    ]

    with pytest.raises(WorkflowTestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'No sent SMS matches expected criterias.'
    assert 'Sent SMS: body does not contain "Goodbye" (was "Hello\n How are you")' in excinfo.value.details


def test_workflow_tests_anonymise(pub):
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
        workflow_tests.AssertAnonymise(),
    ]

    with pytest.raises(WorkflowTestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'Form was not anonymised.'

    anonymise_action = new_status.add_action('anonymise')
    workflow.store()
    formdef.refresh_from_storage()

    testdef.run(formdef)

    anonymise_action.mode = 'intermediate'
    workflow.store()
    formdef.refresh_from_storage()

    testdef.run(formdef)

    anonymise_action.mode = 'unlink_user'
    workflow.store()
    formdef.refresh_from_storage()

    testdef.run(formdef)


def test_workflow_tests_redirect(pub):
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
        workflow_tests.AssertRedirect(url='https://example.com/'),
    ]

    with pytest.raises(WorkflowTestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'No redirection occured.'

    redirect_action = new_status.add_action('redirect_to_url')
    redirect_action.url = 'https://test.com/'
    workflow.store()
    formdef.refresh_from_storage()

    with pytest.raises(WorkflowTestError) as excinfo:
        testdef.run(formdef)
    assert (
        str(excinfo.value)
        == 'Expected redirection to https://example.com/ but was redirected to https://test.com/.'
    )

    testdef.workflow_tests.actions = [
        workflow_tests.AssertRedirect(url='https://test.com/'),
    ]

    testdef.run(formdef)


def test_workflow_tests_history_message(pub):
    workflow = Workflow(name='Workflow One')
    new_status = workflow.add_status(name='New status')
    workflow.store()

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.workflow_id = workflow.id
    formdef.fields = [
        fields.StringField(id='1', label='Text', varname='text'),
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.data['1'] = '<test>'

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.workflow_tests.actions = [
        workflow_tests.AssertHistoryMessage(message='Hello 42 <test>'),
    ]

    with pytest.raises(WorkflowTestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'No history message.'

    register_comment = new_status.add_action('register-comment')
    register_comment.comment = 'Hello {{ 41|add:1 }} {{ form_var_text }}'
    workflow.store()
    formdef.refresh_from_storage()

    testdef.run(formdef)

    # raw HTML check is supported
    testdef.workflow_tests.actions = [
        workflow_tests.AssertHistoryMessage(message='<div>Hello 42'),
    ]

    testdef.run(formdef)

    # multiple checks
    testdef.workflow_tests.actions = [
        workflow_tests.AssertHistoryMessage(message_strings=['Hello', '42']),
    ]

    testdef.run(formdef)

    testdef.workflow_tests.actions = [
        workflow_tests.AssertHistoryMessage(message_strings=['Hello', '42', '43']),
    ]

    with pytest.raises(WorkflowTestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'No displayed history message has expected content.'
    assert (
        'Displayed history message: content does not contain "43" (was "Hello 42 <test>")'
        in excinfo.value.details
    )

    end_status = workflow.add_status(name='End status')
    jump = new_status.add_action('jump')
    jump.status = end_status.id

    register_comment = end_status.add_action('register-comment')
    register_comment.comment = ''

    workflow.store()
    formdef.refresh_from_storage()

    testdef.workflow_tests.actions = [
        workflow_tests.AssertHistoryMessage(message='Hello'),
        workflow_tests.AssertHistoryMessage(message='Goodbye'),
    ]

    with pytest.raises(WorkflowTestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'No history message.'

    register_comment.comment = '{{ form_var_xxx }}'
    workflow.store()
    formdef.refresh_from_storage()

    with pytest.raises(WorkflowTestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'No displayed history message has expected content.'
    assert 'Displayed history message: empty content' in excinfo.value.details


def test_workflow_tests_alert(pub):
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
        workflow_tests.AssertAlert(message='Héllo 42 abc'),
    ]

    with pytest.raises(WorkflowTestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'No alert matching message.'
    assert 'Displayed alerts: None' in excinfo.value.details
    assert 'Expected alert: Héllo 42 abc' in excinfo.value.details

    alert = new_status.add_action('displaymsg')
    alert.message = 'Héllo <strong>{{ 41|add:1 }}</strong>\n abc'
    workflow.store()
    formdef.refresh_from_storage()

    testdef.run(formdef)

    testdef.workflow_tests.actions = [
        workflow_tests.AssertAlert(message='Héllo 42\n abc'),
    ]

    testdef.run(formdef)

    testdef.workflow_tests.actions = [
        workflow_tests.AssertAlert(message='Hello 43'),
    ]

    with pytest.raises(WorkflowTestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'No alert matching message.'
    assert 'Displayed alerts: Héllo 42 abc' in excinfo.value.details
    assert 'Expected alert: Hello 43' in excinfo.value.details

    alert.message = '{{ form_var_xxx }}'
    workflow.store()
    formdef.refresh_from_storage()

    with pytest.raises(WorkflowTestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'No alert matching message.'


def test_workflow_tests_criticality(pub):
    workflow = Workflow(name='Workflow One')
    new_status = workflow.add_status(name='New status')
    green_level = WorkflowCriticalityLevel(name='green')
    red_level = WorkflowCriticalityLevel(name='red')
    workflow.criticality_levels = [green_level, red_level]
    workflow.store()

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.workflow_id = workflow.id
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.workflow_tests.actions = [
        workflow_tests.AssertCriticality(level_id=red_level.id),
    ]

    with pytest.raises(WorkflowTestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'Form should have criticality level "red" but has level "green".'

    new_status.add_action('modify_criticality')
    workflow.store()
    formdef.refresh_from_storage()

    testdef.run(formdef)

    workflow.criticality_levels = []
    workflow.store()
    formdef.refresh_from_storage()

    with pytest.raises(WorkflowTestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'Broken, missing criticality level'


def test_workflow_tests_backoffice_fields(pub):
    carddef = CardDef()
    carddef.name = 'Card title'
    carddef.fields = [
        fields.StringField(id='2', label='Text', varname='text'),
    ]
    carddef.digest_templates = {'default': '{{ form_var_text }}'}
    carddef.store()

    carddata = carddef.data_class()()
    carddata.just_created()
    carddata.data['2'] = 'My card'
    carddata.store()

    workflow = Workflow(name='Workflow One')
    workflow.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(workflow)
    workflow.backoffice_fields_formdef.fields = [
        fields.StringField(id='bo1', label='Text'),
        fields.StringField(id='bo2', label='Text 2'),
        fields.BoolField(id='bo3', label='Bool'),
        fields.ItemField(
            id='bo4',
            label='Item',
            data_source={'type': 'carddef:card-title'},
            display_mode='autocomplete',
        ),
        fields.NumericField(id='bo5', label='Number'),
    ]

    new_status = workflow.add_status(name='New status')
    set_backoffice_fields = new_status.add_action('set-backoffice-fields')
    set_backoffice_fields.fields = [
        {'field_id': 'bo2', 'value': '{{ form_var_text }}'},
        {'field_id': 'bo3', 'value': '{{ True }}'},
        {'field_id': 'bo4', 'value': str(carddata.id)},
        {'field_id': 'bo5', 'value': '{{ 42|add:1 }}'},
    ]

    workflow.store()

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.workflow_id = workflow.id
    formdef.fields = [
        fields.StringField(id='1', label='Text', varname='text'),
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.data['1'] = 'abc'

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.workflow_tests.actions = [
        workflow_tests.AssertBackofficeFieldValues(
            id='1',
            fields=[
                {'field_id': 'bo2', 'value': 'abc'},
                {'field_id': 'bo3', 'value': '{{ True }}'},
                {'field_id': 'bo4', 'value': 'My card'},
                {'field_id': 'bo5', 'value': '43'},
            ],
        ),
    ]

    testdef.run(formdef)

    assert TransientData.count() == 0
    assert pub.token_class.count() == 0

    testdef.workflow_tests.actions[0].fields[2]['value'] = 'xxx'
    testdef.store()

    with pytest.raises(WorkflowTestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'Wrong value for backoffice field "Item" (expected "xxx", got "My card").'

    testdef.data['fields']['1'] = 'def'

    with pytest.raises(WorkflowTestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'Wrong value for backoffice field "Text 2" (expected "abc", got "def").'

    workflow.backoffice_fields_formdef.fields = [
        fields.BoolField(id='bo3', label='Bool'),
    ]
    workflow.store()
    formdef.refresh_from_storage()

    with pytest.raises(WorkflowTestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'Field "bo2" is missing.'

    testdef.workflow_tests.actions = [
        workflow_tests.AssertBackofficeFieldValues(
            id='1',
            fields=[{'field_id': 'bo3', 'value': 'True'}],
        ),
    ]

    testdef.run(formdef)


def test_workflow_tests_backoffice_fields_export_to_model(pub):
    workflow = Workflow(name='Workflow One')
    workflow.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(workflow)
    workflow.backoffice_fields_formdef.fields = [
        fields.FileField(id='bo1', label='File'),
    ]

    new_status = workflow.add_status(name='New status')

    export_to = new_status.add_action('export_to_model')
    export_to.varname = 'doc'
    export_to.method = 'non-interactive'
    template_filename = os.path.join(os.path.dirname(__file__), 'template.odt')
    with open(template_filename, 'rb') as fd:
        template = fd.read()
    upload = QuixoteUpload('/foo/template.odt', content_type='application/octet-stream')
    upload.fp = io.BytesIO()
    upload.fp.write(template)
    upload.fp.seek(0)
    export_to.model_file = UploadedFile(pub.app_dir, None, upload)
    export_to.backoffice_filefield_id = 'bo1'

    workflow.store()

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.workflow_id = workflow.id
    formdef.store()

    testdef = TestDef.create_from_formdata(formdef, formdef.data_class()())
    testdef.workflow_tests.actions = [
        workflow_tests.AssertBackofficeFieldValues(
            id='1',
            fields=[
                {'field_id': 'bo1', 'value': 'template.pdf'},
            ],
        ),
    ]

    testdef.run(formdef)


def test_workflow_tests_dispatch(pub):
    role = pub.role_class(name='test role')
    role.store()
    user = pub.user_class(name='test user')
    user.test_uuid = '42'
    user.roles = [role.id]
    user.store()

    other_role = pub.role_class(name='test role')
    other_role.store()
    other_user = pub.user_class(name='test user')
    other_user.test_uuid = '43'
    other_user.roles = [other_role.id]
    other_user.store()

    workflow = Workflow(name='Workflow One')
    new_status = workflow.add_status(name='New status')
    middle_status = workflow.add_status(name='Middle status')
    end_status = workflow.add_status(name='End status')

    dispatch = new_status.add_action('dispatch')
    dispatch.dispatch_type = 'manual'
    dispatch.role_key = '_receiver'
    dispatch.role_id = role.id

    choice = new_status.add_action('choice')
    choice.label = 'Go to middle status'
    choice.status = middle_status.id
    choice.by = ['_receiver']

    dispatch = middle_status.add_action('dispatch')
    dispatch.dispatch_type = 'manual'
    dispatch.role_key = '_receiver'
    dispatch.role_id = other_role.id

    choice = middle_status.add_action('choice')
    choice.label = 'Go to end status'
    choice.status = end_status.id
    choice.by = ['_receiver']

    workflow.store()

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.workflow_id = workflow.id
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.workflow_tests.actions = [
        workflow_tests.AssertStatus(status_name='New status'),
        workflow_tests.ButtonClick(button_name='Go to middle status', who='other', who_id=user.test_uuid),
        workflow_tests.AssertStatus(status_name='Middle status'),
        workflow_tests.ButtonClick(button_name='Go to end status', who='other', who_id=other_user.test_uuid),
        workflow_tests.AssertStatus(status_name='End status'),
    ]

    testdef.run(formdef)

    testdef.workflow_tests.actions = [
        workflow_tests.AssertStatus(status_name='New status'),
        workflow_tests.ButtonClick(
            button_name='Go to middle status', who='other', who_id=other_user.test_uuid
        ),
    ]

    with pytest.raises(WorkflowTestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'Button "Go to middle status" is not displayed.'


def test_workflow_tests_dispatch_user(pub):
    user = pub.user_class(name='test user')
    user.email = 'test@example.com'
    user.test_uuid = '42'
    user.store()

    workflow = Workflow(name='Workflow One')
    new_status = workflow.add_status(name='New status')
    middle_status = workflow.add_status(name='Middle status')
    end_status = workflow.add_status(name='End status')

    choice = new_status.add_action('choice')
    choice.label = 'Go to middle status'
    choice.status = middle_status.id
    choice.by = ['logged-users']

    choice = middle_status.add_action('choice')
    choice.label = 'Go to end status'
    choice.status = end_status.id
    choice.by = ['_receiver']

    workflow.store()

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.workflow_id = workflow.id
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.workflow_tests.actions = [
        workflow_tests.AssertStatus(status_name='New status'),
        workflow_tests.ButtonClick(button_name='Go to middle status', who='other', who_id=user.test_uuid),
        workflow_tests.AssertStatus(status_name='Middle status'),
        workflow_tests.ButtonClick(button_name='Go to end status', who='other', who_id=user.test_uuid),
        workflow_tests.AssertStatus(status_name='End status'),
    ]

    with pytest.raises(WorkflowTestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'Button "Go to end status" is not displayed.'

    dispatch = middle_status.add_action('dispatch')
    dispatch.dispatch_type = 'manual'
    dispatch.role_key = '_receiver'
    dispatch.role_id = '{{ session_user_email }}'
    workflow.store()
    formdef.refresh_from_storage()

    testdef.run(formdef)


def test_workflow_tests_webservice(pub):
    workflow = Workflow(name='Workflow One')
    new_status = workflow.add_status(name='New status')
    end_status = workflow.add_status(name='End status')

    wscall = new_status.add_action('webservice_call')
    wscall.url = 'http://example.com/json'
    wscall.varname = 'test_webservice'
    wscall.qs_data = {'a': 'b'}

    jump = new_status.add_action('jump')
    jump.status = end_status.id
    jump.condition = {'type': 'django', 'value': 'form_workflow_data_test_webservice_response_foo == "bar"'}

    wscall = end_status.add_action('webservice_call')
    wscall.url = 'http://example.com/json'
    wscall.varname = 'test_webservice_2'
    wscall.method = 'POST'
    wscall.post_data = {'a': 'b'}

    workflow.store()

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.workflow_id = workflow.id
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.store()

    response = WebserviceResponse()
    response.testdef_id = testdef.id
    response.name = 'Fake response'
    response.url = 'http://example.com/json'
    response.payload = '{"foo": "foo"}'
    response.store()

    testdef.workflow_tests.actions = [
        workflow_tests.AssertStatus(status_name='End status'),
        workflow_tests.AssertWebserviceCall(webservice_response_uuid=response.uuid),
    ]

    with pytest.raises(WorkflowTestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'Form should be in status "End status" but is in status "New status".'

    response.payload = '{"foo": "bar"}'
    response.store()

    testdef.run(formdef)

    # response fits both wscall actions so it can be checked two times
    testdef.workflow_tests.actions = [
        workflow_tests.AssertWebserviceCall(webservice_response_uuid=response.uuid)
    ] * 2

    testdef.run(formdef)

    # but not three times
    testdef.workflow_tests.actions = [
        workflow_tests.AssertWebserviceCall(webservice_response_uuid=response.uuid)
    ] * 3

    with pytest.raises(WorkflowTestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'Webservice response Fake response was not used.'

    response.qs_data = {'a': 'b'}
    response.store()

    response2 = WebserviceResponse()
    response2.testdef_id = testdef.id
    response2.name = 'Fake response 2'
    response2.url = 'http://example.com/json'
    response2.payload = '{}'
    response2.method = 'POST'
    response2.store()

    testdef.workflow_tests.actions = [
        workflow_tests.AssertWebserviceCall(webservice_response_uuid=response.uuid),
        workflow_tests.AssertWebserviceCall(webservice_response_uuid=response2.uuid),
    ]

    testdef.run(formdef)

    testdef.workflow_tests.actions = reversed(testdef.workflow_tests.actions)

    testdef.run(formdef)

    testdef.workflow_tests.actions = [
        workflow_tests.AssertWebserviceCall(webservice_response_uuid=response.uuid),
        workflow_tests.AssertWebserviceCall(webservice_response_uuid=response.uuid),
    ]

    with pytest.raises(WorkflowTestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'Webservice response Fake response was not used.'

    testdef.workflow_tests.actions = [
        workflow_tests.AssertWebserviceCall(webservice_response_uuid='xxx'),
    ]

    with pytest.raises(WorkflowTestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'Broken, missing webservice response'


def test_workflow_tests_webservice_status_jump(pub):
    workflow = Workflow(name='Workflow One')
    new_status = workflow.add_status(name='New status')
    end_status = workflow.add_status(name='Error status')

    wscall = new_status.add_action('webservice_call')
    wscall.url = 'http://example.com/json'
    wscall.varname = 'test_webservice'
    wscall.action_on_4xx = end_status.id

    workflow.store()

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.workflow_id = workflow.id
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.store()

    response = WebserviceResponse()
    response.testdef_id = testdef.id
    response.name = 'Fake response'
    response.url = 'http://example.com/json'
    response.payload = '{"foo": "foo"}'
    response.store()

    testdef.workflow_tests.actions = [
        workflow_tests.AssertStatus(status_name='New status'),
        workflow_tests.AssertWebserviceCall(webservice_response_id=response.id),
    ]

    testdef.run(formdef)

    response.status_code = 400
    response.store()

    testdef.workflow_tests.actions = [
        workflow_tests.AssertStatus(status_name='End status'),
        workflow_tests.AssertWebserviceCall(webservice_response_id=response.id),
    ]


def test_workflow_tests_fill_form(pub):
    workflow = Workflow(name='Workflow One')
    new_status = workflow.add_status(name='New status')
    end_status = workflow.add_status(name='End status')

    display_form = new_status.add_action('form', id='_display_form')
    display_form.by = ['_submitter']
    display_form.varname = 'foo'
    display_form.hide_submit_button = False
    display_form.formdef = WorkflowFormFieldsFormDef(item=display_form)
    display_form.formdef.fields = [
        fields.StringField(id='1', label='Text', varname='text'),
    ]

    jump = new_status.add_action('jumponsubmit')
    jump.status = end_status.id

    choice = new_status.add_action('choice')
    choice.label = 'Manual jump'
    choice.status = end_status.id
    choice.by = ['_submitter']

    register_comment = end_status.add_action('register-comment')
    register_comment.comment = '{{ form_workflow_form_foo_var_text }}'

    workflow.store()

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.workflow_id = workflow.id
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.workflow_tests.actions = [
        workflow_tests.FillForm(
            form_action_id='%s-%s' % (new_status.id, display_form.id),
            form_data={'1': 'Hello'},
        ),
        workflow_tests.ButtonClick(button_name='Submit', who='submitter'),
        workflow_tests.AssertStatus(status_name='End status'),
        workflow_tests.AssertHistoryMessage(message='Hello'),
    ]

    testdef.run(formdef)

    testdef.workflow_tests.actions[1].button_name = 'Manual jump'
    workflow.store()
    formdef.refresh_from_storage()

    testdef.run(formdef)

    display_form.formdef.fields[0].validation = {'type': 'digits'}
    workflow.store()
    formdef.refresh_from_storage()

    with pytest.raises(WorkflowTestError) as excinfo:
        testdef.run(formdef)
    assert (
        str(excinfo.value)
        == 'Invalid value "Hello" for field "Text": You should enter digits only, for example: 123.'
    )

    display_form.condition = {'type': 'django', 'value': 'False'}
    workflow.store()
    formdef.refresh_from_storage()

    with pytest.raises(WorkflowTestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'Form is not displayed.'

    display_form.by = ['_receiver']
    workflow.store()
    formdef.refresh_from_storage()

    with pytest.raises(WorkflowTestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'Form is not accessible by user "submitter".'

    # try to fill form from wrong status
    testdef.workflow_tests.actions = [
        workflow_tests.ButtonClick(button_name='Manual jump', who='submitter'),
        workflow_tests.FillForm(form_action_id='%s-%s' % (new_status.id, display_form.id)),
        workflow_tests.ButtonClick(button_name='Submit', who='submitter'),
    ]

    with pytest.raises(WorkflowTestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'Form is not in the status containing form fill action.'
    assert excinfo.value.details == [
        'Status containing action: New status',
        'Form status when error occured: End status',
    ]

    del new_status.items[0]
    workflow.store()
    formdef.refresh_from_storage()

    with pytest.raises(WorkflowTestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'Broken, missing form action'

    # add a different action in place of the workflow form action
    new_status.add_action('anonymise', id='_display_form')
    new_status.items = [new_status.items[-1]] + new_status.items[:-1]
    workflow.store()
    formdef.refresh_from_storage()

    with pytest.raises(WorkflowTestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'Broken, missing form action'

    testdef.workflow_tests.actions = [x for x in testdef.workflow_tests.actions if x.key != 'button-click']

    with pytest.raises(WorkflowTestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'Form fill must be followed by "button click" action.'


def test_workflow_tests_fill_form_multiple_actions(pub):
    workflow = Workflow(name='Workflow One')
    new_status = workflow.add_status(name='New status')
    end_status = workflow.add_status(name='End status')

    display_form = new_status.add_action('form', id='_display_form_1')
    display_form.by = ['_submitter']
    display_form.varname = 'foo'
    display_form.hide_submit_button = False
    display_form.formdef = WorkflowFormFieldsFormDef(item=display_form)
    display_form.formdef.fields = [
        fields.StringField(id='1', label='Text', varname='text'),
    ]

    display_form2 = new_status.add_action('form', id='_display_form_2')
    display_form2.by = ['_submitter']
    display_form2.varname = 'bar'
    display_form2.hide_submit_button = False
    display_form2.formdef = WorkflowFormFieldsFormDef(item=display_form2)
    display_form2.formdef.fields = [
        fields.StringField(id='1', label='Text', varname='text', required='optional'),
    ]

    jump = new_status.add_action('jumponsubmit')
    jump.status = end_status.id

    register_comment = end_status.add_action('register-comment')
    register_comment.comment = '{{ form_workflow_form_foo_var_text }} {{ form_workflow_form_bar_var_text }}'

    workflow.store()

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.workflow_id = workflow.id
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.workflow_tests.actions = [
        workflow_tests.FillForm(
            form_action_id='%s-%s' % (new_status.id, display_form.id),
            form_data={'1': 'Hello'},
        ),
        workflow_tests.FillForm(
            form_action_id='%s-%s' % (new_status.id, display_form2.id),
            form_data={'1': ''},
        ),
        workflow_tests.ButtonClick(button_name='Submit', who='submitter'),
        workflow_tests.AssertStatus(status_name='End status'),
        workflow_tests.AssertHistoryMessage(message='Hello world'),
    ]

    with pytest.raises(WorkflowTestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'No displayed history message has expected content.'

    testdef.workflow_tests.actions[1].form_data['1'] = 'world'
    workflow.store()
    formdef.refresh_from_storage()

    testdef.run(formdef)


def test_workflow_tests_fill_form_multiple_times(pub):
    workflow = Workflow(name='Workflow One')
    new_status = workflow.add_status(name='New status')

    display_form = new_status.add_action('form', id='_display_form_1')
    display_form.by = ['_submitter']
    display_form.varname = 'foo'
    display_form.hide_submit_button = False
    display_form.formdef = WorkflowFormFieldsFormDef(item=display_form)
    display_form.formdef.fields = [
        fields.StringField(id='1', label='Text', varname='text'),
    ]

    jump = new_status.add_action('jumponsubmit')
    jump.status = new_status.id

    register_comment = new_status.add_action('register-comment')
    register_comment.comment = '{{ form_workflow_form_foo_var_text|default:"No text" }}'

    workflow.store()

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.workflow_id = workflow.id
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.workflow_tests.actions = [
        workflow_tests.AssertHistoryMessage(message='No text'),
        workflow_tests.FillForm(
            form_action_id='%s-%s' % (new_status.id, display_form.id),
            form_data={'1': 'Some text'},
        ),
        workflow_tests.ButtonClick(button_name='Submit', who='submitter'),
        workflow_tests.AssertHistoryMessage(message='Some text'),
        workflow_tests.FillForm(
            form_action_id='%s-%s' % (new_status.id, display_form.id),
            form_data={'1': 'Some other text'},
        ),
        workflow_tests.ButtonClick(button_name='Submit', who='submitter'),
        workflow_tests.AssertHistoryMessage(message='Some other text'),
    ]

    testdef.run(formdef)


def test_workflow_tests_fill_form_conditional_fields(pub):
    workflow = Workflow(name='Workflow One')
    new_status = workflow.add_status(name='New status')

    display_form = new_status.add_action('form', id='_display_form')
    display_form.by = ['_submitter']
    display_form.varname = 'foo'
    display_form.hide_submit_button = False
    display_form.formdef = WorkflowFormFieldsFormDef(item=display_form)
    display_form.formdef.fields = [
        fields.StringField(id='1', label='Workflow Form Text', varname='wf_form_text'),
        fields.StringField(
            id='2',
            label='Conditional Field',
            condition={
                'type': 'django',
                'value': 'form_var_text and form_workflow_form_foo_var_wf_form_text',
            },
        ),
    ]

    workflow.store()

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.workflow_id = workflow.id
    formdef.fields = [
        fields.StringField(id='1', label='Text', varname='text'),
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.data = {}

    testdef = TestDef.create_from_formdata(formdef, formdata)
    form_action = workflow_tests.FillForm(
        form_action_id='%s-%s' % (new_status.id, display_form.id),
        form_data={'2': 'xxx'},
    )
    testdef.workflow_tests.actions = [
        form_action,
        workflow_tests.ButtonClick(button_name='Submit', who='submitter'),
    ]

    with pytest.raises(WorkflowTestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'Tried to fill field "Conditional Field" but it is hidden.'

    formdata.data = {'1': 'xxx'}
    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.workflow_tests.actions = [
        form_action,
        workflow_tests.ButtonClick(button_name='Submit', who='submitter'),
    ]
    formdef.refresh_from_storage()

    with pytest.raises(WorkflowTestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'Tried to fill field "Conditional Field" but it is hidden.'

    form_action.form_data['1'] = 'xxx'
    workflow.store()
    formdef.refresh_from_storage()

    testdef.run(formdef)


@pytest.mark.parametrize(
    'field_class,field_data,field_value,field_kwargs',
    [
        (fields.DateField, '2024-01-01', '"2024-01-01"', {}),
        (fields.NumericField, 42, '42', {}),
        (fields.BoolField, True, 'True', {}),
        (fields.ItemField, 'xxx', '"xxx"', {'items': ['xxx']}),
        (fields.ItemsField, ['xxx', 'yyy'], '"xxx, yyy"', {'items': ['xxx', 'yyy']}),
    ],
)
def test_workflow_tests_fill_form_different_field_types(
    field_class, field_data, field_value, field_kwargs, pub
):
    workflow = Workflow(name='Workflow One')
    new_status = workflow.add_status(name='New status')
    end_status = workflow.add_status(name='End status')

    display_form = new_status.add_action('form', id='_display_form')
    display_form.by = ['_submitter']
    display_form.varname = 'foo'
    display_form.hide_submit_button = False
    display_form.formdef = WorkflowFormFieldsFormDef(item=display_form)
    field_id = str(uuid.uuid4())
    display_form.formdef.fields = [
        field_class(id=field_id, label='Test', varname='test', **field_kwargs),
    ]

    jump = new_status.add_action('jumponsubmit')
    jump.status = end_status.id
    jump.condition = {'type': 'django', 'value': 'form_workflow_form_foo_var_test == %s' % field_value}

    workflow.store()

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.workflow_id = workflow.id
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()

    testdef = TestDef.create_from_formdata(formdef, formdata)
    fill_form_action = workflow_tests.FillForm(
        form_action_id='%s-%s' % (new_status.id, display_form.id),
        form_data={},
    )
    testdef.workflow_tests.actions = [
        fill_form_action,
        workflow_tests.ButtonClick(button_name='Submit', who='submitter'),
        workflow_tests.AssertStatus(status_name='End status'),
    ]

    with pytest.raises(WorkflowTestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'Form should be in status "End status" but is in status "New status".'

    fill_form_action.form_data = {field_id: field_data}
    formdef.refresh_from_storage()
    testdef.run(formdef)


def test_workflow_tests_fill_form_block_field(pub):
    block = BlockDef()
    block.name = 'foobar'
    block.fields = [fields.ItemField(id='1', label='Test', varname='item', items=['foo', 'bar', 'baz'])]
    block.store()

    workflow = Workflow(name='Workflow One')
    new_status = workflow.add_status(name='New status')
    end_status = workflow.add_status(name='End status')

    display_form = new_status.add_action('form', id='_display_form')
    display_form.by = ['_submitter']
    display_form.varname = 'foo'
    display_form.hide_submit_button = False
    display_form.formdef = WorkflowFormFieldsFormDef(item=display_form)
    display_form.formdef.fields = [
        fields.BlockField(
            id='1', label='Block Data', varname='blockdata', block_slug='foobar', max_items='3'
        ),
    ]

    jump = new_status.add_action('jumponsubmit')
    jump.status = end_status.id
    jump.condition = {'type': 'django', 'value': 'form_workflow_form_foo_var_blockdata_0_item == "bar"'}

    workflow.store()

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.workflow_id = workflow.id
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()

    testdef = TestDef.create_from_formdata(formdef, formdata)
    fill_form_action = workflow_tests.FillForm(
        form_action_id='%s-%s' % (new_status.id, display_form.id),
        form_data={'1': [{'item': 'foo'}]},
    )
    testdef.workflow_tests.actions = [
        fill_form_action,
        workflow_tests.ButtonClick(button_name='Submit', who='submitter'),
        workflow_tests.AssertStatus(status_name='End status'),
    ]

    with pytest.raises(WorkflowTestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'Form should be in status "End status" but is in status "New status".'

    fill_form_action.form_data = {'1': [{'item': 'bar'}]}
    formdef.refresh_from_storage()
    testdef.run(formdef)


def test_workflow_tests_fill_form_structured_value(pub):
    data_source = {
        'type': 'jsonvalue',
        'value': json.dumps(
            [{'id': '1', 'text': 'un', 'more': 'foo'}, {'id': '2', 'text': 'deux', 'more': 'bar'}]
        ),
    }

    workflow = Workflow(name='Workflow One')
    new_status = workflow.add_status(name='New status')
    end_status = workflow.add_status(name='End status')

    display_form = new_status.add_action('form', id='_display_form')
    display_form.by = ['_submitter']
    display_form.varname = 'foo'
    display_form.hide_submit_button = False
    display_form.formdef = WorkflowFormFieldsFormDef(item=display_form)
    display_form.formdef.fields = [
        fields.ItemField(id='1', label='Test', varname='item', data_source=data_source),
    ]

    jump = new_status.add_action('jumponsubmit')
    jump.status = end_status.id
    jump.condition = {'type': 'django', 'value': 'form_workflow_form_foo_var_item_more == "bar"'}

    workflow.store()

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.workflow_id = workflow.id
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()

    testdef = TestDef.create_from_formdata(formdef, formdata)
    fill_form_action = workflow_tests.FillForm(
        form_action_id='%s-%s' % (new_status.id, display_form.id),
        form_data={
            '1': '2',
            '1_display': 'deux',
            '1_structured': {'id': '2', 'text': 'deux', 'more': 'bar'},
        },
    )
    testdef.workflow_tests.actions = [
        fill_form_action,
        workflow_tests.ButtonClick(button_name='Submit', who='submitter'),
        workflow_tests.AssertStatus(status_name='End status'),
    ]

    testdef.run(formdef)


def test_workflow_tests_fill_comment(pub):
    workflow = Workflow(name='Workflow One')
    new_status = workflow.add_status(name='New status')
    end_status = workflow.add_status(name='End status')

    commentable = new_status.add_action('commentable', id='comment')
    commentable.by = ['_submitter']
    commentable.varname = 'foo'

    jump = new_status.add_action('jumponsubmit')
    jump.status = end_status.id

    register_comment = end_status.add_action('register-comment')
    register_comment.comment = '{{ comment_foo }}'

    workflow.store()

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.workflow_id = workflow.id
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.workflow_tests.actions = [
        workflow_tests.FillComment(comment='Hello'),
        workflow_tests.ButtonClick(button_name='Add Comment', who='submitter'),
        workflow_tests.AssertStatus(status_name='End status'),
        workflow_tests.AssertHistoryMessage(message='Hello'),
    ]

    testdef.run(formdef)

    commentable.condition = {'type': 'django', 'value': 'False'}
    workflow.store()
    formdef.refresh_from_storage()

    with pytest.raises(WorkflowTestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'Button "Add Comment" is not displayed.'

    commentable.condition = None
    commentable.by = ['_receiver']
    workflow.store()
    formdef.refresh_from_storage()

    with pytest.raises(WorkflowTestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'Button "Add Comment" is not displayed.'

    # remove commentable action while keeping button
    del new_status.items[0]

    choice = new_status.add_action('choice')
    choice.label = 'Add Comment'
    choice.status = end_status.id
    choice.by = ['_submitter']

    workflow.store()
    formdef.refresh_from_storage()

    with pytest.raises(WorkflowTestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'Comment action field is not displayed.'

    testdef.workflow_tests.actions = [x for x in testdef.workflow_tests.actions if x.key != 'button-click']

    with pytest.raises(WorkflowTestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'Comment fill must be followed by "button click" action.'


@mock.patch('wcs.qommon.emails.send_email')
def test_workflow_tests_form_creation(mocked_send_email, pub):
    workflow = Workflow(name='Workflow One')
    just_submitted_status = workflow.add_status(name='Just submitted')
    new_status = workflow.add_status(name='New status')

    jump = just_submitted_status.add_action('jump')
    jump.status = new_status.id

    workflow.store()

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.fields = [fields.ItemsField(id='1', label='Test', items=['foo', 'bar', 'baz'], varname='items')]
    formdef.workflow_id = workflow.id
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.data['1'] = ['foo', 'bar']

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.workflow_tests.actions = [
        workflow_tests.AssertFormCreation(
            formdef_slug=formdef.url_name,
            mappings=[Mapping(field_id='1', expression='43')],
        ),
    ]
    testdef.store()

    with pytest.raises(WorkflowTestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'No form was created.'

    second_workflow = Workflow(name='Workflow Two')
    other_status = second_workflow.add_status(name='Other status')

    sendmail = other_status.add_action('sendmail')
    sendmail.to = ['test@example.org']
    sendmail.subject = 'In new status'
    sendmail.body = 'xxx'

    second_workflow.store()

    target_formdef = FormDef()
    target_formdef.name = 'To create'
    target_formdef.workflow_id = second_workflow.id
    target_formdef.fields = [
        fields.StringField(id='1', label='Text'),
        fields.ItemsField(id='2', label='Items'),
    ]
    target_formdef.store()

    create_formdata = new_status.add_action('create_formdata', id='1')
    create_formdata.formdef_slug = target_formdef.url_name
    workflow.store()
    formdef.refresh_from_storage()

    with pytest.raises(WorkflowTestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'No form was created.'

    create_formdata.mappings = [
        Mapping(field_id='1', expression='{{ 42|add:1 }}'),
        Mapping(field_id='2', expression='{{ form_var_items_raw }}'),
    ]
    workflow.store()
    formdef.refresh_from_storage()

    testdef.formdef = formdef
    with pytest.raises(WorkflowTestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'No created form matches expected criterias.'
    assert excinfo.value.details == [
        'Created form: wrong form "test title" (should be "To create")',
        'Form status when error occured: New status',
    ]

    testdef.workflow_tests.actions = [
        workflow_tests.AssertFormCreation(
            formdef_slug=target_formdef.url_name,
            mappings=[Mapping(field_id='1', expression='42')],
        ),
    ]

    with pytest.raises(WorkflowTestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'No created form matches expected criterias.'
    assert excinfo.value.details == [
        'Created form: wrong value "43" for field "Text" (should be "42")',
        'Form status when error occured: New status',
    ]

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.workflow_tests.actions = [
        workflow_tests.AssertFormCreation(
            formdef_slug=target_formdef.url_name,
            mappings=[
                Mapping(field_id='1', expression='43'),
                Mapping(field_id='2', expression='{{ "foo,bar"|split:"," }}'),
            ],
        ),
    ]
    testdef.store()

    testdef.run(formdef)

    # check created formdata is hidden from sql methods
    assert formdef.data_class().count() == 0
    assert target_formdef.data_class().count() == 0

    # but exist in test table
    last_test_result = formdef.get_last_test_results().results[0]
    with testdef.use_test_objects(results=[last_test_result]):
        assert formdef.data_class().count() == 1
        assert target_formdef.data_class().count() == 1

    # check created formdata sendmail action didn't send real email
    mocked_send_email.assert_not_called()

    target_formdef.fields = []
    target_formdef.store()
    pub.reset_caches()

    with pytest.raises(WorkflowTestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'No created form matches expected criterias.'
    assert 'Created form: field "1" is missing' in excinfo.value.details

    testdef.workflow_tests.actions = [
        workflow_tests.AssertFormCreation(
            formdef_slug='xxx',
            mappings=[Mapping(field_id='1', expression='43')],
        ),
    ]

    with pytest.raises(WorkflowTestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'Broken, missing form'


@pytest.mark.freeze_time('2024-02-19 12:00')
def test_workflow_tests_form_creation_date_field(pub):
    workflow = Workflow(name='Workflow One')
    new_status = workflow.add_status(name='New status')
    workflow.store()

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.fields = [fields.ItemsField(id='1', label='Test', items=['foo', 'bar', 'baz'], varname='items')]
    formdef.workflow_id = workflow.id
    formdef.store()

    target_formdef = FormDef()
    target_formdef.name = 'To create'
    target_formdef.fields = [
        fields.DateField(id='1', label='Date'),
    ]
    target_formdef.store()

    create_formdata = new_status.add_action('create_formdata', id='1')
    create_formdata.formdef_slug = target_formdef.url_name
    create_formdata.mappings = [
        Mapping(field_id='1', expression='{{ now }}'),
    ]
    workflow.store()
    formdef.refresh_from_storage()

    testdef = TestDef.create_from_formdata(formdef, formdef.data_class()())
    testdef.workflow_tests.actions = [
        workflow_tests.AssertFormCreation(
            formdef_slug=target_formdef.url_name,
            mappings=[Mapping(field_id='1', expression='xxx')],
        ),
    ]
    testdef.store()

    with pytest.raises(WorkflowTestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'No created form matches expected criterias.'
    assert excinfo.value.details == [
        'Created form: wrong value "2024-02-19" for field "Date" (should be "xxx")',
        'Form status when error occured: New status',
    ]

    testdef.workflow_tests.actions[0].mappings[0].expression = '{{ "2024-02-19"|date }}'
    testdef.store()

    testdef.run(formdef)

    testdef.workflow_tests.actions[0].mappings[0].expression = '2024-02-19'
    testdef.store()

    testdef.run(formdef)


def test_workflow_tests_form_creation_cascade(pub):
    child_of_child_formdef = FormDef()
    child_of_child_formdef.name = 'Child of child'
    child_of_child_formdef.fields = [
        fields.StringField(id='1', label='Text', varname='text'),
    ]
    child_of_child_formdef.store()

    workflow = Workflow(name='Workflow Child')
    new_status = workflow.add_status(name='New status')

    create_formdata = new_status.add_action('create_formdata')
    create_formdata.formdef_slug = child_of_child_formdef.url_name
    create_formdata.mappings = [
        Mapping(field_id='1', expression='abc'),
    ]

    workflow.store()

    child_formdef = FormDef()
    child_formdef.name = 'Child'
    child_formdef.workflow_id = workflow.id
    child_formdef.fields = [
        fields.StringField(id='1', label='Text', varname='text'),
    ]
    child_formdef.store()

    workflow = Workflow(name='Workflow Parent')
    new_status = workflow.add_status(name='New status')

    create_formdata = new_status.add_action('create_formdata')
    create_formdata.formdef_slug = child_formdef.url_name
    create_formdata.mappings = [
        Mapping(field_id='1', expression='def'),
    ]

    workflow.store()

    formdef = FormDef()
    formdef.name = 'Parent'
    formdef.workflow_id = workflow.id
    formdef.store()

    testdef = TestDef.create_from_formdata(formdef, formdef.data_class()())
    testdef.workflow_tests.actions = [
        workflow_tests.AssertFormCreation(
            formdef_slug=child_formdef.url_name,
            mappings=[Mapping(field_id='1', expression='def')],
        ),
    ]
    testdef.store()

    testdef.run(formdef)

    assert child_formdef.data_class().count() == 0
    assert child_of_child_formdef.data_class().count() == 0

    last_test_result = formdef.get_last_test_results().results[0]
    with testdef.use_test_objects(results=[last_test_result]):
        assert child_formdef.data_class().count() == 1
        assert child_of_child_formdef.data_class().count() == 1


def test_workflow_tests_card_edition(pub):
    target_carddef = CardDef()
    target_carddef.name = 'To edit'
    target_carddef.fields = [
        fields.StringField(id='1', label='Text', varname='text'),
    ]
    target_carddef.store()

    workflow = Workflow(name='Workflow One')
    new_status = workflow.add_status(name='New status')

    create_carddata = new_status.add_action('create_carddata')
    create_carddata.formdef_slug = target_carddef.url_name
    create_carddata.mappings = [
        Mapping(field_id='1', expression='abc'),
    ]
    create_carddata.varname = 'created_card'

    edit_carddata = new_status.add_action('edit_carddata')

    register_comment = new_status.add_action('register-comment')
    register_comment.comment = '{{ form_links_created_card_var_text }}'

    workflow.store()

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.workflow_id = workflow.id
    formdef.store()

    testdef = TestDef.create_from_formdata(formdef, formdef.data_class()())
    testdef.workflow_tests.actions = [
        workflow_tests.AssertCardCreation(
            formdef_slug=target_carddef.url_name,
            mappings=[Mapping(field_id='1', expression='abc')],
        ),
        workflow_tests.AssertCardEdition(
            formdef_slug=target_carddef.url_name,
            mappings=[Mapping(field_id='1', expression='def')],
        ),
        workflow_tests.AssertHistoryMessage(message='def'),
    ]
    testdef.store()

    with pytest.raises(WorkflowTestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'No card was edited.'

    edit_carddata.formdef_slug = target_carddef.url_name
    edit_carddata.mappings = [
        Mapping(field_id='1', expression='xxx'),
    ]
    workflow.store()
    formdef.refresh_from_storage()

    with pytest.raises(WorkflowTestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'No edited card matches expected criterias.'

    edit_carddata.mappings = [
        Mapping(field_id='1', expression='def'),
    ]
    workflow.store()
    formdef.refresh_from_storage()

    testdef.run(formdef)


def test_workflow_tests_card_edition_isolation(pub):
    target_carddef = CardDef()
    target_carddef.name = 'To edit'
    target_carddef.fields = [
        fields.StringField(id='1', label='Text', varname='text'),
    ]
    target_carddef.store()

    real_carddata = target_carddef.data_class()()
    real_carddata.just_created()
    real_carddata.store()

    workflow = Workflow(name='Workflow One')
    new_status = workflow.add_status(name='New status')

    register_comment = new_status.add_action('register-comment')
    register_comment.comment = 'Number of cards: {{ cards|objects:"to-edit"|count }}'

    workflow.store()

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.workflow_id = workflow.id
    formdef.store()

    testdef = TestDef.create_from_formdata(formdef, formdef.data_class()())
    testdef.workflow_tests.actions = [
        workflow_tests.AssertHistoryMessage(message='Number of cards: 1'),
    ]
    testdef.store()

    testdef.run(formdef)

    # adding edit carddata action hides real cards from test
    edit_carddata = new_status.add_action('edit_carddata')
    edit_carddata.formdef_slug = target_carddef.url_name
    workflow.store()
    formdef.refresh_from_storage()

    testdef = TestDef.get(testdef.id)
    with pytest.raises(WorkflowTestError) as excinfo:
        testdef.run(formdef)
    assert (
        'Displayed history message: content does not contain "Number of cards: 1" (was "Number of cards: 0")'
        in excinfo.value.details
    )


def test_workflow_tests_card_creation(pub):
    workflow = Workflow(name='Workflow One')
    new_status = workflow.add_status(name='New status')
    workflow.store()

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.workflow_id = workflow.id
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()

    target_carddef = CardDef()
    target_carddef.name = 'To create'
    target_carddef.fields = [
        fields.StringField(id='1', label='Text'),
    ]
    target_carddef.store()

    create_carddata = new_status.add_action('create_carddata', id='1')
    create_carddata.formdef_slug = target_carddef.url_name
    workflow.store()
    formdef.refresh_from_storage()

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.workflow_tests.actions = [
        workflow_tests.AssertCardCreation(
            formdef_slug=target_carddef.url_name,
            mappings=[Mapping(field_id='1', expression='43')],
        ),
    ]
    testdef.store()

    with pytest.raises(WorkflowTestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'No card was created.'

    create_carddata.mappings = [
        Mapping(field_id='1', expression='{{ 42|add:1 }}'),
    ]
    workflow.store()
    formdef.refresh_from_storage()

    testdef.run(formdef)


def test_workflow_tests_card_creation_digest_value(pub):
    workflow = Workflow(name='Workflow One')
    new_status = workflow.add_status(name='New status')

    create_carddata = new_status.add_action('create_carddata')
    create_carddata.formdef_slug = 'card-title'
    create_carddata.mappings = [
        Mapping(field_id='1', expression='My card'),
    ]

    workflow.store()

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.workflow_id = workflow.id
    formdef.store()

    carddef = CardDef()
    carddef.name = 'Card title'
    carddef.fields = [
        fields.ItemField(
            id='1',
            label='Foo',
            data_source={'type': 'carddef:card-title-2'},
        ),
    ]
    carddef.store()

    carddef2 = CardDef()
    carddef2.name = 'Card title 2'
    carddef2.fields = [
        fields.StringField(id='2', label='Text', varname='text'),
    ]
    carddef2.digest_templates = {'default': '{{ form_var_text }}'}
    carddef2.store()

    carddata = carddef2.data_class()()
    carddata.just_created()
    carddata.data['2'] = 'My card'
    carddata.store()

    formdata = formdef.data_class()()
    formdata.just_created()

    testdef = TestDef.create_from_formdata(formdef, formdata)
    test_mapping = Mapping(field_id='1', expression='1')
    testdef.workflow_tests.actions = [
        workflow_tests.AssertCardCreation(
            formdef_slug=carddef.url_name,
            mappings=[test_mapping],
        ),
    ]
    testdef.store()

    testdef.run(formdef)

    test_mapping.expression = 'My card'
    testdef.store()

    testdef.run(formdef)

    test_mapping.expression = 'xxx'
    testdef.store()

    with pytest.raises(WorkflowTestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'No created card matches expected criterias.'
    assert 'Created card: wrong value "My card" for field "Foo" (should be "xxx")' in excinfo.value.details


def test_workflow_tests_geolocate(pub):
    workflow = Workflow(name='Workflow One')
    new_status = workflow.add_status(name='New status')
    end_status = workflow.add_status(name='End status')

    geolocate = new_status.add_action('geolocate')
    geolocate.method = 'map_variable'
    geolocate.map_variable = '{{ form_var_map }}'

    jump = new_status.add_action('jump')
    jump.status = end_status.id
    jump.condition = {'type': 'django', 'value': 'form_geoloc_base_lat'}

    workflow.store()

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.geolocations = {'base': 'bla'}
    formdef.workflow_id = workflow.id
    formdef.fields = [
        fields.MapField(id='1', label='Map', varname='map'),
    ]
    formdef.store()

    testdef = TestDef.create_from_formdata(formdef, formdef.data_class()())
    testdef.workflow_tests.actions = [
        workflow_tests.AssertStatus(status_name='End status'),
    ]

    with pytest.raises(WorkflowTestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'Form should be in status "End status" but is in status "New status".'

    testdef.data['fields']['1'] = {'lat': 48.8337085, 'lon': 2.3233693}

    testdef.run(formdef)


def test_workflow_tests_assert_user_can_view(pub):
    role = pub.role_class(name='test role')
    role.store()
    user = pub.user_class(name='test user')
    user.test_uuid = '42'
    user.roles = [role.id]
    user.store()

    workflow = Workflow(name='Workflow One')
    new_status = workflow.add_status(name='New status')
    workflow.store()

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.workflow_id = workflow.id
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.store()

    testdef = TestDef.create_from_formdata(formdef, formdef.data_class()())
    testdef.workflow_tests.actions = [
        workflow_tests.AssertUserCanView(user_uuid=user.test_uuid),
    ]
    testdef.run(formdef)

    other_role = pub.role_class(name='other role')
    other_role.store()

    dispatch = new_status.add_action('dispatch')
    dispatch.dispatch_type = 'manual'
    dispatch.role_key = '_receiver'
    dispatch.role_id = other_role.id
    workflow.store()
    formdef.refresh_from_storage()

    with pytest.raises(WorkflowTestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'User "test user" cannot view form'

    testdef.workflow_tests.actions = [
        workflow_tests.AssertUserCanView(user_uuid='xxx'),
    ]

    with pytest.raises(WorkflowTestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'Broken, missing user'


def test_workflow_tests_edit_form(pub):
    workflow = Workflow(name='Workflow One')
    new_status = workflow.add_status(name='New status')
    end_status = workflow.add_status(name='End status')

    editable = new_status.add_action('editable')
    editable.label = 'Go to form edit'
    editable.by = ['_submitter']

    choice = new_status.add_action('choice')
    choice.label = 'Manual jump'
    choice.status = end_status.id
    choice.by = ['_submitter']

    register_comment = end_status.add_action('register-comment')
    register_comment.comment = '{{ form_var_text }}'

    workflow.store()

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.workflow_id = workflow.id
    formdef.fields = [
        fields.StringField(id='1', label='Text', varname='text'),
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {
        '1': 'abc',
    }

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.workflow_tests.actions = [
        workflow_tests.EditForm(
            edit_action_id='%s-%s' % (new_status.id, editable.id), who='submitter', form_data={'1': 'def'}
        ),
        workflow_tests.ButtonClick(button_name='Manual jump', who='submitter'),
        workflow_tests.AssertStatus(status_name='End status'),
        workflow_tests.AssertHistoryMessage(message='abc'),
    ]

    with pytest.raises(WorkflowTestError) as excinfo:
        testdef.run(formdef)
    assert 'Displayed history message: content does not contain "abc" (was "def")' in excinfo.value.details

    # try to edit from wrong status
    testdef.workflow_tests.actions = [
        workflow_tests.ButtonClick(button_name='Manual jump', who='submitter'),
        workflow_tests.AssertStatus(status_name='End status'),
        workflow_tests.EditForm(edit_action_id='%s-%s' % (new_status.id, editable.id), who='submitter'),
    ]

    with pytest.raises(WorkflowTestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'Form is not in the status containing edit action.'
    assert excinfo.value.details == [
        'Status containing action: New status',
        'Form status when error occured: End status',
    ]

    # add target status on editable action
    editable.status = end_status.id
    workflow.store()
    formdef.refresh_from_storage()

    testdef.workflow_tests.actions = [
        workflow_tests.EditForm(
            edit_action_id='%s-%s' % (new_status.id, editable.id), who='submitter', form_data={'1': 'abc'}
        ),
        workflow_tests.AssertStatus(status_name='End status'),
        workflow_tests.AssertHistoryMessage(message='abc'),
    ]

    testdef.run(formdef)

    # error during form completion
    formdef.fields.append(
        fields.StringField(
            id='2', label='Other', varname='other', condition={'type': 'django', 'value': 'False'}
        ),
    )
    formdef.store()
    testdef.workflow_tests.actions[0].form_data['2'] = 'xxx'

    with pytest.raises(WorkflowTestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'Tried to fill field "Other" but it is hidden.'

    # action is not possible
    editable.condition = {'type': 'django', 'value': 'False'}
    workflow.store()
    formdef.refresh_from_storage()

    with pytest.raises(WorkflowTestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'Conditions for form edition were not met.'

    editable.condition = None
    role = pub.role_class(name='test role')
    role.store()
    editable.by = [role.id]
    workflow.store()
    formdef.refresh_from_storage()

    with pytest.raises(WorkflowTestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'Form edition is not allowed for user "submitter".'

    del new_status.items[0]
    workflow.store()
    formdef.refresh_from_storage()

    with pytest.raises(WorkflowTestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'Broken, missing edit action'

    # add a different action in place of the workflow edit action
    testdef.workflow_tests.actions[0].edit_action_id = '%s-%s' % (new_status.id, choice.id)
    workflow.store()
    formdef.refresh_from_storage()

    with pytest.raises(WorkflowTestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'Broken, missing edit action'


def test_workflow_tests_edit_form_operation_mode(pub):
    workflow = Workflow(name='Workflow One')
    new_status = workflow.add_status(name='New status')
    end_status = workflow.add_status(name='End status')

    editable = new_status.add_action('editable')
    editable.label = 'Go to form edit'
    editable.by = ['_submitter']
    editable.status = end_status.id
    editable.operation_mode = 'partial'
    editable.page_identifier = 'page2'

    register_comment = end_status.add_action('register-comment')
    register_comment.comment = '{{ form_var_text }} {{ form_var_bool }} {{ form_var_text2 }}'

    workflow.store()

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.workflow_id = workflow.id
    formdef.fields = [
        fields.PageField(id='1', label='1st page', varname='page1'),
        fields.StringField(id='2', label='Text', varname='text'),
        fields.PageField(id='3', label='2nd page', varname='page2'),
        fields.BoolField(id='4', label='Bool', varname='bool'),
        fields.PageField(id='5', label='3rd page', varname='page3'),
        fields.StringField(
            id='6',
            label='Text 2',
            varname='text2',
            condition={'type': 'django', 'value': 'form_var_text == "abc"'},
        ),
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {
        '2': 'abc',
        '4': True,
        '6': 'def',
    }

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.workflow_tests.actions = [
        workflow_tests.EditForm(
            edit_action_id='%s-%s' % (new_status.id, editable.id),
            who='submitter',
            # only data from page 2
            form_data={'4': False, '6': 'yyy'},
        ),
        workflow_tests.AssertStatus(status_name='End status'),
        workflow_tests.AssertHistoryMessage(message='abc True def'),
    ]

    with pytest.raises(WorkflowTestError) as excinfo:
        testdef.run(formdef)
    assert (
        'Displayed history message: content does not contain "abc True def" (was "abc False yyy")'
        in excinfo.value.details
    )

    # only data of page 1
    testdef.workflow_tests.actions[0].form_data = {'2': 'xxx'}

    editable.operation_mode = 'single'
    editable.page_identifier = 'page1'
    workflow.store()
    formdef.refresh_from_storage()

    with pytest.raises(WorkflowTestError) as excinfo:
        testdef.run(formdef)
    assert (
        'Displayed history message: content does not contain "abc True def" (was "xxx True def")'
        in excinfo.value.details
    )

    editable.page_identifier = 'xxx'
    workflow.store()
    formdef.refresh_from_storage()

    with pytest.raises(WorkflowTestError) as excinfo:
        testdef.run(formdef)
    assert str(excinfo.value) == 'Page to edit was not found.'


def test_workflow_tests_external_workflow(pub):
    workflow = Workflow(name='Workflow One')
    new_status = workflow.add_status(name='New status')

    global_action = workflow.add_global_action('Edit')
    trigger = global_action.append_trigger('webservice')
    trigger.identifier = 'edit'

    edit_carddata = global_action.add_action('edit_carddata')
    edit_carddata.formdef_slug = 'test-title'
    edit_carddata.mappings = [
        Mapping(field_id='1', expression='abc'),
    ]
    edit_carddata.target_mode = 'manual'
    edit_carddata.target_id = '{{ form_internal_id }}'

    action = new_status.add_action('external_workflow_global_action')
    action.slug = 'carddef:test-title'
    action.trigger_id = 'action:edit'
    action.target_mode = 'manual'
    action.target_id = '{{ form_internal_id }}'

    register_comment = new_status.add_action('register-comment')
    register_comment.comment = '{{ form_var_text }}'

    workflow.store()

    carddef = CardDef()
    carddef.name = 'test title'
    carddef.fields = [
        fields.StringField(id='1', label='Text', varname='text'),
    ]
    carddef.workflow_id = workflow.id
    carddef.store()

    testdef = TestDef.create_from_formdata(carddef, carddef.data_class()())
    testdef.workflow_tests.actions = [
        workflow_tests.AssertHistoryMessage(message='abc'),
    ]
    testdef.store()

    testdef.run(carddef)


def test_workflow_tests_external_workflow_isolation(pub):
    workflow = Workflow(name='Card workflow')
    new_status = workflow.add_status(name='New status')

    global_action = workflow.add_global_action('Edit')
    trigger = global_action.append_trigger('webservice')
    trigger.identifier = 'edit'

    edit_carddata = global_action.add_action('edit_carddata')
    edit_carddata.formdef_slug = 'my-card'
    edit_carddata.mappings = [
        Mapping(field_id='1', expression='abc'),
    ]
    edit_carddata.target_mode = 'manual'
    edit_carddata.target_id = '{{ form_internal_id }}'

    workflow.store()

    carddef = CardDef()
    carddef.name = 'My Card'
    carddef.fields = [
        fields.StringField(id='1', label='Text', varname='text'),
    ]
    carddef.workflow_id = workflow.id
    carddef.store()

    real_carddata = carddef.data_class()()
    real_carddata.just_created()
    real_carddata.store()

    workflow = Workflow(name='Workflow One')
    new_status = workflow.add_status(name='New status')

    action = new_status.add_action('external_workflow_global_action')
    action.slug = 'carddef:my-card'
    action.trigger_id = 'action:edit'
    action.target_mode = 'manual'
    action.target_id = str(real_carddata.id)

    workflow.store()

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.workflow_id = workflow.id
    formdef.store()

    testdef = TestDef.create_from_formdata(formdef, formdef.data_class()())
    testdef.workflow_tests.actions = [
        workflow_tests.AssertStatus(status_name='New status'),
    ]
    testdef.store()

    testdef.run(formdef)

    real_carddata.refresh_from_storage()
    assert real_carddata.data['1'] is None


def test_workflow_tests_frozen_submission_datetime(pub):
    workflow = Workflow(name='Workflow One')
    new_status = workflow.add_status(name='New status')

    register_comment = new_status.add_action('register-comment')
    register_comment.comment = '{{ form_receipt_datetime }}'

    workflow.store()

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.workflow_id = workflow.id
    formdef.store()

    testdef = TestDef.create_from_formdata(formdef, formdef.data_class()())
    testdef.frozen_submission_datetime = make_aware(datetime.datetime(2025, 8, 21, 15, 00))
    testdef.workflow_tests.actions = [
        workflow_tests.AssertHistoryMessage(message='2025-08-21 15:00'),
    ]

    testdef.run(formdef)


@pytest.mark.freeze_time('2025-03-17 12:00')
def test_workflow_tests_create_from_formdata(pub, http_requests, freezer):
    pub.cfg['sms'] = {'sender': 'xxx', 'passerelle_url': 'http://passerelle.invalid/'}
    pub.write_cfg()

    role = pub.role_class(name='test role')
    role.store()
    user = create_user(pub, is_admin=True)
    user.roles = [role.id]
    user.store()

    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.StringField(id='1', label='Text', varname='text'),
    ]
    block.store()

    carddef = CardDef()
    carddef.name = 'My Card'
    carddef.fields = [
        fields.StringField(id='1', label='Text', varname='text'),
        fields.DateField(id='2', label='Date', varname='date'),
        fields.FileField(id='3', label='File', varname='file', max_file_size='1ko'),
        fields.BlockField(id='4', label='Block Data', varname='blockdata', block_slug='foobar'),
        fields.ItemsField(id='5', label='Items', items=['a', 'b']),
    ]
    carddef.store()

    workflow = Workflow(name='Workflow One')
    workflow.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(workflow)
    workflow.backoffice_fields_formdef.fields = [
        fields.StringField(id='bo1', label='Text'),
        fields.StringField(id='bo2', label='Text 2'),
        fields.FileField(id='bo3', label='File', varname='file', max_file_size='1ko'),
        fields.BlockField(id='bo4', label='Block Data', varname='blockdata', block_slug='foobar'),
        fields.ItemsField(id='bo5', label='Items', items=['a', 'b']),
    ]

    new_status = workflow.add_status('New status')
    status_with_timeout_jump = workflow.add_status('Status with timeout jump')
    status_with_button = workflow.add_status('Status with button')
    transition_status = workflow.add_status('Transition status', 'transition_status')
    status_after_edition = workflow.add_status('Status after edition')
    status_after_form = workflow.add_status('Status after form')
    transition_status2 = workflow.add_status('Transition status 2')
    end_status = workflow.add_status('End status', 'end')

    jump = new_status.add_action('jump')
    jump.status = status_with_timeout_jump.id

    jump = status_with_timeout_jump.add_action('jump')
    jump.status = status_with_button.id
    jump.timeout = '{{ 1 }} day'
    jump.mode = 'timeout'

    choice = status_with_button.add_action('choice')
    choice.label = 'Accept'
    choice.status = transition_status.id
    choice.by = [role.id]

    wscall = transition_status.add_action('webservice_call')
    wscall.url = 'http://remote.example.net/json?test=true'
    wscall.varname = 'test_webservice'

    sendmail = transition_status.add_action('sendmail')
    sendmail.varname = 'mail1'
    sendmail.to = ['test@example.org']
    sendmail.subject = 'In new status'
    sendmail.body = 'xxx\nyyy'

    set_backoffice_fields = transition_status.add_action('set-backoffice-fields')
    set_backoffice_fields.fields = [
        {'field_id': 'bo1', 'value': 'xxx'},
        {'field_id': 'bo3', 'value': '{{ form_var_file_raw }}'},
        {'field_id': 'bo4', 'value': '{% block_value text="xxx" %}'},
        {'field_id': 'bo5', 'value': 'a|b'},
    ]

    sendsms = transition_status.add_action('sendsms')
    sendsms.to = ['0123456789']
    sendsms.body = 'Hello'

    anonymise_action = transition_status.add_action('anonymise')
    anonymise_action.mode = 'intermediate'

    redirect_action = transition_status.add_action('redirect_to_url')
    redirect_action.url = 'https://test.com/'

    register_comment = transition_status.add_action('register-comment')
    register_comment.comment = 'Hello'

    transition_status.add_action('modify_criticality')

    create_formdata = transition_status.add_action('create_formdata')
    create_formdata.formdef_slug = 'test-title'
    create_formdata.mappings = [
        Mapping(field_id='1', expression='xxx'),
    ]

    create_carddata = transition_status.add_action('create_carddata')
    create_carddata.formdef_slug = 'my-card'
    create_carddata.mappings = [
        Mapping(field_id='1', expression='abc'),
        Mapping(field_id='2', expression='{{ now|date }}'),
        Mapping(field_id='3', expression='{{ form_var_file_raw }}'),
        Mapping(field_id='4', expression='{% block_value text="xxx" %}'),
        Mapping(field_id='5', expression='a|b'),
    ]

    edit_carddata = transition_status.add_action('edit_carddata')
    edit_carddata.formdef_slug = 'my-card'
    edit_carddata.mappings = [
        Mapping(field_id='1', expression='def'),
    ]

    editable = transition_status.add_action('editable')
    editable.label = 'Go to form edit'
    editable.by = [role.id]
    editable.status = status_after_edition.id

    wscall = status_after_edition.add_action('webservice_call')
    wscall.url = 'http://remote.example.net/json?test=true'
    wscall.varname = 'test_webservice_2'

    display_form = status_after_edition.add_action('form')
    display_form.by = [role.id]
    display_form.varname = 'foo'
    display_form.hide_submit_button = False
    display_form.formdef = WorkflowFormFieldsFormDef(item=display_form)
    display_form.formdef.fields = [
        fields.StringField(id='1', label='Text', varname='text'),
        fields.FileField(id='2', label='File', varname='file', max_file_size='1ko'),
        fields.BlockField(id='3', label='Block Data', varname='blockdata', block_slug='foobar'),
    ]

    jump = status_after_edition.add_action('jumponsubmit')
    jump.status = status_after_form.id

    global_action = workflow.add_global_action('Action 1')
    global_action.triggers[0].roles = [role.id]

    jump = global_action.add_action('jump')
    jump.status = transition_status2.id

    sendmail = transition_status2.add_action('sendmail')
    sendmail.varname = 'mail2'
    sendmail.to = ['test2@example.org']
    sendmail.subject = 'In transition status 2'
    sendmail.body = 'yyy'

    jump = transition_status2.add_action('jump')
    jump.status = end_status.id

    workflow.store()

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.workflow_id = workflow.id
    formdef.fields = [
        fields.StringField(id='1', label='Text', varname='text'),
        fields.FileField(id='2', label='File', varname='file', max_file_size='1ko'),
    ]
    formdef.store()

    formdata = formdef.data_class()()

    upload = PicklableUpload('test.pdf', 'application/pdf', 'ascii')
    upload.receive([b'first line', b'second line'])
    formdata.data['2'] = upload

    formdata.user_id = user.id
    formdata.just_created()
    formdata.store()

    formdata.record_workflow_event('frontoffice-created')
    formdata.perform_workflow()
    formdata.store()

    freezer.tick(datetime.timedelta(days=2))
    _apply_timeouts(pub)

    app = login(get_app(pub))
    resp = app.get(formdata.get_url())
    resp.form.submit('button1').follow()
    resp = app.get(formdata.get_url())
    resp = resp.form.submit('button12').follow()
    resp.form['f1'] = 'bla'
    resp = resp.form.submit('submit').follow()
    resp.form['ffoo_2_1'] = 'xxx'
    resp.form['ffoo_2_2$file'] = Upload('test.txt', b'foobar', 'text/plain')
    resp.form['ffoo_2_3$element0$f1'] = 'yyy'
    resp = resp.form.submit('submit').follow()
    resp.form.submit('button-action-1').follow()
    formdata.refresh_from_storage()
    assert formdata.status == 'wf-end'

    assert http_requests.get_last('url').startswith(
        'http://remote.example.net/json?test=true&orig=example.net&algo=sha256'
    )

    # hack, transform user into test user
    user.test_uuid = '42'
    user.store()
    testdef = TestDef.create_from_formdata(formdef, formdata, add_workflow_tests=True)
    testdef.agent_id = user.test_uuid

    for action in testdef.workflow_tests.actions:
        if isinstance(action, workflow_tests.ButtonClick):
            action.who = 'receiver'
        if isinstance(action, workflow_tests.EditForm):
            action.who = 'receiver'
            action.form_data = {'1': 'bla'}

    testdef.store()
    testdef = TestDef.get(testdef.id)

    testdef.run(formdef)

    results = TestResults.select()[0]
    assert results.coverage['percent_fields'] == 100
    assert results.coverage['percent_workflow'] == 100

    actions = testdef.workflow_tests.actions
    assert len(actions) == 25

    assert actions[0].key == 'assert-status'
    assert actions[0].status_name == 'Status with timeout jump'

    assert actions[1].key == 'skip-time'
    assert actions[1].seconds == 172800

    assert actions[2].key == 'assert-status'
    assert actions[2].status_name == 'Status with button'

    assert actions[3].key == 'button-click'
    assert actions[3].button_name == 'Accept'

    assert actions[4].key == 'assert-status'
    assert actions[4].status_name == 'Transition status'

    assert actions[5].key == 'assert-webservice-call'

    assert WebserviceResponse.count() == 1
    response = [x for x in WebserviceResponse.select() if x.uuid == actions[5].webservice_response_uuid][0]
    assert response.name == 'http://remote.example.net/json'
    assert response.url == 'http://remote.example.net/json'
    assert response.testdef_id == testdef.id
    assert response.status_code == 200
    assert response.qs_data == {'test': 'true'}
    assert json.loads(response.payload) == {'foo': 'bar'}

    assert actions[6].key == 'assert-email'
    assert actions[6].addresses == ['test@example.org']
    assert actions[6].subject_strings == ['In new status']
    assert actions[6].body_strings == ['xxx', 'yyy']

    assert actions[7].key == 'assert-backoffice-field'
    assert actions[7].fields == [
        {'field_id': 'bo1', 'value': 'xxx'},
        {'field_id': 'bo5', 'value': 'a|b'},
    ]

    assert actions[8].key == 'assert-sms'
    assert actions[9].key == 'assert-anonymise'
    assert actions[10].key == 'assert-redirect'

    assert actions[11].key == 'assert-history-message'
    assert actions[11].message_strings == ['Hello']

    assert actions[12].key == 'assert-criticality'

    assert actions[13].key == 'assert-form-creation'
    assert actions[13].formdef_slug == 'test-title'
    assert actions[13].mappings[0].field_id == '1'
    assert actions[13].mappings[0].expression == 'xxx'

    assert actions[14].key == 'assert-card-creation'
    assert actions[14].formdef_slug == 'my-card'
    assert actions[14].mappings[0].field_id == '1'
    assert actions[14].mappings[0].expression == 'abc'
    assert actions[14].mappings[1].field_id == '2'
    assert actions[14].mappings[1].expression == '2025-03-19'
    assert actions[14].mappings[2].field_id == '5'
    assert actions[14].mappings[2].expression == 'a|b'

    assert actions[15].key == 'assert-card-edition'
    assert actions[15].formdef_slug == 'my-card'
    assert not actions[15].mappings

    assert actions[16].key == 'edit-form'
    assert actions[16].edit_action_id == 'transition_status-12'

    assert actions[17].key == 'assert-status'
    assert actions[17].status_name == 'Status after edition'

    assert actions[18].key == 'assert-webservice-call'
    assert actions[18].webservice_response_uuid == actions[5].webservice_response_uuid
    assert WebserviceResponse.count() == 1

    assert actions[19].key == 'fill-form'
    assert actions[19].form_action_id == '4-2'
    assert actions[19].form_data == {'1': 'xxx'}

    assert actions[20].key == 'button-click'
    assert actions[20].button_name == 'Submit'

    assert actions[21].key == 'assert-status'
    assert actions[21].status_name == 'Status after form'

    assert actions[-3].key == 'button-click'
    assert actions[-3].button_name == 'Action 1'

    assert actions[-2].key == 'assert-status'
    assert actions[-2].status_name == 'End status'

    assert actions[-1].key == 'assert-email'
    assert actions[-1].addresses == ['test2@example.org']
    assert actions[-1].subject_strings == ['In transition status 2']
    assert actions[-1].body_strings == ['yyy']


def test_workflow_tests_create_from_formdata_multiple_buttons(pub, http_requests):
    role = pub.role_class(name='test role')
    role.store()
    user = create_user(pub, is_admin=True)
    user.roles = [role.id]
    user.store()

    workflow = Workflow(name='Workflow One')
    new_status = workflow.add_status('New status', 'new-status')
    middle_status = workflow.add_status('Middle status', 'middle-status')
    end_status = workflow.add_status('End status', 'end-status')

    choice = new_status.add_action('choice')
    choice.label = 'Go to middle status'
    choice.status = middle_status.id
    choice.by = [role.id]

    choice = middle_status.add_action('choice')
    choice.label = 'Go to end status'
    choice.status = end_status.id
    choice.by = [role.id]

    workflow.store()

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.workflow_id = workflow.id
    formdef.store()

    formdata = formdef.data_class()()
    formdata.user_id = user.id
    formdata.just_created()
    formdata.store()

    formdata.perform_workflow()
    formdata.store()

    app = login(get_app(pub))
    resp = app.get(formdata.get_url())
    resp = resp.form.submit('button1').follow()
    resp = resp.form.submit('button1').follow()
    formdata.refresh_from_storage()
    assert formdata.status == 'wf-end-status'

    # hack, transform user into test user
    user.test_uuid = '42'
    user.store()
    testdef = TestDef.create_from_formdata(formdef, formdata, add_workflow_tests=True)
    testdef.agent_id = user.test_uuid

    for action in testdef.workflow_tests.actions:
        if isinstance(action, workflow_tests.ButtonClick):
            action.who = 'receiver'

    testdef.run(formdef)

    actions = testdef.workflow_tests.actions
    assert len(actions) == 5

    assert actions[0].key == 'assert-status'
    assert actions[0].status_name == 'New status'

    assert actions[1].key == 'button-click'
    assert actions[1].button_name == 'Go to middle status'

    assert actions[2].key == 'assert-status'
    assert actions[2].status_name == 'Middle status'

    assert actions[3].key == 'button-click'
    assert actions[3].button_name == 'Go to end status'

    assert actions[4].key == 'assert-status'
    assert actions[4].status_name == 'End status'


def test_workflow_tests_create_from_formdata_assert_status_first_actions(pub, http_requests):
    workflow = Workflow(name='Workflow One')

    just_submitted_status = workflow.add_status('Just submitted')
    new_status = workflow.add_status('New status')

    sendmail = just_submitted_status.add_action('sendmail')
    sendmail.varname = 'mail1'
    sendmail.to = ['test@example.org']
    sendmail.subject = 'Just submitted'
    sendmail.body = 'xxx'

    sendmail = just_submitted_status.add_action('sendmail')
    sendmail.varname = 'mail2'
    sendmail.to = ['test@example.org']
    sendmail.subject = 'Just submitted 2'
    sendmail.body = 'xxx'

    jump = just_submitted_status.add_action('jump')
    jump.status = new_status.id

    sendmail = new_status.add_action('sendmail')
    sendmail.varname = 'mail3'
    sendmail.to = ['test@example.org']
    sendmail.subject = 'New status'
    sendmail.body = 'xxx'

    workflow.store()

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.workflow_id = workflow.id
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()

    formdata.perform_workflow()
    formdata.store()

    testdef = TestDef.create_from_formdata(formdef, formdata, add_workflow_tests=True)
    testdef.run(formdef)

    actions = testdef.workflow_tests.actions
    assert len(actions) == 4

    assert actions[0].key == 'assert-email'
    assert actions[0].subject_strings == ['Just submitted']

    assert actions[1].key == 'assert-email'
    assert actions[1].subject_strings == ['Just submitted 2']

    assert actions[2].key == 'assert-status'
    assert actions[2].status_name == 'New status'

    assert actions[3].key == 'assert-email'
    assert actions[3].subject_strings == ['New status']
