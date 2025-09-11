# authentic2 - versatile identity manager
# Copyright (C) 2010-2023  Entr'ouvert
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

import logging

from authentic2.base_commands import LogToConsoleCommand
from authentic2_auth_oidc.models import OIDCProvider

logger = logging.getLogger('authentic2.auth.oidc')


class Command(LogToConsoleCommand):
    loggername = 'authentic2_auth_oidc.models'

    def core_command(self, *args, **kwargs):
        for oidc_provider in OIDCProvider.objects.all():
            try:
                oidc_provider.refresh_jwkset_json()
            except Exception as e:
                logger.warning('auth_oidc: could not refresh jwkset for provider %s (%s)', oidc_provider, e)
