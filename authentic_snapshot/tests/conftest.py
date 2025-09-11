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


import contextlib
import inspect
import json
import multiprocessing
import os
import signal
import urllib.parse
import warnings
from unittest import mock

import django_webtest
import pytest
import responses
from django.core.cache import cache
from django.core.management import call_command
from django.db import connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test.utils import override_settings
from django.utils.timezone import now

from authentic2.a2_rbac.models import OrganizationalUnit, Role
from authentic2.a2_rbac.utils import get_default_ou
from authentic2.apps.authenticators.models import LoginPasswordAuthenticator
from authentic2.apps.journal import journal
from authentic2.authentication import OIDCUser
from authentic2.manager.utils import get_ou_count
from authentic2.models import Attribute, Service, Setting
from authentic2.utils import hooks as a2_hooks
from authentic2.utils.evaluate import BaseExpressionValidator
from authentic2.utils.misc import RUNTIME_SETTINGS, get_password_authenticator
from authentic2_auth_fc.models import FcAuthenticator
from authentic2_auth_oidc.utils import get_provider_by_issuer
from authentic2_idp_oidc.models import OIDCClient

from . import utils
from .utils import create_user, get_memcache_config


@pytest.fixture
def settings(settings, request):
    # our post_migrate handlers depends upon some values of the settings (like
    # A2_RBAC_MANAGED_CONTENT_TYPES), making the media fixture "autouse=True"
    # fixed the order of running settings and transactional_db, but
    # transactional_db use the flush() which use the post_migrate handlers to
    # restore a blank database state. To force the ordering of transactional_db
    # and settings fixture we need to override the later and use a dynamic call
    # to the transactional_db fixture when needed.
    if 'transactional_db' in request.fixturenames:
        request.getfixturevalue('transactional_db')
    yield settings
    settings.finalize()


@pytest.fixture(autouse=True)
def ensure_db_content(db):
    # Some stuff are initialized by migrations, if db is flushed
    # this stuff will be missing and needs to be created again.
    # It appends with transactional db fixtures or when tests orders changes.

    # Default LoginPasswordAuthenticator
    auth, dummy = LoginPasswordAuthenticator.objects.get_or_create(
        slug='password-authenticator', defaults={'enabled': True}
    )
    if auth.min_password_strength is not None:
        warnings.warn(
            'Test setup with bad LoginPasswordAuthenticator.min_password_length value %s'
            % auth.min_password_strength
        )
        auth.min_password_strength = None
        auth.save()

    # Runtime settings re-init
    for key, data in RUNTIME_SETTINGS.items():
        if key.startswith('sso:') or key.startswith('users:'):
            setting, dummy = Setting.objects.get_or_create(key=key, defaults={'value': data['value']})
            if setting.value != data['value']:
                setting.value = data['value']
                setting.save()


@pytest.fixture
def app_factory():
    wtm = django_webtest.WebTestMixin()
    wtm._patch_settings()
    try:

        def factory(hostname='testserver'):
            return django_webtest.DjangoTestApp(
                extra_environ={'HTTP_HOST': hostname, 'wsgi.url_scheme': 'https'}
            )

        yield factory
    finally:
        wtm._unpatch_settings()

    journal.journal._pending_records = []  # reinit pending events


@pytest.fixture
def app(app_factory):
    return app_factory()


@pytest.fixture
def ou1(db):
    return OrganizationalUnit.objects.create(name='OU1', slug='ou1')


@pytest.fixture
def ou2(db):
    return OrganizationalUnit.objects.create(name='OU2', slug='ou2')


@pytest.fixture
def service1(db):
    return Service.objects.create(
        ou=get_default_ou(),
        name='Service 1',
        slug='service1',
    )


@pytest.fixture
def service2(db):
    return Service.objects.create(
        ou=get_default_ou(),
        name='Service 2',
        slug='service2',
    )


