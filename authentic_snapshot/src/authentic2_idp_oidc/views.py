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

import base64
import datetime
import logging
import math
import secrets
import time
from binascii import Error as Base64Error

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate
from django.db import transaction
from django.http import HttpResponse, HttpResponseNotAllowed, JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils.encoding import force_str
from django.utils.http import urlencode
from django.utils.timezone import now
from django.utils.translation import gettext_lazy as _
from django.views.decorators.csrf import csrf_exempt
from ratelimit.utils import is_ratelimited

from authentic2 import app_settings as a2_app_settings
from authentic2.a2_rbac.models import OrganizationalUnit
from authentic2.custom_user.models import Profile
from authentic2.decorators import setting_enabled
from authentic2.exponential_retry_timeout import ExponentialRetryTimeout
from authentic2.utils import cors, hooks
from authentic2.utils.misc import last_authentication_event, login_require, make_url, redirect
from authentic2.utils.service import set_service
from authentic2.utils.view_decorators import check_view_restriction
from authentic2.views import logout as a2_logout

from . import app_settings, models, utils

logger = logging.getLogger(__name__)


class OIDCException(Exception):
    error_code = None
    error_description = None
    show_message = True

    def __init__(self, error_description=None, status=400, client=None, show_message=None, extra_info=None):
        if error_description:
            self.error_description = error_description
        self.extra_info = extra_info
        self.status = status
        self.client = client
        if show_message is not None:
            self.show_message = show_message
        self.error_description = str(self.error_description)  # unlazy strings

    def json_response(self, request, endpoint):
        content = {
            'error': self.error_code,
        }

        if self.error_description:
            content['error_description'] = self.error_description

        if self.client:
            content['client_id'] = self.client.client_id
            msg = 'idp_oidc: error "%s" in %s endpoint "%s" for client %s'
            if self.extra_info:
                msg += ' (%s)' % self.extra_info
            logger.warning(
                msg,
                self.error_code,
                endpoint,
                self.error_description,
                self.client,
            )
        else:
            logger.warning(
                'idp_oidc: error "%s" in %s endpoint "%s"', self.error_code, endpoint, self.error_description
            )
        return JsonResponse(content, status=self.status)

    def redirect_response(self, request, redirect_uri=None, use_fragment=None, state=None, client=None):
        params = {
            'error': self.error_code,
            'error_description': self.error_description,
        }
        if state is not None:
            params['state'] = state

        log_method = logger.warning
        if not self.show_message:
            # errors not shown as Django messages are regular events, no need to log as warning
            log_method = logger.info

        client = client or self.client
        if client:
            log_method(
                'idp_oidc: error "%s" in authorize endpoint for client %s": %s',
                self.error_code,
                client,
                self.error_description,
            )
        else:
            log_method(
                'idp_oidc: error "%s" in authorize endpoint: %s', self.error_code, self.error_description
            )

        if self.show_message:
            messages.error(
                request,
                _('OpenID Connect Error "%(error_code)s": %(error_description)s')
                % {'error_code': self.error_code, 'error_description': self.error_description},
            )

        if redirect_uri:
            if use_fragment:
                return redirect(request, redirect_uri + '#%s' % urlencode(params), resolve=False)
            else:
                return redirect(request, redirect_uri, params=params, resolve=False)
        else:
            return redirect(request, 'continue', resolve=True)


class InvalidRequest(OIDCException):
    error_code = 'invalid_request'


class InvalidToken(OIDCException):
    error_code = 'invalid_token'


class MissingParameter(InvalidRequest):
    def __init__(self, parameter, **kwargs):
        super().__init__(error_description=_('Missing parameter "%s"') % parameter, **kwargs)


class UnsupportedResponseType(OIDCException):
    error_code = 'unsupported_response_type'


class InvalidScope(OIDCException):
    error_code = 'invalid_scope'


class LoginRequired(OIDCException):
    error_code = 'login_required'
    show_message = False


class InteractionRequired(OIDCException):
    error_code = 'interaction_required'
    show_message = False


class AccessDenied(OIDCException):
    error_code = 'access_denied'
    show_message = False


class UnauthorizedClient(OIDCException):
    error_code = 'unauthorized_client'


class InvalidClient(OIDCException):
    error_code = 'invalid_client'


class InvalidGrant(OIDCException):
    error_code = 'invalid_grant'


class WrongClientSecret(InvalidClient):
    error_description = _('Wrong client secret')

    def __init__(self, *args, wrong_id, **kwargs):
        kwargs['extra_info'] = str(_('received %s') % force_str(wrong_id))
        super().__init__(*args, **kwargs)


class CORSInvalidOrigin(OIDCException):
    error_code = 'invalid_origin'


