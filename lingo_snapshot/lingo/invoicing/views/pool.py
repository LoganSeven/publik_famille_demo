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

from django.conf import settings
from django.contrib import messages
from django.db import transaction
from django.db.models import Count, IntegerField, Subquery, Value
from django.db.models.functions import Coalesce
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils.timezone import now
from django.utils.translation import gettext_lazy as _
from django.views.generic import DeleteView, DetailView, FormView, ListView

from lingo.agendas.chrono import ChronoError
from lingo.agendas.models import Agenda, AgendaUnlockLog
from lingo.invoicing.forms import (
    CreditFilterSet,
    DraftCreditFilterSet,
    DraftInvoiceFilterSet,
    DraftJournalLineFilterSet,
    InvoiceFilterSet,
    JournalLineFilterSet,
    PoolDeleteForm,
)
from lingo.invoicing.models import (
    Campaign,
    Credit,
    CreditCancellationReason,
    CreditLine,
    DraftInvoice,
    DraftInvoiceLine,
    DraftJournalLine,
    Invoice,
    InvoiceCancellationReason,
    InvoiceLine,
    JournalLine,
    Pool,
    Regie,
)
from lingo.invoicing.utils import replay_error
from lingo.invoicing.views.utils import PDFMixin
from lingo.manager.utils import CanBeInvoicedCheckMixin, CanBeViewedCheckMixin


def is_ajax(request):
    return request.headers.get('x-requested-with') == 'XMLHttpRequest'


class PoolStopView(CanBeInvoicedCheckMixin, DeleteView):
    template_name = 'lingo/manager_confirm_delete.html'
    model = Pool
    pk_url_kwarg = 'pool_pk'

    def dispatch(self, request, *args, **kwargs):
        self.regie = get_object_or_404(Regie, pk=kwargs['regie_pk'])
        self.check_object(self.regie)
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .filter(
                campaign=self.kwargs['pk'],
                campaign__regie=self.regie,
                status__in=['registered', 'running'],
            )
        )

    def get_context_data(self, **kwargs):
        kwargs['delete_msg'] = _('Are you sure you want to stop this pool?')
        kwargs['delete_button_label'] = _('Stop')
        return super().get_context_data(**kwargs)

    def form_valid(self, form):
        self.object = self.get_object()
        self.object.status = 'failed'
        self.object.exception = _('Stopped')
        self.object.save()
        success_url = self.get_success_url()
        return HttpResponseRedirect(success_url)

    def get_success_url(self):
        return reverse(
            'lingo-manager-invoicing-pool-detail',
            args=[self.regie.pk, self.object.campaign.pk, self.object.pk],
        )


pool_stop = PoolStopView.as_view()


class PoolMixin:
    def set_pool(self, line_model):
        lines = line_model.objects.filter(pool=self.object).order_by().values('pool')
        if line_model == DraftJournalLine:
            count_error = lines.filter(status='error').annotate(count=Count('pool')).values('count')
        else:
            count_error = (
                lines.filter(status='error', error_status='').annotate(count=Count('pool')).values('count')
            )
        count_warning = lines.filter(status='warning').annotate(count=Count('pool')).values('count')
        count_success = lines.filter(status='success').annotate(count=Count('pool')).values('count')
        self.object = Pool.objects.annotate(
            error_count=Coalesce(Subquery(count_error, output_field=IntegerField()), Value(0)),
            warning_count=Coalesce(Subquery(count_warning, output_field=IntegerField()), Value(0)),
            success_count=Coalesce(Subquery(count_success, output_field=IntegerField()), Value(0)),
        ).get(pk=self.object.pk)


