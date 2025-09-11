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

import datetime
import json
import os
import random
import re
import time
import urllib.parse
from unittest import mock

import jwcrypto
import pytest
import responses
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.contrib.messages import constants as message_constants
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction
from django.http import QueryDict
from django.test.utils import override_settings
from django.urls import reverse
from django.utils.encoding import force_str
from django.utils.timezone import now
from jwcrypto.common import base64url_decode, base64url_encode, json_encode
from jwcrypto.jwk import JWK, JWKSet
from jwcrypto.jws import JWS, InvalidJWSObject
from jwcrypto.jwt import JWT

from authentic2.a2_rbac.models import OrganizationalUnit
from authentic2.a2_rbac.utils import get_default_ou
from authentic2.apps.authenticators.models import LoginPasswordAuthenticator
from authentic2.apps.journal.models import Event, EventType
from authentic2.custom_user.models import DeletedUser
from authentic2.models import Attribute, AttributeValue
from authentic2.utils import crypto
from authentic2.utils.jwc import IDToken, IDTokenError, parse_id_token
from authentic2.utils.misc import last_authentication_event
from authentic2.views import passive_login
from authentic2_auth_oidc.backends import OIDCBackend
from authentic2_auth_oidc.models import OIDCAccount, OIDCClaimMapping, OIDCProvider
from authentic2_auth_oidc.utils import get_attributes, get_provider, get_provider_by_issuer, register_issuer
from authentic2_auth_oidc.views import oidc_login
from tests import utils

from .conftest import KID_EC, KID_RSA

pytestmark = pytest.mark.django_db

User = get_user_model()

ANOTHER_KID_RSA = 'mt80xpd'
ANOTHER_KID_EC = 'iet7tm31'


def test_base64url_decode():
    with pytest.raises(ValueError):
        base64url_decode('x')
    base64url_decode('aa')


JWKSET_URL = 'https://www.example.com/common/discovery/v3.0/keys'
header_rsa_decoded = {'alg': 'RS256', 'kid': KID_RSA}
header_ec_decoded = {'alg': 'ES256', 'kid': KID_EC}
header_hmac_decoded = {'alg': 'HS256'}
payload_decoded = {
    'sub': '248289761001',
    'iss': 'http://server.example.com',
    'aud': 's6BhdRkqt3',
    'nonce': 'n-0S6_WzA2Mj',
    'iat': 1311280970,
    'exp': 2201094278,
}
header_rsa = 'eyJhbGciOiJSUzI1NiIsImtpZCI6IjFlOWdkazcifQ'
header_ec = 'eyJhbGciOiJFUzI1NiIsImtpZCI6ImpiMjBDZzgifQ'
header_hmac = 'eyJhbGciOiJIUzI1NiJ9'
payload = (
    'eyJhdWQiOiJzNkJoZFJrcXQzIiwiZXhwIjoyMjAxMDk0Mjc4LCJpYXQiOjEzMTEyODA5NzAsImlzcyI6Imh0dHA6Ly9zZXJ2Z'
    'XIuZXhhbXBsZS5jb20iLCJub25jZSI6Im4tMFM2X1d6QTJNaiIsInN1YiI6IjI0ODI4OTc2MTAwMSJ9'
)


def test_parse_id_token(code, oidc_provider, oidc_provider_jwkset):
    header = _header(oidc_provider)
    signature = _signature(oidc_provider)
    with oidc_provider_mock(oidc_provider, oidc_provider_jwkset, code):
        with pytest.raises(InvalidJWSObject):
            parse_id_token('x%s.%s.%s' % (header, payload, signature), oidc_provider)
        with pytest.raises(InvalidJWSObject):
            parse_id_token('%s.%s.%s' % ('$', payload, signature), oidc_provider)
        with pytest.raises(InvalidJWSObject):
            parse_id_token('%s.x%s.%s' % (header, payload, signature), oidc_provider)
        with pytest.raises(InvalidJWSObject):
            parse_id_token('%s.%s.%s' % (header, '$', signature), oidc_provider)
        with pytest.raises(InvalidJWSObject):
            parse_id_token('%s.%s.%s' % (header, payload, '-'), oidc_provider)
        assert parse_id_token('%s.%s.%s' % (header, payload, signature), oidc_provider)


def test_idtoken(oidc_provider):
    signature = _signature(oidc_provider)
    header = _header(oidc_provider)
    token = IDToken('%s.%s.%s' % (header, payload, signature))
    token.deserialize(oidc_provider)
    assert token.sub == payload_decoded['sub']
    assert token.iss == payload_decoded['iss']
    assert token.aud == payload_decoded['aud']
    assert token.nonce == payload_decoded['nonce']
    assert token.iat == datetime.datetime(2011, 7, 21, 20, 42, 50, tzinfo=datetime.UTC)
    assert token.exp == datetime.datetime(2039, 10, 1, 15, 4, 38, tzinfo=datetime.UTC)


@pytest.fixture
def oidc_provider_jwkset(jwkset):
    return jwkset


OIDC_PROVIDER_PARAMS = [
    {},
    {
        'idtoken_algo': OIDCProvider.ALGO_HMAC,
    },
    {
        'idtoken_algo': OIDCProvider.ALGO_EC,
    },
    {
        'claims_parameter_supported': True,
    },
]


@pytest.fixture(params=OIDC_PROVIDER_PARAMS)
def oidc_provider(request, db, oidc_provider_jwkset):
    claims_parameter_supported = request.param.get('claims_parameter_supported', False)
    idtoken_algo = request.param.get('idtoken_algo', OIDCProvider.ALGO_RSA)

    return make_oidc_provider(
        idtoken_algo=idtoken_algo,
        jwkset=oidc_provider_jwkset,
        claims_parameter_supported=claims_parameter_supported,
    )


@pytest.fixture
def oidc_provider_rsa(request, db, oidc_provider_jwkset):
    return make_oidc_provider(idtoken_algo=OIDCProvider.ALGO_RSA, jwkset=oidc_provider_jwkset)


def make_oidc_provider(
    name='Server',
    slug=None,
    issuer=None,
    max_auth_age=10,
    strategy=OIDCProvider.STRATEGY_CREATE,
    idtoken_algo=OIDCProvider._meta.get_field('idtoken_algo').default,
    jwkset=None,
    claims_parameter_supported=False,
    client_id='abc',
    client_secret='def',
):
    slug = slug or name.lower()
    issuer = issuer or ('https://%s.example.com' % slug)
    jwkset = json.loads(jwkset.export()) if jwkset else None
    provider = OIDCProvider.objects.create(
        ou=get_default_ou(),
        name=name,
        slug=slug,
        client_id=client_id,
        client_secret=client_secret,
        enabled=True,
        issuer=issuer,
        authorization_endpoint='%s/authorize' % issuer,
        token_endpoint='%s/token' % issuer,
        end_session_endpoint='%s/logout' % issuer,
        userinfo_endpoint='%s/user_info' % issuer,
        token_revocation_endpoint='%s/revoke' % issuer,
        max_auth_age=max_auth_age,
        strategy=strategy,
        jwkset_json=jwkset,
        idtoken_algo=idtoken_algo,
        claims_parameter_supported=claims_parameter_supported,
        button_label=name,
    )
    provider.full_clean()
    OIDCClaimMapping.objects.create(
        authenticator=provider, claim='sub', attribute='username', idtoken_claim=True
    )
    OIDCClaimMapping.objects.create(authenticator=provider, claim='email', attribute='email')
    OIDCClaimMapping.objects.create(authenticator=provider, claim='email', required=True, attribute='email')
    OIDCClaimMapping.objects.create(
        authenticator=provider,
        claim='given_name',
        required=True,
        verified=OIDCClaimMapping.ALWAYS_VERIFIED,
        attribute='first_name',
    )
    OIDCClaimMapping.objects.create(
        authenticator=provider,
        claim='family_name',
        required=True,
        verified=OIDCClaimMapping.VERIFIED_CLAIM,
        attribute='last_name',
    )
    OIDCClaimMapping.objects.create(authenticator=provider, claim='ou', attribute='ou__slug')
    return provider


@pytest.fixture
def code():
    return 'xxxx'


def _header(oidc_provider):
    return {
        OIDCProvider.ALGO_RSA: header_rsa,
        OIDCProvider.ALGO_EC: header_ec,
        OIDCProvider.ALGO_HMAC: header_hmac,
    }.get(oidc_provider.idtoken_algo)


def _signature(oidc_provider):
    if oidc_provider.idtoken_algo == OIDCProvider.ALGO_RSA:
        key = oidc_provider.jwkset.get_key(kid=KID_RSA)
        header_decoded = header_rsa_decoded
    elif oidc_provider.idtoken_algo == OIDCProvider.ALGO_EC:
        key = oidc_provider.jwkset.get_key(kid=KID_EC)
        header_decoded = header_ec_decoded
    elif oidc_provider.idtoken_algo == OIDCProvider.ALGO_HMAC:
        key = JWK(kty='oct', k=base64url_encode(oidc_provider.client_secret.encode('utf-8')))
        header_decoded = header_hmac_decoded
    jws = JWS(payload=json_encode(payload_decoded))
    jws.add_signature(key=key, protected=header_decoded)
    return json.loads(jws.serialize())['signature']


def any_params_matcher(*args):
    """Wildcard matcher for responses URL parameters"""
    return (True, '')


