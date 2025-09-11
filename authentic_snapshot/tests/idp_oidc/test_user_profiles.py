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

import base64
import json
import urllib.parse
from unittest import mock
from uuid import UUID

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils.encoding import force_str
from django.utils.timezone import now
from jwcrypto.jwk import JWK
from jwcrypto.jwt import JWT

from authentic2.custom_user.models import Profile, ProfileType
from authentic2.utils.misc import make_url
from authentic2_idp_oidc.models import OIDCAccessToken, OIDCClient, OIDCCode
from authentic2_idp_oidc.utils import (
    base64url,
    get_first_ec_sig_key,
    get_first_rsa_sig_key,
    get_jwkset,
    make_pairwise_sub,
    make_sub,
    reverse_pairwise_sub,
)

from .. import utils
from .conftest import bearer_authentication_headers, client_authentication_headers

User = get_user_model()

pytestmark = pytest.mark.django_db


@pytest.fixture
def profile_user():
    user = User.objects.create(
        first_name='Foo',
        last_name='Bar',
        username='foobar',
        email='foobar@example.org',
    )
    profile_type_manager = ProfileType.objects.create(
        name='One Manager Type',
        slug='one-manager-type',
    )
    profile_type_delegate = ProfileType.objects.create(
        name='One Delegate Type',
        slug='one-delegate-type',
    )
    Profile.objects.create(
        user=user,
        profile_type=profile_type_manager,
        identifier='Entity 789',
        email='manager@example789.org',
    )
    data = {
        'entity_name': 'Foobar',
        'entity_data': {'au': 'ie', 'ts': 'rn'},
    }
    Profile.objects.create(
        user=user,
        profile_type=profile_type_delegate,
        identifier='Entity 1011',
        email='delegate@example1011.org',
        data=data,
    )
    user.clear_password = 'foobar'
    user.set_password('foobar')
    user.save()
    return user


def test_admin_base_models(app, superuser, simple_user, profile_settings):
    url = reverse('admin:custom_user_profiletype_add')
    assert ProfileType.objects.count() == 0
    response = utils.login(app, superuser, path=url)
    form = response.forms['profiletype_form']
    form.set('name', 'Manager')
    form.set('slug', 'manager')
    response = form.submit(name='_save').follow()
    assert ProfileType.objects.count() == 1

    response = app.get(url)
    form = response.forms['profiletype_form']
    form.set('name', 'Delegate')
    form.set('slug', 'delegate')
    response = form.submit(name='_save').follow()
    assert ProfileType.objects.count() == 2

    url = reverse('admin:custom_user_profile_add')
    assert Profile.objects.count() == 0
    response = app.get(url)
    form = response.forms['profile_form']
    form.set('user', simple_user.id)
    form.set('profile_type', ProfileType.objects.first().pk)
    form.set('email', 'john.doe@example.org')
    form.set('identifier', 'Entity 0123')
    response = form.submit(name='_save').follow()
    assert Profile.objects.count() == 1

    response = app.get(url)
    form = response.forms['profile_form']
    form.set('user', simple_user.id)
    form.set('profile_type', ProfileType.objects.last().pk)
    form.set('email', 'john.doe@anotherexample.org')
    form.set('identifier', 'Entity 5678')
    response = form.submit(name='_save').follow()
    assert Profile.objects.count() == 2


def test_login_profiles_absent(app, oidc_client, simple_user, profile_settings):
    redirect_uri = oidc_client.redirect_uris.split()[0]
    oidc_client.activate_user_profiles = True
    oidc_client.save()
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
    utils.login(app, simple_user)
    response = app.get(authorize_url)
    assert 'a2-oidc-authorization-form' in response.text
    # not interface changes for users without a profile
    assert not 'profile-validation-' in response.text


def test_login_profiles_deactivated(app, oidc_client, profile_user, profile_settings):
    redirect_uri = oidc_client.redirect_uris.split()[0]
    oidc_client.activate_user_profiles = False
    oidc_client.save()
    params = {
        'client_id': oidc_client.client_id,
        'scope': 'openid profile email',
        'redirect_uri': redirect_uri,
        'state': 'xxx',
        'nonce': 'yyy',
        'login_hint': 'backoffice john@example.com',
        'response_type': 'code',
    }
    assert profile_user.profiles.count() == 2

    authorize_url = make_url('oidc-authorize', params=params)
    utils.login(app, profile_user)
    response = app.get(authorize_url)
    assert 'a2-oidc-authorization-form' in response.text
    # not interface changes for users without a profile
    assert not 'profile-validation-' in response.text


