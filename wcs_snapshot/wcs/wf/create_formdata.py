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

import collections
import xml.etree.ElementTree as ET

from django.utils.functional import cached_property
from django.utils.timezone import localtime
from quixote import get_publisher, get_request, get_session
from quixote.html import TemplateIO, htmltext

from wcs.fields.block import BlockRowValue
from wcs.formdef import FormDef
from wcs.qommon import _, ngettext, pgettext
from wcs.qommon.form import (
    CheckboxWidget,
    CompositeWidget,
    ComputedExpressionWidget,
    Form,
    HtmlWidget,
    RadiobuttonsWidget,
    SingleSelectWidget,
    StringWidget,
    VarnameWidget,
    WidgetListAsTable,
)
from wcs.qommon.substitution import CompatibilityNamesDict
from wcs.tracking_code import TrackingCode
from wcs.workflows import AbortOnRemovalException, EvolutionPart, WorkflowStatusItem, register_item_class


class Mapping:
    field_id = None
    expression = None

    def __init__(self, field_id, expression):
        self.field_id = field_id
        self.expression = expression


class MappingWidget(CompositeWidget):
    value_placeholder = _('Leaving the field blank will empty the value.')
    expression_widget_title = _('Expression')

    def __init__(self, name, value=None, to_formdef=None, cached_field_labels=None, **kwargs):
        value = value or Mapping(None, '')
        super().__init__(name, value, **kwargs)

        to_fields = self._fields_to_options(to_formdef)

        if value and value.field_id not in [x[0] for x in to_fields]:
            old_label = (cached_field_labels or {}).get(value.field_id) or _('Unknown')
            error_option = '❗ %s (%s)' % (old_label, _('deleted field'))
            to_fields.append((value.field_id, error_option, value.field_id))
        self.add(
            SingleSelectWidget, name='field_id', title=_('Field'), value=value.field_id, options=to_fields
        )

        self.add(
            ComputedExpressionWidget,
            name='expression',
            title=self.expression_widget_title,
            value=value.expression,
            value_placeholder=self.value_placeholder,
        )

    def _fields_to_options(self, formdef):
        label_counters = {}
        for field in formdef.get_data_fields():
            label = f'{field.label} - {field.get_type_label()}'
            label_counters.setdefault(label, 0)
            label_counters[label] += 1
        repeated_labels = {x for x, y in label_counters.items() if y > 1}

        options = [(None, '---', '')]
        for field in formdef.get_data_fields():
            label = f'{field.label} - {field.get_type_label()}'
            block_label = field.label
            if label in repeated_labels and field.varname:
                label = f'{field.label} - {field.get_type_label()} ({field.varname})'
                block_label = f'{field.label} ({field.varname})'  # do not repeat block type
            options.append((field.id, label, str(field.id)))
            if field.key == 'block':
                for subfield in field.block.get_data_fields():
                    options.append(
                        (
                            f'{field.id}${subfield.id}',
                            f'{block_label} - {subfield.label} - {subfield.get_type_label()}',
                            f'{field.id}${subfield.id}',
                        )
                    )
        return options

    def _parse(self, request):
        super()._parse(request)
        if self.get('field_id') is not None:
            self.value = Mapping(field_id=self.get('field_id'), expression=self.get('expression'))
        else:
            self.value = None


