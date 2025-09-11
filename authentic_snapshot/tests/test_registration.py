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

from datetime import date, timedelta
from unittest import mock
from urllib.parse import quote, urlparse

import pytest
import requests
from django.contrib.auth import REDIRECT_FIELD_NAME, get_user_model
from django.contrib.contenttypes.models import ContentType
from django.urls import reverse

from authentic2 import models
from authentic2.a2_rbac.utils import get_default_ou
from authentic2.apps.authenticators.models import LoginPasswordAuthenticator
from authentic2.apps.journal.models import Event
from authentic2.forms.profile import modelform_factory
from authentic2.forms.registration import RegistrationCompletionForm
from authentic2.models import Attribute, AttributeValue, SMSCode, Token
from authentic2.utils import misc as utils_misc
from authentic2.validators import EmailValidator
from authentic2.views import RegistrationView

from .utils import assert_event, get_link_from_mail, login

User = get_user_model()


@pytest.mark.parametrize('additional_pre_registration_fields', [False, True])
def test_registration_success(
    app, db, settings, mailoutbox, external_redirect, additional_pre_registration_fields
):
    next_url, good_next_url = external_redirect

    settings.LANGUAGE_CODE = 'en-us'
    settings.DEFAULT_FROM_EMAIL = 'show only addr <noreply@example.net>'
    if additional_pre_registration_fields:
        settings.A2_PRE_REGISTRATION_FIELDS = ['first_name', 'last_name']

    # disable existing attributes
    models.Attribute.objects.update(disabled=True)

    url = utils_misc.make_url('registration_register', params={REDIRECT_FIELD_NAME: next_url})
    response = app.get(url)
    assert response.form['email'].attrs['autocomplete'] == 'email'
    response.form.set('email', 'testbot@entrouvert.com')
    if additional_pre_registration_fields:
        assert response.form['first_name'].attrs['autocomplete'] == 'given-name'
        assert response.form['last_name'].attrs['autocomplete'] == 'family-name'
        response.form.set('first_name', 'John')
        response.form.set('last_name', 'Doe')
    response = response.form.submit()

    assert_event('user.registration.request', email='testbot@entrouvert.com')
    assert urlparse(response['Location']).path == reverse('registration_complete')
    if not good_next_url:
        assert not urlparse(response['Location']).query

    response = response.follow()
    if good_next_url:
        assert response.pyquery('a[href="%s"]' % next_url)
    else:
        assert response.pyquery('a[href="/"]')

    assert 'Follow the instructions' in response.text
    assert 'testbot@entrouvert.com' in response.text
    assert 'considered as spam' in response.text
    assert '"noreply@example.net"' in response.text
    assert 'show only addr' not in response.text
    assert len(mailoutbox) == 1

    link = get_link_from_mail(mailoutbox[0])

    # test password validation
    response = app.get(link)
    response.form.set('password1', 'toto')
    response.form.set('password2', 'toto')
    response = response.form.submit()
    assert '8 characters' in response.text

    # set valid password
    response.form.set('password1', 'T0==toto')
    response.form.set('password2', 'T0==toto')
    response = response.form.submit().maybe_follow()
    if good_next_url:
        assert 'You have just created an account.' in response.text
        assert next_url in response.text
        assert response.request.path == '/continue/'
    else:
        assert response.request.path == '/'
        assert 'You have just created an account.' in response.text
    assert User.objects.count() == 1
    assert len(mailoutbox) == 2
    assert 'was successful' in mailoutbox[1].body

    new_user = User.objects.get()
    assert_event('user.registration', user=new_user, how='email')
    assert not Event.objects.filter(type__name='user.login').exists()
    assert new_user.email == 'testbot@entrouvert.com'
    assert new_user.username is None
    assert new_user.check_password('T0==toto')
    assert new_user.is_active
    assert not new_user.is_staff
    assert not new_user.is_superuser
    assert str(app.session['_auth_user_id']) == str(new_user.pk)
    assert app.session['authentication-events'][-1]['how'] == 'email'

    # account creation is considered an authn, therefore identifier changes require a password input
    assert 'password' in app.get(reverse('email-change')).form.fields

    response = app.get('/login/')
    response.form.set('username', 'testbot@entrouvert.com')
    response.form.set('password', 'T0==toto')
    response = response.form.submit(name='login-password-submit')
    assert urlparse(response['Location']).path == reverse('auth_homepage')


def test_registration_realm(app, db, settings, mailoutbox):
    settings.LANGUAGE_CODE = 'en-us'
    settings.A2_REGISTRATION_REALM = 'realm'
    settings.A2_REDIRECT_WHITELIST = ['http://relying-party.org/']
    settings.A2_REQUIRED_FIELDS = ['username']

    # disable existing attributes
    models.Attribute.objects.update(disabled=True)

    next_url = 'http://relying-party.org/'
    url = utils_misc.make_url('registration_register', params={REDIRECT_FIELD_NAME: next_url})

    response = app.get(url)
    response.form.set('email', 'testbot@entrouvert.com')
    response = response.form.submit()

    assert urlparse(response['Location']).path == reverse('registration_complete')

    response = response.follow()
    assert 'Follow the instructions' in response.text
    assert 'testbot@entrouvert.com' in response.text
    assert len(mailoutbox) == 1

    link = get_link_from_mail(mailoutbox[0])

    # register
    response = app.get(link)
    response.form.set('username', 'toto')
    response.form.set('password1', 'T0==toto')
    response.form.set('password2', 'T0==toto')
    response = response.form.submit().follow()
    assert 'You have just created an account.' in response.text
    assert next_url in response.text
    assert len(mailoutbox) == 2
    assert 'was successful' in mailoutbox[1].body

    # verify user has expected attributes
    new_user = User.objects.get()
    assert new_user.username == 'toto@realm'
    assert new_user.email == 'testbot@entrouvert.com'
    assert new_user.check_password('T0==toto')
    assert new_user.is_active
    assert not new_user.is_staff
    assert not new_user.is_superuser
    assert str(app.session['_auth_user_id']) == str(new_user.pk)

    # test login
    response = app.get('/login/')
    response.form.set('username', 'testbot@entrouvert.com')
    response.form.set('password', 'T0==toto')
    response = response.form.submit(name='login-password-submit')
    assert urlparse(response['Location']).path == reverse('auth_homepage')


def test_registration_email_validation(app, db, monkeypatch, settings):
    settings.A2_VALIDATE_EMAIL_DOMAIN = True
    monkeypatch.setattr(EmailValidator, 'query_mxs', lambda x, y: ['mx1.entrouvert.org'])

    resp = app.get(reverse('registration_register'))
    resp.form.set('email', 'testbot@entrouvert.com')
    resp = resp.form.submit().follow()
    assert 'Follow the instructions' in resp.text

    monkeypatch.setattr(EmailValidator, 'query_mxs', lambda x, y: [])
    resp = app.get(reverse('registration_register'))
    resp.form.set('email', 'testbot@entrouvert.com')
    resp = resp.form.submit()
    assert 'Email domain (entrouvert.com) does not exists' in resp.text


