import json

import pytest
import responses
from quixote import cleanup

from wcs import fields
from wcs.blocks import BlockDef
from wcs.carddef import CardDef
from wcs.formdef import FormDef
from wcs.logged_errors import LoggedError
from wcs.qommon.http_request import HTTPRequest
from wcs.wf.create_formdata import Mapping
from wcs.workflow_traces import WorkflowTrace
from wcs.workflows import Workflow

from ..utilities import clean_temporary_pub, create_temporary_pub


def setup_module(module):
    cleanup()


def teardown_module(module):
    clean_temporary_pub()


@pytest.fixture
def pub(request):
    pub = create_temporary_pub()
    pub.cfg['language'] = {'language': 'en'}
    pub.write_cfg()
    req = HTTPRequest(None, {'SERVER_NAME': 'example.net', 'SCRIPT_NAME': ''})
    req.response.filter = {}
    req._user = None
    pub._set_request(req)
    pub.set_config(req)
    return pub


def test_status_loop(pub):
    Workflow.wipe()

    workflow = Workflow(name='foo')
    st1 = workflow.add_status(name='baz')
    st2 = workflow.add_status(name='bar')
    st1.loop_items_template = '{{ "abc"|make_list }}'
    st1.after_loop_status = str(st2.id)
    register_comment1 = st1.add_action('register-comment', id='_register-comment1')
    register_comment1.comment = 'foo {{ status_loop.items }} / {{ status_loop.current_item }} bar'
    register_comment2 = st1.add_action('register-comment', id='_register-comment2')
    register_comment2.comment = 'foo {{ status_loop.index }} / {{ status_loop.index0 }} bar'
    register_comment3 = st1.add_action('register-comment', id='_register-comment3')
    register_comment3.comment = 'foo {{ status_loop.first }} / {{ status_loop.last }} bar'
    workflow.store()

    formdef = FormDef()
    formdef.name = 'bar'
    formdef.fields = []
    formdef.workflow = workflow
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()
    formdata.perform_workflow()
    assert formdata.status == 'wf-%s' % st2.id
    formdata.evolution[0]._display_parts = None  # invalidate cache
    assert [str(x) for x in formdata.evolution[0].display_parts()] == [
        '<div>foo [&#x27;a&#x27;, &#x27;b&#x27;, &#x27;c&#x27;] / a bar</div>',
        '<div>foo 1 / 0 bar</div>',
        '<div>foo True / False bar</div>',
        '<div>foo [&#x27;a&#x27;, &#x27;b&#x27;, &#x27;c&#x27;] / b bar</div>',
        '<div>foo 2 / 1 bar</div>',
        '<div>foo False / False bar</div>',
        '<div>foo [&#x27;a&#x27;, &#x27;b&#x27;, &#x27;c&#x27;] / c bar</div>',
        '<div>foo 3 / 2 bar</div>',
        '<div>foo False / True bar</div>',
    ]
    trace = WorkflowTrace.select_for_formdata(formdata)[0]
    assert trace.event == 'loop-start'
    assert trace.event_args == {}
    trace = WorkflowTrace.select_for_formdata(formdata)[-2]
    assert trace.event == 'loop-end'
    assert trace.event_args == {}

    # add a conditional jump to stop the loop
    st3 = workflow.add_status(name='stop')
    jump = st1.add_action('jump', id='_jump')
    jump.status = st3.id
    jump.condition = {'type': 'django', 'value': 'status_loop.index0 == 1'}
    workflow.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()
    formdata.perform_workflow()
    assert formdata.status == 'wf-%s' % st3.id
    formdata.evolution[0]._display_parts = None  # invalidate cache
    assert [str(x) for x in formdata.evolution[0].display_parts()] == [
        '<div>foo [&#x27;a&#x27;, &#x27;b&#x27;, &#x27;c&#x27;] / a bar</div>',
        '<div>foo 1 / 0 bar</div>',
        '<div>foo True / False bar</div>',
        '<div>foo [&#x27;a&#x27;, &#x27;b&#x27;, &#x27;c&#x27;] / b bar</div>',
        '<div>foo 2 / 1 bar</div>',
        '<div>foo False / False bar</div>',
    ]


