# authentic2 - versatile identity manager
# Copyright (C) 20050-2020 Entr'ouvert
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
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db import connection
from django.db.models import Q
from django.test.utils import CaptureQueriesContext

from authentic2.a2_rbac.models import Operation, OrganizationalUnit, Permission, Role, RoleParenting
from authentic2.custom_user import backends

User = get_user_model()


def test_role_parenting(db):
    ou = OrganizationalUnit.objects.create(name='ou')
    roles = []
    for i in range(10):
        roles.append(Role.objects.create(name='test-role-%d' % i, ou=ou))

    assert Role.objects.filter(name__startswith='test-role-').count() == 10
    role_parenting_qs = RoleParenting.objects.filter(Q(parent__in=roles) | Q(child__in=roles))
    assert role_parenting_qs.count() == 0
    for i in range(1, 3):
        RoleParenting.objects.soft_create(parent=roles[i - 1], child=roles[i])
    assert role_parenting_qs.filter(direct=True).count() == 2
    assert role_parenting_qs.filter(direct=False).count() == 1
    for i, role in enumerate(roles[:3]):
        assert role.children().count() == 3 - i
        assert role.parents().count() == i + 1
        assert role.children(False).count() == 3 - i - 1
        assert role.parents(False).count() == i

    for i in range(4, 6):
        role_parenting_qs.create(parent=roles[i - 1], child=roles[i])
    assert role_parenting_qs.filter(direct=True).count() == 4
    assert role_parenting_qs.filter(direct=False).count() == 2
    for i, role in enumerate(roles[3:6]):
        assert role.children().count() == 3 - i
        assert role.parents().count() == i + 1
        assert role.children(False).count() == 3 - i - 1
        assert role.parents(False).count() == i
    RoleParenting.objects.soft_create(parent=roles[2], child=roles[3])
    assert role_parenting_qs.filter(direct=True).count() == 5
    assert role_parenting_qs.filter(direct=False).count() == 10
    for i in range(6):
        assert roles[i].parents().distinct().count() == i + 1
    for i, role in enumerate(roles[:6]):
        assert role.children().count() == 6 - i
        assert role.parents().count() == i + 1
        assert role.children(False).count() == 6 - i - 1
        assert role.parents(False).count() == i
    RoleParenting.objects.soft_delete(roles[2], roles[3])
    assert (
        role_parenting_qs.filter(
            direct=True,
            deleted__isnull=True,
        ).count()
        == 4
    )
    assert (
        role_parenting_qs.filter(
            direct=False,
            deleted__isnull=True,
        ).count()
        == 2
    )
    # test that it works with cycles
    RoleParenting.objects.soft_create(parent=roles[2], child=roles[3])
    RoleParenting.objects.soft_create(parent=roles[5], child=roles[0])
    for role in roles[:6]:
        assert role.children().count() == 6
        assert role.parents().count() == 6


def test_role_parenting_soft_delete_children(db):
    ou = OrganizationalUnit.objects.create(name='ou')
    roles = []
    for i in range(10):
        roles.append(Role.objects.create(name='r%d' % i, ou=ou))
    role_parenting_qs = RoleParenting.objects.filter(Q(parent__in=roles) | Q(child__in=roles))
    assert not role_parenting_qs.exists()

    rps = []
    for i in range(5):
        rps.append(RoleParenting.objects.soft_create(parent=roles[9 - i], child=roles[i]))
    assert len(role_parenting_qs.all()) == 5
    for i in range(5):
        roles[9 - i].remove_child(roles[i])
        assert len(role_parenting_qs.all()) == 5
        assert len(role_parenting_qs.filter(deleted__isnull=True).all()) == 4 - i
    for i in range(5):
        roles[9 - i].add_child(roles[i])
        assert len(role_parenting_qs.all()) == 5
        assert len(role_parenting_qs.filter(deleted__isnull=True).all()) == i + 1


def test_role_parenting_soft_delete_parents(db):
    ou = OrganizationalUnit.objects.create(name='ou')
    roles = []
    for i in range(10):
        roles.append(Role.objects.create(name='r%d' % i, ou=ou))
    role_parenting_qs = RoleParenting.objects.filter(Q(parent__in=roles) | Q(child__in=roles))
    assert not role_parenting_qs.exists()

    rps = []
    for i in range(5):
        rps.append(RoleParenting.objects.soft_create(child=roles[9 - i], parent=roles[i]))
    assert len(role_parenting_qs.all()) == 5
    for i in range(5):
        roles[9 - i].remove_parent(roles[i])
        assert len(role_parenting_qs.all()) == 5
        assert len(role_parenting_qs.filter(deleted__isnull=True).all()) == 4 - i
    for i in range(5):
        roles[9 - i].add_parent(roles[i])
        assert len(role_parenting_qs.all()) == 5
        assert len(role_parenting_qs.filter(deleted__isnull=True).all()) == i + 1


SIZE = 50
SPAN = 10


