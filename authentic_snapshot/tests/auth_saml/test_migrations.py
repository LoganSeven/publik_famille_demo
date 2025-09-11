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

import os


def test_saml_authenticator_data_migration(migration, settings):
    app = 'authentic2_auth_saml'
    migrate_from = [(app, '0001_initial')]
    migrate_to = [(app, '0002_auto_20220608_1559')]

    old_apps = migration.before(migrate_from)
    SAMLAuthenticator = old_apps.get_model(app, 'SAMLAuthenticator')

    settings.A2_AUTH_SAML_ENABLE = True
    settings.MELLON_METADATA_CACHE_TIME = 42
    settings.MELLON_METADATA_HTTP_TIMEOUT = 42
    settings.MELLON_PROVISION = False
    settings.MELLON_VERIFY_SSL_CERTIFICATE = True
    settings.MELLON_TRANSIENT_FEDERATION_ATTRIBUTE = None
    settings.MELLON_USERNAME_TEMPLATE = 'test'
    settings.MELLON_NAME_ID_POLICY_ALLOW_CREATE = False
    settings.MELLON_FORCE_AUTHN = True
    settings.MELLON_ADD_AUTHNREQUEST_NEXT_URL_EXTENSION = False
    settings.MELLON_GROUP_ATTRIBUTE = 'role'
    settings.MELLON_CREATE_GROUP = True
    settings.MELLON_ERROR_URL = 'https://example.com/error/'
    settings.MELLON_AUTHN_CLASSREF = ('class1', 'class2')
    settings.MELLON_LOGIN_HINTS = ['hint1', 'hint2']
    settings.AUTH_FRONTENDS_KWARGS = {
        'saml': {
            'priority': 1,
            'show_condition': {
                '0': 'first condition',
                '1': 'second condition',
            },
        }
    }
    settings.MELLON_IDENTITY_PROVIDERS = [
        {
            'METADATA': os.path.join(os.path.dirname(__file__), 'metadata.xml'),
            'REALM': 'test',
            'METADATA_CACHE_TIME': 43,
            'METADATA_HTTP_TIMEOUT': 43,
            'PROVISION': True,
            'LOOKUP_BY_ATTRIBUTES': [],
        },
        {
            'METADATA_PATH': os.path.join(os.path.dirname(__file__), 'metadata.xml'),
            'NAME_ID_POLICY_ALLOW_CREATE': True,
            'FORCE_AUTHN': False,
            'ADD_AUTHNREQUEST_NEXT_URL_EXTENSION': True,
            'A2_ATTRIBUTE_MAPPING': [
                {
                    'attribute': 'email',
                    'saml_attribute': 'mail',
                },
            ],
            'LOOKUP_BY_ATTRIBUTES': [{'saml_attribute': 'email', 'user_field': 'email'}],
        },
        {
            'METADATA_URL': 'https://example.com/metadata.xml',
            'SLUG': 'third',
            'ATTRIBUTE_MAPPING': {'email': 'attributes[mail][0]'},
            'SUPERUSER_MAPPING': {'roles': 'Admin'},
        },
    ]

    new_apps = migration.apply(migrate_to)
    SAMLAuthenticator = new_apps.get_model(app, 'SAMLAuthenticator')
    first_authenticator, second_authenticator, third_authenticator = SAMLAuthenticator.objects.all()
    assert first_authenticator.slug == '0'
    assert first_authenticator.order == 1
    assert first_authenticator.show_condition == 'first condition'
    assert first_authenticator.enabled is True
    assert first_authenticator.metadata_path == os.path.join(os.path.dirname(__file__), 'metadata.xml')
    assert first_authenticator.metadata_url == ''
    assert first_authenticator.metadata_cache_time == 43
    assert first_authenticator.metadata_http_timeout == 43
    assert first_authenticator.provision is True
    assert first_authenticator.verify_ssl_certificate is True
    assert first_authenticator.transient_federation_attribute == ''
    assert first_authenticator.realm == 'test'
    assert first_authenticator.username_template == 'test'
    assert first_authenticator.name_id_policy_format == ''
    assert first_authenticator.name_id_policy_allow_create is False
    assert first_authenticator.force_authn is True
    assert first_authenticator.add_authnrequest_next_url_extension is False
    assert first_authenticator.group_attribute == 'role'
    assert first_authenticator.create_group is True
    assert first_authenticator.error_url == 'https://example.com/error/'
    assert first_authenticator.error_redirect_after_timeout == 120
    assert first_authenticator.authn_classref == 'class1, class2'
    assert first_authenticator.login_hints == 'hint1, hint2'
    assert first_authenticator.lookup_by_attributes == []
    assert first_authenticator.a2_attribute_mapping == []
    assert first_authenticator.attribute_mapping == {}
    assert first_authenticator.superuser_mapping == {}

    assert second_authenticator.slug == '1'
    assert second_authenticator.order == 1
    assert second_authenticator.show_condition == 'second condition'
    assert second_authenticator.enabled is True
    assert second_authenticator.metadata_path == os.path.join(os.path.dirname(__file__), 'metadata.xml')
    assert second_authenticator.metadata_url == ''
    assert second_authenticator.metadata_cache_time == 42
    assert second_authenticator.metadata_http_timeout == 42
    assert second_authenticator.provision is False
    assert second_authenticator.verify_ssl_certificate is True
    assert second_authenticator.transient_federation_attribute == ''
    assert second_authenticator.realm == 'saml'
    assert second_authenticator.username_template == 'test'
    assert second_authenticator.name_id_policy_format == ''
    assert second_authenticator.name_id_policy_allow_create is True
    assert second_authenticator.force_authn is False
    assert second_authenticator.add_authnrequest_next_url_extension is True
    assert second_authenticator.group_attribute == 'role'
    assert second_authenticator.create_group is True
    assert second_authenticator.error_url == 'https://example.com/error/'
    assert second_authenticator.error_redirect_after_timeout == 120
    assert second_authenticator.authn_classref == 'class1, class2'
    assert second_authenticator.login_hints == 'hint1, hint2'
    assert second_authenticator.lookup_by_attributes == [{'saml_attribute': 'email', 'user_field': 'email'}]
    assert second_authenticator.a2_attribute_mapping == [
        {
            'attribute': 'email',
            'saml_attribute': 'mail',
        },
    ]
    assert first_authenticator.attribute_mapping == {}
    assert first_authenticator.superuser_mapping == {}

    assert third_authenticator.slug == 'third'
    assert third_authenticator.order == 1
    assert third_authenticator.show_condition == ''
    assert third_authenticator.enabled is True
    assert third_authenticator.metadata_path == ''
    assert third_authenticator.metadata_url == 'https://example.com/metadata.xml'
    assert third_authenticator.metadata_cache_time == 42
    assert third_authenticator.metadata_http_timeout == 42
    assert third_authenticator.provision is False
    assert third_authenticator.verify_ssl_certificate is True
    assert third_authenticator.transient_federation_attribute == ''
    assert third_authenticator.realm == 'saml'
    assert third_authenticator.username_template == 'test'
    assert third_authenticator.name_id_policy_format == ''
    assert third_authenticator.name_id_policy_format == ''
    assert third_authenticator.name_id_policy_allow_create is False
    assert third_authenticator.force_authn is True
    assert third_authenticator.group_attribute == 'role'
    assert third_authenticator.create_group is True
    assert third_authenticator.error_url == 'https://example.com/error/'
    assert third_authenticator.error_redirect_after_timeout == 120
    assert third_authenticator.authn_classref == 'class1, class2'
    assert third_authenticator.login_hints == 'hint1, hint2'
    assert third_authenticator.lookup_by_attributes == [
        {'saml_attribute': 'email', 'user_field': 'email', 'ignore-case': True},
        {'saml_attribute': 'username', 'user_field': 'username'},
    ]
    assert third_authenticator.a2_attribute_mapping == []
    assert third_authenticator.attribute_mapping == {'email': 'attributes[mail][0]'}
    assert third_authenticator.superuser_mapping == {'roles': 'Admin'}


