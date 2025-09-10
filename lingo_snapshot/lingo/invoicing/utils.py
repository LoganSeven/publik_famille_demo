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

import collections
import copy
import dataclasses
import datetime
import decimal

from django.conf import settings
from django.db import transaction
from django.db.models import F, Q
from django.test.client import RequestFactory
from django.utils import formats
from django.utils.timezone import localtime
from django.utils.translation import gettext_lazy as _

from lingo.agendas.chrono import get_check_status, get_subscriptions
from lingo.agendas.models import Agenda, CheckType
from lingo.invoicing.errors import InvoicingError
from lingo.invoicing.models import (
    Campaign,
    CreditLine,
    DraftInvoice,
    DraftInvoiceLine,
    DraftJournalLine,
    InjectedLine,
    InvoiceLine,
    JournalLine,
    Pool,
    Regie,
)
from lingo.pricing.errors import PricingError, PricingNotFound
from lingo.pricing.models import Pricing


def get_agendas(pool):
    agendas_pricings = Pricing.objects.filter(
        flat_fee_schedule=False, agendas__regie=pool.campaign.regie, agendas__in=pool.campaign.agendas.all()
    ).extra(
        where=['(date_start, date_end) OVERLAPS (%s, %s)'],
        params=[pool.campaign.date_start, pool.campaign.date_end],
    )
    return Agenda.objects.filter(pk__in=agendas_pricings.values('agendas')).order_by('pk')


def get_users_from_subscriptions(agendas, pool):
    users = {}
    for agenda in agendas:
        subscriptions = get_subscriptions(
            agenda_slug=agenda.slug,
            date_start=pool.campaign.date_start,
            date_end=pool.campaign.date_end,
        )
        for subscription in subscriptions:
            user_external_id = subscription['user_external_id']
            if user_external_id in users:
                continue
            users[user_external_id] = (subscription['user_first_name'], subscription['user_last_name'])
    return users


def get_pricing(agendas_pricings_for_agenda, date_event):
    # same logic as Pricing.get_pricing
    for pricing in agendas_pricings_for_agenda or []:
        if pricing.date_start > date_event:
            continue
        if pricing.date_end <= date_event:
            continue
        return pricing
    raise PricingNotFound


def get_cached_payer_data(request, regie, payer_data_cache, payer_external_id, pricing=None, payer_data=None):
    if payer_external_id not in payer_data_cache:
        if pricing:
            # will raise a PricingError if payer_data can not be computed
            payer_data_cache[payer_external_id] = regie.get_payer_data(request, payer_external_id)
        elif payer_data:
            payer_data_cache[payer_external_id] = payer_data
    return payer_data_cache.get(payer_external_id) or {}


@dataclasses.dataclass
class Link:
    payer_external_id: str
    unit_amount: decimal.Decimal
    booked: bool
    invoicing_element_number: str


def get_existing_lines_for_user(*, regie, date_min, date_max, user_external_id, serialized_events):
    # extract event slugs from check_status_list
    events = set()
    for serialized_event in serialized_events:
        agenda = serialized_event['agenda']
        event_slug = serialized_event['slug']
        if serialized_event.get('primary_event'):
            event_slug = serialized_event['primary_event']
        events.add(f'{agenda}@{event_slug}')

    # get invoice lines for these events
    invoice_lines = (
        InvoiceLine.objects.filter(
            invoice__regie=regie,
            invoice__cancelled_at__isnull=True,
            invoice__pool__isnull=True,
            event_slug__in=events,
            user_external_id=user_external_id,
            details__jsonpath_exists=f'$.dates[*] ? (@ < "{date_max}" && @ >= "{date_min}")',
        )
        .annotate(
            invoicing_element_number=F('invoice__formatted_number'),
            invoicing_element_created_at=F('invoice__created_at'),
            payer_external_id=F('invoice__payer_external_id'),
        )
        .exclude(
            total_amount=0,
        )
        .order_by('pk')
    )
    # and credit lines for these events
    credit_lines = (
        CreditLine.objects.filter(
            credit__regie=regie,
            credit__cancelled_at__isnull=True,
            credit__pool__isnull=True,
            event_slug__in=events,
            user_external_id=user_external_id,
            details__jsonpath_exists=f'$.dates[*] ? (@ < "{date_max}" && @ >= "{date_min}")',
        )
        .annotate(
            invoicing_element_number=F('credit__formatted_number'),
            invoicing_element_created_at=F('credit__created_at'),
            payer_external_id=F('credit__payer_external_id'),
        )
        .exclude(
            total_amount=0,
        )
        .order_by('pk')
    )

    history = collections.defaultdict(dict)
    invoicing_elements = set()
    for line in list(invoice_lines) + list(credit_lines):
        for _date in set(line.details['dates']):
            # for each event, store lines per date included in the campaign
            if _date < date_min.isoformat():
                continue
            if _date >= date_max.isoformat():
                continue
            if not history[line.event_slug].get(_date):
                history[line.event_slug][_date] = []
            history[line.event_slug][_date].append(line)
            invoicing_elements.add((line.invoicing_element_number, line.invoicing_element_created_at))
    # build a listing of invoicing element numbers and creation dates
    invoicing_elements = {number: cdate for number, cdate in invoicing_elements}

    # annotate line with previous invoicing element's creation date, for ordering
    for dates in history.values():
        for _date, lines in dates.items():
            for line in lines:
                before = line.details.get('adjustment', {}).get(_date, {}).get('before')
                after = line.details.get('adjustment', {}).get(_date, {}).get('after')
                if before:
                    line.previous_invoicing_element_created_at = invoicing_elements.get(before)
                    if not line.previous_invoicing_element_created_at:
                        # previous element not found, take line creation date
                        line.previous_invoicing_element_created_at = line.invoicing_element_created_at
                elif after:
                    # no previous element, but there is a next element
                    line.previous_invoicing_element_created_at = invoicing_elements.get(after)
                    if line.previous_invoicing_element_created_at:
                        # move date just before next element's creation date
                        line.previous_invoicing_element_created_at -= datetime.timedelta(milliseconds=1)
                    else:
                        # next element not found, take line creation date
                        line.previous_invoicing_element_created_at = line.invoicing_element_created_at
                else:
                    # default value
                    line.previous_invoicing_element_created_at = line.invoicing_element_created_at

    # rebuild history per event and date
    for dates in history.values():
        for _date, lines in dates.items():
            # sort lines for each event and date
            sorted_lines = sorted(
                lines, key=lambda a: (a.previous_invoicing_element_created_at, a.invoicing_element_created_at)
            )
            # and build the booked/cancelled chain
            links = []
            for line in sorted_lines:
                booked = True
                unit_amount = abs(line.unit_amount)
                if (
                    isinstance(line, InvoiceLine)
                    and line.total_amount < 0
                    or isinstance(line, CreditLine)
                    and line.total_amount > 0
                ):
                    # negative amount for an invoice, positive amount for a credit,
                    # means it was a cancellation
                    booked = False
                links.append(
                    Link(
                        payer_external_id=line.payer_external_id,
                        unit_amount=unit_amount,
                        booked=booked,
                        invoicing_element_number=line.invoicing_element_number,
                    )
                )
            dates[_date] = links

    return history