def test_username_settings(app, db, settings, mailoutbox):
    settings.LANGUAGE_CODE = 'en-us'
    settings.A2_REGISTRATION_FORM_USERNAME_REGEX = r'^(ab)+$'
    settings.A2_REGISTRATION_FORM_USERNAME_LABEL = 'Identifiant'
    settings.A2_REGISTRATION_FORM_USERNAME_HELP_TEXT = 'Bien remplir'
    settings.A2_REGISTRATION_FIELDS = ['username']
    settings.A2_REQUIRED_FIELDS = ['username']

    # disable existing attributes
    models.Attribute.objects.update(disabled=True)

    response = app.get(reverse('registration_register'))
    response.form.set('email', 'testbot@entrouvert.com')
    response = response.form.submit()
    assert urlparse(response['Location']).path == reverse('registration_complete')

    response = response.follow()
    assert 'Follow the instructions' in response.text
    assert 'testbot@entrouvert.com' in response.text
    assert len(mailoutbox) == 1
    link = get_link_from_mail(mailoutbox[0])

    # register
    response = app.get(link)

    # check form render has changed
    assert response.pyquery('[for=id_username]').text() == 'Identifiant:'
    for key in ['username', 'password1', 'password2']:
        assert response.pyquery('[for=id_%s]' % key)
        assert response.pyquery('[for=id_%s]' % key).attr('class') == 'form-field-required'

    assert response.pyquery('#help_text_id_username').text() == 'Bien remplir'
    assert not response.pyquery('.errorlist')

    # check username is validated using regexp
    response.form.set('username', 'abx')
    response.form.set('password1', 'T0==toto')
    response.form.set('password2', 'T0==toto')
    response = response.form.submit()

    assert response.pyquery('title')[0].text.endswith('there are errors in the form')
    assert 'Enter a valid value' in response.text

    # check regexp accepts some valid values
    response.form.set('username', 'abab')
    response.form.set('password1', 'T0==toto')
    response.form.set('password2', 'T0==toto')
    response = response.form.submit()
    assert urlparse(response['Location']).path == reverse('auth_homepage')
    response = response.follow()
    assert 'You have just created an account.' in response.text
    assert len(mailoutbox) == 2
    assert 'was successful' in mailoutbox[1].body


def test_username_is_unique(app, db, settings, mailoutbox):
    settings.LANGUAGE_CODE = 'en-us'
    settings.A2_REGISTRATION_FIELDS = ['username']
    settings.A2_REQUIRED_FIELDS = ['username']
    settings.A2_USERNAME_IS_UNIQUE = True

    # disable existing attributes
    models.Attribute.objects.update(disabled=True)

    response = app.get(reverse('registration_register'))
    response.form.set('email', 'testbot@entrouvert.com')
    response = response.form.submit()
    assert urlparse(response['Location']).path == reverse('registration_complete')

    response = response.follow()
    assert 'Follow the instructions' in response.text
    assert 'testbot@entrouvert.com' in response.text
    assert len(mailoutbox) == 1

    link = get_link_from_mail(mailoutbox[0])

    response = app.get(link)
    response.form.set('username', 'john.doe')
    response.form.set('password1', 'T0==toto')
    response.form.set('password2', 'T0==toto')
    response = response.form.submit()
    assert urlparse(response['Location']).path == reverse('auth_homepage')
    response = response.follow()
    assert 'You have just created an account.' in response.text
    assert len(mailoutbox) == 2
    assert 'was successful' in mailoutbox[1].body

    # logout
    app.session.flush()

    # try again
    response = app.get(reverse('registration_register'))
    response.form.set('email', 'testbot@entrouvert.com')
    response = response.form.submit()
    link = get_link_from_mail(mailoutbox[2])

    response = app.get(link)
    response = response.click('create')

    response.form.set('username', 'john.doe')
    response.form.set('password1', 'T0==toto')
    response.form.set('password2', 'T0==toto')
    response = response.form.submit()
    assert 'This username is already in use. Please supply a different username.' in response.text


def test_email_is_unique(app, db, settings, mailoutbox):
    settings.LANGUAGE_CODE = 'en-us'
    settings.A2_EMAIL_IS_UNIQUE = True

    # disable existing attributes
    models.Attribute.objects.update(disabled=True)

    response = app.get('/register/')
    response.form.set('email', 'testbot@entrouvert.com')
    response = response.form.submit()
    assert urlparse(response['Location']).path == reverse('registration_complete')

    response = response.follow()
    assert 'Follow the instructions' in response.text
    assert 'testbot@entrouvert.com' in response.text
    assert len(mailoutbox) == 1

    link = get_link_from_mail(mailoutbox[0])

    response = app.get(link)
    response.form.set('password1', 'T0==toto')
    response.form.set('password2', 'T0==toto')
    response = response.form.submit()
    assert urlparse(response['Location']).path == reverse('auth_homepage')
    response = response.follow()
    assert 'You have just created an account.' in response.text
    assert len(mailoutbox) == 2
    assert 'was successful' in mailoutbox[1].body

    # logout
    app.session.flush()

    response = app.get('/register/?next=/whatever/')
    response.form.set('email', 'testbot@entrouvert.com')
    response = response.form.submit()
    assert urlparse(response['Location']).path == reverse('registration_complete')

    response = response.follow()
    assert 'Follow the instructions' in response.text
    assert 'testbot@entrouvert.com' in response.text
    assert 'This email address is already in use.' not in response.text
    assert len(mailoutbox) == 3
    assert 'You already have' in mailoutbox[2].body
    link = get_link_from_mail(mailoutbox[2])
    response = app.get(link)
    # check next_url was preserved
    assert response.location == '/whatever/'


def test_email_is_unique_login_link_different_ou(app, db, settings, mailoutbox, ou2):
    settings.A2_EMAIL_IS_UNIQUE = True

    User.objects.create(email='testbot@entrouvert.com', ou=ou2)

    response = app.get('/register/')
    response.form.set('email', 'testbot@entrouvert.com')
    response = response.form.submit()
    assert urlparse(response['Location']).path == reverse('registration_complete')

    response = response.follow()
    assert 'You already have an account' in mailoutbox[0].body

    link = get_link_from_mail(mailoutbox[0])
    response = app.get(link).follow()
    assert 'logged in with your already-existing account' in response.text


