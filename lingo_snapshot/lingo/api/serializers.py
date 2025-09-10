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
import uuid

from django.utils.timezone import now
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers
from rest_framework.exceptions import ValidationError

from lingo.agendas.chrono import ChronoError, get_events
from lingo.agendas.models import Agenda, CheckType
from lingo.basket.models import Basket, BasketLine, BasketLineItem
from lingo.invoicing.models import (
    Credit,
    DraftInvoice,
    DraftInvoiceLine,
    InjectedLine,
    Invoice,
    InvoiceCancellationReason,
    InvoiceLine,
    Payment,
    PaymentType,
    Refund,
)
from lingo.pricing.errors import PricingError, PricingNotFound
from lingo.pricing.models import Pricing

try:
    from mellon.models import UserSAMLIdentifier
except ImportError:
    UserSAMLIdentifier = None


class CommaSeparatedStringField(serializers.ListField):
    def get_value(self, dictionary):
        return super(serializers.ListField, self).get_value(dictionary)

    def to_internal_value(self, data):
        data = [s.strip() for s in data.split(',') if s.strip()]
        return super().to_internal_value(data)


class PricingSerializer(serializers.ModelSerializer):
    id = serializers.CharField(source='slug', read_only=True)
    text = serializers.CharField(source='__str__', read_only=True)

    class Meta:
        model = Pricing
        fields = [
            'id',
            'text',
            'slug',
            'label',
            'flat_fee_schedule',
            'subscription_required',
            'date_start',
            'date_end',
        ]


