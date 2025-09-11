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
# authentic2

import datetime

import pytest
from django.core import management
from django.core.exceptions import ValidationError
from mellon.models import Issuer, UserSAMLIdentifier

from authentic2.custom_user.models import DeletedUser, User
from authentic2.models import Attribute, AttributeValue, UserExternalId
from authentic2_auth_oidc.models import OIDCAccount, OIDCProvider


def test_user_clean_username(db, settings):
    settings.A2_USERNAME_IS_UNIQUE = True
    u1 = User.objects.create(username='john.doe', email='john.doe@example.net')
    u1.set_password('blank')
    # DoesNotExist
    u1.full_clean()
    u2 = User(username='john.doe', email='john.doe2@example.net')
    u2.set_password('blank')
    # found
    with pytest.raises(ValidationError):
        u2.full_clean()
    u2.save()
    u3 = User(username='john.doe', email='john.doe3@example.net')
    u3.set_password('blank')
    # MultipleObjectsReturned
    with pytest.raises(ValidationError):
        u3.full_clean()


def test_user_clean_email(db, settings):
    settings.A2_EMAIL_IS_UNIQUE = True
    u1 = User.objects.create(username='john.doe', email='john.doe@example.net')
    u1.set_password('blank')
    # DoesNotExist
    u1.full_clean()
    u2 = User(username='john.doe2', email='john.doe@example.net')
    u2.set_password('blank')
    # found
    with pytest.raises(ValidationError):
        u2.full_clean()
    u2.save()
    u3 = User(username='john.doe3', email='john.doe@example.net')
    u3.set_password('blank')
    # MultipleObjectsReturned
    with pytest.raises(ValidationError):
        u3.full_clean()


def test_user_has_verified_attributes(db, settings):
    attribute = Attribute.objects.create(name='phone', label='phone', kind='string')
    user = User(username='john.doe', email='john.doe2@example.net')
    user.save()
    assert user.has_verified_attributes() is False
    attribute_value = AttributeValue.objects.create(owner=user, attribute=attribute, content='0101010101')
    attribute_value.save()
    assert user.has_verified_attributes() is False
    attribute_value.verified = True
    attribute_value.save()
    assert user.has_verified_attributes() is True


def test_sync_first_name(db, settings):
    Attribute.objects.get_or_create(name='first_name', defaults={'label': 'First Name', 'kind': 'string'})

    user = User(username='john.doe', email='john.doe2@example.net')
    user.save()
    user.first_name = 'John'
    user.save()
    assert user.attributes.first_name == 'John'

    user.attributes.first_name = 'John Paul'
    assert user.attributes.first_name == 'John Paul'


def test_sync_phone_deactivated(db, settings):
    Attribute.objects.get_or_create(name='phone', defaults={'label': 'Phone', 'kind': 'phone_number'})

    user = User(username='john.doe', email='john.doe2@example.net')
    user.phone = '+33123456789'  # deprecated model field
    user.save()
    assert user.attributes.phone is None

    user.attributes.phone = '+43876543210'
    user.save()
    user.refresh_from_db()
    assert user.attributes.phone == '+43876543210'
    assert user.phone == '+33123456789'  # deprecated field left unused & unmodified


def test_save_does_not_reset_verified_attributes_first_name(db):
    user = User.objects.create()
    user.verified_attributes.first_name = 'John'

    user = User.objects.get()
    assert user.first_name == 'John'
    assert user.is_verified.first_name
    assert user.verified_attributes.first_name == 'John'
    assert user.attributes.first_name == 'John'
    user.save()

    user = User.objects.get()
    assert user.first_name == 'John'
    assert user.is_verified.first_name
    assert user.verified_attributes.first_name == 'John'
    assert user.attributes.first_name == 'John'
    user.first_name = 'Michel'
    user.save()
    assert not user.is_verified.first_name
    assert user.verified_attributes.first_name is None
    assert user.attributes.first_name == 'Michel'

    user = User.objects.get()
    assert user.first_name == 'Michel'
    assert not user.is_verified.first_name
    assert user.verified_attributes.first_name is None
    assert user.attributes.first_name == 'Michel'
    user.verified_attributes.first_name = 'John'
    assert user.is_verified.first_name
    assert user.verified_attributes.first_name == 'John'
    assert user.attributes.first_name == 'John'

    user = User.objects.get()
    assert user.first_name == 'John'
    assert user.is_verified.first_name
    assert user.verified_attributes.first_name == 'John'
    assert user.attributes.first_name == 'John'


def test_save_does_not_reset_verified_attributes_last_name(db):
    user = User.objects.create()
    user.verified_attributes.last_name = 'John'

    user = User.objects.get()
    assert user.last_name == 'John'
    assert user.is_verified.last_name
    assert user.verified_attributes.last_name == 'John'
    assert user.attributes.last_name == 'John'
    user.save()

    user = User.objects.get()
    assert user.last_name == 'John'
    assert user.is_verified.last_name
    assert user.verified_attributes.last_name == 'John'
    assert user.attributes.last_name == 'John'
    user.last_name = 'Michel'
    user.save()
    assert not user.is_verified.last_name
    assert user.verified_attributes.last_name is None
    assert user.attributes.last_name == 'Michel'

    user = User.objects.get()
    assert user.last_name == 'Michel'
    assert not user.is_verified.last_name
    assert user.verified_attributes.last_name is None
    assert user.attributes.last_name == 'Michel'
    user.verified_attributes.last_name = 'John'
    assert user.is_verified.last_name
    assert user.verified_attributes.last_name == 'John'
    assert user.attributes.last_name == 'John'

    user = User.objects.get()
    assert user.last_name == 'John'
    assert user.is_verified.last_name
    assert user.verified_attributes.last_name == 'John'
    assert user.attributes.last_name == 'John'


