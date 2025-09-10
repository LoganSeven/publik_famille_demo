# w.c.s. - web application for online forms
# Copyright (C) 2005-2016  Entr'ouvert
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

import collections
import json
import urllib.parse

from django.conf import settings
from PIL import Image
from PIL.TiffImagePlugin import IFDRational
from quixote import get_publisher

from wcs.workflows import WorkflowStatusItem, register_item_class

from ..qommon import _, force_str
from ..qommon.errors import ConnectionError
from ..qommon.form import CheckboxWidget, ComputedExpressionWidget, RadiobuttonsWidget
from ..qommon.misc import http_get_page, normalize_geolocation


class GeolocateWorkflowStatusItem(WorkflowStatusItem):
    description = _('Geolocation')
    key = 'geolocate'
    category = 'formdata-action'

    method = 'address_string'
    address_string = None
    map_variable = None
    photo_variable = None
    overwrite = True

    def get_parameters(self):
        return ('method', 'address_string', 'map_variable', 'photo_variable', 'overwrite', 'condition')

    def get_inspect_parameters(self):
        parameters = super().get_inspect_parameters()
        if self.method != 'address_string':
            parameters.remove('address_string')
        if self.method != 'map_variable':
            parameters.remove('map_variable')
        if self.method != 'photo_variable':
            parameters.remove('photo_variable')
        return parameters

    def add_parameters_widgets(self, form, parameters, prefix='', formdef=None, **kwargs):
        super().add_parameters_widgets(form, parameters, prefix=prefix, formdef=formdef, **kwargs)
        methods = collections.OrderedDict(
            [
                ('address_string', _('Address String')),
                ('map_variable', _('Map Data')),
                ('photo_variable', _('Photo Data')),
            ]
        )

        if 'method' in parameters:
            form.add(
                RadiobuttonsWidget,
                '%smethod' % prefix,
                title=_('Method'),
                options=list(methods.items()),
                value=self.method,
                attrs={'data-dynamic-display-parent': 'true'},
                extra_css_class='widget-inline-radio',
            )
        if 'address_string' in parameters:
            form.add(
                ComputedExpressionWidget,
                '%saddress_string' % prefix,
                size=50,
                title=_('Address String'),
                hint=_(
                    'For example: {{ form_var_street_number }} {{ form_var_street_name }}, {{ form_var_city }}'
                ),
                value=self.address_string,
                attrs={
                    'data-dynamic-display-child-of': '%smethod' % prefix,
                    'data-dynamic-display-value': methods.get('address_string'),
                },
            )
        if 'map_variable' in parameters:
            form.add(
                ComputedExpressionWidget,
                '%smap_variable' % prefix,
                size=50,
                title=_('Map data (geographical coordinates)'),
                hint=_('For example: {{ form_var_map }}'),
                value=self.map_variable,
                attrs={
                    'data-dynamic-display-child-of': '%smethod' % prefix,
                    'data-dynamic-display-value': methods.get('map_variable'),
                },
            )
        if 'photo_variable' in parameters:
            form.add(
                ComputedExpressionWidget,
                '%sphoto_variable' % prefix,
                size=50,
                title=_('Photo data (image file with EXIF metadata)'),
                hint=_('For example: {{ form_var_image }}'),
                value=self.photo_variable,
                attrs={
                    'data-dynamic-display-child-of': '%smethod' % prefix,
                    'data-dynamic-display-value': methods.get('photo_variable'),
                },
            )
        if 'overwrite' in parameters:
            form.add(
                CheckboxWidget,
                '%soverwrite' % prefix,
                title=_('Overwrite existing geolocation'),
                value=self.overwrite,
            )

    def get_computed_strings(self):
        yield from super().get_computed_strings()
        yield getattr(self, self.method, '')  # address_string / map_variable / photo_variable

    def perform(self, formdata):
        if not self.method:
            return
        if not formdata.formdef.geolocations:
            return
        geolocation_point = list(formdata.formdef.geolocations.keys())[0]
        if not formdata.geolocations:
            formdata.geolocations = {}
        if formdata.geolocations.get(geolocation_point) and not self.overwrite:
            return
        location = getattr(self, 'geolocate_' + self.method)(formdata)
        if location:
            formdata.geolocations[geolocation_point] = location
            formdata.store()

    def geolocate_address_string(self, formdata, compute_template=True):
        if compute_template:
            try:
                address = self.compute(self.address_string, record_errors=False, raises=True)
            except Exception as e:
                get_publisher().record_error(
                    _('error in template for address string [%s]') % str(e), formdata=formdata, exception=e
                )
                return
        else:
            # this is when the action is being executed to prefill a map field;
            # the template has already been rendered.
            address = self.address_string

        if not address:
            return
        url = get_publisher().get_geocoding_service_url()
        if '?' in url:
            url += '&'
        else:
            url += '?'
        url += 'q=%s' % urllib.parse.quote(address)
        url += '&format=json'
        url += '&accept-language=%s' % (get_publisher().get_site_language() or 'en')

        try:
            dummy, dummy, data, dummy = http_get_page(url, raise_on_http_errors=True)
        except ConnectionError as e:
            exception_str = str(e)
            if settings.NOMINATIM_URL:
                exception_str = exception_str.replace(settings.NOMINATIM_URL, '(nominatim URL)')
            get_publisher().record_error(
                _('error calling geocoding service [%s]') % exception_str,
                formdata=formdata,
                exception=False,
            )
            return
        try:
            data = json.loads(force_str(data))
        except ValueError:
            return
        if len(data) == 0 or isinstance(data, dict):
            return
        coords = data[0]
        return normalize_geolocation({'lon': coords['lon'], 'lat': coords['lat']})

    def geolocate_map_variable(self, formdata):
        value = self.compute(self.map_variable)
        if not value:
            return

        try:
            lat, lon = str(value).split(';')
            lat_lon = normalize_geolocation({'lon': lon, 'lat': lat})
        except Exception as e:
            get_publisher().record_error(
                _('error geolocating from map variable'), formdata=formdata, exception=e
            )
            return

        return lat_lon

    def geolocate_photo_variable(self, formdata):
        with get_publisher().complex_data():
            value = self.compute(self.photo_variable, allow_complex=True)
            if not value:
                return
            value = get_publisher().get_cached_complex_data(value)

        if not hasattr(value, 'get_file_path'):
            return

        try:
            with Image.open(value.get_file_path()) as image:
                exif_data = image._getexif()
        except (AttributeError, OSError):
            return

        if exif_data:
            gps_info = exif_data.get(0x8825)
            if gps_info and 2 in gps_info and 4 in gps_info:
                # lat_ref will be N/S, lon_ref wil l be E/W
                # lat and lon will be degrees/minutes/seconds (value, denominator),
                # like ((33, 1), (51, 1), (2191, 100))
                lat, lon = gps_info[2], gps_info[4]
                try:
                    lat_ref = gps_info[1]
                except KeyError:
                    lat_ref = 'N'
                try:
                    lon_ref = gps_info[3]
                except KeyError:
                    lon_ref = 'E'
                if isinstance(lat[0], IFDRational):
                    lat = 1.0 * lat[0] + 1.0 * lat[1] / 60 + 1.0 * lat[2] / 3600
                    lon = 1.0 * lon[0] + 1.0 * lon[1] / 60 + 1.0 * lon[2] / 3600
                else:
                    # Pillow < 7.2 compat
                    try:
                        lat = (
                            1.0 * lat[0][0] / lat[0][1]
                            + 1.0 * lat[1][0] / lat[1][1] / 60
                            + 1.0 * lat[2][0] / lat[2][1] / 3600
                        )
                        lon = (
                            1.0 * lon[0][0] / lon[0][1]
                            + 1.0 * lon[1][0] / lon[1][1] / 60
                            + 1.0 * lon[2][0] / lon[2][1] / 3600
                        )
                    except ZeroDivisionError:
                        return
                if lat_ref == 'S':
                    lat = -lat
                if lon_ref == 'W':
                    lon = -lon
                return normalize_geolocation({'lon': lon, 'lat': lat})
        return

    def perform_in_tests(self, formdata):
        self.perform(formdata)


register_item_class(GeolocateWorkflowStatusItem)
