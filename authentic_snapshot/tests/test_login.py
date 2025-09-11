# authentic2 - versatile identity manager
# Copyright (C) 2010-2019 Entr'ouvert
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

from urllib.parse import quote

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache

from authentic2 import models
from authentic2.apps.authenticators.models import LoginPasswordAuthenticator
from authentic2.user_login_failure import key as login_failure_key
from authentic2.utils.misc import get_authenticators, get_token_login_url

from .utils import assert_event, clear_events, create_user, login, set_service

User = get_user_model()


def test_success(db, app, simple_user):
    login(app, simple_user)
    assert_event('user.login', user=simple_user, session=app.session, how='password-on-https')
    session = app.session
    app.get('/logout/').form.submit()
    assert_event('user.logout', user=simple_user, session=session)


def test_success_email_with_phone_authn_activated(db, app, simple_user, settings, phone_activated_authn):
    login(app, simple_user)
    assert_event('user.login', user=simple_user, session=app.session, how='password-on-https')
    session = app.session
    app.get('/logout/').form.submit()
    assert_event('user.logout', user=simple_user, session=session)


def test_success_phone_authn_nomail_user(db, app, nomail_user, settings, phone_activated_authn):
    nomail_user.attributes.phone = '+33123456789'
    nomail_user.save()
    login(app, nomail_user, login='123456789')
    assert_event('user.login', user=nomail_user, session=app.session, how='password-on-https')
    session = app.session
    app.get('/logout/').form.submit()
    assert_event('user.logout', user=nomail_user, session=session)


def test_success_phone_authn_simple_user(app, phone_user):
    login(app, phone_user)
    assert_event('user.login', user=phone_user, session=app.session, how='password-on-https')
    session = app.session
    app.get('/logout/').form.submit()
    assert_event('user.logout', user=phone_user, session=session)


def test_success_phone_authn_ou_selector(app, phone_user, phone_activated_authn, ou1, ou2):
    phone_activated_authn.include_ou_selector = True
    phone_activated_authn.save()
    phone_user.ou = ou2
    phone_user.attributes.phone = '+33123456789'
    phone_user.save()
    login(app, phone_user, login='123456789', ou=ou2)
    assert_event('user.login', user=phone_user, session=app.session, how='password-on-https')
    session = app.session
    app.get('/logout/').form.submit()
    assert_event('user.logout', user=phone_user, session=session)
    clear_events()

    # no chosen OU, fallback on last chosen ou
    login(app, phone_user, login='123456789')
    assert_event('user.login', user=phone_user, session=app.session, how='password-on-https')
    app.get('/logout/').form.submit()
    clear_events()

    # no chosen OU, fallback on last chosen ou
    login(app, phone_user, login='123456789')
    assert_event('user.login', user=phone_user, session=app.session, how='password-on-https')
    app.get('/logout/').form.submit()
    clear_events()

    # wrong ou, authentication failure
    login(app, phone_user, login='123456789', ou=ou1, fail=True)

    # authentic reconstructs e.164 from local prefix
    # can't know the failed login target for sure here
    assert_event('user.login.failure', username='123456789')


def test_failure(db, app, simple_user):
    response = login(app, simple_user, password='wrong', fail=True)
    assert response.pyquery('title')[0].text.endswith('there are errors in the form')
    assert_event('user.login.failure', user=simple_user, username=simple_user.username)

    login(app, 'noone', password='wrong', fail=True)
    assert_event('user.login.failure', username='noone')


def test_failure_no_means_of_authentication(app, phone_user):
    phone_user.username = None
    phone_user.phone = None
    phone_user.save()

    with pytest.raises(AssertionError):
        login(app, phone_user)
        assert_event('user.login.failure', user=phone_user, username=phone_user.username)

    with pytest.raises(AssertionError):
        login(app, phone_user)
        assert_event('user.login.failure', user=phone_user, username=phone_user.username)


