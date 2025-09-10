import datetime

import pytest
from quixote import cleanup

from wcs import fields, sessions
from wcs.carddef import CardDef
from wcs.fields import EmailField, ItemField, StringField
from wcs.formdef import FormDef
from wcs.logged_errors import LoggedError
from wcs.qommon.http_request import HTTPRequest
from wcs.qommon.upload_storage import PicklableUpload
from wcs.tracking_code import TrackingCode
from wcs.wf.create_formdata import Mapping
from wcs.workflow_traces import WorkflowTrace
from wcs.workflows import Workflow

from ..backoffice_pages.test_all import create_user as create_backoffice_user
from ..backoffice_pages.test_all import login
from ..form_pages.test_all import create_user
from ..utilities import clean_temporary_pub, create_temporary_pub, get_app


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


def test_create_formdata(pub):
    FormDef.wipe()
    TrackingCode.wipe()

    target_formdef = FormDef()
    target_formdef.name = 'target form'
    target_formdef.fields = [
        StringField(id='0', label='string', varname='foo_string'),
    ]
    target_formdef.store()

    wf = Workflow(name='create-formdata')
    wf.possible_status = Workflow.get_default_workflow().possible_status[:]
    create = wf.possible_status[1].add_action('create_formdata', id='_create', prepend=True)
    create.label = 'create a new linked form'
    create.varname = 'resubmitted'
    create.mappings = [
        Mapping(field_id='0', expression='{{ form_var_toto_string }}'),
        Mapping(field_id='1', expression='{{ form_var_toto_file_raw }}'),
        Mapping(field_id='2', expression='{ {form_var_toto_item_raw }}'),
    ]
    wf.store()

    source_formdef = FormDef()
    source_formdef.name = 'source form'
    source_formdef.fields = []
    source_formdef.workflow_id = wf.id
    source_formdef.store()

    formdata = source_formdef.data_class()()
    formdata.data = {}
    formdata.just_created()
    formdata.store()

    assert target_formdef.data_class().count() == 0
    assert LoggedError.count() == 0
    # check unconfigured action does nothing
    formdata.perform_workflow()
    assert target_formdef.data_class().count() == 0

    create.formdef_slug = target_formdef.url_name
    wf.store()
    assert target_formdef in wf.get_dependencies()
    del source_formdef._workflow
    formdata.perform_workflow()
    assert target_formdef.data_class().count() == 1
    # check evolutions & tracing
    target_formdata = target_formdef.data_class().select()[0]
    trace = WorkflowTrace.select_for_formdata(target_formdata)[0]
    assert trace.event == 'workflow-created'
    assert trace.event_args['external_workflow_id'] == wf.id
    assert trace.event_args['external_status_id'] == 'new'
    assert trace.event_args['external_item_id'] == '_create'
    trace = WorkflowTrace.select_for_formdata(formdata)[-1]
    assert trace.event == 'workflow-created-formdata'
    assert trace.event_args['external_formdef_id'] == target_formdef.id
    assert trace.event_args['external_formdata_id'] == target_formdata.id

    errors = LoggedError.select()
    assert len(errors) == 1
    assert errors[0].summary == 'Missing field: unknown (1), unknown (2)'
    assert errors[0].formdata_id == str(target_formdata.id)

    # add field labels cache
    LoggedError.wipe()
    target_formdef.data_class().wipe()
    create.formdef_slug = target_formdef.url_name
    create.cached_field_labels = {'0': 'field0', '1': 'field1', '2': 'field2'}
    wf.store()
    del source_formdef._workflow
    formdata.perform_workflow()
    assert target_formdef.data_class().count() == 1

    errors = LoggedError.select()
    assert len(errors) == 1
    assert errors[0].summary == 'Missing field: field1, field2'

    # no tracking code has been created
    created_formdata = target_formdef.data_class().select()[0]
    assert created_formdata.tracking_code is None
    assert TrackingCode.count() == 0
    # now we want one
    target_formdef.enable_tracking_codes = True
    target_formdef.store()
    pub.reset_caches()
    target_formdef.data_class().wipe()
    formdata.perform_workflow()
    # and a tracking code is created
    assert target_formdef.data_class().count() == 1
    created_formdata = target_formdef.data_class().select()[0]
    assert created_formdata.tracking_code is not None
    assert TrackingCode.count() == 1
    assert TrackingCode.select()[0].formdef_id == str(target_formdef.id)
    assert TrackingCode.select()[0].formdata_id == str(created_formdata.id)

    create.condition = {'type': 'django', 'value': '1 == 2'}
    wf.store()
    del source_formdef._workflow
    target_formdef.data_class().wipe()
    assert target_formdef.data_class().count() == 0
    formdata.perform_workflow()
    assert target_formdef.data_class().count() == 0


def test_create_formdata_migration(pub):
    wf = Workflow(name='create-formdata')
    st1 = wf.add_status('Status1', 'st1')

    create = st1.add_action('create_formdata', id='_create')
    create.label = 'create a new linked form'
    create.varname = 'resubmitted'
    create.mappings = [
        Mapping(field_id='0', expression='{{ form_var_toto_string }}'),
        Mapping(field_id='1', expression='{{ form_var_toto_file_raw }}'),
        Mapping(field_id='2', expression='{{ form_var_toto_item_raw }}'),
    ]
    create.keep_user = True
    wf.store()

    wf = Workflow.get(wf.id)
    assert wf.possible_status[0].items[0].user_association_mode == 'keep-user'
    assert not hasattr(wf.possible_status[0].items[0], 'keep_user')


