import base64
import datetime
import hashlib
import hmac
import urllib.parse

from django.utils.encoding import force_bytes


def sign_uri(uri, user=None, format='json', orig='coucou', key='1234'):
    timestamp = datetime.datetime.now(datetime.UTC).isoformat()[:19] + 'Z'
    scheme, netloc, path, params, query, fragment = urllib.parse.urlparse(uri)
    if query:
        query += '&'
    if format:
        query += 'format=%s&' % format
    query += 'orig=%s&algo=sha256&timestamp=%s' % (orig, timestamp)
    if user:
        query += '&email=' + urllib.parse.quote(user.email)
    query += '&signature=%s' % urllib.parse.quote(
        base64.b64encode(hmac.new(force_bytes(key), force_bytes(query), hashlib.sha256).digest())
    )
    return urllib.parse.urlunparse((scheme, netloc, path, params, query, fragment))
