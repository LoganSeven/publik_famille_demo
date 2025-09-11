# authentic2 - versatile identity manager
# Copyright (C) 2010-2022 Entr'ouvert
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

import json
from datetime import timedelta

import pytest
import responses
from django import VERSION as DJ_VERSION
from django.utils.html import escape
from django.utils.timezone import now
from webtest import Upload

from authentic2.a2_rbac.models import Role
from authentic2.apps.authenticators.models import AddRoleAction, BaseAuthenticator, LoginPasswordAuthenticator
from authentic2.models import Attribute, SMSCode
from authentic2.utils import misc as utils_misc
from authentic2_auth_fc.models import FcAuthenticator
from authentic2_auth_oidc.models import OIDCProvider
from authentic2_auth_saml.models import SAMLAuthenticator, SetAttributeAction

from .utils import assert_event, login, logout, request_select2


def test_authenticators_authorization(app, simple_user, simple_role, admin, superuser):
    simple_user.roles.add(simple_role.get_admin_role())  # grant user access to /manage/

    resp = login(app, simple_user, path='/manage/')
    assert 'Authenticators' not in resp.text
    app.get('/manage/authenticators/', status=403)

    role = Role.objects.get(name='Manager of authenticators')
    simple_user.roles.add(role)

    resp = app.get('/manage/')
    resp = resp.click('Authentication frontends')
    assert 'Authenticators' in resp.text

    logout(app)
    resp = login(app, admin, path='/manage/')
    assert 'Authentication frontends' in resp.text

    resp = resp.click('Authentication frontends')
    assert 'Authenticators' in resp.text

    logout(app)
    resp = login(app, superuser, path='/manage/')
    assert 'Authentication frontends' in resp.text

    resp = resp.click('Authentication frontends')
    assert 'Authenticators' in resp.text


