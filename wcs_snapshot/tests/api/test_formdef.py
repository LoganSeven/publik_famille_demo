import base64
import datetime
import decimal
import json
import os
import time
import urllib.parse
from functools import partial

import pytest
import responses
from django.utils.encoding import force_str
from django.utils.timezone import localtime
from quixote import get_publisher

from wcs import fields, qommon
from wcs.api_utils import sign_url
from wcs.blocks import BlockDef
from wcs.categories import Category
from wcs.data_sources import NamedDataSource
from wcs.formdef import FormDef
from wcs.qommon.http_request import HTTPRequest
from wcs.qommon.upload_storage import PicklableUpload
from wcs.sql import ApiAccess
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


def _get_url(url, app, auth, access, user, **kwargs):
    if auth == 'http-basic':
        app.set_authorization(('Basic', ('test', '12345')))
        return app.get(url, **kwargs)

    return app.get(sign_uri(url, user=user, orig=access.access_identifier, key=access.access_key), **kwargs)


def _post_url(url, app, auth, access, user, **kwargs):
    if auth == 'http-basic':
        app.set_authorization(('Basic', ('test', '12345')))
        return app.post(url, **kwargs)

    return app.post(sign_uri(url, user=user, orig=access.access_identifier, key=access.access_key), **kwargs)


def _post_json_url(url, app, auth, access, user, **kwargs):
    if auth == 'http-basic':
        app.set_authorization(('Basic', ('test', '12345')))
        return app.post_json(url, **kwargs)

    return app.post_json(
        sign_uri(url, user=user, orig=access.access_identifier, key=access.access_key), **kwargs
    )


@pytest.fixture
def access(pub):
    ApiAccess.wipe()
    access = ApiAccess()
    access.name = 'test'
    access.access_identifier = 'test'
    access.access_key = '12345'
    access.store()
    return access


def test_formdef_list(pub):
    pub.role_class.wipe()
    role = pub.role_class(name='Foo bar')
    role.id = '14'
    role.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.description = 'plop'
    formdef.keywords = 'mobile, test'
    formdef.workflow_roles = {'_receiver': str(role.id)}
    formdef.fields = []
    formdef.store()

    # anonymous access -> 403
    resp1 = get_app(pub).get('/json', status=403)
    resp2 = get_app(pub).get('/', headers={'Accept': 'application/json'}, status=403)
    resp3 = get_app(pub).get('/api/formdefs/', status=403)

    # signed request
    resp1 = get_app(pub).get(sign_uri('/json'))
    resp2 = get_app(pub).get(sign_uri('/'), headers={'Accept': 'application/json'})
    resp3 = get_app(pub).get(sign_uri('/api/formdefs/'))
    assert resp1.json == resp2.json == resp3.json
    assert resp1.json['data'][0]['title'] == 'test'
    assert resp1.json['data'][0]['url'] == 'http://example.net/test/'
    assert resp1.json['data'][0]['redirection'] is False
    assert resp1.json['data'][0]['always_advertise'] is False
    assert resp1.json['data'][0]['description'] == 'plop'
    assert resp1.json['data'][0]['keywords'] == ['mobile', 'test']
    assert list(resp1.json['data'][0]['functions'].keys()) == ['_receiver']
    assert resp1.json['data'][0]['functions']['_receiver']['label'] == 'Recipient'
    assert resp1.json['data'][0]['functions']['_receiver']['role']['slug'] == role.slug
    assert resp1.json['data'][0]['functions']['_receiver']['role']['name'] == role.name
    assert 'count' not in resp1.json['data'][0]

    # backoffice_submission formdef : none
    resp1 = get_app(pub).get('/api/formdefs/?backoffice-submission=on', status=403)
    resp1 = get_app(pub).get(sign_uri('/api/formdefs/?backoffice-submission=on'))
    assert resp1.json['err'] == 0
    assert len(resp1.json['data']) == 0

    formdef.data_class().wipe()

    # a draft
    formdata = formdef.data_class()()
    formdata.data = {}
    formdata.just_created()
    formdata.status = 'draft'
    formdata.store()

    other_formdef = FormDef()
    other_formdef.name = 'test 2'
    other_formdef.fields = []
    other_formdef.store()
    other_formdata = other_formdef.data_class()()
    other_formdata.data = {}
    other_formdata.just_created()
    other_formdata.store()

    # formdata created:
    # - 1 day ago (=3*4)
    # - 7 days ago (=2*2)
    # - 29 days ago (=1*1)
    # - 31 days ago (=0)
    for days in [1, 1, 1, 7, 7, 29, 31]:
        formdata = formdef.data_class()()
        formdata.data = {}
        formdata.just_created()
        formdata.receipt_time = localtime() - datetime.timedelta(days=days)
        formdata.store()

    resp = get_app(pub).get(sign_uri('/api/formdefs/?include-count=on'))
    # 3*4 + 2*2 + 1*1
    assert resp.json['data'][0]['count'] == 17


def test_formdef_list_categories_filter(pub):
    Category.wipe()
    category1 = Category()
    category1.name = 'Category 1'
    category1.store()
    category2 = Category()
    category2.name = 'Category 2'
    category2.store()

    FormDef.wipe()
    formdef1 = FormDef()
    formdef1.name = 'test 1'
    formdef1.category_id = category1.id
    formdef1.store()
    formdef2 = FormDef()
    formdef2.name = 'test 2'
    formdef2.category_id = category2.id
    formdef2.store()

    resp = get_app(pub).get(sign_uri('/api/formdefs/'))
    assert resp.json['err'] == 0
    assert len(resp.json['data']) == 2

    resp = get_app(pub).get(sign_uri('/api/formdefs/?category_slugs=unknown'))
    assert resp.json['err'] == 0
    assert len(resp.json['data']) == 0

    resp = get_app(pub).get(sign_uri('/api/formdefs/?category_slugs=category-1'))
    assert resp.json['err'] == 0
    assert len(resp.json['data']) == 1

    resp = get_app(pub).get(sign_uri('/api/formdefs/?category_slugs=category-1,unknown'))
    assert resp.json['err'] == 0
    assert len(resp.json['data']) == 1

    resp = get_app(pub).get(sign_uri('/api/formdefs/?category_slugs=category-1,category-2'))
    assert resp.json['err'] == 0
    assert len(resp.json['data']) == 2


