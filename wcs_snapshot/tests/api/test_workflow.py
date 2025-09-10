import base64
import os

import pytest
from django.utils.timezone import now

from wcs import fields
from wcs.formdef import FormDef
from wcs.qommon.http_request import HTTPRequest
from wcs.qommon.ident.password_accounts import PasswordAccount
from wcs.qommon.substitution import CompatibilityNamesDict
from wcs.sql import ApiAccess
from wcs.wf.register_comment import JournalEvolutionPart
from wcs.workflows import Workflow, WorkflowBackofficeFieldsFormDef

from ..utilities import clean_temporary_pub, create_temporary_pub, get_app, login
from .utils import sign_uri


@pytest.fixture
def pub(emails):
    pub = create_temporary_pub()

    req = HTTPRequest(None, {'SCRIPT_NAME': '/', 'SERVER_NAME': 'example.net'})
    pub.set_app_dir(req)
    pub.cfg['identification'] = {'methods': ['password']}
    pub.cfg['language'] = {'language': 'en'}
    pub.write_cfg()

    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        fd.write(
            '''\
[api-secrets]
coucou = 1234
'''
        )

    return pub


def teardown_module(module):
    clean_temporary_pub()


def test_workflow_trigger(pub, local_user):
    workflow = Workflow(name='test')
    st1 = workflow.add_status('Status1', 'st1')
    jump = st1.add_action('jump')
    jump.trigger = 'XXX'
    jump.mode = 'trigger'
    jump.status = 'st2'
    workflow.add_status('Status2', 'st2')
    workflow.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = []
    formdef.workflow_id = workflow.id
    formdef.store()

    formdef.data_class().wipe()
    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()
    assert formdef.data_class().get(formdata.id).status == 'wf-st1'

    # incomplete URL
    get_app(pub).post(sign_uri(formdata.get_url() + 'jump/'), status=404)
    get_app(pub).post(sign_uri(formdata.get_url() + 'jump'), status=404)
    get_app(pub).post(sign_uri(formdata.get_url() + 'jump/trigger/'), status=404)
    get_app(pub).post(sign_uri(formdata.get_url() + 'jump/trigger'), status=404)

    get_app(pub).post(sign_uri(formdata.get_url() + 'jump/trigger/XXX'), status=200)
    assert formdef.data_class().get(formdata.id).status == 'wf-st2'
    assert formdef.data_class().get(formdata.id).evolution[-1].who is None

    # check with trailing slash
    formdata.store()  # reset
    get_app(pub).post(sign_uri(formdata.get_url() + 'jump/trigger/XXX/'), status=200)
    assert formdef.data_class().get(formdata.id).status == 'wf-st2'

    # verify trigger presence (not-404 response)
    formdata.store()  # reset
    resp = get_app(pub).get(
        sign_uri(formdata.get_url() + 'jump/trigger/XXX'), headers={'accept': 'application/json'}, status=403
    )  # not 404: ok
    assert resp.json['err_desc'] == 'Wrong HTTP method (must be POST or PUT).'
    assert formdef.data_class().get(formdata.id).status == 'wf-st1'
    get_app(pub).get(sign_uri(formdata.get_url() + 'jump/trigger/ABC'), status=404)
    # jump, and then test trigger is not available
    get_app(pub).post(sign_uri(formdata.get_url() + 'jump/trigger/XXX'), status=200)
    assert formdef.data_class().get(formdata.id).status == 'wf-st2'
    get_app(pub).get(sign_uri(formdata.get_url() + 'jump/trigger/XXX'), status=404)

    pub.role_class.wipe()
    role = pub.role_class(name='xxx')
    role.store()

    jump.by = [role.id]
    workflow.store()

    formdata.store()  # (will get back to wf-st1)
    get_app(pub).post(sign_uri(formdata.get_url() + 'jump/trigger/XXX'), status=403)

    get_app(pub).post(sign_uri(formdata.get_url() + 'jump/trigger/XXX', user=local_user), status=403)

    local_user.roles = [role.id]
    local_user.store()
    get_app(pub).post(sign_uri(formdata.get_url() + 'jump/trigger/XXX', user=local_user), status=200)

    # check with invalid status
    formdata.jump_status('st1')
    formdata.store()
    jump.status = 'invalid'
    workflow.store()
    resp = get_app(pub).post(sign_uri(formdata.get_url() + 'jump/trigger/XXX', user=local_user), status=400)
    assert resp.json['err_desc'] == 'Broken jump or missing target status.'
    assert formdef.data_class().get(formdata.id).status == 'wf-st1'


