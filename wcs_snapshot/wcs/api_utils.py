# w.c.s. - web application for online forms
# Copyright (C) 2005-2013  Entr'ouvert
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

import base64
import datetime
import hashlib
import hmac
import random
import urllib.parse

from django.utils.encoding import force_bytes, force_str
from quixote import get_publisher, get_request

from .qommon import _
from .qommon.errors import AccessForbiddenError, HttpResponse401Error, UnknownNameIdAccessForbiddenError
from .qommon.misc import simplify

DEFAULT_DURATION = 30


def is_url_signed(utcnow=None, duration=DEFAULT_DURATION):
    from .sql import ApiAccess

    if not get_request():
        return False
    if get_request().signed:
        return True
    query_string = get_request().get_query()
    if not query_string:
        return False
    signature = get_request().form.get('signature')
    if not isinstance(signature, str):
        return False
    signature = force_bytes(signature)
    # verify signature
    orig = get_request().form.get('orig')
    if not isinstance(orig, str):
        raise AccessForbiddenError(_('Missing/multiple orig field.'))
    key = ApiAccess.get_access_key(orig) or get_publisher().get_site_option(orig, 'api-secrets')
    if not key:
        raise AccessForbiddenError(_('Invalid orig.'))
    algo = get_request().form.get('algo')
    if not isinstance(algo, str):
        raise AccessForbiddenError(_('Missing/multiple algo field.'))
    if algo not in hashlib.algorithms_guaranteed:
        raise AccessForbiddenError(_('Invalid algo.'))
    try:
        algo = getattr(hashlib, algo)
    except AttributeError:
        raise AccessForbiddenError(_('Invalid algo.'))
    if signature != base64.standard_b64encode(
        hmac.new(
            force_bytes(key), force_bytes(query_string[: query_string.find('&signature=')]), algo
        ).digest()
    ):
        raise AccessForbiddenError(_('Invalid signature.'))
    timestamp = get_request().form.get('timestamp')
    if not isinstance(timestamp, str):
        raise AccessForbiddenError(_('Missing/multiple timestamp field.'))
    try:
        timestamp = datetime.datetime.strptime(timestamp, '%Y-%m-%dT%H:%M:%SZ')
    except ValueError as e:
        raise AccessForbiddenError(_('Invalid timestamp field; %s.') % e)
    delta = (utcnow or datetime.datetime.now(datetime.UTC)).replace(tzinfo=None) - timestamp
    if abs(delta) > datetime.timedelta(seconds=duration):
        period = 'past'
        if delta.total_seconds() < 0:
            delta = abs(delta)
            period = 'future'
        raise AccessForbiddenError(f'timestamp is more than {duration} seconds in the {period}: {delta}')
    # check nonce
    nonce = get_request().form.get('nonce')
    if nonce:
        # normalize nonce
        nonce = simplify(nonce[:128]).replace('/', '-')
        dummy, created = get_publisher().token_class.get_or_create(
            type='nonce', id=nonce, expiration_delay=duration
        )
        if not created:
            raise AccessForbiddenError(_('Nonce already used.'))
    get_request().signed = True
    return True


def check_http_basic_auth(api_name):
    auth_header = get_request().get_header('Authorization', '')
    if not auth_header.startswith('Basic '):
        # we do not handle other authentication schemes
        raise HttpResponse401Error(api_name, 'unhandled authorization header')
    auth_header = auth_header.split(' ', 1)[1]
    try:
        username, password = force_str(base64.decodebytes(force_bytes(auth_header))).split(':', 1)
    except ValueError:  # invalid base64 or not enough values to unpack
        raise HttpResponse401Error(api_name, 'invalid authorization header')
    configured_password = get_publisher().get_site_option(username, section='api-http-auth-%s' % api_name)
    if configured_password != password:
        raise HttpResponse401Error(api_name, 'invalid authorization')


