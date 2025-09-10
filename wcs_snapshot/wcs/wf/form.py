# w.c.s. - web application for online forms
# Copyright (C) 2005-2013  Entr'ouvert
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

import uuid
import xml.etree.ElementTree as ET

from quixote import get_publisher
from quixote.html import TemplateIO, htmltext

from wcs import conditions
from wcs.admin.fields import FieldDefPage, FieldsDirectory
from wcs.clamd import add_clamd_scan_job
from wcs.fields import SetValueError
from wcs.fields.page import PostConditionsTableWidget
from wcs.formdata import get_dict_with_varnames
from wcs.formdef import FormDef
from wcs.forms.common import FileDirectory
from wcs.forms.root import FormPage
from wcs.variables import LazyFormDataVar
from wcs.workflows import (
    EvolutionPart,
    RedisplayFormException,
    WorkflowGlobalAction,
    WorkflowStatusItem,
    register_item_class,
)

from ..qommon import _, misc
from ..qommon.fields import display_fields
from ..qommon.form import CheckboxWidget, HtmlWidget, SingleSelectWidget, VarnameWidget, WidgetList
from ..qommon.xml_storage import PostConditionsXmlMixin


class WorkflowFormEvolutionPart(EvolutionPart):
    uuid = None
    data = None
    formdef = None
    varname = None

    def __init__(self, action, data, live=False):
        self.uuid = str(uuid.uuid4())
        self.varname = action.varname
        self.formdef = action.formdef
        self.data = data
        self.live = live

    def __getstate__(self):
        # make sure live data are not stored
        assert not getattr(self, 'live')
        return self.__dict__

    def get_json_export_dict(self, anonymise=False, include_files=True):
        if not self.varname or anonymise:
            return None
        d = {
            'type': 'workflow-form',
            'key': self.varname,
            'data': {
                k.removeprefix('var_'): v
                for k, v in get_dict_with_varnames(
                    self.formdef.fields, self.data, varnames_only=True, include_files=include_files
                ).items()
            },
        }
        return d


class WorkflowDisplayFormEvolutionPart(EvolutionPart):
    data_uuid = None
    render_for_fts = None

    def __init__(self, formdef, data_uuid):
        self.data_uuid = data_uuid
        self.formdef = formdef

    @property
    def data(self):
        if self._formdata:
            for part in self._formdata.iter_evolution_parts(klass=WorkflowFormEvolutionPart):
                if part.uuid == self.data_uuid:
                    return part.data
        return {}

    def __getstate__(self):
        odict = self.__dict__.copy()
        odict.pop('_formdata', None)
        return odict

    def view(self, formdata=None, **kwargs):
        self._formdata = formdata
        r = TemplateIO(html=True)
        r += htmltext('<div class="dataview form-summary">')
        r += display_fields(self, wf_form=True)
        r += htmltext('<div style="clear: both"></div>')  # cancel grid fields floating
        r += htmltext('</div>')
        return r.getvalue()


def lookup_wf_form_file(self, filename):
    # supports for URLs such as /$formdata/$id/files/form-$formvar-$fieldvar/test.txt
    try:
        literal, formvar, fieldvar = self.reference.split('-')
    except ValueError:
        return
    if literal != 'form' or not self.formdata.workflow_data:
        return
    try:
        return self.formdata.workflow_data['%s_var_%s_raw' % (formvar, fieldvar)]
    except KeyError:
        return


