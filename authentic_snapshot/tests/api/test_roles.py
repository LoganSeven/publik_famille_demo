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

import copy
import types

import pytest

from authentic2.a2_rbac.models import Role
from authentic2.a2_rbac.utils import get_default_ou
from authentic2.api_views import OrganizationalUnit as OU
from authentic2.api_views import RoleFiliationSerializer, RoleParentingSerializer
from authentic2.custom_user.models import User
from tests.utils import USER_ATTRIBUTES_SET, assert_event, create_user, scoped_db_fixture

from .utils import AdminTestMixin, UserTestMixin

OU_JSON = {'slug': 'default', 'uuid': '1' * 32, 'name': 'Default organizational unit'}


@scoped_db_fixture(scope='module', autouse=True)
def setup():
    OU.objects.filter(default=True).update(uuid='1' * 32)
    ou = get_default_ou()
    parent = Role.objects.create(name='Parent', uuid='a' * 32, ou=ou)
    role = Role.objects.create(name='Role', uuid='b' * 32, ou=ou)
    child = Role.objects.create(name='Child', uuid='c' * 32, ou=ou)
    parent.add_child(role)
    role.add_child(child)

    user = create_user(username='user', password='user')
    user_role = Role.objects.create(name='User role', uuid='d' * 32, ou=ou)
    user_role.members.add(user)
    admin = create_user(username='admin', password='admin')
    admin.add_role('Manager')

    return types.SimpleNamespace(
        parent=parent, role=role, child=child, user_role=user_role, user=user, admin=admin
    )


def slugs(resp):
    return {result['slug'] for result in resp.json['results']}


@pytest.fixture
def parent(request, setup):
    return copy.deepcopy(setup.parent)


@pytest.fixture
def role(request, setup):
    name = getattr(request, 'param', 'role')
    return copy.deepcopy(getattr(setup, name))


@pytest.fixture
def child(request, setup):
    return copy.deepcopy(setup.child)


@pytest.fixture(params=['by-uuid', 'by-slug', 'by-full-slug'])
def role_ref(request, role):
    if request.param == 'by-uuid':
        return role.uuid
    if request.param == 'by-slug':
        return role.slug
    if request.param == 'by-full-slug':
        return '%s:%s' % (role.ou.slug, role.slug)


@pytest.fixture
def user(setup):
    return copy.deepcopy(setup.user)


@pytest.fixture
def user_role(setup):
    return copy.deepcopy(setup.user_role)


@pytest.fixture
def admin(setup):
    return copy.deepcopy(setup.admin)


ROLE_SERIALIZATION_FIELDS = {'uuid', 'name', 'slug', 'ou'}


class TestSerializer:
    def test_role_parent(self):
        ou = OU(name='OU', slug='ou', uuid='2' * 32)
        role = Role(uuid='1' * 32, name='Role', slug='role', ou=ou)
        role.direct = True
        assert RoleFiliationSerializer(role).data == {
            'uuid': '1' * 32,
            'name': 'Role',
            'slug': 'role',
            'direct': True,
            'ou': 'ou',
        }

    def test_role_parenting(self, db, role):
        assert RoleParentingSerializer(role.child_relation.first()).data == {
            'parent': {'service': None, 'slug': 'role', 'ou': OU_JSON, 'name': 'Role', 'uuid': 'b' * 32},
            'direct': True,
        }


