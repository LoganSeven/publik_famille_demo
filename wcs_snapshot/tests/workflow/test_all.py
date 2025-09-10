import datetime
import decimal
import io
import time
from unittest import mock

import pytest
from django.utils.timezone import localtime
from quixote import cleanup, get_publisher

from wcs import sessions, sql
from wcs.carddef import CardDef
from wcs.fields import CommentField, DateField, ItemField, ItemsField, NumericField, StringField
from wcs.formdata import Evolution
from wcs.formdef import FormDef
from wcs.logged_errors import LoggedError
from wcs.qommon.afterjobs import AfterJob
from wcs.qommon.form import Form
from wcs.qommon.http_request import HTTPRequest
from wcs.qommon.ident.password_accounts import PasswordAccount
from wcs.tracking_code import TrackingCode
from wcs.wf.aggregation_email import AggregationEmail, send_aggregation_emails
from wcs.wf.anonymise import AnonymiseWorkflowStatusItem
from wcs.wf.criticality import MODE_DEC, MODE_INC, MODE_SET, ModifyCriticalityWorkflowStatusItem
from wcs.wf.display_message import DisplayMessageWorkflowStatusItem
from wcs.wf.form import WorkflowFormFieldsFormDef
from wcs.wf.jump import JumpWorkflowStatusItem
from wcs.wf.redirect_to_url import RedirectToUrlWorkflowStatusItem
from wcs.wf.remove import RemoveWorkflowStatusItem
from wcs.wf.remove_tracking_code import RemoveTrackingCodeWorkflowStatusItem
from wcs.wf.sendmail import EmailEvolutionPart
from wcs.workflows import (
    AbortActionException,
    AttachmentEvolutionPart,
    Workflow,
    WorkflowBackofficeFieldsFormDef,
    WorkflowCriticalityLevel,
    WorkflowStatusItem,
    WorkflowVariablesFieldsFormDef,
    perform_items,
)

from ..test_sql import column_exists_in_table
from ..utilities import MockSubstitutionVariables, clean_temporary_pub, create_temporary_pub, get_app


def setup_module(module):
    cleanup()


def teardown_module(module):
    clean_temporary_pub()


@pytest.fixture
def pub():
    pub = create_temporary_pub()
    pub.cfg['language'] = {'language': 'en'}
    pub.cfg['identification'] = {'methods': ['password']}
    pub.write_cfg()
    req = HTTPRequest(None, {'SERVER_NAME': 'example.net', 'SCRIPT_NAME': ''})
    req.response.filter = {}
    req._user = None
    pub._set_request(req)
    pub.set_app_dir(req)
    req.session = sessions.BasicSession(id=1)
    pub.set_config(req)
    return pub


@pytest.fixture
def admin_user():
    get_publisher().user_class.wipe()
    user = get_publisher().user_class()
    user.name = 'John Doe Admin'
    user.email = 'john.doe@example.com'
    user.name_identifiers = ['0123456789']
    user.is_admin = True
    user.store()

    account = PasswordAccount(id='admin')
    account.set_password('admin')
    account.user_id = user.id
    account.store()

    return user


def test_get_json_export_dict(pub):
    workflow = Workflow(name='wf')
    st1 = workflow.add_status('Status1', 'st1')
    st2 = workflow.add_status('Status2', 'st2')
    st2.forced_endpoint = True

    jump = st1.add_action('jump', id='_jump')
    jump.by = ['_submitter', '_receiver']
    jump.timeout = 0.1
    jump.mode = 'timeout'
    jump.status = 'st2'

    workflow.roles['_other'] = 'Other Function'
    root = workflow.get_json_export_dict()
    assert set(root.keys()) >= {'statuses', 'name', 'functions'}

    assert root['name'] == 'wf'
    assert len(root['statuses']) == 2
    assert {st['id'] for st in root['statuses']} == {'st1', 'st2'}
    assert all(set(status.keys()) >= {'id', 'name', 'forced_endpoint'} for status in root['statuses'])
    assert root['statuses'][0]['id'] == 'st1'
    assert root['statuses'][0]['name'] == 'Status1'
    assert root['statuses'][0]['forced_endpoint'] is False
    assert root['statuses'][0]['endpoint'] is False
    assert root['statuses'][1]['id'] == 'st2'
    assert root['statuses'][1]['name'] == 'Status2'
    assert root['statuses'][1]['forced_endpoint'] is True
    assert root['statuses'][1]['endpoint'] is True


def test_action_repr(pub):
    workflow = Workflow(name='wftest')
    st1 = workflow.add_status('Status1', 'st1')
    jump = JumpWorkflowStatusItem()
    assert repr(jump)  # no crash when not attached to status
    jump = st1.add_action('jump', id='_jump')
    jump.by = ['_submitter', '_receiver']
    jump.timeout = 0.1
    jump.mode = 'timeout'
    jump.status = 'st2'

    action = workflow.add_global_action('Timeout')
    criticality = action.add_action('modify_criticality')
    workflow.store()
    # make sure parent relations are not stored in pickles
    _, cur = sql.get_connection_and_cursor()
    cur.execute(
        'SELECT params FROM workflows WHERE id = %(id)s',
        {'id': workflow.id},
    )
    assert b'parent' not in cur.fetchone()[0]
    cur.close()

    for wf in (workflow, Workflow.get(workflow.id)):
        action = wf.possible_status[0].items[0]
        assert 'Status1' in repr(action)
        assert 'wftest' in repr(action)
        action = wf.global_actions[0].items[0]
        assert 'Timeout' in repr(criticality)
        assert 'wftest' in repr(criticality)


