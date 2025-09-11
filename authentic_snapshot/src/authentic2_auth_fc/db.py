# authentic2-auth-fc - authentic2 authentication for FranceConnect
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

from django.conf import settings


def create_fc_authenticator(apps, schema_editor):
    FcAuthenticator = apps.get_model('authentic2_auth_fc', 'FcAuthenticator')
    if FcAuthenticator.objects.exists():
        return

    if not hasattr(settings, 'A2_FC_ENABLE'):
        return

    authorize_url = getattr(settings, 'A2_FC_AUTHORIZE_URL', '')
    if authorize_url == 'https://app.franceconnect.gouv.fr/api/v1/authorize':
        platform = 'prod'
    else:
        platform = 'test'

    kwargs_settings = getattr(settings, 'AUTH_FRONTENDS_KWARGS', {})
    authenticator_settings = kwargs_settings.get('fc', {})

    priority = authenticator_settings.get('priority')
    priority = priority if priority is not None else -1

    client_id = getattr(settings, 'A2_FC_CLIENT_ID', '') or ''
    client_secret = getattr(settings, 'A2_FC_CLIENT_SECRET', '') or ''

    FcAuthenticator.objects.create(
        slug='fc-authenticator',
        order=priority,
        show_condition=authenticator_settings.get('show_condition') or '',
        enabled=bool(getattr(settings, 'A2_FC_ENABLE', False) and client_id and client_secret),
        platform=platform,
        client_id=client_id[:256],
        client_secret=client_secret[:256],
        scopes=getattr(settings, 'A2_FC_SCOPES', []) or ['profile', 'email'],
    )
