import datetime
import http.cookies
import io
import os
import urllib.parse
import uuid

try:
    import lasso
except ImportError:
    lasso = None

from unittest import mock

import pytest
from quixote import get_response, get_session_manager

from wcs.categories import Category
from wcs.formdef import FormDef
from wcs.qommon import x509utils
from wcs.qommon.http_request import HTTPRequest
from wcs.qommon.ident.idp import MethodAdminDirectory
from wcs.qommon.misc import get_lasso_server
from wcs.qommon.saml2 import Saml2Directory, SOAPException

from .test_fc_auth import get_session
from .test_hobo_notify import PROFILE
from .utilities import clean_temporary_pub, create_temporary_pub, get_app

pytestmark = pytest.mark.skipif('lasso is None')

IDP_METADATA = """<?xml version="1.0"?>
<ns0:EntityDescriptor xmlns:ns0="urn:oasis:names:tc:SAML:2.0:metadata" xmlns:ns1="http://www.w3.org/2000/09/xmldsig#" entityID="http://sso.example.net/saml2/metadata">
  <ns0:IDPSSODescriptor protocolSupportEnumeration="urn:oasis:names:tc:SAML:2.0:protocol">
    <ns0:ArtifactResolutionService Binding="urn:oasis:names:tc:SAML:2.0:bindings:SOAP" Location="http://sso.example.net/saml2/artifact" index="0"/>
    <ns0:SingleLogoutService Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect" Location="http://sso.example.net/saml2/slo" ResponseLocation="http://sso.example.net/saml2/slo_return"/>
    <ns0:SingleLogoutService Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST" Location="http://sso.example.net/saml2/slo" ResponseLocation="http://sso.example.net/saml2/slo_return"/>
    <ns0:SingleLogoutService Binding="urn:oasis:names:tc:SAML:2.0:bindings:SOAP" Location="http://sso.example.net/saml2/slo/soap"/>
    <ns0:SingleSignOnService Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect" Location="http://sso.example.net/saml2/sso"/>
    <ns0:SingleSignOnService Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST" Location="http://sso.example.net/saml2/sso"/>
  </ns0:IDPSSODescriptor>
</ns0:EntityDescriptor>"""

role_uuid1 = str(uuid.uuid4())
role_uuid2 = str(uuid.uuid4())


@pytest.fixture
def pub():
    pub = create_temporary_pub()

    if not pub.cfg:
        pub.cfg = {}
    pub.cfg['sp'] = {
        'saml2_metadata': 'saml2-metadata.xml',
        'saml2_base_url': 'http://example.net/saml',
        'saml2_providerid': 'http://example.net/saml/metadata',
    }
    pub.cfg['users'] = {
        'field_phone': '_phone',
    }
    MethodAdminDirectory().generate_rsa_keypair()
    setup_idps(pub)
    pub.user_class.wipe()
    pub.user_class().store()
    return pub


def setup_idps(pub, idp_number=1):
    pub.cfg['idp'] = {}
    for i in range(idp_number):
        # generate a pair of keys for the mocking idp server
        idp_publickey, idp_privatekey = x509utils.generate_rsa_keypair()
        metadata = IDP_METADATA
        if i == 0:
            base_id = 'http-sso.example.net-saml2-metadata'
        else:
            base_id = 'http-sso%s.example.net-saml2-metadata' % i
            metadata = IDP_METADATA.replace('sso.example.net', 'sso%d.example.net' % i)
        pub.cfg['idp'][base_id] = {
            'metadata': 'idp-%s-metadata.xml' % base_id,
            'publickey': 'idp-%s-publickey.pem' % base_id,
            'role': lasso.PROVIDER_ROLE_IDP,
        }
        filename = pub.cfg['idp'][base_id]['metadata']
        with open(os.path.join(pub.app_dir, filename), 'w') as fd:
            fd.write(metadata)

        filename = pub.cfg['idp'][base_id]['publickey']
        with open(os.path.join(pub.app_dir, filename), 'w') as fd:
            fd.write(idp_publickey)

        filename = pub.cfg['idp'][base_id]['publickey'].replace('public', 'private')
        with open(os.path.join(pub.app_dir, filename), 'w') as fd:
            fd.write(idp_privatekey)

    pub.write_cfg()


def teardown_module(module):
    clean_temporary_pub()


