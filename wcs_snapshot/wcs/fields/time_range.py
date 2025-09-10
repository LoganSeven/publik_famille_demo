# w.c.s. - web application for online forms
# Copyright (C) 2005-2025  Entr'ouvert
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


import datetime
import json

from django.utils.formats import time_format
from quixote import get_request

from wcs import data_sources
from wcs.qommon import _, misc
from wcs.qommon.form import HiddenWidget, StringWidget, TimeRangeWidget

from .base import WidgetField, register_field_class


class TimeRangeField(WidgetField):
    key = 'time-range'
    description = _('Time range')
    section = 'agendas'

    data_source = None
    display_mode = None

    widget_class = TimeRangeWidget

    def perform_more_widget_changes(self, form, kwargs, edit=True):
        metadata = {}
        kwargs['options_with_attributes'] = (
            data_sources.get_items(self.data_source, include_disabled=True, metadata=metadata)
            if self.data_source
            else []
        )
        kwargs['options_metadata'] = metadata

    def store_display_value(self, data, field_id, raise_on_error=False):
        value = data.get(field_id)
        return self.get_display_value(value)

    def get_display_value(self, value):
        if not value or not value.get('start_datetime'):
            return ''

        start_datetime = datetime.datetime.strptime(value['start_datetime'], '%Y-%m-%d %H:%M')
        end_datetime = datetime.datetime.strptime(value['end_datetime'], '%Y-%m-%d %H:%M')
        return _('On %(date)s from %(start_time)s until %(end_time)s') % {
            'date': start_datetime.strftime(misc.date_format()),
            'start_time': time_format(start_datetime),
            'end_time': time_format(end_datetime),
        }

    def get_structured_value(self, data):
        return data.get(self.id)

    def convert_value_from_str(self, value):
        if value:
            return json.loads(value)

    def from_json_value(self, value):
        return value

    def add_to_view_form(self, form, value=None):
        field_key = 'f%s' % self.id
        label_value = self.get_display_value(value)
        form.add(
            StringWidget,
            '%s_label' % field_key,
            value=label_value,
            size=len(label_value) + 2,
            readonly='readonly',
            title=self.label,
        )

        get_request().form[field_key] = json.dumps(value)
        form.add(HiddenWidget, field_key, value=json.dumps(value))

    def fill_admin_form(self, form, formdef):
        super().fill_admin_form(form, formdef)
        form.remove('prefill')

        form.add(
            data_sources.DataSourceSelectionWidget,
            'data_source',
            title=_('Agenda'),
            value=self.data_source,
            allowed_source_types={'named'},
            allowed_external_type='free_range',
        )

    def get_admin_attributes(self):
        return WidgetField.get_admin_attributes(self) + [
            'data_source',
        ]


register_field_class(TimeRangeField)
