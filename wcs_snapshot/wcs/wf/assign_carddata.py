# w.c.s. - web application for online forms
# Copyright (C) 2005-2021  Entr'ouvert
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


from quixote import get_publisher

from wcs.qommon import _
from wcs.wf.create_carddata import CreateCarddataWorkflowStatusItem
from wcs.wf.external_workflow import ExternalWorkflowGlobalAction
from wcs.workflows import register_item_class


class AssignCarddataWorkflowStatusItem(CreateCarddataWorkflowStatusItem, ExternalWorkflowGlobalAction):
    description = _('Assign Card Data')
    key = 'assign_carddata'
    automatic_targetting = _('Assign cards linked to this form/card')
    manual_targetting = _('Specify the list of cards which will be assigned')
    always_show_user_fields = True

    def get_parameters(self):
        return (
            'formdef_slug',
            'target_mode',
            'target_id',
            'user_association_mode',
            'user_association_template',
            'condition',
        )

    @property
    def slug(self):
        # act only on linked carddefs
        return 'carddef:%s' % self.formdef_slug

    def get_line_details(self):
        if not self.formdef:
            return _('not configured')
        return self.formdef.name

    def perform(self, formdata):
        carddef = self.formdef
        if not carddef:
            return

        formdata.store()

        for target_data in self.iter_target_datas(formdata, carddef):
            self.assign_user(dest=target_data, src=formdata)
            with get_publisher().substitutions.freeze():
                target_data.store()

        # update local object as it may have modified itself
        formdata.refresh_from_storage()


register_item_class(AssignCarddataWorkflowStatusItem)
