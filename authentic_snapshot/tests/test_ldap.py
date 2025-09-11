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

import base64
import json
import logging
import os
import time
import urllib.parse
from unittest import mock

import ldap
import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core import mail, management
from django.core.exceptions import ImproperlyConfigured
from django.db.models.query import QuerySet
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_bytes, force_str
from ldap.dn import escape_dn_chars
from ldaptools.slapd import Slapd, has_slapd

from authentic2 import models
from authentic2.a2_rbac.models import OrganizationalUnit, Role
from authentic2.a2_rbac.utils import get_default_ou
from authentic2.apps.authenticators.models import LoginPasswordAuthenticator
from authentic2.backends import ldap_backend
from authentic2.backends.ldap_backend import LDAPObject
from authentic2.ldap_utils import DnFormatter, FilterFormatter
from authentic2.models import Service
from authentic2.utils import crypto, switch_user
from authentic2.utils.misc import PasswordChangeError, authenticate

from . import utils

User = get_user_model()

pytestmark = pytest.mark.skipif(not has_slapd(), reason='slapd is not installed')

USERNAME = 'etienne.michu'
UID = USERNAME
CN = 'Étienne Michu'
DN = 'cn=%s,o=ôrga' % escape_dn_chars(CN)
PASS = 'Passé1234'
UPASS = 'Passé1234'
EMAIL = 'etienne.michu@example.net'
CARLICENSE = '123445ABC'
UUID = '8ff2f34a-4a36-103c-8d0a-e3a0333484d3'
OBJECTGUID_RAW = b'\xab' * 16
OBJECTGUID_B64 = base64.b64encode(OBJECTGUID_RAW).decode()

CN_INCOMPLETE = 'Jean Dupond'
DN_INCOMPLETE = 'cn=%s,o=ôrga' % escape_dn_chars(CN_INCOMPLETE)

EO_O = 'EO'
EO_STREET = '169 rue du Chateau'
EO_POSTALCODE = '75014'
EO_CITY = 'PARIS'

EE_O = 'EE'
EE_STREET = "44 rue de l'Ouest"
EE_POSTALCODE = '75014'
EE_CITY = 'PARIS'

base_dir = os.path.dirname(__file__)
key_file = os.path.join(base_dir, 'key.pem')
cert_file = os.path.join(base_dir, 'cert.pem')
wrong_cert_file = os.path.join(base_dir, 'wrongcert.pem')


@pytest.fixture
def slapd():
    with create_slapd() as s:
        yield s


OBJECTGUID_SCHEMA = '''\
dn: cn=objectguid,cn=schema,cn=config
objectClass: olcSchemaConfig
cn: objectguid
olcAttributeTypes: ( 1.3.6.1.4.1.36560.1.1.3.2 NAME 'objectGUID'
  SYNTAX 1.3.6.1.4.1.1466.115.121.1.40 )
olcObjectClasses: ( 1.3.6.1.4.1.36560.1.1.3.1 NAME 'objectWithObjectGuid'
  MAY ( objectGUID )
  SUP top AUXILIARY )
'''


@pytest.fixture
def slapd_ppolicy():
    with create_slapd() as slapd:
        conn = slapd.get_connection_admin()
        assert conn.protocol_version == ldap.VERSION3
        conn.modify_s('cn=module{0},cn=config', [(ldap.MOD_ADD, 'olcModuleLoad', [force_bytes('ppolicy')])])
        try:
            with open('/etc/ldap/schema/ppolicy.ldif') as fd:
                slapd.add_ldif(fd.read())
        except FileNotFoundError:
            # most likely due to a newer openldap version where ppolicy is not in a separate ldif
            # schema anymore. let's try to go on from here onwards
            pass
        # Replace global manage right by write one, to be sure ppolicy's restrictions will not be overwritten
        conn.modify_s(
            'olcDatabase={2}mdb,cn=config',
            [(ldap.MOD_REPLACE, 'olcAccess', [force_bytes('{0}to * by * write')])],
        )
        slapd.add_ldif(
            '''
dn: olcOverlay={0}ppolicy,olcDatabase={2}mdb,cn=config
objectclass: olcOverlayConfig
objectclass: olcPPolicyConfig
olcoverlay: {0}ppolicy
olcppolicydefault: cn=default,ou=ppolicies,o=ôrga
olcppolicyforwardupdates: FALSE
olcppolicyhashcleartext: TRUE
olcppolicyuselockout: TRUE
'''
        )

        slapd.add_ldif(
            '''
dn: ou=ppolicies,o=ôrga
objectclass: organizationalUnit
ou: ppolicies
'''
        )
        yield slapd


@pytest.fixture
def tls_slapd():
    tcp_port = utils.find_free_tcp_port()
    with Slapd(ldap_url='ldap://localhost.entrouvert.org:%s' % tcp_port, tls=(key_file, cert_file)) as s:
        yield create_slapd(s)


def create_slapd(slapd=None):
    slapd = slapd or Slapd()
    slapd.add_db('o=ôrga')
    slapd.add_ldif(
        '''dn: o=ôrga
objectClass: organization
o: ôrga

dn: {dn}
objectClass: inetOrgPerson
userPassword: {password}
uid: {uid}
cn: Étienne Michu
sn: Michu
gn: Étienne
l: Paris
mail: {email}
jpegPhoto:: ACOE
carLicense: {cl}
o: EO
o: EE
# memberOf is not defined on OpenLDAP so we use street for storing DN like
# memberOf values
strEET: cn=group2,o=ôrga

dn: {dn_incomplete}
objectClass: inetOrgPerson
userPassword: {password}
# account is incomplete, uid missing
cn: {cn_incomplete}
sn: Dupond
gn: Jean
l: Paris
mail: jean.dupond@example.net
jpegPhoto:: ACOE
carLicense: {cl}
o: EO
o: EE
strEET: cn=group2,o=ôrga

dn: cn=GRoup1,o=ôrga
objectClass: groupOfNames
cn: GrOuP1
member: {dn}

dn: o={eo_o},o=ôrga
objectClass: organization
o: {eo_o}
postalAddress: {eo_street}
postalCode: {eo_postalcode}
l: {eo_city}

dn: o={ee_o},o=ôrga
objectClass: organization
o: {ee_o}
postalAddress: {ee_street}
postalCode: {ee_postalcode}
l: {ee_city}

'''.format(
            dn=DN,
            uid=UID,
            email=EMAIL,
            password=PASS,
            cl=CARLICENSE,
            dn_incomplete=DN_INCOMPLETE,
            cn_incomplete=CN_INCOMPLETE,
            eo_o=EO_O,
            eo_street=EO_STREET,
            eo_postalcode=EO_POSTALCODE,
            eo_city=EO_CITY,
            ee_o=EE_O,
            ee_street=EE_STREET,
            ee_postalcode=EE_POSTALCODE,
            ee_city=EE_CITY,
        )
    )
    for i in range(5):
        slapd.add_ldif(
            '''dn: uid=mïchu{i},o=ôrga
objectClass: inetOrgPerson
userPassword: {password}
uid: mïchu{i}
cn: Étienne Michu
sn: Michu
gn: Étienne
l: locality{i}
mail: etienne.michu{i}@example.net

'''.format(
                i=i, password=PASS
            )
        )
    group_ldif = '''dn: cn=group2,o=ôrga
gidNumber: 10
objectClass: posixGroup
memberUid: {uid}
'''.format(
        uid=UID
    )
    group_ldif += '\n\n'
    slapd.add_ldif(group_ldif)
    return slapd


@pytest.fixture
def wraps_ldap_set_option(monkeypatch):
    mock_set_option = mock.Mock()

    old_set_option = LDAPObject.set_option

    def set_option(self, *args, **kwargs):
        mock_set_option(*args, **kwargs)
        return old_set_option(self, *args, **kwargs)

    monkeypatch.setattr('authentic2.backends.ldap_backend.LDAPObject.set_option', set_option)
    return mock_set_option


def test_connection(slapd):
    conn = slapd.get_connection()
    conn.simple_bind_s(DN, PASS)


def test_connection_timeout_options(slapd, wraps_ldap_set_option, db, settings):
    settings.LDAP_AUTH_SETTINGS = [
        {
            'url': [slapd.ldap_url],
            'basedn': 'o=ôrga',
            'bindsasl': (),
            'binddn': force_str(DN),
            'bindpw': PASS,
            'global_ldap_options': {},
            'require_cert': 'demand',
            'cacertfile': '',
            'cacertdir': '',
            'certfile': cert_file,
            'keyfile': key_file,
            'use_tls': False,
            'referrals': False,
            'ldap_options': {},
            'connect_with_user_credentials': True,
            # relevant options here:
            'timeout': 10,
            'sync_timeout': 20,
        }
    ]
    ldap_backend.LDAPBackend.get_connection(settings.LDAP_AUTH_SETTINGS[0])
    timeout_set = False
    network_timeout_set = False
    for call_args in wraps_ldap_set_option.call_args_list:
        if call_args.args[0] == 20482:  # OPT_TIMEOUT
            assert call_args.args[1] == 10
            timeout_set = True
        if call_args.args[0] == 20485:  # OPT_NETWORK_TIMEOUT
            assert call_args.args[1] == 10
            network_timeout_set = True
    assert timeout_set
    assert network_timeout_set

    wraps_ldap_set_option.reset_mock()

    dummy = [user for user in ldap_backend.LDAPBackend.get_users()]
    timeout_set = False
    network_timeout_set = False
    for call_args in wraps_ldap_set_option.call_args_list:
        if call_args.args[0] == 20482:  # OPT_TIMEOUT
            assert call_args.args[1] == 20
            timeout_set = True
        if call_args.args[0] == 20485:  # OPT_NETWORK_TIMEOUT
            assert call_args.args[1] == 20
            network_timeout_set = True
    assert timeout_set
    assert network_timeout_set


def test_simple(slapd, settings, client, transactional_db):
    settings.LDAP_AUTH_SETTINGS = [
        {
            'url': [slapd.ldap_url],
            'basedn': 'o=ôrga',
            'use_tls': False,
            'attributes': ['jpegPhoto'],
        }
    ]
    result = client.post(
        '/login/', {'login-password-submit': '1', 'username': USERNAME, 'password': PASS}, follow=True
    )
    assert result.status_code == 200
    assert force_bytes('Étienne Michu') in result.content
    assert User.objects.count() == 1
    user = User.objects.get()
    assert user.username == '%s@ldap' % USERNAME
    assert user.first_name == 'Étienne'
    assert user.last_name == 'Michu'
    assert user.is_active is True
    assert user.is_superuser is False
    assert user.is_staff is False
    assert user.groups.count() == 0
    assert user.ou == get_default_ou()
    assert not user.check_password(PASS)
    assert 'password' not in client.session['ldap-data']


def test_deactivate_orphaned_users(slapd, settings, client, db, app, superuser):
    settings.LDAP_AUTH_SETTINGS = [
        {
            'url': [slapd.ldap_url],
            'basedn': 'o=ôrga',
            'use_tls': False,
        }
    ]
    utils.login(app, superuser)

    # create users as a side effect
    users = list(ldap_backend.LDAPBackend.get_users())
    block = settings.LDAP_AUTH_SETTINGS[0]
    assert (
        ldap_backend.UserExternalId.objects.filter(user__is_active=False, source=block['realm']).count() == 0
    )
    resp = app.get('/manage/users/%s/' % users[0].pk)
    assert 'Deactivated' not in resp.text

    conn = slapd.get_connection_admin()
    conn.delete_s(DN)

    ldap_backend.LDAPBackend.deactivate_orphaned_users()
    list(ldap_backend.LDAPBackend.get_users())

    deactivated_user = ldap_backend.UserExternalId.objects.get(
        user__is_active=False,
        source=block['realm'],
        user__deactivation__isnull=False,
        user__deactivation_reason__startswith='ldap-',
    )
    utils.assert_event(
        'manager.user.deactivation',
        target_user=deactivated_user.user,
        reason='ldap-not-present',
        origin=slapd.ldap_url,
    )
    resp = app.get('/manage/users/%s/' % deactivated_user.user.pk)
    assert 'Deactivated' in resp.text
    assert 'associated LDAP account does not exist anymore' in resp.text

    # deactivate an active user manually
    User.objects.filter(is_active=True).first().mark_as_inactive(reason='bad user')

    # rename source realm
    settings.LDAP_AUTH_SETTINGS = []
    ldap_backend.LDAPBackend.deactivate_orphaned_users()
    list(ldap_backend.LDAPBackend.get_users())

    ldap_deactivated_users = ldap_backend.UserExternalId.objects.filter(
        user__is_active=False,
        source=block['realm'],
        user__deactivation__isnull=False,
        user__deactivation_reason__startswith='ldap-',
    )
    assert ldap_deactivated_users.count() == 5
    assert (
        ldap_backend.UserExternalId.objects.filter(
            user__is_active=False,
            source=block['realm'],
            user__deactivation__isnull=False,
        ).count()
        == 6
    )

    for ldap_user in ldap_deactivated_users.exclude(pk=deactivated_user.pk):
        utils.assert_event(
            'manager.user.deactivation',
            target_user=ldap_user.user,
            reason='ldap-old-source',
        )
    resp = app.get('/manage/users/%s/' % ldap_user.user.pk)
    assert 'Deactivated' in resp.text
    assert 'associated LDAP source has been deleted' in resp.text

    # reactivate users
    settings.LDAP_AUTH_SETTINGS = [block]
    list(ldap_backend.LDAPBackend.get_users())
    ldap_backend.LDAPBackend.deactivate_orphaned_users()
    assert (
        ldap_backend.UserExternalId.objects.filter(
            user__is_active=False,
            source=block['realm'],
            user__deactivation__isnull=False,
            user__deactivation_reason__startswith='ldap-',
        ).count()
        == 1
    )
    reactivated_users = User.objects.filter(
        is_active=True,
        deactivation_reason__isnull=True,
        deactivation__isnull=True,
        userexternalid__isnull=False,
    )
    assert reactivated_users.count() == 4
    assert User.objects.filter(is_active=False).count() == 2
    assert User.objects.count() == 7

    for user in reactivated_users:
        utils.assert_event(
            'manager.user.activation',
            target_user=user,
            reason='ldap-reactivation',
            origin=slapd.ldap_url,
        )
    resp = app.get('/manage/users/%s/' % user.pk)
    assert 'Deactivated' not in resp.text


