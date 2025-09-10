import datetime
import decimal
from unittest import mock

import pytest

from lingo.agendas.models import Agenda, CheckType, CheckTypeGroup
from lingo.invoicing import utils
from lingo.invoicing.errors import PayerDataError
from lingo.invoicing.models import Campaign, DraftJournalLine, JournalLine, Pool, Regie
from lingo.pricing.models import Pricing

pytestmark = pytest.mark.django_db


def test_get_previous_campaign_journal_lines_for_user():
    regie = Regie.objects.create(label='Regie')
    primary_campaign = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_debit=datetime.date(2022, 11, 15),
    )
    corrective_campaign1 = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_debit=datetime.date(2022, 11, 15),
        primary_campaign=primary_campaign,
    )
    primary_pool = Pool.objects.create(
        campaign=primary_campaign,
        draft=False,
    )
    corrective_pool1 = Pool.objects.create(
        campaign=corrective_campaign1,
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

    # no lines
    assert (
        utils.get_previous_campaign_journal_lines_for_user(corrective_pool1, 'user:1', check_status_list)
        == {}
    )

    # create lines for primary_campaign
    line_args = {
        'event_date': datetime.date(2022, 9, 1),
        'quantity': 1,
        'amount': 1,
        'slug': 'agenda-1@event-1',
        'user_external_id': 'user:1',
        'pricing_data': {'booking_details': {}},
        'pool': primary_pool,
    }
    wrong_values = [
        ('slug', 'foo'),
        ('user_external_id', 'user:2'),
        ('pricing_data', {}),
    ]
    for key, val in wrong_values:
        new_line_args = line_args.copy()
        new_line_args[key] = val
        JournalLine.objects.create(
            **new_line_args,
        )
    assert (
        utils.get_previous_campaign_journal_lines_for_user(corrective_pool1, 'user:1', check_status_list)
        == {}
    )

    # create lines with matching values
    new_line_args = line_args.copy()
    primary_pool_line11 = JournalLine.objects.create(
        **new_line_args,
    )
    new_line_args = line_args.copy()
    new_line_args['event_date'] = datetime.date(2022, 9, 2)
    primary_pool_line12 = JournalLine.objects.create(
        **new_line_args,
    )
    assert utils.get_previous_campaign_journal_lines_for_user(
        corrective_pool1, 'user:1', check_status_list
    ) == {
        'agenda-1@event-1': {
            '2022-09-01': primary_pool_line11,
            '2022-09-02': primary_pool_line12,
        },
    }

    # second corrective pool
    corrective_pool1.draft = False
    corrective_pool1.save()
    corrective_pool2 = Pool.objects.create(
        campaign=corrective_campaign1,
        draft=True,
    )
    # but corrective_pool1 has no lines
    assert utils.get_previous_campaign_journal_lines_for_user(
        corrective_pool2, 'user:1', check_status_list
    ) == {
        'agenda-1@event-1': {
            '2022-09-01': primary_pool_line11,
            '2022-09-02': primary_pool_line12,
        },
    }

    # create lines for corrective_pool1
    line_args = {
        'event_date': datetime.date(2022, 9, 1),
        'quantity': 1,
        'amount': 1,
        'slug': 'agenda-1@event-1',
        'user_external_id': 'user:1',
        'pricing_data': {'booking_details': {}},
        'pool': corrective_pool1,
    }
    wrong_values = [
        ('slug', 'foo'),
        ('user_external_id', 'user:2'),
        ('pricing_data', {}),
    ]
    for key, val in wrong_values:
        new_line_args = line_args.copy()
        new_line_args[key] = val
        JournalLine.objects.create(
            **new_line_args,
        )
    assert utils.get_previous_campaign_journal_lines_for_user(
        corrective_pool1, 'user:1', check_status_list
    ) == {
        'agenda-1@event-1': {
            '2022-09-01': primary_pool_line11,
            '2022-09-02': primary_pool_line12,
        },
    }

    # with matching values
    new_line_args = line_args.copy()
    new_line_args['event_date'] = datetime.date(2022, 9, 3)
    corrective_pool_line23 = JournalLine.objects.create(
        **new_line_args,
    )
    new_line_args = line_args.copy()
    new_line_args['event_date'] = datetime.date(2022, 9, 2)
    corrective_pool_line22 = JournalLine.objects.create(
        **new_line_args,
    )
    assert utils.get_previous_campaign_journal_lines_for_user(
        corrective_pool1, 'user:1', check_status_list
    ) == {
        'agenda-1@event-1': {
            '2022-09-01': primary_pool_line11,
            '2022-09-02': corrective_pool_line22,
            '2022-09-03': corrective_pool_line23,
        },
    }


def test_compare_journal_lines_nominal_case():
    regie = Regie.objects.create(label='Regie')
    agenda = Agenda.objects.create(label='Agenda')
    pricing = Pricing.objects.create(
        date_start=datetime.date(year=2022, month=9, day=1),
        date_end=datetime.date(year=2022, month=10, day=1),
    )
    pricing.agendas.add(agenda)
    primary_campaign = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_debit=datetime.date(2022, 11, 15),
        adjustment_campaign=False,
    )
    corrective_campaign = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_debit=datetime.date(2022, 11, 15),
        adjustment_campaign=False,
        primary_campaign=primary_campaign,
    )
    primary_pool = Pool.objects.create(
        campaign=primary_campaign,
        draft=False,
    )
    corrective_pool = Pool.objects.create(
        campaign=corrective_campaign,
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
        'payer:2': {
            'first_name': 'First2',
            'last_name': 'Last2',
            'address': '42 rue des kangourous\n99999 Kangourou Ville',
            'email': '',
            'phone': '',
            'direct_debit': True,
        },
    }

    # no changes
    previous_journal_line = JournalLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        quantity=1,
        amount=decimal.Decimal('42.5'),
        slug='agenda-1@event-1',
        user_external_id='user:1',
        payer_external_id='payer:1',
        pricing_data={
            'booking_details': {'status': 'presence'},
            'calculation_details': {'pricing': decimal.Decimal('42.5')},
        },
        pool=primary_pool,
    )
    # refresh pricing_data values, to have string instead of chars for pricing
    previous_journal_line.refresh_from_db()
    new_line_kwargs = {
        'label': 'Event 1',
        'event_date': datetime.date(2022, 9, 1),
        'slug': 'agenda@event-1',
        'quantity_type': 'units',
        'user_external_id': 'user:1',
        'user_first_name': 'User1',
        'user_last_name': 'Name1',
        'payer_external_id': 'payer:1',
        'payer_first_name': 'First1',
        'payer_last_name': 'Last1',
        'payer_address': '41 rue des kangourous\n99999 Kangourou Ville',
        'payer_direct_debit': False,
        'event': {'foo': 'bar1'},
        'booking': {'foo': 'baz1'},
        'status': 'success',
        'pool': corrective_pool,
        'accounting_code': '414141',
        # test data
        'quantity': 1,
    }
    new_pricing_data = {
        'booking_details': {'status': 'presence'},
        'calculation_details': {'pricing': decimal.Decimal('42.5')},
        'pricing': decimal.Decimal('42.5'),
    }

    def check_line(line, payer_external_id, quantity, amount, pricing_data):
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
        assert line.event == {'foo': 'bar1'}
        assert line.booking == {'foo': 'baz1'}
        assert line.pricing_data == pricing_data
        assert line.accounting_code == '414141'
        assert line.status == 'success'
        assert line.pool == corrective_pool
        assert line.from_injected_line is None

    lines = list(
        utils.compare_journal_lines(
            request='request',
            pool=corrective_pool,
            check_types={},
            pricing=pricing,
            pricing_data=new_pricing_data,
            line_kwargs=new_line_kwargs.copy(),
            payer_data_cache=payer_data_cache,
            previous_journal_line=previous_journal_line,
        )
    )
    assert lines == []

    # change payer
    previous_journal_line.payer_external_id = 'payer:2'
    previous_journal_line.save()
    lines = list(
        utils.compare_journal_lines(
            request='request',
            pool=corrective_pool,
            check_types={},
            pricing=pricing,
            pricing_data=new_pricing_data,
            line_kwargs=new_line_kwargs.copy(),
            payer_data_cache=payer_data_cache,
            previous_journal_line=previous_journal_line,
        )
    )
    assert len(lines) == 2
    check_line(
        lines[0],
        'payer:2',
        -1,
        decimal.Decimal('42.5'),
        {'adjustment': {'info': 'correction', 'reason': 'missing-cancellation'}},
    )
    check_line(
        lines[1],
        'payer:1',
        1,
        decimal.Decimal('42.5'),
        {
            'booking_details': {'status': 'presence'},
            'calculation_details': {'pricing': decimal.Decimal('42.5')},
            'pricing': decimal.Decimal('42.5'),
        },
    )

    # change amount
    previous_journal_line.payer_external_id = 'payer:1'
    previous_journal_line.save()
    new_line_kwargs.update(
        {
            'quantity': 1,
        }
    )
    new_pricing_data = {
        'booking_details': {'status': 'presence'},
        'calculation_details': {'pricing': 43},
        'pricing': 43,
    }
    lines = list(
        utils.compare_journal_lines(
            request='request',
            pool=corrective_pool,
            check_types={},
            pricing=pricing,
            pricing_data=new_pricing_data,
            line_kwargs=new_line_kwargs.copy(),
            payer_data_cache=payer_data_cache,
            previous_journal_line=previous_journal_line,
        )
    )
    assert len(lines) == 2
    check_line(
        lines[0],
        'payer:1',
        -1,
        decimal.Decimal('42.5'),
        {'adjustment': {'info': 'correction', 'reason': 'missing-cancellation'}},
    )
    check_line(
        lines[1],
        'payer:1',
        1,
        43,
        {
            'booking_details': {'status': 'presence'},
            'calculation_details': {'pricing': 43},
            'pricing': 43,
        },
    )

    # change status
    new_pricing_data = {
        'booking_details': {'status': 'absence'},
        'calculation_details': {'pricing': decimal.Decimal('42.5')},
        'pricing': decimal.Decimal('42.5'),
    }
    lines = list(
        utils.compare_journal_lines(
            request='request',
            pool=corrective_pool,
            check_types={},
            pricing=pricing,
            pricing_data=new_pricing_data,
            line_kwargs=new_line_kwargs.copy(),
            payer_data_cache=payer_data_cache,
            previous_journal_line=previous_journal_line,
        )
    )
    assert len(lines) == 2
    check_line(
        lines[0],
        'payer:1',
        -1,
        decimal.Decimal('42.5'),
        {'adjustment': {'info': 'correction', 'reason': 'missing-cancellation'}},
    )
    check_line(
        lines[1],
        'payer:1',
        1,
        decimal.Decimal('42.5'),
        {
            'booking_details': {'status': 'absence'},
            'calculation_details': {'pricing': decimal.Decimal('42.5')},
            'pricing': decimal.Decimal('42.5'),
        },
    )

    new_pricing_data = {
        'booking_details': {'status': 'not-booked'},
        'pricing': 0,
    }
    lines = list(
        utils.compare_journal_lines(
            request='request',
            pool=corrective_pool,
            check_types={},
            pricing=pricing,
            pricing_data=new_pricing_data,
            line_kwargs=new_line_kwargs.copy(),
            payer_data_cache=payer_data_cache,
            previous_journal_line=previous_journal_line,
        )
    )
    assert len(lines) == 2
    check_line(
        lines[0],
        'payer:1',
        -1,
        decimal.Decimal('42.5'),
        {'adjustment': {'info': 'correction', 'reason': 'missing-cancellation'}},
    )
    check_line(
        lines[1],
        'payer:1',
        1,
        0,
        {
            'booking_details': {'status': 'not-booked'},
            'pricing': 0,
        },
    )

    new_pricing_data = {
        'booking_details': {'status': 'cancelled'},
        'pricing': 0,
    }
    lines = list(
        utils.compare_journal_lines(
            request='request',
            pool=corrective_pool,
            check_types={},
            pricing=pricing,
            pricing_data=new_pricing_data,
            line_kwargs=new_line_kwargs.copy(),
            payer_data_cache=payer_data_cache,
            previous_journal_line=previous_journal_line,
        )
    )
    assert len(lines) == 2
    check_line(
        lines[0],
        'payer:1',
        -1,
        decimal.Decimal('42.5'),
        {'adjustment': {'info': 'correction', 'reason': 'missing-cancellation'}},
    )
    check_line(
        lines[1],
        'payer:1',
        1,
        0,
        {
            'booking_details': {'status': 'cancelled'},
            'pricing': 0,
        },
    )

    # change check_type
    previous_journal_line.pricing_data = {
        'booking_details': {'status': 'presence', 'check_type': 'bar', 'check_type_group': 'foo-group'},
        'calculation_details': {'pricing': decimal.Decimal('42.5')},
        'pricing': decimal.Decimal('42.5'),
    }
    previous_journal_line.save()
    new_pricing_data = {
        'booking_details': {'status': 'presence', 'check_type': 'foo', 'check_type_group': 'foo-group'},
        'calculation_details': {'pricing': decimal.Decimal('42.5')},
        'pricing': decimal.Decimal('42.5'),
    }
    lines = list(
        utils.compare_journal_lines(
            request='request',
            pool=corrective_pool,
            check_types={},
            pricing=pricing,
            pricing_data=new_pricing_data,
            line_kwargs=new_line_kwargs.copy(),
            payer_data_cache=payer_data_cache,
            previous_journal_line=previous_journal_line,
        )
    )
    assert len(lines) == 2
    check_line(
        lines[0],
        'payer:1',
        -1,
        decimal.Decimal('42.5'),
        {'adjustment': {'info': 'correction', 'reason': 'missing-cancellation'}},
    )
    check_line(
        lines[1],
        'payer:1',
        1,
        decimal.Decimal('42.5'),
        {
            'booking_details': {'status': 'presence', 'check_type': 'foo', 'check_type_group': 'foo-group'},
            'calculation_details': {'pricing': decimal.Decimal('42.5')},
            'pricing': decimal.Decimal('42.5'),
        },
    )

    # change check_type_group, same pricing
    previous_journal_line.pricing_data = {
        'booking_details': {'status': 'presence', 'check_type': 'foo', 'check_type_group': 'bar-group'},
        'calculation_details': {'pricing': decimal.Decimal('42.5')},
        'pricing': decimal.Decimal('42.5'),
    }
    previous_journal_line.save()
    new_pricing_data = {
        'booking_details': {'status': 'presence', 'check_type': 'foo', 'check_type_group': 'foo-group'},
        'calculation_details': {'pricing': decimal.Decimal('42.5')},
        'pricing': decimal.Decimal('42.5'),
    }
    lines = list(
        utils.compare_journal_lines(
            request='request',
            pool=corrective_pool,
            check_types={},
            pricing=pricing,
            pricing_data=new_pricing_data,
            line_kwargs=new_line_kwargs.copy(),
            payer_data_cache=payer_data_cache,
            previous_journal_line=previous_journal_line,
        )
    )
    assert len(lines) == 0

    # change check_type_group, and pricing
    previous_journal_line.pricing_data = {
        'booking_details': {'status': 'presence', 'check_type': 'foo', 'check_type_group': 'bar-group'},
        'calculation_details': {'pricing': decimal.Decimal('42.5')},
        'pricing': 0,
    }
    previous_journal_line.save()
    new_pricing_data = {
        'booking_details': {'status': 'presence', 'check_type': 'foo', 'check_type_group': 'foo-group'},
        'calculation_details': {'pricing': decimal.Decimal('42.5')},
        'pricing': decimal.Decimal('42.5'),
    }
    lines = list(
        utils.compare_journal_lines(
            request='request',
            pool=corrective_pool,
            check_types={},
            pricing=pricing,
            pricing_data=new_pricing_data,
            line_kwargs=new_line_kwargs.copy(),
            payer_data_cache=payer_data_cache,
            previous_journal_line=previous_journal_line,
        )
    )
    assert len(lines) == 2
    check_line(
        lines[0],
        'payer:1',
        -1,
        decimal.Decimal('42.5'),
        {'adjustment': {'info': 'correction', 'reason': 'missing-cancellation'}},
    )
    check_line(
        lines[1],
        'payer:1',
        1,
        decimal.Decimal('42.5'),
        {
            'booking_details': {'status': 'presence', 'check_type': 'foo', 'check_type_group': 'foo-group'},
            'calculation_details': {'pricing': decimal.Decimal('42.5')},
            'pricing': decimal.Decimal('42.5'),
        },
    )

    # check with a PayerError
    with mock.patch('lingo.invoicing.models.Regie.get_payer_data') as mock_payer_data:
        mock_payer_data.side_effect = PayerDataError(details={'key': 'foobar', 'reason': 'foo'})
        lines = list(
            utils.compare_journal_lines(
                request='request',
                pool=corrective_pool,
                check_types={},
                pricing=pricing,
                pricing_data=new_pricing_data,
                line_kwargs=new_line_kwargs.copy(),
                payer_data_cache={},
                previous_journal_line=previous_journal_line,
            )
        )
    assert len(lines) == 2
    # first line has a payer error
    assert lines[0].event_date == datetime.date(2022, 9, 1)
    assert lines[0].slug == 'agenda@event-1'
    assert lines[0].label == 'Event 1'
    assert lines[0].amount == decimal.Decimal('42.5')
    assert lines[0].quantity == -1
    assert lines[0].quantity_type == 'units'
    assert lines[0].user_external_id == 'user:1'
    assert lines[0].user_first_name == 'User1'
    assert lines[0].user_last_name == 'Name1'
    assert lines[0].payer_external_id == 'payer:1'
    assert lines[0].payer_first_name == ''
    assert lines[0].payer_last_name == ''
    assert lines[0].payer_address == ''
    assert lines[0].payer_direct_debit is False
    assert lines[0].event == {'foo': 'bar1'}
    assert lines[0].booking == {'foo': 'baz1'}
    assert lines[0].pricing_data == {
        'adjustment': {'reason': 'missing-cancellation', 'info': 'correction'},
        'error': 'PayerDataError',
        'error_details': {'key': 'foobar', 'reason': 'foo'},
    }
    assert lines[0].accounting_code == '414141'
    assert lines[0].status == 'error'
    assert lines[0].pool == corrective_pool
    assert lines[0].from_injected_line is None
    # second line is created with line_kwargs, which contains payer info in this test
    check_line(
        lines[1],
        'payer:1',
        1,
        decimal.Decimal('42.5'),
        {
            'booking_details': {'status': 'presence', 'check_type': 'foo', 'check_type_group': 'foo-group'},
            'calculation_details': {'pricing': decimal.Decimal('42.5')},
            'pricing': decimal.Decimal('42.5'),
        },
    )


