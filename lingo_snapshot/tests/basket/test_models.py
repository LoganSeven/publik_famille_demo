import datetime
from unittest import mock

import pytest
from django.utils.timezone import now

from lingo.basket.models import Basket, BasketLine
from lingo.invoicing.models import (
    Campaign,
    Credit,
    CreditAssignment,
    CreditLine,
    DraftInvoice,
    DraftInvoiceLine,
    Invoice,
    InvoiceLine,
    Pool,
    Regie,
)

pytestmark = pytest.mark.django_db


def test_basket_expiration():
    regie = Regie.objects.create(label='Foo')
    draft_invoice = DraftInvoice.objects.create(
        regie=regie,
        date_publication=datetime.date(2023, 4, 21),
        date_payment_deadline=datetime.date(2023, 4, 22),
        date_due=datetime.date(2023, 4, 23),
    )
    invoice = Invoice.objects.create(
        regie=regie,
        date_publication=datetime.date(2023, 4, 21),
        date_payment_deadline=datetime.date(2023, 4, 22),
        date_due=datetime.date(2023, 4, 23),
    )
    InvoiceLine.objects.create(
        label='Event A',
        event_date=datetime.date(2022, 9, 1),
        invoice=invoice,
        quantity=10,
        unit_amount=1,
        user_external_id='user:1',
    )
    basket = Basket.objects.create(
        regie=regie,
        draft_invoice=draft_invoice,
        invoice=invoice,
        expiry_at=now() + datetime.timedelta(minutes=1),
    )
    line = BasketLine.objects.create(
        basket=basket,
        closed=True,
        user_external_id='user:1',
    )
    # the invoice is partially paid with a credit
    credit = Credit.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        regie=regie,
        payer_external_id='payer:1',
    )
    CreditLine.objects.create(
        credit=credit,
        event_date=datetime.date(2022, 9, 1),
        quantity=1,
        unit_amount=1,
    )
    CreditAssignment.objects.create(
        invoice=invoice,
        credit=credit,
        amount=1,
    )
    # other credit used on another invoice
    draft_invoice2 = DraftInvoice.objects.create(
        regie=regie,
        date_publication=datetime.date(2023, 4, 21),
        date_payment_deadline=datetime.date(2023, 4, 22),
        date_due=datetime.date(2023, 4, 23),
    )
    invoice2 = Invoice.objects.create(
        regie=regie,
        date_publication=datetime.date(2023, 4, 21),
        date_payment_deadline=datetime.date(2023, 4, 22),
        date_due=datetime.date(2023, 4, 23),
    )
    InvoiceLine.objects.create(
        label='Event A',
        event_date=datetime.date(2022, 9, 1),
        invoice=invoice2,
        quantity=10,
        unit_amount=1,
        user_external_id='user:1',
    )
    Basket.objects.create(
        regie=regie,
        draft_invoice=draft_invoice2,
        invoice=invoice2,
        expiry_at=now() + datetime.timedelta(minutes=1),
    )
    credit2 = Credit.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        regie=regie,
        payer_external_id='payer:1',
    )
    CreditLine.objects.create(
        credit=credit2,
        event_date=datetime.date(2022, 9, 1),
        quantity=1,
        unit_amount=1,
    )
    CreditAssignment.objects.create(
        invoice=invoice2,
        credit=credit2,
        amount=1,
    )

    for status in ['open', 'tobepaid', 'cancelled', 'expired', 'completed']:
        basket.status = status
        basket.save()
        # expiry_at is not passed, no changes
        Basket.expire_baskets()
        basket.refresh_from_db()
        assert basket.status == status
        assert basket.expired_at is None

    # open basket, expire it immediatly after expiry_at is passed
    assert CreditAssignment.objects.count() == 2
    basket.status = 'open'
    basket.expiry_at = now()
    basket.save()
    with mock.patch('lingo.utils.requests_wrapper.RequestsSession.send') as mock_send:
        Basket.expire_baskets()
    assert [x[0][0].url for x in mock_send.call_args_list] == []
    basket.refresh_from_db()
    assert basket.status == 'expired'
    assert basket.expired_at is not None
    invoice.refresh_from_db()
    assert invoice.cancelled_at is not None
    assert invoice.cancelled_by is None
    assert invoice.cancellation_reason.slug == 'basket-expired'
    assert invoice.cancellation_description == ''
    assert CreditAssignment.objects.count() == 1

    # tobepaid basket, expire it 1 hour after expiry_at is passed
    line.expiration_callback_url = 'http://expiration1.com'
    line.save()
    BasketLine.objects.create(
        basket=basket,
        closed=True,
        user_external_id='user:2',
        expiration_callback_url='http://expiration2.com',
    )

    basket.expired_at = None
    basket.status = 'tobepaid'
    basket.expiry_at = now()
    basket.save()
    with mock.patch('lingo.utils.requests_wrapper.RequestsSession.send') as mock_send:
        Basket.expire_baskets()
    assert [x[0][0].url for x in mock_send.call_args_list] == []
    basket.refresh_from_db()
    assert basket.status == 'tobepaid'
    assert basket.expired_at is None

    basket.expiry_at = now() - datetime.timedelta(minutes=60)
    basket.save()
    with mock.patch('lingo.utils.requests_wrapper.RequestsSession.send') as mock_send:
        Basket.expire_baskets()
    assert [x[0][0].url for x in mock_send.call_args_list] == [
        'http://expiration1.com/',
        'http://expiration2.com/',
    ]
    basket.refresh_from_db()
    assert basket.status == 'expired'
    assert basket.expired_at is not None

    # other status, no changes
    basket.expired_at = None
    basket.expiry_at = now()
    for status in ['cancelled', 'expired', 'completed']:
        basket.status = status
        basket.save()
        Basket.expire_baskets()
        basket.refresh_from_db()
        assert basket.status == status
        assert basket.expired_at is None


