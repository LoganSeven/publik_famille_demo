# authentic2 - versatile identity manager
# Copyright (C) 2010-2020 Entr'ouvert
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

import django.apps
from django.template.loader import render_to_string

from .constants import SESSION_CAS_LOGOUTS


class Plugin:
    def logout_list(self, request):
        fragments = []
        cas_logouts = request.session.get(SESSION_CAS_LOGOUTS, [])
        for name, url, use_iframe, use_iframe_timeout in cas_logouts:
            ctx = {
                'needs_iframe': use_iframe,
                'name': name,
                'url': url,
                'iframe_timeout': use_iframe_timeout,
            }
            content = render_to_string('authentic2_idp_cas/logout_fragment.html', ctx)
            fragments.append(content)
        return fragments


class AppConfig(django.apps.AppConfig):
    name = 'authentic2_idp_cas'

    def get_a2_plugin(self):
        return Plugin()