def test_login(pub):
    req = HTTPRequest(
        None,
        {
            'SERVER_NAME': 'example.net',
            'SCRIPT_NAME': '',
        },
    )
    pub._set_request(req)
    saml2 = Saml2Directory()
    saml2.perform_login()
    assert req.response.status_code == 302
    assert req.response.headers['location'].startswith('http://sso.example.net/saml2/sso?SAMLRequest')
    assert 'rsa-sha256' in req.response.headers['location']


def get_authn_response_msg(
    pub,
    ni_format=lasso.SAML2_NAME_IDENTIFIER_FORMAT_PERSISTENT,
    protocol_binding=lasso.SAML2_METADATA_BINDING_POST,
):
    idp_metadata_filepath = os.path.join(pub.app_dir, 'idp-http-sso.example.net-saml2-metadata-metadata.xml')
    idp_key_filepath = os.path.join(pub.app_dir, 'idp-http-sso.example.net-saml2-metadata-privatekey.pem')
    idp = lasso.Server(idp_metadata_filepath, idp_key_filepath, None, None)
    idp.addProvider(
        lasso.PROVIDER_ROLE_SP,
        os.path.join(pub.app_dir, 'saml2-metadata.xml'),
        os.path.join(pub.app_dir, 'public-key.pem'),
    )
    login = lasso.Login(idp)
    login.initIdpInitiatedAuthnRequest(pub.cfg['sp']['saml2_providerid'])
    login.request.nameIDPolicy.format = ni_format
    login.request.nameIDPolicy.allowCreate = True
    login.request.protocolBinding = protocol_binding
    login.processAuthnRequestMsg(None)
    login.validateRequestMsg(True, True)
    login.buildAssertion(
        lasso.SAML2_AUTHN_CONTEXT_PASSWORD,
        datetime.datetime.now().isoformat(),
        'unused',
        (datetime.datetime.now() - datetime.timedelta(3600)).isoformat(),
        (datetime.datetime.now() + datetime.timedelta(3600)).isoformat(),
    )
    if ni_format == lasso.SAML2_NAME_IDENTIFIER_FORMAT_UNSPECIFIED:
        login.assertion.subject.nameID.content = '1234'
    value = lasso.MiscTextNode.newWithString('John')
    value.textChild = True
    login.assertion.addAttributeWithNode('first_name', lasso.SAML2_ATTRIBUTE_NAME_FORMAT_BASIC, value)
    value = lasso.MiscTextNode.newWithString('Doe')
    value.textChild = True
    login.assertion.addAttributeWithNode('last_name', lasso.SAML2_ATTRIBUTE_NAME_FORMAT_BASIC, value)
    value = lasso.MiscTextNode.newWithString('john.doe@example.com')
    value.textChild = True
    login.assertion.addAttributeWithNode('email', lasso.SAML2_ATTRIBUTE_NAME_FORMAT_BASIC, value)
    value = lasso.MiscTextNode.newWithString('+33123456789')
    value.textChild = True
    login.assertion.addAttributeWithNode('phone', lasso.SAML2_ATTRIBUTE_NAME_FORMAT_BASIC, value)
    value = lasso.MiscTextNode.newWithString('2000-01-01')
    value.textChild = True
    login.assertion.addAttributeWithNode('birthdate', lasso.SAML2_ATTRIBUTE_NAME_FORMAT_BASIC, value)
    for a_name in ['first_name', 'last_name', 'email', 'phone']:
        value = lasso.MiscTextNode.newWithString(a_name)
        value.textChild = True
        login.assertion.addAttributeWithNode(
            'verified_attributes', lasso.SAML2_ATTRIBUTE_NAME_FORMAT_BASIC, value
        )

    if not login.assertion.attributeStatement:
        login.assertion.attributeStatement = [lasso.Saml2AttributeStatement()]

    # add two roles in role-slug attribute
    role_slug_attribute = lasso.Saml2Attribute()
    role_slug_attribute.name = 'role-slug'
    role_slug_attribute.nameFormat = lasso.SAML2_ATTRIBUTE_NAME_FORMAT_BASIC
    role_uuids = []
    for role_uuid in (role_uuid1, role_uuid2):
        text_node = lasso.MiscTextNode.newWithString(role_uuid)
        text_node.textChild = True
        atv = lasso.Saml2AttributeValue()
        atv.any = [text_node]
        role_uuids.append(atv)
    role_slug_attribute.attributeValue = role_uuids
    attributes = list(login.assertion.attributeStatement[0].attribute)
    attributes.append(role_slug_attribute)
    login.assertion.attributeStatement[0].attribute = attributes

    if protocol_binding == lasso.SAML2_METADATA_BINDING_POST:
        login.buildAuthnResponseMsg()
        return login.msgBody

    login.buildArtifactMsg(lasso.HTTP_METHOD_ARTIFACT_GET)
    return login.msgUrl


