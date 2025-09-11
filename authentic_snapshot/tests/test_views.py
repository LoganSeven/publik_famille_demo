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
# authentic2

import datetime
from unittest import mock
from urllib.parse import urlparse

import pytest
from django.urls import reverse
from django.utils.html import escape
from django.utils.timezone import now

from authentic2.apps.authenticators.models import LoginPasswordAuthenticator
from authentic2.custom_user.models import DeletedUser, User
from authentic2.forms.passwords import PasswordChangeForm, SetPasswordForm
from authentic2.models import Attribute, SMSCode, Token
from authentic2.views import passive_login

from .utils import assert_event, get_link_from_mail, login, logout

pytestmark = pytest.mark.django_db


def test_profile(app, simple_user):
    page = login(app, simple_user, path=reverse('account_management'))
    assert simple_user.first_name in page
    assert simple_user.last_name in page


def test_phone_number_change_invalid_number(settings, app, simple_user):
    settings.A2_PROFILE_FIELDS = ('phone', 'mobile')

    Attribute.objects.create(
        kind='phone_number',
        name='mobile',
        label='Mobile',
        user_visible=True,
        user_editable=True,
    )

    simple_user.attributes.mobile = 'def'  # invalid number
    resp = login(app, simple_user, path='/accounts/edit/')

    assert resp.pyquery('input#id_mobile_1')[0].value == 'def'

    resp = resp.form.submit()
    assert (
        'Invalid phone number. Phone number from Metropolitan France must respect local format (e.g. 06 39 98 01 23).'
    ) == resp.pyquery('.error p')[0].text_content().strip()

    resp.form['mobile_1'] = '612345678'
    resp.form.submit().follow()
    simple_user.refresh_from_db()

    assert simple_user.attributes.mobile == '+33612345678'


def test_password_change(app, simple_user):
    simple_user.set_password('hop')
    simple_user.save()
    resp = login(app, simple_user, password='hop', path='/accounts/password/change/')
    old_session_key = app.session.session_key

    assert resp.form['old_password'].attrs['autocomplete'] == 'current-password'
    assert resp.form['new_password1'].attrs['autocomplete'] == 'new-password'
    assert resp.form['new_password2'].attrs['autocomplete'] == 'new-password'
    resp.form['old_password'] = 'hop'
    resp.form['new_password1'] = 'hopAbcde1'
    resp.form['new_password2'] = 'hopAbcde1'
    resp = resp.form.submit()

    assert resp.location == '/accounts/'

    new_session_key = app.session.session_key
    assert old_session_key != new_session_key, 'session\'s key has not been cycled'
    assert_event('user.password.change', user=simple_user, session=app.session)

    resp = resp.follow()
    assert 'Password changed' in resp


def test_password_change_error(
    app,
    simple_user,
):
    from authentic2.utils.misc import PasswordChangeError

    simple_user.set_password('hop')
    simple_user.save()
    resp = login(app, simple_user, password='hop', path='/accounts/password/change/')
    resp.form['old_password'] = 'hop'
    resp.form['new_password1'] = 'hopAbcde1'
    resp.form['new_password2'] = 'hopAbcde1'

    with mock.patch(
        'authentic2.custom_user.models.User.set_password', side_effect=PasswordChangeError('boum!')
    ):
        resp = resp.form.submit()

    assert 'Password changed' not in resp
    assert 'boum!' in resp


