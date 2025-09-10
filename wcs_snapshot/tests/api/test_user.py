import datetime
import os
from functools import partial

import pytest
from django.utils.timezone import make_aware
from quixote import get_publisher

from wcs import fields
from wcs.admin.settings import UserFieldsFormDef
from wcs.carddef import CardDef
from wcs.categories import Category
from wcs.formdef import FormDef
from wcs.qommon.http_request import HTTPRequest
from wcs.qommon.upload_storage import PicklableUpload
from wcs.sql import ApiAccess
from wcs.workflows import Workflow, WorkflowVariablesFieldsFormDef

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


@pytest.fixture
def access(pub):
    ApiAccess.wipe()
    access = ApiAccess()
    access.name = 'test'
    access.access_identifier = 'test'
    access.access_key = '12345'
    access.store()
    return access


def test_roles(pub, local_user):
    pub.role_class.wipe()
    role = pub.role_class(name='Hello World')
    role.emails = ['toto@example.com', 'zozo@example.com']
    role.details = 'kouign amann'
    role.store()

    resp = get_app(pub).get('/api/roles', status=403)

    resp = get_app(pub).get(sign_uri('/api/roles'))
    assert resp.json['data'][0]['text'] == 'Hello World'
    assert resp.json['data'][0]['slug'] == 'hello-world'
    assert resp.json['data'][0]['emails'] == ['toto@example.com', 'zozo@example.com']
    assert resp.json['data'][0]['emails_to_members'] is False
    assert resp.json['data'][0]['details'] == 'kouign amann'

    # also check old endpoint, for compatibility
    resp = get_app(pub).get(sign_uri('/roles'), headers={'Accept': 'application/json'})
    assert resp.json['data'][0]['text'] == 'Hello World'
    assert resp.json['data'][0]['slug'] == 'hello-world'
    assert resp.json['data'][0]['emails'] == ['toto@example.com', 'zozo@example.com']
    assert resp.json['data'][0]['emails_to_members'] is False
    assert resp.json['data'][0]['details'] == 'kouign amann'


def test_user_api_with_restricted_access(pub, access):
    role = pub.role_class(name='test')
    role.store()

    access.roles = [role]
    access.store()

    resp = get_app(pub).get(sign_uri('/api/user/', orig='test', key='12345'), status=403)
    assert resp.json['err'] == 1
    assert resp.json['err_desc'] == 'Restricted API access.'


def test_users_api_with_restricted_access(pub, local_user, access):
    role = pub.role_class(name='test')
    role.store()

    access.roles = [role]
    access.store()

    resp = get_app(pub).get(sign_uri('/api/users/', orig='test', key='12345'), status=403)
    assert resp.json['err'] == 1
    assert resp.json['err_desc'] == 'Unauthenticated/unsigned request or no access to users.'


def test_users(pub, local_user):
    resp = get_app(pub).get('/api/users/', status=403)

    resp = get_app(pub).get(sign_uri('/api/users/'))
    assert resp.json['data'][0]['user_display_name'] == local_user.name
    assert resp.json['data'][0]['user_email'] == local_user.email
    assert resp.json['data'][0]['user_id'] == local_user.id

    role = pub.role_class(name='Foo bar')
    role.store()
    local_user.roles = [role.id]
    local_user.store()

    resp = get_app(pub).get(sign_uri('/api/users/?q=jean'))
    assert resp.json['data'][0]['user_email'] == local_user.email
    assert len(resp.json['data'][0]['user_roles']) == 1
    assert resp.json['data'][0]['user_roles'][0]['name'] == 'Foo bar'

    resp = get_app(pub).get(sign_uri('/api/users/?q=foobar'))
    assert len(resp.json['data']) == 0

    formdef = UserFieldsFormDef(pub)
    formdef.fields.append(fields.StringField(id='3', label='test'))
    formdef.store()

    local_user.form_data = {'3': 'HELLO'}
    local_user.set_attributes_from_formdata(local_user.form_data)
    local_user.store()

    resp = get_app(pub).get(sign_uri('/api/users/?q=HELLO'))
    assert len(resp.json['data']) == 1
    resp = get_app(pub).get(sign_uri('/api/users/?q=foobar'))
    assert len(resp.json['data']) == 0

    local_user.name_identifiers = ['xyz']
    local_user.store()
    resp = get_app(pub).get(sign_uri('/api/users/?q=xyz'))
    assert len(resp.json['data']) == 1

    local_user.set_deleted()
    resp = get_app(pub).get(sign_uri('/api/users/?q=HELLO'))
    assert len(resp.json['data']) == 0

    resp = get_app(pub).get(sign_uri('/api/users/?NameID=xyz'), status=403)  # unknown NameID

    local_user.name_identifiers = ['xyz']
    local_user.deleted_timestamp = None
    local_user.store()
    resp = get_app(pub).get(sign_uri('/api/users/?NameID=xyz'))
    assert len(resp.json['data']) == 1


