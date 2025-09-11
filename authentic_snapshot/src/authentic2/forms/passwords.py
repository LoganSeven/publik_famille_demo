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
from collections import OrderedDict

from django import forms
from django.contrib.auth import forms as auth_forms
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.forms import Form
from django.utils.translation import gettext_lazy as _

from authentic2.backends.ldap_backend import LDAPUser
from authentic2.journal import journal
from authentic2.passwords import validate_password
from authentic2.utils.misc import get_password_authenticator

from .. import app_settings, models, validators
from ..backends import get_user_queryset
from ..utils import hooks
from ..utils import misc as utils_misc
from ..utils import sms as utils_sms
from .fields import CheckPasswordField, NewPasswordField, PasswordField, PhoneField, ValidatedEmailField
from .honeypot import HoneypotForm
from .utils import NextUrlFormMixin

logger = logging.getLogger(__name__)


class PasswordResetForm(NextUrlFormMixin, HoneypotForm):
    email = ValidatedEmailField(label=_('Email'), required=False)

    phone = PhoneField(
        label=_('Phone number'),
        help_text=_('Your mobile phone number.'),
        required=False,
    )

    def __init__(self, *args, **kwargs):
        self.authenticator = kwargs.pop('password_authenticator')
        super().__init__(*args, **kwargs)
        self.users = []
        if app_settings.A2_USER_CAN_RESET_PASSWORD_BY_USERNAME:
            del self.fields['email']
            self.fields['email_or_username'] = forms.CharField(
                label=_('Email or username'), max_length=254, required=False
            )

        if not self.authenticator.is_phone_authn_active:
            del self.fields['phone']
            if 'email' in self.fields:
                self.fields['email'].required = True
            else:
                self.fields['email_or_username'].required = True
        elif self.authenticator.accept_email_authentication:
            # specific gadjo widget indicating that the phone is a second option
            # apart from the user's email address
            self.fields['phone'].widget.input_type = 'phone-optional'

    def clean_email(self):
        email = self.cleaned_data.get('email')
        if email:
            self.users = get_user_queryset().filter(email__iexact=email)
            return email

    def clean_email_or_username(self):
        email_or_username = self.cleaned_data.get('email_or_username')
        if email_or_username:
            self.users = get_user_queryset().filter(
                models.Q(username__iexact=email_or_username) | models.Q(email__iexact=email_or_username)
            )
            try:
                validators.email_validator(email_or_username)
            except ValidationError:
                pass
            else:
                self.cleaned_data['email'] = email_or_username
        return email_or_username

    def clean_phone(self):
        phone = self.cleaned_data.get('phone')
        user_ct = ContentType.objects.get_for_model(get_user_model())
        if phone:
            user_ids = models.AttributeValue.objects.filter(
                attribute=self.authenticator.phone_identifier_field,
                content=phone,
                content_type=user_ct,
            ).values_list('object_id', flat=True)
            self.users = get_user_queryset().filter(id__in=user_ids)
            return phone

    def clean(self):
        if self.authenticator.is_phone_authn_active:
            if (
                not self.cleaned_data.get('email')
                and not self.cleaned_data.get('email_or_username')
                and not self.cleaned_data.get('phone')
            ):
                raise ValidationError(_('Please provide a valid email address or mobile phone number.'))
        if not self.cleaned_data.get('phone') and self.users and not any(user.email for user in self.users):
            raise ValidationError(
                _('Your account has no email, you cannot ask for a password reset with your username.')
            )
        return self.cleaned_data

    def save(self):
        """
        Generates either:
        · a one-use only link for resetting password and sends to the user.
        · a code sent by SMS which the user needs to input in order to confirm password reset.
        """
        email = self.cleaned_data.get('email')
        email_or_username = self.cleaned_data.get('email_or_username')
        phone = self.cleaned_data.get('phone')

        active_users = self.users.filter(is_active=True)
        email_sent = False
        sms_sent = False

        phone_authn_active = self.authenticator.is_phone_authn_active
        for user in active_users:
            if not user.email and not (phone_authn_active and user.phone_identifier):
                logger.info(
                    'password reset failed for account "%r": account has no email nor mobile phone number',
                    user,
                )
                continue

            if user.userexternalid_set.exists():
                ldap_user = utils_misc.authenticate(user=user)  # get LDAPUser
                if isinstance(ldap_user, LDAPUser):
                    can_reset_password = utils_misc.get_user_flag(
                        user=ldap_user, name='can_reset_password', default=ldap_user.has_usable_password()
                    )
                    message = 'account is from ldap and password reset is forbidden'
                else:
                    can_reset_password = False
                    message = 'account is from ldap but it could not be retrieved'

                if not can_reset_password:
                    log_message = 'password reset failed for account "%%r": %s' % message
                    logger.info(log_message, user)
                    login_url = utils_misc.get_token_login_url(user)
                    email_sent = True
                    utils_misc.send_templated_mail(
                        user, ['authentic2/password_reset_ldap'], {'login_url': login_url}
                    )
                    continue

            # we don't set the password to a random string, as some users should not have
            # a password
            set_random_password = user.has_usable_password() and app_settings.A2_SET_RANDOM_PASSWORD_ON_RESET
            journal.record('user.password.reset.request', email=user.email, user=user)
            if email or (user.email and email_or_username):
                email_sent = True
                utils_misc.send_password_reset_mail(
                    user, set_random_password=set_random_password, next_url=self.cleaned_data.get('next_url')
                )
            elif phone:
                try:
                    sms_sent = True
                    code = utils_sms.send_password_reset_sms(
                        phone,
                        user.ou,
                        user=user,
                    )
                except utils_sms.SMSError:
                    pass
                else:
                    # all user info sending logic contained here, however the view needs to know
                    # which code was sent:
                    return code

        for user in self.users.filter(is_active=False):
            logger.info('password reset failed for user "%r": account is disabled', user)
            if email or email_or_username:
                email_sent = True
                code = utils_misc.send_templated_mail(user, ['authentic2/password_reset_refused'])
            elif phone:
                sms_sent = True
        if not email_sent and email:
            logger.info('password reset request for "%s", no user found', email)
            if self.authenticator.registration_open:
                ctx = {
                    'registration_url': utils_misc.make_url(
                        'registration_register',
                        absolute=True,
                        next_url=self.cleaned_data.get('next_url'),
                        sign_next_url=True,
                    ),
                }
            else:
                ctx = {}
            utils_misc.send_templated_mail(email, ['authentic2/password_reset_no_account'], context=ctx)
            hooks.call_hooks(
                'event', name='password-reset', email=email or email_or_username, users=active_users
            )
        elif not email_sent and not sms_sent and phone:
            try:
                code = utils_sms.send_password_reset_sms(
                    phone,
                    ou=None,
                    user=None,
                )
            except utils_sms.SMSError:
                pass
            else:
                return code


