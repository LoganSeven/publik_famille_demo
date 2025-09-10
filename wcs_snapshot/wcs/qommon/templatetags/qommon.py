# w.c.s. - web application for online forms
# Copyright (C) 2005-2017  Entr'ouvert
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

import collections.abc
import datetime
import functools
import hashlib
import io
import json
import math
import mimetypes
import os
import random
import re
import string
import subprocess
import tempfile
import time
import urllib.parse
import zipfile
from decimal import Decimal
from decimal import DivisionByZero as DecimalDivisionByZero
from decimal import InvalidOperation as DecimalInvalidOperation

import emoji
import pyproj
import unidecode

try:
    import qrcode
except ImportError:
    qrcode = None

from pyproj import Geod
from quixote import get_publisher, get_response, get_session
from quixote.http_request import make_safe_filename

try:
    import langdetect
    from langdetect.lang_detect_exception import LangDetectException
except ImportError:
    langdetect = None

from django import template
from django.contrib.humanize.templatetags.humanize import intcomma as humanize_intcomma
from django.template import defaultfilters
from django.utils import dateparse
from django.utils.encoding import force_bytes, force_str
from django.utils.safestring import mark_safe
from django.utils.text import slugify
from django.utils.timezone import is_naive, localtime, make_aware, make_naive

from wcs.qommon import _, calendar, evalutils, upload_storage
from wcs.qommon.admin.texts import TextsDirectory
from wcs.qommon.humantime import seconds2humanduration
from wcs.qommon.misc import (
    get_as_datetime,
    parse_decimal,
    strip_some_tags,
    unlazy,
    validate_mobile_phone_local,
)
from wcs.qommon.template import TemplateError
from wcs.qommon.upload_storage import PicklableUpload
from wcs.wscalls import NamedWsCall

register = template.Library()


@register.filter
def get(mapping, key):
    mapping = unlazy(mapping)
    key = unlazy(key)
    if hasattr(mapping, 'get'):
        return mapping.get(key)
    if isinstance(mapping, (tuple, list)):
        try:
            key = int(key)
        except (TypeError, ValueError):
            pass
    try:
        return mapping[key]
    except (TypeError, IndexError, KeyError):
        return None


@register.filter
def getlist(mapping, key):
    if mapping is None:
        return []
    if hasattr(mapping, 'getlist'):
        return mapping.getlist(key)
    mapping = unlazy(mapping)
    if isinstance(mapping, list):
        return [x.get(key) for x in mapping]
    get_publisher().record_error(_('|getlist on unsupported value'))
    return []


@register.filter
def getlistdict(mapping, keys):
    if mapping is None or not hasattr(mapping, 'getlistdict'):
        return []

    parsed_keys = {}
    for key in unlazy(keys).split(','):
        if not key.strip():
            continue
        try:
            name, new_name = key.split(':', 1)
        except ValueError:
            name = new_name = key
        parsed_keys[name.strip()] = new_name.strip()

    def adjust_type(x):
        # |getlistdict has been created to be used in parameters of webservice calls
        # and results will be serialized to JSON; as the time.struct_time would be
        # converted to a tuple, it must be converted here to a proper type.
        if isinstance(x, time.struct_time):
            if x[3:6] == (0, 0, 0):
                return datetime.date(*x[:3])
            return datetime.datetime(*x[:6])
        return x

    results = mapping.getlistdict(parsed_keys.keys())
    return [{parsed_keys[k]: adjust_type(v) for k, v in result.items()} for result in results]


@register.filter
def regroup_as_dict(listdict, keyname):
    keyname = unlazy(keyname)
    return {x.pop(keyname): x for x in listdict}


@register.filter
def get_table_column(table, column_no):
    table = unlazy(table)
    if not isinstance(table, (list, tuple)):
        get_publisher().record_error(_('|get_table_column on invalid value'))
        return []
    try:
        column_no = int(unlazy(column_no)) - 1  # column_no starts counting at 1 (not zero-indexed)
        if column_no < 0:
            raise ValueError
    except (TypeError, ValueError):
        get_publisher().record_error(_('|get_table_column with invalid column number'))
        return []
    values = []
    for row in table:
        try:
            values.append(row[column_no])
        except IndexError:
            pass
    return values


@register.filter
def get_table_row(table, row_no):
    table = unlazy(table)
    if not isinstance(table, (list, tuple)):
        get_publisher().record_error(_('|get_table_row on invalid value'))
        return []
    try:
        row_no = int(unlazy(row_no)) - 1  # row_no starts counting at 1 (not zero-indexed)
        if row_no < 0:
            raise ValueError
    except (TypeError, ValueError):
        get_publisher().record_error(_('|get_table_row with invalid row number'))
        return []
    try:
        return table[row_no]
    except IndexError:
        return []


@register.filter
def startswith(string, substring):
    return string and force_str(string).startswith(force_str(substring))


@register.filter
def endswith(string, substring):
    return string and force_str(string).endswith(force_str(substring))


@register.filter
def split(string, sep=None):
    if not string:
        return []
    return force_str(string).split(sep=None if sep is None else force_str(sep))


@register.filter
def strip(string, chars=None):
    if not string:
        return ''
    if chars:
        return force_str(string).strip(force_str(chars))
    return force_str(string).strip()


@register.filter
def removeprefix(string, prefix=None):
    if not string:
        return ''
    value = force_str(string)
    prefix = force_str(prefix or '')
    return value.removeprefix(prefix)


@register.filter
def removesuffix(string, suffix=None):
    if not string:
        return ''
    value = force_str(string)
    suffix = force_str(suffix or '')
    return value.removesuffix(suffix)


@register.filter
def urljoin(base, path=None):
    return urllib.parse.urljoin(base or '', path or '')


@register.filter
def unaccent(value):
    value = unlazy(value)
    if not value:
        return ''
    if not isinstance(value, str):
        get_publisher().record_error(_('Failed to apply unaccent filter on value (%s)') % value)
        return ''
    return unidecode.unidecode(value)


