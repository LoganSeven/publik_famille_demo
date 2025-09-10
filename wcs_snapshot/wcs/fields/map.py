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

import random
import re

from quixote import get_publisher

from wcs.qommon import _
from wcs.qommon.form import ComputedExpressionWidget, MapWidget, SingleSelectWidget, StringWidget
from wcs.qommon.misc import xml_node_text

from .base import SetValueError, WidgetField, register_field_class


class MapOptionsMixin:
    initial_zoom = None
    min_zoom = None
    max_zoom = None

    @classmethod
    def get_zoom_levels(cls):
        zoom_levels = [
            (None, '---'),
            ('0', _('Whole world')),
            ('6', _('Country')),
            ('9', _('Wide area')),
            ('11', _('Area')),
            ('13', _('Town')),
            ('16', _('Small road')),
            ('18', _('Neighbourhood')),
            ('19', _('Ant')),
        ]
        return zoom_levels

    def fill_zoom_admin_form(self, form, **kwargs):
        zoom_levels = self.get_zoom_levels()
        zoom_levels_dict = dict(zoom_levels)
        default_zoom_level = get_publisher().get_default_zoom_level()
        initial_zoom_levels = zoom_levels[:]
        initial_zoom_levels[0] = (None, _('Default (%s)') % zoom_levels_dict[default_zoom_level])
        form.add(
            SingleSelectWidget,
            'initial_zoom',
            title=_('Initial zoom level'),
            value=self.initial_zoom,
            options=initial_zoom_levels,
            **kwargs,
        )
        form.add(
            SingleSelectWidget,
            'min_zoom',
            title=_('Minimal zoom level'),
            value=self.min_zoom,
            options=zoom_levels,
            required=False,
            **kwargs,
        )
        form.add(
            SingleSelectWidget,
            'max_zoom',
            title=_('Maximal zoom level'),
            value=self.max_zoom,
            options=zoom_levels,
            required=False,
            **kwargs,
        )

    def check_zoom_admin_form(self, form):
        initial_zoom = form.get_widget('initial_zoom').parse()
        min_zoom = form.get_widget('min_zoom').parse()
        max_zoom = form.get_widget('max_zoom').parse()
        if min_zoom and max_zoom:
            if int(min_zoom) > int(max_zoom):
                form.get_widget('min_zoom').set_error(
                    _('Minimal zoom level cannot be greater than maximal zoom level.')
                )
        # noqa pylint: disable=too-many-boolean-expressions
        if (initial_zoom and min_zoom and int(initial_zoom) < int(min_zoom)) or (
            initial_zoom and max_zoom and int(initial_zoom) > int(max_zoom)
        ):
            form.get_widget('initial_zoom').set_error(
                _('Initial zoom level must be between minimal and maximal zoom levels.')
            )