def test_users_basic_auth(pub, local_user, access):
    app = get_app(pub)
    app.set_authorization(('Basic', ('test', '12345')))
    resp = app.get('/api/users/', status=403)

    role = pub.role_class(name='Foo bar')
    role.allows_backoffice_access = True
    role.store()
    access.roles = [role]
    access.store()

    resp = app.get('/api/users/', status=403)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.backoffice_submission_roles = [role.id]
    formdef.store()

    resp = app.get('/api/users/', status=200)
    assert resp.json['data'][0]['user_display_name'] == local_user.name
    assert resp.json['data'][0]['user_email'] == local_user.email
    assert resp.json['data'][0]['user_id'] == local_user.id


def test_users_unaccent(pub, local_user):
    local_user.name = 'Jean Sénisme'
    local_user.store()
    resp = get_app(pub).get(sign_uri('/api/users/?q=jean'))
    assert resp.json['data'][0]['user_email'] == local_user.email

    resp = get_app(pub).get(sign_uri('/api/users/?q=senisme'))
    assert resp.json['data'][0]['user_email'] == local_user.email

    resp = get_app(pub).get(sign_uri('/api/users/?q=sénisme'))
    assert resp.json['data'][0]['user_email'] == local_user.email

    resp = get_app(pub).get(sign_uri('/api/users/?q=blah'))
    assert len(resp.json['data']) == 0


def test_users_description(pub, local_user):
    assert 'users' not in pub.cfg

    formdef = UserFieldsFormDef(pub)
    formdef.fields = [
        fields.StringField(id='1', label='phone', varname='phone'),
        fields.StringField(id='2', label='mobile', varname='mobile'),
        fields.StringField(id='3', label='address', varname='address'),
        fields.StringField(id='4', label='zipcode', varname='zipcode'),
        fields.StringField(id='5', label='city', varname='city'),
    ]
    formdef.store()

    local_user.form_data = {
        '1': '0505050505',
        '2': '0606060606',
        '3': 'rue du Chateau',
        '4': '75014',
        '5': 'PARIS',
    }
    local_user.set_attributes_from_formdata(local_user.form_data)
    local_user.store()

    resp = get_app(pub).get(sign_uri('/api/users/'))
    assert resp.json['data'][0]['user_id'] == local_user.id
    assert (
        resp.json['data'][0]['description'].replace('\n', '')
        == 'jean.darmette@triffouilis.fr 📞 0505050505 📱 0606060606 📨 rue du Chateau 75014 PARIS'
    )

    pub.cfg['users'][
        'search_result_template'
    ] = """{{ user_email|default:"" }}{% if user_var_phone %} 📞 {{ user_var_phone }}{% endif %} foo bar"""
    pub.write_cfg()
    resp = get_app(pub).get(sign_uri('/api/users/'))
    assert resp.json['data'][0]['user_id'] == local_user.id
    assert (
        resp.json['data'][0]['description'].replace('\n', '')
        == 'jean.darmette@triffouilis.fr 📞 0505050505 foo bar'
    )


@pytest.mark.parametrize('auth', ['signature', 'http-basic'])
def test_user_by_nameid(pub, local_user, access, auth):
    app = get_app(pub)
    get_url = partial(_get_url, app=app, auth=auth, access=access, user=local_user)

    resp = get_url('/api/users/xyz/', status=404)
    local_user.name_identifiers = ['xyz']
    local_user.store()
    resp = get_url('/api/users/xyz/')
    assert str(resp.json['id']) == str(local_user.id)


