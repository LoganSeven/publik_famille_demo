import datetime
import urllib
from unittest import mock

import eopayment
import pytest
from django.core.management import call_command
from django.urls import reverse
from django.utils.timezone import now

from lingo.basket.models import Basket, BasketLine, BasketLineItem
from lingo.epayment.models import PaymentBackend, Transaction
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
    Payment,
    PaymentType,
    Pool,
    Regie,
)
from tests.utils import login

pytestmark = pytest.mark.django_db


def test_payment_disabled_demo(app, settings):
    settings.DEBUG = False
    app.get(reverse('lingo-epayment-demo'), status=404)


def test_payment_redirect(app, settings):
    settings.DEBUG = True
    PaymentBackend.objects.create(label='Test', service='dummy')
    resp = app.get(reverse('lingo-epayment-demo'))
    assert resp.location.startswith('https://dummy-payment.entrouvert.com/?')
    assert Transaction.objects.all().count() == 1
    transaction = Transaction.objects.all().first()
    assert transaction.status == 0
    assert transaction.amount == 20


def test_payment_redirect_select_backend(app, settings):
    settings.DEBUG = True
    PaymentBackend.objects.create(label='Test', service='dummy')
    resp = app.get(reverse('lingo-epayment-demo') + '?backend=test')
    assert resp.location.startswith('https://dummy-payment.entrouvert.com/?')
    assert Transaction.objects.all().count() == 1
    transaction = Transaction.objects.all().first()
    assert transaction.status == 0
    assert transaction.amount == 20


def test_payment_redirect_amount(app, settings):
    settings.DEBUG = True
    PaymentBackend.objects.create(label='Test', service='dummy')
    app.get(reverse('lingo-epayment-demo') + '?amount=2')
    transaction = Transaction.objects.all().first()
    assert transaction.status == 0
    assert transaction.amount == 2


def test_payment_unexpected_error(app, settings):
    settings.DEBUG = True
    PaymentBackend.objects.create(label='Test', service='dummy')
    with mock.patch('eopayment.Payment.request', side_effect=eopayment.PaymentException('xxx')):
        resp = app.get(reverse('lingo-epayment-demo'))
        assert 'Unexpected error: xxx' in resp.text


def test_payment_redirect_by_form(app, settings):
    settings.DEBUG = True
    PaymentBackend.objects.create(
        label='Test',
        service='ogone',
        service_options={'environment': 'TEST', 'pspid': 'xxx', 'sha_in': 'xxx', 'sha_out': 'xxx'},
    )
    resp = app.get(reverse('lingo-epayment-demo'))
    assert resp.pyquery('input[name="AMOUNT"]').attr.value == '2000'
    assert Transaction.objects.all().count() == 1
    transaction = Transaction.objects.all().first()
    assert transaction.status == 0
    assert transaction.amount == 20


def test_payment_return(app, settings):
    settings.DEBUG = True
    PaymentBackend.objects.create(label='Test', service='dummy')
    app.get(reverse('lingo-epayment-demo'))  # init transaction
    transaction = Transaction.objects.all().first()
    resp = app.get(
        reverse('lingo-epayment-explicit-return', kwargs={'transaction_id': transaction.id})
        + f'?transaction_id={transaction.order_id}&origin=origin&ok=1&signed=1'
    ).follow()
    status_url = reverse('lingo-epayment-processing-status', kwargs={'transaction_id': transaction.id})
    assert status_url in resp
    assert resp.pyquery('#continue a').attr.href == '/'
    assert app.get(status_url).json == {'status': 3, 'running': False, 'paid': True}


def test_payment_return_next_url(app, settings):
    settings.DEBUG = True
    PaymentBackend.objects.create(label='Test', service='dummy')
    app.get(reverse('lingo-epayment-demo') + '?next_url=/next/')  # init transaction
    transaction = Transaction.objects.all().first()
    resp = app.get(
        reverse('lingo-epayment-explicit-return', kwargs={'transaction_id': transaction.id})
        + f'?transaction_id={transaction.order_id}&origin=origin&ok=1&signed=1'
    ).follow()
    status_url = reverse('lingo-epayment-processing-status', kwargs={'transaction_id': transaction.id})
    assert status_url in resp
    assert resp.pyquery('#continue a').attr.href == '/next/'
    assert app.get(status_url).json == {'status': 3, 'running': False, 'paid': True}


