import base64
import datetime
import io
import json
import os
import re
import time
import xml.etree.ElementTree as ET
import zipfile
from contextlib import contextmanager

import pytest
import responses
from django.utils.encoding import force_bytes
from django.utils.timezone import localtime, make_aware
from quixote import get_publisher
from webtest import Upload

from wcs import fields
from wcs.admin.settings import UserFieldsFormDef
from wcs.blocks import BlockDef
from wcs.carddef import CardDef
from wcs.categories import Category
from wcs.data_sources import NamedDataSource
from wcs.formdata import Evolution
from wcs.formdef import FormDef
from wcs.qommon import ods
from wcs.qommon.http_request import HTTPRequest
from wcs.qommon.upload_storage import PicklableUpload
from wcs.sql import ApiAccess
from wcs.wf.comment import WorkflowCommentPart
from wcs.wf.form import WorkflowFormEvolutionPart, WorkflowFormFieldsFormDef
from wcs.workflows import (
    AttachmentEvolutionPart,
    Workflow,
    WorkflowBackofficeFieldsFormDef,
    WorkflowCriticalityLevel,
)

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
            '''
[api-secrets]
coucou = 1234

[variables]
idp_api_url = https://authentic.example.invalid/api/'

[wscall-secrets]
authentic.example.invalid = 4460cf12e156d841c116fbebd52d7ebe41282c63ac2605740068ba5fd89b7316
'''
        )

    pub.user_class.wipe()

    return pub


def teardown_module(module):
    clean_temporary_pub()


@pytest.fixture
def ics_data(local_user):
    get_publisher().role_class.wipe()
    role = get_publisher().role_class(name='test')
    role.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.url_name = 'test'
    formdef.name = 'testé'
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.fields = [
        fields.StringField(id='0', label='foobar', varname='foobar'),
        fields.StringField(id='1', label='foobar2', varname='foobar2'),
        fields.DateField(id='2', label='date', varname='date'),
    ]
    formdef.digest_templates = {'default': 'plöp {{ form_var_foobar }} plÔp'}
    formdef.store()

    data_class = formdef.data_class()
    data_class.wipe()

    date = datetime.datetime(2014, 1, 20, 12, 00)
    for i in range(30):
        formdata = data_class()
        formdata.data = {'0': (date + datetime.timedelta(days=i)).strftime('%Y-%m-%d %H:%M')}
        formdata.data['1'] = (date + datetime.timedelta(days=i, minutes=i + 1)).strftime('%Y-%m-%d %H:%M')
        formdata.data['2'] = (datetime.date(2014, 1, 20) + datetime.timedelta(days=i)).timetuple()
        formdata.user_id = local_user.id
        formdata.just_created()
        if i % 3 == 0:
            formdata.jump_status('new')
        else:
            formdata.jump_status('finished')
        formdata.store()

    # not a datetime: ignored
    date = datetime.date(2014, 1, 20)
    formdata = data_class()
    formdata.data = {'0': '12:00'}
    formdata.data['1'] = '13:00'
    formdata.user_id = local_user.id
    formdata.just_created()
    formdata.jump_status('new')
    formdata.store()


@contextmanager
def low_export_limit_threshold():
    from wcs.backoffice.management import FormPage

    FormPage.WCS_SYNC_EXPORT_LIMIT = 10
    yield
    FormPage.WCS_SYNC_EXPORT_LIMIT = 100


@pytest.mark.parametrize('user', ['query-email', 'api-access'])
@pytest.mark.parametrize('auth', ['signature', 'http-basic'])
def test_formdata(pub, local_user, user, auth):
    NamedDataSource.wipe()

    app = get_app(pub)

    if user == 'api-access':
        ApiAccess.wipe()
        access = ApiAccess()
        access.name = 'test'
        access.access_identifier = 'test'
        access.access_key = '12345'
        access.store()

        if auth == 'http-basic':

            def get_url(url, **kwargs):
                app.set_authorization(('Basic', ('test', '12345')))
                return app.get(url, **kwargs)

        else:

            def get_url(url, **kwargs):
                return app.get(sign_uri(url, orig=access.access_identifier, key=access.access_key), **kwargs)

    else:
        if auth == 'http-basic':
            pytest.skip('http basic authentication requires ApiAccess')

        def get_url(url, **kwargs):
            return app.get(sign_uri(url, user=local_user), **kwargs)

    data_source = NamedDataSource(name='foobar')
    data_source.data_source = {
        'type': 'jsonvalue',
        'value': json.dumps(
            [{'id': '1', 'text': 'foo', 'more': 'XXX'}, {'id': '2', 'text': 'bar', 'more': 'YYY'}]
        ),
    }
    data_source.store()

    BlockDef.wipe()
    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.TitleField(id='dsd', label='Title'),
        fields.StringField(id='abc', label='Foo', varname='foo'),
        fields.ItemField(id='xyz', label='Test', data_source={'type': 'foobar'}, varname='bar'),
    ]
    block.store()

    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.id = '123'
    role.store()
    another_role = pub.role_class(name='another')
    another_role.id = '321'
    another_role.store()
    FormDef.wipe()
    formdef = FormDef()
    formdef.geolocations = {'base': 'blah'}
    formdef.name = 'test'
    formdef.fields = [
        fields.StringField(id='0', label='foobar', varname='foobar'),
        fields.StringField(id='1', label='foobar2'),
        fields.DateField(id='2', label='foobar3', varname='date'),
        fields.FileField(id='3', label='foobar4', varname='file'),
        fields.ItemField(id='4', label='foobar5', varname='item', data_source={'type': 'foobar'}),
        fields.BlockField(id='5', label='test', varname='blockdata', block_slug='foobar', max_items='3'),
        fields.TextField(id='6', label='rich text', varname='richtext', display_mode='rich'),
        fields.FileField(id='7', label='image file', varname='image_file'),
        fields.NumericField(id='8', label='numeric value', varname='numeric'),
    ]
    Workflow.wipe()
    workflow = Workflow(name='foo')
    workflow.possible_status = Workflow.get_default_workflow().possible_status[:]
    workflow.roles['_foobar'] = 'Foobar'
    workflow.store()
    formdef.workflow_id = workflow.id
    formdef.workflow_roles = {'_receiver': role.id, '_foobar': another_role.id}
    formdef.store()
    item_field = formdef.fields[4]

    formdef.data_class().wipe()
    formdata = formdef.data_class()()
    date = time.strptime('2014-01-20', '%Y-%m-%d')
    upload = PicklableUpload('test.txt', 'text/plain', 'ascii')
    upload.receive([b'base64me'])
    image_upload = PicklableUpload('test.png', 'image/png', 'ascii')
    image_upload.receive([b'...'])
    formdata.data = {
        '0': 'foo@localhost',
        '1': 'xxx',
        '2': date,
        '3': upload,
        '4': '1',
        '5': {
            'data': [
                {'abc': 'plop', 'xyz': '1', 'xyz_display': 'foo', 'xyz_structured': 'XXX'},
            ],
            'schema': {},  # not important here
        },
        '5_display': 'hello',
        '6': '<script></script><p>foo</p>',
        '7': image_upload,
        '8': 5.5,
    }
    formdata.data['4_display'] = item_field.store_display_value(formdata.data, item_field.id)
    formdata.data['4_structured'] = item_field.store_structured_value(formdata.data, item_field.id)
    formdata.user_id = local_user.id
    formdata.just_created()
    formdata.status = 'wf-new'
    formdata.evolution[-1].status = 'wf-new'
    formdata.geolocations = {'base': {'lon': 10, 'lat': -12}}
    formdata.store()

    resp = get_url('/api/forms/test/%s/' % formdata.id, status=403)

    if user == 'api-access':
        access.roles = [role]
        access.store()
    else:
        local_user.roles = [role.id]
        local_user.store()

    resp = get_url('/api/forms/test/%s/' % formdata.id, status=200)

    assert datetime.datetime.strptime(resp.json['last_update_time'], '%Y-%m-%dT%H:%M:%S')
    assert datetime.datetime.strptime(resp.json['receipt_time'], '%Y-%m-%dT%H:%M:%S')
    assert len(resp.json['fields']) == 11
    assert 'foobar' in resp.json['fields']
    assert 'foobar2' not in resp.json['fields']  # foobar2 has no varname, not in json
    assert resp.json['user']['name'] == local_user.name
    assert 'var1' not in resp.json['user']
    assert 'var2' not in resp.json['user']
    assert resp.json['fields']['foobar'] == 'foo@localhost'
    assert resp.json['fields']['date'] == '2014-01-20'
    assert resp.json['fields']['file']['content'] == 'YmFzZTY0bWU='  # base64('base64me')
    assert resp.json['fields']['file']['filename'] == 'test.txt'
    assert resp.json['fields']['file']['content_type'] == 'text/plain'
    assert resp.json['fields']['file']['url'].startswith('http://example.net/test/1/download?hash=')
    assert 'thumbnail_url' not in resp.json['fields']['file']
    get_url(resp.json['fields']['file']['url'], status=200)
    assert resp.json['fields']['item'] == 'foo'
    assert resp.json['fields']['item_raw'] == '1'
    assert resp.json['fields']['item_structured'] == {'id': '1', 'text': 'foo', 'more': 'XXX'}
    assert resp.json['fields']['blockdata'] == 'hello'
    assert resp.json['fields']['blockdata_raw'] == [
        {'foo': 'plop', 'bar': 'foo', 'bar_raw': '1', 'bar_structured': 'XXX'}
    ]
    assert resp.json['fields']['richtext'] == '<p>foo</p>'  # only allowed tags

    assert resp.json['fields']['image_file']['content'] == 'Li4u'
    assert resp.json['fields']['image_file']['filename'] == 'test.png'
    assert resp.json['fields']['image_file']['content_type'] == 'image/png'
    assert resp.json['fields']['image_file']['url'].startswith('http://example.net/test/1/download?hash=')
    assert 'thumbnail=1' in resp.json['fields']['image_file']['thumbnail_url']

    assert resp.json['fields']['numeric'] == '5.5'

    assert resp.json['workflow']['status']['name'] == 'New'
    assert resp.json['workflow']['status']['first_arrival_datetime']
    assert resp.json['workflow']['status']['latest_arrival_datetime']
    assert resp.json['workflow']['real_status']['name'] == 'New'
    assert resp.json['workflow']['real_status']['first_arrival_datetime']
    assert resp.json['workflow']['real_status']['latest_arrival_datetime']
    assert resp.json['submission'] == {'backoffice': False, 'channel': 'web'}
    assert resp.json['geolocations']['base']['lon'] == 10
    assert resp.json['geolocations']['base']['lat'] == -12

    assert [x.get('id') for x in resp.json['roles']['_receiver']] == [str(role.id)]
    assert [x.get('id') for x in resp.json['roles']['_foobar']] == [str(another_role.id)]
    assert {x.get('id') for x in resp.json['roles']['concerned']} == {str(role.id), str(another_role.id)}
    assert [x.get('id') for x in resp.json['roles']['actions']] == [str(role.id)]

    assert resp.json['url'] == 'http://example.net/test/%s/' % formdata.id
    assert resp.json['backoffice_url'] == 'http://example.net/backoffice/management/test/%s/' % formdata.id
    assert resp.json['api_url'] == 'http://example.net/api/forms/test/%s/' % formdata.id

    # check the ?format=json endpoint returns 403
    get_app(pub).get('/test/%s/?format=json' % formdata.id, status=403)
    get_app(pub).get(sign_uri('/test/%s/' % formdata.id, user=local_user), status=403)

    # check status visibility
    workflow.add_status('Status1', 'st1')
    workflow.possible_status[-1].visibility = ['__hidden__']
    workflow.store()
    formdef.refresh_from_storage()  # also update cached workflow
    formdata.jump_status('st1')
    assert formdata.status == 'wf-st1'

    resp = get_url('/api/forms/test/%s/' % formdata.id, status=200)
    assert resp.json['workflow']['status']['id'] == 'new'
    assert resp.json['workflow']['real_status']['id'] == 'st1'

    # check ?include-files-content=off
    resp = get_url('/api/forms/test/%s/?include-files-content=off' % formdata.id, status=200)
    assert 'content' not in resp.json['fields']['file']
    assert resp.json['fields']['file']['url']
    assert resp.json['fields']['file']['filename'] == 'test.txt'


@pytest.mark.parametrize('user', ['query-email', 'api-access'])
@pytest.mark.parametrize('auth', ['signature', 'http-basic'])
def test_formdata_list_access(pub, local_user, user, auth):
    NamedDataSource.wipe()

    app = get_app(pub)

    if user == 'api-access':
        ApiAccess.wipe()
        access = ApiAccess()
        access.name = 'test'
        access.access_identifier = 'test'
        access.access_key = '12345'
        access.store()

        if auth == 'http-basic':

            def get_url(url, **kwargs):
                app.set_authorization(('Basic', ('test', '12345')))
                return app.get(url, **kwargs)

        else:

            def get_url(url, **kwargs):
                return app.get(sign_uri(url, orig=access.access_identifier, key=access.access_key), **kwargs)

    else:
        if auth == 'http-basic':
            pytest.skip('http basic authentication requires ApiAccess')

        def get_url(url, **kwargs):
            return app.get(sign_uri(url, user=local_user), **kwargs)

    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.id = '123'
    role.store()
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [
        fields.StringField(id='0', label='foobar', varname='foobar'),
    ]
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.store()

    formdef.data_class().wipe()
    formdata = formdef.data_class()()
    formdata.data = {
        '0': 'foo',
    }
    formdata.just_created()
    formdata.store()

    if user == 'api-access' and auth == 'signature':
        get_url('/api/forms/test/list', status=200)  # signed without user: ok
    else:
        get_url('/api/forms/test/list', status=403)

    if user == 'api-access':
        access.roles = [role]
        access.store()
    else:
        local_user.roles = [role.id]
        local_user.store()

    get_url('/api/forms/test/list', status=200)  # access with appropriate role, always ok

    if user == 'api-access' and auth == 'signature':
        app.get(sign_uri('/api/forms/test/list'), status=200)  # signed without user: ok
        app.get(sign_uri('/api/forms/test/list?NameID='), status=403)  # signed with empty user user: ko


def test_formdata_user_fields(pub, local_user):
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.id = '123'
    role.store()
    local_user.roles = [role.id]
    local_user.store()

    user_formdef = UserFieldsFormDef(pub)
    user_formdef.fields = [
        fields.StringField(id='3', label='test', varname='var1'),
        fields.StringField(id='9', label='noop', varname='var2'),
        fields.DateField(id='10', label='birthdate', varname='birthdate'),
        fields.StringField(id='42', label='no varname'),
    ]
    user_formdef.store()
    local_user.form_data = {'3': 'toto', '9': 'nono', '10': datetime.date(2020, 1, 15).timetuple()}
    local_user.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.store()

    formdef.data_class().wipe()
    formdata = formdef.data_class()()
    formdata.user_id = local_user.id
    formdata.just_created()
    formdata.store()

    for params in [
        '',
        '?include-evolution=on',
        '?include-roles=on',
        '?include-submission=on',
        '?include-workflow=on',
        '?include-workflow-data=on',
    ]:
        resp = get_app(pub).get(sign_uri('/api/forms/test/list%s' % params, user=local_user))
        assert 'user' not in resp.json[0]
    for params in ['?full=on', '?include-fields=on', '?include-user=on']:
        resp = get_app(pub).get(sign_uri('/api/forms/test/list%s' % params, user=local_user))
        assert resp.json[0]['user'] == {
            'id': local_user.id,
            'NameID': ['0123456789'],
            'name': 'Jean Darmette',
            'email': 'jean.darmette@triffouilis.fr',
        }

    resp = get_app(pub).get(sign_uri('/api/forms/test/%s/?include-user=off' % formdata.id, user=local_user))
    assert 'user' not in resp.json
    for params in ['', '?include-fields=on']:
        resp = get_app(pub).get(sign_uri('/api/forms/test/%s/%s' % (formdata.id, params), user=local_user))
        assert resp.json['user'] == {
            'id': local_user.id,
            'NameID': ['0123456789'],
            'name': 'Jean Darmette',
            'email': 'jean.darmette@triffouilis.fr',
        }


def test_formdata_block_field_with_digest(pub, local_user):
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.id = '123'
    role.store()
    local_user.roles = [role.id]
    local_user.store()

    BlockDef.wipe()
    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.StringField(id='abc', label='Foo', varname='foo'),
    ]
    block.digest_template = 'X{{ block_var_foo }}Y'
    block.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [
        fields.BlockField(id='5', label='test', varname='blockdata', block_slug='foobar', max_items='3'),
    ]
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.store()

    formdef.data_class().wipe()
    formdata = formdef.data_class()()
    formdata.data = {
        '5': {
            'data': [
                {'abc': 'plop1'},
                {'abc': 'plop2'},
            ],
            'digests': ['Xplop1Y', 'Xplop2Y'],
            'schema': {},  # not important here
        },
        '5_display': 'Xplop1Y, Xplop2Y',
    }
    formdata.just_created()
    formdata.store()

    resp = get_app(pub).get(sign_uri('/api/forms/test/%s/' % formdata.id, user=local_user))
    assert resp.json['fields'] == {
        'blockdata': 'Xplop1Y, Xplop2Y',
        'blockdata_digests': ['Xplop1Y', 'Xplop2Y'],
        'blockdata_raw': [{'foo': 'plop1'}, {'foo': 'plop2'}],
    }


