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

import sys


class AppSettings:
    '''Thanks django-allauth'''

    __SENTINEL = object()

    def __init__(self, prefix):
        self.prefix = prefix

    def _setting(self, name, dflt=__SENTINEL):
        from django.conf import settings
        from django.core.exceptions import ImproperlyConfigured

        v = getattr(settings, self.prefix + name, dflt)
        if v is self.__SENTINEL:
            raise ImproperlyConfigured('Missing setting %r' % (self.prefix + name))
        return v

    @property
    def ENABLE(self):
        return self._setting('ENABLE', True)

    @property
    def JWKSET(self):
        return self._setting('JWKSET', [])

    @property
    def SCOPES(self):
        return self._setting('SCOPES', [])

    @property
    def DEFAULT_FRONTCHANNEL_TIMEOUT(self):
        return self._setting('DEFAULT_FRONTCHANNEL_TIMEOUT', 300)

    @property
    def IDTOKEN_DURATION(self):
        return self._setting('IDTOKEN_DURATION', 30)

    @property
    def ACCESS_TOKEN_DURATION(self):
        return self._setting('ACCESS_TOKEN_DURATION', 3600 * 8)

    @property
    def REFRESH_TOKEN_DURATION(self):
        return self._setting('REFRESH_TOKEN_DURATION', 3600 * 24 * 30)

    @property
    def PASSWORD_GRANT_RATELIMIT(self):
        return self._setting('PASSWORD_GRANT_RATELIMIT', '100/m')

    @property
    def REDIRECT_URI_MAX_LENGTH(self):
        return self._setting('REDIRECT_URI_MAX_LENGTH', 1024)

    @property
    def DEFAULT_MAPPINGS(self):
        return self._setting(
            'DEFAULT_MAPPINGS',
            [
                {'name': 'given_name', 'value': 'django_user_first_name', 'scopes': 'profile'},
                {'name': 'family_name', 'value': 'django_user_last_name', 'scopes': 'profile'},
                {'name': 'email', 'value': 'django_user_email', 'scopes': 'email'},
                {'name': 'email_verified', 'value': 'django_user_email_verified', 'scopes': 'email'},
            ],
        )

    @property
    def PROFILE_OVERRIDE_MAPPING(self):
        return self._setting('PROFILE_OVERRIDE_MAPPING', {'email': 'email'})


app_settings = AppSettings('A2_IDP_OIDC_')
app_settings.__name__ = __name__
sys.modules[__name__] = app_settings
