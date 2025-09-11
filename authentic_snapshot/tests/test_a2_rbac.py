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

import pytest
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.core.management import call_command

from authentic2.a2_rbac.models import CHANGE_OP, MANAGE_MEMBERS_OP, Operation
from authentic2.a2_rbac.models import OrganizationalUnit as OU
from authentic2.a2_rbac.models import Permission, Role
from authentic2.a2_rbac.utils import get_default_ou
from authentic2.custom_user.models import User
from authentic2.models import Service
from authentic2.utils.misc import get_hex_uuid
from tests.utils import login, request_select2, scoped_db_fixture


def test_update_rbac(db):
    # 6 content types managers and 1 global manager
    assert Role.objects.count() == 7
    # 6 content type global permissions, 1 role administration permissions (for the main manager
    # role which is self-administered)
    # and 1 user view permission (for the role administrator)
    # and 1 user manage authorizations permission (for the role administrator)
    # and 1 ou view permission (for the user and role administrators)
    assert Permission.objects.count() == 10


def test_delete_role(db):
    rcount = Role.objects.count()
    pcount = Permission.objects.count()
    new_role = Role.objects.create(name='Coucou')
    admin_role = new_role.get_admin_role()

    # There should two more roles, the role and its admin counterpart
    assert Role.objects.count() == rcount + 2

    # There should be two more permissions the manage-members permission on the role
    # and the manage-members permission on the admin role
    manage_members_perm = Permission.objects.by_target(new_role).get(operation__slug='manage_members')
    admin_role = Role.objects.get(
        admin_scope_ct=ContentType.objects.get_for_model(manage_members_perm),
        admin_scope_id=manage_members_perm.pk,
    )
    admin_manage_members_perm = Permission.objects.by_target(admin_role).get(operation__slug='manage_members')
    assert Permission.objects.count() == pcount + 2
    new_role.delete()
    with pytest.raises(Permission.DoesNotExist):
        Permission.objects.get(pk=manage_members_perm.pk)
    with pytest.raises(Role.DoesNotExist):
        Role.objects.get(pk=admin_role.pk)
    with pytest.raises(Permission.DoesNotExist):
        Permission.objects.get(pk=admin_manage_members_perm.pk)
    assert Role.objects.count() == rcount
    assert Permission.objects.count() == pcount


def test_access_control(db):
    role_ct = ContentType.objects.get_for_model(Role)
    role_admin_role = Role.objects.get_admin_role(role_ct, 'admin %s' % role_ct, 'admin-role')
    user1 = User.objects.create(username='john.doe')
    assert not user1.has_perm('a2_rbac.change_role')
    assert not user1.has_perm('a2_rbac.view_role')
    assert not user1.has_perm('a2_rbac.delete_role')
    assert not user1.has_perm('a2_rbac.add_role')
    role_admin_role.members.add(user1)
    del user1._rbac_perms_cache
    assert user1.has_perm('a2_rbac.change_role')
    assert user1.has_perm('a2_rbac.view_role')
    assert user1.has_perm('a2_rbac.delete_role')
    assert user1.has_perm('a2_rbac.add_role')


def test_admin_roles_startswith_a2(db):
    coin = Role.objects.create(name='Coin', slug='coin')
    coin.get_admin_role()
    for role in Role.objects.filter(admin_scope_ct__isnull=False):
        assert role.slug.startswith('_a2'), 'role %s slug must start with _a2: %s' % (role.name, role.slug)


def test_admin_roles_update_slug(db):
    user = User.objects.create(username='john.doe')
    name1 = 'Can manage john.doe'
    slug1 = 'can-manage-john-doe'
    admin_role1 = Role.objects.get_admin_role(user, name1, slug1, update_name=True, update_slug=True)
    assert admin_role1.name == name1
    assert admin_role1.slug == slug1
    name2 = 'Should manage john.doe'
    slug2 = 'should-manage-john-doe'
    admin_role2 = Role.objects.get_admin_role(user, name2, slug2, update_slug=True)
    assert admin_role2.name == name1
    assert admin_role2.slug == slug2
    admin_role3 = Role.objects.get_admin_role(user, name2, slug2, update_name=True)
    assert admin_role3.name == name2
    assert admin_role3.slug == slug2


def test_role_search_user_perm_on_ou_update(db):
    role = Role.objects.create(name='Admin')
    admin_role = role.get_admin_role()
    assert admin_role.permissions.get(operation__slug='search').ou is None

    default_ou = get_default_ou()
    role.ou = default_ou
    role.save()
    assert admin_role.permissions.get(operation__slug='search').ou == default_ou

    new_ou = OU.objects.create(name='New OU')
    role.ou = new_ou
    role.save()
    assert admin_role.permissions.get(operation__slug='search').ou == new_ou


