# lingo - payment and billing system
# Copyright (C) 2022  Entr'ouvert
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

from django.conf import settings
from django.contrib.auth import logout as auth_logout
from django.contrib.auth import views as auth_views
from django.http import HttpResponseRedirect
from django.shortcuts import resolve_url
from django.utils.http import quote

if 'mellon' in settings.INSTALLED_APPS:
    from mellon.utils import get_idps  # pylint: disable=import-error
else:

    def get_idps():
        return []


class LoginView(auth_views.LoginView):
    def dispatch(self, request, *args, **kwargs):
        if any(get_idps()):
            if 'next' not in request.GET:
                return HttpResponseRedirect(resolve_url('mellon_login'))
            return HttpResponseRedirect(
                resolve_url('mellon_login') + '?next=' + quote(request.GET.get('next'))
            )
        return super().dispatch(request, *args, **kwargs)


login = LoginView.as_view()


def logout(request, next_page=None):
    if any(get_idps()):
        return HttpResponseRedirect(resolve_url('mellon_logout'))
    auth_logout(request)
    if next_page is not None:
        next_page = resolve_url(next_page)
    else:
        next_page = '/'
    return HttpResponseRedirect(next_page)


def homepage(request):
    template_vars = getattr(settings, 'TEMPLATE_VARS', None) or {}
    return HttpResponseRedirect(template_vars.get('portal_user_url') or '/manage/')
