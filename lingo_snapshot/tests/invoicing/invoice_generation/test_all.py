import datetime
import decimal
from unittest import mock

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils.timezone import now

from lingo.agendas.chrono import ChronoError
from lingo.agendas.models import Agenda, CheckType, CheckTypeGroup
from lingo.invoicing import utils
from lingo.invoicing.errors import PayerDataError, PayerError, PoolPromotionError
from lingo.invoicing.models import (
    Campaign,
    CampaignAsyncJob,
    Counter,
    Credit,
    CreditLine,
    DraftInvoice,
    DraftInvoiceLine,
    DraftJournalLine,
    InjectedLine,
    Invoice,
    InvoiceLine,
    JournalLine,
    Pool,
    PoolAsyncJob,
    Regie,
)
from lingo.pricing.errors import PricingError
from lingo.pricing.models import Criteria, CriteriaCategory, Pricing

pytestmark = pytest.mark.django_db


def test_get_agendas():
    regie = Regie.objects.create(label='Regie')
    agenda1 = Agenda.objects.create(label='Agenda 1', regie=regie)
    agenda2 = Agenda.objects.create(label='Agenda 2', regie=regie)
    agenda3 = Agenda.objects.create(label='Agenda 3', regie=regie)
    agenda4 = Agenda.objects.create(label='Agenda 4', regie=regie)
    campaign = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_debit=datetime.date(2022, 11, 15),
    )
    campaign.agendas.add(agenda1, agenda2, agenda3)
    pool = Pool.objects.create(
        campaign=campaign,
        draft=True,
    )

    # no agenda pricing defined
    assert list(utils.get_agendas(pool=pool)) == []

    # agenda pricing, but for flat_fee_schedule
    pricing = Pricing.objects.create(
        date_start=datetime.date(year=2022, month=9, day=1),
        date_end=datetime.date(year=2022, month=10, day=1),
        flat_fee_schedule=True,  # wrong config
    )
    pricing.agendas.add(agenda1)
    assert list(utils.get_agendas(pool=pool)) == []

    # create some agenda pricing
    pricing1 = Pricing.objects.create(
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2021, month=10, day=1),
    )
    pricing2 = Pricing.objects.create(
        date_start=datetime.date(year=2022, month=9, day=1),
        date_end=datetime.date(year=2022, month=10, day=1),
    )
    assert list(utils.get_agendas(pool=pool)) == []

    # link agendas to agenda pricing
    pricing1.agendas.add(agenda1, agenda2, agenda4)
    pricing2.agendas.add(agenda3)

    assert list(utils.get_agendas(pool=pool)) == [agenda3]
    campaign.date_start = datetime.date(2021, 9, 1)
    campaign.date_end = datetime.date(2021, 10, 1)
    campaign.save()
    assert list(utils.get_agendas(pool=pool)) == [agenda1, agenda2]
    campaign.date_start = datetime.date(2022, 8, 31)
    campaign.date_end = datetime.date(2022, 9, 1)
    campaign.save()
    assert list(utils.get_agendas(pool=pool)) == []
    campaign.date_start = datetime.date(2022, 9, 1)
    campaign.date_end = datetime.date(2022, 9, 2)
    campaign.save()
    assert list(utils.get_agendas(pool=pool)) == [agenda3]
    campaign.date_start = datetime.date(2022, 9, 30)
    campaign.date_end = datetime.date(2022, 10, 1)
    campaign.save()
    assert list(utils.get_agendas(pool=pool)) == [agenda3]
    campaign.date_start = datetime.date(2022, 10, 1)
    campaign.date_end = datetime.date(2022, 10, 2)
    campaign.save()
    assert list(utils.get_agendas(pool=pool)) == []
    campaign.date_start = datetime.date(2021, 9, 15)
    campaign.date_end = datetime.date(2022, 9, 15)
    campaign.save()
    assert list(utils.get_agendas(pool=pool)) == [agenda1, agenda2, agenda3]


@mock.patch('lingo.invoicing.utils.get_subscriptions')
def test_get_users_from_subscriptions_error(mock_subscriptions):
    regie = Regie.objects.create(label='Regie')
    agenda = Agenda.objects.create(label='Agenda')
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
    mock_subscriptions.side_effect = ChronoError('foo baz')
    with pytest.raises(ChronoError):
        utils.get_users_from_subscriptions(agendas=[agenda], pool=pool)


@mock.patch('lingo.invoicing.utils.get_subscriptions')
def test_get_users_from_subscriptions(mock_subscriptions):
    regie = Regie.objects.create(label='Regie')
    agenda1 = Agenda.objects.create(label='Agenda 1')
    agenda2 = Agenda.objects.create(label='Agenda 2')
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

    # no agendas
    assert utils.get_users_from_subscriptions(agendas=[], pool=pool) == {}
    assert mock_subscriptions.call_args_list == []

    # no subscriptions
    mock_subscriptions.return_value = []
    assert utils.get_users_from_subscriptions(agendas=[agenda1, agenda2], pool=pool) == {}
    assert mock_subscriptions.call_args_list == [
        mock.call(
            agenda_slug='agenda-1', date_start=datetime.date(2022, 9, 1), date_end=datetime.date(2022, 10, 1)
        ),
        mock.call(
            agenda_slug='agenda-2', date_start=datetime.date(2022, 9, 1), date_end=datetime.date(2022, 10, 1)
        ),
    ]
    mock_subscriptions.reset_mock()

    # with subscriptions
    mock_subscriptions.side_effect = [
        [
            {
                'user_external_id': 'user:1',
                'user_first_name': 'User1',
                'user_last_name': 'Name1',
                'date_start': '2022-08-01',
                'date_end': '2022-09-02',
            },
            {
                'user_external_id': 'user:1',
                'user_first_name': 'Foo Bar',
                'user_last_name': '',
                'date_start': '2022-09-02',
                'date_end': '2022-09-03',
            },
            {
                'user_external_id': 'user:2',
                'user_first_name': '',
                'user_last_name': '',
                'date_start': '2022-09-02',
                'date_end': '2022-09-03',
            },
        ],
        [
            {
                'user_external_id': 'user:1',
                'user_first_name': 'User1 Name1',
                'user_last_name': '',
                'date_start': '2022-08-01',
                'date_end': '2022-10-01',
            },
        ],
    ]
    assert utils.get_users_from_subscriptions(agendas=[agenda1, agenda2], pool=pool) == {
        'user:1': ('User1', 'Name1'),
        'user:2': ('', ''),
    }
    assert mock_subscriptions.call_args_list == [
        mock.call(
            agenda_slug='agenda-1', date_start=datetime.date(2022, 9, 1), date_end=datetime.date(2022, 10, 1)
        ),
        mock.call(
            agenda_slug='agenda-2', date_start=datetime.date(2022, 9, 1), date_end=datetime.date(2022, 10, 1)
        ),
    ]


@mock.patch('lingo.invoicing.utils.get_check_status')
def test_get_lines_for_user_check_status_error(mock_status):
    regie = Regie.objects.create(label='Regie')
    agenda = Agenda.objects.create(label='Agenda')
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
    mock_status.side_effect = ChronoError('foo baz')
    with pytest.raises(ChronoError):
        utils.get_lines_for_user(
            agendas=[agenda],
            agendas_pricings=[],
            user_external_id='user:1',
            user_first_name='User1',
            user_last_name='Name1',
            pool=pool,
            payer_data_cache={},
        )


@mock.patch('lingo.invoicing.utils.get_check_status')
@mock.patch('lingo.invoicing.utils.build_lines_for_user')
def test_get_lines_for_user_build_lines(mock_lines, mock_status):
    regie = Regie.objects.create(label='Regie')
    agenda1 = Agenda.objects.create(label='Agenda1')
    agenda2 = Agenda.objects.create(label='Agenda2')
    Agenda.objects.create(label='Agenda3')
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
    mock_status.return_value = 'foobar'
    utils.get_lines_for_user(
        agendas=[agenda1, agenda2],
        agendas_pricings='agendas_pricings',
        user_external_id='user_external_id',
        user_first_name='user_first_name',
        user_last_name='user_last_name',
        pool=pool,
        payer_data_cache='payer_data_cache',
        request='request',
    )
    assert mock_lines.call_args_list == [
        mock.call(
            agendas=[agenda1, agenda2],
            agendas_pricings='agendas_pricings',
            user_external_id='user_external_id',
            user_first_name='user_first_name',
            user_last_name='user_last_name',
            pool=pool,
            payer_data_cache='payer_data_cache',
            request='request',
            check_status_list='foobar',
        )
    ]


