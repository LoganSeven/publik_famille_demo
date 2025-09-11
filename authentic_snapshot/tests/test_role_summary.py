from unittest import mock

import responses

from authentic2 import role_summary
from authentic2.a2_rbac.models import Role
from authentic2.a2_rbac.utils import get_default_ou


def test_role_summary(db, simple_role, superuser, tmpdir, monkeypatch, caplog):
    caplog.set_level('DEBUG')
    parent_role = Role.objects.create(
        name='parent role', slug='parent-role', ou=get_default_ou(), uuid='73b3d397fbcf4252aecedb195bab8281'
    )
    simple_role.add_parent(parent_role)

    with responses.RequestsMock() as rsps:
        rsps.get(
            'https://foo.whatever.none/api/export-import/',
            status=404,
        )
        rsps.get(
            'http://example.org/api/export-import/',
            json={
                'data': [
                    {
                        'id': 'forms',
                        'text': 'Formulaires',
                        'singular': 'Formulaire',
                        'urls': {'list': 'http://example.org/api/export-import/forms/'},
                    },
                    {
                        'id': 'workflows',
                        'text': 'Workflows',
                        'singular': 'Workflow',
                        'urls': {'list': 'http://example.org/api/export-import/workflows/'},
                    },
                    {
                        'id': 'roles',
                        'text': 'Roles',
                        'singular': 'Role',
                        'urls': {'list': 'http://example.org/api/export-import/roles/'},
                    },
                ]
            },
        )
        rsps.get(
            'http://example.org/api/export-import/forms/',
            json={
                'data': [
                    {
                        'id': 'foo',
                        'text': 'Foo',
                        'type': 'forms',
                        'urls': {
                            'dependencies': 'http://example.org/api/export-import/forms/foo/dependencies/',
                            'redirect': 'http://example.org/api/export-import/forms/foo/redirect/',
                        },
                    },
                    {
                        'id': 'bar',
                        'text': 'Bar',
                        'type': 'forms',
                        'urls': {
                            'dependencies': 'http://example.org/api/export-import/forms/bar/dependencies/',
                            'redirect': 'http://example.org/api/export-import/forms/bar/redirect/',
                        },
                    },
                ]
            },
        )
        rsps.get(
            'http://example.org/api/export-import/forms/foo/dependencies/',
            json={
                'data': [
                    {
                        'id': simple_role.slug,
                        'text': simple_role.name,
                        'uuid': simple_role.uuid,
                        'type': 'roles',
                    }
                ]
            },
        )
        rsps.get(
            'http://example.org/api/export-import/forms/bar/dependencies/',
            json={
                'data': [
                    {
                        'id': parent_role.slug,
                        'text': parent_role.name,
                        'uuid': parent_role.uuid,
                        'type': 'roles',
                    }
                ]
            },
        )
        rsps.get(
            'http://example.org/api/export-import/workflows/',
            json={
                'data': [
                    {
                        'id': 'test',
                        'text': 'Test',
                        'type': 'workflows',
                        'urls': {
                            'dependencies': 'http://example.org/api/export-import/workflows/test/dependencies/',
                            'redirect': 'http://example.org/api/export-import/workflows/test/redirect/',
                        },
                    },
                ]
            },
        )
        rsps.get(
            'http://example.org/api/export-import/workflows/test/dependencies/',
            json={
                'data': [
                    {
                        'id': simple_role.slug,
                        'text': simple_role.name,
                        'uuid': simple_role.uuid,
                        'type': 'roles',
                    }
                ]
            },
        )
        rsps.get(
            'http://example.org/api/export-import/roles/',
            json={
                'data': [
                    {
                        'id': 'agent',
                        'text': 'Agent',
                        'type': 'roles',
                        'urls': {},
                    },
                ]
            },
        )

        data = role_summary.build_roles_summary_cache()
        assert data == {
            simple_role.uuid: {
                'name': 'simple role',
                'slug': 'simple-role',
                'parents': [parent_role.uuid],
                'type_objects': [
                    {
                        'hit': [
                            {
                                'id': 'foo',
                                'text': 'Foo',
                                'type': 'forms',
                                'urls': {
                                    'dependencies': 'http://example.org/api/export-import/forms/foo/dependencies/',
                                    'redirect': 'http://example.org/api/export-import/forms/foo/redirect/',
                                },
                            }
                        ],
                        'id': 'forms',
                        'singular': 'Formulaire',
                        'text': 'Formulaires',
                        'urls': {'list': 'http://example.org/api/export-import/forms/'},
                    },
                    {
                        'hit': [
                            {
                                'id': 'test',
                                'text': 'Test',
                                'type': 'workflows',
                                'urls': {
                                    'dependencies': 'http://example.org/api/export-import/workflows/test/dependencies/',
                                    'redirect': 'http://example.org/api/export-import/workflows/test/redirect/',
                                },
                            }
                        ],
                        'id': 'workflows',
                        'singular': 'Workflow',
                        'text': 'Workflows',
                        'urls': {'list': 'http://example.org/api/export-import/workflows/'},
                    },
                ],
                'parents_type_objects': [
                    {
                        'hit': [
                            {
                                'id': 'bar',
                                'text': 'Bar',
                                'type': 'forms',
                                'urls': {
                                    'dependencies': 'http://example.org/api/export-import/forms/bar/dependencies/',
                                    'redirect': 'http://example.org/api/export-import/forms/bar/redirect/',
                                },
                            }
                        ],
                        'id': 'forms',
                        'singular': 'Formulaire',
                        'text': 'Formulaires',
                        'urls': {'list': 'http://example.org/api/export-import/forms/'},
                    }
                ],
            },
            parent_role.uuid: {
                'name': 'parent role',
                'slug': 'parent-role',
                'parents': [],
                'type_objects': [
                    {
                        'hit': [
                            {
                                'id': 'bar',
                                'text': 'Bar',
                                'type': 'forms',
                                'urls': {
                                    'dependencies': 'http://example.org/api/export-import/forms/bar/dependencies/',
                                    'redirect': 'http://example.org/api/export-import/forms/bar/redirect/',
                                },
                            }
                        ],
                        'id': 'forms',
                        'singular': 'Formulaire',
                        'text': 'Formulaires',
                        'urls': {'list': 'http://example.org/api/export-import/forms/'},
                    }
                ],
                'parents_type_objects': [],
            },
        }
        assert (
            '\n'.join(caplog.messages)  # do not use caplog.text, it contains line of code numbers
            == '''\
role-summary: retrieving url http://example.org/api/export-import/
role-summary: response {'data': [{'id': 'forms', 'text': 'Formulaires', 'singular': 'Formulaire', 'urls': {'list': 'http://example.org/api/export-import/forms/'}}, {'id': 'workflows', 'text': 'Workflows', 'singular': 'Workflow', 'urls': {'list': 'http://example.org/api/export-import/workflows/'}}, {'id': 'roles', 'text': 'Roles', 'singular': 'Role', 'urls': {'list': 'http://example.org/api/export-import/roles/'}}]}
role-summary: retrieving url http://example.org/api/export-import/forms/
role-summary: response {'data': [{'id': 'foo', 'text': 'Foo', 'type': 'forms', 'urls': {'dependencies': 'http://example.org/api/export-import/forms/foo/dependencies/', 'redirect': 'http://example.org/api/export-import/forms/foo/redirect/'}}, {'id': 'bar', 'text': 'Bar', 'type': 'forms', 'urls': {'dependencies': 'http://example.org/api/export-import/forms/bar/dependencies/', 'redirect': 'http://example.org/api/export-import/forms/bar/redirect/'}}]}
role-summary: retrieving url http://example.org/api/export-import/forms/foo/dependencies/
role-summary: response {'data': [{'id': 'simple-role', 'text': 'simple role', 'uuid': '6115a844a91840f6a83f942c0180f80f', 'type': 'roles'}]}
role-summary: retrieving url http://example.org/api/export-import/forms/bar/dependencies/
role-summary: response {'data': [{'id': 'parent-role', 'text': 'parent role', 'uuid': '73b3d397fbcf4252aecedb195bab8281', 'type': 'roles'}]}
role-summary: retrieving url http://example.org/api/export-import/workflows/
role-summary: response {'data': [{'id': 'test', 'text': 'Test', 'type': 'workflows', 'urls': {'dependencies': 'http://example.org/api/export-import/workflows/test/dependencies/', 'redirect': 'http://example.org/api/export-import/workflows/test/redirect/'}}]}
role-summary: retrieving url http://example.org/api/export-import/workflows/test/dependencies/
role-summary: response {'data': [{'id': 'simple-role', 'text': 'simple role', 'uuid': '6115a844a91840f6a83f942c0180f80f', 'type': 'roles'}]}
role-summary: retrieving url http://example.org/api/export-import/roles/
role-summary: response {'data': [{'id': 'agent', 'text': 'Agent', 'type': 'roles', 'urls': {}}]}
role-summary: retrieving url https://foo.whatever.none/api/export-import/
role-summary: error 404 Client Error: Not Found for url: https://foo.whatever.none/api/export-import/'''
        )


@mock.patch('authentic2.role_summary.get_roles_summary_cache_path')
@mock.patch('authentic2.role_summary.build_roles_summary_cache')
def test_write_roles_summary_cache_error(build_roles_summary_cache, get_roles_summary_cache_path, tmp_path):
    cache_path = tmp_path / 'cache.json'
    get_roles_summary_cache_path.return_value = str(cache_path)
    build_roles_summary_cache.side_effect = Exception('Boom!')

    role_summary.write_roles_summary_cache()

    assert role_summary.get_roles_summary_cache() == {
        'error': 'Building of roles summary cache failed: Boom!'
    }


@mock.patch('authentic2.role_summary.get_roles_summary_cache_path')
def test_read_roles_summary_cache_error(get_roles_summary_cache_path, tmp_path):
    cache_path = tmp_path / 'cache.json'
    get_roles_summary_cache_path.return_value = str(cache_path)
    with cache_path.open('w') as fd:
        fd.write('x')
    assert role_summary.get_roles_summary_cache() == {
        'error': 'Loading of roles summary cache failed: Expecting value: line 1 column 1 (char 0)'
    }
