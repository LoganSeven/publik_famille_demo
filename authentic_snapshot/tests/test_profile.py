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


import pytest
from django.urls import reverse

from authentic2.a2_rbac.utils import get_default_ou
from authentic2.apps.authenticators.models import LoginPasswordAuthenticator
from authentic2.models import Attribute, Setting
from authentic2_idp_oidc.models import OIDCClient

from . import utils

pytestmark = pytest.mark.django_db


def test_account_edit_view(app, simple_user, settings):
    utils.login(app, simple_user)
    url = reverse('profile_edit')
    resp = app.get(url, status=200)

    phone = Attribute.objects.create(
        name='phone', label='phone', kind='phone_number', user_visible=True, user_editable=True
    )
    title = Attribute.objects.create(
        name='title', label='title', kind='title', user_visible=True, user_editable=True
    )
    agreement = Attribute.objects.create(
        name='agreement', label='agreement', kind='boolean', user_visible=True, user_editable=True
    )
    language = Attribute.objects.create(
        name='language', label='language', kind='language', user_visible=True, user_editable=True
    )

    resp = old_resp = app.get(url, status=200)
    resp.form['phone_1'] = '123456789'
    assert resp.form['phone_1'].attrs['type'] == 'text'
    resp.form['title'] = 'Mrs'
    resp.form['agreement'] = False
    assert resp.form['language'].tag == 'select'
    assert resp.form['language'].value == 'en'
    resp.form['language'] = 'fr'
    resp = resp.form.submit()
    # verify that missing next_url in POST is ok
    assert resp['Location'].endswith(reverse('account_management'))
    assert phone.get_value(simple_user) == '+33123456789'
    assert title.get_value(simple_user) == 'Mrs'
    assert agreement.get_value(simple_user) is False
    assert language.get_value(simple_user) == 'fr'

    resp = resp.follow()
    profile = [
        (dt.text.split('\xa0')[0], dd.text.strip())
        for dt, dd in zip(resp.pyquery('dl dt'), resp.pyquery('dl dd'))
    ]
    assert profile == [
        ('First name', 'Jôhn'),
        ('Last name', 'Dôe'),
        ('Email address', 'user@example.net'),
        ('Phone', '+33123456789'),
        ('Title', 'Mrs'),
        ('Language', 'French'),
    ]

    resp = app.get(url, status=200)
    resp.form.set('phone_1', '0123456789')
    resp = resp.form.submit().follow()
    assert phone.get_value(simple_user) == '+33123456789'

    resp = app.get(url, status=200)
    resp.form.set('phone_1', '9876543210')
    resp = resp.form.submit('cancel').follow()
    assert phone.get_value(simple_user) == '+33123456789'

    phone.set_value(simple_user, '+33123456789', verified=True)
    title.set_value(simple_user, 'Mr', verified=True)
    agreement.set_value(simple_user, True, verified=True)
    resp = app.get(url, status=200)
    assert 'phone' not in resp.form.fields
    assert 'title' not in resp.form.fields
    assert 'agreement' not in resp.form.fields
    assert 'readonly' in resp.form['phone@disabled'].attrs
    assert resp.form['phone@disabled'].value == '+33123456789'
    assert resp.form['title@disabled'].value == 'Mr'
    assert resp.form['agreement@disabled'].value == 'Yes'
    resp.form.set('phone@disabled', '1234')
    resp.form.set('title@disabled', 'Mrs')
    resp.form.set('agreement@disabled', 'False')
    resp = resp.form.submit().follow()
    assert phone.get_value(simple_user) == '+33123456789'
    assert title.get_value(simple_user) == 'Mr'
    assert agreement.get_value(simple_user) is True

    resp = old_resp.form.submit()
    assert phone.get_value(simple_user) == '+33123456789'
    assert title.get_value(simple_user) == 'Mr'
    assert agreement.get_value(simple_user) is True

    phone.disabled = True
    phone.save()
    resp = app.get(url, status=200)
    assert 'phone@disabled' not in resp
    assert 'title@disabled' in resp
    assert 'agreement@disabled' in resp
    assert phone.get_value(simple_user) == '+33123456789'

    phone.disabled = False
    phone.save()
    LoginPasswordAuthenticator.objects.update(
        accept_phone_authentication=True,
        phone_identifier_field=phone,
    )

    resp = app.get(url, status=200)
    assert 'Phone' not in resp.text
    resp = app.get(reverse('account_management'), status=200)
    profile = [
        (dt.text.split('\xa0')[0], dd.text.strip())
        for dt, dd in zip(resp.pyquery('dl dt'), resp.pyquery('dl dd'))
    ]
    # phone present in /accounts/ overview page
    assert profile == [
        ('First name', 'Jôhn'),
        ('Last name', 'Dôe'),
        ('Email address', 'user@example.net'),
        ('Phone', '+33123456789'),
        ('Title', 'Mr'),
        ('Agreement', 'True'),
        ('Language', 'French'),
    ]

    settings.A2_PROFILE_FIELDS = [attr.name for attr in Attribute.objects.all()]

    resp = app.get(url, status=200)
    assert 'Phone' not in resp.text
    resp = app.get(reverse('account_management'), status=200)
    profile = [
        (dt.text.split('\xa0')[0], dd.text.strip())
        for dt, dd in zip(resp.pyquery('dl dt'), resp.pyquery('dl dd'))
    ]
    assert profile == [
        ('First name', 'Jôhn'),
        ('Last name', 'Dôe'),
        ('Phone', '+33123456789'),
        ('Title', 'Mr'),
        ('Agreement', 'True'),
        ('Language', 'French'),
    ]

    settings.A2_PROFILE_FIELDS = []

    another_phone = Attribute.objects.create(
        name='another_phone',
        label='Another phone',
        kind='phone_number',
        user_visible=True,
        user_editable=True,
    )
    simple_user.attributes.another_phone = '+33122334455'
    simple_user.save()

    resp = app.get(url, status=200)
    assert 'Another phone' in resp.text
    resp = app.get(reverse('account_management'), status=200)
    profile = [
        (dt.text.split('\xa0')[0], dd.text.strip())
        for dt, dd in zip(resp.pyquery('dl dt'), resp.pyquery('dl dd'))
    ]

    assert profile == [
        ('First name', 'Jôhn'),
        ('Last name', 'Dôe'),
        ('Email address', 'user@example.net'),
        ('Phone', '+33123456789'),
        ('Title', 'Mr'),
        ('Agreement', 'True'),
        ('Language', 'French'),
        ('Another phone', '+33122334455'),
    ]

    LoginPasswordAuthenticator.objects.update(phone_identifier_field=another_phone)

    resp = app.get(url, status=200)
    assert 'Another phone' not in resp.text
    resp = app.get(reverse('account_management'), status=200)
    profile = [
        (dt.text.split('\xa0')[0], dd.text.strip())
        for dt, dd in zip(resp.pyquery('dl dt'), resp.pyquery('dl dd'))
    ]

    assert profile == [
        ('First name', 'Jôhn'),
        ('Last name', 'Dôe'),
        ('Email address', 'user@example.net'),
        ('Phone', '+33123456789'),
        ('Title', 'Mr'),
        ('Agreement', 'True'),
        ('Language', 'French'),
        ('Another phone', '+33122334455'),
    ]

    LoginPasswordAuthenticator.objects.update(
        phone_identifier_field=None,
        accept_phone_authentication=False,
    )

    resp = app.get(url, status=200)
    assert 'Another phone' in resp.text
    resp = app.get(reverse('account_management'), status=200)
    profile = [
        (dt.text.split('\xa0')[0], dd.text.strip())
        for dt, dd in zip(resp.pyquery('dl dt'), resp.pyquery('dl dd'))
    ]

    assert profile == [
        ('First name', 'Jôhn'),
        ('Last name', 'Dôe'),
        ('Email address', 'user@example.net'),
        ('Phone', '+33123456789'),
        ('Title', 'Mr'),
        ('Agreement', 'True'),
        ('Language', 'French'),
        ('Another phone', '+33122334455'),
    ]