def test_compare_journal_lines_adjustment_case_no_change():
    regie = Regie.objects.create(label='Regie')
    agenda = Agenda.objects.create(label='Agenda')
    pricing = Pricing.objects.create(
        date_start=datetime.date(year=2022, month=9, day=1),
        date_end=datetime.date(year=2022, month=10, day=1),
    )
    pricing.agendas.add(agenda)
    primary_campaign = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_debit=datetime.date(2022, 11, 15),
        adjustment_campaign=True,
    )
    corrective_campaign = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_debit=datetime.date(2022, 11, 15),
        adjustment_campaign=True,
        primary_campaign=primary_campaign,
    )
    primary_pool = Pool.objects.create(
        campaign=primary_campaign,
        draft=False,
    )
    corrective_pool = Pool.objects.create(
        campaign=corrective_campaign,
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
        'payer:2': {
            'first_name': 'First2',
            'last_name': 'Last2',
            'address': '42 rue des kangourous\n99999 Kangourou Ville',
            'email': '',
            'phone': '',
            'direct_debit': True,
        },
    }
    previous_journal_line = JournalLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        quantity=1,
        amount=42,
        slug='agenda-1@event-1',
        user_external_id='user:1',
        payer_external_id='payer:1',
        pricing_data={
            'booking_details': {'status': 'presence'},
            'calculation_details': {'pricing': 42},
        },
        pool=primary_pool,
    )
    new_line_kwargs = {
        'quantity': 1,
        'payer_external_id': 'payer:1',
    }
    new_pricing_data = {
        'booking_details': {'status': 'presence'},
        'calculation_details': {'pricing': 42},
        'pricing': 42,
    }

    lines = list(
        utils.compare_journal_lines(
            request='request',
            pool=corrective_pool,
            check_types={},
            pricing=pricing,
            pricing_data=new_pricing_data,
            line_kwargs=new_line_kwargs.copy(),
            payer_data_cache=payer_data_cache,
            previous_journal_line=previous_journal_line,
        )
    )
    assert lines == []


