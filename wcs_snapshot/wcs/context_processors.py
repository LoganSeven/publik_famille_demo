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

import hashlib

from quixote import get_publisher, get_request, get_response, get_session

from wcs.deprecations import has_urgent_deprecations


def get_global_context():
    pub = get_publisher()
    if pub:
        return pub.substitutions.get_context_variables(mode='lazy')


def publisher(request):
    template_base = 'wcs/base.html'
    if request.path.startswith('/backoffice/'):
        if getattr(request, 'is_django_native', False):
            template_base = 'wcs/backoffice.html'
        else:
            template_base = 'wcs/blank.html'

    from wcs.qommon.admin.menu import get_vc_version

    return {
        'publisher': get_publisher,
        'response': get_response,
        'user': lambda: get_request() and get_request().user,
        'template_base': template_base,
        'global_context': get_global_context,
        'session_message': lambda: get_session() and get_session().display_message(),
        'version_hash': hashlib.md5(str(get_vc_version()).encode()).hexdigest(),
        'has_urgent_deprecations': has_urgent_deprecations,
    }
