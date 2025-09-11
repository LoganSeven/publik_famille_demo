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
import hashlib
import hmac
import struct
import uuid
from binascii import Error as Base64Error

from Cryptodome import Random
from Cryptodome.Cipher import AES
from Cryptodome.Hash import HMAC, SHA256
from Cryptodome.Protocol.KDF import PBKDF2
from django.conf import settings
from django.core import signing
from django.core.signing import BadSignature, SignatureExpired  # pylint: disable=unused-import
from django.utils.crypto import constant_time_compare
from django.utils.encoding import force_bytes


class DecryptionError(Exception):
    pass


def new_base64url_id():
    return base64url_encode(uuid.uuid4().bytes).decode('ascii')


def base64url_decode(raw):
    rem = len(raw) % 4
    if rem > 0:
        raw += b'=' * (4 - rem)
    return base64.urlsafe_b64decode(raw)


def base64url_encode(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b'=')


def get_hashclass(name):
    if name in ['md5', 'sha1', 'sha256', 'sha384', 'sha512']:
        return getattr(hashlib, name)
    return None


def aes_base64_encrypt(key, data, urlsafe=False, sep=b'$'):
    """Generate an AES key from any key material using PBKDF2, and encrypt data using CFB mode. A
    new IV is generated each time, the IV is also used as salt for PBKDF2.
    """
    iv = Random.get_random_bytes(16)
    aes_key = PBKDF2(key, iv)
    aes = AES.new(aes_key, AES.MODE_CFB, iv=iv)
    crypted = aes.encrypt(data)
    if urlsafe:
        return b'%s%s%s' % (base64url_encode(iv), sep, base64url_encode(crypted))
    else:
        return b'%s%s%s' % (base64.b64encode(iv), sep, base64.b64encode(crypted))


def aes_base64_decrypt(key, payload, raise_on_error=True, urlsafe=False, sep=b'$'):
    '''Decrypt data encrypted with aes_base64_encrypt'''
    if not isinstance(payload, bytes):
        try:
            payload = payload.encode('ascii')
        except Exception:
            raise DecryptionError('payload is not an ASCII string')
    try:
        iv, crypted = payload.split(sep)
    except (ValueError, TypeError):
        if raise_on_error:
            raise DecryptionError('bad payload')
        return None

    if urlsafe:
        decode = base64url_decode
    else:
        decode = base64.b64decode

    try:
        iv = decode(iv)
        crypted = decode(crypted)
    except Base64Error:
        if raise_on_error:
            raise DecryptionError('incorrect base64 encoding')
        return None
    aes_key = PBKDF2(key, iv)
    aes = AES.new(aes_key, AES.MODE_CFB, iv=iv)
    return aes.decrypt(crypted)


def add_padding(msg, block_size):
    '''Pad message with zero bytes to match block_size'''
    pad_length = block_size - (len(msg) + 2) % block_size
    padded = struct.pack('<h%ds%ds' % (len(msg), pad_length), len(msg), msg, b'\0' * pad_length)
    assert len(padded) % block_size == 0
    return padded


def remove_padding(msg, block_size):
    '''Ignore padded zero bytes'''
    try:
        (msg_length,) = struct.unpack('<h', msg[:2])
    except struct.error:
        raise DecryptionError('wrong padding')
    if len(msg) % block_size != 0:
        raise DecryptionError('message length is not a multiple of block size', len(msg), block_size)
    unpadded = msg[2 : 2 + msg_length]
    if msg_length > len(msg) - 2:
        raise DecryptionError('wrong padding')
    if len(msg[2 + msg_length :].strip(force_bytes('\0'))):
        raise DecryptionError('padding is not all zero')
    if len(unpadded) != msg_length:
        raise DecryptionError('wrong padding')
    return unpadded