def test_formdata_submission_fields(pub, local_user):
    agent = pub.user_class(name='agent')
    agent.store()

    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.id = '123'
    role.store()
    local_user.roles = [role.id]
    local_user.store()

    user_formdef = UserFieldsFormDef(pub)
    user_formdef.fields = [
        fields.StringField(id='3', label='test', varname='var1'),
        fields.StringField(id='9', label='noop', varname='var2'),
        fields.StringField(id='42', label='no varname'),
    ]
    user_formdef.store()
    local_user.form_data = {'3': 'toto', '9': 'nono'}
    local_user.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.store()

    formdef.data_class().wipe()
    formdata = formdef.data_class()()
    formdata.user_id = local_user.id
    formdata.submission_agent_id = agent.id
    formdata.just_created()
    formdata.store()

    for params in [
        '',
        '?include-fields=on',
        '?include-evolution=on',
        '?include-roles=on',
        '?include-workflow=on',
        '?include-workflow-data=on',
    ]:
        resp = get_app(pub).get(sign_uri('/api/forms/test/list%s' % params, user=local_user))
        assert 'submission' not in resp.json[0]
    for params in ['?full=on', '?include-submission=on']:
        resp = get_app(pub).get(sign_uri('/api/forms/test/list%s' % params, user=local_user))
        assert resp.json[0]['submission'] == {
            'backoffice': False,
            'channel': 'web',
            'agent': {'id': agent.id, 'name': 'agent'},
        }

    resp = get_app(pub).get(
        sign_uri('/api/forms/test/%s/?include-submission=off' % formdata.id, user=local_user)
    )
    assert 'submission' not in resp.json
    for params in ['', '?include-submission=on']:
        resp = get_app(pub).get(sign_uri('/api/forms/test/%s/%s' % (formdata.id, params), user=local_user))
        assert resp.json['submission'] == {
            'backoffice': False,
            'channel': 'web',
            'agent': {'id': agent.id, 'name': 'agent'},
        }


def test_formdata_backoffice_fields(pub, local_user):
    test_formdata(pub, local_user, 'query-email', 'signature')
    Workflow.wipe()
    workflow = Workflow(name='foo')
    workflow.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(workflow)
    workflow.backoffice_fields_formdef.fields = [
        fields.StringField(id='bo1', label='1st backoffice field', varname='backoffice_blah'),
    ]
    workflow.store()

    formdef = FormDef.select()[0]
    formdata = formdef.data_class().select()[0]
    formdata.data['bo1'] = 'Hello world'
    formdata.store()

    resp = get_app(pub).get(sign_uri('/api/forms/test/%s/' % formdata.id, user=local_user))
    assert resp.json['workflow']['fields']['backoffice_blah'] == 'Hello world'


@pytest.mark.parametrize('user', ['query-email', 'api-access'])
@pytest.mark.parametrize('auth', ['signature', 'http-basic'])
def test_formdata_workflow_form(pub, local_user, user, auth):
    app = get_app(pub)

    if user == 'api-access':
        ApiAccess.wipe()
        access = ApiAccess()
        access.name = 'test'
        access.access_identifier = 'test'
        access.access_key = '12345'
        access.store()

        if auth == 'http-basic':

            def get_url(url, **kwargs):
                app.set_authorization(('Basic', ('test', '12345')))
                return app.get(url, **kwargs)

        else:

            def get_url(url, **kwargs):
                return app.get(sign_uri(url, orig=access.access_identifier, key=access.access_key), **kwargs)

    else:
        if auth == 'http-basic':
            pytest.skip('http basic authentication requires ApiAccess')

        def get_url(url, **kwargs):
            return app.get(sign_uri(url, user=local_user), **kwargs)

    role = pub.role_class(name='test')
    role.id = '123'
    role.store()

    Workflow.wipe()
    workflow = Workflow(name='foo')
    st = workflow.add_status('st1')
    form_action = st.add_action('form')
    form_action.varname = 'blah'
    form_action.formdef = WorkflowFormFieldsFormDef(item=form_action)
    form_action.formdef.fields = [
        fields.FileField(id='1', label='file', varname='file'),
        fields.StringField(id='2', label='str', varname='str'),
    ]
    workflow.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = []
    formdef.workflow_id = workflow.id
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.store()

    formdef.data_class().wipe()
    formdata = formdef.data_class()()
    formdata.just_created()
    data = {'1': PicklableUpload('test.txt', 'text/plain'), '2': 'text'}
    data['1'].receive([b'hello world wf form'])
    formdata.evolution[-1].parts = [
        WorkflowFormEvolutionPart(form_action, data),
    ]
    formdata.store()

    if user == 'api-access':
        access.roles = [role]
        access.store()
    else:
        local_user.roles = [role.id]
        local_user.store()

    resp = get_url('/api/forms/test/%s/' % formdata.id, status=200)
    assert resp.json['evolution'][0]['parts'] == [
        {
            'data': {
                'file': 'test.txt',
                'file_raw': {
                    'content': 'aGVsbG8gd29ybGQgd2YgZm9ybQ==',
                    'content_is_base64': True,
                    'content_type': 'text/plain',
                    'filename': 'test.txt',
                },
                'file_url': None,
                'str': 'text',
            },
            'key': 'blah',
            'type': 'workflow-form',
        }
    ]

    resp = get_url('/api/forms/test/%s/?include-files-content=off' % formdata.id, status=200)
    assert resp.json['evolution'][0]['parts'] == [
        {
            'data': {'file': 'test.txt', 'file_url': None, 'str': 'text'},
            'key': 'blah',
            'type': 'workflow-form',
        }
    ]


def test_formdata_duplicated_varnames(pub, local_user):
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.id = '123'
    role.store()
    another_role = pub.role_class(name='another')
    another_role.id = '321'
    another_role.store()
    FormDef.wipe()
    formdef = FormDef()
    formdef.geolocations = {'base': 'blah'}
    formdef.name = 'test'
    formdef.fields = [
        fields.StringField(id='0', label='foobar', varname='foobar'),
        fields.StringField(id='1', label='foobar2', varname='foobar'),
    ]
    workflow = Workflow.get_default_workflow()
    workflow.roles['_foobar'] = 'Foobar'
    workflow.id = '2'
    workflow.store()
    formdef.workflow_id = workflow.id
    formdef.workflow_roles = {'_receiver': role.id, '_foobar': another_role.id}
    formdef.store()

    formdef.data_class().wipe()
    formdata = formdef.data_class()()
    formdata.data = {
        '0': 'foo',
        '1': 'bar',
    }
    formdata.user_id = local_user.id
    formdata.just_created()
    formdata.status = 'wf-new'
    formdata.evolution[-1].status = 'wf-new'
    formdata.store()

    local_user.roles = [role.id]
    local_user.store()
    resp = get_app(pub).get(sign_uri('/api/forms/test/%s/' % formdata.id, user=local_user), status=200)
    assert resp.json['fields'] == {'foobar': 'foo'}

    formdata.data = {
        '0': 'foo',
        '1': '',
    }
    formdata.store()
    resp = get_app(pub).get(sign_uri('/api/forms/test/%s/' % formdata.id, user=local_user), status=200)
    assert resp.json['fields'] == {'foobar': 'foo'}

    formdata.data = {
        '0': '',
        '1': 'foo',
    }
    formdata.store()
    resp = get_app(pub).get(sign_uri('/api/forms/test/%s/' % formdata.id, user=local_user), status=200)
    assert resp.json['fields'] == {'foobar': 'foo'}


def test_formdata_edit(pub, local_user):
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.id = '123'
    role.store()
    another_role = pub.role_class(name='another')
    another_role.id = '321'
    another_role.store()
    local_user.roles = [role.id]
    local_user.store()
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
    formdef.workflow_roles = {'_receiver': role.id, '_foobar': another_role.id}
    formdef.store()
    formdef.data_class().wipe()
    formdata = formdef.data_class()()
    formdata.data = {
        '0': 'foo@localhost',
    }
    formdata.user_id = local_user.id
    formdata.just_created()
    formdata.status = 'wf-new'
    formdata.evolution[-1].status = 'wf-new'
    formdata.store()

    # not user
    get_app(pub).post_json(
        sign_uri('/api/forms/test/%s/' % formdata.id), {'data': {'0': 'bar@localhost'}}, status=403
    )

    # no editable action
    get_app(pub).post_json(
        sign_uri('/api/forms/test/%s/' % formdata.id, user=local_user),
        {'data': {'0': 'bar@localhost'}},
        status=403,
    )

    wfedit = workflow.possible_status[1].add_action('editable', id='_wfedit')
    wfedit.by = [local_user.roles[0]]
    workflow.store()

    get_app(pub).post_json(
        sign_uri('/api/forms/test/%s/' % formdata.id, user=local_user),
        {'data': {'0': 'bar@localhost'}},
        status=200,
    )
    assert formdef.data_class().select()[0].data['0'] == 'bar@localhost'

    # bad payload: not a dict, missing data entry
    get_app(pub).post_json(
        sign_uri('/api/forms/test/%s/' % formdata.id, user=local_user),
        'not a dict',
        status=400,
    )
    get_app(pub).post_json(
        sign_uri('/api/forms/test/%s/' % formdata.id, user=local_user),
        {'foo': 'bar'},  # no data
        status=400,
    )

    # not editable by user role
    wfedit.by = ['XX']
    workflow.store()
    get_app(pub).post_json(
        sign_uri('/api/forms/test/%s/' % formdata.id, user=local_user),
        {'data': {'0': 'bar@localhost'}},
        status=403,
    )

    # edit + jump
    wfedit.status = 'rejected'
    wfedit.by = [local_user.roles[0]]
    workflow.store()

    get_app(pub).post_json(
        sign_uri('/api/forms/test/%s/' % formdata.id, user=local_user),
        {'data': {'0': 'bar2@localhost'}},
        status=200,
    )
    assert formdef.data_class().select()[0].data['0'] == 'bar2@localhost'
    assert formdef.data_class().select()[0].status == 'wf-rejected'

    # draft
    formdata = formdef.data_class()()
    formdata.data = {'0': 'foo@localhost'}
    formdata.user_id = local_user.id
    formdata.status = 'draft'
    formdata.store()

    resp = get_app(pub).post_json(
        sign_uri('/api/forms/test/%s/' % formdata.id, user=local_user),
        {'data': {'0': 'bar@localhost'}},
        status=403,
    )
    assert resp.json['err_desc'] == 'Formdata is not editable (still a draft).'


def test_formdata_with_workflow_data(pub, local_user):
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.id = '123'
    role.store()

    local_user.roles = [role.id]
    local_user.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = []
    workflow = Workflow.get_default_workflow()
    workflow.id = '2'
    workflow.store()
    formdef.workflow_id = workflow.id
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.store()

    formdef.data_class().wipe()
    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.status = 'wf-new'
    formdata.evolution[-1].status = 'wf-new'

    upload = PicklableUpload('test.txt', 'text/plain', 'ascii')
    upload.receive([b'test'])
    upload2 = PicklableUpload('test.txt', 'text/plain', 'ascii')
    upload2.receive([b'test'])
    formdata.workflow_data = {'blah': upload, 'blah2': upload2, 'xxx': 23}
    formdata.store()

    resp = get_app(pub).get(sign_uri('/api/forms/test/%s/' % formdata.id, user=local_user))
    assert resp.json['workflow']['data']['xxx'] == 23
    assert resp.json['workflow']['data']['blah']['filename'] == 'test.txt'
    assert resp.json['workflow']['data']['blah']['content_type'] == 'text/plain'
    assert base64.decodebytes(force_bytes(resp.json['workflow']['data']['blah']['content'])) == b'test'
    assert base64.decodebytes(force_bytes(resp.json['workflow']['data']['blah2']['content'])) == b'test'

    for params in [
        '',
        '?include-fields=on',
        '?include-evolution=on',
        '?include-roles=on',
        '?include-submission=on',
        '?include-workflow=on',
    ]:
        resp = get_app(pub).get(sign_uri('/api/forms/test/list%s' % params, user=local_user))
        if 'workflow' in params:
            assert 'workflow' in resp.json[0]
            assert 'data' not in resp.json[0]['workflow']
        else:
            assert 'workflow' not in resp.json[0]
    for params in ['?full=on', '?include-workflow-data=on']:
        resp = get_app(pub).get(sign_uri('/api/forms/test/list%s' % params, user=local_user))
        assert 'workflow' in resp.json[0]
        assert 'data' in resp.json[0]['workflow']

    resp = get_app(pub).get(
        sign_uri('/api/forms/test/%s/?include-workflow-data=off' % formdata.id, user=local_user)
    )
    assert 'workflow' in resp.json
    assert 'data' not in resp.json['workflow']
    for params in ['?full=on', '?include-workflow-data=on']:
        resp = get_app(pub).get(sign_uri('/api/forms/test/%s/%s' % (formdata.id, params), user=local_user))
        assert 'workflow' in resp.json
        assert 'data' in resp.json['workflow']

    resp = get_app(pub).get(
        sign_uri('/api/forms/test/%s/?include-workflow=off' % formdata.id, user=local_user)
    )
    assert 'workflow' in resp.json
    assert 'data' in resp.json['workflow']
    resp = get_app(pub).get(
        sign_uri(
            '/api/forms/test/%s/?include-workflow=off&include-workflow-data=off' % formdata.id,
            user=local_user,
        )
    )
    assert 'workflow' not in resp.json


def test_formdata_with_evolution_part_attachment(pub, local_user):
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.id = '123'
    role.store()

    local_user.roles = [role.id]
    local_user.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = []
    workflow = Workflow.get_default_workflow()
    workflow.id = '2'
    workflow.store()
    formdef.workflow_id = workflow.id
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.store()

    formdef.data_class().wipe()
    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.status = 'wf-new'
    formdata.evolution[-1].status = 'wf-new'
    formdata.evolution[-1].parts = [
        AttachmentEvolutionPart(
            'hello.txt', fp=io.BytesIO(b'test'), content_type='text/plain', varname='testfile'
        )
    ]
    formdata.store()

    resp = get_app(pub).get(sign_uri('/api/forms/test/%s/' % formdata.id, user=local_user))
    assert len(resp.json['evolution']) == 1
    assert len(resp.json['evolution'][0]['parts']) == 1
    part = resp.json['evolution'][0]['parts'][0]
    assert part['filename'] == 'hello.txt'
    assert part['content_type'] == 'text/plain'
    assert 'content' in part
    assert 'to' in part
    assert base64.decodebytes(force_bytes(part['content'])) == b'test'

    resp = get_app(pub).get(sign_uri('/api/forms/test/%s/?anonymise' % formdata.id, user=local_user))
    assert len(resp.json['evolution']) == 1
    assert 'parts' not in resp.json['evolution'][0]

    for params in [
        '',
        '?include-fields=on',
        '?include-roles=on',
        '?include-submission=on',
        '?include-workflow=on',
        '?include-workflow-data=on',
    ]:
        resp = get_app(pub).get(sign_uri('/api/forms/test/list%s' % params, user=local_user))
        assert 'evolution' not in resp.json[0]
        # check this doesn't get into list of forms API
        assert 'hello.txt' not in resp.text
    for params in ['?full=on', '?include-evolution=on']:
        resp = get_app(pub).get(sign_uri('/api/forms/test/list%s' % params, user=local_user))
        assert 'evolution' in resp.json[0]
        # check this doesn't get into list of forms API
        assert 'hello.txt' not in resp.text

    resp = get_app(pub).get(
        sign_uri('/api/forms/test/%s/?include-evolution=off' % formdata.id, user=local_user)
    )
    assert 'evolution' not in resp.json
    for params in ['?full=on', '?include-evolution=on']:
        resp = get_app(pub).get(sign_uri('/api/forms/test/%s/%s' % (formdata.id, params), user=local_user))
        assert 'evolution' in resp.json


def test_formdata_with_evolution_part_attachment_to(pub, local_user):
    Workflow.wipe()
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.id = '123'
    role.store()

    local_user.roles = [role.id]
    local_user.store()

    workflow = Workflow(name='test')
    st1 = workflow.add_status('Status1', 'st1')

    add_to_journal = st1.add_action('register-comment', id='_add_to_journal')
    add_to_journal.comment = 'HELLO WORLD'
    add_to_journal.attachments = ['{{form_var_file_raw}}']
    add_to_journal.to = [role.id]

    workflow.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.workflow_id = workflow.id
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.fields = [fields.FileField(id='1', label='File1', varname='file')]
    formdef.store()
    formdef.data_class().wipe()

    resp = get_app(pub).get('/test/')
    resp.forms[0]['f1$file'] = Upload('hello.txt', b'foobar', 'text/plain')
    resp = resp.forms[0].submit('submit')
    assert 'Check values then click submit.' in resp.text
    resp = resp.forms[0].submit('submit')
    assert resp.status_int == 302
    resp = resp.follow()
    assert 'The form has been recorded' in resp.text

    formdata = formdef.data_class().select()[0]
    resp = get_app(pub).get(sign_uri('/api/forms/test/%s/' % formdata.id, user=local_user))
    assert len(resp.json['evolution']) == 1
    assert len(resp.json['evolution'][0]['parts']) == 2
    assert resp.json['evolution'][0]['parts'][1]['type'] == 'workflow-comment'
    part = resp.json['evolution'][0]['parts'][0]
    assert part['type'] == 'workflow-attachment'
    assert part['filename'] == 'hello.txt'
    assert part['content_type'] == 'text/plain'
    assert part['to'] == ['123']
    assert 'content' in part
    assert base64.decodebytes(force_bytes(part['content'])) == b'foobar'

    resp = get_app(pub).get(sign_uri('/api/forms/test/%s/?anonymise' % formdata.id, user=local_user))
    assert len(resp.json['evolution']) == 1
    assert len(resp.json['evolution'][0]['parts']) == 1
    assert resp.json['evolution'][0]['parts'][0]['type'] == 'workflow-comment'

    # check this doesn't get into list of forms API
    for params in [
        '',
        '?include-fields=on',
        '?include-roles=on',
        '?include-submission=on',
        '?include-workflow=on',
        '?include-workflow-data=on',
    ]:
        resp = get_app(pub).get(sign_uri('/api/forms/test/list%s' % params, user=local_user))
        assert 'evolution' not in resp.json[0]
    for params in ['?full=on', '?include-evolution=on']:
        resp = get_app(pub).get(sign_uri('/api/forms/test/list%s' % params, user=local_user))
        assert len(resp.json[0]['evolution']) == 1
        assert len(resp.json[0]['evolution'][0]['parts']) == 1
        assert resp.json[0]['evolution'][0]['parts'][0]['type'] == 'workflow-comment'


