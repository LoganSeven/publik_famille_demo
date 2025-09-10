# w.c.s. - web application for online forms
# Copyright (C) 2005-2019  Entr'ouvert
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

import json

from quixote import get_request, get_response
from quixote.directory import Directory

from wcs.api import get_query_flag, get_user_from_api_query_string
from wcs.wf.jump import WorkflowTriggeredEvolutionPart
from wcs.workflows import WorkflowGlobalActionWebserviceTrigger, perform_items, push_perform_workflow

from ..qommon import _, errors


class HookDirectory(Directory):
    _q_exports = ['']

    def __init__(self, formdata, action, trigger):
        self.formdata = formdata
        self.action = action
        self.trigger = trigger

    def _q_index(self):
        get_response().set_content_type('application/json')

        if get_request().get_method() not in ('POST', 'PUT'):
            raise errors.AccessForbiddenError(_('Wrong HTTP method (must be POST or PUT).'))

        user = get_user_from_api_query_string() or get_request().user
        if not self.trigger.check_executable(self.formdata, user):
            raise errors.AccessForbiddenError(_('Insufficient roles.'))

        check_progress = not get_query_flag('bypass-processing-check')
        if self.formdata.workflow_processing_timestamp and check_progress:
            if get_request().is_json():
                raise errors.AccessForbiddenError(_('Formdata currently processing actions.'))
            raise errors.AccessForbiddenError()

        if get_request().get_method() == 'POST':
            workflow_data = get_request().json if hasattr(get_request(), '_json') else None
            self.formdata.evolution[-1].add_part(
                WorkflowTriggeredEvolutionPart(self.trigger.identifier, workflow_data, 'global')
            )
            if hasattr(get_request(), '_json'):
                self.formdata.update_workflow_data({self.trigger.identifier: workflow_data})
        else:  # PUT
            self.formdata.evolution[-1].add_part(
                WorkflowTriggeredEvolutionPart.init_with_put_request(self.trigger.identifier, 'global')
            )
        self.formdata.store()

        self.formdata.record_workflow_event('global-api-trigger', global_action_id=self.action.id)
        with push_perform_workflow(self.formdata):
            perform_items(self.action.items, self.formdata, global_action=True, check_progress=check_progress)
        return json.dumps({'err': 0})


class WorkflowGlobalActionWebserviceHooksDirectory(Directory):
    def __init__(self, formdata):
        self.formdata = formdata

    def _q_lookup(self, component):
        for action in self.formdata.formdef.workflow.global_actions:
            for trigger in action.triggers or []:
                if isinstance(trigger, WorkflowGlobalActionWebserviceTrigger):
                    if trigger.identifier == component:
                        return HookDirectory(self.formdata, action, trigger)
        raise errors.TraversalError()

    def _q_traverse(self, path):
        if len(path) == 1:
            # add fake trailing slash into path
            path = path + ['']
        return super()._q_traverse(path)
