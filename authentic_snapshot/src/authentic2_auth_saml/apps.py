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

import django.apps
from django import template
from mellon.utils import get_idp


class AppConfig(django.apps.AppConfig):
    name = 'authentic2_auth_saml'

    def ready(self):
        from django.db.models.signals import pre_save

        from authentic2.custom_user.models import DeletedUser

        pre_save.connect(self.pre_save_deleted_user, sender=DeletedUser)

    def pre_save_deleted_user(self, sender, instance, **kwargs):
        '''Delete and copy UserSamlIdentifier to old_data'''
        from mellon.models import UserSAMLIdentifier

        saml_accounts = UserSAMLIdentifier.objects.filter(user__uuid=instance.old_uuid).order_by('id')
        for saml_account in saml_accounts:
            instance.old_data = instance.old_data or {}
            instance.old_data.setdefault('saml_accounts', []).append(
                {
                    'issuer': saml_account.issuer.entity_id,
                    'name_id': saml_account.name_id,
                }
            )

    def a2_hook_manager_user_data(self, view, user):
        user_saml_identifiers = user.saml_identifiers.all()
        if not user_saml_identifiers:
            return ['']
        for user_saml_identifier in user_saml_identifiers:
            user_saml_identifier.idp = get_idp(user_saml_identifier.issuer.entity_id)
        context = {'user_saml_identifiers': user_saml_identifiers}
        return [
            template.loader.get_template('authentic2_auth_saml/manager_user_sidebar.html').render(context)
        ]

    def a2_hook_redirect_logout_list(self, request, **kwargs):
        from mellon.views import LogoutView

        mellon_logout_url = LogoutView.make_logout_token_url(request, next_url='/logout/')
        if mellon_logout_url:
            return [mellon_logout_url]
        else:
            return []
