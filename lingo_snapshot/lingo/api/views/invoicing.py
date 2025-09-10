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
import logging

import pytz
from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.db.models import Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404
from django.template.defaultfilters import floatformat
from django.urls import reverse
from django.utils import formats
from django.utils.timezone import now
from django.utils.translation import gettext_lazy as _
from django.utils.translation import gettext_noop as N_
from rest_framework.views import APIView
from weasyprint import HTML

from lingo.api import serializers
from lingo.api.utils import APIAdmin, APIErrorBadRequest, Response
from lingo.api.views.utils import FromBookingsMixin
from lingo.invoicing.errors import PayerError
from lingo.invoicing.models import (
    PAYMENT_INFO,
    Credit,
    CreditAssignment,
    CreditLine,
    DraftInvoice,
    DraftInvoiceLine,
    InjectedLine,
    Invoice,
    InvoiceCancellationReason,
    InvoiceLine,
    InvoiceLinePayment,
    Payment,
    PaymentType,
    Refund,
    Regie,
)
from lingo.utils.pdf import write_pdf


class InvoiceCancellationReasons(APIView):
    permission_classes = (APIAdmin,)

    def get(self, request):
        return Response(
            {
                'data': [
                    {'id': reason.slug, 'text': reason.label, 'slug': reason.slug}
                    for reason in InvoiceCancellationReason.objects.filter(disabled=False)
                ]
            }
        )


invoice_cancellation_reasons = InvoiceCancellationReasons.as_view()


class InvoicingRegies(APIView):
    permission_classes = (APIAdmin,)

    def get(self, request, format=None):
        return Response(
            {
                'data': [
                    {'id': regie.slug, 'text': regie.label, 'slug': regie.slug}
                    for regie in Regie.objects.order_by('label')
                ]
            }
        )


invoicing_regies = InvoicingRegies.as_view()


class InvoicingPaymentTypes(APIView):
    permission_classes = (APIAdmin,)

    def get(self, request, regie_identifier):
        regie = get_object_or_404(Regie, slug=regie_identifier)
        return Response(
            {
                'data': [
                    {'id': payment_type.slug, 'text': payment_type.label, 'slug': payment_type.slug}
                    for payment_type in PaymentType.objects.filter(regie=regie, disabled=False).exclude(
                        slug='credit'
                    )
                ]
            }
        )


invoicing_payment_types = InvoicingPaymentTypes.as_view()


class PayerMixin:
    def get_payer_external_id(self, request, regie, nameid=None, payer_external_id=None):
        if payer_external_id:
            return payer_external_id
        if not nameid:
            raise Http404
        try:
            return regie.get_payer_external_id_from_nameid(request, nameid)
        except PayerError:
            raise Http404


class InvoicingInvoices(PayerMixin, APIView):
    permission_classes = (APIAdmin,)
    serializer_class = serializers.InvoiceFiltersSerializer
    invoice_always_enabled = False

    def get_invoices_queryset(self, request, regie):
        serializer = self.serializer_class(data=request.query_params)
        if not serializer.is_valid():
            raise APIErrorBadRequest(N_('invalid filters'), errors=serializer.errors)
        data = serializer.validated_data
        for_backoffice = bool(request.GET.get('payer_external_id'))

        payer_external_id = self.get_payer_external_id(
            request=request,
            regie=regie,
            nameid=request.GET.get('NameID'),
            payer_external_id=request.GET.get('payer_external_id'),
        )
        queryset = Invoice.objects.filter(
            regie=regie,
            remaining_amount__gt=0,
            date_publication__lte=now().date(),
            payer_external_id=payer_external_id,
            basket__isnull=True,
            cancelled_at__isnull=True,
            collection__isnull=True,
        ).exclude(pool__campaign__finalized=False)

        if 'payable' in data and 'payable' in request.query_params:
            date_field = 'date_due' if for_backoffice else 'date_payment_deadline'
            if data['payable'] is False:
                qs_qargs = Q(**{'%s__lt' % date_field: now().date()}) | Q(payer_direct_debit=True)
            else:
                qs_qargs = Q(**{'%s__gte' % date_field: now().date()}) & Q(payer_direct_debit=False)
            queryset = queryset.filter(qs_qargs)

        return queryset.order_by('-created_at')

    def get(self, request, regie_identifier):
        regie = get_object_or_404(Regie, slug=regie_identifier)
        try:
            include_epayment_url = bool(regie.paymentbackend)
        except ObjectDoesNotExist:
            include_epayment_url = False
        invoices = self.get_invoices_queryset(request, regie)
        include_lines = request.GET.get('include_lines')
        label_plus = request.GET.get('verbose_label')
        data = []
        for invoice in invoices:
            invoice_data = invoice.normalize(
                for_backoffice=bool(request.GET.get('payer_external_id')),
                label_plus=(include_lines or label_plus),
                always_enabled=self.invoice_always_enabled,
            )
            if include_epayment_url and invoice_data['online_payment']:
                invoice_data['api'] = {
                    'payment_url': request.build_absolute_uri(
                        reverse('lingo-epayment-invoice', kwargs={'invoice_uuid': invoice.uuid})
                    )
                }
            data.append(invoice_data)
            if bool(include_lines) and not invoice_data['disabled']:
                lines = invoice.get_grouped_and_ordered_lines()
                for line in lines:
                    amount = _('%(amount)s€') % {'amount': floatformat(line.remaining_amount, 2)}
                    parts = ['----']
                    if line.activity_label:
                        parts.append('%s -' % line.activity_label)
                    parts.append(line.label)
                    if line.details.get('check_type_label'):
                        parts.append('- %s' % line.details['check_type_label'])
                    elif line.details.get('status') == 'absence':
                        parts.append('- %s' % _('Absence'))
                    if line.display_description() and line.description:
                        parts.append('- %s' % line.description)
                    parts.append(_('(amount to pay: %s)') % amount)
                    data.append(
                        {
                            'id': 'line:%s' % line.uuid,
                            'label': ' '.join(parts),
                            'disabled': not bool(line.remaining_amount),
                            'is_line': True,
                            'activity_label': line.activity_label,
                            'agenda_slug': line.agenda_slug,
                            'user_external_id': line.user_external_id,
                            'user_first_name': line.user_first_name,
                            'user_last_name': line.user_last_name,
                            'event_date': line.event_date,
                            'details': line.details,
                            'line_label': line.label,
                            'line_description': line.description if line.display_description() else '',
                            'line_raw_description': line.description,
                            'event_slug': line.event_slug,
                            'accounting_code': line.accounting_code,
                            'unit_amount': line.unit_amount,
                            'quantity': line.quantity,
                            'remaining_amount': line.remaining_amount,
                            'invoice_id': line.invoice.uuid,
                        }
                    )
        return Response({'data': data})


