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

from django.utils.encoding import force_str
from quixote import get_publisher, get_request, get_session
from quixote.html import TemplateIO, htmltext

from wcs import data_sources
from wcs.qommon import _, misc
from wcs.qommon.form import (
    CheckboxWidget,
    ComputedExpressionWidget,
    HiddenWidget,
    JsonpSingleSelectWidget,
    MapMarkerSelectionWidget,
    RadiobuttonsWidget,
    RadiobuttonsWithImagesWidget,
    SingleSelectHintWidget,
    SingleSelectWidget,
    StringWidget,
    WidgetList,
)
from wcs.qommon.ods import NS as OD_NS
from wcs.qommon.ods import clean_text as od_clean_text
from wcs.qommon.template import TemplateError
from wcs.sessions import BasicSession
from wcs.sql_criterias import Contains

from .base import SetValueError, WidgetField, register_field_class
from .map import MapOptionsMixin


class UnknownCardValueError(ValueError):
    def __init__(self, value):
        super().__init__('unknown card value (%r)' % value)
        self.value = value

    def get_error_summary(self):
        return _('unknown card value (%r)') % self.value


class ItemWithImageFieldMixin:
    # images options
    image_desktop_size = 150
    image_mobile_size = 75

    def fill_image_options_admin_form(self, form, **kwargs):
        def validate_image_size(value):
            if value.isnumeric():
                return
            if 'x' in value:
                width, height = value.split('x')
                if not (width.isnumeric() and height.isnumeric()):
                    raise ValueError(_('Wrong format'))
            else:
                raise ValueError(_('Wrong format'))

        form.add(
            StringWidget,
            'image_desktop_size',
            title=_('Image size on desktop'),
            value=self.image_desktop_size,
            validation_function=validate_image_size,
            hint=_('In pixels.'),
            **kwargs,
        )

        form.add(
            StringWidget,
            'image_mobile_size',
            title=_('Image size on mobile'),
            value=self.image_mobile_size,
            validation_function=validate_image_size,
            hint=_('In pixels.'),
            **kwargs,
        )


