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
import copy
import json
import uuid
from itertools import islice

import requests
from django.conf import settings
from django.contrib.postgres.indexes import GinIndex
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models, transaction
from django.template.defaultfilters import floatformat
from django.template.loader import get_template
from django.urls import reverse
from django.utils.text import slugify
from django.utils.timezone import now
from django.utils.translation import gettext_lazy as _

from lingo.callback.models import Callback, CallbackFailure
from lingo.invoicing.models.base import (
    AbstractInvoiceLineObject,
    AbstractInvoiceObject,
    get_cancellation_info,
    set_numbers,
)
from lingo.utils.misc import generate_slug
from lingo.utils.requests_wrapper import requests as requests_wrapper


class InvoiceCancellationReason(models.Model):
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


class AbstractInvoice(AbstractInvoiceObject):
    date_payment_deadline_displayed = models.DateField(
        _('Displayed payment deadline'),
        help_text=_(
            'Payment deadline displayed to user on the portal. Leave empty to display the effective payment deadline.'
        ),
        null=True,
    )
    date_payment_deadline = models.DateField(
        _('Effective payment deadline'), help_text=_('Date on which the invoice is no longer payable online.')
    )
    date_due = models.DateField(
        _('Due date'), help_text=_('Date on which the invoice is no longer payable at the counter.')
    )
    date_debit = models.DateField(_('Debit date'), null=True)
    payer_direct_debit = models.BooleanField(default=False)

    payment_callback_url = models.URLField(blank=True)
    cancel_callback_url = models.URLField(blank=True)

    class Meta:
        abstract = True

    def html(self, dynamic=False, template_name=None, context=None):
        template = get_template(template_name or 'lingo/invoicing/invoice.html')
        lines_by_user = self.get_lines_by_user()
        amount_by_user = {}
        for user, lines in lines_by_user:
            amount_by_user[user] = sum(li.total_amount for li in lines)
        context = context or {}
        context.update(
            {
                'author': settings.TEMPLATE_VARS.get('global_title'),
                'lang': settings.LANGUAGE_CODE,
                'regie': self.regie,
                'object': self,
                'invoice': self,
                'lines_by_user': lines_by_user,
                'amount_by_user': amount_by_user,
                'appearance_settings': self.regie.get_appearance_settings(),
            }
        )
        if 'document_model' not in context:
            context.update(
                {
                    'document_model': self.invoice_model,
                }
            )
        if dynamic:
            invoice_payments = self.get_invoice_payments()
            context['invoice_payments'] = invoice_payments
            context['invoice_paid_amount_with_credit'] = sum(
                ip.amount for ip in invoice_payments if ip.payment.payment_type.slug == 'credit'
            )
            context['invoice_paid_amount_without_credit'] = sum(
                ip.amount for ip in invoice_payments if ip.payment.payment_type.slug != 'credit'
            )
        if context['document_model'] == 'full':
            lines_by_user_for_details = []
            for user, lines in lines_by_user:
                lines_for_details = [li for li in lines if li.description and li.display_description()]
                if lines_for_details:
                    lines_by_user_for_details.append((user, lines_for_details))
            context['lines_by_user_for_details'] = lines_by_user_for_details
        return template.render(context)

    def payments_html(self):
        return self.html(
            template_name='lingo/invoicing/payments_certificate.html',
            context={
                'payments': self.get_invoice_payments(),
                'document_model': self.regie.certificate_model,
            },
        )