def test_status_loop_on_cards(pub):
    CardDef.wipe()
    Workflow.wipe()

    carddef = CardDef()
    carddef.name = 'foo'
    carddef.fields = []
    carddef.store()
    carddef.data_class().wipe()
    for i in range(0, 2):
        carddata = carddef.data_class()()
        carddata.just_created()
        carddata.store()

    workflow = Workflow(name='foo')
    st1 = workflow.add_status(name='baz')
    st2 = workflow.add_status(name='bar')
    st1.loop_items_template = '{{ cards|objects:"foo" }}'
    st1.after_loop_status = str(st2.id)
    register_comment = st1.add_action('register-comment', id='_register-comment')
    register_comment.comment = 'foo {{ status_loop.current_item.internal_id }} bar'
    workflow.store()

    formdef = FormDef()
    formdef.name = 'bar'
    formdef.fields = []
    formdef.workflow = workflow
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()
    formdata.perform_workflow()
    assert formdata.status == 'wf-%s' % st2.id
    formdata.evolution[0]._display_parts = None  # invalidate cache
    assert [str(x) for x in formdata.evolution[0].display_parts()] == [
        '<div>foo 1 bar</div>',
        '<div>foo 2 bar</div>',
    ]


def test_status_loop_on_wscall(pub):
    Workflow.wipe()

    workflow = Workflow(name='foo')
    st0 = workflow.add_status(name='foo')
    wscall = st0.add_action('webservice_call', id='_wscall')
    wscall.url = 'http://test/'
    wscall.varname = 'wscall'
    st1 = workflow.add_status(name='baz')
    jump = st0.add_action('jump', id='_jump')
    jump.status = st1.id
    st2 = workflow.add_status(name='bar')
    st1.loop_items_template = '{{ form_workflow_data_wscall_response_result }}'
    st1.after_loop_status = str(st2.id)
    register_comment = st1.add_action('register-comment', id='_register-comment')
    register_comment.comment = 'foo {{ status_loop.current_item }} bar'
    workflow.store()

    formdef = FormDef()
    formdef.name = 'bar'
    formdef.fields = []
    formdef.workflow = workflow
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()
    with responses.RequestsMock() as rsps:
        rsps.get('http://test', status=200, json={'result': ['a', 'b', 'c', 'd']})
        formdata.perform_workflow()
    assert formdata.status == 'wf-%s' % st2.id
    formdata.evolution[1]._display_parts = None  # invalidate cache
    assert [str(x) for x in formdata.evolution[1].display_parts()] == [
        '<div>foo a bar</div>',
        '<div>foo b bar</div>',
        '<div>foo c bar</div>',
        '<div>foo d bar</div>',
    ]


def test_status_loop_on_block(pub):
    Workflow.wipe()
    BlockDef.wipe()

    block = BlockDef()
    block.name = 'Child'
    block.fields = [
        fields.StringField(id='123', required='required', label='First name', varname='firstname')
    ]
    block.digest_template = '{{ child_var_firstname }}'
    block.store()

    workflow = Workflow(name='foo')
    st1 = workflow.add_status(name='baz')
    st2 = workflow.add_status(name='bar')
    st1.loop_items_template = '{{ form_var_children }}'
    st1.after_loop_status = str(st2.id)
    register_comment = st1.add_action('register-comment', id='_register-comment')
    register_comment.comment = 'foo {{ status_loop.index }} {{ status_loop.current_item.firstname }} bar'
    workflow.store()

    formdef = FormDef()
    formdef.name = 'bar'
    formdef.fields = [fields.BlockField(id='1', label='Children', block_slug='child', varname='children')]
    formdef.workflow = workflow
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {
        '1': {
            'data': [{'123': 'first1'}, {'123': 'first2'}],
            'schema': {'123': 'string'},
        },
        '1_display': 'foo, bar',
    }
    formdata.just_created()
    formdata.store()
    formdata.perform_workflow()
    assert formdata.status == 'wf-%s' % st2.id
    formdata.evolution[0]._display_parts = None  # invalidate cache
    assert [str(x) for x in formdata.evolution[0].display_parts()] == [
        '<div>foo 1 first1 bar</div>',
        '<div>foo 2 first2 bar</div>',
    ]