def test_authenticators_password(app, superuser_or_admin, settings, simple_user):
    resp = login(app, superuser_or_admin, path='/manage/authenticators/')
    # Password authenticator already exists
    assert 'Password' in resp.text
    authenticator = LoginPasswordAuthenticator.objects.get()

    resp = resp.click('Configure')
    assert 'Show condition: None' in resp.text
    # cannot delete password authenticator
    assert 'Delete' not in resp.text
    assert 'configuration is not complete' not in resp.text
    app.get('/manage/authenticators/%s/delete/' % authenticator.pk, status=403)

    resp = resp.click('Edit')
    assert list(resp.form.fields) == [
        'csrfmiddlewaretoken',
        'show_condition',
        'button_description',
        'registration_open',
        'registration_forbidden_email_domains',
        'initial-registration_forbidden_email_domains',
        'min_password_strength',
        'password_min_length',
        'remember_me',
        'include_ou_selector',
        'password_regex',
        'password_regex_error_msg',
        'login_exponential_retry_timeout_duration',
        'login_exponential_retry_timeout_factor',
        'login_exponential_retry_timeout_max_duration',
        'login_exponential_retry_timeout_min_duration',
        'emails_ip_ratelimit',
        'sms_ip_ratelimit',
        'emails_address_ratelimit',
        'sms_number_ratelimit',
        None,
    ]

    resp.form['show_condition'] = '}'
    resp = resp.form.submit()
    assert 'could not parse expression: unmatched' in resp.text

    resp.form['show_condition'] = "'backoffice' in login_hint or remote_addr == '1.2.3.4'"
    resp = resp.form.submit().follow()
    assert 'Click "Edit" to change configuration.' not in resp.text
    if DJ_VERSION[0] <= 2:
        assert (
            'Show condition: &#39;backoffice&#39; in login_hint or remote_addr == &#39;1.2.3.4&#39;'
            in resp.text
        )
    else:
        # html-rendered quote characters change in django 3 onwards…
        assert (
            'Show condition: &#x27;backoffice&#x27; in login_hint or remote_addr == &#x27;1.2.3.4&#x27;'
            in resp.text
        )
    assert_event('authenticator.edit', user=superuser_or_admin, session=app.session)
    assert 'Please note that your modification may take 1 minute to be visible.' in resp.text

    resp = resp.click('Edit')
    resp.form['show_condition'] = "remote_addr in dnsbl('ddns.entrouvert.org')"
    resp = resp.form.submit().follow()
    assert 'dnsbl' in resp.text

    # password authenticator cannot be disabled
    assert 'Disable' not in resp.text
    app.get('/manage/authenticators/%s/toggle/' % authenticator.pk, status=403)

    resp = resp.click('Journal of edits')
    assert resp.text.count('edit (show_condition)') == 2

    # cannot add another password authenticator
    resp = app.get('/manage/authenticators/add/')
    assert 'Password' not in resp.text

    # phone authn management feature flag is activated
    settings.A2_ALLOW_PHONE_AUTHN_MANAGEMENT = True

    phone1 = Attribute.objects.create(
        name='another_phone',
        kind='phone_number',
        label='Another phone',
    )
    phone2 = Attribute.objects.create(
        name='yet_another_phone',
        kind='fr_phone_number',
        label='Yet another phone',
    )

    resp = app.get('/manage/authenticators/%s/edit/' % authenticator.pk)
    assert 'Time (in seconds, between 60 and 3600) after which SMS codes expire. Default is 180' in resp.text

    settings.SMS_CODE_DURATION = 240

    resp = app.get('/manage/authenticators/%s/edit/' % authenticator.pk)
    assert resp.form['sms_code_duration'].value == ''

    resp.form['accept_email_authentication'] = False
    resp.form['accept_phone_authentication'] = True
    resp.form['sms_code_duration'] = '1200'
    assert 'Time (in seconds, between 60 and 3600) after which SMS codes expire. Default is 240' in resp.text
    assert resp.form['phone_identifier_field'].options == [
        (str(phone1.id), False, 'Another phone'),
        (str(phone2.id), False, 'Yet another phone'),
    ]
    for i in range(5):
        SMSCode.objects.create(
            phone=f'+3366666666{i}',
            value=str(i) * 6,
            user=simple_user,
            expires=now() + timedelta(hours=5),
        )
    assert not SMSCode.objects.filter(expires__lte=now()).count()
    resp.form['phone_identifier_field'] = phone2.id
    resp.form.submit()
    assert SMSCode.objects.count() == 5
    assert SMSCode.objects.filter(expires__lte=now()).count() == 5

    authenticator.refresh_from_db()
    assert authenticator.accept_email_authentication is False
    assert authenticator.accept_phone_authentication is True
    assert authenticator.phone_identifier_field == phone2
    assert authenticator.sms_code_duration == 1200

    resp = app.get('/manage/authenticators/%s/edit/' % authenticator.pk)
    resp.form['sms_code_duration'] = '4200'  # too high
    resp = resp.form.submit()
    assert resp.pyquery('.error')[0].text_content().strip() == (
        'Ensure that this value is lower than 3600, or leave blank for default value.'
    )
    authenticator.refresh_from_db()
    assert authenticator.sms_code_duration == 1200

    resp.form['sms_code_duration'] = '42'  # too low
    resp = resp.form.submit()
    assert resp.pyquery('.error')[0].text_content().strip() == (
        'Ensure that this value is higher than 60, or leave blank for default value.'
    )
    authenticator.refresh_from_db()
    assert authenticator.sms_code_duration == 1200

    resp.form['sms_code_duration'] = '2442'  # new valid value
    resp = resp.form.submit()
    assert resp.location == f'/manage/authenticators/{authenticator.pk}/detail/'
    resp = resp.follow()
    assert not resp.pyquery('.error')
    authenticator.refresh_from_db()
    assert authenticator.sms_code_duration == 2442

    resp = app.get('/manage/authenticators/%s/edit/' % authenticator.pk)
    resp.form.set('sms_code_duration', '')
    resp = resp.form.submit()
    authenticator.refresh_from_db()
    assert authenticator.sms_code_duration is None

    authenticator.refresh_from_db()
    assert not authenticator.remember_me
    resp = app.get('/manage/authenticators/%s/edit/' % authenticator.pk)
    resp.form.set('remember_me', 300)
    resp = resp.form.submit()
    assert resp.pyquery('.error')[0].text_content().strip() == (
        'Ensure that this value is higher than eight hours, or leave blank for default value.'
    )
    authenticator.refresh_from_db()
    assert not authenticator.remember_me

    resp.form.set('remember_me', 3600 * 24 * 365 * 30)
    resp = resp.form.submit()
    assert resp.pyquery('.error')[0].text_content().strip() == (
        'Ensure that this value is lower than three months, or leave blank for default value.'
    )
    authenticator.refresh_from_db()
    assert not authenticator.remember_me

    resp.form.set('remember_me', 3600 * 24 * 30)
    resp.form.submit().follow()
    authenticator.refresh_from_db()
    assert authenticator.remember_me == 3600 * 24 * 30


