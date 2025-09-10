import json
import os
import shutil
import tempfile
import uuid

import pytest
from django.core.management import call_command

from wcs.api_utils import sign_url
from wcs.ctl.management.commands.hobo_notify import Command as HoboNotifyCommand
from wcs.qommon import force_str
from wcs.qommon.afterjobs import AfterJob
from wcs.qommon.http_request import HTTPRequest
from wcs.sql_criterias import NotNull

from .utilities import create_temporary_pub, get_app


@pytest.fixture
def alt_tempdir():
    alt_tempdir = tempfile.mkdtemp()
    yield alt_tempdir
    shutil.rmtree(alt_tempdir)


@pytest.fixture
def pub(request):
    pub = create_temporary_pub()

    req = HTTPRequest(None, {'SCRIPT_NAME': '/', 'SERVER_NAME': 'example.net'})
    pub.set_app_dir(req)
    pub.cfg['sp'] = {'saml2_providerid': 'test'}
    pub.write_cfg()

    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        fd.write(
            '''\
[api-secrets]
coucou = 1234
'''
        )

    pub.role_class.wipe()
    r = pub.role_class(name='Service étt civil')
    r.slug = 'service-ett-civil'
    r.allows_backoffice_access = False
    r.store()

    return pub


def test_process_notification_role_wrong_audience(pub):
    notification = {
        '@type': 'provision',
        'audience': ['coin'],
        'full': True,
        'objects': {
            '@type': 'role',
            'data': [
                {
                    'name': 'Service enfance',
                    'slug': 'service-enfance',
                    'description': 'Rôle du service petite enfance',
                    'uuid': str(uuid.uuid4()),
                    'emails': ['petite-enfance@example.com'],
                    'emails_to_members': False,
                },
                {
                    '@type': 'role',
                    'name': 'Service état civil',
                    'slug': 'service-etat-civil',
                    'description': 'Rôle du service état civil',
                    'uuid': str(uuid.uuid4()),
                    'emails': ['etat-civil@example.com'],
                    'emails_to_members': True,
                },
            ],
        },
    }
    assert pub.role_class.count() == 1
    assert pub.role_class.select()[0].name == 'Service étt civil'
    assert pub.role_class.select()[0].slug == 'service-ett-civil'
    assert pub.role_class.select()[0].details is None
    assert pub.role_class.select()[0].emails is None
    assert pub.role_class.select()[0].emails_to_members is False
    assert pub.role_class.select()[0].allows_backoffice_access is False
    HoboNotifyCommand().process_notification(notification)
    assert pub.role_class.count() == 1
    assert pub.role_class.select()[0].name == 'Service étt civil'
    assert pub.role_class.select()[0].slug == 'service-ett-civil'
    assert pub.role_class.select()[0].details is None
    assert pub.role_class.select()[0].emails is None
    assert pub.role_class.select()[0].emails_to_members is False
    assert pub.role_class.select()[0].allows_backoffice_access is False


