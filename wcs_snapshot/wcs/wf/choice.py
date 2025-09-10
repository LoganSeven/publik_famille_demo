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

from quixote import get_publisher, get_response

from wcs.qommon import _
from wcs.qommon.form import (
    CheckboxWidget,
    ComputedExpressionWidget,
    SingleSelectWidget,
    StringWidget,
    WidgetList,
    WysiwygTextWidget,
)
from wcs.workflows import WorkflowGlobalAction, WorkflowStatus, WorkflowStatusJumpItem, register_item_class


class ChoiceWorkflowStatusItem(WorkflowStatusJumpItem):
    key = 'choice'
    endpoint = False
    waitpoint = True
    ok_in_global_action = True

    label = None
    by = []
    backoffice_info_text = None
    require_confirmation = False
    confirmation_text = None
    ignore_form_errors = False

    @property
    def description(self):
        if isinstance(self.parent, WorkflowGlobalAction):
            return _('Manual Jump (interactive)')
        return _('Manual Jump')

    def get_label(self):
        expression = self.get_expression(get_publisher().translate(self.label), allow_ezt=False)
        if expression['type'] == 'text':
            return expression['value']
        return _('computed label')

    def get_line_details(self):
        to_status = None
        if self.status == '_previous':
            to_status = WorkflowStatus(_('previously marked status'))
        elif self.status:
            try:
                to_status = self.get_workflow().get_status(self.status)
            except KeyError:
                return _('broken, missing destination status')

        if self.label and to_status:
            more = ''
            if self.set_marker_on_status:
                more += ' ' + str(_('(and set marker)'))
            if self.by:
                return _('"%(label)s", to %(to)s, by %(by)s%(more)s') % {
                    'label': self.get_label(),
                    'to': to_status.name,
                    'by': self.render_list_of_roles(self.by),
                    'more': more,
                }
            return _('"%(label)s", to %(to)s%(more)s') % {
                'label': self.get_label(),
                'to': to_status.name,
                'more': more,
            }
        return _('not completed')

    def get_line_short_details(self):
        return self.label

    def get_computed_strings(self):
        yield from super().get_computed_strings()
        if self.get_expression(self.label, allow_ezt=False)['type'] != 'text':
            yield self.label

    def get_inspect_parameters(self):
        parameters = list(self.get_parameters())
        if not self.require_confirmation:
            parameters.remove('confirmation_text')
        return parameters

    def is_interactive(self):
        return True

    def fill_form(self, form, formdata, user, **kwargs):
        label = self.compute(get_publisher().translate(self.label), allow_ezt=False)
        if not label:
            return
        widget = form.add_submit('button%s' % self.id, label)
        widget.action_id = self.id
        if self.identifier:
            widget.extra_css_class = 'button-%s' % self.identifier
        if self.require_confirmation:
            get_response().add_javascript(['jquery.js', '../../i18n.js', 'qommon.js'])
            widget.attrs = {'data-ask-for-confirmation': self.confirmation_text or 'true'}
        widget.backoffice_info_text = self.backoffice_info_text
        widget.ignore_form_errors = self.ignore_form_errors
        if self.ignore_form_errors:
            widget.attrs['formnovalidate'] = 'formnovalidate'

    def submit_form(self, form, formdata, user, evo):
        if form.get_submit() == 'button%s' % self.id:
            if formdata.is_workflow_test() and formdata.testdef:
                formdata.testdef.add_to_coverage(self)

            wf_status = self.get_target_status(formdata)
            if wf_status:
                evo.status = 'wf-%s' % wf_status[0].id
                self.handle_markers_stack(formdata)
                self.add_jump_part(formdata, evo)
                form.clear_errors()
                return True  # get out of processing loop

    def add_parameters_widgets(self, form, parameters, prefix='', formdef=None, **kwargs):
        super().add_parameters_widgets(form, parameters, prefix=prefix, formdef=formdef, **kwargs)
        if 'label' in parameters:
            form.add(
                ComputedExpressionWidget,
                '%slabel' % prefix,
                title=_('Label'),
                value=self.label,
            )
        if 'by' in parameters:
            form.add(
                WidgetList,
                '%sby' % prefix,
                title=_('By'),
                element_type=SingleSelectWidget,
                value=self.by,
                add_element_label=self.get_add_role_label(),
                element_kwargs={
                    'render_br': False,
                    'options': [(None, '---', None)] + self.get_list_of_roles(current_values=self.by),
                },
            )
        if 'require_confirmation' in parameters:
            form.add(
                CheckboxWidget,
                '%srequire_confirmation' % prefix,
                title=_('Require confirmation'),
                value=self.require_confirmation,
                attrs={'data-dynamic-display-parent': 'true'},
            )
        if 'confirmation_text' in parameters:
            form.add(
                StringWidget,
                '%sconfirmation_text' % prefix,
                title=_('Custom text for confirmation popup'),
                size=100,
                value=self.confirmation_text,
                attrs={
                    'data-dynamic-display-child-of': f'{prefix}require_confirmation',
                    'data-dynamic-display-checked': 'true',
                },
            )
        if 'backoffice_info_text' in parameters:
            form.add(
                WysiwygTextWidget,
                '%sbackoffice_info_text' % prefix,
                title=_('Information Text for Backoffice'),
                value=self.backoffice_info_text,
            )
        if 'ignore_form_errors' in parameters:
            form.add(
                CheckboxWidget,
                '%signore_form_errors' % prefix,
                title=_('Ignore form'),
                value=self.ignore_form_errors,
                advanced=True,
            )

    def get_parameters(self):
        return (
            'label',
            'by',
            'status',
            'require_confirmation',
            'confirmation_text',
            'backoffice_info_text',
            'ignore_form_errors',
            'set_marker_on_status',
            'condition',
            'identifier',
        )

    def i18n_scan(self, base_location):
        location = '%sitems/%s/' % (base_location, self.id)
        yield location, None, self.label
        yield location, None, self.confirmation_text


register_item_class(ChoiceWorkflowStatusItem)
