# w.c.s. - web application for online forms
# Copyright (C) 2005-2017  Entr'ouvert
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
import re

from django.utils.safestring import mark_safe
from django.utils.text import Truncator
from django.utils.timezone import localtime
from quixote import get_publisher, get_request, get_response, get_session, redirect
from quixote.directory import AccessControlled, Directory
from quixote.html import TemplateIO, htmltext

from wcs.admin.documentable import DocumentableMixin
from wcs.backoffice.pagination import pagination_links
from wcs.logged_errors import LoggedError
from wcs.qommon import N_, _, errors, misc, ngettext, template
from wcs.qommon.form import CheckboxesWidget, DateWidget, Form
from wcs.sql_criterias import And, Equal, ILike, Less, Null, Or


class ErrorFrame:
    def __init__(self, context):
        self.context = context or {}

    def source(self):
        if self.context.get('source_url'):
            return {
                'url': self.context.get('source_url'),
                'label': self.context.get('source_label'),
            }
        return None

    def get_frame_lines(self):
        for key, value in self.context.items():
            if key in ('source_url', 'source_label', 'field_url'):
                continue
            key_label = {
                'anchor_date': _('Anchor date'),
                'condition': _('Condition'),
                'condition_type': _('Condition type'),
                'template': _('Template'),
                'status': _('HTTP Status'),
                'response_data': _('HTTP Response'),
                'user': _('User'),
                'user_uuid': _('User UUID'),
                'duration': _('Duration (in seconds)'),
                'process_duration': _('CPU duration (in seconds)'),
                'field_label': _('Field'),
            }.get(key, key)
            if key == 'field_label':
                value = mark_safe(htmltext('<a href="%s">%s</a>') % (self.context.get('field_url'), value))
            yield {'label': key_label, 'value': value}


class LoggedErrorDirectory(Directory, DocumentableMixin):
    _q_exports = ['', 'delete', ('update-documentation', 'update_documentation')]
    do_not_call_in_templates = True

    def __init__(self, parent_dir, error):
        self.parent_dir = parent_dir
        self.error = error
        self.documented_object = self.error
        self.documented_element = self.error

    def _q_index(self):
        get_response().breadcrumb.append(('%s/' % self.error.id, self.error.summary))
        get_response().set_title(_('Logged Errors - %s') % self.error.summary)

        return template.QommonTemplateResponse(
            templates=['wcs/backoffice/logged-error.html'],
            context={
                'view': self,
                'error': self.error,
                'formdef': self.error.get_formdef(),
                'workflow': self.error.get_workflow(),
                'status': self.error.get_status(),
                'status_item': self.error.get_status_item(),
                'formdata': self.error.get_formdata(),
                'tabs': self.get_tabs(),
                'has_sidebar': True,
            },
            is_django_native=True,
        )

    def error_expression_type_label(self):
        return {
            'django': _('Django Expression'),
            'template': _('Template'),
            'text': _('Text'),
        }.get(self.error.expression_type, _('Unknown'))

    def get_context_frames(self):
        for frame_context in reversed(self.error.context.get('stack') or []):
            yield ErrorFrame(frame_context)

    def get_tabs(self):
        r = TemplateIO(html=True)
        parts = (
            ('exception', N_('Exception')),
            ('stack-trace', N_('Stack trace (most recent call first)')),
            ('form', N_('Form')),
            ('request-cookies', N_('Cookies')),
            ('environment', N_('Environment')),
        )
        parts_labels = [x[1] for x in parts]
        current_part = None
        tabs = []
        re_trace_line = re.compile(r'"(.*)?(/wcs)')
        for line in self.error.traceback.splitlines():
            if line.endswith(':') and line.rstrip(':') in parts_labels:
                if current_part in parts_labels[:2]:
                    r += htmltext('</pre>')
                elif current_part:
                    r += htmltext('</table>')

                current_part = line.rstrip(':')
                part_slug = [x[0] for x in parts if x[1] == current_part][0]
                if not (part_slug == 'stack-trace' and tabs):
                    r = TemplateIO(html=True)
                    tabs.append(
                        {
                            'slug': part_slug,
                            'label': _(current_part),
                            'content': r,
                        }
                    )
                    if part_slug == 'stack-trace':
                        tabs[-1]['label'] = _('Stack trace')
                if current_part in parts_labels[:2]:
                    r += htmltext('<pre class="traceback %s">' % part_slug)
                else:
                    r += htmltext('<table class="main compact code">')
                continue
            if current_part in parts_labels[:2]:
                if line.startswith('  File'):
                    if line.endswith('/handlers/base.py", line 181, in _get_response'):
                        break
                    r += htmltext('<div class="stack-trace--location">%s</div>') % re_trace_line.sub(
                        r'"...\2', line
                    )
                elif line.startswith('>'):
                    r += htmltext('<div class="stack-trace--code">%s</div>') % line
                else:
                    r += line + '\n'
            elif line:
                r += htmltext('<tr><td>%s</td><td>%s</td></tr>') % tuple(re.split(r'\s+', line, maxsplit=1))

        if current_part in parts_labels[:2]:
            r += htmltext('</pre>')
        elif current_part:
            r += htmltext('</table>')

        if self.error.context and 'timings' in self.error.context:
            r = TemplateIO(html=True)
            r += htmltext('<table class="main compact">')
            r += htmltext(
                '<thead><tr><th></th><th class="time">%s</th><th class="time">%s</th></tr></thead>'
            ) % (_('Start'), _('Duration'))
            r += htmltext('<tbody>')

            timings = self.error.context.get('timings')
            start = timings[0].get('start', timings[0].get('timstamp'))

            def show(timings, depth=0):
                nonlocal r

                for timing in timings:
                    r += htmltext('<tr><td>')
                    r += htmltext('<span class="indent"></span>' * depth)
                    url = timing.get('context', {}).get('url')
                    if url:
                        r += htmltext('<a href="%s">') % url
                    r += htmltext('%s') % (timing.get('name') or timing.get('mark'))
                    if url:
                        r += htmltext('</a">')
                    timestamp = timing.get('timestamp', timing.get('start'))
                    r += htmltext('</td><td class="time">%.3f</td><td class="time">%.3f</td></tr>') % (
                        timestamp - start,
                        timing['duration'],
                    )
                    show(timing.get('timings', []), depth=depth + 1)

            show(timings)
            r += htmltext('</tbody></table>')
            tabs.insert(
                0,
                {
                    'slug': 'timings',
                    'label': _('Timings'),
                    'content': r,
                },
            )

        for tab in tabs:
            # do not pass TemplateIO to django template
            tab['content'] = tab['content'].getvalue()

        return tabs

    def delete(self):
        self.error.deleted_timestamp = localtime()
        self.error.store()
        return redirect('..')