def test_payment_callback_get(app, settings):
    settings.DEBUG = True
    PaymentBackend.objects.create(label='Test', service='dummy')
    app.get(reverse('lingo-epayment-demo'))  # init transaction
    transaction = Transaction.objects.all().first()
    app.get(
        reverse('lingo-epayment-explicit-callback', kwargs={'transaction_id': transaction.id})
        + f'?transaction_id={transaction.order_id}&origin=origin&ok=1&signed=1',
        status=200,
    )
    transaction.refresh_from_db()
    assert transaction.status == 3


def test_payment_callback_post(app, settings):
    settings.DEBUG = True
    PaymentBackend.objects.create(label='Test', service='dummy')
    app.get(reverse('lingo-epayment-demo'))  # init transaction
    transaction = Transaction.objects.all().first()
    app.post(
        reverse('lingo-epayment-explicit-callback', kwargs={'transaction_id': transaction.id})
        + f'?transaction_id={transaction.order_id}&origin=origin&ok=1&signed=1',
        status=200,
    )
    transaction.refresh_from_db()
    assert transaction.status == 3


def test_payment_auto_return(app, settings):
    settings.DEBUG = True
    app.get(reverse('lingo-epayment-auto-return'), status=400)

    backend = PaymentBackend.objects.create(
        label='Test',
        service='ogone',
        service_options={
            'environment': 'TEST',
            'pspid': 'xxx',
            'sha_in': 'xxx',
            'sha_out': 'xxx',
            'encoding': 'utf-8',
        },
    )
    app.get(reverse('lingo-epayment-demo'))  # init transaction
    transaction = Transaction.objects.all().first()

    ogone_backend = backend.eopayment
    data = {
        'orderid': transaction.order_id,
        'status': '9',
        'payid': '3011229363',
        'ncerror': '0',
    }
    data['shasign'] = ogone_backend.backend.sha_sign_out(data, encoding='utf-8')
    resp = app.get(reverse('lingo-epayment-auto-return') + '?' + urllib.parse.urlencode(data))
    assert resp.location == reverse('lingo-epayment-processing', kwargs={'transaction_id': transaction.id})
    transaction.refresh_from_db()
    assert transaction.status == 3


def test_payment_auto_callback(app, settings):
    settings.DEBUG = True
    assert app.get(reverse('lingo-epayment-auto-callback')).json['err'] == 1

    backend = PaymentBackend.objects.create(
        label='Test',
        service='ogone',
        service_options={
            'environment': 'TEST',
            'pspid': 'xxx',
            'sha_in': 'xxx',
            'sha_out': 'xxx',
            'encoding': 'utf-8',
        },
    )
    app.get(reverse('lingo-epayment-demo'))  # init transaction
    transaction = Transaction.objects.all().first()

    ogone_backend = backend.eopayment
    data = {
        'orderid': transaction.order_id,
        'status': '9',
        'payid': '3011229363',
        'ncerror': '0',
    }
    data['shasign'] = ogone_backend.backend.sha_sign_out(data, encoding='utf-8')
    assert (
        app.get(reverse('lingo-epayment-auto-callback') + '?' + urllib.parse.urlencode(data)).json['err'] == 0
    )
    transaction.refresh_from_db()
    assert transaction.status == 3


