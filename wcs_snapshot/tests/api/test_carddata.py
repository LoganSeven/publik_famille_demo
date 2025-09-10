import datetime
import hashlib
import os

import pytest
from django.utils.encoding import force_bytes

from wcs import fields
from wcs.admin.settings import UserFieldsFormDef
from wcs.carddef import CardDef
from wcs.qommon.http_request import HTTPRequest
from wcs.qommon.upload_storage import PicklableUpload
from wcs.sql import ApiAccess
from wcs.workflows import Workflow

from ..utilities import clean_temporary_pub, create_temporary_pub, get_app
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

    pub.user_class.wipe()

    return pub


def teardown_module(module):
    clean_temporary_pub()


def test_carddata_api_access(pub, local_user):
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()
    local_user.roles = [role.id]
    local_user.store()

    ApiAccess.wipe()
    access = ApiAccess()
    access.name = 'test'
    access.access_identifier = 'test'
    access.access_key = '12345'
    access.store()

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'test'
    carddef.workflow_roles = {'_viewer': role.id}
    carddef.store()

    carddef.data_class().wipe()
    carddata = carddef.data_class()()
    carddata.just_created()
    carddata.store()

    pub.custom_view_class.wipe()
    custom_view = pub.custom_view_class()
    custom_view.title = 'shared carddef custom view'
    custom_view.formdef = carddef
    custom_view.columns = {'list': [{'id': '0'}]}
    custom_view.filters = {}
    custom_view.visibility = 'any'
    custom_view.store()

    custom_view = pub.custom_view_class()
    custom_view.title = 'private carddef custom view'
    custom_view.formdef = carddef
    custom_view.columns = {'list': [{'id': '0'}]}
    custom_view.filters = {}
    custom_view.visibility = 'owner'
    custom_view.user = local_user
    custom_view.is_default = True  # check that set_default_view method is not failing with this value
    custom_view.store()

    app = get_app(pub)
    app.get(
        sign_uri(
            '/api/cards/test/shared-carddef-custom-view/%s/' % carddata.id,
            orig=access.access_identifier,
            key=access.access_key,
        ),
        status=200,
    )


@pytest.mark.parametrize('auth', ['signature', 'http-basic', 'api-admin-user'])
def test_carddata_include_params(pub, local_user, auth):
    # signature: SqlUser
    # http-basic: RestrictedApiUser
    # api-admin-user: ApiAdminUser
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.id = '123'
    role.store()
    local_user.roles = [role.id]
    local_user.store()

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'test'
    carddef.workflow_roles = {'_viewer': role.id}
    carddef.fields = [
        fields.StringField(id='1', label='foobar', varname='foobar'),
    ]
    carddef.store()

    carddef.data_class().wipe()
    carddata = carddef.data_class()()
    carddata.data = {'1': 'FOO BAR'}
    carddata.user_id = local_user.id
    carddata.just_created()
    upload = PicklableUpload('test.txt', 'text/plain', 'ascii')
    upload.receive([b'test'])
    upload2 = PicklableUpload('test.txt', 'text/plain', 'ascii')
    upload2.receive([b'test'])
    carddata.workflow_data = {'blah': upload, 'blah2': upload2, 'xxx': 23}
    carddata.store()

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
                sign_uri(
                    url,
                    user=local_user if auth == 'signature' else None,
                    orig=access.access_identifier,
                    key=access.access_key,
                ),
                **kwargs,
            )

    # no specific include, check uuid is there
    resp = get_url('/api/cards/test/list')
    assert 'uuid' in resp.json['data'][0]
    # check include-*
    resp = get_url('/api/cards/test/list?include-fields=on')
    assert 'fields' in resp.json['data'][0]
    resp = get_url('/api/cards/test/list?include-evolution=on')
    assert 'evolution' in resp.json['data'][0]
    resp = get_url('/api/cards/test/list?include-roles=on')
    assert 'roles' in resp.json['data'][0]
    resp = get_url('/api/cards/test/list?include-submission=on')
    assert 'submission' in resp.json['data'][0]
    resp = get_url('/api/cards/test/list?include-workflow=on')
    assert 'workflow' in resp.json['data'][0]
    assert 'data' not in resp.json['data'][0]['workflow']
    resp = get_url('/api/cards/test/list?include-workflow-data=on')
    assert 'workflow' in resp.json['data'][0]
    assert 'data' in resp.json['data'][0]['workflow']
    resp = get_url('/api/cards/test/list?include-actions=on')
    assert 'actions' in resp.json['data'][0]

    resp = get_url('/api/cards/test/%s/?include-fields=off' % carddata.id)
    assert 'fields' not in resp.json
    resp = get_url('/api/cards/test/%s/?include-evolution=off' % carddata.id)
    assert 'evolution' not in resp.json
    resp = get_url('/api/cards/test/%s/?include-roles=off' % carddata.id)
    assert 'roles' not in resp.json
    resp = get_url('/api/cards/test/%s/?include-submission=off' % carddata.id)
    assert 'submission' not in resp.json
    resp = get_url('/api/cards/test/%s/?include-workflow=off' % carddata.id)
    assert 'workflow' in resp.json
    assert 'data' in resp.json['workflow']
    resp = get_url('/api/cards/test/%s/?include-workflow-data=off' % carddata.id)
    assert 'workflow' in resp.json
    assert 'data' not in resp.json['workflow']
    resp = get_url('/api/cards/test/%s/?include-workflow=off&include-workflow-data=off' % carddata.id)
    assert 'workflow' not in resp.json
    resp = get_url('/api/cards/test/%s/?include-actions=off' % carddata.id)
    assert 'actions' not in resp.json

    resp = get_url('/api/cards/test/list')
    assert len(resp.json['data']) == 1

    carddata.anonymise()
    resp = get_url('/api/cards/test/list')
    assert len(resp.json['data']) == 0

    carddata.anonymise()
    resp = get_url('/api/cards/test/list?include-anonymised=on')
    assert len(resp.json['data']) == 1


