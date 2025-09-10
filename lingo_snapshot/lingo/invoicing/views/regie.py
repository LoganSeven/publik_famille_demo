# lingo - payment and billing system
# Copyright (C) 2022  Entr'ouvert
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

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import (
    CharField,
    Count,
    DecimalField,
    Exists,
    F,
    Func,
    IntegerField,
    JSONField,
    OuterRef,
    Prefetch,
    Q,
    Subquery,
    Sum,
    Value,
)
from django.db.models.functions import Coalesce, Concat, Trim
from django.http import Http404, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect
from django.template.loader import get_template
from django.urls import reverse
from django.utils.timezone import localtime, now
from django.utils.translation import gettext_lazy as _
from django.utils.translation import pgettext
from django.views.generic import CreateView, DeleteView, DetailView, FormView, ListView, UpdateView
from weasyprint import HTML

from lingo.agendas.models import Agenda
from lingo.export_import.views import WithApplicationsMixin
from lingo.invoicing.forms import (
    CollectionDocketForm,
    PaymentDocketForm,
    PaymentDocketPaymentTypeForm,
    PaymentTypeForm,
    RegieCollectionInvoiceFilterSet,
    RegieCreditCancelForm,
    RegieCreditFilterSet,
    RegieDocketFilterSet,
    RegieDocketPaymentFilterSet,
    RegieForm,
    RegieInvoiceCancelForm,
    RegieInvoiceDatesForm,
    RegieInvoiceFilterSet,
    RegiePayerFilterSet,
    RegiePayerForm,
    RegiePayerMappingForm,
    RegiePayerTransactionFilterSet,
    RegiePaymentCancelForm,
    RegiePaymentFilterSet,
    RegiePublishingForm,
    RegieRefundFilterSet,
)
from lingo.invoicing.models import (
    ORIGINS,
    PAYMENT_INFO,
    CollectionDocket,
    Counter,
    Credit,
    CreditAssignment,
    CreditLine,
    InjectedLine,
    Invoice,
    InvoiceLine,
    InvoiceLinePayment,
    JournalLine,
    Payment,
    PaymentDocket,
    PaymentType,
    Pool,
    Refund,
    Regie,
)
from lingo.invoicing.views.utils import PDFMixin
from lingo.manager.utils import (
    CanBeControlledCheckMixin,
    CanBeInvoicedCheckMixin,
    CanBeManagedCheckMixin,
    CanBeManagedRequiredMixin,
    CanBeViewedCheckMixin,
    CanBeViewedRequiredMixin,
    StaffRequiredMixin,
)
from lingo.snapshot.models import RegieSnapshot
from lingo.snapshot.views import InstanceWithSnapshotHistoryCompareView, InstanceWithSnapshotHistoryView
from lingo.utils.misc import json_dump
from lingo.utils.ods import Workbook
from lingo.utils.pdf import write_pdf


class RegiesListView(WithApplicationsMixin, ListView):
    template_name = 'lingo/invoicing/manager_regie_list.html'
    model = Regie

    def dispatch(self, request, *args, **kwargs):
        self.with_applications_dispatch(request)
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        queryset = self.with_applications_queryset()
        if not self.request.user.is_staff:
            group_ids = [x.id for x in self.request.user.groups.all()]
            queryset = queryset.filter(
                Q(view_role_id__in=group_ids)
                | Q(edit_role_id__in=group_ids)
                | Q(invoice_role_id__in=group_ids)
                | Q(control_role_id__in=group_ids)
            )
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        return self.with_applications_context_data(context)


regies_list = RegiesListView.as_view()


def regies_goto_reference(request):
    reference = request.GET.get('reference') or ''
    objs = {
        Invoice: 'lingo-manager-invoicing-regie-invoice-list',
        Credit: 'lingo-manager-invoicing-regie-credit-list',
        Payment: 'lingo-manager-invoicing-regie-payment-list',
        Refund: 'lingo-manager-invoicing-regie-refund-list',
    }
    for obj_type, obj_url in objs.items():
        obj = obj_type.objects.filter(formatted_number__iexact=reference).first()
        if obj:
            url = reverse(obj_url, kwargs={'regie_pk': obj.regie_id})
            return HttpResponseRedirect(url + f'?number={obj.formatted_number}')
    messages.error(request, _('No document found for "%s"') % reference)
    return HttpResponseRedirect(reverse('lingo-manager-invoicing-regie-list'))


class RegieAddView(StaffRequiredMixin, CreateView):
    template_name = 'lingo/invoicing/manager_regie_form.html'
    model = Regie
    fields = ['label', 'description', 'edit_role', 'view_role', 'invoice_role', 'control_role']

    def form_valid(self, form):
        response = super().form_valid(form)
        PaymentType.create_defaults(self.object)
        self.object.take_snapshot(request=self.request, comment=pgettext('snapshot', 'created'))
        return response

    def get_success_url(self):
        return reverse('lingo-manager-invoicing-regie-detail', args=[self.object.pk])


regie_add = RegieAddView.as_view()


class RegieDetailView(CanBeViewedRequiredMixin, DetailView):
    template_name = 'lingo/invoicing/manager_regie_detail.html'
    model = Regie

    def get_context_data(self, **kwargs):
        kwargs['regie'] = self.object
        kwargs['show_non_invoiced_lines'] = settings.SHOW_NON_INVOICED_LINES
        return super().get_context_data(**kwargs)


regie_detail = RegieDetailView.as_view()


class RegieParametersView(CanBeViewedRequiredMixin, DetailView):
    template_name = 'lingo/invoicing/manager_regie_parameters.html'
    model = Regie

    def get_queryset(self):
        paymenttypes_qs = PaymentType.objects.annotate(
            used=Exists(Payment.objects.filter(payment_type=OuterRef('id')))
        )
        return (
            super()
            .get_queryset()
            .select_related('edit_role', 'view_role', 'invoice_role', 'control_role')
            .prefetch_related(Prefetch('paymenttype_set', queryset=paymenttypes_qs))
        )

    def get_context_data(self, **kwargs):
        kwargs['regie'] = self.object
        kwargs['agendas'] = Agenda.objects.filter(regie=self.object).order_by('category_label', 'label')
        has_related_objects = False
        if self.object.campaign_set.exists():
            has_related_objects = True
        elif self.object.injectedline_set.exists():
            has_related_objects = True
        kwargs['has_related_objects'] = has_related_objects
        kwargs['user_can_manage'] = self.object.can_be_managed(self.request.user)
        return super().get_context_data(**kwargs)


regie_parameters = RegieParametersView.as_view()


class RegieEditView(CanBeManagedRequiredMixin, UpdateView):
    template_name = 'lingo/invoicing/manager_regie_form.html'
    model = Regie
    form_class = RegieForm

    def get_success_url(self):
        return reverse('lingo-manager-invoicing-regie-parameters', args=[self.object.pk])

    def form_valid(self, *args, **kwargs):
        response = super().form_valid(*args, **kwargs)
        self.object.take_snapshot(request=self.request)
        return response


regie_edit = RegieEditView.as_view()


class RegiePermissionsEditView(CanBeManagedRequiredMixin, UpdateView):
    template_name = 'lingo/invoicing/manager_regie_permissions_form.html'
    model = Regie
    fields = ['edit_role', 'view_role', 'invoice_role', 'control_role']

    def get_success_url(self):
        return '%s#open:permissions' % reverse(
            'lingo-manager-invoicing-regie-parameters', args=[self.object.pk]
        )

    def form_valid(self, *args, **kwargs):
        response = super().form_valid(*args, **kwargs)
        self.object.take_snapshot(request=self.request, comment=_('changed permissions'))
        return response


regie_permissions_edit = RegiePermissionsEditView.as_view()


class RegiePayerEditView(CanBeManagedRequiredMixin, UpdateView):
    template_name = 'lingo/invoicing/manager_regie_payer_form.html'
    model = Regie
    form_class = RegiePayerForm

    def get_success_url(self):
        return '%s#open:payer' % reverse('lingo-manager-invoicing-regie-parameters', args=[self.object.pk])

    def form_valid(self, *args, **kwargs):
        response = super().form_valid(*args, **kwargs)
        self.object.take_snapshot(request=self.request, comment=_('changed payer options'))
        return response


regie_payer_edit = RegiePayerEditView.as_view()


class RegiePayerMappingEditView(CanBeManagedRequiredMixin, UpdateView):
    template_name = 'lingo/invoicing/manager_regie_payer_mapping_form.html'
    model = Regie
    form_class = RegiePayerMappingForm

    def get_success_url(self):
        return '%s#open:payer-mapping' % reverse(
            'lingo-manager-invoicing-regie-parameters', args=[self.object.pk]
        )

    def form_valid(self, *args, **kwargs):
        response = super().form_valid(*args, **kwargs)
        self.object.take_snapshot(request=self.request, comment=_('changed payer mapping'))
        return response


regie_payer_mapping_edit = RegiePayerMappingEditView.as_view()


class RegieCountersEditView(CanBeManagedRequiredMixin, UpdateView):
    template_name = 'lingo/invoicing/manager_regie_counters_form.html'
    model = Regie
    fields = [
        'counter_name',
        'invoice_number_format',
        'collection_number_format',
        'payment_number_format',
        'docket_number_format',
        'credit_number_format',
        'refund_number_format',
    ]

    def get_success_url(self):
        return '%s#open:counters' % reverse('lingo-manager-invoicing-regie-parameters', args=[self.object.pk])

    def form_valid(self, *args, **kwargs):
        response = super().form_valid(*args, **kwargs)
        self.object.take_snapshot(request=self.request, comment=_('changed counters'))
        return response


regie_counters_edit = RegieCountersEditView.as_view()


