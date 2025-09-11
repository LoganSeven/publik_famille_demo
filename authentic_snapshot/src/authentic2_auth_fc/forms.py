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

from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from . import app_settings
from .models import PLATFORM_CHOICES, SCOPE_CHOICES, FcAccount, FcAuthenticator


class FcAuthenticatorForm(forms.ModelForm):
    scopes = forms.MultipleChoiceField(
        label=_('Scopes'),
        choices=SCOPE_CHOICES,
        widget=forms.CheckboxSelectMultiple(),
        help_text=_('These scopes will be requested in addition to openid'),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if app_settings.display_common_scopes_only:
            common_scopes = [
                'family_name',
                'given_name',
                'birthdate',
                'birthplace',
                'birthcountry',
                'gender',
                'preferred_username',
                'email',
            ]
            self.fields['scopes'].choices = [
                (scope, scope_label)
                for scope, scope_label in SCOPE_CHOICES
                if scope in common_scopes or scope in (self.instance.scopes or [])
            ]
        if not app_settings.display_email_linking_option and not self.instance.link_by_email:
            del self.fields['link_by_email']
        if not self.instance.platform == 'test':
            self.fields['platform'].choices = [
                (platform, platform_label)
                for platform, platform_label in PLATFORM_CHOICES
                if platform != 'test'
            ]

    class Meta:
        model = FcAuthenticator
        exclude = (
            'name',
            'slug',
            'ou',
            'button_description',
            'button_image',
            'button_label',
            'jwkset_json',
            'allow_user_change_email',
        )


class FcAccountSelectionForm(forms.Form):
    account = forms.ChoiceField(
        label=_('Existing accounts with the same FranceConnect identity'),
        widget=forms.RadioSelect(),
        required=True,
    )
    sub = forms.CharField(widget=forms.HiddenInput(), required=True)
    user_info = forms.CharField(widget=forms.HiddenInput(), required=False)

    def __init__(self, *args, **kwargs):
        self.accounts = kwargs.pop('accounts', FcAccount.objects.none())
        super().__init__(*args, **kwargs)

        choices = []
        help_text = _('New account creation')
        if sub := kwargs.get('initial', {}).get('sub'):
            self.sub = sub
            self.accounts = FcAccount.objects.filter(sub=self.sub, user__is_active=True)
        if self.accounts.exists():
            choices = [
                (
                    account.id,
                    _('{full_name} — {identifier} (Linked on {timestamp})').format(
                        full_name=account.user.get_full_name(),
                        identifier=account.user.email
                        or account.user.phone_identifier
                        or _('Identifier unknown'),
                        timestamp=account.created,
                    ),
                )
                for account in self.accounts
            ]
            help_text = _('Choose between existing accounts or create a new one.')

        choices.append((-1, _('Create a new account.')))

        self.fields['account'].choices = choices
        self.fields['account'].help_text = help_text

    def clean(self):
        super().clean()
        if (account := self.cleaned_data.get('account')) != '-1':
            try:
                account = FcAccount.objects.get(id=account)
            except FcAccount.DoesNotExist:
                raise ValidationError(_('Wrong account selection'))
            if account.sub != self.cleaned_data.get('sub'):
                raise ValidationError(_('Subject identifiers mismatch'))
