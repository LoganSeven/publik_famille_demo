import datetime

import pytest
from django.utils.timezone import now

from wcs.logged_errors import LoggedError

from .utilities import clean_temporary_pub, create_temporary_pub


@pytest.fixture
def pub(request):
    return create_temporary_pub()


def teardown_module(module):
    clean_temporary_pub()


def test_deprecated_error(pub):
    pub.record_deprecated_usage('foo bar')
    assert LoggedError.count() == 1
    error = LoggedError.select()[0]
    assert error.summary == 'Deprecation: foo bar'
    assert error.kind == 'deprecated_usage'
    assert error.occurences_count == 1

    pub.record_deprecated_usage('foo bar')
    pub.record_deprecated_usage('foo bar')
    assert LoggedError.count() == 1
    error = LoggedError.select()[0]
    assert error.summary == 'Deprecation: foo bar'
    assert error.kind == 'deprecated_usage'
    assert error.occurences_count == 3


def test_error_cleanup(pub):
    LoggedError.wipe()
    error = LoggedError.record('test')
    LoggedError.clean()
    assert LoggedError.count() == 1

    error.first_occurence_timestamp = error.latest_occurence_timestamp = now() - datetime.timedelta(days=35)
    error.store()
    LoggedError.clean()
    assert LoggedError.count() == 1

    error.deleted_timestamp = now()
    error.store()
    LoggedError.clean()
    assert LoggedError.count() == 1

    error.deleted_timestamp = now() - datetime.timedelta(days=34)
    error.store()
    LoggedError.clean()
    assert LoggedError.count() == 0
