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

import json
from operator import itemgetter

from django import forms
from django.contrib import messages
from django.db.models import Q
from django.http import Http404, HttpResponse, HttpResponseBadRequest, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.urls import reverse, reverse_lazy
from django.utils.encoding import force_str
from django.utils.timezone import now
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext, pgettext
from django.views.generic import (
    CreateView,
    DeleteView,
    DetailView,
    FormView,
    ListView,
    RedirectView,
    UpdateView,
)
from django.views.generic.detail import SingleObjectMixin

from lingo.agendas.chrono import refresh_agendas
from lingo.agendas.models import Agenda, CheckType, CheckTypeGroup
from lingo.agendas.views import AgendaMixin
from lingo.export_import.views import WithApplicationsMixin
from lingo.manager.utils import (
    CanBeManagedCheckMixin,
    CanBeManagedRequiredMixin,
    CanBeViewedRequiredMixin,
    StaffRequiredMixin,
)
from lingo.pricing.forms import (
    CheckTypeForm,
    CheckTypeGroupUnexpectedPresenceForm,
    CheckTypeGroupUnjustifiedAbsenceForm,
    CriteriaForm,
    ExportForm,
    ImportForm,
    NewCheckTypeForm,
    NewCriteriaForm,
    NewPricingForm,
    PricingAgendaAddForm,
    PricingBillingDateForm,
    PricingCriteriaCategoryAddForm,
    PricingCriteriaCategoryEditForm,
    PricingDuplicateForm,
    PricingForm,
    PricingMatrixForm,
    PricingPricingOptionsForm,
    PricingTestToolForm,
    PricingVariableFormSet,
)
from lingo.pricing.models import BillingDate, Criteria, CriteriaCategory, Pricing, PricingCriteriaCategory
from lingo.pricing.utils import export_site, import_site
from lingo.snapshot.models import (
    AgendaSnapshot,
    CheckTypeGroupSnapshot,
    CriteriaCategorySnapshot,
    PricingSnapshot,
)
from lingo.snapshot.views import InstanceWithSnapshotHistoryCompareView, InstanceWithSnapshotHistoryView
from lingo.utils.misc import LingoImportError, json_dump


class ConfigExportView(StaffRequiredMixin, FormView):
    form_class = ExportForm
    template_name = 'lingo/pricing/export.html'

    def form_valid(self, form):
        response = HttpResponse(content_type='application/json')
        response['Content-Disposition'] = 'attachment; filename="export_pricing_config_{}.json"'.format(
            now().strftime('%Y%m%d')
        )
        json_dump(export_site(**form.cleaned_data), response, indent=2)
        return response


config_export = ConfigExportView.as_view()


class ConfigImportView(StaffRequiredMixin, FormView):
    form_class = ImportForm
    template_name = 'lingo/pricing/import.html'
    success_url = reverse_lazy('lingo-manager-pricing-list')

    def form_valid(self, form):
        try:
            config_json = json.loads(force_str(self.request.FILES['config_json'].read()))
        except ValueError:
            form.add_error('config_json', _('File is not in the expected JSON format.'))
            return self.form_invalid(form)

        try:
            results = import_site(config_json)
        except LingoImportError as exc:
            form.add_error('config_json', '%s' % exc)
            return self.form_invalid(form)
        except KeyError as exc:
            form.add_error('config_json', _('Key "%s" is missing.') % exc.args[0])
            return self.form_invalid(form)

        import_messages = {
            'agendas': {
                'update_noop': _('No agenda updated.'),
                'update': lambda x: ngettext(
                    'An agenda has been updated.',
                    '%(count)d agendas have been updated.',
                    x,
                ),
            },
            'check_type_groups': {
                'create_noop': _('No check type group created.'),
                'create': lambda x: ngettext(
                    'A check type group has been created.',
                    '%(count)d check type groups have been created.',
                    x,
                ),
                'update_noop': _('No check type group updated.'),
                'update': lambda x: ngettext(
                    'A check type group has been updated.',
                    '%(count)d check type groups have been updated.',
                    x,
                ),
            },
            'pricing_categories': {
                'create_noop': _('No pricing criteria category created.'),
                'create': lambda x: ngettext(
                    'A pricing criteria category has been created.',
                    '%(count)d pricing criteria categories have been created.',
                    x,
                ),
                'update_noop': _('No pricing criteria category updated.'),
                'update': lambda x: ngettext(
                    'A pricing criteria category has been updated.',
                    '%(count)d pricing criteria categories have been updated.',
                    x,
                ),
            },
            'pricings': {
                'create_noop': _('No pricing created.'),
                'create': lambda x: ngettext(
                    'A pricing has been created.',
                    '%(count)d pricings have been created.',
                    x,
                ),
                'update_noop': _('No pricing updated.'),
                'update': lambda x: ngettext(
                    'A pricing has been updated.',
                    '%(count)d pricings have been updated.',
                    x,
                ),
            },
        }

        global_noop = True
        for obj_name, obj_results in results.items():
            for obj in obj_results['all']:
                obj.take_snapshot(request=self.request, comment=_('imported'))
            if obj_results['all']:
                global_noop = False
                count = len(obj_results['created'])
                if not count:
                    message1 = import_messages[obj_name].get('create_noop')
                else:
                    message1 = import_messages[obj_name]['create'](count) % {'count': count}

                count = len(obj_results['updated'])
                if not count:
                    message2 = import_messages[obj_name]['update_noop']
                else:
                    message2 = import_messages[obj_name]['update'](count) % {'count': count}

                if message1:
                    obj_results['messages'] = '%s %s' % (message1, message2)
                else:
                    obj_results['messages'] = message2

        a_count, ct_count, pc_count, p_count = (
            len(results['agendas']['all']),
            len(results['check_type_groups']['all']),
            len(results['pricing_categories']['all']),
            len(results['pricings']['all']),
        )
        if (a_count, ct_count, pc_count, p_count) == (1, 0, 0, 0):
            # only one agenda imported, redirect to agenda page
            return HttpResponseRedirect(
                reverse(
                    'lingo-manager-agenda-detail',
                    kwargs={'pk': results['agendas']['all'][0].pk},
                )
            )
        if (a_count, ct_count, pc_count, p_count) == (0, 1, 0, 0):
            # only one check type group imported, redirect to check type page
            return HttpResponseRedirect(reverse('lingo-manager-check-type-list'))
        if (a_count, ct_count, pc_count, p_count) == (0, 0, 1, 0):
            # only one criteria category imported, redirect to criteria page
            return HttpResponseRedirect(reverse('lingo-manager-pricing-criteria-list'))
        if (a_count, ct_count, pc_count, p_count) == (0, 0, 0, 1):
            # only one pricing imported, redirect to pricing model page
            return HttpResponseRedirect(
                reverse(
                    'lingo-manager-pricing-detail',
                    kwargs={'pk': results['pricings']['all'][0].pk},
                )
            )

        if global_noop:
            messages.info(self.request, _('No data found.'))
        else:
            messages.info(self.request, results['agendas']['messages'])
            messages.info(self.request, results['check_type_groups']['messages'])
            messages.info(self.request, results['pricing_categories']['messages'])
            messages.info(self.request, results['pricings']['messages'])

        return super().form_valid(form)