class TestRolesAPI:
    def test_unauthenticated(self, app, role_ref):
        app.get('/api/roles/', status=401)
        app.post('/api/roles/', params={}, status=401)
        app.get(f'/api/roles/{role_ref}/', status=401)
        app.get(f'/api/roles/{role_ref}/', status=401)
        app.delete(f'/api/roles/{role_ref}/', status=401)
        app.patch_json(f'/api/roles/{role_ref}/', params={}, status=401)

    class TestAdmin(AdminTestMixin):
        def test_list(self, app):
            resp = app.get('/api/roles/')
            assert len(resp.json['results'])
            assert all(set(result) == ROLE_SERIALIZATION_FIELDS for result in resp.json['results'])

            resp = app.get('/api/roles/?ou__slug=default')
            assert len(resp.json['results']) == 4

        def test_post(self, app, admin):
            resp = app.post_json(
                '/api/roles/',
                params={
                    'name': 'Coffee Manager',
                    'slug': 'role1',
                    'ou': 'default',
                },
            )
            assert set(resp.json) == ROLE_SERIALIZATION_FIELDS
            assert resp.json['name'] == 'Coffee Manager'
            assert resp.json['slug'] == 'role1'
            assert resp.json['ou'] == 'default'
            assert_event('manager.role.creation', user=admin, api=True, role_name='Coffee Manager')
            assert Role.objects.get(uuid=resp.json['uuid'])

        def test_post_auto_slug(self, app):
            resp = app.post_json(
                '/api/roles/',
                params={
                    'name': 'Coffee Manager',
                    'ou': 'default',
                },
            )
            assert resp.json['slug'] == 'coffee-manager'

        def test_post_auto_ou(self, app):
            resp = app.post_json(
                '/api/roles/',
                params={
                    'name': 'Coffee Manager',
                },
            )
            assert Role.objects.get(uuid=resp.json['uuid']).ou == get_default_ou()

        def test_api_post_get_or_create_slug(self, app, role):
            resp = app.post_json('/api/roles/?get_or_create=slug', params={'name': 'foo', 'slug': 'role'})
            # check name was not modified
            assert resp.json['name'] == 'Role'
            role.refresh_from_db()
            assert role.name == 'Role'

        def test_api_post_get_or_create_name(self, app, role):
            resp = app.post_json('/api/roles/?get_or_create=name', params={'name': 'Role', 'slug': 'foo'})
            # check slug was not modified
            assert resp.json['slug'] == 'role'
            role.refresh_from_db()
            assert role.slug == 'role'

        def test_api_post_update_or_create_slug(self, app, role):
            resp = app.post_json('/api/roles/?update_or_create=slug', params={'name': 'foo', 'slug': 'role'})
            # check name was modified
            assert resp.json['name'] == 'foo'
            role.refresh_from_db()
            assert role.name == 'foo'

        def test_api_post_update_or_create_name(self, app, role):
            resp = app.post_json('/api/roles/?update_or_create=name', params={'name': 'Role', 'slug': 'foo'})
            # check slug was modified
            assert resp.json['slug'] == 'foo'
            role.refresh_from_db()
            assert role.slug == 'foo'

        def test_api_post_get_or_create_multiple_ou(self, app):
            other = Role.objects.create(name='Role', ou=OU.objects.create(name='ou'))
            app.post_json(
                '/api/roles/?update_or_create=slug', params={'name': 'foo', 'slug': 'role'}, status=409
            )
            assert (
                app.post_json(
                    '/api/roles/?get_or_create=slug&get_or_create=ou',
                    params={'name': 'foo', 'slug': 'role', 'ou': 'ou'},
                ).json['uuid']
                == other.uuid
            )

        def test_api_post_update_or_create_multiple_ou(self, app, role):
            other = Role.objects.create(name='Role', ou=OU.objects.create(name='ou'))
            app.post_json(
                '/api/roles/?update_or_create=slug',
                params={'name': 'foo', 'slug': 'role', 'ou': 'ou'},
                status=409,
            )
            resp = app.post_json(
                '/api/roles/?update_or_create=slug&update_or_create=ou',
                params={'name': 'foo', 'slug': 'role', 'ou': 'ou'},
            )
            assert resp.json['uuid'] == other.uuid
            assert resp.json['name'] == 'foo'
            other.refresh_from_db()
            assert other.name == 'foo'
            role.refresh_from_db()
            assert role.name == 'Role'

        def test_get(self, app, role_ref, role):
            resp = app.get(f'/api/roles/{role_ref}/')
            assert set(resp.json) == set(ROLE_SERIALIZATION_FIELDS)
            assert resp.json['uuid'] == role.uuid

        def test_get_by_slug_multiple(self, app, role):
            other = Role.objects.create(name='Role', ou=OU.objects.create(name='ou'))
            app.get('/api/roles/role/', status=409)
            assert app.get('/api/roles/default:role/').json['uuid'] == role.uuid
            assert app.get('/api/roles/ou:role/').json['uuid'] == other.uuid

        def test_delete(self, app, role_ref, role, admin):
            app.delete(f'/api/roles/{role_ref}/')
            assert not Role.objects.filter(pk=role.pk).exists()
            assert_event('manager.role.deletion', user=admin, api=True, role_name='Role')

        def test_patch(self, app, role_ref):
            resp1 = app.get(f'/api/roles/{role_ref}/')
            resp2 = app.patch_json(f'/api/roles/{role_ref}/', params={'name': 'update-role'})
            resp3 = app.get(f'/api/roles/{role_ref}/')
            assert resp2.json == resp3.json
            assert {key for key in resp1.json if resp1.json[key] != resp2.json[key]} == {'name'}

        def test_put(self, app, role_ref):
            resp1 = app.get(f'/api/roles/{role_ref}/')
            resp2 = app.put_json(f'/api/roles/{role_ref}/', params={'name': 'update-role'})
            resp3 = app.get('/api/roles/update-role/')
            assert resp2.json == resp3.json
            # on PUT slug is reset and recomputed
            assert {key for key in resp1.json if resp1.json[key] != resp2.json[key]} == {'name', 'slug'}

        def test_post_name_is_none(self, app):
            app.post_json(
                '/api/roles/',
                params={
                    'name': None,
                },
                status=400,
            )

        class TestFilters:
            def test_admin_true(self, app):
                Role.objects.create(name='Role1').get_admin_role()
                resp = app.get('/api/roles/?admin=true')
                assert slugs(resp) >= {'_a2-managers-of-role-role1'}
                assert all(slug.startswith('_a2-manager') for slug in slugs(resp))

            def test_admin_false(self, app):
                Role.objects.create(name='Role1').get_admin_role()
                resp = app.get('/api/roles/?admin=false')
                assert all(not slug.startswith('_a2-managers-of-role-') for slug in slugs(resp))

            def test_internal_true(self, app):
                resp = app.get('/api/roles/?internal=true')
                assert slugs(resp)
                assert all(slug.startswith('_') for slug in slugs(resp))

            def test_internal_false(self, app):
                resp = app.get('/api/roles/?internal=false')
                assert slugs(resp)
                assert all(not slug.startswith('_') for slug in slugs(resp))

            def test_q(self, app):
                r1 = Role.objects.create(name='Service administratif')
                r2 = Role.objects.create(name='Service du personnel')
                resp = app.get('/api/roles/?q=adminstratif')
                assert slugs(resp) == {r1.slug}
                resp = app.get('/api/roles/?q=person')
                assert slugs(resp) == {r2.slug}
                resp = app.get('/api/roles/?q=sevice')
                assert slugs(resp) == {r1.slug, r2.slug}

    class TestUser(UserTestMixin):
        def test_no_permission(self, app, role_ref):
            app.delete(f'/api/roles/{role_ref}/', status=404)
            app.patch_json(f'/api/roles/{role_ref}/', status=404)

        def test_list(self, app, role_ref, role, user_role):
            assert app.get('/api/roles/').json['results'] == []
            user_role.add_permission(role, 'view')
            assert app.get('/api/roles/').json['results'] != []

        def test_get(self, app, role_ref, role, user_role):
            app.get(f'/api/roles/{role_ref}/', status=404)
            user_role.add_permission(role, 'view')
            app.get(f'/api/roles/{role_ref}/')

        def test_post(self, app, user_role):
            app.post('/api/roles/', params={'name': 'foo'}, status=403)
            user_role.add_permission(Role, 'add')
            app.post('/api/roles/', params={'name': 'foo'})

        def test_delete(self, app, role_ref, role, user_role):
            app.delete(f'/api/roles/{role_ref}/', status=404)
            user_role.add_permission(role, 'view')
            app.delete(f'/api/roles/{role_ref}/', status=403)
            user_role.add_permission(role, 'delete')
            app.delete(f'/api/roles/{role_ref}/')


