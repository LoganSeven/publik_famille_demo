import datetime
import json
import os

import pytest
from quixote import cleanup

from wcs import sessions
from wcs.blocks import BlockDef
from wcs.carddef import CardDef
from wcs.fields import BlockField, ComputedField, DateField, FileField, ItemField, MapField, StringField
from wcs.formdef import FormDef
from wcs.logged_errors import LoggedError
from wcs.qommon.http_request import HTTPRequest
from wcs.qommon.upload_storage import PicklableUpload
from wcs.wf.create_formdata import JournalAssignationErrorPart, LinkedFormdataEvolutionPart, Mapping
from wcs.workflow_traces import WorkflowTrace
from wcs.workflows import Workflow

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
    pub.set_app_dir(req)
    req.session = sessions.BasicSession(id=1)
    pub.set_config(req)
    return pub


def test_create_carddata(pub):
    CardDef.wipe()
    FormDef.wipe()

    carddef = CardDef()
    carddef.name = 'My card'
    carddef.fields = [
        StringField(id='1', label='string'),
        ItemField(id='2', label='List', items=['item1', 'item2'], varname='clist'),
        DateField(id='3', label='Date', varname='cdate'),
        FileField(id='4', label='File', varname='cfile'),
    ]
    carddef.store()

    wf = Workflow(name='create-carddata')
    wf.possible_status = Workflow.get_default_workflow().possible_status[:]
    create = wf.possible_status[1].add_action('create_carddata', id='_create', prepend=True)
    assert create.get_line_details() == 'not configured'
    create.varname = 'mycard'
    create.formdef_slug = carddef.url_name
    create.mappings = [
        Mapping(field_id='1', expression='{% if x = b %}test{% endif %}'),
        Mapping(field_id='2', expression='{{ form_var_list }}'),
        Mapping(field_id='4', expression='{{ form_var_file|default_if_none:"" }}'),
    ]
    assert create.get_line_details() == carddef.name
    create.action_label = 'Create CardDef'
    assert create.get_line_details() == 'Create CardDef'
    wf.store()

    formdef = FormDef()
    formdef.name = 'source form'
    formdef.fields = [
        ItemField(id='1', label='List', items=['item1', 'item2'], varname='list'),
        FileField(id='3', label='File', varname='file'),
    ]
    formdef.workflow_id = wf.id
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {}
    formdata.just_created()

    assert carddef.data_class().count() == 0

    formdata = formdef.data_class()()
    formdata.data = {}

    formdata.just_created()
    formdata.store()
    formdata.perform_workflow()

    assert carddef.data_class().count() == 1
    # check tracing
    carddata = carddef.data_class().select()[0]
    trace = WorkflowTrace.select_for_formdata(carddata)[0]
    assert trace.event == 'workflow-created'
    assert trace.event_args['external_workflow_id'] == wf.id
    assert trace.event_args['external_status_id'] == 'new'
    assert trace.event_args['external_item_id'] == '_create'
    trace = WorkflowTrace.select_for_formdata(formdata)[-1]
    assert trace.event == 'workflow-created-carddata'
    assert trace.event_args['external_formdef_id'] == carddef.id
    assert trace.event_args['external_formdata_id'] == carddata.id

    errors = LoggedError.select()
    assert len(errors) == 1
    assert any('syntax error in Django template' in (error.exception_message or '') for error in errors)

    formdata = formdef.data_class()()

    upload = PicklableUpload('test.jpeg', 'image/jpeg')
    with open(os.path.join(os.path.dirname(__file__), '..', 'image-with-gps-data.jpeg'), 'rb') as jpg:
        upload.receive([jpg.read()])

    formdata.data = {'1': 'item1', '1_display': 'item1', '3': upload}
    formdata.just_created()
    formdata.store()
    formdata.perform_workflow()

    assert formdata.get_substitution_variables()['form_links_mycard_form_number'] == '1-2'
    carddata = carddef.data_class().get(id=2)
    assert carddata.data['2'] == 'item1'
    assert carddata.data['2_display'] == 'item1'
    assert carddata.data['4'].base_filename == 'test.jpeg'

    create.condition = {'type': 'django', 'value': '1 == 2'}
    wf.store()
    del formdef._workflow
    carddef.data_class().wipe()
    assert carddef.data_class().count() == 0
    formdata.perform_workflow()
    assert carddef.data_class().count() == 0


def test_create_carddata_with_links(pub):
    CardDef.wipe()
    FormDef.wipe()

    carddef1 = CardDef()
    carddef1.name = 'Card 1'
    carddef1.fields = [
        StringField(id='1', label='string', varname='foo'),
    ]
    carddef1.store()
    carddef1.data_class().wipe()
    carddef2 = CardDef()
    carddef2.name = 'Card 2'
    carddef2.fields = [
        StringField(id='1', label='string', varname='bar'),
        ItemField(
            id='2', label='card', varname='card1', data_source={'type': 'carddef:%s' % carddef1.url_name}
        ),
    ]
    carddef2.store()
    carddef2.data_class().wipe()

    wf = Workflow(name='create-carddata')
    st1 = wf.add_status('Create cards', 'st1')
    create1 = st1.add_action('create_carddata', id='_create1')
    create1.action_label = 'Create CardDef1'
    create1.varname = 'mycard1'
    create1.formdef_slug = carddef1.url_name
    create1.mappings = [
        Mapping(field_id='1', expression='{{ form_var_card1_foo }}'),
    ]
    create2 = st1.add_action('create_carddata', id='_create2')
    create2.action_label = 'Create CardDef2'
    create2.varname = 'mycard2'
    create2.formdef_slug = carddef2.url_name
    create2.mappings = [
        Mapping(field_id='1', expression='{{ form_var_card2_bar }}'),
        Mapping(field_id='2', expression='{{ form_links_mycard1 }}'),
    ]
    wf.store()

    formdef = FormDef()
    formdef.name = 'form'
    formdef.fields = [
        StringField(id='1', label='foo', varname='card1_foo'),
        StringField(id='2', label='bar', varname='card2_bar'),
    ]
    formdef.workflow_id = wf.id
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {
        '1': 'card1 foo',
        '2': 'card2 bar',
    }
    formdata.just_created()
    formdata.store()
    formdata.perform_workflow()

    assert carddef1.data_class().count() == 1
    assert carddef2.data_class().count() == 1
    carddata1 = carddef1.data_class().select()[0]
    carddata2 = carddef2.data_class().select()[0]
    assert carddata1.data['1'] == 'card1 foo'
    assert carddata2.data['1'] == 'card2 bar'
    assert carddata2.data['2'] == str(carddata1.id)

    create2.mappings[1] = Mapping(field_id='2', expression='{{ form_links_mycard1_form_number_raw }}')
    wf.store()
    formdef = FormDef.get(formdef.id)

    carddef1.data_class().wipe()
    carddef2.data_class().wipe()
    formdef.data_class().wipe()
    formdata = formdef.data_class()()
    formdata.data = {
        '1': 'card1 fooo',
        '2': 'card2 barr',
    }
    formdata.just_created()
    formdata.store()
    formdata.perform_workflow()

    assert carddef1.data_class().count() == 1
    assert carddef2.data_class().count() == 1
    carddata1 = carddef1.data_class().select()[0]
    carddata2 = carddef2.data_class().select()[0]
    assert carddata1.data['1'] == 'card1 fooo'
    assert carddata2.data['1'] == 'card2 barr'
    assert carddata2.data['2'] == str(carddata1.id)
    old_carddata1 = carddata1

    formdata.perform_workflow()  # again

    assert carddef1.data_class().count() == 2
    assert carddef2.data_class().count() == 2
    carddata1 = carddef1.data_class().get(id=2)
    carddata2 = carddef2.data_class().get(id=2)
    assert carddata1.data['1'] == 'card1 fooo'
    assert carddata2.data['1'] == 'card2 barr'
    assert carddata2.data['2'] == str(old_carddata1.id)  # first item

    for expression in ('{{ form_links_mycard1 }}', '{{ form_links_mycard1|last }}'):
        create2.mappings[1] = Mapping(field_id='2', expression=expression)
        wf.store()
        formdef = FormDef.get(formdef.id)

        carddef1.data_class().wipe()
        carddef2.data_class().wipe()
        formdef.data_class().wipe()
        formdata = formdef.data_class()()
        formdata.data = {
            '1': 'card1 fooo',
            '2': 'card2 barr',
        }
        formdata.just_created()
        formdata.store()
        formdata.perform_workflow()

        assert carddef1.data_class().count() == 1
        assert carddef2.data_class().count() == 1
        carddata1 = carddef1.data_class().select()[0]
        carddata2 = carddef2.data_class().select()[0]
        assert carddata1.data['1'] == 'card1 fooo'
        assert carddata2.data['1'] == 'card2 barr'
        assert carddata2.data['2'] == str(carddata1.id)

        formdata.perform_workflow()  # again

        assert carddef1.data_class().count() == 2
        assert carddef2.data_class().count() == 2
        carddata1 = carddef1.data_class().select(order_by='id')[-1]
        carddata2 = carddef2.data_class().select(order_by='id')[-1]
        assert carddata1.data['1'] == 'card1 fooo'
        assert carddata2.data['1'] == 'card2 barr'
        assert carddata2.data['2'] == str(carddata1.id)

    create2.mappings[1] = Mapping(field_id='2', expression='{{ form_links_mycard1_0 }}')
    wf.store()
    formdef = FormDef.get(formdef.id)

    carddef1.data_class().wipe()
    carddef2.data_class().wipe()
    formdef.data_class().wipe()
    formdata = formdef.data_class()()
    formdata.data = {
        '1': 'card1 fooo',
        '2': 'card2 barr',
    }
    formdata.just_created()
    formdata.store()
    formdata.perform_workflow()

    assert carddef1.data_class().count() == 1
    assert carddef2.data_class().count() == 1
    carddata1 = carddef1.data_class().select()[0]
    carddata2 = carddef2.data_class().select()[0]
    assert carddata1.data['1'] == 'card1 fooo'
    assert carddata2.data['1'] == 'card2 barr'
    assert carddata2.data['2'] == str(carddata1.id)

    create2.mappings[1] = Mapping(field_id='2', expression='{{ form_links_mycard1_0_form_number_raw }}')
    wf.store()
    formdef = FormDef.get(formdef.id)

    carddef1.data_class().wipe()
    carddef2.data_class().wipe()
    formdef.data_class().wipe()
    formdata = formdef.data_class()()
    formdata.data = {
        '1': 'card1 fooo',
        '2': 'card2 barr',
    }
    formdata.just_created()
    formdata.store()
    formdata.perform_workflow()

    assert carddef1.data_class().count() == 1
    assert carddef2.data_class().count() == 1
    carddata1 = carddef1.data_class().select()[0]
    carddata2 = carddef2.data_class().select()[0]
    assert carddata1.data['1'] == 'card1 fooo'
    assert carddata2.data['1'] == 'card2 barr'
    assert carddata2.data['2'] == str(carddata1.id)


