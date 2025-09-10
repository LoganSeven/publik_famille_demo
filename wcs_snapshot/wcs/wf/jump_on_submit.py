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

from wcs.qommon import _
from wcs.workflows import WorkflowStatusJumpItem, register_item_class


class JumpOnSubmitWorkflowStatusItem(WorkflowStatusJumpItem):
    description = _('On Submit Jump')
    key = 'jumponsubmit'
    ok_in_global_action = False

    def get_jump_label(self, target_id):
        return self.description

    def get_line_details(self):
        if self.status:
            if self.get_target_status():
                return _('to %s') % self.get_target_status()[0].name
            return _('broken')
        return _('not completed')

    def submit_form(self, form, formdata, user, evo):
        if form.is_submitted() and not form.has_errors():
            button = form.get_widget(form.get_submit())
            if hasattr(button, 'prevent_jump_on_submit'):
                # do not jump on submit on clicks on edit/create doc buttons
                return
            wf_status = self.get_target_status(formdata)
            if wf_status:
                evo.status = 'wf-%s' % wf_status[0].id
                self.handle_markers_stack(formdata)
                self.add_jump_part(formdata, evo)
            if formdata.is_workflow_test() and formdata.testdef:
                formdata.testdef.add_to_coverage(self)

    def get_parameters(self):
        return ('status', 'set_marker_on_status', 'condition', 'identifier')


register_item_class(JumpOnSubmitWorkflowStatusItem)
