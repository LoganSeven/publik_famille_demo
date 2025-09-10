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

import datetime
import time
import xml.etree.ElementTree as ET

from django.utils.encoding import force_str
from django.utils.html import urlize
from quixote.html import htmltext

from wcs import data_sources
from wcs.qommon import _
from wcs.qommon.form import (
    AutocompleteStringWidget,
    HiddenWidget,
    StringWidget,
    ValidationWidget,
    WcsExtraStringWidget,
)
from wcs.qommon.misc import date_format, is_ascii_digit, strftime
from wcs.qommon.ods import NS as OD_NS
from wcs.qommon.ods import clean_text as od_clean_text

from .base import WidgetField, register_field_class


class StringField(WidgetField):
    key = 'string'
    description = _('Text (line)')
    available_for_filter = True

    widget_class = WcsExtraStringWidget
    size = None
    maxlength = None
    extra_attributes = ['size', 'maxlength']
    validation = {}
    data_source = {}
    keep_raw_value = False

    def perform_more_widget_changes(self, form, kwargs, edit=True):
        if self.data_source:
            data_source = data_sources.get_object(self.data_source)
            if data_source.can_jsonp():
                kwargs['url'] = data_source.get_jsonp_url()
                self.widget_class = AutocompleteStringWidget

    @property
    def use_live_server_validation(self):
        if self.validation and self.validation['type']:
            return bool(ValidationWidget.validation_methods.get(self.validation['type']))
        return False

    def fill_admin_form(self, form, formdef):
        super().fill_admin_form(form, formdef)
        if self.size:
            form.add(
                StringWidget,
                'size',
                title=_('Line length'),
                hint=_(
                    'Deprecated option, it is advised to use CSS classes '
                    'to size the fields in a manner compatible with all devices.'
                ),
                value=self.size,
            )
        else:
            form.add(HiddenWidget, 'size', value=None)
        form.add(
            ValidationWidget,
            'validation',
            title=_('Validation'),
            value=self.validation,
            advanced=True,
        )

        def validate_maxlength(value):
            if value and not is_ascii_digit(value):
                raise ValueError(_('The maximum number of characters must be empty or a number.'))

        form.add(
            StringWidget,
            'maxlength',
            title=_('Maximum number of characters'),
            value=self.maxlength,
            advanced=True,
            validation_function=validate_maxlength,
        )
        form.add(
            data_sources.DataSourceSelectionWidget,
            'data_source',
            value=self.data_source,
            title=_('Data Source'),
            hint=_('This will allow autocompletion from an external source.'),
            disallowed_source_types={'geojson'},
            advanced=True,
            required=False,
        )

    def get_admin_attributes(self):
        return WidgetField.get_admin_attributes(self) + [
            'size',
            'validation',
            'data_source',
            'maxlength',
        ]

    def get_view_value(self, value, **kwargs):
        value = value or ''
        if isinstance(value, str) and value.startswith(('http://', 'https://')):
            return htmltext(force_str(urlize(value, nofollow=True, autoescape=True)))
        return str(value)

    def get_opendocument_node_value(self, value, formdata=None, **kwargs):
        if value.startswith(('http://', 'https://')):
            node = ET.Element('{%s}a' % OD_NS['text'])
            node.attrib['{%s}href' % OD_NS['xlink']] = value
        else:
            node = ET.Element('{%s}span' % OD_NS['text'])
        node.text = od_clean_text(force_str(value))
        return node

    def get_rst_view_value(self, value, indent=''):
        return indent + str(value or '')

    def convert_value_from_str(self, value):
        return value

    def convert_value_to_str(self, value):
        if value is None:
            return None
        if isinstance(value, (time.struct_time, datetime.date)):
            return strftime(date_format(), value)
        return str(value)

    @classmethod
    def convert_value_from_anything(cls, value):
        if value is None:
            return None
        return str(value)

    def get_fts_value(self, data, **kwargs):
        value = super().get_fts_value(data, **kwargs)
        if value and self.validation and self.validation['type']:
            validation_method = ValidationWidget.validation_methods.get(self.validation['type'])
            if validation_method and validation_method.get('normalize_for_fts'):
                # index both original and normalized value
                # in the case of phone numbers this makes sure the "international/E164"
                # format (ex: +33199001234) is indexed.
                value = '%s %s' % (value, validation_method.get('normalize_for_fts')(value))
        return value

    def migrate(self):
        changed = super().migrate()
        if isinstance(self.validation, str):  # 2019-08-10
            self.validation = {'type': 'regex', 'value': self.validation}
            changed = True
        return changed

    def init_with_xml(self, elem, include_id=False, snapshot=False):
        super().init_with_xml(elem, include_id=include_id)
        self.migrate()

    def get_validation_parameter_view_value(self, widget):
        if not self.validation:
            return
        validation_type = self.validation['type']
        validation_types = {x: y['title'] for x, y in ValidationWidget.validation_methods.items()}
        if validation_type in ('regex', 'django'):
            validation_value = self.validation.get('value')
            if not validation_value:
                return
            return '%s - %s' % (validation_types.get(validation_type), validation_value)
        return str(validation_types.get(validation_type))

    def i18n_scan(self, base_location):
        location = '%s%s/' % (base_location, self.id)
        yield from super().i18n_scan(base_location)
        if self.validation and self.validation.get('error_message'):
            yield location, None, self.validation.get('error_message')


register_field_class(StringField)