config_import = ConfigImportView.as_view()


class CriteriaListView(StaffRequiredMixin, WithApplicationsMixin, ListView):
    template_name = 'lingo/pricing/manager_criteria_list.html'
    model = CriteriaCategory

    def dispatch(self, request, *args, **kwargs):
        self.with_applications_dispatch(request)
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        queryset = self.with_applications_queryset()
        return queryset.prefetch_related('criterias')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        return self.with_applications_context_data(context)


criteria_list = CriteriaListView.as_view()


class CriteriaCategoryAddView(StaffRequiredMixin, CreateView):
    template_name = 'lingo/pricing/manager_criteria_category_form.html'
    model = CriteriaCategory
    fields = ['label']

    def get_success_url(self):
        return reverse('lingo-manager-pricing-criteria-list')

    def form_valid(self, *args, **kwargs):
        response = super().form_valid(*args, **kwargs)
        self.object.take_snapshot(request=self.request, comment=pgettext('snapshot', 'created'))
        return response


criteria_category_add = CriteriaCategoryAddView.as_view()


class CriteriaCategoryEditView(StaffRequiredMixin, UpdateView):
    template_name = 'lingo/pricing/manager_criteria_category_form.html'
    model = CriteriaCategory
    fields = ['label', 'slug']

    def get_success_url(self):
        return reverse('lingo-manager-pricing-criteria-list')

    def form_valid(self, *args, **kwargs):
        response = super().form_valid(*args, **kwargs)
        self.object.take_snapshot(request=self.request)
        return response


criteria_category_edit = CriteriaCategoryEditView.as_view()


class CriteriaCategoryDeleteView(StaffRequiredMixin, DeleteView):
    template_name = 'lingo/manager_confirm_delete.html'
    model = CriteriaCategory

    def get_success_url(self):
        return reverse('lingo-manager-pricing-criteria-list')

    def post(self, *args, **kwargs):
        self.get_object().take_snapshot(request=self.request, deletion=True)
        return super().post(*args, **kwargs)


criteria_category_delete = CriteriaCategoryDeleteView.as_view()


class CriteriaCategoryExport(StaffRequiredMixin, DetailView):
    model = CriteriaCategory

    def get(self, request, *args, **kwargs):
        response = HttpResponse(content_type='application/json')
        attachment = 'attachment; filename="export_pricing_category_{}_{}.json"'.format(
            self.get_object().slug, now().strftime('%Y%m%d')
        )
        response['Content-Disposition'] = attachment
        json_dump({'pricing_categories': [self.get_object().export_json()]}, response, indent=2)
        return response


criteria_category_export = CriteriaCategoryExport.as_view()


class CriteriaOrder(StaffRequiredMixin, DetailView):
    model = CriteriaCategory

    def get(self, request, *args, **kwargs):
        if 'new-order' not in request.GET:
            return HttpResponseBadRequest('missing new-order parameter')
        category = self.get_object()
        try:
            new_order = [int(x) for x in request.GET['new-order'].split(',')]
        except ValueError:
            return HttpResponseBadRequest('incorrect new-order parameter')
        criterias = category.criterias.filter(default=False)
        if set(new_order) != {x.pk for x in criterias} or len(new_order) != len(criterias):
            return HttpResponseBadRequest('incorrect new-order parameter')
        criterias_by_id = {c.pk: c for c in criterias}
        for i, c_id in enumerate(new_order):
            criterias_by_id[c_id].order = i + 1
            criterias_by_id[c_id].save()
        category.take_snapshot(request=self.request, comment=_('reordered criterias'))
        return HttpResponse(status=204)


criteria_order = CriteriaOrder.as_view()


class CriteriaAddView(StaffRequiredMixin, CreateView):
    template_name = 'lingo/pricing/manager_criteria_form.html'
    model = Criteria
    form_class = NewCriteriaForm

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        if not kwargs.get('instance'):
            kwargs['instance'] = self.model()
        self.category_pk = self.kwargs.pop('category_pk')
        kwargs['instance'].category_id = self.category_pk
        return kwargs

    def get_success_url(self):
        return reverse('lingo-manager-pricing-criteria-list')

    def form_valid(self, form):
        response = super().form_valid(form)
        self.object.category.take_snapshot(request=self.request, comment=_('added criteria'))
        return response


criteria_add = CriteriaAddView.as_view()


class CriteriaEditView(StaffRequiredMixin, UpdateView):
    template_name = 'lingo/pricing/manager_criteria_form.html'
    model = Criteria
    form_class = CriteriaForm

    def get_queryset(self):
        self.category_pk = self.kwargs.pop('category_pk')
        return Criteria.objects.filter(category=self.category_pk)

    def get_success_url(self):
        return reverse('lingo-manager-pricing-criteria-list')

    def form_valid(self, form):
        response = super().form_valid(form)
        self.object.category.take_snapshot(request=self.request, comment=_('changed criteria'))
        return response