def oidc_provider_mock(
    oidc_provider,
    oidc_provider_jwkset,
    code,
    extra_id_token=None,
    extra_user_info=None,
    *,
    missing_user_info=None,
    sub='john.doe',
    nonce=None,
    provides_kid_header=False,
    kid=None,
    idtoken_algo=None,
):
    idtoken_algo = idtoken_algo or oidc_provider.idtoken_algo

    def token_endpoint_mock(request):
        if urllib.parse.parse_qs(request.body).get('code') == [code]:
            exp = now() + datetime.timedelta(seconds=10)
            id_token = {
                'iss': oidc_provider.issuer,
                'sub': sub,
                'iat': int(now().timestamp()),
                'aud': str(oidc_provider.client_id),
                'exp': int(exp.timestamp()),
                'name': 'doe',
            }
            if nonce:
                id_token['nonce'] = nonce
            if extra_id_token:
                id_token.update(extra_id_token)

            if idtoken_algo in (OIDCProvider.ALGO_RSA, OIDCProvider.ALGO_EC):
                alg = {
                    OIDCProvider.ALGO_RSA: 'RS256',
                    OIDCProvider.ALGO_EC: 'ES256',
                }.get(idtoken_algo)
                jwk = None
                for key in oidc_provider_jwkset['keys']:
                    if key.key_type == {
                        OIDCProvider.ALGO_RSA: 'RSA',
                        OIDCProvider.ALGO_EC: 'EC',
                    }.get(idtoken_algo):
                        jwk = key
                        break
                if provides_kid_header:
                    header = {'alg': alg, 'kid': kid}
                else:
                    header = {'alg': alg, 'kid': jwk.key_id}
                jwt = JWT(header=header, claims=id_token)
                jwt.make_signed_token(jwk)
            else:  # hmac
                jwt = JWT(header={'alg': 'HS256'}, claims=id_token)
                k = base64url_encode(oidc_provider.client_secret.encode('utf-8'))
                jwt.make_signed_token(JWK(kty='oct', k=force_str(k)))

            content = {
                'access_token': '1234',
                # check token_type is case insensitive
                'token_type': random.choice(['B', 'b']) + 'earer',
                'id_token': jwt.serialize(),
            }
            return (200, {'Content-Type': 'application/json'}, json.dumps(content))
        else:
            return (
                400,
                {'Content-Type': 'application/json'},
                json.dumps({'error': 'invalid request', 'error_description': 'Requête invalide'}),
            )

    def user_info_endpoint_mock(request):
        user_info = {
            'sub': sub,
            'iss': oidc_provider.issuer,
            'given_name': 'John',
            'family_name': 'Doe',
            'email': 'john.doe@example.com',
            'phone_number': '0123456789',
            'nickname': 'Hefty',
        }
        if extra_user_info:
            user_info.update(extra_user_info)
        for key in missing_user_info or []:
            del user_info[key]

        return (200, {'Content-Type': 'application/json'}, json.dumps(user_info))

    def token_revocation_endpoint_mock(request):
        query = urllib.parse.parse_qs(request.body)
        assert 'token' in query
        return (200, {}, '')

    rsps = responses.RequestsMock(assert_all_requests_are_fired=False)
    rsps.add_callback(
        'POST', url=oidc_provider.token_endpoint, match=[any_params_matcher], callback=token_endpoint_mock
    )
    rsps.add_callback(
        'GET',
        url=oidc_provider.userinfo_endpoint,
        match=[any_params_matcher],
        callback=user_info_endpoint_mock,
    )
    rsps.add_callback(
        'POST',
        url=oidc_provider.token_revocation_endpoint,
        match=[any_params_matcher],
        callback=token_revocation_endpoint_mock,
    )

    return rsps


def login_callback_url(oidc_provider):
    return reverse('oidc-login-callback')


def test_oidc_provider_key_sig_consistency(db):
    with pytest.raises(ValidationError, match=r'no jwkset was provided'):
        make_oidc_provider(name='Foo', slug='foo', idtoken_algo=OIDCProvider.ALGO_RSA)
    key_ec = JWK.generate(kty='EC', size=256, kid=KID_EC)
    jwkset = JWKSet()
    jwkset.add(key_ec)
    with pytest.raises(ValidationError, match=r'jwkset does not contain any such key type'):
        make_oidc_provider(name='Bar', slug='bar', idtoken_algo=OIDCProvider.ALGO_RSA, jwkset=jwkset)
    key_rsa = JWK.generate(kty='RSA', size=1024, kid=KID_RSA)
    jwkset.add(key_rsa)
    provider = make_oidc_provider(name='Baz', slug='baz', idtoken_algo=OIDCProvider.ALGO_RSA, jwkset=jwkset)
    assert provider


def test_oidc_provider_jwkset_url(db):
    def jwkset_url_mock(request):
        key_rsa = JWK.generate(kty='RSA', size=1024, kid=ANOTHER_KID_RSA)
        key_ec = JWK.generate(kty='EC', size=256, kid=ANOTHER_KID_EC)
        jwkset = JWKSet()
        jwkset.add(key_rsa)
        jwkset.add(key_ec)
        return (200, {'Content-Type': 'application/json'}, json.dumps(jwkset.export(as_dict=True)))

    with responses.RequestsMock() as rsps:
        rsps.add_callback('GET', url=JWKSET_URL, match=[any_params_matcher], callback=jwkset_url_mock)
        issuer = ('https://www.example.com',)
        provider = OIDCProvider(
            ou=get_default_ou(),
            name='Foo',
            slug='foo',
            client_id='abc',
            client_secret='def',
            enabled=True,
            issuer=issuer,
            authorization_endpoint='%s/authorize' % issuer,
            token_endpoint='%s/token' % issuer,
            end_session_endpoint='%s/logout' % issuer,
            userinfo_endpoint='%s/user_info' % issuer,
            token_revocation_endpoint='%s/revoke' % issuer,
            jwkset_url=JWKSET_URL,
            idtoken_algo=OIDCProvider.ALGO_RSA,
            claims_parameter_supported=False,
            button_label='Connect with Foo',
            strategy=OIDCProvider.STRATEGY_CREATE,
        )
        provider.full_clean()
        provider.save()
        assert provider.jwkset
        assert len(provider.jwkset_json['keys']) == 2
        assert {key['kid'] for key in provider.jwkset_json['keys']} == {ANOTHER_KID_RSA, ANOTHER_KID_EC}


def test_claim_mapping_wrong_source(app, oidc_provider, rf):
    backend = OIDCBackend()
    # set provider config according to idtoken payload
    oidc_provider.max_auth_age = None
    oidc_provider.client_id = 's6BhdRkqt3'
    oidc_provider.userinfo_endpoint = 'http://server.example.com/user_info'
    oidc_provider.issuer = 'http://server.example.com'
    oidc_provider.save()
    # reproduce inconsistent claim mapping config
    for claim in OIDCClaimMapping.objects.all():
        claim.required = False
        claim.save()

    request = rf.get('/')

    header = _header(oidc_provider)
    signature = _signature(oidc_provider)
    id_token = f'{header}.{payload}.{signature}'
    with responses.RequestsMock() as rsps:
        rsps.add(
            'GET', url=oidc_provider.userinfo_endpoint, match=[any_params_matcher], body='null', status=200
        )
        backend.authenticate(request, access_token='auietrns', id_token=id_token, provider=oidc_provider)


@responses.activate
@pytest.mark.parametrize(
    'expt_cls', (jwcrypto.jws.InvalidJWSSignature, jwcrypto.jws.InvalidJWSObject, jwcrypto.common.JWException)
)
def test_jwt_error(app, oidc_provider, rf, caplog, expt_cls):
    backend = OIDCBackend()
    # set provider config according to idtoken payload
    oidc_provider.max_auth_age = None
    oidc_provider.client_id = 's6BhdRkqt3'
    oidc_provider.userinfo_endpoint = 'http://server.example.com/user_info'
    oidc_provider.issuer = 'http://server.example.com'
    oidc_provider.save()

    request = rf.get('/')

    header = _header(oidc_provider)
    signature = _signature(oidc_provider)
    id_token = f'{header}.{payload}.{signature}'
    responses.add(
        'GET', url=oidc_provider.userinfo_endpoint, match=[any_params_matcher], body='null', status=200
    )
    with mock.patch('authentic2_auth_oidc.backends.JWT', side_effect=expt_cls):
        assert (
            backend.authenticate(request, access_token='auietrns', id_token=id_token, provider=oidc_provider)
            is None
        )
    assert len(responses.calls) == 0
    assert len(caplog.records) == 1
    msg = caplog.records[0]
    assert msg.levelname == 'WARNING'
    assert msg.message.startswith('auth_oidc: idtoken signature validation failed (')


def test_oidc_providers_on_login_page(oidc_provider, app):
    response = app.get('/login/')
    # two frontends should be present on login page
    assert response.pyquery('p#oidc-p-server')
    OIDCProvider.objects.create(
        ou=get_default_ou(),
        name='OIDCIDP 2',
        slug='oidcidp-2',
        enabled=True,
        issuer='https://idp2.example.com/',
        authorization_endpoint='https://idp2.example.com/authorize',
        token_endpoint='https://idp2.example.com/token',
        end_session_endpoint='https://idp2.example.com/logout',
        userinfo_endpoint='https://idp*é.example.com/user_info',
        token_revocation_endpoint='https://idp2.example.com/revoke',
        max_auth_age=10,
        strategy=OIDCProvider.STRATEGY_CREATE,
        jwkset_json=None,
        idtoken_algo=OIDCProvider.ALGO_RSA,
        claims_parameter_supported=False,
        button_label='Test label',
        button_description='This is a test.',
    )

    response = app.get('/login/')
    assert response.pyquery('p#oidc-p-server')
    assert response.pyquery('p#oidc-p-oidcidp-2')

    assert 'Test label' in response.text
    assert 'This is a test.' in response.text


def test_oidc_auth_button_image_and_label(app, db):
    auth_oidc = OIDCProvider.objects.create(slug='testidp', order=42, enabled=True, button_label='OIDC label')

    response = app.get('/login/')
    assert response.pyquery('#oidc-p-testidp #oidc-a-testidp').text() == 'OIDC label'

    with open('tests/200x200.jpg', 'rb') as img:
        auth_oidc.button_image = ContentFile(img.read(), name='200x200.jpg')
    auth_oidc.button_label = 'alt OIDC'
    auth_oidc.save()

    response = app.get('/login/')
    assert response.pyquery('#oidc-p-testidp #oidc-a-testidp').text() == ''
    img_attr = response.pyquery('#oidc-p-testidp #oidc-a-testidp img')[0].attrib
    assert img_attr['alt'] == 'alt OIDC'
    assert img_attr['src'].startswith('/media/authenticators/button_images/200x200')


