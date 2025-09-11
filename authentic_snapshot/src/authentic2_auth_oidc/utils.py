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

import logging
import urllib.parse

import requests
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404
from django.utils.text import slugify
from django.utils.translation import gettext as _

from authentic2.a2_rbac.utils import get_default_ou
from authentic2.models import Attribute
from authentic2.utils.cache import GlobalCache
from authentic2.utils.template import Template

from . import models

TIMEOUT = 1

logger = logging.getLogger('authentic2_auth_oidc')


class AuthenticationException(RuntimeError):
    pass


class AlreadyLinked(AuthenticationException):
    def __init__(self, email):
        self.email = email


@GlobalCache(timeout=TIMEOUT)
def get_attributes():
    return Attribute.objects.all()


@GlobalCache(timeout=TIMEOUT)
def get_provider(pk):
    from . import models

    return get_object_or_404(models.OIDCProvider, pk=pk)


@GlobalCache(timeout=TIMEOUT)
def get_provider_by_issuer(issuer):
    from . import models

    return models.OIDCProvider.objects.prefetch_related('claim_mappings').get(issuer=issuer)


def resolve_claim_mappings(provider, context, id_token=None, user_info=None, request=None):
    journal = request.journal if request and hasattr(request, 'journal') else None
    mappings = []
    attributes = {at.name: at for at in Attribute.all_objects.all()}
    for claim_mapping in provider.claim_mappings.all():
        name = claim_mapping.attribute
        claim = claim_mapping.claim
        required = claim_mapping.required
        attribute = attributes.get(name)

        if name in ('username', 'first_name', 'last_name', 'email', 'ou__slug'):
            # legacy attributes
            pass
        elif not attribute:
            logger.warning('auth_oidc: claim %s mapping to unknown attribute %s ignored', claim, name)
            continue

        if attribute and attribute.disabled:
            logger.warning('auth_oidc: claim %s mapping to disabled attribute %s ignored', claim, name)
            continue

        if id_token is None and user_info is None:
            source = context
            source_name = 'context'
        elif claim_mapping.idtoken_claim:
            source = id_token
            source_name = 'id_token'
        else:
            source = user_info
            source_name = 'user_info'
        if not source or claim not in source and not ('{{' in claim or '{%' in claim):
            if required:
                if journal:
                    journal.record(
                        'auth.oidc.claim_error', missing=True, claim=claim, source_name=source_name
                    )
                raise AuthenticationException(
                    _('Your account is misconfigured, missing required claim {}.').format(claim)
                )
            continue

        verified = False
        if '{{' in claim or '{%' in claim:
            template = Template(claim)
            value = template.render(context=context)
        else:
            value = source.get(claim)
            if claim_mapping.verified == models.OIDCClaimMapping.VERIFIED_CLAIM:
                verified = bool(source.get(claim + '_verified', False))
        if claim_mapping.verified == models.OIDCClaimMapping.ALWAYS_VERIFIED:
            verified = True

        if attribute:
            try:
                attribute.validate_value(value)
            except ValidationError:
                if required:
                    if journal:
                        journal.record(
                            'auth.oidc.claim_error', missing=False, claim=claim, source_name=source_name
                        )

                    raise AuthenticationException(
                        _('Your account is misconfigured, invalid value for required claim {}.').format(claim)
                    )
                logger.warning(
                    'auth_oidc: invalid value %s for claim %s mapping to attribute %s ignored',
                    value,
                    claim,
                    name,
                )
                continue

        mappings.append((name, value, verified))
    return mappings


OPENID_CONFIGURATION_REQUIRED = {
    'issuer',
    'authorization_endpoint',
    'token_endpoint',
    'jwks_uri',
    'response_types_supported',
    'subject_types_supported',
    'id_token_signing_alg_values_supported',
    'userinfo_endpoint',
}


def check_https(url):
    return urllib.parse.urlparse(url).scheme == 'https'