criteria_edit = CriteriaEditView.as_view()


class CriteriaDeleteView(StaffRequiredMixin, DeleteView):
    template_name = 'lingo/manager_confirm_delete.html'
    model = Criteria

    def get_queryset(self):
        self.category_pk = self.kwargs.pop('category_pk')
        return Criteria.objects.filter(category=self.category_pk)

    def get_success_url(self):
        return reverse('lingo-manager-pricing-criteria-list')

    def post(self, *args, **kwargs):
        response = super().post(*args, **kwargs)
        self.object.category.take_snapshot(request=self.request, comment=_('removed criteria'))
        return response


criteria_delete = CriteriaDeleteView.as_view()


class CriteriaCategoryInspectView(StaffRequiredMixin, DetailView):
    template_name = 'lingo/pricing/manager_criteria_category_inspect.html'
    model = CriteriaCategory
    context_object_name = 'criteria_category'


criteria_category_inspect = CriteriaCategoryInspectView.as_view()


class CriteriaCategoryHistoryView(StaffRequiredMixin, InstanceWithSnapshotHistoryView):
    template_name = 'lingo/pricing/manager_criteria_category_history.html'
    model = CriteriaCategorySnapshot
    instance_context_key = 'criteria_category'


criteria_category_history = CriteriaCategoryHistoryView.as_view()


class CriteriaCategoryHistoryCompareView(StaffRequiredMixin, InstanceWithSnapshotHistoryCompareView):
    template_name = 'lingo/pricing/manager_criteria_category_history_compare.html'
    inspect_template_name = 'lingo/pricing/manager_criteria_category_inspect_fragment.html'
    model = CriteriaCategory
    instance_context_key = 'criteria_category'
    history_view = 'lingo-manager-criteria-category-history'


criteria_category_history_compare = CriteriaCategoryHistoryCompareView.as_view()


class AgendaListView(StaffRequiredMixin, WithApplicationsMixin, ListView):
    template_name = 'lingo/pricing/manager_agenda_list.html'
    model = Agenda

    def dispatch(self, request, *args, **kwargs):
        self.with_applications_dispatch(request)
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        queryset = self.with_applications_queryset()
        return queryset.filter(archived=False).order_by('category_label', 'label')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['with_archives'] = Agenda.objects.filter(archived=True).exists()
        return self.with_applications_context_data(context)


agenda_list = AgendaListView.as_view()


class AgendaArchivedListView(StaffRequiredMixin, ListView):
    template_name = 'lingo/pricing/manager_agenda_archived_list.html'
    model = Agenda

    def get_queryset(self):
        queryset = super().get_queryset()
        return queryset.filter(archived=True).order_by('category_label', 'label')


agenda_archived_list = AgendaArchivedListView.as_view()


class AgendaSyncView(StaffRequiredMixin, RedirectView):
    def get(self, request, *args, **kwargs):
        refresh_agendas()
        messages.info(self.request, _('Agendas refreshed.'))
        return super().get(request, *args, **kwargs)

    def get_redirect_url(self, *args, **kwargs):
        return reverse('lingo-manager-agenda-list')


agenda_sync = AgendaSyncView.as_view()


class AgendaDetailView(StaffRequiredMixin, AgendaMixin, DetailView):
    template_name = 'lingo/pricing/manager_agenda_detail.html'
    model = Agenda

    def get_context_data(self, **kwargs):
        kwargs['pricings'] = Pricing.objects.filter(agendas=self.agenda, flat_fee_schedule=False).order_by(
            'date_start', 'date_end'
        )
        kwargs['pricings_flat'] = Pricing.objects.filter(
            agendas=self.agenda, flat_fee_schedule=True
        ).order_by('date_start', 'date_end')
        return super().get_context_data(**kwargs)


agenda_detail = AgendaDetailView.as_view()


class AgendaDetailRedirectView(RedirectView):
    def get_redirect_url(self, *args, **kwargs):
        agenda = get_object_or_404(Agenda, slug=kwargs['slug'])
        return reverse('lingo-manager-agenda-detail', kwargs={'pk': agenda.pk})


agenda_detail_redirect = AgendaDetailRedirectView.as_view()


class AgendaExport(StaffRequiredMixin, AgendaMixin, DetailView):
    model = Agenda

    def get(self, request, *args, **kwargs):
        response = HttpResponse(content_type='application/json')
        response['Content-Disposition'] = 'attachment; filename="export_pricing_agenda_{}_{}.json"'.format(
            self.get_object().slug, now().strftime('%Y%m%d')
        )
        json_dump({'agendas': [self.get_object().export_json()]}, response, indent=2)
        return response


agenda_export = AgendaExport.as_view()


class AgendaBookingCheckSettingsView(StaffRequiredMixin, AgendaMixin, UpdateView):
    template_name = 'lingo/pricing/manager_agenda_form.html'
    model = Agenda
    fields = ['check_type_group']
    tab_anchor = 'check'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['form_url'] = reverse('lingo-manager-agenda-booking-check-settings', args=[self.agenda.pk])
        context['title'] = _('Configure booking check options')
        return context

    def form_valid(self, *args, **kwargs):
        response = super().form_valid(*args, **kwargs)
        self.object.take_snapshot(request=self.request, comment=_('changed booking check options'))
        return response


agenda_booking_check_settings = AgendaBookingCheckSettingsView.as_view()


class AgendaInvoicingSettingsView(StaffRequiredMixin, AgendaMixin, UpdateView):
    template_name = 'lingo/pricing/manager_agenda_form.html'
    model = Agenda
    fields = ['regie']
    tab_anchor = 'invoicing'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['form_url'] = reverse('lingo-manager-agenda-invoicing-settings', args=[self.agenda.pk])
        context['title'] = _('Configure invoicing options')
        return context

    def form_valid(self, *args, **kwargs):
        response = super().form_valid(*args, **kwargs)
        self.object.take_snapshot(request=self.request, comment=_('changed invoicing options'))
        return response


