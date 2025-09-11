import json
import random
import uuid

import pytest
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db.utils import IntegrityError
from django.test.utils import override_settings
from django.urls import reverse

from authentic2 import app_settings as a2_app_settings
from authentic2.a2_rbac.models import ADD_OP, SEARCH_OP, VIEW_OP, Role
from authentic2.a2_rbac.utils import get_default_ou
from authentic2.models import APIClient, Attribute

User = get_user_model()


@pytest.fixture
def api_client(db):
    api_client = APIClient.objects.create_user(name='foo', identifier='id', password='pass')
    assert api_client.check_password('pass')
    return api_client


def test_has_perm(api_client):
    role_ct = ContentType.objects.get_for_model(Role)
    role_admin_role = Role.objects.get_admin_role(role_ct, 'admin %s' % role_ct, 'admin-role')
    api_client = APIClient.objects.create_user(name='foo', identifier='foo', password='foo')
    assert not api_client.has_perm('a2_rbac.change_role')
    assert not api_client.has_perm('a2_rbac.view_role')
    assert not api_client.has_perm('a2_rbac.delete_role')
    assert not api_client.has_perm('a2_rbac.add_role')
    role_admin_role.apiclients.add(api_client)
    del api_client._rbac_perms_cache
    assert api_client.has_perm('a2_rbac.change_role')
    assert api_client.has_perm('a2_rbac.view_role')
    assert api_client.has_perm('a2_rbac.delete_role')
    assert api_client.has_perm('a2_rbac.add_role')


def test_has_perm_ou(api_client, ou1):
    role_ct = ContentType.objects.get_for_model(Role)
    role_admin_role = Role.objects.get_admin_role(role_ct, 'admin %s' % role_ct, 'admin-role')
    api_client = APIClient.objects.create_user(identifier='foo', name='foo', password='foo', ou=ou1)
    assert not api_client.has_ou_perm('a2_rbac.change_role', ou1)
    assert not api_client.has_ou_perm('a2_rbac.view_role', ou1)
    assert not api_client.has_ou_perm('a2_rbac.delete_role', ou1)
    assert not api_client.has_ou_perm('a2_rbac.add_role', ou1)
    role_admin_role.apiclients.add(api_client)
    del api_client._rbac_perms_cache
    assert api_client.has_ou_perm('a2_rbac.change_role', ou1)
    assert api_client.has_ou_perm('a2_rbac.view_role', ou1)
    assert api_client.has_ou_perm('a2_rbac.delete_role', ou1)
    assert api_client.has_ou_perm('a2_rbac.add_role', ou1)


