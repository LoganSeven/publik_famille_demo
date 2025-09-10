import pytest
from quixote import cleanup

from wcs import sessions
from wcs.carddef import CardDef
from wcs.fields import EmailField, ItemField, ItemsField, StringField
from wcs.formdef import FormDef
from wcs.logged_errors import LoggedError
from wcs.qommon.http_request import HTTPRequest
from wcs.wf.create_formdata import Mapping
from wcs.wf.external_workflow import ManyExternalCallsPart
from wcs.workflows import Workflow, WorkflowBackofficeFieldsFormDef, perform_items

from ..utilities import clean_temporary_pub, create_temporary_pub, get_app, login
from .test_all import admin_user  # noqa pylint: disable=unused-import


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


def test_call_external_workflow_with_evolution_linked_object(pub):
    FormDef.wipe()
    CardDef.wipe()
    Workflow.wipe()

    external_wf = Workflow(name='External Workflow')
    st1 = external_wf.add_status(name='New')
    action = external_wf.add_global_action('Delete', 'delete')
    action.add_action('remove')
    trigger = action.append_trigger('webservice')
    trigger.identifier = 'delete'
    external_wf.store()

    external_formdef = FormDef()
    external_formdef.name = 'External Form'
    external_formdef.fields = [
        StringField(id='0', label='string', varname='form_string'),
    ]
    external_formdef.workflow = external_wf
    external_formdef.store()

    external_carddef = CardDef()
    external_carddef.name = 'External Card'
    external_carddef.fields = [
        StringField(id='0', label='string', varname='card_string'),
    ]
    external_carddef.workflow = external_wf
    external_carddef.store()

    wf = Workflow(name='External actions')
    st1 = wf.add_status('Create external formdata')
    create_formdata = st1.add_action('create_formdata', id='_create_form')
    create_formdata.action_label = 'create linked form'
    create_formdata.formdef_slug = external_formdef.url_name
    create_formdata.varname = 'created_form'
    mappings = [Mapping(field_id='0', expression='{{ form_var_string }}')]
    create_formdata.mappings = mappings

    create_carddata = st1.add_action('create_carddata', id='_create_card')
    create_carddata.action_label = 'create linked card'
    create_carddata.formdef_slug = external_carddef.url_name
    create_carddata.varname = 'created_card'
    create_carddata.mappings = mappings

    global_action = wf.add_global_action('Delete external linked object', 'delete')
    action = global_action.add_action('external_workflow_global_action')
    action.slug = 'formdef:%s' % external_formdef.url_name
    action.trigger_id = 'action:%s' % trigger.identifier
    wf.store()
    assert external_formdef in wf.get_dependencies()

    formdef = FormDef()
    formdef.name = 'External action form'
    formdef.fields = [
        StringField(id='0', label='string', varname='string'),
    ]
    formdef.workflow = wf
    formdef.store()

    assert external_formdef.data_class().count() == 0
    assert external_carddef.data_class().count() == 0

    formdata = formdef.data_class()()
    formdata.data = {'0': 'test form'}
    formdata.store()
    formdata.just_created()
    formdata.perform_workflow()

    assert external_formdef.data_class().count() == 1
    assert external_formdef.data_class().get(1).relations_data == {'formdef:external-action-form': ['1']}
    assert external_carddef.data_class().count() == 1
    assert external_carddef.data_class().get(1).relations_data == {'formdef:external-action-form': ['1']}

    # remove external formdata
    perform_items([action], formdata)
    assert external_formdef.data_class().count() == 0
    assert external_carddef.data_class().count() == 1
    assert external_carddef.data_class().get(1).relations_data == {'formdef:external-action-form': ['1']}

    # formdata is already deleted: cannot find it again, no problem
    perform_items([action], formdata)

    # try remove an unexisting carddef: do nothing
    unused_carddef = CardDef()
    unused_carddef.name = 'External Card (not used)'
    unused_carddef.fields = []
    unused_carddef.workflow = external_wf
    unused_carddef.store()
    action.slug = 'carddef:%s' % unused_carddef.url_name
    wf.store()
    perform_items([action], formdata)
    assert external_formdef.data_class().count() == 0
    assert external_carddef.data_class().count() == 1
    assert external_carddef.data_class().get(1).relations_data == {'formdef:external-action-form': ['1']}
    # remove the right carddef
    action.slug = 'carddef:%s' % external_carddef.url_name
    wf.store()
    perform_items([action], formdata)
    assert external_formdef.data_class().count() == 0
    assert external_carddef.data_class().count() == 0


