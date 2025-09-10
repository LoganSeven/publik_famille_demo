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

import re
import uuid

from quixote import get_publisher, get_request
from quixote.html import TemplateIO, htmltext

from wcs.carddef import CardDef
from wcs.formdef import FormDef
from wcs.qommon import _
from wcs.qommon.form import ComputedExpressionWidget, RadiobuttonsWidget, SingleSelectWidget
from wcs.variables import LazyFormData, LazyFormDefObjectsManager, LazyList
from wcs.workflows import (
    AbortOnRemovalException,
    EvolutionPart,
    Workflow,
    WorkflowGlobalActionWebserviceTrigger,
    WorkflowStatusItem,
    perform_items,
    push_perform_workflow,
    register_item_class,
)


class ManyExternalCallsPart(EvolutionPart):
    processed_ids = None
    label = None
    running = True
    uuid = None

    def __init__(self, label):
        self.label = label
        self.uuid = str(uuid.uuid4())
        self.processed_ids = []

    def is_hidden(self):
        return bool(not self.running) or not (
            get_request() and get_request().get_path().startswith('/backoffice/')
        )

    def view(self, **kwargs):
        r = TemplateIO(html=True)
        r += htmltext('<div>')
        r += (
            htmltext('<p>%s</p>')
            % _('Running external actions on "%(label)s" (%(count)s processed)')
            % {'label': self.label, 'count': len(self.processed_ids)}
        )
        r += htmltext('</div>')
        return r.getvalue()


