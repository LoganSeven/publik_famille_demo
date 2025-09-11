# authentic2 - versatile identity manager
# Copyright (C) 2010-2021 Entr'ouvert
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

from authentic2.utils.models import generate_slug


def test_generate_slug():
    assert generate_slug('a b') == 'a-b'
    assert generate_slug('a' * 257) == 'a' * 256
    assert generate_slug('a' * 10, max_length=9) == 'a' * 9
    assert generate_slug('a' * 10, seen_slugs=['a' * 9], max_length=9) == 'aaa-1-aaa'
    assert generate_slug('a' * 8, seen_slugs=['a' * 8], max_length=9) == 'aaa-1-aaa'
    assert generate_slug('a' * 7, seen_slugs=['a' * 7], max_length=9) == 'aaaaaaa-1'
    seen_slugs = set()
    for _ in range(1000):
        slug = generate_slug('a' * 10, seen_slugs=seen_slugs, max_length=9)
        assert slug not in seen_slugs
        assert len(slug) == 9
        seen_slugs.add(slug)