class MappingsWidget(WidgetListAsTable):
    readonly = False
    element_type = MappingWidget

    # widget_list.js does not work with ComputedExpressionWidget,
    # so we revert to quixote behaviour for adding a line
    def add_media(self):
        pass

    def __init__(self, name, to_formdef=None, **kwargs):
        self.to_formdef = to_formdef

        value = kwargs.get('value')
        if value:
            # reorder mappings based on to_formdef fields order
            value.sort(key=lambda mapping: self.ranks.get(str(mapping.field_id), 9999))

        cached_field_labels = kwargs.pop('cached_field_labels', None)
        super().__init__(
            name,
            element_type=self.element_type,
            element_kwargs={
                'to_formdef': to_formdef,
                'cached_field_labels': cached_field_labels,
            },
            **kwargs,
        )

    @cached_property
    def ranks(self):
        ranks = {}
        i = 0
        for field in self.to_formdef.get_data_fields():
            ranks[str(field.id)] = i
            i += 1
            if field.key == 'block':
                for subfield in field.block.get_data_fields():
                    ranks[f'{field.id}${subfield.id}'] = i
                    i += 1
        return ranks

    def _parse(self, request):
        super()._parse(request)

        if self.value:
            # prevent many mappings to the same field
            if len({mapping.field_id for mapping in self.value}) != len(self.value):
                self.error = _('Some destination fields are duplicated')
                return

            # reorder mappings based on to_formdef fields order
            self.value.sort(key=lambda mapping: self.ranks.get(str(mapping.field_id), 9999))


class JournalAssignationErrorPart(EvolutionPart):
    summary = None
    label = None
    render_for_fts = None

    def __init__(self, summary, label=None):
        self.summary = summary
        self.label = label

    def is_hidden(self):
        return not (get_request() and get_request().get_path().startswith('/backoffice/'))

    def view(self, **kwargs):
        r = TemplateIO(html=True)
        r += htmltext('<div class="assignation-error">')
        r += htmltext('<h4 class="foldable folded">')
        r += str(_('Assignation error during action "%s"') % self.label)
        r += htmltext('</h4>')
        r += htmltext('<div>')
        r += htmltext('<p>%s</p>\n') % self.summary
        r += htmltext('</div>')
        r += htmltext('</div>')
        return r.getvalue()

    def get_json_export_dict(self, anonymise=False, include_files=True):
        d = {
            'type': 'assignation-error',
        }
        if not anonymise:
            d.update(
                {
                    'summary': self.summary,
                    'label': self.label,
                }
            )
        return d


class LinkedFormdataEvolutionPart(EvolutionPart):
    formdef_class = FormDef
    attach_to_history = False
    render_for_fts = None

    # mark self.formdata_id as created as formdata.get_natural_key()
    # (and not the legacy formdata.id)
    formdata_id_is_natural = False

    def __init__(self, formdata, varname, attach_to_history):
        self._formdef = formdata.formdef
        self._formdata = formdata
        self.formdef_id = formdata.formdef.id
        self.formdata_id = formdata.get_natural_key()
        self.formdata_id_is_natural = True
        self.varname = varname
        self.attach_to_history = attach_to_history

    @property
    def formdef(self):
        return self.formdef_class.cached_get(self.formdef_id, ignore_errors=True, ignore_migration=True)

    @property
    def formdata(self):
        if not hasattr(self, '_formdata'):
            formdef = self.formdef
            if formdef and not self.formdata_id_is_natural:
                self._formdata = formdef.data_class().get(self.formdata_id, ignore_errors=True)
            elif formdef:
                self._formdata = formdef.data_class().get_by_id(self.formdata_id, ignore_errors=True)
            else:
                self._formdata = None  # removed formdef
        return self._formdata

    def __getstate__(self):
        # Forget cached values
        return {k: v for k, v in self.__dict__.items() if not k.startswith('_')}

    def __repr__(self):
        return '<%s %s "%s-%s">' % (
            self.__class__.__name__,
            self.formdef_class.__name__,
            self.formdef_id,
            self.formdata_id,
        )

    @classmethod
    def get_substitution_variables(cls, formdata):
        d = {}
        for part in formdata.iter_evolution_parts(klass=cls):
            if part.formdata:
                d['form_links_%s' % (part.varname or '*')] = part
        return d

    def is_hidden(self):
        return not bool(get_request() and get_request().user)

    def view(self, **kwargs):
        if self.attach_to_history:
            try:
                formdata = self.formdata
                if formdata is None:
                    raise KeyError
            except KeyError:
                # formdef or formdata deleted
                return htmltext('<p class="wf-links">%s (%s, %s-%s)</p>') % (
                    _('New form created'),
                    _('deleted'),
                    self.formdef_id,
                    self.formdata_id,
                )
            result = htmltext('<p class="wf-links">')
            result += htmltext(_('New form "%s" created:') % self.formdef.name)
            result += htmltext(' <a href="%s">%s</a>') % (
                formdata.get_url(backoffice=bool(get_request() and get_request().is_in_backoffice())),
                formdata.get_display_label(include_form_name=False),
            )
            result += htmltext('</p>')
            return result
        return ''


