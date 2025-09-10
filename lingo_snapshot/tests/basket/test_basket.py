import datetime
import json
import uuid
from unittest import mock

import pytest
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.timezone import now
from pyquery import PyQuery

from lingo.basket.models import Basket, BasketLine, BasketLineItem
from lingo.epayment.models import PaymentBackend
from lingo.invoicing.models import (
    Campaign,
    CollectionDocket,
    Credit,
    CreditAssignment,
    CreditLine,
    DraftInvoice,
    DraftInvoiceLine,
    Invoice,
    InvoiceLine,
    InvoiceLinePayment,
    Payment,
    PaymentType,
    Pool,
    Regie,
)
from tests.utils import login

pytestmark = pytest.mark.django_db


def test_basket_detail(app, simple_user):
    resp = app.get('/basket/')
    assert resp.location.endswith('/login/?next=/basket/')
    app = login(app, username='user', password='user')

    # no basket object
    resp = app.get('/basket/')
    assert 'My basket' in resp
    assert len(resp.pyquery('ul.basket li')) == 0
    assert '/basket/validate/' not in resp
    assert '/basket/cancel/' not in resp

    # basket without lines
    regie = Regie.objects.create(label='Foo')
    invoice = DraftInvoice.objects.create(
        regie=regie,
        date_publication=datetime.date(2023, 4, 21),
        date_payment_deadline=datetime.date(2023, 4, 22),
        date_due=datetime.date(2023, 4, 23),
    )
    DraftInvoiceLine.objects.create(
        label='Event A',
        event_date=datetime.date(2022, 9, 1),
        invoice=invoice,
        quantity=1,
        unit_amount=6,
        user_external_id='user:1',
    )
    basket = Basket.objects.create(
        regie=regie,
        draft_invoice=invoice,
        payer_nameid='ab' * 16,
        payer_external_id='payer:1',
        expiry_at=now() + datetime.timedelta(hours=1),
    )
    resp = app.get('/basket/')
    assert 'My basket' in resp
    assert len(resp.pyquery('ul.basket li')) == 0
    assert len(resp.pyquery('ul.basket-amounts')) == 0
    assert '/basket/validate/' not in resp
    assert '/basket/cancel/' not in resp

    # a not closed line
    line = BasketLine.objects.create(
        basket=basket,
        closed=False,
        user_external_id='user:1',
        user_first_name='First1',
        user_last_name='Last1',
        information_message='Lorem ipsum',
    )
    resp = app.get('/basket/')
    assert 'My basket' in resp
    assert len(resp.pyquery('ul.basket li')) == 0
    assert len(resp.pyquery('ul.basket-amounts')) == 0
    assert resp.text.count('<p>Lorem ipsum</p>') == 0
    assert '/basket/validate/' not in resp
    assert '/basket/cancel/' not in resp

    # line is closed but empty
    line.closed = True
    line.save()
    resp = app.get('/basket/')
    assert 'My basket' in resp
    assert len(resp.pyquery('ul.basket li')) == 0
    assert len(resp.pyquery('ul.basket-amounts li')) == 2
    assert resp.text.count('<p>Lorem ipsum</p>') == 1
    assert [PyQuery(li).text() for li in resp.pyquery('ul.basket-amounts li')] == [
        'Basket amount: 6.00€',
        'Amount to pay: 6.00€',
    ]
    assert '/basket/validate/' not in resp
    assert '/basket/cancel/' in resp
    assert 'No payment system has been configured.' in resp

    PaymentBackend.objects.create(label='Test', service='dummy', regie=regie)
    resp = app.get('/basket/')
    assert '/basket/validate/' in resp
    assert '/basket/cancel/' in resp

    # add some items, group_items is False
    BasketLineItem.objects.create(
        event_date=datetime.date(2023, 11, 6),
        line=line,
        label='Repas',
        subject='Réservation',
        details='Lun 06/11',
        quantity=2,
        unit_amount=3,
    )
    BasketLineItem.objects.create(
        event_date=datetime.date(2023, 11, 9),
        line=line,
        label='Repas',
        subject='Réservation',
        details='Jeu 09/11',
        quantity=1,
        unit_amount=3,
    )
    BasketLineItem.objects.create(
        event_date=datetime.date(2023, 11, 10),
        line=line,
        label='Repas',
        subject='Annulation',
        details='Ven 10/11',
        quantity=-1,
        unit_amount=3,
    )
    resp = app.get('/basket/')
    assert 'My basket' in resp
    assert len(resp.pyquery('ul.basket li')) == 3
    assert [PyQuery(li).text() for li in resp.pyquery('ul.basket li')] == [
        'First1 Last1 - Repas - Annulation Ven 10/11 -3.00€',
        'First1 Last1 - Repas - Réservation Jeu 09/11 3.00€',
        'First1 Last1 - Repas - Réservation Lun 06/11 6.00€',
    ]
    assert len(resp.pyquery('ul.basket-amounts li')) == 2
    assert [PyQuery(li).text() for li in resp.pyquery('ul.basket-amounts li')] == [
        'Basket amount: 6.00€',
        'Amount to pay: 6.00€',
    ]
    assert '/basket/validate/' in resp
    assert '/basket/cancel/' in resp

    # group items
    line.group_items = True
    line.save()
    resp = app.get('/basket/')
    assert 'My basket' in resp
    assert len(resp.pyquery('ul.basket li')) == 2
    assert [PyQuery(li).text() for li in resp.pyquery('ul.basket li')] == [
        'First1 Last1 - Repas - Annulation Ven 10/11 -3.00€',
        'First1 Last1 - Repas - Réservation Lun 06/11, Jeu 09/11 9.00€',
    ]
    assert len(resp.pyquery('ul.basket-amounts li')) == 2
    assert [PyQuery(li).text() for li in resp.pyquery('ul.basket-amounts li')] == [
        'Basket amount: 6.00€',
        'Amount to pay: 6.00€',
    ]
    assert '/basket/validate/' in resp
    assert '/basket/cancel/' in resp

    # with available credit
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
    credit = Credit.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        regie=regie,
        payer_external_id='payer:1',
        usable=False,
    )
    CreditLine.objects.create(
        credit=credit,
        event_date=datetime.date(2022, 9, 1),
        quantity=1,
        unit_amount=1,
    )
    resp = app.get('/basket/')
    assert len(resp.pyquery('ul.basket-amounts li')) == 3
    assert [PyQuery(li).text() for li in resp.pyquery('ul.basket-amounts li')] == [
        'Basket amount: 6.00€',
        'Credit: -1.00€',
        'Amount to pay: 5.00€',
    ]

    # not closed line
    line.closed = False
    line.save()
    resp = app.get('/basket/')
    assert 'My basket' in resp
    assert len(resp.pyquery('ul.basket li')) == 0
    assert len(resp.pyquery('ul.basket-amounts')) == 0
    assert '/basket/validate/' not in resp
    assert '/basket/cancel/' not in resp

    # basket payer_nameid is wrong
    line.closed = True
    line.save()
    basket.payer_nameid = uuid.uuid4()
    basket.save()
    resp = app.get('/basket/')
    assert 'My basket' in resp
    assert len(resp.pyquery('ul.basket li')) == 0
    assert len(resp.pyquery('ul.basket-amounts')) == 0
    assert '/basket/validate/' not in resp
    assert '/basket/cancel/' not in resp

    # check status
    basket.payer_nameid = 'ab' * 16
    basket.save()

    # open
    basket.status = 'open'
    basket.save()
    resp = app.get('/basket/')
    assert resp.pyquery('h1').text() == 'My basket'
    assert len(resp.pyquery('ul.basket li')) == 2
    assert len(resp.pyquery('ul.basket-amounts')) == 1
    assert '/basket/validate/' in resp
    assert '/basket/cancel/' in resp

    # tobepaid
    basket.status = 'tobepaid'
    basket.save()
    resp = app.get('/basket/')
    assert resp.pyquery('h1').text() == 'Basket Payment'
    assert '/basket/validate/' not in resp
    assert '/basket/cancel/' in resp

    # 'cancelled', 'expired', 'completed'
    for status in ['cancelled', 'expired', 'completed']:
        basket.status = status
        basket.save()
        resp = app.get('/basket/')
        assert resp.pyquery('h1').text() == 'My basket'
        assert len(resp.pyquery('ul.basket li')) == 0
        assert len(resp.pyquery('ul.basket-amounts')) == 0
        assert '/basket/validate/' not in resp
        assert '/basket/cancel/' not in resp

    # other lines with information_message
    basket.status = 'open'
    basket.save()
    line = BasketLine.objects.create(
        basket=basket,
        closed=True,
        user_external_id='user:2',
        user_first_name='First2',
        user_last_name='Last2',
        information_message='Lorem ipsum',
    )
    line = BasketLine.objects.create(
        basket=basket,
        closed=True,
        user_external_id='user:3',
        user_first_name='First3',
        user_last_name='Last3',
        information_message='Lorem ipsum bis',
    )
    resp = app.get('/basket/')
    assert resp.text.count('<p>Lorem ipsum</p>') == 1
    assert resp.text.count('<p>Lorem ipsum bis</p>') == 1
    assert len(resp.pyquery('p.basket-other-payer-credits--title')) == 0
    assert len(resp.pyquery('ul.basket-other-payer-credits')) == 0

    # with credits for other payers
    draft_invoice_payer2 = DraftInvoice.objects.create(
        regie=regie,
        date_publication=datetime.date(2023, 4, 21),
        date_payment_deadline=datetime.date(2023, 4, 22),
        date_due=datetime.date(2023, 4, 23),
        payer_external_id='payer:2',
        payer_first_name='Payer',
        payer_last_name='2',
    )
    draft_invoice_payer3 = DraftInvoice.objects.create(
        regie=regie,
        date_publication=datetime.date(2023, 4, 21),
        date_payment_deadline=datetime.date(2023, 4, 22),
        date_due=datetime.date(2023, 4, 23),
        payer_external_id='payer:3',
        payer_first_name='Payer',
        payer_last_name='3',
    )
    DraftInvoiceLine.objects.create(
        invoice=draft_invoice_payer2,
        event_date=datetime.date(2025, 6, 12),
        label='Event 1',
        quantity=-1,
        unit_amount=3,
        description='Cancellation 12/06',
        user_external_id='user:1',
        user_first_name='First',
        user_last_name='Last',
    )
    DraftInvoiceLine.objects.create(
        invoice=draft_invoice_payer3,
        event_date=datetime.date(2025, 6, 13),
        label='Event 2',
        quantity=-1,
        unit_amount=1,
        description='Cancellation 13/06',
        user_external_id='user:2',
        user_first_name='First',
        user_last_name='Laast',
    )
    DraftInvoiceLine.objects.create(
        invoice=draft_invoice_payer3,
        event_date=datetime.date(2025, 6, 14),
        label='Event 3',
        quantity=-1,
        unit_amount=2,
        description='Cancellation 14/06',
        user_external_id='user:2',
        user_first_name='First',
        user_last_name='Laast',
    )
    basket.other_payer_credits_draft.set([draft_invoice_payer2, draft_invoice_payer3])
    resp = app.get('/basket/')
    assert len(resp.pyquery('p.basket-other-payer-credits--title')) == 1
    assert len(resp.pyquery('ul.basket-other-payer-credits')) == 1
    assert len(resp.pyquery('ul.basket-other-payer-credits li')) == 7
    assert len(resp.pyquery('ul.basket-other-payer-credits li.basket-other-payer-credits--payer')) == 2
    assert [
        PyQuery(li).text()
        for li in resp.pyquery('ul.basket-other-payer-credits li.basket-other-payer-credits--payer')
    ] == [
        'For Payer 2:\nFirst Last - Event 1 - Cancellation 12/06 3.00€\nCredit: 3.00€',
        'For Payer 3:\nFirst Laast - Event 2 - Cancellation 13/06 1.00€\nFirst Laast - Event 3 - Cancellation 14/06 2.00€\nCredit: 3.00€',
    ]