def test_workflow_trigger_with_data(pub, local_user):
    workflow = Workflow(name='test')
    st1 = workflow.add_status('Status1', 'st1')
    jump = st1.add_action('jump')
    jump.trigger = 'xx-yy'
    jump.mode = 'trigger'
    jump.status = 'st2'
    workflow.add_status('Status2', 'st2')
    workflow.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = []
    formdef.workflow_id = workflow.id
    formdef.store()

    formdef.data_class().wipe()
    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()

    get_app(pub).post_json(
        sign_uri(formdata.get_url() + 'jump/trigger/xx-yy'), status=200, params={'test': 'data'}
    )
    assert formdef.data_class().get(formdata.id).status == 'wf-st2'
    # unstructured storage:
    assert formdef.data_class().get(formdata.id).workflow_data == {'test': 'data'}
    # structured storage:
    formdata.refresh_from_storage()
    substvars = CompatibilityNamesDict()
    substvars.update(formdata.get_substitution_variables())
    assert 'form_trigger_xx_yy_content_test' in substvars.get_flat_keys()
    assert substvars['form_trigger_xx_yy_content_test'] == 'data'
    assert 'form_trigger_xx_yy_datetime' in substvars.get_flat_keys()
    assert 'form_trigger_xx_yy_kind' in substvars.get_flat_keys()
    assert substvars['form_trigger_xx_yy_kind'] == 'jump'
    assert 'form_trigger_xx_yy_0_content_test' in substvars.get_flat_keys()
    assert substvars['form_trigger_xx_yy_0_content_test'] == 'data'
    assert 'form_trigger_xx_yy_0_datetime' in substvars.get_flat_keys()
    assert 'form_trigger_xx_yy_0_kind' in substvars.get_flat_keys()
    assert substvars['form_trigger_xx_yy_0_kind'] == 'jump'
    assert len(substvars['form_trigger_xx_yy']) == 1
    for trigger in substvars['form_trigger_xx_yy']:  # noqa pylint: disable=not-an-iterable
        assert trigger.kind == 'jump'

    # post with empty dictionary
    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()
    get_app(pub).post_json(sign_uri(formdata.get_url() + 'jump/trigger/xx-yy'), status=200, params={})
    assert formdef.data_class().get(formdata.id).status == 'wf-st2'
    assert not formdef.data_class().get(formdata.id).workflow_data

    # post with empty data
    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()
    get_app(pub).post(sign_uri(formdata.get_url() + 'jump/trigger/xx-yy'), status=200)
    assert formdef.data_class().get(formdata.id).status == 'wf-st2'
    assert not formdef.data_class().get(formdata.id).workflow_data

    # post with empty data, but declare json content-type
    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()
    get_app(pub).post(
        sign_uri(formdata.get_url() + 'jump/trigger/xx-yy'),
        status=200,
        headers={'content-type': 'application/json'},
    )
    assert formdef.data_class().get(formdata.id).status == 'wf-st2'
    assert not formdef.data_class().get(formdata.id).workflow_data

    # put a file
    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()
    get_app(pub).put(
        sign_uri(formdata.get_url() + 'jump/trigger/xx-yy'),
        status=200,
        params=b'hello world',
        headers={'content-type': 'text/plain', 'filename': 'hello.txt'},
    )
    formdata.refresh_from_storage()
    assert formdata.status == 'wf-st2'
    substvars = CompatibilityNamesDict()
    substvars.update(formdata.get_substitution_variables())
    assert substvars['form_trigger_xx_yy_content'].base_filename == 'hello.txt'
    assert substvars['form_trigger_xx_yy_content'].content_type == 'text/plain'
    assert substvars['form_trigger_xx_yy_content'].get_content() == b'hello world'
    assert substvars['form_trigger_xx_yy_kind'] == 'jump'

    # post with invalid JSON data
    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()
    get_app(pub).post(
        sign_uri(formdata.get_url() + 'jump/trigger/xx-yy'),
        status=400,
        headers={'content-type': 'application/json'},
        params='ERROR',
    )

    # post with JSON data that is not a dictionary
    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()
    get_app(pub).post_json(
        sign_uri(formdata.get_url() + 'jump/trigger/xx-yy'),
        status=200,
        headers={'content-type': 'application/json'},
        params=['a', 'b', 'c'],
    )
    assert formdef.data_class().get(formdata.id).workflow_data == {'xx-yy': ['a', 'b', 'c']}