def test_saml_authenticator_data_migration_empty_configuration(migration, settings):
    app = 'authentic2_auth_saml'
    migrate_from = [(app, '0001_initial')]
    migrate_to = [(app, '0002_auto_20220608_1559')]

    old_apps = migration.before(migrate_from)
    SAMLAuthenticator = old_apps.get_model(app, 'SAMLAuthenticator')

    new_apps = migration.apply(migrate_to)
    SAMLAuthenticator = new_apps.get_model(app, 'SAMLAuthenticator')
    assert not SAMLAuthenticator.objects.exists()


def test_saml_authenticator_data_migration_bad_settings(migration, settings):
    app = 'authentic2_auth_saml'
    migrate_from = [(app, '0001_initial')]
    migrate_to = [(app, '0002_auto_20220608_1559')]

    old_apps = migration.before(migrate_from)
    SAMLAuthenticator = old_apps.get_model(app, 'SAMLAuthenticator')

    settings.AUTH_FRONTENDS_KWARGS = {'saml': {'priority': None, 'show_condition': None}}
    settings.MELLON_METADATA_CACHE_TIME = 2**16
    settings.MELLON_METADATA_HTTP_TIMEOUT = -1
    settings.MELLON_PROVISION = None
    settings.MELLON_USERNAME_TEMPLATE = 42
    settings.MELLON_GROUP_ATTRIBUTE = None
    settings.MELLON_ERROR_URL = 'a' * 500
    settings.MELLON_AUTHN_CLASSREF = 'not-a-list'
    settings.MELLON_IDENTITY_PROVIDERS = [
        {
            'METADATA': os.path.join(os.path.dirname(__file__), 'metadata.xml'),
            'ERROR_REDIRECT_AFTER_TIMEOUT': -1,
            'SUPERUSER_MAPPING': 'not-a-dict',
        },
    ]

    new_apps = migration.apply(migrate_to)
    SAMLAuthenticator = new_apps.get_model(app, 'SAMLAuthenticator')
    authenticator = SAMLAuthenticator.objects.get()
    assert authenticator.slug == '0'
    assert authenticator.order == 3
    assert authenticator.show_condition == ''
    assert authenticator.enabled is False
    assert authenticator.metadata_cache_time == 3600
    assert authenticator.metadata_http_timeout == 10
    assert authenticator.provision is True
    assert authenticator.username_template == '{attributes[name_id_content]}@{realm}'
    assert authenticator.group_attribute == ''
    assert authenticator.error_url == 'a' * 200
    assert authenticator.error_redirect_after_timeout == 120
    assert authenticator.authn_classref == ''
    assert authenticator.superuser_mapping == {}


