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

import json

from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils.functional import cached_property
from django.utils.translation import gettext_lazy as _

from authentic2.a2_rbac.utils import get_default_ou
from authentic2.apps.authenticators.models import BaseAuthenticator
from authentic2.apps.journal.journal import journal
from authentic2.utils import http
from authentic2.utils.jwc import IDTokenError, parse_id_token, parse_jwkset, validate_jwkset

from . import views

PLATFORM_CHOICES = [
    ('prod', _('Production')),
    ('test', _('Integration')),
    ('tnew', _('Integration with new URLs (for version 2 only)')),
]
SCOPE_CHOICES = [
    ('given_name', _('given name (given_name)')),
    ('gender', _('gender (gender)')),
    ('birthdate', _('birthdate (birthdate)')),
    ('birthcountry', _('birthcountry (birthcountry)')),
    ('birthplace', _('birthplace (birthplace)')),
    ('family_name', _('family name (family_name)')),
    ('email', _('email (email)')),
    ('preferred_username', _('usual family name (preferred_username)')),
    ('identite_pivot', _('core identity (identite_pivot)')),
    ('profile', _('profile (profile)')),
    ('birth', _('birth profile (birth)')),
    ('rnipp_given_name', _('given name (from the RNIPP)')),
    ('rnipp_family_name', _('family name (from the RNIPP)')),
    ('rnipp_gender', _('gender (from the RNIPP)')),
    ('rnipp_birthcountry', _('birthcountry (from the RNIPP)')),
    ('rnipp_birthplace', _('birthplace (from the RNIPP)')),
    ('rnipp_birthdate', _('birthdate (from the RNIPP)')),
    ('rnipp_profile', _('profile (from the RNIPP)')),
    ('rnipp_identite_pivot', _('core identity (from the RNIPP)')),
]

SUPPORTED_VERSION_CHOICES = [
    ('1', _('Version 1 (deprecated mid-2025)')),
    ('2', _('Version 2 (requires dedicated FC service registration)')),
]

REF_URLS = {
    '1': {
        'tnew': {
            'authorize': '',
            'token': '',
            'userinfo': '',
            'logout': '',
            'jwks': '',
        },
        'test': {
            'authorize': 'https://fcp.integ01.dev-franceconnect.fr/api/v1/authorize',
            'token': 'https://fcp.integ01.dev-franceconnect.fr/api/v1/token',
            'userinfo': 'https://fcp.integ01.dev-franceconnect.fr/api/v1/userinfo',
            'logout': 'https://fcp.integ01.dev-franceconnect.fr/api/v1/logout',
            'jwks': '',
        },
        'prod': {
            'authorize': 'https://app.franceconnect.gouv.fr/api/v1/authorize',
            'token': 'https://app.franceconnect.gouv.fr/api/v1/token',
            'userinfo': 'https://app.franceconnect.gouv.fr/api/v1/userinfo',
            'logout': 'https://app.franceconnect.gouv.fr/api/v1/logout',
            'jwks': '',
        },
    },
    '2': {
        'tnew': {
            'authorize': 'https://fcp-low.sbx.dev-franceconnect.fr/api/v2/authorize',
            'token': 'https://fcp-low.sbx.dev-franceconnect.fr/api/v2/token',
            'userinfo': 'https://fcp-low.sbx.dev-franceconnect.fr/api/v2/userinfo',
            'logout': 'https://fcp-low.sbx.dev-franceconnect.fr/api/v2/session/end',
            'jwks': 'https://fcp-low.sbx.dev-franceconnect.fr/api/v2/jwks',
            'issuer': 'https://fcp-low.sbx.dev-franceconnect.fr/api/v2',
        },
        'test': {
            'authorize': 'https://fcp-low.integ01.dev-franceconnect.fr/api/v2/authorize',
            'token': 'https://fcp-low.integ01.dev-franceconnect.fr/api/v2/token',
            'userinfo': 'https://fcp-low.integ01.dev-franceconnect.fr/api/v2/userinfo',
            'logout': 'https://fcp-low.integ01.dev-franceconnect.fr/api/v2/session/end',
            'jwks': 'https://fcp-low.integ01.dev-franceconnect.fr/api/v2/jwks',
            'issuer': 'https://fcp-low.integ01.dev-franceconnect.fr/api/v2',
        },
        'prod': {
            'authorize': 'https://oidc.franceconnect.gouv.fr/api/v2/authorize',
            'token': 'https://oidc.franceconnect.gouv.fr/api/v2/token',
            'userinfo': 'https://oidc.franceconnect.gouv.fr/api/v2/userinfo',
            'logout': 'https://oidc.franceconnect.gouv.fr/api/v2/session/end',
            'jwks': 'https://oidc.franceconnect.gouv.fr/api/v2/jwks',
            'issuer': 'https://oidc.franceconnect.gouv.fr/api/v2',
        },
    },
}


