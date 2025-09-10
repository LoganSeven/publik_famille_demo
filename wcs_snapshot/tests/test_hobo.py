import copy
import json
import os
import pickle
import random
import shutil
import tempfile
import urllib.parse
import zipfile
from unittest import mock

import psycopg2
import pytest
from django.core.management import call_command
from quixote import cleanup

from wcs import fields, sql
from wcs.compat import CompatWcsPublisher
from wcs.ctl.management.commands.hobo_deploy import Command as HoboDeployCommand
from wcs.qommon import force_str
from wcs.sql import cleanup_connection

from .utilities import clean_temporary_pub, create_temporary_pub

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
            'template_name': 'export-test.wcs',
            'variables': {'xxx': 'HELLO WORLD'},
            'title': 'Test wcs',
            'saml-sp-metadata-url': 'http://wcs.example.net/saml/metadata',
            'base_url': 'http://wcs.example.net/',
            'backoffice-menu-url': 'http://wcs.example.net/backoffice/menu.json',
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
            'legacy_urls': [
                {
                    'base_url': 'http://oldportal.example.net/',
                },
                {
                    'base_url': 'http://veryoldportal.example.net/',
                },
            ],
        },
        {
            'service-id': 'lingo',
            'title': 'Lingo',
            'base_url': 'http://payment.example.net/',
            'secret_key': 'aaa',
        },
        {
            'service-id': 'combo',
            'title': 'External',
            'slug': '_interco_portal',
            'base_url': 'http://extportal.example.net/',
            'secret_key': 'ext',
        },
    ],
    'profile': {
        'fields': [
            {
                'kind': 'title',
                'description': '',
                'required': False,
                'user_visible': True,
                'label': 'Civilité',
                'disabled': False,
                'user_editable': True,
                'asked_on_registration': False,
                'name': 'title',
            },
            {
                'kind': 'string',
                'description': '',
                'required': True,
                'user_visible': True,
                'label': 'Prénom',
                'disabled': False,
                'user_editable': True,
                'asked_on_registration': True,
                'name': 'first_name',
            },
            {
                'kind': 'string',
                'description': '',
                'required': True,
                'user_visible': True,
                'label': 'Nom',
                'disabled': False,
                'user_editable': True,
                'asked_on_registration': True,
                'name': 'last_name',
            },
            {
                'kind': 'email',
                'description': '',
                'required': True,
                'user_visible': True,
                'label': 'Adresse électronique',
                'disabled': False,
                'user_editable': True,
                'asked_on_registration': False,
                'name': 'email',
            },
            {
                'kind': 'string',
                'description': '',
                'required': False,
                'user_visible': True,
                'label': 'Addresse',
                'disabled': False,
                'user_editable': True,
                'asked_on_registration': False,
                'name': 'address',
            },
            {
                'kind': 'string',
                'description': '',
                'required': False,
                'user_visible': True,
                'label': 'Code postal',
                'disabled': False,
                'user_editable': True,
                'asked_on_registration': False,
                'name': 'zipcode',
            },
            {
                'kind': 'string',
                'description': '',
                'required': False,
                'user_visible': True,
                'label': 'Commune',
                'disabled': False,
                'user_editable': True,
                'asked_on_registration': False,
                'name': 'city',
            },
            {
                'kind': 'phone_number',
                'description': '',
                'required': False,
                'user_visible': True,
                'label': 'Téléphone',
                'disabled': False,
                'user_editable': True,
                'asked_on_registration': False,
                'name': 'phone',
            },
            {
                'kind': 'phone_number',
                'description': '',
                'required': False,
                'user_visible': True,
                'label': 'Mobile',
                'disabled': False,
                'user_editable': True,
                'asked_on_registration': False,
                'name': 'mobile',
            },
            {
                'kind': 'string',
                'description': '',
                'required': False,
                'user_visible': True,
                'label': 'Pays',
                'disabled': True,
                'user_editable': True,
                'asked_on_registration': False,
                'name': 'country',
            },
            {
                'kind': 'string',
                'description': '',
                'required': False,
                'user_visible': True,
                'label': 'Date de naissance',
                'disabled': True,
                'user_editable': True,
                'asked_on_registration': False,
                'name': 'birthdate',
            },
        ]
    },
    'variables': {
        'foobar': 'http://example.net',
        'email_signature': 'Hello world.',
        'default_from_email': 'noreply@example.net',
        'theme': 'clapotis-les-canards',
        'sms_url': 'http://passerelle.example.net',
        'sms_sender': 'EO',
        'some_dict': {'a': 1, 'b': 2},
        'SETTING_TENANT_DISABLE_CRON_JOBS': True,
        'SETTING_MAINTENANCE_PAGE': True,
        'SETTING_MAINTENANCE_PAGE_MESSAGE': 'foo',
        'SETTING_MAINTENANCE_PASS_THROUGH_HEADER': 'X-bar',
        'SETTING_WHATEVER': 'blah',
        'ou-label': 'Ma Ville',
        'ou-slug': 'ma-ville',
    },
    'users': [
        {
            'username': 'admin',
            'first_name': 'AdminFoo',
            'last_name': 'AdminBar',
            'password': 'pbkdf2_sha256$15000$aXR4knesTiJJ$hubahjFVa4q9C5RTqY5ajSOcrCPc+RZM+Usf1CGYLmA=',
            'email': 'fpeters@entrouvert.com',
        }
    ],
    'timestamp': '1431420355.31',
}