def test_attribute_model(app, db, settings, mailoutbox):
    settings.LANGUAGE_CODE = 'en-us'
    # disable existing attributes
    models.Attribute.objects.update(disabled=True)

    models.Attribute.objects.create(label='Prénom', name='prenom', required=True, kind='string')
    models.Attribute.objects.create(
        label='Nom', name='nom', asked_on_registration=True, user_visible=True, kind='string'
    )
    models.Attribute.objects.create(label='Profession', name='profession', user_editable=True, kind='string')

    response = app.get(reverse('registration_register'))
    response.form.set('email', 'testbot@entrouvert.com')
    response = response.form.submit()
    assert urlparse(response['Location']).path == reverse('registration_complete')

    response = response.follow()
    assert 'Follow the instructions' in response.text
    assert 'testbot@entrouvert.com' in response.text
    assert len(mailoutbox) == 1

    link = get_link_from_mail(mailoutbox[0])

    response = app.get(link)

    for key in ['prenom', 'nom', 'password1', 'password2']:
        assert response.pyquery('#id_%s' % key)

    response.form.set('prenom', 'John')
    response.form.set('nom', 'Doe')
    response.form.set('password1', 'T0==toto')
    response.form.set('password2', 'T0==toto')
    response = response.form.submit()
    assert urlparse(response['Location']).path == reverse('auth_homepage')
    response = response.follow()
    assert 'You have just created an account.' in response.text
    assert len(mailoutbox) == 2
    assert 'was successful' in mailoutbox[1].body

    response = app.get(reverse('account_management'))

    assert 'Nom' in response.text
    assert 'Prénom' not in response.text

    response = app.get(reverse('profile_edit'))
    assert 'profession' in response.form.fields
    assert 'prenom' not in response.form.fields
    assert 'nom' not in response.form.fields

    assert response.pyquery('[for=id_profession]')
    assert not response.pyquery('[for=id_profession].form-field-required')
    response.form.set('profession', 'pompier')
    response = response.form.submit()
    assert urlparse(response['Location']).path == reverse('account_management')

    response = response.follow()

    assert 'Nom' in response.text
    assert 'Doe' in response.text
    assert 'Profession' not in response.text
    assert 'pompier' not in response.text
    assert 'Prénom' not in response.text
    assert 'John' not in response.text


def test_registration_email_blacklist(app, settings, db):
    def test_register(email):
        response = app.get('/register/')
        assert 'email' in response.form.fields
        response.form.set('email', email)
        response = response.form.submit()
        return response.status_code == 302

    settings.A2_REGISTRATION_EMAIL_BLACKLIST = [r'a*@example\.com']
    assert not test_register('aaaa@example.com')
    assert test_register('aaaa@example.com.zob')
    assert test_register('baaaa@example.com')
    settings.A2_REGISTRATION_EMAIL_BLACKLIST = [r'a*@example\.com', r'^ba*@example\.com$']
    assert not test_register('aaaa@example.com')
    assert not test_register('baaaa@example.com')
    assert test_register('bbaaaa@example.com')

    settings.A2_REGISTRATION_EMAIL_BLACKLIST = []
    authenticator = utils_misc.get_password_authenticator()
    authenticator.registration_forbidden_email_domains = ['@example.com']
    authenticator.save()
    assert not test_register('aaa@example.com')
    assert test_register('bbb@example.org')
    assert test_register('aaa@foo.example.com')

    authenticator.registration_forbidden_email_domains = ['example.com', 'example.org']
    authenticator.save()
    assert not test_register('aaa@example.com')
    assert not test_register('bbb@example.org')
    assert test_register('aaa@foo.example.com')

    settings.A2_REGISTRATION_EMAIL_BLACKLIST = [
        r'.*@example\.com',
    ]
    authenticator.registration_forbidden_email_domains = ['example.org']
    authenticator.save()
    assert not test_register('aaa@example.com')
    assert not test_register('bbb@example.org')
    assert test_register('aaa@foo.example.com')


def test_registration_bad_email(app, db, settings):
    settings.LANGUAGE_CODE = 'en-us'

    response = app.post(reverse('registration_register'), params={'email': 'fred@0d..be'}, status=200)
    assert (
        'Please enter a valid email address (example: john.doe@entrouvert.com)'
        in response.context['form'].errors['email']
    )

    response = app.post(reverse('registration_register'), params={'email': 'ééééé'}, status=200)
    assert (
        'Please enter a valid email address (example: john.doe@entrouvert.com)'
        in response.context['form'].errors['email']
    )

    response = app.post(reverse('registration_register'), params={'email': ''}, status=200)
    assert response.pyquery('title')[0].text.endswith('there are errors in the form')
    assert 'This field is required.' in response.context['form'].errors['email']


def test_registration_confirm_data(app, settings, db, rf):
    # make first name not required
    models.Attribute.objects.filter(name='first_name').update(required=False)

    activation_url = utils_misc.build_activation_url(
        rf.post('/register/'),
        email='john.doe@example.com',
        next_url='/',
        first_name='John',
        last_name='Doe',
        no_password=True,
        confirm_data=False,
    )

    response = app.get(activation_url, status=302)

    activation_url = utils_misc.build_activation_url(
        rf.post('/register/'),
        email='john.doe@example.com',
        next_url='/',
        last_name='Doe',
        no_password=True,
        confirm_data=False,
    )

    response = app.get(activation_url, status=200)
    assert 'form' in response.context
    assert set(response.context['form'].fields.keys()) == {'first_name', 'last_name'}

    activation_url = utils_misc.build_activation_url(
        rf.post('/register/'),
        email='john.doe@example.com',
        next_url='/',
        last_name='Doe',
        no_password=True,
        confirm_data='required',
    )
    response = app.get(activation_url, status=302)


def test_revalidate_email(app, rf, db, settings, mailoutbox):
    settings.LANGUAGE_CODE = 'en-us'

    # disable existing attributes
    models.Attribute.objects.update(disabled=True)
    url = utils_misc.build_activation_url(
        rf.get('/'), 'testbot@entrouvert.com', next_url=None, valid_email=False, franceconnect=True
    )

    assert len(mailoutbox) == 0
    # register
    response = app.get(url)
    response.form.set('email', 'johndoe@example.com')
    response.form.set('password1', 'T0==toto')
    response.form.set('password2', 'T0==toto')
    response = response.form.submit()
    assert urlparse(response['Location']).path == reverse('registration_complete')
    response = response.follow()
    assert 'Follow the instructions' in response.text
    assert 'johndoe@example.com' in response.text
    assert len(mailoutbox) == 1


def test_email_is_unique_multiple_objects_returned(app, db, settings, mailoutbox, rf):
    settings.LANGUAGE_CODE = 'en-us'
    settings.A2_REGISTRATION_EMAIL_IS_UNIQUE = True

    # Create two user objects
    User.objects.create(email='testbot@entrouvert.com')
    User.objects.create(email='testbot@entrouvert.com')

    url = utils_misc.build_activation_url(
        rf.get('/'),
        'testbot@entrouvert.com',
        first_name='Test',
        last_name='Bot',
        password='ABcd12345',
        next_url=None,
        valid_email=False,
        franceconnect=True,
    )

    response = app.get(url)
    assert 'This email address is already in use.' in response.text


