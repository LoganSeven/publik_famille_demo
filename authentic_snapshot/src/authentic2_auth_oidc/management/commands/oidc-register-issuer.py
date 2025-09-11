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


import json
import pprint
import warnings

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db.transaction import atomic

from authentic2.a2_rbac.models import OrganizationalUnit
from authentic2_auth_oidc.models import OIDCClaimMapping, OIDCProvider
from authentic2_auth_oidc.utils import register_issuer


class Command(BaseCommand):
    '''Load LDAP ldif file'''

    can_import_django_settings = True
    requires_system_checks = '__all__'
    help = 'Register an OpenID Connect OP'

    def add_arguments(self, parser):
        parser.add_argument('name')
        parser.add_argument('--issuer', help='do automatic registration of the issuer')
        parser.add_argument(
            '--openid-configuration', help='file containing the OpenID Connect configuration of the OP'
        )
        parser.add_argument(
            '--claim-mapping', default=[], action='append', help='mapping from claim to attribute'
        )
        parser.add_argument(
            '--delete-claim', default=[], action='append', help='delete mapping from claim to attribute'
        )
        parser.add_argument('--client-id', help='registered client ID')
        parser.add_argument('--client-secret', help='register client secret')
        parser.add_argument('--scope', default=[], action='append', help='extra scopes, openid is automatic')
        parser.add_argument(
            '--no-verify', default=False, action='store_true', help='do not verify TLS certificates'
        )
        parser.add_argument('--show', default=False, action='store_true', help='show provider configuration')
        parser.add_argument('--ou-slug', help='slug of the ou, if absent default ou is used')

    @atomic
    def handle(self, *args, **options):
        name = options['name']
        openid_configuration = options.get('openid_configuration')
        issuer = options.get('issuer')
        client_id = options.get('client_id')
        client_secret = options.get('client_secret')
        if openid_configuration:
            with open(openid_configuration) as fd:
                openid_configuration = json.load(fd)
        if issuer or openid_configuration:
            if not client_id:
                raise CommandError('Client identifier must be specified')
            if not client_secret:
                raise CommandError('Client secret must be specified')
            try:
                ou = None
                if options.get('ou_slug'):
                    ou = OrganizationalUnit.objects.get(slug=options['ou_slug'])
                provider = register_issuer(
                    name,
                    client_id,
                    client_secret,
                    issuer=issuer,
                    openid_configuration=openid_configuration,
                    verify=not options['no_verify'],
                    ou=ou,
                )
            except ValueError as e:
                raise CommandError(e)
        else:
            if client_id:
                warnings.warn('--client-id given but will not be used', FutureWarning)
            if client_secret:
                warnings.warn('--client-secret given but will not be used', FutureWarning)
            try:
                provider = OIDCProvider.objects.get(name=name)
            except OIDCProvider.DoesNotExist:
                raise CommandError('Unknown OIDC provider')
        try:
            provider.full_clean()
        except ValidationError as e:
            provider.delete()
            raise CommandError(e)
        scope = options.get('scope')
        if scope is not None:
            provider.scopes = ' '.join(filter(None, options['scope']))
        provider.save()

        for claim_mapping in options.get('claim_mapping', []):
            tup = claim_mapping.split()
            if len(tup) < 2:
                raise CommandError(
                    'invalid claim mapping %r. it must contain at least a claim and an attribute name'
                )
            claim, attribute = tup[:2]
            claim_options = [x.strip() for x in tup[2:]]
            extra = {
                'required': 'required' in claim_options,
                'idtoken_claim': 'idtoken' in claim_options,
            }
            if 'always_verified' in claim_options:
                extra['verified'] = OIDCClaimMapping.ALWAYS_VERIFIED
            elif 'verified' in claim_options:
                extra['verified'] = OIDCClaimMapping.VERIFIED_CLAIM
            else:
                extra['verified'] = OIDCClaimMapping.NOT_VERIFIED
            o, created = OIDCClaimMapping.objects.get_or_create(
                authenticator=provider, claim=claim, attribute=attribute, defaults=extra
            )
            if not created:
                OIDCClaimMapping.objects.filter(pk=o.pk).update(**extra)
        delete_claims = options.get('delete_claim', [])
        if delete_claims:
            OIDCClaimMapping.objects.filter(authenticator=provider, claim__in=delete_claims).delete()
        if options.get('show'):
            for field in OIDCProvider._meta.fields:
                print(field.verbose_name, ':')
                value = getattr(provider, field.name)
                if isinstance(value, dict):
                    pprint.pprint(value)
                elif hasattr(provider, str('get_' + field.attname + '_display')):
                    print(getattr(provider, 'get_' + field.attname + '_display')(), '(%s)' % value)
                else:
                    print(value)
            print('Mappings:')
            for claim_mapping in provider.claim_mappings.all():
                print('-', claim_mapping)