@pytest.mark.django_db
def test_simple_with_binddn(slapd, settings, client):
    settings.LDAP_AUTH_SETTINGS = [
        {
            'url': [slapd.ldap_url],
            'binddn': force_str(DN),
            'bindpw': PASS,
            'basedn': 'o=ôrga',
            'use_tls': False,
        }
    ]
    result = client.post(
        '/login/', {'login-password-submit': '1', 'username': USERNAME, 'password': PASS}, follow=True
    )
    assert result.status_code == 200
    assert force_bytes('Étienne Michu') in result.content
    assert User.objects.count() == 1
    user = User.objects.get()
    assert user.username == '%s@ldap' % USERNAME
    assert user.first_name == 'Étienne'
    assert user.last_name == 'Michu'
    assert user.is_active is True
    assert user.is_superuser is False
    assert user.is_staff is False
    assert user.groups.count() == 0
    assert user.ou == get_default_ou()
    assert not user.check_password(PASS)
    assert 'password' not in client.session['ldap-data']


def test_double_login(slapd, simple_user, settings, app, db):
    settings.LDAP_AUTH_SETTINGS = [
        {
            'url': [slapd.ldap_url],
            'basedn': 'o=ôrga',
            'use_tls': False,
            'is_superuser': True,
            'is_staff': True,
        }
    ]
    utils.login(app, simple_user, path='/admin/')
    utils.login(app, UID, password=PASS, path='/admin/')


def test_login_failure(slapd, simple_user, settings, app, db, caplog):
    settings.LDAP_AUTH_SETTINGS = [
        {
            'url': [slapd.ldap_url],
            'basedn': 'o=ôrga',
            'use_tls': False,
            'is_superuser': True,
            'is_staff': True,
        }
    ]
    # prevent lookup with admin credentials
    conn = slapd.get_connection_admin()
    ldif = [
        (
            ldap.MOD_REPLACE,
            'olcAccess',
            b'''to *
by dn.exact=uid=admin,cn=config manage
by users read
by * auth
''',
        )
    ]
    conn.modify_s('olcDatabase={%s}mdb,cn=config' % (slapd.db_index - 1), ldif)

    # create ldap user
    utils.login(app, UID, password=PASS, path='/admin/', fail=True)

    settings.LDAP_AUTH_SETTINGS[0]['binddn'] = 'uid=admin,cn=config'
    settings.LDAP_AUTH_SETTINGS[0]['bindpw'] = 'admin'

    utils.login(app, UID, password=PASS, path='/admin/')
    utils.logout(app)
    user = ldap_backend.LDAPUser.objects.get(username='%s@ldap' % UID)

    utils.login(app, simple_user, password='wrong', fail=True)
    utils.assert_event('user.login.failure', user=simple_user, username=simple_user.username)

    utils.login(app, UID, password='wrong', fail=True)
    utils.assert_event('user.login.failure', user=user, username=UID)

    assert 'unable to retrieve attributes' not in caplog.text


def test_keep_password_in_session(slapd, settings, client, db):
    settings.LDAP_AUTH_SETTINGS = [
        {
            'url': [slapd.ldap_url],
            'basedn': 'o=ôrga',
            'use_tls': False,
            'keep_password_in_session': True,
        }
    ]
    result = client.post(
        '/login/', {'login-password-submit': '1', 'username': USERNAME, 'password': PASS}, follow=True
    )
    assert result.status_code == 200
    assert force_bytes('Étienne Michu') in result.content
    assert User.objects.count() == 1
    user = User.objects.get()
    assert user.username == '%s@ldap' % USERNAME
    assert user.first_name == 'Étienne'
    assert user.last_name == 'Michu'
    assert user.ou == get_default_ou()
    assert not user.check_password(PASS)
    assert client.session['ldap-data']['password']
    assert DN.lower() in result.context['request'].user.ldap_data['password']
    assert crypto.aes_base64_decrypt(
        settings.SECRET_KEY, force_bytes(result.context['request'].user.ldap_data['password'][DN.lower()])
    ) == force_bytes(PASS)


def test_keep_password_true_or_false(slapd, settings, db):
    settings.LDAP_AUTH_SETTINGS = [
        {
            'url': [slapd.ldap_url],
            'basedn': 'o=ôrga',
            'use_tls': False,
            'keep_password': True,
        }
    ]
    user = authenticate(username=USERNAME, password=PASS)
    assert User.objects.count() == 1
    user = User.objects.get()
    assert user.check_password(PASS)

    settings.LDAP_AUTH_SETTINGS[0]['keep_password'] = False
    user = ldap_backend.LDAPBackend().authenticate(username=USERNAME, password=PASS)
    assert User.objects.count() == 1
    user = User.objects.get()
    assert not user.check_password(PASS)


@pytest.mark.django_db
def test_custom_ou(slapd, settings, client):
    ou = OrganizationalUnit.objects.create(name='test', slug='test')
    settings.LDAP_AUTH_SETTINGS = [
        {
            'url': [slapd.ldap_url],
            'basedn': 'o=ôrga',
            'use_tls': False,
            'ou_slug': 'test',
        }
    ]
    result = client.post(
        '/login/', {'login-password-submit': '1', 'username': USERNAME, 'password': PASS}, follow=True
    )
    assert result.status_code == 200
    assert force_bytes('Étienne Michu') in result.content
    assert User.objects.count() == 1
    user = User.objects.get()
    assert user.username == '%s@ldap' % USERNAME
    assert user.first_name == 'Étienne'
    assert user.last_name == 'Michu'
    assert user.ou == ou
    assert not user.check_password(PASS)


def test_wrong_ou(slapd, settings, client, db):
    settings.LDAP_AUTH_SETTINGS = [
        {
            'url': [slapd.ldap_url],
            'basedn': 'o=ôrga',
            'use_tls': False,
            'ou_slug': 'test',
        }
    ]
    with pytest.raises(ImproperlyConfigured):
        client.post(
            '/login/', {'login-password-submit': '1', 'username': USERNAME, 'password': PASS}, follow=True
        )


def test_dn_formatter():
    formatter = FilterFormatter()

    assert formatter.format('uid={uid}', uid='john doe') == 'uid=john doe'
    assert formatter.format('uid={uid}', uid='(#$!"?éé') == 'uid=\\28#$!"?éé'
    assert formatter.format('uid={uid}', uid=['(#$!"?éé']) == 'uid=\\28#$!"?éé'
    assert formatter.format('uid={uid}', uid=('(#$!"?éé',)) == 'uid=\\28#$!"?éé'

    formatter = DnFormatter()

    assert formatter.format('uid={uid}', uid='john doé!#$"\'-_') == 'uid=john doé!#$\\"\'-_'
    assert formatter.format('uid={uid}', uid=['john doé!#$"\'-_']) == 'uid=john doé!#$\\"\'-_'


def test_group_mapping(slapd, settings, client, db):
    settings.LDAP_AUTH_SETTINGS = [
        {
            'url': [slapd.ldap_url],
            'basedn': 'o=ôrga',
            'use_tls': False,
            'create_group': True,
            'group_mapping': [
                ['cn=group1,o=ôrga', ['Group1']],
            ],
        }
    ]
    assert Group.objects.filter(name='Group1').count() == 0
    response = client.post(
        '/login/', {'login-password-submit': '1', 'username': USERNAME, 'password': PASS}, follow=True
    )
    assert Group.objects.filter(name='Group1').count() == 1
    assert response.context['user'].username == '%s@ldap' % USERNAME
    assert response.context['user'].groups.count() == 1


def test_posix_group_mapping(slapd, settings, client, db):
    settings.LDAP_AUTH_SETTINGS = [
        {
            'url': [slapd.ldap_url],
            'basedn': 'o=ôrga',
            'use_tls': False,
            'create_group': True,
            'group_mapping': [
                ['cn=group2,o=ôrga', ['Group2']],
            ],
            'group_filter': '(&(memberUid={uid})(objectClass=posixGroup))',
        }
    ]
    assert Group.objects.filter(name='Group2').count() == 0
    response = client.post(
        '/login/', {'login-password-submit': '1', 'username': USERNAME, 'password': PASS}, follow=True
    )
    assert Group.objects.filter(name='Group2').count() == 1
    assert response.context['user'].username == '%s@ldap' % USERNAME
    assert response.context['user'].groups.count() == 1


def test_group_to_role_mapping(slapd, settings, client, db, caplog):
    Role.objects.create(name='Role1')
    Role.objects.create(name='Role2')
    role3 = Role.objects.create(name='Role3')

    # precreate user, expect lookup_by_username to match it with the LDAP account
    user = User.objects.create(ou=get_default_ou(), username=f'{USERNAME}@ldap')
    user.roles.add(role3)

    settings.LDAP_AUTH_SETTINGS = [
        {
            'url': [slapd.ldap_url],
            'basedn': 'o=ôrga',
            'use_tls': False,
            # memberOf is not defined on OpenLDAP so we use street for storing DN like
            # memberOf values
            'member_of_attribute': 'STReet',
            'group_to_role_mapping': [
                ['cn=GrouP1,o=ôrga', ['Role1']],
                ['cn=GrouP2,o=ôrga', ['Role2']],
                ['cn=GrouP3,o=ôrga', ['Role1']],
                ['cn=GrouP3,o=ôrga', ['Role3']],
            ],
        }
    ]
    response = client.post(
        '/login/', {'login-password-submit': '1', 'username': USERNAME, 'password': PASS}, follow=True
    )
    assert User.objects.count() == 1
    assert response.context['user'].username == '%s@ldap' % USERNAME
    # check Role1 was not removed by its second occurence in the list and Role3 was removed.
    assert set(response.context['user'].roles.values_list('name', flat=True)) == {'Role1', 'Role2'}


def test_posix_group_to_role_mapping(slapd, settings, client, db):
    Role.objects.get_or_create(name='Role2')
    settings.LDAP_AUTH_SETTINGS = [
        {
            'url': [slapd.ldap_url],
            'basedn': 'o=ôrga',
            'use_tls': False,
            'group_to_role_mapping': [
                ['cn=group2,o=ôrga', ['Role2']],
            ],
            'group_filter': '(&(memberUid={uid})(objectClass=posixGroup))',
        }
    ]
    response = client.post(
        '/login/', {'login-password-submit': '1', 'username': USERNAME, 'password': PASS}, follow=True
    )
    assert response.context['user'].username == '%s@ldap' % USERNAME
    assert response.context['user'].roles.count() == 1


def test_group_to_role_mapping_modify_disabled(slapd, settings, db, app, admin, client):
    role = Role.objects.create(name='Role3')
    settings.LDAP_AUTH_SETTINGS = [
        {
            'url': [slapd.ldap_url],
            'basedn': 'o=ôrga',
            'use_tls': False,
            'group_to_role_mapping': [
                ['cn=group1,o=ôrga', ['Role3']],
            ],
        }
    ]
    response = client.post(
        '/login/', {'login-password-submit': '1', 'username': USERNAME, 'password': PASS}, follow=True
    )
    user = response.context['user']
    assert user.roles.count() == 1

    utils.login(app, admin, '/manage/')

    response = app.get('/manage/roles/')
    q = response.pyquery.remove_namespaces()
    assert q('table tbody td.name').text() == 'Role3 (LDAP)'

    response = app.get('/manage/users/%s/roles/?search-ou=%s' % (user.pk, user.ou.pk))
    q = response.pyquery.remove_namespaces()
    assert q('table tbody td.name').text() == 'Role3 (LDAP)'
    assert q('table tbody td.member input').attr('disabled')

    response = app.get('/manage/users/%s/roles/?search-ou=all' % user.pk)
    q = response.pyquery.remove_namespaces()
    assert q('table tbody td.name').text() == 'Role3 (LDAP)'
    assert q('table tbody td.member input').attr('disabled')

    response = app.get('/manage/roles/%s/' % (role.pk))
    assert 'synchronised from LDAP' in response.text
    assert 'Add a role as a member' not in response.text
    q = response.pyquery.remove_namespaces()
    assert not q('form.manager-m2m-add-form')
    assert not q('table tbody td a.icon-remove-sign js-remove-object')


def test_group_su(slapd, settings, client, db):
    settings.LDAP_AUTH_SETTINGS = [
        {
            'url': [slapd.ldap_url],
            'basedn': 'o=ôrga',
            'use_tls': False,
            'groupsu': ['cn=group1,o=ôrga'],
        }
    ]
    response = client.post(
        '/login/', {'login-password-submit': '1', 'username': USERNAME, 'password': PASS}, follow=True
    )
    assert Group.objects.count() == 0
    assert response.context['user'].username == '%s@ldap' % USERNAME
    assert response.context['user'].is_superuser
    assert not response.context['user'].is_staff


def test_group_staff(slapd, settings, client, db):
    settings.LDAP_AUTH_SETTINGS = [
        {
            'url': [slapd.ldap_url],
            'basedn': 'o=ôrga',
            'use_tls': False,
            'groupstaff': ['cn=group1,o=ôrga'],
        }
    ]
    response = client.post(
        '/login/', {'login-password-submit': '1', 'username': USERNAME, 'password': PASS}, follow=True
    )
    assert Group.objects.count() == 0
    assert response.context['user'].username == '%s@ldap' % USERNAME
    assert response.context['user'].is_staff
    assert not response.context['user'].is_superuser


