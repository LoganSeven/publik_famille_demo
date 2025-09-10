# w.c.s. - web application for online forms
# Copyright (C) 2005-2017  Entr'ouvert
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

import datetime
import threading
import types
import urllib.parse

import django.template.base
import django.template.defaulttags
import freezegun
import quixote
import quixote.publish

_thread_local = threading.local()

cleanup_orig = quixote.publish.cleanup
PublisherOrig = quixote.publish.Publisher


class Publisher(quixote.publish.Publisher):
    def __init__(self, root_directory, *args, **kwargs):
        try:
            PublisherOrig.__init__(self, root_directory, *args, **kwargs)
        except RuntimeError:
            pass
        self.set_in_thread()
        self.root_directory = root_directory
        self._request = None

    def set_in_thread(self):
        _thread_local.publisher = self


def get_publisher():
    return getattr(_thread_local, 'publisher', None)


def get_request():
    return _thread_local.publisher.get_request()


def get_response():
    return get_request() and _thread_local.publisher.get_request().response


def get_field(name, default=None):
    return _thread_local.publisher.get_request().get_field(name, default)


def get_cookie(name, default=None):
    return _thread_local.publisher.get_request().get_cookie(name, default)


def get_path(n=0):
    return _thread_local.publisher.get_request().get_path(n)


def redirect(location, permanent=False):
    """(location : string, permanent : boolean = false) -> string

    Create a redirection response.  If the location is relative, then it
    will automatically be made absolute.  The return value is an HTML
    document indicating the new URL (useful if the client browser does
    not honor the redirect).
    """
    request = _thread_local.publisher.get_request()
    location = urllib.parse.urljoin(request.get_url(), str(location))
    return request.response.redirect(location, permanent)


def get_session():
    request = _thread_local.publisher.get_request()
    return request.session if request else None


def get_session_manager():
    return _thread_local.publisher.session_manager


def get_user():
    session = _thread_local.publisher.get_request().session
    return session.user if session is not None else None


def cleanup():
    cleanup_orig()
    _thread_local.publisher = None


for key, value in list(locals().items()):
    if type(value) in (types.FunctionType, type):
        setattr(quixote, key, value)
        setattr(quixote.publish, key, value)


if not hasattr(django.template.base, '_monkeypatched'):
    # patch render_value_in_context function to add a complex data mark when
    # "printing" variables.
    django.template.base._monkeypatched = True
    orig_render_value_in_context = django.template.base.render_value_in_context

    def new_render_value_in_context(value, context):
        rendered_value = orig_render_value_in_context(value, context)
        if context.get('allow_complex') and not isinstance(value, str):
            return get_publisher().cache_complex_data(value, rendered_value)
        return rendered_value

    django.template.base.render_value_in_context = new_render_value_in_context
    django.template.defaulttags.render_value_in_context = new_render_value_in_context

if not hasattr(freezegun, '_monkeypatched'):
    # freezegun has incorrect handling of timezones, see https://github.com/spulec/freezegun/issues/348
    freezegun._monkeypatched = True

    def patched_freezgun_astimezone(self, tz=None):
        from freezegun.api import datetime_to_fakedatetime, real_datetime, tzlocal

        if tz is None:
            tz = tzlocal()

        real = self
        if real.tzinfo is None:
            real = self.replace(tzinfo=datetime.timezone(self._tz_offset()))

        return datetime_to_fakedatetime(real_datetime.astimezone(real, tz))

    def patched_freezegun_now(cls, tz=None):
        from freezegun.api import datetime_to_fakedatetime, real_datetime

        now = cls._time_to_freeze() or real_datetime.now()

        result = now + cls._tz_offset()
        result = datetime_to_fakedatetime(result)

        if tz:
            result = cls.astimezone(result, tz)

        return result

    freezegun.api.FakeDatetime.astimezone = patched_freezgun_astimezone
    freezegun.api.FakeDatetime.now = classmethod(patched_freezegun_now)
