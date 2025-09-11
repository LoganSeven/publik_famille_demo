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

import base64
import datetime
import functools
import json
import urllib.parse

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files import File
from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import QueryDict
from django.test.utils import override_settings
from django.urls import reverse
from django.utils.dateparse import parse_datetime
from django.utils.encoding import force_str
from django.utils.timezone import now
from jwcrypto.jwk import JWK, JWKSet
from jwcrypto.jwt import JWT

from authentic2.a2_rbac.models import OrganizationalUnit, Role
from authentic2.a2_rbac.utils import get_default_ou
from authentic2.custom_user.models import Profile, ProfileType
from authentic2.models import Attribute, AuthorizedRole, Setting
from authentic2.utils.jwc import parse_timestamp
from authentic2.utils.misc import good_next_url, make_url
from authentic2_idp_oidc.models import (
    OIDCAccessToken,
    OIDCAuthorization,
    OIDCClaim,
    OIDCClient,
    OIDCCode,
    OIDCRefreshToken,
)
from authentic2_idp_oidc.utils import (
    base64url,
    get_first_ec_sig_key,
    get_first_rsa_sig_key,
    get_session_id,
    make_sub,
    pkce_s256,
)

from .. import utils
from .conftest import bearer_authentication_headers, client_authentication_headers

User = get_user_model()

pytestmark = pytest.mark.django_db


def test_get_jwkset(oidc_settings):
    from authentic2_idp_oidc.utils import get_jwkset

    get_jwkset()


OIDC_CLIENT_PARAMS = [
    {
        'authorization_flow': OIDCClient.FLOW_IMPLICIT,
    },
    {
        'post_logout_redirect_uris': 'https://example.com/',
    },
    {
        'identifier_policy': OIDCClient.POLICY_UUID,
        'post_logout_redirect_uris': 'https://example.com/',
    },
    {
        'identifier_policy': OIDCClient.POLICY_EMAIL,
    },
    {
        'idtoken_algo': OIDCClient.ALGO_HMAC,
    },
    {
        'idtoken_algo': OIDCClient.ALGO_EC,
    },
    {
        'authorization_mode': OIDCClient.AUTHORIZATION_MODE_NONE,
    },
    {
        'idtoken_duration': datetime.timedelta(hours=1),
    },
    {
        'authorization_flow': OIDCClient.FLOW_IMPLICIT,
        'idtoken_duration': datetime.timedelta(hours=1),
        'post_logout_redirect_uris': 'https://example.com/',
        'home_url': 'https://example.com/',
    },
    {
        'frontchannel_logout_uri': 'https://example.com/southpark/logout/',
    },
    {
        'frontchannel_logout_uri': 'https://example.com/southpark/logout/',
        'frontchannel_timeout': 3000,
        'colour': '#ff00ff',
    },
    {
        'identifier_policy': OIDCClient.POLICY_PAIRWISE_REVERSIBLE,
    },
    {
        'always_save_authorization': True,
        'authorization_default_duration': 105,
    },
    # test that nothings depends upon the sector_identifier_uri when UUID policy is used.
    {
        'identifier_policy': OIDCClient.POLICY_UUID,
        'redirect_uris': 'https://example.com/callbac%C3%A9\nhttps://other.com/callback/',
    },
    {
        'uses_refresh_tokens': True,
        'scope': 'openid email profile offline_access',
        'access_token_duration': datetime.timedelta(seconds=3600 * 7),
    },
]


def test_login_from_client_with_home_url(oidc_client, app, simple_user, settings):
    redirect_uri = oidc_client.redirect_uris.split()[0]
    params = {
        'client_id': oidc_client.client_id,
        'scope': 'openid profile email',
        'redirect_uri': redirect_uri,
        'state': 'xxx',
        'nonce': 'yyy',
        'login_hint': 'backoffice john@example.com',
        'response_type': 'code',
    }
    authorize_url = make_url('oidc-authorize', params=params)
    response = app.get(authorize_url).follow()
    assert not response.pyquery.find('.service-message--link')
    assert response.pyquery.find('.service-message--text')

    # check default settings fallback are unused
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

    old_service_name = oidc_client.name
    old_ou_name = oidc_client.ou.name
    oidc_client.name = ''
    oidc_client.ou.name = ''
    oidc_client.save()
    oidc_client.ou.save()

    response = app.get(authorize_url).follow()
    assert response.pyquery.find('.service-message')
    assert not response.pyquery.find('a.service-message--link')
    assert 'color: #8c22ec' not in response.text
    assert not response.pyquery('img.service-message--logo')

    oidc_client.name = old_service_name
    oidc_client.ou.name = old_ou_name
    oidc_client.save()
    oidc_client.ou.save()

    colour.value = ''
    colour.save()
    home_url.value = ''
    home_url.save()
    logo_url.value = ''
    logo_url.save()
    service_name.value = ''
    service_name.save()

    ou = oidc_client.ou
    ou.home_url = 'https://ou.example.net'
    ou.colour = '#8c00ec'
    with open('tests/200x200.jpg', 'rb') as fd:
        ou.logo = SimpleUploadedFile(name='200x200.jpg', content=fd.read())
        ou.save()
    response = app.get(authorize_url).follow()
    assert response.pyquery.find('.service-message')
    assert not response.pyquery.find('.service-message--link')
    assert 'color: #8c00ec' in response.text
    assert (
        response.pyquery.find('img.service-message--logo')[0].attrib['src']
        == '/media/services/logos/200x200.jpg'
    )

    oidc_client.home_url = 'https://service.example.net'
    oidc_client.colour = '#ec008c'
    with open('tests/201x201.jpg', 'rb') as fd:
        oidc_client.logo = SimpleUploadedFile(name='201x201.jpg', content=fd.read())
    oidc_client.save()

    response = app.get(authorize_url).follow()
    link = response.pyquery.find('a.service-message--link')[0]
    assert link.attrib['href'] == 'https://service.example.net'
    assert 'color: #ec008c' in response.text
    assert (
        response.pyquery.find('img.service-message--logo')[0].attrib['src']
        == '/media/services/logos/201x201.jpg'
    )

    # check registration page
    response = response.click('Register!')
    assert link.attrib['href'] == 'https://service.example.net'
    assert (
        response.pyquery.find('img.service-message--logo')[0].attrib['src']
        == '/media/services/logos/201x201.jpg'
    )

    # check authorization page
    response = utils.login(app, simple_user)
    response = app.get(authorize_url)
    assert response.pyquery.find('.service-message')
    assert response.pyquery.find('a.service-message--link')
    assert (
        response.pyquery.find('img.service-message--logo')[0].attrib['src']
        == '/media/services/logos/201x201.jpg'
    )
    link = response.pyquery.find('a.service-message--link')[0]
    assert link.attrib['href'] == 'https://service.example.net'