@pytest.mark.parametrize(
    'field_value,model_value',
    (
        ('example.com', ['example.com']),
        (' example.com', ['example.com']),
        ('example.com ', ['example.com']),
        (' example.com ', ['example.com']),
        ('example.com, example.org', ['example.com', 'example.org']),
        (' example.com , example.org ', ['example.com', 'example.org']),
    ),
)
def test_authenticators_password_registration_forbidden_email_domains(
    app, superuser, field_value, model_value
):
    authenticator = utils_misc.get_password_authenticator()
    login(app, superuser, path='/manage/authenticators/')
    resp = app.get('/manage/authenticators/%s/edit/' % authenticator.pk)
    resp.form['registration_forbidden_email_domains'] = field_value
    resp.form.submit()

    authenticator.refresh_from_db()
    assert authenticator.registration_forbidden_email_domains == model_value


@pytest.mark.freeze_time('2022-04-19 14:00')
def test_authenticators_password_export(app, superuser):
    resp = login(app, superuser, path='/manage/authenticators/')
    assert LoginPasswordAuthenticator.objects.count() == 1

    resp = resp.click('Configure')
    resp = resp.click('Export')
    assert resp.headers['content-type'] == 'application/json'
    assert (
        resp.headers['content-disposition']
        == 'attachment; filename="export_password_authenticator_20220419.json"'
    )

    authenticator_json = json.loads(resp.text)
    assert authenticator_json == {
        'authenticator_type': 'authenticators.loginpasswordauthenticator',
        'name': '',
        'slug': 'password-authenticator',
        'show_condition': '',
        'button_description': '',
        'button_label': 'Login',
        'registration_open': True,
        'remember_me': None,
        'include_ou_selector': False,
        'min_password_strength': None,
        'password_min_length': 8,
        'password_regex': '',
        'password_regex_error_msg': '',
        'registration_forbidden_email_domains': [],
        'login_exponential_retry_timeout_duration': 1,
        'login_exponential_retry_timeout_factor': 1.8,
        'login_exponential_retry_timeout_max_duration': 3600,
        'login_exponential_retry_timeout_min_duration': 10,
        'emails_ip_ratelimit': '10/h',
        'sms_ip_ratelimit': '10/h',
        'emails_address_ratelimit': '3/d',
        'sms_number_ratelimit': '10/h',
        'ou': None,
        'related_objects': [],
        'accept_email_authentication': True,
        'accept_phone_authentication': False,
        'allow_user_change_email': True,
        'sms_code_duration': None,
    }

    resp = app.get('/manage/authenticators/')
    resp = resp.click('Import')
    authenticator_json['button_description'] = 'test'
    resp.form['authenticator_json'] = Upload(
        'export.json', json.dumps(authenticator_json).encode(), 'application/json'
    )
    resp = resp.form.submit()
    assert LoginPasswordAuthenticator.objects.count() == 1
    assert LoginPasswordAuthenticator.objects.get().button_description == 'test'