def test_workflow_trigger_with_file_data(pub, local_user):
    workflow = Workflow(name='test')

    workflow.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(workflow)
    workflow.backoffice_fields_formdef.fields = [
        fields.FileField(id='bo1', label='bo field 1'),
    ]
    st1 = workflow.add_status('Status1', 'st1')
    jump = st1.add_action('jump')
    jump.trigger = 'XXX'
    jump.mode = 'trigger'
    jump.status = 'st2'

    st2 = workflow.add_status('Status2', 'st2')
    setbo = st2.add_action('set-backoffice-fields')
    setbo.fields = [{'field_id': 'bo1', 'value': '{{ form_workflow_data_document }}'}]

    workflow.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = []
    formdef.workflow_id = workflow.id
    formdef.store()

    formdef.data_class().wipe()
    formdata = formdef.data_class()()
    formdata.data = {}
    formdata.just_created()
    formdata.store()

    params = {
        'document': {
            'filename': 'test.pdf',
            'b64_content': base64.encodebytes(b'%PDF-1.4 ...').decode(),
            'content_type': 'application/pdf',
        }
    }

    get_app(pub).post_json(
        sign_uri(formdata.get_url() + 'jump/trigger/XXX'),
        status=200,
        params=params,
        headers={'content-type': 'application/json'},
    )
    formdata.refresh_from_storage()
    assert formdata.status == 'wf-st2'
    assert formdata.workflow_data == params
    assert formdata.data['bo1'].get_content() == b'%PDF-1.4 ...'

    params = {
        'document': {
            'filename': 'test.pdf',
            'content': base64.encodebytes(b'%PDF-1.5 ...').decode(),
            'content_type': 'application/pdf',
            'content_is_base64': True,
        }
    }

    formdata.jump_status('st1')
    formdata.store()

    get_app(pub).post_json(
        sign_uri(formdata.get_url() + 'jump/trigger/XXX'),
        status=200,
        params=params,
        headers={'content-type': 'application/json'},
    )
    formdata.refresh_from_storage()
    assert formdata.status == 'wf-st2'
    assert formdata.data['bo1'].get_content() == b'%PDF-1.5 ...'


def test_workflow_trigger_with_condition(pub, local_user):
    workflow = Workflow(name='test')
    st1 = workflow.add_status('Status1', 'st1')
    jump = st1.add_action('jump')
    jump.trigger = 'XXX'
    jump.mode = 'trigger'
    jump.condition = {'type': 'django', 'value': 'form_var_foo == "bar"'}
    jump.status = 'st2'
    workflow.add_status('Status2', 'st2')
    workflow.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [fields.StringField(id='0', label='foo', varname='foo')]
    formdef.workflow_id = workflow.id
    formdef.store()

    formdef.data_class().wipe()
    formdata = formdef.data_class()()
    formdata.data = {'0': 'foo'}
    formdata.just_created()
    formdata.store()
    assert formdef.data_class().get(formdata.id).status == 'wf-st1'

    resp = get_app(pub).post(sign_uri(formdata.get_url() + 'jump/trigger/XXX'), status=403)
    assert resp.json == {
        'err_desc': 'Unmet condition.',
        'err': 1,
        'err_class': 'Access denied',
        'err_code': 'access-denied',
    }
    assert formdef.data_class().get(formdata.id).status == 'wf-st1'
    # check without json
    resp = get_app(pub).post(sign_uri(formdata.get_url() + 'jump/trigger/XXX', format=None), status=403)
    assert resp.content_type == 'text/html'

    formdata.data['0'] = 'bar'
    formdata.store()
    resp = get_app(pub).post(sign_uri(formdata.get_url() + 'jump/trigger/XXX'))
    assert resp.json == {'err': 0, 'url': None}


