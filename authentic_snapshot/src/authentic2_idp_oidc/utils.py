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

import base64
import hashlib
import json
import logging
import urllib.parse
import uuid

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils.encoding import force_bytes, force_str
from jwcrypto.jwk import JWK, InvalidJWKValue, JWKSet
from jwcrypto.jwt import JWT

from authentic2.attributes_ng.engine import get_attributes
from authentic2.utils import crypto, hooks
from authentic2.utils.misc import make_url
from authentic2.utils.template import Template

from . import app_settings

logger = logging.getLogger(__name__)


def base64url(content):
    return base64.urlsafe_b64encode(content).strip(b'=')


def flatten_dict(data):
    for key, value in list(data.items()):
        if isinstance(value, dict):
            flatten_dict(value)
            for key2, value2 in value.items():
                data['%s_%s' % (key, key2)] = value2
            del data[key]


def get_jwkset():
    try:
        jwkset = json.dumps(app_settings.JWKSET)
    except Exception as e:
        raise ImproperlyConfigured('invalid setting A2_IDP_OIDC_JWKSET: %s' % e)
    try:
        jwkset = JWKSet.from_json(jwkset)
    except InvalidJWKValue as e:
        raise ImproperlyConfigured('invalid setting A2_IDP_OIDC_JWKSET: %s' % e)
    if len(jwkset['keys']) < 1:
        raise ImproperlyConfigured('empty A2_IDP_OIDC_JWKSET')
    return jwkset


def get_first_sig_key_by_type(kty=None):
    if kty:
        for key in get_jwkset()['keys']:
            # XXX: remove when jwcrypto version is over 0.9.1 everywhere
            if hasattr(key, '_params'):
                if key._params['kty'] != kty:
                    continue
                use = key._params.get('use')
                if use is None or use == 'sig':
                    return key
            else:
                if key['kty'] != kty:
                    continue
                use = key.get('use')
                if use is None or use == 'sig':
                    return key
    return None


def get_first_rsa_sig_key():
    return get_first_sig_key_by_type('RSA')


def get_first_ec_sig_key():
    return get_first_sig_key_by_type('EC')


def make_idtoken(client, claims):
    '''Make a serialized JWT targeted for this client'''
    if client.idtoken_algo == client.ALGO_HMAC:
        header = {'typ': 'JWT', 'alg': 'HS256'}
        k = base64url(client.client_secret.encode('utf-8'))
        jwk = JWK(kty='oct', k=force_str(k))
    elif client.idtoken_algo == client.ALGO_RSA:
        header = {'typ': 'JWT', 'alg': 'RS256'}
        jwk = get_first_rsa_sig_key()
        header['kid'] = jwk.key_id
        if jwk is None:
            raise ImproperlyConfigured('no RSA key for signature operation in A2_IDP_OIDC_JWKSET')
    elif client.idtoken_algo == client.ALGO_EC:
        header = {'typ': 'JWT', 'alg': 'ES256'}
        jwk = get_first_ec_sig_key()
        if jwk is None:
            raise ImproperlyConfigured('no EC key for signature operation in A2_IDP_OIDC_JWKSET')
    else:
        raise NotImplementedError
    jwt = JWT(header=header, claims=claims)
    jwt.make_signed_token(jwk)
    return jwt.serialize()


def scope_set(data):
    '''Convert a scope string into a set of scopes'''
    return {scope.strip() for scope in data.split()}


def clean_words(data):
    '''Clean and order a list of words'''
    return ' '.join(sorted(x.strip() for x in data.split()))


def url_domain(url):
    return urllib.parse.urlparse(url).netloc.split(':')[0]


def make_sub(client, user, profile=None):
    if client.identifier_policy in (client.POLICY_PAIRWISE, client.POLICY_PAIRWISE_REVERSIBLE):
        return make_pairwise_sub(client, user, profile=profile)
    elif client.identifier_policy == client.POLICY_UUID:
        return force_str(user.uuid)
    elif client.identifier_policy == client.POLICY_EMAIL:
        return user.email
    else:
        raise NotImplementedError


def make_pairwise_sub(client, user, profile=None):
    '''Make a pairwise sub'''
    if client.identifier_policy == client.POLICY_PAIRWISE:
        return make_pairwise_unreversible_sub(client, user, profile=profile)
    elif client.identifier_policy == client.POLICY_PAIRWISE_REVERSIBLE:
        return make_pairwise_reversible_sub(client, user, profile=profile)
    else:
        raise NotImplementedError('unknown pairwise client.identifier_policy %s' % client.identifier_policy)


def make_pairwise_unreversible_sub(client, user, profile=None):
    sector_identifier = client.get_sector_identifier()
    sub = sector_identifier + str(user.uuid) + settings.SECRET_KEY
    if profile:
        sub += str(profile.id)
    sub = base64.b64encode(hashlib.sha256(sub.encode('utf-8')).digest())
    return sub.decode('utf-8')


def make_pairwise_reversible_sub(client, user, profile=None):
    return make_pairwise_reversible_sub_from_uuid(client, user.uuid, profile=profile)


