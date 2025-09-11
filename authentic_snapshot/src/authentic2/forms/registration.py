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

import re

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import BaseUserManager
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.forms import Form
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _

from authentic2.a2_rbac.models import OrganizationalUnit
from authentic2.forms.fields import CharField, CheckPasswordField, NewPasswordField
from authentic2.passwords import validate_password

from .. import app_settings, models
from ..utils import misc as utils_misc
from . import profile as profile_forms
from .fields import PhoneField, ValidatedEmailField
from .honeypot import HoneypotForm
from .utils import NextUrlFormMixin

User = get_user_model()


USUAL_FIELDS_AUTOCOMPLETE = {
    'first_name': 'given-name',
    'last_name': 'family-name',
    'address': 'address-line1',
    'zipcode': 'postal-code',
    'city': 'address-level2',
    'country': 'country',
    'phone': 'tel',
    'email': 'email',
}


class RegistrationForm(HoneypotForm):
    error_css_class = 'form-field-error'
    required_css_class = 'form-field-required'
    html5_autocomplete_map = USUAL_FIELDS_AUTOCOMPLETE

    email = ValidatedEmailField(
        label=_('Email'),
        help_text=_('Your email address (example: name@example.com)'),
        required=False,
    )

    phone = PhoneField(
        label=_('Phone number'),
        help_text=_('Your mobile phone number.'),
        required=False,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        attributes = {a.name: a for a in models.Attribute.objects.all()}

        if not utils_misc.get_password_authenticator().is_phone_authn_active:
            del self.fields['phone']
            self.fields['email'].required = True
        elif utils_misc.get_password_authenticator().accept_email_authentication:
            # specific gadjo widget indicating that the phone is a second option
            # apart from the user's email address
            self.fields['phone'].widget.input_type = 'phone-optional'

        for field in app_settings.A2_PRE_REGISTRATION_FIELDS:
            if field in ('first_name', 'last_name'):
                self.fields[field] = User._meta.get_field(field).formfield()
            elif field in attributes:
                self.fields[field] = attributes[field].get_form_field()
            self.fields[field].required = True

        for field, autocomplete_value in self.html5_autocomplete_map.items():
            if field in self.fields:
                self.fields[field].widget.attrs['autocomplete'] = autocomplete_value

    def clean_email(self):
        email = self.cleaned_data['email']

        authenticator = utils_misc.get_password_authenticator()
        for email_domain in authenticator.registration_forbidden_email_domains:
            if not email_domain.startswith('@'):
                email_domain = '@' + email_domain
            if email.endswith(email_domain):
                raise ValidationError(gettext('You cannot register with this email.'))

        for email_pattern in app_settings.A2_REGISTRATION_EMAIL_BLACKLIST:
            if not email_pattern.startswith('^'):
                email_pattern = '^' + email_pattern
            if not email_pattern.endswith('$'):
                email_pattern += '$'
            if re.match(email_pattern, email):
                raise ValidationError(gettext('You cannot register with this email.'))
        return email

    def clean(self):
        if utils_misc.get_password_authenticator().is_phone_authn_active:
            if not self.cleaned_data.get('email') and not self.cleaned_data.get('phone'):
                raise ValidationError(gettext('Please provide an email address or a mobile phone number.'))


validate_name = RegexValidator(
    r'[0-9_!¡?÷?¿/\\+=@#$%ˆ&*(){}|~<>;:[\]]',
    message=_('Special characters are not allowed.'),
    inverse_match=True,
)


class RegistrationCompletionFormNoPassword(profile_forms.BaseUserForm):
    error_css_class = 'form-field-error'
    required_css_class = 'form-field-required'
    html5_autocomplete_map = USUAL_FIELDS_AUTOCOMPLETE

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if 'first_name' in self.fields:
            self.fields['first_name'].validators.append(validate_name)
        if 'last_name' in self.fields:
            self.fields['last_name'].validators.append(validate_name)
        for field, autocomplete_value in self.html5_autocomplete_map.items():
            if field in self.fields:
                self.fields[field].widget.attrs['autocomplete'] = autocomplete_value

    def clean_username(self):
        if self.cleaned_data.get('username'):
            username = self.cleaned_data['username']
            username_is_unique = app_settings.A2_REGISTRATION_USERNAME_IS_UNIQUE
            if app_settings.A2_REGISTRATION_REALM:
                username += '@' + app_settings.A2_REGISTRATION_REALM
            if 'ou' in self.data:
                ou = OrganizationalUnit.objects.get(pk=self.data['ou'])
                username_is_unique |= ou.username_is_unique
            if username_is_unique:
                exist = False
                try:
                    User.objects.get(username=username)
                except User.DoesNotExist:
                    pass
                except User.MultipleObjectsReturned:
                    exist = True
                else:
                    exist = True
                if exist:
                    raise ValidationError(
                        _('This username is already in use. Please supply a different username.')
                    )
            return username

    def clean_email(self):
        if self.cleaned_data.get('email'):
            email = self.cleaned_data['email']
            if app_settings.A2_REGISTRATION_EMAIL_IS_UNIQUE:
                exist = False
                try:
                    User.objects.get(email__iexact=email)
                except User.DoesNotExist:
                    pass
                except User.MultipleObjectsReturned:
                    exist = True
                else:
                    exist = True
                if exist:
                    raise ValidationError(
                        _('This email address is already in use. Please supply a different email address.')
                    )
            return BaseUserManager.normalize_email(email)

    def save(self, commit=True):
        self.instance.is_active = True
        return super().save(commit=commit)


class RegistrationCompletionForm(RegistrationCompletionFormNoPassword):
    password1 = NewPasswordField(label=_('Password'))
    password2 = CheckPasswordField(label=_('Password (again)'))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.authenticator = utils_misc.get_password_authenticator()
        self.fields['password1'].min_strength = self.authenticator.min_password_strength

        self.strength_input_attributes = set()
        for attribute in models.Attribute.objects.all():
            kind = attribute.get_kind()
            if issubclass(kind.get('field_class'), CharField):
                self.strength_input_attributes.add(attribute.name)

        for name, field in self.fields.items():
            if name in self.strength_input_attributes:
                field.widget.attrs['data-password-strength-input'] = True

    def clean_password1(self):
        password = self.cleaned_data['password1']

        inputs = {k: v for k, v in self.cleaned_data.items() if k in self.strength_input_attributes}
        validate_password(password, user=self.instance, inputs=inputs, authenticator=self.authenticator)
        return password

    def clean(self):
        """
        Verifiy that the values entered into the two password fields
        match. Note that an error here will end up in
        ``non_field_errors()`` because it doesn't apply to a single
        field.
        """
        if 'password1' in self.cleaned_data and 'password2' in self.cleaned_data:
            if self.cleaned_data['password1'] != self.cleaned_data['password2']:
                raise ValidationError(_("The two password fields didn't match."))
            self.instance.set_password(self.cleaned_data['password1'])
        return self.cleaned_data


class InputSMSCodeForm(NextUrlFormMixin, Form):
    sms_code = CharField(
        label=_('SMS code'),
        help_text=_('The code you received by SMS.'),
        max_length=settings.SMS_CODE_LENGTH,
    )