def test_workflow_trigger_jump_once(pub, local_user):
    workflow = Workflow(name='test')
    st1 = workflow.add_status('Status1', 'st1')
    st2 = workflow.add_status('Status2', 'st2')
    workflow.add_status('Status3', 'st3')
    jump = st1.add_action('jump')
    jump.trigger = 'XXX'
    jump.mode = 'trigger'
    jump.status = 'st2'
    jump = st2.add_action('jump')
    jump.trigger = 'XXX'
    jump.mode = 'trigger'
    jump.status = 'st3'
    workflow.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = []
    formdef.workflow_id = workflow.id
    formdef.store()

    formdef.data_class().wipe()
    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()
    assert formdef.data_class().get(formdata.id).status == 'wf-st1'

    resp = get_app(pub).post(sign_uri(formdata.get_url() + 'jump/trigger/XXX'))
    assert resp.json == {'err': 0, 'url': None}
    assert formdef.data_class().get(formdata.id).status == 'wf-st2'

    resp = get_app(pub).post(sign_uri(formdata.get_url() + 'jump/trigger/XXX'))
    assert resp.json == {'err': 0, 'url': None}
    assert formdef.data_class().get(formdata.id).status == 'wf-st3'


def test_workflow_trigger_api_access(pub, local_user):
    ApiAccess.wipe()
    pub.role_class.wipe()
    role = pub.role_class(name='xxx')
    role.store()
    role2 = pub.role_class(name='xxx2')
    role2.store()

    workflow = Workflow(name='test')
    st1 = workflow.add_status('Status1', 'st1')
    jump = st1.add_action('jump')
    jump.trigger = 'XXX'
    jump.mode = 'trigger'
    jump.status = 'st2'
    workflow.add_status('Status2', 'st2')
    workflow.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = []
    formdef.workflow_id = workflow.id
    formdef.store()

    formdef.data_class().wipe()
    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()

    jump.by = [role.id]
    workflow.store()

    access = ApiAccess()
    access.name = 'test'
    access.access_identifier = 'test'
    access.access_key = '12345'
    access.roles = [role2]
    access.store()

    get_app(pub).post(
        sign_uri(formdata.get_url() + 'jump/trigger/XXX/', orig='test', key='12345'), status=403
    )
    assert formdef.data_class().get(formdata.id).status == 'wf-st1'  # no change

    access.roles = [role]
    access.store()

    get_app(pub).post(
        sign_uri(formdata.get_url() + 'jump/trigger/XXX/', orig='test', key='12345'), status=200
    )
    assert formdef.data_class().get(formdata.id).status == 'wf-st2'
    assert formdef.data_class().get(formdata.id).evolution[-1].who is None


def test_workflow_trigger_http_auth_access(pub, local_user):
    ApiAccess.wipe()
    pub.role_class.wipe()
    role = pub.role_class(name='xxx')
    role.store()
    role2 = pub.role_class(name='xxx2')
    role2.store()

    workflow = Workflow(name='test')
    st1 = workflow.add_status('Status1', 'st1')
    jump = st1.add_action('jump')
    jump.trigger = 'XXX'
    jump.mode = 'trigger'
    jump.status = 'st2'
    workflow.add_status('Status2', 'st2')
    workflow.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = []
    formdef.workflow_id = workflow.id
    formdef.store()

    formdef.data_class().wipe()
    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()

    jump.by = [role.id]
    workflow.store()

    access = ApiAccess()
    access.name = 'test'
    access.access_identifier = 'test'
    access.access_key = '12345'
    access.roles = [role2]
    access.store()

    app = get_app(pub)
    app.set_authorization(('Basic', ('test', 'wrong')))
    resp = app.post(
        formdata.get_url() + 'jump/trigger/XXX/', headers={'accept': 'application/json'}, status=403
    )
    assert resp.json['err_desc'] == 'User not authenticated.'

    app.set_authorization(('Basic', ('test', '12345')))
    resp = app.post(
        formdata.get_url() + 'jump/trigger/XXX/', headers={'accept': 'application/json'}, status=403
    )
    assert resp.json['err_desc'] == 'Unsufficient roles.'
    assert formdef.data_class().get(formdata.id).status == 'wf-st1'  # no change

    access.roles = [role]
    access.store()

    app.post(formdata.get_url() + 'jump/trigger/XXX/', headers={'accept': 'application/json'}, status=200)
    assert formdef.data_class().get(formdata.id).status == 'wf-st2'
    assert formdef.data_class().get(formdata.id).evolution[-1].who is None


def get_latest_comment(formdata):
    for evolution in reversed(formdata.evolution):
        for part in reversed(evolution.parts):
            if isinstance(part, JournalEvolutionPart):
                return part.content