class TestGetUsers:
    @pytest.fixture(autouse=True)
    def setup(self, settings, slapd):
        settings.LDAP_AUTH_SETTINGS = [
            {
                'url': [slapd.ldap_url],
                'basedn': 'o=ôrga',
                'use_tls': False,
                'create_group': True,
                'group_mapping': [
                    ['cn=group2,o=ôrga', ['Group2']],
                ],
                'group_filter': '(&(memberUid={uid})(objectClass=posixGroup))',
                'group_to_role_mapping': [
                    ['cn=unknown,o=dn', ['Role2']],
                ],
            }
        ]

    @pytest.fixture
    def save(self, monkeypatch):
        mock_save = mock.Mock(wraps=ldap_backend.LDAPUser.save)

        def save(*args, **kwargs):
            return mock_save(*args, **kwargs)

        monkeypatch.setattr(ldap_backend.LDAPUser, 'save', save)
        return mock_save

    @pytest.fixture
    def bulk_create(self, monkeypatch):
        mock_bulk_create = mock.Mock(wraps=QuerySet.bulk_create)

        def bulk_create(*args, **kwargs):
            return mock_bulk_create(*args, **kwargs)

        monkeypatch.setattr(QuerySet, 'bulk_create', bulk_create)
        return mock_bulk_create

    def test_get_users_basic(self, slapd, db, save, bulk_create, caplog):
        assert Group.objects.count() == 0
        assert User.objects.count() == 0

        # Provision all users and their groups
        users = list(ldap_backend.LDAPBackend.get_users())
        assert len(users) == 6
        assert User.objects.count() == 6
        assert bulk_create.call_count == 1
        assert save.call_count == 18
        assert Group.objects.count() == 1
        assert Group.objects.get().user_set.count() == 1

        # Check that if nothing changed no save() is made
        save.reset_mock()
        bulk_create.reset_mock()
        with utils.check_log(caplog, 'ldap: unknown group "cn=unknown,o=dn" mapped to a role'):
            users = list(ldap_backend.LDAPBackend.get_users())
        assert save.call_count == 0
        assert bulk_create.call_count == 0

        # Check that if we delete 1 user, only this user is created
        save.reset_mock()
        bulk_create.reset_mock()
        User.objects.filter(username='etienne.michu@ldap').delete()
        assert User.objects.count() == 5
        users = list(ldap_backend.LDAPBackend.get_users())
        assert len(users) == 6
        assert User.objects.count() == 6
        assert save.call_count == 3
        assert bulk_create.call_count == 1

        # uppercase user uid in the directory and check that no new user is created
        conn = slapd.get_connection_admin()
        ldif = [(ldap.MOD_REPLACE, 'uid', force_bytes(UID.upper()))]
        conn.modify_s(DN, ldif)
        save.reset_mock()
        bulk_create.reset_mock()
        users = list(ldap_backend.LDAPBackend.get_users())
        assert len(users) == 6
        assert User.objects.count() == 6
        assert save.call_count == 0
        assert bulk_create.call_count == 0

    def test_get_users_email_lookup_case(self, slapd, db):
        User.objects.create(
            username='foo.bar',
            first_name='foo',
            last_name='bar',
            email='EtiEnne.Michu@example.net',
            ou=get_default_ou(),
        )

        list(ldap_backend.LDAPBackend.get_users())

        assert User.objects.count() == 6
        assert ldap_backend.UserExternalId.objects.count() == 6

    def test_get_users_no_duplicate_on_uid_case_change(self, settings, db, save, bulk_create):
        # https://dev.entrouvert.org/issues/27697
        # old problem, now that we use guid to federate with LDAP account it does matter anymore
        settings.LDAP_AUTH_SETTINGS[0]['lookups'] = ['external_id', 'username']

        list(ldap_backend.LDAPBackend.get_users())

        assert ldap_backend.LDAPUser.objects.count() == 6

        # create user with the same username, but case-different
        user = ldap_backend.LDAPUser.objects.create(username=UID.capitalize())
        ldap_backend.UserExternalId.objects.create(external_id=UID.capitalize(), source='ldap', user=user)
        # set user login time as if he logged in
        user = ldap_backend.LDAPUser.objects.get(username='%s@ldap' % UID)
        user.last_login = timezone.now()
        user.save()

        assert ldap_backend.LDAPUser.objects.count() == 7
        assert ldap_backend.UserExternalId.objects.count() == 7

        list(ldap_backend.LDAPBackend.get_users())
        assert ldap_backend.LDAPUser.objects.count() == 6
        assert ldap_backend.UserExternalId.objects.count() == 6
        assert ldap_backend.LDAPUser.objects.filter(username='%s' % UID.capitalize()).count() == 0


def test_set_mandatory_roles(slapd, settings, db):
    Role.objects.get_or_create(name='tech')
    Role.objects.get_or_create(name='admin')
    settings.LDAP_AUTH_SETTINGS = [
        {
            'url': [slapd.ldap_url],
            'basedn': 'o=ôrga',
            'use_tls': False,
            'create_group': True,
            'group_mapping': [
                ['cn=group2,o=ôrga', ['Group2']],
            ],
            'group_filter': '(&(memberUid={uid})(objectClass=posixGroup))',
            'set_mandatory_roles': ['tech', 'admin'],
        }
    ]

    list(ldap_backend.LDAPBackend.get_users())
    assert User.objects.first().roles.count() == 2


def test_nocreate_mandatory_roles(slapd, settings, db):
    settings.LDAP_AUTH_SETTINGS = [
        {
            'url': [slapd.ldap_url],
            'basedn': 'o=ôrga',
            'use_tls': False,
            'create_group': True,
            'group_mapping': [
                ['cn=group2,o=ôrga', ['Group2']],
            ],
            'group_filter': '(&(memberUid={uid})(objectClass=posixGroup))',
            'set_mandatory_roles': ['tech', 'admin'],
        }
    ]

    list(ldap_backend.LDAPBackend.get_users())
    assert User.objects.first().roles.count() == 0


def test_from_slug_set_mandatory_roles(slapd, settings, db):
    Role.objects.get_or_create(name='Tech', slug='tech')
    Role.objects.get_or_create(name='Admin', slug='admin')
    settings.LDAP_AUTH_SETTINGS = [
        {
            'url': [slapd.ldap_url],
            'basedn': 'o=ôrga',
            'use_tls': False,
            'create_group': True,
            'group_mapping': [
                ['cn=group2,o=ôrga', ['Group2']],
            ],
            'group_filter': '(&(memberUid={uid})(objectClass=posixGroup))',
            'set_mandatory_roles': ['tech', 'admin'],
        }
    ]

    list(ldap_backend.LDAPBackend.get_users())
    assert User.objects.first().roles.count() == 2


def test_multiple_slug_set_mandatory_roles(slapd, settings, db):
    service1 = Service.objects.create(name='s1', slug='s1')
    service2 = Service.objects.create(name='s2', slug='s2')
    Role.objects.create(name='foo', slug='tech', service=service1)
    Role.objects.create(name='bar', slug='tech', service=service2)
    settings.LDAP_AUTH_SETTINGS = [
        {
            'url': [slapd.ldap_url],
            'basedn': 'o=ôrga',
            'use_tls': False,
            'create_group': True,
            'group_mapping': [
                ['cn=group2,o=ôrga', ['Group2']],
            ],
            'group_filter': '(&(memberUid={uid})(objectClass=posixGroup))',
            'set_mandatory_roles': ['tech'],
        }
    ]

    list(ldap_backend.LDAPBackend.get_users())
    assert User.objects.first().roles.count() == 0


def test_multiple_name_set_mandatory_roles(slapd, settings, db):
    ou1 = OrganizationalUnit.objects.create(name='test1', slug='test1')
    ou2 = OrganizationalUnit.objects.create(name='test2', slug='test2')
    Role.objects.create(name='tech', slug='foo', ou=ou1)
    Role.objects.create(name='tech', slug='bar', ou=ou2)
    settings.LDAP_AUTH_SETTINGS = [
        {
            'url': [slapd.ldap_url],
            'basedn': 'o=ôrga',
            'use_tls': False,
            'create_group': True,
            'group_mapping': [
                ['cn=group2,o=ôrga', ['Group2']],
            ],
            'group_filter': '(&(memberUid={uid})(objectClass=posixGroup))',
            'set_mandatory_roles': ['tech'],
        }
    ]

    list(ldap_backend.LDAPBackend.get_users())
    assert User.objects.first().roles.count() == 0


@pytest.fixture
def slapd_strict_acl(slapd):
    # forbid modifications by user themselves
    conn = slapd.get_connection_external()
    result = conn.search_s('cn=config', ldap.SCOPE_SUBTREE, 'olcSuffix=o=ôrga')
    dn = result[0][0]
    conn.modify_s(
        dn,
        [(ldap.MOD_REPLACE, 'olcAccess', [force_bytes('{0}to * by dn.subtree="o=ôrga" none by * manage')])],
    )
    return slapd


def test_no_connect_with_user_credentials(slapd_strict_acl, db, settings, app):
    slapd = slapd_strict_acl
    settings.LDAP_AUTH_SETTINGS = [
        {
            'url': [slapd.ldap_url],
            'basedn': 'o=ôrga',
            'use_tls': False,
            'create_group': True,
            'group_mapping': [
                ['cn=group2,o=ôrga', ['Group2']],
            ],
            'group_filter': '(&(memberUid={uid})(objectClass=posixGroup))',
            'set_mandatory_roles': ['tech', 'admin'],
        }
    ]
    response = app.get('/login/')
    response.form.set('username', USERNAME)
    response.form.set('password', PASS)
    response = response.form.submit('login-password-submit')
    assert response.status_code == 200
    assert force_bytes('Étienne Michu') not in response.body

    settings.LDAP_AUTH_SETTINGS[0]['connect_with_user_credentials'] = False
    response = app.get('/login/')
    response.form.set('username', USERNAME)
    response.form.set('password', PASS)
    response = response.form.submit('login-password-submit').follow()
    assert force_bytes('Étienne Michu') in response.body


def test_reset_password_ldap_user(slapd, settings, app, db, caplog):
    settings.LDAP_AUTH_SETTINGS = [
        {
            'url': [slapd.ldap_url],
            'binddn': force_str(slapd.root_bind_dn),
            'bindpw': force_str(slapd.root_bind_password),
            'basedn': 'o=ôrga',
            'use_tls': False,
            'attributes': ['uid', 'carLicense'],
        }
    ]

    assert User.objects.count() == 0
    # first login
    response = app.get('/login/')
    response.form['username'] = USERNAME
    response.form['password'] = PASS
    response = response.form.submit('login-password-submit').follow()
    assert User.objects.count() == 1
    assert 'Étienne Michu' in str(response)
    user = User.objects.get()
    assert user.email == EMAIL
    # logout
    response = response.click('Logout').maybe_follow()

    # password reset not allowed
    response = response.click('Reset it!')
    response.form['email'] = EMAIL
    assert len(mail.outbox) == 0
    response.form.submit().maybe_follow()
    assert len(mail.outbox) == 1
    assert mail.outbox[0].subject == 'Password reset cannot be performed on testserver'
    assert 'your account is synchronised from a LDAP server' in mail.outbox[0].body
    assert 'account is from ldap and password reset is forbidden' in caplog.text

    # access to account is possible anyway
    token_login_url = utils.get_link_from_mail(mail.outbox[0])
    response = app.get(token_login_url).follow()
    assert '_auth_user_id' in app.session
    response = response.click('Logout').maybe_follow()

    settings.LDAP_AUTH_SETTINGS[0]['can_reset_password'] = True
    response = response.click('Reset it!')
    response.form['email'] = EMAIL
    response.form.submit().maybe_follow()
    assert len(mail.outbox) == 2
    reset_email_url = utils.get_link_from_mail(mail.outbox[1])
    response = app.get(reset_email_url, status=200)
    new_password = 'Aa1xxxxx'
    response.form['new_password1'] = new_password
    response.form['new_password2'] = new_password
    response = response.form.submit(status=302).maybe_follow()
    assert app.session['_auth_user_backend'] == 'authentic2.backends.ldap_backend.LDAPBackendPasswordLost'
    template_user = response.context['user']
    assert 'carlicense' in template_user.get_attributes(object(), {})
    # logout
    response = response.click('Logout').maybe_follow()

    # verify password has changed
    slapd.get_connection().bind_s(DN, new_password)
    with pytest.raises(ldap.INVALID_CREDENTIALS):
        slapd.get_connection().bind_s(DN, PASS)
    assert not User.objects.get().has_usable_password()


def test_reset_password_ldap_failure(slapd, settings, app, db, caplog):
    settings.LDAP_AUTH_SETTINGS = [
        {
            'url': [slapd.ldap_url],
            'binddn': force_str(slapd.root_bind_dn),
            'bindpw': force_str(slapd.root_bind_password),
            'basedn': 'o=ôrga',
            'use_tls': False,
            'attributes': ['uid', 'carLicense'],
        }
    ]

    assert User.objects.count() == 0
    # first login
    response = app.get('/login/')
    response.form['username'] = USERNAME
    response.form['password'] = PASS
    response = response.form.submit('login-password-submit').follow()
    assert User.objects.count() == 1
    assert 'Étienne Michu' in str(response)
    user = User.objects.get()
    assert user.email == EMAIL
    # logout
    response = response.click('Logout').maybe_follow()

    # password reset not allowed
    slapd.stop()
    response = response.click('Reset it!')
    response.form['email'] = EMAIL
    assert len(mail.outbox) == 0
    response.form.submit().maybe_follow()
    assert len(mail.outbox) == 1
    assert mail.outbox[0].subject == 'Password reset cannot be performed on testserver'
    assert 'your account is synchronised from a LDAP server' in mail.outbox[0].body
    assert 'account is from ldap but it could not be retrieved' in caplog.text


def test_reset_password_refused_by_ldap_server(slapd, settings, app, db, caplog):
    settings.LDAP_AUTH_SETTINGS = [
        {
            'url': [slapd.ldap_url],
            'binddn': force_str(slapd.root_bind_dn),
            'bindpw': force_str(slapd.root_bind_password),
            'basedn': 'o=ôrga',
            'use_tls': False,
            'attributes': ['uid', 'carLicense'],
            'can_reset_password': True,
        }
    ]

    assert User.objects.count() == 0
    # first login
    response = app.get('/login/')
    response.form['username'] = USERNAME
    response.form['password'] = PASS
    response = response.form.submit('login-password-submit').follow()
    assert User.objects.count() == 1
    assert 'Étienne Michu' in str(response)
    user = User.objects.get()
    assert user.email == EMAIL
    # logout
    response = response.click('Logout').maybe_follow()

    # password reset
    response = response.click('Reset it!')
    response.form['email'] = EMAIL
    assert len(mail.outbox) == 0
    response = response.form.submit()
    assert response['Location'].endswith('/instructions/')
    assert len(mail.outbox) == 1
    url = utils.get_link_from_mail(mail.outbox[0])
    relative_url = url.split('testserver')[1]
    response = app.get(relative_url, status=200)
    response.form.set('new_password1', '1234==aA')
    response.form.set('new_password2', '1234==aA')

    # Make LDAP directory as read-only to trigger an error
    conn = slapd.get_connection_admin()
    ldif = [
        (
            ldap.MOD_REPLACE,
            'olcReadOnly',
            b'TRUE',
        )
    ]
    conn.modify_s('olcDatabase={%s}mdb,cn=config' % (slapd.db_index - 1), ldif)

    response = response.form.submit()
    assert 'LDAP directory refused the password change' in response


