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


import base64
import datetime
import hashlib
import re
import urllib.parse
import xml.etree.ElementTree as ET
from unittest import mock

import lasso
import pytest
from django.contrib.auth import REDIRECT_FIELD_NAME
from django.core.files import File
from django.http import HttpResponseRedirect
from django.template import Context, Template
from django.urls import reverse
from django.utils.encoding import force_bytes, force_str
from django.utils.translation import gettext as _

from authentic2.a2_rbac.models import OrganizationalUnit, Role
from authentic2.constants import NONCE_FIELD_NAME
from authentic2.custom_user.models import User
from authentic2.idp.saml import saml2_endpoints
from authentic2.idp.saml.saml2_endpoints import (
    get_extensions,
    get_login_hints_extension,
    get_next_url_extension,
)
from authentic2.models import Attribute, Service, Setting
from authentic2.saml import models as saml_models
from authentic2.saml.models import SAMLAttribute
from authentic2.utils.misc import make_url

from . import utils


@pytest.fixture
def saml_settings(settings):
    settings.A2_IDP_SAML2_ENABLE = True
    settings.A2_LOGIN_DISPLAY_A_CANCEL_BUTTON = True


def get_idp_metadata(app):
    response = app.get('/idp/saml2/metadata')
    # FIXME: add better test of well formedness for metadata
    assert response['Content-type'] == 'text/xml', 'metadata endpoint did not return an XML document'
    assert (
        'IDPSSODescriptor' in response.text
    ), 'metadata endpoint does not contain an IDPSSODescriptor element'
    return response.text


class Raw:
    def __init__(self, d):
        self.__dict__.update(d)


@pytest.fixture
def keys():
    with open('tests/cert.pem') as fd:
        cert = ''.join(fd.read().splitlines()[1:-1])
    with open('tests/key.pem') as fd:
        key = ''.join(fd.read().splitlines()[1:-1])
    return (cert, key)


@pytest.fixture()
def idp(saml_settings, db):
    code_attribute = Attribute.objects.create(kind='string', name='code', label='Code')
    mobile_attribute = Attribute.objects.create(kind='string', name='mobile', label='Mobile')
    avatar_attribute = Attribute.objects.create(kind='profile_image', name='avatar', label='Avatar')
    default_ou = OrganizationalUnit.objects.get()
    return Raw(locals())


@pytest.fixture
def user(idp):
    email = 'john.doe@example.com'
    username = 'john.doe'
    first_name = 'John'
    last_name = 'Doe'
    user = User.objects.create(email=email, username=username, first_name=first_name, last_name=last_name)
    idp.code_attribute.set_value(user, '1234', verified=True)
    idp.mobile_attribute.set_value(user, '5678', verified=True)
    with open('tests/200x200.jpg', 'rb') as fd:
        idp.avatar_attribute.set_value(user, File(fd))
    user.set_password(username)
    user.save()
    return user


class SamlSP:
    METADATA_TPL = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<EntityDescriptor
 entityID="{{ base_url }}/"
 xmlns="urn:oasis:names:tc:SAML:2.0:metadata">
 <SPSSODescriptor
   AuthnRequestsSigned="true"
   WantAssertionsSigned="true"
   protocolSupportEnumeration="urn:oasis:names:tc:SAML:2.0:protocol">
   {% if keys %}
     <KeyDescriptor use="signing">
       <ds:KeyInfo xmlns:ds="http://www.w3.org/2000/09/xmldsig#">
         <ds:X509Data><ds:X509Certificate>{{ keys.0 }}</ds:X509Certificate></ds:X509Data>
       </ds:KeyInfo>
     </KeyDescriptor>
   {% endif %}
   <SingleLogoutService
     Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect"
     Location="https://files.entrouvert.org/mellon/logout" />
   {% if binding == 'post' %}
   <AssertionConsumerService
     index="0"
     isDefault="true"
     Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST"
     Location="{{ base_url }}/sso/POST" />
   {% elif binding == 'artifact' %}
   <AssertionConsumerService
     index="0"
     Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Artifact"
     Location="{{ base_url }}/mellon/artifactResponse" />
   {% endif %}
 </SPSSODescriptor>