def test_login_with_conditional_authenticators(oidc_provider, oidc_provider_jwkset, app, settings, caplog):
    myidp = make_oidc_provider(name='My IDP', slug='myidp', jwkset=oidc_provider_jwkset)
    response = app.get('/login/')
    assert 'My IDP' in response
    assert 'Server' in response

    myidp.show_condition = 'remote_addr==\'0.0.0.0\''
    myidp.save()
    response = app.get('/login/')
    assert 'Server' in response
    assert 'My IDP' not in response

    oidc_provider.show_condition = 'remote_addr==\'127.0.0.1\''
    oidc_provider.save()
    response = app.get('/login/')
    assert 'Server' in response
    assert 'My IDP' not in response

    myidp.show_condition = 'remote_addr==\'127.0.0.1\''
    myidp.save()
    response = app.get('/login/')
    assert 'Server' in response
    assert 'My IDP' in response

    myidp.show_condition = 'remote_addr==\'127.0.0.1\' and \'backoffice\' not in login_hint'
    myidp.save()
    oidc_provider.show_condition = '\'backoffice\' in login_hint'
    oidc_provider.save()
    response = app.get('/login/')
    assert 'Server' not in response
    assert 'My IDP' in response

    # As we do not create a session on each access to the login page, we need
    # to force its creation by making django-webtest believe a session exists.
    # use of force_str() can be removed with support for python2.
    app.set_cookie(force_str(settings.SESSION_COOKIE_NAME), force_str('initial'))
    session = app.session
    session['login-hint'] = ['backoffice']
    session.save()
    app.set_cookie(force_str(settings.SESSION_COOKIE_NAME), force_str(session.session_key))

    response = app.get('/login/')
    assert 'Server' in response
    assert 'My IDP' not in response


def test_login_autorun(oidc_provider, app, settings):
    response = app.get('/login/')
    assert 'Server' in response

    # hide password block
    LoginPasswordAuthenticator.objects.update_or_create(
        slug='password-authenticator', defaults={'enabled': False}
    )
    response = app.get('/login/', status=302)
    assert response['Location'].startswith('https://server.example.com/authorize')


@responses.activate
def test_sso(app, caplog, code, oidc_provider, oidc_provider_jwkset, hooks):
    cassis = OrganizationalUnit.objects.create(name='Cassis', slug='cassis')

    # a mapping defined for a later-deactivated attribute should be ignored
    attr = Attribute.objects.create(kind='string', name='another_name', label='Another name')
    OIDCClaimMapping.objects.create(
        authenticator=oidc_provider,
        claim='given_name',
        required=False,
        attribute='another_name',
    )
    OIDCClaimMapping.objects.create(
        authenticator=oidc_provider,
        claim='email',
        required=False,
        attribute='email',
        verified=OIDCClaimMapping.ALWAYS_VERIFIED,
    )
    attr.disabled = True
    attr.save()

    response = app.get('/admin/').maybe_follow()
    assert oidc_provider.name in response.text
    response = response.click(oidc_provider.name)
    location = urllib.parse.urlparse(response.location)
    endpoint = urllib.parse.urlparse(oidc_provider.authorization_endpoint)
    assert location.scheme == endpoint.scheme
    assert location.netloc == endpoint.netloc
    assert location.path == endpoint.path
    query = QueryDict(location.query)
    state = query['state']
    assert query['response_type'] == 'code'
    assert query['client_id'] == str(oidc_provider.client_id)
    assert query['scope'] == 'openid'
    assert query['redirect_uri'] == 'https://testserver' + reverse('oidc-login-callback')
    nonce = query['nonce']

    if oidc_provider.claims_parameter_supported:
        claims = json.loads(query['claims'])
        assert claims['id_token']['sub'] is None
        assert claims['userinfo']['email']['essential']
        assert claims['userinfo']['given_name']['essential']
        assert claims['userinfo']['family_name']['essential']
        assert claims['userinfo']['ou'] is None

    assert User.objects.count() == 0

    with utils.check_log(caplog, "'error': 'invalid request'"):
        with oidc_provider_mock(oidc_provider, oidc_provider_jwkset, code):
            response = app.get(login_callback_url(oidc_provider), params={'code': 'yyyy', 'state': state})
            cookie = utils.decode_cookie(app.cookies['messages'])
            if isinstance(cookie, list):
                assert len(cookie) == 1
                cookie = cookie[0].message
            assert 'Authentication on Server failed with error' in cookie
    with utils.check_log(caplog, 'invalid id_token'):
        with oidc_provider_mock(oidc_provider, oidc_provider_jwkset, code, extra_id_token={'iss': None}):
            response = app.get(login_callback_url(oidc_provider), params={'code': code, 'state': state})
    with utils.check_log(caplog, 'invalid id_token'):
        with oidc_provider_mock(oidc_provider, oidc_provider_jwkset, code, extra_id_token={'sub': None}):
            response = app.get(login_callback_url(oidc_provider), params={'code': code, 'state': state})
    with utils.check_log(caplog, 'invalid auth_time value'):
        with oidc_provider_mock(
            oidc_provider, oidc_provider_jwkset, code, extra_id_token={'auth_time': '1234'}
        ):
            response = app.get(login_callback_url(oidc_provider), params={'code': code, 'state': state})
    with utils.check_log(caplog, 'authentication is too old'):
        with oidc_provider_mock(oidc_provider, oidc_provider_jwkset, code, extra_id_token={'iat': 1}):
            response = app.get(login_callback_url(oidc_provider), params={'code': code, 'state': state})
    with utils.check_log(caplog, 'invalid id_token'):
        with oidc_provider_mock(oidc_provider, oidc_provider_jwkset, code, extra_id_token={'exp': 1}):
            response = app.get(login_callback_url(oidc_provider), params={'code': code, 'state': state})
    with utils.check_log(caplog, 'invalid id_token audience'):
        with oidc_provider_mock(oidc_provider, oidc_provider_jwkset, code, extra_id_token={'aud': 'zz'}):
            response = app.get(login_callback_url(oidc_provider), params={'code': code, 'state': state})
    with utils.check_log(caplog, 'expected nonce'):
        with oidc_provider_mock(oidc_provider, oidc_provider_jwkset, code):
            response = app.get(login_callback_url(oidc_provider), params={'code': code, 'state': state})
    assert not hooks.auth_oidc_backend_modify_user
    assert len(utils.decode_cookie(app.cookies['messages'])) == 5
    alt_state_content = crypto.loads(state)
    alt_state_content['prompt'] = ['none']
    with utils.check_log(caplog, 'consent_required'):
        with oidc_provider_mock(oidc_provider, oidc_provider_jwkset, code, nonce=nonce):
            response = app.get(
                login_callback_url(oidc_provider),
                params={'error': 'consent_required', 'state': crypto.dumps(alt_state_content)},
            )
            # prompt=none, no message displayed to end user, no additional set cookie
            assert len(utils.decode_cookie(app.cookies['messages'])) == 5
    alt_state_content = crypto.loads(state)
    alt_state_content['prompt'] = ['whatever']  # any value other than none
    with utils.check_log(caplog, 'some_other_error'):
        with oidc_provider_mock(oidc_provider, oidc_provider_jwkset, code, nonce=nonce):
            response = app.get(
                login_callback_url(oidc_provider),
                params={'error': 'some_other_error', 'state': crypto.dumps(alt_state_content)},
            )
            utils.assert_event(
                'user.login.failure',
                reason='auth_oidc: error received some_other_error (prompt: whatever)',
            )
    assert len(hooks.auth_oidc_backend_modify_user) == 0
    with utils.check_log(caplog, 'created user'):
        with oidc_provider_mock(oidc_provider, oidc_provider_jwkset, code, nonce=nonce):
            response = app.get(login_callback_url(oidc_provider), params={'code': code, 'state': state})
    assert len(hooks.auth_oidc_backend_modify_user) == 1
    assert set(hooks.auth_oidc_backend_modify_user[0]['kwargs']) >= {
        'user',
        'provider',
        'user_info',
        'id_token',
        'access_token',
    }
    assert urllib.parse.urlparse(response['Location']).path == '/admin/'
    assert User.objects.count() == 1
    user = User.objects.get()
    assert user.ou == get_default_ou()
    assert user.username == 'john.doe'
    assert user.first_name == 'John'
    assert user.last_name == 'Doe'
    assert user.email == 'john.doe@example.com'
    assert user.email_verified
    assert user.attributes.first_name == 'John'
    assert user.attributes.last_name == 'Doe'
    assert AttributeValue.objects.filter(content='John', verified=True).count() == 1
    assert AttributeValue.objects.filter(content='Doe', verified=False).count() == 1
    assert not AttributeValue.objects.filter(
        content_type=ContentType.objects.get_for_model(user), object_id=user.id, attribute=attr
    )
    assert last_authentication_event(session=app.session)['nonce'] == nonce

    with oidc_provider_mock(
        oidc_provider, oidc_provider_jwkset, code, extra_user_info={'family_name_verified': True}, nonce=nonce
    ):
        response = app.get(login_callback_url(oidc_provider), params={'code': code, 'state': state})
    assert AttributeValue.objects.filter(content='Doe', verified=False).count() == 0
    assert AttributeValue.objects.filter(content='Doe', verified=True).count() == 1

    with oidc_provider_mock(
        oidc_provider, oidc_provider_jwkset, code, extra_user_info={'ou': 'cassis'}, nonce=nonce
    ):
        response = app.get(login_callback_url(oidc_provider), params={'code': code, 'state': state})
    assert User.objects.count() == 1
    user = User.objects.get()
    assert user.ou == cassis

    with oidc_provider_mock(oidc_provider, oidc_provider_jwkset, code, nonce=nonce):
        response = app.get(login_callback_url(oidc_provider), params={'code': code, 'state': state})
    assert User.objects.count() == 1
    user = User.objects.get()
    assert user.ou == cassis
    last_modified = user.modified

    time.sleep(0.1)

    with oidc_provider_mock(oidc_provider, oidc_provider_jwkset, code):
        response = app.get(login_callback_url(oidc_provider), params={'code': code, 'state': state})
    assert User.objects.count() == 1
    user = User.objects.get()
    assert user.ou == cassis
    assert user.modified == last_modified

    response = app.get(reverse('account_management'))
    with utils.check_log(caplog, 'revoked token from OIDC'):
        with oidc_provider_mock(oidc_provider, oidc_provider_jwkset, code):
            response = response.click(href='logout')
    assert response.location.startswith('https://server.example.com/logout?')