@pytest.fixture
def ou_rando(db):
    return OrganizationalUnit.objects.create(name='ou_rando', slug='ou_rando')


@pytest.fixture
def simple_user(db, ou1):
    return create_user(
        username='user',
        first_name='Jôhn',
        last_name='Dôe',
        email='user@example.net',
        ou=get_default_ou(),
    )


@pytest.fixture
def nomail_user(db, ou1):
    return create_user(
        username='nomail_user',
        first_name='Jôhn',
        last_name='Dôe',
        ou=get_default_ou(),
        email='',
        email_verified=False,
        password='user',
    )


@pytest.fixture
def sms_service(settings):
    with responses.mock as responses_mock:
        settings.SMS_URL = 'https://foo.whatever.none/'

        class NS:
            mock = responses_mock
            url = 'https://foo.whatever.none/'
            rsp = responses.post(url, status=200)

            @property
            def call_count(self):
                return self.rsp.call_count

            @property
            def last_message(self):
                return json.loads(self.rsp.calls[-1].request.body)['message']

        yield NS()


@pytest.fixture
def phone_activated_authn(db, sms_service):
    phone, dummy = Attribute.objects.get_or_create(
        name='phone',
        kind='phone_number',
        user_editable=True,
        defaults={'label': 'Phone'},
    )
    get_password_authenticator()
    LoginPasswordAuthenticator.objects.update(
        accept_phone_authentication=True,
        phone_identifier_field=phone,
    )
    return LoginPasswordAuthenticator.objects.get()


@pytest.fixture
def phone_user(nomail_user, phone_activated_authn):
    nomail_user.phone = '+33123456789'
    nomail_user.login = '123456789'
    nomail_user.attributes.phone = nomail_user.phone
    nomail_user.phone_verified_on = now()
    nomail_user.save()
    return nomail_user


@pytest.fixture
def superuser(db):
    return create_user(
        username='superuser',
        first_name='super',
        last_name='user',
        email='superuser@example.net',
        is_superuser=True,
        is_staff=True,
        is_active=True,
    )


@pytest.fixture
def admin(db):
    user = create_user(
        username='admin',
        first_name='global',
        last_name='admin',
        email='admin@example.net',
        is_active=True,
        ou=get_default_ou(),
    )
    user.roles.add(Role.objects.get(slug='_a2-manager'))
    return user


@pytest.fixture
def user_ou1(db, ou1):
    return create_user(
        username='john.doe', first_name='Jôhn', last_name='Dôe', email='john.doe@example.net', ou=ou1
    )


@pytest.fixture
def user_ou2(db, ou2):
    return create_user(
        username='john.doe.ou2', first_name='Jôhn', last_name='Dôe', email='john.doe@example.net', ou=ou2
    )


@pytest.fixture
def admin_ou1(db, ou1):
    user = create_user(
        username='admin.ou1', first_name='Admin', last_name='OU1', email='admin.ou1@example.net', ou=ou1
    )
    user.roles.add(ou1.get_admin_role())
    return user


@pytest.fixture
def admin_ou2(db, ou2):
    user = create_user(
        username='admin.ou2', first_name='Admin', last_name='OU2', email='admin.ou2@example.net', ou=ou2
    )
    user.roles.add(ou2.get_admin_role())
    return user


@pytest.fixture
def admin_rando_role(db, role_random, ou_rando):
    user = create_user(
        username='admin_rando',
        first_name='admin',
        last_name='rando',
        email='admin.rando@weird.com',
        ou=ou_rando,
    )
    user.roles.add(ou_rando.get_admin_role())
    return user


@pytest.fixture(
    params=['superuser', 'user_ou1', 'user_ou2', 'admin_ou1', 'admin_ou2', 'admin_rando_role', 'member_rando']
)
def user(request, superuser, user_ou1, user_ou2, admin_ou1, admin_ou2, admin_rando_role, member_rando):
    return locals().get(request.param)


