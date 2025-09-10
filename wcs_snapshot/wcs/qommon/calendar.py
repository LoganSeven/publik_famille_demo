# w.c.s. - web application for online forms
# Copyright (C) 2005-2020  Entr'ouvert
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, see <http://www.gnu.org/licenses/>.

from django.utils.module_loading import import_string
from quixote import get_publisher

try:
    from workalendar.core import SUN
except ImportError:
    SUN = None


def get_calendar(saturday_is_a_working_day=False):
    # get calendar from settings
    try:
        calendar_class = import_string(get_publisher().get_working_day_calendar())
    except (AttributeError, ImportError):
        return

    # saturday is not a working day, return this calendar
    if not saturday_is_a_working_day:
        return calendar_class()

    # saturday is a working day, build a custom calendar
    class CalendarWithSaturday(calendar_class):
        WEEKEND_DAYS = (SUN,)

    return CalendarWithSaturday()
