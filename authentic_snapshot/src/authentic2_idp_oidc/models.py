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
import urllib.parse
import uuid
from importlib import import_module

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey, GenericRelation
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.core.validators import URLValidator
from django.db import models
from django.utils.timezone import now
from django.utils.translation import gettext_lazy as _
from django.utils.translation import pgettext_lazy

from authentic2.a2_rbac.models import OrganizationalUnit
from authentic2.custom_user.models import Profile
from authentic2.models import Service

from . import app_settings, managers, utils


class RedirectURLValidator(URLValidator):
    schemes = ['https', 'http']
    custom_link_re = re.compile(r'^[a-zA-Z][a-zA-Z0-9_-]*(\.[a-zA-Z][a-zA-Z0-9_-]*)+:/')

    def __call__(self, value):
        if isinstance(value, str) and self.custom_link_re.match(value):
            # Allow custom URL schemes, see https://datatracker.ietf.org/doc/html/rfc8252#section-7
            try:
                url_splitted = urllib.parse.urlsplit(value)
                if url_splitted.netloc:
                    raise ValidationError(_('Netloc is not permitted in a custom scheme URL'))
                return
            except Exception:
                pass
        super().__call__(value)


def generate_uuid():
    return str(uuid.uuid4())


def validate_redirect_url(data):
    errors = []
    data = data.strip()
    if not data:
        return
    for url in data.split():
        try:
            RedirectURLValidator()(url)
        except ValidationError as e:
            errors.append(e)
    if errors:
        raise ValidationError(errors)


def strip_words(data):
    return '\n'.join([url for url in data.split()])