invoicing_invoices = InvoicingInvoices.as_view()
invoicing_invoices.publik_authentication_resolve_user_by_nameid = False


class InvoicingHistoryInvoices(InvoicingInvoices):
    def get_invoices_queryset(self, request, regie):
        payer_external_id = self.get_payer_external_id(
            request=request,
            regie=regie,
            nameid=request.GET.get('NameID'),
            payer_external_id=request.GET.get('payer_external_id'),
        )
        return (
            Invoice.objects.filter(
                regie=regie,
                remaining_amount=0,
                date_publication__lte=now().date(),
                payer_external_id=payer_external_id,
                cancelled_at__isnull=True,
                collection__isnull=True,
            )
            .exclude(pool__campaign__finalized=False)
            .order_by('-created_at')
        )


invoicing_history_invoices = InvoicingHistoryInvoices.as_view()
invoicing_history_invoices.publik_authentication_resolve_user_by_nameid = False


class InvoicingCollectedInvoices(InvoicingInvoices):
    def get_invoices_queryset(self, request, regie):
        payer_external_id = self.get_payer_external_id(
            request=request,
            regie=regie,
            nameid=request.GET.get('NameID'),
            payer_external_id=request.GET.get('payer_external_id'),
        )
        return Invoice.objects.filter(
            Q(pool__isnull=True) | Q(pool__campaign__finalized=True),
            regie=regie,
            date_publication__lte=now().date(),
            payer_external_id=payer_external_id,
            cancelled_at__isnull=True,
            collection__isnull=False,
            collection__draft=False,
        ).order_by('-created_at')


invoicing_collected_invoices = InvoicingCollectedInvoices.as_view()
invoicing_collected_invoices.publik_authentication_resolve_user_by_nameid = False


class InvoicingCancelledInvoices(InvoicingInvoices):
    invoice_always_enabled = True

    def get_invoices_queryset(self, request, regie):
        if not request.GET.get('payer_external_id'):
            raise Http404
        payer_external_id = request.GET['payer_external_id']
        return (
            Invoice.objects.filter(
                regie=regie,
                payer_external_id=payer_external_id,
                basket__isnull=True,
                cancelled_at__isnull=False,
                collection__isnull=True,
            )
            .exclude(pool__campaign__finalized=False)
            .order_by('-created_at')
        )


invoicing_cancelled_invoices = InvoicingCancelledInvoices.as_view()


class InvoicingInvoice(PayerMixin, APIView):
    permission_classes = (APIAdmin,)

    def get(self, request, regie_identifier, invoice_identifier):
        regie = get_object_or_404(Regie, slug=regie_identifier)
        payer_external_id = self.get_payer_external_id(
            request=request,
            regie=regie,
            nameid=request.GET.get('NameID'),
            payer_external_id=request.GET.get('payer_external_id'),
        )
        invoice = get_object_or_404(
            Invoice.objects.exclude(pool__campaign__finalized=False),
            uuid=invoice_identifier,
            regie=regie,
            date_publication__lte=now().date(),
            payer_external_id=payer_external_id,
            cancelled_at__isnull=True,
        )
        invoice_data = invoice.normalize(for_backoffice=bool(request.GET.get('payer_external_id')))
        try:
            if bool(regie.paymentbackend) and invoice_data['online_payment']:
                invoice_data['api'] = {
                    'payment_url': request.build_absolute_uri(
                        reverse('lingo-epayment-invoice', kwargs={'invoice_uuid': invoice.uuid})
                    )
                }
        except ObjectDoesNotExist:
            pass

        return Response({'data': invoice_data})


invoicing_invoice = InvoicingInvoice.as_view()
invoicing_invoice.publik_authentication_resolve_user_by_nameid = False


class InvoicingInvoicePDF(PayerMixin, APIView):
    permission_classes = (APIAdmin,)
    dynamic = False

    def get(self, request, regie_identifier, invoice_identifier):
        regie = get_object_or_404(Regie, slug=regie_identifier)
        payer_external_id = self.get_payer_external_id(
            request=request,
            regie=regie,
            nameid=request.GET.get('NameID'),
            payer_external_id=request.GET.get('payer_external_id'),
        )
        invoice = get_object_or_404(
            Invoice.objects.exclude(pool__campaign__finalized=False),
            uuid=invoice_identifier,
            regie=regie,
            date_publication__lte=now().date(),
            payer_external_id=payer_external_id,
            cancelled_at__isnull=True,
        )
        result = invoice.html(dynamic=self.dynamic)
        html = HTML(string=result)
        pdf = write_pdf(html)
        response = HttpResponse(pdf, content_type='application/pdf')
        response['Content-Disposition'] = 'attachment; filename="%s.pdf"' % invoice.formatted_number
        return response


invoicing_invoice_pdf = InvoicingInvoicePDF.as_view()
invoicing_invoice_pdf.publik_authentication_resolve_user_by_nameid = False


class InvoicingInvoiceDynamicPDF(InvoicingInvoicePDF):
    dynamic = True


invoicing_invoice_dynamic_pdf = InvoicingInvoiceDynamicPDF.as_view()
invoicing_invoice_dynamic_pdf.publik_authentication_resolve_user_by_nameid = False


