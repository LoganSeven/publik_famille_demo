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

from django.utils.timezone import localtime
from quixote import get_publisher
from quixote.html import TemplateIO, htmltext

from wcs import sql
from wcs.qommon import _


class WorkflowTrace(sql.WorkflowTrace):
    def store(self, *args, **kwargs):
        super().store(*args, **kwargs)
        job = getattr(get_publisher(), 'current_cron_job', None)
        if job:
            job.log_debug(
                f'stored trace ({self.id}), {self.formdef_type}/{self.formdef_id}-{self.formdata_id}, '
                f'event: {self.event or "-"}, action: {self.action_item_key or "-"}'
            )

    def get_event_label(self):
        return {
            'aborted-too-many-jumps': _('Aborted (too many jumps)'),
            'api-created': _('Created (by API)'),
            'api-post-edit-action': _('Actions after edit action (by API)'),
            'api-trigger': _('API Trigger'),
            'backoffice-created': _('Created (backoffice submission)'),
            'button': _('Action button'),
            'continuation': _('Continuation'),
            'csv-import-created': _('Created (by CSV import)'),
            'csv-import-updated': _('Updated (by CSV import)'),
            'edit-action': _('Actions after edit action'),
            'email-button': _('Email action button'),
            'frontoffice-created': _('Created (frontoffice submission)'),
            'global-action-button': _('Click on a global action button'),
            'global-action': _('Global action'),
            'global-action-mass': _('Mass global action'),
            'global-action-timeout': _('Global action timeout'),
            'global-api-trigger': _('API Trigger'),
            'global-interactive-action': _('Global action (interactive)'),
            'global-external-workflow': _('Trigger by external workflow'),
            'json-import-created': _('Created (by JSON import)'),
            'json-import-updated': _('Updated (by JSON import)'),
            'loop-start': _('Start of the loop'),
            'loop-end': _('End of the loop'),
            'mass-jump': _('Mass jump action'),
            'timeout-jump': _('Timeout jump'),
            'unstall': _('Unblock stalled processing'),
            'workflow-created': _('Created (by workflow action)'),
            'workflow-edited': _('Edited (by workflow action)'),
            'workflow-created-formdata': _('Created form'),
            'workflow-created-carddata': _('Created card'),
            'workflow-edited-carddata': _('Edited card'),
            'workflow-form-submit': _('Action in workflow form'),  # legacy
        }.get(self.event, self.event)

    def is_global_event(self):
        return bool(self.event and self.event.startswith('global-'))

    @property
    def external_workflow(self):
        if not hasattr(self, '_external_workflow'):
            self._external_workflow = None
            if self.event_args.get('external_workflow_id'):
                from wcs.workflows import Workflow

                self._external_workflow = Workflow.get(self.event_args.get('external_workflow_id'))
        return self._external_workflow

    @property
    def formdef(self):
        from wcs.carddef import CardDef
        from wcs.formdef import FormDef

        formdef_class = FormDef
        if 'carddata' in self.event:
            formdef_class = CardDef

        return formdef_class.cached_get(self.event_args.get('external_formdef_id'), ignore_errors=True)

    @property
    def formdata(self):
        if not self.formdef:
            return None
        if not hasattr(self, '_formdata'):
            self._formdata = self.formdef.data_class().get(
                self.event_args.get('external_formdata_id'), ignore_errors=True
            )
        return self._formdata

    def get_external_url(self, global_event):
        try:
            if self.event_args.get('external_status_id'):
                return '%sitems/%s/' % (
                    self.get_base_url(
                        self.external_workflow,
                        self.event_args.get('external_status_id'),
                        global_event,
                    ),
                    self.event_args.get('external_item_id'),
                )
            if self.event_args.get('global_action_id'):
                return self.get_base_url(self.external_workflow, global_event=global_event)

        except KeyError:
            return '#missing-%s' % self.event_args.get('external_item_id')

    def get_external_formdata_url(self, global_event):
        if self.formdef is None:
            return '#missing-formdef-%s' % self.event_args.get('external_formdef_id')

        if self.formdata is None:
            return '#missing-formdata-%s' % self.event_args.get('external_formdata_id')

        return self.formdata.get_backoffice_url()

    def get_base_url(self, workflow, status_id=None, global_event=None):
        if global_event:
            if not global_event.event_args:
                raise KeyError()
            return '%sglobal-actions/%s/' % (
                workflow.get_admin_url(),
                global_event.event_args.get('global_action_id'),
            )
        status = workflow.get_status(status_id)
        return status.get_admin_url()

    def get_real_action(self, workflow, status_id, action_id, global_event=None):
        if global_event:
            if not global_event.event_args:
                return None
            global_action_id = global_event.event_args.get('global_action_id')
            try:
                global_action = [x for x in workflow.global_actions if x.id == global_action_id][0]
            except IndexError:
                return None
            items = global_action.items
        else:
            try:
                status = workflow.get_status(status_id)
            except KeyError:
                return None
            items = status.items
        try:
            real_action = [x for x in items if x.id == action_id][0]
        except IndexError:
            real_action = None
        return real_action

    def print_event(self, formdata, global_event):
        event_item = TemplateIO(html=True)
        event_item += htmltext(
            '<li><span class="event-datetime">%s</span>'
            % localtime(self.timestamp).strftime('%Y-%m-%d %H:%M:%S')
        )
        if (
            self.event_args
            and self.event_args.get('external_workflow_id')
            and (
                (self.event_args.get('external_status_id') and self.event_args.get('external_item_id'))
                or self.event_args.get('global_action_id')
            )
        ):
            event_item += htmltext('<span class="event"><a href="%s">%s</a></span>') % (
                self.get_external_url(global_event),
                self.get_event_label(),
            )
        elif (
            self.event_args
            and self.event_args.get('external_formdef_id')
            and self.event_args.get('external_formdata_id')
        ):
            event_item += htmltext('<span class="event"><a href="%s">%s - %s</a></span>') % (
                self.get_external_formdata_url(global_event),
                self.get_event_label(),
                self.formdata.get_display_name() if self.formdata else _('deleted'),
            )
        elif (
            self.event_args and self.event_args.get('global_action_id') and self.event_args.get('trigger_id')
        ):
            event_item += htmltext('<span class="event"><a href="%s#trigger-%s">%s</a></span>') % (
                self.get_base_url(formdata.formdef.workflow, None, global_event=global_event),
                self.event_args.get('trigger_id'),
                self.get_event_label(),
            )
        elif self.event_args and self.event_args.get('action_item_id'):
            try:
                url = '%sitems/%s/' % (
                    self.get_base_url(formdata.formdef.workflow, self.status_id),
                    self.event_args.get('action_item_id'),
                )
            except KeyError:
                url = '#missing-%s' % self.event_args['action_item_id']
            label = self.get_event_label()
            real_action = self.get_real_action(
                formdata.formdef.workflow,
                self.status_id,
                self.event_args['action_item_id'],
            )
            if real_action and hasattr(real_action, 'render_as_short_line'):
                label += ' - %s' % real_action.render_as_short_line()
            elif real_action and hasattr(real_action, 'render_as_line'):
                label += ' - %s' % real_action.render_as_line()
            event_item += htmltext('<span class="event"><a href="%s">%s</a></span>') % (
                url,
                label,
            )
        elif self.event == 'workflow-edited-carddata':
            # it would usually have external_formdef_id/external_formdata_id and be handled
            # earlier; this matches the case when no targetted card could be found.
            event_item += htmltext('<span class="event-error">%s</span>') % _('Nothing edited')
        elif self.event in ('global-api-trigger', 'global-action-mass', 'global-interactive-action'):
            global_action_id = self.event_args.get('global_action_id')
            event_item += htmltext('<span class="event">%s</span>') % self.get_event_label()
            if global_action_id:
                global_actions = [
                    x for x in formdata.formdef.workflow.global_actions if x.id == global_action_id
                ]
                if global_actions:
                    event_item += htmltext(
                        '<a class="tracing-link event--global-action" href="%s">%s</a>'
                    ) % (
                        global_actions[0].get_admin_url(),
                        global_actions[0].name,
                    )
        elif self.event == 'continuation':
            # do not include timestamps for continuation lines
            event_item = TemplateIO(html=True)
            event_item += htmltext('<li><span class="event">%s</span>') % _('Continuation')
        else:
            event_item += htmltext('<span class="event">%s</span>') % self.get_event_label()
        event_item += htmltext('</li>')
        return event_item.getvalue()

    def print_action(self, action_classes, filled, global_event):
        action_label = action_classes.get(self.action_item_key, self.action_item_key)
        try:
            url = '%sitems/%s/' % (
                self.get_base_url(filled.formdef.workflow, self.status_id, global_event),
                self.action_item_id,
            )
        except KeyError:
            url = '#missing-%s' % self.action_item_id
        r = '<li><span class="datetime">%s</span> <a class="tracing-link" href="%s">%s</a>' % (
            localtime(self.timestamp).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3],
            url,
            action_label,
        )
        real_action = self.get_real_action(
            filled.formdef.workflow,
            self.status_id,
            self.action_item_id,
            global_event,
        )
        if real_action:
            details = real_action.get_inspect_details()
            if details:
                r += ' <span class="tracing-details">(%s)</span>' % details
        r += '</li>'
        return r

    def print_status(self, filled):
        try:
            status = filled.formdef.workflow.get_status(self.status_id)
            status_label = status.name
            status_admin_base_url = status.get_admin_url()
        except KeyError:
            status_label = _('Unavailable status (%s)') % self.status_id
            status_admin_base_url = '#missing'
        return (
            '<li><span class="datetime">%s</span> '
            '<a class="tracing-link" href="%s"><strong>%s</strong></a></li>'
        ) % (
            localtime(self.timestamp).strftime('%Y-%m-%d %H:%M:%S'),
            status_admin_base_url,
            status_label,
        )


class TestWorkflowTrace(WorkflowTrace):
    _table_name = WorkflowTrace._test_table_name
