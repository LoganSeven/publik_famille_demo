import os
import time

import pytest
from django.utils.timezone import now
from quixote import get_publisher

import wcs.sql
from wcs.qommon.tokens import Token

from .utilities import create_temporary_pub


@pytest.fixture
def pub(request):
    return create_temporary_pub()


def test_migrate_to_sql(pub):
    get_publisher().token_class.wipe()
    assert get_publisher().token_class.count() == 0
    token = Token()
    token.expiration = time.time() + 86400  # expiration stored as timestamp
    token.context = {'a': 'b'}
    token.store()
    assert os.path.exists(token.get_object_filename())

    token2 = Token()
    token2.expiration = time.time() - 86400  # already expired
    token2.context = {'a': 'b'}
    token2.store()
    assert os.path.exists(token2.get_object_filename())

    wcs.sql.migrate_legacy_tokens()
    assert os.path.exists(token.get_object_filename())
    assert not os.path.exists(token2.get_object_filename())
    os.unlink(token.get_object_filename())
    assert get_publisher().token_class.count() == 1
    sql_token = get_publisher().token_class.get(token.id)
    assert sql_token.id == token.id
    assert sql_token.context == token.context
    assert (sql_token.expiration - now()).total_seconds() < 86400


def test_expiration(pub):
    get_publisher().token_class.wipe()
    token = get_publisher().token_class()
    token.store()
    assert get_publisher().token_class().get(token.id)

    token = get_publisher().token_class(expiration_delay=-3600)  # already expired
    token.store()
    with pytest.raises(KeyError):
        assert get_publisher().token_class().get(token.id)


def test_clean_job(pub):
    get_publisher().token_class.wipe()
    token = get_publisher().token_class()
    token.store()
    token = get_publisher().token_class()
    token.store()
    token = get_publisher().token_class(expiration_delay=-7200)  # already expired
    token.store()
    assert get_publisher().token_class.count() == 3
    get_publisher().clean_tokens()
    assert get_publisher().token_class.count() == 2