class InvoicingInvoicePaymentsPDF(PayerMixin, APIView):
    permission_classes = (APIAdmin,)

    def get(self, request, regie_identifier, invoice_identifier):
        regie = get_object_or_404(Regie, slug=regie_identifier)
        payer_external_id = self.get_payer_external_id(
            request=request,
            regie=regie,
            nameid=request.GET.get('NameID'),
            payer_external_id=request.GET.get('payer_external_id'),
        )
        invoice = get_object_or_404(
            Invoice.objects.exclude(pool__campaign__finalized=False),
            uuid=invoice_identifier,
            regie=regie,
            date_publication__lte=now().date(),
            payer_external_id=payer_external_id,
            remaining_amount=0,
            cancelled_at__isnull=True,
            collection__isnull=True,
        )
        result = invoice.payments_html()
        html = HTML(string=result)
        pdf = write_pdf(html)
        response = HttpResponse(pdf, content_type='application/pdf')
        response['Content-Disposition'] = 'attachment; filename="A-%s.pdf"' % invoice.formatted_number
        return response


invoicing_invoice_payments_pdf = InvoicingInvoicePaymentsPDF.as_view()
invoicing_invoice_payments_pdf.publik_authentication_resolve_user_by_nameid = False


class InvoicingInvoicePay(APIView):
    # XXX ?
    authentication_classes = []
    permission_classes = ()

    def post(self, request, regie_identifier, invoice_identifier):
        logging.error(
            'Deprecated enpoint /api/regie/<regie_identifier>/invoice/<invoice_identifier>/pay/ called'
        )
        data = request.data
        transaction_id = data.get('transaction_id')
        transaction_date = data.get('transaction_date')
        if transaction_date:
            try:
                transaction_date = pytz.utc.localize(
                    datetime.datetime.strptime(transaction_date, '%Y-%m-%dT%H:%M:%S')
                )
            except ValueError:
                transaction_date = None
        order_id = data.get('order_id')
        bank_transaction_id = data.get('bank_transaction_id')
        bank_transaction_date = data.get('bank_transaction_date')
        if bank_transaction_date:
            try:
                bank_transaction_date = pytz.utc.localize(
                    datetime.datetime.strptime(bank_transaction_date, '%Y-%m-%dT%H:%M:%S')
                )
            except ValueError:
                bank_transaction_date = None
        bank_data = data.get('bank_data')
        invoice = get_object_or_404(
            Invoice.objects.exclude(pool__campaign__finalized=False),
            uuid=invoice_identifier,
            regie__slug=regie_identifier,
            remaining_amount__gt=0,
            cancelled_at__isnull=True,
            collection__isnull=True,
        )
        try:
            amount = decimal.Decimal(str(data.get('amount')))
        except (decimal.InvalidOperation, ValueError, TypeError):
            amount = invoice.remaining_amount
        payment_type = get_object_or_404(PaymentType, regie=invoice.regie, slug='online')
        payment = Payment.make_payment(
            regie=invoice.regie,
            invoices=[invoice],
            amount=amount,
            payment_type=payment_type,
            transaction_id=transaction_id,
            transaction_date=transaction_date,
            order_id=order_id,
            bank_transaction_id=bank_transaction_id,
            bank_transaction_date=bank_transaction_date,
            bank_data=bank_data,
        )
        return Response(
            {
                'data': {
                    'id': payment.uuid,
                }
            }
        )


invoicing_invoice_pay = InvoicingInvoicePay.as_view()


class InvoicingInvoiceCancel(APIView):
    permission_classes = (APIAdmin,)
    serializer_class = serializers.CancelInvoiceSerializer

    def post(self, request, regie_identifier, invoice_identifier):
        regie = get_object_or_404(Regie, slug=regie_identifier)
        invoice = get_object_or_404(
            Invoice.objects.exclude(
                pk__in=InvoiceLine.objects.filter(invoicelinepayment__isnull=False).values('invoice')
            ).exclude(pool__campaign__finalized=False),
            uuid=invoice_identifier,
            regie=regie,
            cancelled_at__isnull=True,
            collection__isnull=True,
        )

        serializer = self.serializer_class(data=request.data)
        if not serializer.is_valid():
            raise APIErrorBadRequest(N_('invalid payload'), errors=serializer.errors)

        invoice.cancelled_at = now()
        invoice.cancellation_reason = serializer.validated_data['cancellation_reason']
        invoice.cancellation_description = serializer.validated_data.get('cancellation_description') or ''
        invoice.cancelled_by = serializer.validated_data.get('user_uuid') or None
        invoice.save()
        if serializer.validated_data['notify'] is True:
            invoice.notify(payload={'invoice_id': str(invoice.uuid)}, notification_type='cancel')
        return Response({'err': 0})


invoicing_invoice_cancel = InvoicingInvoiceCancel.as_view()


class InvoicingDraftInvoices(APIView):
    permission_classes = (APIAdmin,)
    serializer_class = serializers.DraftInvoiceSerializer

    def post(self, request, regie_identifier):
        regie = get_object_or_404(Regie, slug=regie_identifier)
        serializer = self.serializer_class(data=request.data, regie=regie)
        if not serializer.is_valid():
            raise APIErrorBadRequest(N_('invalid payload'), errors=serializer.errors)

        date_due = serializer.validated_data.pop('date_due', serializer.validated_data['date_publication'])
        date_payment_deadline = serializer.validated_data.pop(
            'date_payment_deadline', serializer.validated_data['date_publication']
        )
        invoice = DraftInvoice.objects.create(
            regie=regie,
            date_due=date_due,
            date_payment_deadline=date_payment_deadline,
            origin='api',
            **serializer.validated_data,
        )

        return Response({'data': {'draft_invoice_id': str(invoice.uuid)}})


invoicing_draft_invoices = InvoicingDraftInvoices.as_view()


class InvoicingDraftCredits(InvoicingDraftInvoices):
    serializer_class = serializers.DraftCreditSerializer


invoicing_draft_credits = InvoicingDraftCredits.as_view()


