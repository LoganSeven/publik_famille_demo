# w.c.s. - web application for online forms
# Copyright (C) 2005-2013  Entr'ouvert
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

import json

from django.http import HttpResponse
from quixote import get_publisher, get_request, get_response

from . import compat
from .qommon import _, misc, template


class Backoffice(compat.TemplateWithFallbackView):
    template_name = 'wcs/backoffice-legacy.html'
    template_names = None

    def post(self, request, *args, **kwargs):
        return self.get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        _request = None
        with compat.request(self.request):
            get_request().response.filter = {'backoffice_page': True}
            body = get_publisher().try_publish(get_request())
            if isinstance(body, template.QommonTemplateResponse):
                body.add_media()
                if body.is_django_native:
                    self.request.is_django_native = True
                    _request = get_request()
                    self.template_names = body.templates
                    context.update(body.context)
                else:
                    body = template.render(body.templates, body.context)
                    self.template_names = None
                get_publisher().session_manager.finish_successful_request()
            self.quixote_response = get_request().response
            context.update(template.get_decorate_vars(body, get_response(), generate_breadcrumb=True))

        # restore request for django mode
        if _request is not None:
            get_publisher()._set_request(_request)

        return context

    def get_template_names(self):
        return self.template_names or [self.template_name]


backoffice = Backoffice.as_view()
backoffice.handles_start_request = True


def i18n_js(request):
    get_request().ignore_session = True
    strings = {
        'confirmation': _('Are you sure?'),
        'file_type_error': _('Invalid file type'),
        'file_size_error': _('File size exceeds limits'),
        'geoloc_unknown_error': _('Geolocation: unknown error'),
        'geoloc_permission_denied': _('Access to your geolocation has been denied by your device'),
        'geoloc_position_unavailable': _('Geolocation: position unavailable'),
        'geoloc_timeout': _('Geolocation: timeout'),
        'map_position_marker_alt': _('Marker of selected position'),
        'map_search_error': _('An error occured while fetching results'),
        'map_search_hint': _('Search address'),
        'map_search_searching': _('Searching...'),
        'map_zoom_in': _('Zoom in'),
        'map_zoom_out': _('Zoom out'),
        'map_display_position': _('Display my position'),
        'map_leaflet_title_attribute': _('Leaflet, a JavaScript library for interactive maps'),
        's2_errorloading': _('The results could not be loaded'),
        's2_nomatches': _('No matches found'),
        's2_tooshort': _('Please enter more characters'),
        's2_loadmore': _('Loading more results...'),
        's2_searching': _('Searching...'),
        'close': _('Close'),
        'email_domain_suggest': _('Did you want to write'),
        'email_domain_fix': _('Apply fix'),
        'warn_condition_maybe_unknown_varname': _('The condition may be using unknown field variables.'),
    }
    return HttpResponse(
        'WCS_I18N = %s;\n' % json.dumps(strings, cls=misc.JSONEncoder), content_type='application/javascript'
    )


def robots_txt(request, *args, **kwargs):
    return HttpResponse(
        get_publisher().get_site_option('robots_txt', 'variables') or '', content_type='text/plain'
    )
