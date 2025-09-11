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

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils.translation import gettext_lazy as _


class AppSettings:
    __PREFIX = 'SAML_'
    __NAMES = ('ALLOWED_FEDERATION_MODE', 'DEFAULT_FEDERATION_MODE')

    class FEDERATION_MODE:
        EXPLICIT = 0
        IMPLICIT = 1

        choices = ((EXPLICIT, _('explicit')), (IMPLICIT, _('implicit')))

        @classmethod
        def get_choices(cls, app_settings):
            choices = []
            for choice in cls.choices:
                if choice[0] in app_settings.ALLOWED_FEDERATION_MODE:
                    choices.append(choice)
            return choices

        @classmethod
        def get_default(cls, app_settings):
            return app_settings.DEFAULT_FEDERATION_MODE

    __DEFAULTS = {
        'ALLOWED_FEDERATION_MODE': (FEDERATION_MODE.EXPLICIT, FEDERATION_MODE.IMPLICIT),
        'DEFAULT_FEDERATION_MODE': FEDERATION_MODE.EXPLICIT,
    }

    def __settings(self, name):
        full_name = self.__PREFIX + name
        if name not in self.__NAMES:
            raise AttributeError('unknown settings ' + full_name)
        try:
            if name in self.__DEFAULTS:
                return getattr(settings, full_name, self.__DEFAULTS[name])
            else:
                return getattr(settings, full_name)
        except AttributeError:
            raise ImproperlyConfigured('missing settings ' + full_name)

    def __getattr__(self, name):
        return self.__settings(name)


app_settings = AppSettings()
app_settings.__name__ = __name__
sys.modules[__name__] = app_settings