def test_create_carddata_with_map_field(pub):
    CardDef.wipe()
    FormDef.wipe()

    carddef = CardDef()
    carddef.name = 'My card'
    carddef.fields = [
        MapField(id='1', label='map', varname='map'),
    ]
    carddef.store()

    wf = Workflow(name='create-carddata')
    wf.possible_status = Workflow.get_default_workflow().possible_status[:]
    create = wf.possible_status[1].add_action('create_carddata', id='_create', prepend=True)
    create.action_label = 'Create CardDef'
    create.varname = 'mycard'
    create.formdef_slug = carddef.url_name
    create.mappings = [
        Mapping(field_id='1', expression=''),
    ]
    wf.store()

    formdef = FormDef()
    formdef.name = 'source form'
    formdef.fields = [
        MapField(id='1', label='map', varname='map'),
    ]
    formdef.workflow_id = wf.id
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {}
    formdata.just_created()
    assert carddef.data_class().count() == 0

    # empty value
    formdata = formdef.data_class()()
    formdata.data = {}
    formdata.just_created()
    formdata.store()
    formdata.perform_workflow()
    assert carddef.data_class().count() == 1
    assert not carddef.data_class().select()[0].data.get('1')

    # valid coordinates
    create.mappings[0].expression = '1;2'
    wf.store()
    formdef.refresh_from_storage()
    carddef.data_class().wipe()
    formdata = formdef.data_class()()
    formdata.data = {}
    formdata.just_created()
    formdata.store()
    formdata.perform_workflow()
    assert carddef.data_class().count() == 1
    assert carddef.data_class().select()[0].data.get('1') == {'lat': 1, 'lon': 2}

    # invalid value
    create.mappings[0].expression = 'plop'
    wf.store()
    formdef.refresh_from_storage()
    carddef.data_class().wipe()
    formdata = formdef.data_class()()
    formdata.data = {}
    formdata.just_created()
    formdata.store()
    formdata.perform_workflow()
    assert carddef.data_class().count() == 1
    assert not carddef.data_class().select()[0].data.get('1')

    errors = LoggedError.select()
    assert len(errors) == 1
    assert any('invalid coordinates' in (error.exception_message or '') for error in errors)

    # value from formdata
    create.mappings[0].expression = '{{ form_var_map }}'
    wf.store()
    formdef.refresh_from_storage()
    carddef.data_class().wipe()
    formdata = formdef.data_class()()
    formdata.data = {'1': {'lat': 2, 'lon': 3}}
    formdata.just_created()
    formdata.store()
    formdata.perform_workflow()
    assert carddef.data_class().count() == 1
    assert carddef.data_class().select()[0].data.get('1') == {'lat': 2, 'lon': 3}


def test_create_carddata_with_date_field(pub):
    CardDef.wipe()
    FormDef.wipe()

    carddef = CardDef()
    carddef.name = 'My card'
    carddef.fields = [
        DateField(id='1', label='date', varname='date'),
    ]
    carddef.store()

    wf = Workflow(name='create-carddata')
    wf.possible_status = Workflow.get_default_workflow().possible_status[:]
    create = wf.possible_status[1].add_action('create_carddata', id='_create', prepend=True)
    create.action_label = 'Create CardDef'
    create.varname = 'mycard'
    create.formdef_slug = carddef.url_name
    create.mappings = [
        Mapping(field_id='1', expression=''),
    ]
    wf.store()

    formdef = FormDef()
    formdef.name = 'source form'
    formdef.fields = [
        DateField(id='1', label='date', varname='date'),
    ]
    formdef.workflow_id = wf.id
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {}
    formdata.just_created()
    assert carddef.data_class().count() == 0

    # empty value
    formdata = formdef.data_class()()
    formdata.data = {}
    formdata.just_created()
    formdata.store()
    formdata.perform_workflow()
    assert carddef.data_class().count() == 1
    assert not carddef.data_class().select()[0].data.get('1')

    # valid date
    create.mappings[0].expression = '2024-06-24'
    wf.store()
    formdef.refresh_from_storage()
    carddef.data_class().wipe()
    formdata = formdef.data_class()()
    formdata.data = {}
    formdata.just_created()
    formdata.store()
    formdata.perform_workflow()
    assert carddef.data_class().count() == 1
    assert carddef.data_class().select()[0].data.get('1')[:3] == (2024, 6, 24)

    # invalid value
    create.mappings[0].expression = 'plop'
    wf.store()
    formdef.refresh_from_storage()
    carddef.data_class().wipe()
    formdata = formdef.data_class()()
    formdata.data = {}
    formdata.just_created()
    formdata.store()
    formdata.perform_workflow()
    assert carddef.data_class().count() == 1
    assert not carddef.data_class().select()[0].data.get('1')

    errors = LoggedError.select()
    assert len(errors) == 1
    assert errors[0].summary == 'Could not assign value to field "date"'
    assert errors[0].exception_message == "invalid date value: 'plop'"

    # raw value from formdata
    create.mappings[0].expression = '{{ form_var_date_raw }}'
    wf.store()
    formdef.refresh_from_storage()
    carddef.data_class().wipe()
    formdata = formdef.data_class()()
    formdata.data = {'1': datetime.date(2024, 6, 24).timetuple()}
    formdata.just_created()
    formdata.store()
    formdata.perform_workflow()
    assert carddef.data_class().count() == 1
    assert carddef.data_class().select()[0].data.get('1')[:3] == (2024, 6, 24)


def test_create_carddata_with_computed_field(pub):
    CardDef.wipe()
    FormDef.wipe()

    carddef = CardDef()
    carddef.name = 'My card'
    carddef.fields = [
        ComputedField(id='1', label='data', varname='data'),
    ]
    carddef.store()

    wf = Workflow(name='create-carddata')
    wf.possible_status = Workflow.get_default_workflow().possible_status[:]
    create = wf.possible_status[1].add_action('create_carddata', id='_create', prepend=True)
    create.action_label = 'Create CardDef'
    create.varname = 'mycard'
    create.formdef_slug = carddef.url_name
    create.mappings = [
        Mapping(field_id='1', expression=''),
    ]
    wf.store()

    formdef = FormDef()
    formdef.name = 'source form'
    formdef.fields = [
        StringField(id='1', label='data', varname='data'),
    ]
    formdef.workflow_id = wf.id
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {}
    formdata.just_created()
    assert carddef.data_class().count() == 0

    # empty value
    formdata = formdef.data_class()()
    formdata.data = {}
    formdata.just_created()
    formdata.store()
    formdata.perform_workflow()
    assert carddef.data_class().count() == 1
    assert not carddef.data_class().select()[0].data.get('1')

    # content
    create.mappings[0].expression = 'hello'
    wf.store()
    formdef.refresh_from_storage()
    carddef.data_class().wipe()
    formdata = formdef.data_class()()
    formdata.data = {}
    formdata.just_created()
    formdata.store()
    formdata.perform_workflow()
    assert carddef.data_class().count() == 1
    assert carddef.data_class().select()[0].data.get('1') == 'hello'

    # value from formdata
    create.mappings[0].expression = '{{ form_var_data }}'
    wf.store()
    formdef.refresh_from_storage()
    carddef.data_class().wipe()
    formdata = formdef.data_class()()
    formdata.data = {'1': 'world'}
    formdata.just_created()
    formdata.store()
    formdata.perform_workflow()
    assert carddef.data_class().count() == 1
    assert carddef.data_class().select()[0].data.get('1') == 'world'


def test_create_carddata_user_association(pub):
    CardDef.wipe()
    FormDef.wipe()
    pub.user_class.wipe()

    user = pub.user_class()
    user.email = 'test@example.net'
    user.name_identifiers = ['xyz']
    user.store()
    user2 = pub.user_class()
    user2.store()

    carddef = CardDef()
    carddef.name = 'My card'
    carddef.fields = []
    carddef.user_support = 'optional'
    carddef.store()
    carddef.data_class().wipe()

    wf = Workflow(name='create-carddata')
    wf.possible_status = Workflow.get_default_workflow().possible_status[:]
    create = wf.possible_status[1].add_action('create_carddata', id='_create', prepend=True)
    create.action_label = 'Create CardDef'
    create.varname = 'mycard'
    create.formdef_slug = carddef.url_name
    create.map_fields_by_varname = True
    wf.store()

    formdef = FormDef()
    formdef.name = 'source form'
    formdef.fields = []
    formdef.workflow_id = wf.id
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {}
    formdata.user_id = user.id
    formdata.just_created()
    formdata.store()
    formdata.perform_workflow()

    assert carddef.data_class().count() == 1
    assert carddef.data_class().select()[0].user is None

    # keep user
    carddef.data_class().wipe()
    create.user_association_mode = 'keep-user'
    wf.store()

    formdata = FormDef.get(formdef.id).data_class()()
    formdata.data = {}
    formdata.user_id = user.id
    formdata.just_created()
    formdata.store()
    formdata.perform_workflow()

    assert carddef.data_class().count() == 1
    assert carddef.data_class().select()[0].user.id == user.id

    # user association on direct user
    carddef.data_class().wipe()
    create.user_association_mode = 'custom'
    create.user_association_template = '{{ form_user }}'
    wf.store()

    formdata = FormDef.get(formdef.id).data_class()()
    formdata.data = {}
    formdata.user_id = user.id
    formdata.just_created()
    formdata.store()
    formdata.perform_workflow()

    assert carddef.data_class().count() == 1
    assert carddef.data_class().select()[0].user.id == user.id

    # user association on user email
    carddef.data_class().wipe()
    create.user_association_mode = 'custom'
    create.user_association_template = 'test@example.net'
    wf.store()

    formdata = FormDef.get(formdef.id).data_class()()
    formdata.data = {}
    formdata.user_id = user2.id
    formdata.just_created()
    formdata.store()
    formdata.perform_workflow()

    assert carddef.data_class().count() == 1
    assert carddef.data_class().select()[0].user.id == user.id

    # user association on name id
    carddef.data_class().wipe()
    create.user_association_mode = 'custom'
    create.user_association_template = 'xyz'
    wf.store()

    formdata = FormDef.get(formdef.id).data_class()()
    formdata.data = {}
    formdata.user_id = user2.id
    formdata.just_created()
    formdata.store()
    formdata.perform_workflow()

    assert carddef.data_class().count() == 1
    assert carddef.data_class().select()[0].user.id == user.id

    # user association on invalid user
    for invalid_user in (('zzz', 'zzz'), ('{{ 42 }}', '42')):
        carddef.data_class().wipe()
        create.user_association_mode = 'custom'
        create.user_association_template = invalid_user[0]
        wf.store()

        formdata = FormDef.get(formdef.id).data_class()()
        formdata.data = {}
        formdata.user_id = user.id
        formdata.just_created()
        LoggedError.wipe()
        formdata.store()
        formdata.perform_workflow()

        assert carddef.data_class().count() == 1
        assert carddef.data_class().select()[0].user is None
        assert isinstance(formdata.evolution[1].parts[0], JournalAssignationErrorPart)
        assert formdata.evolution[1].parts[0].label == 'Create Card Data (My card)'
        assert (
            formdata.evolution[1].parts[0].summary
            == 'Failed to attach user (not found: "%s")' % invalid_user[1]
        )
        assert LoggedError.count() == 0  # no logged error

    # user association on invalid template
    carddef.data_class().wipe()
    create.user_association_mode = 'custom'
    create.user_association_template = '{% %}'
    wf.store()

    formdata = FormDef.get(formdef.id).data_class()()
    formdata.data = {}
    formdata.user_id = user.id
    formdata.just_created()
    formdata.store()
    formdata.perform_workflow()

    assert carddef.data_class().count() == 1
    assert carddef.data_class().select()[0].user is None