def test_show_on_login_page(app, oidc_provider):
    response = app.get('/login/')
    assert 'oidc-a-server' in response.text

    # do not show this provider on login page anymore
    oidc_provider.enabled = False
    oidc_provider.save()

    response = app.get('/login/')
    assert 'oidc-a-server' not in response.text


def test_strategy_find_uuid(app, caplog, code, oidc_provider, oidc_provider_jwkset, simple_user):
    # no mapping please
    OIDCClaimMapping.objects.all().delete()
    oidc_provider.strategy = oidc_provider.STRATEGY_FIND_UUID
    oidc_provider.save()

    assert User.objects.count() == 1

    response = app.get('/').maybe_follow()
    assert oidc_provider.name in response.text
    response = response.click(oidc_provider.name)
    location = urllib.parse.urlparse(response.location)
    query = QueryDict(location.query)
    state = query['state']
    nonce = query['nonce']

    # sub=john.doe, MUST not work
    with utils.check_log(caplog, 'cannot create user'):
        with oidc_provider_mock(oidc_provider, oidc_provider_jwkset, code, nonce=nonce):
            response = app.get(login_callback_url(oidc_provider), params={'code': code, 'state': state})

    # sub=simple_user.uuid MUST work
    with utils.check_log(caplog, 'found user using UUID'):
        with oidc_provider_mock(oidc_provider, oidc_provider_jwkset, code, sub=simple_user.uuid, nonce=nonce):
            response = app.get(login_callback_url(oidc_provider), params={'code': code, 'state': state})

    assert urllib.parse.urlparse(response['Location']).path == '/'
    assert User.objects.count() == 1
    user = User.objects.get()
    # verify user was not modified
    assert user.username == 'user'
    assert user.first_name == 'Jôhn'
    assert user.last_name == 'Dôe'
    assert user.email == 'user@example.net'
    assert user.attributes.first_name == 'Jôhn'
    assert user.attributes.last_name == 'Dôe'

    response = app.get(reverse('account_management'))
    with utils.check_log(caplog, 'revoked token from OIDC'):
        with oidc_provider_mock(oidc_provider, oidc_provider_jwkset, code, nonce=nonce):
            response = response.click(href='logout')
    assert response.location.startswith('https://server.example.com/logout?')


def test_strategy_find_email(app, caplog, code, oidc_provider, oidc_provider_jwkset, simple_user):
    OIDCClaimMapping.objects.all().delete()
    OIDCClaimMapping.objects.create(
        authenticator=oidc_provider,
        claim='email',
        attribute='email',
        idtoken_claim=False,  # served by user_info endpoint
    )
    oidc_provider.strategy = oidc_provider.STRATEGY_FIND_EMAIL
    oidc_provider.save()
    oidc_provider.ou.email_is_unique = True
    oidc_provider.ou.save()

    assert User.objects.count() == 1

    response = app.get('/').maybe_follow()
    assert oidc_provider.name in response.text
    response = response.click(oidc_provider.name)
    location = urllib.parse.urlparse(response.location)
    query = QueryDict(location.query)
    state = query['state']
    nonce = query['nonce']

    with utils.check_log(caplog, 'cannot create user'):
        with oidc_provider_mock(oidc_provider, oidc_provider_jwkset, code, nonce=nonce):
            response = app.get(login_callback_url(oidc_provider), params={'code': code, 'state': state})

    simple_user.email = 'sub@example.com'
    simple_user.save()

    with utils.check_log(caplog, 'cannot create user'):
        with oidc_provider_mock(
            oidc_provider, oidc_provider_jwkset, code, sub='sub@example.com', nonce=nonce
        ):
            response = app.get(login_callback_url(oidc_provider), params={'code': code, 'state': state})

    simple_user.email = 'john.doe@example.com'
    simple_user.save()

    with utils.check_log(caplog, 'found user using email'):
        with oidc_provider_mock(oidc_provider, oidc_provider_jwkset, code, nonce=nonce):
            response = app.get(login_callback_url(oidc_provider), params={'code': code, 'state': state})

    assert urllib.parse.urlparse(response['Location']).path == '/'
    assert User.objects.count() == 1
    user = User.objects.get()
    # verify user was not modified
    assert user.username == 'user'
    assert user.first_name == 'Jôhn'
    assert user.last_name == 'Dôe'
    assert user.email == 'john.doe@example.com'
    assert user.attributes.first_name == 'Jôhn'
    assert user.attributes.last_name == 'Dôe'


def test_strategy_find_email_normalized_unicode_collision_prevention(
    app, caplog, code, oidc_provider, oidc_provider_jwkset, simple_user
):
    OIDCClaimMapping.objects.all().delete()
    OIDCClaimMapping.objects.create(
        authenticator=oidc_provider,
        claim='email',
        attribute='email',
        idtoken_claim=False,  # served by user_info endpoint
    )
    oidc_provider.strategy = oidc_provider.STRATEGY_FIND_EMAIL
    oidc_provider.save()
    oidc_provider.ou.email_is_unique = True
    oidc_provider.ou.save()

    extra_user_info = {'email': 'mike@ıxample.org'}  # dot-less i 'ı' U+0131

    assert User.objects.count() == 1

    response = app.get('/').maybe_follow()
    assert oidc_provider.name in response.text
    response = response.click(oidc_provider.name)
    location = urllib.parse.urlparse(response.location)
    query = QueryDict(location.query)
    state = query['state']
    nonce = query['nonce']

    simple_user.email = 'mike@ixample.org'
    simple_user.save()

    with utils.check_log(caplog, 'cannot create user'):
        with oidc_provider_mock(
            oidc_provider, oidc_provider_jwkset, code, nonce=nonce, extra_user_info=extra_user_info
        ):
            response = app.get(login_callback_url(oidc_provider), params={'code': code, 'state': state})

    simple_user.email = 'mike@ıxample.org'
    simple_user.save()

    with utils.check_log(caplog, 'found user using email'):
        with oidc_provider_mock(
            oidc_provider, oidc_provider_jwkset, code, nonce=nonce, extra_user_info=extra_user_info
        ):
            response = app.get(login_callback_url(oidc_provider), params={'code': code, 'state': state})


def test_strategy_create(app, caplog, code, oidc_provider, oidc_provider_jwkset):
    oidc_provider.ou.email_is_unique = True
    oidc_provider.ou.save()

    User.objects.all().delete()

    response = app.get('/').maybe_follow()
    assert oidc_provider.name in response.text
    response = response.click(oidc_provider.name)
    location = urllib.parse.urlparse(response.location)
    query = QueryDict(location.query)
    state = query['state']
    nonce = query['nonce']

    # sub=john.doe
    with utils.check_log(caplog, 'auth_oidc: created user'):
        with oidc_provider_mock(oidc_provider, oidc_provider_jwkset, code, nonce=nonce):
            response = app.get(login_callback_url(oidc_provider), params={'code': code, 'state': state})
        assert User.objects.count() == 1

    # second time
    with oidc_provider_mock(oidc_provider, oidc_provider_jwkset, code, nonce=nonce):
        response = app.get(login_callback_url(oidc_provider), params={'code': code, 'state': state})
    assert User.objects.count() == 1

    # different sub, same user
    with utils.check_log(caplog, 'auth_oidc: changed user'):
        with oidc_provider_mock(oidc_provider, oidc_provider_jwkset, code, sub='other', nonce=nonce):
            response = app.get(login_callback_url(oidc_provider), params={'code': code, 'state': state})
        assert User.objects.count() == 1


def test_strategy_create_normalized_unicode_collision_prevention(
    app, caplog, code, oidc_provider, oidc_provider_jwkset, simple_user
):
    oidc_provider.ou.email_is_unique = True
    oidc_provider.ou.save()

    response = app.get('/').maybe_follow()
    assert oidc_provider.name in response.text
    response = response.click(oidc_provider.name)
    location = urllib.parse.urlparse(response.location)
    query = QueryDict(location.query)
    state = query['state']
    nonce = query['nonce']

    extra_user_info = {'email': 'mike@ıxample.org'}  # dot-less i 'ı' U+0131

    simple_user.email = 'mike@ixample.org'
    simple_user.save()

    with utils.check_log(caplog, 'auth_oidc: created user'):
        with oidc_provider_mock(
            oidc_provider, oidc_provider_jwkset, code, nonce=nonce, extra_user_info=extra_user_info
        ):
            response = app.get(login_callback_url(oidc_provider), params={'code': code, 'state': state})
        assert User.objects.count() == 2