def test_user_by_nameid_api_access_restrict_to_anonymised_data(pub, local_user, access):
    app = get_app(pub)
    get_url = partial(_get_url, app=app, auth='http-basic', access=access, user=local_user)

    get_url('/api/users/%s/' % local_user.id)

    access.restrict_to_anonymised_data = True
    access.store()
    resp = get_url('/api/users/%s/' % local_user.id, status=403)
    assert resp.json['err'] == 1
    assert resp.json['err_desc'] == 'Restricted API access.'


@pytest.mark.parametrize('auth', ['signature', 'http-basic'])
def test_user_roles(pub, local_user, access, auth):
    role = pub.role_class(name='Foo bar')
    role.store()
    local_user.name_identifiers = ['xyz']
    local_user.roles = [role.id]
    local_user.store()

    app = get_app(pub)
    get_url = partial(_get_url, app=app, auth=auth, access=access, user=local_user)

    resp = get_url('/api/users/xyz/')
    assert len(resp.json['user_roles']) == 1
    assert resp.json['user_roles'][0]['name'] == 'Foo bar'


def test_user_forms(pub, local_user, access):
    Workflow.wipe()
    workflow = Workflow.get_default_workflow()
    workflow.id = '2'
    workflow.variables_formdef = WorkflowVariablesFieldsFormDef(workflow=workflow)
    workflow.variables_formdef.fields.append(fields.DateField(label='Test', varname='option_date'))
    workflow.store()

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'test'
    carddef.store()
    carddef.data_class().wipe()

    carddata1 = carddef.data_class()()
    carddata1.just_created()
    carddata1.store()
    carddata2 = carddef.data_class()()
    carddata2.just_created()
    carddata2.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [
        fields.StringField(id='0', label='foobar', varname='foobar'),
        fields.StringField(id='1', label='foobar2'),
        fields.DateField(id='2', label='date', varname='date'),
        fields.ItemField(id='3', label='Item', data_source={'type': 'carddef:test'}, varname='item'),
    ]
    formdef.keywords = 'hello, world'
    formdef.disabled = False
    formdef.enable_tracking_codes = True
    formdef.workflow = workflow
    formdef.workflow_options = {'option_date': datetime.date(2020, 1, 15).timetuple()}
    formdef.store()
    formdef.data_class().wipe()

    resp = get_app(pub).get(sign_uri('/api/user/forms', user=local_user))
    assert resp.json['err'] == 0
    assert len(resp.json['data']) == 0

    formdatas = []
    for carddata in [carddata1, carddata2, carddata1]:
        formdata = formdef.data_class()()
        formdata.data = {
            '0': 'foo@localhost',
            '1': 'xxx',
            '2': datetime.date(2020, 1, 15).timetuple(),
            '3': str(carddata.id),
            '3_display': 'foo %s' % carddata.id,
        }
        formdata.user_id = local_user.id
        formdata.just_created()
        formdata.jump_status('new')
        formdata.store()
        formdatas.append(formdata)

    resp = get_app(pub).get(sign_uri('/api/user/forms', user=local_user))
    resp2 = get_app(pub).get(sign_uri('/api/users/%s/forms' % local_user.id))
    assert resp.json['err'] == 0
    assert len(resp.json['data']) == 3
    assert resp.json['data'][0]['form_name'] == 'test'
    assert resp.json['data'][0]['form_slug'] == 'test'
    assert resp.json['data'][0]['form_status'] == 'New'
    assert datetime.datetime.strptime(resp.json['data'][0]['form_receipt_datetime'], '%Y-%m-%dT%H:%M:%S')
    assert resp.json['data'][0]['keywords'] == ['hello', 'world']
    assert resp.json == resp2.json

    for bad_value in ['foo', 'foo:foo']:
        resp = get_app(pub).get(sign_uri('/api/user/forms?related=%s' % bad_value, user=local_user))
        resp2 = get_app(pub).get(sign_uri('/api/users/%s/forms?related=%s' % (local_user.id, bad_value)))
        assert resp.json['err'] == 0
        assert len(resp.json['data']) == 0
        assert resp.json == resp2.json

    for unknown in ['carddef:test:42', 'formdef:test:1', 'carddef:unknown:1', 'foo:foo:foo']:
        resp = get_app(pub).get(sign_uri('/api/user/forms?related=%s' % unknown, user=local_user))
        resp2 = get_app(pub).get(sign_uri('/api/users/%s/forms?related=%s' % (local_user.id, unknown)))
        assert resp.json['err'] == 0
        assert len(resp.json['data']) == 0
        assert resp.json == resp2.json

    resp = get_app(pub).get(sign_uri('/api/user/forms?related=carddef:test:1', user=local_user))
    resp2 = get_app(pub).get(sign_uri('/api/users/%s/forms?related=carddef:test:1' % local_user.id))
    assert resp.json['err'] == 0
    assert len(resp.json['data']) == 2
    assert resp.json == resp2.json

    resp = get_app(pub).get(sign_uri('/api/user/forms?related=carddef:test:2', user=local_user))
    resp2 = get_app(pub).get(sign_uri('/api/users/%s/forms?related=carddef:test:2' % local_user.id))
    assert resp.json['err'] == 0
    assert len(resp.json['data']) == 1
    assert resp.json == resp2.json

    resp = get_app(pub).get(sign_uri('/api/user/forms?full=on', user=local_user))
    assert resp.json['err'] == 0
    assert resp.json['data'][0]['fields']['foobar'] == 'foo@localhost'
    assert resp.json['data'][0]['fields']['date'] == '2020-01-15'
    assert resp.json['data'][0]['keywords'] == ['hello', 'world']
    assert resp.json['data'][0]['form_option_option_date'] == '2020-01-15'
    resp2 = get_app(pub).get(sign_uri('/api/user/forms?&full=on', user=local_user))
    assert resp.json == resp2.json

    formdef.disabled = True
    formdef.store()
    resp = get_app(pub).get(sign_uri('/api/user/forms', user=local_user))
    assert resp.json['err'] == 0
    assert len(resp.json['data']) == 3

    # check digest is part of contents
    formdef.digest_templates = {'default': 'XYZ'}
    formdef.data_class().get(formdatas[0].id).store()
    assert formdef.data_class().get(formdatas[0].id).digests['default'] == 'XYZ'
    resp = get_app(pub).get(sign_uri('/api/user/forms', user=local_user))
    assert resp.json['data'][0]['form_digest'] == 'XYZ'

    resp = get_app(pub).get(sign_uri('/api/user/forms?NameID=xxx'))
    assert resp.json == {
        'err': 1,
        'err_class': 'Access denied',
        'err_code': 'unknown-name-id',
        'err_desc': 'Unknown NameID.',
    }
    resp2 = get_app(pub).get(sign_uri('/api/user/forms?&NameID=xxx'))
    assert resp.json == resp2.json

    formdata = formdef.data_class()()
    formdata.user_id = local_user.id
    formdata.status = 'draft'
    formdata.receipt_time = make_aware(datetime.datetime(2015, 1, 1))
    formdata.store()

    resp = get_app(pub).get(sign_uri('/api/user/forms', user=local_user))
    assert resp.json['err'] == 0
    assert len(resp.json['data']) == 3

    resp = get_app(pub).get(sign_uri('/api/user/forms?include-drafts=true', user=local_user))
    assert resp.json['err'] == 0
    assert len(resp.json['data']) == 3

    formdef.disabled = False
    formdef.store()

    resp = get_app(pub).get(sign_uri('/api/user/forms?include-drafts=true', user=local_user))
    assert resp.json['err'] == 0
    assert len(resp.json['data']) == 4

    formdata = formdef.data_class()()
    formdata.data = {'0': 'foo@localhost', '1': 'xyy'}
    formdata.user_id = local_user.id
    formdata.just_created()
    formdata.receipt_time = make_aware(datetime.datetime.now() + datetime.timedelta(days=1))
    formdata.jump_status('new')
    formdata.store()

    resp = get_app(pub).get(sign_uri('/api/user/forms', user=local_user))
    assert len(resp.json['data']) == 4
    resp2 = get_app(pub).get(sign_uri('/api/user/forms?sort=desc', user=local_user))
    assert len(resp2.json['data']) == 4
    assert resp2.json['data'][0] == resp.json['data'][3]
    assert resp2.json['data'][1] == resp.json['data'][2]
    assert resp2.json['data'][2] == resp.json['data'][1]
    assert resp2.json['data'][3] == resp.json['data'][0]

    # check there is no access with roles-limited API users
    role = pub.role_class(name='test')
    role.store()

    access.roles = [role]
    access.store()

    resp = get_app(pub).get(sign_uri('/api/user/forms', orig='test', key='12345'), status=403)
    assert resp.json['err'] == 1
    assert resp.json['err_desc'] == 'Restricted API access.'


