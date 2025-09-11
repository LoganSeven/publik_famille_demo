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


import io
import os
import sys
import warnings
import xml.etree.ElementTree as etree

import requests
from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand, CommandError
from django.db.transaction import atomic
from django.template.defaultfilters import slugify
from django.utils.translation import gettext as _

from authentic2.compat_lasso import lasso
from authentic2.saml.models import LibertyProvider, LibertyServiceProvider, SAMLAttribute, SPOptionsIdPPolicy
from authentic2.saml.shibboleth.afp_parser import parse_attribute_filters_file

from .mapping import (
    get_def_name_from_alias,
    get_def_name_from_oid,
    get_definition_from_alias,
    get_definition_from_oid,
    get_full_definition,
)

SAML2_METADATA_UI_HREF = 'urn:oasis:names:tc:SAML:metadata:ui'


def md_element_name(tag_name):
    return '{%s}%s' % (lasso.SAML2_METADATA_HREF, tag_name)


def mdui_element_name(tag_name):
    return '{%s}%s' % (SAML2_METADATA_UI_HREF, tag_name)


ENTITY_DESCRIPTOR_TN = md_element_name('EntityDescriptor')
ENTITIES_DESCRIPTOR_TN = md_element_name('EntitiesDescriptor')
IDP_SSO_DESCRIPTOR_TN = md_element_name('IDPSSODescriptor')
SP_SSO_DESCRIPTOR_TN = md_element_name('SPSSODescriptor')
ORGANIZATION_DISPLAY_NAME = md_element_name('OrganizationDisplayName')
ORGANIZATION_NAME = md_element_name('OrganizationName')
ORGANIZATION = md_element_name('Organization')
EXTENSIONS = md_element_name('Extensions')
ATTRIBUTE_CONSUMING_SERVICE = md_element_name('AttributeConsumingService')
SERVICE_NAME = md_element_name('ServiceName')
SERVICE_DESCRIPTION = md_element_name('ServiceDescription')
REQUESTED_ATTRIBUTE = md_element_name('RequestedAttribute')

UI_INFO = mdui_element_name('UIInfo')
DISPLAY_NAME = mdui_element_name('DisplayName')

ENTITY_ID = 'entityID'
PROTOCOL_SUPPORT_ENUMERATION = 'protocolSupportEnumeration'
IS_REQUIRED = 'isRequired'
NAME_FORMAT = 'NameFormat'
NAME = 'Name'
FRIENDLY_NAME = 'FriendlyName'


def resolve_urn_oid(urn_oid):
    if not urn_oid.startswith('urn:oid:'):
        return None, None
    oid = urn_oid[8:]
    return get_def_name_from_oid(oid), get_definition_from_oid(oid)


def build_saml_attribute_kwargs(provider, name):
    '''Build SAML attribute following the LDAP profile'''
    content_type = ContentType.objects.get_for_model(LibertyProvider)
    object_id = provider.pk
    attribute_name = name
    definition = get_full_definition(name)
    if not definition:
        definition = get_definition_from_alias(name)
        attribute_name = get_def_name_from_alias(name)
    if not definition:
        return {}, None
    oid = definition['oid']
    return {
        'content_type': content_type,
        'object_id': object_id,
        'name_format': 'uri',
        'name': 'urn:oid:%s' % oid,
    }, {
        'attribute_name': attribute_name.lower(),
        'friendly_name': name,
    }


def check_support_saml2(tree):
    if tree is not None and lasso.SAML2_PROTOCOL_HREF in tree.get(PROTOCOL_SUPPORT_ENUMERATION):
        return True
    return False


def text_child(tree, tag, default=''):
    elt = tree.find(tag)
    return elt.text if elt is not None else default


def load_acs(tree, provider, pks, verbosity):
    acss = tree.iter(ATTRIBUTE_CONSUMING_SERVICE)
    for acs in acss:
        for ra in acs.iter(REQUESTED_ATTRIBUTE):
            oid = ra.get(NAME, '')
            name_format = ra.get(NAME_FORMAT, '')
            friendly_name = ra.get(FRIENDLY_NAME, '')
            is_required = ra.get(IS_REQUIRED, 'false') == 'true'
            if name_format != lasso.SAML2_ATTRIBUTE_NAME_FORMAT_URI:
                continue
            def_name, dummy = resolve_urn_oid(oid)
            if def_name is None:
                warnings.warn(
                    'attribute %s/%s unsupported on service provider %s'
                    % (oid, name_format, provider.entity_id)
                )
                continue
            content_type = ContentType.objects.get_for_model(LibertyProvider)
            object_id = provider.pk
            kwargs = {
                'content_type': content_type,
                'object_id': object_id,
                'name_format': 'uri',
                'name': oid,
            }
            defaults = {
                'attribute_name': def_name.lower(),
                'friendly_name': friendly_name or def_name,
                'enabled': is_required,
            }

            try:
                attribute, created = SAMLAttribute.objects.get_or_create(defaults=defaults, **kwargs)
                if created and verbosity > 1:
                    print(
                        _('Created new attribute %(name)s for %(provider)s')
                        % {'name': oid, 'provider': provider}
                    )
                pks.append(attribute.pk)
            except SAMLAttribute.MultipleObjectsReturned:
                pks.extend(SAMLAttribute.objects.filter(**kwargs).values_list('pk', flat=True))