def idtoken_duration(client):
    return client.idtoken_duration or datetime.timedelta(seconds=app_settings.IDTOKEN_DURATION)


def allowed_scopes(client):
    return client.scope_set() or app_settings.SCOPES or ['openid', 'email', 'profile']


def is_scopes_allowed(scopes, client):
    return scopes <= set(allowed_scopes(client))


@setting_enabled('ENABLE', settings=app_settings)
def openid_configuration(request, *args, **kwargs):
    metadata = {
        'issuer': utils.get_issuer(request),
        'authorization_endpoint': request.build_absolute_uri(reverse('oidc-authorize')),
        'token_endpoint': request.build_absolute_uri(reverse('oidc-token')),
        'token_revocation_endpoint': request.build_absolute_uri(reverse('oidc-token-revocation')),
        'jwks_uri': request.build_absolute_uri(reverse('oidc-certs')),
        'end_session_endpoint': request.build_absolute_uri(reverse('oidc-logout')),
        'response_types_supported': ['code', 'token', 'token id_token'],
        'subject_types_supported': ['public', 'pairwise'],
        'token_endpoint_auth_methods_supported': [
            'client_secret_post',
            'client_secret_basic',
        ],
        'id_token_signing_alg_values_supported': [
            'RS256',
            'HS256',
            'ES256',
        ],
        'userinfo_endpoint': request.build_absolute_uri(reverse('oidc-user-info')),
        'frontchannel_logout_supported': True,
        'frontchannel_logout_session_supported': True,
        'code_challenge_methods_supported': ['plain', 'S256'],
    }
    response = JsonResponse(metadata)
    cors.set_headers(response, origin='*')
    return response


@setting_enabled('ENABLE', settings=app_settings)
def certs(request, *args, **kwargs):
    response = HttpResponse(utils.get_jwkset().export(private_keys=False), content_type='application/json')
    cors.set_headers(response, origin='*')
    return response


@check_view_restriction
@setting_enabled('ENABLE', settings=app_settings)
def authorize(request, *args, **kwargs):
    validated_redirect_uri = None
    client_id = None
    client = None
    client_id = request.GET.get('client_id', '')
    redirect_uri = request.GET.get('redirect_uri', '')
    state = request.GET.get('state')
    use_fragment = False
    try:
        if not client_id:
            raise MissingParameter('client_id')
        if not redirect_uri:
            raise MissingParameter('redirect_uri')
        client = get_client(client_id=client_id)
        if not client:
            raise InvalidRequest(_('Unknown client identifier: "%s"') % client_id)
        # define the current service
        set_service(request, client)
        try:
            client.validate_redirect_uri(redirect_uri)
        except ValueError:
            error_description = _('Redirect URI "%s" is unknown.') % redirect_uri
            if settings.DEBUG:
                error_description += _(' Known redirect URIs are: %s') % ', '.join(client.get_redirect_uris())
            raise InvalidRequest(error_description)
        use_fragment = client.authorization_flow == client.FLOW_IMPLICIT
        validated_redirect_uri = redirect_uri
        return authorize_for_client(request, client, validated_redirect_uri)
    except OIDCException as e:
        return e.redirect_response(
            request,
            redirect_uri=validated_redirect_uri,
            state=validated_redirect_uri and state,
            use_fragment=validated_redirect_uri and use_fragment,
            client=client,
        )


