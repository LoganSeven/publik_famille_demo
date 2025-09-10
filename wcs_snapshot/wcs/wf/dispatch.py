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

import collections
import xml.etree.ElementTree as ET

from quixote import get_publisher
from quixote.html import htmltext

from wcs.roles import get_user_roles
from wcs.workflows import (
    WorkflowStatusItem,
    XmlSerialisable,
    get_role_dependencies,
    get_role_name_and_slug,
    register_item_class,
)

from ..qommon import _, pgettext_lazy
from ..qommon.form import (
    CompositeWidget,
    RadiobuttonsWidget,
    SingleSelectWidget,
    SingleSelectWidgetWithOther,
    StringWidget,
    WidgetListAsTable,
)
from ..qommon.misc import xml_node_text
from ..qommon.template import Template
from ..qommon.templatetags.qommon import unlazy


class AutomaticDispatchRowWidget(CompositeWidget):
    def __init__(self, name, value=None, **kwargs):
        CompositeWidget.__init__(self, name, value, **kwargs)
        if not value:
            value = {}
        self.add(StringWidget, name='value', title=_('Value'), value=value.get('value'), **kwargs)
        self.add(
            SingleSelectWidget,
            name='role_id',
            title=_('Role'),
            value=value.get('role_id'),
            options=[(None, '----', None)] + get_user_roles(),
        )

    def _parse(self, request):
        if self.get('value') or self.get('role_id'):
            self.value = {'value': self.get('value'), 'role_id': self.get('role_id')}
        else:
            self.value = None


class AutomaticDispatchTableWidget(WidgetListAsTable):
    readonly = False

    def __init__(self, name, **kwargs):
        super().__init__(name, element_type=AutomaticDispatchRowWidget, **kwargs)


class RuleNode(XmlSerialisable):
    node_name = 'rule'

    def __init__(self, rule=None):
        rule = rule or {}
        self.role_id = rule.get('role_id')
        self.value = rule.get('value')

    def as_dict(self):
        return {'role_id': self.role_id, 'value': self.value}

    def get_parameters(self):
        return ('role_id', 'value')

    def role_id_export_to_xml(self, item, include_id=False):
        self._role_export_to_xml('role_id', item, include_id=include_id, include_missing=True)

    def role_id_init_with_xml(self, elem, include_id=False, snapshot=False):
        self._role_init_with_xml('role_id', elem, include_id=include_id, snapshot=snapshot)