@pytest.mark.parametrize('injected_lines', ['no', 'period', 'all'])
@mock.patch('lingo.invoicing.models.Regie.get_payer_external_id')
@mock.patch('lingo.pricing.models.Pricing.get_pricing_data_for_event')
def test_build_lines_for_user_check_status(mock_pricing_data_event, mock_payer, injected_lines):
    regie = Regie.objects.create(label='Regie')
    other_regie = Regie.objects.create(label='Other Regie')
    agenda1 = Agenda.objects.create(label='Agenda 1')
    agenda2 = Agenda.objects.create(label='Agenda 2')
    pricing = Pricing.objects.create(
        date_start=datetime.date(year=2022, month=9, day=1),
        date_end=datetime.date(year=2022, month=10, day=1),
    )
    pricing.agendas.add(agenda1, agenda2)
    campaign = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_debit=datetime.date(2022, 11, 15),
        injected_lines=injected_lines,
    )
    old_pool = Pool.objects.create(
        campaign=campaign,
        draft=True,
    )
    pool = Pool.objects.create(
        campaign=campaign,
        draft=True,
    )
    other_campaign = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_debit=datetime.date(2022, 11, 15),
    )
    other_pool = Pool.objects.create(
        campaign=other_campaign,
        draft=True,
    )

    # create some injected lines
    injected_line1 = InjectedLine.objects.create(
        event_date=datetime.date(2022, 8, 31),  # before the period
        slug='event-2022-08-31',
        label='Event 2022-08-31',
        amount=3,
        user_external_id='user:1',
        payer_external_id='payer:1',
        regie=regie,
    )
    injected_line2 = InjectedLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        slug='event-2022-09-01',
        label='Event 2022-09-01',
        amount=-3,
        user_external_id='user:1',
        payer_external_id='payer:1',
        regie=regie,
    )
    # ok, same campaign
    DraftJournalLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        amount=-3,
        pool=old_pool,
        from_injected_line=injected_line2,
    )
    InjectedLine.objects.create(
        event_date=datetime.date(2022, 9, 2),
        slug='event-2022-09-02',
        label='Event 2022-09-02',
        amount=3,
        user_external_id='user:2',  # wrong user
        payer_external_id='payer:1',
        regie=regie,
    )
    injected_line4 = InjectedLine.objects.create(
        event_date=datetime.date(2022, 9, 30),
        slug='event-2022-09-30',
        label='Event 2022-09-30',
        amount=4.5,
        user_external_id='user:1',
        payer_external_id='payer:1',
        regie=regie,
    )
    InjectedLine.objects.create(
        event_date=datetime.date(2022, 10, 1),  # too late
        slug='event-2022-10-01',
        label='Event 2022-10-01',
        amount=3,
        user_external_id='user:1',
        payer_external_id='payer:1',
        regie=regie,
    )
    injected_line6 = InjectedLine.objects.create(
        event_date=datetime.date(2022, 9, 15),
        slug='event-2022-09-15',
        label='Event 2022-09-15',
        amount=4.5,
        user_external_id='user:1',
        payer_external_id='payer:1',
        regie=regie,
    )
    # nok, already invoiced
    JournalLine.objects.create(
        event_date=datetime.date(2022, 9, 15),
        amount=4.5,
        pool=old_pool,
        from_injected_line=injected_line6,
    )
    injected_line7 = InjectedLine.objects.create(
        event_date=datetime.date(2022, 9, 16),
        slug='event-2022-09-15',
        label='Event 2022-09-15',
        amount=4.5,
        user_external_id='user:1',
        payer_external_id='payer:1',
        regie=regie,
    )
    # nok, other campaign
    DraftJournalLine.objects.create(
        event_date=datetime.date(2022, 9, 16),
        amount=4.5,
        pool=other_pool,
        from_injected_line=injected_line7,
    )
    # nok, other regie
    InjectedLine.objects.create(
        event_date=datetime.date(2022, 9, 15),
        slug='event-2022-09-15',
        label='Event 2022-09-15',
        amount=4.5,
        user_external_id='user:1',
        payer_external_id='payer:1',
        regie=other_regie,
    )

    # no agendas
    assert (
        utils.build_lines_for_user(
            agendas=[],
            agendas_pricings=[pricing],
            user_external_id='user:1',
            user_first_name='User1',
            user_last_name='Name1',
            pool=pool,
            payer_data_cache={},
            check_status_list=[],
        )
        == []
    )
    assert mock_payer.call_args_list == []
    assert mock_pricing_data_event.call_args_list == []

    # no status
    lines = utils.build_lines_for_user(
        agendas=[agenda1, agenda2],
        agendas_pricings=[pricing],
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
        pool=pool,
        payer_data_cache={},
        check_status_list=[],
    )
    # refresh total_amount field (triggered)
    lines = DraftJournalLine.objects.filter(pk__in=[li.pk for li in lines]).order_by('pk')
    if injected_lines == 'no':
        assert len(lines) == 0
    elif injected_lines == 'period':
        assert len(lines) == 2  # injected lines
    else:
        assert len(lines) == 3  # injected lines
    assert mock_payer.call_args_list == []
    assert mock_pricing_data_event.call_args_list == []

    # correct data
    mock_pricing_data_event.side_effect = [
        {'foo1': 'bar1', 'pricing': 1, 'accounting_code': '414141'},
        {'foo2': 'bar2', 'pricing': 2, 'accounting_code': '424242'},
        {'foo3': 'bar3', 'pricing': 3, 'accounting_code': '434343'},
        {'foo4': 'bar4', 'pricing': -4, 'accounting_code': '444444'},
    ]
    mock_payer.return_value = 'payer:1'
    check_status_list = [
        # many events for agenda-1
        {
            'event': {
                'agenda': 'agenda-1',
                'start_datetime': '2022-09-01T12:00:00+02:00',
                'slug': 'event-1',
                'label': 'Event 1',
            },
            'check_status': {'foo': 'bar1'},
            'booking': {'foo': 'baz1'},
        },
        {
            'event': {
                'agenda': 'agenda-1',
                'start_datetime': '2022-09-02T12:00:00+02:00',
                'slug': 'event-2',
                'label': 'Event 2',
            },
            'check_status': {'foo': 'bar2'},
            'booking': {'foo': 'baz2'},
        },
        # and for agenda-2
        {
            'event': {
                'agenda': 'agenda-2',
                'start_datetime': '2022-09-01T13:00:00+02:00',
                'slug': 'eveeent-1',
                'label': 'Eveeent 1',
            },
            'check_status': {'foo': 'barrr1'},
            'booking': {'foo': 'bazzz1'},
        },
        {
            'event': {
                'agenda': 'agenda-2',
                'start_datetime': '2022-09-02T13:00:00+02:00',
                'slug': 'eveeent-2',
                'label': 'Eveeent 2',
            },
            'check_status': {'foo': 'barrr2'},
            'booking': {'foo': 'bazzz2'},
        },
    ]
    lines = utils.build_lines_for_user(
        agendas=[agenda1, agenda2],
        agendas_pricings=[pricing],
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
        pool=pool,
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
        check_status_list=check_status_list,
    )
    # refresh total_amount field (triggered)
    lines = DraftJournalLine.objects.filter(pk__in=[li.pk for li in lines]).order_by('pk')
    assert mock_pricing_data_event.call_args_list == [
        mock.call(
            request=mock.ANY,
            agenda=agenda1,
            event={
                'agenda': 'agenda-1',
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
            agenda=agenda1,
            event={
                'agenda': 'agenda-1',
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
            agenda=agenda2,
            event={
                'agenda': 'agenda-2',
                'start_datetime': '2022-09-01T13:00:00+02:00',
                'slug': 'eveeent-1',
                'label': 'Eveeent 1',
            },
            check_status={'foo': 'barrr1'},
            user_external_id='user:1',
            payer_external_id='payer:1',
        ),
        mock.call(
            request=mock.ANY,
            agenda=agenda2,
            event={
                'agenda': 'agenda-2',
                'start_datetime': '2022-09-02T13:00:00+02:00',
                'slug': 'eveeent-2',
                'label': 'Eveeent 2',
            },
            check_status={'foo': 'barrr2'},
            user_external_id='user:1',
            payer_external_id='payer:1',
        ),
    ]
    if injected_lines == 'no':
        assert len(lines) == 4
        line1, line2, line3, line4 = lines
    elif injected_lines == 'period':
        assert len(lines) == 6
        line1, line2, line3, line4, line6, line7 = lines
    else:
        assert len(lines) == 7
        line1, line2, line3, line4, line5, line6, line7 = lines
    assert line1.event_date == datetime.date(2022, 9, 1)
    assert line1.slug == 'agenda-1@event-1'
    assert line1.label == 'Event 1'
    assert line1.amount == 1
    assert line1.quantity == 1
    assert line1.quantity_type == 'units'
    assert line1.user_external_id == 'user:1'
    assert line1.user_first_name == 'User1'
    assert line1.user_last_name == 'Name1'
    assert line1.payer_external_id == 'payer:1'
    assert line1.payer_first_name == 'First1'
    assert line1.payer_last_name == 'Last1'
    assert line1.payer_address == '41 rue des kangourous\n99999 Kangourou Ville'
    assert line1.payer_direct_debit is False
    assert line1.event == {
        'agenda': 'agenda-1',
        'start_datetime': '2022-09-01T12:00:00+02:00',
        'slug': 'event-1',
        'label': 'Event 1',
    }
    assert line1.booking == {'foo': 'baz1'}
    assert line1.pricing_data == {'foo1': 'bar1', 'pricing': 1, 'accounting_code': '414141'}
    assert line1.accounting_code == '414141'
    assert line1.status == 'success'
    assert line1.pool == pool
    assert line1.from_injected_line is None
    assert line2.event_date == datetime.date(2022, 9, 2)
    assert line2.slug == 'agenda-1@event-2'
    assert line2.label == 'Event 2'
    assert line2.amount == 2
    assert line2.quantity == 1
    assert line2.quantity_type == 'units'
    assert line2.user_external_id == 'user:1'
    assert line2.user_first_name == 'User1'
    assert line2.user_last_name == 'Name1'
    assert line2.payer_external_id == 'payer:1'
    assert line2.payer_first_name == 'First1'
    assert line2.payer_last_name == 'Last1'
    assert line2.payer_address == '41 rue des kangourous\n99999 Kangourou Ville'
    assert line2.payer_direct_debit is False
    assert line2.event == {
        'agenda': 'agenda-1',
        'start_datetime': '2022-09-02T12:00:00+02:00',
        'slug': 'event-2',
        'label': 'Event 2',
    }
    assert line2.booking == {'foo': 'baz2'}
    assert line2.pricing_data == {'foo2': 'bar2', 'pricing': 2, 'accounting_code': '424242'}
    assert line2.accounting_code == '424242'
    assert line2.status == 'success'
    assert line2.pool == pool
    assert line2.from_injected_line is None
    assert line3.event_date == datetime.date(2022, 9, 1)
    assert line3.slug == 'agenda-2@eveeent-1'
    assert line3.label == 'Eveeent 1'
    assert line3.amount == 3
    assert line3.quantity == 1
    assert line3.quantity_type == 'units'
    assert line3.user_external_id == 'user:1'
    assert line3.user_first_name == 'User1'
    assert line3.user_last_name == 'Name1'
    assert line3.payer_external_id == 'payer:1'
    assert line3.payer_first_name == 'First1'
    assert line3.payer_last_name == 'Last1'
    assert line3.payer_address == '41 rue des kangourous\n99999 Kangourou Ville'
    assert line3.payer_direct_debit is False
    assert line3.event == {
        'agenda': 'agenda-2',
        'start_datetime': '2022-09-01T13:00:00+02:00',
        'slug': 'eveeent-1',
        'label': 'Eveeent 1',
    }
    assert line3.booking == {'foo': 'bazzz1'}
    assert line3.pricing_data == {'foo3': 'bar3', 'pricing': 3, 'accounting_code': '434343'}
    assert line3.accounting_code == '434343'
    assert line3.status == 'success'
    assert line3.pool == pool
    assert line3.from_injected_line is None
    assert line4.event_date == datetime.date(2022, 9, 2)
    assert line4.slug == 'agenda-2@eveeent-2'
    assert line4.label == 'Eveeent 2'
    assert line4.amount == 4
    assert line4.quantity == -1
    assert line4.quantity_type == 'units'
    assert line4.user_external_id == 'user:1'
    assert line4.user_first_name == 'User1'
    assert line4.user_last_name == 'Name1'
    assert line4.payer_external_id == 'payer:1'
    assert line4.payer_first_name == 'First1'
    assert line4.payer_last_name == 'Last1'
    assert line4.payer_address == '41 rue des kangourous\n99999 Kangourou Ville'
    assert line4.payer_direct_debit is False
    assert line4.event == {
        'agenda': 'agenda-2',
        'start_datetime': '2022-09-02T13:00:00+02:00',
        'slug': 'eveeent-2',
        'label': 'Eveeent 2',
    }
    assert line4.booking == {'foo': 'bazzz2'}
    assert line4.pricing_data == {'foo4': 'bar4', 'pricing': -4, 'accounting_code': '444444'}
    assert line4.accounting_code == '444444'
    assert line4.status == 'success'
    assert line4.pool == pool
    assert line4.from_injected_line is None
    if injected_lines != 'no':
        if injected_lines == 'all':
            assert line5.event_date == injected_line1.event_date
            assert line5.slug == 'event-2022-08-31'
            assert line5.label == 'Event 2022-08-31'
            assert line5.amount == 3
            assert line5.quantity == 1
            assert line5.quantity_type == 'units'
            assert line5.user_external_id == 'user:1'
            assert line5.user_first_name == 'User1'
            assert line5.user_last_name == 'Name1'
            assert line5.payer_external_id == 'payer:1'
            assert line5.payer_first_name == 'First1'
            assert line5.payer_last_name == 'Last1'
            assert line5.payer_address == '41 rue des kangourous\n99999 Kangourou Ville'
            assert line5.payer_direct_debit is False
            assert line5.event == {}
            assert line5.booking == {}
            assert line5.pricing_data == {}
            assert line5.accounting_code == ''
            assert line5.status == 'success'
            assert line5.pool == pool
            assert line5.from_injected_line == injected_line1
        assert line6.event_date == injected_line2.event_date
        assert line6.slug == 'event-2022-09-01'
        assert line6.label == 'Event 2022-09-01'
        assert line6.amount == -3
        assert line6.quantity == 1
        assert line6.quantity_type == 'units'
        assert line6.user_external_id == 'user:1'
        assert line6.user_first_name == 'User1'
        assert line6.user_last_name == 'Name1'
        assert line6.payer_external_id == 'payer:1'
        assert line6.payer_first_name == 'First1'
        assert line6.payer_last_name == 'Last1'
        assert line6.payer_address == '41 rue des kangourous\n99999 Kangourou Ville'
        assert line6.payer_direct_debit is False
        assert line6.event == {}
        assert line6.booking == {}
        assert line6.pricing_data == {}
        assert line6.accounting_code == ''
        assert line6.status == 'success'
        assert line6.pool == pool
        assert line6.from_injected_line == injected_line2
        assert line7.event_date == injected_line4.event_date
        assert line7.slug == 'event-2022-09-30'
        assert line7.label == 'Event 2022-09-30'
        assert line7.amount == 4.5
        assert line7.quantity == 1
        assert line7.quantity_type == 'units'
        assert line7.user_external_id == 'user:1'
        assert line7.user_first_name == 'User1'
        assert line7.user_last_name == 'Name1'
        assert line7.payer_external_id == 'payer:1'
        assert line7.payer_first_name == 'First1'
        assert line7.payer_last_name == 'Last1'
        assert line7.payer_address == '41 rue des kangourous\n99999 Kangourou Ville'
        assert line7.payer_direct_debit is False
        assert line7.event == {}
        assert line7.booking == {}
        assert line7.pricing_data == {}
        assert line7.accounting_code == ''
        assert line7.status == 'success'
        assert line7.pool == pool
        assert line7.from_injected_line == injected_line4


@mock.patch('lingo.invoicing.models.Regie.get_payer_external_id')
@mock.patch('lingo.pricing.models.Pricing.get_pricing_data_for_event')
def test_build_lines_for_user_check_status_partial_bookings(mock_pricing_data_event, mock_payer):
    regie = Regie.objects.create(label='Regie')
    agenda = Agenda.objects.create(label='Agenda', partial_bookings=True)
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
        adjustment_campaign=True,  # ignored for partial bookings
    )
    pool = Pool.objects.create(
        campaign=campaign,
        draft=True,
    )

    mock_pricing_data_event.side_effect = [
        {'foo1': 'bar1', 'pricing': 1, 'accounting_code': '414141'},
        {'foo2': 'bar2', 'pricing': 2, 'accounting_code': '424242'},
        {'foo3': 'bar3', 'pricing': 3, 'accounting_code': '434343'},
    ]
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
        {
            'event': {
                'agenda': 'agenda',
                'start_datetime': '2022-09-02T12:00:00+02:00',
                'slug': 'event-2',
                'label': 'Event 2',
            },
            'check_status': {'foo': 'bar2'},
            'booking': {'foo': 'baz2', 'computed_duration': 45},
        },
        {
            'event': {
                'agenda': 'agenda',
                'start_datetime': '2022-09-02T12:00:00+02:00',
                'slug': 'event-2',
                'label': 'Event 2',
            },
            'check_status': {'foo': 'bar3'},
            'booking': {'foo': 'baz3', 'computed_duration': 70},
        },
    ]
    lines = utils.build_lines_for_user(
        agendas=[agenda],
        agendas_pricings=[pricing],
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
        pool=pool,
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
        check_status_list=check_status_list,
    )
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
    assert len(lines) == 3
    assert lines[0].event_date == datetime.date(2022, 9, 1)
    assert lines[0].slug == 'agenda@event-1'
    assert lines[0].label == 'Event 1'
    assert lines[0].description == ''
    assert lines[0].amount == 1
    assert lines[0].quantity == 0
    assert lines[0].quantity_type == 'minutes'
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
    assert lines[0].pricing_data == {'foo1': 'bar1', 'pricing': 1, 'accounting_code': '414141'}
    assert lines[0].accounting_code == '414141'
    assert lines[0].status == 'success'
    assert lines[0].pool == pool
    assert lines[0].from_injected_line is None
    assert lines[1].event_date == datetime.date(2022, 9, 2)
    assert lines[1].slug == 'agenda@event-2'
    assert lines[1].label == 'Event 2'
    assert lines[1].description == ''
    assert lines[1].amount == 2
    assert lines[1].quantity == 45
    assert lines[1].quantity_type == 'minutes'
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
        'start_datetime': '2022-09-02T12:00:00+02:00',
        'slug': 'event-2',
        'label': 'Event 2',
    }
    assert lines[1].booking == {'foo': 'baz2', 'computed_duration': 45}
    assert lines[1].pricing_data == {'foo2': 'bar2', 'pricing': 2, 'accounting_code': '424242'}
    assert lines[1].accounting_code == '424242'
    assert lines[1].status == 'success'
    assert lines[1].pool == pool
    assert lines[1].from_injected_line is None
    assert lines[2].event_date == datetime.date(2022, 9, 2)
    assert lines[2].slug == 'agenda@event-2'
    assert lines[2].label == 'Event 2'
    assert lines[2].description == ''
    assert lines[2].amount == 3
    assert lines[2].quantity == 70
    assert lines[2].quantity_type == 'minutes'
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
    assert lines[2].booking == {'foo': 'baz3', 'computed_duration': 70}
    assert lines[2].pricing_data == {'foo3': 'bar3', 'pricing': 3, 'accounting_code': '434343'}
    assert lines[2].accounting_code == '434343'
    assert lines[2].status == 'success'
    assert lines[2].pool == pool
    assert lines[2].from_injected_line is None


@mock.patch('lingo.invoicing.models.Regie.get_payer_external_id')
@mock.patch('lingo.pricing.models.Pricing.get_pricing_data_for_event')
def test_build_lines_for_user_check_status_partial_bookings_without_booking(
    mock_pricing_data_event, mock_payer
):
    regie = Regie.objects.create(label='Regie')
    agenda = Agenda.objects.create(label='Agenda', partial_bookings=True)
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
        adjustment_campaign=True,  # ignored for partial bookings
    )
    pool = Pool.objects.create(
        campaign=campaign,
        draft=True,
    )

    # presence but no booking, and no check_type
    mock_pricing_data_event.side_effect = [
        {
            'foo1': 'bar1',
            'pricing': 1,
            'booking_details': {'status': 'presence'},
            'accounting_code': '414141',
        },
    ]
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
            # no adjusted_duration in booking => means no booking on chrono side
            'booking': {'foo': 'baz1', 'computed_duration': 120},
        },
    ]

    lines = utils.build_lines_for_user(
        agendas=[agenda],
        agendas_pricings=[pricing],
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
        pool=pool,
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
        check_status_list=check_status_list,
    )
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
    ]
    assert len(lines) == 1
    assert lines[0].event_date == datetime.date(2022, 9, 1)
    assert lines[0].slug == 'agenda@event-1'
    assert lines[0].label == 'Presence without booking'
    assert lines[0].description == ''
    assert lines[0].amount == 1
    assert lines[0].quantity == 120
    assert lines[0].quantity_type == 'minutes'
    assert lines[0].booking == {'foo': 'baz1', 'computed_duration': 120}
    assert lines[0].pricing_data == {
        'foo1': 'bar1',
        'pricing': 1,
        'booking_details': {'status': 'presence'},
        'accounting_code': '414141',
    }
    assert lines[0].accounting_code == '414141'
    assert lines[0].status == 'success'

    # presence but no booking, and check_type
    group = CheckTypeGroup.objects.create(label='foobar')
    CheckType.objects.create(label='Foo!', group=group, kind='presence')
    mock_pricing_data_event.side_effect = [
        {
            'foo1': 'bar1',
            'pricing': 2,
            'booking_details': {'status': 'presence', 'check_type': 'foo', 'check_type_group': 'foobar'},
            'accounting_code': '414141',
        },
        {
            'foo1': 'bar1',
            'pricing': 1,
            'booking_details': {'status': 'presence'},
            'accounting_code': '414141',
        },  # second call to get normal pricing
    ]
    mock_pricing_data_event.reset_mock()

    lines = utils.build_lines_for_user(
        agendas=[agenda],
        agendas_pricings=[pricing],
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
        pool=pool,
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
        check_status_list=check_status_list,
    )
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
        # second call to get normal pricing
        mock.call(
            request=mock.ANY,
            agenda=agenda,
            event={
                'agenda': 'agenda',
                'start_datetime': '2022-09-01T12:00:00+02:00',
                'slug': 'event-1',
                'label': 'Event 1',
            },
            check_status={'status': 'presence', 'check_type': None},
            user_external_id='user:1',
            payer_external_id='payer:1',
        ),
    ]
    assert len(lines) == 1
    assert lines[0].event_date == datetime.date(2022, 9, 1)
    assert lines[0].slug == 'agenda@event-1'
    assert lines[0].label == 'Foo!'
    assert lines[0].description == ''
    assert lines[0].amount == 2
    assert lines[0].quantity == 120
    assert lines[0].quantity_type == 'minutes'
    assert lines[0].booking == {'foo': 'baz1', 'computed_duration': 120}
    assert lines[0].pricing_data == {
        'foo1': 'bar1',
        'pricing': 2,
        'booking_details': {'status': 'presence', 'check_type': 'foo', 'check_type_group': 'foobar'},
        'accounting_code': '414141',
    }
    assert lines[0].accounting_code == '414141'
    assert lines[0].status == 'success'