def test_login_profile_selection(app, oidc_client, profile_user, profile_settings):
    oidc_client.idtoken_algo = oidc_client.ALGO_HMAC
    oidc_client.activate_user_profiles = True
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
    assert profile_user.profiles.count() == 2

    authorize_url = make_url('oidc-authorize', params=params)
    utils.login(app, profile_user)
    response = app.get(authorize_url)
    assert 'a2-oidc-authorization-form' in response.text
    assert 'profile-validation-' in response.text
    response.form.set('profile-validation', profile_user.profiles.first().id)
    response = response.form.submit('accept')
    assert OIDCCode.objects.count() == 1
    code = OIDCCode.objects.get()
    assert code.client == oidc_client
    assert code.user == profile_user
    assert code.profile == profile_user.profiles.first()
    assert code.scope_set() == set('openid profile email'.split())
    assert code.state == 'xxx'
    assert code.nonce == 'yyy'
    assert code.redirect_uri == redirect_uri
    assert code.session_key == app.session.session_key
    assert code.auth_time <= now()
    assert code.expired >= now()
    assert response['Location'].startswith(redirect_uri)
    location = urllib.parse.urlparse(response['Location'])
    query = urllib.parse.parse_qs(location.query)
    assert set(query.keys()) == {'code', 'state', 'iss'}
    assert query['code'] == [code.uuid]
    code = query['code'][0]
    assert query['state'] == ['xxx']
    assert query['iss'] == ['https://testserver/']

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
    assert 'error' not in response.json
    assert 'access_token' in response.json
    assert 'expires_in' in response.json
    assert 'id_token' in response.json
    assert response.json['token_type'] == 'Bearer'
    access_token = response.json['access_token']
    assert access_token
    id_token = response.json['id_token']
    k = base64.b64encode(oidc_client.client_secret.encode('utf-8'))
    key = JWK(kty='oct', k=force_str(k))
    algs = ['HS256']
    jwt = JWT(jwt=id_token, key=key, algs=algs)
    claims = json.loads(jwt.claims)

    # check subject identifier substitution:
    assert claims['sub'] != make_sub(oidc_client, profile_user)
    assert claims['sub'] == make_sub(oidc_client, profile_user, profile=profile_user.profiles.first())

    # check email substitution
    assert claims['email'] != profile_user.email
    assert claims['email'] == profile_user.profiles.first().email

    # check additional profile claims
    assert claims['profile_identifier'] == profile_user.profiles.first().identifier
    assert claims['profile_type'] == profile_user.profiles.first().profile_type.slug

    # check profile data dict flatten into oidc claims
    assert claims['entity_name'] == 'Foobar'
    assert claims['entity_data_au'] == 'ie'
    assert claims['entity_data_ts'] == 'rn'

    user_info_url = make_url('oidc-user-info')
    response = app.get(user_info_url, headers=bearer_authentication_headers(access_token))

    assert response.json['profile_type'] == 'one-delegate-type'
    assert response.json['profile_identifier'] == 'Entity 1011'
    assert response.json['email'] == 'delegate@example1011.org'
    assert response.json['sub'] == claims['sub']
    assert response.json['entity_name'] == 'Foobar'
    assert response.json['entity_data_au'] == 'ie'
    assert response.json['entity_data_ts'] == 'rn'