def test_create_carddata_map_fields_by_varname(pub):
    CardDef.wipe()
    FormDef.wipe()
    pub.user_class.wipe()

    carddef = CardDef()
    carddef.name = 'My card'
    carddef.fields = [
        StringField(id='3', label='string', varname='foo'),
        StringField(id='4', label='string', varname='bar'),
    ]
    carddef.store()
    carddef.data_class().wipe()

    wf = Workflow(name='create-carddata')
    wf.possible_status = Workflow.get_default_workflow().possible_status[:]
    create = wf.possible_status[1].add_action('create_carddata', id='_create', prepend=True)
    create.action_label = 'Create CardDef'
    create.varname = 'mycard'
    create.formdef_slug = carddef.url_name
    create.map_fields_by_varname = True
    wf.store()

    formdef = FormDef()
    formdef.name = 'source form'
    formdef.fields = [
        StringField(id='1', label='string', varname='foo'),
        StringField(id='2', label='string', varname='xxx'),
    ]
    formdef.workflow_id = wf.id
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {
        '1': 'test1',
        '2': 'test2',
    }
    formdata.just_created()
    formdata.store()
    formdata.perform_workflow()

    assert carddef.data_class().count() == 1
    carddata = carddef.data_class().select()[0]
    assert carddata.data.get('3') == 'test1'
    assert not carddata.data.get('4')


def test_create_carddata_partial_block_field(pub, admin_user):
    BlockDef.wipe()
    CardDef.wipe()

    block = BlockDef()
    block.name = 'foobar'
    block.digest_template = 'X{{foobar_var_foo}}Y'
    block.fields = [
        StringField(id='123', required='required', label='Test', varname='foo'),
        StringField(id='234', required='required', label='Test2', varname='bar'),
    ]
    block.store()

    carddef = CardDef()
    carddef.name = 'Foo Card'
    carddef.fields = [
        StringField(id='0', label='foo', varname='foo'),
        BlockField(id='1', label='block field', block_slug='foobar', varname='foobar'),
    ]
    carddef.store()
    carddef.data_class().wipe()

    card_wf = Workflow(name='Card workflow')
    st1 = card_wf.add_status('Status1')
    st2 = card_wf.add_status('Status2')

    choice = st1.add_action('choice', id='_x')
    choice.status = 'wf-%s' % st2.id

    create = st2.add_action('create_carddata', id='_create')
    create.formdef_slug = carddef.url_name
    create.mappings = [
        Mapping(field_id='0', expression='new value'),
        Mapping(field_id='1', expression='{{ form_var_foobar }}'),
        Mapping(field_id='1$123', expression='new subfield value'),
    ]
    card_wf.store()
    carddef.workflow = card_wf
    carddef.store()

    # execute on card
    carddata = carddef.data_class()()
    carddata.data = {
        '0': 'foo',
        '1': {
            'data': [{'123': 'foo', '234': 'bar'}],
            'schema': {'123': 'string', '234': 'string'},
        },
        '1_display': 'XfooY',
    }
    carddata.store()
    carddata.just_created()
    carddata.jump_status(st2.id)
    carddata.store()
    LoggedError.wipe()
    carddata.perform_workflow()

    # check there were no errors
    assert LoggedError.count() == 0

    # check current carddata has not been changed
    carddata.refresh_from_storage()
    assert carddata.data == {
        '0': 'foo',
        '1': {
            'data': [{'123': 'foo', '234': 'bar'}],
            'schema': {'123': 'string', '234': 'string'},
        },
        '1_display': 'XfooY',
    }

    # check new carddata
    assert carddef.data_class().count() == 2
    new_carddata = carddef.data_class().select(order_by='-id')[0]
    assert new_carddata.data == {
        '0': 'new value',
        '1': {
            'data': [{'123': 'new subfield value', '234': 'bar'}],
            'digests': ['Xnew subfield valueY'],
            'schema': {'123': 'string', '234': 'string'},
        },
        '1_display': 'Xnew subfield valueY',
    }


def test_edit_carddata_with_data_sourced_object(pub):
    FormDef.wipe()
    CardDef.wipe()

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
    carddef = CardDef()
    carddef.name = 'Person'
    carddef.fields = [
        StringField(id='0', label='First Name', varname='first_name'),
        StringField(id='1', label='Last Name', varname='last_name'),
        ItemField(id='2', label='Profession', varname='profession', data_source=datasource),
    ]
    carddef.digest_templates = {'default': '{{ form_var_first_name }} {{ form_var_last_name }}'}
    carddef.store()
    carddef.data_class().wipe()

    carddata = carddef.data_class()()
    carddata.data = {'0': 'Foo', '1': 'Bar', '2': 'l'}
    carddata.data['2_display'] = carddef.fields[2].store_display_value(carddata.data, '2')
    carddata.data['2_structured'] = carddef.fields[2].store_structured_value(carddata.data, '2')
    carddata.just_created()
    carddata.store()

    wf = Workflow(name='Card update')
    st1 = wf.add_status('Update card', 'st1')

    edit = st1.add_action('edit_carddata', id='edit')
    edit.formdef_slug = carddef.url_name
    edit.mappings = [
        Mapping(field_id='2', expression='{{ form_var_new_profession }}'),
    ]
    wf.store()

    datasource = {'type': 'carddef:%s' % carddef.url_name}
    formdef = FormDef()
    formdef.name = 'Persons'
    formdef.fields = [
        ItemField(id='0', label='Person', varname='person', data_source=datasource),
        StringField(id='1', label='New profession', varname='new_profession'),
    ]
    formdef.workflow = wf
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {'0': '1', '1': 'c'}
    formdata.just_created()
    formdata.store()
    formdata.perform_workflow()

    data = carddef.data_class().select()[0]
    assert data.data['2'] == 'c'
    assert data.data['2_display'] == 'cook'
    assert data.data['2_structured'] == {'id': 'c', 'text': 'cook', 'extra': 'plop2'}
    # check evolutions & tracing
    trace = WorkflowTrace.select_for_formdata(carddata)[0]
    assert trace.event == 'workflow-edited'
    assert trace.event_args['external_workflow_id'] == wf.id
    assert trace.event_args['external_status_id'] == 'st1'
    assert trace.event_args['external_item_id'] == 'edit'
    trace = WorkflowTrace.select_for_formdata(formdata)[-1]
    assert trace.event == 'workflow-edited-carddata'
    assert trace.event_args['external_formdef_id'] == carddef.id
    assert trace.event_args['external_formdata_id'] == carddata.id

    formdata = formdef.data_class()()
    formdata.data = {'0': '1', '1': 'b'}
    formdata.store()
    formdata.just_created()
    formdata.store()
    formdata.perform_workflow()

    data = carddef.data_class().select()[0]
    assert data.data['2'] == 'b'
    assert data.data['2_display'] == 'baker'
    assert data.data['2_structured'] == {'id': 'b', 'text': 'baker', 'extra': 'plop'}

    # reset data
    for expression in ('', '""'):
        edit.mappings = [
            Mapping(field_id='2', expression=expression),
        ]
        wf.store()

        formdata = formdef.data_class()()
        formdata.data = {'0': '1', '1': 'b'}
        formdata.store()
        formdata.just_created()
        formdata.store()
        formdata.perform_workflow()

        carddata = carddef.data_class().select()[0]
        assert carddata.data['2'] in [None, '', '""']
        assert carddata.data.get('2_display') is None
        assert carddata.data.get('2_structured') is None

        # restore initial data
        carddata.data = data.data
        carddata.store()

    # not target found
    edit.mappings = [
        Mapping(field_id='2', expression='{{ form_var_new_profession }}'),
    ]
    edit.target_mode = 'manual'
    edit.target_id = '{{ unknown }}'
    wf.store()
    formdata = formdef.data_class()()
    formdata.data = {'0': '1', '1': 'c'}
    formdata.store()
    formdata.just_created()
    formdata.store()
    formdata.perform_workflow()
    # no change
    data = carddef.data_class().select()[0]
    assert data.data['2'] == 'b'
    assert data.data['2_display'] == 'baker'
    assert data.data['2_structured'] == {'id': 'b', 'text': 'baker', 'extra': 'plop'}
    trace = WorkflowTrace.select_for_formdata(formdata)[-1]
    assert trace.event == 'workflow-edited-carddata'
    assert trace.event_args == {}


