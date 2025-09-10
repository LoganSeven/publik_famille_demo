import datetime

import pytest
from django.utils.timezone import localtime, make_aware, now
from pyquery import PyQuery

from lingo.agendas.models import Agenda
from lingo.invoicing.models import (
    CollectionDocket,
    Credit,
    CreditAssignment,
    CreditLine,
    Invoice,
    InvoiceLine,
    InvoiceLinePayment,
    Payment,
    PaymentCancellationReason,
    PaymentDocket,
    PaymentType,
    Regie,
)
from tests.utils import get_ods_rows, login

pytestmark = pytest.mark.django_db


def test_regie_payments(app, admin_user):
    regie = Regie.objects.create(label='Foo')
    Agenda.objects.create(label='Agenda A', regie=regie)
    Agenda.objects.create(label='Agenda B', regie=regie)
    PaymentType.create_defaults(regie)
    invoice1 = Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        regie=regie,
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Name1',
        payer_address='41 rue des kangourous\n99999 Kangourou Ville',
        payer_direct_debit=False,
    )
    invoice1.set_number()
    invoice1.save()
    collection = CollectionDocket.objects.create(regie=regie, date_end=now().date(), draft=False)
    invoice2 = Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_debit=datetime.date(2022, 11, 15),
        regie=regie,
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Name1',
        payer_address='41 rue des kangourous\n99999 Kangourou Ville',
        payer_direct_debit=True,
        collection=collection,
    )
    invoice2.set_number()
    invoice2.save()
    invoice3 = Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_debit=datetime.date(2022, 11, 15),
        regie=regie,
        payer_external_id='payer:3',
        payer_first_name='First3',
        payer_last_name='Name3',
        payer_address='43 rue des kangourous\n99999 Kangourou Ville',
        payer_direct_debit=True,
    )
    invoice3.set_number()
    invoice3.save()

    invoice_line1 = InvoiceLine.objects.create(
        label='Event A',
        event_date=datetime.date(2022, 9, 1),
        invoice=invoice1,
        quantity=1,
        unit_amount=40,
        accounting_code='424242',
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
        details={
            'agenda': 'agenda-a',
            'primary_event': 'event-a',
        },
        event_slug='agenda-a@event-a',
        agenda_slug='agenda-a',
        activity_label='Agenda A',
    )
    invoice_line21 = InvoiceLine.objects.create(
        # non recurring event
        label='Event B',
        event_date=datetime.date(2022, 9, 1),
        invoice=invoice2,
        quantity=1,
        unit_amount=20,
        event_slug='agenda-b@event-b',
        agenda_slug='agenda-b',
        activity_label='Agenda B',
        accounting_code='424243',
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
    )
    invoice_line22 = InvoiceLine.objects.create(
        # non recurring event
        label='Event C',
        event_date=datetime.date(2022, 9, 1),
        invoice=invoice2,
        quantity=1,
        unit_amount=30,
        event_slug='agenda-b@event-c',
        agenda_slug='agenda-b',
        activity_label='Agenda B',
        accounting_code='424244',
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
    )
    invoice_line3 = InvoiceLine.objects.create(
        label='Event A',
        event_date=datetime.date(2022, 9, 1),
        invoice=invoice3,
        quantity=1,
        unit_amount=60,
        event_slug='injected',
        accounting_code='424242',
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
    )

    payment1 = Payment.objects.create(
        regie=regie,
        amount=35,
        payment_type=PaymentType.objects.get(regie=regie, slug='cash'),
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Name1',
        payer_address='41 rue des kangourous\n99999 Kangourou Ville',
        payment_info={
            'check_number': '123456',
        },
        bank_data={'refdet': 'REFDET'},
        date_payment=datetime.date(2024, 10, 13),
    )
    payment1.set_number()
    payment1.save()
    InvoiceLinePayment.objects.create(
        payment=payment1,
        line=invoice_line1,
        amount=35,
    )

    payment2 = Payment.objects.create(
        regie=regie,
        amount=55,
        payment_type=PaymentType.objects.get(regie=regie, slug='check'),
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Name1',
        payer_address='41 rue des kangourous\n99999 Kangourou Ville',
        payment_info={
            'check_number': '123456',
            'check_issuer': 'Foo',
            'check_bank': 'Bar',
            'bank_transfer_number': '234567',
            'payment_reference': 'Ref',
        },
    )
    payment2.set_number()
    payment2.save()
    InvoiceLinePayment.objects.create(
        payment=payment2,
        line=invoice_line1,
        amount=5,
    )
    InvoiceLinePayment.objects.create(
        payment=payment2,
        line=invoice_line21,
        amount=20,
    )
    InvoiceLinePayment.objects.create(
        payment=payment2,
        line=invoice_line22,
        amount=30,
    )

    docket = PaymentDocket.objects.create(
        regie=regie, date_end=now().date() + datetime.timedelta(days=1), draft=False
    )
    docket.set_number()
    docket.save()
    payment3 = Payment.objects.create(
        regie=regie,
        amount=2,
        payment_type=PaymentType.objects.get(regie=regie, slug='credit'),
        payer_external_id='payer:3',
        payer_first_name='First3',
        payer_last_name='Name3',
        payer_address='43 rue des kangourous\n99999 Kangourou Ville',
        docket=docket,
    )
    payment3.set_number()
    payment3.save()
    InvoiceLinePayment.objects.create(
        payment=payment3,
        line=invoice_line3,
        amount=2,
    )
    credit1 = Credit.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        regie=regie,
    )
    credit1.set_number()
    credit1.save()
    CreditLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        quantity=1,
        unit_amount=1,
        credit=credit1,
    )
    CreditAssignment.objects.create(
        invoice=invoice3,
        payment=payment3,
        credit=credit1,
        amount=1,
    )
    credit2 = Credit.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        regie=regie,
    )
    credit2.set_number()
    credit2.save()
    CreditLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        quantity=1,
        unit_amount=1,
        credit=credit2,
    )
    CreditAssignment.objects.create(
        invoice=invoice3,
        payment=payment3,
        credit=credit2,
        amount=1,
    )

    payment4 = Payment.objects.create(
        regie=regie,
        amount=2,
        payment_type=PaymentType.objects.get(regie=regie, slug='check'),
        payer_external_id='payer:3',
        payer_first_name='First3',
        payer_last_name='Name3',
        payer_address='43 rue des kangourous\n99999 Kangourou Ville',
        cancelled_at=now(),
        cancelled_by=admin_user,
        cancellation_reason=PaymentCancellationReason.objects.create(label='Uncovered check'),
        cancellation_description='foo bar\nblah',
    )
    payment4.created_at = make_aware(datetime.datetime(2024, 10, 13, 1, 11))
    payment4.set_number()
    payment4.save()

    invoice1.refresh_from_db()
    assert invoice1.remaining_amount == 0
    assert invoice1.paid_amount == 40
    invoice2.refresh_from_db()
    assert invoice2.remaining_amount == 0
    assert invoice2.paid_amount == 50
    invoice3.refresh_from_db()
    assert invoice3.remaining_amount == 58
    assert invoice3.paid_amount == 2

    app = login(app)
    resp = app.get('/manage/invoicing/regie/%s/payments/' % regie.pk)
    assert resp.pyquery(
        'tr[data-invoicing-element-id="%s"]' % payment1.pk
    ).text() == 'Payment R%02d-%s-0000001 dated %s (created on %s) from First1 Name1, amount 35.00€ (Cash) - download' % (
        regie.pk,
        payment1.date_payment.strftime('%y-%m'),
        payment1.date_payment.strftime('%d/%m/%Y'),
        payment1.created_at.strftime('%d/%m/%Y'),
    )
    assert [
        PyQuery(tr).text() for tr in resp.pyquery('tr[data-related-invoicing-element-id="%s"]' % payment1.pk)
    ] == [
        'Invoice\nAmount charged\nAmount assigned',
        'F%02d-%s-0000001\n40.00€\n35.00€'
        % (
            regie.pk,
            invoice1.created_at.strftime('%y-%m'),
        ),
        'Check number: 123456',
        'Debt reference: REFDET',
        'Cancel payment',
    ]
    assert resp.pyquery(
        'tr[data-invoicing-element-id="%s"]' % payment2.pk
    ).text() == 'Payment %s dated %s from First1 Name1, amount 55.00€ (Check) - download' % (
        payment2.formatted_number,
        payment2.created_at.strftime('%d/%m/%Y'),
    )
    assert [
        PyQuery(tr).text() for tr in resp.pyquery('tr[data-related-invoicing-element-id="%s"]' % payment2.pk)
    ] == [
        'Invoice\nAmount charged\nAmount assigned',
        'F%02d-%s-0000001\n40.00€\n5.00€'
        % (
            regie.pk,
            invoice1.created_at.strftime('%y-%m'),
        ),
        'F%02d-%s-0000002\n50.00€\n50.00€'
        % (
            regie.pk,
            invoice2.created_at.strftime('%y-%m'),
        ),
        'Check issuer: Foo',
        'Check bank/organism: Bar',
        'Check number: 123456',
        'Bank transfer number: 234567',
        'Reference: Ref',
    ]
    assert resp.pyquery(
        'tr[data-invoicing-element-id="%s"]' % payment3.pk
    ).text() == 'Payment %s dated %s from First3 Name3, amount 2.00€ (Credit) - download' % (
        payment3.formatted_number,
        payment3.created_at.strftime('%d/%m/%Y'),
    )
    assert [
        PyQuery(tr).text() for tr in resp.pyquery('tr[data-related-invoicing-element-id="%s"]' % payment3.pk)
    ] == [
        'Invoice\nAmount charged\nAmount assigned',
        'F%02d-%s-0000003 (%s - %s)\n60.00€\n2.00€'
        % (
            regie.pk,
            invoice3.created_at.strftime('%y-%m'),
            credit1.formatted_number,
            credit2.formatted_number,
        ),
        'Docket: B%02d-%s-0000001' % (regie.pk, docket.created_at.strftime('%y-%m')),
        'Cancel payment',
    ]
    assert resp.pyquery(
        'tr[data-invoicing-element-id="%s"]' % payment4.pk
    ).text() == 'Cancelled Payment %s dated %s from First3 Name3, amount 2.00€ (Check) - download' % (
        payment4.formatted_number,
        payment4.created_at.strftime('%d/%m/%Y'),
    )
    assert [
        PyQuery(tr).text() for tr in resp.pyquery('tr[data-related-invoicing-element-id="%s"]' % payment4.pk)
    ] == [
        'Invoice\nAmount charged\nAmount assigned',
        'No assignments for this payment',
        'Cancelled on: %s' % localtime(payment4.cancelled_at).strftime('%d/%m/%Y %H:%M'),
        'Cancelled by: admin',
        'Reason: Uncovered check',
        'Description: foo bar\nblah',
    ]

    resp = app.get('/manage/invoicing/regie/%s/payments/?ods' % regie.pk)
    assert resp.headers['Content-Type'] == 'application/vnd.oasis.opendocument.spreadsheet'
    assert resp.headers['Content-Disposition'] == 'attachment; filename="payments.ods"'
    rows = list(get_ods_rows(resp))
    assert len(rows) == 1 + 5
    assert rows == [
        [
            'Number',
            'Invoice number',
            'Creation date',
            'Payment date',
            'Payer ID',
            'Payer first name',
            'Payer last name',
            'Payment type',
            'Credit numbers',
            'Amount assigned (invoice)',
            'Total amount (payment)',
            'Check issuer',
            'Check bank/organism',
            'Check number',
            'Bank transfer number',
            'Reference',
            'Debt reference',
            'Cancelled on',
            'Cancellation reason',
        ],
        [
            payment3.formatted_number,
            invoice3.formatted_number,
            payment3.created_at.strftime('%m/%d/%Y'),
            None,
            'payer:3',
            'First3',
            'Name3',
            'Credit',
            f'{credit1.formatted_number}, {credit2.formatted_number}',
            '2.00',
            '2.00',
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
        ],
        [
            payment2.formatted_number,
            invoice1.formatted_number,
            payment2.created_at.strftime('%m/%d/%Y'),
            None,
            'payer:1',
            'First1',
            'Name1',
            'Check',
            None,
            '5.00',
            '55.00',
            'Foo',
            'Bar',
            '123456',
            '234567',
            'Ref',
            None,
            None,
            None,
        ],
        [
            payment2.formatted_number,
            invoice2.formatted_number,
            payment2.created_at.strftime('%m/%d/%Y'),
            None,
            'payer:1',
            'First1',
            'Name1',
            'Check',
            None,
            '50.00',
            '55.00',
            'Foo',
            'Bar',
            '123456',
            '234567',
            'Ref',
            None,
            None,
            None,
        ],
        [
            payment1.formatted_number,
            invoice1.formatted_number,
            payment1.created_at.strftime('%m/%d/%Y'),
            payment1.date_payment.strftime('%m/%d/%Y'),
            'payer:1',
            'First1',
            'Name1',
            'Cash',
            None,
            '35.00',
            '35.00',
            None,
            None,
            '123456',
            None,
            None,
            'REFDET',
            None,
            None,
        ],
        [
            payment4.formatted_number,
            None,
            payment4.created_at.strftime('%m/%d/%Y'),
            None,
            'payer:3',
            'First3',
            'Name3',
            'Check',
            None,
            None,
            '2.00',
            None,
            None,
            None,
            None,
            None,
            None,
            payment4.cancelled_at.strftime('%m/%d/%Y'),
            'Uncovered check',
        ],
    ]

    resp = app.get('/manage/invoicing/regie/%s/payments/?ods&full' % regie.pk)
    assert resp.headers['Content-Type'] == 'application/vnd.oasis.opendocument.spreadsheet'
    assert resp.headers['Content-Disposition'] == 'attachment; filename="payments_full.ods"'
    rows = list(get_ods_rows(resp))
    assert len(rows) == 1 + 6
    assert rows == [
        [
            'Number',
            'Invoice number',
            'Creation date',
            'Payment date',
            'Payer ID',
            'Payer first name',
            'Payer last name',
            'Description (line)',
            'Accounting code (line)',
            'Amount (line)',
            'Quantity (line)',
            'Subtotal (line)',
            'Amount assigned (line)',
            'Payment type',
            'Credit numbers',
            'Amount assigned (invoice)',
            'Total amount (payment)',
            'Check issuer',
            'Check bank/organism',
            'Check number',
            'Bank transfer number',
            'Reference',
            'Debt reference',
            'Cancelled on',
            'Cancellation reason',
        ],
        [
            payment3.formatted_number,
            invoice3.formatted_number,
            payment3.created_at.strftime('%m/%d/%Y'),
            None,
            'payer:3',
            'First3',
            'Name3',
            'Event A',
            '424242',
            '60.00',
            '1.00',
            '60.00',
            '2.00',
            'Credit',
            f'{credit1.formatted_number}, {credit2.formatted_number}',
            '2.00',
            '2.00',
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
        ],
        [
            payment2.formatted_number,
            invoice1.formatted_number,
            payment2.created_at.strftime('%m/%d/%Y'),
            None,
            'payer:1',
            'First1',
            'Name1',
            'Event A',
            '424242',
            '40.00',
            '1.00',
            '40.00',
            '5.00',
            'Check',
            None,
            '5.00',
            '55.00',
            'Foo',
            'Bar',
            '123456',
            '234567',
            'Ref',
            None,
            None,
            None,
        ],
        [
            payment2.formatted_number,
            invoice2.formatted_number,
            payment2.created_at.strftime('%m/%d/%Y'),
            None,
            'payer:1',
            'First1',
            'Name1',
            'Event B',
            '424243',
            '20.00',
            '1.00',
            '20.00',
            '20.00',
            'Check',
            None,
            '50.00',
            '55.00',
            'Foo',
            'Bar',
            '123456',
            '234567',
            'Ref',
            None,
            None,
            None,
        ],
        [
            payment2.formatted_number,
            invoice2.formatted_number,
            payment2.created_at.strftime('%m/%d/%Y'),
            None,
            'payer:1',
            'First1',
            'Name1',
            'Event C',
            '424244',
            '30.00',
            '1.00',
            '30.00',
            '30.00',
            'Check',
            None,
            '50.00',
            '55.00',
            'Foo',
            'Bar',
            '123456',
            '234567',
            'Ref',
            None,
            None,
            None,
        ],
        [
            payment1.formatted_number,
            invoice1.formatted_number,
            payment1.created_at.strftime('%m/%d/%Y'),
            payment1.date_payment.strftime('%m/%d/%Y'),
            'payer:1',
            'First1',
            'Name1',
            'Event A',
            '424242',
            '40.00',
            '1.00',
            '40.00',
            '35.00',
            'Cash',
            None,
            '35.00',
            '35.00',
            None,
            None,
            '123456',
            None,
            None,
            'REFDET',
            None,
            None,
        ],
        [
            payment4.formatted_number,
            None,
            payment4.created_at.strftime('%m/%d/%Y'),
            None,
            'payer:3',
            'First3',
            'Name3',
            None,
            None,
            None,
            None,
            None,
            None,
            'Check',
            None,
            None,
            '2.00',
            None,
            None,
            None,
            None,
            None,
            None,
            payment4.cancelled_at.strftime('%m/%d/%Y'),
            'Uncovered check',
        ],
    ]

    # test filters
    today = now().date()
    params = [
        ({'number': payment1.formatted_number}, 1, 1),
        ({'number': payment1.created_at.strftime('%y-%m')}, 2, 3),
        ({'created_at_after': today.strftime('%Y-%m-%d')}, 3, 4),
        ({'created_at_after': (today + datetime.timedelta(days=1)).strftime('%Y-%m-%d')}, 0, 0),
        ({'created_at_before': (today - datetime.timedelta(days=1)).strftime('%Y-%m-%d')}, 1, 1),
        ({'created_at_before': today.strftime('%Y-%m-%d')}, 4, 5),
        ({'invoice_number': invoice1.formatted_number}, 2, 3),
        ({'invoice_number': invoice1.created_at.strftime('%y-%m')}, 3, 4),
        ({'payer_external_id': 'payer:1'}, 2, 3),
        ({'payer_external_id': 'payer:3'}, 2, 2),
        ({'payer_first_name': 'first'}, 4, 5),
        ({'payer_first_name': 'first1'}, 2, 3),
        ({'payer_last_name': 'name'}, 4, 5),
        ({'payer_last_name': 'name1'}, 2, 3),
        ({'payment_type': PaymentType.objects.get(slug='cash').pk}, 1, 1),
        ({'payment_type': PaymentType.objects.get(slug='check').pk}, 2, 3),
        (
            {
                'amount_min': '2',
                'amount_min_lookup': 'gt',
            },
            2,
            3,
        ),
        (
            {
                'amount_min': '2',
                'amount_min_lookup': 'gte',
            },
            4,
            5,
        ),
        (
            {
                'amount_max': '55',
                'amount_max_lookup': 'lt',
            },
            3,
            3,
        ),
        (
            {
                'amount_max': '55',
                'amount_max_lookup': 'lte',
            },
            4,
            5,
        ),
        ({'agenda': 'agenda-a'}, 2, 3),
        ({'agenda': 'agenda-b'}, 1, 2),
        ({'event': 'agenda-a@event-a'}, 2, 3),
        ({'event': 'agenda-b@event-b'}, 1, 2),
        ({'accounting_code': '42'}, 0, 0),
        ({'accounting_code': '424242'}, 3, 4),
        ({'accounting_code': '424243'}, 1, 2),
        ({'cancelled': 'yes'}, 1, 1),
        ({'cancelled': 'no'}, 3, 4),
    ]
    for param, result, ods_result in params:
        resp = app.get(
            '/manage/invoicing/regie/%s/payments/' % regie.pk,
            params=param,
        )
        assert len(resp.pyquery('tr.payment')) == result
        param['ods'] = True
        resp = app.get(
            '/manage/invoicing/regie/%s/payments/' % regie.pk,
            params=param,
        )
        rows = list(get_ods_rows(resp))
        assert len(rows) == 1 + ods_result


