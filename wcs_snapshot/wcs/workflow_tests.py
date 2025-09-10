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

import collections
import copy
import datetime
import json
import uuid
import xml.etree.ElementTree as ET
from contextlib import contextmanager

import requests
from django.utils.timezone import localtime
from pyquery import PyQuery as pq
from quixote import get_publisher, get_session

from wcs import wf
from wcs.carddef import CardDef
from wcs.formdef import FormDef
from wcs.qommon import _, misc
from wcs.qommon.form import (
    CheckboxWidget,
    EmailWidget,
    OptGroup,
    RadiobuttonsWidget,
    SingleSelectWidget,
    SingleSelectWidgetWithOther,
    StringWidget,
    TextWidget,
    WidgetList,
)
from wcs.qommon.humantime import humanduration2seconds, seconds2humanduration, timewords
from wcs.qommon.storage import Contains, Equal
from wcs.qommon.xml_storage import XmlStorableObject
from wcs.testdef import TestError, WebserviceResponse
from wcs.wf.backoffice_fields import SetBackofficeFieldRowWidget, SetBackofficeFieldsTableWidget
from wcs.wf.create_carddata import LinkedCardDataEvolutionPart
from wcs.wf.create_formdata import LinkedFormdataEvolutionPart, Mapping, MappingsWidget, MappingWidget
from wcs.wf.form import WorkflowFormEvolutionPart
from wcs.wf.profile import FieldNode
from wcs.wf.register_comment import JournalEvolutionPart
from wcs.wf.sendmail import EmailEvolutionPart
from wcs.wf.wscall import WorkflowWsCallEvolutionPart
from wcs.workflows import (
    ContentSnapshotPart,
    WorkflowGlobalAction,
    WorkflowGlobalActionTimeoutTrigger,
    WorkflowStatusItem,
)


class WorkflowTestError(TestError):
    pass


def get_test_actions(klass=None):
    for action_class in (klass or WorkflowTestAction).__subclasses__():
        yield action_class
        yield from get_test_actions(klass=action_class)


def get_test_action_options():
    actions = sorted(get_test_actions(), key=lambda x: x.label)

    assertion_options = [OptGroup(_('Assertions'))] + [
        (x.key, x.label, x.key) for x in actions if x.is_assertion
    ]
    other_options = [OptGroup(_('Actions'))] + [
        (x.key, x.label, x.key) for x in actions if not x.is_assertion
    ]

    return assertion_options + other_options


def get_test_action_class_by_type(action_type):
    for action_class in get_test_actions():
        if action_class.key == action_type:
            return action_class

    raise KeyError


class WorkflowTests(XmlStorableObject):
    _names = 'workflow_tests'
    xml_root_node = 'workflow_tests'
    testdef_id = None
    _actions = None

    XML_NODES = [
        ('testdef_id', 'int'),
        ('actions', 'actions'),
    ]

    formdata_test_attributes = {
        'sent_sms': list,
        'sent_emails': list,
        'used_webservice_responses': list,
        'anonymisation_performed': False,
        'redirect_to_url': None,
        'history_messages': list,
        'created_formdata': list,
        'created_carddata': list,
        'edited_carddata': list,
        'testdef': None,
    }

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._actions = []

    @property
    def actions(self):
        return self._actions

    @actions.setter
    def actions(self, actions):
        self._actions = actions
        for action in actions:
            action.parent = self

    def run(self, formdata):
        self.reset_formdata_test_attributes(formdata, self.testdef)

        formdata.perform_workflow()
        for action in self.actions:
            status = formdata.get_status()

            if not action.is_configured:
                continue

            if not action.is_assertion:
                self.reset_formdata_test_attributes(formdata, self.testdef)

            try:
                action.perform(formdata)
            except WorkflowTestError as e:
                e.action_uuid = action.uuid
                e.details.append(_('Form status when error occured: %s') % status.name)
                raise e

    @classmethod
    def reset_formdata_test_attributes(cls, formdata, testdef=None):
        for attribute, default in cls.formdata_test_attributes.items():
            if callable(default):
                default = default()

            setattr(formdata, attribute, default)

        if testdef:
            testdef.used_webservice_responses = formdata.used_webservice_responses
            formdata.testdef = testdef

    @classmethod
    def get_formdata_test_attributes(cls, formdata):
        return [(attribute, getattr(formdata, attribute)) for attribute in cls.formdata_test_attributes]

    def get_new_action_id(self):
        if not self.actions:
            return '1'

        return str(max(int(x.id) for x in self.actions) + 1)

    def add_action(self, action_class, index=None):
        action = action_class(id=self.get_new_action_id())
        action.parent = self
        if index is None:
            self.actions.append(action)
        else:
            self.actions.insert(index, action)
        return action

    def add_actions_from_formdata(self, formdata):
        test_action_class_by_trace_id = {
            'sendmail': AssertEmail,
            'sendsms': AssertSMS,
            'webservice_call': AssertWebserviceCall,
            'set-backoffice-fields': AssertBackofficeFieldValues,
            'button': ButtonClick,
            'global-action-button': ButtonClick,
            'timeout-jump': SkipTime,
            'anonymise': AssertAnonymise,
            'redirect_to_url': AssertRedirect,
            'register-comment': AssertHistoryMessage,
            'modify_criticality': AssertCriticality,
            'workflow-created-formdata': AssertFormCreation,
            'workflow-created-carddata': AssertCardCreation,
            'workflow-edited-carddata': AssertCardEdition,
            'edit-action': EditForm,
            'form': FillForm,
        }
        waitpoint_events = ('button', 'global-action-button', 'timeout-jump', 'edit-action')

        evolution_parts = collections.defaultdict(lambda: collections.defaultdict(list))
        for evo in formdata.evolution:
            for part in evo.parts or []:
                evolution_parts[evo.status][part.__class__].append(part)

        previous_trace = None
        assert_status_next_index = -1
        workflow_traces = formdata.get_workflow_traces()
        for trace in workflow_traces:
            trace_id = trace.event or trace.action_item_key

            if trace_id not in test_action_class_by_trace_id:
                previous_trace = trace
                continue

            if trace.event in waitpoint_events:
                trace_for_status = trace if trace.event != 'edit-action' else previous_trace

                action = self.add_action(AssertStatus, index=assert_status_next_index)
                action.set_attributes_from_trace(formdata.formdef, trace_for_status)

                assert_status_next_index = len(self.actions) + 1

            action = self.add_action(test_action_class_by_trace_id[trace_id])
            action.set_attributes_from_trace(formdata.formdef, trace, previous_trace)

            if action.evolution_part_class:
                action.find_part_and_set_from_part(trace, evolution_parts, formdata)

            previous_trace = trace

        if workflow_traces:
            action = self.add_action(AssertStatus, index=assert_status_next_index)
            action.status_name = formdata.get_status().name

    def export_actions_to_xml(self, element, attribute_name, **kwargs):
        for action in self.actions:
            element.append(action.export_to_xml())

    def import_actions_from_xml(self, element, **kwargs):
        actions = []
        for sub in element.findall('test-action'):
            key = sub.findtext('key')

            try:
                klass = get_test_action_class_by_type(key)
            except KeyError:
                continue

            actions.append(klass.import_from_xml_tree(sub))

        return actions

    def get_dependencies(self):
        for action in self.actions:
            yield from action.get_dependencies()


