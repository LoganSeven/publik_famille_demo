# w.c.s. - web application for online forms
# Copyright (C) 2005-2010  Entr'ouvert
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
import copy
import json
import re
import time

import quixote.http_request
from django.http.request import HttpHeaders
from django.utils.encoding import force_bytes, force_str
from quixote import get_publisher, get_session
from quixote.errors import RequestError

from . import _
from .http_response import HTTPResponse
from .timings import TimingsMixin

user_agent_regex = re.compile(r'(?P<product>.*?)(?P<comment>\(.*?\))(?P<rest>.*)')


class HTTPRequest(quixote.http_request.HTTPRequest, TimingsMixin):
    signed = False
    parsed = False
    django_request = None
    is_in_backoffice_forced_value = None
    query_parameters_forced_value = None
    edited_test_id = None

    def __init__(self, *args, **kwargs):
        quixote.http_request.HTTPRequest.__init__(self, *args, **kwargs)
        self.response = HTTPResponse()
        self.charset = 'utf-8'
        self.is_json_marker = None
        self.ignore_session = False
        self.wscalls_cache = {}
        self.datasources_cache = {}
        # keep a copy of environment to make sure it's not reused along
        # uwsgi/gunicorn processes.
        self.environ = copy.copy(self.environ)

    _user = ()  # use empty tuple instead of None as None is a "valid" user value

    def get_user(self):
        if self._user != ():
            return self._user

        auth_header = self.get_header('Authorization', '')
        if auth_header.startswith('Basic '):
            auth_header = auth_header.split(' ', 1)[1]
            try:
                username, password = force_str(base64.decodebytes(force_bytes(auth_header))).split(':', 1)
            except (UnicodeDecodeError, ValueError):
                # ValueError will catch both missing ":" (not enough values to
                # unpack (expected 2, got 1)) and binascii.Error (incorrect
                # padding or invalid base64-encoded string).
                self._user = None
                return

            from wcs.sql import ApiAccess

            from .ident.password_accounts import PasswordAccount

            try:
                self._user = PasswordAccount.get_with_credentials(username, password)
            except KeyError:
                try:
                    self._user = ApiAccess.get_with_credentials(
                        username, password, self.environ.get('REMOTE_ADDR', None)
                    )
                except KeyError:
                    self._user = None

            return self._user

        try:
            self._user = get_session().get_user()
        except AttributeError:
            self._user = None
        return self._user

    user = property(get_user)

    def get_local_url(self, n=0):
        '''Return the local part of the URL, query string included'''
        query = self.get_query()
        if query:
            return self.get_path(n) + '?' + query
        return self.get_path(n)

    def get_frontoffice_url(self, n=0):
        return get_publisher().get_frontoffice_url(without_script_name=True) + self.get_local_url(n)

    def get_substitution_variables(self):
        from wcs.variables import LazyRequest

        return {'request': LazyRequest(self)}

    def dump(self):
        # straight copy of HTTPRequest.dump(), sole modification is that the
        # values are printed as %r, not %s
        result = []
        row = '%-15s %r'

        if self.form:
            result.append('Form:')
            for k, v in sorted(self.form.items()):
                result.append(row % (k, v))

        result.append('')
        result.append('Cookies:')
        for k, v in sorted(self.cookies.items()):
            result.append(row % (k, v))

        result.append('')
        result.append('Environment:')
        for k, v in sorted(self.environ.items()):
            result.append(row % (k, v))
        return '\n'.join(result)

    def process_inputs(self):
        if self.parsed:
            return
        query = self.get_query()
        if query:
            query_dict = quixote.http_request.parse_query(query, self.charset)
            # quixote will automatically create lists for repeated query items,
            # ignore them for known parameters.
            single_parameters = ['channel', 'NameID', 'ReturnURL', 'cancelurl', 'caller', 'orig']
            for k, v in query_dict.items():
                if isinstance(v, list) and k in single_parameters:
                    query_dict[k] = v[0]
            self.form.update(query_dict)
        length = int(self.environ.get('CONTENT_LENGTH') or '0')
        ctype = self.environ.get('CONTENT_TYPE')
        if self.django_request:
            self.stdin = self.django_request
        if ctype:
            ctype, ctype_params = quixote.http_request.parse_header(ctype)
        if ctype == 'application/x-www-form-urlencoded':
            self._process_urlencoded(length, ctype_params)
        elif ctype == 'multipart/form-data':
            self._process_multipart(length, ctype_params)
        elif ctype == 'application/json' and self.stdin:
            if length:
                payload = self.stdin.read(length)
                try:
                    self._json = json.loads(payload)
                except ValueError as e:
                    raise RequestError(_('Invalid json payload (%s).') % str(e))
            else:
                # consider empty post as an empty dictionary
                self._json = {}
        # remove characters that are not valid XML so it doesn't have to happen
        # down the chain.
        illegal_xml_chars = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]')
        self.form = {
            k: illegal_xml_chars.sub('', v) if isinstance(v, str) else v for k, v in self.form.items()
        }
        self.parsed = True

    @property
    def json(self):
        if not hasattr(self, '_json'):
            raise RequestError(_('Expected JSON but missing appropriate content-type.'))
        return self._json

    def is_json(self):
        if self.is_json_marker:
            return True
        if self.get_header('Content-Type', '').strip() == 'application/json':
            return True
        if self.get_header('Accept', '').strip() == 'application/json':
            return True
        if self.get_query() == 'json':
            return True
        if self.form and self.form.get('format') == 'json':
            return True
        return False

    def is_in_backoffice(self):
        if self.is_in_backoffice_forced_value is not None:
            return self.is_in_backoffice_forced_value

        return self.get_path().startswith('/backoffice/')

    def is_api_url(self):
        return self.get_path().startswith('/api/')

    def is_in_frontoffice(self):
        return not (self.is_in_backoffice() or self.is_api_url())

    def is_from_bot(self):
        user_agent = self.get_environ('HTTP_USER_AGENT', '').lower()
        return bool('bot' in user_agent or 'crawl' in user_agent)

    def is_from_mobile(self):
        user_agent = self.get_environ('HTTP_USER_AGENT', '')
        try:
            dummy, comment, rest = user_agent_regex.match(user_agent).groups()
        except AttributeError:
            return False
        # https://developer.mozilla.org/en-US/docs/Web/HTTP/Browser_detection_using_the_user_agent#mobile_tablet_or_desktop
        # Mozilla (Gecko, Firefox) / Mobile or Tablet inside the comment
        # WebKit-based (Android, Safari) / Mobile Safari token outside the comment
        # Blink-based (Chromium, etc.) / Mobile Safari token outside the comment
        return bool('Mobile' in comment or 'Tablet' in comment or 'Mobile Safari' in rest)

    def has_anonymised_data_api_restriction(self):
        from wcs.sql import ApiAccess

        if 'anonymise' in self.form:
            return True

        orig = self.form.get('orig')
        if orig:
            api_access = ApiAccess.get_by_identifier(orig)
            if api_access:
                return api_access.restrict_to_anonymised_data

        if self.user and self.user.is_api_user and not self.user.is_admin:
            return self.user.api_access.restrict_to_anonymised_data

        return False

    @property
    def META(self):
        return self.environ

    @property
    def headers(self):
        return HttpHeaders(self.META)

    def trace(self, msg):
        print('%.4f' % self.get_duration(), msg)

    def get_duration(self):
        return time.time() - self.t0