def test_compare_journal_lines_adjustment_case_change_previous_line():
    regie = Regie.objects.create(label='Regie')
    agenda = Agenda.objects.create(label='Agenda')
    pricing = Pricing.objects.create(
        date_start=datetime.date(year=2022, month=9, day=1),
        date_end=datetime.date(year=2022, month=10, day=1),
    )
    pricing.agendas.add(agenda)
    primary_campaign = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_debit=datetime.date(2022, 11, 15),
        adjustment_campaign=True,
    )
    corrective_campaign = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_debit=datetime.date(2022, 11, 15),
        adjustment_campaign=True,
        primary_campaign=primary_campaign,
    )
    primary_pool = Pool.objects.create(
        campaign=primary_campaign,
        draft=False,
    )
    corrective_pool = Pool.objects.create(
        campaign=corrective_campaign,
        draft=True,
    )
    group = CheckTypeGroup.objects.create(label='foobar')
    CheckType.objects.create(label='foo', group=group, kind='presence')
    CheckType.objects.create(label='bar', group=group, kind='absence')
    check_type = CheckType.objects.create(label='unexpected', group=group, kind='presence')
    group.unexpected_presence = check_type
    group.save()
    check_types = {(c.slug, c.group.slug, c.kind): c for c in CheckType.objects.select_related('group').all()}
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
    # new line will not generate a correction: amount is positive
    new_line_kwargs = {
        'label': 'Event 1',
        'event_date': datetime.date(2022, 9, 1),
        'slug': 'agenda@event-1',
        'quantity_type': 'units',
        'user_external_id': 'user:1',
        'user_first_name': 'User1',
        'user_last_name': 'Name1',
        'payer_external_id': 'payer:1',
        'payer_first_name': 'First1',
        'payer_last_name': 'Last1',
        'payer_address': '41 rue des kangourous\n99999 Kangourou Ville',
        'payer_direct_debit': False,
        'event': {'foo': 'bar1'},
        'booking': {'foo': 'baz1'},
        'status': 'success',
        'pool': corrective_pool,
        'accounting_code': '414141',
        'quantity': 1,
    }
    new_pricing_data = {
        'booking_details': {'status': 'presence', 'check_type': 'unexpected', 'check_type_group': 'foobar'},
        'calculation_details': {'pricing': decimal.Decimal('42.5')},
        'pricing': decimal.Decimal('42.5'),
    }

    def check_line(line, payer_external_id, quantity, amount, pricing_data):
        assert line.event_date == datetime.date(2022, 9, 1)
        assert line.slug == 'agenda@event-1'
        assert line.label == 'Event 1'
        assert decimal.Decimal(line.amount) == amount
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
        assert line.event == {'foo': 'bar1'}
        assert line.booking == {'foo': 'baz1'}
        assert line.pricing_data == pricing_data
        assert line.accounting_code == '414141'
        assert line.status == 'success'
        assert line.pool == corrective_pool
        assert line.from_injected_line is None

    def check_previous_line_correction(previous_line_correction):
        check_line(
            previous_line_correction,
            'payer:2',
            -1,
            decimal.Decimal('42.5'),
            {'adjustment': {'info': 'correction', 'reason': 'missing-cancellation'}},
        )

    def check_new_line(new_line):
        check_line(
            new_line,
            'payer:1',
            1,
            decimal.Decimal('42.5'),
            {
                'booking_details': {
                    'status': 'presence',
                    'check_type': 'unexpected',
                    'check_type_group': 'foobar',
                },
                'calculation_details': {'pricing': decimal.Decimal('42.5')},
                'pricing': decimal.Decimal('42.5'),
            },
        )

    # previous line is a presence with modifier of 0%
    previous_journal_line = JournalLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        quantity=0,
        amount=42,
        slug='agenda-1@event-1',
        user_external_id='user:1',
        payer_external_id='payer:2',
        pricing_data={
            'booking_details': {'status': 'presence'},
            'calculation_details': {'pricing': decimal.Decimal('42.5')},
        },
        pool=primary_pool,
    )
    # refresh pricing_data values, to have string instead of chars for pricing
    previous_journal_line.refresh_from_db()
    lines = list(
        utils.compare_journal_lines(
            request='request',
            pool=corrective_pool,
            check_types=check_types,
            pricing=pricing,
            pricing_data=new_pricing_data,
            line_kwargs=new_line_kwargs.copy(),
            payer_data_cache=payer_data_cache,
            previous_journal_line=previous_journal_line,
        )
    )
    assert len(lines) == 2
    # correction of previous line
    check_previous_line_correction(lines[0])
    # new line
    check_new_line(lines[-1])

    # previous line is a presence with modifier of 100%
    previous_journal_line = JournalLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        quantity=1,
        amount=decimal.Decimal('42.5'),
        slug='agenda-1@event-1',
        user_external_id='user:1',
        payer_external_id='payer:2',
        pricing_data={
            'booking_details': {
                'status': 'presence',
                'check_type': 'unexpected',
                'check_type_group': 'foobar',
            },
            'calculation_details': {'pricing': decimal.Decimal('42.5')},
        },
        pool=primary_pool,
    )
    lines = list(
        utils.compare_journal_lines(
            request='request',
            pool=corrective_pool,
            check_types=check_types,
            pricing=pricing,
            pricing_data=new_pricing_data,
            line_kwargs=new_line_kwargs.copy(),
            payer_data_cache=payer_data_cache,
            previous_journal_line=previous_journal_line,
        )
    )
    assert len(lines) == 2
    # correction of previous line
    check_previous_line_correction(lines[0])
    # new line
    check_new_line(lines[-1])

    # previous line is a presence with modifier of -100%
    previous_journal_line = JournalLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        quantity=-1,
        amount=decimal.Decimal('42.5'),
        slug='agenda-1@event-1',
        user_external_id='user:1',
        payer_external_id='payer:2',
        pricing_data={
            'booking_details': {'status': 'presence'},
            'calculation_details': {'pricing': decimal.Decimal('42.5')},
        },
        pool=primary_pool,
    )
    lines = list(
        utils.compare_journal_lines(
            request='request',
            pool=corrective_pool,
            check_types=check_types,
            pricing=pricing,
            pricing_data=new_pricing_data,
            line_kwargs=new_line_kwargs.copy(),
            payer_data_cache=payer_data_cache,
            previous_journal_line=previous_journal_line,
        )
    )
    assert len(lines) == 1
    # no correction of previous line
    # new line
    check_new_line(lines[-1])

    # previous line is an absence with modifier of 0%
    previous_journal_line = JournalLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        quantity=0,
        amount=decimal.Decimal('42.5'),
        slug='agenda-1@event-1',
        user_external_id='user:1',
        payer_external_id='payer:2',
        pricing_data={
            'booking_details': {'status': 'absence'},
            'calculation_details': {'pricing': decimal.Decimal('42.5')},
        },
        pool=primary_pool,
    )
    lines = list(
        utils.compare_journal_lines(
            request='request',
            pool=corrective_pool,
            check_types=check_types,
            pricing=pricing,
            pricing_data=new_pricing_data,
            line_kwargs=new_line_kwargs.copy(),
            payer_data_cache=payer_data_cache,
            previous_journal_line=previous_journal_line,
        )
    )
    assert len(lines) == 2
    # correction of previous line
    check_previous_line_correction(lines[0])
    # new line
    check_new_line(lines[-1])

    # check with a PayerError
    with mock.patch('lingo.invoicing.models.Regie.get_payer_data') as mock_payer_data:
        mock_payer_data.side_effect = PayerDataError(details={'key': 'foobar', 'reason': 'foo'})
        lines = list(
            utils.compare_journal_lines(
                request='request',
                pool=corrective_pool,
                check_types=check_types,
                pricing=pricing,
                pricing_data=new_pricing_data,
                line_kwargs=new_line_kwargs.copy(),
                payer_data_cache={},
                previous_journal_line=previous_journal_line,
            )
        )
    assert len(lines) == 2
    # first line has a payer error
    assert lines[0].event_date == datetime.date(2022, 9, 1)
    assert lines[0].slug == 'agenda@event-1'
    assert lines[0].label == 'Event 1'
    assert lines[0].amount == decimal.Decimal('42.5')
    assert lines[0].quantity == -1
    assert lines[0].quantity_type == 'units'
    assert lines[0].user_external_id == 'user:1'
    assert lines[0].user_first_name == 'User1'
    assert lines[0].user_last_name == 'Name1'
    assert lines[0].payer_external_id == 'payer:2'
    assert lines[0].payer_first_name == ''
    assert lines[0].payer_last_name == ''
    assert lines[0].payer_address == ''
    assert lines[0].payer_direct_debit is False
    assert lines[0].event == {'foo': 'bar1'}
    assert lines[0].booking == {'foo': 'baz1'}
    assert lines[0].pricing_data == {
        'adjustment': {'reason': 'missing-cancellation', 'info': 'correction'},
        'error': 'PayerDataError',
        'error_details': {'key': 'foobar', 'reason': 'foo'},
    }
    assert lines[0].accounting_code == '414141'
    assert lines[0].status == 'error'
    assert lines[0].pool == corrective_pool
    assert lines[0].from_injected_line is None
    # second line is created with line_kwargs, which contains payer info in this test
    check_new_line(lines[-1])

    # previous line is an absence with modifier of -100%
    previous_journal_line = JournalLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        quantity=-1,
        amount=decimal.Decimal('42.5'),
        slug='agenda-1@event-1',
        user_external_id='user:1',
        payer_external_id='payer:2',
        pricing_data={
            'booking_details': {'status': 'absence'},
            'calculation_details': {'pricing': decimal.Decimal('42.5')},
        },
        pool=primary_pool,
    )
    lines = list(
        utils.compare_journal_lines(
            request='request',
            pool=corrective_pool,
            check_types=check_types,
            pricing=pricing,
            pricing_data=new_pricing_data,
            line_kwargs=new_line_kwargs.copy(),
            payer_data_cache=payer_data_cache,
            previous_journal_line=previous_journal_line,
        )
    )
    assert len(lines) == 1
    # no correction of previous line
    # new line
    check_new_line(lines[-1])

    # previous line is 'not-booked'
    previous_journal_line = JournalLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        quantity=-1,
        amount=decimal.Decimal('42.5'),
        slug='agenda-1@event-1',
        user_external_id='user:1',
        payer_external_id='payer:2',
        pricing_data={
            'booking_details': {'status': 'not-booked'},
            'pricing': 0,
        },
        pool=primary_pool,
    )
    lines = list(
        utils.compare_journal_lines(
            request='request',
            pool=corrective_pool,
            check_types=check_types,
            pricing=pricing,
            pricing_data=new_pricing_data,
            line_kwargs=new_line_kwargs.copy(),
            payer_data_cache=payer_data_cache,
            previous_journal_line=previous_journal_line,
        )
    )
    assert len(lines) == 1
    # no correction of previous line
    # new line
    check_new_line(lines[-1])

    # previous line is 'cancelled'
    previous_journal_line = JournalLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        quantity=-1,
        amount=decimal.Decimal('42.5'),
        slug='agenda-1@event-1',
        user_external_id='user:1',
        payer_external_id='payer:2',
        pricing_data={
            'booking_details': {'status': 'cancelled'},
            'pricing': 0,
        },
        pool=primary_pool,
    )
    lines = list(
        utils.compare_journal_lines(
            request='request',
            pool=corrective_pool,
            check_types=check_types,
            pricing=pricing,
            pricing_data=new_pricing_data,
            line_kwargs=new_line_kwargs.copy(),
            payer_data_cache=payer_data_cache,
            previous_journal_line=previous_journal_line,
        )
    )
    assert len(lines) == 1
    # no correction of previous line
    # new line
    check_new_line(lines[-1])