def get_assertion_consumer_request(pub, ni_format=lasso.SAML2_NAME_IDENTIFIER_FORMAT_PERSISTENT):
    req = HTTPRequest(
        None,
        {
            'SERVER_NAME': 'example.net',
            'SCRIPT_NAME': '',
            'PATH_INFO': '/saml/assertionConsumerPost',
        },
    )
    pub._set_request(req)
    pub.session_class.wipe()
    req.session = pub.session_class(id=1)
    assert req.session.user is None
    req.form['SAMLResponse'] = get_authn_response_msg(pub, ni_format=ni_format)
    return req


def test_saml_metadata(pub):
    req = HTTPRequest(
        None,
        {
            'SERVER_NAME': 'example.net',
            'SCRIPT_NAME': '',
        },
    )
    pub._set_request(req)

    saml2 = Saml2Directory()
    body = saml2.metadata()
    assert '<EntityDescriptor' in body
    assert req.response.content_type == 'text/xml'


def test_saml_public_key(pub):
    req = HTTPRequest(
        None,
        {
            'SERVER_NAME': 'example.net',
            'SCRIPT_NAME': '',
        },
    )
    pub._set_request(req)

    saml2 = Saml2Directory()
    body = saml2.public_key()
    assert body.startswith('-----BEGIN PUBLIC KEY-----')
    assert req.response.content_type == 'application/octet-stream'


def test_assertion_consumer(pub):
    req = get_assertion_consumer_request(pub)
    saml2 = Saml2Directory()
    saml2.assertionConsumerPost()

    assert req.response.status_code == 303
    assert req.response.headers['location'] == 'http://example.net'
    assert req.session.user is not None


def test_assertion_consumer_redirect_errors(pub):
    req = get_assertion_consumer_request(pub)
    saml2 = Saml2Directory()
    resp = saml2.assertionConsumerRedirect()
    assert 'No SAML Response in query string' in str(resp)

    req.environ['QUERY_STRING'] = 'SAMLResponse=xxx'
    resp = saml2.assertionConsumerRedirect()
    assert 'Unknown error' in str(resp)


def test_assertion_consumer_unspecified(pub):
    req = get_assertion_consumer_request(pub, ni_format=lasso.SAML2_NAME_IDENTIFIER_FORMAT_UNSPECIFIED)
    saml2 = Saml2Directory()
    saml2.assertionConsumerPost()

    assert req.response.status_code == 303
    assert req.response.headers['location'] == 'http://example.net'
    assert req.session.user is not None


def test_assertion_consumer_existing_federation(pub, caplog):
    # setup an hobo profile
    from wcs import sql
    from wcs.ctl.management.commands.hobo_deploy import Command as CmdHoboDeploy

    CmdHoboDeploy().update_profile(PROFILE, pub)

    pub.set_config()

    pub.role_class.wipe()
    role = pub.role_class('Foo')
    role.uuid = role_uuid1
    role.store()

    # 1st pass to generate a user
    pub.user_class.wipe()
    assert pub.user_class.count() == 0
    req = get_assertion_consumer_request(pub)
    saml2 = Saml2Directory()
    saml_response_body = req.form['SAMLResponse']
    body = saml2.assertionConsumerPost()
    assert pub.user_class.count() == 1
    user = pub.user_class.select()[0]
    assert user.verified_fields
    assert len(user.verified_fields) == 4
    assert user.form_data['_birthdate'].tm_year == 2000
    assert user.form_data['_phone'] == '+33123456789'
    assert user.email == 'john.doe@example.com'
    assert user.roles == [role.id]  # other uuid is ignored as unknown

    assert ('enrolling user %s in Foo' % user.id) in [x.message for x in caplog.records]
    assert 'role uuid %s is unknown' % role_uuid2 in [x.message for x in caplog.records]

    req = HTTPRequest(
        None,
        {
            'SERVER_NAME': 'example.net',
            'SCRIPT_NAME': '',
            'PATH_INFO': '/saml/assertionConsumerPost',
        },
    )
    pub._set_request(req)
    req.session = pub.session_class(id=2)  # another session
    req.session.add_message('blah')
    req.form['SAMLResponse'] = saml_response_body
    assert req.session.user is None

    # replay the response, this will give an assertion replay error
    saml2 = Saml2Directory()
    body = saml2.assertionConsumerPost()
    assert 'Assertion replay' in str(body)

    # wipe knowledge of past assertions
    sql.UsedSamlAssertionId.wipe()

    saml2 = Saml2Directory()
    assert req.session.user is None
    assert req.session.message == {'level': 'error', 'message': 'blah', 'job_id': None}
    body = saml2.assertionConsumerPost()
    assert req.session.user == user.id
    assert req.session.saml_authn_context == lasso.SAML2_AUTHN_CONTEXT_PASSWORD
    assert req.session.message is None


