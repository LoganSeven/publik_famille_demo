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
import dataclasses
import decimal
import uuid

from django.conf import settings
from django.core import validators
from django.db import models, transaction
from django.template.loader import get_template
from django.test.client import RequestFactory
from django.urls import reverse
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _

from lingo.invoicing.models.base import get_cancellation_info, set_numbers
from lingo.utils.misc import generate_slug


class PaymentCancellationReason(models.Model):
    label = models.CharField(_('Label'), max_length=150)
    slug = models.SlugField(_('Identifier'), max_length=160, unique=True)
    disabled = models.BooleanField(_('Disabled'), default=False)

    created_at = models.DateTimeField(auto_now_add=True, null=True)
    updated_at = models.DateTimeField(auto_now=True, null=True)

    class Meta:
        ordering = ['label']

    def __str__(self):
        return self.label

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = generate_slug(self)
        super().save(*args, **kwargs)

    @property
    def base_slug(self):
        return slugify(self.label)


PAYMENT_INFO = [
    ('check_issuer', _('Check issuer')),
    ('check_bank', _('Check bank/organism')),
    ('check_number', _('Check number')),
    ('bank_transfer_number', _('Bank transfer number')),
    ('payment_reference', _('Reference')),
]


class Payment(models.Model):
    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    regie = models.ForeignKey('invoicing.Regie', on_delete=models.PROTECT)
    number = models.PositiveIntegerField(default=0)
    formatted_number = models.CharField(max_length=200)
    amount = models.DecimalField(
        max_digits=9, decimal_places=2, validators=[validators.MinValueValidator(decimal.Decimal('0.01'))]
    )
    payment_type = models.ForeignKey('invoicing.PaymentType', on_delete=models.PROTECT)
    payment_info = models.JSONField(blank=True, default=dict)
    payer_external_id = models.CharField(max_length=250)
    payer_first_name = models.CharField(max_length=250)
    payer_last_name = models.CharField(max_length=250)
    payer_address = models.TextField()
    payer_email = models.CharField(max_length=250, blank=True)
    payer_phone = models.CharField(max_length=250, blank=True)
    date_payment = models.DateField(_('Payment date'), null=True)

    cancelled_at = models.DateTimeField(null=True)
    cancelled_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    cancellation_reason = models.ForeignKey(
        PaymentCancellationReason, verbose_name=_('Cancellation reason'), on_delete=models.PROTECT, null=True
    )
    cancellation_description = models.TextField(_('Description'), blank=True)

    docket = models.ForeignKey('invoicing.PaymentDocket', on_delete=models.PROTECT, null=True)

    transaction_id = models.CharField(max_length=200, null=True)
    transaction_date = models.DateTimeField(null=True)
    order_id = models.CharField(max_length=200, null=True)
    bank_transaction_id = models.CharField(max_length=200, null=True)
    bank_transaction_date = models.DateTimeField(blank=True, null=True)
    bank_data = models.JSONField(blank=True, default=dict)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True, null=True)

    class Meta:
        indexes = [models.Index('order_id', name='payment_order_id_idx')]

    def set_number(self):
        set_numbers(self, self.date_payment or self.created_at, 'payment')

    def normalize(self, for_backoffice=False):
        data = {
            'id': str(self.uuid),
            'display_id': self.formatted_number,
            'payment_type': self.payment_type.label,
            'amount': self.amount,
            'created': self.date_payment or self.created_at.date(),
            'has_pdf': True,
        }
        if for_backoffice and self.date_payment:
            data.update(
                {
                    'real_created': self.created_at.date(),
                }
            )
        return data

    @property
    def payer_name(self):
        payer_name = '%s %s' % (self.payer_first_name, self.payer_last_name)
        return payer_name.strip()

    @property
    def payer_external_raw_id(self):
        if ':' in self.payer_external_id:
            return self.payer_external_id.split(':')[1]
        return self.payer_external_id

    def get_notification_payload(self, request):
        return {
            'payment_id': str(self.uuid),
            'urls': {
                'payment_in_backoffice': request.build_absolute_uri(
                    reverse(
                        'lingo-manager-invoicing-payment-redirect',
                        kwargs={'payment_uuid': self.uuid},
                    )
                ),
                'payment_pdf': request.build_absolute_uri(
                    reverse(
                        'lingo-manager-invoicing-payment-pdf-redirect',
                        kwargs={'payment_uuid': self.uuid},
                    )
                ),
            },
            'api_urls': {
                'payment_pdf': request.build_absolute_uri(
                    reverse(
                        'api-invoicing-payment-pdf',
                        kwargs={'regie_identifier': self.regie.slug, 'payment_identifier': self.uuid},
                    )
                ),
            },
        }

    @classmethod
    def make_payment(
        cls,
        regie,
        amount,
        payment_type,
        *,
        invoices=None,
        lines=None,
        transaction_id=None,
        transaction_date=None,
        order_id=None,
        bank_transaction_id=None,
        bank_transaction_date=None,
        bank_data=None,
        payment_info=None,
        date_payment=None,
    ):
        from lingo.invoicing.models import Invoice

        invoices = invoices or []
        lines = lines or []
        line_ids = None
        paid_invoices = []
        with transaction.atomic():
            if lines:
                line_ids = [li.pk for li in lines]
                invoices = Invoice.objects.select_for_update().filter(pk__in=[li.invoice_id for li in lines])
            else:
                invoices = Invoice.objects.select_for_update().filter(pk__in=[i.pk for i in invoices])
            payment = cls.objects.create(
                regie=regie,
                amount=amount,
                payment_type=payment_type,
                transaction_id=transaction_id,
                transaction_date=transaction_date,
                order_id=order_id,
                bank_transaction_id=bank_transaction_id,
                bank_transaction_date=bank_transaction_date,
                bank_data=bank_data or {},
                payer_external_id=invoices[0].payer_external_id,
                payer_first_name=invoices[0].payer_first_name,
                payer_last_name=invoices[0].payer_last_name,
                payer_address=invoices[0].payer_address,
                payer_email=invoices[0].payer_email,
                payer_phone=invoices[0].payer_phone,
                payment_info=payment_info or {},
                date_payment=date_payment,
            )
            payment.set_number()
            payment.save()
            amount_to_assign = amount
            for invoice in invoices.order_by('date_publication', 'created_at'):
                if not invoice.remaining_amount:
                    # nothing to pay for this invoice
                    continue
                for line in invoice.lines.order_by('remaining_amount', 'pk'):
                    if lines and line.pk not in line_ids:
                        # if lines specified, ignore other lines
                        continue
                    if not line.remaining_amount:
                        # nothing to pay for this line
                        continue
                    # paid_amount for this line: it can not be greater than line remaining_amount
                    paid_amount = decimal.Decimal(min(line.remaining_amount, amount_to_assign))
                    InvoiceLinePayment.objects.create(
                        payment=payment,
                        line=line,
                        amount=paid_amount,
                    )
                    # new amount to assign
                    amount_to_assign -= paid_amount
                    if amount_to_assign <= 0:
                        break
                invoice.refresh_from_db()
                if not invoice.remaining_amount:
                    paid_invoices.append(invoice)
                if amount_to_assign <= 0:
                    break

        request = RequestFactory().get('/')
        for invoice in paid_invoices:
            invoice.notify(payload=payment.get_notification_payload(request), notification_type='payment')

        return payment

    def get_payment_info(self):
        result = []
        for key, label in PAYMENT_INFO:
            if self.payment_info.get(key):
                result.append((label, self.payment_info[key]))
        if 'refdet' in self.bank_data:
            result.append((_('Debt reference'), self.bank_data['refdet']))
        return result

    def get_cancellation_info(self):
        return get_cancellation_info(self)

    def has_collected_invoices(self):
        if hasattr(self, 'prefetched_invoicelinepayments'):
            invoice_line_payments = self.prefetched_invoicelinepayments
        else:
            invoice_line_payments = self.invoicelinepayment_set.select_related('line__invoice').order_by(
                'created_at'
            )
        for invoice_line_payment in invoice_line_payments:
            if invoice_line_payment.line.invoice.collection_id:
                return True
        return False

    def get_invoice_payments(self):
        from lingo.invoicing.models import CreditAssignment

        if hasattr(self, 'prefetched_invoicelinepayments'):
            invoice_line_payments = self.prefetched_invoicelinepayments
        else:
            invoice_line_payments = self.invoicelinepayment_set.select_related('line__invoice').order_by(
                'created_at'
            )
        invoice_payments = collections.defaultdict(InvoicePayment)
        invoices = []
        for invoice_line_payment in invoice_line_payments:
            invoice = invoice_line_payment.line.invoice
            invoice_payments[invoice].invoice = invoice
            invoice_payments[invoice].payment = self
            invoice_payments[invoice].amount += invoice_line_payment.amount
            invoice_payments[invoice].lines.append(invoice_line_payment)
            invoices.append(invoice)

        credits_by_invoice_id = collections.defaultdict(list)
        for ca in (
            CreditAssignment.objects.filter(payment=self, invoice__in=invoices)
            .select_related('credit')
            .order_by('pk')
        ):
            credits_by_invoice_id[ca.invoice_id].append(ca.credit)
        for invoice in invoices:
            invoice.credit_formatted_numbers = [
                c.formatted_number for c in credits_by_invoice_id.get(invoice.pk, [])
            ]

        return sorted(invoice_payments.values(), key=lambda a: a.invoice.created_at)

    def html(self):
        template = get_template('lingo/invoicing/payment.html')
        context = {
            'author': settings.TEMPLATE_VARS.get('global_title'),
            'lang': settings.LANGUAGE_CODE,
            'regie': self.regie,
            'object': self,
            'payment': self,
            'invoice_payments': self.get_invoice_payments(),
            'appearance_settings': self.regie.get_appearance_settings(),
        }
        return template.render(context)