def test_formdata_with_evolution_part_comment(pub, local_user):
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.id = '123'
    role.store()

    local_user.roles = [role.id]
    local_user.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = []
    workflow = Workflow.get_default_workflow()
    workflow.id = '2'
    workflow.store()
    formdef.workflow_id = workflow.id
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.store()

    formdef.data_class().wipe()
    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.status = 'wf-new'
    formdata.evolution[-1].status = 'wf-new'
    formdata.evolution[-1].parts = [WorkflowCommentPart('<p>hello world</p>', 'test')]
    formdata.store()

    resp = get_app(pub).get(sign_uri('/api/forms/test/%s/' % formdata.id, user=local_user))
    assert len(resp.json['evolution']) == 1
    assert len(resp.json['evolution'][0]['parts']) == 1
    part = resp.json['evolution'][0]['parts'][0]
    assert part == {
        'type': 'workflow-comment',
        'identifier': 'test',
        'comment': '<p>hello world</p>',
        'comment_plain_text': 'hello world',
    }

    resp = get_app(pub).get(sign_uri('/api/forms/test/%s/?anonymise' % formdata.id, user=local_user))
    assert len(resp.json['evolution']) == 1
    assert 'parts' not in resp.json['evolution'][0]


def test_formdata_with_roles(pub, local_user):
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()
    role2 = pub.role_class(name='test2')
    role2.store()
    role3 = pub.role_class(name='test3')
    role3.store()
    local_user.roles = [role.id]
    local_user.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.fields = []
    formdef.store()

    data_class = formdef.data_class()
    data_class.wipe()
    formdata = data_class()
    formdata.workflow_roles = {'_barfoo': role2.id, '_foobar': [role3.id]}
    formdata.just_created()
    formdata.status = 'wf-new'
    formdata.user_id = local_user.id
    formdata.store()

    for params in [
        '',
        '?include-fields=on',
        '?include-evolution=on',
        '?include-submission=on',
        '?include-workflow=on',
        '?include-workflow-data=on',
    ]:
        resp = get_app(pub).get(sign_uri('/api/forms/test/list%s' % params, user=local_user))
        assert 'roles' not in resp.json[0]
    for params in ['?full=on', '?include-roles=on']:
        resp = get_app(pub).get(sign_uri('/api/forms/test/list%s' % params, user=local_user))
        assert 'roles' in resp.json[0]

    resp = get_app(pub).get(sign_uri('/api/forms/test/%s/?include-roles=off' % formdata.id, user=local_user))
    assert 'roles' not in resp.json
    for params in ['?full=on', '?include-roles=on']:
        resp = get_app(pub).get(sign_uri('/api/forms/test/%s/%s' % (formdata.id, params), user=local_user))
        assert 'roles' in resp.json


def test_api_list_formdata(pub, local_user):
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    Workflow.wipe()
    workflow = Workflow.get_default_workflow()
    workflow.id = None
    workflow.name = 'test'
    workflow.possible_status = Workflow.get_default_workflow().possible_status[:]
    workflow.criticality_levels = [
        WorkflowCriticalityLevel(name='green'),
        WorkflowCriticalityLevel(name='yellow'),
        WorkflowCriticalityLevel(name='red'),
    ]
    workflow.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.workflow = workflow
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.fields = [
        fields.StringField(id='0', label='foobar', varname='foobar'),
        fields.ItemField(id='1', label='foobar3', varname='foobar3', items=['foo', 'bar', 'baz']),
        fields.FileField(id='2', label='foobar4', varname='file'),
    ]
    formdef.store()

    data_class = formdef.data_class()
    data_class.wipe()

    for i in range(30):
        formdata = data_class()
        upload = PicklableUpload('test.txt', 'text/plain', 'ascii')
        upload.receive([b'base64me'])
        formdata.data = {'0': 'FOO BAR %02d' % i, '2': upload}
        formdata.user_id = local_user.id
        if i % 4 == 0:
            formdata.data['1'] = 'foo'
            formdata.data['1_display'] = 'foo'
            formdata.criticality_level = 101
        elif i % 4 == 1:
            formdata.data['1'] = 'bar'
            formdata.data['1_display'] = 'bar'
            formdata.criticality_level = 102
        else:
            formdata.data['1'] = 'baz'
            formdata.data['1_display'] = 'baz'
            formdata.criticality_level = 103

        formdata.just_created()
        if i % 3 == 0:
            formdata.jump_status('new')
        elif i % 3 == 1:
            formdata.jump_status('just_submitted')
        else:
            formdata.jump_status('finished')
        if i % 7 == 0:
            formdata.backoffice_submission = True
            formdata.submission_channel = 'mail'
        formdata.receipt_time = make_aware(datetime.datetime(2018, 1, 2, 3, 4) + datetime.timedelta(hours=i))
        formdata.evolution[0].time = make_aware(
            datetime.datetime(2019, 1, 2, 3, 4) + datetime.timedelta(hours=i)
        )
        formdata.evolution[-1].time = make_aware(
            datetime.datetime(2020, 1, 2, 3, 4) + datetime.timedelta(hours=i)
        )
        formdata._store_all_evolution = True
        formdata.store()
    # a draft by user
    formdata = data_class()
    formdata.data = {'0': 'FOO BAR'}
    formdata.user_id = local_user.id
    formdata.just_created()
    formdata.status = 'draft'
    formdata.store()

    # check access is denied if the user has not the appropriate role
    resp = get_app(pub).get(sign_uri('/api/forms/test/list', user=local_user), status=403)

    # add proper role to user
    local_user.roles = [role.id]
    local_user.store()

    # check it now gets the data
    resp = get_app(pub).get(sign_uri('/api/forms/test/list', user=local_user))
    assert len(resp.json) == 30
    assert datetime.datetime.strptime(resp.json[0]['receipt_time'], '%Y-%m-%dT%H:%M:%S')
    assert 'fields' not in resp.json[0]

    # check getting full formdata
    resp = get_app(pub).get(sign_uri('/api/forms/test/list?full=on', user=local_user))
    assert len(resp.json) == 30
    assert 'receipt_time' in resp.json[0]
    assert 'fields' in resp.json[0]
    assert 'url' in resp.json[0]['fields']['file']
    assert 'content' not in resp.json[0]['fields']['file']  # no file content in full lists
    assert 'user' in resp.json[0]
    assert 'evolution' in resp.json[0]
    assert len(resp.json[0]['evolution']) == 2
    assert 'status' in resp.json[0]['evolution'][0]
    assert 'who' in resp.json[0]['evolution'][0]
    assert 'time' in resp.json[0]['evolution'][0]
    assert resp.json[0]['evolution'][0]['who']['id'] == local_user.id

    assert all('status' in x['workflow'] for x in resp.json)
    assert [x for x in resp.json if x['fields']['foobar'] == 'FOO BAR 00'][0]['submission'][
        'backoffice'
    ] is True
    assert [x for x in resp.json if x['fields']['foobar'] == 'FOO BAR 00'][0]['submission'][
        'channel'
    ] == 'mail'
    assert [x for x in resp.json if x['fields']['foobar'] == 'FOO BAR 01'][0]['submission'][
        'backoffice'
    ] is False
    assert [x for x in resp.json if x['fields']['foobar'] == 'FOO BAR 01'][0]['submission'][
        'channel'
    ] == 'web'

    # check filtered results
    resp = get_app(pub).get(sign_uri('/api/forms/test/list?filter-foobar3=foo', user=local_user))
    assert len(resp.json) == 8
    resp = get_app(pub).get(sign_uri('/api/forms/test/list?filter-foobar3=bar', user=local_user))
    assert len(resp.json) == 8
    resp = get_app(pub).get(sign_uri('/api/forms/test/list?filter-foobar3=baz', user=local_user))
    assert len(resp.json) == 14
    resp = get_app(pub).get(sign_uri('/api/forms/test/list?filter-foobar3=', user=local_user))
    assert len(resp.json) == 0

    resp = get_app(pub).get(sign_uri('/api/forms/test/list?filter-foobar=FOO BAR 03', user=local_user))
    assert len(resp.json) == 1

    # check filter on status
    resp = get_app(pub).get(sign_uri('/api/forms/test/list?filter=pending', user=local_user))
    assert len(resp.json) == 20
    resp = get_app(pub).get(
        sign_uri('/api/forms/test/list?filter=pending&filter-operator=eq', user=local_user)
    )
    assert len(resp.json) == 20
    resp = get_app(pub).get(
        sign_uri('/api/forms/test/list?filter=pending&filter-operator=ne', user=local_user)
    )
    assert len(resp.json) == 10
    local_user.is_admin = True
    local_user.store()
    resp = get_app(pub).get(
        sign_uri('/api/forms/test/list?filter=pending&filter-operator=ne', user=local_user)
    )
    assert len(resp.json) == 10
    local_user.is_admin = False
    local_user.store()
    resp = get_app(pub).get(
        sign_uri('/api/forms/test/list?filter=pending&filter-operator=foo', user=local_user), status=400
    )
    assert resp.json['err_desc'] == 'Invalid operator "foo" for "filter-operator".'
    resp = get_app(pub).get(sign_uri('/api/forms/test/list?filter=done', user=local_user))
    assert len(resp.json) == 10
    resp = get_app(pub).get(sign_uri('/api/forms/test/list?filter=done&filter-operator=eq', user=local_user))
    assert len(resp.json) == 10
    resp = get_app(pub).get(sign_uri('/api/forms/test/list?filter=done&filter-operator=ne', user=local_user))
    assert len(resp.json) == 20
    resp = get_app(pub).get(sign_uri('/api/forms/test/list?filter=done&filter-operator=in', user=local_user))
    assert len(resp.json) == 10
    resp = get_app(pub).get(
        sign_uri('/api/forms/test/list?filter=done&filter-operator=not_in', user=local_user)
    )
    assert len(resp.json) == 20
    resp = get_app(pub).get(
        sign_uri('/api/forms/test/list?filter=new|just_submitted&filter-operator=in', user=local_user)
    )
    assert len(resp.json) == 20
    resp = get_app(pub).get(
        sign_uri('/api/forms/test/list?filter=all|just_submitted&filter-operator=in', user=local_user)
    )
    assert len(resp.json) == 30
    resp = get_app(pub).get(
        sign_uri('/api/forms/test/list?filter=waiting|just_submitted&filter-operator=in', user=local_user)
    )
    assert len(resp.json) == 0
    resp = get_app(pub).get(
        sign_uri('/api/forms/test/list?filter=new|just_submitted&filter-operator=not_in', user=local_user)
    )
    assert len(resp.json) == 10
    resp = get_app(pub).get(
        sign_uri('/api/forms/test/list?filter=all|just_submitted&filter-operator=not_in', user=local_user)
    )
    assert len(resp.json) == 0
    resp = get_app(pub).get(
        sign_uri('/api/forms/test/list?filter=waiting|just_submitted&filter-operator=not_in', user=local_user)
    )
    assert len(resp.json) == 0
    resp = get_app(pub).get(sign_uri('/api/forms/test/list?filter=all', user=local_user))
    assert len(resp.json) == 30
    resp = get_app(pub).get(
        sign_uri('/api/forms/test/list?filter=waiting&filter-operator=eq', user=local_user)
    )
    assert len(resp.json) == 10
    resp = get_app(pub).get(
        sign_uri('/api/forms/test/list?filter=waiting&filter-operator=ne', user=local_user)
    )
    assert len(resp.json) == 20

    # check filter on last update time
    resp = get_app(pub).get(
        sign_uri(
            '/api/forms/test/list?filter-start-mtime=on&filter-start-mtime-value=2020-01-03', user=local_user
        )
    )
    assert len(resp.json) == 16
    resp = get_app(pub).get(
        sign_uri(
            '/api/forms/test/list?filter-start-mtime=on&filter-start-mtime-value=2020-01-03 10:00',
            user=local_user,
        )
    )
    assert len(resp.json) == 10
    resp = get_app(pub).get(
        sign_uri(
            '/api/forms/test/list?filter-end-mtime=on&filter-end-mtime-value=2020-01-03', user=local_user
        )
    )
    assert len(resp.json) == 14
    resp = get_app(pub).get(
        sign_uri(
            '/api/forms/test/list?filter-end-mtime=on&filter-end-mtime-value=2020-01-03 10:00',
            user=local_user,
        )
    )
    assert len(resp.json) == 20

    # check filter on criticality
    resp = get_app(pub).get(
        sign_uri(
            '/api/forms/test/list?filter-criticality_level=1',
            user=local_user,
        )
    )
    assert len(resp.json) == 8
    resp = get_app(pub).get(
        sign_uri(
            '/api/forms/test/list?filter-criticality_level=xxx',
            user=local_user,
        ),
        status=400,
    )
    assert resp.json == {
        'err': 1,
        'err_class': 'Invalid request',
        'err_code': 'invalid-request',
        'err_desc': 'Invalid value "xxx" for "filter-criticality_level".',
    }
    resp = get_app(pub).get(
        sign_uri(
            '/api/forms/test/list?filter-criticality_level=',
            user=local_user,
        ),
        status=400,
    )
    assert resp.json == {
        'err': 1,
        'err_class': 'Invalid request',
        'err_code': 'invalid-request',
        'err_desc': 'Invalid value "" for "filter-criticality_level".',
    }

    # check limit and offset
    resp_all = get_app(pub).get(sign_uri('/api/forms/test/list?filter=all', user=local_user))
    assert len(resp_all.json) == 30
    partial_resps = []
    for i in range(0, 48, 12):
        partial_resps.append(
            get_app(pub).get(
                sign_uri('/api/forms/test/list?filter=all&offset=%s&limit=12' % i, user=local_user)
            )
        )
    assert len(partial_resps[0].json) == 12
    assert len(partial_resps[1].json) == 12
    assert len(partial_resps[2].json) == 6
    assert len(partial_resps[3].json) == 0
    resp_all_ids = [x.get('id') for x in resp_all.json]
    resp_partial_ids = []
    for resp in partial_resps:
        resp_partial_ids.extend([x.get('id') for x in resp.json])
    assert resp_all_ids == resp_partial_ids

    # check error handling
    get_app(pub).get(sign_uri('/api/forms/test/list?filter=all&offset=plop', user=local_user), status=400)
    get_app(pub).get(sign_uri('/api/forms/test/list?filter=all&limit=plop', user=local_user), status=400)

    # just check ordering
    resp = get_app(pub).get(sign_uri('/api/forms/test/list?full=on&order_by=f0', user=local_user))
    assert [d['fields']['foobar'] for d in resp.json] == ['FOO BAR %02d' % i for i in range(0, 30)]

    resp = get_app(pub).get(sign_uri('/api/forms/test/list?full=on&order_by=-f0', user=local_user))
    assert [d['fields']['foobar'] for d in resp.json] == ['FOO BAR %02d' % i for i in range(29, -1, -1)]

    resp = get_app(pub).get(sign_uri('/api/forms/test/list?full=on&order_by=foobar', user=local_user))
    assert [d['fields']['foobar'] for d in resp.json] == ['FOO BAR %02d' % i for i in range(0, 30)]

    resp = get_app(pub).get(sign_uri('/api/forms/test/list?full=on&order_by=-foobar', user=local_user))
    assert [d['fields']['foobar'] for d in resp.json] == ['FOO BAR %02d' % i for i in range(29, -1, -1)]

    # check 400 on multiple order_by
    get_app(pub).get(sign_uri('/api/forms/test/list?full=on&order_by=f0,foobar', user=local_user), status=400)

    # check with uppercase field identifier
    formdef.fields[0].varname = 'fOobar'
    formdef.store()
    resp = get_app(pub).get(sign_uri('/api/forms/test/list?full=on&order_by=-fOobar', user=local_user))
    assert [d['fields']['fOobar'] for d in resp.json] == ['FOO BAR %02d' % i for i in range(29, -1, -1)]

    # check fts
    resp = get_app(pub).get(sign_uri('/api/forms/test/list?full=on&q=foo', user=local_user))
    assert len(resp.json) == 30
    resp = get_app(pub).get(sign_uri('/api/forms/test/list?full=on&q=baz', user=local_user))
    assert len(resp.json) == 14


def test_api_list_formdata_order_by_rank(pub, local_user):
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.fields = [
        fields.StringField(id='0', label='a', display_locations=['listings']),
        fields.StringField(id='1', label='b'),
    ]
    formdef.store()

    data_class = formdef.data_class()
    data_class.wipe()

    # 1st formdata with foo in "unimportant" field
    formdata1 = data_class()
    formdata1.data = {'1': 'FOO'}
    formdata1.just_created()
    formdata1.jump_status('new')
    formdata1.store()

    # 2nd formdata, with no foo
    formdata2 = data_class()
    formdata2.data = {}
    formdata2.just_created()
    formdata2.jump_status('new')
    formdata2.store()

    # 3rd formdata, with foo in "important" field
    formdata3 = data_class()
    formdata3.data = {'0': 'FOO'}
    formdata3.just_created()
    formdata3.jump_status('new')
    formdata3.store()

    # add proper role to user
    local_user.roles = [role.id]
    local_user.store()

    # check fts
    resp = get_app(pub).get(sign_uri('/api/forms/test/list?full=on&q=foo', user=local_user))
    assert len(resp.json) == 2
    assert [int(x['id']) for x in resp.json] == [formdata3.id, formdata1.id]

    resp = get_app(pub).get(sign_uri('/api/forms/test/list?full=on&q=föô', user=local_user))
    assert len(resp.json) == 2
    assert [int(x['id']) for x in resp.json] == [formdata3.id, formdata1.id]