def test_call_external_workflow_with_data_sourced_object(pub, admin_user):
    FormDef.wipe()
    CardDef.wipe()
    Workflow.wipe()

    carddef_wf = Workflow(name='Carddef Workflow')
    carddef_wf.add_status('status')
    carddef_wf.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(carddef_wf)
    carddef_wf.backoffice_fields_formdef.fields = [
        StringField(id='bo0', varname='bo', label='bo variable'),
    ]
    update_action = carddef_wf.add_global_action('Update', 'ac1')
    update_action.add_action('set-backoffice-fields')
    setbo = update_action.items[0]
    setbo.fields = [{'field_id': 'bo0', 'value': '{{ form_var_bo|default:"0"|add:1 }}'}]
    trigger = update_action.append_trigger('webservice')
    trigger.identifier = 'update'

    delete = carddef_wf.add_global_action('Delete', 'delete')
    delete.add_action('remove')
    trigger = delete.append_trigger('webservice')
    trigger.identifier = 'delete'
    carddef_wf.store()

    carddef = CardDef()
    carddef.name = 'Data'
    carddef.fields = [
        StringField(id='0', label='string', varname='card_string'),
    ]
    carddef.digest_templates = {'default': '{{ form_var_card_string }}'}
    carddef.workflow = carddef_wf
    carddef.store()

    carddata = carddef.data_class()()
    carddata.data = {'0': 'Text'}
    carddata.just_created()
    carddata.store()

    wf = Workflow(name='External actions')
    wf.add_status('Action')

    update_global_action = wf.add_global_action('Update linked object data')
    update_action = update_global_action.add_action('external_workflow_global_action')
    update_action.slug = 'carddef:%s' % carddef.url_name
    update_action.trigger_id = 'action:update'

    delete_global_action = wf.add_global_action('Delete external linked object', 'delete')
    delete_action = delete_global_action.add_action('external_workflow_global_action')
    delete_action.slug = 'carddef:%s' % carddef.url_name
    delete_action.trigger_id = 'action:delete'
    wf.store()

    datasource = {'type': 'carddef:%s' % carddef.url_name}
    formdef = FormDef()
    formdef.name = 'External action form'
    formdef.fields = [
        ItemField(id='0', label='Card', varname='card', data_source=datasource),
        EmailField(id='1', label='Email', varname='email'),
    ]
    formdef.workflow = wf
    formdef.store()

    assert carddef.data_class().count() == 1

    formdata = formdef.data_class()()
    formdata.data = {'0': '1', '1': 'foo@example.com'}
    formdata.store()
    formdata.just_created()
    formdata.perform_workflow()

    perform_items([update_action], formdata)
    assert carddef.data_class().count() == 1
    data = carddef.data_class().select()[0]
    assert data.data['bo0'] == '1'

    assert [x for x in data.get_workflow_traces() if x.event][-1].event == 'global-external-workflow'
    resp = login(get_app(pub), username='admin', password='admin').get(data.get_backoffice_url() + 'inspect')
    # check event line is a link to global action
    assert resp.pyquery('#inspect-timeline .event a').text() == 'Trigger by external workflow'
    assert (
        resp.pyquery('#inspect-timeline .event a').attr.href
        == 'http://example.net/backoffice/workflows/1/global-actions/ac1/'
    )
    # check action tracing link are correct
    assert [x.attrib['href'] for x in resp.pyquery('#inspect-timeline a.tracing-link')] == [
        'http://example.net/backoffice/workflows/1/status/1/',
        'http://example.net/backoffice/workflows/1/global-actions/ac1/items/1/',
    ]

    perform_items([update_action], formdata)
    data = carddef.data_class().select()[0]
    assert data.data['bo0'] == '2'

    perform_items([delete_action], formdata)
    assert carddef.data_class().count() == 0

    # linked object is removed: no problem
    perform_items([delete_action], formdata)


