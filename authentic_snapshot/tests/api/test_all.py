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


import datetime
import json
from unittest import mock

import faker
import pytest
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core import mail
from django.test.utils import override_settings
from django.urls import reverse
from django.utils.encoding import force_str
from django.utils.timezone import now
from requests.models import Response

from authentic2 import app_settings as a2_app_settings
from authentic2.a2_rbac.models import VIEW_OP
from authentic2.a2_rbac.models import OrganizationalUnit as OU
from authentic2.a2_rbac.models import Permission, Role
from authentic2.a2_rbac.utils import get_default_ou, get_operation, get_view_user_perm
from authentic2.models import APIClient, Attribute, AttributeValue, PasswordReset, Service
from authentic2.utils.misc import good_next_url

from ..utils import USER_ATTRIBUTES_SET, assert_event, basic_authorization_header, get_link_from_mail, login

pytestmark = pytest.mark.django_db

User = get_user_model()


def test_api_user_simple(logged_app):
    resp = logged_app.get('/api/user/')
    assert isinstance(resp.json, dict)
    assert 'username' in resp.json


def test_api_user(client):
    # create an user, an ou role, a service and a service role
    ou = get_default_ou()

    Attribute.objects.create(kind='birthdate', name='birthdate', label='birthdate', required=True)
    user = User.objects.create(
        ou=ou, username='john.doe', first_name='Jôhn', last_name='Doe', email='john.doe@example.net'
    )
    user.attributes.birthdate = datetime.date(2019, 2, 2)
    user.set_password('password')
    user.save()
    assert user.password != 'password'

    role1 = Role.objects.create(name='Role1', ou=ou)
    role1.members.add(user)

    service = Service.objects.create(name='Service1', slug='service1', ou=ou)
    role2 = Role.objects.create(name='Role2', service=service)
    role2.members.add(user)

    Role.objects.create(name='Role3', ou=ou)
    Role.objects.create(name='Role4', service=service)

    # test failure when unlogged
    response = client.get('/api/user/', HTTP_ORIGIN='https://testserver', secure=True)
    assert response.content == b'{}'

    # login
    client.login(request=None, username='john.doe', password='password')
    response = client.get('/api/user/', HTTP_ORIGIN='https://testserver', secure=True)
    data = json.loads(force_str(response.content))
    assert isinstance(data, dict)
    assert set(data.keys()) == {
        'uuid',
        'username',
        'first_name',
        'ou__slug',
        'ou__uuid',
        'ou__name',
        'last_name',
        'email',
        'roles',
        'services',
        'is_superuser',
        'ou',
        'birthdate',
    }
    assert data['uuid'] == user.uuid
    assert data['username'] == user.username
    assert data['first_name'] == user.first_name
    assert data['last_name'] == user.last_name
    assert data['email'] == user.email
    assert data['is_superuser'] == user.is_superuser
    assert data['ou'] == ou.name
    assert data['ou__name'] == ou.name
    assert data['ou__slug'] == ou.slug
    assert data['ou__uuid'] == ou.uuid
    assert data['birthdate'] == '2019-02-02'
    assert isinstance(data['roles'], list)
    assert len(data['roles']) == 2
    for role in data['roles']:
        assert set(role.keys()) == {
            'uuid',
            'name',
            'slug',
            'is_admin',
            'is_service',
            'ou__uuid',
            'ou__name',
            'ou__slug',
        }
        assert (
            role['uuid'] == role1.uuid
            and role['name'] == role1.name
            and role['slug'] == role1.slug
            and role['is_admin'] is False
            and role['is_service'] is False
            and role['ou__uuid'] == ou.uuid
            and role['ou__name'] == ou.name
            and role['ou__slug'] == ou.slug
        ) or (
            role['uuid'] == role2.uuid
            and role['name'] == role2.name
            and role['slug'] == role2.slug
            and role['is_admin'] is False
            and role['is_service'] is True
            and role['ou__uuid'] == ou.uuid
            and role['ou__name'] == ou.name
            and role['ou__slug'] == ou.slug
        )

    assert isinstance(data['services'], list)
    assert len(data['services']) == 1
    s = data['services'][0]
    assert set(s.keys()) == {'name', 'slug', 'ou', 'ou__name', 'ou__slug', 'ou__uuid', 'roles'}
    assert s['name'] == service.name
    assert s['slug'] == service.slug
    assert s['ou'] == ou.name
    assert s['ou__name'] == ou.name
    assert s['ou__slug'] == ou.slug
    assert s['ou__uuid'] == ou.uuid
    assert isinstance(s['roles'], list)
    assert len(s['roles']) == 2
    for role in s['roles']:
        assert set(role.keys()) == {
            'uuid',
            'name',
            'slug',
            'is_admin',
            'is_service',
            'ou__uuid',
            'ou__name',
            'ou__slug',
        }
        assert (
            role['uuid'] == role1.uuid
            and role['name'] == role1.name
            and role['slug'] == role1.slug
            and role['is_admin'] is False
            and role['is_service'] is False
            and role['ou__uuid'] == ou.uuid
            and role['ou__name'] == ou.name
            and role['ou__slug'] == ou.slug
        ) or (
            role['uuid'] == role2.uuid
            and role['name'] == role2.name
            and role['slug'] == role2.slug
            and role['is_admin'] is False
            and role['is_service'] is True
            and role['ou__uuid'] == ou.uuid
            and role['ou__name'] == ou.name
            and role['ou__slug'] == ou.slug
        )


def test_api_users_list(app, user):
    app.authorization = ('Basic', (user.username, user.clear_password))
    resp = app.get('/api/users/')
    assert isinstance(resp.json, dict)
    assert {'previous', 'next', 'results'} == set(resp.json.keys())
    assert resp.json['previous'] is None
    assert resp.json['next'] is None
    if resp.json['results']:
        assert resp.json['results'][0]['full_name']


def test_api_users_list_limit(app, superuser):
    app.authorization = ('Basic', (superuser.username, superuser.clear_password))
    for i in range(0, 201):
        User.objects.create(first_name='User%s' % i, email=faker.Faker().email())
    resp = app.get('/api/users/')
    assert resp.json['next']
    next_url = resp.json['next']

    with override_settings(A2_API_USERS_NUMBER_LIMIT=200):
        resp = app.get(next_url)
        next_url = resp.json['next']
        # when users number limit is reached DRF returns same next url
        assert next_url == resp.json['next']


def test_api_users_update_with_email_verified(settings, app, admin, simple_user):
    simple_user.set_email_verified(True)
    simple_user.save()

    payload = {
        'username': simple_user.username,
        'id': simple_user.id,
        'email': 'john.doe@nowhere.null',
        'first_name': 'Johnny',
        'last_name': 'Doeny',
        'email_verified': True,
    }
    headers = basic_authorization_header(admin)
    resp = app.put_json(f'/api/users/{simple_user.uuid}/', params=payload, headers=headers, status=200)
    user = User.objects.get(id=simple_user.id)
    assert user.email_verified
    assert resp.json['email_verified']
    assert_event('manager.user.profile.edit', user=admin, api=True)

    user.set_email_verified(True)
    user.email = 'johnny.doeny@foo.bar'
    user.save()

    resp = app.patch_json(f'/api/users/{simple_user.uuid}/', params=payload, headers=headers, status=200)
    user = User.objects.get(id=simple_user.id)
    assert user.email_verified
    assert resp.json['email_verified']


def test_api_users_update_without_email_verified(settings, app, admin, simple_user):
    simple_user.set_email_verified(True)
    simple_user.save()

    payload = {
        'username': simple_user.username,
        'id': simple_user.id,
        'email': 'john.doe@nowhere.null',
        'first_name': 'Johnny',
        'last_name': 'Doeny',
    }
    headers = basic_authorization_header(admin)
    resp = app.put_json(f'/api/users/{simple_user.uuid}/', params=payload, headers=headers, status=200)
    user = User.objects.get(id=simple_user.id)
    assert not user.email_verified
    assert not resp.json['email_verified']

    user.set_email_verified(True)
    user.email = 'johnny.doeny@foo.bar'
    user.save()

    resp = app.patch_json(f'/api/users/{simple_user.uuid}/', params=payload, headers=headers, status=200)
    user = User.objects.get(id=simple_user.id)
    assert not user.email_verified
    assert not resp.json['email_verified']