@pytest.mark.parametrize('oidc_client', OIDC_CLIENT_PARAMS, indirect=True)
@pytest.mark.parametrize('do_not_ask_again', [(True,), (False,)])
@pytest.mark.parametrize('login_first', [(True,), (False,)])
@pytest.mark.parametrize('offline_access', [(True,), (False,)])
def test_authorization_code_sso(
    offline_access,
    login_first,
    do_not_ask_again,
    oidc_client,
    oidc_settings,
    simple_user,
    app,
    rp_app,
    caplog,
    rf,
):
    redirect_uri = oidc_client.redirect_uris.split()[0]
    params = {
        'client_id': oidc_client.client_id,
        'scope': 'openid profile email',
        'redirect_uri': redirect_uri,
        'state': 'xxx',
        'nonce': 'yyy',
        'login_hint': 'backoffice john@example.com',
    }
    if offline_access and oidc_client.uses_refresh_tokens:
        params['scope'] += ' offline_access'

    if oidc_client.authorization_flow == oidc_client.FLOW_AUTHORIZATION_CODE:
        params['response_type'] = 'code'
    elif oidc_client.authorization_flow == oidc_client.FLOW_IMPLICIT:
        params['response_type'] = 'token id_token'
    authorize_url = make_url('oidc-authorize', params=params)

    if login_first:
        utils.login(app, simple_user)
    response = app.get(authorize_url)
    if not login_first:
        assert set(app.session['login-hint']) == {'backoffice', 'john@example.com'}
        response = response.follow()
        assert response.request.path == reverse('auth_login')
        response.form.set('username', simple_user.username)
        response.form.set('password', simple_user.clear_password)
        response = response.form.submit(name='login-password-submit')
        response = response.follow()
        assert response.request.path == reverse('oidc-authorize')
    if oidc_client.authorization_mode != OIDCClient.AUTHORIZATION_MODE_NONE:
        response = response.maybe_follow()
        assert 'a2-oidc-authorization-form' in response.text
        assert OIDCAuthorization.objects.count() == 0
        assert OIDCCode.objects.count() == 0
        assert OIDCAccessToken.objects.count() == 0
        if oidc_client.always_save_authorization or offline_access and oidc_client.uses_refresh_tokens:
            assert 'do_not_ask_again' not in response.text
        else:
            response.form['do_not_ask_again'] = do_not_ask_again
        if offline_access and oidc_client.uses_refresh_tokens:
            assert 'access to this information at any time while you are offline.' in response.text
        response = response.form.submit('accept')
        if do_not_ask_again or oidc_client.always_save_authorization:
            assert OIDCAuthorization.objects.count() == 1
            authz = OIDCAuthorization.objects.get()
            assert authz.client == oidc_client
            assert authz.user == simple_user
            if offline_access and oidc_client.uses_refresh_tokens:
                assert authz.scope_set() == set('openid profile email offline_access'.split())
            else:
                assert authz.scope_set() == set('openid profile email'.split())
            if oidc_client.authorization_default_duration == 0:
                assert 360 < (authz.expired - now()).days < 370  # one year
            else:
                # see authorization_default_duration in OIDC_CLIENT_PARAMS (105 days)
                assert 100 < (authz.expired - now()).days < 110
        else:
            assert OIDCAuthorization.objects.count() == 0

        if offline_access and oidc_client.uses_refresh_tokens:
            event_scopes = ['email', 'offline_access', 'openid', 'profile']
        else:
            event_scopes = ['email', 'openid', 'profile']
        utils.assert_event(
            'user.service.sso.authorization',
            session=app.session,
            user=simple_user,
            service=oidc_client,
            scopes=event_scopes,
        )
    utils.assert_event(
        'user.service.sso',
        session=app.session,
        user=simple_user,
        service=oidc_client,
        how='password-on-https',
    )
    if oidc_client.authorization_flow == oidc_client.FLOW_AUTHORIZATION_CODE:
        assert OIDCCode.objects.count() == 1
        code = OIDCCode.objects.get()
        assert code.client == oidc_client
        assert code.user == simple_user
        if offline_access and oidc_client.uses_refresh_tokens:
            assert authz.scope_set() == set('openid profile email offline_access'.split())
        else:
            assert code.scope_set() == set('openid profile email'.split())
        assert code.state == 'xxx'
        assert code.nonce == 'yyy'
        assert code.redirect_uri == redirect_uri
        assert code.session_key == app.session.session_key
        assert code.auth_time <= now()
        assert code.expired >= now()
    assert response['Location'].startswith(redirect_uri)
    location = urllib.parse.urlparse(response['Location'])
    if oidc_client.authorization_flow == oidc_client.FLOW_AUTHORIZATION_CODE:
        query = urllib.parse.parse_qs(location.query)
        assert set(query.keys()) == {'code', 'state', 'iss'}
        assert query['code'] == [code.uuid]
        code = query['code'][0]
        assert query['state'] == ['xxx']

        token_url = make_url('oidc-token')
        response = rp_app.post(
            token_url,
            params={
                'grant_type': 'authorization_code',
                'code': code,
                'redirect_uri': oidc_client.redirect_uris.split()[0],
            },
            headers=client_authentication_headers(oidc_client),
        )
        if oidc_client.uses_refresh_tokens:
            assert 'refresh_token' in response.json
            refresh_token = OIDCRefreshToken.objects.get(uuid=response.json['refresh_token'])
            assert refresh_token.client == oidc_client
            assert refresh_token.user == simple_user
            assert set(refresh_token.scopes.split()) == {'email', 'openid', 'profile', 'offline_access'}
        assert 'error' not in response.json
        assert 'access_token' in response.json
        assert 'expires_in' in response.json
        assert 'id_token' in response.json
        assert response.json['token_type'] == 'Bearer'
        access_token = response.json['access_token']
        id_token = response.json['id_token']
    elif oidc_client.authorization_flow == oidc_client.FLOW_IMPLICIT:
        assert location.fragment
        query = urllib.parse.parse_qs(location.fragment)
        assert OIDCAccessToken.objects.count() == 1
        access_token = OIDCAccessToken.objects.get()
        assert access_token.authorization is not None
        assert set(query.keys()) == {'access_token', 'token_type', 'expires_in', 'id_token', 'state'}
        assert query['access_token'] == [access_token.uuid]
        assert query['token_type'] == ['Bearer']
        assert query['state'] == ['xxx']
        access_token = query['access_token'][0]
        id_token = query['id_token'][0]

    if oidc_client.idtoken_algo in (oidc_client.ALGO_RSA, oidc_client.ALGO_EC):
        key = JWKSet.from_json(app.get(reverse('oidc-certs')).content)
        algs = ['RS256', 'ES256']
    elif oidc_client.idtoken_algo == oidc_client.ALGO_HMAC:
        k = base64.b64encode(oidc_client.client_secret.encode('utf-8'))
        key = JWK(kty='oct', k=force_str(k))
        algs = ['HS256']
    else:
        raise NotImplementedError
    jwt = JWT(jwt=id_token, key=key, algs=algs)
    claims = json.loads(jwt.claims)
    assert set(claims) >= {'iss', 'sub', 'aud', 'exp', 'iat', 'nonce', 'auth_time', 'acr', 'sid'}
    assert claims['nonce'] == 'yyy'
    assert response.request.url.startswith(claims['iss'])
    assert claims['aud'] == oidc_client.client_id
    assert parse_timestamp(claims['iat']) <= now()
    assert parse_timestamp(claims['auth_time']) <= now()
    assert claims['sid']
    sid = claims['sid']
    exp_delta = (parse_timestamp(claims['exp']) - now()).total_seconds()
    assert exp_delta > 0
    if oidc_client.idtoken_duration:
        assert abs(exp_delta - oidc_client.idtoken_duration.total_seconds()) < 2
    else:
        assert abs(exp_delta - 30) < 2

    if login_first:
        assert claims['acr'] == '0'
    else:
        assert claims['acr'] == '1'
    assert claims['sub'] == make_sub(oidc_client, simple_user)
    assert claims['given_name'] == simple_user.first_name
    assert claims['family_name'] == simple_user.last_name
    assert claims['email'] == simple_user.email
    assert claims['email_verified'] is False

    user_info_url = make_url('oidc-user-info')
    response = app.get(user_info_url, headers=bearer_authentication_headers(access_token))
    assert response.json['sub'] == make_sub(oidc_client, simple_user)
    assert response.json['given_name'] == simple_user.first_name
    assert response.json['family_name'] == simple_user.last_name
    assert response.json['email'] == simple_user.email
    assert response.json['email_verified'] is False

    # when adding extra attributes
    OIDCClaim.objects.create(client=oidc_client, name='ou', value='django_user_ou_name', scopes='profile')
    OIDCClaim.objects.create(client=oidc_client, name='roles', value='a2_role_names', scopes='profile, role')
    OIDCClaim.objects.create(
        client=oidc_client, name='cityscape_image', value='django_user_cityscape_image', scopes='profile'
    )
    OIDCClaim.objects.create(
        client=oidc_client, name='date_joined', value='django_user_date_joined', scopes='profile'
    )
    simple_user.roles.add(Role.objects.create(name='Whatever', slug='whatever', ou=get_default_ou()))
    response = app.get(user_info_url, headers=bearer_authentication_headers(access_token))
    assert response.json['ou'] == simple_user.ou.name
    assert response.json['roles'][0] == 'Whatever'
    assert parse_datetime(response.json['date_joined'])
    assert response.json.get('cityscape_image') is None
    with open('tests/200x200.jpg', 'rb') as fd:
        simple_user.attributes.cityscape_image = File(fd)
    response = app.get(user_info_url, headers=bearer_authentication_headers(access_token))
    assert response.json['cityscape_image'].startswith('https://testserver/media/profile-image/')

    # Now logout
    if oidc_client.post_logout_redirect_uris:
        params = {
            'post_logout_redirect_uri': oidc_client.post_logout_redirect_uris,
            'state': 'xyz',
        }
        logout_url = make_url('oidc-logout', params=params)
        response = app.get(logout_url)
        assert response.status_code == 302
        assert response.location == 'https://example.com/?state=xyz'
        assert '_auth_user_id' not in app.session
    else:
        response = app.get(make_url('account_management'))
        response = response.click('Logout')
        if oidc_client.frontchannel_logout_uri:
            iframes = response.pyquery('iframe[src^="https://example.com/southpark/logout/"]')
            assert iframes
            src = iframes.attr('src')
            assert '?' in src
            src_qd = QueryDict(src.split('?', 1)[1])
            assert 'iss' in src_qd and src_qd['iss'] == 'https://testserver/'
            assert 'sid' in src_qd and src_qd['sid'] == get_session_id(app.session, oidc_client) == sid
            if oidc_client.frontchannel_timeout:
                assert iframes.attr('onload').endswith(', %d)' % oidc_client.frontchannel_timeout)
            else:
                assert iframes.attr('onload').endswith(', 300)')

    # attempt a refresh request
    status = 200 if oidc_client.uses_refresh_tokens else 400
    refresh_token = OIDCRefreshToken.objects.filter(client=oidc_client).last()
    token_url = make_url('oidc-token')
    response = rp_app.post(
        token_url,
        params={
            'grant_type': 'refresh_token',
            'refresh_token': getattr(refresh_token, 'uuid', 'baz'),
        },
        headers=client_authentication_headers(oidc_client),
        status=status,
    )
    if oidc_client.uses_refresh_tokens:
        assert 'access_token' in response.json
        access_token = response.json['access_token']
        assert 'refresh_token' in response.json
        assert (
            OIDCRefreshToken.objects.get(uuid=response.json['refresh_token']).refresh_token == refresh_token
        )
        refresh_token.refresh_from_db()
        assert refresh_token.expired < now() + datetime.timedelta(seconds=601)

        user_info_url = make_url('oidc-user-info')
        status = 200 if offline_access else 400
        response = app.get(user_info_url, headers=bearer_authentication_headers(access_token), status=status)
        if offline_access:
            assert response.json['sub'] == make_sub(oidc_client, simple_user)
            assert response.json['given_name'] == simple_user.first_name
            assert response.json['family_name'] == simple_user.last_name
            assert response.json['email'] == simple_user.email
            assert response.json['email_verified'] is False
            assert response.json['ou'] == simple_user.ou.name
            assert response.json['roles'][0] == 'Whatever'
            assert parse_datetime(response.json['date_joined'])
            assert response.json.get('cityscape_image')


def test_authorization_code_sso_access_token_expired_offline_access(
    oidc_client,
    oidc_settings,
    simple_user,
    app,
    rp_app,
    caplog,
    rf,
    freezer,
):
    oidc_client.authorization_flow = OIDCClient.FLOW_AUTHORIZATION_CODE
    oidc_client.authorization_mode = OIDCClient.AUTHORIZATION_MODE_BY_SERVICE
    oidc_client.scope = 'openid email profile offline_access'
    oidc_client.uses_refresh_tokens = True
    oidc_client.save()

    redirect_uri = oidc_client.redirect_uris.split()[0]
    params = {
        'client_id': oidc_client.client_id,
        'scope': 'openid profile email offline_access',
        'redirect_uri': redirect_uri,
        'state': 'xxx',
        'nonce': 'yyy',
        'login_hint': 'backoffice john@example.com',
        'response_type': 'code',
    }

    authorize_url = make_url('oidc-authorize', params=params)

    response = app.get(authorize_url).follow()
    response.form.set('username', simple_user.username)
    response.form.set('password', simple_user.clear_password)
    response = response.form.submit(name='login-password-submit')
    response = response.follow()
    response = response.maybe_follow()
    response = response.form.submit('accept')
    location = urllib.parse.urlparse(response['Location'])
    query = urllib.parse.parse_qs(location.query)
    code = query['code'][0]

    token_url = make_url('oidc-token')
    response = rp_app.post(
        token_url,
        params={
            'grant_type': 'authorization_code',
            'code': code,
            'redirect_uri': oidc_client.redirect_uris.split()[0],
        },
        headers=client_authentication_headers(oidc_client),
    )
    assert 'error' not in response.json
    assert 'access_token' in response.json
    assert 'expires_in' in response.json
    assert 'id_token' in response.json
    assert response.json['token_type'] == 'Bearer'
    uuid = response.json['access_token']
    id_token = response.json['id_token']
    assert 'refresh_token' in response.json
    refresh_token = OIDCRefreshToken.objects.get(uuid=response.json['refresh_token'])
    assert refresh_token.client == oidc_client
    assert refresh_token.user == simple_user
    assert set(refresh_token.scopes.split()) == {'email', 'openid', 'profile', 'offline_access'}
    # token was issued outside of a refresh request
    access_token = OIDCAccessToken.objects.get(uuid=uuid)
    assert not access_token.refresh_token
    assert access_token.authorization is not None
    assert access_token.authorization == refresh_token.authorization

    k = base64.b64encode(oidc_client.client_secret.encode('utf-8'))
    key = JWK(kty='oct', k=force_str(k))
    algs = ['HS256']
    JWT(jwt=id_token, key=key, algs=algs)
    user_info_url = make_url('oidc-user-info')
    response = app.get(user_info_url, headers=bearer_authentication_headers(uuid))
    assert response.json['sub'] == make_sub(oidc_client, simple_user)
    assert response.json['given_name'] == simple_user.first_name
    assert response.json['family_name'] == simple_user.last_name
    assert response.json['email'] == simple_user.email
    assert response.json['email_verified'] is False

    freezer.tick(3600 * 24 * 7 * 2)  # wait past current session expiry
    response = app.get(user_info_url, headers=bearer_authentication_headers(access_token), status=401)

    # attempt a refresh request
    token_url = make_url('oidc-token')
    params = {'grant_type': 'refresh_token'}
    response = rp_app.post(
        token_url,
        params=params,
        headers=client_authentication_headers(oidc_client),
        status=400,
    )
    params.update({'refresh_token': refresh_token.uuid})
    response = rp_app.post(
        token_url,
        params=params,
        headers=client_authentication_headers(oidc_client),
        status=200,
    )
    assert 'access_token' in response.json
    uuid = response.json['access_token']
    access_token = OIDCAccessToken.objects.get(uuid=uuid)
    assert access_token.refresh_token == refresh_token
    assert access_token.is_valid()
    assert access_token.authorization is not None
    assert access_token.authorization == refresh_token.authorization

    user_info_url = make_url('oidc-user-info')
    response = app.get(user_info_url, headers=bearer_authentication_headers(uuid), status=200)
    assert response.json['sub'] == make_sub(oidc_client, simple_user)
    assert response.json['given_name'] == simple_user.first_name
    assert response.json['family_name'] == simple_user.last_name
    assert response.json['email'] == simple_user.email
    assert response.json['email_verified'] is False

    token_url = make_url('oidc-token')
    response = rp_app.post(
        token_url,
        params={
            'grant_type': 'refresh_token',
            'refresh_token': refresh_token.uuid,
        },
        headers=client_authentication_headers(oidc_client),
    )

    freezer.tick(10)
    # previously issued token has since been invalidated
    assert not OIDCAccessToken.objects.get(uuid=uuid).is_valid()

    freezer.tick(2592001)  # past refresh token expiry
    response = rp_app.post(
        token_url,
        params={
            'grant_type': 'refresh_token',
            'refresh_token': refresh_token.uuid,
        },
        headers=client_authentication_headers(oidc_client),
        status=400,
    )

    response = rp_app.post(
        token_url,
        params={
            'grant_type': 'refresh_token',
            'refresh_token': 'auietsrn',  # inexistant token
        },
        headers=client_authentication_headers(oidc_client),
        status=400,
    )

    response = rp_app.post(
        token_url,
        params={
            'grant_type': 'refresh',  # inexistant grant type
            'refresh_token': refresh_token.uuid,
        },
        headers=client_authentication_headers(oidc_client),
        status=400,
    )


