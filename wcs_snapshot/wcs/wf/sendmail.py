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
import datetime

from django.utils.timezone import now
from quixote import get_publisher
from quixote.html import htmltext

from wcs.mail_templates import MailTemplate
from wcs.qommon import _, emails
from wcs.qommon.errors import TooBigEmailError
from wcs.qommon.form import (
    ComputedExpressionWidget,
    SingleSelectWidget,
    SingleSelectWidgetWithOther,
    StringWidget,
    TextWidget,
    VarnameWidget,
    Widget,
    WidgetList,
)
from wcs.qommon.misc import xml_node_text
from wcs.qommon.template import Template, TemplateError
from wcs.variables import LazyList, LazyUser
from wcs.workflows import (
    EvolutionPart,
    WorkflowImportUnknownReferencedError,
    WorkflowStatusItem,
    get_role_translation_label,
    register_item_class,
    template_on_formdata,
)


class EmailEvolutionPart(EvolutionPart):
    messages_id = None

    def __init__(self, varname, addresses, mail_subject, mail_body, messages_id):
        self.varname = varname
        self.addresses = addresses
        self.mail_subject = mail_subject
        self.mail_body = mail_body
        self.messages_id = messages_id
        self.datetime = now()


class SendmailWorkflowStatusItem(WorkflowStatusItem):
    description = _('Email')
    key = 'sendmail'
    category = 'interaction'
    support_substitution_variables = True

    to = []
    subject = None
    mail_template = None
    body = None
    custom_from = None
    attachments = None
    varname = None

    comment = None

    def get_dependencies(self):
        yield from super().get_dependencies()
        yield MailTemplate.get_by_slug(self.mail_template)

    def _get_role_id_from_xml(self, elem, include_id=False, snapshot=False):
        # override to allow for destination set with computed values.
        if elem is None:
            return None
        value = xml_node_text(elem)

        if self.get_expression(value)['type'] != 'text' or '@' in value:
            return value

        return super()._get_role_id_from_xml(elem, include_id=include_id, snapshot=snapshot)

    def to_export_to_xml(self, item, include_id=False):
        self._roles_export_to_xml('to', item, include_id=include_id, include_missing=True)

    def mail_template_init_with_xml(self, elem, include_id=False, snapshot=False):
        self.mail_template = None
        if elem is None:
            return
        value = xml_node_text(elem)
        if not value:
            return
        mail_template = MailTemplate.get_by_slug(value)
        if not mail_template:
            raise WorkflowImportUnknownReferencedError(
                _('Unknown referenced mail template'), details={_('Unknown mail templates'): {value}}
            )
        self.mail_template = value

    def render_list_of_roles_or_emails(self, roles):
        t = []
        for r in roles:
            expression = self.get_expression(r)
            if expression['type'] == 'template':
                t.append(_('computed value'))
            elif '@' in expression['value']:
                t.append(expression['value'])
            else:
                role_label = get_role_translation_label(self.get_workflow(), r)
                if role_label:
                    t.append(role_label)
        return ', '.join([str(x) for x in t])

    def get_to_parameter_view_value(self):
        return self.render_list_of_roles_or_emails(self.to)

    def get_line_details(self):
        if self.to:
            return _('to %s') % self.render_list_of_roles_or_emails(self.to)
        return _('not completed')

    def get_inspect_details(self):
        if self.to:
            return _('to %s') % self.render_list_of_roles_or_emails(self.to)

    def get_parameters(self):
        parameters = (
            'to',
            'mail_template',
            'subject',
            'body',
            'varname',
            'attachments',
            'custom_from',
            'condition',
        )
        if (
            not get_publisher().has_site_option('include-sendmail-custom-from-option')
            and not self.custom_from
        ):
            parameters = tuple(x for x in parameters if x != 'custom_from')
        return parameters

    def get_inspect_parameters(self):
        parameters = super().get_inspect_parameters()
        if self.mail_template:
            parameters.remove('subject')
            parameters.remove('body')
        return parameters

    def add_parameters_widgets(self, form, parameters, prefix='', formdef=None, **kwargs):
        super().add_parameters_widgets(form, parameters, prefix=prefix, formdef=formdef, **kwargs)
        subject_body_attrs = {}
        if 'subject' in parameters or 'body' in parameters:
            if MailTemplate.count():
                subject_body_attrs = {
                    'data-dynamic-display-value': '',
                    'data-dynamic-display-child-of': '%smail_template' % prefix,
                }

        if 'to' in parameters:
            form.add(
                WidgetList,
                '%sto' % prefix,
                title=_('To'),
                element_type=SingleSelectWidgetWithOther,
                value=self.to,
                add_element_label=self.get_add_role_label(),
                element_kwargs={
                    'render_br': False,
                    'other_widget_class': ComputedExpressionWidget,
                    'options': [(None, '---', None)]
                    + self.get_list_of_roles(include_logged_in_users=False, current_values=self.to),
                },
            )
        if 'subject' in parameters:
            form.add(
                StringWidget,
                '%ssubject' % prefix,
                title=_('Subject'),
                validation_function=ComputedExpressionWidget.validate_template,
                value=self.subject,
                size=40,
                attrs=subject_body_attrs,
            )
        if 'mail_template' in parameters and MailTemplate.count():
            form.add(
                SingleSelectWidget,
                '%smail_template' % prefix,
                title=_('Mail Template'),
                value=self.mail_template,
                options=[(None, '', '')] + MailTemplate.get_as_options_list(),
                attrs={'data-dynamic-display-parent': 'true'},
            )
        if 'body' in parameters:
            form.add(
                TextWidget,
                '%sbody' % prefix,
                title=_('Body'),
                value=self.body,
                cols=80,
                rows=10,
                validation_function=ComputedExpressionWidget.validate_template,
                attrs=subject_body_attrs,
            )

        if 'varname' in parameters:
            form.add(
                VarnameWidget,
                '%svarname' % prefix,
                required=False,
                title=_('Identifier'),
                value=self.varname,
                advanced=True,
                hint=_('This is used to provide access to action details.'),
            )

        if 'custom_from' in parameters:
            form.add(
                ComputedExpressionWidget,
                '%scustom_from' % prefix,
                title=_('Custom From Address'),
                value=self.custom_from,
                advanced=True,
            )

    def clean_subject(self, form):
        if not form.get_widget('mail_template') or not form.get_widget('mail_template').parse():
            if not form.get_widget('subject').parse():
                form.get_widget('subject').set_error(Widget.REQUIRED_ERROR)
            if not form.get_widget('body').parse():
                form.get_widget('body').set_error(Widget.REQUIRED_ERROR)

    def clean_to(self, form):
        widget = form.get_widget('to')
        for element_name in widget.element_names:
            select_or_other = widget.get_widget(element_name)
            if not select_or_other.has_error() and select_or_other.has_other_value:
                value = widget.get(element_name)
                if value and not ('@' in value or Template.is_template_string(value)):
                    select_or_other.set_error(_('Value must be a template or an email address.'))

    def get_message_id(self, formdata):
        hostname = get_publisher().tenant.hostname
        return 'wcs-%(type)s-%(formdef_id)s-%(formdata_id)s.%(ts)s@%(hostname)s' % {
            'type': formdata.formdef.data_sql_prefix,
            'formdef_id': formdata.formdef.id,
            'formdata_id': formdata.id,
            'ts': datetime.datetime.now().strftime('%Y%m%d.%H%M%S.%f'),
            'hostname': hostname,
        }

    def get_threads_headers(self, formdata, addresses):
        remaining = set(addresses)
        result = {}
        for part in formdata.iter_evolution_parts(klass=EmailEvolutionPart):
            if not part.messages_id:
                continue
            for msg_id, addrs in part.messages_id.items():
                concerned = {addr for addr in remaining if addr in addrs}
                remaining -= concerned
                if concerned:
                    result[msg_id] = (self.get_message_id(formdata), list(concerned))
                if len(remaining) == 0:
                    break
            if len(remaining) == 0:
                break
        else:
            # The key identify the email we are replying to. The first email
            # in a thread do not reply to any email : using None as key
            result[None] = (self.get_message_id(formdata), list(remaining))
        return result

    def get_body_parameter_view_value(self):
        return htmltext('<pre class="wrapping-pre">%s</pre>') % self.body

    def get_computed_strings(self):
        yield from super().get_computed_strings()
        if not self.mail_template:
            yield self.subject
            yield self.body
        if self.to:
            yield from self.to
        yield from (self.attachments or [])

    def perform(self, formdata, ignore_i18n=False):
        if not self.to:
            return

        if self.mail_template:
            mail_template = MailTemplate.get_by_slug(self.mail_template)
            if mail_template:
                body = mail_template.body
                subject = mail_template.subject
                extra_attachments = mail_template.attachments
            else:
                message = _('reference to invalid mail template %(mail_template)s in status %(status)s') % {
                    'status': self.parent.name,
                    'mail_template': self.mail_template,
                }
                get_publisher().record_error(message, formdata=formdata, status_item=self)
                return
        else:
            body = self.body
            subject = self.subject
            extra_attachments = None

        if not (subject and body):
            return

        if get_publisher().has_i18n_enabled() and not ignore_i18n:
            orig_to = self.to
            try:
                submitter_language = formdata.get_submitter_language()
                # send mail to submitter in submitter language
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

        subject = get_publisher().translate(subject)
        body = get_publisher().translate(body)

        try:
            mail_body = template_on_formdata(
                formdata,
                self.compute(body, render=False),
                autoescape=body.startswith('<'),
                raises=True,
                record_errors=False,
            )
        except TemplateError as e:
            get_publisher().record_error(
                _('Error in body template, mail could not be generated'), formdata=formdata, exception=e
            )
            return

        try:
            mail_subject = template_on_formdata(
                formdata,
                self.compute(subject, render=False),
                autoescape=False,
                raises=True,
                record_errors=False,
            )
        except TemplateError as e:
            get_publisher().record_error(
                _('Error in subject template, mail could not be generated'), formdata=formdata, exception=e
            )
            return

        # this works around the fact that parametric workflows only support
        # string values, so if we get set a string, we convert it here to an
        # array.
        if isinstance(self.to, str):
            self.to = [self.to]

        dests = []
        for dest in self.to:
            with get_publisher().complex_data():
                try:
                    dest = self.compute(dest, allow_complex=True, raises=True)
                except Exception:
                    continue
                else:
                    dest = get_publisher().get_cached_complex_data(dest)

            if not dest:
                continue

            if isinstance(dest, (LazyUser, get_publisher().user_class)):
                dests.append(dest)
                continue

            if not isinstance(dest, str):
                if isinstance(dest, LazyList):
                    dest = list(dest)
                if isinstance(dest, collections.abc.Iterable):
                    dests.extend(dest)
                continue

            if ',' in dest:
                # if the email contains a comma consider it as a serie of
                # emails
                dests.extend([x.strip() for x in dest.split(',')])
                continue

            if '@' in dest:
                dests.append(dest)
                continue

            if dest == '_submitter':
                submitter_email = formdata.formdef.get_submitter_email(formdata)
                if submitter_email:
                    dests.append(submitter_email)
                continue

            for real_dest in formdata.get_function_roles(dest):
                if real_dest.startswith('_user:'):
                    try:
                        user = get_publisher().user_class.get(real_dest.split(':')[1])
                    except KeyError:
                        continue
                    dests.append(user)
                    continue

                try:
                    role = get_publisher().role_class.get(real_dest)
                except KeyError:
                    continue
                dests.extend(role.get_emails())

        addresses = set()
        for value in dests:
            if not value:
                continue
            if isinstance(value, LazyUser):
                value = value._user
            if isinstance(value, get_publisher().user_class):
                if value.email:
                    addresses.add(value.email)
                continue
            addresses.add(value)

        if not addresses:
            return

        email_from = None
        if self.custom_from:
            email_from = self.compute(self.custom_from)

        common_kwargs = {
            'email_from': email_from,
            'attachments': list(self.convert_attachments_to_uploads(extra_attachments)),
        }

        threads_headers = self.get_threads_headers(formdata, addresses)

        formdata.evolution[-1].add_part(
            EmailEvolutionPart(
                varname=self.varname,
                addresses=addresses,
                mail_subject=mail_subject,
                mail_body=mail_body,
                messages_id={msg_id: addrs for dummy, (msg_id, addrs) in threads_headers.items()},
            )
        )
        formdata.store()

        for first_id, (message_id, addrs) in threads_headers.items():
            if first_id:
                reply_headers = {'In-Reply-To': f'<{first_id}>', 'References': f'<{first_id}>'}
            else:
                reply_headers = {}

            if len(addresses) > 1:
                dest_kwargs = {'email_rcpt': None, 'bcc': addrs}
            else:
                dest_kwargs = {'email_rcpt': addresses}

            email_kwargs = {
                **common_kwargs,
                **dest_kwargs,
                'extra_headers': {'Message-ID': f'<{message_id}>', **reply_headers},
            }
            try:
                email = emails.get_email(mail_subject, mail_body, **email_kwargs)

                if email:
                    self.send_email(email)
            except TooBigEmailError as e:
                get_publisher().record_error(str(e), formdata=formdata, status_item=self)

    def send_email(self, email):
        emails.send_email(email, fire_and_forget=True)

    def i18n_scan(self, base_location):
        location = '%sitems/%s/' % (base_location, self.id)
        if not self.mail_template:
            yield location, None, self.subject
            yield location, None, self.body

    def perform_in_tests(self, formdata):
        def record_email(email):
            formdata.sent_emails.append(email)
            email.workflow_test_addresses = formdata.evolution[-1].parts[-1].addresses

        setattr(self, 'send_email', record_email)

        self.perform(formdata)