def test_api_list_formdata_filter_status(pub, local_user):
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    Workflow.wipe()
    workflow = Workflow(name='noop')
    workflow.add_status('Pending', 'new')
    workflow.add_status('Ongoing', 'wip')
    workflow.add_status('Completed', 'done')
    workflow.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'foo'
    formdef.fields = []
    formdef.workflow_id = workflow.id
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.store()

    local_user.roles = [role.id]
    local_user.store()

    data_class = formdef.data_class()
    data_class.wipe()

    new = data_class()
    new.data = {}
    new.user_id = local_user.id
    new.just_created()
    new.jump_status('new')
    new.store()

    wip = data_class()
    wip.data = {}
    wip.user_id = local_user.id
    wip.just_created()
    wip.jump_status('wip')
    wip.store()

    resp = get_app(pub).get(sign_uri('/api/forms/foo/list?filter=all', user=local_user))
    assert len(resp.json) == 2

    # filter on id
    resp = get_app(pub).get(sign_uri('/api/forms/foo/list?filter=new', user=local_user))
    assert len(resp.json) == 1
    assert resp.json[0]['id'] == str(new.id)

    # filter on name
    resp = get_app(pub).get(sign_uri('/api/forms/foo/list?filter=Ongoing', user=local_user))
    assert len(resp.json) == 1
    assert resp.json[0]['id'] == str(wip.id)


def test_api_list_formdata_unknown_filter(pub, local_user):
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    local_user.roles = [role.id]
    local_user.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.fields = []
    formdef.store()

    data_class = formdef.data_class()
    data_class.wipe()
    for i in range(10):
        formdata = data_class()
        formdata.just_created()
        formdata.jump_status('new')
        formdata.store()

    resp = get_app(pub).get(sign_uri('/api/forms/test/list', user=local_user))
    assert len(resp.json) == 10

    resp = get_app(pub).get(sign_uri('/api/forms/test/list?filter-foobar=42', user=local_user), status=400)
    assert resp.json['err_desc'] == 'Invalid filter "foobar".'

    resp = get_app(pub).get(
        sign_uri('/api/forms/test/list?filter-foobar=42&filter-baz=35', user=local_user), status=400
    )
    assert resp.json['err_desc'] == 'Invalid filters "baz", "foobar".'

    resp = get_app(pub).get(
        sign_uri('/api/forms/test/list?filter-xxx-value=42&filter-yyy-operator=eq', user=local_user),
        status=400,
    )
    assert resp.json['err_desc'] == 'Unused parameters "filter-xxx-value", "filter-yyy-operator".'


def test_api_list_formdata_string_filter(pub, local_user):
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    local_user.roles = [role.id]
    local_user.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.fields = [
        fields.StringField(id=formdef.get_new_field_id(), label='String', varname='string'),
        fields.StringField(id='1', label='String2', varname='string2'),
    ]
    formdef.store()

    data_class = formdef.data_class()
    data_class.wipe()

    for i in range(5):
        formdata = data_class()
        formdata.data = {
            formdef.fields[0].id: 'FOO %s' % i,
            '1': '%s' % (9 + i),
        }
        if i == 3:
            # Empty values
            formdata.data = {
                formdef.fields[0].id: '',
                '1': '',
            }
        if i == 4:
            # None values
            formdata.data = {}
        formdata.user_id = local_user.id
        formdata.just_created()
        formdata.jump_status('new')
        formdata.store()

    resp = get_app(pub).get(sign_uri('/api/forms/test/list?filter-string=FOO 2', user=local_user))
    assert len(resp.json) == 1
    resp = get_app(pub).get(sign_uri('/api/forms/test/list?filter-string=', user=local_user))
    assert len(resp.json) == 1
    params = [
        ('eq', 'FOO 2', 1),
        ('ne', 'FOO 2', 4),
        ('lt', 'FOO 2', 3),
        ('lte', 'FOO 2', 4),
        ('gt', 'FOO 2', 0),
        ('gt', '42', 0),
        ('gte', 'FOO 2', 1),
        ('in', 'FOO 2', 1),
        ('in', 'FOO 2|FOO 1', 2),
        ('not_in', 'FOO 2', 3),
        ('not_in', 'FOO 2|FOO 1', 2),
        ('absent', 'on', 2),
        ('existing', 'on', 3),
        ('between', 'FOO 1|FOO 2', 1),
        ('between', 'FOO 2|FOO 1', 1),
        ('icontains', 'FOO', 3),
        ('icontains', 'foo', 3),
        ('icontains', '2', 1),
        ('ieq', 'foo 2', 1),
        ('ieq', '42', 0),
    ]
    for operator, value, result in params:
        resp = get_app(pub).get(
            sign_uri(
                '/api/forms/test/list?filter-string=%s&filter-string-operator=%s' % (value, operator),
                user=local_user,
            )
        )
        assert len(resp.json) == result

    resp = get_app(pub).get(
        sign_uri(
            '/api/forms/test/list?filter-string=plop&filter-string-operator=between',
            user=local_user,
        ),
        status=400,
    )
    assert resp.json['err_desc'] == 'Invalid value "plop" for operator "between" and filter "filter-string"'

    params = [
        ('eq', '10', 1),
        ('ne', '10', 3),
        ('lt', '10', 1),
        ('lte', '10', 2),
        ('gt', '10', 1),
        ('gt', '9', 2),
        ('gte', '10', 2),
        ('in', '10', 1),
        ('in', '10|9', 2),
        ('in', '10|42', 1),
        ('in', '10|a', 1),
        ('not_in', '10', 2),
        ('not_in', '10|9', 1),
        ('not_in', '10|42', 2),
        ('absent', 'on', 2),
        ('existing', 'on', 3),
        ('between', '9|10', 1),
        ('between', '10|9', 1),
    ]
    for operator, value, result in params:
        resp = get_app(pub).get(
            sign_uri(
                '/api/forms/test/list?filter-string2=%s&filter-string2-operator=%s' % (value, operator),
                user=local_user,
            )
        )
        assert len(resp.json) == result


def test_api_list_formdata_numeric_filter(pub, local_user):
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    local_user.roles = [role.id]
    local_user.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.fields = [
        fields.NumericField(id='2', label='Numeric', varname='numeric'),
    ]
    formdef.store()

    data_class = formdef.data_class()
    data_class.wipe()

    for i in range(5):
        formdata = data_class()
        formdata.data = {
            '2': '%.2f' % (3.2 + 0.8 * i),
        }
        if i == 3:
            # Empty values
            formdata.data = {
                '2': None,
            }
        if i == 4:
            # None values
            formdata.data = {}
        formdata.user_id = local_user.id
        formdata.just_created()
        formdata.jump_status('new')
        formdata.store()

    params = [
        ('eq', '4', 1),
        ('ne', '4', 4),
        ('lt', '4', 1),
        ('lte', '4', 2),
        ('lt', '4.1', 2),
        ('lte', '4.1', 2),
        ('gt', '4', 1),
        ('gt', '3.9', 2),
        ('gte', '4', 2),
        ('in', '4', 1),
        ('in', '3.2|4', 2),
        ('in', '4|42', 1),
        ('in', '4|a', 1),
        ('in', '4.00|a', 1),
        ('not_in', '4', 2),
        ('not_in', '3.2|4', 1),
        ('not_in', '3.2|42', 2),
        ('absent', 'on', 2),
        ('existing', 'on', 3),
        ('between', '3.1|4.5', 2),
        ('between', '3.3|4.5', 1),
        ('between', '4.5|3.1', 2),
    ]
    for operator, value, result in params:
        resp = get_app(pub).get(
            sign_uri(
                '/api/forms/test/list?filter-numeric=%s&filter-numeric-operator=%s' % (value, operator),
                user=local_user,
            )
        )
        assert len(resp.json) == result


def test_api_list_formdata_text_filter(pub, local_user):
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    local_user.roles = [role.id]
    local_user.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.fields = [
        fields.TextField(id=formdef.get_new_field_id(), label='Text', varname='text'),
    ]
    formdef.store()

    data_class = formdef.data_class()
    data_class.wipe()

    for i in range(5):
        formdata = data_class()
        formdata.data = {
            formdef.fields[0].id: 'FOO %s' % i,
        }
        if i == 3:
            # Empty values
            formdata.data = {
                formdef.fields[0].id: '',
            }
        if i == 4:
            # None values
            formdata.data = {}
        formdata.user_id = local_user.id
        formdata.just_created()
        formdata.jump_status('new')
        formdata.store()

    resp = get_app(pub).get(sign_uri('/api/forms/test/list?filter-text=FOO 2', user=local_user))
    assert len(resp.json) == 1
    params = [
        ('eq', 'FOO 2', 1),
        ('ne', 'FOO 2', 4),
        ('lt', 'FOO 2', 3),
        ('lte', 'FOO 2', 4),
        ('gt', 'FOO 2', 0),
        ('gte', 'FOO 2', 1),
        ('in', 'FOO 2', 1),
        ('in', 'FOO 2|FOO 1', 2),
        ('not_in', 'FOO 2', 3),
        ('not_in', 'FOO 2|FOO 1', 2),
        ('absent', 'on', 2),
        ('existing', 'on', 3),
        ('between', 'FOO 1|FOO 2', 1),
        ('between', 'FOO 2|FOO 1', 1),
        ('icontains', 'FOO', 3),
        ('icontains', 'foo', 3),
        ('icontains', '2', 1),
    ]
    for operator, value, result in params:
        resp = get_app(pub).get(
            sign_uri(
                '/api/forms/test/list?filter-text=%s&filter-text-operator=%s' % (value, operator),
                user=local_user,
            )
        )
        assert len(resp.json) == result

    resp = get_app(pub).get(
        sign_uri(
            '/api/forms/test/list?filter-text=plop&filter-text-operator=between',
            user=local_user,
        ),
        status=400,
    )
    assert resp.json['err_desc'] == 'Invalid value "plop" for operator "between" and filter "filter-text"'


def test_api_list_formdata_item_filter(pub, local_user):
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    local_user.roles = [role.id]
    local_user.store()

    NamedDataSource.wipe()
    data_source = NamedDataSource(name='foobar')
    data_source.data_source = {
        'type': 'jsonvalue',
        'value': json.dumps(
            [{'id': '9', 'text': 'foo'}, {'id': '10', 'text': 'bar'}, {'id': '11', 'text': 'baz'}]
        ),
    }
    data_source.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.fields = [
        fields.ItemField(id='0', label='Item', data_source={'type': 'foobar'}, varname='item'),
        fields.ItemField(id='1', label='Other Item', items=['foo', 'bar'], varname='item2'),
    ]
    formdef.store()

    data_class = formdef.data_class()
    data_class.wipe()

    for i in range(5):
        formdata = data_class()
        formdata.data = {}
        formdata.data = {
            '0': str(9 + i),
            '1': 'foo' if i % 2 else 'bar',
        }
        if i == 3:
            # Empty values
            formdata.data = {
                '0': '',
                '1': '',
            }
        if i == 4:
            # None values
            formdata.data = {}
        formdata.user_id = local_user.id
        formdata.just_created()
        formdata.jump_status('new')
        formdata.store()

    resp = get_app(pub).get(sign_uri('/api/forms/test/list?filter-item=9', user=local_user))
    assert len(resp.json) == 1
    resp = get_app(pub).get(sign_uri('/api/forms/test/list?filter-item=', user=local_user))
    assert len(resp.json) == 0
    params = [
        ('eq', '10', 1),
        ('ne', '10', 3),
        ('lt', '10', 1),
        ('lte', '10', 2),
        ('gt', '10', 1),
        ('gt', '9', 2),
        ('gte', '10', 2),
        ('in', '10', 1),
        ('in', '10|9', 2),
        ('in', '10|42', 1),
        ('in', '10|a', 1),
        ('not_in', '10', 2),
        ('not_in', '10|9', 1),
        ('not_in', '10|42', 2),
        ('absent', 'on', 2),
        ('existing', 'on', 3),
        ('between', '9|10', 1),
        ('between', '10|9', 1),
    ]
    for operator, value, result in params:
        resp = get_app(pub).get(
            sign_uri(
                '/api/forms/test/list?filter-item=%s&filter-item-operator=%s' % (value, operator),
                user=local_user,
            )
        )
        assert len(resp.json) == result

    resp = get_app(pub).get(sign_uri('/api/forms/test/list?filter-item2=foo', user=local_user))
    assert len(resp.json) == 1
    params = [
        ('eq', 'foo', 1),
        ('ne', 'foo', 4),
        ('lt', 'foo', 3),
        ('lte', 'foo', 4),
        ('gt', 'foo', 0),
        ('gt', '42', 0),
        ('gte', 'foo', 1),
        ('in', 'foo', 1),
        ('in', 'foo|bar', 3),
        ('in', 'foo|baz', 1),
        ('not_in', 'foo', 3),
        ('not_in', 'foo|bar', 1),
        ('not_in', 'foo|42', 3),
        ('absent', 'on', 2),
        ('existing', 'on', 3),
        ('between', 'bar|foo', 2),
        ('between', 'foo|bar', 2),
    ]
    for operator, value, result in params:
        resp = get_app(pub).get(
            sign_uri(
                '/api/forms/test/list?filter-item2=%s&filter-item2-operator=%s' % (value, operator),
                user=local_user,
            )
        )
        assert len(resp.json) == result


def test_api_list_formdata_item_filter_on_cards(pub, local_user, sql_queries):
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    local_user.roles = [role.id]
    local_user.store()

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'test'
    carddef.fields = [
        fields.StringField(id='0', label='String', varname='string'),
    ]
    carddef.digest_templates = {'default': '{{ form_var_string }}'}
    carddef.store()

    card_data_class = carddef.data_class()
    card_data_class.wipe()
    carddata = card_data_class()
    carddata.data = {'0': 'coin'}
    carddata.just_created()
    carddata.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.fields = [
        fields.ItemField(id='0', label='Item', data_source={'type': 'carddef:test'}, varname='item'),
    ]
    formdef.store()

    data_class = formdef.data_class()
    data_class.wipe()

    formdata = data_class()
    formdata.data = {'0': str(carddata.id)}
    formdata.user_id = local_user.id
    formdata.just_created()
    formdata.jump_status('new')
    formdata.store()

    sql_queries.clear()
    resp = get_app(pub).get(sign_uri('/api/forms/test/list?filter-item=%s' % carddata.id, user=local_user))
    assert len(resp.json) == 1
    carddata_sql_queries = [q for q in sql_queries if 'FROM carddata' in q]
    assert len(carddata_sql_queries) == 1
    assert ' id = ' in carddata_sql_queries[0]

    resp = get_app(pub).get(sign_uri('/api/forms/test/list?filter-item=', user=local_user))
    assert len(resp.json) == 0


def test_api_list_formdata_items_filter(pub, local_user):
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    local_user.roles = [role.id]
    local_user.store()

    NamedDataSource.wipe()
    data_source = NamedDataSource(name='foobar')
    # use large numbers as identifiers as they are concatenated in SQL and it should
    # not trigger any out-of-bounds SQL checks or Python pre-checks.
    data_source.data_source = {
        'type': 'jsonvalue',
        'value': json.dumps(
            [{'id': '9000', 'text': 'foo'}, {'id': '10000', 'text': 'bar'}, {'id': '11000', 'text': 'baz'}]
        ),
    }
    data_source.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.fields = [
        fields.ItemsField(id='0', label='Items', data_source={'type': 'foobar'}, varname='items'),
        fields.ItemsField(id='1', label='Other Item', items=['foo', 'bar', 'baz'], varname='items2'),
    ]
    formdef.store()

    data_class = formdef.data_class()
    data_class.wipe()

    for i in range(5):
        formdata = data_class()
        formdata.data = {}
        formdata.data = {
            '0': ['9000' if i % 2 else '11000', '10000'],
            '1': ['foo' if i % 2 else 'bar', 'baz'],
        }
        if i == 3:
            # Empty values
            formdata.data = {
                '0': [],
                '1': [],
            }
        if i == 4:
            # None values
            formdata.data = {}
        formdata.user_id = local_user.id
        formdata.just_created()
        formdata.jump_status('new')
        formdata.store()

    resp = get_app(pub).get(sign_uri('/api/forms/test/list?filter-items=11000', user=local_user))
    assert len(resp.json) == 2
    params = [
        ('eq', '11000', 2),
        ('eq', '10000', 3),
        ('ne', '9000', 4),
        ('ne', '10000', 2),
        ('lt', '10000', 1),
        ('lte', '10000', 3),
        ('gt', '10000', 2),
        ('gt', '9000', 3),
        ('gte', '11000', 2),
        ('in', '11000', 2),
        ('in', '11000|10000', 3),
        ('in', '11000|9000', 3),
        ('not_in', '11000', 3),
        ('not_in', '11000|10000', 2),
        ('not_in', '11000|9000', 2),
        ('not_in', '11001|9000', 4),
        ('absent', 'on', 2),
        ('existing', 'on', 3),
        ('between', '9000|10000', 1),
        ('between', '10000|9000', 1),
        ('between', '9000|9001', 1),
    ]
    for operator, value, result in params:
        resp = get_app(pub).get(
            sign_uri(
                '/api/forms/test/list?filter-items=%s&filter-items-operator=%s' % (value, operator),
                user=local_user,
            )
        )
        assert len(resp.json) == result

    params = [
        ('eq', 'foo', 1),
        ('ne', 'foo', 4),
        ('lt', 'foo', 3),
        ('lte', 'foo', 3),
        ('gt', 'foo', 0),
        ('gt', '42', 0),
        ('gte', 'foo', 1),
        ('in', 'foo', 1),
        ('in', 'foo|bar', 3),
        ('not_in', 'foo', 4),
        ('not_in', 'foo|bar', 2),
        ('absent', 'on', 2),
        ('existing', 'on', 3),
        ('between', 'bar|bazz', 3),
        ('between', 'bazz|bar', 3),
    ]
    for operator, value, result in params:
        resp = get_app(pub).get(
            sign_uri(
                '/api/forms/test/list?filter-items2=%s&filter-items2-operator=%s' % (value, operator),
                user=local_user,
            )
        )
        assert len(resp.json) == result


