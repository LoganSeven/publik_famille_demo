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

import pytest
from django.core.exceptions import ValidationError
from django.utils.timezone import now

from authentic2.a2_rbac.utils import get_default_ou
from authentic2_idp_oidc.models import OIDCAccessToken, OIDCAuthorization, OIDCClient, OIDCCode


class TestClient:
    @pytest.mark.parametrize(
        'uri,patterns',
        [
            ('http://example.com/a/b/c', ['http://example.com/a/b/c']),
            ('http://example.com/a/b/c', ['http://*example.com/a/b/c']),
            ('http://foobar.example.com/a/b/c', ['http://*example.com/a/b/c']),
            ('http://example.com:80/a/b/c', ['http://example.com/a/b/c']),
            ('https://example.com/a/b/c', ['https://example.com/a/b/c']),
            ('https://example.com:443/a/b/c', ['https://example.com/a/b/c']),
            ('https://example.com/a/b/c?foo=bar', ['https://example.com/a/b/c?foo=bar']),
            ('https://example.com/a/b/c?foo=bar#foobar', ['https://example.com/a/*?*#*']),
            ('com.example.app:/a/b/c', ['com.example.app:/a/b/c']),
            ('com.example.app:/a/b/c?foo=bar#foobar', ['com.example.app:/a/*?*#*']),
            ('http://localhost:1234/a/b/c?foo=bar#foobar', ['http://localhost:1234/a/*?*#*']),
            ('http://localhost:1234/a/b/c?foo=bar#foobar', ['//localhost:1234/a/*?*#*']),
            ('https://localhost/a/b/c?foo=bar#foobar', ['//localhost:0/a/*?*#*']),
            ('https://localhost/a/b/c?foo=bar#foobar', ['//localhost/a/*?*#*']),
        ],
    )
    def test_validate_uri_ok(self, uri, patterns):
        OIDCClient(ou=None)._validate_uri(uri, patterns)
        OIDCClient(ou=None, redirect_uris=' '.join(patterns)).validate_redirect_uri(uri)
        OIDCClient(ou=None, post_logout_redirect_uris=' '.join(patterns)).validate_post_logout_redirect_uris(
            uri
        )

    @pytest.mark.parametrize(
        'uri,patterns',
        [
            ('https://example.com/a/b/c', ['http://example.com/a/b/c']),
            ('http://example.com/a/b/c', ['http:/a/b/c']),
            ('http://example.com/a/b/c', ['http://*/a/b/c']),
            ('http://example.com/a/b/c', ['http:/*']),
            ('http://fooexample.com/a/b/c', ['http://*example.com/a/b/c']),
            ('https://example.com/a/b/c', ['https://example.com/a/b']),
            ('https://example.com/a/b/c#xyz', ['https://example.com/a/b/c']),
            ('https://example.com/a/b/c?bar=foo', ['https://example.com/a/b/c?foo=bar']),
            ('https://example.com/a/b/c?foo=bar&bar=foo', ['https://example.com/a/b/c?foo=bar']),
            ('https://example.com/a/b/c?foo=bar#foobar', ['https://example.com/x/*?*#*']),
            ('com.example.app:/a/b/c', ['com.example.app:/a/b']),
            ('com.example.app:/a/b/c?foo=bar#foobar', ['com.example.app:/x/*?*#*']),
            ('http://localhost:1234/a/b/c?foo=bar#foobar', ['http://localhost:1234/x/*?*#*']),
            ('ftp://localhost:1234/a/b/c?foo=bar#foobar', ['//localhost:1234/a/*?*#*']),
            ('ftp://localhost:1234/a/b/c?foo=bar#foobar', ['//localhost:6789/a/*?*#*']),
        ],
    )
    def test_validate_uri_nok(self, uri, patterns):
        with pytest.raises(ValueError):
            OIDCClient(ou=None)._validate_uri(uri, patterns)
        with pytest.raises(ValueError):
            OIDCClient(ou=None, redirect_uris=' '.join(patterns)).validate_redirect_uri(uri)
        with pytest.raises(ValueError):
            OIDCClient(
                ou=None, post_logout_redirect_uris=' '.join(patterns)
            ).validate_post_logout_redirect_uris(uri)

    def test_clean(self, db):
        OIDCClient.objects.create(
            name='Foobar',
            slug='foobar',
            authorization_mode=OIDCClient.AUTHORIZATION_MODE_NONE,
            redirect_uris='https://rp.mycity.org/oidc/callback',
        ).full_clean()
        OIDCClient.objects.create(
            name='Foobaz',
            slug='foobaz',
            authorization_mode=OIDCClient.AUTHORIZATION_MODE_BY_SERVICE,
            redirect_uris='https://rp2.mycity.org/oidc/callback',
        ).full_clean()
        OIDCClient.objects.create(
            name='Goobaz',
            slug='goobaz',
            authorization_mode=OIDCClient.AUTHORIZATION_MODE_BY_OU,
            redirect_uris='https://rp3.mycity.org/oidc/callback',
            ou=get_default_ou(),
        ).full_clean()