def test_edit_carddata_with_linked_object(pub):
    FormDef.wipe()
    CardDef.wipe()

    carddef = CardDef()
    carddef.name = 'Parent'
    carddef.fields = [
        StringField(id='0', label='First Name', varname='first_name'),
        StringField(id='1', label='Last Name', varname='last_name'),
        StringField(id='2', label='Kids number', varname='kids_number'),
    ]
    carddef.store()
    carddef.data_class().wipe()

    wf = Workflow(name='Card create and update')
    st1 = wf.add_status('Create card', 'st1')
    create = st1.add_action('create_carddata')
    create.formdef_slug = carddef.url_name
    create.mappings = [
        Mapping(field_id='0', expression='{{ form_var_first_name }}'),
        Mapping(field_id='1', expression='{{ form_var_last_name }}'),
        Mapping(field_id='2', expression='{{ form_var_kids_number|default:"0" }}'),
    ]
    jump = st1.add_action('jump', id='_jump')
    jump.by = ['_submitter', '_receiver']
    jump.status = 'st2'

    st2 = wf.add_status('Update card', 'st2')
    edit = st2.add_action('edit_carddata', id='edit')
    edit.formdef_slug = carddef.url_name
    edit.mappings = [
        Mapping(field_id='2', expression='{{ form_var_kids_number|add:"1" }}'),
    ]
    wf.store()

    formdef = FormDef()
    formdef.name = 'Parents'
    formdef.fields = [
        StringField(id='0', label='First Name', varname='first_name'),
        StringField(id='1', label='Last Name', varname='last_name'),
        StringField(id='2', label='Number of kids', varname='kids_number'),
    ]
    formdef.workflow = wf
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {'0': 'Parent', '1': 'Foo', '2': '2'}
    formdata.store()
    formdata.just_created()
    formdata.store()
    formdata.perform_workflow()

    assert carddef.data_class().count() == 1
    card_data = carddef.data_class().select()[0]
    assert card_data.data['2'] == '3'


def test_edit_carddata_manual_targeting(pub):
    FormDef.wipe()
    CardDef.wipe()

    # carddef
    carddef = CardDef()
    carddef.name = 'Parent'
    carddef.fields = [
        StringField(id='0', label='First Name', varname='first_name'),
        StringField(id='1', label='Last Name', varname='last_name'),
        StringField(id='2', label='Kids number', varname='kids_number'),
    ]
    carddef.store()
    carddef.data_class().wipe()

    # and sample carddatas
    for i in range(1, 4):
        carddata = carddef.data_class()()
        carddata.data = {
            '0': 'First name %s' % i,
            '1': 'Last name %s' % i,
            '2': '0',
        }
        carddata.just_created()
        carddata.store()

    # formdef workflow that will update carddata
    wf = Workflow(name='Card create and update')
    st1 = wf.add_status('Create card', 'st1')
    # create linked carddata
    edit = st1.add_action('create_carddata')
    edit.formdef_slug = carddef.url_name
    edit.mappings = [
        Mapping(field_id='0', expression='{{ form_var_first_name }}'),
        Mapping(field_id='1', expression='{{ form_var_last_name }}'),
        Mapping(field_id='2', expression='{{ form_var_kids_number|default:"0" }}'),
    ]
    jump = st1.add_action('jump', id='_jump')
    jump.by = ['_submitter', '_receiver']
    jump.status = 'st2'

    st2 = wf.add_status('Update card', 'st2')
    edit = st2.add_action('edit_carddata', id='edit')
    edit.formdef_slug = carddef.url_name
    edit.target_mode = 'manual'  # not configured
    edit.mappings = [
        Mapping(field_id='2', expression='{{ form_var_kids_number|add:"1" }}'),
    ]
    wf.store()

    # associated formdef
    formdef = FormDef()
    formdef.name = 'Parents'
    datasource = {'type': 'carddef:%s' % carddef.url_name}
    formdef.fields = [
        StringField(id='0', label='First Name', varname='first_name'),
        StringField(id='1', label='Last Name', varname='last_name'),
        StringField(id='2', label='Number of kids', varname='kids_number'),
        ItemField(id='3', label='Card', varname='card', data_source=datasource),
        StringField(id='4', label='string', varname='string'),
    ]
    formdef.workflow = wf
    formdef.store()

    # create formdatas

    # target not configured
    formdata = formdef.data_class()()
    formdata.data = {
        '0': 'Parent',
        '1': 'Foo',
        '2': '2',
        '3': '3',  # set from datasource
        '4': '1',
    }
    # set parent
    formdata.submission_context = {
        'orig_object_type': 'carddef',
        'orig_formdata_id': '2',
        'orig_formdef_id': str(carddef.id),
    }
    formdata.store()
    formdata.just_created()
    formdata.store()
    formdata.perform_workflow()

    assert carddef.data_class().count() == 4
    assert carddef.data_class().get(1).data['2'] == '0'
    assert carddef.data_class().get(2).data['2'] == '0'
    assert carddef.data_class().get(3).data['2'] == '0'
    assert carddef.data_class().get(4).data['2'] == '2'
    assert LoggedError.count() == 0

    # configure target
    edit.target_id = '{{ form_var_string }}'  # == '1'
    wf.store()
    formdata = formdef.data_class()()
    formdata.data = {
        '0': 'Parent',
        '1': 'Foo',
        '2': '2',
        '3': '3',  # set from datasource
        '4': '1',
    }
    # set parent
    formdata.submission_context = {
        'orig_object_type': 'carddef',
        'orig_formdata_id': '2',
        'orig_formdef_id': str(carddef.id),
    }
    formdata.store()
    formdata.just_created()
    formdata.store()
    formdata.perform_workflow()
    assert carddef.data_class().count() == 5
    assert carddef.data_class().get(1).data['2'] == '3'  # 2 + 1
    assert carddef.data_class().get(2).data['2'] == '0'
    assert carddef.data_class().get(3).data['2'] == '0'
    assert carddef.data_class().get(4).data['2'] == '2'
    assert carddef.data_class().get(5).data['2'] == '2'
    assert LoggedError.count() == 0

    # target not found
    edit.target_id = '42{{ form_var_string }}'  # == '421'
    wf.store()
    formdata = formdef.data_class()()
    formdata.data = {
        '0': 'Parent',
        '1': 'Foo',
        '2': '2',
        '3': '3',  # set from datasource
        '4': '1',
    }
    # set parent
    formdata.submission_context = {
        'orig_object_type': 'carddef',
        'orig_formdata_id': '2',
        'orig_formdef_id': str(carddef.id),
    }
    formdata.store()
    formdata.just_created()
    formdata.store()
    formdata.perform_workflow()
    assert carddef.data_class().count() == 6
    assert carddef.data_class().get(1).data['2'] == '3'  # not changed
    assert carddef.data_class().get(2).data['2'] == '0'
    assert carddef.data_class().get(3).data['2'] == '0'
    assert carddef.data_class().get(4).data['2'] == '2'
    assert carddef.data_class().get(5).data['2'] == '2'
    assert carddef.data_class().get(6).data['2'] == '2'
    assert LoggedError.count() == 1
    logged_error = LoggedError.select()[0]
    assert logged_error.summary == 'Could not find targeted "Parent" object by id 421'

    # slug not or badly configured
    edit.target_id = '{{ form_var_string }}'  # == '1'
    edit.formdef_slug = None
    wf.store()
    formdata = formdef.data_class()()
    formdata.data = {
        '0': 'Parent',
        '1': 'Foo',
        '2': '3',
        '3': '3',  # set from datasource
        '4': '1',
    }
    # set parent
    formdata.submission_context = {
        'orig_object_type': 'carddef',
        'orig_formdata_id': '2',
        'orig_formdef_id': str(carddef.id),
    }
    formdata.store()
    formdata.just_created()
    formdata.store()
    formdata.perform_workflow()
    assert carddef.data_class().count() == 7
    assert carddef.data_class().get(1).data['2'] == '3'  # not changed
    assert carddef.data_class().get(2).data['2'] == '0'
    assert carddef.data_class().get(3).data['2'] == '0'
    assert carddef.data_class().get(4).data['2'] == '2'
    assert carddef.data_class().get(5).data['2'] == '2'
    assert carddef.data_class().get(6).data['2'] == '2'
    assert carddef.data_class().get(7).data['2'] == '3'


def test_edit_carddata_targeting_itself(pub):
    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'Foo Card'
    carddef.fields = [
        StringField(id='0', label='foo', varname='foo'),
    ]
    carddef.store()
    carddef.data_class().wipe()

    # card workflow: update itself then jump to second status
    card_wf = Workflow(name='Card workflow')
    st1 = card_wf.add_status('Status1')
    st2 = card_wf.add_status('Status2')

    edit = st1.add_action('edit_carddata', id='_edit')
    edit.formdef_slug = carddef.url_name
    edit.target_mode = 'manual'
    edit.target_id = '{{ form_internal_id }}'  # itself
    edit.mappings = [
        Mapping(field_id='0', expression='bar {{ form_internal_id }}'),
    ]

    jump = st1.add_action('jump', '_jump')
    jump.status = st2.id

    card_wf.store()

    carddef.workflow = card_wf
    carddef.store()

    # create some cardata
    for i in range(1, 4):
        carddata = carddef.data_class()()
        carddata.data = {
            '0': 'foo %s' % i,
        }
        carddata.store()
        # run workflow, verify that carddata is modified
        carddata.just_created()
        carddata.store()
        carddata.perform_workflow()
        assert carddata.data['0'] == 'bar %s' % carddata.id
        assert carddata.status == 'wf-%s' % st2.id


def test_edit_carddata_targeting_itself_no_history(pub):
    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'Foo Card'
    carddef.fields = [
        StringField(id='0', label='foo', varname='foo'),
    ]
    carddef.store()
    carddef.data_class().wipe()

    # card workflow: update itself then jump to second status
    card_wf = Workflow(name='Card workflow')
    st1 = card_wf.add_status('Status1')

    edit = st1.add_action('edit_carddata', id='_edit')
    edit.formdef_slug = carddef.url_name
    edit.target_mode = 'manual'
    edit.target_id = '{{ form_internal_id }}'  # itself
    edit.mappings = [
        Mapping(field_id='0', expression='bar {{ form_internal_id }}'),
    ]

    card_wf.store()

    carddef.workflow = card_wf
    carddef.store()

    carddata = carddef.data_class()()
    carddata.data = {
        '0': 'foo',
    }
    carddata.store()
    # run workflow, verify that carddata is modified
    carddata.just_created()
    carddata.evolution = []  # empty history
    carddata.store()
    carddata.perform_workflow()
    assert carddata.data['0'] == 'bar %s' % carddata.id


