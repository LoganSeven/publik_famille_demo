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

import pytest
from django.core.exceptions import ValidationError

from authentic2.a2_rbac.models import OrganizationalUnit as OU
from authentic2.a2_rbac.models import Role, RoleParenting
from authentic2.data_transfer import (
    ExportContext,
    ImportContext,
    RoleDeserializer,
    export_ous,
    export_roles,
    export_site,
    import_ou,
    import_site,
    search_role,
)
from authentic2.models import Service
from authentic2.utils.misc import get_hex_uuid


def test_export_basic_role(db):
    role = Role.objects.create(name='basic role', slug='basic-role', uuid=get_hex_uuid())
    query_set = Role.objects.filter(uuid=role.uuid)
    roles = export_roles(ExportContext(role_qs=query_set))
    assert len(roles) == 1
    role_dict = roles[0]
    for key, value in role.export_json().items():
        assert role_dict[key] == value


def test_export_role_with_parents(db):
    grand_parent_role = Role.objects.create(
        name='test grand parent role', slug='test-grand-parent-role', uuid=get_hex_uuid()
    )
    parent_1_role = Role.objects.create(
        name='test parent 1 role', slug='test-parent-1-role', uuid=get_hex_uuid()
    )
    parent_1_role.add_parent(grand_parent_role)
    parent_2_role = Role.objects.create(
        name='test parent 2 role', slug='test-parent-2-role', uuid=get_hex_uuid()
    )
    parent_2_role.add_parent(grand_parent_role)
    child_role = Role.objects.create(name='test child role', slug='test-child-role', uuid=get_hex_uuid())
    child_role.add_parent(parent_1_role)
    child_role.add_parent(parent_2_role)

    query_set = Role.objects.filter(slug__startswith='test').order_by('slug')
    roles = export_roles(ExportContext(role_qs=query_set))
    assert len(roles) == 4

    child_role_dict = roles[0]
    assert child_role_dict['slug'] == child_role.slug
    parents = child_role_dict['parents']
    assert len(parents) == 2
    expected_slugs = {parent_1_role.slug, parent_2_role.slug}
    for parent in parents:
        assert parent['slug'] in expected_slugs
        expected_slugs.remove(parent['slug'])

    grand_parent_role_dict = roles[1]
    assert grand_parent_role_dict['slug'] == grand_parent_role.slug

    parent_1_role_dict = roles[2]
    assert parent_1_role_dict['slug'] == parent_1_role.slug
    parents = parent_1_role_dict['parents']
    assert len(parents) == 1
    assert parents[0]['slug'] == grand_parent_role.slug

    parent_2_role_dict = roles[3]
    assert parent_2_role_dict['slug'] == parent_2_role.slug
    parents = parent_2_role_dict['parents']
    assert len(parents) == 1
    assert parents[0]['slug'] == grand_parent_role.slug


def test_export_role_with_soft_deleted_parents(db):
    Role.objects.all().delete()
    parent_1_role = Role.objects.create(
        name='test parent 1 role', slug='test-parent-1-role', uuid=get_hex_uuid()
    )
    parent_2_role = Role.objects.create(
        name='test parent 2 role', slug='test-parent-2-role', uuid=get_hex_uuid()
    )
    child_role = Role.objects.create(name='test child role', slug='test-child-role', uuid=get_hex_uuid())
    child_role.add_parent(parent_1_role)
    child_role.add_parent(parent_2_role)
    child_role.remove_parent(parent_2_role)

    roles = export_roles(ExportContext(role_qs=Role.objects.all()))
    assert len(roles) == 3
    child_role_export = [x for x in roles if x['name'] == 'test child role'][0]
    assert [x['name'] for x in child_role_export['parents']] == ['test parent 1 role']


def test_export_ous(db):
    ou = OU.objects.create(name='ou name', slug='ou-slug', description='ou description')
    ous = export_ous(ExportContext(ou_qs=OU.objects.filter(name='ou name')))
    assert len(ous) == 1
    ou_d = ous[0]
    assert ou_d['name'] == ou.name
    assert ou_d['slug'] == ou.slug
    assert ou_d['description'] == ou.description