class PricingComputeSerializer(serializers.Serializer):
    slots = CommaSeparatedStringField(
        required=False, child=serializers.CharField(max_length=160, allow_blank=False)
    )
    agenda = serializers.SlugField(required=False, allow_blank=False, max_length=160)
    pricing = serializers.SlugField(required=False, allow_blank=False, max_length=160)
    start_date = serializers.DateTimeField(required=False, input_formats=['iso-8601', '%Y-%m-%d'])
    user_external_id = serializers.CharField(required=True, max_length=250)
    payer_external_id = serializers.CharField(required=True, max_length=250)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self._agenda_slugs = []
        self._agendas = {}
        self._serialized_events = {}
        self._agenda = None
        self._pricing = None
        self._billing_date = None

    def _validate_slots(self, value, start_date):
        self._agendas = {a.slug: a for a in Agenda.objects.all()}
        allowed_agenda_slugs = self._agendas.keys()
        agenda_slugs = set()
        slots = set()
        for slot in value:
            try:
                agenda_slug, event_slug = slot.split('@')
            except ValueError:
                raise ValidationError({'slots': _('Invalid format for slot %s') % slot})
            if not agenda_slug:
                raise ValidationError({'slots': _('Missing agenda slug in slot %s') % slot})
            if not event_slug:
                raise ValidationError({'slots': _('Missing event slug in slot %s') % slot})
            agenda_slugs.add(agenda_slug)
            slots.add(slot.split(':')[0])  # remove day for recurring events
        slots = list(slots)
        extra_agendas = agenda_slugs - set(allowed_agenda_slugs)
        if extra_agendas:
            extra_agendas = ', '.join(sorted(extra_agendas))
            raise ValidationError({'slots': _('Unknown agendas: %s') % extra_agendas})
        self._agenda_slugs = sorted(agenda_slugs)

        if not slots:
            return []
        try:
            serialized_events = get_events(slots)
        except ChronoError as e:
            raise ValidationError({'slots': e})
        else:
            for serialized_event in serialized_events:
                event_slug = '%s@%s' % (serialized_event['agenda'], serialized_event['slug'])
                if (
                    serialized_event.get('recurrence_days')
                    and serialized_event.get('primary_event') is None
                    and start_date
                ):
                    # recurring event, take start_date if given
                    serialized_event['start_datetime'] = start_date.isoformat()
                self._serialized_events[event_slug] = serialized_event

        return slots

    def _validate_agenda(self, value, start_date):
        try:
            self._agenda = Agenda.objects.get(slug=value)
            try:
                self._pricing = Pricing.get_pricing(
                    agenda=self._agenda,
                    start_date=start_date.date(),
                    flat_fee_schedule=True,
                )
                if not self._pricing.subscription_required:
                    self._pricing = None
            except PricingNotFound:
                self._pricing = None
        except Agenda.DoesNotExist:
            raise ValidationError({'agenda': _('Unknown agenda: %s') % value})
        return self._agenda

    def _validate_pricing(self, value, start_date):
        try:
            self._pricing = Pricing.objects.get(
                slug=value,
                flat_fee_schedule=True,
                subscription_required=False,
                date_start__lte=start_date.date(),
                date_end__gt=start_date.date(),
            )
        except Pricing.DoesNotExist:
            raise ValidationError({'pricing': _('Unknown pricing: %s') % value})
        return self._pricing

    def validate(self, attrs):
        super().validate(attrs)
        if (
            'slots' not in self.initial_data
            and 'slots' not in attrs
            and 'agenda' not in attrs
            and 'pricing' not in attrs
        ):
            raise ValidationError(_('Either "slots", "agenda" or "pricing" parameter is required.'))
        if 'slots' in attrs:
            self._validate_slots(attrs['slots'], attrs.get('start_date'))
        if 'agenda' in attrs:
            # flat_fee_schedule mode + subscription_required True
            if 'start_date' not in attrs:
                raise ValidationError(
                    {'start_date': _('This field is required when using "agenda" parameter.')}
                )
            self._validate_agenda(attrs['agenda'], attrs['start_date'])
        if 'pricing' in attrs:
            # flat_fee_schedule mode + subscription_required False
            if 'start_date' not in attrs:
                raise ValidationError(
                    {'start_date': _('This field is required when using "pricing" parameter.')}
                )
            self._validate_pricing(attrs['pricing'], attrs['start_date'])
        if attrs.get('start_date'):
            # flat_fee_schedule mode: get billing_date from start_date param
            self.get_billing_date(attrs['start_date'])
        return attrs

    def get_billing_date(self, start_date):
        if self._pricing:
            self._billing_date = (
                self._pricing.billingdates.filter(date_start__lte=start_date).order_by('date_start').last()
            )
            if not self._billing_date:
                self._billing_date = self._pricing.billingdates.order_by('date_start').first()

    def get_extra_variables(self, request):
        extra_variables = {}
        data = request.data or request.query_params
        for k, v in data.items():
            if k not in self.validated_data and k.startswith('extra_variable_'):
                extra_variables[k.replace('extra_variable_', '')] = str(v)
        return extra_variables

    def compute(self, request):
        extra_variables = self.get_extra_variables(request)
        try:
            if not self.validated_data.get('slots'):
                if not self._agenda and not self._pricing:
                    return []
                return self.compute_for_flat_fee_schedule(request, bypass_extra_variables=extra_variables)
            return self.compute_for_event(request, bypass_extra_variables=extra_variables)
        except PricingError as e:
            return {
                'error': type(e),
                'error_details': e.details,
            }

    def compute_for_event(self, request, bypass_extra_variables=None):
        result = []
        event_slugs = sorted(self._serialized_events.keys())
        for event_slug in event_slugs:
            serialized_event = self._serialized_events[event_slug]
            start_date = datetime.datetime.fromisoformat(serialized_event['start_datetime']).date()
            agenda = self._agendas[serialized_event['agenda']]
            try:
                pricing = Pricing.get_pricing(agenda=agenda, start_date=start_date, flat_fee_schedule=False)
                pricing_data = pricing.get_pricing_data_for_event(
                    request=request,
                    agenda=agenda,
                    event=serialized_event,
                    check_status={
                        'status': 'presence',
                        'check_type': None,
                    },
                    user_external_id=self.validated_data['user_external_id'],
                    payer_external_id=self.validated_data['payer_external_id'],
                    bypass_extra_variables=bypass_extra_variables or {},
                )
                result.append(
                    {
                        'event': event_slug,
                        'pricing_data': pricing_data,
                    }
                )
            except PricingNotFound:
                result.append(
                    {'event': event_slug, 'error': _('No agenda pricing found for event %s') % event_slug}
                )
            except PricingError as e:
                result.append({'event': event_slug, 'error': type(e).__name__, 'error_details': e.details})

        result = sorted(result, key=lambda d: d['event'])
        return result

    def compute_for_flat_fee_schedule(self, request, bypass_extra_variables=None):
        result = {}
        if self._agenda:
            result['agenda'] = self._agenda.slug
            if not self._pricing:
                result['error'] = _('No agenda pricing found for agenda %s') % self._agenda.slug
                return result
        else:
            result['pricing'] = self._pricing.slug

        try:
            pricing_data = self._pricing.get_pricing_data(
                request=request,
                pricing_date=(
                    self._billing_date.date_start if self._billing_date else self._pricing.date_start
                ),
                user_external_id=self.validated_data['user_external_id'],
                payer_external_id=self.validated_data['payer_external_id'],
                bypass_extra_variables=bypass_extra_variables or {},
            )
            result['pricing_data'] = pricing_data
            return result
        except PricingError as e:
            result['error'] = type(e).__name__
            result['error_details'] = e.details
            return result


