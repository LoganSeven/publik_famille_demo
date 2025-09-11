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


import functools

from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend as BaseModelBackend
from django.contrib.contenttypes.models import ContentType
from django.db import models
from phonenumbers import PhoneNumberFormat, format_number, is_valid_number

from authentic2.backends import get_user_queryset
from authentic2.models import AttributeValue
from authentic2.user_login_failure import user_login_failure, user_login_success
from authentic2.utils.misc import get_password_authenticator, parse_phone_number

from .. import app_settings


def upn(username, realm):
    '''Build an UPN from a username and a realm'''
    return f'{username}@{realm}'


PROXY_USER_MODEL = None


class ModelBackend(BaseModelBackend):
    """
    Authenticates against settings.AUTH_USER_MODEL.
    """

    def get_query(self, username, realm=None, ou=None):
        username_field = 'username'
        queries = []
        password_authenticator = get_password_authenticator()
        user_ct = ContentType.objects.get_for_model(get_user_model())
        if password_authenticator.accept_email_authentication:
            queries.append(models.Q(**{'email__iexact': username}))
        if password_authenticator.is_phone_authn_active:
            # try with the phone number as user identifier
            if (pn := parse_phone_number(username)) and is_valid_number(pn):
                user_ids = AttributeValue.objects.filter(
                    multiple=False,
                    content_type=user_ct,
                    attribute=password_authenticator.phone_identifier_field,
                    content=format_number(pn, PhoneNumberFormat.E164),
                ).values_list('object_id', flat=True)
                query = {'id__in': user_ids}
                queries.append(models.Q(**query))

        if realm is None:
            queries.append(models.Q(**{username_field: username}))
            if '@' not in username:
                if app_settings.REALMS:
                    for realm, dummy in app_settings.REALMS:
                        queries.append(models.Q(**{username_field: upn(username, realm)}))
        else:
            queries.append(models.Q(**{username_field: upn(username, realm)}))
        queries = functools.reduce(models.Q.__or__, queries)
        if ou:
            queries &= models.Q(ou=ou)
        return queries

    def must_reset_password(self, user):
        from authentic2 import models as a2_models

        return bool(a2_models.PasswordReset.filter(user=user).count())

    def authenticate(self, request, username=None, password=None, realm=None, ou=None):
        UserModel = get_user_model()
        if not username:
            return
        query = self.get_query(username=username, realm=realm, ou=ou)
        users = get_user_queryset().filter(query)
        # order by username to make username without realm come before usernames with realms
        # i.e. "toto" should come before "toto@example.com"
        users = users.order_by('-is_active', UserModel.USERNAME_FIELD, 'id')
        for user in users:
            if user.check_password(password):
                user_login_success(user.get_username())
                return user
            else:
                user_login_failure(user.get_username())
                if hasattr(request, 'failed_logins') and user not in request.failed_logins:
                    request.failed_logins.update({user: {}})

    def get_user(self, user_id):
        UserModel = get_user_model()
        try:
            user = UserModel._default_manager.get(pk=user_id)
        except UserModel.DoesNotExist:
            return None
        return user

    def get_saml2_authn_context(self):
        import lasso

        return lasso.SAML2_AUTHN_CONTEXT_PASSWORD


class DummyModelBackend(ModelBackend):
    def authenticate(self, request, user=None):
        if user is not None:
            return user
