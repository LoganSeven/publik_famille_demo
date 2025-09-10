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

import decimal

from django.contrib import messages
from django.db.models import Count, Exists, IntegerField, OuterRef, Prefetch, Subquery, Value
from django.db.models.functions import Coalesce
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils.datastructures import MultiValueDict
from django.utils.timezone import now
from django.utils.translation import gettext_lazy as _
from django.views.generic import CreateView, DeleteView, DetailView, FormView, ListView, UpdateView

from lingo.agendas.chrono import ChronoError, mark_events_invoiced, unlock_events_check
from lingo.agendas.models import Agenda, AgendaUnlockLog, CheckType
from lingo.invoicing.forms import (
    CampaignDatesForm,
    CampaignEventAmountsODSForm,
    CampaignForm,
    CorrectiveCampaignForm,
)
from lingo.invoicing.models import (
    Campaign,
    CampaignAsyncJob,
    DraftInvoice,
    DraftInvoiceLine,
    DraftJournalLine,
    InjectedLine,
    JournalLine,
    Pool,
    Regie,
)
from lingo.manager.utils import CanBeInvoicedCheckMixin, CanBeViewedCheckMixin, CanBeViewedRequiredMixin
from lingo.utils.ods import Workbook


class CampaignListView(CanBeViewedRequiredMixin, ListView):
    template_name = 'lingo/invoicing/manager_campaign_list.html'
    model = Campaign

    def dispatch(self, request, *args, **kwargs):
        self.regie = get_object_or_404(Regie, pk=kwargs['regie_pk'])
        self.check_object(self.regie)
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        queryset = super().get_queryset()
        return (
            queryset.filter(regie=self.regie, primary_campaign__isnull=True)
            .annotate(
                has_pending_corrective_campaign=Exists(
                    Campaign.objects.filter(primary_campaign=OuterRef('id'), finalized=False)
                ),
            )
            .prefetch_related(
                Prefetch(
                    'corrective_campaigns',
                    queryset=Campaign.objects.order_by('-created_at'),
                    to_attr='prefetched_corrective_campaigns',
                ),
                Prefetch(
                    'agendaunlocklog_set',
                    queryset=AgendaUnlockLog.objects.filter(active=True).select_related('agenda'),
                    to_attr='prefetched_logs',
                ),
                Prefetch('agendas'),
            )
            .order_by('-date_start')
        )

    def get_context_data(self, **kwargs):
        kwargs['regie'] = self.regie
        kwargs['user_can_invoice'] = self.regie.can_be_invoiced(self.request.user)
        context = super().get_context_data(**kwargs)
        for campaign in context['object_list']:
            # remove logs about agendas not included in the campaign
            campaign.prefetched_logs = [
                log for log in campaign.prefetched_logs if log.agenda in campaign.agendas.all()
            ]
        return context


campaign_list = CampaignListView.as_view()