def test_password_change_form(simple_user):
    Attribute.objects.create(
        kind='string',
        name='favourite_song',
    )

    simple_user.attributes.favourite_song = '0opS 1 D1t iT @GAiN'

    data = {
        'new_password1': 'Password0',
        'new_password2': 'Password0',
    }

    form = PasswordChangeForm(user=simple_user, data=data)
    assert form.fields['new_password1'].widget.min_strength is None
    assert 'new_password1' not in form.errors

    LoginPasswordAuthenticator.objects.update(min_password_strength=3)
    form = PasswordChangeForm(user=simple_user, data=data)
    assert form.fields['new_password1'].widget.min_strength == 3
    assert form.errors['new_password1'] == ['This password is not strong enough.']

    data = {
        'new_password1': '0opS 1 D1t iT @GAiN',
        'new_password2': '0opS 1 D1t iT @GAiN',
    }
    form = PasswordChangeForm(user=simple_user, data=data)
    assert form.errors['new_password1'] == ['This password is not strong enough.']

    simple_user.attributes.favourite_song = 'Baby one more time'
    form = PasswordChangeForm(user=simple_user, data=data)
    assert 'new_password1' not in form.errors


def test_set_password_form(simple_user):
    Attribute.objects.create(
        kind='string',
        name='favourite_song',
    )

    simple_user.attributes.favourite_song = '0opS 1 D1t iT @GAiN'

    data = {
        'new_password1': 'Password0',
        'new_password2': 'Password0',
    }

    form = SetPasswordForm(user=simple_user, data=data)
    assert form.fields['new_password1'].widget.min_strength is None
    assert 'new_password1' not in form.errors

    LoginPasswordAuthenticator.objects.update(min_password_strength=3)
    form = SetPasswordForm(user=simple_user, data=data)
    assert form.fields['new_password1'].widget.min_strength == 3
    assert form.errors['new_password1'] == ['This password is not strong enough.']

    data = {
        'new_password1': '0opS 1 D1t iT @GAiN',
        'new_password2': '0opS 1 D1t iT @GAiN',
    }
    form = SetPasswordForm(user=simple_user, data=data)
    assert form.errors['new_password1'] == ['This password is not strong enough.']

    simple_user.attributes.favourite_song = 'Baby one more time'
    form = SetPasswordForm(user=simple_user, data=data)
    assert 'new_password1' not in form.errors


def test_well_known_password_change(app):
    resp = app.get('/.well-known/change-password')
    assert resp.location == '/accounts/password/change/'


