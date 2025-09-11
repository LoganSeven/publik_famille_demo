# authentic2 - versatile identity manager
# Copyright (C) 2010-2019 Entr'ouvert
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

import threading

import pytest
from django.db import connection

from authentic2.models import Attribute, AttributeValue


def test_attribute_value_uniqueness(transactional_db, simple_user, concurrency, pytestconfig):
    if pytestconfig.getoption('nomigrations'):
        pytest.skip('running migrations is required for this test')
    # disable default attributes
    Attribute.objects.update(disabled=True)

    acount = Attribute.objects.count()

    single_at = Attribute.objects.create(name='single', label='single', kind='string', multiple=False)
    multiple_at = Attribute.objects.create(name='multiple', label='multiple', kind='string', multiple=True)
    assert Attribute.objects.count() == acount + 2

    AttributeValue.objects.all().delete()

    for _ in range(3):

        def map_threads(f, l):
            threads = []
            for i in l:
                threads.append(threading.Thread(target=f, args=(i,)))
                threads[-1].start()
            for thread in threads:
                thread.join()

        def f(i):
            simple_user.attributes.multiple = [str(i)]
            connection.close()

        map_threads(f, range(concurrency))
        map_threads(f, range(concurrency))
        assert AttributeValue.objects.filter(attribute=multiple_at).count() == 1

        def f(i):  # pylint: disable=E0102
            simple_user.attributes.single = str(i)
            connection.close()

        map_threads(f, range(concurrency))
        assert AttributeValue.objects.filter(attribute=single_at).count() == 1
