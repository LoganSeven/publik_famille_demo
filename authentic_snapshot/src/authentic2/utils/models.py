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

import itertools

from django.utils.text import slugify


def generate_slug(name, seen_slugs=None, max_length=256):
    base_slug = slugify(name).lstrip('_')
    slug = base_slug[:max_length]
    if seen_slugs and slug in seen_slugs:
        for i in itertools.count(1):
            suffix = '-%s' % i
            if len(base_slug) + len(suffix) <= max_length:
                slug = base_slug + suffix
            else:
                infix = '-%s-' % i
                prefix_len = (max_length - len(infix)) // 2
                suffix_len = max_length - len(infix) - prefix_len
                slug = base_slug[:prefix_len] + infix + base_slug[:suffix_len]
            if slug not in seen_slugs:
                break
    return slug
