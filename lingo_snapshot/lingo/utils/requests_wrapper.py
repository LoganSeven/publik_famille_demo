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

import hashlib
import logging
import urllib.parse
from io import BytesIO

from django.conf import settings
from django.core.cache import cache
from django.utils.encoding import smart_bytes
from django.utils.http import urlencode
from requests import Response
from requests import Session as RequestsSession
from requests.auth import AuthBase

from .misc import get_known_service_for_url
from .signature import sign_url


class NothingInCacheException(Exception):
    pass


class PublikSignature(AuthBase):
    def __init__(self, secret):
        self.secret = secret

    def __call__(self, request):
        request.url = sign_url(request.url, self.secret)
        return request


class Requests(RequestsSession):
    def request(self, method, url, **kwargs):
        remote_service = kwargs.pop('remote_service', None)
        cache_duration = kwargs.pop('cache_duration', 15)
        invalidate_cache = kwargs.pop('invalidate_cache', False)
        user = kwargs.pop('user', None)
        django_request = kwargs.pop('django_request', None)
        without_user = kwargs.pop('without_user', False)
        federation_key = kwargs.pop('federation_key', 'auto')  # 'auto', 'email', 'nameid'
        raise_if_not_cached = kwargs.pop('raise_if_not_cached', False)
        log_errors = kwargs.pop('log_errors', True)

        # don't use persistent cookies
        self.cookies.clear()

        # search in legacy urls
        legacy_urls_mapping = getattr(settings, 'LEGACY_URLS_MAPPING', None)
        if legacy_urls_mapping:
            splitted_url = urllib.parse.urlparse(url)
            hostname = splitted_url.netloc
            if hostname in legacy_urls_mapping:
                url = splitted_url._replace(netloc=legacy_urls_mapping[hostname]).geturl()

        if remote_service == 'auto':
            remote_service = get_known_service_for_url(url)
            if remote_service:
                # only keeps the path (URI) in url parameter, scheme and netloc are
                # in remote_service
                scheme, netloc, path, params, query, fragment = urllib.parse.urlparse(url)
                url = urllib.parse.urlunparse(('', '', path, params, query, fragment))
            else:
                logging.warning('service not found in settings.KNOWN_SERVICES for %s', url)

        if remote_service:
            if isinstance(user, dict):
                query_params = user.copy()
            elif not user or not user.is_authenticated:
                if without_user:
                    query_params = {}
                else:
                    query_params = {'NameID': '', 'email': ''}
            else:
                query_params = {}
                if federation_key == 'nameid':
                    query_params['NameID'] = user.get_name_id()
                elif federation_key == 'email':
                    query_params['email'] = user.email
                else:  # 'auto'
                    user_name_id = user.get_name_id()
                    if user_name_id:
                        query_params['NameID'] = user_name_id
                    else:
                        query_params['email'] = user.email

            if remote_service.get('orig'):
                query_params['orig'] = remote_service.get('orig')

            remote_service_base_url = remote_service.get('url')
            scheme, netloc, dummy, params, old_query, fragment = urllib.parse.urlparse(
                remote_service_base_url
            )

            query = urlencode(query_params)
            if '?' in url:
                path, old_query = url.split('?', 1)
                query += '&' + old_query
            else:
                path = url

            url = urllib.parse.urlunparse((scheme, netloc, path, params, query, fragment))

        if method == 'GET' and cache_duration:
            # handle cache
            params = urlencode(kwargs.get('params', {}))
            cache_key = hashlib.md5(smart_bytes(url + params)).hexdigest()  # nosec
            cache_content = cache.get(cache_key)
            if cache_content and not invalidate_cache:
                response = Response()
                response.status_code = 200
                response.raw = BytesIO(smart_bytes(cache_content))
                return response
            elif raise_if_not_cached:
                raise NothingInCacheException()

        if remote_service:  # sign
            kwargs['auth'] = PublikSignature(remote_service.get('secret'))

        kwargs['timeout'] = kwargs.get('timeout') or settings.REQUESTS_TIMEOUT

        response = super().request(method, url, **kwargs)
        if log_errors and (response.status_code // 100 != 2):
            extra = {}
            if django_request:
                extra['request'] = django_request
            if log_errors == 'warn':
                logging.warning(
                    'failed to %s %s (%s)', method, response.request.url, response.status_code, extra=extra
                )
            else:
                logging.error(
                    'failed to %s %s (%s)', method, response.request.url, response.status_code, extra=extra
                )
        if method == 'GET' and cache_duration and (response.status_code // 100 == 2):
            cache.set(cache_key, response.content, cache_duration)

        return response


requests = Requests()