def test_compare_journal_lines_adjustment_case_change_new_line():
    regie = Regie.objects.create(label='Regie')
    agenda = Agenda.objects.create(label='Agenda')
    pricing = Pricing.objects.create(
        date_start=datetime.date(year=2022, month=9, day=1),
        date_end=datetime.date(year=2022, month=10, day=1),
    )
    pricing.agendas.add(agenda)
    primary_campaign = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_debit=datetime.date(2022, 11, 15),
        adjustment_campaign=True,
    )
    corrective_campaign = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_debit=datetime.date(2022, 11, 15),
        adjustment_campaign=True,
        primary_campaign=primary_campaign,
    )
    primary_pool = Pool.objects.create(
        campaign=primary_campaign,
        draft=False,
    )
    corrective_pool = Pool.objects.create(
        campaign=corrective_campaign,
        draft=True,
    )
    group = CheckTypeGroup.objects.create(label='foobar')
    CheckType.objects.create(label='foo', group=group, kind='presence')
    CheckType.objects.create(label='bar', group=group, kind='absence')
    check_type = CheckType.objects.create(label='unexpected', group=group, kind='presence')
    group.unexpected_presence = check_type
    group.save()
    check_types = {(c.slug, c.group.slug, c.kind): c for c in CheckType.objects.select_related('group').all()}
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
    new_line_kwargs = {
        'label': 'Event 1',
        'event_date': datetime.date(2022, 9, 1),
        'slug': 'agenda@event-1',
        'quantity_type': 'units',
        'user_external_id': 'user:1',
        'user_first_name': 'User1',
        'user_last_name': 'Name1',
        'payer_external_id': 'payer:1',
        'payer_first_name': 'First1',
        'payer_last_name': 'Last1',
        'payer_address': '41 rue des kangourous\n99999 Kangourou Ville',
        'payer_direct_debit': False,
        'event': {'foo': 'bar1'},
        'booking': {'foo': 'baz1'},
        'status': 'success',
        'pool': corrective_pool,
        'accounting_code': '414141',
        'quantity': 1,
    }
    # previous line will not generate a correction: amount is negative
    previous_journal_line = JournalLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        quantity=-1,
        amount=42,
        slug='agenda-1@event-1',
        user_external_id='user:1',
        payer_external_id='payer:2',
        pricing_data={
            'booking_details': {'status': 'presence'},
            'calculation_details': {'pricing': 42},
        },
        pool=primary_pool,
    )

    def check_line(line, payer_external_id, quantity, amount, pricing_data):
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
        assert line.event == {'foo': 'bar1'}
        assert line.booking == {'foo': 'baz1'}
        assert line.pricing_data == pricing_data
        assert line.accounting_code == '414141'
        assert line.status == 'success'
        assert line.pool == corrective_pool
        assert line.from_injected_line is None

    def check_new_line_correction(previous_line_correction):
        check_line(
            previous_line_correction,
            'payer:1',
            1,
            42,
            {'adjustment': {'info': 'correction', 'reason': 'missing-booking'}},
        )

    # new line is a presence with modifier of 0%
    new_pricing_data = {
        'booking_details': {'status': 'presence'},
        'calculation_details': {'pricing': 42},
        'pricing': 0,
    }
    lines = list(
        utils.compare_journal_lines(
            request='request',
            pool=corrective_pool,
            check_types=check_types,
            pricing=pricing,
            pricing_data=new_pricing_data,
            line_kwargs=new_line_kwargs.copy(),
            payer_data_cache=payer_data_cache,
            previous_journal_line=previous_journal_line,
        )
    )
    assert len(lines) == 2
    # correction of new line
    check_new_line_correction(lines[0])
    # new line
    check_line(
        lines[-1],
        'payer:1',
        1,
        0,
        {
            'booking_details': {'status': 'presence'},
            'calculation_details': {'pricing': 42},
            'pricing': 0,
        },
    )

    # new line is a presence with modifier of 100%
    new_pricing_data = {
        'booking_details': {'status': 'presence', 'check_type': 'unexpected', 'check_type_group': 'foobar'},
        'calculation_details': {'pricing': 42},
        'pricing': 42,
    }
    lines = list(
        utils.compare_journal_lines(
            request='request',
            pool=corrective_pool,
            check_types=check_types,
            pricing=pricing,
            pricing_data=new_pricing_data,
            line_kwargs=new_line_kwargs.copy(),
            payer_data_cache=payer_data_cache,
            previous_journal_line=previous_journal_line,
        )
    )
    assert len(lines) == 1
    # no correction of new line
    # new line
    check_line(
        lines[-1],
        'payer:1',
        1,
        42,
        {
            'booking_details': {
                'status': 'presence',
                'check_type': 'unexpected',
                'check_type_group': 'foobar',
            },
            'calculation_details': {'pricing': 42},
            'pricing': 42,
        },
    )

    # new line is a presence with modifier of -100%
    new_pricing_data = {
        'booking_details': {'status': 'presence', 'check_type': 'foo', 'check_type_group': 'foobar'},
        'calculation_details': {'pricing': 42},
        'pricing': -42,
    }
    lines = list(
        utils.compare_journal_lines(
            request='request',
            pool=corrective_pool,
            check_types=check_types,
            pricing=pricing,
            pricing_data=new_pricing_data,
            line_kwargs=new_line_kwargs.copy(),
            payer_data_cache=payer_data_cache,
            previous_journal_line=previous_journal_line,
        )
    )
    assert len(lines) == 2
    # correction of new line
    check_new_line_correction(lines[0])
    # new line
    check_line(
        lines[-1],
        'payer:1',
        -1,
        42,
        {
            'booking_details': {'status': 'presence', 'check_type': 'foo', 'check_type_group': 'foobar'},
            'calculation_details': {'pricing': 42},
            'pricing': -42,
        },
    )

    # new line is an absence with modifier of 0%
    new_pricing_data = {
        'booking_details': {'status': 'absence'},
        'calculation_details': {'pricing': 42},
        'pricing': 0,
    }
    lines = list(
        utils.compare_journal_lines(
            request='request',
            pool=corrective_pool,
            check_types=check_types,
            pricing=pricing,
            pricing_data=new_pricing_data,
            line_kwargs=new_line_kwargs.copy(),
            payer_data_cache=payer_data_cache,
            previous_journal_line=previous_journal_line,
        )
    )
    assert len(lines) == 2
    # correction of new line
    check_new_line_correction(lines[0])
    # new line
    check_line(
        lines[-1],
        'payer:1',
        1,
        0,
        {
            'booking_details': {'status': 'absence'},
            'calculation_details': {'pricing': 42},
            'pricing': 0,
        },
    )

    # new line is an absence with modifier of -100%
    new_pricing_data = {
        'booking_details': {'status': 'absence', 'check_type': 'bar', 'check_type_group': 'foobar'},
        'calculation_details': {'pricing': 42},
        'pricing': -42,
    }
    lines = list(
        utils.compare_journal_lines(
            request='request',
            pool=corrective_pool,
            check_types=check_types,
            pricing=pricing,
            pricing_data=new_pricing_data,
            line_kwargs=new_line_kwargs.copy(),
            payer_data_cache=payer_data_cache,
            previous_journal_line=previous_journal_line,
        )
    )
    assert len(lines) == 2
    # correction of new line
    check_new_line_correction(lines[0])
    # new line
    check_line(
        lines[-1],
        'payer:1',
        -1,
        42,
        {
            'booking_details': {'status': 'absence', 'check_type': 'bar', 'check_type_group': 'foobar'},
            'calculation_details': {'pricing': 42},
            'pricing': -42,
        },
    )

    # new line is not-booked
    new_pricing_data = {
        'booking_details': {'status': 'not-booked'},
        'pricing': 0,
    }
    lines = list(
        utils.compare_journal_lines(
            request='request',
            pool=corrective_pool,
            check_types=check_types,
            pricing=pricing,
            pricing_data=new_pricing_data,
            line_kwargs=new_line_kwargs.copy(),
            payer_data_cache=payer_data_cache,
            previous_journal_line=previous_journal_line,
        )
    )
    assert len(lines) == 1
    # new line
    check_line(
        lines[-1],
        'payer:1',
        1,
        0,
        {
            'booking_details': {'status': 'not-booked'},
            'pricing': 0,
        },
    )

    # new line is cancelled
    new_pricing_data = {
        'booking_details': {'status': 'cancelled'},
        'pricing': 0,
    }
    lines = list(
        utils.compare_journal_lines(
            request='request',
            pool=corrective_pool,
            check_types=check_types,
            pricing=pricing,
            pricing_data=new_pricing_data,
            line_kwargs=new_line_kwargs.copy(),
            payer_data_cache=payer_data_cache,
            previous_journal_line=previous_journal_line,
        )
    )
    assert len(lines) == 1
    # new line
    check_line(
        lines[-1],
        'payer:1',
        1,
        0,
        {
            'booking_details': {'status': 'cancelled'},
            'pricing': 0,
        },
    )