def test_basket_validate(app, simple_user):
    resp = app.get('/basket/validate/')
    assert resp.location.endswith('/login/?next=/basket/validate/')
    app = login(app, username='user', password='user')

    # no basket object
    app.get('/basket/validate/', status=404)

    # basket without line
    regie = Regie.objects.create(label='Foo')
    invoice = DraftInvoice.objects.create(
        regie=regie,
        date_publication=datetime.date(2023, 4, 21),
        date_payment_deadline=datetime.date(2023, 4, 22),
        date_due=datetime.date(2023, 4, 23),
    )
    basket = Basket.objects.create(
        regie=regie,
        draft_invoice=invoice,
        payer_nameid='ab' * 16,
        expiry_at=now() + datetime.timedelta(hours=1),
    )
    app.get('/basket/validate/', status=404)

    # a not closed line
    line = BasketLine.objects.create(
        basket=basket,
        closed=False,
        user_external_id='user:1',
    )
    app.get('/basket/validate/', status=404)

    # line is closed, but wrong payer_nameid
    line.closed = True
    line.save()
    basket.payer_nameid = uuid.uuid4()
    basket.save()
    app.get('/basket/validate/', status=404)

    # good payer_nameid
    basket.payer_nameid = 'ab' * 16
    basket.save()
    resp = app.get('/basket/validate/')
    with (
        mock.patch('lingo.basket.views.pay_invoice') as pay_invoice,
        mock.patch('lingo.utils.requests_wrapper.RequestsSession.send') as mock_send,
    ):
        pay_invoice.side_effect = lambda *args: redirect(reverse('lingo-basket-detail'))
        resp = resp.form.submit()
    assert [x[0][0].url for x in mock_send.call_args_list] == []
    assert resp.location.endswith('/basket/confirmation/?ret=i')
    resp.follow()
    basket.refresh_from_db()
    assert basket.status == 'completed'
    assert basket.validated_at is not None
    assert basket.paid_at is not None
    assert basket.completed_at is not None
    assert Invoice.objects.count() == 1
    invoice = Invoice.objects.latest('pk')
    assert basket.invoice == invoice
    assert basket.credit is None

    # wrong status
    for status in ['tobepaid', 'cancelled', 'expired', 'completed']:
        basket.status = status
        basket.save()
        app.get('/basket/validate/', status=404)

    # check callback
    basket.status = 'open'
    basket.save()
    line.validation_callback_url = 'http://validation1.com'
    line.credit_callback_url = 'http://validation1.com'
    line.save()
    BasketLine.objects.create(
        basket=basket,
        closed=True,
        user_external_id='user:2',
        validation_callback_url='http://validation2.com',
        credit_callback_url='http://validation2.com',
    )
    resp = app.get('/basket/validate/')
    with (
        mock.patch('lingo.basket.views.pay_invoice') as pay_invoice,
        mock.patch('lingo.utils.requests_wrapper.RequestsSession.send') as mock_send,
    ):
        pay_invoice.side_effect = lambda *args: redirect(reverse('lingo-basket-detail'))
        resp = resp.form.submit()
    assert [x[0][0].url for x in mock_send.call_args_list] == [
        'http://validation1.com/',
        'http://validation2.com/',
    ]
    basket.refresh_from_db()
    invoice = Invoice.objects.latest('pk')
    assert basket.invoice == invoice
    assert basket.credit is None
    assert json.loads(mock_send.call_args_list[0][0][0].body) == {
        'invoice_id': str(invoice.uuid),
        'invoice': {
            'id': str(invoice.uuid),
            'total_amount': '0.00',
            'remaining_amount': '0.00',
        },
        'urls': {
            'invoice_in_backoffice': 'http://testserver/manage/invoicing/redirect/invoice/%s/' % invoice.uuid,
            'invoice_pdf': 'http://testserver/manage/invoicing/redirect/invoice/%s/pdf/' % invoice.uuid,
        },
        'api_urls': {
            'invoice_pdf': 'http://testserver/api/regie/foo/invoice/%s/pdf/' % invoice.uuid,
        },
    }
    assert json.loads(mock_send.call_args_list[1][0][0].body) == {
        'invoice_id': str(invoice.uuid),
        'invoice': {
            'id': str(invoice.uuid),
            'total_amount': '0.00',
            'remaining_amount': '0.00',
        },
        'urls': {
            'invoice_in_backoffice': 'http://testserver/manage/invoicing/redirect/invoice/%s/' % invoice.uuid,
            'invoice_pdf': 'http://testserver/manage/invoicing/redirect/invoice/%s/pdf/' % invoice.uuid,
        },
        'api_urls': {
            'invoice_pdf': 'http://testserver/api/regie/foo/invoice/%s/pdf/' % invoice.uuid,
        },
    }

    # basket is expired
    basket.expiry_at = now()
    basket.status = 'open'
    basket.save()
    resp = app.get('/basket/validate/')
    resp.form.submit()
    basket.refresh_from_db()
    assert basket.status == 'open'


