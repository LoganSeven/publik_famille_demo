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

import contextlib
import datetime
import itertools
import json
import math
import os

from django.utils.timezone import localtime
from quixote import get_publisher, get_request, get_response, redirect
from quixote.directory import Directory
from quixote.html import htmltext

from wcs.api import get_query_flag, get_user_from_api_query_string, is_url_signed
from wcs.sql_criterias import Equal, LessOrEqual, Null
from wcs.workflows import (
    EvolutionPart,
    Workflow,
    WorkflowGlobalAction,
    WorkflowStatusJumpItem,
    register_item_class,
)

from ..conditions import Condition
from ..qommon import _, errors, force_str, misc
from ..qommon.cron import CronJob
from ..qommon.form import (
    ComputedExpressionWidget,
    RadiobuttonsWidget,
    SingleSelectWidget,
    StringWidget,
    WidgetList,
)
from ..qommon.humantime import humanduration2seconds, seconds2humanduration, timewords
from ..qommon.publisher import get_publisher_class
from ..qommon.template import Template
from ..qommon.upload_storage import PicklableUpload

JUMP_TIMEOUT_INTERVAL = max((60 // int(os.environ.get('WCS_JUMP_TIMEOUT_CHECKS', '3')), 1))


class WorkflowTriggeredEvolutionPart(EvolutionPart):
    content = None
    trigger_name = None
    datetime = None
    kind = None

    def __init__(self, trigger_name, content, kind):
        self.trigger_name = trigger_name
        self.content = content
        self.kind = kind
        self.datetime = localtime()
        self.trigger_name_key = misc.simplify(self.trigger_name, space='_')

    @classmethod
    def init_with_put_request(cls, trigger_name, kind):
        file_content = get_request().stdin.read()
        content_type = get_request().headers.get('content-type') or 'application/octet-stream'
        filename = get_request().headers.get('filename') or 'file.bin'
        upload = PicklableUpload(filename, content_type)
        upload.receive([file_content])
        return cls(trigger_name, upload, kind)


def jump_and_perform(formdata, action, workflow_data=None, check_progress=True):
    action.handle_markers_stack(formdata)
    must_store = action.add_jump_part(formdata)
    if workflow_data:
        formdata.update_workflow_data(workflow_data)
        must_store = True
    if must_store:
        formdata.store()
    if formdata.jump_status(action.status):
        return formdata.perform_workflow(check_progress=check_progress)


class JumpDirectory(Directory):
    _q_exports = ['trigger']

    def __init__(self, formdata, wfstatusitem, wfstatus):
        self.formdata = formdata
        self.wfstatusitem = wfstatusitem
        self.wfstatus = wfstatus
        self.trigger = TriggerDirectory(formdata, wfstatusitem, wfstatus)


class TriggerDirectory(Directory):
    def __init__(self, formdata, wfstatusitem, wfstatus):
        self.formdata = formdata
        self.wfstatusitem = wfstatusitem
        self.wfstatus = wfstatus

    def _q_lookup(self, component):
        if get_request().is_json():
            get_response().set_content_type('application/json')

        check_progress = not get_query_flag('bypass-processing-check')
        if self.formdata.workflow_processing_timestamp and check_progress:
            if get_request().is_json():
                raise errors.AccessForbiddenError(_('Formdata currently processing actions.'))
            raise errors.AccessForbiddenError()

        signed_request = is_url_signed()
        user = get_user_from_api_query_string() or get_request().user
        for item in self.wfstatus.items:
            if not isinstance(item, JumpWorkflowStatusItem):
                continue
            if item.mode == 'trigger' and item.trigger == component:
                if not item.get_target_status():
                    raise errors.PublishError(_('Broken jump or missing target status.'))
                if get_request().get_method() not in ('POST', 'PUT'):
                    raise errors.AccessForbiddenError(_('Wrong HTTP method (must be POST or PUT).'))
                if signed_request and not item.by:
                    pass
                else:
                    if not user:
                        raise errors.AccessForbiddenError(_('User not authenticated.'))
                    if not item.check_auth(self.formdata, user):
                        raise errors.AccessForbiddenError(_('Unsufficient roles.'))
                if item.check_condition(self.formdata, trigger=component):
                    workflow_data = None
                    if get_request().get_method() == 'POST':
                        if hasattr(get_request(), '_json'):
                            workflow_data = get_request().json
                        self.formdata.evolution[-1].add_part(
                            WorkflowTriggeredEvolutionPart(component, workflow_data, 'jump')
                        )
                        if workflow_data is not None and not isinstance(workflow_data, dict):
                            # for historical reason dictionaries are stored as-is, other data
                            # types are embedded in a new dictionary.
                            workflow_data = {component: workflow_data}
                    else:
                        self.formdata.evolution[-1].add_part(
                            WorkflowTriggeredEvolutionPart.init_with_put_request(component, 'jump')
                        )
                    self.formdata.store()
                    self.formdata.record_workflow_event('api-trigger', action_item_id=item.id)
                    url = jump_and_perform(
                        self.formdata, item, workflow_data=workflow_data, check_progress=check_progress
                    )
                else:
                    if get_request().is_json():
                        raise errors.AccessForbiddenError(_('Unmet condition.'))
                    raise errors.AccessForbiddenError()

                if get_request().is_json():
                    return json.dumps({'err': 0, 'url': url})
                if url:
                    return redirect(url)
                return redirect(self.formdata.get_url())
        # no trigger found
        raise errors.TraversalError()

    def _q_traverse(self, path):
        # remove trailing slash from path
        if path[-1] == '':
            path = path[:-1]
        if len(path) != 1:
            raise errors.TraversalError()
        return super()._q_traverse(path)


class JumpWorkflowStatusItem(WorkflowStatusJumpItem):
    description = _('Automatic Jump')
    key = 'jump'

    by = []
    mode = None
    condition = None
    trigger = None
    timeout = None
    _granularity = JUMP_TIMEOUT_INTERVAL * 60

    directory_name = 'jump'
    directory_class = JumpDirectory

    def init_with_default_values(self):
        super().init_with_default_values()
        if not isinstance(self.parent, WorkflowGlobalAction):
            self.mode = 'immediate'  # default value

    def migrate(self):
        changed = super().migrate()
        if not self.mode:  # 2024-03-29
            if not (hasattr(self, 'parent') and isinstance(self.parent, WorkflowGlobalAction)):
                if self.trigger:
                    self.mode = 'trigger'
                elif self.timeout:
                    self.mode = 'timeout'
                else:
                    self.mode = 'immediate'
                changed = True
        return changed

    def timeout_init_with_xml(self, elem, include_id=False, snapshot=False):
        if elem is None or elem.text is None:
            self.timeout = None
        else:
            timeout = force_str(elem.text)
            if self.get_expression(timeout)['type'] != 'text':
                self.timeout = timeout
            else:
                self.timeout = int(timeout)

    @property
    def waitpoint(self):
        if self.timeout or self.trigger:
            return True
        return False

    def get_jump_type_label(self):
        reasons = []
        if self.condition and self.condition.get('value'):
            reasons.append(_('condition'))
        if self.mode == 'trigger' and self.trigger:
            reasons.append(_('trigger'))
        if self.mode == 'timeout' and self.timeout:
            reasons.append(_('timeout'))
        return ', '.join([str(x) for x in reasons])

    def get_jump_label(self, target_id):
        jump_type_label = self.get_jump_type_label()
        if jump_type_label:
            return '%s (%s)' % (self.description, jump_type_label)
        return self.description

    def render_as_line(self):
        # override parent method to avoid mentioning the condition twice.
        return '%s (%s)' % (self.description, self.get_line_details())

    def render_as_short_line(self):
        return self.description

    def get_line_details(self):
        if not self.status:
            return _('not completed')
        wf_status = self.get_target_status()
        if not wf_status:
            return _('broken')
        jump_type_label = self.get_jump_type_label()
        if jump_type_label:
            return _('to %(name)s, %(jump_type_label)s') % {
                'name': wf_status[0].name,
                'jump_type_label': jump_type_label,
            }
        return _('to %s') % wf_status[0].name

    def get_parameters(self):
        if hasattr(self, 'parent') and isinstance(self.parent, WorkflowGlobalAction):
            return ('status', 'condition', 'set_marker_on_status', 'identifier')
        return (
            'status',
            'condition',
            'mode',
            'trigger',
            'by',
            'timeout',
            'set_marker_on_status',
            'identifier',
        )

    def get_inspect_parameters(self):
        parameters = list(self.get_parameters())
        if self.mode != 'trigger' and 'trigger' in parameters:
            parameters.remove('trigger')
        if self.mode != 'trigger' and 'by' in parameters:
            parameters.remove('by')
        if self.mode != 'timeout' and 'timeout' in parameters:
            parameters.remove('timeout')
        return parameters

    def add_parameters_widgets(self, form, parameters, prefix='', formdef=None, **kwargs):
        super().add_parameters_widgets(form, parameters, prefix, formdef, **kwargs)
        if 'condition' in parameters:
            form.get_widget('%scondition' % prefix).advanced = False
            form.get_widget('%scondition' % prefix).tab = ('general', _('General'))
        if 'mode' in parameters:
            form.add(
                RadiobuttonsWidget,
                '%smode' % prefix,
                title=_('Execution mode'),
                options=[
                    ('immediate', _('Immediate'), 'immediate'),
                    ('timeout', _('After timeout delay'), 'timeout'),
                    ('trigger', _('After call to webservice trigger'), 'trigger'),
                ],
                value=self.mode if self.mode else 'immediate',
                default_value='immediate',
                attrs={'data-dynamic-display-parent': 'true'},
                extra_css_class='widget-inline-radio',
            )
        if 'trigger' in parameters:
            form.add(
                StringWidget,
                '%strigger' % prefix,
                title=_('Identifier for webservice'),
                hint=_(
                    'This jump will be triggered by an authorized call '
                    'to <form_url>/jump/trigger/<identifier>/.'
                ),
                value=self.trigger,
                size=40,
                attrs={
                    'data-dynamic-display-child-of': '%smode' % prefix,
                    'data-dynamic-display-value': 'trigger',
                },
            )
        if 'by' in parameters:
            if get_publisher().has_site_option('workflow-functions-only'):
                label = _('Functions allowed to trigger')
            else:
                label = _('Functions or roles allowed to trigger')
            form.add(
                WidgetList,
                '%sby' % prefix,
                title=label,
                element_type=SingleSelectWidget,
                value=self.by,
                add_element_label=self.get_add_role_label(),
                element_kwargs={
                    'render_br': False,
                    'options': [(None, '---', None)] + self.get_list_of_roles(include_logged_in_users=False),
                },
                attrs={
                    'data-dynamic-display-child-of': '%smode' % prefix,
                    'data-dynamic-display-value': 'trigger',
                },
            )
        if 'timeout' in parameters:
            _hint = htmltext(
                _(
                    'ex.: 1 day 12 hours<br/>'
                    'Usable units of time: %(variables)s.<br/>'
                    'This is only the minimum delay guaranteed in the status: the actual delay can be longer.'
                )
            ) % {'variables': ', '.join(timewords()), 'granularity': seconds2humanduration(self._granularity)}
            if not isinstance(self.timeout, int) and self.get_expression(self.timeout)['type'] != 'text':
                form.add(
                    ComputedExpressionWidget,
                    '%stimeout' % prefix,
                    title=_('Timeout'),
                    value=self.timeout,
                    hint=_hint,
                    attrs={
                        'data-dynamic-display-child-of': '%smode' % prefix,
                        'data-dynamic-display-value': 'timeout',
                    },
                )
            else:
                form.add(
                    StringWidget,
                    '%stimeout' % prefix,
                    title=_('Timeout'),
                    value=seconds2humanduration(self.timeout),
                    hint=_hint,
                    attrs={
                        'data-dynamic-display-child-of': '%smode' % prefix,
                        'data-dynamic-display-value': 'timeout',
                    },
                )

    def timeout_parse(self, value):
        if not value:
            return value
        if self.get_expression(value)['type'] != 'text':
            return value
        return humanduration2seconds(value)

    def get_computed_strings(self):
        yield from super().get_computed_strings()
        yield self.timeout

    def perform(self, formdata):
        wf_status = self.get_target_status(formdata)
        if wf_status:
            self.handle_markers_stack(formdata)
            self.add_jump_part(formdata)
            formdata.status = 'wf-%s' % wf_status[0].id
            formdata.store()

    def check_condition(self, formdata, *args, trigger=None, **kwargs):
        result = super().check_condition(formdata, *args, **kwargs)
        if not result:
            return False

        if self.mode == 'timeout' and self.timeout:
            timeout_str = self.compute(self.timeout)
            try:
                timeout_seconds = float(timeout_str)
            except ValueError:
                try:
                    timeout_seconds = humanduration2seconds(timeout_str)
                except ValueError:
                    timeout_seconds = 0
                if timeout_seconds == 0:
                    get_publisher().record_error(
                        _('Error in timeout value %(value)r (computed from %(template)r)')
                        % {'value': timeout_str, 'template': self.timeout},
                        formdata=formdata,
                        formdef=formdata.formdef,
                        workflow=formdata.formdef.workflow,
                        notify=False,
                        record=True,
                    )
                    return False
            last = formdata.last_update_time
            if last and timeout_seconds:
                diff = (localtime() - last).total_seconds()
                if diff < timeout_seconds:
                    return False

        if self.mode == 'trigger' and self.trigger:
            if trigger is None or trigger != self.trigger:
                return False

        return True

    def is_condition_always_false(self):
        if not self.condition:
            return False
        condition = Condition(self.condition)
        return condition.is_always_false()

    def has_valid_timeout(self):
        if not self.status or not self.timeout:
            return False

        if not self.get_target_status():
            # this will catch status being a removed status
            return False

        return True

    def perform_in_tests(self, formdata):
        self.perform(formdata)


register_item_class(JumpWorkflowStatusItem)


def workflows_with_timeout():
    """Returns {workflow id: {status id: [jump_item...]}}"""
    wfs_status = {}

    for workflow_id in Workflow.keys():
        workflow_id = str(workflow_id)
        workflow = Workflow.get(workflow_id, ignore_errors=True)
        if not workflow:
            continue
        for status in workflow.possible_status:
            status_str_id = 'wf-%s' % status.id
            for item in status.items:
                if not hasattr(item, 'has_valid_timeout') or not item.has_valid_timeout():
                    continue
                # if item.condition is statically always false, we can skip
                # searching formdatas older than this timeout
                if item.is_condition_always_false():
                    continue
                if workflow_id not in wfs_status:
                    wfs_status[workflow_id] = {}
                if status_str_id not in wfs_status[workflow_id]:
                    wfs_status[workflow_id][status_str_id] = []
                wfs_status[workflow_id][status_str_id].append(item)

    return wfs_status


def get_min_jumps_delay(jump_actions):
    delay = math.inf
    for jump_action in jump_actions:
        if Template.is_template_string(jump_action.timeout):
            delay = 0
            break
        delay = min(delay, int(jump_action.timeout))
    # limit delay to minimal delay, with upper limit
    delay = min(max(delay, JUMP_TIMEOUT_INTERVAL * 60), 100 * 365 * 24 * 3600)
    return delay


def _apply_timeouts(publisher, **kwargs):
    '''Traverse all filled form and apply expired timeout jumps if needed'''
    from ..carddef import CardDef
    from ..formdef import FormDef

    wfs_status = workflows_with_timeout()
    job = kwargs.pop('job', None)

    for formdef in itertools.chain(FormDef.select(ignore_errors=True), CardDef.select(ignore_errors=True)):
        status_ids = wfs_status.get(str(formdef.workflow_id))
        if not status_ids:
            continue
        formdata_class = formdef.data_class()
        for status_id in status_ids:
            # get minimum delay for jumps in this status
            delay = get_min_jumps_delay(wfs_status[str(formdef.workflow_id)][status_id])
            status = formdef.workflow.get_status(status_id)

            # record an error if it takes more than than 1/60 of the configured
            # delay, e.g. for the minimal 20 minutes it will warn if it takes more
            # than 20 seconds, for 3 hours it will allow 3 minutes, for 24 hours,
            # 24 minutes. (and allowed CPU time is half that.)
            with (
                job.log_long_job(
                    '%s %s' % (formdef.xml_root_node, formdef.url_name),
                    record_long_duration=(delay / 60),
                    record_long_cpu_duration=(delay / 60 / 2),
                    record_error_kwargs={
                        'error_summary': _(
                            'too much time spent on timeout jumps of "%(form_name)s" in status "%(status_name)s"'
                        )
                        % {
                            'form_name': formdef.name,
                            'status_name': status.name,
                        },
                        'formdef': formdef,
                    },
                )
                if job
                else contextlib.ExitStack()
            ):
                criterias = [
                    Equal('status', status_id),
                    Null('anonymised'),
                    LessOrEqual(
                        'last_update_time',
                        localtime() - datetime.timedelta(seconds=delay),
                    ),
                    Null('workflow_processing_timestamp'),
                ]
                formdatas = formdata_class.select_iterator(criterias, ignore_errors=True, itersize=200)

                if job:
                    job.log_debug(
                        f'applying timeouts on {formdef.url_name} (id:{formdef.id}), status_id: {status_id}'
                    )

                for formdata in formdatas:
                    formdata.refresh_from_storage_if_updated()
                    if formdata.workflow_processing_timestamp:
                        continue
                    for jump_action in wfs_status[str(formdef.workflow_id)][formdata.status]:
                        get_publisher().reset_formdata_state()
                        get_publisher().substitutions.feed(formdef)
                        get_publisher().substitutions.feed(formdata)
                        if jump_action.check_condition(formdata):
                            formdata.record_workflow_event('timeout-jump', action_item_id=jump_action.id)
                            jump_and_perform(formdata, jump_action)
                            break


def register_cronjob():
    # every JUMP_TIMEOUT_INTERVAL minutes check for expired status jump
    # timeouts.
    get_publisher_class().register_cronjob(
        CronJob(
            _apply_timeouts,
            name='evaluate_jumps',
            hours=range(24),
            minutes=range(0, 60, JUMP_TIMEOUT_INTERVAL),
        )
    )


class LazyFormDataWorkflowTriggers:
    def __init__(self, formdata):
        self._formdata = formdata

    def __getattr__(self, trigger_name):
        triggers = []
        if '_varnames' not in self.__dict__:
            # keep a cache of valid attribute names
            self.__dict__['_varnames'] = varnames = set()
        else:
            # use cache to avoid iterating on parts
            varnames = self.__dict__['_varnames']
            if trigger_name not in varnames:
                raise AttributeError(trigger_name)
        for part in self._formdata.iter_evolution_parts(WorkflowTriggeredEvolutionPart):
            varnames.add(part.trigger_name_key)
            if part.trigger_name_key == trigger_name:
                triggers.append(LazyFormDataWorkflowTriggersItem(part))
        if triggers:
            return LazyFormDataWorkflowTriggersItems(triggers)
        raise AttributeError(trigger_name)

    def inspect_keys(self):
        varnames = set()
        for part in self._formdata.iter_evolution_parts(WorkflowTriggeredEvolutionPart):
            if part.trigger_name:
                varnames.add(part.trigger_name_key)
        yield from varnames


class LazyFormDataWorkflowTriggersItems:
    def __init__(self, triggers):
        self._triggers = triggers

    def inspect_keys(self):
        return [str(x) for x in range(len(self._triggers))] + ['content', 'datetime', 'kind']

    # alias to latest values
    @property
    def content(self):
        return self._triggers[-1].content

    @property
    def datetime(self):
        return self._triggers[-1].datetime

    @property
    def kind(self):
        return self._triggers[-1].kind

    def __getitem__(self, key):
        try:
            key = int(key)
        except ValueError:
            try:
                return getattr(self, key)
            except AttributeError:
                return self._triggers[-1][key]
        return self._triggers[key]

    def __len__(self):
        return len(self._triggers)

    def __iter__(self):
        yield from self._triggers


class LazyFormDataWorkflowTriggersItem:
    def __init__(self, part):
        self.content = part.content
        self.datetime = part.datetime
        self.kind = part.kind

    def inspect_keys(self):
        return ['content', 'datetime', 'kind']
