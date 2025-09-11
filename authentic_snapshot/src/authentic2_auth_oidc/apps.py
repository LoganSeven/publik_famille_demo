# authentic2 - versatile identity manager
# Copyright (C) 2010-2020 Entr'ouvert
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

import django.apps
import requests
from django import template


class AppConfig(django.apps.AppConfig):
    name = 'authentic2_auth_oidc'

    def ready(self):
        from django.db.models.signals import pre_save

        from authentic2.custom_user.models import DeletedUser

        pre_save.connect(self.pre_save_deleted_user, sender=DeletedUser)

    def pre_save_deleted_user(self, sender, instance, **kwargs):
        '''Delete and copy OIDCAccount to old_data'''
        from .models import OIDCAccount

        oidc_accounts = OIDCAccount.objects.filter(user__uuid=instance.old_uuid).order_by('id')
        for oidc_account in oidc_accounts:
            instance.old_data = instance.old_data or {}
            instance.old_data.setdefault('oidc_accounts', []).append(
                {
                    'issuer': oidc_account.provider.issuer,
                    'sub': oidc_account.sub,
                }
            )

    def a2_hook_manager_user_data(self, view, user):
        context = {'user': user}
        return [
            template.loader.get_template('authentic2_auth_oidc/manager_user_sidebar.html').render(context)
        ]

    def a2_hook_redirect_logout_list(self, request, **kwargs):
        from django.urls import reverse

        from authentic2.utils.misc import make_url

        from .models import OIDCProvider

        tokens = request.session.get('auth_oidc', {}).get('tokens', [])
        urls = []
        if tokens:
            for token in tokens:
                provider = OIDCProvider.objects.get(pk=token['provider_pk'])
                # ignore providers wihtout SLO
                if not provider.end_session_endpoint:
                    continue
                params = {}
                if 'id_token' in token['token_response']:
                    params['id_token_hint'] = token['token_response']['id_token']
                if 'access_token' in token['token_response'] and provider.token_revocation_endpoint:
                    self._revoke_token(provider, token['token_response']['access_token'])
                params['post_logout_redirect_uri'] = request.build_absolute_uri(reverse('auth_logout'))
                urls.append(make_url(provider.end_session_endpoint, params=params))
        return urls

    @classmethod
    def _revoke_token(cls, provider, access_token):
        logger = logging.getLogger(__name__)

        url = provider.token_revocation_endpoint
        try:
            response = requests.post(
                url,
                auth=(provider.client_id, provider.client_secret),
                data={'token': access_token, 'token_type': 'access_token'},
                timeout=10,
            )
        except requests.RequestException as e:
            logger.warning('failed to revoke access token from OIDC provider %s: %s', provider.issuer, e)
            return
        try:
            response.raise_for_status()
        except requests.RequestException as e:
            try:
                content = response.json()
            except ValueError:
                content = None
            logger.warning(
                'failed to revoke access token from OIDC provider %s: %s, %s', provider.issuer, e, content
            )
            return
        logger.info('revoked token from OIDC provider %s', provider.issuer)