def test_basket_amounts_with_draft_invoice():
    regie = Regie.objects.create(label='Foo')
    draft_invoice = DraftInvoice.objects.create(
        regie=regie,
        date_publication=datetime.date(2023, 4, 21),
        date_payment_deadline=datetime.date(2023, 4, 22),
        date_due=datetime.date(2023, 4, 23),
    )

    # empty basket
    basket = Basket.objects.create(
        regie=regie,
        draft_invoice=draft_invoice,
        expiry_at=now() + datetime.timedelta(minutes=1),
        payer_external_id='payer:1',
    )
    assert basket.total_amount == 0
    assert basket.credit_amount == 0
    assert basket.remaining_amount == 0

    # basket with items, no credits
    DraftInvoiceLine.objects.create(
        label='Event A',
        event_date=datetime.date(2022, 9, 1),
        invoice=draft_invoice,
        quantity=1,
        unit_amount=1,
    )
    basket.draft_invoice.refresh_from_db()
    assert basket.total_amount == 1
    assert basket.credit_amount == 0
    assert basket.remaining_amount == 1

    DraftInvoiceLine.objects.create(
        label='Event A',
        event_date=datetime.date(2022, 9, 1),
        invoice=draft_invoice,
        quantity=9,
        unit_amount=1,
    )
    basket.draft_invoice.refresh_from_db()
    assert basket.total_amount == 10
    assert basket.credit_amount == 0
    assert basket.remaining_amount == 10

    # empty credit
    credit = Credit.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        regie=regie,
        payer_external_id='payer:1',
    )
    basket.draft_invoice.refresh_from_db()
    assert basket.total_amount == 10
    assert basket.credit_amount == 0
    assert basket.remaining_amount == 10

    # credit < amount to pay
    CreditLine.objects.create(
        credit=credit,
        event_date=datetime.date(2022, 9, 1),
        quantity=1,
        unit_amount=1,
    )
    basket.draft_invoice.refresh_from_db()
    assert basket.total_amount == 10
    assert basket.credit_amount == -1
    assert basket.remaining_amount == 9

    # credit for other payer
    other_credit = Credit.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        regie=regie,
        payer_external_id='payer:2',
    )
    CreditLine.objects.create(
        credit=other_credit,
        event_date=datetime.date(2022, 9, 1),
        quantity=1,
        unit_amount=1,
    )
    basket.draft_invoice.refresh_from_db()
    assert basket.total_amount == 10
    assert basket.credit_amount == -1
    assert basket.remaining_amount == 9

    # credit for other regie
    other_regie = Regie.objects.create(label='Bar')
    other_credit = Credit.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        regie=other_regie,
        payer_external_id='payer:1',
    )
    CreditLine.objects.create(
        credit=other_credit,
        event_date=datetime.date(2022, 9, 1),
        quantity=1,
        unit_amount=1,
    )
    basket.draft_invoice.refresh_from_db()
    assert basket.total_amount == 10
    assert basket.credit_amount == -1
    assert basket.remaining_amount == 9

    # not published credit
    other_credit = Credit.objects.create(
        date_publication=now().date() + datetime.timedelta(days=1),
        regie=regie,
        payer_external_id='payer:1',
    )
    CreditLine.objects.create(
        credit=other_credit,
        event_date=datetime.date(2022, 9, 1),
        quantity=3,
        unit_amount=1,
    )
    basket.draft_invoice.refresh_from_db()
    assert basket.total_amount == 10
    assert basket.credit_amount == -1
    assert basket.remaining_amount == 9

    # non finalized campaign
    campaign = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_debit=datetime.date(2022, 11, 15),
        finalized=False,
    )
    pool = Pool.objects.create(
        campaign=campaign,
        draft=False,
    )
    other_credit = Credit.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        regie=regie,
        payer_external_id='payer:1',
        pool=pool,
    )
    CreditLine.objects.create(
        credit=other_credit,
        event_date=datetime.date(2022, 9, 1),
        quantity=3,
        unit_amount=1,
    )
    basket.draft_invoice.refresh_from_db()
    assert basket.total_amount == 10
    assert basket.credit_amount == -1
    assert basket.remaining_amount == 9

    # cancelled credit
    other_credit = Credit.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        regie=regie,
        payer_external_id='payer:1',
        cancelled_at=now(),
    )
    CreditLine.objects.create(
        credit=other_credit,
        event_date=datetime.date(2022, 9, 1),
        quantity=3,
        unit_amount=1,
    )
    basket.draft_invoice.refresh_from_db()
    assert basket.total_amount == 10
    assert basket.credit_amount == -1
    assert basket.remaining_amount == 9

    # credit == amount to pay
    campaign = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_debit=datetime.date(2022, 11, 15),
        finalized=True,
    )
    pool = Pool.objects.create(
        campaign=campaign,
        draft=False,
    )
    credit = Credit.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        regie=regie,
        payer_external_id='payer:1',
        pool=pool,
    )
    CreditLine.objects.create(
        credit=credit,
        event_date=datetime.date(2022, 9, 1),
        quantity=9,
        unit_amount=1,
    )
    basket.draft_invoice.refresh_from_db()
    assert basket.total_amount == 10
    assert basket.credit_amount == -10
    assert basket.remaining_amount == 0

    # credit > amount to pay
    CreditLine.objects.create(
        credit=credit,
        event_date=datetime.date(2022, 9, 1),
        quantity=1,
        unit_amount=1,
    )
    basket.draft_invoice.refresh_from_db()
    assert basket.total_amount == 10
    assert basket.credit_amount == -10
    assert basket.remaining_amount == 0

    # credit with assignment
    invoice = Invoice.objects.create(
        regie=regie,
        date_publication=datetime.date(2023, 4, 21),
        date_payment_deadline=datetime.date(2023, 4, 22),
        date_due=datetime.date(2023, 4, 23),
    )
    InvoiceLine.objects.create(
        label='Event A',
        event_date=datetime.date(2022, 9, 1),
        invoice=invoice,
        quantity=10,
        unit_amount=1,
    )
    CreditAssignment.objects.create(
        invoice=invoice,
        credit=credit,
        amount=2,
    )
    basket.draft_invoice.refresh_from_db()
    assert basket.total_amount == 10
    assert basket.credit_amount == -9
    assert basket.remaining_amount == 1


