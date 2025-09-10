import hashlib
import re

import pytest

from wcs.qommon import force_str
from wcs.qommon.ident.password_accounts import PasswordAccount

from .utilities import clean_temporary_pub, create_temporary_pub, get_app


@pytest.fixture
def pub(request):
    pub = create_temporary_pub()
    pub.cfg['identification'] = {'methods': ['password']}
    pub.cfg['identities'] = {'creation': 'self'}
    pub.write_cfg()
    yield pub
    clean_temporary_pub()


def test_no_configuration(pub):
    pub.cfg['identification'] = {}
    pub.write_cfg()
    resp = get_app(pub).get('/register/')
    assert 'Authentication subsystem is not yet configured.' in resp.text


def test_no_user_registration(pub):
    # makes sure the page is not published unless configured
    app = get_app(pub)
    pub.cfg['identification'] = {'methods': ['password']}
    pub.cfg['identities'] = {'creation': 'admin'}
    pub.write_cfg()
    app.get('/register/', status=404)
    pub.cfg['identities'] = {'creation': 'self'}
    pub.write_cfg()


def test_link_on_login_page(pub):
    app = get_app(pub)
    page = app.get('/login/')
    assert '/register/' in page.text


def test_no_password(pub):
    app = get_app(pub)
    page = app.get('/register/')
    register_form = page.forms[0]
    assert 'username' in register_form.fields
    assert 'password' not in register_form.fields


def test_user_registration_mismatch(pub):
    pub.cfg['passwords'] = {'generate': False}
    pub.write_cfg()
    app = get_app(pub)
    page = app.get('/register/')
    register_form = page.forms[0]
    register_form['username'] = 'foo'
    register_form['password$pwd1'] = 'bar'
    register_form['password$pwd2'] = 'baz'
    resp = register_form.submit()
    assert 'Passwords do not match' in resp.text


def do_user_registration(pub, username='foo', password='bar'):
    initial_user_count = pub.user_class.count()
    initial_account_count = PasswordAccount.count()
    app = get_app(pub)
    page = app.get('/register/')
    register_form = page.forms[0]
    register_form['username'] = username
    if password is not None:
        register_form['password$pwd1'] = password
        register_form['password$pwd2'] = password
    resp = register_form.submit()
    assert resp.status_int == 302
    assert resp.location == 'http://example.net/login/'

    assert pub.user_class.count() == initial_user_count + 1
    assert PasswordAccount.count() == initial_account_count + 1

    account = PasswordAccount.get(username)
    user = account.get_user()
    if password is not None:
        user2 = PasswordAccount.get_with_credentials(username, password)
        assert user.id == user2.id


def test_user_registration(pub):
    pub.user_class.wipe()
    PasswordAccount.wipe()
    pub.cfg['passwords'] = {'generate': False, 'hashing_algo': None}
    pub.write_cfg()
    do_user_registration(pub)

    account = PasswordAccount.get('foo')
    assert account.password == 'bar'  # check it's in clear text


def test_user_password_hashing(pub):
    pub.user_class.wipe()
    PasswordAccount.wipe()
    pub.cfg['passwords'] = {'generate': False, 'hashing_algo': 'sha256'}
    pub.write_cfg()
    do_user_registration(pub)

    account = PasswordAccount.get('foo')
    assert account.password == hashlib.sha256(b'bar').hexdigest()


def test_user_password_accents(pub):
    pub.user_class.wipe()
    PasswordAccount.wipe()
    pub.cfg['passwords'] = {'generate': False, 'hashing_algo': None}
    pub.write_cfg()
    password = force_str('fooë')
    do_user_registration(pub, password=password)

    account = PasswordAccount.get('foo')
    assert account.password == password


def test_admin_notification(pub, emails):
    pub.cfg['identities'] = {'creation': 'self', 'notify-on-register': True}
    pub.write_cfg()
    pub.user_class.wipe()
    PasswordAccount.wipe()
    user = pub.user_class(name='admin')
    user.is_admin = True
    user.email = 'admin@localhost'
    user.store()

    pub.cfg['passwords'] = {'generate': False}
    pub.write_cfg()
    do_user_registration(pub)

    assert emails.get('New Registration')
    assert emails.get('New Registration')['email_rcpt'] == ['admin@localhost']
    assert 'A new account has been created on example.net.' in emails.get('New Registration').email.body


def test_user_notification(pub, emails):
    pub.cfg['identities'] = {'creation': 'self', 'notify-on-register': False, 'email-as-username': True}
    pub.write_cfg()
    pub.user_class.wipe()
    PasswordAccount.wipe()
    user = pub.user_class(name='admin')
    user.is_admin = True
    user.email = 'admin@localhost'
    user.store()

    pub.cfg['passwords'] = {'generate': True, 'hashing_algo': None}
    pub.write_cfg()
    do_user_registration(pub, username='foo@localhost', password=None)

    account = PasswordAccount.get('foo@localhost')

    assert emails.get('Welcome to example.net')
    assert emails.get('Welcome to example.net')['to'] == 'foo@localhost'
    assert account.password in emails.get('Welcome to example.net')['payload']


