# lingo - payment and billing system
# Copyright (C) 2022  Entr'ouvert
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
import datetime
import hashlib
import hmac
import random
import urllib.parse

from django.utils.encoding import smart_bytes
from django.utils.http import quote, urlencode


def sign_url(url, key, algo='sha256', timestamp=None, nonce=None):
    parsed = urllib.parse.urlparse(url)
    new_query = sign_query(parsed.query, key, algo, timestamp, nonce)
    return urllib.parse.urlunparse(parsed[:4] + (new_query,) + parsed[5:])


def sign_query(query, key, algo='sha256', timestamp=None, nonce=None):
    if timestamp is None:
        timestamp = datetime.datetime.utcnow()
    timestamp = timestamp.strftime('%Y-%m-%dT%H:%M:%SZ')
    if nonce is None:
        nonce = hex(random.getrandbits(128))[2:]
    new_query = query
    if new_query:
        new_query += '&'
    new_query += urlencode((('algo', algo), ('timestamp', timestamp), ('nonce', nonce)))
    signature = base64.b64encode(sign_string(new_query, key, algo=algo))
    new_query += '&signature=' + quote(signature)
    return new_query


def sign_string(s, key, algo='sha256', timedelta=30):
    digestmod = getattr(hashlib, algo)
    hash = hmac.HMAC(smart_bytes(key), digestmod=digestmod, msg=smart_bytes(s))
    return hash.digest()
