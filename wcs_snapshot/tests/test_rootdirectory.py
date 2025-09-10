import os
from unittest import mock

import psycopg2
import pytest
from quixote import get_request

import wcs.forms.root
from wcs.categories import Category
from wcs.formdef import FormDef
from wcs.qommon import sessions
from wcs.qommon.http_request import HTTPRequest
from wcs.qommon.ident.password_accounts import PasswordAccount

from .utilities import clean_temporary_pub, create_temporary_pub, get_app, login


@pytest.fixture
def pub():
    pub = create_temporary_pub()

    req = HTTPRequest(None, {'SCRIPT_NAME': '/'})
    req._user = None
    pub._set_request(req)
    req.session = sessions.Session(id=1)

    FormDef.wipe()
    Category.wipe()
    pub.user_class.wipe()

    yield pub
    clean_temporary_pub()


@pytest.fixture
def user1(pub):
    user1 = pub.user_class(name='user-one-role')
    user1.id = 'user-one-role'
    user1.roles = ['role-1']
    return user1


@pytest.fixture
def user2(pub):
    user2 = pub.user_class(name='user-other-role')
    user2.id = 'user-other-role'
    user2.roles = ['role-2']
    return user2


@pytest.fixture
def category(pub):
    category = Category()
    category.name = 'category1'
    category.store()
    return category


@pytest.fixture
def formdef1(pub, category):
    formdef1 = FormDef()
    formdef1.category_id = category.id
    formdef1.name = formdef1.url_name = 'test-formdef-1'
    formdef1.store()
    return formdef1


@pytest.fixture
def formdef2(pub, category):
    formdef2 = FormDef()
    formdef2.category_id = category.id
    formdef2.name = formdef2.url_name = 'test-formdef-2'
    formdef2.store()
    return formdef2


def indexhtml(user=None):
    req = get_request()
    req._user = user
    req.session.user = user.id if user else None
    return str(wcs.forms.root.RootDirectory()._q_index())


def test_empty_site(pub):
    assert indexhtml() == ''


def test_public_site_anonymous_access(pub, formdef1, formdef2):
    output = indexhtml()
    assert 'href="category1/test-formdef-1/"' in output
    assert 'href="category1/test-formdef-2/"' in output


def test_private_site_anonymous_access(pub, formdef1, formdef2):
    formdef1.roles = formdef2.roles = ['role-1']
    formdef1.store()
    formdef2.store()
    with pytest.raises(wcs.forms.root.errors.AccessUnauthorizedError):
        indexhtml()


def test_semi_private_site_anonymous_access(pub, formdef1, formdef2):
    formdef1.roles = ['role-1']
    formdef1.store()
    output = indexhtml()
    assert 'href="category1/test-formdef-1/"' not in output
    assert 'href="category1/test-formdef-2/"' in output


def test_private_site_authorized_access(pub, formdef1, formdef2, user1):
    formdef1.roles = formdef2.roles = ['role-1']
    formdef1.store()
    formdef2.store()
    output = indexhtml(user1)
    assert 'href="category1/test-formdef-1/"' in output
    assert 'href="category1/test-formdef-2/"' in output


def test_private_site_unauthorized_access(pub, formdef1, formdef2, user2):
    formdef1.roles = formdef2.roles = ['role-1']
    formdef1.store()
    formdef2.store()
    with pytest.raises(wcs.forms.root.errors.AccessUnauthorizedError):
        indexhtml(user2)


def test_private_site_semi_authorized_access(pub, formdef1, formdef2, user1):
    formdef1.roles = ['role-1']
    formdef2.roles = ['role-2']
    formdef1.store()
    formdef2.store()
    output = indexhtml(user1)
    assert 'href="category1/test-formdef-1/"' in output
    assert 'href="category1/test-formdef-2/"' not in output


def test_advertized_site_anonymous_access(pub, formdef1, formdef2):
    formdef1.roles = formdef2.roles = ['role-1']
    formdef1.always_advertise = True
    formdef1.store()
    formdef2.store()
    output = indexhtml()
    assert 'href="category1/test-formdef-1/"' in output
    assert 'href="category1/test-formdef-2/"' not in output
    assert 'authentication required' in output  # locales ?


def test_advertized_site_user_access(pub, formdef1, formdef2, user1):
    formdef1.roles = formdef2.roles = ['role-2']
    formdef1.always_advertise = True
    formdef1.store()
    formdef2.store()
    output = indexhtml(user1)
    assert 'href="category1/test-formdef-1/"' in output
    assert 'href="category1/test-formdef-2/"' not in output
    assert 'authentication required' in output  # locales ?