def test_carddata_user_fields(pub, local_user):
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    local_user.roles = [role.id]
    local_user.store()

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'test'
    carddef.workflow_roles = {'_viewer': role.id}
    carddef.store()

    carddata = carddef.data_class()()
    carddata.just_created()
    carddata.store()

    resp = get_app(pub).get(sign_uri('/api/cards/test/%s/' % carddata.id, user=local_user), status=200)
    assert 'user' not in resp.json
    resp = get_app(pub).get(sign_uri('/api/cards/test/list', user=local_user))
    assert 'user' not in resp.json['data'][0]
    resp = get_app(pub).get(sign_uri('/api/cards/test/list?full=on', user=local_user))
    assert 'user' not in resp.json['data'][0]

    carddata.user_id = local_user.id
    carddata.store()
    resp = get_app(pub).get(sign_uri('/api/cards/test/%s/' % carddata.id, user=local_user), status=200)
    assert resp.json['user'] == {
        'id': local_user.id,
        'NameID': ['0123456789'],
        'name': 'Jean Darmette',
        'email': 'jean.darmette@triffouilis.fr',
    }
    resp = get_app(pub).get(sign_uri('/api/cards/test/list', user=local_user))
    assert 'user' not in resp.json['data'][0]
    resp = get_app(pub).get(sign_uri('/api/cards/test/list?full=on', user=local_user))
    assert resp.json['data'][0]['user'] == {
        'id': local_user.id,
        'NameID': ['0123456789'],
        'name': 'Jean Darmette',
        'email': 'jean.darmette@triffouilis.fr',
    }

    formdef = UserFieldsFormDef(pub)
    formdef.fields = [
        fields.StringField(id='3', label='test', varname='var1'),
        fields.StringField(id='9', label='noop', varname='var2'),
        fields.DateField(id='10', label='birthdate', varname='birthdate'),
        fields.StringField(id='42', label='no varname'),
    ]
    formdef.store()

    resp = get_app(pub).get(sign_uri('/api/cards/test/%s/' % carddata.id, user=local_user), status=200)
    assert resp.json['user'] == {
        'id': local_user.id,
        'NameID': ['0123456789'],
        'name': 'Jean Darmette',
        'email': 'jean.darmette@triffouilis.fr',
        'var1': None,
        'var2': None,
        'birthdate': None,
    }
    resp = get_app(pub).get(sign_uri('/api/cards/test/list', user=local_user))
    assert 'user' not in resp.json['data'][0]
    resp = get_app(pub).get(sign_uri('/api/cards/test/list?full=on', user=local_user))
    assert resp.json['data'][0]['user'] == {
        'id': local_user.id,
        'NameID': ['0123456789'],
        'name': 'Jean Darmette',
        'email': 'jean.darmette@triffouilis.fr',
        'var1': None,
        'var2': None,
        'birthdate': None,
    }

    local_user.form_data = {'3': 'toto', '9': 'nono', '10': datetime.date(2020, 1, 15).timetuple()}
    local_user.store()
    resp = get_app(pub).get(sign_uri('/api/cards/test/%s/' % carddata.id, user=local_user), status=200)
    assert resp.json['user'] == {
        'id': local_user.id,
        'NameID': ['0123456789'],
        'name': 'Jean Darmette',
        'email': 'jean.darmette@triffouilis.fr',
        'var1': 'toto',
        'var2': 'nono',
        'birthdate': '2020-01-15',
    }
    resp = get_app(pub).get(sign_uri('/api/cards/test/list', user=local_user))
    assert 'user' not in resp.json['data'][0]
    resp = get_app(pub).get(sign_uri('/api/cards/test/list?full=on', user=local_user))
    assert resp.json['data'][0]['user'] == {
        'id': local_user.id,
        'NameID': ['0123456789'],
        'name': 'Jean Darmette',
        'email': 'jean.darmette@triffouilis.fr',
        'var1': 'toto',
        'var2': 'nono',
        'birthdate': '2020-01-15',
    }


