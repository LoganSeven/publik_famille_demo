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

import collections
import logging
import re
import time
from email.utils import parseaddr

from django import shortcuts
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import REDIRECT_FIELD_NAME, get_user_model
from django.contrib.auth import logout as auth_logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import PasswordChangeView as DjPasswordChangeView
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import FieldDoesNotExist, ValidationError
from django.db.models import Count, OuterRef, Subquery
from django.db.models.query import Q
from django.db.transaction import atomic
from django.forms import CharField
from django.http import Http404, HttpResponseBadRequest, HttpResponseForbidden, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.template import loader
from django.template.loader import render_to_string
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt, ensure_csrf_cookie, requires_csrf_token
from django.views.defaults import permission_denied as django_permission_denied
from django.views.generic import ListView, TemplateView
from django.views.generic.base import RedirectView, View
from django.views.generic.edit import CreateView, DeleteView, FormView, UpdateView
from ratelimit.utils import is_ratelimited

from authentic2.a2_rbac.models import Role
from authentic2.custom_user.models import iter_attributes
from authentic2.forms import authentication as authentication_forms
from authentic2_idp_oidc.models import OIDCAuthorization

from . import app_settings, attribute_kinds, cbv, constants, decorators, models, validators
from .a2_rbac.models import OrganizationalUnit as OU
from .a2_rbac.utils import get_default_ou
from .forms import passwords as passwords_forms
from .forms import profile as profile_forms
from .forms import registration as registration_forms
from .models import Lock
from .utils import crypto, hooks
from .utils import misc as utils_misc
from .utils import sms as utils_sms
from .utils import switch_user as utils_switch_user
from .utils.evaluate import make_condition_context
from .utils.service import get_service, set_home_url
from .utils.sms import SMSError, send_registration_sms, sms_ratelimit_key
from .utils.view_decorators import enable_view_restriction
from .utils.views import csrf_token_check

User = get_user_model()

logger = logging.getLogger(__name__)


class HomeURLMixin:
    def dispatch(self, request, *args, **kwargs):
        set_home_url(request)
        return super().dispatch(request, *args, **kwargs)


class EditProfile(HomeURLMixin, cbv.SuccessUrlViewMixin, cbv.HookMixin, cbv.TemplateNamesMixin, UpdateView):
    model = User
    template_names = ['profiles/edit_profile.html', 'authentic2/accounts_edit.html']
    title = _('Edit account data')
    success_url = reverse_lazy('account_management')

    def get_template_names(self):
        template_names = []
        if 'scope' in self.kwargs:
            template_names.append('authentic2/accounts_edit_%s.html' % self.kwargs['scope'])
        template_names.extend(self.template_names)
        return template_names

    @classmethod
    def can_edit_profile(cls):
        fields, dummy_labels = cls.get_fields()
        return bool(fields) and app_settings.A2_PROFILE_CAN_EDIT_PROFILE

    @classmethod
    def get_fields(cls, scopes=None):
        editable_profile_fields = []
        for field in app_settings.A2_PROFILE_FIELDS:
            if isinstance(field, (list, tuple)):
                field_name = field[0]
            else:
                field_name = field
            try:
                attribute = models.Attribute.objects.get(name=field_name)
            except models.Attribute.DoesNotExist:
                editable_profile_fields.append(field)
            else:
                if attribute.user_editable:
                    editable_profile_fields.append(field)
        attributes = models.Attribute.objects.filter(user_editable=True)
        if scopes:
            scopes = set(scopes)
            default_fields = [
                attribute.name for attribute in attributes if scopes & set(attribute.scopes.split())
            ]
        else:
            default_fields = list(attributes.values_list('name', flat=True))
        fields, labels = utils_misc.get_fields_and_labels(editable_profile_fields, default_fields)
        if scopes:
            # restrict fields to those in the scopes
            fields = [field for field in fields if field in default_fields]
        return fields, labels

    @property
    def form_class(self):
        if 'scope' in self.kwargs:
            scopes = [self.kwargs['scope']]
        else:
            scopes = self.request.GET.get('scope', '').split()
        fields, labels = self.get_fields(scopes=scopes)
        authn = utils_misc.get_password_authenticator()
        # Email and identifier phone must be edited through the change email view, as they need validation
        not_directly_modifiable = ['email']
        if name := getattr(authn.phone_identifier_field, 'name', None):
            not_directly_modifiable.append(name)
        fields = [field for field in fields if field not in not_directly_modifiable]
        return profile_forms.modelform_factory(
            User, fields=fields, labels=labels, form=profile_forms.EditProfileForm
        )

    def get_object(self):
        return self.request.user

    def post(self, request, *args, **kwargs):
        if 'cancel' in request.POST:
            return utils_misc.redirect(request, self.next_url or str(self.success_url))
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        response = super().form_valid(form)
        hooks.call_hooks('event', name='edit-profile', user=self.request.user, form=form)
        self.request.journal.record('user.profile.edit', form=form)
        return response


edit_profile = decorators.setting_enabled('A2_PROFILE_CAN_EDIT_PROFILE')(
    login_required(EditProfile.as_view())
)


class EditRequired(EditProfile):
    template_names = ['authentic2/accounts_edit_required.html']

    def dispatch(self, request, *args, **kwargs):
        self.missing_attributes = request.user.get_missing_required_on_login_attributes()
        if not self.missing_attributes:
            return utils_misc.redirect(request, self.get_success_url())
        return super().dispatch(request, *args, **kwargs)

    @classmethod
    def get_fields(cls, scopes=None):
        # only show the required fields
        attribute_names = models.Attribute.objects.filter(required_on_login=True, disabled=False).values_list(
            'name', flat=True
        )

        fields, labels = utils_misc.get_fields_and_labels(attribute_names)
        return fields, labels


edit_required_profile = login_required(EditRequired.as_view())


class RecentAuthenticationMixin:
    last_authentication_max_age = 600  # 10 minutes

    def reauthenticate(self, action, message):
        methods = [event['how'] for event in utils_misc.get_authentication_events(self.request)]
        return utils_misc.login_require(
            self.request,
            token={
                'action': action,
                'message': message,
                'methods': methods,
            },
        )

    def has_recent_authentication(self):
        age = time.time() - utils_misc.last_authentication_event(request=self.request)['when']
        return age < self.last_authentication_max_age


class IdentifierChangeMixin(RecentAuthenticationMixin):
    reauthn_message = ''
    action = ''

    def can_validate_with_password(self):
        last_event = utils_misc.last_authentication_event(self.request)
        return last_event and last_event['how'] in ('email', 'phone', 'password-on-https')

    def dispatch(self, request, *args, **kwargs):
        if not self.can_validate_with_password() and not self.has_recent_authentication():
            return self.reauthenticate(
                action=self.action,
                message=self.reauthn_message,
            )
        return super().dispatch(request, *args, **kwargs)

    def has_recent_authentication(self):
        age = time.time() - utils_misc.last_authentication_event(request=self.request)['when']
        return age < self.last_authentication_max_age

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def post(self, request, *args, **kwargs):
        if 'cancel' in request.POST:
            return utils_misc.redirect(request, 'account_management')
        return super().post(request, *args, **kwargs)


class EmailChangeView(
    HomeURLMixin,
    IdentifierChangeMixin,
    cbv.SuccessUrlViewMixin,
    cbv.NextUrlViewMixin,
    cbv.TemplateNamesMixin,
    FormView,
):
    template_names = ['profiles/email_change.html', 'authentic2/change_email.html']
    title = _('Email Change')
    success_url = reverse_lazy('auth_homepage')
    reauthn_message = _('You must re-authenticate to change your email address.')
    action = 'email-change'

    def dispatch(self, request, *args, **kwargs):
        if not self.request.user.can_change_email():
            raise Http404(_('Email change not allowed'))
        return super().dispatch(request, *args, **kwargs)

    def get_form_class(self):
        if self.can_validate_with_password():
            return profile_forms.EmailChangeForm
        return profile_forms.EmailChangeFormNoPassword

    def form_valid(self, form):
        email = form.cleaned_data['email']
        utils_misc.send_email_change_email(
            self.request.user,
            email,
            request=self.request,
            next_url=self.get_success_url(),
        )
        hooks.call_hooks('event', name='change-email', user=self.request.user, email=email)
        messages.info(
            self.request,
            _(
                'Your request for changing your email is received. An email of validation was sent to you.'
                ' Please click on the link contained inside.'
            ),
        )
        logger.info('email change request')
        self.request.journal.record(
            'user.email.change.request', user=self.request.user, session=self.request.session, new_email=email
        )
        return super().form_valid(form)


email_change = login_required(EmailChangeView.as_view())


