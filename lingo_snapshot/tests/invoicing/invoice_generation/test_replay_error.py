import datetime
from unittest import mock

import pytest

from lingo.agendas.models import Agenda
from lingo.invoicing import utils
from lingo.invoicing.models import Campaign, DraftInvoice, DraftInvoiceLine, DraftJournalLine, Pool, Regie
from lingo.pricing.models import Pricing

pytestmark = pytest.mark.django_db


@mock.patch('lingo.invoicing.utils.get_check_status')
@mock.patch('lingo.invoicing.utils.build_lines_for_user')
def test_redo_lines_check_status(mock_lines, mock_status):
    regie = Regie.objects.create(label='Regie')
    agenda = Agenda.objects.create(label='Agenda 1')
    Agenda.objects.create(label='Agenda 2')
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

    original_error_line = DraftJournalLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        slug='agenda-1@event-1',
        user_external_id='user:1',
        user_first_name='First1',
        user_last_name='Last1',
        payer_external_id='payer:1',
        pool=pool,
        status='error',
        amount=0,
    )

    mock_status.return_value = [
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
                'slug': 'event-1',
                'label': 'Event 1',
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

    # no invoice as result, build_lines_for_user returns nothing
    assert (
        utils.redo_lines_for_user_and_event(
            agenda=agenda, agendas_pricings='agendas_pricings', original_error_line=original_error_line
        )
        == []
    )
    assert DraftInvoiceLine.objects.count() == 0
    assert DraftInvoice.objects.count() == 0
    assert DraftJournalLine.objects.count() == 0
    assert mock_status.call_args_list == [
        mock.call(
            agenda_slugs=['agenda-1'],
            user_external_id='user:1',
            date_start=datetime.date(2022, 9, 1),
            date_end=datetime.date(2022, 9, 2),
        )
    ]


@mock.patch('lingo.invoicing.utils.get_check_status')
@mock.patch('lingo.invoicing.utils.build_lines_for_user')
def test_redo_lines_deleted_lines(mock_lines, mock_status):
    regie = Regie.objects.create(label='Regie')
    agenda = Agenda.objects.create(label='Agenda 1')
    Agenda.objects.create(label='Agenda 2')
    campaign = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_debit=datetime.date(2022, 11, 15),
    )
    other_pool = Pool.objects.create(
        campaign=campaign,
        draft=True,
    )
    pool = Pool.objects.create(
        campaign=campaign,
        draft=True,
    )

    original_error_line = DraftJournalLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        slug='agenda-1@event-1',
        user_external_id='user:1',
        user_first_name='First1',
        user_last_name='Last1',
        payer_external_id='payer:1',
        pool=pool,
        status='error',
        amount=0,
    )
    old_line = DraftJournalLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        slug='agenda-1@event-1',
        pool=pool,
        user_external_id='user:1',
        payer_external_id='payer:1',
        status='success',
        amount=42,
    )
    wrong_user_line = DraftJournalLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        slug='agenda-1@event-1',
        pool=pool,
        user_external_id='user:2',
        payer_external_id='payer:2',
        status='success',
        amount=42,
    )
    wrong_slug_line = DraftJournalLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        slug='agenda-1@event-2',
        pool=pool,
        user_external_id='user:1',
        payer_external_id='payer:2',
        status='success',
        amount=42,
    )
    wrong_date_line = DraftJournalLine.objects.create(
        event_date=datetime.date(2022, 9, 2),
        slug='agenda-1@event-1',
        pool=pool,
        user_external_id='user:1',
        payer_external_id='payer:2',
        status='success',
        amount=42,
    )
    other_pool_line = DraftJournalLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        slug='agenda-1@event-1',
        pool=other_pool,
        user_external_id='user:1',
        payer_external_id='payer:2',
        status='success',
        amount=42,
    )

    mock_status.return_value = [
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
    ]

    # no invoice as result, build_lines_for_user returns nothing
    assert (
        utils.redo_lines_for_user_and_event(
            agenda=agenda, agendas_pricings='agendas_pricings', original_error_line=original_error_line
        )
        == []
    )
    assert DraftInvoiceLine.objects.count() == 0
    assert DraftInvoice.objects.count() == 0
    assert DraftJournalLine.objects.count() == 4
    assert mock_status.call_args_list == [
        mock.call(
            agenda_slugs=['agenda-1'],
            user_external_id='user:1',
            date_start=datetime.date(2022, 9, 1),
            date_end=datetime.date(2022, 9, 2),
        )
    ]
    assert mock_lines.call_args_list == [
        mock.call(
            agendas=[agenda],
            agendas_pricings='agendas_pricings',
            user_external_id='user:1',
            user_first_name='First1',
            user_last_name='Last1',
            pool=pool,
            payer_data_cache={},
            check_status_list=[
                {
                    'event': {
                        'agenda': 'agenda-1',
                        'start_datetime': '2022-09-01T12:00:00+02:00',
                        'slug': 'event-1',
                        'label': 'Event 1',
                    },
                    'check_status': {'foo': 'bar1'},
                    'booking': {'foo': 'baz1'},
                }
            ],
        ),
    ]
    # lines about this event and this user were deleted
    assert DraftJournalLine.objects.filter(pk=original_error_line.pk).exists() is False
    assert DraftJournalLine.objects.filter(pk=old_line.pk).exists() is False
    # other lines are not affected
    assert DraftJournalLine.objects.filter(pk=wrong_user_line.pk).exists() is True
    assert DraftJournalLine.objects.filter(pk=wrong_slug_line.pk).exists() is True
    assert DraftJournalLine.objects.filter(pk=wrong_date_line.pk).exists() is True
    assert DraftJournalLine.objects.filter(pk=other_pool_line.pk).exists() is True


