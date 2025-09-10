import datetime
import decimal
import uuid
from unittest import mock

import pytest
from django.utils.timezone import now

from lingo.invoicing.errors import PayerError
from lingo.invoicing.models import (
    Campaign,
    CollectionDocket,
    Invoice,
    InvoiceLine,
    InvoiceLinePayment,
    Payment,
    PaymentType,
    Pool,
    Regie,
)

pytestmark = pytest.mark.django_db


def test_add_payment(app, user):
    app.post('/api/regie/foo/payments/', status=403)
    app.authorization = ('Basic', ('john.doe', 'password'))

    app.post('/api/regie/foo/payments/', status=404)

    regie = Regie.objects.create(slug='foo')
    resp = app.post('/api/regie/foo/payments/', status=400)
    assert resp.json['err']
    assert resp.json['errors'] == {
        'amount': ['This field is required.'],
        'payment_type': ['This field is required.'],
        'elements_to_pay': ['This field is required.'],
    }

    params = {
        'amount': 64,
        'payment_type': 'foo',  # unknown payment type
        'elements_to_pay': 'foo, %s' % str(uuid.uuid4()),
    }
    resp = app.post('/api/regie/foo/payments/', params=params, status=400)
    assert resp.json['err']
    assert resp.json['errors'] == {
        'payment_type': ['Object with slug=foo does not exist.'],
        'elements_to_pay': {'0': ['Must be a valid UUID.']},
    }

    # unknown payment type for this regie
    other_regie = Regie.objects.create(slug='bar')
    PaymentType.create_defaults(other_regie)
    params = {
        'amount': 64,
        'payment_type': 'check',
        'elements_to_pay': str(uuid.uuid4()),
    }
    resp = app.post('/api/regie/foo/payments/', params=params, status=400)
    assert resp.json['err']
    assert resp.json['errors'] == {
        'elements_to_pay': {'0': ['Unknown invoice.']},
        'payment_type': ['Object with slug=check does not exist.'],
    }

    PaymentType.create_defaults(regie)  # create default payment types
    PaymentType.objects.filter(slug='check').update(disabled=True)  # disabled payment type
    params = {
        'amount': 64,
        'payment_type': 'check',
        'elements_to_pay': str(uuid.uuid4()),
    }
    resp = app.post('/api/regie/foo/payments/', params=params, status=400)
    assert resp.json['err']
    assert resp.json['errors'] == {
        'elements_to_pay': {'0': ['Unknown invoice.']},
        'payment_type': ['Object with slug=check does not exist.'],
    }

    finalized_campaign = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_debit=datetime.date(2022, 11, 15),
        finalized=True,
    )
    finalized_pool = Pool.objects.create(
        campaign=finalized_campaign,
        draft=False,
        status='completed',
    )

    invoice11 = Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=now().date(),
        regie=regie,
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Name1',
        payer_address='41 rue des kangourous\n99999 Kangourou Ville',
        payer_email='email1',
        payer_phone='phone1',
        payment_callback_url='http://payment.com/invoice11/',
        pool=finalized_pool,
    )
    line111 = InvoiceLine.objects.create(
        event_date=now().date(),
        invoice=invoice11,
        quantity=1,
        unit_amount=44,
    )
    line112 = InvoiceLine.objects.create(
        event_date=now().date(),
        invoice=invoice11,
        quantity=1,
        unit_amount=-2,
    )
    invoice12 = Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=now().date(),
        regie=regie,
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Name1',
        payer_address='41 rue des kangourous\n99999 Kangourou Ville',
        payer_email='email1',
        payer_phone='phone1',
        payment_callback_url='http://payment.com/invoice12/',
    )
    line12 = InvoiceLine.objects.create(
        event_date=now().date(),
        invoice=invoice12,
        quantity=1,
        unit_amount=42,
    )
    invoice13 = Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=now().date(),
        regie=regie,
        payer_external_id='payer:2',
        payer_first_name='First2',
        payer_last_name='Name2',
        payer_address='42 rue des kangourous\n99999 Kangourou Ville',
        payer_email='email2',
        payer_phone='phone2',
        payment_callback_url='http://payment.com/invoice13/',
    )
    line13 = InvoiceLine.objects.create(
        event_date=now().date(),
        invoice=invoice13,
        quantity=1,
        unit_amount=42,
    )
    invoice14 = Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=now().date() - datetime.timedelta(days=1),  # past date
        regie=regie,
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Name1',
        payer_address='41 rue des kangourous\n99999 Kangourou Ville',
        payer_email='email1',
        payer_phone='phone1',
        payment_callback_url='http://payment.com/invoice14/',
    )
    line14 = InvoiceLine.objects.create(
        event_date=now().date(),
        invoice=invoice14,
        quantity=1,
        unit_amount=42,
    )
    invoice15 = Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=now().date(),
        regie=regie,
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Name1',
        payer_address='41 rue des kangourous\n99999 Kangourou Ville',
        payer_email='email1',
        payer_phone='phone1',
        payer_direct_debit=True,
        payment_callback_url='http://payment.com/invoice15/',
    )
    line15 = InvoiceLine.objects.create(
        event_date=now().date(),
        invoice=invoice15,
        quantity=1,
        unit_amount=42,
    )
    invoice21 = Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        regie=regie,
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Name1',
        payer_address='41 rue des kangourous\n99999 Kangourou Ville',
        payer_email='email1',
        payer_phone='phone1',
        payment_callback_url='http://payment.com/invoice21/',
    )
    InvoiceLine.objects.create(
        event_date=now().date(),
        invoice=invoice21,
        quantity=1,
        unit_amount=42,
    )

    other_invoice = Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=now().date(),
        regie=other_regie,
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Name1',
        payer_address='41 rue des kangourous\n99999 Kangourou Ville',
        payer_email='email1',
        payer_phone='phone1',
    )
    other_line = InvoiceLine.objects.create(
        event_date=now().date(),
        invoice=other_invoice,
        quantity=1,
        unit_amount=42,
    )

    cancelled_invoice = Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=now().date(),
        regie=regie,
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Name1',
        cancelled_at=now(),
    )
    cancelled_invoice.set_number()
    cancelled_invoice.save()
    InvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        invoice=cancelled_invoice,
        quantity=2,
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

    non_finalized_campaign = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_debit=datetime.date(2022, 11, 15),
    )
    non_finalized_pool = Pool.objects.create(
        campaign=non_finalized_campaign,
        draft=False,
        status='completed',
    )
    non_finalized_invoice = Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        regie=regie,
        pool=non_finalized_pool,
        payer_external_id='payer:1',
    )
    non_finalized_invoice.set_number()
    non_finalized_invoice.save()
    InvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        invoice=non_finalized_invoice,
        quantity=1.2,
        unit_amount=1,
        pool=non_finalized_pool,
        label='Event A',
        event_slug='event-a-foo-bar',
        user_external_id='user:1',
        description='@overtaking@',
    )

    for invoice in [other_invoice, cancelled_invoice, non_finalized_invoice, collected_invoice]:
        params = {
            'amount': 64,
            'payment_type': 'cash',
            'elements_to_pay': ','.join(
                [
                    str(invoice11.uuid),
                    str(invoice.uuid),
                ]
            ),
        }
        resp = app.post('/api/regie/foo/payments/', params=params, status=400)
        assert resp.json['err']
        assert resp.json['errors'] == {
            'elements_to_pay': {
                '1': ['Unknown invoice.'],
            }
        }

    params = {
        'amount': 64,
        'payment_type': 'cash',
        'elements_to_pay': ','.join([str(invoice11.uuid), str(invoice13.uuid)]),  # not the same payer
    }
    resp = app.post('/api/regie/foo/payments/', params=params, status=400)
    assert resp.json['err']
    assert resp.json['errors'] == {
        'elements_to_pay': ['Can not create payment for invoices of different payers.']
    }

    params = {
        'amount': 64,
        'payment_type': 'cash',
        'elements_to_pay': ','.join([str(invoice11.uuid), str(invoice14.uuid)]),  # too late for invoice14
    }
    resp = app.post('/api/regie/foo/payments/', params=params, status=400)
    assert resp.json['err']
    assert resp.json['errors'] == {'elements_to_pay': {'1': ['The invoice due date has passed.']}}

    params = {
        'amount': 64,
        'payment_type': 'cash',
        'elements_to_pay': ','.join(
            [str(invoice11.uuid), str(invoice15.uuid)]
        ),  # invoice14 set up for direct debit
    }
    resp = app.post('/api/regie/foo/payments/', params=params, status=400)
    assert resp.json['err']
    assert resp.json['errors'] == {'elements_to_pay': {'1': ['The invoice is set up for direct debit.']}}

    params = {
        'amount': 0,
        'payment_type': 'cash',
        'elements_to_pay': ','.join([str(invoice11.uuid)]),
    }
    resp = app.post('/api/regie/foo/payments/', params=params, status=400)
    assert resp.json['err']
    assert resp.json['errors'] == {'amount': ['Ensure this value is greater than or equal to 0.01.']}

    params = {
        'amount': -0.01,
        'payment_type': 'cash',
        'elements_to_pay': ','.join([str(invoice11.uuid)]),
    }
    resp = app.post('/api/regie/foo/payments/', params=params, status=400)
    assert resp.json['err']
    assert resp.json['errors'] == {'amount': ['Ensure this value is greater than or equal to 0.01.']}

    params = {
        'amount': 10,
        'payment_type': 'cash',
        'elements_to_pay': ','.join([str(invoice11.uuid)]),
        'check_number': '123456',
    }
    with mock.patch('lingo.utils.requests_wrapper.RequestsSession.send') as mock_send:
        resp = app.post('/api/regie/foo/payments/', params=params)
    assert [x[0][0].url for x in mock_send.call_args_list] == []
    assert Payment.objects.count() == 1
    assert InvoiceLinePayment.objects.count() == 2
    payment = Payment.objects.latest('pk')
    assert resp.json['data'] == {
        'payment_id': str(payment.uuid),
        'urls': {
            'payment_in_backoffice': 'http://testserver/manage/invoicing/redirect/payment/%s/' % payment.uuid,
            'payment_pdf': 'http://testserver/manage/invoicing/redirect/payment/%s/pdf/' % payment.uuid,
        },
        'api_urls': {
            'payment_pdf': 'http://testserver/api/regie/foo/payment/%s/pdf/' % payment.uuid,
        },
    }
    assert payment.regie == regie
    assert payment.amount == 10
    assert payment.payment_type.slug == 'cash'
    assert payment.payment_type.regie == regie
    assert payment.payer_external_id == 'payer:1'
    assert payment.payer_first_name == 'First1'
    assert payment.payer_last_name == 'Name1'
    assert payment.payer_address == '41 rue des kangourous\n99999 Kangourou Ville'
    assert payment.payer_email == 'email1'
    assert payment.payer_phone == 'phone1'
    assert payment.transaction_id is None
    assert payment.transaction_date is None
    assert payment.order_id is None
    assert payment.bank_transaction_id is None
    assert payment.bank_transaction_date is None
    assert payment.bank_data == {}
    assert payment.number == 1
    assert payment.date_payment is None
    assert payment.formatted_number == 'R%02d-%s-0000001' % (
        regie.pk,
        now().strftime('%y-%m'),
    )
    assert payment.payment_info == {
        'check_number': '123456',
    }
    (
        invoice_line_payment1,
        invoice_line_payment2,
    ) = payment.invoicelinepayment_set.order_by('pk')
    assert invoice_line_payment1.amount == -2
    assert invoice_line_payment1.line == line112
    assert invoice_line_payment2.amount == 12
    assert invoice_line_payment2.line == line111
    invoice11.refresh_from_db()
    assert invoice11.paid_amount == 10
    assert invoice11.remaining_amount == 32
    invoice12.refresh_from_db()
    assert invoice12.paid_amount == 0
    assert invoice12.remaining_amount == 42

    PaymentType.objects.all().update(disabled=False)
    params = {
        'amount': 10,
        'payment_type': 'check',
        'elements_to_pay': ','.join([str(invoice11.uuid), str(invoice12.uuid)]),
        'check_number': '123456',
        'check_issuer': 'Foo',
        'check_bank': 'Bar',
        'bank_transfer_number': '234567',
        'payment_reference': 'Ref',
        'date_payment': '2022-11-06',
    }
    with mock.patch('lingo.utils.requests_wrapper.RequestsSession.send') as mock_send:
        resp = app.post('/api/regie/foo/payments/', params=params)
    assert [x[0][0].url for x in mock_send.call_args_list] == []
    assert Payment.objects.count() == 2
    assert InvoiceLinePayment.objects.count() == 3
    payment = Payment.objects.latest('pk')
    assert payment.regie == regie
    assert payment.amount == 10
    assert payment.payment_type.slug == 'check'
    assert payment.payment_type.regie == regie
    assert payment.payer_external_id == 'payer:1'
    assert payment.payer_first_name == 'First1'
    assert payment.payer_last_name == 'Name1'
    assert payment.payer_address == '41 rue des kangourous\n99999 Kangourou Ville'
    assert payment.payer_email == 'email1'
    assert payment.payer_phone == 'phone1'
    assert payment.transaction_id is None
    assert payment.transaction_date is None
    assert payment.order_id is None
    assert payment.bank_transaction_id is None
    assert payment.bank_transaction_date is None
    assert payment.bank_data == {}
    assert payment.date_payment == datetime.date(2022, 11, 6)
    assert payment.number == 1
    assert payment.formatted_number == 'R%02d-22-11-0000001' % regie.pk
    assert payment.payment_info == {
        'check_number': '123456',
        'check_issuer': 'Foo',
        'check_bank': 'Bar',
        'bank_transfer_number': '234567',
        'payment_reference': 'Ref',
    }
    (invoice_line_payment,) = payment.invoicelinepayment_set.order_by('pk')
    assert invoice_line_payment.amount == 10
    assert invoice_line_payment.line == line111
    invoice11.refresh_from_db()
    assert invoice11.paid_amount == 20
    assert invoice11.remaining_amount == 22
    invoice12.refresh_from_db()
    assert invoice12.paid_amount == 0
    assert invoice12.remaining_amount == 42

    params = {
        'amount': 22.01,
        'payment_type': 'online',
        'elements_to_pay': ','.join([str(invoice12.uuid), str(invoice11.uuid)]),
        'online_refdet': 'rreeffddeett',
    }
    with mock.patch('lingo.utils.requests_wrapper.RequestsSession.send') as mock_send:
        resp = app.post('/api/regie/foo/payments/', params=params)
    assert [x[0][0].url for x in mock_send.call_args_list] == ['http://payment.com/invoice11/']
    assert Payment.objects.count() == 3
    assert InvoiceLinePayment.objects.count() == 5
    payment = Payment.objects.latest('pk')
    assert payment.regie == regie
    assert payment.amount == decimal.Decimal('22.01')
    assert payment.payment_type.slug == 'online'
    assert payment.payment_type.regie == regie
    assert payment.payer_external_id == 'payer:1'
    assert payment.payer_first_name == 'First1'
    assert payment.payer_last_name == 'Name1'
    assert payment.payer_address == '41 rue des kangourous\n99999 Kangourou Ville'
    assert payment.payer_email == 'email1'
    assert payment.payer_phone == 'phone1'
    assert payment.transaction_id is None
    assert payment.transaction_date is None
    assert payment.order_id is None
    assert payment.bank_transaction_id is None
    assert payment.bank_transaction_date is None
    assert payment.bank_data == {'refdet': 'rreeffddeett'}
    assert payment.date_payment is None
    assert payment.number == 2
    assert payment.formatted_number == 'R%02d-%s-0000002' % (
        regie.pk,
        now().strftime('%y-%m'),
    )
    invoice_line_payment1, invoice_line_payment2 = payment.invoicelinepayment_set.order_by('pk')
    assert invoice_line_payment1.amount == 22
    assert invoice_line_payment1.line == line111  # older invoice first
    assert invoice_line_payment2.amount == decimal.Decimal('0.01')
    assert invoice_line_payment2.line == line12
    invoice11.refresh_from_db()
    assert invoice11.paid_amount == 42
    assert invoice11.remaining_amount == 0
    invoice12.refresh_from_db()
    assert invoice12.paid_amount == decimal.Decimal('0.01')
    assert invoice12.remaining_amount == decimal.Decimal('41.99')

    # to much
    params = {
        'amount': 42,
        'payment_type': 'check',
        'elements_to_pay': ','.join([str(invoice11.uuid), str(invoice12.uuid)]),
    }
    with mock.patch('lingo.utils.requests_wrapper.RequestsSession.send') as mock_send:
        resp = app.post('/api/regie/foo/payments/', params=params, status=400)
    assert resp.json['err']
    assert resp.json['errors'] == {'amount': ['Amount is bigger than sum of invoices remaining amounts.']}

    params = {
        'amount': 41.99,
        'payment_type': 'check',
        'elements_to_pay': ','.join([str(invoice11.uuid), str(invoice12.uuid)]),
    }
    with mock.patch('lingo.utils.requests_wrapper.RequestsSession.send') as mock_send:
        resp = app.post('/api/regie/foo/payments/', params=params)
    assert [x[0][0].url for x in mock_send.call_args_list] == ['http://payment.com/invoice12/']
    assert Payment.objects.count() == 4
    assert InvoiceLinePayment.objects.count() == 6
    payment = Payment.objects.latest('pk')
    assert payment.regie == regie
    assert payment.amount == decimal.Decimal('41.99')
    assert payment.payment_type.slug == 'check'
    assert payment.payment_type.regie == regie
    assert payment.payer_external_id == 'payer:1'
    assert payment.payer_first_name == 'First1'
    assert payment.payer_last_name == 'Name1'
    assert payment.payer_address == '41 rue des kangourous\n99999 Kangourou Ville'
    assert payment.payer_email == 'email1'
    assert payment.payer_phone == 'phone1'
    assert payment.transaction_id is None
    assert payment.transaction_date is None
    assert payment.order_id is None
    assert payment.bank_transaction_id is None
    assert payment.bank_transaction_date is None
    assert payment.bank_data == {}
    assert payment.date_payment is None
    assert payment.number == 3
    assert payment.formatted_number == 'R%02d-%s-0000003' % (
        regie.pk,
        now().strftime('%y-%m'),
    )
    (invoice_line_payment,) = payment.invoicelinepayment_set.order_by('pk')
    assert invoice_line_payment.amount == decimal.Decimal('41.99')
    assert invoice_line_payment.line == line12
    invoice11.refresh_from_db()
    assert invoice11.paid_amount == 42
    assert invoice11.remaining_amount == 0
    invoice12.refresh_from_db()
    assert invoice12.paid_amount == 42
    assert invoice12.remaining_amount == 0

    # delete payements, and call endpoint with a list of lines
    InvoiceLinePayment.objects.all().delete()
    Payment.objects.all().delete()

    params = {
        'amount': 64,
        'payment_type': 'cash',
        'elements_to_pay': ','.join(
            ['line:%s' % line112.uuid, 'line:%s' % other_line.uuid]
        ),  # not the same regie
    }
    with mock.patch('lingo.utils.requests_wrapper.RequestsSession.send') as mock_send:
        resp = app.post('/api/regie/foo/payments/', params=params, status=400)
    assert resp.json['err']
    assert resp.json['errors'] == {'elements_to_pay': {'1': ['Unknown invoice line.']}}

    params = {
        'amount': 64,
        'payment_type': 'cash',
        'elements_to_pay': ','.join(
            ['line:%s' % line112.uuid, 'line:%s' % line13.uuid]
        ),  # not the same payer
    }
    with mock.patch('lingo.utils.requests_wrapper.RequestsSession.send') as mock_send:
        resp = app.post('/api/regie/foo/payments/', params=params, status=400)
    assert resp.json['err']
    assert resp.json['errors'] == {
        'elements_to_pay': ['Can not create payment for invoice lines of different payers.']
    }

    params = {
        'amount': 64,
        'payment_type': 'cash',
        'elements_to_pay': ','.join(
            ['line:%s' % line112.uuid, 'line:%s' % line14.uuid]
        ),  # too late for line14
    }
    with mock.patch('lingo.utils.requests_wrapper.RequestsSession.send') as mock_send:
        resp = app.post('/api/regie/foo/payments/', params=params, status=400)
    assert resp.json['err']
    assert resp.json['errors'] == {
        'elements_to_pay': {'1': ['The invoice due date of this line has passed.']}
    }

    params = {
        'amount': 64,
        'payment_type': 'cash',
        'elements_to_pay': ','.join(
            ['line:%s' % line112.uuid, 'line:%s' % line15.uuid]
        ),  # line15 is set up for direct debit
    }
    with mock.patch('lingo.utils.requests_wrapper.RequestsSession.send') as mock_send:
        resp = app.post('/api/regie/foo/payments/', params=params, status=400)
    assert resp.json['err']
    assert resp.json['errors'] == {
        'elements_to_pay': {'1': ['The invoice of this line is set up for direct debit.']}
    }

    params = {
        'amount': 10,
        'payment_type': 'cash',
        'elements_to_pay': ','.join(['line:%s' % line111.uuid, str(invoice13.uuid)]),  # invoice13 is ignored
    }
    with mock.patch('lingo.utils.requests_wrapper.RequestsSession.send') as mock_send:
        resp = app.post('/api/regie/foo/payments/', params=params)
    assert [x[0][0].url for x in mock_send.call_args_list] == []
    assert Payment.objects.count() == 1
    assert InvoiceLinePayment.objects.count() == 1
    payment = Payment.objects.latest('pk')
    assert payment.regie == regie
    assert payment.amount == 10
    assert payment.payment_type.slug == 'cash'
    assert payment.payment_type.regie == regie
    assert payment.payer_external_id == 'payer:1'
    assert payment.payer_first_name == 'First1'
    assert payment.payer_last_name == 'Name1'
    assert payment.payer_address == '41 rue des kangourous\n99999 Kangourou Ville'
    assert payment.payer_email == 'email1'
    assert payment.payer_phone == 'phone1'
    assert payment.transaction_id is None
    assert payment.transaction_date is None
    assert payment.order_id is None
    assert payment.bank_transaction_id is None
    assert payment.bank_transaction_date is None
    assert payment.bank_data == {}
    assert payment.date_payment is None
    assert payment.number == 4
    assert payment.formatted_number == 'R%02d-%s-0000004' % (
        regie.pk,
        now().strftime('%y-%m'),
    )
    (invoice_line_payment,) = payment.invoicelinepayment_set.order_by('pk')
    assert invoice_line_payment.amount == 10
    assert invoice_line_payment.line == line111
    invoice11.refresh_from_db()
    assert invoice11.paid_amount == 10
    assert invoice11.remaining_amount == 32
    invoice12.refresh_from_db()
    assert invoice12.paid_amount == 0
    assert invoice12.remaining_amount == 42

    params = {
        'amount': 10,
        'payment_type': 'check',
        'elements_to_pay': ','.join(
            ['line:%s' % line111.uuid, 'line:%s' % line112.uuid, 'line:%s' % line12.uuid]
        ),
    }
    with mock.patch('lingo.utils.requests_wrapper.RequestsSession.send') as mock_send:
        resp = app.post('/api/regie/foo/payments/', params=params)
    assert [x[0][0].url for x in mock_send.call_args_list] == []
    assert Payment.objects.count() == 2
    assert InvoiceLinePayment.objects.count() == 3
    payment = Payment.objects.latest('pk')
    assert payment.regie == regie
    assert payment.amount == 10
    assert payment.payment_type.slug == 'check'
    assert payment.payment_type.regie == regie
    assert payment.payer_external_id == 'payer:1'
    assert payment.payer_first_name == 'First1'
    assert payment.payer_last_name == 'Name1'
    assert payment.payer_address == '41 rue des kangourous\n99999 Kangourou Ville'
    assert payment.payer_email == 'email1'
    assert payment.payer_phone == 'phone1'
    assert payment.transaction_id is None
    assert payment.transaction_date is None
    assert payment.order_id is None
    assert payment.bank_transaction_id is None
    assert payment.bank_transaction_date is None
    assert payment.bank_data == {}
    assert payment.date_payment is None
    assert payment.number == 5
    assert payment.formatted_number == 'R%02d-%s-0000005' % (
        regie.pk,
        now().strftime('%y-%m'),
    )
    (
        invoice_line_payment1,
        invoice_line_payment2,
    ) = payment.invoicelinepayment_set.order_by('pk')
    assert invoice_line_payment1.amount == -2
    assert invoice_line_payment1.line == line112
    assert invoice_line_payment2.amount == 12
    assert invoice_line_payment2.line == line111
    invoice11.refresh_from_db()
    assert invoice11.paid_amount == 20
    assert invoice11.remaining_amount == 22
    invoice12.refresh_from_db()
    assert invoice12.paid_amount == 0
    assert invoice12.remaining_amount == 42

    params = {
        'amount': 22.01,
        'payment_type': 'check',
        'elements_to_pay': ','.join(
            ['line:%s' % line111.uuid, 'line:%s' % line112.uuid, 'line:%s' % line12.uuid]
        ),
    }
    with mock.patch('lingo.utils.requests_wrapper.RequestsSession.send') as mock_send:
        resp = app.post('/api/regie/foo/payments/', params=params)
    assert [x[0][0].url for x in mock_send.call_args_list] == ['http://payment.com/invoice11/']
    assert Payment.objects.count() == 3
    assert InvoiceLinePayment.objects.count() == 5
    payment = Payment.objects.latest('pk')
    assert payment.regie == regie
    assert payment.amount == decimal.Decimal('22.01')
    assert payment.payment_type.slug == 'check'
    assert payment.payment_type.regie == regie
    assert payment.payer_external_id == 'payer:1'
    assert payment.payer_first_name == 'First1'
    assert payment.payer_last_name == 'Name1'
    assert payment.payer_address == '41 rue des kangourous\n99999 Kangourou Ville'
    assert payment.payer_email == 'email1'
    assert payment.payer_phone == 'phone1'
    assert payment.transaction_id is None
    assert payment.transaction_date is None
    assert payment.order_id is None
    assert payment.bank_transaction_id is None
    assert payment.bank_transaction_date is None
    assert payment.bank_data == {}
    assert payment.date_payment is None
    assert payment.number == 6
    assert payment.formatted_number == 'R%02d-%s-0000006' % (
        regie.pk,
        now().strftime('%y-%m'),
    )
    invoice_line_payment1, invoice_line_payment2 = payment.invoicelinepayment_set.order_by('pk')
    assert invoice_line_payment1.amount == 22
    assert invoice_line_payment1.line == line111  # older invoice first
    assert invoice_line_payment2.amount == decimal.Decimal('0.01')
    assert invoice_line_payment2.line == line12
    invoice11.refresh_from_db()
    assert invoice11.paid_amount == 42
    assert invoice11.remaining_amount == 0
    invoice12.refresh_from_db()
    assert invoice12.paid_amount == decimal.Decimal('0.01')
    assert invoice12.remaining_amount == decimal.Decimal('41.99')

    # to much
    params = {
        'amount': 42,
        'payment_type': 'check',
        'elements_to_pay': ','.join(
            ['line:%s' % line111.uuid, 'line:%s' % line112.uuid, 'line:%s' % line12.uuid]
        ),
    }
    with mock.patch('lingo.utils.requests_wrapper.RequestsSession.send') as mock_send:
        resp = app.post('/api/regie/foo/payments/', params=params, status=400)
    assert resp.json['err']
    assert resp.json['errors'] == {
        'amount': ['Amount is bigger than sum of invoice lines remaining amounts.']
    }

    params = {
        'amount': 41.99,
        'payment_type': 'check',
        'elements_to_pay': ','.join(
            ['line:%s' % line111.uuid, 'line:%s' % line112.uuid, 'line:%s' % line12.uuid]
        ),
    }
    with mock.patch('lingo.utils.requests_wrapper.RequestsSession.send') as mock_send:
        resp = app.post('/api/regie/foo/payments/', params=params)
    assert [x[0][0].url for x in mock_send.call_args_list] == ['http://payment.com/invoice12/']
    assert Payment.objects.count() == 4
    assert InvoiceLinePayment.objects.count() == 6
    payment = Payment.objects.latest('pk')
    assert payment.regie == regie
    assert payment.amount == decimal.Decimal('41.99')
    assert payment.payment_type.slug == 'check'
    assert payment.payment_type.regie == regie
    assert payment.payer_external_id == 'payer:1'
    assert payment.payer_first_name == 'First1'
    assert payment.payer_last_name == 'Name1'
    assert payment.payer_address == '41 rue des kangourous\n99999 Kangourou Ville'
    assert payment.payer_email == 'email1'
    assert payment.payer_phone == 'phone1'
    assert payment.transaction_id is None
    assert payment.transaction_date is None
    assert payment.order_id is None
    assert payment.bank_transaction_id is None
    assert payment.bank_transaction_date is None
    assert payment.bank_data == {}
    assert payment.date_payment is None
    assert payment.number == 7
    assert payment.formatted_number == 'R%02d-%s-0000007' % (
        regie.pk,
        now().strftime('%y-%m'),
    )
    (invoice_line_payment,) = payment.invoicelinepayment_set.order_by('pk')
    assert invoice_line_payment.amount == decimal.Decimal('41.99')
    assert invoice_line_payment.line == line12
    invoice11.refresh_from_db()
    assert invoice11.paid_amount == 42
    assert invoice11.remaining_amount == 0
    invoice12.refresh_from_db()
    assert invoice12.paid_amount == 42
    assert invoice12.remaining_amount == 0

    # pay many invoices in one payment
    InvoiceLinePayment.objects.all().delete()
    Payment.objects.all().delete()
    params = {
        'amount': 84,
        'payment_type': 'check',
        'elements_to_pay': ','.join([str(invoice12.uuid), str(invoice11.uuid)]),
    }
    with mock.patch('lingo.utils.requests_wrapper.RequestsSession.send') as mock_send:
        resp = app.post('/api/regie/foo/payments/', params=params)
    assert [x[0][0].url for x in mock_send.call_args_list] == [
        'http://payment.com/invoice11/',
        'http://payment.com/invoice12/',
    ]
    assert Payment.objects.count() == 1
    assert InvoiceLinePayment.objects.count() == 3
    payment = Payment.objects.latest('pk')
    assert payment.regie == regie
    assert payment.amount == 84
    assert payment.payment_type.slug == 'check'
    assert payment.payment_type.regie == regie
    assert payment.payer_external_id == 'payer:1'
    assert payment.payer_first_name == 'First1'
    assert payment.payer_last_name == 'Name1'
    assert payment.payer_address == '41 rue des kangourous\n99999 Kangourou Ville'
    assert payment.payer_email == 'email1'
    assert payment.payer_phone == 'phone1'
    assert payment.transaction_id is None
    assert payment.transaction_date is None
    assert payment.order_id is None
    assert payment.bank_transaction_id is None
    assert payment.bank_transaction_date is None
    assert payment.bank_data == {}
    assert payment.date_payment is None
    assert payment.number == 8
    assert payment.formatted_number == 'R%02d-%s-0000008' % (
        regie.pk,
        now().strftime('%y-%m'),
    )
    (
        invoice_line_payment1,
        invoice_line_payment2,
        invoice_line_payment3,
    ) = payment.invoicelinepayment_set.order_by('pk')
    assert invoice_line_payment1.amount == -2
    assert invoice_line_payment1.line == line112
    assert invoice_line_payment2.amount == 44
    assert invoice_line_payment2.line == line111
    assert invoice_line_payment3.amount == 42
    assert invoice_line_payment3.line == line12
    invoice11.refresh_from_db()
    assert invoice11.paid_amount == 42
    assert invoice11.remaining_amount == 0
    invoice12.refresh_from_db()
    assert invoice12.paid_amount == 42
    assert invoice12.remaining_amount == 0