@mock.patch('lingo.invoicing.models.Regie.get_payer_external_id')
@mock.patch('lingo.pricing.models.Pricing.get_pricing_data_for_event')
def test_build_lines_for_user_check_status_partial_bookings_with_booking(mock_pricing_data_event, mock_payer):
    regie = Regie.objects.create(label='Regie')
    agenda = Agenda.objects.create(label='Agenda', partial_bookings=True)
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
        adjustment_campaign=True,  # ignored for partial bookings
    )
    pool = Pool.objects.create(
        campaign=campaign,
        draft=True,
    )

    # presence, and no check_type, no overtaking
    mock_pricing_data_event.side_effect = [
        {
            'foo1': 'bar1',
            'pricing': 1,
            'booking_details': {'status': 'presence'},
            'accounting_code': '414141',
        },
    ]
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
            'booking': {'foo': 'baz1', 'computed_duration': 120, 'adjusted_duration': 120},
        },
    ]

    lines = utils.build_lines_for_user(
        agendas=[agenda],
        agendas_pricings=[pricing],
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
        pool=pool,
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
        check_status_list=check_status_list,
    )
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
    ]
    assert len(lines) == 1
    assert lines[0].event_date == datetime.date(2022, 9, 1)
    assert lines[0].slug == 'agenda@event-1'
    assert lines[0].label == 'Agenda'
    assert lines[0].description == '@booked-hours@'
    assert lines[0].amount == 1
    assert lines[0].quantity == 120
    assert lines[0].quantity_type == 'minutes'
    assert lines[0].booking == {'foo': 'baz1', 'computed_duration': 120, 'adjusted_duration': 120}
    assert lines[0].pricing_data == {
        'foo1': 'bar1',
        'pricing': 1,
        'booking_details': {'status': 'presence'},
        'accounting_code': '414141',
    }
    assert lines[0].accounting_code == '414141'
    assert lines[0].status == 'success'

    # presence, and no check_type, with overtaking
    mock_pricing_data_event.side_effect = [
        {
            'foo1': 'bar1',
            'pricing': 1,
            'booking_details': {'status': 'presence'},
            'accounting_code': '414141',
        },
    ]
    mock_pricing_data_event.reset_mock()
    check_status_list = [
        {
            'event': {
                'agenda': 'agenda',
                'start_datetime': '2022-09-01T12:00:00+02:00',
                'slug': 'event-1',
                'label': 'Event 1',
                'primary_event': 'event-1',
            },
            'check_status': {'foo': 'bar1'},
            'booking': {'foo': 'baz1', 'computed_duration': 120, 'adjusted_duration': 90},
        },
    ]

    lines = utils.build_lines_for_user(
        agendas=[agenda],
        agendas_pricings=[pricing],
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
        pool=pool,
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
        check_status_list=check_status_list,
    )
    assert mock_pricing_data_event.call_args_list == [
        mock.call(
            request=mock.ANY,
            agenda=agenda,
            event={
                'agenda': 'agenda',
                'start_datetime': '2022-09-01T12:00:00+02:00',
                'slug': 'event-1',
                'label': 'Event 1',
                'primary_event': 'event-1',
            },
            check_status={'foo': 'bar1'},
            user_external_id='user:1',
            payer_external_id='payer:1',
        ),
    ]
    assert len(lines) == 2
    assert lines[0].event_date == datetime.date(2022, 9, 1)
    assert lines[0].slug == 'agenda@event-1'
    assert lines[0].label == 'Agenda'
    assert lines[0].description == '@booked-hours@'
    assert lines[0].amount == 1
    assert lines[0].quantity == 90
    assert lines[0].quantity_type == 'minutes'
    assert lines[0].event['primary_event'] == 'event-1'
    assert lines[0].booking == {'foo': 'baz1', 'computed_duration': 120, 'adjusted_duration': 90}
    assert lines[0].pricing_data == {
        'foo1': 'bar1',
        'pricing': 1,
        'booking_details': {'status': 'presence'},
        'accounting_code': '414141',
    }
    assert lines[0].accounting_code == '414141'
    assert lines[0].status == 'success'
    assert lines[1].event_date == datetime.date(2022, 9, 1)
    assert lines[1].slug == 'agenda@event-1'
    assert lines[1].label == 'Overtaking'
    assert lines[1].description == '@overtaking@'
    assert lines[1].amount == 1
    assert lines[1].quantity == 30
    assert lines[1].quantity_type == 'minutes'
    assert lines[1].event['primary_event'] == 'event-1::overtaking'
    assert lines[1].booking == {'foo': 'baz1', 'computed_duration': 120, 'adjusted_duration': 90}
    assert lines[1].pricing_data == {
        'foo1': 'bar1',
        'pricing': 1,
        'booking_details': {'status': 'presence'},
        'accounting_code': '414141',
    }
    assert lines[1].accounting_code == '414141'
    assert lines[1].status == 'success'

    # absence, and no check_type
    mock_pricing_data_event.side_effect = [
        {
            'foo1': 'bar1',
            'pricing': 0,
            'booking_details': {'status': 'absence'},
            'accounting_code': '414141',
        },
        {
            'foo1': 'bar1',
            'pricing': 1,
            'booking_details': {'status': 'presence'},
            'accounting_code': '414141',
        },  # second call to get normal pricing
    ]
    mock_pricing_data_event.reset_mock()
    check_status_list = [
        {
            'event': {
                'agenda': 'agenda',
                'start_datetime': '2022-09-01T12:00:00+02:00',
                'slug': 'event-1',
                'label': 'Event 1',
                'primary_event': 'event-1',
            },
            'check_status': {'foo': 'bar1'},
            'booking': {'foo': 'baz1', 'computed_duration': 120, 'adjusted_duration': 120},
        },
    ]

    lines = utils.build_lines_for_user(
        agendas=[agenda],
        agendas_pricings=[pricing],
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
        pool=pool,
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
        check_status_list=check_status_list,
    )
    assert mock_pricing_data_event.call_args_list == [
        mock.call(
            request=mock.ANY,
            agenda=agenda,
            event={
                'agenda': 'agenda',
                'start_datetime': '2022-09-01T12:00:00+02:00',
                'slug': 'event-1',
                'label': 'Event 1',
                'primary_event': 'event-1',
            },
            check_status={'foo': 'bar1'},
            user_external_id='user:1',
            payer_external_id='payer:1',
        ),
        # second call to get normal pricing
        mock.call(
            request=mock.ANY,
            agenda=agenda,
            event={
                'agenda': 'agenda',
                'start_datetime': '2022-09-01T12:00:00+02:00',
                'slug': 'event-1',
                'label': 'Event 1',
                'primary_event': 'event-1',
            },
            check_status={'status': 'presence', 'check_type': None},
            user_external_id='user:1',
            payer_external_id='payer:1',
        ),
    ]
    assert len(lines) == 2
    assert lines[0].event_date == datetime.date(2022, 9, 1)
    assert lines[0].slug == 'agenda@event-1'
    assert lines[0].label == 'Agenda'
    assert lines[0].description == '@booked-hours@'
    assert lines[0].amount == 1
    assert lines[0].quantity == 120
    assert lines[0].quantity_type == 'minutes'
    assert lines[0].event['primary_event'] == 'event-1'
    assert lines[0].booking == {'foo': 'baz1', 'computed_duration': 120, 'adjusted_duration': 120}
    assert lines[0].pricing_data == {
        'foo1': 'bar1',
        'pricing': 1,
        'booking_details': {'status': 'presence'},
        'accounting_code': '414141',
    }
    assert lines[0].accounting_code == '414141'
    assert lines[0].status == 'success'
    assert lines[1].event_date == datetime.date(2022, 9, 1)
    assert lines[1].slug == 'agenda@event-1'
    assert lines[1].label == 'Absence'
    assert lines[1].description == ''
    assert lines[1].amount == 1
    assert lines[1].quantity == -120
    assert lines[1].quantity_type == 'minutes'
    assert lines[1].event['primary_event'] == 'event-1:absence:'
    assert lines[1].booking == {'foo': 'baz1', 'computed_duration': 120, 'adjusted_duration': 120}
    assert lines[1].pricing_data == {
        'foo1': 'bar1',
        'pricing': 0,
        'booking_details': {'status': 'absence'},
        'accounting_code': '414141',
    }
    assert lines[1].accounting_code == '414141'
    assert lines[1].status == 'success'

    # presence, and check_type, with overtaking
    group = CheckTypeGroup.objects.create(label='foobar')
    CheckType.objects.create(label='Foo!', group=group, kind='presence')
    mock_pricing_data_event.side_effect = [
        {
            'foo1': 'bar1',
            'pricing': 1.5,
            'booking_details': {'status': 'presence', 'check_type': 'foo', 'check_type_group': 'foobar'},
            'accounting_code': '414141',
        },
        {
            'foo1': 'bar1',
            'pricing': 1,
            'booking_details': {'status': 'presence'},
            'accounting_code': '414141',
        },  # second call to get normal pricing
    ]
    mock_pricing_data_event.reset_mock()
    check_status_list = [
        {
            'event': {
                'agenda': 'agenda',
                'start_datetime': '2022-09-01T12:00:00+02:00',
                'slug': 'event-1',
                'label': 'Event 1',
                'primary_event': 'event-1',
            },
            'check_status': {'foo': 'bar1'},
            'booking': {'foo': 'baz1', 'computed_duration': 120, 'adjusted_duration': 90},
        },
    ]

    lines = utils.build_lines_for_user(
        agendas=[agenda],
        agendas_pricings=[pricing],
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
        pool=pool,
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
        check_status_list=check_status_list,
    )
    assert mock_pricing_data_event.call_args_list == [
        mock.call(
            request=mock.ANY,
            agenda=agenda,
            event={
                'agenda': 'agenda',
                'start_datetime': '2022-09-01T12:00:00+02:00',
                'slug': 'event-1',
                'label': 'Event 1',
                'primary_event': 'event-1',
            },
            check_status={'foo': 'bar1'},
            user_external_id='user:1',
            payer_external_id='payer:1',
        ),
        # second call to get normal pricing
        mock.call(
            request=mock.ANY,
            agenda=agenda,
            event={
                'agenda': 'agenda',
                'start_datetime': '2022-09-01T12:00:00+02:00',
                'slug': 'event-1',
                'label': 'Event 1',
                'primary_event': 'event-1',
            },
            check_status={'status': 'presence', 'check_type': None},
            user_external_id='user:1',
            payer_external_id='payer:1',
        ),
    ]
    assert len(lines) == 3
    assert lines[0].event_date == datetime.date(2022, 9, 1)
    assert lines[0].slug == 'agenda@event-1'
    assert lines[0].label == 'Agenda'
    assert lines[0].description == '@booked-hours@'
    assert lines[0].amount == 1
    assert lines[0].quantity == 90
    assert lines[0].quantity_type == 'minutes'
    assert lines[0].event['primary_event'] == 'event-1'
    assert lines[0].booking == {'foo': 'baz1', 'computed_duration': 120, 'adjusted_duration': 90}
    assert lines[0].pricing_data == {
        'foo1': 'bar1',
        'pricing': 1,
        'booking_details': {'status': 'presence'},
        'accounting_code': '414141',
    }
    assert lines[0].accounting_code == '414141'
    assert lines[0].status == 'success'
    assert lines[1].event_date == datetime.date(2022, 9, 1)
    assert lines[1].slug == 'agenda@event-1'
    assert lines[1].label == 'Overtaking'
    assert lines[1].description == '@overtaking@'
    assert lines[1].amount == 1
    assert lines[1].quantity == 30
    assert lines[1].quantity_type == 'minutes'
    assert lines[1].event['primary_event'] == 'event-1::overtaking'
    assert lines[1].booking == {'foo': 'baz1', 'computed_duration': 120, 'adjusted_duration': 90}
    assert lines[1].pricing_data == {
        'foo1': 'bar1',
        'pricing': 1,
        'booking_details': {'status': 'presence'},
        'accounting_code': '414141',
    }
    assert lines[1].accounting_code == '414141'
    assert lines[1].status == 'success'
    assert lines[2].event_date == datetime.date(2022, 9, 1)
    assert lines[2].slug == 'agenda@event-1'
    assert lines[2].description == ''
    assert lines[2].label == 'Foo!'
    assert lines[2].amount == 0.5
    assert lines[2].quantity == 120
    assert lines[2].quantity_type == 'minutes'
    assert lines[2].event['primary_event'] == 'event-1:presence:foo'
    assert lines[2].booking == {'foo': 'baz1', 'computed_duration': 120, 'adjusted_duration': 90}
    assert lines[2].pricing_data == {
        'foo1': 'bar1',
        'pricing': 1.5,
        'booking_details': {'status': 'presence', 'check_type': 'foo', 'check_type_group': 'foobar'},
        'accounting_code': '414141',
    }
    assert lines[2].accounting_code == '414141'
    assert lines[2].status == 'success'


@mock.patch('lingo.invoicing.models.Regie.get_payer_external_id')
@mock.patch('lingo.pricing.models.Pricing.get_pricing_data_for_event')
def test_build_lines_for_user_with_resolved_errors(mock_pricing_data_event, mock_payer):
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
    )
    old_pool = Pool.objects.create(
        campaign=campaign,
        draft=True,
    )
    last_pool = Pool.objects.create(
        campaign=campaign,
        draft=True,
    )
    pool = Pool.objects.create(
        campaign=campaign,
        draft=True,
    )

    check_status_list = [
        {
            'event': {
                'agenda': 'agenda',
                'start_datetime': '2022-09-01T12:00:00+02:00',
                'slug': 'event-ignored',
                'label': 'Event Ignored',
            },
            'check_status': {'foo': 'bar'},
            'booking': {'foo': 'baz'},
        },
        {
            'event': {
                'agenda': 'agenda',
                'start_datetime': '2022-09-01T12:00:00+02:00',
                'slug': 'event-',
                'label': 'Event ',
            },
            'check_status': {'foo': 'bar'},
            'booking': {'foo': 'baz'},
        },
        {
            'event': {
                'agenda': 'agenda',
                'start_datetime': '2022-09-01T12:00:00+02:00',
                'slug': 'event-success',
                'label': 'Event Success',
            },
            'check_status': {'foo': 'bar'},
            'booking': {'foo': 'baz'},
        },
        {
            'event': {
                'agenda': 'agenda',
                'start_datetime': '2022-09-01T12:00:00+02:00',
                'slug': 'event-fixed',
                'label': 'Event Fixed',
            },
            'check_status': {'foo': 'bar'},
            'booking': {'foo': 'baz'},
        },
    ]
    mock_payer.side_effect = [
        PayerError(details={'foo': 'bar'}),
        PayerError(details={'foo': 'bar'}),
        'payer:1',
        PayerError(details={'foo': 'bar'}),
    ]
    mock_pricing_data_event.return_value = {'foo': 'bar', 'pricing': 42}

    # lines in older pool
    for error_status in ['', 'ignored', 'fixed', 'success']:
        DraftJournalLine.objects.create(
            event_date=datetime.date(2022, 9, 1),
            slug='agenda@event-%s' % error_status,
            label='Event %s' % error_status,
            amount=0,
            user_external_id='user:1',
            status='error',
            # all in error_status 'fixed', but error_status will be found in last pool, not this one
            error_status='fixed',
            pool=old_pool,
        )

    # lines in last pool
    for error_status in ['', 'ignored', 'fixed']:
        DraftJournalLine.objects.create(
            event_date=datetime.date(2022, 9, 1),
            slug='agenda@event-%s' % error_status,
            label='Event %s' % error_status,
            amount=0,
            user_external_id='user:1',
            status='error',
            error_status=error_status,
            pool=last_pool,
        )
    DraftJournalLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        slug='agenda@event-success',
        label='Event success',
        amount=0,
        user_external_id='user:1',
        status='success',
        pool=last_pool,
    )
    # line for another user
    DraftJournalLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        slug='agenda@event-',
        label='Event ',
        amount=0,
        user_external_id='user:2',
        status='error',
        error_status='fixed',
        pool=last_pool,
    )

    # generate new lines
    new_lines = utils.build_lines_for_user(
        agendas=[agenda],
        agendas_pricings=[pricing],
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
        pool=pool,
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
        check_status_list=check_status_list,
    )
    assert len(new_lines) == 4
    assert new_lines[0].status == 'error'
    assert new_lines[0].error_status == 'ignored'
    assert new_lines[1].status == 'error'
    assert new_lines[1].error_status == ''
    assert new_lines[2].status == 'success'
    assert new_lines[2].error_status == ''
    assert new_lines[3].status == 'error'
    assert new_lines[3].error_status == 'fixed'