def test_user_login(pub):
    pub.cfg['identities'] = {'creation': 'self', 'notify-on-register': False}
    pub.user_class.wipe()
    PasswordAccount.wipe()
    pub.cfg['passwords'] = {'generate': False, 'hashing_algo': 'sha256'}
    pub.write_cfg()
    do_user_registration(pub)

    # wrong password
    app = get_app(pub)
    resp = app.get('/login/')
    resp.forms[0]['username'] = 'foo'
    resp.forms[0]['password'] = 'foo'
    resp = resp.forms[0].submit()
    assert 'Invalid credentials' in resp.text

    # correct passwod
    app = get_app(pub)
    resp = app.get('/login/')
    resp.forms[0]['username'] = 'foo'
    resp.forms[0]['password'] = 'bar'
    resp = resp.forms[0].submit()
    assert resp.location == 'http://example.net/'


def test_forgotten(pub, emails):
    pub.cfg['identities'] = {'creation': 'self', 'notify-on-register': False}
    pub.user_class.wipe()
    PasswordAccount.wipe()
    pub.cfg['passwords'] = {'generate': False, 'hashing_algo': 'sha256'}
    pub.write_cfg()
    do_user_registration(pub)
    user_id = pub.user_class.select()[0].id

    app = get_app(pub)
    resp = app.get('/login/')
    assert '/ident/password/forgotten' in resp.text

    resp = app.get('/ident/password/forgotten')
    resp.forms[0]['username'] = 'bar'  # this account doesn't exist
    resp = resp.forms[0].submit()
    assert 'There is no user with that name or it has no email contact.' in resp

    resp = app.get('/ident/password/forgotten')
    resp.forms[0]['username'] = 'foo'  # this account doesn't have an email
    resp = resp.forms[0].submit()
    assert 'There is no user with that name or it has no email contact.' in resp

    user = pub.user_class.get(user_id)
    user.email = 'foo@localhost'
    user.store()

    resp = app.get('/ident/password/forgotten')
    resp.forms[0]['username'] = 'foo'
    resp = resp.forms[0].submit()
    assert 'A token for changing your password has been emailed to you.' in resp.text

    assert emails.get('Change Password Request')
    assert emails.get('Change Password Request')['to'] == 'foo@localhost'
    body = emails.get('Change Password Request')['payload']

    confirm_urls = re.findall(r'http://.*\w', body)
    assert 'a=cfmpw' in confirm_urls[0]
    assert 'a=cxlpw' in confirm_urls[1]

    # cancel request
    resp = app.get(confirm_urls[1])
    assert 'Your request has been cancelled' in resp.text

    resp = app.get(confirm_urls[1])
    assert 'The token you submitted does not exist' in resp.text

    # new forgotten request
    emails.empty()
    resp = app.get('/ident/password/forgotten')
    resp.forms[0]['username'] = 'foo'
    resp = resp.forms[0].submit()
    assert 'A token for changing your password has been emailed to you.' in resp.text

    body = emails.get('Change Password Request')['payload']
    confirm_urls = re.findall(r'http://.*\w', body)
    assert 'a=cfmpw' in confirm_urls[0]
    assert 'a=cxlpw' in confirm_urls[1]

    resp = app.get(confirm_urls[0])
    assert 'New password sent by email' in resp.text
    assert emails.get('Your new password')

    # check new password is working
    new_password = re.findall('password: (.*)\n', emails.get('Your new password')['payload'])[0]
    resp = app.get('/login/')
    resp.forms[0]['username'] = 'foo'
    resp.forms[0]['password'] = new_password
    resp = resp.forms[0].submit()
    assert resp.status_int == 302

    # check forgotten page when user can choose the password
    pub.cfg['passwords'] = {'generate': False, 'can_change': True}
    pub.write_cfg()

    emails.empty()
    resp = app.get('/ident/password/forgotten')
    resp.forms[0]['username'] = 'foo'
    resp = resp.forms[0].submit()
    assert 'A token for changing your password has been emailed to you.' in resp.text

    body = emails.get('Change Password Request')['payload']
    confirm_urls = re.findall(r'http://.*\w', body)
    assert 'a=cfmpw' in confirm_urls[0]
    assert 'a=cxlpw' in confirm_urls[1]

    resp = app.get(confirm_urls[0])
    resp.forms[0]['new_password$pwd1'] = 'foo'
    resp.forms[0]['new_password$pwd2'] = 'foo'
    resp = resp.forms[0].submit()
    assert resp.status_int == 302

    # check new password is working
    resp = app.get('/login/')
    resp.forms[0]['username'] = 'foo'
    resp.forms[0]['password'] = 'foo'
    resp = resp.forms[0].submit()
    assert resp.status_int == 302


def test_self_registration_email_confirmation(pub, emails):
    pub.cfg['identities'] = {'creation': 'self', 'email-confirmation': True, 'email-as-username': True}
    pub.cfg['passwords'] = {
        'generate': False,
        'min_length': 8,
        'count_uppercase': 1,
        'count_lowercase': 1,
        'count_digit': 1,
        'count_special': 1,
    }
    pub.write_cfg()
    resp = get_app(pub).get('/register/')
    resp.form['username'] = 'user@example.net'
    resp.form['password$pwd1'] = '123abcDEF!!'
    resp.form['password$pwd2'] = '123abcDEF!!'
    resp = resp.form.submit('submit')
    assert 'Email sent' in resp.text
    assert PasswordAccount.get('user@example.net').awaiting_confirmation is True
    assert PasswordAccount.get_with_credentials('user@example.net', '123abcDEF!!')
    assert emails.latest_subject == 'Subscription Confirmation'
    url = re.findall(r'\b(http:.*?)\s', emails.get('Subscription Confirmation').email.body, re.DOTALL)[0]
    resp = get_app(pub).get(url)
    assert 'Account Creation Confirmed' in resp.text
    assert PasswordAccount.get('user@example.net').awaiting_confirmation is False
