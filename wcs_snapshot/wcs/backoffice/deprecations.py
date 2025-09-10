# w.c.s. - web application for online forms
# Copyright (C) 2005-2022  Entr'ouvert
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

import datetime
import json
import os
import re

from quixote import get_publisher, get_request, get_response, redirect
from quixote.directory import Directory

from wcs import deprecations
from wcs.blocks import BlockDef
from wcs.data_sources import NamedDataSource
from wcs.formdef_base import FormDefBase, get_formdefs_of_all_kinds
from wcs.mail_templates import MailTemplate
from wcs.portfolio import has_portfolio
from wcs.qommon import _, ezt, template
from wcs.qommon.afterjobs import AfterJob
from wcs.wf.export_to_model import UploadValidationError
from wcs.workflows import Workflow
from wcs.wscalls import NamedWsCall


class DeprecatedElementsDetected(Exception):
    pass


class DeprecationsDirectory(Directory):
    do_not_call_in_templates = True
    _q_exports = ['', 'scan']
    metadata = deprecations.DEPRECATIONS_METADATA

    def get_deprecations(self, source):
        report_path = deprecations.get_report_path()
        if not os.path.exists(report_path):
            return []
        with open(report_path) as fd:
            report = json.load(fd)
        report['report_lines'] = [x for x in report['report_lines'] if x.get('source') == source]
        report['report_lines'].sort(key=lambda x: x['category'])
        return report

    def _q_index(self):
        report_path = deprecations.get_report_path()
        if not os.path.exists(report_path):
            # create report if necessary
            return self.scan()

        get_response().set_title(_('Deprecations Report'))
        get_response().breadcrumb.append(('deprecations/', _('Deprecations Report')))

        context = {'has_sidebar': False, 'view': self}
        with open(report_path) as fd:
            context['report'] = json.load(fd)
        context['report']['report_lines'].sort(key=lambda x: x['category'])
        return template.QommonTemplateResponse(
            templates=['wcs/backoffice/deprecations.html'], context=context, is_django_native=True
        )

    def scan(self):
        job = get_publisher().add_after_job(
            DeprecationsScan(
                label=_('Scanning for deprecations'),
                user_id=get_request().user.id,
                return_url='/backoffice/studio/deprecations/',
            )
        )
        job.store()
        return redirect(job.get_processing_url())


