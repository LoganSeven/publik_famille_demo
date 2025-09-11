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

import copy
import math

from django import forms
from django.contrib import messages
from django.contrib.auth import forms as auth_forms
from django.forms.widgets import Media
from django.utils import html
from django.utils.encoding import force_str
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _

from authentic2.forms.fields import PasswordField
from authentic2.utils.lazy import lazy_label

from .. import app_settings
from ..a2_rbac.models import OrganizationalUnit as OU
from ..exponential_retry_timeout import ExponentialRetryTimeout
from ..utils import misc as utils_misc


class AuthenticationForm(auth_forms.AuthenticationForm):
    username = auth_forms.UsernameField(
        required=True,
    )
    password = PasswordField(label=_('Password'))
    remember_me = forms.BooleanField(
        initial=False,
        required=False,
        label=_('Remember me'),
        help_text=_('Do not ask for authentication next time'),
    )
    ou = forms.ModelChoiceField(
        label=lazy_label(_('Organizational unit'), lambda: app_settings.A2_LOGIN_FORM_OU_SELECTOR_LABEL),
        required=True,
        queryset=OU.objects.all(),
    )

    field_order = ['username', 'password', 'ou', 'remember_me']

    def __init__(self, *args, **kwargs):
        preferred_ous = kwargs.pop('preferred_ous', [])
        self.authenticator = kwargs.pop('authenticator')

        super().__init__(*args, **kwargs)

        self.exponential_backoff = ExponentialRetryTimeout(
            key_prefix='login-exp-backoff-',
            duration=self.authenticator.login_exponential_retry_timeout_duration,
            factor=self.authenticator.login_exponential_retry_timeout_factor,
        )

        if not self.authenticator.remember_me:
            del self.fields['remember_me']

        if not self.authenticator.include_ou_selector:
            del self.fields['ou']
        else:
            if preferred_ous:
                choices = self.fields['ou'].choices
                new_choices = list(choices)[:1] + [
                    (gettext('Preferred organizational units'), [(ou.pk, ou.name) for ou in preferred_ous]),
                    (gettext('All organizational units'), list(choices)[1:]),
                ]
                self.fields['ou'].choices = new_choices

        if self.request:
            self.remote_addr = self.request.META['REMOTE_ADDR']
        else:
            self.remote_addr = '0.0.0.0'

    def exp_backoff_keys(self):
        return self.cleaned_data['username'], self.remote_addr

    def clean(self):
        username = self.cleaned_data.get('username')
        password = self.cleaned_data.get('password')

        keys = None
        if username and password:
            keys = self.exp_backoff_keys()
            seconds_to_wait = self.exponential_backoff.seconds_to_wait(*keys)
            if seconds_to_wait > self.authenticator.login_exponential_retry_timeout_min_duration:
                seconds_to_wait -= self.authenticator.login_exponential_retry_timeout_min_duration
                msg = _(
                    'You made too many login errors recently, you must wait <span'
                    ' class="js-seconds-until">%s</span> seconds to try again.'
                )
                msg = msg % int(math.ceil(seconds_to_wait))
                msg = html.mark_safe(msg)
                raise forms.ValidationError(msg)

        try:
            self.clean_authenticate()
        except Exception:
            if keys:
                self.exponential_backoff.failure(*keys)
            raise
        else:
            if keys:
                self.exponential_backoff.success(*keys)
        return self.cleaned_data

    def clean_authenticate(self):
        # copied from django.contrib.auth.forms.AuthenticationForm to add support for ou selector
        username = self.cleaned_data.get('username')
        password = self.cleaned_data.get('password')
        ou = self.cleaned_data.get('ou')

        if username is not None and password:
            self.user_cache = utils_misc.authenticate(
                self.request, username=username, password=password, ou=ou
            )
            if self.user_cache is None:
                if warnings := getattr(self.request, 'authn_warnings', []):
                    messages.warning(
                        self.request,
                        '{} {}'.format(
                            _('The following issues were met while trying to log you in:'),
                            '; '.join(warnings),
                        ),
                    )
                raise forms.ValidationError(
                    self.error_messages['invalid_login'],
                    code='invalid_login',
                    params={'username': self.username_field.verbose_name},
                )
            self.confirm_login_allowed(self.user_cache)

        return self.cleaned_data

    @property
    def media(self):
        media = super().media
        media = media + Media(js=['authentic2/js/js_seconds_until.js'])
        if self.authenticator.include_ou_selector:
            media = media + Media(js=['authentic2/js/ou_selector.js'])
        return media

    @property
    def error_messages(self):
        error_messages = copy.copy(auth_forms.AuthenticationForm.error_messages)
        invalid_login_message = [_('Incorrect login or password.')]
        if app_settings.A2_USER_CAN_RESET_PASSWORD is not False and self.authenticator.registration_open:
            invalid_login_message.append(
                _('Try again, use the forgotten password link below, or create an account.')
            )
        elif app_settings.A2_USER_CAN_RESET_PASSWORD is not False:
            invalid_login_message.append(_('Try again or use the forgotten password link below.'))
        elif self.authenticator.registration_open:
            invalid_login_message.append(_('Try again or create an account.'))
        error_messages['invalid_login'] = ' '.join([force_str(x) for x in invalid_login_message])
        return error_messages