</EntityDescriptor>'''

    service = None
    server = None
    binding = 'post'
    keys = None  # pair of public and private key as PEM
    relay_state = 'relay-state'

    def __init__(self, app, **kwargs):
        self.app = app
        self.base_url = 'https://sp.example.com'
        self.name = 'Test SP'
        self.slug = 'test-sp'
        self.idp_entity_idp = ('https://testserver/idp/saml2/metadata',)
        self.default_name_id_format = 'email'
        self.accepted_name_id_format = ['email', 'persistent', 'transient', 'username']
        self.ou = OrganizationalUnit.objects.get()
        self.__dict__.update(kwargs)

        self.provider = saml_models.LibertyProvider(
            name=self.name, slug=self.slug, ou=self.ou, metadata=self.get_metadata()
        )
        self.provider.clean()
        self.provider.save()
        self.service = saml_models.LibertyServiceProvider.objects.create(
            liberty_provider=self.provider, enabled=True
        )
        self.default_sp_options_idp_policy = saml_models.SPOptionsIdPPolicy.objects.create(
            name='Default',
            enabled=True,
            authn_request_signed=False,
            default_name_id_format=self.default_name_id_format,
            accepted_name_id_format=self.accepted_name_id_format,
        )

        # Admin role
        self.admin_role = Role.objects.create(
            name='Administrator', slug='administrator', service=self.provider, is_superuser=True
        )

        # SAML attributes mapping
        self.saml_first_name_attribute = self.provider.attributes.create(
            name_format='basic',
            name='first-name',
            friendly_name='First name',
            attribute_name='django_user_first_name',
        )
        self.saml_last_name_attribute = self.provider.attributes.create(
            name_format='basic',
            name='last-name',
            friendly_name='Last name',
            attribute_name='django_user_last_name',
        )
        self.saml_superuser_attribute = self.provider.attributes.create(
            name_format='basic',
            name='superuser',
            friendly_name='Superuser status',
            attribute_name='superuser',
        )
        self.saml_code_attribute = self.provider.attributes.create(
            name_format='basic', name='code_code', friendly_name='code', attribute_name='django_user_code'
        )
        self.saml_mobile_attribute = self.provider.attributes.create(
            name_format='basic', name='mobile', friendly_name='mobile', attribute_name='django_user_mobile'
        )
        self.saml_verified_attributes = self.provider.attributes.create(
            name_format='basic',
            name='verified_attributes',
            friendly_name='Verified attributes',
            attribute_name='@verified_attributes@',
        )
        self.saml_avatar_attribute = self.provider.attributes.create(
            name_format='basic', name='avatar', friendly_name='Avatar', attribute_name='django_user_avatar'
        )
        self.role_authorized = Role.objects.create(name='PC Delta', slug='pc-delta')
        self.provider.unauthorized_url = 'https://whatever.com/loser/'
        self.provider.save()

    def get_metadata(self):
        return Template(self.METADATA_TPL).render(
            Context(dict(base_url=self.base_url, binding=self.binding, keys=self.keys))
        )

    def get_server(self):
        if not self.server:
            sp_meta = self.get_metadata()
            idp_meta = get_idp_metadata(self.app)
            self.server = lasso.Server.newFromBuffers(sp_meta, self.keys[1] if self.keys else None)
            self.server.signatureMethod = lasso.SIGNATURE_METHOD_RSA_SHA256
            self.server.addProviderFromBuffer(lasso.PROVIDER_ROLE_IDP, force_str(idp_meta))
        return self.server

    def make_authn_request(
        self,
        *,
        entity_id=None,
        method=lasso.HTTP_METHOD_REDIRECT,
        allow_create=True,
        format=lasso.SAML2_NAME_IDENTIFIER_FORMAT_PERSISTENT,
        relay_state=None,
        force_authn=None,
        is_passive=None,
        sp_name_qualifier=None,
        name_id_policy=True,
        login_hints=None,
        next_url=None,
    ):
        server = self.get_server()
        login = self.login = lasso.Login(server)
        if not self.keys:
            login.setSignatureHint(lasso.PROFILE_SIGNATURE_HINT_FORBID)
        login.initAuthnRequest(entity_id, method)
        request = login.request
        policy = request.nameIdPolicy
        if force_authn is not None:
            request.forceAuthn = force_authn
        if is_passive is not None:
            request.isPassive = is_passive
        if allow_create is not None:
            policy.allowCreate = allow_create
        if format is not None:
            policy.format = format
        if sp_name_qualifier is not None:
            policy.spNameQualifier = sp_name_qualifier
        relay_state = relay_state or self.relay_state
        if relay_state is not None:
            login.msgRelayState = force_str(relay_state)
        if not name_id_policy:
            request.nameIdPolicy = None
        request.extensions = lasso.Samlp2Extensions()
        # extension with unicode characters !! test dumping in saml2_endpoints and continue_sso
        extensions = [
            force_str('<extension xmlns="http://example.com/">éé</extension>'),
        ]
        if login_hints:
            extensions.append(
                force_str(
                    '<login-hint xmlns="https://www.entrouvert.com/">%s</login-hint>' % ' '.join(login_hints)
                ),
            )
        if next_url:
            extensions.append(
                force_str('<eo:next_url xmlns:eo="https://www.entrouvert.com/">%s</eo:next_url>' % next_url),
            )
        request.extensions.any = tuple(ext for ext in extensions)
        login.buildAuthnRequestMsg()
        url_parsed = urllib.parse.urlparse(login.msgUrl)
        assert url_parsed.path == reverse('a2-idp-saml-sso'), 'msgUrl should target the sso endpoint'
        if self.keys:
            assert 'rsa-sha256' in login.msgUrl
        return login.msgUrl, login.msgBody, login.msgRelayState, request.id

    def parse_authn_response(self, saml_response):
        login = self.login = lasso.Login(self.get_server())
        login.processAuthnResponseMsg(force_str(saml_response))
        login.acceptSso()

    def parse_artifact_url(self, response):
        login = self.login = lasso.Login(self.get_server())
        if response.location:
            method = lasso.HTTP_METHOD_ARTIFACT_GET
            query_string = response.location.split('?', 1)[1]
            parsed_query_string = urllib.parse.parse_qs(query_string)
            self.relay_state = parsed_query_string.get('RelayState')
            login.msgRelayState = force_str(self.relay_state)
        else:  # lasso.HTTP_METHOD_ARTIFACT_POST, never happens
            raise NotImplementedError
        if not self.keys:
            login.setSignatureHint(lasso.PROFILE_SIGNATURE_HINT_FORBID)
        login.initRequest(force_str(query_string), method)
        login.buildRequestMsg()
        response = self.app.post(
            login.msgUrl, params=force_bytes(login.msgBody), headers={'content-type': 'text/xml'}
        )
        login.processResponseMsg(force_str(response.text))
        login.acceptSso()


class Scenario:
    check_federation = False
    authn_request_login_needed = True

    def __init__(self, app, sp_kwargs=None, make_authn_request_kwargs=None, **kwargs):
        self.app = app
        sp_kwargs = sp_kwargs or {}
        self.sp = SamlSP(app=app, **sp_kwargs)
        self.make_authn_request_kwargs = make_authn_request_kwargs or {}
        self.__dict__.update(kwargs)

    def launch_authn_request(self, requests_params=None):
        requests_params = requests_params or {}

        # Launch an AuthnRequest
        url, body, relay_state, request_id = self.sp.make_authn_request(**self.make_authn_request_kwargs)
        if body is None:
            response = self.app.get(url, **requests_params)
        else:  # post case
            params = {'SAMLRequest': body}
            if relay_state is not None:
                params['RelayState'] = relay_state
            response = self.app.post(url, params=params, **requests_params)

        if self.authn_request_login_needed:
            utils.assert_redirects_complex(
                response,
                reverse('auth_login'),
                **{
                    'nonce': '*',
                    REDIRECT_FIELD_NAME: make_url(
                        'a2-idp-saml-continue', params={NONCE_FIELD_NAME: request_id}
                    ),
                },
            )
            self.nonce = urllib.parse.parse_qs(urllib.parse.urlparse(response['Location']).query)['nonce'][0]
            url = response['Location']
            response = self.app.get(url)
            assert response.status_code == 200
            assert response['Content-Type'].split(';')[0] == 'text/html'
            assert response.pyquery('button.cancel-button[name=cancel]').text() == _('Cancel')
            self.login_page_response = response
        else:
            self.idp_response = response

    def login(self, user):
        response = self.login_page_response
        response.form.set('username', user.username)
        response.form.set('password', user.username)
        response = response.form.submit(name='login-password-submit')
        utils.assert_redirects_complex(response, reverse('a2-idp-saml-continue'), nonce=self.nonce)
        self.idp_response = response.follow()
        return response

    def cancel(self):
        response = self.login_page_response.form.submit(name='cancel')
        utils.assert_redirects_complex(
            response, reverse('a2-idp-saml-continue'), cancel='*', nonce=self.nonce
        )
        self.idp_response = response.follow()
        return response

    def handle_post_response(self):
        response = self.idp_response
        assert response.status_code == 200
        assert response['Content-type'].split(';')[0] == 'text/html'
        assert len(response.forms) == 1
        assert response.form.action == '%s/sso/POST' % self.sp.base_url
        assert 'SAMLResponse' in response.form.fields
        if self.sp.relay_state is not None:
            assert response.form['RelayState'].value == self.sp.relay_state
        saml_response = response.form['SAMLResponse'].value
        decoded_saml_response = base64.b64decode(saml_response)
        assert b'rsa-sha256' in decoded_saml_response
        self.sp.parse_authn_response(saml_response)

    def handle_artifact_response(self):
        response = self.idp_response
        assert response.status_code == 302
        assert response.location.startswith('https://sp.example.com/mellon/artifactResponse?SAMLart=')
        self.sp.parse_artifact_url(response)

    def check_assertion(self, user=None):
        login = self.sp.login
        assertion = login.assertion
        session_not_on_or_after = login.assertion.authnStatement[0].sessionNotOnOrAfter
        assert session_not_on_or_after is not None
        sp_session_expiry_date = datetime.datetime.strptime(session_not_on_or_after, '%Y-%m-%dT%H:%M:%SZ')
        utc_now = datetime.datetime.utcnow()
        assert sp_session_expiry_date > utc_now
        # check session duration on SP is shorter than on IdP
        local_session_expiry_date = self.app.session.get_expiry_date().replace(tzinfo=None)
        assert (sp_session_expiry_date - utc_now) < 0.6 * (local_session_expiry_date - utc_now)

        assertion_xml = assertion.exportToXml()
        namespaces = {
            'saml': lasso.SAML2_ASSERTION_HREF,
        }
        constraints = ()
        # check nameid
        if self.check_federation:
            nid_format = self.make_authn_request_kwargs.get('format')
            if not nid_format:
                name_id = login.assertion.subject.nameID
                if self.sp.default_name_id_format == 'username':
                    assert name_id.format == lasso.SAML2_NAME_IDENTIFIER_FORMAT_UNSPECIFIED
                    assert force_str(name_id.content) == user.username
                elif self.sp.default_name_id_format == 'uuid':
                    assert name_id.format == lasso.SAML2_NAME_IDENTIFIER_FORMAT_UNSPECIFIED
                    assert force_str(name_id.content) == user.uuid
                else:
                    raise NotImplementedError(
                        'unknown default_name_id_format %s' % self.sp.default_name_id_format
                    )
            elif nid_format == lasso.SAML2_NAME_IDENTIFIER_FORMAT_PERSISTENT:
                federation = saml_models.LibertyFederation.objects.get()
                constraints += (
                    ('/saml:Assertion/saml:Subject/saml:NameID', federation.name_id_content),
                    (
                        '/saml:Assertion/saml:Subject/saml:NameID/@Format',
                        lasso.SAML2_NAME_IDENTIFIER_FORMAT_PERSISTENT,
                    ),
                    ('/saml:Assertion/saml:Subject/saml:NameID/@SPNameQualifier', '%s/' % self.sp.base_url),
                )
            elif nid_format == lasso.SAML2_NAME_IDENTIFIER_FORMAT_EMAIL or (
                not nid_format and self.sp.default_name_id_format == 'email'
            ):
                constraints += (
                    ('/saml:Assertion/saml:Subject/saml:NameID', self.email),
                    (
                        '/saml:Assertion/saml:Subject/saml:NameID/@Format',
                        lasso.SAML2_NAME_IDENTIFIER_FORMAT_EMAIL,
                    ),
                )
        constraints += (
            (
                "/saml:Assertion/saml:AttributeStatement/saml:Attribute[@Name='first-name']/@NameFormat",
                lasso.SAML2_ATTRIBUTE_NAME_FORMAT_BASIC,
            ),
            (
                "/saml:Assertion/saml:AttributeStatement/saml:Attribute[@Name='first-name']/@FriendlyName",
                'First name',
            ),
            (
                "/saml:Assertion/saml:AttributeStatement/saml:Attribute[@Name='first-name']/saml:AttributeValue",
                'John',
            ),
            (
                "/saml:Assertion/saml:AttributeStatement/saml:Attribute[@Name='last-name']/@NameFormat",
                lasso.SAML2_ATTRIBUTE_NAME_FORMAT_BASIC,
            ),
            (
                "/saml:Assertion/saml:AttributeStatement/saml:Attribute[@Name='last-name']/@FriendlyName",
                'Last name',
            ),
            (
                "/saml:Assertion/saml:AttributeStatement/saml:Attribute[@Name='last-name']/saml:AttributeValue",
                'Doe',
            ),
            (
                "/saml:Assertion/saml:AttributeStatement/saml:Attribute[@Name='code_code']/@NameFormat",
                lasso.SAML2_ATTRIBUTE_NAME_FORMAT_BASIC,
            ),
            (
                "/saml:Assertion/saml:AttributeStatement/saml:Attribute[@Name='code_code']/@FriendlyName",
                'code',
            ),
            (
                "/saml:Assertion/saml:AttributeStatement/saml:Attribute[@Name='code_code']/saml:AttributeValue",
                '1234',
            ),
            (
                "/saml:Assertion/saml:AttributeStatement/saml:Attribute[@Name='mobile']/@NameFormat",
                lasso.SAML2_ATTRIBUTE_NAME_FORMAT_BASIC,
            ),
            (
                "/saml:Assertion/saml:AttributeStatement/saml:Attribute[@Name='mobile']/@FriendlyName",
                'mobile',
            ),
            (
                "/saml:Assertion/saml:AttributeStatement/saml:Attribute[@Name='mobile']/saml:AttributeValue",
                '5678',
            ),
            (
                "/saml:Assertion/saml:AttributeStatement/saml:Attribute[@Name='avatar']/@NameFormat",
                lasso.SAML2_ATTRIBUTE_NAME_FORMAT_BASIC,
            ),
            (
                "/saml:Assertion/saml:AttributeStatement/saml:Attribute[@Name='avatar']/@FriendlyName",
                'Avatar',
            ),
            (
                "/saml:Assertion/saml:AttributeStatement/saml:Attribute[@Name='avatar']/saml:AttributeValue",
                re.compile('^https://testserver/media/profile-image/.*$'),
            ),
            (
                "/saml:Assertion/saml:AttributeStatement/saml:Attribute[@Name='verified_attributes']/@NameFormat",
                lasso.SAML2_ATTRIBUTE_NAME_FORMAT_BASIC,
            ),
            (
                "/saml:Assertion/saml:AttributeStatement/saml:Attribute[@Name='verified_attributes']/@FriendlyName",
                'Verified attributes',
            ),
            (
                "/saml:Assertion/saml:AttributeStatement/saml:Attribute[@Name='verified_attributes']/saml:AttributeValue",
                {'code_code', 'mobile'},
            ),
        )
        if user is not None and self.sp.admin_role in user.roles.all():
            constraints += (
                (
                    "/saml:Assertion/saml:AttributeStatement/saml:Attribute[@Name='superuser']/@NameFormat",
                    lasso.SAML2_ATTRIBUTE_NAME_FORMAT_BASIC,
                ),
                (
                    "/saml:Assertion/saml:AttributeStatement/saml:Attribute[@Name='superuser']/@FriendlyName",
                    'Superuser status',
                ),
                (
                    "/saml:Assertion/saml:AttributeStatement/saml:Attribute[@Name='superuser']/saml:AttributeValue",
                    'true',
                ),
            )

        utils.assert_xpath_constraints(assertion_xml, constraints, namespaces)


def test_sso_redirect_post(app, idp, user):
    scenario = Scenario(app, sp_kwargs=dict(binding='post'))
    scenario.launch_authn_request()
    scenario.login(user)
    scenario.handle_post_response()
    scenario.check_assertion(user=user)


def test_sso_redirect_post_default_appearance(app, idp, user):
    # check default settings fallback are used for saml services
    colour = Setting.objects.get(key='sso:generic_service_colour')
    colour.value = '#8c22ec'
    colour.save()
    home_url = Setting.objects.get(key='sso:generic_service_home_url')
    home_url.value = 'https://default.example.net'
    home_url.save()
    logo_url = Setting.objects.get(key='sso:generic_service_logo_url')
    logo_url.value = 'https://default.example.net/logo.jpg'
    logo_url.save()
    service_name = Setting.objects.get(key='sso:generic_service_name')
    service_name.value = 'Some default service name'
    service_name.save()

    scenario = Scenario(app, sp_kwargs=dict(binding='post'))
    scenario.launch_authn_request()
    assert scenario.login_page_response.pyquery.find("picture a[href='https://default.example.net']")
    link = scenario.login_page_response.pyquery.find('a.service-message--link')[0]
    assert link.attrib['href'] == 'https://default.example.net'
    assert link.text == 'Some default service name'
    assert 'color: #8c22ec' in scenario.login_page_response.text
    assert (
        scenario.login_page_response.pyquery.find('img.service-message--logo')[0].attrib['src']
        == 'https://default.example.net/logo.jpg'
    )
    assert (
        scenario.login_page_response.pyquery.find('img.service-message--logo')[0].attrib['alt']
        == 'Some default service name'
    )
    scenario.login(user)
    scenario.handle_post_response()
    scenario.check_assertion(user=user)


def test_sso_redirect_post_default_appearance_deactivated_nofx(app, idp, user):
    # check default settings fallback are used for saml services
    colour = Setting.objects.get(key='sso:generic_service_colour')
    colour.value = ''
    colour.save()
    home_url = Setting.objects.get(key='sso:generic_service_home_url')
    home_url.value = ''
    home_url.save()
    logo_url = Setting.objects.get(key='sso:generic_service_logo_url')
    logo_url.value = ''
    logo_url.save()
    service_name = Setting.objects.get(key='sso:generic_service_name')
    service_name.value = ''
    service_name.save()

    scenario = Scenario(app, sp_kwargs=dict(binding='post'))
    scenario.launch_authn_request()
    assert not scenario.login_page_response.pyquery('a.service-message--link')
    assert 'color: #8c22ec' not in scenario.login_page_response.text
    assert not scenario.login_page_response.pyquery('img.service-message--logo')
    scenario.login(user)
    scenario.handle_post_response()
    scenario.check_assertion(user=user)


def test_sso_generic_appearance_accounts_pages(app, idp, user):
    # check default settings fallback are used for saml services
    colour = Setting.objects.get(key='sso:generic_service_colour')
    colour.value = '#8c22ec'
    colour.save()
    home_url = Setting.objects.get(key='sso:generic_service_home_url')
    home_url.value = 'https://default.example.net'
    home_url.save()
    logo_url = Setting.objects.get(key='sso:generic_service_logo_url')
    logo_url.value = 'https://default.example.net/logo.jpg'
    logo_url.save()
    service_name = Setting.objects.get(key='sso:generic_service_name')
    service_name.value = 'Some default service name'
    service_name.save()

    scenario = Scenario(app, sp_kwargs=dict(binding='post'))
    scenario.launch_authn_request()
    scenario.login(user)
    scenario.handle_post_response()
    response = app.get('/accounts/')
    assert '#8c22ec' in response.pyquery('style')[0].text
    assert 'Some default service name' in response.pyquery('#a2-service-information')[0].text
    assert ('class', 'a2-service-information--logo') in response.pyquery('img')[0].items()
    assert ('src', 'https://default.example.net/logo.jpg') in response.pyquery('img')[0].items()
    assert ('alt', 'Some default service name') in response.pyquery('img')[0].items()

    colour.value = ''
    colour.save()
    home_url.value = ''
    home_url.save()
    logo_url.value = ''
    logo_url.save()
    service_name.value = ''
    service_name.save()

    # resetting generic conf disable customization support:
    response = app.get('/accounts/')
    assert not response.pyquery('style')
    assert not response.pyquery('#a2-service-information')


def test_sso_post_post(app, idp, user):
    scenario = Scenario(
        app, make_authn_request_kwargs={'method': lasso.HTTP_METHOD_POST}, sp_kwargs=dict(binding='post')
    )
    scenario.launch_authn_request()
    scenario.login(user)
    scenario.handle_post_response()
    scenario.check_assertion(user=user)


def test_sso_redirect_artifact(app, idp, user, keys):
    scenario = Scenario(app, sp_kwargs=dict(binding='artifact', keys=keys))
    scenario.launch_authn_request()
    scenario.login(user)
    scenario.handle_artifact_response()
    scenario.check_assertion(user=user)


def test_sso_cancel_redirect(app, idp):
    scenario = Scenario(app)
    scenario.launch_authn_request()
    scenario.cancel()
    with pytest.raises(lasso.ProfileRequestDeniedError):
        scenario.handle_post_response()


def test_sso_no_name_id_policy_redirect(app, idp, user):
    scenario = Scenario(app, make_authn_request_kwargs=dict(name_id_policy=False))
    scenario.launch_authn_request()
    scenario.login(user=user)
    scenario.handle_post_response()
    scenario.check_assertion(user=user)


def test_sso_nid_username(app, idp, user):
    scenario = Scenario(
        app,
        sp_kwargs=dict(default_name_id_format='username'),
        make_authn_request_kwargs=dict(name_id_policy=False),
        check_federation=True,
    )
    scenario.launch_authn_request()
    scenario.login(user=user)
    scenario.handle_post_response()
    scenario.check_assertion(user=user)


def test_sso_nid_uuid(app, idp, user):
    scenario = Scenario(
        app,
        sp_kwargs=dict(default_name_id_format='uuid'),
        make_authn_request_kwargs=dict(name_id_policy=False),
        check_federation=True,
    )
    scenario.launch_authn_request()
    scenario.login(user=user)
    scenario.handle_post_response()
    scenario.check_assertion(user=user)


def test_sso_authorized_role_ok(app, idp, user):
    scenario = Scenario(app)
    scenario.sp.provider.add_authorized_role(scenario.sp.role_authorized)
    user.roles.add(scenario.sp.role_authorized)
    scenario.launch_authn_request()
    scenario.login(user=user)
    scenario.handle_post_response()
    scenario.check_assertion(user=user)


def test_sso_authorized_role_nok(app, idp, user):
    scenario = Scenario(app)
    scenario.sp.provider.add_authorized_role(scenario.sp.role_authorized)
    scenario.launch_authn_request()
    scenario.login(user=user)
    assert scenario.idp_response.pyquery('a[href="%s"]' % 'https://whatever.com/loser/').text() == 'Back'
    utils.assert_event(
        'user.service.sso.denial',
        session=app.session,
        user=user,
        service=scenario.sp.provider,
    )


def test_sso_redirect_artifact_login_hints(app, user, keys):
    scenario = Scenario(
        app,
        sp_kwargs=dict(binding='artifact', keys=keys),
        make_authn_request_kwargs={'login_hints': ['backoffice']},
    )
    scenario.launch_authn_request()
    assert app.session['login-hint'] == ['backoffice']
    scenario.login(user)
    scenario.handle_artifact_response()
    scenario.check_assertion(user=user)


def test_sso_redirect_artifact_next_url(app, user, keys):
    scenario = Scenario(
        app,
        sp_kwargs=dict(binding='artifact', keys=keys),
        make_authn_request_kwargs={'next_url': '/foobar/'},
    )
    scenario.launch_authn_request()
    assert app.session['sp_next_url'] == '/foobar/'
    scenario.login(user)
    scenario.handle_artifact_response()
    scenario.check_assertion(user=user)


@pytest.fixture
def add_attributes(rf):
    with mock.patch('authentic2.idp.saml.saml2_endpoints.get_attribute_definitions') as get_definitions:
        with mock.patch(
            'authentic2.idp.saml.saml2_endpoints.get_attributes', wraps=saml2_endpoints.get_attributes
        ) as get_attributes:
            request = rf.get('/', secure=True)
            request.user = None
            assertion = lasso.Saml2Assertion()
            provider = Service(ou=None)

            def func():
                saml2_endpoints.add_attributes(
                    func.request,
                    saml2_endpoints.get_entity_id(func.request),
                    func.assertion,
                    func.provider,
                    func.nid_format,
                )
                return {
                    at.name: {''.join(force_str(mtn.dump()) for mtn in atv.any) for atv in at.attributeValue}
                    for at in assertion.attributeStatement[0].attribute
                }

            func.get_definitions = get_definitions
            func.get_attributes = get_attributes
            func.request = request
            func.assertion = assertion
            func.provider = provider
            func.nid_format = 'transient'

            yield func


def test_add_attributes_empty_assertion(add_attributes):
    '''Verify adding attributes to an otherwise empty assertion'''
    # setup
    add_attributes.get_attributes.return_value = {
        'first_name': ['Éléonore'],
        'last_name': ['Rigby'],
    }
    add_attributes.get_definitions.return_value = [
        SAMLAttribute(name_format='basic', name='prenom', attribute_name='first_name'),
        SAMLAttribute(name_format='basic', name='nom', attribute_name='last_name'),
    ]

    # run
    attributes = add_attributes()

    # check
    assert attributes == {
        'nom': {'Rigby'},
        'prenom': {'Éléonore'},
    }


def test_add_attributes_initialized_assertion(add_attributes):
    '''Verify existing assertion's attributes are preserved'''

    # setup
    add_attributes.get_attributes.return_value = {
        'first_name': ['Éléonore'],
        'last_name': ['Rigby'],
    }
    add_attributes.get_definitions.return_value = [
        SAMLAttribute(name_format='basic', name='prenom', attribute_name='first_name'),
        SAMLAttribute(name_format='basic', name='nom', attribute_name='last_name'),
    ]

    assertion = add_attributes.assertion
    (statement,) = assertion.attributeStatement = [lasso.Saml2AttributeStatement()]
    (attribute,) = statement.attribute = [
        lasso.Saml2Attribute(),
    ]
    attribute.name = 'prenom'
    attribute.nameFormat = lasso.SAML2_ATTRIBUTE_NAME_FORMAT_BASIC
    (atv,) = attribute.attributeValue = [lasso.Saml2AttributeValue()]
    (mtn,) = atv.any = [
        lasso.MiscTextNode.newWithString('coucou'),
    ]
    mtn.textChild = True

    # run
    attributes = add_attributes()

    # check
    assert attributes == {
        'nom': {'Rigby'},
        'prenom': {'Éléonore', 'coucou'},
    }