class WorkflowTestAction(XmlStorableObject):
    xml_root_node = 'test-action'
    _names = 'test-action'
    uuid = None

    optional_fields = []
    is_assertion = True
    editable = True
    edit_button_label = _('Submit')
    edit_redirect_url = '..'
    evolution_part_class = None

    XML_NODES = [
        ('id', 'str'),
        ('uuid', 'str'),
        ('key', 'str'),
    ]

    def __init__(self, **kwargs):
        self.uuid = str(uuid.uuid4())

        allowed_key = {x[0] for x in self.XML_NODES}
        for k, v in kwargs.items():
            if k in allowed_key:
                setattr(self, k, v)

    def __str__(self):
        return str(self.label)

    @property
    def is_configured(self):
        return not any(
            field
            for field, _ in self.XML_NODES
            if field != 'id' and field not in self.optional_fields and getattr(self, field, None) is None
        )

    def set_attributes_from_trace(self, *args, **kwargs):
        pass

    def find_part_and_set_from_part(self, trace, evolution_parts, formdata):
        parts = evolution_parts.get(trace.status_id, {}).get(self.evolution_part_class)
        if parts:
            part = self.pop_evolution_part(parts)
            if part:
                self.set_attributes_from_evolution(part)

    def set_attributes_from_evolution(self, *args, **kwargs):
        pass

    def pop_evolution_part(self, parts):
        return parts.pop(0)

    def render_as_line(self):
        if not self.is_configured:
            return _('not configured')

        return self.details_label

    def get_dependencies(self):
        return []

    def get_admin_url(self):
        return self.parent.testdef.get_admin_url() + 'workflow/%s/' % self.id

    @staticmethod
    def normalize_string(string):
        # remove non-breaking spaces and double spaces
        string = ' '.join(string.split())

        # remove newlines
        string = ' '.join(line.strip() for line in string.splitlines() if line)

        return string

    @classmethod
    def is_substring(cls, substring, string):
        if substring in string:
            return True

        if cls.normalize_string(substring) in cls.normalize_string(string):
            return True

        return False

    def perform_checks(self, formdata):
        data = getattr(formdata, self.formdata_test_attribute)
        if not data:
            raise WorkflowTestError(self.checks_empty_error_message)

        errors = []
        for i, obj in enumerate(data):
            error = self.perform_check(obj)
            if not error:
                data.pop(i)
                return

            errors.append(error)

        if len(errors) == 1:
            details = [
                '%(object_name)s%(:)s %(error)s'
                % {'object_name': self.checks_object_name, ':': _(':'), 'error': errors[0]}
            ]
        else:
            details = [
                '%(object_name)s #%(no)s%(:)s %(error)s'
                % {'no': i, 'object_name': self.checks_object_name, ':': _(':'), 'error': e}
                for i, e in enumerate(errors, 1)
            ]

        raise WorkflowTestError(self.checks_error_message, details=details)


class ActionWithUserMixin:
    who = None
    who_id = None

    optional_fields = ['who_id']

    XML_NODES = WorkflowTestAction.XML_NODES + [
        ('who', 'str'),
        ('who_id', 'str'),
    ]

    def get_user_label(self):
        if self.who == 'receiver':
            user = _('backoffice user')
        elif self.who == 'submitter':
            user = _('submitter')
        else:
            try:
                user = get_publisher().test_user_class.select([Equal('test_uuid', self.who_id)])[0]
            except IndexError:
                user = _('missing user')

        return str(user)

    def get_user(self, formdata):
        if self.who == 'receiver':
            if not self.parent.testdef.agent_id:
                raise WorkflowTestError(_('Broken, missing user'))
            try:
                user = get_publisher().test_user_class.select(
                    [Equal('test_uuid', self.parent.testdef.agent_id)]
                )[0]
            except IndexError:
                raise WorkflowTestError(_('Broken, missing user'))
        elif self.who == 'submitter':
            if formdata.user_id:
                user = get_publisher().test_user_class.get(formdata.user_id)
            else:
                get_session().mark_anonymous_formdata(formdata)
                user = None
        else:
            try:
                user = get_publisher().test_user_class.select([Equal('test_uuid', self.who_id)])[0]
            except IndexError:
                raise WorkflowTestError(_('Broken, missing user'))

        return user

    def add_user_fields(self, form):
        user_options = [
            ('submitter', _('Submitter'), 'submitter'),
            ('other', _('Other user'), 'other'),
        ]
        if self.parent.testdef.agent_id:
            user_options.insert(0, ('receiver', _('Backoffice user'), 'receiver'))

        form.add(
            RadiobuttonsWidget,
            'who',
            title=_('User who clicks on button'),
            options=user_options,
            value=self.who or user_options[0][0],
            attrs={'data-dynamic-display-parent': 'true'},
        )

        user_options = [('', '---', '')] + [
            (str(x.test_uuid), str(x), str(x.test_uuid))
            for x in get_publisher().test_user_class.select(order_by='name')
        ]
        form.add(
            SingleSelectWidget,
            'who_id',
            options=user_options,
            value=self.who_id,
            attrs={
                'data-dynamic-display-child-of': 'who',
                'data-dynamic-display-value-in': 'other',
            },
            **{'data-autocomplete': 'true'},
        )