def test_login_implicit(app, oidc_client, profile_user, profile_settings):
    oidc_client.idtoken_algo = oidc_client.ALGO_HMAC
    oidc_client.authorization_flow = oidc_client.FLOW_IMPLICIT
    oidc_client.activate_user_profiles = True
    oidc_client.save()
    redirect_uri = oidc_client.redirect_uris.split()[0]
    params = {
        'client_id': oidc_client.client_id,
        'scope': 'openid profile email',
        'redirect_uri': redirect_uri,
        'state': 'xxx',
        'nonce': 'yyy',
        'login_hint': 'backoffice john@example.com',
        'response_type': 'token id_token',
    }

    assert profile_user.profiles.count() == 2
    authorize_url = make_url('oidc-authorize', params=params)
    utils.login(app, profile_user)
    response = app.get(authorize_url)
    assert 'a2-oidc-authorization-form' in response.text
    assert 'profile-validation-' in response.text
    response.form.set('profile-validation', profile_user.profiles.first().id)
    response = response.form.submit('accept')
    location = urllib.parse.urlparse(response['Location'])
    assert location.fragment
    query = urllib.parse.parse_qs(location.fragment)
    assert OIDCAccessToken.objects.count() == 1
    access_token = OIDCAccessToken.objects.get()
    assert set(query.keys()) == {'access_token', 'token_type', 'expires_in', 'id_token', 'state'}
    assert query['access_token'] == [access_token.uuid]
    assert query['token_type'] == ['Bearer']
    assert query['state'] == ['xxx']
    access_token = query['access_token'][0]
    id_token = query['id_token'][0]
    k = base64.b64encode(oidc_client.client_secret.encode('utf-8'))
    key = JWK(kty='oct', k=force_str(k))
    algs = ['HS256']
    jwt = JWT(jwt=id_token, key=key, algs=algs)
    claims = json.loads(jwt.claims)

    assert claims['sub'] != make_sub(oidc_client, profile_user)
    assert claims['sub'] == make_sub(oidc_client, profile_user, profile=profile_user.profiles.first())

    # check email substitution
    assert claims['email'] != profile_user.email
    assert claims['email'] == profile_user.profiles.first().email

    # check additional profile claims
    assert claims['profile_identifier'] == profile_user.profiles.first().identifier
    assert claims['profile_type'] == profile_user.profiles.first().profile_type.slug

    # check profile data dict flatten into oidc claims
    assert claims['entity_name'] == 'Foobar'
    assert claims['entity_data_au'] == 'ie'
    assert claims['entity_data_ts'] == 'rn'

    user_info_url = make_url('oidc-user-info')
    response = app.get(user_info_url, headers=bearer_authentication_headers(access_token))

    assert response.json['profile_type'] == 'one-delegate-type'
    assert response.json['profile_identifier'] == 'Entity 1011'
    assert response.json['email'] == 'delegate@example1011.org'
    assert response.json['sub'] == claims['sub']
    assert response.json['entity_name'] == 'Foobar'
    assert response.json['entity_data_au'] == 'ie'
    assert response.json['entity_data_ts'] == 'rn'


def test_login_profile_reversible_sub(app, oidc_client, profile_user, profile_settings):
    oidc_client.idtoken_algo = oidc_client.ALGO_EC
    oidc_client.activate_user_profiles = True
    oidc_client.identifier_policy = oidc_client.POLICY_PAIRWISE_REVERSIBLE
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
    utils.login(app, profile_user)
    response = app.get(authorize_url)
    response.form.set('profile-validation', profile_user.profiles.first().id)
    response = response.form.submit('accept')
    code = OIDCCode.objects.get()
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
    assert response.json['access_token']
    id_token = response.json['id_token']

    jwkset = get_jwkset()
    key = jwkset.get_key('ac85baf4-835b-49b2-8272-ffecce7654c9')
    algs = ['ES256']
    jwt = JWT(jwt=id_token, key=key, algs=algs)
    claims = json.loads(jwt.claims)
    assert claims['sub'] != make_sub(oidc_client, profile_user)
    assert claims['sub'] == make_sub(oidc_client, profile_user, profile=profile_user.profiles.first())


def test_full_sub_reversibility(app, oidc_client, profile_settings):
    from django.utils.encoding import smart_bytes

    oidc_client.idtoken_algo = oidc_client.ALGO_EC
    oidc_client.activate_user_profiles = True
    oidc_client.identifier_policy = oidc_client.POLICY_PAIRWISE_REVERSIBLE
    oidc_client.save()
    profile_type_manager = ProfileType.objects.create(
        name='One Manager Type',
        slug='one-manager-type',
    )
    # first with no profiles
    for i in range(10):
        user = User.objects.create(
            first_name='john-%s' % i,
            last_name='doe',
            email='john.doe.%s@example.org' % i,
        )
        sub = make_pairwise_sub(oidc_client, user, profile=None)
        uuid = reverse_pairwise_sub(oidc_client, smart_bytes(sub))
        assert uuid == UUID(user.uuid).bytes

    # then adding user profile information
    for i in range(100):
        user = User.objects.create(
            first_name='john-%s' % i,
            last_name='doe',
            email='john.doe.%s@example.org' % i,
        )
        profile = Profile.objects.create(
            user=user,
            profile_type=profile_type_manager,
            identifier='manager %s' % i,
            email='manager-%s@example.org' % i,
        )
        sub_with_profile = make_pairwise_sub(oidc_client, user, profile=profile)
        sub_without_profile = make_pairwise_sub(oidc_client, user, profile=None)
        uuid_with_profile = reverse_pairwise_sub(oidc_client, smart_bytes(sub_with_profile))
        uuid_without_profile = reverse_pairwise_sub(oidc_client, smart_bytes(sub_without_profile))
        assert sub_with_profile != sub_without_profile
        assert uuid_with_profile == uuid_without_profile
        assert uuid_with_profile == UUID(user.uuid).bytes


