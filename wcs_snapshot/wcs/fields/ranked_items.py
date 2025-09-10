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

import sys

from quixote.html import TemplateIO, htmltext

from wcs.qommon import _
from wcs.qommon.form import CheckboxWidget, RankedItemsWidget, StringWidget, WidgetList

from .base import WidgetField, register_field_class


class RankedItemsField(WidgetField):
    key = 'ranked-items'
    description = _('Ranked Items')
    allow_complex = True

    items = []
    randomize_items = False
    widget_class = RankedItemsWidget
    anonymise = 'no'

    def perform_more_widget_changes(self, form, kwargs, edit=True):
        kwargs['elements'] = self.items or []
        kwargs['randomize_items'] = self.randomize_items

    def fill_admin_form(self, form, formdef):
        super().fill_admin_form(form, formdef)
        try:
            form.remove('prefill')
        except KeyError:  # perhaps it was already removed
            pass
        form.add(
            WidgetList,
            'items',
            title=_('Items'),
            element_type=StringWidget,
            value=self.items,
            required=True,
            element_kwargs={'render_br': False, 'size': 50},
            add_element_label=_('Add item'),
        )
        form.add(CheckboxWidget, 'randomize_items', title=_('Randomize Items'), value=self.randomize_items)

    def get_admin_attributes(self):
        attrs = WidgetField.get_admin_attributes(self) + ['items', 'randomize_items']
        if 'prefill' in attrs:
            attrs.remove('prefill')
        return attrs

    def get_view_value(self, value, **kwargs):
        r = TemplateIO(html=True)
        r += htmltext('<ul>')
        items = list(value.items())
        items.sort(key=lambda x: x[1] or sys.maxsize)
        counter = 0
        last_it = None
        for it in items:
            if it[1] is not None:
                if last_it != it[1]:
                    counter += 1
                    last_it = it[1]
                r += htmltext('<li>%s: %s</li>') % (counter, it[0])
        r += htmltext('</ul>')
        return r.getvalue()

    def get_rst_view_value(self, value, indent=''):
        items = list(value.items())
        items.sort(key=lambda x: x[1] or sys.maxsize)
        counter = 0
        last_it = None
        values = []
        for it in items:
            if it[1] is not None:
                if last_it != it[1]:
                    counter += 1
                    last_it = it[1]
                values.append('%s: %s' % (counter, it[0]))
        return indent + ' / '.join(values)

    def get_csv_heading(self):
        if not self.items:
            return [self.label]
        return [self.label] + [''] * (len(self.items) - 1)

    def get_csv_value(self, element, **kwargs):
        if not self.items:
            return ['']
        if not isinstance(element, dict):
            element = {}
        items = [x for x in element.items() if x[1] is not None]
        items.sort(key=lambda x: x[1])
        ranked = [x[0] for x in items]
        return ranked + ['' for x in range(len(self.items) - len(ranked))]


register_field_class(RankedItemsField)
