# authentic2 - versatile identity manager
# Copyright (C) 2010-2020 Entr'ouvert
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

import pytest
from django.contrib.auth import authenticate
from django.test.utils import override_settings
from django.urls import reverse

from authentic2.apps.authenticators.models import LoginPasswordAuthenticator
from authentic2.models import SMSCode, Token
from authentic2.utils.misc import send_password_reset_mail
from authentic2.views import PasswordResetView

from . import utils


def test_send_password_reset_email(app, simple_user, mailoutbox):
    assert len(mailoutbox) == 0
    with utils.run_on_commit_hooks():
        send_password_reset_mail(
            simple_user,
            legacy_subject_templates=['registration/password_reset_subject.txt'],
            legacy_body_templates=['registration/password_reset_email.html'],
        )
    assert len(mailoutbox) == 1
    url = utils.get_link_from_mail(mailoutbox[0])
    relative_url = url.split('testserver')[1]
    resp = app.get(relative_url, status=200)
    resp.form.set('new_password1', '1234==aA')
    resp.form.set('new_password2', '1234==aA')
    resp = resp.form.submit().follow()
    assert len(mailoutbox) == 2
    assert str(app.session['_auth_user_id']) == str(simple_user.pk)
    utils.assert_event('user.password.reset', user=simple_user, session=app.session)


def test_send_password_reset_by_sms_code_improperly_configured(app, phone_user, settings):
    settings.SMS_URL = 'https://bar.whatever.none/'

    assert not SMSCode.objects.count()
    assert not Token.objects.count()

    url = reverse('password_reset')
    resp = app.get(url, status=200)
    resp.form.set('phone_1', '0123456789')
    resp = resp.form.submit().follow().maybe_follow()
    assert 'Something went wrong while trying to send' in resp.pyquery('li.error').text()


@pytest.mark.parametrize('erroneous_input', ('01a2233456789', '06a1b2c3d4LL9933', '⛔️🔼💜⛽️🐾📰🚖🕌🐛👂'))
def test_send_password_reset_erroneous_input(
    app, nomail_user, settings, phone_activated_authn, erroneous_input
):
    nomail_user.attributes.phone = '+33123456789'
    nomail_user.save()
    settings.SMS_URL = 'https://foo.whatever.none/'

    url = reverse('password_reset')
    resp = app.get(url, status=200)
    resp.form.set('phone_1', erroneous_input)
    resp = resp.form.submit()
    assert (
        'Please provide a valid email address or mobile phone number.' in resp.pyquery('.errornotice').text()
    )


@pytest.mark.parametrize('erroneous_input', ('paul@example', '⛔️🔼💜⛽️🐾📰🚖🕌🐛👂'))
def test_send_password_phone_activated_erroneous_email_input(
    app, simple_user, settings, phone_activated_authn, erroneous_input
):
    simple_user.attributes.phone = '+33123456789'
    simple_user.save()
    settings.SMS_URL = 'https://foo.whatever.none/'

    url = reverse('password_reset')
    resp = app.get(url, status=200)
    resp.form.set('email', erroneous_input)
    resp = resp.form.submit()
    assert (
        'Please provide a valid email address or mobile phone number.' in resp.pyquery('.errornotice').text()
    )


def test_send_password_reset_by_sms_code(app, phone_user, settings, sms_service):
    code_length = settings.SMS_CODE_LENGTH
    assert not SMSCode.objects.count()
    assert not Token.objects.count()

    resp = app.get('/password/reset/', status=200)
    assert resp.pyquery('form').children()[3].find('div').find('label').text == 'Email:'
    assert resp.pyquery('form').children()[4].text == 'Or'
    assert resp.pyquery('form').children()[5].find('div').find('label').text == 'Phone number:'
    assert not resp.pyquery('.pk-mark-optional-fields')
    assert resp.pyquery('.pk-hide-requisiteness')
    resp.form.set('phone_1', '0123456789')
    resp = resp.form.submit().follow().maybe_follow()
    assert sms_service.last_message.startswith('Your code is')
    code = SMSCode.objects.get()
    assert sms_service.call_count == 1
    assert sms_service.last_message[-code_length:] == code.value
    assert 'Your code is valid for the next 3 minutes' in resp.text
    assert 'The code you received by SMS.' in resp.text
    resp.form.set('sms_code', code.value)
    resp = resp.form.submit().follow()
    assert Token.objects.count() == 1

    assert authenticate(username='user', password='1234==aA') is None
    resp.form.set('new_password1', '1234==aA')
    resp.form.set('new_password2', '1234==aA')
    resp.form.submit().follow()
    assert sms_service.call_count == 2
    assert SMSCode.objects.count() == 1  # no new code generated
    # verify user is logged
    assert str(app.session['_auth_user_id']) == str(phone_user.pk)
    user = authenticate(username=phone_user.attributes.phone, password='1234==aA')
    assert user == phone_user

    settings.A2_USER_CAN_RESET_PASSWORD = False
    app.get('/password/reset/', status=404)