def test_regie_payment_pdf(app, admin_user):
    regie = Regie.objects.create(
        label='Foo',
        controller_name='Le régisseur principal',
        city_name='Kangourou Ville',
        main_colour='#9141ac',
    )
    PaymentType.create_defaults(regie)
    invoice1 = Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        regie=regie,
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Name1',
        payer_address='41 rue des kangourous\n99999 Kangourou Ville',
        payer_direct_debit=False,
    )
    invoice1.set_number()
    invoice1.save()
    invoice2 = Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_debit=datetime.date(2022, 11, 15),
        regie=regie,
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Name1',
        payer_address='41 rue des kangourous\n99999 Kangourou Ville',
        payer_direct_debit=True,
    )
    invoice2.set_number()
    invoice2.save()

    invoice_line1 = InvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        invoice=invoice1,
        quantity=1,
        unit_amount=40,
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
    )
    invoice_line2 = InvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        invoice=invoice2,
        quantity=1,
        unit_amount=50,
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
    )

    payment = Payment.objects.create(
        regie=regie,
        amount=55,
        payment_type=PaymentType.objects.get(regie=regie, slug='check'),
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Name1',
        payer_address='41 rue des kangourous\n99999 Kangourou Ville',
        date_payment=datetime.date(2022, 8, 1),
    )
    payment.set_number()
    payment.save()
    InvoiceLinePayment.objects.create(
        payment=payment,
        line=invoice_line1,
        amount=5,
    )
    InvoiceLinePayment.objects.create(
        payment=payment,
        line=invoice_line2,
        amount=50,
    )
    payment.refresh_from_db()

    app = login(app)
    resp = app.get('/manage/invoicing/regie/%s/payment/%s/pdf/?html' % (regie.pk, payment.pk))
    assert 'color: #9141ac;' in resp
    assert resp.pyquery('#document-label').text() == 'Payment receipt'
    assert resp.pyquery('.address-to-container').text() == (
        'Invoiced account:\nFirst1 Name1 (1)\nInvoicing address:\n41 rue des kangourous\n99999 Kangourou Ville'
    )
    assert resp.pyquery('p#informations').text() == (
        'Hereby certifies that I have received a payment of type CHECK in the amount of 55.00€ (number R%02d-%s-0000001), for account First1 Name1 (1):'
    ) % (
        regie.pk,
        payment.date_payment.strftime('%y-%m'),
    )
    assert [PyQuery(tr).text() for tr in resp.pyquery.find('thead tr')] == [
        'Invoice number\nInvoice object\nAmount charged\nAmount assigned'
    ]
    assert [PyQuery(tr).text() for tr in resp.pyquery.find('tbody tr')] == [
        'F%02d-%s-0000001\n40.00€\n5.00€'
        % (
            regie.pk,
            invoice1.created_at.strftime('%y-%m'),
        ),
        'F%02d-%s-0000002\n50.00€\n50.00€'
        % (
            regie.pk,
            invoice2.created_at.strftime('%y-%m'),
        ),
    ]
    assert resp.pyquery(
        '#regie-signature'
    ).text() == 'Le régisseur principal\nKangourou Ville, on %s' % payment.date_payment.strftime('%d/%m/%Y')

    payment.date_payment = None
    payment.save()
    regie.city_name = ''
    regie.save()
    resp = app.get('/manage/invoicing/regie/%s/payment/%s/pdf/?html' % (regie.pk, payment.pk))
    assert resp.pyquery(
        '#regie-signature'
    ).text() == 'Le régisseur principal\non %s' % payment.created_at.strftime('%d/%m/%Y')

    resp = app.get('/manage/invoicing/regie/%s//payment/%s/pdf/?html' % (0, payment.pk), status=404)
    resp = app.get('/manage/invoicing/regie/%s//payment/%s/pdf/?html' % (regie.pk, 0), status=404)
    other_regie = Regie.objects.create(label='Foo')
    resp = app.get(
        '/manage/invoicing/regie/%s/payment/%s/pdf/?html' % (other_regie.pk, payment.pk), status=404
    )