def authorize_for_client(request, client, redirect_uri):
    hooks.call_hooks('event', name='sso-request', idp='oidc', service=client)

    state = request.GET.get('state')
    nonce = request.GET.get('nonce')
    login_hint = set(request.GET.get('login_hint', '').split())
    prompt = set(filter(None, request.GET.get('prompt', '').split()))

    # check response_type
    response_type = request.GET.get('response_type', '')
    if not response_type:
        raise MissingParameter('response_type')
    if client.authorization_flow == client.FLOW_RESOURCE_OWNER_CRED:
        raise InvalidRequest(
            _(
                'Client is configured for resource owner password credentials grant, authorize endpoint is'
                ' not usable'
            )
        )
    if client.authorization_flow == client.FLOW_AUTHORIZATION_CODE:
        if response_type != 'code':
            raise UnsupportedResponseType(_('Response type must be "code"'))
    elif client.authorization_flow == client.FLOW_IMPLICIT:
        if not set(filter(None, response_type.split())) in ({'id_token', 'token'}, {'id_token'}):
            raise UnsupportedResponseType(_('Response type must be "id_token token" or "id_token"'))
    else:
        raise NotImplementedError

    # check scope
    scope = request.GET.get('scope', '')
    if not scope:
        raise MissingParameter('scope')
    scopes = utils.scope_set(scope)
    if 'openid' not in scopes:
        raise InvalidScope(_('Scope must contain "openid", received "%s"') % ', '.join(sorted(scopes)))
    if not is_scopes_allowed(scopes, client):
        raise InvalidScope(
            _('Scope may contain "%(allowed_scopes)s" scope(s), received "%(scopes)s"')
            % {
                'allowed_scopes': ', '.join(sorted(allowed_scopes(client))),
                'scopes': ', '.join(sorted(scopes)),
            }
        )

    # check max_age
    max_age = request.GET.get('max_age')
    if max_age:
        try:
            max_age = int(max_age)
            if max_age < 0:
                raise ValueError
        except ValueError:
            raise InvalidRequest(_('Parameter "max_age" must be a positive integer'))

    # implement PKCE, https://www.rfc-editor.org/rfc/rfc7636.html
    code_challenge = request.GET.get('code_challenge') or None
    code_challenge_method = code_challenge and request.GET.get('code_challenge_method', 'plain')
    authorized_pkce_code_challenge_methods = ['plain', 'S256']
    if client.pkce_code_challenge:
        authorized_pkce_code_challenge_methods.remove('plain')
    if (
        response_type == 'code'
        and code_challenge
        and code_challenge_method not in authorized_pkce_code_challenge_methods
    ):
        raise InvalidRequest(
            _('Parameter "code_challenge_method" must be %s')
            % (_(' or ').join(f'"{method}"' for method in authorized_pkce_code_challenge_methods))
        )
    if client.pkce_code_challenge and not code_challenge:
        raise InvalidRequest(_('Parameter "code_challenge_method" MUST be provided'))

    # authentication canceled by user
    if 'cancel' in request.GET:
        raise AccessDenied(_('Authentication cancelled by user'))

    if not request.user.is_authenticated or 'login' in prompt:
        if 'none' in prompt:
            raise LoginRequired(_('Login is required but prompt parameter is "none"'))
        params = {}
        if nonce is not None:
            params['nonce'] = nonce
        return login_require(request, params=params, login_hint=login_hint)

    # view restriction and passive SSO
    if hasattr(request, 'view_restriction_response'):
        if request.user.is_authenticated and 'none' in prompt:
            raise InteractionRequired(_('User profile is not complete but prompt parameter is "none"'))
        return request.view_restriction_response

    # if user not authorized, a ServiceAccessDenied exception
    # is raised and handled by ServiceAccessMiddleware
    client.authorize(request.user)

    last_auth = last_authentication_event(request=request)
    if max_age is not None and time.time() - last_auth['when'] >= max_age:
        if 'none' in prompt:
            raise LoginRequired(_('Login is required because of max_age, but prompt parameter is "none"'))
        params = {}
        if nonce is not None:
            params['nonce'] = nonce
        return login_require(request, params=params, login_hint=login_hint)

    iat = now()  # iat = issued at

    user_has_selectable_profiles = False
    needs_scope_validation = False
    profile = None
    new_authz = None
    if client.authorization_mode != client.AUTHORIZATION_MODE_NONE or 'consent' in prompt:
        # authorization by user is mandatory, as per local configuration or per explicit request by
        # the RP
        if client.authorization_mode in (
            client.AUTHORIZATION_MODE_NONE,
            client.AUTHORIZATION_MODE_BY_SERVICE,
        ):
            auth_manager = client.authorizations
        elif client.authorization_mode == client.AUTHORIZATION_MODE_BY_OU:
            auth_manager = client.ou.oidc_authorizations

        qs = auth_manager.filter(user=request.user)
        if 'consent' in prompt:
            # if consent is asked we delete existing authorizations
            # it seems to be the safer option
            qs.delete()
            qs = auth_manager.none()
        else:
            qs = qs.filter(expired__gte=iat)
        authorized_scopes = set()
        authorized_profile = None
        for authorization in qs:
            authorized_scopes |= authorization.scope_set()
            # load first authorized profile
            if not authorized_profile and authorization.profile:
                authorized_profile = authorization.profile
        if request.user.profiles.count() and not authorized_profile:
            user_has_selectable_profiles = True
        else:
            profile = authorized_profile
        if (authorized_scopes & scopes) < scopes:
            needs_scope_validation = True
        if needs_scope_validation or (user_has_selectable_profiles and client.activate_user_profiles):
            if request.method == 'POST':
                if request.POST.get('profile-validation', ''):
                    try:
                        profile = Profile.objects.get(
                            user=request.user,
                            id=request.POST['profile-validation'],
                        )
                    except Profile.DoesNotExist:
                        pass
                if 'accept' in request.POST:
                    if (
                        'do_not_ask_again' in request.POST
                        or client.always_save_authorization
                        or 'offline_access' in scopes
                    ):
                        pk_to_deletes = []
                        for authorization in qs:
                            # clean obsolete authorizations
                            if authorization.scope_set() <= scopes:
                                pk_to_deletes.append(authorization.pk)
                        new_authz = auth_manager.create(
                            user=request.user,
                            profile=profile,
                            scopes=' '.join(sorted(scopes)),
                            expired=iat
                            + datetime.timedelta(days=client.authorization_default_duration or 365),
                        )
                        if pk_to_deletes:
                            auth_manager.filter(pk__in=pk_to_deletes).delete()
                        request.journal.record(
                            'user.service.sso.authorization', service=client, scopes=list(sorted(scopes))
                        )
                        logger.info(
                            'idp_oidc: authorized scopes %s saved for service %s', ' '.join(scopes), client
                        )
                    else:
                        logger.info('idp_oidc: authorized scopes %s for service %s', ' '.join(scopes), client)
                else:
                    request.journal.record(
                        'user.service.sso.refusal', service=client, scopes=list(sorted(scopes))
                    )
                    raise AccessDenied(_('User did not consent'))
            else:
                return render(
                    request,
                    'authentic2_idp_oidc/authorization.html',
                    {
                        'user_has_selectable_profiles': user_has_selectable_profiles,
                        'needs_scope_validation': needs_scope_validation,
                        'client': client,
                        'scopes': scopes - {'openid'},
                        'profile_types': set(
                            Profile.objects.filter(user=request.user).values_list(
                                'profile_type__slug', flat=True
                            )
                        ),
                    },
                )

    iss = utils.get_issuer(request)
    if response_type == 'code':
        code = models.OIDCCode.objects.create(
            client=client,
            user=request.user,
            profile=profile,
            scopes=' '.join(scopes),
            state=state,
            nonce=nonce,
            redirect_uri=redirect_uri,
            expired=iat + datetime.timedelta(seconds=30),
            auth_time=datetime.datetime.fromtimestamp(last_auth['when'], datetime.UTC),
            session_key=request.session.session_key,
            code_challenge=code_challenge,
            code_challenge_method=code_challenge
            and getattr(models.OIDCCode, f'CODE_CHALLENGE_METHOD_{code_challenge_method.upper()}'),
            authorization=new_authz,
        )
        logger.info(
            'idp_oidc: sending code %s for scopes %s for service %s', code.uuid, ' '.join(scopes), client
        )
        params = {
            'code': str(code.uuid),
            # include issuer as per oauth2 server issuer identification
            # (https://datatracker.ietf.org/doc/html/rfc9207)
            'iss': iss,
        }
        if state is not None:
            params['state'] = state
        response = redirect(request, redirect_uri, params=params, resolve=False)
    else:
        need_access_token = 'token' in response_type.split()
        if 'profile-validation' in request.POST:
            try:
                profile = Profile.objects.get(
                    id=request.POST.get('profile-validation', None),
                    user=request.user,
                )
            except Profile.DoesNotExist:
                pass
        if need_access_token:
            if client.access_token_duration is None:
                expires_in = datetime.timedelta(seconds=request.session.get_expiry_age())
                expired = None
            else:
                expires_in = client.access_token_duration
                expired = iat + client.access_token_duration
            access_token = models.OIDCAccessToken.objects.create(
                client=client,
                user=request.user,
                scopes=' '.join(scopes),
                session_key=request.session.session_key,
                expired=expired,
                profile=profile,
                authorization=new_authz,
            )
        acr = '0'
        if nonce is not None and last_auth.get('nonce') == nonce:
            acr = '1'
        id_token = utils.create_user_info(
            request, client, request.user, scopes, id_token=True, profile=profile
        )
        exp = iat + idtoken_duration(client)
        id_token.update(
            {
                'iss': iss,
                'aud': client.client_id,
                'exp': int(exp.timestamp()),
                'iat': int(iat.timestamp()),
                'auth_time': last_auth['when'],
                'acr': acr,
                'sid': utils.get_session_id(request.session, client),
            }
        )
        if nonce is not None:
            id_token['nonce'] = nonce
        params = {
            'id_token': utils.make_idtoken(client, id_token),
        }
        if state is not None:
            params['state'] = state
        if need_access_token:
            params.update(
                {
                    'access_token': access_token.uuid,
                    'token_type': 'Bearer',
                    'expires_in': int(expires_in.total_seconds()),
                }
            )
        # query is transfered through the hashtag
        response = redirect(request, redirect_uri + '#%s' % urlencode(params), resolve=False)
    request.journal.record('user.service.sso', service=client, how=last_auth and last_auth.get('how'))
    hooks.call_hooks('event', name='sso-success', idp='oidc', service=client, user=request.user)
    utils.add_oidc_session(request, client)
    return response