def test_send_password_username_or_email_or_phone_noemail(settings, app, phone_user, sms_service):
    settings.A2_USER_CAN_RESET_PASSWORD_BY_USERNAME = True

    resp = app.get('/password/reset/')
    resp.form.set('email_or_username', phone_user.username)
    resp = resp.form.submit().maybe_follow()

    assert not SMSCode.objects.count()
    assert not Token.objects.count()
    assert not sms_service.call_count

    assert 'Your account has no email, you cannot ask for a password reset with your username.' in resp.text


def test_send_password_reset_by_sms_code_next_url(app, phone_user):
    resp = app.get('/accounts/consents/').follow()
    resp = resp.click('Reset it!')
    resp.form.set('phone_1', '0123456789')
    resp = resp.form.submit().follow().maybe_follow()
    code = SMSCode.objects.get()
    resp.form.set('sms_code', code.value)
    resp = resp.form.submit().follow()

    resp.form.set('new_password1', '1234==aA')
    resp.form.set('new_password2', '1234==aA')
    resp = resp.form.submit()
    assert resp.location == '/accounts/consents/'
    resp = resp.follow()
    assert 'Consent Management' in resp


def test_password_reset_empty_form(app, db, settings, phone_activated_authn):
    settings.SMS_URL = 'https://foo.whatever.none/'

    url = reverse('password_reset')
    resp = app.get(url, status=200)
    resp = resp.form.submit()
    assert 'There were errors processing your form.' in resp.pyquery('div.errornotice').text()
    assert (
        'Please provide a valid email address or mobile phone number.'
        in resp.pyquery('div.errornotice').text()
    )


def test_password_reset_both_fields_filled_email_precedence(
    app, simple_user, settings, mailoutbox, phone_activated_authn
):
    simple_user.attributes.phone = '+33123456789'
    simple_user.save()
    settings.SMS_URL = 'https://foo.whatever.none/'

    url = reverse('password_reset')
    resp = app.get(url, status=200)
    resp.form.set('email', simple_user.email)
    resp.form.set('phone_1', '0123456789')
    resp = resp.form.submit()
    utils.assert_event('user.password.reset.request', user=simple_user, email=simple_user.email)
    assert resp['Location'].endswith('/instructions/')
    resp = resp.follow()
    assert len(mailoutbox) == 1
    assert not SMSCode.objects.count()


def test_send_password_reset_by_sms_code_erroneous_phone_number(
    app, nomail_user, settings, phone_activated_authn
):
    settings.SMS_URL = 'https://foo.whatever.none/'

    assert not SMSCode.objects.count()
    assert not Token.objects.count()

    url = reverse('password_reset')
    resp = app.get(url, status=200)
    resp.form.set('phone_1', '0111111111')
    resp = resp.form.submit().follow().maybe_follow()
    assert 'Something went wrong while trying to send' not in resp.text
    assert 'error' not in resp.text
    assert resp.pyquery('title').text() == 'Authentic2 - testserver - SMS code validation'
    code = SMSCode.objects.get()
    assert code.fake
    resp.form.set('sms_code', 'whatever')
    resp = resp.form.submit()
    assert resp.pyquery('ul.errorlist').text() == 'Wrong SMS code.'
    # even if the correct value is guessed, the code is still fake & not valid whatsoever
    resp.form.set('sms_code', code.value)
    resp = resp.form.submit()
    assert resp.pyquery('ul.errorlist').text() == 'Wrong SMS code.'
    assert not Token.objects.count()