def test_required_username_identifier(db, app, settings, caplog):
    response = app.get('/login/')
    assert not response.pyquery('span.optional')
    assert response.pyquery('label[for="id_username"]').text() == 'Email:'

    phone, dummy = models.Attribute.objects.get_or_create(
        name='phone',
        kind='phone_number',
        defaults={'label': 'Phone'},
    )
    LoginPasswordAuthenticator.objects.update(
        accept_phone_authentication=True,
        phone_identifier_field=phone,
    )
    response = app.get('/login/')
    assert not response.pyquery('span.optional')
    assert response.pyquery('label[for="id_username"]').text() == 'Email or phone number:'

    LoginPasswordAuthenticator.objects.update(accept_email_authentication=False)
    response = app.get('/login/')
    assert not response.pyquery('span.optional')
    assert response.pyquery('label[for="id_username"]').text() == 'Phone number:'

    LoginPasswordAuthenticator.objects.update(accept_phone_authentication=False)
    response = app.get('/login/')
    assert not response.pyquery('span.optional')
    assert response.pyquery('label[for="id_username"]').text() == 'Username:'


def test_login_form_fields_order(db, app, settings, ou1, ou2):
    response = app.get('/login/')
    assert list(key for key in response.form.fields.keys() if key is not None) == [
        'csrfmiddlewaretoken',
        'username',
        'password',
        'login-password-submit',
    ]

    LoginPasswordAuthenticator.objects.update(accept_phone_authentication=True)

    response = app.get('/login/')
    assert list(key for key in response.form.fields.keys() if key is not None) == [
        'csrfmiddlewaretoken',
        'username',
        'password',
        'login-password-submit',
    ]

    authn = get_authenticators()[0]
    authn.remember_me = True
    authn.include_ou_selector = True
    authn.save()

    response = app.get('/login/')
    assert list(key for key in response.form.fields.keys() if key is not None) == [
        'csrfmiddlewaretoken',
        'username',
        'password',
        'ou',
        'remember_me',
        'login-password-submit',
    ]


def test_login_inactive_user(db, app):
    user1 = User.objects.create(username='john.doe')
    user1.set_password('john.doe')
    user1.save()
    user2 = User.objects.create(username='john.doe')
    user2.set_password('john.doe')
    user2.save()

    login(app, user1)
    assert int(app.session['_auth_user_id']) in [user1.id, user2.id]
    app.get('/logout/').form.submit()
    assert '_auth_user_id' not in app.session
    user1.is_active = False
    user1.save()
    login(app, user2)
    assert int(app.session['_auth_user_id']) == user2.id
    app.get('/logout/').form.submit()
    assert '_auth_user_id' not in app.session
    user2.is_active = False
    user2.save()
    with pytest.raises(AssertionError):
        login(app, user1)
    assert '_auth_user_id' not in app.session


def test_show_condition(db, app, settings, caplog):
    response = app.get('/login/')
    assert 'name="login-password-submit"' in response

    LoginPasswordAuthenticator.objects.update(show_condition='False')
    response = app.get('/login/')
    # login form must not be displayed
    assert 'name="login-password-submit"' not in response
    assert len(caplog.records) == 0
    # set a condition with error

    LoginPasswordAuthenticator.objects.update(show_condition='\'admin\' in unknown')
    response = app.get('/login/')
    assert 'name="login-password-submit"' in response
    assert len(caplog.records) == 1


def test_show_condition_service(db, rf, app, settings):
    portal = models.Service.objects.create(pk=1, name='Service', slug='portal')
    service = models.Service.objects.create(pk=2, name='Service', slug='service')
    LoginPasswordAuthenticator.objects.update(show_condition='service_slug == \'portal\'')

    response = app.get('/login/')
    assert 'name="login-password-submit"' not in response

    set_service(app, portal)

    response = app.get('/login/')
    assert 'name="login-password-submit"' in response

    set_service(app, service)

    response = app.get('/login/')
    assert 'name="login-password-submit"' not in response