def test_massive_role_parenting(db):
    Role.objects.all().delete()

    user = User.objects.create(username='user')
    roles = []
    # Try a depth=10 tree of roles
    for i in range(0, SIZE):
        name = 'role%s' % i
        roles.append(Role(pk=i + 1, name=name, slug=name))
    Role.objects.bulk_create(roles)
    relations = []
    for i in range(0, SIZE):
        if not i:
            continue
        relations.append(RoleParenting(parent=roles[i], child=roles[(i - 1) // SPAN]))
    RoleParenting.objects.bulk_create(relations)
    RoleParenting.objects.update_transitive_closure()
    operation, _ = Operation.objects.get_or_create(slug='admin')
    perm, _ = Permission.objects.get_or_create(
        operation=operation,
        target_ct=ContentType.objects.get_for_model(ContentType),
        target_id=ContentType.objects.get_for_model(User).id,
    )
    roles[0].members.add(user)
    Role.objects.get(pk=roles[-1].pk).permissions.add(perm)
    for i in range(SIZE):
        assert Operation.objects.has_perm(user, 'admin', User)
    for i in range(SIZE):
        assert list(Role.objects.for_user(user).order_by('pk')) == list(Role.objects.order_by('pk'))


def test_rbac_backend(db):
    ou1 = OrganizationalUnit.objects.create(name='ou1', slug='ou1')
    ou2 = OrganizationalUnit.objects.create(name='ou2', slug='ou2')
    user1 = User.objects.create(username='john.doe')
    ct_ct = ContentType.objects.get_for_model(ContentType)
    role_ct = ContentType.objects.get_for_model(Role)
    change_op = Operation.objects.get(slug='change')
    view_op = Operation.objects.get(slug='view')
    delete_op = Operation.objects.get(slug='delete')
    add_op = Operation.objects.get(slug='add')
    admin_op = Operation.objects.get(slug='admin')
    perm1 = Permission.objects.create(operation=change_op, target_ct=ct_ct, target_id=role_ct.pk)
    perm2 = Permission.objects.create(operation=view_op, target_ct=ct_ct, target_id=role_ct.pk)
    Role.objects.all().delete()
    role1 = Role.objects.create(name='role1')
    role2 = Role.objects.create(name='role2', ou=ou1)
    role1.permissions.add(perm1)
    role2.permissions.add(perm2)
    role1.add_child(role2)
    role2.members.add(user1)
    perm3 = Permission.objects.create(operation=delete_op, target_ct=role_ct, target_id=role1.pk)
    perm4 = Permission.objects.create(operation=add_op, ou=ou1, target_ct=ct_ct, target_id=role_ct.pk)
    role1.permissions.add(perm3)
    role1.permissions.add(perm4)

    rbac_backend = backends.DjangoRBACBackend()
    ctx = CaptureQueriesContext(connection)
    with ctx:
        assert rbac_backend.get_all_permissions(user1) == {
            'a2_rbac.change_role',
            'a2_rbac.manage_members_role',
            'a2_rbac.search_role',
            'a2_rbac.view_role',
        }
        assert rbac_backend.get_all_permissions(user1, obj=role1) == {
            'a2_rbac.delete_role',
            'a2_rbac.change_role',
            'a2_rbac.manage_members_role',
            'a2_rbac.search_role',
            'a2_rbac.view_role',
        }
        assert rbac_backend.get_all_permissions(user1, obj=role2) == {
            'a2_rbac.change_role',
            'a2_rbac.view_role',
            'a2_rbac.manage_members_role',
            'a2_rbac.search_role',
            'a2_rbac.add_role',
        }
        assert not rbac_backend.has_perm(user1, 'a2_rbac.delete_role', obj=role2)
        assert rbac_backend.has_perm(user1, 'a2_rbac.delete_role', obj=role1)
        assert rbac_backend.has_perms(
            user1, ['a2_rbac.delete_role', 'a2_rbac.change_role', 'a2_rbac.view_role'], obj=role1
        )
        assert rbac_backend.has_module_perms(user1, 'a2_rbac')
        assert not rbac_backend.has_module_perms(user1, 'contenttypes')
    assert len(ctx.captured_queries) == 1
    assert set(rbac_backend.filter_by_perm(user1, 'a2_rbac.add_role', Role.objects.all())) == {role2}
    assert set(rbac_backend.filter_by_perm(user1, 'a2_rbac.delete_role', Role.objects.all())) == {role1}
    assert set(
        rbac_backend.filter_by_perm(user1, ['a2_rbac.delete_role', 'a2_rbac.add_role'], Role.objects.all())
    ) == {role1, role2}
    assert set(rbac_backend.filter_by_perm(user1, 'a2_rbac.view_role', Role.objects.all())) == {
        role1,
        role2,
    }
    assert set(rbac_backend.filter_by_perm(user1, 'a2_rbac.change_role', Role.objects.all())) == {
        role1,
        role2,
    }

    # Test admin op as a generalization of other ops
    user2 = User.objects.create(username='donald.knuth')
    role3 = Role.objects.create(name='role3')
    role3.members.add(user2)
    perm5 = Permission.objects.filter(operation=admin_op, target_ct=ct_ct, target_id=role_ct.pk).first()
    role3.permissions.add(perm5)
    assert rbac_backend.get_all_permissions(user2) == {
        'a2_rbac.activate_role',
        'a2_rbac.add_role',
        'a2_rbac.change_role',
        'a2_rbac.change_email_role',
        'a2_rbac.change_password_role',
        'a2_rbac.search_role',
        'a2_rbac.admin_role',
        'a2_rbac.view_role',
        'a2_rbac.delete_role',
        'a2_rbac.manage_authorizations_role',
        'a2_rbac.manage_members_role',
        'a2_rbac.reset_password_role',
    }

    # test ous_with_perm
    assert set(rbac_backend.ous_with_perm(user1, 'a2_rbac.add_role')) == {ou1}
    assert set(rbac_backend.ous_with_perm(user1, 'a2_rbac.view_role')).issuperset({ou1, ou2})
    assert set(rbac_backend.ous_with_perm(user1, 'a2_rbac.delete_role')) == set()


def test_all_members(db):
    u1 = User.objects.create(username='john.doe')
    u2 = User.objects.create(username='donald.knuth')
    u3 = User.objects.create(username='alan.turing')
    r1 = Role.objects.create(name='r1')
    r1.members.add(u1)
    r1.members.add(u3)
    r2 = Role.objects.create(name='r2')
    r2.members.add(u3)
    r3 = Role.objects.create(name='r3')
    r3.members.add(u2)
    r3.members.add(u3)
    r3.add_parent(r2)
    r2.add_parent(r1)
    for member in r1.all_members():
        if member in (u1, u3):
            assert member.direct == [r1]
        if member == u2:
            assert member.direct == []
    for member in Role.objects.filter(id=r1.id).all_members():
        if member in (u1, u3):
            assert member.direct == [r1]
        if member == u2:
            assert member.direct == []


def test_random_role_parenting(db):
    import random

    import numpy as np

    Role.objects.all().delete()
    c = 15
    roles = [Role.objects.create(id=i, name=f'role{i}') for i in range(c)]
    m = [[False] * c for i in range(c)]
    m = np.zeros((c, c), dtype=bool)

    def check(i):
        one = np.identity(c, dtype=bool)
        z = m
        for i in range(c):
            new_z = np.matmul(z, m | one)
            if np.array_equal(z, new_z):
                break
            z = new_z
        real = np.zeros((c, c), dtype=bool)
        for parent_id, child_id in RoleParenting.objects.filter(deleted__isnull=True).values_list(
            'parent_id', 'child_id'
        ):
            real[parent_id][child_id] = True
        assert np.array_equal(real, z & ~one)

    from time import time

    for i in range(2 * c * c):
        a = random.randint(0, c - 1)
        b = random.randint(0, c - 1)
        if a == b:
            continue
        t = time()
        if random.randint(0, 10) < 8:
            print(f'add {a} <- {b}')
            roles[a].add_child(roles[b])
            m[a][b] = True
        else:
            print(f'remove {a} <- {b}')
            roles[a].remove_child(roles[b])
            m[a][b] = False
        print('duration', time() - t)
        check(i)


class TestInheritance:
    @pytest.fixture
    def role(self, db):
        return Role.objects.create(name='role')

    @pytest.fixture
    def user(self, simple_user, role):
        simple_user.roles.add(role)
        return simple_user

    @pytest.fixture
    def backend(self):
        return backends.DjangoRBACBackend()

    @pytest.fixture
    def oidc_client_ou1(self, ou1):
        from authentic2_idp_oidc.models import OIDCClient

        return OIDCClient.objects.create(ou=ou1, slug='oidclient')

    def test_global(self, role, user, backend):
        role.permissions.add(Permission.from_str('authentic2.admin_service'))
        assert user.has_perm('authentic2_idp_oidc.admin_oidcclient')
        assert user.has_perm('authentic2_idp_oidc.search_oidcclient')

    def test_ou_scoped(self, role, user, backend, ou1, oidc_client_ou1):
        role.permissions.add(Permission.from_str('ou1 authentic2.admin_service'))
        assert user.has_perm('authentic2_idp_oidc.admin_oidcclient', oidc_client_ou1)
        assert user.has_perm('authentic2_idp_oidc.search_oidcclient', oidc_client_ou1)
        assert user.has_perm('authentic2.admin_service', oidc_client_ou1)
        assert user.has_perm('authentic2.search_service', oidc_client_ou1)

    def test_instance_scoped(self, role, user, backend, oidc_client_ou1):
        role.permissions.add(Permission.from_str('authentic2.admin_service', instance=oidc_client_ou1))
        assert user.has_perm('authentic2_idp_oidc.admin_oidcclient', oidc_client_ou1)
        assert user.has_perm('authentic2_idp_oidc.search_oidcclient', oidc_client_ou1)
        assert user.has_perm('authentic2.admin_service', oidc_client_ou1)
        assert user.has_perm('authentic2.search_service', oidc_client_ou1)