def test_variable_compute(pub):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'foobar'
    formdef.fields = [
        StringField(id='1', label='Test', varname='foo'),
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {'1': 'hello'}
    formdata.store()
    pub.substitutions.feed(formdata)

    item = JumpWorkflowStatusItem()

    # straight string
    assert item.compute('blah') == 'blah'

    # django template
    assert item.compute('{{ form_var_foo }}') == 'hello'
    assert item.compute('{{ form_var_foo }}', render=False) == '{{ form_var_foo }}'
    assert item.compute('{% if form_var_foo %}its here{% endif %}') == 'its here'
    assert item.compute('{% if form_var_foo %}') == '{% if form_var_foo %}'
    with pytest.raises(Exception):
        item.compute('{% if form_var_foo %}', raises=True)

    # ezt string
    assert item.compute('[form_var_foo]') == 'hello'
    # ezt string, but not ezt asked
    assert item.compute('[form_var_foo]', render=False) == '[form_var_foo]'
    # ezt string, with an error
    assert item.compute('[end]', raises=False) == '[end]'
    with pytest.raises(Exception):
        item.compute('[end]', raises=True)

    # with context
    assert item.compute('{{ form_var_foo }} {{ bar }}', context={'bar': 'world'}) == 'hello world'
    assert item.compute('[form_var_foo] [bar]', context={'bar': 'world'}) == 'hello world'

    # django wins
    assert item.compute('{{ form_var_foo }} [bar]', context={'bar': 'world'}) == 'hello [bar]'

    # django template, no escaping by default
    formdata.data = {'1': '<b>hello</b>'}
    formdata.store()
    assert item.compute('{{ form_var_foo }}') == '<b>hello</b>'  # autoescape off by default
    assert item.compute('{{ form_var_foo|safe }}') == '<b>hello</b>'  # no escaping (implicit |safe)
    assert item.compute('{{ form_var_foo|escape }}') == '&lt;b&gt;hello&lt;/b&gt;'  # escaping


def test_check_auth(pub):
    user = pub.user_class(name='foo')
    user.store()

    role = pub.role_class(name='bar1')
    role.store()

    formdef = FormDef()
    formdef.name = 'baz'
    formdef.store()

    formdata = formdef.data_class()()

    status_item = WorkflowStatusItem()
    assert status_item.check_auth(formdata, user) is True

    status_item.by = []
    assert status_item.check_auth(formdata, user) is False

    status_item.by = ['logged-users']
    assert status_item.check_auth(formdata, user) is True

    status_item.by = [role.id]
    assert status_item.check_auth(formdata, user) is False
    status_item.by = [int(role.id)]
    assert status_item.check_auth(formdata, user) is False

    user.roles = [role.id]
    status_item.by = [role.id]
    assert status_item.check_auth(formdata, user) is True
    status_item.by = [int(role.id)]
    assert status_item.check_auth(formdata, user) is True

    status_item.by = ['_submitter']
    assert status_item.check_auth(formdata, user) is False
    formdata.user_id = user.id
    assert status_item.check_auth(formdata, user) is True
    formdata.user_id = None

    status_item.by = ['_receiver']
    assert status_item.check_auth(formdata, user) is False
    formdata.workflow_roles = {'_receiver': user.id}
    assert status_item.check_auth(formdata, user) is True
    formdef.workflow_roles = {'_receiver': user.id}
    formdata.workflow_roles = None
    assert status_item.check_auth(formdata, user) is True


def test_workflow_roles_on_workflow_change(pub):
    Workflow.wipe()
    FormDef.wipe()

    pub.role_class.wipe()
    role = pub.role_class(name='xxx')
    role.store()

    wf1 = Workflow(name='wf1')
    st_wf1 = wf1.add_status('status')
    wf1.roles['_other'] = 'Other Function'
    wf1.roles['_yet_another'] = 'Yet another Function'
    wf1.store()

    wf2 = Workflow(name='wf2')
    st_wf2 = wf2.add_status('status')
    wf2.roles['_other'] = 'Other Function'
    wf2.store()

    formdef = FormDef()
    formdef.name = 'baz'
    formdef.workflow = wf1
    formdef.workflow_roles = {'_other': role.id, '_yet_another': None}
    formdef.store()

    formdata = formdef.data_class()()
    formdata.workflow_roles = {'_other': [role.id], '_yet_another': [role.id]}
    formdata.just_created()
    formdata.store()

    formdef.change_workflow(wf2, status_mapping={st_wf1.id: st_wf2.id})

    formdata.refresh_from_storage()
    formdef.refresh_from_storage()
    assert formdef.workflow_roles == {'_other': role.id}
    assert formdata.workflow_roles == {'_other': [role.id]}

    # check unknown functions are also removed
    formdef = FormDef()
    formdef.name = 'baz'
    formdef.workflow = wf1
    formdef.workflow_roles = {'_other': role.id, '_yet_another': None, 'blah': None}
    formdef.store()

    formdata = formdef.data_class()()
    formdata.workflow_roles = {'_other': [role.id], '_yet_another': [role.id], 'blah': [role.id]}
    formdata.just_created()
    formdata.store()

    formdef.change_workflow(wf2, status_mapping={st_wf1.id: st_wf2.id})

    formdata.refresh_from_storage()
    formdef.refresh_from_storage()
    assert formdef.workflow_roles == {'_other': role.id}
    assert formdata.workflow_roles == {'_other': [role.id]}


def test_markers_stack_on_workflow_change(pub):
    Workflow.wipe()
    FormDef.wipe()

    wf1 = Workflow(name='wf1')
    st1_wf1 = wf1.add_status('status1')
    st2_wf1 = wf1.add_status('status2')
    wf1.store()

    wf2 = Workflow(name='wf2')
    st1_wf2 = wf2.add_status('status1', id='11')
    st2_wf2 = wf2.add_status('status2', id='12')
    wf2.store()

    formdef = FormDef()
    formdef.name = 'baz'
    formdef.workflow = wf1
    formdef.store()

    formdata1 = formdef.data_class()()
    formdata1.just_created()
    formdata1.workflow_data = {'_markers_stack': [{'status_id': st1_wf1.id}]}
    formdata1.store()

    formdata2 = formdef.data_class()()
    formdata2.just_created()
    formdata2.store()

    formdef.change_workflow(wf2, status_mapping={st1_wf1.id: st1_wf2.id, st2_wf1.id: st2_wf2.id})

    formdata1.refresh_from_storage()
    assert formdata1.workflow_data == {'_markers_stack': [{'status_id': 'wf-11'}]}


def test_anonymise(pub):
    # build a backoffice field
    Workflow.wipe()
    wf = Workflow(name='wf with backoffice field')
    wf.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(wf)
    wf.backoffice_fields_formdef.fields = [
        StringField(id='bo1', label='bo field 1'),
        ItemField(id='bo2', label='list', items=['bofoo', 'bobar']),
    ]
    wf.add_status('Status1')
    wf.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'baz'
    formdef.fields = [
        StringField(id='1', label='field 1'),
        ItemField(id='2', label='list', items=['abc', 'def']),
    ]
    formdef.workflow_id = wf.id
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.user_id = '1'
    formdata.data = {
        '1': 'foo',
        '2': 'abc',
        '2_display': 'abc',
        'bo1': 'bar',
        'bo2': 'foo',
        'bo2_display': 'foo',
    }
    formdata.workflow_data = {'e': 'mc2'}
    formdata.submission_context = {'foo': 'bar'}
    formdata.store()
    evo = Evolution(formdata)  # add a new evolution
    evo.time = localtime()
    evo.status = formdata.status
    evo.who = 42
    evo.parts = [AttachmentEvolutionPart('hello.txt', fp=io.BytesIO(b'hello world'), varname='testfile')]
    formdata.evolution.append(evo)
    formdata.store()
    assert len(formdata.evolution) == 2
    assert formdata.evolution[0].parts is not None
    assert formdata.evolution[1].parts is not None

    item = AnonymiseWorkflowStatusItem()
    item.perform(formdata)
    formdata.refresh_from_storage()
    assert formdata.user_id is None
    assert formdata.anonymised
    assert formdata.submission_context is None
    assert formdata.data == {
        '1': None,
        '2': 'abc',
        '2_display': 'abc',
        'bo1': None,
        'bo2': 'foo',
        'bo2_display': 'foo',
    }
    assert formdata.workflow_data is None
    assert formdata.evolution[0].who is None
    assert formdata.evolution[1].who is None
    assert len(formdata.evolution) == 2
    assert formdata.evolution[0].parts is None
    assert formdata.evolution[1].parts is None

    assert item.render_as_line() == 'Anonymisation (final)'
    item.mode = 'unlink_user'
    assert item.render_as_line() == 'Anonymisation (only user unlinking)'


def test_anonymise_custom_view_user_filtered(pub):
    CardDef.wipe()
    FormDef.wipe()
    Workflow.wipe()
    pub.custom_view_class.wipe()

    carddef = CardDef()
    carddef.name = 'foo'
    carddef.store()

    carddata = carddef.data_class()()
    carddata.data = {'0': 'FOO BAR 0'}
    carddata.just_created()
    carddata.jump_status('new')
    carddata.store()

    custom_view = pub.custom_view_class()
    custom_view.title = 'card view'
    custom_view.formdef = carddef
    custom_view.columns = {'list': [{'id': '0'}]}
    custom_view.filters = {'filter-user': 'on', 'filter-user-value': '__current__'}
    custom_view.visibility = 'datasource'
    custom_view.store()

    formdef = FormDef()
    formdef.name = 'baz'
    formdef.fields = [
        ItemsField(id='1', label='list', data_source={'type': 'carddef:foo:card-view'}, anonymise='final'),
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.data = {
        '1': ['foo', 'bar'],
        '1_display': 'foo, bar',
    }
    formdata.store()

    pub._set_request(None)  # must run without request
    item = AnonymiseWorkflowStatusItem()
    item.perform(formdata)
    formdata.refresh_from_storage()
    assert formdata.data == {
        '1': None,
        '1_display': None,
    }


def test_remove(pub):
    pub.workflow_execution_stack = []
    formdef = FormDef()
    formdef.name = 'baz'
    formdef.store()

    formdef.data_class().wipe()
    formdata = formdef.data_class()()
    formdata.store()

    item = RemoveWorkflowStatusItem()
    assert formdef.data_class().count() == 1
    with pytest.raises(AbortActionException) as e:
        item.perform(formdata)
        assert e.url == 'http://example.net'
    assert formdef.data_class().count() == 0

    formdata = formdef.data_class()()
    formdata.store()

    item = RemoveWorkflowStatusItem()
    req = pub.get_request()
    req.response.filter['in_backoffice'] = True
    assert formdef.data_class().count() == 1
    with pytest.raises(AbortActionException) as e:
        item.perform(formdata)
        assert e.url == '..'
    assert formdef.data_class().count() == 0
    req.response.filter = {}
    assert req.session.message


def test_stop_on_remove(pub, emails):
    pub.workflow_execution_stack = []
    workflow = Workflow(name='stop-on-remove')
    st1 = workflow.add_status('Status1', 'st1')

    # sendmail + remove + sendmail
    mail1 = st1.add_action('sendmail')
    mail1.to = ['bar@localhost']
    mail1.subject = 'Foobar'
    mail1.body = 'email body'
    st1.add_action('remove')
    mail2 = st1.add_action('sendmail')
    mail2.to = ['bar@localhost']
    mail2.subject = 'Foobar2'
    mail2.body = 'email body 2'

    workflow.store()

    formdef = FormDef()
    formdef.name = 'baz%s' % id(pub)
    formdef.fields = []
    formdef.workflow_id = workflow.id
    assert formdef.get_workflow().id == workflow.id
    formdef.store()

    formdef.data_class().wipe()
    emails.empty()
    assert formdef.data_class().count() == 0
    assert emails.count() == 0

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()
    url = perform_items(st1.items, formdata)
    pub.process_after_jobs()

    # formdata is removed, no email were sent
    assert formdef.data_class().count() == 0
    assert emails.count() == 1
    assert url == 'http://example.net'

    # check the url from a redirect action is used
    redirect = RedirectToUrlWorkflowStatusItem()
    redirect.url = 'https://www.example.net/custom-redirect'
    st1.items.insert(0, redirect)
    redirect.parent = st1
    workflow.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()
    url = perform_items(st1.items, formdata)
    pub.process_after_jobs()
    assert url == redirect.url


def test_display_form(pub):
    formdef = FormDef()
    formdef.name = 'baz'
    formdef.fields = []
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()

    wf = Workflow(name='status')
    st1 = wf.add_status('Status1', 'st1')

    display_form = st1.add_action('form', id='_x')
    display_form.varname = 'xxx'
    display_form.formdef = WorkflowFormFieldsFormDef(item=display_form)
    display_form.formdef.fields.append(StringField(id='1', label='Test'))
    display_form.formdef.fields.append(DateField(id='2', label='Date', varname='date'))
    display_form.hide_submit_button = False

    form = Form(action='#', use_tokens=False)
    display_form.fill_form(form, formdata, None)
    assert form.widgets[0].title == 'Test'
    assert form.widgets[1].title == 'Date'

    pub.get_request().environ['REQUEST_METHOD'] = 'POST'
    pub.get_request().form = {
        f'fxxx_{display_form.id}_1': 'Foobar',
        f'fxxx_{display_form.id}_2': '2015-05-12',
        'submit': 'submit',
    }
    display_form.submit_form(form, formdata, None, None)

    assert formdata.get_substitution_variables()['xxx_var_date'] == '2015-05-12'

    with pub.with_language('fr'):
        formdata = formdef.data_class()()
        formdata.just_created()
        formdata.store()

        form = Form(action='#', use_tokens=False)
        display_form.fill_form(form, formdata, None)
        pub.get_request().environ['REQUEST_METHOD'] = 'POST'
        pub.get_request().form = {
            f'fxxx_{display_form.id}_1': 'Foobar',
            f'fxxx_{display_form.id}_2': '12/05/2015',
            'submit': 'submit',
        }
        display_form.submit_form(form, formdata, None, None)
        assert formdata.get_substitution_variables()['xxx_var_date'] == '12/05/2015'

        assert formdata.get_substitution_variables()['xxx_var_date_raw'] == time.strptime(
            '2015-05-12', '%Y-%m-%d'
        )


def test_display_form_and_comment(pub):
    role = pub.role_class(name='bar1')
    role.store()

    user = pub.user_class()
    user.roles = [role.id]
    user.store()

    formdef = FormDef()
    formdef.name = 'baz'
    formdef.fields = []
    formdef.store()

    wf = Workflow(name='status')
    st1 = wf.add_status('Status1', 'st1')

    display_form = st1.add_action('form', id='_x')
    display_form.by = [role.id]
    display_form.varname = 'xxx'
    display_form.formdef = WorkflowFormFieldsFormDef(item=display_form)
    display_form.formdef.fields.append(CommentField(id='1', label='Test'))

    commentable = st1.add_action('commentable')
    commentable.by = [role.id]

    wf.store()

    formdef.workflow = wf
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()
    assert formdata.get_status().name == 'Status1'

    form = formdata.get_workflow_form(user)
    assert 'Test' in str(form.widgets[0].render())
    assert '<textarea' in str(form.widgets[1].render())


def test_display_form_migration(pub):
    wf = Workflow(name='status')
    st1 = wf.add_status('Status1', 'st1')

    display_form = st1.add_action('form', id='_x')
    display_form.varname = 'xxx'
    display_form.formdef = WorkflowFormFieldsFormDef(item=display_form)
    display_form.formdef.fields = [ItemField(id='1', label='Test')]

    display_form.formdef.fields[0].show_as_radio = True
    wf.store()

    wf = Workflow.get(wf.id)
    assert wf.possible_status[0].items[0].formdef.fields[0].display_mode == 'radio'


def test_display_form_in_global_action_migration(pub):
    wf = Workflow(name='status')
    action = wf.add_global_action('test')

    display_form = action.add_action('form', id='_x')
    display_form.varname = 'xxx'
    display_form.formdef = WorkflowFormFieldsFormDef(item=display_form)
    display_form.formdef.fields = [ItemField(id='1', label='Test')]

    display_form.formdef.fields[0].show_as_radio = True
    wf.store()

    wf = Workflow.get(wf.id)
    assert wf.global_actions[0].items[0].formdef.fields[0].display_mode == 'radio'


def test_display_form_hide_submit_button(pub):
    wf = Workflow(name='test')
    st1 = wf.add_status('Status1', 'st1')
    st2 = wf.add_status('Status2', 'st2')

    display_form = st1.add_action('form', id='_x')
    display_form.varname = 'xxx'
    display_form.by = ['_submitter']
    display_form.formdef = WorkflowFormFieldsFormDef(item=display_form)
    display_form.formdef.fields.append(StringField(id='1', label='Test', varname='test'))
    display_form.hide_submit_button = False

    wf.store()

    formdef = FormDef()
    formdef.name = 'baz'
    formdef.fields = []
    formdef.workflow = wf
    formdef.store()

    formdef.data_class().wipe()

    app = get_app(pub)
    resp = app.get(formdef.get_url())
    resp = resp.form.submit(name='submit')  # -> validation
    resp = resp.form.submit(name='submit')  # -> submission
    resp = resp.follow()
    assert resp.pyquery('#wf-actions button')
    formdata = formdef.data_class().select()[0]

    display_form.hide_submit_button = True
    wf.store()
    resp = app.get(resp.request.path)
    assert not resp.pyquery('#wf-actions button')

    button = st1.add_action('choice')
    button.label = 'button'
    button.by = ['_submitter']
    button.status = st2.id
    wf.store()

    resp = app.get(resp.request.path)
    assert resp.pyquery('#wf-actions button')
    resp.form[f'fxxx_{display_form.id}_1'] = 'plop'
    resp = resp.form.submit(resp.pyquery('#wf-actions button').attr.name)

    formdata.refresh_from_storage()
    assert formdata.get_status().id == st2.id

    pub.substitutions.feed(formdata)
    context = pub.substitutions.get_context_variables(mode='lazy')
    assert context['form_workflow_form_xxx_var_test'] == 'plop'

    # try with button that do not record form
    formdef.data_class().wipe()
    button.ignore_form_errors = True
    wf.store()

    resp = app.get(formdef.get_url())
    resp = resp.form.submit(name='submit')  # -> validation
    resp = resp.form.submit(name='submit')  # -> submission
    resp = resp.follow()
    resp.form[f'fxxx_{display_form.id}_1'] = 'plop'
    resp = resp.form.submit(resp.pyquery('#wf-actions button').attr.name)
    formdata = formdef.data_class().select()[0]
    assert formdata.get_status().id == st2.id

    pub.substitutions.reset()
    pub.substitutions.feed(formdata)
    context = pub.substitutions.get_context_variables(mode='lazy')
    with pytest.raises(KeyError):
        # check workflow form was not recorded
        assert context['form_workflow_form_xxx_var_test']


def test_display_form_migrate_evolution_formdef(pub):
    wf = Workflow(name='test')
    st1 = wf.add_status('Status1', 'st1')
    st2 = wf.add_status('Status2', 'st2')

    display_form = st1.add_action('form', id='_x')
    display_form.varname = 'xxx'
    display_form.by = ['_submitter']
    display_form.formdef = WorkflowFormFieldsFormDef(item=display_form)
    display_form.formdef.fields.append(StringField(id='1', label='Test', varname='test'))
    display_form.hide_submit_button = True

    button = st1.add_action('choice')
    button.label = 'button'
    button.by = ['_submitter']
    button.status = st2.id
    wf.store()

    formdef = FormDef()
    formdef.name = 'baz'
    formdef.fields = []
    formdef.workflow = wf
    formdef.store()

    formdef.data_class().wipe()

    app = get_app(pub)
    resp = app.get(formdef.get_url())
    resp = resp.form.submit(name='submit')  # -> validation
    resp = resp.form.submit(name='submit')  # -> submission
    resp = resp.follow()
    formdata = formdef.data_class().select()[0]

    resp = app.get(resp.request.path)
    assert resp.pyquery('#wf-actions button')
    resp.form[f'fxxx_{display_form.id}_1'] = 'plop'
    resp = resp.form.submit(resp.pyquery('#wf-actions button').attr.name)

    formdata.refresh_from_storage()
    assert formdata.get_status().id == st2.id

    pub.substitutions.feed(formdata)
    context = pub.substitutions.get_context_variables(mode='lazy')
    assert context['form_workflow_form_xxx_var_test'] == 'plop'

    # check it doesn't crash when field migrations have to be run
    with mock.patch('wcs.fields.StringField.migrate') as migrate_string:
        migrate_string.return_value = True
        assert context['form_workflow_form_xxx_var_test'] == 'plop'
        assert migrate_string.call_count == 1


def test_workflow_display_message(pub):
    pub.substitutions.feed(MockSubstitutionVariables())

    workflow = Workflow(name='display message')
    st1 = workflow.add_status('Status1', 'st1')

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'foobar'
    formdef.fields = []
    formdef.store()
    formdata = formdef.data_class()()
    formdata.id = '1'

    display_message = DisplayMessageWorkflowStatusItem()
    display_message.parent = st1

    display_message.message = 'test'
    assert display_message.get_message(formdata) == '<p>test</p>'

    display_message.message = '{{ number }}'
    assert display_message.get_message(formdata) == '<p>%s</p>' % formdata.id

    display_message.message = '[number]'
    assert display_message.get_message(formdata) == '<p>%s</p>' % formdata.id

    display_message.message = '{{ bar }}'
    assert display_message.get_message(formdata) == '<p>Foobar</p>'

    display_message.message = '[bar]'
    assert display_message.get_message(formdata) == '<p>Foobar</p>'

    # makes sure the string is correctly escaped for HTML
    display_message.message = '{{ foo }}'
    assert display_message.get_message(formdata) == '<p>1 &lt; 3</p>'
    display_message.message = '[foo]'
    assert display_message.get_message(formdata) == '<p>1 &lt; 3</p>'


def test_workflow_display_message_to(pub):
    workflow = Workflow(name='display message to')
    st1 = workflow.add_status('Status1', 'st1')

    role = pub.role_class(name='foorole')
    role.store()
    role2 = pub.role_class(name='no-one-role')
    role2.store()
    user = pub.user_class(name='baruser')
    user.roles = []
    user.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'foobar'
    formdef.url_name = 'foobar'
    formdef.workflow = workflow
    formdef.store()

    formdata = formdef.data_class()()
    formdata.status = 'wf-st1'

    display_message = st1.add_action('displaymsg')

    display_message.message = 'all'
    display_message.to = None
    assert display_message.get_message(formdata) == '<p>all</p>'
    assert formdata.get_workflow_messages(user=pub._request._user) == ['<p>all</p>']

    display_message.message = 'to-role'
    display_message.to = [role.id]
    assert display_message.get_message(formdata) == ''
    assert formdata.get_workflow_messages(user=pub._request._user) == []

    pub._request._user = user
    display_message.message = 'to-role'
    display_message.to = [role.id]
    assert display_message.get_message(formdata) == ''
    assert formdata.get_workflow_messages(user=pub._request._user) == []
    user.roles = [role.id]
    assert display_message.get_message(formdata) == '<p>to-role</p>'
    assert formdata.get_workflow_messages(user=pub._request._user) == ['<p>to-role</p>']

    user.roles = []
    display_message.message = 'to-submitter'
    display_message.to = ['_submitter']
    assert display_message.get_message(formdata) == ''
    assert formdata.get_workflow_messages(user=pub._request._user) == []
    formdata.user_id = user.id
    assert display_message.get_message(formdata) == '<p>to-submitter</p>'
    assert formdata.get_workflow_messages(user=pub._request._user) == ['<p>to-submitter</p>']

    display_message.message = 'to-role-or-submitter'
    display_message.to = [role.id, '_submitter']
    assert display_message.get_message(formdata) == '<p>to-role-or-submitter</p>'
    assert formdata.get_workflow_messages(user=pub._request._user) == ['<p>to-role-or-submitter</p>']
    formdata.user_id = None
    assert display_message.get_message(formdata) == ''
    assert formdata.get_workflow_messages(user=pub._request._user) == []
    user.roles = [role.id]
    assert display_message.get_message(formdata) == '<p>to-role-or-submitter</p>'
    assert formdata.get_workflow_messages(user=pub._request._user) == ['<p>to-role-or-submitter</p>']
    formdata.user_id = user.id
    assert display_message.get_message(formdata) == '<p>to-role-or-submitter</p>'
    assert formdata.get_workflow_messages(user=pub._request._user) == ['<p>to-role-or-submitter</p>']

    display_message.to = [role2.id]
    assert display_message.get_message(formdata) == ''
    assert formdata.get_workflow_messages(user=pub._request._user) == []

    display_message.message = 'd1'
    display_message2 = st1.add_action('displaymsg')
    display_message2.message = 'd2'
    display_message2.to = [role.id, '_submitter']
    assert formdata.get_workflow_messages(user=pub._request._user) == ['<p>d2</p>']
    user.roles = [role.id, role2.id]
    assert '<p>d1</p>' in formdata.get_workflow_messages(user=pub._request._user)
    assert '<p>d2</p>' in formdata.get_workflow_messages(user=pub._request._user)


def test_workflow_display_message_line_details(pub):
    workflow = Workflow(name='display message to')
    st1 = workflow.add_status('Status1', 'st1')
    display_message = DisplayMessageWorkflowStatusItem()
    display_message.parent = st1

    assert display_message.get_line_details() == 'top of page'
    display_message.position = 'top'
    assert display_message.get_line_details() == 'top of page'
    display_message.position = 'bottom'
    assert display_message.get_line_details() == 'bottom of page'
    display_message.position = 'actions'
    assert display_message.get_line_details() == 'with actions'

    role = pub.role_class(name='foorole')
    role.store()
    display_message.to = [role.id]
    assert display_message.get_line_details() == 'with actions, for foorole'


def test_workflow_roles(pub, emails):
    pub.substitutions.feed(MockSubstitutionVariables())

    user = pub.user_class(name='foo')
    user.email = 'zorg@localhost'
    user.store()

    pub.role_class.wipe()
    role1 = pub.role_class(name='foo')
    role1.emails = ['foo@localhost']
    role1.details = 'Hello World'
    role1.store()

    role2 = pub.role_class(name='bar')
    role2.emails = ['bar@localhost', 'baz@localhost']
    role2.store()

    workflow = Workflow(name='wf roles')
    st1 = workflow.add_status('Status1', 'st1')
    item = st1.add_action('sendmail')
    item.to = ['_receiver', '_other']
    item.subject = 'Foobar'
    item.body = 'Hello'
    workflow.roles['_other'] = 'Other Function'
    workflow.store()

    formdef = FormDef()
    formdef.name = 'baz'
    formdef.fields = []
    formdef.workflow_roles = {'_receiver': role1.id, '_other': role2.id}
    formdef.workflow_id = workflow.id
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()

    emails.empty()
    item.perform(formdata)
    pub.process_after_jobs()
    assert emails.count() == 1
    assert emails.get('Foobar')
    assert set(emails.get('Foobar')['email_rcpt']) == {'foo@localhost', 'bar@localhost', 'baz@localhost'}

    workflow.roles['_slug-with-dash'] = 'Dashed Function'
    workflow.store()
    formdef.workflow_roles['_slug-with-dash'] = role1.id
    formdef.store()
    substvars = formdata.get_substitution_variables()
    assert substvars.get('form_role_other_name') == 'bar'
    assert substvars.get('form_role_slug_with_dash_name') == 'foo'
    assert substvars.get('form_role_slug_with_dash_details') == 'Hello World'


def test_criticality(pub):
    FormDef.wipe()

    workflow = Workflow(name='criticality')
    workflow.criticality_levels = [
        WorkflowCriticalityLevel(name='green'),
        WorkflowCriticalityLevel(name='yellow'),
        WorkflowCriticalityLevel(name='red'),
    ]
    workflow.store()

    formdef = FormDef()
    formdef.name = 'baz'
    formdef.workflow_id = workflow.id
    formdef.store()

    item = ModifyCriticalityWorkflowStatusItem()

    formdata = formdef.data_class()()
    item.perform(formdata)
    assert formdata.get_criticality_level_object().name == 'yellow'

    formdata = formdef.data_class()()
    item.mode = MODE_INC
    item.perform(formdata)
    assert formdata.get_criticality_level_object().name == 'yellow'
    item.perform(formdata)
    assert formdata.get_criticality_level_object().name == 'red'
    item.perform(formdata)
    assert formdata.get_criticality_level_object().name == 'red'

    item.mode = MODE_DEC
    item.perform(formdata)
    assert formdata.get_criticality_level_object().name == 'yellow'
    item.perform(formdata)
    assert formdata.get_criticality_level_object().name == 'green'
    item.perform(formdata)
    assert formdata.get_criticality_level_object().name == 'green'

    item.mode = MODE_SET
    item.absolute_value = 2
    item.perform(formdata)
    assert formdata.get_criticality_level_object().name == 'red'
    item.absolute_value = 0
    item.perform(formdata)
    assert formdata.get_criticality_level_object().name == 'green'


def test_criticality_colour_migration(pub):
    FormDef.wipe()

    workflow = Workflow(name='criticality')
    workflow.criticality_levels = [
        WorkflowCriticalityLevel(name='green', colour='00FF00'),
    ]
    workflow.store()

    workflow.refresh_from_storage()
    assert workflow.criticality_levels[0].colour == '#00FF00'


@pytest.mark.parametrize('formdef_class', [FormDef, CardDef])
def test_global_timeouts(pub, formdef_class):
    CardDef.wipe()
    FormDef.wipe()
    Workflow.wipe()

    workflow = Workflow(name='global-timeouts')
    workflow.possible_status = Workflow.get_default_workflow().possible_status[:]
    workflow.criticality_levels = [
        WorkflowCriticalityLevel(name='green'),
        WorkflowCriticalityLevel(name='yellow'),
        WorkflowCriticalityLevel(name='red'),
    ]
    action = workflow.add_global_action('Timeout Test')
    action.add_action('modify_criticality')
    trigger = action.append_trigger('timeout')
    trigger.anchor = 'creation'
    workflow.store()

    formdef = formdef_class()
    formdef.name = 'baz'
    formdef.fields = []
    formdef.workflow_id = workflow.id
    formdef.store()
    formdef.data_class().wipe()

    formdata1 = formdef.data_class()()
    formdata1.just_created()
    formdata1.store()

    # delay isn't set yet, no crash
    pub.apply_global_action_timeouts()
    assert formdef.data_class().get(formdata1.id).get_criticality_level_object().name == 'green'

    # delay didn't expire yet, no change
    trigger.timeout = '2'
    workflow.store()

    pub.apply_global_action_timeouts()
    assert formdef.data_class().get(formdata1.id).get_criticality_level_object().name == 'green'

    formdata1.receipt_time = localtime() - datetime.timedelta(days=3)
    formdata1.store()
    pub.apply_global_action_timeouts()
    assert formdef.data_class().get(formdata1.id).get_criticality_level_object().name == 'yellow'

    # make sure it's not triggered a second time
    pub.apply_global_action_timeouts()
    assert formdef.data_class().get(formdata1.id).get_criticality_level_object().name == 'yellow'

    # change id so it's triggered again
    trigger.id = 'XXX1'
    workflow.store()
    pub.apply_global_action_timeouts()
    assert formdef.data_class().get(formdata1.id).get_criticality_level_object().name == 'red'
    pub.apply_global_action_timeouts()
    assert formdef.data_class().get(formdata1.id).get_criticality_level_object().name == 'red'

    # reset formdata to initial state
    formdata1.store()

    trigger.anchor = '1st-arrival'
    trigger.anchor_status_first = None
    workflow.store()

    pub.apply_global_action_timeouts()
    assert formdef.data_class().get(formdata1.id).get_criticality_level_object().name == 'green'

    formdata1.evolution[-1].time = localtime() - datetime.timedelta(days=3)
    formdata1.store()
    pub.apply_global_action_timeouts()
    assert formdef.data_class().get(formdata1.id).get_criticality_level_object().name == 'yellow'

    formdata1.store()  # reset

    # bad (obsolete) status: do nothing
    trigger.anchor_status_first = 'wf-foobar'
    workflow.store()
    formdata1.evolution[-1].time = localtime() - datetime.timedelta(days=3)
    formdata1.store()
    pub.apply_global_action_timeouts()
    assert formdef.data_class().get(formdata1.id).get_criticality_level_object().name == 'green'
    formdata1.store()

    trigger.anchor = 'latest-arrival'
    trigger.anchor_status_latest = None
    workflow.store()

    formdata1.evolution[-1].time = localtime()
    formdata1.store()
    formdata1.jump_status('new')
    formdata1.evolution[-1].time = localtime() - datetime.timedelta(days=7)
    formdata1.jump_status('accepted')
    formdata1.jump_status('new')
    formdata1.evolution[-1].time = localtime() - datetime.timedelta(days=1)

    pub.apply_global_action_timeouts()
    assert formdef.data_class().get(formdata1.id).get_criticality_level_object().name == 'green'

    formdata1.evolution[-1].time = localtime() - datetime.timedelta(days=4)
    formdata1.store()
    pub.apply_global_action_timeouts()
    assert formdef.data_class().get(formdata1.id).get_criticality_level_object().name == 'yellow'
    formdata1.store()

    # limit trigger to formdata with "accepted" status
    trigger.anchor_status_latest = 'wf-accepted'
    workflow.store()
    pub.apply_global_action_timeouts()
    assert formdef.data_class().get(formdata1.id).get_criticality_level_object().name == 'green'
    formdata1.store()

    # limit trigger to formdata with "new" status
    trigger.anchor_status_latest = 'wf-new'
    workflow.store()
    pub.apply_global_action_timeouts()
    assert formdef.data_class().get(formdata1.id).get_criticality_level_object().name == 'yellow'
    formdata1.store()

    # bad (obsolete) status: do nothing
    trigger.anchor_status_latest = 'wf-foobar'
    workflow.store()
    pub.apply_global_action_timeouts()
    assert formdef.data_class().get(formdata1.id).get_criticality_level_object().name == 'green'
    formdata1.store()

    # check trigger is not run on finalized formdata
    formdata1.jump_status('finished')
    formdata1.evolution[-1].time = localtime() - datetime.timedelta(days=4)
    formdata1.store()
    trigger.anchor = 'creation'
    workflow.store()
    pub.apply_global_action_timeouts()
    assert formdef.data_class().get(formdata1.id).get_criticality_level_object().name == 'green'
    formdata1.store()

    # check trigger is run on finalized formdata when anchor status is an
    # endpoint
    formdata1.jump_status('finished')
    formdata1.evolution[-1].last_jump_datetime = None
    formdata1.evolution[-1].time = localtime() - datetime.timedelta(days=4)
    formdata1.store()
    trigger.anchor = 'latest-arrival'
    trigger.anchor_status_latest = 'wf-finished'
    workflow.store()
    pub.apply_global_action_timeouts()
    assert formdef.data_class().get(formdata1.id).get_criticality_level_object().name == 'yellow'
    formdata1.store()

    # check "finalized" anchor
    trigger.anchor = 'finalized'
    workflow.store()
    pub.apply_global_action_timeouts()
    assert formdef.data_class().get(formdata1.id).get_criticality_level_object().name == 'yellow'
    pub.apply_global_action_timeouts()
    assert formdef.data_class().get(formdata1.id).get_criticality_level_object().name == 'yellow'
    formdata1.store()

    formdata1.jump_status('new')
    formdata1.evolution[-1].time = localtime() - datetime.timedelta(days=4)
    formdata1.evolution[-1].last_jump_datetime = None
    formdata1.store()

    # django template
    trigger.anchor = 'template'
    trigger.anchor_template = '{{ form_receipt_date|date:"Y-m-d" }}'
    workflow.store()
    pub.apply_global_action_timeouts()
    assert formdef.data_class().get(formdata1.id).get_criticality_level_object().name == 'yellow'
    formdata1.store()

    # django template (with local date format)
    trigger.anchor = 'template'
    trigger.anchor_template = '{{ form_receipt_date }}'
    workflow.store()
    pub.apply_global_action_timeouts()
    assert formdef.data_class().get(formdata1.id).get_criticality_level_object().name == 'yellow'
    formdata1.store()

    # django template (with local date/time format)
    trigger.anchor = 'template'
    trigger.anchor_template = '{{ form_receipt_datetime }}'
    workflow.store()
    pub.apply_global_action_timeouts()
    assert formdef.data_class().get(formdata1.id).get_criticality_level_object().name == 'yellow'
    formdata1.store()

    # django template (from form_option_)
    workflow.variables_formdef = WorkflowVariablesFieldsFormDef(workflow=workflow)
    workflow.variables_formdef.fields = [
        DateField(id='4', label='Date', varname='date'),
    ]
    trigger.anchor = 'template'
    trigger.anchor_template = '{{ form_option_date }}'
    workflow.store()
    formdef.workflow_options = {
        'date': time.strptime('2015-05-12', '%Y-%m-%d'),
    }
    formdef.store()
    pub.apply_global_action_timeouts()
    assert formdef.data_class().get(formdata1.id).get_criticality_level_object().name == 'yellow'
    formdata1.store()

    # template as timeout value
    trigger.anchor = 'latest-arrival'
    trigger.anchor_status_latest = 'wf-accepted'
    trigger.timeout = '{{ form_option_days }}'
    workflow.store()

    # * invalid value
    LoggedError.wipe()
    formdata1.jump_status('accepted')
    formdata1.evolution[-1].time = localtime() - datetime.timedelta(days=1)
    formdata1.store()
    pub.apply_global_action_timeouts()
    assert formdef.data_class().get(formdata1.id).get_criticality_level_object().name == 'green'
    formdata1.store()
    assert LoggedError.count() == 1
    error = LoggedError.select()[0]
    assert error.summary == 'Timeouts: Error computing timeout'
    assert error.context == {'stack': [{'template': '{{ form_option_days }}'}]}

    # * ok value but too short for timeout
    LoggedError.wipe()
    workflow.variables_formdef = WorkflowVariablesFieldsFormDef(workflow=workflow)
    workflow.variables_formdef.fields = [
        StringField(id='5', label='Days', varname='days'),
    ]
    workflow.store()
    formdef.workflow_options = {'days': '2'}
    formdef.store()
    formdata1.store()
    pub.apply_global_action_timeouts()
    assert formdef.data_class().get(formdata1.id).get_criticality_level_object().name == 'green'
    assert LoggedError.count() == 0

    # * ok value, and timeout is triggered
    formdata1.evolution[-1].time = localtime() - datetime.timedelta(days=4)
    formdata1.store()
    pub.apply_global_action_timeouts()
    assert formdef.data_class().get(formdata1.id).get_criticality_level_object().name == 'yellow'
    assert LoggedError.count() == 0

    # decimal default value
    LoggedError.wipe()
    workflow.variables_formdef = WorkflowVariablesFieldsFormDef(workflow=workflow)
    workflow.variables_formdef.fields = [
        NumericField(id='5', label='Days', varname='days', default_value=decimal.Decimal('1E+2')),
    ]
    workflow.store()
    formdef.workflow_options = {}
    formdef.store()
    formdata1.evolution[-1].time = localtime() - datetime.timedelta(days=105)
    formdata1.store()
    pub.apply_global_action_timeouts()
    assert formdef.data_class().get(formdata1.id).get_criticality_level_object().name == 'yellow'
    assert LoggedError.count() == 0

    # notation with exponent
    LoggedError.wipe()
    workflow.variables_formdef = WorkflowVariablesFieldsFormDef(workflow=workflow)
    workflow.variables_formdef.fields = [
        StringField(id='5', label='Days', varname='days'),
    ]
    workflow.store()
    formdef.workflow_options = {'days': '1E+2'}
    formdef.store()
    formdata1.evolution[-1].time = localtime() - datetime.timedelta(days=105)
    formdata1.store()
    pub.apply_global_action_timeouts()
    assert formdef.data_class().get(formdata1.id).get_criticality_level_object().name == 'yellow'
    assert LoggedError.count() == 0


@pytest.mark.parametrize(
    'timeout',
    [
        # (expression, fast-path)
        (2, True),
        ('2', True),
        ('{{ form_option_timeout }}', True),
        ('{{ form_var_timeout }}', False),
        ('{% firstof form_var_timeout form_option_timeout %}', False),
        ('{{ form.option.timeout }}', True),
        ('{{ form.var.timeout }}', False),  # not quickly dismissed
    ],
)
def test_global_timeouts_finalized(pub, sql_queries, timeout):
    CardDef.wipe()
    FormDef.wipe()
    Workflow.wipe()

    workflow = Workflow(name='global-timeouts')
    workflow.possible_status = Workflow.get_default_workflow().possible_status[:]
    workflow.criticality_levels = [
        WorkflowCriticalityLevel(name='green'),
        WorkflowCriticalityLevel(name='yellow'),
        WorkflowCriticalityLevel(name='red'),
    ]
    action = workflow.add_global_action('Timeout Test')
    action.add_action('modify_criticality')
    trigger = action.append_trigger('timeout')
    trigger.anchor = 'finalized'
    trigger.timeout = timeout[0]
    workflow.variables_formdef = WorkflowVariablesFieldsFormDef(workflow=workflow)
    workflow.variables_formdef.fields = [StringField(id='1', label='Timeout', varname='timeout')]
    workflow.store()

    formdef = FormDef()
    formdef.name = 'baz'
    formdef.fields = [StringField(id='1', label='Timeout', varname='timeout')]
    formdef.workflow_id = workflow.id
    formdef.workflow_options = {'timeout': '2'}
    formdef.store()
    formdef.data_class().wipe()

    formdata1 = formdef.data_class()()
    formdata1.data = {'1': '2'}
    formdata1.just_created()
    formdata1.store()
    formdata1.jump_status('finished')
    formdata1.evolution[-1].time = localtime() - datetime.timedelta(days=4)
    formdata1.store()

    formdata2 = formdef.data_class()()
    formdata2.data = {'1': '2'}
    formdata2.just_created()
    formdata2.store()
    formdata2.jump_status('finished')
    formdata2.evolution[-1].time = localtime() - datetime.timedelta(days=1)
    formdata2.store()

    formdef2 = FormDef()
    formdef2.name = 'bax'
    formdef2.fields = [StringField(id='1', label='Timeout', varname='timeout')]
    formdef2.workflow_id = workflow.id
    formdef2.workflow_options = {'timeout': '5'}
    formdef2.store()
    formdef2.data_class().wipe()

    formdata3 = formdef2.data_class()()
    formdata3.data = {'1': '5'}
    formdata3.just_created()
    formdata3.store()
    formdata3.jump_status('finished')
    formdata3.evolution[-1].time = localtime() - datetime.timedelta(days=6)
    formdata3.store()

    formdata4 = formdef2.data_class()()
    formdata4.data = {'1': '5'}
    formdata4.just_created()
    formdata4.store()
    formdata4.jump_status('finished')
    formdata4.evolution[-1].time = localtime() - datetime.timedelta(days=4)
    formdata4.store()

    pub.apply_global_action_timeouts()
    pub.apply_global_action_timeouts()
    assert bool([x for x in sql_queries if 'NOW() - 2' in x]) is timeout[1]

    assert formdef.data_class().get(formdata1.id).get_criticality_level_object().name == 'yellow'
    assert formdef.data_class().get(formdata2.id).get_criticality_level_object().name == 'green'

    if 'timeout' in str(timeout[0]):
        # templated "5", only one will match
        assert formdef.data_class().get(formdata3.id).get_criticality_level_object().name == 'yellow'
        assert formdef.data_class().get(formdata4.id).get_criticality_level_object().name == 'green'
    else:
        # hardcoded "2", all will match
        assert formdef2.data_class().get(formdata3.id).get_criticality_level_object().name == 'yellow'
        assert formdef2.data_class().get(formdata4.id).get_criticality_level_object().name == 'yellow'


def test_global_timeouts_latest_arrival(pub):
    FormDef.wipe()
    Workflow.wipe()

    workflow = Workflow(name='global-timeouts')
    workflow.possible_status = Workflow.get_default_workflow().possible_status[:]
    workflow.criticality_levels = [
        WorkflowCriticalityLevel(name='green'),
        WorkflowCriticalityLevel(name='yellow'),
        WorkflowCriticalityLevel(name='red'),
    ]
    action = workflow.add_global_action('Timeout Test')
    action.add_action('modify_criticality')
    trigger = action.append_trigger('timeout')
    trigger.anchor = 'latest-arrival'
    trigger.anchor_status_latest = 'wf-new'
    trigger.timeout = '2'
    workflow.store()

    formdef = FormDef()
    formdef.name = 'baz'
    formdef.fields = []
    formdef.workflow_id = workflow.id
    formdef.store()
    formdef.data_class().wipe()

    formdata1 = formdef.data_class()()
    formdata1.just_created()
    formdata1.store()

    formdata1.jump_status('new')
    # enter in status 8 days ago
    formdata1.evolution[-1].time = localtime() - datetime.timedelta(days=8)
    formdata1.store()
    # but get a new comment 1 day ago
    formdata1.evolution.append(Evolution(formdata1))
    formdata1.evolution[-1].time = localtime() - datetime.timedelta(days=1)
    formdata1.evolution[-1].comment = 'plop'
    formdata1.store()
    pub.apply_global_action_timeouts()
    # no change
    assert formdef.data_class().get(formdata1.id).get_criticality_level_object().name == 'green'

    formdata1.evolution[-1].time = localtime() - datetime.timedelta(days=5)
    formdata1.store()
    pub.apply_global_action_timeouts()
    # change
    assert formdef.data_class().get(formdata1.id).get_criticality_level_object().name == 'yellow'

    # check it applies even after the status has been left
    formdata1 = formdef.data_class()()
    formdata1.just_created()
    formdata1.store()
    formdata1.jump_status('new')
    formdata1.evolution[-1].time = localtime() - datetime.timedelta(days=5)
    formdata1.store()
    formdata1.jump_status('accepted')
    formdata1.store()
    pub.apply_global_action_timeouts()
    assert formdef.data_class().get(formdata1.id).get_criticality_level_object().name == 'yellow'

    # but not if an endpoint has been reached
    formdata1 = formdef.data_class()()
    formdata1.just_created()
    formdata1.store()
    formdata1.jump_status('new')
    formdata1.evolution[-1].time = localtime() - datetime.timedelta(days=5)
    formdata1.store()
    formdata1.jump_status('accepted')
    formdata1.jump_status('finished')
    formdata1.store()
    pub.apply_global_action_timeouts()
    assert formdef.data_class().get(formdata1.id).get_criticality_level_object().name == 'green'


def test_global_timeouts_anonymisation(pub):
    FormDef.wipe()
    Workflow.wipe()

    workflow = Workflow(name='global-timeouts')
    workflow.possible_status = Workflow.get_default_workflow().possible_status[:]
    workflow.criticality_levels = [
        WorkflowCriticalityLevel(name='green'),
        WorkflowCriticalityLevel(name='yellow'),
        WorkflowCriticalityLevel(name='red'),
    ]
    action = workflow.add_global_action('Timeout Test')
    action.add_action('modify_criticality')
    trigger = action.append_trigger('timeout')
    trigger.anchor = 'anonymisation'
    trigger.timeout = '2'
    workflow.store()

    formdef = FormDef()
    formdef.name = 'baz'
    formdef.fields = []
    formdef.workflow_id = workflow.id
    formdef.store()
    formdef.data_class().wipe()

    formdata1 = formdef.data_class()()
    formdata1.just_created()
    formdata1.store()
    formdata1.jump_status('new')

    # do not run on non anonymised data
    pub.apply_global_action_timeouts()
    assert formdef.data_class().get(formdata1.id).get_criticality_level_object().name == 'green'

    # do not run on this one that just got anonymised
    formdata1.anonymise()
    pub.apply_global_action_timeouts()
    assert formdef.data_class().get(formdata1.id).get_criticality_level_object().name == 'green'

    # run on aged anonymised formdata
    formdata1.anonymised = formdata1.anonymised - datetime.timedelta(days=5)
    formdata1.store()
    pub.apply_global_action_timeouts()
    assert formdef.data_class().get(formdata1.id).get_criticality_level_object().name == 'yellow'


def test_redirect_to_url(pub):
    formdef = FormDef()
    formdef.name = 'baz'
    formdef.fields = [
        StringField(id='1', label='Test', varname='foo'),
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {'1': 'bar'}

    item = RedirectToUrlWorkflowStatusItem()
    assert item.render_as_line() == 'Web Redirection (not configured)'
    item.url = 'https://www.example.net/?foo=[form_var_foo]'
    assert item.render_as_line() == 'Web Redirection (to https://www.example.net/?foo=[form_var_foo])'
    pub.substitutions.feed(formdata)
    assert item.perform(formdata) == 'https://www.example.net/?foo=bar'

    item.url = 'https://www.example.net/?django={{ form_var_foo }}'
    assert item.render_as_line() == 'Web Redirection (to https://www.example.net/?django={{ form_var_foo }})'
    pub.substitutions.feed(formdata)
    assert item.perform(formdata) == 'https://www.example.net/?django=bar'

    item.url = '[if-any nada]https://www.example.net/[end]'
    pub.substitutions.feed(formdata)
    assert item.perform(formdata) is None

    item.url = '{% if nada %}https://www.example.net/{% endif %}'
    pub.substitutions.feed(formdata)
    assert item.perform(formdata) is None


def test_workflow_action_condition(pub):
    Workflow.wipe()
    workflow = Workflow(name='jump condition migration')
    st1 = workflow.add_status('Status1', 'st1')
    workflow.store()

    role = pub.role_class(name='bar1')
    role.store()

    user = pub.user_class()
    user.roles = [role.id]
    user.store()

    choice = st1.add_action('choice', id='_x')
    choice.by = [role.id]
    workflow.store()

    formdef = FormDef()
    formdef.name = 'baz'
    formdef.fields = [
        StringField(id='1', label='Test', varname='foo'),
    ]
    formdef.workflow_id = workflow.id
    formdef.store()

    formdef.data_class().wipe()

    formdata1 = formdef.data_class()()
    formdata1.data = {'1': 'foo'}
    formdata1.just_created()
    formdata1.store()

    formdata2 = formdef.data_class()()
    formdata2.data = {'2': 'bar'}
    formdata2.just_created()
    formdata2.store()

    assert formdata1.get_actions_roles() == {role.id}
    assert formdata2.get_actions_roles() == {role.id}

    assert len(FormDef.get(formdef.id).data_class().get_actionable_ids([role.id])) == 2

    choice.condition = {'type': 'django', 'value': 'form_var_foo == "foo"'}
    workflow.store()
    pub.process_after_jobs()

    with pub.substitutions.temporary_feed(formdata1):
        assert FormDef.get(formdef.id).data_class().get(formdata1.id).get_actions_roles() == {role.id}
    with pub.substitutions.temporary_feed(formdata2):
        assert FormDef.get(formdef.id).data_class().get(formdata2.id).get_actions_roles() == set()

    assert len(FormDef.get(formdef.id).data_class().get_actionable_ids([role.id])) == 1

    # check with a formdef condition
    choice.condition = {'type': 'django', 'value': 'form_name == "test"'}
    workflow.store()
    pub.process_after_jobs()
    assert len(FormDef.get(formdef.id).data_class().get_actionable_ids([role.id])) == 0

    choice.condition = {'type': 'django', 'value': 'form_name == "baz"'}
    workflow.store()
    pub.process_after_jobs()
    assert len(FormDef.get(formdef.id).data_class().get_actionable_ids([role.id])) == 2

    # check with a condition on session (session data should be ignored)
    pub.get_request().session.extra_variables = {'foo': 'bar'}
    pub.substitutions.feed(pub.get_request().session)
    choice.condition = {'type': 'django', 'value': 'session_var_foo == "bar"'}
    workflow.store()
    pub.process_after_jobs()
    assert len(FormDef.get(formdef.id).data_class().get_actionable_ids([role.id])) == 0

    # bad condition
    LoggedError.wipe()
    choice.condition = {'type': 'django', 'value': 'foobar = barfoo'}
    workflow.store()
    pub.process_after_jobs()
    assert len(FormDef.get(formdef.id).data_class().get_actionable_ids([role.id])) == 0
    assert LoggedError.count() == 1
    logged_error = LoggedError.select()[0]
    assert logged_error.occurences_count > 1  # should be 2... == 12 with pickle, 4 with sql
    assert logged_error.summary == 'Failed to evaluate condition'
    assert logged_error.exception_class == 'TemplateSyntaxError'
    assert logged_error.exception_message == "Could not parse the remainder: '=' from '='"
    assert logged_error.context == {
        'stack': [
            {
                'condition': 'foobar = barfoo',
                'condition_type': 'django',
                'source_label': 'Manual Jump',
                'source_url': 'http://example.net/backoffice/workflows/1/status/st1/items/_x/',
            }
        ]
    }


def test_workflow_field_migration(pub):
    Workflow.wipe()
    wf = Workflow(name='wf with backoffice field')
    wf.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(wf)
    wf.backoffice_fields_formdef.fields = [
        StringField(id='bo1', label='bo field 1', in_listing=True),
    ]
    wf.add_status('Status1')
    wf.store()

    wf = Workflow.get(wf.id)
    assert wf.backoffice_fields_formdef.fields[0].display_locations == ['validation', 'summary', 'listings']


def test_aggregation_email(pub, emails):
    Workflow.wipe()
    pub.role_class.wipe()
    AggregationEmail.wipe()

    role = pub.role_class(name='foobar')
    role.emails = ['foobar@localhost']
    role.emails_to_members = False
    role.store()

    workflow = Workflow(name='aggregation-email')
    workflow.possible_status = Workflow.get_default_workflow().possible_status[:]
    aggregation = workflow.possible_status[1].add_action('aggregationemail', prepend=True)
    assert aggregation.get_line_details() == 'not completed'
    aggregation.to = [role.id]
    assert aggregation.get_line_details() == 'to foobar'
    workflow.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'foobar'
    formdef.fields = []
    formdef.workflow_id = workflow.id
    formdef.store()
    formdef.data_class().wipe()

    for i in range(5):
        formdata = formdef.data_class()()
        formdata.data = {}
        formdata.store()
        formdata.just_created()
        formdata.perform_workflow()
        assert AggregationEmail.count() == 1

    send_aggregation_emails(pub)
    assert AggregationEmail.count() == 0
    assert 'New arrivals' in emails.emails
    for i in range(5):
        assert (
            'http://example.net/foobar/%s/status (New)' % (i + 1) in emails.emails['New arrivals']['payload']
        )

    emails.empty()
    send_aggregation_emails(pub)
    assert 'New arrivals' not in emails.emails

    role.emails = []
    role.emails_to_members = True
    role.store()

    user = pub.user_class(name='bar')
    user.email = 'bar@localhost'
    user.roles = [role.id]
    user.store()

    formdata.perform_workflow()
    assert AggregationEmail.count() == 1

    send_aggregation_emails(pub)
    assert AggregationEmail.count() == 0
    assert 'New arrivals' in emails.emails
    assert (
        'http://example.net/foobar/%s/status (New)' % formdata.id in emails.emails['New arrivals']['payload']
    )


def test_form_update_after_backoffice_fields(pub):
    wf = Workflow(name='wf with backoffice field')
    wf.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(wf)
    wf.backoffice_fields_formdef.fields = [
        StringField(id='bo1', label='bo field 1'),
    ]
    wf.add_status('Status1')
    wf.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'baz'
    formdef.fields = [
        StringField(id='1', label='field 1'),
    ]
    formdef.workflow_id = wf.id
    formdef.store()

    _, cur = sql.get_connection_and_cursor()
    assert column_exists_in_table(cur, formdef.table_name, 'fbo1')

    wf.backoffice_fields_formdef.fields = [
        StringField(id='bo1', label='bo field 1'),
        StringField(id='bo2', label='bo field 2'),
    ]
    wf.backoffice_fields_formdef.store()
    pub.process_after_jobs()
    assert column_exists_in_table(cur, formdef.table_name, 'fbo2')

    # remove first and add third field
    wf.backoffice_fields_formdef.fields = [
        StringField(id='bo2', label='bo field 2'),
        StringField(id='bo3', label='bo field 3'),
    ]
    wf.backoffice_fields_formdef.store()
    pub.process_after_jobs()
    assert not column_exists_in_table(cur, formdef.table_name, 'fbo1')
    assert column_exists_in_table(cur, formdef.table_name, 'fbo3')

    cur.close()


def test_remove_tracking_code(pub):
    formdef = FormDef()
    formdef.name = 'baz'
    formdef.enable_tracking_codes = True
    formdef.store()

    formdef.data_class().wipe()
    formdata = formdef.data_class()()
    formdata.store()
    code = TrackingCode()
    code.formdata = formdata
    formdata.refresh_from_storage()

    assert formdata.tracking_code
    assert TrackingCode.count() == 1

    item = RemoveTrackingCodeWorkflowStatusItem()
    item.perform(formdata)
    assert not formdata.tracking_code
    assert TrackingCode.count() == 0
    item.perform(formdata)  # do not crash if no tracking_code

    item.replace = True
    item.perform(formdata)
    assert formdata.tracking_code
    assert TrackingCode.count() == 1
    tracking_code_orig = formdata.tracking_code
    item.perform(formdata)
    assert formdata.tracking_code
    assert formdata.tracking_code != tracking_code_orig
    assert TrackingCode.count() == 1

    # cannot replace if formdef not handles tracking code
    formdef.enable_tracking_codes = False
    formdef.store()
    item.perform(formdata)
    assert not formdata.tracking_code
    assert TrackingCode.count() == 0


def test_removal_of_obsolete_action_classes(pub):
    Workflow.wipe()
    workflow = Workflow(name='wf')
    workflow.store()
    workflow = Workflow.get(1)

    # workflow with a reference to RedirectToStatusWorkflowStatusItem
    old_pickled_workflow = (
        b'ccopy_reg\n_reconstructor\np0\n(cwcs.workflows\nWorkflow\np1\nc__builtin__\nobject\n'
        b'p2\nNtp3\nRp4\n(dp5\nVid\np6\nV1\np7\nsVname\np8\nVtest\np9\nsVpossible_status\np10\n'
        b'(lp11\ng0\n(cwcs.workflows\nWorkflowStatus\np12\ng2\nNtp13\nRp14\n(dp15\ng8\nVst1\np16\n'
        b'sVitems\np17\n(lp18\ng0\n(cwcs.wf.redirect_to_status\nRedirectToStatusWorkflowStatusItem\n'
        b'p19\ng2\nNtp20\nRp21\n(dp22\ng6\ng7\nsbasg6\ng7\nsbasVroles\np23\n(dp24\nsVglobal_actions\n'
        b'p25\n(lp26\nsVcriticality_levels\np27\n(lp28\nsb.'
    )
    _, cur = sql.get_connection_and_cursor()
    cur.execute(
        'UPDATE workflows SET params = %(params)s WHERE id = %(id)s',
        {'params': old_pickled_workflow, 'id': workflow.id},
    )
    cur.close()

    workflow = Workflow.get(1)
    assert workflow.possible_status[0].items == []


def test_parts_are_saved_on_each_action(pub):
    Workflow.wipe()

    workflow = Workflow(name='register comment to')
    workflow.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(workflow)
    workflow.backoffice_fields_formdef.fields = [
        StringField(id='bo0', varname='bo', label='bo variable'),
    ]
    st0 = workflow.add_status('Status0', 'st0')
    workflow.add_status('Status1', 'st1')

    item = st0.add_action('set-backoffice-fields', id='set-bo')
    item.fields = [{'field_id': 'bo0', 'value': 'foobar'}]

    item = st0.add_action('sendmail')
    item.to = ['_submitter']
    item.subject = 'Foobar'
    item.body = 'Hello'
    item.varname = 'foobar'

    item = st0.add_action('jump')
    item.status = 'st1'

    workflow.store()

    user = pub.user_class(name='baruser')
    user.email = 'foo@bar.com'
    user.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'foobar'
    formdef.url_name = 'foobar'
    formdef.workflow = workflow
    formdef.store()

    formdata = formdef.data_class()()
    formdata.user = user
    formdata.just_created()
    formdata.store()
    formdata.perform_workflow()
    formdata.store()

    formdata = formdef.data_class().get(formdata.id)
    parts = list(formdata.iter_evolution_parts())
    assert parts
    assert any(isinstance(part, EmailEvolutionPart) for part in parts)


def test_get_computed_strings(pub):
    workflow = Workflow(name='test')
    status = workflow.add_status('Status0', 'st0')
    action = status.add_action('create_formdata')
    action.user_association_template = '{{ 1 }}'
    assert '{{ 1 }}' not in action.get_computed_strings()
    action.user_association_mode = 'custom'
    assert '{{ 1 }}' in action.get_computed_strings()

    action = status.add_action('dispatch')
    action.role_id = '{{ 1 }}'
    action.variable = '{{ 2 }}'
    assert action.dispatch_type == 'manual'
    assert '{{ 1 }}' in action.get_computed_strings()
    assert '{{ 2 }}' not in action.get_computed_strings()
    action.dispatch_type = 'automatic'
    assert '{{ 1 }}' not in action.get_computed_strings()
    assert '{{ 2 }}' in action.get_computed_strings()

    action = status.add_action('external_workflow_global_action')
    action.target_id = '{{ 1 }}'
    assert '{{ 1 }}' not in action.get_computed_strings()
    action.target_mode = 'manual'
    assert '{{ 1 }}' in action.get_computed_strings()

    action = status.add_action('geolocate')
    action.address_string = '{{ 1 }}'
    assert '{{ 1 }}' in action.get_computed_strings()
    action.method = 'map_variable'
    assert '{{ 1 }}' not in action.get_computed_strings()

    action = status.add_action('notification')
    action.users_template = '{{ 1 }}'
    assert action.to
    assert '{{ 1 }}' not in action.get_computed_strings()
    action.to = None
    assert '{{ 1 }}' in action.get_computed_strings()


def test_status_colour_migration(pub):
    FormDef.wipe()

    workflow = Workflow(name='criticality')
    st1 = workflow.add_status('st1')
    st1.colour = 'FF0000'
    workflow.store()

    workflow.refresh_from_storage()
    assert workflow.possible_status[0].colour == '#FF0000'


def test_visibility_migration(pub):
    workflow = Workflow(name='visibility')
    workflow.roles = {'_reveiver': 'Receiver', '_other': 'Other function'}
    st1 = workflow.add_status('st1')
    st1.visibility = ['_receiver', '_other']
    st2 = workflow.add_status('st2')
    st2.visibility = ['__hidden__']
    workflow.add_status('st3')
    workflow.store()

    workflow.refresh_from_storage()
    assert workflow.possible_status[0].visibility == ['__restricted__']
    assert workflow.possible_status[1].visibility == ['__hidden__']
    assert not workflow.possible_status[2].visibility


def test_variables_formdef_clean_prefill(pub):
    workflow = Workflow(name='variables')
    workflow.variables_formdef = WorkflowVariablesFieldsFormDef(workflow=workflow)
    workflow.variables_formdef.fields.append(
        StringField(label='Test', default_value='123', prefill={'type': 'string', 'value': 'plop'})
    )
    workflow.store()

    workflow = Workflow.get(id=workflow.id)
    assert not workflow.variables_formdef.fields[0].prefill


def test_variables_formdef_migrate_numeric(pub):
    workflow = Workflow(name='variables')
    workflow.variables_formdef = WorkflowVariablesFieldsFormDef(workflow=workflow)
    workflow.variables_formdef.fields.append(
        NumericField(label='Test', default_value=decimal.Decimal('1E+2'))
    )
    workflow.store()

    workflow = Workflow.get(id=workflow.id)
    assert str(workflow.variables_formdef.fields[0].default_value) == '100'


def test_status_waitpoint_calculation(pub):
    Workflow.wipe()
    workflow = Workflow(name='test')
    st1 = workflow.add_status('st1')
    st2 = workflow.add_status('st2')

    assert st1.is_waitpoint()
    assert st2.is_waitpoint()

    action = st1.add_action('jump')
    action.status = st2.id

    assert not st1.is_waitpoint()
    assert st2.is_waitpoint()

    action.timeout = 10
    assert st1.is_waitpoint()
    assert st2.is_waitpoint()

    action.timeout = None
    st1.forced_endpoint = True
    assert st1.is_waitpoint()
    assert st2.is_waitpoint()


def test_single_reindex_on_workflow_change(pub):
    AfterJob.wipe()
    Workflow.wipe()
    FormDef.wipe()

    wf1 = Workflow(name='wf1')
    wf1.add_status('status')
    wf1.roles['_other'] = 'Other Function'
    wf1.store()

    wf1.roles['_yet_another'] = 'Yet another Function'
    wf1.store()

    jobs = AfterJob.select(order_by='creation_time')
    assert [x.abort_requested for x in jobs] == [True, False]
    pub.process_after_jobs()
    jobs = AfterJob.select(order_by='creation_time')
    assert [x.status for x in jobs] == ['aborted', 'completed']