class CampaignAddView(CanBeInvoicedCheckMixin, CreateView):
    template_name = 'lingo/invoicing/manager_campaign_form.html'
    model = Campaign
    form_class = CampaignForm

    def dispatch(self, request, *args, **kwargs):
        self.regie = get_object_or_404(Regie, pk=kwargs['regie_pk'])
        self.check_object(self.regie)
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        kwargs['regie'] = self.regie
        return super().get_context_data(**kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['instance'] = Campaign(regie=self.regie, invoice_model=self.regie.invoice_model)
        return kwargs

    def get_success_url(self):
        return reverse('lingo-manager-invoicing-campaign-detail', args=[self.regie.pk, self.object.pk])


campaign_add = CampaignAddView.as_view()


class CampaignDetailView(CanBeViewedCheckMixin, DetailView):
    template_name = 'lingo/invoicing/manager_campaign_detail.html'
    model = Campaign

    def dispatch(self, request, *args, **kwargs):
        self.regie = get_object_or_404(Regie, pk=kwargs['regie_pk'])
        self.check_object(self.regie)
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .filter(regie=self.regie)
            .prefetch_related(
                Prefetch(
                    'agendas',
                    queryset=Agenda.objects.order_by('category_label', 'label'),
                    to_attr='prefetched_agendas',
                )
            )
        )

    def get_context_data(self, **kwargs):
        draft_lines = DraftJournalLine.objects.filter(pool=OuterRef('pk')).order_by().values('pool')
        count_draft_error = draft_lines.filter(status='error').annotate(count=Count('pool')).values('count')
        count_draft_warning = (
            draft_lines.filter(status='warning').annotate(count=Count('pool')).values('count')
        )
        count_draft_success = (
            draft_lines.filter(status='success').annotate(count=Count('pool')).values('count')
        )
        lines = JournalLine.objects.filter(pool=OuterRef('pk')).order_by().values('pool')
        count_error = (
            lines.filter(status='error', error_status='').annotate(count=Count('pool')).values('count')
        )
        count_warning = lines.filter(status='warning').annotate(count=Count('pool')).values('count')
        count_success = lines.filter(status='success').annotate(count=Count('pool')).values('count')
        kwargs['regie'] = self.regie
        kwargs['pools'] = self.object.pool_set.annotate(
            draft_error_count=Coalesce(Subquery(count_draft_error, output_field=IntegerField()), Value(0)),
            draft_warning_count=Coalesce(
                Subquery(count_draft_warning, output_field=IntegerField()), Value(0)
            ),
            draft_success_count=Coalesce(
                Subquery(count_draft_success, output_field=IntegerField()), Value(0)
            ),
            error_count=Coalesce(Subquery(count_error, output_field=IntegerField()), Value(0)),
            warning_count=Coalesce(Subquery(count_warning, output_field=IntegerField()), Value(0)),
            success_count=Coalesce(Subquery(count_success, output_field=IntegerField()), Value(0)),
        ).order_by('-created_at')
        kwargs['has_running_pool'] = any(p.status in ['registered', 'running'] for p in kwargs['pools'])
        kwargs['has_real_pool'] = any(not p.draft for p in kwargs['pools'])
        kwargs['has_real_completed_pool'] = any(
            not p.draft and p.status == 'completed' for p in kwargs['pools']
        )
        kwargs['has_injected_lines'] = InjectedLine.objects.filter(regie=self.regie).exists()
        if self.object.invalid:
            messages.warning(self.request, _('The last pool is invalid, please start a new pool.'))
        kwargs['user_can_invoice'] = self.regie.can_be_invoiced(self.request.user)
        primary_campaign = self.object
        if self.object.primary_campaign:
            primary_campaign = self.object.primary_campaign
        kwargs['primary_campaign'] = primary_campaign
        kwargs['has_running_corrective'] = Campaign.objects.filter(
            primary_campaign=primary_campaign, finalized=False
        ).exists()
        return super().get_context_data(**kwargs)


campaign_detail = CampaignDetailView.as_view()


class CampaignEditView(CanBeInvoicedCheckMixin, UpdateView):
    template_name = 'lingo/invoicing/manager_campaign_form.html'
    model = Campaign
    form_class = CampaignForm

    def dispatch(self, request, *args, **kwargs):
        self.regie = get_object_or_404(Regie, pk=kwargs['regie_pk'])
        self.check_object(self.regie)
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .filter(regie=self.regie, finalized=False, primary_campaign__isnull=True)
            .exclude(pool__draft=False)
            .exclude(pool__status__in=['registered', 'running'])
        )

    def get_context_data(self, **kwargs):
        kwargs['regie'] = self.regie
        return super().get_context_data(**kwargs)

    def get_success_url(self):
        return '%s#open:settings' % reverse(
            'lingo-manager-invoicing-campaign-detail', args=[self.regie.pk, self.object.pk]
        )


campaign_edit = CampaignEditView.as_view()


class CampaignDatesEditView(CanBeInvoicedCheckMixin, UpdateView):
    template_name = 'lingo/invoicing/manager_campaign_dates_form.html'
    model = Campaign
    form_class = CampaignDatesForm

    def dispatch(self, request, *args, **kwargs):
        self.regie = get_object_or_404(Regie, pk=kwargs['regie_pk'])
        self.check_object(self.regie)
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .filter(regie=self.regie)
            .exclude(pool__status__in=['registered', 'running'])
        )

    def get_context_data(self, **kwargs):
        kwargs['regie'] = self.regie
        return super().get_context_data(**kwargs)

    def get_success_url(self):
        return '%s#open:dates' % reverse(
            'lingo-manager-invoicing-campaign-detail', args=[self.regie.pk, self.object.pk]
        )


campaign_dates_edit = CampaignDatesEditView.as_view()