@pytest.fixture
def logged_app(app, user):
    utils.login(app, user)
    return app


@pytest.fixture
def simple_role(db):
    return Role.objects.create(
        name='simple role', slug='simple-role', ou=get_default_ou(), uuid='6115a844a91840f6a83f942c0180f80f'
    )


@pytest.fixture
def role_random(db, ou_rando):
    return Role.objects.create(name='rando', slug='rando', ou=ou_rando)


@pytest.fixture
def role_ou1(db, ou1):
    return Role.objects.create(name='role_ou1', slug='role_ou1', ou=ou1)


@pytest.fixture
def role_ou2(db, ou2):
    return Role.objects.create(name='role_ou2', slug='role_ou2', ou=ou2)


@pytest.fixture(params=['role_random', 'role_ou1', 'role_ou2'])
def role(request, role_random, role_ou1, role_ou2):
    return locals().get(request.param)


@pytest.fixture
def member_rando(db, ou_rando):
    return create_user(
        username='test', first_name='test', last_name='test', email='test@test.org', ou=ou_rando
    )


@pytest.fixture
def member_rando2(db, ou_rando):
    return create_user(
        username='test2', first_name='test2', last_name='test2', email='test2@test.org', ou=ou_rando
    )


@pytest.fixture
def member_fake():
    return type('user', (object,), {'username': 'fake', 'uuid': 'fake_uuid'})


@pytest.fixture(params=['member_rando', 'member_fake'])
def member(request, member_rando, member_fake):
    return locals().get(request.param)


@pytest.fixture(params=['superuser', 'admin'])
def superuser_or_admin(request, superuser, admin):
    return locals().get(request.param)


@pytest.fixture
def concurrency(settings):
    """Select a level of concurrency based on the db. Currently only
    postgresql is supported.
    """
    return 20


@pytest.fixture
def oidc_client(db, ou1):
    client = OIDCClient.objects.create(
        name='example',
        slug='example',
        client_id='example',
        client_secret='example',
        authorization_flow=1,
        post_logout_redirect_uris='https://example.net/redirect/',
        identifier_policy=OIDCClient.POLICY_UUID,
        has_api_access=True,
    )

    class TestOIDCUser(OIDCUser):
        clear_password = 'example'

        @property
        def username(self):
            return self.oidc_client.client_id

        @property
        def id(self):
            return self.oidc_client.id

        @property
        def is_superuser(self):
            return False

        @property
        def roles(self):
            return mock.Mock(exists=lambda: True)

        @property
        def ou(self):
            return ou1

    return TestOIDCUser(client)


@pytest.fixture(
    params=[
        'oidc_client',
        'superuser',
        'user_ou1',
        'user_ou2',
        'admin_ou1',
        'admin_ou2',
        'admin_rando_role',
        'member_rando',
    ]
)
def api_user(
    request, oidc_client, superuser, user_ou1, user_ou2, admin_ou1, admin_ou2, admin_rando_role, member_rando
):
    return locals().get(request.param)


@pytest.fixture(autouse=True)
def clear_cache():
    cache.clear()
    BaseExpressionValidator.__call__.cache_clear()
    for cached_el in (
        OrganizationalUnit.cached,
        a2_hooks.get_hooks,
        get_provider_by_issuer,
        get_ou_count,
    ):
        cached_el.cache.clear()


@pytest.fixture
def memcache_down():
    with override_settings(CACHES=get_memcache_config(2)):
        yield None