def test_api_users_update_with_same_unique_email(settings, app, admin, simple_user):
    ou = get_default_ou()
    ou.email_is_unique = True
    ou.save()

    payload = {
        'username': simple_user.username,
        'id': simple_user.id,
        'email': 'john.doe@nowhere.null',
        'first_name': 'Johnny',
        'last_name': 'Doeny',
    }
    headers = basic_authorization_header(admin)
    app.put_json(f'/api/users/{simple_user.uuid}/', params=payload, headers=headers, status=200)
    User.objects.get(id=simple_user.id)

    app.patch_json(f'/api/users/{simple_user.uuid}/', params=payload, headers=headers, status=200)


def test_api_users_create_with_email_verified(settings, app, admin):
    payload = {
        'username': 'janedoe',
        'email': 'jane.doe@nowhere.null',
        'first_name': 'Jane',
        'last_name': 'Doe',
        'email_verified': True,
    }
    headers = basic_authorization_header(admin)
    resp = app.post_json('/api/users/', headers=headers, params=payload, status=201)
    assert resp.json['email_verified']
    user = User.objects.get(uuid=resp.json['uuid'])
    assert user.email_verified
    assert_event('manager.user.creation', user=admin, api=True)


def test_api_users_create_without_email_verified(settings, app, admin):
    payload = {
        'username': 'janedoe',
        'email': 'jane.doe@nowhere.null',
        'first_name': 'Jane',
        'last_name': 'Doe',
    }
    headers = basic_authorization_header(admin)
    resp = app.post_json('/api/users/', headers=headers, params=payload, status=201)
    assert not resp.json['email_verified']
    user = User.objects.get(uuid=resp.json['uuid'])
    assert not user.email_verified


def test_api_email_unset_verification(settings, app, admin, simple_user):
    simple_user.set_email_verified(True)
    simple_user.save()

    payload = {
        'email': 'john.doe@nowhere.null',
    }
    headers = basic_authorization_header(admin)
    app.post_json(f'/api/users/{simple_user.uuid}/email/', params=payload, headers=headers, status=200)
    user = User.objects.get(id=simple_user.id)
    assert not user.email_verified


def test_api_users_boolean_attribute(app, superuser):
    Attribute.objects.create(kind='boolean', name='boolean', label='boolean', required=True)
    superuser.attributes.boolean = True
    app.authorization = ('Basic', (superuser.username, superuser.clear_password))
    resp = app.get('/api/users/%s/' % superuser.uuid)
    assert resp.json['boolean'] is True


def test_api_users_boolean_attribute_optional(app, superuser):
    Attribute.objects.create(kind='boolean', name='boolean', label='boolean', required=False)
    superuser.attributes.boolean = True
    app.authorization = ('Basic', (superuser.username, superuser.clear_password))
    resp = app.get('/api/users/%s/' % superuser.uuid)
    assert resp.json['boolean'] is True


def test_api_users_list_by_authorized_service(app, superuser):
    app.authorization = ('Basic', (superuser.username, superuser.clear_password))

    user1 = User.objects.create(username='user1')
    user2 = User.objects.create(username='user2')
    User.objects.create(username='user3')

    role1 = Role.objects.create(name='role1')
    role2 = Role.objects.create(name='role2')
    role1.add_child(role2)
    user1.roles.set([role1])
    user2.roles.set([role2])

    service1 = Service.objects.create(ou=get_default_ou(), name='service1', slug='service1')
    service1.add_authorized_role(role1)

    Service.objects.create(ou=get_default_ou(), name='service2', slug='service2')

    resp = app.get('/api/users/')
    assert len(resp.json['results']) == 4

    resp = app.get('/api/users/?service-ou=default&service-slug=service1')
    assert len(resp.json['results']) == 2
    assert {user['username'] for user in resp.json['results']} == {'user1', 'user2'}

    resp = app.get('/api/users/?service-ou=default&service-slug=service2')
    assert len(resp.json['results']) == 4


def test_api_users_list_search_text(app, superuser):
    app.authorization = ('Basic', (superuser.username, superuser.clear_password))
    User = get_user_model()
    someuser = User.objects.create(username='someuser')
    resp = app.get('/api/users/?q=some')
    results = resp.json['results']
    assert len(results) == 1
    assert results[0]['username'] == 'someuser'
    someuser.delete()

    resp = app.get('/api/users/?q=some')
    results = resp.json['results']
    assert len(results) == 0


def test_api_users_create(settings, app, api_user):
    from authentic2_idp_oidc.models import OIDCAuthorization, OIDCClient

    at = Attribute.objects.create(kind='title', name='title', label='title')
    app.authorization = ('Basic', (api_user.username, api_user.clear_password))
    # test missing first_name
    payload = {
        'username': 'john.doe',
        'email': 'john.doe@example.net',
    }
    if api_user.roles.exists():
        status = 400
        payload['ou'] = api_user.ou.slug
    resp = app.post_json('/api/users/', params=payload, status=400)
    assert resp.json['result'] == 0
    assert {'first_name', 'last_name'} == set(resp.json['errors'])
    settings.A2_API_USERS_REQUIRED_FIELDS = ['email']
    if api_user.is_superuser or hasattr(api_user, 'oidc_client') or api_user.roles.exists():
        status = 201
    else:
        status = 403
    resp = app.post_json('/api/users/', params=payload, status=status)
    if status == 201:
        assert resp.json
    del settings.A2_API_USERS_REQUIRED_FIELDS

    payload = {
        'username': 'john.doe',
        'first_name': 'John',
        'last_name': 'Doe',
        'email': 'john.doe@example.net',
        'password': 'password',
        'title': 'Mr',
    }
    if api_user.is_superuser:
        status = 201
    elif api_user.roles.exists():
        status = 201
        payload['ou'] = api_user.ou.slug
    else:
        status = 403

    resp = app.post_json('/api/users/', params=payload, status=status)
    if api_user.is_superuser or api_user.roles.exists():
        assert (USER_ATTRIBUTES_SET | {'title', 'title_verified'}) == set(resp.json)
        assert resp.json['first_name'] == payload['first_name']
        assert resp.json['last_name'] == payload['last_name']
        assert resp.json['email'] == payload['email']
        assert resp.json['username'] == payload['username']
        assert resp.json['title'] == payload['title']
        assert resp.json['uuid']
        assert resp.json['id']
        assert resp.json['date_joined']
        assert not resp.json['first_name_verified']
        assert not resp.json['last_name_verified']
        assert not resp.json['title_verified']
        if api_user.is_superuser:
            assert resp.json['ou'] == 'default'
        elif api_user.roles.exists():
            assert resp.json['ou'] == api_user.ou.slug
        new_user = get_user_model().objects.get(id=resp.json['id'])
        assert new_user.uuid == resp.json['uuid']
        assert new_user.username == resp.json['username']
        assert new_user.email == resp.json['email']
        assert new_user.first_name == resp.json['first_name']
        assert new_user.last_name == resp.json['last_name']
        assert AttributeValue.objects.with_owner(new_user).count() == 3
        assert AttributeValue.objects.with_owner(new_user).filter(verified=True).count() == 0
        assert AttributeValue.objects.with_owner(new_user).filter(attribute=at).exists()
        assert AttributeValue.objects.with_owner(new_user).get(attribute=at).content == payload['title']
        # Check that password is hashed
        assert User.objects.get(uuid=resp.json['uuid']).check_password('password')
        assert User.objects.get(uuid=resp.json['uuid']).password != 'password'

        if (
            hasattr(api_user, 'oidc_client')
            and api_user.oidc_client.authorization_mode != OIDCClient.AUTHORIZATION_MODE_NONE
        ):
            if api_user.oidc_client.authorization_mode == OIDCClient.AUTHORIZATION_MODE_BY_SERVICE:
                client_id = api_user.oidc_client.id
                client_ct = ContentType.objects.get_for_model(OIDCClient)
            else:
                client_id = api_user.oidc_client.ou.id
                client_ct = ContentType.objects.get_for_model(OU)
            OIDCAuthorization.objects.create(
                user=new_user,
                client_id=client_id,
                client_ct=client_ct,
                expired=now() + datetime.timedelta(hours=1),
            )
        resp2 = app.get('/api/users/%s/' % resp.json['uuid'])
        assert resp.json == resp2.json
        payload.update({'uuid': '1234567890', 'email': 'foo@example.com', 'username': 'foobar'})
        resp = app.post_json('/api/users/', params=payload, status=status)
        assert resp.json['uuid'] == '1234567890'
        assert 'title' in resp.json
        at.disabled = True
        at.save()
        if (
            hasattr(api_user, 'oidc_client')
            and api_user.oidc_client.authorization_mode != OIDCClient.AUTHORIZATION_MODE_NONE
        ):
            authz = OIDCAuthorization.objects.get(user=new_user)
            authz.user = User.objects.get(uuid='1234567890')
            authz.save()
        resp = app.get('/api/users/1234567890/')
        assert 'title' not in resp.json

    at.disabled = False
    at.save()
    payload = {
        'username': 'john.doe2',
        'first_name': 'John',
        'first_name_verified': True,
        'last_name': 'Doe',
        'last_name_verified': True,
        'email': 'john.doe@example.net',
        'password': 'secret',
        'title': 'Mr',
        'title_verified': True,
    }
    if api_user.is_superuser:
        status = 201
    elif api_user.roles.exists():
        status = 201
        payload['ou'] = api_user.ou.slug
    else:
        status = 403

    resp = app.post_json('/api/users/', params=payload, status=status)
    if api_user.is_superuser or api_user.roles.exists():
        assert (USER_ATTRIBUTES_SET | {'title', 'title_verified'}) == set(resp.json)
        user = get_user_model().objects.get(pk=resp.json['id'])
        assert AttributeValue.objects.with_owner(user).filter(verified=True).count() == 3
        assert AttributeValue.objects.with_owner(user).filter(verified=False).count() == 0
        assert user.verified_attributes.first_name == 'John'
        assert user.verified_attributes.last_name == 'Doe'
        assert user.verified_attributes.title == 'Mr'
        first_name = Attribute.objects.get(name='first_name')
        last_name = Attribute.objects.get(name='last_name')
        title = Attribute.objects.get(name='title')
        first_name_value = AttributeValue.objects.with_owner(user).get(attribute=first_name)
        last_name_value = AttributeValue.objects.with_owner(user).get(attribute=last_name)
        title_value = AttributeValue.objects.with_owner(user).get(attribute=title)
        assert first_name_value.last_verified_on
        assert last_name_value.last_verified_on
        assert title_value.last_verified_on
        assert resp.json['first_name_verified']
        assert resp.json['last_name_verified']
        assert resp.json['title_verified']
        resp2 = app.patch_json('/api/users/%s/' % resp.json['uuid'], params={'title_verified': False})
        assert resp.json['first_name_verified']
        assert resp.json['last_name_verified']
        assert not resp2.json['title_verified']
        # Check that password is hashed
        assert User.objects.get(uuid=resp.json['uuid']).check_password('secret')
        assert User.objects.get(uuid=resp.json['uuid']).password != 'secret'