@pytest.mark.parametrize('full_scopes_display', [True, False])
@pytest.mark.parametrize('email_linking_option', [True, False])
@pytest.mark.parametrize('authn_links_by_email', [True, False])
def test_authenticators_fc(
    app, superuser, settings, full_scopes_display, email_linking_option, authn_links_by_email
):
    if full_scopes_display:
        settings.A2_FC_DISPLAY_COMMON_SCOPES_ONLY = False
    if email_linking_option:
        settings.A2_FC_DISPLAY_EMAIL_LINKING_OPTION = True
    resp = login(app, superuser, path='/manage/authenticators/')

    resp = resp.click('Add new authenticator')
    resp.form['authenticator'] = 'fc'
    resp = resp.form.submit()
    assert '/edit/' in resp.location

    provider = FcAuthenticator.objects.get()
    provider.link_by_email = authn_links_by_email
    provider.platform = 'test'
    provider.save(update_fields=['link_by_email', 'platform'])
    assert provider.order == -1
    assert not provider.supports_multiaccount

    resp = app.get(provider.get_absolute_url())
    assert 'extra-actions-menu-opener' in resp.text
    assert 'Platform: Integration' in resp.text
    assert 'Scopes: profile (profile), email (email)' in resp.text
    assert 'Client ID' not in resp.text
    assert 'Client Secret' not in resp.text

    resp = resp.click('Edit')
    if email_linking_option or authn_links_by_email:
        assert list(resp.form.fields) == [
            'csrfmiddlewaretoken',
            'show_condition',
            'platform',
            'version',
            'client_id',
            'client_secret',
            'scopes',
            'link_by_email',
            'supports_multiaccount',
            None,
        ]
    else:
        assert list(resp.form.fields) == [
            'csrfmiddlewaretoken',
            'show_condition',
            'platform',
            'version',
            'client_id',
            'client_secret',
            'scopes',
            'supports_multiaccount',
            None,
        ]
    assert 'phone' not in resp.pyquery('#id_scopes').html()
    assert 'address' not in resp.pyquery('#id_scopes').html()
    assert {option[0] for option in resp.form.fields['platform'][0].options} == {'test', 'prod', 'tnew'}

    # django 3 and 4 rendered html discrepancies
    scopes_list = resp.pyquery('#id_scopes li') or resp.pyquery('#id_scopes div')

    if full_scopes_display:
        assert {scope.text_content().strip() for scope in scopes_list} == {
            'given name (given_name)',
            'gender (gender)',
            'birthdate (birthdate)',
            'birthcountry (birthcountry)',
            'birthplace (birthplace)',
            'family name (family_name)',
            'email (email)',
            'usual family name (preferred_username)',
            'core identity (identite_pivot)',
            'profile (profile)',
            'birth profile (birth)',
            'given name (from the RNIPP)',
            'family name (from the RNIPP)',
            'gender (from the RNIPP)',
            'birthcountry (from the RNIPP)',
            'birthplace (from the RNIPP)',
            'birthdate (from the RNIPP)',
            'profile (from the RNIPP)',
            'core identity (from the RNIPP)',
        }
    else:
        assert {scope.text_content().strip() for scope in scopes_list} == {
            'family name (family_name)',
            'given name (given_name)',
            'birthdate (birthdate)',
            'birthplace (birthplace)',
            'birthcountry (birthcountry)',
            'profile (profile)',
            'gender (gender)',
            'usual family name (preferred_username)',
            'email (email)',
        }

    resp.form['platform'] = 'prod'
    resp.form['client_id'] = '211286433e39cce01db448d80181bdfd005554b19cd51b3fe7943f6b3b86ab6k'
    resp.form['client_secret'] = '211286433e39cce01db448d80181bdfd005554b19cd51b3fe7943f6b3b86ab6d'
    resp.form['scopes'] = ['given_name', 'birthdate']
    resp.form['supports_multiaccount'] = True
    resp = resp.form.submit().follow()

    provider.refresh_from_db()
    assert provider.supports_multiaccount

    assert 'Platform: Production' in resp.text
    assert 'Scopes: given name (given_name), birthdate (birthdate)' in resp.text
    assert 'Client ID: 211286433e39cce01db448d80181bdfd005554b19cd51b3fe7943f6b3b86ab6k' in resp.text
    assert 'Client Secret: 211286433e39cce01db448d80181bdfd005554b19cd51b3fe7943f6b3b86ab6d' in resp.text

    resp = app.get('/manage/authenticators/')
    assert 'FranceConnect' in resp.text
    assert 'class="section disabled"' in resp.text

    resp = resp.click('Configure', index=1)
    resp = resp.click('Enable').follow()
    assert 'Authenticator has been enabled.' in resp.text

    resp = app.get('/manage/authenticators/')
    assert 'class="section disabled"' not in resp.text

    provider.refresh_from_db()
    provider.scopes.extend(['phone', 'address'])  # deprecated scopes
    provider.save()

    resp = app.get(provider.get_absolute_url())
    resp = resp.click('Edit')
    resp.form.submit().follow()
    assert {option[0] for option in resp.form.fields['platform'][0].options} == {'prod', 'tnew'}
    provider.refresh_from_db()
    assert 'phone' not in provider.scopes
    assert 'address' not in provider.scopes

    if not full_scopes_display:
        provider.refresh_from_db()
        # 2 valid scopes and 2 invalid ones
        provider.scopes.extend(['profile', 'rnipp_identite_pivot', 'address', 'baz'])
        provider.save()
        resp = app.get(provider.get_absolute_url())
        resp = resp.click('Edit')
        scopes_list = resp.pyquery('#id_scopes li') or resp.pyquery('#id_scopes div')
        # only the valid scopes are added to the form
        assert {scope.text_content().strip() for scope in scopes_list} == {
            'given name (given_name)',
            'family name (family_name)',
            'birthcountry (birthcountry)',
            'birthplace (birthplace)',
            'gender (gender)',
            'usual family name (preferred_username)',
            'email (email)',
            'birthdate (birthdate)',
            'profile (profile)',
            'core identity (from the RNIPP)',
        }


