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

import json

from django.core.management.base import CommandError
from quixote import get_publisher

from wcs.formdef import FormDef
from wcs.wf.jump import JumpWorkflowStatusItem
from wcs.wf.jump import jump_and_perform as wcs_jump_and_perform
from wcs.workflows import Workflow

from . import TenantCommand


class Command(TenantCommand):
    """Triggers all "jump trigger" for a formdef, given host publisher context

    source.json file format:
        [
            {
                "data": { "info_for_wf_data": 42, ... },
                "select": { "form_number": 1, ... }
            },
            ...
        ]
    """

    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.add_argument('--trigger', metavar='TRIGGER', required=True)
        parser.add_argument('--workflow-id', metavar='WORKFLOW_ID')
        parser.add_argument('--formdef-id', metavar='FORMDEF_ID')
        parser.add_argument('--all-formdata', action='store_true')
        parser.add_argument('filenames', metavar='FILENAME', nargs='+')

    def handle(self, filenames, domain, trigger, workflow_id, formdef_id, all_formdata, verbosity, **options):
        self.init_tenant_publisher(domain)

        if not all_formdata:
            rows = list(get_rows(filenames))
        else:
            rows = '__all__'

        if formdef_id:
            formdef = FormDef.get(id=formdef_id, ignore_errors=True)
            if not formdef:
                raise CommandError('formdef-id does not exist')
            select_and_jump_formdata(formdef, trigger, rows, verbosity=verbosity)
        else:
            if workflow_id:
                workflow = Workflow.get(id=workflow_id, ignore_errors=True)
                if not workflow:
                    raise CommandError('workflow does not exist')
                workflows = [workflow]
            else:
                workflows = Workflow.select()
            for workflow in workflows:
                status_ids = list(get_status_ids_accepting_trigger(workflow, trigger))
                if status_ids:
                    for formdef in [f for f in FormDef.select() if f.workflow_id == str(workflow.id)]:
                        select_and_jump_formdata(
                            formdef, trigger, rows, status_ids=status_ids, verbosity=verbosity
                        )


def get_rows(args):
    for arg in args:
        with open(arg) as fd:
            yield from json.load(fd)


def get_status_ids_accepting_trigger(workflow, trigger):
    for status in workflow.possible_status:
        for item in status.items:
            if isinstance(item, JumpWorkflowStatusItem) and item.trigger == trigger:
                yield 'wf-%s' % status.id, item
                break


def get_formdata_accepting_trigger(formdef, trigger, status_ids=None):
    if status_ids is None:
        workflow = formdef.get_workflow()
        status_ids = get_status_ids_accepting_trigger(workflow, trigger)
    formdata_ids = []

    data_class = formdef.data_class()
    for status_id, action_item in status_ids:
        formdata_ids = data_class.get_ids_with_indexed_value('status', status_id)
        for formdata_id in formdata_ids:
            yield data_class.get(id=formdata_id), action_item


def match_row(substitution_variables, row):
    select = row['select']
    for key, value in select.items():
        if str(substitution_variables.get(key)) != str(value):
            return False
    return True


def jump_and_perform(formdata, action, workflow_data=None, verbosity=1):
    get_publisher().reset_formdata_state()
    get_publisher().substitutions.feed(formdata.formdef)
    get_publisher().substitutions.feed(formdata)
    if verbosity > 1:
        print('formdata %s jumps to status %s' % (formdata, action.status))
    wcs_jump_and_perform(formdata, action, workflow_data=workflow_data)


def select_and_jump_formdata(formdef, trigger, rows, status_ids=None, verbosity=1):
    for formdata, action_item in get_formdata_accepting_trigger(formdef, trigger, status_ids):
        if rows == '__all__':
            jump_and_perform(formdata, action=action_item, verbosity=verbosity)
        else:
            substitution_variables = formdata.get_substitution_variables()
            for row in rows:
                if match_row(substitution_variables, row):
                    jump_and_perform(
                        formdata, action=action_item, workflow_data=row.get('data'), verbosity=verbosity
                    )
                    break  # next formdata