@pytest.fixture
def profile():
    server = lasso.Server()
    profile = lasso.Login(server)
    profile.request = lasso.Samlp2AuthnRequest()
    yield profile


def test_get_extensions(profile):
    assert not get_extensions(profile)

    profile.request.extensions = lasso.Samlp2Extensions()
    profile.request.extensions.any = (force_str('<extension attribute="1"/>'),)

    extensions = get_extensions(profile)
    assert len(extensions) == 1, 'there should be one extension node'
    assert extensions[0].tag == 'extension'
    assert extensions[0].attrib['attribute'] == '1'


def test_get_login_hints_extension(profile):
    assert get_login_hints_extension(profile) == set()

    extensions = [
        '<login-hint xmlns="https://www.entrouvert.com/">backoffice saint-machin-truc</login-hint>',
        '<extension attribute="1"/>',
        '<login-hint xmlns="https://www.entrouvert.com/">toto@example.com</login-hint>',
    ]

    profile.request.extensions = lasso.Samlp2Extensions()
    profile.request.extensions.any = tuple(force_str(ext) for ext in extensions)

    login_hints = get_login_hints_extension(profile)
    assert login_hints == {'backoffice', 'saint-machin-truc', 'toto@example.com'}


def test_get_next_url_extension(profile):
    assert get_next_url_extension(profile) is None
    extensions = [
        '<eo:next_url xmlns:eo="https://www.entrouvert.com/">/foobar/</eo:next_url>',
    ]

    profile.request.extensions = lasso.Samlp2Extensions()
    profile.request.extensions.any = tuple(force_str(ext) for ext in extensions)
    sp_next_url = get_next_url_extension(profile)
    assert sp_next_url == '/foobar/'


