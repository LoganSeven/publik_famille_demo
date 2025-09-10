import os
from unittest import mock

import pytest
import responses
from quixote import cleanup
from webtest import Upload

from wcs import fields
from wcs.blocks import BlockDef
from wcs.data_sources import NamedDataSource
from wcs.formdef import FormDef
from wcs.qommon.http_request import HTTPRequest
from wcs.qommon.ident.password_accounts import PasswordAccount
from wcs.qommon.substitution import CompatibilityNamesDict
from wcs.wf.form import WorkflowFormFieldsFormDef
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
    pub.set_config(req)
    return pub


def create_formdef():
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = []
    formdef.store()
    return formdef


def test_migrate_hide_submit_button(pub):
    Workflow.wipe()

    wf = Workflow(name='xxx')
    st1 = wf.add_status('Status1')
    st1.add_action('form')
    wf.store()

    # new value is to hide
    assert wf.possible_status[0].items[0].hide_submit_button is True

    # new value is kept on reload
    wf = Workflow.get(wf.id)
    assert wf.possible_status[0].items[0].hide_submit_button is True

    # simulate older action, without a value set
    del wf.possible_status[0].items[0].__dict__['hide_submit_button']
    wf.store()

    wf = Workflow.get(wf.id)
    assert wf.possible_status[0].items[0].hide_submit_button is False

    # simulate older action, with a value set (no change)
    wf.possible_status[0].items[0].hide_submit_button = True
    wf.store()

    wf = Workflow.get(wf.id)
    assert wf.possible_status[0].items[0].hide_submit_button is True


def test_frontoffice_workflow_form_with_conditions(pub):
    user = create_user(pub)
    wf = Workflow.get_default_workflow()
    wf.id = '2'
    wf.store()
    wf = Workflow.get(wf.id)
    status = wf.get_status('new')
    display_form = status.add_action('form', id='_display_form')
    display_form.by = ['_submitter']
    display_form.varname = 'blah'
    display_form.formdef = WorkflowFormFieldsFormDef(item=display_form)
    display_form.formdef.fields = [
        fields.StringField(id='1', label='Test', varname='str', required='required'),
        fields.StringField(id='2', label='Test2', varname='str2', required='required'),
    ]

    wf.store()
    formdef = create_formdef()
    formdef.workflow_id = wf.id
    formdef.fields = [fields.StringField(id='0', label='string', varname='plop')]
    formdef.store()

    formdef.data_class().wipe()

    formdata = formdef.data_class()()
    formdata.user_id = user.id
    formdata.just_created()
    formdata.status = 'wf-new'
    formdata.data = {'0': 'plop'}
    formdata.store()

    app = login(get_app(pub), username='foo', password='foo')
    resp = app.get(formdata.get_url(backoffice=False))
    assert f'fblah_{display_form.id}_1' in resp.form.fields
    assert f'fblah_{display_form.id}_2' in resp.form.fields

    # check with static condition
    display_form.formdef.fields = [
        fields.StringField(id='1', label='Test', varname='str', required='required'),
        fields.StringField(
            id='2',
            label='Test2',
            varname='str2',
            required='required',
            condition={'type': 'django', 'value': '0'},
        ),
    ]
    wf.store()

    resp = login(get_app(pub), username='foo', password='foo').get(formdata.get_url(backoffice=False))
    assert f'fblah_{display_form.id}_1' in resp.form.fields
    assert f'fblah_{display_form.id}_2' not in resp.form.fields

    # check condition based on formdata
    display_form.formdef.fields = [
        fields.StringField(id='1', label='Test', varname='str', required='required'),
        fields.StringField(
            id='2',
            label='Test2',
            varname='str2',
            required='required',
            condition={'type': 'django', 'value': 'form_var_plop'},
        ),
    ]
    wf.store()

    resp = login(get_app(pub), username='foo', password='foo').get(formdata.get_url(backoffice=False))
    assert f'fblah_{display_form.id}_1' in resp.form.fields
    assert f'fblah_{display_form.id}_2' in resp.form.fields

    display_form.formdef.fields = [
        fields.StringField(id='1', label='Test', varname='str', required='required'),
        fields.StringField(
            id='2',
            label='Test2',
            varname='str2',
            required='required',
            condition={'type': 'django', 'value': 'form_var_plop != "xxx"'},
        ),
    ]
    wf.store()

    resp = login(get_app(pub), username='foo', password='foo').get(formdata.get_url(backoffice=False))
    assert f'fblah_{display_form.id}_1' in resp.form.fields
    assert f'fblah_{display_form.id}_2' in resp.form.fields

    for variable_name in (
        'blah_var_str',
        'form_workflow_data_blah_var_str',
        'form_workflow_form_blah_var_str',
    ):
        # check with live conditions
        display_form.formdef.fields = [
            fields.StringField(id='1', label='Test', varname='str', required='required'),
            fields.StringField(
                id='2',
                label='Test2',
                varname='str2',
                required='required',
                condition={'type': 'django', 'value': '%s == "xxx"' % variable_name},
            ),
        ]
        wf.store()

        resp = login(get_app(pub), username='foo', password='foo').get(formdata.get_url(backoffice=False))
        assert f'fblah_{display_form.id}_1' in resp.form.fields
        assert f'fblah_{display_form.id}_2' in resp.form.fields
        assert (
            resp.html.find('div', {'data-field-id': f'blah_{display_form.id}_1'}).attrs['data-live-source']
            == 'true'
        )
        assert (
            resp.html.find('div', {'data-field-id': f'blah_{display_form.id}_2'}).attrs.get('style')
            == 'display: none'
        )
        live_url = resp.html.find('form').attrs['data-live-url']
        resp.form[f'fblah_{display_form.id}_1'] = ''
        live_resp = app.post(live_url, params=resp.form.submit_fields())
        assert live_resp.json['result'][f'blah_{display_form.id}_1']['visible']
        assert not live_resp.json['result'][f'blah_{display_form.id}_2']['visible']

        resp.form[f'fblah_{display_form.id}_1'] = 'xxx'
        live_resp = app.post(live_url, params=resp.form.submit_fields())
        assert live_resp.json['result'][f'blah_{display_form.id}_1']['visible']
        assert live_resp.json['result'][f'blah_{display_form.id}_2']['visible']

    # check submit doesn't work
    resp = resp.form.submit('submit')
    assert 'There were errors processing your form.' in resp.text

    resp.form[f'fblah_{display_form.id}_1'] = 'xxx2'
    live_resp = app.post(live_url, params=resp.form.submit_fields())
    assert live_resp.json['result'][f'blah_{display_form.id}_1']['visible']
    assert not live_resp.json['result'][f'blah_{display_form.id}_2']['visible']

    # check submit does work when second field is hidden
    resp = resp.form.submit('submit').follow()

    assert formdef.data_class().get(formdata.id).workflow_data == {
        'blah_var_str': 'xxx2',
        'blah_var_str2': None,
    }


