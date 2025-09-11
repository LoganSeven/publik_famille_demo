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

import hashlib
import json
import logging
import uuid

import requests
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import REDIRECT_FIELD_NAME
from django.http import HttpResponseBadRequest
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.generic.base import View

from authentic2.utils import crypto
from authentic2.utils.misc import authenticate, good_next_url, login, redirect

from .models import OIDCProvider
from .utils import get_provider, get_provider_by_issuer

logger = logging.getLogger(__name__)


def make_nonce(state):
    return hashlib.sha256(state.encode() + settings.SECRET_KEY.encode()).hexdigest()


def oidc_login(request, pk, next_url=None, passive=None, *args, **kwargs):
    provider = get_provider(pk)
    scopes = set(provider.scopes.split()) | {'openid'}
    state_id = str(uuid.uuid4())
    next_url = next_url or request.GET.get(REDIRECT_FIELD_NAME, '')
    if next_url and not good_next_url(request, next_url):
        next_url = None
    nonce = make_nonce(state_id)
    display = set()
    prompt = set()
    state_content = {
        'state_id': state_id,
        'issuer': provider.issuer,
    }
    if next_url:
        state_content['next'] = next_url
    if passive is True or passive is False:
        if passive:
            prompt.add('none')
        else:
            prompt.add('login')
        state_content['prompt'] = list(prompt)
    params = {
        'client_id': provider.client_id,
        'scope': ' '.join(scopes),
        'response_type': 'code',
        'redirect_uri': request.build_absolute_uri(reverse('oidc-login-callback')),
        'state': crypto.dumps(state_content),
        'nonce': nonce,
    }
    if provider.claims_parameter_supported:
        params['claims'] = json.dumps(provider.authorization_claims_parameter())
    if 'login_hint' in request.GET:
        params['login_hint'] = request.GET['login_hint']
    if provider.max_auth_age:
        params['max_age'] = provider.max_auth_age
    if display:
        params['display'] = ' '.join(display)
    if prompt:
        params['prompt'] = ' '.join(prompt)
    # FIXME: display ?
    # FIXME: prompt ? passive and force_authn
    # FIXME: login_hint ?
    # FIXME: id_token_hint ?
    # FIXME: acr_values ?
    # save request state
    logger.debug(
        'auth_oidc: sent request %s to authorization endpoint "%s"', params, provider.authorization_endpoint
    )
    response = redirect(request, provider.authorization_endpoint, params=params, resolve=False)

    # As the oidc-state is used during a redirect from a third-party, we need
    # it to user SameSite=Lax. See
    # https://developer.mozilla.org/fr/docs/Web/HTTP/Headers/Set-Cookie/SameSite
    # for more explanations.
    response.set_cookie(
        'oidc-state',
        value=state_id,
        path=reverse('oidc-login-callback'),
        httponly=True,
        secure=request.is_secure(),
        samesite='Lax',
    )
    return response


def login_initiate(request, *args, **kwargs):
    if 'iss' not in request.GET:
        return HttpResponseBadRequest('missing iss parameter', content_type='text/plain')
    issuer = request.GET['iss']
    try:
        provider = get_provider_by_issuer(issuer)
    except OIDCProvider.DoesNotExist:
        return HttpResponseBadRequest('unknown issuer %s' % issuer, content_type='text/plain')
    return oidc_login(request, pk=provider.pk, next_url=request.GET.get('target_link_uri'))