def test_process_notification_role(pub):
    uuid1 = str(uuid.uuid4())
    uuid2 = str(uuid.uuid4())
    notification = {
        '@type': 'provision',
        'audience': ['test'],
        'full': True,
        'objects': {
            '@type': 'role',
            'data': [
                {
                    'name': 'Service enfance',
                    'slug': 'service-enfance',
                    'details': 'Rôle du service petite enfance',
                    'uuid': uuid1,
                    'emails': ['petite-enfance@example.com'],
                    'emails_to_members': False,
                },
                {
                    'name': 'Service état civil',
                    'slug': 'service-ett-civil',
                    'details': 'Rôle du service état civil',
                    'uuid': uuid2,
                    'emails': ['etat-civil@example.com'],
                    'emails_to_members': True,
                },
            ],
        },
    }
    assert pub.role_class.count() == 1
    assert pub.role_class.select()[0].name == 'Service étt civil'
    assert pub.role_class.select()[0].slug == 'service-ett-civil'
    assert pub.role_class.select()[0].details is None
    assert pub.role_class.select()[0].emails is None
    assert pub.role_class.select()[0].emails_to_members is False
    assert pub.role_class.select()[0].allows_backoffice_access is False
    existing_role_id = pub.role_class.select()[0].id
    HoboNotifyCommand().process_notification(notification)
    assert pub.role_class.count() == 2
    old_role = pub.role_class.get(existing_role_id)
    assert old_role.name == 'Service état civil'
    assert old_role.uuid == uuid2
    assert old_role.slug == 'service-ett-civil'
    assert old_role.details == 'Rôle du service état civil'
    assert old_role.emails == ['etat-civil@example.com']
    assert old_role.emails_to_members is True
    assert old_role.allows_backoffice_access is False
    new_role = pub.role_class.get_on_index(uuid1, 'uuid')
    assert new_role.id == uuid1
    assert new_role.name == 'Service enfance'
    assert new_role.slug == 'service-enfance'
    assert new_role.uuid == uuid1
    assert new_role.details == 'Rôle du service petite enfance'
    assert new_role.emails == ['petite-enfance@example.com']
    assert new_role.emails_to_members is False
    assert new_role.allows_backoffice_access is False
    notification = {
        '@type': 'provision',
        'audience': ['test'],
        'full': True,
        'objects': {
            '@type': 'role',
            'data': [
                {
                    '@type': 'role',
                    'name': 'Service enfance',
                    'slug': 'service-enfance',
                    'description': 'Rôle du service petite enfance',
                    'uuid': uuid1,
                    'emails': ['petite-enfance@example.com'],
                    'emails_to_members': True,
                },
            ],
        },
    }
    HoboNotifyCommand().process_notification(notification)
    assert pub.role_class.count() == 1
    assert pub.role_class.select()[0].id == new_role.id
    assert pub.role_class.select()[0].uuid == uuid1
    assert pub.role_class.select()[0].name == 'Service enfance'
    assert pub.role_class.select()[0].slug == 'service-enfance'
    assert pub.role_class.select()[0].details is None
    assert pub.role_class.select()[0].emails == ['petite-enfance@example.com']
    assert pub.role_class.select()[0].emails_to_members is True
    assert pub.role_class.select()[0].allows_backoffice_access is False

    role = pub.role_class.select()[0]
    role.allows_backoffice_access = True
    role.store()

    HoboNotifyCommand().process_notification(notification)
    assert pub.role_class.count() == 1
    pub.role_class.select()[0].refresh_from_storage()
    assert pub.role_class.select()[0].name == 'Service enfance'
    assert pub.role_class.select()[0].slug == 'service-enfance'
    assert pub.role_class.select()[0].allows_backoffice_access is True


def test_process_notification_internal_role(pub):
    pub.role_class.wipe()

    notification = {
        '@type': 'provision',
        'audience': ['test'],
        'full': True,
        'objects': {
            '@type': 'role',
            'data': [
                {
                    'name': 'Service enfance',
                    'slug': '_service-enfance',
                    'details': 'Rôle du service petite enfance',
                    'uuid': str(uuid.uuid4()),
                    'emails': ['petite-enfance@example.com'],
                    'emails_to_members': False,
                },
            ],
        },
    }
    HoboNotifyCommand().process_notification(notification)
    assert pub.role_class.count() == 1
    role = pub.role_class.select()[0]
    assert role.is_internal()
    assert role.allows_backoffice_access is False


def test_process_notification_role_description(pub):
    # check descriptions are not used to fill role.details
    uuid1 = str(uuid.uuid4())
    uuid2 = str(uuid.uuid4())
    notification = {
        '@type': 'provision',
        'audience': ['test'],
        'full': True,
        'objects': {
            '@type': 'role',
            'data': [
                {
                    'name': 'Service enfance',
                    'slug': 'service-enfance',
                    'description': 'Rôle du service petite enfance',
                    'uuid': uuid1,
                    'emails': ['petite-enfance@example.com'],
                    'emails_to_members': False,
                },
                {
                    'name': 'Service état civil',
                    'slug': 'service-ett-civil',
                    'description': 'Rôle du service état civil',
                    'uuid': uuid2,
                    'emails': ['etat-civil@example.com'],
                    'emails_to_members': True,
                },
            ],
        },
    }
    assert pub.role_class.count() == 1
    assert pub.role_class.select()[0].name == 'Service étt civil'
    assert pub.role_class.select()[0].slug == 'service-ett-civil'
    assert pub.role_class.select()[0].details is None
    assert pub.role_class.select()[0].emails is None
    assert pub.role_class.select()[0].emails_to_members is False
    existing_role_id = pub.role_class.select()[0].id
    HoboNotifyCommand().process_notification(notification)
    assert pub.role_class.count() == 2
    old_role = pub.role_class.get(existing_role_id)
    assert old_role.name == 'Service état civil'
    assert old_role.slug == 'service-ett-civil'
    assert old_role.uuid == uuid2
    assert old_role.details is None
    assert old_role.emails == ['etat-civil@example.com']
    assert old_role.emails_to_members is True
    new_role = pub.role_class.get_on_index(uuid1, 'uuid')
    assert new_role.name == 'Service enfance'
    assert new_role.slug == 'service-enfance'
    assert new_role.uuid == uuid1
    assert new_role.details is None
    assert new_role.emails == ['petite-enfance@example.com']
    assert new_role.emails_to_members is False
    notification = {
        '@type': 'provision',
        'audience': ['test'],
        'full': True,
        'objects': {
            '@type': 'role',
            'data': [
                {
                    '@type': 'role',
                    'name': 'Service enfance',
                    'slug': 'service-enfance',
                    'description': 'Rôle du service petite enfance',
                    'uuid': uuid1,
                    'emails': ['petite-enfance@example.com'],
                    'emails_to_members': True,
                },
            ],
        },
    }
    HoboNotifyCommand().process_notification(notification)
    assert pub.role_class.count() == 1
    assert pub.role_class.select()[0].id == new_role.id
    assert pub.role_class.select()[0].name == 'Service enfance'
    assert pub.role_class.select()[0].uuid == uuid1
    assert pub.role_class.select()[0].slug == 'service-enfance'
    assert pub.role_class.select()[0].details is None
    assert pub.role_class.select()[0].emails == ['petite-enfance@example.com']
    assert pub.role_class.select()[0].emails_to_members is True