@pytest.fixture
def setuptest():
    hobo_cmd = HoboDeployCommand()
    hobo_cmd.all_services = HOBO_JSON
    CompatWcsPublisher.APP_DIR = tempfile.mkdtemp()
    pub = create_temporary_pub()
    pub.set_tenant_by_hostname('example.net')
    pub.cfg['language'] = {'language': 'en'}

    yield pub, hobo_cmd
    cleanup_connection()
    clean_temporary_pub()
    if os.path.exists(CompatWcsPublisher.APP_DIR):
        shutil.rmtree(CompatWcsPublisher.APP_DIR)


@pytest.fixture
def alt_tempdir():
    alt_tempdir = tempfile.mkdtemp()
    yield alt_tempdir
    shutil.rmtree(alt_tempdir)


@pytest.fixture
def deploy_setup(alt_tempdir):
    CompatWcsPublisher.APP_DIR = alt_tempdir
    with open(os.path.join(alt_tempdir, 'hobo.json'), 'w') as fd:
        hobo_json = copy.deepcopy(HOBO_JSON)
        hobo_json['services'][2]['base_url'] = 'http://wcs.example.net/'  # reset
        del hobo_json['services'][1]  # authentic
        fd.write(json.dumps(hobo_json))
    skeleton_dir = os.path.join(CompatWcsPublisher.APP_DIR, 'skeletons')
    os.mkdir(skeleton_dir)
    db_template_name = 'wcstests_hobo_%d_%%s' % random.randint(0, 100000)
    with open(os.path.join(skeleton_dir, 'export-test.wcs'), 'wb') as f:
        with zipfile.ZipFile(f, 'w') as z:
            CONFIG = {
                'postgresql': {
                    'createdb-connection-params': {'database': 'postgres', 'user': os.environ['USER']},
                    'database-template-name': db_template_name,
                    'user': os.environ['USER'],
                }
            }
            z.writestr('config.json', json.dumps(CONFIG))
    yield True
    shutil.rmtree(skeleton_dir)

    conn = psycopg2.connect(user=os.environ['USER'], dbname='postgres')
    conn.set_isolation_level(psycopg2.extensions.ISOLATION_LEVEL_AUTOCOMMIT)
    cur = conn.cursor()
    cur.execute('DROP DATABASE IF EXISTS %s' % db_template_name % 'wcs_example_net')
    cur.execute('DROP DATABASE IF EXISTS %s' % db_template_name % 'wcs2_example_net')
    cur.close()
    conn.commit()