class LoginCallback(View):
    next_url = None

    def continue_to_next_url(self, request):
        if self.next_url:
            return redirect(request, self.next_url, resolve=False)
        else:
            return redirect(request, settings.LOGIN_REDIRECT_URL)

    def get(self, request, *args, **kwargs):
        response = self.handle_authorization_response(request)
        # clean the state cookie in all cases
        if 'oidc-state' in request.COOKIES:
            response.delete_cookie('oidc-state')
        return response

    def handle_authorization_response(self, request):
        code = request.GET.get('code')
        raw_state = request.GET.get('state')
        if not raw_state:
            return redirect(request, settings.LOGIN_REDIRECT_URL)
        try:
            state_content = crypto.loads(raw_state)
        except crypto.BadSignature:
            return redirect(request, settings.LOGIN_REDIRECT_URL)

        state = state_content['state_id']
        issuer = state_content['issuer']
        nonce = make_nonce(state)
        self.next_url = state_content.get('next')

        try:
            provider = get_provider_by_issuer(issuer)
        except OIDCProvider.DoesNotExist:
            messages.warning(request, _('Unknown OpenID Connect issuer: "%s"') % issuer)
            logger.warning('auth_oidc: unknown issuer, %s', issuer)
            return self.continue_to_next_url(request)

        # Check state
        if 'oidc-state' not in request.COOKIES or request.COOKIES['oidc-state'] != state:
            logger.warning('auth-oidc: state %s for issuer %s has been lost', state, issuer)
            params = {}
            if self.next_url:
                params['next'] = self.next_url
            response = redirect(request, 'oidc-login', kwargs={'pk': str(provider.pk)}, params=params)
            return response

        if 'error' in request.GET:  # error code path
            return self.handle_error(request, provider, prompt=state_content.get('prompt') or [])
        elif not code:
            messages.warning(request, _('Missing code, report %s to an administrator') % request.request_id)
            logger.warning('auth_oidc: missing code, %r', request.GET)
            return self.continue_to_next_url(request)
        else:
            return self.handle_code(request, provider, nonce, code)

    def handle_code(self, request, provider, nonce, code):
        try:
            token_endpoint_request = {
                'grant_type': 'authorization_code',
                'code': code,
                'redirect_uri': request.build_absolute_uri(request.path),
            }
            logger.debug(
                'auth_oidc: sent request %s to token endpoint "%s"',
                token_endpoint_request,
                token_endpoint_request,
            )
            response = requests.post(
                provider.token_endpoint,
                data=token_endpoint_request,
                auth=(provider.client_id, provider.client_secret),
                timeout=10,
            )
            response.raise_for_status()
        except requests.HTTPError as e:
            status_code = e.response.status_code
            try:
                content = response.json()
            except ValueError:
                content = response.content[:1024]
            if isinstance(content, dict):
                error = content.get('error')
                error_description = content.get('error_description')
            else:
                error = None
                error_description = None
            logger.warning(
                'auth_oidc: token_endpoint returned HTTP error status %s for %s with content %s',
                provider.issuer,
                status_code,
                content,
            )
            if error:
                messages.warning(
                    request,
                    _(
                        'Authentication on %(name)s failed with error "%(error)s", report %(request_id)s to'
                        ' an administrator. '
                    )
                    % {
                        'name': provider.name,
                        'error': error_description or error,
                        'request_id': request.request_id,
                    },
                )
            else:
                messages.warning(
                    request,
                    _('Provider %(name)s is down, report %(request_id)s to an administrator. ')
                    % {
                        'name': provider.name,
                        'request_id': request.request_id,
                    },
                )
            return self.continue_to_next_url(request)

        except requests.RequestException as e:
            logger.warning('auth_oidc: failed to contact the token_endpoint for %s, %s', provider.issuer, e)
            messages.warning(
                request,
                _('Provider %(name)s is down, report %(request_id)s to an administrator. ')
                % {
                    'name': provider.name,
                    'request_id': request.request_id,
                },
            )
            return self.continue_to_next_url(request)
        try:
            result = response.json()
        except ValueError as e:
            logger.warning(
                'auth_oidc: response from %s is not a JSON document, %s, %r',
                provider.token_endpoint,
                e,
                response.content,
            )
            messages.warning(
                request,
                _('Provider %(name)s is down, report %(request_id)s to an administrator. ')
                % {
                    'name': provider.name,
                    'request_id': request.request_id,
                },
            )
            return self.continue_to_next_url(request)
        # token_type is case insensitive, https://tools.ietf.org/html/rfc6749#section-4.2.2
        if (
            'access_token' not in result
            or 'token_type' not in result
            or result['token_type'].lower() != 'bearer'
            or 'id_token' not in result
        ):
            logger.warning(
                'auth_oidc: invalid token endpoint response from %s: %r', provider.token_endpoint, result
            )
            messages.warning(
                request,
                _('Provider %(name)s is down, report %(request_id)s to an administrator. ')
                % {
                    'name': provider.name,
                    'request_id': request.request_id,
                },
            )
            return self.continue_to_next_url(request)
        logger.debug('auth_oidc: got token response %s', result)
        access_token = result.get('access_token')
        user = authenticate(
            request, access_token=access_token, nonce=nonce, id_token=result['id_token'], provider=provider
        )
        if user:
            # remember last tokens for logout
            login(request, user, 'oidc', nonce=nonce)
            tokens = request.session.setdefault('auth_oidc', {}).setdefault('tokens', [])
            tokens.append(
                {
                    'token_response': result,
                    'provider_pk': provider.pk,
                }
            )
        return self.continue_to_next_url(request)

    errors = {
        'access_denied': {
            'error_description': _('Connection was denied by you or the identity provider.'),
            'level': logging.INFO,
        }
    }

    def handle_error(self, request, provider, prompt):
        error = request.GET['error']
        error_dict = self.errors.get(error, {})
        level = error_dict.get('level', logging.WARNING)
        remote_error_description = request.GET.get('error_description')
        local_error_description = error_dict.get('error_description')
        error_description = remote_error_description or local_error_description
        error_url = request.GET.get('error_url')

        log_msg = 'auth_oidc: error received '
        if error_description:
            log_msg += '"%s" (%s)' % (error_description, error)
        else:
            log_msg += error
        if prompt:
            log_msg += ' (prompt: %s)' % ','.join(prompt)
        if error_url:
            log_msg += ' see %s' % error_url
        logger.log(level, log_msg)

        if 'none' not in prompt:
            # messages displayed to end user
            if local_error_description:
                # user-friendly error message
                messages.add_message(request, level, local_error_description)
                if remote_error_description:
                    # log a more tech error description for debugging purposes
                    messages.debug(request, remote_error_description)
            elif remote_error_description:
                message = _('%(error_description)s (%(error)s)') % {
                    'error_description': remote_error_description,
                    'error': error,
                }
                messages.add_message(request, level, message)
            else:  # unexpected error code
                message_params = {
                    'request_id': request.request_id,
                    'provider_name': provider and provider.name,
                    'error': error,
                }
                if provider:
                    message = _(
                        'Login with %(provider_name)s failed, please try again later and/or report '
                        '%(request_id)s to an administrator (%(error)s)'
                    )
                else:
                    message = _(
                        'Login with OpenID Connect failed, please try again later and/or report %s to an '
                        'administrator. (%(error)s)'
                    )

                messages.warning(request, message % message_params)
            request.journal.record(
                'user.login.failure',
                authenticator=provider,
                reason=log_msg,
            )
        return self.continue_to_next_url(request)


login_callback = LoginCallback.as_view()