def test_saml_authenticator_data_migration_json_fields(migration, settings):
    migrate_from = [
        (
            'authentic2_auth_saml',
            '0005_addroleaction_renameattributeaction_samlattributelookup_setattributeaction',
        ),
        ('a2_rbac', '0036_delete_roleattribute'),
    ]
    migrate_to = [
        ('authentic2_auth_saml', '0006_migrate_jsonfields'),
        ('a2_rbac', '0036_delete_roleattribute'),
    ]

    old_apps = migration.before(migrate_from)
    SAMLAuthenticator = old_apps.get_model('authentic2_auth_saml', 'SAMLAuthenticator')
    Role = old_apps.get_model('a2_rbac', 'Role')
    OU = old_apps.get_model('a2_rbac', 'OrganizationalUnit')

    ou = OU.objects.create(name='Test OU', slug='test-ou')
    role = Role.objects.create(name='Test role', slug='test-role', ou=ou)

    SAMLAuthenticator.objects.create(
        metadata='meta1.xml',
        slug='idp1',
        lookup_by_attributes=[
            {'saml_attribute': 'email', 'user_field': 'email'},
            {'saml_attribute': 'saml_name', 'user_field': 'first_name', 'ignore-case': True},
        ],
        a2_attribute_mapping=[
            {
                'attribute': 'email',
                'saml_attribute': 'mail',
                'mandatory': True,
            },
            {'action': 'rename', 'from': 'a' * 1025, 'to': 'first_name'},
            {
                'attribute': 'first_name',
                'saml_attribute': 'first_name',
            },
            {
                'attribute': 'invalid',
                'saml_attribute': '',
            },
            {
                'attribute': 'invalid',
                'saml_attribute': None,
            },
            {
                'attribute': 'invalid',
            },
            {
                'action': 'add-role',
                'role': {
                    'name': role.name,
                    'ou': {
                        'name': role.ou.name,
                    },
                },
                'condition': "roles == 'A'",
            },
        ],
    )

    new_apps = migration.apply(migrate_to)
    SAMLAuthenticator = new_apps.get_model('authentic2_auth_saml', 'SAMLAuthenticator')
    authenticator = SAMLAuthenticator.objects.get()

    attribute_lookup1, attribute_lookup2 = authenticator.attribute_lookups.all().order_by('pk')
    assert attribute_lookup1.saml_attribute == 'email'
    assert attribute_lookup1.user_field == 'email'
    assert attribute_lookup1.ignore_case is False
    assert attribute_lookup2.saml_attribute == 'saml_name'
    assert attribute_lookup2.user_field == 'first_name'
    assert attribute_lookup2.ignore_case is True

    set_attribute1, set_attribute2 = authenticator.set_attribute_actions.all().order_by('pk')
    assert set_attribute1.attribute == 'email'
    assert set_attribute1.saml_attribute == 'mail'
    assert set_attribute1.mandatory is True
    assert set_attribute2.attribute == 'first_name'
    assert set_attribute2.saml_attribute == 'first_name'
    assert set_attribute2.mandatory is False

    rename_attribute = authenticator.rename_attribute_actions.get()
    assert rename_attribute.from_name == 'a' * 1024
    assert rename_attribute.to_name == 'first_name'

    add_role = authenticator.add_role_actions.get()
    assert add_role.role.pk == role.pk
    assert add_role.condition == "roles == 'A'"
    assert add_role.mandatory is False


