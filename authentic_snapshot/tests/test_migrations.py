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

import contextlib

import pytest
from django.contrib import auth
from django.contrib.auth.models import AbstractUser
from django.db import IntegrityError
from django.test.utils import override_settings
from django.utils.timezone import now


def test_migration_custom_user_0021_set_unusable_password(transactional_db, migration):
    old_apps = migration.before([('custom_user', '0020_deleteduser')])

    User = old_apps.get_model('custom_user', 'User')
    user = User.objects.create()
    assert user.password == ''

    new_apps = migration.apply([('custom_user', '0021_set_unusable_password')])
    User = new_apps.get_model('custom_user', 'User')
    user = User.objects.get()
    assert not AbstractUser.has_usable_password(user)


def test_migration_custom_user_0026_remove_user_deleted(transactional_db, migration):
    old_apps = migration.before([('custom_user', '0025_user_deactivation')])

    User = old_apps.get_model('custom_user', 'User')
    DeletedUser = old_apps.get_model('custom_user', 'DeletedUser')
    User.objects.create(deleted=now())
    User.objects.create()

    assert User.objects.count() == 2
    assert DeletedUser.objects.count() == 0
    new_apps = migration.apply([('custom_user', '0026_remove_user_deleted')])
    User = new_apps.get_model('custom_user', 'User')
    DeletedUser = new_apps.get_model('custom_user', 'DeletedUser')
    assert User.objects.count() == 1
    assert DeletedUser.objects.count() == 1


def test_migration_custom_user_0028_user_email_verified_date(transactional_db, migration):
    old_apps = migration.before([('custom_user', '0027_user_deactivation_reason')])

    User = old_apps.get_model('custom_user', 'User')
    User.objects.create(email='john.doe@example.com', email_verified=True)

    new_apps = migration.apply([('custom_user', '0028_user_email_verified_date')])
    User = new_apps.get_model('custom_user', 'User')
    user = User.objects.get()
    assert user.email_verified_date == user.date_joined


def test_migration_custom_user_0047_initialize_services_runtime_settings(transactional_db, migration):
    old_apps = migration.before([('authentic2', '0046_runtimesetting')])

    Setting = old_apps.get_model('authentic2', 'Setting')
    assert Setting.objects.count() == 0

    new_apps = migration.apply([('authentic2', '0047_initialize_services_runtime_settings')])
    Setting = new_apps.get_model('authentic2', 'Setting')
    assert Setting.objects.count() == 4
    assert Setting.objects.filter(key__startswith='sso:').count() == 4
    for setting in Setting.objects.filter(key__startswith='sso:'):
        assert setting.value == ''


def test_migration_custom_user_0050_initialize_users_advanced_configuration(transactional_db, migration):
    old_apps = migration.before([('authentic2', '0049_apiclient_allowed_user_attributes')])
    Setting = old_apps.get_model('authentic2', 'Setting')
    before = Setting.objects.count()

    new_apps = migration.apply([('authentic2', '0050_initialize_users_advanced_configuration')])
    Setting = new_apps.get_model('authentic2', 'Setting')
    assert Setting.objects.count() == before + 1
    assert Setting.objects.filter(key__startswith='users:').count() == 1
    assert Setting.objects.get(key='users:backoffice_sidebar_template').value == ''


def test_migration_apiclient_0051_hash_existing_passwords(transactional_db, migration):
    old_apps = migration.before([('authentic2', '0050_initialize_users_advanced_configuration')])

    old_APIClient = old_apps.get_model('authentic2', 'APIClient')
    test_client = old_APIClient.objects.create(name='foo', password='hackmeplz')
    test_client.save()

    new_app = migration.apply([('authentic2', '0051_hash_existing_passwords')])
    new_APIClient = new_app.get_model('authentic2', 'APIClient')
    test_client = new_APIClient.objects.get(name='foo')
    assert test_client.password != 'hackmeplz'
    # check_password method not available, testing "directly" if password is
    # hashed using auth.hashers.check_password
    assert auth.hashers.check_password('hackmeplz', test_client.password)
    # assert test_client.check_password('hackmeplz')


