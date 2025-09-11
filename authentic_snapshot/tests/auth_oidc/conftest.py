# authentic2 - versatile identity manager
# Copyright (C) Entr'ouvert

import pytest
import responses
from jwcrypto.jwk import JWK, JWKSet

KID_RSA = '1e9gdk7'
KID_EC = 'jb20Cg8'


@pytest.fixture
def kid_rsa():
    return KID_RSA


@pytest.fixture
def kid_ec():
    return KID_EC


@pytest.fixture
def jwkset(kid_rsa, kid_ec):
    key_rsa = JWK.generate(kty='RSA', size=1024, kid=kid_rsa)
    key_ec = JWK.generate(kty='EC', size=256, kid=kid_ec)
    jwkset = JWKSet()
    jwkset.add(key_rsa)
    jwkset.add(key_ec)
    return jwkset


@pytest.fixture
def jwkset_url(jwkset):
    jwkset_url = 'https://www.example.com/common/discovery/v3.0/keys'
    responses.get(jwkset_url, json=jwkset.export(as_dict=True))
    yield jwkset_url