def test_user_cannot_change_password(slapd, settings, app, db):
    settings.LDAP_AUTH_SETTINGS = [
        {
            'url': [slapd.ldap_url],
            'binddn': force_str(slapd.root_bind_dn),
            'bindpw': force_str(slapd.root_bind_password),
            'basedn': 'o=ôrga',
            'use_tls': False,
            'user_can_change_password': False,
        }
    ]
    assert User.objects.count() == 0
    # first login
    response = app.get('/login/')
    response.form['username'] = USERNAME
    response.form['password'] = PASS
    response = response.form.submit('login-password-submit').follow()
    response = response.click('Your account')
    assert 'Password' not in response
    response = app.get('/accounts/password/change/')
    assert response['Location'].endswith('/accounts/')


def test_user_change_password_denied(slapd, settings, app, db):
    settings.LDAP_AUTH_SETTINGS = [
        {
            'url': [slapd.ldap_url],
            'basedn': 'o=ôrga',
            'use_tls': False,
        }
    ]
    assert User.objects.count() == 0
    # first login
    response = app.get('/login/')
    response.form['username'] = USERNAME
    response.form['password'] = PASS
    response = response.form.submit('login-password-submit').follow()

    response = app.get('/accounts/password/change/')
    response.form['old_password'] = PASS
    response.form['new_password1'] = 'hopAbcde1'
    response.form['new_password2'] = 'hopAbcde1'
    with mock.patch(
        'authentic2.backends.ldap_backend.LDAPBackend.modify_password', side_effect=ldap.UNWILLING_TO_PERFORM
    ):
        response = response.form.submit()
        assert 'LDAP directory refused the password change' in response.text


def test_user_change_password(slapd, settings, app, db):
    settings.LDAP_AUTH_SETTINGS = [
        {
            'url': [slapd.ldap_url],
            'basedn': 'o=ôrga',
            'use_tls': False,
            'user_can_change_password': True,
        }
    ]
    assert User.objects.count() == 0
    # first login
    response = app.get('/login/')
    response.form['username'] = USERNAME
    response.form['password'] = PASS
    response = response.form.submit('login-password-submit').follow()

    response = app.get('/accounts/password/change/')
    response.form['old_password'] = PASS
    response.form['new_password1'] = 'hopAbcde1'
    response.form['new_password2'] = 'hopAbcde1'
    response = response.form.submit().follow()
    assert 'Password changed' in response.text


def test_login_ppolicy_password_expired(slapd_ppolicy, settings, app, db, caplog):
    settings.LDAP_AUTH_SETTINGS = [
        {
            'url': [slapd_ppolicy.ldap_url],
            'basedn': 'o=ôrga',
            'use_tls': False,
            'user_can_change_password': True,
            'use_controls': True,
            'use_ppolicy_controls': True,
        }
    ]
    # Add default ppolicy with pwdMaxAge defined
    pwdMaxAge = 2
    slapd_ppolicy.add_ldif(
        '''
dn: cn=default,ou=ppolicies,o=ôrga
cn: default
objectclass: top
objectclass: device
objectclass: pwdPolicy
objectclass: pwdPolicyChecker
pwdAttribute: userPassword
pwdMaxAge: {pwdMaxAge}
'''.format(
            pwdMaxAge=pwdMaxAge
        )
    )

    assert User.objects.count() == 0
    # first login
    response = app.get('/login/')
    response.form['username'] = USERNAME
    response.form['password'] = PASS
    response = response.form.submit('login-password-submit').follow()

    password = 'hopAbcde1'
    response = app.get('/accounts/password/change/')
    response.form['old_password'] = PASS
    response.form['new_password1'] = password
    response.form['new_password2'] = password
    response = response.form.submit().follow()
    assert 'Password changed' in response.text

    response = response.click('Logout')

    time.sleep(pwdMaxAge * 2)

    response = app.get('/login/')
    response.form['username'] = USERNAME
    response.form['password'] = password
    response = response.form.submit('login-password-submit').maybe_follow()

    assert 'The password expired.' in response


def test_user_change_password_in_history(slapd_ppolicy, settings, app, db):
    settings.LDAP_AUTH_SETTINGS = [
        {
            'url': [slapd_ppolicy.ldap_url],
            'basedn': 'o=ôrga',
            'use_tls': False,
            'use_ppolicy_controls': True,
            'user_can_change_password': True,
        }
    ]

    # Add default ppolicy with pwdInHistory defined
    slapd_ppolicy.add_ldif(
        '''
dn: cn=default,ou=ppolicies,o=ôrga
cn: default
objectclass: top
objectclass: device
objectclass: pwdPolicy
objectclass: pwdPolicyChecker
pwdAttribute: userPassword
pwdMinAge: 0
pwdMaxAge: 0
pwdInHistory: 1
'''
    )

    assert User.objects.count() == 0
    # first login
    response = app.get('/login/')
    response.form['username'] = USERNAME
    response.form['password'] = PASS
    response = response.form.submit('login-password-submit').follow()

    # change password
    NEW_PASS = 'hopAbcde1'
    response = app.get('/accounts/password/change/')
    response.form['old_password'] = PASS
    response.form['new_password1'] = NEW_PASS
    response.form['new_password2'] = NEW_PASS
    response = response.form.submit().follow()
    assert 'Password changed' in response.text

    # change password again
    response = app.get('/accounts/password/change/')
    response.form['old_password'] = NEW_PASS
    response.form['new_password1'] = PASS
    response.form['new_password2'] = PASS
    response = response.form.submit().maybe_follow()

    assert 'This password has already been used and can no longer be used.' in response.text


def test_user_change_password_too_short(slapd_ppolicy, settings, app, db):
    settings.LDAP_AUTH_SETTINGS = [
        {
            'url': [slapd_ppolicy.ldap_url],
            'basedn': 'o=ôrga',
            'use_tls': False,
            'use_ppolicy_controls': True,
            'user_can_change_password': True,
            'ppolicy_dn': 'cn=default,ou=ppolicies,o=ôrga',
        }
    ]

    # Add default ppolicy with pwdCheckQuality enabled and pwdMinLength defined
    pwdMinLength = 15
    slapd_ppolicy.add_ldif(
        '''
dn: cn=default,ou=ppolicies,o=ôrga
cn: default
objectclass: top
objectclass: device
objectclass: pwdPolicy
objectclass: pwdPolicyChecker
pwdAttribute: userPassword
pwdCheckQuality: 1
pwdMinLength: {pwdMinLength}
'''.format(
            pwdMinLength=pwdMinLength
        )
    )

    assert User.objects.count() == 0
    # first login
    response = app.get('/login/')
    response.form['username'] = USERNAME
    response.form['password'] = PASS
    response = response.form.submit('login-password-submit').follow()

    # change password
    NEW_PASS = 'hopAbcde1'
    response = app.get('/accounts/password/change/')
    response.form['old_password'] = PASS
    response.form['new_password1'] = NEW_PASS
    response.form['new_password2'] = NEW_PASS
    response = response.form.submit().maybe_follow()

    assert 'The password is too short.' in response.text
    assert f'The minimun length is {pwdMinLength} characters.' in response.text


def test_user_change_password_too_soon(slapd_ppolicy, settings, app, db):
    settings.LDAP_AUTH_SETTINGS = [
        {
            'url': [slapd_ppolicy.ldap_url],
            'basedn': 'o=ôrga',
            'use_tls': False,
            'use_ppolicy_controls': True,
            'user_can_change_password': True,
        }
    ]

    # Add default ppolicy with pwdMinAge defined
    slapd_ppolicy.add_ldif(
        '''
dn: cn=default,ou=ppolicies,o=ôrga
cn: default
objectclass: top
objectclass: device
objectclass: pwdPolicy
objectclass: pwdPolicyChecker
pwdAttribute: userPassword
pwdMinAge: 120
'''
    )

    assert User.objects.count() == 0
    # first login
    response = app.get('/login/')
    response.form['username'] = USERNAME
    response.form['password'] = PASS
    response = response.form.submit('login-password-submit').follow()

    # change password
    NEW_PASS = 'hopAbcde1'
    response = app.get('/accounts/password/change/')
    response.form['old_password'] = PASS
    response.form['new_password1'] = NEW_PASS
    response.form['new_password2'] = NEW_PASS
    response = response.form.submit().follow()
    assert 'Password changed' in response.text

    # change password again
    response = app.get('/accounts/password/change/')
    response.form['old_password'] = NEW_PASS
    NEW_PASS += '1'
    response.form['new_password1'] = NEW_PASS
    response.form['new_password2'] = NEW_PASS
    response = response.form.submit().maybe_follow()

    assert 'It is too soon to change the password.' in response.text


def test_reset_password_must_supply_old_password(slapd_ppolicy, settings, app, db, caplog):
    settings.LDAP_AUTH_SETTINGS = [
        {
            'url': [slapd_ppolicy.ldap_url],
            'binddn': force_str(slapd_ppolicy.root_bind_dn),
            'bindpw': force_str(slapd_ppolicy.root_bind_password),
            'basedn': 'o=ôrga',
            'use_tls': False,
            'use_ppolicy_controls': True,
            'can_reset_password': True,
        }
    ]

    # Add default ppolicy with pwdSafeModify enabled
    slapd_ppolicy.add_ldif(
        '''
dn: cn=default,ou=ppolicies,o=ôrga
cn: default
objectclass: top
objectclass: device
objectclass: pwdPolicy
objectclass: pwdPolicyChecker
pwdAttribute: userPassword
pwdSafeModify: TRUE
'''
    )

    assert User.objects.count() == 0
    # first login
    response = app.get('/login/')
    response.form['username'] = USERNAME
    response.form['password'] = PASS
    response = response.form.submit('login-password-submit').follow()
    assert User.objects.count() == 1
    assert 'Étienne Michu' in str(response)
    user = User.objects.get()
    assert user.email == EMAIL
    # logout
    response = response.click('Logout').maybe_follow()

    # password reset
    response = response.click('Reset it!')
    response.form['email'] = EMAIL
    assert len(mail.outbox) == 0
    response = response.form.submit()
    assert response['Location'].endswith('/instructions/')
    assert len(mail.outbox) == 1
    url = utils.get_link_from_mail(mail.outbox[0])
    relative_url = url.split('testserver')[1]
    response = app.get(relative_url, status=200)
    response.form.set('new_password1', '1234==aA')
    response.form.set('new_password2', '1234==aA')

    response = response.form.submit()
    assert 'The old password must be supplied.' in response


def test_reset_by_email_passwords_not_match(app, simple_user, mailoutbox, settings):
    url = reverse('password_reset')
    resp = app.get(url, status=200)
    resp.form.set('email', simple_user.email)
    assert len(mailoutbox) == 0
    settings.DEFAULT_FROM_EMAIL = 'show only addr <noreply@example.net>'
    resp = resp.form.submit()
    utils.assert_event('user.password.reset.request', user=simple_user, email=simple_user.email)
    assert resp['Location'].endswith('/instructions/')
    resp = resp.follow()
    assert len(mailoutbox) == 1
    url = utils.get_link_from_mail(mailoutbox[0])
    relative_url = url.split('testserver')[1]
    resp = app.get(relative_url, status=200)
    resp.form.set('new_password1', '1234==aA')
    resp.form.set('new_password2', '1234')
    resp = resp.form.submit()

    assert 'Passwords do not match.' in resp


def test_login_ppolicy_must_change_password_after_locked(slapd_ppolicy, settings, db, app):
    settings.LDAP_AUTH_SETTINGS = [
        {
            'url': [slapd_ppolicy.ldap_url],
            'basedn': 'o=ôrga',
            'use_tls': False,
            'use_controls': True,
            'use_ppolicy_controls': True,
            'can_reset_password': True,
            'ppolicy_dn': 'cn=default,ou=ppolicies,o=ôrga',
        }
    ]

    # Add default ppolicy with pwdMaxFailure defined and pwdMustChange enabled
    pwdMaxFailure = 2
    slapd_ppolicy.add_ldif(
        '''
dn: cn=default,ou=ppolicies,o=ôrga
cn: default
objectclass: top
objectclass: device
objectclass: pwdPolicy
objectclass: pwdPolicyChecker
pwdAttribute: userPassword
pwdLockout: TRUE
pwdMaxFailure: {pwdMaxFailure}
pwdMustChange: TRUE
'''.format(
            pwdMaxFailure=pwdMaxFailure
        )
    )

    # Locked account after some login errors
    for _ in range(pwdMaxFailure):
        response = app.get('/login/')
        response.form.set('username', USERNAME)
        response.form.set('password', 'invalid')
        response = response.form.submit(name='login-password-submit')
        assert 'Incorrect login or password' in str(response.pyquery('.errornotice'))
        assert 'account is locked' not in str(response.pyquery('.messages'))
    response = app.get('/login/')
    response.form.set('username', USERNAME)
    response.form.set('password', 'invalid')
    response = response.form.submit(name='login-password-submit')

    assert 'Account is locked since ' in str(response.pyquery('.messages'))
    assert f'after {pwdMaxFailure} failed login attempts' in str(response.pyquery('.messages'))

    # Unlock account and force passwor reset
    conn = slapd_ppolicy.get_connection_admin()
    ldif = [
        (ldap.MOD_DELETE, 'pwdAccountLockedTime', None),
        (ldap.MOD_ADD, 'pwdReset', [b'TRUE']),
    ]
    conn.modify_s(DN, ldif)

    # Login with the right password
    next_url = '/'
    response = app.get(f'/login/?next={next_url}')
    response.form.set('username', USERNAME)
    response.form.set('password', PASS)
    response = response.form.submit(name='login-password-submit')

    assert '/password/reset/' in response['Location']
    assert f'next={next_url}' in response['Location']
    response = response.follow()
    assert 'The password was reset and must be changed.' in str(response.pyquery('.messages'))


