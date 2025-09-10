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

import copy
import re

from quixote import get_publisher, get_session
from quixote.html import htmltext

from wcs.qommon import _, ezt, misc
from wcs.qommon.form import (
    ComputedExpressionWidget,
    SingleSelectWidget,
    WidgetListOfRoles,
    get_rich_text_widget_class,
)
from wcs.qommon.template import Template
from wcs.workflows import WorkflowGlobalAction, WorkflowStatusItem, register_item_class


class DisplayMessageWorkflowStatusItem(WorkflowStatusItem):
    description = _('Alert')
    key = 'displaymsg'
    category = 'interaction'
    support_substitution_variables = True
    ok_in_global_action = True

    to = None
    position = 'top'
    level = None
    message = None

    def get_line_details(self):
        in_global_action = isinstance(self.parent, WorkflowGlobalAction)
        parts = []
        if in_global_action:
            pass
        elif self.position == 'top':
            parts.append(_('top of page'))
        elif self.position == 'bottom':
            parts.append(_('bottom of page'))
        elif self.position == 'actions':
            parts.append(_('with actions'))
        if self.to:
            parts.append(_('for %s') % self.render_list_of_roles(self.to))
        return ', '.join([str(x) for x in parts])

    def get_computed_strings(self):
        yield from super().get_computed_strings()
        yield self.message

    def migrate(self):
        changed = super().migrate()
        if not self.level:  # 2023-08-15
            match = re.match(
                r'^<div class="(error|info|warning|success)notice">(.*)</div>$', (self.message or '').strip()
            )
            if match:
                self.level, self.message = match.groups(0)
                changed = True
        return changed

    def get_message_context(self, formdata):
        if formdata:
            ctx = copy.copy(get_publisher().substitutions.get_context_variables('lazy'))
            ctx['date'] = misc.localstrftime(formdata.receipt_time)
            ctx['number'] = formdata.id
            handling_role = formdata.get_handling_role()
            if handling_role and handling_role.details:
                ctx['receiver'] = handling_role.details.replace('\n', '<br />')
        else:
            ctx = get_publisher().substitutions.get_context_variables('lazy')
        return ctx

    def get_message(self, formdata, position='top'):
        if not self.message:
            return ''
        if formdata and not formdata.is_for_current_user(self.to):
            return ''

        in_global_action = isinstance(self.parent, WorkflowGlobalAction)
        if self.position != position and not in_global_action:
            return

        ctx = self.get_message_context(formdata)
        message = get_publisher().translate(self.message)

        if self.level:
            message = '<div class="%snotice">%s</div>' % (self.level, message)
        elif not message.startswith('<'):
            message = '<p>%s</p>' % message

        try:
            return Template(message, ezt_format=ezt.FORMAT_HTML, raises=True, record_errors=False).render(ctx)
        except Exception as e:
            get_publisher().record_error(
                error_summary=_('Error in template of workflow message (%s)') % e, exception=e, notify=True
            )
            return '<div class="errornotice">%s</div>' % _('Error rendering message.')

    def add_parameters_widgets(self, form, parameters, prefix='', formdef=None, **kwargs):
        super().add_parameters_widgets(form, parameters, prefix=prefix, formdef=formdef, **kwargs)
        in_global_action = isinstance(self.parent, WorkflowGlobalAction)
        if 'message' in parameters:
            form.add(
                get_rich_text_widget_class(self.message, usage='wf-displaymsg'),
                '%smessage' % prefix,
                title=_('Message'),
                value=self.message,
                cols=80,
                rows=10,
                validation_function=ComputedExpressionWidget.validate_template,
            )
        if 'level' in parameters:
            form.add(
                SingleSelectWidget,
                '%slevel' % prefix,
                title=_('Level'),
                value=self.level,
                options=[
                    (None, ''),
                    ('success', _('Success')),
                    ('info', _('Information')),
                    ('warning', _('Warning')),
                    ('error', _('Error')),
                ],
            )
        if 'position' in parameters and not in_global_action:
            form.add(
                SingleSelectWidget,
                '%sposition' % prefix,
                title=_('Position'),
                value=self.position,
                options=[
                    ('top', _('Top of page')),
                    ('bottom', _('Bottom of page')),
                    # ('actions', _('With actions'))  "too complicated"
                ],
            )
        if 'to' in parameters:
            form.add(
                WidgetListOfRoles,
                '%sto' % prefix,
                title=_('To'),
                value=self.to or [],
                add_element_label=self.get_add_role_label(),
                first_element_empty_label=_('Everybody'),
                roles=self.get_list_of_roles(include_logged_in_users=False),
            )

    def get_parameters(self):
        return ('to', 'message', 'level', 'position', 'condition')

    def get_message_parameter_view_value(self):
        if self.message.startswith('<'):
            return htmltext(self.message)
        return htmltext('<pre>%s</pre>') % self.message

    def i18n_scan(self, base_location):
        location = '%sitems/%s/' % (base_location, self.id)
        yield location, None, self.message

    def perform_in_tests(self, formdata):
        self.perform(formdata)

    def perform(self, formdata):
        if not isinstance(self.parent, WorkflowGlobalAction) or self.parent.is_interactive():
            return

        if formdata and not formdata.is_for_current_user(self.to):
            return

        ctx = self.get_message_context(formdata)
        message = get_publisher().translate(self.message)

        if not message:
            return

        if not message.startswith('<'):
            message = '<p>%s</p>' % message

        try:
            message = Template(message, ezt_format=ezt.FORMAT_HTML, raises=True, record_errors=False).render(
                ctx
            )
        except Exception as e:
            get_publisher().record_error(
                error_summary=_('Error in template of workflow message (%s)') % e, exception=e, notify=True
            )
            return

        if get_session():
            get_session().add_html_message(message, level=self.level)


register_item_class(DisplayMessageWorkflowStatusItem)
