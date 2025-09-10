import pytest

from wcs.formdef import FormDef
from wcs.qommon.http_request import HTTPRequest

from ..utilities import clean_temporary_pub, create_temporary_pub, get_app, login
from .test_all import create_superuser


@pytest.fixture
def pub(request):
    pub = create_temporary_pub()

    req = HTTPRequest(None, {'SCRIPT_NAME': '/', 'SERVER_NAME': 'example.net'})
    pub.set_app_dir(req)
    pub.cfg['identification'] = {'methods': ['password']}
    pub.cfg['language'] = {'language': 'en'}
    pub.write_cfg()

    return pub


def teardown_module(module):
    clean_temporary_pub()


def test_roles(pub):
    create_superuser(pub)
    app = login(get_app(pub))
    app.get('/backoffice/roles/')


def test_roles_new(pub):
    create_superuser(pub)
    pub.role_class.wipe()
    app = login(get_app(pub))
    resp = app.get('/backoffice/roles/')
    resp = resp.click('New Role')
    resp.forms[0]['name'] = 'a new role'
    resp.forms[0]['details'] = 'bla bla bla'
    resp = resp.forms[0].submit('submit')
    assert resp.location == 'http://example.net/backoffice/roles/'
    resp = resp.follow()
    assert 'a new role' in resp.text
    resp = resp.click('a new role')
    assert '<h2>a new role' in resp.text

    assert pub.role_class.get(1).name == 'a new role'
    assert pub.role_class.get(1).details == 'bla bla bla'


def test_roles_edit(pub):
    create_superuser(pub)
    pub.role_class.wipe()
    role = pub.role_class(name='foobar')
    role.allows_backoffice_access = True
    role.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/roles/1/')
    assert 'Holders of this role are granted access to the backoffice' in resp.text

    resp = resp.click(href='edit')
    assert resp.forms[0]['name'].value == 'foobar'
    resp.forms[0]['name'] = 'baz'
    resp.forms[0]['details'] = 'bla bla bla'
    resp.forms[0]['emails_to_members'].checked = True
    resp = resp.forms[0].submit('submit')
    assert resp.location == 'http://example.net/backoffice/roles/1/'
    resp = resp.follow()
    assert '<h2>baz' in resp.text
    assert 'Holders of this role will receive all emails adressed to the role.' in resp.text

    assert pub.role_class.get(1).details == 'bla bla bla'
    assert pub.role_class.get(1).emails_to_members is True


def test_roles_matching_formdefs(pub):
    create_superuser(pub)
    pub.role_class.wipe()
    role = pub.role_class(name='foo')
    role.store()

    FormDef.wipe()
    app = login(get_app(pub))
    resp = app.get('/backoffice/roles/1/')
    assert 'form bar' not in resp.text

    formdef = FormDef()
    formdef.name = 'form bar'
    formdef.roles = [role.id]
    formdef.fields = []
    formdef.store()

    resp = app.get('/backoffice/roles/1/')
    assert 'form bar' in resp.text
    assert 'form baz' not in resp.text

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form baz'
    formdef.fields = []
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.store()

    resp = app.get('/backoffice/roles/1/')
    assert 'form baz' in resp.text
    assert 'form bar' not in resp.text


def test_roles_delete(pub):
    create_superuser(pub)
    pub.role_class.wipe()
    role = pub.role_class(name='foobar')
    role.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/roles/1/')

    resp = resp.click(href='delete')
    resp = resp.forms[0].submit()
    assert resp.location == 'http://example.net/backoffice/roles/'
    resp = resp.follow()
    assert pub.role_class.count() == 0