def test_categories_page(pub, category, formdef1):
    resp = get_app(pub).get('/categories')
    assert 'href="category1/"' in resp
    FormDef.wipe()
    resp = get_app(pub).get('/categories')
    assert 'href="category1/"' not in resp


def test_static_directories(pub):
    assert get_app(pub).get('/static/images/feed-icon-10x10.png')
    assert get_app(pub).get('/static/css/gadjo.css')
    assert get_app(pub).get('/static/xstatic/jquery.js')
    assert get_app(pub).get('/static/xstatic/jquery-ui.js')

    assert 'Directory listing denied' in get_app(pub).get('/static/css/').text
    assert get_app(pub).get('/static/xxx', status=404)


def test_jquery_debug_mode(pub, formdef1):
    resp = get_app(pub).get('/category1/test-formdef-1/')
    assert 'jquery.min.js' in resp.text
    pub.cfg['debug'] = {'debug_mode': True}
    pub.write_cfg()
    resp = get_app(pub).get('/category1/test-formdef-1/')
    assert 'jquery.js' in resp.text


def test_i18n_js(pub):
    get_app(pub).get('/i18n.js')


def test_no_database_site(pub):
    pub.cfg['postgresql'] = {}
    pub.write_cfg()
    resp = get_app(pub).get('/', status=503)
    assert resp.text == 'Missing database configuration'


def test_myspace_redirect(pub):
    resp = get_app(pub).get('/myspace/', status=302)
    assert '/login/' in resp.location

    if not pub.site_options.has_section('variables'):
        pub.site_options.add_section('variables')
    pub.site_options.set('variables', 'idp_account_url', 'https://idp/account/')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)

    resp = get_app(pub).get('/myspace/', status=302)
    assert resp.location == 'https://idp/account/'


def test_myspace_password_change(pub):
    pub.cfg['identification'] = {'methods': ['password']}
    pub.cfg['passwords'] = {'can_change': True}
    pub.write_cfg()
    user = pub.user_class(name='user')
    user.store()
    account = PasswordAccount(id='user')
    account.set_password('pwd')
    account.user_id = user.id
    account.store()

    app = login(get_app(pub), username='user', password='pwd')
    resp = app.get('/myspace/')
    resp = resp.click('Change My Password')
    resp.form['new_password$pwd1'] = 'bar'
    resp.form['new_password$pwd2'] = 'baz'
    resp = resp.form.submit('submit')
    assert resp.pyquery('.error').text() == 'Passwords do not match.'
    resp.form['new_password$pwd2'] = 'bar'
    resp = resp.form.submit('submit')
    assert PasswordAccount.get_with_credentials('user', 'bar')


def test_invalid_site_options(pub):
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        fd.write('xxx')
    with pytest.raises(Exception):
        get_app(pub).get('/', status=500)


def test_postgresql_down(pub):
    with mock.patch('psycopg2.connect', side_effect=psycopg2.OperationalError()):
        resp = get_app(pub).get('/', status=503)
        assert 'Error connecting to database' in resp.text


def test_short_url_redirect(pub, formdef1):
    formdata = formdef1.data_class()()
    formdata.just_created()
    formdata.store()

    app = get_app(pub)
    app.get('/r/xxx', status=404)
    app.get('/r/300', status=404)
    app.get('/r/300-100', status=404)
    resp = app.get(f'/r/{formdef1.id}', status=302)
    assert resp.location == formdef1.get_url()
    resp = app.get(f'/r/{formdef1.id}-{formdata.id}', status=302)
    assert resp.location == formdata.get_url()
    assert formdata.get_short_url() == f'http://example.net/r/{formdef1.id}-{formdata.id}'
    resp = app.get(formdata.get_short_url(), status=302)
    assert resp.location == formdata.get_url()


def test_not_allowed_hostname(pub):
    app = get_app(pub)
    app.get('/', status=200)

    with open(os.path.join(pub.APP_DIR, 'example.net', 'site-options.cfg'), 'w') as fd:
        fd.write('[options]\nallowed_hostname = another-example.net\n')
    app.get('/', status=404)

    with open(os.path.join(pub.APP_DIR, 'example.net', 'site-options.cfg'), 'w') as fd:
        fd.write('[options]\nallowed_hostname = example.net\n')
    app.get('/', status=200)
