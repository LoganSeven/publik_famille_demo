# authentic2 - versatile identity manager
# Copyright (C) 2010-2019 Entr'ouvert
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import urllib.parse

from django.apps import apps

from authentic2 import app_settings
from authentic2.utils.cache import GlobalCache
from authentic2.utils.misc import same_origin


@GlobalCache(timeout=60)
def _base_urls_map():
    from authentic2.models import Service

    base_urls_map = {}
    for service in Service.objects.select_related().select_subclasses():
        for url in service.get_base_urls():
            base_urls_map[url] = (type(service), service.pk)
    return base_urls_map


def clean_service(request):
    request.session.pop('service_type', None)
    request.session.pop('service_pk', None)


def _set_session_service(session, service):
    if 'home_url' in session:
        del session['home_url']
    if service:
        session['service_type'] = [type(service)._meta.app_label, type(service)._meta.model_name]
        session['service_pk'] = service.pk


def set_service(request, service):
    # do not set service on non document fetch (<script> tags, etc..)
    headers = request.headers
    if 'sec-fetch-dest' in headers and headers['sec-fetch-dest'] != 'document':
        return
    request._service = service
    _set_session_service(request.session, service)


def set_home_url(request, url=None):
    if not url:
        from .misc import select_next_url

        url = select_next_url(request, default=None)
    if not url or not urllib.parse.urlparse(url).netloc:
        # clean saved home_url
        request.session.pop('home_url', None)
        return
    urls_map = _base_urls_map()
    for base_url, (Model, pk) in urls_map.items():
        if same_origin(base_url, url):
            set_service(request, Model.objects.get(pk=pk))
            break
    else:
        clean_service(request)
    request.session['home_url'] = url


def get_service(request):
    if not hasattr(request, '_service'):
        if 'service_type' in request.session and 'service_pk' in request.session:
            ServiceKlass = apps.get_app_config(request.session['service_type'][0]).get_model(
                request.session['service_type'][1]
            )
            try:
                request._service = ServiceKlass.objects.get(pk=request.session['service_pk'])
            except ServiceKlass.DoesNotExist:
                request._service = None
        else:
            request._service = None
    return request._service


def get_home_url(request):
    service = get_service(request)
    if request.session.get('home_url'):
        return request.session['home_url']
    elif service and service.home_url:
        return service.home_url
    elif service and service.ou and service.ou.home_url:
        return service.ou.home_url
    elif request.user.is_authenticated and request.user.ou and request.user.ou.home_url:
        return request.user.ou.home_url
    else:
        return app_settings.A2_HOMEPAGE_URL