class PhoneChangeView(HomeURLMixin, IdentifierChangeMixin, cbv.TemplateNamesMixin, FormView):
    template_name = 'authentic2/change_phone.html'
    reauthn_message = _('You must re-authenticate to change your phone number.')
    action = 'phone-change'

    @property
    def title(self):
        return _('Change {phone_label} attribute used for authentication').format(
            phone_label=getattr(self.authenticator.phone_identifier_field, 'label', _('phone'))
        )

    def get_form_class(self):
        if self.can_validate_with_password():
            return profile_forms.PhoneChangeForm
        return profile_forms.PhoneChangeFormNoPassword

    def dispatch(self, *args, **kwargs):
        self.authenticator = utils_misc.get_password_authenticator()
        if not (
            self.authenticator.phone_identifier_field
            and self.authenticator.phone_identifier_field.user_editable
            and not self.authenticator.phone_identifier_field.disabled
        ):
            raise Http404(_('Phone change is not allowed.'))
        return super().dispatch(*args, **kwargs)

    def get_form_kwargs(self, **kwargs):
        kwargs = super().get_form_kwargs(**kwargs)
        kwargs['password_authenticator'] = self.authenticator
        return kwargs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['phone'] = self.request.user.phone_identifier
        return ctx

    def get_success_url(self):
        params = {}
        if next_url := getattr(self, 'next_url', None):
            params[REDIRECT_FIELD_NAME] = next_url
        return utils_misc.make_url('input_sms_code', kwargs={'token': self.code.url_token}, params=params)

    def form_valid(self, form):
        phone = form.cleaned_data.get('phone')
        if not phone:
            if not utils_misc.user_can_delete_phone_identifier(self.request.user):
                message = _("You can't delete your phone number.")
                level = messages.ERROR
            elif not self.request.user.email and not self.request.user.username:
                message = _(
                    'Please declare an email address or a username before deleting '
                    'your phone number, as it is currently your only identifier.'
                )
                level = messages.WARNING
            else:
                models.AttributeValue.objects.filter(
                    attribute=self.authenticator.phone_identifier_field,
                    content_type=ContentType.objects.get_for_model(get_user_model()),
                    object_id=self.request.user.id,
                ).delete()
                message = _('Your phone number has been deleted.')
                level = messages.INFO

            messages.add_message(
                self.request,
                level,
                message,
            )
            return utils_misc.redirect(self.request, reverse('auth_homepage'))

        self.request.session['phone'] = phone

        code_exists = models.SMSCode.objects.filter(
            kind=models.SMSCode.KIND_PHONE_CHANGE, phone=phone, expires__gt=timezone.now()
        ).exists()
        resend_key = 'phone-change-allow-sms-resend'
        if (
            app_settings.A2_SMS_CODE_EXISTS_WARNING
            and code_exists
            and not self.request.session.get(resend_key)
        ):
            self.request.session[resend_key] = True
            form.add_error(
                'phone',
                _(
                    'An SMS code has already been sent to %s. Click "Validate" again if you really want it to be'
                    ' sent again.'
                )
                % phone,
            )
            return self.form_invalid(form)
        self.request.session[resend_key] = False
        if 'next_url' in form.cleaned_data:
            self.next_url = form.cleaned_data['next_url']

        if is_ratelimited(
            self.request,
            key=sms_ratelimit_key,
            group='phone-change-sms',
            rate=self.authenticator.sms_number_ratelimit or None,
            increment=True,
        ):
            form.add_error(
                'phone',
                _(
                    'Multiple SMSs have already been sent to this number. Further attempts are blocked,'
                    ' try again later.'
                ),
            )
            return self.form_invalid(form)
        if is_ratelimited(
            self.request,
            key='ip',
            group='phone-change-sms',
            rate=self.authenticator.sms_ip_ratelimit or None,
            increment=True,
        ):
            form.add_error(
                'phone',
                _(
                    'Multiple registration attempts have already been made from this IP address. No further'
                    ' SMS will be sent for now, try again later.'
                ),
            )
            return self.form_invalid(form)

        try:
            self.code = utils_sms.send_phone_change_sms(
                phone,
                self.request.user.ou,
                user=self.request.user,
            )
        except utils_sms.SMSError:
            messages.error(
                self.request,
                _(
                    'Something went wrong while trying to send the SMS code to you. '
                    'Please contact your administrator and try again later.'
                ),
            )
            return utils_misc.redirect(self.request, reverse('auth_homepage'))

        old_phone = self.request.user.phone_identifier

        self.request.journal.record(
            'user.phone.change.request',
            user=self.request.user,
            old_phone=old_phone,
            session=self.request.session,
            new_phone=phone,
        )
        return super().form_valid(form)


phone_change = login_required(PhoneChangeView.as_view())


class PhoneVerifyView(PhoneChangeView):
    reauthn_message = _('You must re-authenticate to verify your phone number.')

    @property
    def title(self):
        return _('Verify {phone_label} attribute in order to use it for authentication').format(
            phone_label=getattr(self.authenticator.phone_identifier_field, 'label', _('phone'))
        )

    def dispatch(self, *args, **kwargs):
        if not self.request.user.phone_identifier:
            return HttpResponseForbidden(
                _(
                    'No phone number is linked to your user account, please declare a phone number '
                    'through dedicated action, it will be automatically verified upon declaration.'
                )
            )
        if self.request.user.phone_verified_on:
            return HttpResponseForbidden(
                _('The phone number declared in your user account is already verified.')
            )
        return super().dispatch(*args, **kwargs)

    def get_form_class(self):
        if self.can_validate_with_password():
            return profile_forms.PhoneVerifyForm
        return profile_forms.PhoneVerifyFormNoPassword  # pragma: no cover

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['phone_verification_only'] = True
        return ctx


phone_verify = login_required(PhoneVerifyView.as_view())


class PhoneChangeVerifyView(TemplateView):
    def get(self, request, *args, **kwargs):
        token = kwargs['token']
        authn = utils_misc.get_password_authenticator()
        user_ct = ContentType.objects.get_for_model(get_user_model())
        try:
            token = models.Token.objects.get(
                uuid=token,
                kind='phone-change',
            )
            user_pk = token.content['user']
            phone = token.content['phone']
            user = User.objects.get(pk=user_pk)
        except (models.Token.DoesNotExist, models.Token.MultipleObjectsReturned, ValueError):
            messages.error(request, _('Your phone number update request is invalid, try again'))
            return shortcuts.redirect('phone-change')
        except User.DoesNotExist:
            messages.error(
                request, _('Your phone number update request relates to an unknown user, try again')
            )
            return shortcuts.redirect('phone-change')

        try:
            with atomic():
                Lock.lock_identifier(phone)
                if app_settings.A2_PHONE_IS_UNIQUE:
                    non_unique = (
                        models.AttributeValue.objects.filter(
                            attribute=authn.phone_identifier_field,
                            content_type=user_ct,
                            object_id__isnull=False,
                            content=phone,
                        )
                        .exclude(object_id=user_pk)
                        .exists()
                    )
                    if non_unique:
                        raise ValidationError(_('This phone number is already used by another account.'))
                elif user.ou and user.ou.phone_is_unique:
                    same_phone = models.AttributeValue.objects.filter(
                        object_id=OuterRef('pk'),
                        attribute=authn.phone_identifier_field,
                        content=phone,
                    )

                    non_unique = (
                        User.objects.filter(
                            pk__in=Subquery(same_phone.values_list('object_id', flat=True)),
                            ou=user.ou,
                        )
                        .exclude(pk=user_pk)
                        .exists()
                    )
                    if non_unique:
                        raise ValidationError(
                            _(
                                'This phone number is already used by another account '
                                f'within organizational unit {user.ou}.'
                            )
                        )

                atv, dummy = models.AttributeValue.objects.get_or_create(
                    attribute=authn.phone_identifier_field,
                    content_type=user_ct,
                    object_id=user.pk,
                )
                old_phone = atv.content or ''
                atv.content = phone
                atv.save()
                user.phone_verified_on = timezone.now()
                user.save(
                    update_fields=[
                        'phone_verified_on',
                    ]
                )
                token.delete()
        except Lock.Error:
            messages.error(
                request,
                _(
                    'Something went wrong while updating your phone number. Try again later or contact your platform administrator.'
                ),
            )
            return shortcuts.redirect('phone-change')
        except ValidationError as e:
            messages.error(request, e.message)
            return shortcuts.redirect('phone-change')
        else:
            messages.info(request, _('Your phone number is now verified to be {0}.').format(phone))
            logger.info('user %s changed its phone number from "%s" to "%s"', user, old_phone, phone)
            hooks.call_hooks('event', name='change-phone-confirm', user=user, phone=phone)
            request.journal.record(
                'user.phone.change',
                user=user,
                session=request.session,
                old_phone=old_phone,
                new_phone=phone,
            )
            return shortcuts.redirect('account_management')


phone_change_verify = PhoneChangeVerifyView.as_view()


class EmailChangeVerifyView(TemplateView):
    def get(self, request, *args, **kwargs):
        next_url = reverse('account_management')
        if 'token' in request.GET:
            try:
                token = crypto.loads(
                    request.GET['token'], max_age=app_settings.A2_EMAIL_CHANGE_TOKEN_LIFETIME
                )
                user_pk = token['user_pk']
                email = token['email']
                next_url = token.get('next_url', reverse('email-change'))
                user = User.objects.get(pk=user_pk)
                non_unique = False
                if app_settings.A2_EMAIL_IS_UNIQUE:
                    non_unique = User.objects.filter(email__iexact=email).exclude(pk=user_pk).exists()
                elif user.ou and user.ou.email_is_unique:
                    non_unique = (
                        User.objects.filter(email__iexact=email, ou=user.ou).exclude(pk=user_pk).exists()
                    )
                if non_unique:
                    raise ValidationError(_('This email is already used by another account.'))
                old_email = user.email
                user.email = email
                user.set_email_verified(True, source='user')
                user.save()
                messages.info(
                    request, _('your request for changing your email for {0} is successful').format(email)
                )
                logger.info('user %s changed its email from %s to %s', user, old_email, email)
                hooks.call_hooks('event', name='change-email-confirm', user=user, email=email)
                request.journal.record(
                    'user.email.change',
                    user=user,
                    session=request.session,
                    old_email=old_email,
                    new_email=user.email,
                )
            except crypto.SignatureExpired:
                messages.error(request, _('your request for changing your email is too old, try again'))
            except crypto.BadSignature:
                messages.error(request, _('your request for changing your email is invalid, try again'))
            except ValueError:
                messages.error(
                    request, _('your request for changing your email was not on this site, try again')
                )
            except User.DoesNotExist:
                messages.error(
                    request, _('your request for changing your email is for an unknown user, try again')
                )
            except ValidationError as e:
                messages.error(request, e.message)
            else:
                return shortcuts.redirect(next_url)
        return shortcuts.redirect('email-change')


email_change_verify = EmailChangeVerifyView.as_view()