def test_regie_payment_cancel(app, admin_user):
    regie = Regie.objects.create(
        label='Foo',
    )
    docket = PaymentDocket.objects.create(
        regie=regie, date_end=now().date() + datetime.timedelta(days=1), draft=False
    )
    docket.set_number()
    docket.save()
    PaymentType.create_defaults(regie)
    invoice1 = Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        regie=regie,
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Name1',
        payer_address='41 rue des kangourous\n99999 Kangourou Ville',
        payer_direct_debit=False,
    )
    invoice1.set_number()
    invoice1.save()
    invoice2 = Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_debit=datetime.date(2022, 11, 15),
        regie=regie,
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Name1',
        payer_address='41 rue des kangourous\n99999 Kangourou Ville',
        payer_direct_debit=True,
    )
    invoice2.set_number()
    invoice2.save()

    invoice_line1 = InvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        invoice=invoice1,
        quantity=1,
        unit_amount=40,
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
    )
    invoice_line2 = InvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        invoice=invoice2,
        quantity=1,
        unit_amount=50,
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
    )

    payment = Payment.objects.create(
        regie=regie,
        amount=55,
        payment_type=PaymentType.objects.get(regie=regie, slug='check'),
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Name1',
        payer_address='41 rue des kangourous\n99999 Kangourou Ville',
    )
    payment.set_number()
    payment.save()
    InvoiceLinePayment.objects.create(
        payment=payment,
        line=invoice_line1,
        amount=5,
    )
    InvoiceLinePayment.objects.create(
        payment=payment,
        line=invoice_line2,
        amount=50,
    )
    cancellation_reason = PaymentCancellationReason.objects.create(label='Uncovered check')
    PaymentCancellationReason.objects.create(label='Disabled', disabled=True)
    payment2 = Payment.objects.create(
        regie=regie,
        amount=5,
        payment_type=PaymentType.objects.get(regie=regie, slug='check'),
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Name1',
        payer_address='41 rue des kangourous\n99999 Kangourou Ville',
        docket=docket,
    )
    payment2.set_number()
    payment2.save()
    InvoiceLinePayment.objects.create(
        payment=payment2,
        line=invoice_line1,
        amount=5,
    )
    payment3 = Payment.objects.create(
        regie=regie,
        amount=5,
        payment_type=PaymentType.objects.get(regie=regie, slug='credit'),
        payer_external_id='payer:1',
    )
    payment3.set_number()
    payment3.save()
    InvoiceLinePayment.objects.create(
        payment=payment3,
        line=invoice_line1,
        amount=5,
    )
    credit = Credit.objects.create(
        label='Credit from 01/09/2022',
        date_publication=datetime.date(2022, 10, 1),
        regie=regie,
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Name1',
        payer_address='41 rue des kangourous\n99999 Kangourou Ville',
    )
    credit.set_number()
    credit.save()
    CreditLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        credit=credit,
        quantity=5,
        unit_amount=1,
        label='Label 11',
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
    )
    CreditAssignment.objects.create(
        invoice=invoice1,
        payment=payment3,
        credit=credit,
        amount=5,
    )
    credit.refresh_from_db()

    app = login(app)
    resp = app.get('/manage/invoicing/regie/%s/payments/' % regie.pk)
    resp = resp.click(href='/manage/invoicing/regie/%s/payment/%s/cancel/' % (regie.pk, payment.pk))
    assert 'This payment is included in a docket.' not in resp
    assert 'If you cancel this payment, the docket will be modified.' not in resp
    assert resp.form['cancellation_reason'].options == [
        ('', True, '---------'),
        (str(cancellation_reason.pk), False, 'Uncovered check'),
    ]
    resp.form['cancellation_reason'] = cancellation_reason.pk
    resp.form['cancellation_description'] = 'foo bar blah'
    resp = resp.form.submit()
    assert resp.location.endswith(
        '/manage/invoicing/regie/%s/payments/?number=%s' % (regie.pk, payment.formatted_number)
    )
    payment.refresh_from_db()
    assert payment.cancelled_at is not None
    assert payment.cancelled_by == admin_user
    assert payment.cancellation_reason == cancellation_reason
    assert payment.cancellation_description == 'foo bar blah'
    assert payment.invoicelinepayment_set.count() == 0
    assert payment2.invoicelinepayment_set.count() == 1
    assert payment3.invoicelinepayment_set.count() == 1
    assert payment3.creditassignment_set.count() == 1

    # already cancelled
    app.get('/manage/invoicing/regie/%s/payment/%s/cancel/' % (regie.pk, payment.pk), status=404)
    payment.cancelled_at = None
    payment.save()
    InvoiceLinePayment.objects.create(
        payment=payment,
        line=invoice_line1,
        amount=5,
    )
    InvoiceLinePayment.objects.create(
        payment=payment,
        line=invoice_line2,
        amount=50,
    )

    # other regie
    other_regie = Regie.objects.create(label='Foo')
    app.get('/manage/invoicing/regie/%s/payment/%s/cancel/' % (other_regie.pk, payment.pk), status=404)

    # payment in docket
    resp = app.get('/manage/invoicing/regie/%s/payment/%s/cancel/' % (regie.pk, payment2.pk))
    assert 'This payment is included in a docket.' in resp
    assert 'If you cancel this payment, the docket will be modified.' in resp
    resp.form['cancellation_reason'] = cancellation_reason.pk
    resp.form['cancellation_description'] = 'foo bar blah'
    resp = resp.form.submit()
    assert resp.location.endswith(
        '/manage/invoicing/regie/%s/payments/?number=%s' % (regie.pk, payment2.formatted_number)
    )
    payment2.refresh_from_db()
    assert payment2.cancelled_at is not None
    assert payment2.cancelled_by == admin_user
    assert payment2.cancellation_reason == cancellation_reason
    assert payment2.cancellation_description == 'foo bar blah'
    assert payment2.invoicelinepayment_set.count() == 0

    # payment with credit
    resp = app.get('/manage/invoicing/regie/%s/payment/%s/cancel/' % (regie.pk, payment3.pk))
    resp.form['cancellation_reason'] = cancellation_reason.pk
    resp.form['cancellation_description'] = 'foo bar blah'
    resp = resp.form.submit()
    assert resp.location.endswith(
        '/manage/invoicing/regie/%s/payments/?number=%s' % (regie.pk, payment3.formatted_number)
    )
    payment3.refresh_from_db()
    assert payment3.cancelled_at is not None
    assert payment3.cancelled_by == admin_user
    assert payment3.cancellation_reason == cancellation_reason
    assert payment3.cancellation_description == 'foo bar blah'
    assert payment3.invoicelinepayment_set.count() == 0
    assert payment3.creditassignment_set.count() == 0

    # collected invoice
    collection = CollectionDocket.objects.create(regie=regie, date_end=now().date(), draft=False)
    invoice1.collection = collection
    invoice1.save()
    app.get('/manage/invoicing/regie/%s/payment/%s/cancel/' % (regie.pk, payment.pk), status=404)
