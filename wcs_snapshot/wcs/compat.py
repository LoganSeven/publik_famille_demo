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

from contextlib import contextmanager
from threading import Lock

from django.conf import settings
from django.http import HttpResponse
from django.template import TemplateDoesNotExist, loader
from django.template.response import TemplateResponse
from django.utils.deprecation import MiddlewareMixin
from django.views.generic.base import TemplateView
from quixote import get_publisher, get_request
from quixote.errors import PublishError
from quixote.http_request import Upload

from .publisher import WcsPublisher
from .qommon import force_str, template
from .qommon.http_request import HTTPRequest
from .qommon.publisher import set_publisher_class


def transfer_cookies(quixote_response, django_response):
    for name, attrs in quixote_response.cookies.items():
        value = str(attrs['value'])
        if 'samesite' not in attrs:
            attrs['samesite'] = 'None'
        kwargs = {}
        samesite_none = False
        for attr, val in attrs.items():
            attr = attr.lower()
            if val is None:
                continue
            if attr == 'comment':
                continue
            if attr == 'samesite' and val.lower() == 'none':
                samesite_none = True
            elif attr in ('expires', 'domain', 'path', 'max_age', 'samesite'):
                kwargs[attr] = val
            elif attr in ('httponly', 'secure') and val:
                kwargs[attr] = True
        django_response.set_cookie(name, value, **kwargs)
        # work around absent support for None in django 2.2
        if samesite_none:
            django_response.cookies[name]['samesite'] = 'None'


class TemplateWithFallbackView(TemplateView):
    quixote_response = None

    def get(self, request, *args, **kwargs):
        try:
            loader.get_template(self.template_name)
        except TemplateDoesNotExist:
            return quixote(self.request)

        try:
            context = self.get_context_data(**kwargs)
            self.quixote_response = request.quixote_request.response
        except PublishError as exc:
            context = {'body': get_publisher().finish_interrupted_request(exc)}
            self.quixote_response = get_request().response
        except Exception:
            # Follow native django DEBUG_PROPAGATE_EXCEPTIONS setting, re-raise exception
            # so it can be caught/stopped at when running tests.
            if settings.DEBUG_PROPAGATE_EXCEPTIONS:
                raise
            context = {'body': get_publisher().finish_failed_request()}
            self.quixote_response = get_request().response

        if self.quixote_response.content_type != 'text/html' or self.quixote_response.status_code != 200:
            body = context['body']
            if isinstance(body, template.QommonTemplateResponse):
                body.add_media()
                if body.is_django_native:
                    body = template.render(body.templates, body.context)
            response = HttpResponse(body)
            response.status_code = self.quixote_response.status_code
            response.reason_phrase = self.quixote_response.reason_phrase
        elif request.headers.get('X-Popup') == 'true':
            response = HttpResponse('<div><div class="popup-content">%s</div></div>' % context['body'])
        elif self.quixote_response.raw:
            # used for raw HTML snippets (for example in the test tool
            # results in inspect page).
            response = HttpResponse(context['body'])
        else:
            response = self.render_to_response(context)

            transfer_cookies(self.quixote_response, response)

        for name, value in self.quixote_response.generate_headers():
            if name in ('Connection', 'Content-Length'):
                continue
            response[name] = value

        return response

    def render_to_response(self, context, **response_kwargs):
        django_response = super().render_to_response(context, **response_kwargs)
        if self.quixote_response and self.quixote_response.status_code != 200:
            django_response.status_code = self.quixote_response.status_code
            django_response.reason_phrase = self.quixote_response.reason_phrase
            transfer_cookies(self.quixote_response, django_response)
            for name, value in self.quixote_response.generate_headers():
                if name in ('Connection', 'Content-Length'):
                    continue
                django_response[name] = value

        return django_response


