# authentic2 - versatile identity manager
# Copyright (C) 2010-2020 Entr'ouvert
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

from datetime import datetime

from django.utils import formats


def parse_date(formatted_date):
    parsed_date = None
    for date_format in formats.get_format('DATE_INPUT_FORMATS'):
        try:
            parsed_date = datetime.strptime(formatted_date, date_format)
        except ValueError:
            continue
        else:
            break
    if not parsed_date:
        raise ValueError
    return parsed_date.date()