def test_workflow_global_webservice_trigger(pub, local_user, admin_user):
    workflow = Workflow(name='test')
    workflow.add_status('Status1', 'st1')

    ac1 = workflow.add_global_action('Action', 'ac1')
    trigger = ac1.append_trigger('webservice')
    trigger.identifier = 'plop'

    add_to_journal = ac1.add_action('register-comment', id='_add_to_journal')
    add_to_journal.comment = 'HELLO WORLD'

    workflow.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = []
    formdef.workflow_id = workflow.id
    formdef.store()

    formdef.data_class().wipe()
    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()
    assert formdef.data_class().get(formdata.id).status == 'wf-st1'

    # call to undefined hook
    get_app(pub).post(sign_uri(formdata.get_url() + 'hooks/XXX/'), status=404)
    get_app(pub).post(sign_uri(formdata.get_api_url() + 'hooks/XXX/'), status=404)

    # anonymous call
    get_app(pub).post(formdata.get_url() + 'hooks/plop/', status=200)
    assert get_latest_comment(formdef.data_class().get(formdata.id)) == '<p>HELLO WORLD</p>'

    # anonymous call, with roles an empty list
    trigger.roles = []
    add_to_journal.comment = 'HELLO WORLD 2'
    workflow.store()
    get_app(pub).post(formdata.get_api_url() + 'hooks/plop/', status=200)
    assert get_latest_comment(formdef.data_class().get(formdata.id)) == '<p>HELLO WORLD 2</p>'

    # call requiring user
    add_to_journal.comment = 'HELLO WORLD 3'
    trigger.roles = ['logged-users']
    workflow.store()
    get_app(pub).post(formdata.get_api_url() + 'hooks/plop/', status=403)
    get_app(pub).post(sign_uri(formdata.get_api_url() + 'hooks/plop/'), status=200)
    formdata.refresh_from_storage()
    assert get_latest_comment(formdata) == '<p>HELLO WORLD 3</p>'
    assert [x for x in formdata.get_workflow_traces() if x.event][-1].event == 'global-api-trigger'
    assert [x for x in formdata.get_workflow_traces() if x.event][-1].event_args == {
        'global_action_id': ac1.id
    }
    resp = login(get_app(pub), username='admin', password='admin').get(
        formdata.get_backoffice_url() + 'inspect'
    )
    # check tracing link is correct:
    assert '/global-actions/ac1/items/_add_to_journal/' in resp.text

    # call requiring roles
    add_to_journal.comment = 'HELLO WORLD 4'
    trigger.roles = ['logged-users']
    workflow.store()
    pub.role_class.wipe()
    role = pub.role_class(name='xxx')
    role.store()
    trigger.roles = [role.id]
    workflow.store()
    get_app(pub).post(sign_uri(formdata.get_api_url() + 'hooks/plop/'), status=403)
    get_app(pub).post(sign_uri(formdata.get_api_url() + 'hooks/plop/', user=local_user), status=403)

    local_user.roles = [role.id]
    local_user.store()
    get_app(pub).post(sign_uri(formdata.get_api_url() + 'hooks/plop/', user=local_user), status=200)
    assert get_latest_comment(formdef.data_class().get(formdata.id)) == '<p>HELLO WORLD 4</p>'

    # call requiring to be signed, without user/role
    add_to_journal.comment = 'HELLO WORLD 5'
    trigger.roles = ['_signed_calls']
    workflow.store()
    get_app(pub).post(formdata.get_api_url() + 'hooks/plop/', status=403)
    get_app(pub).post(sign_uri(formdata.get_api_url() + 'hooks/plop/'), status=200)
    assert get_latest_comment(formdef.data_class().get(formdata.id)) == '<p>HELLO WORLD 5</p>'

    # make sure a button is not added for external triggers
    account = PasswordAccount(id='user')
    account.set_password('user')
    account.user_id = local_user.id
    account.store()

    local_user.is_admin = True
    local_user.store()
    app = login(get_app(pub), username='user', password='user')
    resp = app.get(formdata.get_backoffice_url())
    assert not resp.pyquery('button[value="Action"]')

    account.remove_self()
    local_user.is_admin = False
    local_user.store()

    # call adding data
    add_to_journal.comment = 'HELLO {{plop_test}}'
    workflow.store()
    get_app(pub).post_json(
        sign_uri(formdata.get_api_url() + 'hooks/plop/', user=local_user), {'test': 'foobar'}, status=200
    )
    # (django templating make it turn into HTML)
    assert get_latest_comment(formdef.data_class().get(formdata.id)) == '<div>HELLO foobar</div>'

    # call adding data but with no actions
    ac1.items = []
    workflow.store()
    get_app(pub).post_json(
        sign_uri(formdata.get_api_url() + 'hooks/plop/', user=local_user), {'test': 'BAR'}, status=200
    )
    formdata.refresh_from_storage()
    assert formdata.workflow_data == {'plop': {'test': 'BAR'}}
    substvars = CompatibilityNamesDict()
    substvars.update(formdata.get_substitution_variables())
    assert substvars['form_trigger_plop_content_test'] == 'BAR'
    assert substvars['form_trigger_plop_kind'] == 'global'

    # call adding a file as data
    ac1.items = []
    workflow.store()
    get_app(pub).put(
        sign_uri(formdata.get_api_url() + 'hooks/plop/', user=local_user),
        params=b'hello world',
        headers={'content-type': 'text/plain', 'filename': 'hello.txt'},
        status=200,
    )
    formdata.refresh_from_storage()
    assert formdata.workflow_data == {'plop': {'test': 'BAR'}}
    substvars = CompatibilityNamesDict()
    substvars.update(formdata.get_substitution_variables())
    assert substvars['form_trigger_plop_content'].base_filename == 'hello.txt'
    assert substvars['form_trigger_plop_content'].content_type == 'text/plain'
    assert substvars['form_trigger_plop_content'].get_content() == b'hello world'
    assert substvars['form_trigger_plop_kind'] == 'global'


