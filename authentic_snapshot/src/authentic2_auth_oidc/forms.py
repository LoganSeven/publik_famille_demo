# authentic2 - versatile identity manager
# Copyright (C) 2010-2022 Entr'ouvert
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

from django import forms
from django.utils.translation import gettext_lazy as _

from authentic2.forms.widgets import DatalistTextInput, SelectAttributeWidget
from authentic2.manager import fields as manager_fields

from .models import OIDCClaimMapping, OIDCProvider


class OIDCProviderEditForm(forms.ModelForm):
    class Meta:
        model = OIDCProvider
        exclude = (
            'max_auth_age',
            'passive_authn_supported',
            'a2_synchronization_supported',
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.jwkset_url:
            self.fields['jwkset_json'].disabled = True
            self.fields['jwkset_json'].help_text = _('JSON is fetched from the WebKey Set URL')
        self.old_jwkset = self.instance.jwkset_json or {}

    def save(self, commit=True):
        super().save(commit=commit)
        self.instance.log_jwkset_change(self.old_jwkset, self.instance.jwkset_json or {})


class OIDCProviderAdvancedForm(forms.ModelForm):
    class Meta:
        model = OIDCProvider
        fields = OIDCProviderEditForm.Meta.exclude


class OIDCClaimTextInput(DatalistTextInput):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # fill datalist with standard claims from
        # https://openid.net/specs/openid-connect-core-1_0.html#StandardClaims
        self.data = (
            'sub',
            'name',
            'given_name',
            'family_name',
            'nickname',
            'preferred_username',
            'profile',
            'picture',
            'website',
            'email',
            'email_verified',
            'gender',
            'birthdate',
            'zoneinfo',
            'locale',
            'phone_number',
            'phone_number_verified',
            'address',
            'updated_at',
        )
        self.name = 'list__oidcclaim-mapping-inline'
        self.attrs.update({'list': 'list__oidcclaim-mapping-inline'})


class OIDCClaimMappingForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['attribute'].widget = SelectAttributeWidget()

    class Meta:
        model = OIDCClaimMapping
        fields = [
            'claim',
            'attribute',
            'verified',
            'required',
            'idtoken_claim',
        ]
        readonly_fields = ['created', 'modified']
        widgets = {
            'claim': OIDCClaimTextInput,
        }


class OIDCRelatedObjectForm(forms.ModelForm):
    class Meta:
        exclude = ('authenticator',)
        field_classes = {'role': manager_fields.ChooseRoleField}
        widgets = {
            'claim': OIDCClaimTextInput,
            'attribute': SelectAttributeWidget,
        }
        help_texts = {
            'idtoken_claim': _(
                'The claim is retrieved from the IDToken if checked, from the UserInfo endpoint otherwise.'
            )
        }