class WorkflowFormFieldsFormDef(FormDef):
    lightweight = False
    fields_count_total_soft_limit = 40
    fields_count_total_hard_limit = 80

    def __init__(self, item):
        self.item = item
        self.fields = []
        self.id = None

    @property
    def name(self):
        return _('Form action in workflow "%s"') % self.item.get_workflow().name

    def get_admin_url(self):
        base_url = get_publisher().get_backoffice_url()
        parent_type = 'global-actions' if isinstance(self.item.parent, WorkflowGlobalAction) else 'status'
        return '%s/workflows/%s/%s/%s/items/%s/fields/' % (
            base_url,
            self.item.get_workflow().id,
            parent_type,
            self.item.parent.id,
            self.item.id,
        )

    def get_field_admin_url(self, field):
        return self.get_admin_url() + '%s/' % getattr(field, 'original_id', field.id)

    def store(self, comment=None):
        self.item.get_workflow().store(comment=comment)

    def get_workflow(self):
        return self.item.get_workflow()

    def migrate(self):
        changed = False
        for f in self.fields or []:
            changed |= f.migrate()
        return changed


class WorkflowFormFieldDefPage(FieldDefPage):
    section = 'workflows'
    blacklisted_attributes = ['display_locations', 'anonymise']
    is_documentable = False

    def get_deletion_extra_warning(self):
        return None


class WorkflowFormFieldsDirectory(FieldsDirectory):
    section = 'workflows'
    support_import = False
    blacklisted_types = ['page', 'computed']
    field_def_page_class = WorkflowFormFieldDefPage