def test_search_role_by_uuid(db):
    uuid = get_hex_uuid()
    role_d = {'uuid': uuid, 'slug': 'role-slug'}
    role = Role.objects.create(**role_d)
    assert role == search_role({'uuid': uuid, 'slug': 'other-role-slug'})


def test_search_role_by_slug(db):
    role_d = {'uuid': get_hex_uuid(), 'slug': 'role-slug'}
    role = Role.objects.create(**role_d)
    assert role == search_role({'uuid': get_hex_uuid(), 'slug': 'role-slug', 'ou': None, 'service': None})


def test_search_role_not_found(db):
    assert (
        search_role(
            {'uuid': get_hex_uuid(), 'slug': 'role-slug', 'name': 'role name', 'ou': None, 'service': None}
        )
        is None
    )


def test_search_role_slug_not_unique(db):
    role1_d = {'uuid': get_hex_uuid(), 'slug': 'role-slug', 'name': 'role name'}
    role2_d = {'uuid': get_hex_uuid(), 'slug': 'role-slug', 'name': 'role name'}
    ou = OU.objects.create(name='some ou', slug='some-ou')
    role1 = Role.objects.create(ou=ou, **role1_d)
    Role.objects.create(**role2_d)
    assert role1 == search_role(role1.export_json())


def test_role_deserializer(db):
    rd = RoleDeserializer(
        {
            'name': 'some role',
            'description': 'some role description',
            'slug': 'some-role',
            'uuid': get_hex_uuid(),
            'ou': None,
            'service': None,
        },
        ImportContext(),
    )
    assert rd._parents is None
    assert rd._attributes is None
    assert rd._obj is None
    role, status = rd.deserialize()
    assert status == 'created'
    assert role.name == 'some role'
    assert role.description == 'some role description'
    assert role.slug == 'some-role'
    assert rd._obj == role


def test_role_deserializer_with_ou(db):
    ou = OU.objects.create(name='some ou', slug='some-ou')
    rd = RoleDeserializer(
        {
            'uuid': get_hex_uuid(),
            'name': 'some role',
            'description': 'some role description',
            'slug': 'some-role',
            'ou': {'slug': 'some-ou'},
            'service': None,
        },
        ImportContext(),
    )
    role, dummy = rd.deserialize()
    assert role.ou == ou


def test_role_deserializer_missing_ou(db):
    rd = RoleDeserializer(
        {
            'uuid': get_hex_uuid(),
            'name': 'some role',
            'description': 'role description',
            'slug': 'some-role',
            'ou': {'slug': 'some-ou'},
            'service': None,
        },
        ImportContext(),
    )
    with pytest.raises(ValidationError):
        rd.deserialize()


def test_role_deserializer_update_ou(db):
    ou1 = OU.objects.create(name='ou 1', slug='ou-1')
    ou2 = OU.objects.create(name='ou 2', slug='ou-2')
    uuid = get_hex_uuid()
    assert Role.objects.exclude(slug__startswith='_').count() == 0
    existing_role = Role.objects.create(uuid=uuid, slug='some-role', ou=ou1)
    rd = RoleDeserializer(
        {'uuid': uuid, 'name': 'some-role', 'slug': 'some-role', 'ou': {'slug': 'ou-2'}, 'service': None},
        ImportContext(),
    )
    assert rd.deserialize()
    existing_role.refresh_from_db()
    assert existing_role.ou == ou2
    assert Role.objects.exclude(slug__startswith='_').count() == 1


def test_role_deserializer_update_fields(db):
    uuid = get_hex_uuid()
    assert Role.objects.exclude(slug__startswith='_').count() == 0
    existing_role = Role.objects.create(uuid=uuid, slug='some-role', name='some role')
    rd = RoleDeserializer(
        {'uuid': uuid, 'slug': 'some-role', 'name': 'some role changed', 'ou': None, 'service': None},
        ImportContext(),
    )
    role, dummy = rd.deserialize()
    existing_role.refresh_from_db()
    assert role == existing_role
    assert existing_role.name == 'some role changed'
    assert Role.objects.exclude(slug__startswith='_').count() == 1


