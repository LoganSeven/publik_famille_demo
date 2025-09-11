# authentic2 - authentic2 authentication for FranceConnect
# Copyright (C) 2020 Entr'ouvert
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

import datetime
import json
import logging
import re
import urllib.parse

import pytest
import responses
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse
from django.utils.timezone import now
from jwcrypto import jwk, jwt

from authentic2.a2_rbac.models import OrganizationalUnit as OU
from authentic2.a2_rbac.utils import get_default_ou
from authentic2.apps.authenticators.models import LoginPasswordAuthenticator
from authentic2.apps.journal.models import Event
from authentic2.custom_user.models import DeletedUser
from authentic2.models import Attribute, AttributeValue
from authentic2_auth_fc import models
from authentic2_auth_fc.backends import FcBackend

from ..utils import assert_event, decode_cookie, get_link_from_mail, login, set_service

User = get_user_model()


def path(url):
    return urllib.parse.urlparse(url).path


def test_not_configured(app, db):
    app.get('/fc/callback/', status=404)


def test_disabled(app, franceconnect):
    models.FcAuthenticator.objects.update(enabled=False)
    app.get('/fc/callback/', status=404)


def test_fc_url_on_login(app, franceconnect):
    url = reverse('fc-login-or-link')
    response = app.get(url, status=302)
    assert response.location.startswith(franceconnect.url_prefix)
    assert 'fc-state' in app.cookies


def test_retry_authorization_if_state_is_lost(settings, app, franceconnect, hooks):
    response = app.get('/fc/callback/?next=/idp/', status=302)
    # clear fc-state cookie
    app.cookiejar.clear()
    response = franceconnect.handle_authorization(app, response.location, status=302)
    assert response.location.startswith(franceconnect.url_prefix)


@responses.activate
def test_remote_jwkset_retrieval(settings, authenticator):
    prod_jwk1 = jwk.JWK.generate(kty='RSA', size=1024, kid='abc')
    prod_jwk2 = jwk.JWK.generate(kty='EC', size=1024, kid='def')
    test_jwk1 = jwk.JWK.generate(kty='RSA', size=1024, kid='ghi')
    test_jwk2 = jwk.JWK.generate(kty='EC', size=1024, kid='jkl')

    prod_jwks = jwk.JWKSet()
    prod_jwks.add(prod_jwk1)
    prod_jwks.add(prod_jwk2)

    test_jwks = jwk.JWKSet()
    test_jwks.add(test_jwk1)
    test_jwks.add(test_jwk2)

    def prod_jwks_response(request):
        return (200, {'Content-Type': 'application/json'}, json.dumps(prod_jwks.export(as_dict=True)))

    def test_jwks_response(request):
        return (200, {'Content-Type': 'application/json'}, json.dumps(test_jwks.export(as_dict=True)))

    with responses.RequestsMock() as rsps:
        rsps.add_callback(
            'GET',
            url='https://oidc.franceconnect.gouv.fr/api/v2/jwks',
            callback=prod_jwks_response,
        )
        rsps.add_callback(
            'GET',
            url='https://fcp-low.integ01.dev-franceconnect.fr/api/v2/jwks',
            callback=test_jwks_response,
        )

        authenticator.platform = 'test'
        authenticator.version = '2'
        authenticator.clean()
        authenticator.save()

        assert not authenticator.jwkset.get_key('abc')
        assert not authenticator.jwkset.get_key('def')
        assert authenticator.jwkset.get_key('ghi')
        assert authenticator.jwkset.get_key('jkl')

        authenticator.platform = 'prod'
        authenticator.clean()
        authenticator.save()

        assert authenticator.jwkset.get_key('abc')
        assert authenticator.jwkset.get_key('def')
        assert not authenticator.jwkset.get_key('ghi')
        assert not authenticator.jwkset.get_key('jkl')

    # version 1 saves do not trigger jwks updates
    authenticator.platform = 'test'
    authenticator.version = '1'
    authenticator.clean()
    authenticator.save()
    authenticator.platform = 'prod'
    authenticator.clean()
    authenticator.save()


def test_erroneous_authorization_fc_provider_issued_error(
    settings,
    app,
    franceconnect,
    authenticator,
    service,
    caplog,
):
    # test direct creation
    set_service(app, service)
    response = app.get('/login/?next=/accounts/')
    response = response.click(href='callback')

    assert User.objects.count() == 0
    assert Event.objects.which_references(service).count() == 0
    response = franceconnect.handle_authorization(
        app,
        response.location,
        status=302,
        error='invalid_request',
        error_description='Something went wrong et cætera.',
    )
    assert User.objects.count() == 0
    assert response.location == '/accounts/'
    assert re.match(
        r'WARNING.*token request failed, "invalid_request": "Something went wrong et cætera."', caplog.text
    )
    response = response.follow()
    assert response.location == '/login/?next=/accounts/'
    response = response.maybe_follow()
    assert (
        response.pyquery('ul.messages li.warning').text()
        == 'Unable to connect to FranceConnect: invalid request, contact an administrator (invalid_request).'
    )


def test_erroneous_authorization_fc_provider_issued_error_no_next_url(
    settings,
    app,
    franceconnect,
    authenticator,
    service,
):
    set_service(app, service)
    response = app.get('/login/')
    response = response.click(href='callback')

    response = franceconnect.handle_authorization(
        app,
        response.location,
        status=302,
        error='invalid_request',
        error_description='Something went wrong et cætera.',
    )
    assert response.location == '/'
    response = response.follow()
    assert response.location == '/login/?next=/'
    response = response.maybe_follow()


@responses.activate
def test_erroneous_authorization_if_issuer_values_mismatch(
    settings,
    app,
    franceconnect,
    authenticator,
    service,
    caplog,
    fc_version,
):
    if fc_version == '1':
        pytest.skip('v1')

    responses.add_callback(
        'GET',
        url='https://oidc.franceconnect.gouv.fr/api/v2/jwks',
        callback=franceconnect.jwkset_response,
    )
    responses.add_callback(
        'GET',
        url='https://oidc.franceconnect.gouv.fr/api/v2/userinfo',
        callback=franceconnect.user_info_response,
    )
    responses.add_callback(
        'POST',
        url='https://oidc.franceconnect.gouv.fr/api/v2/token',
        callback=franceconnect.access_token_response,
    )

    authenticator.platform = 'prod'
    authenticator.version = '2'
    if authenticator.supports_multiaccount:
        with pytest.raises(
            ValidationError, match=r'Multiaccount is activated yet clashes with email uniqueness.'
        ):
            authenticator.clean()
        return

    authenticator.clean()
    authenticator.save()

    response = app.get('/login/?next=/idp/')
    response = response.click(href='callback')
    response = franceconnect.handle_authorization(
        app,
        response.location,
        status=302,
        iss='https://fcp.integ01.dev-franceconnect.fr/api/v1/authorize',
    )

    assert User.objects.count() == 0
    assert re.match(r'WARNING.*authorization failed issuer authz callback param is wrong', caplog.text)

    caplog.clear()

    response = app.get('/login/?next=/idp/')
    response = response.click(href='callback')
    response = franceconnect.handle_authorization(
        app, response.location, status=302, iss='https://oidc.franceconnect.gouv.fr/api/v2'
    )
    assert User.objects.count() == 1
    assert not 'authorization failed issuer authz callback param is wrong' in caplog.text

    authenticator.platform = 'test'
    authenticator.version = '1'
    authenticator.clean()
    authenticator.save()