def test_frontoffice_workflow_form_with_dynamic_comment(pub):
    user = create_user(pub)
    wf = Workflow.get_default_workflow()
    wf.id = '2'
    wf.store()
    wf = Workflow.get(wf.id)
    status = wf.get_status('new')
    display_form = status.add_action('form', id='_display_form')
    display_form.by = ['_submitter']
    display_form.varname = 'blah'
    display_form.formdef = WorkflowFormFieldsFormDef(item=display_form)
    display_form.formdef.fields = [
        fields.StringField(id='1', label='Test', varname='str', required='required'),
        fields.CommentField(id='2', label='value is {{blah_var_str}}'),
    ]

    wf.store()
    formdef = create_formdef()
    formdef.workflow_id = wf.id
    formdef.fields = [fields.StringField(id='0', label='string', varname='plop')]
    formdef.store()

    formdef.data_class().wipe()

    formdata = formdef.data_class()()
    formdata.user_id = user.id
    formdata.just_created()
    formdata.status = 'wf-new'
    formdata.data = {'0': 'plop'}
    formdata.store()

    app = login(get_app(pub), username='foo', password='foo')
    resp = app.get(formdata.get_url(backoffice=False))
    assert f'fblah_{display_form.id}_1' in resp.form.fields

    live_url = resp.html.find('form').attrs['data-live-url']
    resp.form[f'fblah_{display_form.id}_1'] = 'test'
    live_resp = app.post(live_url, params=resp.form.submit_fields())
    assert live_resp.json['result'][f'blah_{display_form.id}_2']['visible']
    assert live_resp.json['result'][f'blah_{display_form.id}_2']['content'] == '<p>value is test</p>'


def test_frontoffice_workflow_form_with_dynamic_list(pub):
    Workflow.wipe()
    user = create_user(pub)
    wf = Workflow('dynamic list in workflow')
    status = wf.add_status('st1')
    status2 = wf.add_status('st2')
    display_form = status.add_action('form', id='_display_form')
    display_form.by = ['_submitter']
    display_form.varname = 'blah'
    display_form.hide_submit_button = False
    display_form.formdef = WorkflowFormFieldsFormDef(item=display_form)
    display_form.formdef.fields = [
        fields.ItemField(id='1', label='Test', varname='foo', items=['10', '20']),
        fields.ItemField(
            id='2',
            label='Test2',
            varname='item2',
            data_source={'type': 'json', 'value': 'http://example.org/{{form_workflow_form_blah_var_foo}}'},
        ),
    ]
    jump1 = status.add_action('choice', id='_jump')
    jump1.label = 'Jump'
    jump1.by = ['_submitter']
    jump1.status = status2.id

    wf.store()

    formdef = create_formdef()
    formdef.workflow_id = wf.id
    formdef.store()

    formdef.data_class().wipe()

    formdata = formdef.data_class()()
    formdata.user_id = user.id
    formdata.just_created()
    formdata.store()

    app = login(get_app(pub), username='foo', password='foo')
    resp = app.get(formdata.get_url(backoffice=False))

    with responses.RequestsMock() as rsps:
        rsps.get(
            'http://example.org/10',
            json={
                'data': [
                    {'id': '1', 'text': 'hello', 'extra': 'foo'},
                    {'id': '2', 'text': 'world', 'extra': 'bar'},
                ]
            },
        )
        rsps.get(
            'http://example.org/20',
            json={
                'data': [
                    {'id': '11', 'text': 'hello2', 'extra': 'foo'},
                    {'id': '21', 'text': 'world2', 'extra': 'bar'},
                ]
            },
        )

        live_url = resp.html.find('form').attrs['data-live-url']
        live_resp = app.post(live_url, params=resp.form.submit_fields() + [('modified_field_id[]', 'init')])
        assert [x['id'] for x in live_resp.json['result'][f'blah_{display_form.id}_2']['items']] == ['1', '2']
        resp.form[f'fblah_{display_form.id}_1'] = '20'
        live_resp = app.post(
            live_url,
            params=resp.form.submit_fields() + [('modified_field_id[]', f'blah_{display_form.id}_1')],
        )
        assert [x['id'] for x in live_resp.json['result'][f'blah_{display_form.id}_2']['items']] == [
            '11',
            '21',
        ]

        resp.form[f'fblah_{display_form.id}_2'].force_value('11')
        resp = resp.form.submit('submit').follow()
        assert 'Technical error, please try again' not in resp.text
        formdata.refresh_from_storage()
        pub.substitutions.feed(formdata)
        context = pub.substitutions.get_context_variables(mode='lazy')
        assert context['form_workflow_form_blah_var_item2'] == 'hello2'
        assert context['form_workflow_data_blah_var_item2'] == 'hello2'


@pytest.mark.parametrize('button_position', ['before', 'after'])
def test_frontoffice_workflow_form_and_other_button(pub, button_position):
    user = create_user(pub)
    wf = Workflow('form and other button')
    status = wf.add_status('st1')
    status2 = wf.add_status('st2')
    display_form = status.add_action('form', id='_display_form')
    display_form.by = ['_submitter']
    display_form.varname = 'blah'
    display_form.formdef = WorkflowFormFieldsFormDef(item=display_form)
    display_form.hide_submit_button = False
    display_form.formdef.fields = [
        fields.StringField(id='1', label='Test', varname='foo', required='required'),
        fields.StringField(id='2', label='Test2', varname='foo2', required='required'),
    ]
    jump1 = status.add_action('choice', id='_jump', prepend=bool(button_position == 'before'))
    jump1.label = 'Jump'
    jump1.by = ['_submitter']
    jump1.status = status2.id

    wf.store()

    formdef = create_formdef()
    formdef.workflow_id = wf.id
    formdef.store()

    formdef.data_class().wipe()

    app = login(get_app(pub), username='foo', password='foo')

    formdata = formdef.data_class()()
    formdata.user_id = user.id
    formdata.just_created()
    formdata.store()
    resp = app.get(formdata.get_url(backoffice=False))
    resp.form[f'fblah_{display_form.id}_1'] = 'a'
    resp.form[f'fblah_{display_form.id}_2'] = 'b'
    resp = resp.form.submit('submit')
    formdata.refresh_from_storage()
    pub.substitutions.reset()
    pub.substitutions.feed(formdata)
    context = pub.substitutions.get_context_variables(mode='lazy')
    assert 'form_workflow_form_blah_var_foo' in context
    assert context['form_workflow_form_blah_var_foo'] == 'a'
    assert context['form_workflow_data_blah_var_foo'] == 'a'

    formdata = formdef.data_class()()
    formdata.user_id = user.id
    formdata.just_created()
    formdata.store()
    resp = app.get(formdata.get_url(backoffice=False))
    resp.form[f'fblah_{display_form.id}_1'] = 'a'
    resp.form[f'fblah_{display_form.id}_2'] = 'b'
    resp = resp.form.submit('button_jump')
    formdata.refresh_from_storage()
    pub.substitutions.reset()
    pub.substitutions.feed(formdata)
    context = pub.substitutions.get_context_variables(mode='lazy')
    # check workflow form data is saved if button comes after form action
    if button_position == 'before':
        assert 'form_workflow_form_blah_var_foo' not in context
    else:
        assert 'form_workflow_form_blah_var_foo' in context
    # but legacy behaviout it leaks into workflow_data :/
    assert context['form_workflow_data_blah_var_foo'] == 'a'

    # check it also happens with invalid/partial form
    jump1.ignore_form_errors = True
    wf.store()
    formdata = formdef.data_class()()
    formdata.user_id = user.id
    formdata.just_created()
    formdata.store()
    resp = app.get(formdata.get_url(backoffice=False))
    resp.form[f'fblah_{display_form.id}_1'] = 'a'
    resp.form[f'fblah_{display_form.id}_2'] = ''
    resp = resp.form.submit('button_jump')
    formdata.refresh_from_storage()
    pub.substitutions.reset()
    pub.substitutions.feed(formdata)
    context = pub.substitutions.get_context_variables(mode='lazy')
    # check workflow form data is not saved (good)
    assert 'form_workflow_form_blah_var_foo' not in context
    # but legacy behaviout it leaks into workflow_data :/
    assert context['form_workflow_data_blah_var_foo'] == 'a'