def test_role_deserializer_with_attributes(db):
    attributes_data = {
        'is_superuser': dict(name='is_superuser', kind='string', value='true'),
        'emails': dict(name='emails', kind='json', value='["a@a.com"]'),
    }
    rd = RoleDeserializer(
        {
            'uuid': get_hex_uuid(),
            'name': 'some role',
            'description': 'some role description',
            'slug': 'some-role',
            'attributes': list(attributes_data.values()),
            'ou': None,
            'service': None,
        },
        ImportContext(),
    )
    role, status = rd.deserialize()
    rd.attributes()
    assert status == 'created'
    assert role.is_superuser is True
    assert role.emails == ['a@a.com']


def test_role_deserializer_creates_admin_role(db):
    role_dict = {
        'name': 'some role',
        'slug': 'some-role',
        'uuid': get_hex_uuid(),
        'ou': None,
        'service': None,
    }
    rd = RoleDeserializer(role_dict, ImportContext())
    rd.deserialize()
    Role.objects.get(slug='_a2-managers-of-role-some-role')


def test_role_deserializer_parenting_existing_parent(db):
    parent_role_dict = {
        'name': 'grand parent role',
        'slug': 'grand-parent-role',
        'uuid': get_hex_uuid(),
        'ou': None,
        'service': None,
    }
    parent_role = Role.objects.create(**parent_role_dict)
    child_role_dict = {
        'name': 'child role',
        'slug': 'child-role',
        'parents': [parent_role_dict],
        'uuid': get_hex_uuid(),
        'ou': None,
        'service': None,
    }

    rd = RoleDeserializer(child_role_dict, ImportContext())
    child_role, status = rd.deserialize()
    created, dummy = rd.parentings()

    assert status == 'created'
    assert len(created) == 1
    parenting = created[0]
    assert parenting.direct is True
    assert parenting.parent == parent_role
    assert parenting.child == child_role


def test_role_deserializer_parenting_non_existing_parent(db):
    parent_role_dict = {
        'name': 'grand parent role',
        'slug': 'grand-parent-role',
        'uuid': get_hex_uuid(),
        'ou': None,
        'service': None,
    }
    child_role_dict = {
        'name': 'child role',
        'slug': 'child-role',
        'parents': [parent_role_dict],
        'uuid': get_hex_uuid(),
        'ou': None,
        'service': None,
    }
    rd = RoleDeserializer(child_role_dict, ImportContext())
    rd.deserialize()
    with pytest.raises(ValidationError) as excinfo:
        rd.parentings()

    assert 'Could not find parent role' in str(excinfo.value)


def test_role_deserializer_emails(db):
    role_dict = {
        'name': 'test role',
        'slug': 'test-role-slug',
        'uuid': get_hex_uuid(),
        'ou': None,
        'emails': ['a@example.com'],
    }

    import_context = ImportContext()
    rd = RoleDeserializer(role_dict, import_context)
    rd.deserialize()

    role = Role.objects.get(slug='test-role-slug')
    assert role.emails == ['a@example.com']


def test_role_deserializer_permissions(db):
    ou = OU.objects.create(slug='some-ou')
    other_role_dict = {'name': 'other role', 'slug': 'other-role-slug', 'uuid': get_hex_uuid(), 'ou': ou}
    other_role = Role.objects.create(**other_role_dict)
    other_role_dict['permisison'] = {
        'operation': {'slug': 'admin'},
        'ou': {'slug': 'default', 'name': 'Collectivit\u00e9 par d\u00e9faut'},
        'target_ct': {'app_label': 'a2_rbac', 'model': 'role'},
        'target': {
            'slug': 'role-deux',
            'ou': {'slug': 'default', 'name': 'Collectivit\u00e9 par d\u00e9faut'},
            'service': None,
            'name': 'role deux',
        },
    }
    some_role_dict = {
        'name': 'some role',
        'slug': 'some-role',
        'uuid': get_hex_uuid(),
        'ou': None,
        'service': None,
    }
    some_role_dict['permissions'] = [
        {
            'operation': {'slug': 'add'},
            'ou': None,
            'target_ct': {'app_label': 'a2_rbac', 'model': 'role'},
            'target': {'slug': 'other-role-slug', 'ou': {'slug': 'some-ou'}, 'service': None},
        }
    ]

    import_context = ImportContext()
    rd = RoleDeserializer(some_role_dict, import_context)
    rd.deserialize()
    perm_created, perm_deleted = rd.permissions()

    assert len(perm_created) == 1
    assert len(perm_deleted) == 0
    del some_role_dict['permissions']
    role = Role.objects.get(slug=some_role_dict['slug'])
    assert role.permissions.count() == 1
    perm = role.permissions.first()
    assert perm.operation.slug == 'add'
    assert not perm.ou
    assert perm.target == other_role

    # that one should delete permissions
    rd = RoleDeserializer(some_role_dict, import_context)
    role, _ = rd.deserialize()
    perm_created, perm_deleted = rd.permissions()
    assert role.permissions.count() == 0
    assert len(perm_created) == 0
    assert len(perm_deleted) == 1