class LazyFormDataLinks:
    def __init__(self, formdata):
        self._formdata = formdata

    def __getattr__(self, varname):
        linked_formdatas = []
        for part in self._formdata.iter_evolution_parts(LinkedFormdataEvolutionPart):
            if part.varname == varname and part.formdata:
                linked_formdatas.append(LazyFormDataLinksItem(part.formdata.get_as_lazy()))
        if linked_formdatas:
            return LazyFormDataLinksItems(linked_formdatas)
        raise AttributeError(varname)

    def inspect_keys(self):
        seen = set()
        for part in self._formdata.iter_evolution_parts(LinkedFormdataEvolutionPart):
            if part.varname and part.formdata and part.varname not in seen:
                seen.add(part.varname)
                yield part.varname


class LazyFormDataLinksItems:
    inspect_collapse = True

    def __init__(self, linked_formdatas):
        self._linked_formdatas = linked_formdatas

    def inspect_keys(self):
        return [str(x) for x in range(len(self._linked_formdatas))] + ['form']

    def __str__(self):
        return str(self._linked_formdatas[-1]['form'].internal_id)

    @property
    def form(self):
        # alias when there's a single linked formdata
        return self._linked_formdatas[-1]['form']

    def __getattr__(self, varname):
        formdata = self._linked_formdatas[-1]['form']
        return getattr(formdata, varname)

    def __getitem__(self, key):
        try:
            key = int(key)
        except ValueError:
            try:
                return getattr(self, key)
            except AttributeError:
                return self._linked_formdatas[-1][key]
        return self._linked_formdatas[key]

    def __len__(self):
        return len(self._linked_formdatas)

    def __iter__(self):
        yield from self._linked_formdatas


class LazyFormDataLinksItem:
    inspect_collapse = True

    def __init__(self, formdata):
        self._formdata = formdata

    def inspect_keys(self):
        return ['form']

    def __str__(self):
        return str(self._formdata.internal_id)

    @property
    def form(self):
        return self._formdata

    def __getitem__(self, key):
        try:
            return getattr(self, key)
        except AttributeError:
            compat_dict = CompatibilityNamesDict({'form': self._formdata})
            return compat_dict[key]


