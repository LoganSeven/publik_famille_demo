import pytest

from wcs import fields
from wcs.admin.settings import UserFieldsFormDef
from wcs.qommon.http_request import HTTPRequest
from wcs.qommon.ident.password_accounts import PasswordAccount

from ..utilities import clean_temporary_pub, create_temporary_pub, get_app, login
from .test_all import create_role, create_superuser


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


def test_users(pub):
    create_superuser(pub)
    app = login(get_app(pub))
    app.get('/backoffice/users/')


def test_users_new(pub):
    pub.user_class.wipe()
    create_superuser(pub)
    user_count = pub.user_class.count()
    account_count = PasswordAccount.count()
    app = login(get_app(pub))
    resp = app.get('/backoffice/users/')
    resp = resp.click('New User')
    resp.forms[0]['name'] = 'a new user'
    resp = resp.forms[0].submit('submit')
    assert resp.location == 'http://example.net/backoffice/users/'
    resp = resp.follow()
    assert 'a new user' in resp.text
    resp = resp.click('a new user')
    assert 'User - a new user' in resp.text
    assert pub.user_class.count() == user_count + 1
    assert PasswordAccount.count() == account_count


def test_users_new_with_account(pub):
    pub.user_class.wipe()
    PasswordAccount.wipe()
    user = create_superuser(pub)
    user_count = pub.user_class.count()
    account_count = PasswordAccount.count()
    app = login(get_app(pub))
    resp = app.get('/backoffice/users/')
    resp = resp.click('New User')
    resp.forms[0]['name'] = 'a second user'
    resp.forms[0]['method_password$username'] = 'second-user'
    resp.forms[0]['method_password$password'] = 'foobar'
    resp = resp.forms[0].submit('submit')
    assert resp.location == 'http://example.net/backoffice/users/'
    resp = resp.follow()
    assert 'a second user' in resp.text
    assert 'user-inactive' not in resp.text
    resp = resp.click('a second user')
    assert 'User - a second user' in resp.text
    assert pub.user_class.count() == user_count + 1
    assert PasswordAccount.count() == account_count + 1

    user = pub.user_class.get(int(user.id) + 1)
    user.is_active = False
    user.store()
    resp = app.get('/backoffice/users/')
    assert 'user-inactive' in resp.text


def test_users_edit(pub):
    pub.user_class.wipe()
    create_superuser(pub)
    user = pub.user_class(name='foo bar')
    user.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/users/%s/' % user.id)
    assert 'This user is not active.' not in resp.text
    resp = resp.click(href='edit')
    resp.forms[0]['is_admin'].checked = True
    resp = resp.forms[0].submit('submit')
    assert resp.location == 'http://example.net/backoffice/users/%s/' % user.id
    resp = resp.follow()

    user.is_active = False
    user.store()
    resp = app.get('/backoffice/users/%s/' % user.id)
    assert 'This user is not active.' in resp.text


def test_users_edit_new_account(pub):
    pub.user_class.wipe()
    PasswordAccount.wipe()
    create_superuser(pub)
    user = pub.user_class(name='foo bar')
    user.store()
    account_count = PasswordAccount.count()

    app = login(get_app(pub))
    resp = app.get('/backoffice/users/%s/' % user.id)
    resp = resp.click(href='edit')
    resp.forms[0]['is_admin'].checked = True
    resp.forms[0]['method_password$username'] = 'foo'
    resp.forms[0]['method_password$password'] = 'bar'
    resp = resp.forms[0].submit('submit')
    assert resp.location == 'http://example.net/backoffice/users/%s/' % user.id
    resp = resp.follow()

    assert PasswordAccount.count() == account_count + 1


def test_users_edit_edit_account(pub):
    pub.user_class.wipe()
    PasswordAccount.wipe()
    create_superuser(pub)
    user = pub.user_class(name='foo bar')
    user.store()
    account = PasswordAccount(id='test')
    account.user_id = user.id
    account.store()
    assert PasswordAccount.has_key('test')

    app = login(get_app(pub))
    resp = app.get('/backoffice/users/%s/' % user.id)
    resp = resp.click(href='edit')
    resp.forms[0]['is_admin'].checked = True
    resp.forms[0]['method_password$username'] = 'foo'  # change username
    resp.forms[0]['method_password$password'] = 'bar'
    resp = resp.forms[0].submit('submit')
    assert resp.location == 'http://example.net/backoffice/users/%s/' % user.id
    resp = resp.follow()

    # makes sure the old account has been removed
    assert not PasswordAccount.has_key('test')
    assert PasswordAccount.has_key('foo')
    assert PasswordAccount.get('foo').user_id == user.id


def test_users_edit_with_managing_idp(pub):
    create_role(pub)
    pub.user_class.wipe()
    pub.cfg['sp'] = {'idp-manage-user-attributes': True}
    pub.write_cfg()
    PasswordAccount.wipe()
    create_superuser(pub)
    user = pub.user_class(name='foo bar')
    user.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/users/%s/' % user.id)
    assert '>Manage Roles<' in resp.text
    resp = resp.click(href='edit')
    assert 'email' not in resp.form.fields
    assert 'roles$add_element' in resp.form.fields

    pub.cfg['sp'] = {'idp-manage-roles': True}
    pub.write_cfg()
    resp = app.get('/backoffice/users/%s/' % user.id)
    assert '>Edit<' in resp.text
    resp = resp.click(href='edit')
    assert 'email' in resp.form.fields
    assert 'roles$add_element' not in resp.form.fields

    pub.cfg['sp'] = {'idp-manage-roles': True, 'idp-manage-user-attributes': True}
    pub.write_cfg()
    resp = app.get('/backoffice/users/%s/' % user.id)
    assert '/edit' not in resp.text