class InvoicingDraftInvoiceLines(APIView):
    permission_classes = (APIAdmin,)
    serializer_class = serializers.DraftInvoiceLineSerializer

    def post(self, request, regie_identifier, draft_identifier):
        regie = get_object_or_404(Regie, slug=regie_identifier)
        invoice = get_object_or_404(DraftInvoice, regie=regie, uuid=draft_identifier, pool__isnull=True)
        serializer = self.serializer_class(data=request.data)
        if not serializer.is_valid():
            raise APIErrorBadRequest(N_('invalid payload'), errors=serializer.errors)

        event_slug = serializer.validated_data.pop('slug')
        event_label = ''
        agenda_slug = ''
        if '@' in event_slug:
            agenda_slug = event_slug.split('@')[0]
            event_label = serializer.validated_data['label']
        merge_lines = serializer.validated_data.pop('merge_lines')
        subject = serializer.validated_data.pop('subject')
        description = serializer.validated_data['description']

        values = {
            'invoice': invoice,
            'event_slug': event_slug,
            'event_label': event_label,
            'agenda_slug': agenda_slug,
            'details': {'dates': [serializer.validated_data['event_date'].isoformat()]},
        }
        values.update(**serializer.validated_data)
        if subject:
            values['description'] = f'{subject} {description}'

        if merge_lines is True and event_slug and agenda_slug and subject:
            line, created = DraftInvoiceLine.objects.filter(description__startswith=subject).get_or_create(
                invoice=invoice,
                label=serializer.validated_data['label'],
                event_slug=event_slug,
                event_label=event_label,
                agenda_slug=agenda_slug,
                activity_label=serializer.validated_data.get('activity_label') or '',
                unit_amount=serializer.validated_data['unit_amount'],
                accounting_code=serializer.validated_data.get('accounting_code') or '',
                user_external_id=serializer.validated_data['user_external_id'],
                form_url=serializer.validated_data.get('form_url') or '',
                defaults=values,
            )
            if not created:
                # existing line, update quantity and complete description
                line.quantity += serializer.validated_data['quantity']
                if description:
                    parts = []
                    if line.description:
                        parts.append(line.description)
                    parts.append(description)
                    line.description = ', '.join(parts)
                line.details['dates'].append(serializer.validated_data['event_date'].isoformat())
                line.save()
        else:
            line = DraftInvoiceLine.objects.create(**values)

        return Response({'data': {'draft_line_id': line.pk}})


invoicing_draft_invoice_lines = InvoicingDraftInvoiceLines.as_view()


class InvoicingDraftCreditLines(InvoicingDraftInvoiceLines):
    pass


invoicing_draft_credit_lines = InvoicingDraftCreditLines.as_view()


class InvoicingDraftInvoiceClose(APIView):
    permission_classes = (APIAdmin,)
    serializer_class = None

    def post(self, request, regie_identifier, draft_identifier):
        regie = get_object_or_404(Regie, slug=regie_identifier)
        invoice = get_object_or_404(DraftInvoice, regie=regie, uuid=draft_identifier, pool__isnull=True)
        serializer = None
        if self.serializer_class:
            serializer = self.serializer_class(data=request.data)
            if not serializer.is_valid():
                raise APIErrorBadRequest(N_('invalid payload'), errors=serializer.errors)
        self.check_object(invoice)
        final_object = invoice.promote()
        self.make_assignments(final_object, serializer)
        final_object.refresh_from_db()  # refresh amounts
        return self.render_response(request, final_object)

    def check_object(self, obj):
        if obj.total_amount < 0:
            raise APIErrorBadRequest(N_('can not create invoice from draft invoice with negative amount'))

    def make_assignments(self, final_object, serializer):
        final_object.make_assignments()

    def render_response(self, request, final_object):
        return Response({'data': final_object.get_notification_payload(request)})


invoicing_draft_invoice_close = InvoicingDraftInvoiceClose.as_view()


class InvoicingDraftCreditClose(InvoicingDraftInvoiceClose):
    serializer_class = serializers.DraftCreditCloseSerializer

    def check_object(self, obj):
        if obj.total_amount >= 0:
            raise APIErrorBadRequest(N_('can not create credit from draft invoice with positive amount'))

    def make_assignments(self, final_object, serializer):
        if serializer.validated_data['make_assignments'] is True:
            final_object.make_assignments()


invoicing_draft_credit_close = InvoicingDraftCreditClose.as_view()


class InvoicingFromBookingsMixin(FromBookingsMixin):
    def post_init(self, request, regie_identifier):
        self.regie = get_object_or_404(Regie, slug=regie_identifier)

    def get_current_payer_id(self, serializer):
        return serializer.validated_data['payer_external_id']