class OIDCClient(Service):
    POLICY_UUID = 1
    POLICY_PAIRWISE = 2
    POLICY_EMAIL = 3
    POLICY_PAIRWISE_REVERSIBLE = 4

    IDENTIFIER_POLICIES = [
        (POLICY_UUID, _('uuid')),
        (POLICY_PAIRWISE, _('pairwise unreversible')),
        (POLICY_PAIRWISE_REVERSIBLE, _('pairwise reversible')),
        (POLICY_EMAIL, _('email')),
    ]

    ALGO_RSA = 1
    ALGO_HMAC = 2
    ALGO_EC = 3
    ALGO_CHOICES = [
        (ALGO_HMAC, _('HMAC')),
        (ALGO_RSA, _('RSA')),
        (ALGO_EC, _('EC')),
    ]
    FLOW_AUTHORIZATION_CODE = 1
    FLOW_IMPLICIT = 2
    FLOW_RESOURCE_OWNER_CRED = 3
    FLOW_CHOICES = [
        (FLOW_AUTHORIZATION_CODE, _('authorization code')),
        (FLOW_IMPLICIT, _('implicit/native')),
        (FLOW_RESOURCE_OWNER_CRED, _('resource owner password credentials')),
    ]

    AUTHORIZATION_MODE_BY_SERVICE = 1
    AUTHORIZATION_MODE_BY_OU = 2
    AUTHORIZATION_MODE_NONE = 3
    AUTHORIZATION_MODES = [
        (AUTHORIZATION_MODE_BY_SERVICE, _('authorization by service')),
        (AUTHORIZATION_MODE_BY_OU, _('authorization by ou')),
        (AUTHORIZATION_MODE_NONE, _('none')),
    ]

    client_id = models.CharField(
        max_length=255, verbose_name=_('client id'), unique=True, default=generate_uuid
    )
    client_secret = models.CharField(max_length=255, verbose_name=_('client secret'), default=generate_uuid)
    idtoken_duration = models.DurationField(
        verbose_name=_('time during which the token is valid'), blank=True, null=True, default=None
    )
    access_token_duration = models.DurationField(
        verbose_name=_('time during which the access token is valid'), blank=True, null=True, default=None
    )
    authorization_mode = models.PositiveIntegerField(
        default=AUTHORIZATION_MODE_BY_SERVICE,
        choices=AUTHORIZATION_MODES,
        verbose_name=_('authorization mode'),
    )
    authorization_flow = models.PositiveIntegerField(
        verbose_name=_('authorization flow'), default=FLOW_AUTHORIZATION_CODE, choices=FLOW_CHOICES
    )
    always_save_authorization = models.BooleanField(
        verbose_name=_('always save authorization'),
        default=False,
        help_text=_('do not display the “do not ask again” choice'),
    )
    authorization_default_duration = models.PositiveIntegerField(
        verbose_name=_('duration of saved authorization (in days)'),
        default=0,
        help_text=_('0 for default value (one year)'),
    )
    redirect_uris = models.TextField(verbose_name=_('redirect URIs'), validators=[validate_redirect_url])
    post_logout_redirect_uris = models.TextField(
        verbose_name=_('post logout redirect URIs'),
        blank=True,
        default='',
        validators=[validate_redirect_url],
    )
    sector_identifier_uri = models.URLField(verbose_name=_('sector identifier URI'), blank=True)
    identifier_policy = models.PositiveIntegerField(
        verbose_name=_('identifier policy'), default=POLICY_PAIRWISE, choices=IDENTIFIER_POLICIES
    )
    scope = models.TextField(
        verbose_name=_('resource owner credentials grant scope'),
        help_text=_('Permitted or default scopes (for credentials grant)'),
        default='',
        blank=True,
    )
    idtoken_algo = models.PositiveIntegerField(
        default=ALGO_HMAC, choices=ALGO_CHOICES, verbose_name=_('IDToken signature algorithm')
    )
    has_api_access = models.BooleanField(verbose_name=_('has API access'), default=False)

    activate_user_profiles = models.BooleanField(
        verbose_name=_("activate users' juridical entity profiles management"), blank=True, default=False
    )

    frontchannel_logout_uri = models.URLField(verbose_name=_('frontchannel logout URI'), blank=True)
    frontchannel_timeout = models.PositiveIntegerField(
        verbose_name=_('frontchannel timeout'), null=True, blank=True
    )

    authorizations = GenericRelation(
        'OIDCAuthorization', content_type_field='client_ct', object_id_field='client_id'
    )

    pkce_code_challenge = models.BooleanField(
        _('Client MUST provide a PKCE code_challenge'),
        default=False,
        help_text=_('If PKCE is mandatory, the only method accepted will be S256.'),
    )

    uses_refresh_tokens = models.BooleanField(
        verbose_name=_('Client is issued a refresh token at token-endpoint request time'),
        default=False,
        help_text=_(
            'If activated, a refresh token will be issued in reply to the client\'s request to the '
            'the token endpoint (as well as the usual access token). Such refresh tokens can then '
            'be consumed anytime for access token renewal.'
        ),
    )

    # metadata
    created = models.DateTimeField(verbose_name=_('created'), auto_now_add=True)
    modified = models.DateTimeField(verbose_name=_('modified'), auto_now=True)

    def clean(self):
        super().clean()
        self.redirect_uris = strip_words(self.redirect_uris)
        self.post_logout_redirect_uris = strip_words(self.post_logout_redirect_uris)
        if self.idtoken_algo in (OIDCClient.ALGO_RSA, OIDCClient.ALGO_EC):
            try:
                utils.get_jwkset()
            except ImproperlyConfigured:
                raise ValidationError(
                    _('You cannot use algorithm %(algorithm)s, setting A2_IDP_OIDC_JWKSET is not defined')
                    % {'algorithm': self.get_idtoken_algo_display()}
                )
        if self.identifier_policy in [self.POLICY_PAIRWISE, self.POLICY_PAIRWISE_REVERSIBLE]:
            try:
                self.get_sector_identifier()
            except ValueError:
                raise ValidationError(
                    _(
                        'Redirect URIs must have the same domain or you must define a sector identifier URI'
                        ' if you want to use pairwiseidentifiers'
                    )
                )
        if self.pkce_code_challenge and self.authorization_flow != self.FLOW_AUTHORIZATION_CODE:
            raise ValidationError(_('PKCE can only be used with the authorization code flow.'))
        if self.uses_refresh_tokens and not 'offline_access' in self.scope_set():
            self.scope += ' offline_access'

    def get_wanted_attributes(self):
        return self.oidcclaim_set.filter(name__isnull=False).values_list('value', flat=True)

    def validate_redirect_uri(self, redirect_uri):
        return self._validate_uri(redirect_uri, self.get_redirect_uris())

    def validate_post_logout_redirect_uris(self, redirect_uri):
        return self._validate_uri(redirect_uri, self.get_post_logout_redirect_uris())

    def _validate_uri(self, redirect_uri, patterns):
        if len(redirect_uri) > app_settings.REDIRECT_URI_MAX_LENGTH:
            raise ValueError('redirect_uri length > %s' % app_settings.REDIRECT_URI_MAX_LENGTH)

        parsed_uri = urllib.parse.urlparse(redirect_uri)
        for valid_redirect_uri in patterns:
            parsed_valid_uri = urllib.parse.urlparse(valid_redirect_uri)
            if not parsed_valid_uri.scheme and parsed_uri.scheme in ['http', 'https']:
                pass
            elif parsed_uri.scheme != parsed_valid_uri.scheme:
                continue

            if parsed_valid_uri.port == 0:
                pass
            elif parsed_uri.scheme == 'http' and not parsed_valid_uri.port and parsed_uri.port == 80:
                pass
            elif parsed_uri.scheme == 'https' and not parsed_valid_uri.port and parsed_uri.port == 443:
                pass
            elif parsed_uri.port != parsed_valid_uri.port:
                continue

            if parsed_valid_uri.hostname and parsed_valid_uri.hostname.startswith('*'):
                # globing on the left
                hostname = parsed_valid_uri.hostname.lstrip('*')
                if parsed_uri.hostname != hostname and not parsed_uri.hostname.endswith('.' + hostname):
                    continue
            elif parsed_uri.hostname != parsed_valid_uri.hostname:
                continue
            if parsed_valid_uri.path.endswith('*'):
                path = parsed_valid_uri.path.rstrip('*').rstrip('/')
                if parsed_uri.path.rstrip('/') != path and not parsed_uri.path.startswith(path + '/'):
                    continue
            else:
                if parsed_uri.path.rstrip('/') != parsed_valid_uri.path.rstrip('/'):
                    continue
            if parsed_uri.query and parsed_valid_uri.query not in (parsed_uri.query, '*'):
                # xxx parameter validation
                continue
            if parsed_uri.fragment and parsed_valid_uri.fragment not in (parsed_uri.fragment, '*'):
                continue
            return
        raise ValueError('redirect_uri is not declared')

    def scope_set(self):
        return utils.scope_set(self.scope)

    def get_sector_identifier(self):
        if self.authorization_mode in (self.AUTHORIZATION_MODE_BY_SERVICE, self.AUTHORIZATION_MODE_NONE):
            sector_identifier = None
            if self.sector_identifier_uri:
                sector_identifier = utils.url_domain(self.sector_identifier_uri)
            else:
                for redirect_uri in self.get_redirect_uris():
                    hostname = utils.url_domain(redirect_uri)
                    if sector_identifier is None:
                        sector_identifier = hostname
                    elif sector_identifier != hostname:
                        raise ValueError('all redirect_uri do not have the same hostname')
        elif self.authorization_mode == self.AUTHORIZATION_MODE_BY_OU:
            if not self.ou:
                raise ValidationError(_('OU-based authorization requires that the client be within an OU.'))
            sector_identifier = self.ou.slug
        else:
            raise NotImplementedError('unknown self.authorization_mode %s' % self.authorization_mode)
        return sector_identifier

    def get_base_urls(self):
        return super().get_base_urls() + [url for url in self.get_redirect_uris() if url]

    def __repr__(self):
        return '<OIDCClient name:%r client_id:%r identifier_policy:%r>' % (
            self.name,
            self.client_id,
            self.get_identifier_policy_display(),
        )

    @property
    def manager_form_class(self):
        from .manager.forms import OIDCClientForm

        return OIDCClientForm

    def get_manager_context_data(self):
        ctx = super().get_manager_context_data()
        ctx['claims'] = self.oidcclaim_set.all()
        ctx['extra_details_template'] = 'authentic2_idp_oidc/manager/object_detail.html'
        return ctx

    def get_user_data(self, user):
        auth_manager = self.authorizations
        if self.authorization_mode == self.AUTHORIZATION_MODE_BY_OU:
            auth_manager = self.ou.oidc_authorizations
        if not auth_manager.filter(user=user, expired__gte=now()).exists():
            return {}
        return {'id': utils.make_sub(self, user)}

    def get_redirect_uris(self):
        return filter(None, self.redirect_uris.split())

    def get_post_logout_redirect_uris(self):
        return filter(None, self.post_logout_redirect_uris.split())

    @classmethod
    def find_by_post_logout_redirect_uri(cls, *, post_logout_redirect_uri):
        parsed = urllib.parse.urlparse(post_logout_redirect_uri)
        if parsed.netloc:
            qs = cls.objects.filter(post_logout_redirect_uris__contains=parsed.netloc)
        elif (
            parsed.scheme and '.' in parsed.scheme
        ):  # presence of dot indicate private scheme like com.example.android:
            qs = cls.objects.filter(post_logout_redirect_uris__contains=parsed.scheme)
        else:
            return None
        for provider in qs:
            try:
                provider.validate_post_logout_redirect_uris(post_logout_redirect_uri)
            except ValueError:
                pass
            else:
                return provider
        return None


