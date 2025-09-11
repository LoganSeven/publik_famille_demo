# authentic2 - authentic2 authentication for FranceConnect
# Copyright (C) 2020 Entr'ouvert
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


def test_migration_0003_0004_fcaccount_order(migration):
    migrate_from = [('authentic2_auth_fc', '0002_auto_20200416_1439')]
    # it's a two-parts migration, as it contains data and schema changes.
    migrate_to = [('authentic2_auth_fc', '0004_fcaccount_order2')]

    old_apps = migration.before(migrate_from, at_end=False)

    User = old_apps.get_model('custom_user', 'User')
    FcAccount = old_apps.get_model('authentic2_auth_fc', 'FcAccount')
    user1 = User.objects.create(username='user1')
    user2 = User.objects.create(username='user2')
    user3 = User.objects.create(username='user3')
    FcAccount.objects.create(user=user1, sub='sub1')
    FcAccount.objects.create(user=user1, sub='sub2')
    FcAccount.objects.create(user=user2, sub='sub2')
    FcAccount.objects.create(user=user2, sub='sub3')
    FcAccount.objects.create(user=user3, sub='sub3')
    assert len(set(FcAccount.objects.values_list('user_id', flat=True))) == 3
    assert len(set(FcAccount.objects.values_list('sub', flat=True))) == 3
    assert FcAccount.objects.count() == 5

    # execute migration
    new_apps = migration.apply(migrate_to)
    FcAccount = new_apps.get_model('authentic2_auth_fc', 'FcAccount')
    assert len(set(FcAccount.objects.values_list('user_id', 'order'))) == 5
    assert len(set(FcAccount.objects.values_list('sub', 'order'))) == 5


def test_migration_0011_fc_account_discard_nonzero_orders(migration):
    old_apps = migration.before([('authentic2_auth_fc', '0010_fcauthenticator_jwks')])

    User = old_apps.get_model('custom_user', 'User')
    FcAccount = old_apps.get_model('authentic2_auth_fc', 'FcAccount')
    user1 = User.objects.create(username='user1')
    user2 = User.objects.create(username='user2')
    FcAccount.objects.create(user=user1, sub='sub11', order=0)
    FcAccount.objects.create(user=user1, sub='sub12', order=1)
    FcAccount.objects.create(user=user2, sub='sub21', order=2)
    FcAccount.objects.create(user=user2, sub='sub22', order=5)
    assert FcAccount.objects.count() == 4

    new_apps = migration.apply([('authentic2_auth_fc', '0011_fc_account_discard_nonzero_orders')])

    User = new_apps.get_model('custom_user', 'User')
    FcAccount = new_apps.get_model('authentic2_auth_fc', 'FcAccount')
    user1 = User.objects.get(username='user1')

    assert FcAccount.objects.count() == 1
    assert FcAccount.objects.get().user == user1