class RegiePublishingEditView(CanBeManagedRequiredMixin, UpdateView):
    template_name = 'lingo/invoicing/manager_regie_publishing_form.html'
    model = Regie
    form_class = RegiePublishingForm

    def get_success_url(self):
        return '%s#open:publishing' % reverse(
            'lingo-manager-invoicing-regie-parameters', args=[self.object.pk]
        )

    def form_valid(self, *args, **kwargs):
        response = super().form_valid(*args, **kwargs)
        self.object.take_snapshot(request=self.request, comment=_('changed publishing settings'))
        return response


regie_publishing_edit = RegiePublishingEditView.as_view()


class RegieDeleteView(CanBeManagedRequiredMixin, DeleteView):
    template_name = 'lingo/manager_confirm_delete.html'
    model = Regie

    def get_queryset(self):
        return super().get_queryset().filter(campaign__isnull=True, injectedline__isnull=True)

    def form_valid(self, form):
        self.object = self.get_object()
        self.object.take_snapshot(request=self.request, deletion=True)
        Counter.objects.filter(regie=self.object).delete()
        PaymentType.objects.filter(regie=self.object).delete()
        return super().form_valid(form)

    def get_success_url(self):
        return reverse('lingo-manager-invoicing-regie-list')


regie_delete = RegieDeleteView.as_view()


class RegieExport(CanBeManagedRequiredMixin, DetailView):
    model = Regie

    def get(self, request, *args, **kwargs):
        response = HttpResponse(content_type='application/json')
        attachment = 'attachment; filename="export_regie_{}_{}.json"'.format(
            self.get_object().slug, now().strftime('%Y%m%d')
        )
        response['Content-Disposition'] = attachment
        json_dump({'regies': [self.get_object().export_json()]}, response, indent=2)
        return response


regie_export = RegieExport.as_view()


class PaymentTypeAddView(CanBeManagedCheckMixin, CreateView):
    template_name = 'lingo/invoicing/manager_payment_type_form.html'
    model = PaymentType
    fields = ['label']

    def dispatch(self, request, *args, **kwargs):
        self.regie = get_object_or_404(Regie, pk=kwargs.pop('regie_pk'))
        self.check_object(self.regie)
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        kwargs['regie'] = self.regie
        return super().get_context_data(**kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        if not kwargs.get('instance'):
            kwargs['instance'] = self.model()
        kwargs['instance'].regie = self.regie
        return kwargs

    def get_success_url(self):
        return '%s#open:payment-types' % reverse(
            'lingo-manager-invoicing-regie-parameters', args=[self.regie.pk]
        )

    def form_valid(self, *args, **kwargs):
        response = super().form_valid(*args, **kwargs)
        self.regie.take_snapshot(request=self.request, comment=_('added payment type'))
        return response


payment_type_add = PaymentTypeAddView.as_view()


class PaymentTypeEditView(CanBeManagedCheckMixin, UpdateView):
    template_name = 'lingo/invoicing/manager_payment_type_form.html'
    model = PaymentType
    form_class = PaymentTypeForm

    def dispatch(self, request, *args, **kwargs):
        self.regie = get_object_or_404(Regie, pk=kwargs.pop('regie_pk'))
        self.check_object(self.regie)
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        kwargs['regie'] = self.regie
        return super().get_context_data(**kwargs)

    def get_queryset(self):
        return PaymentType.objects.filter(regie=self.regie)

    def get_success_url(self):
        return '%s#open:payment-types' % reverse(
            'lingo-manager-invoicing-regie-parameters', args=[self.regie.pk]
        )

    def form_valid(self, *args, **kwargs):
        response = super().form_valid(*args, **kwargs)
        self.regie.take_snapshot(request=self.request, comment=_('changed payment type'))
        return response


payment_type_edit = PaymentTypeEditView.as_view()


class PaymentTypeDeleteView(CanBeManagedCheckMixin, DeleteView):
    template_name = 'lingo/manager_confirm_delete.html'
    model = PaymentType

    def dispatch(self, request, *args, **kwargs):
        self.regie = get_object_or_404(Regie, pk=kwargs.pop('regie_pk'))
        self.check_object(self.regie)
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return PaymentType.objects.filter(regie=self.regie.pk, payment__isnull=True)

    def get_success_url(self):
        return '%s#open:payment-types' % reverse(
            'lingo-manager-invoicing-regie-parameters', args=[self.regie.pk]
        )

    def post(self, *args, **kwargs):
        response = super().post(*args, **kwargs)
        self.regie.take_snapshot(request=self.request, comment=_('removed payment type'))
        return response


payment_type_delete = PaymentTypeDeleteView.as_view()


class NonInvoicedLineListView(CanBeViewedCheckMixin, ListView):
    template_name = 'lingo/invoicing/manager_non_invoiced_line_list.html'
    paginate_by = 100

    def dispatch(self, request, *args, **kwargs):
        self.regie = get_object_or_404(Regie, pk=kwargs['regie_pk'])
        self.check_object(self.regie)
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        fields = [
            'pk',
            'event_date',
            'slug',
            'label',
            'amount',
            'user_external_id',
            'payer_external_id',
            'payer_first_name',
            'payer_last_name',
            'user_first_name',
            'user_last_name',
            'event',
            'pricing_data',
            'status',
            'pool_id',
        ]
        qs1 = JournalLine.objects.filter(
            status='error', error_status='', pool__campaign__regie=self.regie
        ).values(*fields)
        qs2 = (
            InjectedLine.objects.filter(journalline__isnull=True, regie=self.regie)
            .annotate(
                user_first_name=Value('', output_field=CharField()),
                user_last_name=Value('', output_field=CharField()),
                event=Value({}, output_field=JSONField()),
                pricing_data=Value({}, output_field=JSONField()),
                status=Value('injected', output_field=CharField()),
                pool_id=Value(0, output_field=IntegerField()),
            )
            .values(*fields)
        )
        qs = qs1.union(qs2).order_by('event_date', 'user_external_id', 'label', 'pk')
        return qs

    def get_context_data(self, **kwargs):
        kwargs['regie'] = self.regie
        context = super().get_context_data(**kwargs)
        pools = Pool.objects.filter(draft=False).in_bulk()
        for line in context['object_list']:
            if line['status'] == 'error':
                line['user_name'] = JournalLine(
                    user_first_name=line['user_first_name'], user_last_name=line['user_last_name']
                ).user_name
                line['payer_name'] = JournalLine(
                    payer_first_name=line['payer_first_name'], payer_last_name=line['payer_last_name']
                ).payer_name
                line['error_display'] = JournalLine(
                    status=line['status'], pricing_data=line['pricing_data']
                ).get_error_display()
                line['campaign_id'] = pools[line['pool_id']].campaign_id
                line['chrono_event_url'] = JournalLine(event=line['event']).get_chrono_event_url()
        return context


non_invoiced_line_list = NonInvoicedLineListView.as_view()


class RegieInvoiceListView(CanBeViewedCheckMixin, ListView):
    template_name = 'lingo/invoicing/manager_invoice_list.html'
    paginate_by = 100

    def dispatch(self, request, *args, **kwargs):
        self.regie = get_object_or_404(Regie, pk=kwargs['regie_pk'])
        self.check_object(self.regie)
        if 'ods' in request.GET and not self.regie.can_be_invoiced(self.request.user):
            raise PermissionDenied()
        self.full = bool('full' in request.GET)
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        invoice_queryset = (
            Invoice.objects.filter(
                regie=self.regie,
            )
            .exclude(pool__campaign__finalized=False)
            .select_related('pool', 'collection')
            .order_by('-created_at')
        )
        if self.full:
            payment_queryset = InvoiceLinePayment.objects.select_related('payment__payment_type')
            invoice_queryset = invoice_queryset.prefetch_related(
                Prefetch('lines', queryset=InvoiceLine.objects.all().order_by('pk')),
                Prefetch('lines__invoicelinepayment_set', queryset=payment_queryset),
            )
        self.filterset = RegieInvoiceFilterSet(data=self.request.GET or None, queryset=invoice_queryset)
        return self.filterset.qs

    def get_context_data(self, **kwargs):
        kwargs['regie'] = self.regie
        kwargs['filterset'] = self.filterset
        kwargs['user_can_invoice'] = self.regie.can_be_invoiced(self.request.user)
        return super().get_context_data(**kwargs)

    def get(self, request, *args, **kwargs):
        self.object_list = self.get_queryset()
        context = self.get_context_data()
        if 'ods' in request.GET and self.filterset.form.is_valid():
            return self.ods(request, context)
        return self.render_to_response(context)

    def ods(self, request, context):
        response = HttpResponse(content_type='application/vnd.oasis.opendocument.spreadsheet')
        if self.full:
            response['Content-Disposition'] = 'attachment; filename="invoices-full.ods"'
        else:
            response['Content-Disposition'] = 'attachment; filename="invoices.ods"'

        writer = Workbook()

        headers = [
            _('Number'),
            _('Origin'),
            _('Payer ID'),
            _('Payer first name'),
            _('Payer last name'),
        ]
        if self.full:
            headers += [
                _('Payer email'),
                _('Payer phone'),
            ]
        headers += [
            _('Creation date'),
            _('Invoicing date'),
            _('Publication date'),
            _('Payment deadline'),
            _('Due date'),
            _('Direct debit'),
        ]
        if self.full:
            headers += [
                _('Description'),
                _('Accounting code'),
                _('Unit amount'),
                _('Quantity'),
            ]
        headers += [
            _('Total due'),
        ]
        if self.full:
            headers += [
                _('Payment type'),
            ]
        headers += [
            _('Paid amount'),
            _('Status'),
            _('Cancelled on'),
            _('Cancellation reason'),
        ]
        # headers
        writer.writerow(headers, headers=True)
        for invoice in self.object_list:
            paid_status = _('Not paid')
            if invoice.cancelled_at is not None:
                paid_status = pgettext('invoice', 'Cancelled')
            elif invoice.remaining_amount > 0 and invoice.paid_amount > 0:
                paid_status = _('Partially paid')
            elif invoice.remaining_amount == 0:
                paid_status = _('Paid')
            if not self.full:
                writer.writerow(
                    [
                        invoice.formatted_number,
                        invoice.get_origin_display(),
                        invoice.payer_external_id,
                        invoice.payer_first_name,
                        invoice.payer_last_name,
                        invoice.created_at.date(),
                        invoice.date_invoicing,
                        invoice.date_publication,
                        invoice.date_payment_deadline,
                        invoice.date_due,
                        invoice.payer_direct_debit,
                        invoice.total_amount,
                        invoice.paid_amount,
                        paid_status,
                        invoice.cancelled_at,
                        invoice.cancellation_reason,
                    ]
                )
                continue
            for line in invoice.lines.all():
                if line.total_amount == 0:
                    continue
                paid_status = _('Not paid')
                if invoice.cancelled_at is not None:
                    paid_status = pgettext('invoice', 'Cancelled')
                elif line.remaining_amount > 0 and line.paid_amount > 0:
                    paid_status = _('Partially paid')
                elif line.remaining_amount == 0:
                    paid_status = _('Paid')
                payment_types = {p.payment.payment_type.label for p in line.invoicelinepayment_set.all()}
                writer.writerow(
                    [
                        invoice.formatted_number,
                        invoice.get_origin_display(),
                        invoice.payer_external_id,
                        invoice.payer_first_name,
                        invoice.payer_last_name,
                        invoice.payer_email,
                        invoice.payer_phone,
                        invoice.created_at.date(),
                        invoice.date_invoicing,
                        invoice.date_publication,
                        invoice.date_payment_deadline,
                        invoice.date_due,
                        invoice.payer_direct_debit,
                        line.label,
                        line.accounting_code,
                        line.unit_amount,
                        line.quantity,
                        line.total_amount,
                        ', '.join(sorted(payment_types)),
                        line.paid_amount,
                        paid_status,
                        invoice.cancelled_at,
                        invoice.cancellation_reason,
                    ]
                )

        writer.save(response)
        return response


regie_invoice_list = RegieInvoiceListView.as_view()


class RegieInvoicePDFView(CanBeViewedCheckMixin, PDFMixin, DetailView):
    pk_url_kwarg = 'invoice_pk'
    model = Invoice

    def dispatch(self, request, *args, **kwargs):
        self.regie = get_object_or_404(Regie, pk=kwargs['regie_pk'])
        self.check_object(self.regie)
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return super().get_queryset().filter(regie=self.regie).exclude(pool__campaign__finalized=False)


regie_invoice_pdf = RegieInvoicePDFView.as_view()


class RegieInvoiceDynamicPDFView(RegieInvoicePDFView):
    def html(self):
        return self.object.html(dynamic=True)

    def get_filename(self):
        return '%s-dynamic' % self.object.formatted_number


regie_invoice_dynamic_pdf = RegieInvoiceDynamicPDFView.as_view()


class RegieInvoicePaymentsPDFView(CanBeViewedCheckMixin, PDFMixin, DetailView):
    pk_url_kwarg = 'invoice_pk'
    model = Invoice

    def dispatch(self, request, *args, **kwargs):
        self.regie = get_object_or_404(Regie, pk=kwargs['regie_pk'])
        self.check_object(self.regie)
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .filter(
                regie=self.regie,
                remaining_amount=0,
            )
            .exclude(pool__campaign__finalized=False)
        )

    def html(self):
        return self.object.payments_html()

    def get_filename(self):
        return 'A-%s' % self.object.formatted_number


