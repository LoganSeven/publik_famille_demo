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

from authentic2.forms.widgets import SelectAttributeWidget
from authentic2.manager import fields as manager_fields

from .models import SAMLAuthenticator


class SAMLAuthenticatorAdvancedForm(forms.ModelForm):
    class Meta:
        model = SAMLAuthenticator
        fields = (
            'metadata_cache_time',
            'metadata_http_timeout',
            'verify_ssl_certificate',
            'transient_federation_attribute',
            'realm',
            'username_template',
            'name_id_policy_format',
            'name_id_policy_allow_create',
            'force_authn',
            'add_authnrequest_next_url_extension',
            'group_attribute',
            'create_group',
            'error_url',
            'error_redirect_after_timeout',
            'authn_classref',
            'attribute_mapping',
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.instance.metadata_url:
            del self.fields['metadata_cache_time']
            del self.fields['metadata_http_timeout']

    def clean_attribute_mapping(self):
        return self.cleaned_data['attribute_mapping'] or {}


class SAMLAuthenticatorForm(forms.ModelForm):
    class Meta:
        model = SAMLAuthenticator
        exclude = SAMLAuthenticatorAdvancedForm.Meta.fields


class SAMLRelatedObjectForm(forms.ModelForm):
    class Meta:
        exclude = ('authenticator',)
        field_classes = {'role': manager_fields.ChooseRoleField}
        widgets = {'user_field': SelectAttributeWidget}
