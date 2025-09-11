# Authentic2 © Entr'ouvert

from django.http import HttpResponse, HttpResponseNotAllowed

from .misc import same_origin

DEFAULT_METHODS = ('GET',)


def is_cors_request(request):
    return request.headers.get('sec-fetch-mode') == 'cors'


def is_preflight_request(request):
    return request.method == 'OPTIONS' and is_cors_request(request)


def is_good_origin(request, reference_url):
    origin = request.headers.get('Origin', 'null')
    if origin == 'null':
        return False
    if isinstance(reference_url, str):
        return same_origin(origin, reference_url)
    else:
        for url in reference_url:
            if same_origin(origin, url):
                return True
    return False


def preflight_response(request, *, methods=DEFAULT_METHODS, **kwargs):
    method = request.headers.get('Access-Control-Request-Method', '').upper()
    if method not in methods:
        return HttpResponseNotAllowed(methods)
    return set_headers(HttpResponse(''), methods=methods, **kwargs)


def set_headers(
    response,
    *,
    origin='null',
    with_credentials=False,
    methods=DEFAULT_METHODS,
    headers=('x-requested-with',),
    max_age=86400,
):
    # origin is an HttpRequest, take origin from it
    if hasattr(origin, 'headers'):
        origin = origin.headers['Origin']

    response['Access-Control-Allow-Origin'] = origin
    response['Access-Control-Max-Age'] = str(max_age)
    response['Access-Control-Allow-Methods'] = ','.join(methods)
    if headers:
        response['Access-Control-Allow-Headers'] = ','.join(headers)
    if with_credentials:
        response['Access-Control-Allow-Credentials'] = 'true'
    return response