class ItemFieldMixin:
    def get_real_data_source(self):
        return data_sources.get_real(self.data_source)

    def add_items_fields_admin_form(self, form):
        real_data_source = self.get_real_data_source()
        form.add(
            RadiobuttonsWidget,
            'data_mode',
            title=_('Data'),
            options=[
                ('simple-list', _('Simple List'), 'simple-list'),
                ('data-source', _('Data Source'), 'data-source'),
            ],
            value='data-source' if real_data_source else 'simple-list',
            attrs={'data-dynamic-display-parent': 'true'},
            extra_css_class='widget-inline-radio no-bottom-margin',
        )
        form.add(
            WidgetList,
            'items',
            element_type=StringWidget,
            value=self.items,
            required=False,
            element_kwargs={'render_br': False, 'size': 50},
            add_element_label=_('Add item'),
            attrs={'data-dynamic-display-child-of': 'data_mode', 'data-dynamic-display-value': 'simple-list'},
            extra_css_class='sortable',
        )
        form.add(
            data_sources.DataSourceSelectionWidget,
            'data_source',
            value=self.data_source,
            required=False,
            hint=_('This will get the available items from an external source.'),
            disallowed_source_types={'geojson'},
            attrs={'data-dynamic-display-child-of': 'data_mode', 'data-dynamic-display-value': 'data-source'},
        )

    def check_items_admin_form(self, form):
        data_mode = form.get_widget('data_mode').parse()
        if data_mode == 'simple-list':
            items = form.get_widget('items').parse()
            d = {}
            for v in items or []:
                if v in d:
                    form.set_error('items', _('Duplicated Items'))
                    return
                d[v] = None

            data_source_type = form.get_widget('data_source').get_widget('type')
            data_source_type.set_value(None)
            data_source_type.transfer_form_value(get_request())

    def check_display_mode(self, form):
        data_mode = form.get_widget('data_mode').parse()
        display_mode = form.get_widget('display_mode').parse()
        ds = data_sources.NamedDataSource()
        ds.data_source = data_sources.get_real(form.get_widget('data_source').parse())
        if display_mode == 'images' and (data_mode == 'simple-list' or not ds.can_images()):
            form.get_widget('display_mode').set_error(
                _('Image display is only possible with cards with image fields.')
            )
        elif display_mode == 'map' and (data_mode == 'simple-list' or not ds.can_geojson()):
            form.get_widget('display_mode').set_error(_('Map display is only possible with GeoJSON sources.'))
        elif display_mode == 'timetable' and (data_mode == 'simple-list' or not ds.maybe_datetimes()):
            form.get_widget('display_mode').set_error(
                _('Time table display is only possible with sources with date and times.')
            )

    def get_items_parameter_view_label(self):
        if self.data_source:
            # skip field if there's a data source
            return None
        return str(_('Choices'))

    def get_data_source_parameter_view_label(self):
        return str(_('Data source'))

    def get_carddef(self):
        from wcs.carddef import CardDef

        if (
            not self.data_source
            or not self.data_source.get('type')
            or not self.data_source['type'].startswith('carddef:')
        ):
            return None

        carddef_slug = self.data_source['type'].split(':')[1]
        try:
            return CardDef.get_by_urlname(carddef_slug)
        except KeyError:
            return None

    def get_extended_options(self):
        if self.data_source:
            return data_sources.get_structured_items(
                self.data_source,
                mode='lazy',
                include_disabled=self.display_disabled_items,
                with_file_urls=True,
            )
        if self.items:
            return [{'id': x, 'text': x} for x in self.items]
        return []

    def export_to_json_data_source(self, field):
        if 'items' in field:
            del field['items']
        data_source_type = self.data_source.get('type')
        if data_source_type and data_source_type.startswith('carddef:'):
            carddef_slug = data_source_type.split(':')[1]
            url = None
            if data_source_type.count(':') == 1:
                url = '/api/cards/%s/list' % carddef_slug
            else:
                custom_view_slug = data_source_type.split(':')[2]
                if not custom_view_slug.startswith('_'):
                    url = '/api/cards/%s/%s/list' % (carddef_slug, custom_view_slug)
            if url:
                field['items_url'] = get_request().build_absolute_uri(url)
        return field

    def i18n_scan(self, base_location):
        location = '%s%s/' % (base_location, self.id)
        real_data_source = self.get_real_data_source()
        if not real_data_source:
            for item in self.items or []:
                yield location, None, item

    def get_display_value(self, value):
        data_source = data_sources.get_object(self.data_source)
        if data_source is None:
            return get_publisher().translate(value) or ''

        if data_source.type == 'jsonp':
            if not get_session():
                return value
            return get_session().get_jsonp_display_value('%s_%s' % (data_source.get_jsonp_url(), value))

        display_value = data_source.get_display_value(value)
        session = get_session()
        if (
            isinstance(session, BasicSession)
            and self.display_mode == 'autocomplete'
            and data_source
            and data_source.can_jsonp()
        ):
            carddef = self.get_carddef()
            url_kwargs = {}
            if self.key == 'item' and get_request().is_in_backoffice() and carddef:
                url_kwargs['with_related'] = True
            # store display value in session to be used by select2
            url = data_source.get_jsonp_url(**url_kwargs)
            session.set_jsonp_display_value('%s_%s' % (url, value), display_value)

        return display_value

    def get_carddef_options_by_ids(self, carddef, options_ids):
        if carddef.id_template:
            cards = carddef.data_class().select([Contains('id_display', options_ids)])
            return [(str(x.id_display), x.get_display_label()) for x in cards]

        options_ids = [x for x in options_ids if misc.is_ascii_digit(str(x))]
        cards = carddef.data_class().select([Contains('id', options_ids)])
        return [(str(x.id), x.get_display_label()) for x in cards]