def test_role_clean(db):
    coin = Role(name='Coin')
    coin.clean()
    coin.save()
    assert coin.slug == 'coin'
    with pytest.raises(ValidationError) as exc_info:
        Role(name='Coin2', slug='coin').full_clean()
    assert 'slug' in exc_info.value.error_dict
    with pytest.raises(ValidationError) as exc_info:
        Role(name='Coin', slug='coin2').full_clean()
    assert 'name' in exc_info.value.error_dict


def test_role_natural_key(db):
    ou = OU.objects.create(name='ou1', slug='ou1')
    s1 = Service.objects.create(name='s1', slug='s1')
    s2 = Service.objects.create(name='s2', slug='s2', ou=ou)
    r1 = Role.objects.create(name='r1', slug='r1')
    r2 = Role.objects.create(name='r2', slug='r2', ou=ou)
    r3 = Role.objects.create(name='r3', slug='r3', service=s1)
    r4 = Role.objects.create(name='r4', slug='r4', service=s2)

    for r in (r1, r2, r3, r4):
        assert Role.objects.get_by_natural_key(*r.natural_key()) == r
    assert r1.natural_key() == ['r1', None, None]
    assert r2.natural_key() == ['r2', ['ou1'], None]
    assert r3.natural_key() == ['r3', ['default'], [['default'], 's1']]
    assert r4.natural_key() == ['r4', ['ou1'], [['ou1'], 's2']]
    ou.delete()
    with pytest.raises(Role.DoesNotExist):
        Role.objects.get_by_natural_key(*r2.natural_key())
    with pytest.raises(Role.DoesNotExist):
        Role.objects.get_by_natural_key(*r4.natural_key())


def test_basic_role_export_json(db):
    role = Role.objects.create(
        name='basic role',
        slug='basic-role',
        description='basic role description',
        emails=['test@example.org'],
    )
    role_dict = role.export_json()
    assert role_dict['name'] == role.name
    assert role_dict['slug'] == role.slug
    assert role_dict['uuid'] == role.uuid
    assert role_dict['description'] == role.description
    assert role_dict['details'] == role.details
    assert role_dict['emails'] == role.emails
    assert role_dict['emails_to_members'] == role.emails_to_members
    assert role_dict['is_superuser'] == role.is_superuser
    assert role_dict['external_id'] == role.external_id
    assert role_dict['ou'] is None
    assert role_dict['service'] is None


def test_role_with_ou_export_json(db):
    ou = OU.objects.create(name='ou', slug='ou')
    role = Role.objects.create(name='some role', ou=ou)
    role_dict = role.export_json()
    assert role_dict['ou'] == {'uuid': ou.uuid, 'slug': ou.slug, 'name': ou.name}


def test_role_with_service_export_json(db):
    service = Service.objects.create(name='service name', slug='service-name')
    role = Role.objects.create(name='some role', service=service)
    role_dict = role.export_json()
    default_ou = get_default_ou()
    assert role_dict['service'] == {
        'slug': service.slug,
        'ou': {'name': 'Default organizational unit', 'slug': 'default', 'uuid': default_ou.uuid},
    }


def test_role_with_service_with_ou_export_json(db):
    ou = OU.objects.create(name='ou', slug='ou')
    service = Service.objects.create(name='service name', slug='service-name', ou=ou)
    role = Role.objects.create(name='some role', service=service)
    role_dict = role.export_json()
    assert role_dict['service'] == {'slug': service.slug, 'ou': {'uuid': ou.uuid, 'slug': 'ou', 'name': 'ou'}}


def test_role_with_parents_export_json(db):
    grand_parent_role = Role.objects.create(name='test grand parent role', slug='test-grand-parent-role')
    parent_1_role = Role.objects.create(name='test parent 1 role', slug='test-parent-1-role')
    parent_1_role.add_parent(grand_parent_role)
    parent_2_role = Role.objects.create(name='test parent 2 role', slug='test-parent-2-role')
    parent_2_role.add_parent(grand_parent_role)
    child_role = Role.objects.create(name='test child role', slug='test-child-role')
    child_role.add_parent(parent_1_role)
    child_role.add_parent(parent_2_role)

    child_role_dict = child_role.export_json(parents=True)
    assert child_role_dict['slug'] == child_role.slug
    parents = child_role_dict['parents']
    assert len(parents) == 2
    expected_slugs = {parent_1_role.slug, parent_2_role.slug}
    for parent in parents:
        assert parent['slug'] in expected_slugs
        expected_slugs.remove(parent['slug'])

    grand_parent_role_dict = grand_parent_role.export_json(parents=True)
    assert grand_parent_role_dict['slug'] == grand_parent_role.slug
    assert 'parents' not in grand_parent_role_dict

    parent_1_role_dict = parent_1_role.export_json(parents=True)
    assert parent_1_role_dict['slug'] == parent_1_role.slug
    parents = parent_1_role_dict['parents']
    assert len(parents) == 1
    assert parents[0]['slug'] == grand_parent_role.slug

    parent_2_role_dict = parent_2_role.export_json(parents=True)
    assert parent_2_role_dict['slug'] == parent_2_role.slug
    parents = parent_2_role_dict['parents']
    assert len(parents) == 1
    assert parents[0]['slug'] == grand_parent_role.slug