class PoolDetailView(CanBeViewedCheckMixin, PoolMixin, ListView):
    template_name = 'lingo/invoicing/manager_pool_detail.html'
    paginate_by = 100

    def dispatch(self, request, *args, **kwargs):
        self.regie = get_object_or_404(Regie, pk=kwargs['regie_pk'])
        self.check_object(self.regie)
        self.campaign = get_object_or_404(Campaign, pk=kwargs['pk'], regie=self.regie)
        self.object = get_object_or_404(Pool, pk=kwargs['pool_pk'], campaign=self.campaign)
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        self.display_mode = 'invoices'
        if 'credits' in self.request.GET:
            self.display_mode = 'credits'
        if self.object.draft:
            line_model = DraftJournalLine
            invoice_model = DraftInvoice
            if self.display_mode == 'invoices':
                filter_model = DraftInvoiceFilterSet
            else:
                filter_model = DraftCreditFilterSet
        else:
            line_model = JournalLine
            if self.display_mode == 'invoices':
                invoice_model = Invoice
                filter_model = InvoiceFilterSet
            else:
                invoice_model = Credit
                filter_model = CreditFilterSet

        self.set_pool(line_model)
        invoice_queryset = invoice_model.objects.filter(pool=self.object).order_by('created_at')
        if self.object.draft:
            if self.display_mode == 'invoices':
                invoice_queryset = invoice_queryset.filter(total_amount__gte=0)
            else:
                invoice_queryset = invoice_queryset.filter(total_amount__lt=0)
        elif self.display_mode == 'invoices':
            invoice_queryset = invoice_queryset.select_related('collection')

        data = self.request.GET or None
        self.filterset = filter_model(
            data=data,
            queryset=invoice_queryset,
            pool=self.object,
        )
        return self.filterset.qs

    def get_context_data(self, **kwargs):
        kwargs[self.display_mode] = True
        kwargs['regie'] = self.regie
        kwargs['object'] = self.campaign
        kwargs['pool'] = self.object
        kwargs['filterset'] = self.filterset
        kwargs['has_running_pool'] = any(
            p.status in ['registered', 'running'] for p in self.campaign.pool_set.all()
        )
        kwargs['user_can_invoice'] = self.regie.can_be_invoiced(self.request.user)
        return super().get_context_data(**kwargs)


pool_detail = PoolDetailView.as_view()


class PoolJournalView(CanBeViewedCheckMixin, PoolMixin, ListView):
    template_name = 'lingo/invoicing/manager_pool_journal.html'
    paginate_by = 100

    def dispatch(self, request, *args, **kwargs):
        self.regie = get_object_or_404(Regie, pk=kwargs['regie_pk'])
        self.check_object(self.regie)
        self.campaign = get_object_or_404(Campaign, pk=kwargs['pk'], regie=self.regie)
        self.object = get_object_or_404(Pool, pk=kwargs['pool_pk'], campaign=self.campaign)
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        line_model = JournalLine
        filter_model = JournalLineFilterSet
        if self.object.draft:
            line_model = DraftJournalLine
            filter_model = DraftJournalLineFilterSet

        self.set_pool(line_model)

        all_lines = (
            line_model.objects.filter(pool=self.object).order_by('pk').select_related('invoice_line__invoice')
        )
        data = self.request.GET or None
        self.filterset = filter_model(data=data, queryset=all_lines, pool=self.object)
        return self.filterset.qs if data and [v for v in data.values() if v] else all_lines

    def get_context_data(self, **kwargs):
        kwargs['regie'] = self.regie
        kwargs['object'] = self.campaign
        kwargs['pool'] = self.object
        kwargs['filterset'] = self.filterset
        kwargs['user_can_invoice'] = self.regie.can_be_invoiced(self.request.user)
        kwargs['show_fix_error'] = settings.CAMPAIGN_SHOW_FIX_ERROR
        return super().get_context_data(**kwargs)


pool_journal = PoolJournalView.as_view()