def test_loop_on_block_create_carddata(pub):
    LoggedError.wipe()
    Workflow.wipe()
    BlockDef.wipe()
    CardDef.wipe()

    block = BlockDef()
    block.name = 'foobar'
    block.digest_template = 'X{{ foobar_var_foo }}Y'
    block.fields = [
        fields.StringField(id='123', required='required', label='Test', varname='foo'),
        fields.StringField(id='234', required='required', label='Test2', varname='bar'),
    ]
    block.store()

    carddef = CardDef()
    carddef.name = 'Foo Card'
    carddef.fields = [
        fields.StringField(id='0', label='foo', varname='foo'),
        fields.StringField(id='1', label='bar', varname='bar'),
    ]
    carddef.store()
    carddef.data_class().wipe()

    workflow = Workflow(name='foo')
    st1 = workflow.add_status(name='baz')
    st2 = workflow.add_status(name='bar')
    st1.loop_items_template = '{{ form_var_foobars }}'
    st1.after_loop_status = str(st2.id)
    create = st1.add_action('create_carddata', id='_create')
    create.formdef_slug = carddef.url_name
    create.mappings = [
        Mapping(field_id='0', expression='{{ status_loop.current_item.foo }}'),
        Mapping(field_id='1', expression='{{ status_loop.current_item.bar }}'),
    ]
    workflow.store()

    formdef = FormDef()
    formdef.name = 'bar'
    formdef.fields = [fields.BlockField(id='1', label='Foobar', block_slug='foobar', varname='foobars')]
    formdef.workflow = workflow
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {
        '1': {
            'data': [{'123': 'foo-111', '234': 'bar-111'}, {'123': 'foo-222', '234': 'bar-222'}],
            'schema': {'123': 'string'},
        },
        '1_display': 'foo, bar',
    }
    formdata.just_created()
    formdata.store()
    formdata.perform_workflow()
    assert formdata.status == 'wf-%s' % st2.id

    assert carddef.data_class().count() == 2
    new_carddata = carddef.data_class().select(order_by='id')[0]
    assert new_carddata.data == {
        '0': 'foo-111',
        '1': 'bar-111',
    }
    new_carddata = carddef.data_class().select(order_by='id')[1]
    assert new_carddata.data == {
        '0': 'foo-222',
        '1': 'bar-222',
    }

    # empty block
    formdata = formdef.data_class()()
    formdata.data = {
        '1': None,
        '1_display': None,
    }
    formdata.just_created()
    formdata.store()
    formdata.perform_workflow()
    assert formdata.status == 'wf-%s' % st2.id
    assert carddef.data_class().count() == 2
    assert LoggedError.count() == 0