def test_api_list_formdata_bool_filter(pub, local_user):
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    local_user.roles = [role.id]
    local_user.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.fields = [
        fields.BoolField(id='0', label='Bool', varname='bool'),
    ]
    formdef.store()

    data_class = formdef.data_class()
    data_class.wipe()

    for i in range(4):
        formdata = data_class()
        formdata.data = {}
        if i < 3:  # None values for the last one
            formdata.data = {
                '0': bool(i % 2),
            }
        formdata.user_id = local_user.id
        formdata.just_created()
        formdata.jump_status('new')
        formdata.store()

    resp = get_app(pub).get(sign_uri('/api/forms/test/list?filter-bool=false', user=local_user))
    assert len(resp.json) == 2
    resp = get_app(pub).get(sign_uri('/api/forms/test/list?filter-bool=true', user=local_user))
    assert len(resp.json) == 1
    params = [
        ('eq', 'true', 1),
        ('ne', 'true', 3),
        ('ne', 'false', 2),
        ('absent', 'on', 1),
        ('existing', 'on', 3),
    ]
    for operator, value, result in params:
        resp = get_app(pub).get(
            sign_uri(
                '/api/forms/test/list?filter-bool=%s&filter-bool-operator=%s' % (value, operator),
                user=local_user,
            )
        )
        assert len(resp.json) == result
    for operator in ['lt', 'lte', 'gt', 'gte', 'in', 'not_in', 'between']:
        resp = get_app(pub).get(
            sign_uri(
                '/api/forms/test/list?filter-bool=true&filter-bool-operator=%s' % operator, user=local_user
            ),
            status=400,
        )
        assert resp.json['err_desc'] == 'Invalid operator "%s" for "filter-bool".' % operator


def test_api_list_formdata_date_filter(pub, local_user, freezer):
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    local_user.roles = [role.id]
    local_user.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.fields = [
        fields.DateField(id='0', label='Date', varname='date'),
    ]
    formdef.store()

    data_class = formdef.data_class()
    data_class.wipe()

    for i in range(4):
        formdata = data_class()
        formdata.data = {}
        if i < 3:  # None values for the last one
            formdata.data = {'0': time.strptime('2021-06-%02d' % (i + 10), '%Y-%m-%d')}
        formdata.user_id = local_user.id
        formdata.just_created()
        formdata.jump_status('new')
        formdata.store()

    for value in ['2021-06-11', '11/06/2021']:
        resp = get_app(pub).get(sign_uri('/api/forms/test/list?filter-date=%s' % value, user=local_user))
        assert len(resp.json) == 1
    params = [
        ('eq', '2021-06-11', 1),
        ('ne', '2021-06-11', 3),
        ('lt', '2021-06-11', 1),
        ('lte', '2021-06-11', 2),
        ('gt', '2021-06-11', 1),
        ('gte', '2021-06-11', 2),
        ('in', '2021-06-12', 1),
        ('in', '2021-06-12|2021-06-15', 1),
        ('not_in', '2021-06-12', 2),
        ('not_in', '2021-06-12|2021-06-15', 2),
        ('absent', 'on', 1),
        ('existing', 'on', 3),
        ('between', '2021-06-12|2021-06-15', 1),
        ('between', '2021-06-15|2021-06-12', 1),
    ]
    for operator, value, result in params:
        resp = get_app(pub).get(
            sign_uri(
                '/api/forms/test/list?filter-date=%s&filter-date-operator=%s' % (value, operator),
                user=local_user,
            )
        )
        assert len(resp.json) == result

    # special date filters
    freezer.move_to(datetime.datetime(2021, 6, 12, 11, 0))
    params = [
        ('is_today', 1),
        ('is_tomorrow', 0),
        ('is_yesterday', 1),
        ('is_this_week', 3),
    ]
    for operator, result in params:
        resp = get_app(pub).get(
            sign_uri(
                '/api/forms/test/list?filter-date=on&filter-date-operator=%s' % operator,
                user=local_user,
            )
        )
        assert len(resp.json) == result


def test_api_list_formdata_email_filter(pub, local_user):
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    local_user.roles = [role.id]
    local_user.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.fields = [
        fields.EmailField(id='0', label='Email', varname='email'),
    ]
    formdef.store()

    data_class = formdef.data_class()
    data_class.wipe()

    for i in range(5):
        formdata = data_class()
        formdata.data = {}
        formdata.data = {'0': 'a@localhost' if i % 2 else 'b@localhost'}
        if i == 3:
            # Empty values
            formdata.data = {
                '0': '',
            }
        if i == 4:
            # None values
            formdata.data = {}
        formdata.user_id = local_user.id
        formdata.just_created()
        formdata.jump_status('new')
        formdata.store()

    resp = get_app(pub).get(sign_uri('/api/forms/test/list?filter-email=a@localhost', user=local_user))
    assert len(resp.json) == 1
    params = [
        ('eq', 'a@localhost', 1),
        ('ne', 'a@localhost', 4),
        ('in', 'a@localhost', 1),
        ('in', 'a@localhost|b@localhost', 3),
        ('not_in', 'a@localhost', 3),
        ('not_in', 'a@localhost|b@localhost', 1),
        ('absent', 'on', 2),
        ('existing', 'on', 3),
        ('icontains', 'A@LOCAL', 1),
        ('icontains', 'C@LOCAL', 0),
    ]
    for operator, value, result in params:
        resp = get_app(pub).get(
            sign_uri(
                '/api/forms/test/list?filter-email=%s&filter-email-operator=%s' % (value, operator),
                user=local_user,
            )
        )
        assert len(resp.json) == result
    for operator in ['lt', 'lte', 'gt', 'gte', 'between']:
        resp = get_app(pub).get(
            sign_uri(
                '/api/forms/test/list?filter-email=a@localhost&filter-email-operator=%s' % operator,
                user=local_user,
            ),
            status=400,
        )
        assert resp.json['err_desc'] == 'Invalid operator "%s" for "filter-email".' % operator


def test_api_list_formdata_internal_id_filter(pub, local_user):
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    local_user.roles = [role.id]
    local_user.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.fields = []
    formdef.store()

    data_class = formdef.data_class()
    data_class.wipe()

    for i in range(11):
        formdata = data_class()
        formdata.data = {}
        formdata.user_id = local_user.id
        formdata.just_created()
        formdata.jump_status('new')
        formdata.store()

    resp = get_app(pub).get(sign_uri('/api/forms/test/list?filter-internal-id=1', user=local_user))
    assert len(resp.json) == 1
    resp = get_app(pub).get(sign_uri('/api/forms/test/list?filter-internal-id=2', user=local_user))
    assert len(resp.json) == 1
    resp = get_app(pub).get(sign_uri('/api/forms/test/list?filter-internal-id=42', user=local_user))
    assert len(resp.json) == 0

    params = [
        ('eq', '1', 1),
        ('ne', '1', 10),
        ('lt', '1', 0),
        ('lte', '1', 1),
        ('gt', '1', 10),
        ('gt', '10', 1),
        ('gte', '1', 11),
        ('in', '1|4', 2),
        ('not_in', '1|4', 9),
    ]
    for operator, value, result in params:
        resp = get_app(pub).get(
            sign_uri(
                '/api/forms/test/list?filter-internal-id=%s&filter-internal-id-operator=%s'
                % (value, operator),
                user=local_user,
            )
        )
        assert len(resp.json) == result
    resp = get_app(pub).get(
        sign_uri('/api/forms/test/list?filter-internal-id=blabla', user=local_user), status=400
    )
    assert resp.json['err_desc'] == 'Invalid value "blabla" for "filter-internal-id-value".'

    for operator in ['absent', 'existing', 'between']:
        resp = get_app(pub).get(
            sign_uri(
                '/api/forms/test/list?filter-internal-id=42&filter-internal-id-operator=%s' % operator,
                user=local_user,
            ),
            status=400,
        )
        assert resp.json['err_desc'] == 'Invalid operator "%s" for "filter-internal-id".' % operator

    # multi-ids
    resp = get_app(pub).get(
        sign_uri(
            '/api/forms/test/list?filter-internal-id=1&filter-internal-id=3&filter-internal-id-operator=eq',
            user=local_user,
        )
    )
    assert len(resp.json) == 2
    # multi-ids with single param
    resp = get_app(pub).get(
        sign_uri(
            '/api/forms/test/list?filter-internal-id=1,3&filter-internal-id-operator=eq',
            user=local_user,
        )
    )
    assert len(resp.json) == 2
    # multi-ids with another operator
    resp = get_app(pub).get(
        sign_uri(
            '/api/forms/test/list?filter-internal-id=1&filter-internal-id=3&filter-internal-id-operator=ne',
            user=local_user,
        )
    )
    assert len(resp.json) == 9
    # multi-ids with POST
    resp = get_app(pub).post(
        sign_uri('/api/forms/test/list', user=local_user),
        params='filter-internal-id=1,3&filter-internal-id-operator=eq',
    )
    assert len(resp.json) == 2

    for operator in ['lt', 'lte', 'gt', 'gte']:
        # list of values not allowed with theese operators
        resp = get_app(pub).get(
            sign_uri(
                '/api/forms/test/list?filter-internal-id=1&filter-internal-id=3&filter-internal-id-operator=%s'
                % operator,
                user=local_user,
            ),
            status=400,
        )
        assert (
            resp.json['err_desc']
            == 'Invalid value "[\'1\', \'3\']" for "filter-internal-id" and operator "%s".' % operator
        )

    # not integers
    resp = get_app(pub).get(
        sign_uri(
            '/api/forms/test/list?filter-internal-id=1&filter-internal-id=a&filter-internal-id-operator=eq',
            user=local_user,
        ),
        status=400,
    )
    assert (
        resp.json['err_desc'] == 'Invalid value "[\'1\', \'a\']" for "filter-internal-id" and operator "eq".'
    )


def test_api_list_formdata_number_filter(pub, local_user):
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    local_user.roles = [role.id]
    local_user.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.fields = []
    formdef.store()

    data_class = formdef.data_class()
    data_class.wipe()

    for i in range(2):
        formdata = data_class()
        formdata.data = {}
        formdata.user_id = local_user.id
        formdata.just_created()
        formdata.jump_status('new')
        formdata.store()

    resp = get_app(pub).get(sign_uri('/api/forms/test/list?filter-number=1-1', user=local_user))
    assert len(resp.json) == 1
    resp = get_app(pub).get(sign_uri('/api/forms/test/list?filter-number=1-2', user=local_user))
    assert len(resp.json) == 1
    resp = get_app(pub).get(sign_uri('/api/forms/test/list?filter-number=1-42', user=local_user))
    assert len(resp.json) == 0
    resp = get_app(pub).get(sign_uri('/api/forms/test/list?filter-number=42-1', user=local_user))
    assert len(resp.json) == 0


def test_api_list_formdata_block_field_filter(pub, local_user):
    NamedDataSource.wipe()
    data_source = NamedDataSource(name='foobar')
    data_source.data_source = {
        'type': 'jsonvalue',
        'value': json.dumps([{'id': '1', 'text': 'foo'}, {'id': '2', 'text': 'bar'}]),
    }
    data_source.store()

    BlockDef.wipe()
    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.StringField(id='1', label='String', varname='string'),
        fields.ItemField(id='2', label='Item', data_source={'type': 'foobar'}, varname='item'),
        fields.BoolField(id='3', label='Bool', varname='bool'),
        fields.DateField(id='4', label='Date', varname='date'),
        fields.EmailField(id='5', label='Email', varname='email'),
    ]
    block.store()

    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    local_user.roles = [role.id]
    local_user.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.fields = [
        fields.BlockField(
            id='0', label='Block Data', varname='blockdata', block_slug='foobar', max_items='3'
        ),
    ]
    formdef.store()

    data_class = formdef.data_class()
    data_class.wipe()

    for i in range(14):
        formdata = data_class()
        formdata.data = {
            '0': {
                'data': [
                    {
                        '1': 'plop%s' % i,
                        '2': '1' if i % 2 else '2',
                        '2_display': 'foo' if i % 2 else 'bar',
                        '2_structured': 'XXX' if i % 2 else 'YYY',
                        '3': bool(i % 2),
                        '4': '2021-06-%02d' % (i + 1),
                        '5': 'a@localhost' if i % 2 else 'b@localhost',
                    },
                ],
                'schema': {},  # not important here
            },
            '0_display': 'hello',
        }
        if i == 0:
            # 2 elements with values
            formdata.data['0']['data'].append(
                {
                    '1': 'plop%s' % (i + 1),
                    '2': '1',
                    '2_display': 'foo',
                    '2_structured': 'XXX',
                    '3': True,
                    '4': '2021-06-02',
                    '5': 'a@localhost',
                },
            )
        if i == 10:
            # 2 elements, the second without values
            formdata.data['0']['data'].append(
                {
                    '1': '',
                    '2': '',
                    '4': '',
                    '5': '',
                }
            )
        if i == 11:
            # 2 elements, the second with non values
            formdata.data['0']['data'].append({})
        if i == 12:
            # only one element, without values
            formdata.data = {
                '0': {
                    'data': [
                        {
                            '1': '',
                            '2': '',
                            '4': '',
                            '5': '',
                        }
                    ]
                }
            }
        if i == 13:
            # no element
            formdata.data = {}
        formdata.user_id = local_user.id
        formdata.just_created()
        formdata.jump_status('new')
        formdata.store()

    # string
    resp = get_app(pub).get(sign_uri('/api/forms/test/list?filter-blockdata_string=plop0', user=local_user))
    assert len(resp.json) == 1
    resp = get_app(pub).get(sign_uri('/api/forms/test/list?filter-blockdata_string=plop2', user=local_user))
    assert len(resp.json) == 1
    resp = get_app(pub).get(sign_uri('/api/forms/test/list?filter-blockdata_string=plop12', user=local_user))
    assert len(resp.json) == 0
    params = [
        ('eq', 'plop5', 1),
        ('ne', 'plop5', 13),
        ('ne', 'plop1', 12),
        ('lt', 'plop5', 8),
        ('lte', 'plop5', 9),
        ('gt', 'plop5', 4),
        ('gt', '42', 0),
        ('gte', 'plop5', 5),
        ('in', 'plop5', 1),
        ('in', 'plop5|plop4', 2),
        ('not_in', 'plop5', 13),
        ('not_in', 'plop5|plop4', 12),
        ('absent', 'on', 2),
        ('existing', 'on', 12),
        ('between', 'plop1|plop5', 7),
        ('between', 'plop5|plop1', 7),
        ('icontains', 'PLOP', 12),
        ('icontains', 'LOP1', 4),  # plop1 (twice), plop10, plop11
    ]
    for operator, value, result in params:
        resp = get_app(pub).get(
            sign_uri(
                '/api/forms/test/list?filter-blockdata_string=%s&filter-blockdata_string-operator=%s'
                % (value, operator),
                user=local_user,
            )
        )
        assert len(resp.json) == result
    # item
    resp = get_app(pub).get(sign_uri('/api/forms/test/list?filter-blockdata_item=1', user=local_user))
    assert len(resp.json) == 7
    resp = get_app(pub).get(sign_uri('/api/forms/test/list?filter-blockdata_item=2', user=local_user))
    assert len(resp.json) == 6
    resp = get_app(pub).get(sign_uri('/api/forms/test/list?filter-blockdata_item=3', user=local_user))
    assert len(resp.json) == 0
    params = [
        ('eq', '1', 7),
        ('ne', '1', 7),
        ('lt', '2', 7),
        ('lte', '1', 7),
        ('gt', '1', 6),
        ('gte', '2', 6),
        ('in', '1', 7),
        ('in', '1|2', 12),
        ('in', '1|42', 7),
        ('in', '1|a', 7),
        ('not_in', '1', 7),
        ('not_in', '1|2', 2),
        ('not_in', '1|42', 7),
        ('absent', 'on', 2),
        ('existing', 'on', 12),
        ('between', '1|2', 7),
        ('between', '1|3', 12),
        ('between', '3|1', 12),
    ]
    for operator, value, result in params:
        resp = get_app(pub).get(
            sign_uri(
                '/api/forms/test/list?filter-blockdata_item=%s&filter-blockdata_item-operator=%s'
                % (value, operator),
                user=local_user,
            )
        )
        assert len(resp.json) == result
    # bool
    resp = get_app(pub).get(sign_uri('/api/forms/test/list?filter-blockdata_bool=true', user=local_user))
    assert len(resp.json) == 7
    resp = get_app(pub).get(sign_uri('/api/forms/test/list?filter-blockdata_bool=false', user=local_user))
    assert len(resp.json) == 6
    resp = get_app(pub).get(
        sign_uri('/api/forms/test/list?filter-blockdata_bool=foobar', user=local_user), status=400
    )
    assert resp.json['err_desc'] == 'Invalid value "foobar" for "filter-blockdata_bool".'
    params = [
        ('eq', 'true', 7),
        ('ne', 'true', 7),
        ('absent', 'on', 2),
        ('existing', 'on', 12),
    ]
    for operator, value, result in params:
        resp = get_app(pub).get(
            sign_uri(
                '/api/forms/test/list?filter-blockdata_bool=%s&filter-blockdata_bool-operator=%s'
                % (value, operator),
                user=local_user,
            )
        )
        assert len(resp.json) == result
    for operator in ['lt', 'lte', 'gt', 'gte', 'in', 'not_in', 'between']:
        resp = get_app(pub).get(
            sign_uri(
                '/api/forms/test/list?filter-blockdata_bool=true&filter-blockdata_bool-operator=%s'
                % operator,
                user=local_user,
            ),
            status=400,
        )
        assert resp.json['err_desc'] == 'Invalid operator "%s" for "filter-blockdata_bool".' % operator
    # date
    resp = get_app(pub).get(
        sign_uri('/api/forms/test/list?filter-blockdata_date=2021-06-01', user=local_user)
    )
    assert len(resp.json) == 1
    resp = get_app(pub).get(
        sign_uri('/api/forms/test/list?filter-blockdata_date=2021-06-02', user=local_user)
    )
    assert len(resp.json) == 2
    resp = get_app(pub).get(
        sign_uri('/api/forms/test/list?filter-blockdata_date=02/06/2021', user=local_user)
    )
    assert len(resp.json) == 2
    params = [
        ('eq', '2021-06-02', 2),
        ('ne', '2021-06-02', 12),
        ('lt', '2021-06-02', 3),
        ('lte', '2021-06-02', 4),
        ('gt', '2021-06-02', 10),
        ('gte', '2021-06-02', 12),
        ('in', '2021-06-02', 2),
        ('in', '2021-06-02|2021-06-05', 3),
        ('not_in', '2021-06-02', 12),
        ('not_in', '2021-06-02|2021-06-05', 11),
        ('absent', 'on', 2),
        ('existing', 'on', 12),
        ('between', '2021-06-02|2021-06-05', 4),
        ('between', '2021-06-05|2021-06-02', 4),
    ]
    for operator, value, result in params:
        resp = get_app(pub).get(
            sign_uri(
                '/api/forms/test/list?filter-blockdata_date=%s&filter-blockdata_date-operator=%s'
                % (value, operator),
                user=local_user,
            )
        )
        assert len(resp.json) == result
    # email
    resp = get_app(pub).get(
        sign_uri('/api/forms/test/list?filter-blockdata_email=a@localhost', user=local_user)
    )
    assert len(resp.json) == 7
    resp = get_app(pub).get(
        sign_uri('/api/forms/test/list?filter-blockdata_email=b@localhost', user=local_user)
    )
    assert len(resp.json) == 6
    resp = get_app(pub).get(
        sign_uri('/api/forms/test/list?filter-blockdata_email=c@localhost', user=local_user)
    )
    assert len(resp.json) == 0
    params = [
        ('eq', 'a@localhost', 7),
        ('ne', 'a@localhost', 7),
        ('in', 'a@localhost', 7),
        ('in', 'a@localhost|b@localhost', 12),
        ('not_in', 'a@localhost', 7),
        ('not_in', 'a@localhost|b@localhost', 2),
        ('absent', 'on', 2),
        ('existing', 'on', 12),
    ]
    for operator, value, result in params:
        resp = get_app(pub).get(
            sign_uri(
                '/api/forms/test/list?filter-blockdata_email=%s&filter-blockdata_email-operator=%s'
                % (value, operator),
                user=local_user,
            )
        )
        assert len(resp.json) == result
    for operator in ['lt', 'lte', 'gt', 'gte', 'between']:
        resp = get_app(pub).get(
            sign_uri(
                '/api/forms/test/list?filter-blockdata_email=plop0&filter-blockdata_email-operator=%s'
                % operator,
                user=local_user,
            ),
            status=400,
        )
        assert resp.json['err_desc'] == 'Invalid operator "%s" for "filter-blockdata_email".' % operator
    # mix
    resp = get_app(pub).get(
        sign_uri(
            '/api/forms/test/list?filter-blockdata_item=1&filter-blockdata_string=plop1', user=local_user
        )
    )
    assert len(resp.json) == 2
    resp = get_app(pub).get(
        sign_uri(
            '/api/forms/test/list?filter-blockdata_item=2&filter-blockdata_string=plop1', user=local_user
        )
    )
    assert len(resp.json) == 1
    resp = get_app(pub).get(
        sign_uri(
            '/api/forms/test/list?filter-blockdata_item=1&filter-blockdata_string=plop0', user=local_user
        )
    )
    assert len(resp.json) == 1
    resp = get_app(pub).get(
        sign_uri(
            '/api/forms/test/list?filter-blockdata_item=2&filter-blockdata_string=plop0', user=local_user
        )
    )
    assert len(resp.json) == 1

    # just check ordering
    def get_string(d):
        if not d['fields']['blockdata_raw']:
            return None
        return d['fields']['blockdata_raw'][0]['string']

    plop_list = ['', 'plop0', 'plop1', 'plop10', 'plop11'] + ['plop%s' % i for i in range(2, 10)] + [None]
    reversed_plop_list = list(reversed(plop_list))

    resp = get_app(pub).get(sign_uri('/api/forms/test/list?full=on&order_by=f0-1', user=local_user))
    assert [get_string(d) for d in resp.json] == plop_list

    resp = get_app(pub).get(sign_uri('/api/forms/test/list?full=on&order_by=-f0-1', user=local_user))
    assert [get_string(d) for d in resp.json] == reversed_plop_list

    resp = get_app(pub).get(
        sign_uri('/api/forms/test/list?full=on&order_by=blockdata_string', user=local_user)
    )
    assert [get_string(d) for d in resp.json] == plop_list

    resp = get_app(pub).get(
        sign_uri('/api/forms/test/list?full=on&order_by=-blockdata_string', user=local_user)
    )
    assert [get_string(d) for d in resp.json] == reversed_plop_list


