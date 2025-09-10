import http.cookies

import pytest

from wcs.qommon.ident.password_accounts import PasswordAccount

from .utilities import clean_temporary_pub, create_temporary_pub, get_app, login


@pytest.fixture
def pub():
    pub = create_temporary_pub()
    pub.cfg['identification'] = {'methods': ['password']}
    pub.cfg['language'] = {'language': 'en'}
    pub.write_cfg()

    pub.user_class.wipe()
    PasswordAccount.wipe()

    user = pub.user_class()
    user.email = 'foo@localhost'
    user.store()
    account = PasswordAccount(id='foo')
    account.set_password('foo')
    account.user_id = user.id
    account.store()

    return pub


@pytest.fixture
def pub_2auth(pub):
    pub.cfg['identification'] = {'methods': ['password', 'idp']}
    pub.write_cfg()

    # setup saml
    from wcs.qommon.ident.idp import MethodAdminDirectory

    from .test_saml_auth import setup_idps

    pub.cfg['sp'] = {
        'saml2_metadata': 'saml2-metadata.xml',
        'saml2_base_url': 'http://example.net/saml',
        'saml2_providerid': 'http://example.net/saml/metadata',
    }
    MethodAdminDirectory().generate_rsa_keypair()
    setup_idps(pub)

    return pub


def teardown_module(module):
    clean_temporary_pub()


def test_login_cookie(pub):
    app = get_app(pub)
    assert not app.cookies
    resp = app.get('/login/')
    resp.form['username'] = 'foo'
    resp.form['password'] = 'foo'
    resp = resp.form.submit()
    assert app.cookies
    cookie_name = pub.config.session_cookie_name
    cookie_store = http.cookies.SimpleCookie()
    cookie_store.load(resp.headers['Set-Cookie'])
    assert list(cookie_store.keys()) == [cookie_name]
    assert 'HttpOnly' in resp.headers['Set-Cookie']
    assert 'SameSite=None' in resp.headers['Set-Cookie']
    assert 'Path=/' in resp.headers['Set-Cookie']


def test_login_logout(pub):
    resp_initial = get_app(pub).get('/')
    resp = login(get_app(pub), username='foo', password='foo').get('/')
    resp = resp.click('Logout')
    resp = resp.follow()
    assert resp.text == resp_initial.text


def test_register_account(pub):
    resp = get_app(pub).get('/').click('Login').follow()
    assert not 'register' in resp.text

    pub.cfg['identities'] = {'creation': 'self'}
    pub.write_cfg()
    resp = get_app(pub).get('/').click('Login').follow()
    assert 'register' in resp.text
    resp = resp.click('New Account page')
    resp.form['username'] = 'foobar'
    assert resp.form.submit().location == 'http://example.net/login/'
    assert PasswordAccount.count() == 2
    assert pub.user_class.count() == 2


def test_login_2auth(pub_2auth):
    # check sso is initiated if there is both password and saml support
    resp = get_app(pub_2auth).get('/').click('Login').follow()
    assert resp.location.startswith('http://sso.example.net/saml')


def test_register_2auth(pub_2auth):
    pub_2auth.cfg['saml_identities'] = {
        'identity-creation': 'self',
        'registration-url': 'http://sso.example.net/registration',
    }
    pub_2auth.write_cfg()
    resp = get_app(pub_2auth).get('/register/')
    assert resp.location == 'http://sso.example.net/registration'