def test_frontoffice_workflow_form_with_impossible_condition(pub):
    user = create_user(pub)
    wf = Workflow.get_default_workflow()
    wf.id = '2'
    wf.store()
    wf = Workflow.get(wf.id)
    status = wf.get_status('new')
    display_form = status.add_action('form', id='_display_form')
    display_form.by = ['_submitter']
    display_form.varname = 'blah'
    display_form.formdef = WorkflowFormFieldsFormDef(item=display_form)
    display_form.formdef.fields = [
        fields.StringField(
            id='1',
            label='Test',
            varname='str',
            condition={'type': 'django', 'value': '0 == 1'},
        ),
        fields.StringField(
            id='2',
            label='Test2',
            condition={'type': 'django', 'value': 'blah_var_str == "toto"'},
        ),
    ]

    wf.store()
    formdef = create_formdef()
    formdef.workflow_id = wf.id
    formdef.fields = []
    formdef.store()

    formdef.data_class().wipe()

    formdata = formdef.data_class()()
    formdata.user_id = user.id
    formdata.just_created()
    formdata.status = 'wf-new'
    formdata.store()

    app = login(get_app(pub), username='foo', password='foo')
    resp = app.get(formdata.get_url(backoffice=False))
    assert 'fblah_1' not in resp.form.fields
    assert 'fblah_2' not in resp.form.fields


def test_frontoffice_workflow_form_jump_on_submit(pub):
    user = create_user(pub)
    wf = Workflow(name='select')
    st1 = wf.add_status('st1')
    st2 = wf.add_status('st2')
    display_form = st1.add_action('form')
    display_form.by = ['_submitter']
    display_form.varname = 'blah'
    display_form.hide_submit_button = False
    display_form.formdef = WorkflowFormFieldsFormDef(item=display_form)
    display_form.formdef.fields = [
        fields.StringField(id='1', label='Test', varname='foo'),
    ]
    jump = st1.add_action('jumponsubmit')
    jump.status = st2.id

    wf.store()

    formdef = create_formdef()
    formdef.workflow_id = wf.id
    formdef.fields = []
    formdef.store()

    formdef.data_class().wipe()

    formdata = formdef.data_class()()
    formdata.user_id = user.id
    formdata.data = {}
    formdata.just_created()
    formdata.store()

    app = login(get_app(pub), username='foo', password='foo')
    resp = app.get(formdata.get_url(backoffice=False))
    resp.form[f'fblah_{display_form.id}_1'] = 'hello'
    resp = resp.form.submit('submit').follow()
    formdata.refresh_from_storage()
    assert formdata.status == 'wf-%s' % st2.id
    substvars = CompatibilityNamesDict()
    substvars.update(formdata.get_substitution_variables())
    assert str(substvars['form_workflow_form_blah_var_foo']) == 'hello'


def test_frontoffice_workflow_form_jump_on_submit_with_condition(pub):
    user = create_user(pub)
    wf = Workflow(name='select')
    st1 = wf.add_status('st1')
    st2 = wf.add_status('st2')
    display_form = st1.add_action('form')
    display_form.by = ['_submitter']
    display_form.varname = 'blah'
    display_form.hide_submit_button = False
    display_form.formdef = WorkflowFormFieldsFormDef(item=display_form)
    display_form.formdef.fields = [
        fields.StringField(id='1', label='Test', varname='foo'),
    ]
    jump = st1.add_action('jumponsubmit')
    jump.condition = {'type': 'django', 'value': 'form_workflow_form_blah_var_foo == "test"'}
    jump.status = st2.id

    wf.store()

    formdef = create_formdef()
    formdef.workflow_id = wf.id
    formdef.fields = []
    formdef.store()

    formdef.data_class().wipe()

    formdata = formdef.data_class()()
    formdata.user_id = user.id
    formdata.data = {}
    formdata.just_created()
    formdata.store()

    # valid condition
    app = login(get_app(pub), username='foo', password='foo')
    resp = app.get(formdata.get_url(backoffice=False))
    resp.form[f'fblah_{display_form.id}_1'] = 'test'
    resp = resp.form.submit('submit').follow()
    formdata.refresh_from_storage()
    assert formdata.status == 'wf-%s' % st2.id
    substvars = CompatibilityNamesDict()
    substvars.update(formdata.get_substitution_variables())
    assert str(substvars['form_workflow_form_blah_var_foo']) == 'test'

    # invalid condition
    formdata = formdef.data_class()()
    formdata.user_id = user.id
    formdata.data = {}
    formdata.just_created()
    formdata.store()

    app = login(get_app(pub), username='foo', password='foo')
    resp = app.get(formdata.get_url(backoffice=False))
    resp.form[f'fblah_{display_form.id}_1'] = 'nope'
    resp = resp.form.submit('submit').follow()
    formdata.refresh_from_storage()
    assert formdata.status == 'wf-%s' % st1.id


@responses.activate
def test_frontoffice_workflow_form_with_disappearing_option(pub, monkeypatch):
    NamedDataSource.wipe()
    data_source = NamedDataSource(name='foobar')
    data_source.data_source = {'type': 'json', 'value': 'http://www.example.net/plop'}
    data_source.id_parameter = 'id'
    data_source.store()

    responses.get(
        'http://www.example.net/plop', json={'data': [{'id': '1', 'text': 'un'}, {'id': '2', 'text': 'deux'}]}
    )

    user = create_user(pub)
    wf = Workflow(name='select')
    st1 = wf.add_status('st1')
    st2 = wf.add_status('st2')
    display_form = st1.add_action('form')
    display_form.by = ['_submitter']
    display_form.varname = 'blah'
    display_form.hide_submit_button = False
    display_form.formdef = WorkflowFormFieldsFormDef(item=display_form)
    display_form.formdef.fields = [
        fields.ItemField(id='1', label='Test', varname='foo', data_source={'type': 'foobar'}),
    ]
    jump = st1.add_action('jumponsubmit')
    jump.status = st2.id

    wf.store()

    formdef = create_formdef()
    formdef.workflow_id = wf.id
    formdef.fields = []
    formdef.store()

    formdef.data_class().wipe()

    formdata = formdef.data_class()()
    formdata.user_id = user.id
    formdata.data = {}
    formdata.just_created()
    formdata.store()

    # normal case, status changes and data is recorded
    app = login(get_app(pub), username='foo', password='foo')
    resp = app.get(formdata.get_url(backoffice=False))
    resp.form[f'fblah_{display_form.id}_1'] = '1'
    resp = resp.form.submit('submit').follow()
    formdata.refresh_from_storage()
    assert formdata.status == 'wf-%s' % st2.id
    assert formdata.workflow_data['blah_var_foo_raw'] == '1'
    assert formdata.workflow_data['blah_var_foo'] == 'un'

    # simulate an option disappearing during submit
    monkeypatch.setattr(
        NamedDataSource,
        'get_value_by_id',
        lambda *args: None,
    )
    formdata = formdef.data_class()()
    formdata.user_id = user.id
    formdata.data = {}
    formdata.just_created()
    formdata.store()

    app = login(get_app(pub), username='foo', password='foo')
    resp = app.get(formdata.get_url(backoffice=False))
    resp.form[f'fblah_{display_form.id}_1'] = '1'

    resp = resp.form.submit('submit')
    assert resp.pyquery('.global-errors summary').text() == 'Technical error, please try again.'
    assert (
        resp.pyquery('.global-errors p').text()
        == f"no matching value in datasource (field id: blah_{display_form.id}_1, value: '1')"
    )
    formdata.refresh_from_storage()
    assert formdata.status == 'wf-%s' % st1.id
    assert not formdata.workflow_data