regie_invoice_payments_pdf = RegieInvoicePaymentsPDFView.as_view()


class RegieInvoiceLineListView(CanBeViewedCheckMixin, ListView):
    template_name = 'lingo/invoicing/manager_invoice_lines.html'

    def dispatch(self, request, *args, **kwargs):
        self.regie = get_object_or_404(Regie, pk=kwargs['regie_pk'])
        self.check_object(self.regie)
        self.invoice = get_object_or_404(
            Invoice.objects.exclude(pool__campaign__finalized=False),
            pk=kwargs['invoice_pk'],
            regie=self.regie,
        )
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return self.invoice.get_grouped_and_ordered_lines()

    def get_context_data(self, **kwargs):
        kwargs['regie'] = self.regie
        kwargs['invoice'] = self.invoice
        kwargs['user_can_invoice'] = self.regie.can_be_invoiced(self.request.user)
        return super().get_context_data(**kwargs)


regie_invoice_line_list = RegieInvoiceLineListView.as_view()


class RegieInvoiceCancelView(CanBeInvoicedCheckMixin, UpdateView):
    template_name = 'lingo/invoicing/manager_invoice_cancel_form.html'
    pk_url_kwarg = 'invoice_pk'
    model = Invoice
    form_class = RegieInvoiceCancelForm

    def dispatch(self, request, *args, **kwargs):
        self.regie = get_object_or_404(Regie, pk=kwargs['regie_pk'])
        self.check_object(self.regie)
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .filter(
                regie=self.regie,
                cancelled_at__isnull=True,
                collection__isnull=True,
            )
            .exclude(pk__in=InvoiceLine.objects.filter(invoicelinepayment__isnull=False).values('invoice'))
            .exclude(pool__campaign__finalized=False)
        )

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['request'] = self.request
        return kwargs

    def get_context_data(self, **kwargs):
        kwargs['regie'] = self.regie
        return super().get_context_data(**kwargs)

    def get_success_url(self):
        return '%s?number=%s' % (
            reverse('lingo-manager-invoicing-regie-invoice-list', args=[self.regie.pk]),
            self.object.formatted_number,
        )


regie_invoice_cancel = RegieInvoiceCancelView.as_view()


class RegieInvoiceEditDatesView(CanBeInvoicedCheckMixin, UpdateView):
    template_name = 'lingo/invoicing/manager_invoice_dates_form.html'
    pk_url_kwarg = 'invoice_pk'
    model = Invoice
    form_class = RegieInvoiceDatesForm

    def dispatch(self, request, *args, **kwargs):
        self.regie = get_object_or_404(Regie, pk=kwargs['regie_pk'])
        self.check_object(self.regie)
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .filter(
                regie=self.regie,
                cancelled_at__isnull=True,
                collection__isnull=True,
            )
            .exclude(pool__campaign__finalized=False)
        )

    def get_context_data(self, **kwargs):
        kwargs['regie'] = self.regie
        return super().get_context_data(**kwargs)

    def get_success_url(self):
        return '%s?number=%s' % (
            reverse('lingo-manager-invoicing-regie-invoice-list', args=[self.regie.pk]),
            self.object.formatted_number,
        )


regie_invoice_edit_dates = RegieInvoiceEditDatesView.as_view()


class RegieCollectionInvoiceListView(CanBeViewedCheckMixin, ListView):
    template_name = 'lingo/invoicing/manager_collection_invoice_list.html'
    paginate_by = 100

    def dispatch(self, request, *args, **kwargs):
        self.regie = get_object_or_404(Regie, pk=kwargs['regie_pk'])
        self.check_object(self.regie)
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        self.filterset = RegieCollectionInvoiceFilterSet(
            data=self.request.GET or None,
            queryset=Invoice.objects.filter(
                regie=self.regie,
                collection__isnull=True,
                cancelled_at__isnull=True,
                remaining_amount__gt=0,
            )
            .exclude(pool__campaign__finalized=False)
            .order_by('payer_last_name', 'payer_first_name', 'payer_external_id', '-created_at'),
            regie=self.regie,
        )
        if not self.filterset.form.is_valid():
            return Invoice.objects.none()
        return self.filterset.qs

    def get_context_data(self, **kwargs):
        kwargs['regie'] = self.regie
        kwargs['filterset'] = self.filterset
        kwargs['has_draft'] = self.regie.collectiondocket_set.filter(draft=True).exists()
        kwargs['user_can_invoice'] = self.regie.can_be_invoiced(self.request.user)
        return super().get_context_data(**kwargs)


regie_collection_invoice_list = RegieCollectionInvoiceListView.as_view()


class RegieCollectionListView(CanBeViewedCheckMixin, ListView):
    template_name = 'lingo/invoicing/manager_collection_list.html'
    model = CollectionDocket
    paginate_by = 100

    def dispatch(self, request, *args, **kwargs):
        self.regie = get_object_or_404(Regie, pk=kwargs['regie_pk'])
        self.check_object(self.regie)
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        paid_amounts = (
            InvoiceLinePayment.objects.filter(
                line__invoice__collection=OuterRef('pk'),
                payment__payment_type__slug='collect',
            )
            .annotate(paid_amount=Func(F('amount'), function='Sum'))
            .values('paid_amount')
        )
        return (
            self.regie.collectiondocket_set.all()
            .annotate(
                count=Count('invoice'),
                remaining_amount=Sum('invoice__remaining_amount'),
                paid_amount=Subquery(paid_amounts),
            )
            .order_by('-created_at')
        )

    def get_context_data(self, **kwargs):
        kwargs['regie'] = self.regie
        return super().get_context_data(**kwargs)


regie_collection_list = RegieCollectionListView.as_view()