@pytest.mark.parametrize('auth', ['signature', 'http-basic'])
def test_limited_formdef_list(pub, local_user, access, auth):
    pub.role_class.wipe()
    role = pub.role_class(name='Foo bar')
    role.id = '14'
    role.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.description = 'plop'
    formdef.workflow_roles = {'_receiver': str(role.id)}
    formdef.fields = []
    formdef.store()

    app = get_app(pub)
    get_url = partial(_get_url, app=app, auth=auth, access=access, user=local_user)

    resp = get_app(pub).get(sign_uri('/api/formdefs/'))
    assert resp.json['err'] == 0
    assert len(resp.json['data']) == 1
    assert resp.json['data'][0]['authentication_required'] is False
    # not present in backoffice-submission formdefs
    resp = get_app(pub).get(sign_uri('/api/formdefs/?backoffice-submission=on'))
    assert resp.json['err'] == 0
    assert len(resp.json['data']) == 0

    # check it's not advertised
    formdef.roles = [role.id]
    formdef.store()
    resp = get_app(pub).get(sign_uri('/api/formdefs/'))
    resp2 = get_app(pub).get(sign_uri('/api/formdefs/?NameID='))
    resp3 = get_app(pub).get(sign_uri('/api/formdefs/?NameID=XXX'))
    resp4 = get_url('/api/formdefs/')
    assert resp.json['err'] == 0
    assert len(resp.json['data']) == 1  # advertised in naked calls (as done from combo)
    assert len(resp2.json['data']) == 0  # not advertised otherwise
    assert resp2.json == resp3.json == resp4.json
    # still not present in backoffice-submission formdefs
    resp = get_app(pub).get(sign_uri('/api/formdefs/?backoffice-submission=on'))
    assert resp.json['err'] == 0
    assert len(resp.json['data']) == 0

    # unless user has correct roles
    local_user.roles = [role.id]
    local_user.store()
    if auth == 'http-basic':
        access.roles = [role]
        access.store()
    resp = get_url('/api/formdefs/')
    assert resp.json['err'] == 0
    assert len(resp.json['data']) == 1

    local_user.roles = []
    local_user.store()
    if auth == 'http-basic':
        access.roles = []
        access.store()

    # check it's also included in anonymous/signed calls, but marked for
    # authentication
    resp = get_app(pub).get(sign_uri('/api/formdefs/'))
    assert resp.json['data'][0]
    assert resp.json['data'][0]['authentication_required'] is True

    # check it's advertised
    formdef.always_advertise = True
    formdef.store()
    resp = get_app(pub).get(sign_uri('/api/formdefs/'))
    resp2 = get_app(pub).get(sign_uri('/api/formdefs/?NameID='))
    resp3 = get_app(pub).get(sign_uri('/api/formdefs/?NameID=XXX'))
    resp4 = get_url('/api/formdefs/')
    assert resp.json['err'] == 0
    assert len(resp.json['data']) == 1
    assert resp.json['data'][0]['authentication_required']
    assert resp.json == resp2.json == resp3.json == resp4.json

    formdef.required_authentication_contexts = ['fedict']
    formdef.store()
    resp = get_app(pub).get(sign_uri('/api/formdefs/'))
    assert resp.json['data'][0]['required_authentication_contexts'] == ['fedict']


def test_formdef_list_redirection(pub):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.disabled = True
    formdef.disabled_redirection = 'http://example.net'
    formdef.fields = []
    formdef.store()

    resp1 = get_app(pub).get(sign_uri('/json'))
    assert resp1.json['err'] == 0
    assert resp1.json['data'][0]['title'] == 'test'
    assert resp1.json['data'][0]['url'] == 'http://example.net/test/'
    assert resp1.json['data'][0]['redirection'] is True
    assert 'count' not in resp1.json['data'][0]


@pytest.mark.parametrize('auth', ['signature', 'http-basic'])
def test_backoffice_submission_formdef_list(pub, local_user, access, auth):
    pub.role_class.wipe()
    role = pub.role_class(name='Foo bar')
    role.id = '14'
    role.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.description = 'plop'
    formdef.workflow_roles = {'_receiver': str(role.id)}
    formdef.fields = []
    formdef.store()

    formdef2 = FormDef()
    formdef2.name = 'ignore me'
    formdef2.fields = []
    formdef2.store()

    app = get_app(pub)
    get_url = partial(_get_url, app=app, auth=auth, access=access, user=local_user)

    resp = get_app(pub).get(sign_uri('/api/formdefs/?backoffice-submission=on'))
    assert resp.json['err'] == 0
    assert len(resp.json['data']) == 0

    # check it's not advertised ...
    formdef.backoffice_submission_roles = [role.id]
    formdef.store()
    resp = get_app(pub).get(sign_uri('/api/formdefs/?backoffice-submission=on'))
    assert resp.json['err'] == 0
    assert len(resp.json['data']) == 0

    # even if it's advertised on frontoffice
    formdef.always_advertise = True
    formdef.store()
    resp = get_app(pub).get(sign_uri('/api/formdefs/?backoffice-submission=on'))
    assert resp.json['err'] == 0
    assert len(resp.json['data']) == 0

    # even if user is admin
    local_user.is_admin = True
    local_user.store()
    resp = get_app(pub).get(
        sign_uri('/api/formdefs/?backoffice-submission=on&NameID=%s' % local_user.name_identifiers[0])
    )
    assert resp.json['err'] == 0
    assert len(resp.json['data']) == 0
    local_user.is_admin = False
    local_user.store()

    # ... unless user has correct roles
    local_user.roles = [role.id]
    local_user.store()
    if auth == 'http-basic':
        access.roles = [role]
        access.store()
    resp = get_url('/api/formdefs/?backoffice-submission=on')
    assert resp.json['err'] == 0
    assert len(resp.json['data']) == 1
    assert 'backoffice_submission_url' in resp.json['data'][0]

    # but not advertised if it's a redirection
    formdef.disabled = True
    formdef.disabled_redirection = 'http://example.net'
    formdef.store()
    resp = get_app(pub).get(sign_uri('/api/formdefs/?backoffice-submission=on'))
    assert resp.json['err'] == 0
    assert len(resp.json['data']) == 0