def test_workflow_form_structured_data(pub):
    FormDef.wipe()
    Workflow.wipe()
    BlockDef.wipe()

    user = create_user(pub)

    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.StringField(id='123', required='required', label='Test', varname='test'),
    ]
    block.store()

    wf = Workflow(name='test')
    status = wf.add_status('New', 'st1')
    display_form = status.add_action('form', id='_display_form')
    display_form.by = ['_submitter']
    display_form.varname = 'blah'
    display_form.hide_submit_button = False
    display_form.formdef = WorkflowFormFieldsFormDef(item=display_form)
    display_form.formdef.fields = [
        fields.BlockField(id='1', label='test', block_slug='foobar', varname='fooblock'),
    ]
    wf.store()

    formdef = create_formdef()
    formdef.workflow_id = wf.id
    formdef.fields = []
    formdef.store()

    formdef.data_class().wipe()

    formdata = formdef.data_class()()
    formdata.user_id = user.id
    formdata.just_created()
    formdata.store()

    app = login(get_app(pub), username='foo', password='foo')
    resp = app.get(formdata.get_url(backoffice=False))
    resp.form[f'fblah_{display_form.id}_1$element0$f123'] = 'ABC'
    resp = resp.form.submit('submit').follow()

    resp.form[f'fblah_{display_form.id}_1$element0$f123'] = 'XYZ'
    resp = resp.form.submit('submit').follow()

    formdata.refresh_from_storage()
    assert formdata.workflow_data == {
        'blah_var_fooblock_raw': {'data': [{'123': 'XYZ'}], 'schema': {'123': 'string'}},
        'blah_var_fooblock': 'foobar',
    }

    substvars = CompatibilityNamesDict()
    substvars.update(formdata.get_substitution_variables())
    keys = substvars.get_flat_keys()
    for key in keys:
        # noqa pylint: disable=unused-variable
        var = substvars[key]  # check it doesn't raise, ignore the value

    assert substvars['form_workflow_form_blah_var_fooblock_var_test'] == 'XYZ'
    assert substvars['form_workflow_form_blah_0_var_fooblock_var_test'] == 'ABC'
    assert substvars['form_workflow_form_blah_1_var_fooblock_var_test'] == 'XYZ'

    # disable dumping in workflow_data
    pub.load_site_options()
    if not pub.site_options.has_section('options'):
        pub.site_options.add_section('options')
    pub.site_options.set('options', 'disable-workflow-form-to-workflow-data', 'true')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)

    formdata2 = formdef.data_class()()
    formdata2.user_id = user.id
    formdata2.just_created()
    formdata2.store()

    app = login(get_app(pub), username='foo', password='foo')
    resp = app.get(formdata2.get_url(backoffice=False))
    resp.form[f'fblah_{display_form.id}_1$element0$f123'] = 'ABC'
    resp = resp.form.submit('submit').follow()

    resp.form[f'fblah_{display_form.id}_1$element0$f123'] = 'XYZ'
    resp = resp.form.submit('submit').follow()

    formdata2.refresh_from_storage()
    assert not formdata2.workflow_data

    # check behaviour when block is deleted
    block.remove_self()

    formdata.refresh_from_storage()
    substvars = CompatibilityNamesDict()
    substvars.update(formdata.get_substitution_variables())
    keys = substvars.get_flat_keys()
    for key in keys:
        # noqa pylint: disable=unused-variable
        var = substvars[key]  # check it doesn't raise, ignore the value


def test_workflow_form_file_access(pub):
    FormDef.wipe()
    Workflow.wipe()
    BlockDef.wipe()

    user = create_user(pub)

    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.FileField(id='123', required='required', label='Test', varname='test'),
    ]
    block.store()

    wf = Workflow(name='test')
    status = wf.add_status('New', 'st1')

    status.items = []
    display_form = status.add_action('form', id='_display_form')
    display_form.by = ['_submitter']
    display_form.varname = 'blah'
    display_form.hide_submit_button = False
    display_form.formdef = WorkflowFormFieldsFormDef(item=display_form)
    display_form.formdef.fields = [
        fields.BlockField(id='1', label='test', block_slug='foobar', varname='fooblock', max_items='3'),
        fields.FileField(id='2', label='test2', varname='file'),
    ]

    jump = status.add_action('jumponsubmit', id='_jump')
    jump.status = status.id

    display_message = status.add_action('displaymsg', id='_display')
    display_message.message = '''<p>
        <a href="{{ form_workflow_form_blah_var_fooblock_0_test_url }}" id="t1">1st file in block for last form</a>
        <a href="{{ form_workflow_form_blah_var_fooblock_1_test_url }}" id="t2">2nd file in block for last form</a>
        <a href="{{ form_workflow_form_blah_var_file_url }}" id="t3">file field for last form</a>

        <a href="{{ form_workflow_form_blah_0_var_fooblock_0_test_url }}" id="t4">again 1st file in block for 1st form</a>
        <a href="{{ form_workflow_form_blah_0_var_fooblock_1_test_url }}" id="t5">again 1st file in block for 1st form</a>
        <a href="{{ form_workflow_form_blah_0_var_file_url }}" id="t6">file field for 1st form</a>

        <a href="{{ form_workflow_form_blah_1_var_fooblock_0_test_url }}" id="t7">again 1st file in block for 2nd form</a>
        <a href="{{ form_workflow_form_blah_1_var_fooblock_1_test_url }}" id="t8">again 1st file in block for 2nd form</a>
        <a href="{{ form_workflow_form_blah_1_var_file_url }}" id="t9">file field< for 2nd form/a>
    </p>'''
    display_message.to = []

    wf.store()

    formdef = create_formdef()
    formdef.workflow_id = wf.id
    formdef.fields = []
    formdef.store()

    formdef.data_class().wipe()

    formdata = formdef.data_class()()
    formdata.user_id = user.id
    formdata.just_created()
    formdata.store()

    app = login(get_app(pub), username='foo', password='foo')
    resp = app.get(formdata.get_url(backoffice=False))
    resp.form[f'fblah_{display_form.id}_1$element0$f123$file'] = Upload('test1.txt', b'foobar1', 'text/plain')
    resp = resp.form.submit(f'fblah_{display_form.id}_1$add_element')
    resp.form[f'fblah_{display_form.id}_1$element1$f123$file'] = Upload('test2.txt', b'foobar2', 'text/plain')
    resp.form[f'fblah_{display_form.id}_2$file'] = Upload('test3.txt', b'foobar3', 'text/plain')
    resp = resp.form.submit('submit').follow()

    assert app.get(resp.pyquery('#t1').attr.href).body == b'foobar1'
    assert app.get(resp.pyquery('#t2').attr.href).body == b'foobar2'
    assert app.get(resp.pyquery('#t3').attr.href).body == b'foobar3'
    assert app.get(resp.pyquery('#t4').attr.href).body == b'foobar1'
    assert app.get(resp.pyquery('#t5').attr.href).body == b'foobar2'
    assert app.get(resp.pyquery('#t6').attr.href).body == b'foobar3'

    resp.form[f'fblah_{display_form.id}_1$element0$f123$file'] = Upload('test4.txt', b'foobar4', 'text/plain')
    resp = resp.form.submit(f'fblah_{display_form.id}_1$add_element')
    resp.form[f'fblah_{display_form.id}_1$element1$f123$file'] = Upload('test5.txt', b'foobar5', 'text/plain')
    resp.form[f'fblah_{display_form.id}_2$file'] = Upload('test6.txt', b'foobar6', 'text/plain')
    resp = resp.form.submit('submit').follow()

    # afer second submit of workflow form, general variable
    # form_workflow_form_blah_var contains the last submitted form values

    assert app.get(resp.pyquery('#t1').attr.href).body == b'foobar4'
    assert app.get(resp.pyquery('#t2').attr.href).body == b'foobar5'
    assert app.get(resp.pyquery('#t3').attr.href).body == b'foobar6'
    assert app.get(resp.pyquery('#t4').attr.href).body == b'foobar1'
    assert app.get(resp.pyquery('#t5').attr.href).body == b'foobar2'
    assert app.get(resp.pyquery('#t6').attr.href).body == b'foobar3'
    assert app.get(resp.pyquery('#t7').attr.href).body == b'foobar4'
    assert app.get(resp.pyquery('#t8').attr.href).body == b'foobar5'
    assert app.get(resp.pyquery('#t9').attr.href).body == b'foobar6'
    app.get(resp.pyquery('#t4').attr.href + 'X', status=404)  # wrong URL, unknown file

    # unlogged user
    assert '/login' in get_app(pub).get(resp.pyquery('#t1').attr.href).location

    # other user
    user = pub.user_class()
    user.name = 'Second user'
    user.store()
    account = PasswordAccount(id='foo2')
    account.set_password('foo2')
    account.user_id = user.id
    account.store()
    login(get_app(pub), username='foo2', password='foo2').get(resp.pyquery('#t1').attr.href, status=403)