def test_login_with_condition(settings, app, franceconnect):
    # open the page first time so session cookie can be set
    response = app.get('/login/')
    assert 'fc-button' in response

    # make sure FC block is first
    assert response.text.index('div id="fc-button"') < response.text.index('name="login-password-submit"')

    models.FcAuthenticator.objects.update(show_condition='remote_addr==\'0.0.0.0\'')
    response = app.get('/login/')
    assert 'fc-button' not in response


def test_login_autorun(settings, app, franceconnect):
    # hide password block
    LoginPasswordAuthenticator.objects.update_or_create(
        slug='password-authenticator', defaults={'enabled': False}
    )
    response = app.get('/login/')
    assert response.location.startswith(franceconnect.url_prefix)


def test_login_username_autofocus(settings, app, franceconnect):
    response = app.get('/login/')
    assert response.text.index('div id="fc-button"') < response.text.index('name="login-password-submit"')
    assert response.pyquery('#id_username').attr.autofocus is None

    models.FcAuthenticator.objects.update(order=3)
    response = app.get('/login/')
    assert response.text.index('div id="fc-button"') > response.text.index('name="login-password-submit"')
    assert response.pyquery('#id_username').attr.autofocus is None


def test_create_buggy_state(settings, app, franceconnect, hooks, service, mailoutbox):
    set_service(app, service)
    response = app.get('/login/?next=/idp/')
    response = response.click(href='callback')

    response = franceconnect.handle_authorization(app, response.location, status=302, buggy_state=True)
    assert User.objects.count() == 0
    assert Event.objects.which_references(service).count() == 0
    assert response.location == '/'
    response = response.follow()
    assert response.location == '/login/?next=/'
    response = response.follow()
    assert response.pyquery('.messages .error')[0].text == 'Unable to connect to FranceConnect.'


def test_create(settings, app, franceconnect, hooks, service, mailoutbox):
    # test direct creation
    set_service(app, service)
    response = app.get('/login/?next=/idp/')
    response = response.click(href='callback')

    assert User.objects.count() == 0
    assert Event.objects.which_references(service).count() == 0
    response = franceconnect.handle_authorization(app, response.location, status=302)
    assert 'fc-state' not in app.cookies
    assert User.objects.count() == 1
    user = User.objects.get()
    # check login for service=portail and user registration were registered
    assert Event.objects.which_references(service).count() == 2
    assert (
        Event.objects.filter(type__name='user.registration', user=user).which_references(service).count() == 1
    )

    # check registration email
    assert len(mailoutbox) == 1
    assert mailoutbox[0].subject == 'Account creation using FranceConnect'
    for body in (mailoutbox[0].body, mailoutbox[0].alternatives[0][0]):
        assert 'Hi Ÿuñe Frédérique,' in body
        assert 'You have just created an account using FranceConnect.' in body
        assert 'https://testserver/login/' in body

    assert user.verified_attributes.first_name == 'Ÿuñe'
    assert user.verified_attributes.last_name == 'Frédérique'
    first_name = Attribute.objects.get(name='first_name')
    last_name = Attribute.objects.get(name='last_name')
    first_name_value = AttributeValue.objects.with_owner(user).get(attribute=first_name)
    last_name_value = AttributeValue.objects.with_owner(user).get(attribute=last_name)
    assert first_name_value.last_verified_on
    assert last_name_value.last_verified_on
    assert path(response.location) == '/idp/'
    assert hooks.event[1]['kwargs']['name'] == 'login'
    assert hooks.event[1]['kwargs']['service'] == service
    # we must be connected
    assert app.session['_auth_user_id']
    assert app.session.get_expire_at_browser_close()
    assert models.FcAccount.objects.count() == 1
    assert AttributeValue.objects.with_owner(user).filter(verified=True)

    # test unlink cancel case
    response = app.get('/accounts/')
    response = response.click('Delete link')
    assert len(response.pyquery('[name=cancel][formnovalidate]')) == 1
    response = response.form.submit(name='cancel')
    response = response.follow()

    # test unlink submit case
    response = app.get('/accounts/')
    response = response.click('Delete link')
    response.form.set('new_password1', 'ikKL1234')
    response.form.set('new_password2', 'ikKL1234')
    response = response.form.submit(name='unlink')
    assert models.FcAccount.objects.count() == 0
    assert not AttributeValue.objects.with_owner(user).filter(verified=True)
    response = franceconnect.handle_logout(app, response.location)
    assert path(response.location) == '/accounts/'
    response = response.follow()
    assert 'Your account link to FranceConnect has been deleted' in response


def test_login_existing_accounts_already_linked(
    settings, app, franceconnect, authenticator, hooks, service, mailoutbox
):
    for i in range(3):
        user = User.objects.create(
            first_name=f'John-{i}',
            last_name=f'Doe-{i}',
        )
        models.FcAccount.objects.create(user=user, sub=franceconnect.sub)

    # test direct creation
    set_service(app, service)
    response = app.get('/login/?next=/idp/')
    response = response.click(href='callback')

    assert User.objects.count() == 3
    assert Event.objects.which_references(service).count() == 0
    if authenticator.supports_multiaccount:
        response = franceconnect.handle_authorization(app, response.location, status=200)
        selected_user = User.objects.get(first_name='John-1')
        response.form.set('account', selected_user.fc_account.id)
        response = response.form.submit(name='select-account')
        assert response.location == '/'
        assert app.session['_auth_user_id'] == str(selected_user.id)
        response = response.follow()
        assert response.pyquery('ul.user-info li.ui-name').text() == 'Ÿuñe Frédérique'
    else:
        response = franceconnect.handle_authorization(app, response.location, status=302)
        assert 'fc-state' not in app.cookies
        assert User.objects.count() == 3
        assert app.session['_auth_user_id'] == str(user.id)


