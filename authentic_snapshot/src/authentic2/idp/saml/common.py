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

import logging
from importlib import import_module

from django.conf import settings
from django.contrib.auth import REDIRECT_FIELD_NAME
from django.http import HttpResponseRedirect
from django.utils.http import urlencode


def redirect_to_login(next_url, login_url=None, redirect_field_name=REDIRECT_FIELD_NAME, other_keys=None):
    "Redirects the user to the login page, passing the given 'next' page"
    if not login_url:
        login_url = settings.LOGIN_URL
    data = {redirect_field_name: next_url}
    if other_keys:
        for k, v in other_keys.items():
            data[k] = v
    return HttpResponseRedirect('%s?%s' % (login_url, urlencode(data)))


def kill_django_sessions(session_key):
    engine = import_module(settings.SESSION_ENGINE)
    try:
        for key in session_key:
            store = engine.SessionStore(key)
            logging.debug('Killing session %s', key)
            store.delete()
    except Exception as e:
        logging.error(e)