class PoolAddView(CanBeInvoicedCheckMixin, FormView):
    template_name = 'lingo/invoicing/manager_pool_add.html'

    def dispatch(self, request, *args, **kwargs):
        self.regie = get_object_or_404(Regie, pk=kwargs['regie_pk'])
        self.check_object(self.regie)
        self.object = get_object_or_404(
            Campaign.objects.filter(regie=self.regie, finalized=False)
            .exclude(pool__draft=False)
            .exclude(pool__status__in=['registered', 'running']),
            pk=kwargs['pk'],
        )
        self.has_partial_bookings_agendas = self.object.agendas.filter(partial_bookings=True).exists()
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        kwargs['form'] = None
        kwargs['regie'] = self.regie
        kwargs['object'] = self.object
        kwargs['has_partial_bookings_agendas'] = self.has_partial_bookings_agendas
        return super().get_context_data(**kwargs)

    def post(self, request, *args, **kwargs):
        if self.object.adjustment_campaign and self.has_partial_bookings_agendas:
            messages.warning(
                self.request, _('An adjustment campaign cannot be launched on a partial bookings agenda.')
            )
        self.object.mark_as_valid()
        job = self.object.generate()
        primary_campaign = self.object
        if self.object.primary_campaign:
            primary_campaign = self.object.primary_campaign
        AgendaUnlockLog.objects.filter(
            campaign=primary_campaign, agenda__in=self.object.agendas.all(), active=True
        ).update(active=False, updated_at=now())
        if job.status == 'registered':
            return redirect(
                reverse(
                    'lingo-manager-invoicing-campaign-job-detail',
                    args=[self.regie.pk, primary_campaign.pk, job.uuid],
                )
            )
        return redirect(
            '%s#open:pools'
            % reverse('lingo-manager-invoicing-campaign-detail', args=[self.regie.pk, self.object.pk])
        )


pool_add = PoolAddView.as_view()


class PoolPromoteView(CanBeInvoicedCheckMixin, FormView):
    template_name = 'lingo/invoicing/manager_pool_promote.html'

    def dispatch(self, request, *args, **kwargs):
        self.regie = get_object_or_404(Regie, pk=kwargs['regie_pk'])
        self.check_object(self.regie)
        self.object = get_object_or_404(
            Pool,
            campaign__id=kwargs['pk'],
            campaign__regie=self.regie,
            campaign__invalid=False,
            campaign__finalized=False,
            pk=kwargs['pool_pk'],
            draft=True,
            status='completed',
        )
        if not self.object.is_last:
            raise Http404
        self.cannot_promote = False
        if (
            not settings.CAMPAIGN_ALLOW_PROMOTION_WITH_ERRORS
            and self.object.draftjournalline_set.filter(status='error').exists()
        ):
            self.cannot_promote = True
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        kwargs['form'] = None
        kwargs['regie'] = self.regie
        kwargs['object'] = self.object.campaign
        kwargs['pool'] = self.object
        kwargs['cannot_promote'] = self.cannot_promote
        return super().get_context_data(**kwargs)

    def post(self, request, *args, **kwargs):
        if self.cannot_promote:
            raise Http404
        job = self.object.promote()
        if job.status == 'registered':
            return redirect(
                reverse(
                    'lingo-manager-invoicing-campaign-job-detail',
                    args=[self.regie.pk, self.object.campaign.pk, job.uuid],
                )
            )
        return redirect(
            '%s#open:pools'
            % reverse(
                'lingo-manager-invoicing-campaign-detail', args=[self.regie.pk, self.object.campaign.pk]
            )
        )


pool_promote = PoolPromoteView.as_view()