@pytest.fixture
def deploy_setup_skeleton_dir(alt_tempdir):
    CompatWcsPublisher.APP_DIR = alt_tempdir
    with open(os.path.join(alt_tempdir, 'hobo.json'), 'w') as fd:
        hobo_json = copy.deepcopy(HOBO_JSON)
        hobo_json['services'][2]['base_url'] = 'http://wcs.example.net/'  # reset
        hobo_json['services'][2]['template_name'] = 'export-test'
        del hobo_json['services'][1]  # authentic
        fd.write(json.dumps(hobo_json))
    skeletons_dir = os.path.join(CompatWcsPublisher.APP_DIR, 'skeletons')
    os.mkdir(skeletons_dir)
    db_template_name = 'wcstests_hobo_%d_%%s' % random.randint(0, 100000)
    skeleton_dir = os.path.join(skeletons_dir, 'export-test')
    os.mkdir(skeleton_dir)
    CONFIG = {
        'postgresql': {
            'createdb-connection-params': {'database': 'postgres', 'user': os.environ['USER']},
            'database-template-name': db_template_name,
            'user': os.environ['USER'],
        }
    }
    with open(os.path.join(skeleton_dir, 'config.json'), 'w') as fd:
        json.dump(CONFIG, fd)
    yield True
    shutil.rmtree(skeletons_dir)

    conn = psycopg2.connect(user=os.environ['USER'], dbname='postgres')
    conn.set_isolation_level(psycopg2.extensions.ISOLATION_LEVEL_AUTOCOMMIT)
    cur = conn.cursor()
    cur.execute('DROP DATABASE IF EXISTS %s' % db_template_name % 'wcs_example_net')
    cur.execute('DROP DATABASE IF EXISTS %s' % db_template_name % 'wcs2_example_net')
    cur.close()
    conn.commit()


def test_configure_site_options(setuptest, alt_tempdir):
    pub, hobo_cmd = setuptest
    service = [x for x in HOBO_JSON.get('services', []) if x.get('service-id') == 'wcs'][0]
    hobo_cmd.configure_site_options(service, pub)
    pub.load_site_options()
    assert pub.get_site_option('hobo_url', 'variables') == 'http://hobo.example.net/'
    assert pub.get_site_option('foobar', 'variables') == 'http://example.net'
    assert pub.get_site_option('xxx', 'variables') == 'HELLO WORLD'
    assert pub.get_site_option('portal_agent_url', 'variables') == 'http://agents.example.net/'
    assert pub.get_site_option('portal_url', 'variables') == 'http://portal.example.net/'
    assert pub.get_site_option('lingo_url', 'variables') == 'http://payment.example.net/'
    assert pub.get_site_option('test_wcs_url', 'variables') == 'http://wcs.example.net/'
    assert pub.get_site_option('some_dict', 'variables') == str({'a': 1, 'b': 2})
    assert pub.get_site_option('some_dict__json', 'variables') == json.dumps({'a': 1, 'b': 2})
    assert pub.get_site_option('disable_cron_jobs', 'variables') == 'True'
    assert pub.get_site_option('maintenance_page', 'variables') == 'True'
    assert pub.get_site_option('maintenance_page_message', 'variables') == 'foo'
    assert pub.get_site_option('maintenance_pass_through_header', 'variables') == 'X-bar'
    assert not pub.get_site_option('SETTING_WHATEVER', 'variables')

    key = '109fca71e7dc8ec49708a08fa7c02795de13f34f7d29d27bd150f203b3e0ab40'
    assert pub.get_site_option('authentic.example.net', 'api-secrets') == key
    assert pub.get_site_option('authentic.example.net', 'wscall-secrets') == key
    self_domain = urllib.parse.urlsplit(service.get('base_url')).netloc
    assert pub.get_site_option(self_domain, 'wscall-secrets') != '0'
    assert pub.get_site_option('oldportal.example.net', 'legacy-urls') == 'portal.example.net'
    assert pub.get_site_option('veryoldportal.example.net', 'legacy-urls') == 'portal.example.net'

    service['variables']['xxx'] = None
    hobo_cmd.configure_site_options(service, pub, ignore_timestamp=True)
    pub.load_site_options()
    assert pub.get_site_option('xxx', 'variables') is None

    # check phone region code
    service['variables']['local_country_code'] = '32'
    hobo_cmd.configure_site_options(service, pub, ignore_timestamp=True)
    pub.load_site_options()
    assert pub.get_site_option('local-region-code') == 'BE'

    service['variables']['local_country_code'] = '33'
    hobo_cmd.configure_site_options(service, pub, ignore_timestamp=True)
    pub.load_site_options()
    assert pub.get_site_option('local-region-code') == 'FR'

    # check idp_registration_url
    assert (
        pub.get_site_option('idp_registration_url', 'variables') == 'http://authentic.example.net/register/'
    )

    # check variable name normalization
    assert pub.get_site_option('_interco_portal_url', 'variables') == 'http://extportal.example.net/'
    assert pub.get_site_option('interco_portal_url', 'variables') == 'http://extportal.example.net/'
    assert pub.get_site_option('ou-label', 'variables') == 'Ma Ville'
    assert pub.get_site_option('ou_label', 'variables') == 'Ma Ville'
    assert pub.get_site_option('ou-slug', 'variables') == 'ma-ville'
    assert pub.get_site_option('ou_slug', 'variables') == 'ma-ville'
    service['variables']['ou-label'] = None
    service['variables']['ou-slug'] = None
    hobo_cmd.configure_site_options(service, pub, ignore_timestamp=True)
    pub.load_site_options()
    assert pub.get_site_option('ou-label', 'variables') is None
    assert pub.get_site_option('ou_label', 'variables') is None
    assert pub.get_site_option('ou-slug', 'variables') is None
    assert pub.get_site_option('ou_slug', 'variables') is None
    service['variables']['ou_label'] = 'Ma Ville'
    service['variables']['ou_slug'] = 'ma-ville'
    hobo_cmd.configure_site_options(service, pub, ignore_timestamp=True)
    pub.load_site_options()
    assert pub.get_site_option('ou_label', 'variables') == 'Ma Ville'
    assert pub.get_site_option('ou_slug', 'variables') == 'ma-ville'
    assert pub.get_site_option('ou-label', 'variables') is None
    assert pub.get_site_option('ou-slug', 'variables') is None