class InvoiceLinePayment(models.Model):
    payment = models.ForeignKey(Payment, on_delete=models.PROTECT)
    line = models.ForeignKey('invoicing.InvoiceLine', on_delete=models.PROTECT)
    amount = models.DecimalField(
        max_digits=9, decimal_places=2, validators=[validators.MinValueValidator(decimal.Decimal('0.01'))]
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True, null=True)


@dataclasses.dataclass
class InvoicePayment:
    from lingo.invoicing.models import Invoice

    payment: Payment = None
    invoice: Invoice = None
    amount: decimal.Decimal = 0
    lines: list = dataclasses.field(default_factory=list)


class PaymentDocket(models.Model):
    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    regie = models.ForeignKey('invoicing.Regie', on_delete=models.PROTECT)
    number = models.PositiveIntegerField(default=0)
    formatted_number = models.CharField(max_length=200)

    date_end = models.DateField(_('Stop date'))
    draft = models.BooleanField()
    payment_types = models.ManyToManyField('invoicing.PaymentType')
    payment_types_info = models.JSONField(blank=True, default=dict)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        if self.draft:
            return '%s-%s' % (_('TEMPORARY'), self.pk)
        return self.formatted_number

    def set_number(self):
        set_numbers(self, self.created_at, 'docket')

    def get_active_payments(self):
        result = []
        for payment_type in self.payment_types.all():
            qs = self.payment_set.filter(payment_type=payment_type, cancelled_at__isnull=True).select_related(
                'payment_type'
            )
            result.append(
                {
                    'payment_type': payment_type,
                    'list': qs.order_by('-created_at'),
                    'amount': qs.aggregate(amount=models.Sum('amount'))['amount'],
                }
            )
        return result

    def get_cancelled_payments(self):
        qs = self.payment_set.filter(cancelled_at__isnull=False).select_related(
            'payment_type', 'cancellation_reason'
        )
        return {
            'list': qs.order_by('-created_at'),
            'amount': qs.aggregate(amount=models.Sum('amount'))['amount'],
        }

    def get_payments_amount(self):
        qs = self.payment_set.aggregate(amount=models.Sum('amount'))
        return qs['amount']

    def get_active_payments_amount(self):
        qs = self.payment_set.filter(cancelled_at__isnull=True).aggregate(amount=models.Sum('amount'))
        return qs['amount']

    def html(self):
        template = get_template('lingo/invoicing/docket.html')
        context = {
            'author': settings.TEMPLATE_VARS.get('global_title'),
            'lang': settings.LANGUAGE_CODE,
            'regie': self.regie,
            'object': self,
            'active': self.get_active_payments(),
            'cancelled': self.get_cancelled_payments(),
            'payment_info': PAYMENT_INFO,
        }
        return template.render(context)
