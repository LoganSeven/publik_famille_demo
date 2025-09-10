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

from wcs.qommon import _
from wcs.qommon.form import SingleSelectTableWidget, StringWidget, TableWidget, WidgetList

from .base import register_field_class
from .table import TableField


class TableSelectField(TableField):
    key = 'table-select'
    description = _('Table of Lists')
    allow_complex = True

    items = None

    widget_class = SingleSelectTableWidget

    def __init__(self, **kwargs):
        self.items = []
        TableField.__init__(self, **kwargs)

    def fill_admin_form(self, form, formdef):
        super().fill_admin_form(form, formdef)
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

    def perform_more_widget_changes(self, form, kwargs, edit=True):
        TableField.perform_more_widget_changes(self, form, kwargs, edit=edit)
        if edit:
            kwargs['options'] = self.items or [(None, '---')]
        else:
            self.widget_class = TableWidget

    def get_admin_attributes(self):
        return TableField.get_admin_attributes(self) + ['items']

    def check_admin_form(self, form):
        items = form.get_widget('items').parse()
        d = {}
        for v in items or []:
            if v in d:
                form.set_error('items', _('Duplicated Items'))
                return
            d[v] = None


register_field_class(TableSelectField)