def test_show_condition_with_headers(db, app, settings):
    settings.A2_AUTH_OIDC_ENABLE = False  # prevent db access by OIDC frontend
    LoginPasswordAuthenticator.objects.update(show_condition='\'X-Entrouvert\' in headers')
    response = app.get('/login/')
    assert 'name="login-password-submit"' not in response
    response = app.get('/login/', headers={'x-entrouvert': '1'})
    assert 'name="login-password-submit"' in response


def test_show_condition_is_for_backoffice(db, app, settings, caplog):
    response = app.get('/login/')
    assert 'name="login-password-submit"' in response

    LoginPasswordAuthenticator.objects.update(show_condition='is_for_backoffice()')
    response = app.get('/login/')
    assert 'name="login-password-submit"' not in response
    assert len(caplog.records) == 0

    response = app.get('/manage/')
    response = response.follow()
    assert 'name="login-password-submit"' in response
    assert len(caplog.records) == 0
    app.reset()

    # combine
    LoginPasswordAuthenticator.objects.update(
        show_condition="is_for_backoffice() and 'X-Entrouvert' in headers"
    )
    response = app.get('/manage/')
    response = response.follow()
    assert 'name="login-password-submit"' not in response
    assert len(caplog.records) == 0
    app.reset()

    response = app.get('/manage/')
    response = response.follow(headers={'x-entrouvert': '1'})
    assert 'name="login-password-submit"' in response
    assert len(caplog.records) == 0
    app.reset()

    # set a condition with error
    settings.AUTHENTICATOR_SHOW_CONDITIONS['is_for_backoffice'] = "'backoffice' in unknown"
    LoginPasswordAuthenticator.objects.update(show_condition='is_for_backoffice()')
    response = app.get('/manage/')
    response = response.follow()
    assert 'name="login-password-submit"' not in response
    assert len(caplog.records) == 1


def test_show_condition_is_for_frontoffice(db, app, settings, caplog):
    response = app.get('/login/')
    assert 'name="login-password-submit"' in response

    LoginPasswordAuthenticator.objects.update(show_condition='is_for_frontoffice()')
    response = app.get('/login/')
    assert 'name="login-password-submit"' in response
    assert len(caplog.records) == 0

    response = app.get('/manage/')
    response = response.follow()
    assert 'name="login-password-submit"' not in response
    assert len(caplog.records) == 0
    app.reset()

    # combine
    LoginPasswordAuthenticator.objects.update(
        show_condition="is_for_frontoffice() and 'X-Entrouvert' in headers"
    )
    response = app.get('/login/')
    assert 'name="login-password-submit"' not in response
    assert len(caplog.records) == 0

    response = app.get('/login/', headers={'X-entrouvert': '1'})
    assert 'name="login-password-submit"' in response
    assert len(caplog.records) == 0

    # set a condition with error
    settings.AUTHENTICATOR_SHOW_CONDITIONS['is_for_frontoffice'] = "'backoffice' not in unknown"
    LoginPasswordAuthenticator.objects.update(show_condition='is_for_frontoffice()')
    response = app.get('/login/')
    assert 'name="login-password-submit"' not in response
    assert len(caplog.records) == 1


def test_registration_url_on_login_page(db, app):
    response = app.get('/login/?next=/whatever')
    assert 'register/?next=/whatever"' in response


def test_redirect_login_to_homepage(db, app, settings, simple_user, superuser):
    settings.A2_LOGIN_REDIRECT_AUTHENTICATED_USERS_TO_HOMEPAGE = True
    login(app, simple_user)
    response = app.get('/login/')
    assert response.status_code == 302