def test_assertion_consumer_redirect_after_url(pub):
    req = get_assertion_consumer_request(pub)
    req.form['RelayState'] = '/foobar/?test=ok'
    saml2 = Saml2Directory()
    saml2.assertionConsumerPost()
    assert req.response.status_code == 303
    assert req.response.headers['location'] == 'http://example.net/foobar/?test=ok'


def test_assertion_consumer_full_url_redirect_after_url(pub):
    req = get_assertion_consumer_request(pub)
    req.form['RelayState'] = 'http://example.net/foobar/?test=ok'
    saml2 = Saml2Directory()
    saml2.assertionConsumerPost()
    assert req.response.status_code == 303
    assert req.response.headers['location'] == 'http://example.net/foobar/?test=ok'


def test_assertion_consumer_external_url_redirect_after_url(pub):
    req = get_assertion_consumer_request(pub)
    req.form['RelayState'] = 'http://example.org/foobar/?test=ok'
    saml2 = Saml2Directory()
    assert 'Invalid URL in RelayState' in str(saml2.assertionConsumerPost())

    pub.site_options.set('options', 'relatable-hosts', 'example.org')
    req = get_assertion_consumer_request(pub)
    req.form['RelayState'] = 'http://example.org/foobar/?test=ok'
    saml2 = Saml2Directory()
    saml2.assertionConsumerPost()
    assert get_response().headers['location'] == 'http://example.org/foobar/?test=ok'


def test_assertion_consumer_invalid_url_redirect_after_url(pub):
    req = get_assertion_consumer_request(pub)
    req.form['RelayState'] = urllib.parse.unquote('http://evil.com%EF%BC%8F@bestbuy.com/')
    saml2 = Saml2Directory()
    assert 'Invalid URL in RelayState' in str(saml2.assertionConsumerPost())


def test_assertion_consumer_artifact_error(pub):
    def get_assertion_consumer_request(pub, ni_format=lasso.SAML2_NAME_IDENTIFIER_FORMAT_PERSISTENT):
        msg_url = get_authn_response_msg(pub, protocol_binding=lasso.SAML2_METADATA_BINDING_ARTIFACT)
        artifact = urllib.parse.parse_qs(urllib.parse.urlparse(msg_url).query)['SAMLart'][0]
        req = HTTPRequest(
            None,
            {
                'SERVER_NAME': 'example.net',
                'SCRIPT_NAME': '',
                'PATH_INFO': '/saml/assertionConsumerArtifact',
                'QUERY_STRING': urllib.parse.urlencode(
                    {'SAMLart': artifact, 'RelayState': '/foobar/?test=ok'}
                ),
            },
        )
        req.process_inputs()
        pub._set_request(req)
        pub.session_class.wipe()
        req.session = pub.session_class(id=1)
        assert req.session.user is None
        return req

    with mock.patch('wcs.qommon.saml2.soap_call', side_effect=SOAPException()):
        req = get_assertion_consumer_request(pub)
    saml2 = Saml2Directory()
    saml2.assertionConsumerArtifact()
    assert req.response.status_code == 302
    assert req.response.headers['location'] == 'http://example.net/saml/error?RelayState=/foobar/%3Ftest%3Dok'


def test_assertion_consumer_artifact_head(pub):
    def get_assertion_consumer_request(pub, ni_format=lasso.SAML2_NAME_IDENTIFIER_FORMAT_PERSISTENT):
        msg_url = get_authn_response_msg(pub, protocol_binding=lasso.SAML2_METADATA_BINDING_ARTIFACT)
        artifact = urllib.parse.parse_qs(urllib.parse.urlparse(msg_url).query)['SAMLart'][0]
        req = HTTPRequest(
            None,
            {
                'REQUEST_METHOD': 'HEAD',
                'SERVER_NAME': 'example.net',
                'SCRIPT_NAME': '',
                'PATH_INFO': '/saml/assertionConsumerArtifact',
                'QUERY_STRING': urllib.parse.urlencode(
                    {'SAMLart': artifact, 'RelayState': '/foobar/?test=ok'}
                ),
            },
        )
        req.process_inputs()
        pub._set_request(req)
        pub.session_class.wipe()
        req.session = pub.session_class(id=1)
        assert req.session.user is None
        return req

    req = get_assertion_consumer_request(pub)
    saml2 = Saml2Directory()
    saml2.assertionConsumerArtifact()
    # no request to IdP, no redirection
    assert req.response.status_code == 200