class ExternalWorkflowGlobalAction(WorkflowStatusItem):
    description = _('External workflow')
    key = 'external_workflow_global_action'
    category = 'formdata-action'
    automatic_targetting = _('Action on forms/cards linked to this form/card')
    manual_targetting = _('Specify the list of forms/cards on which the action will be applied')

    slug = None
    target_mode = None
    target_id = None
    trigger_id = None

    def get_workflow_webservice_triggers(self, workflow):
        for action in workflow.global_actions or []:
            for trigger in action.triggers or []:
                if isinstance(trigger, WorkflowGlobalActionWebserviceTrigger) and trigger.identifier:
                    yield trigger

    def get_object_def(self, object_slug=None):
        slug = object_slug or self.slug
        try:
            object_type, slug = slug.split(':')
        except (AttributeError, ValueError):
            return None
        if object_type == 'formdef':
            object_class = FormDef
        elif object_type == 'carddef':
            object_class = CardDef
        try:
            return object_class.get_by_urlname(slug)
        except KeyError:
            pass

    @property
    def formdef(self):
        return self.get_object_def()

    def get_trigger(self, workflow):
        try:
            trigger_id = self.trigger_id.split(':', 1)[1]
        except ValueError:
            return
        for trigger in self.get_workflow_webservice_triggers(workflow):
            if trigger.identifier == trigger_id:
                return trigger

    def get_inspect_parameters(self):
        parameters = super().get_inspect_parameters()
        if self.target_mode != 'manual':
            parameters.remove('target_id')
        return parameters

    def add_parameters_widgets(self, form, parameters, prefix='', formdef=None, **kwargs):
        super().add_parameters_widgets(form, parameters, prefix=prefix, formdef=formdef, **kwargs)

        if 'slug' in parameters or 'trigger_id' in parameters:
            objects = [(None, '---', '', {})]
            trigger_options = []
            is_admin_accessible = {
                'forms': get_publisher().get_backoffice_root().is_accessible('forms'),
                'cards': get_publisher().get_backoffice_root().is_accessible('cards'),
            }

            # preload all cards/forms
            objectdefs = FormDef.select(order_by='id', lightweight=True) + CardDef.select(
                order_by='id', lightweight=True
            )

            # get workflows with external actions
            workflows = {}
            for workflow in Workflow.select(ignore_migration=True):
                external_triggers = list(self.get_workflow_webservice_triggers(workflow))
                if external_triggers:
                    workflows[str(workflow.id)] = workflow
                repeated_names = {}
                for trigger in external_triggers:
                    if trigger.parent.name not in repeated_names:
                        repeated_names[trigger.parent.name] = 0
                    repeated_names[trigger.parent.name] += 1
                for trigger in external_triggers:
                    object_slugs = [
                        f'{x.xml_root_node}:{x.url_name}'
                        for x in objectdefs
                        if x.workflow_id == str(workflow.id)
                    ]
                    trigger_id = 'action:%s' % trigger.identifier
                    if repeated_names.get(trigger.parent.name) > 1:
                        option_label = f'{trigger.parent.name} [{trigger.identifier}]'
                    else:
                        option_label = trigger.parent.name
                    trigger_options.append(
                        (trigger_id, option_label, trigger_id, {'data-slugs': '|'.join(object_slugs)})
                    )

            # list cards/forms with workflows with external actions
            for objectdef in objectdefs:
                workflow = workflows.get(objectdef.workflow_id)
                if not workflow:
                    continue
                object_slug = '%s:%s' % (objectdef.xml_root_node, objectdef.url_name)
                objects.append((object_slug, objectdef.name, object_slug, {}))
                if is_admin_accessible[objectdef.backoffice_section]:
                    objects[-1][-1]['data-goto-url'] = objectdef.get_admin_url()

            if len(objects) == 1:
                form.add_global_errors([_('No workflow with external triggerable global action.')])
                return

        if 'slug' in parameters:
            objects.sort(key=lambda x: x[1])
            form.add(
                SingleSelectWidget,
                '%sslug' % prefix,
                title=_('Form/Card'),
                value=self.slug,
                required=True,
                options=objects,
                **{'data-filter-trigger-select': 'true'},
            )

        if 'target_mode' in parameters:
            target_modes = [
                ('all', self.automatic_targetting, 'all'),
                ('manual', self.manual_targetting, 'manual'),
            ]
            form.add(
                RadiobuttonsWidget,
                '%starget_mode' % prefix,
                title=_('Targeting'),
                value=self.target_mode or 'all',
                required=True,
                options=target_modes,
                attrs={'data-dynamic-display-parent': 'true'},
            )

        if 'target_id' in parameters:
            form.add(
                ComputedExpressionWidget,
                '%starget_id' % prefix,
                value=self.target_id,
                required=False,
                attrs={
                    'data-dynamic-display-child-of': 'target_mode',
                    'data-dynamic-display-value': 'manual',
                },
            )

        if 'trigger_id' in parameters:
            trigger_options.sort(key=lambda x: x[1].rsplit('[')[0])
            form.add(
                SingleSelectWidget,
                '%strigger_id' % prefix,
                title=_('Action'),
                value=self.trigger_id,
                required=True,
                options=[(None, '---', '', {})] + trigger_options,
            )

        if kwargs.get('orig') == 'variable_widget':
            return

    def get_line_details(self):
        if self.slug and self.trigger_id:
            objectdef = self.get_object_def()
            if objectdef:
                trigger = self.get_trigger(objectdef.workflow)
                if trigger:
                    return _('action "%(trigger_name)s" on %(object_name)s') % {
                        'trigger_name': trigger.parent.name,
                        'object_name': objectdef.name,
                    }
        return _('not completed')

    def get_manual_target(self, formdata):
        if self.target_mode != 'manual':
            return

        objectdef = self.get_object_def()
        with get_publisher().complex_data():
            try:
                target_id = self.compute(
                    self.target_id, formdata=formdata, status_item=self, allow_complex=True, raises=True
                )
            except Exception:
                # already logged by self.compute
                return
            if target_id:
                target_id = get_publisher().get_cached_complex_data(target_id)

        if isinstance(target_id, LazyFormData):
            if target_id._formdef != objectdef:
                # abort if it's not the correct formdef/carddef
                get_publisher().record_error(
                    _('Mismatch in target object: expected "%(object_name)s", got "%(object_name2)s"')
                    % {'object_name': objectdef.name, 'object_name2': target_id._formdef.name},
                    formdata=formdata,
                    status_item=self,
                )
                return

            yield target_id._formdata
            return

        if isinstance(target_id, LazyFormDefObjectsManager):
            if target_id._formdef != objectdef:
                # abort if it's not the correct formdef/carddef
                get_publisher().record_error(
                    _('Mismatch in target objects: expected "%(object_name)s", got "%(object_name2)s"')
                    % {'object_name': objectdef.name, 'object_name2': target_id._formdef.name},
                    formdata=formdata,
                    status_item=self,
                )
                return
            for lazy_formdata in target_id:
                yield lazy_formdata._formdata
            return

        if not target_id:
            return

        if isinstance(target_id, (tuple, list, LazyList)):
            target_ids = target_id
        else:
            target_ids = re.split(r'[,|]', str(target_id))

        for target_id in target_ids:
            try:
                yield objectdef.data_class().get_by_id(target_id)
            except KeyError as e:
                # use custom error message depending on target type
                get_publisher().record_error(
                    _('Could not find targeted "%(object_name)s" object by id %(object_id)s')
                    % {'object_name': objectdef.name, 'object_id': target_id},
                    formdata=formdata,
                    status_item=self,
                    exception=e,
                )

    def iter_target_datas(self, formdata, objectdef):
        if self.target_mode == 'manual':
            # return targets
            yield from self.get_manual_target(formdata)
        else:
            yield from formdata.iter_target_datas(
                objectdef=objectdef, object_type=self.slug, status_item=self
            )

    def get_parameters(self):
        return ('slug', 'trigger_id', 'target_mode', 'target_id', 'condition')

    def get_computed_strings(self):
        yield from super().get_computed_strings()
        if self.target_mode == 'manual':
            yield self.target_id

    def get_dependencies(self):
        yield from super().get_dependencies()
        yield self.get_object_def()

    def perform(self, formdata):
        objectdef = self.get_object_def()
        if not objectdef:
            return

        trigger = self.get_trigger(objectdef.workflow)
        if not trigger:
            get_publisher().record_error(
                _('No trigger with id "%s" found in workflow') % self.trigger_id,
                formdata=formdata,
                status_item=self,
            )
            return

        class CallerSource:
            def __init__(self, formdata):
                self.formdata = formdata

            def get_substitution_variables(self):
                return {'caller_form': self.formdata.get_substitution_variables(minimal=True)['form']}

        caller_source = CallerSource(formdata)

        formdata.store()
        status_part = ManyExternalCallsPart(label=objectdef.name)
        for i, target_data in enumerate(self.iter_target_datas(formdata, objectdef)):
            if formdata.test_result_id:
                from wcs.workflow_tests import WorkflowTests

                WorkflowTests.reset_formdata_test_attributes(target_data)

            with (
                get_publisher().substitutions.temporary_feed(target_data),
                push_perform_workflow(target_data),
            ):
                get_publisher().reset_formdata_state()
                get_publisher().substitutions.feed(target_data.formdef)
                get_publisher().substitutions.feed(target_data)
                get_publisher().substitutions.feed(caller_source)

                target_data.record_workflow_event(
                    'global-external-workflow',
                    external_workflow_id=trigger.get_workflow().id,
                    global_action_id=trigger.parent.id,
                )
                perform_items(
                    trigger.parent.items,
                    target_data,
                    global_action=True,
                    check_progress=False,
                )

            try:
                # update local object as it may have been modified by target_data
                # workflow executions.
                formdata.refresh_from_storage()
            except KeyError:
                # current carddata/formdata was removed
                raise AbortOnRemovalException(formdata)

            if i == 0:
                # if there are iterations, add tracking status to object
                formdata.evolution[-1].add_part(status_part)
            elif i:
                # get status object back
                for part in formdata.iter_evolution_parts(klass=ManyExternalCallsPart, reverse=True):
                    if part.uuid == status_part.uuid:
                        status_part = part
                        break
            status_part.processed_ids.append(target_data.get_display_id())
            # after iterating, store
            formdata.store()

        # note it's now done.
        status_part.running = False
        formdata.store()

    def perform_in_tests(self, formdata):
        from wcs.workflow_tests import WorkflowTests

        test_attributes = WorkflowTests.get_formdata_test_attributes(formdata)
        self.perform(formdata)

        # restore test attributes which were removed when refresh_from_storage() was called in perform()
        for attribute, value in test_attributes:
            setattr(formdata, attribute, value)


register_item_class(ExternalWorkflowGlobalAction)
