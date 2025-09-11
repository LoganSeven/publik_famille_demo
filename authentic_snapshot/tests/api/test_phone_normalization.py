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

from authentic2.models import Attribute

from ..utils import basic_authorization_header

pytestmark = pytest.mark.django_db

User = get_user_model()


def test_phone_normalization_ok(settings, app, admin):
    headers = basic_authorization_header(admin)
    Attribute.objects.create(name='extra_phone', label='extra phone', kind='phone_number')
    payload = {
        'username': 'janedoe',
        'extra_phone': ' + 334-99 98.56/43',
        'first_name': 'Jane',
        'last_name': 'Doe',
    }
    resp = app.post_json('/api/users/', headers=headers, params=payload, status=201)
    assert resp.json['extra_phone'] == '+33499985643'
    user = User.objects.get(username='janedoe')
    assert user.attributes.extra_phone == '+33499985643'

    user.delete()
    payload['extra_phone'] = ' + 334-99 98.56/433 '
    resp = app.post_json('/api/users/', headers=headers, params=payload, status=201)
    assert resp.json['extra_phone'] == '+334999856433'
    user = User.objects.get(username='janedoe')
    assert user.attributes.extra_phone == '+334999856433'

    user.delete()
    payload['extra_phone'] = ' 04-99 98.56/433 '
    resp = app.post_json('/api/users/', headers=headers, params=payload, status=201)
    assert resp.json['extra_phone'] == '+334999856433'
    user = User.objects.get(username='janedoe')
    assert user.attributes.extra_phone == '+334999856433'

    user.delete()
    payload['extra_phone'] = ''
    resp = app.post_json('/api/users/', headers=headers, params=payload, status=201)
    assert resp.json['extra_phone'] == ''
    user = User.objects.get(username='janedoe')
    assert user.attributes.extra_phone == ''


def test_phone_normalization_nok(settings, app, admin):
    headers = basic_authorization_header(admin)
    Attribute.objects.create(name='extra_phone', label='extra phone', kind='phone_number')
    payload = {
        'username': 'janedoe',
        'first_name': 'Jane',
        'last_name': 'Doe',
    }
    payload['extra_phone'] = (' + 334-99+98.56/43',)
    app.post_json('/api/users/', headers=headers, params=payload, status=400)

    payload['extra_phone'] = '1#2'
    app.post_json('/api/users/', headers=headers, params=payload, status=400)

    payload['extra_phone'] = '+33499985643343434343'
    app.post_json('/api/users/', headers=headers, params=payload, status=400)

    payload['extra_phone'] = '+334-99 98\\56/43'
    app.post_json('/api/users/', headers=headers, params=payload, status=400)

    payload['extra_phone'] = '+334'
    app.post_json('/api/users/', headers=headers, params=payload, status=400)


def test_fr_phone_normalization_ok(settings, app, admin):
    headers = basic_authorization_header(admin)
    Attribute.objects.create(name='extra_phone', label='extra phone', kind='fr_phone_number')
    payload = {
        'username': 'janedoe',
        'extra_phone': ' 04-99 98.56/43',
        'first_name': 'Jane',
        'last_name': 'Doe',
    }
    resp = app.post_json('/api/users/', headers=headers, params=payload, status=201)
    assert resp.json['extra_phone'] == '0499985643'
    assert User.objects.get(username='janedoe').attributes.extra_phone == '0499985643'


def test_fr_phone_normalization_nok(settings, app, admin):
    headers = basic_authorization_header(admin)
    Attribute.objects.create(name='extra_phone', label='extra phone', kind='fr_phone_number')
    payload = {
        'username': 'janedoe',
        'extra_phone': '+33499985643',
        'first_name': 'Jane',
        'last_name': 'Doe',
    }
    app.post_json('/api/users/', headers=headers, params=payload, status=400)

    payload['extra_phone'] = '1#2'
    app.post_json('/api/users/', headers=headers, params=payload, status=400)