@mock.patch('lingo.invoicing.models.Regie.get_payer_external_id')
def test_build_lines_for_user_get_payer_id_error(mock_payer):
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
    )
    pool = Pool.objects.create(
        campaign=campaign,
        draft=True,
    )
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
    mock_payer.side_effect = PayerError(details={'foo': 'bar'})
    lines = utils.build_lines_for_user(
        agendas=[agenda],
        agendas_pricings=[pricing],
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
        pool=pool,
        payer_data_cache={},
        check_status_list=check_status_list,
    )
    assert len(lines) == 1
    # refresh total_amount field (triggered)
    lines[0].refresh_from_db()
    assert isinstance(lines[0], DraftJournalLine)
    assert lines[0].event_date == datetime.date(2022, 9, 1)
    assert lines[0].slug == 'agenda@event'
    assert lines[0].label == 'Event'
    assert lines[0].amount == 0
    assert lines[0].user_external_id == 'user:1'
    assert lines[0].user_first_name == 'User1'
    assert lines[0].user_last_name == 'Name1'
    assert lines[0].payer_external_id == 'unknown'
    assert lines[0].payer_first_name == ''
    assert lines[0].payer_last_name == ''
    assert lines[0].payer_address == ''
    assert lines[0].payer_direct_debit is False
    assert lines[0].event == {
        'agenda': 'agenda',
        'start_datetime': '2022-09-01T12:00:00+02:00',
        'slug': 'event',
        'label': 'Event',
    }
    assert lines[0].booking == {'foo': 'baz'}
    assert lines[0].pricing_data == {'error': 'PayerError', 'error_details': {'foo': 'bar'}}
    assert lines[0].accounting_code == ''
    assert lines[0].status == 'error'
    assert lines[0].pool == pool
    assert lines[0].from_injected_line is None


@mock.patch('lingo.invoicing.models.Regie.get_payer_external_id')
@mock.patch('lingo.invoicing.models.Regie.get_payer_data')
def test_build_lines_for_user_get_payer_data_error(mock_payer_data, mock_payer):
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
    )
    pool = Pool.objects.create(
        campaign=campaign,
        draft=True,
    )
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
    mock_payer.return_value = 'payer:1'
    mock_payer_data.side_effect = PayerDataError(details={'key': 'foobar', 'reason': 'foo'})
    lines = utils.build_lines_for_user(
        agendas=[agenda],
        agendas_pricings=[pricing],
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
        pool=pool,
        payer_data_cache={},
        check_status_list=check_status_list,
    )
    assert len(lines) == 1
    # refresh total_amount field (triggered)
    lines[0].refresh_from_db()
    assert isinstance(lines[0], DraftJournalLine)
    assert lines[0].event_date == datetime.date(2022, 9, 1)
    assert lines[0].slug == 'agenda@event'
    assert lines[0].label == 'Event'
    assert lines[0].amount == 0
    assert lines[0].user_external_id == 'user:1'
    assert lines[0].user_first_name == 'User1'
    assert lines[0].user_last_name == 'Name1'
    assert lines[0].payer_external_id == 'payer:1'
    assert lines[0].payer_first_name == ''
    assert lines[0].payer_last_name == ''
    assert lines[0].payer_address == ''
    assert lines[0].payer_direct_debit is False
    assert lines[0].event == {
        'agenda': 'agenda',
        'start_datetime': '2022-09-01T12:00:00+02:00',
        'slug': 'event',
        'label': 'Event',
    }
    assert lines[0].booking == {'foo': 'baz'}
    assert lines[0].pricing_data == {
        'error': 'PayerDataError',
        'error_details': {'key': 'foobar', 'reason': 'foo'},
    }
    assert lines[0].status == 'error'
    assert lines[0].pool == pool
    assert lines[0].from_injected_line is None


def test_build_lines_for_user_get_payer_id_and_data():
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
        injected_lines='all',
    )
    pool = Pool.objects.create(
        campaign=campaign,
        draft=True,
    )
    check_status_list = [
        {
            'event': {
                'agenda': 'agenda',
                'start_datetime': '2022-09-01T12:00:00+02:00',
                'slug': 'event',
                'label': 'Event',
            },
            'check_status': {'status': 'presence', 'check_type': ''},
            'booking': {'foo': 'baz'},
        },
    ]

    payer_data_cache = {}

    def get_payer(ap, r, user_external_id, b):
        return {
            'user:1': 'payer:1',
            'user:2': 'payer:2',
        }.get(user_external_id)

    def get_payer_data(ap, r, payer_external_id):
        return {
            'payer:1': {
                'first_name': 'First1',
                'last_name': 'Last1',
                'address': '41 rue des kangourous\n99999 Kangourou Ville',
                'email': 'email1',
                'phone': 'phone1',
                'direct_debit': False,
            },
            'payer:2': {
                'first_name': 'First2',
                'last_name': 'Last2',
                'address': '42 rue des kangourous\n99999 Kangourou Ville',
                'email': 'email2',
                'phone': 'phone2',
                'direct_debit': True,
            },
        }.get(payer_external_id)

    payer_patch = mock.patch.object(Regie, 'get_payer_external_id', autospec=True)
    payer_data_patch = mock.patch.object(Regie, 'get_payer_data', autospec=True)
    with payer_patch as mock_payer, payer_data_patch as mock_payer_data:
        mock_payer.side_effect = get_payer
        mock_payer_data.side_effect = get_payer_data
        lines = []
        for user_external_id in ['user:1', 'user:2', 'user:1']:
            lines += utils.build_lines_for_user(
                agendas=[agenda],
                agendas_pricings=[pricing],
                user_external_id=user_external_id,
                user_first_name='User',
                user_last_name='Name',
                pool=pool,
                payer_data_cache=payer_data_cache,
                check_status_list=check_status_list,
            )

    assert isinstance(lines[0], DraftJournalLine)
    assert lines[0].user_external_id == 'user:1'
    assert lines[0].user_first_name == 'User'
    assert lines[0].user_last_name == 'Name'
    assert lines[0].payer_external_id == 'payer:1'
    assert lines[0].payer_first_name == 'First1'
    assert lines[0].payer_last_name == 'Last1'
    assert lines[0].payer_address == '41 rue des kangourous\n99999 Kangourou Ville'
    assert lines[0].payer_email == 'email1'
    assert lines[0].payer_phone == 'phone1'
    assert lines[0].payer_direct_debit is False
    assert isinstance(lines[1], DraftJournalLine)
    assert lines[1].user_external_id == 'user:2'
    assert lines[1].user_first_name == 'User'
    assert lines[1].user_last_name == 'Name'
    assert lines[1].payer_external_id == 'payer:2'
    assert lines[1].payer_first_name == 'First2'
    assert lines[1].payer_last_name == 'Last2'
    assert lines[1].payer_address == '42 rue des kangourous\n99999 Kangourou Ville'
    assert lines[1].payer_email == 'email2'
    assert lines[1].payer_phone == 'phone2'
    assert lines[1].payer_direct_debit is True
    assert isinstance(lines[2], DraftJournalLine)
    assert lines[2].user_external_id == 'user:1'
    assert lines[2].user_first_name == 'User'
    assert lines[2].user_last_name == 'Name'
    assert lines[2].payer_external_id == 'payer:1'
    assert lines[2].payer_first_name == 'First1'
    assert lines[2].payer_last_name == 'Last1'
    assert lines[2].payer_address == '41 rue des kangourous\n99999 Kangourou Ville'
    assert lines[2].payer_email == 'email1'
    assert lines[2].payer_phone == 'phone1'
    assert lines[2].payer_direct_debit is False

    # cache is populated
    assert payer_data_cache == {
        'payer:1': {
            'first_name': 'First1',
            'last_name': 'Last1',
            'address': '41 rue des kangourous\n99999 Kangourou Ville',
            'email': 'email1',
            'phone': 'phone1',
            'direct_debit': False,
        },
        'payer:2': {
            'first_name': 'First2',
            'last_name': 'Last2',
            'address': '42 rue des kangourous\n99999 Kangourou Ville',
            'email': 'email2',
            'phone': 'phone2',
            'direct_debit': True,
        },
    }
    assert mock_payer.call_args_list == [
        mock.call(regie, mock.ANY, 'user:1', {'foo': 'baz'}),
        mock.call(regie, mock.ANY, 'user:2', {'foo': 'baz'}),
        mock.call(regie, mock.ANY, 'user:1', {'foo': 'baz'}),
    ]
    assert mock_payer_data.call_args_list == [
        mock.call(regie, mock.ANY, 'payer:1'),
        mock.call(regie, mock.ANY, 'payer:2'),
        # only 2 calls, payer:1 is cached after first call
    ]

    # and for injected lines ?
    injected_line = InjectedLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        slug='event-2022-09-01',
        label='Event 2022-09-01',
        amount=7,
        user_external_id='user:1',
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Last1',
        payer_address='41 rue des kangourous\n99999 Kangourou Ville',
        payer_direct_debit=False,
        regie=regie,
    )

    # payer info are stored on injected lines, take it if not in cache
    payer_data_cache = {}
    lines = []
    for user_external_id in ['user:1', 'user:1']:
        lines += utils.build_lines_for_user(
            agendas=[agenda],
            agendas_pricings=[],
            user_external_id=user_external_id,
            user_first_name='User',
            user_last_name='Name',
            pool=pool,
            payer_data_cache=payer_data_cache,
            check_status_list=[],
        )

    assert isinstance(lines[0], DraftJournalLine)
    assert lines[0].user_external_id == 'user:1'
    assert lines[0].user_first_name == 'User'
    assert lines[0].user_last_name == 'Name'
    assert lines[0].payer_external_id == 'payer:1'
    assert lines[0].payer_first_name == 'First1'
    assert lines[0].payer_last_name == 'Last1'
    assert lines[0].payer_address == '41 rue des kangourous\n99999 Kangourou Ville'
    assert lines[0].payer_email == ''
    assert lines[0].payer_phone == ''
    assert lines[0].payer_direct_debit is False
    assert lines[0].from_injected_line == injected_line
    assert isinstance(lines[1], DraftJournalLine)
    assert lines[1].user_external_id == 'user:1'
    assert lines[1].user_first_name == 'User'
    assert lines[1].user_last_name == 'Name'
    assert lines[1].payer_external_id == 'payer:1'
    assert lines[1].payer_first_name == 'First1'
    assert lines[1].payer_last_name == 'Last1'
    assert lines[1].payer_address == '41 rue des kangourous\n99999 Kangourou Ville'
    assert lines[1].payer_email == ''
    assert lines[1].payer_phone == ''
    assert lines[1].payer_direct_debit is False
    assert lines[1].from_injected_line == injected_line

    # cache is populated
    assert payer_data_cache == {
        'payer:1': {
            'first_name': 'First1',
            'last_name': 'Last1',
            'address': '41 rue des kangourous\n99999 Kangourou Ville',
            'email': '',
            'phone': '',
            'direct_debit': False,
        },
    }

    # but take in cache if present
    payer_data_cache = {
        'payer:1': {
            'first_name': 'First1 IN CACHE',
            'last_name': 'Last1 IN CACHE',
            'address': '41 rue des kangourous\n99999 Kangourou Ville IN CACHE',
            'email': 'email1 IN CACHE',
            'phone': 'phone1 IN CACHE',
            'direct_debit': True,
        },
    }

    lines = utils.build_lines_for_user(
        agendas=[agenda],
        agendas_pricings=[],
        user_external_id=user_external_id,
        user_first_name='User',
        user_last_name='Name',
        pool=pool,
        payer_data_cache=payer_data_cache,
        check_status_list=[],
    )
    assert len(lines) == 1
    assert isinstance(lines[0], DraftJournalLine)
    assert lines[0].user_external_id == 'user:1'
    assert lines[0].user_first_name == 'User'
    assert lines[0].user_last_name == 'Name'
    assert lines[0].payer_external_id == 'payer:1'
    assert lines[0].payer_first_name == 'First1 IN CACHE'
    assert lines[0].payer_last_name == 'Last1 IN CACHE'
    assert lines[0].payer_address == '41 rue des kangourous\n99999 Kangourou Ville IN CACHE'
    assert lines[0].payer_email == 'email1 IN CACHE'
    assert lines[0].payer_phone == 'phone1 IN CACHE'
    assert lines[0].payer_direct_debit is True
    assert lines[0].from_injected_line == injected_line


@mock.patch('lingo.invoicing.models.Regie.get_payer_external_id')
def test_build_lines_for_user_check_status_pricing_dates(mock_payer):
    regie = Regie.objects.create(label='Regie')
    agenda = Agenda.objects.create(label='Agenda')
    pricing1 = Pricing.objects.create(
        date_start=datetime.date(year=2022, month=9, day=1),
        date_end=datetime.date(year=2022, month=10, day=1),
    )
    pricing1.agendas.add(agenda)
    pricing2 = Pricing.objects.create(
        date_start=datetime.date(year=2022, month=10, day=1),
        date_end=datetime.date(year=2022, month=11, day=1),
        flat_fee_schedule=True,
    )
    pricing2.agendas.add(agenda)
    pricing3 = Pricing.objects.create(
        date_start=datetime.date(year=2022, month=10, day=1),
        date_end=datetime.date(year=2022, month=11, day=1),
    )
    pricing3.agendas.add(agenda)
    campaign = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 1, 1),
        date_end=datetime.date(2023, 1, 1),
        date_publication=datetime.date(2023, 1, 1),
        date_payment_deadline=datetime.date(2023, 1, 31),
        date_due=datetime.date(2023, 1, 31),
        date_debit=datetime.date(2022, 2, 15),
    )
    pool = Pool.objects.create(
        campaign=campaign,
        draft=True,
    )

    pricing_data_event_patch = mock.patch.object(Pricing, 'get_pricing_data_for_event', autospec=True)
    mock_payer.return_value = 'payer:1'

    # check agenda pricing of september is used
    for event_date in ['2022-09-01T12:00:00+02:00', '2022-09-30T12:00:00+02:00']:
        check_status_list = [
            {
                'event': {
                    'agenda': 'agenda',
                    'start_datetime': event_date,
                    'slug': 'event',
                    'label': 'Event',
                },
                'check_status': {'foo': 'bar'},
                'booking': {'foo': 'baz'},
            },
        ]
        with pricing_data_event_patch as mock_pricing_data_event:
            mock_pricing_data_event.return_value = {'foo': 'bar', 'pricing': 42}
            lines = utils.build_lines_for_user(
                agendas=[agenda],
                agendas_pricings=Pricing.objects.all(),
                user_external_id='user:1',
                user_first_name='User1',
                user_last_name='Name1',
                pool=pool,
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
                check_status_list=check_status_list,
            )
            assert len(lines) == 1
            assert mock_pricing_data_event.call_args_list[0][0][0] == pricing1

    # check agenda pricing of october is used
    for event_date in ['2022-10-01T12:00:00+02:00', '2022-10-31T12:00:00+02:00']:
        check_status_list = [
            {
                'event': {
                    'agenda': 'agenda',
                    'start_datetime': event_date,
                    'slug': 'event',
                    'label': 'Event',
                },
                'check_status': {'foo': 'bar'},
                'booking': {'foo': 'baz'},
            },
        ]
        with pricing_data_event_patch as mock_pricing_data_event:
            mock_pricing_data_event.return_value = {'foo': 'bar', 'pricing': 42}
            lines = utils.build_lines_for_user(
                agendas=[agenda],
                agendas_pricings=Pricing.objects.all(),
                user_external_id='user:1',
                user_first_name='User1',
                user_last_name='Name1',
                pool=pool,
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
                check_status_list=check_status_list,
            )
            assert len(lines) == 1
            assert mock_pricing_data_event.call_args_list[0][0][0] == pricing3

    # no matching agenda pricing
    for event_date in ['2022-08-31T12:00:00+02:00', '2022-11-01T12:00:00+02:00']:
        check_status_list = [
            {
                'event': {
                    'agenda': 'agenda',
                    'start_datetime': event_date,
                    'slug': 'event',
                    'label': 'Event',
                },
                'check_status': {'foo': 'bar'},
                'booking': {'foo': 'baz'},
            },
        ]
        with pricing_data_event_patch as mock_pricing_data_event:
            lines = utils.build_lines_for_user(
                agendas=[agenda],
                agendas_pricings=Pricing.objects.all(),
                user_external_id='user:1',
                user_first_name='User1',
                user_last_name='Name1',
                pool=pool,
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
                check_status_list=check_status_list,
            )
            assert len(lines) == 1
            line = lines[0]
            # refresh total_amount field (triggered)
            line.refresh_from_db()
            assert isinstance(line, DraftJournalLine)
            assert line.slug == 'agenda@event'
            assert line.label == 'Event'
            assert line.amount == 0
            assert line.user_external_id == 'user:1'
            assert line.user_first_name == 'User1'
            assert line.user_last_name == 'Name1'
            assert line.payer_external_id == 'unknown'
            assert line.payer_first_name == ''
            assert line.payer_last_name == ''
            assert line.payer_address == ''
            assert line.payer_direct_debit is False
            assert line.event == {
                'agenda': 'agenda',
                'start_datetime': event_date,
                'slug': 'event',
                'label': 'Event',
            }
            assert line.booking == {'foo': 'baz'}
            assert line.pricing_data == {'error': 'PricingNotFound', 'error_details': {}}
            assert line.status == 'warning'
            assert line.pool == pool
            assert mock_pricing_data_event.call_args_list == []