class ItemField(WidgetField, MapOptionsMixin, ItemFieldMixin, ItemWithImageFieldMixin):
    key = 'item'
    description = _('List')
    allow_complex = True
    allow_statistics = True
    available_for_filter = True

    items = []
    show_as_radio = None
    anonymise = 'no'
    widget_class = SingleSelectHintWidget
    data_source = {}
    in_filters = False
    display_disabled_items = False
    display_mode = 'list'

    # <select> option
    use_hint_as_first_option = True

    # radio option
    radio_orientation = 'auto'  # auto/horizontal/vertical

    # timetable option
    initial_date_alignment = None

    # map options
    initial_position = None
    default_position = None
    position_template = None

    def __init__(self, **kwargs):
        self.items = []
        WidgetField.__init__(self, **kwargs)

    def migrate(self):
        changed = super().migrate()
        if isinstance(getattr(self, 'show_as_radio', None), bool):  # 2019-03-19
            if self.show_as_radio:
                self.display_mode = 'radio'
            else:
                self.display_mode = 'list'
            self.show_as_radio = None
            changed = True
        if self.extra_css_class and 'widget-inline-radio' in self.extra_css_class.split():
            self.extra_css_class = ' '.join(
                [x for x in self.extra_css_class.split() if x != 'widget-inline-radio']
            )
            self.radio_orientation = 'horizontal'
            changed = True
        return changed

    def init_with_xml(self, elem, include_id=False, snapshot=False):
        super().init_with_xml(elem, include_id=include_id)
        if getattr(elem.find('show_as_radio'), 'text', None) == 'True':
            self.display_mode = 'radio'

    @property
    def extra_attributes(self):
        if self.display_mode == 'map':
            return [
                'initial_zoom',
                'min_zoom',
                'max_zoom',
                'initial_position',
                'position_template',
                'data_source',
            ]
        return []

    def get_options(self, mode=None):
        if self.data_source:
            return [
                x[:3]
                for x in data_sources.get_items(
                    self.data_source, mode=mode, include_disabled=self.display_disabled_items
                )
            ]
        if self.items:
            return [(x, get_publisher().translate(x), x) for x in self.items]
        return []

    def get_id_by_option_text(self, text_value):
        if self.data_source:
            return data_sources.get_id_by_option_text(self.data_source, text_value)
        return text_value

    def get_prefill_value(self, user=None, force_string=True):
        value, explicit_lock = super().get_prefill_value(user=user, force_string=False)
        if value and self.data_source:
            data_source = data_sources.get_object(self.data_source)
            struct_value = data_source.get_structured_value(value, check_value_type=True)
            if struct_value:
                value = struct_value.get('id')
        if force_string and value is not None and not isinstance(value, str):
            value = str(value)
        return (value, explicit_lock)

    def get_display_mode(self, data_source=None):
        if not data_source:
            data_source = data_sources.get_object(self.data_source)

        if data_source and data_source.type == 'jsonp':
            # a source defined as JSONP can only be used in autocomplete mode
            return 'autocomplete'

        return self.display_mode

    def perform_more_widget_changes(self, form, kwargs, edit=True):
        data_source = data_sources.get_object(self.data_source)
        display_mode = self.get_display_mode(data_source)

        if display_mode == 'autocomplete' and data_source and data_source.can_jsonp():
            carddef = self.get_carddef()
            url_kwargs = {}
            if get_request().is_in_backoffice() and carddef:
                if (
                    carddef.has_creation_permission(get_request().user)
                    and carddef.submission_user_association != 'any-required'
                ):
                    kwargs['add_related_url'] = carddef.get_backoffice_submission_url()
                kwargs['with_related'] = True
                url_kwargs['with_related'] = True
            self.url = kwargs['url'] = data_source.get_jsonp_url(**url_kwargs)
            kwargs['use_hint_as_first_option'] = self.use_hint_as_first_option
            self.widget_class = JsonpSingleSelectWidget
            return

        if self.display_mode != 'map':
            if self.data_source:
                if display_mode == 'images' and data_source.can_images():
                    self.widget_class = RadiobuttonsWithImagesWidget
                    items = data_sources.get_carddef_items(
                        self.data_source,
                    )
                else:
                    items = data_sources.get_items(
                        self.data_source, include_disabled=self.display_disabled_items
                    )
                kwargs['options'] = [x[:3] for x in items if not x[-1].get('disabled')]
                kwargs['options_with_attributes'] = items[:]
            else:
                kwargs['options'] = self.get_options()
            if not kwargs.get('options'):
                kwargs['options'] = [(None, '---', None)]
        if display_mode == 'list':
            kwargs['use_hint_as_first_option'] = self.use_hint_as_first_option
        elif display_mode == 'radio':
            self.widget_class = RadiobuttonsWidget
            first_items = [x[1] for x in kwargs['options'][:6]]
            length_first_items = sum(len(x) for x in first_items)
            if self.radio_orientation == 'auto':
                # display radio buttons on a single line if there's just a few
                # short options.
                self.inline = bool(len(kwargs['options']) <= 6 and length_first_items <= 40)
            elif self.radio_orientation == 'horizontal':
                self.inline = True
            elif self.radio_orientation == 'vertical':
                self.inline = False
        elif display_mode == 'autocomplete':
            kwargs['use_hint_as_first_option'] = self.use_hint_as_first_option
            kwargs['select2'] = True
        elif display_mode == 'map':
            self.widget_class = MapMarkerSelectionWidget
        elif display_mode == 'timetable':
            # SingleSelectHintWidget with custom template
            kwargs['template-name'] = 'qommon/forms/widgets/select-timetable.html'

    def get_view_value(self, value, value_id=None, **kwargs):
        data_source = data_sources.get_object(self.data_source)
        if value and data_source is None:
            return get_publisher().translate(value) or ''
        value = super().get_view_value(value)
        if not (value_id and self.data_source and self.data_source.get('type', '').startswith('carddef:')):
            return value
        carddef = self.get_carddef()
        if not carddef:
            return value
        try:
            carddata = carddef.data_class().get_by_id(value_id)
        except KeyError:
            return value
        parts = data_source.data_source['type'].split(':')
        digest_key = 'default'
        value = (carddata.digests or {}).get(digest_key) or value
        if len(parts) == 3:
            digest_key = 'custom-view:%s' % parts[-1]
            value = (carddata.digests or {}).get(digest_key) or value
        if get_publisher().has_i18n_enabled():
            digest_key += ':' + get_publisher().current_language
            value = (carddata.digests or {}).get(digest_key) or value
        if not (
            get_request()
            and get_request().is_in_backoffice()
            and carddef.is_user_allowed_read(get_request().user, carddata)
        ):
            return value
        return htmltext('<a href="%s">' % carddata.get_url(backoffice=True)) + htmltext('%s</a>') % value

    def get_opendocument_node_value(self, value, formdata=None, **kwargs):
        span = ET.Element('{%s}span' % OD_NS['text'])
        span.text = od_clean_text(force_str(value))
        return span

    def add_to_view_form(self, form, value=None):
        kwargs = {'readonly': 'readonly', 'title': self.label}
        label_value = ''
        if value is not None:
            label_value = self.get_display_value(value)
        self.field_key = 'f%s' % self.id
        with get_publisher().error_context(
            source_label=_('Field: %s') % self.ellipsized_label, source_url=self.get_admin_url()
        ):
            self.perform_more_widget_changes(form, kwargs, False)

        if self.widget_class is RadiobuttonsWithImagesWidget:
            form.add(
                self.widget_class,
                self.field_key,
                value=value,
                **kwargs,
            )
            widget = form.get_widget(self.field_key)
            widget.field = self
        else:
            form.add(
                StringWidget,
                self.field_key + '_label',
                value=label_value,
                size=len(label_value or '') + 2,
                **kwargs,
            )
            label_widget = form.get_widget(self.field_key + '_label')
            # don't let subwidget overwrite label widget value
            label_widget.secondary = True
            get_request().form[label_widget.name] = label_value
            label_widget.field = self
            form.add(HiddenWidget, self.field_key, value=value)
            form.get_widget(self.field_key).field = self
            widget = form.get_widget(self.field_key + '_label')

        if self.extra_css_class:
            if hasattr(widget, 'extra_css_class') and widget.extra_css_class:
                widget.extra_css_class = '%s %s' % (widget.extra_css_class, self.extra_css_class)
            else:
                widget.extra_css_class = self.extra_css_class
        return widget

    def set_value(self, data, value, raise_on_error=False):
        data_source = data_sources.get_object(self.data_source)
        if raise_on_error and value and data_source and data_source.type != 'jsonp':
            # check value (this looks up on id only, not text, convert_value_from_anything()
            # can be called before if the value may be a text value, as it's done in the
            # wf/backoffice_fields.py action.
            if not data_source.get_structured_value(value):
                raise SetValueError(
                    _('no matching value in datasource (field id: %(field)s, value: %(value)r)')
                    % {'field': self.id, 'value': value}
                )
        super().set_value(data, value, raise_on_error=raise_on_error)

    def store_display_value(self, data, field_id, raise_on_error=False):
        value = data.get(field_id)
        if not value:
            return ''
        data_source = data_sources.get_object(self.data_source)
        if data_source and data_source.type == 'jsonp':
            if get_request():
                display_value = get_request().form.get('f%s_display' % field_id)
                real_data_source = data_source.data_source
                if display_value is None:
                    display_value = get_session().get_jsonp_display_value(
                        '%s_%s' % (real_data_source.get('value'), value)
                    )
                else:
                    get_session().set_jsonp_display_value(
                        '%s_%s' % (real_data_source.get('value'), value), display_value
                    )
                return display_value
        with get_publisher().with_language('default'):
            return self.get_display_value(value)

    def store_structured_value(self, data, field_id, raise_on_error=False):
        data_source = data_sources.get_object(self.data_source)
        if data_source is None:
            return

        if data_source.type == 'jsonp':
            return

        with get_publisher().with_language('default'):
            value = data_source.get_structured_value(data.get(field_id))
        if value is None and raise_on_error:
            raise SetValueError(_('datasource is unavailable (field id: %s)') % field_id)

        if value is None or set(value.keys()) == {'id', 'text'}:
            return
        return value

    def convert_value_from_anything(self, value):
        if not value:
            return None
        value = str(value)
        if self.data_source:
            data_source = data_sources.get_object(self.data_source)
            if data_source.type and data_source.type.startswith('carddef:'):
                card_value = data_source.get_card_structured_value_by_id(value)
                if card_value:
                    value = str(card_value['id'])
                else:
                    raise UnknownCardValueError(value)
        return value

    def convert_value_from_str(self, value):
        # caller should also call store_display_value and store_structured_value
        return value

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
            ('list', _('List'), 'list'),
            ('radio', _('Radio buttons'), 'radio'),
            ('autocomplete', _('Autocomplete'), 'autocomplete'),
            ('map', _('Map (requires geographical data)'), 'map'),
            ('timetable', _('Timetable'), 'timetable'),
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
        form.add(
            CheckboxWidget,
            'display_disabled_items',
            title=_('Display disabled items'),
            value=self.display_disabled_items,
            advanced=True,
        )
        form.add(
            StringWidget,
            'initial_date_alignment',
            title=_('Initial date alignment'),
            value=self.initial_date_alignment,
            validation_function=ComputedExpressionWidget.validate_template,
            attrs={
                'data-dynamic-display-child-of': 'display_mode',
                'data-dynamic-display-value': 'timetable',
            },
        )

        self.fill_image_options_admin_form(
            form,
            attrs={'data-dynamic-display-child-of': 'display_mode', 'data-dynamic-display-value': 'images'},
        )

        self.fill_zoom_admin_form(
            form, attrs={'data-dynamic-display-child-of': 'display_mode', 'data-dynamic-display-value': 'map'}
        )
        initial_position_widget = form.add(
            SingleSelectWidget,
            'initial_position',
            title=_('Initial Position'),
            options=(
                ('', _('Default position (from markers)'), ''),
                ('geoloc', _('Device geolocation'), 'geoloc'),
                ('geoloc-front-only', _('Device geolocation (only in frontoffice)'), 'geoloc-front-only'),
                ('template', _('From template'), 'template'),
            ),
            value=self.initial_position or '',
            extra_css_class='widget-inline-radio',
            attrs={
                'data-dynamic-display-child-of': 'display_mode',
                'data-dynamic-display-value': 'map',
                'data-dynamic-display-parent': 'true',
            },
        )
        form.add(
            StringWidget,
            'position_template',
            value=self.position_template,
            size=80,
            required=False,
            hint=_('Positions (using latitute;longitude format) and addresses are supported.'),
            validation_function=ComputedExpressionWidget.validate_template,
            attrs={
                'data-dynamic-display-child-of': initial_position_widget.get_name(),
                'data-dynamic-display-value': 'template',
            },
        )
        form.add(
            CheckboxWidget,
            'use_hint_as_first_option',
            title=_('Use hint as first option'),
            value=self.use_hint_as_first_option,
            attrs={
                'data-dynamic-display-child-of': 'display_mode',
                'data-dynamic-display-value-in': 'list|autocomplete',
            },
        )
        form.add(
            RadiobuttonsWidget,
            'radio_orientation',
            title=_('Orientation of radio buttons'),
            options=[('auto', _('Automatic')), ('horizontal', _('Horizontal')), ('vertical', _('Vertical'))],
            value=self.radio_orientation,
            attrs={
                'data-dynamic-display-child-of': 'display_mode',
                'data-dynamic-display-value': 'radio',
            },
            extra_css_class='widget-inline-radio',
        )

    def get_admin_attributes(self):
        return WidgetField.get_admin_attributes(self) + [
            'display_mode',
            'items',
            'data_source',
            'in_filters',
            'display_disabled_items',
            'initial_zoom',
            'min_zoom',
            'max_zoom',
            'initial_position',
            'position_template',
            'initial_date_alignment',
            'use_hint_as_first_option',
            'image_desktop_size',
            'image_mobile_size',
            'radio_orientation',
        ]

    def check_admin_form(self, form):
        super().check_admin_form(form)
        self.check_items_admin_form(form)
        self.check_zoom_admin_form(form)
        self.check_display_mode(form)

    def stats(self, values):
        return item_items_stats(self, values)

    def get_initial_date_alignment(self):
        if not self.initial_date_alignment:
            return
        import wcs.workflows

        try:
            date = wcs.workflows.template_on_formdata(None, self.initial_date_alignment, autoescape=False)
        except TemplateError:
            return
        try:
            return misc.get_as_datetime(date)
        except ValueError:
            return

    def feed_session(self, value, display_value):
        real_data_source = self.get_real_data_source()
        if real_data_source and real_data_source.get('type') == 'jsonp':
            get_session().set_jsonp_display_value(
                '%s_%s' % (real_data_source.get('value'), value), display_value
            )

    def get_csv_heading(self):
        if self.data_source:
            return ['%s (%s)' % (self.label, _('identifier')), self.label]
        return [self.label]

    def get_csv_value(self, element, display_value=None, **kwargs):
        values = [element]
        if self.data_source:
            values.append(display_value)
        return values

    def export_to_json(self, include_id=False):
        field = super().export_to_json(include_id=include_id)
        if self.data_source:
            self.export_to_json_data_source(field)
        return field

    def get_filter_options(self, options):
        carddef = self.get_carddef()
        if not carddef:
            return options

        options = {option_id: option for option_id, option in options if option_id}
        options.update(self.get_carddef_options_by_ids(carddef, options.keys()))

        return list(options.items())

    def i18n_scan(self, base_location):
        yield from super(WidgetField, self).i18n_scan(base_location)
        yield from ItemFieldMixin.i18n_scan(self, base_location)

    def __getstate__(self):
        odict = super().__getstate__()
        # fix eventually serialized _cached_data_source
        odict.pop('_cached_data_source', None)
        return odict


