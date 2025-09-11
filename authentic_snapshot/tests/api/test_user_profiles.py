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

from authentic2.a2_rbac.utils import get_default_ou
from authentic2.custom_user.models import Profile, ProfileType

pytestmark = pytest.mark.django_db

User = get_user_model()


def test_user_profile_get(app, superuser):
    app.authorization = ('Basic', (superuser.username, superuser.clear_password))

    user = User.objects.create(username='john.doe', email='john.doe@example.com', ou=get_default_ou())

    # wrong type
    app.get('/api/users/%s/profiles/non-existant-slug/' % user.uuid, status=404)

    profile_type = ProfileType.objects.create(slug='one-type', name='One Type')

    # wrong user
    app.get('/api/users/1234/profiles/one-type/', status=404)

    profile = Profile.objects.create(
        profile_type=profile_type, user=user, email='user123@example.org', data={'foo': 'bar'}
    )
    resp = app.get('/api/users/%s/profiles/one-type/' % user.uuid)
    assert resp.json == [{'data': {'foo': 'bar'}, 'identifier': '', 'email': 'user123@example.org'}]

    resp = app.get('/api/users/%s/profiles/one-type/?identifier=' % user.uuid)
    assert resp.json == {'data': {'foo': 'bar'}, 'identifier': '', 'email': 'user123@example.org'}

    profile.identifier = 'Company ABC'
    profile.save()

    # specify identifier in qs
    resp = app.get(
        '/api/users/%s/profiles/one-type/?identifier=Company ABC' % user.uuid,
    )
    assert resp.json == {'data': {'foo': 'bar'}, 'identifier': 'Company ABC', 'email': 'user123@example.org'}

    # not found on empty identifier
    app.get(
        '/api/users/%s/profiles/one-type/?identifier=' % user.uuid,
        status=404,
    )


def test_user_profile_put(app, superuser):
    app.authorization = ('Basic', (superuser.username, superuser.clear_password))

    user = User.objects.create(username='john.doe', email='john.doe@example.com', ou=get_default_ou())

    # wrong type
    resp = app.put('/api/users/%s/profiles/non-existant-slug/' % user.uuid, status=404)

    profile_type = ProfileType.objects.create(slug='one-type', name='One Type')

    # wrong user
    resp = app.put('/api/users/1234/profiles/one-type/', status=404)

    # missing profile
    resp = app.put_json(
        '/api/users/%s/profiles/one-type/' % user.uuid, params={'data': {'foo': 'bar'}}, status=404
    )

    Profile.objects.create(user=user, profile_type=profile_type, email='manager456@example.org', data={})

    resp = app.put_json(
        '/api/users/%s/profiles/one-type/' % user.uuid,
        params={'data': {'foo': 'bar'}, 'email': 'user789@example.org'},
    )
    assert resp.json == {'result': 1, 'detail': 'Profile successfully updated'}
    assert len(user.profiles.all()) == 1
    assert user.profiles.last().data == {'foo': 'bar'}
    assert user.profiles.last().email == 'user789@example.org'

    # attempt at overwriting profile data
    resp = app.put_json('/api/users/%s/profiles/one-type/' % user.uuid, params={'data': {'baz': 'bob'}})

    # profile has been updated, no extra profile has been created
    assert resp.json == {'result': 1, 'detail': 'Profile successfully updated'}
    assert len(user.profiles.all()) == 1
    assert user.profiles.last().data == {'baz': 'bob'}

    Profile.objects.create(
        user=user,
        profile_type=profile_type,
        identifier='Company DEF',
        data={},
    )

    # overwrite profile data while specifying the identifier
    resp = app.put_json(
        '/api/users/%s/profiles/one-type/?identifier=Company DEF' % user.uuid,
        params={'data': {'baz': 'bob'}},
    )
    assert resp.json == {'result': 1, 'detail': 'Profile successfully updated'}
    assert user.profiles.get(identifier='Company DEF').data == {'baz': 'bob'}
    assert user.profiles.get(identifier='Company DEF').email == ''


def test_user_profile_post(app, superuser):
    app.authorization = ('Basic', (superuser.username, superuser.clear_password))

    user = User.objects.create(username='john.doe', email='john.doe@example.com', ou=get_default_ou())

    # wrong type
    resp = app.get('/api/users/%s/profiles/non-existant-slug/' % user.uuid, status=404)

    ProfileType.objects.create(slug='one-type', name='One Type')

    # wrong user
    resp = app.get('/api/users/1234/profiles/one-type/', status=404)

    assert len(user.profiles.all()) == 0

    resp = app.post_json(
        '/api/users/%s/profiles/one-type/' % user.uuid,
        params={'data': {'baz': 'bob'}, 'email': 'user321@example.org'},
    )
    assert resp.json == {'result': 1, 'detail': 'Profile successfully assigned to user'}
    assert len(user.profiles.all()) == 1
    assert user.profiles.last().data == {'baz': 'bob'}
    assert user.profiles.last().email == 'user321@example.org'

    app.post_json('/api/users/%s/profiles/one-type/' % user.uuid, status=400)

    resp = app.post_json(
        '/api/users/%s/profiles/one-type/?identifier=FooBar' % user.uuid,
        params={'data': {'foo': 'bar'}, 'email': ''},
    )
    assert resp.json == {'result': 1, 'detail': 'Profile successfully assigned to user'}
    assert len(user.profiles.all()) == 2
    assert user.profiles.get(identifier='FooBar').data == {'foo': 'bar'}
    assert user.profiles.get(identifier='FooBar').email == ''

    app.post_json('/api/users/%s/profiles/one-type/?identifier=FooBar' % user.uuid, status=400)


def test_user_profile_delete(app, superuser):
    app.authorization = ('Basic', (superuser.username, superuser.clear_password))

    user = User.objects.create(username='john.doe', email='john.doe@example.com', ou=get_default_ou())
    profile_type = ProfileType.objects.create(slug='one-type', name='One Type')
    Profile.objects.create(user=user, profile_type=profile_type, email='user-abc@example.org')

    app.delete('/api/users/%s/profiles/non-existant-slug/' % user.uuid, status=404)
    app.delete('/api/users/%s/profiles/one-type/' % user.uuid)

    assert not Profile.objects.filter(user=user, profile_type=profile_type)

    app.delete('/api/users/%s/profiles/one-type/' % user.uuid, status=404)

    Profile.objects.create(
        user=user, profile_type=profile_type, identifier='FooBar', email='manager-def@example.org'
    )
    app.delete('/api/users/%s/profiles/one-type/?identifier=FooBar' % user.uuid)
    app.delete(
        '/api/users/%s/profiles/one-type/?identifier=FooBar' % user.uuid,
        status=404,
    )