def test_create_formdata_tracking_code(pub, emails):
    FormDef.wipe()
    TrackingCode.wipe()

    target_wf = Workflow(name='send-mail')
    st1 = target_wf.add_status('Status1', 'st1')
    item = st1.add_action('sendmail')
    item.to = ['bar@localhost']
    item.subject = 'Foobar'
    item.body = '{{ form_tracking_code }}'
    target_wf.store()

    target_formdef = FormDef()
    target_formdef.name = 'target-form'
    target_formdef.fields = [
        EmailField(id='0', label='Email'),
    ]
    target_formdef.workflow_id = target_wf.id
    target_formdef.enable_tracking_codes = True
    target_formdef.store()

    wf = Workflow(name='create-formdata')
    st1 = wf.add_status('Status1', 'st1')
    create = st1.add_action('create_formdata')
    create.formdef_slug = target_formdef.url_name
    create.mappings = [
        Mapping(field_id='0', expression='{{ form_var_email_string }}'),
    ]
    wf.store()

    formdef = FormDef()
    formdef.name = 'source form'
    formdef.fields = [
        EmailField(id='0', label='Email'),
    ]
    formdef.workflow_id = wf.id
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {}
    formdata.just_created()
    formdata.store()

    assert target_formdef.data_class().count() == 0
    assert emails.count() == 0

    formdata.perform_workflow()
    pub.process_after_jobs()
    assert target_formdef.data_class().count() == 1
    assert emails.count() == 1
    tracking_code = target_formdef.data_class().select()[0].tracking_code
    assert tracking_code in emails.get('Foobar')['payload']


def test_create_formdata_attach_to_history(pub):
    FormDef.wipe()
    TrackingCode.wipe()

    target_formdef = FormDef()
    target_formdef.name = 'target form'
    target_formdef.fields = [
        StringField(id='0', label='string', varname='foo_string'),
    ]
    target_formdef.store()
    target_formdef.data_class().wipe()

    wf = Workflow(name='create-formdata')
    wf.possible_status = Workflow.get_default_workflow().possible_status[:]
    create = wf.possible_status[1].add_action('create_formdata', id='_create', prepend=True)
    create.label = 'create a new linked form'
    create.varname = 'resubmitted'
    create.mappings = [
        Mapping(field_id='0', expression='{{ form_var_toto_string }}'),
        Mapping(field_id='1', expression='{{ form_var_toto_file_raw }}'),
        Mapping(field_id='2', expression='{{ form_var_toto_item_raw }}'),
    ]
    wf.store()

    source_formdef = FormDef()
    source_formdef.name = 'source form'
    source_formdef.fields = []
    source_formdef.workflow_id = wf.id
    source_formdef.store()

    formdata = source_formdef.data_class()()
    formdata.data = {}
    formdata.just_created()
    formdata.store()

    create.formdef_slug = target_formdef.url_name
    create.attach_to_history = True
    wf.store()

    del source_formdef._workflow
    formdata.perform_workflow()
    assert target_formdef.data_class().count() == 1
    assert formdata.evolution[-1].parts[0].attach_to_history is True
    assert 'New form "target form" created: <a href="%s1/">1-1</a>' % target_formdef.get_url() in str(
        formdata.evolution[-1].parts[0].view()
    )

    # display digest if it exists
    formdata.refresh_from_storage()
    target_formdef.digest_templates = {'default': 'hello'}
    target_formdef.store()
    target_formdata = target_formdef.data_class().get(1)
    target_formdata.store()  # update digests
    assert 'New form "target form" created: <a href="%s1/">1-1 (hello)</a>' % target_formdef.get_url() in str(
        formdata.evolution[-1].parts[0].view()
    )

    # don't crash in case target formdata is removed
    formdata.refresh_from_storage()
    target_formdef.data_class().wipe()
    assert 'New form created (deleted, 1-1)' in str(formdata.evolution[-1].parts[0].view())

    # don't crash in case target formdef is removed
    target_formdef.remove_self()
    formdata.refresh_from_storage()
    assert 'New form created (deleted, 1-1)' in str(formdata.evolution[-1].parts[0].view())


