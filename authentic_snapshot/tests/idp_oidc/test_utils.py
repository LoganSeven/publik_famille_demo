# authentic2 - versatile identity manager
# Copyright (C) 2022 Entr'ouvert
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

import pytest
from django.core.exceptions import ValidationError

from authentic2.custom_user.models import User
from authentic2_idp_oidc.models import OIDCClient, validate_redirect_url
from authentic2_idp_oidc.utils import make_pairwise_reversible_sub, make_pairwise_unreversible_sub


@pytest.mark.parametrize(
    'client',
    [
        OIDCClient(ou=None, redirect_uris='https://example.com/'),
        OIDCClient(
            ou=None,
            redirect_uris='https://other.example.com/ https://other2.example.com/',
            sector_identifier_uri='https://example.com/',
        ),
    ],
    ids=['through redirect uris', 'through sector_identifier'],
)
def test_make_sub(client):
    user = User(uuid='41540396cde9488b9b2b4219aac07ba4')

    assert (
        make_pairwise_reversible_sub(client, user)
        == 'YTIBAQAzMSFawvkcXUlT2egFyMRDg2aGOySVWGdWkyGI-NKPH8dHvUCeJh9dlfg6Nu3Fhkc'
    )
    assert make_pairwise_unreversible_sub(client, user) == 'UROBgDTSR0dtHNsKTW44/ai38qkKZBfJi73wqzpSAjc='


class TestValidateRedirectURL:
    @pytest.mark.parametrize(
        'redirect_url',
        [
            'https://example.com/',
            'https://example.com/a/b/c',
            'https://example.com/a/b/c?*#*',
            'https://example.com/a/b/c?foo=bar#foobar',
            'https://example.com/a/b/c?foo=bar#foobar\nhttps://example.com/',
            'com.example.app:/coincoin?*#*',
            'com.example-with-dash.app:/coincoin?*#*',
            'http://localhost:3243/a/b/c?foo=bar#foobar',
        ],
    )
    def test_ok(self, redirect_url):
        validate_redirect_url(redirect_url)

    @pytest.mark.parametrize(
        'redirect_url',
        [
            'ftp://example.com/',
            'https://example.com/\nftp://example.com/',
            'htt://example.com/a/b/c?*#*',
            'https:///a/b/c?*#*',
            'com.example.app://coincoin?*#*',
        ],
    )
    def test_nok(self, redirect_url):
        with pytest.raises(ValidationError):
            validate_redirect_url(redirect_url)
