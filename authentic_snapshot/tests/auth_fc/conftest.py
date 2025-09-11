# authentic2-auth-fc - authentic2 authentication for FranceConnect
# Copyright (C) 2019 Entr'ouvert
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
import contextlib
import datetime
import json
import re
import urllib.parse
import uuid

import pytest
import responses
from django.http import QueryDict
from django.urls import reverse
from django.utils.http import urlencode
from django.utils.timezone import now
from jwcrypto import jwk, jwt

from authentic2.a2_rbac.utils import get_default_ou
from authentic2.models import Service
from authentic2.utils.misc import make_url
from authentic2_auth_fc.models import FcAuthenticator

from ..utils import assert_equals_url

CLIENT_ID = 'xxx'
CLIENT_SECRET = 'yyy'
KID_RSA = '1e9gdk7'
KID_EC = 'jb20Cg8'


@pytest.fixture
def v2_jwkset():
    key_rsa = jwk.JWK.generate(kty='RSA', size=1024, kid=KID_RSA)
    key_ec = jwk.JWK.generate(kty='EC', size=256, kid=KID_EC)
    jwkset = jwk.JWKSet()
    jwkset.add(key_rsa)
    jwkset.add(key_ec)
    return jwkset


class FranceConnectMock:
    exp = None
    token_endpoint_response = None
    user_info_endpoint_response = None

    def __init__(self):
        self.sub = '1234'
        self.authn = FcAuthenticator.objects.get()
        self.user_info = {
            'family_name': 'Frédérique',
            'given_name': 'Ÿuñe',
            'email': 'john.doe@example.com',
        }
        self.access_token = str(uuid.uuid4())
        self.client_id = CLIENT_ID
        self.client_secret = CLIENT_SECRET
        self.scopes = {'openid', 'profile', 'email'}
        self.callback_params = {'service': 'portail', 'next': '/idp/'}
        self.id_token_update = {}
        self.jwkset = None

    @property
    def url_prefix(self):
        self.authn.refresh_from_db()
        if self.authn.platform == 'test':
            if self.authn.version == '1':
                return 'https://fcp.integ01'
            else:
                return 'https://fcp-low.integ01'
        else:
            if self.authn.version == '1':
                return 'https://app.franceconnect.gouv.fr'
            else:
                return 'https://oidc.franceconnect.gouv.fr'

    def initialize_callback_params(self, url, buggy_state=False, **kwargs):
        assert url.startswith(self.url_prefix), f'wrong authorize url {url}'
        parsed_url = urllib.parse.urlparse(url)
        query = QueryDict(parsed_url.query)
        assert_equals_url(query['redirect_uri'], self.callback_url)
        assert query['client_id'] == self.client_id
        assert set(query['scope'].split()) == self.scopes
        assert query['state']
        assert query['nonce']
        assert query['response_type'] == 'code'
        assert query['acr_values'] == 'eidas1'
        assert query['prompt'] == 'login consent'  # mandatory fixed value from v2 onwards
        self.state = query['state']
        if buggy_state:
            self.state = self.state + 'def'
        self.nonce = query['nonce']
        self.code = str(uuid.uuid4().hex)

    def handle_authorization(self, app, url, **kwargs):
        buggy_state = kwargs.pop('buggy_state', False)
        self.initialize_callback_params(url, buggy_state=buggy_state, **kwargs)
        params = {'code': self.code, 'state': self.state}
        if self.iss:
            params['iss'] = self.iss
        for extra_qs_param in ('error', 'error_description', 'iss'):
            if extra_qs_param in kwargs:
                params[extra_qs_param] = kwargs.pop(extra_qs_param)
        return app.get(make_url(self.callback_url, params=params), **kwargs)

    @property
    def callback_url(self):
        return 'https://testserver' + reverse('fc-login-or-link')

    def login_with_fc_fixed_params(self, app):
        if app.session:
            app.session.flush()
        response = app.get('/login/?' + urlencode(self.callback_params))
        response = response.click(href='callback')
        return self.handle_authorization(app, response.location, status=302)

    def login_with_fc(self, app, path):
        if app.session:
            app.session.flush()
        response = app.get(path)
        self.callback_params = {
            k: v for k, v in QueryDict(urllib.parse.urlparse(response.location).query).items()
        }
        response = response.follow()
        response = response.click(href='callback')
        return self.handle_authorization(app, response.location, status=302)

    @property
    def iss(self):
        self.authn.refresh_from_db()
        if self.authn.platform == 'test':
            if self.authn.version == '2':
                return 'https://fcp-low.integ01.dev-franceconnect.fr/api/v2'
        else:
            if self.authn.version == '2':
                return 'https://oidc.franceconnect.gouv.fr/api/v2'

    @property
    def version(self):
        self.authn.refresh_from_db()
        return self.authn.version

    def access_token_response(self, request):
        if self.token_endpoint_response:
            return self.token_endpoint_response

        formdata = QueryDict(request.body)
        assert set(formdata.keys()) == {'code', 'client_id', 'client_secret', 'redirect_uri', 'grant_type'}
        assert formdata['code'] == self.code
        assert formdata['client_id'] == self.client_id
        assert formdata['client_secret'] == self.client_secret
        assert formdata['grant_type'] == 'authorization_code'
        assert_equals_url(formdata['redirect_uri'], self.callback_url)

        id_token = {
            'aud': 'xxx',
            'sub': self.sub,
            'nonce': self.nonce,
            'exp': int((self.exp or (now() + datetime.timedelta(seconds=60))).timestamp()),
            'iss': self.iss,
        }
        if self.version == '1':
            id_token.update(self.user_info)
        id_token.update(self.id_token_update)
        return (
            200,
            {'Content-Type': 'application/json'},
            json.dumps(
                {'access_token': self.access_token, 'id_token': self.hmac_jwt(id_token, self.client_secret)}
            ),
        )

    def hmac_jwt(self, payload, key):
        header = {'alg': 'HS256'}
        k = jwk.JWK(kty='oct', k=base64.b64encode(key.encode('utf-8')).decode('ascii'))
        t = jwt.JWT(header=header, claims=payload)
        t.make_signed_token(k)
        return t.serialize()

    def user_info_response(self, request):
        if self.user_info_endpoint_response:
            return self.user_info_endpoint_response

        assert request.headers['Authorization'] == 'Bearer %s' % self.access_token
        self.authn.refresh_from_db()
        if self.version == '1':
            user_info = {}
            user_info['sub'] = self.sub
            user_info.update(self.user_info)
            return (200, {'Content-Type': 'application/json'}, json.dumps(user_info))
        else:
            return (200, {'Content-Type': 'application/jwt'}, self.user_info_jwt)

    def jwkset_response(self, request):
        return (200, {'Content-Type': 'application/json'}, json.dumps(self.jwkset.export(as_dict=True)))

    @property
    def user_info_jwt(self):
        user_info = {}
        user_info['sub'] = self.sub
        user_info['exp'] = int((self.exp or (now() + datetime.timedelta(seconds=60))).timestamp())
        user_info['iss'] = self.iss
        user_info.update(self.user_info)
        key = self.jwkset.get_key(KID_RSA)
        header = {'typ': 'JWT', 'alg': 'RS256', 'kid': KID_RSA}
        token = jwt.JWT(header=header, claims=user_info)
        token.make_signed_token(key)
        return token.serialize()

    @contextlib.contextmanager
    def __call__(self):
        with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
            for method in ('GET', 'POST'):
                rsps.add_callback(
                    method, url=re.compile(r'.*/token(\?.*)?$'), callback=self.access_token_response
                )
                rsps.add_callback(
                    method, url=re.compile(r'.*userinfo(\?.*)?$'), callback=self.user_info_response
                )
            yield None

    def handle_logout(self, app, url):
        self.authn.refresh_from_db()
        if self.authn.platform == 'test':
            if self.authn.version == '1':
                assert url.startswith('https://fcp.integ01.dev-franceconnect.fr/api/v1/logout')
            else:
                assert url.startswith('https://fcp-low.integ01.dev-franceconnect.fr/api/v2/session/end')
        else:
            if self.authn.version == '1':
                assert url.startswith('https://app.franceconnect.gouv.fr/api/v1/logout')
            else:
                assert url.startswith('https://oidc.franceconnect.gouv.fr/api/v2/session/end')
        parsed_url = urllib.parse.urlparse(url)
        query = QueryDict(parsed_url.query)
        assert_equals_url(query['post_logout_redirect_uri'], 'https://testserver' + reverse('fc-logout'))
        assert query['state']
        self.state = query['state']
        return app.get(reverse('fc-logout') + '?state=' + self.state)


