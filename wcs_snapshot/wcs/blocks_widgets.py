# w.c.s. - web application for online forms
# Copyright (C) 2005-2024  Entr'ouvert
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

from quixote import get_publisher, get_request
from quixote.html import htmltag, htmltext

from wcs import conditions
from wcs.qommon import _
from wcs.qommon.form import CompositeWidget, SingleSelectHintWidget, WidgetList


class BlockSubWidget(CompositeWidget):
    template_name = 'qommon/forms/widgets/block_sub.html'

    def __init__(self, name, value=None, *args, **kwargs):
        self.block = kwargs.pop('block')
        self.readonly = kwargs.get('readonly')
        self.remove_button = kwargs.pop('remove_button', False)
        self.remove_element_label = kwargs.pop('remove_element_label')
        self.index = kwargs.pop('index', 0)
        super().__init__(name, value, *args, **kwargs)

        def add_to_form(field):
            if 'readonly' in kwargs:
                field_value = None
                if value is not None:
                    field_value = value.get(field.id)
                return field.add_to_view_form(form=self, value=field_value)

            widget = field.add_to_form(form=self)
            if field.key in ['title', 'subtitle', 'comment']:
                return widget
            widget = self.get_widget('f%s' % field.id)
            if widget:
                widget.div_id = None
                widget.prefill_attributes = field.get_prefill_attributes()
            return widget

        self.fields = {}

        live_sources = []
        live_condition_fields = {}
        for field in self.block.fields:
            context = self.block.get_substitution_counter_variables(self.index)
            if field.key in ['title', 'subtitle', 'comment']:
                with get_publisher().substitutions.temporary_feed(context):
                    widget = add_to_form(field)
            else:
                widget = add_to_form(field)
                if getattr(get_publisher(), 'has_transient_formdata', False):
                    # [HAS_TRANSIENT_DATA] has_transient_data is an attribute set
                    # when adding fields to form. The normal field behaviour is to
                    # update the widget value according to transient_formdata after
                    # the widget has been added and this has to be mimicked here.
                    from wcs.formdef import FormDef

                    d = FormDef.get_field_data(field, widget)
                    block_var = (
                        get_publisher().substitutions.get_context_variables(mode='lazy').get('block_var')
                    )
                    if block_var:
                        block_var._data.update(d)
                    widget._parsed = False
                    widget.error = None

            varnames = []
            if field.condition:
                varnames.extend(field.get_condition_varnames(formdef=self.block))

            if field.prefill and field.prefill.get('type') == 'string':
                varnames.extend(
                    field.get_referenced_varnames(formdef=self.block, value=field.prefill.get('value', ''))
                )

            for varname in varnames:
                if varname not in live_condition_fields:
                    live_condition_fields[varname] = []
                live_condition_fields[varname].append(field)
            live_sources.extend(varnames)

            field.widget = widget
            self.fields[field.id] = widget

        for field in self.block.fields:
            if field.varname in live_sources:
                field.widget.live_condition_source = True
                field.widget.live_condition_fields = live_condition_fields[field.varname]

        if value:
            self.set_value(value)

        self.set_visibility(value)

    def set_visibility(self, value):
        with self.block.evaluation_context(value, self.index):
            for field in self.block.fields:
                widget = self.fields.get(field.id)
                if not widget:
                    continue
                visible = field.is_visible({}, formdef=None)
                widget.is_hidden = not (visible)

    def set_value(self, value):
        self.value = value
        for widget in self.get_widgets():
            if hasattr(widget, 'set_value') and not getattr(widget, 'secondary', False):
                widget.set_value(value.get(widget.field.id))

    def get_field_data(self, field, widget):
        from wcs.formdef import FormDef

        return FormDef.get_field_data(field, widget)

    def _parse(self, request):
        value = {}
        empty = True
        no_data_fields = True
        all_lists = True

        for widget in self.get_widgets():
            if widget.field.key in ['title', 'subtitle', 'comment']:
                continue
            if getattr(widget, 'secondary', False):
                continue
            no_data_fields = False
            with self.block.evaluation_context(value, self.index):
                widget_value = self.get_field_data(widget.field, widget)
                if not widget.field.is_visible({}, formdef=None):
                    widget.clear_error()
                    continue
            value.update(widget_value)
            empty_values = [None]
            if widget.field.key == 'bool' and not widget.field.is_required():
                # ignore unchecked checkboxes unless the field is required
                empty_values.append(False)
            if (
                widget.field.key == 'item'
                and isinstance(widget, SingleSelectHintWidget)
                and widget.separate_hint()
            ):
                # <select> will have its first option automatically selected,
                # do not consider it to mark the field as filled.
                empty_values.append(widget.options[0][0])
            else:
                all_lists = False
            if widget_value.get(widget.field.id) not in empty_values:
                empty = False

        if (not empty or no_data_fields) and self.block.post_conditions:
            error_messages = []
            with self.block.evaluation_context(value, self.index):
                for i, post_condition in enumerate(self.block.post_conditions):
                    condition = post_condition.get('condition')
                    try:
                        if conditions.Condition(condition, record_errors=False).evaluate():
                            continue
                    except RuntimeError:
                        pass
                    error_message = post_condition.get('error_message')
                    error_message = get_publisher().translate(error_message)

                    from wcs.workflows import WorkflowStatusItem

                    error_message = WorkflowStatusItem.compute(error_message, allow_ezt=False)
                    error_messages.append(error_message)
            if error_messages:
                self.set_error(' '.join(error_messages))

        if empty and not all_lists and not get_publisher().keep_all_block_rows_mode:
            value = None
            for widget in self.get_widgets():  # reset "required" errors
                if widget.error == self.REQUIRED_ERROR:
                    widget.clear_error()
        self.value = value

    def add_media(self):
        for widget in self.get_widgets():
            if hasattr(widget, 'add_media'):
                widget.add_media()


