# authentic2 - versatile identity manager
# Copyright (C) 2010-2020 Entr'ouvert
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
import logging
from datetime import datetime, timedelta

import requests
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import JSONField
from django.shortcuts import render
from django.utils.timezone import now
from django.utils.translation import gettext_lazy as _
from django.utils.translation import pgettext_lazy

from authentic2.apps.authenticators.models import (
    AddRoleAction,
    AuthenticatorRelatedObjectBase,
    BaseAuthenticator,
)
from authentic2.apps.journal.journal import journal
from authentic2.utils import http
from authentic2.utils.jwc import parse_jwkset, validate_jwkset
from authentic2.utils.misc import make_url
from authentic2.utils.template import validate_template

from . import managers, utils


class OIDCProvider(BaseAuthenticator):
    STRATEGY_CREATE = 'create'
    STRATEGY_FIND_UUID = 'find-uuid'
    STRATEGY_FIND_USERNAME = 'find-username'
    STRATEGY_FIND_EMAIL = 'find-email'
    STRATEGY_NONE = 'none'

    STRATEGIES = [
        (
            STRATEGY_CREATE,
            _(
                'create if account matching on email address failed (matching will fail if '
                'global and provider\'s ou-wise email uniqueness is deactivated)'
            ),
        ),
        (STRATEGY_FIND_UUID, _('use sub to find existing user through UUID')),
        (STRATEGY_FIND_USERNAME, _('use sub to find existing user through username')),
        (
            STRATEGY_FIND_EMAIL,
            _('use email claim (or sub if claim is absent) to find existing user through email'),
        ),
        (STRATEGY_NONE, _('none')),
    ]
    ALGO_NONE = 0
    ALGO_RSA = 1
    ALGO_HMAC = 2
    ALGO_EC = 3
    ALGO_CHOICES = [
        (ALGO_NONE, _('none')),
        (ALGO_RSA, _('RSA')),
        (ALGO_HMAC, _('HMAC')),
        (ALGO_EC, _('EC')),
    ]

    issuer = models.CharField(max_length=256, verbose_name=_('issuer'), db_index=True)
    client_id = models.CharField(max_length=128, verbose_name=_('client id'))
    client_secret = models.CharField(max_length=128, verbose_name=_('client secret'))
    # endpoints
    authorization_endpoint = models.URLField(max_length=128, verbose_name=_('authorization endpoint'))
    token_endpoint = models.URLField(max_length=128, verbose_name=_('token endpoint'))
    userinfo_endpoint = models.URLField(max_length=128, verbose_name=_('userinfo endpoint'))
    end_session_endpoint = models.URLField(
        max_length=128, blank=True, null=True, verbose_name=_('end session endpoint')
    )
    token_revocation_endpoint = models.URLField(
        max_length=128, blank=True, null=True, verbose_name=_('token revocation endpoint')
    )
    scopes = models.CharField(
        max_length=128,
        blank=True,
        verbose_name=pgettext_lazy('add english name between parenthesis', 'scopes'),
    )
    jwkset_url = models.URLField(
        max_length=256,
        verbose_name=_("URL of the provider's JSON WebKey Set"),
        blank=True,
        default='',
        help_text=_('This URL is usually part of the “well-known” URLs as per the OIDC specifications'),
    )
    jwkset_json = JSONField(
        verbose_name=_('JSON WebKey set'), null=True, blank=True, validators=[validate_jwkset]
    )
    idtoken_algo = models.PositiveIntegerField(
        default=ALGO_RSA, choices=ALGO_CHOICES, verbose_name=_('IDToken signature algorithm')
    )
    claims_parameter_supported = models.BooleanField(
        verbose_name=_('Claims parameter supported'), default=False
    )

    # ou where new users should be created
    strategy = models.CharField(max_length=32, choices=STRATEGIES, verbose_name=_('strategy'))

    # policy
    max_auth_age = models.PositiveIntegerField(
        verbose_name=_('max authentication age'), blank=True, null=True
    )

    # authentic2 specific synchronization api
    a2_synchronization_supported = models.BooleanField(
        verbose_name=_('Authentic2 synchronization supported'),
        default=False,
    )
    last_sync_time = models.DateTimeField(
        verbose_name=_('Last synchronization time'),
        null=True,
        blank=True,
        editable=False,
    )

    # metadata
    created = models.DateTimeField(verbose_name=_('creation date'), auto_now_add=True)
    modified = models.DateTimeField(verbose_name=_('last modification date'), auto_now=True)

    # passive authn deactivation flag
    passive_authn_supported = models.BooleanField(
        verbose_name=_('Supports passive authentication'),
        default=True,
    )
    objects = managers.OIDCProviderManager()

    type = 'oidc'
    how = ['oidc']
    manager_idp_info_template_name = 'authentic2_auth_oidc/idp_configuration_info.html'
    description_fields = ['show_condition', 'issuer', 'scopes', 'strategy', 'created', 'modified']

    class Meta:
        verbose_name = _('OpenID Connect')
        constraints = [
            models.UniqueConstraint(
                fields=['issuer'],
                name='unique_issuer_if_not_empty',
                condition=~models.Q(issuer=''),
            ),
        ]

    @property
    def manager_form_classes(self):
        from .forms import OIDCProviderAdvancedForm, OIDCProviderEditForm

        return [
            (_('General'), OIDCProviderEditForm),
            (_('Advanced'), OIDCProviderAdvancedForm),
        ]

    @property
    def related_object_form_class(self):
        from .forms import OIDCRelatedObjectForm

        return OIDCRelatedObjectForm

    @property
    def related_models(self):
        return {
            OIDCClaimMapping: self.claim_mappings.all(),
            AddRoleAction: self.add_role_actions.all(),
        }

    @property
    def jwkset(self):
        if self.jwkset_json:
            try:
                return parse_jwkset(json.dumps(self.jwkset_json))
            except ValidationError:
                pass
        return None

    def get_short_description(self):
        if self.issuer and self.scopes:
            return _('OIDC provider linked to issuer %(issuer)s with scopes %(scopes)s.') % {
                'issuer': self.issuer,
                'scopes': self.scopes.replace(' ', ', '),
            }

    def clean_fields(self, exclude=None):
        super().clean_fields(exclude=exclude)
        exclude = exclude or []

        if 'jwkset_url' not in exclude and self.jwkset_url:
            try:
                self.jwkset_json = self.load_jwkset_url()
            except ValidationError as e:
                raise ValidationError({'jwkset_url': e})

        if 'idtoken_algo' not in exclude:
            if self.idtoken_algo == self.ALGO_NONE:
                raise ValidationError(
                    _(
                        'A provider signature method should be declared, e.g. HMAC wich will use the '
                        'client secret as the signature key.'
                    )
                )
            if self.idtoken_algo in (self.ALGO_RSA, self.ALGO_EC):
                key_sig_mapping = {
                    self.ALGO_RSA: 'RSA',
                    self.ALGO_EC: 'EC',
                }
                jwkset = self.jwkset
                if not jwkset:
                    raise ValidationError(
                        _('Provider signature method is %s yet no jwkset was provided.')
                        % key_sig_mapping[self.idtoken_algo]
                    )
                # verify that a key is available for the chosen algorithm
                for key in jwkset['keys']:
                    # compatibility with jwcrypto < 1
                    key_type = key.get('kty', None) if isinstance(key, dict) else key.key_type
                    if key_type == key_sig_mapping[self.idtoken_algo]:
                        break
                else:
                    raise ValidationError(
                        _(
                            'Provider signature method is %s yet the provided jwkset does not contain any such key type.'
                        )
                        % key_sig_mapping[self.idtoken_algo]
                    )

    def save(self, *args, **kwargs):
        if self.jwkset_url and not self.jwkset_json:
            raise ValueError('model is not initialized')
        if self.jwkset_json:
            validate_jwkset(self.jwkset_json)
        return super().save(*args, **kwargs)

    def load_jwkset_url(self):
        try:
            response = http.get(self.jwkset_url)
        except http.HTTPError as e:
            raise ValidationError(_('JWKSet URL is unreachable: %s') % e)
        return parse_jwkset(response.content).export(as_dict=True)

    def refresh_jwkset_json(self):
        if not self.jwkset_url:
            return

        old_jwkset = self.jwkset_json
        new_jwkset = self.load_jwkset_url()

        if old_jwkset == new_jwkset:
            return

        with transaction.atomic():
            self.jwkset_json = new_jwkset
            self.log_jwkset_change(old_jwkset, new_jwkset)
            self.save(update_fields=['jwkset_json', 'modified'])

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

    def is_origin_for_user(self, user):
        if hasattr(user, 'oidc_account') and user.oidc_account.provider == self:
            return self.claim_mappings.filter(
                authenticator=self,
                attribute='email',
            ).exists()
        return False

    def authorization_claims_parameter(self):
        idtoken_claims = {}
        userinfo_claims = {}
        for claim_mapping in self.claim_mappings.all():
            d = idtoken_claims if claim_mapping.idtoken_claim else userinfo_claims
            value = d.setdefault(claim_mapping.claim, {}) or {}
            if claim_mapping.required:
                value['essential'] = True
            d[claim_mapping.claim] = value or None
        return {
            'id_token': idtoken_claims,
            'userinfo': userinfo_claims,
        }

    def __repr__(self):
        return '<OIDCProvider %r>' % self.issuer

    def autorun(self, request, block_id, next_url):
        from . import views

        return views.oidc_login(request, pk=self.pk, next_url=next_url)

    def passive_login(self, request, block_id, next_url):
        from . import views

        return views.oidc_login(
            request,
            pk=self.pk,
            next_url=next_url,
            # self.passive_authn_supported == False means that the remote provider implementation
            # is buggy, prompt=none will trigger a remote HTTP 500 instead of the OIDC-specified
            # {login,consent,interaction}_required error. Hence do not try to add prompt=none. Try
            # a standard authn request instead, the lesser evil in this case.
            passive=self.passive_authn_supported,
        )

    def login(self, request, *args, **kwargs):
        context = kwargs.get('context', {}).copy()
        context['provider'] = self
        context['login_url'] = make_url(
            'oidc-login', kwargs={'pk': self.id}, request=request, keep_params=True
        )
        template_names = [
            'authentic2_auth_oidc/login_%s.html' % self.slug,
            'authentic2_auth_oidc/login.html',
        ]
        return render(request, template_names, context)

    def perform_synchronization(self, sync_time=None, timeout=30):
        logger = logging.getLogger(__name__)

        if not self.a2_synchronization_supported:
            logger.error('OIDC provider %s does not support synchronization', self.slug)
            return
        if not sync_time:
            sync_time = now() - timedelta(minutes=1)

        # check all existing users
        def chunks(l, n):
            for i in range(0, len(l), n):
                yield l[i : i + n]

        url = self.issuer + '/api/users/synchronization/'

        unknown_uuids = []
        auth = (self.client_id, self.client_secret)
        for accounts in chunks(OIDCAccount.objects.filter(provider=self), 100):
            subs = [x.sub for x in accounts]
            resp = requests.post(url, json={'known_uuids': subs}, auth=auth, timeout=timeout)
            resp.raise_for_status()
            unknown_uuids.extend(resp.json().get('unknown_uuids'))
        deletion_ratio = len(unknown_uuids) / OIDCAccount.objects.filter(provider=self).count()
        if deletion_ratio > 0.05:  # higher than 5%, something definitely went wrong
            logger.error(
                'deletion ratio is abnormally high (%s), aborting unkwown users deletion', deletion_ratio
            )
        else:
            OIDCAccount.objects.filter(sub__in=unknown_uuids).delete()

        # update recently modified users
        url = self.issuer + '/api/users/?modified__gt=%s&claim_resolution' % (
            self.last_sync_time or datetime.utcfromtimestamp(0)
        ).strftime('%Y-%m-%dT%H:%M:%S')
        while url:
            resp = requests.get(url, auth=auth, timeout=timeout)
            resp.raise_for_status()
            url = resp.json().get('next')
            logger.info('got %s users', len(resp.json()['results']))
            for user_dict in resp.json()['results']:
                if not user_dict.get('sub', None):
                    continue
                try:
                    account = OIDCAccount.objects.get(sub=user_dict['sub'])
                except OIDCAccount.DoesNotExist:
                    continue
                except OIDCAccount.MultipleObjectsReturned:
                    continue
                had_changes = False
                mappings = utils.resolve_claim_mappings(self, user_dict)
                for attribute, value, dummy in mappings:
                    try:
                        old_attribute_value = getattr(account.user, attribute)
                    except AttributeError:
                        try:
                            old_attribute_value = getattr(account.user.attributes, attribute)
                        except AttributeError:
                            old_attribute_value = None
                    if old_attribute_value == value:
                        continue
                    had_changes = True
                    setattr(account.user, attribute, value)
                    try:
                        setattr(account.user.attributes, attribute, value)
                    except AttributeError:
                        pass
                if had_changes:
                    logger.debug('had changes, saving %r', account.user)
                    account.user.save()
        self.last_sync_time = sync_time
        self.save(update_fields=['last_sync_time'])