def test_check_primary_campaign_amounts():
    regie = Regie.objects.create(label='Regie')
    agenda = Agenda.objects.create(label='Agenda')
    pricing = Pricing.objects.create(
        date_start=datetime.date(year=2022, month=9, day=1),
        date_end=datetime.date(year=2022, month=10, day=1),
    )
    pricing.agendas.add(agenda)
    primary_campaign = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_debit=datetime.date(2022, 11, 15),
        adjustment_campaign=True,
    )
    corrective_campaign = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_debit=datetime.date(2022, 11, 15),
        adjustment_campaign=True,
        primary_campaign=primary_campaign,
    )
    primary_pool = Pool.objects.create(
        campaign=primary_campaign,
        draft=False,
    )
    corrective_pool = Pool.objects.create(
        campaign=corrective_campaign,
        draft=True,
    )
    group = CheckTypeGroup.objects.create(label='foobar')
    CheckType.objects.create(label='foo', group=group, kind='presence')
    CheckType.objects.create(label='bar', group=group, kind='absence')
    check_type = CheckType.objects.create(label='unexpected', group=group, kind='presence')
    group.unexpected_presence = check_type
    group.save()
    check_types = {(c.slug, c.group.slug, c.kind): c for c in CheckType.objects.select_related('group').all()}
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

    line_kwargs = {
        'label': 'Event 1',
        'event_date': datetime.date(2022, 9, 1),
        'slug': 'agenda@event-1',
        'quantity_type': 'units',
        'user_external_id': 'user:1',
        'user_first_name': 'User1',
        'user_last_name': 'Name1',
        'payer_external_id': 'payer:1',
        'payer_first_name': 'First1',
        'payer_last_name': 'Last1',
        'payer_address': '41 rue des kangourous\n99999 Kangourou Ville',
        'payer_direct_debit': False,
        'event': {'foo': 'bar1'},
        'booking': {'foo': 'baz1'},
        'status': 'success',
        'pool': corrective_pool,
        'accounting_code': '414141',
        'quantity': 1,
    }

    def check_line(line, payer_external_id, quantity, amount, pricing_data):
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
        assert line.event == {'foo': 'bar1'}
        assert line.booking == {'foo': 'baz1'}
        assert line.pricing_data == pricing_data
        assert line.accounting_code == '414141'
        assert line.status == 'success'
        assert line.pool == corrective_pool
        assert line.from_injected_line is None

    # status is wrong
    previous_journal_line = JournalLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        quantity=1,
        amount=42,
        slug='agenda@event-1',
        user_external_id='user:1',
        payer_external_id='payer:1',
        pricing_data={
            'booking_details': {'status': 'foo'},
            'calculation_details': {'pricing': 42},
            'pricing': 0,
        },
        pool=primary_pool,
    )
    existing = {
        'agenda@event-1': {
            '2022-09-01': [
                utils.Link(
                    payer_external_id='payer:1',
                    unit_amount=11,
                    booked=True,
                    invoicing_element_number='F-0001',
                ),
            ]
        },
    }
    lines = list(
        utils.check_primary_campaign_amounts(
            request='request',
            pool=corrective_pool,
            check_types=check_types,
            pricing=pricing,
            line_kwargs=line_kwargs,
            payer_data_cache=payer_data_cache,
            previous_journal_line=previous_journal_line,
            existing_lines=existing,
        )
    )
    assert lines == []

    # unexpected presence
    previous_journal_line = JournalLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        quantity=1,
        amount=42,
        slug='agenda@event-1',
        user_external_id='user:1',
        payer_external_id='payer:1',
        pricing_data={
            'booking_details': {
                'status': 'presence',
                'check_type': 'unexpected',
                'check_type_group': 'foobar',
            },
            'calculation_details': {'pricing': 42},
            'pricing': 0,
        },
        pool=primary_pool,
    )
    existing = {
        'agenda@event-1': {
            '2022-09-01': [
                utils.Link(
                    payer_external_id='payer:1',
                    unit_amount=11,
                    booked=True,
                    invoicing_element_number='F-0001',
                ),
            ]
        },
    }
    lines = list(
        utils.check_primary_campaign_amounts(
            request='request',
            pool=corrective_pool,
            check_types=check_types,
            pricing=pricing,
            line_kwargs=line_kwargs,
            payer_data_cache=payer_data_cache,
            previous_journal_line=previous_journal_line,
            existing_lines=existing,
        )
    )
    assert lines == []

    # no existing_lines
    previous_journal_line = JournalLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        quantity=1,
        amount=decimal.Decimal('42.5'),
        slug='agenda@event-1',
        user_external_id='user:1',
        payer_external_id='payer:1',
        pricing_data={
            'booking_details': {'status': 'presence'},
            'calculation_details': {'pricing': decimal.Decimal('42.5')},
            'pricing': 0,
        },
        event={'primary_event': 'primary-event-1'},
        pool=primary_pool,
    )
    # refresh pricing_data values, to have string instead of chars for pricing
    previous_journal_line.refresh_from_db()
    existing = {
        'agenda@primary-event-1': {
            '2022-09-02': [
                utils.Link(
                    payer_external_id='payer:1',
                    unit_amount=11,
                    booked=True,
                    invoicing_element_number='F-0001',
                ),
            ]
        },
        'agenda@primary-event-2': {
            '2022-09-01': [
                utils.Link(
                    payer_external_id='payer:1',
                    unit_amount=11,
                    booked=True,
                    invoicing_element_number='F-0001',
                ),
            ]
        },
        'agenda2@primary-event-1': {
            '2022-09-01': [
                utils.Link(
                    payer_external_id='payer:1',
                    unit_amount=11,
                    booked=True,
                    invoicing_element_number='F-0001',
                ),
            ]
        },
    }
    lines = list(
        utils.check_primary_campaign_amounts(
            request='request',
            pool=corrective_pool,
            check_types=check_types,
            pricing=pricing,
            line_kwargs=line_kwargs,
            payer_data_cache=payer_data_cache,
            previous_journal_line=previous_journal_line,
            existing_lines=existing,
        )
    )
    assert lines == []

    # existing lines, but it ends with a cancellation
    existing = [
        {
            'agenda@primary-event-1': {
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
            'agenda@primary-event-1': {
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
                        payer_external_id='payer:1',
                        unit_amount=22,
                        booked=True,
                        invoicing_element_number='F-0002',
                    ),
                    utils.Link(
                        payer_external_id='payer:1',
                        unit_amount=22,
                        booked=False,
                        invoicing_element_number='A-0002',
                    ),
                ]
            }
        },
    ]
    for value in existing:
        lines = list(
            utils.check_primary_campaign_amounts(
                request='request',
                pool=corrective_pool,
                check_types=check_types,
                pricing=pricing,
                line_kwargs=line_kwargs,
                payer_data_cache=payer_data_cache,
                previous_journal_line=previous_journal_line,
                existing_lines=value,
            )
        )
        assert lines == []

    # existing lines, last one is a booking; amount has changed
    existing = [
        {
            'agenda@primary-event-1': {
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
            'agenda@primary-event-1': {
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
        lines = list(
            utils.check_primary_campaign_amounts(
                request='request',
                pool=corrective_pool,
                check_types=check_types,
                pricing=pricing,
                line_kwargs=line_kwargs,
                payer_data_cache=payer_data_cache,
                previous_journal_line=previous_journal_line,
                existing_lines=value,
            )
        )
        assert len(lines) == 2
        check_line(
            lines[0],
            'payer:1',
            -1,
            11,
            {'adjustment': {'before': 'F-0002', 'info': 'pricing-changed', 'reason': 'missing-cancellation'}},
        )
        check_line(
            lines[1],
            'payer:1',
            1,
            decimal.Decimal('42.5'),
            {'adjustment': {'before': 'F-0002', 'info': 'pricing-changed', 'reason': 'missing-booking'}},
        )

    # existing lines, last one is a booking; payer has changed
    existing = [
        {
            'agenda@primary-event-1': {
                '2022-09-01': [
                    utils.Link(
                        payer_external_id='payer:2',
                        unit_amount=decimal.Decimal('42.5'),
                        booked=True,
                        invoicing_element_number='F-0002',
                    ),
                ]
            }
        },
        {
            'agenda@primary-event-1': {
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
                        unit_amount=decimal.Decimal('42.5'),
                        booked=True,
                        invoicing_element_number='F-0002',
                    ),
                ]
            }
        },
    ]
    for value in existing:
        lines = list(
            utils.check_primary_campaign_amounts(
                request='request',
                pool=corrective_pool,
                check_types=check_types,
                pricing=pricing,
                line_kwargs=line_kwargs,
                payer_data_cache=payer_data_cache,
                previous_journal_line=previous_journal_line,
                existing_lines=value,
            )
        )
        assert len(lines) == 2
        check_line(
            lines[0],
            'payer:2',
            -1,
            decimal.Decimal('42.5'),
            {'adjustment': {'before': 'F-0002', 'info': 'pricing-changed', 'reason': 'missing-cancellation'}},
        )
        check_line(
            lines[1],
            'payer:1',
            1,
            decimal.Decimal('42.5'),
            {'adjustment': {'before': 'F-0002', 'info': 'pricing-changed', 'reason': 'missing-booking'}},
        )

    # existing lines, last one is a booking; no change
    existing = [
        {
            'agenda@primary-event-1': {
                '2022-09-01': [
                    utils.Link(
                        payer_external_id='payer:1',
                        unit_amount=decimal.Decimal('42.5'),
                        booked=True,
                        invoicing_element_number='F-0002',
                    ),
                ]
            }
        },
        {
            'agenda@primary-event-1': {
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
                        unit_amount=decimal.Decimal('42.5'),
                        booked=True,
                        invoicing_element_number='F-0002',
                    ),
                ]
            }
        },
    ]
    for value in existing:
        lines = list(
            utils.check_primary_campaign_amounts(
                request='request',
                pool=corrective_pool,
                check_types=check_types,
                pricing=pricing,
                line_kwargs=line_kwargs,
                payer_data_cache=payer_data_cache,
                previous_journal_line=previous_journal_line,
                existing_lines=value,
            )
        )
        assert lines == []


