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

import decimal
import xml.etree.ElementTree as ET

from django.utils.formats import number_format as django_number_format

from wcs.qommon import _, misc
from wcs.qommon.form import CheckboxWidget, NumericWidget
from wcs.qommon.ods import NS as OD_NS
from wcs.qommon.ods import clean_text as od_clean_text

from .base import WidgetField, register_field_class


class NumericField(WidgetField):
    key = 'numeric'
    description = _('Numeric')
    allow_complex = True
    allow_statistics = False
    use_live_server_validation = True
    available_for_filter = True

    widget_class = NumericWidget
    validation = None

    restrict_to_integers = True
    min_value = 0
    max_value = None
    extra_attributes = ['restrict_to_integers', 'min_value', 'max_value']

    def migrate(self):
        changed = super().migrate()
        default_value = getattr(self, 'default_value', None)
        if default_value is not None:  # 2024-12-15
            self.default_value, old_value = misc.parse_decimal(default_value), default_value
            changed |= bool(str(self.default_value) != str(old_value))
        return changed

    def get_admin_attributes(self):
        return super().get_admin_attributes() + ['restrict_to_integers', 'min_value', 'max_value']

    def fill_admin_form(self, form, formdef):
        super().fill_admin_form(form, formdef)
        form.add(
            CheckboxWidget,
            'restrict_to_integers',
            title=_('Restrict to integers'),
            value=self.restrict_to_integers,
        )
        form.add(NumericWidget, 'min_value', title=_('Minimal value'), value=self.min_value)
        form.add(NumericWidget, 'max_value', title=_('Maximal value'), value=self.max_value)

    def perform_more_widget_changes(self, form, kwargs, edit=True):
        pass

    def get_view_value(self, value, **kwargs):
        return django_number_format(value, use_l10n=True) if value is not None else ''

    def get_opendocument_node_value(self, value, formdata=None, **kwargs):
        span = ET.Element('{%s}span' % OD_NS['text'])
        span.text = od_clean_text(self.get_view_value(value))
        return span

    def convert_value_from_anything(self, value):
        return misc.parse_decimal(value, keep_none=True)

    def convert_value_from_str(self, value):
        return misc.parse_decimal(value)

    def convert_value_to_str(self, value):
        if value == '':
            return value
        return django_number_format(value, use_l10n=True)

    def get_json_value(self, value, **kwargs):
        return str(value)

    def from_json_value(self, value):
        try:
            return misc.parse_decimal(value, do_raise=True, keep_none=True)
        except ValueError:
            return None

    def init_with_json(self, elem, include_id=False):
        super().init_with_json(elem, include_id)
        for attribute in ['min_value', 'max_value']:
            value = elem.get(attribute, None)
            if value is not None:
                value = decimal.Decimal(value)
            setattr(self, attribute, value)

    def min_value_init_with_xml(self, el, include_id=False, snapshot=False):
        if el is None or el.text is None:
            self.min_value = None
        else:
            self.min_value = decimal.Decimal(el.text)


register_field_class(NumericField)