def test_cards_list_pagination(pub):
    pub.role_class.wipe()

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'test'
    carddef.fields = [
        fields.StringField(id='0', label='foobar', varname='foobar'),
    ]
    carddef.store()

    carddef.data_class().wipe()

    for i in range(30):
        formdata = carddef.data_class()()
        formdata.data = {'0': f'{"aï" * i}'}
        formdata.just_created()
        formdata.store()

    resp = get_app(pub).get(sign_uri('/api/cards/test/list/'))
    assert resp.json['count'] == 30
    assert resp.json['data'][0]['id'] == '30'
    assert resp.json['data'][-1]['id'] == '1'

    resp = get_app(pub).get(sign_uri('/api/cards/test/list/?limit=2'))
    assert resp.json['count'] == 30
    assert len(resp.json['data']) == 2
    assert resp.json['data'][0]['id'] == '30'
    assert resp.json['data'][-1]['id'] == '29'
    assert 'limit=2' in resp.json['next']
    assert 'offset=2' in resp.json['next']
    assert 'previous' not in resp.json

    resp = get_app(pub).get(sign_uri('/api/cards/test/list/?limit=2&offset=0'))
    assert resp.json['count'] == 30
    assert 'limit=2' in resp.json['next']
    assert 'offset=2' in resp.json['next']
    assert 'previous' not in resp.json

    resp = get_app(pub).get(sign_uri('/api/cards/test/list/?limit=2&offset=2'))
    assert resp.json['count'] == 30
    assert len(resp.json['data']) == 2
    assert resp.json['data'][0]['id'] == '28'
    assert resp.json['data'][-1]['id'] == '27'
    assert 'limit=2' in resp.json['next']
    assert 'offset=4' in resp.json['next']
    assert 'limit=2' in resp.json['previous']
    assert 'offset=0' in resp.json['previous']

    # we cant have a negative offset
    resp = get_app(pub).get(sign_uri('/api/cards/test/list/?limit=4&offset=2'))
    assert resp.json['count'] == 30
    assert 'limit=4' in resp.json['next']
    assert 'offset=6' in resp.json['next']
    assert 'limit=4' in resp.json['previous']
    assert 'offset=0' in resp.json['previous']

    # we cant have an offset > count
    resp = get_app(pub).get(sign_uri('/api/cards/test/list/?limit=10&offset=21'))
    assert resp.json['count'] == 30
    assert len(resp.json['data']) == 9
    assert resp.json['data'][0]['id'] == '9'
    assert resp.json['data'][-1]['id'] == '1'
    assert 'next' not in resp.json
    assert 'limit=10' in resp.json['previous']
    assert 'offset=11' in resp.json['previous']


