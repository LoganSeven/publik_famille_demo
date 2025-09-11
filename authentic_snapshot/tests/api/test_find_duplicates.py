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

import datetime

import pytest
from django.contrib.auth import get_user_model

from authentic2.a2_rbac.utils import get_default_ou
from authentic2.models import Attribute

pytestmark = pytest.mark.django_db

User = get_user_model()


def test_find_duplicates(app, admin, django_assert_max_num_queries, role_random):
    app.authorization = ('Basic', (admin.username, admin.clear_password))

    first_name = 'Jean-Kévin'
    last_name = 'Du Château'
    user = User.objects.create(first_name=first_name, last_name=last_name)
    role_random.members.add(user)

    exact_match = {
        'first_name': first_name,
        'last_name': last_name,
    }
    resp = app.get('/api/users/find_duplicates/', params=exact_match)
    assert resp.json['data'][0]['id'] == user.id
    assert resp.json['data'][0]['duplicate_distance'] == 0
    assert resp.json['data'][0]['text'] == 'Jean-Kévin Du Château'

    typo = {
        'first_name': 'Jean Kévin',
        'last_name': 'Du Châtau',
    }
    with django_assert_max_num_queries(17):
        resp = app.get('/api/users/find_duplicates/', params=typo)
    assert resp.json['data'][0]['id'] == user.id
    assert resp.json['data'][0]['duplicate_distance'] > 0

    typo = {
        'first_name': 'Jean Kévin',
        'last_name': 'Château',
    }
    resp = app.get('/api/users/find_duplicates/', params=typo)
    assert resp.json['data'][0]['id'] == user.id

    other_person = {
        'first_name': 'Jean-Kévin',
        'last_name': 'Du Chêne',
    }
    user = User.objects.create(first_name='Éléonore', last_name='âêîôû')
    resp = app.get('/api/users/find_duplicates/', params=other_person)
    assert len(resp.json['data']) == 0

    other_person = {
        'first_name': 'Pierre',
        'last_name': 'Du Château',
    }
    resp = app.get('/api/users/find_duplicates/', params=other_person)
    assert len(resp.json['data']) == 0


def test_find_duplicates_unaccent(app, admin, settings):
    app.authorization = ('Basic', (admin.username, admin.clear_password))

    user = User.objects.create(first_name='Éléonore', last_name='âêîôû')

    resp = app.get('/api/users/find_duplicates/', params={'first_name': 'Eleonore', 'last_name': 'aeiou'})
    assert resp.json['data'][0]['id'] == user.id


def test_find_duplicates_birthdate(app, admin, settings):
    app.authorization = ('Basic', (admin.username, admin.clear_password))

    Attribute.objects.create(
        kind='birthdate', name='birthdate', label='birthdate', required=False, searchable=True
    )

    user = User.objects.create(first_name='Jean', last_name='Dupont')
    homonym = User.objects.create(first_name='Jean', last_name='Dupont')
    user.attributes.birthdate = datetime.date(1980, 1, 2)
    homonym.attributes.birthdate = datetime.date(1980, 1, 3)

    params = {
        'first_name': 'Jeanne',
        'last_name': 'Dupont',
    }
    resp = app.get('/api/users/find_duplicates/', params=params)
    assert len(resp.json['data']) == 2

    params['birthdate'] = ('1980-01-2',)
    resp = app.get('/api/users/find_duplicates/', params=params)
    assert len(resp.json['data']) == 2
    assert resp.json['data'][0]['id'] == user.pk

    params['birthdate'] = ('1980-01-3',)
    resp = app.get('/api/users/find_duplicates/', params=params)
    assert len(resp.json['data']) == 2
    assert resp.json['data'][0]['id'] == homonym.pk


def test_find_duplicates_ou(app, admin, settings, ou1):
    app.authorization = ('Basic', (admin.username, admin.clear_password))

    user = User.objects.create(first_name='Jean', last_name='Dupont', ou=get_default_ou())
    User.objects.create(first_name='Jean', last_name='Dupont', ou=ou1)

    params = {
        'first_name': 'Jeanne',
        'last_name': 'Dupont',
    }
    resp = app.get('/api/users/find_duplicates/', params=params)
    assert len(resp.json['data']) == 2

    params['ou'] = get_default_ou().slug
    resp = app.get('/api/users/find_duplicates/', params=params)
    assert len(resp.json['data']) == 1
    assert resp.json['data'][0]['id'] == user.pk


def test_find_duplicates_put(app, admin, settings):
    app.authorization = ('Basic', (admin.username, admin.clear_password))
    app.put_json(
        '/api/users/find_duplicates/', params={'first_name': 'Eleonore', 'last_name': 'aeiou'}, status=405
    )