def test_username_is_unique_multiple_objects_returned(app, db, settings, mailoutbox, rf):
    settings.LANGUAGE_CODE = 'en-us'
    settings.A2_REGISTRATION_USERNAME_IS_UNIQUE = True
    settings.A2_REQUIRED_FIELDS = ['username', 'first_name', 'last_name']

    # Create two user objects
    User.objects.create(username='testbot', email='testbot1@entrouvert.com')
    User.objects.create(username='testbot', email='testbot2@entrouvert.com')

    url = utils_misc.build_activation_url(
        rf.get('/'),
        'testbot@entrouvert.com',
        username='testbot',
        first_name='Test',
        last_name='Bot',
        password='ABcd12345',
        next_url=None,
        valid_email=False,
        franceconnect=True,
    )

    response = app.get(url)
    assert 'This username is already in use.' in response.text


def test_registration_redirect(app, db, settings, mailoutbox, external_redirect):
    next_url, good_next_url = external_redirect

    settings.A2_REGISTRATION_REDIRECT = 'http://cms/welcome/'
    settings.LANGUAGE_CODE = 'en-us'

    new_next_url = settings.A2_REGISTRATION_REDIRECT
    if good_next_url:
        new_next_url += '?next=' + quote(next_url)

    # disable existing attributes
    models.Attribute.objects.update(disabled=True)

    url = utils_misc.make_url('registration_register', params={REDIRECT_FIELD_NAME: next_url})
    response = app.get(url)
    response.form.set('email', 'testbot@entrouvert.com')
    response = response.form.submit()

    assert urlparse(response['Location']).path == reverse('registration_complete')

    response = response.follow()
    assert 'Follow the instructions' in response.text
    assert 'testbot@entrouvert.com' in response.text
    assert len(mailoutbox) == 1

    link = get_link_from_mail(mailoutbox[0])
    response = app.get(link)
    response.form.set('password1', 'T0==toto')
    response.form.set('password2', 'T0==toto')
    response = response.form.submit().follow()

    assert 'You have just created an account.' in response.text
    assert new_next_url in response.text

    assert User.objects.count() == 1
    assert len(mailoutbox) == 2
    assert 'was successful' in mailoutbox[1].body

    new_user = User.objects.get()
    assert new_user.email == 'testbot@entrouvert.com'
    assert new_user.username is None
    assert new_user.check_password('T0==toto')
    assert new_user.is_active
    assert not new_user.is_staff
    assert not new_user.is_superuser
    assert str(app.session['_auth_user_id']) == str(new_user.pk)

    response = app.get('/login/')
    response.form.set('username', 'testbot@entrouvert.com')
    response.form.set('password', 'T0==toto')
    response = response.form.submit(name='login-password-submit')
    assert urlparse(response['Location']).path == reverse('auth_homepage')


def test_registration_redirect_when_authenticated(app, db, settings, admin_ou1):
    login(app, admin_ou1)

    response = app.get('/register/')
    assert 'cancel=/' in response.location
    response = response.follow()
    assert response.pyquery('a:contains("Cancel")').attr.href == '/'

    response = response.click('Cancel')
    # we are still in
    assert response.pyquery('li.ui-name').text() == 'Admin OU1'


def test_registration_redirect_tuple(app, db, settings, mailoutbox, external_redirect):
    next_url, good_next_url = external_redirect

    settings.A2_REGISTRATION_REDIRECT = 'http://cms/welcome/', 'target'

    new_next_url = settings.A2_REGISTRATION_REDIRECT[0]
    if good_next_url:
        new_next_url += '?target=' + quote(next_url)

    # disable existing attributes
    models.Attribute.objects.update(disabled=True)

    url = utils_misc.make_url('registration_register', params={REDIRECT_FIELD_NAME: next_url})
    response = app.get(url)
    response.form.set('email', 'testbot@entrouvert.com')
    response = response.form.submit()
    response = response.follow()
    link = get_link_from_mail(mailoutbox[0])
    response = app.get(link)
    response.form.set('password1', 'T0==toto')
    response.form.set('password2', 'T0==toto')
    response = response.form.submit().follow()
    assert new_next_url in response.text


def test_registration_activate_passwords_not_equal(app, db, settings, mailoutbox):
    settings.LANGUAGE_CODE = 'en-us'
    settings.A2_EMAIL_IS_UNIQUE = True

    response = app.get(reverse('registration_register'))
    response.form.set('email', 'testbot@entrouvert.com')
    response = response.form.submit()
    response = response.follow()
    link = get_link_from_mail(mailoutbox[0])
    response = app.get(link)
    response.form.set('first_name', 'john')
    response.form.set('last_name', 'doe')
    response.form.set('password1', 'azerty12AZ')
    response.form.set('password2', 'AAAazerty12AZ')
    response = response.form.submit()
    assert 'The two password fields didn' in response.text


def test_authentication_method(app, db, rf, hooks):
    activation_url = utils_misc.build_activation_url(
        rf.post('/register/'),
        email='john.doe@example.com',
        next_url='/',
        first_name='John',
        last_name='Doe',
        no_password=True,
        confirm_data=False,
    )
    app.get(activation_url)

    assert len(hooks.calls['event']) == 2
    assert hooks.calls['event'][-2]['kwargs']['name'] == 'registration'
    assert hooks.calls['event'][-2]['kwargs']['authentication_method'] == 'email'
    assert hooks.calls['event'][-1]['kwargs']['name'] == 'login'
    assert hooks.calls['event'][-1]['kwargs']['how'] == 'email'

    activation_url = utils_misc.build_activation_url(
        rf.post('/register/'),
        email='jane.doe@example.com',
        next_url='/',
        first_name='Jane',
        last_name='Doe',
        no_password=True,
        authentication_method='another',
        confirm_data=False,
    )
    app.get(activation_url)

    assert len(hooks.calls['event']) == 4
    assert hooks.calls['event'][-2]['kwargs']['name'] == 'registration'
    assert hooks.calls['event'][-2]['kwargs']['authentication_method'] == 'another'
    assert hooks.calls['event'][-1]['kwargs']['name'] == 'login'
    assert hooks.calls['event'][-1]['kwargs']['how'] == 'another'


def test_registration_with_email_suggestions(app, db, settings):
    url = utils_misc.make_url('registration_register')
    response = app.get(url)
    assert 'email_domains_suggestions.js' in response.text
    assert 'field-live-hint' in response.text

    settings.A2_SUGGESTED_EMAIL_DOMAINS = []
    response = app.get(url)
    assert 'email_domains_suggestions.js' not in response.text
    assert 'field-live-hint' not in response.text


def test_registration_no_email_full_profile_no_password(app, db, rf, mailoutbox):
    models.Attribute.objects.create(kind='birthdate', name='birthdate', label='birthdate', required=True)

    data = {
        'email': 'john.doe@example.com',
        'first_name': 'John',
        'last_name': 'Doe',
        'confirm_data': 'required',
        'no_password': True,
        'valid_email': False,
        'franceconnect': True,
        'authentication_method': 'france-connect',
    }

    activation_url = utils_misc.build_activation_url(rf.post('/register/'), next_url='/', **data)

    response = app.get(activation_url)
    response.form.set('first_name', data['first_name'])
    response.form.set('last_name', data['last_name'])
    response.form.set('birthdate', '1981-01-01')
    response.form.set('email', 'john.doe2@example.com')
    response = response.form.submit().follow()
    link = get_link_from_mail(mailoutbox[0])
    response = app.get(link)
    assert response.location == '/'
    assert User.objects.count() == 1
    user = User.objects.get()
    assert user.first_name == 'John'
    assert user.last_name == 'Doe'
    assert user.attributes.birthdate == date(1981, 1, 1)
    assert user.email == 'john.doe2@example.com'
    assert user.email_verified is True