def test_permission_on_role(db):
    perm_ou = OU.objects.create(slug='perm-ou', name='perm ou')
    perm_role = Role.objects.create(slug='perm-role', ou=perm_ou, name='perm role')

    some_role_dict = {'name': 'some role', 'slug': 'some-role-slug', 'ou': None, 'service': None}
    some_role_dict['permissions'] = [
        {
            'operation': {'slug': 'admin'},
            'ou': {'slug': 'perm-ou', 'name': 'perm-ou'},
            'target_ct': {'app_label': 'a2_rbac', 'model': 'role'},
            'target': {
                'slug': 'perm-role',
                'ou': {'slug': 'perm-ou', 'name': 'perm ou'},
                'service': None,
                'name': 'perm role',
            },
        }
    ]

    import_context = ImportContext()
    rd = RoleDeserializer(some_role_dict, import_context)
    rd.deserialize()
    perm_created, dummy = rd.permissions()
    assert len(perm_created) == 1
    perm = perm_created[0]
    assert perm.target == perm_role
    assert perm.ou == perm_ou
    assert perm.operation.slug == 'admin'


def test_permission_on_contentype(db):
    perm_ou = OU.objects.create(slug='perm-ou', name='perm ou')
    some_role_dict = {'name': 'some role', 'slug': 'some-role-slug', 'ou': None, 'service': None}
    some_role_dict['permissions'] = [
        {
            'operation': {'slug': 'admin'},
            'ou': {'slug': 'perm-ou', 'name': 'perm-ou'},
            'target_ct': {'model': 'contenttype', 'app_label': 'contenttypes'},
            'target': {'model': 'logentry', 'app_label': 'admin'},
        }
    ]

    import_context = ImportContext()
    rd = RoleDeserializer(some_role_dict, import_context)
    rd.deserialize()
    perm_created, dummy = rd.permissions()
    assert len(perm_created) == 1
    perm = perm_created[0]
    assert perm.target.app_label == 'admin'
    assert perm.target.model == 'logentry'
    assert perm.ou == perm_ou


def import_ou_created(db):
    uuid = get_hex_uuid()
    ou_d = {'uuid': uuid, 'slug': 'ou-slug', 'name': 'ou name'}
    ou, status = import_ou(ou_d)
    assert status == 'created'
    assert ou.uuid == ou_d['uuid']
    assert ou.slug == ou_d['slug']
    assert ou.name == ou_d['name']


def import_ou_updated(db):
    ou = OU.objects.create(slug='some-ou', name='ou name')
    ou_d = {'uuid': ou.uuid, 'slug': ou.slug, 'name': 'new name'}
    ou_updated, status = import_ou(ou_d)
    assert status == 'updated'
    assert ou == ou_updated
    assert ou.name == 'new name'


def testi_import_site_empty():
    res = import_site({}, ImportContext())
    assert res.roles == {'created': [], 'updated': []}
    assert res.ous == {'created': [], 'updated': []}
    assert res.parentings == {'created': [], 'deleted': []}


