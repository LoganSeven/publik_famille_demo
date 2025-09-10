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

from django.urls import reverse
from django.views.generic import CreateView, DeleteView, TemplateView, UpdateView

from lingo.invoicing.forms import (
    CreditCancellationReasonForm,
    InvoiceCancellationReasonForm,
    PaymentCancellationReasonForm,
)
from lingo.invoicing.models import (
    CreditCancellationReason,
    InvoiceCancellationReason,
    PaymentCancellationReason,
)
from lingo.manager.utils import StaffRequiredMixin


class ReasonListView(StaffRequiredMixin, TemplateView):
    template_name = 'lingo/invoicing/manager_cancellation_reason_list.html'

    def get_context_data(self, **kwargs):
        kwargs.update(
            {
                'invoice_reason_list': InvoiceCancellationReason.objects.all().order_by('disabled', 'label'),
                'credit_reason_list': CreditCancellationReason.objects.all().order_by('disabled', 'label'),
                'payment_reason_list': PaymentCancellationReason.objects.all().order_by('disabled', 'label'),
            }
        )
        return super().get_context_data(**kwargs)


reason_list = ReasonListView.as_view()


class InvoiceReasonAddView(StaffRequiredMixin, CreateView):
    template_name = 'lingo/invoicing/manager_invoice_cancellation_reason_form.html'
    model = InvoiceCancellationReason
    fields = ['label']

    def get_success_url(self):
        return '%s#open:invoice' % reverse('lingo-manager-invoicing-cancellation-reason-list')


invoice_reason_add = InvoiceReasonAddView.as_view()


class InvoiceReasonEditView(StaffRequiredMixin, UpdateView):
    template_name = 'lingo/invoicing/manager_invoice_cancellation_reason_form.html'
    model = InvoiceCancellationReason
    form_class = InvoiceCancellationReasonForm

    def get_success_url(self):
        return '%s#open:invoice' % reverse('lingo-manager-invoicing-cancellation-reason-list')


invoice_reason_edit = InvoiceReasonEditView.as_view()


class InvoiceReasonDeleteView(StaffRequiredMixin, DeleteView):
    template_name = 'lingo/manager_confirm_delete.html'
    model = InvoiceCancellationReason

    def get_queryset(self):
        return InvoiceCancellationReason.objects.filter(invoice__isnull=True)

    def get_success_url(self):
        return '%s#open:invoice' % reverse('lingo-manager-invoicing-cancellation-reason-list')


invoice_reason_delete = InvoiceReasonDeleteView.as_view()


class CreditReasonAddView(StaffRequiredMixin, CreateView):
    template_name = 'lingo/invoicing/manager_credit_cancellation_reason_form.html'
    model = CreditCancellationReason
    fields = ['label']

    def get_success_url(self):
        return '%s#open:credit' % reverse('lingo-manager-invoicing-cancellation-reason-list')


credit_reason_add = CreditReasonAddView.as_view()


class CreditReasonEditView(StaffRequiredMixin, UpdateView):
    template_name = 'lingo/invoicing/manager_credit_cancellation_reason_form.html'
    model = CreditCancellationReason
    form_class = CreditCancellationReasonForm

    def get_success_url(self):
        return '%s#open:credit' % reverse('lingo-manager-invoicing-cancellation-reason-list')


credit_reason_edit = CreditReasonEditView.as_view()


class CreditReasonDeleteView(StaffRequiredMixin, DeleteView):
    template_name = 'lingo/manager_confirm_delete.html'
    model = CreditCancellationReason

    def get_queryset(self):
        return CreditCancellationReason.objects.filter(credit__isnull=True)

    def get_success_url(self):
        return '%s#open:credit' % reverse('lingo-manager-invoicing-cancellation-reason-list')


credit_reason_delete = CreditReasonDeleteView.as_view()


class PaymentReasonAddView(StaffRequiredMixin, CreateView):
    template_name = 'lingo/invoicing/manager_payment_cancellation_reason_form.html'
    model = PaymentCancellationReason
    fields = ['label']

    def get_success_url(self):
        return '%s#open:payment' % reverse('lingo-manager-invoicing-cancellation-reason-list')


payment_reason_add = PaymentReasonAddView.as_view()


class PaymentReasonEditView(StaffRequiredMixin, UpdateView):
    template_name = 'lingo/invoicing/manager_payment_cancellation_reason_form.html'
    model = PaymentCancellationReason
    form_class = PaymentCancellationReasonForm

    def get_success_url(self):
        return '%s#open:payment' % reverse('lingo-manager-invoicing-cancellation-reason-list')


payment_reason_edit = PaymentReasonEditView.as_view()


class PaymentReasonDeleteView(StaffRequiredMixin, DeleteView):
    template_name = 'lingo/manager_confirm_delete.html'
    model = PaymentCancellationReason

    def get_queryset(self):
        return PaymentCancellationReason.objects.filter(payment__isnull=True)

    def get_success_url(self):
        return '%s#open:payment' % reverse('lingo-manager-invoicing-cancellation-reason-list')


payment_reason_delete = PaymentReasonDeleteView.as_view()