@pytest.fixture
def cache_errors():
    # Allows to configure a faulty memcache mock
    # Arguments :
    # - close_after : the memcache server will close the socket after X requests
    # - replies : a dict with command as key and server replies as argument (server will
    #             cycle on each reply for a given command)
    @contextlib.contextmanager
    def _conf_cache(close_after=None, replies=None):
        if close_after:
            if replies is None:
                replies = {
                    'set': ['NOT_STORED', 'STORED'],
                    'incr': ['NOT_FOUND'],
                    'get': ['END'],
                    'delete': ['NOT_FOUND'],
                }
            port_queue = multiprocessing.Queue()
            # Starts fake memcache server
            proc = multiprocessing.Process(
                target=utils.memcache_server, args=[close_after, replies, port_queue]
            )
            proc.start()
            port = port_queue.get()
            port_queue.close()
            # override settings
            with override_settings(CACHES=get_memcache_config(port)):
                yield
            # Stop the server
            os.kill(proc.pid, signal.SIGUSR1)
            proc.join(1)
            if proc.exitcode is None:
                proc.kill()
                proc.join()
        else:
            yield

    return _conf_cache


class AllHook:
    def __init__(self):
        self.calls = {}

    def __call__(self, hook_name, *args, **kwargs):
        calls = self.calls.setdefault(hook_name, [])
        calls.append({'args': args, 'kwargs': kwargs})

    def __getattr__(self, name):
        return self.calls.get(name, [])

    def clear(self):
        self.calls = {}


@pytest.fixture
def hooks(settings):
    if hasattr(settings, 'A2_HOOKS'):
        hooks = settings.A2_HOOKS
    else:
        hooks = settings.A2_HOOKS = {}
    hook = hooks['__all__'] = AllHook()
    yield hook
    hook.clear()
    del settings.A2_HOOKS['__all__']


@pytest.fixture
def auto_admin_role(db, ou1):
    role = Role.objects.create(ou=ou1, slug='auto-admin-role', name='Auto Admin Role')
    role.add_self_administration()
    return role


@pytest.fixture
def user_with_auto_admin_role(auto_admin_role, ou1):
    user = create_user(
        username='user.with.auto.admin.role',
        first_name='User',
        last_name='With Auto Admin Role',
        email='user.with.auto.admin.role@example.net',
        ou=ou1,
    )
    user.roles.add(auto_admin_role)
    return user


# fixtures to check proper validation of redirect_url


@pytest.fixture
def saml_external_redirect(db):
    from authentic2.saml.models import LibertyProvider

    next_url = 'https://saml.example.com/whatever/'
    LibertyProvider.objects.create(
        entity_id='https://saml.example.com/',
        protocol_conformance=3,
        metadata=utils.saml_sp_metadata('https://example.com'),
    )
    return next_url, True


@pytest.fixture
def invalid_external_redirect():
    return 'https://invalid.example.com/whatever/', False


@pytest.fixture
def whitelist_external_redirect(settings):
    settings.A2_REDIRECT_WHITELIST = ['https://whitelist.example.com/']
    return 'https://whitelist.example.com/whatever/', True


@pytest.fixture(params=['saml', 'invalid', 'whitelist'])
def external_redirect(
    request, saml_external_redirect, invalid_external_redirect, whitelist_external_redirect
):
    return locals()[request.param + '_external_redirect']


@pytest.fixture
def external_redirect_next_url(external_redirect):
    return external_redirect[0]


@pytest.fixture
def assert_external_redirect(external_redirect):
    next_url, valid = external_redirect
    if valid:

        def check_location(response, default_return):
            assert next_url.endswith(response['Location'])

    else:

        def check_location(response, default_return):
            assert urllib.parse.urljoin('https://testserver/', default_return).endswith(response['Location'])

    return check_location


@pytest.fixture
def french_translation():
    from django.utils.translation import activate, deactivate

    activate('fr')
    yield
    deactivate()


@pytest.fixture(autouse=True)
def media(settings, tmpdir):
    settings.MEDIA_ROOT = str(tmpdir.mkdir('media'))


@pytest.fixture
def service(db):
    return Service.objects.create(ou=get_default_ou(), slug='service', name='Service')


