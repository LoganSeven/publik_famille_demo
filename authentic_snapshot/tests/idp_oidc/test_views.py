# authentic2 - versatile identity manager
# Copyright (C) 2010-2021 Entr'ouvert
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

from django.utils.timezone import now

from authentic2_idp_oidc.models import OIDCAccessToken

from .. import utils
from .conftest import bearer_authentication_headers


def test_user_info(app, client, freezer, simple_user):
    access_token = OIDCAccessToken.objects.create(
        client=client,
        user=simple_user,
        scopes='openid profile email',
        expired=now() + datetime.timedelta(seconds=3600),
    )

    def get_user_info(**kwargs):
        return app.get(
            '/idp/oidc/user_info/', headers=bearer_authentication_headers(access_token.uuid), **kwargs
        )

    response = app.get('/idp/oidc/user_info/', status=401)
    assert (
        response['WWW-Authenticate']
        == 'Bearer error="invalid_request", error_description="Bearer authentication is mandatory"'
    )

    response = app.get('/idp/oidc/user_info/', headers={'Authorization': 'Bearer'}, status=401)
    assert (
        response['WWW-Authenticate']
        == 'Bearer error="invalid_request", error_description="Invalid Bearer authentication"'
    )

    response = get_user_info(status=200)
    assert dict(response.json, sub='') == {
        'email': 'user@example.net',
        'email_verified': False,
        'family_name': 'Dôe',
        'family_name_verified': True,
        'given_name': 'Jôhn',
        'given_name_verified': True,
        'sub': '',
        'iss': 'https://testserver/',
    }

    # token is expired
    access_token.expired = now() - datetime.timedelta(seconds=1)
    access_token.save()
    response = get_user_info(status=401)
    assert (
        response['WWW-Authenticate']
        == 'Bearer error="invalid_token", error_description="Token expired or user disconnected"'
    )

    # token is unknown
    access_token.delete()
    response = get_user_info(status=401)
    assert response['WWW-Authenticate'] == 'Bearer error="invalid_token", error_description="Token unknown"'

    utils.login(app, access_token.user)
    access_token.expired = now() + datetime.timedelta(seconds=1)
    access_token.session_key = app.session.session_key
    access_token.save()

    get_user_info(status=200)

    app.session.flush()
    response = get_user_info(status=401)
    assert (
        response['WWW-Authenticate']
        == 'Bearer error="invalid_token", error_description="Token expired or user disconnected"'
    )


def test_openid_configuration(app):
    response = app.get('/.well-known/openid-configuration')
    assert response.json == {
        'authorization_endpoint': 'https://testserver/idp/oidc/authorize',
        'end_session_endpoint': 'https://testserver/idp/oidc/logout',
        'frontchannel_logout_session_supported': True,
        'frontchannel_logout_supported': True,
        'id_token_signing_alg_values_supported': ['RS256', 'HS256', 'ES256'],
        'issuer': 'https://testserver/',
        'jwks_uri': 'https://testserver/idp/oidc/certs',
        'response_types_supported': ['code', 'token', 'token id_token'],
        'subject_types_supported': ['public', 'pairwise'],
        'token_endpoint': 'https://testserver/idp/oidc/token',
        'token_endpoint_auth_methods_supported': ['client_secret_post', 'client_secret_basic'],
        'token_revocation_endpoint': 'https://testserver/idp/oidc/revoke',
        'userinfo_endpoint': 'https://testserver/idp/oidc/user_info',
        'code_challenge_methods_supported': ['plain', 'S256'],
    }

    assert response.headers['Access-Control-Allow-Origin'] == '*'


def test_certs(app, oidc_settings):
    response = app.get('/idp/oidc/certs/')
    assert response.json['keys']
    assert response.headers['Access-Control-Allow-Origin'] == '*'
