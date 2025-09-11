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

from unittest import mock

import pytest
from rest_framework.exceptions import MethodNotAllowed, ValidationError

from authentic2.a2_rbac.models import OrganizationalUnit as OU
from authentic2.a2_rbac.models import Role
from authentic2.models import Service
from authentic2.utils.api import DjangoRBACPermission, NaturalKeyRelatedField, get_boolean_flag
from tests.utils import scoped_db_fixture


class TestNaturalKeyRelatedField:
    @scoped_db_fixture(scope='class', autouse=True)
    def fixture(self):
        class Namespace:
            ou = OU.objects.create(name='ou', uuid='1' * 32)
            service = Service.objects.create(name='service', slug='service', ou=ou)
            role = Role.objects.create(name='role', ou=ou, uuid='2' * 32)
            ou2 = OU.objects.create(name='ou2', uuid='3' * 32)

        yield Namespace

    def test_to_representation(self, db, fixture):
        assert NaturalKeyRelatedField(read_only=True).to_representation(fixture.role) == {
            'name': 'role',
            'ou': {'name': 'ou', 'slug': 'ou', 'uuid': '1' * 32},
            'service': None,
            'slug': 'role',
            'uuid': '2' * 32,
        }

    def test_to_representation_service(self, db, fixture):
        fixture.role.service = fixture.service
        fixture.role.save()
        assert NaturalKeyRelatedField(read_only=True).to_representation(fixture.role) == {
            'name': 'role',
            'ou': {'name': 'ou', 'slug': 'ou', 'uuid': '1' * 32},
            'service': {
                'slug': 'service',
                'ou': {'name': 'ou', 'slug': 'ou', 'uuid': '1' * 32},
            },
            'slug': 'role',
            'uuid': '2' * 32,
        }

    @pytest.mark.parametrize(
        'value',
        [
            {'uuid': '2' * 32},
            {'name': 'role'},
            {'slug': 'role'},
            {'name': 'role', 'ou': {'name': 'ou'}},
            {'slug': 'role', 'ou': {'slug': 'ou'}},
            {'slug': 'role', 'ou': {'uuid': '1' * 32}},
        ],
        ids=[
            'by uuid',
            'by name',
            'by slug',
            'by name and ou by name',
            'by name and ou by slug',
            'by name and ou by uuid',
        ],
    )
    def test_to_internal_value_role(self, value, db, fixture):
        assert NaturalKeyRelatedField(queryset=Role.objects.all()).to_internal_value(value) == fixture.role

    @pytest.mark.parametrize(
        'value',
        [
            {'name': 'role'},
            {'slug': 'role'},
        ],
        ids=['by name', 'by slug'],
    )
    def test_to_internal_value_role_ambiguous(self, value, db, fixture):
        Role.objects.create(slug='role', name='role', ou=fixture.ou2)
        with pytest.raises(ValidationError, match='multiple'):
            assert (
                NaturalKeyRelatedField(queryset=Role.objects.all()).to_internal_value(value) == fixture.role
            )

    @pytest.mark.parametrize(
        'value',
        [
            {'name': 'role', 'ou': {'slug': 'ou'}},
            {'slug': 'role', 'ou': {'slug': 'ou'}},
        ],
        ids=['by name and ou', 'by slug and ou'],
    )
    def test_to_internal_value_role_unique(self, value, db, fixture):
        Role.objects.create(slug='role', name='role', ou=fixture.ou2)
        assert NaturalKeyRelatedField(queryset=Role.objects.all()).to_internal_value(value) == fixture.role

    @pytest.mark.parametrize(
        'value',
        [
            {'uuid': '2' * 32},
            {'name': 'role'},
            {'slug': 'role'},
            {'name': 'role', 'ou': {'name': 'ou'}},
            {'slug': 'role', 'ou': {'slug': 'ou'}},
            {'slug': 'role', 'ou': {'uuid': '1' * 32}},
        ],
        ids=[
            'by uuid',
            'by name',
            'by slug',
            'by name and ou by name',
            'by name and ou by slug',
            'by name and ou by uuid',
        ],
    )
    def test_to_internal_value_role_not_found(self, value, db, fixture):
        Role.objects.all().delete()
        with pytest.raises(ValidationError, match='not found'):
            assert (
                NaturalKeyRelatedField(queryset=Role.objects.all()).to_internal_value(value) == fixture.role
            )