class FormWorkflowStatusItem(WorkflowStatusItem, PostConditionsXmlMixin):
    description = _('Form')
    key = 'form'
    category = 'interaction'
    ok_in_global_action = True
    endpoint = False
    waitpoint = True

    by = []
    formdef = None
    varname = None
    hide_submit_button = True
    post_conditions = None
    include_in_form_history = False

    def __init__(self, parent=None):
        super().__init__(parent=parent)
        # force new defaut value
        self.hide_submit_button = True

    def get_line_details(self):
        if not self.formdef or not self.by:
            return _('not completed')
        return _('by %s') % self.render_list_of_roles(self.by)

    @property
    def submit_button_label(self):
        # make submit button go to fields page when there are not yet any field.
        if self.formdef and self.formdef.fields:
            return _('Submit')
        return _('Submit and go to fields edition')

    @property
    def redirect_after_submit_url(self):
        if self.formdef and self.formdef.fields:
            return None
        return 'fields/'

    @classmethod
    def init(cls):
        if 'lookup_wf_form_file' not in FileDirectory._lookup_methods:
            FileDirectory._lookup_methods.append('lookup_wf_form_file')
            FileDirectory.lookup_wf_form_file = lookup_wf_form_file

    def add_parameters_widgets(self, form, parameters, prefix='', formdef=None, **kwargs):
        super().add_parameters_widgets(form, parameters, prefix=prefix, formdef=formdef, **kwargs)
        if 'by' in parameters:
            form.add(
                WidgetList,
                '%sby' % prefix,
                title=_('To'),
                element_type=SingleSelectWidget,
                value=self.by,
                add_element_label=self.get_add_role_label(),
                element_kwargs={
                    'render_br': False,
                    'options': [(None, '---', None)] + self.get_list_of_roles(include_logged_in_users=False),
                },
            )
        if 'hide_submit_button' in parameters:
            form.add(
                CheckboxWidget,
                '%shide_submit_button' % prefix,
                title=_('Hide Submit Button'),
                value=self.hide_submit_button,
                hint=_(
                    'If the default submit button is hidden the form will only be submitted through manual jump buttons.'
                ),
                default_value=self.__class__.hide_submit_button,
                advanced=True,
            )
        if 'varname' in parameters:
            form.add(
                VarnameWidget,
                '%svarname' % prefix,
                required=True,
                title=_('Identifier'),
                value=self.varname,
                hint=_('This is used as prefix for form fields variable names.'),
            )
            if not formdef and self.formdef and self.formdef.fields:
                # add link to go edit or view fields
                widget = HtmlWidget(
                    '<p><a class="pk-button" href="fields/">%s</a></p>'
                    % (_('View fields') if self.get_workflow().is_readonly() else _('Edit Fields'))
                )
                widget.tab = ('general', _('General'))
                form.widgets.append(widget)
        if 'post_conditions' in parameters:
            form.add(
                PostConditionsTableWidget,
                '%spost_conditions' % prefix,
                title=_('Validation conditions'),
                value=self.post_conditions,
            )
        if 'include_in_form_history' in parameters:
            form.add(
                CheckboxWidget,
                '%sinclude_in_form_history' % prefix,
                title=_('Display data in history'),
                value=self.include_in_form_history,
                advanced=True,
            )

    def get_parameters(self):
        return (
            'by',
            'varname',
            'hide_submit_button',
            'condition',
            'post_conditions',
            'include_in_form_history',
        )

    def clean_varname(self, form):
        widget = form.get_widget('varname')
        new_value = widget.parse()

        if new_value == 'form' or new_value.startswith('form_'):
            widget.set_error(_('Wrong identifier detected: "form" prefix is forbidden.'))
            return True

        return False

    def migrate(self):
        changed = False
        if self.formdef and self.formdef.fields:
            changed |= self.formdef.migrate()
        if 'hide_submit_button' not in self.__dict__:
            # force the legacy value so it doesn't get the new default value
            self.hide_submit_button = False
            changed = True
        return changed

    def get_dependencies(self):
        yield from super().get_dependencies()
        if self.formdef and self.formdef.fields:
            for field in self.formdef.fields:
                yield from field.get_dependencies()
        post_conditions = self.post_conditions or []
        for post_condition in post_conditions:
            condition = post_condition.get('condition') or {}
            if condition.get('type') == 'django':
                yield from misc.get_dependencies_from_template(condition.get('value'))

    def export_to_xml(self, include_id=False):
        item = WorkflowStatusItem.export_to_xml(self, include_id=include_id)
        if not hasattr(self, 'formdef') or not self.formdef or not self.formdef.fields:
            return item
        formdef = ET.SubElement(item, 'formdef')

        # we give a name to the formdef because it is required in the formdef
        # xml import.
        ET.SubElement(formdef, 'name').text = '-'

        fields = ET.SubElement(formdef, 'fields')
        for field in self.formdef.fields:
            fields.append(field.export_to_xml(include_id=include_id))

        self.post_conditions_export_to_xml(formdef, include_id=include_id)

        return item

    def init_with_xml(self, elem, include_id=False, snapshot=False, check_datasources=True):
        super().init_with_xml(
            elem, include_id=include_id, snapshot=snapshot, check_datasources=check_datasources
        )
        el = elem.find('formdef')
        if el is None:
            return
        # we can always include id in the formdef export as it lives in
        # a different space, isolated from other formdefs.
        imported_formdef = FormDef.import_from_xml_tree(
            el, include_id=True, snapshot=snapshot, check_datasources=check_datasources
        )
        self.formdef = WorkflowFormFieldsFormDef(item=self)
        self.formdef.fields = imported_formdef.fields

        post_conditions_node = el.find('post_conditions')
        self.post_conditions_init_with_xml(post_conditions_node, include_id=include_id)

    def q_admin_lookup(self, workflow, status, component):
        if component == 'fields':
            if not self.formdef:
                self.formdef = WorkflowFormFieldsFormDef(item=self)
            if workflow.is_readonly():
                self.formdef.readonly = True
            fields_directory = WorkflowFormFieldsDirectory(self.formdef)
            if self.varname:
                fields_directory.field_var_prefix = 'form_workflow_form_%s_var_' % self.varname
            return fields_directory
        return None

    def prefix_form_fields(self):
        for field in self.formdef.fields:
            try:
                field.original_id, field.id = field.id, '%s_%s_%s' % (self.varname, self.id, int(field.id))
            except ValueError:
                # already prefixed or is a uuid
                pass

    def is_interactive(self):
        return True

    def fill_form(self, form, formdata, user, displayed_fields=None, form_data=None, **kwargs):
        if not self.formdef:
            return
        self.prefix_form_fields()
        self.formdef.var_prefixes = [
            'form_workflow_form_%s' % self.varname,
            # legacy access, as unstructured data in formdef.workflow_data dictionary
            'form_workflow_data_%s' % self.varname,
            # legacy access, not even under the proper form_ namespace
            self.varname,
        ]

        self.formdef.add_fields_to_form(form, displayed_fields=displayed_fields, form_data=form_data)
        if 'submit' not in form._names and not self.hide_submit_button:
            form.add_submit('submit', _('Submit'))
            form.get_widget('submit').action_id = self.id

        # put varname in a form attribute so it can be used in templates to
        # identify the form.
        form.varname = self.varname

        formdata.feed_session()

        self.formdef.set_live_condition_sources(form, self.formdef.fields)

        if (
            formdata.evolution
            and formdata.evolution[-1].parts
            and isinstance(formdata.evolution[-1].parts[-1], WorkflowFormEvolutionPart)
            and formdata.evolution[-1].parts[-1].live
        ):
            # attach live evaluated data to form object, to be used in live_process_fields
            # for block conditions.
            form.blocks_formdata_data = formdata.evolution[-1].parts[-1].data

        if form.is_submitted():
            # skip prefilling part when form is being submitted
            return

        fields = self.formdef.fields
        if displayed_fields is not None:
            fields = displayed_fields

        FormPage.apply_field_prefills({}, form, fields)

    def evaluate_live_form(self, form, formdata, user, submit=False):
        if not self.formdef:
            return
        self.prefix_form_fields()
        try:
            formdef_data = self.formdef.get_data(form, raise_on_error=submit)
        except SetValueError as e:
            raise RedisplayFormException(
                form=form, error={'summary': _('Technical error, please try again.'), 'details': e}
            )
        if self.varname and formdata.evolution:
            self.update_workflow_data(formdata, formdef_data, submit)
            form.formdata_data = formdef_data

    def update_workflow_data(self, formdata, formdef_data, submit=False, allow_legacy_storage=True):
        workflow_data = {}
        for k, v in get_dict_with_varnames(self.formdef.fields, formdef_data, varnames_only=True).items():
            workflow_data['%s_%s' % (self.varname, k)] = v
        if allow_legacy_storage and not get_publisher().has_site_option(
            'disable-workflow-form-to-workflow-data'
        ):
            formdata.update_workflow_data(workflow_data)
        if self.varname and formdata.evolution:
            formdata.evolution[-1].add_part(
                WorkflowFormEvolutionPart(self, formdef_data, live=bool(not submit))
            )

    def submit_form(self, form, formdata, user, evo):
        if not self.formdef:
            return
        if form.get_submit() is True:
            # non-submit button, maybe a "add block" button, look for them.
            for widget in form.widgets:
                if isinstance(widget, WidgetList):  # BlockWidget
                    add_element_widget = widget.get_widget('add_element')
                    if add_element_widget and add_element_widget.parse():
                        raise RedisplayFormException(form=form)
        else:
            button_name = form.get_submit()
            button = form.get_widget(button_name)
            ignore_form_errors = getattr(button, 'ignore_form_errors', False)

            error_messages = []
            if not form.has_errors() and self.post_conditions:

                for i, post_condition in enumerate(self.post_conditions):
                    condition = post_condition.get('condition')
                    try:
                        if conditions.Condition(condition, record_errors=False).evaluate():
                            continue
                    except RuntimeError:
                        pass
                    error_message = post_condition.get('error_message')
                    error_message = get_publisher().translate(error_message)
                    error_message = self.compute(error_message, allow_ezt=False)
                    error_messages.append(error_message)

                if error_messages and not ignore_form_errors:
                    form.add_global_errors(error_messages)
                    raise RedisplayFormException(form=form)

            if not form.has_errors() and not formdata.is_workflow_test() and not ignore_form_errors:
                self.evaluate_live_form(form, formdata, user, submit=True)
                formdata.record_workflow_action(action=self)
                if formdata.evolution and self.varname and self.include_in_form_history:
                    evo.add_part(
                        WorkflowDisplayFormEvolutionPart(self.formdef, formdata.evolution[-1].parts[-1].uuid)
                    )
                formdata.store()
                add_clamd_scan_job(formdata)

        get_publisher().substitutions.unfeed(lambda x: x.__class__.__name__ == 'ConditionVars')

    def get_parameters_view(self):
        r = TemplateIO(html=True)
        r += super().get_parameters_view()
        if self.formdef and self.formdef.fields:
            r += htmltext('<p>%s</p>') % _('Form:')
            r += htmltext('<ul class="inspect-wf-form-fields">')
            for field in self.formdef.fields:
                r += htmltext('<li>')
                r += field.label
                if getattr(field, 'required', False):
                    r += htmltext(' (%s)') % _('required')
                r += htmltext(' (%s)') % field.get_type_label()
                r += field.get_parameters_view()
                r += htmltext('</li>')
            r += htmltext('</ul>')
        return r.getvalue()

    def i18n_scan(self, base_location):
        location = '%sitems/%s/fields/' % (base_location, self.id)
        if self.formdef and self.formdef.fields:
            for field in self.formdef.fields:
                yield from field.i18n_scan(location)
        for post_condition in self.post_conditions or []:
            yield location, None, post_condition.get('error_message')