def test_create_formdata_card_item_mapping(pub):
    LoggedError.wipe()
    FormDef.wipe()
    CardDef.wipe()

    carddef = CardDef()
    carddef.name = 'Bar'
    carddef.fields = [
        StringField(id='0', label='Bar', varname='bar'),
    ]
    carddef.digest_templates = {'default': '{{ form_var_bar }}'}
    carddef.store()
    carddef.data_class().wipe()

    for i in range(0, 4):
        carddata = carddef.data_class()()
        carddata.data = {'0': 'Bar %s' % (i + 1)}
        carddata.just_created()
        carddata.store()

    target_formdef = FormDef()
    target_formdef.name = 'Foo'
    target_formdef.fields = [
        ItemField(id='0', label='Bar', data_source={'type': 'carddef:bar'}, required='optional'),
    ]
    target_formdef.store()
    target_formdef.data_class().wipe()

    wf = Workflow(name='create-formdata')
    wf.possible_status = Workflow.get_default_workflow().possible_status[:]
    create = wf.possible_status[1].add_action('create_formdata', id='_create', prepend=True)
    create.label = 'create a new linked form'
    create.formdef_slug = 'foo'
    create.mappings = [
        Mapping(field_id='0', expression='{{ form_var_foo_string|default:"" }}'),
    ]
    wf.store()

    source_formdef = FormDef()
    source_formdef.name = 'Foo'
    source_formdef.fields = [
        StringField(id='0', label='string', varname='foo_string'),
    ]
    source_formdef.workflow_id = wf.id
    source_formdef.store()
    source_formdef.data_class().wipe()

    # empty string a result -> None
    formdata = source_formdef.data_class()()
    formdata.data = {}
    formdata.just_created()
    formdata.store()
    formdata.perform_workflow()

    assert target_formdef.data_class().count() == 1
    target_formdata = target_formdef.data_class().select()[0]
    assert target_formdata.data.get('0') is None
    assert target_formdata.data.get('0_display') is None

    # valid value, using id
    target_formdef.data_class().wipe()
    formdata = source_formdef.data_class()()
    formdata.data = {'0': str(carddata.id)}
    formdata.just_created()
    formdata.store()
    formdata.perform_workflow()

    assert target_formdef.data_class().count() == 1
    target_formdata = target_formdef.data_class().select()[0]
    assert target_formdata.data.get('0') == str(carddata.id)
    assert target_formdata.data.get('0_display') == carddata.default_digest

    # valid value, using digest
    target_formdef.data_class().wipe()
    formdata = source_formdef.data_class()()
    formdata.data = {'0': carddata.default_digest}
    formdata.just_created()
    formdata.store()
    formdata.perform_workflow()

    assert target_formdef.data_class().count() == 1
    target_formdata = target_formdef.data_class().select()[0]
    assert target_formdata.data.get('0') == str(carddata.id)
    assert target_formdata.data.get('0_display') == carddata.default_digest

    # invalid value
    target_formdef.data_class().wipe()
    formdata = source_formdef.data_class()()
    formdata.data = {'0': 'XXX'}
    formdata.just_created()
    formdata.store()
    formdata.perform_workflow()

    assert target_formdef.data_class().count() == 1
    target_formdata = target_formdef.data_class().select()[0]
    assert target_formdata.data.get('0') is None
    assert target_formdata.data.get('0_display') is None
    assert LoggedError.count() == 1
    assert LoggedError.count() == 1
    error = LoggedError.select()[0]
    assert error.exception_message == "unknown card value ('XXX')"


def test_create_formdata_does_not_overwrite_initial_submission_context(pub):
    FormDef.wipe()
    TrackingCode.wipe()

    wf = Workflow(name='create-formdata')
    wf.possible_status = Workflow.get_default_workflow().possible_status[:]
    create = wf.possible_status[1].add_action('create_formdata', id='_create', prepend=True)
    create.label = 'create a new linked form'
    create.varname = 'resubmitted'
    create.keep_submission_context = True
    create.map_fields_by_varname = True
    # prevent recursive execution of create_formdata
    create.condition = {
        'type': 'django',
        'value': 'not form_submission_context_orig_formdata_id',
    }
    wf.store()

    target_formdef = FormDef()
    target_formdef.name = 'target form'
    target_formdef.fields = []
    target_formdef.workflow_id = wf.id
    target_formdef.store()

    create.formdef_slug = target_formdef.url_name
    wf.store()

    formdata = target_formdef.data_class()()
    formdata.data = {}
    formdata.submission_context = {'a': 'b'}
    formdata.just_created()
    formdata.store()

    assert target_formdef.data_class().count() == 1
    del target_formdef._workflow
    formdata.perform_workflow()
    assert target_formdef.data_class().count() == 2
    assert formdata.submission_context == {'a': 'b'}


@pytest.mark.parametrize('submitter_is_triggerer', [True, False])
def test_anonymise_action_unlink_user(pub, submitter_is_triggerer):
    CardDef.wipe()
    FormDef.wipe()
    pub.user_class.wipe()

    user = pub.user_class()
    user.email = 'test@example.net'
    user.name_identifiers = ['xyz']
    user.store()

    if submitter_is_triggerer:
        pub.get_request()._user = ()
        pub.get_request().session = sessions.BasicSession(id=1)
        pub.get_request().session.set_user(user.id)

    wf = Workflow(name='test-unlink-user')
    wf.possible_status = Workflow.get_default_workflow().possible_status[:]
    anonymise = wf.possible_status[1].add_action('anonymise', id='_anonymise', prepend=True)
    anonymise.label = 'Unlink User'
    anonymise.varname = 'mycard'
    anonymise.mode = 'unlink_user'
    wf.store()

    formdef = FormDef()
    formdef.name = 'Person'
    formdef.workflow_id = wf.id
    formdef.enable_tracking_codes = True
    formdef.store()
    formdef.data_class().wipe()

    formdata = formdef.data_class()()
    formdata.data = {'0': 'Foo', '1': 'Bar'}
    formdata.user_id = user.id
    formdata.just_created()
    formdata.store()

    code = TrackingCode()
    code.formdata = formdata

    assert formdef.data_class().select()[0].user.id == user.id
    assert formdef.data_class().select()[0].tracking_code

    formdata.perform_workflow()

    assert formdef.data_class().select()[0].user is None
    assert formdef.data_class().select()[0].tracking_code is None
    assert (
        pub.get_request().session.is_anonymous_submitter(formdef.data_class().select()[0])
        is submitter_is_triggerer
    )


