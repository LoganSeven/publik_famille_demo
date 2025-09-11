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
    __DEFAULTS = {
        'ENABLE': False,
        # allow do tisable check of pgt url for testing purpose
        'CHECK_PGT_URL': True,
    }

    def __init__(self, prefix):
        self.prefix = prefix

    def _setting(self, name, dflt):
        from django.conf import settings

        return getattr(settings, self.prefix + name, dflt)

    def __getattr__(self, name):
        if name not in self.__DEFAULTS:
            raise AttributeError(name)
        return self._setting(name, self.__DEFAULTS[name])


# Ugly? Guido recommends this himself ...
# http://mail.python.org/pipermail/python-ideas/2012-May/014969.html
app_settings = AppSettings('A2_IDP_CAS_')
app_settings.__name__ = __name__
sys.modules[__name__] = app_settings