@pytest.mark.parametrize('cache_close_after', (None, 2, 5, 6, 11, 5000))
def test_exponential_backoff(db, app, settings, cache_errors, cache_close_after):
    with cache_errors(cache_close_after):
        LoginPasswordAuthenticator.objects.update(login_exponential_retry_timeout_duration=0)
        response = app.get('/login/')
        response.form.set('username', '')
        response.form.set('password', 'zozo')
        response = response.form.submit('login-password-submit')
        assert response.status_code == 200

        for i in range(10):
            response.form.set('username', 'zozo')
            response.form.set('password', 'zozo')
            response = response.form.submit('login-password-submit')
            assert 'too many login' not in response.text

        LoginPasswordAuthenticator.objects.update(
            login_exponential_retry_timeout_duration=1.0, login_exponential_retry_timeout_min_duration=10.0
        )

        for i in range(10):
            response.form.set('username', 'zozo')
            response.form.set('password', 'zozo')
            response = response.form.submit('login-password-submit')
            if 1.8**i < 10 and not cache_close_after:
                assert 'too many login' not in response.text, '%s' % i
            elif 'too many login' in response.text:
                break
        else:
            if not cache_close_after:
                pytest.fail('login page never showed too many login message')


def test_encoded_utf8_in_next_url(app, db):
    url = '/manage/roles/?search-ou=all&search-text=r%C3%A9dacteur&search-internals=on'
    response = app.get(url)
    response = response.follow()
    needle = 'next=%s' % quote(url)
    assert needle in response.text


def test_session_expire(app, simple_user, freezer):
    freezer.move_to('2018-01-01')
    # Verify session work as usual
    login(app, simple_user)
    response = app.get('/')
    assert simple_user.first_name in response
    freezer.move_to('2018-01-15')
    response = app.get('/')
    assert simple_user.first_name not in response


def test_session_remember_me_ok(app, settings, simple_user, freezer):
    LoginPasswordAuthenticator.objects.update(remember_me=3600 * 24 * 30)
    freezer.move_to('2018-01-01')
    # Verify session are longer
    login(app, simple_user, remember_me=True)

    response = app.get('/')
    assert simple_user.first_name in response

    # less than 30 days, session is still alive
    freezer.move_to('2018-01-30')
    response = app.get('/')
    assert simple_user.first_name in response


def test_session_remember_me_nok(app, settings, simple_user, freezer):
    LoginPasswordAuthenticator.objects.update(remember_me=3600 * 24 * 30)
    freezer.move_to('2018-01-01')
    # Verify session are longer
    login(app, simple_user, remember_me=True)

    response = app.get('/')
    assert simple_user.first_name in response

    # more than 30 days, session is dead
    freezer.move_to('2018-01-31')
    response = app.get('/')
    assert simple_user.first_name not in response


