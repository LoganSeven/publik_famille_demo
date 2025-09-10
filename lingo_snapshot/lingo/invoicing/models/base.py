# lingo - payment and billing system
# Copyright (C) 2025  Entr'ouvert
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import collections
import uuid

from django.core.serializers.json import DjangoJSONEncoder
from django.db import models
from django.template.defaultfilters import linebreaksbr
from django.utils.timezone import localtime
from django.utils.translation import gettext_lazy as _

DOCUMENT_MODELS = [
    ('basic', _('Basic')),
    ('middle', _('Middle')),
    ('full', _('Full')),
]

ORIGINS = [
    ('api', _('API')),
    ('basket', _('Basket')),
    ('campaign', _('Campaign')),
]


@models.JSONField.register_lookup
class MatchJsonPath(models.lookups.PostgresOperatorLookup):
    lookup_name = 'jsonpath_exists'
    postgres_operator = '@?'
    prepare_rhs = False


def set_numbers(instance, counter_date, counter_kind):
    from lingo.invoicing.models import Counter

    instance.number = Counter.get_count(
        regie=instance.regie,
        name=instance.regie.get_counter_name(counter_date),
        kind=counter_kind,
    )
    instance.formatted_number = instance.regie.format_number(counter_date, instance.number, counter_kind)


def get_cancellation_info(obj):
    result = []
    if not obj.cancelled_at:
        return result
    result.append((_('Cancelled on'), localtime(obj.cancelled_at).strftime('%d/%m/%Y %H:%M')))
    if obj.cancelled_by:
        result.append((_('Cancelled by'), obj.cancelled_by))
    result.append((_('Reason'), obj.cancellation_reason))
    if obj.cancellation_description:
        result.append((_('Description'), linebreaksbr(obj.cancellation_description)))
    return result


class AbstractInvoiceObject(models.Model):
    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    regie = models.ForeignKey('invoicing.Regie', on_delete=models.PROTECT)
    label = models.CharField(_('Label'), max_length=300)
    total_amount = models.DecimalField(max_digits=9, decimal_places=2, default=0)
    payer_external_id = models.CharField(max_length=250)
    payer_first_name = models.CharField(max_length=250)
    payer_last_name = models.CharField(max_length=250)
    payer_address = models.TextField()
    payer_email = models.CharField(max_length=250, blank=True)
    payer_phone = models.CharField(max_length=250, blank=True)

    date_invoicing = models.DateField(_('Invoicing date'), null=True)
    date_publication = models.DateField(
        _('Publication date'), help_text=_('Date on which the invoice is visible on the portal.')
    )
    pool = models.ForeignKey('invoicing.Pool', on_delete=models.PROTECT, null=True)
    usable = models.BooleanField(default=True)
    previous_invoice = models.ForeignKey('Invoice', null=True, on_delete=models.SET_NULL)
    origin = models.CharField(choices=ORIGINS)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True, null=True)

    class Meta:
        abstract = True

    @property
    def payer_name(self):
        payer_name = '%s %s' % (self.payer_first_name, self.payer_last_name)
        return payer_name.strip()

    @property
    def payer_external_raw_id(self):
        if ':' in self.payer_external_id:
            return self.payer_external_id.split(':')[1]
        return self.payer_external_id

    @property
    def invoice_model(self):
        if self.pool:
            return self.pool.campaign.invoice_model
        return self.regie.invoice_model

    def get_grouped_and_ordered_lines(self):
        lines = []
        possible_status = ['presence', 'absence', 'cancelled', '']
        for line in self.lines.all():
            # build event date/datetime, and check status
            event_date = line.event_date.strftime('Y-m-d')
            event_status = 'z' * 5000
            if line.details:
                event_date = '%s:%s' % (line.event_date, line.details.get('event_time') or '')
                status = line.details.get('status') or ''
                event_status = '%s:%s' % (possible_status.index(status), status)
                if status in ['presence', 'absence'] and line.details.get('check_type_label'):
                    # note: presence without reason will be sorted first
                    event_status += ':%s' % line.details['check_type_label']
                if status == 'absence' and not line.details.get('check_type_label'):
                    # so absence without reason will be sorted last
                    event_status += ':%s' % ('z' * 5000)
            lines.append(
                (
                    line,
                    # sort by user
                    line.user_external_id,
                    # by activity
                    line.activity_label or 'z' * 5000,
                    # by date/datetime
                    event_date,
                    # by slug
                    line.event_slug,
                    # by check status
                    event_status,
                    # and pk
                    line.pk,
                )
            )
        lines = sorted(
            lines,
            key=lambda li: li[1:],
        )
        lines = [li[0] for li in lines]
        return lines

    def get_lines_by_user(self):
        lines = self.get_grouped_and_ordered_lines()
        lines_by_user = collections.defaultdict(list)
        for line in lines:
            lines_by_user[line.user_external_id].append(line)
        lines_by_user = list(lines_by_user.items())
        return sorted(lines_by_user, key=lambda li: li[0])


class AbstractInvoiceLineObject(models.Model):
    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    event_date = models.DateField()
    label = models.TextField()
    quantity = models.DecimalField(max_digits=9, decimal_places=2)
    unit_amount = models.DecimalField(max_digits=9, decimal_places=2)
    total_amount = models.DecimalField(max_digits=9, decimal_places=2)
    description = models.TextField()
    event_slug = models.CharField(max_length=250)
    event_label = models.CharField(max_length=260)
    agenda_slug = models.CharField(max_length=250)
    activity_label = models.CharField(max_length=250)
    details = models.JSONField(default=dict, encoder=DjangoJSONEncoder)
    accounting_code = models.CharField(max_length=250, blank=True)
    form_url = models.URLField(blank=True)

    user_external_id = models.CharField(max_length=250)
    user_first_name = models.CharField(max_length=250)
    user_last_name = models.CharField(max_length=250)

    pool = models.ForeignKey('invoicing.Pool', on_delete=models.PROTECT, null=True)

    created_at = models.DateTimeField(auto_now_add=True, null=True)
    updated_at = models.DateTimeField(auto_now=True, null=True)

    class Meta:
        abstract = True

    @property
    def user_name(self):
        user_name = '%s %s' % (self.user_first_name, self.user_last_name)
        return user_name.strip()

    def display_description(self):
        if self.description == '@overtaking@':
            return False
        return True