def register_issuer(
    name, client_id, client_secret, issuer=None, openid_configuration=None, verify=True, timeout=None, ou=None
):
    from . import models

    if issuer and not openid_configuration:
        openid_configuration_url = get_openid_configuration_url(issuer)
        try:
            response = requests.get(openid_configuration_url, verify=verify, timeout=timeout)
            response.raise_for_status()
        except requests.RequestException as e:
            raise ValueError(
                _('Unable to reach the OpenID Connect configuration for %(issuer)s: %(error)s')
                % {
                    'issuer': issuer,
                    'error': e,
                }
            )

    try:
        openid_configuration = openid_configuration or response.json()
        if not isinstance(openid_configuration, dict):
            raise ValueError(_('MUST be a dictionnary'))
        keys = set(openid_configuration.keys())
        if not keys >= OPENID_CONFIGURATION_REQUIRED:
            raise ValueError(_('missing keys %s') % (OPENID_CONFIGURATION_REQUIRED - keys))
        for key in ['issuer', 'authorization_endpoint', 'token_endpoint', 'jwks_uri', 'userinfo_endpoint']:
            if not check_https(openid_configuration[key]):
                raise ValueError(
                    _('%(key)s is not an https:// URL; %(value)s')
                    % {
                        'key': key,
                        'value': openid_configuration[key],
                    }
                )
    except ValueError as e:
        raise ValueError(_('Invalid OpenID Connect configuration for %(issuer)s: %(error)s') % (issuer, e))
    if 'code' not in openid_configuration['response_types_supported']:
        raise ValueError(_('authorization code flow is unsupported: code response type is unsupported'))
    try:
        response = requests.get(openid_configuration['jwks_uri'], verify=verify, timeout=None)
        response.raise_for_status()
    except requests.RequestException as e:
        raise ValueError(
            _('Unable to reach the OpenID Connect JWKSet for %(issuer)s: %(url)s %(error)s')
            % {
                'issuer': issuer,
                'url': openid_configuration['jwks_uri'],
                'error': e,
            }
        )
    try:
        jwkset_json = response.json()
    except ValueError as e:
        raise ValueError(_('Invalid JSKSet document: %s') % e)
    try:
        old_pk = models.OIDCProvider.objects.get(issuer=openid_configuration['issuer']).pk
    except models.OIDCProvider.DoesNotExist:
        old_pk = None
    if {'RS256', 'RS384', 'RS512'} & set(openid_configuration['id_token_signing_alg_values_supported']):
        idtoken_algo = models.OIDCProvider.ALGO_RSA
    elif {'HS256', 'HS384', 'HS512'} & set(openid_configuration['id_token_signing_alg_values_supported']):
        idtoken_algo = models.OIDCProvider.ALGO_HMAC
    elif {'ES256', 'ES384', 'ES512'} & set(openid_configuration['id_token_signing_alg_values_supported']):
        idtoken_algo = models.OIDCProvider.ALGO_EC
    else:
        raise ValueError(
            _('no common algorithm found for signing idtokens: %s')
            % openid_configuration['id_token_signing_alg_values_supported']
        )
    claims_parameter_supported = openid_configuration.get('claims_parameter_supported') is True
    kwargs = dict(
        ou=ou or get_default_ou(),
        name=name,
        slug=slugify(name),
        client_id=client_id,
        client_secret=client_secret,
        issuer=openid_configuration['issuer'],
        authorization_endpoint=openid_configuration['authorization_endpoint'],
        token_endpoint=openid_configuration['token_endpoint'],
        userinfo_endpoint=openid_configuration['userinfo_endpoint'],
        end_session_endpoint=openid_configuration.get('end_session_endpoint', None),
        token_revocation_endpoint=openid_configuration.get('token_revocation_endpoint', None),
        jwkset_url=openid_configuration['jwks_uri'],
        jwkset_json=jwkset_json,
        idtoken_algo=idtoken_algo,
        strategy=models.OIDCProvider.STRATEGY_CREATE,
        claims_parameter_supported=claims_parameter_supported,
    )
    if old_pk:
        models.OIDCProvider.objects.filter(pk=old_pk).update(**kwargs)
        return models.OIDCProvider.objects.get(pk=old_pk)
    else:
        return models.OIDCProvider.objects.create(**kwargs)


def get_openid_configuration_url(issuer):
    parsed = urllib.parse.urlparse(issuer)
    if parsed.query or parsed.fragment or parsed.scheme != 'https':
        raise ValueError(
            _('invalid issuer URL, it must use the https:// scheme and not have a query or fragment')
        )
    issuer = urllib.parse.urlunparse(
        (parsed.scheme, parsed.netloc, parsed.path.rstrip('/'), None, None, None)
    )
    return issuer + '/.well-known/openid-configuration'
