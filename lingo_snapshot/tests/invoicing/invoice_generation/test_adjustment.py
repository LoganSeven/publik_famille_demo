import datetime
import decimal
from unittest import mock

import pytest
from django.utils.timezone import now

from lingo.agendas.models import Agenda, CheckType, CheckTypeGroup
from lingo.invoicing import utils
from lingo.invoicing.errors import PayerDataError
from lingo.invoicing.models import (
    Campaign,
    Credit,
    CreditLine,
    DraftInvoice,
    DraftInvoiceLine,
    DraftJournalLine,
    Invoice,
    InvoiceLine,
    Pool,
    Regie,
)
from lingo.pricing.models import Pricing

pytestmark = pytest.mark.django_db


def test_get_existing_lines_for_user():
    regie = Regie.objects.create(label='Regie')
    campaign = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_debit=datetime.date(2022, 11, 15),
    )
    pool = Pool.objects.create(
        campaign=campaign,
        draft=True,
    )
    check_status_list = [
        {
            'event': {
                'agenda': 'agenda-1',
                'slug': 'event-1',
                'primary_event': 'primary-event-1',
            },
        },
        {
            'event': {
                'agenda': 'agenda-1',
                'slug': 'event-2',
            },
        },
        {
            'event': {
                'agenda': 'agenda-2',
                'slug': 'event-2',
            },
        },
        {
            'event': {
                'agenda': 'agenda-3',
                'slug': 'event-1',
                'primary_event': 'primary-event-1',
            },
        },
    ]

    # no invoice/credit lines
    assert (
        utils.get_existing_lines_for_user(
            regie=regie,
            date_min=pool.campaign.date_start,
            date_max=pool.campaign.date_end,
            user_external_id='user:1',
            serialized_events=[cs['event'] for cs in check_status_list],
        )
        == {}
    )

    def create_invoice(payer_external_id):
        invoice = Invoice.objects.create(
            date_publication=campaign.date_publication,
            date_payment_deadline=campaign.date_payment_deadline,
            date_due=campaign.date_due,
            regie=regie,
            payer_external_id=payer_external_id,
        )
        invoice.set_number()
        invoice.save()
        return invoice

    def create_credit(payer_external_id):
        credit = Credit.objects.create(
            date_publication=campaign.date_publication,
            regie=regie,
            payer_external_id=payer_external_id,
        )
        credit.set_number()
        credit.save()
        return credit

    # invoice and credit lines but without matching values
    invoice = create_invoice('payer:1')
    credit = create_credit('payer:2')
    line_args = {
        'event_date': now().date(),
        'quantity': 1,
        'unit_amount': 1,
        'event_slug': 'agenda-1@primary-event-1',
        'user_external_id': 'user:1',
        'details': {'dates': ['2022-09-01', '2022-09-30']},
    }
    invoice_line_args = line_args.copy()
    invoice_line_args['invoice'] = invoice
    credit_line_args = line_args.copy()
    credit_line_args['credit'] = credit
    credit_line_args['unit_amount'] = 2
    wrong_values = [
        ('event_slug', 'foo'),
        ('user_external_id', 'user:2'),
        ('details', {}),
        ('details', {'date': []}),
        ('details', {'date': ['2022-08-31']}),
        ('details', {'date': ['2022-10-01']}),
    ]
    for key, val in wrong_values:
        new_line_args = invoice_line_args.copy()
        new_line_args[key] = val
        InvoiceLine.objects.create(
            **new_line_args,
        )
        new_line_args = credit_line_args.copy()
        new_line_args[key] = val
        CreditLine.objects.create(
            **new_line_args,
        )
    assert (
        utils.get_existing_lines_for_user(
            regie=regie,
            date_min=pool.campaign.date_start,
            date_max=pool.campaign.date_end,
            user_external_id='user:1',
            serialized_events=[cs['event'] for cs in check_status_list],
        )
        == {}
    )

    # invoice and credit line with matching values
    new_line_args = invoice_line_args.copy()
    InvoiceLine.objects.create(
        **new_line_args,
    )
    new_line_args = credit_line_args.copy()
    CreditLine.objects.create(
        **new_line_args,
    )
    assert utils.get_existing_lines_for_user(
        regie=regie,
        date_min=pool.campaign.date_start,
        date_max=pool.campaign.date_end,
        user_external_id='user:1',
        serialized_events=[cs['event'] for cs in check_status_list],
    ) == {
        'agenda-1@primary-event-1': {
            '2022-09-01': [
                utils.Link(
                    payer_external_id='payer:1',
                    unit_amount=1,
                    booked=True,
                    invoicing_element_number='F%02d-%s-0000001'
                    % (regie.pk, invoice.created_at.strftime('%y-%m')),
                ),
                utils.Link(
                    payer_external_id='payer:2',
                    unit_amount=2,
                    booked=False,
                    invoicing_element_number='A%02d-%s-0000001'
                    % (regie.pk, credit.created_at.strftime('%y-%m')),
                ),
            ],
            '2022-09-30': [
                utils.Link(
                    payer_external_id='payer:1',
                    unit_amount=1,
                    booked=True,
                    invoicing_element_number='F%02d-%s-0000001'
                    % (regie.pk, invoice.created_at.strftime('%y-%m')),
                ),
                utils.Link(
                    payer_external_id='payer:2',
                    unit_amount=2,
                    booked=False,
                    invoicing_element_number='A%02d-%s-0000001'
                    % (regie.pk, credit.created_at.strftime('%y-%m')),
                ),
            ],
        },
    }

    # invoice and credit are cancelled
    invoice.cancelled_at = now()
    invoice.save()
    credit.cancelled_at = now()
    credit.save()
    assert (
        utils.get_existing_lines_for_user(
            regie=regie,
            date_min=pool.campaign.date_start,
            date_max=pool.campaign.date_end,
            user_external_id='user:1',
            serialized_events=[cs['event'] for cs in check_status_list],
        )
        == {}
    )
    # invoice and credit are in a pool
    other_pool = Pool.objects.create(
        campaign=campaign,
        draft=False,
    )
    invoice.cancelled_at = None
    invoice.pool = other_pool
    invoice.save()
    credit.cancelled_at = None
    credit.pool = other_pool
    credit.save()
    assert (
        utils.get_existing_lines_for_user(
            regie=regie,
            date_min=pool.campaign.date_start,
            date_max=pool.campaign.date_end,
            user_external_id='user:1',
            serialized_events=[cs['event'] for cs in check_status_list],
        )
        == {}
    )
    # invoice and create are in another regie
    other_regie = Regie.objects.create(label='Other Regie')
    invoice.pool = None
    invoice.regie = other_regie
    invoice.save()
    credit.pool = None
    credit.regie = other_regie
    credit.save()
    assert (
        utils.get_existing_lines_for_user(
            regie=regie,
            date_min=pool.campaign.date_start,
            date_max=pool.campaign.date_end,
            user_external_id='user:1',
            serialized_events=[cs['event'] for cs in check_status_list],
        )
        == {}
    )

    # other lines
    invoice.regie = regie
    invoice.save()
    credit.regie = regie
    credit.save()
    invoice2 = create_invoice('payer:1')
    credit2 = create_credit('payer:2')
    invoice_line_args['invoice'] = invoice2
    credit_line_args['credit'] = credit2
    new_line_args = invoice_line_args.copy()
    new_line_args['quantity'] = -2
    new_line_args['unit_amount'] = 3
    line = InvoiceLine.objects.create(
        **new_line_args,
    )
    line.created_at += datetime.timedelta(days=3)
    line.save()
    new_line_args = credit_line_args.copy()
    new_line_args['quantity'] = -2
    new_line_args['unit_amount'] = 4
    CreditLine.objects.create(
        **new_line_args,
    )
    new_line_args = invoice_line_args.copy()
    new_line_args['event_slug'] = 'agenda-1@primary-event-1'
    new_line_args['unit_amount'] = -5
    new_line_args['details'] = {'dates': ['2022-09-15']}
    InvoiceLine.objects.create(
        **new_line_args,
    )
    assert utils.get_existing_lines_for_user(
        regie=regie,
        date_min=pool.campaign.date_start,
        date_max=pool.campaign.date_end,
        user_external_id='user:1',
        serialized_events=[cs['event'] for cs in check_status_list],
    ) == {
        'agenda-1@primary-event-1': {
            '2022-09-01': [
                utils.Link(
                    payer_external_id='payer:1',
                    unit_amount=1,
                    booked=True,
                    invoicing_element_number='F%02d-%s-0000001'
                    % (regie.pk, invoice.created_at.strftime('%y-%m')),
                ),
                utils.Link(
                    payer_external_id='payer:2',
                    unit_amount=2,
                    booked=False,
                    invoicing_element_number='A%02d-%s-0000001'
                    % (regie.pk, credit.created_at.strftime('%y-%m')),
                ),
                utils.Link(
                    payer_external_id='payer:1',
                    unit_amount=3,
                    booked=False,
                    invoicing_element_number='F%02d-%s-0000002'
                    % (regie.pk, invoice2.created_at.strftime('%y-%m')),
                ),
                utils.Link(
                    payer_external_id='payer:2',
                    unit_amount=4,
                    booked=True,
                    invoicing_element_number='A%02d-%s-0000002'
                    % (regie.pk, credit2.created_at.strftime('%y-%m')),
                ),
            ],
            '2022-09-15': [
                utils.Link(
                    payer_external_id='payer:1',
                    unit_amount=5,
                    booked=False,
                    invoicing_element_number='F%02d-%s-0000002'
                    % (regie.pk, invoice2.created_at.strftime('%y-%m')),
                ),
            ],
            '2022-09-30': [
                utils.Link(
                    payer_external_id='payer:1',
                    unit_amount=1,
                    booked=True,
                    invoicing_element_number='F%02d-%s-0000001'
                    % (regie.pk, invoice.created_at.strftime('%y-%m')),
                ),
                utils.Link(
                    payer_external_id='payer:2',
                    unit_amount=2,
                    booked=False,
                    invoicing_element_number='A%02d-%s-0000001'
                    % (regie.pk, credit.created_at.strftime('%y-%m')),
                ),
                utils.Link(
                    payer_external_id='payer:1',
                    unit_amount=3,
                    booked=False,
                    invoicing_element_number='F%02d-%s-0000002'
                    % (regie.pk, invoice2.created_at.strftime('%y-%m')),
                ),
                utils.Link(
                    payer_external_id='payer:2',
                    unit_amount=4,
                    booked=True,
                    invoicing_element_number='A%02d-%s-0000002'
                    % (regie.pk, credit2.created_at.strftime('%y-%m')),
                ),
            ],
        },
    }

    # with adjustment elements
    invoice3 = create_invoice('payer:1')
    credit3 = create_credit('payer:2')
    invoice_line_args['invoice'] = invoice3
    credit_line_args['credit'] = credit3
    new_line_args = invoice_line_args.copy()
    new_line_args['quantity'] = 1
    new_line_args['unit_amount'] = 6
    new_line_args['details'] = {
        'dates': ['2022-09-30'],
        'adjustment': {
            'reason': 'missing-booking',
            '2022-09-30': {
                'before': 'F%02d-%s-0000002' % (regie.pk, invoice2.created_at.strftime('%y-%m')),
                'after': 'A%02d-%s-0000002' % (regie.pk, credit2.created_at.strftime('%y-%m')),
            },
        },
    }
    line = InvoiceLine.objects.create(
        **new_line_args,
    )
    new_line_args = invoice_line_args.copy()
    new_line_args['quantity'] = -1
    new_line_args['unit_amount'] = 7
    new_line_args['details'] = {
        'dates': ['2022-09-30'],
        'adjustment': {
            'reason': 'missing-cancellation',
            '2022-09-30': {
                'before': 'F%02d-%s-0000002' % (regie.pk, invoice2.created_at.strftime('%y-%m')),
                'after': 'A%02d-%s-0000002' % (regie.pk, credit2.created_at.strftime('%y-%m')),
            },
        },
    }
    line = InvoiceLine.objects.create(
        **new_line_args,
    )
    new_line_args = invoice_line_args.copy()
    new_line_args['quantity'] = -1
    new_line_args['unit_amount'] = 8
    new_line_args['details'] = {
        'dates': ['2022-09-30'],
        'adjustment': {
            'reason': 'missing-cancellation',
            '2022-09-30': {
                'after': 'F%02d-%s-0000001' % (regie.pk, invoice.created_at.strftime('%y-%m')),
            },
        },
    }
    line = InvoiceLine.objects.create(
        **new_line_args,
    )
    new_line_args = credit_line_args.copy()
    new_line_args['quantity'] = -1
    new_line_args['unit_amount'] = 9
    new_line_args['details'] = {
        'dates': ['2022-09-30'],
        'adjustment': {
            'reason': 'missing-booking',
            '2022-09-30': {
                'before': 'A%02d-%s-0000002' % (regie.pk, credit2.created_at.strftime('%y-%m')),
            },
        },
    }
    CreditLine.objects.create(
        **new_line_args,
    )
    new_line_args = credit_line_args.copy()
    new_line_args['quantity'] = 1
    new_line_args['unit_amount'] = 10
    new_line_args['details'] = {
        'dates': ['2022-09-30'],
        'adjustment': {
            'reason': 'missing-cancellation',
            '2022-09-30': {
                'before': 'A%02d-%s-0000002' % (regie.pk, credit2.created_at.strftime('%y-%m')),
            },
        },
    }
    CreditLine.objects.create(
        **new_line_args,
    )
    new_line_args = credit_line_args.copy()
    new_line_args['quantity'] = 1
    new_line_args['unit_amount'] = 11
    new_line_args['details'] = {
        'dates': ['2022-09-30'],
        'adjustment': {
            'reason': 'missing-cancellation',
            '2022-09-30': {
                'before': 'unknown',
            },
        },
    }
    CreditLine.objects.create(
        **new_line_args,
    )
    new_line_args = invoice_line_args.copy()
    new_line_args['quantity'] = 1
    new_line_args['unit_amount'] = 12
    new_line_args['details'] = {
        'dates': ['2022-09-30'],
        'adjustment': {
            'reason': 'missing-booking',
            '2022-09-30': {
                'after': 'unknown',
            },
        },
    }
    InvoiceLine.objects.create(
        **new_line_args,
    )
    assert utils.get_existing_lines_for_user(
        regie=regie,
        date_min=pool.campaign.date_start,
        date_max=pool.campaign.date_end,
        user_external_id='user:1',
        serialized_events=[cs['event'] for cs in check_status_list],
    ) == {
        'agenda-1@primary-event-1': {
            '2022-09-01': [
                utils.Link(
                    payer_external_id='payer:1',
                    unit_amount=1,
                    booked=True,
                    invoicing_element_number='F%02d-%s-0000001'
                    % (regie.pk, invoice.created_at.strftime('%y-%m')),
                ),
                utils.Link(
                    payer_external_id='payer:2',
                    unit_amount=2,
                    booked=False,
                    invoicing_element_number='A%02d-%s-0000001'
                    % (regie.pk, credit.created_at.strftime('%y-%m')),
                ),
                utils.Link(
                    payer_external_id='payer:1',
                    unit_amount=3,
                    booked=False,
                    invoicing_element_number='F%02d-%s-0000002'
                    % (regie.pk, invoice2.created_at.strftime('%y-%m')),
                ),
                utils.Link(
                    payer_external_id='payer:2',
                    unit_amount=4,
                    booked=True,
                    invoicing_element_number='A%02d-%s-0000002'
                    % (regie.pk, credit2.created_at.strftime('%y-%m')),
                ),
            ],
            '2022-09-15': [
                utils.Link(
                    payer_external_id='payer:1',
                    unit_amount=5,
                    booked=False,
                    invoicing_element_number='F%02d-%s-0000002'
                    % (regie.pk, invoice2.created_at.strftime('%y-%m')),
                ),
            ],
            '2022-09-30': [
                utils.Link(
                    payer_external_id='payer:1',
                    unit_amount=8,
                    booked=False,
                    invoicing_element_number='F%02d-%s-0000003'
                    % (regie.pk, invoice3.created_at.strftime('%y-%m')),
                ),
                utils.Link(
                    payer_external_id='payer:1',
                    unit_amount=1,
                    booked=True,
                    invoicing_element_number='F%02d-%s-0000001'
                    % (regie.pk, invoice.created_at.strftime('%y-%m')),
                ),
                utils.Link(
                    payer_external_id='payer:2',
                    unit_amount=2,
                    booked=False,
                    invoicing_element_number='A%02d-%s-0000001'
                    % (regie.pk, credit.created_at.strftime('%y-%m')),
                ),
                utils.Link(
                    payer_external_id='payer:1',
                    unit_amount=3,
                    booked=False,
                    invoicing_element_number='F%02d-%s-0000002'
                    % (regie.pk, invoice2.created_at.strftime('%y-%m')),
                ),
                utils.Link(
                    payer_external_id='payer:1',
                    unit_amount=6,
                    booked=True,
                    invoicing_element_number='F%02d-%s-0000003'
                    % (regie.pk, invoice3.created_at.strftime('%y-%m')),
                ),
                utils.Link(
                    payer_external_id='payer:1',
                    unit_amount=7,
                    booked=False,
                    invoicing_element_number='F%02d-%s-0000003'
                    % (regie.pk, invoice3.created_at.strftime('%y-%m')),
                ),
                utils.Link(
                    payer_external_id='payer:2',
                    unit_amount=4,
                    booked=True,
                    invoicing_element_number='A%02d-%s-0000002'
                    % (regie.pk, credit2.created_at.strftime('%y-%m')),
                ),
                utils.Link(
                    payer_external_id='payer:2',
                    unit_amount=9,
                    booked=True,
                    invoicing_element_number='A%02d-%s-0000003'
                    % (regie.pk, credit3.created_at.strftime('%y-%m')),
                ),
                utils.Link(
                    payer_external_id='payer:2',
                    unit_amount=10,
                    booked=False,
                    invoicing_element_number='A%02d-%s-0000003'
                    % (regie.pk, credit3.created_at.strftime('%y-%m')),
                ),
                utils.Link(
                    payer_external_id='payer:1',
                    unit_amount=12,
                    booked=True,
                    invoicing_element_number='F%02d-%s-0000003'
                    % (regie.pk, invoice3.created_at.strftime('%y-%m')),
                ),
                utils.Link(
                    payer_external_id='payer:2',
                    unit_amount=11,
                    booked=False,
                    invoicing_element_number='A%02d-%s-0000003'
                    % (regie.pk, credit3.created_at.strftime('%y-%m')),
                ),
            ],
        },
    }