def test_import_site_roles(db):
    parent_role_dict = {
        'name': 'grand parent role',
        'slug': 'grand-parent-role',
        'uuid': get_hex_uuid(),
        'ou': None,
        'service': None,
    }
    child_role_dict = {
        'name': 'child role',
        'slug': 'child-role',
        'parents': [parent_role_dict],
        'uuid': get_hex_uuid(),
        'ou': None,
        'service': None,
    }
    roles = [parent_role_dict, child_role_dict]
    res = import_site({'roles': roles}, ImportContext())
    created_roles = res.roles['created']
    assert len(created_roles) == 2
    parent_role = Role.objects.get(**parent_role_dict)
    del child_role_dict['parents']
    child_role = Role.objects.get(**child_role_dict)
    assert created_roles[0] == parent_role
    assert created_roles[1] == child_role

    assert len(res.parentings['created']) == 1
    assert res.parentings['created'][0] == RoleParenting.objects.get(
        child=child_role, parent=parent_role, direct=True
    )


def test_roles_import_ignore_technical_role(db):
    roles = [{'name': 'some role', 'description': 'some role description', 'slug': '_some-role'}]
    res = import_site({'roles': roles}, ImportContext())
    assert res.roles == {'created': [], 'updated': []}


def test_roles_import_ignore_technical_role_with_service(db):
    roles = [{'name': 'some role', 'description': 'some role description', 'slug': '_some-role'}]
    res = import_site({'roles': roles}, ImportContext())
    assert res.roles == {'created': [], 'updated': []}


def test_import_role_handle_manager_role_parenting(db):
    parent_role_dict = {
        'name': 'grand parent role',
        'slug': 'grand-parent-role',
        'uuid': get_hex_uuid(),
        'ou': None,
        'service': None,
    }
    parent_role_manager_dict = {
        'name': 'Administrateur du role grand parent role',
        'slug': '_a2-managers-of-role-grand-parent-role',
        'uuid': get_hex_uuid(),
        'ou': None,
        'service': None,
    }
    child_role_dict = {
        'name': 'child role',
        'slug': 'child-role',
        'parents': [parent_role_dict, parent_role_manager_dict],
        'uuid': get_hex_uuid(),
        'ou': None,
        'service': None,
    }
    import_site({'roles': [child_role_dict, parent_role_dict]}, ImportContext())
    child = Role.objects.get(slug='child-role')
    manager = Role.objects.get(slug='_a2-managers-of-role-grand-parent-role')
    rp = RoleParenting.objects.get(child=child, parent=manager, direct=True)
    assert str(rp)


def test_import_roles_role_delete_orphans(db):
    roles = [{'name': 'some role', 'description': 'some role description', 'slug': '_some-role'}]
    with pytest.raises(ValidationError):
        import_site({'roles': roles}, ImportContext(role_delete_orphans=True))


def test_roles_import_no_slug(db):
    roles = [{'name': 'some role', 'description': 'some role description', 'ou': None}]
    import_site({'roles': roles}, ImportContext())
    role = Role.objects.get(name='some role')
    assert role.slug == 'some-role'


def test_import_ou(db):
    uuid = get_hex_uuid()
    name = 'ou name'
    ous = [{'uuid': uuid, 'slug': 'ou-slug', 'name': name}]
    res = import_site({'ous': ous}, ImportContext())
    assert len(res.ous['created']) == 1
    ou = res.ous['created'][0]
    assert ou.uuid == uuid
    assert ou.name == name
    Role.objects.get(slug='_a2-managers-of-ou-slug')


def test_import_ou_already_existing(db):
    uuid = get_hex_uuid()
    ou_d = {'uuid': uuid, 'slug': 'ou-slug', 'name': 'ou name'}
    ou = OU.objects.create(**ou_d)
    num_ous = OU.objects.count()
    res = import_site({'ous': [ou_d]}, ImportContext())
    assert len(res.ous['created']) == 0
    assert num_ous == OU.objects.count()
    assert ou == OU.objects.get(uuid=uuid)