def test_login_existing_accounts_already_linked_choose_creation(
    settings, app, fc_multiaccount_only, authenticator, hooks, service, mailoutbox
):
    for i in range(3):
        user = User.objects.create(
            first_name=f'John-{i}',
            last_name=f'Doe-{i}',
        )
        models.FcAccount.objects.create(user=user, sub=fc_multiaccount_only.sub)

    user_count = User.objects.count()
    existing_user_ids = User.objects.all().values_list('id', flat=True)

    # test direct creation
    set_service(app, service)
    response = app.get('/login/?next=/idp/')
    response = response.click(href='callback')

    assert User.objects.count() == 3
    assert Event.objects.which_references(service).count() == 0
    response = fc_multiaccount_only.handle_authorization(app, response.location, status=200)
    response.form.set('account', '-1')
    response = response.form.submit(name='select-account')
    assert response.location == '/'
    assert app.session['_auth_user_id'] and app.session['_auth_user_id'] not in list(existing_user_ids)
    response = response.follow()
    assert response.pyquery('ul.user-info li.ui-name').text() == 'Ÿuñe Frédérique'
    assert models.FcAccount.objects.count() == 3 + 1
    assert User.objects.count() == user_count + 1


def test_login_existing_accounts_already_linked_click_cancel(
    settings, app, fc_multiaccount_only, authenticator, hooks, service, mailoutbox
):
    for i in range(3):
        user = User.objects.create(
            first_name=f'John-{i}',
            last_name=f'Doe-{i}',
        )
        models.FcAccount.objects.create(user=user, sub=fc_multiaccount_only.sub)

    user_count = User.objects.count()

    # test direct creation
    set_service(app, service)
    response = app.get('/login/?next=/idp/')
    response = response.click(href='callback')

    assert User.objects.count() == 3
    assert Event.objects.which_references(service).count() == 0
    response = fc_multiaccount_only.handle_authorization(app, response.location, status=200)
    response = response.form.submit(name='cancel')
    assert response.location == '/'
    assert '_auth_user_id' not in app.session
    response = response.follow().maybe_follow()
    assert not response.pyquery('ul.user-info li.ui-name')
    assert models.FcAccount.objects.count() == 3
    assert User.objects.count() == user_count


def test_login_forging_account_selection_form(
    settings, app, fc_multiaccount_only, authenticator, hooks, service, mailoutbox, admin
):
    for i in range(3):
        user = User.objects.create(
            first_name=f'John-{i}',
            last_name=f'Doe-{i}',
        )
        models.FcAccount.objects.create(user=user, sub=fc_multiaccount_only.sub)

    admin_account = models.FcAccount.objects.create(user=admin, sub='xyz')

    user_count = User.objects.count()

    # test direct creation
    set_service(app, service)
    response = app.get('/login/?next=/idp/')
    response = response.click(href='callback')

    response = fc_multiaccount_only.handle_authorization(app, response.location, status=200)

    token = response.pyquery('input[name="csrfmiddlewaretoken"]')[0].value
    data = {
        'csrfmiddlewaretoken': [token],
        'sub': ['xyz'],
        'account': [str(admin_account.id)],
    }
    response = app.post(reverse('fc-login-or-link'), data)
    response.follow()
    assert models.FcAccount.objects.count() == 4
    assert User.objects.count() == user_count
    assert '_auth_user_id' not in app.session


def test_create_no_unicode_collision(settings, app, franceconnect, hooks, service):
    settings.A2_EMAIL_IS_UNIQUE = True
    set_service(app, service)
    response = app.get('/login/?next=/idp/')
    response = response.click(href='callback')

    User.objects.create(
        first_name='Mike',
        last_name='Doe',
        username='mike',
        email='mike@ixample.org',
    )
    franceconnect.user_info['email'] = 'mike@ıxample.org'  # dot-less i 'ı' U+0131
    response = franceconnect.handle_authorization(app, response.location, status=302)
    assert User.objects.count() == 2
    assert len(User.objects.filter(email='mike@ıxample.org')) == 1


def test_create_expired(settings, app, franceconnect, hooks):
    # test direct creation failure on an expired id_token
    franceconnect.exp = now() - datetime.timedelta(seconds=30)

    response = app.get('/login/?next=/idp/')
    response = response.click(href='callback')

    assert User.objects.count() == 0
    response = franceconnect.handle_authorization(app, response.location, status=302)
    assert User.objects.count() == 0


class TestLinkByEmail:
    @pytest.fixture
    def franceconnect(self, franceconnect):
        franceconnect.callback_params = {'next': '/accounts/'}
        return franceconnect

    def test_enabled(self, settings, app, franceconnect, authenticator, caplog):
        authenticator.link_by_email = True
        authenticator.save()

        user = User(email='john.doe@example.com', first_name='John', last_name='Doe', ou=get_default_ou())
        user.set_password('toto')
        user.save()
        franceconnect.user_info['email'] = user.email

        assert User.objects.count() == 1
        franceconnect.login_with_fc_fixed_params(app)
        assert User.objects.count() == 1
        assert '_auth_user_id' in app.session

    def test_disabled(self, settings, app, franceconnect, authenticator, caplog):
        authenticator.link_by_email = False
        authenticator.save()

        user = User(email='john.doe@example.com', first_name='John', last_name='Doe', ou=get_default_ou())
        user.set_password('toto')
        user.save()
        franceconnect.user_info['email'] = user.email

        assert User.objects.count() == 1
        response = franceconnect.login_with_fc_fixed_params(app)
        assert User.objects.count() == 1
        assert '_auth_user_id' not in app.session

        # no login, so we must have produced a logout request toward FC
        response = franceconnect.handle_logout(app, response.location)
        response = response.maybe_follow()
        assert 'Your FranceConnect email address' in response.pyquery('.messages .warning').text()


def test_link_after_login_with_password(app, franceconnect, simple_user):
    assert models.FcAccount.objects.count() == 0

    response = login(app, simple_user, path='/accounts/')
    response = response.click(href='/fc/callback/')

    franceconnect.callback_params = {'next': '/accounts/'}
    response = franceconnect.handle_authorization(app, response.location, status=302)
    assert models.FcAccount.objects.count() == 1
    response = response.follow()
    assert response.pyquery('.fc').text() == 'Linked FranceConnect identity:\nŸuñe Frédérique Delete link'