def test_card_get_file(pub):
    pub.role_class.wipe()

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'test'
    carddef.fields = [
        fields.StringField(id='0', label='foobar', varname='foobar'),
        fields.FileField(id='3', label='foobar4', varname='file'),
    ]
    carddef.store()

    upload = PicklableUpload('test.txt', 'text/plain', 'ascii')
    upload.receive([b'file content'])

    carddef.data_class().wipe()
    formdata = carddef.data_class()()
    formdata.data = {'0': 'blah', '3': upload}
    formdata.just_created()
    formdata.store()

    resp = get_app(pub).get(sign_uri('/api/cards/test/%s/' % formdata.id))
    file_url = resp.json['fields']['file']['url']
    assert get_app(pub).get(file_url, status=403)

    resp = get_app(pub).get(sign_uri(file_url), status=200)
    assert resp.text == 'file content'

    upload = PicklableUpload('test.jpeg', 'image/jpeg')
    with open(os.path.join(os.path.dirname(__file__), '../image-with-gps-data.jpeg'), 'rb') as fd:
        upload.receive([fd.read()])
    formdata.data = {'0': 'blah', '3': upload}
    formdata.store()

    resp = get_app(pub).get(sign_uri('/api/cards/test/%s/' % formdata.id))
    file_url = resp.json['fields']['file']['url']
    assert upload.can_thumbnail() is True
    thumbs_dir = os.path.join(pub.app_dir, 'thumbs')
    thumb_filepath = os.path.join(
        thumbs_dir, hashlib.sha256(force_bytes(upload.get_fs_filename())).hexdigest()
    )
    assert os.path.exists(thumbs_dir) is False
    assert os.path.exists(thumb_filepath) is False
    get_app(pub).get(sign_uri(file_url + '&thumbnail=1'), status=200)
    assert os.path.exists(thumbs_dir) is True
    assert os.path.exists(thumb_filepath) is True
    # again, thumbs_dir already exists
    get_app(pub).get(sign_uri(file_url + '&thumbnail=1'), status=200)


def test_api_list_formdata_phone_order_by_rank(pub):
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    ApiAccess.wipe()
    access = ApiAccess()
    access.name = 'test'
    access.access_identifier = 'test'
    access.access_key = '12345'
    access.store()

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'test'
    carddef.workflow_roles = {'_viewer': role.id}
    carddef.fields = [
        fields.StringField(id='0', label='a', display_locations=['listings']),
        fields.StringField(id='1', label='b'),
    ]
    carddef.store()

    data_class = carddef.data_class()
    data_class.wipe()

    # 1st carddata, with phone number
    carddata1 = data_class()
    carddata1.data = {'1': '+33623456789'}
    carddata1.just_created()
    carddata1.jump_status('new')
    carddata1.store()

    # 2nd carddata, with no value
    carddata2 = data_class()
    carddata2.data = {}
    carddata2.just_created()
    carddata2.jump_status('new')
    carddata2.store()

    # check fts
    resp = get_app(pub).get(
        sign_uri(
            '/api/cards/test/list?full=on&q=0623456789', orig=access.access_identifier, key=access.access_key
        )
    )
    assert len(resp.json['data']) == 1
    assert [int(x['id']) for x in resp.json['data']] == [carddata1.id]


def test_carddata_list_skip_evolutions(pub, sql_queries):
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.id = '123'
    role.store()

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'test'
    carddef.workflow_roles = {'_viewer': role.id}
    carddef.fields = [
        fields.StringField(id='1', label='foobar', varname='foobar'),
    ]
    carddef.store()

    carddef.data_class().wipe()

    for i in range(10):
        carddata = carddef.data_class()()
        carddata.data = {'1': 'FOO BAR %s' % i}
        carddata.just_created()
        carddata.store()

    ApiAccess.wipe()
    access = ApiAccess()
    access.name = 'test'
    access.access_identifier = 'test'
    access.access_key = '12345'
    access.roles = [role]
    access.store()

    app = get_app(pub)

    def get_url(url, **kwargs):
        app.set_authorization(('Basic', ('test', '12345')))
        return app.get(url, **kwargs)

    sql_queries.clear()
    get_url('/api/cards/test/list')
    assert not [x for x in sql_queries if '%s_evolution' % carddef.table_name in x]
    sql_queries.clear()
    get_url('/api/cards/test/list?include-workflow=on')
    assert [x for x in sql_queries if '%s_evolution' % carddef.table_name in x]
    sql_queries.clear()
    get_url('/api/cards/test/list?include-evolution=on')
    assert [x for x in sql_queries if '%s_evolution' % carddef.table_name in x]
    sql_queries.clear()