def make_pairwise_reversible_sub_from_uuid(client, user_uuid, profile=None):
    try:
        identifier = uuid.UUID(user_uuid).bytes
    except ValueError:
        return None
    sector_identifier = client.get_sector_identifier()
    cipher_args = [
        settings.SECRET_KEY.encode('utf-8'),
        identifier,
        sector_identifier,
    ]
    if profile:
        # add user-chosen profile identifier information
        cipher_args[1] += b'#profile-id:%s' % str(profile.id).encode()
    return crypto.aes_base64url_deterministic_encrypt(*cipher_args).decode('utf-8')


def reverse_pairwise_sub(client, sub):
    sector_identifier = client.get_sector_identifier()
    try:
        reversed_id = crypto.aes_base64url_deterministic_decrypt(
            settings.SECRET_KEY.encode('utf-8'), sub, sector_identifier
        )
        # strip any suffix from original 16-byte-long uuid
        return reversed_id[:16]
    except crypto.DecryptionError:
        return None


def clean_claim_value(value):
    if isinstance(value, (int, float, str, bool)):
        return value
    if isinstance(value, dict):
        return {clean_claim_value(k): clean_claim_value(v) for k, v in value.items()}
    if hasattr(value, '__iter__'):
        return [clean_claim_value(v) for v in value]
    return str(value)


def get_issuer(request):
    return request.build_absolute_uri('/')


def create_user_info(request, client, user, scope_set, id_token=False, profile=None):
    '''Create user info dictionary'''
    user_info = {}
    if 'openid' in scope_set:
        user_info['sub'] = make_sub(client, user, profile=profile)
    attributes = get_attributes(
        {
            'user': user,
            'request': request,
            'service': client,
            '__wanted_attributes': client.get_wanted_attributes(),
        }
    )
    claims = client.oidcclaim_set.filter(name__isnull=False)
    claims_to_show = set()
    for claim in claims:
        if not set(claim.get_scopes()).intersection(scope_set):
            continue
        claims_to_show.add(claim)
        if claim.value and ('{{' in claim.value or '{%' in claim.value):
            template = Template(claim.value)
            attribute_value = template.render(context=attributes)
        else:
            if claim.value not in attributes:
                continue
            try:
                attribute_value = attributes[claim.value]
            except KeyError:
                msg = f'idp_oidc: could not map claim "{claim.name}", claim value "{claim.value}" not found'
                logger.exception(msg)
        if attribute_value is None:
            continue
        try:
            user_info[claim.name] = clean_claim_value(attribute_value)
        except Exception:
            msg = f'idp_oidc: could not map claim {claim.name}'
            logger.exception(msg)
            continue
        # check if attribute is verified
        if claim.value + ':verified' in attributes:
            user_info[claim.name + '_verified'] = True

    for claim in claims_to_show:
        if claim.name not in user_info:
            default_value = None
            if claim.name in [
                'given_name',
                'family_name',
                'full_name',
                'name',
                'middle_name',
                'nickname',
                'email',
                'preferred_username',
            ]:
                default_value = ''
            user_info[claim.name] = default_value
    if profile:
        for attr, userinfo_key in app_settings.PROFILE_OVERRIDE_MAPPING.items():
            if getattr(profile, attr, None) and userinfo_key in user_info:
                user_info[userinfo_key] = getattr(profile, attr)
        user_info['profile_identifier'] = profile.identifier
        user_info['profile_type'] = profile.profile_type.slug
        if isinstance(profile.data, dict):
            flat_data = profile.data.copy()
            flatten_dict(flat_data)
            user_info.update(flat_data)
    user_info['iss'] = get_issuer(request)
    hooks.call_hooks('idp_oidc_modify_user_info', client, user, scope_set, user_info, profile=profile)
    return user_info


def get_session_id(session, client):
    """Derive an OIDC Session Id by hashing:
    - the real session identifier,
    - the client id,
    - the secret key from Django's settings.
    """
    session_key = force_bytes(session.session_key)
    client_id = force_bytes(client.client_id)
    secret_key = force_bytes(settings.SECRET_KEY)
    return hashlib.md5(session_key + client_id + secret_key).hexdigest()


def get_oidc_sessions(request):
    return request.session.get('oidc_sessions', {})


def add_oidc_session(request, client):
    oidc_sessions = request.session.setdefault('oidc_sessions', {})
    if not client.frontchannel_logout_uri:
        return
    sid = get_session_id(request.session, client)
    iss = get_issuer(request)
    uri = make_url(client.frontchannel_logout_uri, params={'iss': iss, 'sid': sid}, resolve=False)
    oidc_session = {
        'frontchannel_logout_uri': uri,
        'frontchannel_timeout': client.frontchannel_timeout,
        'name': client.name,
    }
    if oidc_sessions.get(uri) == oidc_session:
        # already present
        return
    oidc_sessions[uri] = oidc_session
    # force session save
    request.session.modified = True


def pkce_s256(code_verifier):
    ascii_code_verifier = code_verifier.encode('ascii')
    digest = hashlib.sha256(ascii_code_verifier).digest()
    b64url_code_challenge = crypto.base64url_encode(digest).decode('ascii')
    return b64url_code_challenge
