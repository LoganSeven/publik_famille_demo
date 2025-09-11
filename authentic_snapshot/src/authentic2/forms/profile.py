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

import phonenumbers
from django import forms
from django.forms.models import modelform_factory as dj_modelform_factory
from django.utils.translation import gettext_lazy as _

from authentic2 import app_settings, models
from authentic2.custom_user.models import User

from .fields import PhoneField, ValidatedEmailField
from .mixins import LockedFieldFormMixin
from .utils import NextUrlFormMixin
from .widgets import HiddenPhoneInput


class EmailChangeFormNoPassword(NextUrlFormMixin, forms.Form):
    email = ValidatedEmailField(label=_('New email'))

    def __init__(self, user, *args, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)


class EmailChangeForm(EmailChangeFormNoPassword):
    password = forms.CharField(label=_('Password'), widget=forms.PasswordInput)

    def clean_email(self):
        email = self.cleaned_data['email']
        if email == self.user.email:
            raise forms.ValidationError(_('This is already your email address.'))
        return email

    def clean_password(self):
        password = self.cleaned_data['password']
        if not self.user.check_password(password):
            raise forms.ValidationError(
                _('Incorrect password.'),
                code='password_incorrect',
            )
        return password


class PhoneChangeFormNoPassword(forms.Form):
    phone = PhoneField(label=_('New phone number'), required=False)

    def __init__(self, user, *args, **kwargs):
        self.user = user
        self.authenticator = kwargs.pop('password_authenticator')
        super().__init__(*args, **kwargs)


class PhoneChangeForm(PhoneChangeFormNoPassword):
    password = forms.CharField(label=_('Password'), widget=forms.PasswordInput)

    def clean_phone(self):
        phone = self.cleaned_data['phone']
        if (
            models.AttributeValue.objects.with_owner(self.user)
            .filter(attribute=self.authenticator.phone_identifier_field, content=phone)
            .exists()
        ):
            raise forms.ValidationError(_('This is already your phone.'))
        return phone

    def clean_password(self):
        password = self.cleaned_data['password']
        if not self.user.check_password(password):
            raise forms.ValidationError(  # pragma: no cover
                _('Incorrect password.'),
                code='password_incorrect',
            )
        return password


class PhoneVerifyFormNoPassword(PhoneChangeFormNoPassword):
    phone = PhoneField(required=False, widget=HiddenPhoneInput, disabled=True)

    def __init__(self, user, *args, **kwargs):
        super().__init__(user, *args, **kwargs)
        phone = getattr(user, 'phone_identifier', None)
        if phone:
            try:
                pn = phonenumbers.parse(phone)
            except phonenumbers.NumberParseException:
                return
            self.fields['phone'].initial = phonenumbers.format_number(pn, phonenumbers.PhoneNumberFormat.E164)


class PhoneVerifyForm(PhoneVerifyFormNoPassword):
    password = forms.CharField(label=_('Password'), widget=forms.PasswordInput)

    def clean_password(self):
        password = self.cleaned_data['password']
        if not self.user.check_password(password):
            raise forms.ValidationError(
                _('Incorrect password.'),
                code='password_incorrect',
            )
        return password


class BaseUserForm(LockedFieldFormMixin, forms.ModelForm):
    error_messages = {
        'duplicate_username': _('A user with that username already exists.'),
    }

    def __init__(self, *args, **kwargs):
        self.attributes = models.Attribute.objects.all()
        initial = kwargs.setdefault('initial', {})
        instance = kwargs.get('instance')
        # extended attributes are not model fields, their initial value must be
        # explicitely defined
        self.atvs = []
        self.locked_fields = set()
        if instance:
            for attribute in self.attributes:
                kind = attribute.get_kind()
                if kind.get('default'):
                    if attribute.name in self.declared_fields:
                        initial[attribute.name] = (
                            kind['default']() if callable(kind['default']) else kind['default']
                        )
            self.atvs = models.AttributeValue.objects.select_related('attribute').with_owner(instance)
            for atv in self.atvs:
                name = atv.attribute.name
                if name in self.declared_fields:
                    initial[name] = atv.to_python()
                # helper data for LockedFieldFormMixin
                if atv.verified:
                    self.locked_fields.add(name)
        super().__init__(*args, **kwargs)

    def is_field_locked(self, name):
        # helper method for LockedFieldFormMixin
        return name in self.locked_fields

    def save_attributes(self):
        # only save non verified attributes here
        verified_attributes = set(
            self.instance.attribute_values.filter(verified=True).values_list('attribute__name', flat=True)
        )
        for attribute in self.attributes:
            name = attribute.name
            if name in self.fields and name not in verified_attributes:
                value = self.cleaned_data[name]
                setattr(self.instance.attributes, name, value)

    def save(self, commit=True):
        result = super().save(commit=commit)
        if commit:
            self.save_attributes()
        else:
            old = self.save_m2m

            def save_m2m(*args, **kwargs):
                old(*args, **kwargs)
                self.save_attributes()

            self.save_m2m = save_m2m
        return result


class EditProfileForm(NextUrlFormMixin, BaseUserForm):
    pass


def modelform_factory(model, **kwargs):
    """Build a modelform for the given model,

    For the user model also add attribute based fields.
    """

    form = kwargs.pop('form', None)
    fields = kwargs.get('fields') or []
    required = list(kwargs.pop('required', []) or [])
    d = {}
    # KV attributes are only supported for the user model currently
    modelform = None
    if issubclass(model, User):
        if not form:
            form = BaseUserForm
        attributes = models.Attribute.objects.all()
        for attribute in attributes:
            if attribute.name not in fields:
                continue
            d[attribute.name] = attribute.get_form_field()
        for field in app_settings.A2_REQUIRED_FIELDS:
            if field not in required:
                required.append(field)
    if not form or not hasattr(form, 'Meta'):
        meta_d = {'model': model, 'fields': '__all__'}
        meta = type('Meta', (), meta_d)
        d['Meta'] = meta
    if not form:  # fallback
        form = forms.ModelForm
    modelform = None
    if required:

        def __init__(self, *args, **kwargs):
            super(modelform, self).__init__(*args, **kwargs)
            for field in required:
                if field in self.fields:
                    self.fields[field].required = True

        d['__init__'] = __init__
    modelform = type(model.__name__ + 'ModelForm', (form,), d)
    kwargs['form'] = modelform
    modelform.required_css_class = 'form-field-required'
    return dj_modelform_factory(model, **kwargs)