def test_api_anonymized_formdata(pub, local_user, admin_user):
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    local_user.roles = [role.id]
    local_user.store()

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
        fields.StringField(id='0', label='foobar', varname='foobar'),
        fields.ItemField(id='1', label='foobar3', varname='foobar3', items=['foo', 'bar', 'baz']),
        fields.FileField(id='2', label='foobar4', varname='file'),
    ]
    formdef.workflow_id = workflow.id
    formdef.store()

    data_class = formdef.data_class()
    data_class.wipe()

    for i in range(30):
        formdata = data_class()
        upload = PicklableUpload('test.txt', 'text/plain', 'ascii')
        upload.receive([b'base64me'])
        formdata.data = {'0': 'FOO BAR %d' % i, '2': upload}
        formdata.user_id = local_user.id
        if i % 4 == 0:
            formdata.data['1'] = 'foo'
            formdata.data['1_display'] = 'foo'
        elif i % 4 == 1:
            formdata.data['1'] = 'bar'
            formdata.data['1_display'] = 'bar'
        else:
            formdata.data['1'] = 'baz'
            formdata.data['1_display'] = 'baz'

        formdata.just_created()
        if i % 3 == 0:
            formdata.jump_status('st1')
        else:
            evo = Evolution(formdata=formdata)
            evo.who = admin_user.id
            evo.time = localtime()
            evo.status = 'wf-%s' % 'st2'
            formdata.evolution.append(evo)
            formdata.status = evo.status
        formdata.store()

    # check access is granted even if the user has not the appropriate role
    resp = get_app(pub).get(sign_uri('/api/forms/test/list?anonymise&full=on', user=local_user))
    assert len(resp.json) == 30
    assert 'receipt_time' in resp.json[0]
    assert 'fields' in resp.json[0]
    assert 'user' not in resp.json[0]
    assert 'file' not in resp.json[0]['fields']  # no file export in full lists
    assert 'foobar3' in resp.json[0]['fields']
    assert 'foobar' not in resp.json[0]['fields']
    assert 'evolution' in resp.json[0]
    assert len(resp.json[0]['evolution']) == 2
    assert 'status' in resp.json[0]['evolution'][0]
    assert 'who' not in resp.json[0]['evolution'][0]
    assert 'time' in resp.json[0]['evolution'][0]
    # check evolution made by other than _submitter are exported
    assert 'who' in resp.json[1]['evolution'][1]
    assert 'id' in resp.json[1]['evolution'][1]['who']
    assert 'email' in resp.json[1]['evolution'][1]['who']
    assert 'NameID' in resp.json[1]['evolution'][1]['who']
    assert 'name' in resp.json[1]['evolution'][1]['who']
    assert resp.json[0]['workflow']['status']['id'] == 'st2'
    assert resp.json[0]['workflow']['real_status']['id'] == 'st2'
    assert resp.json[2]['workflow']['status']['id'] == 'st1'
    assert resp.json[2]['workflow']['real_status']['id'] == 'st1'

    # check access is granted event if there is no user
    resp = get_app(pub).get(sign_uri('/api/forms/test/list?anonymise&full=on'))
    assert len(resp.json) == 30
    assert 'receipt_time' in resp.json[0]
    assert 'fields' in resp.json[0]
    assert 'user' not in resp.json[0]
    assert 'file' not in resp.json[0]['fields']  # no file export in full lists
    assert 'foobar3' in resp.json[0]['fields']
    assert 'foobar' not in resp.json[0]['fields']
    assert 'evolution' in resp.json[0]
    assert len(resp.json[0]['evolution']) == 2
    assert 'status' in resp.json[0]['evolution'][0]
    assert 'who' not in resp.json[0]['evolution'][0]
    assert 'time' in resp.json[0]['evolution'][0]
    assert resp.json[0]['workflow']['status']['id'] == 'st2'
    assert resp.json[0]['workflow']['real_status']['id'] == 'st2'
    assert resp.json[2]['workflow']['status']['id'] == 'st1'
    assert resp.json[2]['workflow']['real_status']['id'] == 'st1'

    # check anonymise is enforced on detail view
    resp = get_app(pub).get(sign_uri('/api/forms/test/%s/?anonymise' % resp.json[1]['id']))
    assert 'receipt_time' in resp.json
    assert 'fields' in resp.json
    assert 'user' not in resp.json
    assert 'file' not in resp.json['fields']  # no file export in detail
    assert 'foobar3' in resp.json['fields']
    assert 'foobar' not in resp.json['fields']
    assert 'evolution' in resp.json
    assert len(resp.json['evolution']) == 2
    assert 'status' in resp.json['evolution'][0]
    assert 'who' not in resp.json['evolution'][0]
    assert 'time' in resp.json['evolution'][0]
    # check evolution made by other than _submitter are exported
    assert 'who' in resp.json['evolution'][1]
    assert 'id' in resp.json['evolution'][1]['who']
    assert 'email' in resp.json['evolution'][1]['who']
    assert 'NameID' in resp.json['evolution'][1]['who']
    assert 'name' in resp.json['evolution'][1]['who']

    # check no crash with workflow_roles as None
    formdef.workflow_roles = None
    formdef.store()
    get_app(pub).get(sign_uri('/api/forms/test/list?anonymise&full=on', user=local_user))


@pytest.mark.parametrize('auth', ['signature', 'http-basic'])
def test_api_access_restrict_to_anonymised_data(pub, local_user, auth):
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    local_user.roles = [role.id]
    local_user.store()

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

    for i in range(10):
        formdata = data_class()
        formdata.data = {'1': 'FOO BAR1', '2': 'FOO BAR 2'}
        formdata.user_id = local_user.id
        formdata.just_created()
        if i % 3 == 0:
            formdata.jump_status('st1')
        else:
            formdata.jump_status('st2')
        formdata.store()

    # check normal API behaviour: get all data
    ApiAccess.wipe()
    access = ApiAccess()
    access.name = 'test'
    access.access_identifier = 'test'
    access.access_key = '12345'
    access.store()

    app = get_app(pub)

    if auth == 'http-basic':
        # there's not "defaults to admin" permissions in case of basic authentication.
        access.roles = [role]
        access.store()

        def get_url(url, **kwargs):
            app.set_authorization(('Basic', ('test', '12345')))
            return app.get(url, **kwargs)

    else:

        def get_url(url, **kwargs):
            return app.get(
                sign_uri(url, user=local_user, orig=access.access_identifier, key=access.access_key), **kwargs
            )

    resp = get_url('/api/forms/test/list?full=on&order_by=id')
    assert len(resp.json) == 10
    assert resp.json[0]['fields']['foobar'] == 'FOO BAR1'
    assert resp.json[0]['fields']['foobar2'] == 'FOO BAR 2'
    assert resp.json[0].get('user')
    assert resp.json[0]['workflow']['status']['id'] == 'st1'
    assert resp.json[0]['workflow']['real_status']['id'] == 'st1'
    assert resp.json[1]['workflow']['status']['id'] == 'st2'
    assert resp.json[1]['workflow']['real_status']['id'] == 'st2'

    # get a single formdata
    resp = get_url('/api/forms/test/%s/' % formdata.id)
    assert 'user' in resp.json

    # restrict API access to anonymised data
    access.restrict_to_anonymised_data = True
    access.store()

    resp = get_url('/api/forms/test/list?full=on&order_by=id')
    assert len(resp.json) == 10
    assert 'foobar' not in resp.json[0]['fields']
    assert resp.json[0]['fields']['foobar2'] == 'FOO BAR 2'
    assert not resp.json[0].get('user')
    assert resp.json[0]['workflow']['status']['id'] == 'st1'
    assert resp.json[0]['workflow']['real_status']['id'] == 'st1'
    assert resp.json[1]['workflow']['status']['id'] == 'st2'
    assert resp.json[1]['workflow']['real_status']['id'] == 'st2'

    # get a single formdata
    resp = get_url('/api/forms/test/%s/' % formdata.id)
    assert 'user' not in resp.json

    if auth == 'http-basic':
        # for basic HTTP authentication, check there's no access if roles are not given.
        access.roles = []
        access.store()

        get_url('/api/forms/test/list?full=on', status=403)
        get_url('/api/forms/test/%s/' % formdata.id, status=403)


def test_api_geojson_formdata(pub, local_user):
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.fields = [
        fields.StringField(id='0', label='foobar', varname='foobar'),
        fields.FileField(id='1', label='foobar1'),
    ]
    formdef.store()

    data_class = formdef.data_class()
    data_class.wipe()

    formdef.geolocations = {'base': 'Location'}
    formdef.store()

    # check access is denied if the user has not the appropriate role
    resp = get_app(pub).get(sign_uri('/api/forms/test/geojson', user=local_user), status=403)
    # even if there's an anonymse parameter
    resp = get_app(pub).get(sign_uri('/api/forms/test/geojson?anonymise', user=local_user), status=403)

    upload = PicklableUpload('test.txt', 'text/plain', 'ascii')
    upload.receive([b'base64me'])

    foobar = '<font color="red">FOO BAR</font>'
    username = '<font color="red">Jean Darmette</font>'

    data = {'0': foobar, '1': upload}
    local_user.name = username
    local_user.store()
    for i in range(30):
        formdata = data_class()
        formdata.geolocations = {'base': {'lat': 48, 'lon': 2}}
        formdata.data = data
        formdata.user_id = local_user.id
        formdata.just_created()
        if i % 3 == 0:
            formdata.jump_status('new')
        else:
            formdata.jump_status('finished')
        formdata.store()

    # add proper role to user
    local_user.roles = [role.id]
    local_user.store()

    # check it gets the data
    resp = get_app(pub).get(sign_uri('/api/forms/test/geojson', user=local_user))
    assert 'features' in resp.json
    assert len(resp.json['features']) == 10
    assert resp.json['features'][0]['properties']['id'] == '1-28'
    assert resp.json['features'][0]['properties']['raw_id'] == '28'
    assert resp.json['features'][0]['properties']['text'] == 'test #1-28'
    display_fields = resp.json['features'][0]['properties']['display_fields']
    assert len(display_fields) == 5
    for field in display_fields:
        if field['label'] == 'Number':
            assert field['varname'] == 'id'
            assert field['html_value'] == '1-28'
            assert field['value'] == '1-28'
        if field['label'] == 'User Label':
            assert field['varname'] == 'user_label'
            assert field['value'] == username
            assert field['html_value'] == '&lt;font color=&quot;red&quot;&gt;Jean Darmette&lt;/font&gt;'
        if field['label'] == 'foobar':
            assert field['varname'] == 'foobar'
            assert field['value'] == foobar
            assert field['html_value'] == '&lt;font color=&quot;red&quot;&gt;FOO BAR&lt;/font&gt;'
        if field['label'] == 'foobar1':
            assert field['varname'] is None
            assert field['value'] == 'test.txt'
            assert field['html_value'] == (
                '<div class="file-field"><a download="test.txt" href="http://example.net/backoffice/management/test/28/download?f=1">'
                '<span>test.txt</span></a></div>'
            )
            assert field['file_url'] == 'http://example.net/backoffice/management/test/28/download?f=1'
    field_varnames = [f['varname'] for f in display_fields]
    assert 'foobar' not in field_varnames

    # check full=on
    resp = get_app(pub).get(sign_uri('/api/forms/test/geojson?full=on', user=local_user))
    assert len(resp.json['features']) == 10
    display_fields = resp.json['features'][0]['properties']['display_fields']
    assert len(display_fields) == 10
    field_varnames = [f['varname'] for f in display_fields]
    assert 'foobar' in field_varnames

    # check with a filter
    resp = get_app(pub).get(sign_uri('/api/forms/test/geojson?filter=done', user=local_user))
    assert 'features' in resp.json
    assert len(resp.json['features']) == 20

    # check with http basic auth
    app = get_app(pub)
    app.authorization = ('Basic', ('user', 'password'))
    resp = app.get('/api/forms/test/geojson?email=%s' % local_user.email, status=401)

    # add authentication info
    pub.load_site_options()
    pub.site_options.add_section('api-http-auth-geojson')
    pub.site_options.set('api-http-auth-geojson', 'user', 'password')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)

    resp = app.get('/api/forms/test/geojson?email=%s' % local_user.email)
    assert 'features' in resp.json
    assert len(resp.json['features']) == 10

    # check 404 if the formdef doesn't have geolocation support
    formdef.geolocations = {}
    formdef.store()
    resp = get_app(pub).get(sign_uri('/api/forms/test/geojson', user=local_user), status=404)


