import copy
import json
import os
import random
import shutil
import tempfile
import zipfile

import psycopg2
import pytest
from django.core.management import call_command
from quixote import cleanup

from wcs.compat import CompatWcsPublisher
from wcs.ctl.management.commands.hobo_deploy import Command as HoboDeployCommand
from wcs.sql import cleanup_connection

from .utilities import clean_temporary_pub, create_temporary_pub

CONFIG = {
    'postgresql': {
        'createdb-connection-params': {'database': 'postgres', 'user': os.environ['USER']},
        'database-template-name': '%s',
        'user': os.environ['USER'],
    }
}

WCS_BASE_TENANT = 'wcsteststenant%d' % random.randint(0, 100000)
WCS_TENANT = '%s.net' % WCS_BASE_TENANT
WCS_DB_NAME = '%s_net' % WCS_BASE_TENANT

NEW_WCS_BASE_TENANT = 'wcsteststenant%d' % random.randint(0, 100000)
NEW_WCS_TENANT = '%s.net' % NEW_WCS_BASE_TENANT
NEW_WCS_DB_NAME = '%s_net' % NEW_WCS_BASE_TENANT


HOBO_JSON = {
    'services': [
        {
            'title': 'Hobo',
            'slug': 'hobo',
            'service-id': 'hobo',
            'base_url': 'http://hobo.example.net/',
            'saml-sp-metadata-url': 'http://hobo.example.net/accounts/mellon/metadata/',
        },
        {
            'service-id': 'authentic',
            'saml-idp-metadata-url': 'http://authentic.example.net/idp/saml2/metadata',
            'template_name': '',
            'variables': {},
            'title': 'Authentic',
            'base_url': 'http://authentic.example.net/',
            'id': 3,
            'slug': 'authentic',
            'secret_key': '12345',
        },
        {
            'service-id': 'wcs',
            'template_name': 'publik.zip',
            'variables': {'xxx': 'HELLO WORLD'},
            'title': 'Test wcs',
            'saml-sp-metadata-url': 'http://%s/saml/metadata' % WCS_TENANT,
            'base_url': 'http://%s/' % WCS_TENANT,
            'backoffice-menu-url': 'http://%s/backoffice/menu.json' % WCS_TENANT,
            'id': 1,
            'secret_key': 'eiue7aa10nt6e9*#jg2bsfvdgl)cr%4(tafibfjx9i$pgnfj#v',
            'slug': 'test-wcs',
        },
        {
            'service-id': 'combo',
            'template_name': 'portal-agent',
            'title': 'Portal Agents',
            'base_url': 'http://agents.example.net/',
            'secret_key': 'aaa',
        },
        {
            'service-id': 'combo',
            'template_name': 'portal-user',
            'title': 'Portal',
            'base_url': 'http://portal.example.net/',
            'secret_key': 'bbb',
        },
    ],
    'timestamp': '1431420355.31',
}


@pytest.fixture
def setuptest():
    cleanup_connection()
    createdb_cfg = CONFIG['postgresql'].get('createdb-connection-params')
    conn = psycopg2.connect(**createdb_cfg)
    conn.set_isolation_level(psycopg2.extensions.ISOLATION_LEVEL_AUTOCOMMIT)
    cursor = conn.cursor()
    for dbname in (WCS_DB_NAME, NEW_WCS_DB_NAME):
        cursor.execute('DROP DATABASE IF EXISTS %s' % dbname)

    pub = create_temporary_pub()
    pub.cfg['language'] = {'language': 'en'}
    hobo_cmd = HoboDeployCommand()
    hobo_cmd.all_services = HOBO_JSON
    CompatWcsPublisher.APP_DIR = tempfile.mkdtemp()

    skeleton_dir = os.path.join(CompatWcsPublisher.APP_DIR, 'skeletons')
    os.mkdir(skeleton_dir)
    with open(os.path.join(skeleton_dir, 'publik.zip'), 'wb') as f:
        with zipfile.ZipFile(f, 'w') as z:
            z.writestr('config.json', json.dumps(CONFIG))
            z.writestr('site-options.cfg', '[options]\npostgresql = true')

    yield pub, hobo_cmd

    clean_temporary_pub()
    if os.path.exists(CompatWcsPublisher.APP_DIR):
        shutil.rmtree(CompatWcsPublisher.APP_DIR)
    cleanup_connection()
    for dbname in (WCS_DB_NAME, NEW_WCS_DB_NAME):
        cursor.execute('DROP DATABASE IF EXISTS %s' % dbname)
    conn.close()