def parse_http_basic(request):
    authorization = request.headers['Authorization'].split()
    if authorization[0] != 'Basic' or len(authorization) != 2:
        return None, None
    try:
        decoded = force_str(base64.b64decode(authorization[1]))
    except Base64Error:
        return None, None
    parts = decoded.split(':')
    if len(parts) != 2:
        return None, None
    return parts


def get_client(client_id, client=None):
    if not client:
        try:
            client = models.OIDCClient.objects.get(client_id=client_id)
        except models.OIDCClient.DoesNotExist:
            return None
    else:
        if client.client_id != client_id:
            return None
    return client


def authenticate_client_secret(client, client_secret):
    raw_client_client_secret = client.client_secret.encode('utf-8')
    raw_provided_client_secret = client_secret.encode('utf-8')
    if len(raw_client_client_secret) != len(raw_provided_client_secret):
        raise WrongClientSecret(client=client, wrong_id=raw_provided_client_secret)
    if not secrets.compare_digest(raw_client_client_secret, raw_provided_client_secret):
        raise WrongClientSecret(client=client, wrong_id=raw_provided_client_secret)
    return client


def is_ro_cred_grant_ratelimited(request, key='ip', increment=True):
    return is_ratelimited(
        request,
        group='ro-cred-grant',
        increment=increment,
        key=key,
        rate=app_settings.PASSWORD_GRANT_RATELIMIT,
    )