class TestDeleteAccountEmailVerified:
    @pytest.fixture
    def simple_user(self, simple_user):
        simple_user.email_verified = True
        simple_user.save()
        return simple_user

    def test_account_delete(self, app, simple_user, mailoutbox):
        assert simple_user.is_active
        assert len(mailoutbox) == 0
        page = login(app, simple_user, path=reverse('delete_account'))
        assert simple_user.email in page.text
        page.form.submit(name='submit').follow()
        assert len(mailoutbox) == 1
        link = get_link_from_mail(mailoutbox[0])
        assert mailoutbox[0].subject == 'Validate account deletion request on testserver'
        assert [simple_user.email] == mailoutbox[0].to
        page = app.get(link)
        # FIXME: webtest does not set the Referer header, so the logout page will always ask for
        # confirmation under tests
        response = page.form.submit(name='delete')
        assert '_auth_user_id' not in app.session
        assert User.objects.filter(id=simple_user.id).count() == 0
        assert DeletedUser.objects.filter(old_user_id=simple_user.id).count() == 1
        assert len(mailoutbox) == 2
        assert mailoutbox[1].subject == 'Account deletion on testserver'
        assert mailoutbox[0].to == [simple_user.email]
        assert 'Set-Cookie:  messages=' in str(response)  # Deletion performed
        assert urlparse(response.location).path == '/'

    def test_account_delete_when_logged_out(self, app, simple_user, mailoutbox):
        assert simple_user.is_active
        assert len(mailoutbox) == 0
        page = login(app, simple_user, path=reverse('delete_account'))
        page.form.submit(name='submit').follow()
        assert len(mailoutbox) == 1
        link = get_link_from_mail(mailoutbox[0])
        logout(app)
        page = app.get(link)
        assert (
            'You are about to delete the account of <strong>%s</strong>.'
            % escape(simple_user.get_full_name())
            in page.text
        )
        response = page.form.submit(name='delete')
        assert User.objects.filter(id=simple_user.id).count() == 0
        assert DeletedUser.objects.filter(old_user_id=simple_user.id).count() == 1
        assert len(mailoutbox) == 2
        assert mailoutbox[1].subject == 'Account deletion on testserver'
        assert mailoutbox[0].to == [simple_user.email]
        assert 'Set-Cookie:  messages=' in str(response)  # Deletion performed
        assert urlparse(response.location).path == '/'

    def test_account_delete_by_other_user(self, app, simple_user, user_ou1, mailoutbox):
        assert simple_user.is_active
        assert user_ou1.is_active
        assert len(mailoutbox) == 0
        page = login(app, simple_user, path=reverse('delete_account'))
        page.form.submit(name='submit').follow()
        assert len(mailoutbox) == 1
        link = get_link_from_mail(mailoutbox[0])
        logout(app)
        login(app, user_ou1, path=reverse('account_management'))
        page = app.get(link)
        assert (
            'You are about to delete the account of <strong>%s</strong>.'
            % escape(simple_user.get_full_name())
            in page.text
        )
        response = page.form.submit(name='delete')
        assert app.session['_auth_user_id'] == str(user_ou1.id)
        assert User.objects.filter(id=simple_user.id).count() == 0
        assert DeletedUser.objects.filter(old_user_id=simple_user.id).count() == 1
        assert len(mailoutbox) == 2
        assert mailoutbox[1].subject == 'Account deletion on testserver'
        assert mailoutbox[0].to == [simple_user.email]
        assert 'Set-Cookie:  messages=' in str(response)  # Deletion performed
        assert urlparse(response.location).path == '/'

    def test_account_delete_fake_token(self, app, simple_user, mailoutbox):
        response = (
            app.get(reverse('validate_deletion', kwargs={'deletion_token': 'thisismostlikelynotavalidtoken'}))
            .follow()
            .follow()
        )
        assert 'The account deletion request is invalid, try again' in response.text

    def test_account_delete_expired_token(self, app, simple_user, mailoutbox, freezer):
        freezer.move_to('2019-08-01')
        page = login(app, simple_user, path=reverse('delete_account'))
        page.form.submit(name='submit').follow()
        freezer.move_to('2019-08-04')  # Too late...
        link = get_link_from_mail(mailoutbox[0])
        response = app.get(link).follow()
        assert 'The account deletion request is too old, try again' in response.text

    def test_account_delete_valid_token_unexistent_user(self, app, simple_user, mailoutbox):
        page = login(app, simple_user, path=reverse('delete_account'))
        page.form.submit(name='submit').follow()
        link = get_link_from_mail(mailoutbox[0])
        simple_user.delete()
        response = app.get(link).follow().follow()
        assert 'This account has previously been deleted.' in response.text

    def test_account_delete_valid_token_inactive_user(self, app, simple_user, mailoutbox):
        page = login(app, simple_user, path=reverse('delete_account'))
        page.form.submit(name='submit').follow()
        link = get_link_from_mail(mailoutbox[0])
        simple_user.is_active = False
        simple_user.save()
        response = app.get(link).maybe_follow()
        assert 'This account is inactive, it cannot be deleted.' in response.text


