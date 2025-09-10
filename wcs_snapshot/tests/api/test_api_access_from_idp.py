import json
import os

import pytest
import responses

from wcs import fields
from wcs.formdef import FormDef
from wcs.qommon.http_request import HTTPRequest
from wcs.sql import ApiAccess
from wcs.workflows import Workflow

from ..utilities import clean_temporary_pub, create_temporary_pub, get_app


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
[variables]
idp_api_url = https://authentic.example.invalid/api/'
[wscall-secrets]
authentic.example.invalid = 4460cf12e156d841c116fbebd52d7ebe41282c63ac2605740068ba5fd89b7316


'''
        )

    return pub


def teardown_module(module):
    clean_temporary_pub()


@pytest.fixture
def role(pub):
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.id = '123'
    role.uuid = '04188a12-ea15-11ef-a87c-14ac60d82bbb'
    role.store()
    return role


@pytest.fixture
def app(pub):
    return get_app(pub)


def test_idp_http_error(app, pub, role):
    # No authentication : 403
    app.get('/api/forms/', status=403)
    app.set_authorization(('Basic', ('test-client', '12345')))
    # check-api-client returns 403
    with responses.RequestsMock() as rsps:
        rsps.post(
            'https://authentic.example.invalid/api/check-api-client/',
            status=403,
        )
        app.get('/api/forms/', status=403)
        # check api call to authentic
        assert len(rsps.calls) == 1
        request = rsps.calls[0].request
        assert request.headers['Accept'] == 'application/json'
        assert request.headers['Content-Type'] == 'application/json'
        assert json.loads(request.body) == {
            'identifier': 'test-client',
            'password': '12345',
            'ip': '127.0.0.1',
        }


def test_idp_no_err_key(app, pub, role):
    app.set_authorization(('Basic', ('test-client', '12345')))
    with responses.RequestsMock() as rsps:
        rsps.post('https://authentic.example.invalid/api/check-api-client/', json={'foo': 'bar'})
        app.get('/api/forms/', status=403)


def test_idp_app_error(app, pub, role):
    app.set_authorization(('Basic', ('test-client', '12345')))
    with responses.RequestsMock() as rsps:
        rsps.post('https://authentic.example.invalid/api/check-api-client/', json={'err': 1})
        app.get('/api/forms/', status=403)


def test_idp_wrong_serialization(app, pub, role):
    app.set_authorization(('Basic', ('test-client', '12345')))
    with responses.RequestsMock() as rsps:
        rsps.post(
            'https://authentic.example.invalid/api/check-api-client/', json={'err': 0, 'data': {'foo': 'bar'}}
        )
        app.get('/api/forms/', status=403)


def test_access_granted(app, pub, role):
    app.set_authorization(('Basic', ('test-client', '12345')))
    with responses.RequestsMock() as rsps:
        rsps.post(
            'https://authentic.example.invalid/api/check-api-client/',
            json={
                'err': 0,
                'data': {
                    'is_active': True,
                    'is_anonymous': False,
                    'is_authenticated': True,
                    'is_superuser': False,
                    'restrict_to_anonymised_data': False,
                    'roles': [],
                },
            },
        )
        app.get('/api/forms/', status=200)
        app.get('/api/statistics/', status=200)


@pytest.mark.parametrize('client_ip', ('127.0.0.1', '1.2.3.4', '::1', '2001:a:b:c:d::', 'NotAnIP', ''))
def test_access_granted_ip_restrictions(app, pub, role, client_ip):
    app.set_authorization(('Basic', ('test-client', '12345')))

    a2_payload = {
        'err': 0,
        'data': {
            'is_active': True,
            'is_anonymous': False,
            'is_authenticated': True,
            'is_superuser': False,
            'restrict_to_anonymised_data': False,
            'roles': [],
        },
    }
    a2_url = 'https://authentic.example.invalid/api/check-api-client/'
    expected = {'identifier': 'test-client', 'password': '12345', 'ip': client_ip}
    with responses.RequestsMock() as rsps:
        rsps.post(
            a2_url,
            json=a2_payload,
            match=[responses.matchers.json_params_matcher(expected)],
        )
        app.get('/api/forms/', status=200, extra_environ={'REMOTE_ADDR': client_ip})


def test_access_granted_even_if_api_access_exists(app, pub, role):
    ApiAccess.wipe()
    access = ApiAccess()
    access.name = 'test'
    access.access_identifier = 'test'
    access.access_key = '98765'
    access.store()

    app.set_authorization(('Basic', ('test-client', '12345')))
    with responses.RequestsMock() as rsps:
        rsps.post(
            'https://authentic.example.invalid/api/check-api-client/',
            json={
                'err': 0,
                'data': {
                    'is_active': True,
                    'is_anonymous': False,
                    'is_authenticated': True,
                    'is_superuser': False,
                    'restrict_to_anonymised_data': False,
                    'roles': [],
                },
            },
        )
        app.get('/api/forms/', status=200)


def test_roles_are_used(app, pub, role):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [
        fields.StringField(id='0', label='foobar', varname='foobar'),
    ]
    Workflow.wipe()
    workflow = Workflow(name='foo')
    workflow.possible_status = Workflow.get_default_workflow().possible_status[:]
    workflow.roles['_foobar'] = 'Foobar'
    workflow.store()
    formdef.workflow_id = workflow.id
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.store()

    formdef.data_class().wipe()
    formdata = formdef.data_class()()
    formdata.data = {'0': 'foo@localhost'}
    formdata.just_created()
    formdata.status = 'wf-new'
    formdata.evolution[-1].status = 'wf-new'
    formdata.store()

    app.set_authorization(('Basic', ('test-client', '12345')))
    # No receiver role : 403
    with responses.RequestsMock() as rsps:
        rsps.post(
            'https://authentic.example.invalid/api/check-api-client/',
            json={
                'err': 0,
                'data': {
                    'is_active': True,
                    'is_anonymous': False,
                    'is_authenticated': True,
                    'is_superuser': False,
                    'restrict_to_anonymised_data': False,
                    'roles': [],
                },
            },
        )
        app.get('/api/forms/test/%s/' % formdata.id, status=403)
        rsps.reset()

    # Receiver role : Ok
    with responses.RequestsMock() as rsps:
        rsps.post(
            'https://authentic.example.invalid/api/check-api-client/',
            json={
                'err': 0,
                'data': {
                    'is_active': True,
                    'is_anonymous': False,
                    'is_authenticated': True,
                    'is_superuser': False,
                    'restrict_to_anonymised_data': False,
                    'roles': [role.uuid, 'unknown-uuid'],
                },
            },
        )
        app.get('/api/forms/test/%s/' % formdata.id, status=200)


def test_restrict_to_anonymised_data(app, pub, role):
    Workflow.wipe()
    workflow = Workflow()
    workflow.add_status('Status1', 'st1')
    workflow.add_status('Status2', 'st2')
    workflow.possible_status[-1].visibility = [role.id]
    workflow.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.fields = [
        fields.StringField(id='1', label='foobar', varname='foobar'),
        fields.StringField(id='2', label='foobar2', varname='foobar2', anonymise='no'),
    ]
    formdef.workflow_id = workflow.id
    formdef.store()

    data_class = formdef.data_class()
    data_class.wipe()
    formdata = data_class()
    formdata.data = {'1': 'FOO BAR1', '2': 'FOO BAR 2'}
    formdata.just_created()
    formdata.jump_status('st1')
    formdata.store()

    # check normal API behaviour: get all data
    app.set_authorization(('Basic', ('test-client', '12345')))
    with responses.RequestsMock() as rsps:
        rsps.post(
            'https://authentic.example.invalid/api/check-api-client/',
            json={
                'err': 0,
                'data': {
                    'is_active': True,
                    'is_anonymous': False,
                    'is_authenticated': True,
                    'is_superuser': False,
                    'restrict_to_anonymised_data': False,
                    'roles': [role.uuid],
                },
            },
        )
        resp = app.get('/api/forms/test/list?full=on')
        assert len(resp.json) == 1
        assert resp.json[0]['fields']['foobar'] == 'FOO BAR1'
        assert resp.json[0]['fields']['foobar2'] == 'FOO BAR 2'
        assert resp.json[0]['workflow']['status']['id'] == 'st1'
        assert resp.json[0]['workflow']['status']['name'] == 'Status1'
        assert resp.json[0]['workflow']['status']['endpoint']
        assert resp.json[0]['workflow']['status']['first_arrival_datetime']
        assert resp.json[0]['workflow']['status']['latest_arrival_datetime']
        rsps.reset()

    # restrict API access to anonymised data
    with responses.RequestsMock() as rsps:
        rsps.post(
            'https://authentic.example.invalid/api/check-api-client/',
            json={
                'err': 0,
                'data': {
                    'is_active': True,
                    'is_anonymous': False,
                    'is_authenticated': True,
                    'is_superuser': False,
                    'restrict_to_anonymised_data': True,
                    'roles': [role.uuid, 'unknown-uuid'],
                },
            },
        )
        resp = app.get('/api/forms/test/list?full=on')
        assert len(resp.json) == 1
        assert 'foobar' not in resp.json[0]['fields']
        assert resp.json[0]['fields']['foobar2'] == 'FOO BAR 2'
        assert resp.json[0]['workflow']['status']['id'] == 'st1'
        assert resp.json[0]['workflow']['status']['name'] == 'Status1'
        assert resp.json[0]['workflow']['real_status']['id'] == 'st1'
        assert resp.json[0]['workflow']['real_status']['name'] == 'Status1'
