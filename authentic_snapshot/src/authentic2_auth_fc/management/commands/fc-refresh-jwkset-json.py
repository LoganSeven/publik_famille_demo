# authentic2 - versatile identity manager
# Copyright (C) 2010-2024  Entr'ouvert
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
from authentic2_auth_fc.models import FcAuthenticator

logger = logging.getLogger('authentic2.auth.fc')


class Command(LogToConsoleCommand):
    loggername = 'authentic2_auth_fc.models'

    def core_command(self, *args, **kwargs):
        try:
            fc_authn = FcAuthenticator.objects.get()
        except FcAuthenticator.DoesNotExist:
            # FranceConnect is not configured on the instance, do not bother logging any warning
            return
        try:
            fc_authn.refresh_jwkset_json()
        except Exception as e:  # TODO narrow down possible exception classes?
            logger.warning(
                'auth_fc: could not refresh jwkset (version %s on %s platform. %s',
                fc_authn.version,
                fc_authn.plaform,
                str(e),
            )
