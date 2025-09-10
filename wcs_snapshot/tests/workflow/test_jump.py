import datetime
import os
from unittest import mock

import pytest
from django.core.management import call_command
from pyquery import PyQuery
from quixote import cleanup

from wcs.fields import StringField
from wcs.formdef import FormDef
from wcs.logged_errors import LoggedError
from wcs.qommon.http_request import HTTPRequest
from wcs.wf.jump import JumpWorkflowStatusItem, _apply_timeouts
from wcs.workflow_traces import WorkflowTrace
from wcs.workflows import Workflow, perform_items

from ..test_publisher import get_logs
from ..utilities import clean_temporary_pub, create_temporary_pub, get_app, login
from .test_all import admin_user  # noqa pylint: disable=unused-import


def setup_module(module):
    cleanup()


def teardown_module(module):
    clean_temporary_pub()


@pytest.fixture
def pub(request):
    pub = create_temporary_pub()
    pub.cfg['language'] = {'language': 'en'}
    pub.cfg['identification'] = {'methods': ['password']}
    pub.write_cfg()
    req = HTTPRequest(None, {'SERVER_NAME': 'example.net', 'SCRIPT_NAME': ''})
    req.response.filter = {}
    req._user = None
    pub._set_request(req)
    pub.set_config(req)
    return pub


def rewind(formdata, seconds):
    # utility function to move formdata back in time
    formdata.receipt_time = formdata.receipt_time - datetime.timedelta(seconds=seconds)
    formdata.evolution[-1].time = formdata.evolution[-1].time - datetime.timedelta(seconds=seconds)


def test_jump_render_as_line(pub):
    wf = Workflow(name='test')
    status = wf.add_status('Test', id='test')
    item = status.add_action('jump')
    item.status = 'test'

    assert item.render_as_line() == 'Automatic Jump (to Test)'

    item.mode = 'trigger'
    assert item.render_as_line() == 'Automatic Jump (to Test)'

    item.trigger = 'xxx'
    assert item.render_as_line() == 'Automatic Jump (to Test, trigger)'

    item.mode = 'timeout'
    assert item.render_as_line() == 'Automatic Jump (to Test)'

    item.timeout = '234'
    assert item.render_as_line() == 'Automatic Jump (to Test, timeout)'

    item.mode = 'immediate'
    assert item.render_as_line() == 'Automatic Jump (to Test)'


def test_jump_migrate_mode(pub):
    item = JumpWorkflowStatusItem()
    item.migrate()
    assert item.mode == 'immediate'

    item = JumpWorkflowStatusItem()
    item.trigger = 'plop'
    item.migrate()
    assert item.mode == 'trigger'

    item = JumpWorkflowStatusItem()
    item.timeout = '12'
    item.migrate()
    assert item.mode == 'timeout'

    item = JumpWorkflowStatusItem()
    item.trigger = 'plop'
    item.timeout = '12'
    item.migrate()
    assert item.mode == 'trigger'

    # check migration code is not run for actions added via the UI (add_action())
    wf = Workflow(name='test')
    status = wf.add_status('test')
    item = status.add_action('jump')
    assert item.migrate() is False
    assert item.mode == 'immediate'

    # check migration code is not run when used in global actions
    wf = Workflow(name='status')
    action = wf.add_global_action('test')
    item = JumpWorkflowStatusItem()
    action.items = [item]
    item.parent = action
    assert item.migrate() is False
    assert 'mode' not in item.__dict__

    wf.store()
    wf = Workflow.get(wf.id)
    assert 'mode' not in wf.global_actions[0].items[0].__dict__


def test_jump_nothing(pub):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'foobar'
    formdef.store()
    formdata = formdef.data_class()()
    item = JumpWorkflowStatusItem()
    assert item.check_condition(formdata) is True


def test_jump_count_condition(pub):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'foobar'
    formdef.store()
    pub.substitutions.feed(formdef)
    formdef.data_class().wipe()
    formdata = formdef.data_class()()
    item = JumpWorkflowStatusItem()
    item.condition = {'type': 'django', 'value': 'form_objects.count < 2'}
    assert item.check_condition(formdata) is True

    for _ in range(10):
        formdata = formdef.data_class()()
        formdata.just_created()
        formdata.store()

    item.condition = {'type': 'django', 'value': 'form_objects.count < 2'}
    assert item.check_condition(formdata) is False