def test_user_forms_limit_offset(pub, local_user):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test limit offset'
    formdef.fields = [
        fields.StringField(id='0', label='foobar', varname='foobar'),
        fields.StringField(id='1', label='foobar2'),
    ]
    formdef.keywords = 'hello, world'
    formdef.disabled = False
    formdef.enable_tracking_codes = False
    formdef.store()
    formdef.data_class().wipe()

    for i in range(50):
        formdata = formdef.data_class()()
        formdata.data = {'0': 'foo@localhost', '1': str(i)}
        formdata.user_id = local_user.id
        formdata.just_created()
        formdata.receipt_time = make_aware(datetime.datetime.now() + datetime.timedelta(days=i))
        formdata.jump_status('new')
        formdata.store()

    for i in range(50):
        formdata = formdef.data_class()()
        formdata.data = {'0': 'foo@localhost', '1': str(i)}
        formdata.user_id = local_user.id
        formdata.status = 'draft'
        formdata.receipt_time = make_aware(datetime.datetime.now() - datetime.timedelta(days=i))
        formdata.store()

    resp = get_app(pub).get(sign_uri('/api/users/%s/forms' % local_user.id))
    assert resp.json['err'] == 0
    assert len(resp.json['data']) == 50

    resp = get_app(pub).get(sign_uri('/api/users/%s/forms?limit=10' % local_user.id))
    assert resp.json['err'] == 0
    assert len(resp.json['data']) == 10
    assert [x['form_number_raw'] for x in resp.json['data']] == [str(x) for x in range(1, 11)]

    resp = get_app(pub).get(sign_uri('/api/users/%s/forms?limit=10&offset=45' % local_user.id))
    assert resp.json['err'] == 0
    assert len(resp.json['data']) == 5
    assert [x['form_number_raw'] for x in resp.json['data']] == [str(x) for x in range(46, 51)]

    resp = get_app(pub).get(sign_uri('/api/users/%s/forms?limit=10&sort=desc' % local_user.id))
    assert resp.json['err'] == 0
    assert len(resp.json['data']) == 10
    assert [x['form_number_raw'] for x in resp.json['data']] == [str(x) for x in range(50, 40, -1)]