class TestRolesMembersAPI:
    @scoped_db_fixture(scope='class', autouse=True)
    def setup(self, setup):
        # add user to the role in the middle of the role chain
        # parent -> role -> child
        #         | user  |
        setup.role.members.add(setup.user)
        return setup

    def test_list(self, app, role_ref):
        app.get(f'/api/roles/{role_ref}/members/', status=401)

    class TestAdmin(AdminTestMixin):
        def test_list(self, app, role_ref, user):
            resp = app.get(f'/api/roles/{role_ref}/members/')
            assert resp.json['results']
            first_result = resp.json['results'][0]
            assert first_result['uuid'] == user.uuid
            assert first_result['username'] == 'user'
            assert set(first_result) == USER_ATTRIBUTES_SET

        @pytest.mark.parametrize('role', ['parent'], indirect=True)
        def test_get_nested(self, app, role_ref, role):
            assert not app.get(f'/api/roles/{role_ref}/members/').json['results']
            assert app.get(f'/api/roles/{role_ref}/members/?nested=true').json['results']
            assert not app.get(f'/api/roles/{role_ref}/members/?nested=false').json['results']

    class TestUser(UserTestMixin):
        def test_list(self, app, role_ref, role, user_role, user):
            app.get(f'/api/roles/{role_ref}/members/', status=404)

            user_role.add_permission(role, 'view')
            assert not app.get(f'/api/roles/{role_ref}/members/').json['results']

            user_role.add_permission(user, 'view')
            assert app.get(f'/api/roles/{role_ref}/members/').json['results']


