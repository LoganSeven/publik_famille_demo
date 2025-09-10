# w.c.s. - web application for online forms
# Copyright (C) 2005-2010  Entr'ouvert
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

import re

from . import _, ngettext

_minute = 60
_hour = 60 * 60
_day = _hour * 24
_month = _day * 31
_year = int(_day * 365.25)


def list2human(stringlist):
    '''Transform a string list to human enumeration'''
    beginning = stringlist[:-1]
    if not beginning:
        return ''.join(stringlist)
    return _('%(first)s and %(second)s') % {'first': _(', ').join(beginning), 'second': stringlist[-1]}


_humandurations = (
    ((_('day'), _('days'), _('day(s)')), _day),
    ((_('hour'), _('hours'), _('hour(s)')), _hour),
    ((_('minute'), _('minutes'), _('minute(s)')), _minute),
    ((_('second'), _('seconds'), _('second(s)')), 1),
    ((_('month'), _('months'), _('month(s)')), _month),
    ((_('year'), _('years'), _('year(s)')), _year),
)


def timewords():
    '''List of words one can use to specify durations'''
    result = []
    for (dummy, dummy, word), dummy in _humandurations:
        result.append(str(word))  # str() to force translation
    return result


def humanduration2seconds(humanduration):
    if not humanduration:
        raise ValueError()
    seconds = 0
    for (word1, word2, dummy), quantity in _humandurations:
        # look for number then singular or plural forms of unit
        m = re.search(r'(\d+)\s*\b(%s|%s)\b' % (word1, word2), humanduration)
        if m:
            seconds = seconds + int(m.group(1)) * quantity
    return seconds


def seconds2humanduration(seconds, short=False):
    """Convert a time range in seconds to a human string representation"""
    if not isinstance(seconds, int):
        return ''

    if not short:
        years = int(seconds / _year)
        seconds = seconds - _year * years
        months = int(seconds / _month)
        seconds = seconds - _month * months
    days = int(seconds / _day)
    seconds = seconds - _day * days
    hours = int(seconds / _hour)
    seconds = seconds - _hour * hours
    minutes = int(seconds / _minute)
    seconds = seconds - _minute * minutes
    human = []
    if not short:
        if years:
            human.append(ngettext('%(total)s year', '%(total)s years', years) % {'total': years})
        if months:
            human.append(ngettext('%(total)s month', '%(total)s months', months) % {'total': months})
    if days:
        human.append(ngettext('%(total)s day', '%(total)s days', days) % {'total': days})

    if short:
        if hours and minutes:
            human.append(_('%(hours)sh%(minutes)02d') % {'hours': hours, 'minutes': minutes})
        elif hours:
            human.append(_('%(hours)sh') % {'hours': hours})
        elif minutes:
            human.append(_('%(minutes)smin') % {'minutes': minutes})
        return list2human(human)

    if hours:
        human.append(ngettext('%(total)s hour', '%(total)s hours', hours) % {'total': hours})
    if minutes:
        human.append(ngettext('%(total)s minute', '%(total)s minutes', minutes) % {'total': minutes})
    if seconds:
        human.append(ngettext('%(total)s second', '%(total)s seconds', seconds) % {'total': seconds})
    return list2human(human)
