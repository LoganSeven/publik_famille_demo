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

from datetime import date, timedelta

from django.db.models import Max, Min
from django.utils.translation import gettext_lazy as _


def _json_value(value):
    if isinstance(value, (dict, list, str, int, bool)) or value is None:
        return value
    return str(value)


def form_to_old_new(form):
    if hasattr(form, 'validated_data'):
        # form is a DRF serializer
        return {'new': {k: _json_value(v) for k, v in form.validated_data.items()}}
    old = {}
    new = {}
    for key in form.changed_data:
        old_value = form.initial.get(key)
        if old_value is not None:
            old[key] = _json_value(old_value)
        new[key] = _json_value(form.cleaned_data.get(key))
    return {'old': old, 'new': new}


class Statistics:
    time_label_formats = {
        'year': '%Y',
        'month': '%Y-%m',
        'day': '%Y-%m-%d',
    }
    default_y_label = _('None')

    def __init__(self, qs, time_interval):
        self.time_interval = time_interval
        self.x_labels = self.build_x_labels(qs)
        self._x_labels_indexes = {label: i for i, label in enumerate(self.x_labels)}
        self.series = {}
        self.y_labels = []

    def set_y_labels(self, y_labels):
        self.y_labels[:] = y_labels

    def build_x_labels(self, qs):
        if self.time_interval == 'timestamp':
            return list(qs.distinct().values_list(self.time_interval, flat=True))

        aggregate = qs.aggregate(min=Min(self.time_interval), max=Max(self.time_interval))
        if not aggregate['min']:
            return []

        min_date, max_date = aggregate['min'].date(), aggregate['max'].date()
        if self.time_interval == 'day':
            return [min_date + timedelta(days=i) for i in range((max_date - min_date).days + 1)]
        if self.time_interval == 'year':
            return [date(year=i, month=1, day=1) for i in range(min_date.year, max_date.year + 1)]
        if self.time_interval == 'month':
            x_labels = []
            for year in range(min_date.year, max_date.year + 1):
                start_month = 1 if year != min_date.year else min_date.month
                end_month = 12 if year != max_date.year else max_date.month
                for month in range(start_month, end_month + 1):
                    x_labels.append(date(year=year, month=month, day=1))
            return x_labels

    def add(self, x_label, y_label, value):
        if y_label not in self.y_labels:
            self.y_labels.append(y_label)
        serie = self.get_serie(y_label)
        index = self.x_index(x_label)
        serie[index] = (serie[index] or 0) + value

    def get_serie(self, label):
        return self.series.setdefault(label, [None] * len(self.x_labels))

    def x_index(self, x_label):
        return self._x_labels_indexes[x_label]

    def to_json(self, get_y_label=lambda x: x):
        series = []
        if None in self.series:
            series.append({'label': self.default_y_label, 'data': self.series[None]})
        y_labels = [
            (get_y_label(serie_y_label), serie_y_label)
            for serie_y_label in self.y_labels
            if serie_y_label is not None
        ]
        y_labels.sort()
        for y_label, serie_y_label in y_labels:
            series.append({'label': y_label, 'data': self.get_serie(serie_y_label)})
        return {
            'x_labels': [self.format_x_label(label) for label in self.x_labels],
            'series': series,
        }

    def format_x_label(self, label):
        if self.time_interval == 'timestamp':
            return label.isoformat()
        return label.strftime(self.time_label_formats[self.time_interval])