@mock.patch('lingo.invoicing.utils.get_existing_lines_for_user')
@mock.patch('lingo.invoicing.models.Regie.get_payer_external_id')
@mock.patch('lingo.pricing.models.Pricing.get_pricing_data_for_event')
def test_build_lines_for_user_adjustment_campaign(mock_pricing_data_event, mock_payer, mock_existing):
    regie = Regie.objects.create(label='Regie')
    agenda = Agenda.objects.create(label='Agenda')
    pricing = Pricing.objects.create(
        date_start=datetime.date(year=2022, month=9, day=1),
        date_end=datetime.date(year=2022, month=10, day=1),
    )
    pricing.agendas.add(agenda)
    campaign = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_debit=datetime.date(2022, 11, 15),
        adjustment_campaign=True,
    )
    pool = Pool.objects.create(
        campaign=campaign,
        draft=True,
    )

    mock_pricing_data_event.side_effect = [
        {
            'foo1': 'bar1',
            'pricing': 1,
            'accounting_code': '414141',
            'booking_details': {'status': 'presence'},  # presence without check type
            'calculation_details': {'pricing': 11},
        },
        {
            'foo2': 'bar2',
            'pricing': 2,
            'accounting_code': '424242',
            'booking_details': {'check_type': 'foo', 'status': 'presence'},  # presence with check_type
            'calculation_details': {'pricing': 22},
        },
        {'foo3': 'bar3', 'pricing': 3, 'accounting_code': '434343'},
    ]
    mock_payer.return_value = 'payer:1'
    mock_existing.return_value = {}
    check_status_list = [
        {
            'event': {
                'agenda': 'agenda',
                'start_datetime': '2022-09-01T12:00:00+02:00',
                'slug': 'event-1',
                'label': 'Event 1',
            },
            'check_status': {'foo': 'bar1'},
            'booking': {'foo': 'baz1'},
        },
        {
            'event': {
                'agenda': 'agenda',
                'start_datetime': '2022-09-02T12:00:00+02:00',
                'slug': 'event-2',
                'label': 'Event 2',
            },
            'check_status': {'foo': 'bar2'},
            'booking': {'foo': 'baz2'},
        },
        {
            'event': {
                'agenda': 'agenda',
                'start_datetime': '2022-09-02T12:00:00+02:00',
                'slug': 'event-2',
                'label': 'Event 2',
            },
            'check_status': {'foo': 'bar3'},
            'booking': {'foo': 'baz3'},
        },
    ]
    lines = utils.build_lines_for_user(
        agendas=[agenda],
        agendas_pricings=[pricing],
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
        pool=pool,
        check_status_list=check_status_list,
        payer_data_cache={
            'payer:1': {
                'first_name': 'First1',
                'last_name': 'Last1',
                'address': '41 rue des kangourous\n99999 Kangourou Ville',
                'email': '',
                'phone': '',
                'direct_debit': False,
            }
        },
    )
    assert mock_existing.call_args_list == [
        mock.call(
            regie=regie,
            date_min=pool.campaign.date_start,
            date_max=pool.campaign.date_end,
            user_external_id='user:1',
            serialized_events=[cs['event'] for cs in check_status_list],
        )
    ]
    assert mock_pricing_data_event.call_args_list == [
        mock.call(
            request=mock.ANY,
            agenda=agenda,
            event={
                'agenda': 'agenda',
                'start_datetime': '2022-09-01T12:00:00+02:00',
                'slug': 'event-1',
                'label': 'Event 1',
            },
            check_status={'foo': 'bar1'},
            user_external_id='user:1',
            payer_external_id='payer:1',
        ),
        mock.call(
            request=mock.ANY,
            agenda=agenda,
            event={
                'agenda': 'agenda',
                'start_datetime': '2022-09-02T12:00:00+02:00',
                'slug': 'event-2',
                'label': 'Event 2',
            },
            check_status={'foo': 'bar2'},
            user_external_id='user:1',
            payer_external_id='payer:1',
        ),
        mock.call(
            request=mock.ANY,
            agenda=agenda,
            event={
                'agenda': 'agenda',
                'start_datetime': '2022-09-02T12:00:00+02:00',
                'slug': 'event-2',
                'label': 'Event 2',
            },
            check_status={'foo': 'bar3'},
            user_external_id='user:1',
            payer_external_id='payer:1',
        ),
    ]
    assert len(lines) == 5
    assert lines[0].event_date == datetime.date(2022, 9, 1)
    assert lines[0].slug == 'agenda@event-1'
    assert lines[0].label == 'Event 1'
    assert lines[0].description == ''
    assert lines[0].amount == 0
    assert lines[0].quantity == 1
    assert lines[0].quantity_type == 'units'
    assert lines[0].user_external_id == 'user:1'
    assert lines[0].user_first_name == 'User1'
    assert lines[0].user_last_name == 'Name1'
    assert lines[0].payer_external_id == 'payer:1'
    assert lines[0].payer_first_name == 'First1'
    assert lines[0].payer_last_name == 'Last1'
    assert lines[0].payer_address == '41 rue des kangourous\n99999 Kangourou Ville'
    assert lines[0].payer_direct_debit is False
    assert lines[0].event == {
        'agenda': 'agenda',
        'start_datetime': '2022-09-01T12:00:00+02:00',
        'slug': 'event-1',
        'label': 'Event 1',
    }
    assert lines[0].booking == {'foo': 'baz1'}
    assert lines[0].pricing_data == {
        'foo1': 'bar1',
        'pricing': 1,
        'accounting_code': '414141',
        'booking_details': {'status': 'presence'},
        'calculation_details': {'pricing': 11},
    }
    assert lines[0].accounting_code == '414141'
    assert lines[0].status == 'success'
    assert lines[0].pool == pool
    assert lines[0].from_injected_line is None
    assert lines[1].event_date == datetime.date(2022, 9, 1)
    assert lines[1].slug == 'agenda@event-1'
    assert lines[1].label == 'Event 1'
    assert lines[1].description == ''
    assert lines[1].amount == 11
    assert lines[1].quantity == 1
    assert lines[1].quantity_type == 'units'
    assert lines[1].user_external_id == 'user:1'
    assert lines[1].user_first_name == 'User1'
    assert lines[1].user_last_name == 'Name1'
    assert lines[1].payer_external_id == 'payer:1'
    assert lines[1].payer_first_name == 'First1'
    assert lines[1].payer_last_name == 'Last1'
    assert lines[1].payer_address == '41 rue des kangourous\n99999 Kangourou Ville'
    assert lines[1].payer_direct_debit is False
    assert lines[1].event == {
        'agenda': 'agenda',
        'start_datetime': '2022-09-01T12:00:00+02:00',
        'slug': 'event-1',
        'label': 'Event 1',
    }
    assert lines[1].booking == {'foo': 'baz1'}
    assert lines[1].pricing_data == {'adjustment': {'reason': 'missing-booking'}}
    assert lines[1].accounting_code == '414141'
    assert lines[1].status == 'success'
    assert lines[1].pool == pool
    assert lines[1].from_injected_line is None
    assert lines[2].event_date == datetime.date(2022, 9, 2)
    assert lines[2].slug == 'agenda@event-2'
    assert lines[2].label == 'Event 2'
    assert lines[2].description == ''
    assert lines[2].amount == 2
    assert lines[2].quantity == 1
    assert lines[2].quantity_type == 'units'
    assert lines[2].user_external_id == 'user:1'
    assert lines[2].user_first_name == 'User1'
    assert lines[2].user_last_name == 'Name1'
    assert lines[2].payer_external_id == 'payer:1'
    assert lines[2].payer_first_name == 'First1'
    assert lines[2].payer_last_name == 'Last1'
    assert lines[2].payer_address == '41 rue des kangourous\n99999 Kangourou Ville'
    assert lines[2].payer_direct_debit is False
    assert lines[2].event == {
        'agenda': 'agenda',
        'start_datetime': '2022-09-02T12:00:00+02:00',
        'slug': 'event-2',
        'label': 'Event 2',
    }
    assert lines[2].booking == {'foo': 'baz2'}
    assert lines[2].pricing_data == {
        'foo2': 'bar2',
        'pricing': 2,
        'accounting_code': '424242',
        'booking_details': {'check_type': 'foo', 'status': 'presence'},
        'calculation_details': {'pricing': 22},
    }
    assert lines[2].accounting_code == '424242'
    assert lines[2].status == 'success'
    assert lines[2].pool == pool
    assert lines[2].from_injected_line is None
    assert lines[3].event_date == datetime.date(2022, 9, 2)
    assert lines[3].slug == 'agenda@event-2'
    assert lines[3].label == 'Event 2'
    assert lines[3].description == ''
    assert lines[3].amount == 22
    assert lines[3].quantity == 1
    assert lines[3].quantity_type == 'units'
    assert lines[3].user_external_id == 'user:1'
    assert lines[3].user_first_name == 'User1'
    assert lines[3].user_last_name == 'Name1'
    assert lines[3].payer_external_id == 'payer:1'
    assert lines[3].payer_first_name == 'First1'
    assert lines[3].payer_last_name == 'Last1'
    assert lines[3].payer_address == '41 rue des kangourous\n99999 Kangourou Ville'
    assert lines[3].payer_direct_debit is False
    assert lines[3].event == {
        'agenda': 'agenda',
        'start_datetime': '2022-09-02T12:00:00+02:00',
        'slug': 'event-2',
        'label': 'Event 2',
    }
    assert lines[3].booking == {'foo': 'baz2'}
    assert lines[3].pricing_data == {'adjustment': {'reason': 'missing-booking'}}
    assert lines[3].accounting_code == '424242'
    assert lines[3].status == 'success'
    assert lines[3].pool == pool
    assert lines[3].from_injected_line is None
    assert lines[4].event_date == datetime.date(2022, 9, 2)
    assert lines[4].slug == 'agenda@event-2'
    assert lines[4].label == 'Event 2'
    assert lines[4].description == ''
    assert lines[4].amount == 3
    assert lines[4].quantity == 1
    assert lines[4].quantity_type == 'units'
    assert lines[4].user_external_id == 'user:1'
    assert lines[4].user_first_name == 'User1'
    assert lines[4].user_last_name == 'Name1'
    assert lines[4].payer_external_id == 'payer:1'
    assert lines[4].payer_first_name == 'First1'
    assert lines[4].payer_last_name == 'Last1'
    assert lines[4].payer_address == '41 rue des kangourous\n99999 Kangourou Ville'
    assert lines[4].payer_direct_debit is False
    assert lines[4].event == {
        'agenda': 'agenda',
        'start_datetime': '2022-09-02T12:00:00+02:00',
        'slug': 'event-2',
        'label': 'Event 2',
    }
    assert lines[4].booking == {'foo': 'baz3'}
    assert lines[4].pricing_data == {'foo3': 'bar3', 'pricing': 3, 'accounting_code': '434343'}
    assert lines[4].accounting_code == '434343'
    assert lines[4].status == 'success'
    assert lines[4].pool == pool
    assert lines[4].from_injected_line is None