class MapField(WidgetField, MapOptionsMixin):
    key = 'map'
    description = _('Map')

    initial_position = None
    default_position = None
    position_template = None

    widget_class = MapWidget
    extra_attributes = [
        'initial_zoom',
        'min_zoom',
        'max_zoom',
        'initial_position',
        'default_position',
        'position_template',
    ]

    def migrate(self):
        changed = super().migrate()
        if not self.initial_position:  # 2023-04-20
            if getattr(self, 'init_with_geoloc', False):
                self.initial_position = 'geoloc'
                changed = True
            elif self.default_position:
                self.initial_position = 'point'
                changed = True
        return changed

    def fill_admin_form(self, form, formdef):
        super().fill_admin_form(form, formdef)
        self.fill_zoom_admin_form(form, tab=('position', _('Position')))
        initial_position_widget = form.add(
            SingleSelectWidget,
            'initial_position',
            title=_('Initial Position'),
            options=(
                ('', _('Default position'), ''),
                ('point', _('Specific point'), 'point'),
                ('geoloc', _('Device geolocation'), 'geoloc'),
                ('geoloc-front-only', _('Device geolocation (only in frontoffice)'), 'geoloc-front-only'),
                ('template', _('From template'), 'template'),
            ),
            value=self.initial_position or '',
            extra_css_class='widget-inline-radio',
            tab=('position', _('Position')),
            attrs={'data-dynamic-display-parent': 'true'},
        )
        form.add(
            MapWidget,
            'default_position',
            value=self.default_position,
            default_zoom='9',
            required=False,
            tab=('position', _('Position')),
            attrs={
                'data-dynamic-display-child-of': initial_position_widget.get_name(),
                'data-dynamic-display-value': 'point',
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
            tab=('position', _('Position')),
            attrs={
                'data-dynamic-display-child-of': initial_position_widget.get_name(),
                'data-dynamic-display-value': 'template',
            },
        )

    def check_admin_form(self, form):
        self.check_zoom_admin_form(form)

    def get_admin_attributes(self):
        return WidgetField.get_admin_attributes(self) + [
            'initial_zoom',
            'min_zoom',
            'max_zoom',
            'initial_position',
            'default_position',
            'position_template',
        ]

    def default_position_init_with_xml(self, node, include_id=False, snapshot=False):
        self.default_position = None
        if node is None:
            return
        if node.find('lat') is not None:
            self.default_position = {
                'lat': float(xml_node_text(node.find('lat'))),
                'lon': float(xml_node_text(node.find('lon'))),
            }
        else:
            # legacy
            lat, lon = (float(x) for x in xml_node_text(node).split(';'))
            self.default_position = {'lat': lat, 'lon': lon}

    def get_prefill_value(self, user=None, force_string=True):
        if self.prefill.get('type') != 'string' or not self.prefill.get('value'):
            return (None, False)
        # template string must produce lat;lon to be interpreted as coordinates,
        # otherwise it will be interpreted as an address that will be geocoded.
        prefill_value, explicit_lock = super().get_prefill_value()
        if re.match(r'-?\d+(\.\d+)?;-?\d+(\.\d+)?$', prefill_value):
            return (self.convert_value_from_str(prefill_value), explicit_lock)

        from wcs.wf.geolocate import GeolocateWorkflowStatusItem

        geolocate = GeolocateWorkflowStatusItem()
        geolocate.method = 'address_string'
        geolocate.address_string = prefill_value
        coords = geolocate.geolocate_address_string(None, compute_template=False)
        return (coords, False)

    def get_view_value(self, value, **kwargs):
        widget = self.widget_class('x%s' % random.random(), value, readonly=True)
        return widget.render_widget_content()

    def get_rst_view_value(self, value, indent=''):
        try:
            return indent + '%(lat)s;%(lon)s' % value
        except TypeError:
            return ''

    def convert_value_from_str(self, value):
        try:
            lat, lon = (float(x) for x in value.split(';'))
        except (AttributeError, ValueError):
            return None
        return {'lat': lat, 'lon': lon}

    def get_json_value(self, value, **kwargs):
        return value

    def from_json_value(self, value):
        if isinstance(value, str):
            # backward compatibility
            return self.convert_value_from_str(value)
        return value

    def get_structured_value(self, data):
        return self.get_json_value(data.get(self.id))

    def set_value(self, data, value, raise_on_error=False):
        if isinstance(value, dict):
            try:
                value = {
                    'lat': float(value['lat']),
                    'lon': float(value['lon']),
                }
            except (KeyError, ValueError, TypeError):
                raise SetValueError(
                    _('invalid coordinates %(value)r (field id: %(id)s)') % {'value': value, 'id': self.id}
                )
        elif value == '':
            value = None
        elif value and ';' not in value:
            raise SetValueError(
                _('invalid coordinates %(value)r (missing ;) (field id: %(id)s)')
                % {'value': value, 'id': self.id}
            )
        elif value:
            try:
                lat, lon = (float(x) for x in value.split(';'))
            except ValueError:
                # will catch both "too many values to unpack" and invalid float values
                raise SetValueError(
                    _('invalid coordinates %(value)r (field id: %(id)s)') % {'value': value, 'id': self.id}
                )
            value = {'lat': lat, 'lon': lon}
        super().set_value(data, value)


register_field_class(MapField)