class DraftInvoice(AbstractInvoice):
    @property
    def formatted_number(self):
        return '%s-%s' % (_('TEMPORARY'), self.pk)

    def promote(self, pool=None, journal_line_mapping=None):
        if self.total_amount >= 0:
            return self.promote_into_invoice(pool=pool, journal_line_mapping=journal_line_mapping)
        return self.promote_into_credit(pool=pool, journal_line_mapping=journal_line_mapping)

    def promote_into_invoice(self, pool=None, journal_line_mapping=None):
        from lingo.invoicing.models import DraftJournalLine, JournalLine

        final_invoice = copy.deepcopy(self)
        final_invoice.__class__ = Invoice
        final_invoice.pk = None
        final_invoice.uuid = uuid.uuid4()
        final_invoice.pool = pool
        final_invoice.set_number()
        final_invoice.paid_amount = 0
        final_invoice.remaining_amount = 0
        final_invoice.cancelled_at = None
        final_invoice.cancelled_by = None
        final_invoice.cancellation_reason = None
        final_invoice.cancellation_description = ''
        final_invoice.collection = None
        final_invoice.save()

        batch_size = 1000

        # bulk create lines of the new invoice
        lines = self.lines.order_by('pk').iterator(chunk_size=batch_size)
        while True:
            batch = list(islice(lines, batch_size))
            if not batch:
                break
            final_lines = []
            for line in batch:
                final_line = line.promote(pool=pool, invoice=final_invoice, bulk=True)
                final_lines.append(final_line)
            # bulk create lines
            InvoiceLine.objects.bulk_create(final_lines, batch_size)

            if journal_line_mapping:
                # in pool promotion, journal lines have been created first
                for final_line in final_lines:
                    draft_journal_line_ids = DraftJournalLine.objects.filter(
                        invoice_line=final_line._original_line
                    ).values_list('pk', flat=True)
                    journal_line_ids = [
                        jl for djl, jl in journal_line_mapping.items() if djl in draft_journal_line_ids
                    ]
                    # update invoice_line of related journal lines
                    JournalLine.objects.filter(pk__in=journal_line_ids).update(invoice_line=final_line)

        return final_invoice

    def promote_into_credit(self, pool=None, journal_line_mapping=None):
        from lingo.invoicing.models import Credit, CreditLine, DraftJournalLine, JournalLine

        credit = copy.deepcopy(self)
        credit.__class__ = Credit
        credit.pk = None
        credit.uuid = uuid.uuid4()
        credit.pool = pool
        credit.set_number()
        credit.assigned_amount = 0
        credit.remaining_amount = 0
        credit.label = _('Credit from %s') % now().strftime('%d/%m/%Y')
        credit.cancelled_at = None
        credit.cancelled_by = None
        credit.cancellation_reason = None
        credit.cancellation_description = ''
        credit.save()

        batch_size = 1000

        # bulk create lines of the new credit
        lines = self.lines.order_by('pk').iterator(chunk_size=batch_size)
        while True:
            batch = list(islice(lines, batch_size))
            if not batch:
                break
            final_lines = []
            for line in batch:
                final_line = line.promote_into_credit(pool=pool, credit=credit, bulk=True)
                final_lines.append(final_line)
            # bulk create lines
            CreditLine.objects.bulk_create(final_lines, batch_size)

            if journal_line_mapping:
                # in pool promotion, journal lines have been created first
                for final_line in final_lines:
                    draft_journal_line_ids = DraftJournalLine.objects.filter(
                        invoice_line=final_line._original_line
                    ).values_list('pk', flat=True)
                    journal_line_ids = [
                        jl for djl, jl in journal_line_mapping.items() if djl in draft_journal_line_ids
                    ]
                    # update credit_line of related journal lines
                    JournalLine.objects.filter(pk__in=journal_line_ids).update(credit_line=final_line)

        return credit