def test_migration_apiclient_0055_remove_duplicate_user_imports(transactional_db, migration):
    old_app = migration.before([('authentic2', '0054_migrate_user_imports')])

    old_UserImport = old_app.get_model('authentic2', 'UserImport')
    old_OrganizationalUnit = old_app.get_model('a2_rbac', 'OrganizationalUnit')
    ou_id = old_OrganizationalUnit.objects.create(name='test', slug='test').id

    # for now we can create more than one UserImport with the same uuid
    old_UserImport.objects.create(uuid='uuid', ou_id=ou_id)
    old_UserImport.objects.create(uuid='uuid', ou_id=ou_id)
    last_id = old_UserImport.objects.create(uuid='uuid', ou_id=ou_id).id

    assert old_UserImport.objects.filter(uuid='uuid').count() == 3

    new_app = migration.apply(
        [
            # remove duplicates
            ('authentic2', '0055_remove_duplicate_user_imports'),
            # avoid new ones
            ('authentic2', '0056_alter_userimport_uuid'),
        ]
    )

    new_UserImport = new_app.get_model('authentic2', 'UserImport')
    assert new_UserImport.objects.filter(uuid='uuid').count() == 1
    assert new_UserImport.objects.get(uuid='uuid').id == last_id

    with pytest.raises(IntegrityError):
        new_UserImport.objects.create(uuid='uuid', ou_id=ou_id)


def test_migration_apiclient_0058_0060_apiclient_unique_identifier(transactional_db, migration):
    def hashpass(password):
        return auth.hashers.make_password(password)

    old_apps = migration.before([('authentic2', '0057_remove_attributevalue_verification_sources')])

    old_APIClient = old_apps.get_model('authentic2', 'APIClient')
    for i in range(5):
        old_APIClient.objects.create(name='foo', identifier='foo', password=hashpass('pass%d' % i))
    old_APIClient.objects.create(name='foo', identifier='foobar', password=hashpass('pass'))
    old_APIClient.objects.create(name='foo', identifier='foo_3', password=hashpass('password'))
    old_APIClient.objects.create(name='foo', identifier='a' * 256, password=hashpass('aaaa'))
    old_APIClient.objects.create(name='foo', identifier='a' * 256, password=hashpass('bbbb'))
    old_APIClient.objects.create(name='foo', identifier='a' * 254, password=hashpass('cccc'))
    old_APIClient.objects.create(name='foo', identifier='a' * 254, password=hashpass('dddd'))

    new_app = migration.apply([('authentic2', '0060_apiclient_identifier_unique')])
    APIClient = new_app.get_model('authentic2', 'APIClient')
    for i in range(5):
        if i == 0:
            identifier = 'foo'
            identifier_legacy = None
        else:
            offset = 1 if i < 2 else 2
            identifier = 'foo_%d' % (i + offset)
            identifier_legacy = 'foo'
        cli = APIClient.objects.filter(identifier=identifier).get()
        assert auth.hashers.check_password('pass%d' % i, cli.password)
        assert cli.identifier_legacy == identifier_legacy

    id_password = [
        ('foobar', 'pass'),
        ('foo_3', 'password'),
        ('a' * 256, 'aaaa'),
        ('a' * 254, 'cccc'),
        (('a' * 254) + '_2', 'bbbb'),
        (('a' * 254) + '_3', 'dddd'),
    ]
    for identifier, password in id_password:
        cli = APIClient.objects.filter(identifier=identifier).get()
        assert auth.hashers.check_password(password, cli.password)

    new_app = migration.before([('authentic2', '0057_remove_attributevalue_verification_sources')])


@pytest.mark.parametrize('can_change', (True, False, None))
def test_migration_0061_initialize_user_can_change_email_runtime_settings(
    transactional_db, migration, settings, can_change
):
    migration.before([('authentic2', '0060_apiclient_identifier_unique')])

    if can_change is not None:
        override = override_settings
        expected_value = can_change
    else:

        @contextlib.contextmanager
        def override(**dummy):
            yield None

        expected_value = True
        assert not hasattr(settings, 'A2_PROFILE_CAN_CHANGE_EMAIL')

    with override(A2_PROFILE_CAN_CHANGE_EMAIL=can_change):
        new_app = migration.apply([('authentic2', '0061_initialize_user_can_change_email_runtime_settings')])

        Setting = new_app.get_model('authentic2', 'Setting')
        assert Setting.objects.filter(key='users:can_change_email_address').get().value == expected_value


def test_migration_0061_bad_db_state(transactional_db, migration, settings):
    old_app = migration.before([('authentic2', '0060_apiclient_identifier_unique')])

    old_Setting = old_app.get_model('authentic2', 'Setting')
    old_Setting.objects.get_or_create(key='users:can_change_email_address', value=False)
    with override_settings(A2_PROFILE_CAN_CHANGE_EMAIL=True):
        new_app = migration.apply([('authentic2', '0061_initialize_user_can_change_email_runtime_settings')])

        Setting = new_app.get_model('authentic2', 'Setting')
        assert Setting.objects.filter(key='users:can_change_email_address').get().value is False