def test_api_card_list_custom_id_filter_identifier(pub):
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    ApiAccess.wipe()
    access = ApiAccess()
    access.name = 'test'
    access.access_identifier = 'test'
    access.access_key = '12345'
    access.roles = [role]
    access.store()

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'foo'
    carddef.workflow_roles = {'_viewer': role.id}
    carddef.fields = [
        fields.StringField(id='1', label='Test', varname='foo'),
    ]
    carddef.digest_templates = {'default': 'card {{ form_var_foo }}'}
    carddef.id_template = '{{ form_var_foo }}'
    carddef.store()

    card = carddef.data_class()()
    card.data = {'1': 'bar'}
    card.just_created()
    card.store()

    card2 = carddef.data_class()()
    card2.data = {'1': 'foo'}
    card2.just_created()
    card2.store()

    card3 = carddef.data_class()()
    card3.data = {'1': 'baz'}
    card3.just_created()
    card3.store()

    resp = get_app(pub).get(
        sign_uri(
            '/api/cards/foo/list?filter-identifier=bar', orig=access.access_identifier, key=access.access_key
        )
    )
    assert len(resp.json['data']) == 1
    assert resp.json['data'][0]['id'] == 'bar'
    assert resp.json['data'][0]['internal_id'] == str(card.id)

    app = get_app(pub)
    app.set_authorization(('Basic', ('test', '12345')))
    resp = app.get('/api/cards/foo/list?filter-identifier=bar')
    assert len(resp.json['data']) == 1
    assert resp.json['data'][0]['id'] == 'bar'
    assert resp.json['data'][0]['internal_id'] == str(card.id)

    resp = app.get('/api/cards/foo/list?filter-identifier=bar&full=on')
    assert len(resp.json['data']) == 1
    assert resp.json['data'][0]['id'] == 'bar'
    assert resp.json['data'][0]['internal_id'] == str(card.id)

    resp = app.get('/api/cards/foo/list?filter-identifier=bar,foo')
    assert len(resp.json['data']) == 2
    assert {x['id'] for x in resp.json['data']} == {'bar', 'foo'}

    resp = app.get('/api/cards/foo/list?filter-identifier=bar,foo&filter-identifier-operator=ne')
    assert len(resp.json['data']) == 1
    assert {x['id'] for x in resp.json['data']} == {'baz'}

    # check with no custom id
    carddef.id_template = None
    carddef.store()
    resp = app.get(f'/api/cards/foo/list?filter-identifier={card.id}')
    assert len(resp.json['data']) == 1
    resp = app.get(f'/api/cards/foo/list?filter-identifier={card.id},{card2.id}')
    assert len(resp.json['data']) == 2
    resp = app.get(f'/api/cards/foo/list?filter-identifier={2**32}')  # too high for postgresql
    assert len(resp.json['data']) == 0
    resp = app.get(
        f'/api/cards/foo/list?filter-identifier={card.id},{card2.id}&filter-identifier-operator=ne'
    )
    assert len(resp.json['data']) == 1