def test_account_edit_next_url(app, simple_user, external_redirect_next_url, assert_external_redirect):
    utils.login(app, simple_user)
    url = reverse('profile_edit')

    attribute = Attribute.objects.create(
        name='phone', label='phone', kind='string', user_visible=True, user_editable=True
    )

    resp = app.get(url + '?next=%s' % external_redirect_next_url, status=200)
    resp.form.set('phone', '0123456789')
    resp = resp.form.submit()
    assert_external_redirect(resp, reverse('account_management'))
    assert attribute.get_value(simple_user) == '0123456789'

    resp = app.get(url + '?next=%s' % external_redirect_next_url, status=200)
    resp.form.set('phone', '1234')
    resp = resp.form.submit('cancel')
    assert_external_redirect(resp, reverse('account_management'))
    assert attribute.get_value(simple_user) == '0123456789'


def test_account_edit_no_direct_modification_fields(app, simple_user, settings):
    utils.login(app, simple_user)
    url = reverse('profile_edit')

    phone = Attribute.objects.create(
        name='phone', label='phone', kind='string', user_visible=True, user_editable=True, scopes='contact'
    )
    Attribute.objects.create(
        name='mobile',
        label='mobile phone',
        kind='string',
        user_visible=True,
        user_editable=True,
        scopes='contact',
    )

    Attribute.objects.create(
        name='city', label='city', kind='string', user_visible=True, user_editable=True, scopes='address'
    )
    Attribute.objects.create(
        name='zipcode',
        label='zipcode',
        kind='string',
        user_visible=True,
        user_editable=True,
        scopes='address',
    )

    def get_fields(resp):
        return {
            key for key in resp.form.fields.keys() if key and key not in ['csrfmiddlewaretoken', 'cancel']
        }

    resp = app.get(url, status=200)
    assert get_fields(resp) == {'first_name', 'last_name', 'phone', 'mobile', 'city', 'zipcode', 'next_url'}

    LoginPasswordAuthenticator.objects.update(
        accept_phone_authentication=True,
        phone_identifier_field=phone,
    )

    resp = app.get(url, status=200)
    assert get_fields(resp) == {'first_name', 'last_name', 'mobile', 'city', 'zipcode', 'next_url'}

    # profile fields as set by hobo's settings loader
    settings.A2_PROFILE_FIELDS = [attr.name for attr in Attribute.objects.exclude()]

    resp = app.get(url, status=200)
    assert get_fields(resp) == {'first_name', 'last_name', 'mobile', 'city', 'zipcode', 'next_url'}

    # disabling effective authn does not change the identifier nature of the attribute
    LoginPasswordAuthenticator.objects.update(
        accept_phone_authentication=False,
    )

    resp = app.get(url, status=200)
    assert get_fields(resp) == {'first_name', 'last_name', 'mobile', 'city', 'zipcode', 'next_url'}

    # removing any known identifier changes the identifier nature of the attribute
    LoginPasswordAuthenticator.objects.update(
        phone_identifier_field=None,
    )

    resp = app.get(url, status=200)
    assert get_fields(resp) == {'first_name', 'last_name', 'phone', 'mobile', 'city', 'zipcode', 'next_url'}