@mock.patch('lingo.invoicing.utils.get_existing_lines_for_user')
@mock.patch('lingo.invoicing.models.Regie.get_payer_external_id')
@mock.patch('lingo.invoicing.models.Regie.get_payer_data')
@mock.patch('lingo.pricing.models.Pricing.get_pricing_data_for_event')
def test_build_lines_for_user_adjustment_campaign_payer_error(
    mock_pricing_data_event, mock_payer_data, mock_payer, mock_existing
):
    regie = Regie.objects.create(label='Regie')
    agenda = Agenda.objects.create(label='Agenda')
    pricing = Pricing.objects.create(
        date_start=datetime.date(year=2022, month=9, day=1),
        date_end=datetime.date(year=2022, month=10, day=1),
    )
    pricing.agendas.add(agenda)
    campaign = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_debit=datetime.date(2022, 11, 15),
        adjustment_campaign=True,
    )
    pool = Pool.objects.create(
        campaign=campaign,
        draft=True,
    )

    payer_data_cache = {
        'payer:1': {
            'first_name': 'First1',
            'last_name': 'Last1',
            'address': '41 rue des kangourous\n99999 Kangourou Ville',
            'email': '',
            'phone': '',
            'direct_debit': False,
        },
    }

    mock_payer.return_value = 'payer:1'
    mock_payer_data.side_effect = PayerDataError(details={'key': 'foobar', 'reason': 'foo'})
    check_status_list = [
        {
            'event': {
                'agenda': 'agenda',
                'start_datetime': '2022-09-01T12:00:00+02:00',
                'slug': 'event-1',
                'label': 'Event 1',
            },
            'check_status': {'foo': 'bar1'},
            'booking': {'foo': 'baz1'},
        },
    ]
    mock_pricing_data_event.return_value = {
        'pricing': 0,
        'accounting_code': '414141',
        'booking_details': {'status': 'cancelled'},
    }
    mock_existing.return_value = {
        'agenda@event-1': {
            '2022-09-01': [
                utils.Link(
                    payer_external_id='payer:2',
                    unit_amount=22,
                    booked=True,
                    invoicing_element_number='F-0002',
                ),
            ]
        }
    }

    lines = utils.build_lines_for_user(
        agendas=[agenda],
        agendas_pricings=[pricing],
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
        pool=pool,
        check_status_list=check_status_list,
        payer_data_cache=payer_data_cache,
    )
    assert len(lines) == 2
    assert lines[0].amount == 0
    assert lines[0].quantity == 1
    assert lines[1].event_date == datetime.date(2022, 9, 1)
    assert lines[1].slug == 'agenda@event-1'
    assert lines[1].label == 'Event 1'
    assert lines[1].amount == 22
    assert lines[1].quantity == -1
    assert lines[1].quantity_type == 'units'
    assert lines[1].user_external_id == 'user:1'
    assert lines[1].user_first_name == 'User1'
    assert lines[1].user_last_name == 'Name1'
    assert lines[1].payer_external_id == 'payer:2'
    assert lines[1].payer_first_name == ''
    assert lines[1].payer_last_name == ''
    assert lines[1].payer_address == ''
    assert lines[1].payer_direct_debit is False
    assert lines[1].event == {
        'agenda': 'agenda',
        'start_datetime': '2022-09-01T12:00:00+02:00',
        'slug': 'event-1',
        'label': 'Event 1',
    }
    assert lines[1].booking == {'foo': 'baz1'}
    assert lines[1].pricing_data == {
        'adjustment': {'reason': 'missing-cancellation', 'before': 'F-0002'},
        'error': 'PayerDataError',
        'error_details': {'key': 'foobar', 'reason': 'foo'},
    }
    assert lines[1].accounting_code == '414141'
    assert lines[1].status == 'error'
    assert lines[1].pool == pool
    assert lines[1].from_injected_line is None


