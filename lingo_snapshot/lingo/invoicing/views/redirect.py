# lingo - payment and billing system
# Copyright (C) 2024  Entr'ouvert
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


from django.http import Http404
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views.generic import RedirectView

from lingo.agendas.models import Agenda
from lingo.invoicing.models import Credit, Invoice, Payment, Refund


class InvoiceRedirectView(RedirectView):
    def get_redirect_url(self, *args, **kwargs):
        invoice = get_object_or_404(Invoice, uuid=kwargs['invoice_uuid'])
        return '%s?number=%s' % (
            reverse('lingo-manager-invoicing-regie-invoice-list', args=[invoice.regie_id]),
            invoice.formatted_number,
        )


invoice_redirect = InvoiceRedirectView.as_view()


class InvoicePDFRedirectView(RedirectView):
    def get_redirect_url(self, *args, **kwargs):
        invoice = get_object_or_404(Invoice, uuid=kwargs['invoice_uuid'])
        return reverse('lingo-manager-invoicing-regie-invoice-pdf', args=[invoice.regie_id, invoice.pk])


invoice_pdf_redirect = InvoicePDFRedirectView.as_view()


class CreditRedirectView(RedirectView):
    def get_redirect_url(self, *args, **kwargs):
        credit = get_object_or_404(Credit, uuid=kwargs['credit_uuid'])
        return '%s?number=%s' % (
            reverse('lingo-manager-invoicing-regie-credit-list', args=[credit.regie_id]),
            credit.formatted_number,
        )


credit_redirect = CreditRedirectView.as_view()


class CreditPDFRedirectView(RedirectView):
    def get_redirect_url(self, *args, **kwargs):
        credit = get_object_or_404(Credit, uuid=kwargs['credit_uuid'])
        return reverse('lingo-manager-invoicing-regie-credit-pdf', args=[credit.regie_id, credit.pk])


credit_pdf_redirect = CreditPDFRedirectView.as_view()


class PaymentRedirectView(RedirectView):
    def get_redirect_url(self, *args, **kwargs):
        payment = get_object_or_404(Payment, uuid=kwargs['payment_uuid'])
        return '%s?number=%s' % (
            reverse('lingo-manager-invoicing-regie-payment-list', args=[payment.regie_id]),
            payment.formatted_number,
        )


payment_redirect = PaymentRedirectView.as_view()


class PaymentPDFRedirectView(RedirectView):
    def get_redirect_url(self, *args, **kwargs):
        payment = get_object_or_404(Payment, uuid=kwargs['payment_uuid'])
        return reverse('lingo-manager-invoicing-regie-payment-pdf', args=[payment.regie_id, payment.pk])


payment_pdf_redirect = PaymentPDFRedirectView.as_view()


class RefundRedirectView(RedirectView):
    def get_redirect_url(self, *args, **kwargs):
        refund = get_object_or_404(Refund, uuid=kwargs['refund_uuid'])
        return '%s?number=%s' % (
            reverse('lingo-manager-invoicing-regie-refund-list', args=[refund.regie_id]),
            refund.formatted_number,
        )


refund_redirect = RefundRedirectView.as_view()


class TransactionRedirectView(RedirectView):
    def get_redirect_url(self, *args, **kwargs):
        event_slug = self.request.GET.get('event_slug')
        if not event_slug:
            raise Http404
        parts = event_slug.split('@')
        if len(parts) != 2:
            raise Http404
        agenda = get_object_or_404(Agenda, slug=parts[0])
        if agenda.regie is None:
            raise Http404
        return '%s?%s' % (
            reverse('lingo-manager-invoicing-regie-transaction-for-event-list', args=[agenda.regie_id]),
            self.request.GET.urlencode(),
        )


transaction_redirect = TransactionRedirectView.as_view()