def test_workflow_global_webservice_trigger_no_trailing_slash(pub, local_user):
    workflow = Workflow(name='test')
    workflow.add_status('Status1', 'st1')

    ac1 = workflow.add_global_action('Action', 'ac1')
    trigger = ac1.append_trigger('webservice')
    trigger.identifier = 'plop'

    add_to_journal = ac1.add_action('register-comment', id='_add_to_journal')
    add_to_journal.comment = 'HELLO WORLD'

    workflow.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = []
    formdef.workflow_id = workflow.id
    formdef.store()

    formdef.data_class().wipe()
    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()
    assert formdef.data_class().get(formdata.id).status == 'wf-st1'

    # call to undefined hook
    get_app(pub).post(sign_uri(formdata.get_url() + 'hooks/XXX'), status=404)
    get_app(pub).post(sign_uri(formdata.get_api_url() + 'hooks/XXX'), status=404)

    # anonymous call
    get_app(pub).post(formdata.get_url() + 'hooks/plop', status=200)
    assert get_latest_comment(formdef.data_class().get(formdata.id)) == '<p>HELLO WORLD</p>'


def test_workflow_trigger_on_buzy_object(pub, local_user):
    workflow = Workflow(name='test')
    st1 = workflow.add_status('Status1', 'st1')
    jump = st1.add_action('jump')
    jump.trigger = 'xx-yy'
    jump.mode = 'trigger'
    jump.status = 'st2'
    workflow.add_status('Status2', 'st2')
    ac1 = workflow.add_global_action('Action', 'ac1')
    trigger = ac1.append_trigger('webservice')
    trigger.identifier = 'plop'
    workflow.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = []
    formdef.workflow_id = workflow.id
    formdef.store()

    formdef.data_class().wipe()
    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.workflow_processing_timestamp = now()
    formdata.store()

    resp = get_app(pub).post_json(sign_uri(formdata.get_url() + 'jump/trigger/xx-yy'), status=403)
    assert resp.json == {
        'err': 1,
        'err_class': 'Access denied',
        'err_code': 'access-denied',
        'err_desc': 'Formdata currently processing actions.',
    }

    get_app(pub).post(formdata.get_url() + 'hooks/plop/', status=403)  # html response
    resp = get_app(pub).post_json(formdata.get_url() + 'hooks/plop/', status=403)
    assert resp.json == {
        'err': 1,
        'err_class': 'Access denied',
        'err_code': 'access-denied',
        'err_desc': 'Formdata currently processing actions.',
    }

    get_app(pub).post_json(
        sign_uri(formdata.get_url() + 'jump/trigger/xx-yy?bypass-processing-check=true'), status=200
    )

    formdata.workflow_processing_timestamp = now()
    formdata.store()
    get_app(pub).post_json(formdata.get_url() + 'hooks/plop/?bypass-processing-check=true', status=200)
