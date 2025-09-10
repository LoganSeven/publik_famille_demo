# lingo - payment and billing system
# Copyright (C) 2023  Entr'ouvert
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

import datetime
import decimal
import json
import uuid

import requests
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models, transaction
from django.test.client import RequestFactory
from django.utils.functional import cached_property
from django.utils.timezone import now
from django.utils.translation import gettext_lazy as _

from lingo.callback.models import Callback, CallbackFailure
from lingo.invoicing.models import (
    Credit,
    CreditAssignment,
    DraftInvoice,
    Invoice,
    InvoiceCancellationReason,
    Payment,
    PaymentType,
    Regie,
)
from lingo.utils.requests_wrapper import requests as requests_wrapper


class Basket(models.Model):
    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)

    regie = models.ForeignKey(Regie, on_delete=models.PROTECT)
    draft_invoice = models.ForeignKey(DraftInvoice, on_delete=models.PROTECT, related_name='+')
    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, null=True)
    credit = models.ForeignKey(Credit, on_delete=models.PROTECT, null=True)
    other_payer_credits_draft = models.ManyToManyField(DraftInvoice, related_name='+')

    payer_nameid = models.CharField(max_length=250)
    payer_external_id = models.CharField(max_length=250)
    payer_first_name = models.CharField(max_length=250)
    payer_last_name = models.CharField(max_length=250)
    payer_address = models.TextField()
    payer_email = models.CharField(max_length=250, blank=True)
    payer_phone = models.CharField(max_length=250, blank=True)
    status = models.CharField(
        max_length=10,
        choices=[
            ('open', _('open')),
            ('tobepaid', _('to be paid')),
            ('completed', _('completed')),
            ('cancelled', _('cancelled')),
            ('expired', _('expired')),
        ],
        default='open',
    )

    validated_at = models.DateTimeField(null=True)
    paid_at = models.DateTimeField(null=True)
    completed_at = models.DateTimeField(null=True)
    cancelled_at = models.DateTimeField(null=True)
    expired_at = models.DateTimeField(null=True)

    expiry_at = models.DateTimeField(default=now)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True, null=True)

    @cached_property
    def lines(self):
        return self.basketline_set.filter(closed=True).order_by('pk')

    def information_messages(self):
        return sorted(list({li.information_message for li in self.lines if li.information_message}))

    def is_online_payment_possible(self):
        if self.remaining_amount <= 0:
            # there's actually no payment to do, no check necessary
            return True
        return not bool(self.get_no_online_payment_reason())

    def get_no_online_payment_reason(self):
        try:
            return self.regie.paymentbackend.check_payment_is_possible(self.remaining_amount)
        except Regie.paymentbackend.RelatedObjectDoesNotExist:
            return _('No payment system has been configured.')

    def cancel_information_messages(self):
        return sorted(
            list({li.cancel_information_message for li in self.lines if li.cancel_information_message})
        )

    @classmethod
    def signal_paid_invoice(cls, invoice):
        basket = cls.objects.filter(invoice=invoice).first()
        if not basket:
            return
        payment = basket.make_payments_with_credits()
        basket.status = 'completed'
        basket.completed_at = now()
        basket.save()
        request = RequestFactory().get('/')
        basket.notify(
            payload=payment.get_notification_payload(request) if payment else None,
            notification_type='payment',
        )

    @classmethod
    def signal_reopen(cls, invoice):
        basket = cls.objects.filter(invoice=invoice, status='tobepaid').first()
        if not basket:
            return
        basket.status = 'open'
        basket.save()
        if basket.invoice_id:
            # cancel invoice
            basket.invoice.cancelled_at = now()
            basket.invoice.cancellation_reason = InvoiceCancellationReason.objects.get_or_create(
                slug='transaction-cancelled', defaults={'label': _('Transaction cancelled')}
            )[0]
            basket.invoice.save()
            # cancel assignments
            basket.revert_assignments()
            # and detach invoice to have fresh amounts on basket detail page
            basket.invoice = None
            basket.save()

    def notify(self, notification_type, payload=None):
        for line in self.lines:
            line.notify(notification_type=notification_type, payload=payload)

    def revert_assignments(self):
        CreditAssignment.objects.filter(invoice=self.invoice).delete()

    def cancel(self, user=None):
        self.notify(notification_type='cancel')
        self.status = 'cancelled'
        self.cancelled_at = now()
        self.save()
        if self.invoice_id:
            self.invoice.cancelled_at = now()
            self.invoice.cancelled_by = user
            self.invoice.cancellation_reason = InvoiceCancellationReason.objects.get_or_create(
                slug='basket-cancelled', defaults={'label': _('Basket cancelled')}
            )[0]
            self.invoice.save()
            self.revert_assignments()

    def expire(self):
        self.notify(notification_type='expiration')
        self.status = 'expired'
        self.expired_at = now()
        self.save()
        if self.invoice_id:
            self.invoice.cancelled_at = now()
            self.invoice.cancellation_reason = InvoiceCancellationReason.objects.get_or_create(
                slug='basket-expired', defaults={'label': _('Basket expired')}
            )[0]
            self.invoice.save()
            self.revert_assignments()

    @classmethod
    def expire_baskets(cls):
        with transaction.atomic():
            open_baskets = Basket.objects.select_for_update().filter(status='open', expiry_at__lt=now())
            for basket in open_baskets:
                basket.expire()

        with transaction.atomic():
            tobepaid_baskets = Basket.objects.select_for_update().filter(
                status='tobepaid', expiry_at__lt=now() - datetime.timedelta(minutes=60)
            )
            for basket in tobepaid_baskets:
                basket.expire()

    @property
    def is_expired(self):
        return self.status == 'expired' or self.expiry_at <= now()

    @property
    def total_amount(self):
        if self.invoice is not None:
            return self.invoice.total_amount
        return self.draft_invoice.total_amount

    @property
    def credit_amount(self):
        if self.invoice is not None:
            assignment_qs = CreditAssignment.objects.filter(invoice=self.invoice)
            return -sum(a.amount for a in assignment_qs)

        if self.total_amount < 0:
            return 0
        credit_qs = Credit.objects.filter(
            date_publication__lte=now().date(),
            usable=True,
            cancelled_at__isnull=True,
            remaining_amount__gt=0,
            regie=self.regie,
            payer_external_id=self.payer_external_id,
        ).exclude(pool__campaign__finalized=False)
        available_credit = sum(c.remaining_amount for c in credit_qs)
        return -min(self.total_amount, available_credit)

    @property
    def remaining_amount(self):
        return self.total_amount + self.credit_amount

    def assign_credits(self):
        self.invoice.make_assignments(with_payment=False)
        assignment_qs = CreditAssignment.objects.filter(invoice=self.invoice)
        assigned_amount = sum(a.amount for a in assignment_qs)
        if assigned_amount == self.invoice.remaining_amount:
            # invoice totally paid with credits, make payment
            return self.make_payments_with_credits()

    def make_payments_with_credits(self):
        assignment_qs = CreditAssignment.objects.filter(invoice=self.invoice)
        assigned_amount = sum(a.amount for a in assignment_qs)
        if assigned_amount > 0:
            payment_type, dummy = PaymentType.objects.get_or_create(
                regie=self.regie, slug='credit', defaults={'label': _('Credit')}
            )
            payment = Payment.make_payment(
                regie=self.regie,
                amount=assigned_amount,
                payment_type=payment_type,
                invoices=[self.invoice],
            )
            CreditAssignment.objects.filter(invoice=self.invoice).update(payment=payment)
            return payment