def test_update_configuration(setuptest, settings):
    settings.THEMES_DIRECTORY = os.path.join(os.path.dirname(__file__), 'themes')
    pub, hobo_cmd = setuptest
    service = [x for x in HOBO_JSON.get('services', []) if x.get('service-id') == 'wcs'][0]
    hobo_cmd.update_configuration(service, pub)
    assert pub.cfg['misc']['sitename'] == 'Test wcs'
    assert pub.cfg['emails']['footer'] == 'Hello world.'
    assert pub.cfg['emails']['from'] == 'noreply@example.net'
    assert pub.cfg['sms']['passerelle_url'] == 'http://passerelle.example.net'
    assert pub.cfg['sms']['mode'] == 'passerelle'
    assert pub.cfg['sms']['sender'] == 'EO'


def test_update_themes(setuptest, settings):
    settings.THEMES_DIRECTORY = ''
    pub, hobo_cmd = setuptest
    pub.cfg['branding'] = {'theme': 'django'}
    service = [x for x in HOBO_JSON.get('services', []) if x.get('service-id') == 'wcs'][0]
    hobo_cmd.update_configuration(service, pub)
    assert pub.cfg['branding']['theme'] == 'django'

    service['variables']['theme'] = 'foobar'
    hobo_cmd.update_configuration(service, pub)
    assert pub.cfg['branding']['theme'] == 'django'

    settings.THEMES_DIRECTORY = os.path.join(os.path.dirname(__file__), 'themes')
    hobo_cmd.update_configuration(service, pub)
    assert pub.cfg['branding']['theme'] == 'publik-base'
    assert os.readlink(os.path.join(pub.app_dir, 'static')) == os.path.join(
        settings.THEMES_DIRECTORY, 'foobar/static'
    )
    assert os.readlink(os.path.join(pub.app_dir, 'templates')) == os.path.join(
        settings.THEMES_DIRECTORY, 'foobar/templates'
    )
    assert os.readlink(os.path.join(pub.app_dir, 'theme')) == os.path.join(
        settings.THEMES_DIRECTORY, 'publik-base'
    )

    service['variables']['theme'] = 'foobar2'
    hobo_cmd.update_configuration(service, pub)
    assert not os.path.lexists(os.path.join(pub.app_dir, 'static'))
    assert not os.path.lexists(os.path.join(pub.app_dir, 'templates'))
    assert os.readlink(os.path.join(pub.app_dir, 'theme')) == os.path.join(
        settings.THEMES_DIRECTORY, 'foobar'
    )


