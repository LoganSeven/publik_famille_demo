# w.c.s. - web application for online forms
# Copyright (C) 2005-2023  Entr'ouvert
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

from django.utils.encoding import force_str
from quixote import get_publisher

from wcs.blocks_widgets import BlockWidget
from wcs.conditions import ValidationError
from wcs.qommon import _
from wcs.qommon.fields import get_summary_field_details
from wcs.qommon.form import CheckboxWidget, ComputedExpressionWidget, SingleSelectWidget, StringWidget

from .base import SetValueError, WidgetField
from .item import UnknownCardValueError


class IntComputedExpressionWidget(ComputedExpressionWidget):
    extra_css_class = 'ComputedExpressionWidget'

    @classmethod
    def validate(cls, expression, initial_value=None):
        from wcs.workflows import WorkflowStatusItem

        expression = WorkflowStatusItem.get_expression(expression)
        cls.validate_template(expression['value'])
        if not expression.get('type') == 'template':
            try:
                int(expression.get('value'))
            except (TypeError, ValueError):
                raise ValidationError(_('value must be a number or a template'))


class MissingBlockFieldError(Exception):
    def __init__(self, block_slug):
        self.block_slug = block_slug

    def __str__(self):
        return force_str(_('Missing block field: %s') % self.block_slug)


class BlockRowValue:
    # a container for a value that will be added as a "line" of a block
    def __init__(self, append=False, merge=False, existing=None, **kwargs):
        self.append = append
        self.merge = merge
        self.attributes = kwargs
        self.rows = None
        if append is True:
            self.rows = getattr(existing, 'rows', None) or []
            self.rows.append(kwargs)

    def check_current_value(self, current_block_value):
        return (
            isinstance(current_block_value, dict)
            and 'data' in current_block_value
            and isinstance(current_block_value['data'], list)
        )

    def make_value(self, block, field, data):
        def make_row_data(attributes):
            row_data = {}
            for sub_field in block.fields:
                if sub_field.varname and sub_field.varname in attributes:
                    sub_value = attributes.get(sub_field.varname)
                    if sub_field.convert_value_from_anything:
                        sub_value = sub_field.convert_value_from_anything(sub_value)
                    sub_field.set_value(row_data, sub_value)
            return row_data

        try:
            row_data = make_row_data(self.attributes)
        except (UnknownCardValueError, SetValueError, ValueError) as e:
            get_publisher().record_error(_('invalid value when creating block: %s') % str(e), exception=e)
            return None

        current_block_value = data.get(field.id)
        if not self.check_current_value(current_block_value):
            current_block_value = None
        if self.append and current_block_value:
            block_value = current_block_value
            block_value['data'].append(row_data)
        elif self.merge is not False and field.id in data:
            block_value = current_block_value
            try:
                merge_index = -1 if self.merge is True else int(self.merge)
                block_value['data'][merge_index].update(row_data)
            except (ValueError, IndexError, TypeError):
                # ValueError if self.merge is not an integer,
                # IndexError if merge_index is out of range.
                # TypeError if block_value was None
                pass  # ignore
        elif self.rows:
            rows_data = [make_row_data(x) for x in self.rows if x]
            block_value = {'data': rows_data, 'schema': {x.id: x.key for x in block.fields}}
        else:
            block_value = {'data': [row_data], 'schema': {x.id: x.key for x in block.fields}}
        return block_value


