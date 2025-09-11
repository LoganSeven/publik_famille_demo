# authentic2 - versatile identity manager
# Copyright (C) 2010-2022  Entr'ouvert
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


class Command(LogToConsoleCommand):
    loggername = 'authentic2_auth_oidc.models'

    def add_arguments(self, parser):
        parser.add_argument('--provider', type=str, default=None)

    def core_command(self, *args, **kwargs):
        provider = kwargs['provider']

        logger = logging.getLogger(self.loggername)
        providers = OIDCProvider.objects.filter(a2_synchronization_supported=True)
        if provider:
            providers = providers.filter(slug=provider)
        if not providers.count():
            logger.error('no provider supporting synchronization found, exiting')
            return
        logger.info(
            'got %s provider(s): %s',
            providers.count(),
            ' '.join(providers.values_list('slug', flat=True)),
        )
        for provider in providers:
            provider.perform_synchronization()