def load_one_entity(tree, options, sp_policy=None, afp=None):
    '''Load or update an EntityDescriptor into the database'''
    verbosity = int(options['verbosity'])
    entity_id = tree.get(ENTITY_ID)
    name = None
    # try mdui nodes
    display_name = tree.find('.//%s/%s/%s' % (EXTENSIONS, UI_INFO, DISPLAY_NAME))
    if display_name is not None:
        name = display_name.text
    # try "old" organization node
    if not name:
        organization = tree.find(ORGANIZATION)
        if organization is not None:
            organization_display_name = organization.find(ORGANIZATION_DISPLAY_NAME)
            organization_name = organization.find(ORGANIZATION_NAME)
            if organization_display_name is not None:
                name = organization_display_name.text
            elif organization_name is not None:
                name = organization_name.text
    if not name:
        name = entity_id
    sp = False, False
    sp = check_support_saml2(tree.find(SP_SSO_DESCRIPTOR_TN))
    if options.get('delete'):
        LibertyProvider.objects.filter(entity_id=entity_id).delete()
        print('Deleted', entity_id)
        return
    if sp:
        # build an unique slug
        baseslug = slug = slugify(name)[:120]
        n = 1
        while LibertyProvider.objects.filter(slug=slug).exclude(entity_id=entity_id):
            n += 1
            slug = '%s-%d' % (baseslug, n)
        # get or create the provider
        provider, created = LibertyProvider.objects.get_or_create(
            entity_id=entity_id, protocol_conformance=3, defaults={'name': name[:128], 'slug': slug}
        )
        if verbosity > 1:
            if created:
                what = 'Creating'
            else:
                what = 'Updating'
            print('%(what)s %(name)s, %(id)s' % {'what': what, 'name': name.encode('utf8'), 'id': entity_id})
        provider.name = name[:128]
        provider.metadata = etree.tostring(tree, encoding='utf-8').decode('utf-8').strip()
        provider.protocol_conformance = 3
        provider.federation_source = options['source']
        provider.save()
        options['count'] = options.get('count', 0) + 1
        if sp:
            service_provider, created = LibertyServiceProvider.objects.get_or_create(
                liberty_provider=provider, defaults={'enabled': not options['create-disabled']}
            )
            if sp_policy:
                service_provider.sp_options_policy = sp_policy
            service_provider.save()
            pks = []
            if options['load_attribute_consuming_service']:
                load_acs(tree, provider, pks, verbosity)
            if afp and provider.entity_id in afp:
                for name in afp[provider.entity_id]:
                    kwargs, defaults = build_saml_attribute_kwargs(provider, name)
                    if not kwargs:
                        if verbosity > 1:
                            print(
                                _('Unable to find an LDAP definition for attribute %(name)s on %(provider)s')
                                % {'name': name, 'provider': provider},
                                file=sys.stderr,
                            )
                        continue
                    # create object with default attribute mapping to the same name
                    # as the attribute if no SAMLAttribute model already exists,
                    # otherwise do nothing
                    try:
                        attribute, created = SAMLAttribute.objects.get_or_create(defaults=defaults, **kwargs)
                        if created and verbosity > 1:
                            print(
                                _('Created new attribute %(name)s for %(provider)s')
                                % {'name': name, 'provider': provider}
                            )
                        pks.append(attribute.pk)
                    except SAMLAttribute.MultipleObjectsReturned:
                        pks.extend(SAMLAttribute.objects.filter(**kwargs).values_list('pk', flat=True))
                if options.get('reset-attributes'):
                    # remove attributes not matching the filters
                    SAMLAttribute.objects.for_generic_object(provider).exclude(pk__in=pks).delete()


