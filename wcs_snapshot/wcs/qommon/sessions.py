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

import hashlib
import json
import os
import time

from django.conf import settings
from django.core.signing import BadSignature, Signer
from django.utils.encoding import force_bytes
from quixote import get_publisher, get_session
from quixote.publish import get_session_manager
from quixote.session import Session as QuixoteSession
from quixote.session import SessionManager as QuixoteSessionManager
from quixote.util import randbytes

from .storage import StorableObject
from .upload_storage import get_storage_object


class QommonSession(QuixoteSession):
    pass


class CaptchaSession:
    MAX_CAPTCHA_TOKENS = 8
    _captcha_tokens = None
    won_captcha = False

    def create_captcha_token(self):
        if not self._captcha_tokens:
            self._captcha_tokens = []
        token = {'token': randbytes(8), 'answer': None}
        self._captcha_tokens.append(token)
        extra = len(self._captcha_tokens) - self.MAX_CAPTCHA_TOKENS
        if extra > 0:
            del self._captcha_tokens[:extra]
        return token

    def get_captcha_token(self, token):
        if not self._captcha_tokens:
            return None
        try:
            return [x for x in self._captcha_tokens if x.get('token') == token][0]
        except IndexError:
            return None

    def remove_captcha_token(self, token):
        token = self.get_captcha_token(token)
        if token:
            self._captcha_tokens.remove(token)

    def has_info(self):
        return self.won_captcha or self._captcha_tokens


class Session(QommonSession, CaptchaSession, StorableObject):
    _names = 'sessions'

    name_identifier = None
    lasso_session_dump = None
    lasso_session_index = None
    lasso_identity_provider_id = None
    message = None
    saml_authn_context = None
    saml_idp_cookie = None
    ident_idp_token = None
    has_uploads = False
    jsonp_display_values = None
    extra_variables = None
    expire = None
    forced = False
    # should only be overwritten by authentication methods
    extra_user_variables = None
    opened_session_value = None

    username = None  # only set on password authentication

    def force(self):
        # add some data in the session, it will force a cookie to be set, this
        # is used so we get a session identifier fixed even on the first page
        # of a form.
        self.forced = True
        get_session_manager().maintain_session(self)

    def set_expire(self, expire):
        self.expire = expire

    def set_duration(self, duration):
        self.set_expire(time.time() + duration)

    def is_expired(self):
        if self.expire:
            return time.time() >= self.expire
        duration = get_publisher().get_site_option('session_max_age')
        if duration is None:
            return False
        try:
            duration = int(duration)
        except ValueError:
            return False
        return (time.time() - self.get_access_time()) > duration

    def has_info(self):
        return (
            self.name_identifier
            or self.lasso_session_dump
            or self.message
            or self.lasso_identity_provider_id
            or self.saml_authn_context
            or self.ident_idp_token
            or self.has_uploads
            or self.jsonp_display_values
            or self.extra_variables
            or self.opened_session_value
            or CaptchaSession.has_info(self)
            or self.expire
            or self.extra_user_variables
            or self.forced
            or QuixoteSession.has_info(self)
        )

    is_dirty = has_info

    def get_session_id(self):
        return self.id

    def set_session_id(self, session_id):
        self.id = session_id

    session_id = property(get_session_id, set_session_id)

    def has_user(self):
        user_id = QuixoteSession.get_user(self)
        return bool(user_id)

    def get_user_id(self):
        return super().get_user()

    def get_user(self):
        user_id = self.get_user_id()
        if user_id:
            try:
                user = get_publisher().user_class.get(user_id)
            except KeyError:
                return None
            if user.is_active:
                return user
            self.set_user(None)
        return None

    def set_user(self, user_id):  # noqa pylint: disable=arguments-renamed
        self.id = None  # force a new session id to be assigned
        self.extra_user_variables = None
        self.has_uploads = False
        self.jsonp_display_values = None
        QuixoteSession.set_user(self, user_id)
        if user_id is None:
            return
        try:
            user = get_publisher().user_class.get(user_id)
            user.last_seen = time.time()
            user.store()
        except KeyError:
            pass

    def add_message(self, message, *, level='error', job_id=None):
        self.message = {
            'message': str(message),  # str() to force lazy-gettext
            'level': level,
            'job_id': job_id,
        }

    def add_html_message(self, message, *, level='error', job_id=None):
        self.message = {
            'html_message': str(message),  # str() to force lazy-gettext
            'level': level,
            'job_id': job_id,
        }

    def display_message(self):
        if not self.message and not isinstance(self.message, dict):
            return ''
        from quixote.html import htmltext

        job_id = self.message.get('job_id') or ''
        data_job = ' data-job="%s"' if job_id else '%s'

        if self.message.get('html_message'):
            message = htmltext(self.message.get('html_message'))
        else:
            message = self.message.get('message')
        s = htmltext(
            f'<div id="messages"><ul class="messages"><li{data_job} class="%s">%s</li></ul></div>'
        ) % (job_id, self.message['level'], message)
        self.message = None
        return s

    def get_user_object(self):
        return self.get_user()

    def get_authentication_context(self):
        for context in get_publisher().get_supported_authentication_contexts():
            contexts = get_publisher().get_authentication_saml_contexts(context)
            if self.saml_authn_context in contexts:
                return context
        return None

    def get_signer(self):
        return Signer(key=settings.SECRET_KEY + self.id)

    def add_tempfile(self, upload, storage=None):
        dirname = os.path.join(get_publisher().app_dir, 'tempfiles')
        if not os.path.exists(dirname):
            os.mkdir(dirname)
        token = randbytes(8)
        upload.time = time.time()
        upload.token = token
        upload.storage = storage
        get_storage_object(upload.storage).save_tempfile(upload)

        self.has_uploads = True
        if not self.id:
            # the token is signed with the session id so we need to have it
            # created now.
            get_session_manager().maintain_session(self)

        signer = self.get_signer()
        data = {
            'orig_filename': upload.orig_filename,
            'base_filename': upload.base_filename,
            'content_type': upload.content_type,
            'charset': upload.charset,
            'size': getattr(upload, 'size', None),
            'session': self.id,
            'token': signer.sign(token),
            'unsigned_token': token,
            'storage': upload.storage,
            'storage-attrs': getattr(upload, 'storage_attrs', None),
        }
        filename = os.path.join(get_publisher().app_dir, 'tempfiles', upload.token)
        with open(filename + '.json', 'w') as fd:
            json.dump(data, fd, indent=2)

        return data

    def get_tempfile(self, token):
        if not token:
            return None
        if not self.id:  # missing session
            return None
        signer = self.get_signer()
        try:
            value = signer.unsign(token)
        except (BadSignature, UnicodeDecodeError):
            return None
        dirname = os.path.join(get_publisher().app_dir, 'tempfiles')
        filename = os.path.join(dirname, value + '.json')
        if not os.path.exists(filename):
            return None
        with open(filename) as fd:
            return json.loads(fd.read())

    def get_tempfile_path(self, token):
        temp = self.get_tempfile(token)
        if not temp:
            return None
        dirname = os.path.join(get_publisher().app_dir, 'tempfiles')
        filename = os.path.join(dirname, temp['unsigned_token'])
        return filename

    def get_tempfile_content(self, token):
        temp = self.get_tempfile(token)
        if not temp:
            return temp

        return get_storage_object(temp.get('storage')).get_tempfile(temp)

    def add_extra_variables(self, **kwargs):
        if not self.extra_variables:
            self.extra_variables = {}
        self.extra_variables.update(kwargs)

    def get_substitution_variables(self, prefix='session_'):
        d = {}
        d[prefix + 'hash_id'] = hashlib.sha1(force_bytes(self.id)).hexdigest()
        if self.extra_variables:
            for k, v in self.extra_variables.items():
                d[prefix + 'var_' + k] = v
        if self.extra_user_variables:
            for k, v in self.extra_user_variables.items():
                d[prefix + 'var_user_' + k] = v
        return d

    @classmethod
    def get_sessions_for_saml(cls, name_identifier=Ellipsis, session_indexes=()):
        return (
            x
            for x in cls.values()
            if (not session_indexes or x.lasso_session_index in session_indexes)
            and name_identifier in (x.name_identifier or [])
        )

    def get_jsonp_display_value(self, key):
        value = (self.jsonp_display_values or {}).get(key)
        if not isinstance(value, tuple):
            return value  # legacy value, or None
        return value[1]

    def set_jsonp_display_value(self, key, value):
        if not self.jsonp_display_values:
            self.jsonp_display_values = {}
        self.jsonp_display_values[key] = (time.time(), value)
        if len(self.jsonp_display_values) > 20:
            # keep at most 20 items
            timestamp_keys = [(y[0], x) for x, y in self.jsonp_display_values.items() if y]
            timestamp_keys.sort()
            self.jsonp_display_values.pop(timestamp_keys[0][1])