def test_basket_validate_generate_invoice(app, simple_user):
    app = login(app, username='user', password='user')

    regie = Regie.objects.create(label='Foo')
    PaymentBackend.objects.create(label='Test', service='dummy', regie=regie)
    other_regie = Regie.objects.create(label='Bar')
    draft_invoice = DraftInvoice.objects.create(
        regie=regie,
        date_publication=datetime.date(2023, 4, 21),
        date_payment_deadline=datetime.date(2023, 4, 22),
        date_due=now().date(),
        payer_external_id='payer:1',
        payer_first_name='First',
        payer_last_name='Last',
        payer_address='41 rue des kangourous\n99999 Kangourou Ville',
        payer_email='email1',
        payer_phone='phone1',
        origin='basket',
    )
    DraftInvoiceLine.objects.create(
        label='Event A ',
        event_date=datetime.date(2022, 9, 1),
        invoice=draft_invoice,
        quantity=10,
        unit_amount=1,
        user_external_id='user:1',
    )
    # invoice total amount is positive
    draft_invoice.refresh_from_db()
    assert draft_invoice.total_amount == 10
    basket = Basket.objects.create(
        regie=regie,
        draft_invoice=draft_invoice,
        payer_nameid='ab' * 16,
        expiry_at=now() + datetime.timedelta(hours=1),
        payer_external_id='payer:1',
    )
    BasketLine.objects.create(
        basket=basket,
        closed=True,
        user_external_id='user:1',
        group_items=False,
    )

    resp = app.get('/basket/')
    assert 'pk-attention' not in resp
    assert resp.pyquery('a#validate-btn').text() == 'Validate and pay my invoice'
    resp = app.get('/basket/validate/')
    with mock.patch('lingo.basket.views.pay_invoice') as pay_invoice:
        pay_invoice.side_effect = lambda *args, **kwargs: redirect(reverse('lingo-basket-detail'))
        resp = resp.form.submit()
    assert resp.location.endswith('/basket/')
    basket.refresh_from_db()
    assert basket.status == 'tobepaid'
    assert basket.validated_at is not None
    assert basket.paid_at is None
    assert basket.completed_at is None
    invoice = Invoice.objects.latest('pk')
    assert basket.invoice == invoice
    assert invoice.total_amount == 10
    assert Credit.objects.count() == 0

    # with credits, generated invoice is partially paid with credit
    credit1 = Credit.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        regie=regie,
        payer_external_id='payer:1',
    )
    CreditLine.objects.create(
        credit=credit1,
        event_date=datetime.date(2022, 9, 1),
        quantity=1,
        unit_amount=1,
    )
    campaign = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=now().date(),
        date_debit=datetime.date(2022, 11, 15),
        finalized=True,
    )
    pool = Pool.objects.create(
        campaign=campaign,
        draft=False,
    )
    credit2 = Credit.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        regie=regie,
        payer_external_id='payer:1',
        pool=pool,
    )
    CreditLine.objects.create(
        credit=credit2,
        event_date=datetime.date(2022, 9, 1),
        quantity=3,
        unit_amount=1,
    )
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
    campaign = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=now().date(),
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
        pool=pool,  # not finalized pool
    )
    CreditLine.objects.create(
        credit=other_credit,
        event_date=datetime.date(2022, 9, 1),
        quantity=3,
        unit_amount=1,
    )
    other_credit = Credit.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        regie=regie,
        payer_external_id='payer:1',
        cancelled_at=now(),  # cancelled
    )
    CreditLine.objects.create(
        credit=other_credit,
        event_date=datetime.date(2022, 9, 1),
        quantity=3,
        unit_amount=1,
    )
    basket.status = 'open'
    basket.save()
    resp = app.get('/basket/validate/')
    with mock.patch('lingo.basket.views.pay_invoice') as pay_invoice:
        pay_invoice.side_effect = lambda *args, **kwargs: redirect(reverse('lingo-basket-detail'))
        resp = resp.form.submit()
    assert resp.location.endswith('/basket/')
    basket.refresh_from_db()
    assert basket.status == 'tobepaid'
    assert basket.validated_at is not None
    assert basket.paid_at is None
    assert basket.completed_at is None
    invoice = Invoice.objects.latest('pk')
    assert basket.invoice == invoice
    assert invoice.total_amount == 10
    assert invoice.paid_amount == 0
    assert invoice.remaining_amount == 10
    assert invoice.payer_first_name == 'First'
    assert invoice.payer_last_name == 'Last'
    assert invoice.payer_address == '41 rue des kangourous\n99999 Kangourou Ville'
    assert invoice.payer_email == 'email1'
    assert invoice.payer_phone == 'phone1'
    assert invoice.origin == 'basket'
    credit1.refresh_from_db()
    assert credit1.remaining_amount == 0
    assert credit1.assigned_amount == 1
    credit2.refresh_from_db()
    assert credit2.remaining_amount == 0
    assert credit2.assigned_amount == 3
    assert Payment.objects.count() == 0
    assert CreditAssignment.objects.count() == 2
    assignment1, assignment2 = CreditAssignment.objects.all().order_by('pk')
    assert assignment1.amount == 1
    assert assignment1.invoice == invoice
    assert assignment1.payment is None
    assert assignment1.credit == credit1
    assert assignment2.amount == 3
    assert assignment2.invoice == invoice
    assert assignment2.payment is None
    assert assignment2.credit == credit2