class BlockField(WidgetField):
    key = 'block'
    allow_complex = True

    widget_class = BlockWidget
    default_items_count = '1'
    max_items = '1'
    extra_attributes = [
        'block',
        'default_items_count',
        'field',
        'max_items',
        'add_element_label',
        'remove_element_label',
        'label_display',
        'remove_button',
    ]
    add_element_label = ''
    remove_element_label = ''
    label_display = 'normal'
    remove_button = False
    block_slug = None

    # cache
    _block = None

    def migrate(self):
        changed = super().migrate()
        if not self.block_slug:  # 2023-05-21
            self.block_slug = self.type.removeprefix('block:')
            changed = True
        return changed

    @property
    def field(self):
        # declared in 'extra_attributs' so it will get passed to BlockWidget
        # where it is required in the add_element method.
        return self

    @property
    def block(self):
        if self._block:
            return self._block
        from wcs.blocks import BlockDef

        self._block = BlockDef.get_on_index(self.block_slug, 'slug')
        return self._block

    def get_type_label(self):
        try:
            return _('Block of fields (%s)') % self.block.name
        except KeyError:
            return _('Block of fields (%s, missing)') % self.block_slug

    def get_dependencies(self):
        yield from super().get_dependencies()
        try:
            yield self.block
        except KeyError:
            pass

    def i18n_scan(self, base_location):
        yield from super().i18n_scan(base_location)
        location = '%s%s/' % (base_location, self.id)
        yield location, None, self.add_element_label
        yield location, None, self.remove_element_label

    def add_to_form(self, form, value=None):
        try:
            self.block
        except KeyError:
            raise MissingBlockFieldError(self.block_slug)
        return super().add_to_form(form, value=value)

    def fill_admin_form(self, form, formdef):
        super().fill_admin_form(form, formdef)
        form.add(
            IntComputedExpressionWidget,
            'default_items_count',
            title=_('Number of items to display by default'),
            value=str(self.default_items_count or ''),
            required=True,
        )
        form.add(
            IntComputedExpressionWidget,
            'max_items',
            title=_('Maximum number of items'),
            value=str(self.max_items or ''),
            required=True,
        )
        form.add(
            StringWidget,
            'add_element_label',
            title=_('Label of "Add" button'),
            value=self.add_element_label,
            hint=_('If left empty, the default label will be used (%s).') % _('Add another'),
        )
        display_options = [
            ('normal', _('Normal')),
            ('subtitle', _('Subtitle')),
            ('hidden', _('Hidden')),
        ]
        form.add(
            SingleSelectWidget,
            'label_display',
            title=_('Label display'),
            value=self.label_display or 'normal',
            options=display_options,
        )
        form.add(
            CheckboxWidget,
            'remove_button',
            title=_('Include remove button'),
            value=self.remove_button,
            attrs={'data-dynamic-display-parent': 'true'},
        )
        form.add(
            StringWidget,
            'remove_element_label',
            title=_('Label of "Remove" button'),
            value=self.remove_element_label,
            hint=_('If left empty, the default label will be used (%s).') % _('Remove'),
            attrs={
                'data-dynamic-display-child-of': 'remove_button',
                'data-dynamic-display-checked': 'true',
            },
        )

    def get_admin_attributes(self):
        return super().get_admin_attributes() + [
            'default_items_count',
            'max_items',
            'add_element_label',
            'label_display',
            'remove_button',
            'remove_element_label',
            'block_slug',  # only mentioned for xml export/import
        ]

    def store_display_value(self, data, field_id, raise_on_error=False):
        value = data.get(field_id)
        parts = []
        if value and value.get('data'):
            if self.block.digest_template:
                value['digests'] = []
            for subvalue in value.get('data'):
                digest = self.block.get_display_value(subvalue) or ''
                if self.block.digest_template:
                    # store a per-row copy of digest
                    value['digests'].append(digest)
                parts.append(digest)
        return ', '.join(parts)

    def get_value_details(self, formdata, value, include_unset_required_fields, wf_form=False):
        for i, row_value in enumerate((value or {}).get('data') or []):
            try:
                block = self.block
            except KeyError:
                # block was deleted, ignore
                continue
            context = block.get_substitution_counter_variables(i)
            with get_publisher().substitutions.temporary_feed(context):
                yield from get_summary_field_details(
                    formdata,
                    fields=block.fields,
                    include_unset_required_fields=include_unset_required_fields,
                    data=row_value,
                    parent_field=self,
                    parent_field_index=i,
                    wf_form=wf_form,
                )

    def get_view_value(self, value, summary=False, include_unset_required_fields=False, **kwargs):
        return str(value or '')

    def get_value_info(self, data, wf_form=False):
        value = data.get(self.id)
        if value and not any(x for x in value.get('data') or []):
            # skip if there are no values
            return (None, {})
        value_info, value_details = super().get_value_info(data, wf_form)
        if value_info is None and value_details not in (None, {'value_id': None}):
            # buggy digest template created an empty value, switch it to an empty string
            # so it's not considered empty in summary page.
            value_info = ''
        return (value_info, value_details)

    def get_max_items(self):
        try:
            return int(self.max_items or 1)
        except ValueError:  # template
            return 1

    def get_csv_heading(self, subfield=None):
        nb_items = self.get_max_items()
        label = self.label
        if subfield:
            headings = [f'{label} - {x}' for x in subfield.get_csv_heading()]
            label += ' - %s' % subfield.label
        else:
            headings = [label]
        if nb_items == 1:
            return headings
        base_headings = headings[:]
        headings = []
        for i in range(nb_items):
            headings.extend([f'{x} - {i + 1}' for x in base_headings])
        return headings

    def get_csv_value(self, element, **kwargs):
        nb_items = self.get_max_items()
        cells = [''] * nb_items
        if element and element.get('data'):
            for i, subvalue in enumerate(element.get('data')[:nb_items]):
                if subvalue:
                    cells[i] = self.block.get_display_value(subvalue)
        return cells

    def set_value(self, data, value, **kwargs):
        if value == '':
            value = None
        if isinstance(value, BlockRowValue):
            value = value.make_value(block=self.block, field=self, data=data)
        elif value and not (isinstance(value, dict) and 'data' in value and 'schema' in value):
            raise SetValueError(_('invalid value for block (field id: %s)') % self.id)
        elif value:
            value = copy.deepcopy(value)
        super().set_value(data, value, **kwargs)

    def get_json_value(self, value, **kwargs):
        from wcs.formdata import FormData

        result = []
        if not value or not value.get('data'):
            return result
        for i, subvalue_data in enumerate(value.get('data')):
            result.append(
                FormData.get_json_data_dict(
                    subvalue_data,
                    self.block.fields,
                    formdata=kwargs.get('formdata'),
                    include_files=kwargs.get('include_file_content'),
                    include_unnamed_fields=True,
                    parent_field=self,
                    parent_field_index=i,
                )
            )
        return result

    def from_json_value(self, value):
        from wcs.api import posted_json_data_to_formdata_data

        result = []
        if isinstance(value, list):
            for subvalue_data in value or []:
                result.append(posted_json_data_to_formdata_data(self.block, subvalue_data))

        return {'data': result, 'schema': {x.id: x.key for x in self.block.fields}}

    def default_items_count_init_with_xml(self, el, include_id=False, snapshot=False):
        if el is None or el.text is None:
            self.default_items_count = BlockField.default_items_count
        else:
            self.default_items_count = str(el.text)

    def max_items_init_with_xml(self, el, include_id=False, snapshot=False):
        if el is None or el.text is None:
            self.max_items = BlockField.max_items
        else:
            self.max_items = str(el.text)

    def __getstate__(self):
        # do not store _block cache
        odict = super().__getstate__()
        odict.pop('_block', None)
        return odict

    def __setstate__(self, ndict):
        # make sure a cached copy of _block is not restored
        self.__dict__ = ndict
        self._block = None