class OIDCAuthorization(models.Model):
    client_ct = models.ForeignKey(
        'contenttypes.ContentType', verbose_name=_('client ct'), on_delete=models.CASCADE
    )
    client_id = models.PositiveIntegerField(verbose_name=_('client id'))
    client = GenericForeignKey('client_ct', 'client_id')
    user = models.ForeignKey(to=settings.AUTH_USER_MODEL, verbose_name=_('user'), on_delete=models.CASCADE)
    scopes = models.TextField(blank=False, verbose_name=_('scopes'))
    profile = models.ForeignKey(to=Profile, verbose_name=_('profile'), on_delete=models.CASCADE, null=True)

    # metadata
    created = models.DateTimeField(verbose_name=_('created'), auto_now_add=True)
    expired = models.DateTimeField(verbose_name=_('expire'))

    objects = managers.OIDCExpiredManager()

    def scope_set(self):
        return utils.scope_set(self.scopes)

    def __repr__(self):
        return '<OIDCAuthorization client:%r user:%r scopes:%r>' % (
            self.client_id and str(self.client),
            self.user_id and str(self.user),
            self.scopes,
        )


def get_session(session_key):
    engine = import_module(settings.SESSION_ENGINE)
    session = engine.SessionStore(session_key=session_key)
    session.load()
    if session._session_key == session_key:
        return session
    return None