def test_registration_link_unique_use(app, db, mailoutbox):
    models.Attribute.objects.update(disabled=True)

    response = app.get(reverse('registration_register'))
    response.form.set('email', 'testbot@entrouvert.com')
    response = response.form.submit()

    link = get_link_from_mail(mailoutbox[0])

    response = app.get(link)
    response.form.set('password1', 'T0==toto')

    # Clean sesssion
    app.session.flush()
    # accessing multiple times work
    response = app.get(link)
    response.form.set('password1', 'T0==toto')
    response.form.set('password2', 'T0==toto')
    response = response.form.submit().follow()
    assert 'You have just created an account.' in response.text

    # Clean sesssion
    app.session.flush()
    response = app.get(link)
    assert urlparse(response['Location']).path == reverse('registration_register')
    response = response.follow()
    assert 'activation key is unknown or expired' in response.text


def test_double_registration_impossible(app, db, mailoutbox):
    models.Attribute.objects.update(disabled=True)

    for _ in range(2):
        response = app.get(reverse('registration_register'))
        response.form.set('email', 'testbot@entrouvert.com')
        response = response.form.submit()
    assert len(mailoutbox) == 2

    link1, link2 = get_link_from_mail(mailoutbox[0]), get_link_from_mail(mailoutbox[1])

    response = app.get(link1)
    assert urlparse(response['Location']).path == reverse('registration_register')
    response = response.follow()
    assert 'activation key is unknown or expired' in response.text

    response = app.get(link2)
    response.form.set('password1', 'T0==toto')
    response.form.set('password2', 'T0==toto')
    response = response.form.submit().follow()
    assert 'You have just created an account.' in response.text


def test_registration_email_not_verified_required_and_unrequired_attributes(app, db, rf, mailoutbox):
    models.Attribute.objects.create(
        kind='birthdate', name='birthdate', label='birthdate', asked_on_registration=False
    )
    models.Attribute.objects.create(
        kind='string', name='preferred_color', label='couleur préférée', required=True
    )

    data = {
        'email': 'john.doe@example.com',
        'first_name': 'John',
        'last_name': 'Doe',
        'confirm_data': 'required',
        'no_password': True,
        'valid_email': False,
        'franceconnect': True,
        'authentication_method': 'france-connect',
    }

    activation_url = utils_misc.build_activation_url(rf.post('/register/'), next_url='/', **data)

    response = app.get(activation_url)
    response.form.set('first_name', data['first_name'])
    response.form.set('last_name', data['last_name'])
    response.form.set('preferred_color', 'bleu')
    response.form.set('email', 'john.doe2@example.com')
    response = response.form.submit().follow()
    link = get_link_from_mail(mailoutbox[0])
    response = app.get(link)
    assert response.location == '/'
    assert User.objects.count() == 1
    user = User.objects.get()
    assert user.first_name == 'John'
    assert user.last_name == 'Doe'
    assert user.attributes.birthdate is None
    assert user.attributes.preferred_color == 'bleu'
    assert user.email == 'john.doe2@example.com'
    assert user.email_verified is True


def test_honeypot(app, db, settings, mailoutbox):
    settings.DEFAULT_FROM_EMAIL = 'show only addr <noreply@example.net>'

    response = app.get(utils_misc.make_url('registration_register'))
    response.form.set('email', 'testbot@entrouvert.com')
    response.form.set('robotcheck', True)
    response = response.form.submit()
    response = response.follow()
    assert len(mailoutbox) == 0
    assert 'Your registration request has been refused' in response


def test_registration_name_validation(app, db, mailoutbox):
    resp = app.get(reverse('registration_register'))
    resp.form.set('email', 'testbot@entrouvert.com')
    resp = resp.form.submit().follow()
    link = get_link_from_mail(mailoutbox[0])
    resp = app.get(link)

    resp.form.set('password1', 'T0==toto')
    resp.form.set('password2', 'T0==toto')
    resp.form.set('first_name', '01/01/1871')
    resp.form.set('last_name', 'Doe')
    resp = resp.form.submit()
    assert 'Special characters are not allowed' in resp.text

    resp.form.set('password1', 'T0==toto')
    resp.form.set('password2', 'T0==toto')
    resp.form.set('first_name', 'John')
    resp.form.set('last_name', 'a(a')
    resp = resp.form.submit()
    assert 'Special characters are not allowed' in resp.text

    resp.form.set('password1', 'T0==toto')
    resp.form.set('password2', 'T0==toto')
    resp.form.set('first_name', 'Léo')
    resp.form.set('last_name', 'D\'Équerre')
    resp = resp.form.submit().follow()
    assert 'You have just created an account' in resp.text


def test_attribute_model_autocomplete(app, db, settings, mailoutbox):
    settings.LANGUAGE_CODE = 'en-us'

    response = app.get(reverse('registration_register'))
    response.form.set('email', 'testbot@entrouvert.com')
    response = response.form.submit()
    assert urlparse(response['Location']).path == reverse('registration_complete')

    link = get_link_from_mail(mailoutbox[0])
    response = app.get(link)

    assert response.form['first_name'].attrs['autocomplete'] == 'given-name'
    assert response.form['last_name'].attrs['autocomplete'] == 'family-name'


def test_registration_race_condition(app, db, mailoutbox):
    models.Attribute.objects.update(disabled=True)

    response = app.get(reverse('registration_register'))
    response.form.set('email', 'testbot@entrouvert.com')
    response = response.form.submit()

    link = get_link_from_mail(mailoutbox[0])
    mailoutbox.clear()

    response = app.get(link)
    response.form.set('password1', 'T0==toto')
    response.form.set('password2', 'T0==toto')
    with mock.patch('authentic2.views.models.Token.delete', return_value=(0, {})):
        response = response.form.submit().follow()

    # hypothetical case where token disappeared while user has not been created
    assert 'An error occured' in response.text

    # real case where user was created by previous almost simultaneous request
    User.objects.create(email='testbot@entrouvert.com')
    response = app.get(link)
    response.form.set('password1', 'T0==toto')
    response.form.set('password2', 'T0==toto')
    with mock.patch('authentic2.views.models.Token.delete', return_value=(0, {})):
        response = response.form.submit().follow()

    assert 'You have just created an account.' in response.text
    assert len(mailoutbox) == 0


