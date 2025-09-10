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

from django.utils.encoding import smart_str
from quixote.html import TemplateIO, htmltext

from wcs.qommon import _, misc
from wcs.qommon.form import CheckboxWidget, StringWidget, TableListRowsWidget, WidgetList

from .base import WidgetField, register_field_class


class TableRowsField(WidgetField):
    key = 'tablerows'
    description = _('Table with rows')
    allow_complex = True

    total_row = True
    columns = None

    widget_class = TableListRowsWidget

    def __init__(self, **kwargs):
        self.columns = []
        WidgetField.__init__(self, **kwargs)

    def perform_more_widget_changes(self, form, kwargs, edit=True):
        kwargs['columns'] = self.columns
        kwargs['add_element_label'] = _('Add row')

    def fill_admin_form(self, form, formdef):
        super().fill_admin_form(form, formdef)
        try:
            form.remove('prefill')
        except KeyError:  # perhaps it was already removed
            pass
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
        form.add(
            CheckboxWidget,
            'total_row',
            title=_('Total Row'),
            value=self.total_row,
            default_value=self.__class__.total_row,
        )

    def get_admin_attributes(self):
        t = WidgetField.get_admin_attributes(self) + ['columns', 'total_row']
        try:
            t.remove('prefill')
        except ValueError:
            pass
        return t

    def get_view_value(self, value, **kwargs):
        r = TemplateIO(html=True)
        r += htmltext('<table><thead><tr>')
        for column in self.columns:
            r += htmltext('<th>%s</th>') % column
        r += htmltext('</tr></thead><tbody>')
        for row in value:
            r += htmltext('<tr>')
            for j, column in enumerate(self.columns):
                r += htmltext('<td>')
                if value:
                    try:
                        r += row[j]
                    except IndexError:
                        pass
                r += htmltext('</td>')
            r += htmltext('</tr>')
        r += htmltext('</tbody>')

        if self.total_row:
            sums_row = []
            for j, column in enumerate(self.columns):
                sum_column = 0
                for row_value in value:
                    try:
                        cell_value = row_value[j]
                    except IndexError:
                        continue
                    if cell_value in (None, ''):
                        continue
                    try:
                        sum_column += float(cell_value)
                    except ValueError:
                        sums_row.append(None)
                        break
                else:
                    sums_row.append(sum_column)
            if [x for x in sums_row if x is not None]:
                r += htmltext('<tfoot><tr>')
                for sum_column in sums_row:
                    if sum_column is None:
                        r += htmltext('<td></td>')
                    else:
                        r += htmltext('<td>%.2f</td>' % sum_column)
                r += htmltext('</tr></tfoot>')

        r += htmltext('</table>')

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

        for i, row_value in enumerate(value):
            for j, column in enumerate(self.columns):
                try:
                    max_width = max(max_width, len(smart_str(row_value[j])))
                except IndexError:
                    # ignore errors for shorter than expected rows, this is
                    # typical of the field gaining new columns after some forms
                    # were already saved.
                    pass

        r.append(' '.join(['=' * max_width] * (len(self.columns))))
        r.append(' '.join([smart_str(column).center(max_width) for column in self.columns]))
        r.append(' '.join(['=' * max_width] * (len(self.columns))))
        for i, row_value in enumerate(value):
            r.append(
                ' '.join(
                    [cell.center(max_width) for cell in [get_value(i, x) for x in range(len(self.columns))]]
                )
            )
        r.append(' '.join(['=' * max_width] * (len(self.columns))))
        return misc.site_encode('\n'.join([indent + x for x in r]))

    def get_csv_value(self, element, **kwargs):
        return [_('unimplemented')]  # XXX


register_field_class(TableRowsField)