@register.filter
def parse_date(date_string):
    try:
        return evalutils.make_date(date_string)
    except ValueError:
        pass
    # fallback to Django function
    try:
        return dateparse.parse_date(date_string)
    except (ValueError, TypeError):
        return None


@register.filter(expects_localtime=True, is_safe=False)
def date(value, arg=None):
    value = unlazy(value)
    if arg is None:
        value = parse_date(value)
        if not value:
            return ''
        from wcs.variables import flexible_date

        return flexible_date(parse_date(value))
    if not isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        value = parse_datetime(value) or parse_date(value)
    try:
        return defaultfilters.date(value, arg=arg)
    except NotImplementedError:
        # Django raise it on bad date format
        return ''


@register.filter
def parse_datetime(datetime_string):
    try:
        return evalutils.make_datetime(datetime_string)
    except ValueError:
        pass
    # fallback to Django function
    try:
        return dateparse.parse_datetime(datetime_string)
    except (ValueError, TypeError):
        return None


@register.filter(name='datetime', expects_localtime=True, is_safe=False)
def datetime_(value, arg=None):
    value = unlazy(value)
    if arg is None:
        value = parse_datetime(value)
        if not value:
            return ''
        from wcs.variables import flexible_date

        if not is_naive(value):
            # automatically switch to current timezone
            value = localtime(value)

        return flexible_date(value)
    if not isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        value = parse_datetime(value)
    return defaultfilters.date(value, arg=arg)


@register.filter
def parse_time(time_string):
    # if input is a datetime, extract its time
    try:
        dt = parse_datetime(time_string)
        if dt:
            return dt.time()
    except (ValueError, TypeError):
        pass
    # fallback to Django function
    try:
        return dateparse.parse_time(time_string)
    except (ValueError, TypeError):
        return None


@register.filter(name='time', expects_localtime=True, is_safe=False)
def time_(value, arg=None):
    value = unlazy(value)
    if arg is None:
        parsed = parse_time(value)
        return parsed if parsed is not None else ''  # because bool(midnight) == False
    if not isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        value = parse_time(value)
    return defaultfilters.date(value, arg=arg)


@register.filter(is_safe=False)
def decimal(value, arg=None):
    if not isinstance(value, Decimal):
        value = parse_decimal(value)
    if arg is None:
        return value
    arg = unlazy(arg)
    return defaultfilters.floatformat(value, arg=arg)


@register.filter
def integer(value):
    return int(parse_decimal(value))


@register.filter(name='float')
def float_(value):
    return float(parse_decimal(value))


@register.filter
def boolean(value):
    value = unlazy(value)
    return value not in (None, '', 'false', 'False', False, 0)


@register.filter(is_safe=False)
def duration(value, arg='short'):
    if arg not in ('short', 'long'):
        return ''
    # value is expected to be a timedelta or a number of seconds
    value = unlazy(value)
    arg = unlazy(arg)
    if not isinstance(value, datetime.timedelta):
        try:
            value = datetime.timedelta(seconds=int(value) * 60)
        except (TypeError, ValueError):
            return ''
    return seconds2humanduration(int(value.total_seconds()), short=bool(arg != 'long'))


@register.filter(expects_localtime=True, is_safe=False)
def add_days(value, arg):
    value = unlazy(value)
    if value and not isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        try:
            value = get_as_datetime(value, strict_datetime=True)
        except ValueError:
            value = parse_date(value)
    is_datetime = isinstance(value, datetime.datetime)
    if not is_datetime:
        value = parse_date(value)  # consider only date, not hours
        if not value:
            return ''
    from wcs.variables import flexible_date

    arg = parse_decimal(arg)
    if not arg:
        return flexible_date(value)
    result = value + datetime.timedelta(days=float(arg))
    if not is_datetime and hasattr(result, 'date'):
        result = result.date()
    return flexible_date(result)


@register.filter(expects_localtime=True, is_safe=False)
def add_hours(value, arg):
    if hasattr(value, 'timetuple'):
        # extract real value in case of lazy object
        value = value.timetuple()
    value = parse_datetime(value)
    if not value:
        return ''
    from wcs.variables import flexible_date

    arg = parse_decimal(arg)
    if not arg:
        return flexible_date(value)
    return flexible_date(value + datetime.timedelta(hours=float(arg)))


@register.filter(expects_localtime=True, is_safe=False)
def add_minutes(value, arg):
    if hasattr(value, 'timetuple'):
        # extract real value in case of lazy object
        value = value.timetuple()
    value = parse_datetime(value)
    if not value:
        return ''
    from wcs.variables import flexible_date

    arg = parse_decimal(arg)
    if not arg:
        return flexible_date(value)
    return flexible_date(value + datetime.timedelta(minutes=float(arg)))


@register.filter(expects_localtime=True, is_safe=False)
def age_in_days(value, now=None):
    try:
        return evalutils.age_in_days(value, now)
    except ValueError:
        return ''


@register.filter(expects_localtime=True, is_safe=False)
def age_in_hours(value, now=None):
    # consider value and now as datetimes (and not dates)
    if hasattr(value, 'timetuple'):
        # extract real value in case of lazy object
        value = value.timetuple()
    value = parse_datetime(value)
    if not value:
        return ''
    if now is not None:
        if hasattr(now, 'timetuple'):
            now = now.timetuple()
        now = parse_datetime(now)
        if not now:
            return ''
    else:
        now = make_naive(localtime())
    return int((now - value).total_seconds() / 3600)


@register.filter(expects_localtime=True, is_safe=False)
def age_in_years(value, today=None):
    try:
        return evalutils.age_in_years_and_months(value, today)[0]
    except ValueError:
        return ''


@register.filter(expects_localtime=True, is_safe=False)
def age_in_months(value, today=None):
    try:
        years, months = evalutils.age_in_years_and_months(value, today)
    except ValueError:
        return ''
    return years * 12 + months