@pytest.mark.parametrize('auth', ['signature', 'http-basic'])
def test_backoffice_submission_formdef_list_search(pub, local_user, access, auth):
    pub.role_class.wipe()
    role = pub.role_class(name='Foo bar')
    role.store()

    FormDef.wipe()

    formdef = FormDef()
    formdef.name = 'test abc'
    formdef.description = 'plop'
    formdef.backoffice_submission_roles = [role.id]
    formdef.fields = []
    formdef.store()

    formdef2 = FormDef()
    formdef2.name = 'test def'
    formdef2.description = 'plop'
    formdef2.backoffice_submission_roles = [role.id]
    formdef2.fields = []
    formdef2.store()

    formdef3 = FormDef()
    formdef3.name = 'test ghi'
    formdef3.fields = []
    formdef3.store()

    app = get_app(pub)
    get_url = partial(_get_url, app=app, auth=auth, access=access, user=local_user)

    # check nothing is found
    resp = get_app(pub).get(sign_uri('/api/formdefs/?backoffice-submission=on&q=test'))
    assert resp.json['err'] == 0
    assert len(resp.json['data']) == 0

    # unless the proper role is given
    local_user.roles = [role.id]
    local_user.store()
    if auth == 'http-basic':
        access.roles = [role]
        access.store()

    resp = get_url('/api/formdefs/?backoffice-submission=on&q=test')
    assert len(resp.json['data']) == 2

    resp = get_url('/api/formdefs/?backoffice-submission=on&q=tes')
    assert len(resp.json['data']) == 2

    resp = get_url('/api/formdefs/?backoffice-submission=on&q=xyz')
    assert len(resp.json['data']) == 0

    resp = get_url('/api/formdefs/?backoffice-submission=on&q=abc')
    assert len(resp.json['data']) == 1

    formdef2.keywords = 'abc'
    formdef2.store()

    resp = get_url('/api/formdefs/?backoffice-submission=on&q=abc')
    assert len(resp.json['data']) == 2

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'Intervention du service hygiène, salubrité et environnement'
    formdef.backoffice_submission_roles = [role.id]
    formdef.fields = []
    formdef.store()
    resp = get_url('/api/formdefs/?backoffice-submission=on&q=salubrité')
    assert len(resp.json['data']) == 1


def test_formdef_schema(pub, access):
    Workflow.wipe()
    workflow = Workflow(name='test')
    st1 = workflow.add_status('Status1', 'st1')
    jump = st1.add_action('jump')
    jump.status = 'st2'
    jump.timeout = 100
    jump.mode = 'timeout'
    st2 = workflow.add_status('Status2', 'st2')
    jump = st2.add_action('jump')
    jump.status = 'st3'
    workflow.add_status('Status3', 'st3')
    workflow.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(workflow)
    workflow.backoffice_fields_formdef.fields = [
        fields.StringField(id='bo1', label='1st backoffice field', varname='backoffice_blah'),
    ]
    workflow.store()

    Category.wipe()
    cat = Category(name='Bar')
    cat.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [
        fields.StringField(id='0', label='foobar'),
        fields.ItemField(
            id='1',
            label='foobar1',
            varname='foobar1',
            data_source={
                'type': 'json',
                'value': 'http://datasource.com',
            },
        ),
        fields.ItemsField(
            id='2',
            label='foobar2',
            varname='foobar2',
            data_source={
                'type': 'jsonvalue',
                'value': json.dumps([{'id': i, 'text': 'label %s' % i, 'foo': i} for i in range(10)]),
            },
        ),
    ]

    formdef.category_id = cat.id
    formdef.workflow_id = workflow.id
    formdef.enable_tracking_codes = True
    formdef.tracking_code_verify_fields = ['0']
    formdef.store()

    # fails for unauthenticated users
    get_app(pub).get('/api/formdefs/test/schema', status=403)
    get_app(pub).get('/test/schema', status=403)

    # always ok for signed requests
    resp = get_app(pub).get(sign_url('/api/formdefs/test/schema?orig=coucou', '1234'))

    # ok for basic auth only if attached role is appropriate for management
    get_url = partial(_get_url, app=get_app(pub), access=access, auth='http-basic', user=None)
    get_url('/api/formdefs/test/schema', status=403)

    role = pub.role_class(name='Foo bar')
    role.store()
    access.roles = [role]
    access.store()
    get_url('/api/formdefs/test/schema', status=403)

    cat.management_roles = [role]
    cat.store()
    assert get_url('/api/formdefs/test/schema').json == resp.json

    # check schema
    assert set(resp.json.keys()) >= {
        'enable_tracking_codes',
        'tracking_code_verify_fields',
        'url_name',
        'description',
        'workflow',
        'expiration_date',
        'discussion',
        'has_captcha',
        'always_advertise',
        'name',
        'disabled',
        'only_allow_one',
        'fields',
        'keywords',
        'publication_date',
        'detailed_emails',
        'disabled_redirection',
    }
    assert resp.json['name'] == 'test'

    assert resp.json['enable_tracking_codes'] is True
    assert resp.json['tracking_code_verify_fields'] == ['0']

    # fields checks
    assert resp.json['fields'][0]['label'] == 'foobar'
    assert resp.json['fields'][0]['type'] == 'string'

    assert resp.json['fields'][1]['label'] == 'foobar1'
    assert resp.json['fields'][1]['type'] == 'item'

    # check no (structured/) items
    assert 'structured_items' not in resp.json['fields'][1]
    assert 'items' not in resp.json['fields'][1]

    assert resp.json['fields'][2]['label'] == 'foobar2'
    assert resp.json['fields'][2]['type'] == 'items'
    assert 'structured_items' not in resp.json['fields'][2]
    assert 'items' not in resp.json['fields'][2]

    # workflow checks
    assert len(resp.json['workflow']['statuses']) == 3
    assert resp.json['workflow']['statuses'][0]['id'] == 'st1'
    assert resp.json['workflow']['statuses'][0]['endpoint'] is False
    assert resp.json['workflow']['statuses'][0]['waitpoint'] is True
    assert resp.json['workflow']['statuses'][1]['id'] == 'st2'
    assert resp.json['workflow']['statuses'][1]['endpoint'] is False
    assert resp.json['workflow']['statuses'][1]['waitpoint'] is False
    assert resp.json['workflow']['statuses'][2]['id'] == 'st3'
    assert resp.json['workflow']['statuses'][2]['endpoint'] is True
    assert resp.json['workflow']['statuses'][2]['waitpoint'] is True
    assert len(resp.json['workflow']['fields']) == 1

    assert resp.json['workflow']['fields'][0]['label'] == '1st backoffice field'

    get_app(pub).get('/api/formdefs/xxx/schema', status=404)