def test_token_revocation(
    oidc_client,
    oidc_settings,
    simple_user,
    app,
    rp_app,
    caplog,
    rf,
    freezer,
):
    oidc_client.authorization_flow = OIDCClient.FLOW_AUTHORIZATION_CODE
    oidc_client.authorization_mode = OIDCClient.AUTHORIZATION_MODE_BY_SERVICE
    oidc_client.scope = 'openid email profile offline_access'
    oidc_client.uses_refresh_tokens = True
    oidc_client.save()

    redirect_uri = oidc_client.redirect_uris.split()[0]
    params = {
        'client_id': oidc_client.client_id,
        'scope': 'openid profile email offline_access',
        'redirect_uri': redirect_uri,
        'state': 'xxx',
        'nonce': 'yyy',
        'login_hint': 'backoffice john@example.com',
        'response_type': 'code',
    }

    authorize_url = make_url('oidc-authorize', params=params)

    response = app.get(authorize_url).follow()
    response.form.set('username', simple_user.username)
    response.form.set('password', simple_user.clear_password)
    response = response.form.submit(name='login-password-submit')
    response = response.follow()
    response = response.maybe_follow()
    response = response.form.submit('accept')
    location = urllib.parse.urlparse(response['Location'])
    query = urllib.parse.parse_qs(location.query)
    code = query['code'][0]

    token_url = make_url('oidc-token')
    response = rp_app.post(
        token_url,
        params={
            'grant_type': 'authorization_code',
            'code': code,
            'redirect_uri': oidc_client.redirect_uris.split()[0],
        },
        headers=client_authentication_headers(oidc_client),
    )
    access_token = OIDCAccessToken.objects.get(uuid=response.json['access_token'])
    refresh_token = OIDCRefreshToken.objects.get(uuid=response.json['refresh_token'])

    assert access_token.expired > now()
    assert refresh_token.expired > now()

    token_revocation_url = make_url('oidc-token-revocation')
    response = rp_app.post(
        token_revocation_url,
        params={
            'token_type': 'access_token',
            'token': str(access_token.uuid),
        },
        headers=client_authentication_headers(oidc_client),
        status=200,
    )
    assert response.json['err'] == 0
    assert response.json['msg'] == f'access_token {access_token.uuid} successfully revoked'

    access_token.refresh_from_db()
    refresh_token.refresh_from_db()
    assert access_token.expired <= now()
    assert refresh_token.expired > now()

    response = rp_app.post(
        token_revocation_url,
        params={
            'token_type': 'refresh_token',
            'token': str(refresh_token.uuid),
        },
        headers=client_authentication_headers(oidc_client),
        status=200,
    )
    assert response.json['err'] == 0
    assert response.json['msg'] == f'refresh_token {refresh_token.uuid} successfully revoked'

    access_token.refresh_from_db()
    refresh_token.refresh_from_db()
    assert access_token.expired <= now()
    assert refresh_token.expired <= now()

    # invalid method type
    rp_app.put(
        token_revocation_url,
        params={
            'token_type': 'access_token',
            'token': str(access_token.uuid),
        },
        headers=client_authentication_headers(oidc_client),
        status=405,
    )

    # invalid token type
    rp_app.post(
        token_revocation_url,
        params={
            'token_type': 'id_token',
            'token': str(access_token.uuid),
        },
        headers=client_authentication_headers(oidc_client),
        status=400,
    )

    # invalid client
    headers = client_authentication_headers(oidc_client)
    headers['Authorization'] = 'oops'
    rp_app.post(
        token_revocation_url,
        params={
            'token_type': 'access_token',
            'token': str(access_token.uuid),
        },
        headers=headers,
        status=400,
    )

    # missing token
    rp_app.post(
        token_revocation_url,
        params={
            'token_type': 'access_token',
        },
        headers=client_authentication_headers(oidc_client),
        status=400,
    )

    # missing token_type
    rp_app.post(
        token_revocation_url,
        params={
            'token': str(access_token.uuid),
        },
        headers=client_authentication_headers(oidc_client),
        status=400,
    )

    # unknown token
    rp_app.post(
        token_revocation_url,
        params={
            'token_type': 'access_token',
            'token': 'auietsrn',
        },
        headers=client_authentication_headers(oidc_client),
        status=400,
    )


def check_authorize_error(
    response, error, error_description, fragment, caplog, check_next=True, redirect_uri=None, message=True
):
    # check next_url qs
    if message:
        location = urllib.parse.urlparse(response.location)
        assert location.path == '/continue/'
        if check_next:
            location_qs = QueryDict(location.query or '')
            assert 'next' in location_qs
            assert location_qs['next'].startswith(redirect_uri)
            next_url = urllib.parse.urlparse(location_qs['next'])
            next_url_qs = QueryDict(next_url.fragment if fragment else next_url.query)
            assert next_url_qs['error'] == error
            assert next_url_qs['error_description'] == error_description
        # check continue page
        continue_response = response.follow()
        assert error_description in continue_response.pyquery('.error').text()
    elif check_next:
        assert response.location.startswith(redirect_uri)
        location = urllib.parse.urlparse(response.location)
        location_qs = QueryDict(location.fragment if fragment else location.query)
        assert location_qs['error'] == error
        assert location_qs['error_description'] == error_description
    # check logs
    last_record = caplog.records[-1]
    if message:
        assert last_record.levelname == 'WARNING'
    else:
        assert last_record.levelname == 'INFO'
    assert 'error "%s" in authorize endpoint' % error in last_record.message
    assert error_description in last_record.message
    if message:
        return continue_response


def assert_authorization_response(response, fragment=False, **kwargs):
    location = urllib.parse.urlparse(response.location)
    location_qs = QueryDict(location.fragment if fragment else location.query)
    assert set(location_qs) == set(kwargs)
    for key, value in kwargs.items():
        if value is None:
            assert key in location_qs
        elif isinstance(value, list):
            assert set(location_qs.getlist(key)) == set(value)
        else:
            assert value in location_qs[key]