@mock.patch('lingo.invoicing.models.Regie.get_payer_external_id')
@mock.patch('lingo.pricing.models.Pricing.get_pricing_data_for_event')
def test_build_lines_for_user_check_status_pricing_error(mock_pricing_data_event, mock_payer):
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
    )
    pool = Pool.objects.create(
        campaign=campaign,
        draft=True,
    )

    mock_pricing_data_event.side_effect = [
        {'foo1': 'bar1', 'pricing': decimal.Decimal(1)},
        PricingError(details={'foo': 'bar'}),
        {'foo3': 'bar3', 'pricing': decimal.Decimal(3)},
    ]
    mock_payer.return_value = 'payer:1'
    check_status_list = [
        {
            'event': {
                'agenda': 'agenda',
                'start_datetime': '2022-09-01T12:00:00+02:00',
                'slug': 'event-1',
                'label': 'Event 1',
            },
            'check_status': {'foo': 'bar'},
            'booking': {'foo': 'baz'},
        },
        {
            'event': {
                'agenda': 'agenda',
                'start_datetime': '2022-09-02T12:00:00+02:00',
                'slug': 'event-2',
                'label': 'Event 2',
            },
            'check_status': {'foo': 'bar'},
            'booking': {'foo': 'baz'},
        },
        {
            'event': {
                'agenda': 'agenda',
                'start_datetime': '2022-09-03T12:00:00+02:00',
                'slug': 'event-3',
                'label': 'Event 3',
            },
            'check_status': {'foo': 'bar'},
            'booking': {'foo': 'baz'},
        },
    ]
    lines = utils.build_lines_for_user(
        agendas=[agenda],
        agendas_pricings=[pricing],
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
        pool=pool,
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
        check_status_list=check_status_list,
    )
    # refresh total_amount field (triggered)
    lines = DraftJournalLine.objects.filter(pk__in=[li.pk for li in lines]).order_by('pk')
    assert len(lines) == 3
    assert lines[0].event_date == datetime.date(2022, 9, 1)
    assert lines[0].slug == 'agenda@event-1'
    assert lines[0].label == 'Event 1'
    assert lines[0].amount == 1
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
    assert lines[0].booking == {'foo': 'baz'}
    assert lines[0].pricing_data == {'foo1': 'bar1', 'pricing': '1'}
    assert lines[0].accounting_code == ''
    assert lines[0].status == 'success'
    assert lines[0].pool == pool
    assert lines[1].event_date == datetime.date(2022, 9, 2)
    assert lines[1].slug == 'agenda@event-2'
    assert lines[1].label == 'Event 2'
    assert lines[1].amount == 0
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
        'start_datetime': '2022-09-02T12:00:00+02:00',
        'slug': 'event-2',
        'label': 'Event 2',
    }
    assert lines[1].booking == {'foo': 'baz'}
    assert lines[1].pricing_data == {'error': 'PricingError', 'error_details': {'foo': 'bar'}}
    assert lines[1].accounting_code == ''
    assert lines[1].status == 'error'
    assert lines[1].pool == pool
    assert lines[2].event_date == datetime.date(2022, 9, 3)
    assert lines[2].slug == 'agenda@event-3'
    assert lines[2].label == 'Event 3'
    assert lines[2].amount == 3
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
        'start_datetime': '2022-09-03T12:00:00+02:00',
        'slug': 'event-3',
        'label': 'Event 3',
    }
    assert lines[2].booking == {'foo': 'baz'}
    assert lines[2].pricing_data == {'foo3': 'bar3', 'pricing': '3'}
    assert lines[2].accounting_code == ''
    assert lines[2].status == 'success'
    assert lines[2].pool == pool


@mock.patch('lingo.invoicing.utils.get_lines_for_user')
def test_build_lines_for_users(mock_user_lines):
    regie = Regie.objects.create(label='Regie')
    agenda1 = Agenda.objects.create(label='Agenda 1')
    agenda2 = Agenda.objects.create(label='Agenda 2')
    Agenda.objects.create(label='Agenda 3')
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

    # no subscribed users
    utils.build_lines_for_users(
        agendas=[agenda1, agenda2],
        users={},
        pool=pool,
    )
    assert mock_user_lines.call_args_list == []

    # with subscribed users, but wrong pool status
    utils.build_lines_for_users(
        agendas=[agenda1, agenda2],
        users={},
        pool=pool,
    )
    assert mock_user_lines.call_args_list == []

    # correct pool status
    pool.status = 'running'
    pool.save()
    pjob = PoolAsyncJob.objects.create(
        pool=pool, status='registered', users={'user:1': ('User1', 'Name1'), 'user:2': ('User2', 'Name2')}
    )
    utils.build_lines_for_users(
        agendas=[agenda1, agenda2],
        users={'user:1': ('User1', 'Name1'), 'user:2': ('User2', 'Name2')},
        pool=pool,
        job=pjob,
    )
    assert mock_user_lines.call_args_list == [
        mock.call(
            agendas=[agenda1, agenda2],
            agendas_pricings=mock.ANY,
            user_external_id='user:1',
            user_first_name='User1',
            user_last_name='Name1',
            pool=pool,
            payer_data_cache={},
            request=mock.ANY,
        ),
        mock.call(
            agendas=[agenda1, agenda2],
            agendas_pricings=mock.ANY,
            user_external_id='user:2',
            user_first_name='User2',
            user_last_name='Name2',
            pool=pool,
            payer_data_cache={},
            request=mock.ANY,
        ),
    ]
    assert pjob.total_count == 2
    assert pjob.current_count == 2


@mock.patch('lingo.invoicing.utils.get_check_status')
@mock.patch('lingo.invoicing.models.Regie.get_payer_external_id')
@mock.patch('lingo.invoicing.models.Regie.get_payer_data')
def test_build_lines_for_users_queryset(mock_payer_data, mock_payer, mock_status):
    # don't mock get_pricing_data_for_event to check all querysets
    category1 = CriteriaCategory.objects.create(label='Foo1', slug='foo1')
    criteria1 = Criteria.objects.create(label='Bar1', slug='bar1', condition='True', category=category1)
    category2 = CriteriaCategory.objects.create(label='Foo2', slug='foo2')
    criteria2 = Criteria.objects.create(label='Bar2', slug='bar2', condition='True', category=category2)

    agenda1 = Agenda.objects.create(label='Agenda 1')
    pricing11 = Pricing.objects.create(
        date_start=datetime.date(year=2022, month=9, day=1),
        date_end=datetime.date(year=2022, month=10, day=1),
    )
    pricing11.criterias.add(criteria1, criteria2)
    pricing11.categories.add(category1, through_defaults={'order': 1})
    pricing11.categories.add(category2, through_defaults={'order': 2})
    pricing11.agendas.add(agenda1)
    pricing12 = Pricing.objects.create(
        date_start=datetime.date(year=2022, month=10, day=1),
        date_end=datetime.date(year=2022, month=11, day=1),
    )
    pricing12.criterias.add(criteria1, criteria2)
    pricing12.categories.add(category1, through_defaults={'order': 1})
    pricing12.categories.add(category2, through_defaults={'order': 2})
    pricing12.agendas.add(agenda1)

    agenda2 = Agenda.objects.create(label='Agenda 2')
    pricing21 = Pricing.objects.create(
        date_start=datetime.date(year=2022, month=9, day=1),
        date_end=datetime.date(year=2022, month=10, day=1),
    )
    pricing21.criterias.add(criteria1, criteria2)
    pricing21.categories.add(category1, through_defaults={'order': 1})
    pricing21.categories.add(category2, through_defaults={'order': 2})
    pricing21.agendas.add(agenda2)
    pricing22 = Pricing.objects.create(
        date_start=datetime.date(year=2022, month=10, day=1),
        date_end=datetime.date(year=2022, month=11, day=1),
    )
    pricing22.criterias.add(criteria1, criteria2)
    pricing22.categories.add(category1, through_defaults={'order': 1})
    pricing22.categories.add(category2, through_defaults={'order': 2})
    pricing22.agendas.add(agenda2)

    regie = Regie.objects.create(label='Regie')
    campaign = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 11, 1),
        date_publication=datetime.date(2022, 11, 1),
        date_payment_deadline=datetime.date(2022, 11, 30),
        date_due=datetime.date(2022, 11, 30),
        date_debit=datetime.date(2022, 12, 15),
        injected_lines='all',
    )
    pool = Pool.objects.create(
        campaign=campaign,
        draft=True,
        status='running',
    )

    mock_status.return_value = [
        {
            'event': {
                'agenda': 'agenda-1',
                'start_datetime': '2022-09-01T12:00:00+02:00',
                'slug': 'event',
                'label': 'Event',
            },
            'check_status': {'status': 'presence', 'check_type': ''},
            'booking': {'foo': 'baz'},
        },
        {
            'event': {
                'agenda': 'agenda-1',
                'start_datetime': '2022-10-01T12:00:00+02:00',
                'slug': 'event',
                'label': 'Event',
            },
            'check_status': {'status': 'presence', 'check_type': ''},
            'booking': {'foo': 'baz'},
        },
        {
            'event': {
                'agenda': 'agenda-2',
                'start_datetime': '2022-09-01T12:00:00+02:00',
                'slug': 'event',
                'label': 'Event',
            },
            'check_status': {'status': 'presence', 'check_type': ''},
            'booking': {'foo': 'baz'},
        },
        {
            'event': {
                'agenda': 'agenda-2',
                'start_datetime': '2022-10-01T12:00:00+02:00',
                'slug': 'event',
                'label': 'Event',
            },
            'check_status': {'status': 'presence', 'check_type': ''},
            'booking': {'foo': 'baz'},
        },
    ]
    mock_payer.return_value = 'payer:1'
    mock_payer_data.return_value = {
        'first_name': 'First1',
        'last_name': 'Last1',
        'direct_debit': False,
    }

    with CaptureQueriesContext(connection) as ctx:
        utils.build_lines_for_users(
            agendas=[agenda1, agenda2],
            users={'user:1': ('User1', 'Name1'), 'user:2': ('User2', 'Name2')},
            pool=pool,
        )
        assert len(ctx.captured_queries) == 13
    assert pool.draftjournalline_set.exists()