def test_user_forms_no_visible_status(pub, local_user):
    Workflow.wipe()
    workflow = Workflow(name='foo')
    st1 = workflow.add_status('st1')
    st1.set_visibility_mode('restricted')
    workflow.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test no visible status'
    formdef.fields = [
        fields.StringField(id='0', label='foobar', varname='foobar'),
    ]
    formdef.disabled = False
    formdef.workflow = workflow
    formdef.store()
    formdef.data_class().wipe()

    formdata = formdef.data_class()()
    formdata.data = {'0': 'foo'}
    formdata.user_id = local_user.id
    formdata.just_created()
    formdata.store()

    resp = get_app(pub).get(sign_uri('/api/users/%s/forms' % local_user.id))
    assert resp.json['err'] == 0
    assert len(resp.json['data']) == 1
    assert resp.json['data'][0]['status'] is None
    assert resp.json['data'][0]['title'] == f'test no visible status #{formdef.id}-{formdata.id} (unknown)'


def test_user_forms_categories_filter(pub, local_user):
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
    formdef1.fields = [
        fields.StringField(id='0', label='foobar', varname='foobar'),
    ]
    formdef1.category = category1
    formdef1.store()
    formdef2 = FormDef()
    formdef2.name = 'test 2'
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

    resp = get_app(pub).get(sign_uri('/api/users/%s/forms' % local_user.id))
    assert len(resp.json['data']) == 5
    resp = get_app(pub).get(sign_uri('/api/users/%s/forms?category_slugs=category-1' % local_user.id))
    assert len(resp.json['data']) == 2
    resp = get_app(pub).get(sign_uri('/api/users/%s/forms?category_slugs=category-2' % local_user.id))
    assert len(resp.json['data']) == 3
    resp = get_app(pub).get(sign_uri('/api/users/%s/forms?category_slugs=unknown' % local_user.id))
    assert len(resp.json['data']) == 0
    resp = get_app(pub).get(sign_uri('/api/users/%s/forms?category_slugs=category-1,unknown' % local_user.id))
    assert len(resp.json['data']) == 2
    resp = get_app(pub).get(
        sign_uri('/api/users/%s/forms?category_slugs=category-1,category-2' % local_user.id)
    )
    assert len(resp.json['data']) == 5