class DeprecationsScan(AfterJob):
    def done_action_url(self):
        return self.kwargs['return_url']

    def done_action_label(self):
        return _('Go to deprecation report')

    def done_button_attributes(self):
        return {'data-redirect-auto': 'true'}

    def execute(self):
        self.report_lines = []
        formdefs = get_formdefs_of_all_kinds()
        workflows = Workflow.select(ignore_errors=True, ignore_migration=True)
        named_data_sources = NamedDataSource.select(ignore_errors=True, ignore_migration=True)
        named_ws_calls = NamedWsCall.select(ignore_errors=True, ignore_migration=True)
        mail_templates = MailTemplate.select(ignore_errors=True, ignore_migration=True)
        # extra step to build report file
        self.total_count = (
            len(formdefs)
            + len(workflows)
            + len(named_data_sources)
            + len(named_ws_calls)
            + len(mail_templates)
            + 1
        )
        self.store()

        self.check_objects(formdefs + workflows + named_data_sources + named_ws_calls + mail_templates)

        self.build_report_file()
        self.increment_count()

    def check_objects(self, objects):
        for obj in objects:
            if isinstance(obj, (FormDefBase, BlockDef)):
                self.check_formdef(obj)
            elif isinstance(obj, Workflow):
                self.check_workflow(obj)
            elif isinstance(obj, NamedDataSource):
                self.check_named_data_source(obj)
            elif isinstance(obj, NamedWsCall):
                self.check_named_ws_call(obj)
            elif isinstance(obj, MailTemplate):
                self.check_mail_template(obj)
            self.increment_count()

    def check_data_source(self, data_source, location_label, url, source):
        if not data_source:
            return
        if data_source.get('type') == 'jsonp':
            self.add_report_line(
                location_label=location_label,
                url=url,
                category='jsonp',
                source=source,
            )
        if data_source.get('type') == 'json':
            self.check_string(data_source.get('value'), location_label, url, source=source)
            self.check_remote_call_url(data_source.get('value'), location_label, url, source=source)

    def check_string(self, string, location_label, url, source):
        if not isinstance(string, str):
            return
        if template.Template(string).format == 'ezt':
            try:
                ezt.Template().parse(string)
            except ezt.EZTException:
                pass
            else:
                if not re.match(r'\[[^]]*[A-Z][^]]*\]', string):
                    # don't warn on leading [] expression if it has uppercases,
                    # this typically happens as initial "tag" in an email subjet.
                    self.add_report_line(
                        location_label=location_label, url=url, category='ezt', source=source
                    )

    def check_remote_call_url(self, wscall_url, location_label, url, source):
        if 'csvdatasource/' in (wscall_url or ''):
            self.add_report_line(
                location_label=location_label, url=url, category='csv-connector', source=source
            )
        if 'jsondatastore/' in (wscall_url or ''):
            self.add_report_line(
                location_label=location_label, url=url, category='json-data-store', source=source
            )

    def check_formdef(self, formdef):
        if formdef.id:
            source = f'{formdef.xml_root_node}:{formdef.id}' if formdef.id else ''
        elif hasattr(formdef, 'get_workflow') and formdef.get_workflow():
            source = f'workflow:{formdef.get_workflow().id}'
        else:
            source = '-'
        for field in formdef.fields or []:
            location_label = _('%(name)s / Field "%(label)s"') % {
                'name': formdef.name,
                'label': field.ellipsized_label,
            }
            url = formdef.get_field_admin_url(field)
            self.check_data_source(
                getattr(field, 'data_source', None),
                location_label=location_label,
                url=url,
                source=source,
            )
            prefill = getattr(field, 'prefill', None)
            if prefill:
                self.check_string(
                    prefill.get('value'),
                    location_label=location_label,
                    url=url,
                    source=source,
                )
            if field.key in ('title', 'subtitle', 'comment'):
                self.check_string(
                    field.label,
                    location_label=location_label,
                    url=url,
                    source=source,
                )
            if field.key in ('table', 'table-select', 'tablerows', 'ranked-items'):
                self.add_report_line(
                    location_label=location_label,
                    url=url,
                    category='fields',
                    source=source,
                )
            if has_portfolio() and field.key == 'file' and getattr(field, 'allow_portfolio_picking', False):
                self.add_report_line(location_label=location_label, url=url, category='fargo', source=source)

        if source != '-' and len(formdef.fields or []) > formdef.fields_count_total_hard_limit:
            self.add_report_line(
                location_label=formdef.name,
                url=formdef.get_admin_url(),
                category='field-limits',
                source=source,
            )

    def check_workflow(self, workflow):
        source = f'workflow:{workflow.id}'

        wf_form_identifiers = set()
        if not get_publisher().has_site_option('disable-workflow-form-to-workflow-data'):
            for action in workflow.get_all_items():
                if action.key == 'form' and action.varname:
                    wf_form_identifiers.add(action.varname)

        for action in workflow.get_all_items():
            location_label = '%s / %s' % (workflow.name, action.description)
            url = action.get_admin_url()
            for string in action.get_computed_strings():
                self.check_string(string, location_label=location_label, url=url, source=source)
                if wf_form_identifiers:
                    if re.findall(
                        r'\b(?:form_workflow_data_|)?(?:%s)[_\.]var[_\.]' % '|'.join(wf_form_identifiers),
                        str(string),
                    ):
                        self.add_report_line(
                            location_label=location_label,
                            url=url,
                            category='legacy_wf_form_variables',
                            source=source,
                        )

            if action.key == 'export_to_model':
                try:
                    kind = action.model_file_validation(action.model_file, allow_rtf=True)
                except UploadValidationError:
                    pass
                else:
                    if kind == 'rtf':
                        self.add_report_line(
                            location_label=location_label, url=url, category='rtf', source=source
                        )
            if action.key in ('aggregationemail', 'resubmit'):
                self.add_report_line(
                    location_label=location_label, url=url, category=f'action-{action.key}', source=source
                )
            if action.key == 'webservice_call':
                self.check_remote_call_url(action.url, location_label=location_label, url=url, source=source)
            if (
                has_portfolio()
                and action.key in ('addattachment', 'export_to_model')
                and getattr(action, 'push_to_portfolio', False)
            ):
                self.add_report_line(
                    location_label=location_label,
                    url=url,
                    category='fargo',
                    source=source,
                )

    def check_named_data_source(self, named_data_source):
        source = f'datasource:{named_data_source.id}'
        location_label = _('%(title)s "%(name)s"') % {
            'title': _('Data source'),
            'name': named_data_source.name,
        }
        url = named_data_source.get_admin_url()

        self.check_data_source(
            getattr(named_data_source, 'data_source', None),
            location_label=location_label,
            url=url,
            source=source,
        )

    def check_named_ws_call(self, named_ws_call):
        source = f'wscall:{named_ws_call.id}'
        location_label = _('%(title)s "%(name)s"') % {
            'title': _('Webservice'),
            'name': named_ws_call.name,
        }
        url = named_ws_call.get_admin_url()
        for string in named_ws_call.get_computed_strings():
            self.check_string(string, location_label=location_label, url=url, source=source)
        if named_ws_call.request and named_ws_call.request.get('url'):
            self.check_remote_call_url(
                named_ws_call.request['url'], location_label=location_label, url=url, source=source
            )

    def check_mail_template(self, mail_template):
        source = f'mail_template:{mail_template.id}'
        location_label = _('%(title)s "%(name)s"') % {
            'title': _('Mail Template'),
            'name': mail_template.name,
        }
        url = mail_template.get_admin_url()
        for string in mail_template.get_computed_strings():
            self.check_string(string, location_label=location_label, url=url, source=source)

    def add_report_line(self, **kwargs):
        if kwargs not in self.report_lines:
            self.report_lines.append(kwargs)

    def build_report_file(self):
        with open(deprecations.get_report_path(), 'w') as fd:
            json.dump(
                {
                    'now': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    'report_lines': self.report_lines,
                },
                fd,
                indent=2,
            )

    def check_deprecated_elements_in_object(self, obj):
        return  # nothing is forbidden


class DeprecationsScanAfterJob(DeprecationsScan):
    pass  # legacy name, to load old pickle files