@mock.patch('lingo.invoicing.models.Regie.get_payer_external_id')
@mock.patch('lingo.pricing.models.Pricing.get_pricing_data_for_event')
@mock.patch('lingo.invoicing.utils.check_primary_campaign_amounts')
@mock.patch('lingo.invoicing.utils.compare_journal_lines')
def test_build_lines_for_user_corrective_campaign_with_partial_bookings(
    mock_compare, mock_check_amounts, mock_pricing_data_event, mock_payer
):
    regie = Regie.objects.create(label='Regie')
    partial_agenda = Agenda.objects.create(label='Partial', partial_bookings=True)
    pricing = Pricing.objects.create(
        date_start=datetime.date(year=2022, month=9, day=1),
        date_end=datetime.date(year=2022, month=10, day=1),
    )
    pricing.agendas.add(partial_agenda)
    primary_campaign = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_debit=datetime.date(2022, 11, 15),
        adjustment_campaign=True,
    )
    corrective_campaign = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_debit=datetime.date(2022, 11, 15),
        adjustment_campaign=True,
        primary_campaign=primary_campaign,
    )
    corrective_pool = Pool.objects.create(
        campaign=corrective_campaign,
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
    check_status_list = [
        {
            'event': {
                'agenda': 'partial',
                'start_datetime': '2022-09-01T12:00:00+02:00',
                'slug': 'event',
                'label': 'Event',
            },
            'check_status': {'foo': 'bar'},
            'booking': {'foo': 'baz'},
        },
    ]
    mock_pricing_data_event.return_value = {
        'accounting_code': '414141',
        'booking_details': {'status': 'not-booked'},
        'calculation_details': {'pricing': 0},
        'pricing': 0,
    }

    lines = utils.build_lines_for_user(
        agendas=[partial_agenda],
        agendas_pricings=[pricing],
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
        pool=corrective_pool,
        check_status_list=check_status_list,
        payer_data_cache=payer_data_cache,
    )
    assert list(lines) == []
    assert mock_compare.call_args_list == []
    assert mock_check_amounts.call_args_list == []


@mock.patch('lingo.invoicing.utils.get_existing_lines_for_user')
@mock.patch('lingo.invoicing.utils.get_previous_campaign_journal_lines_for_user')
@mock.patch('lingo.invoicing.utils.check_primary_campaign_amounts')
@mock.patch('lingo.invoicing.utils.compare_journal_lines')
@mock.patch('lingo.invoicing.models.Regie.get_payer_external_id')
@mock.patch('lingo.pricing.models.Pricing.get_pricing_data_for_event')
def test_build_lines_for_user_corrective_campaign_user_not_found(
    mock_pricing_data_event, mock_payer, mock_compare, mock_check_amounts, mock_previous, mock_existing
):
    regie = Regie.objects.create(label='Regie')
    agenda = Agenda.objects.create(label='Agenda')
    pricing = Pricing.objects.create(
        date_start=datetime.date(year=2022, month=9, day=1),
        date_end=datetime.date(year=2022, month=10, day=1),
    )
    pricing.agendas.add(agenda)
    primary_campaign = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_debit=datetime.date(2022, 11, 15),
        adjustment_campaign=True,
    )
    corrective_campaign = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_debit=datetime.date(2022, 11, 15),
        adjustment_campaign=True,
        primary_campaign=primary_campaign,
    )
    corrective_pool = Pool.objects.create(
        campaign=corrective_campaign,
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
    mock_existing.return_value = {}
    mock_previous.return_value = {}
    check_status_list = [
        {
            'event': {
                'agenda': 'agenda',
                'start_datetime': '2022-09-01T12:00:00+02:00',
                'slug': 'event',
                'label': 'Event',
            },
            'check_status': {'foo': 'bar'},
            'booking': {'foo': 'baz'},
        },
    ]
    mock_pricing_data_event.return_value = {
        'accounting_code': '414141',
        'booking_details': {'status': 'not-booked'},
        'calculation_details': {'pricing': 0},
        'pricing': 0,
    }

    lines = utils.build_lines_for_user(
        agendas=[agenda],
        agendas_pricings=[pricing],
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
        pool=corrective_pool,
        check_status_list=check_status_list,
        payer_data_cache=payer_data_cache,
    )
    assert len(lines) == 1
    assert mock_compare.call_args_list == []
    assert mock_check_amounts.call_args_list == []
    assert isinstance(lines[0], DraftJournalLine)
    assert lines[0].event_date == datetime.date(2022, 9, 1)
    assert lines[0].slug == 'agenda@event'
    assert lines[0].label == 'Event'
    assert lines[0].amount == 0
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
        'slug': 'event',
        'label': 'Event',
    }
    assert lines[0].booking == {'foo': 'baz'}
    assert lines[0].pricing_data == {
        'accounting_code': '414141',
        'booking_details': {'status': 'not-booked'},
        'calculation_details': {'pricing': 0},
        'pricing': 0,
    }
    assert lines[0].accounting_code == '414141'
    assert lines[0].status == 'success'
    assert lines[0].pool == corrective_pool
    assert lines[0].from_injected_line is None


