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

import base64
import collections
import datetime
import io
import json
import mimetypes
import sys
import traceback
import xml.etree.ElementTree as ET

from django.utils.encoding import force_str
from django.utils.text import slugify
from django.utils.timezone import now
from quixote import get_publisher, get_request
from quixote.html import TemplateIO, htmltext

from wcs.clamd import add_clamd_scan_job
from wcs.workflows import (
    AbortActionException,
    AttachmentEvolutionPart,
    EvolutionPart,
    WorkflowStatusItem,
    register_item_class,
)
from wcs.wscalls import (
    PayloadError,
    PostDataWidget,
    UnflattenKeysException,
    call_webservice,
    get_app_error_code,
    record_wscall_error,
)

from ..qommon import _, force_str, pgettext
from ..qommon.errors import ConnectionError
from ..qommon.form import (
    CheckboxWidget,
    ComputedExpressionWidget,
    RadiobuttonsWidget,
    SingleSelectWidget,
    StringWidget,
    VarnameWidget,
    WidgetDict,
)


class WorkflowWsCallEvolutionPart(EvolutionPart):
    varname = None
    data = None
    datetime = None
    url = None

    render_for_fts = None
    response_size_limit = 100000

    def __init__(self, varname=None, url=None, status=None, data=None):
        self.url = url
        self.varname = varname
        self.datetime = now()
        self.status = status
        if data:
            self.data = data[: self.response_size_limit]  # do not store huge responses

    def is_hidden(self):
        return True


class JournalWsCallErrorPart(WorkflowWsCallEvolutionPart):
    summary = None
    label = None
    render_for_fts = None

    response_size_limit = 10000

    def __init__(self, summary, label, **kwargs):
        self.summary = summary
        self.label = label
        super().__init__(**kwargs)

    def is_hidden(self):
        return not (get_request() and get_request().get_path().startswith('/backoffice/'))

    def view(self, **kwargs):
        r = TemplateIO(html=True)
        r += htmltext('<div class="ws-error">')
        r += htmltext('<h4 class="foldable folded">')
        if self.label:
            r += str(_('Error during webservice call "%s"') % self.label)
        else:
            r += str(_('Error during webservice call'))
        r += htmltext('</h4>')
        r += htmltext('<div>')
        r += htmltext('<p>%s</p>\n') % self.summary
        if self.data:
            try:
                json_data = json.loads(force_str(self.data))
            except ValueError:
                pass
            else:
                labels = {
                    'err': _('Error Code'),
                    'err_class': _('Error Class'),
                    'err_desc': _('Error Description'),
                    'reason': _('Reason'),
                }
                r += htmltext('<ul>')
                for attr in ('err', 'err_class', 'err_desc', 'reason'):
                    if attr in json_data:
                        r += htmltext('<li>%s: %s</li>\n') % (labels.get(attr), json_data[attr])
                r += htmltext('</ul>')
        r += htmltext('</div>')
        r += htmltext('</div>')
        return r.getvalue()

    def get_json_export_dict(self, anonymise=False, include_files=True):
        d = {
            'type': 'wscall-error',
        }
        if not anonymise:
            d.update({'summary': self.summary, 'label': self.label})
            try:
                d['data'] = self.data.decode() if isinstance(self.data, bytes) else self.data
            except UnicodeDecodeError:
                d['data_b64'] = base64.encodebytes(self.data).decode()
        return d