def get_previous_campaign_journal_lines_for_user(pool, user_external_id, check_status_list):
    # extract event slugs from check_status_list
    events = set()
    for check_status in check_status_list:
        agenda = check_status['event']['agenda']
        event_slug = check_status['event']['slug']
        events.add(f'{agenda}@{event_slug}')

    # get all journal lines
    previous_journal_lines = (
        JournalLine.objects.filter(
            # of previous campaigns: primary campaign or corrective campaigns
            Q(pool__campaign=pool.campaign.primary_campaign)
            | Q(pool__campaign__primary_campaign=pool.campaign.primary_campaign),
            # for these events
            slug__in=events,
            # and this user
            user_external_id=user_external_id,
            # only lines containing chrono information
            pricing_data__booking_details__isnull=False,
        )
        # keep the first recent line by (slug, event_date), so the more recent one
        .order_by(
            'slug',
            'event_date',
            '-pool__created_at',
            '-created_at',
        ).distinct('slug', 'event_date')
    )

    history = collections.defaultdict(dict)
    for line in previous_journal_lines:
        history[line.slug][line.event_date.isoformat()] = line

    return history


def create_journal_line(
    *,
    request,
    pool,
    pricing,
    line_kwargs,
    payer_data_cache,
    quantity,
    amount,
    payer_external_id,
    before=None,
    after=None,
    info=None,
):
    assert 'quantity' not in line_kwargs
    new_line_kwargs = line_kwargs.copy()
    reason = 'missing-cancellation' if quantity == -1 else 'missing-booking'
    adjustment = {'reason': reason, 'before': before, 'after': after, 'info': info}
    adjustment = {k: v for k, v in adjustment.items() if v is not None}
    try:
        payer_data = get_cached_payer_data(
            request, pool.campaign.regie, payer_data_cache, payer_external_id, pricing=pricing
        )
    except InvoicingError as e:
        pricing_error = {
            'error': type(e).__name__,
            'error_details': e.details,
        }
        new_line_kwargs.update(
            {
                'payer_external_id': payer_external_id,
                'payer_first_name': '',
                'payer_last_name': '',
                'payer_address': '',
                'payer_email': '',
                'payer_phone': '',
                'payer_direct_debit': False,
                'pricing_data': {'adjustment': adjustment},
                'status': 'error',
            }
        )
        new_line_kwargs['pricing_data'].update(pricing_error)
    else:
        new_line_kwargs.update(
            {
                'payer_external_id': payer_external_id,
                'payer_first_name': payer_data['first_name'],
                'payer_last_name': payer_data['last_name'],
                'payer_address': payer_data['address'],
                'payer_email': payer_data['email'],
                'payer_phone': payer_data['phone'],
                'payer_direct_debit': payer_data['direct_debit'],
                'pricing_data': {'adjustment': adjustment},
            }
        )
    return DraftJournalLine(
        amount=amount,
        quantity=quantity,
        **new_line_kwargs,
    )


def compare_journal_lines(
    *,
    request,
    pool,
    check_types,
    pricing,
    pricing_data,
    line_kwargs,
    payer_data_cache,
    previous_journal_line,
):
    # calculate new journal line
    new_journal_line = build_journal_line_for_nominal_case(
        pool=pool,
        pricing_data=pricing_data,
        line_kwargs=line_kwargs,
    )

    def has_change(previous_journal_line, new_journal_line):
        old_pricing_data = previous_journal_line.pricing_data
        new_pricing_data = new_journal_line.pricing_data
        old_booking_details = old_pricing_data.get('booking_details', {})
        new_booking_details = new_pricing_data.get('booking_details', {})
        if previous_journal_line.payer_external_id != new_journal_line.payer_external_id:
            return True
        old_pricing = old_pricing_data.get('calculation_details', {}).get('pricing', 0)
        new_pricing = new_pricing_data.get('calculation_details', {}).get('pricing', 0)
        if decimal.Decimal(old_pricing) != decimal.Decimal(new_pricing):
            return True
        if old_booking_details.get('status') != new_booking_details.get('status'):
            return True
        if old_booking_details.get('check_type') != new_booking_details.get('check_type'):
            return True
        if old_booking_details.get('check_type_group') != new_booking_details.get('check_type_group'):
            old_pricing = old_pricing_data.get('pricing', 0)
            new_pricing = new_pricing_data.get('pricing', 0)
            if decimal.Decimal(old_pricing) != decimal.Decimal(new_pricing):
                return True
        return False

    if not has_change(previous_journal_line, new_journal_line):
        return

    def create_correction_journal_line(**kwargs):
        return create_journal_line(
            request=request,
            pool=pool,
            pricing=pricing,
            line_kwargs=line_kwargs,
            payer_data_cache=payer_data_cache,
            info='correction',
            **kwargs,
        )

    def check_journal_line_amount_for_adjustment_campaign(journal_line):
        # In an adjustment campaign, bookings are prepaid. In theory, there are no penalties for any type of check type.
        # The cases encountered and dealt with here are as follows:
        # * for presence, invoicing of:
        #   * 0% (booking prepaid, presence not deduced)
        #   * 100% (unexpected presence, therefore not prepaid, must be invoiced)
        #   * -100% (booking prepaid but deduced)
        # * for an absence, invoicing of:
        #   * 0% (the booking has been prepaid, and the absence is not deduced)
        #   * -100% (booking prepaid but deduced)
        booking_details = journal_line.pricing_data.get('booking_details') or {}
        status = booking_details.get('status')
        if status not in ['presence', 'absence']:
            return False
        check_type = check_types.get(
            (
                booking_details.get('check_type'),
                booking_details.get('check_type_group'),
                booking_details.get('status'),
            )
        )
        initial_amount = decimal.Decimal(journal_line.pricing_data['calculation_details']['pricing'])
        amount = journal_line.amount * journal_line.quantity

        if status == 'presence':
            assert amount in [-initial_amount, 0, initial_amount]
            if amount == initial_amount:
                assert check_type
                assert check_type.group.unexpected_presence == check_type
        else:
            assert amount in [-initial_amount, 0]
        return True

    if not pool.campaign.adjustment_campaign:
        # nominal case
        # cancel previous line
        yield create_correction_journal_line(
            quantity=-previous_journal_line.quantity,
            amount=previous_journal_line.amount,
            payer_external_id=previous_journal_line.payer_external_id,
        )
        # add new line
        yield new_journal_line
        return

    # adjustment campaign case
    previous_status = check_journal_line_amount_for_adjustment_campaign(previous_journal_line)
    new_status = check_journal_line_amount_for_adjustment_campaign(new_journal_line)
    # To correct and apply the new check type, we need to:
    # 1/ Rebalance the previous campaign line so that the payer has been fully deduced.
    #    In the case of a 0% or 100% modifier, refund the amount before applying the modifier;
    #    an absence or presence of -100% has already deduced the payer.
    if previous_status and previous_journal_line.amount * previous_journal_line.quantity >= 0:
        # if previous line has positive or zero amount, credit initial amount
        yield create_correction_journal_line(
            quantity=-1,
            amount=previous_journal_line.pricing_data['calculation_details']['pricing'],
            payer_external_id=previous_journal_line.payer_external_id,
        )
    # 2/ For the corrective campaign, add a invoicing if the new line has a modifier of 0% or -100%.
    #    In the case of unexpected presence or 100%, the corrective campaign line will do the invoicing.
    #    In the case of an absence or attendance of 0%, the booking must be invoiced first.
    #    In the case of an absence or attendance of -100%, the booking must first be invoiced before being deduced by the corrective campaign line.
    if new_status and new_journal_line.amount * new_journal_line.quantity <= 0:
        # if new line has negative or zero amount, invoice initial amount
        yield create_correction_journal_line(
            quantity=1,
            amount=new_journal_line.pricing_data['calculation_details']['pricing'],
            payer_external_id=new_journal_line.payer_external_id,
        )
    # 3/ Then add the new line of the corrective campaign.
    yield new_journal_line


