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

from quixote import get_publisher, get_request
from quixote.html import TemplateIO, htmltext

from wcs import data_sources
from wcs.qommon import _
from wcs.qommon.form import (
    CheckboxesWidget,
    CheckboxesWithImagesWidget,
    CheckboxWidget,
    IntWidget,
    MultiSelectWidget,
    RadiobuttonsWidget,
)
from wcs.qommon.misc import simplify
from wcs.qommon.ods import NS as OD_NS
from wcs.qommon.ods import clean_text as od_clean_text

from .base import SetValueError, WidgetField, register_field_class
from .item import ItemFieldMixin, ItemWithImageFieldMixin, item_items_stats


class ItemsField(WidgetField, ItemFieldMixin, ItemWithImageFieldMixin):
    key = 'items'
    description = _('Multiple choice list')
    allow_complex = True
    allow_statistics = True
    available_for_filter = True
    use_live_server_validation = True

    items = []
    min_choices = 0
    max_choices = 0
    data_source = {}
    in_filters = False
    display_disabled_items = False
    display_mode = 'checkboxes'

    widget_class = CheckboxesWidget

    def __init__(self, **kwargs):
        self.items = []
        WidgetField.__init__(self, **kwargs)

    def get_options(self):
        if self.data_source:
            options = [x[:3] for x in data_sources.get_items(self.data_source)]
            return options
        if self.items:
            return [(x, get_publisher().translate(x), simplify(x)) for x in self.items]
        return []

    def perform_more_widget_changes(self, form, kwargs, edit=True):
        data_source = data_sources.get_object(self.data_source)
        kwargs['min_choices'] = self.min_choices
        kwargs['max_choices'] = self.max_choices
        if self.data_source:
            if self.display_mode == 'images' and data_source.can_images():
                items = data_sources.get_carddef_items(self.data_source)
                self.widget_class = CheckboxesWithImagesWidget
            else:
                items = data_sources.get_items(self.data_source, include_disabled=self.display_disabled_items)
            kwargs['options'] = [x[:3] for x in items if not x[-1].get('disabled')]
            kwargs['options_with_attributes'] = items[:]
        else:
            kwargs['options'] = self.get_options()

        if len(kwargs['options']) > 3:
            kwargs['inline'] = False

        if self.display_mode == 'autocomplete':
            self.widget_class = MultiSelectWidget

    def fill_admin_form(self, form, formdef):
        super().fill_admin_form(form, formdef)
        form.add(
            CheckboxWidget,
            'in_filters',
            title=_('Display in default filters'),
            value=self.in_filters,
            advanced=True,
        )
        options = [
            ('checkboxes', _('Checkboxes'), 'checkboxes'),
            ('autocomplete', _('Autocomplete'), 'autocomplete'),
            ('images', _('Images'), 'images'),
        ]
        form.add(
            RadiobuttonsWidget,
            'display_mode',
            title=_('Display Mode'),
            options=options,
            value=self.display_mode,
            attrs={'data-dynamic-display-parent': 'true'},
            extra_css_class='widget-inline-radio',
        )
        self.add_items_fields_admin_form(form)
        self.fill_image_options_admin_form(
            form,
            attrs={
                'data-dynamic-display-child-of': 'display_mode',
                'data-dynamic-display-value': 'images',
            },
        )
        form.add(
            IntWidget,
            'min_choices',
            title=_('Minimum number of choices'),
            value=self.min_choices,
            required=False,
            size=4,
        )
        form.add(
            IntWidget,
            'max_choices',
            title=_('Maximum number of choices'),
            value=self.max_choices,
            required=False,
            size=4,
        )
        form.add(
            CheckboxWidget,
            'display_disabled_items',
            title=_('Display disabled items'),
            value=self.display_disabled_items,
            advanced=True,
        )

    def get_admin_attributes(self):
        return WidgetField.get_admin_attributes(self) + [
            'items',
            'display_mode',
            'min_choices',
            'max_choices',
            'data_source',
            'in_filters',
            'display_disabled_items',
            'image_desktop_size',
            'image_mobile_size',
        ]

    def check_admin_form(self, form):
        super().check_admin_form(form)
        self.check_items_admin_form(form)
        self.check_display_mode(form)

    def get_prefill_value(self, user=None, force_string=True):
        value, explicit_lock = super().get_prefill_value(user=user, force_string=False)
        if value is not None and (
            not isinstance(value, (str, tuple, list)) or not all(isinstance(x, (int, str)) for x in value)
        ):
            get_publisher().record_error(
                _('Invalid value for items prefill on field "%s"') % self.label,
                formdef=getattr(self, 'formdef', None),
            )
            return (None, explicit_lock)
        return (value, explicit_lock)

    def convert_value_to_str(self, value):
        if value is not None:
            return '|'.join(value)

    def convert_value_from_str(self, value):
        if not isinstance(value, str):
            return value
        if not value.strip():
            return None
        return [x.strip() for x in value.split('|') if x.strip()]

    def convert_value_from_anything(self, value):
        if isinstance(value, str):
            return self.convert_value_from_str(value)
        if isinstance(value, int):
            return [str(value)]
        if not value:
            return None
        try:
            value = list(value)
        except TypeError:
            raise ValueError('invalid data for items type (%r)' % value)
        if any(not isinstance(x, str) for x in value):
            raise ValueError('invalid data for items type (%r)' % value)
        return value

    def get_value_info(self, data, wf_form=False):
        value, value_details = super().get_value_info(data)
        labels = []
        if not self.data_source:
            value_id = value_details.get('value_id')
            if value_id:
                labels = value_id.copy()
        else:
            structured_values = self.get_structured_value(data)
            if structured_values:
                labels = [x['text'] for x in structured_values]
        value_details['labels'] = labels
        return (value, value_details)

    def get_view_value(self, value, **kwargs):
        # if it is a carddef datasource and display links to cards in backoffice
        if (
            self.data_source
            and self.data_source.get('type', '').startswith('carddef:')
            and kwargs.get('value_id')
        ):
            carddef = self.get_carddef()
            if not carddef:
                return ''
            r = TemplateIO(html=True)
            for value_id, value_label in zip(kwargs['value_id'], kwargs['labels']):
                try:
                    carddata = carddef.data_class().get_by_id(value_id)
                    value = (carddata.digests or {}).get('default') or value_label
                except KeyError:
                    carddata = None
                    value = value_label
                if (
                    get_request()
                    and get_request().is_in_backoffice()
                    and carddata
                    and carddef.is_user_allowed_read(get_request().user, carddata)
                ):
                    value = htmltext('<a href="%s">%s</a>') % (carddata.get_url(backoffice=True), value)
                r += htmltext('<div>%s</div>') % value
            return r.getvalue()

        if kwargs.get('labels'):
            # summary page and labels are available
            r = TemplateIO(html=True)
            r += htmltext('<ul>')
            for x in kwargs['labels']:
                r += htmltext('<li>%s</li>' % x)
            r += htmltext('</ul>')
            return r.getvalue()

        if isinstance(value, str):  # == display_value
            return value
        if value:
            try:
                return ', '.join([(x) for x in value])
            except TypeError:
                pass
        return ''

    def get_opendocument_node_value(self, value, formdata=None, **kwargs):
        span = ET.Element('{%s}span' % OD_NS['text'])
        span.text = od_clean_text(self.get_view_value(value))
        return span

    def stats(self, values):
        return item_items_stats(self, values)

    def get_csv_heading(self):
        label = str(self.label or '-')
        quals = ['']
        if self.data_source:
            quals = ['(%s)' % _('identifier'), '(%s)' % _('label')]
        nb_columns = 1
        if self.max_choices:
            nb_columns = self.max_choices
        elif len(self.get_options()):
            nb_columns = len(self.get_options())

        labels = []
        for i in range(nb_columns):
            for q in quals:
                labels.append(' '.join((label, str(i + 1), q)).strip())
        return labels

    def get_csv_value(self, element, structured_value=None, **kwargs):
        values = []

        if self.max_choices:
            nb_columns = self.max_choices
        elif len(self.get_options()):
            nb_columns = len(self.get_options())
        else:
            nb_columns = 1

        if self.data_source:
            nb_columns *= 2
            for one_value in structured_value or []:
                values.append(one_value.get('id'))
                values.append(one_value.get('text'))
        else:
            for one_value in element:
                values.append(one_value)

        if len(values) > nb_columns:
            # this would happen if max_choices is set after forms were already
            # filled with more values
            values = values[:nb_columns]
        elif len(values) < nb_columns:
            values.extend([''] * (nb_columns - len(values)))

        return values

    def store_display_value(self, data, field_id, raise_on_error=False):
        if not data.get(field_id):
            return ''
        if not self.data_source or not self.data_source.get('type', '').startswith('carddef:'):
            options = self.get_options()
            if not options:
                return ''
            choices = []
            for choice in data.get(field_id) or []:
                for key, option_value, dummy in options:
                    if str(key) == str(choice):
                        choices.append(option_value)
        else:
            selected_choices = self.store_structured_value(data, field_id, raise_on_error=raise_on_error)
            choices = [x.get('text') for x in selected_choices]
        return ', '.join(choices)

    def store_structured_value(self, data, field_id, raise_on_error=False):
        if not data.get(field_id):
            return
        if not self.data_source:
            return
        selected_options = data.get(field_id) or []
        try:
            return data_sources.get_structured_items(
                self.data_source, raise_on_error=raise_on_error, selected_ids=selected_options
            )
        except data_sources.DataSourceError as e:
            raise SetValueError(str(e))

    def export_to_json(self, include_id=False):
        field = super().export_to_json(include_id=include_id)
        if self.data_source:
            self.export_to_json_data_source(field)
        return field

    def from_json_value(self, value):
        if isinstance(value, list):
            return value
        return []

    def get_exploded_options(self, options):
        carddef = self.get_carddef()
        if not carddef:
            # unnest key/values
            exploded_options = {}
            for option_keys, option_label in options:
                if option_keys and option_label:
                    for option_key, option_label in zip(option_keys, option_label.split(', ')):
                        exploded_options[option_key] = option_label
            return exploded_options.items()

        options_ids = set()
        for option in options:
            if option[0]:
                options_ids.update(set(option[0]))

        return self.get_carddef_options_by_ids(carddef, options_ids)

    def i18n_scan(self, base_location):
        yield from super(WidgetField, self).i18n_scan(base_location)
        yield from ItemFieldMixin.i18n_scan(self, base_location)


register_field_class(ItemsField)