def aes_base64url_deterministic_encrypt(key, data, salt, hash_name='sha256', count=1):
    """Encrypt using AES-128 and sign using HMAC-SHA256 shortened to 64 bits.

    Count and algorithm are encoded in the final string for future evolution.

    """
    mode = 1  # AES128-SHA256
    hashmod = SHA256
    key_size = 16
    hmac_size = key_size

    if isinstance(salt, str):
        salt = force_bytes(salt)
    iv = hashmod.new(salt).digest()

    def prf(secret, salt):
        return HMAC.new(secret, salt, hashmod).digest()

    aes_key = PBKDF2(key, iv, dkLen=key_size, count=count, prf=prf)

    key_size = len(aes_key)

    aes = AES.new(aes_key, AES.MODE_CBC, iv[:key_size])

    crypted = aes.encrypt(add_padding(data, key_size))

    hmac = prf(key, crypted)[:hmac_size]

    raw = struct.pack('<2sBH', b'a2', mode, count) + crypted + hmac
    return base64url_encode(raw)


def aes_base64url_deterministic_decrypt(key, urlencoded, salt, raise_on_error=True, max_count=1):
    mode = 1  # AES128-SHA256
    hashmod = SHA256
    key_size = 16
    hmac_size = key_size

    def prf(secret, salt):
        return HMAC.new(secret, salt, hashmod).digest()

    try:
        try:
            raw = base64url_decode(urlencoded)
        except Exception as e:
            raise DecryptionError('base64 decoding failed', e)
        try:
            magic, mode, count = struct.unpack('<2sBH', raw[:5])
        except struct.error as e:
            raise DecryptionError('invalid packing', e)
        if magic != b'a2':
            raise DecryptionError('invalid magic string', magic)
        if mode != 1:
            raise DecryptionError('mode is not AES128-SHA256', mode)
        if count > max_count:
            raise DecryptionError('count is too big', count)

        crypted, hmac = raw[5:-hmac_size], raw[-hmac_size:]

        if not crypted or not hmac or prf(key, crypted)[:hmac_size] != hmac:
            raise DecryptionError('invalid HMAC')

        if isinstance(salt, str):
            salt = force_bytes(salt)
        iv = hashmod.new(salt).digest()

        aes_key = PBKDF2(key, iv, dkLen=key_size, count=count, prf=prf)

        aes = AES.new(aes_key, AES.MODE_CBC, iv[:key_size])

        data = remove_padding(aes.decrypt(crypted), key_size)

        return data
    except DecryptionError:
        if not raise_on_error:
            return None
        raise


def hmac_url(key, url):
    if hasattr(key, 'encode'):
        key = key.encode()
    if hasattr(url, 'encode'):
        url = url.encode()
    return (
        base64.b32encode(hmac.HMAC(key=key, msg=url, digestmod=hashlib.sha256).digest())
        .decode('ascii')
        .strip('=')
    )


def check_hmac_url(key, url, signature):
    if hasattr(signature, 'decode'):
        signature = signature.decode()
    return constant_time_compare(signature, hmac_url(key, url).encode('ascii'))


def hash_chain(n, seed=None, encoded_seed=None):
    '''Generate a chain of hashes'''
    if encoded_seed:
        seed = base64url_decode(encoded_seed.encode())
    if hasattr(seed, 'encode'):
        seed = seed.encode()
    if seed is None:
        seed = Random.get_random_bytes(16)
    chain = [seed]
    for dummy in range(n - 1):
        chain.append(hashlib.sha256(chain[-1] + settings.SECRET_KEY.encode()).digest())
    return [base64url_encode(x).decode('ascii') for x in chain]


def dumps(obj, key=None, **kwargs):
    if not key:
        key = settings.SECRET_KEY
    return aes_base64_encrypt(
        key.encode(), signing.dumps(obj, key=key, **kwargs).encode(), urlsafe=True, sep=b':'
    ).decode()


def loads(s, key=None, **kwargs):
    if not key:
        key = settings.SECRET_KEY
    try:
        decrypted = aes_base64_decrypt(key.encode(), s.encode(), urlsafe=True, sep=b':')
    except DecryptionError:
        return signing.loads(s, key=key, **kwargs)
    try:
        decrypted = decrypted.decode()
    except UnicodeDecodeError:
        raise BadSignature
    return signing.loads(decrypted, key=key, **kwargs)