def check_primary_campaign_amounts(
    *,
    request,
    pool,
    check_types,
    pricing,
    line_kwargs,
    payer_data_cache,
    previous_journal_line,
    existing_lines,
):
    booking_details = previous_journal_line.pricing_data.get('booking_details') or {}
    status = booking_details.get('status')
    status = booking_details.get('status')
    if status not in ['presence', 'absence']:
        # booking status must be absence or presence to have a prepayment
        return
    check_type = check_types.get(
        (
            booking_details.get('check_type'),
            booking_details.get('check_type_group'),
            booking_details.get('status'),
        )
    )
    if check_type is not None:
        if status == 'presence' and check_type.group.unexpected_presence == check_type:
            # unexpected presences are not prepaid
            return

    # find links for this event and this date
    event_slug = previous_journal_line.slug
    if previous_journal_line.event.get('primary_event'):
        event_slug = '%s@%s' % (event_slug.split('@')[0], previous_journal_line.event['primary_event'])
    event_date = previous_journal_line.event_date
    links = existing_lines.get(event_slug, {}).get(event_date.isoformat(), [])
    if not links:
        # nothing found, no prepayment, it has been adjusted by the primary campaign
        return

    last_link = links[-1]
    if not last_link.booked:
        # cancel link found, it has been adjusted by the primary campaign
        return

    def create_adjustment_journal_line(**kwargs):
        new_line_kwargs = line_kwargs.copy()
        new_line_kwargs.pop('quantity', None)
        return create_journal_line(
            request=request,
            pool=pool,
            pricing=pricing,
            line_kwargs=new_line_kwargs,
            payer_data_cache=payer_data_cache,
            **kwargs,
        )

    # check if last link has same payer and same amount as calculated
    old_pricing = decimal.Decimal(last_link.unit_amount)
    new_pricing = decimal.Decimal(previous_journal_line.pricing_data['calculation_details']['pricing'])
    if last_link.payer_external_id != previous_journal_line.payer_external_id or old_pricing != new_pricing:
        # pricing or payer have changed
        # create a journal line for a missing cancellation with payer and amount of first link
        yield create_adjustment_journal_line(
            quantity=-1,
            amount=old_pricing,
            payer_external_id=last_link.payer_external_id,
            before=last_link.invoicing_element_number,
            info='pricing-changed',
        )
        # and create a journal line for a missing booking with the correct payer and amount
        yield create_adjustment_journal_line(
            quantity=1,
            amount=new_pricing,
            payer_external_id=previous_journal_line.payer_external_id,
            before=last_link.invoicing_element_number,
            info='pricing-changed',
        )


def build_journal_lines(
    *,
    request,
    pool,
    check_types,
    agenda,
    pricing,
    check_status,
    pricing_data,
    line_kwargs,
    payer_data_cache,
    existing_lines,
    previous_journal_line,
):
    booking_details = pricing_data.get('booking_details') or {}

    if pool.campaign.primary_campaign and agenda.partial_bookings:
        return

    if pool.campaign.primary_campaign and previous_journal_line:
        # corrective campaign, event for user found in previous campaign
        # if not found, do as for primary campaign

        # compare with previous line, and correct if necessary
        yield from compare_journal_lines(
            request=request,
            pool=pool,
            pricing=pricing,
            check_types=check_types,
            pricing_data=pricing_data,
            line_kwargs=line_kwargs,
            payer_data_cache=payer_data_cache,
            previous_journal_line=previous_journal_line,
        )

        # first corrective campaign, check that primary campaign checked last booking link
        # XXX this code can be removed when first adjustment campaigns launched before corrective campaigns feature are corrected
        if not hasattr(pool, '_is_first_corrective_campaign'):
            pool._is_first_corrective_campaign = (
                not Campaign.objects.filter(primary_campaign=pool.campaign.primary_campaign)
                .exclude(pk=pool.campaign.pk)
                .exists()
            )
        if not hasattr(pool, '_has_lines_for_wrong_pricing'):
            pool._has_lines_for_wrong_pricing = JournalLine.objects.filter(
                pool__campaign=pool.campaign.primary_campaign,
                pricing_data__adjustment__info='pricing-changed',
            ).exists()
        if (
            pool.campaign.adjustment_campaign
            and pool._is_first_corrective_campaign
            and not pool._has_lines_for_wrong_pricing
        ):
            yield from check_primary_campaign_amounts(
                request=request,
                pool=pool,
                check_types=check_types,
                pricing=pricing,
                line_kwargs=line_kwargs,
                payer_data_cache=payer_data_cache,
                previous_journal_line=previous_journal_line,
                existing_lines=existing_lines,
            )
        return

    # not corrective campaign, or event not found for user
    if agenda.partial_bookings and booking_details.get('status') in ['absence', 'presence']:
        # partial bookings with absence or presence set
        yield from list(
            build_journal_lines_for_partial_bookings_case(
                request=request,
                check_types=check_types,
                agenda=agenda,
                pricing=pricing,
                check_status=check_status,
                pricing_data=pricing_data,
                line_kwargs=line_kwargs,
            )
        )
    else:
        # nominal case
        yield build_journal_line_for_nominal_case(
            pool=pool,
            pricing_data=pricing_data,
            line_kwargs=line_kwargs,
        )
    if pool.campaign.adjustment_campaign and not agenda.partial_bookings:
        # adjustment campaign case, in addition to nominal case
        yield from list(
            build_journal_lines_for_adjustment_case(
                request=request,
                pool=pool,
                check_types=check_types,
                pricing=pricing,
                check_status=check_status,
                pricing_data=pricing_data,
                payer_data_cache=payer_data_cache,
                existing_lines=existing_lines,
                line_kwargs=line_kwargs,
            )
        )