class RegieCollectionAddView(CanBeInvoicedCheckMixin, CreateView):
    template_name = 'lingo/invoicing/manager_collection_form.html'
    model = CollectionDocket
    form_class = CollectionDocketForm

    def dispatch(self, request, *args, **kwargs):
        self.regie = get_object_or_404(Regie, pk=kwargs['regie_pk'])
        self.check_object(self.regie)
        if self.regie.collectiondocket_set.filter(draft=True).exists():
            raise Http404
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        kwargs['regie'] = self.regie
        return super().get_context_data(**kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        if not kwargs.get('instance'):
            kwargs['instance'] = self.model()
        kwargs['instance'].regie = self.regie
        kwargs['instance'].draft = True
        kwargs['regie'] = self.regie
        return kwargs

    def get(self, request, *args, **kwargs):
        form = self.get_form_class()(
            data=(
                {
                    'date_end': request.GET.get('date_end'),
                    'minimum_threshold': request.GET.get('minimum_threshold'),
                }
                if request.GET
                else None
            ),
            **self.get_form_kwargs(),
        )
        if form.is_valid():
            return self.form_valid(form)
        return super().get(request, *args, **kwargs)

    def get_success_url(self):
        return reverse(
            'lingo-manager-invoicing-regie-collection-detail', args=[self.regie.pk, self.object.pk]
        )


regie_collection_add = RegieCollectionAddView.as_view()


class RegieCollectionDetailView(CanBeViewedCheckMixin, DetailView):
    template_name = 'lingo/invoicing/manager_collection_detail.html'
    model = CollectionDocket

    def dispatch(self, request, *args, **kwargs):
        self.regie = get_object_or_404(Regie, pk=kwargs['regie_pk'])
        self.check_object(self.regie)
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return self.regie.collectiondocket_set.all()

    def get_context_data(self, **kwargs):
        kwargs['regie'] = self.regie
        kwargs['invoices'] = self.object.get_invoices()
        kwargs['user_can_invoice'] = self.regie.can_be_invoiced(self.request.user)
        return super().get_context_data(**kwargs)


regie_collection_detail = RegieCollectionDetailView.as_view()


class RegieCollectionEditView(CanBeInvoicedCheckMixin, UpdateView):
    template_name = 'lingo/invoicing/manager_collection_form.html'
    model = CollectionDocket
    form_class = CollectionDocketForm

    def dispatch(self, request, *args, **kwargs):
        self.regie = get_object_or_404(Regie, pk=kwargs['regie_pk'])
        self.check_object(self.regie)
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return self.regie.collectiondocket_set.filter(draft=True)

    def get_context_data(self, **kwargs):
        kwargs['regie'] = self.regie
        return super().get_context_data(**kwargs)

    def get_success_url(self):
        return reverse(
            'lingo-manager-invoicing-regie-collection-detail', args=[self.regie.pk, self.object.pk]
        )


regie_collection_edit = RegieCollectionEditView.as_view()


class RegieCollectionValidateView(CanBeInvoicedCheckMixin, FormView):
    template_name = 'lingo/invoicing/manager_collection_validate.html'

    def dispatch(self, request, *args, **kwargs):
        self.regie = get_object_or_404(Regie, pk=kwargs['regie_pk'])
        self.check_object(self.regie)
        self.object = get_object_or_404(
            CollectionDocket,
            regie=self.regie,
            pk=kwargs['pk'],
            draft=True,
        )
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        kwargs['form'] = None
        kwargs['regie'] = self.regie
        kwargs['object'] = self.object
        return super().get_context_data(**kwargs)

    def post(self, request, *args, **kwargs):
        with transaction.atomic():
            self.object.draft = False
            self.object.set_number()
            self.object.save()
            invoices = list(self.object.invoice_set.all().order_by('pk'))
            if self.object.pay_invoices and invoices:
                amount = sum(i.remaining_amount for i in invoices)
                payment_type, dummy = PaymentType.objects.get_or_create(
                    regie=self.regie, slug='collect', defaults={'label': _('Collect')}
                )
                Payment.make_payment(
                    regie=self.object.regie,
                    invoices=invoices,
                    amount=amount,
                    payment_type=payment_type,
                )

        return redirect(
            reverse('lingo-manager-invoicing-regie-collection-detail', args=[self.regie.pk, self.object.pk])
        )


regie_collection_validate = RegieCollectionValidateView.as_view()


class RegieCollectionDeleteView(CanBeInvoicedCheckMixin, DeleteView):
    template_name = 'lingo/manager_confirm_delete.html'
    model = CollectionDocket

    def dispatch(self, request, *args, **kwargs):
        self.regie = get_object_or_404(Regie, pk=kwargs['regie_pk'])
        self.check_object(self.regie)
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return self.regie.collectiondocket_set.filter(draft=True)

    def form_valid(self, form):
        self.object = self.get_object()
        self.object.invoice_set.update(collection=None)
        return super().form_valid(form)

    def get_success_url(self):
        return reverse('lingo-manager-invoicing-regie-collection-list', args=[self.regie.pk])


regie_collection_delete = RegieCollectionDeleteView.as_view()


class RegiePaymentListView(CanBeViewedCheckMixin, ListView):
    template_name = 'lingo/invoicing/manager_payment_list.html'
    paginate_by = 100

    def dispatch(self, request, *args, **kwargs):
        self.regie = get_object_or_404(Regie, pk=kwargs['regie_pk'])
        self.check_object(self.regie)
        if 'ods' in request.GET and not self.regie.can_be_controlled(self.request.user):
            raise PermissionDenied()
        self.full = bool('full' in request.GET)
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        invoice_line_payment_queryset = InvoiceLinePayment.objects.select_related('line__invoice').order_by(
            'created_at'
        )
        self.filterset = RegiePaymentFilterSet(
            data=self.request.GET or None,
            queryset=Payment.objects.filter(regie=self.regie)
            .prefetch_related(
                'payment_type',
                'cancellation_reason',
                'cancelled_by',
                Prefetch(
                    'invoicelinepayment_set',
                    queryset=invoice_line_payment_queryset,
                    to_attr='prefetched_invoicelinepayments',
                ),
                'docket',
            )
            .order_by('-created_at'),
            regie=self.regie,
        )
        return self.filterset.qs

    def get_context_data(self, **kwargs):
        kwargs['regie'] = self.regie
        kwargs['filterset'] = self.filterset
        kwargs['user_can_control'] = self.regie.can_be_controlled(self.request.user)
        return super().get_context_data(**kwargs)

    def get(self, request, *args, **kwargs):
        self.object_list = self.get_queryset()
        context = self.get_context_data()
        if 'ods' in request.GET and self.filterset.form.is_valid():
            return self.ods(request, context)
        return self.render_to_response(context)

    def ods(self, request, context):
        response = HttpResponse(content_type='application/vnd.oasis.opendocument.spreadsheet')
        if self.full:
            response['Content-Disposition'] = 'attachment; filename="payments_full.ods"'
        else:
            response['Content-Disposition'] = 'attachment; filename="payments.ods"'
        writer = Workbook()
        # headers
        headers = [
            _('Number'),
            _('Invoice number'),
            _('Creation date'),
            _('Payment date'),
            _('Payer ID'),
            _('Payer first name'),
            _('Payer last name'),
        ]
        if self.full:
            headers += [
                _('Description (line)'),
                _('Accounting code (line)'),
                _('Amount (line)'),
                _('Quantity (line)'),
                _('Subtotal (line)'),
                _('Amount assigned (line)'),
            ]
        headers += (
            [
                _('Payment type'),
                _('Credit numbers'),
                _('Amount assigned (invoice)'),
                _('Total amount (payment)'),
            ]
            + [v for k, v in PAYMENT_INFO]
            + [
                _('Debt reference'),
                _('Cancelled on'),
                _('Cancellation reason'),
            ]
        )
        writer.writerow(headers, headers=True)
        for payment in self.object_list:
            invoice_payments = payment.get_invoice_payments()
            for invoice_payment in invoice_payments:
                if not self.full:
                    writer.writerow(
                        [
                            payment.formatted_number,
                            invoice_payment.invoice.formatted_number,
                            localtime(payment.created_at).date(),
                            payment.date_payment,
                            payment.payer_external_id,
                            payment.payer_first_name,
                            payment.payer_last_name,
                            payment.payment_type.label,
                            ', '.join(invoice_payment.invoice.credit_formatted_numbers),
                            invoice_payment.amount,
                            payment.amount,
                        ]
                        + [payment.payment_info.get(k) for k, v in PAYMENT_INFO]
                        + [
                            payment.bank_data.get('refdet'),
                            payment.cancelled_at,
                            payment.cancellation_reason,
                        ]
                    )
                    continue
                for invoice_payment_line in invoice_payment.lines:
                    writer.writerow(
                        [
                            payment.formatted_number,
                            invoice_payment.invoice.formatted_number,
                            localtime(payment.created_at).date(),
                            payment.date_payment,
                            payment.payer_external_id,
                            payment.payer_first_name,
                            payment.payer_last_name,
                            invoice_payment_line.line.label,
                            invoice_payment_line.line.accounting_code,
                            invoice_payment_line.line.unit_amount,
                            invoice_payment_line.line.quantity,
                            invoice_payment_line.line.total_amount,
                            invoice_payment_line.amount,
                            payment.payment_type.label,
                            ', '.join(invoice_payment.invoice.credit_formatted_numbers),
                            invoice_payment.amount,
                            payment.amount,
                        ]
                        + [payment.payment_info.get(k) for k, v in PAYMENT_INFO]
                        + [
                            payment.bank_data.get('refdet'),
                            payment.cancelled_at,
                            payment.cancellation_reason,
                        ]
                    )
            if not invoice_payments:
                row = [
                    payment.formatted_number,
                    '',
                    localtime(payment.created_at).date(),
                    payment.date_payment,
                    payment.payer_external_id,
                    payment.payer_first_name,
                    payment.payer_last_name,
                ]
                if self.full:
                    row += ['', '', '', '', '', '']
                row += [
                    payment.payment_type.label,
                    '',
                    '',
                    payment.amount,
                ]
                row += [payment.payment_info.get(k) for k, v in PAYMENT_INFO]
                row += [
                    payment.bank_data.get('refdet'),
                    payment.cancelled_at,
                    payment.cancellation_reason,
                ]
                writer.writerow(row)

        writer.save(response)
        return response


regie_payment_list = RegiePaymentListView.as_view()


class RegiePaymentPDFView(CanBeViewedCheckMixin, PDFMixin, DetailView):
    pk_url_kwarg = 'payment_pk'
    model = Payment

    def dispatch(self, request, *args, **kwargs):
        self.regie = get_object_or_404(Regie, pk=kwargs['regie_pk'])
        self.check_object(self.regie)
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return super().get_queryset().filter(regie=self.regie)


regie_payment_pdf = RegiePaymentPDFView.as_view()


class RegiePaymentCancelView(CanBeControlledCheckMixin, UpdateView):
    template_name = 'lingo/invoicing/manager_payment_cancel_form.html'
    pk_url_kwarg = 'payment_pk'
    model = Payment
    form_class = RegiePaymentCancelForm

    def dispatch(self, request, *args, **kwargs):
        self.regie = get_object_or_404(Regie, pk=kwargs['regie_pk'])
        self.check_object(self.regie)
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .filter(
                regie=self.regie,
                cancelled_at__isnull=True,
            )
            .exclude(invoicelinepayment__line__invoice__collection__isnull=False)
        )

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['request'] = self.request
        return kwargs

    def get_context_data(self, **kwargs):
        kwargs['regie'] = self.regie
        return super().get_context_data(**kwargs)

    def get_success_url(self):
        return '%s?number=%s' % (
            reverse('lingo-manager-invoicing-regie-payment-list', args=[self.regie.pk]),
            self.object.formatted_number,
        )