agenda_invoicing_settings = AgendaInvoicingSettingsView.as_view()


class AgendaInspectView(StaffRequiredMixin, DetailView):
    template_name = 'lingo/pricing/manager_agenda_inspect.html'
    model = Agenda


agenda_inspect = AgendaInspectView.as_view()


class AgendaHistoryView(StaffRequiredMixin, InstanceWithSnapshotHistoryView):
    template_name = 'lingo/pricing/manager_agenda_history.html'
    model = AgendaSnapshot
    instance_context_key = 'agenda'


agenda_history = AgendaHistoryView.as_view()


class AgendaHistoryCompareView(StaffRequiredMixin, InstanceWithSnapshotHistoryCompareView):
    template_name = 'lingo/pricing/manager_agenda_history_compare.html'
    inspect_template_name = 'lingo/pricing/manager_agenda_inspect_fragment.html'
    model = Agenda
    instance_context_key = 'agenda'
    history_view = 'lingo-manager-agenda-history'


agenda_history_compare = AgendaHistoryCompareView.as_view()


class PricingListView(WithApplicationsMixin, ListView):
    template_name = 'lingo/pricing/manager_pricing_list.html'
    model = Pricing

    def dispatch(self, request, *args, **kwargs):
        self.with_applications_dispatch(request)
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        queryset = self.with_applications_queryset()
        if not self.request.user.is_staff:
            group_ids = [x.id for x in self.request.user.groups.all()]
            queryset = queryset.filter(Q(view_role_id__in=group_ids) | Q(edit_role_id__in=group_ids))
        return queryset.order_by('flat_fee_schedule', 'date_start', 'date_end', 'label')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        return self.with_applications_context_data(context)


pricing_list = PricingListView.as_view()


class PricingAddView(StaffRequiredMixin, CreateView):
    template_name = 'lingo/pricing/manager_pricing_form.html'
    model = Pricing
    form_class = NewPricingForm

    def get_success_url(self):
        return reverse('lingo-manager-pricing-detail', args=[self.object.pk])

    def form_valid(self, *args, **kwargs):
        response = super().form_valid(*args, **kwargs)
        self.object.take_snapshot(request=self.request, comment=pgettext('snapshot', 'created'))
        return response


pricing_add = PricingAddView.as_view()


class PricingDetailView(CanBeViewedRequiredMixin, DetailView):
    model = Pricing
    template_name = 'lingo/pricing/manager_pricing_detail.html'

    def get_queryset(self):
        return Pricing.objects.all().prefetch_related('criterias__category')

    def get_context_data(self, **kwargs):
        kwargs['user_can_manage'] = self.object.can_be_managed(self.request.user)
        return super().get_context_data(**kwargs)


pricing_detail = PricingDetailView.as_view()


class PricingParametersView(CanBeViewedRequiredMixin, DetailView):
    model = Pricing
    template_name = 'lingo/pricing/manager_pricing_parameters.html'

    def get_queryset(self):
        return (
            Pricing.objects.all()
            .prefetch_related('criterias__category')
            .select_related('edit_role', 'view_role')
        )

    def get_context_data(self, **kwargs):
        form = PricingTestToolForm(pricing=self.object, request=self.request, data=self.request.GET or None)
        if self.request.GET:
            form.is_valid()
        kwargs['test_tool_form'] = form
        kwargs['billing_dates'] = self.object.billingdates.order_by('date_start')
        kwargs['user_can_manage'] = self.object.can_be_managed(self.request.user)
        return super().get_context_data(**kwargs)


pricing_parameters = PricingParametersView.as_view()


class PricingTestToolView(RedirectView):
    def get_redirect_url(self, *args, **kwargs):
        return '%s?%s#open:debug' % (
            reverse('lingo-manager-pricing-parameters', args=[kwargs['pk']]),
            self.request.GET.urlencode(),
        )


pricing_test_tool = PricingTestToolView.as_view()


class PricingEditView(CanBeManagedRequiredMixin, UpdateView):
    template_name = 'lingo/pricing/manager_pricing_form.html'
    model = Pricing
    form_class = PricingForm

    def get_success_url(self):
        return reverse('lingo-manager-pricing-parameters', args=[self.object.pk])

    def form_valid(self, *args, **kwargs):
        response = super().form_valid(*args, **kwargs)
        self.object.take_snapshot(request=self.request)
        return response


pricing_edit = PricingEditView.as_view()


class PricingVariableEdit(CanBeManagedCheckMixin, FormView):
    template_name = 'lingo/pricing/manager_pricing_variable_form.html'
    model = Pricing
    form_class = PricingVariableFormSet

    def dispatch(self, request, *args, **kwargs):
        self.object = get_object_or_404(Pricing, pk=kwargs['pk'])
        self.check_object(self.object)
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        kwargs['object'] = self.object
        return super().get_context_data(**kwargs)

    def get_initial(self):
        return sorted(
            ({'key': k, 'value': v} for k, v in self.object.extra_variables.items()),
            key=itemgetter('key'),
        )

    def form_valid(self, form):
        self.object.extra_variables = {}
        for sub_data in form.cleaned_data:
            if not sub_data.get('key'):
                continue
            self.object.extra_variables[sub_data['key']] = sub_data['value']
        self.object.save()
        self.object.take_snapshot(request=self.request, comment=_('changed variables'))
        return HttpResponseRedirect(self.get_success_url())

    def get_success_url(self):
        return '%s#open:variables' % reverse('lingo-manager-pricing-parameters', args=[self.object.pk])


pricing_variable_edit = PricingVariableEdit.as_view()


class PricingPermissionsEditView(CanBeManagedRequiredMixin, UpdateView):
    template_name = 'lingo/pricing/manager_pricing_permissions_form.html'
    model = Pricing
    fields = ['edit_role', 'view_role']

    def get_success_url(self):
        return '%s#open:permissions' % reverse('lingo-manager-pricing-parameters', args=[self.object.pk])

    def form_valid(self, *args, **kwargs):
        response = super().form_valid(*args, **kwargs)
        self.object.take_snapshot(request=self.request, comment=_('changed permissions'))
        return response


