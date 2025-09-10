import pytest

from wcs.qommon.http_request import HTTPRequest
from wcs.variables import LazyRequest

from .utilities import clean_temporary_pub, create_temporary_pub


@pytest.fixture
def pub():
    return create_temporary_pub()


def teardown_module(module):
    clean_temporary_pub()


def test_is_in_backoffice(pub):
    req = HTTPRequest(None, {'SCRIPT_NAME': '/', 'SERVER_NAME': 'example.net'})
    assert not req.is_in_backoffice()
    assert not LazyRequest(req).is_in_backoffice
    req = HTTPRequest(None, {'SCRIPT_NAME': '/backoffice/test', 'SERVER_NAME': 'example.net'})
    assert req.is_in_backoffice()
    assert LazyRequest(req).is_in_backoffice


def test_is_from_mobile(pub):
    req = HTTPRequest(None, {'SCRIPT_NAME': '/', 'SERVER_NAME': 'example.net'})
    assert not req.is_from_mobile()
    assert not LazyRequest(req).is_from_mobile
    req = HTTPRequest(None, {'SCRIPT_NAME': '/', 'SERVER_NAME': 'example.net', 'HTTP_USER_AGENT': 'bot/1.0'})
    assert not req.is_from_mobile()
    assert not LazyRequest(req).is_from_mobile
    req = HTTPRequest(
        None,
        {'SCRIPT_NAME': '/', 'SERVER_NAME': 'example.net', 'HTTP_USER_AGENT': 'Mozilla/5.0 (Mobile) plop'},
    )
    assert req.is_from_mobile()
    assert LazyRequest(req).is_from_mobile
    req = HTTPRequest(
        None,
        {
            'SCRIPT_NAME': '/',
            'SERVER_NAME': 'example.net',
            'HTTP_USER_AGENT': 'Mozilla/5.0 (Chrome) Mobile Safari',
        },
    )
    assert req.is_from_mobile()
    assert LazyRequest(req).is_from_mobile