regie_payment_cancel = RegiePaymentCancelView.as_view()


class RegieDocketPaymentListView(CanBeViewedCheckMixin, ListView):
    template_name = 'lingo/invoicing/manager_docket_payment_list.html'
    paginate_by = 100

    def dispatch(self, request, *args, **kwargs):
        self.regie = get_object_or_404(Regie, pk=kwargs['regie_pk'])
        self.check_object(self.regie)
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        self.filterset = RegieDocketPaymentFilterSet(
            data=self.request.GET or None,
            queryset=Payment.objects.filter(regie=self.regie, docket__isnull=True, cancelled_at__isnull=True)
            .select_related('payment_type')
            .order_by('-created_at'),
            regie=self.regie,
        )
        if not self.filterset.form.is_valid():
            return Payment.objects.none()
        return self.filterset.qs

    def get_context_data(self, **kwargs):
        kwargs['regie'] = self.regie
        kwargs['filterset'] = self.filterset
        kwargs['has_draft'] = self.regie.paymentdocket_set.filter(draft=True).exists()
        kwargs['user_can_control'] = self.regie.can_be_controlled(self.request.user)
        return super().get_context_data(**kwargs)


regie_docket_payment_list = RegieDocketPaymentListView.as_view()


class RegieDocketListView(CanBeViewedCheckMixin, ListView):
    template_name = 'lingo/invoicing/manager_docket_list.html'
    model = PaymentDocket
    paginate_by = 100

    def dispatch(self, request, *args, **kwargs):
        self.regie = get_object_or_404(Regie, pk=kwargs['regie_pk'])
        self.check_object(self.regie)
        if ('ods' in request.GET or 'pdf' in request.GET) and not self.regie.can_be_controlled(
            self.request.user
        ):
            raise PermissionDenied()
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        payments = Payment.objects.filter(docket=OuterRef('pk')).order_by().values('docket')
        active_count = (
            payments.filter(cancelled_at__isnull=True).annotate(count=Count('docket')).values('count')
        )
        active_amount = (
            payments.filter(cancelled_at__isnull=True).annotate(total=Sum('amount')).values('total')
        )
        cancelled_count = (
            payments.filter(cancelled_at__isnull=False).annotate(count=Count('docket')).values('count')
        )
        cancelled_amount = (
            payments.filter(cancelled_at__isnull=False).annotate(total=Sum('amount')).values('total')
        )
        queryset = (
            self.regie.paymentdocket_set.all()
            .prefetch_related('payment_types')
            .annotate(
                active_count=Coalesce(Subquery(active_count, output_field=IntegerField()), Value(0)),
                cancelled_count=Coalesce(Subquery(cancelled_count, output_field=IntegerField()), Value(0)),
                active_amount=Coalesce(
                    Subquery(active_amount, output_field=DecimalField(max_digits=9, decimal_places=2)),
                    Value(0),
                    output_field=DecimalField(max_digits=9, decimal_places=2),
                ),
                cancelled_amount=Coalesce(
                    Subquery(cancelled_amount, output_field=DecimalField(max_digits=9, decimal_places=2)),
                    Value(0),
                    output_field=DecimalField(max_digits=9, decimal_places=2),
                ),
            )
            .order_by('-created_at')
        )
        self.filterset = RegieDocketFilterSet(data=self.request.GET or None, queryset=queryset)
        return self.filterset.qs

    def get_context_data(self, **kwargs):
        kwargs['regie'] = self.regie
        kwargs['filterset'] = self.filterset
        kwargs['user_can_control'] = self.regie.can_be_controlled(self.request.user)
        return super().get_context_data(**kwargs)

    def get(self, request, *args, **kwargs):
        self.object_list = self.get_queryset()
        context = self.get_context_data()
        if 'ods' in request.GET:
            return self.ods(request, context)
        if 'pdf' in request.GET:
            return self.pdf(request, context)
        return self.render_to_response(context)

    def get_filename(self, ext):
        dates = []
        date_end = self.filterset.form.cleaned_data.get('date_end')
        if date_end:
            if date_end.start:
                dates.append(date_end.start.strftime('%Y%m%d'))
            if date_end.stop:
                dates.append(date_end.stop.strftime('%Y%m%d'))
        return 'dockets%s.%s' % ('-%s' % '-'.join(dates) if dates else '', ext)

    def ods(self, request, context):
        response = HttpResponse(content_type='application/vnd.oasis.opendocument.spreadsheet')
        response['Content-Disposition'] = 'attachment; filename="%s"' % self.get_filename('ods')
        writer = Workbook()
        writer.writerow(
            [
                _('Initial amount'),
                _('Cancelled amount'),
                _('Final amount'),
            ],
            headers=True,
        )
        cancelled = self.get_cancelled_payments()
        writer.writerow(
            [
                self.get_payments_amount(),
                cancelled['amount'],
                self.get_active_payments_amount(),
            ]
        )
        for value in self.get_active_payments():
            if not value['list']:
                continue
            writer.writerow(
                [
                    _('Number of payments'),
                    _('Total amount'),
                    _('Payment type'),
                ],
                headers=True,
            )
            writer.writerow(
                [
                    len(value['list']),
                    value['amount'],
                    value['payment_type'],
                ]
            )
            headers = [
                _('Docket'),
                _('Number'),
                _('Date'),
                _('Payer ID'),
                _('Payer first name'),
                _('Payer last name'),
                _('Payment type'),
                _('Total amount'),
            ] + [v for k, v in PAYMENT_INFO]
            writer.writerow(headers, headers=True)
            for payment in value['list']:
                writer.writerow(
                    [
                        str(payment.docket),
                        payment.formatted_number,
                        localtime(payment.created_at).date(),
                        payment.payer_external_id,
                        payment.payer_first_name,
                        payment.payer_last_name,
                        payment.payment_type.label,
                        payment.amount,
                    ]
                    + [payment.payment_info.get(k) for k, v in PAYMENT_INFO]
                )
        if cancelled['list']:
            writer.writerow(
                [
                    _('Number of payments'),
                    _('Total amount'),
                    _('Cancelled payments'),
                ],
                headers=True,
            )
            writer.writerow(
                [
                    len(cancelled['list']),
                    cancelled['amount'],
                ]
            )
            headers = (
                [
                    _('Docket'),
                    _('Number'),
                    _('Date'),
                    _('Payer ID'),
                    _('Payer first name'),
                    _('Payer last name'),
                    _('Payment type'),
                    _('Total amount'),
                ]
                + [v for k, v in PAYMENT_INFO]
                + [
                    _('Cancelled on'),
                    _('Cancellation reason'),
                ]
            )
            writer.writerow(headers, headers=True)
            for payment in cancelled['list']:
                writer.writerow(
                    [
                        str(payment.docket),
                        payment.formatted_number,
                        localtime(payment.created_at).date(),
                        payment.payer_external_id,
                        payment.payer_first_name,
                        payment.payer_last_name,
                        payment.payment_type.label,
                        payment.amount,
                    ]
                    + [payment.payment_info.get(k) for k, v in PAYMENT_INFO]
                    + [
                        payment.cancelled_at,
                        payment.cancellation_reason,
                    ]
                )
        writer.save(response)
        return response

    def pdf(self, request, context):
        result = self.html()
        if 'html' in request.GET:
            return HttpResponse(result)
        html = HTML(string=result)
        pdf = write_pdf(html)
        response = HttpResponse(pdf, content_type='application/pdf')
        if 'inline' not in request.GET:
            response['Content-Disposition'] = 'attachment; filename="%s"' % self.get_filename('pdf')
        return response

    def html(self):
        template = get_template('lingo/invoicing/dockets.html')
        title = _('List of the payments of all dockets')
        date_end = self.filterset.form.cleaned_data.get('date_end')
        if date_end:
            if date_end.start and date_end.stop:
                title = _('List of the payments of dockets from %(start)s to %(stop)s') % {
                    'start': date_end.start.strftime('%d/%m/%Y'),
                    'stop': date_end.stop.strftime('%d/%m/%Y'),
                }
            elif date_end.start:
                title = _('List of the payments of dockets from %(start)s') % {
                    'start': date_end.start.strftime('%d/%m/%Y')
                }
            elif date_end.stop:
                title = _('List of the payments of dockets to %(stop)s') % {
                    'stop': date_end.stop.strftime('%d/%m/%Y')
                }
        context = {
            'regie': self.regie,
            'object_list': self.object_list,
            'pdf_title': title,
            'active': self.get_active_payments(),
            'cancelled': self.get_cancelled_payments(),
            'payments_amount': self.get_payments_amount(),
            'active_payments_amount': self.get_active_payments_amount(),
            'payment_info': PAYMENT_INFO,
        }
        return template.render(context)

    def get_active_payments(self):
        result = []
        payment_types = set()
        for docket in self.object_list:
            payment_types.update(docket.payment_types.all())
        for payment_type in sorted(payment_types, key=lambda a: a.label):
            qs = Payment.objects.filter(
                docket__in=self.object_list, payment_type=payment_type, cancelled_at__isnull=True
            ).select_related(
                'payment_type',
                'docket',
            )
            result.append(
                {
                    'payment_type': payment_type,
                    'list': qs.order_by('-created_at'),
                    'amount': qs.aggregate(amount=Sum('amount'))['amount'],
                }
            )
        return result

    def get_cancelled_payments(self):
        qs = Payment.objects.filter(docket__in=self.object_list, cancelled_at__isnull=False).select_related(
            'payment_type', 'docket', 'cancellation_reason'
        )
        return {'list': qs.order_by('-created_at'), 'amount': qs.aggregate(amount=Sum('amount'))['amount']}

    def get_payments_amount(self):
        qs = Payment.objects.filter(docket__in=self.object_list).aggregate(amount=Sum('amount'))
        return qs['amount']

    def get_active_payments_amount(self):
        qs = Payment.objects.filter(docket__in=self.object_list, cancelled_at__isnull=True).aggregate(
            amount=Sum('amount')
        )
        return qs['amount']