@register.filter(expects_localtime=True)
def datetime_in_past(value):
    value = parse_datetime(value)
    if not value:
        return False

    if is_naive(value):
        value = make_aware(value)
    date_now = make_aware(datetime.datetime.now())
    return value <= date_now


@register.filter(expects_localtime=True)
def is_working_day(value, saturday_is_a_working_day=False):
    value = parse_date(value)
    if not value:
        return False

    cal = calendar.get_calendar(saturday_is_a_working_day=saturday_is_a_working_day)
    if not cal:
        return False

    return cal.is_working_day(value)


@register.filter(expects_localtime=True)
def is_working_day_with_saturday(value):
    return is_working_day(value, saturday_is_a_working_day=True)


@register.filter(expects_localtime=True)
def add_working_days(value, arg, saturday_is_a_working_day=False):
    value = parse_date(value)
    if not value:
        return ''

    cal = calendar.get_calendar(saturday_is_a_working_day=saturday_is_a_working_day)
    if not cal:
        return ''

    try:
        return cal.add_working_days(value, int(arg))
    except ValueError:
        return ''


@register.filter(expects_localtime=True)
def add_working_days_with_saturday(value, arg):
    return add_working_days(value, arg, saturday_is_a_working_day=True)


@register.filter(expects_localtime=True)
def adjust_to_working_day(value, saturday_is_a_working_day=False):
    value = parse_date(value)
    if not value:
        return ''

    cal = calendar.get_calendar(saturday_is_a_working_day=saturday_is_a_working_day)
    if not cal:
        return ''

    if cal.is_working_day(value):
        return value
    # return next working day
    return cal.add_working_days(value, 1)


@register.filter(expects_localtime=True)
def adjust_to_working_day_with_saturday(value):
    return adjust_to_working_day(value, saturday_is_a_working_day=True)


@register.filter(expects_localtime=True)
def age_in_working_days(value, arg=None, saturday_is_a_working_day=False):
    value = parse_date(value)
    if not value:
        return ''

    if arg:
        arg = parse_date(arg)
        if not arg:
            return ''
    else:
        arg = datetime.datetime.now()

    cal = calendar.get_calendar(saturday_is_a_working_day=saturday_is_a_working_day)
    if not cal:
        return ''

    delta = cal.get_working_days_delta(value, arg)
    if arg.timetuple() < value.timetuple():
        delta = -delta
    return delta


@register.filter(expects_localtime=True)
def age_in_working_days_with_saturday(value, arg=None):
    return age_in_working_days(value, arg, saturday_is_a_working_day=True)


@register.filter(expects_localtime=True)
def adjust_to_week_monday(value):
    value = parse_date(unlazy(value))
    if not value:
        return ''
    return value - datetime.timedelta(days=value.weekday())


@register.filter(expects_localtime=True)
def iterate_days_until(value, until):
    value = parse_date(unlazy(value))
    until = parse_date(unlazy(until))
    if not (value and until):
        return
    while value < until:
        yield value
        value = value + datetime.timedelta(days=1)
    yield value


@register.simple_tag
def standard_text(text_id):
    return mark_safe(TextsDirectory.get_html_text(str(text_id)))


@register.simple_tag
def add_javascript(js_id):
    get_response().add_javascript([js_id])
    return ''


@register.simple_tag(takes_context=True)
def action_button(context, action_id, label=None, delay=3, message=None, done_message=None):
    formdata_id = context.get('form_number_raw')
    formdef_urlname = context.get('form_slug')
    formdef_type = context.get('form_type')
    if not (formdef_urlname and formdata_id):
        return ''
    if not label:
        raise TemplateError(_('{%% action_button %%} requires a label parameter'))
    token = get_publisher().token_class(expiration_delay=delay * 86400, size=64)
    token.type = 'action'
    token.context = {
        'form_slug': formdef_urlname,
        'form_type': formdef_type,
        'form_number_raw': formdata_id,
        'action_id': action_id,
        'label': label,
        'message': message,
        'done_message': done_message,
    }
    token.store()
    return '---===BUTTON:%s:%s===---' % (token.id, label)


@register.simple_tag(takes_context=True)
def temporary_access_button(
    context, label=None, days=None, hours=None, minutes=None, seconds=None, bypass_checks=False
):
    if not label:
        raise TemplateError(_('{%% temporary_action_button %%} requires a label parameter'))
    url = temporary_access_url(context, days=days, hours=hours, minutes=minutes, bypass_checks=bypass_checks)
    if not url:
        return ''
    return '---===BUTTON:URL:%s:%s===---' % (url, label)


@register.simple_tag(takes_context=True)
def webservice(context, ws_slug, **kwargs):
    with get_publisher().substitutions.temporary_feed({'parameters': kwargs}):
        return NamedWsCall.get_by_slug(ws_slug).call()


@register.filter
def concat(s1, s2):
    return f'{s1}{s2}'


@register.filter
def add(term1, term2):
    '''replace the "add" native django filter'''

    term1 = unlazy(term1)
    term2 = unlazy(term2)

    # append to term1 if term1 is a list and not term2
    if isinstance(term1, list) and not isinstance(term2, list):
        if term2 is None:
            return term1
        return term1 + [term2]

    # consider None content as the empty string
    if term1 is None:
        term1 = ''
    if term2 is None:
        term2 = ''

    # return available number if the other term is the empty string
    if term1 == '':
        try:
            return parse_decimal(term2, do_raise=True)
        except ValueError:
            pass
    if term2 == '':
        try:
            return parse_decimal(term1, do_raise=True)
        except ValueError:
            pass

    # compute addition if both terms are numbers
    try:
        return parse_decimal(term1, do_raise=True) + parse_decimal(term2, do_raise=True)
    except ValueError:
        pass

    # fallback to django add filter
    return defaultfilters.add(unlazy(term1), unlazy(term2))


@register.filter
def subtract(term1, term2):
    return parse_decimal(term1) - parse_decimal(term2)