def test_basket_validate_generate_invoice_nothing_to_pay(app, simple_user):
    app = login(app, username='user', password='user')

    regie = Regie.objects.create(label='Foo')
    draft_invoice = DraftInvoice.objects.create(
        regie=regie,
        date_publication=datetime.date(2023, 4, 21),
        date_payment_deadline=datetime.date(2023, 4, 22),
        date_due=now().date(),
        payer_external_id='payer:1',
    )
    DraftInvoiceLine.objects.create(
        label='Event A',
        event_date=datetime.date(2022, 9, 1),
        invoice=draft_invoice,
        quantity=1,
        unit_amount=1,
        user_external_id='user:1',
    )
    invoice_line2 = DraftInvoiceLine.objects.create(
        label='Event B ' * 255,
        event_date=datetime.date(2022, 9, 1),
        invoice=draft_invoice,
        quantity=-1,
        unit_amount=1,
        user_external_id='user:1',
    )
    draft_invoice.refresh_from_db()
    assert draft_invoice.total_amount == 0
    basket = Basket.objects.create(
        regie=regie,
        draft_invoice=draft_invoice,
        payer_nameid='ab' * 16,
        expiry_at=now() + datetime.timedelta(hours=1),
        payer_external_id='payer:1',
    )
    line1 = BasketLine.objects.create(
        basket=basket,
        closed=True,
        user_external_id='user:1',
        group_items=False,
    )
    line2 = BasketLine.objects.create(
        basket=basket,
        closed=True,
        user_external_id='user:2',
        group_items=False,
    )

    with mock.patch('eopayment.Payment.get_minimal_amount', return_value=100):
        PaymentBackend.objects.create(label='Test', service='dummy', regie=regie)
        resp = app.get('/basket/')
        assert 'The amount is too low to be paid online.' not in resp.text

    resp = app.get('/basket/validate/')
    with mock.patch('lingo.utils.requests_wrapper.RequestsSession.send') as mock_send:
        resp = resp.form.submit()
    assert [x[0][0].url for x in mock_send.call_args_list] == []
    assert resp.location.endswith('/basket/confirmation/?ret=i')
    resp.follow()
    basket.refresh_from_db()
    assert basket.status == 'completed'
    assert basket.validated_at is not None
    assert basket.paid_at is not None
    assert basket.completed_at is not None
    invoice = Invoice.objects.latest('pk')
    assert basket.invoice == invoice
    assert invoice.total_amount == 0
    assert Credit.objects.count() == 0
    assert Payment.objects.count() == 0

    # total is zero, but with credits
    invoice_line2.delete()
    line1.validation_callback_url = 'http://validation1.com'
    line1.credit_callback_url = 'http://credit1.com'
    line1.payment_callback_url = 'http://payment1.com'
    line1.save()
    line2.validation_callback_url = 'http://validation2.com'
    line2.credit_callback_url = 'http://credit2.com'
    line2.payment_callback_url = 'http://payment2.com'
    line2.save()
    credit1 = Credit.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        regie=regie,
        payer_external_id='payer:1',
    )
    CreditLine.objects.create(
        credit=credit1,
        event_date=datetime.date(2022, 9, 1),
        quantity=10,
        unit_amount=1,
    )
    draft_invoice.refresh_from_db()
    assert draft_invoice.total_amount == 1
    basket.status = 'open'
    basket.save()

    resp = app.get('/basket/')
    assert (
        resp.pyquery('.pk-attention').text()
        == "You don't have to pay anything but you have to validate your basket for the modifications to be taken into account."
    )
    assert resp.pyquery('a#validate-btn').text() == 'Validate and get my invoice'
    resp = app.get('/basket/validate/')
    with mock.patch('lingo.utils.requests_wrapper.RequestsSession.send') as mock_send:
        resp = resp.form.submit()
    assert [x[0][0].url for x in mock_send.call_args_list] == [
        'http://validation1.com/',
        'http://validation2.com/',
        'http://payment1.com/',
        'http://payment2.com/',
    ]
    assert resp.location.endswith('/basket/confirmation/?ret=ip')
    resp.follow()
    basket.refresh_from_db()
    assert basket.status == 'completed'
    assert basket.validated_at is not None
    assert basket.paid_at is not None
    assert basket.completed_at is not None
    invoice = Invoice.objects.latest('pk')
    assert basket.invoice == invoice
    assert invoice.total_amount == 1
    assert invoice.remaining_amount == 0
    assert invoice.paid_amount == 1
    assert Credit.objects.count() == 1
    credit1.refresh_from_db()
    assert credit1.remaining_amount == 9
    assert credit1.assigned_amount == 1
    assert Payment.objects.count() == 1
    payment = Payment.objects.latest('pk')
    assert payment.amount == 1
    assert payment.payment_type.slug == 'credit'
    assert CreditAssignment.objects.count() == 1
    assignment = CreditAssignment.objects.latest('pk')
    assert assignment.amount == 1
    assert assignment.invoice == invoice
    assert assignment.payment == payment
    assert assignment.credit == credit1
    assert payment.invoicelinepayment_set.count() == 1
    invoicelinepayment = InvoiceLinePayment.objects.latest('pk')
    assert invoicelinepayment.line == invoice.lines.get()
    assert invoicelinepayment.amount == 1
    assert json.loads(mock_send.call_args_list[0][0][0].body) == {
        'invoice_id': str(invoice.uuid),
        'invoice': {
            'id': str(invoice.uuid),
            'total_amount': '1.00',
            'remaining_amount': '1.00',
        },
        'urls': {
            'invoice_in_backoffice': 'http://testserver/manage/invoicing/redirect/invoice/%s/' % invoice.uuid,
            'invoice_pdf': 'http://testserver/manage/invoicing/redirect/invoice/%s/pdf/' % invoice.uuid,
        },
        'api_urls': {
            'invoice_pdf': 'http://testserver/api/regie/foo/invoice/%s/pdf/' % invoice.uuid,
        },
    }
    assert json.loads(mock_send.call_args_list[1][0][0].body) == {
        'invoice_id': str(invoice.uuid),
        'invoice': {
            'id': str(invoice.uuid),
            'total_amount': '1.00',
            'remaining_amount': '1.00',
        },
        'urls': {
            'invoice_in_backoffice': 'http://testserver/manage/invoicing/redirect/invoice/%s/' % invoice.uuid,
            'invoice_pdf': 'http://testserver/manage/invoicing/redirect/invoice/%s/pdf/' % invoice.uuid,
        },
        'api_urls': {
            'invoice_pdf': 'http://testserver/api/regie/foo/invoice/%s/pdf/' % invoice.uuid,
        },
    }
    assert json.loads(mock_send.call_args_list[2][0][0].body) == {
        'payment_id': str(payment.uuid),
        'urls': {
            'payment_in_backoffice': 'http://testserver/manage/invoicing/redirect/payment/%s/' % payment.uuid,
            'payment_pdf': 'http://testserver/manage/invoicing/redirect/payment/%s/pdf/' % payment.uuid,
        },
        'api_urls': {
            'payment_pdf': 'http://testserver/api/regie/foo/payment/%s/pdf/' % payment.uuid,
        },
    }

    # invoice paid be 2 credits
    draft_invoice = DraftInvoice.objects.create(
        regie=regie,
        date_publication=datetime.date(2023, 4, 21),
        date_payment_deadline=datetime.date(2023, 4, 22),
        date_due=now().date(),
        payer_external_id='payer:1',
    )
    DraftInvoiceLine.objects.create(
        label='Event A',
        event_date=datetime.date(2022, 9, 1),
        invoice=draft_invoice,
        quantity=10,
        unit_amount=1,
        user_external_id='user:1',
    )
    draft_invoice.refresh_from_db()
    assert draft_invoice.total_amount == 10
    basket.draft_invoice = draft_invoice
    basket.status = 'open'
    basket.save()
    credit2 = Credit.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        regie=regie,
        payer_external_id='payer:1',
    )
    CreditLine.objects.create(
        credit=credit2,
        event_date=datetime.date(2022, 9, 1),
        quantity=10,
        unit_amount=1,
    )

    resp = app.get('/basket/')
    assert (
        resp.pyquery('.pk-attention').text()
        == "You don't have to pay anything but you have to validate your basket for the modifications to be taken into account."
    )
    assert resp.pyquery('a#validate-btn').text() == 'Validate and get my invoice'
    resp = app.get('/basket/validate/')
    with mock.patch('lingo.utils.requests_wrapper.RequestsSession.send') as mock_send:
        resp = resp.form.submit()
    assert [x[0][0].url for x in mock_send.call_args_list] == [
        'http://validation1.com/',
        'http://validation2.com/',
        'http://payment1.com/',
        'http://payment2.com/',
    ]
    assert resp.location.endswith('/basket/confirmation/?ret=ip')
    resp.follow()
    basket.refresh_from_db()
    assert basket.status == 'completed'
    assert basket.validated_at is not None
    assert basket.paid_at is not None
    assert basket.completed_at is not None
    invoice = Invoice.objects.latest('pk')
    assert basket.invoice == invoice
    assert invoice.total_amount == 10
    assert invoice.remaining_amount == 0
    assert invoice.paid_amount == 10
    assert Credit.objects.count() == 2
    credit1.refresh_from_db()
    assert credit1.remaining_amount == 0
    assert credit1.assigned_amount == 10
    credit2.refresh_from_db()
    assert credit2.remaining_amount == 9
    assert credit2.assigned_amount == 1
    assert Payment.objects.count() == 2
    payment = Payment.objects.latest('pk')
    assert payment.amount == 10
    assert payment.payment_type.slug == 'credit'
    assert CreditAssignment.objects.count() == 3
    assignment2, assignment3 = CreditAssignment.objects.order_by('pk')[1:]
    assert assignment2.amount == 9
    assert assignment2.invoice == invoice
    assert assignment2.payment == payment
    assert assignment2.credit == credit1
    assert assignment3.amount == 1
    assert assignment3.invoice == invoice
    assert assignment3.payment == payment
    assert assignment3.credit == credit2
    assert payment.invoicelinepayment_set.count() == 1
    invoicelinepayment = InvoiceLinePayment.objects.latest('pk')
    assert invoicelinepayment.line == invoice.lines.get()
    assert invoicelinepayment.amount == 10
    assert json.loads(mock_send.call_args_list[0][0][0].body) == {
        'invoice_id': str(invoice.uuid),
        'invoice': {
            'id': str(invoice.uuid),
            'total_amount': '10.00',
            'remaining_amount': '10.00',
        },
        'urls': {
            'invoice_in_backoffice': 'http://testserver/manage/invoicing/redirect/invoice/%s/' % invoice.uuid,
            'invoice_pdf': 'http://testserver/manage/invoicing/redirect/invoice/%s/pdf/' % invoice.uuid,
        },
        'api_urls': {
            'invoice_pdf': 'http://testserver/api/regie/foo/invoice/%s/pdf/' % invoice.uuid,
        },
    }
    assert json.loads(mock_send.call_args_list[1][0][0].body) == {
        'invoice_id': str(invoice.uuid),
        'invoice': {
            'id': str(invoice.uuid),
            'total_amount': '10.00',
            'remaining_amount': '10.00',
        },
        'urls': {
            'invoice_in_backoffice': 'http://testserver/manage/invoicing/redirect/invoice/%s/' % invoice.uuid,
            'invoice_pdf': 'http://testserver/manage/invoicing/redirect/invoice/%s/pdf/' % invoice.uuid,
        },
        'api_urls': {
            'invoice_pdf': 'http://testserver/api/regie/foo/invoice/%s/pdf/' % invoice.uuid,
        },
    }
    assert json.loads(mock_send.call_args_list[2][0][0].body) == {
        'payment_id': str(payment.uuid),
        'urls': {
            'payment_in_backoffice': 'http://testserver/manage/invoicing/redirect/payment/%s/' % payment.uuid,
            'payment_pdf': 'http://testserver/manage/invoicing/redirect/payment/%s/pdf/' % payment.uuid,
        },
        'api_urls': {
            'payment_pdf': 'http://testserver/api/regie/foo/payment/%s/pdf/' % payment.uuid,
        },
    }


