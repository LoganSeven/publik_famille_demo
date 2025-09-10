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

from django import forms
from django.forms import ValidationError
from django.template import Template, TemplateSyntaxError
from django.utils.timezone import make_aware, now
from django.utils.translation import gettext_lazy as _
from django.utils.translation import pgettext_lazy
from gadjo.forms.widgets import MultiSelectWidget

from lingo.agendas.chrono import ChronoError, get_event
from lingo.agendas.models import Agenda, CheckType, CheckTypeGroup
from lingo.pricing.errors import PricingError
from lingo.pricing.models import BillingDate, Criteria, CriteriaCategory, Pricing
from lingo.utils.fields import AgendaSelect, AgendasMultipleChoiceField, CategorySelect


class ExportForm(forms.Form):
    agendas = forms.BooleanField(label=_('Agendas'), required=False, initial=True)
    check_type_groups = forms.BooleanField(label=_('Check type groups'), required=False, initial=True)
    pricing_categories = forms.BooleanField(
        label=_('Pricing criteria categories'), required=False, initial=True
    )
    pricings = forms.BooleanField(
        label=pgettext_lazy('agenda pricing', 'Pricings'), required=False, initial=True
    )


class ImportForm(forms.Form):
    config_json = forms.FileField(label=_('Export File'))


class NewCriteriaForm(forms.ModelForm):
    class Meta:
        model = Criteria
        fields = ['label', 'default', 'condition']

    def clean(self):
        cleaned_data = super().clean()

        if cleaned_data.get('default') is True:
            cleaned_data['condition'] = ''
        else:
            condition = cleaned_data['condition']
            if not condition:
                self.add_error('condition', self.fields['condition'].default_error_messages['required'])
            else:
                try:
                    Template('{%% if %s %%}OK{%% endif %%}' % condition)
                except TemplateSyntaxError:
                    self.add_error('condition', _('Invalid syntax.'))

        return cleaned_data


class CriteriaForm(NewCriteriaForm):
    class Meta:
        model = Criteria
        fields = ['label', 'slug', 'default', 'condition']

    def clean_slug(self):
        slug = self.cleaned_data['slug']

        if self.instance.category.criterias.filter(slug=slug).exclude(pk=self.instance.pk).exists():
            raise ValidationError(_('Another criteria exists with the same identifier.'))

        return slug


