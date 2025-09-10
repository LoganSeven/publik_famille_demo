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

import ipaddress
import json
import sys
import threading
import time
import urllib.parse

import psycopg2
from django.conf import settings
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.utils.deprecation import MiddlewareMixin
from quixote import get_publisher
from quixote.errors import RequestError

from .compat import CompatHTTPRequest, CompatWcsPublisher, transfer_cookies
from .qommon.publisher import ImmediateRedirectException


class PublisherInitialisationMiddleware(MiddlewareMixin):
    '''Initializes the publisher according to the request server name.'''

    def process_request(self, request):
        pub = get_publisher()
        if not pub:
            pub = CompatWcsPublisher.create_publisher()
        compat_request = CompatHTTPRequest(request)
        request.t0 = compat_request.t0 = time.time()
        try:
            pub.init_publish(compat_request)
        except ImmediateRedirectException as e:
            return HttpResponseRedirect(e.location)
        except psycopg2.OperationalError:
            return HttpResponse('Error connecting to database', content_type='text/plain', status=503)

        if not pub.has_postgresql_config():
            return HttpResponse('Missing database configuration', content_type='text/plain', status=503)

        pub._set_request(compat_request)
        try:
            pub.parse_request(compat_request)
        except RequestError as e:
            if compat_request.is_json():
                return HttpResponseBadRequest(
                    json.dumps(
                        {'err': 1, 'err_class': e.title, 'err_code': e.err_code, 'err_desc': e.public_msg}
                    ),
                    content_type='application/json',
                )
            return HttpResponseBadRequest(str(e))

        request._publisher = pub

        # handle session_var_<xxx> in query strings, add them to session and
        # redirect to same URL without the parameters
        if compat_request.get_method() == 'GET' and compat_request.form:
            query_string_allowed_vars = pub.get_site_option('query_string_allowed_vars') or ''
            query_string_allowed_vars = [x.strip() for x in query_string_allowed_vars.split(',')]
            had_session_variables = False
            session_variables = {}
            for k, v in list(compat_request.form.items()):
                if k.startswith('session_var_'):
                    had_session_variables = True
                    session_variable = str(k[len('session_var_') :])
                    # only add variable to session if it's a string, this
                    # handles the case of repeated parameters producing a
                    # list of values (by ignoring those parameters altogether).
                    if session_variable in query_string_allowed_vars and (isinstance(v, str)):
                        session_variables[session_variable] = v
                    del compat_request.form[k]
            if had_session_variables:
                pub.start_request()  # creates session
                compat_request.session.add_extra_variables(**session_variables)
                pub.finish_successful_request()  # commits session
                new_query_string = ''
                if compat_request.form:
                    new_query_string = '?' + urllib.parse.urlencode(compat_request.form)
                response = HttpResponseRedirect(compat_request.get_path() + new_query_string)
                transfer_cookies(compat_request.response, response)
                for name, value in compat_request.response.generate_headers():
                    if name in ('Connection', 'Content-Length'):
                        continue
                    response[name] = value
                return response

    def process_view(self, request, view_func, view_args, view_kwargs):
        if not getattr(view_func, 'handles_start_request', False):
            request._publisher.start_request()
        # returning nothing will make django continue processing.

    def process_response(self, request, response):
        pub = get_publisher()
        if pub:
            request = pub.get_request()
            if request and not request.ignore_session:
                # it is necessary to save the session one last time as the actual
                # rendering may have altered it (for example a form would add its
                # token).
                pub.session_manager.finish_successful_request()

            pub.cleanup()
        return response


class AfterJobsMiddleware(MiddlewareMixin):
    def process_response(self, request, response):
        pub = get_publisher()
        if settings.AFTERJOB_MODE == 'thread' or (
            settings.AFTERJOB_MODE != 'tests' and ('uwsgi' not in sys.modules and settings.WCS_MANAGE_COMMAND)
        ):
            threading.Thread(target=pub.process_after_jobs).start()
        else:
            pub.process_after_jobs()
        return response


def pass_through(request, pub):
    remote_addr = request.META.get('REMOTE_ADDR')
    if remote_addr:
        pass_through_ips = getattr(settings, 'MAINTENANCE_PASS_THROUGH_IPS', [])
        if remote_addr in pass_through_ips:
            return True
        for network in [x for x in pass_through_ips if '/' in x]:
            try:
                if ipaddress.ip_address(remote_addr) in ipaddress.ip_network(network, strict=False):
                    return True
            except ValueError:  # bad remote_addr or network syntax
                pass
    pass_through_header = pub.get_site_option('maintenance_pass_through_header', 'variables')
    if pass_through_header and pass_through_header in request.headers:
        return True
    return False


class MaintenanceMiddleware(MiddlewareMixin):
    def process_request(self, request):
        pub = get_publisher()
        maintenance_mode = pub.get_site_option('maintenance_page', 'variables')
        if maintenance_mode and not pass_through(request, pub):
            pub.install_lang()
            context = pub.get_site_options('variables')
            maintenance_message = pub.get_site_option('maintenance_page_message', 'variables')
            context['maintenance_message'] = maintenance_message or ''
            return TemplateResponse(
                request,
                ['hobo/maintenance/maintenance_page.html', 'wcs/maintenance_page.html'],
                context=context,
                status=503,
            ).render()
        return self.get_response(request)