@mock.patch('lingo.invoicing.utils.get_check_status')
@mock.patch('lingo.invoicing.utils.build_lines_for_user')
def test_redo_lines_new_invoices(mock_lines, mock_status):
    regie = Regie.objects.create(label='Regie')
    agenda = Agenda.objects.create(label='Agenda 1')
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

    def create_lines(*args, **kwargs):
        line1 = DraftJournalLine.objects.create(
            slug='agenda-1@event-1',
            event_date=datetime.date(2022, 9, 1),
            amount=1,
            user_external_id='user:1',
            payer_external_id='payer:1',
            status='success',
            pool=pool,
        )
        line2 = DraftJournalLine.objects.create(
            slug='agenda-1@event-1',
            event_date=datetime.date(2022, 9, 1),
            amount=2,
            user_external_id='user:1',
            payer_external_id='payer:1',
            status='success',
            pool=pool,
        )
        line3 = DraftJournalLine.objects.create(
            slug='agenda-1@event-1',
            event_date=datetime.date(2022, 9, 1),
            amount=1,
            user_external_id='user:1',
            payer_external_id='payer:2',
            status='success',
            pool=pool,
        )
        return line1, line2, line3

    # lines returned by build_lines_for_user
    mock_lines.side_effect = create_lines

    # existing invoices in this pool
    old_invoice1 = DraftInvoice.objects.create(
        date_publication=campaign.date_publication,
        date_payment_deadline=campaign.date_payment_deadline,
        date_due=campaign.date_due,
        regie=regie,
        pool=pool,
        payer_external_id='payer:1',
    )
    old_line11 = DraftInvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        unit_amount=1,
        quantity=1,
        user_external_id='user:1',
        invoice=old_invoice1,
        pool=pool,
    )
    old_jline11 = DraftJournalLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        slug='agenda-1@event-1',
        amount=1,
        user_external_id='user:1',
        payer_external_id='payer:1',
        status='success',
        pool=pool,
        invoice_line=old_line11,
    )
    old_line12 = DraftInvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        unit_amount=1,
        quantity=1,
        user_external_id='user:2',
        invoice=old_invoice1,
        pool=pool,
    )
    old_jline12 = DraftJournalLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        slug='agenda-1@event-1',
        amount=1,
        user_external_id='user:2',
        payer_external_id='payer:1',
        status='success',
        pool=pool,
        invoice_line=old_line12,
    )
    # other payer and other user
    old_invoice2 = DraftInvoice.objects.create(
        date_publication=campaign.date_publication,
        date_payment_deadline=campaign.date_payment_deadline,
        date_due=campaign.date_due,
        regie=regie,
        pool=pool,
        payer_external_id='payer:3',
    )
    old_line2 = DraftInvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        unit_amount=12,
        quantity=1,
        user_external_id='user:3',
        invoice=old_invoice2,
        pool=pool,
    )
    old_jline2 = DraftJournalLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        slug='agenda-1@event-1',
        amount=12,
        user_external_id='user:3',
        payer_external_id='payer:3',
        status='success',
        pool=pool,
        invoice_line=old_line2,
    )
    # other payer but same user
    old_invoice3 = DraftInvoice.objects.create(
        date_publication=campaign.date_publication,
        date_payment_deadline=campaign.date_payment_deadline,
        date_due=campaign.date_due,
        regie=regie,
        pool=pool,
        payer_external_id='payer:3',
    )
    old_line3 = DraftInvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        unit_amount=1,
        quantity=1,
        user_external_id='user:1',
        invoice=old_invoice3,
        pool=pool,
    )
    old_jline3 = DraftJournalLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        slug='agenda-1@event-1',
        amount=1,
        user_external_id='user:1',
        payer_external_id='payer:3',
        status='success',
        pool=pool,
        invoice_line=old_line3,
    )
    # other payer and other user
    old_invoice4 = DraftInvoice.objects.create(
        date_publication=campaign.date_publication,
        date_payment_deadline=campaign.date_payment_deadline,
        date_due=campaign.date_due,
        regie=regie,
        pool=pool,
        payer_external_id='payer:4',
    )
    old_line4 = DraftInvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        unit_amount=7,
        quantity=1,
        user_external_id='user:4',
        invoice=old_invoice4,
        pool=pool,
    )
    old_jline4 = DraftJournalLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        slug='agenda-1@event-1',
        amount=7,
        user_external_id='user:4',
        payer_external_id='payer:4',
        status='success',
        pool=pool,
        invoice_line=old_line4,
    )
    # other invoice
    other_invoice = DraftInvoice.objects.create(
        date_publication=campaign.date_publication,
        date_payment_deadline=campaign.date_payment_deadline,
        date_due=campaign.date_due,
        regie=regie,
        payer_external_id='payer:1',
    )
    other_line = DraftInvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        unit_amount=13,
        quantity=1,
        user_external_id='user:1',
        invoice=other_invoice,
    )

    new_invoices = utils.redo_lines_for_user_and_event(
        agenda=agenda, agendas_pricings='agendas_pricings', original_error_line=old_jline11
    )
    assert DraftInvoiceLine.objects.count() == 7
    assert DraftInvoice.objects.count() == 5
    assert DraftJournalLine.objects.count() == 6

    # refresh total_amount field (triggered)
    new_invoices = DraftInvoice.objects.filter(pk__in=[inv.pk for inv in new_invoices]).order_by('pk')
    assert len(new_invoices) == 3
    # lines about this event and this user were deleted
    assert DraftJournalLine.objects.filter(pk=old_jline11.pk).exists() is False
    assert DraftJournalLine.objects.filter(pk=old_jline3.pk).exists() is False
    # other lines are not affected
    assert DraftJournalLine.objects.filter(pk=old_jline12.pk).exists() is True
    assert DraftJournalLine.objects.filter(pk=old_jline2.pk).exists() is True
    assert DraftJournalLine.objects.filter(pk=old_jline4.pk).exists() is True
    # invoice of payer:1 and payer:3 were deleted, as payer:1 and payer:3 has lines about user:1
    assert DraftInvoice.objects.filter(pk=old_invoice1.pk).exists() is False
    assert DraftInvoice.objects.filter(pk=old_invoice2.pk).exists() is False
    assert DraftInvoice.objects.filter(pk=old_invoice3.pk).exists() is False
    # invoice of payer:4 were not affected
    assert DraftInvoice.objects.filter(pk=old_invoice4.pk).exists() is True
    old_invoice4.refresh_from_db()
    assert old_invoice4.total_amount == 7
    assert DraftInvoiceLine.objects.filter(pk=old_line4.pk).exists() is True
    # invoice outside the pool were not affected
    assert DraftInvoice.objects.filter(pk=other_invoice.pk).exists() is True
    other_invoice.refresh_from_db()
    assert other_invoice.total_amount == 13
    assert DraftInvoiceLine.objects.filter(pk=other_line.pk).exists() is True

    jlines = DraftJournalLine.objects.exclude(pk__in=[old_jline12.pk, old_jline2.pk, old_jline4.pk]).order_by(
        'pk'
    )
    assert len(jlines) == 3
    old_jline12.refresh_from_db()
    old_jline2.refresh_from_db()
    assert new_invoices[0].total_amount == 4
    assert new_invoices[0].payer_external_id == 'payer:1'
    lines1 = new_invoices[0].lines.all()
    assert len(lines1) == 3
    assert lines1[0].total_amount == 1
    assert lines1[0].user_external_id == 'user:2'
    assert jlines[0].invoice_line == lines1[1]
    assert lines1[1].total_amount == 1
    assert lines1[1].user_external_id == 'user:1'
    assert jlines[1].invoice_line == lines1[2]
    assert lines1[2].total_amount == 2
    assert lines1[2].user_external_id == 'user:1'
    assert old_jline12.invoice_line == lines1[0]
    assert new_invoices[1].total_amount == 12
    assert new_invoices[1].payer_external_id == 'payer:3'
    line2 = new_invoices[1].lines.get()
    assert line2.total_amount == 12
    assert line2.user_external_id == 'user:3'
    assert old_jline2.invoice_line == line2
    assert new_invoices[2].total_amount == 1
    assert new_invoices[2].payer_external_id == 'payer:2'
    line3 = new_invoices[2].lines.get()
    assert line3.total_amount == 1
    assert line3.user_external_id == 'user:1'
    assert jlines[2].invoice_line == line3