regie_docket_list = RegieDocketListView.as_view()


class RegieDocketAddView(CanBeControlledCheckMixin, CreateView):
    template_name = 'lingo/invoicing/manager_docket_form.html'
    model = PaymentDocket
    form_class = PaymentDocketForm

    def dispatch(self, request, *args, **kwargs):
        self.regie = get_object_or_404(Regie, pk=kwargs['regie_pk'])
        self.check_object(self.regie)
        if self.regie.paymentdocket_set.filter(draft=True).exists():
            raise Http404
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        kwargs['regie'] = self.regie
        return super().get_context_data(**kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        if not kwargs.get('instance'):
            kwargs['instance'] = self.model()
        kwargs['instance'].regie = self.regie
        kwargs['instance'].draft = True
        kwargs['regie'] = self.regie
        return kwargs

    def get(self, request, *args, **kwargs):
        form = self.get_form_class()(
            data=(
                {
                    'payment_types': request.GET.getlist('payment_type'),
                    'date_end': request.GET.get('date_end'),
                }
                if request.GET
                else None
            ),
            **self.get_form_kwargs(),
        )
        if form.is_valid():
            return self.form_valid(form)
        return super().get(request, *args, **kwargs)

    def get_success_url(self):
        return reverse('lingo-manager-invoicing-regie-docket-detail', args=[self.regie.pk, self.object.pk])


regie_docket_add = RegieDocketAddView.as_view()


class RegieDocketDetailView(CanBeViewedCheckMixin, DetailView):
    template_name = 'lingo/invoicing/manager_docket_detail.html'
    model = PaymentDocket

    def dispatch(self, request, *args, **kwargs):
        self.regie = get_object_or_404(Regie, pk=kwargs['regie_pk'])
        self.check_object(self.regie)
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return self.regie.paymentdocket_set.all()

    def get_context_data(self, **kwargs):
        kwargs['regie'] = self.regie
        kwargs['active'] = self.object.get_active_payments()
        kwargs['cancelled'] = self.object.get_cancelled_payments()
        kwargs['user_can_control'] = self.regie.can_be_controlled(self.request.user)
        return super().get_context_data(**kwargs)


regie_docket_detail = RegieDocketDetailView.as_view()


class RegieDocketODSView(CanBeControlledCheckMixin, DetailView):
    model = PaymentDocket

    def dispatch(self, request, *args, **kwargs):
        self.regie = get_object_or_404(Regie, pk=kwargs['regie_pk'])
        self.check_object(self.regie)
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return self.regie.paymentdocket_set.all()

    def get(self, request, *args, **kwargs):
        self.object = self.get_object()
        response = HttpResponse(content_type='application/vnd.oasis.opendocument.spreadsheet')
        response['Content-Disposition'] = 'attachment; filename="docket-%s.ods"' % self.object
        writer = Workbook()
        writer.writerow(
            [
                _('Initial docket amount'),
                _('Cancelled docket amount'),
                _('Final docket amount'),
            ],
            headers=True,
        )
        cancelled = self.object.get_cancelled_payments()
        writer.writerow(
            [
                self.object.get_payments_amount(),
                cancelled['amount'],
                self.object.get_active_payments_amount(),
            ]
        )
        for value in self.object.get_active_payments():
            if not value['list']:
                continue
            writer.writerow(
                [
                    _('Number of payments'),
                    _('Total amount'),
                    _('Payment type'),
                    _('Additional information'),
                ],
                headers=True,
            )
            writer.writerow(
                [
                    len(value['list']),
                    value['amount'],
                    value['payment_type'],
                    self.object.payment_types_info.get(value['payment_type'].slug),
                ]
            )
            headers = [
                _('Number'),
                _('Date'),
                _('Payer ID'),
                _('Payer first name'),
                _('Payer last name'),
                _('Payment type'),
                _('Total amount'),
            ] + [v for k, v in PAYMENT_INFO]
            writer.writerow(headers, headers=True)
            for payment in value['list']:
                writer.writerow(
                    [
                        payment.formatted_number,
                        localtime(payment.created_at).date(),
                        payment.payer_external_id,
                        payment.payer_first_name,
                        payment.payer_last_name,
                        payment.payment_type.label,
                        payment.amount,
                    ]
                    + [payment.payment_info.get(k) for k, v in PAYMENT_INFO]
                )
        if cancelled['list']:
            writer.writerow(
                [
                    _('Number of payments'),
                    _('Total amount'),
                    _('Cancelled payments'),
                ],
                headers=True,
            )
            writer.writerow(
                [
                    len(cancelled['list']),
                    cancelled['amount'],
                ]
            )
            headers = (
                [
                    _('Number'),
                    _('Date'),
                    _('Payer ID'),
                    _('Payer first name'),
                    _('Payer last name'),
                    _('Payment type'),
                    _('Total amount'),
                ]
                + [v for k, v in PAYMENT_INFO]
                + [
                    _('Cancelled on'),
                    _('Cancellation reason'),
                ]
            )
            writer.writerow(headers, headers=True)
            for payment in cancelled['list']:
                writer.writerow(
                    [
                        payment.formatted_number,
                        localtime(payment.created_at).date(),
                        payment.payer_external_id,
                        payment.payer_first_name,
                        payment.payer_last_name,
                        payment.payment_type.label,
                        payment.amount,
                    ]
                    + [payment.payment_info.get(k) for k, v in PAYMENT_INFO]
                    + [
                        payment.cancelled_at,
                        payment.cancellation_reason,
                    ]
                )
        writer.save(response)
        return response


regie_docket_ods = RegieDocketODSView.as_view()


class RegieDocketPDFView(CanBeControlledCheckMixin, PDFMixin, DetailView):
    model = PaymentDocket

    def dispatch(self, request, *args, **kwargs):
        self.regie = get_object_or_404(Regie, pk=kwargs['regie_pk'])
        self.check_object(self.regie)
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return self.regie.paymentdocket_set.all()

    def get_filename(self):
        return self.object


regie_docket_pdf = RegieDocketPDFView.as_view()


class RegieDocketEditView(CanBeControlledCheckMixin, UpdateView):
    template_name = 'lingo/invoicing/manager_docket_form.html'
    model = PaymentDocket
    form_class = PaymentDocketForm

    def dispatch(self, request, *args, **kwargs):
        self.regie = get_object_or_404(Regie, pk=kwargs['regie_pk'])
        self.check_object(self.regie)
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return self.regie.paymentdocket_set.filter(draft=True)

    def get_context_data(self, **kwargs):
        kwargs['regie'] = self.regie
        return super().get_context_data(**kwargs)

    def get_success_url(self):
        return reverse('lingo-manager-invoicing-regie-docket-detail', args=[self.regie.pk, self.object.pk])


regie_docket_edit = RegieDocketEditView.as_view()


class RegieDocketPaymentTypeEditView(CanBeControlledCheckMixin, UpdateView):
    template_name = 'lingo/invoicing/manager_docket_payment_type_form.html'
    model = PaymentDocket
    form_class = PaymentDocketPaymentTypeForm

    def dispatch(self, request, *args, **kwargs):
        self.regie = get_object_or_404(Regie, pk=kwargs['regie_pk'])
        self.check_object(self.regie)
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return self.regie.paymentdocket_set.filter(draft=True)

    def get_context_data(self, **kwargs):
        kwargs['regie'] = self.regie
        return super().get_context_data(**kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['payment_type'] = get_object_or_404(
            self.object.payment_types, pk=self.kwargs['payment_type_pk']
        )
        return kwargs

    def get_success_url(self):
        return reverse('lingo-manager-invoicing-regie-docket-detail', args=[self.regie.pk, self.object.pk])


regie_docket_payment_type_edit = RegieDocketPaymentTypeEditView.as_view()


class RegieDocketValidateView(CanBeControlledCheckMixin, FormView):
    template_name = 'lingo/invoicing/manager_docket_validate.html'

    def dispatch(self, request, *args, **kwargs):
        self.regie = get_object_or_404(Regie, pk=kwargs['regie_pk'])
        self.check_object(self.regie)
        self.object = get_object_or_404(
            PaymentDocket,
            regie=self.regie,
            pk=kwargs['pk'],
            draft=True,
        )
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        kwargs['form'] = None
        kwargs['regie'] = self.regie
        kwargs['object'] = self.object
        return super().get_context_data(**kwargs)

    def post(self, request, *args, **kwargs):
        self.object.draft = False
        self.object.set_number()
        self.object.save()
        return redirect(
            reverse('lingo-manager-invoicing-regie-docket-detail', args=[self.regie.pk, self.object.pk])
        )


regie_docket_validate = RegieDocketValidateView.as_view()


class RegieDocketDeleteView(CanBeControlledCheckMixin, DeleteView):
    template_name = 'lingo/manager_confirm_delete.html'
    model = PaymentDocket

    def dispatch(self, request, *args, **kwargs):
        self.regie = get_object_or_404(Regie, pk=kwargs['regie_pk'])
        self.check_object(self.regie)
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return self.regie.paymentdocket_set.filter(draft=True)

    def form_valid(self, form):
        self.object = self.get_object()
        self.object.payment_set.update(docket=None)
        return super().form_valid(form)

    def get_success_url(self):
        return reverse('lingo-manager-invoicing-regie-docket-list', args=[self.regie.pk])


regie_docket_delete = RegieDocketDeleteView.as_view()


class RegieCreditListView(CanBeViewedCheckMixin, ListView):
    template_name = 'lingo/invoicing/manager_credit_list.html'
    paginate_by = 100

    def dispatch(self, request, *args, **kwargs):
        self.regie = get_object_or_404(Regie, pk=kwargs['regie_pk'])
        self.check_object(self.regie)
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        credit_queryset = Credit.objects.filter(regie=self.regie).order_by('-created_at')
        self.filterset = RegieCreditFilterSet(data=self.request.GET or None, queryset=credit_queryset)
        return self.filterset.qs

    def get_context_data(self, **kwargs):
        kwargs['regie'] = self.regie
        kwargs['filterset'] = self.filterset
        return super().get_context_data(**kwargs)


regie_credit_list = RegieCreditListView.as_view()


class RegieCreditLineListView(CanBeViewedCheckMixin, ListView):
    template_name = 'lingo/invoicing/manager_credit_lines.html'

    def dispatch(self, request, *args, **kwargs):
        self.regie = get_object_or_404(Regie, pk=kwargs['regie_pk'])
        self.check_object(self.regie)
        self.credit = get_object_or_404(
            Credit,
            pk=kwargs['credit_pk'],
            regie=self.regie,
        )
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return self.credit.get_grouped_and_ordered_lines()

    def get_context_data(self, **kwargs):
        kwargs['regie'] = self.regie
        kwargs['credit'] = self.credit
        kwargs['user_can_invoice'] = self.regie.can_be_invoiced(self.request.user)
        return super().get_context_data(**kwargs)


regie_credit_line_list = RegieCreditLineListView.as_view()


class RegieCreditCancelView(CanBeInvoicedCheckMixin, UpdateView):
    template_name = 'lingo/invoicing/manager_credit_cancel_form.html'
    pk_url_kwarg = 'credit_pk'
    model = Credit
    form_class = RegieCreditCancelForm

    def dispatch(self, request, *args, **kwargs):
        self.regie = get_object_or_404(Regie, pk=kwargs['regie_pk'])
        self.check_object(self.regie)
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .filter(
                regie=self.regie,
                cancelled_at__isnull=True,
            )
            .exclude(pk__in=CreditAssignment.objects.values('credit'))
            .exclude(pool__campaign__finalized=False)
        )

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['request'] = self.request
        return kwargs

    def get_context_data(self, **kwargs):
        kwargs['regie'] = self.regie
        return super().get_context_data(**kwargs)

    def get_success_url(self):
        return '%s?number=%s' % (
            reverse('lingo-manager-invoicing-regie-credit-list', args=[self.regie.pk]),
            self.object.formatted_number,
        )


regie_credit_cancel = RegieCreditCancelView.as_view()


class RegieCreditPDFView(CanBeViewedCheckMixin, PDFMixin, DetailView):
    pk_url_kwarg = 'credit_pk'
    model = Credit

    def dispatch(self, request, *args, **kwargs):
        self.regie = get_object_or_404(Regie, pk=kwargs['regie_pk'])
        self.check_object(self.regie)
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .filter(
                regie=self.regie,
            )
        )