def test_unlink_after_login_with_password(app, franceconnect, simple_user):
    models.FcAccount.objects.create(user=simple_user, user_info='{}')

    response = login(app, simple_user, path='/accounts/')
    response = response.click('Delete link')
    assert 'new_password1' not in response.form.fields
    response = response.form.submit(name='unlink').follow()
    assert 'Your account link to FranceConnect has been deleted' in response.text
    # no logout from FC since we are not logged to it
    assert response.request.path == '/accounts/'


def test_unlink_after_login_with_fc(app, franceconnect, simple_user):
    models.FcAccount.objects.create(user=simple_user, sub=franceconnect.sub, user_info='{}')

    response = franceconnect.login_with_fc(app, path='/accounts/')
    response = response.maybe_follow()
    response = response.click('Delete link')
    response.form.set('new_password1', 'ikKL1234')
    response.form.set('new_password2', 'ikKL1234')
    response = response.form.submit(name='unlink')
    assert models.FcAccount.objects.count() == 0
    response = franceconnect.handle_logout(app, response.location)
    assert path(response.location) == '/accounts/'
    response = response.follow()
    assert 'Your account link to FranceConnect has been deleted' in response


@pytest.mark.parametrize('email_verified', [True, False])
def test_account_self_deletion_logs_out_and_clears_fc_session(
    app, franceconnect, simple_user, email_verified, mailoutbox
):
    simple_user.email_verified = email_verified
    simple_user.save(update_fields=['email_verified'])

    models.FcAccount.objects.create(user=simple_user, sub=franceconnect.sub, user_info='{}')
    assert len(mailoutbox) == 0
    response = app.get('/login/?service=portail&next=/accounts/')
    response = response.click(href='callback')
    response = franceconnect.handle_authorization(app, response.location, status=302)
    response = response.follow().click('Delete account')
    response = response.form.submit(name='submit')
    if email_verified:
        response.follow()
        link = get_link_from_mail(mailoutbox[0])
        response = app.get(link).form.submit(name='delete').follow()
        location = response.pyquery('.a2-continue a#a2-continue')[0].get('href')
    else:
        location = response.location
    response = franceconnect.handle_logout(app, location)
    assert response.location.startswith('/logout/')
    response.follow().follow()  # /logout/ then /accounts/validate-deletion/?…
    assert 'fc_id_token' not in app.session
    assert 'fc_id_token_raw' not in app.session
    assert '_auth_user_id' not in app.session
    assert 'fc-state' not in app.cookies


def test_login_email_is_unique_and_already_linked(settings, app, franceconnect, caplog):
    settings.A2_EMAIL_IS_UNIQUE = True

    # setup an already linked user account
    user = User.objects.create(email='john.doe@example.com', first_name='John', last_name='Doe')
    models.FcAccount.objects.create(user=user, sub='4567', token='xxx', user_info='{}')
    response = app.get('/login/?service=portail&next=/idp/')
    response = response.click(href='callback')
    response = franceconnect.handle_authorization(app, response.location, status=302)
    assert models.FcAccount.objects.count() == 1
    cookie = decode_cookie(app.cookies['messages'])
    if isinstance(cookie, list):
        assert len(cookie) == 1
        cookie = cookie[0].message
    assert 'is already used' in cookie
    assert '_auth_user_id' not in app.session
    response = franceconnect.handle_logout(app, response.location)
    assert response.location == '/idp/'


def test_no_password_with_fc_account_can_reset_password(app, db, mailoutbox):
    user = User(email='john.doe@example.com')
    user.set_unusable_password()
    user.save()
    # No FC account, forbidden to set a password
    response = app.get('/login/')
    response = response.click('Reset it!').maybe_follow()
    response.form['email'] = user.email
    assert len(mailoutbox) == 0
    response = response.form.submit()
    assert len(mailoutbox) == 1
    url = get_link_from_mail(mailoutbox[0])
    response = app.get(url).follow().follow()
    assert '_auth_user_id' not in app.session
    assert 'not possible to reset' in response

    # With FC account, can set a password
    models.FcAccount.objects.create(user=user, sub='xxx', token='aaa')
    response = app.get('/login/')
    response = response.click('Reset it!').maybe_follow()
    response.form['email'] = user.email
    assert len(mailoutbox) == 1
    response = response.form.submit()
    assert len(mailoutbox) == 2
    url = get_link_from_mail(mailoutbox[1])
    response = app.get(url, status=200)
    response.form.set('new_password1', 'ikKL1234')
    response.form.set('new_password2', 'ikKL1234')
    response = response.form.submit().follow()
    assert '_auth_user_id' in app.session


def test_login_with_missing_required_attributes(settings, app, franceconnect):
    Attribute.objects.create(label='Title', name='title', required=True, user_editable=True, kind='title')
    Attribute.objects.create(
        label='Birth country', name='birthcountry', required=True, user_editable=True, kind='string'
    )

    assert User.objects.count() == 0
    assert models.FcAccount.objects.count() == 0

    franceconnect.user_info['birthcountry'] = '99512'  # Solomon Islands
    settings.A2_FC_USER_INFO_MAPPINGS = {'birthcountry': {'ref': 'birthcountry'}}

    response = app.get('/login/?service=portail&next=/idp/')
    response = response.click(href='callback')
    response = franceconnect.handle_authorization(app, response.location)

    assert path(response.location) == '/accounts/edit/'
    assert User.objects.count() == 1
    assert models.FcAccount.objects.count() == 1
    cookie = decode_cookie(app.cookies['messages'])
    if isinstance(cookie, list):
        assert len(cookie) == 1
        cookie = cookie[0].message
    assert 'The following fields are mandatory for account creation: Title' in cookie


def test_can_change_password(settings, app, franceconnect):
    user = User.objects.create(email='john.doe@example.com')
    models.FcAccount.objects.create(user=user, sub=franceconnect.sub)

    response = franceconnect.login_with_fc(app, path='/accounts/')
    response = response.maybe_follow()
    assert len(response.pyquery('[href*="password/change"]')) == 0
    response = response.click('Logout')
    response = franceconnect.handle_logout(app, response.location).follow()
    assert '_auth_user_id' not in app.session

    # Login with password
    user.username = 'test'
    user.set_password('test')
    user.save()

    response = login(app, user, path='/accounts/')
    assert len(response.pyquery('[href*="password/change"]')) > 0
    response = response.click('Logout').follow()

    # Relogin with FC
    response = franceconnect.login_with_fc(app, path='/accounts/')
    response = response.maybe_follow()
    assert len(response.pyquery('[href*="password/change"]')) == 0

    # Unlink
    response = response.click('Delete link')
    response.form.set('new_password1', 'ikKL1234')
    response.form.set('new_password2', 'ikKL1234')
    response = response.form.submit(name='unlink')
    assert models.FcAccount.objects.count() == 0
    response = franceconnect.handle_logout(app, response.location)
    assert path(response.location) == '/accounts/'
    response = response.follow()
    assert 'Your account link to FranceConnect has been deleted' in response
    assert len(response.pyquery('[href*="password/change"]')) > 0