def test_api_users_create_email_is_unique(settings, app, superuser):
    ou1 = OU.objects.create(name='OU1', slug='ou1')
    ou2 = OU.objects.create(name='OU2', slug='ou2', email_is_unique=True)

    app.authorization = ('Basic', (superuser.username, superuser.clear_password))
    # test missing first_name
    payload = {
        'ou': 'ou1',
        'first_name': 'John',
        'last_name': 'Doe',
        'email': 'john.doe@example.net',
    }

    assert User.objects.filter(ou=ou1).count() == 0
    assert User.objects.filter(ou=ou2).count() == 0

    app.post_json('/api/users/', params=payload)
    assert User.objects.filter(ou=ou1).count() == 1

    app.post_json('/api/users/', params=payload)
    assert User.objects.filter(ou=ou1).count() == 2

    payload['ou'] = 'ou2'
    app.post_json('/api/users/', params=payload)
    assert User.objects.filter(ou=ou2).count() == 1

    resp = app.post_json('/api/users/', params=payload, status=400)
    assert User.objects.filter(ou=ou2).count() == 1
    assert resp.json['result'] == 0
    assert resp.json['errors']['email']

    settings.A2_EMAIL_IS_UNIQUE = True
    User.objects.filter(ou=ou1).delete()
    assert User.objects.filter(ou=ou1).count() == 0
    payload['ou'] = 'ou1'
    app.post_json('/api/users/', params=payload, status=400)
    assert User.objects.filter(ou=ou1).count() == 0
    assert resp.json['result'] == 0
    assert resp.json['errors']['email']

    payload['email'] = 'john.doe2@example.net'
    resp = app.post_json('/api/users/', params=payload)
    uuid = resp.json['uuid']

    app.patch_json('/api/users/%s/' % uuid, params={'email': 'john.doe3@example.net'})
    resp = app.patch_json('/api/users/%s/' % uuid, params={'email': 'john.doe@example.net'}, status=400)
    assert resp.json['result'] == 0
    assert resp.json['errors']['email']
    settings.A2_EMAIL_IS_UNIQUE = False

    payload['ou'] = 'ou2'
    payload['email'] = 'john.doe2@example.net'
    resp = app.post_json('/api/users/', params=payload)
    assert User.objects.filter(ou=ou2).count() == 2
    uuid = resp.json['uuid']
    resp = app.patch_json('/api/users/%s/' % uuid, params={'email': 'john.doe@example.net'}, status=400)
    assert resp.json['result'] == 0
    assert resp.json['errors']['email']


def test_api_users_create_send_mail(app, settings, superuser, rf):
    # Use case is often that Email is the main identifier
    settings.A2_EMAIL_IS_UNIQUE = True
    Attribute.objects.create(kind='title', name='title', label='title')

    app.authorization = ('Basic', (superuser.username, superuser.clear_password))
    payload = {
        'username': 'john.doe',
        'first_name': 'John',
        'last_name': 'Doe',
        'email': 'john.doe@example.net',
        'title': 'Mr',
        'send_registration_email': True,
        'send_registration_email_next_url': 'http://example.com/',
    }
    assert len(mail.outbox) == 0
    resp = app.post_json('/api/users/', params=payload, status=201)
    user_id = resp.json['id']
    assert len(mail.outbox) == 1
    # Follow activation link
    url = get_link_from_mail(mail.outbox[0])
    relative_url = url.split('testserver')[1]
    resp = app.get(relative_url, status=200)
    new_password = '1234==aA12312'
    resp.form.set('new_password1', new_password)
    resp.form.set('new_password2', new_password)
    resp = resp.form.submit()
    # Check user was properly logged in
    assert str(app.session['_auth_user_id']) == str(user_id)
    assert not good_next_url(rf.get('/'), 'http://example.com')
    assert resp.location == 'http://example.com/'
    # Check password is hashed
    assert User.objects.get(username='john.doe').check_password(new_password)
    assert User.objects.get(username='john.doe').password != new_password


def test_api_users_create_force_password_reset(app, client, settings, superuser):
    app.authorization = ('Basic', (superuser.username, superuser.clear_password))
    payload = {
        'username': 'john.doe',
        'first_name': 'John',
        'last_name': 'Doe',
        'email': 'john.doe@example.net',
        'password': '1234',
        'force_password_reset': True,
    }
    app.post_json('/api/users/', params=payload, status=201)
    # Verify password reset is enforced on next login
    resp = login(app, 'john.doe', path='/', password='1234').follow()
    resp.form.set('old_password', '1234')
    new_password = '1234==aA12312'
    resp.form.set('new_password1', new_password)
    resp.form.set('new_password2', new_password)
    resp = resp.form.submit('Submit').follow().maybe_follow()
    assert 'Password changed' in resp
    assert User.objects.get(username='john.doe').check_password(new_password)
    assert User.objects.get(username='john.doe').password != new_password