def test_recursive_create_formdata(pub):
    FormDef.wipe()
    LoggedError.wipe()

    formdef = FormDef()
    formdef.name = 'test form'
    formdef.fields = [
        StringField(id='0', label='string', varname='foo_string'),
    ]
    formdef.store()

    wf = Workflow(name='create-formdata')
    wf.possible_status = Workflow.get_default_workflow().possible_status[:]
    create = wf.possible_status[1].add_action('create_formdata', id='_create', prepend=True)
    create.label = 'create a new linked form'
    create.varname = 'resubmitted'
    create.mappings = [Mapping(field_id='0', expression='test')]
    create.formdef_slug = formdef.url_name
    wf.store()

    formdef.workflow_id = wf.id
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {}
    formdata.just_created()
    formdata.store()
    formdata.perform_workflow()

    assert formdef.data_class().count() == 2
    assert LoggedError.count() == 1
    error = LoggedError.select()[0]
    assert error.formdef_id == str(formdef.id)
    assert error.workflow_id == str(wf.id)
    assert error.status_item_id == '_create'
    assert error.status_id == 'new'
    assert error.summary == 'Detected recursive creation of forms'


def test_recursive_create_formdata_with_subformdata(pub):
    FormDef.wipe()
    LoggedError.wipe()

    # simple sub formdef, which workflow does not create other related formdatas.
    subformdef = FormDef()
    subformdef.name = 'test subform'
    subformdef.fields = [
        StringField(id='0', label='string', varname='foo_string'),
    ]
    subformdef.store()

    formdef = FormDef()
    formdef.name = 'test form'
    formdef.fields = [
        StringField(id='0', label='string', varname='foo_string'),
    ]
    formdef.store()

    wf = Workflow(name='create-formdata')
    wf.possible_status = Workflow.get_default_workflow().possible_status[:]
    create = wf.possible_status[1].add_action('create_formdata', id='_create1_wf', prepend=True)
    create.label = 'create a sub form'
    create.varname = 'resubmitted'
    create.mappings = [Mapping(field_id='0', expression='test')]
    create.formdef_slug = subformdef.url_name
    create2 = wf.possible_status[1].add_action('create_formdata', id='_create2_wf', prepend=True)
    create2.label = 'create a sub form 2, the same'
    create2.varname = 'resubmitted'
    create2.mappings = [Mapping(field_id='0', expression='test')]
    create2.formdef_slug = subformdef.url_name
    wf.store()

    formdef.workflow_id = wf.id
    formdef.store()
    pub.reset_caches()

    formdata = formdef.data_class()()
    formdata.data = {}
    formdata.just_created()
    formdata.store()
    formdata.perform_workflow()

    assert formdef.data_class().count() == 1
    assert subformdef.data_class().count() == 2
    assert LoggedError.count() == 0

    formdef.data_class().wipe()
    subformdef.data_class().wipe()

    # now add formdata creation in subformdef workflow
    subsubformdef = FormDef()
    subsubformdef.name = 'test subsubform'
    subsubformdef.fields = [
        StringField(id='0', label='string', varname='foo_string'),
    ]
    subsubformdef.store()
    pub.reset_caches()

    subwf = Workflow(name='create-formdata-again')
    subwf.possible_status = Workflow.get_default_workflow().possible_status[:]
    create = subwf.possible_status[1].add_action('create_formdata', id='_create1_subwf', prepend=True)
    create.label = 'create a subsub form'
    create.varname = 'resubmitted'
    create.mappings = [Mapping(field_id='0', expression='test')]
    create.formdef_slug = subsubformdef.url_name
    subwf.store()

    subformdef.workflow_id = subwf.id
    subformdef.store()
    pub.reset_caches()

    formdata = formdef.data_class()()
    formdata.data = {}
    formdata.just_created()
    formdata.store()
    formdata.perform_workflow()

    assert formdef.data_class().count() == 1
    assert subformdef.data_class().count() == 2  # from two actions
    assert subsubformdef.data_class().count() == 2  # one per subformdef created above
    assert LoggedError.count() == 0  # no error detected