class InvoicingFromBookings(InvoicingFromBookingsMixin, APIView):
    permission_classes = (APIAdmin,)
    serializer_class = serializers.FromBookingsSerializer

    def process(self, request, serializer, aggregated_lines, payer_data_cache):
        invoice_data = serializer.validated_data.copy()
        invoice_data.pop('payer_external_id')
        invoice_data.pop('user_external_id')
        invoice_data.pop('user_first_name')
        invoice_data.pop('user_last_name')
        invoice_data.pop('form_url', '')
        invoice_data.pop('booked_events')
        invoice_data.pop('cancelled_events')
        draft_invoices = {}
        current_payer_external_id = serializer.validated_data['payer_external_id']
        # create draft invoice for each involved payer
        for payer_external_id in sorted(aggregated_lines.keys()):
            lines = aggregated_lines[payer_external_id]
            total_amount = sum(li['unit_amount'] * li['quantity'] for li in lines)
            if payer_external_id != current_payer_external_id and total_amount >= 0:
                # amount is positive, it will generate an invoice:
                # for other payers, only credits are generated
                continue
            payer_data = payer_data_cache[payer_external_id]
            draft_invoice = DraftInvoice.objects.create(
                regie=self.regie,
                origin='api',
                payer_external_id=payer_external_id,
                payer_first_name=payer_data['first_name'],
                payer_last_name=payer_data['last_name'],
                payer_address=payer_data['address'],
                payer_email=payer_data['email'],
                payer_phone=payer_data['phone'],
                **invoice_data,
            )
            invoice_lines = []
            for line in lines:
                invoice_lines.append(
                    DraftInvoiceLine(
                        user_external_id=serializer.validated_data['user_external_id'],
                        user_first_name=serializer.validated_data['user_first_name'],
                        user_last_name=serializer.validated_data['user_last_name'],
                        form_url=serializer.validated_data.get('form_url') or '',
                        invoice=draft_invoice,
                        **line,
                    )
                )
            DraftInvoiceLine.objects.bulk_create(invoice_lines)
            draft_invoice.refresh_from_db()  # refresh amounts
            draft_invoices[payer_external_id] = draft_invoice

        data = {}
        with transaction.atomic():
            if current_payer_external_id in draft_invoices:
                # make definitive invoice/credit for current payer
                draft_invoice = draft_invoices[current_payer_external_id]
                if draft_invoice.total_amount >= 0:
                    invoice = draft_invoice.promote()
                    invoice.make_assignments()
                    invoice.refresh_from_db()  # refresh amounts
                    data = invoice.get_notification_payload(request)
                    if draft_invoice.total_amount == 0:
                        invoice.notify(payload={'invoice_id': str(invoice.uuid)}, notification_type='payment')
                else:
                    credit = draft_invoice.promote()
                    credit.make_assignments()
                    credit.refresh_from_db()  # refresh amounts
                    data = credit.get_notification_payload(request)
            other_payer_draft_credits = []
            for payer_external_id in sorted(draft_invoices.keys()):
                # make draft invoice (future credit) for other payers
                if payer_external_id == current_payer_external_id:
                    continue
                other_payer_draft_credits.append(draft_invoices[payer_external_id])
            if other_payer_draft_credits:
                data['other_payer_credit_draft_ids'] = [str(di.uuid) for di in other_payer_draft_credits]

        return data


invoicing_from_bookings = InvoicingFromBookings.as_view()


class InvoicingFromBookingsDryRun(InvoicingFromBookingsMixin, APIView):
    permission_classes = (APIAdmin,)
    serializer_class = serializers.FromBookingsDryRunSerializer

    def process(self, request, serializer, aggregated_lines, payer_data_cache):
        invoicing_elements = {}
        current_payer_external_id = serializer.validated_data['payer_external_id']
        # invoicing_element for each involved payer
        for payer_external_id in sorted(aggregated_lines.keys()):
            lines = aggregated_lines[payer_external_id]
            payer_data = payer_data_cache[payer_external_id]
            invoicing_element_lines = []
            for line in lines:
                invoicing_element_lines.append(
                    {
                        'label': line['label'],
                        'description': line['description'],
                        'unit_amount': line['unit_amount'],
                        'quantity': line['quantity'],
                        'total_amount': line['quantity'] * line['unit_amount'],
                    }
                )
            total_amount = sum(li['total_amount'] for li in invoicing_element_lines)
            if payer_external_id != current_payer_external_id and total_amount >= 0:
                # amount is positive, it will generate an invoice:
                # for other payers, only credits are generated
                continue
            is_invoice = True
            if total_amount < 0:
                # it will be a credit, transform amounts
                total_amount *= -1
                is_invoice = False
                for line in invoicing_element_lines:
                    line['quantity'] *= -1
                    line['total_amount'] *= -1
            payer_name = f"{payer_data['first_name']} {payer_data['last_name']}".strip()
            invoicing_elements[payer_external_id] = {
                'total_amount': total_amount,
                'lines': invoicing_element_lines,
                'payer_external_id': payer_external_id,
                'payer_name': payer_name,
                'is_invoice': is_invoice,
            }
        data = {}
        if current_payer_external_id in invoicing_elements:
            invoicing_element = invoicing_elements[current_payer_external_id]
            is_invoice = invoicing_element.pop('is_invoice')
            if is_invoice:
                data['invoice'] = invoicing_element
            else:
                data['credit'] = invoicing_element
        other_payer_credit_drafts = []
        for payer_external_id in sorted(invoicing_elements.keys()):
            if payer_external_id == current_payer_external_id:
                continue
            invoicing_element = invoicing_elements[payer_external_id]
            invoicing_element.pop('is_invoice')
            other_payer_credit_drafts.append(invoicing_element)
        if other_payer_credit_drafts:
            data['other_payer_credit_drafts'] = other_payer_credit_drafts
        return data


invoicing_from_bookings_dry_run = InvoicingFromBookingsDryRun.as_view()


class InjectedLines(APIView):
    permission_classes = (APIAdmin,)
    serializer_class = serializers.InjectedLineSerializer

    def post(self, request, regie_identifier):
        regie = get_object_or_404(Regie, slug=regie_identifier)
        serializer = self.serializer_class(data=request.data)
        if not serializer.is_valid():
            raise APIErrorBadRequest(N_('invalid payload'), errors=serializer.errors)

        instance = InjectedLine.objects.create(regie=regie, **serializer.validated_data)
        return Response({'err': 0, 'id': instance.pk})


injected_lines = InjectedLines.as_view()


class InvoicingPayments(PayerMixin, APIView):
    permission_classes = (APIAdmin,)
    serializer_class = serializers.MakePaymentSerializer

    def get(self, request, regie_identifier):
        regie = get_object_or_404(Regie, slug=regie_identifier)
        payer_external_id = self.get_payer_external_id(
            request=request,
            regie=regie,
            nameid=request.GET.get('NameID'),
            payer_external_id=request.GET.get('payer_external_id'),
        )
        payment_qs = Payment.objects
        if request.GET.get('NameID'):
            payment_qs = payment_qs.exclude(payment_type__slug='collect')
        payments = payment_qs.filter(
            regie=regie,
            payer_external_id=payer_external_id,
            cancelled_at__isnull=True,
        ).order_by('-created_at')
        data = []
        for payment in payments:
            data.append(payment.normalize(for_backoffice=bool(request.GET.get('payer_external_id'))))
        return Response({'data': data})

    def post(self, request, regie_identifier):
        regie = get_object_or_404(Regie, slug=regie_identifier)
        serializer = self.serializer_class(data=request.data, regie=regie)
        if not serializer.is_valid():
            raise APIErrorBadRequest(N_('invalid payload'), errors=serializer.errors)

        bank_data = {}
        if serializer.validated_data['payment_type'].slug == 'online' and serializer.validated_data.get(
            'online_refdet'
        ):
            bank_data = {'refdet': serializer.validated_data['online_refdet']}
        instance = Payment.make_payment(
            regie=regie,
            invoices=serializer._invoices,
            lines=serializer._lines,
            amount=serializer.validated_data['amount'],
            payment_type=serializer.validated_data['payment_type'],
            payment_info={
                k: serializer.validated_data[k] for k, l in PAYMENT_INFO if serializer.validated_data.get(k)
            },
            bank_data=bank_data,
            date_payment=serializer.validated_data.get('date_payment'),
        )
        return Response(
            {
                'err': 0,
                'id': str(instance.uuid),  # legacy
                'data': instance.get_notification_payload(request),
            }
        )