def test_role_with_permission_export_json(db):
    some_ou = OU.objects.create(name='some ou', slug='some-ou')
    role = Role.objects.create(name='role name', slug='role-slug')
    other_role = Role.objects.create(
        name='other role name', slug='other-role-slug', uuid=get_hex_uuid(), ou=some_ou
    )
    ou = OU.objects.create(name='basic ou', slug='basic-ou', description='basic ou description')
    op = Operation.objects.get(slug='add')
    perm_saml = Permission.objects.create(
        operation=op,
        ou=ou,
        target_ct=ContentType.objects.get_for_model(ContentType),
        target_id=ContentType.objects.get(app_label='saml', model='libertyprovider').pk,
    )
    role.permissions.add(perm_saml)
    perm_role = Permission.objects.create(
        operation=op, ou=None, target_ct=ContentType.objects.get_for_model(Role), target_id=other_role.pk
    )
    role.permissions.add(perm_role)

    export = role.export_json(permissions=True)
    permissions = export['permissions']
    assert len(permissions) == 2
    assert permissions[0] == {
        'operation': {'slug': 'add'},
        'ou': {'uuid': ou.uuid, 'slug': ou.slug, 'name': ou.name},
        'target_ct': {'app_label': 'contenttypes', 'model': 'contenttype'},
        'target': {'model': 'libertyprovider', 'app_label': 'saml'},
    }
    assert permissions[1] == {
        'operation': {'slug': 'add'},
        'ou': None,
        'target_ct': {'app_label': 'a2_rbac', 'model': 'role'},
        'target': {
            'slug': 'other-role-slug',
            'service': None,
            'uuid': other_role.uuid,
            'ou': {'slug': 'some-ou', 'uuid': some_ou.uuid, 'name': 'some ou'},
            'name': 'other role name',
        },
    }


def test_ou_export_json(db):
    ou = OU.objects.create(
        name='basic ou',
        slug='basic-ou',
        description='basic ou description',
        username_is_unique=True,
        email_is_unique=True,
        phone_is_unique=True,
        default=False,
        validate_emails=True,
    )
    ou_dict = ou.export_json()
    assert ou_dict['name'] == ou.name
    assert ou_dict['slug'] == ou.slug
    assert ou_dict['uuid'] == ou.uuid
    assert ou_dict['description'] == ou.description
    assert ou_dict['username_is_unique'] == ou.username_is_unique
    assert ou_dict['email_is_unique'] == ou.email_is_unique
    assert ou_dict['phone_is_unique'] == ou.phone_is_unique
    assert ou_dict['default'] == ou.default
    assert ou_dict['validate_emails'] == ou.validate_emails


def test_admin_cleanup(db):
    r1 = Role.objects.create(name='r1')

    assert Role.objects.filter(name__contains='r1', admin_scope_ct_id__isnull=False).count() == 0

    r1.get_admin_role()

    assert Role.objects.filter(name__contains='r1', admin_scope_ct_id__isnull=False).count() == 1

    r1.delete()

    assert Role.objects.filter(name__contains='r1', admin_scope_ct_id__isnull=False).count() == 0


def test_admin_cleanup_bulk_delete(db):
    r1 = Role.objects.create(name='r1')

    assert Role.objects.filter(name__contains='r1', admin_scope_ct_id__isnull=False).count() == 0

    r1.get_admin_role()

    assert Role.objects.filter(name__contains='r1', admin_scope_ct_id__isnull=False).count() == 1

    Role.objects.filter(name='r1').delete()

    assert Role.objects.filter(name__contains='r1', admin_scope_ct_id__isnull=False).count() == 0


def test_admin_cleanup_failure(db):
    Role.objects.create(
        name='manager of r1',
        admin_scope_ct=ContentType.objects.get_for_model(Permission),
        admin_scope_id=9999,
    )

    assert Role.objects.filter(name__contains='r1', admin_scope_ct_id__isnull=False).count() == 1

    Role.objects.cleanup()

    assert Role.objects.filter(name__contains='r1', admin_scope_ct_id__isnull=False).count() == 0


def test_admin_cleanup_command(db):
    Role.objects.create(
        name='manager of r1',
        admin_scope_ct=ContentType.objects.get_for_model(Permission),
        admin_scope_id=9999,
    )

    assert Role.objects.filter(name__contains='r1', admin_scope_ct_id__isnull=False).count() == 1

    call_command('cleanupauthentic')

    assert Role.objects.filter(name__contains='r1', admin_scope_ct_id__isnull=False).count() == 0