def test_make_edu_person_targeted_id(db, settings, rf):
    user = User.objects.create(username='a')
    provider = saml_models.LibertyProvider(entity_id='https://sp.com/')

    assert saml2_endpoints.make_edu_person_targeted_id_value(provider, user) is None

    settings.A2_IDP_SAML2_EDU_PERSON_TARGETED_ID_SALT = 'b'
    settings.A2_IDP_SAML2_EDU_PERSON_TARGETED_ID_ATTRIBUTE = 'username'

    assert (
        saml2_endpoints.make_edu_person_targeted_id_value(provider, user)
        == '_A485C0ACEEF43A6D39145F5CFE25D9D3B6F15DC6443F412263C76D81C72DA8D5'
    )

    assert (
        saml2_endpoints.make_edu_person_targeted_id_value(provider, user)
        == '_' + hashlib.sha256(b'b' + b'https://sp.com/' + b'a').hexdigest().upper()
    )

    edpt = saml2_endpoints.make_edu_person_targeted_id(
        'https://testserver/idp/saml2/metadata', provider, user
    )
    assert edpt is not None
    node = lasso.Node.newFromXmlNode(force_str(ET.tostring(edpt)))
    assert isinstance(node, lasso.Saml2NameID)
    assert force_str(node.content) == '_A485C0ACEEF43A6D39145F5CFE25D9D3B6F15DC6443F412263C76D81C72DA8D5'
    assert node.format == lasso.SAML2_NAME_IDENTIFIER_FORMAT_PERSISTENT

    assert node.nameQualifier == 'https://testserver/idp/saml2/metadata'
    assert node.spNameQualifier == 'https://sp.com/'


