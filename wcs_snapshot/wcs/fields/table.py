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

import xml.etree.ElementTree as ET

from django.utils.encoding import smart_str
from quixote.html import TemplateIO, htmltext

from wcs.qommon import _, misc
from wcs.qommon.form import StringWidget, TableWidget, WidgetList
from wcs.qommon.ods import NS as OD_NS

from .base import WidgetField, register_field_class


class TableField(WidgetField):
    key = 'table'
    description = _('Table')
    allow_complex = True

    rows = None
    columns = None

    widget_class = TableWidget

    def __init__(self, **kwargs):
        self.rows = []
        self.columns = []
        WidgetField.__init__(self, **kwargs)

    def perform_more_widget_changes(self, form, kwargs, edit=True):
        kwargs['rows'] = self.rows
        kwargs['columns'] = self.columns

    def fill_admin_form(self, form, formdef):
        super().fill_admin_form(form, formdef)
        try:
            form.remove('prefill')
        except KeyError:  # perhaps it was already removed
            pass
        form.add(
            WidgetList,
            'rows',
            title=_('Rows'),
            element_type=StringWidget,
            value=self.rows,
            required=True,
            element_kwargs={'render_br': False, 'size': 50},
            add_element_label=_('Add row'),
        )
        form.add(
            WidgetList,
            'columns',
            title=_('Columns'),
            element_type=StringWidget,
            value=self.columns,
            required=True,
            element_kwargs={'render_br': False, 'size': 50},
            add_element_label=_('Add column'),
        )

    def get_admin_attributes(self):
        t = WidgetField.get_admin_attributes(self) + ['rows', 'columns']
        try:
            t.remove('prefill')
        except ValueError:
            pass
        return t

    def get_view_value(self, value, **kwargs):
        r = TemplateIO(html=True)
        r += htmltext('<table><thead><tr><td></td>')
        for column in self.columns:
            r += htmltext('<th>%s</th>') % column
        r += htmltext('</tr></thead><tbody>')
        for i, row in enumerate(self.rows):
            r += htmltext('<tr><th>%s</th>') % row
            for j, column in enumerate(self.columns):
                r += htmltext('<td>')
                if value:
                    try:
                        r += value[i][j]
                    except IndexError:
                        pass
                r += htmltext('</td>')
            r += htmltext('</tr>')
        r += htmltext('</tbody></table>')

        return r.getvalue()

    def get_rst_view_value(self, value, indent=''):
        if not value:
            return indent
        r = []
        max_width = 0
        for column in self.columns:
            max_width = max(max_width, len(smart_str(column)))

        for i, row in enumerate(value):
            value[i] = [x or '' for x in row]

        def get_value(i, j):
            try:
                return smart_str(value[i][j])
            except IndexError:
                return '-'

        for i, row in enumerate(self.rows):
            max_width = max(max_width, len(row))
            for j, column in enumerate(self.columns):
                max_width = max(max_width, len(get_value(i, j)))

        r.append(' '.join(['=' * max_width] * (len(self.columns) + 1)))
        r.append(' '.join([smart_str(column).center(max_width) for column in ['/'] + self.columns]))
        r.append(' '.join(['=' * max_width] * (len(self.columns) + 1)))
        for i, row in enumerate(self.rows):
            r.append(
                ' '.join(
                    [
                        cell.center(max_width)
                        for cell in [smart_str(row)] + [get_value(i, x) for x in range(len(self.columns))]
                    ]
                )
            )
        r.append(' '.join(['=' * max_width] * (len(self.columns) + 1)))
        return misc.site_encode('\n'.join([indent + x for x in r]))

    def get_csv_heading(self):
        if not self.columns:
            return [self.label]
        labels = []
        for col in self.columns:
            for row in self.rows:
                t = '%s / %s' % (col, row)
                if len(labels) == 0:
                    labels.append('%s - %s' % (self.label, t))
                else:
                    labels.append(t)
        return labels

    def get_csv_value(self, element, **kwargs):
        if not self.columns:
            return ['']
        values = []
        for i in range(len(self.columns)):
            for j in range(len(self.rows)):
                try:
                    values.append(element[j][i])
                except IndexError:
                    values.append('')
        return values

    def get_opendocument_node_value(self, value, formdata=None, **kwargs):
        table = ET.Element('{%s}table' % OD_NS['table'])
        ET.SubElement(table, '{%s}table-column' % OD_NS['table'])
        for col in self.columns:
            ET.SubElement(table, '{%s}table-column' % OD_NS['table'])
        row = ET.SubElement(table, '{%s}table-row' % OD_NS['table'])
        ET.SubElement(row, '{%s}table-cell' % OD_NS['table'])
        for col in self.columns:
            table_cell = ET.SubElement(row, '{%s}table-cell' % OD_NS['table'])
            cell_value = ET.SubElement(table_cell, '{%s}p' % OD_NS['text'])
            cell_value.text = col
        for i, row_label in enumerate(self.rows):
            row = ET.SubElement(table, '{%s}table-row' % OD_NS['table'])
            table_cell = ET.SubElement(row, '{%s}table-cell' % OD_NS['table'])
            cell_value = ET.SubElement(table_cell, '{%s}p' % OD_NS['text'])
            cell_value.text = row_label
            for j, col in enumerate(self.columns):
                table_cell = ET.SubElement(row, '{%s}table-cell' % OD_NS['table'])
                cell_value = ET.SubElement(table_cell, '{%s}p' % OD_NS['text'])
                try:
                    cell_value.text = value[i][j]
                except IndexError:
                    pass
        return table

    def from_json_value(self, value):
        return value


register_field_class(TableField)
