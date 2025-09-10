import datetime
import os
import random

import pytest
from quixote import cleanup

import wcs.qommon.storage as st
from wcs.qommon.storage import StorableObject, cache_umask

from .utilities import clean_temporary_pub, create_temporary_pub


def setup_module(module):
    cleanup()
    global pub
    pub = create_temporary_pub()


def teardown_module(module):
    clean_temporary_pub()


class Foobar(StorableObject):
    _names = 'tests%s' % random.randint(0, 100000)

    value = None
    unique_value = None


class Foobar2(StorableObject):
    _names = 'tests%s' % random.randint(0, 100000)

    value = None
    unique_value = None


def test_store():
    test = Foobar()
    test.value = 'value'
    test.unique_value = 'unique-value'
    test.store()
    assert test.id == '1'


def test_get():
    test = Foobar.get(1)
    assert test.value == 'value'


def test_remove_self():
    test = Foobar.get(1)
    test.remove_self()
    with pytest.raises(KeyError):
        test = Foobar.get(1)
    test = Foobar.get(1, ignore_errors=True)
    assert test is None


def test_select():
    Foobar.wipe()

    for x in range(1, 51):
        test = Foobar()
        test.unique_value = x
        test.store()

    assert len(Foobar.select()) == 50

    assert len(Foobar.select(lambda x: x.unique_value < 26)) == 25
    assert len(Foobar.select([st.Less('unique_value', 26)])) == 25
    assert len(Foobar.select([st.Not(st.Equal('unique_value', 25))])) == 49
    assert len(Foobar.select([st.Contains('unique_value', [24, 25, 26])])) == 3
    assert len(Foobar.select([st.Contains('unique_value', [24, 25, 86])])) == 2
    assert len(Foobar.select([st.Not(st.Contains('unique_value', [24, 25, 86]))])) == 48


def test_select_order_by():
    Foobar.wipe()

    for x in range(1, 51):
        test = Foobar()
        test.unique_value = 51 - x
        test.store()

    assert [int(x.id) for x in Foobar.select(order_by='id')] == list(range(1, 51))
    assert [int(x.id) for x in Foobar.select(order_by='-id')] == list(range(50, 0, -1))
    assert [int(x.id) for x in Foobar.select(order_by='unique_value')] == list(range(50, 0, -1))
    assert [int(x.id) for x in Foobar.select(order_by='-unique_value')] == list(range(1, 51))


def test_select_datetime():
    Foobar.wipe()

    d = datetime.datetime(2014, 1, 1)
    for i in range(50):
        test = Foobar()
        test.receipt_time = (d + datetime.timedelta(days=i)).timetuple()
        test.store()

    assert len(Foobar.select()) == 50

    assert len(Foobar.select(lambda x: x.receipt_time == d.timetuple())) == 1
    assert len(Foobar.select([st.Equal('receipt_time', d.timetuple())])) == 1
    assert len(Foobar.select([st.Less('receipt_time', (d + datetime.timedelta(days=20)).timetuple())])) == 20
    assert (
        len(Foobar.select([st.Greater('receipt_time', (d + datetime.timedelta(days=20)).timetuple())])) == 29
    )


def test_select_limit_offset():
    Foobar.wipe()

    for _ in range(50):
        test = Foobar()
        test.store()

    assert len(Foobar.select()) == 50
    assert [int(x.id) for x in Foobar.select(order_by='id', limit=10)] == list(range(1, 11))
    assert [int(x.id) for x in Foobar.select(order_by='id', limit=10, offset=10)] == list(range(11, 21))
    assert [int(x.id) for x in Foobar.select(order_by='id', limit=20, offset=20)] == list(range(21, 41))
    assert [int(x.id) for x in Foobar.select(order_by='id', offset=10)] == list(range(11, 51))


def test_select_criteria_overlaps():
    Foobar.wipe()

    test = Foobar()
    test.a = [1, 2]
    test.store()

    test = Foobar()
    test.a = []
    test.store()

    test = Foobar()
    test.a = [2, 3]
    test.store()

    assert len(Foobar.select([st.Intersects('a', [1])])) == 1
    assert len(Foobar.select([st.Intersects('a', [2])])) == 2
    assert len(Foobar.select([st.Intersects('a', [4])])) == 0
    assert len(Foobar.select([st.Intersects('a', [1, 2, 3])])) == 2


def test_count():
    Foobar.wipe()

    for x in range(50):
        test = Foobar()
        test.value = x + 1
        test.store()

    assert Foobar.count() == 50
    assert Foobar.count([st.Less('value', 26)]) == 25


def test_select_criteria_or_and():
    Foobar.wipe()

    for x in range(50):
        test = Foobar()
        test.value = x + 1
        test.store()

    assert len(Foobar.select()) == 50

    assert [int(x.id) for x in Foobar.select([st.Or([])], order_by='id')] == []
    assert [int(x.id) for x in Foobar.select([st.Or([st.Less('value', 10)])], order_by='id')] == list(
        range(1, 10)
    )
    assert [
        int(x.id)
        for x in Foobar.select([st.Or([st.Less('value', 10), st.Equal('value', 15)])], order_by='value')
    ] == list(range(1, 10)) + [15]
    assert [
        int(x.id)
        for x in Foobar.select([st.And([st.Less('value', 10), st.Greater('value', 5)])], order_by='id')
    ] == list(range(6, 10))


def test_items():
    Foobar.wipe()

    for _ in range(50):
        test = Foobar()
        test.store()

    assert sorted((int(x), int(y.id)) for (x, y) in Foobar.items()) == list(zip(range(1, 51), range(1, 51)))


def test_reversed_order():
    Foobar.wipe()

    for _ in range(50):
        test = Foobar()
        test.store()

    assert len(Foobar.select()) == 50
    assert [int(x.id) for x in Foobar.select(order_by='-id', limit=10)] == list(range(50, 40, -1))


def test_umask():
    test = Foobar()
    test.value = 'value'
    test.unique_value = 'unique-value'

    os.umask(0o022)
    cache_umask()
    test.store()
    assert (os.stat(test.get_object_filename()).st_mode % 0o1000) == 0o644

    os.umask(0o002)
    cache_umask()
    test.store()
    assert (os.stat(test.get_object_filename()).st_mode % 0o1000) == 0o664


def test_publisher_cache():
    pub.reset_caches()

    Foobar.wipe()
    Foobar2.wipe()

    test = Foobar()
    test.value = 'value'
    test.unique_value = 'unique-value'
    test.store()

    test2 = Foobar2()
    test2.value = 'value'
    test2.unique_value = 'unique-value'
    test2.store()

    test = Foobar.cached_get('1')
    assert test.value == 'value'
    assert Foobar.cached_get('1') is test  # same object
