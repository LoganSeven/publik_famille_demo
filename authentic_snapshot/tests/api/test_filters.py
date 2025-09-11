# authentic2 - versatile identity manager
# Copyright (C) 2010-2023 Entr'ouvert
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

from authentic2.a2_rbac.models import OrganizationalUnit as OU
from authentic2.a2_rbac.models import Role
from authentic2.models import AuthorizedRole

pytestmark = pytest.mark.django_db

User = get_user_model()


def test_api_filter_role_list(app, superuser):
    app.authorization = ('Basic', (superuser.username, superuser.clear_password))
    role = Role.objects.create(name='Test role')
    for i in range(10):
        Role.objects.create(name=f'Prefixed test role {i}')

    resp = app.get('/api/roles/?name__icontains=test role')
    assert len(resp.json['results']) == 11

    resp = app.get('/api/roles/?slug__icontains=test-role')
    assert len(resp.json['results']) == 11

    resp = app.get('/api/roles/?name__startswith=Prefixed')
    assert len(resp.json['results']) == 10

    resp = app.get('/api/roles/?slug__startswith=prefixed')
    assert len(resp.json['results']) == 10

    resp = app.get('/api/roles/?uuid=%s' % role.uuid)
    assert len(resp.json['results']) == 1
    assert resp.json['results'][0]['name'] == 'Test role'

    resp = app.get('/api/roles/?name=Test role')
    assert len(resp.json['results']) == 1

    resp = app.get('/api/roles/?name=test role')
    assert len(resp.json['results']) == 0

    resp = app.get('/api/roles/?ou__slug=ou2&slug__icontains=test-role')
    assert len(resp.json['results']) == 0

    ou2 = OU.objects.create(name='OU2', slug='ou2', email_is_unique=True)
    role = Role.objects.create(name='Test role', ou=ou2)
    resp = app.get('/api/roles/?ou__slug=ou2&slug__icontains=test-role')
    assert len(resp.json['results']) == 1


def test_filter_users_by_service(app, admin, simple_user, role_random, service):
    app.authorization = ('Basic', (admin.username, admin.clear_password))

    resp = app.get('/api/users/')
    assert len(resp.json['results']) == 2

    resp = app.get('/api/users/?service-slug=xxx')
    assert len(resp.json['results']) == 0

    resp = app.get('/api/users/?service-slug=service&service-ou=default')
    assert len(resp.json['results']) == 2

    role_random.members.add(simple_user)
    AuthorizedRole.objects.get_or_create(service=service, role=role_random)

    resp = app.get('/api/users/?service-slug=service&service-ou=default')
    assert len(resp.json['results']) == 1


def test_filter_users_by_last_modification(app, admin, simple_user, freezer):
    app.authorization = ('Basic', (admin.username, admin.clear_password))

    freezer.move_to('2019-10-27T02:00:00Z')

    admin.save()
    simple_user.save()

    # AmbiguousTimeError
    # django 3 (justifiably) fails on tz-naive datetimes
    app.get('/api/users/', params={'modified__gt': '2019-10-27T02:58:07'}, status=400)
    app.get('/api/users/', params={'modified__lt': '2019-10-27T02:58:07'}, status=400)
    # tz-aware datetimes work
    resp = app.get('/api/users/', params={'modified__gt': '2019-10-27T02:58:07+00:00'})
    assert len(resp.json['results']) == 0
    resp = app.get('/api/users/', params={'modified__lt': '2019-10-27T02:58:07+00:00'})
    assert len(resp.json['results']) == 2


def test_filter_users_by_date_joined(app, admin, simple_user, freezer):
    app.authorization = ('Basic', (admin.username, admin.clear_password))

    freezer.move_to('2019-10-27T02:00:00Z')

    admin.save()
    simple_user.save()

    # AmbiguousTimeError
    # django 3 (justifiably) fails on tz-naive datetimes
    app.get('/api/users/', params={'date_joined__gt': '2019-10-27T02:58:07'}, status=400)
    app.get('/api/users/', params={'date_joined__lt': '2019-10-27T02:58:07'}, status=400)
    # tz-aware datetimes work
    resp = app.get('/api/users/', params={'date_joined__lt': '2021-10-27T02:58:07+00:00'})
    assert len(resp.json['results']) == 0
    resp = app.get('/api/users/', params={'date_joined__gt': '2019-10-27T02:58:07+00:00'})
    assert len(resp.json['results']) == 2