def test_import_context_flags(db):
    ous = [{'uuid': get_hex_uuid(), 'slug': 'ou-slug', 'name': 'ou name'}]
    roles = [
        {
            'name': 'other role',
            'slug': 'other-role-slug',
            'uuid': get_hex_uuid(),
            'ou': {'slug': 'ou-slug'},
        }
    ]
    d = {'ous': ous, 'roles': roles}
    import_site(d, ImportContext(import_roles=False, import_ous=False))
    assert Role.objects.exclude(slug__startswith='_').count() == 0
    assert OU.objects.exclude(slug='default').count() == 0
    with pytest.raises(ValidationError) as e:
        import_site(d, ImportContext(import_roles=True, import_ous=False))
    assert 'missing Organizational' in e.value.args[0]
    assert Role.objects.exclude(slug__startswith='_').count() == 0
    assert OU.objects.exclude(slug='default').count() == 0
    import_site(d, ImportContext(import_roles=False, import_ous=True))
    assert Role.objects.exclude(slug__startswith='_').count() == 0
    assert OU.objects.exclude(slug='default').count() == 1
    import_site(d, ImportContext(import_roles=True, import_ous=True))
    assert Role.objects.exclude(slug__startswith='_').count() == 1
    assert OU.objects.exclude(slug='default').count() == 1


def test_export_site(db):
    ou = OU.objects.create(name='ou')
    Role.objects.create(name='role', ou=ou)
    d = export_site()
    assert len([ou for ou in d['ous'] if ou['slug'] != 'default']) == 1
    assert len([role for role in d['roles'] if role['slug'][0] != '_']) == 1
    d = export_site(ExportContext(ou_qs=OU.objects.filter(name='ou')))
    assert len(d['ous']) == 1
    d = export_site(ExportContext(role_qs=Role.objects.filter(name='role')))
    assert len(d['roles']) == 1
    d = export_site(ExportContext(export_roles=False))
    assert 'roles' not in d
    d = export_site(ExportContext(export_ous=False))
    assert 'ous' not in d


def test_role_validate_unique(db):
    ou = OU.objects.create(name='ou', slug='ou')
    Role.objects.create(name='role1', slug='role1', ou=ou)
    Role.objects.create(name='role2', slug='role2', ou=ou)

    data = {
        'roles': [
            {
                'name': 'role1',
                'slug': 'role2',
                'ou': {'slug': 'ou'},
            }
        ]
    }
    with pytest.raises(ValidationError, match=r'Role "role1": name="role1": Name already used'):
        import_site(data)


@pytest.mark.parametrize('uuid', [None, 1, [], {}, '', 'a'])
def test_import_roles_invalid_uuid(uuid, db):
    with pytest.raises(ValidationError, match='.*invalid uuid'):
        import_site(
            {
                'roles': [
                    {
                        'ou': {'slug': 'default'},
                        'uuid': uuid,
                        'name': 'role',
                        'description': 'role',
                        'slug': '-role',
                    }
                ]
            }
        )


@pytest.mark.parametrize('slug', [None, 1, [], {}, '', 'é', 'x a'])
def test_import_roles_invalid_slug(slug, db):
    with pytest.raises(ValidationError, match='.*invalid slug'):
        import_site(
            {
                'roles': [
                    {
                        'ou': {'slug': 'default'},
                        'uuid': '1d2a8aea-f8e3-40c1-aed5-6fc1327fdba0',
                        'name': 'role',
                        'description': 'role',
                        'slug': slug,
                    }
                ]
            }
        )


def test_import_roles_multiple_object_returned(db):
    s1 = Service.objects.create(slug='s1')
    s2 = Service.objects.create(slug='s2')
    s3 = Service.objects.create(slug='s3')
    default_ou = OU.objects.get()
    Role.objects.create(slug='slug', name='Role1', ou=default_ou, service=s1)
    Role.objects.create(slug='slug', name='Role2', ou=default_ou, service=s2)

    roles = [
        {
            'name': 'some role',
            'description': 'some role description',
            'slug': 'some-role',
            'ou': {
                'slug': 'default',
            },
            'parents': [
                {
                    'name': 'Role3',
                    'slug': 'slug',
                    'ou': {
                        'slug': 'default',
                    },
                    'service': {
                        'slug': 's3',
                        'ou': {'slug': 'default'},
                    },
                }
            ],
        }
    ]
    with pytest.raises(ValidationError, match=r'Could not find parent role'):
        import_site({'roles': roles})

    Role.objects.create(slug='slug', name='Role3', ou=default_ou, service=s3)
    import_site({'roles': roles})