@mock.patch.object(Regie, 'get_payer_external_id_from_nameid', autospec=True)
def test_list_payments(mock_payer, app, user):
    app.get('/api/regie/foo/payments/', status=403)
    app.authorization = ('Basic', ('john.doe', 'password'))

    app.get('/api/regie/foo/payments/', status=404)

    regie = Regie.objects.create(label='Foo')
    PaymentType.create_defaults(regie)
    app.get('/api/regie/foo/payments/', status=404)

    invoice = Invoice.objects.create(
        label='My invoice',
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=now().date(),
        date_due=datetime.date(2022, 10, 31),
        regie=regie,
        payer_external_id='payer:1',
    )
    invoice.set_number()
    invoice.save()
    line = InvoiceLine.objects.create(
        event_date=now().date(),
        invoice=invoice,
        quantity=1,
        unit_amount=42,
    )
    invoice.refresh_from_db()
    payment = Payment.objects.create(
        regie=regie,
        amount=42,
        payment_type=PaymentType.objects.get(regie=regie, slug='cash'),
        payer_external_id='payer:1',
    )
    payment.set_number()
    payment.save()
    InvoiceLinePayment.objects.create(
        payment=payment,
        line=line,
        amount=42,
    )

    mock_payer.return_value = 'payer:1'
    resp = app.get('/api/regie/foo/payments/', params={'NameID': 'foobar'})
    assert resp.json['err'] == 0
    assert resp.json['data'] == [
        {
            'id': str(payment.uuid),
            'display_id': 'R%02d-%s-0000001'
            % (
                regie.pk,
                payment.created_at.strftime('%y-%m'),
            ),
            'payment_type': 'Cash',
            'amount': 42,
            'created': now().date().isoformat(),
            'has_pdf': True,
        }
    ]
    assert mock_payer.call_args_list == [mock.call(regie, mock.ANY, 'foobar')]
    payment.date_payment = datetime.date(2022, 9, 1)
    payment.save()
    resp = app.get('/api/regie/foo/payments/', params={'NameID': 'foobar'})
    assert resp.json['data'][0]['created'] == '2022-09-01'
    assert 'real_created' not in resp.json['data'][0]
    payment.date_payment = None
    payment.save()

    # other regie
    other_regie = Regie.objects.create(label='Other Foo')
    payment.regie = other_regie
    payment.save()
    resp = app.get('/api/regie/foo/payments/', params={'NameID': 'foobar'})
    assert resp.json['err'] == 0
    assert resp.json['data'] == []

    # no matching payer id
    payment.regie = regie
    payment.save()
    mock_payer.return_value = 'payer:unknown'
    resp = app.get('/api/regie/foo/payments/', params={'NameID': 'foobar'})
    assert resp.json['err'] == 0
    assert resp.json['data'] == []

    # payer error
    mock_payer.side_effect = PayerError
    app.get('/api/regie/foo/payments/', params={'NameID': 'foobar'}, status=404)

    # payment is cancelled
    mock_payer.side_effect = None
    mock_payer.return_value = 'payer:1'
    payment.cancelled_at = now()
    payment.save()
    resp = app.get('/api/regie/foo/payments/', params={'NameID': 'foobar'})
    assert resp.json['err'] == 0
    assert resp.json['data'] == []

    # payment_type is collect
    payment.cancelled_at = None
    payment.payment_type = PaymentType.objects.create(regie=regie, slug='collect')
    payment.save()
    resp = app.get('/api/regie/foo/payments/', params={'NameID': 'foobar'})
    assert resp.json['err'] == 0
    assert resp.json['data'] == []