class DispatchWorkflowStatusItem(WorkflowStatusItem):
    description = _('Function/Role Linking')
    key = 'dispatch'
    category = 'formdata-action'

    role_id = None
    role_key = None
    dispatch_type = 'manual'
    variable = None
    rules = None
    operation_mode = 'set'

    def get_parameters(self):
        return ('role_key', 'dispatch_type', 'role_id', 'variable', 'rules', 'operation_mode', 'condition')

    def get_inspect_parameters(self):
        parameters = list(self.get_parameters())
        if self.dispatch_type != 'automatic':
            parameters.remove('variable')
            parameters.remove('rules')
        if self.dispatch_type != 'manual':
            parameters.remove('role_id')
        return parameters

    def role_id_export_to_xml(self, item, include_id=False):
        self._role_export_to_xml('role_id', item, include_id=include_id)

    def _get_role_id_from_xml(self, elem, include_id=False, snapshot=False):
        # override to allow for "other" role being a plain string referencing an user
        # by its email address.
        value = xml_node_text(elem)
        if '@' in (xml_node_text(elem) or ''):
            return value
        return super()._get_role_id_from_xml(elem, include_id=include_id, snapshot=snapshot)

    def role_id_init_with_xml(self, elem, include_id=False, snapshot=False):
        self._role_init_with_xml('role_id', elem, include_id=include_id, snapshot=snapshot)

    def rules_export_to_xml(self, item, include_id=False):
        if self.dispatch_type != 'automatic' or not self.rules:
            return

        rules_node = ET.SubElement(item, 'rules')
        for rule in self.rules:
            rules_node.append(RuleNode(rule).export_to_xml(include_id=include_id))

        return rules_node

    def rules_init_with_xml(self, elem, include_id=False, snapshot=False):
        rules = []
        if elem is None:
            return
        for rule_xml_node in elem.findall('rule'):
            rule_node = RuleNode()
            rule_node.init_with_xml(rule_xml_node, include_id=include_id, snapshot=snapshot)
            rules.append(rule_node.as_dict())
        if rules:
            self.rules = rules

    def get_dependencies(self):
        yield from get_role_dependencies([self.role_id])
        yield from get_role_dependencies([x.get('role_id') for x in self.rules or []])

    def get_line_details(self):
        operation_mode_labels = {
            'set': pgettext_lazy('function_dispatch', 'set'),
            'add': pgettext_lazy('function_dispatch', 'add'),
            'remove': pgettext_lazy('function_dispatch', 'remove'),
        }
        if self.role_key:
            function_label = '%s %s' % (
                operation_mode_labels.get(self.operation_mode, ''),
                self.get_workflow().roles.get(self.role_key, '?'),
            )
            return function_label
        return None

    def add_parameters_widgets(self, form, parameters, prefix='', formdef=None, **kwargs):
        super().add_parameters_widgets(form, parameters, prefix=prefix, formdef=formdef, **kwargs)
        if 'role_key' in parameters:
            if not self.get_workflow().roles:
                self.get_workflow().roles = {}
            form.add(
                SingleSelectWidget,
                '%srole_key' % prefix,
                title=_('Function to Set'),
                value=self.role_key,
                options=[(None, '----', None)] + [(x, y, x) for x, y in self.get_workflow().roles.items()],
            )
        dispatch_types = collections.OrderedDict([('manual', _('Simple')), ('automatic', _('Multiple'))])
        if 'dispatch_type' in parameters:
            form.add(
                RadiobuttonsWidget,
                '%sdispatch_type' % prefix,
                title=_('Dispatch Type'),
                options=list(dispatch_types.items()),
                value=self.dispatch_type,
                required=True,
                attrs={'data-dynamic-display-parent': 'true'},
                extra_css_class='widget-inline-radio',
            )
        if 'role_id' in parameters:
            form.add(
                SingleSelectWidgetWithOther,
                '%srole_id' % prefix,
                title=_('Role'),
                value=str(self.role_id) if self.role_id else None,
                options=[(None, '---', None)] + get_user_roles(),
                attrs={
                    'data-dynamic-display-child-of': '%sdispatch_type' % prefix,
                    'data-dynamic-display-value': dispatch_types.get('manual'),
                },
            )
        if 'variable' in parameters:
            form.add(
                StringWidget,
                '%svariable' % prefix,
                title=_('Value template, or variable name'),
                value=self.variable,
                attrs={
                    'data-dynamic-display-child-of': '%sdispatch_type' % prefix,
                    'data-dynamic-display-value': dispatch_types.get('automatic'),
                },
            )
        if 'rules' in parameters:
            form.add(
                AutomaticDispatchTableWidget,
                '%srules' % prefix,
                title=_('Rules'),
                value=self.rules,
                attrs={
                    'data-dynamic-display-child-of': '%sdispatch_type' % prefix,
                    'data-dynamic-display-value': dispatch_types.get('automatic'),
                },
            )
        if 'operation_mode' in parameters:
            form.add(
                RadiobuttonsWidget,
                '%soperation_mode' % prefix,
                title=_('Operation Mode'),
                options=[
                    ('set', _('Set role to function')),
                    ('add', _('Add role to function')),
                    ('remove', _('Remove role from function')),
                ],
                value=self.operation_mode,
                required=True,
                extra_css_class='widget-inline-radio',
            )

    def get_role_id_parameter_view_value(self):
        try:
            return get_role_name_and_slug(self.role_id)[0]
        except KeyError:
            return _('Unknown role (%s)') % self.role_id

    def get_rules_parameter_view_value(self):
        result = []
        for rule in self.rules or []:
            try:
                result.append(
                    htmltext('<li>%s → %s</li>')
                    % (rule.get('value'), get_role_name_and_slug(rule.get('role_id'))[0])
                )
            except KeyError:
                result.append(
                    htmltext('<li>%s → %s</li>')
                    % (rule.get('value'), _('Unknown role (%s)') % rule.get('role_id'))
                )
        return htmltext('<ul class="rules">%s</ul>') % htmltext('').join(result)

    def get_computed_user_id(self, user_identifier):
        # look for a user matching "user_identifier", "user_identifier" can be
        # an actual user object, a nameid, an email, or a full name.
        with get_publisher().complex_data():
            maybe_user = self.compute(str(user_identifier), allow_complex=True)
            if maybe_user:
                maybe_user = unlazy(get_publisher().get_cached_complex_data(maybe_user))
        if not maybe_user:
            return None
        if isinstance(maybe_user, get_publisher().user_class):
            return maybe_user.id
        if not isinstance(maybe_user, str):
            return None
        users_by_nameid = get_publisher().user_class.get_users_with_name_identifier(maybe_user)
        if users_by_nameid:
            return users_by_nameid[0].id
        users_by_email = get_publisher().user_class.get_users_with_email(maybe_user)
        if users_by_email:
            return users_by_email[0].id
        users_by_name = get_publisher().user_class.get_users_with_name(maybe_user)
        if users_by_name:
            return users_by_name[0].id
        return None

    def get_computed_strings(self):
        yield from super().get_computed_strings()
        if self.dispatch_type == 'manual':
            yield self.role_id
        elif self.dispatch_type == 'automatic':
            yield self.variable

    def perform(self, formdata):
        if not formdata.workflow_roles:
            formdata.workflow_roles = {}

        new_role_id = None

        if self.dispatch_type == 'manual' or not self.dispatch_type:
            if not (self.role_id and self.role_key):
                return
            new_role_id = self.get_computed_role_id(self.role_id)
            if not new_role_id:
                user_id = self.get_computed_user_id(self.role_id)
                if user_id:
                    new_role_id = '_user:%s' % user_id
            if not new_role_id:
                if Template.is_template_string(self.role_id):
                    template_value = self.compute(str(self.role_id))
                    get_publisher().record_error(
                        _('error in dispatch, missing role (%(role)s, from "%(template)s" template)')
                        % {'role': template_value, 'template': self.role_id},
                        formdata=formdata,
                    )
                else:
                    get_publisher().record_error(
                        _('error in dispatch, missing role (%s)') % self.role_id, formdata=formdata
                    )
        elif self.dispatch_type == 'automatic':
            if not (self.role_key and self.variable and self.rules):
                return
            variable_values = []
            if Template.is_template_string(self.variable):
                variable_values = [self.compute(self.variable, formdata=formdata, status_item=self)]
            else:
                # legacy, self.variable as a straight variable name
                variables = get_publisher().substitutions.get_context_variables()
                # convert the given value to a few different types, to allow more
                # diversity in matching.
                variable_values = [variables.get(self.variable)]
                if not variable_values[0]:
                    variable_values.append(None)
                if variable_values[0] is not None:
                    variable_values.append(str(variable_values[0]))
                try:
                    variable_values.append(int(variable_values[0]))
                except (ValueError, TypeError):
                    pass

            for rule in self.rules:
                if rule.get('value') in variable_values:
                    new_role_id = rule.get('role_id')
                    break

            if new_role_id and not get_publisher().role_class.get(new_role_id, ignore_errors=True):
                get_publisher().record_error(
                    _('error in dispatch, missing role (%s)') % new_role_id, formdata=formdata
                )
                new_role_id = None

        if new_role_id:
            new_role_id = str(new_role_id)
            if not formdata.workflow_roles.get(self.role_key):
                formdef_workflow_roles = formdata.formdef.workflow_roles or {}
                formdata.workflow_roles[self.role_key] = formdef_workflow_roles.get(self.role_key)
            if self.operation_mode == 'set':
                formdata.workflow_roles[self.role_key] = [new_role_id]
            else:
                if not isinstance(formdata.workflow_roles[self.role_key], list):
                    if formdata.workflow_roles[self.role_key]:
                        formdata.workflow_roles[self.role_key] = [formdata.workflow_roles[self.role_key]]
                    else:
                        formdata.workflow_roles[self.role_key] = []
                roles = formdata.workflow_roles[self.role_key]
                if self.operation_mode == 'add' and new_role_id not in roles:
                    roles.append(new_role_id)
                elif self.operation_mode == 'remove' and new_role_id in roles:
                    roles.remove(new_role_id)
            formdata.store()

    def perform_in_tests(self, formdata):
        self.perform(formdata)


register_item_class(DispatchWorkflowStatusItem)