def passive_login(request, *, next_url, login_hint=None):
    '''View to use in IdP backends to implement passive login toward IdPs'''
    service = get_service(request)
    authenticators = utils_misc.get_authenticators()

    login_hint = login_hint or {}
    show_ctx = make_condition_context(request=request, login_hint=login_hint)
    if service:
        show_ctx['service_ou_slug'] = service.ou and service.ou.slug
        show_ctx['service_slug'] = service.slug
        show_ctx['service'] = service
    else:
        show_ctx['service_ou_slug'] = ''
        show_ctx['service_slug'] = ''
        show_ctx['service'] = None
    visible_authenticators = [
        authenticator
        for authenticator in authenticators
        if (
            authenticator.shown(ctx=show_ctx)
            and getattr(authenticator, 'passive_authn_supported', True)
            and getattr(authenticator, 'passive_login', None)
        )
    ]

    if not visible_authenticators:
        return None

    unique_authenticator = visible_authenticators[0]
    return unique_authenticator.passive_login(
        request,
        block_id=unique_authenticator.get_identifier(),
        next_url=next_url,
    )


@csrf_exempt
@ensure_csrf_cookie
@never_cache
def login(request, template_name='authentic2/login.html', redirect_field_name=REDIRECT_FIELD_NAME):
    """Displays the login form and handles the login action."""

    request.login_token = token = {}
    if 'token' in request.GET:
        try:
            token.update(crypto.loads(request.GET['token']))
            logger.debug('login: got token %s', token)
        except (crypto.SignatureExpired, crypto.BadSignature, ValueError):
            logger.warning('login: bad token')
    methods = token.get('methods', [])

    # redirect user to homepage if already connected, if setting
    # A2_LOGIN_REDIRECT_AUTHENTICATED_USERS_TO_HOMEPAGE is True
    if request.user.is_authenticated and app_settings.A2_LOGIN_REDIRECT_AUTHENTICATED_USERS_TO_HOMEPAGE:
        return utils_misc.redirect(request, 'auth_homepage')

    redirect_to = request.GET.get(redirect_field_name)

    if not redirect_to or ' ' in redirect_to:
        redirect_to = settings.LOGIN_REDIRECT_URL
    # Heavier security check -- redirects to http://example.com should
    # not be allowed, but things like /view/?param=http://example.com
    # should be allowed. This regex checks if there is a '//' *before* a
    # question mark.
    elif '//' in redirect_to and re.match(r'[^\?]*//', redirect_to):
        redirect_to = settings.LOGIN_REDIRECT_URL
    nonce = request.GET.get(constants.NONCE_FIELD_NAME)

    authenticators = utils_misc.get_authenticators()

    password_authenticators = [x for x in authenticators if x.slug == 'password-authenticator']
    if not password_authenticators:
        registration_open = False
    else:
        registration_open = password_authenticators[0].registration_open

    blocks = []

    registration_url = utils_misc.get_registration_url(request)

    context = {
        'cancel': app_settings.A2_LOGIN_DISPLAY_A_CANCEL_BUTTON and nonce is not None,
        'can_reset_password': app_settings.A2_USER_CAN_RESET_PASSWORD is not False,
        'registration_authorized': registration_open,
        'registration_url': registration_url,
    }

    # Cancel button
    if request.method == 'POST' and constants.CANCEL_FIELD_NAME in request.POST:
        return utils_misc.continue_to_next_url(request, params={'cancel': 1})

    # Create blocks
    for authenticator in authenticators:
        if methods and not set(authenticator.how) & set(methods):
            continue
        auth_blocks = []
        parameters = {'request': request, 'context': context}
        login_hint = set(request.session.get('login-hint', []))
        show_ctx = make_condition_context(request=request, login_hint=login_hint)
        service = get_service(request)
        if service:
            show_ctx['service_ou_slug'] = service.ou and service.ou.slug
            show_ctx['service_slug'] = service.slug
            show_ctx['service'] = service
        else:
            show_ctx['service_ou_slug'] = ''
            show_ctx['service_slug'] = ''
            show_ctx['service'] = None
        if authenticator.shown(ctx=show_ctx):
            context['block_index'] = len(blocks)
            auth_blocks.append(utils_misc.get_authenticator_method(authenticator, 'login', parameters))
        # If a login frontend method returns an HttpResponse with a status code != 200
        # this response is returned.
        for block in auth_blocks:
            if block:
                if block['status_code'] != 200:
                    return block['response']
                blocks.append(block)

    # run the only available authenticator if able to autorun
    if len(blocks) == 1:
        block = blocks[0]
        authenticator = block['authenticator']
        if hasattr(authenticator, 'autorun'):
            if 'message' in token:
                messages.info(request, token['message'])
            return authenticator.autorun(request, block_id=block.get('id'), next_url=redirect_to)

    context.update(
        {
            'blocks': collections.OrderedDict((block['id'], block) for block in blocks),
            redirect_field_name: redirect_to,
        }
    )
    if 'message' in token:
        messages.info(request, token['message'])
    return render(request, template_name, context)


def service_list(request):
    '''Compute the service list to show on user homepage'''
    return utils_misc.accumulate_from_backends(request, 'service_list')


