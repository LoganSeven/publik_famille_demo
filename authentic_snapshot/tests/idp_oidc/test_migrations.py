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


def test_oidclient_claims_data_migration(migration):
    app = 'authentic2_idp_oidc'
    migrate_from = [(app, '0009_auto_20180313_1156')]
    migrate_to = [(app, '0010_oidcclaim')]

    old_apps = migration.before(migrate_from)
    OIDCClient = old_apps.get_model('authentic2_idp_oidc', 'OIDCClient')

    client = OIDCClient(name='test', slug='test', redirect_uris='https://example.net/')
    client.save()

    new_apps = migration.apply(migrate_to)
    OIDCClient = new_apps.get_model('authentic2_idp_oidc', 'OIDCClient')
    OIDCClaim = new_apps.get_model('authentic2_idp_oidc', 'OIDCClaim')

    client = OIDCClient.objects.first()
    assert OIDCClaim.objects.filter(client=client.id).count() == 5


def test_oidclient_preferred_username_as_identifier_data_migration(migration):
    app = 'authentic2_idp_oidc'
    migrate_from = [(app, '0010_oidcclaim')]
    migrate_to = [(app, '0011_auto_20180808_1546')]

    old_apps = migration.before(migrate_from)
    OIDCClient = old_apps.get_model('authentic2_idp_oidc', 'OIDCClient')
    OIDCClaim = old_apps.get_model('authentic2_idp_oidc', 'OIDCClaim')

    client1 = OIDCClient.objects.create(name='test', slug='test', redirect_uris='https://example.net/')
    client2 = OIDCClient.objects.create(name='test1', slug='test1', redirect_uris='https://example.net/')
    client3 = OIDCClient.objects.create(name='test2', slug='test2', redirect_uris='https://example.net/')
    client4 = OIDCClient.objects.create(name='test3', slug='test3', redirect_uris='https://example.net/')
    for client in (client1, client2, client3, client4):
        if client.name == 'test1':
            continue
        if client.name == 'test3':
            OIDCClaim.objects.create(
                client=client, name='preferred_username', value='django_user_full_name', scopes='profile'
            )
        else:
            OIDCClaim.objects.create(
                client=client, name='preferred_username', value='django_user_username', scopes='profile'
            )
        OIDCClaim.objects.create(
            client=client, name='given_name', value='django_user_first_name', scopes='profile'
        )
        OIDCClaim.objects.create(
            client=client, name='family_name', value='django_user_last_name', scopes='profile'
        )
        if client.name == 'test2':
            continue
        OIDCClaim.objects.create(client=client, name='email', value='django_user_email', scopes='email')
        OIDCClaim.objects.create(
            client=client, name='email_verified', value='django_user_email_verified', scopes='email'
        )

    new_apps = migration.apply(migrate_to)
    OIDCClient = new_apps.get_model('authentic2_idp_oidc', 'OIDCClient')

    client = OIDCClient.objects.first()
    for client in OIDCClient.objects.all():
        claims = client.oidcclaim_set.all()
        if client.name == 'test':
            assert claims.count() == 5
            assert sorted(claims.values_list('name', flat=True)) == [
                'email',
                'email_verified',
                'family_name',
                'given_name',
                'preferred_username',
            ]
            assert sorted(claims.values_list('value', flat=True)) == [
                'django_user_email',
                'django_user_email_verified',
                'django_user_first_name',
                'django_user_identifier',
                'django_user_last_name',
            ]
        elif client.name == 'test2':
            assert claims.count() == 3
            assert sorted(claims.values_list('name', flat=True)) == [
                'family_name',
                'given_name',
                'preferred_username',
            ]
            assert sorted(claims.values_list('value', flat=True)) == [
                'django_user_first_name',
                'django_user_last_name',
                'django_user_username',
            ]
        elif client.name == 'test3':
            assert claims.count() == 5
            assert sorted(claims.values_list('name', flat=True)) == [
                'email',
                'email_verified',
                'family_name',
                'given_name',
                'preferred_username',
            ]
            assert sorted(claims.values_list('value', flat=True)) == [
                'django_user_email',
                'django_user_email_verified',
                'django_user_first_name',
                'django_user_full_name',
                'django_user_last_name',
            ]
        else:
            assert claims.count() == 0