def test_workflow_form_line_details(pub):
    workflow = Workflow(name='choice')
    st1 = workflow.add_status('Status1', 'st1')
    display_form = st1.add_action('form')

    assert display_form.get_line_details() == 'not completed'

    role = pub.role_class(name='foorole')
    role.store()
    display_form.by = [role.id]
    assert display_form.get_line_details() == 'not completed'

    display_form.formdef = WorkflowFormFieldsFormDef(item=display_form)
    display_form.formdef.fields = [
        fields.BlockField(id='1', label='test', block_slug='foobar', varname='fooblock'),
    ]
    assert display_form.get_line_details() == 'by foorole'


def test_workflow_form_block_condition(pub):
    FormDef.wipe()
    Workflow.wipe()
    BlockDef.wipe()

    user = create_user(pub)

    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.StringField(id='123', required='required', label='One', varname='one'),
        fields.StringField(
            id='234',
            required='required',
            label='Two',
            condition={'type': 'django', 'value': 'block_var_one|startswith:"test"'},
        ),
    ]
    block.store()

    wf = Workflow(name='test')
    status = wf.add_status('New', 'st1')

    status.items = []
    display_form = status.add_action('form', id='_display_form')
    display_form.by = ['_submitter']
    display_form.varname = 'blah'
    display_form.hide_submit_button = False
    display_form.formdef = WorkflowFormFieldsFormDef(item=display_form)
    display_form.formdef.fields = [
        fields.BlockField(id='1', label='test', block_slug='foobar', varname='fooblock', max_items='3'),
    ]

    jump = status.add_action('jumponsubmit', id='_jump')
    jump.status = status.id

    wf.store()

    formdef = create_formdef()
    formdef.workflow_id = wf.id
    formdef.fields = []
    formdef.store()

    formdef.data_class().wipe()

    formdata = formdef.data_class()()
    formdata.user_id = user.id
    formdata.just_created()
    formdata.store()

    app = login(get_app(pub), username='foo', password='foo')
    resp = app.get(formdata.get_url(backoffice=False))
    assert (
        resp.pyquery(f'[data-widget-name="fblah_{display_form.id}_1$element0$f234"]').attr.style
        == 'display: none'
    )
    live_url = resp.html.find('form').attrs['data-live-url']
    live_resp = app.post(live_url, params=resp.form.submit_fields() + [('modified_field_id[]', '123')])
    assert live_resp.json['result'][f'blah_{display_form.id}_1-234-0']['visible'] is False
    resp.form[f'fblah_{display_form.id}_1$element0$f123'] = 'test'
    live_resp = app.post(live_url, params=resp.form.submit_fields() + [('modified_field_id[]', '123')])
    assert live_resp.json['result'][f'blah_{display_form.id}_1-234-0']['visible'] is True

    resp = resp.form.submit(f'fblah_{display_form.id}_1$add_element')
    resp = resp.form.submit(f'fblah_{display_form.id}_1$add_element')
    live_resp = app.post(live_url, params=resp.form.submit_fields() + [('modified_field_id[]', '123')])
    assert live_resp.json['result'][f'blah_{display_form.id}_1-234-0']['visible'] is True
    assert live_resp.json['result'][f'blah_{display_form.id}_1-234-1']['visible'] is False
    assert live_resp.json['result'][f'blah_{display_form.id}_1-234-2']['visible'] is False

    resp.form[f'fblah_{display_form.id}_1$element2$f123'] = 'test3'
    live_resp = app.post(live_url, params=resp.form.submit_fields() + [('modified_field_id[]', '123')])
    assert live_resp.json['result'][f'blah_{display_form.id}_1-234-0']['visible'] is True
    assert live_resp.json['result'][f'blah_{display_form.id}_1-234-1']['visible'] is False
    assert live_resp.json['result'][f'blah_{display_form.id}_1-234-2']['visible'] is True


def test_workflow_form_file_clamd(pub):
    pub.load_site_options()
    if not pub.site_options.has_section('options'):
        pub.site_options.add_section('options')
    pub.site_options.set('options', 'enable-clamd', 'true')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)
    pub.load_site_options()

    FormDef.wipe()
    Workflow.wipe()
    BlockDef.wipe()

    user = create_user(pub)

    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.FileField(id='123', required='required', label='Test', varname='test'),
    ]
    block.store()

    wf = Workflow(name='test')
    status = wf.add_status('New', 'st1')

    status.items = []
    display_form = status.add_action('form', id='_display_form')
    display_form.by = ['_submitter']
    display_form.varname = 'blah'
    display_form.hide_submit_button = False
    display_form.formdef = WorkflowFormFieldsFormDef(item=display_form)
    display_form.formdef.fields = [
        fields.BlockField(id='1', label='test', block_slug='foobar', varname='fooblock', max_items='3'),
        fields.FileField(id='2', label='test2', varname='file'),
    ]

    jump = status.add_action('jumponsubmit', id='_jump')
    jump.status = status.id

    wf.store()

    formdef = create_formdef()
    formdef.workflow_id = wf.id
    formdef.fields = []
    formdef.store()

    formdef.data_class().wipe()

    formdata = formdef.data_class()()
    formdata.user_id = user.id
    formdata.just_created()
    formdata.store()

    app = login(get_app(pub), username='foo', password='foo')
    resp = app.get(formdata.get_url(backoffice=False))

    resp.form[f'fblah_{display_form.id}_1$element0$f123$file'] = Upload('test1.txt', b'foobar1', 'text/plain')
    resp = resp.form.submit(f'fblah_{display_form.id}_1$add_element')
    resp.form[f'fblah_{display_form.id}_1$element1$f123$file'] = Upload('test2.txt', b'foobar2', 'text/plain')
    resp.form[f'fblah_{display_form.id}_2$file'] = Upload('test3.txt', b'foobar3', 'text/plain')

    with mock.patch('wcs.clamd.subprocess') as subp:
        attrs = {'run.return_value': mock.Mock(returncode=0, stdout='stdout')}
        subp.configure_mock(**attrs)
        resp = resp.form.submit('submit').follow()
        assert subp.run.call_count == 6  # 3 files but each file is stored in a part and in workflow_data
        formdata = formdef.data_class().select()[0]
        for file_data in formdata.get_all_file_data(with_history=False):
            assert file_data.has_been_scanned()
            assert file_data.clamd['returncode'] == 0
            subp.run.assert_any_call(
                ['clamdscan', '--fdpass', file_data.get_fs_filename()],
                check=False,
                capture_output=True,
                text=True,
            )