@mock.patch('lingo.invoicing.utils.get_existing_lines_for_user')
@mock.patch('lingo.invoicing.models.Regie.get_payer_external_id')
@mock.patch('lingo.pricing.models.Pricing.get_pricing_data_for_event')
def test_build_lines_for_user_adjustment_campaign_should_be_empty(
    mock_pricing_data_event, mock_payer, mock_existing
):
    # cases where chain should be empty:
    # - not booked
    # - unexpected presence

    regie = Regie.objects.create(label='Regie')
    agenda = Agenda.objects.create(label='Agenda')
    pricing = Pricing.objects.create(
        date_start=datetime.date(year=2022, month=9, day=1),
        date_end=datetime.date(year=2022, month=10, day=1),
    )
    pricing.agendas.add(agenda)
    campaign = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_debit=datetime.date(2022, 11, 15),
        adjustment_campaign=True,
    )
    pool = Pool.objects.create(
        campaign=campaign,
        draft=True,
    )
    group = CheckTypeGroup.objects.create(label='foobar')
    check_type = CheckType.objects.create(label='unexpected', group=group, kind='presence')
    group.unexpected_presence = check_type
    group.save()

    payer_data_cache = {
        'payer:1': {
            'first_name': 'First1',
            'last_name': 'Last1',
            'address': '41 rue des kangourous\n99999 Kangourou Ville',
            'email': '',
            'phone': '',
            'direct_debit': False,
        },
        'payer:2': {
            'first_name': 'First2',
            'last_name': 'Last2',
            'address': '42 rue des kangourous\n99999 Kangourou Ville',
            'email': '',
            'phone': '',
            'direct_debit': True,
        },
    }

    mock_payer.return_value = 'payer:1'
    check_status_list = [
        {
            'event': {
                'agenda': 'agenda',
                'start_datetime': '2022-09-01T12:00:00+02:00',
                'slug': 'event-1',
                'label': 'Event 1',
            },
            'check_status': {'foo': 'bar1'},
            'booking': {'foo': 'baz1'},
        },
    ]
    pricing_data = [
        {
            'pricing': 1,
            'booking_details': {'status': 'not-booked'},
        },
        {
            'pricing': 2,
            'booking_details': {
                'check_type': 'unexpected',
                'check_type_group': 'foobar',
                'status': 'presence',
            },
        },
    ]

    def build_lines():
        return utils.build_lines_for_user(
            agendas=[agenda],
            agendas_pricings=[pricing],
            user_external_id='user:1',
            user_first_name='User1',
            user_last_name='Name1',
            pool=pool,
            check_status_list=check_status_list,
            payer_data_cache=payer_data_cache,
        )

    for data in pricing_data:
        mock_pricing_data_event.return_value = data

        # chain is empty
        existing = [
            {},
            {'agenda@event-1': {}},
            {'agenda@event-1': {'2022-09-01': []}},
        ]
        for value in existing:
            mock_existing.return_value = value
            lines = build_lines()
            assert len(lines) == 1
            assert lines[0].amount == data['pricing']
            assert lines[0].quantity == 1