def authenticate_client(request):
    '''Authenticate client on the token endpoint'''

    if 'authorization' in request.headers:
        client_id, client_secret = parse_http_basic(request)
    elif 'client_id' in request.POST:
        client_id = request.POST.get('client_id', '')
        client_secret = request.POST.get('client_secret', '')
    else:
        raise InvalidRequest('missing client_id')

    if not client_id:
        raise InvalidClient(_('Empty client identifier'))

    if not client_secret:
        raise InvalidRequest('missing client_secret')

    client = get_client(client_id)
    if not client:
        raise InvalidClient(_('Wrong client identifier: %s') % client_id)

    if cors.is_cors_request(request):
        if not cors.is_good_origin(request, client.get_redirect_uris()):
            raise CORSInvalidOrigin(_('Your Origin header does not match the configured redirect_uris'))

    return authenticate_client_secret(client, client_secret)


def idtoken_from_user_credential(request):
    # if rate limit by ip is exceeded, do not even try client authentication
    if is_ro_cred_grant_ratelimited(request, increment=False):
        raise InvalidRequest('Rate limit exceeded for IP address "%s"' % request.META.get('REMOTE_ADDR', ''))

    try:
        client = authenticate_client(request)
    except InvalidClient:
        # increment rate limit by IP
        if is_ro_cred_grant_ratelimited(request):
            raise InvalidRequest(
                _('Rate limit exceeded for IP address "%s"') % request.META.get('REMOTE_ADDR', '')
            )
        raise

    # check rate limit by client id
    if is_ro_cred_grant_ratelimited(request, key=lambda group, request: client.client_id):
        raise InvalidClient(
            _('Rate limit of %(ratelimit)s exceeded for client "%(client)s"')
            % {'ratelimit': app_settings.PASSWORD_GRANT_RATELIMIT, 'client': client},
            client=client,
        )

    if request.headers.get('content-type') != 'application/x-www-form-urlencoded':
        raise InvalidRequest(
            _('Wrong content type. request content type must be \'application/x-www-form-urlencoded\''),
            client=client,
        )
    username = request.POST.get('username')
    scope = request.POST.get('scope')
    profile_id = request.POST.get('profile')

    # scope is ignored, we used the configured scope

    if not all((username, request.POST.get('password'))):
        raise InvalidRequest(
            _(
                'Request must bear both username and password as parameters using the'
                ' "application/x-www-form-urlencoded" media type'
            ),
            client=client,
        )

    if client.authorization_flow != models.OIDCClient.FLOW_RESOURCE_OWNER_CRED:
        raise UnauthorizedClient(
            _('Client is not configured for resource owner password credential grant'), client=client
        )

    exponential_backoff = ExponentialRetryTimeout(
        key_prefix='idp-oidc-ro-cred-grant',
        duration=a2_app_settings.A2_LOGIN_EXPONENTIAL_RETRY_TIMEOUT_DURATION,
        factor=a2_app_settings.A2_LOGIN_EXPONENTIAL_RETRY_TIMEOUT_FACTOR,
    )
    backoff_keys = (username, client.client_id)

    seconds_to_wait = exponential_backoff.seconds_to_wait(*backoff_keys)
    seconds_to_wait = min(seconds_to_wait, a2_app_settings.A2_LOGIN_EXPONENTIAL_RETRY_TIMEOUT_MAX_DURATION)
    if seconds_to_wait:
        raise InvalidRequest(
            _('Too many attempts with erroneous RO password, you must wait %s seconds to try again.')
            % int(math.ceil(seconds_to_wait)),
            client=client,
        )

    ou = None
    if 'ou_slug' in request.POST:
        try:
            ou = OrganizationalUnit.objects.get(slug=request.POST.get('ou_slug'))
        except OrganizationalUnit.DoesNotExist:
            raise InvalidRequest(
                _('Parameter "ou_slug" does not match an existing organizational unit'), client=client
            )

    user = authenticate(request, username=username, password=request.POST.get('password'), ou=ou)
    if not user:
        exponential_backoff.failure(*backoff_keys)
        raise AccessDenied(_('Invalid user credentials'), client=client)

    # limit requested scopes
    if scope is not None:
        scopes = utils.scope_set(scope) & client.scope_set()
    else:
        scopes = client.scope_set()

    exponential_backoff.success(*backoff_keys)
    iat = now()  # iat = issued at
    # make access_token
    if client.access_token_duration is None:
        expires_in = datetime.timedelta(seconds=app_settings.ACCESS_TOKEN_DURATION)
    else:
        expires_in = client.access_token_duration

    profile = None
    if profile_id:
        if not client.activate_user_profiles:
            raise AccessDenied(
                _('User profile requested yet client does not manage profiles.'), client=client
            )
        try:
            profile = Profile.objects.get(id=profile_id, user=user)
        except Profile.DoesNotExist:
            raise AccessDenied(_('Invalid profile'), client=client)

    access_token = models.OIDCAccessToken.objects.create(
        client=client,
        user=user,
        scopes=' '.join(scopes),
        session_key='',
        expired=iat + expires_in,
        profile=profile,
        authorization=None,
    )
    # make id_token
    id_token = utils.create_user_info(request, client, user, scopes, profile=profile, id_token=True)
    exp = iat + idtoken_duration(client)
    id_token.update(
        {
            'iss': utils.get_issuer(request),
            'aud': client.client_id,
            'exp': int(exp.timestamp()),
            'iat': int(iat.timestamp()),
            'auth_time': int(iat.timestamp()),
            'acr': '0',
        }
    )
    return JsonResponse(
        {
            'access_token': str(access_token.uuid),
            'token_type': 'Bearer',
            'expires_in': int(expires_in.total_seconds()),
            'id_token': utils.make_idtoken(client, id_token),
        }
    )