@pytest.mark.parametrize('oidc_client', OIDC_CLIENT_PARAMS, indirect=True)
def test_invalid_request(
    oidc_client, caplog, oidc_settings, simple_user, app, rp_app, make_client, app_factory
):
    redirect_uri = oidc_client.redirect_uris.split()[0]
    if oidc_client.authorization_flow == oidc_client.FLOW_AUTHORIZATION_CODE:
        fragment = False
        response_type = 'code'
    elif oidc_client.authorization_flow == oidc_client.FLOW_IMPLICIT:
        fragment = True
        response_type = 'id_token token'
    else:
        raise NotImplementedError

    assert_authorize_error = functools.partial(
        check_authorize_error, caplog=caplog, fragment=fragment, redirect_uri=redirect_uri
    )

    # missing client_id
    response = app.get(make_url('oidc-authorize', params={}))
    assert_authorize_error(response, 'invalid_request', 'Missing parameter "client_id"', check_next=False)

    # missing redirect_uri
    response = app.get(
        make_url(
            'oidc-authorize',
            params={
                'client_id': oidc_client.client_id,
            },
        )
    )
    assert_authorize_error(response, 'invalid_request', 'Missing parameter "redirect_uri"', check_next=False)

    # invalid client_id
    authorize_url = app.get(
        make_url(
            'oidc-authorize',
            params={
                'client_id': 'xxx',
                'redirect_uri': redirect_uri,
            },
        )
    )
    assert_authorize_error(response, 'invalid_request', 'Unknown client identifier: "xxx"', check_next=False)

    # invalid redirect_uri
    response = app.get(
        make_url(
            'oidc-authorize',
            params={
                'client_id': oidc_client.client_id,
                'redirect_uri': 'xxx',
                'response_type': 'code',
                'scope': 'openid',
            },
        ),
        status=302,
    )
    continue_response = assert_authorize_error(
        response, 'invalid_request', 'Redirect URI "xxx" is unknown.', check_next=False
    )
    assert 'Known' not in continue_response.pyquery('.error').text()

    # invalid redirect_uri with DEBUG=True, list of redirect_uris is shown
    with override_settings(DEBUG=True):
        response = app.get(
            make_url(
                'oidc-authorize',
                params={
                    'client_id': oidc_client.client_id,
                    'redirect_uri': 'xxx',
                    'response_type': 'code',
                    'scope': 'openid',
                },
            ),
            status=302,
        )
        continue_response = assert_authorize_error(
            response, 'invalid_request', 'Redirect URI "xxx" is unknown.', check_next=False
        )
        assert (
            'Known redirect URIs are: https://example.com/callbac%C3%A9'
            in continue_response.pyquery('.error').text()
        )

    # missing response_type
    response = app.get(
        make_url(
            'oidc-authorize',
            params={
                'client_id': oidc_client.client_id,
                'redirect_uri': redirect_uri,
            },
        )
    )
    assert_authorize_error(response, 'invalid_request', 'Missing parameter "response_type"')

    # unsupported response_type
    response = app.get(
        make_url(
            'oidc-authorize',
            params={
                'client_id': oidc_client.client_id,
                'redirect_uri': redirect_uri,
                'response_type': 'xxx',
            },
        )
    )

    if oidc_client.authorization_flow == oidc_client.FLOW_AUTHORIZATION_CODE:
        assert_authorize_error(response, 'unsupported_response_type', 'Response type must be "code"')
    elif oidc_client.authorization_flow == oidc_client.FLOW_IMPLICIT:
        assert_authorize_error(
            response, 'unsupported_response_type', 'Response type must be "id_token token" or "id_token"'
        )

    # missing scope
    response = app.get(
        make_url(
            'oidc-authorize',
            params={
                'client_id': oidc_client.client_id,
                'redirect_uri': redirect_uri,
                'response_type': response_type,
            },
        )
    )
    assert_authorize_error(response, 'invalid_request', 'Missing parameter "scope"')

    # invalid max_age : not an integer
    response = app.get(
        make_url(
            'oidc-authorize',
            params={
                'client_id': oidc_client.client_id,
                'redirect_uri': redirect_uri,
                'response_type': response_type,
                'scope': 'openid',
                'max_age': 'xxx',
            },
        )
    )
    assert_authorize_error(response, 'invalid_request', 'Parameter "max_age" must be a positive integer')

    # invalid max_age : not positive
    response = app.get(
        make_url(
            'oidc-authorize',
            params={
                'client_id': oidc_client.client_id,
                'redirect_uri': redirect_uri,
                'response_type': response_type,
                'scope': 'openid',
                'max_age': '-1',
            },
        )
    )
    assert_authorize_error(response, 'invalid_request', 'Parameter "max_age" must be a positive integer')

    # openid scope is missing
    authorize_url = make_url(
        'oidc-authorize',
        params={
            'client_id': oidc_client.client_id,
            'redirect_uri': redirect_uri,
            'response_type': response_type,
            'scope': 'profile',
        },
    )

    response = app.get(authorize_url)
    assert_authorize_error(response, 'invalid_scope', 'Scope must contain "openid", received "profile"')

    # use of an unknown scope
    authorize_url = make_url(
        'oidc-authorize',
        params={
            'client_id': oidc_client.client_id,
            'redirect_uri': redirect_uri,
            'response_type': response_type,
            'scope': 'openid email profile zob',
        },
    )

    response = app.get(authorize_url)
    if 'offline_access' in oidc_client.scope_set():
        msg_scopes = 'email, offline_access, openid, profile'
    else:
        msg_scopes = 'email, openid, profile'
    assert_authorize_error(
        response,
        'invalid_scope',
        f'Scope may contain "{msg_scopes}" scope(s), received "email, openid, profile, zob"',
    )

    # restriction on scopes
    with override_settings(A2_IDP_OIDC_SCOPES=['openid']):
        response = app.get(
            make_url(
                'oidc-authorize',
                params={
                    'client_id': oidc_client.client_id,
                    'redirect_uri': redirect_uri,
                    'response_type': response_type,
                    'scope': 'openid email',
                },
            )
        )
        if 'offline_access' in oidc_client.scope_set():
            location = urllib.parse.urlparse(response.location)
            assert location.path == '/login/'
        else:
            assert_authorize_error(
                response, 'invalid_scope', 'Scope may contain "openid" scope(s), received "email, openid"'
            )

    # cancel
    response = app.get(
        make_url(
            'oidc-authorize',
            params={
                'client_id': oidc_client.client_id,
                'redirect_uri': redirect_uri,
                'response_type': response_type,
                'scope': 'openid email profile',
                'cancel': '1',
            },
        )
    )
    assert_authorize_error(response, 'access_denied', 'Authentication cancelled by user', message=False)

    # prompt=none
    response = app.get(
        make_url(
            'oidc-authorize',
            params={
                'client_id': oidc_client.client_id,
                'redirect_uri': redirect_uri,
                'response_type': response_type,
                'scope': 'openid email profile',
                'prompt': 'none',
            },
        )
    )
    assert_authorize_error(
        response,
        'login_required',
        error_description='Login is required but prompt parameter is "none"',
        message=False,
    )

    utils.login(app, simple_user)

    # prompt=none max_age=0
    response = app.get(
        make_url(
            'oidc-authorize',
            params={
                'client_id': oidc_client.client_id,
                'redirect_uri': redirect_uri,
                'response_type': response_type,
                'scope': 'openid email profile',
                'max_age': '0',
                'prompt': 'none',
            },
        )
    )
    assert_authorize_error(
        response,
        'login_required',
        error_description='Login is required because of max_age, but prompt parameter is "none"',
        message=False,
    )

    # max_age=0
    response = app.get(
        make_url(
            'oidc-authorize',
            params={
                'client_id': oidc_client.client_id,
                'redirect_uri': redirect_uri,
                'response_type': response_type,
                'scope': 'openid email profile',
                'max_age': '0',
            },
        )
    )
    assert response.location.startswith(reverse('auth_login') + '?')

    # prompt=login
    authorize_url = make_url(
        'oidc-authorize',
        params={
            'client_id': oidc_client.client_id,
            'redirect_uri': redirect_uri,
            'response_type': response_type,
            'scope': 'openid email profile',
            'prompt': 'login',
        },
    )
    response = app.get(authorize_url)
    assert urllib.parse.urlparse(response['Location']).path == reverse('auth_login')

    if oidc_client.authorization_mode != oidc_client.AUTHORIZATION_MODE_NONE:
        # prompt is none, but account selection is required, out-of-spec corner case without error
        oidc_client.activate_user_profiles = True
        oidc_client.save()
        profile_type_manager = ProfileType.objects.create(
            name='One Manager Type',
            slug='one-manager-type',
        )
        profile_type_delegate = ProfileType.objects.create(
            name='One Delegate Type',
            slug='one-delegate-type',
        )
        profile_manager = Profile.objects.create(
            user=simple_user,
            profile_type=profile_type_manager,
            identifier='Entity 789',
            email='manager@example789.org',
        )
        profile_delegate = Profile.objects.create(
            user=simple_user,
            profile_type=profile_type_delegate,
            identifier='Entity 1011',
            email='delegate@example1011.org',
        )
        # authorization exists
        authorize = OIDCAuthorization.objects.create(
            client=oidc_client,
            user=simple_user,
            scopes='openid profile email',
            expired=now() + datetime.timedelta(days=2),
        )
        response = app.get(
            make_url(
                'oidc-authorize',
                params={
                    'client_id': oidc_client.client_id,
                    'redirect_uri': redirect_uri,
                    'response_type': response_type,
                    'scope': 'openid',
                    'prompt': 'none',
                },
            )
        )

        response.form['profile-validation'] = str(profile_manager.id)
        response = response.form.submit('accept')

        if oidc_client.authorization_flow == OIDCClient.FLOW_IMPLICIT:
            assert 'access_token' in response.location
            assert 'id_token' in response.location
            assert 'expires_in' in response.location
            assert 'token_type' in response.location
        elif oidc_client.authorization_flow == oidc_client.FLOW_AUTHORIZATION_CODE:
            assert 'code' in response.location

        profile_manager.delete()
        profile_delegate.delete()
        authorize.delete()

        # prompt is none, consent is required, out-of-spec corner case without error
        response = app.get(
            make_url(
                'oidc-authorize',
                params={
                    'client_id': oidc_client.client_id,
                    'redirect_uri': redirect_uri,
                    'response_type': response_type,
                    'scope': 'openid',
                    'prompt': 'none',
                },
            )
        )
        response = response.form.submit('accept')

        if oidc_client.authorization_flow == OIDCClient.FLOW_IMPLICIT:
            assert 'access_token' in response.location
            assert 'id_token' in response.location
            assert 'expires_in' in response.location
            assert 'token_type' in response.location
        elif oidc_client.authorization_flow == oidc_client.FLOW_AUTHORIZATION_CODE:
            assert 'code' in response.location

        # user do not consent
        response = app.get(
            make_url(
                'oidc-authorize',
                params={
                    'client_id': oidc_client.client_id,
                    'redirect_uri': redirect_uri,
                    'response_type': response_type,
                    'scope': 'openid email profile',
                },
            )
        )
        response = response.form.submit('refuse')
        utils.assert_event(
            'user.service.sso.refusal',
            session=app.session,
            user=simple_user,
            service=oidc_client,
            scopes=['email', 'openid', 'profile'],
        )
        assert_authorize_error(response, 'access_denied', 'User did not consent', message=False)

    # authorization exists
    authorize = OIDCAuthorization.objects.create(
        client=oidc_client,
        user=simple_user,
        scopes='openid profile email',
        expired=now() + datetime.timedelta(days=2),
    )
    response = app.get(
        make_url(
            'oidc-authorize',
            params={
                'client_id': oidc_client.client_id,
                'redirect_uri': redirect_uri,
                'response_type': response_type,
                'scope': 'openid email profile',
            },
        )
    )
    if oidc_client.authorization_flow == oidc_client.FLOW_AUTHORIZATION_CODE:
        assert_authorization_response(response, code=None, iss='https://testserver/')
    elif oidc_client.authorization_flow == oidc_client.FLOW_IMPLICIT:
        assert_authorization_response(
            response,
            access_token=None,
            id_token=None,
            expires_in=None,
            token_type=None,
            fragment=True,
        )

    # client ask for explicit authorization
    authorize_url = make_url(
        'oidc-authorize',
        params={
            'client_id': oidc_client.client_id,
            'redirect_uri': redirect_uri,
            'response_type': response_type,
            'scope': 'openid email profile',
            'prompt': 'consent',
        },
    )
    response = app.get(authorize_url)
    assert 'a2-oidc-authorization-form' in response.text
    # check all authorization have been deleted, it's our policy
    assert OIDCAuthorization.objects.count() == 0
    if oidc_client.authorization_mode == oidc_client.AUTHORIZATION_MODE_NONE:
        # authorization mode is none, but explicit consent is asked, we validate it
        response = response.form.submit('accept')

    # authorization has expired
    OIDCCode.objects.all().delete()
    authorize.expired = now() - datetime.timedelta(days=2)
    authorize.save()
    response = app.get(authorize_url)
    assert 'a2-oidc-authorization-form' in response.text
    authorize.expired = now() + datetime.timedelta(days=2)
    authorize.scopes = 'openid profile'
    authorize.save()
    assert OIDCAuthorization.objects.count() == 1
    if not oidc_client.always_save_authorization:
        response.form['do_not_ask_again'] = True
    response = response.form.submit('accept')
    assert OIDCAuthorization.objects.count() == 1
    # old authorizations have been deleted
    assert OIDCAuthorization.objects.get().pk != authorize.pk

    # check expired codes
    if oidc_client.authorization_flow == oidc_client.FLOW_AUTHORIZATION_CODE:
        assert OIDCCode.objects.count() == 1
        oidc_code = OIDCCode.objects.get()
        assert oidc_code.is_valid()

        location = urllib.parse.urlparse(response['Location'])
        query = urllib.parse.parse_qs(location.query)
        assert set(query.keys()) == {'code', 'iss'}
        assert query['code'] == [oidc_code.uuid]
        code = query['code'][0]
        token_url = make_url('oidc-token')

        # missing code parameter
        params = {
            'grant_type': 'authorization_code',
            'redirect_uri': oidc_client.redirect_uris.split()[0],
        }
        response = rp_app.post(
            token_url, params=params, headers=client_authentication_headers(oidc_client), status=400
        )
        assert response.json['error'] == 'invalid_request'
        assert response.json['error_description'] == 'Missing parameter "code"'

        params['code'] = code

        # wrong redirect_uri
        response = rp_app.post(
            token_url,
            params=dict(params, redirect_uri='https://example.com/'),
            headers=client_authentication_headers(oidc_client),
            status=400,
        )
        assert 'error' in response.json
        assert response.json['error'] == 'invalid_grant', response.json
        assert response.json['error_description'] == 'Redirect_uri does not match the code.'
        assert response.json['client_id'] == '1234'

        # unknown code
        response = rp_app.post(
            token_url,
            params=dict(params, code='xyz'),
            headers=client_authentication_headers(oidc_client),
            status=400,
        )
        assert 'error' in response.json
        assert response.json['error'] == 'invalid_grant'
        assert response.json['error_description'] == 'Code is unknown or has expired.'
        assert response.json['client_id'] == '1234'

        # code from another client
        other_client = make_client(rp_app, params={'slug': 'other', 'name': 'other', 'client_id': 'abcd'})
        other_oidc_code = OIDCCode.objects.create(
            client=other_client,
            user=oidc_code.user,
            profile=None,
            scopes='',
            state='1234',
            nonce='1234',
            expired=now() + datetime.timedelta(hours=1),
            redirect_uri=oidc_code.redirect_uri,
            auth_time=now(),
            session_key=oidc_code.session_key,
        )
        response = rp_app.post(
            token_url,
            params=dict(params, code=other_oidc_code.uuid),
            headers=client_authentication_headers(oidc_client),
            status=400,
        )
        assert 'error' in response.json
        assert response.json['error'] == 'invalid_grant'
        assert response.json['error_description'] == 'Code was issued to a different client.', response.json
        assert response.json['client_id'] == '1234'
        other_oidc_code.delete()
        other_client.delete()

        # simulate expired session
        from django.contrib.sessions.models import Session

        session = Session.objects.get(session_key=oidc_code.session_key)
        Session.objects.filter(pk=session.pk).delete()
        response = rp_app.post(
            token_url, params=params, headers=client_authentication_headers(oidc_client), status=400
        )
        assert 'error' in response.json
        assert response.json['error'] == 'invalid_grant'
        assert response.json['error_description'] == 'User is disconnected or session was lost.'
        assert response.json['client_id'] == '1234'
        session.save()

        # make code expire
        oidc_code.expired = now() - datetime.timedelta(seconds=120)
        assert not oidc_code.is_valid()
        oidc_code.save()

        # expired code
        response = rp_app.post(
            token_url, params=params, headers=client_authentication_headers(oidc_client), status=400
        )
        assert 'error' in response.json
        assert response.json['error'] == 'invalid_grant'
        assert response.json['error_description'] == 'Code is unknown or has expired.'
        assert response.json['client_id'] == '1234'

    # invalid logout
    logout_url = make_url(
        'oidc-logout',
        params={
            'post_logout_redirect_uri': 'https://whatever.com/',
        },
    )
    response = app.get(logout_url)
    assert '_auth_user_id' in app.session
    assert 'Location' in response.headers

    # check code expiration after logout
    if oidc_client.authorization_flow == oidc_client.FLOW_AUTHORIZATION_CODE:
        code = OIDCCode.objects.get()
        code.expired = now() + datetime.timedelta(seconds=120)
        code.save()
        assert code.is_valid()
        utils.logout(app)
        code = OIDCCode.objects.get()
        assert not code.is_valid()
        response = app.post(
            token_url,
            params={
                'grant_type': 'authorization_code',
                'code': code.uuid,
                'redirect_uri': oidc_client.redirect_uris.split()[0],
            },
            headers=client_authentication_headers(oidc_client),
            status=400,
        )
        assert 'error' in response.json
        assert response.json['error'] == 'invalid_grant'


