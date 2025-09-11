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

import datetime
import random
import uuid

import pytest
from django.utils.encoding import force_bytes

from authentic2.utils import crypto

key = b'1234'


def test_idempotency():
    for _ in range(10):
        s = force_bytes(str(random.getrandbits(1024)))
        assert crypto.aes_base64_decrypt(key, crypto.aes_base64_encrypt(key, s)) == s


def test_exceptions():
    with pytest.raises(crypto.DecryptionError):
        crypto.aes_base64_decrypt(key, 'xxxx')
    with pytest.raises(crypto.DecryptionError):
        crypto.aes_base64_decrypt(key, 'xxx$y')
    assert crypto.aes_base64_decrypt(key, 'xxxx', raise_on_error=False) is None
    assert crypto.aes_base64_decrypt(key, 'xxx$y', raise_on_error=False) is None


def test_padding():
    from Cryptodome import Random

    for i in range(1, 100):
        for j in range(2, 32):
            msg = Random.get_random_bytes(i)
            assert crypto.remove_padding(crypto.add_padding(msg, j), j) == msg


def test_deterministic_encryption():
    salt = b'4567'
    raw = uuid.uuid4().bytes

    for hash_name in ['md5', 'sha1', 'sha256', 'sha384', 'sha512']:
        for count in [1, 50]:
            crypted1 = crypto.aes_base64url_deterministic_encrypt(
                key, raw, salt, hash_name=hash_name, count=count
            )
            crypted2 = crypto.aes_base64url_deterministic_encrypt(
                key, raw, salt, hash_name=hash_name, count=count
            )
            assert crypted1 == crypted2
            assert crypto.aes_base64url_deterministic_decrypt(key, crypted1, salt, max_count=count) == raw


def test_hmac_url():
    key = 'é'
    url = 'https://example.invalid/'
    assert crypto.check_hmac_url(key, url, crypto.hmac_url(key, url))
    key = 'é'
    url = 'https://example.invalid/\u0000'
    assert crypto.check_hmac_url(key, url, crypto.hmac_url(key, url))


def test_dumps_loads(settings, freezer):
    data = {'a': 1, 'b': 'foo', 'bar': 'zib@!$#$#$#$#'}

    token = crypto.dumps(data)
    assert token.encode('ascii')
    assert crypto.loads(token) == data
    settings.SECRET_KEY = 'bb'
    with pytest.raises(crypto.BadSignature):
        assert crypto.loads(token)

    token = crypto.dumps(data, key='aa')
    with pytest.raises(crypto.BadSignature):
        assert crypto.loads(token)
    assert crypto.loads(token, key='aa') == data

    freezer.move_to(datetime.timedelta(seconds=100))
    with pytest.raises(crypto.SignatureExpired):
        crypto.loads(token, key='aa', max_age=10)
    assert crypto.loads(token, key='aa') == data


def test_dumps_loads_retrocompatibility():
    from django.core import signing

    data = {'a': 1, 'b': 'foo', 'bar': 'zib@!$#$#$#$#'}
    token = signing.dumps(data)
    assert crypto.loads(token) == data