class OIDCClaimMapping(AuthenticatorRelatedObjectBase):
    NOT_VERIFIED = 0
    VERIFIED_CLAIM = 1
    ALWAYS_VERIFIED = 2
    VERIFIED_CHOICES = [
        (NOT_VERIFIED, _('not verified')),
        (VERIFIED_CLAIM, _('verified claim')),
        (ALWAYS_VERIFIED, _('always verified')),
    ]

    claim = models.CharField(max_length=128, verbose_name=_('claim'), validators=[validate_template])
    attribute = models.CharField(max_length=64, verbose_name=_('attribute'))
    verified = models.PositiveIntegerField(
        default=NOT_VERIFIED, choices=VERIFIED_CHOICES, verbose_name=_('verified')
    )
    required = models.BooleanField(blank=True, default=False, verbose_name=_('required'))
    idtoken_claim = models.BooleanField(verbose_name=_('idtoken claim'), default=False, blank=True)
    created = models.DateTimeField(verbose_name=_('creation date'), auto_now_add=True)
    modified = models.DateTimeField(verbose_name=_('last modification date'), auto_now=True)

    objects = managers.OIDCClaimMappingManager()

    description = _('Set user fields using claims.')

    class Meta:
        default_related_name = 'claim_mappings'
        verbose_name = _('Claim')
        verbose_name_plural = _('Claims')

    def natural_key(self):
        return (self.claim, self.attribute, self.verified, self.required)

    def get_attribute_display(self):
        from .forms import SelectAttributeWidget

        return SelectAttributeWidget.get_options().get(self.attribute, self.attribute)

    def __str__(self):
        s = '%s → %s' % (self.claim, self.get_attribute_display())
        if self.verified:
            s += ', verified'
        if self.required:
            s += ', required'
        if self.idtoken_claim:
            s += ', idtoken'
        return s

    def __repr__(self):
        return '<OIDCClaimMapping %r:%r on provider %r verified:%s required:%s >' % (
            self.claim,
            self.attribute,
            self.authenticator,
            self.verified,
            self.required,
        )


class OIDCAccount(models.Model):
    created = models.DateTimeField(verbose_name=_('creation date'), auto_now_add=True)
    modified = models.DateTimeField(verbose_name=_('last modification date'), auto_now=True)
    provider = models.ForeignKey(
        to='OIDCProvider', verbose_name=_('provider'), related_name='accounts', on_delete=models.CASCADE
    )
    user = models.OneToOneField(
        to=settings.AUTH_USER_MODEL,
        verbose_name=_('user'),
        related_name='oidc_account',
        on_delete=models.CASCADE,
    )
    sub = models.CharField(verbose_name=_('sub'), max_length=256)

    def __str__(self):
        return f'{self.sub} on {self.provider and self.provider.issuer} linked to {self.user}'

    def __repr__(self):
        return '<OIDCAccount %r on %r>' % (self.sub, self.provider and self.provider.issuer)

    class Meta:
        unique_together = [
            ('provider', 'sub'),
        ]