def test_reset_by_email(app, simple_user, mailoutbox, settings):
    url = reverse('password_reset')
    resp = app.get(url, status=200)
    resp.form.set('email', simple_user.email)
    assert len(mailoutbox) == 0
    settings.DEFAULT_FROM_EMAIL = 'show only addr <noreply@example.net>'
    resp = resp.form.submit()
    utils.assert_event('user.password.reset.request', user=simple_user, email=simple_user.email)
    assert resp['Location'].endswith('/instructions/')
    resp = resp.follow()
    assert '"noreply@example.net"' in resp.text
    assert 'show only addr' not in resp.text
    assert len(mailoutbox) == 1
    url = utils.get_link_from_mail(mailoutbox[0])
    relative_url = url.split('testserver')[1]
    resp = app.get(relative_url, status=200)
    resp.form.set('new_password1', '1234==aA')
    resp.form.set('new_password2', '1234==aA')
    resp = resp.form.submit()
    # verify user is logged
    assert str(app.session['_auth_user_id']) == str(simple_user.pk)

    with override_settings(A2_USER_CAN_RESET_PASSWORD=False):
        url = reverse('password_reset')
        app.get(url, status=404)


def test_can_reset_by_username(app, db, simple_user, settings, mailoutbox):
    resp = app.get('/password/reset/')
    assert 'email_or_username' not in resp.form.fields
    settings.A2_USER_CAN_RESET_PASSWORD_BY_USERNAME = True
    resp = app.get('/password/reset/')
    assert 'email_or_username' in resp.form.fields

    resp.form.set('email_or_username', simple_user.username)
    resp = resp.form.submit().follow()

    assert 'An email has been sent to %s' % simple_user.username in resp
    assert len(mailoutbox) == 1
    assert mailoutbox[0].to == [simple_user.email]

    url = utils.get_link_from_mail(mailoutbox[0])
    relative_url = url.split('testserver')[1]
    resp = app.get(relative_url, status=200)
    resp.form.set('new_password1', '1234==aA')
    resp.form.set('new_password2', '1234==aA')
    resp = resp.form.submit()
    assert len(mailoutbox) == 2
    # verify user is logged
    assert str(app.session['_auth_user_id']) == str(simple_user.pk)


def test_can_reset_by_username_with_email(app, db, simple_user, settings, mailoutbox):
    settings.A2_USER_CAN_RESET_PASSWORD_BY_USERNAME = True
    resp = app.get('/password/reset/')
    resp.form.set('email_or_username', simple_user.username)
    resp = resp.form.submit().follow()
    assert 'An email has been sent to %s' % simple_user.username in resp
    assert len(mailoutbox) == 1


def test_can_reset_by_username_no_email(app, db, simple_user, settings, mailoutbox):
    settings.A2_USER_CAN_RESET_PASSWORD_BY_USERNAME = True
    simple_user.email = ''
    simple_user.save()

    resp = app.get('/password/reset/')
    resp.form.set('email_or_username', simple_user.username)
    resp = resp.form.submit()
    assert resp.pyquery('title')[0].text.endswith('there are errors in the form')
    assert any('Your account has no email' in text for text in resp.pyquery('.errornotice p').contents())
    assert len(mailoutbox) == 0


def test_reset_by_email_no_account(app, db, mailoutbox):
    resp = app.get('/password/reset/')
    resp.form.set('email', 'john.doe@example.com')
    resp = resp.form.submit().follow()

    assert 'An email has been sent to john.doe@example.com' in resp
    assert len(mailoutbox) == 1
    assert 'no account was found' in mailoutbox[0].body


def test_can_reset_by_username_no_account(app, db, settings, mailoutbox):
    settings.A2_USER_CAN_RESET_PASSWORD_BY_USERNAME = True

    resp = app.get('/password/reset/')
    resp.form.set('email_or_username', 'john.doe')
    resp = resp.form.submit().follow()
    assert 'An email has been sent to john.doe' in resp
    assert len(mailoutbox) == 0


def test_can_reset_by_username_no_account_email(app, db, settings, mailoutbox):
    settings.A2_USER_CAN_RESET_PASSWORD_BY_USERNAME = True

    resp = app.get('/password/reset/')
    resp.form.set('email_or_username', 'john.doe@example.com')
    resp = resp.form.submit().follow()
    assert 'An email has been sent to john.doe' in resp
    assert len(mailoutbox) == 1


