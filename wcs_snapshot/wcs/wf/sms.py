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

import collections.abc

from quixote import get_publisher

from wcs.qommon import _, errors, get_cfg, sms
from wcs.qommon.form import ComputedExpressionWidget, RadiobuttonsWidget, TextWidget, WidgetList
from wcs.qommon.template import TemplateError
from wcs.variables import LazyList
from wcs.workflows import WorkflowStatusItem, register_item_class, template_on_formdata


class SendSMSWorkflowStatusItem(WorkflowStatusItem):
    description = _('SMS')
    key = 'sendsms'
    category = 'interaction'
    support_substitution_variables = True

    to_mode = None  # submitter or other
    to = []
    body = None
    counter_name = None

    # don't use roles (de)serializer for "to" field
    to_export_to_xml = None
    to_init_with_xml = None

    def migrate(self):
        changed = super().migrate()
        if self.to_mode is None and self.to:  # 2023-12-05
            self.to_mode = 'other'
            changed = True
        return changed

    @classmethod
    def is_available(cls, workflow=None):
        sms_cfg = get_cfg('sms', {})
        return bool(sms_cfg.get('sender') and sms_cfg.get('passerelle_url'))

    def get_parameters(self):
        return ('to_mode', 'to', 'body', 'counter_name', 'condition')

    def add_parameters_widgets(self, form, parameters, prefix='', formdef=None, **kwargs):
        super().add_parameters_widgets(form, parameters, prefix=prefix, formdef=formdef, **kwargs)
        if 'to_mode' in parameters:
            form.add(
                RadiobuttonsWidget,
                f'{prefix}to_mode',
                title=_('To'),
                options=(('submitter', _('Submitter'), 'submitter'), ('other', _('Other'), 'other')),
                value=self.to_mode or 'submitter',
                required=True,
                attrs={'data-dynamic-display-parent': 'true'},
                extra_css_class='widget-inline-radio',
            )
        if 'to' in parameters:
            form.add(
                WidgetList,
                '%sto' % prefix,
                element_type=ComputedExpressionWidget,
                value=self.to,
                add_element_label=_('Add Number'),
                element_kwargs={'render_br': False},
                attrs={
                    'data-dynamic-display-child-of': f'{prefix}to_mode',
                    'data-dynamic-display-value': 'other',
                },
            )
        if 'body' in parameters:
            form.add(TextWidget, '%sbody' % prefix, title=_('Body'), value=self.body, cols=80, rows=10)
        if 'counter_name' in parameters:
            form.add(
                ComputedExpressionWidget,
                '%scounter_name' % prefix,
                title=_('Counter name'),
                hint=_('This name will be available to filter SMS sending statistics.'),
                value=self.counter_name,
                advanced=True,
            )

    def get_computed_strings(self):
        yield from super().get_computed_strings()
        yield self.body
        if self.to:
            yield from self.to

    def perform(self, formdata):
        if not self.is_available():
            return
        if self.to_mode == 'other' and not self.to:
            return
        if not self.body:
            return

        recipients = []

        if self.to_mode in (None, 'submitter'):
            recipients.append(formdata.formdef.get_submitter_phone(formdata))

        for dest in self.to or []:
            with get_publisher().complex_data():
                try:
                    dest = self.compute(dest, allow_complex=True, raises=True)
                except Exception:
                    continue
                else:
                    dest = get_publisher().get_cached_complex_data(dest)

            if not dest:
                continue

            if isinstance(dest, list):
                recipients.extend(dest)
            if not isinstance(dest, str):
                if isinstance(dest, LazyList):
                    dest = list(dest)
                if isinstance(dest, collections.abc.Iterable):
                    recipients.extend(dest)
                continue

            if ',' in dest:
                # if the recipient contains a comma consider it as a serie of
                # numbers
                recipients.extend([x.strip() for x in dest.split(',')])
                continue

            recipients.append(dest)

        recipients = list({x for x in recipients if x})  # deduplicate & ignore empty elements
        if not recipients:
            return

        try:
            sms_body = template_on_formdata(
                formdata,
                self.compute(get_publisher().translate(self.body), render=False),
                autoescape=False,
                raises=True,
                record_errors=False,
            )
        except TemplateError as e:
            get_publisher().record_error(
                _('Error in template, SMS could not be generated'), formdata=formdata, exception=e
            )
            return

        counter_name = None
        if self.counter_name:
            try:
                counter_name = template_on_formdata(
                    formdata,
                    self.compute(self.counter_name, render=False),
                    autoescape=False,
                    raises=True,
                    record_errors=False,
                )
            except TemplateError as e:
                get_publisher().record_error(
                    _('Error in counter template, sms could not be generated'), formdata=formdata, exception=e
                )
                return

        sms_cfg = get_cfg('sms', {})
        sender = sms_cfg.get('sender', 'AuQuotidien')[:11]
        try:
            self.send_sms(sender, recipients, sms_body, counter_name)
        except errors.SMSError as e:
            get_publisher().record_error(_('Could not send SMS'), formdata=formdata, exception=e)

    def send_sms(self, sender, recipients, sms_body, counter_name):
        sms.SMS.get_sms_class().send(sender, recipients, sms_body, counter_name)

    def i18n_scan(self, base_location):
        location = '%sitems/%s/' % (base_location, self.id)
        yield location, None, self.body

    def perform_in_tests(self, formdata):
        def record_sms(sender, recipients, sms_body, counter_name):
            formdata.sent_sms.append(
                {'phone_numbers': recipients, 'body': sms_body, 'counter_name': counter_name}
            )

        setattr(self, 'send_sms', record_sms)

        self.perform(formdata)


register_item_class(SendSMSWorkflowStatusItem)