class TestDeleteAccountEmailNotVerified:
    def test_account_delete(self, app, simple_user, mailoutbox):
        assert simple_user.is_active
        assert len(mailoutbox) == 0
        page = login(app, simple_user, path=reverse('delete_account'))
        response = page.form.submit(name='submit').follow()
        assert '_auth_user_id' not in app.session
        assert User.objects.filter(id=simple_user.id).count() == 0
        assert DeletedUser.objects.filter(old_user_id=simple_user.id).count() == 1
        assert len(mailoutbox) == 1
        assert mailoutbox[0].subject == 'Account deletion on testserver'
        assert mailoutbox[0].to == [simple_user.email]
        assert 'Set-Cookie:  messages=' in str(response)  # Deletion performed
        assert urlparse(response.location).path == '/'

    def test_account_delete_old_authentication(self, app, simple_user, mailoutbox, freezer):
        assert simple_user.is_active
        assert len(mailoutbox) == 0
        login(app, simple_user)
        freezer.move_to(datetime.timedelta(hours=1))
        redirect = app.get('/accounts/delete/')
        login_page = redirect.follow()
        assert 'You must re-authenticate' in login_page
        login_page.form.set('password', simple_user.clear_password)
        page = login_page.form.submit(name='login-password-submit').follow()
        response = page.form.submit(name='submit').follow()
        assert '_auth_user_id' not in app.session
        assert User.objects.filter(id=simple_user.id).count() == 0
        assert DeletedUser.objects.filter(old_user_id=simple_user.id).count() == 1
        assert len(mailoutbox) == 1
        assert mailoutbox[0].subject == 'Account deletion on testserver'
        assert mailoutbox[0].to == [simple_user.email]
        assert 'Set-Cookie:  messages=' in str(response)  # Deletion performed
        assert urlparse(response.location).path == '/'


def test_delete_account_phone_identifier(app, phone_user, phone_activated_authn):
    login(
        app,
        phone_user,
        login=phone_user.attributes.phone,
        path='/accounts/',
    )
    resp = app.get('/accounts/delete/')
    assert 'A validation code will be sent to +33123456789' in resp.text
    resp = resp.form.submit().follow()
    code = SMSCode.objects.get()
    assert not Token.objects.count()
    resp.form.set('sms_code', code.value)
    resp = resp.form.submit('').follow()
    # assert not Token.objects.count()  # single use token?
    with pytest.raises(User.DoesNotExist):
        User.objects.get(id=phone_user.id)


def test_delete_account_phone_identifier_deactivated_user(app, phone_user, phone_activated_authn):
    login(app, phone_user)
    resp = app.get('/accounts/delete/')
    resp = resp.form.submit().follow()
    code = SMSCode.objects.get()
    resp.form.set('sms_code', code.value)

    phone_user.is_active = False
    phone_user.save()

    resp = resp.form.submit('').follow().maybe_follow()
    # a user, submitting a deletion request, deactivated since then, should still
    # be able to complete the deletion if the request has been submitted
    with pytest.raises(User.DoesNotExist):
        User.objects.get(id=phone_user.id)


def test_delete_account_phone_verified_yet_missing(app, phone_user):
    login(app, phone_user)

    # some improper use, e.g. in backoffice where phones can be arbitrarily erased
    phone_user.attributes.phone = ''
    phone_user.save()

    resp = app.get('/accounts/delete/')

    assert resp.pyquery('form p')[-1].text.strip() == 'Do you really want to delete your account?'
    assert 'validation code' not in resp.form.html
    assert '+33122446666' not in resp.html


def test_delete_account_verified_email_precedence_over_verified_phone(app, phone_user, phone_activated_authn):
    phone_user.email = 'user@example.net'
    phone_user.email_verified = True
    phone_user.save()

    login(app, phone_user)

    resp = app.get('/accounts/delete/')
    # email is verified and defaults as deletion code exchange means
    assert 'A validation message will be sent to user@example.net.' in resp.text
    resp.form.submit().follow()
    assert not SMSCode.objects.count()


def test_delete_account_verified_phone_precedence_over_unverified_email(
    app,
    phone_user,
):
    assert not phone_user.email_verified
    login(app, phone_user)
    resp = app.get('/accounts/delete/')
    # email is unverified and skipped as deletion code exchange means
    # fallback on phone
    assert f'A validation code will be sent to {phone_user.attributes.phone}' in resp.text
    resp.form.submit().follow()
    assert SMSCode.objects.get()