pricing_permissions_edit = PricingPermissionsEditView.as_view()


class PricingCriteriaCategoryAddView(CanBeManagedCheckMixin, FormView):
    template_name = 'lingo/pricing/manager_pricing_criteria_category_form.html'
    model = Pricing
    form_class = PricingCriteriaCategoryAddForm

    def dispatch(self, request, *args, **kwargs):
        self.object = get_object_or_404(Pricing, pk=kwargs['pk'])
        self.check_object(self.object)
        if self.object.categories.count() >= 3:
            raise Http404
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['pricing'] = self.object
        return kwargs

    def get_context_data(self, **kwargs):
        kwargs['object'] = self.object
        return super().get_context_data(**kwargs)

    def form_valid(self, form):
        PricingCriteriaCategory.objects.create(pricing=self.object, category=form.cleaned_data['category'])
        response = super().form_valid(form)
        self.object.take_snapshot(request=self.request, comment=_('added criteria category'))
        return response

    def get_success_url(self):
        return '%s#open:criterias' % reverse('lingo-manager-pricing-parameters', args=[self.object.pk])


pricing_criteria_category_add = PricingCriteriaCategoryAddView.as_view()


class PricingCriteriaCategoryEditView(CanBeManagedCheckMixin, FormView):
    template_name = 'lingo/pricing/manager_pricing_criteria_category_form.html'
    model = Pricing
    form_class = PricingCriteriaCategoryEditForm

    def dispatch(self, request, *args, **kwargs):
        self.object = get_object_or_404(Pricing, pk=kwargs['pk'])
        self.check_object(self.object)
        self.category = get_object_or_404(self.object.categories, pk=kwargs['category_pk'])
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['pricing'] = self.object
        kwargs['category'] = self.category
        return kwargs

    def get_context_data(self, **kwargs):
        kwargs['object'] = self.object
        kwargs['category'] = self.category
        return super().get_context_data(**kwargs)

    def form_valid(self, form):
        old_criterias = self.object.criterias.filter(category=self.category)
        new_criterias = form.cleaned_data['criterias']
        removed_criterias = set(old_criterias) - set(new_criterias)
        self.object.criterias.remove(*removed_criterias)
        self.object.criterias.add(*new_criterias)
        response = super().form_valid(form)
        self.object.take_snapshot(request=self.request, comment=_('changed criteria category'))
        return response

    def get_success_url(self):
        return '%s#open:criterias' % reverse('lingo-manager-pricing-parameters', args=[self.object.pk])


pricing_criteria_category_edit = PricingCriteriaCategoryEditView.as_view()


class PricingCriteriaCategoryDeleteView(CanBeManagedCheckMixin, DeleteView):
    template_name = 'lingo/manager_confirm_delete.html'
    model = CriteriaCategory
    pk_url_kwarg = 'category_pk'

    def dispatch(self, request, *args, **kwargs):
        self.pricing = get_object_or_404(Pricing, pk=kwargs['pk'])
        self.check_object(self.pricing)
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return self.pricing.categories.all()

    def form_valid(self, form):
        self.object = self.get_object()
        self.pricing.categories.remove(self.object)
        self.pricing.criterias.remove(*self.pricing.criterias.filter(category=self.object))
        self.pricing.take_snapshot(request=self.request, comment=_('removed criteria category'))
        return HttpResponseRedirect(self.get_success_url())

    def get_success_url(self):
        return '%s#open:criterias' % reverse('lingo-manager-pricing-parameters', args=[self.pricing.pk])


pricing_criteria_category_delete = PricingCriteriaCategoryDeleteView.as_view()


class PricingCriteriaCategoryOrder(CanBeManagedRequiredMixin, DetailView):
    model = Pricing

    def get(self, request, *args, **kwargs):
        pricing = self.get_object()
        if 'new-order' not in request.GET:
            return HttpResponseBadRequest('missing new-order parameter')
        try:
            new_order = [int(x) for x in request.GET['new-order'].split(',')]
        except ValueError:
            return HttpResponseBadRequest('incorrect new-order parameter')
        categories = pricing.categories.all()
        if set(new_order) != {x.pk for x in categories} or len(new_order) != len(categories):
            return HttpResponseBadRequest('incorrect new-order parameter')
        for i, c_id in enumerate(new_order):
            PricingCriteriaCategory.objects.filter(pricing=pricing, category=c_id).update(order=i + 1)
        pricing.take_snapshot(request=self.request, comment=_('reordered criteria categories'))
        return HttpResponse(status=204)


pricing_criteria_category_order = PricingCriteriaCategoryOrder.as_view()


class PricingPricingOptionsEditView(CanBeManagedRequiredMixin, UpdateView):
    template_name = 'lingo/pricing/manager_pricing_pricingoptions_form.html'
    model = Pricing
    form_class = PricingPricingOptionsForm

    def get_queryset(self):
        return super().get_queryset().filter(kind='effort')

    def get_success_url(self):
        return '%s#open:pricing-options' % reverse('lingo-manager-pricing-detail', args=[self.object.pk])

    def form_valid(self, *args, **kwargs):
        response = super().form_valid(*args, **kwargs)
        self.object.take_snapshot(request=self.request, comment=_('changed pricing options'))
        return response


pricing_pricingoptions_edit = PricingPricingOptionsEditView.as_view()


class PricingDeleteView(CanBeManagedRequiredMixin, DeleteView):
    template_name = 'lingo/manager_confirm_delete.html'
    model = Pricing

    def get_success_url(self):
        return reverse('lingo-manager-pricing-list')

    def post(self, *args, **kwargs):
        self.get_object().take_snapshot(request=self.request, deletion=True)
        return super().post(*args, **kwargs)


pricing_delete = PricingDeleteView.as_view()


class PricingExport(CanBeManagedRequiredMixin, DetailView):
    model = Pricing

    def get(self, request, *args, **kwargs):
        response = HttpResponse(content_type='application/json')
        attachment = 'attachment; filename="export_pricing_{}_{}.json"'.format(
            self.get_object().slug, now().strftime('%Y%m%d')
        )
        response['Content-Disposition'] = attachment
        json_dump({'pricings': [self.get_object().export_json()]}, response, indent=2)
        return response