def test_jump_django_conditions(pub):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'foobar'
    formdef.fields = [
        StringField(id='1', label='Test', varname='foo'),
    ]
    formdef.store()
    formdata = formdef.data_class()()
    formdata.data = {'1': 'hello'}
    pub.substitutions.feed(formdata)
    item = JumpWorkflowStatusItem()

    item.condition = {'type': 'django', 'value': '1 < 2'}
    assert item.check_condition(formdata) is True

    item.condition = {'type': 'django', 'value': 'form_var_foo == "hello"'}
    assert item.check_condition(formdata) is True

    item.condition = {'type': 'django', 'value': 'form_var_foo|first|upper == "H"'}
    assert item.check_condition(formdata) is True

    item.condition = {'type': 'django', 'value': 'form_var_foo|first|upper == "X"'}
    assert item.check_condition(formdata) is False

    assert LoggedError.count() == 0

    item.condition = {'type': 'django', 'value': '~ invalid ~'}
    assert item.check_condition(formdata) is False
    assert LoggedError.count() == 1
    logged_error = LoggedError.select()[0]
    assert logged_error.summary == 'Failed to evaluate condition'
    assert logged_error.exception_class == 'TemplateSyntaxError'
    assert logged_error.exception_message == "Could not parse the remainder: '~' from '~'"
    assert logged_error.context == {
        'stack': [
            {
                'condition': '~ invalid ~',
                'source_url': '',
                'source_label': 'Automatic Jump',
                'condition_type': 'django',
            }
        ]
    }


@pytest.mark.parametrize(
    'condition,result',
    [
        ('false', True),
        ('fAlSe', True),
        ('true == false', True),
        ('false == true', True),
        ('form_var_xxx <', True),  # invalid expression
        ('true', False),
        ('form_var_xxx', False),
        ('form_var_xxx < 3', False),
    ],
)
def test_jump_condition_is_always_false(pub, condition, result):
    item = JumpWorkflowStatusItem()
    item.condition = {'type': 'django', 'value': condition}
    assert item.is_condition_always_false() is result


def test_timeout(pub):
    workflow = Workflow(name='timeout')
    st1 = workflow.add_status('Status1', 'st1')
    workflow.add_status('Status2', 'st2')

    jump = st1.add_action('jump', id='_jump')
    jump.by = ['_submitter', '_receiver']
    jump.timeout = 30 * 60  # 30 minutes
    jump.mode = 'timeout'
    jump.status = 'st2'
    workflow.store()

    # test timeout_parse
    assert jump.timeout_parse(None) is None  # no value, kept as is
    assert jump.timeout_parse('') == ''  # no value, kept as is
    assert jump.timeout_parse('20 minutes') == 20 * 60
    assert jump.timeout_parse('20') == 0  # not unit
    assert jump.timeout_parse('error') == 0  # not a valid value

    formdef = FormDef()
    formdef.name = 'baz'
    formdef.fields = []
    formdef.workflow_id = workflow.id
    assert formdef.get_workflow().id == workflow.id
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    rewind(formdata, seconds=40 * 60)
    formdata.store()
    formdata_id = formdata.id

    _apply_timeouts(pub)

    assert formdef.data_class().get(formdata_id).status == 'wf-st2'

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()
    formdata_id = formdata.id
    with mock.patch('wcs.wf.jump.JumpWorkflowStatusItem.check_condition') as must_jump:
        must_jump.return_value = False
        _apply_timeouts(pub)
        assert must_jump.call_count == 0  # not enough time has passed

        # check a lower than minimal delay is not considered
        jump.timeout = 5 * 50  # 5 minutes
        workflow.store()
        rewind(formdata, seconds=10 * 60)
        formdata.store()
        _apply_timeouts(pub)
        assert must_jump.call_count == 0

        # but is executed once delay is reached
        rewind(formdata, seconds=10 * 60)
        formdata.store()
        _apply_timeouts(pub)
        assert must_jump.call_count == 1

        # check a templated timeout is considered as minimal delay for explicit evaluation
        jump.timeout = '{{ "0" }}'
        workflow.store()
        _apply_timeouts(pub)
        assert must_jump.call_count == 2

        # a jump with an always false condition is ignored
        jump.condition = {'type': 'django', 'value': ' fAlse '}
        workflow.store()
        _apply_timeouts(pub)
        assert must_jump.call_count == 2

    # check there's no crash on workflow without jumps
    formdef = FormDef()
    formdef.name = 'xxx'
    formdef.store()
    _apply_timeouts(pub)