def test_basket_payment(app, simple_user):
    app = login(app, username='user', password='user')

    regie = Regie.objects.create(label='Foo')
    PaymentType.create_defaults(regie)
    PaymentBackend.objects.create(label='Test', service='dummy', regie=regie)
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
        quantity=5,
        unit_amount=1,
        user_external_id='user:1',
    )
    DraftInvoiceLine.objects.create(
        label='Event A',
        event_date=datetime.date(2022, 9, 1),
        invoice=invoice,
        quantity=5,
        unit_amount=1,
        user_external_id='user:1',
    )

    basket = Basket.objects.create(
        regie=regie,
        draft_invoice=invoice,
        payer_nameid='ab' * 16,
        expiry_at=now() + datetime.timedelta(hours=1),
    )
    line = BasketLine.objects.create(
        basket=basket,
        closed=False,
        user_external_id='user:1',
        payment_callback_url='http://payment1.com',
    )
    BasketLineItem.objects.create(
        event_date=datetime.date.today(),
        line=line,
        label='Event A',
        subject='Réservation',
        details='Lun 06/11, Mar 07/11',
        quantity=5,
        unit_amount=1,
    )
    line.closed = True
    line.save()
    line = BasketLine.objects.create(
        basket=basket,
        closed=False,
        user_external_id='user:2',
        payment_callback_url='http://payment2.com',
    )
    BasketLineItem.objects.create(
        event_date=datetime.date.today(),
        line=line,
        label='Event A',
        subject='Réservation',
        details='Lun 06/11, Mar 07/11',
        quantity=5,
        unit_amount=1,
    )
    line.closed = True
    line.save()

    with mock.patch('eopayment.Payment.get_minimal_amount', return_value=100):
        resp = app.get('/basket/')
        assert 'The amount is too low to be paid online.' in resp.text

    resp = app.get('/basket/')
    assert 'Validate' in resp.text

    resp = app.get('/basket/validate/')
    resp = resp.form.submit()

    resp_direct_pay = app.get(
        reverse('lingo-epayment-invoice', kwargs={'invoice_uuid': Invoice.objects.all().first().uuid})
    )
    assert 'This invoice must be paid using the basket.' in resp_direct_pay.text

    assert resp.location.startswith('https://dummy-payment.entrouvert.com/')
    assert Transaction.objects.all().count() == 1
    transaction = Transaction.objects.all().first()
    assert transaction.status == 0
    assert transaction.amount == 10

    with mock.patch('lingo.utils.requests_wrapper.RequestsSession.send') as mock_send:
        resp = app.get(
            reverse('lingo-epayment-explicit-return', kwargs={'transaction_id': transaction.id})
            + f'?transaction_id={transaction.order_id}&origin=origin&ok=1&signed=1'
        ).follow()
        assert Payment.objects.all().count() == 1
        payment = Payment.objects.all().first()
        assert payment.amount == 10
        assert payment.payment_type.slug == 'online'
        assert payment.get_invoice_payments()[0].invoice.paid_amount == 10
        assert payment.get_invoice_payments()[0].invoice.remaining_amount == 0
        assert [x[0][0].url for x in mock_send.call_args_list] == [
            'http://payment1.com/',
            'http://payment2.com/',
        ]