def test_ou_selector(app, settings, simple_user, ou1, ou2, user_ou1, role_ou1):
    LoginPasswordAuthenticator.objects.update(include_ou_selector=True)
    response = app.get('/login/')
    # Check selector is here and there are no errors
    assert not response.pyquery('.errorlist')
    assert response.pyquery.find('select#id_ou')
    assert len(response.pyquery.find('select#id_ou optgroup')) == 0
    assert {elt.text for elt in response.pyquery.find('select#id_ou option')} == {
        'Default organizational unit',
        'OU1',
        'OU2',
        '---------',
    }
    # Check selector is required
    response.form.set('username', simple_user.username)
    response.form.set('password', simple_user.clear_password)
    response = response.form.submit(name='login-password-submit')
    assert response.pyquery('.widget-with-error')
    # Check login to the wrong ou do not work
    response.form.set('password', simple_user.clear_password)
    response.form.set('ou', str(ou1.pk))
    response = response.form.submit(name='login-password-submit')
    assert response.pyquery('.errornotice')
    assert '_auth_user_id' not in app.session
    # Check login to the proper ou works
    response.form.set('password', simple_user.clear_password)
    response.form.set('ou', str(simple_user.ou.pk))
    response = response.form.submit(name='login-password-submit').follow()
    assert '_auth_user_id' in app.session
    response = response.click('Logout').maybe_follow()
    assert '_auth_user_id' not in app.session
    assert app.cookies['preferred-ous'] == str(simple_user.ou.pk)

    # Check last ou is preselected and shown first
    response = app.get('/login/')
    assert response.pyquery.find('select#id_ou')
    assert len(response.pyquery.find('select#id_ou optgroup')) == 2
    assert {elt.text for elt in response.pyquery.find('select#id_ou option')} == {
        'Default organizational unit',
        'OU1',
        'OU2',
        '---------',
    }
    assert response.pyquery.find('select#id_ou option[selected]')[0].text == 'Default organizational unit'

    # Create a service
    service = models.Service.objects.create(name='Service', slug='service', ou=ou1)
    response = app.get('/login/')
    assert response.pyquery.find('select#id_ou option[selected]')[0].text == 'Default organizational unit'

    set_service(app, service)
    # service is specified but not access-control is defined, default for user is selected
    response = app.get('/login/')
    assert response.pyquery.find('select#id_ou option[selected]')[0].text == 'Default organizational unit'

    # service is specified, access control is defined but role is empty, default for user is selected
    service.authorized_roles.through.objects.create(service=service, role=role_ou1)
    response = app.get('/login/')
    assert response.pyquery.find('select#id_ou option[selected]')[0].text == 'Default organizational unit'

    # user is added to role_ou1, default for user is still selected
    user_ou1.roles.add(role_ou1)
    response = app.get('/login/')
    assert response.pyquery.find('select#id_ou option[selected]')[0].text == 'Default organizational unit'

    # Clear cookies, OU1 is selected
    app.cookiejar.clear()
    set_service(app, service)
    response = app.get('/login/')
    assert response.pyquery.find('select#id_ou option[selected]')[0].text == 'OU1'

    # if we change the user's ou, then default selected OU changes
    user_ou1.ou = ou2
    user_ou1.save()
    response = app.get('/login/')
    assert response.pyquery.find('select#id_ou option[selected]')[0].text == 'OU2'


def test_login_test_cookie(app, simple_user):
    resp = app.get('/login/')
    # simulate browser blocking cooking by clearing the cookiejar
    app.cookiejar.clear()
    resp.form.set('username', simple_user.username)
    resp.form.set('password', simple_user.clear_password)
    resp = resp.form.submit(name='login-password-submit')
    # CSRF and test cookie checks failed
    assert 'Cookies are disabled' in resp


def test_login_csrf_no_middleware(app, simple_user, settings):
    resp = app.get('/login/')
    # remove all middleware including csrf validation
    settings.MIDDLEWARE = []
    resp.form.set('username', simple_user.username)
    resp.form.set('password', simple_user.clear_password)
    # invalidating csrf field does not raise an error
    resp.form.set('csrfmiddlewaretoken', 'doesn\'tcare')
    resp = resp.form.submit(name='login-password-submit')
    assert resp.location == '/'
    resp = resp.follow()
    assert resp.pyquery('title')[0].text == 'Authentic2 - testserver - Home'
    assert 'out of date' not in resp


def test_login_error_messages(app, settings, simple_user):
    settings.A2_USER_CAN_RESET_PASSWORD = True
    resp = app.get('/login/')
    resp.form.set('username', 'x')
    resp.form.set('password', 'y')
    resp = resp.form.submit(name='login-password-submit')
    assert 'Incorrect login or password.' in resp
    assert 'use the forgotten password link below' in resp
    assert 'or create an account.' in resp

    settings.A2_USER_CAN_RESET_PASSWORD = False
    LoginPasswordAuthenticator.objects.update(registration_open=False)
    resp.form.set('username', 'x')
    resp.form.set('password', 'y')
    resp = resp.form.submit(name='login-password-submit')
    assert 'Incorrect login or password.' in resp
    assert 'use the forgotten password link below' not in resp
    assert 'or create an account.' not in resp

    settings.A2_USER_CAN_RESET_PASSWORD = True
    resp.form.set('username', 'x')
    resp.form.set('password', 'y')
    resp = resp.form.submit(name='login-password-submit')
    assert 'Incorrect login or password.' in resp
    assert 'use the forgotten password link below' in resp
    assert 'or create an account.' not in resp

    settings.A2_USER_CAN_RESET_PASSWORD = False
    LoginPasswordAuthenticator.objects.update(registration_open=True)
    resp.form.set('username', 'x')
    resp.form.set('password', 'y')
    resp = resp.form.submit(name='login-password-submit')
    assert 'Incorrect login or password.' in resp
    assert 'use the forgotten password link below' not in resp
    assert 'or create an account.' in resp