def get_user_from_api_query_string(api_name=None):
    from .sql import ApiAccess

    # check signature or auth header
    if not is_url_signed():
        user = getattr(get_request(), 'user', None)
        if user and user.is_api_user:
            return user
        if api_name:
            check_http_basic_auth(api_name)
        else:
            return None

    # check access restriction defined in API access object
    orig = get_request().form.get('orig')
    if orig:
        api_access = ApiAccess.get_by_identifier(orig)
        if api_access and api_access.get_roles():
            return api_access.get_as_api_user()

    # get user reference from query string
    user = None
    if get_request().form.get('email'):
        email = get_request().form.get('email')
        if not isinstance(email, str):
            raise AccessForbiddenError(_('Multiple email field.'))
        users = list(get_publisher().user_class.get_users_with_email(email))
        if users:
            user = users[0]
        else:
            raise AccessForbiddenError(_('Unknown email.'))
    elif get_request().form.get('NameID'):
        ni = get_request().form.get('NameID')
        if not isinstance(ni, str):
            raise AccessForbiddenError(_('Multiple NameID field.'))
        users = list(get_publisher().user_class.get_users_with_name_identifier(ni))
        if users:
            user = users[0]
        else:
            raise UnknownNameIdAccessForbiddenError(_('Unknown NameID.'))
    elif 'email' in get_request().form or 'NameID' in get_request().form:
        # email or NameID were given as empty to the query string, this maps
        # the anonymous user case.
        return False

    return user


def sign_url(url, key, algo='sha256', timestamp=None, nonce=None):
    parsed = urllib.parse.urlparse(url)
    new_query = sign_query(parsed.query, key, algo, timestamp, nonce)
    return urllib.parse.urlunparse(parsed[:4] + (new_query,) + parsed[5:])


def sign_query(query, key, algo='sha256', timestamp=None, nonce=None):
    if timestamp is None:
        timestamp = datetime.datetime.now(datetime.UTC)
    timestamp = timestamp.strftime('%Y-%m-%dT%H:%M:%SZ')
    if nonce is None:
        # rstrip('L') for py2/3 compatibility, as py2 formats number as 0x...L, and py3 as 0x...
        nonce = hex(random.getrandbits(128))[2:].rstrip('L')
    new_query = query
    if new_query:
        new_query += '&'
    new_query += urllib.parse.urlencode((('algo', algo), ('timestamp', timestamp), ('nonce', nonce)))
    signature = base64.b64encode(sign_string(new_query, key, algo=algo))
    new_query += '&signature=' + urllib.parse.quote(signature)
    return new_query


def sign_string(s, key, algo='sha256', timedelta=30):
    digestmod = getattr(hashlib, algo)
    hash = hmac.HMAC(force_bytes(key), digestmod=digestmod, msg=force_bytes(s))
    return hash.digest()


class MissingSecret(Exception):
    pass


def get_secret_and_orig(url):
    frontoffice_url = get_publisher().get_frontoffice_url()
    orig = urllib.parse.urlparse(frontoffice_url).netloc.rsplit('@', 1)[-1].rsplit(':', 1)[0]
    target_orig = urllib.parse.urlparse(url).netloc.rsplit('@', 1)[-1].rsplit(':', 1)[0]
    secret = get_publisher().get_site_option(target_orig, 'wscall-secrets')
    if not secret:
        raise MissingSecret()
    return secret, orig


def sign_url_auto_orig(url):
    try:
        signature_key, orig = get_secret_and_orig(url)
    except MissingSecret:
        return url
    parsed = urllib.parse.urlparse(url)
    querystring = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    querystring.append(('orig', orig))
    querystring = urllib.parse.urlencode(querystring)
    url = urllib.parse.urlunparse(parsed[:4] + (querystring,) + parsed[5:6])
    return sign_url(url, signature_key)


def get_query_flag(flag, default=False):
    value = get_request() and get_request().form.get(flag)
    if value in (True, 'True', 'true', 'on', '1'):
        return True
    if value in (False, 'False', 'false', 'off', '0'):
        return False
    return default