def test_user_change_password_not_allowed(slapd_ppolicy, settings, app, db):
    settings.LDAP_AUTH_SETTINGS = [
        {
            'url': [slapd_ppolicy.ldap_url],
            'basedn': 'o=ôrga',
            'use_tls': False,
            'use_ppolicy_controls': True,
            'user_can_change_password': True,
        }
    ]

    # Add default ppolicy with pwdAllowUserChange disabled
    slapd_ppolicy.add_ldif(
        '''
dn: cn=default,ou=ppolicies,o=ôrga
cn: default
objectclass: top
objectclass: device
objectclass: pwdPolicy
objectclass: pwdPolicyChecker
pwdAttribute: userPassword
pwdAllowUserChange: FALSE
'''
    )

    assert User.objects.count() == 0
    # first login
    response = app.get('/login/')
    response.form['username'] = USERNAME
    response.form['password'] = PASS
    response = response.form.submit('login-password-submit').follow()

    # change password
    NEW_PASS = 'hopAbcde1'
    response = app.get('/accounts/password/change/')
    response.form['old_password'] = PASS
    response.form['new_password1'] = NEW_PASS
    response.form['new_password2'] = NEW_PASS
    response = response.form.submit().maybe_follow()

    assert 'It is not possible to modify the password.' in response.text


def test_tls(db, tls_slapd, settings, client):
    conn = tls_slapd.get_connection_admin()
    conn.modify_s(
        'cn=config',
        [
            (ldap.MOD_ADD, 'olcTLSCACertificateFile', force_bytes(cert_file)),
            (ldap.MOD_ADD, 'olcTLSVerifyClient', b'demand'),
        ],
    )

    # without TLS it does not work
    settings.LDAP_AUTH_SETTINGS = [
        {
            'url': [tls_slapd.ldap_url],
            'basedn': 'o=ôrga',
            'use_tls': False,
        }
    ]
    result = client.post(
        '/login/', {'login-password-submit': '1', 'username': USERNAME, 'password': PASS}, follow=True
    )
    assert result.status_code == 200
    assert force_bytes('Étienne Michu') not in result.content
    assert force_bytes('name="username"') in result.content

    # without TLS client authentication it does not work
    settings.LDAP_AUTH_SETTINGS = [
        {
            'url': [tls_slapd.ldap_url],
            'basedn': 'o=ôrga',
            'use_tls': True,
            'cacertfile': cert_file,
        }
    ]
    result = client.post(
        '/login/', {'login-password-submit': '1', 'username': USERNAME, 'password': PASS}, follow=True
    )
    assert result.status_code == 200
    assert force_bytes('Étienne Michu') not in result.content
    assert force_bytes('name="username"') in result.content

    # now it works !
    settings.LDAP_AUTH_SETTINGS = [
        {
            'url': [tls_slapd.ldap_url],
            'basedn': 'o=ôrga',
            'use_tls': True,
            'cacertfile': cert_file,
            'certfile': cert_file,
            'keyfile': key_file,
        }
    ]
    result = client.post(
        '/login/', {'login-password-submit': '1', 'username': USERNAME, 'password': PASS}, follow=True
    )
    assert result.status_code == 200
    assert force_bytes('Étienne Michu') in result.content
    assert force_bytes('name="username"') not in result.content


@pytest.mark.parametrize('trailing_slash', ('', '/'))
def test_tls_connect_on_ldap_errors(db, tls_slapd, settings, client, caplog, trailing_slash):
    conn = tls_slapd.get_connection_admin()
    conn.modify_s(
        'cn=config',
        [
            # modifying ca cert to mock buggy ldap server
            (ldap.MOD_ADD, 'olcTLSCACertificateFile', force_bytes(wrong_cert_file)),
            (ldap.MOD_ADD, 'olcTLSVerifyClient', b'demand'),
        ],
    )

    settings.LDAP_AUTH_SETTINGS = [
        {
            'url': [tls_slapd.ldap_url + trailing_slash],
            'basedn': 'o=ôrga',
            'use_tls': True,
            'cacertfile': cert_file,
            'certfile': cert_file,
            'keyfile': key_file,
        }
    ]
    result = client.post(
        '/login/', {'login-password-submit': '1', 'username': USERNAME, 'password': PASS}, follow=True
    )

    # ssl error on bind attempt
    assert result.status_code == 200
    assert force_bytes('Étienne Michu') not in result.content
    assert force_bytes('name="username"') in result.content
    assert 'ssl error on host localhost.entrouvert.org' in caplog.text


def test_connect_wrong_host(db, tls_slapd, settings, client, caplog):
    conn = tls_slapd.get_connection_admin()
    conn.modify_s(
        'cn=config',
        [
            (ldap.MOD_ADD, 'olcTLSCACertificateFile', force_bytes(cert_file)),
            (ldap.MOD_ADD, 'olcTLSVerifyClient', b'demand'),
        ],
    )

    settings.LDAP_AUTH_SETTINGS = [
        {
            'basedn': 'o=ôrga',
            'use_tls': True,
            'cacertfile': cert_file,
            'certfile': cert_file,
            'keyfile': key_file,
        }
    ]

    url = tls_slapd.ldap_url
    uri, port = url.rsplit(':', 1)
    wrong_port = str(int(port) + 1)  # oops slapd not listening on this port
    settings.LDAP_AUTH_SETTINGS[0]['url'] = ['%s:%s' % (uri, wrong_port)]
    result = client.post(
        '/login/', {'login-password-submit': '1', 'username': USERNAME, 'password': PASS}, follow=True
    )

    # earlier connect error when the port is wrong
    assert result.status_code == 200
    assert force_bytes('Étienne Michu') not in result.content
    assert force_bytes('name="username"') in result.content
    assert "ldap '%s:%s' is down" % (uri, wrong_port) in caplog.text
    caplog.clear()

    wrong_uri = 'ldap://localhost.nowhere.null'
    settings.LDAP_AUTH_SETTINGS[0]['url'] = ['%s:%s' % (wrong_uri, port)]

    result = client.post(
        '/login/', {'login-password-submit': '1', 'username': USERNAME, 'password': PASS}, follow=True
    )

    # earlier connect error when the hostname is wrong
    assert result.status_code == 200
    assert force_bytes('Étienne Michu') not in result.content
    assert force_bytes('name="username"') in result.content
    assert "ldap '%s:%s' is down" % (wrong_uri, port) in caplog.text


def test_user_attributes(slapd, settings, client, db):
    settings.LDAP_AUTH_SETTINGS = [
        {
            'url': [slapd.ldap_url],
            'basedn': 'o=ôrga',
            'use_tls': False,
            'user_attributes': [
                {
                    'from_ldap': 'l',
                    'to_user': 'locality',
                },
            ],
        }
    ]

    # create a locality attribute
    models.Attribute.objects.create(
        label='locality',
        name='locality',
        kind='string',
        required=False,
        user_visible=True,
        user_editable=False,
        asked_on_registration=False,
        multiple=False,
    )

    client.post(
        '/login/', {'login-password-submit': '1', 'username': USERNAME, 'password': PASS}, follow=True
    )
    username = '%s@ldap' % USERNAME
    user = User.objects.get(username=username)
    assert user.attributes.locality == 'Paris'
    client.session.flush()
    for i in range(5):
        client.post(
            '/login/',
            {
                'login-password-submit': '1',
                'username': 'mïchu%s' % i,
                'password': PASS,
            },
            follow=True,
        )
        username = 'mïchu%s@ldap' % i
        user = User.objects.get(username=username)
        assert user.attributes.locality == 'locality%s' % i
        client.session.flush()


def test_set_password(slapd, settings, db, caplog):
    settings.LDAP_AUTH_SETTINGS = [
        {
            'url': [slapd.ldap_url],
            'basedn': 'o=ôrga',
            'use_tls': False,
        }
    ]
    user = authenticate(username=USERNAME, password=PASS)
    assert user
    assert user.check_password(PASS)
    user.set_password('àbon')
    assert user.check_password('àbon')
    user2 = authenticate(username=USERNAME, password='àbon')
    assert user.pk == user2.pk

    with mock.patch(
        'authentic2.backends.ldap_backend.LDAPBackend.modify_password', side_effect=ldap.UNWILLING_TO_PERFORM
    ):
        with pytest.raises(PasswordChangeError):
            user.set_password(PASS)
            assert 'set_password failed (UNWILLING_TO_PERFORM)' in caplog.text


def test_login_ppolicy_pwdMaxFailure(slapd_ppolicy, settings, db, app):
    settings.LDAP_AUTH_SETTINGS = [
        {
            'url': [slapd_ppolicy.ldap_url],
            'basedn': 'o=ôrga',
            'use_tls': False,
            'use_controls': True,
            'use_ppolicy_controls': True,
            'ppolicy_dn': 'cn=default,ou=ppolicies,o=ôrga',
        }
    ]

    pwdMaxFailure = 2
    slapd_ppolicy.add_ldif(
        '''
dn: cn=default,ou=ppolicies,o=ôrga
cn: default
objectclass: top
objectclass: device
objectclass: pwdPolicy
objectclass: pwdPolicyChecker
pwdAttribute: userPassword
pwdMinAge: 0
pwdMaxAge: 0
pwdInHistory: 0
pwdCheckQuality: 0
pwdMinLength: 0
pwdExpireWarning: 0
pwdGraceAuthnLimit: 0
pwdLockout: TRUE
pwdLockoutDuration: 0
pwdMaxFailure: {pwdMaxFailure}
pwdMaxRecordedFailure: 0
pwdFailureCountInterval: 0
pwdMustChange: FALSE
pwdAllowUserChange: FALSE
pwdSafeModify: FALSE
'''.format(
            pwdMaxFailure=pwdMaxFailure
        )
    )

    for _ in range(pwdMaxFailure):
        response = app.get('/login/')
        response.form.set('username', USERNAME)
        response.form.set('password', 'invalid')
        response = response.form.submit(name='login-password-submit')
        assert 'Incorrect login or password' in str(response.pyquery('.errornotice'))
        assert 'account is locked' not in str(response.pyquery('.messages'))
    response = app.get('/login/')
    response.form.set('username', USERNAME)
    response.form.set('password', 'invalid')
    response = response.form.submit(name='login-password-submit')
    assert 'Account is locked since ' in str(response.pyquery('.messages'))
    assert f'after {pwdMaxFailure} failed login attempts' in str(response.pyquery('.messages'))


def ppolicy_authenticate_exactly_pwdMaxFailure(slapd_ppolicy, caplog):
    pwdMaxFailure = 2
    slapd_ppolicy.add_ldif(
        '''
dn: cn=default,ou=ppolicies,o=ôrga
cn: default
objectclass: top
objectclass: device
objectclass: pwdPolicy
objectclass: pwdPolicyChecker
pwdAttribute: userPassword
pwdMinAge: 0
pwdMaxAge: 0
pwdInHistory: 0
pwdCheckQuality: 0
pwdMinLength: 0
pwdExpireWarning: 0
pwdGraceAuthnLimit: 0
pwdLockout: TRUE
pwdLockoutDuration: 0
pwdMaxFailure: {pwdMaxFailure}
pwdMaxRecordedFailure: 0
pwdFailureCountInterval: 0
pwdMustChange: FALSE
pwdAllowUserChange: FALSE
pwdSafeModify: FALSE
'''.format(
            pwdMaxFailure=pwdMaxFailure
        )
    )

    for _ in range(pwdMaxFailure):
        assert authenticate(username=USERNAME, password='incorrect') is None
        assert 'failed to login' in caplog.text


def test_authenticate_ppolicy_pwdMaxFailure(slapd_ppolicy, settings, db, caplog):
    settings.LDAP_AUTH_SETTINGS = [
        {
            'url': [slapd_ppolicy.ldap_url],
            'basedn': 'o=ôrga',
            'use_tls': False,
            'use_controls': True,
            'use_ppolicy_controls': True,
        }
    ]

    ppolicy_authenticate_exactly_pwdMaxFailure(slapd_ppolicy, caplog)
    assert 'Account is locked' not in caplog.text
    assert authenticate(username=USERNAME, password='incorrect') is None
    assert 'Account is locked since 20' in caplog.text


def test_do_not_use_controls(slapd_ppolicy, settings, db, caplog):
    """
    Same as test_authenticate_ppolicy_pwdMaxFailure but with use_controls
    deactivated and therefore not logging when an account is locked.
    """
    settings.LDAP_AUTH_SETTINGS = [
        {
            'url': [slapd_ppolicy.ldap_url],
            'basedn': 'o=ôrga',
            'use_tls': False,
            'use_controls': False,
            'use_ppolicy_controls': False,
        }
    ]

    ppolicy_authenticate_exactly_pwdMaxFailure(slapd_ppolicy, caplog)
    assert 'account is locked' not in caplog.text
    assert authenticate(username=USERNAME, password='incorrect') is None
    # this following line is the difference with test_authenticate_ppolicy_pwdMaxFailure
    assert 'account is locked' not in caplog.text


def test_get_ppolicy_attributes(slapd_ppolicy, settings, db):
    settings.LDAP_AUTH_SETTINGS = [
        {
            'url': [slapd_ppolicy.ldap_url],
            'basedn': 'o=ôrga',
            'ppolicy_dn': 'cn=default,ou=ppolicies,o=ôrga',
            'use_tls': False,
        }
    ]

    pwdMaxAge = 1
    slapd_ppolicy.add_ldif(
        '''
dn: cn=default,ou=ppolicies,o=ôrga
cn: default
objectclass: top
objectclass: device
objectclass: pwdPolicy
objectclass: pwdPolicyChecker
pwdAttribute: userPassword
pwdMinAge: 0
pwdMaxAge: {pwdMaxAge}
pwdInHistory: 1
pwdCheckQuality: 0
pwdMinLength: 0
pwdExpireWarning: 0
pwdGraceAuthnLimit: 0
pwdLockout: TRUE
pwdLockoutDuration: 0
pwdMaxFailure: 0
pwdMaxRecordedFailure: 0
pwdFailureCountInterval: 0
pwdMustChange: FALSE
pwdAllowUserChange: TRUE
pwdSafeModify: FALSE
'''.format(
            pwdMaxAge=pwdMaxAge
        )
    )

    user = authenticate(username=USERNAME, password=UPASS)
    assert user.check_password(UPASS)
    password = 'ogutOmyetew4'
    user.set_password(password)

    time.sleep(pwdMaxAge * 3)

    conn = ldap_backend.LDAPBackend.get_connection(settings.LDAP_AUTH_SETTINGS[0])
    attributes = ldap_backend.LDAPBackend.get_ppolicy_attributes(settings.LDAP_AUTH_SETTINGS[0], conn, DN)
    assert 'pwdchangedtime' in attributes
    assert attributes['pwdmaxage'] == [str(pwdMaxAge)]