@pytest.mark.parametrize('auth', ['signature', 'http-basic'])
def test_user_forms_from_agent(pub, local_user, access, auth):
    pub.role_class.wipe()
    role = pub.role_class(name='Foo bar')
    role.allows_backoffice_access = True
    role.store()

    agent_user = get_publisher().user_class()
    agent_user.name = 'Agent'
    agent_user.email = 'agent@example.com'
    agent_user.name_identifiers = ['ABCDE']
    agent_user.roles = [role.id]
    agent_user.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [
        fields.StringField(id='0', label='foobar', varname='foobar'),
        fields.StringField(id='1', label='foobar2'),
    ]
    formdef.store()
    formdef.data_class().wipe()

    formdata = formdef.data_class()()
    formdata.data = {'0': 'foo@localhost', '1': 'xxx'}
    formdata.user_id = local_user.id
    formdata.just_created()
    formdata.jump_status('new')
    formdata.store()

    if auth == 'http-basic':
        access.roles = [role]
        access.store()

    app = get_app(pub)
    get_url = partial(_get_url, app=app, auth=auth, access=access, user=agent_user)

    resp = get_url('/api/users/%s/forms' % local_user.id)
    assert resp.json['err'] == 0
    assert len(resp.json['data']) == 1
    assert resp.json['data'][0]['form_name'] == 'test'
    assert resp.json['data'][0]['form_slug'] == 'test'
    assert resp.json['data'][0]['form_status'] == 'New'
    assert resp.json['data'][0]['readable'] is False

    formdef.skip_from_360_view = True
    formdef.store()

    resp = get_url('/api/users/%s/forms' % local_user.id)
    assert len(resp.json['data']) == 0

    formdef.workflow_roles = {'_receiver': str(role.id)}
    formdef.store()
    formdef.data_class().rebuild_security()
    resp = get_url('/api/users/%s/forms' % local_user.id)
    assert len(resp.json['data']) == 1

    agent_user.roles = []
    agent_user.store()
    if auth == 'http-basic':
        access.roles = []
        access.store()
        resp = get_url('/api/users/%s/forms' % local_user.id)
        assert len(resp.json['data']) == 0
    else:
        get_url('/api/users/%s/forms' % local_user.id, status=403)


def test_user_forms_api_access_restrict_to_anonymised_data(pub, local_user, access):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = []
    formdef.store()

    app = get_app(pub)
    get_url = partial(_get_url, app=app, auth='http-basic', access=access, user=local_user)

    get_url('/api/users/%s/forms' % local_user.id)

    access.restrict_to_anonymised_data = True
    access.store()
    resp = get_url('/api/users/%s/forms' % local_user.id, status=403)
    assert resp.json['err'] == 1
    assert resp.json['err_desc'] == 'Restricted API access.'