def test_list_payments_for_payer(app, user):
    app.get('/api/regie/foo/payments/', status=403)
    app.authorization = ('Basic', ('john.doe', 'password'))

    app.get('/api/regie/foo/payments/', status=404)

    regie = Regie.objects.create(label='Foo')
    PaymentType.create_defaults(regie)
    app.get('/api/regie/foo/payments/', status=404)

    resp = app.get('/api/regie/foo/payments/', params={'payer_external_id': 'payer:1'})
    assert resp.json['err'] == 0
    assert resp.json['data'] == []

    invoice = Invoice.objects.create(
        label='My invoice',
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=now().date(),
        date_due=datetime.date(2022, 10, 31),
        regie=regie,
        payer_external_id='payer:1',
    )
    invoice.set_number()
    invoice.save()
    line = InvoiceLine.objects.create(
        event_date=now().date(),
        invoice=invoice,
        quantity=1,
        unit_amount=42,
    )
    invoice.refresh_from_db()
    payment = Payment.objects.create(
        regie=regie,
        amount=42,
        payment_type=PaymentType.objects.get(regie=regie, slug='cash'),
        payer_external_id='payer:1',
    )
    payment.set_number()
    payment.save()
    InvoiceLinePayment.objects.create(
        payment=payment,
        line=line,
        amount=42,
    )

    resp = app.get('/api/regie/foo/payments/', params={'payer_external_id': 'payer:1'})
    assert resp.json['err'] == 0
    assert resp.json['data'] == [
        {
            'id': str(payment.uuid),
            'display_id': 'R%02d-%s-0000001'
            % (
                regie.pk,
                payment.created_at.strftime('%y-%m'),
            ),
            'payment_type': 'Cash',
            'amount': 42,
            'created': now().date().isoformat(),
            'has_pdf': True,
        }
    ]
    payment.date_payment = datetime.date(2022, 9, 1)
    payment.save()
    resp = app.get('/api/regie/foo/payments/', params={'payer_external_id': 'payer:1'})
    assert resp.json['data'][0]['created'] == '2022-09-01'
    assert resp.json['data'][0]['real_created'] == now().date().isoformat()
    payment.date_payment = None
    payment.save()

    # other regie
    other_regie = Regie.objects.create(label='Other Foo')
    payment.regie = other_regie
    payment.save()
    resp = app.get('/api/regie/foo/payments/', params={'payer_external_id': 'payer:1'})
    assert resp.json['err'] == 0
    assert resp.json['data'] == []

    # payment is cancelled
    payment.regie = regie
    payment.cancelled_at = now()
    payment.save()
    resp = app.get('/api/regie/foo/payments/', params={'payer_external_id': 'payer:1'})
    assert resp.json['err'] == 0
    assert resp.json['data'] == []

    # payment_type is collect
    payment.cancelled_at = None
    payment.payment_type = PaymentType.objects.create(regie=regie, slug='collect', label='Collect')
    payment.save()
    resp = app.get('/api/regie/foo/payments/', params={'payer_external_id': 'payer:1'})
    assert resp.json['err'] == 0
    assert resp.json['data'] == [
        {
            'id': str(payment.uuid),
            'display_id': 'R%02d-%s-0000001'
            % (
                regie.pk,
                payment.created_at.strftime('%y-%m'),
            ),
            'payment_type': 'Collect',
            'amount': 42,
            'created': now().date().isoformat(),
            'has_pdf': True,
        }
    ]


