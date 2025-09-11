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

import datetime
import logging

import jwcrypto
import requests
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
from django.db import IntegrityError
from django.db.transaction import atomic
from django.utils.timezone import now
from django.utils.translation import gettext as _
from jwcrypto.jwk import JWK
from jwcrypto.jwt import JWT

from authentic2 import app_settings
from authentic2.a2_rbac.models import OrganizationalUnit
from authentic2.a2_rbac.utils import get_default_ou
from authentic2.models import Lock
from authentic2.utils import hooks
from authentic2.utils.crypto import base64url_encode
from authentic2.utils.jwc import IDToken, IDTokenError
from authentic2.utils.template import evaluate_condition_template

from . import models, utils

logger = logging.getLogger(__name__)


class OIDCBackend(ModelBackend):
    # pylint: disable=arguments-renamed
    def authenticate(self, request, access_token=None, id_token=None, nonce=None, provider=None):
        try:
            with atomic():
                return self._authenticate(
                    request, access_token=access_token, id_token=id_token, nonce=nonce, provider=provider
                )
        except utils.AlreadyLinked as e:
            logger.warning('auth_oidc: email %s is already linked to another provider.', e.email)
            if request:
                messages.warning(
                    request,
                    _(
                        'Your email is already linked to another SSO account, please contact an administrator.'
                    ),
                )
        except utils.AuthenticationException as e:
            logger.warning('auth_oidc: %s', str(e))
            if request:
                messages.warning(request, _('%s Please contact your administrator.') % str(e))
        # Ensure journal's records inserted during _authenticate() are saved
        # even if the DB transaction has been rollback by the atomic() block
        request.journal.record_pending()
        return None

    def _authenticate(self, request, access_token=None, id_token=None, nonce=None, provider=None):
        if None in (id_token, provider):
            return
        original_id_token = id_token
        try:
            id_token_content = None
            id_token = IDToken(id_token)
            id_token_content = id_token.as_dict(provider)
            logger.debug('auth_oidc: id_token content %s', id_token_content)
            id_token.deserialize(provider)
        except IDTokenError as e:
            if request:
                messages.warning(
                    request, _('OpenIDConnect provider %(name)s is currently down.') % {'name': provider.name}
                )
                if settings.DEBUG:
                    messages.warning(
                        request,
                        _('Unable to validate the idtoken: {error}').format(
                            id_token=original_id_token, error=e
                        ),
                    )
            if not logger.isEnabledFor(logging.DEBUG):
                logger.info('auth_oidc: id_token content, %s', id_token_content or original_id_token)
            logger.warning('auth_oidc: invalid id_token, %s', e)
            return None

        try:
            provider = utils.get_provider_by_issuer(id_token.iss)
        except models.OIDCProvider.DoesNotExist:
            logger.warning('auth_oidc: unknown issuer "%s"', id_token.iss)
            return None

        key_or_keyset = None
        if provider.idtoken_algo == models.OIDCProvider.ALGO_RSA:
            key_or_keyset = provider.jwkset
            if not key_or_keyset:
                logger.warning(
                    'auth_oidc: idtoken signature algorithm is RSA but no JWKSet is defined on provider %s',
                    id_token.iss,
                )
                return None
            algs = ['RS256', 'RS384', 'RS512']
        elif provider.idtoken_algo == models.OIDCProvider.ALGO_HMAC:
            k = base64url_encode(provider.client_secret.encode('utf-8'))
            key_or_keyset = JWK(kty='oct', k=k.decode('ascii'))
            if not provider.client_secret:
                logger.warning(
                    'auth_oidc: idtoken signature algorithm is HMAC but no client_secret is defined on'
                    ' provider %s',
                    id_token.iss,
                )
                return None
            algs = ['HS256', 'HS384', 'HS512']
        elif provider.idtoken_algo == models.OIDCProvider.ALGO_EC:
            key_or_keyset = provider.jwkset
            if not key_or_keyset:
                logger.warning(
                    'auth_oidc: idtoken signature algorithm is EC but no JWKSet is defined on provider %s',
                    id_token.iss,
                )
                return None
            algs = ['ES256', 'ES384', 'ES512']

        if key_or_keyset:
            try:
                jwt = JWT(jwt=original_id_token, key=key_or_keyset, check_claims={}, algs=algs)
                jwt.claims  # pylint: disable=pointless-statement
            except jwcrypto.common.JWException as expt:
                logger.warning('auth_oidc: idtoken signature validation failed (%s)', expt)
                return None

        if isinstance(id_token.aud, str) and provider.client_id != id_token.aud:
            logger.warning(
                'auth_oidc: invalid id_token audience %s != provider client_id %s',
                id_token.aud,
                provider.client_id,
            )
            return None
        if isinstance(id_token.aud, list):
            if provider.client_id not in id_token.aud:
                logger.warning(
                    'auth_oidc: invalid id_token audience %s != provider client_id %s',
                    id_token.aud,
                    provider.client_id,
                )
                return None
            if len(id_token.aud) > 1 and 'azp' not in id_token:
                logger.warning('auth_oidc: multiple audience and azp not set')
                return None
            if id_token.azp != provider.client_id:
                logger.warning(
                    'auth_oidc: multiple audience and azp %r does not match client_id %r',
                    id_token.azp,
                    provider.client_id,
                )
                return None

        if provider.max_auth_age:
            if not id_token.iat:
                logger.warning(
                    'auth_oidc: provider configured for fresh authentication but iat is missing from idtoken'
                )
                return None
            duration = now() - id_token.iat
            if duration > datetime.timedelta(seconds=provider.max_auth_age):
                logger.warning('auth_oidc: authentication is too old %s (%s old)', id_token.iat, duration)
                return None

        id_token_nonce = getattr(id_token, 'nonce', None)
        if nonce and nonce != id_token_nonce:
            logger.warning('auth_oidc: id_token nonce %r != expected nonce %r', id_token_nonce, nonce)
            return None

        # map claims to attributes or user fields
        # mapping is done before eventual creation of user as EMAIL_IS_UNIQUE needs to know if the
        # mapping will provide some mail to us
        ou_map = {ou.slug: ou for ou in OrganizationalUnit.cached()}
        user_ou = provider.ou or get_default_ou()
        user_info = {}
        save_user = False
        context = id_token_content.copy()
        need_user_info = False
        for claim_mapping in provider.claim_mappings.all():
            need_user_info = need_user_info or not claim_mapping.idtoken_claim

        if need_user_info:
            if not access_token:
                logger.warning('auth_oidc: need user info for some claims, but no access token was returned')
                return None
            try:
                response = requests.get(
                    provider.userinfo_endpoint,
                    headers={
                        'Authorization': 'Bearer %s' % access_token,
                    },
                    timeout=settings.REQUESTS_TIMEOUT,
                )
                response.raise_for_status()
            except requests.RequestException as e:
                logger.warning('auth_oidc: failed to retrieve user info %s', e)
            else:
                try:
                    user_info = response.json()
                except ValueError as e:
                    logger.warning('auth_oidc: bad JSON in user info response, %s (%r)', e, response.content)
                else:
                    logger.debug('auth_oidc: user_info content %s', user_info)
                    context.update(user_info or {})

        has_ou_mapping = False
        mappings = utils.resolve_claim_mappings(provider, context, id_token, user_info, request)
        for attribute, value, dummy in mappings:
            if attribute == 'ou__slug' and value in ou_map:
                user_ou = ou_map[value]
                has_ou_mapping = True
                break

        # find en email in mappings
        email = None
        for attribute, value, verified in mappings:
            if attribute == 'email':
                email = value

        Lock.lock_identifier(identifier=id_token.sub)

        User = get_user_model()
        user = None
        if provider.strategy == models.OIDCProvider.STRATEGY_FIND_UUID:
            # use the OP sub to find an user by UUUID
            # it means OP and RP share the same account base and OP is passing its UUID as sub
            try:
                user = User.objects.get(uuid=id_token.sub, is_active=True)
            except User.DoesNotExist:
                pass
            else:
                logger.info('auth_oidc: found user using UUID (=sub) "%s": %s', id_token.sub, user)
        elif provider.strategy == models.OIDCProvider.STRATEGY_FIND_USERNAME:
            users = User.objects.filter(username=id_token.sub, is_active=True).order_by('pk')
            if not users:
                logger.warning('auth_oidc: user with username (=sub) "%s" not found', id_token.sub)
            else:
                user = users[0]
                logger.info('auth_oidc: found user using username (=sub) "%s": %s', id_token.sub, user)
        elif provider.strategy == models.OIDCProvider.STRATEGY_FIND_EMAIL:
            if not email:
                logger.warning(
                    'auth_oidc: email claim absent yet STRATEGY_FIND_EMAIL is set, using subject identifier (%s) instead',
                    id_token.sub,
                )
                email = id_token.sub

            if not email:
                logger.error(
                    'auth_oidc: email lookup activated for provider "%s" yet no email received', provider
                )
            users = User.objects.filter(is_active=True)
            if not app_settings.A2_EMAIL_IS_UNIQUE and provider.ou:
                users = users.filter(ou=provider.ou)
            Lock.lock_email(email)
            try:
                user = users.get_by_email(email)
            except User.DoesNotExist:
                logger.warning('auth_oidc: user with email "%s" not found', email)
            else:
                logger.info('auth_oidc: found user using email "%s": %s', email, user)
        else:
            try:
                user = User.objects.get(
                    oidc_account__provider=provider, oidc_account__sub=id_token.sub, is_active=True
                )
            except User.DoesNotExist:
                pass
            else:
                logger.info('auth_oidc: found user using with sub "%s": %s', id_token.sub, user)

        # eventually create a new user or link to an existing one based on email
        created_user = False
        linked = False
        if not user:
            if provider.strategy == models.OIDCProvider.STRATEGY_CREATE:
                if email:
                    users = User.objects.filter(is_active=True)
                    if not app_settings.A2_EMAIL_IS_UNIQUE and provider.ou:
                        users = users.filter(ou=provider.ou)
                    Lock.lock_email(email)
                    try:
                        user = users.get_by_email(email)
                        linked = True
                    except User.DoesNotExist:
                        pass
                    except User.MultipleObjectsReturned:
                        logger.error(
                            'auth_oidc: cannot create user with sub "%s", too many users with the same email "%s"'
                            ' in ou "%s"',
                            id_token.sub,
                            email,
                            provider.ou,
                        )
                        return
                if not user:
                    user = User.objects.create(ou=user_ou, email=email or '')
                    user.set_unusable_password()
                    created_user = True
                try:
                    oidc_account, created = models.OIDCAccount.objects.get_or_create(
                        provider=provider, user=user, defaults={'sub': id_token.sub}
                    )
                except IntegrityError:
                    raise utils.AlreadyLinked(email=email)

                if not created and oidc_account.sub != id_token.sub:
                    logger.info(
                        'auth_oidc: changed user %s sub from %s to %s (issuer %s)',
                        user,
                        oidc_account.sub,
                        id_token.sub,
                        id_token.iss,
                    )
                    oidc_account.sub = id_token.sub
                    oidc_account.save()
            else:
                if request:
                    messages.warning(request, _('No user found'))
                    if request.journal:
                        request.journal.record(
                            'auth.oidc.user_error',
                            sub=id_token.sub,
                            issuer=id_token.iss,
                        )
                logger.warning(
                    'auth_oidc: cannot create user for sub %r as issuer %r does not allow it',
                    id_token.sub,
                    id_token.iss,
                )
                return None

        if created_user:
            logger.info(
                'auth_oidc: created user %s for sub %s and issuer %s in ou %s',
                user,
                id_token.sub,
                id_token.iss,
                user_ou,
            )

        if linked:
            logger.info('auth_oidc: linked user %s to sub %s and issuer %s', user, id_token.sub, id_token.iss)

        # legacy attributes
        for attribute, value, verified in mappings:
            if attribute not in ('username', 'first_name', 'last_name', 'email'):
                continue
            if getattr(user, attribute) != value:
                logger.info('auth_oidc: set user %s attribute %s to value %s', user, attribute, value)
                setattr(user, attribute, value)
                save_user = True
            if attribute == 'email' and (created_user and verified or user.email != value):
                # email may remain unchanged or its owner be created
                # but its verification state is still relevant here
                user.set_email_verified(verified, source='oidc')
                logger.info('auth_oidc: user email %s is now %sverified', value, '' if verified else 'un')
                save_user = True

        if user.ou is None:
            logger.info('auth_oidc: set user %s ou to %s', user, user_ou)
            user.ou = user_ou
            save_user = True

        if has_ou_mapping and user.ou != user_ou:
            logger.info('auth_oidc: set user %s mapped ou from %s to %s', user, user.ou, user_ou)
            user.ou = user_ou
            save_user = True

        if any(
            hooks.call_hooks(
                'auth_oidc_backend_modify_user',
                user=user,
                user_info=user_info,
                access_token=access_token,
                id_token=id_token,
                provider=provider,
            )
        ):
            save_user = True

        if save_user:
            user.save()

        # new style attributes
        for attribute, value, verified in mappings:
            if attribute in ('username', 'email', 'ou__slug'):
                continue
            if attribute in ('first_name', 'last_name') and not verified:
                continue
            if verified:
                setattr(user.verified_attributes, attribute, value)
            else:
                setattr(user.attributes, attribute, value)

        for action in provider.add_role_actions.all():
            if action.condition:
                if evaluate_condition_template(action.condition, {'attributes': context}):
                    logger.info(
                        'auth_oidc: adding role "%s" based on condition "%s"',
                        action.role,
                        action.condition,
                        extra={'user': user},
                    )
                    if action.role not in user.roles.all():
                        request.journal.record(
                            'auth.oidc.add_role_action',
                            user=user,
                            session=request.session,
                            role=action.role,
                            condition=action.condition,
                        )
                    user.roles.add(action.role)
                else:
                    if action.role in user.roles.all():
                        request.journal.record(
                            'auth.oidc.add_role_action',
                            user=user,
                            session=request.session,
                            role=action.role,
                            condition=action.condition,
                            adding=False,
                        )
                    user.roles.remove(action.role)
            else:
                logger.info('auth_oidc: adding role "%s" to user %s', action.role, user)
                if action.role not in user.roles.all():
                    request.journal.record(
                        'auth.oidc.add_role_action', user=user, session=request.session, role=action.role
                    )
                user.roles.add(action.role)

        return user

    def get_saml2_authn_context(self):
        import lasso

        return lasso.SAML2_AUTHN_CONTEXT_PASSWORD_PROTECTED_TRANSPORT
