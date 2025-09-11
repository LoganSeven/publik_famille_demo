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

from ..utils import login

pytestmark = pytest.mark.django_db

User = get_user_model()


def test_api_users_get_or_create(settings, app, admin):
    app.authorization = ('Basic', (admin.username, admin.clear_password))
    # test missing first_name
    payload = {
        'email': 'john.doe@example.net',
        'first_name': 'John',
        'last_name': 'Doe',
    }
    resp = app.post_json('/api/users/?get_or_create=email', params=payload, status=201)
    id = resp.json['id']
    assert User.objects.get(id=id).first_name == 'John'
    assert User.objects.get(id=id).last_name == 'Doe'
    password = User.objects.get(id=id).password

    resp = app.post_json('/api/users/?get_or_create=email', params=payload, status=200)
    assert id == resp.json['id']
    assert User.objects.get(id=id).first_name == 'John'
    assert User.objects.get(id=id).last_name == 'Doe'
    assert User.objects.get(id=id).password == password

    payload = {
        'email': 'john.doe@example.net',
        'first_name': 'Jane',
    }
    resp = app.post_json('/api/users/?update_or_create=email', params=payload, status=200)
    assert id == resp.json['id']
    assert User.objects.get(id=id).first_name == 'Jane'
    assert User.objects.get(id=id).last_name == 'Doe'
    assert User.objects.get(id=id).password == password

    payload['password'] = 'secret'
    resp = app.post_json('/api/users/?update_or_create=email', params=payload, status=200)
    assert User.objects.get(id=id).first_name == 'Jane'
    assert User.objects.get(id=id).last_name == 'Doe'
    assert User.objects.get(id=id).password != password
    assert User.objects.get(id=id).check_password('secret')

    # do not get deleted user, create a new one
    User.objects.get(id=id).delete()
    payload['last_name'] = 'Doe'
    resp = app.post_json('/api/users/?get_or_create=email', params=payload, status=201)
    assert id != resp.json['id']
    id = resp.json['id']
    assert User.objects.get(id=id).first_name == 'Jane'
    assert User.objects.get(id=id).last_name == 'Doe'
    assert User.objects.get(id=id).password != password
    assert User.objects.get(id=id).check_password('secret')


def test_api_users_get_or_create_force_password_reset(app, client, settings, superuser):
    app.authorization = ('Basic', (superuser.username, superuser.clear_password))
    # test missing first_name
    payload = {
        'email': 'john.doe@example.net',
        'first_name': 'John',
        'last_name': 'Doe',
        'force_password_reset': True,
        'password': '1234',
    }
    resp = app.post_json('/api/users/?get_or_create=email', params=payload, status=201)
    id = resp.json['id']
    assert User.objects.get(id=id).first_name == 'John'
    assert User.objects.get(id=id).last_name == 'Doe'
    password = User.objects.get(id=id).password

    resp = app.post_json('/api/users/?get_or_create=email', params=payload, status=200)
    assert id == resp.json['id']
    assert User.objects.get(id=id).first_name == 'John'
    assert User.objects.get(id=id).last_name == 'Doe'
    assert User.objects.get(id=id).password == password

    # Verify password reset is enforced on next login
    resp = login(app, 'john.doe@example.net', path='/', password='1234').follow()
    resp.form.set('old_password', '1234')
    resp.form.set('new_password1', '1234==aB')
    resp.form.set('new_password2', '1234==aB')
    resp = resp.form.submit('Submit').follow().maybe_follow()
    assert 'Password changed' in resp


def test_api_users_update_or_create_force_password_reset(app, client, settings, superuser):
    app.authorization = ('Basic', (superuser.username, superuser.clear_password))
    user = User.objects.create(
        first_name='John',
        last_name='Doe',
        email='john.doe@example.net',
    )
    id = user.id
    user.set_password('1234')
    user.save()
    password = user.password

    payload = {
        'email': 'john.doe@example.net',
        'first_name': 'Jane',
        'force_password_reset': True,
    }
    resp = app.post_json('/api/users/?update_or_create=email', params=payload, status=200)
    assert id == resp.json['id']
    assert User.objects.get(id=id).first_name == 'Jane'
    assert User.objects.get(id=id).last_name == 'Doe'
    assert User.objects.get(id=id).password == password

    payload['password'] = 'secret'
    resp = app.post_json('/api/users/?update_or_create=email', params=payload, status=200)
    assert User.objects.get(id=id).first_name == 'Jane'
    assert User.objects.get(id=id).last_name == 'Doe'
    assert User.objects.get(id=id).password != password
    assert User.objects.get(id=id).check_password('secret')
    # Verify password reset is enforced on next login
    resp = login(app, 'john.doe@example.net', path='/', password='secret').follow()
    resp.form.set('old_password', 'secret')
    resp.form.set('new_password1', 'secret==aB1234!!')
    resp.form.set('new_password2', 'secret==aB1234!!')
    resp = resp.form.submit('Submit').follow().maybe_follow()
    assert 'Password changed' in resp