class NewPricingForm(forms.ModelForm):
    class Meta:
        model = Pricing
        fields = ['label', 'date_start', 'date_end', 'flat_fee_schedule', 'subscription_required']
        widgets = {
            'date_start': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
            'date_end': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.instance.pk:
            last_date_end = (
                Pricing.objects.all().order_by('date_end').values_list('date_end', flat=True).last()
            )
            self.initial['date_start'] = last_date_end or now().date()

    def clean(self):
        cleaned_data = super().clean()

        if 'date_start' in cleaned_data and 'date_end' in cleaned_data:
            if cleaned_data['date_end'] <= cleaned_data['date_start']:
                self.add_error('date_end', _('End date must be greater than start date.'))
        if 'flat_fee_schedule' in cleaned_data and 'subscription_required' in cleaned_data:
            if not cleaned_data['flat_fee_schedule']:
                cleaned_data['subscription_required'] = True

        return cleaned_data


class PricingForm(NewPricingForm):
    class Meta:
        model = Pricing
        fields = [
            'label',
            'slug',
            'date_start',
            'date_end',
            'flat_fee_schedule',
            'subscription_required',
            'kind',
            'reduction_rate',
            'effort_rate_target',
            'accounting_code',
        ]
        widgets = {
            'date_start': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
            'date_end': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
            'label': forms.TextInput(attrs={'size': 100}),
            'kind': forms.Select(attrs={'data-dynamic-display-parent': 'true'}),
            'reduction_rate': forms.TextInput(
                attrs={
                    'data-dynamic-display-child-of': 'kind',
                    'data-dynamic-display-value': 'reduction',
                    'size': 100,
                }
            ),
            'effort_rate_target': forms.TextInput(
                attrs={
                    'data-dynamic-display-child-of': 'kind',
                    'data-dynamic-display-value': 'effort',
                    'size': 100,
                }
            ),
            'accounting_code': forms.TextInput(attrs={'size': 100}),
        }

    def clean_slug(self):
        slug = self.cleaned_data['slug']

        if Pricing.objects.filter(slug=slug).exclude(pk=self.instance.pk).exists():
            raise ValidationError(_('Another pricing exists with the same identifier.'))

        return slug

    def clean_subscription_required(self):
        subscription_required = self.cleaned_data['subscription_required']
        if subscription_required is False and self.instance.subscription_required is True:
            # value has changed, check linked agendas
            if self.instance.agendas.exists():
                raise forms.ValidationError(
                    _('Some agendas are linked to this pricing; please unlink them first.')
                )
        return subscription_required

    def clean(self):
        cleaned_data = super().clean()
        if (
            'date_start' in cleaned_data
            and 'date_end' in cleaned_data
            and 'flat_fee_schedule' in cleaned_data
        ):
            old_date_start = self.instance.date_start
            old_date_end = self.instance.date_end
            old_flat_fee_schedule = self.instance.flat_fee_schedule
            new_date_start = cleaned_data['date_start']
            new_date_end = cleaned_data['date_end']
            new_flat_fee_schedule = cleaned_data['flat_fee_schedule']
            if (
                old_date_start != new_date_start
                or old_date_end != new_date_end
                or old_flat_fee_schedule != new_flat_fee_schedule
            ):
                overlapping_qs = (
                    Pricing.objects.filter(flat_fee_schedule=new_flat_fee_schedule)
                    .exclude(pk=self.instance.pk)
                    .extra(
                        where=['(date_start, date_end) OVERLAPS (%s, %s)'],
                        params=[new_date_start, new_date_end],
                    )
                )
                for agenda in self.instance.agendas.all():
                    if overlapping_qs.filter(agendas=agenda).exists():
                        self.add_error(
                            None,
                            _('Agenda "%s" has already a pricing overlapping this period.') % agenda.label,
                        )
            if (
                old_flat_fee_schedule != new_flat_fee_schedule
                and new_flat_fee_schedule is False
                and self.instance.billingdates.exists()
            ):
                self.add_error(
                    'flat_fee_schedule',
                    _('Some billing dates are are defined for this pricing; please delete them first.'),
                )
            if (
                old_flat_fee_schedule == new_flat_fee_schedule
                and new_flat_fee_schedule is True
                and (old_date_start != new_date_start or old_date_end != new_date_end)
            ):
                if (
                    self.instance.billingdates.filter(date_start__lt=new_date_start).exists()
                    or self.instance.billingdates.filter(date_start__gte=new_date_end).exists()
                ):
                    self.add_error(None, _('Some billing dates are outside the pricing period.'))

        if cleaned_data.get('kind') == 'reduction' and not cleaned_data.get('reduction_rate'):
            self.add_error(
                'reduction_rate', _('Declare the reduction rate you want to apply for this pricing.')
            )

        if cleaned_data.get('kind') == 'effort' and not cleaned_data.get('effort_rate_target'):
            self.add_error(
                'effort_rate_target',
                _('Declare the amount you want to multiply by the effort rate for this pricing.'),
            )

        if cleaned_data.get('kind') in ['basic', 'reduction']:
            cleaned_data['effort_rate_target'] = ''
        elif cleaned_data.get('kind') in ['basic', 'effort']:
            cleaned_data['reduction_rate'] = ''

        return cleaned_data


class PricingDuplicateForm(forms.Form):
    label = forms.CharField(label=_('New label'), max_length=150, required=False)
    date_start = forms.DateField(
        label=_('Start date'),
        widget=forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
        required=False,
    )
    date_end = forms.DateField(
        label=_('End date'),
        widget=forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
        required=False,
    )


class PricingVariableForm(forms.Form):
    key = forms.CharField(label=_('Variable name'), required=False)
    value = forms.CharField(
        label=_('Value template'), widget=forms.TextInput(attrs={'size': 60}), required=False
    )


PricingVariableFormSet = forms.formset_factory(PricingVariableForm)


class PricingCriteriaCategoryAddForm(forms.Form):
    category = forms.ModelChoiceField(
        label=_('Criteria category to add'), queryset=CriteriaCategory.objects.none(), required=True
    )

    def __init__(self, *args, **kwargs):
        self.pricing = kwargs.pop('pricing')
        super().__init__(*args, **kwargs)
        self.fields['category'].queryset = CriteriaCategory.objects.exclude(pricings=self.pricing)


class PricingCriteriaCategoryEditForm(forms.Form):
    criterias = forms.ModelMultipleChoiceField(
        label=_('Criterias'),
        queryset=Criteria.objects.none(),
        required=True,
        widget=forms.CheckboxSelectMultiple(),
    )

    def __init__(self, *args, **kwargs):
        self.pricing = kwargs.pop('pricing')
        self.category = kwargs.pop('category')
        super().__init__(*args, **kwargs)
        self.fields['criterias'].queryset = self.category.criterias.all()
        self.initial['criterias'] = self.pricing.criterias.filter(category=self.category)


class PricingPricingOptionsForm(forms.ModelForm):
    class Meta:
        model = Pricing
        fields = ['min_pricing', 'max_pricing']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.kind == 'reduction':
            del self.fields['max_pricing']


class PricingAgendaAddForm(forms.Form):
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

    def __init__(self, *args, **kwargs):
        self.pricing = kwargs.pop('pricing')
        super().__init__(*args, **kwargs)
        self.fields['agendas'].queryset = Agenda.objects.exclude(pricings=self.pricing).order_by(
            'category_label', 'label'
        )
        self.fields['category'].choices = [('', '---------')] + list(
            Agenda.objects.exclude(pricings=self.pricing)
            .filter(category_slug__isnull=False)
            .values_list('category_slug', 'category_label')
            .order_by('category_label')
            .distinct()
        )

    def clean_agendas(self):
        agendas = self.cleaned_data['agendas']
        for agenda in agendas:
            overlapping_qs = Pricing.objects.filter(
                flat_fee_schedule=self.pricing.flat_fee_schedule, agendas=agenda
            ).extra(
                where=['(date_start, date_end) OVERLAPS (%s, %s)'],
                params=[self.pricing.date_start, self.pricing.date_end],
            )
            if overlapping_qs.exists():
                raise forms.ValidationError(
                    _('The agenda "%s" has already a pricing overlapping this period.') % agenda.label
                )
        return agendas


class PricingBillingDateForm(forms.ModelForm):
    class Meta:
        model = BillingDate
        fields = ['date_start', 'label']
        widgets = {
            'date_start': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
        }

    def clean_date_start(self):
        date_start = self.cleaned_data['date_start']
        if date_start < self.instance.pricing.date_start or date_start >= self.instance.pricing.date_end:
            raise forms.ValidationError(_('The billing start date must be within the period of the pricing.'))
        return date_start


class PricingMatrixForm(forms.Form):
    def __init__(self, *args, **kwargs):
        matrix = kwargs.pop('matrix')
        pricing = kwargs.pop('pricing')
        super().__init__(*args, **kwargs)
        for i in range(len(matrix.rows[0].cells)):
            more = 0
            if pricing.kind == 'effort':
                more = 2
            self.fields['crit_%i' % i] = forms.DecimalField(
                required=True, max_digits=5 + more, decimal_places=2 + more
            )


class PricingTestToolForm(forms.Form):
    agenda = forms.ModelChoiceField(label=_('Agenda'), empty_label=None, queryset=Agenda.objects.none())
    event_slug = forms.CharField(label=_('Event identifier'))
    billing_date = forms.ModelChoiceField(
        label=_('Billing date'), empty_label=None, queryset=BillingDate.objects.none()
    )
    user_external_id = forms.CharField(label=_('User external identifier'))
    payer_external_id = forms.CharField(label=_('Payer external identifier'))
    booking_status = forms.ChoiceField(label=_('Booking status'), choices=[])

    def __init__(self, *args, **kwargs):
        self.request = kwargs.pop('request')
        self.pricing = kwargs.pop('pricing')
        self.agenda = None
        if kwargs['data'] and kwargs['data'].get('agenda'):
            self.init_agenda(kwargs['data']['agenda'])
        self.serialized_event = None
        self.check_type_slug = None
        self.booking_status = None
        super().__init__(*args, **kwargs)
        if self.pricing.subscription_required:
            self.fields['agenda'].queryset = self.pricing.agendas.all()
        else:
            del self.fields['agenda']
        if self.pricing.flat_fee_schedule:
            del self.fields['event_slug']
            del self.fields['booking_status']
            self.init_billing_date()
        else:
            del self.fields['billing_date']
            self.init_booking_status()

    def init_agenda(self, agenda_id):
        try:
            self.agenda = self.pricing.agendas.get(pk=agenda_id)
        except Agenda.DoesNotExist:
            pass

    def init_booking_status(self):
        presence_check_types = (
            self.agenda.check_type_group.check_types.presences()
            if self.agenda and self.agenda.check_type_group
            else []
        )
        absence_check_types = (
            self.agenda.check_type_group.check_types.absences()
            if self.agenda and self.agenda.check_type_group
            else []
        )
        status_choices = [
            ('presence', _('Presence')),
        ]
        status_choices += [
            ('presence::%s' % ct.slug, _('Presence (%s)') % ct.label) for ct in presence_check_types
        ]
        status_choices += [('absence', _('Absence'))]
        status_choices += [
            ('absence::%s' % ct.slug, _('Absence (%s)') % ct.label) for ct in absence_check_types
        ]
        self.fields['booking_status'].choices = status_choices

    def init_billing_date(self):
        billing_dates = self.pricing.billingdates.order_by('date_start')
        if not billing_dates:
            del self.fields['billing_date']
            return
        self.fields['billing_date'].queryset = billing_dates

    def clean_event_slug(self):
        event_slug = self.cleaned_data['event_slug']
        if not self.agenda:
            return event_slug
        try:
            self.serialized_event = get_event('%s@%s' % (self.agenda.slug, event_slug))
        except ChronoError as e:
            raise forms.ValidationError(e)

        event_date = datetime.datetime.fromisoformat(self.serialized_event['start_datetime'])
        if (
            self.serialized_event.get('recurrence_days')
            and self.serialized_event.get('primary_event') is None
        ):
            # recurring event, take the beginning of the period
            event_date = make_aware(datetime.datetime.combine(self.pricing.date_start, datetime.time(0, 0)))
            self.serialized_event['start_datetime'] = event_date.isoformat()
        event_date = event_date.date()
        if event_date < self.pricing.date_start or event_date >= self.pricing.date_end:
            raise ValidationError(_('This event takes place outside the period covered by this pricing'))

        return event_slug

    def clean_booking_status(self):
        original_booking_status = self.cleaned_data['booking_status']
        self.booking_status = original_booking_status
        if '::' in original_booking_status:
            # split value to get booking status and selected check_type
            self.booking_status, self.check_type_slug = original_booking_status.split('::')
        return original_booking_status

    def compute(self):
        try:
            if self.pricing.flat_fee_schedule:
                return self.compute_for_flat_fee_schedule()
            return self.compute_for_event()
        except PricingError as e:
            return {
                'error': e,
                'error_details': e.details,
            }

    def compute_for_flat_fee_schedule(self):
        pricing_date = self.pricing.date_start
        if self.cleaned_data.get('billing_date'):
            pricing_date = self.cleaned_data['billing_date'].date_start
        return self.pricing.get_pricing_data(
            request=self.request,
            pricing_date=pricing_date,
            user_external_id=self.cleaned_data['user_external_id'],
            payer_external_id=self.cleaned_data['payer_external_id'],
        )

    def compute_for_event(self):
        return self.pricing.get_pricing_data_for_event(
            request=self.request,
            agenda=self.agenda,
            event=self.serialized_event,
            check_status={
                'status': self.booking_status,
                'check_type': self.check_type_slug,
            },
            user_external_id=self.cleaned_data['user_external_id'],
            payer_external_id=self.cleaned_data['payer_external_id'],
        )


class CheckTypeGroupUnexpectedPresenceForm(forms.ModelForm):
    class Meta:
        model = CheckTypeGroup
        fields = ['unexpected_presence']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['unexpected_presence'].queryset = self.instance.check_types.filter(kind='presence')


class CheckTypeGroupUnjustifiedAbsenceForm(forms.ModelForm):
    class Meta:
        model = CheckTypeGroup
        fields = ['unjustified_absence']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['unjustified_absence'].queryset = self.instance.check_types.filter(kind='absence')


class NewCheckTypeForm(forms.ModelForm):
    class Meta:
        model = CheckType
        fields = ['label', 'kind', 'pricing', 'pricing_rate']

    def clean(self):
        super().clean()
        if self.cleaned_data.get('pricing') is not None and self.cleaned_data.get('pricing_rate') is not None:
            raise ValidationError(_('Please choose between pricing and pricing rate.'))

    def save(self):
        if not self.instance.pk and self.instance.kind == 'absence':
            self.instance.colour = '#FF0000'

        return super().save()


class CheckTypeForm(NewCheckTypeForm):
    class Meta:
        model = CheckType
        fields = ['label', 'slug', 'code', 'colour', 'pricing', 'pricing_rate', 'disabled']
        widgets = {
            'colour': forms.TextInput(attrs={'type': 'color'}),
        }

    def clean_slug(self):
        slug = self.cleaned_data['slug']

        if self.instance.group.check_types.filter(slug=slug).exclude(pk=self.instance.pk).exists():
            raise ValidationError(_('Another check type exists with the same identifier.'))

        return slug