def test_register_issuer(db, app, caplog, oidc_provider_jwkset):
    config_dir = os.path.dirname(__file__)
    config_file = os.path.join(config_dir, 'openid_configuration.json')
    with open(config_file) as f:
        oidc_conf = json.load(f)

    mock_args = {
        'method': 'GET',
        'url': oidc_conf['jwks_uri'],
        'body': oidc_provider_jwkset.export(),
        'match': [any_params_matcher],
        'status': 200,
    }
    with responses.RequestsMock() as rsps:
        rsps.add(**mock_args)
        register_issuer(
            name='test_issuer',
            client_id='abc',
            client_secret='def',
            issuer='https://default.issuer',
            openid_configuration=oidc_conf,
        )

    oidc_conf['id_token_signing_alg_values_supported'] = ['HS256']
    with responses.RequestsMock() as rsps:
        rsps.add(**mock_args)
        register_issuer(
            name='test_issuer_hmac_only',
            client_id='ghi',
            client_secret='jkl',
            issuer='https://hmac_only.issuer',
            openid_configuration=oidc_conf,
        )


def test_required_keys(db, oidc_provider, caplog):
    erroneous_payload = base64url_encode(
        json.dumps(
            {
                'sub': '248289761001',
                'iss': 'http://server.example.com',
                'iat': 1311280970,
                'exp': 1311281970,  # Missing 'aud' and 'nonce' required claims
                'extra_stuff': 'hi there',  # Wrong claim
            }
        ).encode('ascii')
    )

    with pytest.raises(IDTokenError):
        with utils.check_log(caplog, 'missing field'):
            token = IDToken(f'{_header(oidc_provider)}.{erroneous_payload}.{_signature(oidc_provider)}')
            token.deserialize(oidc_provider)


def test_invalid_kid(app, caplog, code, oidc_provider_rsa, oidc_provider_jwkset, simple_user):
    # no mapping please
    OIDCClaimMapping.objects.all().delete()

    assert User.objects.count() == 1

    response = app.get('/').maybe_follow()
    assert oidc_provider_rsa.name in response.text
    response = response.click(oidc_provider_rsa.name)
    location = urllib.parse.urlparse(response.location)
    query = QueryDict(location.query)
    state = query['state']
    nonce = query['nonce']

    # test invalid kid
    with utils.check_log(caplog, message='not in key set', levelname='WARNING'):
        with oidc_provider_mock(
            oidc_provider_rsa, oidc_provider_jwkset, code, nonce=nonce, provides_kid_header=True, kid='coin'
        ):
            response = app.get(login_callback_url(oidc_provider_rsa), params={'code': code, 'state': state})

    # test missing kid
    with utils.check_log(caplog, message='Key ID None not in key set', levelname='WARNING'):
        with oidc_provider_mock(
            oidc_provider_rsa, oidc_provider_jwkset, code, nonce=nonce, provides_kid_header=True, kid=None
        ):
            response = app.get(login_callback_url(oidc_provider_rsa), params={'code': code, 'state': state})


def test_templated_claim_mapping(app, caplog, code, oidc_provider, oidc_provider_jwkset):
    Attribute.objects.create(
        name='pro_phone', label='professonial phone', kind='phone_number', asked_on_registration=True
    )
    # no default mapping
    OIDCClaimMapping.objects.all().delete()

    OIDCClaimMapping.objects.create(
        authenticator=oidc_provider,
        attribute='username',
        idtoken_claim=False,
        claim='{{ given_name }} "{{ nickname }}" {{ family_name }}',
    )
    OIDCClaimMapping.objects.create(
        authenticator=oidc_provider,
        attribute='pro_phone',
        idtoken_claim=False,
        claim='(prefix +33) {{ phone_number }}',
    )
    OIDCClaimMapping.objects.create(
        authenticator=oidc_provider,
        attribute='email',
        idtoken_claim=False,
        claim='{{ given_name }}@foo.bar',
    )
    # last one, with an idtoken claim
    OIDCClaimMapping.objects.create(
        authenticator=oidc_provider,
        attribute='last_name',
        idtoken_claim=True,
        claim='{{ name|upper }}',
    )
    # typo in template string
    OIDCClaimMapping.objects.create(
        authenticator=oidc_provider,
        attribute='first_name',
        idtoken_claim=True,
        claim='{{ given_name',
    )
    oidc_provider.save()

    assert User.objects.count() == 0

    response = app.get('/').maybe_follow()
    response = response.click(oidc_provider.name)
    location = urllib.parse.urlparse(response.location)
    query = QueryDict(location.query)
    state = query['state']
    nonce = query['nonce']

    with oidc_provider_mock(oidc_provider, oidc_provider_jwkset, code, nonce=nonce):
        response = app.get(
            login_callback_url(oidc_provider), params={'code': code, 'state': state}
        ).maybe_follow()

    assert User.objects.count() == 1
    user = User.objects.first()

    assert user.username == 'John "Hefty" Doe'
    assert user.attributes.pro_phone == '(prefix +33) 0123456789'
    assert user.email == 'John@foo.bar'
    assert user.last_name == 'DOE'
    # typo in template string, no rendering
    assert user.first_name == '{{ given_name'


def test_lost_state(app, caplog, code, oidc_provider, oidc_provider_jwkset, hooks):
    response = app.get('/login/?next=/whatever/')
    assert oidc_provider.name in response.text
    response = response.click(oidc_provider.name)
    # As the oidc-state is used during a redirect from a third-party, we need
    # it to be lax.
    assert re.search('Set-Cookie.* oidc-state=.*SameSite=Lax', str(response))
    qs = urllib.parse.parse_qs(urllib.parse.urlparse(response.location).query)
    state = qs['state']

    # reset the session to forget the state
    app.cookiejar.clear()

    caplog.clear()

    def norequest(request):
        assert False, 'no request should be done'

    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        for meth in ('POST', 'GET', 'PATCH', 'PUT'):
            rsps.add_callback(meth, url=re.compile('^.*$'), callback=norequest)
        response = app.get(login_callback_url(oidc_provider), params={'code': code, 'state': state})

    # not logged
    assert re.match('^auth-oidc: state.*has been lost', caplog.records[-1].message)
    # event is recorded
    assert '_auth_user_id' not in app.session
    # we are automatically redirected to our destination
    assert response.location == '/accounts/oidc/login/%s/?next=/whatever/' % oidc_provider.pk


def test_multiple_accounts(db, oidc_provider_jwkset):
    user1 = User.objects.create()
    user2 = User.objects.create()
    provider1 = make_oidc_provider(name='Provider1', jwkset=oidc_provider_jwkset)
    provider2 = make_oidc_provider(name='Provider2', jwkset=oidc_provider_jwkset)
    OIDCAccount.objects.create(user=user1, provider=provider1, sub='1234')
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            OIDCAccount.objects.create(user=user1, provider=provider2, sub='4567')
    OIDCAccount.objects.create(user=user2, provider=provider2, sub='1234')


def test_save_account_on_delete_user(db, oidc_provider_jwkset):
    provider = make_oidc_provider(name='Provider1', jwkset=oidc_provider_jwkset)
    user = User.objects.create()
    OIDCAccount.objects.create(user=user, provider=provider, sub='1234')

    user.delete()
    assert OIDCAccount.objects.count() == 0

    deleted_user = DeletedUser.objects.get()
    assert deleted_user.old_data.get('oidc_accounts') == [
        {
            'issuer': 'https://provider1.example.com',
            'sub': '1234',
        }
    ]


def test_multiple_users_with_same_email(app, caplog, code, oidc_provider_jwkset, hooks):
    oidc_provider = make_oidc_provider(idtoken_algo=OIDCProvider.ALGO_HMAC, jwkset=oidc_provider_jwkset)
    ou = get_default_ou()
    ou.email_is_unique = True
    ou.save()

    user1 = User.objects.create(ou=ou, email='john.doe@example.com')

    assert OIDCAccount.objects.count() == 0

    response = app.get('/').maybe_follow()
    assert oidc_provider.name in response.text
    response = response.click(oidc_provider.name)
    location = urllib.parse.urlparse(response.location)
    query = QueryDict(location.query)
    state = query['state']
    nonce = query['nonce']

    # sub=john.doe, MUST not work
    with oidc_provider_mock(oidc_provider, oidc_provider_jwkset, code, nonce=nonce):
        response = app.get(login_callback_url(oidc_provider), params={'code': code, 'state': state})

    assert app.session['_auth_user_id'] == str(user1.id)
    assert OIDCAccount.objects.count() == 1

    app.session.flush()
    OIDCAccount.objects.all().delete()
    User.objects.create(ou=ou, email='john.doe@example.com')

    response = app.get('/').maybe_follow()
    assert oidc_provider.name in response.text
    response = response.click(oidc_provider.name)
    location = urllib.parse.urlparse(response.location)
    query = QueryDict(location.query)
    state = query['state']
    nonce = query['nonce']

    assert OIDCAccount.objects.count() == 0

    # sub=john.doe, MUST not work
    with oidc_provider_mock(oidc_provider, oidc_provider_jwkset, code, nonce=nonce):
        response = app.get(login_callback_url(oidc_provider), params={'code': code, 'state': state})

    assert '_auth_user_id' not in app.session
    assert OIDCAccount.objects.count() == 0
    assert 'too many users' in caplog.records[-1].message


def test_no_user_found_error(app, caplog, code, oidc_provider, oidc_provider_jwkset, hooks, simple_user):
    OIDCClaimMapping.objects.all().delete()
    oidc_provider.strategy = oidc_provider.STRATEGY_NONE
    oidc_provider.save()

    response = app.get('/').maybe_follow()
    assert oidc_provider.name in response.text
    response = response.click(oidc_provider.name)
    location = urllib.parse.urlparse(response.location)
    query = QueryDict(location.query)
    state = query['state']
    nonce = query['nonce']

    Event.objects.all().delete()
    # sub=simple_user.uuid MUST not work
    with utils.check_log(caplog, 'cannot create user'):
        with oidc_provider_mock(oidc_provider, oidc_provider_jwkset, code, sub=simple_user.uuid, nonce=nonce):
            response = app.get(login_callback_url(oidc_provider), params={'code': code, 'state': state})

    assert len(caplog.records) == 1
    assert caplog.records[0].msg == 'auth_oidc: cannot create user for sub %r as issuer %r does not allow it'
    assert caplog.records[0].args == (simple_user.uuid, oidc_provider.issuer)

    assert Event.objects.count() == 1
    event = Event.objects.get()
    assert event.message == 'Cannot create user for sub "%s" as issuer "%s" does not allow it' % (
        simple_user.uuid,
        oidc_provider.issuer,
    )