def test_invalid_next_url(app, franceconnect):
    assert app.get('/fc/callback/?code=coin&state=JJJ72QQQ').location == '/'


def test_manager_user_sidebar(app, superuser, simple_user):
    login(app, superuser, '/manage/')
    response = app.get('/manage/users/%s/' % simple_user.id)
    assert 'FranceConnect' not in response

    fc_account = models.FcAccount(user=simple_user)
    fc_account.save()

    response = app.get('/manage/users/%s/' % simple_user.id)
    assert 'FranceConnect' in response


def test_user_info_incomplete(settings, app, franceconnect):
    franceconnect.user_info = {}
    franceconnect.login_with_fc_fixed_params(app)

    user = User.objects.get()
    assert app.session['_auth_user_id'] == str(user.pk)
    fc_account = models.FcAccount.objects.get(user=user)
    assert fc_account.sub == franceconnect.sub
    assert fc_account.get_user_info().get('sub') == franceconnect.sub


def test_idtoken_invalid_iss(settings, app, franceconnect, fc_version, caplog):
    if fc_version == '1':
        pytest.skip('v1')

    franceconnect.id_token_update = {'iss': 'foobar'}
    franceconnect.login_with_fc_fixed_params(app)
    assert caplog.messages == [
        'auth_fc: id_token iss "foobar" does not match the expected "https://fcp-low.integ01.dev-franceconnect.fr/api/v2"'
    ]


def test_user_info_jwt_erroneous(settings, app, franceconnect, caplog, fc_version):
    if fc_version == '1':
        pytest.skip('v1')
    franceconnect.user_info_endpoint_response = (200, {'Content-Type': 'application/jwt'}, 'foobar')
    franceconnect.login_with_fc_fixed_params(app)

    assert not User.objects.exists()
    assert '_auth_user_id' not in app.session
    assert not models.FcAccount.objects.exists()
    assert re.match(r'WARNING.*failed to parse UserInfo JWT.*Error during token deserialization', caplog.text)


def test_user_info_jwt_success(settings, app, franceconnect, caplog, fc_version):
    if fc_version == '1':
        pytest.skip('v1')

    franceconnect.login_with_fc_fixed_params(app)

    assert ' failed to parse UserInfo JWT' not in caplog.text
    user = User.objects.get()
    assert app.session['_auth_user_id'] == str(user.pk)
    fc_account = models.FcAccount.objects.get(user=user)
    assert fc_account.sub == franceconnect.sub
    user_info = fc_account.get_user_info()
    assert user_info['email'] == 'john.doe@example.com'
    assert user_info['family_name'] == 'Frédérique'
    assert user_info['given_name'] == 'Ÿuñe'


def test_user_info_jwt_signed_with_wrong_key(settings, app, franceconnect, caplog, fc_version):
    if fc_version == '1':
        pytest.skip('v1')
    # provider's keyset has changed afterwards :/
    new_kid_rsa = 'an129fe13'
    new_kid_ec = '12aue99ej'
    new_key_rsa = jwk.JWK.generate(kty='RSA', size=1024, kid=new_kid_rsa)
    new_key_ec = jwk.JWK.generate(kty='EC', size=256, kid=new_kid_ec)
    franceconnect.jwkset = jwk.JWKSet()
    franceconnect.jwkset.add(new_key_rsa)
    franceconnect.jwkset.add(new_key_ec)
    user_info = franceconnect.user_info.copy()
    header = {'typ': 'JWT', 'alg': 'RS256', 'kid': new_kid_rsa}
    token = jwt.JWT(header=header, claims=user_info)
    token.make_signed_token(new_key_rsa)

    franceconnect.user_info_endpoint_response = (
        200,
        {'Content-Type': 'application/jwt'},
        token.serialize(),
    )

    franceconnect.login_with_fc_fixed_params(app)

    assert not User.objects.exists()
    assert '_auth_user_id' not in app.session
    assert not models.FcAccount.objects.exists()
    assert re.match(r'WARNING.*failed to parse UserInfo JWT.*Key ID.*not in key set', caplog.text)


def test_user_info_incomplete_already_linked(settings, app, franceconnect, authenticator, simple_user):
    user = User.objects.create()
    models.FcAccount.objects.create(user=user, sub=franceconnect.sub)
    franceconnect.user_info = {}
    franceconnect.callback_params = {'next': '/accounts/'}

    response = login(app, simple_user, path='/accounts/')
    response = response.click(href='callback')
    response = franceconnect.handle_authorization(app, response.location, status=302)
    cookie = decode_cookie(app.cookies['messages'])
    if isinstance(cookie, list):
        assert len(cookie) == 1
        cookie = cookie[0].message
    if not authenticator.supports_multiaccount:
        assert 'FranceConnect identity  is already' in cookie
    else:
        assert 'Your FranceConnect account  has been linked.' in cookie
        assert models.FcAccount.objects.get(user=simple_user, sub=franceconnect.sub)


def test_already_linked_to_several_accounts(settings, app, franceconnect, authenticator, simple_user):

    for i in range(3):
        user = User.objects.create(
            first_name=f'John-{i}',
            last_name=f'Doe-{i}',
        )
        models.FcAccount.objects.create(user=user, sub=franceconnect.sub)
    response = login(app, simple_user, path='/accounts/')
    response = response.click(href='callback')
    response = franceconnect.handle_authorization(app, response.location, status=302)
    cookie = decode_cookie(app.cookies['messages'])
    if isinstance(cookie, list):
        assert len(cookie) == 1
        cookie = cookie[0].message
    if not authenticator.supports_multiaccount:
        assert 'FranceConnect identity Ÿuñe Frédérique is already' in cookie
    else:
        assert 'Your FranceConnect account Ÿuñe Frédérique has been linked.' in cookie
        assert models.FcAccount.objects.get(user=simple_user, sub=franceconnect.sub)


def test_save_account_on_delete_user(db):
    user = User.objects.create()
    models.FcAccount.objects.create(user=user, sub='1234')
    user.delete()
    assert models.FcAccount.objects.count() == 0

    deleted_user = DeletedUser.objects.get()
    assert deleted_user.old_data.get('fc_accounts') == [
        {
            'sub': '1234',
        },
    ]