def test_call_external_workflow_with_items_data_sourced_object(pub, admin_user):
    FormDef.wipe()
    CardDef.wipe()
    Workflow.wipe()

    carddef_wf = Workflow(name='Carddef Workflow')
    carddef_wf.add_status('status')
    carddef_wf.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(carddef_wf)
    carddef_wf.backoffice_fields_formdef.fields = [
        StringField(id='bo0', varname='bo', label='bo variable'),
    ]
    update_action = carddef_wf.add_global_action('Update', 'ac1')
    update_action.add_action('set-backoffice-fields')
    setbo = update_action.items[0]
    setbo.fields = [{'field_id': 'bo0', 'value': '{{ form_var_bo|default:"0"|add:1 }}'}]
    trigger = update_action.append_trigger('webservice')
    trigger.identifier = 'update'
    carddef_wf.store()

    carddef = CardDef()
    carddef.name = 'Data'
    carddef.fields = [
        StringField(id='0', label='string', varname='card_string'),
    ]
    carddef.digest_templates = {'default': '{{ form_var_card_string }}'}
    carddef.workflow = carddef_wf
    carddef.store()

    carddata = carddef.data_class()()
    carddata.data = {'0': 'Text1'}
    carddata.just_created()
    carddata.store()

    carddata2 = carddef.data_class()()
    carddata2.data = {'0': 'Text2'}
    carddata2.just_created()
    carddata2.store()

    carddata3 = carddef.data_class()()
    carddata3.data = {'0': 'Text3'}
    carddata3.just_created()
    carddata3.store()

    wf = Workflow(name='External actions')
    wf.add_status('Action')

    update_global_action = wf.add_global_action('Update linked object data')
    update_action = update_global_action.add_action('external_workflow_global_action')
    update_action.slug = 'carddef:%s' % carddef.url_name
    update_action.trigger_id = 'action:update'

    wf.store()

    datasource = {'type': 'carddef:%s' % carddef.url_name}
    formdef = FormDef()
    formdef.name = 'External action form'
    formdef.fields = [
        ItemsField(id='0', label='Cards', varname='cards', data_source=datasource),
        EmailField(id='1', label='Email', varname='email'),
    ]
    formdef.workflow = wf
    formdef.store()

    assert carddef.data_class().count() == 3

    formdata = formdef.data_class()()
    formdata.data = {'0': [str(carddata.id), str(carddata3.id)], '1': 'foo@example.com'}
    formdata.data['0_display'] = formdef.fields[0].store_display_value(formdata.data, '1')
    formdata.data['0_structured'] = formdef.fields[0].store_structured_value(formdata.data, '1')
    formdata.store()
    formdata.just_created()
    formdata.perform_workflow()

    perform_items([update_action], formdata)
    assert carddef.data_class().count() == 3
    carddata.refresh_from_storage()
    assert carddata.data['bo0'] == '1'
    carddata2.refresh_from_storage()
    assert not carddata2.data.get('bo0')
    carddata3.refresh_from_storage()
    assert carddata3.data['bo0'] == '1'

    perform_items([update_action], formdata)
    carddata.refresh_from_storage()
    assert carddata.data['bo0'] == '2'
    carddata2.refresh_from_storage()
    assert not carddata2.data.get('bo0')
    carddata3.refresh_from_storage()
    assert carddata3.data['bo0'] == '2'


def test_call_external_workflow_with_parent_object(pub):
    FormDef.wipe()
    CardDef.wipe()
    Workflow.wipe()

    # carddef workflow, with global action to increment a counter in its
    # backoffice fields.
    carddef_wf = Workflow(name='Carddef Workflow')
    carddef_wf.add_status('New')
    carddef_wf.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(carddef_wf)
    carddef_wf.backoffice_fields_formdef.fields = [
        StringField(id='bo0', varname='bo', label='bo variable'),
    ]
    increment_global_action = carddef_wf.add_global_action('Update')
    increment_global_action.add_action('set-backoffice-fields')
    setbo = increment_global_action.items[0]
    setbo.fields = [{'field_id': 'bo0', 'value': '{{ form_var_bo|default:"0"|add:1 }}'}]
    trigger = increment_global_action.append_trigger('webservice')
    trigger.identifier = 'update'
    carddef_wf.store()

    # associated carddef
    carddef = CardDef()
    carddef.name = 'Data'
    carddef.fields = [
        StringField(id='0', label='string', varname='card_string'),
    ]
    carddef.workflow = carddef_wf
    carddef.store()

    # and sample carddata
    carddata = carddef.data_class()()
    carddata.data = {'0': 'Text'}
    carddata.just_created()
    carddata.store()

    # formdef workflow that will trigger the global action
    wf = Workflow(name='External actions')
    wf.add_status('Action')

    update_global_action = wf.add_global_action('Update linked object data')
    update_action = update_global_action.add_action('external_workflow_global_action')
    update_action.slug = 'carddef:%s' % carddef.url_name
    update_action.trigger_id = 'action:update'
    wf.store()

    # associated formdef
    formdef = FormDef()
    formdef.name = 'External action form'
    formdef.fields = [EmailField(id='1', label='Email', varname='email')]
    formdef.workflow = wf
    formdef.store()

    # and formdata
    formdata = formdef.data_class()()
    formdata.data = {'1': 'foo@example.com'}
    formdata.store()
    formdata.just_created()
    formdata.perform_workflow()

    # run, against no parent
    perform_items([update_action], formdata)
    card = carddef.data_class().get(carddata.id)
    assert not card.data.get('bo0')  # not called

    # other parent
    formdata.submission_context = {
        'orig_object_type': 'formdef',
        'orig_formdata_id': str(formdata.id),
        'orig_formdef_id': str(formdef.id),
    }
    formdata.store()
    perform_items([update_action], formdata)
    card = carddef.data_class().get(carddata.id)
    assert not card.data.get('bo0')  # not called

    # appropriate parent
    formdata.submission_context = {
        'orig_object_type': 'carddef',
        'orig_formdata_id': str(carddata.id),
        'orig_formdef_id': str(carddef.id),
    }
    formdata.store()
    perform_items([update_action], formdata)
    card = carddef.data_class().get(carddata.id)
    assert card.data['bo0'] == '1'  # got called


