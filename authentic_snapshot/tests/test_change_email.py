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

from unittest import mock

from django.contrib.auth import REDIRECT_FIELD_NAME
from django.utils.http import urlencode

from . import utils


def change_email(app, user, email, mailoutbox, next_url=None):
    utils.login(app, user)
    l = len(mailoutbox)
    url = '/accounts/change-email/'
    if next_url is not None:
        url += f'?{urlencode({REDIRECT_FIELD_NAME: next_url})}'
    response = app.get(url)
    if not user.email:
        assert "Your account currently doesn't declare any email address." in response.text
    else:
        assert f'Your current email is {user.email}.' in response.text
    response.form.set('email', email)
    response.form.set('password', user.clear_password)
    response = response.form.submit()
    response = response.follow()
    assert (
        'Your request for changing your email is received. An email of validation was sent to you. Please click on the link contained inside.'
        == response.pyquery('ul.messages li.info').text()
    )
    assert len(mailoutbox) == l + 1
    return mailoutbox[-1]


def change_email_no_password(app, user, email, mailoutbox, recent_authn=True):
    utils.login(app, user)
    # for some reason session-stored authn events have been lost
    app.session['authentication-events'] = []
    app.session.save()
    l = len(mailoutbox)
    with mock.patch(
        'authentic2.views.IdentifierChangeMixin.can_validate_with_password'
    ) as mocked_can_validate:
        with mock.patch(
            'authentic2.views.IdentifierChangeMixin.has_recent_authentication'
        ) as mocked_has_recent_authn:
            mocked_can_validate.return_value = False
            mocked_has_recent_authn.return_value = recent_authn
            response = app.get('/accounts/change-email/')
            if not recent_authn:
                response = response.follow()
                assert (
                    response.pyquery('li.info')[0].text
                    == 'You must re-authenticate to change your email address.'
                )
                response.form.set('username', user.username)
                response.form.set('password', user.clear_password)
                response = response.form.submit(name='login-password-submit')
                mocked_has_recent_authn.return_value = True
                response = response.follow().maybe_follow()
            response.form.set('email', email)
            assert 'password' not in response.form.fields
            response = response.form.submit()
    assert len(mailoutbox) == l + 1
    return mailoutbox[-1]


def test_change_email(app, simple_user, user_ou1, mailoutbox):
    email = change_email(app, simple_user, user_ou1.email, mailoutbox)
    utils.assert_event(
        'user.email.change.request',
        user=simple_user,
        session=app.session,
        old_email=simple_user.email,
        email=user_ou1.email,
    )
    link = utils.get_link_from_mail(email)
    resp = app.get(link)
    utils.assert_event(
        'user.email.change',
        user=simple_user,
        session=app.session,
        old_email=simple_user.email,
        email=user_ou1.email,
    )
    simple_user.refresh_from_db()
    # ok it worked
    assert resp.location == '/'
    assert simple_user.email == user_ou1.email

    resp = resp.follow()
    assert (
        'your request for changing your email for john.doe@example.net is successful'
        in resp.pyquery('ul.messages li.info').text()
    )


def test_change_email_next_url(app, simple_user, user_ou1, mailoutbox):
    email = change_email(app, simple_user, user_ou1.email, mailoutbox, next_url='/accounts/consents/')
    utils.assert_event(
        'user.email.change.request',
        user=simple_user,
        session=app.session,
        old_email=simple_user.email,
        email=user_ou1.email,
    )
    link = utils.get_link_from_mail(email)
    resp = app.get(link)
    utils.assert_event(
        'user.email.change',
        user=simple_user,
        session=app.session,
        old_email=simple_user.email,
        email=user_ou1.email,
    )
    simple_user.refresh_from_db()
    assert resp.location == '/accounts/consents/'
    # ok it worked
    assert simple_user.email == user_ou1.email

    resp = resp.follow()
    assert (
        'your request for changing your email for john.doe@example.net is successful'
        == resp.pyquery('ul.messages li.info').text()
    )


def test_change_email_next_url_invalid(app, simple_user, user_ou1, mailoutbox):
    email = change_email(
        app, simple_user, user_ou1.email, mailoutbox, next_url='https://evil-website.example.com/'
    )
    utils.assert_event(
        'user.email.change.request',
        user=simple_user,
        session=app.session,
        old_email=simple_user.email,
        email=user_ou1.email,
    )
    link = utils.get_link_from_mail(email)
    resp = app.get(link)
    utils.assert_event(
        'user.email.change',
        user=simple_user,
        session=app.session,
        old_email=simple_user.email,
        email=user_ou1.email,
    )
    simple_user.refresh_from_db()
    assert resp.location == '/'
    assert simple_user.email == user_ou1.email

    resp = resp.follow()
    assert (
        'your request for changing your email for john.doe@example.net is successful'
        == resp.pyquery('ul.messages li.info').text()
    )