@mock.patch('lingo.invoicing.utils.get_existing_lines_for_user')
@mock.patch('lingo.invoicing.utils.get_previous_campaign_journal_lines_for_user')
@mock.patch('lingo.invoicing.utils.check_primary_campaign_amounts')
@mock.patch('lingo.invoicing.utils.compare_journal_lines')
@mock.patch('lingo.invoicing.models.Regie.get_payer_external_id')
@mock.patch('lingo.pricing.models.Pricing.get_pricing_data_for_event')
def test_build_lines_for_user_corrective_campaign_check_primary_campaign_amounts(
    mock_pricing_data_event, mock_payer, mock_compare, mock_check_amounts, mock_previous, mock_existing
):
    regie = Regie.objects.create(label='Regie')
    agenda = Agenda.objects.create(label='Agenda')
    pricing = Pricing.objects.create(
        date_start=datetime.date(year=2022, month=9, day=1),
        date_end=datetime.date(year=2022, month=10, day=1),
    )
    pricing.agendas.add(agenda)
    primary_campaign = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_debit=datetime.date(2022, 11, 15),
        adjustment_campaign=True,
    )
    corrective_campaign1 = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_debit=datetime.date(2022, 11, 15),
        adjustment_campaign=True,
        primary_campaign=primary_campaign,
    )
    primary_pool = Pool.objects.create(
        campaign=primary_campaign,
        draft=False,
    )
    corrective_pool1 = Pool.objects.create(
        campaign=corrective_campaign1,
        draft=True,
    )
    previous_journal_line = JournalLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        quantity=1,
        amount=42,
        slug='agenda@event',
        user_external_id='user:1',
        payer_external_id='payer:1',
        pricing_data={
            'booking_details': {'status': 'presence'},
            'calculation_details': {'pricing': 42},
        },
        pool=primary_pool,
    )

    payer_data_cache = {
        'payer:1': {
            'first_name': 'First1',
            'last_name': 'Last1',
            'address': '41 rue des kangourous\n99999 Kangourou Ville',
            'email': 'email1',
            'phone': 'phone1',
            'direct_debit': False,
        },
    }

    mock_payer.return_value = 'payer:1'
    mock_existing.return_value = {}
    mock_previous.return_value = {
        'agenda@event': {
            '2022-09-01': previous_journal_line,
        },
    }
    check_status_list = [
        {
            'event': {
                'agenda': 'agenda',
                'start_datetime': '2022-09-01T12:00:00+02:00',
                'slug': 'event',
                'label': 'Event',
            },
            'check_status': {'foo': 'bar'},
            'booking': {'foo': 'baz'},
        },
    ]
    mock_pricing_data_event.return_value = {
        'accounting_code': '414141',
        'booking_details': {'status': 'not-booked'},
        'calculation_details': {'pricing': 0},
        'pricing': 0,
    }
    mock_compare.return_value = []

    # this is not the first corrective campaign for this primary campaign
    corrective_campaign2 = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_debit=datetime.date(2022, 11, 15),
        adjustment_campaign=True,
        primary_campaign=primary_campaign,
    )
    corrective_pool2 = Pool.objects.create(
        campaign=corrective_campaign2,
        draft=True,
    )
    lines = utils.build_lines_for_user(
        agendas=[agenda],
        agendas_pricings=[pricing],
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
        pool=corrective_pool2,
        check_status_list=check_status_list,
        payer_data_cache=payer_data_cache,
    )
    assert lines == []  # result of compare_journal_lines
    assert mock_compare.call_args_list == [
        mock.call(
            request=mock.ANY,
            pool=corrective_pool2,
            pricing=pricing,
            check_types={},
            pricing_data={
                'accounting_code': '414141',
                'booking_details': {'status': 'not-booked'},
                'calculation_details': {'pricing': 0},
                'pricing': 0,
            },
            line_kwargs={
                'label': 'Event',
                'event_date': datetime.date(2022, 9, 1),
                'slug': 'agenda@event',
                'quantity': 1,
                'quantity_type': 'units',
                'user_external_id': 'user:1',
                'user_first_name': 'User1',
                'user_last_name': 'Name1',
                'payer_external_id': 'payer:1',
                'payer_first_name': 'First1',
                'payer_last_name': 'Last1',
                'payer_address': '41 rue des kangourous\n99999 Kangourou Ville',
                'payer_email': 'email1',
                'payer_phone': 'phone1',
                'payer_direct_debit': False,
                'event': {
                    'agenda': 'agenda',
                    'start_datetime': '2022-09-01T12:00:00+02:00',
                    'slug': 'event',
                    'label': 'Event',
                },
                'booking': {'foo': 'baz'},
                'status': 'success',
                'pool': corrective_pool2,
                'accounting_code': '414141',
            },
            payer_data_cache={
                'payer:1': {
                    'first_name': 'First1',
                    'last_name': 'Last1',
                    'address': '41 rue des kangourous\n99999 Kangourou Ville',
                    'email': 'email1',
                    'phone': 'phone1',
                    'direct_debit': False,
                }
            },
            previous_journal_line=previous_journal_line,
        )
    ]
    # not called, because it is not the first corrective campaign
    assert mock_check_amounts.call_args_list == []

    corrective_pool2.delete()
    corrective_campaign2.delete()
    mock_compare.reset_mock()

    # primary campaign already checked amounts, JournalLine with 'pricing-changed' values already exists
    JournalLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        quantity=1,
        amount=42,
        slug='xx',
        user_external_id='xx',
        payer_external_id='xx',
        pricing_data={
            'adjustment': {'before': 'F-0002', 'info': 'pricing-changed', 'reason': 'missing-cancellation'}
        },
        pool=primary_pool,
    )
    lines = utils.build_lines_for_user(
        agendas=[agenda],
        agendas_pricings=[pricing],
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
        pool=corrective_pool1,
        check_status_list=check_status_list,
        payer_data_cache=payer_data_cache,
    )
    assert lines == []  # result of compare_journal_lines
    assert mock_compare.call_args_list == [
        mock.call(
            request=mock.ANY,
            pool=corrective_pool1,
            pricing=pricing,
            check_types={},
            pricing_data={
                'accounting_code': '414141',
                'booking_details': {'status': 'not-booked'},
                'calculation_details': {'pricing': 0},
                'pricing': 0,
            },
            line_kwargs={
                'label': 'Event',
                'event_date': datetime.date(2022, 9, 1),
                'slug': 'agenda@event',
                'quantity': 1,
                'quantity_type': 'units',
                'user_external_id': 'user:1',
                'user_first_name': 'User1',
                'user_last_name': 'Name1',
                'payer_external_id': 'payer:1',
                'payer_first_name': 'First1',
                'payer_last_name': 'Last1',
                'payer_address': '41 rue des kangourous\n99999 Kangourou Ville',
                'payer_email': 'email1',
                'payer_phone': 'phone1',
                'payer_direct_debit': False,
                'event': {
                    'agenda': 'agenda',
                    'start_datetime': '2022-09-01T12:00:00+02:00',
                    'slug': 'event',
                    'label': 'Event',
                },
                'booking': {'foo': 'baz'},
                'status': 'success',
                'pool': corrective_pool1,
                'accounting_code': '414141',
            },
            payer_data_cache={
                'payer:1': {
                    'first_name': 'First1',
                    'last_name': 'Last1',
                    'address': '41 rue des kangourous\n99999 Kangourou Ville',
                    'email': 'email1',
                    'phone': 'phone1',
                    'direct_debit': False,
                }
            },
            previous_journal_line=previous_journal_line,
        )
    ]
    # not called, because amounts were checked by primary campaign
    assert mock_check_amounts.call_args_list == []