def test_client_secret_post_authentication(oidc_settings, app, simple_oidc_client, simple_user):
    utils.login(app, simple_user)
    redirect_uri = simple_oidc_client.redirect_uris.split()[0]

    params = {
        'client_id': simple_oidc_client.client_id,
        'scope': 'openid profile email',
        'redirect_uri': redirect_uri,
        'state': 'xxx',
        'nonce': 'yyy',
        'response_type': 'code',
    }

    authorize_url = make_url('oidc-authorize', params=params)
    response = app.get(authorize_url)
    response = response.form.submit('accept')
    location = urllib.parse.urlparse(response['Location'])
    query = urllib.parse.parse_qs(location.query)
    code = query['code'][0]
    token_url = make_url('oidc-token')
    response = app.post(
        token_url,
        params={
            'grant_type': 'authorization_code',
            'code': code,
            'redirect_uri': redirect_uri,
            'client_id': simple_oidc_client.client_id,
            'client_secret': simple_oidc_client.client_secret,
        },
    )

    assert 'error' not in response.json
    assert 'access_token' in response.json
    assert 'expires_in' in response.json
    assert 'id_token' in response.json
    assert response.json['token_type'] == 'Bearer'


@pytest.mark.parametrize('login_first', [(True,), (False,)])
def test_role_control_access(login_first, oidc_settings, oidc_client, simple_user, app):
    # authorized_role
    role_authorized = Role.objects.create(name='Goth Kids', slug='goth-kids', ou=get_default_ou())
    oidc_client.add_authorized_role(role_authorized)

    redirect_uri = oidc_client.redirect_uris.split()[0]
    params = {
        'client_id': oidc_client.client_id,
        'scope': 'openid profile email',
        'redirect_uri': redirect_uri,
        'state': 'xxx',
        'nonce': 'yyy',
    }

    if oidc_client.authorization_flow == oidc_client.FLOW_AUTHORIZATION_CODE:
        params['response_type'] = 'code'
    elif oidc_client.authorization_flow == oidc_client.FLOW_IMPLICIT:
        params['response_type'] = 'token id_token'
    authorize_url = make_url('oidc-authorize', params=params)

    if login_first:
        utils.login(app, simple_user)

    # user not authorized
    response = app.get(authorize_url)
    assert 'https://example.com/southpark/' in response.text

    # user authorized
    simple_user.roles.add(role_authorized)
    simple_user.save()
    response = app.get(authorize_url)

    if not login_first:
        response = response.follow()
        response.form.set('username', simple_user.username)
        response.form.set('password', simple_user.clear_password)
        response = response.form.submit(name='login-password-submit')
        response = response.follow()
    if oidc_client.authorization_mode != oidc_client.AUTHORIZATION_MODE_NONE:
        if not oidc_client.always_save_authorization:
            response.form['do_not_ask_again'] = True
        response = response.form.submit('accept')
        assert OIDCAuthorization.objects.get()
    if oidc_client.authorization_flow == oidc_client.FLOW_AUTHORIZATION_CODE:
        code = OIDCCode.objects.get()
    location = urllib.parse.urlparse(response['Location'])
    if oidc_client.authorization_flow == oidc_client.FLOW_AUTHORIZATION_CODE:
        query = urllib.parse.parse_qs(location.query)
        code = query['code'][0]
        token_url = make_url('oidc-token')
        response = app.post(
            token_url,
            params={
                'grant_type': 'authorization_code',
                'code': code,
                'redirect_uri': oidc_client.redirect_uris.split()[0],
            },
            headers=client_authentication_headers(oidc_client),
        )
        id_token = response.json['id_token']
    elif oidc_client.authorization_flow == oidc_client.FLOW_IMPLICIT:
        query = urllib.parse.parse_qs(location.fragment)
        id_token = query['id_token'][0]

    if oidc_client.idtoken_algo in (oidc_client.ALGO_RSA, oidc_client.ALGO_EC):
        key = JWKSet.from_json(app.get(reverse('oidc-certs')).content)
        algs = ['RS256', 'ES256']
    elif oidc_client.idtoken_algo == oidc_client.ALGO_HMAC:
        k = base64.b64encode(oidc_client.client_secret.encode('utf-8'))
        key = JWK(kty='oct', k=force_str(k))
        algs = ['HS256']
    else:
        raise NotImplementedError
    jwt = JWT(jwt=id_token, key=key, algs=algs)
    claims = json.loads(jwt.claims)
    if login_first:
        assert claims['acr'] == '0'
    else:
        assert claims['acr'] == '1'


def test_registration_service_slug(oidc_settings, app, simple_oidc_client, simple_user, hooks, mailoutbox):
    redirect_uri = simple_oidc_client.redirect_uris.split()[0]

    simple_oidc_client.ou.home_url = 'https://portal/'
    simple_oidc_client.ou.save()

    params = {
        'client_id': simple_oidc_client.client_id,
        'scope': 'openid profile email',
        'redirect_uri': redirect_uri,
        'state': 'xxx',
        'nonce': 'yyy',
        'response_type': 'code',
    }

    authorize_url = make_url('oidc-authorize', params=params)
    response = app.get(authorize_url)

    response = response.follow().click('Register')
    response.form.set('email', 'john.doe@example.com')
    response = response.form.submit()
    assert len(mailoutbox) == 1
    link = utils.get_link_from_mail(mailoutbox[0])
    response = app.get(link)
    body = response.pyquery('body')[0]
    assert body.attrib['data-home-ou-slug'] == 'default'
    assert body.attrib['data-home-ou-name'] == 'Default organizational unit'
    assert body.attrib['data-home-service-slug'] == 'client'
    assert body.attrib['data-home-service-name'] == 'client'
    assert body.attrib['data-home-url'] == 'https://portal/'
    response.form.set('first_name', 'John')
    response.form.set('last_name', 'Doe')
    response.form.set('password1', 'T0==toto')
    response.form.set('password2', 'T0==toto')
    response = response.form.submit()
    assert hooks.event[0]['kwargs']['name'] == 'sso-request'
    assert hooks.event[0]['kwargs']['service'].slug == 'client'

    assert hooks.event[1]['kwargs']['name'] == 'registration'
    assert hooks.event[1]['kwargs']['service'].slug == 'client'

    assert hooks.event[2]['kwargs']['name'] == 'login'
    assert hooks.event[2]['kwargs']['how'] == 'email'
    assert hooks.event[2]['kwargs']['service'].slug == 'client'


def test_claim_default_value(oidc_settings, normal_oidc_client, simple_user, app):
    oidc_settings.A2_IDP_OIDC_SCOPES = ['openid', 'profile', 'email', 'phone']
    Attribute.objects.create(
        name='phone',
        label='phone',
        kind='phone_number',
        asked_on_registration=False,
        required=False,
        user_visible=False,
        user_editable=False,
    )
    OIDCClaim.objects.create(
        client=normal_oidc_client, name='phone', value='django_user_phone', scopes='phone'
    )
    normal_oidc_client.authorization_flow = normal_oidc_client.FLOW_AUTHORIZATION_CODE
    normal_oidc_client.authorization_mode = normal_oidc_client.AUTHORIZATION_MODE_NONE
    normal_oidc_client.save()

    utils.login(app, simple_user)

    simple_user.username = None
    simple_user.save()

    oidc_client = normal_oidc_client
    redirect_uri = oidc_client.redirect_uris.split()[0]

    params = {
        'client_id': oidc_client.client_id,
        'scope': 'openid email profile phone',
        'redirect_uri': redirect_uri,
        'state': 'xxx',
        'nonce': 'yyy',
        'response_type': 'code',
    }

    def sso():
        authorize_url = make_url('oidc-authorize', params=params)

        response = app.get(authorize_url)
        location = urllib.parse.urlparse(response['Location'])
        query = urllib.parse.parse_qs(location.query)
        code = query['code'][0]

        token_url = make_url('oidc-token')
        response = app.post(
            token_url,
            params={
                'grant_type': 'authorization_code',
                'code': code,
                'redirect_uri': oidc_client.redirect_uris.split()[0],
            },
            headers=client_authentication_headers(oidc_client),
        )
        access_token = response.json['access_token']
        id_token = response.json['id_token']

        k = base64.b64encode(oidc_client.client_secret.encode('utf-8'))
        key = JWK(kty='oct', k=force_str(k))
        jwt = JWT(jwt=id_token, key=key)
        claims = json.loads(jwt.claims)

        user_info_url = make_url('oidc-user-info')
        response = app.get(user_info_url, headers=bearer_authentication_headers(access_token))
        return claims, response.json

    claims, user_info = sso()

    assert claims['sub'] == make_sub(oidc_client, simple_user)
    assert claims['given_name'] == simple_user.first_name
    assert claims['family_name'] == simple_user.last_name
    assert claims['email'] == simple_user.email
    assert claims['phone'] == simple_user.phone
    assert claims['email_verified'] is False

    assert user_info['sub'] == make_sub(oidc_client, simple_user)
    assert user_info['given_name'] == simple_user.first_name
    assert user_info['family_name'] == simple_user.last_name
    assert user_info['email'] == simple_user.email
    assert user_info['phone'] == simple_user.phone
    assert user_info['email_verified'] is False

    params['scope'] = 'openid email'

    claims, user_info = sso()

    assert claims['sub'] == make_sub(oidc_client, simple_user)
    assert claims['email'] == simple_user.email
    assert claims['email_verified'] is False
    assert 'phone' not in claims
    assert 'given_name' not in claims
    assert 'family_name' not in claims

    assert user_info['sub'] == make_sub(oidc_client, simple_user)
    assert user_info['email'] == simple_user.email
    assert user_info['email_verified'] is False
    assert 'phone' not in user_info
    assert 'given_name' not in user_info
    assert 'family_name' not in user_info