class CreateFormdataWorkflowStatusItem(WorkflowStatusItem):
    description = _('New Form Creation')
    key = 'create_formdata'
    category = 'formdata-action'
    support_substitution_variables = True

    formdef_class = FormDef
    evolution_part_class = LinkedFormdataEvolutionPart
    workflow_trace_event = 'workflow-created-formdata'
    workflow_test_data_attribute = 'created_formdata'

    formdef_slug = None
    formdef_label = _('Form')
    mappings_label = _('Mappings to new form fields')
    varname_hint = _('This is used to get linked forms in expressions.')
    user_association_option_label = _('User to associate to form')

    action_label = None
    draft = False
    backoffice_submission = False
    user_association_mode = None
    user_association_template = None
    keep_submission_context = False
    mappings = None
    varname = None
    map_fields_by_varname = False
    attach_to_history = False
    cached_field_labels = None
    draft_edit_operation_mode = 'full'  # or 'single' or 'partial'
    page_identifier = None

    def migrate(self):
        changed = super().migrate()
        if getattr(self, 'keep_user', False) is True:  # 2021-03-15
            self.user_association_mode = 'keep-user'
            delattr(self, 'keep_user')
            changed = True
        return changed

    def _resolve_formdef_slug(self, formdef_slug):
        if formdef_slug:
            return self.formdef_class.get_by_urlname(formdef_slug, use_cache=True, ignore_errors=True)
        return None

    @property
    def formdef(self):
        return self._resolve_formdef_slug(self.formdef_slug)

    def get_inspect_parameters(self):
        parameters = super().get_inspect_parameters()
        if self.user_association_mode != 'custom' and 'user_association_template' in parameters:
            parameters.remove('user_association_template')
        if not self.draft:
            if 'draft_edit_operation_mode' in parameters:
                parameters.remove('draft_edit_operation_mode')
            if 'page_identifier' in parameters:
                parameters.remove('page_identifier')
        if self.draft_edit_operation_mode not in ('single', 'partial') and 'page_identifier' in parameters:
            parameters.remove('page_identifier')
        return parameters

    def add_parameters_widgets(self, form, parameters, prefix='', formdef=None, **kwargs):
        super().add_parameters_widgets(form, parameters, prefix=prefix, formdef=formdef, **kwargs)
        if 'action_label' in parameters:
            form.add(
                StringWidget,
                '%saction_label' % prefix,
                size=40,
                title=_('Action Label'),
                value=self.action_label,
            )
        if 'formdef_slug' in parameters:
            list_forms = [(None, '---', '', {})]
            list_forms += [
                (x.url_name, x.name, x.url_name, {'data-goto-url': x.get_admin_url()})
                for x in self.formdef_class.select(order_by='name')
                if not x.disabled or x.url_name == self.formdef_slug
            ]
            if not get_publisher().get_backoffice_root().is_accessible(self.formdef_class.backoffice_section):
                # do not include goto url if section is not accessible
                list_forms = [(x[0], x[1], x[2], {}) for x in list_forms]
            form.add(
                SingleSelectWidget,
                '%sformdef_slug' % prefix,
                title=self.formdef_label,
                value=self.formdef_slug,
                options=list_forms,
                **{'data-autocomplete': 'true'},
            )
        if 'draft' in parameters:
            form.add(
                CheckboxWidget,
                '%sdraft' % prefix,
                title=_('Create new draft'),
                value=self.draft,
                attrs={'data-dynamic-display-parent': 'true'},
                tab=('draft', _('Draft')),
            )
        if 'draft_edit_operation_mode' in parameters:
            form.add(
                RadiobuttonsWidget,
                '%sdraft_edit_operation_mode' % prefix,
                title=_('Operation mode when a draft is created'),
                options=[
                    ('full', _('All pages'), 'full'),
                    ('single', _('Single page'), 'single'),
                    ('partial', _('From specific page'), 'partial'),
                ],
                tab=('draft', _('Draft')),
                value=self.draft_edit_operation_mode,
                attrs={
                    'data-dynamic-display-parent': 'true',
                    'data-dynamic-display-child-of': f'{prefix}draft',
                    'data-dynamic-display-checked': 'true',
                },
                extra_css_class='widget-inline-radio',
                default_value=self.__class__.draft_edit_operation_mode,
            )
        if 'page_identifier' in parameters:
            form.add(
                StringWidget,
                '%spage_identifier' % prefix,
                title=_('Page Identifier'),
                value=self.page_identifier,
                tab=('draft', _('Draft')),
                attrs={
                    'data-dynamic-display-child-of': '%sdraft_edit_operation_mode' % prefix,
                    'data-dynamic-display-value-in': 'single|partial',
                },
            )
        if 'backoffice_submission' in parameters:
            form.add(
                CheckboxWidget,
                '%sbackoffice_submission' % prefix,
                title=_('Backoffice submission'),
                value=self.backoffice_submission,
                advanced=True,
            )
        if 'user_association_mode' in parameters:
            form.add(
                RadiobuttonsWidget,
                '%suser_association_mode' % prefix,
                title=self.user_association_option_label,
                options=[
                    (None, _('None'), 'none'),
                    ('keep-user', _('Keep Current User'), 'keep-user'),
                    ('custom', _('Custom (user will come from template value)'), 'custom'),
                ],
                value=self.user_association_mode,
                attrs={'data-dynamic-display-parent': 'true'},
                advanced=True,
            )
        if 'user_association_template' in parameters:
            form.add(
                ComputedExpressionWidget,
                '%suser_association_template' % prefix,
                title=_('Template for user association (via email or NameID)'),
                value=self.user_association_template,
                attrs={
                    'data-dynamic-display-child-of': '%suser_association_mode' % prefix,
                    'data-dynamic-display-value': 'custom',
                },
                advanced=True,
            )
        if 'keep_submission_context' in parameters:
            form.add(
                CheckboxWidget,
                '%skeep_submission_context' % prefix,
                title=_('Keep submission context'),
                value=self.keep_submission_context,
                advanced=True,
            )
        formdef_slug = form.get('%sformdef_slug' % prefix)
        formdef = self._resolve_formdef_slug(formdef_slug)
        if 'mappings' in parameters and formdef:
            widget = form.add(
                MappingsWidget,
                '%smappings' % prefix,
                title=self.mappings_label,
                to_formdef=formdef,
                value=self.mappings,
                cached_field_labels=self.cached_field_labels,
            )
            if form.is_submitted() and get_request().form.get('map_fields_by_varname') != 'yes':
                # do not validate form if formdef is changed and there is no mappings
                if formdef_slug != self.formdef_slug and not widget.parse():
                    form.get_widget('%smappings' % prefix).set_error(_('Please define new mappings'))
        if 'varname' in parameters:
            form.add(
                VarnameWidget,
                '%svarname' % prefix,
                title=_('Identifier'),
                value=self.varname,
                hint=self.varname_hint,
                advanced=True,
            )
        if 'map_fields_by_varname' in parameters and formdef:
            form.add(
                CheckboxWidget,
                '%smap_fields_by_varname' % prefix,
                title=_('Map fields by varname'),
                value=self.map_fields_by_varname,
                advanced=True,
            )
            workflow_formdefs = self.get_workflow().formdefs()
            if self.map_fields_by_varname and self.formdef and workflow_formdefs:
                common_varnames = self.get_common_varnames(workflow_formdefs)
                if common_varnames:
                    common_varnames = htmltext(', ').join(
                        [htmltext('<tt>%s</tt>') % x for x in common_varnames]
                    )
                else:
                    common_varnames = htmltext('<i>%s</i>') % pgettext('identifier', 'none')
                form.add(
                    HtmlWidget,
                    name='common_varnames',
                    title=htmltext('<div class="infonotice common-varnames">%s %s</div>')
                    % (_('Common varnames:'), common_varnames),
                    advanced=True,
                )
        if 'attach_to_history' in parameters:
            form.add(
                CheckboxWidget,
                '%sattach_to_history' % prefix,
                title=_('Include new form in the form history'),
                value=self.attach_to_history,
            )

        if kwargs.get('orig') == 'variable_widget':
            return

        errors = [w.name for w in form.get_all_widgets() if w.has_error()]
        if set(errors) == {'%smappings' % prefix}:
            form.ERROR_NOTICE = _('This action is configured in two steps. See below for details.')
        else:
            form.ERROR_NOTICE = Form.ERROR_NOTICE

    def submit_admin_form(self, form):
        super().submit_admin_form(form)
        # keep a cache of field labels, to be used in case of errors if fields are removed
        mapped_field_ids = [x.field_id for x in self.mappings or []]
        if self.formdef:
            self.cached_field_labels = {}
            for field in self.formdef.get_data_fields():
                if field.id in mapped_field_ids:
                    self.cached_field_labels[field.id] = field.label
                if field.key == 'block':
                    for subfield in field.block.get_data_fields():
                        mapped_subfield_id = f'{field.id}${subfield.id}'
                        if mapped_subfield_id in mapped_field_ids:
                            self.cached_field_labels[mapped_subfield_id] = f'{field.label} - {subfield.label}'
        if not self.draft:
            # cleanup
            if 'draft_edit_operation_mode' in self.get_parameters():
                delattr(self, 'draft_edit_operation_mode')
            if 'page_identifier' in self.get_parameters():
                delattr(self, 'page_identifier')

    def get_mappings_parameter_view_value(self):
        to_id_fields = {str(field.id): field for field in self.formdef.get_data_fields()}
        result = []
        for mapping in self.mappings or []:
            try:
                dest_field = to_id_fields[str(mapping.field_id)]
                result.append(htmltext('<li>%s → %s</li>') % (dest_field.label, mapping.expression))
            except KeyError:
                result.append(htmltext('<li>#%s → %s</li>') % (mapping.field_id, mapping.expression))
        return htmltext('<ul class="mappings">%s</ul>') % htmltext('').join(result)

    def get_common_varnames(self, workflow_formdefs):
        # Compute common varnames between the targeted formdef and all formdefs related
        # to the parent workflow.
        assert self.formdef
        varnames = {
            field.varname
            for formdef in workflow_formdefs
            for field in formdef.get_data_fields()
            if field.varname
        }
        return sorted(varnames & {field.varname for field in self.formdef.get_data_fields() if field.varname})

    def get_parameters(self):
        return (
            'action_label',
            'formdef_slug',
            'map_fields_by_varname',
            'mappings',
            'backoffice_submission',
            'draft',
            'draft_edit_operation_mode',
            'page_identifier',
            'user_association_mode',
            'user_association_template',
            'keep_submission_context',
            'varname',
            'attach_to_history',
            'condition',
        )

    def get_line_details(self):
        if not self.formdef or not (self.mappings or self.map_fields_by_varname):
            return _('not configured')
        if self.action_label:
            return self.action_label
        return self.formdef.name

    def assign_user(self, dest, src):
        if self.user_association_mode == 'keep-user':
            dest.user_id = src.user_id
        elif self.user_association_mode == 'custom' and self.user_association_template:
            with get_publisher().complex_data():
                try:
                    value = self.compute(
                        self.user_association_template,
                        formdata=src,
                        raises=True,
                        allow_complex=True,
                        status_item=self,
                    )
                except Exception:
                    # already logged by self.compute
                    value = None
                else:
                    value = get_publisher().get_cached_complex_data(value)

            from wcs.variables import LazyUser

            if isinstance(value, LazyUser):
                value = value._user
            if isinstance(value, get_publisher().user_class):
                dest.user = value
            else:
                dest.user = get_publisher().user_class.lookup_by_string(str(value))
                if value and not dest.user:
                    src.evolution[-1].add_part(
                        JournalAssignationErrorPart(
                            _('Failed to attach user (not found: "%s")') % value,
                            '%s (%s)' % (self.description, self.formdef.name),
                        )
                    )
                    src.store()
        else:
            dest.user_id = None

    def get_computed_strings(self):
        yield from super().get_computed_strings()
        if self.user_association_mode == 'custom':
            yield self.user_association_template
        for mapping in self.mappings or []:
            yield mapping.expression

    def get_dependencies(self):
        yield from super().get_dependencies()
        yield self.formdef

    def perform(self, formdata):
        formdef = self.formdef
        if not formdef or not (self.mappings or self.map_fields_by_varname):
            return

        # Protect against recursion:
        #
        # Keep the list of callers for this action and abort if there are
        # too many different callers (meaning it's not just being repeatedly
        # called from the same workflow in a controlled setting).
        #
        # (Recursion within the same formdata is already handled in the
        # perform_items method).

        recursion_limit = int(get_publisher().get_site_option('create-data-action-recursion-limit') or 1)
        action_key = (self.key, self.get_workflow().id, self.parent.id, self.id)

        publisher = get_publisher()

        if 'create_data_action' not in publisher.workflow_execution_stack[-1]['context']:
            # add a create_data_action dictionary in current workflow execution
            # context, it will hold sets indexed on current action reference
            # (action_key) that will count number of callers.
            publisher.workflow_execution_stack[-1]['context']['create_data_action'] = collections.defaultdict(
                set
            )

        # add a caller
        publisher.workflow_execution_stack[-1]['context']['create_data_action'][action_key].add(
            (formdata.formdef.xml_root_node, formdata.id)
        )

        # sum all callers from current and past execution stacks
        create_data_action_set = set()
        for workflow_stack in publisher.workflow_execution_stack:
            for val in workflow_stack['context'].get('create_data_action', {}).get(action_key, []):
                create_data_action_set.add(val)

        if len(create_data_action_set) > recursion_limit:
            get_publisher().record_error(
                _('Detected recursive creation of %s') % self.formdef_class.item_name_plural,
                formdata=formdata,
                status_item=self,
            )
            return

        new_formdata = formdef.data_class()()
        new_formdata.receipt_time = localtime()

        if formdata.test_result_id:
            from wcs.workflow_tests import WorkflowTests

            new_formdata.test_result_id = formdata.test_result_id
            WorkflowTests.reset_formdata_test_attributes(new_formdata)

        self.assign_user(dest=new_formdata, src=formdata)

        if self.keep_submission_context:
            new_formdata.submission_context = (formdata.submission_context or {}).copy()
            new_formdata.submission_channel = formdata.submission_channel
            new_formdata.submission_agent_id = formdata.submission_agent_id
        else:
            new_formdata.submission_context = {}

        new_formdata.backoffice_submission = self.backoffice_submission
        if self.backoffice_submission and get_request() and get_request().user is not None:
            new_formdata.submission_agent_id = str(get_request().user.id)

        new_formdata.submission_context['orig_object_type'] = formdata.formdef.xml_root_node
        new_formdata.submission_context['orig_formdef_id'] = str(formdata.formdef.id)
        new_formdata.submission_context['orig_formdata_id'] = str(formdata.id)
        new_formdata.data = {}

        self.apply_mappings(dest=new_formdata, src=formdata)

        removed_self = False

        if formdef.enable_tracking_codes:
            code = TrackingCode()

        if self.draft:
            new_formdata.status = 'draft'
            new_formdata.receipt_time = localtime()
            if self.draft_edit_operation_mode != 'full':
                new_formdata.workflow_data = {
                    '_create_formdata_draft_edit': {
                        'operation_mode': self.draft_edit_operation_mode,
                        'page_identifier': self.page_identifier,
                    },
                }
            new_formdata.store()
            if formdef.enable_tracking_codes:
                code.formdata = new_formdata  # this will .store() the code
        else:
            formdata.store()

            # freeze substitutions during submission, as it has side effects
            with get_publisher().substitutions.freeze():
                new_formdata.just_created()
                new_formdata.store()
                if formdef.enable_tracking_codes:
                    code.formdata = new_formdata  # this will .store() the code
                # add a link to current workflow & action
                new_formdata.record_workflow_event(
                    'workflow-created',
                    display_id=formdata.get_display_id(),
                    external_workflow_id=self.get_workflow().id,
                    external_status_id=self.parent.id,
                    external_item_id=self.id,
                )
                new_formdata.perform_workflow()
                # add a link to created formdata
                formdata.record_workflow_event(
                    self.workflow_trace_event,
                    external_formdef_id=formdef.id,
                    external_formdata_id=new_formdata.id,
                )

            try:
                # update local object as it may have been modified by new_formdata
                # workflow execution.
                formdata.refresh_from_storage()
            except KeyError:
                # current carddata/formdata was removed
                removed_self = True

        if new_formdata.user_id is None and not new_formdata.backoffice_submission and get_session():
            get_session().mark_anonymous_formdata(new_formdata)

        if removed_self:
            raise AbortOnRemovalException(formdata)

        evo = formdata.evolution[-1]
        evo.add_part(
            self.evolution_part_class(
                new_formdata, varname=self.varname, attach_to_history=self.attach_to_history
            )
        )
        formdata.store()

    def apply_mappings(self, dest, src):
        if self.map_fields_by_varname:
            fields_by_varname = {
                field.varname: field for field in self.formdef.get_data_fields() if field.varname
            }
            for field in src.formdef.get_data_fields():
                dest_field = fields_by_varname.get(field.varname)
                if dest_field is None:
                    continue
                try:
                    self._set_value(formdata=dest, field=dest_field, value=src.data.get(field.id))
                except Exception as e:
                    get_publisher().record_error(
                        _('Could not copy field by varname for "%s"') % field.varname,
                        formdata=src,
                        status_item=self,
                        exception=e,
                    )

        # field.id can be serialized to xml, so we must always convert them to
        # str when matching
        to_id_fields = {str(field.id): field for field in self.formdef.get_data_fields()}
        for field in self.formdef.get_data_fields():
            if field.key == 'block':
                for subfield in field.block.get_data_fields():
                    to_id_fields[f'{field.id}${subfield.id}'] = subfield
                    subfield.parent_block_field = field

        missing_fields = []

        # sort mappings to be sure block subfields come after parent block fields
        if self.mappings:
            self.mappings.sort(key=lambda x: x.field_id)
        for mapping in self.mappings or []:
            try:
                dest_field = to_id_fields[str(mapping.field_id)]
            except KeyError:
                missing_fields.append(mapping.field_id)
                continue
            with get_publisher().complex_data():
                try:
                    value = self.compute(
                        mapping.expression,
                        formdata=src,
                        raises=True,
                        allow_complex=dest_field.allow_complex,
                        status_item=self,
                    )
                except Exception:
                    # already logged by self.compute
                    continue
                if dest_field.allow_complex:
                    value = get_publisher().get_cached_complex_data(value)

            try:
                self._set_value(formdata=dest, field=dest_field, value=value)
            except Exception as e:
                expression = self.get_expression(mapping.expression)
                with get_publisher().error_context(
                    field_label=dest_field.label, field_url=dest_field.get_admin_url()
                ):
                    get_publisher().record_error(
                        _('Could not assign value to field "%s"') % dest_field.label,
                        formdata=src,
                        status_item=self,
                        expression=expression['value'],
                        expression_type=expression['type'],
                        exception=e,
                    )

        if missing_fields:
            labels = [(self.cached_field_labels or {}).get(x, 'unknown (%s)' % x) for x in missing_fields]
            summary = '%s %s' % (
                ngettext('Missing field:', 'Missing fields:', len(labels)),
                ', '.join(sorted(labels)),
            )
            get_publisher().record_error(summary, formdata=src, status_item=self)

    def _set_value(self, formdata, field, value):
        if field.convert_value_from_anything:
            dummy = value  # noqa: F841, copy value for debug
            value = field.convert_value_from_anything(value)

        parent_block_field = getattr(field, 'parent_block_field', None)
        if parent_block_field:
            if not formdata.data.get(parent_block_field.id):
                formdata.data[parent_block_field.id] = BlockRowValue().make_value(
                    block=parent_block_field.block, field=parent_block_field, data={}
                )
            for sub_data in formdata.data[parent_block_field.id]['data']:
                field.set_value(sub_data, value)
            formdata.data[f'{parent_block_field.id}_display'] = parent_block_field.store_display_value(
                formdata.data, parent_block_field.id
            )
        else:
            field.set_value(formdata.data, value)

    def mappings_export_to_xml(self, parent, include_id=False):
        container = ET.SubElement(parent, 'mappings')
        for mapping in self.mappings or []:
            item = ET.SubElement(container, 'mapping')
            item.attrib['field_id'] = str(mapping.field_id)
            item.text = mapping.expression

    def mappings_init_with_xml(self, container, include_id=False, snapshot=False):
        self.mappings = []
        for child in container:
            field_id = child.attrib.get('field_id', '')
            expression = child.text
            if field_id:
                self.mappings.append(Mapping(field_id=field_id, expression=expression))

    def perform_in_tests(self, formdata):
        from wcs.workflow_tests import WorkflowTests

        test_attributes = WorkflowTests.get_formdata_test_attributes(formdata)

        self.perform(formdata)

        # restore test attributes which were removed when refresh_from_storage() was called in perform()
        for attribute, value in test_attributes:
            setattr(formdata, attribute, value)

        evo = formdata.evolution[-1]
        if evo.parts and isinstance(evo.parts[-1], self.evolution_part_class):
            getattr(formdata, self.workflow_test_data_attribute).append(evo.parts[-1]._formdata)


register_item_class(CreateFormdataWorkflowStatusItem)
