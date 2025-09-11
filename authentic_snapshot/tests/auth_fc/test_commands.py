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

import responses
from jwcrypto.jwk import JWK, JWKSet

from authentic2.a2_rbac.utils import get_default_ou
from authentic2_auth_fc.models import FcAuthenticator
from tests.utils import call_command


@responses.activate
def test_auth_fc_refresh_jwkset_json(db, app, admin, settings, caplog):
    jwkset_url = 'https://fcp-low.integ01.dev-franceconnect.fr/api/v2/jwks'
    kid_rsa = '123'
    kid_ec = '456'

    def generate_remote_jwkset_json():
        key_rsa = JWK.generate(kty='RSA', size=1024, kid=kid_rsa)
        key_ec = JWK.generate(kty='EC', size=256, kid=kid_ec)
        jwkset = JWKSet()
        jwkset.add(key_rsa)
        jwkset.add(key_ec)
        d = jwkset.export(as_dict=True)
        # add extra key without kid to check it is just ignored by change logging
        other_key = JWK.generate(kty='EC', size=256).export(as_dict=True)
        other_key.pop('kid', None)
        d['keys'].append(other_key)
        return d

    responses.get(
        jwkset_url,
        json={
            'headers': {
                'content-type': 'application/json',
            },
            'status_code': 200,
            **generate_remote_jwkset_json(),
        },
    )

    provider = FcAuthenticator(
        ou=get_default_ou(),
        version='2',
        platform='test',
        name='Foo',
        slug='foo',
        client_id='abc',
        client_secret='def',
    )
    provider.full_clean()
    provider.save()
    assert {key.get('kid') for key in provider.jwkset_json['keys']} == {'123', '456', None}

    kid_rsa = 'abcdefg'
    kid_ec = 'hijklmn'

    responses.replace(
        responses.GET,
        jwkset_url,
        json={
            'headers': {
                'content-type': 'application/json',
            },
            'status_code': 200,
            **generate_remote_jwkset_json(),
        },
    )

    call_command('fc-refresh-jwkset-json', '-v1')
    provider.refresh_from_db()
    assert {key.get('kid') for key in provider.jwkset_json['keys']} == {'abcdefg', 'hijklmn', None}

    kid_rsa = '123'
    kid_ec = '456'

    responses.replace(
        responses.GET,
        jwkset_url,
        json={
            'headers': {
                'content-type': 'application/json',
            },
            'status_code': 200,
            **generate_remote_jwkset_json(),
        },
    )

    # version 1 ignores jwkset retrieval
    provider.version = '1'
    provider.save()
    call_command('fc-refresh-jwkset-json', '-v1')
    provider.refresh_from_db()
    assert {key.get('kid') for key in provider.jwkset_json['keys']} == {'abcdefg', 'hijklmn', None}