def test_user_exclude(app, simple_user, mailoutbox, settings):
    settings.A2_USER_EXCLUDE = {'username': simple_user.username}  # will not match simple_user

    url = reverse('password_reset')
    resp = app.get(url, status=200)
    resp.form.set('email', simple_user.email)
    assert len(mailoutbox) == 0
    resp = resp.form.submit()
    assert 'no account was found associated with this address' in mailoutbox[0].body


def test_old_url_redirect(app, db):
    response = app.get('/password/reset/whatever')
    assert response.location == '/password/reset/'
    response = response.follow()
    assert 'please reset your password again' in response


@pytest.mark.parametrize(
    'registration_open',
    [True, False],
)
def test_send_password_reset_email_no_account(app, db, mailoutbox, settings, registration_open):
    LoginPasswordAuthenticator.objects.update(registration_open=registration_open)
    resp = app.get('/password/reset/?next=/whatever/', status=200)
    resp.form.set('email', 'test@entrouvert.com')
    resp = resp.form.submit()

    mail = mailoutbox[0]
    assert mail.subject == 'Password reset on testserver'
    for body in (mail.body, mail.alternatives[0][0]):
        assert 'no account was found associated with this address' in body
        if registration_open:
            assert 'https://testserver/register/' in body
            # check next_url was preserved
            assert 'next=/whatever/' in body
        else:
            assert 'https://testserver/register/' not in body


def test_send_password_reset_email_disabled_account(app, simple_user, mailoutbox):
    simple_user.is_active = False
    simple_user.save()

    url = reverse('password_reset')
    resp = app.get(url, status=200)
    resp.form.set('email', simple_user.email)
    resp = resp.form.submit()

    mail = mailoutbox[0]
    assert mail.subject == 'Your account on testserver is disabled'
    assert 'your account has been disabled on this server' in mail.body


def test_email_validation(app, db):
    resp = app.get('/password/reset/')
    resp.form.set('email', 'coin@')
    resp = resp.form.submit()
    assert 'Please enter a valid email address (example: john.doe@entrouvert.com)' in resp


def test_honeypot(app, db, settings, mailoutbox):
    settings.DEFAULT_FROM_EMAIL = 'show only addr <noreply@example.net>'

    url = reverse('password_reset')
    response = app.get(url, status=200)
    response.form.set('email', 'testbot@entrouvert.com')
    response.form.set('robotcheck', True)
    response = response.form.submit()
    response = response.follow()
    assert len(mailoutbox) == 0
    assert 'Your password reset request has been refused' in response


def test_ou_policies(app, db, settings, user_ou1, ou1, user_ou2, ou2, mailoutbox):
    settings.A2_USER_CAN_RESET_PASSWORD = True

    user_ou1.email = 'john.doe.ou1@example.net'
    user_ou1.save()
    ou1.user_can_reset_password = False  # impossible
    ou1.save()

    url = reverse('password_reset')
    resp = app.get(url, status=200)
    resp.form.set('email', user_ou1.email)
    resp = resp.form.submit()
    url = utils.get_link_from_mail(mailoutbox[0])
    relative_url = url.split('testserver')[1]
    resp = app.get(relative_url, status=302)  # impossible, redirected to /
    assert resp['Location'] == '/'

    ou2.user_can_reset_password = None  # system default
    ou2.save()

    url = reverse('password_reset')
    resp = app.get(url, status=200)
    resp.form.set('email', user_ou2.email)
    resp = resp.form.submit()
    url = utils.get_link_from_mail(mailoutbox[1])
    relative_url = url.split('testserver')[1]
    resp = app.get(relative_url, status=200)
    assert 'In order to create a secure password' in resp.text

    settings.A2_USER_CAN_RESET_PASSWORD = False

    url = reverse('password_reset')
    resp = app.get(url, status=404)  # globally deactivated, page not found


def test_open_redirection(db, rf, app):
    BAD_URL = 'https://bad.url.com/'

    request = rf.get(f'/password/reset/?next={BAD_URL}')

    password_reset = PasswordResetView()
    password_reset.setup(request)
    assert password_reset.get_form_kwargs()['initial'].get('next_url') != BAD_URL

    request = rf.post('/password/reset/', {'next_url': BAD_URL, 'email': 'john.doe@example.com'})
    password_reset = PasswordResetView()
    password_reset.setup(request)
    form = password_reset.get_form()
    assert form.is_valid()
    assert form.cleaned_data['next_url'] == ''