@mock.patch('lingo.invoicing.utils.get_existing_lines_for_user')
@mock.patch('lingo.invoicing.utils.get_previous_campaign_journal_lines_for_user')
@mock.patch('lingo.invoicing.utils.check_primary_campaign_amounts')
@mock.patch('lingo.invoicing.utils.compare_journal_lines')
@mock.patch('lingo.invoicing.models.Regie.get_payer_external_id')
@mock.patch('lingo.pricing.models.Pricing.get_pricing_data_for_event')
def test_build_lines_for_user_corrective_campaign(
    mock_pricing_data_event, mock_payer, mock_compare, mock_check_amounts, mock_previous, mock_existing
):
    regie = Regie.objects.create(label='Regie')
    agenda = Agenda.objects.create(label='Agenda')
    pricing = Pricing.objects.create(
        date_start=datetime.date(year=2022, month=9, day=1),
        date_end=datetime.date(year=2022, month=10, day=1),
    )
    pricing.agendas.add(agenda)
    primary_campaign = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_debit=datetime.date(2022, 11, 15),
        adjustment_campaign=True,
    )
    corrective_campaign1 = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_debit=datetime.date(2022, 11, 15),
        adjustment_campaign=True,
        primary_campaign=primary_campaign,
    )
    primary_pool = Pool.objects.create(
        campaign=primary_campaign,
        draft=False,
    )
    corrective_pool1 = Pool.objects.create(
        campaign=corrective_campaign1,
        draft=True,
    )
    previous_journal_line = JournalLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        quantity=1,
        amount=42,
        slug='agenda@event',
        user_external_id='user:1',
        payer_external_id='payer:1',
        pricing_data={
            'booking_details': {'status': 'presence'},
            'calculation_details': {'pricing': 42},
        },
        pool=primary_pool,
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
    mock_existing.return_value = 'existing'
    mock_previous.return_value = {
        'agenda@event': {
            '2022-09-01': previous_journal_line,
        },
    }
    check_status_list = [
        {
            'event': {
                'agenda': 'agenda',
                'start_datetime': '2022-09-01T12:00:00+02:00',
                'slug': 'event',
                'label': 'Event',
            },
            'check_status': {'foo': 'bar'},
            'booking': {'foo': 'baz'},
        },
    ]
    mock_pricing_data_event.return_value = {
        'accounting_code': '414141',
        'booking_details': {'status': 'not-booked'},
        'calculation_details': {'pricing': 0},
        'pricing': 0,
    }
    mock_compare.return_value = []
    mock_check_amounts.return_value = []

    lines = utils.build_lines_for_user(
        agendas=[agenda],
        agendas_pricings=[pricing],
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
        pool=corrective_pool1,
        check_status_list=check_status_list,
        payer_data_cache=payer_data_cache,
    )
    assert len(lines) == 0
    assert mock_compare.call_args_list == [
        mock.call(
            request=mock.ANY,
            pool=corrective_pool1,
            pricing=pricing,
            check_types={},
            pricing_data={
                'accounting_code': '414141',
                'booking_details': {'status': 'not-booked'},
                'calculation_details': {'pricing': 0},
                'pricing': 0,
            },
            line_kwargs={
                'label': 'Event',
                'event_date': datetime.date(2022, 9, 1),
                'slug': 'agenda@event',
                'quantity': 1,
                'quantity_type': 'units',
                'user_external_id': 'user:1',
                'user_first_name': 'User1',
                'user_last_name': 'Name1',
                'payer_external_id': 'payer:1',
                'payer_first_name': 'First1',
                'payer_last_name': 'Last1',
                'payer_address': '41 rue des kangourous\n99999 Kangourou Ville',
                'payer_email': '',
                'payer_phone': '',
                'payer_direct_debit': False,
                'event': {
                    'agenda': 'agenda',
                    'start_datetime': '2022-09-01T12:00:00+02:00',
                    'slug': 'event',
                    'label': 'Event',
                },
                'booking': {'foo': 'baz'},
                'status': 'success',
                'pool': corrective_pool1,
                'accounting_code': '414141',
            },
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
            previous_journal_line=previous_journal_line,
        )
    ]
    assert mock_check_amounts.call_args_list == [
        mock.call(
            request=mock.ANY,
            pool=corrective_pool1,
            check_types={},
            pricing=pricing,
            line_kwargs={
                'label': 'Event',
                'event_date': datetime.date(2022, 9, 1),
                'slug': 'agenda@event',
                'quantity': 1,
                'quantity_type': 'units',
                'user_external_id': 'user:1',
                'user_first_name': 'User1',
                'user_last_name': 'Name1',
                'payer_external_id': 'payer:1',
                'payer_first_name': 'First1',
                'payer_last_name': 'Last1',
                'payer_address': '41 rue des kangourous\n99999 Kangourou Ville',
                'payer_email': '',
                'payer_phone': '',
                'payer_direct_debit': False,
                'event': {
                    'agenda': 'agenda',
                    'start_datetime': '2022-09-01T12:00:00+02:00',
                    'slug': 'event',
                    'label': 'Event',
                },
                'booking': {'foo': 'baz'},
                'status': 'success',
                'pool': corrective_pool1,
                'accounting_code': '414141',
            },
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
            previous_journal_line=previous_journal_line,
            existing_lines='existing',
        )
    ]