def test_api_drf_authentication_class(app, admin, user_ou1, oidc_client):
    from authentic2_idp_oidc.models import OIDCAuthorization, OIDCClient

    url = '/api/users/%s/' % user_ou1.uuid
    # test invalid client
    app.authorization = ('Basic', ('foo', 'bar'))
    resp = app.get(url, status=401)
    assert resp.json['result'] == 0
    assert resp.json['errors'] == 'Invalid username/password.'
    # test inactive client
    admin.is_active = False
    admin.save()
    app.authorization = ('Basic', (admin.username, admin.clear_password))
    resp = app.get(url, status=401)
    assert resp.json['result'] == 0
    assert resp.json['errors'] == 'User inactive or deleted.'
    # test oidc client unauthorized for user_ou1
    app.authorization = ('Basic', (oidc_client.username, oidc_client.clear_password))
    app.get(url, status=404)
    OIDCAuthorization.objects.create(
        client_id=oidc_client.id,
        client_ct=ContentType.objects.get_for_model(OIDCClient),
        user=user_ou1,
        expired=now() + datetime.timedelta(hours=1),
    )
    # test oidc client
    app.get(url, status=200)
    # test oidc client without has API access
    oidc_client.oidc_client.has_api_access = False
    oidc_client.oidc_client.save()
    app.authorization = ('Basic', (oidc_client.username, oidc_client.clear_password))
    response = app.get(url, status=401)
    assert response.json['result'] == 0
    assert response.json['errors']


def test_api_check_password(app, superuser, oidc_client, user_ou1):
    app.authorization = ('Basic', (superuser.username, superuser.clear_password))
    # test with invalid paylaod
    payload = {'username': 'whatever'}
    resp = app.post_json(reverse('a2-api-check-password'), params=payload, status=400)
    assert resp.json['result'] == 0
    assert resp.json['errors'] == {'password': ['This field is required.']}
    # test with invalid credentials
    payload = {'username': 'whatever', 'password': 'password'}
    resp = app.post_json(reverse('a2-api-check-password'), params=payload, status=200)
    assert resp.json['result'] == 0
    assert resp.json['errors'] == ['Invalid username/password.']
    # test with valid credentials
    payload = {'username': user_ou1.username, 'password': user_ou1.clear_password}
    resp = app.post_json(reverse('a2-api-check-password'), params=payload, status=200)
    assert resp.json['result'] == 1
    # test valid oidc credentials
    payload = {
        'username': oidc_client.oidc_client.client_id,
        'password': oidc_client.oidc_client.client_secret,
    }
    resp = app.post_json(reverse('a2-api-check-password'), params=payload, status=200)
    assert resp.json['result'] == 1
    assert resp.json['oidc_client'] is True


def test_password_change(app, ou1, admin):
    app.authorization = ('Basic', (admin.username, admin.clear_password))

    user1 = User(username='john.doe', email='john.doe@example.com', ou=ou1)
    user1.set_password('password')
    user1.save()
    user2 = User(username='john.doe2', email='john.doe@example.com', ou=ou1)
    user2.set_password('password')
    user2.save()

    payload = {
        'email': 'none@example.com',
        'ou': ou1.slug,
        'old_password': 'password',
        'new_password': 'password2',
    }
    url = reverse('a2-api-password-change')
    response = app.post_json(url, params=payload, status=400)
    assert 'errors' in response.json
    assert response.json['result'] == 0

    payload = {
        'email': 'john.doe@example.com',
        'ou': ou1.slug,
        'old_password': 'password',
        'new_password': 'password2',
    }
    response = app.post_json(url, params=payload, status=400)
    assert 'errors' in response.json
    assert response.json['result'] == 0
    user2.delete()

    response = app.post_json(url, params=payload)
    assert response.json['result'] == 1
    assert User.objects.get(username='john.doe').check_password('password2')
    assert User.objects.get(username='john.doe').password != 'password2'
    assert_event('manager.user.password.change', user=admin, api=True)


def test_password_reset(app, ou1, admin, user_ou1, mailoutbox):
    email = user_ou1.email
    url = reverse('a2-api-users-password-reset', kwargs={'uuid': user_ou1.uuid})
    app.authorization = ('Basic', (user_ou1.username, user_ou1.clear_password))
    app.post(url, status=403)
    app.authorization = ('Basic', (admin.username, admin.clear_password))
    app.get(url, status=405)
    user_ou1.email = ''
    user_ou1.save()
    resp = app.post(url, status=500)
    assert resp.json['result'] == 0
    assert resp.json['reason'] == 'User has no mail'
    user_ou1.email = email
    user_ou1.save()
    app.post(url, status=204)
    assert len(mailoutbox) == 1
    mail = mailoutbox[0]
    assert mail.to[0] == email
    assert 'https://testserver/password/reset/confirm/' in mail.body
    assert_event('manager.user.password.reset.request', user=admin, api=True)


def test_force_password_reset(app, ou1, admin, user_ou1, mailoutbox):
    url = reverse('a2-api-users-force-password-reset', kwargs={'uuid': user_ou1.uuid})
    app.authorization = ('Basic', (user_ou1.username, user_ou1.clear_password))
    app.post(url, status=403)
    app.authorization = ('Basic', (admin.username, admin.clear_password))
    app.get(url, status=405)
    app.post(url, status=204)
    assert_event('manager.user.password.change.force', user=admin, api=True)
    assert PasswordReset.objects.filter(user=user_ou1).exists()


def test_users_email(app, ou1, admin, user_ou1, mailoutbox):
    url = reverse('a2-api-users-email', kwargs={'uuid': user_ou1.uuid})
    # test access error
    app.authorization = ('Basic', (user_ou1.username, user_ou1.clear_password))
    app.post(url, status=403)

    # test method error
    app.authorization = ('Basic', (admin.username, admin.clear_password))
    app.get(url, status=405)

    new_email = 'newmail@yopmail.com'
    resp = app.post_json(url, params={'email': new_email})
    assert resp.json['result'] == 1

    assert len(mailoutbox) == 1
    mail = mailoutbox[0]

    assert mail.to[0] == new_email
    assert 'https://testserver/accounts/change-email/verify/' in mail.body


def test_no_opened_session_cookie_on_api(app, user, settings):
    settings.A2_OPENED_SESSION_COOKIE_DOMAIN = 'testserver.local'
    app.authorization = ('Basic', (user.username, user.clear_password))
    app.get('/api/users/')
    assert 'A2_OPENED_SESSION' not in app.cookies


def test_api_users_hashed_password(settings, app, admin):
    app.authorization = ('Basic', (admin.username, admin.clear_password))
    payload = {
        'email': 'john.doe@example.net',
        'first_name': 'John',
        'last_name': 'Doe',
        'hashed_password': 'pbkdf2_sha256$36000$re9zaUj1ize0$bX1cqB91ni4aMOtRh8//TLaJkX+xnD2w84MCQx9AJcE=',
    }
    resp = app.post_json('/api/users/?get_or_create=email', params=payload, status=201)
    id = resp.json['id']
    assert User.objects.get(id=id).first_name == 'John'
    assert User.objects.get(id=id).last_name == 'Doe'
    assert User.objects.get(id=id).check_password('admin')
    password = User.objects.get(id=id).password

    payload['hashed_password'] = 'sha-oldap$$e5e9fa1ba31ecd1ae84f75caaa474f3a663f05f4'
    resp = app.post_json('/api/users/?update_or_create=email', params=payload, status=200)
    assert User.objects.get(id=id).password != password
    assert User.objects.get(id=id).check_password('secret')

    payload['password'] = 'secret'
    resp = app.post_json('/api/users/?update_or_create=email', params=payload, status=400)
    assert resp.json['result'] == 0
    assert resp.json['errors']['password'] == ['conflict with provided hashed_password']

    del payload['password']
    payload['hashed_password'] = 'unknown_format'
    resp = app.post_json('/api/users/?update_or_create=email', params=payload, status=400)
    assert resp.json['result'] == 0
    assert resp.json['errors']['hashed_password'] == ['unknown hash format']

    payload['hashed_password'] = 'argon2$wrong_format'
    resp = app.post_json('/api/users/?update_or_create=email', params=payload, status=400)
    assert resp.json['result'] == 0
    assert resp.json['errors']['hashed_password'] == ['hash format error']


