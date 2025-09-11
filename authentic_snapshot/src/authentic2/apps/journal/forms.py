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

from django import forms
from django.http import QueryDict
from django.utils.formats import date_format
from django.utils.functional import cached_property
from django.utils.translation import gettext_lazy as _

from . import models, search_engine


class Page:
    def __init__(self, form, events, is_first_page, is_last_page):
        self.form = form
        self.events = events
        self.is_first_page = is_first_page
        self.is_last_page = is_last_page
        self.limit = form.limit

    @property
    def previous_page_cursor(self):
        return None if self.is_first_page else self.events[0].cursor

    @property
    def next_page_cursor(self):
        return None if self.is_last_page else self.events[-1].cursor

    @cached_property
    def next_page_url(self):
        if self.is_last_page:
            return None
        else:
            return self.form.make_url('after_cursor', self.events[-1].cursor)

    @cached_property
    def first_page_url(self):
        return self.form.make_url('after_cursor', '0 0')

    @cached_property
    def previous_page_url(self):
        if self.is_first_page:
            return None
        else:
            return self.form.make_url('before_cursor', self.events[0].cursor)

    @cached_property
    def last_page_url(self):
        return self.form.make_url('before_cursor', '%s 0' % (2**31 - 1))

    def __bool__(self):
        return bool(self.events)

    def __iter__(self):
        return reversed(self.events)


class DateHierarchy:
    def __init__(self, form, year=None, month=None, day=None):
        self.form = form
        self.year = year
        self.month = month
        self.day = day

    @property
    def title(self):
        if self.day:
            return date_format(self.current_datetime, 'DATE_FORMAT')
        elif self.month:
            return date_format(self.current_datetime, 'F Y')
        elif self.year:
            return str(self.year)

    @cached_property
    def back_urls(self):
        def helper():
            if self.year:
                yield _('Journal - All dates'), self.form.make_url(exclude=['year', 'month', 'day'])
                if self.month:
                    yield str(self.year), self.form.make_url(exclude=['month', 'day'])
                    current_datetime = datetime(self.year, self.month or 1, self.day or 1)
                    month_name = date_format(current_datetime, format='F Y').title()
                    if self.day:
                        yield month_name, self.form.make_url(exclude=['day'])
                        yield str(self.day), '#'
                    else:
                        yield month_name, '#'
                else:
                    yield str(self.year), '#'
            else:
                yield _('Journal - All dates'), '#'

        return list(helper())

    @property
    def current_datetime(self):
        return datetime(self.year or 1900, self.month or 1, self.day or 1)

    @property
    def month_name(self):
        return date_format(self.current_datetime, format='F')

    @cached_property
    def choice_urls(self):
        def helper():
            if self.day:
                return
            elif self.month:
                for day in self.form.days:
                    yield str(day), self.form.make_url('day', day)
            elif self.year:
                for month in self.form.months:
                    dt = datetime(self.year, month, 1)
                    month_name = date_format(dt, format='F')
                    yield month_name, self.form.make_url('month', month, exclude=['day'])
            else:
                for year in self.form.years:
                    yield str(year), self.form.make_url('year', year, exclude=['month', 'day'])

        return list(helper())

    @property
    def choice_name(self):
        if self.day:
            return
        elif self.month:
            return _('Days of %s') % self.month_name
        elif self.year:
            return _('Months of %s') % self.year
        else:
            return _('Years')


class SearchField(forms.CharField):
    type = 'search'