class LoggedErrorsDirectory(AccessControlled, Directory):
    _q_exports = ['', 'cleanup']

    @classmethod
    def get_errors(cls, offset, limit, formdef_class=None, formdef_id=None, workflow_id=None, q=None):
        errors = []

        select_kwargs = {
            'order_by': '-latest_occurence_timestamp',
            'limit': limit,
            'offset': offset,
        }
        clauses = []

        if formdef_id and formdef_class:
            clauses = [Equal('formdef_id', str(formdef_id)), Equal('formdef_class', formdef_class.__name__)]
        elif workflow_id:
            clauses = [Equal('workflow_id', str(workflow_id))]
        else:
            clauses = LoggedError.get_permission_criterias()

        if q:
            clauses.append(
                And(
                    [
                        Or(
                            [
                                ILike(attr, x)
                                for attr in ('summary', 'expression', 'exception_class', 'exception_message')
                            ]
                        )
                        for x in q.split()
                    ]
                )
            )

        clauses.append(Null('deleted_timestamp'))

        errors = LoggedError.select(clause=clauses, **select_kwargs)
        count = LoggedError.count(clauses)

        return list(errors), count

    @classmethod
    def errors_block(cls, formdef_class=None, formdef_id=None, workflow_id=None):
        # select 3 + 1 last errors
        errors, total = cls.get_errors(
            offset=0, limit=4, formdef_class=formdef_class, formdef_id=formdef_id, workflow_id=workflow_id
        )
        if not errors:
            return ''

        r = TemplateIO(html=True)
        r += htmltext('<div class="bo-block logged-errors">')
        r += (
            htmltext('<h3><a href="logged-errors/">%s</a></h3>')
            % ngettext('%(count)d error', '%(count)d errors', total)
            % {'count': total}
        )
        r += htmltext('<ul>')
        for error in errors[:3]:
            r += htmltext('<li><a href="logged-errors/%s/">%s</a> ') % (error.id, error.wbr_summary)
            if error.exception_class or error.exception_message:
                message = _('error %(class)s (%(message)s)') % {
                    'class': error.exception_class,
                    'message': error.exception_message,
                }
                message = Truncator(message).chars(80, truncate='…')
                r += htmltext(message)
            r += htmltext('</li>')
        if len(errors) > 3:
            r += htmltext('<li>...</li>')
        r += htmltext('</ul>')
        r += htmltext('</div>')
        return r.getvalue()

    def __init__(self, parent_dir, formdef_class=None, formdef_id=None, workflow_id=None):
        self.parent_dir = parent_dir
        self.formdef_class = formdef_class
        self.formdef_id = formdef_id
        self.workflow_id = workflow_id

    def _q_access(self):
        backoffice_root = get_publisher().get_backoffice_root()
        if not (
            backoffice_root.is_accessible('forms')
            or backoffice_root.is_accessible('cards')
            or backoffice_root.is_accessible('workflows')
        ):
            raise errors.AccessForbiddenError()

    def _q_index(self):
        get_response().breadcrumb.append(('logged-errors/', _('Logged Errors')))
        get_response().set_title(_('Logged Errors'))
        get_session().latest_errors_visit = localtime()
        limit = misc.get_int_or_400(
            get_request().form.get('limit', get_publisher().get_site_option('default-page-size')) or 20
        )
        offset = misc.get_int_or_400(get_request().form.get('offset', 0))
        logged_errors, total_count = self.get_errors(
            offset=offset,
            limit=limit,
            formdef_class=self.formdef_class,
            formdef_id=self.formdef_id,
            workflow_id=self.workflow_id,
            q=get_request().form.get('q'),
        )
        links = ''
        links = pagination_links(offset, limit, total_count, load_js=False)
        return template.QommonTemplateResponse(
            templates=['wcs/backoffice/logged-errors.html'],
            context={
                'errors': logged_errors,
                'pagination_links': links,
                'q': get_request().form.get('q'),
            },
        )

    def cleanup(self):
        backoffice_root = get_publisher().get_backoffice_root()
        form = Form(enctype='multipart/form-data')
        options = []
        if backoffice_root.is_accessible('forms'):
            options.append(('formdef', _('Forms'), 'formdef'))
        if backoffice_root.is_accessible('cards'):
            options.append(('carddef', _('Card Models'), 'carddef'))
        if backoffice_root.is_accessible('workflows'):
            options.append(('others', _('Others'), 'others'))
        if not (self.formdef_id or self.workflow_id):
            form.add(
                CheckboxesWidget,
                'types',
                title=_('Error types'),
                value=[x[0] for x in options],  # check all by default
                options=options,
                required=True,
            )
        form.add(
            DateWidget,
            'latest_occurence',
            title=_('Latest occurence'),
            value=datetime.date.today() - datetime.timedelta(days=180),
            required=True,
        )
        form.add_submit('submit', _('Submit'))
        form.add_submit('cancel', _('Cancel'))

        if form.get_widget('cancel').parse():
            return redirect('.')

        if form.get_submit() == 'submit' and not form.has_errors():
            criterias = []

            if self.formdef_id and self.formdef_class:
                criterias.append(Equal('formdef_id', str(self.formdef_id)))
                criterias.append(Equal('formdef_class', self.formdef_class.__name__))
            elif self.workflow_id:
                criterias.append(Equal('workflow_id', str(self.workflow_id)))
            else:
                if 'formdef' in form.get_widget('types').parse():
                    criterias.append(Equal('formdef_class', 'FormDef'))
                if 'carddef' in form.get_widget('types').parse():
                    criterias.append(Equal('formdef_class', 'CardDef'))
                if 'others' in form.get_widget('types').parse():
                    criterias.append(Null('formdef_class'))
                criterias = [Or(criterias)]
            criterias.append(
                Less(
                    'latest_occurence_timestamp',
                    misc.get_as_datetime(form.get_widget('latest_occurence').parse()),
                )
            )
            LoggedError.mark_for_deletion(clause=criterias)
            return redirect('.')

        get_response().set_title(_('Cleanup'))
        r = TemplateIO(html=True)
        r += htmltext('<h2>%s</h2>') % _('Cleanup')
        r += form.render()
        return r.getvalue()

    def _q_lookup(self, component):
        try:
            error = LoggedError.get(component)
        except KeyError:
            raise errors.TraversalError()
        get_response().breadcrumb.append(('logged-errors/', _('Logged Errors')))
        return LoggedErrorDirectory(self.parent_dir, error)