register_item_class(SendmailWorkflowStatusItem)


class LazyFormDataEmailsBase:
    def __init__(self, formdata):
        self._formdata = formdata

    def __getattr__(self, varname):
        email_parts = []
        for part in self._formdata.iter_evolution_parts(EmailEvolutionPart):
            if part.varname == varname:
                email_parts.append(LazyFormDataEmail(part))
        if email_parts:
            return LazyFormDataEmails(email_parts)
        raise AttributeError(varname)

    def inspect_keys(self):
        varnames = set()
        for part in self._formdata.iter_evolution_parts(EmailEvolutionPart):
            if part.varname:
                varnames.add(part.varname)
        yield from varnames


class LazyFormDataEmails:
    def __init__(self, email_parts):
        self._email_parts = email_parts

    def inspect_keys(self):
        keys = self._email_parts[-1].inspect_keys()
        if len(self._email_parts) > 1:
            # if multiple emails with same varname have been sent, advertise
            # access via indices.
            keys.extend([str(x) for x in range(len(self._email_parts))])
        return keys

    def __getitem__(self, key):
        try:
            key = int(key)
        except ValueError:
            try:
                return getattr(self, key)
            except AttributeError:
                return self._email_parts[-1][key]
        return self._email_parts[key]

    def __len__(self):
        return len(self._email_parts)

    def __iter__(self):
        yield from self._email_parts


class LazyFormDataEmail:
    def __init__(self, part):
        self.part = part

    def inspect_keys(self):
        return ['addresses', 'body', 'subject', 'datetime']

    def __getitem__(self, key):
        try:
            return getattr(self, key)
        except AttributeError:
            raise KeyError(key)

    @property
    def addresses(self):
        return self.part.addresses

    @property
    def datetime(self):
        return self.part.datetime

    @property
    def body(self):
        return self.part.mail_body

    @property
    def subject(self):
        return self.part.mail_subject
