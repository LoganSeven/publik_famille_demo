import os

import pytest
from quixote import get_publisher

from wcs.roles import get_user_roles

from .utilities import clean_temporary_pub, create_temporary_pub


@pytest.fixture
def pub(request):
    pub = create_temporary_pub()
    pub.cfg['language'] = {'language': 'en'}
    pub.cfg['misc'] = {'charset': 'utf-8'}
    pub.role_class.wipe()
    pub.user_class.wipe()
    return pub


def teardown_module(module):
    clean_temporary_pub()


def test_slug(pub):
    role = get_publisher().role_class(name='Hello world')
    role.store()
    assert role.slug == 'hello-world'


def test_duplicated_name(pub):
    role = get_publisher().role_class(name='Hello world')
    role.store()
    assert role.slug == 'hello-world'
    role = get_publisher().role_class(name='Hello world')
    role.store()
    assert role.slug == 'hello-world-1'


def test_get_user_roles(pub):
    get_publisher().role_class(name='f1').store()
    get_publisher().role_class(name='é1').store()
    get_publisher().role_class(name='a1').store()
    assert [x[1] for x in get_user_roles()] == ['a1', 'é1', 'f1']


def test_get_emails(pub):
    role = get_publisher().role_class(name='role')
    role.emails_to_members = True
    role.store()

    users = []
    for i in range(2):
        user = pub.user_class(name='John Doe %s' % i)
        user.email = 'john.doe.%s@example.com' % i
        user.add_roles([role.id])
        user.store()
        users.append(user)

    assert len(set(role.get_emails())) == 2
    users[-1].is_active = False
    users[-1].store()
    assert len(set(role.get_emails())) == 1


def test_variables(pub):
    role = get_publisher().role_class(name='Hello world')
    role.uuid = 'plop'
    role.store()
    assert role.get_substitution_variables() == {
        'name': 'Hello world',
        'details': '',
        'emails': '',
        'uuid': 'plop',
    }


def test_cache(pub, sql_queries):
    role = get_publisher().role_class(name='Hello world')
    role.store()

    for cache in (False, True):
        sql_queries.clear()
        get = pub.role_class.cached_get if cache else pub.role_class.get
        for i in range(5):
            assert get(role.id).name == 'Hello world'
            assert len(sql_queries) == 1 if cache else (i + 1)

    for cache in (False, True):
        sql_queries.clear()
        get = pub.role_class.cached_get if cache else pub.role_class.get
        for i in range(5):
            with pytest.raises(KeyError):
                assert get('xxx')
            assert len(sql_queries) == 1 if cache else (i + 1)

            assert get('xxx', ignore_errors=True) is None
            assert len(sql_queries) == 1 if cache else (i + 1)


def test_all_permissions_to_first_role(pub):
    pub.load_site_options()
    pub.site_options.set('options', 'give-all-permissions-to-first-role', 'true')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)
    pub.role_class.wipe()

    role = pub.role_class('test')
    role.store()
    assert role.allows_backoffice_access is True
    pub.reload_cfg()
    assert pub.cfg['admin-permissions']
    for k in ['forms', 'cards', 'workflows', 'users', 'roles', 'categories', 'settings', 'journal']:
        assert pub.cfg['admin-permissions'][k] == [role.id]