register_field_class(ItemField)


def item_items_stats(field, values):
    if field.data_source:
        options = data_sources.get_items(field.data_source)
    else:
        options = field.items or []
    if len(options) == 0:
        return None
    no_records = len(values)
    if no_records == 0:
        return None
    r = TemplateIO(html=True)
    r += htmltext('<table class="stats">')
    r += htmltext('<thead><tr><th colspan="4">')
    r += field.label
    r += htmltext('</th></tr></thead>')
    r += htmltext('<tbody>')
    for option in options:
        if type(option) in (tuple, list):
            option_label = option[1]
            option_value = str(option[0])
        else:
            option_label = option
            option_value = option
        if field.key == 'item':
            no = len([None for x in values if x.data.get(field.id) == option_value])
        else:
            no = len([None for x in values if option_value in (x.data.get(field.id) or [])])
        r += htmltext('<tr>')
        r += htmltext('<td class="label">')
        r += option_label
        r += htmltext('</td>')

        r += htmltext('<td class="percent">')
        r += htmltext(' %.2f&nbsp;%%') % (100.0 * no / no_records)
        r += htmltext('</td>')
        r += htmltext('<td class="total">')
        r += '(%d/%d)' % (no, no_records)
        r += htmltext('</td>')
        r += htmltext('</tr>')
        r += htmltext('<tr>')
        r += htmltext('<td class="bar" colspan="3">')
        r += htmltext('<span style="width: %d%%"></span>' % (100 * no / no_records))
        r += htmltext('</td>')
        r += htmltext('</tr>')
    r += htmltext('</tbody>')
    r += htmltext('</table>')
    return r.getvalue()
