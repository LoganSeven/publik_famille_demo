# authentic2 - versatile identity manager
# Copyright (C) 2010-2024 Entr'ouvert
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
import json

from django.core.exceptions import ValidationError
from django.utils.timezone import now
from django.utils.translation import gettext_lazy as _
from jwcrypto.common import JWException, base64url_encode, json_decode
from jwcrypto.jwk import JWK, InvalidJWKValue, JWKSet
from jwcrypto.jwt import JWT


class IDTokenError(ValueError):
    pass


REQUIRED_ID_TOKEN_KEYS = {'iss', 'sub', 'aud', 'exp', 'iat'}
KEY_TYPES = {
    'iss': str,
    'sub': str,
    'exp': int,
    'iat': int,
    'auth_time': int,
    'nonce': str,
    'acr': str,
    'azp': str,
    # aud and amr havec specific checks
}


def parse_timestamp(tstamp):
    if not isinstance(tstamp, int):
        raise ValueError('%s' % tstamp)
    return datetime.datetime.fromtimestamp(tstamp, datetime.UTC)


class IDToken:
    auth_time = None
    nonce = None

    def __init__(self, encoded):
        if not isinstance(encoded, (bytes, str)):
            raise IDTokenError(_('Encoded ID Token must be either binary or string data'))
        self._encoded = encoded

    def as_dict(self, provider):
        return parse_id_token(self._encoded, provider)

    def deserialize(self, provider):
        decoded = parse_id_token(self._encoded, provider)
        if not decoded:
            raise IDTokenError(_('invalid id_token'))
        keys = set(decoded.keys())
        # check fields are ok
        if not keys.issuperset(REQUIRED_ID_TOKEN_KEYS):
            raise IDTokenError(_('missing field: %s') % (REQUIRED_ID_TOKEN_KEYS - keys))
        for key in keys:
            if key == 'amr':
                if not isinstance(decoded['amr'], list):
                    raise IDTokenError(_('invalid amr value: %s') % decoded['amr'])
                if not all(isinstance(v, str) for v in decoded['amr']):
                    raise IDTokenError(_('invalid amr value: %s') % decoded['amr'])
            elif key in KEY_TYPES:
                if key not in REQUIRED_ID_TOKEN_KEYS and (decoded[key] is None or decoded[key] == ''):
                    # for optional keys ignore null and empty string values,
                    # even if specification says it should not happen.
                    # https://openid.net/specs/openid-connect-core-1_0.html#rfc.section.5.3.2
                    continue
                if not isinstance(decoded[key], KEY_TYPES[key]):
                    raise IDTokenError(
                        _('invalid %(key)s value: %(value)s') % {'key': key, 'value': decoded[key]}
                    )
        self.iss = decoded.pop('iss')
        self.sub = decoded.pop('sub')
        self.aud = decoded.pop('aud')
        self.exp = parse_timestamp(decoded.pop('exp'))
        self.iat = parse_timestamp(decoded.pop('iat'))
        auth_time = decoded.get('auth_time')
        if auth_time:
            try:
                self.auth_time = parse_timestamp(auth_time)
            except ValueError as e:
                raise IDTokenError(_('invalid auth_time value: %s') % e)
        self.nonce = decoded.pop('nonce', None)
        self.acr = decoded.pop('acr', None)
        self.azp = decoded.pop('azp', None)
        self.extra = decoded

    def __contains__(self, key):
        if key in self.__dict__:
            return True
        if key in self.extra:
            return True
        return False

    def __getitem__(self, key):
        if key in self.__dict__:
            return self.__dict__[key]
        if key in self.extra:
            return self.extra[key]
        raise KeyError(key)

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default


def parse_jwkset(data):
    try:
        return JWKSet.from_json(data)
    except InvalidJWKValue:
        raise ValidationError(_('Invalid JWKSet'))


def validate_jwkset(data):
    if data:
        parse_jwkset(json.dumps(data))


def base64url_decode(encoded):
    rem = len(encoded) % 4
    if rem > 0:
        encoded += '=' * (4 - rem)
    return base64.urlsafe_b64decode(encoded)


def parse_id_token(encoded, provider, allowed_algs=None):
    """May raise any subclass of jwcrypto.common.JWException"""
    jwt = JWT()
    try:
        jwt.deserialize(encoded, None)
    except ValueError as e:
        raise IDTokenError(_('Error during token deserialization: %s') % e)
    header = jwt.token.jose_header

    alg = header.get('alg')
    if allowed_algs and alg not in allowed_algs:
        raise IDTokenError(_('alg {} not in allowed algs: {}').format(alg, allowed_algs.join(', ')))
    try:
        if alg in (
            'RS256',
            'RS384',
            'RS512',
            'ES256',
            'ES384',
            'ES512',
        ):
            kid = header.get('kid', None)
            key = provider.jwkset and provider.jwkset.get_key(kid=kid)
            if not key:
                raise IDTokenError(_('Key ID %r not in key set') % kid)
        elif alg in ('HS256', 'HS384', 'HS512'):
            key = JWK(kty='oct', k=base64url_encode(provider.client_secret.encode('utf-8')))
        else:
            raise IDTokenError(_('Unknown signature algorithm: %s') % alg)

        jwt = JWT()
        jwt.deserialize(encoded, key)
    except JWException as e:
        raise IDTokenError(_('Error during token parsing: %s') % e)
    payload = json_decode(jwt.claims)
    if 'exp' not in payload or parse_timestamp(payload['exp']) < now():
        raise IDTokenError(_('IDToken is expired.'))
    return payload