def test_claim_templated(oidc_settings, normal_oidc_client, simple_user, app):
    oidc_settings.A2_IDP_OIDC_SCOPES = ['openid', 'profile', 'email']
    OIDCClaim.objects.filter(client=normal_oidc_client, name='given_name').delete()
    OIDCClaim.objects.filter(client=normal_oidc_client, name='family_name').delete()
    OIDCClaim.objects.create(
        client=normal_oidc_client,
        name='given_name',
        value='{{ django_user_first_name|add:"ounet" }}',
        scopes='profile',
    )
    OIDCClaim.objects.create(
        client=normal_oidc_client,
        name='family_name',
        value='{{ "Von der "|add:django_user_last_name }}',
        scopes='profile',
    )
    normal_oidc_client.authorization_flow = normal_oidc_client.FLOW_AUTHORIZATION_CODE
    normal_oidc_client.authorization_mode = normal_oidc_client.AUTHORIZATION_MODE_NONE
    normal_oidc_client.save()

    utils.login(app, simple_user)

    oidc_client = normal_oidc_client
    redirect_uri = oidc_client.redirect_uris.split()[0]

    params = {
        'client_id': oidc_client.client_id,
        'scope': 'openid email profile',
        'redirect_uri': redirect_uri,
        'state': 'xxx',
        'nonce': 'yyy',
        'response_type': 'code',
    }

    def sso():
        authorize_url = make_url('oidc-authorize', params=params)

        response = app.get(authorize_url)
        location = urllib.parse.urlparse(response['Location'])
        query = urllib.parse.parse_qs(location.query)
        code = query['code'][0]

        token_url = make_url('oidc-token')
        response = app.post(
            token_url,
            params={
                'grant_type': 'authorization_code',
                'code': code,
                'redirect_uri': oidc_client.redirect_uris.split()[0],
            },
            headers=client_authentication_headers(oidc_client),
        )
        access_token = response.json['access_token']
        id_token = response.json['id_token']

        k = base64.b64encode(oidc_client.client_secret.encode('utf-8'))
        key = JWK(kty='oct', k=force_str(k))
        jwt = JWT(jwt=id_token, key=key)
        claims = json.loads(jwt.claims)

        user_info_url = make_url('oidc-user-info')
        response = app.get(user_info_url, headers=bearer_authentication_headers(access_token))
        return claims, response.json

    claims, user_info = sso()

    assert claims['given_name'].endswith('ounet')
    assert claims['given_name'].startswith(simple_user.first_name)
    assert claims['family_name'].startswith('Von der')
    assert claims['family_name'].endswith(simple_user.last_name)

    assert user_info['given_name'].endswith('ounet')
    assert user_info['given_name'].startswith(simple_user.first_name)
    assert user_info['family_name'].startswith('Von der')
    assert user_info['family_name'].endswith(simple_user.last_name)


def test_client_validate_redirect_uri():
    client = OIDCClient(
        redirect_uris='''http://example.com
http://example2.com/
http://example3.com/toto
http://*example4.com/
http://example5.com/toto*
http://example6.com/#*
http://example7.com/?*
http://example8.com/?*#*
'''
    )
    # ok
    for uri in [
        'http://example.com',
        'http://example.com/',
        'http://example2.com',
        'http://example2.com/',
        'http://example3.com/toto',
        'http://example3.com/toto/',
        'http://example4.com/',
        'http://example4.com',
        'http://coin.example4.com',
        'http://coin.example4.com/',
        'http://example5.com/toto',
        'http://example5.com/toto/',
        'http://example5.com/toto/tata',
        'http://example5.com/toto/tata/',
        'http://example6.com/#some-fragment',
        'http://example7.com/?foo=bar',
        'http://example8.com/?foo=bar#some-fragment',
    ]:
        client.validate_redirect_uri(uri)
    # nok
    for uri in [
        'http://coin.example.com/',
        'http://example.com/toto/',
        'http://coin.example.com',
        'http://example3.com/',
        'http://example3.com',
        'http://coinexample4.com',
        'http://coinexample4.com/',
        'http://example5.com/tototata/',
        'http://example5.com/tototata',
        'http://example6.com/?foo=bar',
        'http://example7.com/#some-fragment',
    ]:
        with pytest.raises(ValueError, match=r'is not declared'):
            client.validate_redirect_uri(uri)
    client.validate_redirect_uri('http://example5.com/toto/' + 'a' * 500)
    with pytest.raises(ValueError, match=r'redirect_uri length >'):
        client.validate_redirect_uri('http://example5.com/toto/' + 'a' * 1024)


def test_filter_api_users(app, oidc_client, admin, simple_user, role_random):
    oidc_client.has_api_access = True
    oidc_client.save()

    if oidc_client.identifier_policy not in (oidc_client.POLICY_UUID, oidc_client.POLICY_PAIRWISE_REVERSIBLE):
        return

    app.authorization = ('Basic', (oidc_client.client_id, oidc_client.client_secret))

    response = app.get('/api/users/')
    count = len(response.json['results'])
    assert count > 0

    AuthorizedRole.objects.create(service=oidc_client, role=role_random)

    response = app.get('/api/users/')
    assert len(response.json['results']) == 0

    role_random.members.add(simple_user)
    response = app.get('/api/users/')
    assert len(response.json['results']) == 1

    AuthorizedRole.objects.all().delete()

    response = app.get('/api/users/')
    assert len(response.json['results']) == count


def test_credentials_grant(app, oidc_client, admin, simple_user):
    oidc_client.authorization_flow = OIDCClient.FLOW_RESOURCE_OWNER_CRED
    oidc_client.scope = 'openid'
    oidc_client.save()
    token_url = make_url('oidc-token')
    if oidc_client.idtoken_algo == OIDCClient.ALGO_HMAC:
        k = base64url(oidc_client.client_secret.encode('utf-8'))
        jwk = JWK(kty='oct', k=force_str(k))
    elif oidc_client.idtoken_algo == OIDCClient.ALGO_RSA:
        jwk = get_first_rsa_sig_key()
    elif oidc_client.idtoken_algo == OIDCClient.ALGO_EC:
        jwk = get_first_ec_sig_key()

    # 1. test in-request client credentials
    params = {
        'client_id': oidc_client.client_id,
        'client_secret': oidc_client.client_secret,
        'grant_type': 'password',
        'username': simple_user.username,
        'password': simple_user.clear_password,
    }
    response = app.post(token_url, params=params)
    assert 'id_token' in response.json
    token = response.json['id_token']
    assert len(token.split('.')) == 3
    jwt = JWT()
    # jwt deserialization implicitly checks the token signature:
    jwt.deserialize(token, key=jwk)
    claims = json.loads(jwt.claims)
    assert set(claims) == {'acr', 'aud', 'auth_time', 'exp', 'iat', 'iss', 'sub'}
    assert all(claims.values())

    # 2. test basic authz
    params.pop('client_id')
    params.pop('client_secret')

    response = app.post(token_url, params=params, headers=client_authentication_headers(oidc_client))
    assert 'id_token' in response.json
    token = response.json['id_token']
    assert len(token.split('.')) == 3
    jwt = JWT()
    # jwt deserialization implicitly checks the token signature:
    jwt.deserialize(token, key=jwk)
    claims = json.loads(jwt.claims)
    assert set(claims) == {'acr', 'aud', 'auth_time', 'exp', 'iat', 'iss', 'sub'}
    assert all(claims.values())


def test_credentials_grant_ratelimitation_invalid_client(
    app, oidc_client, admin, simple_user, oidc_settings, freezer
):
    freezer.move_to('2020-01-01')

    oidc_client.authorization_flow = OIDCClient.FLOW_RESOURCE_OWNER_CRED
    oidc_client.save()
    token_url = make_url('oidc-token')
    params = {
        'client_id': oidc_client.client_id,
        'client_secret': 'notgood',
        'grant_type': 'password',
        'username': simple_user.username,
        'password': simple_user.clear_password,
    }
    for _ in range(int(oidc_settings.A2_IDP_OIDC_PASSWORD_GRANT_RATELIMIT.split('/')[0])):
        response = app.post(token_url, params=params, status=400)
        assert response.json['error'] == 'invalid_client'
        assert 'Wrong client secret' in response.json['error_description']
    response = app.post(token_url, params=params, status=400)
    assert response.json['error'] == 'invalid_request'
    assert response.json['error_description'] == 'Rate limit exceeded for IP address "127.0.0.1"'


def test_credentials_grant_ratelimitation_valid_client(
    app, oidc_client, admin, simple_user, oidc_settings, freezer
):
    freezer.move_to('2020-01-01')

    oidc_client.authorization_flow = OIDCClient.FLOW_RESOURCE_OWNER_CRED
    oidc_client.save()
    token_url = make_url('oidc-token')
    params = {
        'client_id': oidc_client.client_id,
        'client_secret': oidc_client.client_secret,
        'grant_type': 'password',
        'username': simple_user.username,
        'password': simple_user.clear_password,
    }
    for _ in range(int(oidc_settings.A2_IDP_OIDC_PASSWORD_GRANT_RATELIMIT.split('/')[0])):
        app.post(token_url, params=params)
    response = app.post(token_url, params=params, status=400)
    assert response.json['error'] == 'invalid_client'
    assert response.json['error_description'] == 'Rate limit of 100/m exceeded for client "oidcclient"'


def test_credentials_grant_retrytimout(app, oidc_client, admin, simple_user, settings, freezer):
    freezer.move_to('2020-01-01')

    settings.A2_LOGIN_EXPONENTIAL_RETRY_TIMEOUT_DURATION = 2
    oidc_client.authorization_flow = OIDCClient.FLOW_RESOURCE_OWNER_CRED
    oidc_client.save()
    token_url = make_url('oidc-token')
    params = {
        'client_id': oidc_client.client_id,
        'client_secret': oidc_client.client_secret,
        'grant_type': 'password',
        'username': simple_user.username,
        'password': 'SurelyNotTheRightPassword',
    }
    attempts = 0
    while attempts < 100:
        response = app.post(token_url, params=params, status=400)
        attempts += 1
        if attempts >= 10:
            assert response.json['error'] == 'invalid_request'
            assert 'Too many attempts with erroneous RO password' in response.json['error_description']

    # freeze some time after backoff delay expiration
    freezer.move_to(datetime.timedelta(days=2))

    # obtain a successful login
    params['password'] = simple_user.clear_password
    response = app.post(token_url, params=params, status=200)
    assert 'id_token' in response.json


def test_credentials_grant_invalid_flow(app, oidc_client, admin, simple_user, settings):
    params = {
        'client_id': oidc_client.client_id,
        'client_secret': oidc_client.client_secret,
        'grant_type': 'password',
        'username': simple_user.username,
        'password': 'SurelyNotTheRightPassword',
    }
    token_url = make_url('oidc-token')
    response = app.post(token_url, params=params, status=400)
    assert response.json['error'] == 'unauthorized_client'
    assert 'is not configured' in response.json['error_description']


def test_credentials_grant_invalid_client(app, oidc_client, admin, simple_user, settings, caplog):
    oidc_client.authorization_flow = OIDCClient.FLOW_RESOURCE_OWNER_CRED
    oidc_client.save()
    params = {
        'client_id': oidc_client.client_id,
        'client_secret': 'tryingthis',  # Nope, wrong secret
        'grant_type': 'password',
        'username': simple_user.username,
        'password': simple_user.clear_password,
    }
    token_url = make_url('oidc-token')
    response = app.post(token_url, params=params, status=400)
    assert response.json['error'] == 'invalid_client'
    assert response.json['error_description'] == 'Wrong client secret'
    assert 'tryingthis' not in str(response.json)
    assert 'received tryingthis' in caplog.messages[0]


def test_credentials_grant_invalid_client_identifier(app, oidc_client, admin, simple_user, settings, caplog):
    oidc_client.authorization_flow = OIDCClient.FLOW_RESOURCE_OWNER_CRED
    oidc_client.save()
    params = {
        'client_id': 'xxx',
        'client_secret': 'tryingthis',
        'grant_type': 'password',
        'username': simple_user.username,
        'password': simple_user.clear_password,
    }
    token_url = make_url('oidc-token')
    response = app.post(token_url, params=params, status=400)
    assert response.json['error'] == 'invalid_client'
    assert response.json['error_description'] == 'Wrong client identifier: xxx'

    params['client_id'] = ''
    response = app.post(token_url, params=params, status=400)
    assert response.json['error_description'] == 'Empty client identifier'