def test_role_rename(db):
    r1 = Role.objects.create(name='r1')
    assert r1.slug == 'r1'

    assert Role.objects.filter(name__contains='r1', admin_scope_ct_id__isnull=False).count() == 0

    assert not r1.get_admin_role(create=False)

    assert Role.objects.filter(name__contains='r1', admin_scope_ct_id__isnull=False).count() == 0

    ar1 = r1.get_admin_role()

    assert ar1
    assert ar1.name == 'Managers of role "r1"'
    assert ar1.slug == '_a2-managers-of-role-r1'
    assert Role.objects.filter(name__contains='r1', admin_scope_ct_id__isnull=False).count() == 1

    assert ar1.name == 'Managers of role "r1"'

    Role.objects.filter(pk=r1.pk).update(name='r1bis')

    r1.refresh_from_db()
    ar1.refresh_from_db()

    assert r1.name == 'r1bis'
    assert ar1.name == 'Managers of role "r1"'

    ar1 = r1.get_admin_role(create=False)

    assert ar1.name == 'Managers of role "r1bis"'
    assert ar1.slug == '_a2-managers-of-role-r1bis'

    r1.name = 'r1ter'
    r1.save()
    ar1.refresh_from_db()

    assert ar1.name == 'Managers of role "r1ter"'
    assert ar1.slug == '_a2-managers-of-role-r1ter'


def test_admin_role_user_view(db, settings, app, admin, simple_user, ou1, user_ou1, role_ou1):
    role_ou1.get_admin_role().members.add(simple_user)

    # Default: only OU users are visible
    response = login(app, simple_user, '/manage/roles/')
    response = response.click('role_ou1')
    select2_json = request_select2(app, response)
    assert select2_json['more'] is False
    user_ids = {int(x['id'].split('-')[1]) for x in select2_json['results'] if x['id'].startswith('user')}
    assert user_ids == {user_ou1.id}
    # add user to OU
    admin.ou = ou1
    admin.save()
    select2_json = request_select2(app, response)
    user_ids = {int(x['id'].split('-')[1]) for x in select2_json['results'] if x['id'].startswith('user')}
    # it must be visible
    assert user_ids == {user_ou1.id, admin.id}


def test_no_managed_ct(transactional_db, settings):
    from django.core.management.sql import emit_post_migrate_signal

    call_command('flush', verbosity=0, interactive=False, database='default', reset_sequences=False)
    assert Role.objects.count() == 7
    OU.objects.create(name='OU1', slug='ou1')
    emit_post_migrate_signal(verbosity=0, interactive=False, db='default', created_models=[])
    assert Role.objects.count() == 7 + 6 + 6
    settings.A2_RBAC_MANAGED_CONTENT_TYPES = ()
    call_command('flush', verbosity=0, interactive=False, database='default', reset_sequences=False)
    assert Role.objects.count() == 0
    # create ou
    OU.objects.create(name='OU1', slug='ou1')
    emit_post_migrate_signal(verbosity=0, interactive=False, db='default', created_models=[])
    assert Role.objects.count() == 0


def test_global_manager_roles(db):
    manager = Role.objects.get(ou__isnull=True, slug='_a2-manager')
    ou_manager = Role.objects.get(ou__isnull=True, slug='_a2-manager-of-organizational-units')
    user_manager = Role.objects.get(ou__isnull=True, slug='_a2-manager-of-users')
    role_manager = Role.objects.get(ou__isnull=True, slug='_a2-manager-of-roles')
    service_manager = Role.objects.get(ou__isnull=True, slug='_a2-manager-of-services')
    authenticator_manager = Role.objects.get(ou__isnull=True, slug='_a2-manager-of-authenticators')
    apiclients_manager = Role.objects.get(ou__isnull=True, slug='_a2-manager-of-api-clients')
    assert ou_manager in manager.parents()
    assert user_manager in manager.parents()
    assert role_manager in manager.parents()
    assert service_manager in manager.parents()
    assert authenticator_manager in manager.parents()
    assert apiclients_manager in manager.parents()
    assert manager.parents(include_self=False).count() == 6
    assert Role.objects.count() == 7
    assert OU.objects.count() == 1