regie_credit_pdf = RegieCreditPDFView.as_view()


class RegieRefundListView(CanBeViewedCheckMixin, ListView):
    template_name = 'lingo/invoicing/manager_refund_list.html'
    paginate_by = 100

    def dispatch(self, request, *args, **kwargs):
        self.regie = get_object_or_404(Regie, pk=kwargs['regie_pk'])
        self.check_object(self.regie)
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        refund_queryset = Refund.objects.filter(regie=self.regie).order_by('-created_at')
        self.filterset = RegieRefundFilterSet(data=self.request.GET or None, queryset=refund_queryset)
        return self.filterset.qs

    def get_context_data(self, **kwargs):
        kwargs['regie'] = self.regie
        kwargs['filterset'] = self.filterset
        return super().get_context_data(**kwargs)


regie_refund_list = RegieRefundListView.as_view()


class RegiePayerListView(CanBeViewedCheckMixin, ListView):
    template_name = 'lingo/invoicing/manager_regie_payer_list.html'
    paginate_by = 100

    def dispatch(self, request, *args, **kwargs):
        self.regie = get_object_or_404(Regie, pk=kwargs['regie_pk'])
        self.check_object(self.regie)
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        qs_credit = (
            Credit.objects.filter(regie=self.regie)
            .annotate(
                payer_name=Trim(Concat('payer_first_name', Value(' '), 'payer_last_name')),
            )
            .values(
                'payer_external_id',
                'payer_last_name',
                'payer_first_name',
                'payer_name',
            )
        )
        qs_invoice = (
            Invoice.objects.filter(regie=self.regie)
            .annotate(
                payer_name=Trim(Concat('payer_first_name', Value(' '), 'payer_last_name')),
            )
            .values(
                'payer_external_id',
                'payer_last_name',
                'payer_first_name',
                'payer_name',
            )
        )
        self.filterset_credit = RegiePayerFilterSet(data=self.request.GET or None, queryset=qs_credit)
        self.filterset_invoice = RegiePayerFilterSet(data=self.request.GET or None, queryset=qs_invoice)
        return self.filterset_credit.qs.union(self.filterset_invoice.qs).order_by(
            'payer_external_id', 'payer_last_name', 'payer_first_name'
        )

    def get_context_data(self, **kwargs):
        kwargs['regie'] = self.regie
        kwargs['filterset'] = self.filterset_credit
        context = super().get_context_data(**kwargs)
        return context


regie_payer_list = RegiePayerListView.as_view()


