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

import copy
import xml.etree.ElementTree as ET

from quixote import get_publisher
from quixote.html import htmltext

from wcs.fields import SetValueError, WidgetField
from wcs.wf.profile import FieldNode
from wcs.workflows import ContentSnapshotPart, WorkflowStatusItem, register_item_class

from ..qommon import _
from ..qommon.form import (
    CompositeWidget,
    ComputedExpressionWidget,
    SingleSelectWidget,
    StringWidget,
    WidgetListAsTable,
)


class SetBackofficeFieldRowWidget(CompositeWidget):
    value_placeholder = _('Leaving the field blank will empty the value.')

    def __init__(self, name, value=None, workflow=None, **kwargs):
        CompositeWidget.__init__(self, name, value, **kwargs)
        if not value:
            value = {}

        label_counters = {}
        for field in workflow.get_backoffice_fields():
            if not issubclass(field.__class__, WidgetField):
                continue
            label = f'{field.label} - {field.get_type_label()}'
            label_counters.setdefault(label, 0)
            label_counters[label] += 1
        repeated_labels = {x for x, y in label_counters.items() if y > 1}

        fields = [('', '', '')]
        for field in workflow.get_backoffice_fields():
            if not issubclass(field.__class__, WidgetField):
                continue
            label = f'{field.label} - {field.get_type_label()}'
            if label in repeated_labels and field.varname:
                label = f'{field.label} - {field.get_type_label()} ({field.varname})'
            fields.append((field.id, label, field.id))

        self.add(
            SingleSelectWidget,
            name='field_id',
            title=_('Field'),
            value=value.get('field_id'),
            options=fields,
            **kwargs,
        )
        self.add(
            ComputedExpressionWidget,
            name='value',
            title=_('Value'),
            value=value.get('value'),
            value_placeholder=self.value_placeholder,
        )

    def _parse(self, request):
        if self.get('field_id'):
            self.value = {'value': self.get('value'), 'field_id': self.get('field_id')}
        else:
            self.value = None


class SetBackofficeFieldsTableWidget(WidgetListAsTable):
    readonly = False
    element_type = SetBackofficeFieldRowWidget

    def __init__(self, name, **kwargs):
        super().__init__(
            name,
            element_type=self.element_type,
            element_kwargs={'workflow': kwargs.pop('workflow')},
            **kwargs,
        )


class SetBackofficeFieldsWorkflowStatusItem(WorkflowStatusItem):
    description = _('Backoffice Data')
    key = 'set-backoffice-fields'
    category = 'formdata-action'

    label = None
    fields = None

    @classmethod
    def is_available(cls, workflow=None):
        return bool(workflow and getattr(workflow.backoffice_fields_formdef, 'fields', None))

    def get_line_details(self):
        return self.label or None

    def get_jump_label(self, target_id):
        if self.label:
            return _('Backoffice Data "%s"') % self.label
        return _('Backoffice Data')

    def get_parameters(self):
        return ('label', 'fields', 'condition')

    def add_parameters_widgets(self, form, parameters, prefix='', formdef=None, **kwargs):
        super().add_parameters_widgets(form, parameters, prefix=prefix, formdef=formdef, **kwargs)
        if 'label' in parameters:
            form.add(StringWidget, '%slabel' % prefix, size=40, title=_('Label'), value=self.label)

        if 'fields' in parameters:
            form.add(
                SetBackofficeFieldsTableWidget,
                '%sfields' % prefix,
                title=_('Fields Update'),
                value=self.fields,
                workflow=self.get_workflow(),
            )

    def get_fields_parameter_view_value(self):
        result = []
        for field in self.fields:
            try:
                formdef_field = [
                    x for x in self.get_workflow().get_backoffice_fields() if x.id == field['field_id']
                ][0]
                result.append(htmltext('<li>%s → %s</li>') % (formdef_field.label, field['value']))
            except IndexError:
                result.append(htmltext('<li>#%s → %s</li>') % (field['field_id'], field['value']))
        return htmltext('<ul class="fields">%s</ul>') % htmltext('').join(result)

    def get_computed_strings(self):
        yield from super().get_computed_strings()
        for field in self.fields or []:
            yield field.get('value')

    def perform(self, formdata):
        if not self.fields:
            return

        old_data = copy.deepcopy(formdata.data)
        for field in self.fields:
            try:
                formdef_field = [
                    x for x in self.get_workflow().get_backoffice_fields() if x.id == field['field_id']
                ][0]
            except IndexError:
                continue

            if not field.get('value'):
                # assign empty value as None, as that will work for all field types
                new_value = None
            else:
                with get_publisher().complex_data():
                    try:
                        new_value = self.compute(
                            field['value'],
                            raises=True,
                            allow_complex=formdef_field.allow_complex,
                            formdata=formdata,
                            status_item=self,
                        )
                    except Exception:
                        continue
                    if formdef_field.allow_complex:
                        new_value = get_publisher().get_cached_complex_data(new_value)

            if formdef_field.convert_value_from_anything:
                try:
                    new_value = formdef_field.convert_value_from_anything(new_value)
                except ValueError as e:
                    if hasattr(e, 'get_error_summary'):
                        summary = _('Failed to assign field (%(id)s): %(summary)s') % {
                            'id': formdef_field.varname or field['field_id'],
                            'summary': e.get_error_summary(),
                        }
                    else:
                        summary = _('Failed to convert %(class)s value to %(kind)s field (%(id)s)') % {
                            'class': type(new_value),
                            'kind': formdef_field.get_type_label(),
                            'id': formdef_field.varname or field['field_id'],
                        }
                    expression_dict = self.get_expression(field['value'])
                    with get_publisher().error_context(
                        field_label=formdef_field.label, field_url=formdef_field.get_admin_url()
                    ):
                        get_publisher().record_error(
                            summary,
                            formdata=formdata,
                            status_item=self,
                            expression=expression_dict['value'],
                            expression_type=expression_dict['type'],
                            exception=e,
                        )
                    continue

            try:
                formdef_field.set_value(formdata.data, new_value, raise_on_error=True)
            except SetValueError as e:
                summary = _('Failed to set %(kind)s field (%(id)s), error: %(exc)s') % {
                    'kind': formdef_field.get_type_label(),
                    'id': field['field_id'],
                    'exc': e,
                }
                get_publisher().record_error(
                    summary,
                    formdata=formdata,
                    status_item=self,
                    exception=e,
                )
                continue

            # store formdata everytime so substitution cache is invalidated,
            # and backoffice field values can be used in subsequent fields.
            formdata.store()

        ContentSnapshotPart.take(formdata=formdata, old_data=old_data, source='set-backoffice-fields')
        formdata.store()

    def fields_export_to_xml(self, item, include_id=False):
        if not self.fields:
            return

        fields_node = ET.SubElement(item, 'fields')
        for field in self.fields:
            fields_node.append(FieldNode(field).export_to_xml(include_id=include_id))

        return fields_node

    def fields_init_with_xml(self, elem, include_id=False, snapshot=False):
        fields = []
        if elem is None:
            return
        for field_xml_node in elem.findall('field'):
            field_node = FieldNode()
            field_node.init_with_xml(field_xml_node, include_id=include_id, snapshot=snapshot)
            fields.append(field_node.as_dict())
        if fields:
            self.fields = fields

    def perform_in_tests(self, formdata):
        self.perform(formdata)


register_item_class(SetBackofficeFieldsWorkflowStatusItem)