def test_save_does_not_reset_verified_attributes_phone(db):
    Attribute.objects.get_or_create(name='phone', defaults={'label': 'Phone', 'kind': 'phone_number'})
    user = User.objects.create()
    user.verified_attributes.phone = '+33123456789'

    user = User.objects.get()
    assert user.phone is None  # deprecated model field
    assert user.is_verified.phone
    assert user.verified_attributes.phone == '+33123456789'
    assert user.attributes.phone == '+33123456789'
    user.save()

    user = User.objects.get()
    assert user.phone is None  # deprecated model field
    assert user.is_verified.phone
    assert user.verified_attributes.phone == '+33123456789'
    assert user.attributes.phone == '+33123456789'
    user.phone = '+43876543210'  # deprecated field mistakenly modified in db
    user.save()
    assert user.is_verified.phone  # verication state unmodified
    assert user.verified_attributes.phone == '+33123456789'
    assert user.attributes.phone == '+33123456789'

    user = User.objects.get()
    assert user.is_verified.phone
    assert user.verified_attributes.phone == '+33123456789'
    assert user.attributes.phone == '+33123456789'


def test_fix_attributes(db):
    first_name_attribute = Attribute.objects.get(name='first_name')
    user = User.objects.create(first_name='john', last_name='Doe')
    user.attribute_values.all().delete()
    first_name_attribute.set_value(user, 'John', verified=True)
    user.refresh_from_db()
    assert user.first_name == 'john'
    assert user.attributes.first_name == 'John'
    assert user.verified_attributes.first_name == 'John'
    assert user.attribute_values.count() == 1
    assert user.attribute_values.all()[0].content == 'John'

    management.call_command('fix-attributes')

    user.refresh_from_db()
    assert user.attribute_values.count() == 2
    assert user.first_name == 'John'
    assert user.verified_attributes.first_name == 'John'
    assert user.verified_attributes.last_name is None
    assert user.last_name == 'Doe'
    assert user.attributes.last_name == 'Doe'


def test_attributes_hasattr(db):
    user = User.objects.create(first_name='john', last_name='Doe')
    assert hasattr(user.attributes, 'first_name')
    assert hasattr(user.attributes, 'last_name')
    assert user.verified_attributes.first_name is None
    assert user.verified_attributes.last_name is None
    assert not hasattr(user.attributes, 'email')
    assert not hasattr(user.verified_attributes, 'email')

    user.verified_attributes.first_name = 'john'
    assert hasattr(user.attributes, 'first_name')
    assert hasattr(user.attributes, 'last_name')
    assert not hasattr(user.attributes, 'email')
    assert not hasattr(user.verified_attributes, 'email')
    assert hasattr(user.verified_attributes, 'first_name')
    assert user.verified_attributes.last_name is None

    user = User.objects.get()
    assert hasattr(user.attributes, 'first_name')
    assert hasattr(user.attributes, 'last_name')
    assert not hasattr(user.attributes, 'email')
    assert not hasattr(user.verified_attributes, 'email')
    assert hasattr(user.verified_attributes, 'first_name')
    assert user.verified_attributes.last_name is None


def test_attribute_values_order(db):
    phone = Attribute.objects.create(name='phone', label='phone', kind='string', order=9)
    birthdate = Attribute.objects.create(name='birthdate', label='birthdate', kind='birthdate', order=10)
    user = User.objects.create(first_name='john', last_name='Doe')
    user.attributes.phone = '0123456789'
    user.attributes.birthdate = datetime.date(year=1980, month=1, day=2)

    attribute_values = user.attribute_values.all().reverse()
    val1, val2 = attribute_values[:2]
    assert val1.attribute.label == 'birthdate'
    assert val2.attribute.label == 'phone'

    phone.order, birthdate.order = birthdate.order, phone.order
    phone.save()
    birthdate.save()
    val1, val2 = attribute_values[:2]
    assert val1.attribute.label == 'phone'
    assert val2.attribute.label == 'birthdate'


def test_save_userexternalid_on_delete_user(db):
    user = User.objects.create()
    UserExternalId.objects.create(user=user, source='ldap1', external_id='1234')
    UserExternalId.objects.create(user=user, source='ldap2', external_id='4567')

    user.delete()
    assert UserExternalId.objects.count() == 0

    deleted_user = DeletedUser.objects.get()
    assert deleted_user.old_data.get('external_ids') == [
        {
            'source': 'ldap1',
            'external_id': '1234',
        },
        {
            'source': 'ldap2',
            'external_id': '4567',
        },
    ]


def test_set_random_password():
    user = User()
    user.set_unusable_password()
    assert not user.has_usable_password()
    user.set_random_password()
    assert user.has_usable_password()


def test_user_is_external_account(db):
    user = User.objects.create()
    assert user.is_external_account() is False

    external_id = UserExternalId.objects.create(user=user, source='ldap1', external_id='1234')
    assert user.is_external_account() is True

    external_id.delete()
    assert user.is_external_account() is False

    provider = OIDCProvider.objects.create()
    oidc_account = OIDCAccount.objects.create(user=user, provider=provider)
    assert user.is_external_account() is True

    oidc_account.delete()
    user.refresh_from_db()
    assert user.is_external_account() is False

    issuer = Issuer.objects.create(slug='idp', entity_id='http://idp/metadata')
    UserSAMLIdentifier.objects.create(user=user, issuer=issuer)

    assert user.is_external_account() is True