def test_modify_user_info_hook(app, oidc_client, profile_settings, profile_user, hooks):
    class MockAppConfig:
        def a2_hook_idp_oidc_modify_user_info(self, client, user, scope_set, user_info, profile=None):
            user_info.clear()
            user_info['email'] = 'def@ad.dre.ss'
            user_info['profile'] = profile.id
            user_info['customclaim'] = 'whatever'

    def mock_get_hooks(hook_name):
        app_config = MockAppConfig()
        return [app_config.a2_hook_idp_oidc_modify_user_info]

    oidc_client.idtoken_algo = oidc_client.ALGO_HMAC
    oidc_client.activate_user_profiles = True
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
    utils.login(app, profile_user)
    response = app.get(authorize_url)
    response.form.set('profile-validation', profile_user.profiles.first().id)
    response = response.form.submit('accept')
    location = urllib.parse.urlparse(response['Location'])
    query = urllib.parse.parse_qs(location.query)
    code = query['code'][0]

    token_url = make_url('oidc-token')

    with mock.patch('authentic2.utils.hooks.get_hooks') as get_hooks:
        get_hooks.return_value = mock_get_hooks('')
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
    k = base64.b64encode(oidc_client.client_secret.encode('utf-8'))
    key = JWK(kty='oct', k=force_str(k))
    algs = ['HS256']
    jwt = JWT(jwt=id_token, key=key, algs=algs)
    claims = json.loads(jwt.claims)

    assert claims['email'] == 'def@ad.dre.ss'
    assert claims['profile'] == profile_user.profiles.first().id
    assert claims['customclaim'] == 'whatever'


def test_profile_selection_user_credentials_grant(app, oidc_client, admin, simple_user):
    oidc_client.authorization_flow = OIDCClient.FLOW_RESOURCE_OWNER_CRED
    oidc_client.scope = 'openid email'
    oidc_client.save()
    token_url = make_url('oidc-token')

    if oidc_client.idtoken_algo == OIDCClient.ALGO_HMAC:
        k = base64url(oidc_client.client_secret.encode('utf-8'))
        jwk = JWK(kty='oct', k=force_str(k))
    elif oidc_client.idtoken_algo == OIDCClient.ALGO_RSA:
        jwk = get_first_rsa_sig_key()
    elif oidc_client.idtoken_algo == OIDCClient.ALGO_EC:
        jwk = get_first_ec_sig_key()

    profile_type = ProfileType.objects.create(
        name='One Manager Type',
        slug='one-manager-type',
    )
    profile = Profile.objects.create(
        user=simple_user,
        identifier='abc',
        profile_type=profile_type,
        email='profile@ad.dr.ess',
    )

    params = {
        'client_id': oidc_client.client_id,
        'client_secret': oidc_client.client_secret,
        'grant_type': 'password',
        'username': simple_user.username,
        'password': simple_user.clear_password,
        # profile id yet client doesn't active profile management
        'profile': profile.id,
    }
    response = app.post(token_url, params=params, status=400)

    assert response.json['error'] == 'access_denied'
    assert response.json['error_description'] == 'User profile requested yet client does not manage profiles.'

    oidc_client.activate_user_profiles = True
    oidc_client.save()

    # wrong profile id
    params['profile'] = profile.id + 1
    response = app.post(token_url, params=params, status=400)

    assert response.json['error'] == 'access_denied'
    assert response.json['error_description'] == 'Invalid profile'

    another_profile = Profile.objects.create(
        user=admin,
        identifier='def',
        profile_type=profile_type,
        email='admin@ad.dr.ess',
    )

    # another user's profile
    params['profile'] = another_profile.id
    response = app.post(token_url, params=params, status=400)

    assert response.json['error'] == 'access_denied'
    # the oidc provider doesn't reveal that this is a valid profile id, linked to another user:
    assert response.json['error_description'] == 'Invalid profile'

    # correct profile
    params['profile'] = profile.id
    response = app.post(token_url, params=params)
    token = response.json['id_token']
    jwt = JWT()
    jwt.deserialize(token, key=jwk)
    claims = json.loads(jwt.claims)

    assert set(claims) == {
        'acr',
        'aud',
        'auth_time',
        'exp',
        'iat',
        'iss',
        'sub',
        # profile-related claims in the id token:
        'profile_type',
        'profile_identifier',
        'email',
        'email_verified',
    }
    assert claims['profile_type'] == 'one-manager-type'
    assert claims['profile_identifier'] == 'abc'
    assert claims['email'] == 'profile@ad.dr.ess'

    access_token = response.json['access_token']
    user_info_url = make_url('oidc-user-info')
    response = app.get(user_info_url, headers=bearer_authentication_headers(access_token))

    assert response.json['profile_type'] == 'one-manager-type'
    assert response.json['profile_identifier'] == 'abc'
    assert response.json['email'] == 'profile@ad.dr.ess'
    assert response.json['sub'] == claims['sub']
