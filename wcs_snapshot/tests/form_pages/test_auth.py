import pytest

from wcs.formdef import FormDef

from ..utilities import clean_temporary_pub, create_temporary_pub, get_app, login
from .test_all import create_user


@pytest.fixture
def pub(request):
    pub = create_temporary_pub()
    pub.cfg['identification'] = {'methods': ['password']}
    pub.cfg['language'] = {'language': 'en'}
    pub.write_cfg()
    return pub


@pytest.fixture
def formdef(pub):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = []
    formdef.store()
    return formdef


def teardown_module(module):
    clean_temporary_pub()


def test_form_auth(pub, formdef):
    create_user(pub)

    resp = get_app(pub).get('/test/auth')
    assert resp.location == 'http://example.net/login/?ReturnUrl=http%3A//example.net/test/'
    resp = get_app(pub).get('/test/auth?param1=foo&param2=bar')
    assert (
        resp.location
        == 'http://example.net/login/?ReturnUrl=http%3A//example.net/test/%3Fparam1%3Dfoo%26param2%3Dbar'
    )

    app = login(get_app(pub), username='foo', password='foo')
    resp = app.get('/test/auth')
    assert resp.location == 'http://example.net/test/'
    resp = app.get('/test/auth?param1=foo&param2=bar')
    assert resp.location == 'http://example.net/test/?param1=foo&param2=bar'


def test_form_tryauth(pub, formdef):
    create_user(pub)

    resp = get_app(pub).get('/test/tryauth')
    assert resp.location == 'http://example.net/test/'
    resp = get_app(pub).get('/test/tryauth?param1=foo&param2=bar')
    assert resp.location == 'http://example.net/test/?param1=foo&param2=bar'

    app = login(get_app(pub), username='foo', password='foo')
    pub.cfg['identification'] = {'methods': ['idp']}
    pub.write_cfg()
    # if the user is logged in, the form should be presented
    resp = app.get('/test/tryauth')
    assert resp.location == 'http://example.net/test/'
    resp = app.get('/test/tryauth?param1=foo&param2=bar')
    assert resp.location == 'http://example.net/test/?param1=foo&param2=bar'

    # if the user is unlogged, there should be a passive redirection to SSO
    resp = get_app(pub).get('/test/tryauth')
    assert 'IsPassive=true' in resp.location

    pub.cfg['identification'] = {'methods': ['password']}
    pub.write_cfg()


def test_form_forceauth(pub, formdef):
    create_user(pub)

    resp = get_app(pub).get('/test/forceauth')
    assert resp.location == (
        'http://example.net/login/' '?ReturnUrl=http%3A//example.net/test/&forceAuthn=true'
    )
    resp = get_app(pub).get('/test/forceauth?param1=foo&param2=bar')
    assert resp.location == (
        'http://example.net/login/'
        '?ReturnUrl=http%3A//example.net/test/%3Fparam1%3Dfoo%26param2%3Dbar&forceAuthn=true'
    )