class ButtonClick(ActionWithUserMixin, WorkflowTestAction):
    label = _('Simulate click on action button')

    key = 'button-click'
    button_name = None

    is_assertion = False

    XML_NODES = ActionWithUserMixin.XML_NODES + [
        ('button_name', 'str'),
    ]

    @property
    def details_label(self):
        return _('Click on "%(button_name)s" by %(user)s') % {
            'button_name': self.button_name,
            'user': self.get_user_label(),
        }

    def set_attributes_from_trace(self, formdef, trace, previous_trace=None):
        if 'action_item_id' in trace.event_args:
            try:
                button_name = [
                    x.label
                    for x in self.get_all_choice_actions(formdef)
                    if x.id == trace.event_args['action_item_id'] and 'wf-%s' % x.parent.id == trace.status_id
                ][0]
            except IndexError:
                return
        elif 'global_action_id' in trace.event_args:
            try:
                button_name = [
                    x.name
                    for x in self.get_all_global_actions(formdef)
                    if x.id == trace.event_args['global_action_id']
                ][0]
            except IndexError:
                return

        self.button_name = button_name

    def perform(self, formdata):
        user = self.get_user(formdata)
        get_publisher().substitutions.feed(user)

        status = formdata.get_status()
        form = status.get_action_form(formdata, user)
        if not form or not any(
            button_widget := x for x in form.submit_widgets if x.label == self.button_name
        ):
            raise WorkflowTestError(_('Button "%s" is not displayed.') % self.button_name)

        if hasattr(self, 'comment'):
            if not form.get_widget('comment'):
                raise WorkflowTestError(_('Comment action field is not displayed.'))

            form.force_value('comment', self.comment)

        form.get_submit = lambda: button_widget.name
        form.has_errors = lambda: False
        status.handle_form(form, formdata, user, check_replay=False)

    @staticmethod
    def get_all_choice_actions(formdef):
        for item in formdef.workflow.get_all_items():
            if isinstance(item, wf.choice.ChoiceWorkflowStatusItem) and item.status:
                yield item
            elif isinstance(item, wf.form.FormWorkflowStatusItem) and not item.hide_submit_button:
                item.label = str(_('Submit'))
                yield item

    @staticmethod
    def get_all_global_actions(formdef):
        for action in formdef.workflow.global_actions or []:
            if not action.is_interactive():
                yield action

    def fill_admin_form(self, form, formdef):
        possible_button_names = {x.label for x in self.get_all_choice_actions(formdef)}
        possible_button_names.update(action.name for action in self.get_all_global_actions(formdef))
        possible_button_names.update(
            x.button_label
            for x in formdef.workflow.get_all_items()
            if x.key == 'commentable' and x.button_label
        )

        possible_button_names = [x for x in possible_button_names if '{{' not in x and '{%' not in x]
        possible_button_names = sorted(possible_button_names)

        form.add(
            SingleSelectWidgetWithOther,
            'button_name',
            title=_('Button name'),
            options=possible_button_names,
            required=True,
            value=self.button_name,
        )

        self.add_user_fields(form)

    def get_dependencies(self):
        if self.who == 'other' and self.who_id:
            try:
                yield get_publisher().test_user_class.select([Equal('test_uuid', self.who_id)])[0]
            except IndexError:
                pass


class AssertStatus(WorkflowTestAction):
    label = _('Form status')

    key = 'assert-status'
    status_name = None

    XML_NODES = WorkflowTestAction.XML_NODES + [
        ('status_name', 'str'),
    ]

    @property
    def details_label(self):
        return _('Status is "%s"') % self.status_name

    def set_attributes_from_trace(self, formdef, trace, previous_trace=None):
        try:
            status = formdef.workflow.get_status(trace.status_id)
        except KeyError:
            return

        self.status_name = status.name

    def perform(self, formdata):
        status = formdata.get_status()
        if status.name != self.status_name:
            raise WorkflowTestError(
                _('Form should be in status "%(expected_status)s" but is in status "%(status)s".')
                % {'expected_status': self.status_name, 'status': status.name}
            )

    def fill_admin_form(self, form, formdef):
        possible_statuses = [x.name for x in formdef.workflow.possible_status]

        value = self.status_name
        if value and value not in possible_statuses:
            value = '%s (%s)' % (value, _('not available'))
            possible_statuses.append(value)

        form.add(
            SingleSelectWidget,
            'status_name',
            title=_('Status name'),
            options=possible_statuses,
            required=True,
            value=self.status_name,
        )