@mock.patch('lingo.invoicing.utils.get_existing_lines_for_user')
@mock.patch('lingo.invoicing.models.Regie.get_payer_external_id')
@mock.patch('lingo.pricing.models.Pricing.get_pricing_data_for_event')
def test_build_lines_for_user_adjustment_campaign_should_be_cancelled(
    mock_pricing_data_event, mock_payer, mock_existing
):
    # cases where chain should ends with booked:
    # - not-booked (non empty chain)
    # - unexpected presence
    # - cancelled

    regie = Regie.objects.create(label='Regie')
    agenda = Agenda.objects.create(label='Agenda')
    pricing = Pricing.objects.create(
        date_start=datetime.date(year=2022, month=9, day=1),
        date_end=datetime.date(year=2022, month=10, day=1),
    )
    pricing.agendas.add(agenda)
    campaign = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_debit=datetime.date(2022, 11, 15),
        adjustment_campaign=True,
    )
    pool = Pool.objects.create(
        campaign=campaign,
        draft=True,
    )
    group = CheckTypeGroup.objects.create(label='foobar')
    check_type = CheckType.objects.create(label='unexpected', group=group, kind='presence')
    group.unexpected_presence = check_type
    group.save()

    payer_data_cache = {
        'payer:1': {
            'first_name': 'First1',
            'last_name': 'Last1',
            'address': '41 rue des kangourous\n99999 Kangourou Ville',
            'email': '',
            'phone': '',
            'direct_debit': False,
        },
        'payer:2': {
            'first_name': 'First2',
            'last_name': 'Last2',
            'address': '42 rue des kangourous\n99999 Kangourou Ville',
            'email': '',
            'phone': '',
            'direct_debit': True,
        },
    }

    mock_payer.return_value = 'payer:1'
    check_status_list = [
        {
            'event': {
                'agenda': 'agenda',
                'start_datetime': '2022-09-01T12:00:00+02:00',
                'slug': 'event-1',
                'label': 'Event 1',
            },
            'check_status': {'foo': 'bar1'},
            'booking': {'foo': 'baz1'},
        },
    ]
    pricing_data = [
        {
            'pricing': 1,
            'accounting_code': '414141',
            'booking_details': {'status': 'not-booked'},
        },
        {
            'pricing': 2,
            'accounting_code': '414141',
            'booking_details': {
                'check_type': 'unexpected',
                'check_type_group': 'foobar',
                'status': 'presence',
            },
        },
        {
            'pricing': 3,
            'accounting_code': '414141',
            'booking_details': {'status': 'cancelled'},
        },
    ]

    def build_lines():
        return utils.build_lines_for_user(
            agendas=[agenda],
            agendas_pricings=[pricing],
            user_external_id='user:1',
            user_first_name='User1',
            user_last_name='Name1',
            pool=pool,
            check_status_list=check_status_list,
            payer_data_cache=payer_data_cache,
        )

    def check_line(line, payer_external_id, quantity, amount, adjustment_data):
        assert line.event_date == datetime.date(2022, 9, 1)
        assert line.slug == 'agenda@event-1'
        assert line.label == 'Event 1'
        assert line.amount == amount
        assert line.quantity == quantity
        assert line.quantity_type == 'units'
        assert line.user_external_id == 'user:1'
        assert line.user_first_name == 'User1'
        assert line.user_last_name == 'Name1'
        assert line.payer_external_id == payer_external_id
        assert line.payer_first_name == payer_data_cache[payer_external_id]['first_name']
        assert line.payer_last_name == payer_data_cache[payer_external_id]['last_name']
        assert line.payer_address == payer_data_cache[payer_external_id]['address']
        assert line.payer_direct_debit == payer_data_cache[payer_external_id]['direct_debit']
        assert line.event == {
            'agenda': 'agenda',
            'start_datetime': '2022-09-01T12:00:00+02:00',
            'slug': 'event-1',
            'label': 'Event 1',
        }
        assert line.booking == {'foo': 'baz1'}
        assert line.pricing_data == {'adjustment': adjustment_data}
        assert line.accounting_code == '414141'
        assert line.status == 'success'
        assert line.pool == pool
        assert line.from_injected_line is None

    for data in pricing_data:
        mock_pricing_data_event.return_value = data

        # chain is empty
        existing = [
            {},
            {'agenda@event-1': {}},
            {'agenda@event-1': {'2022-09-01': []}},
        ]
        for value in existing:
            mock_existing.return_value = value
            lines = build_lines()
            assert len(lines) == 1
            assert lines[0].amount == data['pricing']
            assert lines[0].quantity == 1

        # chain is complete, and ends with a cancellation
        existing = [
            {
                'agenda@event-1': {
                    '2022-09-01': [
                        utils.Link(
                            payer_external_id='payer:1',
                            unit_amount=11,
                            booked=True,
                            invoicing_element_number='F-0001',
                        ),
                        utils.Link(
                            payer_external_id='payer:1',
                            unit_amount=11,
                            booked=False,
                            invoicing_element_number='A-0001',
                        ),
                    ]
                }
            },
            {
                'agenda@event-1': {
                    '2022-09-01': [
                        utils.Link(
                            payer_external_id='payer:1',
                            unit_amount=11,
                            booked=True,
                            invoicing_element_number='F-0001',
                        ),
                        utils.Link(
                            payer_external_id='payer:1',
                            unit_amount=11,
                            booked=False,
                            invoicing_element_number='A-0001',
                        ),
                        utils.Link(
                            payer_external_id='payer:2',
                            unit_amount=22,
                            booked=True,
                            invoicing_element_number='F-0002',
                        ),
                        utils.Link(
                            payer_external_id='payer:2',
                            unit_amount=22,
                            booked=False,
                            invoicing_element_number='A-0002',
                        ),
                    ]
                }
            },
        ]
        for value in existing:
            mock_existing.return_value = value
            lines = build_lines()
            assert len(lines) == 1
            assert lines[0].amount == data['pricing']
            assert lines[0].quantity == 1

        # chain is not complete, last cancellation is missing
        existing = [
            {
                'agenda@event-1': {
                    '2022-09-01': [
                        utils.Link(
                            payer_external_id='payer:2',
                            unit_amount=22,
                            booked=True,
                            invoicing_element_number='F-0002',
                        ),
                    ]
                }
            },
            {
                'agenda@event-1': {
                    '2022-09-01': [
                        utils.Link(
                            payer_external_id='payer:1',
                            unit_amount=11,
                            booked=True,
                            invoicing_element_number='F-0001',
                        ),
                        utils.Link(
                            payer_external_id='payer:1',
                            unit_amount=11,
                            booked=False,
                            invoicing_element_number='A-0001',
                        ),
                        utils.Link(
                            payer_external_id='payer:2',
                            unit_amount=22,
                            booked=True,
                            invoicing_element_number='F-0002',
                        ),
                    ]
                }
            },
        ]
        for value in existing:
            mock_existing.return_value = value
            lines = build_lines()
            assert len(lines) == 2
            assert lines[0].amount == data['pricing']
            assert lines[0].quantity == 1
            check_line(
                lines[1],
                'payer:2',
                -1,
                22,
                {'reason': 'missing-cancellation', 'before': 'F-0002'},
            )

        # chain is not complete, missing booking
        existing = [
            {
                'agenda@event-1': {
                    '2022-09-01': [
                        utils.Link(
                            payer_external_id='payer:2',
                            unit_amount=22,
                            booked=False,
                            invoicing_element_number='A-0002',
                        ),
                    ]
                }
            },
            {
                'agenda@event-1': {
                    '2022-09-01': [
                        utils.Link(
                            payer_external_id='payer:1',
                            unit_amount=11,
                            booked=True,
                            invoicing_element_number='F-0001',
                        ),
                        utils.Link(
                            payer_external_id='payer:1',
                            unit_amount=11,
                            booked=False,
                            invoicing_element_number='A-0001',
                        ),
                        utils.Link(
                            payer_external_id='payer:2',
                            unit_amount=22,
                            booked=False,
                            invoicing_element_number='A-0002',
                        ),
                    ]
                }
            },
        ]
        for value in existing:
            mock_existing.return_value = value
            lines = build_lines()
            assert len(lines) == 2
            assert lines[0].amount == data['pricing']
            assert lines[0].quantity == 1
            if len(value['agenda@event-1']['2022-09-01']) == 3:
                check_line(
                    lines[1],
                    'payer:2',
                    1,
                    22,
                    {
                        'reason': 'missing-booking',
                        'before': 'A-0001',
                        'after': 'A-0002',
                    },
                )
            else:
                check_line(
                    lines[1],
                    'payer:2',
                    1,
                    22,
                    {
                        'reason': 'missing-booking',
                        'after': 'A-0002',
                    },
                )

        # chain is not complete, missing cancellation
        mock_existing.return_value = {
            'agenda@event-1': {
                '2022-09-01': [
                    utils.Link(
                        payer_external_id='payer:1',
                        unit_amount=11,
                        booked=True,
                        invoicing_element_number='F-0001',
                    ),
                    utils.Link(
                        payer_external_id='payer:2',
                        unit_amount=22,
                        booked=True,
                        invoicing_element_number='F-0002',
                    ),
                    utils.Link(
                        payer_external_id='payer:2',
                        unit_amount=22,
                        booked=False,
                        invoicing_element_number='A-0001',
                    ),
                ]
            }
        }
        lines = build_lines()
        assert len(lines) == 2
        assert lines[0].amount == data['pricing']
        assert lines[0].quantity == 1
        check_line(
            lines[1],
            'payer:1',
            -1,
            11,
            {
                'reason': 'missing-cancellation',
                'before': 'F-0001',
                'after': 'F-0002',
            },
        )

        # chain is not complete, payer inconsistancy
        mock_existing.return_value = {
            'agenda@event-1': {
                '2022-09-01': [
                    utils.Link(
                        payer_external_id='payer:1',
                        unit_amount=11,
                        booked=True,
                        invoicing_element_number='F-0001',
                    ),
                    utils.Link(
                        payer_external_id='payer:2',
                        unit_amount=11,
                        booked=False,
                        invoicing_element_number='A-0001',
                    ),
                ]
            }
        }
        lines = build_lines()
        assert len(lines) == 3
        assert lines[0].amount == data['pricing']
        assert lines[0].quantity == 1
        check_line(
            lines[1],
            'payer:1',
            -1,
            11,
            {'reason': 'missing-cancellation', 'before': 'F-0001', 'after': 'A-0001'},
        )
        check_line(
            lines[2],
            'payer:2',
            1,
            11,
            {'reason': 'missing-booking', 'before': 'F-0001', 'after': 'A-0001'},
        )

        # chain is not complete, amount inconsistancy
        mock_existing.return_value = {
            'agenda@event-1': {
                '2022-09-01': [
                    utils.Link(
                        payer_external_id='payer:1',
                        unit_amount=11,
                        booked=True,
                        invoicing_element_number='F-0001',
                    ),
                    utils.Link(
                        payer_external_id='payer:1',
                        unit_amount=22,
                        booked=False,
                        invoicing_element_number='A-0001',
                    ),
                ]
            }
        }
        lines = build_lines()
        assert len(lines) == 3
        assert lines[0].amount == data['pricing']
        assert lines[0].quantity == 1
        check_line(
            lines[1],
            'payer:1',
            -1,
            11,
            {'reason': 'missing-cancellation', 'before': 'F-0001', 'after': 'A-0001'},
        )
        check_line(
            lines[2],
            'payer:1',
            1,
            22,
            {'reason': 'missing-booking', 'before': 'F-0001', 'after': 'A-0001'},
        )