def test_patch_payment(app, user):
    app.get('/api/regie/foo/payment/%s/' % str(uuid.uuid4()), status=403)
    app.authorization = ('Basic', ('john.doe', 'password'))

    app.patch('/api/regie/foo/payment/%s/' % str(uuid.uuid4()), status=404)

    regie = Regie.objects.create(label='Foo')
    app.patch('/api/regie/foo/payment/%s/' % str(uuid.uuid4()), status=404)

    PaymentType.create_defaults(regie)
    payment = Payment.objects.create(
        regie=regie,
        amount=42,
        payment_type=PaymentType.objects.get(regie=regie, slug='credit'),
        payer_external_id='payer:1',
    )
    payment.set_number()
    payment.save()

    resp = app.patch('/api/regie/foo/payment/%s/' % str(payment.uuid))
    assert resp.json['err'] == 0
    assert resp.json['data'] == {
        'payment_id': str(payment.uuid),
        'urls': {
            'payment_in_backoffice': 'http://testserver/manage/invoicing/redirect/payment/%s/' % payment.uuid,
            'payment_pdf': 'http://testserver/manage/invoicing/redirect/payment/%s/pdf/' % payment.uuid,
        },
        'api_urls': {
            'payment_pdf': 'http://testserver/api/regie/foo/payment/%s/pdf/' % payment.uuid,
        },
    }
    payment.refresh_from_db()
    assert payment.payment_info == {}

    resp = app.patch('/api/regie/foo/payment/%s/' % str(payment.uuid), params={'payment_reference': '12345'})
    assert resp.json['err'] == 0
    payment.refresh_from_db()
    assert payment.payment_info == {
        'payment_reference': '12345',
    }

    resp = app.patch('/api/regie/foo/payment/%s/' % str(payment.uuid), params={'check_issuer': 'issuer'})
    assert resp.json['err'] == 0
    payment.refresh_from_db()
    assert payment.payment_info == {
        'check_issuer': 'issuer',
        'payment_reference': '12345',
    }

    resp = app.patch(
        '/api/regie/foo/payment/%s/' % str(payment.uuid),
        params={
            'check_bank': 'bank',
            'check_number': 'number',
            'bank_transfer_number': '34567',
            'payment_reference': '23456',
        },
    )
    assert resp.json['err'] == 0
    payment.refresh_from_db()
    assert payment.payment_info == {
        'check_issuer': 'issuer',
        'check_bank': 'bank',
        'check_number': 'number',
        'bank_transfer_number': '34567',
        'payment_reference': '23456',
    }

    resp = app.patch(
        '/api/regie/foo/payment/%s/' % str(payment.uuid),
        params={
            'check_bank': 'bank',
            'check_number': 'number',
            'bank_transfer_number': '34567',
            'payment_reference': '23456',
            'online_refdet': 'rreeffddeett',
        },
    )
    assert resp.json['err'] == 0
    payment.refresh_from_db()
    assert payment.payment_info == {
        'check_issuer': 'issuer',
        'check_bank': 'bank',
        'check_number': 'number',
        'bank_transfer_number': '34567',
        'payment_reference': '23456',
    }
    assert payment.bank_data == {}

    payment.payment_type = PaymentType.objects.get(regie=regie, slug='online')
    payment.save()
    resp = app.patch(
        '/api/regie/foo/payment/%s/' % str(payment.uuid),
        params={
            'check_bank': 'bank',
            'check_number': 'number',
            'bank_transfer_number': '34567',
            'payment_reference': '23456',
            'online_refdet': 'rreeffddeett',
        },
    )
    assert resp.json['err'] == 0
    payment.refresh_from_db()
    assert payment.payment_info == {
        'check_issuer': 'issuer',
        'check_bank': 'bank',
        'check_number': 'number',
        'bank_transfer_number': '34567',
        'payment_reference': '23456',
    }
    assert payment.bank_data == {'refdet': 'rreeffddeett'}