class CampaignInvoicesEditView(CampaignDatesEditView):
    template_name = 'lingo/invoicing/manager_campaign_invoices_form.html'
    model = Campaign
    form_class = None
    fields = [
        'invoice_model',
        'invoice_custom_text',
    ]

    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .filter(regie=self.regie, finalized=False, primary_campaign__isnull=True)
            .exclude(pool__status__in=['registered', 'running'])
        )

    def get_success_url(self):
        return '%s#open:invoices' % reverse(
            'lingo-manager-invoicing-campaign-detail', args=[self.regie.pk, self.object.pk]
        )


campaign_invoices_edit = CampaignInvoicesEditView.as_view()


class CampaignDeleteView(CanBeInvoicedCheckMixin, DeleteView):
    template_name = 'lingo/manager_confirm_delete.html'
    model = Campaign

    def dispatch(self, request, *args, **kwargs):
        self.regie = get_object_or_404(Regie, pk=kwargs['regie_pk'])
        self.check_object(self.regie)
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .filter(regie=self.regie, finalized=False)
            .exclude(pool__draft=False)
            .exclude(pool__status__in=['registered', 'running'])
        )

    def form_valid(self, form):
        self.object = self.get_object()
        DraftJournalLine.objects.filter(pool__campaign=self.object).delete()
        DraftInvoiceLine.objects.filter(pool__campaign=self.object).delete()
        DraftInvoice.objects.filter(pool__campaign=self.object).delete()
        Pool.objects.filter(campaign=self.object).delete()
        return super().form_valid(form)

    def get_success_url(self):
        return reverse('lingo-manager-invoicing-campaign-list', args=[self.regie.pk])


campaign_delete = CampaignDeleteView.as_view()


class CampaignUnlockCheckView(CanBeInvoicedCheckMixin, FormView):
    template_name = 'lingo/invoicing/manager_campaign_unlock_check.html'

    def dispatch(self, request, *args, **kwargs):
        self.regie = get_object_or_404(Regie, pk=kwargs['regie_pk'])
        self.check_object(self.regie)
        self.object = get_object_or_404(
            Campaign.objects.filter(regie=self.regie, finalized=False)
            .exclude(pool__draft=False)
            .exclude(pool__status__in=['registered', 'running']),
            pk=kwargs['pk'],
        )
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        kwargs['form'] = None
        kwargs['regie'] = self.regie
        kwargs['object'] = self.object
        return super().get_context_data(**kwargs)

    def post(self, request, *args, **kwargs):
        self.object.mark_as_invalid()
        agendas = [a.slug for a in self.object.agendas.all()]
        if agendas:
            try:
                unlock_events_check(
                    agenda_slugs=agendas,
                    date_start=self.object.date_start,
                    date_end=self.object.date_end,
                )
            except ChronoError as e:
                messages.error(self.request, _('Fail to unlock events check: %s') % e)

        return redirect(
            '%s#open:pools'
            % reverse('lingo-manager-invoicing-campaign-detail', args=[self.regie.pk, self.object.pk])
        )


campaign_unlock_check = CampaignUnlockCheckView.as_view()


class CampaignFinalizeView(CanBeInvoicedCheckMixin, FormView):
    template_name = 'lingo/invoicing/manager_campaign_finalize.html'

    def dispatch(self, request, *args, **kwargs):
        self.regie = get_object_or_404(Regie, pk=kwargs['regie_pk'])
        self.check_object(self.regie)
        self.object = get_object_or_404(
            Campaign.objects.filter(regie=self.regie, invalid=False, finalized=False).filter(
                pk__in=Pool.objects.filter(draft=False, status='completed').values('campaign')
            ),
            pk=kwargs['pk'],
        )
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        kwargs['form'] = None
        kwargs['regie'] = self.regie
        kwargs['object'] = self.object
        return super().get_context_data(**kwargs)

    def post(self, request, *args, **kwargs):
        job = None
        try:
            agendas = [a.slug for a in self.object.agendas.all()]
            if agendas:
                try:
                    mark_events_invoiced(
                        agenda_slugs=agendas,
                        date_start=self.object.date_start,
                        date_end=self.object.date_end,
                    )
                except ChronoError as e:
                    messages.error(self.request, _('Fail to mark events as invoiced: %s') % e)
                    raise
        except ChronoError:
            pass
        else:
            job = self.object.mark_as_finalized()

        if job and job.status == 'registered':
            return redirect(
                reverse(
                    'lingo-manager-invoicing-campaign-job-detail',
                    args=[self.regie.pk, self.object.pk, job.uuid],
                )
            )
        return redirect(
            '%s#open:pools'
            % reverse('lingo-manager-invoicing-campaign-detail', args=[self.regie.pk, self.object.pk])
        )


