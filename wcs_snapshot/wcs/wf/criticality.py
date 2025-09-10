# w.c.s. - web application for online forms
# Copyright (C) 2005-2016  Entr'ouvert
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

from wcs.qommon.form import SingleSelectWidget
from wcs.workflows import WorkflowStatusItem, register_item_class

from ..qommon import _

MODE_INC = '1'
MODE_DEC = '2'
MODE_SET = '3'


class ModifyCriticalityWorkflowStatusItem(WorkflowStatusItem):
    description = _('Criticality Levels')
    key = 'modify_criticality'
    category = 'formdata-action'

    mode = MODE_INC
    absolute_value = None

    def get_parameters(self):
        return ('mode', 'absolute_value', 'condition')

    def get_inspect_parameters(self):
        parameters = super().get_inspect_parameters()
        if self.mode != MODE_SET:
            parameters.remove('absolute_value')
        return parameters

    @classmethod
    def is_available(cls, workflow=None):
        return workflow and workflow.criticality_levels

    def add_parameters_widgets(self, form, parameters, prefix='', formdef=None, **kwargs):
        super().add_parameters_widgets(form, parameters, prefix=prefix, formdef=formdef, **kwargs)
        if 'mode' in parameters:
            form.add(
                SingleSelectWidget,
                '%smode' % prefix,
                title=_('Modification Mode'),
                value=self.mode,
                required=True,
                options=[
                    (MODE_INC, _('Increase Level')),
                    (MODE_DEC, _('Decrease Level')),
                    (MODE_SET, _('Set Level')),
                ],
                attrs={'data-dynamic-display-parent': 'true'},
            )
        if 'absolute_value' in parameters:
            if self.get_workflow().criticality_levels:
                options = [(str(i), x.name) for i, x in enumerate(self.get_workflow().criticality_levels)]
            else:
                options = [('0', '---')]
            form.add(
                SingleSelectWidget,
                '%sabsolute_value' % prefix,
                title=_('Value'),
                value=self.absolute_value,
                options=options,
                attrs={
                    'data-dynamic-display-child-of': '%smode' % prefix,
                    'data-dynamic-display-value': _('Set Level'),
                },
            )

    def perform(self, formdata):
        if self.mode == MODE_INC:
            formdata.increase_criticality_level()
        elif self.mode == MODE_DEC:
            formdata.decrease_criticality_level()
        elif self.mode == MODE_SET:
            formdata.set_criticality_level(int(self.absolute_value))

    def perform_in_tests(self, formdata):
        self.perform(formdata)


register_item_class(ModifyCriticalityWorkflowStatusItem)