def test_add_attributes_edu_person_targeted_id_nid_format(db, settings, rf, add_attributes):
    # setup
    user = User.objects.create(username='a', first_name='John', last_name='Rambo')

    settings.A2_IDP_SAML2_EDU_PERSON_TARGETED_ID_SALT = 'b'
    settings.A2_IDP_SAML2_EDU_PERSON_TARGETED_ID_ATTRIBUTE = 'username'
    add_attributes.provider.entity_id = 'https://sp.com/'
    add_attributes.request.user = user
    add_attributes.nid_format = 'edupersontargetedid'
    add_attributes.get_definitions.return_value = [
        SAMLAttribute(name_format='basic', name='prenom', attribute_name='django_user_first_name'),
        SAMLAttribute(name_format='basic', name='nom', attribute_name='django_user_last_name'),
    ]

    # run
    attributes = add_attributes()

    # check
    assert len(attributes) == 3
    assert attributes['nom'] == {'Rambo'}
    assert attributes['prenom'] == {'John'}
    edu_name = 'urn:oid:1.3.6.1.4.1.5923.1.1.1.10'

    assert len(attributes[edu_name]) == 1
    node = lasso.Node.newFromXmlNode(force_str(list(attributes[edu_name])[0]))
    assert isinstance(node, lasso.Saml2NameID)
    assert force_str(node.content) == '_A485C0ACEEF43A6D39145F5CFE25D9D3B6F15DC6443F412263C76D81C72DA8D5'
    assert node.format == lasso.SAML2_NAME_IDENTIFIER_FORMAT_PERSISTENT
    assert node.nameQualifier == 'https://testserver/idp/saml2/metadata'
    assert node.spNameQualifier == 'https://sp.com/'