@register.filter
def multiply(term1, term2):
    return parse_decimal(term1) * parse_decimal(term2)


@register.filter
def divide(term1, term2):
    try:
        return parse_decimal(term1) / parse_decimal(term2)
    except DecimalInvalidOperation:
        return ''
    except DecimalDivisionByZero:
        return ''


@register.filter
def modulo(term1, term2):
    try:
        return parse_decimal(term1) % parse_decimal(term2)
    except DecimalInvalidOperation:
        return ''
    except DecimalDivisionByZero:
        return ''


@register.filter(name='sum')
def sum_(list_):
    list_ = unlazy(list_)
    if isinstance(list_, str):
        # do not consider string as iterable, to avoid misusage
        return ''
    try:
        return sum(parse_decimal(term) for term in list_)
    except TypeError:  # list_ is not iterable
        return ''


@register.filter
def ceil(value):
    '''the smallest integer value greater than or equal to value'''
    return decimal(math.ceil(parse_decimal(value)))


@register.filter
def floor(value):
    return decimal(math.floor(parse_decimal(value)))


@register.filter(name='abs')
def abs_(value):
    return decimal(abs(parse_decimal(value)))


@register.filter
def clamp(value, minmax):
    try:
        value = parse_decimal(value, do_raise=True)
        min_value, max_value = (parse_decimal(x, do_raise=True) for x in unlazy(minmax).split())
    except ValueError:
        return ''
    return max(min_value, min(value, max_value))


@register.filter
def limit_low(value, min_value):
    try:
        return max(parse_decimal(value, do_raise=True), parse_decimal(min_value, do_raise=True))
    except ValueError:
        return ''


@register.filter
def limit_high(value, max_value):
    try:
        return min(parse_decimal(value, do_raise=True), parse_decimal(max_value, do_raise=True))
    except ValueError:
        return ''


@register.simple_tag
def version_hash():
    from wcs.qommon.admin.menu import get_vc_version

    return hashlib.md5(force_bytes(get_vc_version())).hexdigest()


def generate_token(alphabet, length):
    r = random.SystemRandom()
    return ''.join([r.choice(alphabet) for i in range(length)])


@register.simple_tag
def token_decimal(length=6):
    # entropy by default is log(10^6)/log(2) = 19.93 bits
    # decimal always need more length than alphanum for the same security level
    # for 128bits security level, length must be more than log(2^128)/log(10) = 38.53 digits
    return generate_token(string.digits, length)


@register.simple_tag
def token_alphanum(length=4):
    # use of a 28 characters alphabet using uppercase letters and digits but
    # removing confusing characters and digits 0, O, 1 and I.
    # entropy by default is log(28^4)/log(2) = 19.22 bits
    # for 128 bits security level length must be more than log(2^128)/log(28) = 26.62 characters
    return generate_token('23456789ABCDEFGHJKLMNPQRSTUVWXYZ', length)


@register.filter
def token_check(token1, token2):
    return force_str(token1).strip().upper() == force_str(token2).strip().upper()


def get_latlon(obj):
    if getattr(obj, 'geoloc', None):
        if 'base' in obj.geoloc:
            return obj.geoloc['base']['lat'], obj.geoloc['base']['lon']
        return None, None
    obj = unlazy(obj)
    if isinstance(obj, dict) and 'lat' in obj and 'lon' in obj:
        try:
            return float(obj['lat']), float(obj['lon'])
        except (TypeError, ValueError):
            pass
    if isinstance(obj, dict) and 'lat' in obj and 'lng' in obj:
        try:
            return float(obj['lat']), float(obj['lng'])
        except (TypeError, ValueError):
            pass
    if isinstance(obj, str) and ';' in obj:
        try:
            return float(obj.split(';')[0]), float(obj.split(';')[1])
        except ValueError:
            pass
    return None, None


@register.filter
def distance(obj1, obj2):
    lat1, lon1 = get_latlon(obj1)
    if lat1 is None or lon1 is None:
        return None
    lat2, lon2 = get_latlon(obj2)
    if lat2 is None or lon2 is None:
        return None
    geod = Geod(ellps='WGS84')
    distance = geod.inv(lon1, lat1, lon2, lat2)[2]
    return distance


def register_queryset_filter(name=None, attr=None):
    if callable(name) and attr is None:
        func = name
        return decorate_queryset_filter(func=name, name=func.__name__, attr=func.__name__)

    def dec(func):
        return decorate_queryset_filter(func=func, name=name or func.__name__, attr=attr)

    return dec


def decorate_queryset_filter(func, name, attr):
    @functools.wraps(func)
    def f(queryset, *args, **kwargs):
        if not hasattr(queryset, attr):
            get_publisher().record_error(
                _('|%(filter)s used on something else than a queryset (%(obj)r)')
                % {'filter': name, 'obj': queryset}
            )
            return None
        return func(queryset, *args, **kwargs)

    return register.filter(name=name, filter_func=f)


@register_queryset_filter
def set_geo_center(queryset, lazy_formdata):
    return queryset.set_geo_center(lazy_formdata)


@register_queryset_filter
def filter_by_distance(queryset, distance=1000):
    return queryset.filter_by_distance(distance=unlazy(distance))


@register.filter
def distance_filter(queryset, distance=1000):
    # deprecated name, for backward compatibility
    return filter_by_distance(queryset, distance=distance)


@register_queryset_filter
def same_user(queryset):
    return queryset.same_user()


@register_queryset_filter
def exclude_self(queryset):
    return queryset.exclude_self()


@register_queryset_filter
def current_user(queryset):
    return queryset.current_user()


@register_queryset_filter
def filter_by_user(queryset, user):
    return queryset.filter_by_user(unlazy(user))


@register_queryset_filter
def filter_by_status(queryset, status):
    return queryset.filter_by_status(status)


@register_queryset_filter
def filter_by_internal_id(queryset, form_internal_id):
    return queryset.filter_by_internal_id(unlazy(form_internal_id))