def test_global_timeouts_create_formdata(pub):
    FormDef.wipe()
    Workflow.wipe()

    # simple sub formdef, which workflow does not create other related formdatas.
    subformdef = FormDef()
    subformdef.name = 'test subform'
    subformdef.fields = [
        StringField(id='0', label='string', varname='foo_string'),
    ]
    subformdef.store()

    workflow = Workflow(name='global-timeouts')
    workflow.possible_status = Workflow.get_default_workflow().possible_status[:]
    action = workflow.add_global_action('Timeout Test')
    create = action.add_action('create_formdata')
    create.label = 'create a sub form'
    create.varname = 'resubmitted'
    create.mappings = [Mapping(field_id='0', expression='test')]
    create.formdef_slug = subformdef.url_name
    trigger = action.append_trigger('timeout')
    trigger.anchor = 'template'
    trigger.anchor_template = '{{ "%s" }}' % (
        datetime.datetime.today() - datetime.timedelta(days=3)
    ).strftime('%Y-%m-%d')
    trigger.timeout = '2'
    workflow.store()

    formdef = FormDef()
    formdef.name = 'test form'
    formdef.fields = [
        StringField(id='0', label='string', varname='foo_string'),
    ]
    formdef.workflow = workflow
    formdef.store()

    formdef.data_class().wipe()
    subformdef.data_class().wipe()

    formdata = formdef.data_class()()
    formdata.data = {}
    formdata.just_created()
    formdata.store()
    formdata.jump_status('new')

    pub.apply_global_action_timeouts()

    assert subformdef.data_class().count() == 1


@pytest.fixture(params=[{'attach_to_history': True}, {}])
def create_formdata(request, pub):
    admin = create_backoffice_user(pub, is_admin=True)

    FormDef.wipe()

    source_formdef = FormDef()
    source_formdef.name = 'source form'
    source_formdef.workflow_roles = {'_receiver': 1}
    source_formdef.fields = [
        fields.StringField(id='0', label='string', varname='toto_string'),
        fields.FileField(id='1', label='file', varname='toto_file'),
    ]
    source_formdef.store()

    target_formdef = FormDef()
    target_formdef.name = 'target form'
    target_formdef.workflow_roles = {'_receiver': 1}
    target_formdef.backoffice_submission_roles = admin.roles[:]
    target_formdef.fields = [
        fields.StringField(id='0', label='string', varname='foo_string'),
        fields.FileField(id='1', label='file', varname='foo_file'),
    ]
    target_formdef.store()
    wf = Workflow(name='create-formdata')

    st1 = wf.add_status('New')
    st2 = wf.add_status('Resubmit')

    jump = st1.add_action('choice', id='_resubmit')
    jump.label = 'Resubmit'
    jump.by = ['_receiver']
    jump.status = st2.id

    create_formdata = st2.add_action('create_formdata', id='_create_formdata')
    create_formdata.varname = 'resubmitted'
    create_formdata.draft = True
    create_formdata.formdef_slug = target_formdef.url_name
    create_formdata.user_association_mode = 'keep-user'
    create_formdata.backoffice_submission = True
    create_formdata.attach_to_history = request.param.get('attach_to_history', False)
    create_formdata.mappings = [
        Mapping(field_id='0', expression='{{ form_var_toto_string }}'),
        Mapping(field_id='1', expression='{{ form_var_toto_file_raw }}'),
    ]

    redirect = st2.add_action('redirect_to_url', id='_redirect')
    redirect.url = '{{ form_links_resubmitted.form_backoffice_url }}'

    jump = st2.add_action('jumponsubmit', id='_jump')
    jump.status = st1.id

    wf.store()
    source_formdef.workflow_id = wf.id
    source_formdef.store()
    source_formdef.data_class().wipe()
    target_formdef.data_class().wipe()
    return locals()


def test_backoffice_create_formdata_backoffice_submission(pub, create_formdata):
    # create submitting user
    user = create_formdata['pub'].user_class()
    user.name = 'Jean Darmette'
    user.email = 'jean.darmette@triffouilis.fr'
    user.store()

    # create source formdata
    formdata = create_formdata['source_formdef'].data_class()()
    upload = PicklableUpload('/foo/bar', content_type='text/plain')
    upload.receive([b'hello world'])
    formdata.data = {
        '0': 'coucou',
        '1': upload,
    }
    formdata.user = user
    formdata.just_created()
    formdata.store()
    formdata.perform_workflow()

    # agent login and go to backoffice management pages
    app = get_app(create_formdata['pub'])
    app = login(app)
    resp = app.get(create_formdata['source_formdef'].get_url(backoffice=True))

    # click on first available formdata
    resp = resp.click('%s-%s' % (create_formdata['source_formdef'].id, formdata.id))
    target_data_class = create_formdata['target_formdef'].data_class()
    assert target_data_class.count() == 0
    # resubmit it through backoffice submission
    resp = resp.form.submit(name='button_resubmit')
    assert LoggedError.count() == 0
    assert target_data_class.count() == 1
    target_formdata = target_data_class.select()[0]

    assert target_formdata.submission_context == {
        'orig_object_type': 'formdef',
        'orig_formdata_id': '1',
        'orig_formdef_id': '1',
    }
    assert target_formdata.submission_agent_id == str(create_formdata['admin'].id)
    assert target_formdata.user.id == user.id
    assert target_formdata.status == 'draft'
    assert target_formdata.receipt_time
    assert resp.location == 'http://example.net/backoffice/management/target-form/%s/' % target_formdata.id
    resp = resp.follow()
    assert resp.location == 'http://example.net/backoffice/submission/target-form/%s/' % target_formdata.id
    resp = resp.follow()
    # second redirect with magic-token
    resp = resp.follow()
    assert resp.pyquery('.user-selection option').attr.value == str(user.id)
    resp = resp.form.submit(name='submit')  # -> validation
    resp = resp.form.submit(name='submit')  # -> submission
    target_formdata = target_data_class.get(id=target_formdata.id)
    assert target_formdata.user.id == user.id
    assert target_formdata.status == 'wf-new'
    resp = resp.follow()
    pq = resp.pyquery.remove_namespaces()
    assert pq('.field-type-string .value').text() == 'coucou'
    assert pq('.field-type-file .value').text() == 'bar'


