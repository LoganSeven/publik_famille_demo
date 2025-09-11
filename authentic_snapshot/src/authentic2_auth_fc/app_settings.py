# authentic2-auth-fc - authentic2 authentication for FranceConnect
# Copyright (C) 2019 Entr'ouvert
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

from django.utils.translation import pgettext_lazy


class AppSettings:
    __SENTINEL = object()

    def __init__(self, prefix):
        self.prefix = prefix

    def _setting(self, name, dflt=__SENTINEL):
        from django.conf import settings

        v = getattr(settings, self.prefix + name, dflt)
        if v is self.__SENTINEL:
            raise AttributeError(name)
        return v

    @property
    def about_url(self):
        return self._setting('ABOUT_URL', 'https://franceconnect.gouv.fr/')

    @property
    def logout_when_unlink(self):
        return self._setting('LOGOUT_WHEN_UNLINK', True)

    @property
    def user_info_mappings(self):
        return self._setting(
            'USER_INFO_MAPPINGS',
            {
                'last_name': {
                    'ref': 'family_name',
                    'verified': True,
                },
                'first_name': {
                    'ref': 'given_name',
                    'verified': True,
                },
                'email': {
                    'ref': 'email',
                    'if-empty': True,
                    'tag': 'email',
                },
                'email_verified': {
                    'ref': 'email',
                    'translation': 'notempty',
                    'if-tag': 'email',
                },
                'title': {
                    'ref': 'gender',
                    'if-empty': True,
                    'translation': 'simple',
                    'translation_simple': {
                        'female': pgettext_lazy('title', 'Mrs'),
                        'male': pgettext_lazy('title', 'Mr'),
                    },
                },
            },
        )

    @property
    def display_common_scopes_only(self):
        return self._setting('DISPLAY_COMMON_SCOPES_ONLY', True)

    @property
    def display_email_linking_option(self):
        return self._setting('DISPLAY_EMAIL_LINKING_OPTION', False)

    @property
    def verify_certificate(self):
        return self._setting('VERIFY_CERTIFICATE', True)

    @property
    def client_credentials(self):
        return self._setting('CLIENT_CREDENTIALS', ())


app_settings = AppSettings('A2_FC_')
app_settings.__name__ = __name__
sys.modules[__name__] = app_settings
