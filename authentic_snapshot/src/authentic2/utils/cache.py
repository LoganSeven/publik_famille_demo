# authentic2 - versatile identity manager
# Copyright (C) 2022 Entr'ouvert
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

import base64
import pickle
import time
from functools import partial, wraps

from django.core.cache import cache

from authentic2 import app_settings, middleware


class CacheUnusable(RuntimeError):
    pass


class CacheDecoratorBase:
    """Base class to build cache decorators.

    It helps for building keys from function arguments.
    """

    def __new__(cls, *args, **kwargs):
        if len(args) > 1:
            raise TypeError(
                '%s got unexpected arguments, only one argument must be given, the function to decorate'
                % cls.__name__
            )
        if args:
            # Case of a decorator used directly
            return cls(**kwargs)(args[0])
        return super().__new__(cls)

    def __init__(self, timeout=None, hostname_vary=True, args=None, kwargs=None):
        self.timeout = timeout
        self.hostname_vary = hostname_vary
        self.args = args
        self.kwargs = kwargs

    def set(self, key, value):
        raise NotImplementedError

    def get(self, key):
        raise NotImplementedError

    def clear(self):
        raise NotImplementedError

    def __call__(self, func):
        @wraps(func)
        def f(*args, **kwargs):
            try:
                if not app_settings.A2_CACHE_ENABLED:
                    raise CacheUnusable
                now = time.time()
                key = self.key(*args, **kwargs)
                value, tstamp = self.get(key)
                if tstamp is not None:
                    if self.timeout is None or tstamp + self.timeout > now:
                        return value
                    if hasattr(self, 'delete'):
                        self.delete(key, (key, tstamp))
                value = func(*args, **kwargs)
                self.set(key, (value, now))
                return value
            except CacheUnusable:  # fallback when cache cannot be used
                return func(*args, **kwargs)

        f.cache = self
        return f

    def key(self, *args, **kwargs):
        '''Transform arguments to string and build a key from it'''
        parts = [str(id(self))]  # add cache instance to the key
        if self.hostname_vary:
            request = middleware.StoreRequestMiddleware.get_request()
            if request:
                parts.append(request.get_host())
            else:
                # if we cannot determine the hostname it's better to ignore the
                # cache
                raise CacheUnusable
        for i, arg in enumerate(args):
            if self.args and i not in self.args:
                continue
            parts.append(str(arg))

        for kw, arg in sorted(kwargs.items(), key=lambda x: x[0]):
            if kw not in self.kwargs:
                continue
            parts.append('%s-%s' % (str(kw), str(arg)))
        return '|'.join(parts)


class SimpleDictionnaryCacheMixin:
    """Default implementations of set, get and delete for a cache implemented
    using a dictionary. The dictionnary must be returned by a property named
    'cache'.
    """

    def set(self, key, value):
        self.cache[key] = value

    def get(self, key):
        return self.cache.get(key, (None, None))

    def delete(self, key, value):
        if key in self.cache and self.cache[key] == value:
            del self.cache[key]

    def clear(self):
        self.cache.clear()


class GlobalCache(SimpleDictionnaryCacheMixin, CacheDecoratorBase):
    def __init__(self, *args, **kwargs):
        self.cache = {}
        super().__init__(*args, **kwargs)


class RequestCache(SimpleDictionnaryCacheMixin, CacheDecoratorBase):
    @property
    def cache(self):
        request = middleware.StoreRequestMiddleware.get_request()
        if not request:
            return {}
        # create a cache dictionary on the request
        return request.__dict__.setdefault(self.__class__.__name__, {})


class PickleCacheMixin:
    def set(self, key, value):
        value, tstamp = value
        value = base64.b64encode(pickle.dumps(value)).decode('ascii')
        super().set(key, (value, tstamp))

    def get(self, key):
        value = super().get(key)
        if value[0] is not None:
            value, tstamp = value
            try:
                value = base64.b64decode(value.encode('ascii'))
            except ValueError:
                pass
            value = (pickle.loads(value), tstamp)
        return value


class SessionCache(PickleCacheMixin, SimpleDictionnaryCacheMixin, CacheDecoratorBase):
    @property
    def cache(self):
        request = middleware.StoreRequestMiddleware.get_request()
        if not request:
            return {}
        # create a cache dictionary on the request
        return request.session.setdefault(self.__class__.__name__, {})

    def set(self, key, value):
        request = middleware.StoreRequestMiddleware.get_request()
        if request:
            request.session.modified = True
        return super().set(key, value)

    def clear(self):
        request = middleware.StoreRequestMiddleware.get_request()
        if request:
            request.session.modified = True
        return super().clear()


def cache_decorator(func=None, *, timeout=30):
    if func is None:
        return partial(cache_decorator, timeout=30)

    key = f'{func.__name__}.{func.__module__}'

    @wraps(func)
    def f(*args, **kwargs):
        value = cache.get(key)
        if value is None or not app_settings.A2_CACHE_ENABLED:
            value = func(*args, **kwargs)
            cache.set(key, value)
        return value

    def clear():
        cache.delete(key)

    f.clear_cache = clear

    return f
