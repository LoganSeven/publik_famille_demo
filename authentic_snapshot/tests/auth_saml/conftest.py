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

import pathlib

import lasso
import pytest

from authentic2.custom_user.models import User
from authentic2.models import Attribute
from authentic2_auth_saml.adapters import AuthenticAdapter
from authentic2_auth_saml.models import SAMLAuthenticator, SetAttributeAction


@pytest.fixture
def adapter():
    return AuthenticAdapter()


base_path = pathlib.Path(__file__).parent


@pytest.fixture
def idp(db, settings):
    settings.MELLON_PRIVATE_KEY = str((base_path / './private_key.pem').resolve())
    settings.MELLON_PUBLIC_KEY = str((base_path / './public_key.pem').resolve())
    authenticator = SAMLAuthenticator.objects.create(
        enabled=True,
        metadata=(base_path / './metadata.xml').read_text(),
        slug='idp1',
    )
    SetAttributeAction.objects.create(
        authenticator=authenticator,
        user_field='email',
        saml_attribute='mail',
        mandatory=True,
    )
    SetAttributeAction.objects.create(
        authenticator=authenticator,
        user_field='title',
        saml_attribute='title',
    )
    SetAttributeAction.objects.create(
        authenticator=authenticator,
        user_field='first_name',
        saml_attribute='http://nice/attribute/givenName',
    )
    return authenticator.settings


@pytest.fixture
def title_attribute(db):
    return Attribute.objects.create(kind='title', name='title', label='title')


@pytest.fixture
def saml_attributes():
    return {
        'issuer': 'http://idp5/metadata',
        'name_id_content': 'xxx',
        'name_id_format': lasso.SAML2_NAME_IDENTIFIER_FORMAT_PERSISTENT,
        'mail': ['john.doe@example.com'],
        'title': ['Mr.'],
        'http://nice/attribute/givenName': ['John'],
    }


@pytest.fixture
def user(db):
    return User.objects.create()