class Invoice(AbstractInvoice):
    number = models.PositiveIntegerField(default=0)
    formatted_number = models.CharField(max_length=200)
    paid_amount = models.DecimalField(max_digits=9, decimal_places=2, default=0)
    remaining_amount = models.DecimalField(max_digits=9, decimal_places=2, default=0)
    cancelled_at = models.DateTimeField(null=True)
    cancelled_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    cancellation_reason = models.ForeignKey(
        InvoiceCancellationReason, verbose_name=_('Cancellation reason'), on_delete=models.PROTECT, null=True
    )
    cancellation_description = models.TextField(_('Description'), blank=True)

    collection = models.ForeignKey('invoicing.CollectionDocket', on_delete=models.PROTECT, null=True)

    def set_number(self):
        set_numbers(self, self.date_invoicing or self.created_at, 'invoice')

    def normalize(self, for_backoffice=False, label_plus=False, always_enabled=False):
        from lingo.invoicing.models import Regie

        paid = bool(self.remaining_amount == 0)
        with_payments = bool(self.paid_amount > 0)
        date_limit = self.date_due if for_backoffice else self.date_payment_deadline

        online_payment = True
        not_payable_reason = None
        if paid:
            online_payment = False
        if online_payment and date_limit < now().date():
            online_payment = False
            not_payable_reason = 'past-due-date'

        # disabled field is for a datasource usage
        # disable it only if already paid or limit date is past
        # so we can pay it at the counter, event if payment backend says that it is not payable online
        disabled = False
        if not always_enabled:
            # always_enabled is True for cancelled invoices endpoint
            if self.payer_direct_debit or not online_payment:
                # disable for direct debit or invoice is already paid or limit date is past
                disabled = True

        if online_payment:
            # if amount to pay or limit date is not past, check payment backend limits
            # reason will be displayed also in BO cells
            try:
                not_payable_reason = self.regie.paymentbackend.check_payment_is_possible_code(
                    self.remaining_amount
                )
            except Regie.paymentbackend.RelatedObjectDoesNotExist:
                not_payable_reason = 'no-payment-system-configured'
            if not_payable_reason:
                online_payment = False

        # for a backoffice usage: never payable online
        if for_backoffice:
            online_payment = False

        label = self.label
        if label_plus:
            label = '%s - %s' % (self.formatted_number, label)
            amount = _('%(amount)s€') % {'amount': floatformat(self.remaining_amount, 2)}
            label += ' ' + _('(amount to pay: %s)') % amount

        data = {
            'id': str(self.uuid),
            'display_id': self.formatted_number,
            'label': label,
            'paid': paid,
            'amount': self.remaining_amount,
            'remaining_amount': self.remaining_amount,
            'total_amount': self.total_amount,
            'created': self.date_invoicing or self.created_at.date(),
            'pay_limit_date': date_limit if not paid else '',
            'online_payment': online_payment,
            'has_pdf': True,
            'has_dynamic_pdf': with_payments,
            'has_payments_pdf': bool(paid),
            'due_date': self.date_due,
            'payment_deadline_date': self.date_payment_deadline,
            'disabled': disabled,
            'is_line': False,
            'invoice_label': self.label,
        }
        if for_backoffice and self.date_invoicing:
            data.update(
                {
                    'real_created': self.created_at.date(),
                }
            )
        if self.collection:
            data.update(
                {
                    'paid': False,
                    'amount': 0,
                    'remaining_amount': 0,
                    'collected_amount': self.collected_amount,
                    'collection_date': self.collection.created_at.date(),
                    'pay_limit_date': '',
                    'has_dynamic_pdf': False,
                    'has_payments_pdf': False,
                    'online_payment': False,
                }
            )
        elif not_payable_reason:
            data['no_online_payment_reason'] = not_payable_reason
        return data

    def get_invoice_payments(self):
        from lingo.invoicing.models import CreditAssignment, InvoiceLinePayment, InvoicePayment

        invoice_line_payments = (
            InvoiceLinePayment.objects.filter(line__invoice=self)
            .select_related('payment', 'payment__payment_type')
            .order_by('created_at')
        )
        invoice_payments = collections.defaultdict(InvoicePayment)
        payments = []
        for invoice_line_payment in invoice_line_payments:
            payment = invoice_line_payment.payment
            invoice_payments[payment].invoice = self
            invoice_payments[payment].payment = payment
            invoice_payments[payment].amount += invoice_line_payment.amount
            payments.append(payment)
        credits_by_payment_id = collections.defaultdict(list)
        for ca in (
            CreditAssignment.objects.filter(invoice=self, payment__in=payments)
            .select_related('credit')
            .order_by('pk')
        ):
            credits_by_payment_id[ca.payment_id].append(ca.credit)
        for payment in payments:
            payment.credit_formatted_numbers = [
                c.formatted_number for c in credits_by_payment_id.get(payment.pk, [])
            ]

        return sorted(invoice_payments.values(), key=lambda a: a.payment.created_at)

    def get_cancellation_info(self):
        return get_cancellation_info(self)

    @property
    def collected_amount(self):
        from lingo.invoicing.models import InvoiceLinePayment

        paid_amount = (
            InvoiceLinePayment.objects.filter(
                line__invoice=self,
                payment__payment_type__slug='collect',
            ).aggregate(paid_amount=models.Sum('amount'))['paid_amount']
            or 0
        )
        return self.remaining_amount + paid_amount

    @property
    def paid_amount_before_collect(self):
        from lingo.invoicing.models import InvoiceLinePayment

        return (
            InvoiceLinePayment.objects.filter(
                line__invoice=self,
            )
            .exclude(
                payment__payment_type__slug='collect',
            )
            .aggregate(paid_amount=models.Sum('amount'))['paid_amount']
            or 0
        )

    def notify(self, notification_type, payload):
        return Callback.notify(self, notification_type, payload)

    def do_notify(self, notification_type, payload, timeout=None):
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

    def get_notification_payload(self, request):
        return {
            'invoice_id': str(self.uuid),
            'invoice': {
                'id': str(self.uuid),
                'total_amount': self.total_amount,
                'remaining_amount': self.remaining_amount,
            },
            'urls': {
                'invoice_in_backoffice': request.build_absolute_uri(
                    reverse(
                        'lingo-manager-invoicing-invoice-redirect',
                        kwargs={'invoice_uuid': self.uuid},
                    )
                ),
                'invoice_pdf': request.build_absolute_uri(
                    reverse(
                        'lingo-manager-invoicing-invoice-pdf-redirect',
                        kwargs={'invoice_uuid': self.uuid},
                    )
                ),
            },
            'api_urls': {
                'invoice_pdf': request.build_absolute_uri(
                    reverse(
                        'api-invoicing-invoice-pdf',
                        kwargs={
                            'regie_identifier': self.regie.slug,
                            'invoice_identifier': self.uuid,
                        },
                    )
                ),
            },
        }

    def make_assignments(self, with_payment=True):
        from lingo.invoicing.models import Credit, PaymentType

        if self.date_due < now().date():
            return
        payment_type, dummy = PaymentType.objects.get_or_create(
            regie=self.regie, slug='credit', defaults={'label': _('Credit')}
        )
        credit_qs = Credit.objects.filter(
            usable=True,
            cancelled_at__isnull=True,
            remaining_amount__gt=0,
            payer_external_id=self.payer_external_id,
            regie=self.regie,
        ).exclude(pool__campaign__finalized=False)
        self.refresh_from_db()  # update amounts from db
        amount_to_pay = self.remaining_amount
        for credit in credit_qs.order_by('pk'):
            self.refresh_from_db()  # update amounts from db
            if not self.remaining_amount:
                return
            paid_amount = self.assign_credit(
                credit, payment_type, with_payment=with_payment, amount_to_pay=amount_to_pay
            )
            # new amount to assign
            if paid_amount is not None:
                amount_to_pay -= paid_amount
            if amount_to_pay <= 0:
                break

    def assign_credit(self, credit, payment_type, with_payment=True, amount_to_pay=None):
        from lingo.invoicing.models import Credit, CreditAssignment, Payment

        if not self.remaining_amount:
            return
        with transaction.atomic():
            credit = (
                Credit.objects.select_for_update()
                .filter(
                    usable=True,
                    cancelled_at__isnull=True,
                    remaining_amount__gt=0,
                    pk=credit.pk,
                )
                .first()
            )
            if not credit:
                return
            payment = None
            if amount_to_pay is None:
                amount_to_pay = self.remaining_amount
            paid_amount = min(credit.remaining_amount, amount_to_pay)
            if with_payment:
                # make payment
                payment = Payment.make_payment(
                    self.regie,
                    paid_amount,
                    payment_type,
                    invoices=[self],
                )
            # assign credit
            CreditAssignment.objects.create(
                invoice=self,
                credit=credit,
                payment=payment,
                amount=paid_amount,
            )
            return paid_amount