invoicing_payments = InvoicingPayments.as_view()
invoicing_payments.publik_authentication_resolve_user_by_nameid = False


class InvoicingPayment(PayerMixin, APIView):
    permission_classes = (APIAdmin,)
    serializer_class = serializers.PaymentSerializer

    def patch(self, request, regie_identifier, payment_identifier):
        regie = get_object_or_404(Regie, slug=regie_identifier)
        payment = get_object_or_404(
            Payment,
            uuid=payment_identifier,
            regie=regie,
        )
        serializer = self.serializer_class(payment, data=request.data, partial=True)
        if not serializer.is_valid():
            raise APIErrorBadRequest(N_('invalid payload'), errors=serializer.errors)

        for k, dummy in PAYMENT_INFO:
            if serializer.validated_data.get(k):
                payment.payment_info[k] = serializer.validated_data[k]
        if payment.payment_type.slug == 'online' and serializer.validated_data.get('online_refdet'):
            payment.bank_data = {'refdet': serializer.validated_data['online_refdet']}
        payment.save()
        return Response(
            {
                'err': 0,
                'id': str(payment.uuid),  # legacy
                'data': payment.get_notification_payload(request),
            }
        )


invoicing_payment = InvoicingPayment.as_view()
invoicing_payment.publik_authentication_resolve_user_by_nameid = False


class InvoicingPaymentPDF(PayerMixin, APIView):
    permission_classes = (APIAdmin,)

    def get(self, request, regie_identifier, payment_identifier):
        regie = get_object_or_404(Regie, slug=regie_identifier)
        payer_external_id = self.get_payer_external_id(
            request=request,
            regie=regie,
            nameid=request.GET.get('NameID'),
            payer_external_id=request.GET.get('payer_external_id'),
        )
        payment_qs = Payment
        if request.GET.get('NameID'):
            payment_qs = Payment.objects.exclude(payment_type__slug='collect')
        payment = get_object_or_404(
            payment_qs,
            uuid=payment_identifier,
            regie=regie,
            payer_external_id=payer_external_id,
            cancelled_at__isnull=True,
        )
        result = payment.html()
        html = HTML(string=result)
        pdf = write_pdf(html)
        response = HttpResponse(pdf, content_type='application/pdf')
        response['Content-Disposition'] = 'attachment; filename="%s.pdf"' % payment.formatted_number
        return response


invoicing_payment_pdf = InvoicingPaymentPDF.as_view()
invoicing_payment_pdf.publik_authentication_resolve_user_by_nameid = False


class InvoicingCredits(PayerMixin, APIView):
    permission_classes = (APIAdmin,)
    serializer_class = serializers.CreditFiltersSerializer

    def get_credits_queryset(self, request, regie):
        serializer = self.serializer_class(data=request.query_params)
        if not serializer.is_valid():
            raise APIErrorBadRequest(N_('invalid filters'), errors=serializer.errors)
        data = serializer.validated_data

        payer_external_id = self.get_payer_external_id(
            request=request,
            regie=regie,
            nameid=request.GET.get('NameID'),
            payer_external_id=request.GET.get('payer_external_id'),
        )
        credit_qs = (
            Credit.objects.filter(
                regie=regie,
                remaining_amount__gt=0,
                payer_external_id=payer_external_id,
                date_publication__lte=now().date(),
                cancelled_at__isnull=True,
            )
            .exclude(pool__campaign__finalized=False)
            .order_by('-created_at')
        )
        if 'usable' in data and 'usable' in request.query_params:
            usable = data['usable']
            credit_qs = credit_qs.filter(usable=usable)
        return credit_qs

    def get(self, request, regie_identifier):
        regie = get_object_or_404(Regie, slug=regie_identifier)
        credits_qs = self.get_credits_queryset(request, regie)
        data = []
        for credit in credits_qs:
            data.append(credit.normalize(for_backoffice=bool(request.GET.get('payer_external_id'))))
        return Response({'data': data})


invoicing_credits = InvoicingCredits.as_view()
invoicing_credits.publik_authentication_resolve_user_by_nameid = False


class InvoicingHistoryCredits(InvoicingCredits):
    serializer_class = None

    def get_credits_queryset(self, request, regie):
        payer_external_id = self.get_payer_external_id(
            request=request,
            regie=regie,
            nameid=request.GET.get('NameID'),
            payer_external_id=request.GET.get('payer_external_id'),
        )
        return (
            Credit.objects.filter(
                regie=regie,
                remaining_amount=0,
                payer_external_id=payer_external_id,
                date_publication__lte=now().date(),
                cancelled_at__isnull=True,
            )
            .exclude(pool__campaign__finalized=False)
            .order_by('-created_at')
        )


invoicing_history_credits = InvoicingHistoryCredits.as_view()
invoicing_history_credits.publik_authentication_resolve_user_by_nameid = False