def test_add_attributes_edu_person_targeted_id_attribute(db, settings, rf, add_attributes):
    # setup
    user = User.objects.create(username='a', first_name='John', last_name='Rambo')

    settings.A2_IDP_SAML2_EDU_PERSON_TARGETED_ID_SALT = 'b'
    settings.A2_IDP_SAML2_EDU_PERSON_TARGETED_ID_ATTRIBUTE = 'username'
    add_attributes.provider.entity_id = 'https://sp.com/'
    add_attributes.request.user = user
    add_attributes.nid_format = 'transient'
    add_attributes.get_definitions.return_value = [
        SAMLAttribute(name_format='basic', name='prenom', attribute_name='django_user_first_name'),
        SAMLAttribute(name_format='basic', name='nom', attribute_name='django_user_last_name'),
        SAMLAttribute(name_format='basic', name='edupersontargetedid', attribute_name='edupersontargetedid'),
    ]

    # run
    attributes = add_attributes()

    # check
    assert len(attributes) == 3
    assert attributes['nom'] == {'Rambo'}
    assert attributes['prenom'] == {'John'}

    assert len(attributes['edupersontargetedid']) == 1
    node = lasso.Node.newFromXmlNode(force_str(list(attributes['edupersontargetedid'])[0]))
    assert isinstance(node, lasso.Saml2NameID)
    assert force_str(node.content) == '_A485C0ACEEF43A6D39145F5CFE25D9D3B6F15DC6443F412263C76D81C72DA8D5'
    assert node.format == lasso.SAML2_NAME_IDENTIFIER_FORMAT_PERSISTENT
    assert node.nameQualifier == 'https://testserver/idp/saml2/metadata'
    assert node.spNameQualifier == 'https://sp.com/'


