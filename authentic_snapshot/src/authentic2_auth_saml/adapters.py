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


import logging

from django.contrib import messages
from django.db.transaction import atomic
from django.utils.translation import gettext as _
from mellon.adapters import DefaultAdapter, UserCreationError

from authentic2.a2_rbac.utils import get_default_ou
from authentic2.backends import get_user_queryset
from authentic2.models import Lock
from authentic2.utils import misc as utils_misc
from authentic2.utils.template import evaluate_condition_template

from .models import SAMLAuthenticator

logger = logging.getLogger('authentic2.auth_saml')


class MappingError(Exception):
    details = None

    def __init__(self, msg, *args, **kwargs):
        assert msg % kwargs
        self.msg = msg
        self.kwargs = kwargs
        super().__init__(*args)

    def message(self):
        return self.msg % self.kwargs

    def __str__(self):
        return self.msg % self.kwargs


class SamlConditionContextProxy:
    def __init__(self, saml_attributes):
        self.saml_attributes = saml_attributes

    def __getitem__(self, key):
        if key.endswith('__list'):
            return self.saml_attributes[key[: -len('__list')]]
        else:
            v = self.saml_attributes[key]
            if isinstance(v, list):
                return v[0] if v else None
            else:
                return v


class AuthenticAdapter(DefaultAdapter):
    def get_identity_providers_setting(self):
        for authenticator in SAMLAuthenticator.objects.filter(enabled=True):
            if authenticator.metadata or authenticator.metadata_url:
                yield authenticator.settings

    def create_user(self, user_class):
        user = user_class()
        user.set_unusable_password()
        user.save()
        return user

    @atomic(savepoint=False)
    def lookup_user(self, idp, saml_attributes, *args, **kwargs):
        return super().lookup_user(idp, saml_attributes, *args, **kwargs)

    def _lookup_by_attributes(self, idp, saml_attributes, lookup_by_attributes):
        for rule in lookup_by_attributes:
            user_field = rule.get('user_field')
            saml_attribute = rule.get('saml_attribute')
            emails = saml_attributes.get(saml_attribute)
            if user_field and user_field == 'email' and emails:
                for email in emails:
                    Lock.lock_email(email)
        return super()._lookup_by_attributes(idp, saml_attributes, lookup_by_attributes)

    def finish_create_user(self, idp, saml_attributes, user):
        try:
            self.provision_a2_attributes(user, idp, saml_attributes)
        except MappingError as e:
            logger.warning('auth_saml: user creation failed on a mandatory mapping action, %s', e)
            if self.request:
                messages.error(self.request, _('User creation failed: %s.') % e.message())
            raise UserCreationError
        if not user.ou:
            if idp['authenticator'].ou:
                user.ou = idp['authenticator'].ou
            else:
                user.ou = get_default_ou()
            user.save()

    def provision(self, user, idp, saml_attributes):
        super().provision(user, idp, saml_attributes)
        try:
            self.provision_a2_attributes(user, idp, saml_attributes)
        except MappingError as e:
            logger.warning('auth_saml: failure during attribute provisionning %s', e)

    @atomic
    def provision_a2_attributes(self, user, idp, saml_attributes):
        saml_attributes = saml_attributes.copy()

        self.set_attributes(user, idp, saml_attributes)
        self.action_add_role(user, idp, saml_attributes)

    def set_attributes(self, user, idp, saml_attributes):
        user_modified = False
        for action in idp['authenticator'].set_attribute_actions.all():
            try:
                user_modified |= self.set_user_attribute(user, action, saml_attributes)
            except MappingError as e:
                logger.warning('auth_saml: mapping action failed: %s', e)
                if action.mandatory:
                    # it's mandatory, provisionning should fail completely
                    raise e

        if user_modified:
            user.save()

    def set_user_attribute(self, user, action, saml_attributes):
        if action.saml_attribute not in saml_attributes:
            raise MappingError(_('unknown saml_attribute (%s)') % action.saml_attribute)

        attribute = action.user_field
        value = saml_attributes[action.saml_attribute]
        if isinstance(value, list):
            if len(value) == 0:
                raise MappingError(_('no value for attribute "%(attribute)s"'), attribute=attribute)
            if len(value) > 1:
                raise MappingError(
                    _('too many values for attribute "%(attribute)s": %(value)s'),
                    attribute=attribute,
                    value=value,
                )
            value = value[0]
        if attribute in ('first_name', 'last_name', 'email', 'username'):
            if getattr(user, attribute) != value:
                logger.info('auth_saml: attribute %r set to %r', attribute, value, extra={'user': user})
                setattr(user, attribute, value)
                return True
        else:
            if getattr(user.attributes, attribute) != value:
                logger.info('auth_saml: attribute %r set to %r', attribute, value, extra={'user': user})
                setattr(user.attributes, attribute, value)
                return True
        return False

    def action_add_role(self, user, idp, saml_attributes):
        for action in idp['authenticator'].add_role_actions.all():
            if action.condition:
                if evaluate_condition_template(action.condition, {'attributes': saml_attributes}):
                    if action.role not in user.roles.all():
                        logger.info(
                            'auth_saml: adding role "%s" based on condition "%s"',
                            action.role,
                            action.condition,
                            extra={'user': user},
                        )
                        user.roles.add(action.role)
                else:
                    if action.role in user.roles.all():
                        logger.info(
                            'auth_saml: removing role "%s" based on condition "%s"',
                            action.role,
                            action.condition,
                            extra={'user': user},
                        )
                        user.roles.remove(action.role)
            else:
                if action.role not in user.roles.all():
                    logger.info('auth_saml: adding role "%s"', action.role, extra={'user': user})
                    user.roles.add(action.role)

    def auth_login(self, request, user):
        utils_misc.login(request, user, 'saml')

    def get_users_queryset(self, idp, saml_attributes):
        return get_user_queryset()
