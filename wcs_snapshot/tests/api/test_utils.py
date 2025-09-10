import pytest
from quixote import cleanup

from wcs import sessions
from wcs.api_utils import get_query_flag
from wcs.qommon.http_request import HTTPRequest

from ..utilities import clean_temporary_pub, create_temporary_pub


def setup_module(module):
    cleanup()


def teardown_module(module):
    clean_temporary_pub()


@pytest.fixture
def pub(request):
    pub = create_temporary_pub()
    pub.cfg['language'] = {'language': 'en'}
    pub.write_cfg()
    req = HTTPRequest(None, {'SERVER_NAME': 'example.net', 'SCRIPT_NAME': ''})
    req.response.filter = {}
    req._user = None
    pub._set_request(req)
    req.session = sessions.BasicSession(id=1)
    pub.set_config(req)
    return pub


@pytest.mark.parametrize(
    'value, default, expected',
    [
        (True, '42', True),
        ('True', '42', True),
        ('true', '42', True),
        ('on', '42', True),
        ('1', '42', True),
        (False, '42', False),
        ('False', '42', False),
        ('false', '42', False),
        ('off', '42', False),
        ('0', '42', False),
        ('Blah', '42', '42'),
        ('Blah', False, False),
        ('Blah', True, True),
        (None, '42', '42'),
        (None, False, False),
        (None, True, True),
    ],
)
def test_get_query_flag(pub, value, default, expected):
    pub.get_request().form = {'flag': value}
    assert get_query_flag('flag', default=default) == expected