def test_basket_validate_generate_credit(app, simple_user):
    app = login(app, username='user', password='user')

    regie = Regie.objects.create(label='Foo')
    draft_invoice = DraftInvoice.objects.create(
        regie=regie,
        date_publication=datetime.date(2023, 4, 21),
        date_payment_deadline=datetime.date(2023, 4, 22),
        date_due=now().date(),
        payer_external_id='payer:1',
        payer_first_name='First',
        payer_last_name='Last',
        payer_address='41 rue des kangourous\n99999 Kangourou Ville',
        payer_email='email1',
        payer_phone='phone1',
        origin='basket',
    )
    DraftInvoiceLine.objects.create(
        label='Event A',
        event_date=datetime.date(2022, 9, 1),
        invoice=draft_invoice,
        quantity=-1,
        unit_amount=1,
        description='A description',
        user_external_id='user:1',
        user_first_name='First1',
        user_last_name='Last1',
        event_slug='agenda@repas',
        event_label='Repas',
        agenda_slug='agenda',
        activity_label='Activity Label !',
        accounting_code='424242',
    )
    draft_invoice.refresh_from_db()
    assert draft_invoice.total_amount == -1
    basket = Basket.objects.create(
        regie=regie,
        draft_invoice=draft_invoice,
        payer_nameid='ab' * 16,
        expiry_at=now() + datetime.timedelta(hours=1),
    )
    line = BasketLine.objects.create(
        basket=basket,
        closed=True,
        user_external_id='user:1',
    )

    # credit is not used if basket amount is negative
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

    with mock.patch('eopayment.Payment.get_minimal_amount', return_value=100):
        PaymentBackend.objects.create(label='Test', service='dummy', regie=regie)
        resp = app.get('/basket/')
        assert 'The amount is too low to be paid online.' not in resp.text

    resp = app.get('/basket/')
    assert 'pk-attention' not in resp
    assert resp.pyquery('a#validate-btn').text() == 'Validate and get my credit'
    resp = app.get('/basket/validate/')
    with mock.patch('lingo.utils.requests_wrapper.RequestsSession.send') as mock_send:
        resp = resp.form.submit()
    assert [x[0][0].url for x in mock_send.call_args_list] == []
    assert resp.location.endswith('/basket/confirmation/?ret=c')
    resp.follow()
    basket.refresh_from_db()
    assert basket.status == 'completed'
    assert basket.validated_at is not None
    assert basket.paid_at is not None
    assert basket.completed_at is not None
    assert basket.invoice is None
    credit = Credit.objects.latest('pk')
    assert basket.credit == credit
    assert credit.label == 'Credit from %s' % now().strftime('%d/%m/%Y')
    assert credit.total_amount == 1
    assert credit.regie == regie
    assert credit.payer_external_id == 'payer:1'
    assert credit.payer_first_name == 'First'
    assert credit.payer_last_name == 'Last'
    assert credit.payer_address == '41 rue des kangourous\n99999 Kangourou Ville'
    assert credit.payer_email == 'email1'
    assert credit.payer_phone == 'phone1'
    assert credit.lines.count() == 1
    assert credit.pool is None
    assert credit.date_publication == datetime.date(2023, 4, 21)
    assert credit.origin == 'basket'
    (line1,) = credit.lines.all().order_by('pk')
    assert line1.event_date == datetime.date(2022, 9, 1)
    assert line1.label == 'Event A'
    assert line1.quantity == 1
    assert line1.unit_amount == 1
    assert line1.total_amount == 1
    assert line1.user_external_id == 'user:1'
    assert line1.user_first_name == 'First1'
    assert line1.user_last_name == 'Last1'
    assert line1.description == 'A description'
    assert line1.event_slug == 'agenda@repas'
    assert line1.event_label == 'Repas'
    assert line1.agenda_slug == 'agenda'
    assert line1.activity_label == 'Activity Label !'
    assert line1.accounting_code == '424242'
    assert Invoice.objects.count() == 0

    # check callback
    basket.status = 'open'
    basket.save()
    line.validation_callback_url = 'http://validation1.com'
    line.credit_callback_url = 'http://credit1.com'
    line.save()
    BasketLine.objects.create(
        basket=basket,
        closed=True,
        user_external_id='user:2',
        validation_callback_url='http://validation2.com',
        credit_callback_url='http://credit2.com',
    )
    resp = app.get('/basket/validate/')
    with mock.patch('lingo.utils.requests_wrapper.RequestsSession.send') as mock_send:
        resp = resp.form.submit()
    basket.refresh_from_db()
    credit = Credit.objects.latest('pk')
    assert basket.credit == credit
    assert Invoice.objects.count() == 0
    assert [x[0][0].url for x in mock_send.call_args_list] == [
        'http://validation1.com/',
        'http://validation2.com/',
        'http://credit1.com/',
        'http://credit2.com/',
    ]
    assert json.loads(mock_send.call_args_list[0][0][0].body) == {
        'credit_id': str(credit.uuid),
        'credit': {
            'id': str(credit.uuid),
            'total_amount': '1.00',
        },
        'urls': {
            'credit_in_backoffice': 'http://testserver/manage/invoicing/redirect/credit/%s/' % credit.uuid,
            'credit_pdf': 'http://testserver/manage/invoicing/redirect/credit/%s/pdf/' % credit.uuid,
        },
        'api_urls': {
            'credit_pdf': 'http://testserver/api/regie/foo/credit/%s/pdf/' % credit.uuid,
        },
    }
    assert json.loads(mock_send.call_args_list[1][0][0].body) == json.loads(
        mock_send.call_args_list[0][0][0].body
    )
    assert json.loads(mock_send.call_args_list[2][0][0].body) == json.loads(
        mock_send.call_args_list[0][0][0].body
    )
    assert json.loads(mock_send.call_args_list[3][0][0].body) == json.loads(
        mock_send.call_args_list[0][0][0].body
    )


