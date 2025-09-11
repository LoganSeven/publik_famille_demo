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
import collections
import datetime
import hashlib
import json
import logging
import os
import random
import socket
import ssl
import time
import urllib.parse
import uuid

import ldap
import ldap.modlist
import ldap.sasl
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.core.exceptions import ImproperlyConfigured
from django.db.models import Q
from django.db.transaction import atomic
from django.utils.dateformat import format as dateformat
from django.utils.encoding import force_bytes, force_str
from django.utils.translation import gettext as _
from django.utils.translation import ngettext
from ldap.controls import DecodeControlTuples, SimplePagedResultsControl, ppolicy
from ldap.dn import escape_dn_chars
from ldap.filter import filter_format
from ldap.ldapobject import ReconnectLDAPObject as NativeLDAPObject

from authentic2.a2_rbac.models import OrganizationalUnit, Role
from authentic2.a2_rbac.utils import get_default_ou
from authentic2.backends import is_user_authenticable
from authentic2.compat_lasso import lasso
from authentic2.ldap_utils import FilterFormatter
from authentic2.middleware import StoreRequestMiddleware
from authentic2.models import Lock, UserExternalId
from authentic2.user_login_failure import user_login_failure, user_login_success
from authentic2.utils import crypto
from authentic2.utils.misc import PasswordChangeError, get_password_authenticator, to_list

# code originaly copied from by now merely inspired by
# http://www.amherst.k12.oh.us/django-ldap.html


log = logging.getLogger(__name__)

User = get_user_model()

DEFAULT_CA_BUNDLE = ''

CA_BUNDLE_PATHS = [
    '/etc/pki/tls/certs/ca-bundle.crt',  # RHEL/Fedora
    '/etc/ssl/certs/ca-certificates.crt',  # Debian
    '/var/lib/ca-certificates/ca-bundle.pem',  # OpenSuse
]

USUAL_GUID_ATTRIBUTES = ['entryuuid', 'objectguid', 'nsuniqueid']


class UserCreationError(Exception):
    pass


# Select a system certificate store
for bundle_path in CA_BUNDLE_PATHS:
    if os.path.exists(bundle_path):
        DEFAULT_CA_BUNDLE = bundle_path
        break


@to_list
def filter_non_unicode_values(atvs):
    for atv in atvs:
        try:
            yield atv.decode('utf-8')
        except UnicodeDecodeError:
            pass


def ldap_error_str(e):
    return repr(e) if not getattr(e, 'desc', None) else '%r - %s' % (e, e.desc)


class LDAPObject(NativeLDAPObject):
    def __init__(
        self,
        uri,
        trace_level=0,
        trace_file=None,
        trace_stack_limit=5,
        bytes_mode=False,
        bytes_strictness=None,
        retry_max=1,
        retry_delay=60.0,
    ):
        NativeLDAPObject.__init__(
            self,
            uri=uri,
            trace_level=trace_level,
            trace_file=trace_file,
            trace_stack_limit=trace_stack_limit,
            bytes_mode=bytes_mode,
            bytes_strictness=bytes_strictness,
            retry_max=retry_max,
            retry_delay=retry_delay,
        )

    @to_list
    def _convert_results_to_unicode(self, result_list):
        for dn, attrs in result_list:
            if dn is None:
                continue
            new_attrs = {}
            for attribute in attrs:
                values = attrs[attribute]
                # specialize for GUID attributes
                if attribute.lower() in USUAL_GUID_ATTRIBUTES and len(values[0]) == 16:
                    try:
                        values = [str(uuid.UUID(bytes=values[0])).encode()]
                    except ValueError:
                        values = []
                values = filter_non_unicode_values(values)
                if not values:
                    continue
                new_attrs[attribute] = values
            yield dn, new_attrs

    def modify_s(self, dn, modlist):
        new_modlist = []
        for mod_op, mod_typ, mod_vals in modlist:

            def convert(v):
                if hasattr(v, 'isnumeric'):
                    # unicode case
                    v = v.encode('utf-8')
                return v

            if mod_vals is None:
                pass
            elif isinstance(mod_vals, list):
                mod_vals = [convert(mod_val) for mod_val in mod_vals]
            else:
                mod_vals = convert(mod_vals)
            new_modlist.append((mod_op, mod_typ, mod_vals))
        return NativeLDAPObject.modify_s(self, dn, new_modlist)

    def result4(
        self,
        msgid=ldap.RES_ANY,
        all=1,
        timeout=None,
        add_ctrls=0,
        add_intermediates=0,
        add_extop=0,
        resp_ctrl_classes=None,
    ):
        (
            resp_type,
            resp_data,
            resp_msgid,
            decoded_resp_ctrls,
            resp_name,
            resp_value,
        ) = NativeLDAPObject.result4(
            self,
            msgid=msgid,
            all=all,
            timeout=timeout,
            add_ctrls=add_ctrls,
            add_intermediates=add_intermediates,
            add_extop=add_extop,
            resp_ctrl_classes=resp_ctrl_classes,
        )
        if resp_data:
            resp_data = self._convert_results_to_unicode(resp_data)
        return resp_type, resp_data, resp_msgid, decoded_resp_ctrls, resp_name, resp_value


def map_text(d):
    if d is None:
        return d
    elif isinstance(d, str):
        return force_str(d)
    elif isinstance(d, (list, tuple)):
        return d.__class__(map_text(x) for x in d)
    elif isinstance(d, dict):
        return {map_text(k): map_text(v) for k, v in d.items()}
    raise NotImplementedError


def password_policy_control_messages(ctrl, attributes):
    messages = []

    if ctrl.error is not None:
        error = ppolicy.PasswordPolicyError.namedValues[ctrl.error]
        error2message = {
            'passwordExpired': _('The password expired.'),
            'accountLocked': '{locked}{failure}'.format(
                locked=(
                    _('Account is locked since {since}.').format(since=attributes['pwdaccountlockedtime'][0])
                    if attributes['pwdaccountlockedtime']
                    else _('Account is locked.')
                ),
                failure=(
                    _(" It's been locked after {count} failed login attempts.").format(
                        count=attributes['pwdmaxfailure'][0]
                    )
                    if attributes['pwdmaxfailure']
                    else ''
                ),
            ),
            'changeAfterReset': _('The password was reset and must be changed.'),
            'passwordModNotAllowed': _('It is not possible to modify the password.'),
            'mustSupplyOldPassword': _('The old password must be supplied.'),
            'insufficientPasswordQuality': _('The password does not meet the quality requirements.'),
            'passwordTooShort': _('The password is too short.{minlength}').format(
                minlength=(
                    _(' The minimun length is {minlength} characters.').format(
                        minlength=attributes['pwdminlength'][0]
                    )
                    if attributes['pwdminlength']
                    else ''
                )
            ),
            'passwordTooYoung': _('It is too soon to change the password.'),
            'passwordInHistory': _('This password has already been used and can no longer be used.'),
        }
        messages.append(error2message.get(error, _('Unexpected error {error}').format(error=error)))
        return messages

    if ctrl.timeBeforeExpiration:
        expiration_date = datetime.datetime.fromtimestamp(time.time() + ctrl.timeBeforeExpiration)
        messages.append(
            _('The password will expire at {expiration_date}.').format(
                expiration_date=dateformat(expiration_date, 'l j F Y, P')
            )
        )
    if ctrl.graceAuthNsRemaining:
        messages.append(
            ngettext(
                'This password expired: this is the last time it can be used.',
                'This password expired and can only be used {graceAuthNsRemaining} times, including this'
                ' one.',
                ctrl.graceAuthNsRemaining,
            ).format(graceAuthNsRemaining=ctrl.graceAuthNsRemaining)
        )
    return messages


LDAP_DEACTIVATION_REASON_NOT_PRESENT = 'ldap-not-present'
LDAP_DEACTIVATION_REASON_OLD_SOURCE = 'ldap-old-source'


