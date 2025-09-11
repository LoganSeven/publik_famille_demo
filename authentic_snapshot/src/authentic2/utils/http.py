# authentic2 - authentic2 authentication for FranceConnect
# Copyright (C) Entr'ouvert


import requests
import requests.adapters
import requests.exceptions
from django.conf import settings
from django.utils.translation import gettext as _

try:
    from urllib3.util import Retry
except ImportError:
    from requests.packages.urllib3.util.retry import Retry


def retry_session(
    retries=2,
    backoff_factor=0.5,
    status_forcelist=(500, 502, 504),
    session=None,
):
    '''Create a requests session which retries after 0.5s then 1s'''
    session = session or requests.Session()
    retry = Retry(
        total=retries,
        read=retries,
        connect=retries,
        backoff_factor=backoff_factor,
        status_forcelist=status_forcelist,
    )
    adapter = requests.adapters.HTTPAdapter(max_retries=retry)
    session.mount('http://', adapter)
    session.mount('https://', adapter)
    # set proxies
    session.proxies.update(getattr(settings, 'REQUESTS_PROXIES', {}))
    return session


class HTTPError(Exception):
    def __init__(self, message, **details):
        super().__init__(message)
        self.details = details

    def __str__(self):
        s = super().__str__()
        if self.details:
            s += ' ('
            s += ' '.join('%s=%r' % (k, v) for k, v in self.details.items())
            s += ')'
        return s


def request(method, url, session=None, expected_statuses=None, retries=2, **kwargs):
    session = retry_session(session=session, retries=retries)

    try:
        response = getattr(session, method)(
            url,
            **kwargs,
        )
        response.raise_for_status()
    except requests.exceptions.HTTPError:
        if expected_statuses and response.status_code in expected_statuses:
            return response
        try:
            content = response.json()
        except ValueError:
            content = response.text[:256]
        raise HTTPError(_('Status code is not 200.'), status_code=response.status_code, content=content)
    except requests.exceptions.RequestException as e:
        raise HTTPError(_('URL is unreachable.'), exception=e)
    return response


def parse_json_response(response):
    try:
        content = response.json()
    except ValueError:
        raise HTTPError(_('Document at URL is not JSON.'), content=response.content[:1024])
    return content


def post_json(url, **kwargs):
    response = request('post', url=url, **kwargs)
    return parse_json_response(response)


def get(url, **kwargs):
    return request('get', url=url, **kwargs)


def get_json(url, **kwargs):
    response = request('get', url=url, **kwargs)
    return parse_json_response(response)