def test_credentials_grant_unauthz_client(app, oidc_client, admin, simple_user, settings):
    params = {
        'client_id': oidc_client.client_id,
        'client_secret': oidc_client.client_secret,
        'grant_type': 'password',
        'username': simple_user.username,
        'password': simple_user.clear_password,
    }
    token_url = make_url('oidc-token')
    response = app.post(token_url, params=params, status=400)
    assert response.json['error'] == 'unauthorized_client'
    assert 'Client is not configured for resource owner' in response.json['error_description']


def test_credentials_grant_invalid_content_type(app, oidc_client, admin, simple_user, settings):
    oidc_client.authorization_flow = OIDCClient.FLOW_RESOURCE_OWNER_CRED
    oidc_client.save()
    params = {
        'client_id': oidc_client.client_id,
        'client_secret': oidc_client.client_secret,
        'grant_type': 'password',
        'username': simple_user.username,
        'password': simple_user.clear_password,
    }
    token_url = make_url('oidc-token')
    response = app.post(token_url, params=params, content_type='multipart/form-data', status=400)
    assert response.json['error'] == 'invalid_request'
    assert 'Wrong content type' in response.json['error_description']


def test_credentials_grant_ou_selection_simple(
    app, oidc_client, admin, user_ou1, user_ou2, ou1, ou2, settings
):
    oidc_client.authorization_flow = OIDCClient.FLOW_RESOURCE_OWNER_CRED
    oidc_client.save()
    params = {
        'client_id': oidc_client.client_id,
        'client_secret': oidc_client.client_secret,
        'grant_type': 'password',
        'ou_slug': ou1.slug,
        'username': user_ou1.username,
        'password': user_ou1.clear_password,
    }
    token_url = make_url('oidc-token')
    response = app.post(token_url, params=params, status=200)

    params['username'] = user_ou2.username
    params['password'] = user_ou2.password
    response = app.post(token_url, params=params, status=400)
    assert response.json['error'] == 'access_denied'
    assert response.json['error_description'] == 'Invalid user credentials'


def test_credentials_grant_ou_selection_username_not_unique(
    app, oidc_client, admin, user_ou1, admin_ou2, ou1, ou2, settings
):
    settings.A2_USERNAME_IS_UNIQUE = False
    oidc_client.authorization_flow = OIDCClient.FLOW_RESOURCE_OWNER_CRED
    oidc_client.save()
    admin_ou2.username = user_ou1.username
    admin_ou2.set_password(user_ou1.clear_password)
    admin_ou2.save()
    params = {
        'client_id': oidc_client.client_id,
        'client_secret': oidc_client.client_secret,
        'grant_type': 'password',
        'ou_slug': ou1.slug,
        'username': user_ou1.username,
        'password': user_ou1.clear_password,
    }
    token_url = make_url('oidc-token')
    response = app.post(token_url, params=params)
    assert OIDCAccessToken.objects.get(uuid=response.json['access_token']).user == user_ou1

    params['ou_slug'] = ou2.slug
    response = app.post(token_url, params=params)
    assert OIDCAccessToken.objects.get(uuid=response.json['access_token']).user == admin_ou2


def test_credentials_grant_ou_selection_username_not_unique_wrong_ou(
    app, oidc_client, admin, user_ou1, admin_ou2, ou1, ou2, settings
):
    settings.A2_USERNAME_IS_UNIQUE = False
    oidc_client.authorization_flow = OIDCClient.FLOW_RESOURCE_OWNER_CRED
    oidc_client.save()
    params = {
        'client_id': oidc_client.client_id,
        'client_secret': oidc_client.client_secret,
        'grant_type': 'password',
        'ou_slug': ou2.slug,
        'username': user_ou1.username,
        'password': user_ou1.clear_password,
    }
    token_url = make_url('oidc-token')
    response = app.post(token_url, params=params, status=400)

    params['ou_slug'] = ou1.slug
    params['username'] = admin_ou2.username
    params['password'] = admin_ou2.password
    response = app.post(token_url, params=params, status=400)
    assert response.json['error'] == 'access_denied'
    assert response.json['error_description'] == 'Invalid user credentials'


def test_credentials_grant_ou_selection_invalid_ou(app, oidc_client, admin, user_ou1, settings):
    oidc_client.authorization_flow = OIDCClient.FLOW_RESOURCE_OWNER_CRED
    oidc_client.save()
    params = {
        'client_id': oidc_client.client_id,
        'client_secret': oidc_client.client_secret,
        'grant_type': 'password',
        'ou_slug': 'invalidslug',
        'username': user_ou1.username,
        'password': user_ou1.clear_password,
    }
    token_url = make_url('oidc-token')
    response = app.post(token_url, params=params, status=400)
    assert response.json['error'] == 'invalid_request'
    assert (
        response.json['error_description']
        == 'Parameter "ou_slug" does not match an existing organizational unit'
    )


def test_consents_deleteview(app, oidc_client, simple_user):
    auth = OIDCAuthorization.objects.create(
        client=oidc_client,
        user=simple_user,
        scopes='openid profile offline_access',
        expired=now() + datetime.timedelta(days=2),
    )
    at = OIDCAccessToken.objects.create(
        client=oidc_client,
        user=simple_user,
        scopes='openid profile offline_access',
        expired=now() + datetime.timedelta(days=2),
        authorization=auth,
    )
    rt = OIDCRefreshToken.objects.create(
        client=oidc_client,
        user=simple_user,
        scopes='openid profile offline_access',
        expired=now() + datetime.timedelta(days=2),
        authorization=auth,
    )

    utils.login(app, simple_user)
    resp = app.get(reverse('consents'))
    assert 'You have given authorizations to access your account profile data.' in resp.text
    assert (
        'Additionally, you gave this service access to the aforementioned information at any time including while you are logged out.'
        in resp.text
    )
    resp = resp.form.submit().follow()

    assert 'You have not given any authorization to access your account profile data.' in resp.text

    assert not OIDCAuthorization.objects.filter(id=auth.id).exists()
    assert not OIDCAccessToken.objects.filter(id=at.id).exists()
    assert not OIDCRefreshToken.objects.filter(id=rt.id).exists()


def test_oidc_client_clean():
    OIDCClient(
        redirect_uris='https://example.com/ https://example2.com/', identifier_policy=OIDCClient.POLICY_UUID
    ).clean()

    with pytest.raises(ValidationError, match=r'same domain'):
        OIDCClient(
            redirect_uris='https://example.com/ https://example2.com/',
            identifier_policy=OIDCClient.POLICY_PAIRWISE_REVERSIBLE,
        ).clean()

    with pytest.raises(ValidationError, match=r'same domain'):
        OIDCClient(
            redirect_uris='https://example.com/ https://example2.com/',
            identifier_policy=OIDCClient.POLICY_PAIRWISE,
        ).clean()

    with pytest.raises(ValidationError, match=r'same domain'):
        OIDCClient(
            redirect_uris='https://example.com/ https://example2.com/',
            identifier_policy=OIDCClient.POLICY_PAIRWISE,
        ).clean()

    OIDCClient(
        redirect_uris='https://example.com/ https://example2.com/',
        identifier_policy=OIDCClient.POLICY_UUID,
        authorization_mode=OIDCClient.AUTHORIZATION_MODE_NONE,
    ).clean()

    OIDCClient(
        redirect_uris='https://example.com/ https://example2.com/',
        sector_identifier_uri='https://example.com/',
    ).clean()


def test_consents_view(app, oidc_client, simple_user):
    url = '/accounts/consents/'
    response = app.get(url, status=302)
    assert '/login/' in response.location

    utils.login(app, simple_user)
    response = app.get(url, status=200)
    assert 'You have not given any authorization to access your account profile data.' in response.text

    # create an ou authz
    ou1 = OrganizationalUnit.objects.create(name='Orgunit1', slug='orgunit1')
    OIDCAuthorization.objects.create(
        client=ou1,
        user=simple_user,
        scopes='openid profile email foobar',
        expired=now() + datetime.timedelta(days=2),
    )
    # create service authzs
    OIDCAuthorization.objects.create(
        client=oidc_client, user=simple_user, scopes='openid', expired=now() + datetime.timedelta(days=2)
    )
    OIDCAuthorization.objects.create(
        client=oidc_client,
        user=simple_user,
        scopes='openid profile',
        expired=now() + datetime.timedelta(days=2),
    )
    OIDCAuthorization.objects.create(
        client=oidc_client,
        user=simple_user,
        scopes='openid profile email',
        expired=now() + datetime.timedelta(days=2),
    )

    response = app.get(url, status=200)
    assert 'You have given authorizations to access your account profile data.' in response.text
    assert len(response.html.find_all('button', {'class': 'consents--revoke-button'})) == 4

    assert response.pyquery('.consents--scopes')[0].text.strip() == 'The following information is shared:'
    assert response.pyquery('.consents--scopes li')[0].text == 'Your first and last name, your username'
    assert response.pyquery('.consents--scopes li')[1].text == 'Your email: user@example.net'
    assert response.pyquery('.consents--scopes li')[2].text == 'Other information: foobar'

    # revoke two service authz
    response = response.forms[1].submit()
    response = response.follow()
    assert len(response.html.find_all('button', {'class': 'consents--revoke-button'})) == 3
    assert OIDCAuthorization.objects.filter(client_ct__model='oidcclient').count() == 2
    utils.assert_event(
        'user.service.sso.unauthorization',
        session=app.session,
        user=simple_user,
        service=oidc_client,
    )

    response = response.forms[1].submit()
    response = response.follow()
    assert len(response.html.find_all('button', {'class': 'consents--revoke-button'})) == 2
    assert OIDCAuthorization.objects.filter(client_ct__model='oidcclient').count() == 1

    # revoke the only OU authz
    response = response.forms[0].submit()
    response = response.follow()
    assert len(response.html.find_all('button', {'class': 'consents--revoke-button'})) == 1
    assert OIDCAuthorization.objects.filter(client_ct__model='organizationalunit').count() == 0


def test_oidc_good_next_url_hook(app, oidc_client):
    from django.test.client import RequestFactory

    rf = RequestFactory()
    request = rf.get('/')
    assert good_next_url(request, 'https://example.com/')


def test_authorize_with_prompt_none_and_view_restriction(
    oidc_settings, app, simple_oidc_client, simple_user, cgu_attribute
):
    redirect_uri = simple_oidc_client.redirect_uris.split()[0]

    params = {
        'client_id': simple_oidc_client.client_id,
        'scope': 'openid profile email',
        'redirect_uri': redirect_uri,
        'state': 'xxx',
        'nonce': 'yyy',
        'response_type': 'code',
        'prompt': 'none',
    }

    # login first
    utils.login(app, simple_user)
    authorize_url = make_url('oidc-authorize', params=params)
    response = app.get(authorize_url)

    location = urllib.parse.urlparse(response['Location'])
    assert QueryDict(location.query)['error'] == 'interaction_required'


def test_authorize_with_view_restriction(oidc_settings, app, simple_oidc_client, simple_user, cgu_attribute):
    redirect_uri = simple_oidc_client.redirect_uris.split()[0]

    params = {
        'client_id': simple_oidc_client.client_id,
        'scope': 'openid profile email',
        'redirect_uri': redirect_uri,
        'state': 'xxx',
        'nonce': 'yyy',
        'response_type': 'code',
    }

    # login first
    utils.login(app, simple_user)

    authorize_url = make_url('oidc-authorize', params=params)
    response = app.get(authorize_url)

    assert response.location.startswith('/accounts/edit/required/?')


