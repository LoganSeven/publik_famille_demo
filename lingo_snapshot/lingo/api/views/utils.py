# lingo - payment and billing system
# Copyright (C) 2025  Entr'ouvert
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


import collections
import datetime

from django.utils import formats
from django.utils.translation import gettext_lazy as _
from django.utils.translation import gettext_noop as N_

from lingo.agendas.models import Agenda
from lingo.api.utils import APIErrorBadRequest, Response
from lingo.invoicing.errors import InvoicingError
from lingo.invoicing.utils import check_links, get_cached_payer_data, get_existing_lines_for_user, get_pricing
from lingo.pricing.errors import PricingError
from lingo.pricing.models import Pricing


class FromBookingsMixin:
    def post(self, request, **kwargs):
        self.post_init(request, **kwargs)
        serializer = self.serializer_class(**self.get_serializer_kwargs(request))
        if not serializer.is_valid():
            raise APIErrorBadRequest(N_('invalid payload'), errors=serializer.errors)

        booked_events = serializer.validated_data['booked_events']
        cancelled_events = serializer.validated_data['cancelled_events']
        all_events = booked_events + cancelled_events
        if not all_events:
            raise APIErrorBadRequest(N_('no changes'))

        agenda_slugs = {e['agenda'] for e in all_events}
        regie_agenda_by_slugs = {
            a.slug: a for a in Agenda.objects.filter(slug__in=agenda_slugs, regie=self.regie)
        }
        unknown_agenda_slugs = [s for s in agenda_slugs if s not in regie_agenda_by_slugs]
        if unknown_agenda_slugs:
            raise APIErrorBadRequest(
                N_('wrong regie for agendas: %s') % ', '.join(sorted(unknown_agenda_slugs))
            )
        self.agendas_by_slug = {k: v for k, v in regie_agenda_by_slugs.items() if k in agenda_slugs}

        event_dates = {e['start_datetime'].date() for e in all_events}
        agendas_pricings = (
            Pricing.objects.filter(flat_fee_schedule=False, agendas__slug__in=self.agendas_by_slug)
            .extra(
                where=['(date_start, date_end) OVERLAPS (%s, %s)'],
                params=[min(event_dates), max(event_dates) + datetime.timedelta(days=1)],
            )
            .prefetch_related('agendas', 'criterias', 'categories')
        )
        self.agendas_pricings_by_agendas = collections.defaultdict(list)
        for pricing in agendas_pricings:
            for agenda in pricing.agendas.all():
                self.agendas_pricings_by_agendas[agenda.slug].append(pricing)

        payer_data_cache = {}

        existing_lines = get_existing_lines_for_user(
            regie=self.regie,
            date_min=min(event_dates),
            date_max=max(event_dates) + datetime.timedelta(days=1),  # date_max is excluded
            user_external_id=serializer.validated_data['user_external_id'],
            serialized_events=all_events,
        )

        unit_lines = collections.defaultdict(list)
        for serialized_event in booked_events:
            for line_payer_external_id, line in self.get_lines_for_event(
                request,
                serializer,
                serialized_event,
                payer_data_cache,
                booked=True,
                existing_lines=existing_lines,
            ):
                unit_lines[line_payer_external_id].append(line)
        for serialized_event in cancelled_events:
            for line_payer_external_id, line in self.get_lines_for_event(
                request,
                serializer,
                serialized_event,
                payer_data_cache,
                booked=False,
                existing_lines=existing_lines,
            ):
                unit_lines[line_payer_external_id].append(line)

        aggregated_lines = collections.defaultdict(list)
        for payer_external_id in sorted(unit_lines.keys()):
            aggregated_lines[payer_external_id] = self.aggregate_lines(unit_lines[payer_external_id])
        data = self.process(request, serializer, aggregated_lines, payer_data_cache)
        return Response({'err': 0, 'data': data})

    def post_init(self, request, *args):
        raise NotImplementedError

    def get_serializer_kwargs(self, request):
        return {
            'data': request.data,
        }

    def get_current_payer_id(self, serializer):
        raise NotImplementedError

    def process(self, request, serializer, aggregated_lines, payer_data_cache):
        raise NotImplementedError

    def get_lines_for_event(
        self, request, serializer, serialized_event, payer_data_cache, booked, existing_lines
    ):
        agenda = self.agendas_by_slug[serialized_event['agenda']]
        event_date = serialized_event['start_datetime'].date()
        serialized_event['start_datetime'] = serialized_event['start_datetime'].isoformat()
        event_slug = serialized_event['slug']
        if serialized_event.get('primary_event'):
            # primary event if available
            event_slug = serialized_event['primary_event']
        event_slug = f'{agenda.slug}@{event_slug}'
        links = existing_lines.get(event_slug, {}).get(event_date.isoformat(), [])
        payer_external_id = self.get_current_payer_id(serializer)
        try:
            # get normal pricing for current payer_external_id
            pricing = get_pricing(self.agendas_pricings_by_agendas.get(agenda.slug), event_date)
            pricing_data = pricing.get_pricing_data_for_event(
                request=request,
                agenda=agenda,
                event=serialized_event,
                check_status={'status': 'presence', 'check_type': None},
                user_external_id=serializer.validated_data['user_external_id'],
                payer_external_id=payer_external_id,
            )
        except PricingError as e:
            raise APIErrorBadRequest('error: %s, details: %s' % (type(e).__name__, e.details))
        description = _('Booking') if booked else _('Cancellation')

        def get_line(payer_external_id, quantity, amount, description, adjustment=None):
            try:
                get_cached_payer_data(
                    request, self.regie, payer_data_cache, payer_external_id, pricing=pricing
                )
            except InvoicingError as e:
                raise APIErrorBadRequest('error: %s, details: %s' % (type(e).__name__, e.details))
            return {
                'event_date': event_date,
                'label': serialized_event['label'],
                'quantity': quantity,
                'unit_amount': amount,
                'event_slug': event_slug,
                'event_label': serialized_event['label'],
                'agenda_slug': agenda.slug,
                'activity_label': agenda.label,
                'description': str(description),
                'accounting_code': pricing_data.get('accounting_code') or '',
                'details': {'adjustment': adjustment} if adjustment else {},
            }

        # add lines for adjustment
        for result in check_links(
            final_link='cancelled' if booked else 'booking',
            current_pricing=pricing_data['pricing'],
            current_payer_external_id=payer_external_id,
            remaining_links=links,
            fix_when_pricing_changed=False,
        ):
            adjustment_description = (
                _('Booking (regularization)')
                if result['quantity'] == 1
                else _('Cancellation (regularization)')
            )
            reason = 'missing-cancellation' if result['quantity'] == -1 else 'missing-booking'
            adjustment = {
                'reason': reason,
                'before': result.get('before'),
                'after': result.get('after'),
            }
            adjustment = {k: v for k, v in adjustment.items() if v is not None}
            yield result['payer_external_id'], get_line(
                result['payer_external_id'],
                result['quantity'],
                result['amount'],
                adjustment_description,
                adjustment=adjustment,
            )
        # generate line for the change: booking or cancellation
        pricing = pricing_data['pricing']
        if not booked and links:
            # his a cancellation; we have to refund the payer who previously booked, at the same pricing
            last_link = links[-1]
            if last_link.booked:
                pricing = last_link.unit_amount
                payer_external_id = last_link.payer_external_id
        yield payer_external_id, get_line(payer_external_id, 1 if booked else -1, pricing, description)

    def aggregate_lines(self, unit_lines):
        keys = []  # to keep ordering from unit_lines
        grouped_lines = collections.defaultdict(list)  # group lines
        for line in unit_lines:
            key = (
                line['unit_amount'],
                line['description'],
                line['event_slug'],
                line['accounting_code'],
            )
            booking_str = str(_('Booking (regularization)'))
            cancellation_str = str(_('Cancellation (regularization)'))
            if key[1] in [booking_str, cancellation_str]:
                other_key = list(key).copy()
                other_key[1] = booking_str if key[1] == cancellation_str else cancellation_str
                other_grouped_lines = grouped_lines.get(tuple(other_key)) or []
                other_line_found = False
                for other_line in other_grouped_lines:
                    if other_line['event_date'] == line['event_date']:
                        other_grouped_lines.remove(other_line)
                        other_line_found = True
                        break
                if other_line_found:
                    continue
            if key not in keys:
                keys.append(key)
            grouped_lines[key].append(line)
        result = []
        for key in keys:
            lines = grouped_lines[key]
            if not lines:
                continue
            first_line = lines[0]
            description = [first_line['description']]
            dates = [line['event_date'] for line in lines]
            quantity = sum(line['quantity'] for line in lines)
            description.append(', '.join([formats.date_format(event_date, 'd/m') for event_date in dates]))
            details = {
                'dates': dates,
            }
            if first_line['details'].get('adjustment'):
                adjustment = {'reason': first_line['details']['adjustment']['reason']}
                for line in lines:
                    adjustment[line['event_date'].isoformat()] = {
                        k: v for k, v in line['details']['adjustment'].items() if k != 'reason'
                    }
                details['adjustment'] = adjustment
            result.append(
                {
                    'event_date': dates[0],
                    'label': first_line['label'],
                    'quantity': quantity,
                    'unit_amount': first_line['unit_amount'],
                    'details': details,
                    'event_slug': first_line['event_slug'],
                    'event_label': first_line['event_label'],
                    'agenda_slug': first_line['agenda_slug'],
                    'activity_label': first_line['activity_label'],
                    'description': ' '.join(description),
                    'accounting_code': first_line['accounting_code'],
                }
            )
        return sorted(result, key=lambda a: (a['label'], a['description']))