def test_create_missing_email(settings, app, franceconnect, hooks):
    del franceconnect.user_info['email']
    response = app.get('/login/?service=portail&next=/idp/')
    response = response.click(href='callback')

    response = franceconnect.handle_authorization(app, response.location, status=302)
    assert User.objects.count() == 1

    response = app.get('/accounts/', status=200)


def test_multiple_accounts_with_same_email(settings, app, franceconnect):
    ou = get_default_ou()
    ou.email_is_unique = True
    ou.save()

    User.objects.create(email=franceconnect.user_info['email'], ou=ou)
    User.objects.create(email=franceconnect.user_info['email'], ou=ou)

    response = franceconnect.login_with_fc(app, path='/accounts/')
    response = franceconnect.handle_logout(app, response.location)
    assert response.location == '/accounts/'

    response = response.maybe_follow()
    assert 'is already used by another' in response


def test_inactive_raise_permission_denied(app, db, rf):
    usera = User.objects.create(is_active=False, username='a')
    models.FcAccount.objects.create(user=usera, sub='1234')

    with pytest.raises(PermissionDenied):
        FcBackend().authenticate(rf.get('/'), sub='1234', token={}, user_info={})


def test_resolve_authorization_code_http_400(app, franceconnect, caplog):
    franceconnect.token_endpoint_response = (
        400,
        {'Content-Type': 'application/json'},
        json.dumps({'error': 'invalid_request'}),
    )

    response = franceconnect.login_with_fc(app, path='/accounts/')
    assert re.match(r'WARNING.*token request failed.*invalid_request', caplog.text)

    response = response.maybe_follow()
    assert 'invalid_request' not in response
    assert (
        response.pyquery('li.warning').text()
        == 'Unable to connect to FranceConnect: invalid request, contact an administrator.'
    )


def test_resolve_authorization_code_http_400_error_description(app, franceconnect, caplog):
    franceconnect.token_endpoint_response = (
        400,
        {'Content-Type': 'application/json'},
        json.dumps({'error': 'invalid_request', 'error_description': 'Requête invalide'}),
    )

    response = franceconnect.login_with_fc(app, path='/accounts/')
    assert re.match(r'WARNING.*token request failed.*invalid_request', caplog.text)

    response = response.maybe_follow()
    assert 'invalid_request' not in response
    assert 'Requête invalide' not in response  # qs error_desc not displayed to end user
    assert (
        response.pyquery('li.warning').text()
        == 'Unable to connect to FranceConnect: invalid request, contact an administrator.'
    )


def test_resolve_authorization_code_not_json(app, franceconnect, caplog):
    franceconnect.token_endpoint_response = (200, {}, 'not json')
    franceconnect.login_with_fc(app, path='/accounts/').follow()
    assert re.match(r'WARNING.*resolve_authorization_code.*not JSON.*not json', caplog.text)


def test_get_user_info_http_400(app, franceconnect, caplog):
    franceconnect.user_info_endpoint_response = (
        400,
        {'Content-Type': 'application/json'},
        json.dumps({'error': 'invalid_request'}),
    )

    franceconnect.login_with_fc(app, path='/accounts/').follow()
    assert re.match(r'WARNING.*get_user_info.*is not 200.*status_code=400.*invalid_request', caplog.text)


def test_get_user_info_http_400_text_content(app, franceconnect, caplog):
    franceconnect.user_info_endpoint_response = (
        400,
        {},
        'coin',
    )
    franceconnect.login_with_fc(app, path='/accounts/').follow()
    assert re.match(r'WARNING.*get_user_info.*is not 200.*status_code=400.*coin', caplog.text)


def test_get_user_info_not_json(app, franceconnect, caplog):
    franceconnect.user_info_endpoint_response = (
        200,
        {'Content-Type': 'application/json'},
        'coin',
    )
    franceconnect.login_with_fc(app, path='/accounts/').follow()
    assert re.match(r'WARNING.*user_info parsing error.*not JSON.*coin', caplog.text)


def test_get_user_info_no_content_type(app, franceconnect, caplog):
    franceconnect.user_info_endpoint_response = (
        200,
        {},
        'coin',
    )
    franceconnect.login_with_fc(app, path='/accounts/').follow()
    assert "MIME type is invalid ('text/plain')" in caplog.text


def test_get_user_info_wrong_content_type(app, franceconnect, caplog):
    franceconnect.user_info_endpoint_response = (
        200,
        {'Content-Type': 'application/pgp-encrypted'},
        'coin',
    )
    franceconnect.login_with_fc(app, path='/accounts/').follow()
    assert "MIME type is invalid ('application/pgp-encrypted')" in caplog.text


def test_get_user_info_content_type_with_charset(app, franceconnect, caplog):
    franceconnect.user_info_endpoint_response = (
        200,
        {'Content-Type': 'application/json; charset=utf-8'},
        json.dumps(franceconnect.user_info),
    )
    franceconnect.login_with_fc(app, path='/accounts/').follow()
    assert 'MIME type is invalid' not in caplog.text