class PasswordResetMixin(Form):
    """Remove all password reset object for the current user when password is
    successfully changed."""

    def save(self, commit=True):
        ret = super().save(commit=commit)
        if commit:
            models.PasswordReset.objects.filter(user=self.user).delete()
        else:
            old_save = self.user.save

            def save(*args, **kwargs):
                ret = old_save(*args, **kwargs)
                models.PasswordReset.objects.filter(user=self.user).delete()
                return ret

            self.user.save = save
        return ret


class NotifyOfPasswordChange:
    def save(self, commit=True):
        user = super().save(commit=commit)
        authn = get_password_authenticator()
        if user.email and user.email_verified:
            ctx = {
                'user': user,
                'password': self.cleaned_data['new_password1'],
            }
            utils_misc.send_templated_mail(user, 'authentic2/password_change', ctx)
        elif authn.is_phone_authn_active and (phone := user.phone_identifier) and user.phone_verified_on:
            utils_sms.send_password_reset_confirmation_sms(phone, user.ou)
        return user


class SetPasswordForm(NotifyOfPasswordChange, PasswordResetMixin, auth_forms.SetPasswordForm):
    new_password1 = NewPasswordField(label=_('New password'))
    new_password2 = CheckPasswordField(label=_('New password confirmation'))

    def __init__(self, user, *args, **kwargs):
        super().__init__(user, *args, **kwargs)
        self.authenticator = get_password_authenticator()
        self.fields['new_password1'].min_strength = self.authenticator.min_password_strength

    def clean_new_password1(self):
        new_password1 = self.cleaned_data.get('new_password1')
        if new_password1 and self.user.check_password(new_password1):
            raise ValidationError(_('New password must differ from old password'))

        validate_password(new_password1, user=self.user, authenticator=self.authenticator)

        return new_password1


class PasswordChangeForm(NotifyOfPasswordChange, PasswordResetMixin, auth_forms.PasswordChangeForm):
    old_password = PasswordField(label=_('Old password'))
    new_password1 = NewPasswordField(label=_('New password'))
    new_password2 = CheckPasswordField(label=_('New password confirmation'))

    def __init__(self, user, *args, **kwargs):
        super().__init__(user, *args, **kwargs)
        self.authenticator = get_password_authenticator()
        self.fields['new_password1'].min_strength = self.authenticator.min_password_strength

    def clean_new_password1(self):
        new_password1 = self.cleaned_data.get('new_password1')
        old_password = self.cleaned_data.get('old_password')
        if new_password1 and new_password1 == old_password:
            raise ValidationError(_('New password must differ from old password'))

        validate_password(new_password1, user=self.user, authenticator=self.authenticator)

        return new_password1


# make old_password the first field
new_base_fields = OrderedDict()

for k in ['old_password', 'new_password1', 'new_password2']:
    new_base_fields[k] = PasswordChangeForm.base_fields[k]

for k in PasswordChangeForm.base_fields:
    if k not in ['old_password', 'new_password1', 'new_password2']:
        new_base_fields[k] = PasswordChangeForm.base_fields[k]

PasswordChangeForm.base_fields = new_base_fields