def test_registration_service_integration(app, service, settings):
    service.home_url = 'https://portail.example.net/'
    service.save()

    response = app.get('/register/?next=https://portail.example.net/page/')
    assert app.session['home_url'] == 'https://portail.example.net/page/'
    assert app.session['service_pk'] == service.pk
    assert response.context['home_ou'] == service.ou
    assert response.context['home_service'] == service
    assert response.context['home_url'] == 'https://portail.example.net/page/'


def test_registration_completion_form(db, simple_user):
    form_class = modelform_factory(get_user_model(), form=RegistrationCompletionForm)
    data = {
        'email': 'jonh.doe@yopmail.com',
        'password': 'blah',
        'password1': 'Password0',
        'password2': 'Password0',
        'date_joined': '2022-02-07',
        'ou': simple_user.ou.pk,
    }

    form = form_class(instance=simple_user, data=data)
    assert form.fields['password1'].widget.min_strength is None
    assert 'password1' not in form.errors

    LoginPasswordAuthenticator.objects.update(min_password_strength=3)
    form = form_class(instance=simple_user, data=data)
    assert form.fields['password1'].widget.min_strength == 3
    assert form.errors['password1'] == ['This password is not strong enough.']


def test_registration_completion(db, app, mailoutbox):
    Attribute.objects.create(
        kind='string',
        label='Favourite Song',
        name='favourite_song',
        asked_on_registration=True,
    )

    Attribute.objects.create(
        kind='boolean',
        label='Favourite Boolean',
        name='favourite_boolean',
        asked_on_registration=True,
    )

    LoginPasswordAuthenticator.objects.update(min_password_strength=3)

    resp = app.get(reverse('registration_register'))
    resp.form.set('email', 'testbot@entrouvert.com')
    resp = resp.form.submit().follow()
    link = get_link_from_mail(mailoutbox[0])
    resp = app.get(link)

    resp.form.set('password1', 'Password0')
    resp.form.set('password2', 'Password0')
    resp.form.set('first_name', 'John')
    resp.form.set('last_name', 'Doe')
    resp.form.set('favourite_song', '0opS 1 D1t iT @GAiN')
    resp.form.set('favourite_boolean', True)

    assert resp.pyquery('input[name=first_name][data-password-strength-input]')
    assert resp.pyquery('input[name=last_name][data-password-strength-input]')
    assert resp.pyquery('input[name=favourite_song][data-password-strength-input]')
    assert not resp.pyquery('input[name=password1][data-password-strength-input]')
    assert not resp.pyquery('input[name=password2][data-password-strength-input]')

    resp = resp.form.submit()
    assert 'This password is not strong enough' in resp.text

    resp.form.set('password1', 'testbot@entrouvert.com')
    resp.form.set('password2', 'testbot@entrouvert.com')
    resp = resp.form.submit()

    assert 'This password is not strong enough' in resp.text

    resp.form.set('password1', '0opS 1 D1t iT @GAiN')
    resp.form.set('password2', '0opS 1 D1t iT @GAiN')
    resp = resp.form.submit()

    assert 'This password is not strong enough' in resp.text

    resp.form.set('favourite_song', 'Baby one more time')
    resp = resp.form.submit()

    assert 'This password is not strong enough' not in resp.text


def test_registration_no_identifier(app, db, settings, phone_activated_authn):
    resp = app.get(reverse('registration_register'))
    resp = resp.form.submit()
    assert 'Please provide an email address or a mobile' in resp.text


def test_registration_erroneous_phone_identifier(app, db, settings, phone_activated_authn):
    resp = app.get(reverse('registration_register'))
    resp.form.set('phone_1', 'thatsnotquiteit')
    resp = resp.form.submit()
    assert (
        'Invalid phone number. Phone number from Metropolitan France must respect local format (e.g. 06 39 98 01 23).'
    ) == resp.pyquery('.error p')[0].text_content().strip()


def test_phone_registration_wrong_code(app, db, settings, phone_activated_authn):
    resp = app.get(reverse('registration_register'))
    resp.form.set('phone_1', '612345678')
    resp = resp.form.submit().follow()
    resp.form.set('sms_code', 'abc')
    resp = resp.form.submit()
    assert not Token.objects.count()
    assert resp.pyquery('ul.errorlist li')[0].text_content() == 'Wrong SMS code.'


def test_phone_registration_wrong_input_code_opaque_url(app, db, settings, phone_activated_authn):
    resp = app.get(reverse('registration_register'))
    resp.form.set('phone_1', '612345678')
    resp = resp.form.submit()
    location = resp.location[:-5] + 'wxyz/'  # oops, something went wrong with the url token
    app.get(location, status=404)
    assert not Token.objects.count()

    location = (
        resp.location[:-5] + 'abcd/'
    )  # oops, something went wrong again although it's a valid uuid format
    app.get(location, status=404)
    assert not Token.objects.count()


def test_phone_registration_expired_code(app, db, settings, freezer, phone_activated_authn):
    resp = app.get(reverse('registration_register'))
    resp.form.set('phone_1', '612345678')
    resp = resp.form.submit().follow()
    code = SMSCode.objects.get()
    resp.form.set('sms_code', code.value)
    freezer.move_to(timedelta(hours=1))
    resp = resp.form.submit()
    assert not Token.objects.count()
    assert resp.pyquery('ul.errorlist li')[0].text_content() == 'The code has expired.'


def test_phone_registration_cancel(app, db, settings, freezer, phone_activated_authn):
    resp = app.get(reverse('registration_register'))
    resp.form.set('phone_1', '612345678')
    resp = resp.form.submit().follow()
    code = SMSCode.objects.get()
    resp.form.set('sms_code', code.value)
    resp.form.submit('cancel').follow()
    assert not Token.objects.count()
    assert not SMSCode.objects.count()


def test_phone_registration_wrong_input(app, db, settings, freezer, phone_activated_authn):
    resp = app.get(reverse('registration_register'))
    resp.form.set('phone_1', '12244666')
    resp = resp.form.submit()
    assert (
        'Invalid phone number. Phone number from Metropolitan France must respect local format (e.g. 06 39 98 01 23).'
    ) == resp.pyquery('.error p')[0].text_content().strip()

    resp.form.set('phone_0', '32')
    resp.form.set('phone_1', '12244')
    resp = resp.form.submit()
    assert (
        'Invalid phone number. Phone number from Belgium must respect local format (e.g. 042 11 22 33).'
    ) == resp.pyquery('.error p')[0].text_content().strip()


def test_phone_registration_improperly_configured(app, db, settings, freezer, caplog, phone_activated_authn):
    settings.SMS_URL = ''

    resp = app.get(reverse('registration_register'))
    resp.form.set('phone_1', '612345678')
    resp = resp.form.submit().follow().maybe_follow()
    assert not Token.objects.count()
    assert not SMSCode.objects.count()
    assert (
        'Something went wrong while trying to send the SMS code to you'
        in resp.pyquery('li.warning')[0].text_content()
    )
    assert caplog.records[0].message == 'settings.SMS_URL is not set'


