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

import os

import pytest
import responses
from mellon.models import Issuer, UserSAMLIdentifier

from authentic2.custom_user.models import DeletedUser, User
from authentic2_auth_saml.models import SAMLAttributeLookup, SAMLAuthenticator, ValidationError


def test_save_account_on_delete_user(db):
    user = User.objects.create()
    issuer1, _ = Issuer.objects.get_or_create(entity_id='https://idp1.com/')
    UserSAMLIdentifier.objects.create(user=user, issuer=issuer1, name_id='1234')
    issuer2, _ = Issuer.objects.get_or_create(entity_id='https://idp2.com/')
    UserSAMLIdentifier.objects.create(user=user, issuer=issuer2, name_id='4567')

    user.delete()
    assert UserSAMLIdentifier.objects.count() == 0

    deleted_user = DeletedUser.objects.get()
    assert deleted_user.old_data.get('saml_accounts') == [
        {
            'issuer': 'https://idp1.com/',
            'name_id': '1234',
        },
        {
            'issuer': 'https://idp2.com/',
            'name_id': '4567',
        },
    ]


def test_saml_authenticator_settings(db):
    authenticator = SAMLAuthenticator.objects.create(
        enabled=True, metadata='meta1.xml', slug='idp1', authn_classref='a, b'
    )

    assert 'METADATA' in authenticator.settings
    assert 'METADATA_URL' not in authenticator.settings
    assert authenticator.settings['AUTHN_CLASSREF'] == ['a', 'b']

    authenticator.metadata = ''
    authenticator.metadata_url = 'https://example.com/metadata.xml'
    authenticator.save()

    assert 'METADATA_URL' in authenticator.settings
    assert 'METADATA' not in authenticator.settings

    authenticator.authn_classref = ''
    authenticator.save()

    assert authenticator.settings['AUTHN_CLASSREF'] == []

    SAMLAttributeLookup.objects.create(
        authenticator=authenticator,
        user_field='email',
        saml_attribute='mail',
    )
    assert authenticator.settings['LOOKUP_BY_ATTRIBUTES'] == [
        {'saml_attribute': 'mail', 'user_field': 'email', 'ignore-case': False}
    ]


@responses.activate
def test_saml_authenticator_refresh_metadata_from_url(db):
    authenticator = SAMLAuthenticator.objects.create(
        enabled=True, metadata_url='https://example.com/metadata.xml', slug='idp1', authn_classref='a, b'
    )
    assert authenticator.metadata == ''

    with pytest.raises(ValidationError, match='Metadata URL is unreachable'):
        authenticator.refresh_metadata_from_url()

    with open(os.path.join(os.path.dirname(__file__), 'metadata.xml')) as metadata:
        metadata_content = metadata.read()
        responses.get('https://example.com/metadata.xml', body=metadata_content, status=200)
        authenticator.refresh_metadata_from_url()
        assert authenticator.metadata == metadata_content