class RegiePayerTransactionListView(CanBeViewedCheckMixin, ListView):
    template_name = 'lingo/invoicing/manager_regie_payer_transaction_list.html'
    paginate_by = 100

    def dispatch(self, request, *args, **kwargs):
        self.regie = get_object_or_404(Regie, pk=kwargs['regie_pk'])
        self.check_object(self.regie)
        if 'ods' in request.GET and not self.regie.can_be_invoiced(self.request.user):
            raise PermissionDenied()
        self.full = bool('full' in request.GET)
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        qs_creditline = (
            CreditLine.objects.filter(
                credit__regie=self.regie,
                credit__payer_external_id=self.kwargs['payer_external_id'],
                credit__cancelled_at__isnull=True,
            )
            .annotate(
                invoicing_element_number=F('credit__formatted_number'),
                invoicing_element_created_at=F('credit__created_at'),
                invoicing_element_date_invoicing=F('credit__date_invoicing'),
                invoicing_element_payer_external_id=F('credit__payer_external_id'),
                invoicing_element_payer_first_name=F('credit__payer_first_name'),
                invoicing_element_payer_last_name=F('credit__payer_last_name'),
                invoicing_element_payer_name=Trim(
                    Concat(
                        'invoicing_element_payer_first_name', Value(' '), 'invoicing_element_payer_last_name'
                    )
                ),
                invoicing_element_unit_amount=F('unit_amount'),
                invoicing_element_quantity=-F('quantity'),
                invoicing_element_total_amount=-F('total_amount'),
                invoicing_element_origin=F('credit__origin'),
                invoicing_element=Value('credit'),
            )
            .defer('credit', 'unit_amount', 'quantity', 'total_amount')
        )
        qs_invoiceline = (
            InvoiceLine.objects.filter(
                invoice__regie=self.regie,
                invoice__payer_external_id=self.kwargs['payer_external_id'],
                invoice__cancelled_at__isnull=True,
            )
            .annotate(
                invoicing_element_number=F('invoice__formatted_number'),
                invoicing_element_created_at=F('invoice__created_at'),
                invoicing_element_date_invoicing=F('invoice__date_invoicing'),
                invoicing_element_payer_external_id=F('invoice__payer_external_id'),
                invoicing_element_payer_first_name=F('invoice__payer_first_name'),
                invoicing_element_payer_last_name=F('invoice__payer_last_name'),
                invoicing_element_payer_name=Trim(
                    Concat(
                        'invoicing_element_payer_first_name', Value(' '), 'invoicing_element_payer_last_name'
                    )
                ),
                invoicing_element_unit_amount=F('unit_amount'),
                invoicing_element_quantity=F('quantity'),
                invoicing_element_total_amount=F('total_amount'),
                invoicing_element_origin=F('invoice__origin'),
                invoicing_element=Value('invoice'),
            )
            .defer(
                'invoice',
                'paid_amount',
                'remaining_amount',
                'unit_amount',
                'quantity',
                'total_amount',
            )
        )
        self.filterset_credit = RegiePayerTransactionFilterSet(
            data=self.request.GET or None,
            queryset=qs_creditline,
            regie=self.regie,
            payer_external_id=self.kwargs['payer_external_id'],
        )
        self.filterset_invoice = RegiePayerTransactionFilterSet(
            data=self.request.GET or None,
            queryset=qs_invoiceline,
            other_filterset=self.filterset_credit,
        )
        return self.filterset_credit.qs.union(self.filterset_invoice.qs).order_by(
            '-invoicing_element_created_at', 'user_external_id', 'pk'
        )

    def get_context_data(self, **kwargs):
        kwargs['regie'] = self.regie
        kwargs['payer_external_id'] = self.kwargs['payer_external_id']
        kwargs['user_can_invoice'] = self.regie.can_be_invoiced(self.request.user)
        kwargs['filterset'] = self.filterset_credit
        context = super().get_context_data(**kwargs)
        for value in context['object_list']:
            if value.details.get('dates'):
                value.details['dates'] = [
                    datetime.date(*map(int, d.split('-'))) for d in value.details['dates']
                ]
            if value.invoicing_element_origin:
                value.invoicing_element_origin = {v[0]: v[1] for v in ORIGINS}.get(
                    value.invoicing_element_origin
                )
        return context

    def get(self, request, *args, **kwargs):
        self.object_list = self.get_queryset()
        context = self.get_context_data()
        if 'ods' in request.GET:
            return self.ods(request, context)
        return self.render_to_response(context)

    def ods(self, request, context):
        response = HttpResponse(content_type='application/vnd.oasis.opendocument.spreadsheet')
        if self.full:
            response['Content-Disposition'] = 'attachment; filename="payer-transactions-full.ods"'
        else:
            response['Content-Disposition'] = 'attachment; filename="payer-transactions.ods"'
        writer = Workbook()
        headers = [
            _('Invoicing object'),
            _('Origin'),
            _('Creation date'),
            _('Invoicing date'),
            _('Payer ID'),
            _('Payer first name'),
            _('Payer last name'),
            _('User ID'),
            _('User first name'),
            _('User last name'),
            _('Activity'),
            _('Agenda slug'),
            _('Event'),
            _('Event slug'),
            _('Accounting code'),
            _('Description'),
            _('Details'),
        ]
        event_date_index = len(headers)
        if self.full:
            headers += [
                _('Event date'),
            ]
        headers += [
            _('Unit amount'),
            _('Quantity'),
            _('Total amount'),
        ]
        # headers
        writer.writerow(headers, headers=True)
        for line in self.object_list:
            description = ''
            if not line.details.get('partial_bookings'):
                if line.details.get('check_type_label'):
                    description = line.details['check_type_label']
                elif line.details.get('status') == 'absence':
                    description = _('Absence')
            origin = ''
            if line.invoicing_element_origin:
                origin = {v[0]: v[1] for v in ORIGINS}.get(line.invoicing_element_origin)
            row = [
                line.invoicing_element_number,
                origin,
                line.invoicing_element_created_at.date(),
                line.invoicing_element_date_invoicing,
                line.invoicing_element_payer_external_id,
                line.invoicing_element_payer_first_name,
                line.invoicing_element_payer_last_name,
                line.user_external_id,
                line.user_first_name,
                line.user_last_name,
                line.activity_label,
                line.agenda_slug,
                line.label,
                line.event_slug.split('@')[1] if '@' in line.event_slug else '',
                line.accounting_code,
                description,
                line.description if line.display_description() else '',
                line.invoicing_element_unit_amount,
                line.invoicing_element_quantity,
                line.invoicing_element_total_amount,
            ]
            if not self.full:
                writer.writerow(row)
                continue
            row.insert(event_date_index, '')
            quantity = abs(line.invoicing_element_quantity)
            if quantity != len(line.details.get('dates', [])) or quantity == 0:
                writer.writerow(row)
                continue
            dates = [datetime.date(*map(int, d.split('-'))) for d in line.details['dates']]
            for i in range(int(quantity)):
                new_row = row.copy()
                row_quantity = 1 if line.invoicing_element_quantity > 0 else -1
                new_row[-1] = decimal.Decimal(new_row[-3] * row_quantity).quantize(decimal.Decimal('.01'))
                new_row[-2] = decimal.Decimal(row_quantity).quantize(decimal.Decimal('.01'))
                new_row[event_date_index] = dates[i]
                writer.writerow(new_row)

        writer.save(response)
        return response


regie_payer_transaction_list = RegiePayerTransactionListView.as_view()


class RegieTransactionForEventListView(CanBeViewedCheckMixin, ListView):
    template_name = 'lingo/invoicing/manager_regie_transaction_for_event_list.html'
    paginate_by = 100

    def dispatch(self, request, *args, **kwargs):
        self.regie = get_object_or_404(Regie, pk=kwargs['regie_pk'])
        self.check_object(self.regie)
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        user_external_id = self.request.GET.get('user_external_id')
        event_slug = self.request.GET.get('event_slug')
        event_date = self.request.GET.get('event_date')
        if not (user_external_id and event_slug and event_date):
            raise Http404
        qs_creditline = (
            CreditLine.objects.filter(
                credit__regie=self.regie,
                credit__cancelled_at__isnull=True,
                user_external_id=user_external_id,
                event_slug=event_slug,
                details__jsonpath_exists=f'$.dates[*] ? (@ == "{event_date}")',
            )
            .annotate(
                invoicing_element_number=F('credit__formatted_number'),
                invoicing_element_created_at=F('credit__created_at'),
                invoicing_element_date_invoicing=F('credit__date_invoicing'),
                invoicing_element_payer_external_id=F('credit__payer_external_id'),
                invoicing_element_payer_first_name=F('credit__payer_first_name'),
                invoicing_element_payer_last_name=F('credit__payer_last_name'),
                invoicing_element_payer_name=Trim(
                    Concat(
                        'invoicing_element_payer_first_name', Value(' '), 'invoicing_element_payer_last_name'
                    )
                ),
                invoicing_element_unit_amount=F('unit_amount'),
                invoicing_element_quantity=-F('quantity'),
                invoicing_element_total_amount=-F('total_amount'),
                invoicing_element_origin=F('credit__origin'),
                invoicing_element=Value('credit'),
            )
            .defer('credit', 'unit_amount', 'quantity', 'total_amount')
        )
        qs_invoiceline = (
            InvoiceLine.objects.filter(
                invoice__regie=self.regie,
                invoice__cancelled_at__isnull=True,
                user_external_id=user_external_id,
                event_slug=event_slug,
                details__jsonpath_exists=f'$.dates[*] ? (@ == "{event_date}")',
            )
            .annotate(
                invoicing_element_number=F('invoice__formatted_number'),
                invoicing_element_created_at=F('invoice__created_at'),
                invoicing_element_date_invoicing=F('invoice__date_invoicing'),
                invoicing_element_payer_external_id=F('invoice__payer_external_id'),
                invoicing_element_payer_first_name=F('invoice__payer_first_name'),
                invoicing_element_payer_last_name=F('invoice__payer_last_name'),
                invoicing_element_payer_name=Trim(
                    Concat(
                        'invoicing_element_payer_first_name', Value(' '), 'invoicing_element_payer_last_name'
                    )
                ),
                invoicing_element_unit_amount=F('unit_amount'),
                invoicing_element_quantity=F('quantity'),
                invoicing_element_total_amount=F('total_amount'),
                invoicing_element_origin=F('invoice__origin'),
                invoicing_element=Value('invoice'),
            )
            .defer(
                'invoice',
                'paid_amount',
                'remaining_amount',
                'unit_amount',
                'quantity',
                'total_amount',
            )
        )
        return qs_creditline.union(qs_invoiceline).order_by('-invoicing_element_created_at', 'pk')

    def get_context_data(self, **kwargs):
        kwargs['regie'] = self.regie
        kwargs['user_external_id'] = self.request.GET.get('user_external_id')
        kwargs['event_slug'] = self.request.GET.get('event_slug')
        kwargs['event_date'] = self.request.GET.get('event_date')
        context = super().get_context_data(**kwargs)
        for value in context['object_list']:
            if value.details.get('dates'):
                value.details['dates'] = [
                    datetime.date(*map(int, d.split('-'))) for d in value.details['dates']
                ]
            if value.invoicing_element_origin:
                value.invoicing_element_origin = {v[0]: v[1] for v in ORIGINS}.get(
                    value.invoicing_element_origin
                )
        return context


regie_transaction_for_event_list = RegieTransactionForEventListView.as_view()


class RegieInspectView(CanBeManagedRequiredMixin, DetailView):
    template_name = 'lingo/invoicing/manager_regie_inspect.html'
    model = Regie

    def get_queryset(self):
        return super().get_queryset().select_related('edit_role', 'view_role', 'invoice_role', 'control_role')


regie_inspect = RegieInspectView.as_view()


class RegieHistoryView(CanBeManagedCheckMixin, InstanceWithSnapshotHistoryView):
    template_name = 'lingo/invoicing/manager_regie_history.html'
    model = RegieSnapshot
    instance_context_key = 'regie'

    def dispatch(self, request, *args, **kwargs):
        self.regie = get_object_or_404(Regie, pk=kwargs['pk'])
        self.check_object(self.regie)
        return super().dispatch(request, *args, **kwargs)


regie_history = RegieHistoryView.as_view()


class RegieHistoryCompareView(CanBeManagedRequiredMixin, InstanceWithSnapshotHistoryCompareView):
    template_name = 'lingo/invoicing/manager_regie_history_compare.html'
    inspect_template_name = 'lingo/invoicing/manager_regie_inspect_fragment.html'
    model = Regie
    instance_context_key = 'regie'
    history_view = 'lingo-manager-regie-history'


regie_history_compare = RegieHistoryCompareView.as_view()