class SessionMixin:
    @property
    def session(self):
        if not hasattr(self, '_session'):
            if self.session_key:
                self._session = get_session(self.session_key)
            else:
                self._session = None
        return getattr(self, '_session', None)

    @session.setter
    def session(self, session):
        if session:
            self.session_key = session.session_key
            self._session = session
        else:
            self.session_key = ''
            self._session = None

    def refresh_from_db(self, *args, **kwargs):
        if hasattr(self, '_session'):
            del self._session
        return super().refresh_from_db(*args, **kwargs)


class OIDCCode(SessionMixin, models.Model):
    CODE_CHALLENGE_METHOD_PLAIN = 1
    CODE_CHALLENGE_METHOD_S256 = 2

    CODE_CHALLENGE_METHODS = [
        (CODE_CHALLENGE_METHOD_PLAIN, 'plain'),
        (CODE_CHALLENGE_METHOD_S256, 'S256'),
    ]

    uuid = models.CharField(max_length=128, verbose_name=_('uuid'), default=generate_uuid)
    client = models.ForeignKey(to=OIDCClient, verbose_name=_('client'), on_delete=models.CASCADE)
    user = models.ForeignKey(to=settings.AUTH_USER_MODEL, verbose_name=_('user'), on_delete=models.CASCADE)
    profile = models.ForeignKey(
        to=Profile, verbose_name=_('user selected profile'), null=True, on_delete=models.CASCADE
    )
    scopes = models.TextField(verbose_name=pgettext_lazy('add english name between parenthesis', 'scopes'))
    state = models.TextField(null=True, verbose_name=_('state'))
    nonce = models.TextField(null=True, verbose_name='nonce')
    redirect_uri = models.TextField(verbose_name=_('redirect URI'), validators=[URLValidator()])
    session_key = models.CharField(verbose_name=_('session key'), max_length=128)
    auth_time = models.DateTimeField(verbose_name=_('auth time'))
    code_challenge = models.TextField(verbose_name=_('Code challenge'), blank=False, null=True)
    code_challenge_method = models.IntegerField(
        verbose_name=_('Code challenge method'),
        choices=CODE_CHALLENGE_METHODS,
        null=True,
        default=CODE_CHALLENGE_METHOD_PLAIN,
    )
    authorization = models.ForeignKey(
        to='authentic2_idp_oidc.OIDCAuthorization',
        verbose_name=_('authorization'),
        null=True,
        on_delete=models.CASCADE,
    )

    # metadata
    created = models.DateTimeField(verbose_name=_('created'), auto_now_add=True)
    expired = models.DateTimeField(verbose_name=_('expire'))

    objects = managers.OIDCExpiredManager()

    def scope_set(self):
        return utils.scope_set(self.scopes)

    def is_valid(self):
        if self.expired < now():
            return False
        if not self.session:
            return False
        if self.session.get('_auth_user_id') != str(self.user_id):
            return False
        return True

    def __repr__(self):
        return '<OIDCCode uuid:%s client:%s user:%s expired:%s scopes:%s>' % (
            self.uuid,
            self.client_id and str(self.client),
            self.user_id and str(self.user),
            self.expired,
            self.scopes,
        )