@mock.patch('lingo.invoicing.utils.get_existing_lines_for_user')
@mock.patch('lingo.invoicing.models.Regie.get_payer_external_id')
@mock.patch('lingo.pricing.models.Pricing.get_pricing_data_for_event')
def test_build_lines_for_user_adjustment_campaign_should_be_booked(
    mock_pricing_data_event, mock_payer, mock_existing
):
    # cases where chain should ends with booked:
    # - simple presence
    # - presence with check type, but not unexpected
    # - absence

    regie = Regie.objects.create(label='Regie')
    agenda = Agenda.objects.create(label='Agenda')
    pricing = Pricing.objects.create(
        date_start=datetime.date(year=2022, month=9, day=1),
        date_end=datetime.date(year=2022, month=10, day=1),
    )
    pricing.agendas.add(agenda)
    campaign = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_debit=datetime.date(2022, 11, 15),
        adjustment_campaign=True,
    )
    pool = Pool.objects.create(
        campaign=campaign,
        draft=True,
    )
    group = CheckTypeGroup.objects.create(label='foobar')
    CheckType.objects.create(label='foo', group=group, kind='presence')
    CheckType.objects.create(label='bar', group=group, kind='absence')

    payer_data_cache = {
        'payer:1': {
            'first_name': 'First1',
            'last_name': 'Last1',
            'address': '41 rue des kangourous\n99999 Kangourou Ville',
            'email': '',
            'phone': '',
            'direct_debit': False,
        },
        'payer:2': {
            'first_name': 'First2',
            'last_name': 'Last2',
            'address': '42 rue des kangourous\n99999 Kangourou Ville',
            'email': '',
            'phone': '',
            'direct_debit': True,
        },
    }

    mock_payer.return_value = 'payer:1'
    check_status_list = [
        {
            'event': {
                'agenda': 'agenda',
                'start_datetime': '2022-09-01T12:00:00+02:00',
                'slug': 'event-1',
                'label': 'Event 1',
            },
            'check_status': {'foo': 'bar1'},
            'booking': {'foo': 'baz1'},
        },
    ]
    pricing_data = [
        {
            'pricing': 0,
            'accounting_code': '414141',
            'booking_details': {'status': 'presence'},
            'calculation_details': {'pricing': 111},
        },
        {
            'pricing': 2,
            'accounting_code': '414141',
            'booking_details': {'check_type': 'foo', 'check_type_group': 'foobar', 'status': 'presence'},
            'calculation_details': {'pricing': decimal.Decimal('222.5')},
        },
        {
            'pricing': 3,
            'accounting_code': '414141',
            'booking_details': {'status': 'absence'},
            'calculation_details': {'pricing': 333},
        },
        {
            'pricing': 4,
            'accounting_code': '414141',
            'booking_details': {'check_type': 'bar', 'check_type_group': 'foobar', 'status': 'absence'},
            'calculation_details': {'pricing': 444},
        },
    ]

    def build_lines():
        return utils.build_lines_for_user(
            agendas=[agenda],
            agendas_pricings=[pricing],
            user_external_id='user:1',
            user_first_name='User1',
            user_last_name='Name1',
            pool=pool,
            check_status_list=check_status_list,
            payer_data_cache=payer_data_cache,
        )

    def check_line(line, payer_external_id, quantity, amount, adjustment_data):
        assert line.event_date == datetime.date(2022, 9, 1)
        assert line.slug == 'agenda@event-1'
        assert line.label == 'Event 1'
        assert line.amount == amount
        assert line.quantity == quantity
        assert line.quantity_type == 'units'
        assert line.user_external_id == 'user:1'
        assert line.user_first_name == 'User1'
        assert line.user_last_name == 'Name1'
        assert line.payer_external_id == payer_external_id
        assert line.payer_first_name == payer_data_cache[payer_external_id]['first_name']
        assert line.payer_last_name == payer_data_cache[payer_external_id]['last_name']
        assert line.payer_address == payer_data_cache[payer_external_id]['address']
        assert line.payer_direct_debit == payer_data_cache[payer_external_id]['direct_debit']
        assert line.event == {
            'agenda': 'agenda',
            'start_datetime': '2022-09-01T12:00:00+02:00',
            'slug': 'event-1',
            'label': 'Event 1',
        }
        assert line.booking == {'foo': 'baz1'}
        assert line.pricing_data == {'adjustment': adjustment_data}
        assert line.accounting_code == '414141'
        assert line.status == 'success'
        assert line.pool == pool
        assert line.from_injected_line is None

    for data in pricing_data:
        mock_pricing_data_event.return_value = data

        # chain is empty
        existing = [
            {},
            {'agenda@event-1': {}},
            {'agenda@event-1': {'2022-09-01': []}},
        ]
        for value in existing:
            mock_existing.return_value = value
            lines = build_lines()
            assert len(lines) == 2
            assert lines[0].amount == data['pricing']
            assert lines[0].quantity == 1
            check_line(
                lines[1],
                'payer:1',
                1,
                data['calculation_details']['pricing'],
                {'reason': 'missing-booking'},
            )

        # chain is complete, and ends with a booking
        existing = [
            {
                'agenda@event-1': {
                    '2022-09-01': [
                        utils.Link(
                            payer_external_id='payer:1',
                            unit_amount=decimal.Decimal(data['calculation_details']['pricing']),
                            booked=True,
                            invoicing_element_number='F-0001',
                        ),
                    ]
                }
            },
            {
                'agenda@event-1': {
                    '2022-09-01': [
                        utils.Link(
                            payer_external_id='payer:2',
                            unit_amount=11,
                            booked=True,
                            invoicing_element_number='F-0001',
                        ),
                        utils.Link(
                            payer_external_id='payer:2',
                            unit_amount=11,
                            booked=False,
                            invoicing_element_number='A-0001',
                        ),
                        utils.Link(
                            payer_external_id='payer:1',
                            unit_amount=decimal.Decimal(data['calculation_details']['pricing']),
                            booked=True,
                            invoicing_element_number='F-0002',
                        ),
                    ]
                }
            },
        ]
        for value in existing:
            mock_existing.return_value = value
            lines = build_lines()
            assert len(lines) == 1
            assert lines[0].amount == data['pricing']
            assert lines[0].quantity == 1

        # chain is not complete, last booking is missing
        mock_existing.return_value = {
            'agenda@event-1': {
                '2022-09-01': [
                    utils.Link(
                        payer_external_id='payer:1',
                        unit_amount=11,
                        booked=True,
                        invoicing_element_number='F-0001',
                    ),
                    utils.Link(
                        payer_external_id='payer:1',
                        unit_amount=11,
                        booked=False,
                        invoicing_element_number='A-0001',
                    ),
                ]
            }
        }
        lines = build_lines()
        assert len(lines) == 2
        assert lines[0].amount == data['pricing']
        assert lines[0].quantity == 1
        check_line(
            lines[1],
            'payer:1',
            1,
            data['calculation_details']['pricing'],
            {'reason': 'missing-booking', 'before': 'A-0001'},
        )

        # chain is not complete, missing booking
        existing = [
            {
                'agenda@event-1': {
                    '2022-09-01': [
                        utils.Link(
                            payer_external_id='payer:2',
                            unit_amount=22,
                            booked=False,
                            invoicing_element_number='A-0002',
                        ),
                        utils.Link(
                            payer_external_id='payer:1',
                            unit_amount=decimal.Decimal(data['calculation_details']['pricing']),
                            booked=True,
                            invoicing_element_number='F-0003',
                        ),
                    ]
                }
            },
            {
                'agenda@event-1': {
                    '2022-09-01': [
                        utils.Link(
                            payer_external_id='payer:1',
                            unit_amount=11,
                            booked=True,
                            invoicing_element_number='F-0001',
                        ),
                        utils.Link(
                            payer_external_id='payer:1',
                            unit_amount=11,
                            booked=False,
                            invoicing_element_number='A-0001',
                        ),
                        utils.Link(
                            payer_external_id='payer:2',
                            unit_amount=22,
                            booked=False,
                            invoicing_element_number='A-0002',
                        ),
                        utils.Link(
                            payer_external_id='payer:1',
                            unit_amount=decimal.Decimal(data['calculation_details']['pricing']),
                            booked=True,
                            invoicing_element_number='F-0003',
                        ),
                    ]
                }
            },
        ]
        for value in existing:
            mock_existing.return_value = value
            lines = build_lines()
            assert len(lines) == 2
            assert lines[0].amount == data['pricing']
            assert lines[0].quantity == 1
            if len(value['agenda@event-1']['2022-09-01']) == 4:
                check_line(
                    lines[1],
                    'payer:2',
                    1,
                    22,
                    {
                        'reason': 'missing-booking',
                        'before': 'A-0001',
                        'after': 'A-0002',
                    },
                )
            else:
                check_line(
                    lines[1],
                    'payer:2',
                    1,
                    22,
                    {
                        'reason': 'missing-booking',
                        'after': 'A-0002',
                    },
                )

        # chain is not complete, missing cancellation
        mock_existing.return_value = {
            'agenda@event-1': {
                '2022-09-01': [
                    utils.Link(
                        payer_external_id='payer:2',
                        unit_amount=22,
                        booked=True,
                        invoicing_element_number='F-0001',
                    ),
                    utils.Link(
                        payer_external_id='payer:1',
                        unit_amount=decimal.Decimal(data['calculation_details']['pricing']),
                        booked=True,
                        invoicing_element_number='F-0002',
                    ),
                ]
            }
        }
        lines = build_lines()
        assert len(lines) == 2
        assert lines[0].amount == data['pricing']
        assert lines[0].quantity == 1
        check_line(
            lines[1],
            'payer:2',
            -1,
            22,
            {
                'reason': 'missing-cancellation',
                'before': 'F-0001',
                'after': 'F-0002',
            },
        )

        # chain is not complete, payer inconsistancy
        mock_existing.return_value = {
            'agenda@event-1': {
                '2022-09-01': [
                    utils.Link(
                        payer_external_id='payer:1',
                        unit_amount=11,
                        booked=True,
                        invoicing_element_number='F-0001',
                    ),
                    utils.Link(
                        payer_external_id='payer:2',
                        unit_amount=11,
                        booked=False,
                        invoicing_element_number='A-0001',
                    ),
                    utils.Link(
                        payer_external_id='payer:1',
                        unit_amount=decimal.Decimal(data['calculation_details']['pricing']),
                        booked=True,
                        invoicing_element_number='F-0002',
                    ),
                ]
            }
        }
        lines = build_lines()
        assert len(lines) == 3
        assert lines[0].amount == data['pricing']
        assert lines[0].quantity == 1
        check_line(
            lines[1],
            'payer:1',
            -1,
            11,
            {'reason': 'missing-cancellation', 'before': 'F-0001', 'after': 'A-0001'},
        )
        check_line(
            lines[2],
            'payer:2',
            1,
            11,
            {'reason': 'missing-booking', 'before': 'F-0001', 'after': 'A-0001'},
        )

        # chain is not complete, amount inconsistancy
        mock_existing.return_value = {
            'agenda@event-1': {
                '2022-09-01': [
                    utils.Link(
                        payer_external_id='payer:1',
                        unit_amount=11,
                        booked=True,
                        invoicing_element_number='F-0001',
                    ),
                    utils.Link(
                        payer_external_id='payer:1',
                        unit_amount=22,
                        booked=False,
                        invoicing_element_number='A-0001',
                    ),
                    utils.Link(
                        payer_external_id='payer:1',
                        unit_amount=decimal.Decimal(data['calculation_details']['pricing']),
                        booked=True,
                        invoicing_element_number='F-0002',
                    ),
                ]
            }
        }
        lines = build_lines()
        assert len(lines) == 3
        assert lines[0].amount == data['pricing']
        assert lines[0].quantity == 1
        check_line(
            lines[1],
            'payer:1',
            -1,
            11,
            {'reason': 'missing-cancellation', 'before': 'F-0001', 'after': 'A-0001'},
        )
        check_line(
            lines[2],
            'payer:1',
            1,
            22,
            {'reason': 'missing-booking', 'before': 'F-0001', 'after': 'A-0001'},
        )

        # chain is complete, but last link has a wrong pricing (payer is correct)
        existing = [
            {
                'agenda@event-1': {
                    '2022-09-01': [
                        utils.Link(
                            payer_external_id='payer:1',
                            unit_amount=11,
                            booked=True,
                            invoicing_element_number='F-0002',
                        ),
                    ]
                }
            },
            {
                'agenda@event-1': {
                    '2022-09-01': [
                        utils.Link(
                            payer_external_id='payer:2',
                            unit_amount=22,
                            booked=True,
                            invoicing_element_number='F-0001',
                        ),
                        utils.Link(
                            payer_external_id='payer:2',
                            unit_amount=22,
                            booked=False,
                            invoicing_element_number='A-0001',
                        ),
                        utils.Link(
                            payer_external_id='payer:1',
                            unit_amount=11,
                            booked=True,
                            invoicing_element_number='F-0002',
                        ),
                    ]
                }
            },
        ]
        for value in existing:
            mock_existing.return_value = value
            lines = build_lines()
            assert len(lines) == 3
            assert lines[0].amount == data['pricing']
            assert lines[0].quantity == 1
            check_line(
                lines[1],
                'payer:1',
                -1,
                11,
                {
                    'reason': 'missing-cancellation',
                    'before': 'F-0002',
                    'info': 'pricing-changed',
                },
            )
            check_line(
                lines[2],
                'payer:1',
                1,
                decimal.Decimal(data['calculation_details']['pricing']),
                {'reason': 'missing-booking', 'before': 'F-0002', 'info': 'pricing-changed'},
            )
            assert lines[1].created_at < lines[2].created_at

        # chain is complete, but last link has a wrong payer (pricing is correct)
        existing = [
            {
                'agenda@event-1': {
                    '2022-09-01': [
                        utils.Link(
                            payer_external_id='payer:2',
                            unit_amount=decimal.Decimal(data['calculation_details']['pricing']),
                            booked=True,
                            invoicing_element_number='F-0002',
                        ),
                    ]
                }
            },
            {
                'agenda@event-1': {
                    '2022-09-01': [
                        utils.Link(
                            payer_external_id='payer:1',
                            unit_amount=22,
                            booked=True,
                            invoicing_element_number='F-0001',
                        ),
                        utils.Link(
                            payer_external_id='payer:1',
                            unit_amount=22,
                            booked=False,
                            invoicing_element_number='A-0001',
                        ),
                        utils.Link(
                            payer_external_id='payer:2',
                            unit_amount=decimal.Decimal(data['calculation_details']['pricing']),
                            booked=True,
                            invoicing_element_number='F-0002',
                        ),
                    ]
                }
            },
        ]
        for value in existing:
            mock_existing.return_value = value
            lines = build_lines()
            assert len(lines) == 3
            assert lines[0].amount == data['pricing']
            assert lines[0].quantity == 1
            check_line(
                lines[1],
                'payer:2',
                -1,
                decimal.Decimal(data['calculation_details']['pricing']),
                {
                    'reason': 'missing-cancellation',
                    'before': 'F-0002',
                    'info': 'pricing-changed',
                },
            )
            check_line(
                lines[2],
                'payer:1',
                1,
                decimal.Decimal(data['calculation_details']['pricing']),
                {'reason': 'missing-booking', 'before': 'F-0002', 'info': 'pricing-changed'},
            )
            assert lines[1].created_at < lines[2].created_at

        # chain is complete, no changes
        existing = [
            {
                'agenda@event-1': {
                    '2022-09-01': [
                        utils.Link(
                            payer_external_id='payer:1',
                            unit_amount=decimal.Decimal(data['calculation_details']['pricing']),
                            booked=True,
                            invoicing_element_number='F-0002',
                        ),
                    ]
                }
            },
            {
                'agenda@event-1': {
                    '2022-09-01': [
                        utils.Link(
                            payer_external_id='payer:1',
                            unit_amount=22,
                            booked=True,
                            invoicing_element_number='F-0001',
                        ),
                        utils.Link(
                            payer_external_id='payer:1',
                            unit_amount=22,
                            booked=False,
                            invoicing_element_number='A-0001',
                        ),
                        utils.Link(
                            payer_external_id='payer:1',
                            unit_amount=decimal.Decimal(data['calculation_details']['pricing']),
                            booked=True,
                            invoicing_element_number='F-0002',
                        ),
                    ]
                }
            },
        ]
        for value in existing:
            mock_existing.return_value = value
            lines = build_lines()
            assert len(lines) == 1
            assert lines[0].amount == data['pricing']
            assert lines[0].quantity == 1


