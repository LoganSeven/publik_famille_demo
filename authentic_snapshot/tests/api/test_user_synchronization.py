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

import datetime
import random
import uuid

import pytest
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.urls import reverse
from django.utils.timezone import now

from authentic2.a2_rbac.models import ADMIN_OP, SEARCH_OP, Permission, Role
from authentic2.a2_rbac.utils import get_default_ou, get_operation
from authentic2.apps.journal.models import Event, EventType
from authentic2.custom_user.models import Profile, ProfileType, User

from ..utils import basic_authorization_header, basic_authorization_oidc_client

pytestmark = pytest.mark.django_db

URL = '/api/users/synchronization/'


@pytest.fixture
def user(simple_user):
    role = Role.objects.get_admin_role(
        ContentType.objects.get_for_model(User), name='role', slug='role', operation=SEARCH_OP
    )
    role.members.add(simple_user)
    return simple_user


@pytest.fixture
def app(app, user):
    app.set_authorization(('Basic', (user.username, user.clear_password)))
    return app


@pytest.fixture
def users(db):
    return [User.objects.create(first_name='john', last_name='doe') for _ in range(10)]


@pytest.fixture
def uuids(users):
    return [user.uuid for user in users]


def test_url(app, simple_user):
    # URL is publikc api, check it
    assert URL == reverse('a2-api-users-synchronization')


def test_authentication_required(app):
    app.set_authorization(None)
    app.post_json(URL, status=401)


def test_permission_required(app, user):
    user.roles.clear()
    app.post_json(URL, status=403)


def test_ou_permission_sufficient(app, user, ou1, users):
    user.roles.clear()
    r = Role.objects.get_admin_role(
        ContentType.objects.get_for_model(User),
        name='role',
        slug='role',
        ou=ou1,
        operation=SEARCH_OP,
    )
    user.roles.add(r)

    users = users[:6]
    now = datetime.datetime.now()
    content = {
        'known_uuids': [user.uuid for user in users],
        'timestamp': (now - datetime.timedelta(days=3)).isoformat(),
    }
    resp = app.post_json(URL, params=content, status=200)
    assert len(resp.json['unknown_uuids']) == 6

    for user in users[:4]:
        user.ou = ou1
        user.save()

    resp = app.post_json(URL, params=content, status=200)
    assert len(resp.json['unknown_uuids']) == 6 - 4


@pytest.fixture(scope='session')
def unknown_uuids():
    return [uuid.uuid4().hex for i in range(10)]


@pytest.fixture
def payload(uuids, unknown_uuids):
    content = {
        'known_uuids': uuids + unknown_uuids,
    }
    random.shuffle(content['known_uuids'])
    return content


def test_basic(app, payload, unknown_uuids):
    response = app.post_json(URL, params=payload)
    assert response.json['err'] == 0
    assert response.json['result'] == 1
    assert set(response.json['unknown_uuids']) == set(unknown_uuids)


def test_payload_error(app):
    payload = {'known_uuids': 'foo'}
    response = app.post_json(URL, params=payload, status=400)
    assert response.json['err'] == 1


def test_full_known_users(app, payload):
    payload['full_known_users'] = 1
    response = app.post_json(URL, params=payload)
    assert response.json['err'] == 0
    assert response.json['result'] == 1

    # known users returned as part of api's full mode:
    assert len(response.json['known_users']) == 10
    for user_dict in response.json['known_users']:
        assert user_dict['first_name'] == 'john'
        assert user_dict['last_name'] == 'doe'
        assert {
            'uuid',
            'email',
            'is_staff',
            'is_superuser',
            'email_verified',
            'ou',
            'is_active',
            'deactivation',
            'modified',
        }.issubset(set(user_dict.keys()))