@responses.activate
def test_authenticators_saml(app, superuser, ou1, ou2, saml_metadata_url):
    resp = login(app, superuser, path='/manage/authenticators/')

    resp = resp.click('Add new authenticator')
    resp.form['name'] = 'Test'
    resp.form['authenticator'] = 'saml'
    resp = resp.form.submit()

    authenticator = SAMLAuthenticator.objects.filter(slug='test').get()
    resp = app.get(authenticator.get_absolute_url())
    assert 'Create user if their username does not already exists: Yes' in resp.text
    assert 'Metadata file path' not in resp.text

    assert 'Enable' not in resp.text
    assert 'configuration is not complete' in resp.text

    resp = resp.click('Edit')
    assert resp.pyquery('button#tab-general').attr('class') == 'pk-tabs--button-marker'
    assert not resp.pyquery('button#tab-advanced').attr('class')
    assert 'ou' in resp.form.fields

    resp = resp.form.submit()
    assert 'One of the metadata fields must be filled.' in resp.text

    resp.form['metadata_url'] = 'https://example.com/metadata.xml'
    resp = resp.form.submit().follow()
    assert 'Metadata URL: https://example.com/metadata.xml' in resp.text

    resp = resp.click('Enable').follow()
    assert 'Authenticator has been enabled.' in resp.text

    resp = resp.click('Edit')
    resp.form['attribute_mapping'] = '[{"attribute": "email", "saml_attribute": "mail", "mandatory": false}]'
    resp = resp.form.submit().follow()

    authenticator.refresh_from_db()
    assert authenticator.attribute_mapping == [
        {'attribute': 'email', 'saml_attribute': 'mail', 'mandatory': False}
    ]

    resp = resp.click('Edit')
    assert resp.pyquery('button#tab-advanced').attr('class') == 'pk-tabs--button-marker'

    resp = app.get(authenticator.get_absolute_url())
    resp = resp.click('Journal of edits')
    assert 'edit (metadata_url)' in resp.text


@responses.activate
def test_authenticators_saml_hide_metadata_url_advanced_fields(app, superuser, ou1, ou2, saml_metadata_url):
    authenticator = SAMLAuthenticator.objects.create(slug='idp1')

    resp = login(app, superuser)
    resp = app.get('/manage/authenticators/%s/edit/' % authenticator.pk)
    assert 'Metadata cache time' not in resp.text
    assert 'Metadata HTTP timeout' not in resp.text

    resp.form['metadata_url'] = 'https://example.com/metadata.xml'
    resp = resp.form.submit().follow()

    resp = resp.click('Edit')
    assert 'Metadata cache time' in resp.text
    assert 'Metadata HTTP timeout' in resp.text


def test_authenticators_saml_validate_metadata(app, superuser):
    authenticator = SAMLAuthenticator.objects.create(slug='idp1')

    resp = login(app, superuser)
    resp = app.get('/manage/authenticators/%s/edit/' % authenticator.pk)
    resp.form['metadata'] = 'invalid'

    resp.form['metadata'] = '<a/>'
    resp = resp.form.submit()
    assert 'Invalid metadata, missing tag {urn:oasis:names:tc:SAML:2.0:metadata}EntityDescriptor' in resp.text

    resp.form['metadata'] = (
        '<ns0:EntityDescriptor xmlns:ns0="urn:oasis:names:tc:SAML:2.0:metadata"></ns0:EntityDescriptor>'
    )
    resp = resp.form.submit()
    assert 'Invalid metadata, missing entityID' in resp.text

    resp.form['metadata'] = (
        '<ns0:EntityDescriptor xmlns:ns0="urn:oasis:names:tc:SAML:2.0:metadata"'
        ' entityID="https://example.com"></ns0:EntityDescriptor>'
    )
    resp.form.submit(status=302)