class InvoicingCreditPDF(PayerMixin, APIView):
    permission_classes = (APIAdmin,)

    def get(self, request, regie_identifier, credit_identifier):
        regie = get_object_or_404(Regie, slug=regie_identifier)
        payer_external_id = self.get_payer_external_id(
            request=request,
            regie=regie,
            nameid=request.GET.get('NameID'),
            payer_external_id=request.GET.get('payer_external_id'),
        )
        credit = get_object_or_404(
            Credit.objects.exclude(pool__campaign__finalized=False),
            uuid=credit_identifier,
            regie=regie,
            payer_external_id=payer_external_id,
            date_publication__lte=now().date(),
            cancelled_at__isnull=True,
        )
        result = credit.html()
        html = HTML(string=result)
        pdf = write_pdf(html)
        response = HttpResponse(pdf, content_type='application/pdf')
        response['Content-Disposition'] = 'attachment; filename="%s.pdf"' % credit.formatted_number
        return response


invoicing_credit_pdf = InvoicingCreditPDF.as_view()
invoicing_credit_pdf.publik_authentication_resolve_user_by_nameid = False


class InvoicingCreditAssign(APIView):
    permission_classes = (APIAdmin,)

    def post(self, request, regie_identifier, credit_identifier):
        regie = get_object_or_404(Regie, slug=regie_identifier)
        credit = get_object_or_404(
            Credit.objects.exclude(pool__campaign__finalized=False),
            uuid=credit_identifier,
            regie=regie,
            remaining_amount__gt=0,
            date_publication__lte=now().date(),
            cancelled_at__isnull=True,
            usable=True,
        )
        credit.make_assignments(force_assignation=True)
        return Response({'err': 0})


invoicing_credit_assign = InvoicingCreditAssign.as_view()


class InvoicingRefunds(PayerMixin, APIView):
    permission_classes = (APIAdmin,)
    serializer_class = serializers.RefundSerializer

    def get(self, request, regie_identifier):
        regie = get_object_or_404(Regie, slug=regie_identifier)
        payer_external_id = self.get_payer_external_id(
            request=request,
            regie=regie,
            nameid=request.GET.get('NameID'),
            payer_external_id=request.GET.get('payer_external_id'),
        )
        refunds = Refund.objects.filter(
            regie=regie,
            payer_external_id=payer_external_id,
        ).order_by('-created_at')
        data = []
        for refund in refunds:
            data.append(refund.normalize(for_backoffice=bool(request.GET.get('payer_external_id'))))
        return Response({'data': data})

    def post(self, request, regie_identifier):
        regie = get_object_or_404(Regie, slug=regie_identifier)
        serializer = self.serializer_class(data=request.data, regie=regie)
        if not serializer.is_valid():
            raise APIErrorBadRequest(N_('invalid payload'), errors=serializer.errors)

        credit = serializer.validated_data['credit']
        with transaction.atomic():
            refund = Refund.objects.create(
                regie=regie,
                amount=credit.remaining_amount,
                payer_external_id=credit.payer_external_id,
                payer_first_name=credit.payer_first_name,
                payer_last_name=credit.payer_last_name,
                payer_address=credit.payer_address,
                payer_email=credit.payer_email,
                payer_phone=credit.payer_phone,
                date_refund=serializer.validated_data.get('date_refund'),
            )
            refund.set_number()
            refund.save()
            CreditAssignment.objects.create(
                refund=refund,
                amount=credit.remaining_amount,
                credit=credit,
            )

        return Response(
            {
                'err': 0,
                'data': refund.get_notification_payload(request),
            }
        )


invoicing_refunds = InvoicingRefunds.as_view()
invoicing_refunds.publik_authentication_resolve_user_by_nameid = False