def test_strategy_find_username(app, caplog, code, oidc_provider, oidc_provider_jwkset, simple_user):
    # no mapping please
    OIDCClaimMapping.objects.all().delete()
    oidc_provider.strategy = oidc_provider.STRATEGY_FIND_USERNAME
    oidc_provider.save()

    assert User.objects.count() == 1

    response = app.get('/').maybe_follow()
    assert oidc_provider.name in response.text
    response = response.click(oidc_provider.name)
    location = urllib.parse.urlparse(response.location)
    query = QueryDict(location.query)
    state = query['state']
    nonce = query['nonce']

    # sub=simple_user.uuid MUST not work
    with utils.check_log(caplog, 'cannot create user'):
        with oidc_provider_mock(oidc_provider, oidc_provider_jwkset, code, sub=simple_user.uuid, nonce=nonce):
            response = app.get(login_callback_url(oidc_provider), params={'code': code, 'state': state})

    # sub=john.doe, MUST not work
    with utils.check_log(caplog, 'cannot create user'):
        with oidc_provider_mock(oidc_provider, oidc_provider_jwkset, code, nonce=nonce):
            response = app.get(login_callback_url(oidc_provider), params={'code': code, 'state': state})

    simple_user.username = 'john.doe'
    simple_user.save()

    # sub=john.doe, MUST work
    with utils.check_log(caplog, 'found user using username'):
        with oidc_provider_mock(oidc_provider, oidc_provider_jwkset, code, nonce=nonce):
            response = app.get(login_callback_url(oidc_provider), params={'code': code, 'state': state})


def test_error_access_denied(app, caplog, oidc_provider_jwkset):
    oidc_provider = make_oidc_provider(jwkset=oidc_provider_jwkset)
    response = app.get('/login/')
    response = response.click(oidc_provider.name)
    location = urllib.parse.urlparse(response.location)
    query = QueryDict(location.query)
    state = query['state']

    response = app.get(login_callback_url(oidc_provider), params={'error': 'access_denied', 'state': state})

    response = response.maybe_follow()

    assert 'denied by you or the identity provider' in caplog.records[-1].message
    assert caplog.records[-1].levelname == 'INFO'
    assert 'denied by you or the identity provider' in response.pyquery('.info').text()
    assert 'access_denied' not in response  # error code not logged in UI anymore

    response = app.get(
        login_callback_url(oidc_provider),
        params={
            'error': 'access_denied',
            'error_description': 'some OP technical error message',
            'state': state,
        },
    )
    response = response.maybe_follow()
    assert 'denied by you or the identity provider' not in caplog.records[-1].message
    assert 'some OP technical error message' in caplog.records[-1].message

    with override_settings(MESSAGE_LEVEL=message_constants.DEBUG):
        response = app.get(
            login_callback_url(oidc_provider),
            params={
                'error': 'access_denied',
                'error_description': 'some OP technical error message',
                'state': state,
            },
        )

        response = response.maybe_follow()
        assert 'denied by you or the identity provider' in response.pyquery('.info').text()
        assert 'some OP technical error message' in response.pyquery('.debug').text()


def test_error_other(app, caplog, oidc_provider_jwkset):
    oidc_provider = make_oidc_provider(jwkset=oidc_provider_jwkset)
    response = app.get('/login/')
    response = response.click(oidc_provider.name)
    location = urllib.parse.urlparse(response.location)
    query = QueryDict(location.query)
    state = query['state']

    response = app.get(login_callback_url(oidc_provider), params={'error': 'misc_error', 'state': state})

    response = response.maybe_follow()

    assert 'misc_error' in caplog.records[-1].message
    assert caplog.records[-1].levelname == 'WARNING'
    assert 'misc_error' in response


def test_link_by_email(app, caplog, code, oidc_provider_jwkset):
    oidc_provider = make_oidc_provider(idtoken_algo=OIDCProvider.ALGO_HMAC, jwkset=oidc_provider_jwkset)
    ou = get_default_ou()
    ou.email_is_unique = True
    ou.save()

    user = User.objects.create(ou=ou, email='john.doe@example.com')
    assert User.objects.count() == 1
    assert OIDCAccount.objects.count() == 0

    response = app.get('/login/')
    response = response.click(oidc_provider.name)
    location = urllib.parse.urlparse(response.location)
    query = QueryDict(location.query)
    state = query['state']
    nonce = query['nonce']

    with oidc_provider_mock(
        oidc_provider,
        oidc_provider_jwkset,
        code,
        nonce=nonce,
        extra_user_info={'email': 'JOHN.DOE@examplE.COM'},
    ):
        response = app.get(login_callback_url(oidc_provider), params={'code': code, 'state': state})

    assert app.session['_auth_user_id'] == str(user.id)
    assert User.objects.count() == 1
    assert OIDCAccount.objects.count() == 1

    # verify that email change is possible
    resp = app.get('/accounts/')
    assert 'Change email' in resp.text

    provider = OIDCAccount.objects.get().provider
    provider.allow_user_change_email = False
    provider.save()

    # verify that email change is impossible
    resp = app.get('/accounts/')
    assert 'Change email' not in resp.text
    resp = app.get('/accounts/change-email/', status=404)


def test_auth_time_is_null(app, caplog, code, oidc_provider, oidc_provider_jwkset):
    response = app.get('/').maybe_follow()
    assert oidc_provider.name in response.text
    response = response.click(oidc_provider.name)
    location = urllib.parse.urlparse(response.location)
    query = QueryDict(location.query)
    state = query['state']
    nonce = query['nonce']

    # sub=john.doe
    with utils.check_log(caplog, 'auth_oidc: created user'):
        with oidc_provider_mock(
            oidc_provider, oidc_provider_jwkset, code, nonce=nonce, extra_id_token={'auth_time': None}
        ):
            response = app.get(login_callback_url(oidc_provider), params={'code': code, 'state': state})
        assert User.objects.count() == 1


@pytest.mark.parametrize(
    'auth_frontend_kwargs',
    [
        {'oidc': {'priority': 3, 'show_condition': '"backoffice" not in login_hint'}},
        {'oidc': {'show_condition': {'baz': '"backoffice" not in login_hint', 'bar': 'True'}}},
    ],
)
def test_oidc_provider_authenticator_data_migration(auth_frontend_kwargs, migration, settings):
    settings.AUTH_FRONTENDS_KWARGS = auth_frontend_kwargs

    app = 'authentic2_auth_oidc'
    migrate_from = [(app, '0008_auto_20201102_1142')]
    migrate_to = [(app, '0012_auto_20220524_1147')]

    old_apps = migration.before(migrate_from)
    OIDCProvider = old_apps.get_model(app, 'OIDCProvider')
    OIDCClaimMapping = old_apps.get_model(app, 'OIDCClaimMapping')
    OIDCAccount = old_apps.get_model(app, 'OIDCAccount')
    OrganizationalUnit = old_apps.get_model('a2_rbac', 'OrganizationalUnit')
    User = old_apps.get_model('custom_user', 'User')
    ou1 = OrganizationalUnit.objects.create(name='OU1', slug='ou1')
    issuer = 'https://baz.example.com'
    first_provider = OIDCProvider.objects.create(
        name='Baz',
        slug='baz',
        ou=ou1,
        show=True,
        issuer=issuer,
        authorization_endpoint='%s/authorize' % issuer,
        token_endpoint='%s/token' % issuer,
        end_session_endpoint='%s/logout' % issuer,
        userinfo_endpoint='%s/user_info' % issuer,
        token_revocation_endpoint='%s/revoke' % issuer,
    )
    second_provider = OIDCProvider.objects.create(name='Second', slug='second', ou=ou1)
    second_provider_claim_mapping = OIDCClaimMapping.objects.create(
        provider=second_provider, claim='second_provider', attribute='username'
    )
    user1 = User.objects.create()
    second_provider_account = OIDCAccount.objects.create(
        user=user1, provider=second_provider, sub='second_provider'
    )
    first_provider_claim_mapping = OIDCClaimMapping.objects.create(
        provider=first_provider, claim='first_provider', attribute='username'
    )

    new_apps = migration.apply(migrate_to)
    OIDCProvider = new_apps.get_model(app, 'OIDCProvider')
    BaseAuthenticator = new_apps.get_model('authenticators', 'BaseAuthenticator')

    authenticator = OIDCProvider.objects.get(slug='baz')
    assert authenticator.name == 'Baz'
    assert authenticator.ou.pk == ou1.pk
    assert authenticator.enabled is True
    assert authenticator.order == auth_frontend_kwargs['oidc'].get('priority', 2)
    assert authenticator.show_condition == '"backoffice" not in login_hint'
    assert authenticator.authorization_endpoint == '%s/authorize' % issuer
    assert authenticator.claim_mappings.count() == 1
    assert authenticator.claim_mappings.get().pk == first_provider_claim_mapping.pk
    assert not authenticator.accounts.exists()

    base_authenticator = BaseAuthenticator.objects.get(slug='baz')
    assert authenticator.uuid == base_authenticator.uuid

    second_authenticator = OIDCProvider.objects.get(slug='second')
    assert second_authenticator.name == 'Second'
    assert second_authenticator.claim_mappings.count() == 1
    assert second_authenticator.claim_mappings.get().pk == second_provider_claim_mapping.pk
    assert second_authenticator.accounts.count() == 1
    assert second_authenticator.accounts.get().pk == second_provider_account.pk