def test_status_loop_on_items(pub):
    CardDef.wipe()
    Workflow.wipe()

    workflow = Workflow(name='foo')
    st1 = workflow.add_status(name='baz')
    st2 = workflow.add_status(name='bar')
    st1.loop_items_template = '{{ form_var_items }}'
    st1.after_loop_status = str(st2.id)
    register_comment = st1.add_action('register-comment', id='_register-comment')
    register_comment.comment = 'foo {{ status_loop.index }} {{ status_loop.current_item }} bar'
    workflow.store()

    formdef = FormDef()
    formdef.name = 'bar'
    formdef.fields = [
        fields.ItemsField(id='1', label='Items', varname='items', items=['foo1', 'foo2', 'foo3'])
    ]
    formdef.workflow = workflow
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {
        '1': ['foo1', 'foo3'],
    }
    formdata.data['1_display'] = formdef.fields[0].store_display_value(formdata.data, '1')
    formdata.data['1_structured'] = formdef.fields[0].store_structured_value(formdata.data, '1')
    formdata.just_created()
    formdata.store()
    formdata.perform_workflow()
    assert formdata.status == 'wf-%s' % st2.id
    formdata.evolution[0]._display_parts = None  # invalidate cache
    assert [str(x) for x in formdata.evolution[0].display_parts()] == [
        '<div>foo 1 foo1 bar</div>',
        '<div>foo 2 foo3 bar</div>',
    ]

    # with carddef datasource
    carddef = CardDef()
    carddef.name = 'foo'
    carddef.fields = [fields.StringField(id='1', label='First name', varname='firstname')]
    carddef.store()
    carddef.data_class().wipe()
    for i in range(0, 3):
        carddata = carddef.data_class()()
        carddata.data = {
            '1': 'foo%s' % i,
        }
        carddata.just_created()
        carddata.store()

    formdef.fields[0].data_source = {'type': 'carddef:%s' % carddef.url_name}
    formdef.fields[0].items = None
    formdef.store()
    st1.loop_items_template = '{{ form_var_items }}'
    register_comment.comment = 'foo {{ status_loop.index }} {{ status_loop.current_item.var.firstname }} bar'
    workflow.store()

    formdata = formdef.data_class()()
    formdata.data = {
        '1': ['1', '3'],
    }
    formdata.data['1_display'] = formdef.fields[0].store_display_value(formdata.data, '1')
    formdata.data['1_structured'] = formdef.fields[0].store_structured_value(formdata.data, '1')
    formdata.just_created()
    formdata.store()
    formdata.perform_workflow()
    assert formdata.status == 'wf-%s' % st2.id
    formdata.evolution[0]._display_parts = None  # invalidate cache
    assert [str(x) for x in formdata.evolution[0].display_parts()] == [
        '<div>foo 1 foo0 bar</div>',
        '<div>foo 2 foo2 bar</div>',
    ]

    # with json value
    datasource = {
        'type': 'jsonvalue',
        'value': json.dumps(
            [
                {'id': 'b', 'text': 'baker', 'extra': 'plop'},
                {'id': 'c', 'text': 'cook', 'extra': 'plop2'},
                {'id': 'l', 'text': 'lawyer', 'extra': 'plop3'},
            ]
        ),
    }
    formdef.fields[0].data_source = datasource
    formdef.store()
    st1.loop_items_template = '{{ form_var_items }}'
    register_comment.comment = 'foo {{ status_loop.index }} {{ status_loop.current_item.extra }} bar'
    workflow.store()

    formdata = formdef.data_class()()
    formdata.data = {
        '1': ['b', 'l'],
    }
    formdata.data['1_display'] = formdef.fields[0].store_display_value(formdata.data, '1')
    formdata.data['1_structured'] = formdef.fields[0].store_structured_value(formdata.data, '1')
    formdata.just_created()
    formdata.store()
    formdata.perform_workflow()
    assert formdata.status == 'wf-%s' % st2.id
    formdata.evolution[0]._display_parts = None  # invalidate cache
    assert [str(x) for x in formdata.evolution[0].display_parts()] == [
        '<div>foo 1 plop bar</div>',
        '<div>foo 2 plop3 bar</div>',
    ]


def test_status_loop_unknown_status_with_global_action(pub):
    workflow = Workflow(name='foo')
    st1 = workflow.add_status(name='baz')
    st2 = workflow.add_status(name='bar')
    st1.loop_items_template = '{{ "abc"|make_list }}'
    st1.after_loop_status = str(st2.id)
    ac1 = workflow.add_global_action('Action', 'ac1')
    ac1.backoffice_info_text = '<p>Foo</p>'
    add_to_journal = ac1.add_action('register-comment', id='_add_to_journal')
    add_to_journal.comment = 'HELLO WORLD'
    trigger = ac1.triggers[0]
    assert trigger.key == 'manual'
    trigger.roles = ['_submitter']
    trigger.statuses = ['unknown']
    workflow.store()

    formdef = FormDef()
    formdef.name = 'bar'
    formdef.fields = []
    formdef.workflow = workflow
    formdef.store()

    user = pub.user_class(name='admin')
    user.email = 'admin@localhost'
    user.store()
    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.status = 'unknown'
    formdata.user_id = str(user.id)
    formdata.store()
    formdata.perform_global_action(ac1.id, user)  # no error


def test_status_loop_vs_global_action(pub):
    Workflow.wipe()

    user = pub.user_class(name='admin')
    user.email = 'admin@localhost'
    user.store()

    workflow = Workflow(name='foo')
    st1 = workflow.add_status(name='baz')
    st2 = workflow.add_status(name='bar')
    st1.loop_items_template = '{{ "abc"|make_list }}'
    st1.after_loop_status = str(st2.id)
    register_comment1 = st1.add_action('register-comment', id='_register-comment1')
    register_comment1.comment = 'foo {{ status_loop.items }} / {{ status_loop.current_item }} bar'

    action = workflow.add_global_action('Remove')
    add_to_journal = action.add_action('register-comment', id='_add_to_journal')
    add_to_journal.comment = '<p>HELLO WORLD</p>'
    trigger = action.triggers[0]
    trigger.roles = ['_submitter']

    workflow.store()

    formdef = FormDef()
    formdef.name = 'bar'
    formdef.fields = []
    formdef.workflow = workflow
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.user_id = user.id
    formdata.store()
    assert formdata.status == 'wf-%s' % st1.id

    formdata.perform_global_action(action.id, user)  # no error
    assert [str(x) for x in formdata.evolution[0].display_parts()] == ['<p>HELLO WORLD</p>']


