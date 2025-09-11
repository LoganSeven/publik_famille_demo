# authentic2 - © Entr'ouvert

import types

import faker
from django.contrib.auth import get_user_model

from authentic2.a2_rbac.models import OrganizationalUnit as OU
from authentic2.a2_rbac.models import Role
from authentic2.a2_rbac.utils import get_default_ou
from authentic2.custom_user.models import User
from tests.utils import scoped_db_fixture

from .utils import AdminTestMixin

OU_JSON = {'slug': 'default', 'uuid': '1' * 32, 'name': 'Default organizational unit'}


@scoped_db_fixture(scope='module', autouse=True)
def setup():
    OU.objects.filter(default=True).update(uuid='1' * 32)
    ou = get_default_ou()

    fake = faker.Faker()
    fake.seed_instance(4567)

    users = [
        User.objects.create(
            ou=ou, first_name=fake.first_name(), last_name=fake.last_name(), email=fake.free_email()
        )
        for _ in range(20)
    ]

    same_last_name = fake.last_name()

    users += [
        User.objects.create(
            ou=ou, first_name=fake.first_name(), last_name=same_last_name, email=fake.free_email()
        )
        for _ in range(20)
    ]

    return types.SimpleNamespace(users=users, ou=ou, same_last_name=same_last_name)


class TestUsersAPI:
    def test_unauthenticated(self, app, setup):
        app.get('/api/users/', status=401)
        app.get(f'/api/users/{setup.users[0].uuid}/', status=401)
        app.post(f'/api/users/{setup.users[0].uuid}/', status=401)
        app.put(f'/api/users/{setup.users[0].uuid}/', status=401)
        app.patch(f'/api/users/{setup.users[0].uuid}/', status=401)

    class TestAdmin(AdminTestMixin):
        def test_list(self, app, django_assert_num_queries):
            # first query to initialize ContentType cache
            app.get('/api/users/?limit=1')
            with django_assert_num_queries(12):
                resp = app.get('/api/users/')
            assert len(resp.json['results'])
            assert all('roles' in user for user in resp.json['results'])
            resp = app.get('/api/users/?include-roles=true')
            assert all('roles' in user for user in resp.json['results'])
            resp = app.get('/api/users/?include-roles=false')
            assert all('roles' not in user for user in resp.json['results'])

        def test_pagination(self, app):
            # limit to 10 to have multiple pages of results
            resp = app.get('/api/users/?limit=10')
            assert len(resp.json['results']) == 10
            uuids = {user['uuid'] for user in resp.json['results']}
            print(resp.json['next'])
            resp = app.get(resp.json['next'])
            assert len(resp.json['results']) == 10
            # next page is not the same users
            assert not uuids & {user['uuid'] for user in resp.json['results']}
            print(resp.json['previous'])
            resp = app.get(resp.json['previous'])
            # previous page, we nearly get the same users as there is some
            # artifact when going in reverse direction, see comment in
            # rest_framework.pagination.CursorPagination.get_previous_link():
            #
            #   The change in direction will introduce a paging artifact,
            #   where we end up skipping back a few extra items.
            #
            assert uuids - {user['uuid'] for user in resp.json['results']}

        def test_pagination_with_q(self, app, setup):
            # check pagination works with ordering specific to the ?q= parameter
            resp = app.get(f'/api/users/?limit=10&q={setup.same_last_name}')
            assert all(user['last_name'] == setup.same_last_name for user in resp.json['results'])
            resp = app.get(resp.json['next'])
            assert all(user['last_name'] == setup.same_last_name for user in resp.json['results'])
            resp = app.get(resp.json['previous'])
            # artifact when going reverse: it's not really the previous page (see comment in test_pagination)
            assert any(user['last_name'] == setup.same_last_name for user in resp.json['results'])


def test_roles_in_users_api(app, admin, ou1, ou2, service1, service2, freezer):
    freezer.move_to('2018-01-01')

    User = get_user_model()
    user1 = User(username='john.doe', email='john.doe@example.com')
    user1.set_password('password')
    user1.save()
    user2 = User(username='bob.smith', email='bob.smith@example.com')
    user2.set_password('password')
    user2.save()

    role1 = Role.objects.create(name='Role1')
    role1.ou = ou1
    role1.service = service1
    role1.members.add(user1)
    role2 = Role.objects.create(name='Role2')
    role2.ou = ou2
    role2.service = service2
    role2.members.add(user1)
    role2.members.add(user2)
    role2.add_parent(role1)
    role3 = Role.objects.create(name='Role3')
    role3.ou = get_default_ou()
    role3.members.add(user2)
    role3.add_parent(role2)

    freezer.move_to('2019-01-01')

    app.authorization = ('Basic', (admin.username, admin.clear_password))
    response = app.get(
        '/api/users/',
        params={'date_joined__lt': '2019-01-01T02:58:07+00:00'},
        status=200,
    )
    assert len(response.json['results']) == 2
    for user in response.json['results']:
        assert user['roles']
        for role in user['roles']:
            assert {'slug', 'name', 'uuid', 'service', 'description', 'ou'} == set(role.keys())
            role_object = Role.objects.get(uuid=role['uuid'])
            if getattr(role_object, 'ou', None) is not None:
                for ou_attr in ('uuid', 'slug', 'name'):
                    assert getattr(role_object.ou, ou_attr) == role['ou'][ou_attr]
            if getattr(role_object, 'service', None) is not None:
                for service_attr in ('slug', 'name'):
                    assert getattr(role_object.service, service_attr) == role['service'][service_attr]
                # Service OUs identified by their slug:
                if getattr(role_object.service, 'ou', None) is not None:
                    assert role_object.service.ou.slug == role['service']['ou']

    url = '/api/users/%s/' % admin.uuid
    response = app.get(url, status=200)
    assert len(response.json['roles']) == 7
    assert {role['slug'] for role in response.json['roles']} == {
        '_a2-manager',
        '_a2-manager-of-api-clients',
        '_a2-manager-of-authenticators',
        '_a2-manager-of-users',
        '_a2-manager-of-roles',
        '_a2-manager-of-services',
        '_a2-manager-of-organizational-units',
    }

    for user in [user1, user2]:
        url = '/api/users/%s/' % user.uuid
        response = app.get(url, status=200)
        roles = user.roles_and_parents().all()
        assert len(response.json['roles']) == roles.count()
        assert {role['name'] for role in response.json['roles']} == set(roles.values_list('name', flat=True))