def test_call_external_workflow_use_caller_variable(pub):
    FormDef.wipe()
    CardDef.wipe()
    Workflow.wipe()

    # carddef workflow, with global action to set a value in a backoffice field
    carddef_wf = Workflow(name='Carddef Workflow')
    carddef_wf.add_status('New')
    carddef_wf.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(carddef_wf)
    carddef_wf.backoffice_fields_formdef.fields = [
        StringField(id='bo0', varname='bo', label='bo variable'),
    ]
    global_action = carddef_wf.add_global_action('Update')
    global_action.add_action('set-backoffice-fields')
    setbo = global_action.items[0]
    setbo.fields = [{'field_id': 'bo0', 'value': '{{ caller_form_var_email }}'}]
    trigger = global_action.append_trigger('webservice')
    trigger.identifier = 'update'
    carddef_wf.store()

    # associated carddef
    carddef = CardDef()
    carddef.name = 'Data'
    carddef.fields = [
        StringField(id='0', label='string', varname='card_string'),
    ]
    carddef.workflow = carddef_wf
    carddef.store()

    # and sample carddata
    carddata = carddef.data_class()()
    carddata.data = {'0': 'Text'}
    carddata.just_created()
    carddata.store()

    # formdef workflow that will trigger the global action
    wf = Workflow(name='External actions')
    wf.add_status('Action')

    update_global_action = wf.add_global_action('Update linked object data')
    update_action = update_global_action.add_action('external_workflow_global_action')
    update_action.slug = 'carddef:%s' % carddef.url_name
    update_action.trigger_id = 'action:update'
    wf.store()

    # associated formdef
    formdef = FormDef()
    formdef.name = 'External action form'
    formdef.fields = [EmailField(id='1', label='Email', varname='email')]
    formdef.workflow = wf
    formdef.store()

    # and formdata
    formdata = formdef.data_class()()
    formdata.data = {'1': 'foo@example.com'}
    formdata.store()
    formdata.just_created()
    formdata.perform_workflow()

    # run, against no parent
    perform_items([update_action], formdata)
    card = carddef.data_class().get(carddata.id)
    assert not card.data.get('bo0')  # not called

    # appropriate parent
    formdata.submission_context = {
        'orig_object_type': 'carddef',
        'orig_formdata_id': str(carddata.id),
        'orig_formdef_id': str(carddef.id),
    }
    formdata.store()
    perform_items([update_action], formdata)
    card = carddef.data_class().get(carddata.id)
    assert card.data['bo0'] == 'foo@example.com'  # got called