def test_formdef_schema_block(pub, access):
    BlockDef.wipe()
    FormDef.wipe()

    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.StringField(id='0', label='Foo', varname='foo'),
    ]
    block.store()

    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [
        fields.BlockField(id='0', label='test', varname='blockdata', block_slug='foobar', max_items='3'),
    ]
    formdef.store()

    resp = get_app(pub).get(sign_url('/api/formdefs/test/schema?orig=coucou', '1234'))
    assert resp.json['fields'][0]['type'] == 'block'
    assert resp.json['fields'][0]['block_slug'] == 'foobar'
    assert resp.json['fields'][0]['schema']['fields'][0]['label'] == 'Foo'
    assert resp.json['fields'][0]['schema']['fields'][0]['varname'] == 'foo'
    assert 'id' not in resp.json['fields'][0]['schema']['fields'][0]

    resp = get_app(pub).get(sign_url('/api/formdefs/test/schema?orig=coucou&include-id=true', '1234'))
    assert resp.json['fields'][0]['schema']['fields'][0]['id'] == '0'


def test_post_invalid_json(pub, local_user):
    resp = get_app(pub).post(
        '/api/formdefs/test/submit', params='not a json payload', content_type='application/json', status=400
    )
    assert resp.json['err'] == 1
    assert resp.json['err_class'] == 'Invalid request'