@register_queryset_filter
def filter_by_number(queryset, form_number):
    return queryset.filter_by_number(form_number)


@register_queryset_filter
def filter_by_identifier(queryset, form_identifier):
    return queryset.filter_by_identifier(form_identifier)


@register_queryset_filter
def pending(queryset):
    return queryset.pending()


@register_queryset_filter
def done(queryset):
    return queryset.done()


@register.filter
def objects(forms_source, slug):
    from wcs.formdef_base import FormDefDoesNotExist
    from wcs.variables import CardsSource, FormsSource

    if not isinstance(forms_source, (CardsSource, FormsSource)):
        get_publisher().record_error(_('|objects with invalid source (%r)') % forms_source)
        return None
    try:
        return getattr(forms_source, unlazy(slug)).objects
    except FormDefDoesNotExist:
        get_publisher().record_error(_('|objects with invalid reference (%r)') % slug)
        return None


@register_queryset_filter
def with_custom_view(queryset, custom_view_slug):
    return queryset.with_custom_view(custom_view_slug)


@register_queryset_filter
def with_drafts(queryset):
    return queryset.with_drafts()


@register_queryset_filter
def order_by(queryset, attribute):
    return queryset.order_by(unlazy(attribute))


@register_queryset_filter
def filter_by(queryset, attribute):
    return queryset.filter_by(unlazy(attribute))


@register_queryset_filter(attr='apply_filter_value')
def filter_value(queryset, value):
    return queryset.apply_filter_value(unlazy(value))


@register_queryset_filter(attr='apply_exclude_value')
def exclude_value(queryset, value):
    return queryset.apply_exclude_value(unlazy(value))


@register_queryset_filter(name='equal', attr='apply_eq')
def eq(queryset):
    return queryset.apply_eq()


@register_queryset_filter(name='i_equal', attr='apply_ieq')
def ieq(queryset):
    return queryset.apply_ieq()


@register_queryset_filter(name='not_equal', attr='apply_ne')
def ne(queryset):
    return queryset.apply_ne()


@register_queryset_filter(name='less_than', attr='apply_lt')
def lt(queryset):
    return queryset.apply_lt()


@register_queryset_filter(name='less_than_or_equal', attr='apply_lte')
def lte(queryset):
    return queryset.apply_lte()


@register_queryset_filter(name='greater_than', attr='apply_gt')
def gt(queryset):
    return queryset.apply_gt()


@register_queryset_filter(name='greater_than_or_equal', attr='apply_gte')
def gte(queryset):
    return queryset.apply_gte()


@register_queryset_filter(name='in', attr='apply_in')
def _in(queryset):
    return queryset.apply_in()


@register_queryset_filter(name='not_in', attr='apply_not_in')
def not_in(queryset):
    return queryset.apply_not_in()


@register_queryset_filter(name='absent', attr='apply_absent')
def absent(queryset):
    return queryset.apply_absent()


@register_queryset_filter(name='existing', attr='apply_existing')
def existing(queryset):
    return queryset.apply_existing()


@register_queryset_filter(name='between', attr='apply_between')
def between(queryset):
    return queryset.apply_between()


@register_queryset_filter(name='icontains', attr='apply_icontains')
def icontains(queryset):
    return queryset.apply_icontains()


@register_queryset_filter(name='is_today', attr='apply_is_today')
def is_today(queryset):
    return queryset.apply_is_today()


@register_queryset_filter(name='is_tomorrow', attr='apply_is_tomorrow')
def is_tomorrow(queryset):
    return queryset.apply_is_tomorrow()


@register_queryset_filter(name='is_yesterday', attr='apply_is_yesterday')
def is_yesterday(queryset):
    return queryset.apply_is_yesterday()


@register_queryset_filter(name='is_this_week', attr='apply_is_this_week')
def is_this_week(queryset):
    return queryset.apply_is_this_week()


@register_queryset_filter(name='is_future', attr='apply_is_future')
def is_future(queryset):
    return queryset.apply_is_future()


@register_queryset_filter(name='is_past', attr='apply_is_past')
def is_past(queryset):
    return queryset.apply_is_past()


@register_queryset_filter(name='is_today_or_future', attr='apply_is_today_or_future')
def is_today_or_future(queryset):
    return queryset.apply_is_today_or_future()


@register_queryset_filter(name='is_today_or_past', attr='apply_is_today_or_past')
def is_today_or_past(queryset):
    return queryset.apply_is_today_or_past()


@register.filter
def count(queryset):
    if hasattr(queryset, '__len__'):
        # don't unlazy if object has native __len__ support, this is required
        # for blocks as unlazying would give {'data': ..., 'schema': ...} and
        # the length would always be 2.
        return len(queryset)
    queryset = unlazy(queryset)
    if queryset is None:
        return 0
    try:
        return len(queryset)
    except TypeError:
        get_publisher().record_error(_('|count used on uncountable value'))
        return 0


@register.filter
def reproj(coords, projection_name):
    proj = pyproj.Proj(init='EPSG:4326')
    target_proj = pyproj.Proj(init=projection_name)
    return pyproj.transform(proj, target_proj, coords['lon'], coords['lat'])


@register.filter
def has_role(user, role_name):
    if not callable(getattr(user, 'get_roles', None)):
        # do not fail on non-user objects, just return False
        return False
    for role_id in user.get_roles():
        try:
            if role_name == get_publisher().role_class.get(role_id).name:
                return True
        except KeyError:  # role has been deleted
            pass
    return False


@register.filter
def roles(user):
    if not callable(getattr(user, 'get_roles', None)):
        # do not fail on non-user objects, just return empty list
        return []
    role_ids = user.get_roles()
    roles = [get_publisher().role_class.get(x, ignore_errors=True, ignore_migration=True) for x in role_ids]
    return [x.name for x in roles if x]