def test_linked_forms_variables(pub, create_formdata):
    # create source formdata
    formdata = create_formdata['source_formdef'].data_class()()
    upload = PicklableUpload('/foo/bar', content_type='text/plain')
    upload.receive([b'hello world'])
    formdata.data = {
        '0': 'coucou',
        '1': upload,
    }
    formdata.just_created()
    formdata.store()
    formdata.perform_workflow()
    formdata.store()
    formdata.jump_status('2')
    formdata.perform_workflow()
    formdata.store()

    pub.substitutions.reset()
    pub.substitutions.feed(formdata)
    substvars = pub.substitutions.get_context_variables(mode='lazy')
    assert str(substvars['form_links_resubmitted_form_var_foo_string']) == 'coucou'
    assert 'form_links_resubmitted_form_var_foo_string' in substvars.get_flat_keys()

    source_formdata = create_formdata['source_formdef'].data_class().select()[0]

    app = get_app(create_formdata['pub'])
    app = login(app)
    resp = app.get(source_formdata.get_url(backoffice=True) + 'inspect')
    assert '?expand=form_links_resubmitted' in resp
    resp = app.get(source_formdata.get_url(backoffice=True) + 'inspect?expand=form_links_resubmitted')
    assert 'form_links_resubmitted_form_var_foo_string' in resp

    # delete target formdata
    create_formdata['target_formdef'].data_class().wipe()
    resp = app.get(source_formdata.get_url(backoffice=True) + 'inspect')
    assert '?expand=form_links_resubmitted' not in resp
    assert 'form_links_resubmitted_form_var_foo_string' not in resp

    # delete target formdef
    create_formdata['target_formdef'].remove_self()
    resp = app.get(source_formdata.get_url(backoffice=True) + 'inspect')


def test_backoffice_create_formdata_map_fields_by_varname(pub, create_formdata):
    create_formdata['create_formdata'].map_fields_by_varname = True
    create_formdata['create_formdata'].mappings = []
    create_formdata['wf'].store()
    create_formdata['source_formdef'].fields = [
        fields.StringField(id='0', label='string', varname='string0'),
        fields.FileField(id='1', label='file', varname='file1'),
        fields.StringField(id='2', label='string', varname='string2', required='optional'),
        fields.FileField(id='3', label='file', varname='file3', required='optional'),
    ]
    create_formdata['source_formdef'].store()
    create_formdata['target_formdef'].fields = [
        fields.StringField(id='0', label='string', varname='string0'),
        fields.FileField(id='1', label='file', varname='file1'),
        fields.StringField(id='2', label='string', varname='string2', required='optional'),
        fields.FileField(id='3', label='file', varname='file3', required='optional'),
    ]
    create_formdata['target_formdef'].store()

    # create submitting user
    user = create_formdata['pub'].user_class()
    user.name = 'Jean Darmette'
    user.email = 'jean.darmette@triffouilis.fr'
    user.store()

    # create source formdata
    create_formdata['source_formdef'].digest_templates = {'default': 'blah'}
    create_formdata['source_formdef'].store()
    formdata = create_formdata['source_formdef'].data_class()()
    create_formdata['formdata'] = formdata
    upload = PicklableUpload('/foo/bar', content_type='text/plain')
    upload.receive([b'hello world'])
    formdata.data = {
        '0': 'coucou',
        '1': upload,
    }
    formdata.user = user
    formdata.just_created()
    formdata.store()
    formdata.perform_workflow()

    # agent login and go to backoffice management pages
    app = get_app(create_formdata['pub'])
    app = login(app)
    resp = app.get(create_formdata['source_formdef'].get_url(backoffice=True))

    # click on first available formdata
    resp = resp.click('%s-%s' % (create_formdata['source_formdef'].id, formdata.id))
    target_data_class = create_formdata['target_formdef'].data_class()
    assert target_data_class.count() == 0
    # resubmit it through backoffice submission
    resp = resp.form.submit(name='button_resubmit')
    assert LoggedError.count() == 0
    assert target_data_class.count() == 1
    target_formdata = target_data_class.select()[0]

    assert target_formdata.submission_context == {
        'orig_object_type': 'formdef',
        'orig_formdata_id': '1',
        'orig_formdef_id': '1',
    }
    assert target_formdata.submission_agent_id == str(create_formdata['admin'].id)
    assert target_formdata.user.id == user.id
    assert target_formdata.status == 'draft'
    assert resp.location == 'http://example.net/backoffice/management/target-form/%s/' % target_formdata.id
    resp = resp.follow()
    assert resp.location == 'http://example.net/backoffice/submission/target-form/%s/' % target_formdata.id
    resp = resp.follow()
    # second redirect with magic-token
    resp = resp.follow()
    # check parent form is displayed in sidebar
    assert resp.pyquery('.extra-context--orig-data').attr.href == formdata.get_backoffice_url()
    assert resp.pyquery('.extra-context--orig-data').text() == 'source form #1-1 (blah)'
    resp = resp.form.submit(name='submit')  # -> validation
    resp = resp.form.submit(name='submit')  # -> submission
    target_formdata = target_data_class.get(id=target_formdata.id)
    assert target_formdata.user.id == user.id
    assert target_formdata.status == 'wf-new'
    resp = resp.follow()
    pq = resp.pyquery.remove_namespaces()
    assert pq('.field-type-string .value').text() == 'coucou'
    assert pq('.field-type-file .value').text() == 'bar'

    resp = app.get(create_formdata['formdata'].get_url(backoffice=True))
    pq = resp.pyquery.remove_namespaces()
    assert pq('.field-type-string .value').text() == 'coucou'
    if create_formdata['create_formdata'].attach_to_history:
        assert pq('.wf-links')
    else:
        assert not pq('.wf-links')