@pytest.mark.parametrize('auth', ['signature', 'http-basic'])
def test_formdef_submit(pub, local_user, access, auth):
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()
    local_user.roles = [role.id]
    local_user.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [fields.StringField(id='0', label='foobar')]
    formdef.backoffice_submission_roles = [role.id]
    formdef.store()
    data_class = formdef.data_class()

    app = get_app(pub)
    post_url = partial(_post_url, app=app, auth=auth, access=access, user=local_user)
    post_json_url = partial(_post_json_url, app=app, auth=auth, access=access, user=local_user)

    if auth == 'http-basic':
        app.post_json('/api/formdefs/test/submit', params={'data': {}}, status=403)  # anonymous
        resp = post_json_url('/api/formdefs/test/submit', params={'data': {}}, status=403)
        assert resp.json['err_code'] == 'user-not-allowed-backoffice-submission'
        access.roles = [role]
        access.store()

    resp = post_json_url('/api/formdefs/test/submit', params={'data': {}})
    assert resp.json['err'] == 0
    assert resp.json['data']['url'] == ('http://example.net/test/%s/' % resp.json['data']['id'])
    assert resp.json['data']['backoffice_url'] == (
        'http://example.net/backoffice/management/test/%s/' % resp.json['data']['id']
    )
    assert resp.json['data']['api_url'] == ('http://example.net/api/forms/test/%s/' % resp.json['data']['id'])
    assert data_class.get(resp.json['data']['id']).status == 'wf-new'
    if auth == 'signature':
        assert data_class.get(resp.json['data']['id']).user_id == str(local_user.id)
    else:
        assert data_class.get(resp.json['data']['id']).user_id is None
    assert data_class.get(resp.json['data']['id']).tracking_code is None

    local_user2 = get_publisher().user_class()
    local_user2.name = 'Test'
    local_user2.email = 'foo@localhost'
    local_user2.store()
    resp = post_json_url(
        '/api/formdefs/test/submit', params={'data': {}, 'user': {'NameID': [], 'email': local_user2.email}}
    )
    assert data_class.get(resp.json['data']['id']).user.email == local_user2.email

    # bad user format
    resp = post_json_url('/api/formdefs/test/submit', params={'data': {}, 'user': ''}, status=400)
    assert resp.json['err_desc'] == 'Invalid user parameter.'

    resp = post_url(
        '/api/formdefs/test/submit', params=json.dumps({'data': {}}), status=400
    )  # missing Content-Type: application/json header
    assert resp.json['err_desc'] == 'Expected JSON but missing appropriate content-type.'

    # check qualified content type are recognized
    resp = post_url(
        '/api/formdefs/test/submit',
        params=json.dumps({'data': {}}),
        content_type='application/json; charset=utf-8',
    )
    assert resp.json['data']['url']

    formdef.disabled = True
    formdef.store()
    resp = post_json_url('/api/formdefs/test/submit', params={'data': {}}, status=403)
    assert resp.json['err'] == 1
    assert resp.json['err_desc'] == 'Disabled form.'

    formdef.disabled = False
    formdef.backoffice_submission_roles = []
    formdef.store()
    resp = post_json_url(
        '/api/formdefs/test/submit', params={'meta': {'backoffice-submission': True}, 'data': {}}, status=403
    )
    formdef.backoffice_submission_roles = ['xx']
    formdef.store()
    resp = post_json_url(
        '/api/formdefs/test/submit', params={'meta': {'backoffice-submission': True}, 'data': {}}, status=403
    )
    formdef.backoffice_submission_roles = [role.id]
    formdef.store()
    if auth == 'http-basic':
        access.roles = [role]
        access.store()
    resp = post_json_url(
        '/api/formdefs/test/submit', params={'meta': {'backoffice-submission': True}, 'data': {}}
    )
    assert data_class.get(resp.json['data']['id']).status == 'wf-new'
    assert data_class.get(resp.json['data']['id']).backoffice_submission is True
    assert data_class.get(resp.json['data']['id']).user_id is None
    if auth == 'signature':
        assert data_class.get(resp.json['data']['id']).submission_agent_id == str(local_user.id)
    else:
        assert data_class.get(resp.json['data']['id']).submission_agent_id is None

    formdef.enable_tracking_codes = True
    formdef.store()
    resp = post_json_url('/api/formdefs/test/submit', params={'data': {}})
    assert data_class.get(resp.json['data']['id']).tracking_code

    resp = post_json_url('/api/formdefs/test/submit', params={'meta': {'draft': True}, 'data': {}})
    assert data_class.get(resp.json['data']['id']).status == 'draft'

    resp = post_json_url(
        '/api/formdefs/test/submit',
        params={
            'meta': {'backoffice-submission': True},
            'data': {},
            'context': {'channel': 'mail', 'comments': 'blah'},
        },
    )
    assert data_class.get(resp.json['data']['id']).status == 'wf-new'
    assert data_class.get(resp.json['data']['id']).backoffice_submission is True
    assert data_class.get(resp.json['data']['id']).user_id is None
    assert data_class.get(resp.json['data']['id']).submission_context == {'comments': 'blah'}
    assert data_class.get(resp.json['data']['id']).submission_channel == 'mail'

    # check some invalid content
    resp = post_json_url('/api/formdefs/test/submit', params={'data': None}, status=400)
    resp = post_json_url('/api/formdefs/test/submit', params={'data': 'foobar'}, status=400)
    resp = post_json_url('/api/formdefs/test/submit', params={'data': []}, status=400)
    resp = post_json_url('/api/formdefs/test/submit', params='datastring', status=400)

    data_class.wipe()


def test_formdef_submit_only_one(pub, local_user):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.only_allow_one = True
    formdef.fields = [fields.StringField(id='0', label='foobar')]
    formdef.store()
    data_class = formdef.data_class()

    def url():
        signed_url = sign_url(
            'http://example.net/api/formdefs/test/submit'
            + '?format=json&orig=coucou&email=%s' % urllib.parse.quote(local_user.email),
            '1234',
        )
        return signed_url[len('http://example.net') :]

    resp = get_app(pub).post_json(url(), {'data': {}})
    assert data_class.get(resp.json['data']['id']).user_id == str(local_user.id)

    assert data_class.count() == 1

    resp = get_app(pub).post_json(url(), {'data': {}}, status=403)
    assert resp.json['err'] == 1
    assert resp.json['err_desc'] == 'Only one formdata by user is allowed.'

    formdata = data_class.select()[0]
    formdata.user_id = '1000'  # change owner
    formdata.store()

    resp = get_app(pub).post_json(url(), {'data': {}}, status=200)
    assert data_class.get(resp.json['data']['id']).user_id == str(local_user.id)
    assert data_class.count() == 2