def build_journal_lines_for_partial_bookings_case(
    *, request, check_types, agenda, pricing, check_status, pricing_data, line_kwargs
):
    # partial booking: we need to first invoice booked hours, then apply overtaking or reductions
    booking_details = pricing_data.get('booking_details') or {}
    serialized_event = check_status['event']
    serialized_booking = check_status['booking']
    check_type = check_types.get(
        (
            booking_details.get('check_type'),
            booking_details.get('check_type_group'),
            booking_details.get('status'),
        )
    )
    quantity = line_kwargs.pop('quantity')
    user_external_id = line_kwargs['user_external_id']
    payer_external_id = line_kwargs['payer_external_id']

    normal_pricing_data = pricing_data
    if booking_details['status'] != 'presence' or booking_details.get('check_type'):
        # calculate pricing for absence without check type
        normal_pricing_data = pricing.get_pricing_data_for_event(
            request=request,
            agenda=agenda,
            event=serialized_event,
            check_status={'status': 'presence', 'check_type': None},
            user_external_id=user_external_id,
            payer_external_id=payer_external_id,
        )
    normal_pricing = normal_pricing_data['pricing']
    current_pricing = pricing_data['pricing']

    if serialized_booking.get('adjusted_duration'):
        # there was a booking,
        # first add line for booked hours
        new_line_kwargs = line_kwargs.copy()
        new_line_kwargs['label'] = agenda.label
        yield DraftJournalLine(
            description='@booked-hours@',
            amount=normal_pricing,
            quantity=serialized_booking['adjusted_duration'],
            pricing_data=normal_pricing_data,
            **new_line_kwargs,
        )
        # and add overtaking if needed
        if serialized_booking['computed_duration'] > serialized_booking['adjusted_duration']:
            new_line_kwargs = line_kwargs.copy()
            overtaking_event = copy.deepcopy(serialized_event)
            if overtaking_event.get('primary_event'):
                overtaking_event['primary_event'] += '::overtaking'
            new_line_kwargs['event'] = overtaking_event
            new_line_kwargs['label'] = _('Overtaking')
            yield DraftJournalLine(
                description='@overtaking@',  # so days will be not displayed
                amount=normal_pricing,
                quantity=serialized_booking['computed_duration'] - serialized_booking['adjusted_duration'],
                pricing_data=normal_pricing_data,
                **new_line_kwargs,
            )
        diff_pricing = current_pricing - normal_pricing
        if diff_pricing != 0:
            new_line_kwargs = line_kwargs.copy()
            # reduction of overcharging to apply
            new_line_kwargs['label'] = ''
            if check_type:
                new_line_kwargs['label'] = check_type.label
            elif booking_details['status'] == 'absence':
                new_line_kwargs['label'] = _('Absence')
            diff_event = copy.deepcopy(serialized_event)
            if diff_event.get('primary_event'):
                diff_event['primary_event'] += ':%s:%s' % (
                    booking_details['status'],
                    booking_details.get('check_type') or '',
                )
            new_line_kwargs['event'] = diff_event
            # if negative pricing, quantity is negative and pricing is set to the opposite
            amount = abs(diff_pricing)
            quantity = serialized_booking['computed_duration']
            if diff_pricing < 0:
                quantity = -quantity
            yield DraftJournalLine(
                amount=amount,
                quantity=quantity,
                pricing_data=pricing_data,
                **new_line_kwargs,
            )
    else:
        # no booking
        new_line_kwargs = line_kwargs.copy()
        if check_type:
            new_line_kwargs['label'] = check_type.label
        elif booking_details.get('status') == 'presence':
            new_line_kwargs['label'] = _('Presence without booking')
        yield DraftJournalLine(
            amount=pricing_data['pricing'],
            quantity=quantity,
            pricing_data=pricing_data,
            **new_line_kwargs,
        )


def build_journal_lines_for_adjustment_case(
    *,
    request,
    pool,
    check_types,
    pricing,
    check_status,
    pricing_data,
    payer_data_cache,
    existing_lines,
    line_kwargs,
):
    # The adjustment campaign assumes that all bookings have been prepaid.
    # It deduces justified absences that have been deducted, invoices non booked presences,
    # in addition to invoice bookings according to presence/absence check type.
    # If some bookings/cancellations were not invoiced, because wcs did not inform lingo,
    # new journal lines are required to fix the situation and invoice/deduce things.
    serialized_event = check_status['event']
    booking_details = pricing_data.get('booking_details') or {}
    agenda = check_status['event']['agenda']
    event_slug = check_status['event']['slug']
    if check_status['event'].get('primary_event'):
        event_slug = check_status['event']['primary_event']
    event_slug = f'{agenda}@{event_slug}'

    # find links for this event and this date
    event_date = datetime.datetime.fromisoformat(serialized_event['start_datetime']).date()
    links = existing_lines.get(event_slug, {}).get(event_date.isoformat(), [])

    # determine how the links should end
    mapping = {
        'not-booked': 'cancelled',
        'cancelled': 'cancelled',
        'unexpected-presence': 'cancelled',
        'presence': 'booking',
        'absence': 'booking',
    }
    status = booking_details.get('status')
    check_type = check_types.get(
        (
            booking_details.get('check_type'),
            booking_details.get('check_type_group'),
            booking_details.get('status'),
        )
    )
    if check_type is not None:
        if status == 'presence' and check_type.group.unexpected_presence == check_type:
            status = 'unexpected-presence'
    if status not in mapping:
        return []
    final_link = mapping[status]

    def create_adjustment_journal_line(**kwargs):
        return create_journal_line(
            request=request,
            pool=pool,
            pricing=pricing,
            line_kwargs=line_kwargs,
            payer_data_cache=payer_data_cache,
            **kwargs,
        )

    for result in check_links(
        final_link=final_link,
        current_pricing=(
            pricing_data['calculation_details']['pricing']
            if final_link == 'booking'
            else pricing_data['pricing']
        ),
        current_payer_external_id=line_kwargs['payer_external_id'],
        remaining_links=links,
    ):
        yield create_adjustment_journal_line(**result)