class LDAPUser(User):
    SESSION_LDAP_DATA_KEY = 'ldap-data'
    _changed = False
    _created = False

    class Meta:
        proxy = True
        app_label = 'authentic2'

    @property
    def block(self):
        return self.ldap_data.get('block', {})

    @property
    def dn(self):
        return self.ldap_data.get('dn', '')

    def ldap_init(self, block, dn):
        self.ldap_data = {
            'block': block,
            'dn': dn,
        }

    def init_from_session(self, session):
        if self.SESSION_LDAP_DATA_KEY in session:
            self.ldap_data = session[self.SESSION_LDAP_DATA_KEY]
            # update dn case, can be removed in the future
            self.ldap_data['dn'] = self.ldap_data['dn'].lower()
            if self.ldap_data.get('password'):
                self.ldap_data['password'] = {
                    key.lower(): value for key, value in self.ldap_data['password'].items()
                }

            # retrieve encrypted bind pw if necessary
            encrypted_bindpw = self.ldap_data.get('block', {}).get('encrypted_bindpw')
            if encrypted_bindpw:
                decrypted = crypto.aes_base64_decrypt(
                    settings.SECRET_KEY, encrypted_bindpw, raise_on_error=False
                )
                if decrypted:
                    decrypted = force_str(decrypted)
                    self.ldap_data['block']['bindpw'] = decrypted
                    del self.ldap_data['block']['encrypted_bindpw']

    def init_to_session(self, session):
        # encrypt bind password in sessions
        data = dict(self.ldap_data)
        data['block'] = dict(data['block'])
        if data['block'].get('bindpw'):
            data['block']['encrypted_bindpw'] = force_str(
                crypto.aes_base64_encrypt(settings.SECRET_KEY, force_bytes(data['block']['bindpw']))
            )
            del data['block']['bindpw']
        session[self.SESSION_LDAP_DATA_KEY] = data

    def update_request(self):
        request = StoreRequestMiddleware.get_request()
        if request:
            assert request.session is not None
            self.init_to_session(request.session)

    def init_from_request(self):
        request = StoreRequestMiddleware.get_request()
        if not request or request.session is None:
            log.warning(
                'ldap: failed to init user from request', extra={'user': None}
            )  # pass explicit user attribute, to prevent recursion #91260
            return
        self.init_from_session(request.session)

    def keep_password(self, password):
        if not password:
            return
        if self.block.get('keep_password_in_session', False):
            self.keep_password_in_session(password)
        if self.block['keep_password']:
            if not super().check_password(password):
                super().set_password(password)
                self._changed = True
        else:
            if super().has_usable_password():
                self.set_unusable_password()
                self._changed = True

    def keep_password_in_session(self, password):
        cache = self.ldap_data.setdefault('password', {})
        if password is not None:
            # Prevent eavesdropping of the password through the session storage
            password = force_str(crypto.aes_base64_encrypt(settings.SECRET_KEY, force_bytes(password)))
        cache[self.dn] = password
        # ensure session is marked dirty
        self.update_request()

    def get_password_in_session(self):
        if self.block.get('keep_password_in_session', False):
            cache = self.ldap_data.get('password', {})
            password = cache.get(self.dn)
            if password is not None:
                try:
                    password = force_str(crypto.aes_base64_decrypt(settings.SECRET_KEY, password))
                except crypto.DecryptionError:
                    log.error('unable to decrypt a stored LDAP password')
                    self.keep_password_in_session(None)
                    password = None
                else:
                    password = force_str(password)
            return password
        else:
            self.keep_password_in_session(None)
            return None

    def check_password(self, raw_password):
        connection = self.ldap_backend.get_connection(self.block)
        if connection:
            try:
                connection.simple_bind_s(self.dn, raw_password)
                self._current_password = raw_password
                return True
            except ldap.INVALID_CREDENTIALS:
                return False
            except ldap.LDAPError as e:
                log.warning('ldap: check_password failed (%s)', ldap_error_str(e))
        log.warning('ldap: check_password failed, could not get a connection')
        return False

    def set_password(self, new_password):
        # Allow change password to work in all cases, as the form does a check_password() first
        # if the verify pass, we have the old password stored in self._current_password
        _current_password = getattr(self, '_current_password', None) or self.get_password_in_session()
        if _current_password != new_password:
            conn = self.get_connection()
            if not conn:
                log.warning('ldap: set_password failed, could not get a connection')
                return
            try:
                self.ldap_backend.modify_password(conn, self.block, self.dn, _current_password, new_password)
            except ldap.LDAPError as e:
                log.warning('ldap: set_password failed (%s)', ldap_error_str(e))
                raise PasswordChangeError(_('LDAP directory refused the password change.'))
            self._current_password = new_password
        self.keep_password_in_session(new_password)
        if self.block['keep_password']:
            super().set_password(new_password)
        else:
            self.set_unusable_password()

    def has_usable_password(self):
        return True

    def get_connection(self):
        ldap_password = getattr(self, '_current_password', None) or self.get_password_in_session()
        credentials = ()
        if ldap_password:
            credentials = (self.dn, ldap_password)
        # must be redone if session is older than current code update and new
        # options have been added to the setting dictionnary for LDAP
        # authentication
        self.ldap_backend.update_default(self.block, validate=False)
        return self.ldap_backend.get_connection(self.block, credentials=credentials)

    def get_attributes(self, attribute_source, ctx):
        cache_key = hashlib.md5(
            (force_str(str(self.pk)) + ';' + force_str(self.dn)).encode('utf-8')
        ).hexdigest()
        conn = self.get_connection()
        # prevents blocking on temporary LDAP failures
        if conn is not None:
            attributes = self.ldap_backend.get_ldap_attributes(self.block, conn, self.dn) or {}
            # keep attributes in cache for 8 hours
            cache.set(cache_key, attributes, 3600 * 8)
            return attributes
        else:
            log.warning('ldap: get_attributes failed, could not get a connection')
        return cache.get(cache_key, {})

    def save(self, *args, **kwargs):
        if hasattr(self, 'keep_pk'):
            pk = self.pk  # pylint: disable=access-member-before-definition
            self.pk = self.keep_pk
        super().save(*args, **kwargs)
        if hasattr(self, 'keep_pk'):
            self.pk = pk  # pylint: disable=used-before-assignment

    @property
    def can_reset_password(self):
        return self.block.get('can_reset_password', False)

    def can_change_password(self):
        return self.block.get('user_can_change_password', False)