def test_saml_error_page(pub):
    resp = get_app(pub).get('/saml/error?RelayState=/foobar/%3Ftest%3Dok')
    resp = resp.form.submit()
    assert resp.status_int == 302
    assert urllib.parse.parse_qs(urllib.parse.urlparse(resp.location).query)['RelayState'] == [
        '/foobar/?test=ok'
    ]


def test_saml_login_page(pub):
    resp = get_app(pub).get('/login/')
    assert resp.status_int == 302
    assert resp.headers['X-Robots-Tag'] == 'noindex'
    assert resp.location.startswith('http://sso.example.net/saml2/sso?SAMLRequest=')
    request = lasso.Samlp2AuthnRequest()
    request.initFromQuery(urllib.parse.urlparse(resp.location).query)
    assert request.forceAuthn is False


def test_saml_login_page_force_authn(pub):
    resp = get_app(pub).get('/login/?forceAuthn=true')
    assert resp.status_int == 302
    assert resp.location.startswith('http://sso.example.net/saml2/sso?SAMLRequest=')
    request = lasso.Samlp2AuthnRequest()
    request.initFromQuery(urllib.parse.urlparse(resp.location).query)
    assert request.forceAuthn is True


def test_saml_login_page_several_idp(pub):
    setup_idps(pub, idp_number=4)
    # even if there are multiple IdP, /login/ will initiate SSO with the first
    # one.
    # idp are stored in a dict, so the first idp is indeterminate
    first_idp_domain = sorted(pub.cfg['idp'].keys())[0].split('-')[1]
    resp = get_app(pub).get('/login/')
    assert resp.status_int == 302
    assert resp.location.startswith('http://%s/saml2/sso?SAMLRequest=' % first_idp_domain)


def test_saml_backoffice_redirect(pub):
    resp = get_app(pub).get('/backoffice/')
    assert resp.status_int == 302
    assert resp.location.startswith('http://example.net/login/?next=')
    resp = resp.follow()
    assert resp.location.startswith('http://sso.example.net/saml2/sso')
    assert urllib.parse.parse_qs(urllib.parse.urlparse(resp.location).query)['SAMLRequest']
    assert urllib.parse.parse_qs(urllib.parse.urlparse(resp.location).query)['RelayState'] == [
        'http://example.net/backoffice/'
    ]

    request = lasso.Samlp2AuthnRequest()
    request.initFromQuery(urllib.parse.urlparse(resp.location).query)
    assert ':next_url>http://example.net/backoffice/<' in request.getOriginalXmlnode()


def test_saml_login_invalid_next(pub):
    resp = get_app(pub).get('http://example.net/login/?next=%s' % urllib.parse.quote('http://invalid'))
    assert 'Invalid URL in RelayState' in resp.text


def test_saml_login_hint(pub):
    resp = get_app(pub).get('/login/')
    assert resp.status_int == 302
    assert resp.location.startswith('http://sso.example.net/saml2/sso')
    request = lasso.Samlp2AuthnRequest()
    request.initFromQuery(urllib.parse.urlparse(resp.location).query)
    assert 'login-hint' not in request.getOriginalXmlnode()

    resp = get_app(pub).get('/backoffice/')
    assert resp.status_int == 302
    assert resp.location.startswith('http://example.net/login/?next=')
    resp = resp.follow()
    assert resp.location.startswith('http://sso.example.net/saml2/sso')
    request = lasso.Samlp2AuthnRequest()
    request.initFromQuery(urllib.parse.urlparse(resp.location).query)
    assert ':login-hint>backoffice<' in request.getOriginalXmlnode()

    resp = get_app(pub).get('http://example.net/login/?next=/backoffice/')
    request = lasso.Samlp2AuthnRequest()
    request.initFromQuery(urllib.parse.urlparse(resp.location).query)
    assert ':login-hint>backoffice<' in request.getOriginalXmlnode()


