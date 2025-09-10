import os

import pytest

from wcs.formdef import FormDef
from wcs.qommon.http_request import HTTPRequest
from wcs.qommon.ident.password_accounts import PasswordAccount

from ..utilities import clean_temporary_pub, create_temporary_pub, get_app, login


@pytest.fixture
def pub(request):
    pub = create_temporary_pub()

    req = HTTPRequest(None, {'SCRIPT_NAME': '/', 'SERVER_NAME': 'example.net'})
    pub.set_app_dir(req)
    pub.cfg['identification'] = {'methods': ['password']}
    pub.cfg['language'] = {'language': 'en'}
    pub.write_cfg()

    return pub


def create_superuser(pub):
    if pub.user_class.select(lambda x: x.name == 'admin'):
        user1 = pub.user_class.select(lambda x: x.name == 'admin')[0]
        user1.is_admin = True
        user1.store()
        return user1

    user1 = pub.user_class(name='admin')
    user1.is_admin = True
    user1.email = 'admin@example.com'
    user1.store()

    account1 = PasswordAccount(id='admin')
    account1.set_password('admin')
    account1.user_id = user1.id
    account1.store()

    return user1


def create_role(pub):
    pub.role_class.wipe()
    role = pub.role_class(name='foobar')
    role.store()
    return role


def teardown_module(module):
    clean_temporary_pub()


def test_empty_site(pub):
    pub.user_class.wipe()
    resp = get_app(pub).get('/backoffice/users/')
    resp = resp.click('New User')
    resp = get_app(pub).get('/backoffice/settings/')


def test_empty_site_but_idp_settings(pub):
    pub.cfg['idp'] = {'xxx': {}}
    pub.write_cfg()
    resp = get_app(pub).get('/backoffice/')
    assert resp.location == 'http://example.net/login/?next=http%3A%2F%2Fexample.net%2Fbackoffice%2F'


def test_with_user(pub):
    create_superuser(pub)
    resp = get_app(pub).get('/backoffice/', status=302)
    assert resp.location == 'http://example.net/login/?next=http%3A%2F%2Fexample.net%2Fbackoffice%2F'


def test_with_superuser(pub):
    create_superuser(pub)
    app = login(get_app(pub))
    app.get('/backoffice/')


def test_admin_redirect(pub):
    create_superuser(pub)
    app = login(get_app(pub))
    assert app.get('/admin/whatever', status=302).location == 'http://example.net/backoffice/whatever'


def test_admin_for_all(pub):
    user = create_superuser(pub)
    role = create_role(pub)

    formdef = FormDef()
    formdef.name = 'test'
    formdef.store()

    try:
        with open(os.path.join(pub.app_dir, 'ADMIN_FOR_ALL'), 'w'):
            pass  # create empty file
        resp = get_app(pub).get('/backoffice/')
        assert resp.location.endswith('studio/')
        resp = resp.follow()
        # check there is a CSS class
        assert resp.pyquery.find('body.admin-for-all')

        # check there are menu items
        resp.click('Forms', index=0)
        resp.click('Settings', index=0)

        # cheeck it's possible to get inside the subdirectories
        resp = get_app(pub).get('/backoffice/settings/', status=200)

        pub.cfg['admin-permissions'] = {'settings': [role.id]}
        pub.write_cfg()
        resp = get_app(pub).get('/backoffice/settings/', status=200)

        # check it doesn't work with a non-empty ADMIN_FOR_ALL file
        with open(os.path.join(pub.app_dir, 'ADMIN_FOR_ALL'), 'w') as fd:
            fd.write('x.x.x.x')
        resp = get_app(pub).get('/backoffice/settings/', status=302)

        # check it works if the file contains the user IP address
        with open(os.path.join(pub.app_dir, 'ADMIN_FOR_ALL'), 'w') as fd:
            fd.write('127.0.0.1')
        resp = get_app(pub).get('/backoffice/settings/', status=200)

        # check it's also ok if the user is logged in but doesn't have the
        # permissions
        user.is_admin = False
        user.store()
        resp = login(get_app(pub)).get('/backoffice/settings/', status=200)
        # check there are menu items
        resp.click('Management', index=0)
        resp.click('Forms', index=0)
        resp.click('Settings', index=0)

    finally:
        if 'admin-permissions' in pub.cfg:
            del pub.cfg['admin-permissions']
            pub.write_cfg()
        os.unlink(os.path.join(pub.app_dir, 'ADMIN_FOR_ALL'))
        role.remove_self()
        user.is_admin = True
        user.store()


def test_users_roles_menu_entries(pub):
    create_superuser(pub)
    app = login(get_app(pub))
    resp = app.get('/backoffice/management/forms')
    assert resp.pyquery('#sidepage-menu .icon-users')
    assert resp.pyquery('#sidepage-menu .icon-roles')
    resp = app.get('/backoffice/menu.json')
    assert 'Users' in [x['label'] for x in resp.json]
    assert 'Roles' in [x['label'] for x in resp.json]

    # don't include users/roles in menu if roles are managed by an external
    # identity provider.
    pub.cfg['sp'] = {'idp-manage-roles': True}
    pub.write_cfg()

    resp = app.get('/backoffice/management/forms')
    assert not resp.pyquery('#sidepage-menu .icon-users')
    assert not resp.pyquery('#sidepage-menu .icon-roles')
    resp = app.get('/backoffice/menu.json')
    assert 'Users' not in [x['label'] for x in resp.json]
    assert 'Roles' not in [x['label'] for x in resp.json]