def check_links(
    *,
    final_link,
    current_pricing,
    current_payer_external_id,
    remaining_links,
    previous_invoicing_element_number=None,
    fix_when_pricing_changed=True,
):
    if not remaining_links:
        # end of links
        if final_link == 'booking':
            # if final_link should be 'booking', create a journal line for a missing booking
            yield {
                'quantity': 1,
                'amount': decimal.Decimal(current_pricing),
                'payer_external_id': current_payer_external_id,
                'before': previous_invoicing_element_number,
            }
        return

    first_link = remaining_links[0]
    if not first_link.booked:
        # it should be a booking; create a journal line for a missing booking
        yield {
            'quantity': 1,
            'amount': first_link.unit_amount,
            'payer_external_id': first_link.payer_external_id,
            'before': previous_invoicing_element_number,
            'after': first_link.invoicing_element_number,
        }
        # check the rest of links
        yield from check_links(
            final_link=final_link,
            current_pricing=current_pricing,
            current_payer_external_id=current_payer_external_id,
            remaining_links=remaining_links[1:],
            fix_when_pricing_changed=fix_when_pricing_changed,
        )
        return

    if len(remaining_links) == 1:
        # end of links
        if final_link == 'cancelled':
            # if final_links should be 'cancelled', create a journal line for a missing cancellation
            # with the same payer and amount as previous booking
            yield {
                'quantity': -1,
                'amount': first_link.unit_amount,
                'payer_external_id': first_link.payer_external_id,
                'before': first_link.invoicing_element_number,
            }
            return

        if not fix_when_pricing_changed:
            return

        # check if last link, a booking, has same payer and same amount as calculated
        old_pricing = decimal.Decimal(first_link.unit_amount)
        new_pricing = decimal.Decimal(current_pricing)
        if first_link.payer_external_id != current_payer_external_id or old_pricing != new_pricing:
            # pricing or payer have changed
            # create a journal line for a missing cancellation with payer and amount of first link
            yield {
                'quantity': -1,
                'amount': old_pricing,
                'payer_external_id': first_link.payer_external_id,
                'before': first_link.invoicing_element_number,
                'info': 'pricing-changed',
            }
            # and create a journal line for a missing booking with the correct payer and amount
            yield {
                'quantity': 1,
                'amount': current_pricing,
                'payer_external_id': current_payer_external_id,
                'before': first_link.invoicing_element_number,
                'info': 'pricing-changed',
            }
        return

    # check that next link is a cancellation with the same payer and the same amount
    second_link = remaining_links[1]

    if second_link.booked:
        # it should be a cancellation; create a journal line for a missing cancellation
        yield {
            'quantity': -1,
            'amount': first_link.unit_amount,
            'payer_external_id': first_link.payer_external_id,
            'before': first_link.invoicing_element_number,
            'after': second_link.invoicing_element_number,
        }
        # check the rest of links
        yield from check_links(
            final_link=final_link,
            current_pricing=current_pricing,
            current_payer_external_id=current_payer_external_id,
            remaining_links=remaining_links[1:],
            fix_when_pricing_changed=fix_when_pricing_changed,
        )
        return

    if (
        first_link.unit_amount != second_link.unit_amount
        or first_link.payer_external_id != second_link.payer_external_id
    ):
        # amount or payer are not the same
        # create a journal line for a missing cancellation with payer and amount of first link
        yield {
            'quantity': -1,
            'amount': first_link.unit_amount,
            'payer_external_id': first_link.payer_external_id,
            'before': first_link.invoicing_element_number,
            'after': second_link.invoicing_element_number,
        }
        # and create a journal line for a missing booking with ppayer and amount of second link
        yield {
            'quantity': 1,
            'amount': second_link.unit_amount,
            'payer_external_id': second_link.payer_external_id,
            'before': first_link.invoicing_element_number,
            'after': second_link.invoicing_element_number,
        }
    # check the rest of links
    yield from check_links(
        final_link=final_link,
        current_pricing=current_pricing,
        current_payer_external_id=current_payer_external_id,
        remaining_links=remaining_links[2:],
        previous_invoicing_element_number=second_link.invoicing_element_number,
        fix_when_pricing_changed=fix_when_pricing_changed,
    )


def build_journal_line_for_nominal_case(*, pool, pricing_data, line_kwargs):
    booking_details = pricing_data.get('booking_details') or {}
    amount = pricing_data['pricing']
    quantity = line_kwargs.pop('quantity')
    if amount < 0:
        # negative amount, change for positive amount and negative quantity
        amount = -amount
        quantity = -quantity
    if (
        pool.campaign.adjustment_campaign
        and booking_details.get('status') == 'presence'
        and not booking_details.get('check_type')
    ):
        # adjustment campaign, booking checked as presence without checktype should be ignored, amount is set to 0
        amount = 0
    return DraftJournalLine(
        amount=amount,
        quantity=quantity,
        pricing_data=pricing_data,
        **line_kwargs,
    )


def get_lines_for_user(
    agendas,
    agendas_pricings,
    user_external_id,
    user_first_name,
    user_last_name,
    pool,
    payer_data_cache,
    request=None,
):
    if not agendas:
        return []

    # get check status for user_external_id, on agendas, for the period
    check_status_list = get_check_status(
        agenda_slugs=[a.slug for a in agendas],
        user_external_id=user_external_id,
        date_start=pool.campaign.date_start,
        date_end=pool.campaign.date_end,
    )

    return build_lines_for_user(
        agendas=agendas,
        agendas_pricings=agendas_pricings,
        user_external_id=user_external_id,
        user_first_name=user_first_name,
        user_last_name=user_last_name,
        pool=pool,
        payer_data_cache=payer_data_cache,
        check_status_list=check_status_list,
        request=request,
    )


def replay_error(original_error_line):
    if '@' not in original_error_line.slug:
        raise Agenda.DoesNotExist
    agenda_slug = original_error_line.slug.split('@')[0]
    agenda = Agenda.objects.get(slug=agenda_slug)
    event_date = original_error_line.event_date
    agendas_pricings = (
        Pricing.objects.filter(flat_fee_schedule=False, agendas=agenda)
        .extra(
            where=['(date_start, date_end) OVERLAPS (%s, %s)'],
            params=[event_date, event_date + datetime.timedelta(days=1)],
        )
        .prefetch_related('agendas', 'criterias', 'categories')
    )
    return redo_lines_for_user_and_event(
        agenda=agenda, agendas_pricings=agendas_pricings, original_error_line=original_error_line
    )