@mock.patch.object(Regie, 'get_payer_external_id_from_nameid', autospec=True)
def test_pdf_payment(mock_payer, app, user):
    app.get('/api/regie/foo/payment/%s/pdf/' % str(uuid.uuid4()), status=403)
    app.authorization = ('Basic', ('john.doe', 'password'))

    app.get('/api/regie/foo/payment/%s/pdf/' % str(uuid.uuid4()), status=404)

    regie = Regie.objects.create(label='Foo')
    PaymentType.create_defaults(regie)
    app.get('/api/regie/foo/payment/%s/pdf/' % str(uuid.uuid4()), status=404)

    app.get('/api/regie/foo/payment/%s/pdf/' % str(uuid.uuid4()), params={'NameID': 'foobar'}, status=404)

    invoice = Invoice.objects.create(
        label='My invoice',
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=now().date(),
        date_due=datetime.date(2022, 10, 31),
        regie=regie,
        payer_external_id='payer:1',
    )
    invoice.set_number()
    invoice.save()
    line = InvoiceLine.objects.create(
        event_date=now().date(),
        invoice=invoice,
        quantity=1,
        unit_amount=42,
    )
    invoice.refresh_from_db()
    payment = Payment.objects.create(
        regie=regie,
        amount=42,
        payment_type=PaymentType.objects.get(regie=regie, slug='cash'),
        payer_external_id='payer:1',
    )
    InvoiceLinePayment.objects.create(
        payment=payment,
        line=line,
        amount=42,
    )

    mock_payer.return_value = 'payer:1'
    resp = app.get('/api/regie/foo/payment/%s/pdf/' % str(payment.uuid), params={'NameID': 'foobar'})
    assert resp.headers['Content-Disposition'] == 'attachment; filename="%s.pdf"' % payment.formatted_number

    # other regie
    other_regie = Regie.objects.create(label='Other Foo')
    payment.regie = other_regie
    payment.save()
    app.get('/api/regie/foo/payment/%s/pdf/' % str(payment.uuid), params={'NameID': 'foobar'}, status=404)

    # no matching payer id
    payment.regie = regie
    payment.save()
    mock_payer.return_value = 'payer:unknown'
    app.get('/api/regie/foo/payment/%s/pdf/' % str(payment.uuid), params={'NameID': 'foobar'}, status=404)

    # payer error
    mock_payer.side_effect = PayerError
    app.get('/api/regie/foo/payment/%s/pdf/' % str(payment.uuid), params={'NameID': 'foobar'}, status=404)

    # payment is cancelled
    mock_payer.return_value = 'payer:1'
    payment.cancelled_at = now()
    payment.save()
    app.get('/api/regie/foo/payment/%s/pdf/' % str(payment.uuid), params={'NameID': 'foobar'}, status=404)

    # payment_type is collect
    payment.cancelled_at = None
    payment.payment_type = PaymentType.objects.create(regie=regie, slug='collect')
    payment.save()
    app.get('/api/regie/foo/payment/%s/pdf/' % str(payment.uuid), params={'NameID': 'foobar'}, status=404)