class JournalForm(forms.Form):
    year = forms.CharField(label=_('year'), widget=forms.HiddenInput(), required=False)

    month = forms.CharField(label=_('month'), widget=forms.HiddenInput(), required=False)

    day = forms.CharField(label=_('day'), widget=forms.HiddenInput(), required=False)

    after_cursor = forms.CharField(widget=forms.HiddenInput(), required=False)

    before_cursor = forms.CharField(widget=forms.HiddenInput(), required=False)

    search = SearchField(required=False, label='')

    search_engine_class = search_engine.JournalSearchEngine

    def __init__(self, *args, **kwargs):
        self.queryset = kwargs.pop('queryset', None)
        if self.queryset is None:
            self.queryset = models.Event.objects.all()
        self.limit = kwargs.pop('limit', 20)
        search_engine_class = kwargs.pop('search_engine_class', None)
        if search_engine_class:
            self.search_engine_class = search_engine_class
        super().__init__(*args, **kwargs)

    @cached_property
    def years(self):
        self.is_valid()
        return [dt.year for dt in self.queryset.datetimes('timestamp', 'year')]

    @cached_property
    def months(self):
        self.is_valid()
        if self.cleaned_data.get('year'):
            return [
                dt.month
                for dt in self.queryset.filter(timestamp__year=self.cleaned_data['year']).datetimes(
                    'timestamp', 'month'
                )
            ]
        return []

    @cached_property
    def days(self):
        self.is_valid()
        if self.cleaned_data.get('month') and self.cleaned_data.get('year'):
            return [
                dt.day
                for dt in self.queryset.filter(
                    timestamp__year=self.cleaned_data['year'], timestamp__month=self.cleaned_data['month']
                ).datetimes('timestamp', 'day')
            ]
        return []

    @staticmethod
    def _clean_integer_value(value):
        try:
            return int(value)
        except ValueError:
            return None

    def clean_year(self):
        return self._clean_integer_value(self.cleaned_data['year'])

    def clean_month(self):
        return self._clean_integer_value(self.cleaned_data['month'])

    def clean_day(self):
        return self._clean_integer_value(self.cleaned_data['day'])

    def clean(self):
        super().clean()

        year = self.cleaned_data.get('year')
        if year not in self.years:
            self.cleaned_data['year'] = None
        month = self.cleaned_data.get('month')
        if month not in self.months:
            self.cleaned_data['month'] = None
        day = self.cleaned_data.get('day')
        if day not in self.days:
            self.cleaned_data['day'] = None

    def clean_after_cursor(self):
        return models.EventCursor.parse(self.cleaned_data['after_cursor'])

    def clean_before_cursor(self):
        return models.EventCursor.parse(self.cleaned_data['before_cursor'])

    def clean_search(self):
        self.cleaned_data['_search_query'] = self.search_engine_class().query(
            query_string=self.cleaned_data['search']
        )
        return self.cleaned_data['search']

    def get_queryset(self, limit=None):
        self.is_valid()

        qs = self.queryset
        year = self.cleaned_data.get('year')
        month = self.cleaned_data.get('month')
        day = self.cleaned_data.get('day')
        search_query = self.cleaned_data.get('_search_query')

        if year:
            qs = qs.filter(timestamp__year=year)
        if month:
            qs = qs.filter(timestamp__month=month)
        if day:
            qs = qs.filter(timestamp__day=day)
        if search_query:
            qs = qs.filter(search_query)
        return qs

    def make_querydict(self, name=None, value=None, exclude=()):
        querydict = QueryDict(mutable=True)
        for k, v in self.cleaned_data.items():
            if k.startswith('_'):
                continue
            if k in exclude:
                continue
            if v:
                querydict[k] = str(v)

        if name:
            if name in ['after_cursor', 'before_cursor']:
                querydict.pop('after_cursor', None)
                querydict.pop('before_cursor', None)
            assert name in self.fields
            assert value is not None
            querydict[name] = value
        return querydict

    def make_url(self, name=None, value=None, exclude=()):
        return '?' + self.make_querydict(name=name, value=value, exclude=exclude).urlencode()

    @cached_property
    def page(self):
        self.is_valid()

        after_cursor = self.cleaned_data['after_cursor']
        before_cursor = self.cleaned_data['before_cursor']
        first = False
        last = False
        limit = self.limit

        qs = self.get_queryset()
        if after_cursor:
            page = list(qs[after_cursor : (limit + 2)])
            first = not (qs[-1:after_cursor])
            if len(page) > limit:
                last = len(page) != (limit + 2)
                if page[0].cursor == after_cursor:
                    page = page[1 : (limit + 1)]
                else:
                    page = page[:limit]
            else:
                last = True
                before_cursor = after_cursor if not page else page[-1].cursor
                page = list(qs[-(limit + 1) : before_cursor])
                first = len(page) < (limit + 1)
                page = page[-limit:]
        elif before_cursor:
            page = list(qs[-(limit + 2) : before_cursor])
            last = not (qs[before_cursor:1])
            if len(page) > limit:
                first = len(page) != (limit + 2)
                page = page[-(limit + 1) : -1]
            else:
                first = True
                after_cursor = before_cursor if not page else page[0].cursor
                page = list(qs[after_cursor : (limit + 1)])
                last = len(page) < (limit + 1)
                page = page[:limit]
        else:
            qs = qs.order_by('-timestamp', '-id')
            page = qs[: (limit + 1) : -1]
            first = len(page) <= limit
            last = True
            page = page[-limit:]
        models.prefetch_events_references(page, prefetcher=self.prefetcher)
        if page:
            self.data = self.data.copy()
            self.cleaned_data['after_cursor'] = self.data['after_cursor'] = page[0].cursor.minus_one()
            self.cleaned_data['before_cursor'] = ''
        return Page(self, page, first, last)

    def prefetcher(self, model, pks):
        return []

    @cached_property
    def date_hierarchy(self):
        self.is_valid()
        return DateHierarchy(
            self,
            year=self.cleaned_data['year'],
            month=self.cleaned_data['month'],
            day=self.cleaned_data['day'],
        )

    @property
    def url(self):
        return self.make_url()