def test_manager_roles_multi_ou(db, ou1):
    manager = Role.objects.get(ou__isnull=True, slug='_a2-manager')
    ou_manager = Role.objects.get(ou__isnull=True, slug='_a2-manager-of-organizational-units')
    user_manager = Role.objects.get(ou__isnull=True, slug='_a2-manager-of-users')
    role_manager = Role.objects.get(ou__isnull=True, slug='_a2-manager-of-roles')
    service_manager = Role.objects.get(ou__isnull=True, slug='_a2-manager-of-services')
    authenticator_manager = Role.objects.get(ou__isnull=True, slug='_a2-manager-of-authenticators')
    apiclients_manager = Role.objects.get(ou__isnull=True, slug='_a2-manager-of-api-clients')
    assert ou_manager in manager.parents()
    assert user_manager in manager.parents()
    assert role_manager in manager.parents()
    assert service_manager in manager.parents()
    assert authenticator_manager in manager.parents()
    assert apiclients_manager in manager.parents()
    assert manager.parents(include_self=False).count() == 6

    for ou in [get_default_ou(), ou1]:
        manager = Role.objects.get(ou__isnull=True, slug=f'_a2-managers-of-{ou.slug}')
        user_manager = Role.objects.get(ou=ou, slug=f'_a2-manager-of-users-{ou.slug}')
        role_manager = Role.objects.get(ou=ou, slug=f'_a2-manager-of-roles-{ou.slug}')
        service_manager = Role.objects.get(ou=ou, slug=f'_a2-manager-of-services-{ou.slug}')
        authenticator_manager = Role.objects.get(ou=ou, slug=f'_a2-manager-of-authenticators-{ou.slug}')
        apiclients_manager = Role.objects.get(ou=ou, slug=f'_a2-manager-of-api-clients-{ou.slug}')

        assert user_manager in manager.parents()
        assert role_manager in manager.parents()
        assert service_manager in manager.parents()
        assert authenticator_manager in manager.parents()
        assert apiclients_manager in manager.parents()
        assert manager.parents(include_self=False).count() == 5

    # 7 global roles and 6 ou roles for both ous
    assert Role.objects.count() == 7 + 6 + 6


@pytest.mark.parametrize(
    'alert,deletion', [(-1, 31), (31, -1), (0, 31), (31, 0), (None, 31), (31, None), (32, 31)]
)
def test_unused_account_settings_validation(db, ou1, alert, deletion):
    ou1.clean_unused_accounts_alert = alert
    ou1.clean_unused_accounts_deletion = deletion
    with pytest.raises(ValidationError):
        ou1.full_clean()


def test_update_content_types_roles(transactional_db, simple_user):
    from django.core.management.sql import emit_post_migrate_signal

    role = Role.objects.get(name='Manager of users')

    # for the purpose of this test, remove admin perm that inherit manage_authorizations perm
    admin_perm = [x for x in role.permissions.all() if x.operation.name == 'Management'][0]
    role.permissions.remove(admin_perm)

    # 'Manager of users' role gives manage_authorizations perm
    manage_authorizations_perm = [
        x for x in role.permissions.all() if x.operation.name == 'Manage service consents'
    ][0]
    assert manage_authorizations_perm
    simple_user.roles.add(role)
    assert simple_user.has_perm('custom_user.manage_authorizations_user')

    def update_user_permissions():
        del simple_user._rbac_perms_cache
        simple_user.roles.add(role)

    # remove manage_authorizations perm
    manage_authorizations_perm.delete()
    assert not [x for x in role.permissions.all() if x.operation.name == 'Manage service consents']
    update_user_permissions()
    assert not simple_user.has_perm('custom_user.manage_authorizations_user')
    assert not [x for x in simple_user.get_all_permissions() if x == 'custom_user.manage_authorizations_user']

    # manage_authorizations perm is (re-)created on post migrate
    emit_post_migrate_signal(verbosity=0, interactive=False, db='default', created_models=[])
    assert [x for x in role.permissions.all() if x.operation.name == 'Manage service consents'][0]
    # and added to 'Manager of users' role
    update_user_permissions()
    assert simple_user.has_perm('custom_user.manage_authorizations_user')
    assert [x for x in simple_user.get_all_permissions() if x == 'custom_user.manage_authorizations_user']


@pytest.mark.parametrize('new_perm_exists', [True, False])
def test_update_self_admin_perm_migration(migration, new_perm_exists):
    old_apps = migration.before([('a2_rbac', '0022_auto_20200402_1101')])
    Role = old_apps.get_model('a2_rbac', 'Role')
    old_apps.get_model('a2_rbac', 'OrganizationalUnit')
    Permission = old_apps.get_model('a2_rbac', 'Permission')
    Operation = old_apps.get_model('django_rbac', 'Operation')
    ContentType = old_apps.get_model('contenttypes', 'ContentType')
    ct = ContentType.objects.get_for_model(Role)
    change_op, _ = Operation.objects.get_or_create(slug=CHANGE_OP.slug)
    manage_members_op, _ = Operation.objects.get_or_create(slug=MANAGE_MEMBERS_OP.slug)

    # add old self administration
    role = Role.objects.create(name='name', slug='slug')
    self_perm, _ = Permission.objects.get_or_create(operation=change_op, target_ct=ct, target_id=role.pk)
    role.permissions.add(self_perm)

    if new_perm_exists:
        new_self_perm, _ = Permission.objects.get_or_create(
            operation=manage_members_op, target_ct=ct, target_id=role.pk
        )
    else:
        Permission.objects.filter(operation=manage_members_op, target_ct=ct, target_id=role.pk).delete()

    new_apps = migration.apply([('a2_rbac', '0024_fix_self_admin_perm')])
    Role = new_apps.get_model('a2_rbac', 'Role')
    Operation = old_apps.get_model('django_rbac', 'Operation')

    role = Role.objects.get(slug='slug')
    assert role.permissions.count() == 1

    perm = role.permissions.first()
    assert perm.operation.pk == manage_members_op.pk
    assert perm.target_ct.pk == ct.pk
    assert perm.target_id == role.pk

    if new_perm_exists:
        assert perm.pk == new_self_perm.pk

    assert not Permission.objects.filter(operation=change_op, target_ct=ct, target_id=role.pk).exists()


