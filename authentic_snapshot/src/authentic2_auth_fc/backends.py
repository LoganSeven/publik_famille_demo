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

import logging

from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
from django.core.exceptions import PermissionDenied

from . import models

logger = logging.getLogger(__name__)
User = get_user_model()


class FcBackend(ModelBackend):
    def authenticate(self, request, sub, token, user_info):
        user = None
        try:
            account = models.FcAccount.objects.select_related().get(sub=sub)
        except models.FcAccount.DoesNotExist:
            return None
        except models.FcAccount.MultipleObjectsReturned:
            # most likeley multiaccount was activated then withdrawn; instead of prompting
            # the user for one of their accounts (multiaccount behavior), pick the latest one
            account = models.FcAccount.objects.select_related().filter(sub=sub).order_by('id').last()

        if not account.user.is_active:
            logger.info('auth_fc: login refused for user %s, it is inactive', user)
            raise PermissionDenied

        return account.user

    def get_saml2_authn_context(self):
        import lasso

        return lasso.SAML2_AUTHN_CONTEXT_PASSWORD_PROTECTED_TRANSPORT