def test_generate_invoices_from_lines_aggregation():
    Agenda.objects.create(label='Agenda 1')
    Agenda.objects.create(label='Agenda 2')
    regie = Regie.objects.create(label='Regie')

    campaign = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_debit=datetime.date(2022, 11, 15),
        adjustment_campaign=True,
    )
    pool = Pool.objects.create(
        campaign=campaign,
        draft=True,
        status='running',
    )
    group = CheckTypeGroup.objects.create(label='foobar')
    CheckType.objects.create(label='Foo!', group=group, kind='presence')
    # 3 lines for event event-1, check_type foo
    for i in range(3):
        DraftJournalLine.objects.create(
            label='Event 1',
            description='A description!',
            event_date=datetime.date(2022, 9, 1 + i),
            event={
                'agenda': 'agenda-1',
                'label': 'A recurring event',
                'primary_event': 'event-1',
                'start_datetime': '2022-09-01T12:00:00+02:00',
            },
            pricing_data={
                'booking_details': {'check_type': 'foo', 'check_type_group': 'foobar', 'status': 'presence'}
            },
            amount=1,
            quantity=1,
            quantity_type='units',
            accounting_code='424242',
            user_external_id='user:1',
            user_first_name='UserFirst1',
            user_last_name='UserLast1',
            payer_external_id='payer:1',
            payer_first_name='First1',
            payer_last_name='Last1',
            payer_address='41 rue des kangourous\n99999 Kangourou Ville',
            payer_direct_debit=False,
            status='success',
            pool=pool,
        )
    DraftJournalLine.objects.create(
        label='Foobar',
        slug='agenda-1@foobar',
        event_date=datetime.date(2022, 9, 1),
        event={
            'agenda': 'agenda-1',
            'start_datetime': '2022-09-01T12:00:00+02:00',
        },  # no primary_event: non recurring event
        pricing_data={'booking_details': {'status': 'presence'}},  # presence without check_type, ignored
        amount=0,
        accounting_code='424242',
        user_external_id='user:1',
        user_first_name='UserFirst1',
        user_last_name='UserLast1',
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Last1',
        payer_address='41 rue des kangourous\n99999 Kangourou Ville',
        payer_direct_debit=False,
        status='success',
        pool=pool,
    )
    DraftJournalLine.objects.create(
        label='Event 1',
        event_date=datetime.date(2022, 9, 1),
        event={
            'agenda': 'agenda-1',
            'primary_event': 'event-1',
            'start_datetime': '2022-09-01T12:00:00+02:00',
        },
        pricing_data={'booking_details': {'status': 'not-booked'}},  # not booked, ignored
        amount=1,
        accounting_code='424242',
        user_external_id='user:1',
        user_first_name='UserFirst1',
        user_last_name='UserLast1',
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Last1',
        payer_address='41 rue des kangourous\n99999 Kangourou Ville',
        payer_direct_debit=False,
        status='success',
        pool=pool,
    )
    DraftJournalLine.objects.create(
        label='Event 1',
        event_date=datetime.date(2022, 9, 1),
        event={
            'agenda': 'agenda-1',
            'primary_event': 'event-1',
            'start_datetime': '2022-09-01T12:00:00+02:00',
        },
        pricing_data={'booking_details': {'status': 'cancelled'}},  # cancelled, ignored
        amount=1,
        accounting_code='424242',
        user_external_id='user:2',
        user_first_name='UserFirst2',
        user_last_name='UserLast2',
        payer_external_id='payer:2',
        payer_first_name='First2',
        payer_last_name='Last2',
        payer_address='42 rue des kangourous\n99999 Kangourou Ville',
        payer_direct_debit=False,
        status='success',
        pool=pool,
    )
    for event_date in range(2):
        for primary_event in [True, False]:
            event = {
                'agenda': 'agenda-1',
                'start_datetime': '2022-09-01T12:00:00+02:00',
            }
            event_slug = 'agenda-1@foobar'
            if primary_event:
                event['primary_event'] = 'primary'
                event_slug = 'primary@foobar'
            DraftJournalLine.objects.create(
                label='Foobar',
                slug=event_slug,
                event_date=datetime.date(2022, 9, event_date + 1),
                event=event,
                pricing_data={'adjustment': {'reason': 'missing-booking'}},
                quantity=1,
                amount=1,
                accounting_code='424242',
                user_external_id='user:1',
                user_first_name='UserFirst1',
                user_last_name='UserLast1',
                payer_external_id='payer:1',
                payer_first_name='First1',
                payer_last_name='Last1',
                payer_address='41 rue des kangourous\n99999 Kangourou Ville',
                payer_direct_debit=False,
                status='success',
                pool=pool,
            )
    for event_date in range(2):
        for primary_event in [True, False]:
            event = {
                'agenda': 'agenda-1',
                'start_datetime': '2022-09-01T12:00:00+02:00',
            }
            event_slug = 'agenda-1@foobar'
            if primary_event:
                event['primary_event'] = 'primary'
                event_slug = 'primary@foobar'
            DraftJournalLine.objects.create(
                label='Foobar',
                slug=event_slug,
                event_date=datetime.date(2022, 9, event_date + 3),
                event=event,
                pricing_data={'adjustment': {'reason': 'missing-cancellation'}},
                quantity=-1,
                amount=1,
                accounting_code='424242',
                user_external_id='user:1',
                user_first_name='UserFirst1',
                user_last_name='UserLast1',
                payer_external_id='payer:1',
                payer_first_name='First1',
                payer_last_name='Last1',
                payer_address='41 rue des kangourous\n99999 Kangourou Ville',
                payer_direct_debit=False,
                status='success',
                pool=pool,
            )
    event = {
        'agenda': 'agenda-1',
        'start_datetime': '2022-09-01T12:00:00+02:00',
        'primary_event': 'primary',
    }
    # too lines missing booking/cancellation that cancel each other
    DraftJournalLine.objects.create(
        label='Foobar',
        slug='primary@foobar',
        event_date=datetime.date(2022, 9, 2),
        event=event,
        pricing_data={'adjustment': {'reason': 'missing-cancellation'}},
        quantity=-1,
        amount=1,
        accounting_code='424242',
        user_external_id='user:1',
        user_first_name='UserFirst1',
        user_last_name='UserLast1',
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Last1',
        payer_address='41 rue des kangourous\n99999 Kangourou Ville',
        payer_direct_debit=False,
        status='success',
        pool=pool,
    )
    DraftJournalLine.objects.create(
        label='Foobar',
        slug='primary@foobar',
        event_date=datetime.date(2022, 9, 2),
        event=event,
        pricing_data={'adjustment': {'reason': 'missing-booking'}},
        quantity=1,
        amount=1,
        accounting_code='424242',
        user_external_id='user:1',
        user_first_name='UserFirst1',
        user_last_name='UserLast1',
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Last1',
        payer_address='41 rue des kangourous\n99999 Kangourou Ville',
        payer_direct_debit=False,
        status='success',
        pool=pool,
    )
    # too lines missing booking/cancellation that cancel each other for another payer
    # => no invoice generated
    DraftJournalLine.objects.create(
        label='Foobar',
        slug='primary@foobar',
        event_date=datetime.date(2022, 9, 2),
        event=event,
        pricing_data={'adjustment': {'reason': 'missing-cancellation'}},
        quantity=-1,
        amount=1,
        accounting_code='424242',
        user_external_id='user:2',
        user_first_name='UserFirst2',
        user_last_name='UserLast2',
        payer_external_id='payer:2',
        payer_first_name='First2',
        payer_last_name='Last2',
        payer_address='42 rue des kangourous\n99999 Kangourou Ville',
        payer_direct_debit=False,
        status='success',
        pool=pool,
    )
    DraftJournalLine.objects.create(
        label='Foobar',
        slug='primary@foobar',
        event_date=datetime.date(2022, 9, 2),
        event=event,
        pricing_data={'adjustment': {'reason': 'missing-booking'}},
        quantity=1,
        amount=1,
        accounting_code='424242',
        user_external_id='user:2',
        user_first_name='UserFirst2',
        user_last_name='UserLast2',
        payer_external_id='payer:2',
        payer_first_name='First2',
        payer_last_name='Last2',
        payer_address='42 rue des kangourous\n99999 Kangourou Ville',
        payer_direct_debit=False,
        status='success',
        pool=pool,
    )
    lines = DraftJournalLine.objects.all().order_by('pk')

    invoices = utils.generate_invoices_from_lines(pool=pool)
    assert len(invoices) == 1
    invoice = invoices[0]
    # refresh total_amount field (triggered)
    invoice.refresh_from_db()
    lines = DraftJournalLine.objects.all().order_by('pk')
    assert DraftInvoiceLine.objects.count() == 7
    (
        iline1,
        iline2,
        iline3,
        iline4,
        iline5,
        iline6,
        iline7,
    ) = DraftInvoiceLine.objects.all().order_by('pk')
    assert isinstance(invoice, DraftInvoice)
    assert invoice.total_amount == 3
    # 3 journal lines grouped in an invoice line
    assert iline1.event_date == campaign.date_start
    assert iline1.label == 'Event 1'
    assert iline1.quantity == 3
    assert iline1.unit_amount == 1
    assert iline1.details == {
        'agenda': 'agenda-1',
        'primary_event': 'event-1',
        'status': 'presence',
        'check_type': 'foo',
        'check_type_group': 'foobar',
        'check_type_label': 'Foo!',
        'dates': ['2022-09-01', '2022-09-02', '2022-09-03'],
        'event_time': '12:00:00',
        'partial_bookings': False,
    }
    assert iline1.event_slug == 'agenda-1@event-1'
    assert iline1.event_label == 'A recurring event'
    assert iline1.agenda_slug == 'agenda-1'
    assert iline1.activity_label == 'Agenda 1'
    assert iline1.description == 'A description!'
    assert iline1.accounting_code == '424242'
    assert iline1.user_external_id == 'user:1'
    assert iline1.user_first_name == 'UserFirst1'
    assert iline1.user_last_name == 'UserLast1'
    assert iline1.pool == pool
    assert iline1 == lines[0].invoice_line
    assert iline1 == lines[1].invoice_line
    assert iline1 == lines[2].invoice_line
    # 2 journal lines grouped in an invoice line
    assert iline2.event_date == campaign.date_start
    assert iline2.label == 'Foobar'
    assert iline2.quantity == 2
    assert iline2.unit_amount == 1
    assert iline2.details == {
        'agenda': 'agenda-1',
        'primary_event': 'primary',
        'status': None,
        'check_type': None,
        'check_type_group': None,
        'check_type_label': 'Booking (regularization)',
        'dates': ['2022-09-01', '2022-09-02'],
        'event_time': '12:00:00',
        'partial_bookings': False,
    }
    assert iline2.event_slug == 'agenda-1@primary'
    assert iline2.event_label == 'Foobar'
    assert iline2.agenda_slug == 'agenda-1'
    assert iline2.activity_label == 'Agenda 1'
    assert iline2.description == '01/09, 02/09'
    assert iline2.accounting_code == '424242'
    assert iline2.user_external_id == 'user:1'
    assert iline2.user_first_name == 'UserFirst1'
    assert iline2.user_last_name == 'UserLast1'
    assert iline2.pool == pool
    assert iline2 == lines[6].invoice_line
    assert iline2 == lines[15].invoice_line
    # 2 journal lines grouped in an invoice line
    assert iline3.event_date == campaign.date_start
    assert iline3.label == 'Foobar'
    assert iline3.quantity == -2
    assert iline3.unit_amount == 1
    assert iline3.details == {
        'agenda': 'agenda-1',
        'primary_event': 'primary',
        'status': None,
        'check_type': None,
        'check_type_group': None,
        'check_type_label': 'Cancellation (regularization)',
        'dates': ['2022-09-03', '2022-09-04'],
        'event_time': '12:00:00',
        'partial_bookings': False,
    }
    assert iline3.event_slug == 'agenda-1@primary'
    assert iline3.event_label == 'Foobar'
    assert iline3.agenda_slug == 'agenda-1'
    assert iline3.activity_label == 'Agenda 1'
    assert iline3.description == '03/09, 04/09'
    assert iline3.accounting_code == '424242'
    assert iline3.user_external_id == 'user:1'
    assert iline3.user_first_name == 'UserFirst1'
    assert iline3.user_last_name == 'UserLast1'
    assert iline3.pool == pool
    assert iline3 == lines[10].invoice_line
    assert iline3 == lines[12].invoice_line
    # one journal line, one invoice line
    ilines = [
        (lines[7], iline4),
        (lines[9], iline5),
        (lines[11], iline6),
        (lines[13], iline7),
    ]
    for line, iline in ilines:
        assert iline.event_date == line.event_date
        assert iline.label == line.label
        assert iline.event_label == line.label
        assert iline.unit_amount == line.amount
        assert iline.user_external_id == line.user_external_id
        assert iline.user_first_name == line.user_first_name
        assert iline.user_last_name == line.user_last_name
        assert iline.pool == pool
        assert iline == line.invoice_line
    assert iline4.details == {}
    assert iline4.event_slug == 'agenda-1@foobar'
    assert iline4.agenda_slug == 'agenda-1'
    assert iline4.activity_label == 'Agenda 1'
    assert iline4.description == 'Booking (regularization)'
    assert iline4.quantity == 1
    assert iline4.accounting_code == ''
    assert iline5.details == {}
    assert iline5.event_slug == 'agenda-1@foobar'
    assert iline5.agenda_slug == 'agenda-1'
    assert iline5.activity_label == 'Agenda 1'
    assert iline5.description == 'Booking (regularization)'
    assert iline5.quantity == 1
    assert iline5.accounting_code == ''
    assert iline6.details == {}
    assert iline6.event_slug == 'agenda-1@foobar'
    assert iline6.agenda_slug == 'agenda-1'
    assert iline6.activity_label == 'Agenda 1'
    assert iline6.description == 'Cancellation (regularization)'
    assert iline6.quantity == -1
    assert iline6.accounting_code == ''
    assert iline7.details == {}
    assert iline7.event_slug == 'agenda-1@foobar'
    assert iline7.agenda_slug == 'agenda-1'
    assert iline7.activity_label == 'Agenda 1'
    assert iline7.description == 'Cancellation (regularization)'
    assert iline7.quantity == -1
    assert iline7.accounting_code == ''