class TestRole:
    @scoped_db_fixture(scope='class')
    def fixture(self):
        class Namespace:
            role_ct = ContentType.objects.get_for_model(Role)
            ct_ct = ContentType.objects.get_for_model(ContentType)
            role = Role.objects.create(name='role')
            parent = Role.objects.create(name='parent')
            child = Role.objects.create(name='child')
            role.add_parent(parent)
            role.add_child(child)

        return Namespace

    class TestAddPermission:
        def test_string_on_model(self, db, fixture):
            qs = fixture.role.permissions.filter(
                operation__slug='change',
                target_ct=fixture.ct_ct,
                target_id=fixture.role_ct.id,
                ou__isnull=True,
            )

            assert not qs.exists()
            fixture.role.add_permission(Role, 'change')
            assert qs.exists()

        def test_string_on_model_with_ou(self, db, fixture):
            ou = get_default_ou()
            qs = fixture.role.permissions.filter(
                operation__slug='change', target_ct=fixture.ct_ct, target_id=fixture.role_ct.id, ou=ou
            )

            assert not qs.exists()
            fixture.role.add_permission(Role, 'change', ou=ou)
            assert qs.exists()

        def test_tpl_on_model(self, db, fixture):
            qs = fixture.role.permissions.filter(
                operation__slug='change',
                target_ct=fixture.ct_ct,
                target_id=fixture.role_ct.id,
                ou__isnull=True,
            )

            assert not qs.exists()
            fixture.role.add_permission(Role, CHANGE_OP)
            assert qs.exists()

        def test_string_on_instance(self, db, fixture):
            qs = fixture.role.permissions.filter(
                operation__slug='change',
                target_ct=fixture.role_ct,
                target_id=fixture.role.pk,
                ou__isnull=True,
            )

            assert not qs.exists()
            fixture.role.add_permission(fixture.role, 'change')
            assert qs.exists()

        def test_value_error(self):
            role = Role(name='role')
            with pytest.raises(ValueError):
                role.add_permission('coin', 'change')

    class TestRemovePermission:
        def test_string_on_model(self, db, fixture):
            operation = Operation.objects.get(slug='change')
            ou = get_default_ou()
            fixture.role.permissions.create(
                operation=operation, target_ct=fixture.ct_ct, target_id=fixture.role_ct.pk, ou=ou
            )
            fixture.role.permissions.create(
                operation=operation, target_ct=fixture.ct_ct, target_id=fixture.role_ct.pk, ou=None
            )
            qs = fixture.role.permissions.filter(
                operation__slug='change', target_ct=fixture.ct_ct, target_id=fixture.role_ct.id
            )

            assert qs.filter(ou__isnull=True).exists()
            assert qs.filter(ou__isnull=False).exists()
            fixture.role.remove_permission(Role, 'change')
            assert not qs.filter(ou__isnull=True).exists()
            assert qs.filter(ou__isnull=False).exists()
            fixture.role.remove_permission(Role, 'change', ou=ou)
            assert not qs.filter(ou__isnull=True).exists()
            assert not qs.filter(ou__isnull=False).exists()

        def test_value_error(self, db):
            role = Role(name='role')
            with pytest.raises(ValueError):
                role.remove_permission('coin', 'change')

    class TestParents:
        def test_annotate_direct_assertion(self, db, fixture):
            with pytest.raises(AssertionError):
                fixture.role.parents(annotate=True, direct=True)
            fixture.role.parents(annotate=True, direct=None)
            fixture.role.parents(annotate=True, direct=False)
            fixture.role.parents(annotate=False, direct=True)
            fixture.role.parents(annotate=False, direct=True)
            fixture.role.parents(annotate=False, direct=None)
            fixture.role.parents(annotate=False)

        def test_direct(self, db, fixture):
            assert set(fixture.role.parents(direct=True)) == {fixture.role, fixture.parent}
            assert fixture.role.parents(include_self=False, direct=True).get() == fixture.parent

    class TestChildren:
        def test_annotate_direct_assertion(self, db, fixture):
            with pytest.raises(AssertionError):
                fixture.role.children(annotate=True, direct=True)
            fixture.role.children(annotate=True, direct=None)
            fixture.role.children(annotate=True, direct=False)
            fixture.role.children(annotate=False, direct=True)
            fixture.role.children(annotate=False, direct=True)
            fixture.role.children(annotate=False, direct=None)
            fixture.role.children(annotate=False)

        def test_direct(self, db, fixture):
            assert set(fixture.role.children(direct=True)) == {fixture.role, fixture.child}
            assert fixture.role.children(include_self=False, direct=True).get() == fixture.child

    class TestQueryset:
        def test_filter_admin_roles(self, db):
            admin_role = Role.objects.create(name='Role1').get_admin_role()
            qs = Role.objects.filter_admin_roles()
            assert all(role.slug.startswith('_a2-manager') for role in qs)
            assert admin_role in qs

        def test_exclude_admin_roles(self, db):
            role = Role.objects.create(name='Role1')
            role.get_admin_role()
            qs = Role.objects.exclude_admin_roles()
            assert set(qs) == {role}

        def test_filter_internal_roles(self, db):
            role = Role.objects.create(name='Role1')
            roles = set(Role.objects.all())
            qs = Role.objects.filter_internal_roles()
            assert all(role.slug.startswith('_') for role in qs)
            assert set(qs) == (roles - {role})

        def test_exclude_internal_roles(self, db):
            role = Role.objects.create(name='Role1')
            qs = Role.objects.exclude_internal_roles()
            assert set(qs) == {role}

        def test_filter_by_text_query(self, db):
            service_administratif = Role.objects.create(name='Service administratif')
            service_du_personnel = Role.objects.create(name='Service du personnel')
            qs = Role.objects.all()
            assert set(qs.filter_by_text_query('adminstratif')) == {service_administratif}
            assert set(qs.filter_by_text_query('person')) == {service_du_personnel}
            roles = qs.filter_by_text_query('service du')
            assert list(roles)[:2] == [service_du_personnel, service_administratif]
            assert roles[0].dist == 0  # 'service du' is a sub sentence of service_du_personnel's name
            assert roles[0].dist < roles[1].dist