pricing_export = PricingExport.as_view()


class PricingDuplicate(CanBeManagedCheckMixin, SingleObjectMixin, FormView):
    template_name = 'lingo/pricing/manager_pricing_duplicate_form.html'
    model = Pricing
    form_class = PricingDuplicateForm

    def dispatch(self, request, *args, **kwargs):
        self.object = self.get_object()
        self.check_object(self.object)
        return super().dispatch(request, *args, **kwargs)

    def get_success_url(self):
        return reverse('lingo-manager-pricing-detail', kwargs={'pk': self.new_pricing.pk})

    def form_valid(self, form):
        self.new_pricing = self.object.duplicate(
            label=form.cleaned_data['label'],
            date_start=form.cleaned_data['date_start'],
            date_end=form.cleaned_data['date_end'],
        )
        self.new_pricing.take_snapshot(request=self.request, comment=pgettext('snapshot', 'created'))
        return super().form_valid(form)


pricing_duplicate = PricingDuplicate.as_view()


class PricingAgendaAddView(CanBeManagedCheckMixin, FormView):
    template_name = 'lingo/pricing/manager_pricing_agenda_form.html'
    model = Pricing
    form_class = PricingAgendaAddForm

    def dispatch(self, request, *args, **kwargs):
        self.object = get_object_or_404(Pricing, pk=kwargs['pk'], subscription_required=True)
        self.check_object(self.object)
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['pricing'] = self.object
        return kwargs

    def get_context_data(self, **kwargs):
        kwargs['object'] = self.object
        return super().get_context_data(**kwargs)

    def form_valid(self, form):
        self.object.agendas.add(*form.cleaned_data['agendas'])
        self.object.take_snapshot(request=self.request, comment=_('added agendas'))
        return super().form_valid(form)

    def get_success_url(self):
        return '%s#open:agendas' % reverse('lingo-manager-pricing-parameters', args=[self.object.pk])


pricing_agenda_add = PricingAgendaAddView.as_view()


class PricingAgendaDeleteView(CanBeManagedCheckMixin, DeleteView):
    template_name = 'lingo/manager_confirm_delete.html'
    model = Agenda
    pk_url_kwarg = 'agenda_pk'

    def dispatch(self, request, *args, **kwargs):
        self.pricing = get_object_or_404(Pricing, pk=kwargs['pk'], subscription_required=True)
        self.check_object(self.pricing)
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return self.pricing.agendas.all()

    def form_valid(self, form):
        self.object = self.get_object()
        self.pricing.agendas.remove(self.object)
        self.pricing.take_snapshot(request=self.request, comment=_('removed agenda'))
        return HttpResponseRedirect(self.get_success_url())

    def get_success_url(self):
        return '%s#open:agendas' % reverse('lingo-manager-pricing-parameters', args=[self.pricing.pk])


pricing_agenda_delete = PricingAgendaDeleteView.as_view()


class PricingBillingDateAddView(CanBeManagedCheckMixin, FormView):
    template_name = 'lingo/pricing/manager_pricing_billing_date_form.html'
    model = Pricing
    form_class = PricingBillingDateForm

    def dispatch(self, request, *args, **kwargs):
        self.object = get_object_or_404(Pricing, pk=kwargs['pk'], flat_fee_schedule=True)
        self.check_object(self.object)
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['instance'] = BillingDate(pricing=self.object)
        return kwargs

    def get_context_data(self, **kwargs):
        kwargs['object'] = self.object
        return super().get_context_data(**kwargs)

    def form_valid(self, form):
        form.save()
        self.object.take_snapshot(request=self.request, comment=_('added billing date'))
        return super().form_valid(form)

    def get_success_url(self):
        return '%s#open:billing-dates' % reverse('lingo-manager-pricing-parameters', args=[self.object.pk])


pricing_billing_date_add = PricingBillingDateAddView.as_view()


class PricingBillingDateEditView(CanBeManagedCheckMixin, FormView):
    template_name = 'lingo/pricing/manager_pricing_billing_date_form.html'
    model = Pricing
    form_class = PricingBillingDateForm

    def dispatch(self, request, *args, **kwargs):
        self.pricing = get_object_or_404(Pricing, pk=kwargs['pk'], flat_fee_schedule=True)
        self.check_object(self.pricing)
        self.object = get_object_or_404(self.pricing.billingdates, pk=kwargs['billing_date_pk'])
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['instance'] = self.object
        return kwargs

    def get_context_data(self, **kwargs):
        kwargs['object'] = self.pricing
        return super().get_context_data(**kwargs)

    def form_valid(self, form):
        form.save()
        self.pricing.take_snapshot(request=self.request, comment=_('changed billing date'))
        return super().form_valid(form)

    def get_success_url(self):
        return '%s#open:billing-dates' % reverse('lingo-manager-pricing-parameters', args=[self.pricing.pk])


pricing_billing_date_edit = PricingBillingDateEditView.as_view()


class PricingBillingDateDeleteView(CanBeManagedCheckMixin, DeleteView):
    template_name = 'lingo/manager_confirm_delete.html'
    model = BillingDate
    pk_url_kwarg = 'billing_date_pk'

    def dispatch(self, request, *args, **kwargs):
        self.pricing = get_object_or_404(Pricing, pk=kwargs['pk'], subscription_required=True)
        self.check_object(self.pricing)
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return self.pricing.billingdates.all()

    def form_valid(self, form):
        self.get_object().delete()
        self.pricing.take_snapshot(request=self.request, comment=_('removed billing date'))
        return HttpResponseRedirect(self.get_success_url())

    def get_success_url(self):
        return '%s#open:billing-dates' % reverse('lingo-manager-pricing-parameters', args=[self.pricing.pk])


pricing_billing_date_delete = PricingBillingDateDeleteView.as_view()