def test_status_loop_on_invalid_type(pub):
    Workflow.wipe()
    LoggedError.wipe()

    workflow = Workflow(name='foo')
    st0 = workflow.add_status(name='foo')
    st1 = workflow.add_status(name='baz')
    jump = st0.add_action('jump', id='_jump')
    jump.status = st1.id
    st2 = workflow.add_status(name='bar')
    st1.loop_items_template = '{{ 1 }}'
    st1.after_loop_status = str(st2.id)
    register_comment = st1.add_action('register-comment', id='_register-comment')
    register_comment.comment = 'foo {{ status_loop.current_item }} bar'
    workflow.store()

    formdef = FormDef()
    formdef.name = 'bar'
    formdef.fields = []
    formdef.workflow = workflow
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()
    formdata.perform_workflow()
    assert formdata.status == 'wf-%s' % st2.id
    formdata.evolution[1]._display_parts = None  # invalidate cache
    assert [str(x) for x in formdata.evolution[1].display_parts()] == []  # nothing
    assert LoggedError.count() == 1
    assert LoggedError.select()[0].summary == 'Invalid value to be looped on (1)'


def test_status_loop_after_loop_previously_marked_status(pub):
    Workflow.wipe()

    workflow = Workflow(name='foo')
    st1 = workflow.add_status(name='baz')
    st2 = workflow.add_status(name='bar')
    st1.loop_items_template = '{{ "abc"|make_list }}'
    st1.after_loop_status = '_previous'
    register_comment1 = st1.add_action('register-comment', id='_register-comment1')
    register_comment1.comment = 'foo {{ status_loop.items }} / {{ status_loop.current_item }} bar'
    workflow.store()

    formdef = FormDef()
    formdef.name = 'bar'
    formdef.fields = []
    formdef.workflow = workflow
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.workflow_data = {'_markers_stack': [{'status_id': str(st2.id)}]}
    formdata.store()
    formdata.perform_workflow()
    formdata.evolution[0]._display_parts = None  # invalidate cache
    assert [str(x) for x in formdata.evolution[0].display_parts()] == [
        '<div>foo [&#x27;a&#x27;, &#x27;b&#x27;, &#x27;c&#x27;] / a bar</div>',
        '<div>foo [&#x27;a&#x27;, &#x27;b&#x27;, &#x27;c&#x27;] / b bar</div>',
        '<div>foo [&#x27;a&#x27;, &#x27;b&#x27;, &#x27;c&#x27;] / c bar</div>',
    ]
    assert formdata.status == 'wf-%s' % st2.id


def test_status_loop_after_loop_global_action(pub):
    Workflow.wipe()

    pub.user_class.wipe()
    user = pub.user_class(name='admin')
    user.email = 'admin@localhost'
    user.store()

    workflow = Workflow(name='foo')
    st1 = workflow.add_status(name='baz')
    st2 = workflow.add_status(name='bar')
    st1.loop_items_template = '{{ "abc"|make_list }}'
    st1.after_loop_status = str(st2.id)

    action = workflow.add_global_action('Global action')
    register_comment1 = action.add_action('register-comment')
    register_comment1.comment = 'blah'
    trigger = action.triggers[0]
    trigger.roles = ['_submitter']

    workflow.store()

    formdef = FormDef()
    formdef.name = 'bar'
    formdef.fields = []
    formdef.workflow = workflow
    formdef.store()

    formdata = formdef.data_class()()
    formdata.user_id = user.id
    formdata.just_created()
    formdata.store()

    formdata.perform_global_action(action.id, user)
    assert formdata.status == 'wf-%s' % st1.id  # no jump
    assert [str(x) for x in formdata.evolution[-1].display_parts()] == ['<p>blah</p>']