def test_call_external_workflow_manual_targeting(pub):
    FormDef.wipe()
    CardDef.wipe()
    Workflow.wipe()

    # carddef workflow, with global action to increment a counter in its
    # backoffice fields.
    carddef_wf = Workflow(name='Carddef Workflow')
    carddef_wf.add_status(name='New')
    carddef_wf.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(carddef_wf)
    carddef_wf.backoffice_fields_formdef.fields = [
        StringField(id='bo0', varname='bo', label='bo variable'),
    ]
    global_action = carddef_wf.add_global_action('Update')
    global_action.add_action('set-backoffice-fields')
    setbo = global_action.items[0]
    setbo.fields = [{'field_id': 'bo0', 'value': '{{ form_var_bo|default:"0"|add:1 }}'}]
    trigger = global_action.append_trigger('webservice')
    trigger.identifier = 'update'
    carddef_wf.store()

    # associated carddef
    carddef = CardDef()
    carddef.name = 'Data'
    carddef.fields = [
        StringField(id='0', label='string', varname='card_string'),
    ]
    carddef.workflow = carddef_wf
    carddef.store()
    carddef.data_class().wipe()

    # and sample carddatas
    for i in range(1, 4):
        carddata = carddef.data_class()()
        carddata.data = {'0': 'Text %s' % i}
        carddata.just_created()
        carddata.store()

    # formdef workflow that will trigger the global action
    wf = Workflow(name='External actions')
    st1 = wf.add_status('Action')

    update_global_action = wf.add_global_action('Update linked object data')
    update_action = update_global_action.add_action('external_workflow_global_action')
    update_action.slug = 'carddef:%s' % carddef.url_name
    update_action.target_mode = 'manual'
    update_action.target_id = None  # not configured
    update_action.trigger_id = 'action:update'

    # and create carddata
    create_carddata = st1.add_action('create_carddata', id='_create_card')
    create_carddata.action_label = 'create linked card'
    create_carddata.formdef_slug = carddef.url_name
    create_carddata.varname = 'created_card'
    create_carddata.mappings = [Mapping(field_id='0', expression='{{ form_var_string }}')]

    wf.store()

    # associated formdef
    datasource = {'type': 'carddef:%s' % carddef.url_name}
    formdef = FormDef()
    formdef.name = 'External action form'
    formdef.fields = [
        ItemField(id='0', label='Card', varname='card', data_source=datasource),
        StringField(id='1', label='string', varname='string'),
    ]
    formdef.workflow = wf
    formdef.store()

    # and formdata
    formdata = formdef.data_class()()
    formdata.data = {
        '0': '3',  # set from datasource
        '1': '1',
    }
    # set parent
    formdata.submission_context = {
        'orig_object_type': 'carddef',
        'orig_formdata_id': '2',
        'orig_formdef_id': str(carddef.id),
    }
    formdata.store()
    formdata.just_created()
    formdata.perform_workflow()
    assert carddef.data_class().count() == 4
    assert carddef.data_class().get(1).data['bo0'] is None
    assert carddef.data_class().get(2).data['bo0'] is None
    assert carddef.data_class().get(3).data['bo0'] is None
    assert carddef.data_class().get(4).data['bo0'] is None
    # linked carddata
    assert carddef.data_class().get(4).data['0'] == '1'

    # target not configured
    perform_items([update_action], formdata)
    assert carddef.data_class().get(1).data['bo0'] is None
    assert carddef.data_class().get(2).data['bo0'] is None
    assert carddef.data_class().get(3).data['bo0'] is None
    assert carddef.data_class().get(4).data['bo0'] is None
    assert LoggedError.count() == 0

    # configure target
    update_action.target_id = '{{ form_var_string }}'  # == '1'
    wf.store()
    perform_items([update_action], formdata)
    assert carddef.data_class().get(1).data['bo0'] == '1'
    assert carddef.data_class().get(2).data['bo0'] is None
    assert carddef.data_class().get(3).data['bo0'] is None
    assert carddef.data_class().get(4).data['bo0'] is None

    # target not found
    update_action.target_id = '42{{ form_var_string }}'  # == '421'
    wf.store()
    perform_items([update_action], formdata)
    assert carddef.data_class().get(1).data['bo0'] == '1'
    assert carddef.data_class().get(2).data['bo0'] is None
    assert carddef.data_class().get(3).data['bo0'] is None
    assert carddef.data_class().get(4).data['bo0'] is None
    assert LoggedError.count() == 1
    logged_error = LoggedError.select()[0]
    assert logged_error.summary == 'Could not find targeted "Data" object by id 421'

    # error in target template
    LoggedError.wipe()
    update_action.target_id = '{% cards|objects:"..."|test }}'
    wf.store()
    perform_items([update_action], formdata)
    assert LoggedError.count() == 1
    logged_error = LoggedError.select()[0]
    assert logged_error.summary == 'Failed to compute template'

    # slug not or badly configured
    update_action.target_id = '{{ form_var_string }}'  # == '1'
    update_action.slug = None
    wf.store()
    perform_items([update_action], formdata)
    assert carddef.data_class().get(1).data['bo0'] == '1'  # not changed
    assert carddef.data_class().get(2).data['bo0'] is None
    assert carddef.data_class().get(3).data['bo0'] is None
    assert carddef.data_class().get(4).data['bo0'] is None
    update_action.slug = 'foo'
    wf.store()
    perform_items([update_action], formdata)
    assert carddef.data_class().get(1).data['bo0'] == '1'  # not changed
    assert carddef.data_class().get(2).data['bo0'] is None
    assert carddef.data_class().get(3).data['bo0'] is None
    assert carddef.data_class().get(4).data['bo0'] is None


