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

from itertools import groupby

import django_filters
from django import forms
from django.conf import settings
from django.db import transaction
from django.db.models import F, Func, OuterRef, Q, Subquery
from django.db.models.fields.json import KT
from django.db.models.functions import Coalesce
from django.utils.timezone import now
from django.utils.translation import gettext_lazy as _
from django.utils.translation import pgettext
from gadjo.forms.widgets import MultiSelectWidget

from lingo.agendas.models import Agenda, AgendaUnlockLog
from lingo.invoicing.models import (
    DOCUMENT_MODELS,
    ORIGINS,
    AbstractJournalLine,
    Campaign,
    CollectionDocket,
    Credit,
    CreditAssignment,
    CreditCancellationReason,
    CreditLine,
    DraftInvoice,
    DraftInvoiceLine,
    DraftJournalLine,
    InjectedLine,
    Invoice,
    InvoiceCancellationReason,
    InvoiceLine,
    InvoiceLinePayment,
    JournalLine,
    Payment,
    PaymentCancellationReason,
    PaymentDocket,
    PaymentType,
    Refund,
    Regie,
)
from lingo.utils.fields import AgendaSelect, AgendasMultipleChoiceField, CategorySelect
from lingo.utils.wcs import get_wcs_options


class ExportForm(forms.Form):
    regies = forms.BooleanField(label=_('Regies'), required=False, initial=True)


class ImportForm(forms.Form):
    config_json = forms.FileField(label=_('Export File'))


class RegieForm(forms.ModelForm):
    class Meta:
        model = Regie
        fields = [
            'label',
            'slug',
            'with_campaigns',
            'description',
            'assign_credits_on_creation',
        ]

    def clean_slug(self):
        slug = self.cleaned_data['slug']

        if Regie.objects.filter(slug=slug).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError(_('Another regie exists with the same identifier.'))

        return slug