@pytest.fixture
def service(db):
    return Service.objects.create(name='portail', slug='portail', ou=get_default_ou())


@pytest.fixture(params=['1', '2'])
def fc_version(request):
    return request.param


@pytest.fixture
def authenticator(db, fc_version, v2_jwkset):
    return FcAuthenticator.objects.create(
        enabled=True,
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        platform='test',
        version=fc_version,
        scopes=['profile', 'email'],
        jwkset_json=v2_jwkset.export(as_dict=True) if fc_version == '2' else None,
    )


@pytest.fixture(scope='function', params=[True, False])
def franceconnect(request, settings, authenticator, service, db, fc_version, v2_jwkset):
    mock_object = FranceConnectMock()
    if fc_version == '2':
        mock_object.jwkset = v2_jwkset
    authenticator.supports_multiaccount = request.param
    authenticator.save(update_fields=('supports_multiaccount',))
    with mock_object():
        yield mock_object


@pytest.fixture
def fc_multiaccount_only(settings, authenticator, service, db, fc_version, v2_jwkset):
    mock_object = FranceConnectMock()
    if fc_version == '2':
        mock_object.jwkset = v2_jwkset
    authenticator.supports_multiaccount = True
    authenticator.save(update_fields=('supports_multiaccount',))
    with mock_object():
        yield mock_object