def test_only_idtoken_claims(app, caplog, code, oidc_provider, oidc_provider_jwkset):
    oidc_provider.claim_mappings.update(idtoken_claim=True)
    response = app.get('/').maybe_follow()
    assert oidc_provider.name in response.text
    response = response.click(oidc_provider.name)
    location = urllib.parse.urlparse(response.location)
    query = QueryDict(location.query)
    state = query['state']
    nonce = query['nonce']

    # sub=john.doe
    extra_id_token = {
        'given_name': 'John',
        'family_name': 'Doe',
        'email': 'john.doe@example.com',
    }
    with utils.check_log(caplog, 'missing required claim'):
        with oidc_provider_mock(
            oidc_provider,
            oidc_provider_jwkset,
            code,
            nonce=nonce,
        ):
            response = app.get(
                login_callback_url(oidc_provider), params={'code': code, 'state': state}
            ).maybe_follow()
            assert 'Your account is misconfigured, missing required claim email.' in response
        assert User.objects.count() == 0

    with utils.check_log(caplog, 'auth_oidc: created user'):
        with oidc_provider_mock(
            oidc_provider,
            oidc_provider_jwkset,
            code,
            nonce=nonce,
            extra_id_token=extra_id_token,
        ):
            response = app.get(login_callback_url(oidc_provider), params={'code': code, 'state': state})
        assert User.objects.count() == 1


@pytest.mark.parametrize('cache_enabled', (True, False))
@pytest.mark.parametrize('missing_claim', ('given_name', 'family_name', 'email'))
def test_claim_error_missing(
    app, caplog, code, oidc_provider, oidc_provider_jwkset, missing_claim, cache_enabled
):

    # When cache is enable some issuer/provider infos are cached
    # we will need to clear them in order to trigger expected errors
    cache_to_clear = (get_provider_by_issuer, get_provider, get_attributes)

    with override_settings(A2_CACHE_ENABLED=cache_enabled):
        oidc_provider.claim_mappings.update(idtoken_claim=True)
        response = app.get('/').maybe_follow()
        assert oidc_provider.name in response.text
        response = response.click(oidc_provider.name)
        location = urllib.parse.urlparse(response.location)
        query = QueryDict(location.query)
        state = query['state']
        nonce = query['nonce']

        # sub=john.doe
        extra = {
            'given_name': 'John',
            'family_name': 'Doe',
            'email': 'john.doe@example.com',
        }
        del extra[missing_claim]
        for func in cache_to_clear:
            func.cache.clear()

        Event.objects.all().delete()
        EventType.objects.all().delete()
        caplog.clear()
        with utils.check_log(caplog, 'missing required claim'):
            with oidc_provider_mock(
                oidc_provider,
                oidc_provider_jwkset,
                code,
                nonce=nonce,
                extra_id_token=extra,
            ):
                response = app.get(login_callback_url(oidc_provider), params={'code': code, 'state': state})
        assert Event.objects.count() == 1
        event = Event.objects.get()
        assert (
            event.message == 'Missconfigured account, missing required claim %s in id_token' % missing_claim
        )
        assert User.objects.count() == 0

        oidc_provider.claim_mappings.update(idtoken_claim=False)
        Event.objects.all().delete()
        caplog.clear()

        for func in cache_to_clear:
            func.cache.clear()
        with utils.check_log(caplog, 'missing required claim'):
            with oidc_provider_mock(
                oidc_provider,
                oidc_provider_jwkset,
                code,
                nonce=nonce,
                missing_user_info=[missing_claim],
            ):
                response = app.get(login_callback_url(oidc_provider), params={'code': code, 'state': state})
        assert Event.objects.count() == 1
        event = Event.objects.get()
        assert (
            event.message == 'Missconfigured account, missing required claim %s in user_info' % missing_claim
        )
        assert User.objects.count() == 0


@pytest.mark.parametrize('claim_name,claim_value', (('given_name', '@@/\x00'), ('family_name', '\x00')))
def test_claim_error_invalid(app, caplog, code, oidc_provider, oidc_provider_jwkset, claim_name, claim_value):

    oidc_provider.claim_mappings.update(idtoken_claim=True)
    response = app.get('/').maybe_follow()
    assert oidc_provider.name in response.text
    response = response.click(oidc_provider.name)
    location = urllib.parse.urlparse(response.location)
    query = QueryDict(location.query)
    state = query['state']
    nonce = query['nonce']

    # sub=john.doe
    extra = {
        'given_name': 'John',
        'family_name': 'Doe',
        'email': 'john.doe@example.com',
    }
    extra[claim_name] = claim_value

    Event.objects.all().delete()
    caplog.clear()
    with utils.check_log(caplog, 'invalid value for required claim'):
        with oidc_provider_mock(
            oidc_provider,
            oidc_provider_jwkset,
            code,
            nonce=nonce,
            extra_id_token=extra,
        ):
            response = app.get(login_callback_url(oidc_provider), params={'code': code, 'state': state})
    assert Event.objects.count() == 1
    event = Event.objects.get()
    assert (
        event.message
        == 'Missconfigured account, invalid value for required claim %s in id_token' % claim_name
    )
    assert User.objects.count() == 0

    oidc_provider.claim_mappings.update(idtoken_claim=False)
    Event.objects.all().delete()
    caplog.clear()
    with utils.check_log(caplog, 'invalid value for required claim'):
        with oidc_provider_mock(
            oidc_provider,
            oidc_provider_jwkset,
            code,
            nonce=nonce,
            extra_user_info={claim_name: claim_value},
        ):
            response = app.get(login_callback_url(oidc_provider), params={'code': code, 'state': state})
    assert Event.objects.count() == 1
    event = Event.objects.get()
    assert (
        event.message
        == 'Missconfigured account, invalid value for required claim %s in user_info' % claim_name
    )
    assert User.objects.count() == 0


def test_oidc_add_role(app, code, oidc_provider, oidc_provider_jwkset, simple_role, role_random, role_ou1):
    oidc_provider.add_role_actions.create(role=simple_role)
    oidc_provider.add_role_actions.create(role=role_random, condition='"Test" in attributes.groups')
    oidc_provider.add_role_actions.create(role=role_ou1, condition='"Unknown" in attributes.groups')

    Event.objects.all().delete()
    response = app.get('/').maybe_follow()
    response = response.click(oidc_provider.name)
    location = urllib.parse.urlparse(response.location)
    query = QueryDict(location.query)
    state = query['state']
    nonce = query['nonce']

    with oidc_provider_mock(
        oidc_provider, oidc_provider_jwkset, code, nonce=nonce, extra_user_info={'groups': ['Test']}
    ):
        response = app.get(login_callback_url(oidc_provider), params={'code': code, 'state': state})

    user = User.objects.get()
    assert simple_role in user.roles.all()
    assert role_random in user.roles.all()
    assert role_ou1 not in user.roles.all()
    events = Event.objects.all()
    assert ['auth.oidc.add_role_action', 'auth.oidc.add_role_action', 'user.login'] == [
        evt.type.name for evt in events
    ]
    assert {
        'adding role "simple role" to user "John Doe"',
        'adding role "rando" to user "John Doe" based on condition : "Test" in attributes.groups',
    } == {evt.message for evt in events[:2]}
    Event.objects.all().delete()

    with oidc_provider_mock(
        oidc_provider, oidc_provider_jwkset, code, nonce=nonce, extra_user_info={'groups': ['New group']}
    ):
        response = app.get(login_callback_url(oidc_provider), params={'code': code, 'state': state})
    assert role_random not in user.roles.all()
    events = Event.objects.all()
    assert ['auth.oidc.add_role_action', 'user.login'] == [evt.type.name for evt in events]
    assert (
        'removing role "rando" to user "John Doe" based on condition : "Test" in attributes.groups'
        == events[0].message
    )
    Event.objects.all().delete()

    with oidc_provider_mock(oidc_provider, oidc_provider_jwkset, code, nonce=nonce):
        response = app.get(login_callback_url(oidc_provider), params={'code': code, 'state': state})
    assert role_random not in user.roles.all()
    assert role_ou1 not in user.roles.all()
    assert ['user.login'] == [evt.type.name for evt in Event.objects.all()]
    Event.objects.all().delete()

    # sub=john.doe
    extra_id_token = {
        'given_name': 'John',
        'family_name': 'Doe',
        'email': 'john.doe@example.com',
        'groups': 'Test',
    }

    # no user info retreived because all claims present in idtoken
    oidc_provider.claim_mappings.update(idtoken_claim=True)
    with oidc_provider_mock(
        oidc_provider, oidc_provider_jwkset, code, nonce=nonce, extra_id_token=extra_id_token
    ):
        response = app.get(login_callback_url(oidc_provider), params={'code': code, 'state': state})

    assert role_random in user.roles.all()
    assert role_ou1 not in user.roles.all()
    events = Event.objects.all()
    assert ['auth.oidc.add_role_action', 'user.login'] == [evt.type.name for evt in events]
    assert (
        'adding role "rando" to user "John Doe" based on condition : "Test" in attributes.groups'
        == events[0].message
    )
    Event.objects.all().delete()

    extra_id_token.pop('groups')
    with oidc_provider_mock(
        oidc_provider, oidc_provider_jwkset, code, nonce=nonce, extra_id_token=extra_id_token
    ):
        response = app.get(login_callback_url(oidc_provider), params={'code': code, 'state': state})

    assert role_random not in user.roles.all()
    assert role_ou1 not in user.roles.all()

    events = Event.objects.all()
    assert ['auth.oidc.add_role_action', 'user.login'] == [evt.type.name for evt in events]
    assert (
        'removing role "rando" to user "John Doe" based on condition : "Test" in attributes.groups'
        == events[0].message
    )
    Event.objects.all().delete()


def test_oidc_unicity_contraint_issuer(db):
    OIDCProvider.objects.create(issuer='', slug='a')
    OIDCProvider.objects.create(issuer='', slug='b')
    OIDCProvider.objects.create(issuer='test', slug='c')

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            OIDCProvider.objects.create(issuer='test', slug='d')