@pytest.fixture
def add_attributes_all(add_attributes):
    add_attributes.provider.entity_id = 'https://sp.com/'
    add_attributes.nid_format = 'transient'
    attribute_names = [
        # django_user source
        'django_user_id',
        'django_user_password',
        'django_user_last_login',
        'django_user_is_superuser',
        'django_user_uuid',
        'django_user_username',
        'django_user_first_name',
        'django_user_last_name',
        'django_user_email',
        'django_user_email_verified',
        'django_user_is_staff',
        'django_user_is_active',
        'django_user_ou',
        'django_user_date_joined',
        'django_user_modified',
        'django_user_last_account_deletion_alert',
        'django_user_deleted',
        'django_user_ou_uuid',
        'django_user_ou_slug',
        'django_user_ou_name',
        'django_user_birthdate',
        'django_user_groups',
        'django_user_group_names',
        'django_user_domain',
        'django_user_identifier',
        'django_user_full_name',
        'a2_role_slugs',
        'a2_role_names',
        'a2_role_uuids',
        'a2_service_ou_role_slugs',
        'a2_service_ou_role_names',
        'a2_service_ou_role_uuids',
    ]
    add_attributes.get_definitions.return_value = list(
        SAMLAttribute(name_format='basic', name=name, attribute_name=name) for name in attribute_names
    )

    def func(user):
        add_attributes.request.user = user
        return add_attributes()

    for key in dir(add_attributes):
        if not key.startswith(('func_', '__')):
            setattr(func, key, getattr(add_attributes, key))
    return func


def test_add_attributes_user_ou1_role_ou2(add_attributes_all, user_ou1, role_ou2, ou1):
    Attribute.objects.create(kind='birthdate', name='birthdate', label='birthdate', required=False)
    user_ou1.roles.add(role_ou2)
    user_ou1.attributes.birthdate = datetime.date(1970, 1, 1)

    add_attributes_all.provider.slug = 'provider'
    add_attributes_all.provider.name = 'Provider'
    add_attributes_all.provider.ou = ou1
    add_attributes_all.provider.save()

    service_role = Role.objects.create(
        name='Role of service',
        slug='role-of-service',
        ou=ou1,
        service=add_attributes_all.provider,
        is_superuser=True,
    )

    user_ou1.roles.add(service_role)

    add_attributes_all.get_definitions.return_value.append(
        SAMLAttribute(name_format='basic', name='is_superuser', attribute_name='is_superuser'),
    )

    attributes = add_attributes_all(user_ou1)
    assert attributes == {
        'a2_role_names': {'Role of service', 'role_ou2'},
        'a2_role_slugs': {'role-of-service', 'role_ou2'},
        'a2_role_uuids': {service_role.uuid, role_ou2.uuid},
        'a2_service_ou_role_names': {'Role of service'},
        'a2_service_ou_role_slugs': {'role-of-service'},
        'a2_service_ou_role_uuids': {service_role.uuid},
        'django_user_birthdate': {'1970-01-01'},
        'django_user_date_joined': {str(user_ou1.date_joined)},
        'django_user_deleted': set(),
        'django_user_domain': {''},
        'django_user_email': {'john.doe@example.net'},
        'django_user_email_verified': {'false'},
        'django_user_first_name': {'J\xf4hn'},
        'django_user_full_name': {'J\xf4hn D\xf4e'},
        'django_user_group_names': set(),
        'django_user_groups': set(),
        'django_user_id': {str(user_ou1.id)},
        'django_user_identifier': {'john.doe'},
        'django_user_is_active': {'true'},
        'django_user_is_staff': {'false'},
        'django_user_is_superuser': {'false'},
        'django_user_last_account_deletion_alert': set(),
        'django_user_last_login': set(),
        'django_user_last_name': {'D\xf4e'},
        'django_user_modified': {str(user_ou1.modified)},
        'django_user_ou': set(),
        'django_user_ou_name': {'OU1'},
        'django_user_ou_slug': {'ou1'},
        'django_user_ou_uuid': {ou1.uuid},
        'django_user_password': {user_ou1.password},
        'django_user_username': {'john.doe'},
        'django_user_uuid': {user_ou1.uuid},
        'is_superuser': {'true'},
    }


def test_metadata_with_openssl_public_key(app, idp, settings):
    settings.A2_IDP_SAML2_SIGNATURE_PUBLIC_KEY = '''-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAvxFkfPdndlGgQPDZgFGX
brNAc/79PULZBuNdWFHDD9P5hNhZn9Kqm4Cp06Pe/A6u+g5wLnYvbZQcFCgfQAEz
ziJtb3J55OOlB7iMEI/T2AX2WzrUH8QT8NGhABONKU2Gg4XiyeXNhH5R7zdHlUwc
Wq3ZwNbtbY0TVc+n665EbrfV/59xihSqsoFrkmBLH0CoepUXtAzA7WDYn8AzusIu
Mx3n8844pJwgxhTB7Gjuboptlz9Hri8JRdXiVT9OS9Wt69ubcNoM6zuKASmtm48U
uGnhj8v6XwvbjKZrL9kA+xf8ziazZfvvw/VGTm+IVFYB7d1x457jY5zjjXJvNyso
owIDAQAB
-----END PUBLIC KEY-----'''
    app.get('/idp/saml2/metadata')


def test_null_character_nonce(app, db):
    response = app.get('/idp/saml2/continue/', params={'nonce': '\0'}, status=400)
    assert response.text == 'null character in query string'


