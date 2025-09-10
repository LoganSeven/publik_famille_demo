# w.c.s. - web application for online forms
# Copyright (C) 2005-2019  Entr'ouvert
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

import urllib.parse

from quixote import get_publisher

from wcs.variables import LazyList, LazyUser
from wcs.workflows import WorkflowStatusItem, register_item_class, template_on_formdata

from ..qommon import _
from ..qommon.form import ComputedExpressionWidget, SingleSelectWidget, StringWidget, TextWidget
from ..qommon.template import TemplateError
from .wscall import WebserviceCallStatusItem


class SendNotificationWorkflowStatusItem(WebserviceCallStatusItem):
    description = _('User Notification')
    key = 'notification'
    category = 'interaction'
    support_substitution_variables = True

    # parameters
    to = ['_submitter']
    users_template = None
    title = None
    body = None
    origin = None
    target_url = None

    # webservice parameters
    varname = 'notification'
    post = False
    _method = 'POST'
    response_type = 'json'

    action_on_app_error = ':pass'
    action_on_4xx = ':pass'
    action_on_5xx = ':pass'
    action_on_bad_data = ':pass'
    action_on_network_errors = ':pass'
    notify_on_errors = True
    record_errors = False

    @classmethod
    def is_available(cls, workflow=None):
        return bool(cls.get_api_url() is not None)

    @classmethod
    def get_api_url(cls):
        for variable_name in ('_interco_portal_url', 'portal_url'):
            url = get_publisher().get_site_option(variable_name, 'variables')
            if url:
                return urllib.parse.urljoin(url, '/api/notification/add/')
        return None

    def get_jump_label(self, target_id):
        return _(self.description)

    def get_parameters(self):
        return ('to', 'users_template', 'title', 'body', 'target_url', 'origin', 'condition')

    def add_parameters_widgets(self, form, parameters, prefix='', formdef=None, **kwargs):
        if 'to' in parameters:
            # never displayed in the current UI (no 'to' in get_parameters)
            options = [(None, '---', None)] + self.get_list_of_roles(include_logged_in_users=False)
            options.append(('__other', _('Other (from template)'), '__other'))
            form.add(
                SingleSelectWidget,
                '%sto' % prefix,
                title=_('To'),
                value=self.to[0] if self.to else '__other',
                options=options,
                attrs={'data-dynamic-display-parent': 'true'},
            )
        if 'users_template' in parameters:
            form.add(
                ComputedExpressionWidget,
                '%susers_template' % prefix,
                title=_('Users template'),
                value=self.users_template,
                attrs={
                    'data-dynamic-display-child-of': '%sto' % prefix,
                    'data-dynamic-display-value': '__other',
                },
            )
        if 'title' in parameters:
            form.add(
                StringWidget,
                '%stitle' % prefix,
                title=_('Title'),
                value=self.title,
                size=80,
                validation_function=ComputedExpressionWidget.validate_template,
            )
        if 'body' in parameters:
            form.add(
                TextWidget,
                '%sbody' % prefix,
                title=_('Body'),
                value=self.body,
                cols=80,
                rows=5,
                validation_function=ComputedExpressionWidget.validate_template,
            )
        if 'target_url' in parameters:
            form.add(
                StringWidget,
                '%starget_url' % prefix,
                title=_('URL'),
                value=self.target_url,
                required=False,
                advanced=True,
                hint=_(
                    'Defaults to card/form URL. Common variables are available with the {{variable}} syntax.'
                ),
            )
        if 'origin' in parameters:
            form.add(
                StringWidget,
                '%sorigin' % prefix,
                title=_('Origin'),
                value=self.origin,
                required=False,
                advanced=True,
            )
        WorkflowStatusItem.add_parameters_widgets(
            self, form, parameters, prefix=prefix, formdef=formdef, **kwargs
        )

    def submit_admin_form(self, form):
        super().submit_admin_form(form)
        if not form.has_errors():
            if self.to == '__other':
                self.to = []
            elif self.to and isinstance(self.to, str):
                self.to = [self.to]
                self.users_template = None

    def get_computed_strings(self):
        yield from super().get_computed_strings()
        yield from [self.title, self.body, self.target_url]
        if not self.to:
            yield self.users_template

    def perform(self, formdata, ignore_i18n=False):
        if not (self.is_available() and (self.to or self.users_template) and self.title):
            return

        if get_publisher().has_i18n_enabled() and not ignore_i18n:
            orig_to = self.to
            try:
                submitter_language = formdata.get_submitter_language()
                # send notification to submitter in submitter language
                if submitter_language and '_submitter' in self.to:
                    with get_publisher().with_language(submitter_language):
                        to = self.to
                        self.to = ['_submitter']
                        self.perform(formdata, ignore_i18n=True)
                        self.to = [x for x in to if x != '_submitter']
                # and others in the site language
                with get_publisher().with_language(get_publisher().get_default_language()):
                    self.perform(formdata, ignore_i18n=True)
            finally:
                # restore attribute value
                self.to = orig_to
            return

        title = get_publisher().translate(self.title)

        try:
            title = template_on_formdata(
                formdata, self.compute(title, render=False), autoescape=False, record_errors=False
            )
        except TemplateError as e:
            get_publisher().record_error(
                _('error in template for title, notification could not be generated'), exception=e
            )
            return

        body = self.body
        if body:
            body = get_publisher().translate(self.body)
            try:
                body = template_on_formdata(
                    formdata, self.compute(body, render=False), autoescape=False, record_errors=False
                )
            except TemplateError as e:
                get_publisher().record_error(
                    _('error in template for body, notification could not be generated'), exception=e
                )
                return

        users = []
        if self.to:
            for dest in self.to:
                if dest == '_submitter':
                    users.append(formdata.get_user())
                    continue

                for dest_id in formdata.get_function_roles(dest):
                    try:
                        role = get_publisher().role_class.get(dest_id)
                    except KeyError:
                        continue
                    users.extend(get_publisher().user_class.get_users_with_role(role.id))
        else:
            with get_publisher().complex_data():
                try:
                    to = self.compute(self.users_template, allow_complex=True, raises=True, formdata=formdata)
                except Exception:
                    return
                to = get_publisher().get_cached_complex_data(to)

            if isinstance(to, LazyList):
                to = list(to)
            elif isinstance(to, str):
                to = [x.strip() for x in to.split(',')]
            if not isinstance(to, list):
                get_publisher().record_error(
                    _('Failed to notify users, bad template result (%s)') % to,
                    formdata=formdata,
                    status_item=self,
                )
                return
            to = [v for v in to if v]
            for value in to:
                if isinstance(value, LazyUser):
                    value = value._user
                if isinstance(value, get_publisher().user_class):
                    users.append(value)
                else:
                    user = get_publisher().user_class.lookup_by_string(str(value))
                    if not user:
                        get_publisher().record_error(
                            _('Failed to notify user (not found: "%s")') % value,
                            formdata=formdata,
                            status_item=self,
                        )
                        continue
                    users.append(user)

        name_ids = set()
        for user in users:
            if not user or not user.is_active or user.deleted_timestamp:
                continue
            for name_id in user.name_identifiers or []:
                name_ids.add(name_id)

        if not name_ids:
            return

        if self.target_url:
            target_url = self.compute(self.target_url, allow_ezt=False)
        else:
            target_url = formdata.get_url()

        self.post_data = {
            'summary': title,
            'body': body,
            'url': target_url,
            'origin': self.origin or '',
            'id': 'formdata:%s' % formdata.get_display_id(),
            'name_ids': list(name_ids),
        }
        self.url = self.get_api_url()

        super().perform(formdata)

    def i18n_scan(self, base_location):
        location = '%sitems/%s/' % (base_location, self.id)
        yield location, None, self.title
        yield location, None, self.body


register_item_class(SendNotificationWorkflowStatusItem)