def test_process_notification_role_deprovision(pub):
    uuid1 = str(uuid.uuid4())
    notification = {
        '@type': 'deprovision',
        'audience': ['test'],
        'full': False,
        'objects': {
            '@type': 'role',
            'data': [
                {
                    '@type': 'role',
                    'uuid': uuid1,
                },
            ],
        },
    }
    role = pub.role_class.select()[0]
    role.remove_self()
    assert role.name == 'Service étt civil'
    assert role.slug == 'service-ett-civil'
    role.id = uuid1
    role.store()

    role = pub.role_class('foo')
    role.slug = 'bar'
    role.store()

    assert pub.role_class.count() == 2
    HoboNotifyCommand().process_notification(notification)
    assert pub.role_class.count() == 1
    assert pub.role_class.select()[0].slug == 'bar'

    r = pub.role_class(name='Service étt civil')
    r.uuid = uuid1
    r.store()
    assert pub.role_class.count() == 2
    HoboNotifyCommand().process_notification(notification)
    assert pub.role_class.count() == 1
    assert pub.role_class.select()[0].slug == 'bar'


PROFILE = {
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
            'kind': 'string',
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
            'kind': 'string',
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
            'kind': 'birthdate',
            'description': '',
            'required': False,
            'user_visible': True,
            'label': 'Date de naissance',
            'disabled': False,
            'user_editable': True,
            'asked_on_registration': False,
            'name': 'birthdate',
        },
    ]
}