def test_api_users_required_attribute(settings, app, admin, simple_user):
    assert Attribute.objects.get(name='last_name').required is True

    payload = {
        'username': simple_user.username,
        'id': simple_user.id,
        'email': 'john.doe@nowhere.null',
        'first_name': 'Johnny',
    }
    headers = basic_authorization_header(admin)

    # create fails
    resp = app.post_json('/api/users/', params=payload, headers=headers, status=400)
    assert resp.json['result'] == 0
    assert resp.json['errors']['last_name'] == ['This field is required.']

    # update from missing value to blank field fails
    payload['last_name'] = ''
    resp = app.put_json(f'/api/users/{simple_user.uuid}/', params=payload, headers=headers, status=400)
    assert resp.json['result'] == 0
    assert resp.json['errors']['last_name'] == ['This field may not be blank.']

    # update with value pass
    payload['last_name'] = 'Foobar'
    resp = app.put_json(f'/api/users/{simple_user.uuid}/', params=payload, headers=headers, status=200)
    user = User.objects.get(id=simple_user.id)
    assert user.last_name == 'Foobar'

    # update from non-empty value to blank fails
    payload['last_name'] = ''
    resp = app.put_json(f'/api/users/{simple_user.uuid}/', params=payload, headers=headers, status=400)
    assert resp.json['result'] == 0
    assert resp.json['errors']['last_name'] == ['This field may not be blank.']


def test_api_users_required_date_attributes(settings, app, admin, simple_user):
    Attribute.objects.create(kind='string', name='prefered_color', label='prefered color', required=True)
    Attribute.objects.create(kind='date', name='date', label='date', required=True)
    Attribute.objects.create(kind='birthdate', name='birthdate', label='birthdate', required=True)

    payload = {
        'username': simple_user.username,
        'id': simple_user.id,
        'email': 'john.doe@nowhere.null',
        'first_name': 'Johnny',
        'last_name': 'Doe',
    }
    headers = basic_authorization_header(admin)

    # create fails
    resp = app.post_json('/api/users/', params=payload, headers=headers, status=400)
    assert resp.json['result'] == 0
    assert resp.json['errors']['prefered_color'] == ['This field is required.']
    assert resp.json['errors']['date'] == ['This field is required.']
    assert resp.json['errors']['birthdate'] == ['This field is required.']

    # update from missing value to blank fails
    payload['prefered_color'] = ''
    payload['date'] = ''
    payload['birthdate'] = ''
    resp = app.put_json(f'/api/users/{simple_user.uuid}/', params=payload, headers=headers, status=400)
    assert resp.json['result'] == 0
    assert resp.json['errors']['prefered_color'] == ['This field may not be blank.']
    assert resp.json['errors']['date'] == ['This field may not be blank.']
    assert resp.json['errors']['birthdate'] == ['This field may not be blank.']

    # update with invalid values fails
    payload['prefered_color'] = '?' * 257
    payload['date'] = '0000-00-00'
    payload['birthdate'] = '1899-12-31'
    resp = app.put_json(f'/api/users/{simple_user.uuid}/', params=payload, headers=headers, status=400)
    assert resp.json['result'] == 0
    assert resp.json['errors']['prefered_color'] == ['Ensure this field has no more than 256 characters.']
    assert any(error.startswith('Date has wrong format.') for error in resp.json['errors']['date'])
    assert resp.json['errors']['birthdate'] == [
        'birthdate must be in the past and greater or equal than 1900-01-01.'
    ]

    # update with values pass
    del payload['id']
    payload['prefered_color'] = 'blue'
    payload['date'] = '1515-01-15'
    payload['birthdate'] = '1900-02-22'
    resp = app.put_json(f'/api/users/{simple_user.uuid}/', params=payload, headers=headers, status=200)
    assert_event('manager.user.profile.edit', user=admin, api=True, new=payload)

    # value are properly returned on a get
    resp = app.get(f'/api/users/{simple_user.uuid}/', headers=headers, status=200)
    assert resp.json['prefered_color'] == 'blue'
    assert resp.json['date'] == '1515-01-15'
    assert resp.json['birthdate'] == '1900-02-22'


def test_api_users_optional_date_attributes(settings, app, admin, simple_user):
    Attribute.objects.create(kind='string', name='prefered_color', label='prefered color', required=False)
    Attribute.objects.create(kind='date', name='date', label='date', required=False)
    Attribute.objects.create(kind='birthdate', name='birthdate', label='birthdate', required=False)

    payload = {
        'username': simple_user.username,
        'id': simple_user.id,
        'email': 'john.doe@nowhere.null',
        'first_name': 'Johnny',
        'last_name': 'Doe',
    }
    headers = basic_authorization_header(admin)
    app.put_json(f'/api/users/{simple_user.uuid}/', params=payload, headers=headers, status=200)
    app.get(f'/api/users/{simple_user.uuid}/', headers=headers, status=200)
    payload['prefered_color'] = None
    payload['date'] = None
    payload['birthdate'] = None

    payload['prefered_color'] = ''
    payload['date'] = ''
    payload['birthdate'] = ''
    app.put_json(f'/api/users/{simple_user.uuid}/', params=payload, headers=headers, status=200)
    app.get(f'/api/users/{simple_user.uuid}/', headers=headers, status=200)
    payload['prefered_color'] = None
    payload['date'] = None
    payload['birthdate'] = None


class MockedRequestResponse(mock.Mock):
    status_code = 200

    def json(self):
        return json.loads(self.content)


def test_api_address_autocomplete(app, admin, settings):
    app.authorization = ('Basic', (admin.username, admin.clear_password))

    settings.ADDRESS_AUTOCOMPLETE_URL = 'example.com'

    params = {'q': '42 avenue'}
    with mock.patch('authentic2.api_views.requests.get') as requests_get:
        mock_resp = Response()
        mock_resp.status_code = 500
        requests_get.return_value = mock_resp
        resp = app.get('/api/address-autocomplete/', params=params)
    assert resp.json == {}
    assert requests_get.call_args_list[0][0][0] == 'example.com'
    assert requests_get.call_args_list[0][1]['params'] == {'q': ['42 avenue']}
    with mock.patch('authentic2.api_views.requests.get') as requests_get:
        mock_resp = Response()
        mock_resp.status_code = 404
        requests_get.return_value = mock_resp
        resp = app.get('/api/address-autocomplete/', params=params)
    assert resp.json == {}
    with mock.patch('authentic2.api_views.requests.get') as requests_get:
        requests_get.return_value = MockedRequestResponse(content=json.dumps({'data': {'foo': 'bar'}}))
        resp = app.get('/api/address-autocomplete/', params=params)
    assert resp.json == {'data': {'foo': 'bar'}}

    settings.ADDRESS_AUTOCOMPLETE_URL = None
    with mock.patch('authentic2.api_views.requests.get') as requests_get:
        resp = app.get('/api/address-autocomplete/', params=params)
    assert resp.json == {}
    assert requests_get.call_args_list == []

    del settings.ADDRESS_AUTOCOMPLETE_URL
    with mock.patch('authentic2.api_views.requests.get') as requests_get:
        resp = app.get('/api/address-autocomplete/', params=params)
    assert resp.json == {}
    assert requests_get.call_args_list == []


def test_api_users_create_user_delete(app, settings, admin):
    email = 'foo@example.net'
    user1 = User.objects.create(username='foo', email=email)
    user1.delete()
    user2 = User.objects.create(username='foo2', email=email)

    app.authorization = ('Basic', (admin.username, admin.clear_password))
    resp = app.get(f'/api/users/?email={email}')
    assert len(resp.json['results']) == 1

    payload = {
        'username': 'foo3',
        'email': email,
        'first_name': 'John',
        'last_name': 'Doe',
    }
    headers = basic_authorization_header(admin)
    app.post_json('/api/users/', headers=headers, params=payload, status=201)

    resp = app.get(f'/api/users/?email={email}')
    assert len(resp.json['results']) == 2

    user2.delete()
    resp = app.get(f'/api/users/?email={email}')
    assert len(resp.json['results']) == 1

    settings.A2_EMAIL_IS_UNIQUE = True
    # email already used
    resp = app.post_json('/api/users/', headers=headers, params=payload, status=400)
    assert resp.json['errors'] == {'email': ['email already used']}