def test_workflow_form_post_condition(pub):
    user = create_user(pub)
    Workflow.wipe()
    wf = Workflow('form')
    status = wf.add_status('st1')
    status2 = wf.add_status('st2')
    display_form = status.add_action('form', id='_display_form')
    display_form.by = ['_submitter']
    display_form.varname = 'blah'
    display_form.formdef = WorkflowFormFieldsFormDef(item=display_form)
    display_form.hide_submit_button = True
    display_form.formdef.fields = [
        fields.StringField(id='1', label='Test', varname='foo', required='optional'),
    ]
    display_form.post_conditions = [
        {
            'condition': {'type': 'django', 'value': 'form_workflow_form_blah_var_foo == "a"'},
            'error_message': 'You shall not pass.',
        }
    ]
    jump = status.add_action('choice')
    jump.label = 'Jump'
    jump.by = ['_submitter']
    jump.status = status2.id

    jump2 = status.add_action('choice')
    jump2.label = 'Jump2'
    jump2.by = ['_submitter']
    jump2.status = status2.id
    jump2.ignore_form_errors = True

    wf.store()

    formdef = create_formdef()
    formdef.fields = [fields.StringField(id='0', label='string', varname='plop')]
    formdef.workflow_id = wf.id
    formdef.store()

    formdef.data_class().wipe()

    app = login(get_app(pub), username='foo', password='foo')

    # condition ok
    formdata = formdef.data_class()()
    formdata.user_id = user.id
    formdata.just_created()
    formdata.store()
    resp = app.get(formdata.get_url(backoffice=False))
    resp.form[f'fblah_{display_form.id}_1'] = 'a'
    resp = resp.form.submit(f'button{jump.id}')
    assert formdata.get_status() == status
    formdata.refresh_from_storage()
    traces = WorkflowTrace.select_for_formdata(formdata)
    assert len(traces) == 2
    assert traces[0].action_item_key == 'form'
    assert traces[0].action_item_id == '_display_form'
    assert traces[1].event == 'button'
    pub.substitutions.reset()
    pub.substitutions.feed(formdata)
    context = pub.substitutions.get_context_variables(mode='lazy')
    assert 'form_workflow_form_blah_var_foo' in context
    assert context['form_workflow_form_blah_var_foo'] == 'a'

    # condition not ok
    formdata = formdef.data_class()()
    formdata.user_id = user.id
    formdata.just_created()
    formdata.store()
    resp = app.get(formdata.get_url(backoffice=False))
    resp.form[f'fblah_{display_form.id}_1'] = 'c'
    resp = resp.form.submit(f'button{jump.id}')
    assert resp.pyquery('form .global-errors').text() == 'You shall not pass.'
    formdata.refresh_from_storage()
    assert len(WorkflowTrace.select_for_formdata(formdata)) == 0
    pub.substitutions.reset()
    pub.substitutions.feed(formdata)
    context = pub.substitutions.get_context_variables(mode='lazy')
    assert 'form_workflow_form_blah_var_foo' not in context

    # condition not ok but click on button ignoring form
    formdata = formdef.data_class()()
    formdata.user_id = user.id
    formdata.just_created()
    formdata.store()
    resp = app.get(formdata.get_url(backoffice=False))
    resp = resp.form.submit(f'button{jump2.id}')
    formdata.refresh_from_storage()
    assert formdata.get_status() == status2

    # condition referencing form data (ko)
    display_form.post_conditions[0]['condition'][
        'value'
    ] = 'form_var_plop == "xxx" and form_workflow_form_blah_var_foo == "a"'
    wf.store()
    formdata = formdef.data_class()()
    formdata.data = {'0': 'plop'}
    formdata.user_id = user.id
    formdata.just_created()
    formdata.store()
    resp = app.get(formdata.get_url(backoffice=False))
    resp.form[f'fblah_{display_form.id}_1'] = 'a'
    resp = resp.form.submit(f'button{jump.id}')
    assert resp.pyquery('form .global-errors').text() == 'You shall not pass.'
    formdata.refresh_from_storage()
    pub.substitutions.reset()
    pub.substitutions.feed(formdata)
    context = pub.substitutions.get_context_variables(mode='lazy')
    assert 'form_workflow_form_blah_var_foo' not in context

    # condition referencing form data (ok)
    formdata = formdef.data_class()()
    formdata.data = {'0': 'xxx'}
    formdata.user_id = user.id
    formdata.just_created()
    formdata.store()
    resp = app.get(formdata.get_url(backoffice=False))
    resp.form[f'fblah_{display_form.id}_1'] = 'a'
    resp = resp.form.submit(f'button{jump.id}').follow()
    formdata.refresh_from_storage()
    pub.substitutions.reset()
    pub.substitutions.feed(formdata)
    context = pub.substitutions.get_context_variables(mode='lazy')
    assert 'form_workflow_form_blah_var_foo' in context

    # condition with an error (ko)
    display_form.post_conditions[0]['condition']['value'] = 'a = b'  # invalid django
    wf.store()
    formdata = formdef.data_class()()
    formdata.data = {'0': 'plop'}
    formdata.user_id = user.id
    formdata.just_created()
    formdata.store()
    resp = app.get(formdata.get_url(backoffice=False))
    resp.form[f'fblah_{display_form.id}_1'] = 'a'
    resp = resp.form.submit(f'button{jump.id}')
    assert resp.pyquery('form .global-errors').text() == 'You shall not pass.'
    formdata.refresh_from_storage()
    pub.substitutions.reset()
    pub.substitutions.feed(formdata)
    context = pub.substitutions.get_context_variables(mode='lazy')
    assert 'form_workflow_form_blah_var_foo' not in context

    # condition with a previous form with valid value
    formdata = formdef.data_class()()
    formdata.user_id = user.id
    formdata.just_created()
    formdata.store()
    resp = app.get(formdata.get_url(backoffice=False))
    resp.form[f'fblah_{display_form.id}_1'] = 'a'
    resp = resp.form.submit(f'button{jump.id}')
    formdata.refresh_from_storage()

    formdata.status = f'wf-{status.id}'  # get back to first status
    formdata.store()
    pub.substitutions.reset()
    pub.substitutions.feed(formdata)

    resp = app.get(formdata.get_url(backoffice=False))
    resp.form[f'fblah_{display_form.id}_1'] = ''  # left empty (previous value shouldn't be used in test)
    resp = resp.form.submit(f'button{jump.id}')
    assert resp.pyquery('form .global-errors').text() == 'You shall not pass.'