def test_formdef_submit_with_varname(pub, local_user):
    NamedDataSource.wipe()
    data_source = NamedDataSource(name='foobar')
    source = [{'id': '1', 'text': 'foo', 'more': 'XXX'}, {'id': '2', 'text': 'bar', 'more': 'YYY'}]
    data_source.data_source = {'type': 'jsonvalue', 'value': json.dumps(source)}
    data_source.store()

    data_source = NamedDataSource(name='foobar_jsonp')
    data_source.data_source = {'type': 'formula', 'value': 'http://example.com/jsonp'}
    data_source.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [
        fields.StringField(id='0', label='foobar0', varname='foobar0'),
        fields.ItemField(id='1', label='foobar1', varname='foobar1', data_source={'type': 'foobar'}),
        fields.ItemField(id='2', label='foobar2', varname='foobar2', data_source={'type': 'foobar_jsonp'}),
        fields.DateField(id='3', label='foobar3', varname='date'),
        fields.FileField(id='4', label='foobar4', varname='file'),
        fields.MapField(id='5', label='foobar5', varname='map'),
        fields.StringField(id='6', label='foobar6', varname='foobar6'),
        fields.TableField(id='7', label='table', varname='table', rows=['Person1', 'Person2'], cols=['Name']),
        fields.ItemsField(id='8', label='items', varname='items', items=['value']),
        fields.BoolField(id='9', label='boolfalse', varname='boolfalse'),
        fields.BoolField(id='10', label='booltrue', varname='booltrue'),
    ]
    formdef.store()
    data_class = formdef.data_class()

    signed_url = sign_url(
        'http://example.net/api/formdefs/test/submit'
        + '?format=json&orig=coucou&email=%s' % urllib.parse.quote(local_user.email),
        '1234',
    )
    url = signed_url[len('http://example.net') :]
    payload = {
        'data': {
            'foobar0': 'xxx',
            'foobar1': '1',
            'foobar1_structured': {
                'id': '1',
                'text': 'foo',
                'more': 'XXX',
            },
            'foobar2': 'bar',
            'foobar2_raw': '10',
            'date': '1970-01-01',
            'file': {
                'filename': 'test.txt',
                'content': force_str(base64.b64encode(b'test')),
            },
            'map': {
                'lat': 1.5,
                'lon': 2.25,
            },
            'table': [['Name1'], ['Name2']],
            'items': '["a"]',
            'boolfalse': False,
            'booltrue': True,
            'blah': 'not a field',
        }
    }
    resp = get_app(pub).post_json(url, payload)
    assert resp.json['err'] == 0
    formdata = data_class.get(resp.json['data']['id'])
    assert formdata.status == 'wf-new'
    assert formdata.user_id == str(local_user.id)
    assert formdata.tracking_code is None
    assert formdata.data['0'] == 'xxx'
    assert formdata.data['1'] == '1'
    assert formdata.data['1_structured'] == source[0]
    assert formdata.data['2'] == '10'
    assert formdata.data['2_display'] == 'bar'
    assert formdata.data['3'] == time.struct_time((1970, 1, 1, 0, 0, 0, 3, 1, -1))
    assert formdata.data['4'].orig_filename == 'test.txt'
    assert formdata.data['4'].get_content() == b'test'
    assert formdata.data['5'] == {'lat': 1.5, 'lon': 2.25}
    assert formdata.data['8'] == []
    assert formdata.data['9'] is False
    assert formdata.data['10'] is True
    # check unknown fields are not stored in initial content snapshot
    assert 'blah' not in formdata.evolution[0].parts[0].new_data
    # test bijectivity
    assert formdef.fields[3].get_json_value(formdata.data['3']) == payload['data']['date']
    for k in payload['data']['file']:
        data = formdata.data['4']
        assert formdef.fields[4].get_json_value(data)[k] == payload['data']['file'][k]
    assert formdef.fields[5].get_json_value(formdata.data['5']) == payload['data']['map']

    data_class.wipe()


def test_formdef_submit_from_wscall(pub, local_user):
    test_formdef_submit_with_varname(pub, local_user)
    formdef = FormDef.select()[0]
    workflow = Workflow.get_default_workflow()
    workflow.id = '2'
    workflow.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(workflow)
    workflow.backoffice_fields_formdef.fields = [
        fields.StringField(id='bo1', label='1st backoffice field', varname='backoffice_blah'),
    ]
    workflow.store()
    formdef.workflow = workflow
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()

    upload = PicklableUpload('test.txt', 'text/plain', 'ascii')
    upload.receive([b'test'])

    formdata.data = {
        '0': 'xxx',
        '1': '1',
        '1_display': '1',
        '1_structured': {
            'id': '1',
            'text': 'foo',
            'more': 'XXX',
        },
        '2': '10',
        '2_display': 'bar',
        '3': time.strptime('1970-01-01', '%Y-%m-%d'),
        '4': upload,
        'bo1': 'backoffice field',
    }
    formdata.just_created()
    formdata.evolution[-1].status = 'wf-new'
    formdata.store()

    for map_value in ('1.5;2.25', {'lat': 1.5, 'lon': 2.25}):
        formdata.data['5'] = map_value
        payload = json.loads(json.dumps(formdata.get_json_export_dict(), cls=qommon.misc.JSONEncoder))
        signed_url = sign_url('http://example.net/api/formdefs/test/submit?orig=coucou', '1234')
        url = signed_url[len('http://example.net') :]

        resp = get_app(pub).post_json(url, payload)
        assert resp.json['err'] == 0
        new_formdata = formdef.data_class().get(resp.json['data']['id'])
        assert new_formdata.data['0'] == formdata.data['0']
        assert new_formdata.data['1'] == formdata.data['1']
        assert new_formdata.data['1_display'] == formdata.data['1_display']
        assert new_formdata.data['1_structured'] == formdata.data['1_structured']
        assert new_formdata.data['2'] == formdata.data['2']
        assert new_formdata.data['2_display'] == formdata.data['2_display']
        assert new_formdata.data['3'] == formdata.data['3']
        assert new_formdata.data['4'].get_content() == formdata.data['4'].get_content()
        assert new_formdata.data['5'] == {'lat': 1.5, 'lon': 2.25}
        assert new_formdata.data['bo1'] == formdata.data['bo1']
        assert not new_formdata.data.get('6')
        assert new_formdata.user_id is None

    # add an extra attribute
    payload['extra'] = {'foobar6': 'YYY'}
    signed_url = sign_url('http://example.net/api/formdefs/test/submit?orig=coucou', '1234')
    url = signed_url[len('http://example.net') :]
    resp = get_app(pub).post_json(url, payload)
    assert resp.json['err'] == 0
    new_formdata = formdef.data_class().get(resp.json['data']['id'])
    assert new_formdata.data['0'] == formdata.data['0']
    assert new_formdata.data['6'] == 'YYY'

    # add user
    formdata.user_id = local_user.id
    formdata.store()

    payload = json.loads(json.dumps(formdata.get_json_export_dict(), cls=qommon.misc.JSONEncoder))
    signed_url = sign_url('http://example.net/api/formdefs/test/submit?orig=coucou', '1234')
    url = signed_url[len('http://example.net') :]

    resp = get_app(pub).post_json(url, payload)
    assert resp.json['err'] == 0
    new_formdata = formdef.data_class().get(resp.json['data']['id'])
    assert str(new_formdata.user_id) == str(local_user.id)

    # test missing map data
    del formdata.data['5']

    payload = json.loads(json.dumps(formdata.get_json_export_dict(), cls=qommon.misc.JSONEncoder))
    signed_url = sign_url('http://example.net/api/formdefs/test/submit?orig=coucou', '1234')
    url = signed_url[len('http://example.net') :]

    resp = get_app(pub).post_json(url, payload)
    assert resp.json['err'] == 0
    new_formdata = formdef.data_class().get(resp.json['data']['id'])
    assert new_formdata.data.get('5') is None