def test_authenticate_ppolicy_pwdGraceAuthnLimit(slapd_ppolicy, settings, db, caplog):
    settings.LDAP_AUTH_SETTINGS = [
        {
            'url': [slapd_ppolicy.ldap_url],
            'basedn': 'o=ôrga',
            'use_tls': False,
            'use_ppolicy_controls': True,
        }
    ]

    pwdMaxAge = 1
    pwdGraceAuthnLimit = 3
    slapd_ppolicy.add_ldif(
        '''
dn: cn=default,ou=ppolicies,o=ôrga
cn: default
objectclass: top
objectclass: device
objectclass: pwdPolicy
objectclass: pwdPolicyChecker
pwdAttribute: userPassword
pwdMinAge: 0
pwdMaxAge: {pwdMaxAge}
pwdInHistory: 1
pwdCheckQuality: 0
pwdMinLength: 0
pwdExpireWarning: 0
pwdGraceAuthnLimit: {pwdGraceAuthnLimit}
pwdLockout: TRUE
pwdLockoutDuration: 0
pwdMaxFailure: 0
pwdMaxRecordedFailure: 0
pwdFailureCountInterval: 0
pwdMustChange: FALSE
pwdAllowUserChange: TRUE
pwdSafeModify: FALSE
'''.format(
            pwdMaxAge=pwdMaxAge, pwdGraceAuthnLimit=pwdGraceAuthnLimit
        )
    )

    user = authenticate(username=USERNAME, password=UPASS)
    assert user.check_password(UPASS)
    password = 'ogutOmyetew4'
    user.set_password(password)

    time.sleep(pwdMaxAge * 3)

    assert 'used 2 time' not in caplog.text
    assert authenticate(username=USERNAME, password=password) is not None
    try:
        assert 'used 2 times' in caplog.text

        assert '3 times' not in caplog.text
        assert authenticate(username=USERNAME, password=password) is not None
        assert '3 times' in caplog.text

        assert 'last time' not in caplog.text
        assert authenticate(username=USERNAME, password=password) is not None
        assert 'last time' in caplog.text
    except AssertionError:
        # xxx pwdGraceAuthnLimit behaviour change in upper openldap versions
        assert authenticate(username=USERNAME, password=password) is not None
        assert 'used 2 times' in caplog.text


def test_authenticate_ppolicy_pwdExpireWarning(slapd_ppolicy, settings, db, caplog):
    settings.LDAP_AUTH_SETTINGS = [
        {
            'url': [slapd_ppolicy.ldap_url],
            'basedn': 'o=ôrga',
            'use_tls': False,
            'use_ppolicy_controls': True,
        }
    ]

    # Add default ppolicy with pwdMaxAge and pwdExpireWarning defined
    pwdMaxAge = 3600
    slapd_ppolicy.add_ldif(
        '''
dn: cn=default,ou=ppolicies,o=ôrga
cn: default
objectclass: top
objectclass: device
objectclass: pwdPolicy
objectclass: pwdPolicyChecker
pwdAttribute: userPassword
pwdMaxAge: {pwdMaxAge}
pwdExpireWarning: {pwdMaxAge}
'''.format(
            pwdMaxAge=pwdMaxAge
        )
    )

    user = authenticate(username=USERNAME, password=UPASS)
    assert user.check_password(UPASS)
    password = 'ogutOmyetew4'
    user.set_password(password)

    time.sleep(2)

    assert 'password will expire' not in caplog.text
    assert authenticate(username=USERNAME, password=password) is not None
    assert 'password will expire' in caplog.text


def test_login_ppolicy_pwdExpireWarning(slapd_ppolicy, settings, app, db, caplog):
    settings.LDAP_AUTH_SETTINGS = [
        {
            'url': [slapd_ppolicy.ldap_url],
            'binddn': force_str(slapd_ppolicy.root_bind_dn),
            'bindpw': force_str(slapd_ppolicy.root_bind_password),
            'basedn': 'o=ôrga',
            'use_tls': False,
            'attributes': ['carLicense'],
            'use_ppolicy_controls': True,
            'can_reset_password': True,
        }
    ]

    # Add default ppolicy with pwdMaxAge and pwdExpireWarning defined
    pwdMaxAge = 3600
    slapd_ppolicy.add_ldif(
        '''
dn: cn=default,ou=ppolicies,o=ôrga
cn: default
objectclass: top
objectclass: device
objectclass: pwdPolicy
objectclass: pwdPolicyChecker
pwdAttribute: userPassword
pwdMaxAge: {pwdMaxAge}
pwdExpireWarning: {pwdMaxAge}
'''.format(
            pwdMaxAge=pwdMaxAge
        )
    )

    list(ldap_backend.LDAPBackend.get_users())

    # reset password
    response = app.get('/login/')
    response = response.click('Reset it!')
    response.form['email'] = EMAIL
    response = response.form.submit()
    reset_email_url = utils.get_link_from_mail(mail.outbox[0])
    response = app.get(reset_email_url, status=200)
    password = 'Aa1xxxxx'
    response.form['new_password1'] = password
    response.form['new_password2'] = password
    response = response.form.submit().maybe_follow()
    response = response.click('Logout')

    time.sleep(2)

    response = app.get('/login/')
    response.form['username'] = USERNAME
    response.form['password'] = password
    response = response.form.submit('login-password-submit')
    assert '/password/change/' in response['Location']


def test_authenticate_ppolicy_pwdAllowUserChange(slapd_ppolicy, settings, db, caplog):
    settings.LDAP_AUTH_SETTINGS = [
        {
            'url': [slapd_ppolicy.ldap_url],
            'basedn': 'o=ôrga',
            'use_tls': False,
            'use_controls': True,
        }
    ]

    slapd_ppolicy.add_ldif(
        '''
dn: cn=default,ou=ppolicies,o=ôrga
cn: default
objectclass: top
objectclass: device
objectclass: pwdPolicy
pwdAttribute: userPassword
pwdMinAge: 0
pwdMaxAge: 0
pwdInHistory: 0
pwdCheckQuality: 0
pwdMinLength: 0
pwdExpireWarning: 0
pwdGraceAuthnLimit: 0
pwdLockout: TRUE
pwdLockoutDuration: 0
pwdMaxFailure: 0
pwdMaxRecordedFailure: 0
pwdFailureCountInterval: 0
pwdMustChange: FALSE
pwdAllowUserChange: FALSE
pwdSafeModify: FALSE
'''
    )

    user = authenticate(username=USERNAME, password=UPASS)
    with pytest.raises(PasswordChangeError):
        user.set_password('ogutOmyetew4')
        assert 'STRONG_AUTH_REQUIRED' in caplog.text


def test_ou_selector(slapd, settings, app, ou1):
    settings.LDAP_AUTH_SETTINGS = [
        {
            'url': [slapd.ldap_url],
            'binddn': force_str(DN),
            'bindpw': PASS,
            'basedn': 'o=ôrga',
            'ou_slug': ou1.slug,
            'use_tls': False,
        }
    ]
    LoginPasswordAuthenticator.objects.update(include_ou_selector=True)

    # Check login to the wrong ou does not work
    response = app.get('/login/')
    response.form.set('username', USERNAME)
    response.form.set('password', PASS)
    response.form.set('ou', str(get_default_ou().pk))
    response = response.form.submit(name='login-password-submit')
    assert response.pyquery('.errornotice')
    assert '_auth_user_id' not in app.session

    # Check login to the proper ou works
    response = app.get('/login/')
    response.form.set('username', USERNAME)
    response.form.set('password', PASS)
    response.form.set('ou', str(ou1.pk))
    response = response.form.submit(name='login-password-submit').follow()
    assert '_auth_user_id' in app.session


def test_ou_selector_default_ou(slapd, settings, app, ou1):
    settings.LDAP_AUTH_SETTINGS = [
        {
            'url': [slapd.ldap_url],
            'binddn': force_str(DN),
            'bindpw': PASS,
            'basedn': 'o=ôrga',
            'use_tls': False,
        }
    ]
    LoginPasswordAuthenticator.objects.update(include_ou_selector=True)

    # Check login to the wrong ou does not work
    response = app.get('/login/')
    response.form.set('username', USERNAME)
    response.form.set('password', PASS)
    response.form.set('ou', str(ou1.pk))
    response = response.form.submit(name='login-password-submit')
    assert response.pyquery('.errornotice')
    assert '_auth_user_id' not in app.session

    # Check login to the proper ou works
    response = app.get('/login/')
    response.form.set('username', USERNAME)
    response.form.set('password', PASS)
    response.form.set('ou', str(get_default_ou().pk))
    response = response.form.submit(name='login-password-submit').follow()
    assert '_auth_user_id' in app.session


@mock.patch.dict(os.environ, {'TERM': 'xterm-256color'})
@mock.patch('authentic2.backends.ldap_backend.logging.StreamHandler.emit')
def test_sync_ldap_users_verbosity(mocked_emit, slapd, settings, app, db):
    management.call_command('sync-ldap-users')
    assert not mocked_emit.call_count

    management.call_command('sync-ldap-users', verbosity=2)
    assert mocked_emit.call_count


def test_sync_ldap_users(slapd, settings, app, db, caplog, nologtoconsole):
    caplog.set_level('INFO')

    conn = slapd.get_connection_admin()
    entryuuid = conn.search_s('o=ôrga', ldap.SCOPE_SUBTREE, f'(uid={UID})', ['entryUUID'])[0][1]['entryUUID'][
        0
    ].decode()
    management.call_command('sync-ldap-users')
    assert caplog.records[0].message == 'No LDAP server configured.'

    caplog.clear()
    settings.LDAP_AUTH_SETTINGS = [
        {
            'url': [slapd.ldap_url],
            'basedn': 'o=ôrga',
            'use_tls': False,
            'user_attributes': [
                {
                    'from_ldap': 'l',
                    'to_user': 'locality',
                },
            ],
        }
    ]

    # create a locality attribute
    models.Attribute.objects.create(
        label='locality',
        name='locality',
        kind='string',
        required=False,
        user_visible=True,
        user_editable=False,
        asked_on_registration=False,
        multiple=False,
    )

    assert User.objects.count() == 0
    management.call_command('sync-ldap-users', verbosity=2)
    assert caplog.records[0].message == 'Synchronising users from realm "ldap"'
    assert caplog.records[1].message == 'Binding to server %s (anonymously)' % slapd.ldap_url
    assert caplog.records[2].message == (
        (
            "Created user etienne.michu@ldap (uuid %s) from dn=cn=Étienne Michu,o=ôrga, uid=['%s'], "
            "sn=['Michu'], givenname=['Étienne'], l=['Paris'], mail=['etienne.michu@example.net'], entryuuid=['%s']"
        )
        % (User.objects.order_by('id').first().uuid, USERNAME, entryuuid)
    )
    assert caplog.records[-1].message == 'Search for (|(mail=*)(uid=*)) returned 6 users.'

    assert User.objects.count() == 6
    assert all(user.first_name == 'Étienne' for user in User.objects.all())
    assert all(user.attributes.first_name == 'Étienne' for user in User.objects.all())
    assert all(user.last_name == 'Michu' for user in User.objects.all())
    assert all(user.attributes.last_name == 'Michu' for user in User.objects.all())
    assert all(
        user.attributes.locality == 'Paris' or user.attributes.locality.startswith('locality')
        for user in User.objects.all()
    )
    assert all(
        [
            user.userexternalid_set.first().external_id
            == urllib.parse.quote(user.username.split('@')[0].encode('utf-8'))
            for user in User.objects.all()
        ]
    )

    caplog.clear()
    User.objects.update(first_name='John')
    management.call_command('sync-ldap-users', verbosity=3)
    assert caplog.records[2].message == (
        "Updated user etienne.michu@ldap (uuid %s) from dn=cn=Étienne Michu,o=ôrga, uid=['%s'], "
        "sn=['Michu'], givenname=['Étienne'], l=['Paris'], mail=['etienne.michu@example.net'], entryuuid=['%s']"
    ) % (User.objects.order_by('id').first().uuid, USERNAME, entryuuid)


def test_update_mapped_roles_manageable_members(slapd, settings, app, db, caplog, nologtoconsole):
    caplog.set_level('INFO')

    # new roles are mapped, they shouldn't be assignable anymore
    Role.objects.create(name='LdapRole1', can_manage_members=True)
    Role.objects.create(name='LdapRole2', can_manage_members=True)
    # roles are unmapped, they should become assignable again
    Role.objects.create(name='LdapRole3', can_manage_members=False)
    Role.objects.create(name='LdapRole4', can_manage_members=False)

    settings.LDAP_AUTH_SETTINGS = [
        {
            'url': [slapd.ldap_url],
            'basedn': 'o=ôrga',
            'use_tls': False,
            'group_to_role_mapping': [
                ['cn=GrouP1,o=ôrga', ['LdapRole1']],
                ['cn=GrouP2,o=ôrga', ['LdapRole2']],
                # unknown role, should not be create
                ['cn=GrouP2,o=ôrga', ['LdapRole5']],
            ],
        }
    ]

    management.call_command('update-ldap-mapped-roles-list')

    assert set(
        Role.objects.filter(name__startswith='LdapRole', can_manage_members=False).values_list(
            'name', flat=True
        )
    ) == {'LdapRole1', 'LdapRole2'}
    assert set(
        Role.objects.filter(name__startswith='LdapRole', can_manage_members=True).values_list(
            'name', flat=True
        )
    ) == {'LdapRole3', 'LdapRole4'}
    assert not Role.objects.filter(name='LdapRole5')
    assert len(caplog.messages) == 1
    assert "couldn't retrieve role 'LdapRole5' during mapping list update" in caplog.messages[0]


def test_get_users_select_realm(slapd, settings, db, caplog, nologtoconsole):
    settings.LDAP_AUTH_SETTINGS = [
        {
            'url': [slapd.ldap_url],
            'realm': 'first',
            'basedn': 'o=ôrga',
            'use_tls': False,
        },
        {
            'url': [slapd.ldap_url],
            'realm': 'second',
            'basedn': 'o=ôrga',
            'use_tls': False,
        },
    ]
    management.call_command('sync-ldap-users', verbosity=2)
    assert 'Synchronising users from realm "first"' in caplog.messages
    assert 'Synchronising users from realm "second"' in caplog.messages

    caplog.clear()
    management.call_command('sync-ldap-users', verbosity=2, realm='second')
    assert 'Synchronising users from realm "first"' not in caplog.messages
    assert 'Synchronising users from realm "second"' in caplog.messages