def test_double_link(app, caplog, code, simple_user, oidc_provider_jwkset):
    ou = get_default_ou()
    ou.email_is_unique = True
    ou.save()
    provider1 = make_oidc_provider(name='provider1', jwkset=oidc_provider_jwkset)
    provider2 = make_oidc_provider(name='provider2', jwkset=oidc_provider_jwkset)

    OIDCAccount.objects.create(provider=provider2, sub='1234', user=simple_user)

    response = app.get('/').maybe_follow()
    response = response.click('provider1')
    location = urllib.parse.urlparse(response.location)
    query = QueryDict(location.query)
    state = query['state']
    nonce = query['nonce']

    # sub=john.doe
    with utils.check_log(caplog, 'auth_oidc: email user@example.net is already linked'):
        with oidc_provider_mock(
            provider1,
            oidc_provider_jwkset,
            code,
            nonce=nonce,
            extra_id_token={'email': simple_user.email},
            extra_user_info={'email': simple_user.email},
        ):
            response = app.get(login_callback_url(provider1), params={'code': code, 'state': state})
        response = response.maybe_follow()
    warnings = response.pyquery('.warning')
    assert len(warnings) == 1
    assert 'Your email is already linked' in warnings.text()


@mock.patch('authentic2_auth_oidc.views.get_provider')
def test_oidc_login(get_provider, rf):
    AUTHORIZE_URL = 'https://op.example.com/authorize'
    SCOPES = {'profile'}

    provider = OIDCProvider(
        pk=1, client_id='1234', authorization_endpoint=AUTHORIZE_URL, scopes=' '.join(SCOPES)
    )
    get_provider.return_value = provider

    url = oidc_login(rf.get('/', secure=True), 1, next_url='/idp/x/').url
    assert url
    prefix, query = url.split('?', 1)
    assert prefix == AUTHORIZE_URL
    qs = dict(urllib.parse.parse_qsl(query))
    assert qs['client_id'] == '1234'
    assert qs['nonce']
    assert qs['state']
    assert qs['redirect_uri'] == 'https://testserver/accounts/oidc/callback/'
    assert 'ui_locales' not in qs
    assert set(qs['scope'].split()) == {'profile', 'openid'}
    assert 'prompt' not in qs

    # passive
    url = oidc_login(rf.get('/', secure=True), 1, next_url='/idp/x/', passive=True).url
    prefix, query = url.split('?', 1)
    qs = dict(urllib.parse.parse_qsl(query))
    assert qs['prompt'] == 'none'

    # not passive
    url = oidc_login(rf.get('/', secure=True), 1, next_url='/idp/x/', passive=False).url
    prefix, query = url.split('?', 1)
    qs = dict(urllib.parse.parse_qsl(query))
    assert qs['prompt'] == 'login'


@mock.patch('authentic2_auth_oidc.views.get_provider')
def test_autorun(get_provider, rf):
    AUTHORIZE_URL = 'https://op.example.com/authorize'
    SCOPES = {'profile'}

    provider = OIDCProvider(
        pk=1, client_id='1234', authorization_endpoint=AUTHORIZE_URL, scopes=' '.join(SCOPES)
    )
    get_provider.return_value = provider
    req = rf.get('/?next=/idp/x/')
    req.user = mock.Mock()
    req.user.is_authenticated = False

    url = provider.autorun(req, block_id=1, next_url='/').url
    _, query = url.split('?', 1)
    qs = dict(urllib.parse.parse_qsl(query))
    assert 'prompt' not in qs


@mock.patch('authentic2_auth_oidc.views.get_provider')
def test_passive_login(get_provider, rf):
    AUTHORIZE_URL = 'https://op.example.com/authorize'
    SCOPES = {'profile'}

    provider = OIDCProvider(
        pk=1, client_id='1234', authorization_endpoint=AUTHORIZE_URL, scopes=' '.join(SCOPES)
    )
    get_provider.return_value = provider
    req = rf.get('/?next=/idp/x/')
    req.user = mock.Mock()
    req.user.is_authenticated = False

    url = provider.passive_login(req, block_id=1, next_url='/').url
    _, query = url.split('?', 1)
    qs = dict(urllib.parse.parse_qsl(query))
    assert qs['prompt'] == 'none'


@mock.patch('authentic2_auth_oidc.views.get_provider')
def test_passive_login_deactivated(get_provider, rf):
    AUTHORIZE_URL = 'https://op.example.com/authorize'
    SCOPES = {'profile'}

    provider = OIDCProvider.objects.create(
        pk=1,
        client_id='1234',
        authorization_endpoint=AUTHORIZE_URL,
        scopes=' '.join(SCOPES),
        enabled=True,
        passive_authn_supported=False,  # remote provider will break on prompt=None
    )
    get_provider.return_value = provider
    req = rf.get('/?next=/idp/x/')
    req.user = mock.Mock()
    req.user.is_authenticated = False

    url = provider.passive_login(req, block_id=1, next_url='/').url
    _, query = url.split('?', 1)
    qs = dict(urllib.parse.parse_qsl(query))
    assert qs['prompt'] == 'login'


@mock.patch('authentic2_auth_oidc.views.get_provider')
def test_passive_login_main_view(get_provider, rf):
    AUTHORIZE_URL = 'https://op.example.com/authorize'
    SCOPES = {'profile'}

    provider = OIDCProvider.objects.create(
        pk=1,
        client_id='1234',
        authorization_endpoint=AUTHORIZE_URL,
        scopes=' '.join(SCOPES),
        passive_authn_supported=True,
        enabled=True,
    )
    get_provider.return_value = provider
    req = rf.get('/')
    req.user = mock.Mock()
    req.user.is_authenticated = False
    req.session = {}

    response = passive_login(req, next_url='/manage/')
    assert response.status_code == 302
    assert response.url.startswith('https://op.example.com/authorize?')
    _, query = response.url.split('?', 1)
    qs = dict(urllib.parse.parse_qsl(query))
    assert qs['prompt'] == 'none'


@mock.patch('authentic2_auth_oidc.views.get_provider')
def test_passive_login_main_view_deactivated(get_provider, rf):
    AUTHORIZE_URL = 'https://op.example.com/authorize'
    SCOPES = {'profile'}

    provider = OIDCProvider.objects.create(
        pk=1,
        client_id='1234',
        authorization_endpoint=AUTHORIZE_URL,
        scopes=' '.join(SCOPES),
        passive_authn_supported=False,
        enabled=True,
    )
    get_provider.return_value = provider
    req = rf.get('/')
    req.user = mock.Mock()
    req.user.is_authenticated = False
    req.session = {}

    response = passive_login(req, next_url='/manage/')
    assert response is None


def test_missing_jwkset(app, caplog, code, simple_user, oidc_provider_jwkset, settings):
    provider1 = make_oidc_provider(idtoken_algo=OIDCProvider.ALGO_HMAC, name='provider1')

    response = app.get('/').maybe_follow()
    response = response.click('provider1')
    location = urllib.parse.urlparse(response.location)
    query = QueryDict(location.query)
    state = query['state']
    nonce = query['nonce']

    # sub=john.doe
    with oidc_provider_mock(
        provider1,
        oidc_provider_jwkset,
        code,
        nonce=nonce,
        idtoken_algo=OIDCProvider.ALGO_RSA,
        extra_id_token={'email': simple_user.email},
        extra_user_info={'email': simple_user.email},
    ):
        response = app.get(login_callback_url(provider1), params={'code': code, 'state': state})
        response = response.maybe_follow()
        assert [elt.text() for elt in response.pyquery('.messages .warning').items()] == [
            'OpenIDConnect provider provider1 is currently down.',
        ]

        settings.DEBUG = True
        response = app.get(login_callback_url(provider1), params={'code': code, 'state': state})
        response = response.maybe_follow()
        assert [elt.text() for elt in response.pyquery('.messages .warning').items()] == [
            'OpenIDConnect provider provider1 is currently down.',
            'Unable to validate the idtoken: Key ID \'1e9gdk7\' not in key set',
        ]


def test_bad_claim_value(app, caplog, code, oidc_provider, oidc_provider_jwkset):
    oidc_provider.claim_mappings.update(idtoken_claim=True)
    Attribute.objects.create(kind='title', name='title', label='title')
    oidc_provider.claim_mappings.create(claim='title', attribute='title', required=True, idtoken_claim=True)

    response = app.get('/').maybe_follow()
    assert oidc_provider.name in response.text
    response = response.click(oidc_provider.name)
    location = urllib.parse.urlparse(response.location)
    query = QueryDict(location.query)
    state = query['state']
    nonce = query['nonce']

    # sub=john.doe
    extra_id_token = {
        'given_name': 'John',
        'family_name': 'Doe',
        'email': 'john.doe@example.com',
        'title': 'xxx',
    }
    with utils.check_log(caplog, 'invalid value for required claim'):
        with oidc_provider_mock(
            oidc_provider,
            oidc_provider_jwkset,
            code,
            nonce=nonce,
            extra_id_token=extra_id_token,
        ):
            response = app.get(
                login_callback_url(oidc_provider), params={'code': code, 'state': state}
            ).maybe_follow()
            assert 'Your account is misconfigured, invalid value for required claim title.' in response
        assert User.objects.count() == 0

    Attribute.objects.create(kind='fr_phone_number', name='phone', label='Phone')
    oidc_provider.claim_mappings.create(claim='phone', attribute='phone', required=True, idtoken_claim=True)

    extra_id_token.update({'title': 'Mrs', 'phone': '....'})

    with utils.check_log(caplog, 'invalid value for required claim'):
        with oidc_provider_mock(
            oidc_provider,
            oidc_provider_jwkset,
            code,
            nonce=nonce,
            extra_id_token=extra_id_token,
        ):
            response = app.get(
                login_callback_url(oidc_provider), params={'code': code, 'state': state}
            ).maybe_follow()
            assert 'Your account is misconfigured, invalid value for required claim phone.' in response
        assert User.objects.count() == 0