def test_process_notification_user_provision(pub):
    User = pub.user_class
    User.wipe()

    # create some roles
    from wcs.ctl.management.commands.hobo_deploy import Command

    # setup an hobo profile
    assert 'users' not in pub.cfg
    Command().update_profile(PROFILE, pub)
    assert pub.cfg['users']['field_phone'] == '_phone'

    uuid1 = str(uuid.uuid4())
    uuid2 = str(uuid.uuid4())
    notification = {
        '@type': 'provision',
        'audience': ['test'],
        'full': True,
        'objects': {
            '@type': 'role',
            'data': [
                {
                    'name': 'Service enfance',
                    'slug': 'service-enfance',
                    'description': 'Rôle du service petite enfance',
                    'uuid': uuid1,
                    'emails': ['petite-enfance@example.com'],
                    'emails_to_members': False,
                },
                {
                    'name': 'Service état civil',
                    'slug': 'service-ett-civil',
                    'description': 'Rôle du service état civil',
                    'uuid': uuid2,
                    'emails': ['etat-civil@example.com'],
                    'emails_to_members': True,
                },
            ],
        },
    }
    assert pub.role_class.count() == 1
    assert pub.role_class.select()[0].name == 'Service étt civil'
    assert pub.role_class.select()[0].slug == 'service-ett-civil'
    assert pub.role_class.select()[0].details is None
    assert pub.role_class.select()[0].emails is None
    assert pub.role_class.select()[0].emails_to_members is False
    existing_role_id = pub.role_class.select()[0].id
    HoboNotifyCommand().process_notification(notification)
    assert pub.role_class.count() == 2
    old_role = pub.role_class.get(existing_role_id)
    assert old_role.name == 'Service état civil'
    assert old_role.uuid == uuid2
    assert old_role.slug == 'service-ett-civil'
    assert old_role.details is None
    assert old_role.emails == ['etat-civil@example.com']
    assert old_role.emails_to_members is True
    new_role = pub.role_class.get_on_index(uuid1, 'uuid')
    assert new_role.name == 'Service enfance'
    assert new_role.slug == 'service-enfance'
    assert new_role.uuid == uuid1
    assert new_role.details is None
    assert new_role.emails == ['petite-enfance@example.com']
    assert new_role.emails_to_members is False

    notification = {
        '@type': 'provision',
        'issuer': 'http://idp.example.net/idp/saml/metadata',
        'audience': ['test'],
        'objects': {
            '@type': 'user',
            'data': [
                {
                    'uuid': 'a' * 32,
                    'first_name': 'John',
                    'last_name': 'Doé',
                    'email': 'john.doe@example.net',
                    'phone': '+33123456789',
                    'zipcode': '13400',
                    'is_superuser': False,
                    'is_active': True,
                    'roles': [
                        {
                            'uuid': uuid1,
                            'name': 'Service petite enfance',
                            'description': 'etc.',
                        },
                        {
                            'uuid': uuid2,
                            'name': 'Service état civil',
                            'description': 'etc.',
                        },
                    ],
                }
            ],
        },
    }
    HoboNotifyCommand().process_notification(notification)
    assert User.count() == 1
    user = User.select()[0]
    assert user.form_data is not None
    assert user.form_data['_email'] == 'john.doe@example.net'
    assert user.email == 'john.doe@example.net'
    assert user.form_data['_phone'] == '+33123456789'
    assert user.form_data['_first_name'] == 'John'
    assert user.form_data['_last_name'] == force_str('Doé')
    assert user.form_data['_zipcode'] == '13400'
    assert user.form_data['_birthdate'] is None
    assert user.name_identifiers == ['a' * 32]
    assert user.is_admin is False
    assert user.is_active is True
    assert set(user.roles) == {new_role.id, old_role.id}

    notification = {
        '@type': 'provision',
        'issuer': 'http://idp.example.net/idp/saml/metadata',
        'audience': ['test'],
        'objects': {
            '@type': 'user',
            'data': [
                {
                    'uuid': 'a' * 32,
                    'first_name': 'John',
                    'last_name': 'Doe',
                    'email': 'john.doe@example.net',
                    'zipcode': '13600',
                    'birthdate': '2000-01-01',
                    'is_superuser': True,
                    'is_active': False,
                    'roles': [
                        {
                            'uuid': uuid2,
                            'name': 'Service état civil',
                            'description': 'etc.',
                        },
                        {
                            'uuid': str(uuid.uuid4()),
                            'name': 'Service enfance',
                            'description': '',
                        },
                    ],
                }
            ],
        },
    }
    HoboNotifyCommand().process_notification(notification)
    assert User.count() == 1
    user = User.select()[0]
    assert user.form_data is not None
    assert user.form_data['_email'] == 'john.doe@example.net'
    assert user.email == 'john.doe@example.net'
    assert user.form_data['_first_name'] == 'John'
    assert user.form_data['_last_name'] == 'Doe'
    assert user.form_data['_zipcode'] == '13600'
    assert user.form_data['_birthdate'].tm_year == 2000
    assert user.name_identifiers == ['a' * 32]
    assert user.is_admin is True
    assert user.is_active is False
    assert set(user.roles) == {old_role.id}

    for birthdate in ('baddate', '', None):
        notification = {
            '@type': 'provision',
            'issuer': 'http://idp.example.net/idp/saml/metadata',
            'audience': ['test'],
            'objects': {
                '@type': 'user',
                'data': [
                    {
                        'uuid': 'a' * 32,
                        'first_name': 'John',
                        'last_name': 'Doe',
                        'email': 'john.doe@example.net',
                        'zipcode': '13600',
                        'birthdate': birthdate,
                        'is_superuser': True,
                        'roles': [
                            {
                                'uuid': uuid2,
                                'name': 'Service état civil',
                                'description': 'etc.',
                            },
                        ],
                    }
                ],
            },
        }
        HoboNotifyCommand().process_notification(notification)
        assert User.count() == 1
        if birthdate not in (None, ''):  # wrong value : no nothing
            assert User.select()[0].form_data['_birthdate'].tm_year == 2000
        else:  # empty value : empty field
            assert User.select()[0].form_data['_birthdate'] is None

    # check provisionning a count with no email works
    for no_email in (None, ''):
        User.wipe()
        notification = {
            '@type': 'provision',
            'issuer': 'http://idp.example.net/idp/saml/metadata',
            'audience': ['test'],
            'objects': {
                '@type': 'user',
                'data': [
                    {
                        'uuid': 'a' * 32,
                        'first_name': 'John',
                        'last_name': 'Doé',
                        'email': no_email,
                        'zipcode': '13400',
                        'is_superuser': False,
                        'roles': [
                            {
                                'uuid': uuid1,
                                'name': 'Service petite enfance',
                                'description': 'etc.',
                            },
                            {
                                'uuid': uuid2,
                                'name': 'Service état civil',
                                'description': 'etc.',
                            },
                        ],
                    }
                ],
            },
        }
        HoboNotifyCommand().process_notification(notification)
        assert User.count() == 1
        assert User.select()[0].first_name == 'John'
        assert not User.select()[0].email

    # check provisionning an account with no phone works
    for no_phone in (None, ''):
        User.wipe()
        notification = {
            '@type': 'provision',
            'issuer': 'http://idp.example.net/idp/saml/metadata',
            'audience': ['test'],
            'objects': {
                '@type': 'user',
                'data': [
                    {
                        'uuid': 'a' * 32,
                        'first_name': 'John',
                        'last_name': 'Doé',
                        'email': 'john.doe@example.net',
                        'phone': no_phone,
                        'zipcode': '13400',
                        'is_superuser': False,
                        'roles': [
                            {
                                'uuid': uuid1,
                                'name': 'Service petite enfance',
                                'description': 'etc.',
                            },
                            {
                                'uuid': uuid2,
                                'name': 'Service état civil',
                                'description': 'etc.',
                            },
                        ],
                    }
                ],
            },
        }
        HoboNotifyCommand().process_notification(notification)
        assert User.count() == 1
        assert User.select()[0].first_name == 'John'
        assert not User.select()[0].phone