class TestRoleMembershipAPI:
    def test_get(self, app, role_ref, user):
        app.get(f'/api/roles/{role_ref}/members/{user.uuid}/', status=401)

    class TestAdmin(AdminTestMixin):
        def test_get(self, app, role_ref, role, user):
            app.get(f'/api/roles/{role_ref}/members/{user.uuid}/', status=404)
            role.members.add(user)
            resp = app.get(f'/api/roles/{role_ref}/members/{user.uuid}/')
            assert resp.json['uuid'] == user.uuid
            assert set(resp.json) == USER_ATTRIBUTES_SET

        def test_post(self, app, role_ref, role, admin, user):
            assert user not in role.members.all()
            app.post(f'/api/roles/{role_ref}/members/{user.uuid}/', status=201)
            assert user in role.members.all()

            assert_event(
                'manager.role.membership.grant',
                user=admin,
                api=True,
                role_name='Role',
                member_name=user.get_full_name(),
            )

        def test_delete(self, app, role_ref, role, admin, user):
            role.members.add(user)
            app.delete(f'/api/roles/{role_ref}/members/{user.uuid}/')
            assert user not in role.members.all()

            assert_event(
                'manager.role.membership.removal',
                user=admin,
                api=True,
                role_name='Role',
                member_name=user.get_full_name(),
            )

    class TestSimpleUser(UserTestMixin):
        def test_get(self, app, role_ref, role, user_role, user):
            role.members.add(user)

            app.get(f'/api/roles/{role_ref}/members/{user.uuid}/', status=404)

            user_role.add_permission(role, 'view')
            app.get(f'/api/roles/{role_ref}/members/{user.uuid}/', status=404)

            user_role.add_permission(User, 'view')
            app.get(f'/api/roles/{role_ref}/members/{user.uuid}/', status=200)

        def test_post(self, app, role_ref, role, user_role, user):
            assert user not in role.members.all()
            app.post(f'/api/roles/{role_ref}/members/{user.uuid}/', status=404)
            assert user not in role.members.all()

            user_role.members.add(user)
            user_role.add_permission(Role, 'view')
            user_role.add_permission(User, 'view')

            app.post(f'/api/roles/{role_ref}/members/{user.uuid}/', status=403)
            assert user not in role.members.all()

            user_role.add_permission(role, 'manage_members')

            app.post(f'/api/roles/{role_ref}/members/{user.uuid}/', status=201)
            assert user in role.members.all()

        def test_delete(self, app, role_ref, role, user_role, user):
            role.members.add(user)

            app.delete(f'/api/roles/{role_ref}/members/{user.uuid}/', status=404)
            assert user in role.members.all()

            user_role.add_permission(Role, 'view')
            user_role.add_permission(User, 'view')
            app.delete(f'/api/roles/{role_ref}/members/{user.uuid}/', status=403)
            assert user in role.members.all()

            user_role.add_permission(role, 'manage_members')
            app.delete(f'/api/roles/{role_ref}/members/{user.uuid}/', status=200)
            assert user not in role.members.all()


