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
# authentic2

from authentic2.saml.x509utils import (
    can_generate_rsa_key_pair,
    check_key_pair_consistency,
    decapsulate_pem_file,
    generate_rsa_keypair,
    get_rsa_public_key_exponent,
    get_rsa_public_key_modulus,
    get_xmldsig_rsa_key_value,
)


def test_x509utils():
    assert can_generate_rsa_key_pair()
    publickey, privatekey = generate_rsa_keypair()
    assert publickey is not None and privatekey is not None
    assert check_key_pair_consistency(publickey, privatekey)
    _, privatekey = generate_rsa_keypair()
    assert not check_key_pair_consistency(publickey, privatekey)
    assert get_xmldsig_rsa_key_value(publickey) is not None
    assert get_rsa_public_key_modulus(publickey) is not None
    assert get_rsa_public_key_exponent(publickey) is not None
    # Certificate/key generated using
    # openssl req -x509 -newkey rsa:1024 -keyout key.pem -out req.pem
    cert = '''-----BEGIN CERTIFICATE-----
MIICHjCCAYegAwIBAgIJALgmNSS3spUaMA0GCSqGSIb3DQEBBQUAMBUxEzARBgNV
BAoTCkVudHJvdXZlcnQwHhcNMDkxMDI4MjIwODEzWhcNMDkxMTI3MjIwODEzWjAV
MRMwEQYDVQQKEwpFbnRyb3V2ZXJ0MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKB
gQCtTbDTe/LrD+gvK0Sgf/rnvAg4zcc/vJcEdsiGsJ3shTse7OPf5fIaD7lry+jm
tFX61n8Rn1d1iw+whuYbrG6R3OhDw50vufb2RrRSHBOA7CcfiKQD6CT2p31msv+C
iHbGmoHRFyt2CnRGy2FCX2Oizf5qxfjHaJEXu0tk/SdN2QIDAQABo3YwdDAdBgNV
HQ4EFgQUlDrrh8KudeyeInXqios+Rdf9tQAwRQYDVR0jBD4wPIAUlDrrh8Kudeye
InXqios+Rdf9tQChGaQXMBUxEzARBgNVBAoTCkVudHJvdXZlcnSCCQC4JjUkt7KV
GjAMBgNVHRMEBTADAQH/MA0GCSqGSIb3DQEBBQUAA4GBAFHXBDW13NIiafS2cRP1
/KAMIfnB/kYINTUU7iv2oIOYtfpVR9yMmnLIVxTyN3rCWb7UV/ICkMotTHmKLDT8
Rp7tKc0zTQ+CQGFVYvfRAlz4kgW14DDx/oIBqr/yDv5mInFb8reSfP85cPrXp/wR
ufewZ2WHikP2kWoHWDkw8MDd
-----END CERTIFICATE-----'''
    key = '''-----BEGIN RSA PRIVATE KEY-----
MIICXgIBAAKBgQCtTbDTe/LrD+gvK0Sgf/rnvAg4zcc/vJcEdsiGsJ3shTse7OPf
5fIaD7lry+jmtFX61n8Rn1d1iw+whuYbrG6R3OhDw50vufb2RrRSHBOA7CcfiKQD
6CT2p31msv+CiHbGmoHRFyt2CnRGy2FCX2Oizf5qxfjHaJEXu0tk/SdN2QIDAQAB
AoGBAKlFVQ17540JAHPyAxnxZxSpaC5zb8YlYiwOCVblc5rtlw1hvEGYy5wA987+
YAHW6pQSphKEXFyG81Asst0c0vExgGVFjzAy/GFrBTnl0l5PtwPDDIAmGP6DQw4C
lOHJePloKp0xjCo2nJ8XluxkPp1+XtJyJOhZWpQPDvF3uL+xAkEA3t58jg0SV55s
E10R04QOJB0qIB9U4Nw29uhh5RXv8JRq41pw4iDmpi9I67nGqDeuxlDUQ/+5rLOE
Ptp07BsFWwJBAMcQ7wiwhIYtRC8ff3WbWX9wcABDyX47uYvAMIiaEOmFmJyI41mW
xlik821Aaid1Z45vgBN32hYkEbpWaaIVe9sCQQCX7mpQ2F5ptskMhkTxwbN2MR+X
mGRfiiA6P/8EkejpQ/R+GxibPzydi9yVPidMY/FUpqOd24YzUonT408T6fPDAkEA
pkkt86tIOLEtaNO97CcF/t+Un5QAh9MqLmQv5pwUDo4Lqo7qo1bAfyHjOlr5kdaP
17qqWRjf82jT6jzu5nddywJAVQpxlZ8fIZUzTD2mRQeLf5O+rXmtH1LlwRRGCNaa
8eM47A92x9uplD/sN550pTKM7XLhHBvEfLujUoGHpWQxGA==
-----END RSA PRIVATE KEY-----'''
    assert check_key_pair_consistency(cert, key)
    assert (
        get_xmldsig_rsa_key_value(cert)
        == '''\
<RSAKeyValue xmlns="http://www.w3.org/2000/09/xmldsig#">
	<Modulus>rU2w03vy6w/oLytEoH/657wIOM3HP7yXBHbIhrCd7IU7Huzj3+XyGg+5a8vo5rRV+tZ/EZ9XdYsPsIbmG6xukdzoQ8OdL7n29ka0UhwTgOwnH4ikA+gk9qd9ZrL/goh2xpqB0Rcrdgp0RsthQl9jos3+asX4x2iRF7tLZP0nTdk=</Modulus>
	<Exponent>AQAB</Exponent>
</RSAKeyValue>'''
    )
    assert get_rsa_public_key_modulus(cert) is not None
    assert get_rsa_public_key_exponent(cert) is not None
    assert len(decapsulate_pem_file(key).splitlines()) == len(key.splitlines()) - 2