def test_sso_is_passive_and_view_restriction(app, idp, user, cgu_attribute, caplog):
    utils.login(app, user)

    scenario = Scenario(
        app,
        make_authn_request_kwargs={'is_passive': True},
        authn_request_login_needed=False,
    )
    scenario.launch_authn_request()

    assert 'view restriction and passive request, returning NoPassive' in caplog.text
    with pytest.raises(lasso.ProfileStatusNotSuccessError):
        scenario.handle_post_response()

    assert (
        scenario.sp.login.response.status.statusCode.value == 'urn:oasis:names:tc:SAML:2.0:status:Responder'
    )
    assert (
        scenario.sp.login.response.status.statusCode.statusCode.value
        == 'urn:oasis:names:tc:SAML:2.0:status:NoPassive'
    )


def test_sso_view_restriction(app, idp, user, cgu_attribute):
    scenario = Scenario(
        app,
    )
    scenario.launch_authn_request()
    scenario.login(user=user)
    assert scenario.idp_response.location.startswith('/accounts/edit/required/?')


def test_sso_is_passive(app, idp, user, cgu_attribute, caplog):
    scenario = Scenario(
        app,
        make_authn_request_kwargs={'is_passive': True},
        authn_request_login_needed=False,
    )
    scenario.launch_authn_request()

    with pytest.raises(lasso.ProfileStatusNotSuccessError):
        scenario.handle_post_response()

    assert (
        scenario.sp.login.response.status.statusCode.value == 'urn:oasis:names:tc:SAML:2.0:status:Responder'
    )
    assert (
        scenario.sp.login.response.status.statusCode.statusCode.value
        == 'urn:oasis:names:tc:SAML:2.0:status:NoPassive'
    )


def test_sso_with_authenticator_passive_sso_canceled(app, idp):
    scenario = Scenario(
        app,
        make_authn_request_kwargs={'is_passive': True},
        authn_request_login_needed=False,
    )

    authenticator = mock.Mock()
    authenticator.show.return_value = True

    mock_passive_login = mock.Mock()

    def passive_login(request, block_id, next_url, passive=None):
        mock_passive_login(request=request, block_id=block_id, next_url=next_url, passive=passive)
        return HttpResponseRedirect('https://idp.example.com/?passive')

    authenticator.passive_login = passive_login

    with mock.patch('authentic2.utils.misc.get_authenticators', return_value=[authenticator]):
        scenario.launch_authn_request()

    assert scenario.idp_response.location == 'https://idp.example.com/?passive'
    assert mock_passive_login.call_args[1]['next_url'].startswith('/idp/saml2/continue?nonce=')

    # check NoPassive status code response after if conitnue is called and still no user is logged in
    response = app.get(mock_passive_login.call_args[1]['next_url'])
    scenario.idp_response = response
    with pytest.raises(lasso.ProfileStatusNotSuccessError):
        scenario.handle_post_response()
    assert (
        scenario.sp.login.response.status.statusCode.value == 'urn:oasis:names:tc:SAML:2.0:status:Responder'
    )
    assert (
        scenario.sp.login.response.status.statusCode.statusCode.value
        == 'urn:oasis:names:tc:SAML:2.0:status:NoPassive'
    )


def test_sso_with_authenticator_passive_sso_authenticated(app, idp, user, monkeypatch):
    scenario = Scenario(
        app,
        make_authn_request_kwargs={'is_passive': True},
        authn_request_login_needed=False,
    )

    authenticator = mock.Mock()
    authenticator.show.return_value = True

    mock_passive_login = mock.Mock()

    def passive_login(request, block_id, next_url, passive=None):
        mock_passive_login(request=request, block_id=block_id, next_url=next_url, passive=passive)
        return HttpResponseRedirect('https://idp.example.com/?passive')

    authenticator.passive_login = passive_login

    with mock.patch('authentic2.utils.misc.get_authenticators', return_value=[authenticator]):
        scenario.launch_authn_request()

    assert scenario.idp_response.location == 'https://idp.example.com/?passive'
    assert mock_passive_login.call_args[1]['next_url'].startswith('/idp/saml2/continue?nonce=')

    # check a successfull response is returned if a user is logged in
    app.set_user(user.username)
    with monkeypatch.context() as m:
        m.setattr(
            'django_webtest.backends.WebtestUserBackend.get_saml2_authn_context',
            mock.Mock(return_value='webtest'),
            raising=False,
        )
        response = app.get(mock_passive_login.call_args[1]['next_url'])
    scenario.idp_response = response
    scenario.handle_post_response()
    assert scenario.sp.login.response.status.statusCode.value == 'urn:oasis:names:tc:SAML:2.0:status:Success'


def test_sso_cors_nok(app, idp, user):
    response = app.get('/login/')
    response.form.set('username', user.username)
    response.form.set('password', user.username)
    response.form.submit(name='login-password-submit')

    scenario = Scenario(app, sp_kwargs=dict(binding='post'))
    scenario.authn_request_login_needed = False

    # test preflight requests
    app.options(
        '/idp/saml2/sso',
        headers={
            'Origin': 'https://whatever.example.com',
            'Sec-Fetch-Mode': 'cors',
            'Access-Control-Request-Method': 'PUT',
        },
        status=405,
    )
    response = app.options(
        '/idp/saml2/sso',
        headers={
            'Origin': 'https://whatever.example.com',
            'Sec-Fetch-Mode': 'cors',
            'Access-Control-Request-Method': 'GET',
        },
        status=200,
    )
    assert response.headers['Access-Control-Allow-Origin'] == 'https://whatever.example.com'
    assert response.headers['Access-Control-Allow-Credentials'] == 'true'

    scenario.launch_authn_request(
        requests_params={
            'headers': {
                'Origin': 'https://whatever.example.com',
                'Sec-Fetch-Mode': 'cors',
                'Access-Control-Request-Method': 'GET',
            }
        }
    )
    assert scenario.idp_response.headers.get('Access-Control-Allow-Origin') is None


def test_sso_cors_ok(app, idp, user):
    response = app.get('/login/')
    response.form.set('username', user.username)
    response.form.set('password', user.username)
    response.form.submit(name='login-password-submit')

    scenario = Scenario(app, sp_kwargs=dict(binding='post'))
    scenario.authn_request_login_needed = False
    scenario.launch_authn_request(
        requests_params={'headers': {'Origin': 'https://sp.example.com', 'Sec-Fetch-Mode': 'cors'}}
    )
    assert scenario.idp_response.headers.get('Access-Control-Allow-Origin') == 'https://sp.example.com'