class Homepage(cbv.TemplateNamesMixin, TemplateView):
    template_names = ['idp/homepage.html', 'authentic2/homepage.html']

    def dispatch(self, request, *args, **kwargs):
        if app_settings.A2_HOMEPAGE_URL:
            home_url = app_settings.A2_HOMEPAGE_URL
            if request.user.is_authenticated and request.user.ou and request.user.ou.home_url:
                home_url = request.user.ou.home_url
            return utils_misc.redirect(request, home_url, resolve=False)
        return login_required(super().dispatch)(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['account_management'] = 'account_management'
        ctx['authorized_services'] = service_list(self.request)
        return ctx


homepage = enable_view_restriction(Homepage.as_view())


class ProfileView(HomeURLMixin, cbv.TemplateNamesMixin, TemplateView):
    template_names = ['idp/account_management.html', 'authentic2/accounts.html']
    title = _('Your account')

    def dispatch(self, request, *args, **kwargs):
        if app_settings.A2_ACCOUNTS_URL:
            return utils_misc.redirect(request, app_settings.A2_ACCOUNTS_URL)
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        frontends = utils_misc.get_authenticators()

        request = self.request

        if request.method == 'POST':
            for frontend in frontends:
                if 'submit-%s' % frontend.get_identifier() in request.POST:
                    form = frontend.form()(data=request.POST)
                    if form.is_valid():
                        return frontend.post(request, form, None, '/profile')
        # User attributes management
        profile = []
        field_names = app_settings.A2_PROFILE_FIELDS
        if not field_names:
            field_names = list(app_settings.A2_REGISTRATION_FIELDS)
            for field_name in getattr(request.user, 'USER_PROFILE', []):
                if field_name not in field_names:
                    field_names.append(field_name)
            qs = models.Attribute.objects.filter(Q(user_editable=True) | Q(user_visible=True))
            qs = qs.values_list('name', flat=True)
            for field_name in qs:
                if field_name not in field_names:
                    field_names.append(field_name)
        attributes = []
        authenticator = utils_misc.get_password_authenticator()
        for field_name in field_names:
            title = None
            if isinstance(field_name, (list, tuple)):
                if len(field_name) > 1:
                    title = field_name[1]
                field_name = field_name[0]

            try:
                attribute = models.Attribute.objects.get(name=field_name)
            except models.Attribute.DoesNotExist:
                attribute = None

            if attribute:
                if not attribute.user_visible:
                    continue
                html_value = attribute.get_kind().get('html_value', lambda a, b: b)
                qs = models.AttributeValue.objects.with_owner(request.user)
                qs = qs.filter(attribute=attribute)
                qs = qs.select_related()
                value = [at_value.to_python() for at_value in qs]
                value = filter(None, value)
                value = [html_value(attribute, at_value) for at_value in value]
                if not title:
                    title = str(attribute)
            else:
                # fallback to model attributes
                try:
                    field = request.user._meta.get_field(field_name)
                except FieldDoesNotExist:
                    continue
                if not title:
                    title = field.verbose_name
                value = getattr(self.request.user, field_name, None)
                attribute = models.Attribute(name=field_name, label=title)

            raw_value = None
            if value:
                if callable(value):
                    value = value()
                if not isinstance(value, (list, tuple)):
                    value = (value,)
                raw_value = value
                value = [str(v) for v in value]
            if value or app_settings.A2_PROFILE_DISPLAY_EMPTY_FIELDS:
                profile.append((title, value))
                attributes.append({'attribute': attribute, 'values': raw_value})

        # Credentials management
        parameters = {'request': request, 'context': context}
        profiles = [
            utils_misc.get_authenticator_method(frontend, 'profile', parameters) for frontend in frontends
        ]
        # Old frontends data structure for templates
        blocks = [block['content'] for block in profiles if block]
        # New frontends data structure for templates
        blocks_by_id = collections.OrderedDict((block['id'], block) for block in profiles if block)

        context.update(
            {
                'phone': self.request.user.phone_identifier,
                'email': self.request.user.email,
            }
        )

        allow_phone_change = bool(
            authenticator.phone_identifier_field
            and authenticator.phone_identifier_field.user_editable
            and not authenticator.phone_identifier_field.disabled
        )
        unverified_phone = bool(
            allow_phone_change and request.user.phone_identifier and not request.user.phone_verified_on
        )
        phone_label = getattr(authenticator.phone_identifier_field, 'label', _('phone'))

        completion_ratio = None
        if app_settings.A2_ACCOUNTS_DISPLAY_COMPLETION_RATIO and (
            total_attrs := models.Attribute.objects.filter(
                disabled=False,
                user_visible=True,
                user_editable=True,
            )
        ):
            total_count = total_attrs.count()
            filled_attrs_count = (
                models.AttributeValue.objects.filter(
                    content_type=ContentType.objects.get_for_model(get_user_model()),
                    object_id=self.request.user.id,
                    attribute_id__in=total_attrs,
                    content__isnull=False,
                )
                .order_by('attribute_id')
                .distinct('attribute_id')
                .count()
            )
            completion_ratio = round(filled_attrs_count / total_count, 2)

        context.update(
            {
                'frontends_block': blocks,
                'frontends_block_by_id': blocks_by_id,
                'profile': profile,
                'attributes': attributes,
                'allow_account_deletion': app_settings.A2_REGISTRATION_CAN_DELETE_ACCOUNT,
                'allow_profile_edit': EditProfile.can_edit_profile(),
                'allow_email_change': request.user.can_change_email,
                'allow_phone_change': allow_phone_change,
                'unverified_phone': unverified_phone,
                'phone_label': phone_label,
                'allow_authorization_management': False,
                # TODO: deprecated should be removed when publik-base-theme is updated
                'allow_password_change': utils_misc.user_can_change_password(request=request),
                'completion_ratio': completion_ratio,
            }
        )

        if (
            'authentic2_idp_oidc' in settings.INSTALLED_APPS
            and app_settings.A2_PROFILE_CAN_MANAGE_SERVICE_AUTHORIZATIONS
        ):
            from authentic2_idp_oidc.models import OIDCClient

            context['allow_authorization_management'] = OIDCClient.objects.filter(
                authorization_mode=OIDCClient.AUTHORIZATION_MODE_BY_SERVICE
            ).exists()

        hooks.call_hooks('modify_context_data', self, context)
        return context


profile = enable_view_restriction(login_required(ProfileView.as_view()))


def logout_list(request):
    '''Return logout links from idp backends'''
    return utils_misc.accumulate_from_backends(request, 'logout_list')


def redirect_logout_list(request):
    '''Return redirect logout URLs from idp backends or authenticators'''
    redirect_logout_list = []
    for urls in hooks.call_hooks('redirect_logout_list', request=request):
        if urls:
            redirect_logout_list.extend(urls)
    return redirect_logout_list


def logout(request, next_url=None, do_local=True, check_referer=True):
    """Logout first check if a logout request is authorized, i.e.
    that logout was done using a POST with CSRF token or with a GET
    from the same site.

    Logout endpoints of IdP module must re-user the view by setting
    check_referer and do_local to False.
    """
    next_url = next_url or utils_misc.select_next_url(request, settings.LOGIN_REDIRECT_URL)

    cancel_url = utils_misc.select_next_url(request, field_name='cancel', default=next_url)

    if request.user.is_authenticated:
        confirm = False
        if 'confirm ' in request.GET and request.method == 'GET':
            confirm = True

        if check_referer and not utils_misc.check_referer(request):
            confirm = True

        if confirm:
            return render(
                request, 'authentic2/logout_confirm.html', {'next_url': next_url, 'cancel_url': cancel_url}
            )

        fragments = logout_list(request)
        do_local = do_local and 'local' in request.GET
        if not do_local and fragments:
            # Full logout with iframes
            local_logout_next_url = utils_misc.make_url(
                'auth_logout', params={'local': 'ok'}, next_url=next_url, sign_next_url=True
            )
            ctx = {}
            ctx['next_url'] = local_logout_next_url
            ctx['redir_timeout'] = 60
            ctx['logout_list'] = fragments
            ctx['message'] = _('Logging out from all your services')
            return render(request, 'authentic2/logout.html', ctx)
        # Get redirection targets for full logout with redirections
        # (needed before local logout)
        targets = redirect_logout_list(request)
        # Last redirection will be the current next_url
        targets.append(next_url)
        # Local logout
        request.journal.record('user.logout')
        auth_logout(request)
        if targets:
            # Full logout with redirections
            next_url = targets.pop(0)
            if targets:
                # Put redirection targets in session
                request.session['logout_redirections'] = targets
        response = shortcuts.redirect(next_url)
        response.set_cookie('a2_just_logged_out', 1, max_age=60, samesite='Lax')
        return response
    else:
        # continue redirections after logout
        targets = request.session.pop('logout_redirections', None)
        if targets:
            # Full logout with redirections
            next_url = targets.pop(0)
            request.session['logout_redirections'] = targets
        return shortcuts.redirect(next_url)


def login_password_login(request, authenticator, *args, **kwargs):
    def get_service_ous(service):
        roles = Role.objects.filter(allowed_services=service).children()
        if not roles:
            return []
        service_ou_ids = []
        qs = (
            User.objects.filter(roles__in=roles)
            .values_list('ou')
            .annotate(count=Count('ou'))
            .order_by('-count')
        )
        for ou_id, dummy_count in qs:
            if not ou_id:
                continue
            service_ou_ids.append(ou_id)
        if not service_ou_ids:
            return []
        return OU.objects.filter(pk__in=service_ou_ids)

    def get_preferred_ous(request):
        service = get_service(request)
        preferred_ous_cookie = utils_misc.get_remember_cookie(request, 'preferred-ous')
        preferred_ous = []
        if preferred_ous_cookie:
            preferred_ous.extend(OU.objects.filter(pk__in=preferred_ous_cookie))
        # for the special case of services open to only one OU, pre-select it
        if service:
            for ou in get_service_ous(service):
                if ou in preferred_ous:
                    continue
                preferred_ous.append(ou)
        return preferred_ous

    context = kwargs.get('context', {})
    is_post = request.method == 'POST' and 'login-password-submit' in request.POST
    data = request.POST if is_post else None
    initial = {}
    preferred_ous = []
    request.failed_logins = {}

    # Special handling when the form contains an OU selector
    if authenticator.include_ou_selector:
        preferred_ous = get_preferred_ous(request)
        if preferred_ous:
            initial['ou'] = preferred_ous[0]

    form = authentication_forms.AuthenticationForm(
        request=request, data=data, initial=initial, preferred_ous=preferred_ous, authenticator=authenticator
    )
    if request.user.is_authenticated and request.login_token.get('action'):
        form.initial['username'] = request.user.username or request.user.email
        form.fields['username'].widget.attrs['readonly'] = True
    if authenticator.accept_email_authentication:
        form.fields['username'].label = _('Email')
        if authenticator.is_phone_authn_active:
            form.fields['username'].label = _('Email or phone number')
    elif authenticator.is_phone_authn_active:
        form.fields['username'].label = _('Phone number')
    else:
        form.fields['username'].label = _('Username')
    if app_settings.A2_USERNAME_LABEL:
        form.fields['username'].label = app_settings.A2_USERNAME_LABEL
    is_secure = request.is_secure
    context['submit_name'] = 'login-password-submit'
    context['authenticator'] = authenticator
    if is_post:
        csrf_token_check(request, form)
        if form.is_valid():
            if is_secure:
                how = 'password-on-https'
            else:
                how = 'password'
            if form.cleaned_data.get('remember_me'):
                request.session['remember_me'] = True
                request.session.set_expiry(authenticator.remember_me)
            response = utils_misc.login(request, form.get_user(), how)
            if 'ou' in form.fields:
                utils_misc.prepend_remember_cookie(
                    request, response, 'preferred-ous', form.cleaned_data['ou'].pk
                )

            if hasattr(request, 'needs_password_change'):
                del request.needs_password_change
                return utils_misc.redirect(
                    request, 'password_change', params={'next': response.url}, resolve=True
                )

            return response
        else:
            username = form.cleaned_data.get('username') or ''
            username = username.strip()
            if request.failed_logins:
                for user, failure_data in request.failed_logins.items():
                    request.journal.record(
                        'user.login.failure',
                        authenticator=authenticator,
                        user=user,
                        reason=failure_data.get('reason', None),
                        username=username,
                    )
            elif username:
                request.journal.record('user.login.failure', authenticator=authenticator, username=username)

            if hasattr(request, 'needs_password_change'):
                del request.needs_password_change
                return utils_misc.redirect(
                    request,
                    'password_reset',
                    resolve=True,
                    params={'next': utils_misc.select_next_url(request, '')},
                )

    context['form'] = form
    return render(request, 'authentic2/login_password_form.html', context)


def login_password_profile(request, *args, **kwargs):
    context = kwargs.pop('context', {})
    can_change_password = utils_misc.user_can_change_password(request=request)
    has_usable_password = request.user.has_usable_password()
    context.update(
        {
            'can_change_password': can_change_password,
            'has_usable_password': has_usable_password,
        }
    )
    return render_to_string(
        ['auth/login_password_profile.html', 'authentic2/login_password_profile.html'],
        context,
        request=request,
    )


def csrf_failure_view(request, reason=''):
    messages.warning(request, _('The page is out of date, it was reloaded for you'))
    return HttpResponseRedirect(request.get_full_path())


class PasswordResetView(cbv.NextUrlViewMixin, FormView):
    '''Ask for an email and send a password reset link by mail'''

    form_class = passwords_forms.PasswordResetForm
    title = _('Password Reset')
    code = None

    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)
        self.authenticator = utils_misc.get_password_authenticator()

    def get_success_url(self):
        if (
            not utils_misc.get_password_authenticator().is_phone_authn_active or not self.code
        ):  # user input is email
            return reverse('password_reset_instructions')
        else:  # user input is phone number
            params = {}
            if next_url := getattr(self, 'next_url', None):
                params[REDIRECT_FIELD_NAME] = next_url
            return utils_misc.make_url('input_sms_code', kwargs={'token': self.code.url_token}, params=params)

    def get_template_names(self):
        return [
            'authentic2/password_reset_form.html',
            'registration/password_reset_form.html',
        ]

    def get_form_kwargs(self, **kwargs):
        kwargs = super().get_form_kwargs(**kwargs)
        kwargs['password_authenticator'] = self.authenticator
        return kwargs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        if app_settings.A2_USER_CAN_RESET_PASSWORD is False:
            raise Http404('Password reset is not allowed.')
        ctx['title'] = _('Password reset')
        ctx['is_phone_authn_active'] = self.authenticator.is_phone_authn_active
        return ctx

    def form_valid(self, form):
        if form.is_robot():
            return utils_misc.redirect(
                self.request,
                self.get_success_url(),
                params={
                    'robot': 'on',
                },
            )
        email_field = 'email_or_username' if app_settings.A2_USER_CAN_RESET_PASSWORD_BY_USERNAME else 'email'
        email = form.cleaned_data.get(email_field)
        phone = form.cleaned_data.get('phone')
        if phone:
            code_exists = models.SMSCode.objects.filter(
                kind=models.SMSCode.KIND_PASSWORD_LOST, phone=phone, expires__gt=timezone.now()
            ).exists()
            resend_key = 'password-reset-allow-sms-resend'
            if (
                app_settings.A2_SMS_CODE_EXISTS_WARNING
                and code_exists
                and not self.request.session.get(resend_key)
            ):
                self.request.session[resend_key] = True
                form.add_error(
                    'phone',
                    _(
                        'An SMS code has already been sent to %s. Click "Validate" again if you really want it to be'
                        ' sent again.'
                    )
                    % phone,
                )
                return self.form_invalid(form)
            self.request.session[resend_key] = False

        # if an email has already been sent, warn once before allowing resend
        # TODO handle multiple SMS warning message
        token = models.Token.objects.filter(
            kind='pw-reset', content__email__iexact=email, expires__gt=timezone.now()
        ).exists()
        resend_key = 'pw-reset-allow-resend'
        if app_settings.A2_TOKEN_EXISTS_WARNING and token and not self.request.session.get(resend_key):
            self.request.session[resend_key] = True
            form.add_error(
                email_field,
                _(
                    'An email has already been sent to %s. Click "Validate" again if you really want it to be'
                    ' sent again.'
                )
                % email,
            )
            return self.form_invalid(form)
        self.request.session[resend_key] = False
        self.request.session['phone'] = phone

        if email:
            if is_ratelimited(
                self.request,
                key='post:email',
                group='pw-reset-email',
                rate=self.authenticator.emails_address_ratelimit or None,
                increment=True,
            ):
                self.request.journal.record('user.password.reset.failure', email=email)
                form.add_error(
                    email_field,
                    _(
                        'Multiple emails have already been sent to this address. Further attempts are blocked,'
                        ' please check your spam folder or try again later.'
                    ),
                )
                return self.form_invalid(form)
            if is_ratelimited(
                self.request,
                key='ip',
                group='pw-reset-email',
                rate=self.authenticator.emails_ip_ratelimit or None,
                increment=True,
            ):
                self.request.journal.record('user.password.reset.failure', email=email)
                form.add_error(
                    email_field,
                    _(
                        'Multiple password reset attempts have already been made from this IP address. No further'
                        ' email will be sent, please check your spam folder or try again later.'
                    ),
                )
                return self.form_invalid(form)
            form.save()

        elif phone:
            if 'next_url' in form.cleaned_data:
                self.next_url = form.cleaned_data['next_url']

            if is_ratelimited(
                self.request,
                key=sms_ratelimit_key,
                group='pw-reset-sms',
                rate=self.authenticator.sms_number_ratelimit or None,
                increment=True,
            ):
                form.add_error(
                    'phone',
                    _(
                        'Multiple SMSs have already been sent to this number. Further attempts are blocked,'
                        ' try again later.'
                    ),
                )
                return self.form_invalid(form)
            if is_ratelimited(
                self.request,
                key='ip',
                group='pw-reset-sms',
                rate=self.authenticator.sms_ip_ratelimit or None,
                increment=True,
            ):
                form.add_error(
                    'email',
                    _(
                        'Multiple registration attempts have already been made from this IP address. No further'
                        ' SMS will be sent for now, try again later.'
                    ),
                )
                return self.form_invalid(form)

            self.code = form.save()
            if not self.code:
                messages.error(
                    self.request,
                    _(
                        'Something went wrong while trying to send the SMS code to you. '
                        'Please contact your administrator and try again later.'
                    ),
                )
                return utils_misc.redirect(self.request, reverse('auth_homepage'))
        if email:
            self.request.session['reset_email'] = email
        elif phone:
            self.request.session['reset_phone'] = phone
        return super().form_valid(form)


