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

import base64
import os
import subprocess
import tempfile

_openssl = 'openssl'


def decapsulate_pem_file(file_or_string):
    '''Remove PEM header lines'''
    if not isinstance(file_or_string, str):
        content = file_or_string.read()
    else:
        content = file_or_string
    i = content.find('--BEGIN')
    j = content.find('\n', i)
    k = content.find('--END', j)
    l = content.rfind('\n', 0, k)  # noqa: E741
    return content[j + 1 : l]


def _call_openssl(args):
    """Use subprocees to spawn an openssl process

    Return a tuple made of the return code and the stdout output
    """
    try:
        with subprocess.Popen(
            args=[_openssl] + args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True
        ) as process:
            output = process.communicate()[0]
            return process.returncode, output
    except OSError:
        return 1, None


def check_key_pair_consistency(publickey=None, privatekey=None):
    """Check if two PEM key pair whether they are publickey or certificate, are
    well formed and related.
    """
    if not publickey or not privatekey:
        return None

    with (
        tempfile.NamedTemporaryFile(mode='w') as privatekey_file,
        tempfile.NamedTemporaryFile(mode='w') as publickey_file,
    ):
        privatekey_file.write(privatekey)
        privatekey_file.flush()
        publickey_file.write(publickey)
        publickey_file.flush()

        if 'BEGIN CERTIFICATE' in publickey:
            rc1, modulus1 = _call_openssl(['x509', '-in', publickey_file.name, '-noout', '-modulus'])
        else:
            rc1, modulus1 = _call_openssl(['rsa', '-pubin', '-in', publickey_file.name, '-noout', '-modulus'])
            if rc1 != 0:
                rc1, modulus1 = _call_openssl(
                    ['dsa', '-pubin', '-in', publickey_file.name, '-noout', '-modulus']
                )

        if rc1 != 0:
            return False

        rc2, modulus2 = _call_openssl(['rsa', '-in', privatekey_file.name, '-noout', '-modulus'])
        if rc2 != 0:
            rc2, modulus2 = _call_openssl(['dsa', '-in', privatekey_file.name, '-noout', '-modulus'])

        return bool(rc1 == 0 and rc2 == 0 and modulus1 == modulus2)


def generate_rsa_keypair(numbits=1024):
    """Generate simple RSA public and private key files"""
    with (
        tempfile.NamedTemporaryFile(mode='r') as privatekey_file,
        tempfile.NamedTemporaryFile(mode='r') as publickey_file,
    ):
        rc1, _ = _call_openssl(['genrsa', '-out', privatekey_file.name, '-passout', 'pass:', str(numbits)])
        if rc1 != 0:
            raise Exception('Failed to generate a key')
        rc2, _ = _call_openssl(['rsa', '-in', privatekey_file.name, '-pubout', '-out', publickey_file.name])
        if rc2 != 0:
            raise Exception('Failed to generate a key')
        return (publickey_file.read(), privatekey_file.read())


def get_rsa_public_key_modulus(publickey):
    with tempfile.NamedTemporaryFile(mode='w') as publickey_file:
        publickey_file.write(publickey)
        publickey_file.flush()

        if 'BEGIN PUBLIC' in publickey:
            rc, modulus = _call_openssl(['rsa', '-pubin', '-in', publickey_file.name, '-noout', '-modulus'])
        elif 'BEGIN RSA PRIVATE KEY' in publickey:
            rc, modulus = _call_openssl(['rsa', '-in', publickey_file.name, '-noout', '-modulus'])
        elif 'BEGIN CERTIFICATE' in publickey:
            rc, modulus = _call_openssl(['x509', '-in', publickey_file.name, '-noout', '-modulus'])
        else:
            return None

        i = modulus.find('=')

        if rc == 0 and i:
            return int(modulus[i + 1 :].strip(), 16)
    return None


def get_rsa_public_key_exponent(publickey):
    with tempfile.NamedTemporaryFile(mode='w') as publickey_file:
        publickey_file.write(publickey)
        publickey_file.flush()

        _exponent = 'Exponent: '
        if 'BEGIN PUBLIC' in publickey:
            rc, modulus = _call_openssl(['rsa', '-pubin', '-in', publickey_file.name, '-noout', '-text'])
        elif 'BEGIN RSA PRIVATE' in publickey:
            rc, modulus = _call_openssl(['rsa', '-in', publickey_file.name, '-noout', '-text'])
            _exponent = 'publicExponent: '
        elif 'BEGIN CERTIFICATE' in publickey:
            rc, modulus = _call_openssl(['x509', '-in', publickey_file.name, '-noout', '-text'])
        else:
            return None
        i = modulus.find(_exponent)
        j = modulus.find('(', i)
        if rc == 0 and i and j:
            return int(modulus[i + len(_exponent) : j].strip())
    return None


def can_generate_rsa_key_pair():
    syspath = os.environ.get('PATH')
    if syspath:
        for base in syspath.split(':'):
            if os.path.exists(os.path.join(base, 'openssl')):
                return True
    else:
        return False


def int_to_cryptobinary(integer):
    # ref: https://www.w3.org/TR/xmldsig-core1/#sec-CryptoBinary
    byte_length = (integer.bit_length() + 7) // 8
    integer_bytes = integer.to_bytes(byte_length, byteorder='big')
    return base64.b64encode(integer_bytes).decode('ascii')


def get_xmldsig_rsa_key_value(publickey):
    mod = get_rsa_public_key_modulus(publickey)
    exp = get_rsa_public_key_exponent(publickey)
    return (
        '<RSAKeyValue'
        ' xmlns="http://www.w3.org/2000/09/xmldsig#">\n\t<Modulus>%s</Modulus>\n\t<Exponent>%s</Exponent>\n</RSAKeyValue>'
        % (
            int_to_cryptobinary(mod),
            int_to_cryptobinary(exp),
        )
    )