def test_workflow_form_traces(pub):
    BlockDef.wipe()
    WorkflowTrace.wipe()
    user = create_user(pub)
    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.StringField(id='123', label='blockfield'),
    ]
    block.store()
    wf = Workflow(name='xxx')
    status = wf.add_status('st1')

    # test with submit button
    display_form = status.add_action('form', id='_display_form')
    display_form.hide_submit_button = False
    display_form.by = ['_submitter']
    display_form.varname = 'blah'
    display_form.formdef = WorkflowFormFieldsFormDef(item=display_form)
    display_form.formdef.fields = [
        fields.StringField(id='1', label='Test', varname='str', required='required'),
        fields.BlockField(id='2', label='testblock', block_slug='foobar', required='optional', max_items='3'),
    ]

    wf.store()
    formdef = create_formdef()
    formdef.workflow_id = wf.id
    formdef.store()

    formdef.data_class().wipe()
    formdata = formdef.data_class()()
    formdata.user_id = user.id
    formdata.just_created()
    formdata.store()

    app = login(get_app(pub), username='foo', password='foo')
    resp = app.get(formdata.get_url(backoffice=False))
    # form displayed, no trace
    assert f'fblah_{display_form.id}_1' in resp.form.fields
    assert len(WorkflowTrace.select_for_formdata(formdata)) == 0

    # add block element, no trace
    assert resp.text.count('>blockfield<') == 1
    assert resp.html.find('div', {'class': 'list-add'})
    resp = resp.form.submit('fblah__display_form_2$add_element')
    assert resp.text.count('>blockfield<') == 2
    assert len(WorkflowTrace.select_for_formdata(formdata)) == 0

    # form submitted but in error, no trace
    resp = resp.form.submit('submit')
    assert 'There were errors processing your form' in resp.text
    assert len(WorkflowTrace.select_for_formdata(formdata)) == 0

    # form successfully submitted
    resp.form[f'fblah_{display_form.id}_1'] = 'aaa'
    resp = resp.form.submit('submit').follow()
    formdata.refresh_from_storage()
    traces = WorkflowTrace.select_for_formdata(formdata)
    assert len(traces) == 2
    assert traces[0].action_item_key == 'form'
    assert traces[0].action_item_id == '_display_form'
    assert traces[1].event == 'button'

    # test with jump
    display_form.hide_submit_button = True
    wf.add_status('st2')
    jump = status.add_action('choice')
    jump.label = 'Jump'
    jump.status = 'st2'
    jump.by = ['_submitter']
    wf.store()

    formdata = formdef.data_class().wipe()
    WorkflowTrace.wipe()
    formdata = formdef.data_class()()
    formdata.user_id = user.id
    formdata.just_created()
    formdata.store()
    app = login(get_app(pub), username='foo', password='foo')
    resp = app.get(formdata.get_url(backoffice=False))
    resp.form[f'fblah_{display_form.id}_1'] = 'aaa'
    resp = resp.forms['wf-actions'].submit(f'button{jump.id}')
    formdata.refresh_from_storage()
    assert formdata.status == 'wf-1'
    traces = WorkflowTrace.select_for_formdata(formdata)
    assert len(traces) == 2
    assert traces[0].action_item_key == 'form'
    assert traces[0].action_item_id == '_display_form'
    assert traces[1].event == 'button'


def test_workflow_form_include_in_form_history_submit_no_jump(pub):
    FormDef.wipe()
    Workflow.wipe()
    BlockDef.wipe()

    user = create_user(pub)

    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.StringField(id='123', required=True, label='Test', varname='test'),
    ]
    block.store()

    wf = Workflow(name='test')
    status = wf.add_status('New', 'st1')
    display_form = status.add_action('form', id='_display_form')
    display_form.by = ['_submitter']
    display_form.varname = 'blah'
    display_form.hide_submit_button = False
    display_form.formdef = WorkflowFormFieldsFormDef(item=display_form)
    display_form.formdef.fields = [
        fields.StringField(id='1', label='Test', varname='str', required=True),
    ]
    wf.store()

    formdef = create_formdef()
    formdef.workflow_id = wf.id
    formdef.fields = []

    formdef.store()
    formdef.data_class().wipe()

    formdata = formdef.data_class()()
    formdata.user_id = user.id
    formdata.just_created()
    formdata.store()

    app = login(get_app(pub), username='foo', password='foo')
    resp = app.get(formdata.get_url(backoffice=False))
    resp.form[f'fblah_{display_form.id}_1'] = 'ABC'
    resp = resp.form.submit('submit').follow()
    assert resp.pyquery('ul#evolutions li.msg-in').length == 1

    display_form.include_in_form_history = True
    wf.store()
    resp = app.get(formdata.get_url(backoffice=False))
    resp.form[f'fblah_{display_form.id}_1'] = 'ABC'
    resp = resp.form.submit('submit').follow()

    # still in the same status but data displayed on the second evolution
    formdata.refresh_from_storage()
    assert formdata.status == 'wf-%s' % status.id
    assert resp.pyquery('ul#evolutions li.msg-in').length == 2
    assert resp.pyquery('ul#evolutions li.msg-in:last div div.msg div div.field p').text() == 'Test ABC'


def test_workflow_form_include_in_form_history_manual_jump(pub):
    FormDef.wipe()
    Workflow.wipe()
    BlockDef.wipe()

    user = create_user(pub)

    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.StringField(id='123', required=True, label='Blocktest', varname='blocktest'),
        fields.FileField(id='456', required='required', label='Blockfile', varname='blockfile'),
    ]
    block.store()

    wf = Workflow(name='test')
    status = wf.add_status('New', 'st1')
    display_form = status.add_action('form', id='_display_form')
    display_form.by = ['_submitter']
    display_form.varname = 'blah'
    display_form.hide_submit_button = True
    display_form.include_in_form_history = True
    display_form.formdef = WorkflowFormFieldsFormDef(item=display_form)
    display_form.formdef.fields = [
        fields.StringField(id='1', label='Test', varname='str', required=True),
        fields.FileField(id='2', required='required', label='File', varname='file'),
        fields.BlockField(id='3', label='Fooblock', block_slug='foobar', varname='fooblock', max_items='3'),
    ]
    status2 = wf.add_status('Two', 'st2')
    jump1 = status.add_action('choice', id='_jump')
    jump1.label = 'Jump'
    jump1.by = ['_submitter']
    jump1.status = status2.id
    wf.store()

    formdef = create_formdef()
    formdef.workflow_id = wf.id
    formdef.fields = []
    formdef.store()

    formdef.data_class().wipe()

    formdata = formdef.data_class()()
    formdata.user_id = user.id
    formdata.just_created()
    formdata.store()

    app = login(get_app(pub), username='foo', password='foo')
    resp = app.get(formdata.get_url(backoffice=False))

    # submit with data, no crash on file fields
    resp = resp.form.submit('button_jump')
    assert resp.pyquery(f'#form_error_fblah_{display_form.id}_1').text() == 'required field'
    assert resp.pyquery(f'#form_error_fblah_{display_form.id}_2').text() == 'required field'
    assert resp.pyquery(f'#form_error_fblah_{display_form.id}_3').text() == 'required field'

    resp.form[f'fblah_{display_form.id}_1'] = 'ABC'
    resp.form[f'fblah_{display_form.id}_2$file'] = Upload('test1.txt', b'foobar1', 'text/plain')
    resp.form[f'fblah_{display_form.id}_3$element0$f456$file'] = Upload('test2.txt', b'foobar2', 'text/plain')
    resp.form[f'fblah_{display_form.id}_3$element0$f123'] = 'DEF'
    resp = resp.form.submit('button_jump').follow()

    # status changed and data is displayed on the second evolution
    formdata.refresh_from_storage()
    assert formdata.status == 'wf-%s' % status2.id
    assert resp.pyquery('ul#evolutions li.msg-in').length == 2

    # check string fields
    assert (
        resp.pyquery(
            'ul#evolutions li.msg-in:last div div.msg div.form-summary > div.field-type-string p.value'
        ).text()
        == 'ABC'
    )
    assert (
        resp.pyquery(
            'ul#evolutions li.msg-in:last div div.msg div.form-summary div.field-type-block div.field-type-string p.value'
        ).text()
        == 'DEF'
    )

    # check file fields
    assert (
        resp.pyquery(
            'ul#evolutions li.msg-in:last div div.msg div.form-summary > div.field-type-file span'
        ).text()
        == 'test1.txt'
    )
    file_resp = resp.click('test1.txt')
    assert file_resp.content_type == 'text/plain'
    assert file_resp.text == 'foobar1'

    assert (
        resp.pyquery(
            'ul#evolutions li.msg-in:last div div.msg div.form-summary div.field-type-block span'
        ).text()
        == 'test2.txt'
    )
    file_resp = resp.click('test2.txt')
    assert file_resp.content_type == 'text/plain'
    assert file_resp.text == 'foobar2'