def test_backoffice_create_formdata_map_fields_by_varname_plus_empty(pub, create_formdata):
    create_formdata['create_formdata'].map_fields_by_varname = True
    create_formdata['create_formdata'].mappings = [
        Mapping(field_id='0', expression=None),
    ]
    create_formdata['wf'].store()
    create_formdata['source_formdef'].fields = [
        fields.StringField(id='0', label='string', varname='string0'),
        fields.StringField(id='2', label='string', varname='string2', required='optional'),
    ]
    create_formdata['source_formdef'].store()
    create_formdata['target_formdef'].fields = [
        fields.StringField(id='0', label='string', varname='string0'),
        fields.StringField(id='2', label='string', varname='string2', required='optional'),
    ]
    create_formdata['target_formdef'].store()

    # create submitting user
    user = create_formdata['pub'].user_class()
    user.name = 'Jean Darmette'
    user.email = 'jean.darmette@triffouilis.fr'
    user.store()

    # create source formdata
    formdata = create_formdata['source_formdef'].data_class()()
    create_formdata['formdata'] = formdata
    formdata.data = {
        '0': 'foo',
        '2': 'bar',
    }
    formdata.user = user
    formdata.just_created()
    formdata.store()
    formdata.perform_workflow()

    # agent login and go to backoffice management pages
    app = get_app(create_formdata['pub'])
    app = login(app)
    resp = app.get(create_formdata['source_formdef'].get_url(backoffice=True))

    # click on first available formdata
    resp = resp.click('%s-%s' % (create_formdata['source_formdef'].id, formdata.id))
    target_data_class = create_formdata['target_formdef'].data_class()
    assert target_data_class.count() == 0
    # resubmit it through backoffice submission
    resp = resp.form.submit(name='button_resubmit')
    assert target_data_class.count() == 1
    target_formdata = target_data_class.select()[0]

    assert target_formdata.submission_context == {
        'orig_object_type': 'formdef',
        'orig_formdata_id': '1',
        'orig_formdef_id': '1',
    }
    assert target_formdata.submission_agent_id == str(create_formdata['admin'].id)
    assert target_formdata.user.id == user.id
    assert target_formdata.status == 'draft'
    assert target_formdata.data == {'0': None, '2': 'bar'}


def test_create_formdata_show_link_in_history(pub):
    FormDef.wipe()
    TrackingCode.wipe()

    target_formdef = FormDef()
    target_formdef.name = 'target-form'
    target_formdef.fields = [
        fields.StringField(id='0', label='string', varname='foo_string'),
    ]
    target_formdef.store()

    wf = Workflow(name='create-formdata')
    wf.possible_status = Workflow.get_default_workflow().possible_status[:]
    create = wf.possible_status[1].add_action('create_formdata', id='_create', prepend=True)
    create.label = 'create a new linked form'
    create.varname = 'resubmitted'
    create.mappings = [
        Mapping(field_id='0', expression='coincoin'),
    ]
    wf.store()

    source_formdef = FormDef()
    source_formdef.name = 'source-form'
    source_formdef.fields = []
    source_formdef.workflow_id = wf.id
    source_formdef.enable_tracking_codes = True
    source_formdef.store()

    create.formdef_slug = target_formdef.url_name
    create.attach_to_history = True
    wf.store()

    source_formdef.data_class().wipe()
    target_formdef.data_class().wipe()

    create_user(pub)
    app = login(get_app(pub), username='foo', password='foo')
    resp = app.get('/source-form/')
    resp = resp.forms[0].submit('submit')
    assert 'Check values then click submit.' in resp.text
    resp = resp.forms[0].submit('submit')
    assert resp.status_int == 302
    resp = resp.follow()
    assert 'The form has been recorded' in resp.text

    formdata = source_formdef.data_class().select()[0]

    # logged access: show link to created formdata
    resp = app.get('/source-form/%s/' % formdata.id)
    assert 'The form has been recorded on' in resp.text
    assert 'New form "target-form" created' in resp.text
    assert resp.pyquery('.wf-links a')

    # anonymous access via tracking code: no link
    app = get_app(pub)
    resp = app.get('/code/%s/load' % formdata.tracking_code)
    resp = resp.follow()
    assert 'The form has been recorded on' in resp.text
    assert 'New form "target-form" created' not in resp.text
    assert not resp.pyquery('.wf-links a')