def test_alert_on_wrong_user_filter(slapd, settings, client, db, caplog):
    settings.LDAP_AUTH_SETTINGS = [
        {
            'url': [slapd.ldap_url],
            'basedn': 'o=ôrga',
            'use_tls': False,
            'user_filter': '(&(objectClass=user)(sAMAccountName=*)',  # wrong
        }
    ]
    with utils.check_log(caplog, "account name authentication filter doesn't contain '%s'"):
        client.post(
            '/login/', {'login-password-submit': '1', 'username': USERNAME, 'password': PASS}, follow=True
        )


def test_get_attributes(slapd, settings, db, rf):
    settings.LDAP_AUTH_SETTINGS = [
        {
            'url': [slapd.ldap_url],
            'basedn': 'o=ôrga',
            'use_tls': False,
            'attributes': ['uid', 'carLicense'],
        }
    ]
    user = authenticate(username=USERNAME, password=UPASS)
    assert user
    assert dict(user.get_attributes(object(), {}), entryuuid=None) == {
        'dn': 'cn=étienne michu,o=\xf4rga',
        'givenname': ['Étienne'],
        'mail': ['etienne.michu@example.net'],
        'sn': ['Michu'],
        'uid': [USERNAME],
        'carlicense': ['123445ABC'],
        'entryuuid': None,
    }
    # simulate LDAP down
    slapd.stop()
    assert dict(user.get_attributes(object(), {}), entryuuid=None) == {
        'dn': 'cn=étienne michu,o=\xf4rga',
        'givenname': ['\xc9tienne'],
        'mail': ['etienne.michu@example.net'],
        'sn': ['Michu'],
        'uid': [USERNAME],
        'carlicense': ['123445ABC'],
        'entryuuid': None,
    }
    assert not user.check_password(UPASS)
    # simulate LDAP come back up
    slapd.start()
    assert user.check_password(UPASS)
    # modify LDAP record and check attributes are updated
    conn = slapd.get_connection_admin()
    ldif = [(ldap.MOD_REPLACE, 'sn', [b'Micho'])]
    conn.modify_s(DN, ldif)
    assert dict(user.get_attributes(object(), {}), entryuuid=None) == {
        'dn': 'cn=étienne michu,o=\xf4rga',
        'givenname': ['\xc9tienne'],
        'mail': ['etienne.michu@example.net'],
        'sn': ['Micho'],
        'uid': [USERNAME],
        'carlicense': ['123445ABC'],
        'entryuuid': None,
    }


@pytest.mark.django_db
@pytest.mark.parametrize('cache_close_after', (None, 2, 5, 6, 11, 5000))
def test_get_extra_attributes(slapd, settings, client, cache_errors, cache_close_after):
    with cache_errors(cache_close_after):
        settings.LDAP_AUTH_SETTINGS = [
            {
                'url': [slapd.ldap_url],
                'basedn': 'o=ôrga',
                'use_tls': False,
                'groupstaff': ['cn=group1,o=ôrga'],
                'attributes': ['uid'],
                'extra_attributes': {
                    'orga': {
                        'loop_over_attribute': 'o',
                        'filter': '(&(objectclass=organization)(o={item}))',
                        'basedn': 'o=ôrga',
                        'scope': 'sub',
                        'mapping': {
                            'id': 'o',
                            'street': 'postalAddress',
                            'city': 'l',
                            'postal_code': 'postalCode',
                        },
                        'serialization': 'json',
                    }
                },
            }
        ]
        response = client.post(
            '/login/', {'login-password-submit': '1', 'username': USERNAME, 'password': PASS}, follow=True
        )
        user = response.context['user']
        fetched_attrs = user.get_attributes(object(), {})
        assert UID in fetched_attrs.get('uid')
        assert 'orga' in fetched_attrs
        orgas = json.loads(fetched_attrs.get('orga'))
        assert isinstance(orgas, list)
        assert len(orgas) == 2
        assert {'id': EO_O, 'street': EO_STREET, 'city': EO_CITY, 'postal_code': EO_POSTALCODE} in orgas
        assert {'id': EE_O, 'street': EE_STREET, 'city': EE_CITY, 'postal_code': EE_POSTALCODE} in orgas


def test_config_to_lowercase(db):
    config = {
        'fname_field': 'givenName',
        'lname_field': 'surName',
        'email_field': 'EMAIL',
        'attributes': ['ZoB', 'CoiN'],
        'mandatory_attributes_values': {
            'XXX': ['A'],
        },
        'member_of_attribute': 'memberOf',
        'group_mapping': [
            ['CN=coin,OU=Groups,DC=coin,DC=Fr', ['Group 1']],
        ],
        'group_to_role_mapping': [
            ['CN=coin,OU=Groups,DC=coin,DC=Fr', ['Group 1']],
        ],
        'attribute_mappings': [
            ['XXX', 'YYY'],
        ],
        'external_id_tuples': [['A', 'B', 'C']],
        'user_attributes': [
            {
                'from_ldap': 'ABC',
                'to_user': 'Phone',
            }
        ],
    }

    config_normalized = dict(config, url='ldap://example.net', basedn='dc=coin,dc=fr')
    ldap_backend.LDAPBackend.update_default(config_normalized)

    # only keep keys we are interested in
    for key in list(config_normalized):
        if key not in config:
            del config_normalized[key]

    assert config_normalized == {
        'fname_field': 'givenname',
        'lname_field': 'surname',
        'email_field': 'email',
        'attributes': ['zob', 'coin'],
        'mandatory_attributes_values': {'xxx': ['A']},
        'member_of_attribute': 'memberof',
        'group_mapping': [['cn=coin,ou=groups,dc=coin,dc=fr', ['Group 1']]],
        'group_to_role_mapping': [['cn=coin,ou=groups,dc=coin,dc=fr', ['Group 1']]],
        'attribute_mappings': [
            ['xxx', 'yyy'],
        ],
        'external_id_tuples': [
            ['a', 'b', 'c'],
        ],
        'user_attributes': [
            {
                'from_ldap': 'abc',
                'to_user': 'Phone',
            }
        ],
    }


def test_switch_user_ldap_user(slapd, settings, app, db, caplog):
    caplog.set_level(logging.DEBUG)  # force pytest to reset log level after test

    settings.LDAP_AUTH_SETTINGS = [
        {
            'url': [slapd.ldap_url],
            'binddn': force_str(slapd.root_bind_dn),
            'bindpw': force_str(slapd.root_bind_password),
            'basedn': 'o=ôrga',
            'use_tls': False,
            'attributes': ['carLicense'],
        }
    ]
    # get all users
    management.call_command('sync-ldap-users', verbosity=2)

    user = User.objects.get(username=USERNAME + '@ldap')
    url = switch_user.build_url(user)
    response = app.get(url).follow()
    assert app.session['_auth_user_backend'] == 'authentic2.backends.ldap_backend.LDAPBackendPasswordLost'
    template_user = response.context['user']
    assert 'carlicense' in template_user.get_attributes(object(), {})


def test_build_external_id(slapd, settings, client, db):
    backend = ldap_backend.LDAPBackend()

    assert backend.build_external_id(['uid'], {'uid': 'john.doe'}) == 'john.doe'
    assert backend.build_external_id(['uid'], {}) is None


def test_manager_user_sidebar(slapd, settings, client, db, app, superuser):
    settings.LDAP_AUTH_SETTINGS = [
        {
            'url': [slapd.ldap_url],
            'basedn': 'o=ôrga',
            'use_tls': False,
        }
    ]

    # create users as a side effect
    list(ldap_backend.LDAPBackend.get_users())
    user = User.objects.get(username='etienne.michu@ldap')

    utils.login(app, superuser, '/manage/')
    resp = app.get('/manage/users/%s/' % user.pk)
    assert 'LDAP' in resp.text
    assert 'server "ldap"' in resp.text
    assert 'external_id etienne.michu' in resp.text

    user.userexternalid_set.all().delete()
    resp = app.get('/manage/users/%s/' % user.pk)
    assert 'LDAP' not in resp.text


@pytest.mark.parametrize(
    'exception',
    (
        (ldap.CONNECT_ERROR, 'ldap connect error'),
        (ldap.TIMEOUT, 'ldap timeout'),
        (ldap.SERVER_DOWN, 'ldap server down'),
    ),
)
def test_user_journal_login_failure(slapd, settings, client, db, monkeypatch, exception):
    settings.LDAP_AUTH_SETTINGS = [
        {
            'url': [slapd.ldap_url],
            'basedn': 'o=ôrga',
            'use_tls': False,
            'attributes': ['jpegPhoto'],
        }
    ]

    # create ldap user
    client.post(
        '/login/', {'login-password-submit': '1', 'username': USERNAME, 'password': PASS}, follow=True
    )

    def patched_process_bind_controls(cls, request, block, conn, authz_id, ctrls):
        raise exception[0]('oops')

    monkeypatch.setattr(
        ldap_backend.LDAPBackend,
        'process_bind_controls',
        patched_process_bind_controls,
    )
    client.post(
        '/login/', {'login-password-submit': '1', 'username': USERNAME, 'password': PASS}, follow=True
    )
    user = ldap_backend.LDAPUser.objects.get(username='%s@ldap' % UID)
    event = utils.assert_event('user.login.failure', user=user, username=UID, reason=exception[1])
    assert (
        event.message
        == f'login failure with username "etienne.michu" on authenticator Password (reason: {exception[1]})'
    )


@pytest.mark.parametrize(
    'exception',
    (
        ldap.CONNECT_ERROR,
        ldap.TIMEOUT,
        ldap.SERVER_DOWN,
        ldap.UNAVAILABLE,
    ),
)
def test_user_authn_form_failure_explanation(slapd, settings, client, db, monkeypatch, exception, app):
    settings.LDAP_AUTH_SETTINGS = [
        {
            'url': [slapd.ldap_url],
            'basedn': 'o=ôrga',
            'use_tls': False,
            'attributes': ['jpegPhoto'],
        }
    ]

    # create ldap user
    client.post(
        '/login/', {'login-password-submit': '1', 'username': USERNAME, 'password': PASS}, follow=True
    )

    def patched_process_bind_controls(cls, request, block, conn, authz_id, ctrls):
        raise exception('oops')

    monkeypatch.setattr(
        ldap_backend.LDAPBackend,
        'process_bind_controls',
        patched_process_bind_controls,
    )
    resp = app.get('/login/')
    form = resp.form
    form['username'] = USERNAME
    form['password'] = PASS
    resp = resp.form.submit('login-password-submit')
    assert {
        ldap.CONNECT_ERROR: 'error while connecting to server ldap',
        ldap.TIMEOUT: 'connection to server ldap timed out',
        ldap.SERVER_DOWN: 'server ldap is down',
        ldap.UNAVAILABLE: 'server ldap\'s system agent (DSA) is unavailable',
    }.get(exception) in resp.pyquery('ul.messages li.warning').text()
    assert '_auth_user_id' not in app.session


def test_technical_info_ldap(app, admin, superuser, slapd, settings, monkeypatch):
    settings.LDAP_AUTH_SETTINGS = [
        {
            'url': [slapd.ldap_url],
            'binddn': force_str('cn=%s,o=ôrga' % escape_dn_chars('Étienne Michu')),
            'bindpw': PASS,
            'basedn': 'o=ôrga',
            'use_tls': False,
        }
    ]

    utils.login(app, admin, 'a2-manager-homepage')
    app.get(reverse('a2-manager-tech-info'), status=403)
    utils.logout(app)

    resp = utils.login(app, superuser, 'a2-manager-tech-info')
    ldap_config_text = resp.pyquery('div#a2-manager-tech-info-ldap-list').text()

    assert 'Base ldapsearch command' in ldap_config_text
    assert 'ldapsearch -v -H ldapi://' in ldap_config_text
    assert '-D "cn=Étienne Michu,o=ôrga"' in ldap_config_text
    assert f'-w "{PASS}"' in ldap_config_text
    assert '-b "o=ôrga"' in ldap_config_text
    assert '"(|(mail=*)(uid=*))"' in ldap_config_text

    options = [
        'active_directory',
        'attribute_mappings',
        'attributes',
        'basedn',
        'bind_with_username',
        'binddn',
        'bindpw',
        'bindsasl',
        'cacertdir',
        'cacertfile',
        'can_reset_password',
        'certfile',
        'clean_external_id_on_update',
        'connect_with_user_credentials',
        'create_group',
        'disable_update',
        'email_field',
        'external_id_tuples',
        'extra_attributes',
        'fname_field',
        'global_ldap_options',
        'group_basedn',
        'group_filter',
        'group_mapping',
        'group_to_role_mapping',
        'groupactive',
        'groupstaff',
        'groupsu',
        'is_staff',
        'is_superuser',
        'keep_password',
        'keep_password_in_session',
        'keyfile',
        'ldap_options',
        'limit_to_realm',
        'lname_field',
        'lookups',
        'mandatory_attributes_values',
        'member_of_attribute',
        'multimatch',
        'ou_slug',
        'ppolicy_dn',
        'realm',
        'referrals',
        'replicas',
        'require_cert',
        'set_mandatory_groups',
        'set_mandatory_roles',
        'shuffle_replicas',
        'sync_ldap_users_filter',
        'timeout',
        'update_username',
        'url',
        'use_controls',
        'use_first_url_for_external_id',
        'use_password_modify',
        'use_tls',
        'user_attributes',
        'user_basedn',
        'user_can_change_password',
        'user_dn_template',
        'user_filter',
        'username_template',
    ]

    for opt in options:
        assert opt in ldap_config_text

    assert 'LDAPTLS_REQCERT' not in ldap_config_text
    settings.LDAP_AUTH_SETTINGS[0]['require_cert'] = 'never'
    resp = app.get(reverse('a2-manager-tech-info'))
    ldap_config_text = resp.pyquery('div#a2-manager-tech-info-ldap-list').text()

    assert 'LDAPTLS_REQCERT=never ldapsearch' in ldap_config_text

    # mock a buggy connection
    monkeypatch.setattr(
        ldap_backend.LDAPBackend,
        'bind',
        mock.Mock(return_value=(False, 'some buggy connection error message')),
    )
    resp = app.get(reverse('a2-manager-tech-info'))
    ldap_config_text = resp.pyquery('div#a2-manager-tech-info-ldap-list').text()

    assert 'Base ldapsearch command' in ldap_config_text
    assert 'Error while attempting to connect to LDAP server' in ldap_config_text
    assert 'some buggy connection error message' in ldap_config_text
    assert slapd.ldap_url in ldap_config_text
    for opt in options:
        assert opt in ldap_config_text