def tokens_from_authz_code(request):
    client = authenticate_client(request)

    code = request.POST.get('code')
    if not code:
        raise MissingParameter('code', client=client)
    oidc_code_qs = models.OIDCCode.objects.filter(expired__gte=now()).select_related()
    try:
        oidc_code = oidc_code_qs.get(uuid=code)
    except models.OIDCCode.DoesNotExist:
        raise InvalidGrant(_('Code is unknown or has expired.'), client=client)
    if oidc_code.client != client:
        raise InvalidGrant(_('Code was issued to a different client.'), client=client)
    if not oidc_code.is_valid():
        raise InvalidGrant(_('User is disconnected or session was lost.'), client=client)
    redirect_uri = request.POST.get('redirect_uri')
    if oidc_code.redirect_uri != redirect_uri:
        raise InvalidGrant(_('Redirect_uri does not match the code.'), client=client)

    if oidc_code.code_challenge:
        # PKCE: Compare code_challenge to the one generated using code_verifier
        code_verifier = request.POST.get('code_verifier')
        if not code_verifier:
            raise MissingParameter('code_verifier', client=client)
        if oidc_code.code_challenge_method == models.OIDCCode.CODE_CHALLENGE_METHOD_PLAIN:
            code_challenge = code_verifier
        elif oidc_code.code_challenge_method == models.OIDCCode.CODE_CHALLENGE_METHOD_S256:
            code_challenge = utils.pkce_s256(code_verifier)
        else:
            raise NotImplementedError('unknown code_challenge_method %s' % oidc_code.code_challenge_method)
        if not secrets.compare_digest(oidc_code.code_challenge, code_challenge):
            raise InvalidGrant(_('The code_verifier does not match the code_challenge.'), client=client)
    if client.access_token_duration is None:
        expires_in = datetime.timedelta(seconds=oidc_code.session.get_expiry_age())
    else:
        expires_in = client.access_token_duration
    expired = now() + expires_in
    access_token = models.OIDCAccessToken.objects.create(
        client=client,
        user=oidc_code.user,
        scopes=oidc_code.scopes,
        session_key=oidc_code.session_key,
        expired=expired,
        profile=oidc_code.profile,
        authorization=oidc_code.authorization,
    )
    start = now()
    acr = '0'
    if (
        oidc_code.nonce is not None
        and last_authentication_event(session=oidc_code.session).get('nonce') == oidc_code.nonce
    ):
        acr = '1'
    # prefill id_token with user info
    id_token = utils.create_user_info(
        request,
        client,
        oidc_code.user,
        oidc_code.scope_set(),
        id_token=True,
        profile=oidc_code.profile,
    )
    exp = start + idtoken_duration(client)
    id_token.update(
        {
            'iss': utils.get_issuer(request),
            'sub': utils.make_sub(client, oidc_code.user, profile=oidc_code.profile),
            'aud': client.client_id,
            'exp': int(exp.timestamp()),
            'iat': int(start.timestamp()),
            'auth_time': int(oidc_code.auth_time.timestamp()),
            'acr': acr,
            'sid': utils.get_session_id(oidc_code.session, client),
        }
    )
    if oidc_code.nonce is not None:
        id_token['nonce'] = oidc_code.nonce
    response = {
        'access_token': str(access_token.uuid),
        'token_type': 'Bearer',
        'expires_in': int(expires_in.total_seconds()),
        'id_token': utils.make_idtoken(client, id_token),
    }
    if 'offline_access' in client.scope_set() and client.uses_refresh_tokens:
        refresh_token = models.OIDCRefreshToken.objects.create(
            user=oidc_code.user,
            client=client,
            expired=now() + datetime.timedelta(seconds=app_settings.REFRESH_TOKEN_DURATION),
            scopes=oidc_code.scopes,
            authorization=oidc_code.authorization,
        )
        response.update({'refresh_token': refresh_token.uuid})
    return JsonResponse(response)