def test_saml_authenticator_data_migration_json_fields_log_errors(migration, settings, caplog):
    migrate_from = [
        (
            'authentic2_auth_saml',
            '0005_addroleaction_renameattributeaction_samlattributelookup_setattributeaction',
        ),
        ('a2_rbac', '0036_delete_roleattribute'),
    ]
    migrate_to = [
        ('authentic2_auth_saml', '0006_migrate_jsonfields'),
        ('a2_rbac', '0036_delete_roleattribute'),
    ]

    old_apps = migration.before(migrate_from)
    SAMLAuthenticator = old_apps.get_model('authentic2_auth_saml', 'SAMLAuthenticator')

    SAMLAuthenticator.objects.create(
        metadata='meta1.xml',
        slug='idp1',
        lookup_by_attributes=[{'saml_attribute': 'email', 'user_field': 'email'}],
        a2_attribute_mapping=['bad'],
    )

    new_apps = migration.apply(migrate_to)
    SAMLAuthenticator = new_apps.get_model('authentic2_auth_saml', 'SAMLAuthenticator')

    authenticator = SAMLAuthenticator.objects.get()
    assert not authenticator.attribute_lookups.exists()

    assert caplog.messages == [
        'could not create related objects for authenticator SAMLAuthenticator object (%s)' % authenticator.pk,
        'attribute mapping for SAMLAuthenticator object (%s): ["bad"]' % authenticator.pk,
        'lookup by attributes for SAMLAuthenticator object (%s): [{"user_field": "email", "saml_attribute": "email"}]'
        % authenticator.pk,
    ]


