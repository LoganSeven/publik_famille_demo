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

from wcs.carddef import CardDef
from wcs.qommon import _
from wcs.wf.create_formdata import CreateFormdataWorkflowStatusItem, LinkedFormdataEvolutionPart
from wcs.workflows import register_item_class


class LinkedCardDataEvolutionPart(LinkedFormdataEvolutionPart):
    formdef_class = CardDef


class CreateCarddataWorkflowStatusItem(CreateFormdataWorkflowStatusItem):
    description = _('Create Card Data')
    key = 'create_carddata'
    category = 'formdata-action'
    ok_in_global_action = True

    formdef_class = CardDef
    evolution_part_class = LinkedCardDataEvolutionPart
    workflow_trace_event = 'workflow-created-carddata'
    workflow_test_data_attribute = 'created_carddata'

    formdef_label = _('Card')
    mappings_label = _('Mappings to new card fields')
    varname_hint = _('This is used to get linked card in expressions.')
    user_association_option_label = _('User to associate to card')

    def get_parameters(self):
        return (
            'action_label',
            'formdef_slug',
            'map_fields_by_varname',
            'mappings',
            'user_association_mode',
            'user_association_template',
            'varname',
            'condition',
        )


register_item_class(CreateCarddataWorkflowStatusItem)