@mock.patch('lingo.invoicing.utils.redo_lines_for_user_and_event')
def test_replay_error_agenda(mock_redo):
    regie = Regie.objects.create(label='Regie')
    Agenda.objects.create(label='Agenda 2')
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

    original_error_line = DraftJournalLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        slug='event-1',
        user_external_id='user:1',
        user_first_name='First1',
        user_last_name='Last1',
        payer_external_id='payer:1',
        pool=pool,
        status='error',
        amount=0,
    )

    with pytest.raises(Agenda.DoesNotExist):
        utils.replay_error(original_error_line)
    assert mock_redo.call_args_list == []

    original_error_line.slug = 'agenda-1@event-1'
    original_error_line.save()
    with pytest.raises(Agenda.DoesNotExist):
        utils.replay_error(original_error_line)
    assert mock_redo.call_args_list == []

    agenda = Agenda.objects.create(label='Agenda 1')
    utils.replay_error(original_error_line)
    assert mock_redo.call_args_list == [
        mock.call(agenda=agenda, agendas_pricings=mock.ANY, original_error_line=original_error_line)
    ]

    original_error_line.slug = 'agenda-1'
    original_error_line.save()
    mock_redo.reset_mock()
    with pytest.raises(Agenda.DoesNotExist):
        utils.replay_error(original_error_line)
    assert mock_redo.call_args_list == []