def test_api_users_list(app, api_client):
    user = User.objects.create(username='user1')

    app.authorization = ('Basic', ('foo', 'bar'))
    resp = app.get('/api/users/', status=401)

    app.authorization = ('Basic', (api_client.identifier, 'pass'))
    resp = app.get('/api/users/')
    assert len(resp.json['results']) == 0

    # give permissions
    r = Role.objects.get_admin_role(
        ContentType.objects.get_for_model(User), name='role', slug='role', operation=VIEW_OP
    )
    api_client.apiclient_roles.add(r)
    resp = app.get('/api/users/')
    assert len(resp.json['results']) == 1

    preferred_color = Attribute.objects.create(
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
    user.attributes.preferred_color = 'blue'
    user.attributes.phone2 = '+33122334455'
    user.save()

    api_client.allowed_user_attributes.add(preferred_color, phone2)
    api_client.save()
    resp = app.get('/api/users/')
    assert len(resp.json['results']) == 1
    assert resp.json['results'][0]['preferred_color'] == 'blue'
    assert resp.json['results'][0]['phone2'] == '+33122334455'

    api_client.allowed_user_attributes.remove(preferred_color)
    api_client.save()
    resp = app.get('/api/users/')
    assert len(resp.json['results']) == 1
    assert 'preferred_color' not in resp.json['results'][0]
    assert resp.json['results'][0]['phone2'] == '+33122334455'

    api_client.allowed_user_attributes.remove(phone2)
    api_client.save()
    resp = app.get('/api/users/')
    assert len(resp.json['results']) == 1
    assert resp.json['results'][0]['preferred_color'] == 'blue'
    assert resp.json['results'][0]['phone2'] == '+33122334455'

    api_client.allowed_user_attributes.add(preferred_color)
    api_client.save()
    resp = app.get('/api/users/')
    assert len(resp.json['results']) == 1
    assert resp.json['results'][0]['preferred_color'] == 'blue'
    assert 'phone2' not in resp.json['results'][0]


def test_api_user_synchronization(app, api_client):
    uuids = []
    for _ in range(100):
        user = User.objects.create(first_name='ben', last_name='dauve')
        uuids.append(user.uuid)
    unknown_uuids = [uuid.uuid4().hex for i in range(100)]
    url = reverse('a2-api-users-synchronization')
    content = {
        'known_uuids': uuids + unknown_uuids,
    }
    random.shuffle(content['known_uuids'])

    app.authorization = ('Basic', ('foo', 'bar'))
    response = app.post_json(url, params=content, status=401)

    app.authorization = ('Basic', (api_client.identifier, 'pass'))
    response = app.post_json(url, params=content, status=403)

    # give custom_user.search_user permission to user
    r = Role.objects.get_admin_role(
        ContentType.objects.get_for_model(User), name='role', slug='role', operation=SEARCH_OP
    )
    api_client.apiclient_roles.add(r)
    response = app.post_json(url, params=content)
    assert response.json['err'] == 0
    assert response.json['result'] == 1
    assert set(response.json['unknown_uuids']) == set(unknown_uuids)


def test_api_user_synchronization_ou(app, api_client, ou1):
    uuids = []
    authorized_uuids = []
    for index in range(100):
        ou = ou1 if index % 2 else get_default_ou()
        user = User.objects.create(first_name='ben', last_name='dauve', ou=ou)
        uuids.append(user.uuid)
        if index % 2:
            authorized_uuids.append(user.uuid)
    unknown_uuids = [uuid.uuid4().hex for i in range(100)]
    url = reverse('a2-api-users-synchronization')
    content = {
        'known_uuids': uuids + unknown_uuids,
    }
    random.shuffle(content['known_uuids'])

    app.authorization = ('Basic', ('foo', 'bar'))
    response = app.post_json(url, params=content, status=401)

    app.authorization = ('Basic', (api_client.identifier, 'pass'))
    response = app.post_json(url, params=content, status=403)

    # give custom_user.search_user permission to user
    r = Role.objects.get_admin_role(
        ContentType.objects.get_for_model(User),
        name='role',
        slug='role',
        ou=ou1,
        operation=SEARCH_OP,
    )
    api_client.apiclient_roles.add(r)
    response = app.post_json(url, params=content)
    assert response.json['err'] == 0
    assert response.json['result'] == 1
    assert set(response.json['unknown_uuids']) != set(unknown_uuids)
    assert set(unknown_uuids).issubset(set(response.json['unknown_uuids']))


def test_api_users_create(app, api_client):
    payload = {
        'username': 'janedoe',
        'email': 'jane.doe@nowhere.null',
        'first_name': 'Jane',
        'last_name': 'Doe',
    }
    app.authorization = ('Basic', (api_client.identifier, 'pass'))
    resp = app.post_json('/api/users/', params=payload, status=403)

    # give permissions
    r = Role.objects.get_admin_role(
        ContentType.objects.get_for_model(User), name='role', slug='role', operation=ADD_OP
    )
    api_client.apiclient_roles.add(r)
    resp = app.post_json('/api/users/', params=payload)
    assert User.objects.get(uuid=resp.json['uuid'])


def test_api_users_hasher_change(app, api_client, settings, nocache):
    del api_client.password  # load password hash from DB
    original_password = api_client.password

    settings.PASSWORD_HASHERS = ['authentic2.hashers.PloneSHA1PasswordHasher'] + settings.PASSWORD_HASHERS

    assert api_client.check_password('foobar') is False
    assert original_password == api_client.password

    assert api_client.check_password('pass')
    assert original_password != api_client.password


def test_api_users_create_ip_restricted(settings, app, api_client):
    payload = {
        'username': 'janedoe',
        'email': 'jane.doe@nowhere.null',
        'first_name': 'Jane',
        'last_name': 'Doe',
        'password': 'secret',
    }
    # Duplicate apiclient identifier
    APIClient.objects.create(name='foobar', identifier='toto', identifier_legacy=api_client.identifier)

    app.authorization = ('Basic', (api_client.identifier, 'pass'))
    resp = app.post_json('/api/users/', params=payload, status=403)

    # give permissions
    r = Role.objects.get_admin_role(
        ContentType.objects.get_for_model(User), name='role', slug='role', operation=ADD_OP
    )
    api_client.apiclient_roles.add(r)
    resp = app.post_json('/api/users/', params=payload)
    user = User.objects.get(uuid=resp.json['uuid'])
    assert user
    assert user.check_password('secret')
    assert user.password != 'secret'
    user.delete()

    # add ip restriction to api-client
    api_client.allowed_ip = '1.2.3.4'
    api_client.save()

    # IP restriction feature flag disabled
    a2_app_settings.A2_API_USERS_ALLOW_IP_RESTRICTIONS = False
    resp = app.post_json('/api/users/', params=payload)
    user = User.objects.get(uuid=resp.json['uuid'])
    assert user
    user.delete()

    # IP restriction feature flag enabled
    a2_app_settings.A2_API_USERS_ALLOW_IP_RESTRICTIONS = True
    resp = app.post_json('/api/users/', params=payload, status=401)

    api_client.allowed_ip = '::1\n127.0.0.0/16'
    api_client.save()
    resp = app.post_json('/api/users/', params=payload)
    user = User.objects.get(uuid=resp.json['uuid'])
    assert user
    user.delete()

    api_client.allowed_ip = '# 127.0.0.1\n2001:42:42::1\n192.168.42.0/24'
    api_client.save()
    resp = app.post_json('/api/users/', params=payload, status=401)

    # ensure x_fwd_middleware is loaded to test it
    x_fwd_middleware = 'authentic2.middleware.XForwardedForMiddleware'
    middlewares = settings.MIDDLEWARE
    if x_fwd_middleware not in middlewares:
        middlewares.append(x_fwd_middleware)
    with override_settings(MIDDLEWARE=x_fwd_middleware):
        body = json.dumps(payload)
        resp = app.post(
            '/api/users/',
            body,
            headers={'Content-Type': 'application/json', 'x-forwarded-for': '192.168.42.2'},
        )
        user = User.objects.get(uuid=resp.json['uuid'])
        assert user
        user.delete()

        resp = app.post(
            '/api/users/',
            body,
            headers={'Content-Type': 'application/json', 'X-Forwarded-For': '2001:42:42:0:0::1'},
        )
        user = User.objects.get(uuid=resp.json['uuid'])
        assert user
        user.delete()
        resp = app.post(
            '/api/users/',
            body,
            headers={'Content-Type': 'application/json', 'X-Forwarded-For': '2.3.4.5'},
            status=401,
        )
        resp = app.post('/api/users/', body, headers={'Content-Type': 'application/json'}, status=401)


def test_api_users_ip_restrictions(app, api_client):
    # IP restriction feature flag enabled
    a2_app_settings.A2_API_USERS_ALLOW_IP_RESTRICTIONS = True

    # no restriction by default
    for ip in ('127.0.0.1', '::1', '001.002.003.004', '000A:000b:c000::'):
        assert api_client.ip_authorized(ip)
    assert api_client.ip_authorized(None)

    # whitelist
    api_client.allowed_ip = '''
127.0.0.0/24
4.3.2.1
# 1.2.3.8
1.2.3.5/30

# ipv6
A:B:C::/64
4:3:2::1
1:2:3:4:5:6:7:89/119
    '''
    assert not api_client.ip_authorized(None)

    for i in range(1, 254):
        assert api_client.ip_authorized('127.0.0.%d' % i)
    assert not api_client.ip_authorized('127.0.1.1')

    assert api_client.ip_authorized('4.3.2.1')
    assert not api_client.ip_authorized('4.3.2.0')
    assert not api_client.ip_authorized('4.3.2.2')

    assert api_client.ip_authorized('1.2.3.4')
    assert api_client.ip_authorized('1.2.3.5')
    assert api_client.ip_authorized('1.2.3.6')
    assert api_client.ip_authorized('1.2.3.7')
    assert not api_client.ip_authorized('1.2.3.8')
    assert not api_client.ip_authorized('1.2.3.3')

    assert not api_client.ip_authorized('192.168.40.2')
    assert not api_client.ip_authorized('::ffff:4.3.2.1')
    assert not api_client.ip_authorized('::ffff:403:201')

    assert api_client.ip_authorized('a:b:c::')
    assert api_client.ip_authorized('a:b:c:0:1:2:3:4')
    assert api_client.ip_authorized('a:b:c:0:FFFF:FFFF:FFFF:FFFF')
    assert not api_client.ip_authorized('a:b:c:1::')

    assert api_client.ip_authorized('4:3:2::1')
    assert not api_client.ip_authorized('4:3:2:1::')

    assert api_client.ip_authorized('1:2:3:4:5:6:7:89')
    assert api_client.ip_authorized('1:2:3:4:5:6:7::')
    assert api_client.ip_authorized('1:2:3:4:5:6:7:1ff')
    assert not api_client.ip_authorized('1:2:3:4:5:6:7:200')

    # blacklist
    api_client.allowed_ip = ''
    api_client.denied_ip = '10.2.3.200/24\n4.3.2.1\n::/0'
    assert not api_client.ip_authorized(None)

    assert not api_client.ip_authorized('::1')
    assert not api_client.ip_authorized('2001:42:42::1')

    assert not api_client.ip_authorized('4.3.2.1')
    assert not api_client.ip_authorized('10.2.3.1')
    assert not api_client.ip_authorized('10.2.3.250')
    assert api_client.ip_authorized('127.0.0.1')
    assert api_client.ip_authorized('10.3.2.1')

    # deny / allow
    api_client.allowed_ip = '::1\n1:2:3::/64'
    api_client.ip_allow_deny = False
    assert not api_client.ip_authorized(None)

    assert not api_client.ip_authorized('2001:42:42::1')
    assert api_client.ip_authorized('::1')
    assert api_client.ip_authorized('1:2:3::')
    assert api_client.ip_authorized('1:2:3::4:3:2:1')
    assert not api_client.ip_authorized('10.2.3.1')
    assert not api_client.ip_authorized('10.2.3.250')
    assert api_client.ip_authorized('127.0.0.1')
    assert api_client.ip_authorized('10.3.2.1')

    # allow / deny
    api_client.ip_allow_deny = True
    api_client.allowed_ip = '192.168.0.0/16'
    api_client.denied_ip = '192.168.0.0/24'
    assert not api_client.ip_authorized(None)

    assert api_client.ip_authorized('192.168.42.42')
    assert not api_client.ip_authorized('192.168.0.42')

    assert not api_client.ip_authorized('::1')
    assert not api_client.ip_authorized('2001:42:42::1')
    assert not api_client.ip_authorized('10.2.2.2')
    assert not api_client.ip_authorized('1.2.3.4')

    # IP restriction feature flag disabled
    a2_app_settings.A2_API_USERS_ALLOW_IP_RESTRICTIONS = False
    assert api_client.ip_authorized(None)
    assert api_client.ip_authorized('192.168.42.42')
    assert api_client.ip_authorized('192.168.0.42')
    assert api_client.ip_authorized('::1')
    assert api_client.ip_authorized('2001:42:42::1')
    assert api_client.ip_authorized('10.2.2.2')
    assert api_client.ip_authorized('1.2.3.4')


def test_api_user_identifier_unique(app, db):
    APIClient.objects.create_user(name='foo', identifier='foo', password='foo')
    with pytest.raises(IntegrityError):
        APIClient.objects.create_user(name='foo2', identifier='foo', password='foo2')