def test_basket_payment_with_assigned_credits(app, simple_user):
    app = login(app, username='user', password='user')

    regie = Regie.objects.create(label='Foo')
    PaymentType.create_defaults(regie)
    PaymentBackend.objects.create(label='Test', service='dummy', regie=regie)
    invoice = DraftInvoice.objects.create(
        regie=regie,
        date_publication=datetime.date(2023, 4, 21),
        date_payment_deadline=datetime.date(2023, 4, 22),
        date_due=now().date(),
        payer_external_id='payer:1',
    )
    DraftInvoiceLine.objects.create(
        label='Event A',
        event_date=datetime.date(2022, 9, 1),
        invoice=invoice,
        quantity=10,
        unit_amount=1,
        user_external_id='user:1',
    )

    basket = Basket.objects.create(
        regie=regie,
        draft_invoice=invoice,
        payer_nameid='ab' * 16,
        payer_external_id='payer:1',
        expiry_at=now() + datetime.timedelta(hours=1),
    )
    line = BasketLine.objects.create(
        basket=basket,
        closed=False,
        user_external_id='user:1',
        payment_callback_url='http://payment1.com',
    )
    BasketLineItem.objects.create(
        event_date=datetime.date.today(),
        line=line,
        label='Event A',
        subject='Réservation',
        details='Lun 06/11, Mar 07/11',
        quantity=10,
        unit_amount=1,
    )
    line.closed = True
    line.save()

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
        date_due=datetime.date(2022, 10, 31),
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
    other_regie = Regie.objects.create(label='Foo')
    other_credit = Credit.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        regie=other_regie,  # other regie
        payer_external_id='payer:1',
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
        payer_external_id='payer:2',  # other payer
    )
    CreditLine.objects.create(
        credit=other_credit,
        event_date=datetime.date(2022, 9, 1),
        quantity=3,
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

    resp = app.get('/basket/validate/')
    resp = resp.form.submit()
    assert resp.location.startswith('https://dummy-payment.entrouvert.com/')
    assert Transaction.objects.all().count() == 1
    transaction = Transaction.objects.all().first()
    assert transaction.status == 0
    assert transaction.amount == 6

    with mock.patch('lingo.utils.requests_wrapper.RequestsSession.send') as mock_send:
        app.get(
            reverse('lingo-epayment-explicit-return', kwargs={'transaction_id': transaction.id})
            + f'?transaction_id={transaction.order_id}&origin=origin&ok=1&signed=1'
        ).follow()
    assert Payment.objects.count() == 2
    payment1, payment2 = Payment.objects.all().order_by('pk')
    assert payment1.amount == 6
    assert payment1.payment_type.slug == 'online'
    assert payment1.get_invoice_payments()[0].invoice.paid_amount == 10
    assert payment1.get_invoice_payments()[0].invoice.remaining_amount == 0
    assert payment2.amount == 4
    assert payment2.payment_type.slug == 'credit'
    assert payment2.get_invoice_payments()[0].invoice.paid_amount == 10
    assert payment2.get_invoice_payments()[0].invoice.remaining_amount == 0
    assert [x[0][0].url for x in mock_send.call_args_list] == ['http://payment1.com/']
    assert payment1.get_invoice_payments()[0].invoice == payment2.get_invoice_payments()[0].invoice
    assert CreditAssignment.objects.count() == 2
    assignment1, assignment2 = CreditAssignment.objects.all().order_by('pk')
    assert assignment1.amount == 1
    assert assignment1.invoice == payment2.get_invoice_payments()[0].invoice
    assert assignment1.payment == payment2
    assert assignment1.credit == credit1
    assert assignment2.amount == 3
    assert assignment2.invoice == payment2.get_invoice_payments()[0].invoice
    assert assignment2.payment == payment2
    assert assignment2.credit == credit2


def test_basket_cancelled_payment(app, simple_user):
    app = login(app, username='user', password='user')

    regie = Regie.objects.create(label='Foo')
    PaymentType.create_defaults(regie)
    PaymentBackend.objects.create(label='Test', service='dummy', regie=regie)
    invoice = DraftInvoice.objects.create(
        regie=regie,
        date_publication=datetime.date(2023, 4, 21),
        date_payment_deadline=datetime.date(2023, 4, 22),
        date_due=now().date(),
        payer_external_id='payer:1',
    )
    DraftInvoiceLine.objects.create(
        label='Event A',
        event_date=datetime.date(2022, 9, 1),
        invoice=invoice,
        quantity=10,
        unit_amount=1,
        user_external_id='user:1',
    )

    basket = Basket.objects.create(
        regie=regie,
        draft_invoice=invoice,
        payer_nameid='ab' * 16,
        payer_external_id='payer:1',
        expiry_at=now() + datetime.timedelta(hours=1),
    )
    line = BasketLine.objects.create(
        basket=basket,
        closed=False,
        user_external_id='user:1',
        payment_callback_url='http://payment1.com',
    )
    BasketLineItem.objects.create(
        event_date=datetime.date.today(),
        line=line,
        label='Event A',
        subject='Réservation',
        details='Lun 06/11, Mar 07/11',
        quantity=10,
        unit_amount=1,
    )
    line.closed = True
    line.save()

    resp = app.get('/basket/validate/')
    resp = resp.form.submit()
    assert resp.location.startswith('https://dummy-payment.entrouvert.com/')
    assert Transaction.objects.all().count() == 1
    transaction = Transaction.objects.all().first()
    assert transaction.status == 0
    assert transaction.amount == 10
    basket = Basket.objects.get(pk=basket.pk)
    old_invoice = basket.invoice

    def get_cancelled_response():
        from eopayment.common import PaymentResponse

        return PaymentResponse(result=eopayment.CANCELED, signed=True)

    with (
        mock.patch('eopayment.Payment.response', return_value=get_cancelled_response()) as mocked_response,
    ):
        transaction = Transaction.objects.latest('start_date')
        resp = app.post(
            reverse('lingo-epayment-explicit-return', kwargs={'transaction_id': transaction.id}),
            params={'xxx': 'yyy'},
        ).follow()
        assert mocked_response.call_count == 1

        transaction.refresh_from_db()
        assert transaction.status == eopayment.CANCELED
        basket = Basket.objects.get(pk=basket.pk)
        assert basket.status == 'open'
        assert basket.invoice is None
        old_invoice.refresh_from_db()
        assert old_invoice.cancelled_at is not None
        assert old_invoice.cancellation_reason.slug == 'transaction-cancelled'

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

    resp = app.get('/basket/validate/')
    resp = resp.form.submit()
    basket = Basket.objects.get(pk=basket.pk)
    assert basket.invoice.pk != old_invoice.pk
    assert CreditAssignment.objects.count() == 1
    assignment = CreditAssignment.objects.get()
    assert assignment.amount == 1
    assert assignment.invoice == basket.invoice
    assert assignment.payment is None
    assert assignment.credit == credit1
    old_invoice = basket.invoice

    with (
        mock.patch('eopayment.Payment.response', return_value=get_cancelled_response()) as mocked_response,
    ):
        transaction = Transaction.objects.latest('start_date')
        resp = app.post(
            reverse('lingo-epayment-explicit-return', kwargs={'transaction_id': transaction.id}),
            params={'xxx': 'yyy'},
        ).follow()
        assert mocked_response.call_count == 1

        transaction.refresh_from_db()
        assert transaction.status == eopayment.CANCELED
        basket = Basket.objects.get(pk=basket.pk)
        assert basket.status == 'open'
        assert basket.invoice is None
        old_invoice.refresh_from_db()
        assert old_invoice.cancelled_at is not None
        assert old_invoice.cancellation_reason.slug == 'transaction-cancelled'
        assert CreditAssignment.objects.count() == 0


def test_payment_payfip(app, settings, freezer, simple_user):
    app = login(app, username='user', password='user')
    settings.DEBUG = True
    with mock.patch('eopayment.Payment.request', return_value=(1, eopayment.URL, 'https://payfip/')):
        PaymentBackend.objects.create(
            label='Test',
            service='payfip_ws',
            service_options={},
        )
        resp = app.get(reverse('lingo-epayment-demo'))
        assert resp.location.startswith('https://payfip/')

    def get_waiting_response():
        from eopayment.common import PaymentResponse

        return PaymentResponse(result=eopayment.WAITING, signed=True)

    with (
        mock.patch('eopayment.Payment.response', return_value=get_waiting_response()) as mocked_response,
        mock.patch('eopayment.Payment.payment_status', return_value=get_waiting_response()) as mocked_status,
    ):
        transaction = Transaction.objects.all().first()
        resp = app.post(
            reverse('lingo-epayment-explicit-return', kwargs={'transaction_id': transaction.id}),
            params={'xxx': 'yyy'},
        ).follow()
        assert mocked_response.call_count == 1
        assert mocked_status.call_count == 1

        transaction.refresh_from_db()
        assert transaction.status == eopayment.WAITING

        status_url = reverse('lingo-epayment-processing-status', kwargs={'transaction_id': transaction.id})
        resp = app.get(status_url)
        assert mocked_status.call_count == 2

        transaction.refresh_from_db()
        assert transaction.status == eopayment.WAITING

    def get_paid_response():
        from eopayment.common import PaymentResponse

        return PaymentResponse(result=eopayment.PAID, signed=True)

    with mock.patch('eopayment.Payment.payment_status', return_value=get_paid_response()) as mocked:
        call_command('poll_payment_backends')
        assert mocked.call_count == 0

        freezer.move_to(datetime.timedelta(seconds=300))
        call_command('poll_payment_backends')
        assert mocked.call_count == 1

        transaction.refresh_from_db()
        assert transaction.status == eopayment.PAID


def test_payment_payfip_pre_checks(app, settings, freezer, simple_user):
    settings.DEBUG = True
    app = login(app, username='user', password='user')
    with mock.patch('eopayment.Payment.request', return_value=(1, eopayment.URL, 'https://payfip/')):
        PaymentBackend.objects.create(
            label='Test',
            service='payfip_ws',
            service_options={},
        )
        resp = app.get(reverse('lingo-epayment-demo'))
        assert resp.location.startswith('https://payfip/')

        resp = app.get(reverse('lingo-epayment-demo') + '?backend=test&amount=0.4')
        assert 'The amount is too low to be paid online.' in resp.text
        with mock.patch('eopayment.Payment.get_maximal_amount', return_value=100):
            resp = app.get(reverse('lingo-epayment-demo') + '?backend=test&amount=1000')
            assert 'The amount is too high to be paid online.' in resp.text
        simple_user.email = ''
        simple_user.save()
        resp = app.get(reverse('lingo-epayment-demo') + '?backend=test')
        assert 'The payment requires an email address.' in resp.text


@mock.patch.object(Regie, 'get_payer_external_id_from_nameid', autospec=True)
def test_invoice_payment(mock_payer, app, simple_user):
    app.authorization = ('Basic', ('user', 'user'))

    regie = Regie.objects.create(label='Foo')
    PaymentType.create_defaults(regie)
    PaymentBackend.objects.create(label='Test', service='dummy', regie=regie)

    invoice = Invoice.objects.create(
        label='My invoice',
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=now().date() + datetime.timedelta(days=1),
        date_due=datetime.date(2022, 10, 31),
        regie=regie,
        payer_external_id='payer:1',
        payment_callback_url='http://payment1.com',
    )
    invoice.set_number()
    invoice.save()

    InvoiceLine.objects.create(
        event_date=now().date(),
        invoice=invoice,
        quantity=1,
        unit_amount=10,
    )
    invoice.refresh_from_db()

    mock_payer.return_value = 'payer:1'
    resp = app.get('/api/regie/foo/invoices/', params={'NameID': 'foobar'})
    assert resp.json['err'] == 0
    assert mock_payer.call_args_list == [mock.call(regie, mock.ANY, 'foobar')]

    payment_url = resp.json['data'][0]['api']['payment_url']

    resp = app.get(f'/api/regie/foo/invoice/{invoice.uuid}/', params={'NameID': 'foobar'})
    assert resp.json['data']['api']['payment_url'] == payment_url

    resp = app.get(payment_url)
    assert resp.location.startswith('https://dummy-payment.entrouvert.com/')
    assert Transaction.objects.all().count() == 1
    transaction = Transaction.objects.all().first()
    assert transaction.status == 0
    assert transaction.amount == 10

    with mock.patch('lingo.utils.requests_wrapper.RequestsSession.send') as mock_send:
        resp = app.get(
            reverse('lingo-epayment-explicit-return', kwargs={'transaction_id': transaction.id})
            + f'?transaction_id={transaction.order_id}&origin=origin&ok=1&signed=1'
        ).follow()
        assert Payment.objects.all().count() == 1
        payment = Payment.objects.all().first()
        assert payment.amount == 10
        assert payment.payment_type.slug == 'online'
        assert payment.get_invoice_payments()[0].invoice.paid_amount == 10
        assert payment.get_invoice_payments()[0].invoice.remaining_amount == 0
        assert [x[0][0].url for x in mock_send.call_args_list] == ['http://payment1.com/']

    status_url = reverse('lingo-epayment-processing-status', kwargs={'transaction_id': transaction.id})
    assert status_url in resp
    assert app.get(status_url).json == {'status': 3, 'running': False, 'paid': True}
    assert resp.pyquery('#continue a').attr.href == '/'

    resp = app.get(payment_url)
    assert 'This invoice has already been paid.' in resp.text

    # cancelled invoice
    invoice = Invoice.objects.create(
        label='My invoice',
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=now().date() + datetime.timedelta(days=1),
        date_due=datetime.date(2022, 10, 31),
        regie=regie,
        payer_external_id='payer:1',
        payment_callback_url='http://payment1.com',
    )
    invoice.set_number()
    invoice.save()

    InvoiceLine.objects.create(
        event_date=now().date(),
        invoice=invoice,
        quantity=1,
        unit_amount=10,
    )
    invoice.refresh_from_db()
    invoice.cancelled_at = now()
    invoice.save()

    payment_url = reverse('lingo-epayment-invoice', kwargs={'invoice_uuid': invoice.uuid})
    resp = app.get(payment_url)
    assert 'This invoice has been cancelled.' in resp.text

    # invoice from non finalized campaign
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
    invoice = Invoice.objects.create(
        label='My invoice',
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=now().date() + datetime.timedelta(days=1),
        date_due=datetime.date(2022, 10, 31),
        regie=regie,
        payer_external_id='payer:1',
        payment_callback_url='http://payment1.com',
        pool=pool,
    )
    invoice.set_number()
    invoice.save()

    InvoiceLine.objects.create(
        event_date=now().date(),
        invoice=invoice,
        quantity=1,
        unit_amount=10,
    )
    invoice.refresh_from_db()
    invoice.save()

    payment_url = reverse('lingo-epayment-invoice', kwargs={'invoice_uuid': invoice.uuid})
    resp = app.get(payment_url)
    assert 'This invoice cannot yet be paid.' in resp.text

    # collected invoice
    collection = CollectionDocket.objects.create(regie=regie, date_end=now().date(), draft=True)
    collected_invoice = Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=now().date() + datetime.timedelta(days=1),
        date_due=datetime.date(2022, 10, 31),
        regie=regie,
        payer_external_id='payer:1',
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
    collected_invoice.refresh_from_db()
    collected_invoice.save()

    payment_url = reverse('lingo-epayment-invoice', kwargs={'invoice_uuid': collected_invoice.uuid})
    resp = app.get(payment_url)
    assert 'This invoice has been collected.' in resp.text