def test_user_forms_include_accessible(pub, local_user, access):
    pub.role_class.wipe()
    role = pub.role_class(name='Foo bar')
    role.allows_backoffice_access = True
    role.store()

    another_user = get_publisher().user_class()
    another_user.name = 'Another user'
    another_user.email = 'another@example.com'
    another_user.name_identifiers = ['AZERTY']
    another_user.store()

    agent_user = get_publisher().user_class()
    agent_user.name = 'Agent'
    agent_user.email = 'agent@example.com'
    agent_user.name_identifiers = ['ABCDE']
    agent_user.roles = [role.id]
    agent_user.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [
        fields.StringField(id='0', label='foobar', varname='foobar'),
        fields.StringField(id='1', label='foobar2'),
    ]
    formdef.store()
    formdef.data_class().wipe()

    formdata1 = formdef.data_class()()
    formdata1.data = {'0': 'foo@localhost', '1': 'xxx'}
    formdata1.user_id = local_user.id
    formdata1.just_created()
    formdata1.jump_status('new')
    formdata1.store()

    formdata2 = formdef.data_class()()
    formdata2.data = {'0': 'foo@localhost', '1': 'xxx'}
    formdata2.user_id = another_user.id
    formdata2.just_created()
    formdata2.jump_status('new')
    formdata2.store()

    formdata3 = formdef.data_class()()
    formdata3.data = {'0': 'foo@localhost', '1': 'xxx'}
    formdata3.user_id = another_user.id
    formdata3.just_created()
    formdata3.jump_status('new')
    formdata3.workflow_roles = {'_receiver': ['_user:%s' % local_user.id]}
    formdata3.store()

    formdata4 = formdef.data_class()()
    formdata4.data = {'0': 'foo@localhost', '1': 'xxx'}
    formdata4.user_id = agent_user.id
    formdata4.just_created()
    formdata4.jump_status('new')
    formdata4.store()

    app = get_app(pub)

    def get_ids(url):
        resp = app.get(url)
        return {int(x['form_number_raw']) for x in resp.json['data']}

    resp = get_ids(sign_uri('/api/user/forms', user=local_user))
    assert resp == {formdata1.id}

    resp = get_ids(sign_uri('/api/user/forms?include-accessible=on', user=local_user))
    assert resp == {formdata1.id, formdata3.id}

    # an agent gets the same results
    resp = get_ids(sign_uri('/api/users/%s/forms' % local_user.id, user=agent_user))
    assert resp == {formdata1.id}

    resp = get_ids(sign_uri('/api/users/%s/forms?include-accessible=on' % local_user.id, user=agent_user))
    assert resp == {formdata1.id, formdata3.id}

    # an api access gets the same results
    access.roles = [role]
    access.store()
    app.set_authorization(('Basic', ('test', '12345')))

    resp = get_ids('/api/users/%s/forms' % local_user.id)
    assert resp == {formdata1.id}

    resp = get_ids('/api/users/%s/forms?include-accessible=on' % local_user.id)
    assert resp == {formdata1.id, formdata3.id}


def test_user_drafts(pub, local_user):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [
        fields.StringField(id='0', label='foobar', varname='foobar'),
        fields.StringField(id='1', label='foobar2'),
        fields.FileField(id='2', label='foobar3', varname='file'),
    ]
    formdef.keywords = 'hello, world'
    formdef.disabled = False
    formdef.enable_tracking_codes = True
    formdef.store()

    formdef.data_class().wipe()

    resp = get_app(pub).get(sign_uri('/api/user/drafts', user=local_user))
    assert resp.json['err'] == 0
    assert len(resp.json['data']) == 0

    formdata = formdef.data_class()()
    upload = PicklableUpload('test.txt', 'text/plain', 'ascii')
    upload.receive([b'base64me'])
    formdata.data = {'0': 'foo@localhost', '1': 'xxx', '2': upload}
    formdata.user_id = local_user.id
    formdata.page_no = 1
    formdata.status = 'draft'
    formdata.receipt_time = make_aware(datetime.datetime(2015, 1, 1))
    formdata.store()

    resp = get_app(pub).get(sign_uri('/api/user/drafts', user=local_user))
    assert resp.json['err'] == 0
    assert len(resp.json['data']) == 1
    assert 'fields' not in resp.json['data'][0]
    assert resp.json['data'][0]['keywords'] == ['hello', 'world']

    resp = get_app(pub).get(sign_uri('/api/user/drafts?full=on', user=local_user))
    assert resp.json['err'] == 0
    assert 'fields' in resp.json['data'][0]
    assert resp.json['data'][0]['fields']['foobar'] == 'foo@localhost'
    assert 'url' in resp.json['data'][0]['fields']['file']
    assert 'content' not in resp.json['data'][0]['fields']['file']  # no file content in full lists
    assert resp.json['data'][0]['keywords'] == ['hello', 'world']

    formdef.enable_tracking_codes = False
    formdef.store()
    resp = get_app(pub).get(sign_uri('/api/user/drafts', user=local_user))
    assert resp.json['err'] == 0
    assert len(resp.json['data']) == 1

    formdef.enable_tracking_codes = True
    formdef.disabled = True
    formdef.store()
    resp = get_app(pub).get(sign_uri('/api/user/drafts', user=local_user))
    assert resp.json['err'] == 0
    assert len(resp.json['data']) == 0

    resp = get_app(pub).get(sign_uri('/api/user/drafts?NameID=xxx'))
    assert resp.json == {
        'err': 1,
        'err_class': 'Access denied',
        'err_code': 'unknown-name-id',
        'err_desc': 'Unknown NameID.',
    }
    resp2 = get_app(pub).get(sign_uri('/api/user/drafts?&NameID=xxx'))
    assert resp.json == resp2.json


