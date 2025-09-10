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

from wcs import data_sources
from wcs.qommon import _
from wcs.qommon.form import CheckboxWidget, ComputedExpressionWidget, StringWidget, TextWidget, VarnameWidget
from wcs.qommon.misc import get_dependencies_from_template

from .base import Field, register_field_class


class ComputedField(Field):
    key = 'computed'
    description = _('Computed Data')

    value_template = None
    freeze_on_initial_value = False
    data_source = {}

    add_to_form = None
    add_to_view_form = None
    get_opendocument_node_value = None

    TEXT_ATTRIBUTES = Field.TEXT_ATTRIBUTES + ['value_template']

    def get_admin_attributes(self):
        attributes = super().get_admin_attributes()
        attributes.remove('condition')
        return attributes + ['varname', 'value_template', 'freeze_on_initial_value', 'data_source']

    def fill_admin_form(self, form, formdef):
        form.add(StringWidget, 'label', title=_('Label'), value=self.label, required=True, size=50)
        form.add(
            VarnameWidget,
            'varname',
            title=_('Identifier'),
            required=True,
            value=self.varname,
            size=30,
            hint=_('This is used as suffix for variable names.'),
        )
        value_widget = StringWidget
        value_widget_kwargs = {'size': 150}
        if len(str(self.value_template or '')) > value_widget_kwargs['size']:
            value_widget = TextWidget
            value_widget_kwargs = {'cols': 150, 'rows': len(self.value_template) // 150 + 1}
        form.add(
            value_widget,
            'value_template',
            title=_('Value'),
            required=True,
            value=self.value_template,
            validation_function=ComputedExpressionWidget.validate_template,
            hint=_('As a Django template'),
            **value_widget_kwargs,
        )
        form.add(
            CheckboxWidget,
            'freeze_on_initial_value',
            title=_('Freeze on initial value'),
            value=self.freeze_on_initial_value,
        )
        form.add(
            data_sources.DataSourceSelectionWidget,
            'data_source',
            value=self.data_source,
            allowed_source_types={'cards'},
            title=_('Data Source (cards only)'),
            hint=_('This will make linked card data available for expressions.'),
            required=False,
        )

    def get_real_data_source(self):
        return data_sources.get_real(self.data_source)

    def get_dependencies(self):
        yield from super().get_dependencies()
        yield from get_dependencies_from_template(self.value_template)


register_field_class(ComputedField)