class CompatHTTPRequest(HTTPRequest):
    def __init__(self, request):
        self.django_request = request
        self.django_request.quixote_request = self
        self.django_request.user = self.django_request.quixote_request.get_user
        self.response = None
        request.environ['SCRIPT_NAME'] = str(request.environ['SCRIPT_NAME'])
        request.environ['PATH_INFO'] = force_str(request.environ['PATH_INFO'])
        self.environ = self.django_request.META
        HTTPRequest.__init__(self, None, request.environ)
        self.scheme = str(self.django_request.scheme)

    def _process_urlencoded(self, length, params):
        return self._process_multipart(length, params)

    def _process_multipart(self, length, params):
        if not self.form:
            self.form = {}
        for k in self.django_request.POST:
            v = self.django_request.POST[k]
            if k.endswith('[]'):
                v = [x for x in self.django_request.POST.getlist(k)]
            self.form[k] = v

        for k, upload_file in self.django_request.FILES.items():
            upload = Upload(upload_file.name, upload_file.content_type, upload_file.charset)
            upload.fp = upload_file.file
            self.form[k] = upload

    def build_absolute_uri(self, *args):
        return self.django_request.build_absolute_uri(*args)


class CompatWcsPublisher(WcsPublisher):
    def filter_output(self, request, output):
        response = self.get_request().response
        if response.status_code == 304:
            # clients don't like to receive content with a 304
            return ''
        if response.content_type != 'text/html' or response.raw:
            return output
        if not hasattr(response, 'filter') or not response.filter:
            return output
        if request.headers.get('X-Popup') == 'true':
            return '<div><div class="popup-content">%s</div></div>' % output

        if isinstance(output, template.QommonTemplateResponse):
            template_response = output
        else:
            template_response = template.QommonTemplateResponse(
                templates=['wcs/base.html'], context={'body': output}
            )

        return self.render_template(request, response, template_response)

    def render_template(self, request, response, template_response):
        template_response.add_media()
        context = template.get_decorate_vars(
            template_response.context.get('body'),
            response,
            generate_breadcrumb=False,
            template_context=template_response.context,
        )
        context['request'] = request.django_request
        context.update(template_response.context)
        django_response = TemplateResponse(
            request.django_request,
            template_response.templates,
            context,
            content_type=response.content_type,
            status=response.status_code,
        )

        return django_response

    def set_app_dir(self, request):
        settings.THEME_SKELETON_URL = None
        super().set_app_dir(request)
        settings.THEME_SKELETON_URL = self.get_site_option('theme_skeleton_url')

    def process_request(self, request):
        self._set_request(request)
        try:
            self.parse_request(request)
            self.init_publish(request)
            output = self.try_publish(request)
        except PublishError as exc:
            output = self.finish_interrupted_request(exc)
        except Exception:
            # Follow native django DEBUG_PROPAGATE_EXCEPTIONS setting, re-raise exception
            # so it can be caught/stopped at when running tests.
            if settings.DEBUG_PROPAGATE_EXCEPTIONS:
                raise
            output = self.finish_failed_request()
        response = request.response

        output = self.filter_output(request, output)

        if isinstance(output, TemplateResponse):
            django_response = output
            django_response.render()
        else:
            content = output
            django_response = HttpResponse(
                content,
                content_type=response.content_type,
                status=response.status_code,
                reason=response.reason_phrase,
            )

        if not request.ignore_session:
            # it is necessary to save the session one last time as the actual
            # rendering may have altered it (for example a form would add its
            # token).
            self.session_manager.finish_successful_request()
            request.ignore_session = True  # no further changes

        transfer_cookies(response, django_response)

        for name, value in response.generate_headers():
            if name in ('Connection', 'Content-Length'):
                continue
            django_response[name] = value

        self._clear_request()
        return django_response


# keep a lock during quixote processing as it's not meant to work with threads;
# the publisher instance can't be shared for concurrent requests.
quixote_lock = Lock()


def quixote(request):
    pub = get_publisher()
    return pub.process_request(pub.get_request())


quixote.handles_start_request = True


@contextmanager
def request(request):
    pub = get_publisher()
    yield
    pub._clear_request()


class PublishErrorMiddleware(MiddlewareMixin):
    def process_exception(self, request, exception):
        if not isinstance(exception, PublishError):
            return None
        request = get_request()

        content_type = None
        status_code = getattr(exception, 'status_code', None)

        if hasattr(exception, 'render'):
            exception_body = exception.render()
        else:
            exception_body = str(exception)
            content_type = 'text/plain'

        django_response = HttpResponse(
            exception_body,
            content_type=content_type or request.response.content_type,
            status=status_code or request.response.status_code,
            reason=request.response.reason_phrase,
        )

        transfer_cookies(request.response, django_response)

        for name, value in request.response.generate_headers():
            if name in ('Connection', 'Content-Length'):
                continue
            django_response[name] = value

        return django_response


set_publisher_class(CompatWcsPublisher)