def redo_lines_for_user_and_event(*, agenda, agendas_pricings, original_error_line):
    pool = original_error_line.pool
    user_external_id = original_error_line.user_external_id
    event_date = original_error_line.event_date
    slug = original_error_line.slug

    # get all line related to this user and this event
    old_lines = DraftJournalLine.objects.filter(
        pool=pool,
        user_external_id=user_external_id,
        event_date=event_date,
        slug=slug,
    )

    # get check status for user_external_id, just for this event
    check_status_list = []
    for check_status in get_check_status(
        agenda_slugs=[agenda.slug],
        user_external_id=user_external_id,
        date_start=event_date,
        date_end=event_date + datetime.timedelta(days=1),
    ):
        serialized_event = check_status['event']
        # remove other events
        if '%s@%s' % (serialized_event['agenda'], serialized_event['slug']) != slug:
            continue
        if datetime.datetime.fromisoformat(serialized_event['start_datetime']).date() != event_date:
            continue
        check_status_list.append(check_status)

    with transaction.atomic():
        # get all payers from old lines
        payer_external_ids = {line.payer_external_id for line in old_lines}
        # delete old lines
        old_lines.delete()
        # build new lines
        new_lines = build_lines_for_user(
            agendas=[agenda],
            agendas_pricings=agendas_pricings,
            user_external_id=user_external_id,
            user_first_name=original_error_line.user_first_name,
            user_last_name=original_error_line.user_last_name,
            pool=pool,
            payer_data_cache={},
            check_status_list=check_status_list,
        )
        # get all payers from new lines
        payer_external_ids.update({line.payer_external_id for line in new_lines})
        # delete old invoices for these payers
        old_invoice_lines = DraftInvoiceLine.objects.filter(
            pool=pool, invoice__payer_external_id__in=payer_external_ids
        )
        DraftJournalLine.objects.filter(invoice_line__in=old_invoice_lines).update(invoice_line=None)
        old_invoice_lines.delete()
        DraftInvoice.objects.filter(pool=pool, payer_external_id__in=payer_external_ids).delete()
        # and rebuild invoices
        return generate_invoices_from_lines(pool, payer_external_ids=payer_external_ids)


def build_lines_for_user(
    *,
    agendas,
    agendas_pricings,
    user_external_id,
    user_first_name,
    user_last_name,
    pool,
    payer_data_cache,
    check_status_list,
    request=None,
):
    if not agendas:
        return []

    request = request or RequestFactory().get('/')
    request.requests_max_retries = settings.CAMPAIGN_REQUEST_MAX_RETRIES
    request.requests_timeout = settings.CAMPAIGN_REQUEST_TIMEOUT

    agendas_by_slug = {a.slug: a for a in agendas}
    agendas_pricings_by_agendas = collections.defaultdict(list)
    for pricing in agendas_pricings:
        if pricing.flat_fee_schedule:
            continue
        for agenda in pricing.agendas.all():
            agendas_pricings_by_agendas[agenda.slug].append(pricing)
    check_types = {(c.slug, c.group.slug, c.kind): c for c in CheckType.objects.select_related('group').all()}

    existing_lines = {}
    if pool.campaign.adjustment_campaign:
        # get existing invoice/credit lines for this user_external_id and events from chrono
        existing_lines = get_existing_lines_for_user(
            regie=pool.campaign.regie,
            date_min=pool.campaign.date_start,
            date_max=pool.campaign.date_end,
            user_external_id=user_external_id,
            serialized_events=[cs['event'] for cs in check_status_list],
        )

    previous_lines = {}
    if pool.campaign.primary_campaign:
        # get journal lines of previous campaign
        previous_lines = get_previous_campaign_journal_lines_for_user(
            pool=pool, user_external_id=user_external_id, check_status_list=check_status_list
        )

    # get resolved errors in last pool for this user
    if not hasattr(pool, '_previous_pool'):
        pool._previous_pool = pool.campaign.pool_set.exclude(pk=pool.pk).order_by('created_at').last()
    previous_pool = pool._previous_pool
    resolved_errors = (
        {
            li.slug: li
            for li in DraftJournalLine.objects.filter(
                pool=previous_pool, status='error', user_external_id=user_external_id
            ).exclude(error_status='')
        }
        if previous_pool is not None
        else {}
    )

    # build lines from check status
    lines = []
    for check_status in check_status_list:
        serialized_event = check_status['event']
        serialized_booking = check_status['booking']
        event_date = datetime.datetime.fromisoformat(serialized_event['start_datetime']).date()
        event_slug = '%s@%s' % (serialized_event['agenda'], serialized_event['slug'])
        previous_journal_line = previous_lines.get(event_slug, {}).get(event_date.isoformat())

        agenda = agendas_by_slug[serialized_event['agenda']]
        quantity = 1
        quantity_type = 'units'
        if agenda.partial_bookings:
            quantity = serialized_booking.get('computed_duration') or 0
            quantity_type = 'minutes'
        payer_external_id = _('unknown')
        payer_data = {}
        try:
            pricing = get_pricing(agendas_pricings_by_agendas.get(agenda.slug), event_date)
            payer_external_id = pool.campaign.regie.get_payer_external_id(
                request, user_external_id, serialized_booking
            )
            payer_data = get_cached_payer_data(
                request, pool.campaign.regie, payer_data_cache, payer_external_id, pricing=pricing
            )
            pricing_data = pricing.get_pricing_data_for_event(
                request=request,
                agenda=agenda,
                event=serialized_event,
                check_status=check_status['check_status'],
                user_external_id=user_external_id,
                payer_external_id=payer_external_id,
            )

            line_kwargs = {
                'label': serialized_event['label'],
                'event_date': event_date,
                'slug': event_slug,
                'quantity': quantity,
                'quantity_type': quantity_type,
                'user_external_id': user_external_id,
                'user_first_name': user_first_name,
                'user_last_name': user_last_name,
                'payer_external_id': payer_external_id,
                'payer_first_name': payer_data['first_name'],
                'payer_last_name': payer_data['last_name'],
                'payer_address': payer_data['address'],
                'payer_email': payer_data['email'],
                'payer_phone': payer_data['phone'],
                'payer_direct_debit': payer_data['direct_debit'],
                'event': serialized_event,
                'booking': serialized_booking,
                'status': 'success',
                'pool': pool,
                'accounting_code': pricing_data.get('accounting_code') or '',
            }
            lines += build_journal_lines(
                request=request,
                pool=pool,
                check_types=check_types,
                agenda=agenda,
                pricing=pricing,
                check_status=check_status,
                pricing_data=pricing_data,
                line_kwargs=line_kwargs,
                payer_data_cache=payer_data_cache,
                existing_lines=existing_lines,
                previous_journal_line=previous_journal_line,
            )
        except (PricingError, InvoicingError) as e:
            # PricingNotFound: can happen if pricing model defined only on a part of the requested period
            pricing_error = {
                'error': type(e).__name__,
                'error_details': e.details,
            }
            error_status = ''
            if resolved_errors.get(event_slug):
                error_status = resolved_errors[event_slug].error_status
            lines.append(
                DraftJournalLine(
                    event_date=event_date,
                    slug=event_slug,
                    label=serialized_event['label'],
                    amount=0,
                    quantity=quantity,
                    quantity_type=quantity_type,
                    user_external_id=user_external_id,
                    user_first_name=user_first_name,
                    user_last_name=user_last_name,
                    payer_external_id=payer_external_id,
                    payer_first_name=payer_data.get('first_name') or '',
                    payer_last_name=payer_data.get('last_name') or '',
                    payer_address=payer_data.get('address') or '',
                    payer_email=payer_data.get('email') or '',
                    payer_phone=payer_data.get('phone') or '',
                    payer_direct_debit=payer_data.get('direct_debit') or False,
                    event=serialized_event,
                    booking=serialized_booking,
                    pricing_data=pricing_error,
                    status='warning' if isinstance(e, PricingNotFound) else 'error',
                    error_status=error_status,
                    pool=pool,
                )
            )

    if pool.campaign.injected_lines != 'no':
        # fetch injected lines
        injected_lines = (
            InjectedLine.objects.filter(
                regie=pool.campaign.regie,
                event_date__lt=pool.campaign.date_end,
                user_external_id=user_external_id,
            )
            .exclude(
                # exclude already invoiced lines
                journalline__isnull=False
            )
            .exclude(
                # exclude lines used in another campaign
                pk__in=DraftJournalLine.objects.filter(from_injected_line__isnull=False)
                .exclude(pool__campaign=pool.campaign)
                .values('from_injected_line')
            )
            .order_by('event_date')
        )
        if pool.campaign.injected_lines == 'period':
            injected_lines = injected_lines.filter(
                event_date__gte=pool.campaign.date_start,
            )

        for injected_line in injected_lines:
            payer_external_id = injected_line.payer_external_id
            payer_data = {
                'first_name': injected_line.payer_first_name,
                'last_name': injected_line.payer_last_name,
                'address': injected_line.payer_address,
                'email': '',
                'phone': '',
                'direct_debit': injected_line.payer_direct_debit,
            }
            payer_data = get_cached_payer_data(
                request, pool.campaign.regie, payer_data_cache, payer_external_id, payer_data=payer_data
            )
            lines.append(
                DraftJournalLine(
                    event_date=injected_line.event_date,
                    slug=injected_line.slug,
                    label=injected_line.label,
                    amount=injected_line.amount,
                    user_external_id=user_external_id,
                    user_first_name=user_first_name,
                    user_last_name=user_last_name,
                    payer_external_id=payer_external_id,
                    payer_first_name=payer_data['first_name'],
                    payer_last_name=payer_data['last_name'],
                    payer_address=payer_data['address'],
                    payer_email=payer_data['email'],
                    payer_phone=payer_data['phone'],
                    payer_direct_debit=payer_data['direct_debit'],
                    status='success',
                    pool=pool,
                    from_injected_line=injected_line,
                )
            )

    DraftJournalLine.objects.bulk_create(lines)

    return lines