def test_delete_account_unverified_identifiers_direct_deletion(app, simple_user, phone_activated_authn):
    simple_user.attributes.phone = '+33122446666'
    assert not simple_user.email_verified
    assert not simple_user.phone_verified_on

    login(app, simple_user)

    resp = app.get('/accounts/delete/')
    # email is unverified but so is the user's phone
    # deletion process is direct
    assert 'A validation message' not in resp.text
    assert 'A validation code' not in resp.text
    resp.form.submit().follow()
    assert not SMSCode.objects.count()
    assert not Token.objects.count()
    assert not User.objects.filter(id=simple_user.id).exists()


def test_delete_account_phone_identifier_changed_in_between(app, phone_user):
    login(app, phone_user)

    resp = app.get('/accounts/delete/')
    resp = resp.form.submit().follow()
    code = SMSCode.objects.get()

    phone_user.attributes.phone = '+33122446688'

    resp.form.set('sms_code', code.value)
    resp = resp.form.submit('').follow().maybe_follow()
    assert 'Something went wrong' in resp.text
    assert User.objects.filter(id=phone_user.id).exists()


def test_verify_phone_link_displayed(app, nomail_user, settings, phone_activated_authn):
    settings.SMS_URL = 'https://foo.whatever.none/'

    nomail_user.attributes.phone = '+33122446666'
    nomail_user.phone_verified_on = now()
    nomail_user.save()
    login(app, nomail_user, login=nomail_user.attributes.phone, path='/', password=nomail_user.clear_password)
    resp = app.get('/accounts/')
    assert not resp.pyquery('a[href="/accounts/verify-phone/"]')
    app.get('/accounts/verify-phone/', status=403)

    nomail_user.phone_verified_on = None
    nomail_user.attributes.phone = None
    nomail_user.save()

    resp = app.get('/accounts/')
    assert not resp.pyquery('a[href="/accounts/verify-phone/"]')
    app.get('/accounts/verify-phone/', status=403)

    nomail_user.phone_verified_on = None
    nomail_user.attributes.phone = '+33122446666'
    nomail_user.save()

    resp = app.get('/accounts/')
    assert resp.pyquery('a[href="/accounts/verify-phone/"]')
    resp = app.get('/accounts/verify-phone/')
    assert 'Verify Phone attribute in order to use it for authentication' in resp.pyquery('title')[0].text
    assert 'Your current unverified phone number is +33122446666.' in resp.text

    phone_activated_authn.phone_identifier_field = None
    phone_activated_authn.save()

    resp = app.get('/accounts/')
    assert not resp.pyquery('a[href="/accounts/verify-phone/"]')
    app.get('/accounts/verify-phone/', status=403)


def test_login_invalid_next(app):
    app.get(reverse('auth_login') + '?next=plop')


def test_custom_account(settings, app, simple_user):
    response = login(app, simple_user, path=reverse('account_management'))
    assert response.status_code == 200
    settings.A2_ACCOUNTS_URL = 'http://combo/account/'
    response = app.get(reverse('account_management'))
    assert response.status_code == 302
    assert response['Location'] == settings.A2_ACCOUNTS_URL