def database_exists(database):
    res = False
    cleanup_connection()
    createdb_cfg = CONFIG['postgresql'].get('createdb-connection-params')
    conn = psycopg2.connect(**createdb_cfg)
    conn.set_isolation_level(psycopg2.extensions.ISOLATION_LEVEL_AUTOCOMMIT)
    cursor = conn.cursor()
    cursor.execute("SELECT 1 AS result FROM pg_database WHERE datname='%s'" % database)
    if cursor.fetchall():
        res = True
    conn.close()
    return res


def test_deploy(setuptest):
    assert not os.path.exists(os.path.join(CompatWcsPublisher.APP_DIR, 'tenants', WCS_TENANT))
    assert not database_exists(WCS_DB_NAME)
    assert not os.path.exists(os.path.join(CompatWcsPublisher.APP_DIR, 'tenants', NEW_WCS_TENANT))
    assert not database_exists(NEW_WCS_DB_NAME)

    cleanup()
    with open(os.path.join(CompatWcsPublisher.APP_DIR, 'hobo.json'), 'w') as fd:
        fd.write(json.dumps(HOBO_JSON))
    call_command(
        'hobo_deploy',
        '--ignore-timestamp',
        'http://%s/' % WCS_TENANT,
        os.path.join(CompatWcsPublisher.APP_DIR, 'hobo.json'),
    )
    assert os.path.exists(os.path.join(CompatWcsPublisher.APP_DIR, 'tenants', WCS_TENANT))
    assert database_exists(WCS_DB_NAME)

    # deploy a new tenant
    with open(os.path.join(CompatWcsPublisher.APP_DIR, 'hobo.json'), 'w') as fd:
        hobo_json = copy.deepcopy(HOBO_JSON)
        wcs_service = hobo_json['services'][2]
        wcs_service['saml-sp-metadata-url'] = ('http://%s/saml/metadata' % WCS_TENANT,)
        wcs_service['base_url'] = 'http://%s/' % NEW_WCS_TENANT
        wcs_service['backoffice-menu-url'] = 'http://%s/backoffice/menu.json' % NEW_WCS_TENANT
        fd.write(json.dumps(hobo_json))

    call_command(
        'hobo_deploy',
        '--ignore-timestamp',
        'http://%s/' % NEW_WCS_TENANT,
        os.path.join(CompatWcsPublisher.APP_DIR, 'hobo.json'),
    )
    assert os.path.exists(os.path.join(CompatWcsPublisher.APP_DIR, 'tenants', WCS_TENANT))
    assert database_exists(WCS_DB_NAME)
    assert os.path.exists(os.path.join(CompatWcsPublisher.APP_DIR, 'tenants', NEW_WCS_TENANT))
    assert database_exists(NEW_WCS_DB_NAME)


def test_deploy_url_change(setuptest):
    assert not os.path.exists(os.path.join(CompatWcsPublisher.APP_DIR, 'tenants', WCS_TENANT))
    assert not database_exists(WCS_DB_NAME)
    assert not os.path.exists(os.path.join(CompatWcsPublisher.APP_DIR, 'tenants', NEW_WCS_TENANT))
    assert not database_exists(NEW_WCS_DB_NAME)

    cleanup()
    with open(os.path.join(CompatWcsPublisher.APP_DIR, 'hobo.json'), 'w') as fd:
        fd.write(json.dumps(HOBO_JSON))
    call_command(
        'hobo_deploy',
        '--ignore-timestamp',
        'http://%s/' % WCS_TENANT,
        os.path.join(CompatWcsPublisher.APP_DIR, 'hobo.json'),
    )
    assert os.path.exists(os.path.join(CompatWcsPublisher.APP_DIR, 'tenants', WCS_TENANT))
    assert database_exists(WCS_DB_NAME)

    # domain change request
    with open(os.path.join(CompatWcsPublisher.APP_DIR, 'hobo.json'), 'w') as fd:
        hobo_json = copy.deepcopy(HOBO_JSON)
        wcs_service = hobo_json['services'][2]
        wcs_service['legacy_urls'] = [
            {
                'saml-sp-metadata-url': wcs_service['saml-sp-metadata-url'],
                'base_url': wcs_service['base_url'],
                'backoffice-menu-url': wcs_service['backoffice-menu-url'],
            }
        ]

        wcs_service['saml-sp-metadata-url'] = ('http://%s/saml/metadata' % WCS_TENANT,)
        wcs_service['base_url'] = 'http://%s/' % NEW_WCS_TENANT
        wcs_service['backoffice-menu-url'] = 'http://%s/backoffice/menu.json' % NEW_WCS_TENANT
        fd.write(json.dumps(hobo_json))

    call_command(
        'hobo_deploy',
        '--ignore-timestamp',
        'http://%s/' % NEW_WCS_TENANT,
        os.path.join(CompatWcsPublisher.APP_DIR, 'hobo.json'),
    )
    assert not os.path.exists(os.path.join(CompatWcsPublisher.APP_DIR, 'tenants', WCS_TENANT))
    assert database_exists(WCS_DB_NAME)
    assert os.path.exists(os.path.join(CompatWcsPublisher.APP_DIR, 'tenants', NEW_WCS_TENANT))
    assert not database_exists(NEW_WCS_DB_NAME)

    publisher = CompatWcsPublisher.create_publisher()
    publisher.set_tenant_by_hostname(NEW_WCS_TENANT)
    # check that WCS_DB_NAME is used by NEW_WCS_TENANT
    assert publisher.cfg['postgresql']['database'] == WCS_DB_NAME
    # check that sp configuration is updated
    assert publisher.cfg['sp']['saml2_providerid'] == 'http://%s/saml/metadata' % NEW_WCS_TENANT


