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

import urllib.parse

import quixote
from quixote import get_publisher, get_response
from quixote.errors import AccessError, MethodNotAllowedError, PublishError, RequestError, TraversalError
from quixote.html import TemplateIO, htmltext

from . import _, template


class AccessForbiddenError(AccessError):
    backoffice_template_name = None
    err_code = 'access-denied'

    def __init__(self, public_msg=None, private_msg=None, location_hint=None, err_code=None):
        publish_error_init(self, public_msg=public_msg, private_msg=private_msg, err_code=err_code)
        self.location_hint = location_hint

    def render(self):
        from . import _

        if self.public_msg:
            return template.error_page(
                self.public_msg,
                _('Access Forbidden'),
                location_hint=self.location_hint,
                backoffice_template_name=self.backoffice_template_name,
            )

        return template.error_page(
            _('You do not have the required permissions to access this page.'),
            _('Access Forbidden'),
            location_hint=self.location_hint,
            backoffice_template_name=self.backoffice_template_name,
        )


class UnknownNameIdAccessForbiddenError(AccessForbiddenError):
    err_code = 'unknown-name-id'


class AccessUnauthorizedError(AccessForbiddenError):
    def render(self):
        session = quixote.get_session()
        request = quixote.get_request()
        if request.user:
            return AccessForbiddenError.render(self)

        if self.public_msg:
            session.add_message(self.public_msg)
        login_url = get_publisher().get_root_url() + 'login/'
        login_url += '?' + urllib.parse.urlencode({'next': request.get_frontoffice_url()})
        return quixote.redirect(login_url)


class HttpResponse401Error(AccessError):
    status_code = 401
    err_code = 'access-unauthorized'

    def __init__(self, realm, public_msg=None):
        self.realm = realm
        super().__init__(public_msg=public_msg)


class HttpResponse200Error(AccessError):
    status_code = 200


class EmailError(Exception):
    pass


class TooBigEmailError(EmailError):
    limit = 50 * 1024**2

    def __str__(self):
        return _('Email too big to be sent (>%dMB)') % (self.limit / (1024**2))


class InternalServerError(PublishError):
    status_code = 500

    def __init__(self, logged_error):
        super().__init__(_('Technical error'))
        self.logged_error = logged_error

    def render(self):
        from . import _

        get_response().set_title(_('Technical error'))
        r = TemplateIO(html=True)

        r += htmltext('<p>')
        r += str(_('A fatal error happened. It has been recorded and will be available to administrators.'))
        if self.logged_error:
            r += ' (id:%s)' % self.logged_error.id
        r += htmltext('</p>')
        return r.getvalue()


class ConnectionError(Exception):
    def __init__(self, msg):
        self.msg = msg

    def __str__(self):
        return self.msg


class ConfigurationError(PublishError):
    status_code = 501  # "not implemented", as hopefully the error will be fixed.

    def render(self):
        from . import _

        return template.error_page(self.public_msg, _('Configuration error'))


class SMSError(Exception):
    pass


class InspectException(Exception):
    # exception raised in place of logged errors when using the inspect tools
    def get_error_message(self):
        return str(self)


original_publish_error_init = PublishError.__init__


def publish_error_init(self, public_msg=None, private_msg=None, err_code=None):
    self.err_code = err_code or self.err_code

    if public_msg:
        public_msg = str(public_msg)

    original_publish_error_init(self, public_msg=public_msg, private_msg=private_msg)


PublishError.__init__ = publish_error_init
PublishError.err_code = 'publishing-error'

MethodNotAllowedError.err_code = 'method-not-allowed'
RequestError.err_code = 'invalid-request'
AccessError.err_code = 'access-error'

TraversalError.title = _('Page not found')
TraversalError.description = _(
    'The requested link does not exist on this site.  If '
    'you arrived here by following a link from an external '
    "page, please inform that page's maintainer."
)
TraversalError.err_code = 'not-found'


def format_publish_error(exc):
    if getattr(exc, 'public_msg', None):
        return template.error_page(exc.format(), exc.title)
    return template.error_page(exc.description, exc.title)


class UnknownReferencedErrorMixin:
    def __init__(self, msg, msg_args=None, details=None):
        self.msg = msg
        self.msg_args = msg_args or ()
        self._details = details

    @property
    def details(self):
        if not self._details:
            return None
        details = []
        for kind in sorted(self._details.keys()):
            details.append('%s: %s' % (kind, ', '.join(sorted(self._details[kind]))))
        return '; '.join(details)

    def render(self):
        result = htmltext('<ul>')
        for kind in sorted(self._details.keys()):
            result += htmltext('<li>%s: %s</li>' % (kind, ', '.join(sorted(self._details[kind]))))
        result += htmltext('</ul>')
        return result
