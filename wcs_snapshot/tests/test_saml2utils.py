from quixote import cleanup

from wcs.qommon import x509utils
from wcs.qommon.saml2utils import Metadata

from .utilities import clean_temporary_pub, create_temporary_pub


def setup_module(module):
    cleanup()
    global pub
    pub = create_temporary_pub()


def teardown_module(module):
    clean_temporary_pub()


def test_metadata_generation():
    pkey, _ = x509utils.generate_rsa_keypair()
    meta = Metadata(
        publisher=pub,
        config={
            'organization_name': 'foobar',
            'saml2_base_url': 'saml2_base_url',
            'saml2_base_soap_url': 'saml2_base_soap_url',
            'authn-request-signed': False,
        },
        provider_id='provider_id_1',
    )
    assert meta is not None
    content = meta.get_saml2_metadata(pkey, '', True, True)
    assert isinstance(content, str) and content != ''
    assert 'EntityDescriptor' in content
    assert 'SPSSODescriptor' in content
