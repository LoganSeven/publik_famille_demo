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
import hashlib
import json
import re
import urllib.parse
import xml.etree.ElementTree as ET

from django.conf import settings
from django.core.cache import cache as django_cache
from django.utils.encoding import force_bytes, force_str
from quixote import get_publisher, get_request

import wcs.sql
from wcs.api_utils import MissingSecret, get_secret_and_orig, sign_url
from wcs.qommon.errors import ConnectionError
from wcs.workflows import WorkflowStatusItem

from .qommon import _, force_str, misc
from .qommon.form import (
    CheckboxWidget,
    CompositeWidget,
    ComputedExpressionWidget,
    DurationWidget,
    RadiobuttonsWidget,
    StringWidget,
    WidgetDict,
)
from .qommon.misc import JSONEncoder, get_variadic_url
from .qommon.storage import StoredObjectMixin
from .qommon.template import Template
from .qommon.xml_storage import XmlObjectMixin
from .utils import add_timing_mark


class NamedWsCallImportError(Exception):
    pass


class PayloadError(Exception):
    pass


class UnflattenKeysException(Exception):
    def get_summary(self):
        return _('unable to unflatten payload keys (%s)') % self


def unflatten_keys(d):
    """Transform:

       {"a/b/0/x": "1234"}

    into:

       {"a": {"b": [{"x": "1234"}]}}
    """
    if not isinstance(d, dict) or not d:  # unflattening an empty dict has no sense
        return d

    def split_key(key):
        def map_key(x):
            if misc.is_ascii_digit(x):
                return int(x)
            if isinstance(x, str):
                # allow / char escaping
                return x.replace('//', '/')
            return x

        # split key by single / only
        return [map_key(x) for x in re.split(r'(?<!/)/(?!/)', key)]

    keys = [(split_key(key), key) for key in d]
    try:
        keys.sort()
    except TypeError:
        # sorting fail means that there is a mix between lists and dicts
        raise UnflattenKeysException(_('there is a mix between lists and dicts'))

    def set_path(path, orig_key, d, value, i=0):
        assert path

        key, tail = path[i], path[i + 1 :]

        if not tail:  # end of path, set the value
            if isinstance(key, int):
                assert isinstance(d, list)
                if len(d) != key:
                    raise UnflattenKeysException(_('incomplete array before key "%s"') % orig_key)
                d.append(value)
            else:
                if not isinstance(d, dict):
                    prev_key = '/'.join(map(str, path[:-1]))
                    raise UnflattenKeysException(
                        _('key "%(orig_key)s" invalid because key "%(prev_key)s" has value "%(value)s"')
                        % {'orig_key': orig_key, 'prev_key': prev_key, 'value': d}
                    )
                d[key] = value
            return  # end of recursion

        new = [] if isinstance(tail[0], int) else {}

        if isinstance(key, int):
            assert isinstance(d, list)
            if len(d) < key:
                raise UnflattenKeysException(
                    _('incomplete array before %(path)s in %(key)s')
                    % {'path': ('/'.join([str(x) for x in path[: i + 1]])), 'key': orig_key}
                )
            if len(d) == key:
                d.append(new)
            else:
                new = d[key]
        else:
            new = d.setdefault(key, new)
        set_path(path, orig_key, new, value, i + 1)

    # Is the first level an array (ie key is like "0/param") or a dict (key is like "param/0") ?
    if isinstance(keys[0][0][0], int):
        new = []
    else:
        new = {}
    for path, key in keys:
        value = d[key]
        set_path(path, key, new, value)
    return new


def get_app_error_code(response, data, response_type):
    app_error_code = 0
    app_error_code_header = response.headers.get('x-error-code')
    if app_error_code_header:
        # result is good only if header value is '0'
        try:
            app_error_code = int(app_error_code_header)
        except ValueError:
            app_error_code = app_error_code_header
    elif response_type == 'json':
        try:
            d = json.loads(force_str(data))
        except (ValueError, TypeError):
            pass
        else:
            if isinstance(d, dict) and d.get('err'):
                try:
                    app_error_code = int(d['err'])
                except ValueError:
                    app_error_code = d['err']
    return app_error_code