class CheckTypeSerializer(serializers.ModelSerializer):
    id = serializers.CharField(source='slug')
    text = serializers.CharField(source='label')
    color = serializers.CharField(source='colour')
    unexpected_presence = serializers.SerializerMethodField()
    unjustified_absence = serializers.SerializerMethodField()
    agendas = serializers.SerializerMethodField()

    class Meta:
        model = CheckType
        fields = [
            'id',
            'text',
            'kind',
            'code',
            'color',
            'unexpected_presence',
            'unjustified_absence',
            'agendas',
        ]

    def get_unexpected_presence(self, check_type):
        return bool(check_type.group.unexpected_presence == check_type)

    def get_unjustified_absence(self, check_type):
        return bool(check_type.group.unjustified_absence == check_type)

    def get_agendas(self, check_type):
        return [x.slug for x in check_type.group.agenda_set.all()]


class AgendaSlugsSerializer(serializers.Serializer):
    agendas = CommaSeparatedStringField(child=serializers.SlugField(max_length=160, allow_blank=False))

    def validate_agendas(self, value):
        slugs = set(value)
        objects = Agenda.objects.filter(partial_bookings=False, slug__in=slugs)
        if len(objects) != len(slugs):
            unknown_slugs = sorted(slugs - {obj.slug for obj in objects})
            unknown_slugs = ', '.join(unknown_slugs)
            raise ValidationError(('unknown agendas: %s') % unknown_slugs)
        return objects


class AgendasCheckTypeListSerializer(AgendaSlugsSerializer):
    pass


class AgendaUnlockSerializer(AgendaSlugsSerializer):
    date_start = serializers.DateField()
    date_end = serializers.DateField()


class InvoiceFiltersSerializer(serializers.Serializer):
    payable = serializers.BooleanField(required=False)


class CreditFiltersSerializer(serializers.Serializer):
    usable = serializers.BooleanField(required=False)