class TestDjangoRBACPermission:
    @pytest.fixture
    def permission(self):
        return DjangoRBACPermission(
            perms_map={
                'GET': [],
                'POST': ['create'],
                'DELETE': [],
            },
            object_perms_map={
                'GET': [],
                'DELETE': ['delete'],
            },
        )

    @pytest.fixture
    def view(self):
        view = mock.Mock()
        view.get_queryset.return_value = Role.objects.all()
        return view

    class TestHasPermission:
        def test_user_must_be_authenticated(self, permission):
            request = mock.Mock()
            request.user.is_authenticated = False
            assert not permission.has_permission(request=request, view=mock.Mock())

        def test_method_is_not_allowed(self, rf, permission):
            request = mock.Mock()
            request.method = 'PATCH'
            request.user.is_authenticated = True

            with pytest.raises(MethodNotAllowed):
                permission.has_permission(request=request, view=mock.Mock())

        def test_method_post(self, permission, view):
            request = mock.Mock()
            request.method = 'POST'
            request.user.is_authenticated = True
            request.user.has_perms = lambda perms, obj=None: not obj and set(perms) <= {'a2_rbac.create_role'}
            assert permission.has_permission(request=request, view=view)

            request.user.has_perms = lambda perms, obj=None: not obj and set(perms) <= set()
            assert not permission.has_permission(request=request, view=view)

        def test_method_get(self, permission, view):
            request = mock.Mock()
            request.user.is_authenticated = True
            request.method = 'GET'
            request.user.has_perms = lambda perms, obj=None: not obj and (not perms or set(perms) <= set())
            assert permission.has_permission(request=request, view=view)

    class TestHasObjectPermission:
        def test_user_must_be_authenticated(self, permission):
            request = mock.Mock()
            request.user.is_authenticated = False
            assert not permission.has_object_permission(request=request, view=mock.Mock(), obj=mock.Mock())

        def test_method_is_not_allowed(self, rf, permission):
            request = mock.Mock()
            request.method = 'PATCH'
            request.user.is_authenticated = True

            with pytest.raises(MethodNotAllowed):
                permission.has_object_permission(request=request, view=mock.Mock(), obj=mock.Mock())

        def test_method_delete(self, permission, view):
            request = mock.Mock()
            request.method = 'DELETE'
            request.user.is_authenticated = True
            mock_obj = mock.Mock()
            request.user.has_perms = (
                lambda perms, obj=None: set(perms) <= {'a2_rbac.delete_role'} and obj is mock_obj
            )
            assert permission.has_object_permission(request=request, view=view, obj=mock_obj)

            request.user.has_perms = mock.Mock(return_value=False)
            assert not permission.has_object_permission(request=request, view=view, obj=mock_obj)
            assert request.user.has_perms.call_args[1]['obj'] is mock_obj

            request.method = 'GET'
            request.user.has_perms = lambda perms, obj=None: not obj and set(perms) <= {'a2_rbac.create_role'}
            assert permission.has_permission(request=request, view=view)


def test_get_boolean_flag(rf):
    def test(path, name, default=None):
        return get_boolean_flag(rf.get(path), name, default=default)

    assert test('/?include-role=yes', 'include-role', default='barfoo') is True
    assert test('/?include-role=no', 'include-role', default='barfoo') is False
    assert test('/?include-role=foobar', 'include-role', default='barfoo') == 'barfoo'
    assert test('/?include-foo=yes', 'include-role', default='barfoo') == 'barfoo'
    assert test('/?include-foo=yes', 'include-role') is None