def get_cache_key(url, cache_duration):
    cache_key = f'{cache_duration}-{url}'
    return force_str(hashlib.md5(force_bytes(cache_key)).hexdigest())


def call_webservice(
    url,
    *,
    qs_data=None,
    request_signature_key=None,
    method=None,
    post_data=None,
    post_formdata=None,
    formdata=None,
    cache=False,
    cache_duration=None,
    timeout=None,
    notify_on_errors=False,
    record_on_errors=False,
    error_context_label=None,
    handle_connection_errors=True,
    **kwargs,
):
    # noqa pylint: disable=too-many-arguments

    error_context_label = error_context_label or _('Webservice')

    url = url.strip()
    if Template.is_template_string(url):
        variables = get_publisher().substitutions.get_context_variables(mode='lazy')
        url = get_variadic_url(url, variables)

    parsed = urllib.parse.urlparse(url)

    if not request_signature_key and '@' not in parsed.netloc:
        try:
            request_signature_key, orig = get_secret_and_orig(url)
        except MissingSecret:
            pass
        else:
            if not qs_data:
                qs_data = {}
            qs_data['orig'] = orig

    if qs_data:  # merge qs_data into url
        qs = list(urllib.parse.parse_qsl(parsed.query))
        for key, value in qs_data.items():
            with get_publisher().complex_data():
                try:
                    value = WorkflowStatusItem.compute(value, allow_complex=True, raises=True)
                except Exception as e:
                    get_publisher().record_error(exception=e, notify=True)
                else:
                    if value:
                        value = get_publisher().get_cached_complex_data(value)
                    if isinstance(value, (tuple, list, set)):
                        qs.extend((key, x) for x in value)
                    else:
                        value = str(value) if value is not None else ''
                        qs.append((key, value))
        qs = urllib.parse.urlencode(qs)
        url = urllib.parse.urlunparse(parsed[:4] + (qs,) + parsed[5:6])

    unsigned_url = url

    add_timing_mark(f'call_webservice {method} {url}', url=url)

    if method == 'GET':
        if cache is True:  # check request cache
            request = get_request()
            if hasattr(request, 'wscalls_cache') and unsigned_url in request.wscalls_cache:
                return (None,) + request.wscalls_cache[unsigned_url]
        if cache_duration and int(cache_duration):
            cache_key = 'wscall-%s' % get_cache_key(unsigned_url, cache_duration)
            cached_result = django_cache.get(cache_key)
            if cached_result:
                return (None,) + cached_result

    if request_signature_key:
        signature_key = str(WorkflowStatusItem.compute(request_signature_key))
        if signature_key:
            url = sign_url(url, signature_key)

    headers = {
        'Accept': 'application/json',
        'User-agent': 'w.c.s./0 (https://dev.entrouvert.org/projects/wcs/)',
    }
    payload = None

    # if post_data exists, payload is a dict built from it
    if method in ('PATCH', 'PUT', 'POST', 'DELETE') and post_data:
        payload = {}
        with get_publisher().complex_data():
            for key, value in post_data.items():
                try:
                    payload[key] = WorkflowStatusItem.compute(value, allow_complex=True, raises=True)
                except Exception as e:
                    get_publisher().record_error(exception=e, notify=True)
                else:
                    if payload[key]:
                        payload[key] = get_publisher().get_cached_complex_data(payload[key])
        payload = unflatten_keys(payload)

    # if formdata has to be sent, it's the payload. If post_data exists,
    # it's added in formdata['extra']
    if method in ('PATCH', 'PUT', 'POST', 'DELETE') and post_formdata:
        if formdata:
            formdata_dict = formdata.get_json_export_dict()
            if payload is not None:
                formdata_dict['extra'] = payload
            payload = formdata_dict

    try:
        request_kwargs = {
            'url': url,
            'headers': headers,
            'timeout': int(timeout) if timeout else None,
            'error_url': unsigned_url,
        }
        if method in ('PATCH', 'PUT', 'POST', 'DELETE'):
            if payload:
                headers['Content-type'] = 'application/json'
                try:
                    payload = json.dumps(payload, cls=JSONEncoder)
                except TypeError as e:
                    get_publisher().record_error(
                        exception=e,
                        context=error_context_label,
                        notify=notify_on_errors,
                        record=record_on_errors,
                    )
                    raise PayloadError(str(e)) from e
            response, status, data, dummy = misc._http_request(method=method, body=payload, **request_kwargs)
        else:
            response, status, data, dummy = misc.http_get_page(**request_kwargs)
            request = get_request()
            if cache is True and request and hasattr(request, 'wscalls_cache'):
                request.wscalls_cache[unsigned_url] = (status, data)
            if cache_duration:
                cache_key = 'wscall-%s' % get_cache_key(unsigned_url, cache_duration)
                django_cache.set(cache_key, (status, data), int(cache_duration))
    except ConnectionError as e:
        if not handle_connection_errors:
            raise e
        get_publisher().record_error(
            exception=e,
            context=error_context_label,
            notify=notify_on_errors,
            record=record_on_errors,
            interrupt_inspect=False,
        )
        return (None, None, None)

    if status >= 400 and (notify_on_errors or record_on_errors):
        app_error_code = get_app_error_code(response, data, 'json')
        record_wscall_error(
            status,
            data,
            response,
            app_error_code,
            notify_on_errors,
            record_on_errors,
            error_context_label=error_context_label,
        )

    return (response, status, data)


