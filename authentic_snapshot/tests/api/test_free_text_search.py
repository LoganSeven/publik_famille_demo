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

from authentic2.models import Attribute

pytestmark = pytest.mark.django_db

User = get_user_model()


def test_free_text_search(app, admin, settings):
    settings.LANGUAGE_CODE = 'fr'  # use fr date format

    app.authorization = ('Basic', (admin.username, admin.clear_password))
    Attribute.objects.create(
        kind='birthdate', name='birthdate', label='birthdate', required=False, searchable=True
    )

    user = User.objects.create()
    user.attributes.birthdate = datetime.date(1982, 2, 10)

    resp = app.get('/api/users/?q=10/02/1982')
    assert len(resp.json['results']) == 1
    assert resp.json['results'][0]['id'] == user.id


def test_free_text_search_local_phone(app, admin, simple_user, settings):
    app.authorization = ('Basic', (admin.username, admin.clear_password))
    settings.DEFAULT_COUNTRY_CODE = '33'
    Attribute.objects.create(
        kind='phone_number', name='phone', label='Phone', required=False, searchable=True
    )

    simple_user.attributes.phone = '+33612345678'
    simple_user.save()

    resp = app.get('/api/users/?q=+33612345678')
    assert len(resp.json['results']) == 1
    assert resp.json['results'][0]['id'] == simple_user.id

    # non globally-unique phone number still resolvable from DEFAULT_COUNTRY_CODE prefix
    resp = app.get('/api/users/?q=0612345678')
    assert len(resp.json['results']) == 1
    assert resp.json['results'][0]['id'] == simple_user.id

    # additionnaly, erroneous data must still be searchable
    simple_user.attributes.phone = '0xf00d'
    simple_user.save()

    resp = app.get('/api/users/?q=0xf00d')
    assert len(resp.json['results']) == 1
    assert resp.json['results'][0]['id'] == simple_user.id