@pytest.mark.parametrize('view_name', ['registration_register', 'password_reset', 'phone-change'])
def test_views_sms_ratelimit(app, db, simple_user, settings, freezer, view_name, phone_activated_authn):
    phone_activated_authn.sms_ip_ratelimit = '10/h'
    phone_activated_authn.sms_number_ratelimit = '3/d'
    phone_activated_authn.save()

    freezer.move_to('2020-01-01')

    settings.SMS_SENDER = 'EO'

    if view_name in ('phone-change',):
        login(app, simple_user)

    response = app.get(reverse(view_name))
    response.form.set('phone_0', '33')
    response.form.set('phone_1', '0612345678')

    if view_name in ('phone-change',):
        response.form.set('password', simple_user.clear_password)

    response = response.form.submit()
    assert 'try again later' not in response.text
    for _ in range(2):
        response = app.get(reverse(view_name))
        response.form.set('phone_0', '33')
        response.form.set('phone_1', '0612345678')
        if view_name in ('phone-change',):
            response.form.set('password', simple_user.clear_password)
        response = response.form.submit()
        assert 'An SMS code has already been sent' in response.text
        if view_name in ('phone-change',):
            response.form.set('password', simple_user.clear_password)
        response = response.form.submit()
        assert 'try again later' not in response.text

    response = app.get(reverse(view_name))
    response.form.set('phone_0', '33')
    response.form.set('phone_1', '0612345678')
    if view_name in ('phone-change',):
        response.form.set('password', simple_user.clear_password)
    response = response.form.submit()
    if view_name in ('phone-change',):
        response.form.set('password', simple_user.clear_password)
    response = response.form.submit()
    assert 'try again later' in response.text

    suffixes = iter(range(6000, 9999))
    # reach ip limit
    for _ in range(7):
        response = app.get(reverse(view_name))
        random_suffix = next(suffixes)
        response.form.set('phone_0', '33')
        response.form.set('phone_1', f'061234{random_suffix:04d}')
        if view_name in ('phone-change',):
            response.form.set('password', simple_user.clear_password)
        response = response.form.submit()
        assert 'try again later' not in response.text

    response = app.get(reverse(view_name))
    random_suffix = next(suffixes)
    response.form.set('phone_0', '33')
    response.form.set('phone_1', f'061234{random_suffix:04d}')
    if view_name in ('phone-change',):
        response.form.set('password', simple_user.clear_password)
    response = response.form.submit()
    assert 'try again later' in response.text

    # ip ratelimits are lifted after an hour
    freezer.tick(datetime.timedelta(hours=1))
    response = app.get(reverse(view_name))
    random_suffix = next(suffixes)
    response.form.set('phone_0', '33')
    response.form.set('phone_1', f'061234{random_suffix:04d}')
    if view_name in ('phone-change',):
        response.form.set('password', simple_user.clear_password)
    response = response.form.submit()
    assert 'try again later' not in response.text

    # identifier ratelimits are lifted after a day
    response = app.get(reverse(view_name))
    response.form.set('phone_0', '33')
    response.form.set('phone_1', '0612345678')
    if view_name in ('phone-change',):
        response.form.set('password', simple_user.clear_password)
    response = response.form.submit()
    assert 'Multiple SMSs have already been sent to this number.' in response.text
    if view_name in ('phone-change',):
        response.form.set('password', simple_user.clear_password)
    response = response.form.submit()
    assert 'try again later' in response.text

    freezer.tick(datetime.timedelta(days=1))
    response = app.get(reverse(view_name))
    response.form.set('phone_0', '33')
    response.form.set('phone_1', '0612345678')
    if view_name in ('phone-change',):
        response.form.set('password', simple_user.clear_password)
    response = response.form.submit()
    assert 'try again later' not in response.text