@mock.patch('lingo.invoicing.utils.redo_lines_for_user_and_event')
def test_replay_error_pricing_dates(mock_redo):
    regie = Regie.objects.create(label='Regie')
    agenda = Agenda.objects.create(label='Agenda 1')
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

    original_error_line = DraftJournalLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        slug='agenda-1@event-1',
        user_external_id='user:1',
        user_first_name='First1',
        user_last_name='Last1',
        payer_external_id='payer:1',
        pool=pool,
        status='error',
        amount=0,
    )

    utils.replay_error(original_error_line)
    assert list(mock_redo.call_args_list[0][1]['agendas_pricings']) == []

    pricing = Pricing.objects.create(
        date_start=datetime.date(year=2022, month=9, day=1),
        date_end=datetime.date(year=2022, month=9, day=2),
    )
    pricing.agendas.add(agenda)
    mock_redo.reset_mock()
    utils.replay_error(original_error_line)
    assert list(mock_redo.call_args_list[0][1]['agendas_pricings']) == [pricing]

    pricing.flat_fee_schedule = True
    pricing.save()
    mock_redo.reset_mock()
    utils.replay_error(original_error_line)
    assert list(mock_redo.call_args_list[0][1]['agendas_pricings']) == []

    pricing.flat_fee_schedule = False
    pricing.date_start = datetime.date(2022, 9, 2)
    pricing.date_end = datetime.date(2022, 9, 3)
    pricing.save()
    mock_redo.reset_mock()
    utils.replay_error(original_error_line)
    assert list(mock_redo.call_args_list[0][1]['agendas_pricings']) == []

    pricing.date_start = datetime.date(2022, 8, 31)
    pricing.date_end = datetime.date(2022, 9, 1)
    pricing.save()
    mock_redo.reset_mock()
    utils.replay_error(original_error_line)
    assert list(mock_redo.call_args_list[0][1]['agendas_pricings']) == []