class BasketLine(models.Model):
    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)

    basket = models.ForeignKey(Basket, on_delete=models.PROTECT)

    user_external_id = models.CharField(max_length=250)
    user_first_name = models.CharField(max_length=250)
    user_last_name = models.CharField(max_length=250)

    information_message = models.TextField(blank=True)
    cancel_information_message = models.TextField(blank=True)
    group_items = models.BooleanField(default=False)

    closed = models.BooleanField(default=False)

    form_url = models.URLField(blank=True)
    validation_callback_url = models.URLField(blank=True)
    payment_callback_url = models.URLField(blank=True)
    credit_callback_url = models.URLField(blank=True)
    cancel_callback_url = models.URLField(blank=True)
    expiration_callback_url = models.URLField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True, null=True)

    class Meta:
        unique_together = ('basket', 'user_external_id')

    @property
    def user_name(self):
        user_name = '%s %s' % (self.user_first_name, self.user_last_name)
        return user_name.strip()

    @cached_property
    def formatted_items(self):
        if not self.group_items:
            return self.get_items_without_grouping()
        return self.get_items_with_grouping()

    def get_items_without_grouping(self):
        result = []
        for item in self.items.all().order_by('pk'):
            description = []
            if item.subject:
                description.append(item.subject)
            if item.details:
                description.append(item.details)
            result.append(
                (
                    item.label,
                    ' '.join(description),
                    item.quantity,
                    item.unit_amount,
                    item.quantity * item.unit_amount,
                    item.event_date,
                    item.event_slug,
                    item.event_label,
                    item.agenda_slug,
                    item.activity_label,
                    item.accounting_code,
                    [item.event_date.isoformat()],
                )
            )
        return sorted(result, key=lambda a: (a[0], a[1]))

    def get_items_with_grouping(self):
        keys = []  # to keep ordering from line items
        items = {}  # group items
        for item in self.items.all().order_by('pk'):
            key = (
                item.unit_amount,
                item.label,
                item.subject,
                item.event_slug,
                item.event_label,
                item.agenda_slug,
                item.activity_label,
                item.accounting_code,
            )
            if key not in keys:
                keys.append(key)
                items[key] = {'details': [], 'quantity': decimal.Decimal('0')}
            if item.details:
                items[key]['details'].append(item.details)
            items[key]['quantity'] += item.quantity
            if 'dates' not in items[key]:
                items[key]['dates'] = []
            items[key]['dates'].append(item.event_date)
        result = []
        for key in keys:
            item = items[key]
            description = []
            if key[2]:
                description.append(key[2])
            if item['details']:
                description.append(', '.join(item['details']))
            result.append(
                (
                    key[1],
                    ' '.join(description),
                    item['quantity'],
                    key[0],
                    item['quantity'] * key[0],
                    item['dates'][0],
                    key[3],
                    key[4],
                    key[5],
                    key[6],
                    key[7],
                    [d.isoformat() for d in item['dates']],
                )
            )
        return sorted(result, key=lambda a: (a[0], a[1]))

    def notify(self, notification_type, payload):
        return Callback.notify(self, notification_type, payload)

    def do_notify(self, notification_type, payload=None, timeout=None):
        url = getattr(self, '%s_callback_url' % notification_type, None)
        if not url:
            return
        try:
            response = requests_wrapper.post(
                url,
                data=json.dumps(payload, cls=DjangoJSONEncoder),
                headers={'Content-Type': 'application/json'},
                remote_service='auto',
                timeout=timeout or 15,
                log_errors=False,
            )
            response.raise_for_status()
        except requests.RequestException as e:
            if e.response is not None:
                raise CallbackFailure(
                    'error (HTTP %s) notifying %s' % (e.response.status_code, notification_type)
                )
            raise CallbackFailure('error (%s) notifying %s' % (str(e) or type(e), notification_type))


class BasketLineItem(models.Model):
    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)

    line = models.ForeignKey(BasketLine, on_delete=models.PROTECT, related_name='items')

    label = models.CharField(max_length=200)
    subject = models.CharField(max_length=2000, blank=True)
    details = models.TextField(blank=True)

    event_date = models.DateField()
    event_slug = models.CharField(max_length=250)
    event_label = models.CharField(max_length=260)
    agenda_slug = models.CharField(max_length=250)
    activity_label = models.CharField(max_length=250)

    quantity = models.DecimalField(max_digits=9, decimal_places=2)
    unit_amount = models.DecimalField(max_digits=9, decimal_places=2)
    accounting_code = models.CharField(max_length=250, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True, null=True)