def test_timeout_with_humantime_template(pub):
    workflow = Workflow(name='timeout')
    st1 = workflow.add_status('Status1', 'st1')
    workflow.add_status('Status2', 'st2')

    jump = st1.add_action('jump', id='_jump')
    jump.by = ['_submitter', '_receiver']
    jump.timeout = '{{ 30 }} minutes'
    jump.mode = 'timeout'
    jump.status = 'st2'

    workflow.store()

    formdef = FormDef()
    formdef.name = 'baz'
    formdef.fields = []
    formdef.workflow_id = workflow.id
    assert formdef.get_workflow().id == workflow.id
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()
    formdata_id = formdata.id

    _apply_timeouts(pub)
    assert formdef.data_class().get(formdata_id).status == 'wf-st1'  # no change

    rewind(formdata, seconds=40 * 60)
    formdata.store()
    _apply_timeouts(pub)
    assert formdef.data_class().get(formdata_id).status == 'wf-st2'

    # invalid timeout value
    jump.timeout = '{{ 30 }} plop'
    workflow.store()
    formdef.refresh_from_storage()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()
    formdata_id = formdata.id

    LoggedError.wipe()

    rewind(formdata, seconds=40 * 60)
    formdata.store()
    _apply_timeouts(pub)
    assert formdef.data_class().get(formdata_id).status == 'wf-st1'  # no change

    assert LoggedError.count() == 1
    logged_error = LoggedError.select()[0]
    assert logged_error.summary == "Error in timeout value '30 plop' (computed from '{{ 30 }} plop')"

    # template timeout value returning nothing
    jump.timeout = '{% if 1 %}{% endif %}'
    workflow.store()
    formdef.refresh_from_storage()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()
    formdata_id = formdata.id

    LoggedError.wipe()

    rewind(formdata, seconds=40 * 60)
    formdata.store()
    _apply_timeouts(pub)
    assert formdef.data_class().get(formdata_id).status == 'wf-st1'  # no change

    assert LoggedError.count() == 1
    logged_error = LoggedError.select()[0]
    assert logged_error.summary == "Error in timeout value '' (computed from '{% if 1 %}{% endif %}')"


def test_legacy_timeout(pub):
    workflow = Workflow(name='timeout')
    st1 = workflow.add_status('Status1', 'st1')
    workflow.add_status('Status2', 'st2')

    jump = st1.add_action('timeout', id='_jump')
    jump.timeout = 30 * 60  # 30 minutes
    jump.mode = 'timeout'
    jump.status = 'st2'

    workflow.store()

    formdef = FormDef()
    formdef.name = 'baz'
    formdef.fields = []
    formdef.workflow_id = workflow.id
    assert formdef.get_workflow().id == workflow.id
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    rewind(formdata, seconds=40 * 60)
    formdata.store()
    formdata_id = formdata.id

    _apply_timeouts(pub)

    assert formdef.data_class().get(formdata_id).status == 'wf-st2'