def test_account_edit_scopes(app, simple_user):
    utils.login(app, simple_user)
    url = reverse('profile_edit')

    Attribute.objects.create(
        name='phone', label='phone', kind='string', user_visible=True, user_editable=True, scopes='contact'
    )
    Attribute.objects.create(
        name='mobile',
        label='mobile phone',
        kind='string',
        user_visible=True,
        user_editable=True,
        scopes='contact',
    )

    Attribute.objects.create(
        name='city', label='city', kind='string', user_visible=True, user_editable=True, scopes='address'
    )
    Attribute.objects.create(
        name='zipcode',
        label='zipcode',
        kind='string',
        user_visible=True,
        user_editable=True,
        scopes='address',
    )

    def get_fields(resp):
        return {
            key for key in resp.form.fields.keys() if key and key not in ['csrfmiddlewaretoken', 'cancel']
        }

    resp = app.get(url, status=200)
    assert get_fields(resp) == {'first_name', 'last_name', 'phone', 'mobile', 'city', 'zipcode', 'next_url'}

    resp = app.get(url + '?scope=contact', status=200)
    assert get_fields(resp) == {'phone', 'mobile', 'next_url'}

    resp = app.get(url + '?scope=address', status=200)
    assert get_fields(resp) == {'city', 'zipcode', 'next_url'}

    resp = app.get(url + '?scope=contact address', status=200)
    assert get_fields(resp) == {'phone', 'mobile', 'city', 'zipcode', 'next_url'}

    resp = app.get(reverse('profile_edit_with_scope', kwargs={'scope': 'contact'}), status=200)
    assert get_fields(resp) == {'phone', 'mobile', 'next_url'}

    resp = app.get(reverse('profile_edit_with_scope', kwargs={'scope': 'address'}), status=200)
    assert get_fields(resp) == {'city', 'zipcode', 'next_url'}


def test_account_edit_locked_title(app, simple_user):
    Attribute.objects.create(name='title', label='title', kind='title', user_visible=True, user_editable=True)
    simple_user.attributes.title = 'Monsieur'

    utils.login(app, simple_user)
    url = reverse('profile_edit')
    response = app.get(url, status=200)
    assert len(response.pyquery('input[type="radio"][name="title"]')) == 2
    assert len(response.pyquery('input[type="radio"][name="title"][readonly="true"]')) == 0
    assert len(response.pyquery('select[name="title"]')) == 0

    simple_user.verified_attributes.title = 'Monsieur'

    response = app.get(url, status=200)
    assert len(response.pyquery('input[type="radio"][name="title"]')) == 0
    assert len(response.pyquery('input[type="text"][name="title@disabled"][readonly]')) == 1