@pytest.fixture()
def migration(request, transactional_db):
    # see https://gist.github.com/asfaltboy/b3e6f9b5d95af8ba2cc46f2ba6eae5e2
    """
    This fixture returns a helper object to test Django data migrations.
    The fixture returns an object with two methods;
     - `before` to initialize db to the state before the migration under test
     - `after` to execute the migration and bring db to the state after the migration
    The methods return `old_apps` and `new_apps` respectively; these can
    be used to initiate the ORM models as in the migrations themselves.
    For example:
        def test_foo_set_to_bar(migration):
            old_apps = migration.before([('my_app', '0001_inital')])
            Foo = old_apps.get_model('my_app', 'foo')
            Foo.objects.create(bar=False)
            assert Foo.objects.count() == 1
            assert Foo.objects.filter(bar=False).count() == Foo.objects.count()
            # executing migration
            new_apps = migration.apply([('my_app', '0002_set_foo_bar')])
            Foo = new_apps.get_model('my_app', 'foo')

            assert Foo.objects.filter(bar=False).count() == 0
            assert Foo.objects.filter(bar=True).count() == Foo.objects.count()
    Based on: https://gist.github.com/blueyed/4fb0a807104551f103e6
    """

    class Migrator:
        def before(self, targets, at_end=True):
            """Specify app and starting migration names as in:
            before([('app', '0001_before')]) => app/migrations/0001_before.py
            """
            executor = MigrationExecutor(connection)
            executor.migrate(targets)
            executor.loader.build_graph()
            return executor._create_project_state(with_applied_migrations=True).apps

        def apply(self, targets):
            """Migrate forwards to the "targets" migration"""
            executor = MigrationExecutor(connection)
            executor.migrate(targets)
            executor.loader.build_graph()
            return executor._create_project_state(with_applied_migrations=True).apps

    yield Migrator()

    call_command('migrate', verbosity=0)


@pytest.fixture
def cgu_attribute(db):
    return Attribute.objects.create(
        name='cgu_2021',
        label='J\'accepte les conditions générales d\'utilisation',
        kind='boolean',
        required_on_login=True,
        user_visible=True,
    )


@pytest.fixture(scope='session')
def scoped_db(django_db_setup, django_db_blocker):
    '''Scoped fixture, use like that to load some models for session/module/class scope:

    @pytest.fixture(scope='module')
    def myfixture(scoped_db):
        @scoped_db
        def f():
             return Model.objects.create(x=1)
        yield from f()
    '''

    @contextlib.contextmanager
    def scoped_db(func):
        with django_db_blocker.unblock():
            with transaction.atomic():
                try:
                    if inspect.isgeneratorfunction(func):
                        generator = func()
                        value = next(generator)
                    else:
                        value = func()
                    with django_db_blocker.block():
                        yield value
                    if inspect.isgeneratorfunction(func):
                        try:
                            next(generator)
                        except StopIteration:
                            pass
                        else:
                            raise RuntimeError(f'{func} yielded more than one time')
                finally:
                    transaction.set_rollback(True)

    return scoped_db


@pytest.fixture
def fc(db):
    FcAuthenticator.objects.create(
        enabled=True,
        client_id='xxx',
        client_secret='yyy',
        platform='test',
        scopes=['profile', 'email'],
    )


@pytest.fixture
def nologtoconsole(monkeypatch):
    monkeypatch.setattr('authentic2.base_commands.log_to_console', lambda *args: contextlib.nullcontext())


@pytest.fixture
def nocache(settings):
    settings.CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.dummy.DummyCache',
        }
    }


@pytest.fixture
def saml_metadata_url():
    metadata_url = 'https://example.com/metadata.xml'
    with open(os.path.join(os.path.dirname(__file__), 'metadata.xml')) as metadata:
        metadata_content = metadata.read()
    responses.get(metadata_url, body=metadata_content, status=200)
    yield metadata_url


@pytest.fixture(scope='session', autouse=True)
def faker_session_locale():
    return ['fr_FR']