class OIDCAccessToken(SessionMixin, models.Model):
    uuid = models.CharField(max_length=128, verbose_name=_('uuid'), default=generate_uuid, db_index=True)
    client = models.ForeignKey(to=OIDCClient, verbose_name=_('client'), on_delete=models.CASCADE)
    user = models.ForeignKey(to=settings.AUTH_USER_MODEL, verbose_name=_('user'), on_delete=models.CASCADE)
    scopes = models.TextField(verbose_name=_('scopes'))
    session_key = models.CharField(verbose_name=_('session key'), max_length=128, blank=True)
    profile = models.ForeignKey(to=Profile, verbose_name=_('profile'), on_delete=models.CASCADE, null=True)
    refresh_token = models.ForeignKey(
        to='authentic2_idp_oidc.OIDCRefreshToken',
        verbose_name=_('refresh token'),
        null=True,
        on_delete=models.SET_NULL,
    )
    authorization = models.ForeignKey(
        to='authentic2_idp_oidc.OIDCAuthorization',
        verbose_name=_('authorization'),
        null=True,
        on_delete=models.CASCADE,
    )

    # metadata
    created = models.DateTimeField(verbose_name=_('created'), auto_now_add=True)
    expired = models.DateTimeField(verbose_name=_('expire'), null=True)

    objects = managers.OIDCExpiredManager()

    def scope_set(self):
        return utils.scope_set(self.scopes)

    def is_valid(self):
        if self.expired is not None and self.expired < now():
            return False
        if not self.session_key:
            return True
        if self.session is None:
            return False
        if self.session.get('_auth_user_id') != str(self.user_id):
            return False
        return True

    def __repr__(self):
        return '<OIDCAccessToken uuid:%s client:%s user:%s expired:%s scopes:%s>' % (
            self.uuid,
            self.client_id and str(self.client),
            self.user_id and str(self.user),
            self.expired,
            self.scopes,
        )


# Add generic field to a2_rbac.OrganizationalUnit
GenericRelation(
    'authentic2_idp_oidc.OIDCAuthorization', content_type_field='client_ct', object_id_field='client_id'
).contribute_to_class(OrganizationalUnit, 'oidc_authorizations')


class OIDCClaim(models.Model):
    client = models.ForeignKey(to=OIDCClient, verbose_name=_('client'), on_delete=models.CASCADE)
    name = models.CharField(max_length=128, blank=True, verbose_name=_('attribute name'))
    value = models.CharField(max_length=128, blank=True, verbose_name=_('value of attribute'))
    scopes = models.CharField(max_length=128, blank=True, verbose_name=_('attribute scopes'))

    def __str__(self):
        return '%s - %s - %s' % (self.name, self.value, self.scopes)

    def get_scopes(self):
        return self.scopes.strip().split(',')


class OIDCRefreshToken(models.Model):
    uuid = models.CharField(max_length=128, verbose_name=_('uuid'), default=generate_uuid, db_index=True)
    client = models.ForeignKey(to=OIDCClient, verbose_name=_('client'), on_delete=models.CASCADE)
    user = models.ForeignKey(to=settings.AUTH_USER_MODEL, verbose_name=_('user'), on_delete=models.CASCADE)
    scopes = models.TextField(verbose_name=_('scopes'))
    profile = models.ForeignKey(to=Profile, verbose_name=_('profile'), on_delete=models.CASCADE, null=True)
    refresh_token = models.ForeignKey(
        to='authentic2_idp_oidc.OIDCRefreshToken',
        verbose_name=_('refresh token'),
        null=True,
        on_delete=models.SET_NULL,
    )
    authorization = models.ForeignKey(
        to='authentic2_idp_oidc.OIDCAuthorization',
        verbose_name=_('authorization'),
        null=True,
        on_delete=models.CASCADE,
    )

    created = models.DateTimeField(verbose_name=_('created'), auto_now_add=True)
    expired = models.DateTimeField(verbose_name=_('expire'), null=True)

    objects = managers.OIDCExpiredManager()

    def is_valid(self):
        if self.expired is not None and self.expired < now():
            return False
        return True