def test_basket_validate_generate_credit_with_invoices(transactional_db, app, simple_user):
    app = login(app, username='user', password='user')

    regie = Regie.objects.create(label='Foo')
    other_regie = Regie.objects.create(label='Other Foo')
    draft_invoice = DraftInvoice.objects.create(
        regie=regie,
        date_publication=datetime.date(2023, 4, 21),
        date_payment_deadline=datetime.date(2023, 4, 22),
        date_due=now().date(),
        payer_external_id='payer:1',
        payer_first_name='First',
        payer_last_name='Last',
        payer_address='41 rue des kangourous\n99999 Kangourou Ville',
    )
    DraftInvoiceLine.objects.create(
        label='Event A',
        event_date=datetime.date(2022, 9, 1),
        invoice=draft_invoice,
        quantity=-1,
        unit_amount=42,
        description='A description',
        user_external_id='user:1',
        user_first_name='First1',
        user_last_name='Last1',
        event_slug='agenda@repas',
        event_label='Repas',
        agenda_slug='agenda',
        activity_label='Activity Label !',
        accounting_code='424242',
    )
    basket = Basket.objects.create(
        regie=regie,
        draft_invoice=draft_invoice,
        payer_nameid='ab' * 16,
        expiry_at=now() + datetime.timedelta(hours=1),
        payer_external_id='payer:1',
    )
    BasketLine.objects.create(
        basket=basket,
        closed=True,
        user_external_id='user:1',
    )

    invoice1 = Invoice.objects.create(
        label='Invoice from 01/09/2022',
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2023, 4, 22),
        date_due=now().date(),
        regie=regie,
        payer_external_id='payer:1',
        payment_callback_url='http://payment1.com',
    )
    InvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        invoice=invoice1,
        quantity=5,
        unit_amount=1,
    )
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
    invoice2 = Invoice.objects.create(
        label='Invoice from 01/09/2022',
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2023, 4, 22),
        date_due=now().date(),
        regie=regie,
        payer_external_id='payer:1',
        pool=pool,
        payment_callback_url='http://payment2.com',
    )
    InvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        invoice=invoice2,
        quantity=5,
        unit_amount=1,
    )
    invoice3 = Invoice.objects.create(
        label='Invoice from 01/09/2022',
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2023, 4, 22),
        date_due=now().date(),
        regie=regie,
        payer_external_id='payer:42',  # wrong payer
    )
    InvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        invoice=invoice3,
        quantity=5,
        unit_amount=1,
    )
    other_invoice = Invoice.objects.create(
        label='Invoice from 01/09/2022',
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2023, 4, 22),
        date_due=now().date(),
        regie=other_regie,  # other regie
        payer_external_id='payer:1',
    )
    InvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        invoice=other_invoice,
        quantity=5,
        unit_amount=1,
    )
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
    other_invoice = Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2023, 4, 22),
        date_due=now().date(),
        regie=regie,
        payer_external_id='payer:1',
        pool=pool,  # not finalized pool
    )
    InvoiceLine.objects.create(
        invoice=other_invoice,
        event_date=datetime.date(2022, 9, 1),
        quantity=5,
        unit_amount=1,
    )
    other_invoice = Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2023, 4, 22),
        date_due=now().date(),
        regie=regie,
        payer_external_id='payer:1',
        cancelled_at=now(),  # cancelled
    )
    InvoiceLine.objects.create(
        invoice=other_invoice,
        event_date=datetime.date(2022, 9, 1),
        quantity=5,
        unit_amount=1,
    )
    other_invoice = Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2023, 4, 22),
        date_due=now().date(),
        regie=regie,
        payer_external_id='payer:1',
    )
    InvoiceLine.objects.create(
        invoice=other_invoice,
        event_date=datetime.date(2022, 9, 1),
        quantity=5,
        unit_amount=1,
    )
    draft_invoice = DraftInvoice.objects.create(
        date_publication=datetime.date(2023, 4, 21),
        date_payment_deadline=datetime.date(2023, 4, 22),
        date_due=now().date(),
        regie=regie,
        payer_external_id='payer:1',
        payer_first_name='First',
        payer_last_name='Last',
        payer_address='41 rue des kangourous\n99999 Kangourou Ville',
    )
    Basket.objects.create(
        regie=regie,
        draft_invoice=draft_invoice,
        invoice=other_invoice,  # in basket
    )
    other_invoice = Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2023, 4, 22),
        date_due=now().date() - datetime.timedelta(days=1),  # not payable
        regie=regie,
        payer_external_id='payer:1',
    )
    InvoiceLine.objects.create(
        invoice=other_invoice,
        event_date=datetime.date(2022, 9, 1),
        quantity=5,
        unit_amount=1,
    )
    collection = CollectionDocket.objects.create(regie=regie, date_end=now().date(), draft=True)
    collected_invoice = Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=now().date(),
        regie=regie,
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Name1',
        collection=collection,
    )
    collected_invoice.set_number()
    collected_invoice.save()
    InvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        invoice=collected_invoice,
        quantity=2,
        unit_amount=1,
    )

    resp = app.get('/basket/validate/')
    with mock.patch('lingo.utils.requests_wrapper.RequestsSession.send') as mock_send:
        resp = resp.form.submit()
    assert [x[0][0].url for x in mock_send.call_args_list] == ['http://payment1.com/', 'http://payment2.com/']
    credit = Credit.objects.latest('pk')
    assert credit.total_amount == 42
    assert credit.remaining_amount == 32
    invoice1.refresh_from_db()
    assert invoice1.remaining_amount == 0
    assert invoice1.paid_amount == 5
    invoice2.refresh_from_db()
    assert invoice2.remaining_amount == 0
    assert invoice2.paid_amount == 5
    assert Payment.objects.count() == 2
    assert CreditAssignment.objects.count() == 2
    assignment1, assignment2 = CreditAssignment.objects.all().order_by('pk')
    assert assignment1.amount == 5
    assert assignment1.invoice == invoice1
    assert assignment1.credit == credit
    assert assignment2.amount == 5
    assert assignment2.invoice == invoice2
    assert assignment2.credit == credit
    assert Payment.objects.count() == 2
    payment1, payment2 = Payment.objects.all().order_by('pk')
    assert payment1.amount == 5
    assert payment1.payment_type.slug == 'credit'
    assert payment2.amount == 5
    assert payment2.payment_type.slug == 'credit'
    assert assignment1.payment == payment1
    assert assignment2.payment == payment2
    assert payment1.invoicelinepayment_set.count() == 1
    (invoicelinepayment11,) = payment1.invoicelinepayment_set.order_by('pk')
    assert invoicelinepayment11.line == invoice1.lines.get()
    assert invoicelinepayment11.amount == 5
    assert payment2.invoicelinepayment_set.count() == 1
    (invoicelinepayment21,) = payment2.invoicelinepayment_set.order_by('pk')
    assert invoicelinepayment21.line == invoice2.lines.get()
    assert invoicelinepayment21.amount == 5
    assert json.loads(mock_send.call_args_list[0][0][0].body) == {
        'payment_id': str(payment1.uuid),
        'urls': {
            'payment_in_backoffice': 'http://testserver/manage/invoicing/redirect/payment/%s/'
            % payment1.uuid,
            'payment_pdf': 'http://testserver/manage/invoicing/redirect/payment/%s/pdf/' % payment1.uuid,
        },
        'api_urls': {
            'payment_pdf': 'http://testserver/api/regie/foo/payment/%s/pdf/' % payment1.uuid,
        },
    }
    assert json.loads(mock_send.call_args_list[1][0][0].body) == {
        'payment_id': str(payment2.uuid),
        'urls': {
            'payment_in_backoffice': 'http://testserver/manage/invoicing/redirect/payment/%s/'
            % payment2.uuid,
            'payment_pdf': 'http://testserver/manage/invoicing/redirect/payment/%s/pdf/' % payment2.uuid,
        },
        'api_urls': {
            'payment_pdf': 'http://testserver/api/regie/foo/payment/%s/pdf/' % payment2.uuid,
        },
    }

    # more invoice amount to pay than credit amount
    draft_invoice = DraftInvoice.objects.create(
        regie=regie,
        date_publication=datetime.date(2023, 4, 21),
        date_payment_deadline=datetime.date(2023, 4, 22),
        date_due=now().date(),
        payer_external_id='payer:1',
        payer_first_name='First',
        payer_last_name='Last',
        payer_address='41 rue des kangourous\n99999 Kangourou Ville',
    )
    DraftInvoiceLine.objects.create(
        label='Event A',
        event_date=datetime.date(2022, 9, 1),
        invoice=draft_invoice,
        quantity=-1,
        unit_amount=42,
        description='A description',
        user_external_id='user:1',
        user_first_name='First1',
        user_last_name='Last1',
        event_slug='agenda@repas',
        event_label='Repas',
        agenda_slug='agenda',
        activity_label='Activity Label !',
        accounting_code='424242',
    )
    basket = Basket.objects.create(
        regie=regie,
        draft_invoice=draft_invoice,
        payer_nameid='ab' * 16,
        expiry_at=now() + datetime.timedelta(hours=1),
        payer_external_id='payer:1',
    )
    BasketLine.objects.create(
        basket=basket,
        closed=True,
        user_external_id='user:1',
    )

    invoice1 = Invoice.objects.create(
        label='Invoice from 01/09/2022',
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2023, 4, 22),
        date_due=now().date(),
        regie=regie,
        payer_external_id='payer:1',
        payment_callback_url='http://payment.com',
    )
    InvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        invoice=invoice1,
        quantity=43,
        unit_amount=1,
    )

    resp = app.get('/basket/validate/')
    with mock.patch('lingo.utils.requests_wrapper.RequestsSession.send') as mock_send:
        resp = resp.form.submit()
    assert [x[0][0].url for x in mock_send.call_args_list] == []
    credit = Credit.objects.latest('pk')
    assert credit.total_amount == 42
    assert credit.remaining_amount == 0
    invoice1.refresh_from_db()
    assert invoice1.remaining_amount == 1
    assert invoice1.paid_amount == 42

    # regie not configured to assign credits when created
    regie.assign_credits_on_creation = False
    regie.save()
    draft_invoice = DraftInvoice.objects.create(
        regie=regie,
        date_publication=datetime.date(2023, 4, 21),
        date_payment_deadline=datetime.date(2023, 4, 22),
        date_due=now().date(),
        payer_external_id='payer:1',
        payer_first_name='First',
        payer_last_name='Last',
        payer_address='41 rue des kangourous\n99999 Kangourou Ville',
    )
    DraftInvoiceLine.objects.create(
        label='Event A',
        event_date=datetime.date(2022, 9, 1),
        invoice=draft_invoice,
        quantity=-1,
        unit_amount=42,
        description='A description',
        user_external_id='user:1',
        user_first_name='First1',
        user_last_name='Last1',
        event_slug='agenda@repas',
        event_label='Repas',
        agenda_slug='agenda',
        activity_label='Activity Label !',
        accounting_code='424242',
    )
    basket = Basket.objects.create(
        regie=regie,
        draft_invoice=draft_invoice,
        payer_nameid='ab' * 16,
        expiry_at=now() + datetime.timedelta(hours=1),
        payer_external_id='payer:1',
    )
    BasketLine.objects.create(
        basket=basket,
        closed=True,
        user_external_id='user:1',
    )

    invoice1 = Invoice.objects.create(
        label='Invoice from 01/09/2022',
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2023, 4, 22),
        date_due=now().date(),
        regie=regie,
        payer_external_id='payer:1',
        payment_callback_url='http://payment.com',
    )
    InvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        invoice=invoice1,
        quantity=42,
        unit_amount=1,
    )

    resp = app.get('/basket/validate/')
    with mock.patch('lingo.utils.requests_wrapper.RequestsSession.send') as mock_send:
        resp = resp.form.submit()
    assert [x[0][0].url for x in mock_send.call_args_list] == []
    credit = Credit.objects.latest('pk')
    assert credit.total_amount == 42
    assert credit.remaining_amount == 42
    invoice1.refresh_from_db()
    assert invoice1.remaining_amount == 42
    assert invoice1.paid_amount == 0