def test_timestamp(app, users):
    now = datetime.datetime.now()
    users = users[:6]

    for i, event_name in enumerate(
        [
            'manager.user.creation',
            'manager.user.profile.edit',
            'manager.user.activation',
            'manager.user.deactivation',
            'manager.user.password.change.force',
            'manager.user.password.change.unforce',
        ]
    ):
        event_type = EventType.objects.get_for_name(event_name)
        Event.objects.create(
            type=event_type,
            timestamp=now - datetime.timedelta(days=i, hours=1),
            references=[users[i]],
        )

    content = {
        'known_uuids': [user.uuid for user in users],
        'timestamp': (now - datetime.timedelta(days=3)).isoformat(),
    }

    response = app.post(URL, params=content)

    for user in users[:3]:
        assert user.uuid in response.json['modified_users_uuids']
        assert user.uuid not in response.json['unknown_uuids']
    for user in users[3:]:
        assert user.uuid not in response.json['modified_users_uuids']
        assert user.uuid not in response.json['unknown_uuids']

    for user in users[:3]:
        user.delete()

    content['timestamp'] = (now - datetime.timedelta(days=7)).isoformat()

    response = app.post(URL, params=content)

    for user in users[:3]:
        assert user.uuid not in response.json['modified_users_uuids']
        assert user.uuid in response.json['unknown_uuids']
    for user in users[3:]:
        assert user.uuid in response.json['modified_users_uuids']
        assert user.uuid not in response.json['unknown_uuids']

    for user in users[3:]:
        user.delete()

    response = app.post(URL, params=content)

    assert not response.json['modified_users_uuids']
    for user in users:
        assert user.uuid in response.json['unknown_uuids']

    for user in users[:3]:
        content['known_uuids'].remove(user.uuid)

    response = app.post(URL, params=content)

    assert not response.json['modified_users_uuids']
    assert len(response.json['unknown_uuids']) == 3
    for user in users[3:]:
        assert user.uuid in response.json['unknown_uuids']


def test_keepalive_false(app, payload, unknown_uuids):
    app.post_json(URL, params=payload)
    assert User.objects.filter(keepalive__isnull=False).count() == 0

    payload['keepalive'] = False
    app.post_json(URL, params=payload)
    assert User.objects.filter(keepalive__isnull=False).count() == 0


def test_keepalive_missing_permission(app, user, payload, freezer):
    payload['keepalive'] = True
    app.post_json(URL, params=payload, status=403)


class TestWithPermission:
    @pytest.fixture(autouse=True)
    def configure_ou(self, users, db):
        ou = get_default_ou()
        User.objects.all().update(ou=ou)
        User.objects.update(last_login=models.F('date_joined'))
        ou.clean_unused_accounts_alert = 60
        ou.clean_unused_accounts_deletion = 63
        ou.save()

    @pytest.fixture
    def user(self, user):
        perm, _ = Permission.objects.get_or_create(
            ou__isnull=True,
            operation=get_operation(ADMIN_OP),
            target_ct=ContentType.objects.get_for_model(ContentType),
            target_id=ContentType.objects.get_for_model(User).pk,
        )
        user.roles.all()[0].permissions.add(perm)
        return user

    @pytest.fixture
    def payload(self, payload):
        payload['keepalive'] = True
        return payload

    def test_keepalive_true(self, app, user, users, payload, freezer):
        freezer.move_to(datetime.timedelta(days=50, hours=1))
        app.post_json(URL, params=payload)
        assert User.objects.filter(keepalive__isnull=False).count() == len(users)

    def test_keepalive_one_time_by_clean_unused_period_alert(self, app, user, users, payload, freezer):
        # set last keepalive 29 days ago
        User.objects.exclude(pk=user.pk).update(keepalive=now())
        app.post_json(URL, params=payload)
        freezer.move_to(datetime.timedelta(days=30))
        # keepalive did not change
        assert User.objects.filter(keepalive__lt=now() - datetime.timedelta(days=1)).count() == 10

        # move 2 days in the future
        freezer.move_to(datetime.timedelta(days=1))
        app.post_json(URL, params=payload)
        # keepalive did change
        assert User.objects.filter(keepalive__lt=now() - datetime.timedelta(days=1)).count() == 0