def test_edit_carddata_auto_targeting_custom_id(pub):
    CardDef.wipe()

    carddef = CardDef()
    carddef.name = 'Foo Card'
    carddef.fields = [
        StringField(id='0', label='foo', varname='foo'),
        StringField(id='1', label='slug', varname='slug'),
    ]
    carddef.id_template = 'card_{{form_var_slug}}'
    carddef.store()
    carddef.data_class().wipe()

    carddata = carddef.data_class()()
    carddata.data = {'0': 'foo', '1': 'foo'}
    carddata.store()
    carddata.just_created()
    carddata.store()
    assert carddata.identifier == 'card_foo'

    carddef2 = CardDef()
    carddef2.name = 'Bar Card'
    carddef2.fields = [
        ItemField(id='1', label='card', varname='card', data_source={'type': 'carddef:%s' % carddef.url_name})
    ]
    carddef2.store()

    card_wf = Workflow(name='Card workflow')
    st1 = card_wf.add_status('Status1')
    st2 = card_wf.add_status('Status2')

    edit = st1.add_action('edit_carddata', id='_edit')
    edit.formdef_slug = carddef.url_name
    edit.target_mode = 'all'
    edit.mappings = [Mapping(field_id='0', expression='bar')]

    jump = st1.add_action('jump', '_jump')
    jump.status = st2.id

    card_wf.store()

    carddef2.workflow = card_wf
    carddef2.store()

    carddata2 = carddef2.data_class()()
    carddata2.data = {
        '1': 'card_foo',
    }
    carddata2.store()
    carddata2.just_created()
    carddata2.store()
    carddata2.perform_workflow()

    carddata.refresh_from_storage()
    assert carddata.data['0'] == 'bar'


def test_edit_carddata_manual_targeting_custom_id(pub):
    CardDef.wipe()

    carddef = CardDef()
    carddef.name = 'Foo Card'
    carddef.fields = [
        StringField(id='0', label='foo', varname='foo'),
        StringField(id='1', label='slug', varname='slug'),
    ]
    carddef.id_template = 'card_{{form_var_slug}}'
    carddef.store()
    carddef.data_class().wipe()

    carddata = carddef.data_class()()
    carddata.data = {'0': 'foo', '1': 'foo'}
    carddata.store()
    carddata.just_created()
    carddata.store()
    assert carddata.identifier == 'card_foo'

    carddef2 = CardDef()
    carddef2.name = 'Bar Card'
    carddef2.fields = []
    carddef2.store()

    card_wf = Workflow(name='Card workflow')
    st1 = card_wf.add_status('Status1')
    st2 = card_wf.add_status('Status2')

    edit = st1.add_action('edit_carddata', id='_edit')
    edit.formdef_slug = carddef.url_name
    edit.target_mode = 'manual'
    edit.target_id = 'card_foo'
    edit.mappings = [Mapping(field_id='0', expression='bar')]

    jump = st1.add_action('jump', '_jump')
    jump.status = st2.id

    card_wf.store()

    carddef2.workflow = card_wf
    carddef2.store()

    carddata2 = carddef2.data_class()()
    carddata2.data = {}
    carddata2.store()
    carddata2.just_created()
    carddata2.store()
    carddata2.perform_workflow()

    carddata.refresh_from_storage()
    assert carddata.data['0'] == 'bar'


def test_edit_carddata_from_created_object(pub):
    FormDef.wipe()
    CardDef.wipe()

    carddef = CardDef()
    carddef.name = 'Card'
    carddef.fields = [
        StringField(id='0', label='Card Field', varname='card_field'),
    ]
    carddef.store()

    formdef = FormDef()
    formdef.name = 'Form'
    formdef.fields = [
        StringField(id='0', label='Form Field', varname='form_field'),
    ]
    formdef.store()

    # card workflow: create formdata then jump to second status
    card_wf = Workflow(name='Card workflow')
    st1 = card_wf.add_status('Status1')
    st2 = card_wf.add_status('Status2')

    create = st1.add_action('create_formdata', id='_create')
    create.formdef_slug = formdef.url_name
    create.mappings = [
        Mapping(field_id='0', expression='...'),
    ]

    jump = st1.add_action('jump', id='_jump')
    jump.status = st2.id

    # form workflow: edit parent card data
    form_wf = Workflow(name='Form workflow')
    st1 = form_wf.add_status('Status1')
    edit = st1.add_action('edit_carddata', id='edit')
    edit.formdef_slug = carddef.url_name
    edit.mappings = [
        Mapping(field_id='0', expression='HELLO'),
    ]
    form_wf.store()

    carddef.workflow = card_wf
    carddef.store()

    formdef.workflow = form_wf
    formdef.store()

    carddata = carddef.data_class()()
    carddata.data = {'0': 'Foo'}
    carddata.store()
    carddata.just_created()
    carddata.store()
    carddata.perform_workflow()

    carddata_reloaded = carddata.get(carddata.id)
    assert carddata_reloaded.data['0'] == 'HELLO'
    assert carddata_reloaded.status == 'wf-2'


def test_edit_carddata_invalid_file_field(pub):
    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'Foo Card'
    carddef.fields = [
        StringField(id='0', label='foo', varname='foo'),
        FileField(id='4', label='File', varname='file'),
    ]
    carddef.store()
    carddef.data_class().wipe()

    card_wf = Workflow(name='Card workflow')
    st1 = card_wf.add_status('Status1')

    edit = st1.add_action('edit_carddata', id='_edit')
    edit.formdef_slug = carddef.url_name
    edit.target_mode = 'manual'
    edit.target_id = '{{ form_internal_id }}'  # itself
    edit.mappings = [
        Mapping(field_id='0', expression='new value'),
        Mapping(field_id='4', expression='{{ form_objects|getlist:"foo" }}'),
    ]

    card_wf.store()

    carddef.workflow = card_wf
    carddef.store()

    carddata = carddef.data_class()()
    carddata.data = {'0': 'foo'}
    carddata.store()
    carddata.just_created()
    carddata.store()
    LoggedError.wipe()
    carddata.perform_workflow()
    assert LoggedError.count() == 1
    assert LoggedError.select()[0].summary == 'Could not assign value to field "File"'
    carddata.refresh_from_storage()
    assert carddata.data == {'0': 'new value', '4': None}


def test_edit_carddata_partial_block_field(pub, admin_user):
    BlockDef.wipe()
    CardDef.wipe()

    block = BlockDef()
    block.name = 'foobar'
    block.digest_template = 'X{{foobar_var_foo}}Y'
    block.fields = [
        StringField(id='123', required='required', label='Test', varname='foo'),
        StringField(id='234', required='required', label='Test2', varname='bar'),
    ]
    block.store()

    carddef = CardDef()
    carddef.name = 'Foo Card'
    carddef.fields = [
        StringField(id='0', label='foo', varname='foo'),
        BlockField(id='1', label='block field', block_slug='foobar'),
    ]
    carddef.store()
    carddef.data_class().wipe()

    card_wf = Workflow(name='Card workflow')
    st1 = card_wf.add_status('Status1')

    edit = st1.add_action('edit_carddata', id='_edit')
    edit.formdef_slug = carddef.url_name
    edit.target_mode = 'manual'
    edit.target_id = '{{ form_internal_id }}'  # itself
    edit.mappings = [
        Mapping(field_id='0', expression='new value'),
        Mapping(field_id='1$123', expression='new subfield value'),
        Mapping(field_id='1$234', expression=None),
    ]
    card_wf.store()
    carddef.workflow = card_wf
    carddef.store()

    # check action form
    resp = login(get_app(pub), username='admin', password='admin').get(edit.get_admin_url())
    assert resp.form['mappings$element1$field_id'].options == [
        ('', False, '---'),
        ('0', False, 'foo - Text (line)'),
        ('1', False, 'block field - Block of fields (foobar)'),
        ('1$123', True, 'block field - Test - Text (line)'),
        ('1$234', False, 'block field - Test2 - Text (line)'),
    ]
    resp = resp.form.submit('submit')

    # execute on card
    carddata = carddef.data_class()()
    carddata.data = {
        '0': 'foo',
        '1': {
            'data': [{'123': 'foo', '234': 'bar'}],
            'schema': {'123': 'string', '234': 'string'},
        },
        '1_display': 'XfooY',
    }
    carddata.store()
    carddata.just_created()
    carddata.store()
    LoggedError.wipe()
    carddata.perform_workflow()
    assert carddata.data == {
        '0': 'new value',
        '1': {
            'data': [{'123': 'new subfield value', '234': None}],
            'digests': ['Xnew subfield valueY'],
            'schema': {'123': 'string', '234': 'string'},
        },
        '1_display': 'Xnew subfield valueY',
    }

    # execute on card with multiple block rows
    carddata = carddef.data_class()()
    carddata.data = {
        '0': 'foo',
        '1': {
            'data': [{'123': 'foo', '234': 'bar'}, {'123': 'foo2', '234': 'bar2'}],
            'digests': ['XfooY', 'Xfoo2Y'],
            'schema': {'123': 'string', '234': 'string'},
        },
        '1_display': 'XfooY, Xfoo2Y',
    }
    carddata.store()
    carddata.just_created()
    carddata.store()
    LoggedError.wipe()
    carddata.perform_workflow()
    assert carddata.data == {
        '0': 'new value',
        '1': {
            'data': [{'123': 'new subfield value', '234': None}, {'123': 'new subfield value', '234': None}],
            'digests': ['Xnew subfield valueY', 'Xnew subfield valueY'],
            'schema': {'123': 'string', '234': 'string'},
        },
        '1_display': 'Xnew subfield valueY, Xnew subfield valueY',
    }

    # execute on card without any block data
    carddata = carddef.data_class()()
    carddata.data = {'0': 'foo'}
    carddata.store()
    carddata.just_created()
    carddata.store()
    LoggedError.wipe()
    carddata.perform_workflow()
    assert carddata.data == {
        '0': 'new value',
        '1': {
            'data': [{'123': 'new subfield value', '234': None}],
            'digests': ['Xnew subfield valueY'],
            'schema': {'123': 'string', '234': 'string'},
        },
        '1_display': 'Xnew subfield valueY',
    }