def test_update_profile(setuptest):
    pub, hobo_cmd = setuptest
    profile = HOBO_JSON.get('profile')

    # load in an empty site
    hobo_cmd.update_profile(profile, pub)
    from wcs.admin.settings import UserFieldsFormDef

    formdef = UserFieldsFormDef(pub)
    field_labels = [force_str(x.get('label')) for x in profile.get('fields') if not x.get('disabled')]
    assert [x.label for x in formdef.fields] == field_labels
    assert pub.cfg['users']['field_email'] in [x.id for x in formdef.fields]
    assert pub.cfg['users']['fullname_template']

    assert formdef.fields[0].id == '_title'
    assert formdef.fields[1].id == '_first_name'
    assert formdef.fields[2].id == '_last_name'

    # check phone numbers has their validation set
    assert formdef.fields[7].id == '_phone'
    assert formdef.fields[7].validation == {'type': 'phone'}
    assert formdef.fields[8].id == '_mobile'
    assert formdef.fields[8].validation == {'type': 'phone'}

    # change a varname value
    formdef.fields[0].varname = 'civilite'
    formdef.store()

    # reload config, check the varname is kept
    hobo_cmd.update_profile(profile, pub)
    formdef = UserFieldsFormDef(pub)
    assert formdef.fields[0].id == '_title'
    assert formdef.fields[0].varname == 'civilite'

    # change first_name/last_name order
    HOBO_JSON['profile']['fields'][1], HOBO_JSON['profile']['fields'][2] = (
        HOBO_JSON['profile']['fields'][2],
        HOBO_JSON['profile']['fields'][1],
    )
    hobo_cmd.update_profile(profile, pub)
    formdef = UserFieldsFormDef(pub)
    assert formdef.fields[1].id == '_last_name'
    assert formdef.fields[2].id == '_first_name'

    # disable mobile
    assert '_mobile' in [x.id for x in formdef.fields]
    HOBO_JSON['profile']['fields'][8]['disabled'] = True
    hobo_cmd.update_profile(profile, pub)
    formdef = UserFieldsFormDef(pub)
    assert '_mobile' not in [x.id for x in formdef.fields]

    # add a custom local field
    formdef = UserFieldsFormDef(pub)
    formdef.fields.append(fields.BoolField(id='3', label='bool'))
    formdef.store()
    hobo_cmd.update_profile(profile, pub)
    formdef = UserFieldsFormDef(pub)
    assert 'bool' in [x.label for x in formdef.fields]

    # create a fake entry in idp to check attribute mapping
    pub.cfg['idp'] = {'xxx': {}}
    hobo_cmd.update_profile(profile, pub)

    attribute_mapping = pub.cfg['idp']['xxx']['attribute-mapping']
    for field in profile.get('fields'):
        attribute_name = str(field['name'])
        field_id = str('_' + attribute_name)
        if field.get('disabled'):
            assert attribute_name not in attribute_mapping
        else:
            assert attribute_mapping[attribute_name] == field_id


def test_configure_authentication_methods(setuptest, http_requests):
    pub, hobo_cmd = setuptest
    pub.cfg['idp'] = {}
    service = [x for x in HOBO_JSON.get('services', []) if x.get('service-id') == 'wcs'][0]

    # with real metadata
    hobo_cmd.configure_authentication_methods(service, pub)
    hobo_cmd.configure_site_options(service, pub)

    # reload options
    pub.load_site_options()

    idp_keys = list(pub.cfg['idp'].keys())
    assert len(idp_keys) == 1
    assert pub.cfg['idp'][idp_keys[0]]['metadata_url'] == 'http://authentic.example.net/idp/saml2/metadata'
    assert pub.cfg['saml_identities']['registration-url']
    assert pub.cfg['sp']['idp-manage-user-attributes']
    assert pub.cfg['sp']['idp-manage-roles']
    assert pub.get_site_option('idp_account_url', 'variables').endswith('/accounts/')
    assert pub.get_site_option('idp_session_cookie_name') == 'a2-opened-session-5aef2f'

    # change idp
    new_hobo_json = copy.deepcopy(HOBO_JSON)
    new_authentic_service = {
        'service-id': 'authentic',
        'saml-idp-metadata-url': 'http://authentic2.example.net/idp/saml2/metadata',
        'template_name': '',
        'variables': {},
        'title': 'Authentic 2',
        'base_url': 'http://authentic2.example.net/',
        'id': 3,
        'slug': 'authentic-2',
        'secret_key': '6789',
    }
    index = None
    for i, service in enumerate(new_hobo_json['services']):
        if service['service-id'] == 'authentic':
            index = i
            break
    new_hobo_json['services'][index] = new_authentic_service
    try:
        hobo_cmd.all_services = new_hobo_json

        hobo_cmd.configure_authentication_methods(service, pub)
        idp_keys = list(pub.cfg['idp'].keys())
        assert len(idp_keys) == 1
        # idp changed
        assert (
            pub.cfg['idp'][idp_keys[0]]['metadata_url'] == 'http://authentic2.example.net/idp/saml2/metadata'
        )
    finally:
        hobo_cmd.all_services = HOBO_JSON