class Command(BaseCommand):
    """Load SAMLv2 metadata file into the LibertyProvider, LibertyServiceProvider
    and LibertyIdentityProvider files"""

    can_import_django_settings = True
    output_transaction = True
    requires_system_checks = '__all__'

    help = 'Load the specified SAMLv2 metadata file'

    def add_arguments(self, parser):
        parser.add_argument('metadata_file_path')
        parser.add_argument('--idp', action='store_true', dest='idp', default=False, help='Do nothing')
        parser.add_argument('--sp', action='store_true', dest='sp', default=False, help='Do nothing')
        parser.add_argument(
            '--sp-policy', dest='sp_policy', default=None, help='SAML2 service provider options policy'
        )
        parser.add_argument(
            '--delete',
            action='store_true',
            dest='delete',
            default=False,
            help='Delete all providers defined in the metadata file (kind of uninstall)',
        )
        parser.add_argument(
            '--ignore-errors',
            action='store_true',
            dest='ignore-errors',
            default=False,
            help='If loading of one EntityDescriptor fails, continue loading',
        )
        parser.add_argument(
            '--source',
            dest='source',
            default=None,
            help=(
                'Tag the loaded providers with the given source string, existing providers with the same tag'
                ' will be removed if they do not exist anymore in the metadata file.'
            ),
        )
        parser.add_argument(
            '--reset-attributes',
            action='store_true',
            default=False,
            help=(
                'When loading shibboleth attribute filter policies, start by removing all existing SAML'
                ' attributes for each provider'
            ),
        )
        parser.add_argument(
            '--dont-load-attribute-consuming-service',
            dest='load_attribute_consuming_service',
            default=True,
            action='store_false',
            help=(
                'Prevent loading of the attribute policy from AttributeConsumingService nodes in the metadata'
                ' file.'
            ),
        )
        parser.add_argument(
            '--shibboleth-attribute-filter-policy',
            dest='attribute-filter-policy',
            default=None,
            help='''Path to a file containing an Attribute Filter Policy for the
Shibboleth IdP, that will be used to configure SAML attributes for
each provider. The following schema is supported:

    <AttributeFilterPolicy id="<whatever>">
        <PolicyRequirementRule xsi:type="basic:AttributeRequesterString" value="<entityID>" >
        [
          <AttributeRule attributeID="<attribute-name>">
                <PermitValueRule xsi:type="basic:ANY"/>
          </AttributeRule>
        ]*
    </AttributeFilterPolicy>

Any other kind of attribute filter policy is unsupported.
''',
        )
        parser.add_argument(
            '--create-disabled',
            dest='create-disabled',
            action='store_true',
            default=False,
            help='When creating a new provider, make it disabled by default.',
        )

    @atomic
    def handle(self, *args, **options):
        verbosity = int(options['verbosity'])
        source = options['source']
        metadata_file_path = options['metadata_file_path']
        if metadata_file_path.startswith('http://') or metadata_file_path.startswith('https://'):
            response = requests.get(metadata_file_path, timeout=settings.REQUESTS_TIMEOUT)
            if not response.ok:
                raise CommandError('Unable to open url %s' % metadata_file_path)
            metadata_file = io.BytesIO(response.content)
        else:
            try:
                with open(metadata_file_path, 'rb') as fd:
                    metadata_file = io.BytesIO(fd.read())
            except OSError:
                raise CommandError('Unable to open file %s' % metadata_file_path)

        try:
            doc = etree.parse(metadata_file)
        except Exception as e:
            raise CommandError('XML parsing error: %s' % str(e))
        if doc.getroot().tag == ENTITY_DESCRIPTOR_TN:
            load_one_entity(doc.getroot(), options)
        elif doc.getroot().tag == ENTITIES_DESCRIPTOR_TN:
            afp = None
            if 'attribute-filter-policy' in options and options['attribute-filter-policy']:
                path = options['attribute-filter-policy']
                if not os.path.isfile(path):
                    raise CommandError('No attribute filter policy file %s' % path)
                afp = parse_attribute_filters_file(options['attribute-filter-policy'])
            sp_policy = None
            if 'sp_policy' in options and options['sp_policy']:
                sp_policy_name = options['sp_policy']
                try:
                    sp_policy = SPOptionsIdPPolicy.objects.get(name=sp_policy_name)
                    if verbosity > 1:
                        print(
                            'Service providers are set with the following SAML2 options policy: %s'
                            % sp_policy
                        )
                except SPOptionsIdPPolicy.DoesNoextExist:
                    if verbosity > 0:
                        print(
                            _('SAML2 service provider options policy with name %s not found')
                            % sp_policy_name,
                            file=sys.stderr,
                        )
                        raise CommandError()
            else:
                if verbosity > 1:
                    print('No SAML2 service provider options policy provided')
            loaded = []
            if doc.getroot().tag == ENTITY_DESCRIPTOR_TN:
                entity_descriptors = [doc.getroot()]
            else:
                entity_descriptors = doc.getroot().findall(ENTITY_DESCRIPTOR_TN)
            for entity_descriptor in entity_descriptors:
                try:
                    load_one_entity(entity_descriptor, options, sp_policy=sp_policy, afp=afp)
                    loaded.append(entity_descriptor.get(ENTITY_ID))
                except Exception:
                    if not options['ignore-errors']:
                        raise
                    if verbosity > 0:
                        print(
                            _('Failed to load entity descriptor for %s') % entity_descriptor.get(ENTITY_ID),
                            file=sys.stderr,
                        )
                    raise CommandError()
            if options['source']:
                if options['delete']:
                    print('Finally delete all providers for source: %s...' % source)
                    LibertyProvider.objects.filter(federation_source=source).delete()
                else:
                    to_delete = []
                    for provider in LibertyProvider.objects.filter(federation_source=source):
                        if provider.entity_id not in loaded:
                            to_delete.append(provider)
                    for provider in to_delete:
                        if verbosity > 1:
                            print(_('Deleted obsolete provider %s') % provider.entity_id)
                        provider.delete()
        else:
            raise CommandError('%s is not a SAMLv2 metadata file' % metadata_file)
        if not options.get('delete'):
            if verbosity > 1:
                print('Loaded', options.get('count', 0), 'providers')