@register.filter
def user_id_for_service(user, service_slug):
    from wcs.wscalls import call_webservice

    name_id = None
    if isinstance(user, str):
        name_id = user
    elif getattr(user, 'nameid', None):
        name_id = user.nameid
    if not name_id:
        return ''
    idp_api_url = get_publisher().get_site_option('idp_api_url', 'variables') or ''
    if not idp_api_url:
        return ''
    url = urllib.parse.urljoin(idp_api_url, 'users/%s/service/%s/' % (name_id, service_slug))
    _, status, data = call_webservice(url, method='GET', timeout=5, cache=True)
    if status != 200:
        return ''
    try:
        user_id = json.loads(data)['data']['user']['id']
        return str(user_id or '')
    except (ValueError, TypeError, KeyError):
        pass
    return ''


@register.filter
def language_detect(value):
    if langdetect is None:
        return ''
    try:
        return langdetect.detect(str(value))
    except LangDetectException:
        return ''


@register.filter(is_safe=False)
def phonenumber_fr(value, separator=' '):
    DROMS = ('262', '508', '590', '594', '596')

    value = unlazy(value)
    if not value or not isinstance(value, str):
        return value
    number = value.strip()
    if not number:
        return value
    if number[0] == '+':
        international = '+'
        number = '00' + number[1:]
    else:
        international = '00' + separator
    number = ''.join(c for c in number if c in '0123456789')

    def in_pairs(num):
        return separator.join(num[i * 2 : i * 2 + 2] for i in range(len(num) // 2))

    # local number
    if len(number) == 10 and number[0] == '0' and number[1] in '123456789':
        return in_pairs(number)
    # international
    if len(number) == 14 and number[0:5] == '00330':
        # +/00 33 (0)x xx xx xx xx : remove (0)
        number = number[0:4] + number[5:]
    if len(number) == 13 and number[0:4] == '0033':
        return international + '33' + separator + number[4] + separator + in_pairs(number[5:])
    if len(number) == 11 and number[0:2] == '00' and number[2:5] in DROMS:
        return international + number[2:5] + separator + in_pairs(number[5:])

    # unknown
    return value


@register.filter
def is_french_mobile_phone_number(value):
    # check the given value is a valid French mobile phone number (Metropolitan
    # or overseas, with support for local prefixes).
    value = unlazy(value)

    if not value:
        return False

    value = value.strip().replace(' ', '')
    return validate_mobile_phone_local(value, region_code='FR')


@register.filter
def is_empty(value):
    from wcs.variables import LazyFormDefObjectsManager, LazyList

    value = unlazy(value)

    if isinstance(value, (str, list, dict)):
        return not value
    if isinstance(value, (LazyFormDefObjectsManager, LazyList)):
        return not list(value)
    return value is None


@register.filter
def strip_metadata(value):
    return unlazy(value).strip_metadata()


@register.filter
def strip_emoji(value):
    return emoji.replace_emoji(unlazy(value) or '', replace='').strip()


@register.filter
def rename_file(value, new_name):
    from wcs.fields import FileField

    file_object = FileField.convert_value_from_anything(value)
    if not file_object:
        return None
    new_name = unlazy(new_name)
    if not new_name:
        get_publisher().record_error(_('|rename_file called with empty new name'))
        return file_object
    if new_name.endswith('.$ext'):
        if file_object.base_filename:
            new_name = os.path.splitext(new_name)[0] + os.path.splitext(file_object.base_filename)[1]
        else:
            new_name = new_name.removesuffix('.$ext')
    new_name = make_safe_filename(new_name)
    file_object.orig_filename = new_name
    file_object.base_filename = new_name
    return file_object


@register.filter
def convert_image_format(value, new_format):
    from wcs.fields import FileField

    formats = {
        'jpeg': 'image/jpeg',
        'pdf': 'application/pdf',
        'png': 'image/png',
    }
    if new_format not in formats:
        get_publisher().record_error(
            _('|convert_image_format: unknown format (must be one of %s)') % ', '.join(formats.keys())
        )
        return None

    try:
        file_object = FileField.convert_value_from_anything(value)
    except ValueError:
        file_object = None
    if not file_object:
        get_publisher().record_error(_('|convert_image_format: missing input'))
        return None

    if file_object.base_filename:
        current_name, current_format = os.path.splitext(file_object.base_filename)
        if current_format == f'.{new_format}':
            return file_object
        new_name = f'{current_name}.{new_format}'
    else:
        new_name = '%s.%s' % (_('file'), new_format)

    try:
        proc = subprocess.run(
            ['gm', 'convert', '-', f'{new_format}:-'],
            input=file_object.get_content(),
            capture_output=True,
            check=True,
        )
    except FileNotFoundError:
        get_publisher().record_error(_('|convert_image_format: not supported'))
        return None
    except subprocess.CalledProcessError as e:
        get_publisher().record_error(_('|convert_image_format: conversion error (%s)' % e.stderr.decode()))
        return None

    new_file_object = FileField.convert_value_from_anything(
        {'content': proc.stdout, 'filename': new_name, 'content_type': formats[new_format]}
    )

    return new_file_object


@register.filter
def first(value):
    try:
        return defaultfilters.first(value)
    except (TypeError, AttributeError, KeyError):
        return ''


@register.filter
def last(value):
    try:
        return defaultfilters.last(value)
    except (TypeError, AttributeError, KeyError):
        return ''


@register.filter(name='random')
def random_(value):
    try:
        return defaultfilters.random(value)
    except (IndexError, TypeError, KeyError):
        return ''


@register.filter(name='list')
def list_(value):
    # turn a generator into a list
    real_value = unlazy(value)
    if (
        isinstance(real_value, collections.abc.Iterable)
        and not isinstance(real_value, (collections.abc.Mapping, str))
        and not hasattr(real_value, 'base_filename')
    ):
        return list(real_value)

    return [real_value]


@register.filter(name='set')
def set_(value):
    # turn a generator into a set
    return set(unlazy(value))


@register.filter(name='qrcode')
def qrcode_filter(value, name=None):
    if not qrcode:
        return ''
    value = unlazy(value)
    if not isinstance(value, str):
        return ''
    img = qrcode.make(value)
    buf = io.BytesIO()
    img.save(buf)
    upload = upload_storage.PicklableUpload(name or 'qrcode.png', 'image/png')
    upload.receive([buf.getvalue()])
    return upload


@register.simple_tag
def newline(windows=False):
    return '\r\n' if windows else '\n'


@register.filter
def repeat(to_repeat, repeat_count):
    try:
        repeat_count = parse_decimal(unlazy(repeat_count), do_raise=True)
    except ValueError:
        get_publisher().record_error(_('Repetition count %(val)r is not a number') % {'val': repeat_count})
        return ''

    multiplier = int(repeat_count)
    if multiplier != repeat_count:
        get_publisher().record_error(
            _('Repetition count (%(val)s) have non-zero decimal part') % {'val': repeat_count}
        )
        return ''
    if multiplier < 0:
        get_publisher().record_error(_('Repetition count (%(val)s) is negative') % {'val': repeat_count})
        return ''

    to_repeat = unlazy(to_repeat)
    if isinstance(to_repeat, (int, float)) and not isinstance(to_repeat, bool):
        to_repeat = str(to_repeat)
    elif not isinstance(to_repeat, (bytes, str, list, tuple)):
        get_publisher().record_error(
            _('Cannot repeat something that is not a string or a list (%(val)r)') % {'val': to_repeat}
        )
        return ''

    return to_repeat * multiplier


@register.simple_tag(takes_context=True)
def block_value(context, append=False, merge=False, init=False, output=None, **kwargs):
    # kwargs are varnames of block subfields
    # * append=True will add a "row" to the existing block.
    # * merge=True will merge the value into an existing row of the block
    #   it can be True to alter the last row, or a row number (counting from 0).
    # Both will create a row if there's no existing value.
    #
    # It is also possible to construct multiple row values, it starts with a first
    # call to initialize the block, then any number of {% block_value %} with append
    # set to the initialized block, then getting the result with a finishing block.
    # For example:
    #   {% block_value init=True as foobar %}
    #   {% for a in "ABC" %}{% block_value a=a append=foobar as foobar %}
    #   {% endfor %}
    #   {% block_value output=foobar %}
    # (all on a single line)

    from wcs.fields.block import BlockRowValue

    if output:
        value = get_publisher().get_cached_complex_data(output)
    elif init is True:
        value = BlockRowValue(append=True)
    else:
        existing = None
        if not isinstance(append, bool):
            existing = get_publisher().get_cached_complex_data(append)
            append = True
            if not isinstance(existing, BlockRowValue):
                existing = None

        value = BlockRowValue(
            append=append, merge=merge, existing=existing, **{k: unlazy(v) for k, v in kwargs.items()}
        )

    if context.get('allow_complex'):
        return get_publisher().cache_complex_data(value, '<block value>')
    return value  # mostly non-useful


@register.filter
def as_template(value):
    from wcs.workflows import WorkflowStatusItem

    return WorkflowStatusItem.compute(unlazy(value))


@register.filter(is_safe=True)
def stripsometags(value, arg=None):
    arg = arg or ''
    allowed_tags = arg.split(',')
    allowed_tags = [t.strip() for t in allowed_tags]
    allowed_tags = [t for t in allowed_tags if t]
    return strip_some_tags(unlazy(value), allowed_tags)


@register.filter(is_safe=True)
def translate(string, context=None):
    string = unlazy(string)
    if string is None:
        return None
    return get_publisher().translate(string, context=context, register=True)


@register.filter(is_safe=False)
def default_if_none(value, arg):
    value = unlazy(value)
    if value is None:
        return arg
    return value


@register.filter(is_safe=True)
def intcomma(value):
    return humanize_intcomma(decimal(value))


@register.filter
def get_preference(user, pref_name):
    return user.get_preference(pref_name) if user else None


@register.simple_tag(takes_context=True)
def temporary_access_url(
    context, days=None, hours=None, minutes=None, seconds=None, bypass_checks=False, **kwargs
):
    # {% temporary_access_url %}
    # parameters:
    # * days/hours/minutes/seconds, to set duration of access (by default, 30 minutes)
    # * bypass_checks, to give direct access, without checking if tracking code is enabled or
    #   verification fields.
    formdata_id = context.get('form_number_raw')
    if not formdata_id:
        # try to get id of draft formdata
        try:
            formdata_id = context.get('form')._formdata._draft_id
        except (AttributeError, KeyError):
            pass
    formdef_urlname = context.get('form_slug')
    formdef_type = context.get('form_type')
    if not (formdef_type == 'formdef' and formdef_urlname and formdata_id):
        return ''

    from wcs.formdef import FormDef

    formdef = FormDef.get_by_urlname(formdef_urlname)
    try:
        formdata = formdef.data_class().get(formdata_id)
    except KeyError:
        # formdata somehow got removed, ignore
        return ''

    duration = 0
    for amount, unit in ((days, 86400), (hours, 3600), (minutes, 60), (seconds, 1)):
        duration += (unlazy(amount) or 0) * unit
    if not duration:
        duration = 30 * 60  # 30 minutes

    duration = min(duration, 10 * 86400)  # maximum duration is 10 days.

    return formdata.get_temporary_access_url(duration, bypass_checks=bypass_checks)


@register.filter(is_safe=True)
def json_dumps(value):
    try:
        return mark_safe(json.dumps(value))
    except TypeError:
        return ''


@register.simple_tag(takes_context=True)
def make_public_url(context, url=None):
    url = unlazy(url)
    if not url:
        return ''
    token = get_session().create_token('sign-url-token', {'url': url})
    return '/api/sign-url-token/%s' % token.id


@register.filter
def with_auth(value, arg):
    url = unlazy(value)
    if not url:
        return ''
    parsed_url = urllib.parse.urlparse(url)
    new_netloc = '%s@%s' % (arg, parsed_url.netloc.rsplit('@', 1)[-1])
    return urllib.parse.urlunparse(parsed_url._replace(netloc=new_netloc))


@register.filter
def wbr(value):
    return mark_safe(value.replace('_', '_<wbr/>'))


@register.filter
def check_no_duplicates(value):
    value = unlazy(value)
    if not isinstance(value, (type(None), tuple, list, set)):
        get_publisher().record_error(_('|check_no_duplicates not used on a list (%s)') % value)
        return False
    return bool(len(value or []) == len(set(value or [])))


@register.filter
def details_format(value, format=None):
    if format is None:
        get_publisher().record_error(_('|details_format called without specifying a format'))
        return ''
    if format not in ('text',):
        get_publisher().record_error(_('|details_format called with unknown format (%s)') % format)
        return ''
    return evalutils.details_format(value, format=format)


@register.filter
def housenumber_number(housenumber):
    housenumber = unlazy(housenumber)
    if not housenumber:
        return ''
    match = re.match(r'^\s*([0-9]+)(.*)$', force_str(housenumber))
    if not match:
        return ''
    return match.groups()[0]


@register.filter
def housenumber_btq(housenumber):
    housenumber = unlazy(housenumber)
    if not housenumber:
        return ''
    match = re.match(r'^\s*([0-9]+)(.*)$', force_str(housenumber))
    if not match:
        return ''
    return match.groups()[1]


@register.filter
def searchify(string):
    # like slugify but treat single quotes as spaces, to match the behaviour
    # of the window.slugify javascript function.
    return slugify(string.replace("'", ' '))


class ZipNode(template.Node):
    def __init__(self, filename, content=None, target_var=None):
        self.filename = filename
        self.content = content
        self.target_var = target_var

    def write_value(self, value, archive, basename, extension, counter=''):
        from wcs.fields import FileField

        try:
            file_value = FileField.convert_value_from_anything(value)
        except ValueError:
            return False
        if not file_value:
            return False

        extension = mimetypes.guess_extension(file_value.content_type, strict=True) or extension
        if not os.path.basename(basename) and file_value.base_filename:
            basename = basename + os.path.splitext(file_value.base_filename)[0]
            # do not add the counter if we use the original file name
            counter = ''
        if not os.path.basename(basename):
            basename = basename + f'noname{len(archive.namelist())}'
        full_filename = f'{basename}{counter}{extension}'
        i = 1
        while full_filename in archive.namelist():
            full_filename = f'{basename}-dup{i}{extension}'
            i += 1

        with file_value.get_file_pointer() as fp:
            with archive.open(full_filename, 'w') as fd:
                for buffer in iter(lambda: fp.read(2**17), b''):  # Use a 128k buffer
                    fd.write(buffer)
        return True

    def return_value(self, context, value):
        if self.target_var is not None:
            context[self.target_var] = value
            return ''
        if context.get('allow_complex'):
            return get_publisher().cache_complex_data(value, str(value))
        return value

    def render(self, context):
        zip_filename = unlazy(self.filename.resolve(context))
        if not zip_filename or not ZIP_FILENAME_RE.match(zip_filename):
            get_publisher().record_error(
                _('{%% zip %%} invalid zip filename "%s" reverting to "archive.zip"') % zip_filename
            )
            zip_filename = 'archive.zip'

        with tempfile.SpooledTemporaryFile() as fd:
            with zipfile.ZipFile(fd, 'w') as archive:
                for basename, extension, var in self.content or []:
                    value = var.resolve(context, ignore_failures=True)
                    if not value:
                        continue
                    value = unlazy(value)
                    if not isinstance(value, list):
                        self.write_value(value, archive, basename, extension)
                    else:
                        counter = 1
                        for v in value:
                            if self.write_value(
                                v,
                                archive,
                                basename,
                                extension,
                                counter=f'-{counter}',
                            ):
                                counter += 1
            fd.seek(0)
            result = PicklableUpload(zip_filename, 'application/x-zip', None)
            result.receive(iter(lambda: fd.read(2**17), b''))  # Use a 128k buffer

        return self.return_value(context, result)


ZIP_FILENAME_RE = re.compile(r'^[a-zA-Z0-9_./-]*$')


@register.tag
def zip(parser, token):
    '''Zip template tag

    {% zip "archive.zip" dir1/file1.pdf=form_var_file1 dir1/piece_jointe.pdf=form_var_block|getlist:"file" %}

    '''
    try:
        dummy, zip_filename, *args = token.split_contents()
    except ValueError:
        raise template.TemplateSyntaxError(_('{%% zip %%} missing zip filename'))

    try:
        zip_filename_expression = parser.compile_filter(zip_filename)
    except template.TemplateSyntaxError:
        raise template.TemplateSyntaxError(
            _('{%% zip %%} invalid zip filename expression "%s"') % zip_filename
        )

    content = []
    target_var = None
    if len(args) >= 2 and args[-2] == 'as':
        target_var = args[-1]
        args = args[:-2]
    for arg in args:
        try:
            filename, var = arg.split('=', 1)
        except ValueError:
            raise template.TemplateSyntaxError(_('{%% zip %%} invalid content descriptor "%s"') % arg)

        if not ZIP_FILENAME_RE.match(filename):
            raise template.TemplateSyntaxError(_('{%% zip %%} invalid content filename "%s"') % filename)

        filename = filename.lstrip('/')
        basename, extension = os.path.splitext(filename)

        try:
            file_content_expression = parser.compile_filter(var)
        except template.TemplateSyntaxError:
            raise template.TemplateSyntaxError(_('{%% zip %%} invalid content expression "%s"') % var)
        content.append((basename, extension, file_content_expression))
    return ZipNode(zip_filename_expression, content, target_var)


@register.filter
def sha256(string, salt=None):
    string = force_str(unlazy(string))
    if salt is not None:
        string += force_str(unlazy(salt))
    return hashlib.sha256(string.encode()).hexdigest()
