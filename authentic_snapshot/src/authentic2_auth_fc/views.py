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

import json
import logging
import time

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.views import update_session_auth_hash
from django.core.cache import cache
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.forms import Form
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.template.loader import render_to_string
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils.http import urlencode
from django.utils.translation import gettext as _
from django.views.generic import FormView, View
from requests_oauthlib import OAuth2Session

from authentic2 import app_settings as a2_app_settings
from authentic2 import constants
from authentic2.a2_rbac.utils import get_default_ou
from authentic2.forms.passwords import SetPasswordForm
from authentic2.models import Attribute, AttributeValue, Lock
from authentic2.utils import hooks
from authentic2.utils import jwc as utils_jwc
from authentic2.utils import misc as utils_misc
from authentic2.utils import views as utils_views
from authentic2.utils.crypto import check_hmac_url, hash_chain, hmac_url
from authentic2.utils.http import HTTPError, get, parse_json_response, post_json
from authentic2.validators import email_validator

from . import app_settings, models, utils
from .utils import apply_user_info_mappings, build_logout_url, clean_fc_session

logger = logging.getLogger(__name__)
User = get_user_model()


class EmailExistsError(Exception):
    pass


ERRORS_TO_DESCRIPTIONS = {
    'invalid_scope': _('invalid requested data, contact an administrator'),
    'invalid_request': _('invalid request, contact an administrator'),
    'access_denied': _('access denied'),
    'server_error': _('server error, try again later'),
    'temporarily_unavailable': _('service temporarily unavailable, try again later'),
}


def login(request, *args, **kwargs):
    if 'nofc' in request.GET:
        return
    fc_user_info = request.session.get('fc_user_info')
    context = kwargs.pop('context', {}).copy()
    context.update(
        {
            'about_url': app_settings.about_url,
            'fc_user_info': fc_user_info,
        }
    )
    context['login_url'] = utils_misc.make_url('fc-login-or-link', keep_params=True, request=request)
    context['block-extra-css-class'] = 'fc-login'
    template = kwargs.get('template', 'authentic2_auth_fc/login.html')
    return TemplateResponse(request, template, context)


def registration(request, *args, **kwargs):
    kwargs['template'] = 'authentic2_auth_fc/registration.html'
    return login(request, *args, **kwargs)


def profile(request, *args, **kwargs):
    # We prevent unlinking if the user has no usable password and can't change it
    # because we assume that the password is the unique other mean of authentication
    # and unlinking would make the account unreachable.
    unlink = request.user.has_usable_password() or a2_app_settings.A2_REGISTRATION_CAN_CHANGE_PASSWORD

    account_path = utils_misc.reverse('account_management')
    params = {
        'next': account_path,
    }
    link_url = utils_misc.make_url('fc-login-or-link', params=params)

    context = kwargs.pop('context', {}).copy()
    context.update(
        {
            'unlink': unlink,
            'about_url': app_settings.about_url,
            'link_url': link_url,
        }
    )
    return render_to_string('authentic2_auth_fc/linking.html', context, request=request)