def test_deploy(setuptest, alt_tempdir, deploy_setup, settings):
    settings.THEMES_DIRECTORY = os.path.join(os.path.dirname(__file__), 'themes')
    cleanup_connection()
    cleanup()
    call_command(
        'hobo_deploy', '--ignore-timestamp', 'http://wcs.example.net/', os.path.join(alt_tempdir, 'hobo.json')
    )
    assert os.path.exists(os.path.join(alt_tempdir, 'tenants', 'wcs.example.net'))

    # update
    cleanup_connection()
    cleanup()
    with open(os.path.join(alt_tempdir, 'tenants', 'wcs.example.net', 'config.pck'), 'rb') as fd:
        pub_cfg = pickle.load(fd)
    assert pub_cfg['language'] == {'language': 'fr'}
    del pub_cfg['language']
    with open(os.path.join(alt_tempdir, 'tenants', 'wcs.example.net', 'config.pck'), 'wb') as fd:
        pickle.dump(pub_cfg, fd)
    call_command(
        'hobo_deploy',
        '--ignore-timestamp',
        'http://wcs.example.net/',
        os.path.join(alt_tempdir, 'tenants', 'wcs.example.net', 'hobo.json'),
    )
    with open(os.path.join(alt_tempdir, 'tenants', 'wcs.example.net', 'config.pck'), 'rb') as fd:
        pub_cfg = pickle.load(fd)
    assert pub_cfg['language'] == {'language': 'fr'}
    cleanup_connection()
    cleanup()


def test_configure_postgresql(setuptest, alt_tempdir, deploy_setup, settings):
    settings.THEMES_DIRECTORY = os.path.join(os.path.dirname(__file__), 'themes')
    pub, hobo_cmd = setuptest
    cleanup_connection()
    cleanup()
    with open(os.path.join(alt_tempdir, 'hobo.json'), 'w') as fd:
        hobo_json = copy.deepcopy(HOBO_JSON)
        del hobo_json['services'][1]  # authentic
        fd.write(json.dumps(HOBO_JSON))

    service = [x for x in HOBO_JSON.get('services', []) if x.get('service-id') == 'wcs'][0]

    call_command(
        'hobo_deploy', '--ignore-timestamp', 'http://wcs.example.net/', os.path.join(alt_tempdir, 'hobo.json')
    )
    assert os.path.exists(os.path.join(alt_tempdir, 'tenants', 'wcs.example.net'))

    cleanup_connection()
    cleanup()

    pub = CompatWcsPublisher.create_publisher(register_tld_names=False)
    pub.app_dir = os.path.join(alt_tempdir, 'tenants', 'wcs.example.net')
    pub.cfg['postgresql'] = {
        'createdb-connection-params': {'user': 'test', 'database': 'postgres'},
        'database-template-name': 'tests_wcs_%s',
        'user': 'fred',
    }
    pub.write_cfg()
    pub.set_config(skip_sql=True)
    service['base_url'] = service['base_url'].strip('/')

    pub.initialize_sql = mock.Mock()
    with mock.patch('psycopg2.connect') as connect:
        hobo_cmd.configure_sql(service, pub)
        assert connect.call_args_list[0][1] == {'user': 'test', 'dbname': 'postgres'}
        assert connect.call_args_list[1][1] == {
            'user': 'fred',
            'dbname': 'tests_wcs_wcs_example_net',
            'application_name': 'wcs',
            'connection_factory': sql.WcsPgConnection,
        }
    assert pub.initialize_sql.call_count == 1

    pub.reload_cfg()
    assert 'createdb-connection-params' in pub.cfg['postgresql']
    with mock.patch('psycopg2.connect') as connect:
        sql.get_connection(new=True)
        assert connect.call_args_list[0][1] == {
            'user': 'fred',
            'dbname': 'tests_wcs_wcs_example_net',
            'application_name': 'wcs',
            'connection_factory': sql.WcsPgConnection,
        }

    pub.cfg['postgresql']['database-template-name'] = 'very_long_' * 10 + '%s'
    with mock.patch('psycopg2.connect') as connect:
        hobo_cmd.configure_sql(service, pub)
        assert connect.call_args_list[0][1] == {'user': 'test', 'dbname': 'postgres'}
        assert connect.call_args_list[1][1] == {
            'user': 'fred',
            'dbname': 'very_long_very_long_very_long_c426_ng_very_long_wcs_example_net',
            'application_name': 'wcs',
            'connection_factory': sql.WcsPgConnection,
        }
        assert len(connect.call_args_list[1][1]['dbname']) == 63
    assert pub.initialize_sql.call_count == 2

    pub.cfg['postgresql']['database-template-name'] = 'test_2_%(domain_database_name)s'
    with mock.patch('psycopg2.connect') as connect:
        hobo_cmd.configure_sql(service, pub)
        assert connect.call_args_list[0][1] == {'user': 'test', 'dbname': 'postgres'}
        assert connect.call_args_list[1][1] == {
            'user': 'fred',
            'dbname': 'test_2_wcs_example_net',
            'application_name': 'wcs',
            'connection_factory': sql.WcsPgConnection,
        }
    assert pub.initialize_sql.call_count == 3