def record_error(exception=None, *args, **kwargs):
    if exception:
        raise exception


def test_process_notification_user_with_errors(pub):
    User = pub.user_class

    # setup an hobo profile
    from wcs.ctl.management.commands.hobo_deploy import Command

    User.wipe()
    pub.role_class.wipe()
    Command().update_profile(PROFILE, pub)

    notification = {
        '@type': 'provision',
        'issuer': 'http://idp.example.net/idp/saml/metadata',
        'audience': ['test'],
        'full': True,
        'objects': {
            '@type': 'user',
            'data': [
                {
                    'uuid': 'a' * 32,
                    'first_name': 'John',
                    'last_name': 'Doe',
                    'email': 'john.doe@example.net',
                    'zipcode': '13400',
                    'is_superuser': False,
                    'roles': [
                        {
                            'uuid': 'xyz',
                            'name': 'Service état civil',
                            'description': 'etc.',
                        },
                    ],
                }
            ],
        },
    }
    with pytest.raises(NotImplementedError) as e:
        HoboNotifyCommand().process_notification(notification)
    assert e.value.args == ('full is not supported for users',)
    assert User.count() == 0

    notification['full'] = False

    pub.record_error = record_error

    for key in ('uuid', 'first_name', 'last_name', 'email'):
        backup = notification['objects']['data'][0][key]
        del notification['objects']['data'][0][key]
        with pytest.raises(Exception) as e:
            HoboNotifyCommand().process_notification(notification)
        assert e.type == ValueError
        assert e.value.args == ('invalid user',)
        assert User.count() == 0
        notification['objects']['data'][0][key] = backup

    notification['@type'] = 'deprovision'
    del notification['objects']['data'][0]['uuid']
    with pytest.raises(Exception) as e:
        HoboNotifyCommand().process_notification(notification)
    assert e.type == KeyError
    assert e.value.args == ('user without uuid',)


def test_process_notification_role_with_errors(pub):
    User = pub.user_class
    User.wipe()
    pub.role_class.wipe()
    notification = {
        '@type': 'provision',
        'audience': ['test'],
        'full': True,
        'objects': {
            '@type': 'role',
            'data': [
                {
                    'name': 'Service enfance',
                    'slug': 'service-enfance',
                    'details': 'Rôle du service petite enfance',
                    # 'uuid': u'12345',
                    'emails': ['petite-enfance@example.com'],
                    'emails_to_members': False,
                }
            ],
        },
    }
    with pytest.raises(KeyError) as e:
        HoboNotifyCommand().process_notification(notification)
    assert e.value.args == ('role without uuid',)
    assert pub.role_class.count() == 0

    notification['objects']['data'][0]['uuid'] = '12345'
    del notification['objects']['data'][0]['name']
    with pytest.raises(ValueError) as e:
        HoboNotifyCommand().process_notification(notification)
    assert e.value.args == ('invalid role',)
    assert pub.role_class.count() == 0