class CancelInvoiceSerializer(serializers.Serializer):
    cancellation_reason = serializers.SlugRelatedField(
        slug_field='slug', queryset=InvoiceCancellationReason.objects.none()
    )
    cancellation_description = serializers.CharField(allow_blank=True, required=False)
    user_uuid = serializers.UUIDField(required=False)
    notify = serializers.BooleanField(required=False, default=True)

    class Meta:
        model = Invoice
        fields = [
            'cancellation_reason',
            'cancellation_description',
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['cancellation_reason'].queryset = InvoiceCancellationReason.objects.filter(disabled=False)

    def validate_user_uuid(self, value):
        if UserSAMLIdentifier is None:
            return
        try:
            return UserSAMLIdentifier.objects.get(name_id=value.hex).user
        except UserSAMLIdentifier.DoesNotExist:
            raise ValidationError(_('User not found.'))


class DraftInvoiceSerializer(serializers.ModelSerializer):
    previous_invoice = serializers.UUIDField(required=False)

    class Meta:
        model = DraftInvoice
        fields = [
            'label',
            'date_publication',
            'date_payment_deadline_displayed',
            'date_payment_deadline',
            'date_due',
            'date_invoicing',
            'payer_external_id',
            'payer_first_name',
            'payer_last_name',
            'payer_address',
            'payer_email',
            'payer_phone',
            'payment_callback_url',
            'cancel_callback_url',
            'previous_invoice',
        ]

    def __init__(self, *args, **kwargs):
        self.regie = kwargs.pop('regie')
        super().__init__(*args, **kwargs)

    def validate_previous_invoice(self, value):
        try:
            return Invoice.objects.filter(regie=self.regie).get(uuid=value)
        except Invoice.DoesNotExist:
            raise ValidationError(_('Unknown invoice.'))


class DraftCreditSerializer(DraftInvoiceSerializer):
    class Meta:
        model = DraftInvoice
        fields = [
            'label',
            'date_publication',
            'date_invoicing',
            'payer_external_id',
            'payer_first_name',
            'payer_last_name',
            'payer_address',
            'payer_email',
            'payer_phone',
            'usable',
            'previous_invoice',
        ]


class DraftInvoiceLineSerializer(serializers.ModelSerializer):
    slug = serializers.CharField(max_length=250)
    activity_label = serializers.CharField(required=False, max_length=250)
    description = serializers.CharField(required=False, default='', max_length=500)
    merge_lines = serializers.BooleanField(required=False, default=False)
    subject = serializers.CharField(required=False, default='', max_length=120)

    class Meta:
        model = DraftInvoiceLine
        fields = [
            'event_date',
            'slug',
            'label',
            'quantity',
            'unit_amount',
            'activity_label',
            'description',
            'accounting_code',
            'user_external_id',
            'user_first_name',
            'user_last_name',
            'form_url',
            'merge_lines',
            'subject',
        ]


class DraftCreditCloseSerializer(serializers.Serializer):
    make_assignments = serializers.BooleanField(required=False, default=True)


class EventSerializer(serializers.Serializer):
    agenda_slug = serializers.CharField(max_length=250, source='agenda')
    slug = serializers.CharField(max_length=500)
    primary_event = serializers.CharField(allow_null=True, max_length=500)
    datetime = serializers.DateTimeField(source='start_datetime')
    label = serializers.CharField(max_length=250)

    def to_internal_value(self, data):
        ret = super().to_internal_value(data)
        for key, value in data.items():
            if not key.startswith('custom_field_'):
                continue
            ret[key] = value
        if ret['primary_event']:
            ret['primary_event'] = ret['primary_event'].split('@')[1]
        return ret


class FromBookingsDryRunSerializer(serializers.Serializer):
    payer_external_id = serializers.CharField(max_length=250)
    user_external_id = serializers.CharField(max_length=250)
    booked_events = serializers.ListField(child=EventSerializer(), allow_empty=True)
    cancelled_events = serializers.ListField(child=EventSerializer(), allow_empty=True)


class FromBookingsSerializer(serializers.ModelSerializer):
    user_external_id = serializers.CharField(max_length=250)
    user_first_name = serializers.CharField(max_length=250)
    user_last_name = serializers.CharField(max_length=250)
    form_url = serializers.URLField(required=False)
    booked_events = serializers.ListField(child=EventSerializer(), allow_empty=True)
    cancelled_events = serializers.ListField(child=EventSerializer(), allow_empty=True)

    class Meta:
        model = DraftInvoice
        fields = [
            'label',
            'date_publication',
            'date_payment_deadline_displayed',
            'date_payment_deadline',
            'date_due',
            'date_invoicing',
            'user_external_id',
            'user_first_name',
            'user_last_name',
            'payer_external_id',
            'form_url',
            'payment_callback_url',
            'cancel_callback_url',
            'booked_events',
            'cancelled_events',
        ]


class InjectedLineSerializer(serializers.ModelSerializer):
    class Meta:
        model = InjectedLine
        fields = [
            'event_date',
            'slug',
            'label',
            'amount',
            'user_external_id',
            'payer_external_id',
        ]


class MakePaymentSerializer(serializers.ModelSerializer):
    elements_to_pay = CommaSeparatedStringField(child=serializers.CharField())
    payment_type = serializers.SlugRelatedField(slug_field='slug', queryset=PaymentType.objects.none())
    check_issuer = serializers.CharField(required=False, max_length=250)
    check_bank = serializers.CharField(required=False, max_length=250)
    check_number = serializers.CharField(required=False, max_length=250)
    bank_transfer_number = serializers.CharField(required=False, max_length=250)
    payment_reference = serializers.CharField(required=False, max_length=250)
    online_refdet = serializers.CharField(required=False, max_length=250)

    class Meta:
        model = Payment
        fields = [
            'amount',
            'payment_type',
            'elements_to_pay',
            'check_issuer',
            'check_bank',
            'check_number',
            'bank_transfer_number',
            'payment_reference',
            'online_refdet',
            'date_payment',
        ]

    def __init__(self, *args, **kwargs):
        self.regie = kwargs.pop('regie')
        self._invoices = []
        self._lines = []
        super().__init__(*args, **kwargs)
        self.fields['payment_type'].queryset = PaymentType.objects.filter(regie=self.regie, disabled=False)

    def _validate_invoice(self, index, invoice_uuid):
        try:
            invoice = Invoice.objects.exclude(pool__campaign__finalized=False).get(
                uuid=uuid.UUID(invoice_uuid),
                regie=self.regie,
                cancelled_at__isnull=True,
                collection__isnull=True,
            )
        except ValueError:
            raise ValidationError({str(index): [_('Must be a valid UUID.')]})
        except Invoice.DoesNotExist:
            raise ValidationError({str(index): [_('Unknown invoice.')]})
        if invoice.date_due < now().date():
            raise ValidationError({str(index): [_('The invoice due date has passed.')]})
        if invoice.payer_direct_debit:
            raise ValidationError({str(index): [_('The invoice is set up for direct debit.')]})
        return invoice

    def _validate_line(self, index, line_uuid):
        try:
            line = InvoiceLine.objects.select_related('invoice').get(
                uuid=uuid.UUID(line_uuid), invoice__regie=self.regie
            )
        except ValueError:
            raise ValidationError({str(index): [_('Must be a valid UUID.')]})
        except InvoiceLine.DoesNotExist:
            raise ValidationError({str(index): [_('Unknown invoice line.')]})
        if line.invoice.date_due < now().date():
            raise ValidationError({str(index): [_('The invoice due date of this line has passed.')]})
        if line.invoice.payer_direct_debit:
            raise ValidationError({str(index): [_('The invoice of this line is set up for direct debit.')]})
        return line

    def validate_elements_to_pay(self, value):
        for i, invoice_uuid in enumerate(value):
            if invoice_uuid.startswith('line:'):
                line_uuid = invoice_uuid.split(':')[1]
                self._lines.append(self._validate_line(i, line_uuid))
            else:
                if not self._lines:
                    # ignore invoices if there are lines in payload
                    self._invoices.append(self._validate_invoice(i, invoice_uuid))

    def validate(self, attrs):
        super().validate(attrs)

        amount = attrs['amount']

        if self._lines:
            if len({i.invoice.payer_external_id for i in self._lines}) > 1:
                raise ValidationError(
                    {'elements_to_pay': _('Can not create payment for invoice lines of different payers.')}
                )

            if sum(i.remaining_amount for i in self._lines) < amount:
                raise ValidationError(
                    {'amount': _('Amount is bigger than sum of invoice lines remaining amounts.')}
                )
        else:
            if len({i.payer_external_id for i in self._invoices}) > 1:
                raise ValidationError(
                    {'elements_to_pay': _('Can not create payment for invoices of different payers.')}
                )

            if sum(i.remaining_amount for i in self._invoices) < amount:
                raise ValidationError(
                    {'amount': _('Amount is bigger than sum of invoices remaining amounts.')}
                )

        return attrs


class PaymentSerializer(serializers.ModelSerializer):
    check_issuer = serializers.CharField(required=False, max_length=250)
    check_bank = serializers.CharField(required=False, max_length=250)
    check_number = serializers.CharField(required=False, max_length=250)
    bank_transfer_number = serializers.CharField(required=False, max_length=250)
    payment_reference = serializers.CharField(required=False, max_length=250)
    online_refdet = serializers.CharField(required=False, max_length=250)

    class Meta:
        model = Payment
        fields = [
            'check_issuer',
            'check_bank',
            'check_number',
            'bank_transfer_number',
            'payment_reference',
            'online_refdet',
        ]


class RefundSerializer(serializers.ModelSerializer):
    credit = serializers.UUIDField()

    class Meta:
        model = Refund
        fields = [
            'credit',
            'date_refund',
        ]

    def __init__(self, *args, **kwargs):
        self.regie = kwargs.pop('regie')
        super().__init__(*args, **kwargs)

    def validate_credit(self, value):
        try:
            credit = Credit.objects.exclude(pool__campaign__finalized=False).get(
                uuid=value,
                regie=self.regie,
                date_publication__lte=now().date(),
                cancelled_at__isnull=True,
            )
        except Credit.DoesNotExist:
            raise ValidationError(_('Unknown credit.'))
        if credit.remaining_amount == 0:
            raise ValidationError(_('Credit already completely assigned.'))
        return credit


class InvoicingElementsSplitSerializer(serializers.Serializer):
    old_agenda = serializers.SlugRelatedField(
        queryset=Agenda.objects.none(), slug_field='slug', required=True
    )
    new_agenda = serializers.SlugRelatedField(
        queryset=Agenda.objects.none(), slug_field='slug', required=True
    )
    user_external_id = serializers.CharField(required=True, max_length=250)
    date_start = serializers.DateField()
    date_end = serializers.DateField()

    def __init__(self, *args, **kwargs):
        self.regie = kwargs.pop('regie')
        super().__init__(*args, **kwargs)
        self.fields['old_agenda'].queryset = Agenda.objects.filter(regie=self.regie)
        self.fields['new_agenda'].queryset = Agenda.objects.filter(regie=self.regie)

    def validate(self, attrs):
        super().validate(attrs)
        if attrs.get('date_start') and attrs.get('date_end') and attrs['date_start'] > attrs['date_end']:
            raise ValidationError({'date_start': _('date_start must be before date_end.')})
        return attrs


class BasketSerializer(serializers.ModelSerializer):
    class Meta:
        model = Basket
        fields = [
            'payer_nameid',
            'payer_external_id',
            'payer_first_name',
            'payer_last_name',
            'payer_address',
            'payer_email',
            'payer_phone',
        ]

    def __init__(self, *args, **kwargs):
        self.regie = kwargs.pop('regie')
        super().__init__(*args, **kwargs)

    def validate_payer_nameid(self, value):
        basket_qs = Basket.objects.filter(
            payer_nameid=value,
            regie=self.regie,
            status='tobepaid',
        )
        other_regie_basket_qs = Basket.objects.filter(
            payer_nameid=value,
            status__in=['open', 'tobepaid'],
        ).exclude(regie=self.regie)
        if basket_qs.exists() or other_regie_basket_qs.exists():
            raise ValidationError(_('a basket to finalize already exists'))

        return value


class BasketCheckSerializer(serializers.Serializer):
    user_external_id = serializers.CharField(required=True, max_length=250)
    payer_nameid = serializers.CharField(required=True, max_length=250)

    def __init__(self, *args, **kwargs):
        self.regie = kwargs.pop('regie')
        super().__init__(*args, **kwargs)

    def validate(self, attrs):
        super().validate(attrs)

        other_regie_basket_qs = Basket.objects.filter(
            payer_nameid=attrs['payer_nameid'],
            status__in=['open', 'tobepaid'],
        ).exclude(regie=self.regie)
        if other_regie_basket_qs.exists():
            raise ValidationError(
                {'payer_nameid': _('a basket to finalize already exists in another regie')},
                code='payer_active_basket',
            )

        basket_qs = Basket.objects.filter(
            payer_nameid=attrs['payer_nameid'],
            status__in=['open', 'tobepaid'],
            regie=self.regie,
            basketline__user_external_id=attrs['user_external_id'],
        )
        if basket_qs.exists():
            raise ValidationError(
                {
                    'user_external_id': _(
                        'a line already exists in active basket in this regie for this user_external_id'
                    )
                },
                code='user_existing_line',
            )

        basket_qs = Basket.objects.filter(
            payer_nameid=attrs['payer_nameid'],
            regie=self.regie,
            status='tobepaid',
        )
        if basket_qs.exists():
            raise ValidationError(
                {'payer_nameid': _('a basket to pay already exists')},
                code='payer_active_basket_to_pay',
            )

        return attrs


class BasketLineSerializer(serializers.ModelSerializer):
    reuse = serializers.BooleanField(required=False, default=False)

    class Meta:
        model = BasketLine
        fields = [
            'user_external_id',
            'user_first_name',
            'user_last_name',
            'information_message',
            'cancel_information_message',
            'group_items',
            'form_url',
            'validation_callback_url',
            'payment_callback_url',
            'credit_callback_url',
            'cancel_callback_url',
            'expiration_callback_url',
            'reuse',
        ]

    def __init__(self, *args, **kwargs):
        self.basket = kwargs.pop('basket')
        super().__init__(*args, **kwargs)

    def validate(self, attrs):
        super().validate(attrs)

        basket_item_qs = BasketLine.objects.filter(
            user_external_id=attrs['user_external_id'],
            basket=self.basket,
        )
        if basket_item_qs.exists():
            if attrs['reuse'] is False or not basket_item_qs.first().closed:
                raise ValidationError(
                    {'user_external_id': _('a line is already opened in basket for this user_external_id')},
                    code='user_existing_line',
                )

        return attrs


class BasketLinesFromBookingsDryRunSerializer(serializers.Serializer):
    user_external_id = serializers.CharField(max_length=250)
    payer_external_id = serializers.CharField(max_length=250)
    booked_events = serializers.ListField(child=EventSerializer(), allow_empty=True)
    cancelled_events = serializers.ListField(child=EventSerializer(), allow_empty=True)


class BasketLinesFromBookingsSerializer(serializers.ModelSerializer):
    booked_events = serializers.ListField(child=EventSerializer(), allow_empty=True)
    cancelled_events = serializers.ListField(child=EventSerializer(), allow_empty=True)

    class Meta:
        model = BasketLine
        fields = [
            'user_external_id',
            'user_first_name',
            'user_last_name',
            'information_message',
            'cancel_information_message',
            'form_url',
            'validation_callback_url',
            'payment_callback_url',
            'credit_callback_url',
            'cancel_callback_url',
            'expiration_callback_url',
            'booked_events',
            'cancelled_events',
        ]

    def __init__(self, *args, **kwargs):
        self.basket = kwargs.pop('basket')
        super().__init__(*args, **kwargs)

    def validate(self, attrs):
        super().validate(attrs)

        basket_item_qs = BasketLine.objects.filter(
            user_external_id=attrs['user_external_id'],
            basket=self.basket,
        )
        if basket_item_qs.exists():
            raise ValidationError(
                {'user_external_id': _('a line is already opened in basket for this user_external_id')},
                code='user_existing_line',
            )

        return attrs


class BasketLineItemSerializer(serializers.ModelSerializer):
    slug = serializers.CharField(required=False, max_length=250)
    activity_label = serializers.CharField(required=False, max_length=250)

    class Meta:
        model = BasketLineItem
        fields = [
            'event_date',
            'label',
            'subject',
            'details',
            'quantity',
            'unit_amount',
            'slug',
            'activity_label',
            'accounting_code',
        ]


MEASURE_CHOICES = {
    'count': _('Invoice count'),
    'total_amount': _('Total amount'),
    'paid_amount': _('Paid amount'),
    'remaining_amount': _('Remaining amount'),
}


class StatisticsFiltersSerializer(serializers.Serializer):
    time_interval = serializers.ChoiceField(choices=('day', _('Day')), default='day')
    start = serializers.DateTimeField(required=False, input_formats=['iso-8601', '%Y-%m-%d'])
    end = serializers.DateTimeField(required=False, input_formats=['iso-8601', '%Y-%m-%d'])
    measures = serializers.ListField(
        required=False, child=serializers.ChoiceField(choices=list(MEASURE_CHOICES)), default=['total_amount']
    )
    regie = serializers.CharField(required=False, allow_blank=False, max_length=256)
    activity = serializers.CharField(required=False, allow_blank=False, max_length=256)
    payer_external_id = serializers.CharField(required=False, allow_blank=False, max_length=256)


class AgendaDuplicateSettingsSerializer(serializers.Serializer):
    target_agenda = serializers.SlugField(required=True, max_length=160)