class PricingMatrixEdit(CanBeManagedCheckMixin, FormView):
    template_name = 'lingo/pricing/manager_pricing_matrix_form.html'
    field = 'pricing'
    change_comment = _('changed pricing')

    def get_pricing(self, request, *args, **kwargs):
        self.object = get_object_or_404(Pricing, pk=kwargs['pk'])

    def dispatch(self, request, *args, **kwargs):
        self.get_pricing(request, *args, **kwargs)
        self.check_object(self.object)
        matrix_list = list(getattr(self.object, 'iter_%s_matrix' % self.field)())
        if not matrix_list:
            raise Http404
        self.matrix = None
        if kwargs.get('slug'):
            for matrix in matrix_list:
                if matrix.criteria is None:
                    continue
                if matrix.criteria.slug == kwargs['slug']:
                    self.matrix = matrix
                    break
        else:
            if matrix_list[0].criteria is None:
                self.matrix = matrix_list[0]
        if self.matrix is None:
            raise Http404
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        kwargs['object'] = self.object
        kwargs['matrix'] = self.matrix
        return super().get_context_data(**kwargs)

    def get_form(self):
        count = len(self.matrix.rows)
        PricingMatrixFormSet = forms.formset_factory(
            PricingMatrixForm, min_num=count, max_num=count, extra=0, can_delete=False
        )
        kwargs = {
            'initial': [
                {'crit_%i' % i: cell.value for i, cell in enumerate(row.cells)} for row in self.matrix.rows
            ]
        }
        if self.request.method == 'POST':
            kwargs.update(
                {
                    'data': self.request.POST,
                }
            )
        return PricingMatrixFormSet(form_kwargs={'matrix': self.matrix, 'pricing': self.object}, **kwargs)

    def post(self, *args, **kwargs):
        form = self.get_form()
        if form.is_valid():
            # build prixing_data for this matrix
            formatted_pricing_data = self.object.format_pricing_data(field=self.field)
            for i, sub_data in enumerate(form.cleaned_data):
                row = self.matrix.rows[i]
                for j, cell in enumerate(row.cells):
                    value = sub_data['crit_%s' % j]
                    key = cell.criteria.identifier if cell.criteria else None
                    # get identifiers of 3 categories
                    path = [key, row.criteria.identifier]
                    if self.matrix.criteria:
                        path.append(self.matrix.criteria.identifier)
                    # remove empty values, "key" can be None
                    path = [k for k in path if k]
                    # get key
                    formatted_key = self.object.format_pricing_data_key(path)
                    # update formatted pricing_data
                    formatted_pricing_data[formatted_key] = float(value)
            self.object.set_pricing_data_from_formatted(formatted_pricing_data, field=self.field)
            self.object.save()
            self.object.take_snapshot(request=self.request, comment=self.change_comment)
            return self.form_valid(form)
        else:
            return self.form_invalid(form)

    def get_success_url(self):
        return '%s#open:matrix' % (reverse('lingo-manager-pricing-detail', args=[self.object.pk]),)


pricing_matrix_edit = PricingMatrixEdit.as_view()


class PricingMinMatrixEdit(PricingMatrixEdit):
    template_name = 'lingo/pricing/manager_pricing_min_matrix_form.html'
    field = 'min_pricing'
    change_comment = _('changed minimum pricing')

    def get_pricing(self, request, *args, **kwargs):
        self.object = get_object_or_404(Pricing, pk=kwargs['pk'], kind='reduction')

    def get_success_url(self):
        return '%s#open:min-matrix' % (reverse('lingo-manager-pricing-detail', args=[self.object.pk]),)

    def form_valid(self, *args, **kwargs):
        response = super().form_valid(*args, **kwargs)
        return response


pricing_min_matrix_edit = PricingMinMatrixEdit.as_view()


class PricingInspectView(CanBeManagedRequiredMixin, DetailView):
    template_name = 'lingo/pricing/manager_pricing_inspect.html'
    model = Pricing

    def get_queryset(self):
        return super().get_queryset().select_related('edit_role', 'view_role')


pricing_inspect = PricingInspectView.as_view()


class PricingHistoryView(CanBeManagedCheckMixin, InstanceWithSnapshotHistoryView):
    template_name = 'lingo/pricing/manager_pricing_history.html'
    model = PricingSnapshot
    instance_context_key = 'pricing'

    def dispatch(self, request, *args, **kwargs):
        self.pricing = get_object_or_404(Pricing, pk=kwargs['pk'])
        self.check_object(self.pricing)
        return super().dispatch(request, *args, **kwargs)


pricing_history = PricingHistoryView.as_view()


class PricingHistoryCompareView(CanBeManagedRequiredMixin, InstanceWithSnapshotHistoryCompareView):
    template_name = 'lingo/pricing/manager_pricing_history_compare.html'
    inspect_template_name = 'lingo/pricing/manager_pricing_inspect_fragment.html'
    model = Pricing
    instance_context_key = 'pricing'
    history_view = 'lingo-manager-pricing-history'


pricing_history_compare = PricingHistoryCompareView.as_view()


class CheckTypeListView(StaffRequiredMixin, WithApplicationsMixin, ListView):
    template_name = 'lingo/pricing/manager_check_type_list.html'
    model = CheckTypeGroup

    def dispatch(self, request, *args, **kwargs):
        self.with_applications_dispatch(request)
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        queryset = self.with_applications_queryset()
        return queryset.prefetch_related('check_types')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        return self.with_applications_context_data(context)


check_type_list = CheckTypeListView.as_view()


class CheckTypeGroupAddView(StaffRequiredMixin, CreateView):
    template_name = 'lingo/pricing/manager_check_type_group_form.html'
    model = CheckTypeGroup
    fields = ['label']

    def get_success_url(self):
        return reverse('lingo-manager-check-type-list')

    def form_valid(self, *args, **kwargs):
        response = super().form_valid(*args, **kwargs)
        self.object.take_snapshot(request=self.request, comment=pgettext('snapshot', 'created'))
        return response


check_type_group_add = CheckTypeGroupAddView.as_view()


