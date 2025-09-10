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
from quixote import get_publisher, get_request

from wcs.formdata import Evolution
from wcs.qommon import _
from wcs.qommon.form import (
    CheckboxWidget,
    RadiobuttonsWidget,
    SingleSelectWidget,
    StringWidget,
    VarnameWidget,
    WidgetList,
    WysiwygTextWidget,
)
from wcs.workflows import WorkflowStatusItem, register_item_class


class EditableWorkflowStatusItem(WorkflowStatusItem):
    description = _('Edition')
    key = 'editable'
    category = 'formdata-action'
    endpoint = False
    waitpoint = True
    ok_in_global_action = False

    by = []
    status = None
    label = None
    backoffice_info_text = None
    operation_mode = 'full'  # or 'single' or 'partial'
    page_identifier = None
    set_marker_on_status = False
    identifier = None

    def get_line_details(self):
        if self.by:
            return _('"%(button_label)s", by %(by)s') % {
                'button_label': self.get_button_label(),
                'by': self.render_list_of_roles(self.by),
            }
        return _('not completed')

    def get_jump_label(self, target_id):
        # force action description instead of button label
        return self.render_as_line()

    def i18n_scan(self, base_location):
        location = '%sitems/%s/' % (base_location, self.id)
        if self.label:
            yield location, None, self.label

    def get_button_label(self):
        if self.label:
            return get_publisher().translate(self.label)
        return _('Edit Form')

    def fill_form(self, form, formdata, user, **kwargs):
        widget = form.add_submit('button%s' % self.id, self.get_button_label())
        widget.backoffice_info_text = self.backoffice_info_text
        widget.ignore_form_errors = True
        widget.prevent_jump_on_submit = True
        widget.attrs['formnovalidate'] = 'formnovalidate'

    def submit_form(self, form, formdata, user, evo):
        if form.get_submit() == 'button%s' % self.id:
            return (
                formdata.get_url(
                    backoffice=get_request().is_in_backoffice(),
                    include_category=True,
                    language=get_publisher().current_language,
                )
                + 'wfedit-%s' % self.id
            )

    def finish_edition(self, formdata, user):
        user_id = None
        if user:
            if get_request().is_in_frontoffice() and formdata.is_submitter(user):
                user_id = '_submitter'
            else:
                user_id = user.id

        wf_status = self.get_target_status(formdata)
        if wf_status:
            self.handle_markers_stack(formdata)
            self.add_jump_part(formdata)
            formdata.store()
            if formdata.jump_status(wf_status[0].id, user_id=user_id):
                formdata.record_workflow_event('edit-action', action_item_id=self.id)
                return formdata.perform_workflow()
        else:
            # add history entry
            evo = Evolution(formdata=formdata)
            evo.time = localtime()
            evo.who = user_id
            formdata.evolution.append(evo)
            formdata.store()

    def get_edit_pages(self, pages):
        edit_pages = []
        for page in pages:
            if self.page_identifier == page.varname or edit_pages:
                edit_pages.append(page)
                if self.operation_mode == 'single':
                    break

        return edit_pages

    def get_inspect_parameters(self):
        parameters = super().get_inspect_parameters()
        if self.operation_mode not in ('single', 'partial'):
            parameters.remove('page_identifier')
        return parameters

    def add_parameters_widgets(self, form, parameters, prefix='', formdef=None, **kwargs):
        super().add_parameters_widgets(form, parameters, prefix=prefix, formdef=formdef, **kwargs)
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
                    'options': [(None, '---', None)] + self.get_list_of_roles(),
                },
            )
        if 'status' in parameters:
            form.add(
                SingleSelectWidget,
                '%sstatus' % prefix,
                title=_('Status After Edit'),
                value=self.status,
                hint=_("Don't select any if you don't want status change processing"),
                options=[(None, '---', '', {})] + self.get_workflow().get_possible_target_options(),
                attrs={'data-dynamic-display-parent': 'true'},
            )
        if 'label' in parameters:
            form.add(StringWidget, '%slabel' % prefix, title=_('Button Label'), value=self.label)
        if 'backoffice_info_text' in parameters:
            form.add(
                WysiwygTextWidget,
                '%sbackoffice_info_text' % prefix,
                title=_('Information Text for Backoffice'),
                value=self.backoffice_info_text,
            )
        if 'operation_mode' in parameters:
            form.add(
                RadiobuttonsWidget,
                '%soperation_mode' % prefix,
                title=_('Operation Mode'),
                options=[
                    ('full', _('All pages'), 'full'),
                    ('single', _('Single page'), 'single'),
                    ('partial', _('From specific page'), 'partial'),
                ],
                advanced=True,
                value=self.operation_mode,
                attrs={'data-dynamic-display-parent': 'true'},
                extra_css_class='widget-inline-radio',
                default_value=self.__class__.operation_mode,
            )
        if 'page_identifier' in parameters:
            form.add(
                StringWidget,
                '%spage_identifier' % prefix,
                title=_('Page Identifier'),
                value=self.page_identifier,
                advanced=True,
                attrs={
                    'data-dynamic-display-child-of': '%soperation_mode' % prefix,
                    'data-dynamic-display-value-in': 'single|partial',
                },
            )
        if 'identifier' in parameters:
            form.add(
                VarnameWidget,
                '%sidentifier' % prefix,
                title=_('Identifier of status jump'),
                value=self.identifier,
                advanced=True,
                attrs={
                    'data-dynamic-display-child-of': 'status',
                    'data-dynamic-display-value-in': '|'.join(
                        [x[0] for x in self.get_workflow().get_possible_target_options()]
                    ),
                },
            )
        if 'set_marker_on_status' in parameters:
            form.add(
                CheckboxWidget,
                '%sset_marker_on_status' % prefix,
                title=_('Set marker to jump back to current status'),
                value=self.set_marker_on_status,
                advanced=True,
                attrs={
                    'data-dynamic-display-child-of': 'status',
                    'data-dynamic-display-value-in': '|'.join(
                        [x[0] for x in self.get_workflow().get_possible_target_options()]
                    ),
                },
            )

    def get_parameters(self):
        return (
            'by',
            'status',
            'label',
            'backoffice_info_text',
            'condition',
            'operation_mode',
            'page_identifier',
            'identifier',
            'set_marker_on_status',
        )


register_item_class(EditableWorkflowStatusItem)