def test_login_failure_cache_ok(app, settings, db, caplog):
    user = create_user(username='faillogin', password='randompassword')
    settings.A2_LOGIN_FAILURE_COUNT_BEFORE_WARNING = 3
    for i in range(1, 3 + 2):
        caplog.clear()
        resp = app.get('/login/')
        resp.form.set('username', user.username)
        resp.form.set('password', 'nor a password')
        resp = resp.form.submit(name='login-password-submit')
        assert len(caplog.records) > 0
        assert caplog.records[0].msg == 'user %s failed to login'
        assert caplog.records[0].levelname == 'INFO'
        if i < settings.A2_LOGIN_FAILURE_COUNT_BEFORE_WARNING:
            assert len(caplog.records) == 1
            assert 'Incorrect login or password.' in resp
        else:
            assert len(caplog.records) == 2
            assert caplog.records[1].msg == 'user %s failed to login more than %d times in a row'
            assert caplog.records[1].levelname == 'WARNING'


def test_login_failure_cache_down(app, simple_user, memcache_down):
    for dummy in range(3):
        resp = app.get('/login/')
        resp.form.set('username', simple_user.username)
        resp.form.set('password', 'toto')
        resp = resp.form.submit(name='login-password-submit')
        assert 'Incorrect login or password.' in resp
        assert cache.get(login_failure_key(simple_user.username)) is None


@pytest.mark.parametrize('cache_close_after', (2, 5, 6, 11, 5000))
def test_login_failure_cache_errors(app, simple_user, cache_errors, cache_close_after):
    with cache_errors(cache_close_after):
        for dummy in range(3):
            resp = app.get('/login/')
            resp.form.set('username', simple_user.username)
            resp.form.set('password', 'toto')
            resp = resp.form.submit(name='login-password-submit')
            assert 'Incorrect login or password.' in resp
            assert cache.get(login_failure_key(simple_user.username)) is None


def test_login_opened_session_cookie(db, app, settings, simple_user):
    settings.A2_OPENED_SESSION_COOKIE_DOMAIN = 'testserver.local'
    app.cookiejar.clear()
    login(app, simple_user)
    assert 'A2_OPENED_SESSION' in app.cookies

    app.cookiejar.clear()
    login(app, simple_user)
    assert 'A2_OPENED_SESSION' in app.cookies
    for cookie in app.cookiejar:
        if cookie.name == 'A2_OPENED_SESSION':
            assert cookie.secure is True


def test_null_characters(app, db):
    response = app.get('/login/')
    response.form.set('username', 'xx\0xx')
    response.form.set('password', 'whatever')
    response = response.form.submit(name='login-password-submit', status=400)
    assert response.text == 'null character in form data'


def test_token_login(app, simple_user):
    url = get_token_login_url(simple_user)

    resp = app.get(url).follow()
    assert simple_user.first_name in resp.text
    assert app.session['_auth_user_id'] == str(simple_user.pk)
    assert_event('user.login', user=simple_user, session=app.session, how='token')


def test_button_description(app, db):
    LoginPasswordAuthenticator.objects.update(button_description='This is a test.')

    response = app.get('/login/')
    assert 'This is a test.' in response.text


