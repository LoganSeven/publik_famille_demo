# w.c.s. - web application for online forms
# Copyright (C) 2005-2010  Entr'ouvert
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, see <http://www.gnu.org/licenses/>.

import os

from . import force_str, x509utils


def bool2xs(boolean):
    '''Convert a boolean value to XSchema boolean representation'''
    if boolean is True:
        return 'true'
    if boolean is False:
        return 'false'
    raise TypeError()


class Metadata:
    internal_endpoints = {'slo': 'singleLogout', 'ac': 'assertionConsumer'}

    def __init__(self, publisher, provider_id, config):
        self.publisher = publisher
        self.provider_id = provider_id
        self.config = config

    def get_key_descriptor(self, keytype, key):
        '''Format key as an XML Dsig KeyNode content'''
        if keytype:
            prologue = '    <KeyDescriptor use="%s">' % keytype
        else:
            prologue = '    <KeyDescriptor>'
        if key and 'CERTIF' in key:
            naked = x509utils.decapsulate_pem_file(key)
            return (
                prologue
                + '''
      <ds:KeyInfo xmlns:ds="http://www.w3.org/2000/09/xmldsig#">
        <ds:X509Data><ds:X509Certificate>%s</ds:X509Certificate></ds:X509Data>
      </ds:KeyInfo>
    </KeyDescriptor>
'''
                % naked
            )
        # FIXME: generate proper RSAKeyValue, but wait for support in Lasso
        if key and 'KEY' in key:
            naked = x509utils.get_xmldsig_rsa_key_value(key)
            return (
                prologue
                + '''
      <ds:KeyInfo xmlns:ds="http://www.w3.org/2000/09/xmldsig#">
        %s
      </ds:KeyInfo>
    </KeyDescriptor>
'''
                % naked
            )
        return ''

    def get_key_descriptors(self, signing_pem_key, encryption_pem_key):
        signing_pem_key, encryption_pem_key = self.get_new_or_old_keys(signing_pem_key, encryption_pem_key)
        sp_key = {
            'signing': self.get_key_descriptor('signing', signing_pem_key),
            'encryption': self.get_key_descriptor('encryption', encryption_pem_key),
        }
        if not sp_key['signing'] and sp_key['encryption']:
            sp_key = {'signing': '', 'encryption': self.get_key_descriptor('', encryption_pem_key)}
        if sp_key['signing'] and not sp_key['encryption']:
            sp_key = {'signing': self.get_key_descriptor('', signing_pem_key), 'encryption': ''}
        return sp_key

    def get_new_or_old_keys(self, signing_pem_key, encryption_pem_key):
        '''Return new or earlier version of PEM keys'''
        _dir = self.publisher.app_dir
        if not signing_pem_key and self.config.get('publickey'):
            with open(os.path.join(_dir, 'public-key.pem')) as fd:
                signing_pem_key = fd.read()
        if not encryption_pem_key and self.config.get('encryption_publickey'):
            with open(os.path.join(_dir, 'encryption-public-key.pem')) as fd:
                encryption_pem_key = fd.read()
        return (signing_pem_key, encryption_pem_key)

    def get_spsso_descriptor(self, signing_pem_key, encryption_pem_key, endpoints):
        signing_pem_key, encryption_pem_key = self.get_new_or_old_keys(signing_pem_key, encryption_pem_key)

        authnrequestsigned = bool2xs(self.config.get('authn-request-signed', True))
        wantassertionsigned = bool2xs(self.config.get('want-assertion-signed', True))
        prologue = '''  <SPSSODescriptor
    AuthnRequestsSigned="%s" WantAssertionsSigned="%s"
    protocolSupportEnumeration="urn:oasis:names:tc:SAML:2.0:protocol">
''' % (
            authnrequestsigned,
            wantassertionsigned,
        )
        sp_key = self.get_key_descriptors(signing_pem_key, encryption_pem_key)
        config = {}
        config.update(self.config)
        config.update(self.internal_endpoints)
        config.update(endpoints)
        return (
            prologue
            + sp_key['signing']
            + sp_key['encryption']
            + '''
    <SingleLogoutService
      Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect"
      Location="%(saml2_base_url)s/%(slo)s"
      ResponseLocation="%(saml2_base_url)s/%(slo)sReturn" />
    <SingleLogoutService
      Binding="urn:oasis:names:tc:SAML:2.0:bindings:SOAP"
      Location="%(saml2_base_url)s/%(slo)sSOAP" />
    <AssertionConsumerService isDefault="true" index="0"
      Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Artifact"
      Location="%(saml2_base_url)s/%(ac)sArtifact" />
    <AssertionConsumerService index="1"
      Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST"
      Location="%(saml2_base_url)s/%(ac)sPost" />
    <AssertionConsumerService index="3"
      Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect"
      Location="%(saml2_base_url)s/%(ac)sRedirect" />
  </SPSSODescriptor>'''
            % config
        )

    def get_idpsso_descriptor(self, signing_pem_key, encryption_pem_key):
        signing_pem_key, encryption_pem_key = self.get_new_or_old_keys(signing_pem_key, encryption_pem_key)

        idp_key = self.get_key_descriptors(signing_pem_key, encryption_pem_key)
        idp_head = """
  <IDPSSODescriptor
      WantAuthnRequestsSigned="%s"
      protocolSupportEnumeration="urn:oasis:names:tc:SAML:2.0:protocol">
""" % bool2xs(
            self.config.get('want-authn-request-signed', True)
        )
        idp_body = (
            """
    <ArtifactResolutionService isDefault="true" index="0"
      Binding="urn:oasis:names:tc:SAML:2.0:bindings:SOAP"
      Location="%(saml2_base_soap_url)s/artifact" />
    <SingleLogoutService
      Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect"
      Location="%(saml2_base_url)s/singleLogout"
      ResponseLocation="%(saml2_base_url)s/singleLogoutReturn" />
    <SingleLogoutService
      Binding="urn:oasis:names:tc:SAML:2.0:bindings:SOAP"
      Location="%(saml2_base_soap_url)s/singleLogoutSOAP" />
    <SingleSignOnService
      Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect"
      Location="%(saml2_base_url)s/singleSignOn" />
    <SingleSignOnService
      Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST"
      Location="%(saml2_base_url)s/singleSignOn" />
    <SingleSignOnService
      Binding="urn:oasis:names:tc:SAML:2.0:bindings:SOAP"
      Location="%(saml2_base_soap_url)s/singleSignOnSOAP" />
  </IDPSSODescriptor>"""
            % self.config
        )
        return idp_head + idp_key['signing'] + idp_key['encryption'] + idp_body

    def get_saml2_metadata(
        self, signing_pem_key='', encryption_pem_key='', do_idp=False, do_sp=False, endpoints=None
    ):
        endpoints = endpoints or {}
        prologue = (
            '''<?xml version="1.0"?>
<EntityDescriptor xmlns="urn:oasis:names:tc:SAML:2.0:metadata"
    xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion"
    xmlns:ds="http://www.w3.org/2000/09/xmldsig#"
    entityID="%s">'''
            % self.provider_id
        )

        sp_descriptor = ''
        if do_sp:
            sp_descriptor = self.get_spsso_descriptor(signing_pem_key, encryption_pem_key, endpoints)

        idp_descriptor = ''
        if do_idp:
            idp_descriptor = self.get_idpsso_descriptor(signing_pem_key, encryption_pem_key)

        orga = ''
        if self.config.get('organization_name'):
            orga = '''<Organization>
   <OrganizationName xml:lang="en">%s</OrganizationName>
</Organization>''' % force_str(
                self.config['organization_name']
            )

        epilogue = '</EntityDescriptor>'

        return '\n'.join([prologue, sp_descriptor, idp_descriptor, orga, epilogue])