class AssertEmail(WorkflowTestAction):
    label = _('Email send')

    formdata_test_attribute = 'sent_emails'
    checks_object_name = _('Sent email')
    checks_error_message = _('No sent email matches expected criterias.')
    checks_empty_error_message = _('No email was sent.')

    key = 'assert-email'
    addresses = None
    subject_strings = None
    body_strings = None

    optional_fields = ['addresses', 'subject_strings', 'body_strings']
    evolution_part_class = EmailEvolutionPart

    XML_NODES = WorkflowTestAction.XML_NODES + [
        ('addresses', 'str_list'),
        ('subject_strings', 'str_list'),
        ('body_strings', 'str_list'),
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.addresses = self.addresses or []
        self.subject_strings = self.subject_strings or []
        self.body_strings = self.body_strings or []

    @property
    def details_label(self):
        label = ''

        if self.addresses:
            label = _('Email to "%s"') % self.addresses[0]

            if len(self.addresses) > 1:
                label = '%s (+%s)' % (label, len(self.addresses) - 1)
        elif self.subject_strings:
            label = _('Subject must contain "%s"') % misc.ellipsize(self.subject_strings[0])
        elif self.body_strings:
            label = _('Body must contain "%s"') % misc.ellipsize(self.body_strings[0])

        return label

    def set_attributes_from_evolution(self, part):
        self.addresses = part.addresses
        self.subject_strings = [part.mail_subject]
        self.body_strings = [line for line in part.mail_body.splitlines() if line]

    def perform(self, formdata):
        self.perform_checks(formdata)

    def perform_check(self, email):
        for address in self.addresses:
            if address not in email.workflow_test_addresses:
                return _('was not addressed to %(recipient)s (recipients were %(recipients)s)') % {
                    'recipient': address,
                    'recipients': ', '.join(sorted(email.email_msg.recipients())),
                }

        for subject in self.subject_strings:
            if not self.is_substring(subject, email.email_msg.subject):
                return _('subject does not contain "%(expected)s" (was "%(subject)s")') % {
                    'expected': subject,
                    'subject': self.normalize_string(email.email_msg.subject),
                }

        for body in self.body_strings:
            if not self.is_substring(body, email.email_msg.body):
                return _('body does not contain "%(expected)s" (was "%(body)s")') % {
                    'expected': body,
                    'body': self.normalize_string(email.email_msg.body),
                }

    def fill_admin_form(self, form, formdef):
        form.add(
            WidgetList,
            'addresses',
            element_type=EmailWidget,
            title=_('Email addresses'),
            value=self.addresses,
            add_element_label=_('Add address'),
            element_kwargs={'render_br': False, 'size': 50},
        )
        form.add(
            WidgetList,
            'subject_strings',
            element_type=StringWidget,
            title=_('Subject must contain'),
            value=self.subject_strings,
            add_element_label=_('Add string'),
            element_kwargs={'render_br': False, 'size': 50},
        )
        form.add(
            WidgetList,
            'body_strings',
            element_type=StringWidget,
            title=_('Body must contain'),
            value=self.body_strings,
            add_element_label=_('Add string'),
            element_kwargs={'render_br': False, 'size': 50},
        )


class SkipTime(WorkflowTestAction):
    label = _('Move forward in time')

    key = 'skip-time'
    seconds = None

    is_assertion = False

    XML_NODES = WorkflowTestAction.XML_NODES + [
        ('seconds', 'int'),
    ]

    @property
    def details_label(self):
        return seconds2humanduration(self.seconds)

    def set_attributes_from_trace(self, formdef, trace, previous_trace=None):
        if previous_trace:
            self.seconds = int((trace.timestamp - previous_trace.timestamp).total_seconds())

    def perform(self, formdata):
        self.parent.testdef.fake_datetime.tick(self.seconds)

        self.apply_jumps(formdata)
        self.apply_global_actions_timeout(formdata)

    def apply_jumps(self, formdata):
        jump_actions = []
        status = formdata.get_status()
        for item in status.items:
            if hasattr(item, 'has_valid_timeout') and item.has_valid_timeout():
                jump_actions.append(item)

        if not jump_actions:
            return

        delay = wf.jump.get_min_jumps_delay(jump_actions)

        if formdata.last_update_time > localtime() - datetime.timedelta(seconds=delay):
            return

        get_publisher().substitutions.invalidate_cache()
        for jump_action in jump_actions:
            if jump_action.check_condition(formdata):
                if formdata.is_workflow_test() and formdata.testdef:
                    formdata.testdef.add_to_coverage(jump_action)

                wf.jump.jump_and_perform(formdata, jump_action)
                break

    @contextmanager
    def mock_sql_methods(self, formdata):
        real_select_iterator = formdata.formdef.data_class().select_iterator
        real_formdefs = formdata.formdef.workflow.formdefs
        real_carddefs = formdata.formdef.workflow.carddefs

        try:
            formdata.formdef.data_class().select_iterator = lambda *args, **kwargs: [formdata]
            formdata.formdef.workflow.formdefs = lambda: [formdata.formdef]
            formdata.formdef.workflow.carddefs = lambda: []
            yield
        finally:
            formdata.formdef.data_class().select_iterator = real_select_iterator
            formdata.formdef.workflow.formdefs = real_formdefs
            formdata.formdef.workflow.carddefs = real_carddefs

    def apply_global_actions_timeout(self, formdata):
        with self.mock_sql_methods(formdata):
            WorkflowGlobalActionTimeoutTrigger.apply(formdata.formdef.workflow)

    def fill_admin_form(self, form, formdef):
        form.add(
            StringWidget,
            'seconds',
            title=_('Value'),
            value=seconds2humanduration(self.seconds),
            hint=_('ex.: 1 day 12 hours. Usable units of time: %(variables)s.')
            % {'variables': ','.join(timewords())},
        )

    def seconds_parse(self, value):
        if not value:
            return value
        try:
            return humanduration2seconds(value)
        except ValueError:
            return None


class AssertBackofficeFieldRowWidget(SetBackofficeFieldRowWidget):
    value_placeholder = None


class AssertBackofficeFieldsTableWidget(SetBackofficeFieldsTableWidget):
    element_type = AssertBackofficeFieldRowWidget


class AssertBackofficeFieldValues(WorkflowTestAction):
    label = _('Backoffice field values')

    key = 'assert-backoffice-field'
    fields = None

    evolution_part_class = ContentSnapshotPart

    XML_NODES = WorkflowTestAction.XML_NODES + [
        ('fields', 'fields'),
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields = self.fields or []

    @property
    def details_label(self):
        backoffice_field_labels = {
            x.id: x.label for x in self.parent.testdef.formdef.workflow.get_backoffice_fields()
        }
        field_ids = [x['field_id'] for x in self.fields or []]
        labels = [backoffice_field_labels[x] for x in field_ids if x in backoffice_field_labels]
        if not labels:
            return _('not configured')
        return ', '.join(labels)

    def set_attributes_from_evolution(self, part):
        for field in part.formdef.workflow.get_backoffice_fields():
            if field.id not in part.new_data:
                continue

            if field.key in ('file', 'block'):
                continue

            value = part.new_data[field.id]
            if value == part.old_data[field.id]:
                continue

            if field.convert_value_to_str:
                value = field.convert_value_to_str(value)

            self.fields.append({'field_id': field.id, 'value': value})

    def pop_evolution_part(self, parts):
        for i, part in enumerate(parts):
            if part.source == 'set-backoffice-fields':
                return parts.pop(i)

    def perform(self, formdata):
        for field_dict in self.fields:
            field_id = field_dict['field_id']
            try:
                field = [x for x in formdata.formdef.workflow.get_backoffice_fields() if x.id == field_id][0]
            except IndexError:
                raise WorkflowTestError(_('Field "%s" is missing.') % field_id)

            formdata_values = [formdata.data.get(field_id)]
            if '%s_display' % field_id in formdata.data:
                formdata_values.append(formdata.data['%s_display' % field_id])
            if field.convert_value_to_str:
                formdata_values.append(field.convert_value_to_str(formdata_values[0]))
            elif field.key == 'file':
                formdata_values.append(str(formdata_values[0]))

            with get_publisher().complex_data():
                value = WorkflowStatusItem.compute(field_dict['value'], allow_complex=True)
                expected_value = get_publisher().get_cached_complex_data(value)

            if expected_value not in formdata_values:
                raise WorkflowTestError(
                    _(
                        'Wrong value for backoffice field "%(field)s" (expected "%(expected_value)s", got "%(value)s").'
                    )
                    % {
                        'field': field.label,
                        'value': formdata_values[-1],
                        'expected_value': expected_value,
                    }
                )

    def fill_admin_form(self, form, formdef):
        form.add(
            AssertBackofficeFieldsTableWidget,
            'fields',
            value_widget_class=StringWidget,
            value=self.fields,
            workflow=formdef.workflow,
        )

    def export_fields_to_xml(self, element, attribute_name, **kwargs):
        for field in self.fields:
            element.append(FieldNode(field).export_to_xml(include_id=True))

    def import_fields_from_xml(self, element, **kwargs):
        fields = []
        for field_xml_node in element.findall('field'):
            field_node = FieldNode()
            field_node.init_with_xml(field_xml_node, include_id=True, snapshot=None)
            fields.append(field_node.as_dict())

        return fields


class AssertWebserviceCall(WorkflowTestAction):
    label = _('Webservice call')

    key = 'assert-webservice-call'
    webservice_response_uuid = None

    evolution_part_class = WorkflowWsCallEvolutionPart

    XML_NODES = WorkflowTestAction.XML_NODES + [
        ('webservice_response_uuid', 'str'),
    ]

    @property
    def details_label(self):
        webservice_responses = [
            x
            for x in self.parent.testdef.get_webservice_responses()
            if x.uuid == self.webservice_response_uuid
        ]
        if webservice_responses:
            return webservice_responses[0].name
        return _('Broken, missing webservice response')

    @property
    def empty_form_error(self):
        r = '<p>%s</p>' % _(
            'In order to assert a webservice is called, you must define corresponding webservice response.'
        )
        r += '<p><a href="%swebservice-responses/">%s</a><p>' % (
            self.parent.testdef.get_admin_url(),
            _('Add webservice response'),
        )
        return r

    def set_attributes_from_evolution(self, part):
        request = requests.Request('GET', part.url)
        prepared_request = request.prepare()

        for response in self.parent.testdef.get_webservice_responses():
            if not response.get_mismatch_reason(prepared_request):
                break
        else:
            response = WebserviceResponse.create_from_evolution_part(self.parent.testdef, part)
            if not response:
                return

        self.webservice_response_uuid = response.uuid

    def perform(self, formdata):
        try:
            response = [
                x
                for x in self.parent.testdef.get_webservice_responses()
                if x.uuid == self.webservice_response_uuid
            ][0]
        except IndexError:
            raise WorkflowTestError(_('Broken, missing webservice response'))

        for used_response in formdata.used_webservice_responses.copy():
            if used_response.uuid == self.webservice_response_uuid:
                formdata.used_webservice_responses.remove(used_response)
                break
        else:
            raise WorkflowTestError(_('Webservice response %(name)s was not used.') % {'name': response.name})

    def fill_admin_form(self, form, formdef):
        webservice_response_options = [
            (response.uuid, response.name, response.uuid)
            for response in self.parent.testdef.get_webservice_responses()
        ]

        if not webservice_response_options:
            return

        form.add(
            SingleSelectWidget,
            'webservice_response_uuid',
            title=_('Webservice response'),
            options=webservice_response_options,
            required=True,
            value=self.webservice_response_uuid,
        )


class AssertSMS(WorkflowTestAction):
    label = _('SMS send')

    formdata_test_attribute = 'sent_sms'
    checks_object_name = _('Sent SMS')
    checks_error_message = _('No sent SMS matches expected criterias.')
    checks_empty_error_message = _('No SMS was sent.')

    key = 'assert-sms'
    phone_numbers = None
    body = None

    optional_fields = ['phone_numbers', 'body']

    XML_NODES = WorkflowTestAction.XML_NODES + [
        ('phone_numbers', 'str_list'),
        ('body', 'str'),
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.phone_numbers = self.phone_numbers or []

    @property
    def details_label(self):
        label = ''

        if self.phone_numbers:
            label = _('SMS to %s') % self.phone_numbers[0]

            if len(self.phone_numbers) > 1:
                label = '%s (+%s)' % (label, len(self.phone_numbers) - 1)
        elif self.body:
            label = misc.ellipsize(self.body)

        return label

    def perform(self, formdata):
        self.perform_checks(formdata)

    def perform_check(self, sms):
        for recipient in self.phone_numbers:
            if recipient not in sms['phone_numbers']:
                return _('was not addressed to %(recipient)s (recipients were %(recipients)s)') % {
                    'recipient': recipient,
                    'recipients': ', '.join(sms['phone_numbers']),
                }

        if self.body and self.body != self.normalize_string(sms['body']):
            return _('body does not contain "%(expected)s" (was "%(body)s")') % {
                'expected': self.body,
                'body': sms['body'],
            }

    def fill_admin_form(self, form, formdef):
        form.add(
            WidgetList,
            'phone_numbers',
            title=_('Phone numbers'),
            value=self.phone_numbers,
            add_element_label=_('Add phone number'),
            element_kwargs={'render_br': False, 'size': 50},
        )
        form.add(
            StringWidget,
            'body',
            title=_('Body'),
            value=self.body,
        )


class AssertAnonymise(WorkflowTestAction):
    label = _('Anonymisation')

    key = 'assert-anonymise'

    editable = False
    details_label = ''

    def perform(self, formdata):
        if not formdata.anonymisation_performed:
            raise WorkflowTestError(_('Form was not anonymised.'))


class AssertRedirect(WorkflowTestAction):
    label = _('Redirect')

    key = 'assert-redirect'
    url = None

    XML_NODES = WorkflowTestAction.XML_NODES + [
        ('url', 'str'),
    ]

    @property
    def details_label(self):
        return self.url

    def perform(self, formdata):
        if not formdata.redirect_to_url:
            raise WorkflowTestError(_('No redirection occured.'))

        if formdata.redirect_to_url != self.url:
            raise WorkflowTestError(
                _('Expected redirection to %(expected_url)s but was redirected to %(url)s.')
                % {'expected_url': self.url, 'url': formdata.redirect_to_url}
            )

    def fill_admin_form(self, form, formdef):
        form.add(
            StringWidget,
            'url',
            title=_('URL'),
            value=self.url,
        )


class AssertHistoryMessage(WorkflowTestAction):
    label = _('History message display')

    formdata_test_attribute = 'history_messages'
    checks_object_name = _('Displayed history message')
    checks_error_message = _('No displayed history message has expected content.')
    checks_empty_error_message = _('No history message.')

    evolution_part_class = JournalEvolutionPart

    key = 'assert-history-message'
    message = None
    message_strings = None

    XML_NODES = WorkflowTestAction.XML_NODES + [
        ('message', 'str'),  # legacy
        ('message_strings', 'str_list'),
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.message_strings = self.message_strings or []

    @property
    def is_configured(self):
        return self.message_strings or self.message

    @property
    def details_label(self):
        return misc.ellipsize(', '.join(self.messages))

    @property
    def messages(self):
        return self.message_strings or [self.message]

    def set_attributes_from_evolution(self, part):
        if not part.content:
            return

        self.message_strings = [pq(part.content).text()]

    def perform(self, formdata):
        self.perform_checks(formdata)

    def perform_check(self, raw_message):
        if not raw_message:
            return _('empty content')

        message = pq(raw_message).text()
        for string in self.messages:
            if not self.is_substring(string, message) and not self.is_substring(string, raw_message):
                return _('content does not contain "%(expected)s" (was "%(message)s")') % {
                    'expected': string,
                    'message': self.normalize_string(message),
                }

    def fill_admin_form(self, form, formdef):
        form.add(
            WidgetList,
            'message_strings',
            element_type=StringWidget,
            title=_('Message must contain'),
            value=self.messages,
            element_kwargs={'render_br': False, 'size': 50},
        )


class AssertAlert(WorkflowTestAction):
    label = _('Alert display')

    key = 'assert-alert'
    message = None

    XML_NODES = WorkflowTestAction.XML_NODES + [
        ('message', 'str'),
    ]

    @property
    def details_label(self):
        return misc.ellipsize(self.message)

    def perform(self, formdata):
        messages = [pq(x).text() for x in formdata.get_workflow_messages()]

        for message in messages:
            if self.is_substring(self.message, message):
                break
        else:
            details = [
                _('Displayed alerts: %s')
                % (', '.join([self.normalize_string(x) for x in messages]) if messages else _('None')),
                _('Expected alert: %s') % self.message,
            ]
            raise WorkflowTestError(_('No alert matching message.'), details=details)

    def fill_admin_form(self, form, formdef):
        form.add(
            TextWidget,
            'message',
            title=_('Message'),
            value=self.message,
            hint=_('Assertion will pass if the text is contained in alert message.'),
        )


class AssertCriticality(WorkflowTestAction):
    label = _('Criticality level')
    empty_form_error = _('Workflow has no criticality levels.')

    key = 'assert-criticality'
    level_id = None

    XML_NODES = WorkflowTestAction.XML_NODES + [
        ('level_id', 'str'),
    ]

    @property
    def details_label(self):
        levels = [
            x for x in self.parent.testdef.formdef.workflow.criticality_levels or [] if x.id == self.level_id
        ]
        if not levels:
            return _('Broken, missing criticality level')

        return _('Criticality is "%s"') % levels[0].name

    def perform(self, formdata):
        levels = [x for x in formdata.formdef.workflow.criticality_levels or [] if x.id == self.level_id]
        if not levels:
            raise WorkflowTestError(_('Broken, missing criticality level'))

        current_level = formdata.get_criticality_level_object()
        if current_level.id != self.level_id:
            raise WorkflowTestError(
                _('Form should have criticality level "%(expected_level)s" but has level "%(level)s".')
                % {'expected_level': levels[0].name, 'level': current_level.name}
            )

    def fill_admin_form(self, form, formdef):
        if not formdef.workflow.criticality_levels:
            return

        form.add(
            SingleSelectWidget,
            'level_id',
            title=_('Name'),
            value=self.level_id,
            options=[(x.id, x.name, x.id) for x in formdef.workflow.criticality_levels],
        )


class FillForm(WorkflowTestAction):
    label = _('Fill form')
    empty_form_error = _('Workflow has no form actions.')
    edit_button_label = _('Submit and go to fields filling')
    edit_redirect_url = 'fields/'

    key = 'fill-form'
    form_action_id = None
    form_data = None
    feed_last_test_result = False

    is_assertion = False
    evolution_part_class = WorkflowFormEvolutionPart

    XML_NODES = WorkflowTestAction.XML_NODES + [
        ('form_action_id', 'str'),
        ('form_data', 'json'),
        ('feed_last_test_result', 'bool'),
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.form_data = self.form_data or {}

    def export_json_to_xml(self, element, attribute_name, **kwargs):
        element.text = json.dumps(getattr(self, attribute_name), indent=2, sort_keys=True)

    def import_json_from_xml(self, element, **kwargs):
        return json.loads(element.text)

    def get_workflow_form_action(self, formdef):
        status_id, action_id = self.form_action_id.split('-')
        status = formdef.workflow.get_status(status_id)
        action = status.get_item(action_id)
        if action.key != 'form':
            raise KeyError()
        return action

    @property
    def details_label(self):
        try:
            form_action = self.get_workflow_form_action(self.parent.testdef.formdef)
        except KeyError:
            return _('Broken, missing form action')

        return '%s - %s' % (form_action.parent.name, form_action.varname)

    def find_part_and_set_from_part(self, trace, evolution_parts, formdata):
        # the part can be stored on the same evo than the trace or on the very next one
        trace_status_seen = False
        last_evo_to_inspect = False
        for evo in formdata.evolution or []:
            if evo.status == trace.status_id:
                trace_status_seen = True
            if not trace_status_seen:
                continue
            for part in evo.parts or []:
                if isinstance(part, self.evolution_part_class):
                    self.set_attributes_from_evolution(part)
                    return
            assert not last_evo_to_inspect, 'this should not be reached'
            last_evo_to_inspect = True

    def set_attributes_from_trace(self, formdef, trace, previous_trace=None):
        self.status_id = trace.status_id.removeprefix('wf-')

    def set_attributes_from_evolution(self, part):
        self.form_action_id = '%s-%s' % (self.status_id, part.formdef.item.id)
        for field in part.formdef.fields:
            if field.key in ('file', 'block'):
                continue

            if field.id in part.data:
                field_id = getattr(field, 'original_id', field.id)
                self.form_data[field_id] = part.data[field.id]

    def perform(self, formdata):
        action_index = self.parent.actions.index(self)
        for action in self.parent.actions[action_index + 1 :]:
            if action.key == 'button-click':
                button_click = action
                break
        else:
            raise WorkflowTestError(_('Form fill must be followed by "button click" action.'))

        try:
            form_action = self.get_workflow_form_action(formdata.formdef)
        except KeyError:
            raise WorkflowTestError(_('Broken, missing form action'))

        if form_action.parent != formdata.get_status():
            raise WorkflowTestError(
                _('Form is not in the status containing form fill action.'),
                details=[_('Status containing action: %s') % form_action.parent.name],
            )

        user = button_click.get_user(formdata)
        if not form_action.check_auth(formdata, user):
            raise WorkflowTestError(_('Form is not accessible by user "%s".') % button_click.get_user_label())

        if not form_action.check_condition(formdata):
            raise WorkflowTestError(_('Form is not displayed.'))

        self.parent.testdef.add_to_coverage(form_action)

        form_action.prefix_form_fields()

        form_data = {}
        for field in form_action.formdef.fields:
            field_id = getattr(field, 'original_id', field.id)
            for suffix in ('', '_display', '_structured'):
                key = '%s%s' % (field_id, suffix)
                if key in self.form_data:
                    form_data['%s%s' % (field.id, suffix)] = self.form_data[key]

        testdef = copy.copy(self.parent.testdef)
        testdef.data = {'fields': form_data}
        testdef.coverage = None

        try:
            testdef.run_form_fill(form_action.formdef)
        except TestError as e:
            raise WorkflowTestError(e.msg)

    def fill_admin_form(self, form, formdef):
        form_actions = []
        for item in formdef.workflow.get_all_items():
            if isinstance(item.parent, WorkflowGlobalAction):
                continue

            if item.key == 'form' and item.formdef:
                form_actions.append(item)

        if not form_actions:
            return

        form.add(
            SingleSelectWidget,
            'form_action_id',
            title=_('Name'),
            value=self.form_action_id,
            options=[
                (
                    '%s-%s' % (x.parent.id, x.id),
                    '%s - %s' % (x.parent.name, x.varname),
                    '%s-%s' % (x.parent.id, x.id),
                )
                for x in form_actions
            ],
        )
        form.add(
            CheckboxWidget,
            'feed_last_test_result',
            value=self.feed_last_test_result,
            title=_('Use last test result in fields filling page'),
            hint=_(
                'If form has a datasource that depends on workflow data (backoffice field, previous form), '
                'this option can be used to display the correct choice list.'
            ),
        )


class FillComment(WorkflowTestAction):
    label = _('Fill comment')
    empty_form_error = _('Workflow has no comment actions.')

    key = 'fill-comment'
    comment = ''

    is_assertion = False

    XML_NODES = WorkflowTestAction.XML_NODES + [
        ('comment', 'str'),
    ]

    @property
    def details_label(self):
        return misc.ellipsize(self.comment)

    def perform(self, formdata):
        action_index = self.parent.actions.index(self)
        for action in self.parent.actions[action_index + 1 :]:
            if action.key == 'button-click':
                button_click = action
                break
        else:
            raise WorkflowTestError(_('Comment fill must be followed by "button click" action.'))

        button_click.comment = self.comment

    def fill_admin_form(self, form, formdef):
        form.add(
            TextWidget,
            'comment',
            title=_('Comment'),
            value=self.comment,
        )


class AssertFormCreationMappingWidget(MappingWidget):
    value_placeholder = None
    expression_widget_title = _('Expected value')


class AssertFormCreationMappingsWidget(MappingsWidget):
    element_type = AssertFormCreationMappingWidget


class AssertFormCreation(WorkflowTestAction):
    label = _('Form creation')
    empty_form_error = _('Workflow has no form creation action.')

    formdef_class = FormDef
    evolution_part_class = LinkedFormdataEvolutionPart
    action_name = 'create_formdata'
    formdata_test_attribute = 'created_formdata'
    broken_error_message = _('Broken, missing form')

    checks_object_name = _('Created form')
    checks_error_message = _('No created form matches expected criterias.')
    checks_empty_error_message = _('No form was created.')
    checks_kind_error_message = _('wrong form "%(formdef)s" (should be "%(expected_formdef)s")')

    key = 'assert-form-creation'
    formdef_slug = None
    mappings = None

    XML_NODES = WorkflowTestAction.XML_NODES + [
        ('formdef_slug', 'str'),
        ('mappings', 'mappings'),
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.mappings = self.mappings or []

    @property
    def details_label(self):
        try:
            formdef = self.formdef_class.get_by_slug(self.formdef_slug)
        except KeyError:
            return self.broken_error_message

        return formdef.name

    def set_attributes_from_trace(self, formdef, trace, previous_trace=None):
        created_formdef_id = trace.event_args['external_formdef_id']

        try:
            formdef = self.formdef_class.get(created_formdef_id)
        except KeyError:
            pass
        else:
            self.formdef_slug = formdef.slug

    def set_attributes_from_evolution(self, part):
        if not part.formdata or not self.formdef_slug:
            return

        try:
            formdef = self.formdef_class.get_by_slug(self.formdef_slug)
        except KeyError:
            return

        # get first content snapshot evolution part
        content_snapshot_part = next(part.formdata.iter_evolution_parts(klass=ContentSnapshotPart))

        for field in formdef.fields:
            if field.id not in content_snapshot_part.new_data:
                continue

            if field.key in ('file', 'block'):
                continue

            value = content_snapshot_part.new_data[field.id]
            if field.convert_value_to_str:
                value = field.convert_value_to_str(value)

            self.mappings.append(Mapping(field_id=field.id, expression=value))

    def perform(self, formdata):
        self.perform_checks(formdata)

    def perform_check(self, new_formdata):
        try:
            formdef = self.formdef_class.get_by_slug(self.formdef_slug)
        except KeyError:
            raise WorkflowTestError(self.broken_error_message)

        if new_formdata.formdef.url_name != self.formdef_slug:
            return self.checks_kind_error_message % {
                'formdef': formdef.name,
                'expected_formdef': new_formdata.formdef.name,
            }

        for mapping in self.mappings:
            try:
                field = [x for x in new_formdata.formdef.fields if x.id == mapping.field_id][0]
            except IndexError:
                return _('field "%s" is missing') % mapping.field_id

            with get_publisher().complex_data():
                value = WorkflowStatusItem.compute(mapping.expression, allow_complex=True)
                expected_value = get_publisher().get_cached_complex_data(value)

            actual_values = [new_formdata.data.get(field.id)]
            if '%s_display' % field.id in new_formdata.data:
                actual_values.append(new_formdata.data['%s_display' % field.id])
            if field.convert_value_to_str:
                actual_values.append(field.convert_value_to_str(actual_values[0]))

            if expected_value not in actual_values:
                return _('wrong value "%(value)s" for field "%(field)s" (should be "%(expected_value)s")') % {
                    'value': actual_values[-1],
                    'field': field.label,
                    'expected_value': expected_value,
                }

    def fill_admin_form(self, form, formdef):
        formdef_slugs = [
            x.formdef_slug for x in formdef.workflow.get_all_items() if x.key == self.action_name
        ]
        formdefs = self.formdef_class.select([Contains('slug', formdef_slugs)])
        if not formdefs:
            return

        form.add(
            SingleSelectWidget,
            'formdef_slug',
            title=_('Form'),
            value=self.formdef_slug,
            options=[(x.url_name, x.name, x.url_name) for x in formdefs],
        )

        formdef_slug = form.get('formdef_slug')
        try:
            to_formdef = [x for x in formdefs if x.url_name == formdef_slug][0]
        except IndexError:
            return

        widget = form.add(
            AssertFormCreationMappingsWidget,
            'mappings',
            to_formdef=to_formdef,
            value=self.mappings,
        )

        if form.is_submitted() and not widget.parse():
            widget.set_error(widget.REQUIRED_ERROR)
            form.ERROR_NOTICE = _('This action is configured in two steps. See below for details.')

    def export_mappings_to_xml(self, element, *args, **kwargs):
        for mapping in self.mappings or []:
            item = ET.SubElement(element, 'mapping')
            item.attrib['field_id'] = str(mapping.field_id)
            item.text = mapping.expression

    def import_mappings_from_xml(self, element, **kwargs):
        return [Mapping(field_id=x.attrib['field_id'], expression=x.text) for x in element.findall('mapping')]


class AssertCardCreation(AssertFormCreation):
    label = _('Card creation')
    empty_form_error = _('Workflow has no card creation action.')

    formdef_class = CardDef
    evolution_part_class = LinkedCardDataEvolutionPart
    action_name = 'create_carddata'
    formdata_test_attribute = 'created_carddata'
    broken_error_message = _('Broken, missing card')

    checks_object_name = _('Created card')
    checks_error_message = _('No created card matches expected criterias.')
    checks_empty_error_message = _('No card was created.')
    checks_kind_error_message = _('wrong card "%(formdef)s" (should be "%(expected_formdef)s")')

    key = 'assert-card-creation'


class AssertCardEdition(AssertCardCreation):
    label = _('Card edition')
    empty_form_error = _('Workflow has no card edit action.')

    evolution_part_class = None
    action_name = 'edit_carddata'
    formdata_test_attribute = 'edited_carddata'

    checks_object_name = _('Edited card')
    checks_error_message = _('No edited card matches expected criterias.')
    checks_empty_error_message = _('No card was edited.')

    key = 'assert-card-edition'


class AssertUserCanView(WorkflowTestAction):
    label = _('Visibility for a user')
    empty_form_error = _('There are no test users.')

    key = 'assert-user-can-view'
    user_uuid = None

    XML_NODES = WorkflowTestAction.XML_NODES + [
        ('user_uuid', 'str'),
    ]

    def get_user(self):
        if not self.user_uuid:
            return

        try:
            return get_publisher().test_user_class.select([Equal('test_uuid', self.user_uuid)])[0]
        except IndexError:
            pass

    @property
    def details_label(self):
        user = self.get_user()
        if not user:
            return _('Broken, missing user')

        return str(user)

    def perform(self, formdata):
        user = self.get_user()
        if not user:
            raise WorkflowTestError(_('Broken, missing user'))

        if not formdata.formdef.is_user_allowed_read(user, formdata):
            raise WorkflowTestError(_('User "%s" cannot view form') % user)

    def fill_admin_form(self, form, formdef):
        user_options = [
            (str(x.test_uuid), str(x), str(x.test_uuid))
            for x in get_publisher().test_user_class.select(order_by='name')
        ]

        if not user_options:
            return

        form.add(
            SingleSelectWidget,
            'user_uuid',
            options=user_options,
            value=self.user_uuid,
            **{'data-autocomplete': 'true'},
        )

    def get_dependencies(self):
        user = self.get_user()
        if user:
            yield user


class EditForm(ActionWithUserMixin, WorkflowTestAction):
    label = _('Edit form')
    empty_form_error = _('Workflow has no edit actions.')
    edit_button_label = _('Submit and go to form edition')
    edit_redirect_url = 'edit-form/'

    key = 'edit-form'
    form_data = None
    edit_action_id = None
    feed_last_test_result = False

    is_assertion = False

    XML_NODES = ActionWithUserMixin.XML_NODES + [
        ('edit_action_id', 'str'),
        ('form_data', 'json'),
        ('feed_last_test_result', 'bool'),
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.form_data = self.form_data or {}

    def export_json_to_xml(self, element, attribute_name, **kwargs):
        element.text = json.dumps(getattr(self, attribute_name), indent=2, sort_keys=True)

    def import_json_from_xml(self, element, **kwargs):
        return json.loads(element.text)

    @property
    def details_label(self):
        try:
            edit_action = self.get_workflow_edit_action(self.parent.testdef.formdef)
        except KeyError:
            return _('Broken, missing edit action')

        return _('"%(button_label)s" by %(user)s') % {
            'button_label': edit_action.get_button_label(),
            'user': self.get_user_label(),
        }

    def set_attributes_from_trace(self, formdef, trace, previous_trace):
        self.edit_action_id = '%s-%s' % (
            previous_trace.status_id.removeprefix('wf-'),
            trace.event_args['action_item_id'],
        )

    def get_workflow_edit_action(self, formdef):
        status_id, action_id = self.edit_action_id.split('-')
        status = formdef.workflow.get_status(status_id)
        action = status.get_item(action_id)
        if action.key != 'editable':
            raise KeyError()
        return action

    def perform(self, formdata):
        try:
            edit_action = self.get_workflow_edit_action(formdata.formdef)
        except KeyError:
            raise WorkflowTestError(_('Broken, missing edit action'))

        if edit_action.parent != formdata.get_status():
            raise WorkflowTestError(
                _('Form is not in the status containing edit action.'),
                details=[_('Status containing action: %s') % edit_action.parent.name],
            )

        if not edit_action.check_auth(formdata, self.get_user(formdata)):
            raise WorkflowTestError(_('Form edition is not allowed for user "%s".') % self.get_user_label())

        if not edit_action.check_condition(formdata):
            raise WorkflowTestError(_('Conditions for form edition were not met.'))

        self.parent.testdef.add_to_coverage(edit_action)

        testdef = copy.copy(self.parent.testdef)
        testdef.data = {'fields': self.form_data}

        try:
            testdef.run_form_fill(formdata.formdef, formdata.data, edit_action=edit_action)
        except TestError as e:
            raise WorkflowTestError(e.msg)

        edit_action.finish_edition(formdata, self.get_user(formdata))

    def fill_admin_form(self, form, formdef):
        edit_actions = [item for item in formdef.workflow.get_all_items() if item.key == 'editable']

        if not edit_actions:
            return

        form.add(
            SingleSelectWidget,
            'edit_action_id',
            title=_('Button label of edit action'),
            value=self.edit_action_id,
            options=[
                (
                    '%s-%s' % (x.parent.id, x.id),
                    _('%(button_label)s (in status %(status)s)')
                    % {'button_label': x.get_button_label(), 'status': x.parent.name},
                    '%s-%s' % (x.parent.id, x.id),
                )
                for x in edit_actions
            ],
        )

        self.add_user_fields(form)

        form.add(
            CheckboxWidget,
            'feed_last_test_result',
            value=self.feed_last_test_result,
            title=_('Use last test result in edition page'),
            hint=_(
                'If form has a condition based on workflow data, '
                'this option can be used to display the correct fields.'
            ),
        )