def test_basket_amounts_with_invoice():
    regie = Regie.objects.create(label='Foo')
    draft_invoice = DraftInvoice.objects.create(
        regie=regie,
        date_publication=datetime.date(2023, 4, 21),
        date_payment_deadline=datetime.date(2023, 4, 22),
        date_due=datetime.date(2023, 4, 23),
    )
    invoice = Invoice.objects.create(
        regie=regie,
        date_publication=datetime.date(2023, 4, 21),
        date_payment_deadline=datetime.date(2023, 4, 22),
        date_due=datetime.date(2023, 4, 23),
    )

    # empty basket
    basket = Basket.objects.create(
        regie=regie,
        draft_invoice=draft_invoice,
        invoice=invoice,
        expiry_at=now() + datetime.timedelta(minutes=1),
        payer_external_id='payer:1',
    )
    assert basket.total_amount == 0
    assert basket.credit_amount == 0
    assert basket.remaining_amount == 0

    # basket with items, no credits
    InvoiceLine.objects.create(
        label='Event A',
        event_date=datetime.date(2022, 9, 1),
        invoice=invoice,
        quantity=1,
        unit_amount=1,
    )
    basket.invoice.refresh_from_db()
    assert basket.total_amount == 1
    assert basket.credit_amount == 0
    assert basket.remaining_amount == 1

    InvoiceLine.objects.create(
        label='Event A',
        event_date=datetime.date(2022, 9, 1),
        invoice=invoice,
        quantity=9,
        unit_amount=1,
    )
    basket.invoice.refresh_from_db()
    assert basket.total_amount == 10
    assert basket.credit_amount == 0
    assert basket.remaining_amount == 10

    # empty credit
    credit = Credit.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        regie=regie,
        payer_external_id='payer:1',
    )
    basket.invoice.refresh_from_db()
    assert basket.total_amount == 10
    assert basket.credit_amount == 0
    assert basket.remaining_amount == 10

    # credit < amount to pay but no payment
    CreditLine.objects.create(
        credit=credit,
        event_date=datetime.date(2022, 9, 1),
        quantity=1,
        unit_amount=1,
    )
    basket.invoice.refresh_from_db()
    assert basket.total_amount == 10
    assert basket.credit_amount == 0
    assert basket.remaining_amount == 10

    CreditAssignment.objects.create(
        invoice=invoice,
        credit=credit,
        amount=1,
    )
    basket.invoice.refresh_from_db()
    assert basket.total_amount == 10
    assert basket.credit_amount == -1
    assert basket.remaining_amount == 9

    # credit == amount to pay
    CreditLine.objects.create(
        credit=credit,
        event_date=datetime.date(2022, 9, 1),
        quantity=9,
        unit_amount=1,
    )
    CreditAssignment.objects.create(
        invoice=invoice,
        credit=credit,
        amount=9,
    )
    basket.invoice.refresh_from_db()
    assert basket.total_amount == 10
    assert basket.credit_amount == -10
    assert basket.remaining_amount == 0