@pytest.mark.parametrize('auth', ['signature', 'http-basic'])
def test_carddata_global_actions(auth, pub, local_user):
    CardDef.wipe()
    Workflow.wipe()
    ApiAccess.wipe()

    pub.role_class.wipe()
    role = pub.role_class(name='allowed-action-role')
    role.store()

    local_user.roles = [role.id]
    local_user.store()

    access = ApiAccess()
    access.name = 'test'
    access.access_identifier = 'test'
    access.access_key = '12345'
    access.store()

    app = get_app(pub)

    if auth == 'http-basic':
        access.roles = [role]
        access.store()

        def get_url(url, **kwargs):
            app.set_authorization(('Basic', ('test', '12345')))
            return app.get(url, **kwargs)

    else:

        def get_url(url, **kwargs):
            return app.get(
                sign_uri(
                    url,
                    user=local_user,
                    orig=access.access_identifier,
                    key=access.access_key,
                ),
                **kwargs,
            )

    workflow = Workflow(name='test-workflow')
    workflow.add_status('test-status')

    action = workflow.add_global_action('Global Action')
    trigger = action.append_trigger('webservice')
    trigger.identifier = 'test-trigger'
    trigger.roles = [role.id]
    workflow.store()

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'test-carddef'
    carddef.workflow_id = workflow.id
    carddef.workflow_roles = {'_receiver': role.id}
    carddef.store()

    carddata = carddef.data_class()()
    carddata.just_created()
    carddata.jump_status('workflow-status')
    carddata.store()

    resp = get_url('/api/cards/test-carddef/%s/?include-actions=on' % carddata.id)
    assert resp.json['actions'] == {
        'global-action:test-trigger': f'{carddata.get_api_url()}hooks/test-trigger/'
    }

    trigger.identifier = None
    workflow.store()

    resp = get_url('/api/cards/test-carddef/%s/?include-actions=on' % carddata.id)
    assert resp.json['actions'] == {}

    trigger.identifier = 'test-trigger'
    trigger.roles = ['_unhautorized']
    workflow.store()

    resp = get_url('/api/cards/test-carddef/%s/?include-actions=on' % carddata.id)
    assert resp.json['actions'] == {}


@pytest.mark.parametrize('auth', ['signature', 'http-basic'])
def test_carddata_global_actions_submitter(auth, pub, local_user):
    CardDef.wipe()
    Workflow.wipe()
    ApiAccess.wipe()

    pub.role_class.wipe()
    role = pub.role_class(name='allowed-action-role')
    role.store()

    local_user.roles = [role.id]
    local_user.store()

    access = ApiAccess()
    access.name = 'test'
    access.access_identifier = 'test'
    access.access_key = '12345'
    access.store()

    app = get_app(pub)

    if auth == 'http-basic':
        access.roles = [role]
        access.store()

        def get_url(url, **kwargs):
            app.set_authorization(('Basic', ('test', '12345')))
            return app.get(url, **kwargs)

    else:

        def get_url(url, **kwargs):
            return app.get(
                sign_uri(
                    url,
                    user=local_user,
                    orig=access.access_identifier,
                    key=access.access_key,
                ),
                **kwargs,
            )

    workflow = Workflow(name='test-workflow')
    workflow.add_status('test-status')

    action = workflow.add_global_action('Global Action')
    trigger = action.append_trigger('webservice')
    trigger.identifier = 'test-trigger'
    trigger.roles = ['_submitter']
    workflow.store()

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'test-carddef'
    carddef.workflow_id = workflow.id
    carddef.workflow_roles = {'_receiver': role.id}
    carddef.store()

    carddata = carddef.data_class()()
    carddata.just_created()
    carddata.jump_status('workflow-status')
    carddata.store()

    resp = get_url('/api/cards/test-carddef/%s/?include-actions=on' % carddata.id)
    assert resp.json['actions'] == {}

    resp = get_url('/api/cards/test-carddef/list?include-actions=on')
    assert resp.json['data'][0]['actions'] == {}

    carddata.user_id = local_user.id
    carddata.store()

    if auth == 'signature':
        # check _submitter function is ok for signed calls
        # (http auth has no user associated)
        resp = get_url('/api/cards/test-carddef/%s/?include-actions=on' % carddata.id)
        assert resp.json['actions'] == {
            'global-action:test-trigger': f'{carddata.get_api_url()}hooks/test-trigger/'
        }

        resp = get_url('/api/cards/test-carddef/list?include-actions=on')
        assert resp.json['data'][0]['actions'] == {
            'global-action:test-trigger': f'{carddata.get_api_url()}hooks/test-trigger/'
        }


