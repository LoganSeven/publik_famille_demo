# w.c.s. - web application for online forms
# Copyright (C) 2005-2023  Entr'ouvert
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, see <http://www.gnu.org/licenses/>.

import configparser
import hashlib
import io
import json
import os
import random
import sys
import tempfile
import urllib.parse
import zipfile

import phonenumbers
from django.conf import settings
from django.utils.encoding import force_bytes
from quixote import cleanup

from wcs.admin.settings import UserFieldsFormDef
from wcs.fields import DateField, EmailField, StringField
from wcs.qommon import force_str, misc
from wcs.qommon.publisher import UnknownTenantError, get_publisher_class
from wcs.qommon.storage import atomic_write
from wcs.sql import cleanup_connection

from . import TenantCommand


def atomic_symlink(src, dst):
    if os.path.exists(dst) and os.readlink(dst) == src:
        return
    if os.path.exists(dst + '.tmp'):
        os.unlink(dst + '.tmp')
    os.symlink(src, dst + '.tmp')
    os.rename(dst + '.tmp', dst)


class NoChange(Exception):
    pass


class Command(TenantCommand):
    def add_arguments(self, parser):
        parser.add_argument('base_url', metavar='BASE_URL', nargs='?', type=str)
        parser.add_argument('json_filename', metavar='JSON_FILENAME', nargs='?', type=str)
        parser.add_argument('--ignore-timestamp', dest='ignore_timestamp', action='store_true', default=False)
        parser.add_argument('--redeploy', action='store_true', default=False)

    def handle(self, **options):
        if options.get('redeploy'):
            options['ignore_timestamp'] = True
            for tenant in get_publisher_class().get_tenants():
                hobo_json_path = os.path.join(tenant.directory, 'hobo.json')
                if not os.path.exists(hobo_json_path):
                    continue
                with open(hobo_json_path) as fd:
                    hobo_json = json.load(fd)
                try:
                    me = [service for service in hobo_json['services'] if service.get('this') is True][0]
                except IndexError:
                    pass
                else:
                    cleanup()
                    options['base_url'] = me['base_url']
                    options['json_filename'] = hobo_json_path
                    self.init_tenant_publisher(tenant.hostname, register_tld_names=False)
                    self.deploy(**options)
                    cleanup_connection()
        else:
            self.deploy(**options)

    def deploy(self, **options):
        pub = get_publisher_class().create_publisher(register_tld_names=False)

        global_app_dir = pub.app_dir
        global_tenants_dir = os.path.join(global_app_dir, 'tenants')
        base_url = options.get('base_url')

        if options.get('json_filename') == '-':
            # get environment definition from stdin
            self.all_services = json.load(sys.stdin)
        else:
            with open(options.get('json_filename')) as fd:
                self.all_services = json.load(fd)

        try:
            service = [
                x
                for x in self.all_services.get('services', [])
                if x.get('service-id') == 'wcs'
                and x.get('base_url') in (base_url, base_url.rstrip('/'))
                and not x.get('secondary')
            ][0]
        except IndexError:
            return

        service['this'] = True
        stored_hobo_json_content = json.dumps(self.all_services, indent=2)
        if base_url.endswith('/'):  # wcs doesn't expect a trailing slash
            service['base_url'] = base_url[:-1]

        new_site = False
        force_spconfig = False
        try:
            pub.set_tenant_by_hostname(self.get_instance_path(service.get('base_url')), skip_sql=True)
        except UnknownTenantError:
            if not os.path.exists(global_tenants_dir):
                os.mkdir(global_tenants_dir)
            tenant_app_dir = os.path.join(global_tenants_dir, self.get_instance_path(service.get('base_url')))
            # check in legacy_urls for domain change
            for legacy_urls in service.get('legacy_urls', []):
                legacy_base_url = legacy_urls.get('base_url')
                if legacy_base_url.endswith('/'):  # wcs doesn't expect a trailing slash
                    legacy_base_url = legacy_base_url[:-1]
                legacy_instance_path = self.get_instance_path(legacy_base_url)
                try:
                    pub.set_tenant_by_hostname(legacy_instance_path, skip_sql=True)
                    # rename tenant directory
                    for base_dir in (global_app_dir, global_tenants_dir):
                        legacy_tenant_dir = os.path.join(base_dir, legacy_instance_path)
                        if os.path.exists(legacy_tenant_dir):
                            print('rename tenant directory %s to %s' % (legacy_tenant_dir, tenant_app_dir))
                            os.rename(legacy_tenant_dir, tenant_app_dir)
                            site_options_filepath = os.path.join(tenant_app_dir, 'site-options.cfg')
                            if os.path.exists(site_options_filepath):
                                config = configparser.ConfigParser(interpolation=None)
                                config.read(site_options_filepath)
                                if 'options' not in config.sections():
                                    config.add_section('options')
                                config.set(
                                    'options',
                                    'allowed_hostname',
                                    self.get_instance_path(service.get('base_url')),
                                )
                                with open(site_options_filepath, 'w') as site_options:
                                    config.write(site_options)
                            break
                    else:
                        print('tenant directory not found')
                        return
                    pub.set_tenant_by_hostname(self.get_instance_path(service.get('base_url')))
                    force_spconfig = True
                    break
                except UnknownTenantError:
                    pass
            else:
                # new tenant
                print('initializing instance in', tenant_app_dir)
                os.mkdir(tenant_app_dir)
                pub.set_tenant_by_hostname(self.get_instance_path(service.get('base_url')))
                self.mark_deployment_state(pub, deployed=False)
                skeleton_filenames = ['default', 'default.zip']
                if service.get('template_name'):
                    skeleton_filenames.append(service.get('template_name'))
                for skeleton_filename in reversed(skeleton_filenames):
                    skeleton_filepath = os.path.join(global_app_dir, 'skeletons', skeleton_filename)
                    if os.path.isdir(skeleton_filepath):
                        fd = io.BytesIO()
                        with zipfile.ZipFile(fd, 'w') as z:
                            for base_path, dummy, filenames in os.walk(skeleton_filepath):
                                for filename in filenames:
                                    z.write(
                                        os.path.join(base_path, filename),
                                        arcname=os.path.join(base_path, filename).removeprefix(
                                            skeleton_filepath
                                        ),
                                    )
                        fd.seek(0)
                        pub.import_zip(fd)
                        break
                    if os.path.exists(skeleton_filepath):
                        with open(skeleton_filepath, 'rb') as fd:
                            pub.import_zip(fd)
                        break
                self.mark_deployment_state(pub, deployed=False)  # again in case skeleton overwrote it
                new_site = True

        else:
            print('updating instance in', pub.app_dir)

        try:
            self.configure_site_options(service, pub, ignore_timestamp=options.get('ignore_timestamp'))
        except NoChange:
            print('  skipping')
            return

        pub.set_config(skip_sql=new_site)
        if new_site:
            self.configure_sql(service, pub)
        self.update_configuration(service, pub)
        self.configure_authentication_methods(service, pub, force_spconfig)

        self.update_profile(self.all_services.get('profile', {}), pub)
        # Store hobo.json
        atomic_write(os.path.join(pub.tenant.directory, 'hobo.json'), force_bytes(stored_hobo_json_content))

        if new_site:
            self.mark_deployment_state(pub, deployed=True)

    def update_configuration(self, service, pub):
        if not pub.cfg.get('misc'):
            pub.cfg['misc'] = {}
        pub.cfg['misc']['sitename'] = force_str(service.get('title'))
        pub.cfg['misc']['frontoffice-url'] = force_str(service.get('base_url'))
        if not pub.cfg.get('language'):
            pub.cfg['language'] = {'language': 'fr'}

        if not pub.cfg.get('emails'):
            pub.cfg['emails'] = {}
        if not pub.cfg.get('sms'):
            pub.cfg['sms'] = {}

        variables = self.all_services.get('variables') or {}
        variables.update(service.get('variables') or {})

        theme_id = variables.get('theme')
        theme_data = None
        if theme_id and os.path.exists(settings.THEMES_DIRECTORY):
            for theme_module in os.listdir(settings.THEMES_DIRECTORY):
                try:
                    with open(os.path.join(settings.THEMES_DIRECTORY, theme_module, 'themes.json')) as fd:
                        themes_json = json.load(fd)
                except OSError:
                    continue
                if not isinstance(themes_json, dict):  # compat
                    themes_json = {'themes': themes_json}
                for theme_data in themes_json.get('themes'):
                    if theme_data.get('id') == theme_id:
                        if 'module' not in theme_data:
                            theme_data['module'] = theme_module
                        break
                else:
                    theme_data = None
                    continue
                break
        if theme_data:
            pub.cfg['branding'] = {'theme': 'publik-base'}
            pub.cfg['branding']['included_js_libraries'] = theme_data['variables'].get(
                'included_js_libraries'
            )
            tenant_dir = pub.app_dir
            theme_dir = os.path.join(tenant_dir, 'theme')
            target_dir = os.path.join(settings.THEMES_DIRECTORY, theme_data['module'])
            atomic_symlink(target_dir, theme_dir)
            for component in ('static', 'templates'):
                component_dir = os.path.join(tenant_dir, component)
                if not os.path.islink(component_dir) and os.path.isdir(component_dir):
                    try:
                        os.rmdir(component_dir)
                    except OSError:
                        continue
                if not theme_data.get('overlay'):
                    try:
                        os.unlink(component_dir)
                    except OSError:
                        pass
                else:
                    atomic_symlink(
                        os.path.join(settings.THEMES_DIRECTORY, theme_data['overlay'], component),
                        component_dir,
                    )

        if variables.get('default_from_email'):
            pub.cfg['emails']['from'] = force_str(variables.get('default_from_email'))
        if variables.get('email_signature') is not None:
            pub.cfg['emails']['footer'] = force_str(variables.get('email_signature'))

        if variables.get('sms_url'):
            pub.cfg['sms']['passerelle_url'] = force_str(variables.get('sms_url'))
            pub.cfg['sms']['mode'] = 'passerelle'
        if variables.get('sms_sender'):
            pub.cfg['sms']['sender'] = force_str(variables.get('sms_sender'))

        pub.write_cfg()

    def update_profile(self, profile, pub):
        formdef = UserFieldsFormDef(publisher=pub)
        profile_fields = {}
        profile_field_ids = ['_' + x['name'] for x in profile.get('fields', [])]
        for field in formdef.fields:
            if field.id in profile_field_ids:
                profile_fields[field.id] = field

        html5_autocomplete_map = {
            'first_name': 'given-name',
            'last_name': 'family-name',
            'address': 'address-line1',
            'zipcode': 'postal-code',
            'city': 'address-level2',
            'country': 'country',
            'phone': 'tel',
            'email': 'email',
        }

        # create or update profile fields
        for attribute in profile.get('fields', []):
            field_id = '_' + attribute['name']
            if field_id not in profile_fields:
                field_class = StringField
                if attribute['kind'] == 'email':
                    field_class = EmailField
                elif attribute['kind'] in ('date', 'birthdate', 'fedict_date'):
                    field_class = DateField
                new_field = field_class(
                    label=force_str(attribute['label']), type=field_class.key, varname=attribute['name']
                )
                new_field.id = field_id
                profile_fields[field_id] = new_field
            else:
                # remove it for the moment
                formdef.fields.remove(profile_fields[field_id])

            profile_fields[field_id].label = force_str(attribute['label'])
            profile_fields[field_id].hint = force_str(attribute['description'])
            profile_fields[field_id].required = attribute['required']

            if attribute['disabled']:
                profile_field_ids.remove('_' + attribute['name'])
            if attribute['name'] in html5_autocomplete_map:
                profile_fields[field_id].extra_css_class = (
                    'autocomplete-%s' % html5_autocomplete_map[attribute['name']]
                )
            if attribute['kind'] in ('phone_number', 'fr_phone_number'):
                profile_fields[field_id].validation = {'type': 'phone'}

        # insert profile fields at the beginning
        formdef.fields = [profile_fields[x] for x in profile_field_ids] + formdef.fields
        formdef.store()

        pub.cfg['users']['field_email'] = '_email'
        if not pub.cfg['users'].get('field_phone'):
            pub.cfg['users']['field_phone'] = '_phone'
        if not pub.cfg['users'].get('field_mobile'):
            pub.cfg['users']['field_mobile'] = '_mobile'
        if not (pub.cfg['users'].get('fullname_template') or pub.cfg['users'].get('field_name')):
            pub.cfg['users'][
                'fullname_template'
            ] = '{{user_var_first_name|default:""}} {{user_var_last_name|default:""}}'
        pub.write_cfg()

        # add mapping for SAML provisioning
        for idp in pub.cfg.get('idp', {}).values():
            if not idp.get('attribute-mapping'):
                idp['attribute-mapping'] = {}
            for field in profile.get('fields', []):
                attribute_name = field['name']
                field_id = '_' + attribute_name
                if field_id in profile_field_ids:
                    idp['attribute-mapping'][str(attribute_name)] = str(field_id)
        pub.write_cfg()

    def configure_authentication_methods(self, service, pub, force_spconfig=False):
        # look for an identity provider
        idps = [x for x in self.all_services.get('services', []) if x.get('service-id') == 'authentic']
        if not pub.cfg.get('identification'):
            pub.cfg['identification'] = {}
        methods = pub.cfg['identification'].get('methods', [])
        if idps and 'idp' not in methods:
            methods.append('idp')
        elif not idps and 'password' not in methods:
            methods.append('password')
        pub.cfg['identification']['methods'] = methods
        if not pub.cfg.get('sp'):
            pub.cfg['sp'] = {}
        pub.cfg['sp']['idp-manage-user-attributes'] = bool(idps)
        pub.cfg['sp']['idp-manage-roles'] = bool(idps)
        pub.write_cfg()

        if not idps:
            return

        # initialize service provider side
        if not pub.cfg['sp'].get('publickey') or force_spconfig:
            from wcs.qommon.ident.idp import MethodAdminDirectory

            spconfig = pub.cfg['sp']
            spconfig['saml2_base_url'] = str(service.get('base_url')) + '/saml'
            spconfig['saml2_providerid'] = spconfig['saml2_base_url'] + '/metadata'
            MethodAdminDirectory().generate_rsa_keypair()

        if 'saml_identities' not in pub.cfg:
            pub.cfg['saml_identities'] = {}

        if idps:
            pub.cfg['saml_identities']['identity-creation'] = 'self'

        # write down configuration to disk as it will get reloaded
        # automatically and we don't want to lose our changes.
        pub.write_cfg()

        if 'idp' in pub.cfg:
            idp_urls = [idp['saml-idp-metadata-url'] for idp in idps]
            # clean up configuration
            to_delete = []
            for idp_key, idp in pub.cfg['idp'].items():
                if idp['metadata_url'] not in idp_urls:
                    to_delete.append(idp_key)
            for idp_key in to_delete:
                del pub.cfg['idp'][idp_key]
            pub.write_cfg()

        for idp in idps:
            if not idp['base_url'].endswith('/'):
                idp['base_url'] = idp['base_url'] + '/'
            metadata_url = '%sidp/saml2/metadata' % idp['base_url']
            try:
                rfd = misc.urlopen(metadata_url)
            except misc.ConnectionError as e:
                print('failed to get metadata URL', metadata_url, e, file=sys.stderr)
                continue

            s = rfd.read()
            metadata_pathname = tempfile.mkstemp('.metadata')[1]
            atomic_write(metadata_pathname, force_bytes(s))

            from wcs.qommon.ident.idp import AdminIDPDir

            admin_dir = AdminIDPDir()
            key_provider_id = admin_dir.submit_new_remote(metadata_pathname, metadata_url)
            admin_attribute = service.get('variables', {}).get('admin-attribute')
            if not admin_attribute:
                admin_attribute = 'is_superuser=true'
            else:
                admin_attribute = force_str(admin_attribute)
            admin_attribute_dict = dict([admin_attribute.split('=')])
            pub.cfg['idp'][key_provider_id]['admin-attributes'] = admin_attribute_dict
            pub.cfg['idp'][key_provider_id]['nameidformat'] = 'unspecified'
            pub.cfg['saml_identities']['registration-url'] = str('%sregister/' % idp['base_url'])
            pub.write_cfg()

    def get_instance_path(self, base_url):
        parsed_url = urllib.parse.urlsplit(base_url)
        instance_path = parsed_url.netloc
        if parsed_url.path:
            instance_path += '+%s' % parsed_url.path.replace('/', '+')
        return instance_path

    def handle_maintenance_variables(self, name, value):
        match = False
        if name == 'SETTING_TENANT_DISABLE_CRON_JOBS':
            name = 'disable_cron_jobs'
            match = True
        if name.startswith('SETTING_MAINTENANCE'):
            name = name[8:]
            match = True
        if match and not value:
            value = None
        return name, value

    def configure_site_options(self, current_service, pub, ignore_timestamp=False):
        # configure site-options.cfg
        config = configparser.ConfigParser(interpolation=None)
        site_options_filepath = os.path.join(pub.app_dir, 'site-options.cfg')
        if os.path.exists(site_options_filepath):
            config.read(site_options_filepath)

        if not ignore_timestamp:
            try:
                if config.get('hobo', 'timestamp') == self.all_services.get('timestamp'):
                    raise NoChange()
            except (configparser.NoOptionError, configparser.NoSectionError):
                pass

        if 'hobo' not in config.sections():
            config.add_section('hobo')
        config.set('hobo', 'timestamp', self.all_services.get('timestamp'))

        if 'options' not in config.sections():
            config.add_section('options')

        config.set('options', 'allowed_hostname', pub.tenant.hostname)

        variables = {}
        api_secrets = {}
        legacy_urls = {}
        for service in self.all_services.get('services', []):
            service_url = service.get('base_url')
            if not service_url.endswith('/'):
                service_url += '/'
            if service.get('slug'):
                variables['%s_url' % service.get('slug').replace('-', '_')] = service_url

            if not service.get('secret_key'):
                continue

            domain = urllib.parse.urlparse(service_url).netloc.split(':')[0]
            if service is current_service:
                if config.has_option('api-secrets', domain):
                    api_secrets[domain] = config.get('api-secrets', domain)
                else:
                    # custom key calcultation for "self", as the shared_secret code
                    # would do secret_key ^ secret_key = 0.
                    api_secrets[domain] = self.shared_secret(
                        current_service.get('secret_key'), str(random.SystemRandom().random())
                    )
                continue

            api_secrets[domain] = self.shared_secret(
                current_service.get('secret_key'), service.get('secret_key')
            )

            if service.get('service-id') == 'authentic':
                variables['idp_url'] = service_url
                variables['idp_account_url'] = service_url + 'accounts/'
                variables['idp_api_url'] = service_url + 'api/'
                variables['idp_registration_url'] = service_url + 'register/'
                idp_hash = hashlib.md5(force_bytes(service_url)).hexdigest()[:6]
                config.set('options', 'idp_session_cookie_name', 'a2-opened-session-%s' % idp_hash)

            if service.get('secondary'):
                continue

            if service.get('service-id') == 'combo':
                if 'portal-agent' in service.get('template_name', ''):
                    variables['portal_agent_url'] = service_url
                    variables['portal_agent_title'] = service.get('title')
                elif 'portal-user' in service.get('template_name', ''):
                    variables['portal_url'] = service_url
                    variables['portal_user_url'] = service_url
                    variables['portal_user_title'] = service.get('title')
                    config.set('options', 'theme_skeleton_url', service.get('base_url') + '__skeleton__/')

            if service.get('service-id') == 'lingo':
                variables['lingo_url'] = urllib.parse.urljoin(service_url, '/')

            for legacy_url in service.get('legacy_urls', []):
                legacy_domain = urllib.parse.urlparse(legacy_url['base_url']).netloc.split(':')[0]
                legacy_urls[legacy_domain] = domain

        if self.all_services.get('variables'):
            for key, value in self.all_services.get('variables').items():
                key, value = self.handle_maintenance_variables(key, value)
                variables[key] = value
        for key, value in current_service.get('variables', {}).items():
            variables[key] = value

        if variables:
            if 'variables' not in config.sections():
                config.add_section('variables')

            def normalized(key):
                if key.startswith('_'):
                    yield key[1:].replace('-', '_')
                if '-' in key:
                    yield key.replace('-', '_')

            for key, value in variables.items():
                key = force_str(key)
                if key.startswith('SETTING_'):
                    # skip and remove SETTING_ variables
                    value = None
                if value is None:
                    config.remove_option('variables', key)
                    for norm_key in normalized(key):
                        if variables.get(norm_key) is None:
                            config.remove_option('variables', norm_key)
                    continue
                if isinstance(value, (list, dict)):
                    try:
                        config.set('variables', f'{key}__json', json.dumps(value))
                    except TypeError:
                        pass
                if not isinstance(value, str):
                    value = str(value)
                value = force_str(value)
                config.set('variables', key, value)
                for norm_key in normalized(key):
                    config.set('variables', norm_key, value)

        if variables.get('local_country_code'):
            region_code = phonenumbers.region_code_for_country_code(int(variables.get('local_country_code')))
            config.set('options', 'local-region-code', region_code)

        if 'api-secrets' not in config.sections():
            config.add_section('api-secrets')
        if 'wscall-secrets' not in config.sections():
            config.add_section('wscall-secrets')
        for key, value in api_secrets.items():
            config.set('api-secrets', key, value)
            # for now the secrets are the same whatever the direction is.
            config.set('wscall-secrets', key, value)

        if config.has_section('legacy-urls'):
            config.remove_section('legacy-urls')
        if legacy_urls:
            config.add_section('legacy-urls')
        for key, value in legacy_urls.items():
            config.set('legacy-urls', key, value)

        # add known services
        for service in self.all_services.get('services', []):
            if service.get('secondary'):
                continue
            if service.get('service-id') == 'fargo':
                config.set('options', 'fargo_url', service.get('base_url'))
            elif service.get('service-id') == 'chrono':
                config.set('options', 'chrono_url', service.get('base_url'))

        try:
            portal_agent_url = config.get('variables', 'portal_agent_url')
        except configparser.NoOptionError:
            pass
        else:
            if portal_agent_url.endswith('/'):
                portal_agent_url = portal_agent_url.rstrip('/')
            extra_head = (
                '''<script src="%s/__services.js"></script>'''
                '''<script src="%s/static/js/publik.js"></script>''' % (portal_agent_url, portal_agent_url)
            )
            config.set('options', 'backoffice_extra_head', extra_head)

        with open(site_options_filepath, 'w') as site_options:
            config.write(site_options)

    def normalize_database_name(self, database_name):
        if len(database_name) > 63:
            digest = hashlib.md5(force_bytes(database_name)).hexdigest()[:4]
            database_name = '%s_%s_%s' % (database_name[:29], digest, database_name[-28:])
        return database_name

    def configure_sql(self, service, pub):
        if not pub.cfg.get('postgresql'):
            return

        import psycopg2
        import psycopg2.errorcodes

        # determine database name using the instance path
        domain_database_name = (
            self.get_instance_path(service.get('base_url'))
            .replace('-', '_')
            .replace('.', '_')
            .replace('+', '_')
        )

        if pub.cfg['postgresql'].get('database-template-name'):
            database_template_name = pub.cfg['postgresql'].pop('database-template-name')
            # replace legacy %s by dictionary equivalent
            database_template_name = database_template_name.replace('%s', '%(domain_database_name)s')
            database_name = (database_template_name % {'domain_database_name': domain_database_name}).strip(
                '_'
            )
        else:
            # legacy way to create a database name, if it contained an
            # underscore character, use the first part as a prefix
            database_name = pub.cfg['postgresql'].get('database', 'wcs')
            if domain_database_name not in database_name:
                database_name = '%s_%s' % (database_name.split('_')[0], domain_database_name)

        database_name = self.normalize_database_name(database_name)

        createdb_cfg = pub.cfg['postgresql'].get('createdb-connection-params')
        if not createdb_cfg:
            createdb_cfg = {}
            for k, v in pub.cfg['postgresql'].items():
                if v and isinstance(v, str):
                    createdb_cfg[k] = v

        if 'database' in createdb_cfg:
            createdb_cfg['dbname'] = createdb_cfg.pop('database')
        try:
            pgconn = psycopg2.connect(**createdb_cfg)
        except psycopg2.Error as e:
            print(
                'failed to connect to postgresql (%s)' % psycopg2.errorcodes.lookup(e.pgcode), file=sys.stderr
            )
            return

        pgconn.set_isolation_level(psycopg2.extensions.ISOLATION_LEVEL_AUTOCOMMIT)
        cur = pgconn.cursor()
        new_database = True
        try:
            cur.execute('''CREATE DATABASE %s''' % database_name)
        except psycopg2.Error as e:
            if e.pgcode == psycopg2.errorcodes.DUPLICATE_DATABASE:
                cur.execute(
                    """SELECT table_name
                               FROM information_schema.tables
                               WHERE table_schema = 'public'
                               AND table_type = 'BASE TABLE'
                               AND table_name = 'wcs_meta'"""
                )

                if cur.fetchall():
                    new_database = False
            else:
                print(
                    'failed to create database (%s)' % psycopg2.errorcodes.lookup(e.pgcode), file=sys.stderr
                )
                return
        else:
            cur.close()

        pub.cfg['postgresql']['database'] = database_name
        pub.write_cfg()
        pub.set_config(skip_sql=False)

        if not new_database:
            return

        # create tables etc.
        pub.initialize_sql()

    @classmethod
    def shared_secret(cls, secret1, secret2):
        secret1 = hashlib.sha256(force_bytes(secret1)).hexdigest()
        secret2 = hashlib.sha256(force_bytes(secret2)).hexdigest()
        # rstrip('L') for py2/3 compatibility, as py2 formats number as 0x...L, and py3 as 0x...
        return hex(int(secret1, 16) ^ int(secret2, 16))[2:].rstrip('L')

    def mark_deployment_state(self, pub, deployed=False):
        config = configparser.ConfigParser(interpolation=None)
        site_options_filepath = os.path.join(pub.app_dir, 'site-options.cfg')
        if os.path.exists(site_options_filepath):
            config.read(site_options_filepath)
        if deployed:
            allowed_hostname = config.get('options', 'allowed_hostname')
            if allowed_hostname == 'deployment-in-progress.invalid':
                config.remove_option('options', 'allowed_hostname')
        else:
            if 'options' not in config.sections():
                config.add_section('options')
            config.set('options', 'allowed_hostname', 'deployment-in-progress.invalid')
        with open(site_options_filepath, 'w') as site_options:
            config.write(site_options)