campaign_finalize = CampaignFinalizeView.as_view()


class CorrectiveCampaignAddView(CanBeInvoicedCheckMixin, CreateView):
    template_name = 'lingo/invoicing/manager_corrective_campaign_form.html'
    form_class = CorrectiveCampaignForm

    def dispatch(self, request, *args, **kwargs):
        self.regie = get_object_or_404(Regie, pk=kwargs['regie_pk'])
        self.check_object(self.regie)
        self.from_campaign = get_object_or_404(
            Campaign,
            regie=self.regie,
            finalized=True,
            pk=kwargs['pk'],
        )
        if not self.from_campaign.is_last:
            raise Http404
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['from_campaign'] = self.from_campaign
        return kwargs

    def get_context_data(self, **kwargs):
        kwargs['regie'] = self.regie
        kwargs['object'] = self.from_campaign
        return super().get_context_data(**kwargs)

    def get_success_url(self):
        return '%s#open:pools' % reverse(
            'lingo-manager-invoicing-campaign-detail', args=[self.regie.pk, self.object.pk]
        )


corrective_campaign_add = CorrectiveCampaignAddView.as_view()


class CorrectiveCampaignAllAgendasAddView(CanBeInvoicedCheckMixin, FormView):
    template_name = 'lingo/invoicing/manager_corrective_campaign_add_agendas_form.html'
    kind = 'all'

    def set_agendas(self):
        self.agendas = self.primary_campaign.agendas.filter(
            pk__in=AgendaUnlockLog.objects.filter(campaign=self.primary_campaign, active=True).values(
                'agenda'
            )
        )

    def dispatch(self, request, *args, **kwargs):
        self.regie = get_object_or_404(Regie, pk=kwargs['regie_pk'])
        self.check_object(self.regie)
        self.primary_campaign = get_object_or_404(
            Campaign,
            regie=self.regie,
            finalized=True,
            primary_campaign__isnull=True,
            pk=kwargs['pk'],
        )
        self.set_agendas()
        for agenda in self.agendas:
            assert agenda in self.primary_campaign.agendas.all()
        self.running_corrective = (
            Campaign.objects.filter(primary_campaign=self.primary_campaign, finalized=False)
            .order_by('created_at')
            .first()
        )
        if self.running_corrective and self.running_corrective.pool_set.filter(draft=False).exists():
            # corrective campaign is not finalized, but a final pool exists
            messages.error(
                self.request,
                _('Not possible to update current corrective campaign, invoices have been generated.'),
            )
            return redirect(
                '%s#open:pools'
                % reverse(
                    'lingo-manager-invoicing-campaign-detail',
                    args=[self.regie.pk, self.running_corrective.pk],
                )
            )
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        kwargs['form'] = None
        kwargs['regie'] = self.regie
        kwargs['object'] = self.primary_campaign
        kwargs['agendas'] = self.agendas
        kwargs['kind'] = self.kind
        kwargs['has_running_corrective'] = self.running_corrective is not None
        return super().get_context_data(**kwargs)

    def post(self, request, *args, **kwargs):
        if self.running_corrective:
            self.running_corrective.agendas.add(*self.agendas)
            self.running_corrective.invalid = True
            self.running_corrective.save()
            AgendaUnlockLog.objects.filter(
                campaign=self.primary_campaign, agenda__in=self.agendas, active=True
            ).update(active=False, updated_at=now())
            return redirect(
                '%s#open:pools'
                % reverse(
                    'lingo-manager-invoicing-campaign-detail',
                    args=[self.regie.pk, self.running_corrective.pk],
                )
            )
        last_corrective = (
            Campaign.objects.filter(primary_campaign=self.primary_campaign, finalized=True)
            .order_by('created_at')
            .last()
        )
        form = CorrectiveCampaignForm(
            from_campaign=last_corrective or self.primary_campaign,
            data=MultiValueDict({'agendas': [a.pk for a in self.agendas]}),
        )
        form.is_valid()
        corrective_campaign = form.save()
        return redirect(
            '%s#open:pools'
            % reverse('lingo-manager-invoicing-campaign-detail', args=[self.regie.pk, corrective_campaign.pk])
        )


corrective_campaign_all_agendas_add = CorrectiveCampaignAllAgendasAddView.as_view()


class CorrectiveCampaignAgendaAddView(CorrectiveCampaignAllAgendasAddView):
    kind = 'one'

    def set_agendas(self):
        self.agendas = self.primary_campaign.agendas.filter(
            pk__in=AgendaUnlockLog.objects.filter(
                campaign=self.primary_campaign, agenda=self.kwargs['agenda_pk'], active=True
            ).values('agenda')
        )