@responses.activate
def test_authenticators_saml_empty_attribute_mapping(app, superuser, saml_metadata_url):
    authenticator = SAMLAuthenticator.objects.create(
        metadata_url='https://example.com/metadata.xml', slug='idp1'
    )

    resp = login(app, superuser)
    resp = app.get('/manage/authenticators/%s/edit/' % authenticator.pk)

    resp.form['attribute_mapping'] = None
    resp = resp.form.submit().follow()

    authenticator.refresh_from_db()
    assert authenticator.attribute_mapping == {}


def test_authenticators_saml_view_metadata(app, superuser):
    authenticator = SAMLAuthenticator.objects.create(slug='idp1')

    resp = login(app, superuser)
    resp = app.get('/manage/authenticators/%s/detail/' % authenticator.pk)

    assert 'Metadata (XML):' not in resp.text
    assert app.get('/manage/authenticators/%s/metadata.xml' % authenticator.pk, status=404)

    authenticator.metadata = '<a><b></b></a>'
    authenticator.save()

    resp = app.get('/manage/authenticators/%s/detail/' % authenticator.pk)
    assert 'Metadata (XML):' in resp.text

    resp = resp.click('View metadata')
    assert resp.text == '<a><b></b></a>'


def test_authenticators_saml_missing_signing_key(app, superuser, settings):
    authenticator = SAMLAuthenticator.objects.create(slug='idp1')

    resp = login(app, superuser)
    resp = app.get(authenticator.get_absolute_url())
    assert 'Signing key is missing' in resp.text

    settings.MELLON_PRIVATE_KEY = 'xxx'
    settings.MELLON_PUBLIC_KEYS = ['yyy']
    resp = app.get(authenticator.get_absolute_url())
    assert 'Signing key is missing' not in resp.text


def test_authenticators_saml_no_name_display(app, superuser, ou1, ou2):
    SAMLAuthenticator.objects.create(metadata='meta1.xml', slug='idp1')

    resp = login(app, superuser, path='/manage/authenticators/')
    assert 'SAML - idp1' in resp.text


@responses.activate
def test_authenticators_saml_name_id_format_select(app, superuser, saml_metadata_url):
    authenticator = SAMLAuthenticator.objects.create(
        metadata_url='https://example.com/metadata.xml', slug='idp1'
    )

    resp = login(app, superuser, path='/manage/authenticators/%s/edit/' % authenticator.pk)
    resp.form['name_id_policy_format'].select(
        text='Persistent (urn:oasis:names:tc:SAML:2.0:nameid-format:persistent)'
    )
    resp.form.submit().follow()

    authenticator.refresh_from_db()
    assert authenticator.name_id_policy_format == 'urn:oasis:names:tc:SAML:2.0:nameid-format:persistent'


def test_authenticators_saml_attribute_lookup(app, superuser):
    authenticator = SAMLAuthenticator.objects.create(metadata='meta1.xml', slug='idp1')
    resp = login(app, superuser, path=authenticator.get_absolute_url())

    resp = resp.click('Add', href='samlattributelookup')
    resp.form['user_field'].select(text='Email address (email)')
    resp.form['saml_attribute'] = 'mail'
    resp = resp.form.submit()
    assert_event('authenticator.related_object.creation', user=superuser, session=app.session)
    assert '#open:samlattributelookup' in resp.location

    resp = resp.follow()
    assert escape('"mail" (from "Email address (email)")') in resp.text

    resp = resp.click('mail')
    resp.form['ignore_case'] = True
    resp = resp.form.submit().follow()
    assert escape('"mail" (from "Email address (email)"), case insensitive') in resp.text
    assert_event('authenticator.related_object.edit', user=superuser, session=app.session)

    Attribute.objects.create(kind='string', name='test', label='Test')
    resp = resp.click('mail')
    resp.form['user_field'].select(text='Test (test)')
    resp = resp.form.submit().follow()
    assert escape('"mail" (from "Test (test)"), case insensitive') in resp.text

    resp = resp.click('Remove', href='samlattributelookup')
    resp = resp.form.submit().follow()
    assert 'Test (test)' not in resp.text
    assert_event('authenticator.related_object.deletion', user=superuser, session=app.session)