class CheckTypeGroupEditView(StaffRequiredMixin, UpdateView):
    template_name = 'lingo/pricing/manager_check_type_group_form.html'
    model = CheckTypeGroup
    fields = ['label', 'slug']

    def get_success_url(self):
        return reverse('lingo-manager-check-type-list')

    def form_valid(self, *args, **kwargs):
        response = super().form_valid(*args, **kwargs)
        self.object.take_snapshot(request=self.request)
        return response


check_type_group_edit = CheckTypeGroupEditView.as_view()


class CheckTypeGroupUnexpectedPresenceEditView(StaffRequiredMixin, UpdateView):
    template_name = 'lingo/pricing/manager_check_type_group_form.html'
    model = CheckTypeGroup
    form_class = CheckTypeGroupUnexpectedPresenceForm

    def get_success_url(self):
        return reverse('lingo-manager-check-type-list')

    def form_valid(self, *args, **kwargs):
        response = super().form_valid(*args, **kwargs)
        self.object.take_snapshot(request=self.request)
        return response


check_type_group_unexpected_presence_edit = CheckTypeGroupUnexpectedPresenceEditView.as_view()


class CheckTypeGroupUnjustifiedAbsenceEditView(StaffRequiredMixin, UpdateView):
    template_name = 'lingo/pricing/manager_check_type_group_form.html'
    model = CheckTypeGroup
    form_class = CheckTypeGroupUnjustifiedAbsenceForm

    def get_success_url(self):
        return reverse('lingo-manager-check-type-list')

    def form_valid(self, *args, **kwargs):
        response = super().form_valid(*args, **kwargs)
        self.object.take_snapshot(request=self.request)
        return response


check_type_group_unjustified_absence_edit = CheckTypeGroupUnjustifiedAbsenceEditView.as_view()


class CheckTypeGroupDeleteView(StaffRequiredMixin, DeleteView):
    template_name = 'lingo/manager_confirm_delete.html'
    model = CheckTypeGroup

    def get_success_url(self):
        return reverse('lingo-manager-check-type-list')

    def post(self, *args, **kwargs):
        self.get_object().take_snapshot(request=self.request, deletion=True)
        return super().post(*args, **kwargs)


check_type_group_delete = CheckTypeGroupDeleteView.as_view()


class CheckTypeGroupExport(StaffRequiredMixin, DetailView):
    model = CheckTypeGroup

    def get(self, request, *args, **kwargs):
        response = HttpResponse(content_type='application/json')
        attachment = 'attachment; filename="export_check_type_group_{}_{}.json"'.format(
            self.get_object().slug, now().strftime('%Y%m%d')
        )
        response['Content-Disposition'] = attachment
        json_dump({'check_type_groups': [self.get_object().export_json()]}, response, indent=2)
        return response


check_type_group_export = CheckTypeGroupExport.as_view()


class CheckTypeAddView(StaffRequiredMixin, CreateView):
    template_name = 'lingo/pricing/manager_check_type_form.html'
    model = CheckType
    form_class = NewCheckTypeForm

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        if not kwargs.get('instance'):
            kwargs['instance'] = self.model()
        self.group_pk = self.kwargs.pop('group_pk')
        kwargs['instance'].group_id = self.group_pk
        return kwargs

    def get_success_url(self):
        return reverse('lingo-manager-check-type-list')

    def form_valid(self, *args, **kwargs):
        response = super().form_valid(*args, **kwargs)
        self.object.group.take_snapshot(request=self.request, comment=pgettext('snapshot', 'created'))
        return response


check_type_add = CheckTypeAddView.as_view()


class CheckTypeEditView(StaffRequiredMixin, UpdateView):
    template_name = 'lingo/pricing/manager_check_type_form.html'
    model = CheckType
    form_class = CheckTypeForm

    def get_queryset(self):
        self.group_pk = self.kwargs.pop('group_pk')
        return CheckType.objects.filter(group=self.group_pk)

    def get_success_url(self):
        return reverse('lingo-manager-check-type-list')

    def form_valid(self, *args, **kwargs):
        response = super().form_valid(*args, **kwargs)
        self.object.group.take_snapshot(request=self.request)
        return response


check_type_edit = CheckTypeEditView.as_view()


class CheckTypeDeleteView(StaffRequiredMixin, DeleteView):
    template_name = 'lingo/manager_confirm_delete.html'
    model = CheckType

    def get_queryset(self):
        self.group_pk = self.kwargs['group_pk']
        return CheckType.objects.filter(group=self.group_pk)

    def get_success_url(self):
        return reverse('lingo-manager-check-type-list')

    def post(self, *args, **kwargs):
        group = self.get_object().group
        response = super().post(*args, **kwargs)
        group.refresh_from_db()
        group.take_snapshot(request=self.request)
        return response


check_type_delete = CheckTypeDeleteView.as_view()


class CheckTypeGroupInspectView(StaffRequiredMixin, DetailView):
    template_name = 'lingo/pricing/manager_check_type_group_inspect.html'
    model = CheckTypeGroup
    context_object_name = 'check_type_group'

    def get_queryset(self):
        return super().get_queryset().select_related('unjustified_absence', 'unexpected_presence')


check_type_group_inspect = CheckTypeGroupInspectView.as_view()


class CheckTypeGroupHistoryView(StaffRequiredMixin, InstanceWithSnapshotHistoryView):
    template_name = 'lingo/pricing/manager_check_type_group_history.html'
    model = CheckTypeGroupSnapshot
    instance_context_key = 'check_type_group'


check_type_group_history = CheckTypeGroupHistoryView.as_view()


class CheckTypeGroupHistoryCompareView(StaffRequiredMixin, InstanceWithSnapshotHistoryCompareView):
    template_name = 'lingo/pricing/manager_check_type_group_history_compare.html'
    inspect_template_name = 'lingo/pricing/manager_check_type_group_inspect_fragment.html'
    model = CheckTypeGroup
    instance_context_key = 'check_type_group'
    history_view = 'lingo-manager-check-type-group-history'


check_type_group_history_compare = CheckTypeGroupHistoryCompareView.as_view()
