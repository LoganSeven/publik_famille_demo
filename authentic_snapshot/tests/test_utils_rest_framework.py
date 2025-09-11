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
# authentic2

import io
import json

import pytest

from authentic2.utils.rest_framework import _FLATTEN_SEPARATOR as SEP
from authentic2.utils.rest_framework import UnflattenJSONParser
from authentic2.utils.rest_framework import _unflatten as unflatten


def test_unflatten_base():
    assert unflatten('') == ''
    assert unflatten('a') == 'a'
    assert unflatten([]) == []
    assert unflatten([1]) == [1]
    assert unflatten({}) == {}
    assert unflatten(0) == 0
    assert unflatten(1) == 1
    assert unflatten(False) is False
    assert unflatten(True) is True


def test_unflatten_dict():
    assert unflatten(
        {
            'a' + SEP + 'b' + SEP + '0': 1,
            'a' + SEP + 'c' + SEP + '1': 'a',
            'a' + SEP + 'b' + SEP + '1': True,
            'a' + SEP + 'c' + SEP + '0': [1],
        }
    ) == {
        'a': {
            'b': [1, True],
            'c': [[1], 'a'],
        }
    }


def test_unflatten_array():
    assert unflatten(
        {
            '0' + SEP + 'b' + SEP + '0': 1,
            '1' + SEP + 'c' + SEP + '1': 'a',
            '0' + SEP + 'b' + SEP + '1': True,
            '1' + SEP + 'c' + SEP + '0': [1],
        }
    ) == [{'b': [1, True]}, {'c': [[1], 'a']}]


def test_unflatten_missing_final_index():
    with pytest.raises(ValueError) as exc_info:
        unflatten({'1': 1})
    assert 'incomplete' in exc_info.value.args[0]


def test_unflatten_missing_intermediate_index():
    with pytest.raises(ValueError) as exc_info:
        unflatten({'a' + SEP + '1' + SEP + 'b': 1})
    assert 'incomplete' in exc_info.value.args[0]


class TestUnflattenJsonParser:
    @pytest.fixture
    def parser(self):
        return UnflattenJSONParser()

    def test_parse(self, parser):
        in_json = {
            'a/b/c': {'d/e': 1},
            'b/0': 1,
            'b/1': 2,
        }
        out_json = {'a': {'b': {'c': {'d/e': 1}}}, 'b': [1, 2]}

        stream = io.BytesIO(json.dumps(in_json).encode())
        assert parser.parse(stream) == out_json