def test_formdef_submit_structured(pub, local_user):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [
        fields.ItemField(
            id='0',
            label='foobar',
            varname='foobar',
            data_source={
                'type': 'json',
                'value': 'http://datasource.com',
            },
        ),
        fields.ItemField(
            id='1',
            label='foobar1',
            varname='foobar1',
            data_source={
                'type': 'jsonvalue',
                'value': json.dumps([{'id': i, 'text': 'label %s' % i, 'foo': i} for i in range(10)]),
            },
        ),
        fields.ItemField(
            id='2',
            label='foobar2',
            varname='foobar2',
            data_source={
                'type': 'json',
                'value': 'http://datasource.com/{{form_var_foobar_foo}}',
            },
        ),
    ]
    formdef.store()
    data_class = formdef.data_class()

    def url():
        signed_url = sign_url(
            'http://example.net/api/formdefs/test/submit'
            '?format=json&orig=coucou&email=%s' % urllib.parse.quote(local_user.email),
            '1234',
        )
        return signed_url[len('http://example.net') :]

    with responses.RequestsMock() as rsps:
        json_data = {
            'data': [
                {'id': 0, 'text': 'zéro', 'foo': 'bar'},
                {'id': 1, 'text': 'uné', 'foo': 'bar1'},
                {'id': 2, 'text': 'deux', 'foo': 'bar2'},
            ]
        }
        rsps.get('http://datasource.com', json=json_data)
        rsps.get('http://datasource.com/bar', json=json_data)
        resp = get_app(pub).post_json(
            url(),
            {
                'data': {
                    '0': '0',
                    '1': '3',
                    '2': '2',
                }
            },
        )
        assert len(rsps.calls) == 2
        assert rsps.calls[0].request.url == 'http://datasource.com/'
        assert rsps.calls[1].request.url == 'http://datasource.com/bar'

    formdata = data_class.get(resp.json['data']['id'])
    assert formdata.status == 'wf-new'
    assert formdata.data['0'] == '0'
    assert formdata.data['0_display'] == 'zéro'
    assert formdata.data['0_structured'] == {
        'id': 0,
        'text': 'zéro',
        'foo': 'bar',
    }
    assert formdata.data['1'] == '3'
    assert formdata.data['1_display'] == 'label 3'
    assert formdata.data['1_structured'] == {
        'id': 3,
        'text': 'label 3',
        'foo': 3,
    }
    assert formdata.data['2'] == '2'
    assert formdata.data['2_display'] == 'deux'
    assert formdata.data['2_structured'] == {
        'id': 2,
        'text': 'deux',
        'foo': 'bar2',
    }

    data_class.wipe()


def test_formdef_submit_structured_with_block_field(pub, local_user):
    BlockDef.wipe()
    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.ItemField(
            id='0',
            label='foobar',
            varname='foobar',
            data_source={
                'type': 'json',
                'value': 'http://datasource.com',
            },
        ),
        fields.ItemField(
            id='1',
            label='foobar2',
            varname='foobar2',
            data_source={
                'type': 'json',
                'value': 'http://datasource.com/{{form_var_foobar_foo}}',
            },
        ),
    ]
    block.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [
        fields.BlockField(id='0', label='test', varname='blockdata', block_slug='foobar', max_items='3'),
    ]
    formdef.store()
    data_class = formdef.data_class()
    data_class.wipe()

    def url():
        signed_url = sign_url(
            'http://example.net/api/formdefs/test/submit'
            '?format=json&orig=coucou&email=%s' % urllib.parse.quote(local_user.email),
            '1234',
        )
        return signed_url[len('http://example.net') :]

    for field_key in ('0', 'blockdata'):
        with responses.RequestsMock() as rsps:
            json_data = {
                'data': [
                    {'id': 0, 'text': 'zéro', 'foo': 'bar'},
                    {'id': 2, 'text': 'deux', 'foo': 'bar2'},
                ]
            }
            rsps.get('http://datasource.com', json=json_data)
            rsps.get('http://datasource.com/bar', json=json_data)
            resp = get_app(pub).post_json(
                url(),
                {'data': {field_key: [{'foobar': '0', 'foobar2': '2'}]}},
            )
            assert len(rsps.calls) == 2
            assert rsps.calls[0].request.url == 'http://datasource.com/'
            assert rsps.calls[1].request.url == 'http://datasource.com/bar'

        formdata = data_class.get(resp.json['data']['id'])
        assert formdata.status == 'wf-new'
        blockdata = formdata.data['0']['data'][0]
        assert blockdata['0'] == '0'
        assert blockdata['0_display'] == 'zéro'
        assert blockdata['0_structured'] == {
            'id': 0,
            'text': 'zéro',
            'foo': 'bar',
        }
        assert blockdata['1'] == '2'
        assert blockdata['1_display'] == 'deux'
        assert blockdata['1_structured'] == {
            'id': 2,
            'text': 'deux',
            'foo': 'bar2',
        }

    # wrong data for block field
    resp = get_app(pub).post_json(
        url(),
        {'data': {'0': True}},
    )
    formdata = data_class.get(resp.json['data']['id'])
    assert formdata.status == 'wf-new'
    assert formdata.data['0']['data'] == []  # ignored