def test_expired_manager(db, simple_user):
    expired = now() - datetime.timedelta(seconds=1)
    not_expired = now() + datetime.timedelta(days=1)
    client = OIDCClient.objects.create(
        name='client', slug='client', ou=get_default_ou(), redirect_uris='https://example.com/'
    )
    OIDCAuthorization.objects.create(client=client, user=simple_user, scopes='openid', expired=expired)
    OIDCAuthorization.objects.create(client=client, user=simple_user, scopes='openid', expired=not_expired)
    assert OIDCAuthorization.objects.count() == 2
    OIDCAuthorization.objects.cleanup()
    assert OIDCAuthorization.objects.count() == 1

    OIDCCode.objects.create(
        client=client,
        user=simple_user,
        scopes='openid',
        redirect_uri='https://example.com/',
        session_key='xxx',
        auth_time=now(),
        expired=expired,
    )
    OIDCCode.objects.create(
        client=client,
        user=simple_user,
        scopes='openid',
        redirect_uri='https://example.com/',
        session_key='xxx',
        auth_time=now(),
        expired=not_expired,
    )
    assert OIDCCode.objects.count() == 2
    OIDCCode.objects.cleanup()
    assert OIDCCode.objects.count() == 1

    OIDCAccessToken.objects.create(
        client=client, user=simple_user, scopes='openid', session_key='xxx', expired=expired
    )
    OIDCAccessToken.objects.create(
        client=client, user=simple_user, scopes='openid', session_key='xxx', expired=not_expired
    )
    assert OIDCAccessToken.objects.count() == 2
    OIDCAccessToken.objects.cleanup()
    assert OIDCAccessToken.objects.count() == 1


def test_access_token_is_valid_session(simple_oidc_client, simple_user, session):
    token = OIDCAccessToken.objects.create(
        client=simple_oidc_client, user=simple_user, scopes='openid', session_key=session.session_key
    )

    assert token.is_valid()
    session.flush()
    token.refresh_from_db()
    assert not token.is_valid()


def test_access_token_is_valid_expired(simple_oidc_client, simple_user, freezer):
    start = now()
    expired = start + datetime.timedelta(seconds=30)

    token = OIDCAccessToken.objects.create(
        client=simple_oidc_client, user=simple_user, scopes='openid', expired=expired
    )

    assert token.is_valid()
    freezer.move_to(expired)
    token.refresh_from_db()
    assert token.is_valid()
    freezer.move_to(expired + datetime.timedelta(seconds=1))
    token.refresh_from_db()
    assert not token.is_valid()


def test_access_token_is_valid_session_and_expired(simple_oidc_client, simple_user, session, freezer):
    start = now()
    expired = start + datetime.timedelta(seconds=30)

    token = OIDCAccessToken.objects.create(
        client=simple_oidc_client,
        user=simple_user,
        scopes='openid',
        session_key=session.session_key,
        expired=expired,
    )

    assert token.is_valid()
    freezer.move_to(expired)
    token.refresh_from_db()
    assert token.is_valid()
    freezer.move_to(expired + datetime.timedelta(seconds=1))
    token.refresh_from_db()
    assert not token.is_valid()
    freezer.move_to(start)
    token.refresh_from_db()
    assert token.is_valid()
    session.flush()
    token.refresh_from_db()
    assert not token.is_valid()


def test_clean_pkce(db):
    client = OIDCClient(authorization_flow=OIDCClient.FLOW_AUTHORIZATION_CODE, pkce_code_challenge=True)
    client.clean()

    with pytest.raises(ValidationError, match=r'PKCE can only.*'):
        client.authorization_flow = OIDCClient.FLOW_IMPLICIT
        client.clean()