@pytest.mark.parametrize('cache_close_after', (None, 2, 5, 6, 11, 5000))
def test_fc_is_down(app, franceconnect, freezer, caplog, cache_errors, cache_close_after):
    with cache_errors(cache_close_after):
        franceconnect.token_endpoint_response = (500, {}, 'Internal server error')

        # first error -> warning
        response = franceconnect.login_with_fc(app, path='/accounts/')
        assert len(caplog.records) == 1
        assert caplog.records[-1].levelname == 'WARNING'
        response = response.maybe_follow()
        assert 'Unable to connect to FranceConnect' in response

        # second error, four minutes later -> warning
        freezer.move_to(datetime.timedelta(seconds=+240))
        response = franceconnect.login_with_fc(app, path='/accounts/')
        assert len(caplog.records) == 2
        assert caplog.records[-1].levelname == 'WARNING'
        response = response.maybe_follow()
        assert 'Unable to connect to FranceConnect' in response

        if not cache_close_after:
            # after 5 minutes an error is logged
            freezer.move_to(datetime.timedelta(seconds=+240))
            response = franceconnect.login_with_fc(app, path='/accounts/')
            assert len(caplog.records) == 4
            assert caplog.records[-1].levelname == 'ERROR'
            response = response.maybe_follow()
            assert 'Unable to connect to FranceConnect' in response

            # but only every 5 minutes
            freezer.move_to(datetime.timedelta(seconds=+60))
            response = franceconnect.login_with_fc(app, path='/accounts/')
            assert len(caplog.records) == 5
            assert caplog.records[-1].levelname == 'WARNING'
            response = response.maybe_follow()
            assert 'Unable to connect to FranceConnect' in response

            # a success clear the down flag
            franceconnect.token_endpoint_response = None
            response = franceconnect.login_with_fc(app, path='/accounts/')
            assert app.session['_auth_user_id']
            app.session.flush()
            assert len(caplog.records) == 9

            # such that 5 minutes later only a warning is emitted
            freezer.move_to(datetime.timedelta(seconds=310))
            franceconnect.token_endpoint_response = (500, {}, 'Internal server error')
            response = franceconnect.login_with_fc(app, path='/accounts/')
            assert len(caplog.records) == 10
            assert caplog.records[-1].levelname == 'WARNING'
            response = response.maybe_follow()
            assert 'Unable to connect to FranceConnect' in response
        else:
            # no cache available, not able to determine downtime : 1 log per failure
            freezer.move_to(datetime.timedelta(seconds=+240))
            response = franceconnect.login_with_fc(app, path='/accounts/')
            assert len(caplog.records) == 3
            freezer.move_to(datetime.timedelta(seconds=+60))
            response = franceconnect.login_with_fc(app, path='/accounts/')
            assert len(caplog.records) == 4

            # up again +4 log messages
            franceconnect.token_endpoint_response = None
            response = franceconnect.login_with_fc(app, path='/accounts/')
            assert len(caplog.records) == 8
            # down again +1 log message
            freezer.move_to(datetime.timedelta(seconds=310))
            franceconnect.token_endpoint_response = (500, {}, 'Internal server error')
            response = franceconnect.login_with_fc(app, path='/accounts/')
            assert len(caplog.records) == 9


def test_authorization_error(app, franceconnect):
    error = 'unauthorized'
    error_description = 'Vous n\'êtes pas autorisé à vous connecter.'

    response = app.get(
        '/fc/callback/', params={'error': error, 'error_description': error_description, 'next': '/accounts/'}
    ).maybe_follow()
    messages = response.pyquery('.messages').text()
    assert error not in messages
    assert error_description not in messages  # qs error_desc not display to end user
    assert messages == 'Unable to connect to FranceConnect: technical error, contact an administrator.'

    response = app.get('/fc/callback/', params={'error': error, 'next': '/accounts/'}).maybe_follow()
    messages = response.pyquery('.messages').text()
    assert error not in messages
    assert error_description not in messages
    assert messages == 'Unable to connect to FranceConnect: technical error, contact an administrator.'


def test_registration_page(settings, app, franceconnect, hooks):
    assert User.objects.count() == 0
    assert app.get('/register/?service=portail&next=/idp/')
    franceconnect.login_with_fc_fixed_params(app)

    # a new user has been created
    assert User.objects.count() == 1

    # we must be connected
    assert app.session['_auth_user_id']

    # hook must have been called
    assert hooks.calls['event'][0]['kwargs']['name'] == 'fc-create'


def test_same_email_different_sub(app, franceconnect):
    OU.objects.all().update(email_is_unique=True)

    assert User.objects.count() == 0
    franceconnect.callback_params = {}

    franceconnect.login_with_fc_fixed_params(app)

    # ok user created
    assert User.objects.count() == 1
    # logout
    app.session.flush()

    # change sub
    franceconnect.sub = '4567'

    resp = franceconnect.login_with_fc_fixed_params(app)

    resp = franceconnect.handle_logout(app, resp.location)

    resp = resp.maybe_follow()
    # email collision, sub is different, no new user created
    assert User.objects.count() == 1
    assert 'another email address' in resp


def test_update_fc_email(settings, app, franceconnect):
    settings.A2_EMAIL_IS_UNIQUE = True
    user = User(email='john.doe@example.com', first_name='John', last_name='Doe')
    user.save()
    models.FcAccount.objects.create(user=user, sub='1234')

    # user1 FC email has changed
    assert franceconnect.sub == '1234'
    assert franceconnect.user_info['given_name'] == 'Ÿuñe'
    franceconnect.user_info['email'] = 'jhonny@example.com'

    # connection using FC sub 1234 will not update user1 email
    franceconnect.login_with_fc_fixed_params(app)
    assert User.objects.get(pk=user.pk).email == 'john.doe@example.com'
    assert User.objects.get(pk=user.pk).first_name == 'Ÿuñe'
    assert app.session['_auth_user_id'] == str(user.pk)


def test_change_email(settings, app, franceconnect, mailoutbox, freezer):
    response = app.get('/login/?service=portail&next=/idp/')
    response = response.click(href='callback')
    response = franceconnect.handle_authorization(app, response.location, status=302)
    freezer.move_to(datetime.timedelta(hours=1))
    redirect = app.get('/accounts/change-email/')
    display_message_redirect = redirect.follow()
    display_message_page = display_message_redirect.follow()
    assert 'You must re-authenticate' in display_message_page
    callback_url = display_message_page.pyquery('#a2-continue')[0].attrib['href']
    change_email_page = franceconnect.handle_authorization(app, callback_url, status=302).follow()
    user = User.objects.get()
    assert user.email == 'john.doe@example.com'
    change_email_page.form.set('email', 'jane.doe@example.com')
    redirect = change_email_page.form.submit()
    assert_event(
        'user.email.change.request',
        user=user,
        session=app.session,
        old_email='john.doe@example.com',
        email='jane.doe@example.com',
    )
    link = get_link_from_mail(mailoutbox[-1])
    app.get(link)
    assert_event(
        'user.email.change',
        user=user,
        session=app.session,
        old_email='john.doe@example.com',
        email='jane.doe@example.com',
    )
    assert User.objects.get().email == 'jane.doe@example.com'