def test_users_delete(pub):
    pub.user_class.wipe()
    PasswordAccount.wipe()
    create_superuser(pub)
    user = pub.user_class(name='foo bar')
    user.store()
    account = PasswordAccount(id='test')
    account.user_id = user.id
    account.store()

    user_count = pub.user_class.count()
    account_count = PasswordAccount.count()

    app = login(get_app(pub))
    resp = app.get('/backoffice/users/%s/' % user.id)

    resp = resp.click(href='delete')
    resp = resp.forms[0].submit()
    assert resp.location == 'http://example.net/backoffice/users/'
    resp = resp.follow()

    assert pub.user_class.count() == user_count - 1
    assert PasswordAccount.count() == account_count - 1


def test_users_view_deleted(pub):
    pub.user_class.wipe()
    PasswordAccount.wipe()
    create_superuser(pub)
    user = pub.user_class(name='foo bar')
    user.store()
    account = PasswordAccount(id='test')
    account.user_id = user.id
    account.store()

    user.set_deleted()

    app = login(get_app(pub))
    resp = app.get('/backoffice/users/%s/' % user.id)
    assert 'Marked as deleted on' in resp


def test_users_pagination(pub):
    pub.user_class.wipe()
    PasswordAccount.wipe()
    create_superuser(pub)
    for i in range(50):
        user = pub.user_class(name='foo bar %s' % (i + 1))
        user.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/users/')
    assert 'foo bar 10' in resp.text
    assert 'foo bar 30' not in resp.text

    resp = resp.click('Next Page')
    assert 'foo bar 10' not in resp.text
    assert 'foo bar 30' in resp.text

    resp = resp.click('Previous Page')
    assert 'foo bar 10' in resp.text
    assert 'foo bar 30' not in resp.text

    resp = resp.click('Next Page')
    resp = resp.click('Next Page')
    assert 'foo bar 50' in resp.text


def test_users_filter(pub):
    pub.user_class.wipe()
    PasswordAccount.wipe()
    create_superuser(pub)
    role = create_role(pub)
    for i in range(50):
        user = pub.user_class(name='foo bar %s' % (i + 1))
        user.store()

    for i in range(5):
        user = pub.user_class(name='baz bar %s' % (i + 1))
        user.roles = [role.id]
        user.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/users/')
    assert 'admin' in resp.text  # superuser
    assert 'foo bar 10' in resp.text  # simple user

    # uncheck 'None'; unfortunately this doesn't work with webtest 1.3
    #   resp.forms[0].fields['role'][-1].checked = False
    #   resp = resp.forms[0].submit()
    # therefore we fall back on using the URL
    resp = app.get('/backoffice/users/?offset=0&limit=100&q=&filter=true&role=admin')
    assert '>Number of filtered users: 1<' in resp.text
    assert 'user-is-admin' in resp.text  # superuser
    assert 'foo bar 1' not in resp.text  # simple user
    assert 'baz bar 1' not in resp.text  # user with role

    resp = app.get('/backoffice/users/?offset=0&limit=100&q=&filter=true&role=1')
    assert '>Number of filtered users: 5<' in resp.text
    assert 'user-is-admin' not in resp.text  # superuser
    assert 'foo bar 10' not in resp.text  # simple user
    assert 'baz bar 1' in resp.text  # user with role


def test_users_search(pub):
    pub.user_class.wipe()
    PasswordAccount.wipe()
    create_superuser(pub)
    for i in range(20):
        user = pub.user_class(name='foo %s' % (i + 1))
        user.store()
    for i in range(10):
        user = pub.user_class(name='bar %s' % (i + 1))
        user.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/users/')
    assert 'foo 10' in resp.text

    resp.forms[0]['q'] = 'bar'
    resp = resp.forms[0].submit()
    assert 'foo 10' not in resp.text
    assert 'bar 10' in resp.text
    assert 'Number of filtered users: 10' in resp.text


def test_users_new_with_custom_formdef(pub):
    pub.user_class.wipe()
    formdef = UserFieldsFormDef(pub)
    formdef.fields.append(fields.StringField(id='3', label='test'))
    formdef.fields.append(fields.CommentField(id='4', label='test'))
    formdef.fields.append(fields.FileField(id='5', label='test', required='optional'))
    formdef.store()

    create_superuser(pub)
    user_count = pub.user_class.count()
    account_count = PasswordAccount.count()
    app = login(get_app(pub))
    resp = app.get('/backoffice/users/')
    resp = resp.click('New User')
    resp.form['name'] = 'a new user'
    resp.form['f3'] = 'TEST'
    resp = resp.form.submit('submit')
    assert resp.location == 'http://example.net/backoffice/users/'
    resp = resp.follow()
    assert 'a new user' in resp.text
    resp = resp.click('a new user')
    assert 'User - a new user' in resp.text
    assert 'TEST' in resp.text
    assert pub.user_class.count() == user_count + 1
    assert PasswordAccount.count() == account_count


def test_users_display_roles(pub):
    pub.user_class.wipe()

    user = create_superuser(pub)
    role = create_role(pub)
    user.roles = [role.id, 'XXX']
    user.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/users/%s/' % user.id)
    assert role.name in resp.text
    assert 'Unknown role (XXX)' in resp.text