def test_user_synchronization(app, simple_user):
    headers = basic_authorization_header(simple_user)

    # first remove custom_user.search_user permission to user
    r = Role.objects.get_admin_role(
        ContentType.objects.get_for_model(User), name='role', slug='role', operation=SEARCH_OP
    )
    r.members.remove(simple_user)

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
    response = app.post_json(url, params=content, headers=headers, status=403)

    # then give back custom_user.search_user permission to user
    r.members.add(simple_user)
    response = app.post_json(url, params=content, headers=headers)
    assert response.json['err'] == 0
    assert response.json['result'] == 1
    assert set(response.json['unknown_uuids']) == set(unknown_uuids)


def test_user_synchronization_modification_profile(app):
    from authentic2_idp_oidc.models import OIDCAuthorization, OIDCClient
    from authentic2_idp_oidc.utils import make_sub

    uuids = []
    users = []
    precreate_dt = datetime.datetime.now()
    profile_type = ProfileType.objects.create(name='Referee', slug='referee')
    client_ct = ContentType.objects.get_for_model(OIDCClient)
    oidc_client = OIDCClient.objects.create(
        name='Synchronized client',
        slug='synchronized-client',
        sector_identifier_uri='https://sync-client.example.org/',
        identifier_policy=OIDCClient.POLICY_PAIRWISE_REVERSIBLE,
        has_api_access=True,
        authorization_mode=OIDCClient.AUTHORIZATION_MODE_BY_SERVICE,
    )
    headers = basic_authorization_oidc_client(oidc_client)

    for i in range(100):
        user = User.objects.create(first_name='john', last_name='doe', email='john.doe.%s@ad.dre.ss' % i)
        uuids.append(user.uuid)
        profile = None
        if i % 2:
            profile = Profile.objects.create(
                profile_type=profile_type,
                user=user,
                email=f'referee-{i}@ad.dre.ss',
                identifier=f'referee-{i}',
                data={'foo': i},
            )
        users.append(
            (
                user,
                profile,
            )
        )
        if i % 4:
            OIDCAuthorization.objects.create(
                client_ct=client_ct,
                client_id=oidc_client.id,
                user=user,
                scopes='openid email profile',
                expired=datetime.datetime.now() + datetime.timedelta(hours=5),
            )

    url = reverse('a2-api-users-synchronization')

    # first attempt with no profile information
    uuids = [make_sub(oidc_client, user) for user, _ in users]
    content = {
        'known_uuids': uuids,
    }
    response = app.post_json(url, params=content, headers=headers)
    assert response
    assert not response.json['unknown_uuids']

    # this time subs with profile info
    uuids = [make_sub(oidc_client, user, profile) for user, profile in users]
    content = {
        'known_uuids': uuids,
    }
    response = app.post_json(url, params=content, headers=headers)
    assert response
    assert not response.json['unknown_uuids']

    response = app.get(
        '/api/users/?modified__gt=%s' % precreate_dt.strftime('%Y-%m-%dT%H:%M:%S'), headers=headers
    )
    assert len(response.json['results']) == 75


def test_user_synchronization_full(app, admin):
    headers = basic_authorization_header(admin)
    uuids = []
    for _ in range(100):
        user = User.objects.create(first_name='jim', last_name='jam')
        uuids.append(user.uuid)
    unknown_uuids = [uuid.uuid4().hex for i in range(100)]
    url = reverse('a2-api-users-synchronization')
    content = {
        'known_uuids': uuids + unknown_uuids,
        'full_known_users': 1,
    }
    random.shuffle(content['known_uuids'])
    response = app.post_json(url, params=content, headers=headers)
    assert response.json['err'] == 0
    assert response.json['result'] == 1

    # known users returned as part of api's full mode:
    assert len(response.json['known_users']) == 100
    for user_dict in response.json['known_users']:
        assert user_dict['first_name'] == 'jim'
        assert user_dict['last_name'] == 'jam'
        assert {
            'uuid',
            'email',
            'is_staff',
            'is_superuser',
            'email_verified',
            'ou',
            'is_active',
            'deactivation',
            'modified',
        }.issubset(set(user_dict.keys()))