def test_api_geojson_formdata_related_field(pub, local_user):
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    # add role to user
    local_user.roles = [role.id]
    local_user.store()

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'test'
    carddef.fields = [
        fields.FileField(id='1', label='foobar'),
    ]
    carddef.digest_templates = {'default': 'plop'}
    carddef.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.fields = [
        fields.ItemField(id='1', label='item', varname='foo', data_source={'type': 'carddef:test'}),
    ]
    formdef.geolocations = {'base': 'Location'}
    formdef.store()

    data_class = formdef.data_class()
    data_class.wipe()
    upload = PicklableUpload('test.txt', 'text/plain', 'ascii')
    upload.receive([b'base64me'])
    carddata = carddef.data_class()()
    carddata.data = {'1': upload}
    carddata.just_created()
    carddata.store()

    formdata = formdef.data_class()()
    formdata.data = {'1': str(carddata.id)}
    formdata.data['1_display'] = formdef.fields[0].store_display_value(formdata.data, '1')
    formdata.data['1_structured'] = formdef.fields[0].store_structured_value(formdata.data, '1')
    formdata.geolocations = {'base': {'lat': 48, 'lon': 2}}
    formdata.just_created()
    formdata.store()

    resp = get_app(pub).get(sign_uri('/api/forms/test/geojson?filter=all', user=local_user))
    assert len(resp.json['features']) == 1
    assert resp.json['features'][0]['properties']['id'] == '1-1'

    # check full=on
    resp = get_app(pub).get(sign_uri('/api/forms/test/geojson?filter=all&full=on', user=local_user))
    assert len(resp.json['features']) == 1
    properties = {x['label']: x['value'] for x in resp.json['features'][0]['properties']['display_fields']}
    assert properties['item - foobar'] == 'test.txt'


def test_api_geojson_formdata_file_in_block_field(pub, local_user):
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    # add role to user
    local_user.roles = [role.id]
    local_user.store()

    BlockDef.wipe()
    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.FileField(id='123', label='file', varname='foo'),
    ]
    block.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.fields = [
        fields.BlockField(id='1', label='test', varname='blockdata', block_slug='foobar', max_items='3'),
    ]
    formdef.geolocations = {'base': 'Location'}
    formdef.store()

    data_class = formdef.data_class()
    data_class.wipe()
    upload = PicklableUpload('test.txt', 'text/plain', 'ascii')
    upload.receive([b'base64me'])

    formdata = formdef.data_class()()
    formdata.data = {'1': {'data': [{'123': upload}], 'schema': {'123': 'file'}}, '1_display': 'test.txt'}
    formdata.geolocations = {'base': {'lat': 48, 'lon': 2}}
    formdata.just_created()
    formdata.store()

    # get with blockfield
    resp = get_app(pub).get(sign_uri('/api/forms/test/geojson?filter=all&1=on', user=local_user))
    assert len(resp.json['features']) == 1
    assert resp.json['features'][0]['properties']['id'] == '1-1'
    assert resp.json['features'][0]['properties']['display_fields'][0]['value'] == 'test.txt'

    # get with file field in block as property
    resp = get_app(pub).get(sign_uri('/api/forms/test/geojson?filter=all&1-123=on', user=local_user))
    assert len(resp.json['features']) == 1
    assert resp.json['features'][0]['properties']['id'] == '1-1'
    assert resp.json['features'][0]['properties']['display_fields'][0]['value'] == 'test.txt'
    assert 'download?f=1$0$123' in resp.json['features'][0]['properties']['display_fields'][0]['html_value']

    # check full=on
    resp = get_app(pub).get(sign_uri('/api/forms/test/geojson?filter=all&full=on', user=local_user))
    assert len(resp.json['features']) == 1
    properties = {x['label']: x['value'] for x in resp.json['features'][0]['properties']['display_fields']}
    assert properties['test'] == 'test.txt'
    assert 'file' not in properties


def test_api_geojson_formdata_numeric_in_block_field(pub, local_user):
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    # add role to user
    local_user.roles = [role.id]
    local_user.store()

    BlockDef.wipe()
    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.NumericField(id='123', label='num', varname='foo'),
    ]
    block.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.fields = [
        fields.BlockField(id='1', label='test', varname='blockdata', block_slug='foobar', max_items='3'),
    ]
    formdef.geolocations = {'base': 'Location'}
    formdef.store()

    data_class = formdef.data_class()
    data_class.wipe()
    formdata = formdef.data_class()()
    formdata.data = {
        '1': {'data': [{'123': 456}, {'123': 321}], 'schema': {'123': 'numeric'}},
        '1_display': '456, 321',
    }
    formdata.geolocations = {'base': {'lat': 48, 'lon': 2}}
    formdata.just_created()
    formdata.store()

    # get with blockfield
    resp = get_app(pub).get(sign_uri('/api/forms/test/geojson?filter=all&1=on', user=local_user))
    assert len(resp.json['features']) == 1
    assert resp.json['features'][0]['properties']['id'] == '1-1'
    assert resp.json['features'][0]['properties']['display_fields'][0]['value'] == '456, 321'

    # get with numeric field in block as property
    resp = get_app(pub).get(sign_uri('/api/forms/test/geojson?filter=all&1-123=on', user=local_user))
    assert len(resp.json['features']) == 1
    assert resp.json['features'][0]['properties']['id'] == '1-1'
    assert resp.json['features'][0]['properties']['display_fields'][0]['value'] == '456, 321'


def test_api_distance_filter(pub, local_user):
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.fields = []
    formdef.geolocations = {'base': 'Location'}
    formdef.store()
    data_class = formdef.data_class()
    data_class.wipe()

    for i in range(6):
        data = data_class()
        data.geolocations = {'base': {'lat': i, 'lon': i}}
        data.just_created()
        data.store()

    for i in range(4):
        data = data_class()
        data.geolocations = {'base': {'lat': i + 0.5, 'lon': i + 0.5}}
        data.just_created()
        data.jump_status('finished')
        data.store()

    # add proper role to user
    local_user.roles = [role.id]
    local_user.store()

    resp = get_app(pub).get(sign_uri('/api/forms/test/list', user=local_user))
    assert len(resp.json) == 10
    resp = get_app(pub).get(
        sign_uri('/api/forms/test/list?center_lat=1&center_lon=2&filter-distance=200000', user=local_user)
    )
    assert len(resp.json) == 5
    resp = get_app(pub).get(
        sign_uri('/api/forms/test/list?center_lat=1&center_lon=2&filter-distance=150000', user=local_user)
    )
    assert len(resp.json) == 3
    get_app(pub).get(sign_uri('/api/forms/test/list?filter-distance=150000', user=local_user), status=400)


