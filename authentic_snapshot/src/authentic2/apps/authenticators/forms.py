# authentic2 - versatile identity manager
# Copyright (C) 2022 Entr'ouvert
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

from django import forms
from django.core.exceptions import ValidationError
from django.db.models import Max
from django.utils.timezone import now
from django.utils.translation import gettext as _

from authentic2 import app_settings
from authentic2.forms.mixins import SlugMixin
from authentic2.models import Attribute, SMSCode

from .models import BaseAuthenticator, LoginPasswordAuthenticator


class AuthenticatorsOrderForm(forms.Form):
    order = forms.CharField(widget=forms.HiddenInput)


class AuthenticatorAddForm(SlugMixin, forms.ModelForm):
    field_order = ('authenticator', 'name', 'ou')

    authenticator = forms.ChoiceField(label=_('Authenticator'))

    class Meta:
        model = BaseAuthenticator
        fields = ('name',)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.authenticators = {
            x.type: x for x in BaseAuthenticator.__subclasses__() if not x.unique or x.objects.count() < 1
        }
        self.fields['authenticator'].choices = [
            (k, v._meta.verbose_name) for k, v in self.authenticators.items()
        ]

    def clean(self):
        if self.cleaned_data['authenticator'] in ('saml', 'oidc') and not self.cleaned_data.get('name'):
            self.add_error('name', _('This field is required.'))

    def save(self):
        max_order = BaseAuthenticator.objects.aggregate(max=Max('order'))['max'] or 0

        Authenticator = self.authenticators[self.cleaned_data['authenticator']]
        self.instance = Authenticator(name=self.cleaned_data['name'], order=max_order + 1)
        return super().save()


class AuthenticatorImportForm(forms.Form):
    authenticator_json = forms.FileField(label=_('Authenticator export file'))

    def clean_authenticator_json(self):
        try:
            return json.loads(self.cleaned_data['authenticator_json'].read().decode())
        except ValueError:
            raise ValidationError(_('File is not in the expected JSON format.'))


class LoginPasswordAuthenticatorAdvancedForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields['phone_identifier_field'].choices = Attribute.objects.filter(
            disabled=False,
            multiple=False,
            kind__in=('phone_number', 'fr_phone_number'),
        ).values_list('id', 'label')

        # TODO drop temporary feature-flag app setting once phone number
        # verification is enforced everywhere in /accounts/
        if not app_settings.A2_ALLOW_PHONE_AUTHN_MANAGEMENT:
            for field in (
                'accept_email_authentication',
                'accept_phone_authentication',
                'phone_identifier_field',
                'sms_code_duration',
            ):
                del self.fields[field]

    def save(self, commit=False):
        if (
            self.cleaned_data.get('accept_phone_authentication', False)
            and 'phone_identifier_field' in self.changed_data
        ):
            SMSCode.objects.update(expires=now())
        return super().save(commit=commit)

    class Meta:
        model = LoginPasswordAuthenticator
        fields = (
            'remember_me',
            'include_ou_selector',
            'password_regex',
            'password_regex_error_msg',
            'login_exponential_retry_timeout_duration',
            'login_exponential_retry_timeout_factor',
            'login_exponential_retry_timeout_max_duration',
            'login_exponential_retry_timeout_min_duration',
            'emails_ip_ratelimit',
            'sms_ip_ratelimit',
            'emails_address_ratelimit',
            'sms_number_ratelimit',
            'accept_email_authentication',
            'accept_phone_authentication',
            'phone_identifier_field',
            'sms_code_duration',
        )


class LoginPasswordAuthenticatorEditForm(forms.ModelForm):
    class Meta:
        model = LoginPasswordAuthenticator
        exclude = (
            'name',
            'slug',
            'ou',
            'allow_user_change_email',
            'button_label',
            'button_image',
        ) + LoginPasswordAuthenticatorAdvancedForm.Meta.fields
