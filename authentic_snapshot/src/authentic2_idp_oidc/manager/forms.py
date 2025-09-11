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
from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.template.defaultfilters import mark_safe
from django.urls import reverse
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from authentic2.attributes_ng.engine import get_service_attributes
from authentic2.forms.mixins import SlugMixin
from authentic2.forms.widgets import DatalistTextInput
from authentic2.middleware import StoreRequestMiddleware
from authentic2_idp_oidc.models import OIDCClaim, OIDCClient
from authentic2_idp_oidc.utils import url_domain


class OIDCClientForm(SlugMixin, forms.ModelForm):
    class Meta:
        model = OIDCClient
        fields = [
            'name',
            'slug',
            'redirect_uris',
            'post_logout_redirect_uris',
            'sector_identifier_uri',
            'frontchannel_logout_uri',
            'ou',
            'identifier_policy',
            'idtoken_algo',
            'unauthorized_url',
            'authorization_mode',
            'client_id',
            'client_secret',
            'always_save_authorization',
            'authorization_default_duration',
            'authorization_flow',
            'home_url',
            'colour',
            'logo',
            'has_api_access',
            'activate_user_profiles',
            'pkce_code_challenge',
            'uses_refresh_tokens',
        ]
        labels = {
            'has_api_access': _("Has access to Authentic's synchronization API"),
            'activate_user_profiles': _('Activates user profiles selection'),
        }
        widgets = {
            'colour': forms.TextInput(attrs={'type': 'color'}),
            'client_secret': forms.TextInput(attrs={'readonly': 'readonly'}),
            'client_id': forms.TextInput(attrs={'readonly': 'readonly'}),
        }

    def __init__(self, *args, **kwargs):
        user = kwargs.pop('user')
        super().__init__(*args, **kwargs)
        for fieldname in ('client_secret', 'client_id'):
            if fieldname in self.fields:
                self.fields[fieldname].help_text = format_html(
                    '<a href="{}" class="generate-input-value" aria-controls="id_{}">{}</a>',
                    mark_safe(reverse('a2-manager-service-generate-uuid')),
                    fieldname,
                    _('Generate'),
                )
        # hide internal functionalities from regular administrators
        if not (user and isinstance(user, get_user_model()) and user.is_superuser):
            del self.fields['has_api_access']
            del self.fields['activate_user_profiles']

    def clean(self):
        authz_mode = self.cleaned_data['authorization_mode']
        save_authz = self.cleaned_data['always_save_authorization']

        if authz_mode == OIDCClient.AUTHORIZATION_MODE_NONE and save_authz:
            self.add_error(
                'always_save_authorization',
                _('Cannot save user authorizations when authorization mode is none.'),
            )

        redirect_uris = self.cleaned_data['redirect_uris']
        sector_identifier_uri = self.cleaned_data.get('sector_identifier_uri', '')
        if not sector_identifier_uri:
            if len({url_domain(uri) for uri in filter(None, redirect_uris.split())}) > 1:
                self.add_error(
                    'redirect_uris',
                    _(
                        'Cannot save redirect URIs bearing different domains '
                        'if no sector identifier URI is provided.'
                    ),
                )
        obj = self.instance
        policy = self.cleaned_data['identifier_policy']
        policies = (obj.POLICY_PAIRWISE, obj.POLICY_PAIRWISE_REVERSIBLE)
        if 'sector_identifier_uri' in self.changed_data and policy in policies:
            old_value = obj.get_sector_identifier()
            if old_value is not None:
                # we compare the new value to the old value's logic
                if url_domain(sector_identifier_uri) != old_value:
                    self.add_error(
                        'sector_identifier_uri',
                        _(
                            'You are not allowed to set an URI that does not match "{sector_identifier_uri}" '
                            'because this value is used by the identifier policy.'
                        ).format(sector_identifier_uri=old_value),
                    )
        for fieldname in ('client_id', 'client_secret'):
            if fieldname in self.changed_data:
                if len(self.cleaned_data[fieldname]) < 36:
                    self.add_error(
                        fieldname,
                        _('Please use the generate link to change %(fieldname)s') % {'fieldname': fieldname},
                    )

        return super().clean()


class OIDCClientAddForm(OIDCClientForm):
    class Meta:
        exclude = ['slug', 'client_id', 'client_secret']


class OIDCClaimForm(forms.ModelForm):
    class Meta:
        model = OIDCClaim
        fields = ('name', 'value', 'scopes')
        widgets = {
            'value': DatalistTextInput,
        }

    def clean_name(self):
        name = self.cleaned_data['name']  # name is now a mandatory field
        request = StoreRequestMiddleware.get_request()
        client = OIDCClient.objects.get(pk=request.resolver_match.kwargs['service_pk'])
        errmsg = _('This claim name is already defined for this client. Pick another claim name.')
        try:
            claim = OIDCClaim.objects.get(client=client, name=name)
        except OIDCClaim.DoesNotExist:
            pass
        except OIDCClaim.MultipleObjectsReturned:
            raise ValidationError(errmsg)
        else:
            if self.instance != claim:
                raise ValidationError(errmsg)
        return name

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        data = dict(get_service_attributes(getattr(self.instance, 'client', None))).keys()
        for field in ('name', 'value', 'scopes'):
            self.fields[field].required = True
        self.fields['value'].help_text = _(
            'Use “⇩” (arrow down) for pre-defined claim values from the user profile.'
        )
        widget = self.fields['value'].widget
        widget.data = data
        widget.name = 'list__oidcclaim-inline'
        widget.attrs.update({'list': 'list__oidcclaim-inline'})
