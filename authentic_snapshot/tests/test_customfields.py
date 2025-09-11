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

import pytest

from authentic2.saml.models import NAME_ID_FORMATS_CHOICES, KeyValue, SPOptionsIdPPolicy

# Adaptation of http://djangosnippets.org/snippets/513/


class CustomDataType(str):
    pass


@pytest.mark.parametrize(
    'value',
    [
        '\xe9',
        b'\xc3\xa9',
        {1: 1, 2: 4, 3: 6, 4: 8, 5: 10},
        'Hello World',
        (1, 2, 3, 4, 5),
        [1, 2, 3, 4, 5],
        CustomDataType('Hello World'),
    ],
    ids=repr,
)
def test_pickled_data_integrity(value, db):
    """Tests that data remains the same when saved to and fetched from the database."""
    KeyValue.objects.create(key='a', value=value)
    assert KeyValue.objects.get().value == value


def test_multiselectfield_data_integrity(db):
    spp = SPOptionsIdPPolicy.objects.create(name='spp')
    value = [x[0] for x in NAME_ID_FORMATS_CHOICES]
    spp.accepted_name_id_format = value
    spp.save()
    spp = SPOptionsIdPPolicy.objects.get(name='spp')
    assert spp.accepted_name_id_format == value


def test_multiselectfield_lookup(db):
    value = [x[0] for x in NAME_ID_FORMATS_CHOICES]
    SPOptionsIdPPolicy.objects.create(name='spp', accepted_name_id_format=value)
    assert SPOptionsIdPPolicy.objects.get(accepted_name_id_format=value).accepted_name_id_format == value
