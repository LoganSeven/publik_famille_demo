# w.c.s. - web application for online forms
# Copyright (C) 2005-2023  Entr'ouvert
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

import time
import xml.etree.ElementTree as ET

from wcs.qommon import _, evalutils, misc
from wcs.qommon.form import CheckboxWidget, DateWidget
from wcs.qommon.misc import date_format, get_as_datetime, strftime
from wcs.qommon.ods import NS as OD_NS
from wcs.qommon.ods import clean_text as od_clean_text

from .base import WidgetField, register_field_class


class DateField(WidgetField):
    key = 'date'
    description = _('Date')
    available_for_filter = True
    allow_complex = True

    widget_class = DateWidget
    minimum_date = None
    maximum_date = None
    minimum_is_future = False
    date_in_the_past = False
    date_can_be_today = False
    extra_attributes = [
        'minimum_date',
        'minimum_is_future',
        'maximum_date',
        'date_in_the_past',
        'date_can_be_today',
    ]

    def fill_admin_form(self, form, formdef):
        super().fill_admin_form(form, formdef)
        form.add(DateWidget, 'minimum_date', title=_('Minimum Date'), value=self.minimum_date)
        form.add(
            CheckboxWidget,
            'minimum_is_future',
            title=_('Date must be in the future'),
            value=self.minimum_is_future,
            hint=_('This option is obviously not compatible with setting a minimum date'),
        )
        form.add(DateWidget, 'maximum_date', title=_('Maximum Date'), value=self.maximum_date)
        form.add(
            CheckboxWidget,
            'date_in_the_past',
            title=_('Date must be in the past'),
            value=self.date_in_the_past,
            hint=_('This option is obviously not compatible with setting a maximum date'),
        )
        form.add(
            CheckboxWidget,
            'date_can_be_today',
            title=_('Date can be present day'),
            value=self.date_can_be_today,
            hint=_('This option is only useful combined with one of the previous checkboxes.'),
        )

    def get_admin_attributes(self):
        return WidgetField.get_admin_attributes(self) + [
            'minimum_date',
            'minimum_is_future',
            'maximum_date',
            'date_in_the_past',
            'date_can_be_today',
        ]

    @classmethod
    def convert_value_from_anything(cls, value):
        if value is None or value == '':
            return None
        date_value = evalutils.make_date(value).timetuple()  # could raise ValueError
        return date_value

    def convert_value_from_str(self, value):
        if not value:
            return None
        try:
            return get_as_datetime(value).timetuple()
        except ValueError:
            return None

    def convert_value_to_str(self, value):
        if value is None:
            return ''
        if isinstance(value, str):
            return value
        try:
            return strftime(date_format(), value)
        except TypeError:
            return ''

    def add_to_form(self, form, value=None):
        if value and not isinstance(value, str):
            value = self.convert_value_to_str(value)
        return WidgetField.add_to_form(self, form, value=value)

    def add_to_view_form(self, form, value=None):
        value = strftime(misc.date_format(), value)
        return super().add_to_view_form(form, value=value)

    def get_view_value(self, value, **kwargs):
        try:
            return strftime(misc.date_format(), value)
        except TypeError:
            return value

    def get_opendocument_node_value(self, value, formdata=None, **kwargs):
        span = ET.Element('{%s}span' % OD_NS['text'])
        span.text = od_clean_text(self.get_view_value(value))
        return span

    def get_json_value(self, value, **kwargs):
        try:
            return strftime('%Y-%m-%d', value)
        except TypeError:
            return ''

    def from_json_value(self, value):
        try:
            return time.strptime(value, '%Y-%m-%d')
        except (TypeError, ValueError):
            return None


register_field_class(DateField)