class LoginOrLinkView(View):
    """Login with FC, if the FC account is already linked, connect this user,
    if a user is logged link the user to this account, otherwise display an
    error message.
    """

    _next_url = None
    display_message_on_redirect = False

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.authenticator = get_object_or_404(models.FcAuthenticator, enabled=True)

    @property
    def next_url(self):
        return self._next_url or utils_misc.select_next_url(self.request, default=settings.LOGIN_REDIRECT_URL)

    @property
    def redirect_uri(self):
        return self.request.build_absolute_uri(reverse('fc-login-or-link'))

    def redirect(self):
        return utils_misc.redirect(self.request, self.next_url)

    def logout_and_redirect(self):
        url = utils.build_logout_url(self.request, self.authenticator.logout_url, next_url=self.next_url)
        if url:
            clean_fc_session(self.request.session)
            response = utils_misc.redirect(self.request, url, resolve=False)
            response.display_message = False
            return response
        return self.redirect()

    @property
    def fc_display_name(self):
        '''Human representation of the current FC account'''
        display_name = ''
        family_name = self.user_info.get('family_name')
        given_name = self.user_info.get('given_name')
        if given_name:
            display_name += given_name
        if family_name:
            if display_name:
                display_name += ' '
            display_name += family_name
        return display_name

    def get_error_description(self, request):
        return ERRORS_TO_DESCRIPTIONS.get(request.GET.get('error')) or _(
            'technical error, contact an administrator'
        )

    def get(self, request, *args, next_url=None, **kwargs):
        if next_url:
            self._next_url = next_url

        state = request.GET.get('state')
        if state:
            # check state signature and parse it
            try:
                state, self._next_url = self.decode_state(state)
            except ValueError:
                messages.error(request, _('Unable to connect to FranceConnect.'))
                return utils_misc.redirect(request, settings.LOGIN_REDIRECT_URL)

        code = request.GET.get('code')
        if code:
            response = self.handle_authorization_response(request, code=code, state=state)
            response.delete_cookie('fc-state', path=reverse('fc-login-or-link'))
            return response
        elif 'error' in request.GET:
            return self.authorization_error(
                request,
                error=request.GET['error'],
                error_description=self.get_error_description(request),
            )
        else:
            return self.make_authorization_request(request)

    def post(self, request, *args, next_url=None, **kwargs):
        from .forms import FcAccountSelectionForm

        # twofold form instanciation necessary here:
        form = FcAccountSelectionForm(
            data=request.POST,
        )
        form.full_clean()

        # 1. first check sub hidden field before retrieve associated accounts
        if 'sub' in form.cleaned_data:
            accounts = models.FcAccount.objects.filter(
                sub=form.cleaned_data['sub'],
                user__is_active=True,
            )
        # 2. then check account selection integrity, depending on cleaned subject identifier
        form = FcAccountSelectionForm(
            accounts=accounts,
            data=request.POST,
        )
        form.full_clean()
        if form.is_valid():
            account_id = form.cleaned_data['account']
            self.sub = form.cleaned_data['sub']
            self.user_info = json.loads(form.cleaned_data['user_info'])

            if 'select-account' in request.POST:

                if account_id == '-1':
                    # create a new account
                    user, created = self.create_account(
                        request, sub=self.sub, token={}, user_info=self.user_info
                    )
                    return self.finish_login(request, user, self.user_info, created)

                try:
                    account = models.FcAccount.objects.get(id=account_id, sub=self.sub, user__is_active=True)
                except models.FcAccount.DoesNotExist:
                    messages.info(
                        request,
                        _(
                            'Something went wrong during existing-account selection, '
                            'try again or contact the site\'s administrator.'
                        ),
                    )
                    logger.error(
                        'auth_fc: invalid account selection (id=%s, sub=%s)',
                        account_id,
                        self.sub,
                    )
                    return self.redirect()

                if not account.user.is_active:
                    logger.error(
                        'auth_fc: invalid account selection, user %r is inactive',
                        account.user,
                    )

                return self.finish_login(request, account.user, self.user_info, False)

            else:
                logger.info('auth_fc: cancelled linking to %s', self.sub)
                messages.info(request, _('Linking cancelled'))
        else:
            logger.warning('auth_fc: something went wrong at multiaccount selection (%s)', form.data)
            messages.warning(
                request, _('Something went wrong while logging you in, please contact an administrator.')
            )

        return self.redirect()

    def handle_authorization_response(self, request, code, state):
        # check for provider errors at callback time (FC v2 only)
        if 'error' in request.GET:
            logger.warning(
                'auth_fc: token request failed, "%s": "%s"',
                request.GET.get('error') or '',
                request.GET.get('error_description') or '',
            )
            messages.warning(
                request,
                _('Unable to connect to FranceConnect: {desc} ({error}).').format(
                    desc=self.get_error_description(request),
                    error=request.GET.get('error') or '',
                ),
            )
            return self.redirect()

        # regenerte the chain of hash from the stored nonce_seed
        try:
            encoded_seed = request.COOKIES.get('fc-state', '')
            if not encoded_seed:
                raise ValueError
            dummy, hash_nonce, hash_state = hash_chain(3, encoded_seed=encoded_seed)
            if not state or state != hash_state:
                logger.warning('auth_fc: state lost, requesting authorization again')
                raise ValueError
        except ValueError:
            return self.make_authorization_request(request)

        issuer = self.authenticator.issuer
        if issuer:
            iss = request.GET.get('iss')
            if iss != issuer:
                messages.error(request, _('Unable to connect to FranceConnect: invalid issuer.'))
                logger.warning(
                    'auth_fc: authorization failed issuer authz callback param is wrong: received "%s", expected "%s"',
                    iss,
                    issuer,
                )
                return self.redirect()

        # resolve the authorization_code and check the token endpoint response
        self.token = self.resolve_authorization_code(code)
        if not self.token:
            # resolve_authorization_code already logged a warning.
            return self.report_fc_is_down(request)
        if 'error' in self.token:
            logger.warning('auth_fc: token request failed, "%s"', self.token)
            error = self.token['error']
            desc = ERRORS_TO_DESCRIPTIONS.get(
                error,
                self.token.get('error_description') or error,
            )
            messages.warning(
                request,
                _('Unable to connect to FranceConnect: %s.') % desc,
            )
            return self.redirect()

        # parse the id_token
        if not self.token.get('id_token') or not isinstance(self.token['id_token'], str):
            logger.warning('auth_fc: token endpoint did not return an id_token')
            return self.report_fc_is_down(request)

        try:
            self.id_token = utils_jwc.parse_id_token(
                self.token['id_token'],
                self.authenticator,
            )
        except utils_jwc.IDTokenError as e:
            logger.warning('auth_fc: validation of id_token failed: %s', e)
            return self.report_fc_is_down(request)
        logger.debug('auth_fc: parsed id_token %s', self.id_token)

        nonce = self.id_token.get('nonce')
        if nonce != hash_nonce:
            logger.warning('auth_fc: invalid nonce in id_token')
            return self.report_fc_is_down(request)

        id_token_iss = self.id_token.get('iss')
        if issuer and id_token_iss != issuer:
            logger.warning(
                'auth_fc: id_token iss "%s" does not match the expected "%s"', id_token_iss, issuer
            )
            return self.report_fc_is_down(request)

        self.sub = self.id_token.get('sub')
        if not self.sub:
            logger.warning('auth_fc: no sub in id_token %s', self.id_token)
            return self.report_fc_is_down(request)

        # get user info using the access token
        if not self.token.get('access_token') or not isinstance(self.token['access_token'], str):
            logger.warning('auth_fc: token endpoint did not return an access_token')
            return self.report_fc_is_down(request)

        self.user_info = self.get_user_info()
        if self.user_info is None:
            return self.report_fc_is_down(request)
        logger.debug('auth_fc: user_info %s', self.user_info)

        user_info_iss = self.user_info.get('iss')
        if issuer and user_info_iss != issuer:
            logger.warning(
                'auth_fc: user_info iss "%s" does not match the expected "%s"', user_info_iss, issuer
            )
            return self.report_fc_is_down(request)

        # clear FranceConnect down status
        cache.delete('fc_is_down')

        # keep id_token around for logout
        request.session['fc_id_token'] = self.id_token
        request.session['fc_id_token_raw'] = self.token['id_token']

        if request.user.is_authenticated:
            return self.link(request)
        else:
            return self.login(request)

    def encode_state(self, state):
        encoded_state = state + ' ' + self.next_url
        encoded_state += ' ' + hmac_url(settings.SECRET_KEY, encoded_state)
        return encoded_state

    def decode_state(self, state):
        payload, signature = state.rsplit(' ', 1)
        if not check_hmac_url(settings.SECRET_KEY, payload, signature):
            raise ValueError
        state, next_url, *dummy = payload.split(' ')
        return state, next_url

    def make_authorization_request(self, request):
        supported_scopes = {key for key, _ in models.SCOPE_CHOICES}
        scopes = set(self.authenticator.scopes).intersection(supported_scopes)
        scopes.add('openid')  # mandatory hence not appearing in FC authenticator list
        scope = ' '.join(scopes)

        nonce_seed, nonce, state = hash_chain(3)

        # encode the target service and next_url in the state
        full_state = state + ' ' + self.next_url + ' '
        full_state += ' ' + hmac_url(settings.SECRET_KEY, full_state)
        params = {
            'client_id': self.authenticator.client_id,
            'scope': scope,
            'redirect_uri': self.redirect_uri,
            'response_type': 'code',
            'state': self.encode_state(state),
            'nonce': nonce,
            'acr_values': 'eidas1',
            'prompt': 'login consent',
        }
        url = f'{self.authenticator.authorize_url}?{urlencode(params)}'
        logger.debug('auth_fc: authorization_request redirect to %s', url)

        response = HttpResponseRedirect(url)
        # prevent unshown messages to block the navigation to FranceConnect
        response.display_message = self.display_message_on_redirect

        # store nonce_seed in a browser cookie to prevent CSRF and check nonce
        # in id_token on return by generating the hash chain again
        response.set_cookie(
            'fc-state',
            value=nonce_seed,
            path=reverse('fc-login-or-link'),
            httponly=True,
            secure=request.is_secure(),
            samesite='Lax',
        )
        return response

    def resolve_authorization_code(self, authorization_code):
        '''Exchange an authorization_code for an access_token'''
        data = {
            'code': authorization_code,
            'client_id': self.authenticator.client_id,
            'client_secret': self.authenticator.client_secret,
            'redirect_uri': self.redirect_uri,
            'grant_type': 'authorization_code',
        }
        logger.debug('auth_fc: resolve_access_token request params %s', data)

        try:
            token = post_json(
                url=self.authenticator.token_url,
                data=data,
                expected_statuses=[400],
                verify=app_settings.verify_certificate,
                allow_redirects=False,
                timeout=3,
            )
        except HTTPError as e:
            logger.warning('auth_fc: resolve_authorization_code error %s', e)
            return None
        else:
            logger.debug('auth_fc: token endpoint returned "%s"', token)
            return token

    def get_user_info(self):
        try:
            response = get(
                url=self.authenticator.userinfo_url + '?schema=openid',
                session=OAuth2Session(self.authenticator.client_id, token=self.token),
                verify=app_settings.verify_certificate,
                allow_redirects=False,
                timeout=3,
            )
        except HTTPError as e:
            logger.warning('auth_fc: get_user_info error %s', e)
            return None
        logger.debug('auth_fc: get_user_info returned %r', response.content)
        ct = response.headers.get('content-type', '').split('; ', maxsplit=1)[0]
        if ct == 'application/json':
            try:
                return parse_json_response(response)
            except HTTPError as e:
                logger.warning('auth_fc: user_info parsing error %s', e)
        elif ct == 'application/jwt':
            try:
                return utils_jwc.parse_id_token(response.content.decode('utf-8'), self.authenticator)
            except utils_jwc.IDTokenError as e:
                logger.warning('auth_fc: failed to parse UserInfo JWT (%r): %s', response.content, e)
        else:
            logger.warning("auth_fc: UserInfo response's MIME type is invalid ('%s')", ct)
        return None

    def authorization_error(self, request, error, error_description):
        messages.error(request, _('Unable to connect to FranceConnect: %s.') % (error_description or error))
        logger.warning(
            'auth_fc: authorization failed with error=%r error_description=%r', error, error_description or ''
        )
        return utils_misc.redirect(request, 'auth_login', params={'next': self.next_url})

    def report_fc_is_down(self, request):
        messages.warning(request, _('Unable to connect to FranceConnect.'))
        # put FranceConnect status in cache, if it happens for more than 5 minutes, log an error
        last_down = cache.get('fc_is_down')
        now = time.time()
        more_than_5_minutes = last_down and (now - last_down) > 5 * 60
        if more_than_5_minutes:
            logger.error('auth_fc: FranceConnect is down for more than 5 minutes')
        if not last_down or more_than_5_minutes:
            cache.set('fc_is_down', now, 10 * 60)
        return self.redirect()

    def link(self, request):
        '''Request an access grant code and associate it to the current user'''

        # monoaccount consistency error checks
        if (
            not self.authenticator.supports_multiaccount
            and models.FcAccount.objects.exclude(user=request.user)
            .filter(sub=self.sub, user__is_active=True)
            .exists()
        ):
            return self.uniqueness_check_failed(request)

        self.fc_account, created = models.FcAccount.objects.get_or_create(
            sub=self.sub,
            user=request.user,
            defaults={
                'token': json.dumps(self.token),
                'user_info': json.dumps(self.user_info),
            },
        )

        if created:
            logger.info('auth_fc: link created sub %s', self.sub)
            messages.info(
                request, _('Your FranceConnect account {} has been linked.').format(self.fc_display_name)
            )
            hooks.call_hooks('event', name='fc-link', user=request.user, sub=self.sub, request=request)
        else:
            if self.fc_account.created <= request.user.last_login:
                utils_misc.record_authentication_event(request, 'france-connect')
        self.update_user_info(request.user, self.user_info)
        return self.redirect()

    def authn_and_create_account(self, request):
        with transaction.atomic():
            user = utils_misc.authenticate(
                request, sub=self.sub, user_info=self.user_info, token=getattr(self, 'token', {})
            )

            if not user:
                user, created = self.create_account(
                    request, sub=self.sub, token=self.token, user_info=self.user_info
                )
            else:
                created = False

            if not user:
                return self.logout_and_redirect()

            return self.finish_login(request, user, self.user_info, created)

    def login(self, request):
        accounts = models.FcAccount.objects.filter(sub=self.sub, user__is_active=True)
        if not self.authenticator.supports_multiaccount or accounts.count() < 2:
            # no account ambiguity, proceed with authn
            return self.authn_and_create_account(request)
        else:
            from .forms import FcAccountSelectionForm

            # monoaccount consistency error checks
            form = FcAccountSelectionForm(
                initial={'sub': self.sub, 'user_info': json.dumps(self.user_info)},
            )
            context = {'form': form}
            return render(request, 'authentic2_auth_fc/select_account.html', context)

    def finish_login(self, request, user, user_info, created):
        self.update_user_info(user, user_info)
        utils_views.check_cookie_works(request)
        user.backend = 'authentic2_auth_fc.backends.FcBackend'
        utils_misc.login(request, user, 'france-connect')

        # set session expiration policy to EXPIRE_AT_BROWSER_CLOSE
        request.session.set_expiry(0)

        # redirect to account edit page if any required attribute is not filled
        # only on user registration
        missing = created and self.missing_required_attributes(user)
        if missing:
            messages.warning(
                request,
                _('The following fields are mandatory for account creation: %s') % ', '.join(missing),
            )
            return utils_misc.redirect(request, 'profile_edit', params={'next': self.next_url})
        return self.redirect()

    def missing_required_attributes(self, user):
        '''Compute if user has not filled some required attributes.'''
        name_to_label = dict(
            Attribute.objects.filter(required=True, user_editable=True).values_list('name', 'label')
        )
        required = list(a2_app_settings.A2_REGISTRATION_REQUIRED_FIELDS) + list(name_to_label)
        missing = []
        for attr_name in set(required):
            value = getattr(user, attr_name, None) or getattr(user.attributes, attr_name, None)
            if value in [None, '']:
                missing.append(name_to_label[attr_name])
        return missing

    def create_account(self, request, sub, token, user_info):
        email = user_info.get('email')

        try:
            email_validator(email)
        except ValidationError:
            logger.warning('auth_fc: invalid email "%s" was ignored on creation', email)
            email = None

        if email:
            # try to create or find an user with this email
            try:
                user, created = self.get_or_create_user_with_email(email)
            except EmailExistsError:
                user = None
            if not user:
                messages.warning(
                    request,
                    _(
                        'Your FranceConnect email address \'%s\' is already used by another account, so we'
                        ' cannot create an account for you. Please connect with you existing account or'
                        ' create an account with another email address then link it to FranceConnect using'
                        ' your account management page.'
                    )
                    % email,
                )
                return None, False
            if not created and hasattr(user, 'fc_account'):
                messages.warning(
                    request,
                    _(
                        'Your FranceConnect email address "%(email)s" is already used by the FranceConnect'
                        ' account of "%(user)s", so we cannot create an account for you. Please create an'
                        ' account with another email address then link it to FranceConnect using your account'
                        ' management page.'
                    )
                    % {'email': email, 'user': user.get_full_name()},
                )
        else:  # no email, we cannot disembiguate users, let's create it anyway
            user = User.objects.create(ou=get_default_ou())
            created = True

        try:
            if created:
                user.set_unusable_password()
                user.save()

            # As we intercept IntegrityError and we can never be sure if we are
            # in a transaction or not, we must use one to prevent later SQL
            # queries to fail.
            with transaction.atomic():
                models.FcAccount.objects.create(
                    user=user,
                    sub=sub,
                    token=json.dumps(token),
                    user_info=json.dumps(user_info),
                )
        except IntegrityError:
            # uniqueness check failed, as the user is new, it can only mean that the sub is not unique
            # let's try again
            if created:
                user.delete()
            return utils_misc.authenticate(request, sub=sub, token=token, user_info=user_info), False
        except Exception:
            # if anything unexpected happen and user was created, delete it and re-raise
            if created:
                user.delete()
            raise
        else:
            self.update_user_info(user, user_info)
            if created:
                logger.info('auth_fc: new account "%s" created with FranceConnect sub "%s"', user, sub)
                hooks.call_hooks('event', name='fc-create', user=user, sub=sub, request=request)
                utils_misc.send_templated_mail(
                    user,
                    template_names=['authentic2_auth_fc/registration_success'],
                    context={
                        'user': user,
                        'login_url': request.build_absolute_uri(settings.LOGIN_URL),
                    },
                    request=self.request,
                )
                # FC account creation does not rely on the registration_completion generic view.
                # Registration event has to be recorded here:
                request.journal.record('user.registration', user=user, how='france-connect')
            else:
                logger.info('auth_fc: existing account "%s" linked to FranceConnect sub "%s"', user, sub)
                hooks.call_hooks('event', name='fc-link', user=user, sub=sub, request=request)

        authenticated_user = utils_misc.authenticate(request, sub=sub, user_info=user_info, token=token)
        return authenticated_user, created

    def uniqueness_check_failed(self, request):
        # currently logged :
        if models.FcAccount.objects.filter(user=request.user).exists():
            # cannot link because we are already linked to another FC account
            messages.error(request, _('Your account is already linked to FranceConnect'))
        else:
            # cannot link because the FC account is already linked to another account.
            messages.error(
                request,
                _('The FranceConnect identity {} is already linked to another account.').format(
                    self.fc_display_name
                ),
            )
        return self.logout_and_redirect()

    def update_user_info(self, user, user_info):
        # always handle given_name and family_name
        updated = []
        if user_info.get('given_name') and user.first_name != user_info['given_name']:
            user.first_name = user_info['given_name']
            updated.append('given name: "%s"' % user_info['given_name'])
        if user_info.get('family_name') and user.last_name != user_info['family_name']:
            user.last_name = user_info['family_name']
            updated.append('family name: "%s"' % user_info['family_name'])
        if updated:
            user.save()
            logger.debug('auth_fc: updated (%s)', ' - '.join(updated))
        apply_user_info_mappings(user, user_info)
        return user

    def get_or_create_user_with_email(self, email):
        ou = get_default_ou()

        qs = User.objects
        if not a2_app_settings.A2_EMAIL_IS_UNIQUE:
            qs = qs.filter(ou=ou)

        Lock.lock_email(email)
        try:
            user = qs.get_by_email(email)
            if not self.authenticator.link_by_email:
                raise EmailExistsError
        except User.DoesNotExist:
            return User.objects.create(ou=ou, email=email), True
        except User.MultipleObjectsReturned:
            raise EmailExistsError

        if user.ou != ou:
            raise EmailExistsError
        return user, False