class LDAPBackend:
    _DEFAULTS = {
        'basedn': '',
        'binddn': '',
        'bindpw': '',
        'bindsasl': (),
        'user_dn_template': '',
        'user_filter': 'uid=%s',  # will be '(|(mail=%s)(uid=%s))' if
        'sync_ldap_users_filter': '',
        'user_basedn': '',
        'group_basedn': '',
        'member_of_attribute': '',
        'group_filter': '(&(member={user_dn})(objectClass=groupOfNames))',
        'groupsu': (),
        'groupstaff': (),
        'groupactive': (),
        'group_mapping': (),
        'group_to_role_mapping': (),
        'replicas': True,
        'email_field': 'mail',
        'fname_field': 'givenName',
        'lname_field': 'sn',
        'timeout': 5,
        'sync_timeout': 30,
        'referrals': False,
        'disable_update': False,
        'bind_with_username': False,
        # always use the first URL to build the external id
        'use_first_url_for_external_id': True,
        # active directory ?
        'active_directory': False,
        # shuffle replicas
        'shuffle_replicas': True,
        # all users from this LDAP are superusers
        'is_superuser': None,
        # all users from this LDAP are staff
        'is_staff': None,
        # create missing group if needed
        'create_group': False,
        # attributes to retrieve and store with the user object
        'attributes': ['uid'],
        # default value for some attributes
        'mandatory_attributes_values': {},
        # mapping from LDAP attributes name to other names
        'attribute_mappings': [],
        # extra attributes retrieve by making other LDAP search using user object informations
        'extra_attributes': {},
        # realm for selecting an ldap configuration or formatting usernames
        'realm': 'ldap',
        # template for building username
        'username_template': '{uid[0]}@{realm}',
        # allow to match multiple user records
        'multimatch': True,
        # update username on all login, use with CAUTION !! only if you know that
        # generated username are unique
        'update_username': False,
        # lookup existing user with an external id build with attributes
        'lookups': ('guid', 'external_id', 'username', 'email'),
        'external_id_tuples': (
            ('uid',),
            ('dn:noquote',),
        ),
        # clean all other existing external id for an user after linking the user
        # to an external id.
        'clean_external_id_on_update': True,
        # keep password around so that Django authentification also work
        'keep_password': False,
        # Converse the password in the session if needed to retrieve attributes or change password
        'keep_password_in_session': False,
        # Only authenticate users coming from the corresponding realm
        'limit_to_realm': False,
        # Assign users mandatorily to some groups
        'set_mandatory_groups': (),
        # Assign users mandatorily to some roles
        'set_mandatory_roles': (),
        # Can users change their password ?
        'user_can_change_password': True,
        # Use starttls
        'use_tls': True,
        # Require certificate
        'require_cert': 'demand',
        # client and server certificates
        'cacertfile': DEFAULT_CA_BUNDLE,
        'cacertdir': '',
        'certfile': '',
        'keyfile': '',
        # LDAP library options
        'ldap_options': {},
        'global_ldap_options': {},
        # Use Password Modify extended operation
        'use_password_modify': True,
        # Target OU
        'ou_slug': '',
        # use user credentials when we have them to connect to the LDAP
        'connect_with_user_credentials': True,
        # can reset password
        'can_reset_password': False,
        # mapping from LDAP attributes to User attributes
        'user_attributes': [],
        # https://www.python-ldap.org/en/python-ldap-3.3.0/reference/ldap.html#ldap-controls
        'use_controls': False,
        'use_ppolicy_controls': False,
        'ppolicy_dn': '',
        # default page size for SimplePagedSearch extension
        'page_size': 100,
        'authentication': True,
        'provisionning': True,
    }
    _REQUIRED = ('url', 'basedn')
    _TO_ITERABLE = ('url', 'groupsu', 'groupstaff', 'groupactive')
    _TO_LOWERCASE = (
        'fname_field',
        'lname_field',
        'email_field',
        'attributes',
        'mandatory_attributes_values',
        'member_of_attribute',
        'group_to_role_mapping',
        'group_mapping',
        'attribute_mappings',
        'external_id_tuples',
    )
    _VALID_CONFIG_KEYS = list(set(_REQUIRED).union(set(_DEFAULTS)))

    @classmethod
    @to_list
    def get_realms(cls):
        config = cls.get_config()
        for block in config:
            yield block['realm']

    @classmethod
    def get_config(cls):
        if not getattr(settings, 'LDAP_AUTH_SETTINGS', []):
            return []
        if isinstance(settings.LDAP_AUTH_SETTINGS[0], dict):
            blocks = settings.LDAP_AUTH_SETTINGS
        else:
            blocks = (cls._parse_simple_config(),)
        # First get our configuration into a standard format
        for block in blocks:
            cls.update_default(block)
        return blocks

    @classmethod
    def process_bind_controls(cls, request, block, conn, authz_id, ctrls):
        attributes = cls.get_ppolicy_attributes(block, conn, authz_id)
        for c in ctrls:
            if c.controlType == ppolicy.PasswordPolicyControl.controlType:
                message = ' '.join(password_policy_control_messages(c, attributes))
                if request is not None:
                    messages.add_message(request, messages.WARNING, message)
                    if (
                        c.graceAuthNsRemaining
                        or c.timeBeforeExpiration
                        or (
                            c.error is not None
                            and ppolicy.PasswordPolicyError.namedValues[c.error] == 'changeAfterReset'
                        )
                    ):
                        request.needs_password_change = True
            else:
                message = str(vars(c))
            log.info('ldap: bind error with authz_id "%s" -> "%s"', authz_id, message)

    @classmethod
    def process_modify_password_controls(cls, block, conn, authz_id, ctrls):
        attributes = cls.get_ppolicy_attributes(block, conn, authz_id)
        errors = []
        for c in ctrls:
            if c.controlType == ppolicy.PasswordPolicyControl.controlType:
                message = ' '.join(password_policy_control_messages(c, attributes))
            else:
                message = str(vars(c))
            log.info('ldap: fail to modify password of "%s" -> "%s"', authz_id, message)
            errors.append(message)

        if errors:
            raise PasswordChangeError(' '.join(errors))

    @classmethod
    def check_group_to_role_mappings(cls, block):
        group_to_role_mapping = block.get('group_to_role_mapping')
        if not block.get('group_filter') or not group_to_role_mapping:
            return
        for conn in cls.get_connections(block):
            existing_groups = cls.get_groups_dns(conn, block)
            for group_dn, dummy_role_slugs in group_to_role_mapping:
                if group_dn in existing_groups:
                    continue
                log.warning('ldap: unknown group "%s" mapped to a role', group_dn)

    @classmethod
    def get_groups_dns(cls, conn, block):
        group_base_dn = block['group_basedn'] or block['basedn']
        # 1.1 is special attribute meaning, "no attribute requested"
        results = conn.search_s(group_base_dn, ldap.SCOPE_SUBTREE, block['group_filter'], ['1.1'])
        results = cls.normalize_ldap_results(results)
        return {group_dn for group_dn, attrs in results}

    def authenticate(self, request=None, username=None, password=None, realm=None, ou=None):
        if username is None or password is None:
            return None

        config = self.get_config()
        if not config:
            return

        if not ldap:
            raise ImproperlyConfigured('ldap is not available')

        default_ou_slug = get_default_ou().slug

        # Now we can try to authenticate
        for block in config:
            if block['authentication'] is False:
                continue
            uid = username
            # if ou is provided, ignore LDAP server for other OU
            if ou:
                if ou.slug != (block.get('ou_slug') or default_ou_slug):
                    continue
            if block['limit_to_realm']:
                if realm is None and '@' in username:
                    uid, realm = username.rsplit('@', 1)
                if realm and block.get('realm') != realm:
                    continue
            if '%s' not in block['user_filter']:
                log.error("account name authentication filter doesn't contain '%s'")
                continue
            user = self.authenticate_block(request, block, uid, password)
            if user is not None:
                return user

    def authenticate_block(self, request, block, username, password):
        for conn in self.get_connections(block):
            ldap_uri = conn.get_option(ldap.OPT_URI)
            authz_ids = []
            user_basedn = force_str(block.get('user_basedn') or block['basedn'])

            try:
                if block['user_dn_template']:
                    template = force_str(block['user_dn_template'])
                    escaped_username = escape_dn_chars(username)
                    authz_ids.append(template.format(username=escaped_username))
                else:
                    try:
                        if block.get('bind_with_username'):
                            authz_ids.append(username)
                        elif block['user_filter']:
                            # allow multiple occurences of the username in the filter
                            user_filter = force_str(block['user_filter'])
                            n = len(user_filter.split('%s')) - 1
                            try:
                                query = filter_format(user_filter, (username,) * n)
                            except TypeError as e:
                                log.error(
                                    '[%s] user_filter syntax error %r: %s', ldap_uri, block['user_filter'], e
                                )
                                return
                            log.debug(
                                '[%s] looking up dn for username %r using query %r', ldap_uri, username, query
                            )
                            results = conn.search_s(user_basedn, ldap.SCOPE_SUBTREE, query, ['1.1'])
                            results = self.normalize_ldap_results(results)
                            # remove search references
                            results = [result for result in results if result[0] is not None]
                            log.debug('found dns %r', results)
                            if len(results) == 0:
                                log.debug('[%s] user lookup failed: no entry found, %s', ldap_uri, query)
                            elif not block['multimatch'] and len(results) > 1:
                                log.error(
                                    '[%s] user lookup failed: too many (%d) entries found: %s',
                                    ldap_uri,
                                    len(results),
                                    query,
                                )
                            else:
                                authz_ids.extend(result[0] for result in results)
                        else:
                            raise NotImplementedError
                    except ldap.NO_SUCH_OBJECT:
                        log.error('[%s] user lookup failed, basedn %s not found', ldap_uri, user_basedn)
                        if block['replicas']:
                            break
                        continue
                    except (ldap.TIMEOUT, ldap.UNAVAILABLE) as e:
                        log.warning(
                            '[%s] user lookup failed, with query %r (%s)', ldap_uri, query, ldap_error_str(e)
                        )
                        continue
                    except ldap.LDAPError as e:
                        log.error(
                            '[%s] user lookup failed, with query %r (%s)', ldap_uri, query, ldap_error_str(e)
                        )
                        continue
                if not authz_ids:
                    continue

                try:
                    ldap_error = False
                    for authz_id in authz_ids:
                        try:
                            if block.get('use_ppolicy_controls'):
                                serverctrls = [ppolicy.PasswordPolicyControl()]
                            else:
                                serverctrls = []
                            results = conn.simple_bind_s(authz_id, password, serverctrls=serverctrls)
                            self.process_bind_controls(request, block, conn, authz_id, results[3])
                            user_login_success(authz_id)
                            if not block['connect_with_user_credentials']:
                                success, msg = self.bind(block, conn)
                                if not success:
                                    log.error('rebind failure after login bind: %s', msg)
                                    continue
                            break
                        except ldap.INVALID_CREDENTIALS as e:
                            if block.get('use_controls') and len(e.args) > 0 and 'ctrls' in e.args[0]:
                                self.process_bind_controls(
                                    request, block, conn, authz_id, DecodeControlTuples(e.args[0]['ctrls'])
                                )
                            success, error = self.bind(block, conn)
                            if success:
                                attributes = self.get_ldap_attributes(block, conn, authz_id)
                                user = self._lookup_existing_user(authz_id, block, attributes)
                                if (
                                    user
                                    and hasattr(request, 'failed_logins')
                                    and user not in request.failed_logins
                                ):
                                    request.failed_logins.update({user: {}})
                            else:
                                log.warning(
                                    'could not rebind after a bind failure, unable to attach the error to the'
                                    ' user (error %s)',
                                    error,
                                )
                            user_login_failure(authz_id)
                    else:
                        log.debug('user bind failed: invalid credentials')
                        if block['replicas']:
                            break
                        continue
                except ldap.NO_SUCH_OBJECT:
                    # should not happen as we just searched for this object !
                    log.error('[%s] user bind failed: authz_id not found %r', ldap_uri, ', '.join(authz_ids))
                    if block['replicas']:
                        break
                try:
                    return self._return_user(authz_id, password, conn, block)
                except UserCreationError as e:
                    if request:
                        messages.error(request, str(e))
                    return None
            except ldap.CONNECT_ERROR:
                log.error(
                    '[%s] connection to %r failed, did you forget to declare the TLS certificate in'
                    ' /etc/ldap/ldap.conf ?',
                    ldap_uri,
                    block['url'],
                )
                self._record_failure_for_user(request, 'ldap connect error', authz_id, block, conn)
                ldap_error = _('error while connecting to server {}').format(block['realm'])
            except ldap.TIMEOUT:
                log.error('connection to %r timed out', block['url'])
                self._record_failure_for_user(request, 'ldap timeout', authz_id, block, conn)
                ldap_error = _('connection to server {} timed out').format(block['realm'])
            except ldap.SERVER_DOWN:
                log.error('ldap authentication error: %r is down', block['url'])
                self._record_failure_for_user(request, 'ldap server down', authz_id, block, conn)
                ldap_error = _('server {} is down').format(block['realm'])
            except ldap.UNAVAILABLE:
                log.error('ldap authentication error: %r\'s system agent (DSA) is unavailable', block['url'])
                self._record_failure_for_user(
                    request, 'ldap server\' DSA is unavailable', authz_id, block, conn
                )
                ldap_error = _('server {}\'s system agent (DSA) is unavailable').format(block['realm'])
            finally:
                del conn
            if ldap_error:
                warnings = getattr(request, 'authn_warnings', [])
                warnings.append(ldap_error)
                request.authn_warnings = warnings
        return None

    def get_user(self, user_id, session=None):
        try:
            try:
                user_id = int(user_id)
            except ValueError:
                return None
            user = LDAPUser.objects.get(pk=user_id)
            # retrieve data from current request
            if session:
                user.init_from_session(session)
            else:
                user.init_from_request()
            return user
        except LDAPUser.DoesNotExist:
            return None

    @classmethod
    def _parse_simple_config(cls):
        if len(settings.LDAP_AUTH_SETTINGS) < 2:
            raise ImproperlyConfigured(
                'In a minimal configuration, you must at least specify url and user DN'
            )
        return {'url': settings.LDAP_AUTH_SETTINGS[0], 'basedn': settings.LDAP_AUTH_SETTINGS[1]}

    def backend_name(self):
        return '%s.%s' % (__name__, self.__class__.__name__)

    def create_username(self, block, attributes):
        '''Build a username using the configured template'''
        username_template = force_str(block['username_template'])
        try:
            return username_template.format(realm=block['realm'], **attributes)
        except KeyError as e:
            log.warning('missing attribute %s to build the username', e.args[0])
            # attributes are missing to build the username
            return None

    def populate_user_attributes(self, user, block, attributes):
        # map legacy attributes (columns from Django user model)
        for legacy_attribute, legacy_field in (
            ('email', 'email_field'),
            ('first_name', 'fname_field'),
            ('last_name', 'lname_field'),
        ):
            ldap_attribute = block[legacy_field]
            if not ldap_attribute:
                break
            if ldap_attribute in attributes:
                value = attributes[ldap_attribute][0]
            else:
                value = ''
            if getattr(user, legacy_attribute) != value:
                setattr(user, legacy_attribute, value)
                user._changed = True
        # map new style attributes
        user_attributes = {}
        for mapping in block.get('user_attributes', []):
            from_ldap = mapping.get('from_ldap')
            to_user = mapping.get('to_user')
            if not from_ldap or not to_user:
                continue
            from_ldap = from_ldap.lower()
            if not attributes.get(from_ldap):
                user_attributes[to_user] = ''
            else:
                user_attributes[to_user] = attributes[from_ldap][0]
        for name, new_value in user_attributes.items():
            value = getattr(user.attributes, name, None)
            if value != new_value:
                if not user.pk:
                    user.save()
                setattr(user.attributes, name, new_value)
                user._changed = True

    def populate_admin_flags_by_group(self, user, block, group_dns):
        """Attribute admin flags based on groups.

        It supersedes is_staff, is_superuser and is_active."""
        for g, attr in (
            ('groupsu', 'is_superuser'),
            ('groupstaff', 'is_staff'),
            ('groupactive', 'is_active'),
        ):
            group_dns_to_match = block[g]
            if not group_dns_to_match:
                continue
            for group_dn in group_dns_to_match:
                if group_dn in group_dns:
                    v = True
                    break
            else:
                v = False
            if getattr(user, attr) != v:
                setattr(user, attr, v)
                user._changed = True

    def populate_groups_by_mapping(self, user, dn, conn, block, group_dns):
        '''Assign group to user based on a mapping from group DNs'''
        group_mapping = block['group_mapping']
        if not group_mapping:
            return
        if not user.pk:
            user.save()
            user._changed = False
        groups = user.groups.all()
        for group_dn, group_names in group_mapping:
            for group_name in group_names:
                group = self.get_group_by_name(block, group_name)
                if group is None:
                    continue
                # Add missing groups
                if group_dn in group_dns and group not in groups:
                    user.groups.add(group)
                # Remove extra groups
                elif group_dn not in group_dns and group in groups:
                    user.groups.remove(group)

    def populate_roles_by_mapping(self, user, dn, conn, block, group_dns):
        '''Assign role to user based on a mapping from group DNs'''
        group_to_role_mapping = block.get('group_to_role_mapping')
        if not group_to_role_mapping:
            return
        if not user.pk:
            user.save()
            user._changed = False

        # aggregate membership status
        role_is_member = collections.defaultdict(bool)
        for group_dn, role_names in group_to_role_mapping:
            for role_name in role_names:
                role, error = self.get_role(block, role_id=role_name)
                if role is None:
                    log.warning('error %s: couldn\'t retrieve role %r', error, role_name)
                    continue
                if group_dn in group_dns:
                    role_is_member[role] |= True
                elif group_dn not in group_dns:
                    role_is_member[role] |= False

        if not role_is_member:
            return

        # synchronize membership status
        roles = user.roles.all()
        for role, is_member in role_is_member.items():
            if is_member and role not in roles:
                log.info('ldap: user %s is now member of role %s', user, role)
                user.roles.add(role)
            if not is_member and role in roles:
                log.info('ldap: user %s is no longer member of role %s', user, role)
                user.roles.remove(role)
            if role.can_manage_members:
                log.info('ldap: role %s is now only manageable through LDAP', role)
                Role.objects.filter(pk=role.pk).update(can_manage_members=False)

    def get_ldap_group_dns(self, user, dn, conn, block, attributes):
        """Retrieve group DNs from the LDAP by attributes (memberOf) or by
        filter.
        """
        group_base_dn = block['group_basedn'] or block['basedn']
        member_of_attribute = block['member_of_attribute']
        group_filter = block['group_filter']
        group_dns = set()
        if member_of_attribute:
            group_dns.update([dn.lower() for dn in attributes.get(member_of_attribute, [])])
        if group_filter:
            group_filter = force_str(group_filter)
            params = attributes.copy()
            params['user_dn'] = dn
            query = FilterFormatter().format(group_filter, **params)
            try:
                results = conn.search_s(group_base_dn, ldap.SCOPE_SUBTREE, query, [])
                results = self.normalize_ldap_results(results)
            except ldap.NO_SUCH_OBJECT:
                pass
            else:
                group_dns.update(dn for dn, attrs in results)
        return group_dns

    def populate_user_groups(self, user, dn, conn, block, attributes):
        group_dns = self.get_ldap_group_dns(user, dn, conn, block, attributes)
        log.debug('groups for dn %r: %r', dn, group_dns)
        self.populate_admin_flags_by_group(user, block, group_dns)
        self.populate_groups_by_mapping(user, dn, conn, block, group_dns)

    def populate_user_roles(self, user, dn, conn, block, attributes):
        group_dns = self.get_ldap_group_dns(user, dn, conn, block, attributes)
        log.debug('roles for dn %r: %r', dn, group_dns)
        self.populate_roles_by_mapping(user, dn, conn, block, group_dns)

    def get_group_by_name(self, block, group_name, create=None):
        '''Obtain a Django group'''
        if create is None:
            create = block['create_group']
        if create:
            group, dummy = Group.objects.get_or_create(name=group_name)
            return group
        else:
            try:
                return Group.objects.get(name=group_name)
            except Group.DoesNotExist:
                return None

    @classmethod
    def get_role(cls, block, role_id):
        '''Obtain a Django role'''
        kwargs = {}
        slug = None
        if isinstance(role_id, str):
            slug = role_id
        elif isinstance(role_id, (tuple, list)):
            try:
                slug, ou__slug = role_id
                kwargs = {'ou__slug': ou__slug}
            except ValueError:
                try:
                    slug, ou__slug, service__slug = role_id
                    kwargs = {'ou__slug': ou__slug, 'service__slug': service__slug}
                except ValueError:
                    pass
        if slug:
            try:
                return Role.objects.get(slug=slug, **kwargs), None
            except Role.DoesNotExist:
                try:
                    return Role.objects.get(name=slug, **kwargs), None
                except Role.DoesNotExist:
                    error = 'role %r does not exist' % role_id
                except Role.MultipleObjectsReturned:
                    error = 'multiple objects returned, identifier is imprecise'
                    if 'ou__slug' not in kwargs:
                        try:
                            return Role.objects.get(name=slug, ou=get_default_ou(), **kwargs), None
                        except Role.DoesNotExist:
                            error = 'multiple objects returned none of which belongs to default ou, role *name* is ambiguous'
                        except Role.MultipleObjectsReturned:
                            pass
            except Role.MultipleObjectsReturned:
                error = 'multiple objects returned, identifier is imprecise'
                if 'ou__slug' not in kwargs:
                    try:
                        return Role.objects.get(slug=slug, ou=get_default_ou(), **kwargs), None
                    except Role.DoesNotExist:
                        error = 'multiple objects returned none of which belongs to default ou, role *slug* is ambiguous'
                    except Role.MultipleObjectsReturned:
                        pass
        else:
            error = (
                'invalid role identifier must be slug, (slug, ou__slug) or (slug, ou__slug, service__slug)'
            )
        return None, error

    def populate_mandatory_groups(self, user, block):
        mandatory_groups = block.get('set_mandatory_groups')
        if not mandatory_groups:
            return
        if not user.pk:
            user.save()
            user._changed = False
        groups = user.groups.all()
        for group_name in mandatory_groups:
            group = self.get_group_by_name(block, group_name)
            if group is None:
                log.warning('error: couldn\'t retrieve group %r', group_name)
                continue
            if group not in groups:
                user.groups.add(group)

    def populate_mandatory_roles(self, user, block):
        mandatory_roles = block.get('set_mandatory_roles')
        if not mandatory_roles:
            return
        if not user.pk:
            user.save()
            user._changed = False
        roles = user.roles.all()
        for role_name in mandatory_roles:
            role, error = self.get_role(block, role_id=role_name)
            if role is None:
                log.warning('error %s: couldn\'t retrieve role %r', error, role_name)
                continue
            if role not in roles:
                user.roles.add(role)

    def populate_admin_fields(self, user, block):
        if block['is_staff'] is not None:
            if user.is_staff != block['is_staff']:
                user.is_staff = block['is_staff']
                user._changed = True
        if block['is_superuser'] is not None:
            if user.is_superuser != block['is_superuser']:
                user.is_superuser = block['is_superuser']
                user._changed = True

    def populate_user(self, user, dn, username, conn, block, attributes):
        self.populate_user_attributes(user, block, attributes)
        self.populate_admin_fields(user, block)
        self.populate_user_ou(user, dn, conn, block, attributes)
        self.update_user_identifiers(user, username, block, attributes)
        self.populate_mandatory_groups(user, block)
        self.populate_mandatory_roles(user, block)
        self.populate_user_groups(user, dn, conn, block, attributes)
        self.populate_user_roles(user, dn, conn, block, attributes)

    def _get_target_ou(self, block):
        ou_slug = block['ou_slug']
        if ou_slug:
            ou_slug = force_str(ou_slug)
            try:
                return OrganizationalUnit.objects.get(slug=ou_slug)
            except OrganizationalUnit.DoesNotExist:
                raise ImproperlyConfigured('ou_slug value is wrong for ldap %r' % block['url'])
        else:
            return get_default_ou()

    def populate_user_ou(self, user, dn, conn, block, attributes):
        """Assign LDAP user to an ou, the default one if ou_slug setting is
        None"""
        ou = self._get_target_ou(block)
        if user.ou != ou:
            user.ou = ou
            user._changed = True

    @classmethod
    def attribute_name_from_external_id_tuple(cls, external_id_tuple):
        for attribute in external_id_tuple:
            if ':' in attribute:
                attribute = attribute.split(':', 1)[0]
            yield attribute

    @classmethod
    def get_sync_ldap_user_filter(cls, block):
        user_filter = force_str(block['sync_ldap_users_filter'] or block['user_filter'])
        user_filter = user_filter.replace('%s', '*')
        return user_filter

    @classmethod
    def get_ldap_attributes_names(cls, block):
        attributes = set()
        attributes.update(map_text(block['attributes']))
        for field in ('email_field', 'fname_field', 'lname_field', 'member_of_attribute'):
            if block[field]:
                attributes.add(block[field])
        for external_id_tuple in map_text(block['external_id_tuples']):
            attributes.update(cls.attribute_name_from_external_id_tuple(external_id_tuple))
        for dummy_from_at, to_at in map_text(block['attribute_mappings']):
            attributes.add(to_at)
        for mapping in block['user_attributes']:
            from_ldap = mapping.get('from_ldap')
            if from_ldap:
                attributes.add(from_ldap)
        for extra_at in block.get('extra_attributes', {}):
            if 'loop_over_attribute' in block['extra_attributes'][extra_at]:
                attributes.add(block['extra_attributes'][extra_at]['loop_over_attribute'])
            at_mapping = block['extra_attributes'][extra_at].get('mapping', {})
            for key in at_mapping:
                if at_mapping[key] != 'dn':
                    attributes.add(at_mapping[key])
        # add usual GUID attributes
        attributes.update(USUAL_GUID_ATTRIBUTES)
        return list({attribute.lower() for attribute in attributes})

    @classmethod
    def get_ppolicy_attributes(cls, block, conn, dn):
        ldap_uri = conn.get_option(ldap.OPT_URI)

        def get_attributes(dn, attributes):
            try:
                results = conn.search_s(dn, ldap.SCOPE_BASE, '(objectclass=*)', attributes)
            except (ldap.TIMEOUT, ldap.UNAVAILABLE) as e:
                log.warning(
                    '[%s] unable to retrieve attributes of dn %r (%s)', ldap_uri, dn, ldap_error_str(e)
                )
                return {}
            except ldap.LDAPError as e:
                log.error('[%s] unable to retrieve attributes of dn %r (%s)', ldap_uri, dn, ldap_error_str(e))
                return {}
            results = cls.normalize_ldap_results(results)
            if results:
                attributes_results.update(results[0][1])
            return attributes_results

        user_attributes = [
            'pwdaccountlockedtime',
            'pwdchangedtime',
            'pwdfailuretime',
            'pwdgraceusetime',
            'pwdhistory',
            'pwdreset',
        ]
        ppolicy_attributes = [
            'pwdminage',
            'pwdmaxage',
            'pwdinhistory',
            'pwdcheckquality',
            'pwdminlength',
            'pwdexpirewarning',
            'pwdgraceauthnlimit',
            'pwdlockout',
            'pwdlockoutduration',
            'pwdmaxfailure',
            'pwdmaxrecordedfailure',
            'pwdfailurecountinterval',
            'pwdmustchange',
            'pwdallowuserchange',
            'pwdsafemodify',
        ]
        attributes_results = {k: [] for k in user_attributes + ppolicy_attributes}

        attributes_results.update(get_attributes(dn, user_attributes))
        ppolicy_dn = block.get('ppolicy_dn')
        if ppolicy_dn:
            attributes_results.update(**get_attributes(ppolicy_dn, ppolicy_attributes))

        return attributes_results

    @classmethod
    def get_ldap_attributes(cls, block, conn, dn):
        """Retrieve some attributes from LDAP, add mandatory values then apply
        defined mappings between atrribute names"""
        ldap_uri = conn.get_option(ldap.OPT_URI)
        attributes = cls.get_ldap_attributes_names(block)
        attribute_mappings = map_text(block['attribute_mappings'])
        mandatory_attributes_values = map_text(block['mandatory_attributes_values'])
        try:
            results = conn.search_s(dn, ldap.SCOPE_BASE, '(objectclass=*)', attributes)
        except (ldap.TIMEOUT, ldap.UNAVAILABLE) as e:
            log.warning('[%s] unable to retrieve attributes of dn %r (%s)', ldap_uri, dn, ldap_error_str(e))
            return None
        except ldap.LDAPError as e:
            log.error('[%s] unable to retrieve attributes of dn %r (%s)', ldap_uri, dn, ldap_error_str(e))
            return None
        else:
            results = cls.normalize_ldap_results(results)
        attribute_map = results[0][1] if results else {}
        # add mandatory attributes
        for key, mandatory_values in mandatory_attributes_values.items():
            key = force_str(key)
            old = attribute_map.setdefault(key, [])
            new = set(old) | set(mandatory_values)
            attribute_map[key] = list(new)
        # apply mappings
        for from_attribute, to_attribute in attribute_mappings:
            from_attribute = force_str(from_attribute)
            if from_attribute not in attribute_map:
                continue
            to_attribute = force_str(to_attribute)
            old = attribute_map.setdefault(to_attribute, [])
            new = set(old) | set(attribute_map[from_attribute])
            attribute_map[to_attribute] = list(new)
        attribute_map['dn'] = force_str(dn)

        # extra attributes
        attribute_map = cls.get_ldap_extra_attributes(block, conn, dn, attribute_map)

        return attribute_map

    @classmethod
    def get_ldap_extra_attributes(cls, block, conn, dn, attribute_map):
        '''Retrieve extra attributes from LDAP'''

        ldap_scopes = {
            'base': ldap.SCOPE_BASE,
            'one': ldap.SCOPE_ONELEVEL,
            'sub': ldap.SCOPE_SUBTREE,
        }
        log.debug('Attrs before extra attributes : %s', attribute_map)
        for extra_attribute_name in block.get('extra_attributes', {}):
            extra_attribute_config = block['extra_attributes'][extra_attribute_name]
            extra_attribute_values = []
            if 'loop_over_attribute' in extra_attribute_config:
                extra_attribute_config['loop_over_attribute'] = extra_attribute_config[
                    'loop_over_attribute'
                ].lower()
                if extra_attribute_config['loop_over_attribute'] not in attribute_map:
                    log.debug(
                        'loop_over_attribute %s not present (or empty) in retrieved user object attributes.'
                        ' Pass.',
                        extra_attribute_config['loop_over_attribute'],
                    )
                    continue
                if 'filter' not in extra_attribute_config and 'basedn' not in extra_attribute_config:
                    log.warning(
                        'Extra attribute %s not correctly configured : you need to defined at least one of'
                        ' filter or basedn parameters',
                        extra_attribute_name,
                    )
                for item in attribute_map[extra_attribute_config['loop_over_attribute']]:
                    ldap_filter = extra_attribute_config.get('filter', 'objectClass=*').format(
                        item=item, **attribute_map
                    )
                    ldap_basedn = extra_attribute_config.get('basedn', block.get('basedn')).format(
                        item=item, **attribute_map
                    )
                    ldap_scope = ldap_scopes.get(
                        extra_attribute_config.get('scope', 'sub'), ldap.SCOPE_SUBTREE
                    )
                    ldap_attributes_mapping = extra_attribute_config.get('mapping', {})
                    ldap_attributes_names = list(
                        filter(lambda a: a != 'dn', ldap_attributes_mapping.values())
                    )
                    try:
                        results = conn.search_s(ldap_basedn, ldap_scope, ldap_filter, ldap_attributes_names)
                    except ldap.LDAPError:
                        log.exception(
                            'unable to retrieve extra attribute %s for item %s', extra_attribute_name, item
                        )
                        continue
                    else:
                        results = cls.normalize_ldap_results(results)
                    item_value = {}
                    for dn, attrs in results:
                        log.debug(
                            'Object retrieved for extra attr %s with item %s : %s %s',
                            extra_attribute_name,
                            item,
                            dn,
                            attrs,
                        )
                        for key in ldap_attributes_mapping:
                            item_value[key] = attrs.get(ldap_attributes_mapping[key].lower())
                            log.debug(
                                'Object attribute %s value retrieved for extra attr %s with item %s : %s',
                                ldap_attributes_mapping[key],
                                extra_attribute_name,
                                item,
                                item_value[key],
                            )
                            if not item_value[key]:
                                del item_value[key]
                            elif len(item_value[key]) == 1:
                                item_value[key] = item_value[key][0]
                    extra_attribute_values.append(item_value)
            else:
                log.warning('loop_over_attribute not defined for extra attribute %s', extra_attribute_name)
            extra_attribute_serialization = extra_attribute_config.get('serialization', None)
            if extra_attribute_serialization is None:
                attribute_map[extra_attribute_name] = extra_attribute_values
            elif extra_attribute_serialization == 'json':
                attribute_map[extra_attribute_name] = json.dumps(extra_attribute_values)
            else:
                log.warning(
                    'Invalid serialization type "%s" for extra attribute %s',
                    extra_attribute_serialization,
                    extra_attribute_name,
                )
        return attribute_map

    @classmethod
    def external_id_to_filter(cls, external_id, external_id_tuple):
        """Split the external id, decode it and build an LDAP filter from it
        and the external_id_tuple.
        """
        splitted = external_id.split()
        if len(splitted) != len(external_id_tuple):
            return
        filters = zip(external_id_tuple, splitted)
        decoded = []
        for attribute, value in filters:
            quote = True
            if ':' in attribute:
                attribute, param = attribute.split(':')
                quote = 'noquote' not in param.split(',')
            if quote:
                decoded.append((attribute, urllib.parse.unquote(value)))
            else:
                decoded.append((attribute, force_str(value)))
        filters = [filter_format('(%s=%s)', (a, b)) for a, b in decoded]
        return '(&{})'.format(''.join(filters))

    def build_external_id(self, external_id_tuple, attributes):
        """Build the external id for the user, use attribute that eventually
        never change like GUID or UUID.
        """
        parts = []
        for attribute in external_id_tuple:
            quote = True
            if ':' in attribute:
                attribute, param = attribute.split(':')
                quote = 'noquote' not in param.split(',')
            try:
                part = attributes[attribute]
            except KeyError:
                return None
            if isinstance(part, list):
                part = part[0]
            if quote:
                part = urllib.parse.quote(part.encode('utf-8'))
            parts.append(part)
        return ' '.join(part for part in parts)

    def _lookup_user_queryset(self, block):
        return LDAPUser.objects.prefetch_related('groups').exclude(userexternalid__source=block['realm'])

    def _lookup_by_username(self, ou, block, username):
        try:
            log.debug('ldap: lookup using username %r', username)
            return self._lookup_user_queryset(block=block).get(ou=ou, username=username)
        except LDAPUser.DoesNotExist:
            return None
        except LDAPUser.MultipleObjectsReturned:
            log.warning(
                'ldap: lookup using username %r, too many users with this username in ou "%s"', username, ou
            )
            return None

    def _get_email_from_attributes(self, block, attributes):
        email_field = block.get('email_field')
        if not email_field:
            return
        if email_field not in attributes:
            return
        return attributes[email_field][0]

    def _lookup_by_email(self, ou, block, attributes, lock=False):
        email = self._get_email_from_attributes(block, attributes)
        if not email:
            return
        if lock:
            Lock.lock_email(email)
        try:
            log.debug('ldap: lookup using email %r', email)
            return self._lookup_user_queryset(block=block).filter(ou=ou).get_by_email(email)
        except LDAPUser.DoesNotExist:
            return None
        except LDAPUser.MultipleObjectsReturned:
            log.warning('ldap: lookup using email %r, too many users with this email in ou "%s"', email, ou)
            return None

    def _lookup_by_external_id(self, block, attributes):
        realm = block['realm']
        for eid_tuple in map_text(block['external_id_tuples']):
            external_id = self.build_external_id(eid_tuple, attributes)
            if not external_id:
                continue
            log.debug('lookup using external_id %r: %r', eid_tuple, external_id)
            users = (
                LDAPUser.objects.prefetch_related('groups')
                .filter(
                    userexternalid__external_id__iexact=external_id,
                    userexternalid__source=realm,
                )
                .order_by('-last_login')
            )
            # ordering of NULLs cannot be done through the ORM
            users = sorted(users, reverse=True, key=lambda u: (u.last_login is not None, u.last_login))
            if users:
                user = users[0]
                if len(users) > 1:
                    log.info(
                        'found %d users, collectings roles into the first one and deleting the other ones.',
                        len(users),
                    )
                    for other in users[1:]:
                        user.roles.add(*other.roles.all())
                        other.delete()
                return user
        return None

    def _lookup_by_external_guid(self, block, attribute, guid):
        if not guid:
            return None
        log.debug('ldap: lookup by external_guid %s=%s', attribute, guid)
        try:
            return LDAPUser.objects.get(
                userexternalid__source=block['realm'], userexternalid__external_guid=guid
            )
        except LDAPUser.DoesNotExist:
            return None

    @classmethod
    def _decode_guid_attribute(cls, value):
        # objectguid is encoded as the bytes representation of the UUID,  but
        # we convert it to string representation on LDAP response loading (see
        # _convert_results_to_unicode)
        try:
            return uuid.UUID(value)
        except (ValueError, UnicodeDecodeError):
            return None

    def _lookup_by_guid(self, block, attributes):
        attribute, guid = self._get_guid(attributes)
        return self._lookup_by_external_guid(block=block, attribute=attribute, guid=guid)

    @classmethod
    def _get_guid(cls, attributes):
        for attribute in USUAL_GUID_ATTRIBUTES:
            if attribute not in attributes:
                continue
            value = attributes[attribute][0]
            guid = cls._decode_guid_attribute(value)
            if guid:
                return attribute, guid
        return None, None

    def _lookup_existing_user(self, username, block, attributes, lock=False):
        user = None
        ou = self._get_target_ou(block)
        for lookup_type in block['lookups']:
            if lookup_type == 'username':
                user = self._lookup_by_username(ou=ou, block=block, username=username)
                if not user:
                    username_without_realm = username.split('@', 1)[0]
                    if username_without_realm != username:
                        user = self._lookup_by_username(ou, block, username_without_realm)
            elif lookup_type == 'external_id' and attributes:
                user = self._lookup_by_external_id(block=block, attributes=attributes)
            elif lookup_type == 'email' and attributes:
                user = self._lookup_by_email(ou=ou, block=block, attributes=attributes, lock=lock)
            elif lookup_type == 'guid' and attributes:
                user = self._lookup_by_guid(block=block, attributes=attributes)
            if user:
                return user

    def update_user_identifiers(self, user, username, block, attributes):
        realm = block['realm']
        # if username has changed and we propagate those changes, update it
        if block['update_username']:
            if user.username != username:
                old_username = user.username
                user.username = username
                user._changed = True
                log_msg = 'updating username from %r to %r'
                log.debug(log_msg, old_username, user.username)
        # if external_id lookup is used, update it
        userexternalid = None
        use_guid = False
        use_external_id = False
        guid = None
        external_id = None
        found_by_guid = False

        if 'guid' in block['lookups']:
            use_guid = True
            _attribute, guid = self._get_guid(attributes)

        if guid and user.pk:
            if guid:
                try:
                    userexternalid = UserExternalId.objects.get(user=user, external_guid=guid, source=realm)
                    found_by_guid = True
                except UserExternalId.DoesNotExist:
                    pass

        if (
            'external_id' in block['lookups']
            and block.get('external_id_tuples')
            and block['external_id_tuples'][0]
        ):
            use_external_id = True
            external_id = self.build_external_id(map_text(block['external_id_tuples'][0]), attributes)

        if external_id and user.pk:
            try:
                userexternalid = UserExternalId.objects.get(
                    user=user, external_id__iexact=external_id, source=realm
                )
            except UserExternalId.DoesNotExist:
                pass

        if userexternalid:
            changed = False
            if userexternalid.external_guid != guid:
                userexternalid.external_guid = guid
                changed = True
            if userexternalid.external_id != external_id:
                userexternalid.external_id = external_id
                changed = True
            if changed:
                if found_by_guid:
                    # prevent collision if external_id had a different guid before
                    # keep the userexternalid with a NULL external_id to
                    # remember the origin of the account, which will be
                    # disabled by the synchronization command.
                    UserExternalId.objects.exclude(external_guid=guid).filter(
                        source=realm, external_id__iexact=external_id
                    ).update(external_id=None)
                userexternalid.save()
        elif use_guid or use_external_id:
            if not guid and not external_id:
                log.error(
                    'ldap: unable to build an user_external_id (%r) with attributes: %r',
                    block['external_id_tuples'],
                    attributes,
                )
                raise UserCreationError(_('LDAP configuration is broken, please contact your administrator'))
            if not user.pk:
                user._changed = False
                user.save()
            UserExternalId.objects.create(
                user=user, source=realm, external_id=external_id, external_guid=guid
            )

    def _record_failure_for_user(self, request, reason, user_id, block, conn, attributes=None):
        user = None
        if not reason or not hasattr(request, 'failed_logins'):
            return
        attributes = attributes or self.get_ldap_attributes(block, conn, user_id)
        ou = self._get_target_ou(block)
        for lookup_type in block['lookups']:
            if lookup_type == 'external_id' and attributes:
                user = self._lookup_by_external_id(block=block, attributes=attributes)
            elif lookup_type == 'username':
                user = self._lookup_by_username(ou=ou, block=block, username=user_id)
            elif lookup_type == 'email' and attributes:
                user = self._lookup_by_email(ou=ou, block=block, attributes=attributes)
            if user:
                break
        if user:
            request.failed_logins.update({user: {'username': user_id, 'reason': reason}})

    def _return_user(self, dn, password, conn, block, attributes=None):
        attributes = attributes or self.get_ldap_attributes(block, conn, dn)
        if attributes is None:
            # attributes retrieval failed
            return
        log.debug('retrieved attributes for %r: %r', dn, attributes)
        username = self.create_username(block, attributes)
        if not username:
            return
        with atomic():
            return self._return_django_user(dn, username, password, conn, block, attributes)

    def _return_django_user(self, dn, username, password, conn, block, attributes):
        from authentic2.manager.journal_event_types import ManagerUserActivation

        user = self._lookup_existing_user(username, block, attributes, lock=True)
        if user:
            log.debug('found existing user %r', user)
        else:
            user = LDAPUser(username=username)
            user._created = True
            user.set_unusable_password()
        user.ldap_init(block, dn)
        user.keep_password(password)
        self.populate_user(user, dn, username, conn, block, attributes)
        if not user.pk or getattr(user, '_changed', False):
            user.save()

        if not is_user_authenticable(user):
            return None

        if not user.is_active and user.deactivation_reason and user.deactivation_reason.startswith('ldap-'):
            user.mark_as_active()
            ldap_uri = conn.get_option(ldap.OPT_URI)
            ManagerUserActivation.record(target_user=user, reason='ldap-reactivation', origin=ldap_uri)

        user_login_success(user.get_username())
        return user

    def has_usable_password(self, user):
        return True

    def get_saml2_authn_context(self):
        return lasso.SAML2_AUTHN_CONTEXT_PASSWORD

    @classmethod
    def get_attribute_names(cls):
        names = set()
        for block in cls.get_config():
            names.update(cls.get_ldap_attributes_names(block))
            names.update(map_text(block['mandatory_attributes_values']).keys())
            names.update(map_text(block['extra_attributes']).keys())
        return [(a, '%s (LDAP)' % a) for a in sorted(names)]

    @classmethod
    def paged_search(cls, block, conn, *args, **kwargs):
        page_size = block.get('page_size', 100)
        pg_ctrl = SimplePagedResultsControl(criticality=True, size=page_size, cookie='')
        while True:
            msgid = conn.search_ext(*args, serverctrls=[pg_ctrl], **kwargs)
            dummy_result_type, data, msgid, serverctrls = conn.result3(msgid)
            yield from cls.normalize_ldap_results(data)
            if not serverctrls:
                break
            pg_ctrl.cookie = serverctrls[0].cookie
            if not pg_ctrl.cookie:
                break

    @classmethod
    def get_users_for_block(cls, block):
        log.info('Synchronising users from realm "%s"', block['realm'])
        conn = cls.get_connection(block, synchronization=True)
        if conn is None:
            log.warning('unable to synchronize with LDAP servers %s', force_str(block['url']))
            return
        cls.check_group_to_role_mappings(block)
        user_basedn = force_str(block.get('user_basedn') or block['basedn'])
        user_filter = cls.get_sync_ldap_user_filter(block)
        attribute_names = cls.get_ldap_attributes_names(block)
        results = cls.paged_search(
            block, conn, user_basedn, ldap.SCOPE_SUBTREE, user_filter, attrlist=attribute_names
        )
        backend = cls()
        for dn, attrs in results:
            try:
                user = backend._return_user(dn, None, conn, block, attrs)
            except UserCreationError:
                user = None
            if not user:
                log.warning('unable to retrieve user for dn %s', dn)
                continue
            if user._changed or user._created:
                log.info(
                    '%s user %s (uuid %s) from %s',
                    'Created' if user._created else 'Updated',
                    user.get_username(),
                    user.uuid,
                    ', '.join('%s=%s' % (k, v) for k, v in attrs.items()),
                )
            yield user

    @classmethod
    def get_users(cls, realm=None):
        blocks = cls.get_config()
        if not blocks:
            log.info('No LDAP server configured.')
            return
        for block in blocks:
            if realm and realm != block['realm']:
                continue
            if block['provisionning'] is False:
                continue
            count = 0
            try:
                for user in cls.get_users_for_block(block):
                    count += 1
                    yield user
            except ldap.LDAPError as e:
                log.error('synchronization failed on an LDAP error (%s)', ldap_error_str(e))
            user_filter = cls.get_sync_ldap_user_filter(block)
            log.info('Search for %s returned %s users.', user_filter, count)

    @classmethod
    def update_mapped_roles_list(cls):
        blocks = cls.get_config()
        if not blocks:
            log.info('No LDAP server configured.')
            return
        known_mapped_roles = set()
        for block in blocks:
            for dummy, role_names in block.get('group_to_role_mapping'):
                for role_name in role_names:
                    role, error = cls.get_role(block, role_id=role_name)
                    if role is not None:
                        known_mapped_roles.add(role.id)
                    else:
                        log.warning(
                            "error %s: couldn't retrieve role %r during mapping list update", error, role_name
                        )
        # unmapped roles become assignable again
        Role.objects.filter(can_manage_members=False).exclude(id__in=known_mapped_roles).update(
            can_manage_members=True
        )
        # on the contrary mapped roles' members list is readonly
        Role.objects.filter(can_manage_members=True, id__in=known_mapped_roles).update(
            can_manage_members=False
        )

    @classmethod
    def deactivate_orphaned_users(cls):
        from authentic2.manager.journal_event_types import ManagerUserDeactivation

        for block in cls.get_config():
            conn = cls.get_connection(block)
            if conn is None:
                continue
            ldap_uri = conn.get_option(ldap.OPT_URI)
            eids = list(
                UserExternalId.objects.filter(user__is_active=True, source=block['realm']).values_list(
                    'external_id', flat=True
                )
            )
            guids = set(
                UserExternalId.objects.filter(user__is_active=True, source=block['realm']).values_list(
                    'external_guid', flat=True
                )
            )
            basedn = force_str(block.get('user_basedn') or block['basedn'])
            attribute_names = list(
                {a[0] for a in cls.attribute_name_from_external_id_tuple(block['external_id_tuples'])}
                | set(USUAL_GUID_ATTRIBUTES)
            )
            user_filter = cls.get_sync_ldap_user_filter(block)
            results = cls.paged_search(
                block, conn, basedn, ldap.SCOPE_SUBTREE, user_filter, attrlist=attribute_names
            )
            for dn, attrs in results:
                data = attrs.copy()
                data['dn'] = dn
                _attribute, guid = cls._get_guid(data)
                if guid and guid in guids:
                    guids.discard(guid)
                for eid_tuple in map_text(block['external_id_tuples']):
                    backend = cls()
                    external_id = backend.build_external_id(eid_tuple, data)
                    if external_id:
                        try:
                            eids.remove(external_id)
                        except ValueError:
                            pass
            for eid in UserExternalId.objects.filter(user__is_active=True, source=block['realm']).filter(
                Q(external_id__in=eids, external_guid__isnull=True)
                | Q(external_guid__in=guids, external_id__isnull=True)
                | Q(external_guid__in=guids, external_id__in=eids)
            ):
                if eid.user.is_active:
                    eid.user.mark_as_inactive(reason=LDAP_DEACTIVATION_REASON_NOT_PRESENT)
                    ManagerUserDeactivation.record(
                        target_user=eid.user, reason=LDAP_DEACTIVATION_REASON_NOT_PRESENT, origin=ldap_uri
                    )
        # Handle users of old sources
        uei_qs = UserExternalId.objects.exclude(source__in=[block['realm'] for block in cls.get_config()])
        for user in User.objects.filter(userexternalid__in=uei_qs):
            if user.is_active:
                user.mark_as_inactive(reason=LDAP_DEACTIVATION_REASON_OLD_SOURCE)
                ManagerUserDeactivation.record(target_user=user, reason=LDAP_DEACTIVATION_REASON_OLD_SOURCE)

    @classmethod
    def ad_encoding(cls, s):
        '''Encode a string for AD consumption as a password'''
        return (f'"{s}"').encode('utf-16-le')

    @classmethod
    def modify_password(cls, conn, block, dn, old_password, new_password):
        '''Change user password with adaptation for Active Directory'''
        serverctrls = []
        if block.get('use_ppolicy_controls'):
            serverctrls = [ppolicy.PasswordPolicyControl()]

        try:
            if old_password is not None and (block['use_password_modify'] and not block['active_directory']):
                results = conn.passwd_s(dn, old_password, new_password, serverctrls=serverctrls)
            else:
                modlist = []
                if block['active_directory']:
                    attr = 'unicodePwd'
                    value = cls.ad_encoding(new_password)
                    if old_password:
                        modlist = [
                            (ldap.MOD_DELETE, attr, [cls.ad_encoding(old_password)]),
                            (ldap.MOD_ADD, attr, [value]),
                        ]
                    else:
                        modlist = [(ldap.MOD_REPLACE, attr, [value])]
                else:
                    key = 'userPassword'
                    modlist = [(ldap.MOD_REPLACE, key, [new_password.encode('utf8')])]
                results = conn.modify_ext_s(dn, modlist, serverctrls=serverctrls)
            if block.get('use_ppolicy_controls') and len(results) >= 3:
                cls.process_modify_password_controls(block, conn, dn, results[3])
        except ldap.LDAPError as e:
            if block.get('use_ppolicy_controls') and len(e.args) > 0 and 'ctrls' in e.args[0]:
                cls.process_modify_password_controls(block, conn, dn, DecodeControlTuples(e.args[0]['ctrls']))
            raise

        log.debug('modified password for dn %r', dn)

    @classmethod
    def normalize_ldap_results(cls, results, encoding='utf-8'):
        new_results = []

        for dn, attrs in results:
            # ignore referrals
            if not dn:
                continue
            new_attrs = {'dn': dn}
            for key in attrs:
                try:
                    new_attrs[key.lower()] = [force_str(value, encoding) for value in attrs[key]]
                except UnicodeDecodeError:
                    log.debug('unable to decode attribute %r as UTF-8, converting to base64', key)
                    new_attrs[key.lower()] = [base64.b64encode(value).decode('ascii') for value in attrs[key]]
            new_results.append((dn.lower(), new_attrs))
        return new_results

    @classmethod
    def get_connections(cls, block, credentials=(), raises=False, synchronization=False):
        '''Try each replicas, and yield successfull connections'''
        if not block['url']:
            raise ImproperlyConfigured("block['url'] must contain at least one url")
        errmsg = None
        for url in map_text(block['url']):
            for key, value in block['global_ldap_options'].items():
                ldap.set_option(key, value)
            conn = LDAPObject(url)
            if block['timeout'] > 0 and synchronization is False:
                conn.set_option(ldap.OPT_NETWORK_TIMEOUT, block['timeout'])
                conn.set_option(ldap.OPT_TIMEOUT, block['timeout'])
            elif block['sync_timeout'] > 0 and synchronization is True:
                conn.set_option(ldap.OPT_NETWORK_TIMEOUT, block['sync_timeout'])
                conn.set_option(ldap.OPT_TIMEOUT, block['sync_timeout'])
            conn.set_option(
                ldap.OPT_X_TLS_REQUIRE_CERT, getattr(ldap, 'OPT_X_TLS_' + block['require_cert'].upper())
            )
            if block['cacertfile']:
                conn.set_option(ldap.OPT_X_TLS_CACERTFILE, block['cacertfile'])
            if block['cacertdir']:
                conn.set_option(ldap.OPT_X_TLS_CACERTDIR, block['cacertdir'])
            if block['certfile']:
                conn.set_option(ldap.OPT_X_TLS_CERTFILE, block['certfile'])
            if block['keyfile']:
                conn.set_option(ldap.OPT_X_TLS_KEYFILE, block['keyfile'])
            for key, value in block['ldap_options']:
                conn.set_option(key, value)
            conn.set_option(ldap.OPT_REFERRALS, 1 if block['referrals'] else 0)
            # allow TLS options to be applied
            conn.set_option(ldap.OPT_X_TLS_NEWCTX, 0)
            try:
                if not url.startswith('ldaps://') and block['use_tls']:
                    try:
                        conn.start_tls_s()
                    except ldap.CONNECT_ERROR:
                        errmsg = (
                            'connection to %r failed when activating TLS, did you forget to '
                            'declare the TLS certificate in /etc/ldap/ldap.conf ?'
                        ) % url
                        log.error(errmsg)
            except ldap.TIMEOUT:
                errmsg = 'connection to %r timed out' % url
                log.error(errmsg)
            except ldap.CONNECT_ERROR:
                errmsg = (
                    'connection to %r failed when activating TLS, did you forget to declare '
                    'the TLS certificate in /etc/ldap/ldap.conf ?'
                ) % url
                log.error(errmsg)
            except ldap.SERVER_DOWN:
                errmsg = 'ldap %r is down' % url
                if block['replicas']:
                    log.warning(errmsg)
                else:
                    log.error(errmsg)
            except ldap.UNAVAILABLE:
                errmsg = "ldap %r's system agent (DSA) is unavailable" % url
                log.error(errmsg)
            if errmsg:
                continue
            user_credentials = block['connect_with_user_credentials'] and credentials
            success, error = cls.bind(block, conn, credentials=user_credentials)
            if success:
                yield conn
            else:
                errmsg = 'admin bind failed on %s: %s' % (url, error)
                if block['replicas']:
                    log.warning(errmsg)
                else:
                    log.error(errmsg)
        if raises and errmsg:
            exception = ldap.LDAPError(errmsg)
            exception.url = url
            raise exception

    @classmethod
    def bind(cls, block, conn, credentials=()):
        '''Bind to the LDAP server'''
        ldap_uri = conn.get_option(ldap.OPT_URI)
        try:
            if credentials:
                who, password = credentials[0], credentials[1]
                password = force_str(password)
                conn.simple_bind_s(who, password)
                log_message = 'with user %s' % who
            elif block['bindsasl']:
                sasl_mech, who, sasl_params = map_text(block['bindsasl'])
                handler_class = getattr(ldap.sasl, sasl_mech)
                auth = handler_class(*sasl_params)
                conn.sasl_interactive_bind_s(who, auth)
                log_message = 'with account %s' % who
            elif block['binddn'] and block['bindpw']:
                who = force_str(block['binddn'])
                conn.simple_bind_s(who, force_str(block['bindpw']))
                log_message = 'with binddn %s' % who
            else:
                who = 'anonymous'
                conn.simple_bind_s()
                log_message = 'anonymously'
            log.info('Binding to server %s (%s)', ldap_uri, log_message)
            return True, None
        except ldap.STRONG_AUTH_REQUIRED:
            return False, 'strong auth required'
        except ldap.INVALID_CREDENTIALS:
            return False, 'invalid credentials'
        except ldap.INVALID_DN_SYNTAX:
            return False, 'invalid dn syntax %s' % who
        except ldap.CONNECT_ERROR:
            return False, 'connection error'
        except ldap.TIMEOUT:
            return False, 'timeout'
        except ldap.SERVER_DOWN:
            if block['use_tls']:
                url = urllib.parse.urlparse(ldap_uri)
                hostname = url.hostname
                port = url.port or 636
                context = ssl.create_default_context()
                try:
                    with socket.create_connection((hostname, port), timeout=2) as sock:
                        with context.wrap_socket(sock, server_hostname=hostname):
                            pass
                except (socket.herror, socket.gaierror) as e:
                    return False, 'socket address error on host %s: %s' % (hostname, e)
                except TimeoutError:
                    return False, 'socket timeout error on host %s' % hostname
                except (OSError, ssl.SSLError) as e:
                    return False, 'ssl error on host %s: %s' % (hostname, e)
                return False, 'ldap is down yet no ssl error was detected on host %s' % hostname
            return False, 'ldap is down'

    @classmethod
    def get_connection(cls, block, credentials=(), raises=False, synchronization=False):
        '''Try to get at least one connection'''
        for conn in cls.get_connections(
            block, credentials=credentials, raises=raises, synchronization=synchronization
        ):
            return conn

    @classmethod
    def update_default(cls, block, validate=True):
        '''Add missing key to block based on default values'''
        for key in block:
            if key not in cls._VALID_CONFIG_KEYS and validate:
                raise ImproperlyConfigured(
                    '"{}" : invalid LDAP_AUTH_SETTINGS key, available are {}'.format(
                        key, cls._VALID_CONFIG_KEYS
                    )
                )

        for r in cls._REQUIRED:
            if r not in block:
                raise ImproperlyConfigured('LDAP_AUTH_SETTINGS: missing required configuration option %r' % r)

        # convert string to list of strings for settings accepting it
        for i in cls._TO_ITERABLE:
            if i in block and isinstance(block[i], str):
                block[i] = (block[i],)

        email_authn = get_password_authenticator().accept_email_authentication
        for d, value in cls._DEFAULTS.items():
            if d not in block:
                block[d] = value
                if d == 'user_filter' and email_authn:
                    block[d] = '(|(mail=%s)(uid=%s))'
            else:
                if isinstance(value, str):
                    if not isinstance(block[d], str):
                        raise ImproperlyConfigured('LDAP_AUTH_SETTINGS: attribute %r must be a string' % d)
                    try:
                        block[d] = force_str(block[d])
                    except UnicodeEncodeError:
                        raise ImproperlyConfigured('LDAP_AUTH_SETTINGS: attribute %r must be a string' % d)
                if isinstance(value, bool) and not isinstance(block[d], bool):
                    raise ImproperlyConfigured('LDAP_AUTH_SETTINGS: attribute %r must be a boolean' % d)
                if isinstance(value, (list, tuple)) and not isinstance(block[d], (list, tuple)):
                    raise ImproperlyConfigured(
                        'LDAP_AUTH_SETTINGS: attribute %r must be a list or a tuple' % d
                    )
                if isinstance(value, dict) and not isinstance(block[d], dict):
                    raise ImproperlyConfigured('LDAP_AUTH_SETTINGS: attribute %r must be a dictionary' % d)
                if not isinstance(value, bool) and d in cls._REQUIRED and not block[d]:
                    raise ImproperlyConfigured('LDAP_AUTH_SETTINGS: attribute %r is required but is empty')
                # force_bytes all strings in iterable or dict
                if isinstance(block[d], (list, tuple, dict)):
                    block[d] = map_text(block[d])
        # lowercase LDAP attribute names
        assert block['external_id_tuples'] is not None
        for key in cls._TO_LOWERCASE:
            # we handle strings, list of strings and list of list or tuple whose first element is a
            # string
            if isinstance(block[key], str):
                block[key] = force_str(block[key]).lower()
            elif isinstance(block[key], (list, tuple)):
                new_seq = []
                for elt in block[key]:
                    if isinstance(elt, str):
                        new_seq.append(elt.lower())
                    elif isinstance(elt, (list, tuple)):
                        new_elt = []
                        for subelt in elt:
                            if isinstance(subelt, str):
                                subelt = subelt.lower()
                            new_elt.append(subelt)
                        new_seq.append(new_elt)
                block[key] = new_seq
            elif isinstance(block[key], dict):
                newdict = {}
                for subkey in block[key]:
                    newdict[force_str(subkey).lower()] = block[key][subkey]
                block[key] = newdict
            else:
                raise NotImplementedError(
                    'LDAP setting %r cannot be converted to lowercase setting, its type is %r'
                    % (key, type(block[key]))
                )
        # special case user_attributes
        user_attributes = []
        for mapping in block['user_attributes']:
            if 'from_ldap' not in mapping or 'to_user' not in mapping:
                continue
            from_ldap = mapping['from_ldap']
            if not isinstance(from_ldap, str):
                continue
            from_ldap = from_ldap.lower()
            user_attributes.append({'from_ldap': from_ldap, 'to_user': mapping['to_user']})
        block['user_attributes'] = user_attributes
        # Want to randomize our access, otherwise what's the point of having multiple servers?
        block['url'] = list(block['url'])
        if block['shuffle_replicas']:
            random.shuffle(block['url'])