def build_lines_for_users(agendas, users, pool, job=None):
    agendas_pricings = (
        Pricing.objects.filter(flat_fee_schedule=False)
        .extra(
            where=['(date_start, date_end) OVERLAPS (%s, %s)'],
            params=[pool.campaign.date_start, pool.campaign.date_end],
        )
        .prefetch_related('agendas', 'criterias', 'categories')
    )

    payer_data_cache = {}
    request = RequestFactory().get('/')
    if job:
        job.set_total_count(len(users.keys()))
    for user_external_id, (user_first_name, user_last_name) in users.items():
        if not Pool.objects.filter(pk=pool.pk, status='running').exists():
            return
        # generate lines for each user
        get_lines_for_user(
            agendas=agendas,
            agendas_pricings=agendas_pricings,
            user_external_id=user_external_id,
            user_first_name=user_first_name,
            user_last_name=user_last_name,
            pool=pool,
            payer_data_cache=payer_data_cache,
            request=request,
        )
        if job:
            job.increment_count()


def generate_invoices_from_lines(pool, payer_external_ids=None, job=None):
    regie = pool.campaign.regie

    # get journal lines of the pool
    if payer_external_ids is not None:
        all_lines = pool.draftjournalline_set.filter(payer_external_id__in=payer_external_ids).order_by('pk')
    else:
        all_lines = pool.draftjournalline_set.all().order_by('pk')

    # regroup lines by payer_external_id (payer)
    lines = {}
    for line in all_lines:
        if line.status != 'success':
            # ignore lines in error
            continue
        if line.payer_external_id not in lines:
            lines[line.payer_external_id] = {
                'payer_first_name': line.payer_first_name,
                'payer_last_name': line.payer_last_name,
                'payer_address': line.payer_address,
                'payer_email': line.payer_email,
                'payer_phone': line.payer_phone,
                'payer_direct_debit': line.payer_direct_debit,
                'lines': [],
            }
        lines[line.payer_external_id]['lines'].append(line)

    if job:
        job.set_total_count(len(lines.keys()))

    def is_line_to_be_ignored(line):
        booking_details = line.pricing_data.get('booking_details', {})
        if booking_details.get('status') in ['not-booked', 'cancelled']:
            # ignore not booked or cancelled events
            return True
        if (
            pool.campaign.adjustment_campaign
            and booking_details.get('status') == 'presence'
            and not booking_details.get('check_type')
        ):
            # ignore lines with presence without check type in adjustment mode
            return True
        return False

    # generate invoices by regie and by payer_external_id (payer)
    invoices = []
    agendas_by_slug = {a.slug: a for a in Agenda.objects.all()}
    check_types = {(c.slug, c.group.slug, c.kind): c for c in CheckType.objects.select_related('group').all()}
    for payer_external_id, payer_data in lines.items():
        if not Pool.objects.filter(pk=pool.pk, status='running').exists():
            if job:
                job.increment_count()
            return []
        # regroup journal lines by user_external_id, status, check_type, check_type_group, pricing
        grouped_lines = collections.defaultdict(list)
        other_lines = []
        for line in payer_data['lines']:
            if is_line_to_be_ignored(line):
                continue
            if not line.event.get('primary_event'):
                # not a recurring event
                other_lines.append(line)
                continue
            key = (
                line.user_external_id,
                line.event['agenda'],
                line.event['primary_event'],
                line.pricing_data.get('booking_details', {}).get('status'),
                line.pricing_data.get('booking_details', {}).get('check_type'),
                line.pricing_data.get('booking_details', {}).get('check_type_group'),
                line.pricing_data.get('adjustment', {}).get('reason'),
                line.amount,
                line.quantity_type,
                line.accounting_code,
            )
            if key[6] in ['missing-booking', 'missing-cancellation']:
                other_key = list(key).copy()
                other_key[6] = (
                    'missing-booking' if key[6] == 'missing-cancellation' else 'missing-cancellation'
                )
                other_grouped_lines = grouped_lines.get(tuple(other_key)) or []
                other_line_found = False
                for other_line in other_grouped_lines:
                    if other_line.event_date == line.event_date:
                        other_grouped_lines.remove(other_line)
                        other_line_found = True
                        break
                if other_line_found:
                    continue
            grouped_lines[key].append(line)
        invoice_lines = []
        for key, journal_lines in grouped_lines.items():
            if not journal_lines:
                continue
            journal_lines = sorted(journal_lines, key=lambda li: li.pk)
            first_line = journal_lines[0]
            check_type = check_types.get((key[4], key[5], key[3]))
            event_datetime = localtime(
                datetime.datetime.fromisoformat(journal_lines[0].event['start_datetime'])
            )
            event_time = event_datetime.time().isoformat()
            quantity = sum(li.quantity for li in journal_lines)
            if key[8] == 'minutes':
                quantity = quantity / 60  # convert in hours
            agenda = agendas_by_slug.get(key[1])
            dates = sorted(li.event_date for li in journal_lines)
            description = ''
            adjustment_reason = ''
            if first_line.description:
                description = first_line.description
                if description == '@booked-hours@':
                    rounded_quantity = int(quantity)  # remove leading zero
                    if rounded_quantity != quantity:
                        rounded_quantity = quantity
                    description = _('%s booked hours for the period') % rounded_quantity
            elif dates:
                if key[6] == 'missing-booking':
                    adjustment_reason = _('Booking (regularization)')
                if key[6] == 'missing-cancellation':
                    adjustment_reason = _('Cancellation (regularization)')
                description = ', '.join(formats.date_format(d, 'd/m') for d in dates)
            invoice_line = DraftInvoiceLine(
                event_date=pool.campaign.date_start,
                label=first_line.label,
                quantity=quantity,
                unit_amount=first_line.amount,
                details={
                    'agenda': key[1],
                    'primary_event': key[2],
                    'status': key[3],
                    'check_type': key[4],
                    'check_type_group': key[5],
                    'check_type_label': adjustment_reason or (check_type.label if check_type else key[4]),
                    'dates': dates,
                    'event_time': event_time,
                    'partial_bookings': agenda.partial_bookings if agenda else False,
                },
                event_slug='%s@%s' % (key[1], key[2]),
                event_label=first_line.event.get('label') or first_line.label,
                agenda_slug=key[1],
                activity_label=agenda.label if agenda else '',
                description=description,
                accounting_code=key[9],
                user_external_id=first_line.user_external_id,
                user_first_name=first_line.user_first_name,
                user_last_name=first_line.user_last_name,
                pool=pool,
            )
            invoice_line._journal_lines = journal_lines
            invoice_lines.append(invoice_line)
        for line in other_lines:
            agenda_slug = ''
            if '@' in line.slug:
                agenda_slug = line.slug.split('@')[0]
            agenda = agendas_by_slug.get(agenda_slug)
            description = ''
            reason = line.pricing_data.get('adjustment', {}).get('reason')
            if reason == 'missing-booking':
                description = _('Booking (regularization)')
            if reason == 'missing-cancellation':
                description = _('Cancellation (regularization)')
            invoice_line = DraftInvoiceLine(
                event_date=line.event_date,
                label=line.label,
                quantity=line.quantity,
                unit_amount=line.amount,
                event_slug=line.slug,
                event_label=line.event.get('label') or line.label,
                agenda_slug=agenda_slug,
                activity_label=agenda.label if agenda else '',
                description=description,
                user_external_id=line.user_external_id,
                user_first_name=line.user_first_name,
                user_last_name=line.user_last_name,
                pool=pool,
            )
            invoice_line._journal_lines = [line]
            invoice_lines.append(invoice_line)

        if not invoice_lines:
            # don't create empty invoice
            if job:
                job.increment_count()
            continue

        invoice = DraftInvoice.objects.create(
            label=_('Invoice from %(start)s to %(end)s')
            % {
                'start': pool.campaign.date_start.strftime('%d/%m/%Y'),
                'end': (pool.campaign.date_end - datetime.timedelta(days=1)).strftime('%d/%m/%Y'),
            },
            date_publication=pool.campaign.date_publication,
            date_payment_deadline=pool.campaign.date_payment_deadline,
            date_due=pool.campaign.date_due,
            date_debit=pool.campaign.date_debit if payer_data['payer_direct_debit'] else None,
            regie=regie,
            payer_external_id=payer_external_id,
            payer_first_name=payer_data['payer_first_name'],
            payer_last_name=payer_data['payer_last_name'],
            payer_address=payer_data['payer_address'],
            payer_email=payer_data['payer_email'],
            payer_phone=payer_data['payer_phone'],
            payer_direct_debit=payer_data['payer_direct_debit'],
            pool=pool,
            origin='campaign',
        )
        for invoice_line in invoice_lines:
            invoice_line.invoice = invoice
        DraftInvoiceLine.objects.bulk_create(invoice_lines)
        for invoice_line in invoice_lines:
            DraftJournalLine.objects.filter(pk__in=[li.pk for li in invoice_line._journal_lines]).update(
                invoice_line=invoice_line
            )
        invoices.append(invoice)
        if job:
            job.increment_count()

    return invoices


def export_site(
    regies=True,
):
    '''Dump site objects to JSON-dumpable dictionnary'''
    data = {}
    if regies:
        data['regies'] = [x.export_json() for x in Regie.objects.all()]
    return data


def import_site(data):
    results = {
        key: collections.defaultdict(list)
        for key in [
            'regies',
        ]
    }

    with transaction.atomic():
        for cls, key in ((Regie, 'regies'),):
            objs = data.get(key, [])
            for obj in objs:
                created, obj = cls.import_json(obj)
                results[key]['all'].append(obj)
                if created:
                    results[key]['created'].append(obj)
                else:
                    results[key]['updated'].append(obj)
    return results