class TestRoleMembershipsAPI:
    def test_unauthenticated(self, app, role_ref):
        app.get(f'/api/roles/{role_ref}/relationships/members/', status=401)

    class TestAdmin(AdminTestMixin):
        def test_post(self, app, role_ref, role, admin, user):
            role.members.add(admin)

            assert {admin} == set(role.members.all())
            app.post_json(
                f'/api/roles/{role_ref}/relationships/members/',
                params={
                    'data': [{'uuid': user.uuid}],
                },
            )
            assert {admin, user} == set(role.members.all())

            assert_event(
                'manager.role.membership.grant',
                user=admin,
                api=True,
                role_name='Role',
                member_name='user',
            )

        def test_patch(self, app, role_ref, role, admin, user):
            role.members.add(admin)

            assert {admin} == set(role.members.all())
            app.patch_json(
                f'/api/roles/{role_ref}/relationships/members/',
                params={
                    'data': [{'uuid': user.uuid}],
                },
            )
            assert {user} == set(role.members.all())

            assert_event(
                'manager.role.membership.grant',
                user=admin,
                api=True,
                role_name='Role',
                member_name='user',
            )
            assert_event(
                'manager.role.membership.removal',
                user=admin,
                api=True,
                role_name='Role',
                member_name='admin',
            )

        def test_delete(self, app, role_ref, role, admin, user):
            role.members.add(user)

            assert {user} == set(role.members.all())
            app.delete_json(
                f'/api/roles/{role_ref}/relationships/members/',
                params={
                    'data': [{'uuid': user.uuid}],
                },
            )
            assert set() == set(role.members.all())

            assert_event(
                'manager.role.membership.removal',
                user=admin,
                api=True,
                role_name='Role',
                member_name='user',
            )

        # Test input validation
        def test_missing_payload(self, app):
            app.post_json('/api/roles/role/relationships/members/', status=400)
            app.patch_json('/api/roles/role/relationships/members/', status=400)
            app.delete_json('/api/roles/role/relationships/members/', status=400)

        @pytest.mark.parametrize(
            'payload',
            [[], {'data': [[]]}, {'data': ['a' * 32]}],
            ids=['list', 'data is list of list', 'data is list of uuid'],
        )
        def test_bad_payload(self, app, payload):
            app.post_json('/api/roles/role/relationships/members/', params=payload, status=400)
            app.patch_json('/api/roles/role/relationships/members/', params=payload, status=400)
            app.delete_json('/api/roles/role/relationships/members/', params=payload, status=400)

    class TestUser(UserTestMixin):
        def test_post(self, app, role_ref, role, admin, user_role, user):
            role.members.add(admin)

            payload = {'data': [{'uuid': user.uuid}]}

            assert {admin} == set(role.members.all())
            app.post_json(f'/api/roles/{role_ref}/relationships/members/', params=payload, status=403)
            user_role.add_permission(role, 'manage_members')
            app.post_json(f'/api/roles/{role_ref}/relationships/members/', params=payload, status=201)
            assert {admin, user} == set(role.members.all())

            assert_event(
                'manager.role.membership.grant',
                user=user,
                api=True,
                role_name='Role',
                member_name='user',
            )

        def test_patch(self, app, role_ref, role, admin, user_role, user):
            role.members.add(admin)

            payload = {'data': [{'uuid': user.uuid}]}

            assert {admin} == set(role.members.all())
            app.patch_json(f'/api/roles/{role_ref}/relationships/members/', params=payload, status=403)
            user_role.add_permission(role, 'manage_members')
            app.patch_json(f'/api/roles/{role_ref}/relationships/members/', params=payload, status=200)
            assert {user} == set(role.members.all())

            assert_event(
                'manager.role.membership.grant',
                user=user,
                api=True,
                role_name='Role',
                member_name='user',
            )
            assert_event(
                'manager.role.membership.removal',
                user=user,
                api=True,
                role_name='Role',
                member_name='admin',
            )

        def test_delete(self, app, role_ref, role, admin, user_role, user):
            role.members.add(user)
            payload = {'data': [{'uuid': user.uuid}]}

            assert {user} == set(role.members.all())
            app.delete_json(f'/api/roles/{role_ref}/relationships/members/', params=payload, status=403)
            user_role.add_permission(role, 'manage_members')
            app.delete_json(f'/api/roles/{role_ref}/relationships/members/', params=payload, status=200)
            assert set() == set(role.members.all())

            assert_event(
                'manager.role.membership.removal',
                user=user,
                api=True,
                role_name='Role',
                member_name='user',
            )