@pytest.mark.parametrize('view_name', ['registration_register', 'password_reset'])
def test_views_email_ratelimit(app, db, simple_user, settings, mailoutbox, freezer, view_name):
    freezer.move_to('2020-01-01')
    LoginPasswordAuthenticator.objects.update(emails_ip_ratelimit='10/h', emails_address_ratelimit='3/d')
    users = [User.objects.create(email='test%s@test.com' % i) for i in range(8)]

    # reach email limit
    for _ in range(3):
        response = app.get(reverse(view_name))
        response.form.set('email', simple_user.email)
        response = response.form.submit()
    assert len(mailoutbox) == 3

    response = app.get(reverse(view_name))
    response.form.set('email', simple_user.email)
    response = response.form.submit()
    assert len(mailoutbox) == 3
    assert 'try again later' in response.text
    if view_name == 'password_reset':
        assert_event('user.password.reset.failure', email=simple_user.email)

    # reach ip limit
    for i in range(7):
        response = app.get(reverse(view_name))
        response.form.set('email', users[i].email)
        response = response.form.submit()
    assert len(mailoutbox) == 10

    response = app.get(reverse(view_name))
    response.form.set('email', users[i + 1].email)
    response = response.form.submit()
    assert len(mailoutbox) == 10
    assert 'try again later' in response.text

    # ip ratelimits are lifted after an hour
    freezer.tick(datetime.timedelta(hours=1))
    response = app.get(reverse(view_name))
    response.form.set('email', users[0].email)
    response = response.form.submit()
    assert len(mailoutbox) == 11

    # email ratelimits are lifted after a day
    response = app.get(reverse(view_name))
    response.form.set('email', simple_user.email)
    response = response.form.submit()
    assert len(mailoutbox) == 11
    assert 'try again later' in response.text

    freezer.tick(datetime.timedelta(days=1))
    response = app.get(reverse(view_name))
    response.form.set('email', simple_user.email)
    response = response.form.submit()
    assert len(mailoutbox) == 12


@pytest.mark.parametrize('view_name', ['registration_register', 'password_reset'])
def test_views_email_token_resend(app, simple_user, settings, mailoutbox, view_name):
    settings.A2_TOKEN_EXISTS_WARNING = True

    response = app.get(reverse(view_name))
    response.form.set('email', simple_user.email)
    response = response.form.submit()
    assert len(mailoutbox) == 1

    # warn user token has already been sent
    response = app.get(reverse(view_name))
    response.form.set('email', simple_user.email)
    response = response.form.submit()
    assert 'email has already been sent' in response.text
    assert len(mailoutbox) == 1

    # validating again anyway works
    response = response.form.submit()
    assert len(mailoutbox) == 2


def test_views_login_display_a_cancel_button(app, settings):
    response = app.get(reverse('auth_login'), params={'next': '/foo/', 'nonce': 'xxx'})
    assert not response.html.find('button', {'class': 'cancel-button'})

    settings.A2_LOGIN_DISPLAY_A_CANCEL_BUTTON = True
    response = app.get(reverse('auth_login'), params={'next': '/foo/', 'nonce': 'xxx'})
    assert response.html.find('button', {'class': 'cancel-button'})


def test_set_home_url(settings, app, simple_user, service, monkeypatch):
    from authentic2.models import Service

    settings.A2_REDIRECT_WHITELIST = ['https://example.com/', 'https://not-example.com/']
    monkeypatch.setattr(Service, 'get_base_urls', lambda self: ['https://example.com/'])

    login(app, simple_user)
    assert 'service_pk' not in app.session
    app.get('/accounts/?next=https://example.com/')
    assert app.session['service_pk'] == service.pk
    app.get('/accounts/?next=https://not-example.com/')
    assert 'service_pk' not in app.session


def test_redirected_views(app):
    assert app.get('/accounts/register/').location == '/register/'
    assert (
        app.get('/accounts/password/reset/confirm/abcd1234/').location == '/password/reset/confirm/abcd1234/'
    )


def test_passive_login(rf):
    from django.contrib.sessions.middleware import SessionMiddleware

    req = rf.get('/')
    SessionMiddleware(lambda x: None).process_request(req)
    assert passive_login(req, next_url='/', login_hint={'pop'}) is None

    authenticator1 = mock.Mock()
    authenticator1.show.return_value = True
    authenticator1.passive_login.return_value = 'response1'
    authenticator2 = mock.Mock()
    authenticator2.show.return_value = True
    authenticator2.passive_login.return_value = 'response2'

    with mock.patch(
        'authentic2.utils.misc.get_authenticators', return_value=[authenticator1, authenticator2]
    ):
        assert passive_login(req, next_url='/', login_hint={'pop'}) == 'response1'