class LDAPBackendPasswordLost(LDAPBackend):
    def authenticate(self, request, user=None):
        if not user:
            return
        config = self.get_config()
        if not config:
            return
        for user_external_id in user.userexternalid_set.all():
            external_id = user_external_id.external_id
            if not external_id:
                continue
            for block in config:
                if block['authentication'] is False:
                    continue
                if user_external_id.source != force_str(block['realm']):
                    continue
                for external_id_tuple in map_text(block['external_id_tuples']):
                    conn = self.ldap_backend.get_connection(block)
                    if not conn:
                        log.warning('ldap: password-lost authenticate failed, could not get a connection')
                        continue
                    try:
                        if external_id_tuple == ('dn:noquote',):
                            dn = external_id
                            results = conn.search_s(dn, ldap.SCOPE_BASE)
                        else:
                            ldap_filter = self.external_id_to_filter(external_id, external_id_tuple)
                            results = conn.search_s(block['basedn'], ldap.SCOPE_SUBTREE, ldap_filter)
                            results = self.normalize_ldap_results(results)
                            if not results:
                                log.warning(
                                    'unable to find user %r based on external id %s', user, external_id
                                )
                                continue
                            dn = results[0][0]
                    except ldap.LDAPError as e:
                        log.warning(
                            'unable to find user %r based on external id %s (%s)',
                            user,
                            external_id,
                            ldap_error_str(e),
                        )
                        continue
                    try:
                        return self._return_user(dn, None, conn, block)
                    except UserCreationError as e:
                        messages.error(request, str(e))
                        return None


LDAPUser.ldap_backend = LDAPBackend
LDAPBackendPasswordLost.ldap_backend = LDAPBackend
