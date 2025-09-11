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

import django.apps
from django import template


class AppConfig(django.apps.AppConfig):
    name = 'authentic2_auth_fc'

    def a2_hook_api_modify_serializer(self, view, serializer):
        from rest_framework import serializers

        from authentic2.utils.misc import make_url

        from .models import FcAuthenticator

        if not FcAuthenticator.objects.filter(enabled=True).exists():
            return

        request = view.request

        if 'full' not in request.GET:
            return

        if view.__class__.__name__ == 'UsersAPI':

            def get_franceconnect(user):
                linked = hasattr(user, 'fc_account')
                return {
                    'linked': linked,
                    'link_url': make_url('fc-login-or-link', request=request, absolute=True),
                    'unlink_url': make_url('fc-unlink', request=request, absolute=True),
                }

            serializer.get_franceconnect = get_franceconnect
            serializer.fields['franceconnect'] = serializers.SerializerMethodField()

    def a2_hook_manager_user_data(self, view, user):
        context = {'user': user}
        return [template.loader.get_template('authentic2_auth_fc/manager_user_sidebar.html').render(context)]

    def a2_hook_user_can_reset_password(self, user):
        return hasattr(user, 'fc_account') or None

    def a2_hook_user_can_change_password(self, user, request, **kwargs):
        from authentic2.utils.misc import get_authentication_events

        if not request:
            return True
        try:
            session = request.session
        except AttributeError:
            return True
        if session and 'fc_id_token' in session:
            for authentication_event in get_authentication_events(request=request):
                if authentication_event['how'] == 'france-connect':
                    return False
        return True

    def ready(self):
        from authentic2.api_views import UsersAPI

        from .api_views import fc_unlink

        UsersAPI.fc_unlink = fc_unlink

        from django.db.models.signals import pre_save

        from authentic2.custom_user.models import DeletedUser

        pre_save.connect(self.pre_save_deleted_user, sender=DeletedUser)

    def pre_save_deleted_user(self, sender, instance, **kwargs):
        '''Delete and copy FcAccount to old_data'''
        from .models import FcAccount

        fc_accounts = FcAccount.objects.filter(user__uuid=instance.old_uuid).order_by('id')
        for fc_account in fc_accounts:
            instance.old_data = instance.old_data or {}
            instance.old_data.setdefault('fc_accounts', []).append(
                {
                    'sub': fc_account.sub,
                }
            )

    def a2_hook_redirect_logout_list(self, request, **kwargs):
        from django.urls import reverse

        from . import utils
        from .models import FcAuthenticator

        try:
            authenticator = FcAuthenticator.objects.get()
        except FcAuthenticator.DoesNotExist:
            return []

        url = utils.build_logout_url(request, authenticator.logout_url, next_url=reverse('auth_logout'))
        # url is assumed empty if no active session on the OP.
        if url:
            return [url]
        return []

    def a2_hook_password_change_view(self, request=None, **kwargs):
        from django.contrib import messages
        from django.utils.translation import gettext as _

        if request and request.user.is_authenticated and hasattr(request.user, 'fc_account'):
            messages.warning(
                request,
                _(
                    '''\
Watch out, this password is the one from your local account and not the one from your \
FranceConnect provider. It will only be useful when you log in \
locally and not through FranceConnect.'''
                ),
            )