def access_token_from_refresh_request(request):
    client = authenticate_client(request)
    if not client.uses_refresh_tokens:
        raise OIDCException('refresh_token grant type is not allowed for client %s' % client.client_id)
    uuid = request.POST.get('refresh_token', '')
    if not uuid:
        raise OIDCException('refresh_token parameter missing from token refresh request')
    try:
        refresh_token = models.OIDCRefreshToken.objects.get(
            client=client,
            uuid=uuid,
        )
    except models.OIDCRefreshToken.DoesNotExist:
        raise OIDCException('invalid token refresh request, token is invalid or stale')

    if not refresh_token.is_valid():
        raise OIDCException('invalid refresh token, token has reached expiry')

    if client.access_token_duration is None:
        expires_in = datetime.timedelta(seconds=request.session.get_expiry_age())
        expired = None
    else:
        expires_in = client.access_token_duration
        expired = refresh_token.created + expires_in

    with transaction.atomic():
        # invalidate previously issued access tokens linked to this refresh token
        models.OIDCAccessToken.objects.filter(refresh_token=refresh_token).update(expired=now())
        # same for previous refresh token links in the refresh chain
        models.OIDCRefreshToken.objects.filter(refresh_token=refresh_token).update(expired=now())
        access_token = models.OIDCAccessToken.objects.create(
            client=client,
            user=refresh_token.user,
            scopes=refresh_token.scopes,
            expired=expired,
            profile=refresh_token.profile,
            refresh_token=refresh_token,
            authorization=refresh_token.authorization,
        )
        new_refresh_token = models.OIDCRefreshToken.objects.create(
            client=client,
            user=refresh_token.user,
            scopes=refresh_token.scopes,
            expired=now() + datetime.timedelta(seconds=app_settings.REFRESH_TOKEN_DURATION),
            profile=refresh_token.profile,
            refresh_token=refresh_token,
            authorization=refresh_token.authorization,
        )
        # XXX refresh token expires soon after consumption, this behaviour may
        # be django-settable in the future
        refresh_token.expired = now() + datetime.timedelta(minutes=10)
        refresh_token.save(update_fields=['expired'])
    response = {
        'access_token': str(access_token.uuid),
        'token_type': 'Bearer',
        'expires_in': int(expires_in.total_seconds()),
        'refresh_token': str(new_refresh_token.uuid),
    }
    return JsonResponse(response)