def test_deploy_url_change_old_tenant_dir(setuptest):
    assert not os.path.exists(os.path.join(CompatWcsPublisher.APP_DIR, 'tenants', WCS_TENANT))
    assert not database_exists(WCS_DB_NAME)
    assert not os.path.exists(os.path.join(CompatWcsPublisher.APP_DIR, 'tenants', NEW_WCS_TENANT))
    assert not database_exists(NEW_WCS_DB_NAME)

    cleanup()
    with open(os.path.join(CompatWcsPublisher.APP_DIR, 'hobo.json'), 'w') as fd:
        fd.write(json.dumps(HOBO_JSON))
    call_command(
        'hobo_deploy',
        '--ignore-timestamp',
        'http://%s/' % WCS_TENANT,
        os.path.join(CompatWcsPublisher.APP_DIR, 'hobo.json'),
    )
    assert os.path.exists(os.path.join(CompatWcsPublisher.APP_DIR, 'tenants', WCS_TENANT))
    assert database_exists(WCS_DB_NAME)
    # move tenant to APP_DIR (legacy way)
    os.replace(
        os.path.join(CompatWcsPublisher.APP_DIR, 'tenants', WCS_TENANT),
        os.path.join(CompatWcsPublisher.APP_DIR, WCS_TENANT),
    )
    assert not os.path.exists(os.path.join(CompatWcsPublisher.APP_DIR, 'tenants', WCS_TENANT))

    # domain change request
    with open(os.path.join(CompatWcsPublisher.APP_DIR, 'hobo.json'), 'w') as fd:
        hobo_json = copy.deepcopy(HOBO_JSON)
        wcs_service = hobo_json['services'][2]
        wcs_service['legacy_urls'] = [
            {
                'saml-sp-metadata-url': wcs_service['saml-sp-metadata-url'],
                'base_url': wcs_service['base_url'],
                'backoffice-menu-url': wcs_service['backoffice-menu-url'],
            }
        ]

        wcs_service['saml-sp-metadata-url'] = ('http://%s/saml/metadata' % WCS_TENANT,)
        wcs_service['base_url'] = 'http://%s/' % NEW_WCS_TENANT
        wcs_service['backoffice-menu-url'] = 'http://%s/backoffice/menu.json' % NEW_WCS_TENANT
        fd.write(json.dumps(hobo_json))

    call_command(
        'hobo_deploy',
        '--ignore-timestamp',
        'http://%s/' % NEW_WCS_TENANT,
        os.path.join(CompatWcsPublisher.APP_DIR, 'hobo.json'),
    )
    assert not os.path.exists(os.path.join(CompatWcsPublisher.APP_DIR, WCS_TENANT))
    assert database_exists(WCS_DB_NAME)
    assert os.path.exists(os.path.join(CompatWcsPublisher.APP_DIR, 'tenants', NEW_WCS_TENANT))
    assert not database_exists(NEW_WCS_DB_NAME)

    publisher = CompatWcsPublisher.create_publisher()
    publisher.set_tenant_by_hostname(NEW_WCS_TENANT)
    # check that WCS_DB_NAME is used by NEW_WCS_TENANT
    assert publisher.cfg['postgresql']['database'] == WCS_DB_NAME
    # check that sp configuration is updated
    assert publisher.cfg['sp']['saml2_providerid'] == 'http://%s/saml/metadata' % NEW_WCS_TENANT