def test_timeout_then_remove(pub):
    workflow = Workflow(name='timeout-then-remove')
    st1 = workflow.add_status('Status1', 'st1')
    st2 = workflow.add_status('Status2', 'st2')

    jump = st1.add_action('jump', id='_jump')
    jump.by = ['_submitter', '_receiver']
    jump.timeout = 30 * 60  # 30 minutes
    jump.mode = 'timeout'
    jump.status = 'st2'

    st2.add_action('remove')

    workflow.store()

    formdef = FormDef()
    formdef.name = 'baz%s' % id(pub)
    formdef.fields = []
    formdef.workflow_id = workflow.id
    assert formdef.get_workflow().id == workflow.id
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    rewind(formdata, seconds=40 * 60)
    formdata.store()
    formdata.record_workflow_event('frontoffice-created')
    formdata_id = formdata.id

    assert str(formdata_id) in [str(x) for x in formdef.data_class().keys()]
    assert bool(formdata.get_workflow_traces())

    _apply_timeouts(pub)

    assert not str(formdata_id) in [str(x) for x in formdef.data_class().keys()]
    # check workflow traces are removed
    assert not bool(formdata.get_workflow_traces())


def test_timeout_with_mark(pub):
    workflow = Workflow(name='timeout')
    st1 = workflow.add_status('Status1', 'st1')
    workflow.add_status('Status2', 'st2')

    jump = st1.add_action('jump', id='_jump')
    jump.by = ['_submitter', '_receiver']
    jump.timeout = 30 * 60  # 30 minutes
    jump.mode = 'timeout'
    jump.status = 'st2'
    jump.set_marker_on_status = True

    workflow.store()

    formdef = FormDef()
    formdef.name = 'baz'
    formdef.fields = []
    formdef.workflow_id = workflow.id
    assert formdef.get_workflow().id == workflow.id
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    rewind(formdata, seconds=40 * 60)
    formdata.store()
    formdata_id = formdata.id

    _apply_timeouts(pub)

    formdata = formdef.data_class().get(formdata_id)
    assert formdata.workflow_data.get('_markers_stack') == [{'status_id': 'st1'}]


def test_timeout_on_anonymised(pub):
    workflow = Workflow(name='timeout')
    st1 = workflow.add_status('Status1', 'st1')
    workflow.add_status('Status2', 'st2')

    jump = st1.add_action('timeout', id='_jump')
    jump.timeout = 30 * 60  # 30 minutes
    jump.mode = 'timeout'
    jump.status = 'st2'

    workflow.store()

    formdef = FormDef()
    formdef.name = 'baz'
    formdef.fields = []
    formdef.workflow_id = workflow.id
    assert formdef.get_workflow().id == workflow.id
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    rewind(formdata, seconds=40 * 60)
    formdata.anonymise()
    formdata.store()
    formdata_id = formdata.id

    _apply_timeouts(pub)

    assert formdef.data_class().get(formdata_id).status == 'wf-st1'  # no change


def test_jump_missing_previous_mark(pub):
    FormDef.wipe()
    Workflow.wipe()

    workflow = Workflow(name='jump-mark')
    st1 = workflow.add_status('Status1', 'st1')

    jump = st1.add_action('jump', id='_jump')
    jump.by = ['_submitter', '_receiver']
    jump.status = '_previous'
    jump.timeout = 30 * 60  # 30 minutes
    jump.mode = 'timeout'

    workflow.store()

    formdef = FormDef()
    formdef.name = 'baz'
    formdef.fields = []
    formdef.workflow_id = workflow.id
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    rewind(formdata, seconds=40 * 60)
    formdata.store()

    LoggedError.wipe()
    _apply_timeouts(pub)
    assert LoggedError.count() == 1


def test_conditional_jump_vs_tracing(pub):
    workflow = Workflow(name='wf')
    st1 = workflow.add_status('Status1', 'st1')
    workflow.add_status('Status2', 'st2')
    comment = st1.add_action('register-comment')
    comment.comment = 'hello world'
    jump1 = st1.add_action('jump')
    jump1.parent = st1
    jump1.condition = {'type': 'django', 'value': 'False'}
    jump1.status = 'wf-st2'
    jump2 = st1.add_action('jump')
    jump2.parent = st1
    jump2.status = 'wf-st2'
    workflow.store()

    formdef = FormDef()
    formdef.name = 'baz'
    formdef.workflow = workflow
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()
    perform_items(st1.items, formdata)
    formdata.refresh_from_storage()
    assert [(x.action_item_key, x.action_item_id) for x in formdata.get_workflow_traces()][-2:] == [
        ('register-comment', str(comment.id)),
        ('jump', str(jump2.id)),
    ]