def test_edit_carddata_partial_block_date_field(pub):
    BlockDef.wipe()
    CardDef.wipe()

    block = BlockDef()
    block.name = 'foobar'
    block.digest_template = 'X{{foobar_var_foo}}Y'
    block.fields = [
        DateField(id='123', required='required', label='Test', varname='foo'),
    ]
    block.store()

    carddef = CardDef()
    carddef.name = 'Foo Card'
    carddef.fields = [
        DateField(id='0', label='date field', varname='date'),
        BlockField(id='1', label='block field', block_slug='foobar'),
    ]
    carddef.store()
    carddef.data_class().wipe()

    card_wf = Workflow(name='Card workflow')
    st1 = card_wf.add_status('Status1')

    edit = st1.add_action('edit_carddata', id='_edit')
    edit.formdef_slug = carddef.url_name
    edit.target_mode = 'manual'
    edit.target_id = '{{ form_internal_id }}'  # itself
    card_wf.store()
    carddef.workflow = card_wf
    carddef.store()

    for date_template in ('2024-10-19', '{{ "2024-10-19"|date }}', '{{ form_var_date }}'):
        edit.mappings = [Mapping(field_id='1$123', expression=date_template)]
        card_wf.store()
        carddef.refresh_from_storage()

        # execute on card
        carddata = carddef.data_class()()
        carddata.data = {'0': datetime.datetime(2024, 10, 19).timetuple()}
        carddata.store()
        carddata.just_created()
        carddata.store()
        LoggedError.wipe()
        carddata.perform_workflow()
        assert carddata.data == {
            '0': datetime.datetime(2024, 10, 19).timetuple(),
            '1': {
                'data': [{'123': datetime.datetime(2024, 10, 19).timetuple()}],
                'digests': ['X2024-10-19Y'],
                'schema': {'123': 'date'},
            },
            '1_display': 'X2024-10-19Y',
        }
        assert LoggedError.count() == 0


def test_edit_carddata_invalid_block_field(pub):
    BlockDef.wipe()
    CardDef.wipe()

    block = BlockDef()
    block.name = 'foobar'
    block.digest_template = 'X{{foobar_var_foo}}Y'
    block.fields = [
        StringField(id='123', required='required', label='Test', varname='foo'),
        StringField(id='234', required='required', label='Test2', varname='bar'),
    ]
    block.store()

    carddef = CardDef()
    carddef.name = 'Foo Card'
    carddef.fields = [
        StringField(id='0', label='foo', varname='foo'),
        BlockField(id='1', label='block field', block_slug='foobar'),
    ]
    carddef.store()
    carddef.data_class().wipe()

    card_wf = Workflow(name='Card workflow')
    st1 = card_wf.add_status('Status1')

    edit = st1.add_action('edit_carddata', id='_edit')
    edit.formdef_slug = carddef.url_name
    edit.target_mode = 'manual'
    edit.target_id = '{{ form_internal_id }}'  # itself
    edit.mappings = [
        Mapping(field_id='0', expression='new value'),
        Mapping(field_id='1', expression='{{ form_objects|getlist:"foo" }}'),
    ]

    card_wf.store()

    carddef.workflow = card_wf
    carddef.store()

    carddata = carddef.data_class()()
    carddata.data = {'0': 'foo'}
    carddata.store()
    carddata.just_created()
    carddata.store()
    LoggedError.wipe()
    carddata.perform_workflow()
    assert LoggedError.count() == 1
    assert LoggedError.select()[0].summary == 'Could not assign value to field "block field"'
    carddata.refresh_from_storage()
    assert carddata.data == {'0': 'new value', '1': None, '1_display': None}


def test_assign_carddata_with_data_sourced_object(pub):
    FormDef.wipe()
    CardDef.wipe()
    pub.user_class.wipe()

    user = pub.user_class()
    user.email = 'test@example.net'
    user.name_identifiers = ['xyz']
    user.store()

    carddef = CardDef()
    carddef.name = 'Person'
    carddef.fields = [
        StringField(id='0', label='First Name', varname='first_name'),
        StringField(id='1', label='Last Name', varname='last_name'),
    ]
    carddef.digest_templates = {'default': '{{ form_var_first_name }} {{ form_var_last_name }}'}
    carddef.store()
    carddef.data_class().wipe()

    carddata = carddef.data_class()()
    carddata.data = {'0': 'Foo', '1': 'Bar'}
    carddata.store()

    wf = Workflow(name='Card update')
    st1 = wf.add_status('Assign card', 'st1')

    assign = st1.add_action('assign_carddata', id='assign')
    assign.formdef_slug = carddef.url_name
    assign.user_association_mode = 'keep-user'
    wf.store()

    datasource = {'type': 'carddef:%s' % carddef.url_name}
    formdef = FormDef()
    formdef.name = 'Persons'
    formdef.fields = [
        ItemField(id='0', label='Person', varname='person', data_source=datasource),
    ]
    formdef.workflow = wf
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {'0': '1'}
    formdata.user_id = user.id
    formdata.store()
    formdata.just_created()
    formdata.store()
    formdata.perform_workflow()

    data = carddef.data_class().select()[0]
    assert str(data.user_id) == str(user.id)


def test_assign_carddata_with_linked_object(pub):
    FormDef.wipe()
    CardDef.wipe()
    pub.user_class.wipe()

    user = pub.user_class()
    user.email = 'test@example.net'
    user.name_identifiers = ['xyz']
    user.store()

    carddef = CardDef()
    carddef.name = 'Parent'
    carddef.fields = [
        StringField(id='0', label='First Name', varname='first_name'),
        StringField(id='1', label='Last Name', varname='last_name'),
    ]
    carddef.store()
    carddef.data_class().wipe()

    wf = Workflow(name='Card create and assign')
    st1 = wf.add_status('Create card', 'st1')
    create = st1.add_action('create_carddata')
    create.formdef_slug = carddef.url_name
    create.user_association_mode = None
    create.mappings = [
        Mapping(field_id='0', expression='{{ form_var_first_name }}'),
        Mapping(field_id='1', expression='{{ form_var_last_name }}'),
    ]
    jump = st1.add_action('jump', id='_jump')
    jump.by = ['_submitter', '_receiver']
    jump.status = 'st2'

    st2 = wf.add_status('Assign card', 'st2')
    assign = st2.add_action('assign_carddata', id='assign')
    assign.formdef_slug = carddef.url_name
    assign.user_association_mode = 'keep-user'
    wf.store()

    formdef = FormDef()
    formdef.name = 'Parents'
    formdef.fields = [
        StringField(id='0', label='First Name', varname='first_name'),
        StringField(id='1', label='Last Name', varname='last_name'),
    ]
    formdef.workflow = wf
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {'0': 'Parent', '1': 'Foo'}
    formdata.user_id = user.id
    formdata.store()
    formdata.just_created()
    formdata.store()
    formdata.perform_workflow()

    assert carddef.data_class().count() == 1
    card_data = carddef.data_class().select()[0]
    assert str(card_data.user_id) == str(user.id)


def test_assign_carddata_manual_targeting(pub):
    FormDef.wipe()
    CardDef.wipe()
    pub.user_class.wipe()

    user = pub.user_class()
    user.email = 'test@example.net'
    user.name_identifiers = ['xyz']
    user.store()

    # carddef
    carddef = CardDef()
    carddef.name = 'Parent'
    carddef.fields = [
        StringField(id='0', label='First Name', varname='first_name'),
        StringField(id='1', label='Last Name', varname='last_name'),
    ]
    carddef.store()
    carddef.data_class().wipe()

    # and sample carddatas
    for i in range(1, 4):
        carddata = carddef.data_class()()
        carddata.data = {
            '0': 'First name %s' % i,
            '1': 'Last name %s' % i,
        }
        carddata.store()

    # formdef workflow that will assign carddata
    wf = Workflow(name='Card create and Assign')
    st1 = wf.add_status('Create card', 'st1')
    # create linked carddata
    create = st1.add_action('create_carddata')
    create.formdef_slug = carddef.url_name
    create.user_association_mode = None
    create.mappings = [
        Mapping(field_id='0', expression='{{ form_var_first_name }}'),
        Mapping(field_id='1', expression='{{ form_var_last_name }}'),
    ]
    jump = st1.add_action('jump', id='_jump')
    jump.by = ['_submitter', '_receiver']
    jump.status = 'st2'

    st2 = wf.add_status('Assign card', 'st2')
    assign = st2.add_action('assign_carddata', id='assign')
    assign.formdef_slug = carddef.url_name
    assign.target_mode = 'manual'  # not configured
    assign.user_association_mode = 'keep-user'
    wf.store()

    # associated formdef
    formdef = FormDef()
    formdef.name = 'Parents'
    datasource = {'type': 'carddef:%s' % carddef.url_name}
    formdef.fields = [
        StringField(id='0', label='First Name', varname='first_name'),
        StringField(id='1', label='Last Name', varname='last_name'),
        StringField(id='2', label='string', varname='string'),
        ItemField(id='3', label='Card', varname='card', data_source=datasource),
    ]
    formdef.workflow = wf
    formdef.store()

    # create formdatas

    # target not configured
    formdata = formdef.data_class()()
    formdata.data = {
        '0': 'Parent',
        '1': 'Foo',
        '2': '1',
        '3': '3',  # set from datasource
    }
    # set parent
    formdata.submission_context = {
        'orig_object_type': 'carddef',
        'orig_formdata_id': '2',
        'orig_formdef_id': str(carddef.id),
    }
    formdata.user_id = user.id
    formdata.store()
    formdata.just_created()
    formdata.store()
    formdata.perform_workflow()

    assert carddef.data_class().count() == 4
    assert carddef.data_class().get(1).user_id is None
    assert carddef.data_class().get(2).user_id is None
    assert carddef.data_class().get(3).user_id is None
    assert carddef.data_class().get(4).user_id is None
    assert LoggedError.count() == 0

    # configure target
    assign.target_id = '{{ form_var_string }}'  # == '1'
    wf.store()
    formdata = formdef.data_class()()
    formdata.data = {
        '0': 'Parent',
        '1': 'Foo',
        '2': '1',
        '3': '3',  # set from datasource
    }
    # set parent
    formdata.submission_context = {
        'orig_object_type': 'carddef',
        'orig_formdata_id': '2',
        'orig_formdef_id': str(carddef.id),
    }
    formdata.user_id = user.id
    formdata.store()
    formdata.just_created()
    formdata.store()
    formdata.perform_workflow()
    assert carddef.data_class().count() == 5
    assert carddef.data_class().get(1).user_id == str(user.id)
    assert carddef.data_class().get(2).user_id is None
    assert carddef.data_class().get(3).user_id is None
    assert carddef.data_class().get(4).user_id is None
    assert carddef.data_class().get(5).user_id is None
    assert LoggedError.count() == 0

    # target not found
    assign.target_id = '42{{ form_var_string }}'  # == '424'
    wf.store()
    formdata = formdef.data_class()()
    formdata.data = {
        '0': 'Parent',
        '1': 'Foo',
        '2': '4',
        '3': '3',  # set from datasource
    }
    # set parent
    formdata.submission_context = {
        'orig_object_type': 'carddef',
        'orig_formdata_id': '2',
        'orig_formdef_id': str(carddef.id),
    }
    formdata.user_id = user.id
    formdata.store()
    formdata.just_created()
    formdata.store()
    formdata.perform_workflow()
    assert carddef.data_class().count() == 6
    assert carddef.data_class().get(1).user_id == str(user.id)
    assert carddef.data_class().get(2).user_id is None
    assert carddef.data_class().get(3).user_id is None
    assert carddef.data_class().get(4).user_id is None  # not changed
    assert carddef.data_class().get(5).user_id is None
    assert carddef.data_class().get(6).user_id is None
    assert LoggedError.count() == 1
    logged_error = LoggedError.select()[0]
    assert logged_error.summary == 'Could not find targeted "Parent" object by id 424'

    # slug not or badly configured
    assign.target_id = '{{ form_var_string }}'  # == '5'
    assign.formdef_slug = None
    wf.store()
    formdata = formdef.data_class()()
    formdata.data = {
        '0': 'Parent',
        '1': 'Foo',
        '2': '5',
        '3': '3',  # set from datasource
    }
    # set parent
    formdata.submission_context = {
        'orig_object_type': 'carddef',
        'orig_formdata_id': '2',
        'orig_formdef_id': str(carddef.id),
    }
    formdata.user_id = user.id
    formdata.store()
    formdata.just_created()
    formdata.store()
    formdata.perform_workflow()
    assert carddef.data_class().count() == 7
    assert carddef.data_class().get(1).user_id == str(user.id)
    assert carddef.data_class().get(2).user_id is None
    assert carddef.data_class().get(3).user_id is None
    assert carddef.data_class().get(4).user_id is None
    assert carddef.data_class().get(5).user_id is None  # not changed
    assert carddef.data_class().get(6).user_id is None
    assert carddef.data_class().get(7).user_id is None