def test_user_drafts_categories_filter(pub, local_user):
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
    formdef1.fields = [
        fields.StringField(id='0', label='foobar', varname='foobar'),
    ]
    formdef1.category = category1
    formdef1.store()
    formdef2 = FormDef()
    formdef2.name = 'test 2'
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
        formdata.status = 'draft'
        formdata.store()

    for _ in range(3):
        formdata = data_class2()
        formdata.data = {'0': 'FOO BAZ'}
        formdata.user_id = local_user.id
        formdata.status = 'draft'
        formdata.store()

    resp = get_app(pub).get(sign_uri('/api/users/%s/drafts' % local_user.id))
    assert len(resp.json['data']) == 5
    resp = get_app(pub).get(sign_uri('/api/users/%s/drafts?category_slugs=category-1' % local_user.id))
    assert len(resp.json['data']) == 2
    resp = get_app(pub).get(sign_uri('/api/users/%s/drafts?category_slugs=category-2' % local_user.id))
    assert len(resp.json['data']) == 3
    resp = get_app(pub).get(sign_uri('/api/users/%s/drafts?category_slugs=unknown' % local_user.id))
    assert len(resp.json['data']) == 0
    resp = get_app(pub).get(
        sign_uri('/api/users/%s/drafts?category_slugs=category-1,unknown' % local_user.id)
    )
    assert len(resp.json['data']) == 2
    resp = get_app(pub).get(
        sign_uri('/api/users/%s/drafts?category_slugs=category-1,category-2' % local_user.id)
    )
    assert len(resp.json['data']) == 5


def test_user_forms_filter_on_status(pub, local_user):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [
        fields.StringField(id='0', label='foobar', varname='foobar'),
        fields.StringField(id='1', label='foobar2'),
    ]
    formdef.store()
    formdef.data_class().wipe()

    formdata1 = formdef.data_class()()
    formdata1.data = {'0': 'foo@localhost', '1': 'xxx'}
    formdata1.user_id = local_user.id
    formdata1.just_created()
    formdata1.jump_status('new')
    formdata1.store()

    formdata2 = formdef.data_class()()
    formdata2.data = {'0': 'foo@localhost', '1': 'xxx'}
    formdata2.user_id = local_user.id
    formdata2.just_created()
    formdata2.jump_status('finished')
    formdata2.store()

    def get_ids(url):
        resp = get_app(pub).get(url)
        return {int(x['form_number_raw']) for x in resp.json['data']}

    resp = get_ids(sign_uri('/api/user/forms', user=local_user))
    assert resp == {int(formdata1.id), int(formdata2.id)}

    resp = get_ids(sign_uri('/api/user/forms?status=all', user=local_user))
    assert resp == {int(formdata1.id), int(formdata2.id)}

    resp = get_ids(sign_uri('/api/user/forms?status=done', user=local_user))
    assert resp == {int(formdata2.id)}

    resp = get_ids(sign_uri('/api/user/forms?status=open', user=local_user))
    assert resp == {int(formdata1.id)}

    resp = get_ids(sign_uri('/api/user/forms?status=open', user=local_user))
    assert resp == {int(formdata1.id)}


def test_api_user_preferences(pub, local_user):
    app = get_app(pub)
    app.get('/api/user/preferences', status=405)
    app.post_json('/api/user/preferences', {'a': 'b'}, status=403)
    login(app, username='user', password='user')
    app.post_json('/api/user/preferences', {'a': 'b'}, status=200)
    local_user.refresh_from_storage()
    assert local_user.preferences == {'a': 'b'}
    app.post_json('/api/user/preferences', {'c': False}, status=200)
    local_user.refresh_from_storage()
    assert local_user.preferences == {'a': 'b', 'c': False}
    app.post_json('/api/user/preferences', {'d': 'x' * 2000}, status=400)