password_reset = PasswordResetView.as_view()


class PasswordResetInstructionsView(TemplateView):
    template_name = 'registration/password_reset_instructions.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['from_email_address'] = parseaddr(settings.DEFAULT_FROM_EMAIL)[1]
        return ctx


password_reset_instructions = PasswordResetInstructionsView.as_view()


class TokenLoginView(RedirectView):
    def get_redirect_url(self, *args, **kwargs):
        token = kwargs['token'].replace(' ', '')
        try:
            token = models.Token.use('login', token, delete=False)
        except models.Token.DoesNotExist:
            messages.warning(self.request, _('Login token is unknown or expired'))
            return reverse('auth_homepage')
        except (TypeError, ValueError):
            messages.warning(self.request, _('Login token is invalid'))
            return reverse('auth_homepage')

        uid = token.content['user']
        user = User.objects.get(pk=uid)
        utils_misc.simulate_authentication(self.request, user, 'token', record=True)
        return reverse('auth_homepage')


token_login = TokenLoginView.as_view()


class PasswordResetConfirmView(FormView):
    """Validate password reset link, show a set password form and login
    the user.
    """

    success_url = reverse_lazy('auth_homepage')
    form_class = passwords_forms.SetPasswordForm
    title = _('Password Reset')

    def get_template_names(self):
        return [
            'registration/password_reset_confirm.html',
            'authentic2/password_reset_confirm.html',
        ]

    def dispatch(self, request, *args, **kwargs):
        token = kwargs['token'].replace(' ', '')
        self.authenticator = utils_misc.get_password_authenticator()
        try:
            self.token = models.Token.use('pw-reset', token, delete=False)
        except models.Token.DoesNotExist:
            messages.warning(request, _('Password reset token is unknown or expired'))
            return utils_misc.redirect(request, self.get_success_url())
        except (TypeError, ValueError):
            messages.warning(request, _('Password reset token is invalid'))
            return utils_misc.redirect(request, self.get_success_url())

        uid = self.token.content['user']
        self.success_url = self.token.content.get(REDIRECT_FIELD_NAME) or self.success_url
        try:
            # use authenticate to eventually get an LDAPUser
            self.user = utils_misc.authenticate(request, user=User._default_manager.get(pk=uid))
        except (TypeError, ValueError, OverflowError, User.DoesNotExist):
            messages.warning(request, _('User not found'))
            return utils_misc.redirect(request, self.get_success_url())

        can_reset_password = utils_misc.get_user_flag(
            user=self.user, name='can_reset_password', default=self.user.has_usable_password()
        )
        if (
            can_reset_password is False
            or can_reset_password is None
            and not app_settings.A2_USER_CAN_RESET_PASSWORD
        ):
            messages.warning(
                request, _('It\'s not possible to reset your password. Please contact an administrator.')
            )
            return utils_misc.redirect(request, self.get_success_url())
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        # compatibility with existing templates !
        ctx['title'] = _('Enter new password')
        ctx['validlink'] = True
        return ctx

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.user
        return kwargs

    def form_valid(self, form):
        # Changing password by mail validate the user's known identifier
        if self.token.content.get('email'):
            form.user.set_email_verified(True, source='user')
        elif self.token.content.get('phone'):
            form.user.phone_verified_on = timezone.now()
        try:
            form.save()
        except utils_misc.PasswordChangeError as e:
            form.add_error('new_password1', e.message)
            return self.form_invalid(form)
        hooks.call_hooks('event', name='password-reset-confirm', user=form.user, token=self.token, form=form)
        logger.info('password reset for user %s with token %r', self.user, self.token.uuid)
        self.token.delete()
        utils_misc.simulate_authentication(self.request, self.user, 'email')
        self.request.journal.record('user.password.reset')
        return super().form_valid(form)


password_reset_confirm = PasswordResetConfirmView.as_view()