class AbstractInvoiceLine(AbstractInvoiceLineObject):

    class Meta:
        abstract = True


class DraftInvoiceLine(AbstractInvoiceLine):
    invoice = models.ForeignKey(DraftInvoice, on_delete=models.PROTECT, null=True, related_name='lines')

    def promote(self, invoice, pool=None, bulk=False):
        final_line = copy.deepcopy(self)
        final_line.__class__ = InvoiceLine
        final_line.pk = None
        final_line.uuid = uuid.uuid4()
        final_line.pool = pool
        final_line.invoice = invoice
        final_line.error_status = ''
        final_line.paid_amount = 0
        final_line.remaining_amount = 0
        if not bulk:
            final_line.save()
        final_line._original_line = self
        return final_line

    def promote_into_credit(self, credit, pool=None, bulk=False):
        from lingo.invoicing.models import CreditLine

        credit_line = copy.deepcopy(self)
        credit_line.__class__ = CreditLine
        credit_line.pk = None
        credit_line.uuid = uuid.uuid4()
        credit_line.pool = pool
        credit_line.credit = credit
        credit_line.quantity = -self.quantity  # inverse quantities, so credit total_amout is positive
        credit_line.assigned_amount = 0
        credit_line.remaining_amount = 0
        if not bulk:
            credit_line.save()
        credit_line._original_line = self
        return credit_line