def test_phone_registration_connection_error(
    app, db, settings, freezer, caplog, phone_activated_authn, sms_service
):
    resp = app.get('/register/')
    resp.form.set('phone_1', '612345678')

    sms_service.mock.replace(
        sms_service.mock.POST, sms_service.url, body=requests.ConnectionError('unreachable')
    )
    resp = resp.form.submit().follow().maybe_follow()
    assert (
        'Something went wrong while trying to send the SMS code to you'
        in resp.pyquery('li.warning')[0].text_content()
    )
    assert (
        caplog.records[0].message
        == 'sms code to +33612345678 using https://foo.whatever.none/ failed: unreachable'
    )


def test_phone_registration_number_already_existing_single_account_duplicate(
    app,
    db,
    settings,
    phone_activated_authn,
    sms_service,
):
    # create one duplicate only
    user = User.objects.create(
        first_name='John',
        last_name='Doe',
        email='john@example.com',
        username='john',
        ou=get_default_ou(),
    )
    user.attributes.phone = '+33612345678'
    user.save()

    resp = app.get(reverse('registration_register'))
    resp.form.set('phone_1', '612345678')
    resp = resp.form.submit().follow()
    code = SMSCode.objects.get()
    assert (
        f'You already have an account for this phone number, use code {code.value} to retrieve it.'
        in sms_service.last_message
    )
    resp.form.set('sms_code', code.value)
    resp = resp.form.submit()
    location = resp.location.split('?')[0]
    resp = resp.follow()
    assert Token.objects.count() == 1

    assert 'Existing accounts are associated with this phone number.' in resp.text
    assert len(resp.pyquery('form')) == 1
    assert resp.pyquery('p a').text() == 'create a new account'
    resp = app.get(f'{location}?create')

    resp.form.set('password1', 'Password0')
    resp.form.set('password2', 'Password0')
    resp.form.set('first_name', 'John')
    resp.form.set('last_name', 'Doe')
    resp = resp.form.submit().follow()
    assert 'You have just created an account' in resp.text
    assert AttributeValue.objects.filter(content='+33612345678').count() == 2
    assert User.objects.filter(first_name='John', last_name='Doe').count() == 2


def test_phone_registration_number_already_existing_create(
    app, db, settings, phone_activated_authn, sms_service
):
    # create duplicates
    for i in range(3):
        user = User.objects.create(
            first_name='John',
            last_name='Doe',
            email=f'john-{i}@example.com',
            username=f'john-{i}',
            ou=get_default_ou(),
        )
        user.attributes.phone = '+33612345678'
        user.save()

    resp = app.get(reverse('registration_register'))
    resp.form.set('phone_1', '612345678')
    resp = resp.form.submit().follow()
    code = SMSCode.objects.get()
    assert (
        f'You already have an account for this phone number, use code {code.value} to retrieve it.'
        in sms_service.last_message
    )
    resp.form.set('sms_code', code.value)
    resp = resp.form.submit()
    location = resp.location.split('?')[0]
    resp = resp.follow()
    assert Token.objects.count() == 1

    assert 'Existing accounts are associated with this phone number.' in resp.text
    # three existing accounts
    assert len(resp.pyquery('form')) == 3
    # the possibility to create a new one
    assert resp.pyquery('p a').text() == 'create a new account'
    resp = app.get(f'{location}?create')

    resp.form.set('password1', 'Password0')
    resp.form.set('password2', 'Password0')
    resp.form.set('first_name', 'John')
    resp.form.set('last_name', 'Doe')
    resp = resp.form.submit().follow()
    assert 'You have just created an account' in resp.text
    assert AttributeValue.objects.filter(content='+33612345678').count() == 4
    assert User.objects.filter(first_name='John', last_name='Doe').count() == 4


def test_phone_registration_number_already_existing_select(app, db, settings, phone_activated_authn):
    user_ids = []

    # create duplicates
    for i in range(3):
        user = User.objects.create(
            first_name='John',
            last_name='Doe',
            email=f'john-{i}@example.com',
            username=f'john-{i}',
            ou=get_default_ou(),
        )
        user.attributes.phone = '+33612345678'
        user.save()
        user_ids.append(user.id)

    resp = app.get(reverse('registration_register'))
    resp.form.set('phone_1', '612345678')
    resp = resp.form.submit().follow()
    code = SMSCode.objects.get()
    resp.form.set('sms_code', code.value)
    resp = resp.form.submit().follow()
    assert Token.objects.count() == 1

    assert 'Existing accounts are associated with this phone number.' in resp.text
    # three existing accounts
    assert len(resp.pyquery('form')) == 3
    # the possibility to create a new one
    assert resp.pyquery('p a').text() == 'create a new account'

    resp.forms[1].submit().follow()
    assert app.session['_auth_user_id'] == str(user_ids[1])


def test_phone_registration_number_already_existing_phone_is_unique(app, db, settings, phone_activated_authn):
    settings.A2_PHONE_IS_UNIQUE = True
    settings.A2_REGISTRATION_PHONE_IS_UNIQUE = True

    # create duplicate
    user = User.objects.create(
        first_name='John',
        last_name='Doe',
        email='john@example.com',
        username='john',
        ou=get_default_ou(),
    )
    user.attributes.phone = '+33612345678'
    user.save()

    resp = app.get(reverse('registration_register'))
    resp.form.set('phone_1', '612345678')
    resp = resp.form.submit().follow()
    code = SMSCode.objects.get()
    resp.form.set('sms_code', code.value)
    resp = resp.form.submit().follow()

    assert app.session['_auth_user_id'] == str(user.id)
    # logged in user is redirected to their homepage
    assert resp.location == '/'


def test_phone_registration_number_already_existing_registration_phone_is_unique(
    app, db, settings, phone_activated_authn
):
    settings.A2_PHONE_IS_UNIQUE = False
    settings.A2_REGISTRATION_PHONE_IS_UNIQUE = True

    user_ids = []

    # create duplicate
    user = User.objects.create(
        first_name='John',
        last_name='Doe',
        email='john@example.com',
        username='john',
        ou=get_default_ou(),
    )
    user.attributes.phone = '+33612345678'
    user.save()
    user_ids.append(user.id)

    resp = app.get(reverse('registration_register'))
    resp.form.set('phone_1', '612345678')
    resp = resp.form.submit().follow()
    code = SMSCode.objects.get()
    resp.form.set('sms_code', code.value)
    resp = resp.form.submit().follow()
    assert Token.objects.count() == 1

    assert app.session['_auth_user_id'] == str(user.id)
    # logged in user is redirected to their homepage
    assert resp.location == '/'