def test_create_formdata_multiple(pub):
    FormDef.wipe()
    TrackingCode.wipe()

    target_formdef = FormDef()
    target_formdef.name = 'target-form'
    target_formdef.fields = [
        fields.StringField(id='0', label='string', varname='foo_string'),
    ]
    target_formdef.store()

    wf = Workflow(name='create-formdata')
    wf.possible_status = Workflow.get_default_workflow().possible_status[:]
    global_action = wf.add_global_action('create formdata')
    trigger = global_action.triggers[0]
    trigger.roles = ['_submitter']
    create = global_action.add_action('create_formdata')
    create.label = 'create a new linked form'
    create.varname = 'resubmitted'
    create.mappings = [Mapping(field_id='0', expression='plop')]
    wf.store()

    source_formdef = FormDef()
    source_formdef.name = 'source-form'
    source_formdef.fields = []
    source_formdef.workflow_id = wf.id
    source_formdef.enable_tracking_codes = True
    source_formdef.store()

    create.formdef_slug = target_formdef.url_name
    wf.store()

    source_formdef.data_class().wipe()
    target_formdef.data_class().wipe()

    user = create_user(pub)

    formdata = source_formdef.data_class()()
    formdata.user_id = user.id
    formdata.just_created()
    formdata.store()

    formdata2 = source_formdef.data_class()()
    formdata2.user_id = user.id
    formdata2.just_created()
    formdata2.store()

    app = login(get_app(pub), username='foo', password='foo')
    resp = app.get(formdata.get_url())

    resp = resp.form.submit('button-action-1')
    assert target_formdef.data_class().count() == 1

    resp = app.get(formdata.get_url())
    resp = resp.form.submit('button-action-1')
    assert target_formdef.data_class().count() == 2

    # do it from another formdata (should not trigger recursive call detection)
    resp = app.get(formdata2.get_url())
    resp = resp.form.submit('button-action-1')
    assert target_formdef.data_class().count() == 3


@pytest.mark.parametrize('mode', ['single', 'partial'])
def test_create_formdata_edit_single_or_partial_pages(pub, mode):
    FormDef.wipe()
    TrackingCode.wipe()

    target_formdef = FormDef()
    target_formdef.name = 'target-form'
    target_formdef.fields = [
        fields.PageField(id='1', label='page1'),
        fields.StringField(id='2', label='string', varname='foo_string'),
        fields.PageField(id='3', label='page2', varname='page2'),
        fields.StringField(id='4', label='string2', varname='bar_string'),
        fields.PageField(id='4', label='page3'),
    ]
    target_formdef.store()

    wf = Workflow(name='create-formdata')
    wf.possible_status = Workflow.get_default_workflow().possible_status[:]
    create = wf.possible_status[1].add_action('create_formdata', id='_create', prepend=True)
    create.label = 'create a new linked form'
    create.varname = 'resubmitted'
    create.draft = True
    create.formdef_slug = target_formdef.url_name
    create.attach_to_history = True
    create.draft_edit_operation_mode = mode
    create.page_identifier = 'page2'
    create.mappings = [
        Mapping(field_id='2', expression='blah1'),
        Mapping(field_id='4', expression='blah2'),
    ]
    wf.store()

    source_formdef = FormDef()
    source_formdef.name = 'source-form'
    source_formdef.fields = []
    source_formdef.workflow_id = wf.id
    source_formdef.enable_tracking_codes = True
    source_formdef.store()

    source_formdef.data_class().wipe()
    target_formdef.data_class().wipe()

    create_user(pub)
    app = login(get_app(pub), username='foo', password='foo')
    resp = app.get('/source-form/')
    resp = resp.forms[0].submit('submit')  # -> validation
    resp = resp.forms[0].submit('submit').follow()  # -> submit
    assert 'The form has been recorded' in resp.text

    created_url = resp.pyquery('.wf-links a')[0].attrib['href']
    resp = app.get(created_url).follow()

    if mode == 'single':
        assert resp.pyquery('.wcs-step').length == 2
    else:
        assert resp.pyquery('.wcs-step').length == 3
    assert resp.pyquery('.wcs-step.current .label').text() == 'page2 (current step)'
    assert resp.forms[1]['f4'].value == 'blah2'

    if mode == 'partial':
        resp = resp.forms[1].submit('submit')  # -> page 3
        assert resp.pyquery('.wcs-step.current .label').text() == 'page3 (current step)'

    resp = resp.forms[1].submit('submit')  # -> validation
    resp = resp.forms[1].submit('submit')  # -> submit
    assert target_formdef.data_class().count() == 1
    formdata = target_formdef.data_class().select()[0]
    assert formdata.data == {'2': 'blah1', '4': 'blah2'}