def test_authenticators_saml_set_attribute(app, superuser):
    authenticator = SAMLAuthenticator.objects.create(metadata='meta1.xml', slug='idp1')
    resp = login(app, superuser, path=authenticator.get_absolute_url())

    resp = resp.click('Add', href='setattributeaction')
    resp.form['user_field'].select(text='Email address (email)')
    resp.form['saml_attribute'] = 'mail'
    resp = resp.form.submit().follow()
    assert escape('"Email address (email)" from "mail"') in resp.text

    resp = resp.click('mail')
    resp.form['mandatory'] = True
    resp = resp.form.submit().follow()
    assert escape('"Email address (email)" from "mail" (mandatory)') in resp.text


def test_authenticators_saml_add_role(app, superuser, role_ou1, role_ou2):
    authenticator = SAMLAuthenticator.objects.create(metadata='meta1.xml', slug='idp1')
    resp = login(app, superuser, path=authenticator.get_absolute_url())

    resp = resp.click('Add', href='addroleaction')
    select2_json = request_select2(app, resp, term='role_ou')
    assert [x['text'] for x in select2_json['results']] == ['OU1 - role_ou1', 'OU2 - role_ou2']

    resp.form['role'].force_value(select2_json['results'][0]['id'])
    resp = resp.form.submit().follow()
    assert 'role_ou1' in resp.text

    resp = resp.click('role_ou1')
    resp.form['role'].force_value(select2_json['results'][1]['id'])
    resp = resp.form.submit().follow()
    assert 'role_ou1' not in resp.text
    assert 'role_ou2' in resp.text


def test_authenticators_saml_export(app, superuser, simple_role):
    authenticator = SAMLAuthenticator.objects.create(metadata='meta1.xml', slug='idp1')
    SetAttributeAction.objects.create(authenticator=authenticator, user_field='test', saml_attribute='hop')
    AddRoleAction.objects.create(authenticator=authenticator, role=simple_role)

    resp = login(app, superuser, path=authenticator.get_absolute_url())
    export_resp = resp.click('Export')

    SAMLAuthenticator.objects.all().delete()
    SetAttributeAction.objects.all().delete()
    AddRoleAction.objects.all().delete()

    resp = app.get('/manage/authenticators/import/')
    resp.form['authenticator_json'] = Upload('export.json', export_resp.body, 'application/json')
    resp = resp.form.submit().follow()

    authenticator = SAMLAuthenticator.objects.get()
    assert authenticator.slug == 'idp1'
    assert authenticator.metadata == 'meta1.xml'
    assert SetAttributeAction.objects.filter(
        authenticator=authenticator, user_field='test', saml_attribute='hop'
    ).exists()
    assert AddRoleAction.objects.filter(authenticator=authenticator, role=simple_role).exists()


def test_authenticators_order(app, superuser):
    resp = login(app, superuser, path='/manage/authenticators/')

    saml_authenticator = SAMLAuthenticator.objects.create(name='Test', slug='test', enabled=True, order=42)
    SAMLAuthenticator.objects.create(name='Test disabled', slug='test-disabled', enabled=False)
    fc_authenticator = FcAuthenticator.objects.create(slug='fc-authenticator', enabled=True, order=-1)
    password_authenticator = LoginPasswordAuthenticator.objects.get()

    assert fc_authenticator.order == -1
    assert password_authenticator.order == 0
    assert saml_authenticator.order == 42

    resp = resp.click('Edit order')
    assert resp.text.index('FranceConnect') < resp.text.index('Password') < resp.text.index('SAML - Test')
    assert 'SAML - Test disabled' not in resp.text

    resp.form['order'] = '%s,%s,%s' % (saml_authenticator.pk, password_authenticator.pk, fc_authenticator.pk)
    resp.form.submit()

    fc_authenticator.refresh_from_db()
    password_authenticator.refresh_from_db()
    saml_authenticator.refresh_from_db()
    assert fc_authenticator.order == 2
    assert password_authenticator.order == 1
    assert saml_authenticator.order == 0