register_item_class(FormWorkflowStatusItem)


class LazyFormDataWorkflowForms:
    def __init__(self, formdata):
        self._formdata = formdata

    def __getattr__(self, varname):
        wfform_formdatas = []
        if '_varnames' not in self.__dict__:
            # keep a cache of valid attribute names
            self.__dict__['_varnames'] = varnames = set()
        else:
            # use cache to avoid iterating on parts
            varnames = self.__dict__['_varnames']
            if varname not in varnames:
                raise AttributeError(varname)
        for part in self._formdata.iter_evolution_parts(WorkflowFormEvolutionPart):
            varnames.add(part.varname)
            if part.varname == varname and part.data:
                part.formdef.migrate()
                wfform_formdatas.append(LazyFormDataWorkflowFormsItem(part, base_formdata=self._formdata))
        if wfform_formdatas:
            return LazyFormDataWorkflowFormsItems(wfform_formdatas)
        raise AttributeError(varname)

    def inspect_keys(self):
        varnames = set()
        for part in self._formdata.iter_evolution_parts(WorkflowFormEvolutionPart):
            if part.varname and part.data:
                varnames.add(part.varname)
        yield from varnames


class LazyFormDataWorkflowFormsItems:
    def __init__(self, wfform_formdatas):
        self._wfform_formdatas = wfform_formdatas

    def inspect_keys(self):
        return [str(x) for x in range(len(self._wfform_formdatas))] + ['var']

    @property
    def var(self):
        # alias to latest values
        return self._wfform_formdatas[-1].var

    def __getitem__(self, key):
        try:
            key = int(key)
        except ValueError:
            try:
                return getattr(self, key)
            except AttributeError:
                return self._wfform_formdatas[0][key]
        return self._wfform_formdatas[key]

    def __len__(self):
        return len(self._wfform_formdatas)

    def __iter__(self):
        yield from self._wfform_formdatas


class LazyFormDataWorkflowFormsItem:
    def __init__(self, part, base_formdata):
        self._part = part
        self.data = part.data
        self.base_formdata = base_formdata

    def inspect_keys(self):
        return ['var']

    @property
    def var(self):
        # pass self as formdata, it will be used to access self.data in LazyFieldVarBlock
        return LazyFormDataVar(
            self._part.formdef.get_all_fields(),
            self._part.data,
            formdata=self,
            base_formdata=self.base_formdata,
        )
