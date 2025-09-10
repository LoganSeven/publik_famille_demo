# w.c.s. - web application for online forms
# Copyright (C) 2005-2020  Entr'ouvert
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

import copy

from django.utils.timezone import localtime
from quixote import get_publisher

from wcs.formdata import Evolution
from wcs.qommon import _
from wcs.wf.create_carddata import CreateCarddataWorkflowStatusItem
from wcs.wf.external_workflow import ExternalWorkflowGlobalAction
from wcs.workflows import ContentSnapshotPart, register_item_class


class EditCarddataWorkflowStatusItem(CreateCarddataWorkflowStatusItem, ExternalWorkflowGlobalAction):
    description = _('Edit Card Data')
    key = 'edit_carddata'
    mappings_label = _('Mappings to card fields')
    automatic_targetting = _('Action on cards linked to this form/card')
    manual_targetting = _('Specify the list of cards on which the action will be applied')

    @classmethod
    def is_available(cls, workflow=None):
        return ExternalWorkflowGlobalAction.is_available()

    def get_parameters(self):
        return ('action_label', 'formdef_slug', 'target_mode', 'target_id', 'mappings', 'condition')

    @property
    def slug(self):
        # act only on linked carddefs
        return 'carddef:%s' % self.formdef_slug

    def perform(self, formdata):
        carddef = self.formdef
        if not carddef:
            return

        formdata.store()

        target_data = None
        for target_data in self.iter_target_datas(formdata, carddef):
            if formdata.is_workflow_test():
                self.edited_carddata.append(target_data)

            old_data = copy.deepcopy(target_data.data)
            self.apply_mappings(dest=target_data, src=formdata)
            with get_publisher().substitutions.freeze():
                evo = Evolution(formdata=target_data)
                if target_data.evolution:
                    last_evo = target_data.evolution[-1]
                else:
                    # target data should always have an existing history but create an empty
                    # evolution entry if there is no history at all.
                    last_evo = Evolution(formdata=target_data)
                evo.time = localtime()
                evo.status = target_data.status
                target_data.evolution.append(evo)
                part = ContentSnapshotPart.take(formdata=target_data, old_data=old_data)
                if (
                    part.has_changes
                    or last_evo.comment
                    or [x for x in last_evo.parts or [] if not isinstance(x, ContentSnapshotPart)]
                ):
                    # record a workflow event with a link to current workflow & action
                    target_data.record_workflow_event(
                        'workflow-edited',
                        external_workflow_id=self.get_workflow().id,
                        external_status_id=self.parent.id,
                        external_item_id=self.id,
                    )
                    target_data.store()
                    # add a link to created carddata
                    formdata.record_workflow_event(
                        'workflow-edited-carddata',
                        external_formdef_id=carddef.id,
                        external_formdata_id=target_data.id,
                    )
        if target_data is None:
            # nothing updated, no target_data found
            formdata.record_workflow_event(
                'workflow-edited-carddata',
            )

        # update local object as it may have modified itself
        formdata.refresh_from_storage()

    def perform_in_tests(self, formdata):
        from wcs.workflow_tests import WorkflowTests

        test_attributes = WorkflowTests.get_formdata_test_attributes(formdata)

        self.edited_carddata = []
        self.perform(formdata)

        # restore test attributes which were removed when refresh_from_storage() was called in perform()
        for attribute, value in test_attributes:
            setattr(formdata, attribute, value)

        formdata.edited_carddata.extend(self.edited_carddata)


register_item_class(EditCarddataWorkflowStatusItem)
