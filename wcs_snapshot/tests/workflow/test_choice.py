import pytest
from pyquery import PyQuery
from quixote import cleanup

from wcs import sessions
from wcs.formdef import FormDef
from wcs.qommon.http_request import HTTPRequest
from wcs.workflow_traces import WorkflowTrace
from wcs.workflows import Workflow

from ..form_pages.test_all import create_user
from ..utilities import clean_temporary_pub, create_temporary_pub, get_app, login


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
    req.session = sessions.BasicSession(id=1)
    pub.set_config(req)
    return pub


def test_choice_button_no_label(pub):
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

    choice = st1.add_action('choice', id='_x')
    choice.by = [role.id]

    choice2 = st1.add_action('choice', id='_x2')
    choice2.label = 'TEST'
    choice2.by = [role.id]

    wf.store()

    formdef.workflow = wf
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()
    assert formdata.get_status().name == 'Status1'

    form = formdata.get_workflow_form(user)
    form.render()
    assert str(form.render()).count('<button') == 1
    assert '>TEST</button>' in str(form.render())


def test_choice_button_template_label(pub):
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

    choice = st1.add_action('choice', id='_x')
    choice.label = '{{ "a"|add:"b" }}'
    choice.by = [role.id]

    wf.store()

    formdef.workflow = wf
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()

    form = formdata.get_workflow_form(user)
    form.render()
    assert '>ab</button>' in str(form.render())

    # check ezt template are not interpreted
    choice.label = '[if-any test]a[else]b[end]'
    wf.store()

    formdata.refresh_from_storage()
    form = formdata.get_workflow_form(user)
    form.render()
    assert '>[if-any test]a[else]b[end]</button>' in str(form.render())


def test_choice_line_details(pub):
    workflow = Workflow(name='choice')
    st1 = workflow.add_status('Status1', 'st1')
    choice = st1.add_action('choice')

    assert choice.get_line_details() == 'not completed'

    choice.status = 'wf-%s' % st1.id
    choice.label = 'foobar'
    assert choice.get_line_details() == '"foobar", to Status1'

    role = pub.role_class(name='foorole')
    role.store()
    choice.by = [role.id]
    assert choice.get_line_details() == '"foobar", to Status1, by foorole'

    choice.by = ['_receiver']
    assert choice.get_line_details() == '"foobar", to Status1, by Recipient'

    choice.by = ['logged-users']
    assert choice.get_line_details() == '"foobar", to Status1, by Logged Users'


def test_multiple_choices_with_same_identifier(pub):
    user = create_user(pub)

    workflow = Workflow(name='choice')
    st1 = workflow.add_status('Status1')
    st2 = workflow.add_status('Status2')
    st3 = workflow.add_status('Status3')
    choice1 = st1.add_action('choice')
    choice1.label = 'foobar1'
    choice1.varname = 'foobar'
    choice1.by = ['logged-users']
    choice1.status = f'wf-{st2.id}'

    choice2 = st1.add_action('choice')
    choice2.label = 'foobar2'
    choice2.varname = 'foobar'
    choice2.by = ['logged-users']
    choice1.status = f'{st2.id}'
    choice2.status = f'{st3.id}'
    workflow.store()

    formdef = FormDef()
    formdef.name = 'baz'
    formdef.fields = []
    formdef.workflow = workflow
    formdef.store()

    formdata = formdef.data_class()()
    formdata.user_id = user.id
    formdata.just_created()
    formdata.store()

    app = login(get_app(pub), username='foo', password='foo')
    resp = app.get(formdata.get_url())
    resp = resp.form.submit('button2')
    resp = resp.follow()
    formdata.refresh_from_storage()
    assert formdata.status == f'wf-{st3.id}'


def test_choice_button_confirmation(pub):
    user = pub.user_class()
    user.store()

    wf = Workflow(name='status')
    st1 = wf.add_status('Status1', 'st1')
    choice = st1.add_action('choice', id='_x')
    choice.label = 'button'
    choice.by = ['logged-users']
    wf.store()

    formdef = FormDef()
    formdef.name = 'baz'
    formdef.fields = []
    formdef.store()
    formdef.workflow = wf
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()

    form = formdata.get_workflow_form(user)
    html_form = PyQuery(str(form.render()))
    assert html_form.find('button').attr('data-ask-for-confirmation') is None

    choice.require_confirmation = True
    form = formdata.get_workflow_form(user)
    html_form = PyQuery(str(form.render()))
    assert html_form.find('button').attr('data-ask-for-confirmation') == 'true'

    choice.require_confirmation = True
    choice.confirmation_text = 'Are you sure?'
    form = formdata.get_workflow_form(user)
    html_form = PyQuery(str(form.render()))
    assert html_form.find('button').attr('data-ask-for-confirmation') == 'Are you sure?'


def test_choice_workflow_event(pub):
    user = create_user(pub)

    workflow = Workflow(name='choice')
    st1 = workflow.add_status('Status1')
    st2 = workflow.add_status('Status2')
    choice1 = st1.add_action('choice')
    choice1.label = 'foobar1'
    choice1.by = ['logged-users']
    choice1.status = str(st2.id)
    workflow.store()

    formdef = FormDef()
    formdef.name = 'baz'
    formdef.fields = []
    formdef.workflow = workflow
    formdef.store()

    formdata = formdef.data_class()()
    formdata.user_id = user.id
    formdata.just_created()
    formdata.store()

    app = login(get_app(pub), username='foo', password='foo')
    resp = app.get(formdata.get_url())
    resp = resp.form.submit(f'button{choice1.id}')
    formdata.refresh_from_storage()
    assert formdata.status == f'wf-{st2.id}'

    traces = WorkflowTrace.select_for_formdata(formdata)
    assert [(x.event, x.event_args) for x in traces] == [('button', {'action_item_id': '1'})]