class PoolDeleteView(CanBeInvoicedCheckMixin, FormView):
    form_class = PoolDeleteForm

    def dispatch(self, request, *args, **kwargs):
        self.regie = get_object_or_404(Regie, pk=kwargs['regie_pk'])
        self.check_object(self.regie)
        self.campaign = get_object_or_404(
            Campaign.objects.filter(regie=self.regie, finalized=False).exclude(
                pool__status__in=['registered', 'running']
            ),
            pk=kwargs['pk'],
        )
        self.object = get_object_or_404(self.campaign.pool_set, pk=kwargs['pool_pk'])
        InvoiceCancellationReason.objects.get_or_create(
            slug='final-pool-deletion', defaults={'label': _('Final pool deletion')}
        )
        CreditCancellationReason.objects.get_or_create(
            slug='final-pool-deletion', defaults={'label': _('Final pool deletion')}
        )
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        with transaction.atomic():
            if self.object.is_last and self.object.draft:
                self.campaign.mark_as_invalid()
            line_model = JournalLine
            if self.object.draft:
                line_model = DraftJournalLine
            line_model.objects.filter(pool=self.object).delete()
            if self.object.draft:
                DraftInvoiceLine.objects.filter(pool=self.object).delete()
                DraftInvoice.objects.filter(pool=self.object).delete()
            else:
                cancellation_reason = InvoiceCancellationReason.objects.get(slug='final-pool-deletion')
                InvoiceLine.objects.filter(pool=self.object).update(pool=None)
                Invoice.objects.filter(pool=self.object).update(
                    pool=None,
                    cancelled_at=now(),
                    cancelled_by=self.request.user,
                    cancellation_reason=cancellation_reason,
                    cancellation_description=form.cleaned_data['cancellation_description'],
                )
                cancellation_reason = CreditCancellationReason.objects.get(slug='final-pool-deletion')
                CreditLine.objects.filter(pool=self.object).update(pool=None)
                Credit.objects.filter(pool=self.object).update(
                    pool=None,
                    cancelled_at=now(),
                    cancelled_by=self.request.user,
                    cancellation_reason=cancellation_reason,
                    cancellation_description=form.cleaned_data['cancellation_description'],
                )
            self.object.delete()
        return super().form_valid(form)

    def get_template_names(self):
        if self.object.draft:
            return ['lingo/manager_confirm_delete.html']
        return ['lingo/invoicing/manager_pool_delete.html']

    def get_context_data(self, **kwargs):
        kwargs['regie'] = self.regie
        kwargs['object'] = self.campaign
        kwargs['pool'] = self.object
        return super().get_context_data(**kwargs)

    def get_success_url(self):
        return '%s#open:pools' % reverse(
            'lingo-manager-invoicing-campaign-detail', args=[self.regie.pk, self.campaign.pk]
        )


pool_delete = PoolDeleteView.as_view()


class InvoicePDFView(CanBeViewedCheckMixin, PDFMixin, DetailView):
    pk_url_kwarg = 'invoice_pk'

    def dispatch(self, request, *args, **kwargs):
        self.regie = get_object_or_404(Regie, pk=kwargs['regie_pk'])
        self.check_object(self.regie)
        self.pool = get_object_or_404(
            Pool, pk=kwargs['pool_pk'], campaign=kwargs['pk'], campaign__regie=self.regie
        )
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        invoice_model = Invoice
        if self.pool.draft:
            invoice_model = DraftInvoice
        return invoice_model.objects.filter(pool=self.pool)


invoice_pdf = InvoicePDFView.as_view()


class CreditPDFView(CanBeViewedCheckMixin, PDFMixin, DetailView):
    pk_url_kwarg = 'credit_pk'
    model = Credit

    def dispatch(self, request, *args, **kwargs):
        self.regie = get_object_or_404(Regie, pk=kwargs['regie_pk'])
        self.check_object(self.regie)
        self.pool = get_object_or_404(
            Pool, pk=kwargs['pool_pk'], campaign=kwargs['pk'], campaign__regie=self.regie
        )
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        invoice_model = Credit
        if self.pool.draft:
            invoice_model = DraftInvoice
        return invoice_model.objects.filter(pool=self.pool)


credit_pdf = CreditPDFView.as_view()


class InvoiceLineListView(CanBeViewedCheckMixin, ListView):
    template_name = 'lingo/invoicing/manager_invoice_lines.html'

    def dispatch(self, request, *args, **kwargs):
        self.regie = get_object_or_404(Regie, pk=kwargs['regie_pk'])
        self.check_object(self.regie)
        self.pool = get_object_or_404(
            Pool, pk=kwargs['pool_pk'], campaign_id=kwargs['pk'], campaign__regie=self.regie
        )
        invoice_model = Invoice
        if self.pool.draft:
            invoice_model = DraftInvoice
        self.invoice = get_object_or_404(invoice_model, pk=kwargs['invoice_pk'], pool=self.pool)
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return self.invoice.get_grouped_and_ordered_lines()

    def get_context_data(self, **kwargs):
        kwargs['regie'] = self.pool.campaign.regie
        kwargs['object'] = self.pool.campaign
        kwargs['pool'] = self.pool
        kwargs['invoice'] = self.invoice
        return super().get_context_data(**kwargs)


invoice_line_list = InvoiceLineListView.as_view()