def test_pdf_payment_for_payer(app, user):
    app.get('/api/regie/foo/payment/%s/pdf/' % str(uuid.uuid4()), status=403)
    app.authorization = ('Basic', ('john.doe', 'password'))

    app.get('/api/regie/foo/payment/%s/pdf/' % str(uuid.uuid4()), status=404)

    regie = Regie.objects.create(label='Foo')
    PaymentType.create_defaults(regie)
    app.get('/api/regie/foo/payment/%s/pdf/' % str(uuid.uuid4()), status=404)

    app.get(
        '/api/regie/foo/payment/%s/pdf/' % str(uuid.uuid4()),
        params={'payer_external_id': 'payer:1'},
        status=404,
    )

    invoice = Invoice.objects.create(
        label='My invoice',
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=now().date(),
        regie=regie,
        payer_external_id='payer:1',
    )
    invoice.set_number()
    invoice.save()
    line = InvoiceLine.objects.create(
        event_date=now().date(),
        invoice=invoice,
        quantity=1,
        unit_amount=42,
    )
    invoice.refresh_from_db()
    payment = Payment.objects.create(
        regie=regie,
        amount=42,
        payment_type=PaymentType.objects.get(regie=regie, slug='cash'),
        payer_external_id='payer:1',
    )
    InvoiceLinePayment.objects.create(
        payment=payment,
        line=line,
        amount=42,
    )

    resp = app.get(
        '/api/regie/foo/payment/%s/pdf/' % str(payment.uuid), params={'payer_external_id': 'payer:1'}
    )
    assert resp.headers['Content-Disposition'] == 'attachment; filename="%s.pdf"' % payment.formatted_number

    # other regie
    other_regie = Regie.objects.create(label='Other Foo')
    payment.regie = other_regie
    payment.save()
    app.get(
        '/api/regie/foo/payment/%s/pdf/' % str(payment.uuid),
        params={'payer_external_id': 'payer:1'},
        status=404,
    )

    # payment is cancelled
    payment.regie = regie
    payment.cancelled_at = now()
    payment.save()
    app.get(
        '/api/regie/foo/payment/%s/pdf/' % str(payment.uuid),
        params={'payer_external_id': 'payer:1'},
        status=404,
    )

    # payment_type is collect
    payment.cancelled_at = None
    payment.payment_type = PaymentType.objects.create(regie=regie, slug='collect')
    payment.save()
    app.get(
        '/api/regie/foo/payment/%s/pdf/' % str(payment.uuid),
        params={'payer_external_id': 'payer:1'},
        status=200,
    )