def test_redeploy(setuptest, alt_tempdir, deploy_setup, settings):
    settings.THEMES_DIRECTORY = os.path.join(os.path.dirname(__file__), 'themes')
    cleanup_connection()
    cleanup()
    call_command(
        'hobo_deploy', '--ignore-timestamp', 'http://wcs.example.net/', os.path.join(alt_tempdir, 'hobo.json')
    )
    assert os.path.exists(os.path.join(alt_tempdir, 'tenants', 'wcs.example.net'))

    with open(os.path.join(alt_tempdir, 'hobo2.json'), 'w') as fd:
        hobo_json = copy.deepcopy(HOBO_JSON)
        del hobo_json['services'][1]  # authentic
        hobo_json['services'][1]['saml-sp-metadata-url'] = 'http://wcs2.example.net/saml/metadata'
        hobo_json['services'][1]['base_url'] = 'http://wcs2.example.net/'
        hobo_json['services'][1]['backoffice-menu-url'] = 'http://wcs2.example.net/backoffice/menu.json'
        hobo_json['services'][1]['slug'] = 'test-wcs2'
        fd.write(json.dumps(hobo_json))

    call_command(
        'hobo_deploy',
        '--ignore-timestamp',
        'http://wcs2.example.net/',
        os.path.join(alt_tempdir, 'hobo2.json'),
    )
    assert os.path.exists(os.path.join(alt_tempdir, 'tenants', 'wcs2.example.net'))

    with mock.patch('wcs.ctl.management.commands.hobo_deploy.Command.deploy') as deploy:
        call_command('hobo_deploy', '--redeploy')
        assert deploy.call_count == 2
        assert {x[1]['base_url'] for x in deploy.call_args_list} == {
            'http://wcs.example.net/',
            'http://wcs2.example.net/',
        }
    cleanup_connection()
    cleanup()


def test_configure_site_options_legacy_urls(setuptest, alt_tempdir):
    pub, hobo_cmd = setuptest
    service = [x for x in HOBO_JSON.get('services', []) if x.get('service-id') == 'wcs'][0]
    hobo_cmd.configure_site_options(service, pub)
    pub.load_site_options()
    assert pub.get_site_option('oldportal.example.net', 'legacy-urls') == 'portal.example.net'
    assert pub.get_site_option('veryoldportal.example.net', 'legacy-urls') == 'portal.example.net'

    hobo_cmd.all_services = copy.deepcopy(HOBO_JSON)
    hobo_cmd.all_services['services'][4]['legacy_urls'] = [{'base_url': 'http://oldportal.example.net/'}]
    hobo_cmd.configure_site_options(service, pub, ignore_timestamp=True)
    pub.load_site_options()
    assert pub.get_site_option('veryoldportal.example.net', 'legacy-urls') is None

    hobo_cmd.all_services['services'][4]['legacy_urls'] = []
    hobo_cmd.configure_site_options(service, pub, ignore_timestamp=True)
    pub.load_site_options()
    assert pub.get_site_option('veryoldportal.example.net', 'legacy-urls') is None


def test_deploy_skeleton_dir(setuptest, alt_tempdir, deploy_setup_skeleton_dir, settings):
    settings.THEMES_DIRECTORY = os.path.join(os.path.dirname(__file__), 'themes')
    cleanup_connection()
    cleanup()
    call_command(
        'hobo_deploy', '--ignore-timestamp', 'http://wcs.example.net/', os.path.join(alt_tempdir, 'hobo.json')
    )
    assert os.path.exists(os.path.join(alt_tempdir, 'tenants', 'wcs.example.net'))
    cleanup_connection()
    cleanup()
