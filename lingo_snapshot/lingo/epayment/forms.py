# lingo - payment and billing system
# Copyright (C) 2022-2023  Entr'ouvert
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

import django_filters
from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from lingo.invoicing.forms import DateFromToRangeFilter

from .models import PaymentBackend, Transaction

TYPE_FIELD_MAPPING = {
    str: forms.CharField,
    bool: forms.BooleanField,
    int: forms.IntegerField,
    float: forms.FloatField,
}


def get_validator(func, err_msg):
    def validate(value):
        if not func(value):
            message = err_msg or _('Invalid value.')
            raise ValidationError(message)

    return validate


def create_form_fields(parameters, json_field):
    fields, initial = [], {}
    for param in parameters:
        field_name = param['name']
        if field_name in ('normal_return_url', 'automatic_return_url') or param.get('deprecated'):
            continue

        field_params = {
            'label': param.get('caption') or field_name,
            'required': param.get('required', False),
            'help_text': param.get('help_text', ''),
        }
        if 'validation' in param:
            field_params['validators'] = [get_validator(param['validation'], param.get('validation_err_msg'))]

        _type = param.get('type', str)
        choices = param.get('choices')
        if choices is not None:
            field_class = forms.MultipleChoiceField if _type is list else forms.ChoiceField
            if choices and not isinstance(choices[0], tuple):
                choices = [(choice, choice) for choice in choices]
            field_params['choices'] = choices
        else:
            field_class = TYPE_FIELD_MAPPING[_type]

        fields.append((field_name, field_class(**field_params)))
        initial_value = json_field.get(field_name, param.get('default'))
        if initial_value:
            initial[field_name] = initial_value

    return fields, initial


def compute_json_field(parameters, cleaned_data):
    json_field = {}
    for param in parameters:
        param_name = param['name']
        if param_name in cleaned_data:
            json_field[param_name] = cleaned_data[param_name]
    return json_field


class PaymentBackendCreateForm(forms.ModelForm):
    class Meta:
        model = PaymentBackend
        fields = ['label', 'service']


class PaymentBackendForm(forms.ModelForm):
    class Meta:
        model = PaymentBackend
        fields = ['label', 'slug', 'regie', 'service', 'edit_role', 'view_role']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        fields, initial = create_form_fields(self.instance.service_parameters, self.instance.service_options)
        self.fields.update(fields)
        self.initial.update(initial)
        if self.fields['service']:
            self.fields['service'].disabled = True

    def save(self):
        instance = super().save()
        instance.service_options = compute_json_field(self.instance.service_parameters, self.cleaned_data)
        instance.save()
        return instance


class TransactionFilterSet(django_filters.FilterSet):
    status = django_filters.ChoiceFilter(
        label=_('Status'),
        widget=forms.RadioSelect,
        empty_label=_('All'),
        choices=[
            ('running', _('Started')),
            ('paid', _('Paid')),
            ('others', _('Others')),
        ],
        method='filter_status',
    )
    date = DateFromToRangeFilter(
        label=_('Date'),
        field_name='start_date',
    )
    order_id = django_filters.CharFilter(
        label=_('Order Identifier'),
        field_name='order_id',
        lookup_expr='contains',
    )
    bank_transaction_id = django_filters.CharFilter(
        label=_('Bank Transaction Identifier'),
        field_name='bank_transaction_id',
        lookup_expr='contains',
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
    invoice = django_filters.CharFilter(
        label=_('Invoice number'),
        field_name='invoice__formatted_number',
    )
    payment = django_filters.CharFilter(
        label=_('Payment number'),
        field_name='payment_formatted_number',
    )

    def filter_status(self, queryset, name, value):
        if value == 'running':
            return queryset.filter(status__in=Transaction.RUNNING_STATUSES)
        if value == 'paid':
            return queryset.filter(status__in=Transaction.PAID_STATUSES)
        if value == 'others':
            return queryset.filter(status__in=Transaction.OTHER_STATUSES)
        return queryset