def test_generate_invoices_from_lines():
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

    DraftJournalLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        event={'agenda': 'agenda-1'},
        amount=0,
        user_external_id='user:1',
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Last1',
        payer_direct_debit=False,
        status='error',
        pool=pool,
    )
    line1 = DraftJournalLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        event={'agenda': 'agenda-1'},
        amount=1,
        user_external_id='user:1',
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Last1',
        payer_direct_debit=False,
        status='success',
        pool=pool,
    )
    line2 = DraftJournalLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        event={'agenda': 'agenda-1'},
        amount=2,
        user_external_id='user:1',
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Last1',
        payer_direct_debit=False,
        status='success',
        pool=pool,
    )
    line3 = DraftJournalLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        event={'agenda': 'agenda-2'},
        amount=3,
        user_external_id='user:1',
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Last1',
        payer_direct_debit=False,
        status='success',
        pool=pool,
    )
    line4 = DraftJournalLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        event={'agenda': 'agenda-2'},
        amount=4,
        user_external_id='user:1',
        payer_external_id='payer:2',
        payer_first_name='First2',
        payer_last_name='Last2',
        payer_direct_debit=True,
        status='success',
        pool=pool,
    )
    line5 = DraftJournalLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        event={'agenda': 'agenda-3'},
        amount=5,
        user_external_id='user:2',
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Last1',
        payer_direct_debit=False,
        status='success',
        pool=pool,
    )
    injected_line = InjectedLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        slug='event-2022-09-01',
        label='Event 2022-09-01',
        amount=-7,
        user_external_id='user:1',
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Last1',
        payer_direct_debit=False,
        regie=regie,
    )
    line6 = DraftJournalLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        amount=-7,
        user_external_id='user:1',
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Last1',
        payer_direct_debit=False,
        status='success',
        pool=pool,
        from_injected_line=injected_line,
    )

    invoices = utils.generate_invoices_from_lines(pool=pool)
    # pool status is not correct
    assert len(invoices) == 0

    # correct pool status
    pool.status = 'running'
    pool.save()
    pjob = PoolAsyncJob.objects.create(
        pool=pool, status='registered', users={'user:1': ('User1', 'Name1'), 'user:2': ('User2', 'Name2')}
    )
    invoices = utils.generate_invoices_from_lines(pool=pool, job=pjob)
    assert pjob.total_count == 2
    assert pjob.current_count == 2
    assert len(invoices) == 2
    # refresh total_amount field (triggered)
    invoices[0].refresh_from_db()
    invoices[1].refresh_from_db()
    line1.refresh_from_db()
    line2.refresh_from_db()
    line3.refresh_from_db()
    line4.refresh_from_db()
    line5.refresh_from_db()
    line6.refresh_from_db()
    assert DraftInvoiceLine.objects.count() == 6
    iline1, iline2, iline3, iline4, iline5, iline6 = DraftInvoiceLine.objects.all().order_by('pk')
    assert isinstance(invoices[0], DraftInvoice)
    assert invoices[0].label == 'Invoice from 01/09/2022 to 30/09/2022'
    assert invoices[0].total_amount == 4
    assert invoices[0].date_publication == datetime.date(2022, 10, 1)
    assert invoices[0].date_payment_deadline == datetime.date(2022, 10, 31)
    assert invoices[0].date_due == datetime.date(2022, 10, 31)
    assert invoices[0].date_debit is None
    assert invoices[0].regie == regie
    assert invoices[0].payer_external_id == 'payer:1'
    assert invoices[0].payer_first_name == 'First1'
    assert invoices[0].payer_last_name == 'Last1'
    assert invoices[0].payer_address == ''
    assert invoices[0].payer_direct_debit is False
    assert invoices[0].pool == pool
    assert invoices[0].origin == 'campaign'
    assert list(invoices[0].lines.order_by('pk')) == [iline1, iline2, iline3, iline4, iline5]
    assert isinstance(invoices[1], DraftInvoice)
    assert invoices[1].label == 'Invoice from 01/09/2022 to 30/09/2022'
    assert invoices[1].total_amount == 4
    assert invoices[1].date_publication == datetime.date(2022, 10, 1)
    assert invoices[1].date_payment_deadline == datetime.date(2022, 10, 31)
    assert invoices[1].date_due == datetime.date(2022, 10, 31)
    assert invoices[1].date_debit == datetime.date(2022, 11, 15)
    assert invoices[1].regie == regie
    assert invoices[1].payer_external_id == 'payer:2'
    assert invoices[1].payer_first_name == 'First2'
    assert invoices[1].payer_last_name == 'Last2'
    assert invoices[1].payer_address == ''
    assert invoices[1].payer_direct_debit is True
    assert invoices[1].pool == pool
    assert invoices[1].origin == 'campaign'
    assert list(invoices[1].lines.order_by('pk')) == [iline6]
    for line, iline in [
        (line1, iline1),
        (line2, iline2),
        (line3, iline3),
        (line5, iline4),
        (line6, iline5),
        (line4, iline6),
    ]:
        assert iline.event_date == line.event_date
        assert iline.label == line.label
        assert iline.quantity == 1
        assert iline.unit_amount == line.amount
        assert iline.user_external_id == line.user_external_id
        assert iline.user_first_name == line.user_first_name
        assert iline.user_last_name == line.user_last_name
        assert iline.pool == pool
        assert iline == line.invoice_line


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
            payer_email='email1',
            payer_phone='phone1',
            payer_direct_debit=False,
            status='success',
            pool=pool,
        )
    # 3 lines for event event-1, check_type foo, per-hour invoicing
    for i in range(3):
        DraftJournalLine.objects.create(
            label='Event 1',
            event_date=datetime.date(2022, 9, 1 + i),
            event={
                'agenda': 'agenda-1',
                'primary_event': 'event-1',
                'start_datetime': '2022-09-01T12:00:00+02:00',
            },
            pricing_data={
                'booking_details': {'check_type': 'foo', 'check_type_group': 'foobar', 'status': 'presence'}
            },
            amount=1,
            quantity=30,
            quantity_type='minutes',
            accounting_code='424242',
            user_external_id='user:1',
            user_first_name='UserFirst1',
            user_last_name='UserLast1',
            payer_external_id='payer:1',
            payer_first_name='First1',
            payer_last_name='Last1',
            payer_address='41 rue des kangourous\n99999 Kangourou Ville',
            payer_email='email1',
            payer_phone='phone1',
            payer_direct_debit=False,
            status='success',
            pool=pool,
        )
    DraftJournalLine.objects.create(
        label='Event 1',
        description='@booked-hours@',
        event_date=datetime.date(2022, 9, 1),
        event={
            'agenda': 'agenda-1',
            'primary_event': 'event-1',
            'start_datetime': '2022-09-01T12:00:00+02:00',
        },
        pricing_data={
            'booking_details': {'check_type': 'foo', 'check_type_group': 'foobar', 'status': 'presence'}
        },
        amount=1,
        accounting_code='424242',
        user_external_id='user:2',  # another user
        user_first_name='UserFirst2',
        user_last_name='UserLast2',
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Last1',
        payer_address='41 rue des kangourous\n99999 Kangourou Ville',
        payer_email='email1',
        payer_phone='phone1',
        payer_direct_debit=False,
        status='success',
        pool=pool,
    )
    DraftJournalLine.objects.create(
        label='Event 1',
        event_date=datetime.date(2022, 9, 1),
        event={
            'agenda': 'agenda-2',
            'primary_event': 'event-1',
            'start_datetime': '2022-09-01T12:00:00+02:00',
        },  # another agenda
        pricing_data={
            'booking_details': {'check_type': 'foo', 'check_type_group': 'foobar', 'status': 'presence'}
        },
        amount=1,
        accounting_code='424242',
        user_external_id='user:1',
        user_first_name='UserFirst1',
        user_last_name='UserLast1',
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Last1',
        payer_address='41 rue des kangourous\n99999 Kangourou Ville',
        payer_email='email1',
        payer_phone='phone1',
        payer_direct_debit=False,
        status='success',
        pool=pool,
    )
    DraftJournalLine.objects.create(
        label='Event 2',
        event_date=datetime.date(2022, 9, 1),
        event={
            'agenda': 'agenda-1',
            'primary_event': 'event-2',
            'start_datetime': '2022-09-01T12:00:00+02:00',
        },  # another event
        pricing_data={
            'booking_details': {'check_type': 'foo', 'check_type_group': 'foobar', 'status': 'presence'}
        },
        amount=1,
        accounting_code='424242',
        user_external_id='user:1',
        user_first_name='UserFirst1',
        user_last_name='UserLast1',
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Last1',
        payer_address='41 rue des kangourous\n99999 Kangourou Ville',
        payer_email='email1',
        payer_phone='phone1',
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
        pricing_data={
            'booking_details': {'check_type': 'bar', 'check_type_group': 'foobar', 'status': 'presence'}
        },  # another check_type
        amount=1,
        accounting_code='424242',
        user_external_id='user:1',
        user_first_name='UserFirst1',
        user_last_name='UserLast1',
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Last1',
        payer_address='41 rue des kangourous\n99999 Kangourou Ville',
        payer_email='email1',
        payer_phone='phone1',
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
        pricing_data={
            'booking_details': {'check_type': 'foo', 'check_type_group': 'foobaz', 'status': 'presence'}
        },  # another check_type_group
        amount=1,
        accounting_code='424242',
        user_external_id='user:1',
        user_first_name='UserFirst1',
        user_last_name='UserLast1',
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Last1',
        payer_address='41 rue des kangourous\n99999 Kangourou Ville',
        payer_email='email1',
        payer_phone='phone1',
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
        pricing_data={
            'booking_details': {'check_type': 'foo', 'check_type_group': 'foobar', 'status': 'absence'}
        },  # another status
        amount=1,
        accounting_code='424242',
        user_external_id='user:1',
        user_first_name='UserFirst1',
        user_last_name='UserLast1',
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Last1',
        payer_address='41 rue des kangourous\n99999 Kangourou Ville',
        payer_email='email1',
        payer_phone='phone1',
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
        pricing_data={
            'booking_details': {'check_type': 'foo', 'check_type_group': 'foobar', 'status': 'presence'}
        },
        amount=2,  # another amount
        accounting_code='424242',
        user_external_id='user:1',
        user_first_name='UserFirst1',
        user_last_name='UserLast1',
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Last1',
        payer_address='41 rue des kangourous\n99999 Kangourou Ville',
        payer_email='email1',
        payer_phone='phone1',
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
        pricing_data={
            'booking_details': {'check_type': 'foo', 'check_type_group': 'foobar', 'status': 'presence'}
        },
        amount=1,
        accounting_code='434343',  # another accounting code
        user_external_id='user:1',
        user_first_name='UserFirst1',
        user_last_name='UserLast1',
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Last1',
        payer_address='41 rue des kangourous\n99999 Kangourou Ville',
        payer_email='email1',
        payer_phone='phone1',
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
        pricing_data={'booking_details': {'status': 'presence'}},
        amount=1,
        accounting_code='424242',
        user_external_id='user:1',
        user_first_name='UserFirst1',
        user_last_name='UserLast1',
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Last1',
        payer_address='41 rue des kangourous\n99999 Kangourou Ville',
        payer_email='email1',
        payer_phone='phone1',
        payer_direct_debit=False,
        status='success',
        pool=pool,
    )
    DraftJournalLine.objects.create(
        label='Foobar Injected',
        slug='foobar',
        event_date=datetime.date(2022, 9, 1),
        event={},  # injected line
        pricing_data={
            'booking_details': {'check_type': 'foo', 'check_type_group': 'foobar', 'status': 'presence'}
        },
        amount=1,
        user_external_id='user:1',
        user_first_name='UserFirst1',
        user_last_name='UserLast1',
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Last1',
        payer_address='41 rue des kangourous\n99999 Kangourou Ville',
        payer_email='email1',
        payer_phone='phone1',
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
        payer_email='email1',
        payer_phone='phone1',
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
        payer_email='email1',
        payer_phone='phone1',
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
    assert DraftInvoiceLine.objects.count() == 12
    (
        iline1,
        iline2,
        iline3,
        iline4,
        iline5,
        iline6,
        iline7,
        iline8,
        iline9,
        iline10,
        iline11,
        iline12,
    ) = DraftInvoiceLine.objects.all().order_by('pk')
    assert isinstance(invoice, DraftInvoice)
    assert invoice.total_amount == decimal.Decimal('15.50')
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
    # 3 journal lines grouped in an invoice line
    assert iline2.event_date == campaign.date_start
    assert iline2.label == 'Event 1'
    assert iline2.quantity == decimal.Decimal('1.5')  # 90 minutes
    assert iline2.unit_amount == 1
    assert iline2.details == {
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
    assert iline2.event_slug == 'agenda-1@event-1'
    assert iline2.event_label == 'Event 1'
    assert iline2.agenda_slug == 'agenda-1'
    assert iline2.activity_label == 'Agenda 1'
    assert iline2.description == '01/09, 02/09, 03/09'
    assert iline2.accounting_code == '424242'
    assert iline2.user_external_id == 'user:1'
    assert iline2.user_first_name == 'UserFirst1'
    assert iline2.user_last_name == 'UserLast1'
    assert iline2.pool == pool
    assert iline2 == lines[3].invoice_line
    assert iline2 == lines[4].invoice_line
    assert iline2 == lines[5].invoice_line
    # one journal line, one invoice line
    ilines = [
        (lines[6], iline3),
        (lines[7], iline4),
        (lines[8], iline5),
        (lines[9], iline6),
        (lines[10], iline7),
        (lines[11], iline8),
        (lines[12], iline9),
        (lines[13], iline10),
        (lines[14], iline11),
        (lines[15], iline12),
    ]
    for line, iline in ilines:
        assert iline.event_date == line.event_date
        assert iline.label == line.label
        assert iline.event_label == line.label
        assert iline.quantity == 1
        assert iline.unit_amount == line.amount
        assert iline.user_external_id == line.user_external_id
        assert iline.user_first_name == line.user_first_name
        assert iline.user_last_name == line.user_last_name
        assert iline.pool == pool
        assert iline == line.invoice_line
    assert iline3.details == {
        'agenda': 'agenda-1',
        'primary_event': 'event-1',
        'status': 'presence',
        'check_type': 'foo',
        'check_type_group': 'foobar',
        'check_type_label': 'Foo!',
        'dates': ['2022-09-01'],
        'event_time': '12:00:00',
        'partial_bookings': False,
    }
    assert iline3.event_slug == 'agenda-1@event-1'
    assert iline3.agenda_slug == 'agenda-1'
    assert iline3.activity_label == 'Agenda 1'
    assert iline3.description == '1 booked hours for the period'
    assert iline3.accounting_code == '424242'
    assert iline4.details == {
        'agenda': 'agenda-2',
        'primary_event': 'event-1',
        'status': 'presence',
        'check_type': 'foo',
        'check_type_group': 'foobar',
        'check_type_label': 'Foo!',
        'dates': ['2022-09-01'],
        'event_time': '12:00:00',
        'partial_bookings': False,
    }
    assert iline4.event_slug == 'agenda-2@event-1'
    assert iline4.agenda_slug == 'agenda-2'
    assert iline4.activity_label == 'Agenda 2'
    assert iline4.description == '01/09'
    assert iline4.accounting_code == '424242'
    assert iline5.details == {
        'agenda': 'agenda-1',
        'primary_event': 'event-2',
        'status': 'presence',
        'check_type': 'foo',
        'check_type_group': 'foobar',
        'check_type_label': 'Foo!',
        'dates': ['2022-09-01'],
        'event_time': '12:00:00',
        'partial_bookings': False,
    }
    assert iline5.event_slug == 'agenda-1@event-2'
    assert iline5.agenda_slug == 'agenda-1'
    assert iline5.activity_label == 'Agenda 1'
    assert iline5.description == '01/09'
    assert iline5.accounting_code == '424242'
    assert iline6.details == {
        'agenda': 'agenda-1',
        'primary_event': 'event-1',
        'status': 'presence',
        'check_type': 'bar',
        'check_type_group': 'foobar',
        'check_type_label': 'bar',
        'dates': ['2022-09-01'],
        'event_time': '12:00:00',
        'partial_bookings': False,
    }
    assert iline6.event_slug == 'agenda-1@event-1'
    assert iline6.agenda_slug == 'agenda-1'
    assert iline6.activity_label == 'Agenda 1'
    assert iline6.description == '01/09'
    assert iline6.accounting_code == '424242'
    assert iline7.details == {
        'agenda': 'agenda-1',
        'primary_event': 'event-1',
        'status': 'presence',
        'check_type': 'foo',
        'check_type_group': 'foobaz',
        'check_type_label': 'foo',
        'dates': ['2022-09-01'],
        'event_time': '12:00:00',
        'partial_bookings': False,
    }
    assert iline7.event_slug == 'agenda-1@event-1'
    assert iline7.agenda_slug == 'agenda-1'
    assert iline7.activity_label == 'Agenda 1'
    assert iline7.description == '01/09'
    assert iline7.accounting_code == '424242'
    assert iline8.details == {
        'agenda': 'agenda-1',
        'primary_event': 'event-1',
        'status': 'absence',
        'check_type': 'foo',
        'check_type_group': 'foobar',
        'check_type_label': 'foo',
        'dates': ['2022-09-01'],
        'event_time': '12:00:00',
        'partial_bookings': False,
    }
    assert iline8.event_slug == 'agenda-1@event-1'
    assert iline8.agenda_slug == 'agenda-1'
    assert iline8.activity_label == 'Agenda 1'
    assert iline8.description == '01/09'
    assert iline8.accounting_code == '424242'
    assert iline9.details == {
        'agenda': 'agenda-1',
        'primary_event': 'event-1',
        'status': 'presence',
        'check_type': 'foo',
        'check_type_group': 'foobar',
        'check_type_label': 'Foo!',
        'dates': ['2022-09-01'],
        'event_time': '12:00:00',
        'partial_bookings': False,
    }
    assert iline9.event_slug == 'agenda-1@event-1'
    assert iline9.agenda_slug == 'agenda-1'
    assert iline9.activity_label == 'Agenda 1'
    assert iline9.description == '01/09'
    assert iline9.accounting_code == '424242'
    assert iline10.details == {
        'agenda': 'agenda-1',
        'primary_event': 'event-1',
        'status': 'presence',
        'check_type': 'foo',
        'check_type_group': 'foobar',
        'check_type_label': 'Foo!',
        'dates': ['2022-09-01'],
        'event_time': '12:00:00',
        'partial_bookings': False,
    }
    assert iline10.event_slug == 'agenda-1@event-1'
    assert iline10.agenda_slug == 'agenda-1'
    assert iline10.activity_label == 'Agenda 1'
    assert iline10.description == '01/09'
    assert iline10.accounting_code == '434343'
    assert iline11.details == {}
    assert iline11.event_slug == 'agenda-1@foobar'
    assert iline11.agenda_slug == 'agenda-1'
    assert iline11.activity_label == 'Agenda 1'
    assert iline11.description == ''
    assert iline11.accounting_code == ''
    assert iline12.details == {}
    assert iline12.event_slug == 'foobar'
    assert iline12.agenda_slug == ''
    assert iline12.activity_label == ''
    assert iline12.description == ''
    assert iline12.accounting_code == ''


@mock.patch('lingo.invoicing.models.campaign.lock_events_check')
@mock.patch('lingo.invoicing.utils.get_agendas')
@mock.patch('lingo.invoicing.utils.get_users_from_subscriptions')
@mock.patch('lingo.invoicing.utils.build_lines_for_users')
@mock.patch('lingo.invoicing.utils.generate_invoices_from_lines')
def test_generate_invoices(mock_generate, mock_lines, mock_users, mock_agendas, mock_lock):
    regie = Regie.objects.create(label='Regie')
    agenda1 = Agenda.objects.create(label='Agenda 1')
    agenda2 = Agenda.objects.create(label='Agenda 2')
    Agenda.objects.create(label='Agenda 3')
    campaign = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_debit=datetime.date(2022, 11, 15),
    )

    mock_agendas.return_value = [agenda1, agenda2]
    mock_users.return_value = {'foo': (), 'bar': ()}

    # check only calls between functions
    with mock.patch('lingo.invoicing.models.CampaignAsyncJob.run') as mock_run:
        campaign.generate()
        assert mock_run.call_args_list == [mock.call()]

    pool = Pool.objects.latest('pk')
    assert pool.campaign == campaign
    assert pool.draft is True

    assert CampaignAsyncJob.objects.count() == 1
    assert PoolAsyncJob.objects.count() == 0
    cjob = CampaignAsyncJob.objects.get()
    assert cjob.campaign == campaign
    assert cjob.params == {'draft_pool_id': pool.pk, 'force_cron': False}
    assert cjob.action == 'generate'
    assert cjob.status == 'registered'
    with mock.patch('lingo.invoicing.models.PoolAsyncJob.run') as mock_run:
        cjob.run()
        assert mock_run.call_args_list == [mock.call(), mock.call(), mock.call()]
    assert cjob.status == 'completed'
    assert cjob.total_count == 5
    assert cjob.current_count == 5
    assert CampaignAsyncJob.objects.count() == 1
    assert PoolAsyncJob.objects.count() == 3
    pjob1, pjob2, pjob3 = list(PoolAsyncJob.objects.all())
    assert pjob1.pool == pool
    assert pjob1.users == {'bar': []}
    assert pjob1.campaign_job == cjob
    assert pjob1.params == {'force_cron': False}
    assert pjob1.action == 'generate_invoices'
    assert pjob1.status == 'registered'
    assert pjob2.pool == pool
    assert pjob2.users == {'foo': []}
    assert pjob2.campaign_job == cjob
    assert pjob2.params == {'force_cron': False}
    assert pjob2.action == 'generate_invoices'
    assert pjob2.status == 'registered'
    assert pjob3.pool == pool
    assert pjob3.users == {}
    assert pjob3.campaign_job == cjob
    assert pjob3.params == {'force_cron': False}
    assert pjob3.action == 'finalize_invoices'
    assert pjob3.status == 'registered'

    pjob3.run()
    assert pjob3.status == 'waiting'

    pjob1.run()
    assert pjob1.status == 'completed'
    pjob3.run()
    assert pjob3.status == 'waiting'

    pjob2.run()
    assert pjob2.status == 'completed'
    pjob3.run()
    assert pjob3.status == 'completed'

    assert mock_lock.call_args_list == [
        mock.call(
            agenda_slugs=['agenda-1', 'agenda-2'],
            date_start=datetime.date(2022, 9, 1),
            date_end=datetime.date(2022, 10, 1),
        )
    ]
    assert mock_agendas.call_args_list == [
        mock.call(pool=pool),
        mock.call(pool=pool),
        mock.call(pool=pool),
        mock.call(pool=pool),
    ]
    assert mock_users.call_args_list == [
        mock.call(
            agendas=[agenda1, agenda2],
            pool=pool,
        )
    ]
    assert mock_lines.call_args_list == [
        mock.call(
            agendas=[agenda1, agenda2],
            users={'bar': []},
            pool=pool,
            job=pjob1,
        ),
        mock.call(
            agendas=[agenda1, agenda2],
            users={'foo': []},
            pool=pool,
            job=pjob2,
        ),
    ]
    assert mock_generate.call_args_list == [mock.call(pool=pool, job=pjob3)]


@mock.patch('lingo.invoicing.models.campaign.lock_events_check')
@mock.patch('lingo.invoicing.utils.get_agendas')
@mock.patch('lingo.invoicing.utils.get_users_from_subscriptions')
@mock.patch('lingo.invoicing.utils.build_lines_for_users')
@mock.patch('lingo.invoicing.utils.generate_invoices_from_lines')
def test_generate_invoices_errors(mock_generate, mock_lines, mock_users, mock_agendas, mock_lock):
    regie = Regie.objects.create(label='Regie')
    agenda1 = Agenda.objects.create(label='Agenda 1')
    agenda2 = Agenda.objects.create(label='Agenda 2')
    Agenda.objects.create(label='Agenda 3')
    campaign = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_debit=datetime.date(2022, 11, 15),
    )

    mock_lock.side_effect = ChronoError('foo baz')
    mock_agendas.return_value = [agenda1, agenda2]
    mock_users.return_value = {'foo': (), 'bar': ()}
    campaign.generate()
    pool = Pool.objects.latest('pk')
    assert pool.status == 'failed'
    assert pool.exception == 'foo baz'

    mock_lock.side_effect = None
    mock_agendas.return_value = [agenda1, agenda2]
    mock_users.side_effect = ChronoError('foo bar')
    campaign.generate()
    pool = Pool.objects.latest('pk')
    assert pool.status == 'failed'
    assert pool.exception == 'foo bar'


