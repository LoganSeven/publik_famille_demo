# authentic2 - versatile identity manager
# Copyright (C) 2023 Entr'ouvert
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

from django.utils.text import slugify


def slugify_keep_underscore(value):
    slugified = slugify(value)
    if value.startswith('_') and not slugified.startswith('_'):
        underscore_count = len(value) - len(value.lstrip('_'))
        slugified = '_' * underscore_count + slugified
    if value.endswith('_') and not slugified.endswith('_'):
        underscore_count = len(value) - len(value.rstrip('_'))
        slugified += '_' * underscore_count
    return slugified