def test_token_endpoint_code_timeout(oidc_client, oidc_settings, simple_user, app, caplog, rf, freezer):
    '''Verify codes are valid during 30 seconds'''
    utils.login(app, simple_user)

    oidc_client.authorization_mode = oidc_client.AUTHORIZATION_MODE_NONE
    oidc_client.save()

    redirect_uri = oidc_client.redirect_uris.split()[0]
    params = {
        'client_id': oidc_client.client_id,
        'scope': 'openid profile email',
        'redirect_uri': redirect_uri,
        'state': 'xxx',
        'nonce': 'yyy',
        'login_hint': 'backoffice john@example.com',
        'response_type': 'code',
    }
    authorize_url = make_url('oidc-authorize', params=params)
    response = app.get(authorize_url)
    location = urllib.parse.urlparse(response['Location'])
    query = urllib.parse.parse_qs(location.query)
    code = query['code'][0]

    def resolve_code(**kwargs):
        token_url = make_url('oidc-token')
        return app.post(
            token_url,
            params={
                'grant_type': 'authorization_code',
                'code': code,
                'redirect_uri': oidc_client.redirect_uris.split()[0],
            },
            headers=client_authentication_headers(oidc_client),
            **kwargs,
        )

    response = resolve_code()
    assert 'access_token' in response.json

    freezer.move_to(datetime.timedelta(seconds=29))
    response = resolve_code()
    assert 'access_token' in response.json

    # code should expire after 30 seconds
    freezer.move_to(datetime.timedelta(seconds=1.1))
    response = resolve_code(status=400)
    assert 'access_token' not in response.json


def test_authenticate_client_exception_handling(app, oidc_client, simple_user, rf):
    from authentic2_idp_oidc.views import (
        CORSInvalidOrigin,
        InvalidClient,
        InvalidRequest,
        WrongClientSecret,
        authenticate_client,
    )

    request = rf.post('/')

    # missing client id
    with pytest.raises(InvalidRequest):
        authenticate_client(request)

    # empty client id
    request.POST = {'client_id': '', 'client_secret': ''}
    with pytest.raises(InvalidClient):
        authenticate_client(request)

    # empty client secret
    request.POST['client_id'] = 'abc'
    with pytest.raises(InvalidRequest):
        authenticate_client(request)

    # wrong client id
    request.POST['client_secret'] = 'def'
    with pytest.raises(InvalidClient):
        authenticate_client(request)

    # wrong client secret
    request.POST['client_id'] = oidc_client.client_id
    with pytest.raises(WrongClientSecret):
        authenticate_client(request)

    # wrong client secret
    request.POST['client_id'] = oidc_client.client_id
    with pytest.raises(WrongClientSecret):
        authenticate_client(request)

    # OK
    request.POST['client_secret'] = oidc_client.client_secret
    assert authenticate_client(request) == oidc_client

    # missing origin
    request = rf.post(
        '/', data={'client_id': oidc_client.client_id, 'client_secret': 'xxx'}, HTTP_SEC_FETCH_MODE='cors'
    )
    with pytest.raises(CORSInvalidOrigin):
        authenticate_client(request)

    # invalid origin
    request = rf.post(
        '/',
        data={'client_id': oidc_client.client_id, 'client_secret': 'xxx'},
        HTTP_SEC_FETCH_MODE='cors',
        HTTP_ORIGIN='https://sp.example.com/',
    )
    with pytest.raises(CORSInvalidOrigin):
        authenticate_client(request)


def test_token_cors_preflight(app):
    token_url = make_url('oidc-token')

    app.options(token_url, status=405)

    app.options(
        token_url,
        headers={
            'sec-fetch-mode': 'cors',
            'origin': 'https://coin.org',
            'access-control-request-method': 'get',
        },
        status=405,
    )

    response = app.options(
        token_url,
        headers={
            'sec-fetch-mode': 'cors',
            'origin': 'https://coin.org',
            'access-control-request-method': 'post',
        },
        status=200,
    )
    assert response.headers['access-control-allow-origin'] == 'https://coin.org'


def test_login_from_client_accounts_appearance(oidc_client, app, simple_user, settings):
    redirect_uri = oidc_client.redirect_uris.split()[0]
    params = {
        'client_id': oidc_client.client_id,
        'scope': 'openid profile email',
        'redirect_uri': redirect_uri,
        'state': 'xxx',
        'nonce': 'yyy',
        'login_hint': 'backoffice john@example.com',
        'response_type': 'code',
    }
    authorize_url = make_url('oidc-authorize', params=params)
    response = app.get(authorize_url).follow()
    assert not response.pyquery.find('.service-message--link')
    assert response.pyquery.find('.service-message--text')

    # check default settings fallback is unused
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
    service_name.value = 'Some generic service name'
    service_name.save()

    oidc_client.colour = ''
    oidc_client.name = ''
    oidc_client.ou.name = ''
    oidc_client.save()
    oidc_client.ou.save()

    response = app.get(authorize_url).follow()
    response.form.set('username', simple_user.username)
    response.form.set('password', simple_user.clear_password)
    response = response.form.submit(name='login-password-submit').follow()
    response.form.submit('accept')

    response = app.get('/accounts/')
    assert not response.pyquery('style')
    assert not response.pyquery('#a2-service-information')

    oidc_client.colour = '#754da9'
    oidc_client.name = 'One specific client'
    oidc_client.ou.name = 'Misc OU'
    with open('tests/201x201.jpg', 'rb') as fd:
        oidc_client.logo = SimpleUploadedFile(name='201x201.jpg', content=fd.read())
    oidc_client.save()
    oidc_client.ou.save()

    response = app.get('/accounts/')
    assert '#754da9' in response.pyquery('style')[0].text
    assert 'One specific client' in response.pyquery('#a2-service-information')[0].text
    assert ('class', 'a2-service-information--logo') in response.pyquery('img')[0].items()
    assert ('src', '/media/services/logos/201x201.jpg') in response.pyquery('img')[0].items()
    assert ('alt', 'One specific client') in response.pyquery('img')[0].items()


def test_user_info_cors(app, oidc_client, simple_user):
    response = app.options(
        '/idp/oidc/user_info/',
        headers={'Sec-Fetch-Mode': 'cors', 'Access-Control-Request-Method': 'GET'},
        status=200,
    )
    assert response.headers['Access-Control-Allow-Origin'] == '*'
    assert response.headers['Access-Control-Max-Age']
    assert response.headers['Access-Control-Allow-Methods'] == 'GET'
    assert response.headers['Access-Control-Allow-Headers'] == 'x-requested-with,authorization'
    assert response.content == b''

    token = OIDCAccessToken.objects.create(client=oidc_client, user=simple_user)

    response = app.get(
        '/idp/oidc/user_info/',
        headers={'Authorization': f'Bearer {token.uuid}'},
        status=200,
    )
    assert response.headers['Access-Control-Allow-Origin'] == '*'


@pytest.mark.parametrize('code_challenge_method', [None, 'plain'])
def test_pkce_plain(code_challenge_method, oidc_settings, app, simple_oidc_client, simple_user):
    utils.login(app, simple_user)
    redirect_uri = simple_oidc_client.redirect_uris.split()[0]

    params = {
        'client_id': simple_oidc_client.client_id,
        'scope': 'openid profile email',
        'redirect_uri': redirect_uri,
        'state': 'xxx',
        'nonce': 'yyy',
        'response_type': 'code',
        'code_challenge': 'xyz',
    }
    if code_challenge_method:
        params['code_challenge_method'] = code_challenge_method

    response = app.get('/idp/oidc/authorize/', params=params)
    response = response.form.submit('accept')
    code = urllib.parse.parse_qs(urllib.parse.urlparse(response.location).query)['code'][0]

    response = app.post(
        '/idp/oidc/token/',
        params={
            'grant_type': 'authorization_code',
            'code': code,
            'redirect_uri': redirect_uri,
            'client_id': simple_oidc_client.client_id,
            'client_secret': simple_oidc_client.client_secret,
            'code_verifier': 'xyz',
        },
    )

    assert 'error' not in response.json
    assert 'access_token' in response.json
    assert 'expires_in' in response.json
    assert 'id_token' in response.json
    assert response.json['token_type'] == 'Bearer'


def test_pkce_s256(oidc_settings, app, simple_oidc_client, simple_user):
    utils.login(app, simple_user)
    redirect_uri = simple_oidc_client.redirect_uris.split()[0]

    params = {
        'client_id': simple_oidc_client.client_id,
        'scope': 'openid profile email',
        'redirect_uri': redirect_uri,
        'state': 'xxx',
        'nonce': 'yyy',
        'response_type': 'code',
        'code_challenge': pkce_s256('xyz'),
        'code_challenge_method': 'S256',
    }

    response = app.get('/idp/oidc/authorize/', params=params)
    response = response.form.submit('accept')
    code = urllib.parse.parse_qs(urllib.parse.urlparse(response.location).query)['code'][0]

    response = app.post(
        '/idp/oidc/token/',
        params={
            'grant_type': 'authorization_code',
            'code': code,
            'redirect_uri': redirect_uri,
            'client_id': simple_oidc_client.client_id,
            'client_secret': simple_oidc_client.client_secret,
            'code_verifier': 'xyz',
        },
    )

    assert 'error' not in response.json
    assert 'access_token' in response.json
    assert 'expires_in' in response.json
    assert 'id_token' in response.json
    assert response.json['token_type'] == 'Bearer'


@pytest.mark.parametrize('code_challenge_method', [None, 'plain', 'S256'])
def test_pkce_missing_code_verifier(
    code_challenge_method, oidc_settings, app, simple_oidc_client, simple_user
):
    utils.login(app, simple_user)
    redirect_uri = simple_oidc_client.redirect_uris.split()[0]

    params = {
        'client_id': simple_oidc_client.client_id,
        'scope': 'openid profile email',
        'redirect_uri': redirect_uri,
        'state': 'xxx',
        'nonce': 'yyy',
        'response_type': 'code',
        'code_challenge': 'xyz',
    }
    if code_challenge_method:
        params['code_challenge_method'] = code_challenge_method

    response = app.get('/idp/oidc/authorize/', params=params)
    response = response.form.submit('accept')
    code = urllib.parse.parse_qs(urllib.parse.urlparse(response.location).query)['code'][0]

    response = app.post(
        '/idp/oidc/token/',
        params={
            'grant_type': 'authorization_code',
            'code': code,
            'redirect_uri': redirect_uri,
            'client_id': simple_oidc_client.client_id,
            'client_secret': simple_oidc_client.client_secret,
        },
        status=400,
    )

    assert 'error' in response.json


@pytest.mark.parametrize('code_challenge_method', ['', 'abcd'])
def test_pkce_invalid_code_challenge_method(
    code_challenge_method, oidc_settings, app, simple_oidc_client, simple_user
):
    utils.login(app, simple_user)
    redirect_uri = simple_oidc_client.redirect_uris.split()[0]

    params = {
        'client_id': simple_oidc_client.client_id,
        'scope': 'openid profile email',
        'redirect_uri': redirect_uri,
        'state': 'xxx',
        'nonce': 'yyy',
        'response_type': 'code',
        'code_challenge': 'xyz',
        'code_challenge_method': code_challenge_method,
    }

    response = app.get('/idp/oidc/authorize/', params=params)
    response = response.follow()
    href = response.pyquery('#a2-continue')[0].attrib['href']
    location_qs = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
    assert location_qs['error'][0] == 'invalid_request'
    assert (
        location_qs['error_description'][0] == 'Parameter "code_challenge_method" must be "plain" or "S256"'
    )


def test_pkce_code_challenge_is_mandatory(oidc_settings, app, simple_oidc_client, simple_user):
    simple_oidc_client.pkce_code_challenge = True
    simple_oidc_client.save()

    utils.login(app, simple_user)
    redirect_uri = simple_oidc_client.redirect_uris.split()[0]

    params = {
        'client_id': simple_oidc_client.client_id,
        'scope': 'openid profile email',
        'redirect_uri': redirect_uri,
        'state': 'xxx',
        'nonce': 'yyy',
        'response_type': 'code',
    }

    response = app.get('/idp/oidc/authorize/', params=params)
    response = response.follow()
    href = response.pyquery('#a2-continue')[0].attrib['href']
    location_qs = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
    assert location_qs['error'][0] == 'invalid_request'
    assert location_qs['error_description'][0] == 'Parameter "code_challenge_method" MUST be provided'
