import os

import pytest

from wcs.categories import Category
from wcs.formdef import FormDef
from wcs.qommon.http_request import HTTPRequest

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

    return pub


def teardown_module(module):
    clean_temporary_pub()


def test_categories(pub):
    FormDef.wipe()
    Category.wipe()
    category = Category()
    category.name = 'Category'
    category.description = 'hello world'
    category.store()

    resp = get_app(pub).get('/api/categories/', headers={'Accept': 'application/json'})
    assert resp.json['data'] == []  # no advertised forms

    formdef = FormDef()
    formdef.name = 'test'
    formdef.category_id = category.id
    formdef.fields = []
    formdef.keywords = 'mobile, test'
    formdef.store()
    formdef.data_class().wipe()

    formdef = FormDef()
    formdef.name = 'test 2'
    formdef.category_id = category.id
    formdef.fields = []
    formdef.keywords = 'foobar'
    formdef.store()
    formdef.data_class().wipe()

    resp = get_app(pub).get('/api/categories/')
    resp2 = get_app(pub).get('/categories', headers={'Accept': 'application/json'})
    assert resp.json == resp2.json
    assert resp.json['data'][0]['title'] == 'Category'
    assert resp.json['data'][0]['url'] == 'http://example.net/category/'
    assert resp.json['data'][0]['description'] == '<p>hello world</p>'
    assert set(resp.json['data'][0]['keywords']) == {'foobar', 'mobile', 'test'}
    assert 'forms' not in resp.json['data'][0]

    # check HTML description
    category.description = '<p><strong>hello world</strong></p>'
    category.store()
    resp = get_app(pub).get('/api/categories/')
    assert resp.json['data'][0]['description'] == category.description


def test_categories_private(pub, local_user):
    FormDef.wipe()
    Category.wipe()
    category = Category()
    category.name = 'Category'
    category.description = 'hello world'
    category.store()

    formdef = FormDef()
    formdef.name = 'test'
    formdef.category_id = category.id
    formdef.fields = []
    formdef.store()
    formdef.data_class().wipe()

    # open form
    resp = get_app(pub).get('/api/categories/')
    assert len(resp.json['data']) == 1

    # private form, the category doesn't appear anymore
    formdef.roles = ['plop']
    formdef.store()
    resp = get_app(pub).get('/api/categories/')
    assert len(resp.json['data']) == 0

    # not even for a signed request specifying an user
    resp = get_app(pub).get(sign_uri('http://example.net/api/categories/', local_user))
    assert len(resp.json['data']) == 0

    # but it appears if this is a signed request without user
    resp = get_app(pub).get(sign_uri('http://example.net/api/categories/'))
    assert len(resp.json['data']) == 1

    # or signed with an authorised user
    local_user.roles = ['plop']
    local_user.store()
    resp = get_app(pub).get(sign_uri('http://example.net/api/categories/', local_user))
    assert len(resp.json['data']) == 1


def test_categories_formdefs(pub, local_user):
    FormDef.wipe()
    Category.wipe()
    category = Category()
    category.name = 'Category'
    category.description = 'hello world'
    category.store()

    formdef = FormDef()
    formdef.name = 'test'
    formdef.category_id = category.id
    formdef.fields = []
    formdef.keywords = 'mobile, test'
    formdef.store()
    formdef.data_class().wipe()

    formdef = FormDef()
    formdef.name = 'test 2'
    formdef.category_id = category.id
    formdef.fields = []
    formdef.keywords = 'foobar'
    formdef.store()
    formdef.data_class().wipe()

    formdef2 = FormDef()
    formdef2.name = 'other test'
    formdef2.category_id = None
    formdef2.fields = []
    formdef2.store()
    formdef2.data_class().wipe()

    formdef2 = FormDef()
    formdef2.name = 'test disabled'
    formdef2.category_id = category.id
    formdef2.fields = []
    formdef2.disabled = True
    formdef2.store()
    formdef2.data_class().wipe()

    resp = get_app(pub).get('/api/categories/category/formdefs/', status=403)
    resp2 = get_app(pub).get('/category/json', status=403)
    resp = get_app(pub).get(sign_uri('/api/categories/category/formdefs/'))
    resp2 = get_app(pub).get(sign_uri('/category/json'))
    assert resp.json == resp2.json
    assert resp.json['err'] == 0
    assert len(resp.json['data']) == 2
    assert resp.json['data'][0]['title'] == 'test'
    assert resp.json['data'][0]['url'] == 'http://example.net/test/'
    assert resp.json['data'][0]['redirection'] is False
    assert resp.json['data'][0]['category'] == 'Category'
    assert resp.json['data'][0]['category_slug'] == 'category'
    assert 'count' not in resp.json['data'][0]

    resp = get_app(pub).get(sign_uri('/api/categories/category/formdefs/?include-count=on'))
    assert resp.json['data'][0]['title'] == 'test'
    assert resp.json['data'][0]['url'] == 'http://example.net/test/'
    assert resp.json['data'][0]['count'] == 0

    resp = get_app(pub).get(sign_uri('/api/categories/category/formdefs/?include-disabled=on'))
    assert len(resp.json['data']) == 3
    assert resp.json['data'][2]['title'] == 'test disabled'

    get_app(pub).get('/api/categories/XXX/formdefs/', status=404)

    resp = get_app(pub).get(sign_uri('/api/categories/category/formdefs/?backoffice-submission=on'))
    assert resp.json['err'] == 0
    assert len(resp.json['data']) == 0

    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()
    local_user.roles = []
    local_user.store()
    # check it's not advertised ...
    formdef.backoffice_submission_roles = [role.id]
    formdef.store()
    resp = get_app(pub).get(sign_uri('/api/categories/category/formdefs/?backoffice-submission=on'))
    assert resp.json['err'] == 0
    assert len(resp.json['data']) == 0
    resp = get_app(pub).get(
        sign_uri(
            '/api/categories/category/formdefs/?backoffice-submission=on&NameID=%s'
            % local_user.name_identifiers[0]
        )
    )
    assert resp.json['err'] == 0
    assert len(resp.json['data']) == 0
    # ... unless user has correct roles
    local_user.roles = [role.id]
    local_user.store()
    resp = get_app(pub).get(
        sign_uri(
            '/api/categories/category/formdefs/?backoffice-submission=on&NameID=%s'
            % local_user.name_identifiers[0]
        )
    )
    assert resp.json['err'] == 0
    assert len(resp.json['data']) == 1


def test_categories_full(pub):
    test_categories(pub)
    resp = get_app(pub).get('/api/categories/?full=on')
    assert len(resp.json['data'][0]['forms']) == 2
    assert resp.json['data'][0]['forms'][0]['title'] == 'test'
    assert resp.json['data'][0]['forms'][1]['title'] == 'test 2'