def test_phone_registration_number_already_existing_ou_phone_is_unique(
    app, db, settings, phone_activated_authn
):
    settings.A2_PHONE_IS_UNIQUE = False
    settings.A2_REGISTRATION_PHONE_IS_UNIQUE = False
    ou = get_default_ou()
    ou.phone_is_unique = True
    ou.save()

    user_ids = []

    # create duplicate
    user = User.objects.create(
        first_name='John',
        last_name='Doe',
        email='john@example.com',
        username='john',
        ou=ou,
    )
    user.attributes.phone = '+33612345678'
    user.save()
    user_ids.append(user.id)

    resp = app.get(reverse('registration_register'))
    resp.form.set('phone_1', '612345678')
    resp = resp.form.submit().follow()
    code = SMSCode.objects.get()
    resp.form.set('sms_code', code.value)
    resp = resp.form.submit().follow()
    assert Token.objects.count() == 1

    assert app.session['_auth_user_id'] == str(user.id)
    # logged in user is redirected to their homepage
    assert resp.location == '/'


def test_phone_registration(app, db, settings, phone_activated_authn, sms_service):
    code_length = settings.SMS_CODE_LENGTH
    phone_activated_authn.sms_code_duration = 420
    phone_activated_authn.save()

    assert not SMSCode.objects.count()
    assert not Token.objects.count()
    resp = app.get(reverse('registration_register'))
    assert not resp.pyquery('.pk-mark-optional-fields')
    assert resp.pyquery('.pk-hide-requisiteness')
    resp.form.set('phone_1', '612345678')
    resp = resp.form.submit().follow()
    assert sms_service.last_message.startswith('Your code is')
    code = SMSCode.objects.get()
    assert sms_service.last_message[-code_length:] == code.value
    assert 'Your code is valid for the next 7 minutes' in resp.text
    assert 'The code you received by SMS.' in resp.text
    resp.form.set('sms_code', code.value)
    resp = resp.form.submit().follow()
    assert Token.objects.count() == 1

    resp.form.set('password1', 'Password0')
    resp.form.set('password2', 'Password0')
    resp.form.set('first_name', 'John')
    resp.form.set('last_name', 'Doe')
    resp = resp.form.submit().follow()
    assert 'You have just created an account' in resp.text
    assert app.session['authentication-events'][-1]['how'] == 'phone'

    # account creation is considered an authn, therefore identifier changes require a password input
    assert 'password' in app.get(reverse('phone-change')).form.fields

    user = User.objects.get(first_name='John', last_name='Doe')
    assert user.attributes.phone == '+33612345678'
    assert user.phone_verified_on
    assert not user.email_verified


def test_phone_registration_redirect_url(app, db, settings, phone_activated_authn):
    resp = app.get('/accounts/consents/').follow()
    resp = resp.click('Register!')
    resp.form.set('phone_1', '612345678')
    resp = resp.form.submit().follow()
    code = SMSCode.objects.get()
    resp.form.set('sms_code', code.value)
    resp = resp.form.submit().follow()

    resp.form.set('password1', 'Password0')
    resp.form.set('password2', 'Password0')
    resp.form.set('first_name', 'John')
    resp.form.set('last_name', 'Doe')
    resp = resp.form.submit()
    assert resp.location == '/accounts/consents/'
    resp.follow()
    user = User.objects.get(first_name='John', last_name='Doe')
    assert user.attributes.phone == '+33612345678'


def test_registration_email_address_max_length(app, db):
    resp = app.get('/register/')
    resp.form['email'] = 'a' * 250 + '@entrouvert.com'
    resp = resp.form.submit()
    assert 'Ensure this value has at most 254 characters (it has 265).' in resp.text


def test_already_logged(db, app, simple_user):
    login(app, simple_user)

    # already logged, if we try to register, we are redirect to the logout page...
    resp = app.get('/register/?next=/whatever/')
    assert resp.location == '/logout/?confirm=1&cancel=/whatever/&next=/register/%3Fnext%3D/whatever/'
    resp = resp.follow()

    # with a message of explaining the reason..
    assert 'If you want to register, you need to logout first.' in resp
    assert resp.form['next'].value == '/register/?next=/whatever/'

    # and we can cancel to come back to where we come from...
    assert resp.pyquery('a[href="/whatever/"]').text() == 'Cancel'

    # if we logout...
    resp = resp.form.submit()
    assert resp.location == '/register/?next=/whatever/'

    # then we can register.
    resp = resp.follow()
    assert resp.form['email']


def test_phone_registration_existing_identifier_number(app, db, settings, phone_activated_authn):
    random_user = User.objects.create(
        first_name='foo',
        last_name='bar',
        email='foobar@example.com',
        username='foobar',
    )

    random_user.attributes.phone = '+33612345678'
    random_user.save()

    resp = app.get(reverse('registration_register'))
    resp.form.set('phone_1', '612345678')
    resp = resp.form.submit().follow()
    code = SMSCode.objects.get()
    resp.form.set('sms_code', code.value)
    resp = resp.form.submit().follow()

    resp.form.set('password1', 'Password0')
    resp.form.set('password2', 'Password0')
    resp.form.set('first_name', 'John')
    resp.form.set('last_name', 'Doe')
    resp = resp.form.submit().follow()

    user = User.objects.get(first_name='John', last_name='Doe')
    assert user.attributes.phone == '+33612345678'
    assert user.phone_verified_on
    assert not user.email_verified
    assert user.id != random_user.id


@pytest.mark.parametrize('global_uniqueness', [False, True])
def test_phone_registration_existing_identifier_number_ou_phone_is_unique(
    app, db, settings, phone_activated_authn, global_uniqueness
):
    settings.A2_PHONE_IS_UNIQUE = global_uniqueness  # test message is same for both options

    random_user = User.objects.create(
        first_name='foo',
        last_name='bar',
        email='foobar@example.com',
        username='foobar',
    )
    ou = get_default_ou()
    ou.phone_is_unique = True
    ou.save()

    random_user.attributes.phone = '+33612345678'
    random_user.ou = ou
    random_user.save()

    resp = app.get(reverse('registration_register'))
    resp.form.set('phone_1', '612345678')
    resp = resp.form.submit().follow()
    code = SMSCode.objects.get()
    resp.form.set('sms_code', code.value)
    resp = resp.form.submit().follow()

    assert resp.location == '/'
    resp = resp.follow()
    assert resp.pyquery('.messages .info')[0].text_content() == (
        "You've been logged in with your already-existing account for this identifier."
    )

    assert resp.pyquery('.ui-name')[0].text_content() == 'foo bar'
    assert (
        AttributeValue.objects.filter(
            content='+33612345678', content_type=ContentType.objects.get_for_model(User)
        )
        .order_by('object_id')
        .distinct('object_id')
        .count()
        == 1
    )


def test_open_redirection(app, rf, db):
    BAD_URL = 'https://bad.url.com/'

    request = rf.get(f'/register/?next={BAD_URL}')

    register = RegistrationView()
    register.setup(request)
    assert register.get_form_kwargs()['initial'].get('next_url') != BAD_URL

    request = rf.post('/register/', {'next_url': BAD_URL, 'email': 'john.doe@example.com'})
    register = RegistrationView()
    register.setup(request)
    form = register.get_form()
    assert form.is_valid()
    assert form.cleaned_data['next_url'] == ''
