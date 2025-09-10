import configparser
import os

import pytest

from wcs.forms.root import FormPage
from wcs.qommon.ident.password_accounts import PasswordAccount

from .utilities import EmailsMocking, HttpRequestsMocking, SMSMocking

FormPage.AUTOSAVE_TIMEOUT = 2


def site_options(request, pub, section, variable, value):
    config = configparser.ConfigParser()
    path = os.path.join(pub.app_dir, 'site-options.cfg')
    if os.path.exists(path):
        config.read([path])
    if not config.has_section(section):
        config.add_section(section)
    config.set(section, variable, value)
    with open(path, 'w') as site_option:
        config.write(site_option)

    def fin():
        config = configparser.ConfigParser()
        if os.path.exists(path):
            config.read([path])
            config.remove_option(section, variable)
            with open(path, 'w') as site_option:
                config.write(site_option)

    request.addfinalizer(fin)
    return value


@pytest.fixture
def chrono_url(request, pub):
    return site_options(request, pub, 'options', 'chrono_url', 'http://chrono.example.net/')


@pytest.fixture
def fargo_url(request, pub):
    return site_options(request, pub, 'options', 'fargo_url', 'http://fargo.example.net/')


@pytest.fixture
def fargo_secret(request, pub):
    return site_options(request, pub, 'wscall-secrets', 'fargo.example.net', 'xxx')


@pytest.fixture
def emails():
    with EmailsMocking() as mock:
        yield mock


@pytest.fixture
def sms_mocking():
    with SMSMocking() as sms:
        yield sms


@pytest.fixture
def http_requests():
    with HttpRequestsMocking() as http_requests:
        yield http_requests


@pytest.fixture
def nocache(settings):
    settings.CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.dummy.DummyCache',
        }
    }


@pytest.fixture
def sql_queries(monkeypatch):
    import wcs.sql

    queries = []
    wcs.sql.cleanup_connection()
    wcs.sql.LoggingCursor.queries = queries
    yield queries
    wcs.sql.cleanup_connection()


@pytest.fixture
def backoffice_role(pub):
    role = pub.role_class.get_on_index('backoffice-role', 'slug', ignore_errors=True)
    if not role:
        role = pub.role_class(name='backoffice role')
        role.allows_backoffice_access = True
        role.store()
        assert role.slug == 'backoffice-role'
    return role


@pytest.fixture
def backoffice_user(pub, backoffice_role):
    try:
        user = pub.user_class.get_users_with_email('backoffice-user@example.net')[0]
    except IndexError:
        user = pub.user_class()
        user.name = 'backoffice user'
        user.email = 'backoffice-user@example.net'
        user.roles = [backoffice_role.id]
        user.store()

    account1 = PasswordAccount(id='backoffice-user')
    account1.set_password('backoffice-user')
    account1.user_id = user.id
    account1.store()

    return user