def test_call_external_workflow_manual_queryset_targeting(pub):
    FormDef.wipe()
    CardDef.wipe()
    Workflow.wipe()

    # carddef workflow, with global action to increment a counter in its
    # backoffice fields.
    carddef_wf = Workflow(name='Carddef Workflow')
    carddef_wf.add_status(name='New')
    carddef_wf.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(carddef_wf)
    carddef_wf.backoffice_fields_formdef.fields = [
        StringField(id='bo0', varname='bo', label='bo variable'),
    ]
    global_action = carddef_wf.add_global_action('Update')
    global_action.add_action('set-backoffice-fields')
    setbo = global_action.items[0]
    setbo.fields = [{'field_id': 'bo0', 'value': '{{ form_var_bo|default:"0"|add:1 }}'}]
    trigger = global_action.append_trigger('webservice')  # external call
    trigger.identifier = 'update'
    carddef_wf.store()

    # associated carddef
    carddef = CardDef()
    carddef.name = 'Data'
    carddef.fields = [
        StringField(id='0', label='string', varname='card_string'),
    ]
    carddef.workflow = carddef_wf
    carddef.store()
    carddef.data_class().wipe()

    # and sample carddatas
    for i in range(1, 5):
        carddata = carddef.data_class()()
        carddata.data = {'0': 'Text %s' % i}
        carddata.store()
        carddata.just_created()
        carddata.store()

    # formdef workflow that will trigger the global action
    wf = Workflow(name='External actions')
    wf.add_status('Blah')
    update_global_action = wf.add_global_action('Update linked object data')
    update_action = update_global_action.add_action('external_workflow_global_action')
    update_action.slug = 'carddef:%s' % carddef.url_name
    update_action.target_mode = 'manual'
    update_action.target_id = None  # not configured
    update_action.trigger_id = 'action:update'
    wf.store()

    # associated formdef
    formdef = FormDef()
    formdef.name = 'External action form'
    formdef.fields = []
    formdef.workflow = wf
    formdef.store()

    # and formdata
    formdata = formdef.data_class()()
    formdata.data = {}
    formdata.store()
    formdata.just_created()

    # target not configured
    perform_items([update_action], formdata)
    assert carddef.data_class().count() == 4
    assert carddef.data_class().get(1).data['bo0'] is None
    assert carddef.data_class().get(2).data['bo0'] is None
    assert carddef.data_class().get(3).data['bo0'] is None
    assert carddef.data_class().get(4).data['bo0'] is None

    # target all cards
    update_action.target_id = '{{cards|objects:"%s"}}' % carddef.url_name
    wf.store()
    perform_items([update_action], formdata)
    assert carddef.data_class().get(1).data['bo0'] == '1'
    assert carddef.data_class().get(2).data['bo0'] == '1'
    assert carddef.data_class().get(3).data['bo0'] == '1'
    assert carddef.data_class().get(4).data['bo0'] == '1'
    status_part = [x for x in formdata.evolution[-1].parts if isinstance(x, ManyExternalCallsPart)][0]
    assert status_part.running is False
    assert status_part.is_hidden() is True
    assert '4 processed' in str(status_part.view())
    assert set(status_part.processed_ids) == {x.get_display_id() for x in carddef.data_class().select()}

    # target some cards
    update_action.target_id = (
        '{{cards|objects:"%s"|filter_by:"card_string"|filter_value:"Text 2"}}' % carddef.url_name
    )
    wf.store()
    perform_items([update_action], formdata)
    assert carddef.data_class().get(1).data['bo0'] == '1'
    assert carddef.data_class().get(2).data['bo0'] == '2'
    assert carddef.data_class().get(3).data['bo0'] == '1'
    assert carddef.data_class().get(4).data['bo0'] == '1'

    # target some cards with slice
    update_action.target_id = '{{cards|objects:"%s"|order_by:"id"|slice:":2"}}' % carddef.url_name
    wf.store()
    perform_items([update_action], formdata)
    assert carddef.data_class().get(1).data['bo0'] == '2'
    assert carddef.data_class().get(2).data['bo0'] == '3'
    assert carddef.data_class().get(3).data['bo0'] == '1'
    assert carddef.data_class().get(4).data['bo0'] == '1'

    # target a single formdata
    update_action.target_id = (
        '{{cards|objects:"%s"|filter_by:"card_string"|filter_value:"Text 2"|first}}' % carddef.url_name
    )
    wf.store()
    perform_items([update_action], formdata)
    assert carddef.data_class().get(1).data['bo0'] == '2'
    assert carddef.data_class().get(2).data['bo0'] == '4'
    assert carddef.data_class().get(3).data['bo0'] == '1'
    assert carddef.data_class().get(4).data['bo0'] == '1'

    # mismatch in target
    carddef2 = CardDef()
    carddef2.name = 'Other data'
    carddef2.fields = []
    carddef2.workflow = carddef_wf
    carddef2.store()

    update_action.slug = 'carddef:%s' % carddef2.url_name
    update_action.target_id = (
        '{{cards|objects:"%s"|filter_by:"card_string"|filter_value:"Text 2"}}' % carddef.url_name
    )
    wf.store()
    perform_items([update_action], formdata)
    assert LoggedError.count() == 1
    logged_error = LoggedError.select()[0]
    assert logged_error.summary == 'Mismatch in target objects: expected "Other data", got "Data"'

    # mismatch in target, with formdata
    LoggedError.wipe()
    update_action.target_id = (
        '{{cards|objects:"%s"|filter_by:"card_string"|filter_value:"Text 2"|first}}' % carddef.url_name
    )
    wf.store()
    perform_items([update_action], formdata)
    assert LoggedError.count() == 1
    logged_error = LoggedError.select()[0]
    assert logged_error.summary == 'Mismatch in target object: expected "Other data", got "Data"'