def test_formdef_submit_tracking_code(pub, local_user):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [
        fields.StringField(id='0', label='foobar'),
    ]

    formdef.enable_tracking_codes = True
    formdef.store()
    data_class = formdef.data_class()

    signed_url = sign_url('http://example.net/api/formdefs/test/submit?orig=coucou', '1234')
    url = signed_url[len('http://example.net') :]
    payload = {'data': {'foobar': 'xxx'}}
    resp = get_app(pub).post_json(url, payload)
    assert resp.json['err'] == 0
    formdata = data_class.get(resp.json['data']['id'])
    assert formdata.tracking_code == resp.json['tracking_code']


def test_formdef_import_export_block(pub, admin_user):
    BlockDef.wipe()
    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.StringField(id='0', label='Foo', varname='foo'),
        fields.ItemField(id='1', label='Test', data_source={'type': 'foobar'}, varname='bar'),
        fields.StringField(id='2', label='Unnamed', required='optional'),
        fields.DateField(id='3', label='Date'),
    ]
    block.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [
        fields.BlockField(id='0', label='test', varname='blockdata', block_slug='foobar', max_items='3'),
    ]
    formdef.store()

    formdef.data_class().wipe()
    formdata = formdef.data_class()()
    formdata.data = {
        '0': {
            'data': [
                {
                    '0': 'plop',
                    '1': '1',
                    '1_display': 'foo',
                    '1_structured': 'XXX',
                    '2': 'yop',
                    '3': time.strptime('2020-04-24', '%Y-%m-%d'),
                },
                {
                    '0': 'hop',
                    '1': '2',
                    '1_display': 'bar',
                    '1_structured': 'YYY',
                    '2': None,
                    '3': time.strptime('2020-04-24', '%Y-%m-%d'),
                },
            ],
            'schema': {'0': 'string', '1': 'item', '2': 'string', '3': 'date'},
        },
        '0_display': 'test',
    }
    formdata.just_created()
    formdata.store()

    app = login(get_app(pub))
    resp = app.get('/api/forms/test/%s/' % formdata.id, status=200)

    formdata_export = resp.json
    formdef.data_class().wipe()

    resp = app.post_json(sign_url('/api/formdefs/test/submit?orig=coucou', '1234'), formdata_export)

    new_formdata = formdef.data_class().select()[0]
    assert new_formdata.data == formdata.data


def test_formdef_import_export_unnamed_block(pub, admin_user):
    BlockDef.wipe()
    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.StringField(id='1', label='Foo', varname='foo'),
    ]
    block.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [
        fields.BlockField(id='0', label='test', block_slug='foobar', max_items='3'),
    ]
    formdef.store()

    formdef.data_class().wipe()
    formdata = formdef.data_class()()
    formdata.data = {
        '0': {
            'data': [
                {
                    '1': 'plop',
                },
                {
                    '1': 'hop',
                },
            ],
            'schema': {'1': 'string'},
        },
        '0_display': 'foobar, foobar',
    }
    formdata.just_created()
    formdata.store()

    formdata_export = formdata.get_json_export_dict(include_unnamed_fields=True, include_evolution=False)
    del formdata_export['receipt_time']
    del formdata_export['last_update_time']
    del formdata_export['workflow']['real_status']['first_arrival_datetime']
    del formdata_export['workflow']['real_status']['latest_arrival_datetime']
    formdef.data_class().wipe()

    app = login(get_app(pub))
    app.post_json(sign_url('/api/formdefs/test/submit?orig=coucou', '1234'), formdata_export)
    new_formdata = formdef.data_class().select()[0]
    assert new_formdata.data == formdata.data


def test_formdef_submit_numeric_field(pub, local_user):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [
        fields.NumericField(id='11', label='numeric', varname='numeric'),
    ]
    formdef.store()
    data_class = formdef.data_class()

    def post(payload):
        signed_url = sign_url(
            'http://example.net/api/formdefs/test/submit'
            + '?format=json&orig=coucou&email=%s' % urllib.parse.quote(local_user.email),
            '1234',
        )
        url = signed_url[len('http://example.net') :]
        return get_app(pub).post_json(url, payload)

    # valid value as float
    payload = {
        'data': {
            'numeric': 10.5,
        }
    }
    resp = post(payload)
    assert resp.json['err'] == 0
    formdata = data_class.get(resp.json['data']['id'])
    assert formdata.data['11'] == decimal.Decimal('10.5')

    # valid value as string
    payload = {
        'data': {
            'numeric': '10.5',
        }
    }
    resp = post(payload)
    assert resp.json['err'] == 0
    formdata = data_class.get(resp.json['data']['id'])
    assert formdata.data['11'] == decimal.Decimal('10.5')

    # null value
    payload = {
        'data': {
            'numeric': None,
        }
    }
    resp = post(payload)
    assert resp.json['err'] == 0
    formdata = data_class.get(resp.json['data']['id'])
    assert formdata.data['11'] is None

    # invalid value
    payload = {
        'data': {
            'numeric': 'xxx',
        }
    }
    resp = post(payload)
    assert resp.json['err'] == 0
    formdata = data_class.get(resp.json['data']['id'])
    assert formdata.data['11'] is None


def test_formdef_web_view_denied_api_access(pub, access):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.store()

    app = get_app(pub)
    app.set_authorization(('Basic', ('test', '12345')))
    resp = app.get(formdef.get_url(), status=403)
    assert 'Not an API view.' in resp.text
