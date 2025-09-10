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

try:
    from pymemcache import MemcacheServerError
except ImportError:
    MemcacheServerError = NotImplementedError

from django.core.cache.backends.base import InvalidCacheBackendError
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string
from quixote import get_publisher


class TenantBaseCache:
    '''Prepend the tenant application directory to the cache prefix'''

    def set_key_prefix(self, prefix):
        self.__key_prefix = prefix

    def get_key_prefix(self):
        if get_publisher():
            return '%s_%s' % (get_publisher().app_dir, self.__key_prefix)
        return self.__key_prefix

    key_prefix = property(get_key_prefix, set_key_prefix)

    def set(self, *args, **kwargs):
        try:
            return super().set(*args, **kwargs)
        except MemcacheServerError:
            pass


__DERIVED_CLASSES = {}


def WcsTenantCache(host, params, **kwargs):
    try:
        backend = params['REAL_BACKEND']
    except KeyError:
        raise ImproperlyConfigured('The %s.WcsTenantCache backend needs a REAL_BACKEND parameter' % __name__)
    try:
        backend_cls = import_string(backend)
    except ImportError as e:
        raise InvalidCacheBackendError("Could not find backend '%s': %s" % (backend, e))
    derived_cls_name = 'Tenant' + backend_cls.__name__
    if derived_cls_name not in __DERIVED_CLASSES:
        # dynamically create a new class with TenantBaseCache
        # and the original class as parents
        __DERIVED_CLASSES[derived_cls_name] = type(derived_cls_name, (TenantBaseCache, backend_cls), {})
    return __DERIVED_CLASSES[derived_cls_name](host, params, **kwargs)