class WebserviceCallStatusItem(WorkflowStatusItem):
    description = _('Webservice')
    key = 'webservice_call'
    category = 'interaction'
    support_substitution_variables = True

    label = None
    url = None
    varname = None
    post = False
    request_signature_key = None
    post_data = None
    qs_data = None
    _method = None
    response_type = 'json'
    backoffice_filefield_id = None
    attach_file_to_history = True  # legacy behaviour
    store_all_responses = False

    action_on_app_error = ':pass'
    action_on_4xx = ':stop'
    action_on_5xx = ':stop'
    action_on_bad_data = ':pass'
    action_on_network_errors = ':stop'
    notify_on_errors = False
    record_on_errors = True
    record_errors = False
    set_marker_on_status = False

    @property
    def waitpoint(self):
        for jump_attribute in (
            'action_on_app_error',
            'action_on_4xx',
            'action_on_5xx',
            'action_on_bad_data',
            'action_on_network_errors',
        ):
            if getattr(self, jump_attribute) == ':stop':
                return True
        return False

    @property
    def method(self):
        if self._method in ('GET', 'POST', 'PUT', 'PATCH', 'DELETE'):
            return self._method
        if self.post or self.post_data:
            return 'POST'
        return 'GET'

    @method.setter
    def method(self, value):
        self._method = value

    def get_line_details(self):
        if self.label:
            return self.label
        return None

    def get_parameters(self):
        return (
            'label',
            'method',
            'url',
            'qs_data',
            'post',
            'post_data',
            'response_type',
            'varname',
            'request_signature_key',
            'store_all_responses',
            'attach_file_to_history',
            'backoffice_filefield_id',
            'action_on_app_error',
            'action_on_4xx',
            'action_on_5xx',
            'action_on_bad_data',
            'action_on_network_errors',
            'record_errors',
            'record_on_errors',
            'notify_on_errors',
            'condition',
            'set_marker_on_status',
        )

    def get_inspect_parameters(self):
        parameters = super().get_inspect_parameters()
        if self.method not in ('POST', 'PUT', 'PATCH', 'DELETE'):
            parameters.remove('post')
            parameters.remove('post_data')
        if self.response_type != 'attachment':
            if 'backoffice_filefield_id' in parameters:
                parameters.remove('backoffice_filefield_id')
            if 'attach_file_to_history' in parameters:
                parameters.remove('attach_file_to_history')
        if self.response_type != 'json':
            parameters.remove('store_all_responses')
            parameters.remove('action_on_bad_data')
        return parameters

    def add_parameters_widgets(self, form, parameters, prefix='', formdef=None, **kwargs):
        super().add_parameters_widgets(form, parameters, prefix=prefix, formdef=formdef, **kwargs)
        if 'label' in parameters:
            form.add(StringWidget, '%slabel' % prefix, size=40, title=_('Label'), value=self.label)

        if 'url' in parameters:
            form.add(
                StringWidget,
                '%surl' % prefix,
                title=_('URL'),
                value=self.url,
                size=80,
                hint=_('Common variables are available with the {{variable}} syntax.'),
            )
        if 'request_signature_key' in parameters:
            form.add(
                ComputedExpressionWidget,
                '%srequest_signature_key' % prefix,
                title=_('Request Signature Key'),
                value=self.request_signature_key,
                advanced=True,
            )
        if 'qs_data' in parameters:
            form.add(
                WidgetDict,
                '%sqs_data' % prefix,
                title=_('Query string data'),
                value=self.qs_data or {},
                element_value_type=ComputedExpressionWidget,
                allow_empty_values=True,
                value_for_empty_value='',
            )
        methods = collections.OrderedDict(
            [
                ('GET', _('GET')),
                ('POST', _('POST (JSON)')),
                ('PUT', _('PUT (JSON)')),
                ('PATCH', _('PATCH (JSON)')),
                ('DELETE', _('DELETE (JSON)')),
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
        if 'post' in parameters:
            form.add(
                CheckboxWidget,
                '%spost' % prefix,
                title=_('Post complete card/form data'),
                hint=_(
                    'Warning: this option sends the full content of the card/form, '
                    'with additional POST data in an additional "extra" key. It is often '
                    'not necessary.'
                ),
                value=self.post,
                attrs={
                    'data-dynamic-display-child-of': '%smethod' % prefix,
                    'data-dynamic-display-value-in': '|'.join(
                        [
                            str(_(methods['POST'])),
                            str(_(methods['PUT'])),
                            str(_(methods['PATCH'])),
                            str(_(methods['DELETE'])),
                        ]
                    ),
                },
                advanced=True,
            )
        if 'post_data' in parameters:
            form.add(
                PostDataWidget,
                '%spost_data' % prefix,
                value=self.post_data or {},
                attrs={
                    'data-dynamic-display-child-of': '%smethod' % prefix,
                    'data-dynamic-display-value-in': '|'.join(
                        [
                            str(_(methods['POST'])),
                            str(_(methods['PUT'])),
                            str(_(methods['PATCH'])),
                            str(_(methods['DELETE'])),
                        ]
                    ),
                },
            )

        response_types = collections.OrderedDict([('json', _('JSON')), ('attachment', _('Attachment'))])
        if 'response_type' in parameters:
            form.add(
                RadiobuttonsWidget,
                '%sresponse_type' % prefix,
                title=_('Response Type'),
                options=list(response_types.items()),
                value=self.response_type,
                attrs={'data-dynamic-display-parent': 'true'},
                extra_css_class='widget-inline-radio',
                tab=('response', _('Response')),
                default_value=self.__class__.response_type,
            )
        if 'varname' in parameters:
            form.add(
                VarnameWidget,
                '%svarname' % prefix,
                title=_('Identifier'),
                value=self.varname,
                hint=_('This is used as prefix for webservice result variable names.'),
            )

        if 'store_all_responses' in parameters:
            form.add(
                CheckboxWidget,
                '%sstore_all_responses' % prefix,
                title=_('Store all responses'),
                hint=_('By default only the latest response is stored.'),
                value=self.store_all_responses,
                attrs={
                    'data-dynamic-display-child-of': '%sresponse_type' % prefix,
                    'data-dynamic-display-value': response_types.get('json'),
                },
                default_value=self.__class__.store_all_responses,
                tab=('response', _('Response')),
            )

        if 'attach_file_to_history' in parameters:
            form.add(
                CheckboxWidget,
                '%sattach_file_to_history' % prefix,
                title=_('Include in form history'),
                value=self.attach_file_to_history,
                attrs={
                    'data-dynamic-display-child-of': '%sresponse_type' % prefix,
                    'data-dynamic-display-value': response_types.get('attachment'),
                },
                default_value=self.__class__.attach_file_to_history,
                tab=('response', _('Response')),
            )

        if 'backoffice_filefield_id' in parameters:
            options = self.get_backoffice_filefield_options()
            if options:
                form.add(
                    SingleSelectWidget,
                    '%sbackoffice_filefield_id' % prefix,
                    title=_('Store in a backoffice file field'),
                    value=self.backoffice_filefield_id,
                    options=[(None, '---', None)] + options,
                    attrs={
                        'data-dynamic-display-child-of': '%sresponse_type' % prefix,
                        'data-dynamic-display-value': response_types.get('attachment'),
                    },
                    tab=('response', _('Response')),
                )

        error_actions = [(':stop', _('Stop'), ':stop', {}), (':pass', _('Ignore'), ':pass', {})]
        error_actions.extend(
            [
                (x.id, _('Jump to %s') % x.name, str(x.id), {'data-goto-url': x.get_admin_url()})
                for x in self.get_workflow().possible_status
            ]
        )
        for attribute in (
            'action_on_app_error',
            'action_on_4xx',
            'action_on_5xx',
            'action_on_network_errors',
            'action_on_bad_data',
        ):
            if attribute not in parameters:
                continue
            if attribute == 'action_on_bad_data':
                attrs = {
                    'data-dynamic-display-child-of': '%sresponse_type' % prefix,
                    'data-dynamic-display-value': response_types.get('json'),
                }
            else:
                attrs = {}
            label = {
                'action_on_app_error': _('Action on application error'),
                'action_on_4xx': _('Action on HTTP error 4xx'),
                'action_on_5xx': _('Action on HTTP error 5xx'),
                'action_on_bad_data': _('Action on non-JSON response'),
                'action_on_network_errors': _('Action on network errors'),
            }.get(attribute)
            form.add(
                SingleSelectWidget,
                '%s%s' % (prefix, attribute),
                title=label,
                value=getattr(self, attribute),
                options=error_actions,
                attrs=attrs,
                tab=('error', _('Error Handling')),
                default_value=getattr(self.__class__, attribute),
            )

        if 'notify_on_errors' in parameters and get_publisher().logger.error_email:
            form.add(
                CheckboxWidget,
                '%snotify_on_errors' % prefix,
                title=_('Notify errors by email'),
                hint=_('Error traces will be sent to %s') % get_publisher().logger.error_email,
                value=self.notify_on_errors,
                tab=('error', _('Error Handling')),
            )

        if 'record_on_errors' in parameters:
            form.add(
                CheckboxWidget,
                '%srecord_on_errors' % prefix,
                title=_('Record errors in the central error screen, for management by administrators'),
                value=self.record_on_errors,
                tab=('error', _('Error Handling')),
                default_value=self.__class__.record_on_errors,
            )

        if 'record_errors' in parameters:
            form.add(
                CheckboxWidget,
                '%srecord_errors' % prefix,
                title=_('Record errors in card/form history log, for agents'),
                value=self.record_errors,
                tab=('error', _('Error Handling')),
            )

        if 'set_marker_on_status' in parameters:
            form.add(
                CheckboxWidget,
                '%sset_marker_on_status' % prefix,
                title=_('Set marker to jump back to current status'),
                value=self.set_marker_on_status,
                tab=('error', _('Error Handling')),
            )

    def _get_dict_parameter_view_value(self, value):
        if not value:
            return htmltext(pgettext('wscall-parameter', 'none'))
        r = TemplateIO(html=True)
        r += htmltext('<ul class="fields">')
        for key, value in sorted(value.items()):
            r += htmltext('<li>%s → %s</li>') % (key, value)
        r += htmltext('</ul>')
        return r.getvalue()

    def get_post_data_parameter_view_value(self):
        return self._get_dict_parameter_view_value(self.post_data)

    def get_qs_data_parameter_view_value(self):
        return self._get_dict_parameter_view_value(self.qs_data)

    def get_computed_strings(self):
        yield from super().get_computed_strings()
        yield self.url
        if self.qs_data:
            yield from self.qs_data.values()
        if self.post_data:
            yield from self.post_data.values()

    def get_static_strings(self):
        if self.qs_data:
            yield from self.qs_data.keys()
        if self.post_data:
            yield from self.post_data.keys()

    def perform(self, formdata):
        if not self.url:
            # misconfigured action
            return

        workflow_data = {}
        if self.varname:
            workflow_data['%s_time' % self.varname] = datetime.datetime.now().isoformat()

        try:
            response, status, data = call_webservice(
                url=self.url,
                qs_data=self.qs_data,
                request_signature_key=self.request_signature_key,
                method=self.method,
                post_data=self.post_data,
                post_formdata=self.post,
                formdata=formdata,
                handle_connection_errors=False,
                error_context_label=_('Webservice action'),
            )
        except ConnectionError as e:
            status = 0
            if self.varname:
                workflow_data['%s_connection_error' % self.varname] = str(e)
                formdata.update_workflow_data(workflow_data)
                formdata.store()
            self.action_on_error(self.action_on_network_errors, formdata, exception=e)
            return
        except PayloadError as e:
            if self.varname:
                workflow_data['%s_payload_error' % self.varname] = str(e)
                formdata.update_workflow_data(workflow_data)
                formdata.store()
            # always log errors from our side
            get_publisher().record_error(
                _('Invalid payload (%s)') % str(e),
                context=_('Webservice action'),
                formdata=formdata,
                status_item=self,
                exception=e,
            )
            return
        except UnflattenKeysException as e:
            get_publisher().record_error(
                error_summary=e.get_summary(),
                exception=e,
                context=_('Webservice action'),
                formdata=formdata,
                status_item=self,
                notify=self.notify_on_errors,
                record=self.record_on_errors,
            )
            return

        app_error_code = get_app_error_code(response, data, self.response_type)
        app_error_code_header = response.headers.get('x-error-code')

        if self.varname:
            workflow_data.update(
                {
                    '%s_status' % self.varname: status,
                    '%s_app_error_code' % self.varname: app_error_code,
                }
            )
            if app_error_code_header:
                workflow_data['%s_app_error_header' % self.varname] = app_error_code_header

        if self.varname and self.response_type == 'json':
            self.store_response_part(formdata, response)

        if status in (204, 205):
            pass  # not returning any content
        elif (status // 100) == 2 and app_error_code == 0:
            self.store_response(formdata, response, data, workflow_data)
        elif self.varname:  # on error, record data if it is JSON
            try:
                d = json.loads(force_str(data))
            except (ValueError, TypeError):
                pass
            else:
                workflow_data['%s_error_response' % self.varname] = d

        if workflow_data:
            formdata.update_workflow_data(workflow_data)

        formdata.store()

        recorded_error = False
        if app_error_code != 0:
            recorded_error = self.action_on_error(
                self.action_on_app_error, formdata, response, data=data, recorded_error=recorded_error
            )
        if (status // 100) == 4:
            self.action_on_error(
                self.action_on_4xx, formdata, response, data=data, recorded_error=recorded_error
            )
        if (status // 100) == 5:
            self.action_on_error(
                self.action_on_5xx, formdata, response, data=data, recorded_error=recorded_error
            )

    def get_attachment_data(self, response):
        filename = None
        content_type = response.headers.get('content-type') or ''
        if content_type:
            content_type = content_type.split(';')[0].strip().lower()
        extension = mimetypes.guess_extension(content_type, strict=False) or ''
        content_disposition = response.headers.get('content-disposition') or ''
        if 'filename=' in content_disposition:
            filename = content_disposition.split('filename=')[-1].strip()
            filename = [slugify(part) for part in filename.split('.')]
            filename = '.'.join([part for part in filename if part])
            if filename and extension and not filename.endswith(extension):
                filename += extension
        if not filename:
            if self.varname:
                filename = '%s%s' % (self.varname, extension)
            elif self.backoffice_filefield_id:
                filename = 'file-%s%s' % (self.backoffice_filefield_id, extension)
            else:
                filename = 'file%s' % extension
        return filename, content_type

    def store_response_part(self, formdata, response):
        if not self.store_all_responses:
            # remove previous responses from history
            for evo in formdata.evolution or []:
                parts_to_remove = []
                for part in evo.parts or []:
                    if (
                        not isinstance(part, WorkflowWsCallEvolutionPart)
                        or part.varname != self.varname
                        or isinstance(part, JournalWsCallErrorPart)
                    ):
                        continue
                    parts_to_remove.append(part)
                for part in parts_to_remove:
                    evo.parts.remove(part)
                if hasattr(evo, '_sql_id') and evo is not formdata.evolution[-1]:
                    formdata._store_all_evolution = True
        part = WorkflowWsCallEvolutionPart(
            varname=self.varname, url=response.request.url, status=response.status_code, data=response.content
        )
        formdata.evolution[-1].add_part(part)

    def store_response(self, formdata, response, data, workflow_data):
        if self.response_type == 'json' and self.varname:
            try:
                d = json.loads(force_str(data))
            except (ValueError, TypeError) as e:
                formdata.update_workflow_data(workflow_data)
                formdata.store()
                self.action_on_error(self.action_on_bad_data, formdata, response, data=data, exception=e)
            else:
                workflow_data['%s_response' % self.varname] = d
                if isinstance(d, dict) and self.method == 'POST':
                    # if POST response contains a display_id value it is
                    # considered to be used as replacement for the form
                    # own identifier; this is used so a unique public
                    # identifier can be used between w.c.s. and a business
                    # application.
                    if isinstance(d.get('data'), dict) and d['data'].get('display_id'):
                        formdata.id_display = d.get('data', {}).get('display_id')
                    elif d.get('display_id'):
                        formdata.id_display = d.get('display_id')
        elif self.response_type == 'attachment':
            # store result as attachment
            filename, content_type = self.get_attachment_data(response)
            if self.varname:
                workflow_data['%s_content_type' % self.varname] = content_type
                workflow_data['%s_length' % self.varname] = len(data)
            fp_content = io.BytesIO(data)
            attachment = AttachmentEvolutionPart(
                filename, fp_content, content_type=content_type, varname=self.varname
            )
            attachment.display_in_history = self.attach_file_to_history
            formdata.evolution[-1].add_part(attachment)

            if self.backoffice_filefield_id:
                self.store_in_backoffice_filefield(
                    formdata, self.backoffice_filefield_id, filename, content_type, data
                )
            add_clamd_scan_job(formdata)

    def action_on_error(
        self, action, formdata, response=None, data=None, exception=None, recorded_error=False
    ):
        # return True if an error has been recorded in history, False otherwise. And raises
        # AbortActionException if processing should be stopped.
        has_recorded_error = False
        if action in (':pass', ':stop') and (
            self.notify_on_errors or self.record_on_errors or self.record_errors
        ):
            if exception is None:
                summary = record_wscall_error(
                    response.status_code,
                    data,
                    response,
                    get_app_error_code(response, data, 'json'),
                    self.notify_on_errors,
                    self.record_on_errors,
                    error_context_label=_('Webservice action'),
                )
            else:
                exc_type, exc_value = sys.exc_info()[:2]
                summary = traceback.format_exception_only(exc_type, exc_value)[-1]
                get_publisher().record_error(
                    error_summary=summary,
                    exception=exception,
                    context=_('Webservice action'),
                    notify=self.notify_on_errors,
                    record=self.record_on_errors,
                )

            if self.record_errors and formdata.evolution and not recorded_error:
                url = response.request.url if response else None
                formdata.evolution[-1].add_part(
                    JournalWsCallErrorPart(
                        summary, varname=self.varname, url=url, label=self.label, data=data
                    )
                )
                formdata.store()
                has_recorded_error = True
        if action == ':pass':
            return has_recorded_error
        if action == ':stop':
            raise AbortActionException()

        # verify that target still exist
        try:
            self.get_workflow().get_status(action)
        except KeyError as e:
            get_publisher().record_error(
                'reference to invalid status %r in workflow %r, status %r'
                % (action, self.get_workflow().name, self.parent.name),
                exception=e,
                context=_('Webservice action'),
                notify=True,
            )
            raise AbortActionException()

        self.handle_markers_stack(formdata)
        formdata.status = 'wf-%s' % action
        formdata.store()
        raise AbortActionException()

    def get_target_status_url(self):
        # do not return anything as target status are accessory
        return None

    def get_target_status(self, formdata=None):
        # always return self status as a target so it's included in the
        # workflow visualisation as a "normal" action, in addition to
        # jumps related to error handling.
        targets = [self.parent]
        for attribute in (
            'action_on_app_error',
            'action_on_4xx',
            'action_on_5xx',
            'action_on_bad_data',
            'action_on_network_errors',
        ):
            value = getattr(self, attribute)
            if value in (':pass', ':stop'):
                continue
            try:
                target = self.get_workflow().get_status(value)
            except KeyError:
                if formdata:
                    # do not log when rendering the workflow diagram
                    message = _(
                        'reference to invalid status in workflow %(workflow)s, status %(status)s, item %(item)s'
                    ) % {
                        'workflow': self.get_workflow().name,
                        'status': self.parent.name,
                        'item': self.description,
                    }
                    get_publisher().record_error(message, workflow=self.get_workflow())
                continue
            targets.append(target)
        return targets

    def get_jump_label(self, target_id):
        if target_id == self.parent.id:
            if self.label:
                return _('Webservice "%s"') % self.label
            return _('Webservice')
        if self.label:
            return _('Error calling webservice "%s"') % self.label
        return _('Error calling webservice')

    def perform_in_tests(self, formdata):
        if self.response_type != 'json':
            return

        self.perform(formdata)

    def _kv_data_export_to_xml(self, xml_item, include_id, attribute):
        assert attribute
        if not getattr(self, attribute):
            return
        el = ET.SubElement(xml_item, attribute)
        for key, value in getattr(self, attribute).items():
            item = ET.SubElement(el, 'item')
            if isinstance(key, str):
                ET.SubElement(item, 'name').text = force_str(key)
            else:
                raise AssertionError('unknown type for key (%r)' % key)
            if isinstance(value, str):
                ET.SubElement(item, 'value').text = force_str(value)
            else:
                raise AssertionError('unknown type for value (%r)' % key)

    def _kv_data_init_with_xml(self, elem, include_id, attribute):
        if elem is None:
            return
        setattr(self, attribute, {})
        for item in elem.findall('item'):
            key = force_str(item.find('name').text)
            value = force_str(item.find('value').text or '')
            getattr(self, attribute)[key] = value

    def post_data_export_to_xml(self, xml_item, include_id=False):
        self._kv_data_export_to_xml(xml_item, include_id=include_id, attribute='post_data')

    def post_data_init_with_xml(self, elem, include_id=False, snapshot=False):
        self._kv_data_init_with_xml(elem, include_id=include_id, attribute='post_data')

    def qs_data_export_to_xml(self, xml_item, include_id=False):
        self._kv_data_export_to_xml(xml_item, include_id=include_id, attribute='qs_data')

    def qs_data_init_with_xml(self, elem, include_id=False, snapshot=False):
        self._kv_data_init_with_xml(elem, include_id=include_id, attribute='qs_data')


register_item_class(WebserviceCallStatusItem)


class LazyFormDataWsCallsBase:
    def __init__(self, formdata):
        self._formdata = formdata

    def __getattr__(self, varname):
        parts = []
        for part in self._formdata.iter_evolution_parts(WorkflowWsCallEvolutionPart):
            if part.varname == varname and not isinstance(part, JournalWsCallErrorPart):
                parts.append(LazyFormDataWsCall(part))
        if parts:
            return LazyFormDataWsCalls(parts)
        raise AttributeError(varname)

    def inspect_keys(self):
        varnames = set()
        for part in self._formdata.iter_evolution_parts(WorkflowWsCallEvolutionPart):
            if part.varname and not isinstance(part, JournalWsCallErrorPart):
                varnames.add(part.varname)
        yield from varnames


class LazyFormDataWsCalls:
    def __init__(self, parts):
        self._parts = parts

    def inspect_keys(self):
        keys = self._parts[-1].inspect_keys()
        if len(self._parts) > 1:
            # if multiple responses with same varname have been stored, advertise
            # access via indices.
            keys.extend([str(x) for x in range(len(self._parts))])
        return keys

    def __getitem__(self, key):
        try:
            key = int(key)
        except ValueError:
            try:
                return getattr(self, key)
            except AttributeError:
                return self._parts[-1][key]
        return self._parts[key]

    def __len__(self):
        return len(self._parts)

    def __iter__(self):
        yield from self._parts


class LazyFormDataWsCall:
    def __init__(self, part):
        self.part = part

    def inspect_keys(self):
        return ['datetime', 'success', 'response', 'status', 'url']

    def __getitem__(self, key):
        try:
            return getattr(self, key)
        except AttributeError:
            raise KeyError(key)

    @property
    def datetime(self):
        return self.part.datetime.astimezone()

    @property
    def response(self):
        try:
            return json.loads(force_str(self.part.data))
        except ValueError:
            return '<invalid>'

    @property
    def status(self):
        return self.part.status

    @property
    def success(self):
        return 200 <= self.part.status < 300

    @property
    def url(self):
        return self.part.url