def test_password_authenticator_data_migration(migration, settings):
    app = 'authenticators'
    migrate_from = [(app, '0002_loginpasswordauthenticator')]
    migrate_to = [(app, '0003_auto_20220413_1504')]

    old_apps = migration.before(migrate_from)
    LoginPasswordAuthenticator = old_apps.get_model(app, 'LoginPasswordAuthenticator')

    settings.AUTH_FRONTENDS_KWARGS = {
        'password': {'priority': -1, 'show_condition': "'backoffice' not in login_hint"}
    }
    settings.A2_LOGIN_FORM_OU_SELECTOR = True
    settings.A2_AUTH_PASSWORD_ENABLE = False
    settings.A2_USER_REMEMBER_ME = 42

    new_apps = migration.apply(migrate_to)
    LoginPasswordAuthenticator = new_apps.get_model(app, 'LoginPasswordAuthenticator')
    authenticator = LoginPasswordAuthenticator.objects.get()
    assert authenticator.slug == 'password-authenticator'
    assert authenticator.order == -1
    assert authenticator.show_condition == "'backoffice' not in login_hint"
    assert authenticator.enabled is False
    assert authenticator.remember_me == 42
    assert authenticator.include_ou_selector is True


def test_password_authenticator_data_migration_new_settings(migration, settings):
    app = 'authenticators'
    migrate_from = [(app, '0008_new_password_settings_fields')]
    migrate_to = [(app, '0009_migrate_new_password_settings')]

    old_apps = migration.before(migrate_from)
    LoginPasswordAuthenticator = old_apps.get_model(app, 'LoginPasswordAuthenticator')

    settings.A2_PASSWORD_POLICY_MIN_LENGTH = 10
    settings.A2_PASSWORD_POLICY_REGEX = '^.*ok.*$'
    settings.A2_PASSWORD_POLICY_REGEX_ERROR_MSG = 'not ok'
    settings.A2_LOGIN_EXPONENTIAL_RETRY_TIMEOUT_DURATION = 10.5
    settings.A2_LOGIN_EXPONENTIAL_RETRY_TIMEOUT_FACTOR = 1
    settings.A2_LOGIN_EXPONENTIAL_RETRY_TIMEOUT_MAX_DURATION = 100
    settings.A2_LOGIN_EXPONENTIAL_RETRY_TIMEOUT_MIN_DURATION = 200
    settings.A2_EMAILS_IP_RATELIMIT = '42/h'
    settings.A2_SMS_IP_RATELIMIT = '43/h'
    settings.A2_EMAILS_ADDRESS_RATELIMIT = '44/h'
    settings.A2_SMS_NUMBER_RATELIMIT = '45/h'

    new_apps = migration.apply(migrate_to)
    LoginPasswordAuthenticator = new_apps.get_model(app, 'LoginPasswordAuthenticator')
    authenticator = LoginPasswordAuthenticator.objects.get()
    assert authenticator.password_min_length == 10
    assert authenticator.password_regex == '^.*ok.*$'
    assert authenticator.password_regex_error_msg == 'not ok'
    assert authenticator.login_exponential_retry_timeout_duration == 10.5
    assert authenticator.login_exponential_retry_timeout_factor == 1
    assert authenticator.login_exponential_retry_timeout_max_duration == 100
    assert authenticator.login_exponential_retry_timeout_min_duration == 200
    assert authenticator.emails_ip_ratelimit == '42/h'
    assert authenticator.sms_ip_ratelimit == '43/h'
    assert authenticator.emails_address_ratelimit == '44/h'
    assert authenticator.sms_number_ratelimit == '45/h'