def test_assign_carddata_targeting_itself(pub):
    CardDef.wipe()
    pub.user_class.wipe()

    user = pub.user_class()
    user.email = 'test@example.net'
    user.name_identifiers = ['xyz']
    user.store()

    carddef = CardDef()
    carddef.name = 'Foo Card'
    carddef.fields = [
        StringField(id='0', label='foo', varname='foo'),
    ]
    carddef.store()
    carddef.data_class().wipe()

    # card workflow: assign itself then jump to second status
    card_wf = Workflow(name='Card workflow')
    st1 = card_wf.add_status('Status1')
    st2 = card_wf.add_status('Status2')

    assign = st1.add_action('assign_carddata', id='_assign')
    assign.formdef_slug = carddef.url_name
    assign.target_mode = 'manual'
    assign.target_id = '{{ form_internal_id }}'  # itself
    assign.user_association_mode = 'custom'
    assign.user_association_template = 'xyz'

    jump = st1.add_action('jump', id='_jump')
    jump.status = st2.id

    card_wf.store()

    carddef.workflow = card_wf
    carddef.store()

    # create some cardata
    for i in range(1, 4):
        carddata = carddef.data_class()()
        carddata.data = {
            '0': 'foo %s' % i,
        }
        carddata.user_id = 42
        carddata.store()
        # run workflow, verify that carddata is assign
        carddata.just_created()
        carddata.store()
        carddata.perform_workflow()
        assert str(carddata.user_id) == str(user.id)
        assert carddata.status == 'wf-%s' % st2.id


def test_assign_carddata_from_created_object(pub):
    FormDef.wipe()
    CardDef.wipe()
    pub.user_class.wipe()

    user = pub.user_class()
    user.email = 'test@example.net'
    user.name_identifiers = ['xyz']
    user.store()

    carddef = CardDef()
    carddef.name = 'Card'
    carddef.fields = [
        StringField(id='0', label='Card Field', varname='card_field'),
    ]
    carddef.store()
    carddef.data_class().wipe()

    formdef = FormDef()
    formdef.name = 'Form'
    formdef.fields = [
        StringField(id='0', label='Form Field', varname='form_field'),
    ]
    formdef.store()

    # card workflow: create formdata then jump to second status
    card_wf = Workflow(name='Card workflow')
    st1 = card_wf.add_status('Status1')
    st2 = card_wf.add_status('Status2')

    create = st1.add_action('create_formdata', id='_create')
    create.formdef_slug = formdef.url_name
    create.mappings = [
        Mapping(field_id='0', expression='...'),
    ]

    jump = st1.add_action('jump', id='_jump')
    jump.status = st2.id

    # form workflow: assign parent card data
    form_wf = Workflow(name='Form workflow')
    st1 = form_wf.add_status('Status1')
    assign = st1.add_action('assign_carddata', id='assign')
    assign.formdef_slug = carddef.url_name
    assign.user_association_mode = 'custom'
    assign.user_association_template = 'xyz'
    form_wf.store()

    carddef.workflow = card_wf
    carddef.store()

    formdef.workflow = form_wf
    formdef.store()

    carddata = carddef.data_class()()
    carddata.data = {'0': 'Foo'}
    carddata.store()
    carddata.just_created()
    carddata.store()
    carddata.perform_workflow()
    assert str(carddata.user_id) == str(user.id)

    carddata_reloaded = carddata.get(carddata.id)
    assert carddata_reloaded.status == 'wf-2'
    assert str(carddata_reloaded.user_id) == str(user.id)


def test_assign_carddata_user_association(pub):
    CardDef.wipe()
    FormDef.wipe()
    pub.user_class.wipe()

    user = pub.user_class()
    user.email = 'test@example.net'
    user.name_identifiers = ['xyz']
    user.store()
    user2 = pub.user_class()
    user2.store()

    carddef = CardDef()
    carddef.name = 'Person'
    carddef.fields = [
        StringField(id='0', label='First Name', varname='first_name'),
        StringField(id='1', label='Last Name', varname='last_name'),
    ]
    carddef.digest_templates = {'default': '{{ form_var_first_name }} {{ form_var_last_name }}'}
    carddef.user_support = 'optional'
    carddef.store()
    carddef.data_class().wipe()

    carddata = carddef.data_class()()
    carddata.data = {'0': 'Foo', '1': 'Bar'}
    carddata.user_id = user2.id
    carddata.store()

    wf = Workflow(name='assign-carddata')
    wf.possible_status = Workflow.get_default_workflow().possible_status[:]
    assign = wf.possible_status[1].add_action('assign_carddata', id='_assign', prepend=True)
    assign.label = 'Assign CardDef'
    assign.varname = 'mycard'
    assign.formdef_slug = carddef.url_name
    wf.store()

    datasource = {'type': 'carddef:%s' % carddef.url_name}
    formdef = FormDef()
    formdef.name = 'Persons'
    formdef.fields = [
        ItemField(id='0', label='Person', varname='person', data_source=datasource),
    ]
    formdef.workflow = wf
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {'0': '1'}
    formdata.user_id = user.id
    formdata.just_created()
    formdata.store()
    formdata.perform_workflow()

    assert carddef.data_class().select()[0].user is None

    # keep user
    carddata.user_id = user2.id
    carddata.store()
    assign.user_association_mode = 'keep-user'
    wf.store()

    formdata = FormDef.get(formdef.id).data_class()()
    formdata.data = {'0': '1'}
    formdata.user_id = user.id
    formdata.just_created()
    formdata.store()
    formdata.perform_workflow()

    assert carddef.data_class().select()[0].user.id == user.id

    # user association on direct user
    carddata.user_id = user2.id
    carddata.store()
    assign.user_association_mode = 'custom'
    assign.user_association_template = '{{ form_user }}'
    wf.store()

    formdata = FormDef.get(formdef.id).data_class()()
    formdata.data = {'0': '1'}
    formdata.user_id = user.id
    formdata.just_created()
    formdata.store()
    formdata.perform_workflow()

    assert carddef.data_class().select()[0].user.id == user.id

    # user association on user email
    carddata.user_id = user2.id
    carddata.store()
    assign.user_association_mode = 'custom'
    assign.user_association_template = 'test@example.net'
    wf.store()

    formdata = FormDef.get(formdef.id).data_class()()
    formdata.data = {'0': '1'}
    formdata.just_created()
    formdata.store()
    formdata.perform_workflow()

    assert carddef.data_class().select()[0].user.id == user.id

    # user association on name id
    carddata.user_id = user2.id
    carddata.store()
    assign.user_association_mode = 'custom'
    assign.user_association_template = 'xyz'
    wf.store()

    formdata = FormDef.get(formdef.id).data_class()()
    formdata.data = {'0': '1'}
    formdata.just_created()
    formdata.store()
    formdata.perform_workflow()

    assert carddef.data_class().select()[0].user.id == user.id

    # user association on invalid user
    carddata.user_id = user2.id
    carddata.store()
    assign.user_association_mode = 'custom'
    assign.user_association_template = 'zzz'
    wf.store()

    formdata = FormDef.get(formdef.id).data_class()()
    formdata.data = {'0': '1'}
    formdata.just_created()
    LoggedError.wipe()
    formdata.store()
    formdata.perform_workflow()

    assert carddef.data_class().select()[0].user is None
    assert isinstance(formdata.evolution[1].parts[0], JournalAssignationErrorPart)
    assert formdata.evolution[1].parts[0].label == 'Assign Card Data (Person)'
    assert formdata.evolution[1].parts[0].summary == 'Failed to attach user (not found: "zzz")'
    assert LoggedError.count() == 0  # no logged error

    # user association on invalid template
    carddata.user_id = user2.id
    carddata.store()
    assign.user_association_mode = 'custom'
    assign.user_association_template = '{% %}'
    wf.store()

    formdata = FormDef.get(formdef.id).data_class()()
    formdata.data = {'0': '1'}
    formdata.just_created()
    formdata.store()
    formdata.perform_workflow()

    assert carddef.data_class().select()[0].user is None