def record_wscall_error(
    status, data, response, app_error_code, notify_on_errors, record_on_errors, error_context_label
):
    try:
        json_data = json.loads(force_str(data))
        if not isinstance(json_data, dict):
            json_data = {}
    except (ValueError, TypeError):
        json_data = {}

    summary = '<no response>'
    if response is not None:
        summary = '%s %s' % (status, response.reason) if status != 200 else ''
        # add url to local variables, for traces
        url = response.request.url  # noqa pylint: disable=unused-variable
    if app_error_code != 0:
        details = [f'err: {app_error_code}'] if status == 200 else []
        for key in ['err_desc', 'err_class']:
            if json_data.get(key):
                details.append('%s: %s' % (key, json_data[key]))
        details = ', '.join(details) if details else ''
        summary = '%s (%s)' % (summary, details) if (summary and details) else (summary or details)

    get_publisher().record_error(
        summary,
        context=error_context_label,
        notify=notify_on_errors,
        record=record_on_errors,
        interrupt_inspect=False,
    )
    return summary


class NamedWsCall(wcs.sql.SqlWsCall, StoredObjectMixin, XmlObjectMixin):
    _names = 'wscalls'
    xml_root_node = 'wscall'
    backoffice_class = 'wcs.admin.wscalls.NamedWsCallPage'
    verbose_name = _('Webservice call')
    verbose_name_plural = _('Webservice calls')

    id = None
    name = None
    slug = None
    documentation = None
    request = None
    notify_on_errors = False
    record_on_errors = False

    SLUG_DASH = '_'

    # declarations for serialization
    XML_NODES = [
        ('name', 'str'),
        ('slug', 'str'),
        ('description', 'str'),  # legacy
        ('documentation', 'str'),
        ('request', 'request'),
        ('notify_on_errors', 'bool'),
        ('record_on_errors', 'bool'),
    ]

    def __init__(self, name=None):
        self.name = name

    def migrate(self):
        changed = False
        if getattr(self, 'description', None):  # 2024-04-07
            self.documentation = getattr(self, 'description')
            self.description = None
            changed = True
        if changed:
            self.store(comment=_('Automatic update'), snapshot_store_user=False)
        return changed

    def get_admin_url(self):
        base_url = get_publisher().get_backoffice_url()
        return '%s/settings/wscalls/%s/' % (base_url, self.id)

    @classmethod
    def has_admin_access(cls, user=None):
        backoffice_root = get_publisher().get_backoffice_root()
        return backoffice_root.is_global_accessible('settings')

    def get_computed_strings(self):
        if self.request:
            yield self.request.get('url')
            yield self.request.get('request_signature_key')
            if self.request.get('qs_data'):
                yield from self.request.get('qs_data').values()
            if self.request.get('post_data'):
                yield from self.request.get('post_data').values()

    @classmethod
    def import_from_xml_tree(cls, tree, include_id=False, check_deprecated=False, **kwargs):
        from wcs.backoffice.deprecations import DeprecatedElementsDetected, DeprecationsScan

        wscall = super().import_from_xml_tree(
            tree, include_id=include_id, check_deprecated=check_deprecated, **kwargs
        )

        if check_deprecated:
            # check for deprecated elements
            job = DeprecationsScan()
            try:
                job.check_deprecated_elements_in_object(wscall)
            except DeprecatedElementsDetected as e:
                raise NamedWsCallImportError(str(e))

        return wscall

    def export_request_to_xml(self, element, attribute_name, **kwargs):
        request = getattr(self, attribute_name)
        for attr in ('url', 'request_signature_key', 'method', 'timeout', 'cache_duration'):
            ET.SubElement(element, attr).text = force_str(request.get(attr) or '')
        for attr in ('qs_data', 'post_data'):
            data_element = ET.SubElement(element, attr)
            for k, v in (request.get(attr) or {}).items():
                sub = ET.SubElement(data_element, 'param')
                sub.attrib['key'] = str(k)
                sub.text = str(v)
        if request.get('post_formdata'):
            ET.SubElement(element, 'post_formdata')

    def import_request_from_xml(self, element, **kwargs):
        request = {}
        for attr in ('url', 'request_signature_key', 'method', 'timeout', 'cache_duration'):
            request[attr] = ''
            if element.find(attr) is not None and element.find(attr).text:
                request[attr] = force_str(element.find(attr).text)
        for attr in ('qs_data', 'post_data'):
            request[attr] = {}
            data_element = element.find(attr)
            if data_element is None:
                continue
            for param in data_element.findall('param'):
                request[attr][force_str(param.attrib['key'])] = force_str(param.text or '')
        request['post_formdata'] = bool(element.find('post_formdata') is not None)
        return request

    def store(self, comment=None, snapshot_store_user=True, application=None, *args, **kwargs):
        assert not self.is_readonly()
        if self.slug is None:
            # set slug if it's not yet there
            self.slug = self.get_new_slug()
        super().store(*args, **kwargs)
        if get_publisher().snapshot_class:
            get_publisher().snapshot_class.snap(
                instance=self, comment=comment, store_user=snapshot_store_user, application=application
            )

    @classmethod
    def get_substitution_variables(cls):
        return {'webservice': WsCallsSubstitutionProxy()}

    def get_dependencies(self):
        for string in self.get_computed_strings():
            yield from misc.get_dependencies_from_template(string)

    def __eq__(self, other):
        return bool(isinstance(other, NamedWsCall) and self.id == other.id)

    def call(self):
        notify_on_errors = self.notify_on_errors
        record_on_errors = self.record_on_errors
        if getattr(get_request(), 'disable_error_notifications', None) is True:
            notify_on_errors = False
            record_on_errors = False
        source_label = _('Webservice call (%(name)s, %(slug)s)') % {'name': self.name, 'slug': self.slug}
        source_url = self.get_admin_url()
        with get_publisher().error_context(source_label=source_label, source_url=source_url):
            try:
                data = call_webservice(
                    cache=True,
                    notify_on_errors=notify_on_errors,
                    record_on_errors=record_on_errors,
                    error_context_label=source_label,
                    **(self.request or {}),
                )[2]
                return json.loads(force_str(data))
            except UnflattenKeysException as e:
                get_publisher().record_error(
                    error_summary=e.get_summary(),
                    exception=e,
                    context=source_label,
                    notify=notify_on_errors,
                    record=record_on_errors,
                )