class TestRolesParentsRelationshipsAPI:
    def test_unauthenticated(self, app, role_ref):
        app.get(f'/api/roles/{role_ref}/relationships/parents/', status=401)

    class TestAdmin(AdminTestMixin):
        def test_list(self, app, role_ref):
            resp = app.get(f'/api/roles/{role_ref}/relationships/parents/')
            doc = resp.json
            assert doc == {
                'err': 0,
                'data': [
                    {
                        'direct': True,
                        'parent': {
                            'uuid': 'a' * 32,
                            'name': 'Parent',
                            'slug': 'parent',
                            'ou': OU_JSON,
                            'service': None,
                        },
                    }
                ],
            }

        @pytest.mark.parametrize('role', ['parent'], indirect=True)
        def test_list_parent(self, app, role_ref):
            resp = app.get(f'/api/roles/{role_ref}/relationships/parents/')
            assert len(resp.json['data']) == 0

        @pytest.mark.parametrize('role', ['child'], indirect=True)
        def test_list_child(self, app, role_ref):
            resp = app.get(f'/api/roles/{role_ref}/relationships/parents/')
            assert len(resp.json['data']) == 1

        @pytest.mark.parametrize('role', ['child'], indirect=True)
        def test_list_child_all(self, app, role_ref):
            resp = app.get(f'/api/roles/{role_ref}/relationships/parents/?all')
            assert len(resp.json['data']) == 2

        def test_post(self, app, role_ref, role, parent):
            role.remove_parent(parent)

            payload = {
                'parent': {
                    'slug': 'parent',
                }
            }
            assert parent not in role.parents(direct=True)
            assert app.post_json(f'/api/roles/{role_ref}/relationships/parents/', params=payload).json['data']
            assert parent in role.parents(direct=True)

        def test_post_natural_key_validation_error(self, app, role_ref, role, parent):
            payload = {'parent': 'whatever'}
            app.post_json(f'/api/roles/{role_ref}/relationships/parents/', params=payload, status=400)

        def test_delete(self, app, role_ref, role, parent):
            payload = {
                'parent': {
                    'slug': 'parent',
                }
            }
            assert parent in role.parents(direct=True)
            assert not app.delete_json(f'/api/roles/{role_ref}/relationships/parents/', params=payload).json[
                'data'
            ]
            assert parent not in role.parents(direct=True)

    class TestUser(UserTestMixin):
        def test_list(self, app, role_ref, role, user_role):
            app.get(f'/api/roles/{role_ref}/relationships/parents/', status=404)
            user_role.add_permission(role, 'view')
            assert not app.get(f'/api/roles/{role_ref}/relationships/parents/').json['data']
            user_role.add_permission(Role, 'view')
            assert app.get(f'/api/roles/{role_ref}/relationships/parents/').json['data']

        def test_post(self, app, role_ref, role, parent, user_role):
            role.remove_parent(parent)

            payload = {
                'parent': {
                    'slug': 'parent',
                }
            }
            assert parent not in role.parents(direct=True)
            app.post_json(f'/api/roles/{role_ref}/relationships/parents/', params=payload, status=404)
            assert parent not in role.parents(direct=True)
            user_role.add_permission(role, 'view')
            app.post_json(f'/api/roles/{role_ref}/relationships/parents/', params=payload, status=403)
            assert parent not in role.parents(direct=True)
            user_role.add_permission(parent, 'manage_members')
            assert app.post_json(f'/api/roles/{role_ref}/relationships/parents/', params=payload).json['data']
            assert parent in role.parents(direct=True)

        def test_delete(self, app, role_ref, role, parent, user_role):
            payload = {
                'parent': {
                    'slug': 'parent',
                }
            }
            assert parent in role.parents(direct=True)
            app.delete_json(f'/api/roles/{role_ref}/relationships/parents/', params=payload, status=404)
            assert parent in role.parents(direct=True)
            user_role.add_permission(role, 'view')
            app.delete_json(f'/api/roles/{role_ref}/relationships/parents/', params=payload, status=403)
            assert parent in role.parents(direct=True)
            user_role.add_permission(parent, 'manage_members')
            assert not app.delete_json(f'/api/roles/{role_ref}/relationships/parents/', params=payload).json[
                'data'
            ]
            assert parent not in role.parents(direct=True)