def test_create_carddata_with_workflow_deleting_it(pub):
    CardDef.wipe()
    FormDef.wipe()

    card_wf = Workflow(name='card workflow')
    st1 = card_wf.add_status('st1')
    st1.add_action('remove')
    card_wf.store()

    carddef = CardDef()
    carddef.name = 'My card'
    carddef.fields = [
        StringField(id='1', label='string'),
    ]
    carddef.workflow_id = card_wf.id
    carddef.store()

    wf = Workflow(name='create-carddata')
    wf.possible_status = Workflow.get_default_workflow().possible_status[:]
    create = wf.possible_status[1].add_action('create_carddata', id='_create', prepend=True)
    create.action_label = 'Create CardDef'
    create.varname = 'mycard'
    create.formdef_slug = carddef.url_name
    create.mappings = [Mapping(field_id='1', expression='plop')]
    wf.store()

    formdef = FormDef()
    formdef.name = 'source form'
    formdef.fields = []
    formdef.workflow_id = wf.id
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {}
    formdata.just_created()
    formdata.store()
    formdata.perform_workflow()
    assert carddef.data_class().count() == 0


def setup_anonymise_action_unlink_user(pub):
    CardDef.wipe()
    FormDef.wipe()
    pub.user_class.wipe()

    user = pub.user_class()
    user.email = 'test@example.net'
    user.name_identifiers = ['xyz']
    user.store()

    wf = Workflow(name='test-unlink-user')
    wf.possible_status = Workflow.get_default_workflow().possible_status[:]
    anonymise = wf.possible_status[1].add_action('anonymise', id='_anonymise', prepend=True)
    anonymise.label = 'Unlink User'
    anonymise.varname = 'mycard'
    anonymise.mode = 'unlink_user'
    wf.store()

    carddef = CardDef()
    carddef.name = 'Person'
    carddef.workflow_id = wf.id
    carddef.store()
    carddef.data_class().wipe()

    carddata = carddef.data_class()()
    carddata.data = {'0': 'Foo', '1': 'Bar'}
    carddata.user_id = user.id
    carddata.just_created()
    carddata.store()
    return carddef, user, carddata


def test_anonymise_action_unlink_user(pub):
    carddef, user, carddata = setup_anonymise_action_unlink_user(pub)

    assert carddef.data_class().select()[0].user.id == user.id
    carddata.perform_workflow()
    assert carddef.data_class().select()[0].user is None


def test_anonymise_action_unlink_user_submitter_is_triggerer(pub):
    carddef, user, carddata = setup_anonymise_action_unlink_user(pub)

    pub.get_request()._user = ()
    pub.get_request().session = sessions.BasicSession(id=1)
    pub.get_request().session.set_user(user.id)

    assert carddef.data_class().select()[0].user.id == user.id
    carddata.perform_workflow()
    assert carddef.data_class().select()[0].user is None
    assert not pub.get_request().session.is_anonymous_submitter(carddata)


def test_anonymise_action_unlink_user_no_request(pub):
    carddef, user, carddata = setup_anonymise_action_unlink_user(pub)

    pub._request = None

    assert carddef.data_class().select()[0].user.id == user.id
    carddata.perform_workflow()
    assert carddef.data_class().select()[0].user is None


def test_create_carddata_from_external_workflow(pub):
    CardDef.wipe()
    FormDef.wipe()
    LoggedError.wipe()

    carddef_guest_dinner = CardDef()
    carddef_guest_dinner.name = 'guest/dinner'
    carddef_guest_dinner.fields = [StringField(id='1', label='foo'), StringField(id='2', label='bar')]
    carddef_guest_dinner.store()

    carddef_guest = CardDef()
    carddef_guest.name = 'guest'
    carddef_guest.fields = [StringField(id='0', label='bar', varname='bar')]
    carddef_guest.store()

    carddef_dinner = CardDef()
    carddef_dinner.name = 'dinner'
    carddef_dinner.fields = [StringField(id='0', label='foo', varname='foo')]
    carddef_dinner.store()

    guest_workflow = Workflow(name='guest')
    guest_workflow.add_status('st0')
    action = guest_workflow.add_global_action('create guest/dinner card')
    trigger = action.append_trigger('webservice')
    trigger.identifier = 'create-guest-dinner-card'
    create_carddata = action.add_action('create_carddata')
    create_carddata.formdef_slug = carddef_guest_dinner.slug
    create_carddata.varname = 'guest_dinner'
    create_carddata.mappings = [
        Mapping(field_id='1', expression='{{ caller_form_var_foo }}'),
        Mapping(field_id='2', expression='{{ form_var_bar }}'),
    ]
    guest_workflow.store()

    carddef_guest.workflow = guest_workflow
    carddef_guest.store()

    dinner_workflow = Workflow(name='dinner')
    status = dinner_workflow.add_status('st0')
    action = status.add_action('external_workflow_global_action')
    action.slug = 'carddef:%s' % carddef_guest.url_name
    action.trigger_id = 'action:create-guest-dinner-card'
    action.target_mode = 'manual'
    action.target_id = '{{ cards|objects:"guest" }}'
    dinner_workflow.store()

    carddef_dinner.workflow = dinner_workflow
    carddef_dinner.store()

    # create some guests
    for i in range(10):
        guest = carddef_guest.data_class()()
        guest.data = {'0': 'bar %s' % i}
        guest.just_created()
        guest.store()

    # create a dinner
    dinner = carddef_dinner.data_class()()
    dinner.data = {'0': 'foo'}
    dinner.just_created()
    dinner.store()

    dinner.perform_workflow()
    assert LoggedError.count() == 0  # no error detected
    assert carddef_guest_dinner.data_class().count() == 10


def test_create_carddata_remove_self(pub):
    CardDef.wipe()
    FormDef.wipe()

    carddef = CardDef()
    carddef.name = 'My card'
    carddef.fields = [
        StringField(id='1', label='string'),
    ]
    carddef.store()

    wf = Workflow(name='create-carddata')

    action = wf.add_global_action('Delete', 'delete')
    action.add_action('remove')
    trigger = action.append_trigger('webservice')
    trigger.identifier = 'delete'

    wf.possible_status = Workflow.get_default_workflow().possible_status[:]
    create = wf.possible_status[1].add_action('create_carddata', id='_create', prepend=True)
    create.varname = 'mycard'
    create.formdef_slug = carddef.url_name
    create.mappings = [
        Mapping(field_id='1', expression='hello'),
    ]
    create.action_label = 'Create CardDef'
    wf.store()

    formdef = FormDef()
    formdef.name = 'source form'
    formdef.fields = []
    formdef.workflow_id = wf.id
    formdef.store()

    card_wf = Workflow(name='card-wf')
    st1 = card_wf.add_status('st1')
    action = st1.add_action('external_workflow_global_action')
    action.slug = 'formdef:%s' % formdef.url_name
    action.trigger_id = 'action:%s' % trigger.identifier
    card_wf.store()
    carddef.workflow = card_wf
    carddef.store()

    formdata = formdef.data_class()()
    formdata.data = {}
    formdata.just_created()
    formdata.store()
    url = formdata.perform_workflow()
    with pytest.raises(KeyError):
        formdata.refresh_from_storage()  # was deleted
    assert url == 'http://example.net'  # forced abort, redirect to home


def test_create_carddata_custom_id(pub):
    CardDef.wipe()
    FormDef.wipe()

    carddef = CardDef()
    carddef.name = 'My card'
    carddef.fields = [
        StringField(id='1', label='slug', varname='slug'),
    ]
    carddef.id_template = 'card_{{form_var_slug}}'
    carddef.store()

    wf = Workflow(name='create-carddata')
    wf.possible_status = Workflow.get_default_workflow().possible_status[:]
    create = wf.possible_status[1].add_action('create_carddata', id='_create', prepend=True)
    create.varname = 'mycard'
    create.formdef_slug = carddef.url_name
    create.mappings = [
        Mapping(field_id='1', expression='a'),
    ]
    create.action_label = 'Create CardDef'
    wf.store()

    formdef = FormDef()
    formdef.name = 'source form'
    formdef.workflow_id = wf.id
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {}
    formdata.just_created()

    assert carddef.data_class().count() == 0

    formdata = formdef.data_class()()
    formdata.data = {}

    formdata.just_created()
    formdata.store()
    formdata.perform_workflow()

    assert carddef.data_class().count() == 1
    # check tracing
    carddata = carddef.data_class().select()[0]
    trace = WorkflowTrace.select_for_formdata(carddata)[0]
    assert trace.event == 'workflow-created'
    assert trace.event_args['external_workflow_id'] == wf.id
    assert trace.event_args['external_status_id'] == 'new'
    assert trace.event_args['external_item_id'] == '_create'
    trace = WorkflowTrace.select_for_formdata(formdata)[-1]
    assert trace.event == 'workflow-created-carddata'
    assert trace.event_args['external_formdef_id'] == carddef.id
    assert trace.event_args['external_formdata_id'] == carddata.id  # traces keep using native id

    assert formdata.get_substitution_variables()['form_links_mycard_form_identifier'] == 'card_a'
    assert list(formdata.iter_target_datas())[0][0].id == carddata.id

    # create a second card
    create.mappings = [
        Mapping(field_id='1', expression='b'),
    ]
    wf.store()
    formdef.refresh_from_storage()
    formdata = formdef.data_class().get(formdata.id)
    formdata.perform_workflow()
    carddata2 = carddef.data_class().select(order_by='id')[1]
    assert {x[0].id for x in formdata.iter_target_datas()} == {carddata.id, carddata2.id}

    # check compatibility with older parts, that didn't have formdata_id_is_natural
    part = list(formdata.iter_evolution_parts(LinkedFormdataEvolutionPart))[-1]
    del part.formdata_id_is_natural
    part.formdata_id = carddata2.id
    formdata.store()
    assert {x[0].id for x in formdata.iter_target_datas()} == {carddata.id, carddata2.id}

    carddef.data_class().wipe()
    assert set(formdata.iter_target_datas()) == {
        ('Linked "My card" object by id card_a', 'Evolution - not found'),
        (f'Linked "My card" object by id {carddata2.id}', 'Evolution - not found'),
    }

    formdata = formdef.data_class().get(formdata.id)
    with pytest.raises(KeyError):
        # noqa pylint: disable=expression-not-assigned
        formdata.get_substitution_variables()['form_links_mycard_form_identifier']