def test_call_external_workflow_manual_multiple_values_targeting(pub):
    FormDef.wipe()
    CardDef.wipe()
    Workflow.wipe()

    # carddef workflow, with global action to increment a counter in its
    # backoffice fields.
    carddef_wf = Workflow(name='Carddef Workflow')
    carddef_wf.add_status(name='New')
    carddef_wf.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(carddef_wf)
    carddef_wf.backoffice_fields_formdef.fields = [
        StringField(id='bo0', varname='bo', label='bo variable'),
    ]
    global_action = carddef_wf.add_global_action('Update')
    global_action.add_action('set-backoffice-fields')
    setbo = global_action.items[0]
    setbo.fields = [{'field_id': 'bo0', 'value': '{{ form_var_bo|default:"0"|add:1 }}'}]
    trigger = global_action.append_trigger('webservice')  # external call
    trigger.identifier = 'update'
    carddef_wf.store()

    # associated carddef
    carddef = CardDef()
    carddef.name = 'Data'
    carddef.fields = [
        StringField(id='0', label='string', varname='card_string'),
    ]
    carddef.workflow = carddef_wf
    carddef.store()
    carddef.data_class().wipe()

    # and sample carddatas
    for i in range(1, 5):
        carddata = carddef.data_class()()
        carddata.data = {'0': 'Text %s' % i}
        carddata.store()
        carddata.just_created()
        carddata.store()

    # formdef workflow that will trigger the global action
    wf = Workflow(name='External actions')
    wf.add_status('Blah')
    update_global_action = wf.add_global_action('Update linked object data')
    update_action = update_global_action.add_action('external_workflow_global_action')
    update_action.slug = 'carddef:%s' % carddef.url_name
    update_action.target_mode = 'manual'
    update_action.target_id = None  # not configured
    update_action.trigger_id = 'action:update'
    wf.store()

    # associated formdef
    formdef = FormDef()
    formdef.name = 'External action form'
    formdef.fields = []
    formdef.workflow = wf
    formdef.store()

    # and formdata
    formdata = formdef.data_class()()
    formdata.data = {}
    formdata.store()
    formdata.just_created()

    update_action.target_id = '1,3'
    wf.store()
    perform_items([update_action], formdata)
    assert carddef.data_class().get(1).data['bo0'] == '1'
    assert not carddef.data_class().get(2).data['bo0']
    assert carddef.data_class().get(3).data['bo0'] == '1'
    assert not carddef.data_class().get(4).data['bo0']

    update_action.target_id = '{{ "1,3"|split:"," }}'
    wf.store()
    perform_items([update_action], formdata)
    assert carddef.data_class().get(1).data['bo0'] == '2'
    assert not carddef.data_class().get(2).data['bo0']
    assert carddef.data_class().get(3).data['bo0'] == '2'
    assert not carddef.data_class().get(4).data['bo0']


def test_call_external_remove_self(pub):
    # formdef workflow calling carddef action, calling back to trigger removing formdef
    CardDef.wipe()
    FormDef.wipe()
    Workflow.wipe()

    wf_card = Workflow(name='call-back')
    wf_card.add_status('st1')
    wf_card.store()

    carddef = CardDef()
    carddef.name = 'My card'
    carddef.fields = [
        StringField(id='1', label='string'),
    ]
    carddef.workflow_id = wf_card.id
    carddef.store()

    carddata = carddef.data_class()()
    carddata.store()
    carddata.just_created()
    carddata.store()

    formdef = FormDef()
    formdef.name = 'source form'
    formdef.fields = []
    formdef.store()

    wf_form = Workflow(name='call-external')
    st1 = wf_form.add_status('st1')
    action = wf_form.add_global_action('Delete', 'delete')
    action.add_action('remove')
    trigger = action.append_trigger('webservice')
    trigger.identifier = 'delete'

    action = st1.add_action('external_workflow_global_action')
    action.slug = 'carddef:%s' % carddef.url_name
    action.trigger_id = 'action:call'
    action.target_mode = 'manual'
    action.target_id = '%s' % carddata.id
    wf_form.store()

    card_action = wf_card.add_global_action('Call back', 'call')
    card_trigger = card_action.append_trigger('webservice')
    card_trigger.identifier = 'call'
    action = card_action.add_action('external_workflow_global_action')
    action.slug = 'formdef:%s' % formdef.url_name
    action.trigger_id = 'action:delete'
    action.target_mode = 'manual'
    action.target_id = '{{ caller_form_internal_id }}'
    wf_card.store()

    formdef.workflow_id = wf_form.id
    formdef.store()

    formdata = formdef.data_class()()
    formdata.store()
    formdata.just_created()
    url = formdata.perform_workflow()
    with pytest.raises(KeyError):
        formdata.refresh_from_storage()  # was deleted
    assert url == 'http://example.net'  # forced abort, redirect to home