def get_default_scopes():
    return ['profile', 'email']


class FcAuthenticator(BaseAuthenticator):
    platform = models.CharField(_('Platform'), default='tnew', max_length=4, choices=PLATFORM_CHOICES)
    version = models.CharField(_('Version'), max_length=4, choices=SUPPORTED_VERSION_CHOICES, default='2')
    client_id = models.CharField(('Client ID'), max_length=256)
    client_secret = models.CharField(_('Client Secret'), max_length=256)
    scopes = ArrayField(
        models.CharField(max_length=32, choices=SCOPE_CHOICES),
        verbose_name=_('Scopes'),
        default=get_default_scopes,
    )
    link_by_email = models.BooleanField(
        verbose_name=_('Link by email address'),
        default=False,
        help_text=_(
            'This legacy behaviour has been deprecated. If unchecked, this checkbox will '
            'disappear, and linking won\'t be activable again. If no specific use case on '
            'the platform requires this option, it is strongly recommended deactivating it '
            'as, generally speaking, the email address from one\'s FranceConnect identity '
            'must not be considered as trustworthy.'
        ),
    )
    jwkset_json = models.JSONField(
        verbose_name=_('JSON WebKey set'), null=True, blank=True, validators=[validate_jwkset]
    )
    supports_multiaccount = models.BooleanField(
        verbose_name=_('Supports linking FC identity to several accounts'),
        default=False,
        help_text=_(
            'Some use cases require that a unique FranceConnect user identity be linked to '
            'several user accounts (this option requires adequate FranceConnect mappings and/or '
            'compatible email-uniqueness settings). Leave unchecked if unsure.'
        ),
    )

    type = 'fc'
    how = ['france-connect']
    manager_idp_info_template_name = 'authentic2_auth_fc/idp_configuration_info.html'
    unique = True
    description_fields = [
        'show_condition',
        'platform',
        'client_id',
        'client_secret',
        'scopes',
        'link_by_email',
    ]

    class Meta:
        verbose_name = _('FranceConnect')

    @property
    def manager_form_class(self):
        from .forms import FcAuthenticatorForm

        return FcAuthenticatorForm

    def clean(self):
        if self.version == '2':
            try:
                self.refresh_jwkset_json(save=False)
                validate_jwkset(self.jwkset_json)
            except ValidationError:
                if not self.jwkset_json:
                    raise
        elif self.platform == 'tnew':
            raise ValidationError(_('New integration URLs are strictly for version 2.'))
        if self.supports_multiaccount:
            from authentic2 import app_settings as a2_app_settings

            from . import app_settings

            if (
                ('email' in self.scopes or 'profile' in self.scopes)
                and not a2_app_settings.A2_EMAIL_IS_UNIQUE
                and not (self.ou or get_default_ou()).email_is_unique
                and app_settings.user_info_mappings.get('email', {}).get('ref', '') == 'email'
            ):
                raise ValidationError(_('Multiaccount is activated yet clashes with email uniqueness.'))

    def save(self, *args, **kwargs):
        if not self.pk:
            self.order = -1
        return super().save(*args, **kwargs)

    def get_scopes_display(self):
        scope_dict = {k: v for k, v in SCOPE_CHOICES}
        return ', '.join(str(scope_dict[scope]) for scope in self.scopes if scope in scope_dict)

    @property
    def urls(self):
        return REF_URLS[self.version][self.platform]

    @property
    def authorize_url(self):
        return self.urls['authorize']

    @property
    def token_url(self):
        return self.urls['token']

    @property
    def userinfo_url(self):
        return self.urls['userinfo']

    @property
    def logout_url(self):
        return self.urls['logout']

    @property
    def jwkset_url(self):
        return self.urls['jwks']

    @property
    def issuer(self):
        return self.urls.get('issuer')

    @property
    def jwkset(self):
        if self.jwkset_json:
            try:
                return parse_jwkset(json.dumps(self.jwkset_json))
            except ValidationError:
                pass
        return None

    def load_jwkset_url(self):
        try:
            response = http.get(self.jwkset_url)
        except http.HTTPError as e:
            raise ValidationError(_('FranceConnect JWKSet URL is unreachable: %s') % e)
        return parse_jwkset(response.content).export(as_dict=True)

    def refresh_jwkset_json(self, save=True):
        if not self.jwkset_url:
            return

        old_jwkset = self.jwkset_json
        new_jwkset = self.load_jwkset_url()

        if old_jwkset == new_jwkset:
            return

        with transaction.atomic():
            self.jwkset_json = new_jwkset
            self.log_jwkset_change(old_jwkset, new_jwkset)
            if save:
                self.save(update_fields=['jwkset_json'])

    def log_jwkset_change(self, old_jwkset, new_jwkset):
        old_kids = {kid for key in (old_jwkset or dict()).get('keys', []) if (kid := key.get('kid'))}
        new_kids = {kid for key in new_jwkset.get('keys', []) if (kid := key.get('kid'))}

        if old_kids == new_kids:
            return

        journal.record(
            'provider.keyset.change',
            provider=self.name,
            new_keyset=new_kids,
            old_keyset=old_kids,
        )

    def autorun(self, request, block_id, next_url):
        return views.LoginOrLinkView.as_view(display_message_on_redirect=True)(request, next_url=next_url)

    def login(self, request, *args, **kwargs):
        return views.login(request, *args, **kwargs)

    def profile(self, request, *args, **kwargs):
        if request.user and request.user.is_external_account():
            return
        return views.profile(request, *args, **kwargs)

    def registration(self, request, *args, **kwargs):
        return views.registration(request, *args, **kwargs)

    def is_origin_for_user(self, user):
        return hasattr(user, 'fc_account')


class FcAccount(models.Model):
    created = models.DateTimeField(verbose_name=_('created'), auto_now_add=True)
    modified = models.DateTimeField(verbose_name=_('modified'), auto_now=True)
    user = models.OneToOneField(
        to=settings.AUTH_USER_MODEL,
        verbose_name=_('user'),
        related_name='fc_account',
        on_delete=models.CASCADE,
    )
    sub = models.TextField(verbose_name=_('sub'), db_index=True)
    token = models.TextField(verbose_name=_('access token'), default='{}')
    user_info = models.TextField(verbose_name=_('user info'), null=True, default='{}')

    @cached_property
    def id_token(self):
        authenticator = FcAuthenticator.objects.get()
        try:
            return parse_id_token(self.get_token()['id_token'], authenticator)
        except IDTokenError:
            return None

    def get_token(self):
        if self.token:
            return json.loads(self.token)
        else:
            return {}

    def get_user_info(self):
        if self.user_info:
            return json.loads(self.user_info)
        else:
            return {}

    def __str__(self):
        user_info = self.get_user_info()
        display_name = []
        if 'given_name' in user_info:
            display_name.append(user_info['given_name'])
        if 'family_name' in user_info:
            display_name.append(user_info['family_name'])
        return ' '.join(display_name)