def test_account_view(app, simple_user, settings):
    utils.login(app, simple_user)
    url = reverse('account_management')
    # no oidc client defined -> no authorization management
    response = app.get(url, status=200)
    assert [x['href'] for x in response.html.find('div', {'id': 'a2-profile'}).find_all('a')] == [
        reverse('email-change'),
        reverse('profile_edit'),
        reverse('delete_account'),
    ]

    # oidc client defined -> authorization management
    client = OIDCClient.objects.create(
        name='client', slug='client', ou=get_default_ou(), redirect_uris='https://example.com/'
    )
    response = app.get(url, status=200)
    assert [x['href'] for x in response.html.find('div', {'id': 'a2-profile'}).find_all('a')] == [
        reverse('email-change'),
        reverse('profile_edit'),
        reverse('consents'),
        reverse('delete_account'),
    ]

    # oidc client defined but no authorization mode -> no authorization management
    client.authorization_mode = OIDCClient.AUTHORIZATION_MODE_NONE
    client.save()
    response = app.get(url, status=200)
    assert [x['href'] for x in response.html.find('div', {'id': 'a2-profile'}).find_all('a')] == [
        reverse('email-change'),
        reverse('profile_edit'),
        reverse('delete_account'),
    ]

    # restore authorization mode
    client.authorization_mode = OIDCClient.AUTHORIZATION_MODE_BY_SERVICE
    client.save()

    # disabled authentic2_idp_oidc app -> no authorization management
    settings.INSTALLED_APPS = tuple(x for x in settings.INSTALLED_APPS if x != 'authentic2_idp_oidc')
    url = reverse('account_management')
    response = app.get(url, status=200)
    assert [x['href'] for x in response.html.find('div', {'id': 'a2-profile'}).find_all('a')] == [
        reverse('email-change'),
        reverse('profile_edit'),
        reverse('delete_account'),
    ]
    settings.INSTALLED_APPS += ('authentic2_idp_oidc',)

    phone, dummy = Attribute.objects.get_or_create(
        name='phone',
        label='Phone',
        kind='string',
        user_editable=True,
    )
    LoginPasswordAuthenticator.objects.update(
        phone_identifier_field=phone,
        accept_phone_authentication=True,
    )
    url = reverse('account_management')
    response = app.get(url, status=200)
    assert [x['href'] for x in response.html.find('div', {'id': 'a2-profile'}).find_all('a')] == [
        reverse('email-change'),
        reverse('phone-change'),
        reverse('profile_edit'),
        reverse('consents'),
        reverse('delete_account'),
    ]

    LoginPasswordAuthenticator.objects.update(accept_phone_authentication=False)
    url = reverse('account_management')
    response = app.get(url, status=200)
    assert [x['href'] for x in response.html.find('div', {'id': 'a2-profile'}).find_all('a')] == [
        reverse('email-change'),
        reverse('phone-change'),
        reverse('profile_edit'),
        reverse('consents'),
        reverse('delete_account'),
    ]

    phone.user_editable = False
    phone.save()
    url = reverse('account_management')
    response = app.get(url, status=200)
    assert [x['href'] for x in response.html.find('div', {'id': 'a2-profile'}).find_all('a')] == [
        reverse('email-change'),
        reverse('profile_edit'),
        reverse('consents'),
        reverse('delete_account'),
    ]

    phone.user_editable = True
    phone.disabled = True
    phone.save()
    url = reverse('account_management')
    response = app.get(url, status=200)
    assert [x['href'] for x in response.html.find('div', {'id': 'a2-profile'}).find_all('a')] == [
        reverse('email-change'),
        reverse('profile_edit'),
        reverse('consents'),
        reverse('delete_account'),
    ]
    phone.disabled = False
    phone.save()
    LoginPasswordAuthenticator.objects.update(phone_identifier_field=None)

    # more disabled options -> less actions
    LoginPasswordAuthenticator.objects.update(allow_user_change_email=False)
    can_change = Setting.objects.filter(key='users:can_change_email_address').get()
    can_change.value = True
    can_change.save()
    settings.A2_PROFILE_CAN_MANAGE_SERVICE_AUTHORIZATIONS = False
    settings.A2_REGISTRATION_CAN_DELETE_ACCOUNT = False
    # check that service authz page is unknown due to setting deactivation
    url = reverse('consents')
    response = app.get(url, status=404)
    # only profile edit link is available on main page
    url = reverse('account_management')
    response = app.get(url, status=200)
    print(response.text)
    assert [x['href'] for x in response.html.find('div', {'id': 'a2-profile'}).find_all('a')] == [
        reverse('profile_edit'),
    ]

    LoginPasswordAuthenticator.objects.update(allow_user_change_email=True)
    can_change = Setting.objects.filter(key='users:can_change_email_address').get()
    can_change.value = True
    can_change.save()
    url = reverse('account_management')
    response = app.get(url, status=200)
    assert {x['href'] for x in response.html.find('div', {'id': 'a2-profile'}).find_all('a')} == {
        reverse('profile_edit'),
        reverse('email-change'),
    }

    Setting.objects.filter(key='users:can_change_email_address').delete()
    url = reverse('account_management')
    response = app.get(url, status=200)
    assert {x['href'] for x in response.html.find('div', {'id': 'a2-profile'}).find_all('a')} == {
        reverse('profile_edit'),
        reverse('email-change'),
    }