def test_fc_authenticator_data_migration(migration, settings):
    app = 'authentic2_auth_fc'
    migrate_from = [(app, '0005_fcauthenticator')]
    migrate_to = [(app, '0006_auto_20220525_1409')]

    old_apps = migration.before(migrate_from)
    FcAuthenticator = old_apps.get_model(app, 'FcAuthenticator')

    settings.AUTH_FRONTENDS_KWARGS = {
        'fc': {'priority': 3, 'show_condition': "'backoffice' not in login_hint"}
    }
    settings.A2_FC_ENABLE = True
    settings.A2_FC_CLIENT_ID = '211286433e39cce01db448d80181bdfd005554b19cd51b3fe7943f6b3b86ab6k'
    settings.A2_FC_CLIENT_SECRET = '211286433e39cce01db448d80181bdfd005554b19cd51b3fe7943f6b3b86ab6z'
    settings.A2_FC_AUTHORIZE_URL = 'https://fcp.integ01.dev-franceconnect.fr/api/v1/authorize'
    settings.A2_FC_SCOPES = ['profile', 'email', 'birthdate']

    new_apps = migration.apply(migrate_to)
    FcAuthenticator = new_apps.get_model(app, 'FcAuthenticator')
    authenticator = FcAuthenticator.objects.get()
    assert authenticator.slug == 'fc-authenticator'
    assert authenticator.order == 3
    assert authenticator.show_condition == "'backoffice' not in login_hint"
    assert authenticator.enabled is True
    assert authenticator.platform == 'test'
    assert authenticator.client_id == '211286433e39cce01db448d80181bdfd005554b19cd51b3fe7943f6b3b86ab6k'
    assert authenticator.client_secret == '211286433e39cce01db448d80181bdfd005554b19cd51b3fe7943f6b3b86ab6z'
    assert authenticator.scopes == ['profile', 'email', 'birthdate']

    # 0007 should have no effect
    new_apps = migration.apply([(app, '0007_auto_20220615_1002')])
    FcAuthenticator = new_apps.get_model(app, 'FcAuthenticator')
    assert FcAuthenticator.objects.get().pk == authenticator.pk


def test_fc_authenticator_data_migration_defaults(migration, settings):
    app = 'authentic2_auth_fc'
    migrate_from = [(app, '0005_fcauthenticator')]
    migrate_to = [(app, '0006_auto_20220525_1409')]

    old_apps = migration.before(migrate_from)
    FcAuthenticator = old_apps.get_model(app, 'FcAuthenticator')

    settings.A2_FC_ENABLE = False

    new_apps = migration.apply(migrate_to)
    FcAuthenticator = new_apps.get_model(app, 'FcAuthenticator')
    authenticator = FcAuthenticator.objects.get()
    assert authenticator.slug == 'fc-authenticator'
    assert authenticator.order == -1
    assert authenticator.show_condition == ''
    assert authenticator.enabled is False
    assert authenticator.platform == 'test'
    assert authenticator.client_id == ''
    assert authenticator.client_secret == ''
    assert authenticator.scopes == ['profile', 'email']


def test_fc_authenticator_data_migration_empty_configuration(migration, settings):
    app = 'authentic2_auth_fc'
    migrate_from = [(app, '0005_fcauthenticator')]
    migrate_to = [(app, '0006_auto_20220525_1409')]

    old_apps = migration.before(migrate_from)
    FcAuthenticator = old_apps.get_model(app, 'FcAuthenticator')

    new_apps = migration.apply(migrate_to)
    FcAuthenticator = new_apps.get_model(app, 'FcAuthenticator')
    assert not FcAuthenticator.objects.exists()


def test_fc_authenticator_data_migration_bad_settings(migration, settings):
    app = 'authentic2_auth_fc'
    migrate_from = [(app, '0005_fcauthenticator')]
    migrate_to = [(app, '0006_auto_20220525_1409')]

    old_apps = migration.before(migrate_from)
    FcAuthenticator = old_apps.get_model(app, 'FcAuthenticator')

    settings.AUTH_FRONTENDS_KWARGS = {'fc': {'priority': None, 'show_condition': None}}
    settings.A2_FC_ENABLE = False
    settings.A2_FC_CLIENT_ID = 'x' * 260
    settings.A2_FC_CLIENT_SECRET = None
    settings.A2_FC_AUTHORIZE_URL = 'https://fcp.integ01.dev-franceconnect.fr/api/v1/authorize'
    settings.A2_FC_SCOPES = None

    new_apps = migration.apply(migrate_to)
    FcAuthenticator = new_apps.get_model(app, 'FcAuthenticator')
    authenticator = FcAuthenticator.objects.get()
    assert authenticator.slug == 'fc-authenticator'
    assert authenticator.order == -1
    assert authenticator.show_condition == ''
    assert authenticator.enabled is False
    assert authenticator.platform == 'test'
    assert authenticator.client_id == 'x' * 256
    assert authenticator.client_secret == ''
    assert authenticator.scopes == ['profile', 'email']


def test_fc_authenticator_data_migration_fixup(migration, settings):
    app = 'authentic2_auth_fc'
    migrate_from = [(app, '0006_auto_20220525_1409')]
    migrate_to = [(app, '0007_auto_20220615_1002')]

    old_apps = migration.before(migrate_from)
    FcAuthenticator = old_apps.get_model(app, 'FcAuthenticator')

    # authenticator was not created by 0006
    assert not FcAuthenticator.objects.exists()

    settings.A2_FC_ENABLE = True
    settings.A2_FC_CLIENT_ID = '211286433e39cce01db448d80181bdfd005554b19cd51b3fe7943f6b3b86ab6k'
    settings.A2_FC_CLIENT_SECRET = '211286433e39cce01db448d80181bdfd005554b19cd51b3fe7943f6b3b86ab6z'

    new_apps = migration.apply(migrate_to)
    FcAuthenticator = new_apps.get_model(app, 'FcAuthenticator')

    # authenticator was created by 0007
    authenticator = FcAuthenticator.objects.get()
    assert authenticator.slug == 'fc-authenticator'
    assert authenticator.client_id == '211286433e39cce01db448d80181bdfd005554b19cd51b3fe7943f6b3b86ab6k'


def test_bad_email_handling(settings, app, franceconnect, caplog):
    caplog.set_level(logging.WARNING)

    # On creation
    franceconnect.user_info['email'] = '@'

    response = app.get('/login/?next=/idp/')
    response = response.click(href='callback')

    response = franceconnect.handle_authorization(app, response.location, status=302)

    user = User.objects.get()
    assert user.email == ''
    assert caplog.text.count('invalid email')

    caplog.clear()
    app.session.flush()
    user.email = 'john.doe@example.com'
    user.save()

    # On update
    response = app.get('/login/?next=/idp/')
    response = response.click(href='callback')

    response = franceconnect.handle_authorization(app, response.location, status=302)

    user = User.objects.get()
    assert user.email == 'john.doe@example.com'
    assert caplog.text.count('invalid email')


def test_no_fc_link_button_for_external_user(app, simple_user, franceconnect):
    response = login(app, simple_user, path='/accounts/')
    assert 'div id="fc-button"' in response.text
    assert 'FranceConnect' in response.text

    simple_user.userexternalid_set.create(source='ldap', external_id='user')

    response = app.get('/accounts/')
    assert 'div id="fc-button"' not in response.text