def test_promote_pool():
    today = now().date()
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
    old_pool = Pool.objects.create(
        campaign=campaign,
        draft=True,
    )
    pool = Pool.objects.create(
        campaign=campaign,
        draft=True,
        completed_at=now(),
        status='completed',
    )
    other_campaign = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_debit=datetime.date(2022, 11, 15),
    )
    other_pool = Pool.objects.create(
        campaign=other_campaign,
        draft=True,
    )

    invoice1 = DraftInvoice.objects.create(
        date_publication=campaign.date_publication,
        date_payment_deadline=campaign.date_payment_deadline,
        date_due=campaign.date_due,
        regie=regie,
        pool=pool,
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Last1',
        payer_address='41 rue des kangourous\n99999 Kangourou Ville',
        payer_email='email1',
        payer_phone='phone1',
        payer_direct_debit=False,
        origin='campaign',
    )
    iline11 = DraftInvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        label='Label 11',
        quantity=1,
        unit_amount=1,
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
        details={'foo': 'baz'},
        event_slug='agenda@label-11',
        event_label='Label 11',
        agenda_slug='agenda',
        activity_label='Agenda 1',
        invoice=invoice1,
        pool=pool,
    )
    line11 = DraftJournalLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        slug='label-11',
        label='Label 11',
        amount=1,
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Last1',
        payer_address='41 rue des kangourous\n99999 Kangourou Ville',
        payer_email='email1',
        payer_phone='phone1',
        payer_direct_debit=False,
        status='success',
        event={'foo': 'bar'},
        pricing_data={'foo': 'baz'},
        pool=pool,
        invoice_line=iline11,
    )
    injected_line12 = InjectedLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        slug='label-12',
        label='Label 12',
        amount=2,
        user_external_id='user:2',
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Last1',
        payer_address='41 rue des kangourous\n99999 Kangourou Ville',
        payer_direct_debit=False,
        regie=regie,
    )
    iline12 = DraftInvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        label='Label 12',
        quantity=1,
        unit_amount=2,
        user_external_id='user:2',
        user_first_name='User2',
        user_last_name='Name2',
        invoice=invoice1,
        pool=pool,
    )
    line12 = DraftJournalLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        slug='label-12',
        label='Label 12',
        amount=2,
        user_external_id='user:2',
        user_first_name='User2',
        user_last_name='Name2',
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Last1',
        payer_address='41 rue des kangourous\n99999 Kangourou Ville',
        payer_email='email1',
        payer_phone='phone1',
        payer_direct_debit=False,
        status='success',
        from_injected_line=injected_line12,
        pool=pool,
        invoice_line=iline12,
    )
    invoice2 = DraftInvoice.objects.create(
        date_publication=campaign.date_publication,
        date_payment_deadline=campaign.date_payment_deadline,
        date_due=campaign.date_due,
        date_debit=campaign.date_debit,
        regie=regie,
        pool=pool,
        payer_external_id='payer:2',
        payer_first_name='First2',
        payer_last_name='Last2',
        payer_address='42 rue des kangourous\n99999 Kangourou Ville',
        payer_email='email2',
        payer_phone='phone2',
        payer_direct_debit=True,
        origin='campaign',
    )
    injected_line21 = InjectedLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        slug='label-21',
        label='Label 21',
        amount=1,
        user_external_id='user:2',
        payer_external_id='payer:2',
        payer_first_name='First2',
        payer_last_name='Last2',
        payer_address='42 rue des kangourous\n99999 Kangourou Ville',
        payer_direct_debit=True,
        regie=regie,
    )
    iline21 = DraftInvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        label='Label 21',
        quantity=1,
        unit_amount=1,
        event_slug='label-21',
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
        invoice=invoice2,
        pool=pool,
    )
    line21 = DraftJournalLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        slug='label-21',
        label='Label 21',
        amount=1,
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
        payer_external_id='payer:2',
        payer_first_name='First2',
        payer_last_name='Last2',
        payer_address='42 rue des kangourous\n99999 Kangourou Ville',
        payer_email='email2',
        payer_phone='phone2',
        payer_direct_debit=True,
        status='success',
        from_injected_line=injected_line21,
        pool=pool,
        invoice_line=iline21,
    )
    iline22 = DraftInvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        label='Label 22',
        quantity=1,
        unit_amount=2,
        user_external_id='user:2',
        user_first_name='User2',
        user_last_name='Name2',
        invoice=invoice2,
        pool=pool,
    )
    line22 = DraftJournalLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        slug='label-22',
        label='Label 22',
        amount=2,
        user_external_id='user:2',
        user_first_name='User2',
        user_last_name='Name2',
        payer_external_id='payer:2',
        payer_first_name='First2',
        payer_last_name='Last2',
        payer_address='42 rue des kangourous\n99999 Kangourou Ville',
        payer_email='email2',
        payer_phone='phone2',
        payer_direct_debit=True,
        status='success',
        pool=pool,
        invoice_line=iline22,
    )
    orphan_line1 = DraftJournalLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        slug='orphan-1',
        label='Orphan 1',
        amount=0,
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Last1',
        payer_address='41 rue des kangourous\n99999 Kangourou Ville',
        payer_email='email1',
        payer_phone='phone1',
        payer_direct_debit=False,
        status='error',
        pool=pool,
    )
    orphan_line2 = DraftJournalLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        slug='orphan-2',
        label='Orphan 2',
        amount=0,
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Last1',
        payer_address='41 rue des kangourous\n99999 Kangourou Ville',
        payer_email='email1',
        payer_phone='phone1',
        payer_direct_debit=False,
        status='warning',
        pool=pool,
    )

    invoice3 = DraftInvoice.objects.create(
        date_publication=campaign.date_publication,
        date_payment_deadline=campaign.date_payment_deadline,
        date_due=campaign.date_due,
        regie=regie,
        pool=pool,
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Last1',
        payer_address='41 rue des kangourous\n99999 Kangourou Ville',
        payer_email='email1',
        payer_phone='phone1',
        payer_direct_debit=False,
        origin='campaign',
    )

    invoice4 = DraftInvoice.objects.create(
        date_publication=campaign.date_publication,
        date_payment_deadline=campaign.date_payment_deadline,
        date_due=campaign.date_due,
        regie=regie,
        pool=pool,
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Last1',
        payer_address='41 rue des kangourous\n99999 Kangourou Ville',
        payer_email='email1',
        payer_phone='phone1',
        payer_direct_debit=False,
        origin='campaign',
    )
    iline41 = DraftInvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        label='Label 41',
        quantity=1,
        unit_amount=-1,
        event_slug='label-41',
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
        details={'foo': 'baz'},
        invoice=invoice4,
        pool=pool,
    )
    line41 = DraftJournalLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        slug='label-41',
        label='Label 41',
        amount=-1,
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
        payer_external_id='payer:2',
        payer_first_name='First2',
        payer_last_name='Last2',
        payer_address='42 rue des kangourous\n99999 Kangourou Ville',
        payer_email='email2',
        payer_phone='phone2',
        payer_direct_debit=True,
        status='success',
        pool=pool,
        invoice_line=iline41,
    )

    old_invoice = DraftInvoice.objects.create(
        date_publication=campaign.date_publication,
        date_payment_deadline=campaign.date_payment_deadline,
        date_due=campaign.date_due,
        regie=regie,
        pool=old_pool,
        payer_external_id='payer:1',
    )
    old_iline = DraftInvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        label='Old 1',
        quantity=1,
        unit_amount=1,
        event_slug='old-1',
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
        invoice=old_invoice,
        pool=old_pool,
    )
    DraftJournalLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        slug='old-1',
        label='Old 1',
        amount=1,
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
        payer_external_id='payer:1',
        status='success',
        pool=old_pool,
        invoice_line=old_iline,
    )
    old_injected_line2 = InjectedLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        slug='old-2',
        label='Old 2',
        amount=2,
        user_external_id='user:2',
        payer_external_id='payer:1',
        regie=regie,
    )
    old_iline2 = DraftInvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        label='Old 2',
        quantity=1,
        unit_amount=2,
        event_slug='old-2',
        user_external_id='user:2',
        user_first_name='User2',
        user_last_name='Name2',
        invoice=old_invoice,
        pool=old_pool,
    )
    DraftJournalLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        slug='old-2',
        label='Old 2',
        amount=2,
        user_external_id='user:2',
        user_first_name='User2',
        user_last_name='Name2',
        payer_external_id='payer:1',
        status='success',
        from_injected_line=old_injected_line2,
        pool=old_pool,
        invoice_line=old_iline2,
    )

    other_invoice = DraftInvoice.objects.create(
        date_publication=campaign.date_publication,
        date_payment_deadline=campaign.date_payment_deadline,
        date_due=campaign.date_due,
        regie=regie,
        pool=other_pool,
        payer_external_id='payer:1',
    )
    other_iline = DraftInvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        label='Other 1',
        quantity=1,
        unit_amount=1,
        event_slug='other-1',
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
        invoice=other_invoice,
        pool=other_pool,
    )
    DraftJournalLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        slug='other-1',
        label='Other 1',
        amount=1,
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
        payer_external_id='payer:1',
        status='success',
        pool=other_pool,
        invoice_line=other_iline,
    )
    other_injected_line2 = InjectedLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        slug='other-2',
        label='Other 2',
        amount=2,
        user_external_id='user:2',
        payer_external_id='payer:1',
        regie=regie,
    )
    other_iline2 = DraftInvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        label='Other 2',
        quantity=1,
        unit_amount=2,
        event_slug='other-2',
        user_external_id='user:2',
        user_first_name='User2',
        user_last_name='Name2',
        invoice=other_invoice,
        pool=other_pool,
    )
    DraftJournalLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        slug='other-2',
        label='Other 2',
        amount=2,
        user_external_id='user:2',
        user_first_name='User2',
        user_last_name='Name2',
        payer_external_id='payer:1',
        status='success',
        from_injected_line=other_injected_line2,
        pool=other_pool,
        invoice_line=other_iline2,
    )
    # refresh amounts
    iline11.refresh_from_db()
    iline12.refresh_from_db()
    iline21.refresh_from_db()
    iline22.refresh_from_db()
    iline41.refresh_from_db()
    invoice1.refresh_from_db()
    invoice2.refresh_from_db()
    invoice4.refresh_from_db()

    # orphan invoice
    orphan_invoice = DraftInvoice.objects.create(
        date_publication=campaign.date_publication,
        date_payment_deadline=campaign.date_payment_deadline,
        date_due=campaign.date_due,
        regie=regie,
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Last1',
        payer_address='41 rue des kangourous\n99999 Kangourou Ville',
        payer_email='email1',
        payer_phone='phone1',
        payer_direct_debit=False,
    )
    orphan_iline = DraftInvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        label='Orphan 3',
        quantity=1,
        unit_amount=1,
        event_slug='orphan-3',
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
        invoice=orphan_invoice,
    )
    DraftJournalLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        slug='orphan-3',
        label='Orphan 3',
        amount=1,
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Last1',
        payer_address='41 rue des kangourous\n99999 Kangourou Ville',
        payer_email='email1',
        payer_phone='phone1',
        payer_direct_debit=False,
        status='success',
        invoice_line=orphan_iline,
    )

    assert Campaign.objects.count() == 2
    assert Pool.objects.count() == 3
    assert Pool.objects.filter(draft=False).count() == 0
    assert DraftInvoice.objects.count() == 7
    assert DraftInvoiceLine.objects.count() == 10
    assert DraftJournalLine.objects.count() == 12
    assert Invoice.objects.count() == 0
    assert InvoiceLine.objects.count() == 0
    assert Credit.objects.count() == 0
    assert CreditLine.objects.count() == 0
    assert JournalLine.objects.count() == 0
    assert InjectedLine.objects.count() == 4

    with mock.patch('lingo.invoicing.models.CampaignAsyncJob.run') as mock_run:
        pool.promote()
        assert mock_run.call_args_list == [mock.call()]

    final_pool = Pool.objects.filter(draft=False).get()

    assert CampaignAsyncJob.objects.count() == 1
    assert PoolAsyncJob.objects.count() == 0
    cjob = CampaignAsyncJob.objects.get()
    assert cjob.campaign == campaign
    assert cjob.params == {
        'draft_pool_id': pool.pk,
        'final_pool_id': final_pool.pk,
    }
    assert cjob.action == 'populate_from_draft'
    assert cjob.status == 'registered'
    cjob.run()
    assert cjob.total_count == 11
    assert cjob.current_count == 11
    assert cjob.status == 'completed'

    def test_counts():
        assert Campaign.objects.count() == 2
        assert Pool.objects.count() == 4
        assert Pool.objects.filter(draft=False).count() == 1
        assert DraftInvoice.objects.count() == 7
        assert DraftInvoiceLine.objects.count() == 10
        assert DraftJournalLine.objects.count() == 12
        assert Invoice.objects.count() == 3
        assert InvoiceLine.objects.count() == 4
        assert Credit.objects.count() == 1
        assert CreditLine.objects.count() == 1
        assert JournalLine.objects.count() == 7
        assert InjectedLine.objects.count() == 4
        assert Counter.objects.get(regie=regie, kind='invoice', name=today.strftime('%y')).value == 3
        assert Counter.objects.get(regie=regie, kind='credit', name=today.strftime('%y')).value == 1

    test_counts()

    final_pool.refresh_from_db()
    assert final_pool.campaign == campaign
    assert final_pool.created_at > pool.created_at
    assert final_pool.completed_at > pool.completed_at
    assert final_pool.status == 'completed'
    assert final_pool.exception == ''

    final_invoice1 = Invoice.objects.order_by('pk')[0]
    assert final_invoice1.date_publication == invoice1.date_publication
    assert final_invoice1.date_payment_deadline == invoice1.date_payment_deadline
    assert final_invoice1.date_due == invoice1.date_due
    assert final_invoice1.date_debit == invoice1.date_debit
    assert final_invoice1.regie == regie
    assert final_invoice1.pool == final_pool
    assert final_invoice1.payer_external_id == invoice1.payer_external_id
    assert final_invoice1.payer_first_name == invoice1.payer_first_name
    assert final_invoice1.payer_last_name == invoice1.payer_last_name
    assert final_invoice1.payer_address == invoice1.payer_address
    assert final_invoice1.payer_email == invoice1.payer_email
    assert final_invoice1.payer_phone == invoice1.payer_phone
    assert final_invoice1.payer_direct_debit == invoice1.payer_direct_debit
    assert final_invoice1.total_amount == invoice1.total_amount == 3
    assert final_invoice1.number == 1
    assert final_invoice1.formatted_number == 'F%02d-%s-0000001' % (regie.pk, today.strftime('%y-%m'))
    assert final_invoice1.origin == 'campaign'

    final_iline11 = InvoiceLine.objects.order_by('pk')[0]
    assert final_iline11.event_date == iline11.event_date
    assert final_iline11.label == iline11.label
    assert final_iline11.quantity == iline11.quantity
    assert final_iline11.unit_amount == iline11.unit_amount
    assert final_iline11.total_amount == iline11.total_amount
    assert final_iline11.user_external_id == iline11.user_external_id
    assert final_iline11.user_first_name == iline11.user_first_name
    assert final_iline11.user_last_name == iline11.user_last_name
    assert final_iline11.details
    assert final_iline11.details == iline11.details
    assert final_iline11.event_slug == iline11.event_slug
    assert final_iline11.event_label == iline11.event_label
    assert final_iline11.agenda_slug == iline11.agenda_slug
    assert final_iline11.activity_label == iline11.activity_label
    assert final_iline11.pool == final_pool
    assert final_iline11.invoice == final_invoice1
    final_line11 = JournalLine.objects.order_by('pk')[0]
    assert final_line11.event_date == line11.event_date
    assert final_line11.slug == line11.slug
    assert final_line11.label == line11.label
    assert final_line11.amount == line11.amount
    assert final_line11.user_external_id == line11.user_external_id
    assert final_line11.user_first_name == line11.user_first_name
    assert final_line11.user_last_name == line11.user_last_name
    assert final_line11.payer_external_id == line11.payer_external_id
    assert final_line11.payer_first_name == line11.payer_first_name
    assert final_line11.payer_last_name == line11.payer_last_name
    assert final_line11.payer_address == line11.payer_address
    assert final_line11.payer_email == line11.payer_email
    assert final_line11.payer_phone == line11.payer_phone
    assert final_line11.payer_direct_debit == line11.payer_direct_debit
    assert final_line11.event == line11.event
    assert final_line11.pricing_data == line11.pricing_data
    assert final_line11.status == line11.status
    assert final_line11.pool == final_pool
    assert final_line11.from_injected_line is None
    assert final_line11.invoice_line == final_iline11

    final_iline12 = InvoiceLine.objects.order_by('pk')[1]
    assert final_iline12.event_date == iline12.event_date
    assert final_iline12.label == iline12.label
    assert final_iline12.quantity == iline12.quantity
    assert final_iline12.unit_amount == iline12.unit_amount
    assert final_iline12.total_amount == iline12.total_amount
    assert final_iline12.user_external_id == iline12.user_external_id
    assert final_iline12.user_first_name == iline12.user_first_name
    assert final_iline12.user_last_name == iline12.user_last_name
    assert final_iline12.details == iline12.details
    assert final_iline12.event_slug == iline12.event_slug
    assert final_iline12.event_label == iline12.event_label
    assert final_iline12.agenda_slug == iline12.agenda_slug
    assert final_iline12.activity_label == iline12.activity_label
    assert final_iline12.pool == final_pool
    assert final_iline12.invoice == final_invoice1
    final_line12 = JournalLine.objects.order_by('pk')[1]
    assert final_line12.event_date == line12.event_date
    assert final_line12.slug == line12.slug
    assert final_line12.label == line12.label
    assert final_line12.amount == line12.amount
    assert final_line12.user_external_id == line12.user_external_id
    assert final_line12.user_first_name == line12.user_first_name
    assert final_line12.user_last_name == line12.user_last_name
    assert final_line12.payer_external_id == line12.payer_external_id
    assert final_line12.payer_first_name == line12.payer_first_name
    assert final_line12.payer_last_name == line12.payer_last_name
    assert final_line12.payer_address == line12.payer_address
    assert final_line12.payer_email == line12.payer_email
    assert final_line12.payer_phone == line12.payer_phone
    assert final_line12.payer_direct_debit == line12.payer_direct_debit
    assert final_line12.event == line12.event
    assert final_line12.pricing_data == line12.pricing_data
    assert final_line12.status == line12.status
    assert final_line12.pool == final_pool
    assert final_line12.from_injected_line == injected_line12
    assert final_line12.invoice_line == final_iline12

    final_invoice2 = Invoice.objects.order_by('pk')[1]
    assert final_invoice2.date_publication == invoice2.date_publication
    assert final_invoice2.date_payment_deadline == invoice2.date_payment_deadline
    assert final_invoice2.date_due == invoice2.date_due
    assert final_invoice2.date_debit == invoice2.date_debit
    assert final_invoice2.regie == regie
    assert final_invoice2.pool == final_pool
    assert final_invoice2.payer_external_id == invoice2.payer_external_id
    assert final_invoice2.payer_first_name == invoice2.payer_first_name
    assert final_invoice2.payer_last_name == invoice2.payer_last_name
    assert final_invoice2.payer_address == invoice2.payer_address
    assert final_invoice2.payer_email == invoice2.payer_email
    assert final_invoice2.payer_phone == invoice2.payer_phone
    assert final_invoice2.payer_direct_debit == invoice2.payer_direct_debit
    assert final_invoice2.total_amount == invoice2.total_amount == 3
    assert final_invoice2.number == 2
    assert final_invoice2.formatted_number == 'F%02d-%s-0000002' % (regie.pk, today.strftime('%y-%m'))
    assert final_invoice2.origin == 'campaign'

    final_iline21 = InvoiceLine.objects.order_by('pk')[2]
    assert final_iline21.event_date == iline21.event_date
    assert final_iline21.label == iline21.label
    assert final_iline21.quantity == iline21.quantity
    assert final_iline21.unit_amount == iline21.unit_amount
    assert final_iline21.total_amount == iline21.total_amount
    assert final_iline21.user_external_id == iline21.user_external_id
    assert final_iline21.user_first_name == iline21.user_first_name
    assert final_iline21.user_last_name == iline21.user_last_name
    assert final_iline21.details == iline21.details
    assert final_iline21.event_slug == iline21.event_slug
    assert final_iline21.event_label == iline21.event_label
    assert final_iline21.agenda_slug == iline21.agenda_slug
    assert final_iline21.activity_label == iline21.activity_label
    assert final_iline21.pool == final_pool
    assert final_iline21.invoice == final_invoice2
    final_line21 = JournalLine.objects.order_by('pk')[2]
    assert final_line21.event_date == line21.event_date
    assert final_line21.slug == line21.slug
    assert final_line21.label == line21.label
    assert final_line21.amount == line21.amount
    assert final_line21.user_external_id == line21.user_external_id
    assert final_line21.user_first_name == line21.user_first_name
    assert final_line21.user_last_name == line21.user_last_name
    assert final_line21.payer_external_id == line21.payer_external_id
    assert final_line21.payer_first_name == line21.payer_first_name
    assert final_line21.payer_last_name == line21.payer_last_name
    assert final_line21.payer_address == line21.payer_address
    assert final_line21.payer_email == line21.payer_email
    assert final_line21.payer_phone == line21.payer_phone
    assert final_line21.payer_direct_debit == line21.payer_direct_debit
    assert final_line21.event == line21.event
    assert final_line21.pricing_data == line21.pricing_data
    assert final_line21.status == line21.status
    assert final_line21.pool == final_pool
    assert final_line21.from_injected_line == injected_line21
    assert final_line21.invoice_line == final_iline21

    final_iline22 = InvoiceLine.objects.order_by('pk')[3]
    assert final_iline22.event_date == iline22.event_date
    assert final_iline22.label == iline22.label
    assert final_iline22.quantity == iline22.quantity
    assert final_iline22.unit_amount == iline22.unit_amount
    assert final_iline22.total_amount == iline22.total_amount
    assert final_iline22.user_external_id == iline22.user_external_id
    assert final_iline22.user_first_name == iline22.user_first_name
    assert final_iline22.user_last_name == iline22.user_last_name
    assert final_iline22.details == iline22.details
    assert final_iline22.event_slug == iline22.event_slug
    assert final_iline22.event_label == iline22.event_label
    assert final_iline22.agenda_slug == iline22.agenda_slug
    assert final_iline22.activity_label == iline22.activity_label
    assert final_iline22.pool == final_pool
    assert final_iline22.invoice == final_invoice2
    final_line22 = JournalLine.objects.order_by('pk')[3]
    assert final_line22.event_date == line22.event_date
    assert final_line22.slug == line22.slug
    assert final_line22.label == line22.label
    assert final_line22.amount == line22.amount
    assert final_line22.user_external_id == line22.user_external_id
    assert final_line22.user_first_name == line22.user_first_name
    assert final_line22.user_last_name == line22.user_last_name
    assert final_line22.payer_external_id == line22.payer_external_id
    assert final_line22.payer_first_name == line22.payer_first_name
    assert final_line22.payer_last_name == line22.payer_last_name
    assert final_line22.payer_address == line22.payer_address
    assert final_line22.payer_email == line22.payer_email
    assert final_line22.payer_phone == line22.payer_phone
    assert final_line22.payer_direct_debit == line22.payer_direct_debit
    assert final_line22.event == line22.event
    assert final_line22.pricing_data == line22.pricing_data
    assert final_line22.status == line22.status
    assert final_line22.pool == final_pool
    assert final_line22.from_injected_line is None
    assert final_line22.invoice_line == final_iline22

    credit = Credit.objects.order_by('pk')[0]
    assert credit.date_publication == invoice4.date_publication
    assert credit.regie == regie
    assert credit.pool == final_pool
    assert credit.payer_external_id == invoice4.payer_external_id
    assert credit.payer_first_name == invoice4.payer_first_name
    assert credit.payer_last_name == invoice4.payer_last_name
    assert credit.total_amount == -invoice4.total_amount == 1
    assert credit.number == 1
    assert credit.formatted_number == 'A%02d-%s-0000001' % (regie.pk, today.strftime('%y-%m'))
    assert credit.origin == 'campaign'

    credit_line41 = CreditLine.objects.order_by('pk')[0]
    assert credit_line41.event_date == iline41.event_date
    assert credit_line41.label == iline41.label
    assert credit_line41.quantity == -iline41.quantity
    assert credit_line41.unit_amount == iline41.unit_amount
    assert credit_line41.total_amount == -iline41.total_amount
    assert credit_line41.user_external_id == iline41.user_external_id
    assert credit_line41.user_first_name == iline41.user_first_name
    assert credit_line41.user_last_name == iline41.user_last_name
    assert credit_line41.details
    assert credit_line41.details == iline41.details
    assert credit_line41.event_slug == iline41.event_slug
    assert credit_line41.event_label == iline41.event_label
    assert credit_line41.agenda_slug == iline41.agenda_slug
    assert credit_line41.activity_label == iline41.activity_label
    assert credit_line41.pool == final_pool
    assert credit_line41.credit == credit
    final_line41 = JournalLine.objects.order_by('pk')[6]
    assert final_line41.event_date == line41.event_date
    assert final_line41.slug == line41.slug
    assert final_line41.label == line41.label
    assert final_line41.amount == line41.amount
    assert final_line41.user_external_id == line41.user_external_id
    assert final_line41.user_first_name == line41.user_first_name
    assert final_line41.user_last_name == line41.user_last_name
    assert final_line41.payer_external_id == line41.payer_external_id
    assert final_line41.payer_first_name == line41.payer_first_name
    assert final_line41.payer_last_name == line41.payer_last_name
    assert final_line41.payer_address == line41.payer_address
    assert final_line41.payer_email == line41.payer_email
    assert final_line41.payer_phone == line41.payer_phone
    assert final_line41.payer_direct_debit == line41.payer_direct_debit
    assert final_line41.event == line41.event
    assert final_line41.pricing_data == line41.pricing_data
    assert final_line41.status == line41.status
    assert final_line41.pool == final_pool
    assert final_line41.from_injected_line is None
    assert final_line41.credit_line == credit_line41

    final_orphan_line1 = JournalLine.objects.order_by('pk')[4]
    assert final_orphan_line1.event_date == orphan_line1.event_date
    assert final_orphan_line1.slug == orphan_line1.slug
    assert final_orphan_line1.label == orphan_line1.label
    assert final_orphan_line1.amount == orphan_line1.amount
    assert final_orphan_line1.user_external_id == orphan_line1.user_external_id
    assert final_orphan_line1.user_first_name == orphan_line1.user_first_name
    assert final_orphan_line1.user_last_name == orphan_line1.user_last_name
    assert final_orphan_line1.payer_external_id == orphan_line1.payer_external_id
    assert final_orphan_line1.payer_first_name == orphan_line1.payer_first_name
    assert final_orphan_line1.payer_last_name == orphan_line1.payer_last_name
    assert final_orphan_line1.payer_address == orphan_line1.payer_address
    assert final_orphan_line1.payer_email == orphan_line1.payer_email
    assert final_orphan_line1.payer_phone == orphan_line1.payer_phone
    assert final_orphan_line1.payer_direct_debit == orphan_line1.payer_direct_debit
    assert final_orphan_line1.event == orphan_line1.event
    assert final_orphan_line1.pricing_data == orphan_line1.pricing_data
    assert final_orphan_line1.status == orphan_line1.status
    assert final_orphan_line1.pool == final_pool
    assert final_orphan_line1.from_injected_line is None
    assert final_orphan_line1.invoice_line is None

    final_orphan_line2 = JournalLine.objects.order_by('pk')[5]
    assert final_orphan_line2.event_date == orphan_line2.event_date
    assert final_orphan_line2.slug == orphan_line2.slug
    assert final_orphan_line2.label == orphan_line2.label
    assert final_orphan_line2.amount == orphan_line2.amount
    assert final_orphan_line2.user_external_id == orphan_line2.user_external_id
    assert final_orphan_line2.user_first_name == orphan_line2.user_first_name
    assert final_orphan_line2.user_last_name == orphan_line2.user_last_name
    assert final_orphan_line2.payer_external_id == orphan_line2.payer_external_id
    assert final_orphan_line2.payer_first_name == orphan_line2.payer_first_name
    assert final_orphan_line2.payer_last_name == orphan_line2.payer_last_name
    assert final_orphan_line2.payer_address == orphan_line2.payer_address
    assert final_orphan_line2.payer_email == orphan_line2.payer_email
    assert final_orphan_line2.payer_phone == orphan_line2.payer_phone
    assert final_orphan_line2.payer_direct_debit == orphan_line2.payer_direct_debit
    assert final_orphan_line2.event == orphan_line2.event
    assert final_orphan_line2.pricing_data == orphan_line2.pricing_data
    assert final_orphan_line2.status == orphan_line2.status
    assert final_orphan_line2.pool == final_pool
    assert final_orphan_line2.from_injected_line is None
    assert final_orphan_line2.invoice_line is None

    final_invoice3 = Invoice.objects.order_by('pk')[2]
    assert final_invoice3.date_publication == invoice3.date_publication
    assert final_invoice3.date_payment_deadline == invoice3.date_payment_deadline
    assert final_invoice3.date_due == invoice3.date_due
    assert final_invoice3.date_debit == invoice3.date_debit
    assert final_invoice3.regie == regie
    assert final_invoice3.pool == final_pool
    assert final_invoice3.payer_external_id == invoice3.payer_external_id
    assert final_invoice3.payer_first_name == invoice3.payer_first_name
    assert final_invoice3.payer_last_name == invoice3.payer_last_name
    assert final_invoice3.payer_direct_debit == invoice3.payer_direct_debit
    assert final_invoice3.total_amount == invoice3.total_amount == 0
    assert final_invoice3.number == 3
    assert final_invoice3.formatted_number == 'F%02d-%s-0000003' % (regie.pk, today.strftime('%y-%m'))
    assert final_invoice3.origin == 'campaign'

    with pytest.raises(PoolPromotionError) as excinfo:
        old_pool.promote()
    assert '%s' % excinfo.value == 'Pool too old'
    test_counts()

    with pytest.raises(PoolPromotionError) as excinfo:
        final_pool.promote()
    assert '%s' % excinfo.value == 'Pool is final'
    test_counts()

    for status in ['registered', 'running', 'failed']:
        other_pool.status = status
        other_pool.save()
        with pytest.raises(PoolPromotionError) as excinfo:
            other_pool.promote()
        assert '%s' % excinfo.value == 'Pool is not completed'
        test_counts()