def test_saml_register(pub):
    get_app(pub).get('/register/', status=404)

    # check redirection to known registration page
    pub.cfg['saml_identities'] = {
        'registration-url': 'http://sso.example.net/registration',
    }
    pub.write_cfg()
    resp = get_app(pub).get('/register/')
    assert resp.location == 'http://sso.example.net/registration'

    # check redirection to known registration page, with a variable
    pub.cfg['saml_identities'] = {
        'registration-url': 'http://sso.example.net/registration?next_url=[next_url]',
    }
    pub.write_cfg()
    resp = get_app(pub).get('/register/')
    assert (
        resp.location == 'http://sso.example.net/registration?next_url=http%3A%2F%2Fexample.net%2Fregister%2F'
    )


def test_saml_logout(pub):
    req = get_assertion_consumer_request(pub)
    saml2 = Saml2Directory()
    saml2.assertionConsumerPost()
    assert req.session.user is not None
    saml2.slo_sp()
    assert req.response.headers['location'].startswith('http://sso.example.net/saml2/slo?SAMLRequest=')
    assert 'rsa-sha256' in req.response.headers['location']
    assert req.session.user is None


def test_saml_logout_soap(pub):
    req = get_assertion_consumer_request(pub)
    saml2 = Saml2Directory()
    saml2.assertionConsumerPost()
    slo_ok_response = b'''<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"
    xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol"
    xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion">
      <s:Body>
        <samlp:LogoutResponse Version="2.0" IssueInstant="2023-10-01T10:49:31Z">
          <saml:Issuer>http://sso.example.net/saml2/metadata</saml:Issuer>
          <samlp:Status>
            <samlp:StatusCode Value="urn:oasis:names:tc:SAML:2.0:status:Success"/>
          </samlp:Status>
        </samlp:LogoutResponse>
      </s:Body>
    </s:Envelope>'''
    lasso_logout = lasso.Logout(get_lasso_server())
    lasso_logout.setSignatureVerifyHint(lasso.PROFILE_SIGNATURE_VERIFY_HINT_IGNORE)
    with (
        mock.patch('wcs.qommon.saml2.soap_call', return_value=slo_ok_response),
        mock.patch('wcs.qommon.saml2.lasso.Logout', return_value=lasso_logout),
    ):
        saml2.slo_sp(method=lasso.HTTP_METHOD_SOAP)
    assert req.response.headers['location'] == 'http://example.net/'
    assert req.session is None

    # check SAML error
    req = get_assertion_consumer_request(pub)
    saml2 = Saml2Directory()
    saml2.assertionConsumerPost()
    slo_ok_response = b'''<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"
    xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol"
    xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion">
      <s:Body>
        <samlp:LogoutResponse Version="2.0" IssueInstant="2023-10-01T10:49:31Z">
          <saml:Issuer>http://sso.example.net/saml2/metadata</saml:Issuer>
          <samlp:Status>
            <samlp:StatusCode Value="urn:oasis:names:tc:SAML:2.0:status:Responder"/>
          </samlp:Status>
        </samlp:LogoutResponse>
      </s:Body>
    </s:Envelope>'''
    with (
        mock.patch('wcs.qommon.saml2.soap_call', return_value=slo_ok_response),
        mock.patch('wcs.qommon.saml2.lasso.Logout', return_value=lasso_logout),
    ):
        saml2.slo_sp(method=lasso.HTTP_METHOD_SOAP)
    assert req.response.headers['location'] == 'http://example.net/'
    assert req.session is not None

    # check SOAP error
    req = get_assertion_consumer_request(pub)
    saml2 = Saml2Directory()
    saml2.assertionConsumerPost()
    assert req.session.user is not None
    with mock.patch('wcs.qommon.saml2.soap_call', side_effect=SOAPException()):
        resp = saml2.slo_sp(method=lasso.HTTP_METHOD_SOAP)
        assert 'Failure to communicate with identity provider' in str(resp)