def test_technical_info_ldap_unavailable(app, superuser, tls_slapd, settings, monkeypatch):
    settings.LDAP_AUTH_SETTINGS = [
        {
            'url': [tls_slapd.ldap_url],
            'binddn': force_str('cn=%s,o=ôrga' % escape_dn_chars('Étienne Michu')),
            'bindpw': PASS,
            'basedn': 'o=ôrga',
            'use_tls': True,
        }
    ]

    def patched_start_tls_s(cls):
        raise ldap.UNAVAILABLE('oops')

    monkeypatch.setattr(
        ldap_backend.LDAPObject,
        'start_tls_s',
        patched_start_tls_s,
    )
    utils.login(app, superuser, 'a2-manager-homepage')
    resp = app.get(reverse('a2-manager-tech-info'))
    ldap_config_text = resp.pyquery('div#a2-manager-tech-info-ldap-list').text()
    assert 'Error while attempting to connect to LDAP server' in ldap_config_text
    assert 'system agent (DSA) is unavailable' in ldap_config_text


class TestLookup:
    @pytest.fixture
    def settings(self, settings, slapd):
        settings.LDAP_AUTH_SETTINGS = [
            {
                'url': [slapd.ldap_url],
                'basedn': 'o=ôrga',
                'use_tls': False,
            }
        ]
        return settings

    @pytest.fixture
    def backend(self):
        return ldap_backend.LDAPBackend()

    def test_by_email(self, backend, slapd, settings, client, db):
        user = User.objects.create(email=EMAIL, ou=get_default_ou())
        assert backend.authenticate(None, username=USERNAME, password=PASS) == user
        assert models.UserExternalId.objects.get(user=user, source='ldap', external_id=UID)
        user.email = ''
        user.save()
        # if email is changed
        auth_user = backend.authenticate(None, username=USERNAME, password=PASS)
        assert auth_user == user
        assert auth_user.email == EMAIL

    def test_by_email_only(self, backend, slapd, settings, client, db):
        settings.LDAP_AUTH_SETTINGS[0]['lookups'] = ['email']
        user = User.objects.create(email=EMAIL, ou=get_default_ou())
        assert backend.authenticate(None, username=USERNAME, password=PASS) == user
        assert not models.UserExternalId.objects.exists()
        user.email = ''
        user.save()
        new_user = backend.authenticate(None, username=EMAIL, password=PASS)
        assert new_user and new_user != user
        assert new_user.email == EMAIL

    def test_by_username(self, backend, slapd, settings, client, db):
        user = User.objects.create(username=UID, ou=get_default_ou())
        assert backend.authenticate(None, username=EMAIL, password=PASS) == user
        assert models.UserExternalId.objects.get(user=user, source='ldap', external_id=UID)
        user.username = ''
        user.save()
        auth_user = backend.authenticate(None, username=EMAIL, password=PASS)
        assert auth_user == user
        assert auth_user.username == ''
        settings.LDAP_AUTH_SETTINGS[0]['update_username'] = True
        auth_user = backend.authenticate(None, username=EMAIL, password=PASS)
        assert auth_user == user
        assert auth_user.username == f'{UID}@ldap'

    def test_by_guid_migration(self, backend, slapd, settings, client, db):
        settings.LDAP_AUTH_SETTINGS[0]['lookups'] = ['external_id']
        assert backend.authenticate(None, username=USERNAME, password=PASS)
        assert User.objects.count() == 1
        user_external_id = models.UserExternalId.objects.get()
        assert user_external_id.external_id
        assert not user_external_id.external_guid

        settings.LDAP_AUTH_SETTINGS[0]['lookups'] = ['guid', 'external_id']
        assert backend.authenticate(None, username=USERNAME, password=PASS)
        assert User.objects.count() == 1
        user_external_id = models.UserExternalId.objects.get()
        assert user_external_id.external_id
        assert user_external_id.external_guid

    def test_by_guid_only(self, backend, slapd, settings, client, db):
        settings.LDAP_AUTH_SETTINGS[0]['lookups'] = ['guid']
        assert backend.authenticate(None, username=USERNAME, password=PASS)
        assert User.objects.count() == 1
        user_external_id = models.UserExternalId.objects.get()
        assert not user_external_id.external_id
        assert user_external_id.external_guid

        assert backend.authenticate(None, username=USERNAME, password=PASS)
        assert User.objects.count() == 1
        user_external_id = models.UserExternalId.objects.get()
        assert not user_external_id.external_id
        assert user_external_id.external_guid

    def test_by_guid_only_objectguid(self, backend, slapd, settings, client, db, monkeypatch):
        slapd.add_ldif(OBJECTGUID_SCHEMA)
        conn = slapd.get_connection_admin()
        ldif = [
            (ldap.MOD_ADD, 'objectClass', b'objectWithObjectGuid'),
            (ldap.MOD_ADD, 'objectguid', OBJECTGUID_RAW),
        ]
        conn.modify_s(DN, ldif)
        conn.unbind_s()

        settings.LDAP_AUTH_SETTINGS[0]['lookups'] = ['guid']
        monkeypatch.setattr(ldap_backend, 'USUAL_GUID_ATTRIBUTES', ['objectguid'])
        assert backend.authenticate(None, username=USERNAME, password=PASS)
        assert User.objects.count() == 1
        user_external_id = models.UserExternalId.objects.get()
        assert not user_external_id.external_id
        assert user_external_id.external_guid.bytes == OBJECTGUID_RAW

    def test_duplicate_external_id(self, backend, slapd, settings, client, db):
        user1 = backend.authenticate(None, username=USERNAME, password=PASS)
        assert user1

        assert User.objects.count() == 1

        # change uid
        conn = slapd.get_connection_admin()
        ldif = [
            # make a typo in the username
            (ldap.MOD_REPLACE, 'uid', b'etienne.mchu'),
        ]
        conn.modify_s(DN, ldif)
        conn.unbind_s()
        # simulate change of GUID (cannot change it on slapd)
        models.UserExternalId.objects.update(external_guid=UUID)

        user2 = backend.authenticate(None, username='etienne.mchu', password=PASS)
        assert user2

        assert User.objects.count() == 2
        assert models.UserExternalId.objects.count() == 2
        assert models.UserExternalId.objects.filter(external_id__isnull=False).count() == 2

        # fix typo on uid
        conn = slapd.get_connection_admin()
        ldif = [
            # make a typo in the username
            (ldap.MOD_REPLACE, 'uid', b'etienne.michu'),
        ]
        conn.modify_s(DN, ldif)
        conn.unbind_s()

        assert backend.authenticate(None, username=USERNAME, password=PASS) == user2

        assert User.objects.count() == 2
        assert models.UserExternalId.objects.count() == 2
        assert models.UserExternalId.objects.filter(external_id__isnull=True).get().user == user1
        assert models.UserExternalId.objects.filter(external_id__isnull=False).get().user == user2


def test_build_external_id_failure_authenticate(db, rf, slapd, settings, caplog):
    caplog.set_level('ERROR')
    settings.LDAP_AUTH_SETTINGS = [
        {
            'url': [slapd.ldap_url],
            'basedn': 'o=ôrga',
            'use_tls': False,
            'external_id_tuples': [
                ['missing'],
            ],
            'lookups': ['external_id', 'username'],
        }
    ]
    request = rf.get('/login/')
    request._messages = mock.Mock()
    backend = ldap_backend.LDAPBackend()
    assert backend.authenticate(request, username=USERNAME, password=PASS) is None
    assert request._messages.add.call_count == 1
    assert request._messages.add.call_args[0] == (
        40,
        'LDAP configuration is broken, please contact your administrator',
        '',
    )
    assert len(caplog.records) == 1
    assert caplog.records[0].levelname == 'ERROR'
    assert 'unable to build an user_external_id' in caplog.records[0].message


def test_build_external_id_failure_get_users(db, rf, slapd, settings, caplog):
    caplog.set_level('ERROR')
    settings.LDAP_AUTH_SETTINGS = [
        {
            'url': [slapd.ldap_url],
            'basedn': 'o=ôrga',
            'use_tls': False,
            'external_id_tuples': [
                ['missing'],
            ],
            'lookups': ['external_id', 'username'],
        }
    ]
    backend = ldap_backend.LDAPBackend()
    users = list(backend.get_users())
    assert not users
    assert len(caplog.records) == 6
    assert all(record.levelname == 'ERROR' for record in caplog.records)
    assert all('unable to build an user_external_id' in record.message for record in caplog.records)


def test_mandatory_role_slug_ambiguity_fallback_on_default_ou(db, rf, slapd, client, settings, caplog, ou1):
    settings.LDAP_AUTH_SETTINGS = [
        {
            'url': [slapd.ldap_url],
            'basedn': 'o=ôrga',
            'use_tls': False,
            'attributes': ['jpegPhoto'],
            'set_mandatory_roles': ['ambiguous-role'],
        }
    ]

    default_ou = get_default_ou()
    Role.objects.create(slug='ambiguous-role', ou=default_ou)
    Role.objects.create(slug='ambiguous-role', ou=ou1)
    result = client.post(
        '/login/', {'login-password-submit': '1', 'username': USERNAME, 'password': PASS}, follow=True
    )
    assert result.status_code == 200
    assert force_bytes('Étienne Michu') in result.content
    assert User.objects.count() == 1
    user = User.objects.get()
    role = user.roles.get(slug='ambiguous-role')
    assert role.ou == default_ou


def test_mandatory_role_name_ambiguity_fallback_on_default_ou(db, rf, slapd, client, settings, caplog, ou1):
    settings.LDAP_AUTH_SETTINGS = [
        {
            'url': [slapd.ldap_url],
            'basedn': 'o=ôrga',
            'use_tls': False,
            'attributes': ['jpegPhoto'],
            'set_mandatory_roles': ['Ambiguous role'],
        }
    ]

    default_ou = get_default_ou()
    Role.objects.create(name='Ambiguous role', ou=default_ou)
    Role.objects.create(name='Ambiguous role', ou=ou1)
    result = client.post(
        '/login/', {'login-password-submit': '1', 'username': USERNAME, 'password': PASS}, follow=True
    )
    assert result.status_code == 200
    assert force_bytes('Étienne Michu') in result.content
    assert User.objects.count() == 1
    user = User.objects.get()
    role = user.roles.get(name='Ambiguous role')
    assert role.ou == default_ou


def test_authenticate_no_authentication(slapd, settings, client, db):
    settings.LDAP_AUTH_SETTINGS = [
        {
            'url': [slapd.ldap_url],
            'basedn': 'o=ôrga',
            'use_tls': False,
            'attributes': ['jpegPhoto'],
            'authentication': False,
        }
    ]
    result = client.post(
        '/login/', {'login-password-submit': '1', 'username': USERNAME, 'password': PASS}, follow=True
    )
    assert result.status_code == 200
    assert force_bytes('Étienne Michu') not in result.content
    assert User.objects.count() == 0


def test_get_users_no_provisionning(slapd, settings, db, monkeypatch, caplog):
    settings.LDAP_AUTH_SETTINGS = [
        {
            'url': [slapd.ldap_url],
            'basedn': 'o=ôrga',
            'use_tls': False,
            'create_group': True,
            'group_mapping': [
                ['cn=group2,o=ôrga', ['Group2']],
            ],
            'group_filter': '(&(memberUid={uid})(objectClass=posixGroup))',
            'group_to_role_mapping': [
                ['cn=unknown,o=dn', ['Role2']],
            ],
            'lookups': ['external_id', 'username'],
            'provisionning': False,
        }
    ]
    assert Group.objects.count() == 0
    assert User.objects.count() == 0
    users = list(ldap_backend.LDAPBackend.get_users())
    assert len(users) == 0
    assert User.objects.count() == 0
    assert Group.objects.count() == 0


def test_deactivate_orphaned_users_when_no_provisionning(slapd, settings, client, db, app, superuser):
    settings.LDAP_AUTH_SETTINGS = [
        {
            'url': [slapd.ldap_url],
            'basedn': 'o=ôrga',
            'use_tls': False,
        }
    ]
    utils.login(app, superuser)

    # create users as a side effect
    users = list(ldap_backend.LDAPBackend.get_users())
    block = settings.LDAP_AUTH_SETTINGS[0]
    assert (
        ldap_backend.UserExternalId.objects.filter(user__is_active=False, source=block['realm']).count() == 0
    )
    resp = app.get('/manage/users/%s/' % users[0].pk)
    assert 'Deactivated' not in resp.text

    conn = slapd.get_connection_admin()
    conn.delete_s(DN)

    settings.LDAP_AUTH_SETTINGS = [
        {
            'url': [slapd.ldap_url],
            'basedn': 'o=ôrga',
            'use_tls': False,
            'provisionning': False,
        }
    ]

    ldap_backend.LDAPBackend.deactivate_orphaned_users()

    deactivated_user = ldap_backend.UserExternalId.objects.get(
        user__is_active=False,
        source=block['realm'],
        user__deactivation__isnull=False,
        user__deactivation_reason__startswith='ldap-',
    )
    utils.assert_event(
        'manager.user.deactivation',
        target_user=deactivated_user.user,
        reason='ldap-not-present',
        origin=slapd.ldap_url,
    )
    resp = app.get('/manage/users/%s/' % deactivated_user.user.pk)
    assert 'Deactivated' in resp.text
    assert 'associated LDAP account does not exist anymore' in resp.text


def test_good_user_wrong_password(slapd, transactional_db, settings, client):
    settings.LDAP_AUTH_SETTINGS = [
        {
            'url': [slapd.ldap_url],
            'binddn': force_str(DN),
            'bindpw': PASS,
            'basedn': 'o=ôrga',
            'use_tls': False,
        }
    ]
    result = client.post(
        '/login/', {'login-password-submit': '1', 'username': USERNAME, 'password': 'wrong'}, follow=True
    )
    assert result.status_code == 200