def test_workflow_form_include_in_form_history_jump_on_submit(pub):
    FormDef.wipe()
    Workflow.wipe()
    BlockDef.wipe()

    user = create_user(pub)

    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.StringField(id='123', required=True, label='Test', varname='test'),
    ]
    block.store()

    wf = Workflow(name='test')
    status = wf.add_status('New', 'st1')
    display_form = status.add_action('form', id='_display_form')
    display_form.by = ['_submitter']
    display_form.varname = 'blah'
    display_form.hide_submit_button = False
    display_form.include_in_form_history = True
    display_form.formdef = WorkflowFormFieldsFormDef(item=display_form)
    display_form.formdef.fields = [
        fields.StringField(id='1', label='Test', varname='str', required=True),
    ]
    status2 = wf.add_status('Two', 'st2')
    jump = status.add_action('jumponsubmit')
    jump.status = status2.id
    wf.store()

    formdef = create_formdef()
    formdef.workflow_id = wf.id
    formdef.fields = []
    formdef.store()

    formdef.data_class().wipe()
    formdata = formdef.data_class()()
    formdata.user_id = user.id
    formdata.just_created()
    formdata.store()
    app = login(get_app(pub), username='foo', password='foo')
    resp = app.get(formdata.get_url(backoffice=False))
    resp.form[f'fblah_{display_form.id}_1'] = 'ABC'
    resp = resp.form.submit('submit').follow()

    # status changed and data is displayed on the second evolution
    formdata.refresh_from_storage()
    assert formdata.status == 'wf-%s' % status2.id
    assert resp.pyquery('ul#evolutions li.msg-in').length == 2
    assert resp.pyquery('ul#evolutions li.msg-in:last div div.msg div div.field p').text() == 'Test ABC'


def test_workflow_form_include_in_form_history_global_action(pub):
    FormDef.wipe()
    Workflow.wipe()
    user = create_user(pub)
    wf = Workflow(name='status')
    wf.add_status('Status1')
    global_action = wf.add_global_action('workflow form')
    display_form = global_action.add_action('form')
    display_form.by = ['_submitter']
    display_form.varname = 'blah'
    display_form.hide_submit_button = False
    display_form.include_in_form_history = True
    display_form.formdef = WorkflowFormFieldsFormDef(item=display_form)
    display_form.formdef.fields = [
        fields.StringField(id='1', label='Test', varname='str', required=True),
    ]
    global_action.triggers[0].roles = ['_submitter']
    wf.store()
    formdef = create_formdef()
    formdef.workflow_id = wf.id
    formdef.fields = []
    formdef.store()

    formdata = formdef.data_class()()
    formdata.user_id = user.id
    formdata.just_created()
    formdata.store()

    app = login(get_app(pub), username='foo', password='foo')
    resp = app.get(formdata.get_url(backoffice=False))
    resp = resp.forms['wf-actions'].submit('button-action-1').follow()
    resp.forms['wf-actions'][f'fblah_{display_form.id}_1'] = 'ABC'
    resp = resp.forms['wf-actions'].submit('submit').follow()
    assert resp.pyquery('ul#evolutions li.msg-in').length == 2
    assert resp.pyquery('ul#evolutions li.msg-in:last div div.msg div div.field p').text() == 'Test ABC'


def test_workflow_form_include_in_form_history_same_status(pub):
    FormDef.wipe()
    Workflow.wipe()
    BlockDef.wipe()

    user = create_user(pub)

    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.StringField(id='123', required=True, label='Blocktest', varname='blocktest'),
        fields.FileField(id='456', required='required', label='Blockfile', varname='blockfile'),
    ]
    block.store()

    wf = Workflow(name='test')
    status = wf.add_status('New', 'st1')
    display_form = status.add_action('form', id='_display_form')
    display_form.by = ['_submitter']
    display_form.varname = 'blah'
    display_form.hide_submit_button = False
    display_form.include_in_form_history = True
    display_form.formdef = WorkflowFormFieldsFormDef(item=display_form)
    display_form.formdef.fields = [
        fields.StringField(id='1', label='Test', varname='str', required=True),
        fields.FileField(id='2', required='required', label='File', varname='file'),
        fields.BlockField(id='3', label='Fooblock', block_slug='foobar', varname='fooblock', max_items='3'),
    ]
    wf.store()

    formdef = create_formdef()
    formdef.workflow_id = wf.id
    formdef.fields = []
    formdef.store()

    formdef.data_class().wipe()
    formdata = formdef.data_class()()
    formdata.user_id = user.id
    formdata.just_created()
    formdata.store()

    app = login(get_app(pub), username='foo', password='foo')
    resp = app.get(formdata.get_url(backoffice=False))
    resp.form[f'fblah_{display_form.id}_1'] = 'ABC'
    resp.form[f'fblah_{display_form.id}_2$file'] = Upload('test1.txt', b'foobar1', 'text/plain')
    resp.form[f'fblah_{display_form.id}_3$element0$f456$file'] = Upload('test2.txt', b'foobar2', 'text/plain')
    resp.form[f'fblah_{display_form.id}_3$element0$f123'] = 'DEF'
    resp = resp.form.submit('submit').follow()

    file_resp = resp.click('test1.txt')
    assert file_resp.content_type == 'text/plain'
    assert file_resp.text == 'foobar1'
    file_resp = resp.click('test2.txt')
    assert file_resp.content_type == 'text/plain'
    assert file_resp.text == 'foobar2'

    # check that we can submit new files and that there are no conflicts with the previous ones
    resp.form[f'fblah_{display_form.id}_1'] = 'ABC'
    resp.form[f'fblah_{display_form.id}_2$file'] = Upload('test3.txt', b'foobar3', 'text/plain')
    resp.form[f'fblah_{display_form.id}_3$element0$f456$file'] = Upload('test4.txt', b'foobar4', 'text/plain')
    resp.form[f'fblah_{display_form.id}_3$element0$f123'] = 'DEF'
    resp = resp.form.submit('submit').follow()

    file_resp = resp.click('test3.txt')
    assert file_resp.content_type == 'text/plain'
    assert file_resp.text == 'foobar3'
    file_resp = resp.click('test4.txt')
    assert file_resp.content_type == 'text/plain'
    assert file_resp.text == 'foobar4'


def test_workflow_form_global_action_post_conditions(pub):
    FormDef.wipe()
    Workflow.wipe()
    user = create_user(pub)
    wf = Workflow(name='status')
    wf.add_status('Status1')
    global_action = wf.add_global_action('workflow form')
    display_form = global_action.add_action('form')
    display_form.by = ['_submitter']
    display_form.varname = 'blah'
    display_form.hide_submit_button = False
    display_form.post_conditions = [
        {'condition': {'type': 'django', 'value': 'False'}, 'error_message': 'You shall not pass.'}
    ]
    display_form.formdef = WorkflowFormFieldsFormDef(item=display_form)
    display_form.formdef.fields = [
        fields.StringField(id='1', label='Test', varname='str', required=True),
    ]
    global_action.triggers[0].roles = ['_submitter']
    wf.store()
    formdef = create_formdef()
    formdef.workflow_id = wf.id
    formdef.fields = []
    formdef.store()

    formdata = formdef.data_class()()
    formdata.user_id = user.id
    formdata.just_created()
    formdata.store()

    app = login(get_app(pub), username='foo', password='foo')
    resp = app.get(formdata.get_url(backoffice=False))
    resp = resp.forms['wf-actions'].submit('button-action-1').follow()
    resp.forms['wf-actions'][f'fblah_{display_form.id}_1'] = 'ABC'
    resp = resp.forms['wf-actions'].submit('submit')
    assert resp.pyquery('.errornotice.global-errors').text() == 'You shall not pass.'
    assert resp.forms['wf-actions'][f'fblah_{display_form.id}_1'].value == 'ABC'  # value has been kept