@pytest.fixture
def logout_setup(pub):
    req = get_assertion_consumer_request(pub)
    saml2 = Saml2Directory()
    saml2.assertionConsumerPost()
    assert req.session.user is not None
    get_session_manager().maintain_session(req.session)

    # get id from existing assertion
    server = get_lasso_server()
    login = lasso.Login(server)
    login.setSessionFromDump(req.session.lasso_session_dump)
    assertion_id = login.session.assertions['http://sso.example.net/saml2/metadata'].id
    name_id = req.session.name_identifier

    # and recreate an idp session
    idp_metadata_filepath = os.path.join(pub.app_dir, 'idp-http-sso.example.net-saml2-metadata-metadata.xml')
    idp_key_filepath = os.path.join(pub.app_dir, 'idp-http-sso.example.net-saml2-metadata-privatekey.pem')
    idp = lasso.Server(idp_metadata_filepath, idp_key_filepath, None, None)
    idp.addProvider(
        lasso.PROVIDER_ROLE_SP,
        os.path.join(pub.app_dir, 'saml2-metadata.xml'),
        os.path.join(pub.app_dir, 'public-key.pem'),
    )

    login = lasso.Login(idp)
    login.initIdpInitiatedAuthnRequest(pub.cfg['sp']['saml2_providerid'])
    login.request.nameIDPolicy.format = lasso.SAML2_NAME_IDENTIFIER_FORMAT_PERSISTENT
    login.request.nameIDPolicy.allowCreate = True
    login.request.protocolBinding = lasso.SAML2_METADATA_BINDING_POST
    login.processAuthnRequestMsg(None)
    login.validateRequestMsg(True, True)
    login.buildAssertion(
        lasso.SAML2_AUTHN_CONTEXT_PASSWORD,
        datetime.datetime.now().isoformat(),
        'unused',
        (datetime.datetime.now() - datetime.timedelta(3600)).isoformat(),
        (datetime.datetime.now() + datetime.timedelta(3600)).isoformat(),
    )
    login.assertion.subject.nameID.content = name_id
    login.assertion.id = assertion_id
    login.assertion.authnStatement[0].sessionIndex = assertion_id
    login.buildAuthnResponseMsg()
    session_dump = login.session.dump()

    logout = lasso.Logout(idp)
    logout.setSessionFromDump(session_dump)
    return logout


def test_saml_idp_logout(pub, logout_setup):
    logout = logout_setup
    logout.initRequest(pub.cfg['sp']['saml2_providerid'], lasso.HTTP_METHOD_REDIRECT)
    logout.buildRequestMsg()

    # process logout message
    saml2 = Saml2Directory()
    saml2.slo_idp(urllib.parse.urlparse(logout.msgUrl).query)
    req = pub.get_request()
    assert req.response.headers['location'].startswith(
        'http://sso.example.net/saml2/slo_return?SAMLResponse='
    )
    assert req.session is None


def test_saml_idp_soap_logout(pub, logout_setup):
    logout = logout_setup
    logout.initRequest(pub.cfg['sp']['saml2_providerid'], lasso.HTTP_METHOD_SOAP)
    logout.buildRequestMsg()

    # process logout message
    req = pub.get_request()
    req.environ['REQUEST_METHOD'] = 'POST'
    req.environ['CONTENT_TYPE'] = 'text/xml'
    req.environ['CONTENT_LENGTH'] = len(logout.msgBody)
    req.stdin = io.BytesIO(logout.msgBody.encode())
    saml2 = Saml2Directory()
    assert req.session is not None
    assert pub.session_class.count() == 1
    resp = saml2.singleLogoutSOAP()
    assert 'samlp:LogoutResponse' in resp
    assert req.session is None
    assert pub.session_class.count() == 0