def test_basket_cancel(app, simple_user):
    resp = app.get('/basket/cancel/')
    assert resp.location.endswith('/login/?next=/basket/cancel/')
    app = login(app, username='user', password='user')

    # no basket object
    app.get('/basket/cancel/', status=404)

    # basket without line
    regie = Regie.objects.create(label='Foo')
    PaymentType.create_defaults(regie)
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
        payer_nameid='ab' * 16,
        expiry_at=now() + datetime.timedelta(hours=1),
    )
    app.get('/basket/cancel/', status=404)
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

    # a not closed line
    line = BasketLine.objects.create(
        basket=basket,
        closed=False,
        user_external_id='user:1',
        cancel_information_message='Lorem ipsum',
    )
    app.get('/basket/cancel/', status=404)

    # line is closed, but wrong payer_nameid
    line.closed = True
    line.save()
    basket.payer_nameid = uuid.uuid4()
    basket.save()
    app.get('/basket/cancel/', status=404)

    # good payer_nameid
    assert CreditAssignment.objects.count() == 1
    basket.payer_nameid = 'ab' * 16
    basket.save()
    resp = app.get('/basket/cancel/')
    assert resp.text.count('<p>Lorem ipsum</p>') == 1
    with mock.patch('lingo.utils.requests_wrapper.RequestsSession.send') as mock_send:
        resp = resp.form.submit()
    assert [x[0][0].url for x in mock_send.call_args_list] == []
    assert resp.location.endswith('/basket/')
    basket.refresh_from_db()
    assert basket.status == 'cancelled'
    assert basket.cancelled_at is not None
    invoice.refresh_from_db()
    assert invoice.cancelled_at is not None
    assert invoice.cancelled_by == simple_user
    assert invoice.cancellation_reason.slug == 'basket-cancelled'
    assert invoice.cancellation_description == ''
    assert CreditAssignment.objects.count() == 0

    basket.status = 'tobepaid'
    basket.cancelled_at = None
    basket.save()
    resp = app.get('/basket/cancel/')
    with mock.patch('lingo.utils.requests_wrapper.RequestsSession.send') as mock_send:
        resp = resp.form.submit()
    assert [x[0][0].url for x in mock_send.call_args_list] == []
    assert resp.location.endswith('/basket/')
    basket.refresh_from_db()
    assert basket.status == 'cancelled'
    assert basket.cancelled_at is not None

    # wrong status
    for status in ['cancelled', 'expired', 'completed']:
        basket.status = status
        basket.save()
        app.get('/basket/cancel/', status=404)

    # check callback
    basket.status = 'open'
    basket.save()
    line.cancel_callback_url = 'http://cancellation1.com'
    line.save()
    BasketLine.objects.create(
        basket=basket,
        closed=True,
        user_external_id='user:2',
        cancel_callback_url='http://cancellation2.com',
    )
    resp = app.get('/basket/cancel/')
    with mock.patch('lingo.utils.requests_wrapper.RequestsSession.send') as mock_send:
        resp = resp.form.submit()
    assert [x[0][0].url for x in mock_send.call_args_list] == [
        'http://cancellation1.com/',
        'http://cancellation2.com/',
    ]

    # basket is expired
    basket.expiry_at = now()
    basket.status = 'open'
    basket.save()
    app.get('/basket/cancel/')

    # other lines with information_message
    line = BasketLine.objects.create(
        basket=basket,
        closed=True,
        user_external_id='user:3',
        user_first_name='First3',
        user_last_name='Last3',
        cancel_information_message='Lorem ipsum',
    )
    line = BasketLine.objects.create(
        basket=basket,
        closed=True,
        user_external_id='user:4',
        user_first_name='First4',
        user_last_name='Last4',
        cancel_information_message='Lorem ipsum bis',
    )
    resp = app.get('/basket/cancel/')
    assert resp.text.count('<p>Lorem ipsum</p>') == 1
    assert resp.text.count('<p>Lorem ipsum bis</p>') == 1