corrective_campaign_agenda_add = CorrectiveCampaignAgendaAddView.as_view()


class CampaignEventAmountsODSView(CanBeInvoicedCheckMixin, FormView):
    template_name = 'lingo/invoicing/manager_campaign_event_amounts_form.html'
    model = Campaign
    form_class = CampaignEventAmountsODSForm

    def dispatch(self, request, *args, **kwargs):
        self.regie = get_object_or_404(Regie, pk=kwargs['regie_pk'])
        self.check_object(self.regie)
        self.object = get_object_or_404(
            Campaign.objects.exclude(corrective_campaigns__finalized=False),
            regie=self.regie,
            finalized=True,
            primary_campaign__isnull=True,
            pk=kwargs['pk'],
        )
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        kwargs['regie'] = self.regie
        kwargs['object'] = self.object
        return super().get_context_data(**kwargs)

    def get_check_types(self):
        check_types_qs = CheckType.objects.filter(
            group__agenda__in=self.object.agendas.all(), kind='presence'
        )
        check_types = {}
        for agenda in self.object.agendas.all():
            check_types[agenda.slug] = {}
            for check_type in check_types_qs:
                if check_type.group_id != agenda.check_type_group_id:
                    continue
                check_types[agenda.slug][check_type.slug] = check_type
        return check_types

    def form_valid(self, form):
        last_corrective = self.object.corrective_campaigns.order_by('pk').last()
        campaign = self.object
        if last_corrective:
            campaign = last_corrective
        pool = campaign.pool_set.get(draft=False)
        response = HttpResponse(content_type='application/vnd.oasis.opendocument.spreadsheet')
        response['Content-Disposition'] = (
            'attachment; filename="campaign-%s-event-amounts.ods"' % self.object.pk
        )
        writer = Workbook()
        headers = [
            _('Payer external ID'),
            _('Payer first name'),
            _('Payer last name'),
            _('Payer address'),
            _('Payer email'),
            _('Payer phone'),
            _('User external ID'),
            _('User first name'),
            _('User last name'),
            _('Event date'),
            _('Activity'),
        ]
        extra_data_keys = form.get_extra_data_keys()
        for key in extra_data_keys:
            headers.append(key)
        headers += [
            _('Amount'),
            _('Booking status'),
        ]
        writer.writerow(
            headers,
            headers=True,
        )
        line_queryset = JournalLine.objects.filter(
            pool=pool,
            pricing_data__booking_details__status='presence',
            status='success',
        )
        check_types = self.get_check_types()
        for line in line_queryset.iterator(chunk_size=1000):
            check_status = line.pricing_data['booking_details'].get('check_type')
            check_type = check_types.get(line.event.get('agenda'), {}).get(check_status)
            row = [
                line.payer_external_id,
                line.payer_first_name,
                line.payer_last_name,
                line.payer_address,
                line.payer_email,
                line.payer_phone,
                line.user_external_id,
                line.user_first_name,
                line.user_last_name,
                line.event_date,
                line.event.get('agenda'),
            ]
            for key in extra_data_keys:
                row.append(line.booking.get('extra_data', {}).get(key))
            row += [
                decimal.Decimal(line.pricing_data['pricing']),
                (check_type.code or check_type.slug) if check_type else 'P',
            ]
            writer.writerow(row)
        writer.save(response)
        return response


campaign_event_amounts_ods = CampaignEventAmountsODSView.as_view()


class CampaignAsyncJobDetailView(CanBeInvoicedCheckMixin, DetailView):
    model = CampaignAsyncJob
    template_name = 'lingo/invoicing/manager_job_detail.html'
    pk_url_kwarg = 'job_uuid'

    def dispatch(self, request, *args, **kwargs):
        self.regie = get_object_or_404(Regie, pk=kwargs['regie_pk'])
        self.check_object(self.regie)
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .filter(campaign__regie=self.regie, campaign__pk=self.kwargs['pk'])
            .select_related('campaign')
        )

    def get_context_data(self, **kwargs):
        job = self.object
        kwargs['regie'] = self.regie
        kwargs['object'] = job.campaign
        kwargs['job'] = job
        kwargs.update(job.check_completion())
        return super().get_context_data(**kwargs)


campaign_job_detail = CampaignAsyncJobDetailView.as_view()