def test_process_user_deprovision(pub):
    User = pub.user_class

    # setup an hobo profile
    from wcs.ctl.management.commands.hobo_deploy import Command

    User.wipe()
    pub.role_class.wipe()
    Command().update_profile(PROFILE, pub)

    user = User()
    user.name = 'Pierre'
    user.name_identifiers = ['a' * 32]
    user.store()

    notification = {
        '@type': 'deprovision',
        'issuer': 'http://idp.example.net/idp/saml/metadata',
        'audience': ['test'],
        'objects': {
            '@type': 'user',
            'data': [
                {
                    'uuid': 'a' * 32,
                }
            ],
        },
    }

    assert User.count() == 1
    assert len(User.select([NotNull('deleted_timestamp')])) == 0
    HoboNotifyCommand().process_notification(notification)
    assert User.count() == 1
    assert len(User.select([NotNull('deleted_timestamp')])) == 1


def test_process_user_deprovision_with_data(pub):
    from wcs.formdef import FormDef

    User = pub.user_class

    # setup an hobo profile
    from wcs.ctl.management.commands.hobo_deploy import Command

    User.wipe()
    FormDef.wipe()
    Command().update_profile(PROFILE, pub)

    user = User()
    user.name = 'Pierre'
    user.name_identifiers = ['a' * 32]
    user.store()

    formdef = FormDef()
    formdef.name = 'foobar'
    formdef.url_name = 'foobar'
    formdef.fields = []
    formdef.store()
    data_class = formdef.data_class()

    formdata = data_class()
    formdata.user_id = user.id
    formdata.store()

    notification = {
        '@type': 'deprovision',
        'issuer': 'http://idp.example.net/idp/saml/metadata',
        'audience': ['test'],
        'objects': {
            '@type': 'user',
            'data': [
                {
                    'uuid': 'a' * 32,
                }
            ],
        },
    }

    assert User.count() == 1
    assert len(User.select([NotNull('deleted_timestamp')])) == 0
    HoboNotifyCommand().process_notification(notification)
    assert User.count() == 1
    assert len(User.select([NotNull('deleted_timestamp')])) == 1


def test_provision_http_endpoint(pub):
    get_app(pub).get('/__provision__/', status=403)

    get_app(pub).get(sign_url('/__provision__/?orig=coucou', '1234'), status=400)

    notification = {
        '@type': 'provision',
        'issuer': 'http://idp.example.net/idp/saml/metadata',
        'audience': ['test'],
        'objects': {
            '@type': 'user',
            'data': [
                {
                    'uuid': 'a' * 32,
                    'first_name': 'John',
                    'last_name': 'Doé',
                    'email': 'john.doe@example.net',
                    'zipcode': '13400',
                    'is_superuser': False,
                    'is_active': True,
                    'roles': [],
                }
            ],
        },
    }

    AfterJob.wipe()
    pub.user_class.wipe()
    get_app(pub).post_json(sign_url('/__provision__/?orig=coucou', '1234'), notification)
    assert AfterJob.count() == 1  # async by default
    assert pub.user_class.count() == 1

    AfterJob.wipe()
    pub.user_class.wipe()
    get_app(pub).post_json(sign_url('/__provision__/?orig=coucou&sync=1', '1234'), notification)
    assert AfterJob.count() == 0  # sync
    assert pub.user_class.count() == 1


def test_hobo_notify_call_command(pub, alt_tempdir):
    uuid1 = str(uuid.uuid4())
    with open(os.path.join(alt_tempdir, 'message.json'), 'w') as fd:
        json.dump(
            {
                '@type': 'deprovision',
                'audience': ['test'],
                'full': False,
                'objects': {
                    '@type': 'role',
                    'data': [
                        {
                            '@type': 'role',
                            'uuid': uuid1,
                        },
                    ],
                },
            },
            fd,
        )

    pub.role_class.wipe()
    role = pub.role_class('foo')
    role.id = uuid1
    role.store()
    call_command('hobo_notify', os.path.join(alt_tempdir, 'message.json'))
    assert pub.role_class.count() == 0