def test_call_external_workflow_remove_other_card(pub, admin_user):
    role = pub.role_class(name='bar1')
    role.store()
    admin_user.roles = [role.id]
    admin_user.store()

    FormDef.wipe()
    CardDef.wipe()
    Workflow.wipe()

    carddef_wf = Workflow(name='Carddef Workflow')
    carddef_wf.add_status('status')
    delete = carddef_wf.add_global_action('Delete', 'delete')
    delete.add_action('remove')
    trigger = delete.append_trigger('webservice')
    trigger.identifier = 'delete'
    carddef_wf.store()

    carddef = CardDef()
    carddef.name = 'Data'
    carddef.fields = [
        StringField(id='0', label='string', varname='card_string'),
    ]
    carddef.digest_templates = {'default': '{{ form_var_card_string }}'}
    carddef.workflow = carddef_wf
    carddef.store()

    carddata = carddef.data_class()()
    carddata.data = {'0': 'Text'}
    carddata.just_created()
    carddata.store()

    wf = Workflow(name='External actions')
    wf.add_status('Action')
    delete_global_action = wf.add_global_action('Delete external linked object', 'delete')
    delete_global_action.triggers[0].roles = admin_user.roles
    delete_action = delete_global_action.add_action('external_workflow_global_action')
    delete_action.slug = 'carddef:%s' % carddef.url_name
    delete_action.trigger_id = 'action:delete'
    wf.store()

    datasource = {'type': 'carddef:%s' % carddef.url_name}
    formdef = FormDef()
    formdef.name = 'External action form'
    formdef.fields = [
        ItemField(id='0', label='Card', varname='card', data_source=datasource),
    ]
    formdef.workflow = wf
    formdef.store()

    assert carddef.data_class().count() == 1

    formdata = formdef.data_class()()
    formdata.data = {'0': '1'}
    formdata.store()
    formdata.just_created()
    formdata.store()

    resp = login(get_app(pub), username='admin', password='admin').get(formdata.get_backoffice_url())
    resp = resp.forms['wf-actions'].submit('button-action-delete').follow()
    # the session message from the external workflow should not appear
    assert 'The card has been deleted.' not in resp.text


def test_option_label_with_repeated_names(pub, admin_user):
    role = pub.role_class(name='bar1')
    role.store()
    admin_user.roles = [role.id]
    admin_user.store()

    FormDef.wipe()
    CardDef.wipe()
    Workflow.wipe()

    carddef_wf = Workflow(name='Carddef Workflow')
    carddef_wf.add_status('status')
    delete = carddef_wf.add_global_action('Delete', 'delete')
    delete.add_action('remove')
    trigger = delete.append_trigger('webservice')
    trigger.identifier = 'delete1'
    carddef_wf.store()

    carddef = CardDef()
    carddef.name = 'Data'
    carddef.fields = [
        StringField(id='0', label='string', varname='card_string'),
    ]
    carddef.digest_templates = {'default': '{{ form_var_card_string }}'}
    carddef.workflow = carddef_wf
    carddef.store()

    wf = Workflow(name='External actions')
    wf.add_status('Action')
    delete_global_action = wf.add_global_action('Delete external linked object', 'delete')
    delete_global_action.triggers[0].roles = admin_user.roles
    delete_action = delete_global_action.add_action('external_workflow_global_action')
    delete_action.slug = 'carddef:%s' % carddef.url_name
    delete_action.trigger_id = 'action:delete'
    wf.store()

    datasource = {'type': 'carddef:%s' % carddef.url_name}
    formdef = FormDef()
    formdef.name = 'External action form'
    formdef.fields = [
        ItemField(id='0', label='Card', varname='card', data_source=datasource),
    ]
    formdef.workflow = wf
    formdef.store()

    resp = login(get_app(pub), username='admin', password='admin').get(delete_action.get_admin_url())
    assert [x[2] for x in resp.form['trigger_id'].options] == ['---', 'Delete']

    trigger2 = delete.append_trigger('webservice')
    trigger2.identifier = 'delete2'
    carddef_wf.store()

    resp = login(get_app(pub), username='admin', password='admin').get(delete_action.get_admin_url())
    assert [x[2] for x in resp.form['trigger_id'].options] == ['---', 'Delete [delete1]', 'Delete [delete2]']