def test_saml_authenticator_data_migration_rename_attributes(migration, settings):
    migrate_from = [('authentic2_auth_saml', '0008_auto_20220913_1105')]
    migrate_to = [('authentic2_auth_saml', '0009_statically_rename_attributes')]

    old_apps = migration.before(migrate_from)
    SAMLAuthenticator = old_apps.get_model('authentic2_auth_saml', 'SAMLAuthenticator')
    RenameAttributeAction = old_apps.get_model('authentic2_auth_saml', 'RenameAttributeAction')
    SetAttributeAction = old_apps.get_model('authentic2_auth_saml', 'SetAttributeAction')
    SAMLAttributeLookup = old_apps.get_model('authentic2_auth_saml', 'SAMLAttributeLookup')

    authenticator = SAMLAuthenticator.objects.create(slug='idp1')
    RenameAttributeAction.objects.create(
        authenticator=authenticator, from_name='http://nice/attribute/givenName', to_name='first_name'
    )
    SAMLAttributeLookup.objects.create(
        authenticator=authenticator, user_field='first_name', saml_attribute='first_name'
    )
    SAMLAttributeLookup.objects.create(
        authenticator=authenticator, user_field='title', saml_attribute='title'
    )
    SetAttributeAction.objects.create(
        authenticator=authenticator, user_field='first_name', saml_attribute='first_name'
    )
    SetAttributeAction.objects.create(authenticator=authenticator, user_field='title', saml_attribute='title')

    new_apps = migration.apply(migrate_to)
    SAMLAuthenticator = new_apps.get_model('authentic2_auth_saml', 'SAMLAuthenticator')
    authenticator = SAMLAuthenticator.objects.get()

    attribute_lookup1, attribute_lookup2 = authenticator.attribute_lookups.all().order_by('pk')
    assert attribute_lookup1.saml_attribute == 'http://nice/attribute/givenName'
    assert attribute_lookup1.user_field == 'first_name'
    assert attribute_lookup2.saml_attribute == 'title'
    assert attribute_lookup2.user_field == 'title'

    set_attribute1, set_attribute2 = authenticator.set_attribute_actions.all().order_by('pk')
    assert set_attribute1.saml_attribute == 'http://nice/attribute/givenName'
    assert set_attribute1.user_field == 'first_name'
    assert set_attribute2.saml_attribute == 'title'
    assert set_attribute2.user_field == 'title'


def test_saml_authenticator_data_migration_metadata_file_to_db(migration, settings):
    migrate_from = [('authentic2_auth_saml', '0012_move_add_role_action')]
    migrate_to = [('authentic2_auth_saml', '0014_remove_samlauthenticator_metadata_path')]

    old_apps = migration.before(migrate_from)
    SAMLAuthenticator = old_apps.get_model('authentic2_auth_saml', 'SAMLAuthenticator')

    SAMLAuthenticator.objects.create(
        slug='idp1', metadata_path=os.path.join(os.path.dirname(__file__), 'metadata.xml')
    )

    SAMLAuthenticator.objects.create(slug='idp2', metadata='xxx')
    SAMLAuthenticator.objects.create(slug='idp3', metadata_url='https://example.com')
    SAMLAuthenticator.objects.create(slug='idp4', metadata_path='/unknown/')

    new_apps = migration.apply(migrate_to)
    SAMLAuthenticator = new_apps.get_model('authentic2_auth_saml', 'SAMLAuthenticator')

    authenticator = SAMLAuthenticator.objects.get(slug='idp1')
    assert authenticator.metadata.startswith('<?xml version="1.0"?>')
    assert authenticator.metadata.endswith('</EntityDescriptor>\n')

    assert SAMLAuthenticator.objects.filter(slug='idp2', metadata='xxx').count() == 1
    assert SAMLAuthenticator.objects.filter(slug='idp3', metadata_url='https://example.com').count() == 1