def test_api_password_change_user_delete(app, settings, admin, ou1):
    app.authorization = ('Basic', (admin.username, admin.clear_password))
    user1 = User.objects.create(username='john.doe', email='john.doe@example.com', ou=ou1)
    user1.set_password('password')
    user1.save()
    user2 = User.objects.create(username='john.doe2', email='john.doe@example.com', ou=ou1)
    user2.set_password('password')
    user1.save()

    payload = {
        'email': 'john.doe@example.com',
        'ou': ou1.slug,
        'old_password': 'password',
        'new_password': 'password2',
    }
    url = reverse('a2-api-password-change')
    app.post_json(url, params=payload, status=400)
    user2.delete()
    app.post_json(url, params=payload)
    assert User.objects.get(username='john.doe').check_password('password2')
    assert User.objects.get(username='john.doe').password != 'password2'


def test_api_users_delete(settings, app, admin, simple_user):
    headers = basic_authorization_header(admin)
    app.delete_json(f'/api/users/{simple_user.uuid}/', headers=headers)
    assert not User.objects.filter(pk=simple_user.pk).exists()
    assert_event('manager.user.deletion', user=admin, api=True)


def test_users_page_size(app, admin):
    app.authorization = ('Basic', (admin.username, admin.clear_password))

    User.objects.create()

    resp = app.get('/api/users/')
    assert len(resp.json['results']) == 2

    resp = app.get('/api/users/?limit=1')
    assert len(resp.json['results']) == 1


def test_user_service_data_accessrights(
    app, oidc_client, superuser, user_ou1, admin_ou1, admin_rando_role, member_rando
):
    from authentic2_idp_oidc.models import OIDCClient

    joe = User.objects.create(username='john.doe', email='john.doe@example.com', ou=get_default_ou())
    OIDCClient.objects.create(
        name='OIDC Client',
        slug='oidc-client',
        sector_identifier_uri='https://sync-client.example.org/',
    )
    url = '/api/users/%s/service/oidc-client/' % joe.uuid

    # Create an APIClient
    apiclient1 = APIClient.objects.create_user(
        name='foo_apiclient1', identifier='foo_apiclient1', password='foo_apiclient1', ou=get_default_ou()
    )
    apiclient2 = APIClient.objects.create_user(
        name='foo_apiclient2', identifier='foo_apiclient2', password='foo_apiclient2', ou=get_default_ou()
    )
    apiclient3 = APIClient.objects.create_user(
        name='foo_apiclient3', identifier='foo_apiclient3', password='foo_apiclient3', ou=get_default_ou()
    )

    service_role = Role.objects.create(name='service_viewer')
    user_role = Role.objects.create(name='user_viewer')
    ok_role = Role.objects.create(name='service_user_viewer')

    view_service_perm, dummy = Permission.objects.get_or_create(
        operation=get_operation(VIEW_OP),
        target_ct=ContentType.objects.get_for_model(ContentType),
        target_id=ContentType.objects.get_for_model(Service).pk,
    )
    service_role.permissions.add(view_service_perm)
    user_role.permissions.add(get_view_user_perm())
    ok_role.permissions.add(view_service_perm)
    ok_role.permissions.add(get_view_user_perm())

    apiclient1.apiclient_roles.add(service_role)
    apiclient1.save()
    apiclient2.apiclient_roles.add(user_role)
    apiclient2.save()
    apiclient3.apiclient_roles.add(ok_role)
    apiclient3.save()
    for apiclient in apiclient1, apiclient2, apiclient3:
        apiclient.username = apiclient.name

    app.get(url, status=401)  # anonymous, not allowed

    for user in oidc_client, user_ou1, admin_ou1, admin_rando_role, member_rando, apiclient1, apiclient2:
        app.authorization = ('Basic', (user.username, user.clear_password))
        resp = app.get(url, status=403)
        assert resp.json['errors']

    for user in apiclient3, superuser:
        app.authorization = ('Basic', (user.username, user.clear_password))
        resp = app.get(url, status=200)
        assert 'user' in resp.json['data']


def test_user_service_data(app, superuser):
    app.authorization = ('Basic', (superuser.username, superuser.clear_password))

    joe = User.objects.create(username='john.doe', email='john.doe@example.com', ou=get_default_ou())
    app.get('/api/users/unknown-uuid-coincoin/service/oidc-client/', status=404)
    app.get('/api/users/%s/service/non-existant-slug/' % joe.uuid, status=404)

    # OIDC Service
    from authentic2_idp_oidc.models import OIDCClient
    from authentic2_idp_oidc.utils import make_sub

    oidc_client = OIDCClient.objects.create(
        name='OIDC Client',
        slug='oidc-client',
        sector_identifier_uri='https://sync-client.example.org/',
    )
    authorization = oidc_client.authorizations.create(user=joe, expired=now() + datetime.timedelta(hours=1))

    sub = make_sub(oidc_client, joe)
    resp = app.get('/api/users/%s/service/oidc-client/' % joe.uuid)
    assert resp.json['err'] == 0
    assert resp.json['result'] == 1
    assert resp.json['data']['service'] == {
        'slug': 'oidc-client',
        'name': 'OIDC Client',
        'type': 'OIDCClient',
    }
    assert resp.json['data']['user']['id'] == sub

    # expired authorization => no sub
    authorization.expired = now() - datetime.timedelta(hours=1)
    authorization.save()
    resp = app.get('/api/users/%s/service/oidc-client/' % joe.uuid)
    assert resp.json['data']['user'] == {}
    # no authorization => no sub
    authorization.delete()
    resp = app.get('/api/users/%s/service/oidc-client/' % joe.uuid)
    assert resp.json['data']['user'] == {}

    # authorization by OU
    oidc_client.authorization_mode = OIDCClient.AUTHORIZATION_MODE_BY_OU
    oidc_client.save()
    sub = make_sub(oidc_client, joe)
    authorization = oidc_client.ou.oidc_authorizations.create(
        user=joe, expired=now() + datetime.timedelta(hours=1)
    )
    resp = app.get('/api/users/%s/service/oidc-client/' % joe.uuid)
    assert resp.json['data']['user']['id'] == sub
    authorization.expired = now() - datetime.timedelta(hours=1)
    authorization.save()
    resp = app.get('/api/users/%s/service/oidc-client/' % joe.uuid)
    assert resp.json['data']['user'] == {}
    # no authorization => no sub
    authorization.delete()
    resp = app.get('/api/users/%s/service/oidc-client/' % joe.uuid)
    assert resp.json['data']['user'] == {}

    # SAML Service
    import lasso

    from authentic2.saml import models as saml_models

    provider = saml_models.LibertyProvider.objects.create(
        name='SAML SP', slug='saml-sp', protocol_conformance=lasso.PROTOCOL_SAML_2_0
    )
    service = saml_models.LibertyServiceProvider.objects.create(liberty_provider=provider, enabled=True)
    name_id = 'a1b2c3'
    resp = app.get('/api/users/%s/service/saml-sp/' % joe.uuid)
    assert resp.json['err'] == 0
    assert resp.json['result'] == 1
    assert resp.json['data']['service'] == {
        'slug': 'saml-sp',
        'name': 'SAML SP',
        'type': 'LibertyProvider',
    }
    assert resp.json['data']['user'] == {}  # no federation yet

    saml_models.LibertyFederation.objects.create(user=joe, sp=service, name_id_content=name_id)
    resp = app.get('/api/users/%s/service/saml-sp/' % joe.uuid)
    assert resp.json['err'] == 0
    assert resp.json['result'] == 1
    assert resp.json['data']['service'] == {
        'slug': 'saml-sp',
        'name': 'SAML SP',
        'type': 'LibertyProvider',
    }
    assert resp.json['data']['user']['id'] == name_id