class BlockWidget(WidgetList):
    template_name = 'qommon/forms/widgets/block.html'
    always_include_add_button = True

    def __init__(
        self,
        name,
        value=None,
        title=None,
        block=None,
        field=None,
        default_items_count=None,
        max_items=None,
        add_element_label=None,
        **kwargs,
    ):
        self.block = block
        self.field = field
        self.readonly = kwargs.get('readonly')
        self.label_display = kwargs.pop('label_display') or 'normal'
        self.remove_button = kwargs.pop('remove_button', False)
        self.remove_element_label = kwargs.pop('remove_element_label', None)
        if self.remove_element_label:
            self.remove_element_label = get_publisher().translate(self.remove_element_label)
        else:
            self.remove_element_label = _('Remove')
        if add_element_label:
            add_element_label = get_publisher().translate(add_element_label)
        else:
            add_element_label = _('Add another')
        element_values = None
        if value:
            element_values = value.get('data')

        from wcs.workflows import WorkflowStatusItem

        try:
            max_items = int(WorkflowStatusItem.compute(max_items, allow_ezt=False)) or 1
        except (TypeError, ValueError):
            max_items = 1

        try:
            default_items_count = min(
                int(WorkflowStatusItem.compute(default_items_count, allow_ezt=False) or 1), max_items
            )
        except (TypeError, ValueError):
            default_items_count = 1

        hint = kwargs.pop('hint', None)
        element_kwargs = {
            'block': self.block,
            'render_br': False,
            'remove_button': self.remove_button,
            'remove_element_label': self.remove_element_label,
        }
        element_kwargs.update(kwargs)
        super().__init__(
            name,
            value=element_values,
            title=title,
            default_items_count=default_items_count,
            max_items=max_items,
            element_type=BlockSubWidget,
            element_kwargs=element_kwargs,
            add_element_label=add_element_label,
            hint=hint,
            **kwargs,
        )

    @property
    def a11y_labelledby(self):
        return bool(self.a11y_role)

    @property
    def a11y_role(self):
        # don't mark block as a group if it has no label
        if self.label_display != 'hidden':
            return 'group'
        return None

    def add_element(self, value=None, element_name=None):
        row_index = len(self.element_names)
        try:
            block_data = (
                get_publisher()
                .substitutions.get_context_variables(mode='lazy')['form']
                ._formdata.data.get(self.field.id)
            )
            row_data = block_data['data'][row_index]
        except (AttributeError, IndexError, KeyError, TypeError):
            # AttributeError happens if ['form'] is not yet a formdata
            # (is a LazyFormDef).
            row_data = {}
        with self.block.evaluation_context(row_data, row_index):
            super().add_element(value=value, element_name=element_name)

    def set_value(self, value):
        from .fields.block import BlockRowValue

        if isinstance(value, BlockRowValue):
            value = value.make_value(block=self.block, field=self.field, data={})
        if isinstance(value, dict) and 'data' in value:
            super().set_value(value['data'])
            self.value = value
        else:
            self.value = None

    def _parse(self, request):
        # iterate over existing form keys to get actual list of elements.
        # (maybe this could be moved to WidgetList)
        prefix = '%s$element' % self.name
        known_prefixes = {x.split('$', 2)[1] for x in request.form.keys() if x.startswith(prefix)}
        for prefix in known_prefixes:
            if prefix not in self.element_names:
                self.add_element(element_name=prefix)
        super()._parse(request)
        if self.value:
            self.value = {'data': self.value}
            # keep "schema" next to data, this allows custom behaviour for
            # date fields (time.struct_time) when writing/reading from
            # database in JSON.
            self.value['schema'] = {x.id: x.key for x in self.block.fields}

    def unparse(self):
        self._parsed = False
        for widget in self.widgets:
            widget._parsed = False

    def parse(self, request=None):
        if not self._parsed:
            self._parsed = True
            if request is None:
                request = get_request()
            self._parse(request)
            if self.required and self.value is None:
                self.set_error(_(self.REQUIRED_ERROR))
            for widget in self.widgets:
                # mark required rows with a special attribute, to avoid doubling the
                # error messages in the template.
                widget.is_required_error = bool(widget.error == self.REQUIRED_ERROR)
        return self.value

    def add_media(self):
        for widget in self.get_widgets():
            if hasattr(widget, 'add_media'):
                widget.add_media()

    def get_error(self, request=None):
        request = request or get_request()
        if request.get_method() == 'POST':
            self.parse(request=request)
        return self.error

    def has_error(self, request=None):
        if self.get_error():
            return True
        # we know subwidgets have been parsed
        has_error = False
        for widget in self.widgets:
            if widget.value is None:
                continue
            if widget.has_error():
                has_error = True
        return has_error

    def render_title(self, title):
        attrs = {'id': 'form_label_%s' % self.get_name_for_id()}
        if not title or self.label_display == 'hidden':
            # add a tag even if there's no label to display as it's used as an anchor point
            # for links to errors.
            return htmltag('div', **attrs) + htmltext('</div>')

        if self.label_display == 'normal':
            return super().render_title(title)

        if self.required:
            title += htmltext('<span title="%s" class="required">*</span>') % _('This field is required.')
        hint = self.get_hint()
        if hint:
            attrs['aria-describedby'] = 'form_hint_%s' % self.name
        title_tag = htmltag('h4', **attrs)
        return title_tag + htmltext('%s</h4>') % title

    def had_add_clicked(self):
        add_widget = self.get_widget('add_element')
        request = get_request()
        request_form = getattr(request, 'orig_form', request.form)
        return request_form.get(add_widget.name) if add_widget else False

    def get_row_widgets(self, name):
        for widget in self.widgets:
            if not isinstance(widget, BlockSubWidget):
                continue
            sub_widget = widget.get_widget(name)
            if sub_widget:
                yield sub_widget