def test_timeout_tracing(pub, admin_user):
    workflow = Workflow(name='timeout')
    st1 = workflow.add_status('Status1', 'st1')
    st2 = workflow.add_status('Status2', 'st2')

    jump = st1.add_action('timeout', id='_jump')
    jump.timeout = 30 * 60  # 30 minutes
    jump.mode = 'timeout'
    jump.status = 'st2'

    add_message = st2.add_action('register-comment')
    add_message.comment = 'hello'
    workflow.store()

    formdef = FormDef()
    formdef.name = 'baz'
    formdef.fields = []
    formdef.workflow_id = workflow.id
    assert formdef.get_workflow().id == workflow.id
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    rewind(formdata, seconds=40 * 60)
    formdata.store()
    formdata.record_workflow_event('backoffice-created')
    _apply_timeouts(pub)

    resp = login(get_app(pub), username='admin', password='admin').get(
        formdata.get_backoffice_url() + 'inspect'
    )
    assert [PyQuery(x).text() for x in resp.pyquery('#inspect-timeline li > *:nth-child(2)')] == [
        'Created (backoffice submission)',
        'Status1',
        'Timeout jump - Change Status on Timeout',
        'Status2',
        'History Message',
    ]


def test_jump_self_timeout(pub):
    FormDef.wipe()
    Workflow.wipe()

    workflow = Workflow(name='timeout')
    st1 = workflow.add_status('Status1', 'st1')

    jump = st1.add_action('jump')
    jump.timeout = 30 * 60  # 30 minutes
    jump.mode = 'timeout'
    jump.status = 'st1'

    workflow.store()

    formdef = FormDef()
    formdef.name = 'baz'
    formdef.fields = []
    formdef.workflow_id = workflow.id
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    rewind(formdata, seconds=40 * 60)
    formdata.store()
    formdata.record_workflow_event('backoffice-created')
    _apply_timeouts(pub)


def test_timeout_cron_debug_log(pub):
    FormDef.wipe()
    Workflow.wipe()

    workflow = Workflow(name='timeout')
    st1 = workflow.add_status('Status1', 'st1')
    workflow.add_status('Status2', 'st2')

    jump = st1.add_action('jump', id='_jump')
    jump.by = ['_submitter', '_receiver']
    jump.timeout = 30 * 60  # 30 minutes
    jump.mode = 'timeout'
    jump.status = 'st2'

    workflow.store()

    formdef = FormDef()
    formdef.name = 'baz'
    formdef.fields = []
    formdef.workflow_id = workflow.id
    assert formdef.get_workflow().id == workflow.id
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    rewind(formdata, seconds=40 * 60)
    formdata.store()
    formdata_id = formdata.id

    pub.load_site_options()
    pub.site_options.set('options', 'cron-log-level', 'debug')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)

    call_command('cron', job_name='evaluate_jumps', domain='example.net', force_job=True)

    logs = get_logs('example.net', ignore_sql=True)
    assert formdef.data_class().get(formdata_id).status == 'wf-st2'
    assert logs[:2] == ['start', "running jobs: ['evaluate_jumps']"]
    assert 'applying timeouts on baz' in logs[2]
    assert 'event: timeout-jump' in logs[3]