def test_check_api_client(app, superuser, ou1, ou2):
    url = '/api/check-api-client/'
    payload = {'identifier': 'foo', 'password': 'bar'}
    resp = app.post_json(url, params=payload, status=401)

    app.authorization = ('Basic', (superuser.username, superuser.clear_password))
    resp = app.post_json(url, params=payload)
    assert resp.json['err'] == 1
    assert resp.json['err_desc'] == 'api client not found'

    api_client = APIClient.objects.create_user(id=42, name='Foo Bar', identifier='foo', password='foo')
    ou = get_default_ou()
    service = Service.objects.create(name='Service1', slug='service1', ou=ou)
    role1 = Role.objects.create(name='Role1', ou=ou, service=service)
    api_client.apiclient_roles.add(role1)

    resp = app.post_json(url, params=payload)
    assert resp.json['err'] == 1
    assert resp.json['err_desc'] == 'api client not found'

    payload = {'identifier': 'foo', 'password': 'foo'}
    resp = app.post_json(url, params=payload)
    assert resp.json['err'] == 0
    data = resp.json['data']
    assert data['id'] == 42
    assert data['name'] == 'Foo Bar'
    assert data['is_active'] is True
    assert data['is_anonymous'] is False
    assert data['is_authenticated'] is True
    assert data['is_superuser'] is False
    assert data['restrict_to_anonymised_data'] is False
    assert data['roles'] == [role1.uuid]
    assert data['ou'] == get_default_ou().slug
    assert data['allowed_user_attributes'] == []

    # create an api client with the same identifier not modified yet
    APIClient.objects.create_user(name='foo2', identifier='foo_2', identifier_legacy='foo', password='foo2')

    payload = {'identifier': 'foo', 'password': 'foo'}
    resp = app.post_json(url, params=payload)
    assert resp.json['err'] == 0

    payload = {'identifier': 'foo', 'password': 'foo2'}
    resp = app.post_json(url, params=payload)
    assert resp.json['err'] == 0

    payload = {'identifier': 'foo', 'password': 'foo3'}
    resp = app.post_json(url, params=payload)
    assert resp.json['err'] == 1

    api_client.ou = ou1
    api_client.save()
    payload = {'identifier': 'foo', 'password': 'foo'}
    resp = app.post_json(url, params=payload)
    assert resp.json['data']['ou'] == 'ou1'

    payload['ou'] = ou1.slug
    resp = app.post_json(url, params=payload)
    assert resp.json['data']['ou'] == 'ou1'

    payload['ou'] = ou2.slug
    resp = app.post_json(url, params=payload)
    assert resp.json['err'] == 1
    assert resp.json['err_desc'] == 'api client not found'

    color = Attribute.objects.create(
        name='preferred_color',
        label='Preferred color',
        kind='string',
        disabled=False,
        multiple=False,
    )
    phone2 = Attribute.objects.create(
        name='phone2',
        label='Second phone number',
        kind='phone_number',
        disabled=False,
        multiple=False,
    )
    api_client.allowed_user_attributes.add(color, phone2)
    api_client.save()
    payload = {'identifier': 'foo', 'password': 'foo'}
    resp = app.post_json(url, params=payload)
    assert resp.json['err'] == 0
    data = resp.json['data']
    assert data['id'] == 42
    assert data['name'] == 'Foo Bar'
    assert data['is_active'] is True
    assert data['is_anonymous'] is False
    assert data['is_authenticated'] is True
    assert data['is_superuser'] is False
    assert data['restrict_to_anonymised_data'] is False
    assert data['roles'] == [role1.uuid]
    assert data['ou'] == ou1.slug
    assert set(data['allowed_user_attributes']) == {'preferred_color', 'phone2'}
    assert data['service_superuser']['default'] == {'service1': False}
    assert data['service_superuser']['ou1'] == {}
    assert data['service_superuser']['ou2'] == {}

    service = Service.objects.create(name='Service2', slug='service2', ou=ou2)
    role2 = Role.objects.create(name='Role2', ou=ou, service=service, is_superuser=True)
    api_client.apiclient_roles.add(role2)

    resp = app.post_json(url, params=payload)
    assert resp.json['err'] == 0
    data = resp.json['data']
    assert data['id'] == 42
    assert data['name'] == 'Foo Bar'
    assert data['is_active'] is True
    assert data['is_anonymous'] is False
    assert data['is_authenticated'] is True
    assert data['is_superuser'] is False
    assert data['restrict_to_anonymised_data'] is False
    assert set(data['roles']) == {role1.uuid, role2.uuid}
    assert data['ou'] == ou1.slug
    assert set(data['allowed_user_attributes']) == {'preferred_color', 'phone2'}
    assert data['service_superuser']['default'] == {'service1': False}
    assert data['service_superuser']['ou1'] == {}
    assert data['service_superuser']['ou2'] == {'service2': True}

    role2.is_superuser = False
    role1.is_superuser = True
    role2.save()
    role1.save()
    resp = app.post_json(url, params=payload)
    assert resp.json['err'] == 0
    data = resp.json['data']
    assert data['service_superuser']['default'] == {'service1': True}
    assert data['service_superuser']['ou1'] == {}
    assert data['service_superuser']['ou2'] == {'service2': False}


def test_check_api_client_ip_restrictions(app, superuser, ou1, ou2):
    # IP restriction feature flag enabled
    a2_app_settings.A2_API_USERS_ALLOW_IP_RESTRICTIONS = True

    url = '/api/check-api-client/'
    app.authorization = ('Basic', (superuser.username, superuser.clear_password))
    api_client = APIClient.objects.create_user(identifier='foo', name='Foo', password='foo')
    ou = get_default_ou()
    role1 = Role.objects.create(name='Role1', ou=ou)
    api_client.apiclient_roles.add(role1)

    payload = {'identifier': 'foo', 'password': 'foo'}
    resp = app.post_json(url, params=payload)
    assert resp.json['err'] == 0

    api_client.allowed_ip = '127.0.0.0/24'
    api_client.save()
    resp = app.post_json(url, params=payload)
    assert resp.json['err'] == 1
    assert resp.json['err_desc'] == 'api client not found'

    payload['ip'] = '255.255.255.255'
    resp = app.post_json(url, params=payload)
    assert resp.json['err'] == 1
    assert resp.json['err_desc'] == 'api client not found'

    payload['ip'] = '0:A:002:abc::0:1'
    resp = app.post_json(url, params=payload)
    assert resp.json['err'] == 1
    assert resp.json['err_desc'] == 'api client not found'

    payload['ip'] = '127.0.0.42'
    resp = app.post_json(url, params=payload)
    assert resp.json['err'] == 0

    api_client.denied_ip = '127.0.0.1'
    api_client.ip_allow_deny = True
    api_client.save()

    payload['ip'] = '127.0.0.42'
    resp = app.post_json(url, params=payload)
    assert resp.json['err'] == 0

    payload['ip'] = '127.0.0.1'
    resp = app.post_json(url, params=payload)
    assert resp.json['err'] == 1
    assert resp.json['err_desc'] == 'api client not found'

    api_client.ip_allow_deny = False
    api_client.save()
    resp = app.post_json(url, params=payload)
    assert resp.json['err'] == 0

    payload['ip'] = 'something else'
    resp = app.post_json(url, params=payload, status=400)
    payload['ip'] = '0.0.0.0/0'
    resp = app.post_json(url, params=payload, status=400)
    payload['ip'] = '1.2.3.4/12'
    resp = app.post_json(url, params=payload, status=400)

    # Disabling IP restrictions
    a2_app_settings.A2_API_USERS_ALLOW_IP_RESTRICTIONS = False

    payload['ip'] = '1.2.3.4'
    resp = app.post_json(url, params=payload)
    assert resp.json['err'] == 0
    payload['ip'] = '127.0.0.1'
    resp = app.post_json(url, params=payload)
    assert resp.json['err'] == 0
    payload['ip'] = '127.0.0.42'
    resp = app.post_json(url, params=payload)
    assert resp.json['err'] == 0
    payload['ip'] = '::1'
    resp = app.post_json(url, params=payload)
    assert resp.json['err'] == 0
    payload['ip'] = '2001:42::42'
    resp = app.post_json(url, params=payload)
    assert resp.json['err'] == 0