@pytest.mark.parametrize('auth', ['signature', 'http-basic'])
def test_carddata_jump_trigger_action(auth, pub, local_user):
    CardDef.wipe()
    Workflow.wipe()
    ApiAccess.wipe()

    pub.role_class.wipe()
    role = pub.role_class(name='allowed-role')
    role.store()

    local_user.roles = [role.id]
    local_user.store()

    access = ApiAccess()
    access.name = 'test'
    access.access_identifier = 'test'
    access.access_key = '12345'
    access.store()

    app = get_app(pub)

    if auth == 'http-basic':
        access.roles = [role]
        access.store()

        def get_url(url, **kwargs):
            app.set_authorization(('Basic', ('test', '12345')))
            return app.get(url, **kwargs)

    else:

        def get_url(url, **kwargs):
            return app.get(
                sign_uri(
                    url,
                    user=local_user,
                    orig=access.access_identifier,
                    key=access.access_key,
                ),
                **kwargs,
            )

    workflow = Workflow(name='test-workflow')
    source_status = workflow.add_status('source-status')
    target_status = workflow.add_status('target-status')

    jump = source_status.add_action('jump')
    jump.status = target_status.id
    jump.trigger = 'test-trigger'
    jump.by = [role.id]

    workflow.store()

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'test-carddef'
    carddef.workflow_id = workflow.id
    carddef.workflow_roles = {'_receiver': role.id}
    carddef.store()

    carddata = carddef.data_class()()
    carddata.just_created()
    carddata.jump_status('source-status')
    carddata.store()

    resp = get_url('/api/cards/test-carddef/%s/?include-actions=on' % carddata.id)
    assert resp.json['actions'] == {
        'jump:test-trigger': f'{carddata.get_api_url()}jump/trigger/test-trigger/'
    }

    jump.trigger = None
    workflow.store()

    resp = get_url('/api/cards/test-carddef/%s/?include-actions=on' % carddata.id)
    assert resp.json['actions'] == {}

    jump.trigger = 'test-trigger'
    jump.condition = {'type': 'django', 'value': 'false'}
    workflow.store()

    resp = get_url('/api/cards/test-carddef/%s/?include-actions=on' % carddata.id)
    assert resp.json['actions'] == {}

    jump.condition = None
    jump.by = ['_submitter']
    workflow.store()

    resp = get_url('/api/cards/test-carddef/%s/?include-actions=on' % carddata.id)
    assert resp.json['actions'] == {}

    jump.by = [role.id]
    workflow.store()
    carddata.jump_status(target_status.id)
    carddata.store()

    resp = get_url('/api/cards/test-carddef/%s/?include-actions=on' % carddata.id)
    assert resp.json['actions'] == {}


@pytest.mark.parametrize('flag', [True, False])
@pytest.mark.parametrize('auth', ['signature', 'http-basic'])
def test_carddata_editable_action(auth, pub, local_user, flag):
    pub.load_site_options()
    pub.site_options.add_section('options')
    pub.site_options.set('options', 'api-include-editable-action', 'true' if flag else 'false')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)

    CardDef.wipe()
    Workflow.wipe()
    ApiAccess.wipe()

    pub.role_class.wipe()
    role = pub.role_class(name='allowed-role')
    role.store()

    local_user.roles = [role.id]
    local_user.store()

    access = ApiAccess()
    access.name = 'test'
    access.access_identifier = 'test'
    access.access_key = '12345'
    access.store()

    app = get_app(pub)

    if auth == 'http-basic':
        access.roles = [role]
        access.store()

        def get_url(url, **kwargs):
            app.set_authorization(('Basic', ('test', '12345')))
            return app.get(url, **kwargs)

    else:

        def get_url(url, **kwargs):
            return app.get(
                sign_uri(
                    url,
                    user=local_user,
                    orig=access.access_identifier,
                    key=access.access_key,
                ),
                **kwargs,
            )

    workflow = Workflow(name='test-workflow')
    source_status = workflow.add_status('source-status')

    edit = source_status.add_action('editable')
    edit.by = [role.id]

    workflow.store()

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'test-carddef'
    carddef.workflow_id = workflow.id
    carddef.workflow_roles = {'_receiver': role.id}
    carddef.store()

    carddata = carddef.data_class()()
    carddata.just_created()
    carddata.jump_status('source-status')
    carddata.store()

    resp = get_url('/api/cards/test-carddef/%s/?include-actions=on' % carddata.id)
    if flag:
        assert resp.json['actions'] == {
            'link:edit:1-1': 'http://example.net/backoffice/data/test-carddef/1/wfedit-1'
        }
    else:
        assert resp.json['actions'] == {}

    workflow.store()

    edit.condition = {'type': 'django', 'value': 'false'}
    workflow.store()

    resp = get_url('/api/cards/test-carddef/%s/?include-actions=on' % carddata.id)
    assert resp.json['actions'] == {}

    edit.condition = None
    edit.by = ['_submitter']
    workflow.store()

    resp = get_url('/api/cards/test-carddef/%s/?include-actions=on' % carddata.id)
    assert resp.json['actions'] == {}