class QommonSessionManager(QuixoteSessionManager):
    pass


class StorageSessionManager(QommonSessionManager):
    def forget_changes(self, session):
        pass

    def __getitem__(self, session_id):
        try:
            session = self.session_class.get(session_id)
            if session.is_expired():
                try:
                    session.remove_self()
                except OSError:
                    pass
                raise KeyError
            return session
        except KeyError:
            raise KeyError

    def get(self, session_id, default=None):
        try:
            return self[session_id]
        except KeyError:
            return default
        except ValueError:  # happens for "insecure string pickle"
            return default

    def commit_changes(self, session):
        if session and session.id:
            session.store()

    def keys(self):
        return self.session_class.keys()

    def values(self):
        return self.session_class.values()

    def items(self):
        return self.session_class.items()

    def has_key(self, session_id):
        return self.session_class.has_key(session_id)

    def __setitem__(self, session_id, session):
        session.store()

    def __delitem__(self, session_id):
        if not session_id:
            return
        self.session_class.remove_object(session_id)

    def get_sessions_for_saml(self, name_identifier=Ellipsis, session_indexes=()):
        return self.session_class.get_sessions_for_saml(name_identifier, session_indexes)

    def get_session_for_saml(self, name_identifier=None, session_index=None):
        if session_index:
            session_indexes = (session_index,)
        else:
            session_indexes = ()
        for session in self.get_sessions_for_saml(name_identifier, session_indexes):
            return session
        return None

    def finish_successful_request(self):
        # compared to quixote default session manager, this one doesn't include
        # an extra call to commit_changes, as it would just store again the same data.
        session = get_session()
        if session is not None:
            self.maintain_session(session)