def test_api_users_get_or_create_email_is_unique(settings, app, admin):
    settings.A2_EMAIL_IS_UNIQUE = True
    app.authorization = ('Basic', (admin.username, admin.clear_password))
    # test missing first_name
    payload = {
        'email': 'john.doe@example.net',
        'first_name': 'John',
        'last_name': 'Doe',
    }
    resp = app.post_json('/api/users/?get_or_create=email', params=payload, status=201)
    id = resp.json['id']
    assert User.objects.get(id=id).first_name == 'John'
    assert User.objects.get(id=id).last_name == 'Doe'

    resp = app.post_json('/api/users/?get_or_create=email', params=payload, status=200)
    assert id == resp.json['id']
    assert User.objects.get(id=id).first_name == 'John'
    assert User.objects.get(id=id).last_name == 'Doe'

    payload['first_name'] = 'Jane'
    resp = app.post_json('/api/users/?update_or_create=email', params=payload, status=200)
    assert id == resp.json['id']
    assert User.objects.get(id=id).first_name == 'Jane'
    assert User.objects.get(id=id).last_name == 'Doe'


def test_api_users_get_or_create_email_not_unique(settings, app, admin):
    settings.A2_EMAIL_IS_UNIQUE = False
    ou1 = OU.objects.create(name='OU1', slug='ou1', email_is_unique=True)
    ou2 = OU.objects.create(name='OU2', slug='ou2', email_is_unique=False)

    app.authorization = ('Basic', (admin.username, admin.clear_password))
    payload = {'email': 'john.doe@example.net', 'first_name': 'John', 'last_name': 'Doe', 'ou': 'ou1'}
    # 1. create
    resp = app.post_json('/api/users/?get_or_create=email', params=payload, status=201)
    id_user = resp.json['id']
    assert User.objects.get(id=id_user).first_name == 'John'
    assert User.objects.get(id=id_user).last_name == 'Doe'
    assert User.objects.get(id=id_user).ou == ou1
    # 2. get but override email with an uppercase version, to test if search is case-insensitive
    resp = app.post_json(
        '/api/users/?get_or_create=email', params=dict(payload, email=payload['email'].upper()), status=200
    )
    # 3. explicitly create in a different OU
    payload['ou'] = 'ou2'
    resp = app.post_json('/api/users/', params=payload, status=201)
    id_user2 = resp.json['id']
    assert id_user2 != id_user
    assert User.objects.get(id=id_user2).first_name == 'John'
    assert User.objects.get(id=id_user2).last_name == 'Doe'
    assert User.objects.get(id=id_user2).ou == ou2
    # 4. fail to retrieve a single instance for an ambiguous get-or-create key
    resp = app.post_json('/api/users/?get_or_create=email', params=payload, status=409)
    assert (
        resp.json['errors']
        == "retrieved several instances of model User for key attributes {'email__iexact': 'john.doe@example.net'}"
    )


def test_api_users_get_or_create_multi_key(settings, app, admin):
    app.authorization = ('Basic', (admin.username, admin.clear_password))
    # test missing first_name
    payload = {
        'email': 'john.doe@example.net',
        'first_name': 'John',
        'last_name': 'Doe',
    }
    resp = app.post_json(
        '/api/users/?get_or_create=first_name&get_or_create=last_name', params=payload, status=201
    )
    id = resp.json['id']
    assert User.objects.get(id=id).first_name == 'John'
    assert User.objects.get(id=id).last_name == 'Doe'
    password = User.objects.get(id=id).password

    resp = app.post_json(
        '/api/users/?get_or_create=first_name&get_or_create=last_name', params=payload, status=200
    )
    assert id == resp.json['id']
    assert User.objects.get(id=id).first_name == 'John'
    assert User.objects.get(id=id).last_name == 'Doe'
    assert User.objects.get(id=id).password == password

    payload['email'] = 'john.doe@example2.net'
    payload['password'] = 'secret'
    resp = app.post_json(
        '/api/users/?update_or_create=first_name&update_or_create=last_name', params=payload, status=200
    )
    assert id == resp.json['id']
    assert User.objects.get(id=id).email == 'john.doe@example2.net'
    assert User.objects.get(id=id).password != password
    assert User.objects.get(id=id).check_password('secret')
