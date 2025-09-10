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

from django.utils.timezone import localtime
from quixote import get_publisher, get_request, get_session

from wcs.formdef import FormDef
from wcs.qommon.form import SingleSelectWidget, StringWidget, WidgetList, WysiwygTextWidget
from wcs.workflows import WorkflowStatusItem, register_item_class

from ..qommon import _


class ResubmitWorkflowStatusItem(WorkflowStatusItem):
    description = _('Resubmission')
    key = 'resubmit'
    category = 'formdata-action'
    endpoint = False
    waitpoint = True
    ok_in_global_action = False

    by = []
    formdef_slug = None
    label = None
    backoffice_info_text = None

    @classmethod
    def is_available(cls, workflow=None):
        return get_publisher().has_site_option('workflow-resubmit-action')

    def get_line_details(self):
        if self.by:
            return _('by %s') % self.render_list_of_roles(self.by)
        return _('not completed')

    def fill_form(self, form, formdata, user, **kwargs):
        label = self.label
        if not label:
            label = _('Resubmit')
        if not self.formdef_slug:  # user can choose appropriate form
            if get_request().is_in_backoffice():
                list_forms = [
                    (x.id, x.name, x.id)
                    for x in FormDef.select(order_by='name', lightweight=True)
                    if x.backoffice_submission_roles and not x.is_disabled()
                ]
            else:
                list_forms = [
                    (x.id, x.name, x.id)
                    for x in FormDef.select(order_by='name', lightweight=True)
                    if x.enable_tracking_codes and not x.is_disabled()
                ]
            form.add(SingleSelectWidget, 'resubmit', title=_('Form'), required=True, options=list_forms)
        form.add_submit('button%s' % self.id, label, attrs={'class': 'resubmit'})
        form.get_widget('button%s' % self.id).backoffice_info_text = self.backoffice_info_text
        form.get_widget('button%s' % self.id).action_id = self.id

    def submit_form(self, form, formdata, user, evo):
        if form.get_submit() != 'button%s' % self.id:
            return
        if not self.formdef_slug:  # user can choose appropriate form
            formdef_id = form.get_widget('resubmit').parse()
            formdef = FormDef.get(formdef_id)
        elif self.formdef_slug == '_same':
            formdef = formdata.formdef
        else:
            formdef = FormDef.get_by_urlname(self.formdef_slug)
        new_formdata = formdef.data_class()()
        new_formdata.status = 'draft'
        new_formdata.receipt_time = localtime()
        new_formdata.user_id = formdata.user_id
        new_formdata.submission_context = (formdata.submission_context or {}).copy()
        new_formdata.submission_channel = formdata.submission_channel
        new_formdata.backoffice_submission = get_request().is_in_backoffice()
        if not new_formdata.submission_context:
            new_formdata.submission_context = {}
        new_formdata.submission_context['orig_object_type'] = formdata.formdef.xml_root_node
        new_formdata.submission_context['orig_formdef_id'] = formdata.formdef.id
        new_formdata.submission_context['orig_formdata_id'] = formdata.id
        new_formdata.data = {}

        field_dict = {}
        for field in formdata.formdef.fields:
            if not field.varname:
                continue
            field_dict['%s-%s' % (field.varname, field.key)] = field.id

        for field in formdef.fields:
            field_dict_key = '%s-%s' % (field.varname, field.key)
            orig_formdata_field_id = field_dict.get(field_dict_key)
            if orig_formdata_field_id is None:
                continue
            for suffix in ('', '_raw', '_structured'):
                old_key = '%s%s' % (orig_formdata_field_id, suffix)
                new_key = '%s%s' % (field.id, suffix)
                if old_key not in formdata.data:
                    continue
                new_formdata.data[new_key] = formdata.data[old_key]

        new_formdata.store()

        workflow_data = {
            'resubmit_formdata_backoffice_url': new_formdata.get_url(backoffice=True),
            'resubmit_formdata_draft_url': new_formdata.get_url(backoffice=False),
        }
        formdata.update_workflow_data(workflow_data)
        formdata.store()
        if formdata.user_id is None and not new_formdata.backoffice_submission:
            get_session().mark_anonymous_formdata(new_formdata)

    def add_parameters_widgets(self, form, parameters, prefix='', formdef=None, **kwargs):
        super().add_parameters_widgets(form, parameters, prefix=prefix, formdef=formdef, **kwargs)
        if 'by' in parameters:
            form.add(
                WidgetList,
                '%sby' % prefix,
                title=_('By'),
                element_type=SingleSelectWidget,
                value=self.by,
                add_element_label=_('Add Role'),
                element_kwargs={
                    'render_br': False,
                    'options': [(None, '---', None)] + self.get_list_of_roles(),
                },
            )
        if 'label' in parameters:
            form.add(
                StringWidget, '%slabel' % prefix, title=_('Button Label'), value=self.label or _('Resubmit')
            )
        if 'formdef_slug' in parameters:
            list_forms = [(None, _('Any'), None), ('_same', _('Same as form'), '_same')]
            list_forms.extend(
                [(x.url_name, x.name, x.url_name) for x in FormDef.select(order_by='name', lightweight=True)]
            )
            form.add(
                SingleSelectWidget,
                'formdef_slug',
                title=_('Form'),
                value=self.formdef_slug,
                required=False,
                options=list_forms,
            )
        if 'backoffice_info_text' in parameters:
            form.add(
                WysiwygTextWidget,
                '%sbackoffice_info_text' % prefix,
                title=_('Information Text for Backoffice'),
                value=self.backoffice_info_text,
            )

    def get_parameters(self):
        return ('by', 'label', 'formdef_slug', 'backoffice_info_text', 'condition')


register_item_class(ResubmitWorkflowStatusItem)
