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

# Decorating URL includes, <https://djangosnippets.org/snippets/2532/>

from django.contrib.auth.decorators import user_passes_test
from django.core.exceptions import PermissionDenied
from django.urls.resolvers import URLPattern, URLResolver


class DecoratedURLPattern(URLPattern):
    def resolve(self, *args, **kwargs):
        result = super().resolve(*args, **kwargs)
        if result:
            result.func = self._decorate_with(result.func)
        return result


class DecoratedURLResolver(URLResolver):
    def resolve(self, *args, **kwargs):
        result = super().resolve(*args, **kwargs)
        if result:
            result.func = self._decorate_with(result.func)
        return result


def decorated_includes(func, includes, *args, **kwargs):
    urlconf_module, app_name, namespace = includes

    for item in urlconf_module:
        if isinstance(item, URLResolver):
            item.__class__ = DecoratedURLResolver
        else:
            item.__class__ = DecoratedURLPattern
        item._decorate_with = func

    return urlconf_module, app_name, namespace


def manager_required(function=None, login_url=None):
    def check_manager(user):
        if user and user.is_staff:
            return True
        if user and not user.is_anonymous:
            raise PermissionDenied()
        # As the last resort, show the login form
        return False

    actual_decorator = user_passes_test(check_manager, login_url=login_url)
    if function:
        return actual_decorator(function)
    return actual_decorator