class BaseRegistrationView(HomeURLMixin, cbv.NextUrlViewMixin, FormView):
    form_class = registration_forms.RegistrationForm
    template_name = 'registration/registration_form.html'
    title = _('Registration')

    def dispatch(self, request, *args, **kwargs):
        self.authenticator = utils_misc.get_password_authenticator()
        if not self.authenticator.registration_open:
            raise Http404('Registration is not open.')

        self.token = {}
        self.ou = get_default_ou()

        if request.user.is_authenticated:
            # if user is currently logged, ask for logout and comme back to registration
            messages.warning(request, _('If you want to register, you need to logout first.'))
            return utils_misc.redirect_and_come_back(
                request,
                'auth_logout',
                params={'confirm': '1', 'cancel': self.next_url or reverse('auth_homepage')},
            )

        # load pre-filled values when registering with email address
        if request.GET.get('token'):
            try:
                self.token = crypto.loads(
                    request.GET.get('token'), max_age=settings.ACCOUNT_ACTIVATION_DAYS * 3600 * 24
                )
            except (TypeError, ValueError, crypto.BadSignature) as e:
                logger.warning('registration_view: invalid token: %s', e)
                return HttpResponseBadRequest('invalid token', content_type='text/plain')
            if 'ou' in self.token:
                self.ou = OU.objects.get(pk=self.token['ou'])

        self.next_url = self.token.pop(REDIRECT_FIELD_NAME, self.next_url)
        set_home_url(request, self.next_url)

        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        if form.is_robot():
            return utils_misc.redirect(
                self.request,
                'registration_complete',
                params={
                    REDIRECT_FIELD_NAME: self.next_url,
                    'robot': 'on',
                },
            )
        email = form.cleaned_data.pop('email')
        if email:
            return self.perform_email_registration(form, email)

        if self.authenticator.is_phone_authn_active:
            phone = form.cleaned_data.pop('phone')
            return self.perform_phone_registration(form, phone)

        return ValidationError(_('No means of registration provided.'))

    def perform_phone_registration(self, form, phone):
        code_exists = models.SMSCode.objects.filter(
            kind=models.SMSCode.KIND_REGISTRATION, phone=phone, expires__gt=timezone.now()
        ).exists()
        resend_key = 'registration-allow-sms-resend'
        if (
            app_settings.A2_SMS_CODE_EXISTS_WARNING
            and code_exists
            and not self.request.session.get(resend_key)
        ):
            self.request.session[resend_key] = True
            form.add_error(
                'phone',
                _(
                    'An SMS code has already been sent to %s. Click "Validate" again if you really want it to be'
                    ' sent again.'
                )
                % phone,
            )
            return self.form_invalid(form)
        self.request.session[resend_key] = False
        self.request.session['phone'] = phone

        if is_ratelimited(
            self.request,
            key=sms_ratelimit_key,
            group='registration-sms',
            rate=self.authenticator.sms_number_ratelimit,
            increment=True,
        ):
            form.add_error(
                'phone',
                _(
                    'Multiple SMSs have already been sent to this number. Further attempts are blocked,'
                    ' try again later.'
                ),
            )
            return self.form_invalid(form)
        if is_ratelimited(
            self.request,
            key='ip',
            group='registration-sms',
            rate=self.authenticator.sms_ip_ratelimit,
            increment=True,
        ):
            form.add_error(
                'email',
                _(
                    'Multiple registration attempts have already been made from this IP address. No further'
                    ' SMS will be sent for now, try again later.'
                ),
            )
            return self.form_invalid(form)
        try:
            code = send_registration_sms(phone, ou=self.ou, **self.token)
        except SMSError:
            messages.warning(
                self.request,
                _(
                    'Something went wrong while trying to send the SMS code to you.'
                    ' Please contact your administrator and try again later.'
                ),
            )
            return utils_misc.redirect(self.request, reverse('auth_homepage'))

        self.request.session['registered_phone'] = phone
        return utils_misc.redirect(
            self.request,
            reverse('input_sms_code', kwargs={'token': code.url_token}),
            params={REDIRECT_FIELD_NAME: self.next_url},
        )

    def perform_email_registration(self, form, email):
        # if an email has already been sent, warn once before allowing resend
        token = models.Token.objects.filter(
            kind='registration', content__email__iexact=email, expires__gt=timezone.now()
        ).exists()
        resend_key = 'registration-allow-email-resend'
        if app_settings.A2_TOKEN_EXISTS_WARNING and token and not self.request.session.get(resend_key):
            self.request.session[resend_key] = True
            form.add_error(
                'email',
                _(
                    'An email has already been sent to %s. Click "Validate" again if you really want it to be'
                    ' sent again.'
                )
                % email,
            )
            return self.form_invalid(form)
        self.request.session[resend_key] = False

        if is_ratelimited(
            self.request,
            key='post:email',
            group='registration-email',
            rate=self.authenticator.emails_address_ratelimit or None,
            increment=True,
        ):
            form.add_error(
                'email',
                _(
                    'Multiple emails have already been sent to this address. Further attempts are blocked,'
                    ' please check your spam folder or try again later.'
                ),
            )
            return self.form_invalid(form)
        if is_ratelimited(
            self.request,
            key='ip',
            group='registration-email',
            rate=self.authenticator.emails_ip_ratelimit or None,
            increment=True,
        ):
            form.add_error(
                'email',
                _(
                    'Multiple registration attempts have already been made from this IP address. No further'
                    ' email will be sent, please check your spam folder or try again later.'
                ),
            )
            return self.form_invalid(form)

        for field in form.cleaned_data:
            if field in app_settings.A2_PRE_REGISTRATION_FIELDS:
                self.token[field] = form.cleaned_data[field]

        self.token.pop(REDIRECT_FIELD_NAME, None)
        self.token.pop('email', None)

        utils_misc.send_registration_mail(
            self.request, email, next_url=self.next_url, ou=self.ou, **self.token
        )
        self.request.session['registered_email'] = email
        return utils_misc.redirect(
            self.request, 'registration_complete', params={REDIRECT_FIELD_NAME: self.next_url}
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        parameters = {'request': self.request, 'context': context}
        blocks = [
            utils_misc.get_authenticator_method(authenticator, 'registration', parameters)
            for authenticator in utils_misc.get_authenticators()
        ]
        context['frontends'] = collections.OrderedDict((block['id'], block) for block in blocks if block)
        return context


class InputSMSCodeView(cbv.ValidateCSRFMixin, cbv.NextUrlViewMixin, FormView):
    template_name = 'registration/sms_input_code.html'
    form_class = registration_forms.InputSMSCodeForm
    success_url = '/accounts/'
    title = _('SMS code validation')

    def dispatch(self, request, *args, **kwargs):
        token = kwargs['token']
        self.authenticator = utils_misc.get_password_authenticator()
        try:
            self.code = models.SMSCode.objects.get(url_token=token)
        except models.SMSCode.DoesNotExist:
            raise Http404(_('Invalid token'))
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['duration'] = (self.authenticator.sms_code_duration or settings.SMS_CODE_DURATION) // 60
        return ctx

    def post(self, request, *args, **kwargs):
        if 'cancel' in request.POST:
            self.code.delete()
            return utils_misc.redirect(request, reverse('auth_homepage'))
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        super().form_valid(form)
        sms_code = form.cleaned_data.pop('sms_code')
        next_url = form.cleaned_data.get('next_url')
        if self.code.value != sms_code or self.code.fake:
            # TODO ratelimit on erroneous code inputs(?)
            # (code expires after 120 seconds)
            form.add_error('sms_code', _('Wrong SMS code.'))
            return self.form_invalid(form)
        if self.code.expires < timezone.now():
            form.add_error('sms_code', _('The code has expired.'))
            return self.form_invalid(form)
        content = {
            # TODO missing ou registration management
            'authentication_method': 'phone',
            'phone': self.code.phone,
            'user': self.code.user.pk if self.code.user else None,
            REDIRECT_FIELD_NAME: next_url,
        }
        # create token to process final account activation and user-defined attributes
        token = models.Token.create(
            kind=self.code.CODE_TO_TOKEN_KINDS[self.code.kind],
            content=content,
            duration=120,
        )

        if self.code.kind == models.SMSCode.KIND_REGISTRATION:
            return utils_misc.redirect(
                self.request,
                reverse(
                    'registration_activate',
                    kwargs={'registration_token': token.uuid},
                ),
            )
        elif self.code.kind == models.SMSCode.KIND_PASSWORD_LOST:
            return utils_misc.redirect(
                self.request,
                reverse(
                    'password_reset_confirm',
                    kwargs={'token': token.uuid},
                ),
            )
        elif self.code.kind == models.SMSCode.KIND_PHONE_CHANGE:
            return utils_misc.redirect(
                self.request,
                reverse(
                    'phone-change-verify',
                    kwargs={'token': token.uuid},
                ),
            )
        elif self.code.kind == models.SMSCode.KIND_ACCOUNT_DELETION:
            return utils_misc.redirect(
                self.request,
                reverse(
                    'validate_deletion',
                    kwargs={'deletion_token': token.uuid},
                ),
            )


input_sms_code = InputSMSCodeView.as_view()


class RegistrationView(cbv.ValidateCSRFMixin, BaseRegistrationView):
    pass


class RegistrationCompletionView(CreateView):
    model = get_user_model()
    success_url = 'auth_homepage'

    def get_template_names(self):
        if self.users and 'create' not in self.request.GET:
            return ['registration/registration_completion_choose.html']
        else:
            return ['registration/registration_completion_form.html']

    def get_success_url(self):
        try:
            redirect_url, next_field = app_settings.A2_REGISTRATION_REDIRECT
        except Exception:
            redirect_url = app_settings.A2_REGISTRATION_REDIRECT
            next_field = REDIRECT_FIELD_NAME

        if self.token and self.token.get(REDIRECT_FIELD_NAME):
            url = self.token[REDIRECT_FIELD_NAME]
            if redirect_url:
                url = utils_misc.make_url(redirect_url, params={next_field: url})
        elif (next_url := self.request.GET.get(REDIRECT_FIELD_NAME)) and utils_misc.good_next_url(
            self.request, next_url
        ):
            url = next_url
        else:
            if redirect_url:
                url = redirect_url
            else:
                url = utils_misc.make_url(self.success_url)
        return url

    @atomic(savepoint=False)
    def dispatch(self, request, *args, **kwargs):
        registration_token = kwargs['registration_token'].replace(' ', '')
        self.authenticator = utils_misc.get_password_authenticator()
        user_ct = ContentType.objects.get_for_model(get_user_model())
        try:
            token = models.Token.use('registration', registration_token, delete=False)
        except models.Token.DoesNotExist:
            messages.warning(request, _('Your activation key is unknown or expired'))
            return utils_misc.redirect(request, 'registration_register')
        except (TypeError, ValueError):
            messages.warning(request, _('Activation failed'))
            return utils_misc.redirect(request, 'registration_register')
        self.token_obj = token
        self.token = token.content

        # allow access to token content from external authentication backends
        request.token = self.token

        self.authentication_method = self.token.get('authentication_method', 'email')
        if 'ou' in self.token:
            self.ou = OU.objects.get(pk=self.token['ou'])
        else:
            self.ou = get_default_ou()

        if self.token.get('email', None):
            self.email = self.token['email']
            qs_filter = {'email__iexact': self.email}
            Lock.lock_email(self.email)
        elif self.token.get('phone', None) and self.authenticator.is_phone_authn_active:
            self.phone = self.token['phone']
            user_ids = models.AttributeValue.objects.filter(
                attribute=self.authenticator.phone_identifier_field,
                content=self.phone,
                content_type=user_ct,
            ).values_list('object_id', flat=True)

            qs_filter = {'id__in': user_ids}
            Lock.lock_identifier(self.phone)
        else:
            messages.warning(request, _('Activation failed'))
            return utils_misc.redirect(request, 'registration_register')

        self.users = User.objects.filter(**qs_filter).order_by('date_joined')
        if self.ou and not app_settings.A2_EMAIL_IS_UNIQUE:
            self.users = self.users.filter(ou=self.ou)
        self.email_is_unique = self.phone_is_unique = False
        if self.token.get('email', None):
            self.email_is_unique = (
                app_settings.A2_EMAIL_IS_UNIQUE or app_settings.A2_REGISTRATION_EMAIL_IS_UNIQUE
            )
            if self.ou:
                self.email_is_unique |= self.ou.email_is_unique
        elif self.token.get('phone', None) and self.authenticator.is_phone_authn_active:
            self.phone_is_unique = (
                app_settings.A2_PHONE_IS_UNIQUE or app_settings.A2_REGISTRATION_PHONE_IS_UNIQUE
            )
            if self.ou:
                self.phone_is_unique |= self.ou.phone_is_unique
        self.init_fields_labels_and_help_texts()
        set_home_url(request, self.get_success_url())
        return super().dispatch(request, *args, **kwargs)

    def init_fields_labels_and_help_texts(self):
        attributes = models.Attribute.objects.filter(asked_on_registration=True)
        default_fields = attributes.values_list('name', flat=True)
        required_fields = models.Attribute.objects.filter(required=True).values_list('name', flat=True)
        fields, labels = utils_misc.get_fields_and_labels(
            app_settings.A2_REGISTRATION_FIELDS,
            default_fields,
            app_settings.A2_REGISTRATION_REQUIRED_FIELDS,
            app_settings.A2_REQUIRED_FIELDS,
            models.Attribute.objects.filter(required=True).values_list('name', flat=True),
        )
        help_texts = {}
        if app_settings.A2_REGISTRATION_FORM_USERNAME_LABEL:
            labels['username'] = app_settings.A2_REGISTRATION_FORM_USERNAME_LABEL
        if app_settings.A2_REGISTRATION_FORM_USERNAME_HELP_TEXT:
            help_texts['username'] = app_settings.A2_REGISTRATION_FORM_USERNAME_HELP_TEXT
        required = list(app_settings.A2_REGISTRATION_REQUIRED_FIELDS) + list(required_fields)
        # identifier fields don't belong here
        for field in ('email', 'phone'):
            if field in fields:
                fields.remove(field)
        for field in self.token.get('skip_fields') or []:
            if field in fields:
                fields.remove(field)
        self.fields = fields
        self.labels = labels
        self.required = required
        self.help_texts = help_texts

    def get_form_class(self):
        if not self.token.get('valid_email', True):
            self.fields.append('email')
            self.required.append('email')
        form_class = registration_forms.RegistrationCompletionForm
        if self.token.get('no_password', False):
            form_class = registration_forms.RegistrationCompletionFormNoPassword
        form_class = profile_forms.modelform_factory(
            self.model,
            form=form_class,
            fields=self.fields,
            labels=self.labels,
            required=self.required,
            help_texts=self.help_texts,
        )
        if 'username' in self.fields and app_settings.A2_REGISTRATION_FORM_USERNAME_REGEX:
            # Keep existing field label and help_text
            old_field = form_class.base_fields['username']
            field = CharField(
                max_length=256,
                label=old_field.label,
                help_text=old_field.help_text,
                validators=[validators.UsernameValidator()],
            )
            form_class = type('RegistrationForm', (form_class,), {'username': field})
        return form_class

    def get_form_kwargs(self, **kwargs):
        '''Initialize mail from token'''
        kwargs = super().get_form_kwargs(**kwargs)
        if 'ou' in self.token:
            ou = get_object_or_404(OU, id=self.token['ou'])
        else:
            ou = get_default_ou()

        attributes = {'ou': ou}
        if hasattr(self, 'email'):
            attributes['email'] = self.email

        for key in self.token:
            if key in app_settings.A2_PRE_REGISTRATION_FIELDS:
                attributes[key] = self.token[key]
        logger.debug('attributes %s', attributes)

        prefilling_list = utils_misc.accumulate_from_backends(self.request, 'registration_form_prefill')
        logger.debug('prefilling_list %s', prefilling_list)
        # Build a single meaningful prefilling with sets of values
        prefilling = {}
        for p in prefilling_list:
            for name, values in p.items():
                if name in self.fields:
                    prefilling.setdefault(name, set()).update(values)
        logger.debug('prefilling %s', prefilling)

        for name, values in prefilling.items():
            attributes[name] = ' '.join(values)
        logger.debug('attributes with prefilling %s', attributes)

        if self.token.get('user_id'):
            kwargs['instance'] = User.objects.get(id=self.token.get('user_id'))
        else:
            init_kwargs = {}
            keys = ['email', 'first_name', 'last_name', 'ou']
            for key in keys:
                if key in attributes:
                    init_kwargs[key] = attributes[key]
            kwargs['instance'] = get_user_model()(**init_kwargs)
            # phone identifier is a separate attribute and is set post user-creation
            if hasattr(self, 'phone'):
                kwargs['instance'].phone_verified_on = timezone.now()
            elif hasattr(self, 'email'):
                kwargs['instance'].set_email_verified(True, source='registration')

        return kwargs

    def get_form(self, form_class=None):
        form = super().get_form(form_class=form_class)
        hooks.call_hooks('front_modify_form', self, form)
        return form

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['token'] = self.token
        ctx['users'] = self.users
        if hasattr(self, 'email'):
            ctx['email'] = self.email
        if hasattr(self, 'phone'):
            ctx['phone'] = self.phone
        ctx['email_is_unique'] = self.email_is_unique
        ctx['phone_is_unique'] = self.phone_is_unique
        ctx['create'] = 'create' in self.request.GET
        return ctx

    def get(self, request, *args, **kwargs):
        if len(self.users) == 1 and (
            self.email_is_unique
            and self.token.get('email', None)
            or self.phone_is_unique
            and self.token.get('phone', None)
        ):
            messages.info(
                request, _("You've been logged in with your already-existing account for this identifier.")
            )

            # Found one user whose identifier is unique, log her in
            utils_misc.simulate_authentication(request, self.users[0], method=self.authentication_method)
            return utils_misc.redirect(request, self.get_success_url())
        confirm_data = self.token.get('confirm_data', False)

        if confirm_data == 'required':
            fields_to_confirm = self.required
        else:
            fields_to_confirm = self.fields
        if all(field in self.token for field in fields_to_confirm) and (
            not confirm_data or confirm_data == 'required'
        ):
            # We already have every fields
            form_kwargs = self.get_form_kwargs()
            form_class = self.get_form_class()
            data = self.token
            if 'password' in data:
                data['password1'] = data['password']
                data['password2'] = data['password']
                del data['password']
            form_kwargs['data'] = data
            form = form_class(**form_kwargs)
            if form.is_valid():
                user = form.save()
                self.process_registration(request, user, form)
                return self.registration_success(request, user)
            self.get_form = lambda *args, **kwargs: form
        return super().get(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        if self.users and (
            self.email_is_unique
            and self.token.get('email', None)
            or self.phone_is_unique
            and self.token.get('phone', None)
        ):
            # identifier is unique, users already exist, creating a new one is forbidden !
            return utils_misc.redirect(
                request, request.resolver_match.view_name, args=self.args, kwargs=self.kwargs
            )
        if 'uid' in request.POST:
            uid = request.POST['uid']
            for user in self.users:
                if str(user.id) == uid:
                    utils_misc.simulate_authentication(request, user, method=self.authentication_method)
                    return utils_misc.redirect(request, self.get_success_url())
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        # remove verified fields from form, this allows an authentication
        # method to provide verified data fields and to present it to the user,
        # while preventing the user to modify them.
        for av in models.AttributeValue.objects.with_owner(form.instance):
            if av.verified and av.attribute.name in form.fields:
                del form.fields[av.attribute.name]

        if (
            'email' in self.request.POST
            and ('email' not in self.token or self.request.POST['email'] != self.token['email'])
            and not self.token.get('skip_email_check')
        ):
            # If an email is submitted it must be validated or be the same as in the token
            data = form.cleaned_data.copy()
            # handle complex attributes
            for attribute in iter_attributes():
                if attribute.name not in data:
                    continue
                kind = attribute.get_kind()
                if kind['serialize'] is attribute_kinds.identity:
                    continue
                data[attribute.name] = kind['serialize'](data[attribute.name])

            data['no_password'] = self.token.get('no_password', False)
            utils_misc.send_registration_mail(
                self.request, ou=self.ou, next_url=self.get_success_url(), **data
            )
            self.token_obj.delete()
            self.request.session['registered_email'] = form.cleaned_data['email']
            return utils_misc.redirect(self.request, 'registration_complete')

        count, dummy = self.token_obj.delete()
        # prevent duplicate user creations in case several requests are processed in parallel
        if count:
            super().form_valid(form)  # user creation happens here
            user = form.instance
            if (phone := getattr(self, 'phone', None)) and self.authenticator.is_phone_authn_active:
                # phone identifier set post user-creation
                models.AttributeValue.objects.create(
                    content_type=ContentType.objects.get_for_model(get_user_model()),
                    object_id=user.id,
                    content=phone,
                    attribute=self.authenticator.phone_identifier_field,
                )
            self.process_registration(self.request, user, form)
        else:
            try:
                user = User.objects.get(email=self.email)
            except User.DoesNotExist:
                messages.warning(self.request, _('An error occured during account creation.'))
                return utils_misc.redirect(self.request, 'registration_register')

        return self.registration_success(self.request, user)

    def process_registration(self, request, user, form):
        request.journal.record('user.registration', user=user, session=None, how=self.authentication_method)
        hooks.call_hooks(
            'event',
            name='registration',
            user=user,
            form=form,
            view=self,
            authentication_method=self.authentication_method,
            token=self.token,
            service=get_service(request),
        )
        self.send_registration_success_email(user)

    def registration_success(self, request, user):
        utils_misc.simulate_authentication(request, user, method=self.authentication_method)
        message_template = loader.get_template('authentic2/registration_success_message.html')
        messages.info(self.request, message_template.render(request=request))
        return utils_misc.redirect(request, self.get_success_url())

    def send_registration_success_email(self, user):
        # user may not have a registered email by then
        if not user.email:
            return

        template_names = ['authentic2/registration_success']
        login_url = self.request.build_absolute_uri(settings.LOGIN_URL)
        utils_misc.send_templated_mail(
            user,
            template_names=template_names,
            context={
                'user': user,
                'email': user.email,
                'site': self.request.get_host(),
                'login_url': login_url,
                'method': self.authentication_method,
            },
            request=self.request,
        )


registration_completion = RegistrationCompletionView.as_view()


class AccountDeleteView(HomeURLMixin, cbv.NextUrlViewMixin, RecentAuthenticationMixin, TemplateView):
    template_name = 'authentic2/accounts_delete_request.html'
    title = _('Request account deletion')

    def dispatch(self, request, *args, **kwargs):
        self.ou = self.request.user.ou or get_default_ou()
        self.authenticator = utils_misc.get_password_authenticator()
        if not app_settings.A2_REGISTRATION_CAN_DELETE_ACCOUNT:
            return utils_misc.redirect(request, '..')
        if (
            not (self.request.user.email_verified or self.request.user.phone_verified_on)
            and not self.has_recent_authentication()
        ):
            return self.reauthenticate(
                action='account-delete', message=_('You must re-authenticate to delete your account.')
            )
        return super().dispatch(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        if 'cancel' in request.POST:
            return utils_misc.redirect(request, 'account_management')
        phone = request.user.phone_identifier
        if self.request.user.email_verified:
            utils_misc.send_account_deletion_code(self.request, self.request.user, next_url=self.next_url)
            messages.info(
                request, _('An account deletion validation email has been sent to your email address.')
            )
        elif self.request.user.phone_verified_on and phone:
            try:
                code = utils_sms.send_account_deletion_sms(phone, ou=self.ou, user=self.request.user)
            except SMSError:
                messages.warning(
                    self.request,
                    _(
                        'Something went wrong while trying to send the SMS code to you.'
                        ' Please contact your administrator and try again later.'
                    ),
                )
                return utils_misc.redirect(self.request, reverse('auth_homepage'))

            return utils_misc.redirect(
                self.request,
                reverse('input_sms_code', kwargs={'token': code.url_token}),
                params={REDIRECT_FIELD_NAME: self.next_url},
            )
        else:
            deletion_url = utils_misc.build_deletion_url(request, prompt=False, next_url=self.next_url)
            return logout(
                request,
                next_url=deletion_url,
                check_referer=False,
            )
        return utils_misc.redirect(request, 'account_management')

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['email'] = self.request.user.email
        ctx['phone'] = self.request.user.phone_identifier
        ctx['is_phone_authn_active'] = self.authenticator.is_phone_authn_active
        return ctx


class ValidateDeletionView(TemplateView):
    template_name = 'authentic2/accounts_delete_validation.html'
    title = _('Confirm account deletion')
    user = None
    prompt = True
    next_url = None

    def dispatch(self, request, *args, **kwargs):
        error = None
        self.authenticator = utils_misc.get_password_authenticator()

        try:
            deletion_token = crypto.loads(
                kwargs['deletion_token'], max_age=app_settings.A2_DELETION_REQUEST_LIFETIME
            )
            self.prompt = deletion_token.get('prompt', self.prompt)
            self.next_url = deletion_token.get('next_url', self.next_url)
            user_pk = deletion_token['user_pk']
            self.user = get_user_model().objects.get(pk=user_pk)
            # A user account wont be deactived twice
            if not self.user.is_active:
                raise ValidationError(_('This account is inactive, it cannot be deleted.'))
            logger.info('user %s confirmed the deletion of their own account', self.user)
        except crypto.SignatureExpired:
            error = _('The account deletion request is too old, try again')
        except crypto.BadSignature:
            error = _('The account deletion request is invalid, try again')
        except ValueError:
            error = _('The account deletion request was not on this site, try again')
        except ValidationError as e:
            error = e.message
        except get_user_model().DoesNotExist:
            error = _('This account has previously been deleted.')

        if error:
            # second attempt, phone-based deletion, based on models.Token usage
            token = kwargs['deletion_token'].replace(' ', '')
            if token:
                try:
                    token = models.Token.use('account-deletion', token, delete=False)
                except (models.Token.DoesNotExist, ValidationError, ValueError):
                    messages.error(request, error)
                    return utils_misc.redirect(request, 'auth_homepage')

                try:
                    self.token_phone = token.content['phone']
                    user_pk = token.content['user']
                except KeyError:
                    messages.error(
                        request,
                        _('Something went wrong while trying to delete your account. Try again later.'),
                    )
                    return utils_misc.redirect(request, 'auth_homepage')

                try:
                    self.user = User.objects.get(pk=user_pk)
                except User.DoesNotExist:
                    messages.error(
                        request,
                        _('Something went wrong while trying to delete your account. Try again later.'),
                    )
                    return utils_misc.redirect(request, 'auth_homepage')

                self.current_phone = self.user.phone_identifier
                self.prompt = False
                if not self.token_phone or self.current_phone != self.token_phone:
                    messages.error(
                        request,
                        _(
                            'Something went wrong while processing your account deletion request. Please try again.'
                        ),
                    )
                    return utils_misc.redirect(request, 'auth_homepage')
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, *args, **kwargs):
        if not self.prompt:
            return self.delete_account(request)
        return super().get(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        if 'cancel' not in request.POST:
            return self.delete_account(request)
        return utils_misc.redirect(request, self.next_url or 'auth_homepage')

    def delete_account(self, request):
        utils_misc.send_account_deletion_mail(self.request, self.user)
        logger.info('deletion of account %s performed', self.user)
        hooks.call_hooks('event', name='delete-account', user=self.user)
        request.journal.record('user.deletion', user=self.user)
        is_deleted_user_logged = self.user == request.user
        self.user.delete()
        messages.info(request, _('Deletion performed.'))
        # No real use for cancel_url or next_url here, assuming the link
        # has been received by email. We instead redirect the user to the
        # homepage.
        if is_deleted_user_logged:
            return logout(request, check_referer=False)
        return utils_misc.redirect(request, self.next_url or 'auth_homepage')

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['user'] = self.user  # Not necessarily the user in request
        return ctx


class RegistrationCompleteView(cbv.NextUrlViewMixin, TemplateView):
    template_name = 'registration/registration_complete.html'

    def get_context_data(self, **kwargs):
        kwargs['from_email'] = settings.DEFAULT_FROM_EMAIL
        kwargs['from_email_address'] = parseaddr(settings.DEFAULT_FROM_EMAIL)[1]
        return super().get_context_data(account_activation_days=settings.ACCOUNT_ACTIVATION_DAYS, **kwargs)


registration_complete = RegistrationCompleteView.as_view()


class PasswordChangeView(HomeURLMixin, DjPasswordChangeView):
    title = _('Password Change')
    do_not_call_in_templates = True
    success_url = reverse_lazy('account_management')

    def redirect(self):
        return HttpResponseRedirect(self.get_success_url())

    def dispatch(self, request, *args, **kwargs):
        if not utils_misc.user_can_change_password(request=request):
            messages.warning(request, _('Password change is forbidden'))
            return self.redirect()
        hooks.call_hooks('password_change_view', request=self.request)
        return super().dispatch(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        if 'cancel' in request.POST:
            return self.redirect()
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        hooks.call_hooks('event', name='change-password', user=self.request.user, request=self.request)
        models.PasswordReset.objects.filter(user=self.request.user).delete()
        try:
            response = super().form_valid(form)
        except utils_misc.PasswordChangeError as e:
            form.add_error('new_password1', e.message)
            return self.form_invalid(form)
        messages.info(self.request, _('Password changed'))
        self.request.journal.record('user.password.change', session=self.request.session)
        return response

    @property
    def form_class(self):
        if self.request.user.has_usable_password():
            return passwords_forms.PasswordChangeForm
        else:
            return passwords_forms.SetPasswordForm


password_change = decorators.setting_enabled('A2_REGISTRATION_CAN_CHANGE_PASSWORD')(
    PasswordChangeView.as_view()
)


class SuView(View):
    def get(self, request, uuid):
        user = utils_switch_user.resolve_token(uuid)
        if not user:
            raise Http404
        # LDAP ad-hoc behaviour
        if user.userexternalid_set.exists():
            user = utils_misc.authenticate(request, user=user)
        return utils_misc.simulate_authentication(
            request, user, 'su', next_url=reverse_lazy('account_management'), record=True
        )


su = SuView.as_view()


class Consents(HomeURLMixin, ListView):
    template_name = 'authentic2/consents.html'
    title = _('Consent Management')
    model = OIDCAuthorization
    context_object_name = 'consents'

    def get_queryset(self):
        return super().get_queryset().filter(user=self.request.user)


consents = decorators.setting_enabled('A2_PROFILE_CAN_MANAGE_SERVICE_AUTHORIZATIONS')(Consents.as_view())


class ConsentDelete(DeleteView):
    title = _('Consent Delete')
    model = OIDCAuthorization
    success_url = reverse_lazy('consents')

    def get(self, request, *args, **kwargs):
        return HttpResponseRedirect(self.success_url)

    def get_success_url(self):
        self.request.journal.record('user.service.sso.unauthorization', service=self.object.client)
        return super().get_success_url()


consent_delete = decorators.setting_enabled('A2_PROFILE_CAN_MANAGE_SERVICE_AUTHORIZATIONS')(
    ConsentDelete.as_view()
)


def old_view_redirect(request, to, message=None):
    '''Redirect old URL to new URL, eventually showing a message.'''
    if message:
        messages.info(request, message)
    return utils_misc.redirect(request, to=to)


class DisplayMessageAndContinueView(cbv.NextUrlViewMixin, TemplateView):
    template_name = 'authentic2/display_message_and_continue.html'
    next_url = reverse_lazy('account_management')

    def get(self, request, *args, **kwargs):
        self.only_info = True

        storage = messages.get_messages(request)
        if not storage:
            return utils_misc.redirect(request, self.next_url, resolve=False)

        for message in storage:
            if message.level not in (messages.INFO, messages.SUCCESS):
                # If there are warning or error messages, the intermediate page must not redirect
                # automatically but should ask for an user confirmation
                self.only_info = False
        storage.used = False
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['url'] = self.next_url
        ctx['only_info'] = self.only_info
        return ctx


display_message_and_continue = DisplayMessageAndContinueView.as_view()


@requires_csrf_token
def permission_denied(request, exception):
    if request.path.startswith('/manage/'):
        from authentic2.manager.views import permission_denied

        return permission_denied(request, exception=exception)
    return django_permission_denied(request, exception)