class WsCallsSubstitutionProxy:
    def __getattr__(self, attr):
        try:
            return NamedWsCall.get_by_slug(attr).call()
        except (KeyError, ValueError):
            raise AttributeError(attr)


class WsCallRequestWidget(CompositeWidget):
    def __init__(self, name, value=None, include_post_formdata=False, **kwargs):
        CompositeWidget.__init__(self, name, value, **kwargs)
        self.include_post_formdata = include_post_formdata

        if not value:
            value = {}

        methods = collections.OrderedDict(
            [
                ('GET', _('GET')),
                ('POST', _('POST (JSON)')),
                ('PUT', _('PUT (JSON)')),
                ('PATCH', _('PATCH (JSON)')),
                ('DELETE', _('DELETE (JSON)')),
            ]
        )
        self.add(
            RadiobuttonsWidget,
            'method',
            title=_('Method'),
            options=list(methods.items()),
            value=value.get('method') or 'GET',
            attrs={'data-dynamic-display-parent': 'true'},
            extra_css_class='widget-inline-radio',
        )
        method_widget = self.get_widget('method')
        self.add(StringWidget, 'url', title=_('URL'), value=value.get('url'), size=80)
        self.add(
            WidgetDict,
            'qs_data',
            title=_('Query string data'),
            value=value.get('qs_data') or {},
            element_value_type=ComputedExpressionWidget,
            allow_empty_values=True,
            value_for_empty_value='',
        )

        if self.include_post_formdata:
            self.add(
                CheckboxWidget,
                'post_formdata',
                title=_('Post formdata'),
                value=value.get('post_formdata'),
                attrs={
                    'data-dynamic-display-child-of': method_widget.get_name(),
                    'data-dynamic-display-value': methods.get('POST'),
                },
            )
        self.add(
            PostDataWidget,
            'post_data',
            value=value.get('post_data') or {},
            attrs={
                'data-dynamic-display-child-of': method_widget.get_name(),
                'data-dynamic-display-value': methods.get('POST'),
            },
        )

        self.add(
            ComputedExpressionWidget,
            'request_signature_key',
            title=_('Request Signature Key'),
            value=value.get('request_signature_key'),
        )

        def validate_timeout(value):
            if value and not value.isdecimal():
                raise ValueError(_('Timeout must be empty or a number.'))

        self.add(
            DurationWidget,
            'cache_duration',
            value=value.get('cache_duration'),
            title=_('Cache Duration'),
            required=False,
            attrs={
                'data-dynamic-display-child-of': method_widget.get_name(),
                'data-dynamic-display-value': methods.get('GET'),
            },
        )

        self.add(
            StringWidget,
            'timeout',
            title=_('Timeout'),
            value=value.get('timeout'),
            size=20,
            hint=_(
                'Stop waiting for a response after this number of seconds. '
                'Leave empty to get default timeout (%ss).'
            )
            % settings.REQUESTS_TIMEOUT,
            validation_function=validate_timeout,
        )

    def _parse(self, request):
        values = {}
        for name in (
            'url',
            'request_signature_key',
            'qs_data',
            'method',
            'post_formdata',
            'post_data',
            'timeout',
            'cache_duration',
        ):
            if not self.include_post_formdata and name == 'post_formdata':
                continue
            value = self.get(name)
            if value:
                values[name] = value
        self.value = values or None


class PostDataWidget(WidgetDict):
    template_name = 'qommon/forms/widgets/post-data.html'

    def __init__(self, name, *args, **kwargs):
        super().__init__(
            name,
            title=_('POST data'),
            element_value_type=ComputedExpressionWidget,
            allow_empty_values=True,
            value_for_empty_value='',
            **kwargs,
        )
