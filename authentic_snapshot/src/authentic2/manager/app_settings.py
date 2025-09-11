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
    __PREFIX = 'A2_MANAGER_'
    __DEFAULTS = {
        'HOMEPAGE_URL': None,
        'SHOW_ALL_OU': True,
        'ROLE_MEMBERS_FROM_OU': False,
        'SHOW_INTERNAL_ROLES': False,
        'USER_SEARCH_MINIMUM_CHARS': 0,
        'LOGIN_URL': None,
        'SITE_TITLE': None,
        'CHECK_DUPLICATE_USERS': False,
    }

    def __getattr__(self, name):
        from django.conf import settings

        if name not in self.__DEFAULTS:
            raise AttributeError
        return getattr(settings, self.__PREFIX + name, self.__DEFAULTS[name])


app_settings = AppSettings()
app_settings.__name__ = __name__
sys.modules[__name__] = app_settings