@pytest.mark.parametrize('user', ['query-email', 'api-access', 'idp-api-client'])
@pytest.mark.parametrize('auth', ['signature', 'http-basic'])
@responses.activate
def test_api_ods_formdata(pub, local_user, user, auth):
    ApiAccess.wipe()

    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.uuid = 'ddbaf103-ea18-11ef-92cf-14ac60d82bbb'
    role.store()

    app = get_app(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.fields = [
        fields.StringField(id='0', label='foobar', varname='foobar'),
    ]
    formdef.store()

    data_class = formdef.data_class()
    data_class.wipe()

    if user == 'api-access':
        access = ApiAccess()
        access.name = 'test'
        access.access_identifier = 'test'
        access.access_key = '12345'
        access.store()

        if auth == 'http-basic':

            def get_url(url, **kwargs):
                app.set_authorization(('Basic', ('test', '12345')))
                return app.get(url, **kwargs)

        else:

            def get_url(url, **kwargs):
                return app.get(sign_uri(url, orig=access.access_identifier, key=access.access_key), **kwargs)

    elif user == 'idp-api-client':
        if auth == 'signature':
            pytest.skip('signature authentication requires local user')

        def get_url(url, **kwargs):
            app.set_authorization(('Basic', ('test', '12345')))
            return app.get(url, **kwargs)

        responses.post(
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

    else:
        if auth == 'http-basic':
            pytest.skip('http basic authentication requires ApiAccess')

        def get_url(url, **kwargs):
            return app.get(sign_uri(url, user=local_user), **kwargs)

    # check access is denied if the user has not the appropriate role
    resp = get_url('/api/forms/test/ods', status=403)
    # even if there's an anonymise parameter
    resp = get_url('/api/forms/test/ods?anonymise', status=403)

    data = {'0': 'foobar'}
    for i in range(30):
        formdata = data_class()
        formdata.data = data
        formdata.user_id = local_user.id
        formdata.just_created()
        if i % 3 == 0:
            formdata.jump_status('new')
        else:
            formdata.jump_status('finished')
        formdata.store()

    # add proper role to user
    if user == 'api-access':
        access.roles = [role]
        access.store()
    elif user == 'idp-api-client':
        responses.post(
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
    else:
        local_user.roles = [role.id]
        local_user.store()

    # check it gets the data
    resp = get_url('/api/forms/test/ods')
    assert resp.content_type == 'application/vnd.oasis.opendocument.spreadsheet'

    # check it still gives a ods file when it's over the threashold for afterjobs
    with low_export_limit_threshold():
        resp = get_url('/api/forms/test/ods')
        assert resp.content_type == 'application/vnd.oasis.opendocument.spreadsheet'
        with zipfile.ZipFile(io.BytesIO(resp.body)) as zipf:
            with zipf.open('content.xml') as fd:
                ods_sheet = ET.parse(fd)
        assert len(ods_sheet.findall('.//{%s}table-row' % ods.NS['table'])) == 11

    # check it's not subject to category permissions
    role2 = pub.role_class(name='test2')
    role2.store()
    category = Category()
    category.name = 'Category 1'
    category.export_roles = [role2]
    category.store()
    formdef.category = category
    formdef.store()
    get_url('/api/forms/test/ods', status=200)

    if user == 'idp-api-client':
        # check a single api access object has been created
        assert ApiAccess.count() == 1
        api_access = ApiAccess.select()[0]
        assert api_access.idp_api_client
        assert api_access.access_identifier == '_idp_test'
        assert api_access.access_key is None


def test_api_global_geojson(pub, local_user):
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.fields = []
    formdef.store()

    data_class = formdef.data_class()
    data_class.wipe()

    formdef.geolocations = {'base': 'Location'}
    formdef.store()

    for i in range(30):
        formdata = data_class()
        formdata.geolocations = {'base': {'lat': 48, 'lon': 2}}
        formdata.user_id = local_user.id
        formdata.just_created()
        if i % 3 == 0:
            formdata.jump_status('new')
        else:
            formdata.jump_status('finished')
        formdata.store()

    # check empty content if user doesn't have the appropriate role
    resp = get_app(pub).get(sign_uri('/api/forms/geojson', user=local_user))
    assert 'features' in resp.json
    assert len(resp.json['features']) == 0

    # add proper role to user
    local_user.roles = [role.id]
    local_user.store()

    # check it gets the data
    resp = get_app(pub).get(sign_uri('/api/forms/geojson', user=local_user))
    assert 'features' in resp.json
    assert len(resp.json['features']) == 10

    # check with a filter
    resp = get_app(pub).get(sign_uri('/api/forms/geojson?status=done', user=local_user))
    assert 'features' in resp.json
    assert len(resp.json['features']) == 20


@pytest.mark.parametrize('user', ['query-email', 'api-access'])
@pytest.mark.parametrize('auth', ['signature', 'http-basic'])
@pytest.mark.parametrize('id_template', ['id_{{ form_internal_id }}', None])
def test_api_global_listing(pub, local_user, user, auth, id_template):
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    app = get_app(pub)

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'test'
    carddef.id_template = id_template
    carddef.store()
    carddef.data_class().wipe()

    carddata1 = carddef.data_class()()
    carddata1.just_created()
    carddata1.store()
    carddata2 = carddef.data_class()()
    carddata2.just_created()
    carddata2.store()

    if user == 'api-access':
        ApiAccess.wipe()
        access = ApiAccess()
        access.name = 'test'
        access.access_identifier = 'test'
        access.access_key = '12345'
        access.store()

        if auth == 'http-basic':

            def get_url(url, **kwargs):
                app.set_authorization(('Basic', ('test', '12345')))
                return app.get(url, **kwargs)

        else:

            def get_url(url, **kwargs):
                return app.get(sign_uri(url, orig=access.access_identifier, key=access.access_key), **kwargs)

    else:
        if auth == 'http-basic':
            pytest.skip('http basic authentication requires ApiAccess')

        def get_url(url, **kwargs):
            return app.get(sign_uri(url, user=local_user), **kwargs)

    # check there's no crash if there are no formdefs
    resp = get_url('/api/forms/')
    assert len(resp.json['data']) == 0

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.fields = [
        fields.StringField(id='0', label='foobar', varname='foobar'),
        fields.ItemField(id='1', label='Item', data_source={'type': 'carddef:test'}, varname='item'),
    ]
    formdef.store()

    data_class = formdef.data_class()
    data_class.wipe()

    formdef.store()

    related_prefix = '' if id_template is None else 'id_'

    for i in range(30):
        formdata = data_class()
        carddata = carddata1 if i < 10 else carddata2
        formdata.data = {
            '0': 'FOO BAR',
            '1': f'{related_prefix}{carddata.id}',
            '1_display': 'foo %s' % carddata.id,
        }
        formdata.user_id = local_user.id
        formdata.just_created()
        if i % 3 == 0:
            formdata.jump_status('new')
        else:
            formdata.jump_status('finished')
        formdata.store()

    # check empty content if user doesn't have the appropriate role
    resp = get_url('/api/forms/')
    assert len(resp.json['data']) == 0

    # add proper role to user
    if user == 'api-access':
        access.roles = [role]
        access.store()
    else:
        local_user.roles = [role.id]
        local_user.store()

    # check it gets the data
    resp = get_url('/api/forms/')
    assert len(resp.json['data']) == 10

    # check with a filter
    resp = get_url('/api/forms/?status=done')
    assert len(resp.json['data']) == 20

    # check with related
    for bad_value in ['foo', 'foo:foo']:
        resp = get_url('/api/forms/?related=%s' % bad_value)
        assert len(resp.json['data']) == 0

    for unknown in ['carddef:test:42', 'formdef:test:1', 'carddef:unknown:1', 'foo:foo:foo']:
        resp = get_url('/api/forms/?related=%s' % unknown)
        assert len(resp.json['data']) == 0

    resp = get_url(f'/api/forms/?related=carddef:test:{related_prefix}1')
    assert len(resp.json['data']) == 4

    resp = get_url(f'/api/forms/?related=carddef:test:{related_prefix}2')
    assert len(resp.json['data']) == 6

    # check limit/offset
    resp = get_url('/api/forms/?status=done&limit=5')
    assert len(resp.json['data']) == 5
    resp = get_url('/api/forms/?status=done&offset=5&limit=5')
    assert len(resp.json['data']) == 5
    resp = get_url('/api/forms/?status=done&offset=18&limit=5')
    assert len(resp.json['data']) == 2

    # check error handling
    get_url('/api/forms/?status=', status=400)
    get_url('/api/forms/?status=xxx', status=400)
    get_url('/api/forms/?status=done&limit=plop', status=400)
    get_url('/api/forms/?status=done&offset=plop', status=400)
    get_url('/api/forms/?full=on', status=400)

    # check when there are missing statuses
    for formdata in data_class.select():
        formdata.status = 'wf-missing'
        formdata.store()
    resp = get_url('/api/forms/?status=all')
    assert resp.json['data'][0]['status'] is None
    assert 'unknown' in resp.json['data'][0]['title']


def test_api_global_listing_categories_filter(pub, local_user):
    Category.wipe()
    category1 = Category()
    category1.name = 'Category 1'
    category1.store()
    category2 = Category()
    category2.name = 'Category 2'
    category2.store()

    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()
    local_user.roles = [role.id]
    local_user.store()

    FormDef.wipe()
    formdef1 = FormDef()
    formdef1.name = 'test 1'
    formdef1.workflow_roles = {'_receiver': role.id}
    formdef1.fields = [
        fields.StringField(id='0', label='foobar', varname='foobar'),
    ]
    formdef1.category = category1
    formdef1.store()
    formdef2 = FormDef()
    formdef2.name = 'test 2'
    formdef2.workflow_roles = {'_receiver': role.id}
    formdef2.fields = [
        fields.StringField(id='0', label='foobar', varname='foobar'),
    ]
    formdef2.category = category2
    formdef2.store()

    data_class1 = formdef1.data_class()
    data_class1.wipe()
    data_class2 = formdef2.data_class()
    data_class2.wipe()

    for _ in range(2):
        formdata = data_class1()
        formdata.data = {'0': 'FOO BAR'}
        formdata.user_id = local_user.id
        formdata.just_created()
        formdata.jump_status('new')
        formdata.store()

    for _ in range(3):
        formdata = data_class2()
        formdata.data = {'0': 'FOO BAZ'}
        formdata.user_id = local_user.id
        formdata.just_created()
        formdata.jump_status('new')
        formdata.store()

    resp = get_app(pub).get(sign_uri('/api/forms/', user=local_user))
    assert len(resp.json['data']) == 5
    resp = get_app(pub).get(sign_uri('/api/forms/?category_slugs=category-1', user=local_user))
    assert len(resp.json['data']) == 2
    resp = get_app(pub).get(sign_uri('/api/forms/?category_slugs=category-2', user=local_user))
    assert len(resp.json['data']) == 3
    resp = get_app(pub).get(sign_uri('/api/forms/?category_slugs=unknown', user=local_user))
    assert len(resp.json['data']) == 0
    resp = get_app(pub).get(sign_uri('/api/forms/?category_slugs=category-1,unknown', user=local_user))
    assert len(resp.json['data']) == 2
    resp = get_app(pub).get(sign_uri('/api/forms/?category_slugs=category-1,category-2', user=local_user))
    assert len(resp.json['data']) == 5


def test_api_global_listing_ignored_roles(pub, local_user):
    test_api_global_listing(pub, local_user, user='query-email', auth='signature', id_template=None)

    role = pub.role_class(name='test2')
    role.store()

    formdef = FormDef()
    formdef.name = 'test2'
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.fields = [
        fields.StringField(id='0', label='foobar', varname='foobar'),
    ]
    formdef.store()

    data_class = formdef.data_class()
    data_class.wipe()

    for _ in range(10):
        formdata = data_class()
        formdata.data = {'0': 'FOO BAR'}
        formdata.user_id = local_user.id
        formdata.just_created()
        formdata.jump_status('new')
        formdata.store()

    # considering roles
    resp = get_app(pub).get(sign_uri('/api/forms/?status=all&limit=100', user=local_user))
    assert len(resp.json['data']) == 30

    # ignore roles
    resp = get_app(pub).get(sign_uri('/api/forms/?status=all&limit=100&ignore-roles=on', user=local_user))
    assert len(resp.json['data']) == 40

    # check sensitive forms are not exposed
    formdef.skip_from_360_view = True
    formdef.store()
    resp = get_app(pub).get(sign_uri('/api/forms/?status=all&limit=100&ignore-roles=on', user=local_user))
    assert len(resp.json['data']) == 30


def test_api_include_anonymised(pub, local_user):
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    # add proper role to user
    local_user.roles = [role.id]
    local_user.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.fields = [
        fields.StringField(id='0', label='foobar', varname='foobar'),
    ]
    formdef.store()

    data_class = formdef.data_class()
    data_class.wipe()

    for _ in range(10):
        formdata = data_class()
        formdata.data = {'0': 'FOO BAR'}
        formdata.user_id = local_user.id
        formdata.just_created()
        formdata.jump_status('new')
        formdata.store()

    # anonymise the last one
    formdata.anonymise()

    resp = get_app(pub).get(sign_uri('/api/forms/', user=local_user))
    assert len(resp.json['data']) == 9

    resp = get_app(pub).get(sign_uri('/api/forms/?include-anonymised=on', user=local_user))
    assert len(resp.json['data']) == 10

    resp = get_app(pub).get(sign_uri('/api/forms/?include-anonymised=off', user=local_user))
    assert len(resp.json['data']) == 9


def test_global_forms_api_user_uuid_filter(pub, local_user):
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    local_user.roles = [role.id]
    local_user.store()

    another_user = get_publisher().user_class()
    another_user.name = 'Another'
    another_user.name_identifiers = ['ABCDEF']
    another_user.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [
        fields.StringField(id='0', label='foobar', varname='foobar'),
    ]
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.store()

    data_class = formdef.data_class()
    data_class.wipe()

    # a submitted form
    formdata1 = data_class()
    formdata1.data = {'0': 'FOO BAR'}
    formdata1.user_id = local_user.id
    formdata1.just_created()
    formdata1.jump_status('new')
    formdata1.store()

    # a submitted form for another user
    formdata2 = data_class()
    formdata2.data = {'0': 'FOO BAR'}
    formdata2.user_id = another_user.id
    formdata2.just_created()
    formdata2.jump_status('new')
    formdata2.store()

    # another submitted form for another user
    formdata3 = data_class()
    formdata3.data = {'0': 'FOO BAR'}
    formdata3.user_id = another_user.id
    formdata3.just_created()
    formdata3.jump_status('new')
    formdata3.store()

    # a draft by user
    formdata = data_class()
    formdata.data = {'0': 'FOO BAR'}
    formdata.user_id = local_user.id
    formdata.status = 'draft'
    formdata.store()

    # an anonymous draft
    formdata = data_class()
    formdata.data = {'0': 'FOO BAR'}
    formdata.user_id = None
    formdata.status = 'draft'
    formdata.store()

    def get_ids(url):
        resp = get_app(pub).get(url)
        return {int(x['form_number_raw']) for x in resp.json['data']}

    resp = get_ids(sign_uri('/api/forms/?status=all', user=local_user))
    assert resp == {formdata1.id, formdata2.id, formdata3.id}

    resp = get_ids(sign_uri('/api/forms/?filter-user-uuid=ABCDEF', user=local_user))
    assert resp == {formdata2.id, formdata3.id}

    resp = get_ids(sign_uri('/api/forms/?filter-user-uuid=nonexistent', user=local_user))
    assert resp == set()

    # remove role
    local_user.roles = []
    local_user.store()

    resp = get_ids(sign_uri('/api/forms/?status=all', user=local_user))
    assert resp == set()


def test_api_ics_formdata(pub, local_user, ics_data):
    role = pub.role_class.select()[0]

    # check access is denied if the user has not the appropriate role
    resp = get_app(pub).get(sign_uri('/api/forms/test/ics/foobar', user=local_user), status=403)
    # even if there's an anonymse parameter
    resp = get_app(pub).get(sign_uri('/api/forms/test/ics/foobar?anonymise', user=local_user), status=403)

    # add proper role to user
    local_user.roles = [role.id]
    local_user.store()

    def remove_dtstamp(body):
        # remove dtstamp as the precise timing may vary between two consecutive
        # calls and we shouldn't care.
        return re.sub('DTSTAMP:.*', 'DTSTAMP:--', body)

    # check 404 on incomplete ics url access
    assert get_app(pub).get(sign_uri('/api/forms/test/ics/', user=local_user), status=404)

    # check it gets the data
    resp = get_app(pub).get(sign_uri('/api/forms/test/ics/foobar', user=local_user))
    resp2 = get_app(pub).get(sign_uri('/api/forms/test/ics/foobar/', user=local_user))
    assert remove_dtstamp(resp.text) == remove_dtstamp(resp2.text)
    assert resp.headers['content-type'] == 'text/calendar; charset=utf-8'
    assert resp.text.count('BEGIN:VEVENT') == 10
    # check that description contains form name, display id, workflow status,
    # backoffice url and attached user
    pattern = re.compile(r'DESCRIPTION:testé \| 1-\d+ \| New', re.MULTILINE)
    m = pattern.findall(resp.text)
    assert len(m) == 10
    assert resp.text.count('Jean Darmette') == 10
    assert resp.text.count('DTSTART') == 10

    # check formdata digest summary and description contains the formdata digest
    pattern = re.compile(r'SUMMARY:testé #1-\d+ - plöp \d{4}-\d{2}-\d{2} \d{2}:\d{2} plÔp', re.MULTILINE)
    m = pattern.findall(resp.text)
    assert len(m) == 10
    assert resp.text.count(r'plöp') == 20

    # check with a filter
    resp = get_app(pub).get(sign_uri('/api/forms/test/ics/foobar?filter=done', user=local_user))
    assert resp.text.count('BEGIN:VEVENT') == 20
    pattern = re.compile(r'DESCRIPTION:testé \| 1-\d+ \| Finished', re.MULTILINE)
    m = pattern.findall(resp.text)
    assert len(m) == 20
    assert resp.text.count('Jean Darmette') == 20

    # check 404 on erroneous field var
    resp = get_app(pub).get(sign_uri('/api/forms/test/ics/xxx', user=local_user), status=404)

    # check 404 on an erroneous field var for endtime
    resp = get_app(pub).get(sign_uri('/api/forms/test/ics/foobar/xxx', user=local_user), status=404)

    # check 404 on too many path elements
    resp = get_app(pub).get(sign_uri('/api/forms/test/ics/foobar/foobar2/xxx', user=local_user), status=404)

    # check ics data with start and end varnames
    resp = get_app(pub).get(sign_uri('/api/forms/test/ics/foobar/foobar2', user=local_user))
    resp2 = get_app(pub).get(sign_uri('/api/forms/test/ics/foobar/foobar2/', user=local_user))
    assert remove_dtstamp(resp.text) == remove_dtstamp(resp2.text)
    assert resp.text.count('DTSTART') == 10
    assert resp.text.count('DTEND') == 10


def test_api_ics_formdata_http_auth(pub, local_user, admin_user, ics_data):
    role = pub.role_class.select()[0]

    # check as admin
    app = login(get_app(pub))
    resp = app.get('/api/forms/test/ics/foobar', status=200)

    # no access
    app = get_app(pub)
    resp = app.get('/api/forms/test/ics/foobar?email=%s' % local_user.email, status=401)
    assert resp.headers['Www-Authenticate']

    # auth but no access
    app = get_app(pub)
    app.authorization = ('Basic', ('user', 'password'))
    resp = app.get('/api/forms/test/ics/foobar?email=%s' % local_user.email, status=401)

    # add authentication info
    pub.load_site_options()
    pub.site_options.add_section('api-http-auth-ics')
    pub.site_options.set('api-http-auth-ics', 'user', 'password')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)

    # check access is denied if the user has not the appropriate role
    resp = app.get('/api/forms/test/ics/foobar?email=%s' % local_user.email, status=403)

    # check access is denied if the user is not specified
    resp = app.get('/api/forms/test/ics/foobar', status=403)

    # add proper role to user
    local_user.roles = [role.id]
    local_user.store()

    # check it gets the data
    resp = app.get('/api/forms/test/ics/foobar?email=%s' % local_user.email, status=200)
    assert resp.headers['content-type'] == 'text/calendar; charset=utf-8'
    assert resp.text.count('BEGIN:VEVENT') == 10

    # check it fails with a different password
    app.authorization = ('Basic', ('user', 'password2'))
    resp = app.get('/api/forms/test/ics/foobar?email=%s' % local_user.email, status=401)


def test_api_ics_formdata_api_user(pub, local_user, admin_user, ics_data):
    role = pub.role_class.select()[0]

    ApiAccess.wipe()
    access = ApiAccess()
    access.name = 'test'
    access.access_identifier = 'test'
    access.access_key = '12345'
    access.store()

    app = get_app(pub)

    app.authorization = ('Basic', ('test', '12345'))
    resp = app.get('/api/forms/test/ics/foobar', status=403)

    # add proper role to user
    access.roles = [role]
    access.store()

    # check it gets the data
    resp = app.get('/api/forms/test/ics/foobar', status=200)
    assert resp.headers['content-type'] == 'text/calendar; charset=utf-8'
    assert resp.text.count('BEGIN:VEVENT') == 10

    # check it fails with a different password
    app.authorization = ('Basic', ('user', 'password2'))
    resp = app.get('/api/forms/test/ics/foobar', status=401)

    # check using query string authentication
    app.authorization = None
    resp = app.get('/api/forms/test/ics/foobar?api-user=test&api-key=12345', status=200)
    assert resp.headers['content-type'] == 'text/calendar; charset=utf-8'
    assert resp.text.count('BEGIN:VEVENT') == 10

    # invalid key
    resp = app.get('/api/forms/test/ics/foobar?api-user=test&api-key=123456', status=401)

    # missing role
    access.roles = []
    access.store()
    resp = app.get('/api/forms/test/ics/foobar?api-user=test&api-key=12345', status=403)


def test_api_ics_formdata_custom_view(pub, local_user, ics_data):
    role = pub.role_class.select()[0]

    formdef = FormDef.get_by_urlname('test')

    pub.custom_view_class.wipe()
    custom_view = pub.custom_view_class()
    custom_view.title = 'custom view'
    custom_view.formdef = formdef
    custom_view.columns = {'list': [{'id': '0'}]}
    custom_view.filters = {'filter-0': 'on', 'filter-0-value': 'foobar', 'filter-0-operator': 'ne'}
    custom_view.visibility = 'any'
    custom_view.store()

    # check access is denied if the user has not the appropriate role
    resp = get_app(pub).get(sign_uri('/api/forms/test/custom-view/ics/foobar', user=local_user), status=403)
    # even if there's an anonymise parameter
    resp = get_app(pub).get(
        sign_uri('/api/forms/test/custom-view/ics/foobar?anonymise', user=local_user), status=403
    )

    # add proper role to user
    local_user.roles = [role.id]
    local_user.store()

    def remove_dtstamp(body):
        # remove dtstamp as the precise timing may vary between two consecutive
        # calls and we shouldn't care.
        return re.sub('DTSTAMP:.*', 'DTSTAMP:--', body)

    # check it gets the data
    resp = get_app(pub).get(sign_uri('/api/forms/test/custom-view/ics/foobar', user=local_user))
    resp2 = get_app(pub).get(sign_uri('/api/forms/test/custom-view/ics/foobar/', user=local_user))
    assert remove_dtstamp(resp.text) == remove_dtstamp(resp2.text)
    assert resp.headers['content-type'] == 'text/calendar; charset=utf-8'
    assert resp.text.count('BEGIN:VEVENT') == 10

    # check ics data with start and end varnames
    resp = get_app(pub).get(sign_uri('/api/forms/test/custom-view/ics/foobar/foobar2', user=local_user))
    resp2 = get_app(pub).get(sign_uri('/api/forms/test/custom-view/ics/foobar/foobar2/', user=local_user))
    assert remove_dtstamp(resp.text) == remove_dtstamp(resp2.text)
    assert resp.text.count('DTSTART') == 10
    assert resp.text.count('DTEND') == 10


def test_api_ics_formdata_dtstart_type(pub, local_user, ics_data):
    role = pub.role_class.select()[0]
    local_user.roles = [role.id]
    local_user.store()

    resp = get_app(pub).get(sign_uri('/api/forms/test/ics/date/date/', user=local_user))
    assert 'DTSTART;VALUE=DATE:20140123\r\n' in resp.text
    assert 'DTEND;VALUE=DATE:20140123\r\n' in resp.text

    resp = get_app(pub).get(sign_uri('/api/forms/test/ics/foobar/foobar/', user=local_user))
    assert 'DTSTART;VALUE=DATE-TIME:20140123T120000\r\n' in resp.text
    assert 'DTEND;VALUE=DATE-TIME:20140123T120000\r\n' in resp.text

    # check using full variable names
    resp = get_app(pub).get(sign_uri('/api/forms/test/ics/form_var_date/form_var_date/', user=local_user))
    assert 'DTSTART;VALUE=DATE:20140123\r\n' in resp.text
    assert 'DTEND;VALUE=DATE:20140123\r\n' in resp.text

    resp = get_app(pub).get(sign_uri('/api/forms/test/ics/form_var_foobar/form_var_foobar/', user=local_user))
    assert 'DTSTART;VALUE=DATE-TIME:20140123T120000\r\n' in resp.text
    assert 'DTEND;VALUE=DATE-TIME:20140123T120000\r\n' in resp.text


def test_api_invalid_http_basic_auth(pub, local_user, admin_user, ics_data):
    app = get_app(pub)
    app.get(
        '/api/forms/test/ics/foobar?email=%s' % local_user.email,
        headers={'Authorization': 'Basic garbage'},
        status=401,
    )


@pytest.mark.parametrize('auth', ['signature', 'http-basic'])
def test_api_access_formdata_hidden_and_real_status(pub, local_user, auth):
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

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
    formdef.fields = []
    formdef.workflow_id = workflow.id
    formdef.store()

    data_class = formdef.data_class()
    data_class.wipe()

    formdata = data_class()
    formdata.data = {}
    formdata.just_created()
    formdata.jump_status('st1')
    formdata.jump_status('st2')
    formdata.store()

    ApiAccess.wipe()
    access = ApiAccess()
    access.name = 'test'
    access.access_identifier = 'test'
    access.access_key = '12345'
    access.roles = [role]
    access.store()

    app = get_app(pub)

    if auth == 'http-basic':

        def get_url(url, **kwargs):
            app.set_authorization(('Basic', ('test', '12345')))
            return app.get(url, **kwargs)

    else:

        def get_url(url, **kwargs):
            return app.get(
                sign_uri(url, user=local_user, orig=access.access_identifier, key=access.access_key), **kwargs
            )

    resp = get_url('/api/forms/test/list?full=on')
    assert len(resp.json) == 1
    assert not resp.json[0].get('user')
    assert resp.json[0]['workflow']['status']['id'] == 'st1'
    assert resp.json[0]['workflow']['real_status']['id'] == 'st2'

    # get a single formdata
    resp = get_url('/api/forms/test/%s/' % formdata.id)
    assert not resp.json.get('user')
    assert resp.json['workflow']['status']['id'] == 'st1'
    assert resp.json['workflow']['real_status']['id'] == 'st2'


def test_api_formdata_status_endpoint(pub, local_user):
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    Workflow.wipe()
    workflow = Workflow()
    st1 = workflow.add_status('Status1', 'st1')
    st2 = workflow.add_status('Status2', 'st2')
    goto = st1.add_action('choice')
    goto.label = 'Go to'
    goto.status = st2.id
    workflow.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.fields = []
    formdef.workflow_id = workflow.id
    formdef.store()

    data_class = formdef.data_class()
    data_class.wipe()

    formdata1 = data_class()
    formdata1.data = {}
    formdata1.just_created()
    formdata1.jump_status('st1')
    formdata1.store()

    formdata2 = data_class()
    formdata2.data = {}
    formdata2.just_created()
    formdata2.jump_status('st2')
    formdata2.store()

    ApiAccess.wipe()
    access = ApiAccess()
    access.name = 'test'
    access.access_identifier = 'test'
    access.access_key = '12345'
    access.roles = [role]
    access.store()

    app = get_app(pub)
    app.set_authorization(('Basic', ('test', '12345')))

    resp = app.get('/api/forms/test/%s/' % formdata1.id)
    assert resp.json['workflow']['status']['id'] == 'st1'

    resp = app.get('/api/forms/test/%s/' % formdata2.id)
    assert resp.json['workflow']['status']['id'] == 'st2'


def test_formdata_dict_type(pub, local_user):
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.id = '123'
    role.store()
    local_user.roles = [role.id]
    local_user.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.store()

    formdef.data_class().wipe()
    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()

    resp = get_app(pub).get(sign_uri('/api/forms/test/list?response_type=dict', user=local_user))
    assert resp.json['count'] == 1
    assert len(resp.json['data']) == 1