class RegiePayerForm(forms.ModelForm):
    payer_carddef_reference = forms.ChoiceField(
        label=_('Linked card model'),
        required=False,
    )

    class Meta:
        model = Regie
        fields = [
            'payer_carddef_reference',
            'payer_external_id_prefix',
            'payer_external_id_template',
            'payer_external_id_from_nameid_template',
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.instance.with_campaigns:
            self.fields.pop('payer_carddef_reference')
            self.fields.pop('payer_external_id_prefix')
            self.fields.pop('payer_external_id_template')
        else:
            card_models = get_wcs_options('/api/cards/@list')
            self.fields['payer_carddef_reference'].choices = [('', '-----')] + card_models


class RegiePayerMappingForm(forms.ModelForm):
    class Meta:
        model = Regie
        fields = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.instance.payer_cached_carddef_json:
            return
        for key, label in self.instance.payer_user_variables:
            self.fields[key] = forms.ChoiceField(
                label=label,
                choices=[('', '-----')] + [(k, v) for k, v in self.instance.payer_carddef_fields.items()],
                initial=self.instance.payer_user_fields_mapping.get(key),
                required=False,
            )

    def save(self):
        self.instance.payer_user_fields_mapping = {
            k: self.cleaned_data[k] for k, v in self.instance.payer_user_variables
        }
        self.instance.save()
        return self.instance


class RegiePublishingForm(forms.ModelForm):
    class Meta:
        model = Regie
        fields = [
            'main_colour',
            'invoice_model',
            'invoice_custom_text',
            'certificate_model',
            'controller_name',
            'city_name',
            'custom_logo',
            'custom_address',
            'custom_invoice_extra_info',
        ]
        widgets = {
            'main_colour': forms.TextInput(attrs={'type': 'color'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['certificate_model'].choices = [('', '%s %s' % (_('Invoice information:'), _('No')))] + [
            (k, '%s %s' % (_('Invoice information:'), v)) for (k, v) in DOCUMENT_MODELS
        ]


class PaymentTypeForm(forms.ModelForm):
    class Meta:
        model = PaymentType
        fields = ['label', 'slug', 'disabled']

    def clean_slug(self):
        slug = self.cleaned_data['slug']

        if self.instance.regie.paymenttype_set.filter(slug=slug).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError(_('Another payment type exists with the same identifier.'))

        return slug


class CampaignForm(forms.ModelForm):
    category = forms.ChoiceField(
        label=_('Add agendas of the category'),
        choices=(),
        required=False,
        widget=CategorySelect,
    )
    agendas = AgendasMultipleChoiceField(
        label=_('Agendas'),
        queryset=Agenda.objects.none(),
        widget=MultiSelectWidget(form_field=AgendaSelect),
    )

    class Meta:
        model = Campaign
        fields = [
            'label',
            'date_start',
            'date_end',
            'injected_lines',
            'adjustment_campaign',
            'category',
            'agendas',
        ]
        widgets = {
            'date_start': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
            'date_end': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['agendas'].queryset = self.instance.regie.agenda_set.all().order_by(
            'category_label', 'label'
        )
        self.fields['category'].choices = [('', '---------')] + list(
            self.instance.regie.agenda_set.filter(category_slug__isnull=False)
            .values_list('category_slug', 'category_label')
            .order_by('category_label')
            .distinct()
        )
        if not InjectedLine.objects.filter(regie=self.instance.regie).exists():
            del self.fields['injected_lines']
        if self.instance.pk:
            self.initial['agendas'] = self.instance.agendas.order_by('category_label', 'label')

    def clean(self):
        cleaned_data = super().clean()

        if 'date_start' in cleaned_data and 'date_end' in cleaned_data:
            if cleaned_data['date_end'] <= cleaned_data['date_start']:
                self.add_error('date_end', _('End date must be greater than start date.'))
            elif 'agendas' in cleaned_data:
                new_date_start = cleaned_data['date_start']
                new_date_end = cleaned_data['date_end']
                new_agendas = cleaned_data['agendas']
                overlapping_qs = (
                    Campaign.objects.filter(regie=self.instance.regie)
                    .exclude(pk=self.instance.pk)
                    .extra(
                        where=['(date_start, date_end) OVERLAPS (%s, %s)'],
                        params=[new_date_start, new_date_end],
                    )
                )
                for agenda in new_agendas:
                    if overlapping_qs.filter(agendas=agenda).exists():
                        self.add_error(
                            None,
                            _('Agenda "%s" has already a campaign overlapping this period.') % agenda.label,
                        )

        return cleaned_data

    def save(self):
        super().save(commit=False)

        if self.instance._state.adding:
            # init date fields
            self.instance.date_publication = self.instance.date_end
            self.instance.date_payment_deadline = self.instance.date_end
            self.instance.date_due = self.instance.date_end
            self.instance.date_debit = self.instance.date_end
        elif self.instance.pool_set.exists():
            self.instance.mark_as_invalid(commit=False)

        self.instance.save()
        self._save_m2m()

        return self.instance


class CampaignDatesForm(forms.ModelForm):
    class Meta:
        model = Campaign
        fields = [
            'date_publication',
            'date_payment_deadline_displayed',
            'date_payment_deadline',
            'date_due',
            'date_debit',
        ]
        widgets = {
            'date_publication': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
            'date_payment_deadline_displayed': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
            'date_payment_deadline': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
            'date_due': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
            'date_debit': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.finalized:
            self.fields.pop('date_debit')

    def clean(self):
        cleaned_data = super().clean()

        if (
            'date_publication' in cleaned_data
            and 'date_payment_deadline' in cleaned_data
            and 'date_due' in cleaned_data
        ):
            if cleaned_data['date_publication'] > cleaned_data['date_payment_deadline']:
                self.add_error(
                    'date_payment_deadline', _('Payment deadline must be greater than publication date.')
                )
            elif cleaned_data['date_payment_deadline'] > cleaned_data['date_due']:
                self.add_error('date_due', _('Due date must be greater than payment deadline.'))
        return cleaned_data

    def save(self):
        super().save()

        draft_invoice_qs = DraftInvoice.objects.filter(pool__campaign=self.instance)
        invoice_qs = Invoice.objects.filter(pool__campaign=self.instance)

        for qs in [draft_invoice_qs, invoice_qs]:
            qs.update(
                date_publication=self.instance.date_publication,
                date_payment_deadline_displayed=self.instance.date_payment_deadline_displayed,
                date_payment_deadline=self.instance.date_payment_deadline,
                date_due=self.instance.date_due,
            )
            qs.filter(payer_direct_debit=True).update(date_debit=self.instance.date_debit)

        return self.instance


class CorrectiveCampaignForm(forms.ModelForm):
    category = forms.ChoiceField(
        label=_('Add agendas of the category'),
        choices=(),
        required=False,
        widget=CategorySelect,
    )
    agendas = AgendasMultipleChoiceField(
        label=_('Agendas'),
        queryset=Agenda.objects.none(),
        widget=MultiSelectWidget(form_field=AgendaSelect),
    )

    class Meta:
        model = Campaign
        fields = ['category', 'agendas']

    def __init__(self, *args, **kwargs):
        self.from_campaign = kwargs.pop('from_campaign')
        self.primary_campaign = self.from_campaign
        if self.from_campaign.primary_campaign:
            self.primary_campaign = self.from_campaign.primary_campaign
        super().__init__(*args, **kwargs)
        self.fields['agendas'].queryset = self.primary_campaign.agendas.all().order_by(
            'category_label', 'label'
        )
        self.fields['category'].choices = [('', '---------')] + list(
            self.primary_campaign.agendas.filter(category_slug__isnull=False)
            .values_list('category_slug', 'category_label')
            .order_by('category_label')
            .distinct()
        )

    def save(self):
        super().save(commit=False)
        self.instance.label = '%s - %s' % (self.primary_campaign.label, _('Correction'))
        self.instance.regie = self.from_campaign.regie
        self.instance.date_start = self.primary_campaign.date_start
        self.instance.date_end = self.primary_campaign.date_end
        self.instance.injected_lines = self.primary_campaign.injected_lines
        self.instance.adjustment_campaign = self.primary_campaign.adjustment_campaign
        self.instance.invoice_model = self.primary_campaign.invoice_model
        self.instance.invoice_custom_text = self.primary_campaign.invoice_custom_text
        self.instance.primary_campaign = self.primary_campaign
        # take dates of previous corrective campaign if available
        self.instance.date_publication = self.from_campaign.date_publication
        self.instance.date_payment_deadline = self.from_campaign.date_payment_deadline
        self.instance.date_due = self.from_campaign.date_due
        self.instance.date_debit = self.from_campaign.date_debit
        self.instance.save()
        self.instance.agendas.set(self.cleaned_data['agendas'])
        AgendaUnlockLog.objects.filter(
            campaign=self.primary_campaign, agenda__in=self.instance.agendas.all(), active=True
        ).update(active=False, updated_at=now())
        return self.instance


class CampaignEventAmountsODSForm(forms.Form):
    extra_data_keys = forms.CharField(
        label=_('Extra data to add to the report'),
        max_length=250,
        required=False,
        help_text=_('Comma separated list of keys defined in extra_data.'),
    )

    def get_extra_data_keys(self):
        extra_data_keys = self.cleaned_data.get('extra_data_keys').split(',')
        return [d.strip() for d in extra_data_keys if d.strip()]


class AgendaFieldsFilterSetMixin:
    def _init_agenda_fields(self, invoice_queryset):
        # get agendas from slugs
        agenda_slugs = self.line_model.objects.filter(
            **{f'{self.invoice_field}__in': invoice_queryset}
        ).values('agenda_slug')
        agendas = Agenda.objects.filter(slug__in=agenda_slugs).order_by('category_label', 'label')
        # and init agenda filter choices
        self.filters['agenda'].field.choices = [
            (cat, [(agenda.slug, agenda.label) for agenda in group])
            for cat, group in groupby(agendas, key=lambda a: a.category_label or _('Misc'))
        ]
        # get line details to build event filter choices
        agendas_by_slug = {a.slug: a for a in agendas}
        lines = (
            self.line_model.objects.filter(**{f'{self.invoice_field}__in': invoice_queryset})
            .values('event_label', 'event_slug', 'agenda_slug')
            .exclude(agenda_slug='')
            .distinct()
            .order_by()
        )
        events = []
        for line in lines:
            if line['agenda_slug'] not in agendas_by_slug:
                # unknown agenda slug
                continue
            if ':' in line['event_slug']:
                # partial bookings, remove overtaking, reductions and overcharging
                continue
            agenda = agendas_by_slug[line['agenda_slug']]
            events.append(
                (
                    line['event_slug'],
                    '%s / %s' % (agenda.label, line['event_label']),
                    agenda.category_label or _('Misc'),
                    agenda.category_label or 'z' * 10,
                    agenda.label,
                    line['event_label'],
                )
            )
        # build event filter choices
        events = sorted(list(events), key=lambda e: (e[3], e[4], e[5]))
        self.filters['event'].field.choices = [
            (cat, [(e[0], e[1]) for e in group]) for cat, group in groupby(events, key=lambda e: e[2])
        ]

    def filter_agenda(self, queryset, name, value):
        if not value:
            return queryset
        lines = self.line_model.objects.filter(agenda_slug=value).values(self.invoice_field)
        return queryset.filter(pk__in=lines)

    def filter_event(self, queryset, name, value):
        if not value:
            return queryset
        lines = self.line_model.objects.filter(event_slug=value).values(self.invoice_field)
        return queryset.filter(pk__in=lines)


class PoolDeleteForm(forms.Form):
    cancellation_description = forms.CharField(
        label=_('Cancellation description for invoices'),
        widget=forms.Textarea,
        required=False,
    )


class AbstractInvoiceFilterSet(AgendaFieldsFilterSetMixin, django_filters.FilterSet):
    payer_external_id = django_filters.CharFilter(
        label=_('Payer (external ID)'),
    )
    payer_first_name = django_filters.CharFilter(
        label=_('Payer first name'),
        lookup_expr='icontains',
    )
    payer_last_name = django_filters.CharFilter(
        label=_('Payer last name'),
        lookup_expr='icontains',
    )
    user_external_id = django_filters.CharFilter(
        label=_('User (external ID)'),
        method='filter_user_external_id',
    )
    user_first_name = django_filters.CharFilter(
        label=_('User first name'),
        method='filter_user_first_name',
    )
    user_last_name = django_filters.CharFilter(
        label=_('User last name'),
        method='filter_user_last_name',
    )
    total_amount_min = django_filters.LookupChoiceFilter(
        label=_('Total amount min'),
        field_name='total_amount',
        field_class=forms.DecimalField,
        empty_label=None,
        lookup_choices=[
            ('gt', '>'),
            ('gte', '>='),
        ],
    )
    total_amount_max = django_filters.LookupChoiceFilter(
        label=_('Total amount max'),
        field_name='total_amount',
        field_class=forms.DecimalField,
        empty_label=None,
        lookup_choices=[
            ('lt', '<'),
            ('lte', '<='),
        ],
    )
    agenda = django_filters.ChoiceFilter(
        label=_('Activity'),
        empty_label=_('all'),
        method='filter_agenda',
    )
    event = django_filters.ChoiceFilter(
        label=_('Event'),
        empty_label=_('all'),
        method='filter_event',
    )
    accounting_code = django_filters.CharFilter(
        label=_('Accounting code'),
        method='filter_accounting_code',
    )

    def __init__(self, *args, **kwargs):
        self.pool = kwargs.pop('pool')
        super().__init__(*args, **kwargs)

        self._init_agenda_fields(self.queryset)

    def filter_user_external_id(self, queryset, name, value):
        if not value:
            return queryset
        lines = self.line_model.objects.filter(user_external_id=value).values(self.invoice_field)
        return queryset.filter(pk__in=lines)

    def filter_user_first_name(self, queryset, name, value):
        if not value:
            return queryset
        lines = self.line_model.objects.filter(user_first_name__icontains=value).values(self.invoice_field)
        return queryset.filter(pk__in=lines)

    def filter_user_last_name(self, queryset, name, value):
        if not value:
            return queryset
        lines = self.line_model.objects.filter(user_last_name__icontains=value).values(self.invoice_field)
        return queryset.filter(pk__in=lines)

    def filter_accounting_code(self, queryset, name, value):
        if not value:
            return queryset
        lines = self.line_model.objects.filter(accounting_code__iexact=value).values(self.invoice_field)
        return queryset.filter(pk__in=lines)


class DraftInvoiceFilterSet(AbstractInvoiceFilterSet):
    pk = django_filters.NumberFilter(
        label=_('Invoice number'),
    )
    payer_direct_debit = django_filters.BooleanFilter(
        label=_('Payer direct debit'),
    )
    line_model = DraftInvoiceLine
    invoice_field = 'invoice'

    class Meta:
        model = DraftInvoice
        fields = [
            'pk',
            'payer_external_id',
            'payer_first_name',
            'payer_last_name',
            'payer_direct_debit',
            'user_external_id',
            'user_first_name',
            'user_last_name',
            'total_amount_min',
            'total_amount_max',
            'agenda',
            'event',
            'accounting_code',
        ]


class InvoiceFilterSet(AbstractInvoiceFilterSet):
    number = django_filters.CharFilter(
        label=_('Invoice number'),
        field_name='formatted_number',
        lookup_expr='contains',
    )
    payment_number = django_filters.CharFilter(
        label=_('Payment number'),
        method='filter_payment_number',
    )
    payer_direct_debit = django_filters.BooleanFilter(
        label=_('Payer direct debit'),
    )
    paid = django_filters.ChoiceFilter(
        label=_('Paid'),
        widget=forms.RadioSelect,
        empty_label=_('all'),
        choices=[
            ('yes', _('Totally')),
            ('partially', _('Partially')),
            ('no', _('No')),
        ],
        method='filter_paid',
    )
    line_model = InvoiceLine
    invoice_field = 'invoice'

    class Meta:
        model = Invoice
        fields = [
            'number',
            'payment_number',
            'payer_external_id',
            'payer_first_name',
            'payer_last_name',
            'payer_direct_debit',
            'user_external_id',
            'user_first_name',
            'user_last_name',
            'total_amount_min',
            'total_amount_max',
            'paid',
            'agenda',
            'event',
            'accounting_code',
        ]

    def filter_payment_number(self, queryset, name, value):
        line_queryset = InvoiceLine.objects.filter(
            pk__in=InvoiceLinePayment.objects.filter(payment__formatted_number__contains=value).values('line')
        )
        return queryset.filter(pk__in=line_queryset.values(self.invoice_field))

    def filter_paid(self, queryset, name, value):
        if value == 'yes':
            return queryset.filter(remaining_amount=0, total_amount__gt=0)
        if value == 'partially':
            return queryset.filter(remaining_amount__gt=0, paid_amount__gt=0)
        if value == 'no':
            return queryset.filter(paid_amount=0)
        return queryset


class RegieCollectionInvoiceFilterSet(django_filters.FilterSet):
    date_end = django_filters.DateFilter(
        label=_('Stop date'),
        widget=forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
        required=True,
        field_name='date_due',
        lookup_expr='lt',
    )
    minimum_threshold = django_filters.NumberFilter(
        label=_('Minimal threshold'),
        required=True,
        method='filter_minimum_threshold',
    )

    class Meta:
        model = Invoice
        fields = []

    def __init__(self, *args, **kwargs):
        self.regie = kwargs.pop('regie')
        if kwargs.get('data') is None:
            # set initial through data, so form is valid on page load
            kwargs['data'] = {
                'date_end': now().date(),
                'minimum_threshold': 0,
            }
        super().__init__(*args, **kwargs)

    def filter_queryset(self, queryset):
        # filter now, after applying date filter
        queryset = super().filter_queryset(queryset)
        if 'minimum_threshold' not in self.form.cleaned_data:
            return queryset
        value = self.form.cleaned_data['minimum_threshold']
        remaining_amounts = (
            queryset.filter(payer_external_id=OuterRef('payer_external_id'))
            .order_by()
            .annotate(total_remaining=Func(F('remaining_amount'), function='Sum'))
            .values('total_remaining')
        )
        queryset = queryset.annotate(total_remaining=Subquery(remaining_amounts)).filter(
            total_remaining__gte=value
        )
        return queryset

    def filter_minimum_threshold(self, queryset, name, value):
        return queryset


class CollectionDocketForm(forms.ModelForm):
    class Meta:
        model = CollectionDocket
        fields = ['date_end', 'minimum_threshold', 'pay_invoices']
        widgets = {
            'date_end': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
        }

    def __init__(self, *args, **kwargs):
        instance = kwargs['instance']
        if not instance.pk:
            self.regie = kwargs.pop('regie')
            if kwargs.get('data') is None:
                # set initial through data, so form is valid on page load
                kwargs['data'] = {
                    'date_end': now().date(),
                    'minimum_threshold': 0,
                }
        else:
            self.regie = instance.regie
        super().__init__(*args, **kwargs)

    def save(self):
        self.instance = super().save()
        filterset = RegieCollectionInvoiceFilterSet(
            regie=self.regie,
            queryset=Invoice.objects.filter(
                regie=self.regie,
                collection__isnull=True,
                cancelled_at__isnull=True,
                remaining_amount__gt=0,
            ).exclude(pool__campaign__finalized=False),
            data={
                'date_end': self.instance.date_end,
                'minimum_threshold': self.instance.minimum_threshold,
            },
        )
        if filterset.form.is_valid():
            invoice_queryset = filterset.qs
            with transaction.atomic():
                Invoice.objects.filter(regie=self.regie, collection=self.instance).exclude(
                    pk__in=invoice_queryset
                ).update(collection=None)
                invoice_queryset.update(collection=self.instance)
        return self.instance


class DraftCreditFilterSet(AbstractInvoiceFilterSet):
    pk = django_filters.NumberFilter(
        label=_('Credit number'),
    )
    line_model = DraftInvoiceLine
    invoice_field = 'invoice'

    class Meta:
        model = DraftInvoice
        fields = [
            'pk',
            'payer_external_id',
            'payer_first_name',
            'payer_last_name',
            'user_external_id',
            'user_first_name',
            'user_last_name',
            'total_amount_min',
            'total_amount_max',
            'agenda',
            'event',
            'accounting_code',
        ]


class CreditFilterSet(AbstractInvoiceFilterSet):
    number = django_filters.CharFilter(
        label=_('Credit number'),
        field_name='formatted_number',
        lookup_expr='contains',
    )
    payment_number = django_filters.CharFilter(
        label=_('Payment number'),
        method='filter_payment_number',
    )
    assigned = django_filters.ChoiceFilter(
        label=_('Paid'),
        widget=forms.RadioSelect,
        empty_label=_('all'),
        choices=[
            ('yes', _('Totally')),
            ('partially', _('Partially')),
            ('no', _('No')),
        ],
        method='filter_assigned',
    )
    line_model = CreditLine
    invoice_field = 'credit'

    class Meta:
        model = Credit
        fields = [
            'number',
            'payment_number',
            'payer_external_id',
            'payer_first_name',
            'payer_last_name',
            'user_external_id',
            'user_first_name',
            'user_last_name',
            'total_amount_min',
            'total_amount_max',
            'assigned',
            'agenda',
            'event',
            'accounting_code',
        ]

    def filter_payment_number(self, queryset, name, value):
        assignment_queryset = CreditAssignment.objects.filter(
            payment__formatted_number__contains=value
        ).values('credit')
        return queryset.filter(pk__in=assignment_queryset)

    def filter_assigned(self, queryset, name, value):
        if value == 'yes':
            return queryset.filter(remaining_amount=0, total_amount__gt=0)
        if value == 'partially':
            return queryset.filter(remaining_amount__gt=0, assigned_amount__gt=0)
        if value == 'no':
            return queryset.filter(assigned_amount=0)
        return queryset


class AbstractJournalLineFilterSet(django_filters.FilterSet):
    # for JournalLine
    invoice_number = django_filters.CharFilter(
        label=_('Invoice number'),
        field_name='invoice_line__invoice__formatted_number',
        lookup_expr='contains',
    )
    credit_number = django_filters.CharFilter(
        label=_('Credit number'),
        field_name='credit_line__credit__formatted_number',
        lookup_expr='contains',
    )
    # for DraftJournalLine
    invoice_id = django_filters.NumberFilter(
        label=_('Invoice/credit number'),
        field_name='invoice_line__invoice_id',
    )
    invoice_line = django_filters.NumberFilter(
        label=_('Invoice line'),
    )
    credit_line = django_filters.NumberFilter(
        label=_('Credit line'),
    )
    payer_external_id = django_filters.CharFilter(
        label=_('Payer (external ID)'),
    )
    payer_first_name = django_filters.CharFilter(
        label=_('Payer first name'),
        lookup_expr='icontains',
    )
    payer_last_name = django_filters.CharFilter(
        label=_('Payer last name'),
        lookup_expr='icontains',
    )
    payer_direct_debit = django_filters.BooleanFilter(
        label=_('Payer direct debit'),
    )
    user_external_id = django_filters.CharFilter(
        label=_('User (external ID)'),
    )
    user_first_name = django_filters.CharFilter(
        label=_('User first name'),
        lookup_expr='icontains',
    )
    user_last_name = django_filters.CharFilter(
        label=_('User last name'),
        lookup_expr='icontains',
    )
    agenda = django_filters.ChoiceFilter(
        label=_('Activity'),
        empty_label=_('all'),
        method='filter_agenda',
    )
    event = django_filters.ChoiceFilter(
        label=_('Event'),
        empty_label=_('all'),
        method='filter_event',
    )
    accounting_code = django_filters.CharFilter(
        label=_('Accounting code'),
        lookup_expr='iexact',
    )
    status = django_filters.ChoiceFilter(
        label=_('Status'),
        widget=forms.RadioSelect,
        empty_label=_('all'),
        method='filter_status',
    )

    def __init__(self, *args, **kwargs):
        self.pool = kwargs.pop('pool')
        super().__init__(*args, **kwargs)

        if self.pool.draft:
            del self.filters['invoice_number']
            del self.filters['credit_line']
        else:
            del self.filters['invoice_id']

        agenda_slugs = self.queryset.annotate(agenda_slug=KT('event__agenda')).values('agenda_slug')
        agendas = Agenda.objects.filter(slug__in=agenda_slugs)
        self.filters['agenda'].field.choices = [(a.slug, a.label) for a in agendas]
        agenda_labels_by_slug = {a.slug: a.label for a in agendas}
        lines = (
            self.queryset.annotate(
                event_slug=Coalesce(
                    KT('event__primary_event'),
                    KT('event__slug'),
                )
            )
            .values('event__agenda', 'event_slug', 'label')
            .distinct()
            .order_by()
        )
        events = []
        for line in lines:
            if line['event__agenda'] not in agenda_labels_by_slug:
                continue
            events.append(
                (
                    '%s@%s' % (line['event__agenda'], line['event_slug']),
                    '%s / %s' % (agenda_labels_by_slug.get(line['event__agenda']), line['label']),
                    agenda_labels_by_slug.get(line['event__agenda']),
                    line['label'],
                )
            )
        events = sorted(list(events), key=lambda e: (e[2], e[3]))
        self.filters['event'].field.choices = [(e[0], e[1]) for e in events]

        error_types = (
            self.queryset.annotate(error_type=KT('pricing_data__error'))
            .filter(error_type__isnull=False)
            .values('error_type')
            .distinct()
            .order_by()
        )
        status_choices = [
            ('success', _('Success')),
        ]
        if InjectedLine.objects.filter(regie=self.pool.campaign.regie).exists():
            status_choices += [
                ('success_injected', _('Success (Injected)')),
            ]
        status_choices += [
            ('warning', _('Warning')),
            ('error', _('Error')),
        ]
        if settings.CAMPAIGN_SHOW_FIX_ERROR:
            status_choices += [
                ('error_todo', _('Error (To treat)')),
                ('error_ignored', _('Error (Ignored)')),
                ('error_fixed', _('Error (Fixed)')),
            ]
        status_choices += [
            (e['error_type'], _('Error: %s') % AbstractJournalLine.get_error_label(e['error_type']))
            for e in error_types
        ]
        self.filters['status'].field.choices = status_choices

    def filter_agenda(self, queryset, name, value):
        if not value:
            return queryset
        return queryset.filter(event__agenda=value)

    def filter_event(self, queryset, name, value):
        if not value:
            return queryset
        agenda_slug, event_slug = value.split('@')
        return queryset.filter(
            Q(event__primary_event=event_slug) | Q(event__slug=event_slug), event__agenda=agenda_slug
        )

    def filter_status(self, queryset, name, value):
        if not value:
            return queryset
        if value == 'success_injected':
            return queryset.filter(status='success', from_injected_line__isnull=False)
        if value == 'error_todo':
            return queryset.filter(status='error', error_status='')
        if value == 'error_ignored':
            return queryset.filter(status='error', error_status='ignored')
        if value == 'error_fixed':
            return queryset.filter(status='error', error_status='fixed')
        if value in ['success', 'warning', 'error']:
            return queryset.filter(status=value)
        return queryset.filter(pricing_data__error=value)


class DraftJournalLineFilterSet(AbstractJournalLineFilterSet):
    class Meta:
        model = DraftJournalLine
        fields = []


class JournalLineFilterSet(AbstractJournalLineFilterSet):
    class Meta:
        model = JournalLine
        fields = []


class MultipleChoiceField(forms.MultipleChoiceField):
    widget = forms.CheckboxSelectMultiple


class MultipleChoiceFilter(django_filters.MultipleChoiceFilter):
    field_class = MultipleChoiceField


class DateRangeWidget(django_filters.widgets.DateRangeWidget):
    def __init__(self, attrs=None):
        widgets = (
            forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
            forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
        )
        super(django_filters.widgets.SuffixedMultiWidget, self).__init__(widgets, attrs)


class DateRangeField(django_filters.fields.DateRangeField):
    widget = DateRangeWidget


class DateFromToRangeFilter(django_filters.DateFromToRangeFilter):
    field_class = DateRangeField


class RegieInvoiceFilterSet(AgendaFieldsFilterSetMixin, django_filters.FilterSet):
    number = django_filters.CharFilter(
        label=_('Invoice number'),
        field_name='formatted_number',
        lookup_expr='contains',
    )
    origin = MultipleChoiceFilter(
        label=_('Invoice origin'),
        choices=ORIGINS,
    )
    created_at = DateFromToRangeFilter(
        label=_('Creation date'),
        field_name='created_at',
    )
    date_payment_deadline = DateFromToRangeFilter(
        label=_('Payment deadline'),
        field_name='date_payment_deadline',
    )
    date_due = DateFromToRangeFilter(
        label=_('Due date'),
        field_name='date_due',
    )
    payment_number = django_filters.CharFilter(
        label=_('Payment number'),
        method='filter_payment_number',
    )
    payer_external_id = django_filters.CharFilter(
        label=_('Payer (external ID)'),
    )
    payer_first_name = django_filters.CharFilter(
        label=_('Payer first name'),
        lookup_expr='icontains',
    )
    payer_last_name = django_filters.CharFilter(
        label=_('Payer last name'),
        lookup_expr='icontains',
    )
    payer_direct_debit = django_filters.BooleanFilter(
        label=_('Payer direct debit'),
    )
    user_external_id = django_filters.CharFilter(
        label=_('User (external ID)'),
        method='filter_user_external_id',
    )
    user_first_name = django_filters.CharFilter(
        label=_('User first name'),
        method='filter_user_first_name',
    )
    user_last_name = django_filters.CharFilter(
        label=_('User last name'),
        method='filter_user_last_name',
    )
    total_amount_min = django_filters.LookupChoiceFilter(
        label=_('Total amount min'),
        field_name='total_amount',
        field_class=forms.DecimalField,
        empty_label=None,
        lookup_choices=[
            ('gt', '>'),
            ('gte', '>='),
        ],
    )
    total_amount_max = django_filters.LookupChoiceFilter(
        label=_('Total amount max'),
        field_name='total_amount',
        field_class=forms.DecimalField,
        empty_label=None,
        lookup_choices=[
            ('lt', '<'),
            ('lte', '<='),
        ],
    )
    paid = django_filters.ChoiceFilter(
        label=_('Paid'),
        widget=forms.RadioSelect,
        empty_label=_('all'),
        choices=[
            ('yes', _('Totally')),
            ('partially', _('Partially')),
            ('no', _('No')),
        ],
        method='filter_paid',
    )
    cancelled = django_filters.ChoiceFilter(
        label=_('Cancelled'),
        widget=forms.RadioSelect,
        empty_label=_('all'),
        choices=[
            ('yes', _('Yes')),
            ('no', _('No')),
        ],
        method='filter_cancelled',
    )
    collected = django_filters.ChoiceFilter(
        label=pgettext('invoice', 'Collected'),
        widget=forms.RadioSelect,
        empty_label=_('all'),
        choices=[
            ('yes', _('Yes')),
            ('no', _('No')),
        ],
        method='filter_collected',
    )
    agenda = django_filters.ChoiceFilter(
        label=_('Activity'),
        empty_label=_('all'),
        method='filter_agenda',
    )
    event = django_filters.ChoiceFilter(
        label=_('Event'),
        empty_label=_('all'),
        method='filter_event',
    )
    accounting_code = django_filters.CharFilter(
        label=_('Accounting code'),
        method='filter_accounting_code',
    )
    line_model = InvoiceLine
    invoice_field = 'invoice'

    class Meta:
        model = Invoice
        fields = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self._init_agenda_fields(self.queryset)

    def filter_payment_number(self, queryset, name, value):
        line_queryset = InvoiceLine.objects.filter(
            pk__in=InvoiceLinePayment.objects.filter(payment__formatted_number__contains=value).values('line')
        )
        return queryset.filter(pk__in=line_queryset.values('invoice'))

    def filter_user_external_id(self, queryset, name, value):
        if not value:
            return queryset
        lines = InvoiceLine.objects.filter(user_external_id=value).values('invoice')
        return queryset.filter(pk__in=lines)

    def filter_user_first_name(self, queryset, name, value):
        if not value:
            return queryset
        lines = InvoiceLine.objects.filter(user_first_name__icontains=value).values('invoice')
        return queryset.filter(pk__in=lines)

    def filter_user_last_name(self, queryset, name, value):
        if not value:
            return queryset
        lines = InvoiceLine.objects.filter(user_last_name__icontains=value).values('invoice')
        return queryset.filter(pk__in=lines)

    def filter_paid(self, queryset, name, value):
        if value == 'yes':
            return queryset.filter(remaining_amount=0, total_amount__gt=0)
        if value == 'partially':
            return queryset.filter(remaining_amount__gt=0, paid_amount__gt=0)
        if value == 'no':
            return queryset.filter(paid_amount=0)
        return queryset

    def filter_cancelled(self, queryset, name, value):
        if not value:
            return queryset
        if value == 'yes':
            return queryset.filter(cancelled_at__isnull=False)
        return queryset.filter(cancelled_at__isnull=True)

    def filter_collected(self, queryset, name, value):
        if not value:
            return queryset
        if value == 'yes':
            return queryset.filter(collection__isnull=False)
        return queryset.filter(collection__isnull=True)

    def filter_accounting_code(self, queryset, name, value):
        if not value:
            return queryset
        lines = InvoiceLine.objects.filter(accounting_code__iexact=value).values('invoice')
        return queryset.filter(pk__in=lines)


class RegieInvoiceCancelForm(forms.ModelForm):
    class Meta:
        model = Invoice
        fields = ['cancellation_reason', 'cancellation_description']

    def __init__(self, *args, **kwargs):
        self.request = kwargs.pop('request')
        super().__init__(*args, **kwargs)
        self.fields['cancellation_reason'].queryset = InvoiceCancellationReason.objects.filter(disabled=False)

    def save(self):
        super().save(commit=False)
        self.instance.cancelled_at = now()
        self.instance.cancelled_by = self.request.user
        self.instance.save()
        self.instance.notify(payload={'invoice_id': str(self.instance.uuid)}, notification_type='cancel')
        return self.instance


class RegieInvoiceDatesForm(forms.ModelForm):
    class Meta:
        model = Invoice
        fields = ['date_publication', 'date_payment_deadline_displayed', 'date_payment_deadline', 'date_due']

    def clean(self):
        cleaned_data = super().clean()

        if (
            'date_publication' in cleaned_data
            and 'date_payment_deadline' in cleaned_data
            and 'date_due' in cleaned_data
        ):
            if cleaned_data['date_publication'] > cleaned_data['date_payment_deadline']:
                self.add_error(
                    'date_payment_deadline', _('Payment deadline must be greater than publication date.')
                )
            elif cleaned_data['date_payment_deadline'] > cleaned_data['date_due']:
                self.add_error('date_due', _('Due date must be greater than payment deadline.'))
        return cleaned_data


class RegiePaymentFilterSet(AgendaFieldsFilterSetMixin, django_filters.FilterSet):
    number = django_filters.CharFilter(
        label=_('Payment number'),
        field_name='formatted_number',
        lookup_expr='contains',
    )
    created_at = DateFromToRangeFilter(
        label=_('Date'),
        field_name='created_at',
    )
    invoice_number = django_filters.CharFilter(
        label=_('Invoice number'),
        method='filter_invoice_number',
    )
    payer_external_id = django_filters.CharFilter(
        label=_('Payer (external ID)'),
    )
    payer_first_name = django_filters.CharFilter(
        label=_('Payer first name'),
        lookup_expr='icontains',
    )
    payer_last_name = django_filters.CharFilter(
        label=_('Payer last name'),
        lookup_expr='icontains',
    )
    payment_type = django_filters.ChoiceFilter(
        label=_('Payment type'),
        widget=forms.RadioSelect,
        empty_label=_('all'),
    )
    amount_min = django_filters.LookupChoiceFilter(
        label=_('Amount min'),
        field_name='amount',
        field_class=forms.DecimalField,
        empty_label=None,
        lookup_choices=[
            ('gt', '>'),
            ('gte', '>='),
        ],
    )
    amount_max = django_filters.LookupChoiceFilter(
        label=_('Amount max'),
        field_name='amount',
        field_class=forms.DecimalField,
        empty_label=None,
        lookup_choices=[
            ('lt', '<'),
            ('lte', '<='),
        ],
    )
    agenda = django_filters.ChoiceFilter(
        label=_('Activity'),
        empty_label=_('all'),
        method='filter_agenda',
    )
    event = django_filters.ChoiceFilter(
        label=_('Event'),
        empty_label=_('all'),
        method='filter_event',
    )
    accounting_code = django_filters.CharFilter(
        label=_('Accounting code'),
        method='filter_accounting_code',
    )
    cancelled = django_filters.ChoiceFilter(
        label=_('Cancelled'),
        widget=forms.RadioSelect,
        empty_label=_('all'),
        choices=[
            ('yes', _('Yes')),
            ('no', _('No')),
        ],
        method='filter_cancelled',
    )
    line_model = InvoiceLine
    invoice_field = 'invoice'

    class Meta:
        model = Payment
        fields = []

    def __init__(self, *args, **kwargs):
        self.regie = kwargs.pop('regie')
        super().__init__(*args, **kwargs)
        self.filters['payment_type'].field.choices = [(t.pk, t) for t in self.regie.paymenttype_set.all()]

        line_queryset = InvoiceLine.objects.filter(
            pk__in=InvoiceLinePayment.objects.filter(payment__in=self.queryset).values('line')
        )
        invoice_queryset = Invoice.objects.filter(pk__in=line_queryset.values('invoice'))
        self._init_agenda_fields(invoice_queryset)

    def filter_invoice_number(self, queryset, name, value):
        return queryset.filter(
            pk__in=InvoiceLinePayment.objects.filter(line__invoice__formatted_number__contains=value).values(
                'payment'
            )
        )

    def filter_agenda(self, queryset, name, value):
        if not value:
            return queryset
        lines = InvoiceLine.objects.filter(agenda_slug=value)
        return queryset.filter(pk__in=InvoiceLinePayment.objects.filter(line__in=lines).values('payment'))

    def filter_event(self, queryset, name, value):
        if not value:
            return queryset
        lines = InvoiceLine.objects.filter(event_slug=value)
        return queryset.filter(pk__in=InvoiceLinePayment.objects.filter(line__in=lines).values('payment'))

    def filter_accounting_code(self, queryset, name, value):
        if not value:
            return queryset
        lines = InvoiceLine.objects.filter(accounting_code__iexact=value)
        return queryset.filter(pk__in=InvoiceLinePayment.objects.filter(line__in=lines).values('payment'))

    def filter_cancelled(self, queryset, name, value):
        if not value:
            return queryset
        if value == 'yes':
            return queryset.filter(cancelled_at__isnull=False)
        return queryset.filter(cancelled_at__isnull=True)


class RegiePaymentCancelForm(forms.ModelForm):
    class Meta:
        model = Payment
        fields = ['cancellation_reason', 'cancellation_description']

    def __init__(self, *args, **kwargs):
        self.request = kwargs.pop('request')
        super().__init__(*args, **kwargs)
        self.fields['cancellation_reason'].queryset = PaymentCancellationReason.objects.filter(disabled=False)

    def save(self):
        super().save(commit=False)
        self.instance.cancelled_at = now()
        self.instance.cancelled_by = self.request.user
        with transaction.atomic():
            self.instance.save()
            self.instance.invoicelinepayment_set.all().delete()
            self.instance.creditassignment_set.all().delete()
        return self.instance


class RegieDocketPaymentFilterSet(django_filters.FilterSet):
    payment_type = django_filters.MultipleChoiceFilter(
        label=_('Payment type'),
        widget=forms.CheckboxSelectMultiple,
        required=True,
    )
    date_end = django_filters.DateFilter(
        label=_('Stop date'),
        widget=forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
        required=True,
        method='filter_date_end',
    )

    class Meta:
        model = Payment
        fields = []

    def __init__(self, *args, **kwargs):
        self.regie = kwargs.pop('regie')
        if kwargs.get('data') is None:
            # set initial through data, so form is valid on page load
            kwargs['data'] = {
                'payment_type': [p.pk for p in self.regie.paymenttype_set.all()],
                'date_end': now().date(),
            }
        super().__init__(*args, **kwargs)
        self.filters['payment_type'].field.choices = [(t.pk, t) for t in self.regie.paymenttype_set.all()]

    def filter_date_end(self, queryset, name, value):
        if not value:
            return queryset
        return queryset.annotate(
            created_at_coalesce=Coalesce(F('date_payment'), F('created_at__date'))
        ).filter(created_at_coalesce__lt=value)


class RegieDocketFilterSet(django_filters.FilterSet):
    number = django_filters.CharFilter(
        label=_('Docket number'),
        field_name='formatted_number',
        lookup_expr='contains',
    )
    date_end = DateFromToRangeFilter(
        label=_('Stop date'),
    )

    class Meta:
        model = PaymentDocket
        fields = []


class PaymentDocketForm(forms.ModelForm):
    class Meta:
        model = PaymentDocket
        fields = ['payment_types', 'date_end']
        widgets = {
            'payment_types': forms.CheckboxSelectMultiple,
            'date_end': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
        }

    def __init__(self, *args, **kwargs):
        instance = kwargs['instance']
        if not instance.pk:
            self.regie = kwargs.pop('regie')
            if kwargs.get('data') is None:
                # set initial through data, so form is valid on page load
                kwargs['data'] = {
                    'payment_types': [p.pk for p in self.regie.paymenttype_set.all()],
                    'date_end': now().date(),
                }
        else:
            self.regie = instance.regie
        super().__init__(*args, **kwargs)
        self.fields['payment_types'].queryset = self.regie.paymenttype_set.all()

    def save(self):
        self.instance = super().save()
        filterset = RegieDocketPaymentFilterSet(
            regie=self.regie,
            queryset=Payment.objects.filter(regie=self.regie, docket__isnull=True, cancelled_at__isnull=True),
            data={
                'payment_type': [p.pk for p in self.instance.payment_types.all()],
                'date_end': self.instance.date_end,
            },
        )
        if filterset.form.is_valid():
            payment_queryset = filterset.qs
            with transaction.atomic():
                Payment.objects.filter(regie=self.regie, docket=self.instance).exclude(
                    pk__in=payment_queryset
                ).update(docket=None)
                payment_queryset.update(docket=self.instance)
        return self.instance


class PaymentDocketPaymentTypeForm(forms.ModelForm):
    additionnal_information = forms.CharField(
        label=_('Additional information'),
        widget=forms.Textarea,
        required=False,
    )

    class Meta:
        model = PaymentDocket
        fields = []

    def __init__(self, *args, **kwargs):
        self.payment_type = kwargs.pop('payment_type')
        super().__init__(*args, **kwargs)
        self.initial['additionnal_information'] = (
            self.instance.payment_types_info.get(self.payment_type.slug) or ''
        )

    def save(self):
        super().save(commit=False)

        self.instance.payment_types_info[self.payment_type.slug] = self.cleaned_data[
            'additionnal_information'
        ]
        self.instance.save()
        return self.instance


class RegieCreditFilterSet(AgendaFieldsFilterSetMixin, django_filters.FilterSet):
    number = django_filters.CharFilter(
        label=_('Credit number'),
        field_name='formatted_number',
        lookup_expr='contains',
    )
    origin = MultipleChoiceFilter(
        label=_('Credit origin'),
        choices=ORIGINS,
    )
    created_at = DateFromToRangeFilter(
        label=_('Creation date'),
        field_name='created_at',
    )
    payment_number = django_filters.CharFilter(
        label=_('Payment number'),
        method='filter_payment_number',
    )
    payer_external_id = django_filters.CharFilter(
        label=_('Payer (external ID)'),
    )
    payer_first_name = django_filters.CharFilter(
        label=_('Payer first name'),
        lookup_expr='icontains',
    )
    payer_last_name = django_filters.CharFilter(
        label=_('Payer last name'),
        lookup_expr='icontains',
    )
    user_external_id = django_filters.CharFilter(
        label=_('User (external ID)'),
        method='filter_user_external_id',
    )
    user_first_name = django_filters.CharFilter(
        label=_('User first name'),
        method='filter_user_first_name',
    )
    user_last_name = django_filters.CharFilter(
        label=_('User last name'),
        method='filter_user_last_name',
    )
    total_amount_min = django_filters.LookupChoiceFilter(
        label=_('Total amount min'),
        field_name='total_amount',
        field_class=forms.DecimalField,
        empty_label=None,
        lookup_choices=[
            ('gt', '>'),
            ('gte', '>='),
        ],
    )
    total_amount_max = django_filters.LookupChoiceFilter(
        label=_('Total amount max'),
        field_name='total_amount',
        field_class=forms.DecimalField,
        empty_label=None,
        lookup_choices=[
            ('lt', '<'),
            ('lte', '<='),
        ],
    )
    assigned = django_filters.ChoiceFilter(
        label=_('Assigned'),
        widget=forms.RadioSelect,
        empty_label=_('all'),
        choices=[
            ('yes', _('Totally')),
            ('partially', _('Partially')),
            ('no', _('No')),
        ],
        method='filter_assigned',
    )
    cancelled = django_filters.ChoiceFilter(
        label=_('Cancelled'),
        widget=forms.RadioSelect,
        empty_label=_('all'),
        choices=[
            ('yes', _('Yes')),
            ('no', _('No')),
        ],
        method='filter_cancelled',
    )
    agenda = django_filters.ChoiceFilter(
        label=_('Activity'),
        empty_label=_('all'),
        method='filter_agenda',
    )
    event = django_filters.ChoiceFilter(
        label=_('Event'),
        empty_label=_('all'),
        method='filter_event',
    )
    accounting_code = django_filters.CharFilter(
        label=_('Accounting code'),
        method='filter_accounting_code',
    )
    line_model = CreditLine
    invoice_field = 'credit'

    class Meta:
        model = Credit
        fields = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self._init_agenda_fields(self.queryset)

    def filter_payment_number(self, queryset, name, value):
        assignment_queryset = CreditAssignment.objects.filter(
            payment__formatted_number__contains=value
        ).values('credit')
        return queryset.filter(pk__in=assignment_queryset)

    def filter_user_external_id(self, queryset, name, value):
        if not value:
            return queryset
        lines = CreditLine.objects.filter(user_external_id=value).values('credit')
        return queryset.filter(pk__in=lines)

    def filter_user_first_name(self, queryset, name, value):
        if not value:
            return queryset
        lines = CreditLine.objects.filter(user_first_name__icontains=value).values('credit')
        return queryset.filter(pk__in=lines)

    def filter_user_last_name(self, queryset, name, value):
        if not value:
            return queryset
        lines = CreditLine.objects.filter(user_last_name__icontains=value).values('credit')
        return queryset.filter(pk__in=lines)

    def filter_assigned(self, queryset, name, value):
        if value == 'yes':
            return queryset.filter(remaining_amount=0, total_amount__gt=0)
        if value == 'partially':
            return queryset.filter(remaining_amount__gt=0, assigned_amount__gt=0)
        if value == 'no':
            return queryset.filter(assigned_amount=0)
        return queryset

    def filter_cancelled(self, queryset, name, value):
        if not value:
            return queryset
        if value == 'yes':
            return queryset.filter(cancelled_at__isnull=False)
        return queryset.filter(cancelled_at__isnull=True)

    def filter_agenda(self, queryset, name, value):
        if not value:
            return queryset
        lines = CreditLine.objects.filter(agenda_slug=value).values('credit')
        return queryset.filter(pk__in=lines)

    def filter_event(self, queryset, name, value):
        if not value:
            return queryset
        lines = CreditLine.objects.filter(event_slug=value).values('credit')
        return queryset.filter(pk__in=lines)

    def filter_accounting_code(self, queryset, name, value):
        if not value:
            return queryset
        lines = CreditLine.objects.filter(accounting_code__iexact=value).values('credit')
        return queryset.filter(pk__in=lines)


class RegieCreditCancelForm(forms.ModelForm):
    class Meta:
        model = Credit
        fields = ['cancellation_reason', 'cancellation_description']

    def __init__(self, *args, **kwargs):
        self.request = kwargs.pop('request')
        super().__init__(*args, **kwargs)
        self.fields['cancellation_reason'].queryset = CreditCancellationReason.objects.filter(disabled=False)

    def save(self):
        super().save(commit=False)
        self.instance.cancelled_at = now()
        self.instance.cancelled_by = self.request.user
        self.instance.save()
        return self.instance


class RegieRefundFilterSet(django_filters.FilterSet):
    number = django_filters.CharFilter(
        label=_('Refund number'),
        field_name='formatted_number',
        lookup_expr='contains',
    )
    created_at = DateFromToRangeFilter(
        label=_('Creation date'),
        field_name='created_at',
    )
    credit_number = django_filters.CharFilter(
        label=_('Credit number'),
        method='filter_credit_number',
    )
    payer_external_id = django_filters.CharFilter(
        label=_('Payer (external ID)'),
    )
    payer_first_name = django_filters.CharFilter(
        label=_('Payer first name'),
        lookup_expr='icontains',
    )
    payer_last_name = django_filters.CharFilter(
        label=_('Payer last name'),
        lookup_expr='icontains',
    )
    amount_min = django_filters.LookupChoiceFilter(
        label=_('Amount min'),
        field_name='amount',
        field_class=forms.DecimalField,
        empty_label=None,
        lookup_choices=[
            ('gt', '>'),
            ('gte', '>='),
        ],
    )
    amount_max = django_filters.LookupChoiceFilter(
        label=_('Amount max'),
        field_name='amount',
        field_class=forms.DecimalField,
        empty_label=None,
        lookup_choices=[
            ('lt', '<'),
            ('lte', '<='),
        ],
    )

    class Meta:
        model = Refund
        fields = []

    def filter_credit_number(self, queryset, name, value):
        assignment_queryset = CreditAssignment.objects.filter(
            credit__formatted_number__contains=value
        ).values('refund')
        return queryset.filter(pk__in=assignment_queryset)


class RegiePayerFilterSet(django_filters.FilterSet):
    payer_external_id = django_filters.CharFilter(
        label=_('Payer (external ID)'),
    )
    payer_first_name = django_filters.CharFilter(
        label=_('Payer first name'),
        lookup_expr='icontains',
    )
    payer_last_name = django_filters.CharFilter(
        label=_('Payer last name'),
        lookup_expr='icontains',
    )


class InvoiceCancellationReasonForm(forms.ModelForm):
    class Meta:
        model = InvoiceCancellationReason
        fields = ['label', 'slug', 'disabled']

    def clean_slug(self):
        slug = self.cleaned_data['slug']

        if InvoiceCancellationReason.objects.filter(slug=slug).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError(
                _('Another invoice cancellation reason exists with the same identifier.')
            )

        return slug


class CreditCancellationReasonForm(forms.ModelForm):
    class Meta:
        model = CreditCancellationReason
        fields = ['label', 'slug', 'disabled']

    def clean_slug(self):
        slug = self.cleaned_data['slug']

        if CreditCancellationReason.objects.filter(slug=slug).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError(
                _('Another credit cancellation reason exists with the same identifier.')
            )

        return slug


class PaymentCancellationReasonForm(forms.ModelForm):
    class Meta:
        model = PaymentCancellationReason
        fields = ['label', 'slug', 'disabled']

    def clean_slug(self):
        slug = self.cleaned_data['slug']

        if PaymentCancellationReason.objects.filter(slug=slug).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError(
                _('Another payment cancellation reason exists with the same identifier.')
            )

        return slug


class RegiePayerTransactionFilterSet(django_filters.FilterSet):
    number = django_filters.CharFilter(
        label=_('Invoice/Credit number'),
        field_name='invoicing_element_number',
        lookup_expr='icontains',
    )
    origin = MultipleChoiceFilter(
        label=_('Invoice/Credit origin'),
        choices=ORIGINS,
        field_name='invoicing_element_origin',
    )
    user_external_id = django_filters.CharFilter(
        label=_('User (external ID)'),
    )
    user_first_name = django_filters.CharFilter(
        label=_('User first name'),
        lookup_expr='icontains',
    )
    user_last_name = django_filters.CharFilter(
        label=_('User last name'),
        lookup_expr='icontains',
    )
    agenda = django_filters.ChoiceFilter(
        label=_('Activity'),
        empty_label=_('all'),
        method='filter_agenda',
    )
    event = django_filters.ChoiceFilter(
        label=_('Event'),
        empty_label=_('all'),
        method='filter_event',
    )
    event_date = DateFromToRangeFilter(
        label=_('Event date'),
        method='filter_event_date',
    )
    accounting_code = django_filters.CharFilter(
        label=_('Accounting code'),
        lookup_expr='iexact',
    )

    def __init__(self, *args, **kwargs):
        if 'other_filterset' in kwargs:
            self.other_filterset = kwargs.pop('other_filterset')
        else:
            self.regie = kwargs.pop('regie')
            self.payer_external_id = kwargs.pop('payer_external_id')
        super().__init__(*args, **kwargs)

        self._init_agenda_fields(self.queryset)

    def _init_agenda_fields(self, invoice_queryset):
        if hasattr(self, 'other_filterset'):
            self.filters['agenda'].field.choices = self.other_filterset.filters['agenda'].field.choices
            self.filters['event'].field.choices = self.other_filterset.filters['event'].field.choices
            return

        # get agendas from slugs
        creditline_qs = CreditLine.objects.filter(
            credit__regie=self.regie, credit__payer_external_id=self.payer_external_id
        ).values('agenda_slug')
        invoiceline_qs = InvoiceLine.objects.filter(
            invoice__regie=self.regie, invoice__payer_external_id=self.payer_external_id
        ).values('agenda_slug')
        agenda_slugs = creditline_qs.union(invoiceline_qs).order_by('agenda_slug')
        agendas = Agenda.objects.filter(slug__in=agenda_slugs).order_by('category_label', 'label')
        # and init agenda filter choices
        self.filters['agenda'].field.choices = [
            (cat, [(agenda.slug, agenda.label) for agenda in group])
            for cat, group in groupby(agendas, key=lambda a: a.category_label or _('Misc'))
        ]
        # get line details to build event filter choices
        agendas_by_slug = {a.slug: a for a in agendas}
        creditline_qs = (
            CreditLine.objects.filter(
                credit__regie=self.regie, credit__payer_external_id=self.payer_external_id
            )
            .exclude(agenda_slug='')
            .values('event_label', 'event_slug', 'agenda_slug')
            .distinct()
        )
        invoiceline_qs = (
            InvoiceLine.objects.filter(
                invoice__regie=self.regie, invoice__payer_external_id=self.payer_external_id
            )
            .exclude(agenda_slug='')
            .values('event_label', 'event_slug', 'agenda_slug')
            .distinct()
        )
        lines = creditline_qs.union(invoiceline_qs).order_by('event_label', 'event_slug', 'agenda_slug')
        events = []
        for line in lines:
            if line['agenda_slug'] not in agendas_by_slug:
                # unknown agenda slug
                continue
            if ':' in line['event_slug']:
                # partial bookings, remove overtaking, reductions and overcharging
                continue
            agenda = agendas_by_slug[line['agenda_slug']]
            events.append(
                (
                    line['event_slug'],
                    '%s / %s' % (agenda.label, line['event_label']),
                    agenda.category_label or _('Misc'),
                    agenda.category_label or 'z' * 10,
                    agenda.label,
                    line['event_label'],
                )
            )
        # build event filter choices
        events = sorted(list(events), key=lambda e: (e[3], e[4], e[5]))
        self.filters['event'].field.choices = [
            (cat, [(e[0], e[1]) for e in group]) for cat, group in groupby(events, key=lambda e: e[2])
        ]

    def filter_agenda(self, queryset, name, value):
        if not value:
            return queryset
        return queryset.filter(agenda_slug=value)

    def filter_event(self, queryset, name, value):
        if not value:
            return queryset
        return queryset.filter(event_slug=value)

    def filter_event_date(self, queryset, name, value):
        if not value:
            return queryset

        qs_kwargs = {}
        if value.start is not None and value.stop is not None:
            qs_kwargs = {
                'details__jsonpath_exists': f'$.dates[*] ? (@ <= "{value.stop.date()}" && @ >= "{value.start.date()}")'
            }
        elif value.start is not None:
            qs_kwargs = {'details__jsonpath_exists': f'$.dates[*] ? (@ >= "{value.start.date()}")'}
        elif value.stop is not None:
            qs_kwargs = {'details__jsonpath_exists': f'$.dates[*] ? (@ <= "{value.stop.date()}")'}
        return queryset.filter(**qs_kwargs)