class CreditLineListView(CanBeViewedCheckMixin, ListView):
    template_name = 'lingo/invoicing/manager_credit_lines.html'
    invoice_model = Credit

    def dispatch(self, request, *args, **kwargs):
        self.regie = get_object_or_404(Regie, pk=kwargs['regie_pk'])
        self.check_object(self.regie)
        self.pool = get_object_or_404(
            Pool, pk=kwargs['pool_pk'], campaign_id=kwargs['pk'], campaign__regie=self.regie
        )
        credit_model = Credit
        if self.pool.draft:
            credit_model = DraftInvoice
        self.credit = get_object_or_404(credit_model, pk=kwargs['credit_pk'], pool=self.pool)
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return self.credit.get_grouped_and_ordered_lines()

    def get_context_data(self, **kwargs):
        kwargs['regie'] = self.pool.campaign.regie
        kwargs['object'] = self.pool.campaign
        kwargs['pool'] = self.pool
        kwargs['credit'] = self.credit
        return super().get_context_data(**kwargs)


credit_line_list = CreditLineListView.as_view()


class LineSetErrorStatusView(CanBeInvoicedCheckMixin, DetailView):
    pk_url_kwarg = 'line_pk'
    template_name = 'lingo/invoicing/manager_line_detail_fragment.html'

    def dispatch(self, request, *args, **kwargs):
        self.regie = get_object_or_404(Regie, pk=kwargs['regie_pk'])
        self.check_object(self.regie)
        self.pool = get_object_or_404(
            Pool, pk=kwargs['pool_pk'], campaign_id=kwargs['pk'], campaign__regie=self.regie
        )
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        line_model = JournalLine
        if self.pool.draft:
            line_model = DraftJournalLine
        return line_model.objects.filter(
            status='error',
            pool=self.pool,
        )

    def get_context_data(self, **kwargs):
        kwargs['regie'] = self.regie
        kwargs['object'] = self.pool.campaign
        kwargs['pool'] = self.pool
        kwargs['line'] = self.object
        return super().get_context_data(**kwargs)

    def get(self, request, *args, **kwargs):
        self.object = self.get_object()
        error_status = kwargs['status']
        if error_status == 'reset':
            self.object.error_status = ''
        elif error_status == 'ignore':
            self.object.error_status = 'ignored'
        elif error_status == 'fix':
            self.object.error_status = 'fixed'
        else:
            raise Http404
        self.object.save()

        if is_ajax(self.request):
            context = self.get_context_data(object=self.object)
            return self.render_to_response(context)

        return redirect(
            reverse(
                'lingo-manager-invoicing-pool-journal', args=[self.regie.pk, kwargs['pk'], kwargs['pool_pk']]
            )
        )


line_set_error_status = LineSetErrorStatusView.as_view()


class LineReplayView(CanBeInvoicedCheckMixin, DetailView):
    pk_url_kwarg = 'line_pk'
    template_name = 'lingo/invoicing/manager_line_replayed.html'

    def dispatch(self, request, *args, **kwargs):
        self.regie = get_object_or_404(Regie, pk=kwargs['regie_pk'])
        self.check_object(self.regie)
        self.pool = get_object_or_404(
            Pool,
            pk=kwargs['pool_pk'],
            campaign_id=kwargs['pk'],
            campaign__regie=self.regie,
            draft=True,
        )
        if not self.pool.is_last:
            raise Http404
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return DraftJournalLine.objects.filter(
            status='error',
            error_status='',
            pool=self.pool,
        )

    def get_context_data(self, **kwargs):
        kwargs['regie'] = self.regie
        kwargs['object'] = self.pool.campaign
        kwargs['pool'] = self.pool
        kwargs['line'] = self.object
        return super().get_context_data(**kwargs)

    def get(self, request, *args, **kwargs):
        self.object = self.get_object()
        try:
            replay_error(self.object)
        except Agenda.DoesNotExist:
            raise Http404('unknown agenda')
        except ChronoError as e:
            messages.error(self.request, e.msg)
        else:
            if is_ajax(self.request):
                context = self.get_context_data(object=self.object)
                return self.render_to_response(context)

        return redirect(
            reverse(
                'lingo-manager-invoicing-pool-journal', args=[self.regie.pk, kwargs['pk'], kwargs['pool_pk']]
            )
        )


line_replay = LineReplayView.as_view()