def test_password_authenticator_data_migration_new_settings_invalid(migration, settings):
    app = 'authenticators'
    migrate_from = [(app, '0008_new_password_settings_fields')]
    migrate_to = [(app, '0009_migrate_new_password_settings')]

    old_apps = migration.before(migrate_from)
    LoginPasswordAuthenticator = old_apps.get_model(app, 'LoginPasswordAuthenticator')

    settings.A2_PASSWORD_POLICY_MIN_LENGTH = 'abc'
    settings.A2_PASSWORD_POLICY_REGEX = None
    settings.A2_PASSWORD_POLICY_REGEX_ERROR_MSG = 42
    settings.A2_LOGIN_EXPONENTIAL_RETRY_TIMEOUT_DURATION = None
    settings.A2_LOGIN_EXPONENTIAL_RETRY_TIMEOUT_MAX_DURATION = 10.5
    settings.A2_EMAILS_IP_RATELIMIT = None
    settings.A2_SMS_IP_RATELIMIT = 42

    new_apps = migration.apply(migrate_to)
    LoginPasswordAuthenticator = new_apps.get_model(app, 'LoginPasswordAuthenticator')
    authenticator = LoginPasswordAuthenticator.objects.get()
    assert authenticator.password_min_length == 8
    assert authenticator.password_regex == ''
    assert authenticator.password_regex_error_msg == ''
    assert authenticator.login_exponential_retry_timeout_duration == 1
    assert authenticator.login_exponential_retry_timeout_max_duration == 10
    assert authenticator.emails_ip_ratelimit == '10/h'
    assert authenticator.sms_ip_ratelimit == '10/h'


@pytest.mark.parametrize('email,phone', [(True, True), (True, False), (False, True)])
def test_password_authenticator_migration_accept_authentication_settings(migration, settings, email, phone):
    app = 'authenticators'
    migrate_from = [(app, '0010_auto_20230614_1017')]
    migrate_to = [(app, '0011_migrate_a2_accept_authentication_settings')]

    migration.before(migrate_from)

    settings.A2_ACCEPT_EMAIL_AUTHENTICATION = email
    settings.A2_ACCEPT_PHONE_AUTHENTICATION = phone

    new_apps = migration.apply(migrate_to)
    LoginPasswordAuthenticator = new_apps.get_model(app, 'LoginPasswordAuthenticator')
    authenticator = LoginPasswordAuthenticator.objects.get()

    assert authenticator.accept_email_authentication == email
    assert authenticator.accept_phone_authentication == phone


@pytest.mark.parametrize('strength,expected_strength', [(None, 3), ('invalid', 3), (1, 3), (4, 4), (42, 4)])
def test_password_authenticator_data_migration_min_password_strength(
    migration, settings, strength, expected_strength
):
    app = 'authenticators'
    migrate_from = [
        (app, '0012_loginpasswordauthenticator_min_password_strength'),
        ('a2_rbac', '0036_delete_roleattribute'),
    ]
    migrate_to = [(app, '0013_migrate_min_password_strength')]

    old_apps = migration.before(migrate_from)
    OU = old_apps.get_model('a2_rbac', 'OrganizationalUnit')

    settings.A2_PASSWORD_POLICY_MIN_STRENGTH = strength

    OU.objects.create(name='OU1', slug='ou1', min_password_strength=2)
    OU.objects.create(name='OU2', slug='ou2', min_password_strength=None)
    OU.objects.create(name='OU3', slug='ou3', min_password_strength=3)

    new_apps = migration.apply(migrate_to)
    LoginPasswordAuthenticator = new_apps.get_model(app, 'LoginPasswordAuthenticator')
    authenticator = LoginPasswordAuthenticator.objects.get()
    assert authenticator.min_password_strength == expected_strength


def test_password_authenticator_data_migration_min_password_strength_zero(migration, settings):
    app = 'authenticators'
    migrate_from = [
        (app, '0012_loginpasswordauthenticator_min_password_strength'),
        ('a2_rbac', '0036_delete_roleattribute'),
    ]
    migrate_to = [(app, '0013_migrate_min_password_strength')]

    old_apps = migration.before(migrate_from)
    OU = old_apps.get_model('a2_rbac', 'OrganizationalUnit')

    OU.objects.create(name='OU1', slug='ou1', min_password_strength=0)

    new_apps = migration.apply(migrate_to)
    LoginPasswordAuthenticator = new_apps.get_model(app, 'LoginPasswordAuthenticator')
    authenticator = LoginPasswordAuthenticator.objects.get()
    assert authenticator.min_password_strength == 0