@setting_enabled('ENABLE', settings=app_settings)
@csrf_exempt
def token(request, *args, **kwargs):
    if cors.is_preflight_request(request):
        return cors.preflight_response(request, origin=request, methods=('POST',))
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])
    grant_type = request.POST.get('grant_type')
    try:
        if grant_type == 'password':
            response = idtoken_from_user_credential(request)
        elif grant_type == 'authorization_code':
            response = tokens_from_authz_code(request)
        elif grant_type == 'refresh_token':
            response = access_token_from_refresh_request(request)
        else:
            raise InvalidRequest('grant_type must be either authorization_code, password or refresh_token')
        response['Cache-Control'] = 'no-store'
        response['Pragma'] = 'no-cache'
        return response
    except OIDCException as e:
        response = e.json_response(request, endpoint='token')
        # special case of client authentication error with HTTP Basic
        if 'HTTP_AUTHORIZATION' in request and e.error_code == 'invalid_client':
            response['WWW-Authenticate'] = 'Basic'
            response.status_code = 401
        return response


def authenticate_access_token(request):
    if 'authorization' not in request.headers:
        raise InvalidRequest(_('Bearer authentication is mandatory'), status=401)
    authorization = request.headers['Authorization'].split()
    if authorization[0] != 'Bearer' or len(authorization) != 2:
        raise InvalidRequest(_('Invalid Bearer authentication'), status=401)
    try:
        access_token = models.OIDCAccessToken.objects.select_related().get(uuid=authorization[1])
    except models.OIDCAccessToken.DoesNotExist:
        raise InvalidToken(_('Token unknown'), status=401)
    if not access_token.is_valid():
        raise InvalidToken(_('Token expired or user disconnected'), status=401)
    return access_token


@setting_enabled('ENABLE', settings=app_settings)
@csrf_exempt
def revoke(request, *args, **kwargs):
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])
    try:
        response = process_token_revocation(request)
        response['Cache-Control'] = 'no-store'
        response['Pragma'] = 'no-cache'
        return response
    except OIDCException as e:
        response = e.json_response(request, endpoint='revoke')
        if 'HTTP_AUTHORIZATION' in request and e.error_code == 'invalid_client':
            response['WWW-Authenticate'] = 'Basic'  # pragma: no cover
            response.status_code = 401  # pragma: no cover
        return response


def process_token_revocation(request):
    client = authenticate_client(request)
    if 'token_type' not in request.POST:
        raise OIDCException('missing required "token_type" parameter from POST-request payload')
    if 'token' not in request.POST:
        raise OIDCException('missing required "token" parameter from POST-request payload')
    token_uuid = request.POST['token']
    token_type = request.POST['token_type']
    if token_type == 'access_token':
        token_model = models.OIDCAccessToken
    elif token_type == 'refresh_token':
        token_model = models.OIDCRefreshToken
    else:
        raise OIDCException('token_type must either be access_token or refresh_token')
    try:
        with transaction.atomic():
            token = token_model.objects.get(
                uuid=token_uuid,
                client=client,
            )
            token.expired = now()
            token.save(update_fields=('expired',))
    except token_model.DoesNotExist:
        raise OIDCException(f'unknow {token_type} uuid: {token_uuid}')
    response = {'err': 0, 'msg': f'{token_type} {token_uuid} successfully revoked'}
    return JsonResponse(response)


@setting_enabled('ENABLE', settings=app_settings)
@csrf_exempt
def user_info(request, *args, **kwargs):
    if cors.is_preflight_request(request):
        return cors.preflight_response(request, origin='*', headers=('x-requested-with', 'authorization'))

    try:
        access_token = authenticate_access_token(request)
        user_info = utils.create_user_info(
            request,
            access_token.client,
            access_token.user,
            access_token.scope_set(),
            profile=access_token.profile,
        )
        response = JsonResponse(user_info)
    except OIDCException as e:
        error_response = e.json_response(request, endpoint='user_info')
        if e.status == 401:
            error_response['WWW-Authenticate'] = 'Bearer error="%s", error_description="%s"' % (
                e.error_code,
                e.error_description,
            )
        response = error_response
    cors.set_headers(response, origin='*', headers=('x-requested-with', 'authorization'))
    return response


@setting_enabled('ENABLE', settings=app_settings)
def logout(request):
    post_logout_redirect_uri = request.GET.get('post_logout_redirect_uri')
    state = request.GET.get('state')
    if post_logout_redirect_uri:
        provider = models.OIDCClient.find_by_post_logout_redirect_uri(
            post_logout_redirect_uri=post_logout_redirect_uri
        )
        if not provider:
            messages.warning(request, _('Invalid post logout URI'))
            return redirect(request, settings.LOGIN_REDIRECT_URL)
        set_service(request, provider)
        if state:
            post_logout_redirect_uri = make_url(post_logout_redirect_uri, params={'state': state})
    # FIXME: do something with id_token_hint
    return a2_logout(request, next_url=post_logout_redirect_uri, do_local=False, check_referer=False)