def test_check_api_client_role_inheritance(app, superuser):
    api_client = APIClient.objects.create_user(name='Foo Bar', identifier='foo', password='foo')

    role1 = Role.objects.create(name='Role1', ou=get_default_ou())
    api_client.apiclient_roles.add(role1)

    app.authorization = ('Basic', (superuser.username, superuser.clear_password))
    payload = {'identifier': 'foo', 'password': 'foo'}
    resp = app.post_json('/api/check-api-client/', params=payload)
    assert resp.json['err'] == 0
    assert resp.json['data']['roles'] == [role1.uuid]

    role2 = Role.objects.create(name='Role2', ou=get_default_ou())
    role3 = Role.objects.create(name='Role3', ou=get_default_ou())

    role1.add_parent(role2)
    role2.add_parent(role3)

    resp = app.post_json('/api/check-api-client/', params=payload)
    assert resp.json['err'] == 0
    assert set(resp.json['data']['roles']) == {role1.uuid, role2.uuid, role3.uuid}


def test_api_basic_authz_user_phone_number(app, settings, superuser, phone_activated_authn):
    headers = {'Authorization': 'Basic abc'}
    app.get('/api/users/', headers=headers, status=401)

    headers = basic_authorization_header(superuser)
    app.get('/api/users/', headers=headers, status=200)

    superuser.attributes.phone = '+33499985643'
    superuser.save()

    # authn valid
    headers = basic_authorization_header('+33499985643', superuser.clear_password)
    app.get('/api/users/', headers=headers, status=200)

    headers = basic_authorization_header('+33499985643 ', superuser.clear_password)
    app.get('/api/users/', headers=headers, status=200)

    headers = basic_authorization_header('+33-4/99/985643', superuser.clear_password)
    app.get('/api/users/', headers=headers, status=200)

    headers = basic_authorization_header('0499985643', superuser.clear_password)
    app.get('/api/users/', headers=headers, status=200)

    # wrong phone number
    headers = basic_authorization_header('+33499985644', superuser.clear_password)
    app.get('/api/users/', headers=headers, status=401)


def test_api_authn_healthcheck(app, settings, superuser, simple_user, phone_activated_authn):
    phone_activated_authn.accept_email_authentication = False
    phone_activated_authn.save()

    phone2, dummy = Attribute.objects.get_or_create(
        name='yet_another_phone',
        kind='phone_number',
        defaults={'label': 'Yet another phone'},
    )

    app.authorization = ('Basic', (superuser.username, superuser.clear_password))
    resp = app.get('/api/authn-healthcheck/', status=200)
    assert resp.json['err'] == 0
    assert resp.json['data'] == {
        'accept_email_authentication': False,
        'accept_phone_authentication': True,
        'phone_identifier_field': 'phone',
    }

    phone_activated_authn.accept_phone_authentication = False
    phone_activated_authn.accept_email_authentication = True
    phone_activated_authn.phone_identifier_field = phone2
    phone_activated_authn.save()

    resp = app.get('/api/authn-healthcheck/', status=200)
    assert resp.json['err'] == 0
    assert resp.json['data'] == {
        'accept_email_authentication': True,
        'accept_phone_authentication': False,
        'phone_identifier_field': 'yet_another_phone',
    }

    phone_activated_authn.accept_email_authentication = False
    phone_activated_authn.phone_identifier_field = None
    phone_activated_authn.save()

    resp = app.get('/api/authn-healthcheck/', status=200)
    assert resp.json['err'] == 0
    assert resp.json['data'] == {
        'accept_email_authentication': False,
        'accept_phone_authentication': False,
        'phone_identifier_field': '',
    }

    app.authorization = ('Basic', (simple_user.username, simple_user.clear_password))
    app.get('/api/authn-healthcheck/', status=403)


def test_api_users_create_phone_identifier_unique(settings, app, admin, phone_activated_authn, simple_user):
    simple_user.attributes.phone = '+33122334455'
    simple_user.save()
    settings.A2_PHONE_IS_UNIQUE = True
    payload = {
        'username': 'janedoe',
        'email': 'jane.doe@nowhere.null',
        'first_name': 'Jane',
        'last_name': 'Doe',
        'email_verified': True,
        'phone': '+33122334455',
    }
    headers = basic_authorization_header(admin)
    resp = app.post_json('/api/users/', headers=headers, params=payload, status=400)
    assert resp.json['errors']['attributes'] == ['This phone number identifier is already used.']


def test_api_users_create_phone_identifier_unique_by_ou(
    settings, app, admin, phone_activated_authn, simple_user, ou1, ou2
):
    ou1.phone_is_unique = ou2.phone_is_unique = True
    ou1.save()
    ou2.save()
    simple_user.attributes.phone = '+33122334455'
    simple_user.ou = ou1
    simple_user.save()
    usercount = User.objects.count()
    settings.A2_PHONE_IS_UNIQUE = False
    payload = {
        'username': 'janedoe',
        'email': 'jane.doe@nowhere.null',
        'first_name': 'Jane',
        'last_name': 'Doe',
        'ou': 'ou1',
        'email_verified': True,
        'phone': '+33122334455',
    }
    headers = basic_authorization_header(admin)
    resp = app.post_json('/api/users/', headers=headers, params=payload, status=400)
    assert resp.json['errors']['attributes'] == ['This phone number identifier is already used.']
    assert set(
        AttributeValue.objects.filter(
            attribute=phone_activated_authn.phone_identifier_field,
            content='+33122334455',
        ).values_list('object_id', flat=True)
    ) == {simple_user.id}
    assert User.objects.count() == usercount

    # change ou, where phone number isn't taken yet
    payload['ou'] = 'ou2'
    resp = app.post_json('/api/users/', headers=headers, params=payload, status=201)
    new_id = resp.json['id']
    assert new_id != simple_user.id
    assert set(
        AttributeValue.objects.filter(
            attribute=phone_activated_authn.phone_identifier_field,
            content='+33122334455',
        ).values_list('object_id', flat=True)
    ) == {simple_user.id, new_id}
    assert User.objects.count() == usercount + 1

    # trying to create yet another user in that same last with the same phone number should fail:
    payload['username'] = 'bobdoe'
    payload['email'] = 'bobdoe@nowhere.null'
    resp = app.post_json('/api/users/', headers=headers, params=payload, status=400)
    assert resp.json['errors']['attributes'] == ['This phone number identifier is already used.']
    # no new phone attribute created
    assert set(
        AttributeValue.objects.filter(
            attribute=phone_activated_authn.phone_identifier_field,
            content='+33122334455',
        ).values_list('object_id', flat=True)
    ) == {simple_user.id, new_id}
    assert User.objects.count() == usercount + 1


def test_api_users_create_no_phone_model_field_writes(settings, app, admin, phone_activated_authn):
    payload = {
        'username': 'janedoe',
        'email': 'jane.doe@nowhere.null',
        'first_name': 'Jane',
        'last_name': 'Doe',
        'email_verified': True,
        'phone': '+33122334455',
    }
    headers = basic_authorization_header(admin)
    resp = app.post_json('/api/users/', headers=headers, params=payload, status=201)
    user = User.objects.get(uuid=resp.json['uuid'])
    assert not user.phone
    assert user.phone_identifier == '+33122334455'


def test_api_users_update_no_phone_model_field_writes(
    settings, app, admin, simple_user, phone_activated_authn
):
    simple_user.phone = None
    simple_user.save()

    payload = {
        'username': simple_user.username,
        'id': simple_user.id,
        'email': 'john.doe@nowhere.null',
        'first_name': 'Johnny',
        'last_name': 'Doeny',
        'phone': '+33122334455',
    }
    headers = basic_authorization_header(admin)
    app.put_json(f'/api/users/{simple_user.uuid}/', params=payload, headers=headers, status=200)
    user = User.objects.get(id=simple_user.id)
    assert not user.phone
    assert user.phone_identifier == '+33122334455'

    # already existing value should be left unchanged
    simple_user.phone = '+33123456789'
    simple_user.save()

    payload['phone'] = '+33155555555'
    app.put_json(f'/api/users/{simple_user.uuid}/', params=payload, headers=headers, status=200)
    user = User.objects.get(id=simple_user.id)
    assert user.phone == '+33123456789'
    assert user.phone_identifier == '+33155555555'


def test_empty_title_is_accepted(settings, app, admin):
    Attribute.objects.create(kind='title', name='title', label='title')
    app.authorization = ('Basic', (admin.username, admin.clear_password))

    payload = {
        'first_name': 'John',
        'last_name': 'Doe',
        'title': '',
    }
    app.post_json('/api/users/', params=payload, status=201)