def test_authenticators_add_last(app, superuser):
    resp = login(app, superuser, path='/manage/authenticators/')

    BaseAuthenticator.objects.all().delete()

    resp = resp.click('Add new authenticator')
    resp.form['name'] = 'Test'
    resp.form['authenticator'] = 'saml'
    resp.form.submit()

    authenticator = SAMLAuthenticator.objects.get()
    assert authenticator.order == 1

    authenticator.order = 42
    authenticator.save()
    resp = app.get('/manage/authenticators/add/')
    resp.form['name'] = 'Test 2'
    resp.form['authenticator'] = 'saml'
    resp.form.submit()

    authenticator = SAMLAuthenticator.objects.filter(slug='test-2').get()
    assert authenticator.order == 43


def test_authenticators_configuration_info(app, superuser, ou1, ou2):
    resp = login(app, superuser, path='/manage/authenticators/')

    resp = resp.click('Add new authenticator')
    assert resp.text.count('infonotice') == 3
    assert '<div class="infonotice saml idp-info">' in resp.text
    assert '<div class="infonotice oidc idp-info">' in resp.text
    assert '<div class="infonotice fc idp-info">' in resp.text
    assert resp.text.count('Configuration information for your identity provider') == 2

    # saml
    assert (
        'Metadata URL:<br><a href="https://testserver/accounts/saml/metadata/" rel="nofollow">'
        'https://testserver/accounts/saml/metadata/</a>'
    ) in resp.text
    assert 'Commonly expected attributes:' in resp.text
    assert 'Email (email)' in resp.text

    authenticator = SAMLAuthenticator.objects.create(metadata='meta1.xml', slug='idp1')
    SetAttributeAction.objects.create(
        authenticator=authenticator, user_field='email', saml_attribute='mail', mandatory=True
    )
    SetAttributeAction.objects.create(
        authenticator=authenticator, user_field='first_name', saml_attribute='given_name'
    )

    resp = app.get('/manage/authenticators/%s/detail/' % authenticator.pk)
    assert 'Information for configuration' in resp.text
    assert 'Expected attributes' in resp.text
    assert 'mail (mandatory)' in resp.text
    assert 'given_name' in resp.text

    # fc
    FcAuthenticator.objects.all().delete()
    add_page = app.get('/manage/authenticators/add/')
    authenticator = FcAuthenticator.objects.create(slug='whatever')
    detail_page = app.get('/manage/authenticators/%s/detail/' % authenticator.pk)
    for resp in (add_page, detail_page):
        assert (
            'Configuration information to declare in your service provider enrollment form (“Datapass”)'
        ) in resp.text
        assert 'Login URL (redirect_uri):' in resp.text
        assert (
            '<a href="https://testserver/fc/callback/" rel="nofollow">https://testserver/fc/callback/</a>'
        ) in resp.text
        assert 'Logout URL (post_logout_redirect_uri):' in resp.text
        assert (
            '<a href="https://testserver/fc/callback_logout/" rel="nofollow">https://testserver/fc/callback_logout/</a>'
        ) in resp.text
        assert 'Signature algorithm: ES256 or RS256' in resp.text

    # oidc
    authenticator = OIDCProvider.objects.create(slug='idp2')
    for url in ('/manage/authenticators/add/', '/manage/authenticators/%s/detail/' % authenticator.pk):
        resp = app.get(url)
        assert (
            'Redirect URI (redirect_uri):<br><a href="https://testserver/accounts/oidc/callback/" '
            'rel="nofollow">https://testserver/accounts/oidc/callback/</a>'
        ) in resp.text
        assert (
            'Redirect URI after logout (post_logout_redirect_uri):<br><a href="https://testserver/logout/" '
            'rel="nofollow">https://testserver/logout/</a>'
        ) in resp.text


def test_authenticators_journal_pages(app, superuser):
    authenticator = LoginPasswordAuthenticator.objects.get()

    # generate login failure event
    login(app, 'noone', password='wrong', fail=True)

    login(app, superuser)
    resp = app.get('/manage/authenticators/%s/edit/' % authenticator.pk)

    # generate edit event
    resp.form['button_description'] = 'abc'
    resp = resp.form.submit().follow()

    resp = app.get('/manage/authenticators/%s/detail/' % authenticator.pk)
    resp = resp.click('Journal of edits')
    assert resp.pyquery('td.journal-list--message-column').text() == 'edit (button_description)'
    assert 'noone' not in resp.text

    resp = app.get('/manage/authenticators/%s/detail/' % authenticator.pk)
    resp = resp.click('Journal of logins')
    assert resp.pyquery('td.journal-list--message-column').text() == 'login failure with username "noone"'
    assert 'edit (button_description)' not in resp.text