@pytest.mark.parametrize('path', ['/', '/foobar/test/'])
def test_opened_session_cookie(pub, path):
    Category.wipe()
    cat = Category(name='foobar')
    cat.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.category_id = str(cat.id)
    formdef.fields = []
    formdef.store()

    app = get_app(pub)
    app.set_cookie('IDP_OPENED_SESSION', '1')
    resp = app.get(path)
    assert resp.status_int == 200
    pub.site_options.set('options', 'idp_session_cookie_name', 'IDP_OPENED_SESSION')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)

    pub.session_class.wipe()
    resp = app.get(f'{path}?parameter=value')
    assert pub.session_class.count() == 1
    assert pub.session_class.select()[0].opened_session_value
    cookie_name = pub.config.session_cookie_name
    cookie_store = http.cookies.SimpleCookie()
    cookie_store.load(resp.headers['Set-Cookie'])
    assert list(cookie_store.keys()) == [cookie_name]
    assert pub.session_class.select()[0].id == cookie_store[cookie_name].value
    assert 'HttpOnly' in resp.headers['Set-Cookie']
    assert 'SameSite=None' in resp.headers['Set-Cookie']
    assert 'Path=/' in resp.headers['Set-Cookie']
    assert resp.status_int == 302
    assert (
        resp.location
        == f'http://example.net/login/?ReturnUrl=http%3A//example.net{path}%3Fparameter%3Dvalue&IsPassive=true'
    )
    assert cookie_name in app.cookies

    # if we try again, no passive authentication occurs
    resp = app.get(f'{path}?parameter=value').maybe_follow()
    assert resp.status_int != 302

    # if IDP_OPENED_SESSION is modified, then passive authentication is tried again
    app.set_cookie('IDP_OPENED_SESSION', '2')
    resp = app.get(f'{path}?parameter=value')
    assert resp.status_int == 302

    # simulate a saml login
    user = pub.user_class()
    user.store()
    request = mock.Mock()
    request.get_environ.return_value = '1.1.1.1'
    with (
        mock.patch('quixote.session.get_request', return_value=request),
        mock.patch('wcs.qommon.saml2', return_value=mock.Mock(cookies={'IDP_OPENED_SESSION': '2'})),
    ):
        session = get_session_manager().session_class(id=None)
        session.set_user(user.id)
    session.opened_session_value = '2'
    session.id = 'abcd'
    session.store()
    app.set_cookie(pub.config.session_cookie_name, session.id)
    assert get_session(app).opened_session_value == '2'

    resp = app.get(f'{path}?parameter=value')
    assert resp.status_int == 200
    assert get_session(app).opened_session_value == '2'
    assert get_session(app).user == user.id

    # if the IDP_OPENED_SESSION cookie change then we are logged out
    app.set_cookie('IDP_OPENED_SESSION', '3')
    resp = app.get(f'{path}?parameter=value')
    assert not get_session(app)
    assert not get_session_manager().session_class.get(session.id, ignore_errors=True)


def test_no_opened_session_cookie(pub):
    app = get_app(pub)
    resp = app.get('/')
    assert resp.status_int == 200
    cookie_name = '%s-passive-auth-tried' % pub.config.session_cookie_name
    assert cookie_name not in app.cookies


def test_expired_opened_session_cookie_menu_json(pub):
    app = get_app(pub)
    app.get('/')  # init pub, set app_dir, etc.

    pub.site_options.set('options', 'idp_session_cookie_name', 'IDP_OPENED_SESSION')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)

    app.set_cookie('IDP_OPENED_SESSION', '1')

    # simulate a saml login
    user = pub.user_class()
    user.store()
    request = mock.Mock()
    request.get_environ.return_value = '1.1.1.1'
    with (
        mock.patch('quixote.session.get_request', return_value=request),
        mock.patch('wcs.qommon.saml2', return_value=mock.Mock(cookies={'IDP_OPENED_SESSION': '2'})),
    ):
        session = get_session_manager().session_class(id=None)
        session.set_user(user.id)
    session.opened_session_value = '2'
    session.id = 'abcd'
    session.store()
    app.set_cookie(pub.config.session_cookie_name, session.id)

    # access to a restricted page with no session on the idp or passive sso not yet tried
    app.set_cookie('IDP_OPENED_SESSION', '3')
    app.get('/backoffice/menu.json', status=302)

    # access to a restricted page with passive sso tried
    session.opened_session_value = '3'
    session.store()
    app.get('/backoffice/menu.json', status=403)


def test_opened_session_backoffice_url(pub):
    app = get_app(pub)
    app.get('/')  # init pub, set app_dir, etc.

    pub.site_options.set('options', 'idp_session_cookie_name', 'IDP_OPENED_SESSION')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)

    app.set_cookie('IDP_OPENED_SESSION', '1')

    # do not go through passive SSO
    resp = app.get('/backoffice/studio/')
    assert not urllib.parse.parse_qs(urllib.parse.urlparse(resp.location).query).get('IsPassive')

    # simulate a saml login
    user = pub.user_class()
    user.is_admin = True
    user.store()
    request = mock.Mock()
    request.get_environ.return_value = '1.1.1.1'
    with (
        mock.patch('quixote.session.get_request', return_value=request),
        mock.patch('wcs.qommon.saml2', return_value=mock.Mock(cookies={'IDP_OPENED_SESSION': '2'})),
    ):
        session = get_session_manager().session_class(id=None)
        session.set_user(user.id)
    session.opened_session_value = '1'
    session.id = 'abcd'
    session.store()
    app.set_cookie(pub.config.session_cookie_name, session.id)

    # if IDP_OPENED_SESSION is modified, then passive authentication is tried
    app.set_cookie('IDP_OPENED_SESSION', '2')
    resp = app.get('/backoffice/studio/')
    assert resp.status_int == 302
    assert urllib.parse.parse_qs(urllib.parse.urlparse(resp.location).query).get('IsPassive')
