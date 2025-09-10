# w.c.s. - web application for online forms
# Copyright (C) 2005-2010  Entr'ouvert
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

import contextlib

from quixote import get_publisher

from wcs.workflows import WorkflowStatusItem, register_item_class

from ..qommon import _, emails
from ..qommon.cron import CronJob
from ..qommon.form import SingleSelectWidget, WidgetList
from ..qommon.publisher import get_publisher_class
from ..qommon.storage import StorableObject


class AggregationEmailWorkflowStatusItem(WorkflowStatusItem):
    description = _('Daily Summary Email')
    key = 'aggregationemail'
    category = 'interaction'
    ok_in_global_action = False

    to = []

    def get_line_details(self):
        if self.to:
            return _('to %s') % self.render_list_of_roles(self.to)
        return _('not completed')

    def add_parameters_widgets(self, form, parameters, prefix='', formdef=None, **kwargs):
        super().add_parameters_widgets(form, parameters, prefix=prefix, formdef=formdef, **kwargs)
        if 'to' in parameters:
            form.add(
                WidgetList,
                '%sto' % prefix,
                title=_('To'),
                element_type=SingleSelectWidget,
                value=self.to,
                add_element_label=self.get_add_role_label(),
                element_kwargs={
                    'render_br': False,
                    'options': [(None, '---', None)]
                    + self.get_list_of_roles(include_submitter=False, include_logged_in_users=False),
                },
            )

    def get_parameters(self):
        return ('to', 'condition')

    def perform(self, formdata):
        if not self.to:
            return

        for dest in self.to:
            for dest_id in formdata.get_function_roles(dest):
                try:
                    aggregate = AggregationEmail.get(dest_id)
                except KeyError:
                    aggregate = AggregationEmail(id=dest_id)

                aggregate.append(
                    {'formdef': formdata.formdef.id, 'formdata': formdata.id, 'formurl': formdata.get_url()}
                )
                aggregate.store()


register_item_class(AggregationEmailWorkflowStatusItem)


class AggregationEmail(StorableObject):
    _names = 'aggregation_emails'

    items = None

    def __init__(self, id=None):
        StorableObject.__init__(self, id=id)
        self.items = []

    def append(self, dict):
        self.items.append(dict)


def lax_int(s):
    try:
        return int(s)
    except (ValueError, TypeError):
        return 10000


def send_aggregation_emails(publisher, **kwargs):
    from wcs.formdef import FormDef

    publisher.reload_cfg()
    site_name = publisher.cfg.get('misc', {}).get('sitename', None)
    job = kwargs.pop('job', None)

    cache = {}
    for aggregate_id in AggregationEmail.keys():
        with job.log_long_job('aggregation email %s' % aggregate_id) if job else contextlib.ExitStack():
            aggregate = AggregationEmail.get(aggregate_id)
            aggregate.remove_self()

            try:
                role = get_publisher().role_class.get(aggregate_id)
            except KeyError:
                continue
            if not role.get_emails():
                continue
            if not aggregate.items:
                continue

            last_formdef = None
            body = []
            for item in sorted(
                aggregate.items, key=lambda x: (lax_int(x['formdef']), lax_int(x['formdata']))
            ):
                formdef_id = item.get('formdef')
                if formdef_id in cache:
                    formdef, formdata, workflow = cache[formdef_id]
                else:
                    try:
                        formdef = FormDef.get(formdef_id)
                    except KeyError:
                        # formdef has been deleted after AggregationEmail creation
                        continue
                    formdata = formdef.data_class()
                    workflow = formdef.workflow
                    cache[formdef_id] = (formdef, formdata, workflow)

                try:
                    data = formdata.get(item.get('formdata'))
                except KeyError:
                    continue
                status = data.get_status()
                url = item.get('formurl')

                if last_formdef != formdef:
                    if last_formdef is not None:
                        body.append('')  # blank line
                    last_formdef = formdef
                    body.append(formdef.name)
                    body.append('-' * len(formdef.name))
                    body.append('')

                body.append('- %sstatus (%s)' % (url, status.name))

            if not body:
                continue

            body = '\n'.join(body)

            mail_subject = _('New arrivals')
            if site_name:
                mail_subject += ' (%s)' % site_name

            emails.email(mail_subject, body, email_rcpt=role.get_emails())


def register_cronjob():
    # at 6:00 in the morning, every day but the week end
    get_publisher_class().register_cronjob(
        CronJob(
            send_aggregation_emails, name='send_aggregation_emails', hours=[6], minutes=[0], weekdays=range(5)
        )
    )
