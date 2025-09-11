# authentic2 - authentic2 authentication for FranceConnect
# Copyright (C) Entr'ouvert

import os

import pytest
import requests
import requests.exceptions
import responses

from authentic2.utils.http import HTTPError, get_json, post_json, retry_session


@responses.activate
def test_requests_proxies(settings, monkeypatch):
    session = retry_session()
    assert not session.proxies
    other_session = requests.Session()
    other_session.proxies = {'http': 'http://example.net'}
    session = retry_session(session=other_session)
    assert session is other_session
    assert session.proxies == {'http': 'http://example.net'}

    settings.REQUESTS_PROXIES = {'https': 'http://pubproxy.com/api/proxy'}
    session = retry_session()
    assert session.proxies == {'https': 'http://pubproxy.com/api/proxy'}

    #  on local test execution 'NO_PROXY' env variable might be set
    if 'NO_PROXY' in os.environ:
        monkeypatch.delenv('NO_PROXY')
    if 'no_proxy' in os.environ:
        monkeypatch.delenv('no_proxy')

    response = responses.get('https://example.net/', status=200, body=b'whatever')

    session.get('https://example.net/')
    assert response.calls[0][0].req_kwargs['proxies'] == {'https': 'http://pubproxy.com/api/proxy'}


@responses.activate
def test_get_json():
    responses.get('http://example.net/json', json={'foo': 'bar'})
    assert get_json('http://example.net/json') == {'foo': 'bar'}


@responses.activate
def test_post_json():
    response = responses.post('http://example.net/json', json={'foo': 'bar'})
    assert post_json('http://example.net/json', data={'bar': 'foo'}) == {'foo': 'bar'}
    assert response.calls[0].request.body == 'bar=foo'

    response = responses.post('http://example.net/json', json={'foo': 'bar'})
    assert post_json('http://example.net/json', json={'bar': 'foo'}) == {'foo': 'bar'}
    assert response.calls[0].request.body == b'{"bar": "foo"}'


@responses.activate
def test_http_error():
    responses.get('http://example.net/connection-error', body=requests.exceptions.ConnectionError())
    with pytest.raises(HTTPError):
        assert get_json('http://example.net/connection-error')

    responses.get('http://example.net/bad-json', body=b'{')
    with pytest.raises(HTTPError):
        assert get_json('http://example.net/connection-error')