class InvoiceLine(AbstractInvoiceLine):
    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, null=True, related_name='lines')
    paid_amount = models.DecimalField(max_digits=9, decimal_places=2, default=0)
    remaining_amount = models.DecimalField(max_digits=9, decimal_places=2, default=0)

    class Meta:
        indexes = [
            GinIndex(fields=['details']),
            models.Index(fields=['user_external_id']),
        ]
        constraints = [
            models.CheckConstraint(
                check=models.Q(
                    models.Q(
                        ('paid_amount__lte', models.F('total_amount')),
                        ('total_amount__gt', 0),
                    ),
                    models.Q(
                        ('paid_amount__gte', models.F('total_amount')),
                        ('total_amount__lt', 0),
                    ),
                    models.Q(('paid_amount', 0), ('total_amount', 0)),
                    _connector='OR',
                ),
                name='paid_amount_check',
            )
        ]


class CollectionDocket(models.Model):
    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    regie = models.ForeignKey('invoicing.Regie', on_delete=models.PROTECT)
    number = models.PositiveIntegerField(default=0)
    formatted_number = models.CharField(max_length=200)

    date_end = models.DateField(_('End date'))
    draft = models.BooleanField()
    minimum_threshold = models.DecimalField(max_digits=9, decimal_places=2, default=0)
    pay_invoices = models.BooleanField(
        _('Pay invoices'),
        help_text=_(
            'When the collection is validated, add a "Collect" type payment to the collected invoices.'
        ),
        default=False,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        if self.draft:
            return '%s-%s' % (_('TEMPORARY'), self.pk)
        return self.formatted_number

    def set_number(self):
        set_numbers(self, self.created_at, 'collection')

    def get_invoices(self):
        from lingo.invoicing.models import InvoiceLinePayment

        queryset = self.invoice_set.all()
        remaining_amounts = (
            queryset.filter(payer_external_id=models.OuterRef('payer_external_id'))
            .annotate(total_remaining=models.Func(models.F('remaining_amount'), function='Sum'))
            .values('total_remaining')
        )
        paid_amounts = (
            InvoiceLinePayment.objects.filter(
                line__invoice__in=queryset,
                line__invoice__payer_external_id=models.OuterRef('payer_external_id'),
                payment__payment_type__slug='collect',
            )
            .annotate(total_paid=models.Func(models.F('amount'), function='Sum'))
            .values('total_paid')
        )
        collect_paid_amounts = (
            InvoiceLinePayment.objects.filter(
                line__invoice=models.OuterRef('pk'),
                payment__payment_type__slug='collect',
            )
            .annotate(collect_paid_amount=models.Func(models.F('amount'), function='Sum'))
            .values('collect_paid_amount')
        )
        queryset = queryset.annotate(
            total_remaining=models.Subquery(remaining_amounts),
            total_paid=models.Subquery(paid_amounts),
            collect_paid_amount=models.Subquery(collect_paid_amounts),
        ).order_by('payer_last_name', 'payer_first_name', 'payer_external_id', '-created_at')
        return queryset

    def get_invoices_amount(self):
        from lingo.invoicing.models import InvoiceLinePayment

        remaining_amount = (
            self.invoice_set.aggregate(remaining_amount=models.Sum('remaining_amount'))['remaining_amount']
            or 0
        )
        paid_amount = (
            InvoiceLinePayment.objects.filter(
                line__invoice__in=self.invoice_set.all(),
                payment__payment_type__slug='collect',
            ).aggregate(paid_amount=models.Sum('amount'))['paid_amount']
            or 0
        )
        return remaining_amount + paid_amount