def test_change_email_next_url_redirect_passlist(app, simple_user, user_ou1, mailoutbox, settings):
    settings.A2_REDIRECT_WHITELIST = ['https://remote-website.example.com/']
    email = change_email(
        app, simple_user, user_ou1.email, mailoutbox, next_url='https://remote-website.example.com/'
    )
    utils.assert_event(
        'user.email.change.request',
        user=simple_user,
        session=app.session,
        old_email=simple_user.email,
        email=user_ou1.email,
    )
    link = utils.get_link_from_mail(email)
    resp = app.get(link)
    utils.assert_event(
        'user.email.change',
        user=simple_user,
        session=app.session,
        old_email=simple_user.email,
        email=user_ou1.email,
    )
    simple_user.refresh_from_db()
    # intermediary ui to display pending django messages before leaving authentic
    assert resp.location.startswith('/continue/?next=https%3A//remote-website.example.com/')
    assert simple_user.email == user_ou1.email

    resp = resp.follow()
    assert (
        'your request for changing your email for john.doe@example.net is successful'
        == resp.pyquery('ul.messages li.info').text()
    )


def test_declare_first_email(app, nomail_user, user_ou1, mailoutbox):
    email = change_email(app, nomail_user, user_ou1.email, mailoutbox)
    utils.assert_event(
        'user.email.change.request',
        user=nomail_user,
        session=app.session,
        old_email=nomail_user.email,
        email=user_ou1.email,
    )
    link = utils.get_link_from_mail(email)
    app.get(link)
    utils.assert_event(
        'user.email.change',
        user=nomail_user,
        session=app.session,
        old_email=nomail_user.email,
        email=user_ou1.email,
    )
    nomail_user.refresh_from_db()
    # ok it worked
    assert nomail_user.email == user_ou1.email


def test_change_email_no_password(app, simple_user, user_ou1, mailoutbox):
    email = change_email_no_password(app, simple_user, user_ou1.email, mailoutbox)
    utils.assert_event(
        'user.email.change.request',
        user=simple_user,
        session=app.session,
        old_email=simple_user.email,
        email=user_ou1.email,
    )
    link = utils.get_link_from_mail(email)
    app.get(link)
    utils.assert_event(
        'user.email.change',
        user=simple_user,
        session=app.session,
        old_email=simple_user.email,
        email=user_ou1.email,
    )
    simple_user.refresh_from_db()
    # ok it worked
    assert simple_user.email == user_ou1.email


def test_change_email_no_password_no_recent_authn(app, simple_user, user_ou1, mailoutbox):
    email = change_email_no_password(app, simple_user, user_ou1.email, mailoutbox, recent_authn=False)
    utils.assert_event(
        'user.email.change.request',
        user=simple_user,
        session=app.session,
        old_email=simple_user.email,
        email=user_ou1.email,
    )
    link = utils.get_link_from_mail(email)
    app.get(link)
    utils.assert_event(
        'user.email.change',
        user=simple_user,
        session=app.session,
        old_email=simple_user.email,
        email=user_ou1.email,
    )
    simple_user.refresh_from_db()
    # ok it worked
    assert simple_user.email == user_ou1.email


def test_change_email_email_is_unique(app, settings, simple_user, user_ou1, mailoutbox):
    settings.A2_EMAIL_IS_UNIQUE = True
    email = change_email(app, simple_user, user_ou1.email, mailoutbox)
    link = utils.get_link_from_mail(email)
    # email change is impossible as email is already taken
    assert 'password/reset' in link


def test_change_email_ou_email_is_unique(app, simple_user, user_ou1, user_ou2, mailoutbox):
    user_ou1.ou.email_is_unique = True
    user_ou1.ou.save()
    user_ou2.email = 'john.doe-ou2@example.net'
    user_ou2.save()
    email = change_email(app, simple_user, user_ou2.email, mailoutbox)
    link = utils.get_link_from_mail(email)
    app.get(link)
    simple_user.refresh_from_db()
    # ok it worked for a differnt ou
    assert simple_user.email == user_ou2.email
    # now set simple_user in same ou as user_ou1
    simple_user.ou = user_ou1.ou
    simple_user.save()
    email = change_email(app, simple_user, user_ou1.email, mailoutbox)
    link = utils.get_link_from_mail(email)
    # email change is impossible as email is already taken in the same ou
    assert 'password/reset' in link


def test_change_email_is_unique_after_first_view(app, settings, simple_user, user_ou1, mailoutbox):
    settings.A2_EMAIL_IS_UNIQUE = True
    new_email = 'wtf@example.net'
    email = change_email(app, simple_user, new_email, mailoutbox)
    link = utils.get_link_from_mail(email)
    # user_ou1 take the new email in the meantime
    user_ou1.email = new_email
    user_ou1.save()
    # email change is impossible as email is already taken
    link = utils.get_link_from_mail(email)
    response = app.get(link).follow()
    assert 'is already used by another account' in response.text


def test_email_change_no_existing_number_address_action_label_variation(
    app,
    nomail_user,
    phone_activated_authn,
):
    resp = utils.login(
        app,
        nomail_user,
        login=nomail_user.attributes.phone,
        path='/accounts/',
        password=nomail_user.clear_password,
    )
    assert resp.pyquery("[href='/accounts/change-email/']")[0].text == 'Declare your email address'
    nomail_user.email = 'testemail@example.com'
    nomail_user.save()

    resp = app.get('/accounts/')
    assert resp.pyquery("[href='/accounts/change-email/']")[0].text == 'Change email'