def test_a2_rbac_operation_migration(migration, settings):
    migrate_from = [
        ('a2_rbac', '0030_organizationalunit_min_password_strength'),
        ('django_rbac', '0009_auto_20221004_1343'),
    ]
    migrate_to = [('a2_rbac', '0033_remove_old_operation_fk')]

    old_apps = migration.before(migrate_from)
    ContentType = old_apps.get_model('contenttypes', 'ContentType')
    Operation = old_apps.get_model('django_rbac', 'Operation')
    Permission = old_apps.get_model('a2_rbac', 'Permission')

    # check objects created by signal handlers
    base_operation = Operation.objects.get(slug='search')
    base_permission = Permission.objects.filter(operation=base_operation).first()

    # check other objects
    new_operation = Operation.objects.create(slug='test')
    Permission.objects.create(
        operation=new_operation,
        target_ct=ContentType.objects.get_for_model(ContentType),
        target_id=ContentType.objects.get_for_model(User).pk,
    )

    new_apps = migration.apply(migrate_to)
    ContentType = new_apps.get_model('contenttypes', 'ContentType')
    Operation = new_apps.get_model('a2_rbac', 'Operation')
    Permission = new_apps.get_model('a2_rbac', 'Permission')

    base_operation = Operation.objects.get(slug='search')
    assert (
        Permission.objects.filter(
            operation_id=base_operation,
            target_ct_id=base_permission.target_ct.pk,
            target_id=base_permission.target_id,
        ).count()
        == 1
    )

    new_operation = Operation.objects.get(slug=new_operation.slug)
    assert (
        Permission.objects.filter(
            operation=new_operation,
            target_ct=ContentType.objects.get_for_model(ContentType),
            target_id=ContentType.objects.get_for_model(User).pk,
        ).count()
        == 1
    )


def test_a2_rbac_role_attribute_migration(migration, settings):
    migrate_from = [('a2_rbac', '0034_new_role_fields')]
    migrate_to = [('a2_rbac', '0036_delete_roleattribute')]

    old_apps = migration.before(migrate_from)
    Role = old_apps.get_model('a2_rbac', 'Role')
    RoleAttribute = old_apps.get_model('a2_rbac', 'RoleAttribute')

    role = Role.objects.create(name='role', slug='1')
    RoleAttribute.objects.create(role=role, kind='json', name='details', value='"abc"')
    RoleAttribute.objects.create(role=role, kind='json', name='emails', value='["a@a.com", "b@b.com"]')
    RoleAttribute.objects.create(role=role, kind='json', name='emails_to_members', value='false')
    RoleAttribute.objects.create(role=role, kind='string', name='is_superuser', value='true')

    role = Role.objects.create(name='role_default_values', slug='2')
    RoleAttribute.objects.create(role=role, kind='json', name='details', value='""')
    RoleAttribute.objects.create(role=role, kind='json', name='emails', value='[]')
    RoleAttribute.objects.create(role=role, kind='json', name='emails_to_members', value='true')
    RoleAttribute.objects.create(role=role, kind='string', name='is_superuser', value='false')

    role = Role.objects.create(name='role_no_attribute', slug='3')

    role = Role.objects.create(name='role_bad_attributes', slug='4')
    RoleAttribute.objects.create(role=role, kind='json', name='details', value='bad')
    RoleAttribute.objects.create(role=role, kind='json', name='emails', value='true')
    RoleAttribute.objects.create(role=role, kind='json', name='emails_to_members', value='bad')
    RoleAttribute.objects.create(role=role, kind='string', name='unknown', value='xxx')

    role = Role.objects.create(name='role_one_attribute', slug='5')
    RoleAttribute.objects.create(role=role, kind='json', name='details', value='"xxx"')

    new_apps = migration.apply(migrate_to)
    Role = new_apps.get_model('a2_rbac', 'Role')

    role = Role.objects.get(name='role')
    assert role.details == 'abc'
    assert role.emails == ['a@a.com', 'b@b.com']
    assert role.emails_to_members is False
    assert role.is_superuser is True

    role = Role.objects.get(name='role_default_values')
    assert role.details == ''
    assert role.emails == []
    assert role.emails_to_members is True
    assert role.is_superuser is False

    role = Role.objects.get(name='role_no_attribute')
    assert role.details == ''
    assert role.emails == []
    assert role.emails_to_members is True
    assert role.is_superuser is False

    role = Role.objects.get(name='role_bad_attributes')
    assert role.details == ''
    assert role.emails == []
    assert role.emails_to_members is True
    assert role.is_superuser is False

    role = Role.objects.get(name='role_one_attribute')
    assert role.details == 'xxx'
    assert role.emails == []
    assert role.emails_to_members is True
    assert role.is_superuser is False