@pytest.mark.parametrize(
    'setting_allow,auth_allow,expected',
    [(True, True, True), (False, False, False), (False, True, False), (True, False, False)],
)
def test_account_view_change_email(app, simple_user, settings, setting_allow, auth_allow, expected):
    utils.login(app, simple_user)

    LoginPasswordAuthenticator.objects.update(allow_user_change_email=auth_allow)
    can_change = Setting.objects.filter(key='users:can_change_email_address').get()
    can_change.value = setting_allow
    can_change.save()

    expected_links = [reverse('profile_edit'), reverse('delete_account')]
    if expected:
        expected_links.append(reverse('email-change'))

    url = reverse('account_management')
    response = app.get(url, status=200)
    assert {x['href'] for x in response.html.find('div', {'id': 'a2-profile'}).find_all('a')} == set(
        expected_links
    )


def test_account_view_boolean(app, simple_user, settings):
    settings.LANGUAGE_CODE = 'fr'

    Attribute.objects.create(
        name='accept', label='Accept', kind='boolean', user_visible=True, user_editable=True
    )
    simple_user.attributes.accept = True

    utils.login(app, simple_user)
    resp = app.get(reverse('account_management'))
    assert 'Vrai' in resp.text

    simple_user.attributes.accept = False
    resp = app.get(reverse('account_management'))
    assert 'Vrai' not in resp.text


def test_account_profile_completion_ratio(app, simple_user, settings):
    settings.A2_ACCOUNTS_DISPLAY_COMPLETION_RATIO = True
    Attribute.objects.all().delete()
    for i in range(8):
        Attribute.objects.create(
            name=f'attr_{i}',
            label=f'Attribute {i}',
            kind='string',
            disabled=False,
            multiple=False,
            user_visible=True,
            user_editable=True,
        )

    utils.login(app, simple_user)
    resp = app.get(reverse('account_management'))
    assert (
        resp.pyquery('#a2-profile-completion-ratio')[0].text_content().strip()
        == 'You have completed 0% of your user profile.'
    )

    simple_user.attributes.attr_0 = 'foo'
    resp = app.get(reverse('account_management'))
    assert (
        resp.pyquery('#a2-profile-completion-ratio')[0].text_content().strip()
        == 'You have completed 12% of your user profile.'
    )

    simple_user.attributes.attr_1 = 'bar'
    resp = app.get(reverse('account_management'))
    assert (
        resp.pyquery('#a2-profile-completion-ratio')[0].text_content().strip()
        == 'You have completed 25% of your user profile.'
    )

    # test that multiple attribute values don't jinx the stats
    attr_2 = Attribute.objects.get(name='attr_2')
    attr_2.multiple = True
    attr_2.save()
    simple_user.attributes.attr_2 = ['b', 'é', 'p', 'o']
    resp = app.get(reverse('account_management'))
    assert (
        resp.pyquery('#a2-profile-completion-ratio')[0].text_content().strip()
        == 'You have completed 38% of your user profile.'
    )

    # remaining attributes up to 100% completion
    for i, percent in (('3', 50), ('4', 62), ('5', 75), ('6', 88), ('7', 100)):
        setattr(simple_user.attributes, f'attr_{i}', i)
        resp = app.get(reverse('account_management'))
        assert (
            resp.pyquery('#a2-profile-completion-ratio')[0].text_content().strip()
            == f'You have completed {percent}% of your user profile.'
        )

    settings.A2_ACCOUNTS_DISPLAY_COMPLETION_RATIO = False
    resp = app.get(reverse('account_management'))
    assert not resp.pyquery('#a2-profile-completion-ratio')