def test_basket_status_js(app, simple_user):
    resp = app.get('/basket/status.js')
    assert resp.location.endswith('/login/?next=/basket/status.js')
    app = login(app, username='user', password='user')

    # no basket object
    assert 'basket_entry_count.textContent = ""' in app.get('/basket/status.js').text

    # basket without line
    regie = Regie.objects.create(label='Foo')
    invoice = DraftInvoice.objects.create(
        regie=regie,
        date_publication=datetime.date(2023, 4, 21),
        date_payment_deadline=datetime.date(2023, 4, 22),
        date_due=datetime.date(2023, 4, 23),
    )
    basket = Basket.objects.create(
        regie=regie,
        draft_invoice=invoice,
        payer_nameid='ab' * 16,
        expiry_at=now() + datetime.timedelta(hours=1),
    )
    assert 'basket_entry_count.textContent = ""' in app.get('/basket/status.js').text

    # a not closed line
    line = BasketLine.objects.create(
        basket=basket,
        closed=False,
        user_external_id='user:1',
    )
    assert 'basket_entry_count.textContent = ""' in app.get('/basket/status.js').text

    # line is closed, but wrong payer_nameid
    line.closed = True
    line.save()
    basket.payer_nameid = uuid.uuid4()
    basket.save()
    assert 'basket_entry_count.textContent = ""' in app.get('/basket/status.js').text

    # good payer_nameid
    basket.payer_nameid = 'ab' * 16
    basket.save()
    assert 'basket_entry_count.textContent = "1"' in app.get('/basket/status.js').text

    # basket is expired
    basket.expiry_at = now()
    basket.status = 'open'
    basket.save()
    assert 'basket_entry_count.textContent = ""' in app.get('/basket/status.js').text


def test_basket_detail_back_url(app, simple_user):
    app = login(app, username='user', password='user')
    resp = app.get('/basket/')
    assert not resp.pyquery('.basket-back-link')

    # set back link
    resp = app.get('/basket/?back_url=https%3A//example.net/')
    assert resp.pyquery('.basket-back-link')

    # check it's maintained in session
    resp = app.get('/basket/')
    assert resp.pyquery('.basket-back-link')
    assert resp.pyquery('.basket-back-link')[0].attrib['href'] == 'https://example.net/'