def test_timeout_cron_errors_on_long_jobs(pub, freezer):
    FormDef.wipe()
    Workflow.wipe()

    workflow = Workflow(name='timeout')
    st1 = workflow.add_status('Status1', 'st1')
    st2 = workflow.add_status('Status2', 'st2')

    jump = st1.add_action('jump', id='_jump')
    jump.by = ['_submitter', '_receiver']
    jump.timeout = 30 * 60  # 30 minutes
    jump.mode = 'timeout'
    jump.status = 'st2'
    st2.add_action('webservice_call')
    workflow.store()

    formdef = FormDef()
    formdef.name = 'baz'
    formdef.workflow = workflow
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    rewind(formdata, seconds=40 * 60)
    formdata.store()

    LoggedError.wipe()
    with mock.patch('wcs.wf.wscall.WebserviceCallStatusItem.perform') as perform_wscall:
        perform_wscall.noop = True
        # doing nothing
        call_command('cron', job_name='evaluate_jumps', domain='example.net', force_job=True)
        assert LoggedError.count() == 0

    formdef.data_class().wipe()
    formdata = formdef.data_class()()
    formdata.just_created()
    rewind(formdata, seconds=40 * 60)
    formdata.store()

    # rerun jumps but make them take time
    with mock.patch('wcs.wf.wscall.WebserviceCallStatusItem.perform') as perform_wscall:
        perform_wscall.noop = False
        perform_wscall.side_effect = lambda *x: freezer.move_to(datetime.timedelta(seconds=360))
        call_command('cron', job_name='evaluate_jumps', domain='example.net', force_job=True)
        assert perform_wscall.call_count == 1
        assert LoggedError.count() == 1
        error = LoggedError.select()[0]
        assert error.summary == 'too much time spent on timeout jumps of "baz" in status "Status1"'
        assert error.formdef_id == str(formdef.id)
        assert set(LoggedError.select()[0].context['stack'][0].keys()) == {
            'duration',
            'process_duration',
        }

    # rerun jumps but with a longer timeout configured (thus more allowed timed)
    LoggedError.wipe()
    jump.timeout = 24 * 60 * 60  # a day
    workflow.store()

    formdef.data_class().wipe()
    formdata = formdef.data_class()()
    formdata.just_created()
    rewind(formdata, seconds=25 * 60 * 60)
    formdata.store()

    with mock.patch('wcs.wf.wscall.WebserviceCallStatusItem.perform') as perform_wscall:
        perform_wscall.noop = False
        perform_wscall.side_effect = lambda *x: freezer.move_to(datetime.timedelta(seconds=360))
        call_command('cron', job_name='evaluate_jumps', domain='example.net', force_job=True)
        assert perform_wscall.call_count == 1
        assert LoggedError.count() == 0


def test_too_many_jumps(pub):
    LoggedError.wipe()

    workflow = Workflow(name='test')
    status1 = workflow.add_status('st1')
    status2 = workflow.add_status('st2')
    jump = status1.add_action('jump')
    jump.status = str(status2.id)
    jump = status2.add_action('jump')
    jump.status = str(status1.id)
    workflow.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'foobar'
    formdef.workflow = workflow
    formdef.store()
    formdef.data_class().wipe()
    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()
    formdata.perform_workflow()
    assert LoggedError.count() == 1
    logged_error = LoggedError.select()[0]
    assert logged_error.summary == 'Too many jumps in workflow'
    assert logged_error.workflow_id
    traces = WorkflowTrace.select_for_formdata(formdata)
    assert traces[-1].event == 'aborted-too-many-jumps'


def test_jump_identifier_in_global_action(pub, admin_user):
    FormDef.wipe()
    Workflow.wipe()

    wf = Workflow(name='blah')
    st1 = wf.add_status('One')
    st1.id = 'one'
    st2 = wf.add_status('Two')
    st2.id = 'two'

    global_action = wf.add_global_action('FOOBAR')
    jump = global_action.add_action('jump')
    jump.identifier = 'jump1'
    jump.status = st2.id
    global_action.triggers[0].roles = ['_submitter']
    wf.store()

    formdef = FormDef()
    formdef.name = 'foobar'
    formdef.workflow = wf
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {}
    formdata.user_id = admin_user.id
    formdata.just_created()
    formdata.store()

    app = login(get_app(pub), username='admin', password='admin')

    resp = app.get(jump.get_admin_url())
    assert 'identifier' in resp.form.fields

    resp = app.get(formdata.get_backoffice_url())
    resp = resp.form.submit('button-action-1')
    resp = resp.follow()

    formdata.refresh_from_storage()
    assert formdata.status == 'wf-two'
    substitution_variables = formdata.get_substitution_variables()
    assert substitution_variables['form_jumps'] == ['jump1']
    assert substitution_variables['form_latest_jump'] == 'jump1'