login_or_link = LoginOrLinkView.as_view()


class UnlinkView(FormView):
    template_name = 'authentic2_auth_fc/unlink.html'

    def get_success_url(self):
        url = reverse('account_management')
        if app_settings.logout_when_unlink:
            # logout URL can be None if not session exists with FC
            authenticator = get_object_or_404(models.FcAuthenticator, enabled=True)
            url = build_logout_url(self.request, authenticator.logout_url, next_url=url) or url
        return url

    def get_form_class(self):
        form_class = Form
        if self.must_set_password():
            form_class = SetPasswordForm
        return form_class

    def get_form_kwargs(self, **kwargs):
        kwargs = super().get_form_kwargs(**kwargs)
        if self.must_set_password():
            kwargs['user'] = self.request.user
        return kwargs

    def must_set_password(self):
        for event in self.request.session.get(constants.AUTHENTICATION_EVENTS_SESSION_KEY, []):
            if event['how'].startswith('password'):
                return False
        return self.request.user.can_change_password()

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            raise PermissionDenied()
        # We prevent unlinking if the user has no usable password and can't change it
        # because we assume that the password is the unique other mean of authentication
        # and unlinking would make the account unreachable.
        if self.must_set_password() and not a2_app_settings.A2_REGISTRATION_CAN_CHANGE_PASSWORD:
            # Prevent access to the view.
            raise Http404
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        if self.must_set_password():
            form.save()
            update_session_auth_hash(self.request, self.request.user)
            logger.info('auth_fc: user %s has set a password', self.request.user)
        links = models.FcAccount.objects.filter(user=self.request.user)
        for link in links:
            logger.info('auth_fc: user %s unlinked from %s', self.request.user, link)
        hooks.call_hooks('event', name='fc-unlink', user=self.request.user)
        messages.info(self.request, _('Your account link to FranceConnect has been deleted.'))
        links.delete()
        # FC mapping config may have changed over time, hence it is impossible to tell which
        # attribute was verified at FC link time.
        AttributeValue.objects.with_owner(self.request.user).update(verified=False)
        response = super().form_valid(form)
        if app_settings.logout_when_unlink:
            response.display_message = False
        clean_fc_session(self.request.session)
        return response

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if self.must_set_password():
            context['no_password'] = True
        return context

    def post(self, request, *args, **kwargs):
        if 'cancel' in request.POST:
            return utils_misc.redirect(request, 'account_management')
        return super().post(request, *args, **kwargs)


unlink = UnlinkView.as_view()


class LogoutReturnView(View):
    def get(self, request, *args, **kwargs):
        state = request.GET.get('state')
        clean_fc_session(request.session)
        states = request.session.pop('fc_states', None)
        next_url = None
        if states and state in states:
            next_url = states[state].get('next')
        if not next_url:
            next_url = reverse('auth_logout')
        return HttpResponseRedirect(next_url)


logout = LogoutReturnView.as_view()