class TestRolesParentsAPI:
    def test_unauthenticated(self, app, role_ref):
        app.get(f'/api/roles/{role_ref}/parents/', status=401)

    class TestAdmin(AdminTestMixin):
        @pytest.mark.parametrize('role', ['child'], indirect=True)
        def test_list(self, app, role_ref):
            resp = app.get(f'/api/roles/{role_ref}/parents/')
            doc = resp.json
            assert doc == {
                'err': 0,
                'data': [{'name': 'Role', 'ou': 'default', 'slug': 'role', 'uuid': 'b' * 32}],
            }
            resp = app.get(f'/api/roles/{role_ref}/parents/?all')
            doc = resp.json
            assert doc == {
                'err': 0,
                'data': [
                    {'name': 'Parent', 'ou': 'default', 'slug': 'parent', 'uuid': 'a' * 32, 'direct': False},
                    {'name': 'Role', 'ou': 'default', 'slug': 'role', 'uuid': 'b' * 32, 'direct': True},
                ],
            }


class TestRolesChildrenAPI:
    def test_unauthenticated(self, app, role_ref):
        app.get(f'/api/roles/{role_ref}/children/', status=401)

    class TestAdmin(AdminTestMixin):
        @pytest.mark.parametrize('role', ['parent'], indirect=True)
        def test_list(self, app, role_ref):
            resp = app.get(f'/api/roles/{role_ref}/children/')
            doc = resp.json
            assert doc == {
                'err': 0,
                'data': [{'name': 'Role', 'ou': 'default', 'slug': 'role', 'uuid': 'b' * 32}],
            }
            resp = app.get(f'/api/roles/{role_ref}/children/?all')
            doc = resp.json
            assert doc == {
                'err': 0,
                'data': [
                    {'name': 'Role', 'ou': 'default', 'slug': 'role', 'uuid': 'b' * 32, 'direct': True},
                    {'name': 'Child', 'ou': 'default', 'slug': 'child', 'uuid': 'c' * 32, 'direct': False},
                ],
            }

    class TestUser(UserTestMixin):
        def test_list(self, app, role_ref, role, parent, child, user_role):
            app.get('/api/roles/child/parents/', status=404)
            user_role.add_permission(child, 'view')
            assert not app.get('/api/roles/child/parents/').json['data']
            user_role.add_permission(role, 'search')
            assert len(app.get('/api/roles/child/parents/').json['data']) == 1
            assert len(app.get('/api/roles/child/parents/?all').json['data']) == 1