def test_api_geojson_carddata(pub, local_user):
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'test'
    carddef.workflow_roles = {'_viewer': role.id}
    carddef.fields = [
        fields.StringField(id='0', label='foobar', varname='foo'),
    ]
    carddef.store()

    data_class = carddef.data_class()
    data_class.wipe()

    carddef.geolocations = {'base': 'Location'}
    carddef.store()

    # check access is denied if the user has not the appropriate role
    resp = get_app(pub).get(sign_uri('/api/cards/test/geojson', user=local_user), status=403)
    # even if there's an anonymse parameter
    resp = get_app(pub).get(sign_uri('/api/cards/test/geojson?anonymise', user=local_user), status=403)

    for i in range(10):
        carddata = data_class()
        carddata.geolocations = {'base': {'lat': 48, 'lon': 2}}
        carddata.data = {'0': f'test{i}'}
        carddata.just_created()
        carddata.store()

    # add proper role to user
    local_user.roles = [role.id]
    local_user.store()

    # check it gets the data
    resp = get_app(pub).get(sign_uri('/api/cards/test/geojson', user=local_user))
    assert len(resp.json['features']) == 10
    assert resp.json['features'][0]['properties']['id'] == '1-10'
    assert resp.json['features'][0]['properties']['text'] == 'test #1-10'

    # check with a digest
    carddef.digest_templates = {'default': 'card {{ form_var_foo }}'}
    carddef.store()
    for carddata in data_class.select():
        carddata.store()
    resp = get_app(pub).get(sign_uri('/api/cards/test/geojson', user=local_user))
    assert resp.json['features'][0]['properties']['text'] == 'card test9'


def test_api_customview_related_field(pub, local_user):
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    local_user.roles = [role.id]
    local_user.store()

    CardDef.wipe()
    carddef_ds = CardDef()
    carddef_ds.name = 'foo'
    carddef_ds.fields = [
        fields.StringField(id='1', label='Test', varname='foo'),
    ]
    carddef_ds.digest_templates = {'default': '{{ form_var_foo }}'}
    carddef_ds.store()
    carddef_ds.data_class().wipe()

    card_ids = {}
    for label in ('foo', 'bar', 'baz'):
        card = carddef_ds.data_class()()
        card.data = {'1': label}
        card.just_created()
        card.store()
        card_ids[label] = str(card.id)

    carddef = CardDef()
    carddef.name = 'test'
    carddef.workflow_roles = {'_viewer': role.id}
    carddef.fields = [
        fields.ItemField(id='0', label='Item', data_source={'type': 'carddef:foo'}, varname='item'),
    ]
    carddef.store()
    pub.custom_view_class.wipe()
    custom_view = pub.custom_view_class()
    custom_view.title = 'custom view'
    custom_view.formdef = carddef
    custom_view.filters = {}
    custom_view.columns = {'list': [{'id': '0$1'}]}
    custom_view.visibility = 'datasource'
    custom_view.store()

    data_class = carddef.data_class()
    data_class.wipe()

    for i in range(5):
        carddata = data_class()
        if i < 3:
            carddata.data = {
                '0': str(i + 1),
                '0_display': 'card %s' % ['foo', 'bar', 'baz'][i],
            }
        if i == 3:
            # Empty values
            carddata.data = {
                '0': '',
            }
        if i == 4:
            # None values
            carddata.data = {}
        carddata.just_created()
        carddata.jump_status('recorded')
        carddata.store()

    resp = get_app(pub).get(
        sign_uri('/api/cards/test/custom-view/list?include-fields=on&order_by=id', user=local_user)
    )
    assert resp.json['data'][0]['related_fields'] == {'item': {'foo': 'foo'}}
    assert resp.json['data'][1]['related_fields'] == {'item': {'foo': 'bar'}}
    assert resp.json['data'][2]['related_fields'] == {'item': {'foo': 'baz'}}
    assert resp.json['data'][3]['related_fields'] == {'item': {'foo': None}}