def test_a2_rbac_0040_migration_update_admin_roles_permission(migration, settings):
    migrate_from = [('a2_rbac', '0040_role_name_idx')]
    migrate_to = [('a2_rbac', '0041_update_role_administration_permissions')]

    old_apps = migration.before(migrate_from)

    ContentType = old_apps.get_model('contenttypes', 'ContentType')
    Operation = old_apps.get_model('a2_rbac', 'Operation')
    OrganizationalUnit = old_apps.get_model('a2_rbac', 'OrganizationalUnit')
    Permission = old_apps.get_model('a2_rbac', 'Permission')
    Role = old_apps.get_model('a2_rbac', 'Role')
    User = old_apps.get_model('custom_user', 'User')

    watched_roles = []

    view_operation, _ = Operation.objects.get_or_create(slug='view')

    role = Role.objects.create(slug='main-role', ou=None)
    view_user_perm, _ = Permission.objects.get_or_create(
        operation=view_operation,
        target_ct=ContentType.objects.get_for_model(ContentType),
        target_id=ContentType.objects.get_for_model(User).pk,
        ou__isnull=True,
        ou=None,
    )
    admin_role = Role.objects.create(
        admin_scope_ct=ContentType.objects.get_for_model(Role),
        admin_scope_id=role.id,
        ou=None,
        slug=f'admin-role-{role.slug}',
    )
    admin_role.permissions.add(view_user_perm)
    admin_role.save()

    watched_roles.append(admin_role.id)

    for i in range(4):
        ou = OrganizationalUnit.objects.create(slug=f'ou-{i}', name=f'OU {i}')
        role = Role.objects.create(slug=f'role-{i}', name=f'Role {i}', ou=ou)
        view_user_perm, _ = Permission.objects.get_or_create(
            operation=view_operation,
            target_ct=ContentType.objects.get_for_model(ContentType),
            target_id=ContentType.objects.get_for_model(User).pk,
            ou__isnull=ou is None,
            ou=ou,
        )
        admin_role = Role.objects.create(
            admin_scope_ct=ContentType.objects.get_for_model(Role),
            admin_scope_id=role.id,
            ou=role.ou,
            slug=f'admin-role-{role.slug}',
        )
        admin_role.permissions.add(view_user_perm)
        admin_role.save()
        watched_roles.append(admin_role.id)

    new_apps = migration.apply(migrate_to)

    ContentType = new_apps.get_model('contenttypes', 'ContentType')
    Operation = new_apps.get_model('a2_rbac', 'Operation')
    Permission = new_apps.get_model('a2_rbac', 'Permission')
    Role = new_apps.get_model('a2_rbac', 'Role')
    User = new_apps.get_model('custom_user', 'User')

    view_operation, _ = Operation.objects.get_or_create(slug='view')
    search_operation, _ = Operation.objects.get_or_create(slug='search')

    for role in Role.objects.filter(id__in=watched_roles):
        view_user_perm, _ = Permission.objects.get_or_create(
            operation=view_operation,
            target_ct=ContentType.objects.get_for_model(ContentType),
            target_id=ContentType.objects.get_for_model(User).pk,
            ou__isnull=role.ou is None,
            ou=role.ou,
        )
        search_user_perm, _ = Permission.objects.get_or_create(
            operation=search_operation,
            target_ct=ContentType.objects.get_for_model(ContentType),
            target_id=ContentType.objects.get_for_model(User).pk,
            ou__isnull=role.ou is None,
            ou=role.ou,
        )
        assert role not in view_user_perm.roles.all()
        assert role in search_user_perm.roles.all()