class InvoicingElementsSplit(APIView):
    permission_classes = (APIAdmin,)
    serializer_class = serializers.InvoicingElementsSplitSerializer

    def get_dates_in_period(self, line, date_start, date_end):
        for ldate in line.details['dates']:
            if ldate < date_start.isoformat():
                continue
            if ldate >= date_end.isoformat():
                continue
            yield datetime.datetime.strptime(ldate, '%Y-%m-%d').date()

    def get_dates_outside_period(self, line, date_start, date_end):
        for ldate in line.details['dates']:
            if ldate < date_start.isoformat():
                yield datetime.datetime.strptime(ldate, '%Y-%m-%d').date()
            if ldate >= date_end.isoformat():
                yield datetime.datetime.strptime(ldate, '%Y-%m-%d').date()

    def get_descriptions(self, line, dates_in_period, dates_outside_period):
        first_part = line.description.split(' ')[0].replace(',', '')
        if formats.date_format(dates_in_period[0], 'd/m') not in line.description:
            return line.description, line.description
        if first_part == formats.date_format(dates_in_period[0], 'd/m'):
            first_part = ''
        if first_part == formats.date_format(dates_outside_period[0], 'd/m'):
            first_part = ''
        if first_part:
            first_part += ' '
        return (
            '%s%s' % (first_part, ', '.join(formats.date_format(d, 'd/m') for d in dates_in_period)),
            '%s%s' % (first_part, ', '.join(formats.date_format(d, 'd/m') for d in dates_outside_period)),
        )

    def get_quantity_sign(self, line):
        if line.quantity > 1:
            return 1
        return -1

    def move_invoice_line(self, line, new_agenda, date_start, date_end):
        assert len(line.details['dates']) == abs(line.quantity)
        event_slug = line.event_slug.split('@')[1]

        dates_in_period = list(self.get_dates_in_period(line, date_start, date_end))
        assert dates_in_period
        dates_outside_period = list(self.get_dates_outside_period(line, date_start, date_end))
        if not dates_outside_period:
            line.agenda_slug = new_agenda.slug
            line.activity_label = new_agenda.label
            line.event_slug = f'{new_agenda.slug}@{event_slug}'
            line.save()
            return

        with transaction.atomic():
            new_description, old_description = self.get_descriptions(
                line, dates_in_period, dates_outside_period
            )
            quantity_sign = self.get_quantity_sign(line)

            new_line = InvoiceLine.objects.create(
                invoice=line.invoice,
                event_date=line.event_date,
                event_slug=f'{new_agenda.slug}@{event_slug}',
                event_label=line.event_label,
                agenda_slug=new_agenda.slug,
                activity_label=new_agenda.label,
                label=line.label,
                description=new_description,
                user_external_id=line.user_external_id,
                user_first_name=line.user_first_name,
                user_last_name=line.user_last_name,
                quantity=quantity_sign * len(dates_in_period),
                unit_amount=line.unit_amount,
                accounting_code=line.accounting_code,
                form_url=line.form_url,
                details={'dates': dates_in_period},
            )
            new_line.refresh_from_db()  # refresh amounts

            old_invoice_line_payments = InvoiceLinePayment.objects.filter(line=line).order_by('created_at')
            old_payment_amount = 0
            old_line_new_amount = line.total_amount - new_line.total_amount
            for invoice_line_payment in old_invoice_line_payments:
                if old_payment_amount == old_line_new_amount:
                    # old line new amount is reached, move payment line on new line
                    invoice_line_payment.line = new_line
                    invoice_line_payment.save()
                    continue

                if old_payment_amount + invoice_line_payment.amount <= old_line_new_amount:
                    # keep payment line on old line, until old line new amount
                    old_payment_amount += invoice_line_payment.amount
                    continue

                # split payment line
                remaining = old_line_new_amount - old_payment_amount
                InvoiceLinePayment.objects.create(
                    payment=invoice_line_payment.payment,
                    line=new_line,
                    amount=invoice_line_payment.amount - remaining,
                )
                invoice_line_payment.amount = remaining
                invoice_line_payment.save()
                old_payment_amount += remaining

            line.refresh_from_db()  # refresh amounts
            line.details['dates'] = dates_outside_period
            line.description = old_description
            line.quantity = quantity_sign * len(dates_outside_period)
            line.save()

        return new_line

    def move_credit_line(self, line, new_agenda, date_start, date_end):
        assert len(line.details['dates']) == abs(line.quantity)
        event_slug = line.event_slug.split('@')[1]

        dates_in_period = list(self.get_dates_in_period(line, date_start, date_end))
        assert dates_in_period
        dates_outside_period = list(self.get_dates_outside_period(line, date_start, date_end))
        if not dates_outside_period:
            line.agenda_slug = new_agenda.slug
            line.activity_label = new_agenda.label
            line.event_slug = f'{new_agenda.slug}@{event_slug}'
            line.save()
            return

        with transaction.atomic():
            new_description, old_description = self.get_descriptions(
                line, dates_in_period, dates_outside_period
            )
            quantity_sign = self.get_quantity_sign(line)
            new_line = CreditLine.objects.create(
                credit=line.credit,
                event_date=line.event_date,
                event_slug=f'{new_agenda.slug}@{event_slug}',
                event_label=line.event_label,
                agenda_slug=new_agenda.slug,
                activity_label=new_agenda.label,
                label=line.label,
                description=new_description,
                user_external_id=line.user_external_id,
                user_first_name=line.user_first_name,
                user_last_name=line.user_last_name,
                quantity=quantity_sign * len(dates_in_period),
                unit_amount=line.unit_amount,
                accounting_code=line.accounting_code,
                form_url=line.form_url,
                details={'dates': dates_in_period},
            )
            new_line.refresh_from_db()

            line.refresh_from_db()
            line.details['dates'] = dates_outside_period
            line.description = old_description
            line.quantity = quantity_sign * len(dates_outside_period)
            line.save()

        return new_line

    def post(self, request, regie_identifier):
        regie = get_object_or_404(Regie, slug=regie_identifier)
        serializer = self.serializer_class(data=request.data, regie=regie)
        if not serializer.is_valid():
            raise APIErrorBadRequest(N_('invalid payload'), errors=serializer.errors)

        old_agenda = serializer.validated_data['old_agenda']
        new_agenda = serializer.validated_data['new_agenda']
        user_external_id = serializer.validated_data['user_external_id']
        date_start = serializer.validated_data['date_start']
        date_end = serializer.validated_data['date_end']

        result = []

        old_invoice_lines = InvoiceLine.objects.filter(
            user_external_id=user_external_id,
            agenda_slug=old_agenda.slug,
            event_slug__startswith=f'{old_agenda.slug}@',
            details__jsonpath_exists=f'$.dates[*] ? (@ < "{date_end}" && @ >= "{date_start}")',
            invoice__cancelled_at__isnull=True,
            invoice__regie=regie,
        ).select_related('invoice')
        for line in old_invoice_lines:
            new_line = self.move_invoice_line(line, new_agenda, date_start, date_end)
            if new_line is None:
                result.append(
                    {
                        'invoice_id': line.invoice.uuid,
                        'line_id': line.uuid,
                        'action': 'moved',
                    }
                )
            else:
                result.append(
                    {
                        'invoice_id': line.invoice.uuid,
                        'line_id': line.uuid,
                        'action': 'updated',
                    }
                )
                result.append(
                    {
                        'invoice_id': new_line.invoice.uuid,
                        'line_id': new_line.uuid,
                        'action': 'created',
                    }
                )

        old_credit_lines = CreditLine.objects.filter(
            user_external_id=user_external_id,
            agenda_slug=old_agenda.slug,
            event_slug__startswith=f'{old_agenda.slug}@',
            details__jsonpath_exists=f'$.dates[*] ? (@ < "{date_end}" && @ >= "{date_start}")',
            credit__cancelled_at__isnull=True,
            credit__regie=regie,
        ).select_related('credit')
        for line in old_credit_lines:
            new_line = self.move_credit_line(line, new_agenda, date_start, date_end)
            if new_line is None:
                result.append(
                    {
                        'credit_id': line.credit.uuid,
                        'line_id': line.uuid,
                        'action': 'moved',
                    }
                )
            else:
                result.append(
                    {
                        'credit_id': line.credit.uuid,
                        'line_id': line.uuid,
                        'action': 'updated',
                    }
                )
                result.append(
                    {
                        'credit_id': new_line.credit.uuid,
                        'line_id': new_line.uuid,
                        'action': 'created',
                    }
                )

        return Response({'err': 0, 'invoicing_elements': result})


invoicing_elements_split = InvoicingElementsSplit.as_view()
