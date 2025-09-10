# w.c.s. - web application for online forms
# Copyright (C) 2005-2016  Entr'ouvert
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

"""
Collection of utility functions
"""
import datetime
import time

from .misc import get_as_datetime

today = datetime.date.today
now = datetime.datetime.now


def make_date(date_var):
    '''Extract a date from a datetime, a date, a struct_time or a string'''
    if isinstance(date_var, datetime.datetime):
        return date_var.date()
    if isinstance(date_var, datetime.date):
        return date_var
    if isinstance(date_var, time.struct_time) or (isinstance(date_var, tuple) and len(date_var) == 9):
        return datetime.date(*date_var[:3])
    try:
        return get_as_datetime(str(date_var)).date()
    except ValueError:
        raise ValueError('invalid date value: %s' % repr(date_var))


def make_datetime(datetime_var):
    '''Extract a date from a datetime, a date, a struct_time or a string'''
    if isinstance(datetime_var, datetime.datetime):
        return datetime_var
    if isinstance(datetime_var, datetime.date):
        return datetime.datetime(year=datetime_var.year, month=datetime_var.month, day=datetime_var.day)
    if isinstance(datetime_var, time.struct_time) or (
        isinstance(datetime_var, tuple) and len(datetime_var) == 9
    ):
        return datetime.datetime(*datetime_var[:6])
    try:
        return get_as_datetime(str(datetime_var))
    except ValueError:
        raise ValueError('invalid datetime value: %s' % repr(datetime_var))


def date_delta(t1, t2):
    '''Return the timedelta between two date like values'''
    t1, t2 = make_date(t1), make_date(t2)
    return t1 - t2


def datetime_delta(t1, t2):
    '''Return the timedelta between two datetime like values'''
    t1, t2 = make_datetime(t1), make_datetime(t2)
    return t1 - t2


def age_in_years_and_months(born, today=None):
    '''Compute age since today as the number of years and months elapsed'''
    born = make_date(born)
    if today is None:
        today = datetime.date.today()
    today = make_date(today)
    before = (today.month, today.day) < (born.month, born.day)
    years = today.year - born.year
    months = today.month - born.month
    if before:
        years -= 1
        months += 12
    if today.day < born.day:
        months -= 1
    return years, months


def age_in_years(born, today=None):
    '''Compute age since today as the number of years elapsed'''
    return age_in_years_and_months(born, today=today)[0]


def age_in_days(born, today=None):
    '''Compute age since today as the number of days elapsed'''
    born = make_date(born)
    if today is None:
        today = datetime.date.today()
    today = make_date(today)
    return date_delta(today, born).days


def age_in_seconds(born, today=None):
    '''Compute age since today as the number of seconds elapsed'''
    born = make_datetime(born)
    if today is None:
        today = datetime.datetime.now()
    today = make_datetime(today)
    return datetime_delta(today, born).total_seconds()


def details_format(value, format=None):
    # render form_details as plain text
    # (for now this is the only possible output so it just returns the value as is)
    return str(value)
