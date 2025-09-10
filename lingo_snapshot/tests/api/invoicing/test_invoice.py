import datetime
import decimal
import random
import uuid
from unittest import mock

import pytest
from django.utils.timezone import make_aware, now

from lingo.agendas.models import Agenda
from lingo.basket.models import Basket
from lingo.epayment.models import PaymentBackend
from lingo.invoicing.errors import PayerError
from lingo.invoicing.models import (
    Campaign,
    CollectionDocket,
    Credit,
    CreditAssignment,
    CreditLine,
    DraftInvoice,
    DraftInvoiceLine,
    Invoice,
    InvoiceCancellationReason,
    InvoiceLine,
    InvoiceLinePayment,
    Payment,
    PaymentType,
    Pool,
    Regie,
)

pytestmark = pytest.mark.django_db


@mock.patch.object(Regie, 'get_payer_external_id_from_nameid', autospec=True)
def test_list_invoices(mock_payer, app, user):
    app.get('/api/regie/foo/invoices/', status=403)
    app.authorization = ('Basic', ('john.doe', 'password'))

    app.get('/api/regie/foo/invoices/', status=404)

    regie = Regie.objects.create(label='Foo')
    PaymentType.create_defaults(regie)
    app.get('/api/regie/foo/invoices/', status=404)

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
    invoice.refresh_from_db()

    # invoice remaining_amount is 0
    assert invoice.remaining_amount == 0
    mock_payer.return_value = 'payer:1'
    resp = app.get('/api/regie/foo/invoices/', params={'NameID': 'foobar'})
    assert resp.json['err'] == 0
    assert resp.json['data'] == []
    assert mock_payer.call_args_list == [mock.call(regie, mock.ANY, 'foobar')]

    # invoice with something to pay
    line = InvoiceLine.objects.create(
        event_date=now().date(),
        invoice=invoice,
        quantity=1,
        unit_amount=42,
    )
    invoice.refresh_from_db()
    resp = app.get('/api/regie/foo/invoices/', params={'NameID': 'foobar'})
    assert resp.json['err'] == 0
    assert resp.json['data'] == [
        {
            'amount': 42,
            'total_amount': 42,
            'remaining_amount': 42,
            'created': now().date().isoformat(),
            'display_id': 'F%02d-%s-0000001'
            % (
                regie.pk,
                invoice.created_at.strftime('%y-%m'),
            ),
            'has_pdf': True,
            'has_dynamic_pdf': False,
            'has_payments_pdf': False,
            'id': str(invoice.uuid),
            'invoice_label': 'My invoice',
            'is_line': False,
            'label': 'My invoice',
            'online_payment': False,
            'no_online_payment_reason': 'no-payment-system-configured',
            'paid': False,
            'pay_limit_date': now().date().isoformat(),
            'due_date': invoice.date_due.isoformat(),
            'payment_deadline_date': invoice.date_payment_deadline.isoformat(),
            'disabled': False,
        }
    ]
    PaymentBackend.objects.create(label='Test', service='dummy', regie=regie)
    resp = app.get('/api/regie/foo/invoices/', params={'NameID': 'foobar'})
    assert resp.json['err'] == 0
    assert resp.json['data'] == [
        {
            'amount': 42,
            'total_amount': 42,
            'remaining_amount': 42,
            'created': now().date().isoformat(),
            'display_id': 'F%02d-%s-0000001'
            % (
                regie.pk,
                invoice.created_at.strftime('%y-%m'),
            ),
            'has_pdf': True,
            'has_dynamic_pdf': False,
            'has_payments_pdf': False,
            'id': str(invoice.uuid),
            'invoice_label': 'My invoice',
            'is_line': False,
            'label': 'My invoice',
            'online_payment': True,
            'paid': False,
            'pay_limit_date': now().date().isoformat(),
            'due_date': invoice.date_due.isoformat(),
            'payment_deadline_date': invoice.date_payment_deadline.isoformat(),
            'disabled': False,
            'api': {
                'payment_url': 'http://testserver/pay/invoice/%s/' % invoice.uuid,
            },
        }
    ]
    with mock.patch('eopayment.Payment.get_minimal_amount', return_value=100):
        resp = app.get('/api/regie/foo/invoices/', params={'NameID': 'foobar'})
        assert resp.json['data'][0]['online_payment'] is False
        assert resp.json['data'][0]['no_online_payment_reason'] == 'amount-to-low'
        assert 'api' not in resp.json['data'][0]
    with mock.patch('eopayment.Payment.get_maximal_amount', return_value=0):
        resp = app.get('/api/regie/foo/invoices/', params={'NameID': 'foobar'})
        assert resp.json['data'][0]['online_payment'] is False
        assert resp.json['data'][0]['no_online_payment_reason'] == 'amount-to-high'
        assert 'api' not in resp.json['data'][0]
    resp = app.get('/api/regie/foo/invoices/', params={'NameID': 'foobar', 'payable': 'foo'}, status=400)
    assert resp.json['err_class'] == 'invalid filters'
    assert resp.json['errors'] == {'payable': ['Must be a valid boolean.']}
    resp = app.get('/api/regie/foo/invoices/', params={'NameID': 'foobar', 'payable': False})
    assert len(resp.json['data']) == 0
    resp = app.get('/api/regie/foo/invoices/', params={'NameID': 'foobar', 'payable': True})
    assert len(resp.json['data']) == 1
    invoice.date_invoicing = datetime.date(2022, 9, 1)
    invoice.save()
    resp = app.get('/api/regie/foo/invoices/', params={'NameID': 'foobar'})
    assert resp.json['data'][0]['created'] == '2022-09-01'
    assert 'real_created' not in resp.json['data'][0]
    invoice.date_invoicing = None
    invoice.save()

    # publication date is in the future
    invoice.date_publication = now().date() + datetime.timedelta(days=1)
    invoice.save()
    resp = app.get('/api/regie/foo/invoices/', params={'NameID': 'foobar'})
    assert resp.json['err'] == 0
    assert resp.json['data'] == []

    # other regie
    other_regie = Regie.objects.create(label='Other Foo')
    invoice.date_publication = now().date()
    invoice.regie = other_regie
    invoice.save()
    resp = app.get('/api/regie/foo/invoices/', params={'NameID': 'foobar'})
    assert resp.json['err'] == 0
    assert resp.json['data'] == []

    # part of amount was already paid
    invoice.regie = regie
    invoice.save()
    payment = Payment.objects.create(
        regie=regie,
        amount=1,
        payment_type=PaymentType.objects.get(regie=regie, slug='cash'),
    )
    invoice_line_payment = InvoiceLinePayment.objects.create(
        payment=payment,
        line=line,
        amount=1,
    )
    resp = app.get('/api/regie/foo/invoices/', params={'NameID': 'foobar'})
    assert resp.json['err'] == 0
    assert resp.json['data'] == [
        {
            'amount': 41,
            'total_amount': 42,
            'remaining_amount': 41,
            'created': now().date().isoformat(),
            'display_id': 'F%02d-%s-0000001'
            % (
                regie.pk,
                invoice.created_at.strftime('%y-%m'),
            ),
            'has_pdf': True,
            'has_dynamic_pdf': True,
            'has_payments_pdf': False,
            'id': str(invoice.uuid),
            'invoice_label': 'My invoice',
            'is_line': False,
            'label': 'My invoice',
            'online_payment': True,
            'paid': False,
            'pay_limit_date': now().date().isoformat(),
            'due_date': invoice.date_due.isoformat(),
            'payment_deadline_date': invoice.date_payment_deadline.isoformat(),
            'disabled': False,
            'api': {
                'payment_url': 'http://testserver/pay/invoice/%s/' % invoice.uuid,
            },
        }
    ]
    resp = app.get('/api/regie/foo/invoices/', params={'NameID': 'foobar', 'payable': False})
    assert len(resp.json['data']) == 0
    resp = app.get('/api/regie/foo/invoices/', params={'NameID': 'foobar', 'payable': True})
    assert len(resp.json['data']) == 1

    # invoice is paid
    invoice_line_payment.amount = 42
    invoice_line_payment.save()
    resp = app.get('/api/regie/foo/invoices/', params={'NameID': 'foobar'})
    assert resp.json['err'] == 0
    assert resp.json['data'] == []

    # no matching payer id
    invoice_line_payment.amount = 1
    invoice_line_payment.save()
    mock_payer.return_value = 'payer:unknown'
    resp = app.get('/api/regie/foo/invoices/', params={'NameID': 'foobar'})
    assert resp.json['err'] == 0
    assert resp.json['data'] == []

    # payer error
    mock_payer.side_effect = PayerError
    app.get('/api/regie/foo/invoices/', params={'NameID': 'foobar'}, status=404)

    # payment deadline is in past
    mock_payer.side_effect = None
    mock_payer.return_value = 'payer:1'
    invoice.date_payment_deadline = now().date() - datetime.timedelta(days=1)
    invoice.save()
    resp = app.get('/api/regie/foo/invoices/', params={'NameID': 'foobar'})
    assert resp.json['data'][0]['disabled'] is True
    resp = app.get('/api/regie/foo/invoices/', params={'NameID': 'foobar', 'payable': False})
    assert len(resp.json['data']) == 1
    resp = app.get('/api/regie/foo/invoices/', params={'NameID': 'foobar', 'payable': True})
    assert len(resp.json['data']) == 0

    # invoice with direct debit
    invoice.date_payment_deadline = now().date() + datetime.timedelta(days=1)
    invoice.payer_direct_debit = True
    invoice.save()
    resp = app.get('/api/regie/foo/invoices/', params={'NameID': 'foobar'})
    assert resp.json['data'][0]['disabled'] is True
    resp = app.get('/api/regie/foo/invoices/', params={'NameID': 'foobar', 'payable': False})
    assert len(resp.json['data']) == 1
    resp = app.get('/api/regie/foo/invoices/', params={'NameID': 'foobar', 'payable': True})
    assert len(resp.json['data']) == 0

    # campaign is not finalized
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
    invoice.pool = pool
    invoice.save()
    resp = app.get('/api/regie/foo/invoices/', params={'NameID': 'foobar'})
    assert resp.json['err'] == 0
    assert resp.json['data'] == []

    # campaign is finalized
    campaign.finalized = True
    campaign.save()
    resp = app.get('/api/regie/foo/invoices/', params={'NameID': 'foobar'})
    assert resp.json['err'] == 0
    assert len(resp.json['data']) == 1

    # invoice is from a basket
    draft_invoice = DraftInvoice.objects.create(
        date_publication=datetime.date(2023, 4, 21),
        date_payment_deadline=datetime.date(2023, 4, 22),
        date_due=datetime.date(2023, 4, 23),
        regie=regie,
    )
    Basket.objects.create(regie=regie, draft_invoice=draft_invoice, invoice=invoice)
    resp = app.get('/api/regie/foo/invoices/', params={'NameID': 'foobar'})
    assert resp.json['err'] == 0
    assert len(resp.json['data']) == 0

    # invoice is cancelled
    Basket.objects.all().delete()
    invoice.cancelled_at = now()
    invoice.save()
    resp = app.get('/api/regie/foo/invoices/', params={'NameID': 'foobar'})
    assert resp.json['err'] == 0
    assert len(resp.json['data']) == 0

    # invoice is collected
    collection = CollectionDocket.objects.create(regie=regie, date_end=now().date(), draft=True)
    invoice.cancelled_at = None
    invoice.collection = collection
    invoice.save()
    resp = app.get('/api/regie/foo/invoices/', params={'NameID': 'foobar'})
    assert resp.json['err'] == 0
    assert len(resp.json['data']) == 0


def test_list_invoices_for_payer(app, user):
    app.get('/api/regie/foo/invoices/', status=403)
    app.authorization = ('Basic', ('john.doe', 'password'))

    app.get('/api/regie/foo/invoices/', status=404)

    regie = Regie.objects.create(label='Foo')
    PaymentBackend.objects.create(label='Test', service='dummy', regie=regie)
    Agenda.objects.create(label='Agenda A', regie=regie)
    PaymentType.create_defaults(regie)
    app.get('/api/regie/foo/invoices/', status=404)

    resp = app.get('/api/regie/foo/invoices/', params={'payer_external_id': 'payer:1'})
    assert resp.json['err'] == 0
    assert resp.json['data'] == []

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
    invoice.refresh_from_db()

    # invoice remaining_amount is 0
    assert invoice.remaining_amount == 0
    resp = app.get('/api/regie/foo/invoices/', params={'payer_external_id': 'payer:1'})
    assert resp.json['err'] == 0
    assert resp.json['data'] == []

    # invoice with something to pay
    line1 = InvoiceLine.objects.create(
        event_date=now().date(),
        label='A line',
        invoice=invoice,
        quantity=1,
        unit_amount=42,
        user_external_id='user:1',
        accounting_code='424242',
    )
    invoice.refresh_from_db()
    resp = app.get('/api/regie/foo/invoices/', params={'payer_external_id': 'payer:1'})
    assert resp.json['err'] == 0
    assert resp.json['data'] == [
        {
            'amount': 42,
            'total_amount': 42,
            'remaining_amount': 42,
            'created': now().date().isoformat(),
            'display_id': 'F%02d-%s-0000001'
            % (
                regie.pk,
                invoice.created_at.strftime('%y-%m'),
            ),
            'has_pdf': True,
            'has_dynamic_pdf': False,
            'has_payments_pdf': False,
            'id': str(invoice.uuid),
            'invoice_label': 'My invoice',
            'is_line': False,
            'label': 'My invoice',
            'online_payment': False,
            'paid': False,
            'pay_limit_date': now().date().isoformat(),
            'due_date': invoice.date_due.isoformat(),
            'payment_deadline_date': invoice.date_payment_deadline.isoformat(),
            'disabled': False,
        }
    ]
    with mock.patch('eopayment.Payment.get_minimal_amount', return_value=100):
        resp = app.get('/api/regie/foo/invoices/', params={'payer_external_id': 'payer:1'})
        assert resp.json['data'][0]['online_payment'] is False
        assert resp.json['data'][0]['no_online_payment_reason'] == 'amount-to-low'
        assert 'api' not in resp.json['data'][0]
    with mock.patch('eopayment.Payment.get_maximal_amount', return_value=0):
        resp = app.get('/api/regie/foo/invoices/', params={'payer_external_id': 'payer:1'})
        assert resp.json['data'][0]['online_payment'] is False
        assert resp.json['data'][0]['no_online_payment_reason'] == 'amount-to-high'
        assert 'api' not in resp.json['data'][0]
    resp = app.get('/api/regie/foo/invoices/', params={'payer_external_id': 'payer:1', 'payable': False})
    assert len(resp.json['data']) == 0
    resp = app.get('/api/regie/foo/invoices/', params={'payer_external_id': 'payer:1', 'payable': True})
    assert len(resp.json['data']) == 1
    invoice.date_invoicing = datetime.date(2022, 9, 1)
    invoice.save()
    resp = app.get('/api/regie/foo/invoices/', params={'payer_external_id': 'payer:1'})
    assert resp.json['data'][0]['created'] == '2022-09-01'
    assert resp.json['data'][0]['real_created'] == now().date().isoformat()
    invoice.date_invoicing = None
    invoice.save()
    resp = app.get('/api/regie/foo/invoices/', params={'payer_external_id': 'payer:1', 'include_lines': True})
    assert resp.json['err'] == 0
    assert resp.json['data'] == [
        {
            'amount': 42,
            'total_amount': 42,
            'remaining_amount': 42,
            'created': now().date().isoformat(),
            'display_id': 'F%02d-%s-0000001'
            % (
                regie.pk,
                invoice.created_at.strftime('%y-%m'),
            ),
            'has_pdf': True,
            'has_dynamic_pdf': False,
            'has_payments_pdf': False,
            'id': str(invoice.uuid),
            'invoice_label': 'My invoice',
            'is_line': False,
            'label': 'F%02d-%s-0000001 - My invoice (amount to pay: 42.00€)'
            % (
                regie.pk,
                invoice.created_at.strftime('%y-%m'),
            ),
            'online_payment': False,
            'paid': False,
            'pay_limit_date': now().date().isoformat(),
            'due_date': invoice.date_due.isoformat(),
            'payment_deadline_date': invoice.date_payment_deadline.isoformat(),
            'disabled': False,
        },
        {
            'accounting_code': '424242',
            'activity_label': '',
            'agenda_slug': '',
            'details': {},
            'disabled': False,
            'event_date': now().strftime('%Y-%m-%d'),
            'id': f'line:{line1.uuid}',
            'invoice_id': str(invoice.uuid),
            'is_line': True,
            'label': '---- A line (amount to pay: 42.00€)',
            'line_description': '',
            'line_label': 'A line',
            'line_raw_description': '',
            'event_slug': '',
            'quantity': 1,
            'remaining_amount': 42,
            'unit_amount': 42,
            'user_external_id': 'user:1',
            'user_first_name': '',
            'user_last_name': '',
        },
    ]
    resp = app.get('/api/regie/foo/invoices/', params={'payer_external_id': 'payer:1', 'verbose_label': True})
    assert resp.json['err'] == 0
    assert resp.json['data'] == [
        {
            'amount': 42,
            'total_amount': 42,
            'remaining_amount': 42,
            'created': now().date().isoformat(),
            'display_id': 'F%02d-%s-0000001'
            % (
                regie.pk,
                invoice.created_at.strftime('%y-%m'),
            ),
            'has_pdf': True,
            'has_dynamic_pdf': False,
            'has_payments_pdf': False,
            'id': str(invoice.uuid),
            'invoice_label': 'My invoice',
            'is_line': False,
            'label': 'F%02d-%s-0000001 - My invoice (amount to pay: 42.00€)'
            % (
                regie.pk,
                invoice.created_at.strftime('%y-%m'),
            ),
            'online_payment': False,
            'paid': False,
            'pay_limit_date': now().date().isoformat(),
            'due_date': invoice.date_due.isoformat(),
            'payment_deadline_date': invoice.date_payment_deadline.isoformat(),
            'disabled': False,
        },
    ]

    # publication date is in the future
    invoice.date_publication = now().date() + datetime.timedelta(days=1)
    invoice.save()
    resp = app.get('/api/regie/foo/invoices/', params={'payer_external_id': 'payer:1'})
    assert resp.json['err'] == 0
    assert resp.json['data'] == []

    # other regie
    other_regie = Regie.objects.create(label='Other Foo')
    invoice.date_publication = now().date()
    invoice.regie = other_regie
    invoice.save()
    resp = app.get('/api/regie/foo/invoices/', params={'payer_external_id': 'payer:1'})
    assert resp.json['err'] == 0
    assert resp.json['data'] == []

    # part of amount was already paid
    invoice.regie = regie
    invoice.save()
    payment = Payment.objects.create(
        regie=regie,
        amount=1,
        payment_type=PaymentType.objects.get(regie=regie, slug='cash'),
    )
    invoice_line_payment = InvoiceLinePayment.objects.create(
        payment=payment,
        line=line1,
        amount=1,
    )
    resp = app.get('/api/regie/foo/invoices/', params={'payer_external_id': 'payer:1'})
    assert resp.json['err'] == 0
    assert resp.json['data'] == [
        {
            'amount': 41,
            'total_amount': 42,
            'remaining_amount': 41,
            'created': now().date().isoformat(),
            'display_id': 'F%02d-%s-0000001'
            % (
                regie.pk,
                invoice.created_at.strftime('%y-%m'),
            ),
            'has_pdf': True,
            'has_dynamic_pdf': True,
            'has_payments_pdf': False,
            'id': str(invoice.uuid),
            'invoice_label': 'My invoice',
            'is_line': False,
            'label': 'My invoice',
            'online_payment': False,
            'paid': False,
            'pay_limit_date': now().date().isoformat(),
            'due_date': invoice.date_due.isoformat(),
            'payment_deadline_date': invoice.date_payment_deadline.isoformat(),
            'disabled': False,
        }
    ]
    resp = app.get('/api/regie/foo/invoices/', params={'payer_external_id': 'payer:1', 'payable': False})
    assert len(resp.json['data']) == 0
    resp = app.get('/api/regie/foo/invoices/', params={'payer_external_id': 'payer:1', 'payable': True})
    assert len(resp.json['data']) == 1
    resp = app.get('/api/regie/foo/invoices/', params={'payer_external_id': 'payer:1', 'include_lines': True})
    assert resp.json['err'] == 0
    assert resp.json['data'] == [
        {
            'amount': 41,
            'total_amount': 42,
            'remaining_amount': 41,
            'created': now().date().isoformat(),
            'display_id': 'F%02d-%s-0000001'
            % (
                regie.pk,
                invoice.created_at.strftime('%y-%m'),
            ),
            'has_pdf': True,
            'has_dynamic_pdf': True,
            'has_payments_pdf': False,
            'id': str(invoice.uuid),
            'invoice_label': 'My invoice',
            'is_line': False,
            'label': 'F%02d-%s-0000001 - My invoice (amount to pay: 41.00€)'
            % (
                regie.pk,
                invoice.created_at.strftime('%y-%m'),
            ),
            'online_payment': False,
            'paid': False,
            'pay_limit_date': now().date().isoformat(),
            'due_date': invoice.date_due.isoformat(),
            'payment_deadline_date': invoice.date_payment_deadline.isoformat(),
            'disabled': False,
        },
        {
            'accounting_code': '424242',
            'activity_label': '',
            'agenda_slug': '',
            'details': {},
            'disabled': False,
            'event_date': now().strftime('%Y-%m-%d'),
            'id': f'line:{line1.uuid}',
            'invoice_id': str(invoice.uuid),
            'is_line': True,
            'label': '---- A line (amount to pay: 41.00€)',
            'line_description': '',
            'line_label': 'A line',
            'line_raw_description': '',
            'event_slug': '',
            'quantity': 1,
            'remaining_amount': 41,
            'unit_amount': 42,
            'user_external_id': 'user:1',
            'user_first_name': '',
            'user_last_name': '',
        },
    ]

    # invoice is paid
    invoice_line_payment.amount = 42
    invoice_line_payment.save()
    resp = app.get('/api/regie/foo/invoices/', params={'payer_external_id': 'payer:1'})
    assert resp.json['err'] == 0
    assert resp.json['data'] == []

    # another lines with something to pay
    line2 = InvoiceLine.objects.create(
        event_date=now().date(),
        label='Another line',
        invoice=invoice,
        quantity=1,
        unit_amount=5,
        details={
            'agenda': 'agenda-a',
            'primary_event': 'event-a',
            'status': 'presence',
            'check_type': 'foo',
            'check_type_group': 'foobar',
            'check_type_label': 'Foo!',
        },
        event_slug='agenda-a@event-a',
        agenda_slug='agenda-a',
        activity_label='Agenda A',
        user_external_id='user:1',
        user_first_name='First1',
        user_last_name='Last1',
        accounting_code='424242',
    )
    line3 = InvoiceLine.objects.create(
        event_date=now().date(),
        label='Another line again',
        invoice=invoice,
        quantity=1,
        unit_amount=5,
        details={
            'status': 'absence',
        },
        user_external_id='user:2',
        user_first_name='First2',
        user_last_name='Last2',
        accounting_code='424243',
    )
    resp = app.get('/api/regie/foo/invoices/', params={'payer_external_id': 'payer:1', 'include_lines': True})
    assert resp.json['err'] == 0
    assert resp.json['data'] == [
        {
            'amount': 10,
            'total_amount': 52,
            'remaining_amount': 10,
            'created': now().date().isoformat(),
            'display_id': 'F%02d-%s-0000001'
            % (
                regie.pk,
                invoice.created_at.strftime('%y-%m'),
            ),
            'has_pdf': True,
            'has_dynamic_pdf': True,
            'has_payments_pdf': False,
            'id': str(invoice.uuid),
            'invoice_label': 'My invoice',
            'is_line': False,
            'label': 'F%02d-%s-0000001 - My invoice (amount to pay: 10.00€)'
            % (
                regie.pk,
                invoice.created_at.strftime('%y-%m'),
            ),
            'online_payment': False,
            'paid': False,
            'pay_limit_date': now().date().isoformat(),
            'due_date': invoice.date_due.isoformat(),
            'payment_deadline_date': invoice.date_payment_deadline.isoformat(),
            'disabled': False,
        },
        {
            'accounting_code': '424242',
            'activity_label': 'Agenda A',
            'agenda_slug': 'agenda-a',
            'details': {
                'agenda': 'agenda-a',
                'check_type': 'foo',
                'check_type_group': 'foobar',
                'check_type_label': 'Foo!',
                'primary_event': 'event-a',
                'status': 'presence',
            },
            'disabled': False,
            'event_date': now().strftime('%Y-%m-%d'),
            'id': f'line:{line2.uuid}',
            'invoice_id': str(invoice.uuid),
            'is_line': True,
            'label': '---- Agenda A - Another line - Foo! (amount to pay: 5.00€)',
            'line_description': '',
            'line_label': 'Another line',
            'line_raw_description': '',
            'event_slug': 'agenda-a@event-a',
            'quantity': 1,
            'remaining_amount': 5,
            'unit_amount': 5,
            'user_external_id': 'user:1',
            'user_first_name': 'First1',
            'user_last_name': 'Last1',
        },
        {
            'accounting_code': '424242',
            'activity_label': '',
            'agenda_slug': '',
            'details': {},
            'disabled': True,
            'event_date': now().strftime('%Y-%m-%d'),
            'id': f'line:{line1.uuid}',
            'invoice_id': str(invoice.uuid),
            'is_line': True,
            'label': '---- A line (amount to pay: 0.00€)',
            'line_description': '',
            'line_label': 'A line',
            'line_raw_description': '',
            'event_slug': '',
            'quantity': 1,
            'remaining_amount': 0,
            'unit_amount': 42,
            'user_external_id': 'user:1',
            'user_first_name': '',
            'user_last_name': '',
        },
        {
            'accounting_code': '424243',
            'activity_label': '',
            'agenda_slug': '',
            'details': {
                'status': 'absence',
            },
            'disabled': False,
            'event_date': now().strftime('%Y-%m-%d'),
            'id': f'line:{line3.uuid}',
            'invoice_id': str(invoice.uuid),
            'is_line': True,
            'label': '---- Another line again - Absence (amount to pay: 5.00€)',
            'line_description': '',
            'line_label': 'Another line again',
            'line_raw_description': '',
            'event_slug': '',
            'quantity': 1,
            'remaining_amount': 5,
            'unit_amount': 5,
            'user_external_id': 'user:2',
            'user_first_name': 'First2',
            'user_last_name': 'Last2',
        },
    ]

    # due date is in past
    invoice.date_due = now().date() - datetime.timedelta(days=1)
    invoice.save()
    resp = app.get('/api/regie/foo/invoices/', params={'payer_external_id': 'payer:1'})
    assert resp.json['data'][0]['disabled'] is True
    resp = app.get('/api/regie/foo/invoices/', params={'payer_external_id': 'payer:1', 'payable': False})
    assert len(resp.json['data']) == 1
    resp = app.get('/api/regie/foo/invoices/', params={'payer_external_id': 'payer:1', 'payable': True})
    assert len(resp.json['data']) == 0
    resp = app.get('/api/regie/foo/invoices/', params={'payer_external_id': 'payer:1', 'include_lines': True})
    assert resp.json['data'][0]['disabled'] is True

    # invoice with direct debit
    invoice.date_due = now().date() + datetime.timedelta(days=1)
    invoice.payer_direct_debit = True
    invoice.save()
    resp = app.get('/api/regie/foo/invoices/', params={'payer_external_id': 'payer:1'})
    assert resp.json['data'][0]['disabled'] is True
    resp = app.get('/api/regie/foo/invoices/', params={'payer_external_id': 'payer:1', 'payable': False})
    assert len(resp.json['data']) == 1
    resp = app.get('/api/regie/foo/invoices/', params={'payer_external_id': 'payer:1', 'payable': True})
    assert len(resp.json['data']) == 0

    # campaign is not finalized
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
    invoice.pool = pool
    invoice.save()
    resp = app.get('/api/regie/foo/invoices/', params={'payer_external_id': 'payer:1'})
    assert resp.json['err'] == 0
    assert resp.json['data'] == []

    # campaign is finalized
    campaign.finalized = True
    campaign.save()
    resp = app.get('/api/regie/foo/invoices/', params={'payer_external_id': 'payer:1'})
    assert resp.json['err'] == 0
    assert len(resp.json['data']) == 1

    # invoice is from a basket
    draft_invoice = DraftInvoice.objects.create(
        date_publication=datetime.date(2023, 4, 21),
        date_payment_deadline=datetime.date(2023, 4, 22),
        date_due=datetime.date(2023, 4, 23),
        regie=regie,
    )
    Basket.objects.create(regie=regie, draft_invoice=draft_invoice, invoice=invoice)
    resp = app.get('/api/regie/foo/invoices/', params={'payer_external_id': 'payer:1'})
    assert resp.json['err'] == 0
    assert len(resp.json['data']) == 0

    # invoice is cancelled
    Basket.objects.all().delete()
    invoice.cancelled_at = now()
    invoice.save()
    resp = app.get('/api/regie/foo/invoices/', params={'payer_external_id': 'payer:1'})
    assert resp.json['err'] == 0
    assert len(resp.json['data']) == 0

    # invoice is collected
    collection = CollectionDocket.objects.create(regie=regie, date_end=now().date(), draft=True)
    invoice.cancelled_at = None
    invoice.collection = collection
    invoice.save()
    resp = app.get('/api/regie/foo/invoices/', params={'payer_external_id': 'payer:1'})
    assert resp.json['err'] == 0
    assert len(resp.json['data']) == 0


@mock.patch.object(Regie, 'get_payer_external_id_from_nameid', autospec=True)
def test_list_history_invoices(mock_payer, app, user):
    app.get('/api/regie/foo/invoices/history/', status=403)
    app.authorization = ('Basic', ('john.doe', 'password'))

    app.get('/api/regie/foo/invoices/history/', status=404)

    regie = Regie.objects.create(label='Foo')
    PaymentType.create_defaults(regie)
    app.get('/api/regie/foo/invoices/history/', status=404)

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
    line1 = InvoiceLine.objects.create(
        event_date=now().date(),
        label='A line',
        invoice=invoice,
        quantity=1,
        unit_amount=42,
        user_external_id='user:1',
        accounting_code='424242',
    )
    invoice.refresh_from_db()
    payment = Payment.objects.create(
        regie=regie,
        amount=42,
        payment_type=PaymentType.objects.get(regie=regie, slug='cash'),
    )
    invoice_line_payment = InvoiceLinePayment.objects.create(
        payment=payment,
        line=line1,
        amount=2,
    )
    invoice.refresh_from_db()

    # invoice remaining_amount is not 0
    assert invoice.remaining_amount != 0
    mock_payer.return_value = 'payer:1'
    resp = app.get('/api/regie/foo/invoices/history/', params={'NameID': 'foobar'})
    assert resp.json['err'] == 0
    assert resp.json['data'] == []
    assert mock_payer.call_args_list == [mock.call(regie, mock.ANY, 'foobar')]

    # invoice with nothing to pay
    invoice_line_payment.amount = 42
    invoice_line_payment.save()
    invoice.refresh_from_db()
    resp = app.get('/api/regie/foo/invoices/history/', params={'NameID': 'foobar'})
    assert resp.json['err'] == 0
    assert resp.json['data'] == [
        {
            'amount': 0,
            'total_amount': 42,
            'remaining_amount': 0,
            'created': now().date().isoformat(),
            'display_id': 'F%02d-%s-0000001'
            % (
                regie.pk,
                invoice.created_at.strftime('%y-%m'),
            ),
            'has_pdf': True,
            'has_dynamic_pdf': True,
            'has_payments_pdf': True,
            'id': str(invoice.uuid),
            'invoice_label': 'My invoice',
            'is_line': False,
            'label': 'My invoice',
            'online_payment': False,
            'paid': True,
            'pay_limit_date': '',
            'due_date': invoice.date_due.isoformat(),
            'payment_deadline_date': invoice.date_payment_deadline.isoformat(),
            'disabled': True,
        }
    ]

    # publication date is in the future
    invoice.date_publication = now().date() + datetime.timedelta(days=1)
    invoice.save()
    resp = app.get('/api/regie/foo/invoices/history/', params={'NameID': 'foobar'})
    assert resp.json['err'] == 0
    assert resp.json['data'] == []

    # other regie
    other_regie = Regie.objects.create(label='Other Foo')
    invoice.date_publication = now().date()
    invoice.regie = other_regie
    invoice.save()
    resp = app.get('/api/regie/foo/invoices/history/', params={'NameID': 'foobar'})
    assert resp.json['err'] == 0
    assert resp.json['data'] == []

    # no matching payer id
    invoice.regie = regie
    invoice.save()
    mock_payer.return_value = 'payer:unknown'
    resp = app.get('/api/regie/foo/invoices/history/', params={'NameID': 'foobar'})
    assert resp.json['err'] == 0
    assert resp.json['data'] == []

    # payer error
    mock_payer.side_effect = PayerError
    app.get('/api/regie/foo/invoices/history/', params={'NameID': 'foobar'}, status=404)

    # campaign is not finalized
    mock_payer.side_effect = None
    mock_payer.return_value = 'payer:1'
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
    invoice.pool = pool
    invoice.save()
    resp = app.get('/api/regie/foo/invoices/history/', params={'NameID': 'foobar'})
    assert resp.json['err'] == 0
    assert resp.json['data'] == []

    # campaign is finalized
    campaign.finalized = True
    campaign.save()
    resp = app.get('/api/regie/foo/invoices/history/', params={'NameID': 'foobar'})
    assert resp.json['err'] == 0
    assert len(resp.json['data']) == 1

    # invoice is from a basket
    draft_invoice = DraftInvoice.objects.create(
        date_publication=datetime.date(2023, 4, 21),
        date_payment_deadline=datetime.date(2023, 4, 22),
        date_due=datetime.date(2023, 4, 23),
        regie=regie,
    )
    Basket.objects.create(regie=regie, draft_invoice=draft_invoice, invoice=invoice)
    resp = app.get('/api/regie/foo/invoices/history/', params={'NameID': 'foobar'})
    assert resp.json['err'] == 0
    assert len(resp.json['data']) == 1

    # invoice is cancelled
    Basket.objects.all().delete()
    invoice.cancelled_at = now()
    invoice.save()
    resp = app.get('/api/regie/foo/invoices/history/', params={'NameID': 'foobar'})
    assert resp.json['err'] == 0
    assert len(resp.json['data']) == 0

    # invoice is collected
    collection = CollectionDocket.objects.create(regie=regie, date_end=now().date(), draft=True)
    invoice.cancelled_at = None
    invoice.collection = collection
    invoice.save()
    resp = app.get('/api/regie/foo/invoices/history/', params={'NameID': 'foobar'})
    assert resp.json['err'] == 0
    assert len(resp.json['data']) == 0


def test_list_history_invoices_for_payer(app, user):
    app.get('/api/regie/foo/invoices/history/', status=403)
    app.authorization = ('Basic', ('john.doe', 'password'))

    app.get('/api/regie/foo/invoices/history/', status=404)

    regie = Regie.objects.create(label='Foo')
    PaymentType.create_defaults(regie)
    app.get('/api/regie/foo/invoices/history/', status=404)

    resp = app.get('/api/regie/foo/invoices/history/', params={'payer_external_id': 'payer:1'})
    assert resp.json['err'] == 0
    assert resp.json['data'] == []

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
    payment = Payment.objects.create(
        regie=regie,
        amount=42,
        payment_type=PaymentType.objects.get(regie=regie, slug='cash'),
    )
    invoice_line_payment = InvoiceLinePayment.objects.create(
        payment=payment,
        line=line,
        amount=2,
    )
    invoice.refresh_from_db()

    # invoice remaining_amount is not 0
    assert invoice.remaining_amount != 0
    resp = app.get('/api/regie/foo/invoices/history/', params={'payer_external_id': 'payer:1'})
    assert resp.json['err'] == 0
    assert resp.json['data'] == []

    # invoice with nothing to pay
    invoice_line_payment.amount = 42
    invoice_line_payment.save()
    invoice.refresh_from_db()
    resp = app.get('/api/regie/foo/invoices/history/', params={'payer_external_id': 'payer:1'})
    assert resp.json['err'] == 0
    assert resp.json['data'] == [
        {
            'amount': 0,
            'total_amount': 42,
            'remaining_amount': 0,
            'created': now().date().isoformat(),
            'display_id': 'F%02d-%s-0000001'
            % (
                regie.pk,
                invoice.created_at.strftime('%y-%m'),
            ),
            'has_pdf': True,
            'has_dynamic_pdf': True,
            'has_payments_pdf': True,
            'id': str(invoice.uuid),
            'invoice_label': 'My invoice',
            'is_line': False,
            'label': 'My invoice',
            'online_payment': False,
            'paid': True,
            'pay_limit_date': '',
            'due_date': invoice.date_due.isoformat(),
            'payment_deadline_date': invoice.date_payment_deadline.isoformat(),
            'disabled': True,
        }
    ]

    # publication date is in the future
    invoice.date_publication = now().date() + datetime.timedelta(days=1)
    invoice.save()
    resp = app.get('/api/regie/foo/invoices/history/', params={'payer_external_id': 'payer:1'})
    assert resp.json['err'] == 0
    assert resp.json['data'] == []

    # other regie
    other_regie = Regie.objects.create(label='Other Foo')
    invoice.date_publication = now().date()
    invoice.regie = other_regie
    invoice.save()
    resp = app.get('/api/regie/foo/invoices/history/', params={'payer_external_id': 'payer:1'})
    assert resp.json['err'] == 0
    assert resp.json['data'] == []

    # campaign is not finalized
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
    invoice.regie = regie
    invoice.pool = pool
    invoice.save()
    resp = app.get('/api/regie/foo/invoices/history/', params={'payer_external_id': 'payer:1'})
    assert resp.json['err'] == 0
    assert resp.json['data'] == []

    # campaign is finalized
    campaign.finalized = True
    campaign.save()
    resp = app.get('/api/regie/foo/invoices/history/', params={'payer_external_id': 'payer:1'})
    assert resp.json['err'] == 0
    assert len(resp.json['data']) == 1

    # invoice is from a basket
    draft_invoice = DraftInvoice.objects.create(
        date_publication=datetime.date(2023, 4, 21),
        date_payment_deadline=datetime.date(2023, 4, 22),
        date_due=datetime.date(2023, 4, 23),
        regie=regie,
    )
    Basket.objects.create(regie=regie, draft_invoice=draft_invoice, invoice=invoice)
    resp = app.get('/api/regie/foo/invoices/history/', params={'payer_external_id': 'payer:1'})
    assert resp.json['err'] == 0
    assert len(resp.json['data']) == 1

    # invoice is cancelled
    Basket.objects.all().delete()
    invoice.cancelled_at = now()
    invoice.save()
    resp = app.get('/api/regie/foo/invoices/history/', params={'payer_external_id': 'payer:1'})
    assert resp.json['err'] == 0
    assert len(resp.json['data']) == 0

    # invoice is collected
    collection = CollectionDocket.objects.create(regie=regie, date_end=now().date(), draft=True)
    invoice.cancelled_at = None
    invoice.collection = collection
    invoice.save()
    resp = app.get('/api/regie/foo/invoices/history/', params={'payer_external_id': 'payer:1'})
    assert resp.json['err'] == 0
    assert len(resp.json['data']) == 0


@mock.patch.object(Regie, 'get_payer_external_id_from_nameid', autospec=True)
def test_list_collected_invoices(mock_payer, app, user):
    app.get('/api/regie/foo/invoices/history/', status=403)
    app.authorization = ('Basic', ('john.doe', 'password'))

    app.get('/api/regie/foo/invoices/history/', status=404)

    regie = Regie.objects.create(label='Foo')
    PaymentType.create_defaults(regie)
    app.get('/api/regie/foo/invoices/history/', status=404)

    collection = CollectionDocket.objects.create(regie=regie, date_end=now().date(), draft=False)
    invoice = Invoice.objects.create(
        label='My invoice',
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=now().date(),
        date_due=datetime.date(2022, 10, 31),
        regie=regie,
        payer_external_id='payer:1',
        collection=collection,
    )
    invoice.set_number()
    invoice.save()
    line1 = InvoiceLine.objects.create(
        event_date=now().date(),
        label='A line',
        invoice=invoice,
        quantity=1,
        unit_amount=42,
        user_external_id='user:1',
        accounting_code='424242',
    )
    invoice.refresh_from_db()
    payment = Payment.objects.create(
        regie=regie,
        amount=42,
        payment_type=PaymentType.objects.get(regie=regie, slug='cash'),
    )
    InvoiceLinePayment.objects.create(
        payment=payment,
        line=line1,
        amount=2,
    )
    invoice.refresh_from_db()

    # invoice was not paid by collection
    mock_payer.return_value = 'payer:1'
    resp = app.get('/api/regie/foo/invoices/collected/', params={'NameID': 'foobar'})
    assert resp.json['err'] == 0
    assert resp.json['data'] == [
        {
            'amount': 0,
            'total_amount': 42,
            'remaining_amount': 0,
            'collected_amount': 40,
            'created': now().date().isoformat(),
            'display_id': 'F%02d-%s-0000001'
            % (
                regie.pk,
                invoice.created_at.strftime('%y-%m'),
            ),
            'has_pdf': True,
            'has_dynamic_pdf': False,
            'has_payments_pdf': False,
            'id': str(invoice.uuid),
            'invoice_label': 'My invoice',
            'is_line': False,
            'label': 'My invoice',
            'online_payment': False,
            'paid': False,
            'pay_limit_date': '',
            'due_date': invoice.date_due.isoformat(),
            'payment_deadline_date': invoice.date_payment_deadline.isoformat(),
            'collection_date': now().date().isoformat(),
            'disabled': False,
        }
    ]

    # invoice was paid by collection
    payment_type, dummy = PaymentType.objects.get_or_create(regie=regie, slug='collect')
    Payment.make_payment(
        regie=regie,
        invoices=[invoice],
        amount=40,
        payment_type=payment_type,
    )
    invoice.refresh_from_db()
    resp = app.get('/api/regie/foo/invoices/collected/', params={'NameID': 'foobar'})
    assert resp.json['err'] == 0
    assert resp.json['data'] == [
        {
            'amount': 0,
            'total_amount': 42,
            'remaining_amount': 0,
            'collected_amount': 40,
            'created': now().date().isoformat(),
            'display_id': 'F%02d-%s-0000001'
            % (
                regie.pk,
                invoice.created_at.strftime('%y-%m'),
            ),
            'has_pdf': True,
            'has_dynamic_pdf': False,
            'has_payments_pdf': False,
            'id': str(invoice.uuid),
            'invoice_label': 'My invoice',
            'is_line': False,
            'label': 'My invoice',
            'online_payment': False,
            'paid': False,
            'pay_limit_date': '',
            'due_date': invoice.date_due.isoformat(),
            'payment_deadline_date': invoice.date_payment_deadline.isoformat(),
            'collection_date': now().date().isoformat(),
            'disabled': True,
        }
    ]

    # publication date is in the future
    invoice.date_publication = now().date() + datetime.timedelta(days=1)
    invoice.save()
    resp = app.get('/api/regie/foo/invoices/collected/', params={'NameID': 'foobar'})
    assert resp.json['err'] == 0
    assert resp.json['data'] == []

    # other regie
    other_regie = Regie.objects.create(label='Other Foo')
    invoice.date_publication = now().date()
    invoice.regie = other_regie
    invoice.save()
    resp = app.get('/api/regie/foo/invoices/collected/', params={'NameID': 'foobar'})
    assert resp.json['err'] == 0
    assert resp.json['data'] == []

    # no matching payer id
    invoice.regie = regie
    invoice.save()
    mock_payer.return_value = 'payer:unknown'
    resp = app.get('/api/regie/foo/invoices/collected/', params={'NameID': 'foobar'})
    assert resp.json['err'] == 0
    assert resp.json['data'] == []

    # payer error
    mock_payer.side_effect = PayerError
    app.get('/api/regie/foo/invoices/collected/', params={'NameID': 'foobar'}, status=404)

    # campaign is not finalized
    mock_payer.side_effect = None
    mock_payer.return_value = 'payer:1'
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
    invoice.pool = pool
    invoice.save()
    resp = app.get('/api/regie/foo/invoices/collected/', params={'NameID': 'foobar'})
    assert resp.json['err'] == 0
    assert resp.json['data'] == []

    # campaign is finalized
    campaign.finalized = True
    campaign.save()
    resp = app.get('/api/regie/foo/invoices/collected/', params={'NameID': 'foobar'})
    assert resp.json['err'] == 0
    assert len(resp.json['data']) == 1

    # invoice is cancelled
    Basket.objects.all().delete()
    invoice.cancelled_at = now()
    invoice.save()
    resp = app.get('/api/regie/foo/invoices/collected/', params={'NameID': 'foobar'})
    assert resp.json['err'] == 0
    assert len(resp.json['data']) == 0

    # draft collection
    collection.draft = True
    collection.save()
    resp = app.get('/api/regie/foo/invoices/collected/', params={'NameID': 'foobar'})
    assert resp.json['err'] == 0
    assert len(resp.json['data']) == 0

    # invoice is not collected
    invoice.cancelled_at = None
    invoice.collection = None
    invoice.save()
    resp = app.get('/api/regie/foo/invoices/collected/', params={'NameID': 'foobar'})
    assert resp.json['err'] == 0
    assert len(resp.json['data']) == 0


def test_list_collected_invoices_for_payer(app, user):
    app.get('/api/regie/foo/invoices/history/', status=403)
    app.authorization = ('Basic', ('john.doe', 'password'))

    app.get('/api/regie/foo/invoices/history/', status=404)

    regie = Regie.objects.create(label='Foo')
    PaymentType.create_defaults(regie)
    app.get('/api/regie/foo/invoices/history/', status=404)

    collection = CollectionDocket.objects.create(regie=regie, date_end=now().date(), draft=False)
    invoice = Invoice.objects.create(
        label='My invoice',
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=now().date(),
        date_due=datetime.date(2022, 10, 31),
        regie=regie,
        payer_external_id='payer:1',
        collection=collection,
    )
    invoice.set_number()
    invoice.save()
    line1 = InvoiceLine.objects.create(
        event_date=now().date(),
        label='A line',
        invoice=invoice,
        quantity=1,
        unit_amount=42,
        user_external_id='user:1',
        accounting_code='424242',
    )
    invoice.refresh_from_db()
    payment = Payment.objects.create(
        regie=regie,
        amount=42,
        payment_type=PaymentType.objects.get(regie=regie, slug='cash'),
    )
    InvoiceLinePayment.objects.create(
        payment=payment,
        line=line1,
        amount=2,
    )
    invoice.refresh_from_db()

    # invoice was not paid by collection
    resp = app.get('/api/regie/foo/invoices/collected/', params={'payer_external_id': 'payer:1'})
    assert resp.json['err'] == 0
    assert resp.json['data'] == [
        {
            'amount': 0,
            'total_amount': 42,
            'remaining_amount': 0,
            'collected_amount': 40,
            'created': now().date().isoformat(),
            'display_id': 'F%02d-%s-0000001'
            % (
                regie.pk,
                invoice.created_at.strftime('%y-%m'),
            ),
            'has_pdf': True,
            'has_dynamic_pdf': False,
            'has_payments_pdf': False,
            'id': str(invoice.uuid),
            'invoice_label': 'My invoice',
            'is_line': False,
            'label': 'My invoice',
            'online_payment': False,
            'paid': False,
            'pay_limit_date': '',
            'due_date': invoice.date_due.isoformat(),
            'payment_deadline_date': invoice.date_payment_deadline.isoformat(),
            'collection_date': now().date().isoformat(),
            'disabled': True,
        }
    ]

    # invoice was paid by collection
    payment_type, dummy = PaymentType.objects.get_or_create(regie=regie, slug='collect')
    Payment.make_payment(
        regie=regie,
        invoices=[invoice],
        amount=40,
        payment_type=payment_type,
    )
    invoice.refresh_from_db()
    resp = app.get('/api/regie/foo/invoices/collected/', params={'payer_external_id': 'payer:1'})
    assert resp.json['err'] == 0
    assert resp.json['data'] == [
        {
            'amount': 0,
            'total_amount': 42,
            'remaining_amount': 0,
            'collected_amount': 40,
            'created': now().date().isoformat(),
            'display_id': 'F%02d-%s-0000001'
            % (
                regie.pk,
                invoice.created_at.strftime('%y-%m'),
            ),
            'has_pdf': True,
            'has_dynamic_pdf': False,
            'has_payments_pdf': False,
            'id': str(invoice.uuid),
            'invoice_label': 'My invoice',
            'is_line': False,
            'label': 'My invoice',
            'online_payment': False,
            'paid': False,
            'pay_limit_date': '',
            'due_date': invoice.date_due.isoformat(),
            'payment_deadline_date': invoice.date_payment_deadline.isoformat(),
            'collection_date': now().date().isoformat(),
            'disabled': True,
        }
    ]

    # publication date is in the future
    invoice.date_publication = now().date() + datetime.timedelta(days=1)
    invoice.save()
    resp = app.get('/api/regie/foo/invoices/collected/', params={'payer_external_id': 'payer:1'})
    assert resp.json['err'] == 0
    assert resp.json['data'] == []

    # other regie
    other_regie = Regie.objects.create(label='Other Foo')
    invoice.date_publication = now().date()
    invoice.regie = other_regie
    invoice.save()
    resp = app.get('/api/regie/foo/invoices/collected/', params={'payer_external_id': 'payer:1'})
    assert resp.json['err'] == 0
    assert resp.json['data'] == []

    # campaign is not finalized
    invoice.regie = regie
    invoice.save()
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
    invoice.pool = pool
    invoice.save()
    resp = app.get('/api/regie/foo/invoices/collected/', params={'payer_external_id': 'payer:1'})
    assert resp.json['err'] == 0
    assert resp.json['data'] == []

    # campaign is finalized
    campaign.finalized = True
    campaign.save()
    resp = app.get('/api/regie/foo/invoices/collected/', params={'payer_external_id': 'payer:1'})
    assert resp.json['err'] == 0
    assert len(resp.json['data']) == 1

    # invoice is cancelled
    Basket.objects.all().delete()
    invoice.cancelled_at = now()
    invoice.save()
    resp = app.get('/api/regie/foo/invoices/collected/', params={'payer_external_id': 'payer:1'})
    assert resp.json['err'] == 0
    assert len(resp.json['data']) == 0

    # draft collection
    collection.draft = True
    collection.save()
    resp = app.get('/api/regie/foo/invoices/collected/', params={'payer_external_id': 'payer:1'})
    assert resp.json['err'] == 0
    assert len(resp.json['data']) == 0

    # invoice is not collected
    invoice.cancelled_at = None
    invoice.collection = None
    invoice.save()
    resp = app.get('/api/regie/foo/invoices/collected/', params={'payer_external_id': 'payer:1'})
    assert resp.json['err'] == 0
    assert len(resp.json['data']) == 0


def test_list_cancelled_invoices_for_payer(app, user):
    app.get('/api/regie/foo/invoices/cancelled/', status=403)
    app.authorization = ('Basic', ('john.doe', 'password'))

    app.get('/api/regie/foo/invoices/cancelled/', status=404)

    regie = Regie.objects.create(label='Foo')
    app.get('/api/regie/foo/invoices/cancelled/', status=404)

    resp = app.get('/api/regie/foo/invoices/cancelled/', params={'payer_external_id': 'payer:1'})
    assert resp.json['err'] == 0
    assert resp.json['data'] == []

    invoice = Invoice.objects.create(
        label='My invoice',
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        regie=regie,
        payer_external_id='payer:1',
        cancelled_at=now(),
    )
    invoice.set_number()
    invoice.save()
    line1 = InvoiceLine.objects.create(
        event_date=now().date(),
        invoice=invoice,
        quantity=1,
        unit_amount=42,
        accounting_code='424242',
    )
    line2 = InvoiceLine.objects.create(
        event_date=now().date(),
        label='Another line',
        invoice=invoice,
        quantity=1,
        unit_amount=5,
        details={
            'agenda': 'agenda-a',
            'primary_event': 'event-a',
            'status': 'presence',
            'check_type': 'foo',
            'check_type_group': 'foobar',
            'check_type_label': 'Foo!',
        },
        event_slug='agenda-a@event-a',
        agenda_slug='agenda-a',
        activity_label='Agenda A',
        user_external_id='user:1',
        user_first_name='First1',
        user_last_name='Last1',
        accounting_code='424242',
    )
    line3 = InvoiceLine.objects.create(
        event_date=now().date(),
        label='Another line again',
        invoice=invoice,
        quantity=1,
        unit_amount=5,
        details={
            'status': 'absence',
        },
        user_external_id='user:2',
        user_first_name='First2',
        user_last_name='Last2',
        accounting_code='424243',
    )
    invoice.refresh_from_db()

    resp = app.get('/api/regie/foo/invoices/cancelled/', params={'payer_external_id': 'payer:1'})
    assert resp.json['err'] == 0
    assert resp.json['data'] == [
        {
            'amount': 52,
            'total_amount': 52,
            'remaining_amount': 52,
            'created': now().date().isoformat(),
            'display_id': 'F%02d-%s-0000001'
            % (
                regie.pk,
                invoice.created_at.strftime('%y-%m'),
            ),
            'has_pdf': True,
            'has_dynamic_pdf': False,
            'has_payments_pdf': False,
            'id': str(invoice.uuid),
            'invoice_label': 'My invoice',
            'is_line': False,
            'label': 'My invoice',
            'online_payment': False,
            'no_online_payment_reason': 'past-due-date',
            'paid': False,
            'pay_limit_date': invoice.date_due.isoformat(),
            'due_date': invoice.date_due.isoformat(),
            'payment_deadline_date': invoice.date_payment_deadline.isoformat(),
            'disabled': False,
        }
    ]
    resp = app.get(
        '/api/regie/foo/invoices/cancelled/', params={'payer_external_id': 'payer:1', 'include_lines': True}
    )
    assert resp.json['err'] == 0
    assert resp.json['data'] == [
        {
            'amount': 52,
            'total_amount': 52,
            'remaining_amount': 52,
            'created': now().date().isoformat(),
            'display_id': 'F%02d-%s-0000001'
            % (
                regie.pk,
                invoice.created_at.strftime('%y-%m'),
            ),
            'has_pdf': True,
            'has_dynamic_pdf': False,
            'has_payments_pdf': False,
            'id': str(invoice.uuid),
            'invoice_label': 'My invoice',
            'is_line': False,
            'label': 'F%02d-%s-0000001 - My invoice (amount to pay: 52.00€)'
            % (regie.pk, invoice.created_at.strftime('%y-%m')),
            'online_payment': False,
            'no_online_payment_reason': 'past-due-date',
            'paid': False,
            'pay_limit_date': invoice.date_due.isoformat(),
            'due_date': invoice.date_due.isoformat(),
            'payment_deadline_date': invoice.date_payment_deadline.isoformat(),
            'disabled': False,
        },
        {
            'accounting_code': '424242',
            'activity_label': '',
            'agenda_slug': '',
            'details': {},
            'disabled': False,
            'event_date': now().strftime('%Y-%m-%d'),
            'id': f'line:{line1.uuid}',
            'invoice_id': str(invoice.uuid),
            'is_line': True,
            'label': '----  (amount to pay: 42.00€)',
            'line_description': '',
            'line_label': '',
            'line_raw_description': '',
            'event_slug': '',
            'quantity': 1,
            'remaining_amount': 42.0,
            'unit_amount': 42,
            'user_external_id': '',
            'user_first_name': '',
            'user_last_name': '',
        },
        {
            'accounting_code': '424242',
            'activity_label': 'Agenda A',
            'agenda_slug': 'agenda-a',
            'details': {
                'agenda': 'agenda-a',
                'check_type': 'foo',
                'check_type_group': 'foobar',
                'check_type_label': 'Foo!',
                'primary_event': 'event-a',
                'status': 'presence',
            },
            'disabled': False,
            'event_date': now().strftime('%Y-%m-%d'),
            'id': f'line:{line2.uuid}',
            'invoice_id': str(invoice.uuid),
            'is_line': True,
            'label': '---- Agenda A - Another line - Foo! (amount to pay: 5.00€)',
            'line_description': '',
            'line_label': 'Another line',
            'line_raw_description': '',
            'event_slug': 'agenda-a@event-a',
            'quantity': 1.0,
            'remaining_amount': 5.0,
            'unit_amount': 5.0,
            'user_external_id': 'user:1',
            'user_first_name': 'First1',
            'user_last_name': 'Last1',
        },
        {
            'accounting_code': '424243',
            'activity_label': '',
            'agenda_slug': '',
            'details': {
                'status': 'absence',
            },
            'disabled': False,
            'event_date': now().strftime('%Y-%m-%d'),
            'id': f'line:{line3.uuid}',
            'invoice_id': str(invoice.uuid),
            'is_line': True,
            'label': '---- Another line again - Absence (amount to pay: 5.00€)',
            'line_description': '',
            'line_label': 'Another line again',
            'line_raw_description': '',
            'event_slug': '',
            'quantity': 1.0,
            'remaining_amount': 5.0,
            'unit_amount': 5.0,
            'user_external_id': 'user:2',
            'user_first_name': 'First2',
            'user_last_name': 'Last2',
        },
    ]

    # other regie
    other_regie = Regie.objects.create(label='Other Foo')
    invoice.regie = other_regie
    invoice.save()
    resp = app.get('/api/regie/foo/invoices/cancelled/', params={'payer_external_id': 'payer:1'})
    assert resp.json['err'] == 0
    assert resp.json['data'] == []

    # campaign is not finalized
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
    invoice.regie = regie
    invoice.pool = pool
    invoice.save()
    resp = app.get('/api/regie/foo/invoices/cancelled/', params={'payer_external_id': 'payer:1'})
    assert resp.json['err'] == 0
    assert resp.json['data'] == []

    # campaign is finalized
    campaign.finalized = True
    campaign.save()
    resp = app.get('/api/regie/foo/invoices/cancelled/', params={'payer_external_id': 'payer:1'})
    assert resp.json['err'] == 0
    assert len(resp.json['data']) == 1

    # invoice is from a basket
    draft_invoice = DraftInvoice.objects.create(
        date_publication=datetime.date(2023, 4, 21),
        date_payment_deadline=datetime.date(2023, 4, 22),
        date_due=datetime.date(2023, 4, 23),
        regie=regie,
    )
    Basket.objects.create(regie=regie, draft_invoice=draft_invoice, invoice=invoice)
    resp = app.get('/api/regie/foo/invoices/cancelled/', params={'payer_external_id': 'payer:1'})
    assert resp.json['err'] == 0
    assert len(resp.json['data']) == 0

    # invoice is not cancelled
    Basket.objects.all().delete()
    invoice.cancelled_at = None
    invoice.save()
    resp = app.get('/api/regie/foo/invoices/cancelled/', params={'payer_external_id': 'payer:1'})
    assert resp.json['err'] == 0
    assert len(resp.json['data']) == 0

    # invoice is collected
    collection = CollectionDocket.objects.create(regie=regie, date_end=now().date(), draft=True)
    invoice.cancelled_at = now()
    invoice.collection = collection
    invoice.save()
    resp = app.get('/api/regie/foo/invoices/cancelled/', params={'payer_external_id': 'payer:1'})
    assert resp.json['err'] == 0
    assert len(resp.json['data']) == 0


@mock.patch.object(Regie, 'get_payer_external_id_from_nameid', autospec=True)
def test_detail_invoice(mock_payer, app, user):
    app.get('/api/regie/foo/invoice/%s/' % str(uuid.uuid4()), status=403)
    app.authorization = ('Basic', ('john.doe', 'password'))

    app.get('/api/regie/foo/invoice/%s/' % str(uuid.uuid4()), status=404)

    regie = Regie.objects.create(label='Foo')
    PaymentBackend.objects.create(label='Test', service='dummy', regie=regie)
    PaymentType.create_defaults(regie)
    app.get('/api/regie/foo/invoice/%s/' % str(uuid.uuid4()), status=404)

    app.get('/api/regie/foo/invoice/%s/' % str(uuid.uuid4()), params={'NameID': 'foobar'}, status=404)

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

    mock_payer.return_value = 'payer:1'
    resp = app.get('/api/regie/foo/invoice/%s/' % str(invoice.uuid), params={'NameID': 'foobar'})
    assert resp.json['err'] == 0
    assert resp.json['data'] == {
        'amount': 42,
        'total_amount': 42,
        'remaining_amount': 42,
        'created': now().date().isoformat(),
        'display_id': 'F%02d-%s-0000001'
        % (
            regie.pk,
            invoice.created_at.strftime('%y-%m'),
        ),
        'has_pdf': True,
        'has_dynamic_pdf': False,
        'has_payments_pdf': False,
        'id': str(invoice.uuid),
        'invoice_label': 'My invoice',
        'is_line': False,
        'label': 'My invoice',
        'online_payment': True,
        'paid': False,
        'pay_limit_date': now().date().isoformat(),
        'due_date': invoice.date_due.isoformat(),
        'payment_deadline_date': invoice.date_payment_deadline.isoformat(),
        'disabled': False,
        'api': {
            'payment_url': 'http://testserver/pay/invoice/%s/' % invoice.uuid,
        },
    }
    invoice.date_invoicing = datetime.date(2022, 9, 1)
    invoice.save()
    resp = app.get('/api/regie/foo/invoice/%s/' % str(invoice.uuid), params={'NameID': 'foobar'})
    assert resp.json['data']['created'] == '2022-09-01'
    assert 'real_created' not in resp.json['data']
    invoice.date_invoicing = None
    invoice.save()

    # publication date is in the future
    invoice.date_publication = now().date() + datetime.timedelta(days=1)
    invoice.save()
    app.get('/api/regie/foo/invoice/%s/' % str(invoice.uuid), params={'NameID': 'foobar'}, status=404)

    # other regie
    other_regie = Regie.objects.create(label='Other Foo')
    invoice.date_publication = now().date()
    invoice.regie = other_regie
    invoice.save()
    app.get('/api/regie/foo/invoice/%s/' % str(invoice.uuid), params={'NameID': 'foobar'}, status=404)

    # part of amount was already paid
    invoice.regie = regie
    invoice.save()
    payment = Payment.objects.create(
        regie=regie,
        amount=1,
        payment_type=PaymentType.objects.get(regie=regie, slug='cash'),
    )
    invoice_line_payment = InvoiceLinePayment.objects.create(
        payment=payment,
        line=line,
        amount=1,
    )
    invoice.refresh_from_db()
    resp = app.get('/api/regie/foo/invoice/%s/' % str(invoice.uuid), params={'NameID': 'foobar'})
    assert resp.json['err'] == 0
    assert resp.json['data'] == {
        'amount': 41,
        'total_amount': 42,
        'remaining_amount': 41,
        'created': now().date().isoformat(),
        'display_id': 'F%02d-%s-0000001'
        % (
            regie.pk,
            invoice.created_at.strftime('%y-%m'),
        ),
        'has_pdf': True,
        'has_dynamic_pdf': True,
        'has_payments_pdf': False,
        'id': str(invoice.uuid),
        'invoice_label': 'My invoice',
        'is_line': False,
        'label': 'My invoice',
        'online_payment': True,
        'paid': False,
        'pay_limit_date': now().date().isoformat(),
        'due_date': invoice.date_due.isoformat(),
        'payment_deadline_date': invoice.date_payment_deadline.isoformat(),
        'disabled': False,
        'api': {
            'payment_url': 'http://testserver/pay/invoice/%s/' % invoice.uuid,
        },
    }

    # invoice is paid
    invoice_line_payment.amount = 42
    invoice_line_payment.save()
    invoice.refresh_from_db()
    resp = app.get('/api/regie/foo/invoice/%s/' % str(invoice.uuid), params={'NameID': 'foobar'})
    assert resp.json['err'] == 0
    assert resp.json['data'] == {
        'amount': 0,
        'total_amount': 42,
        'remaining_amount': 0,
        'created': now().date().isoformat(),
        'display_id': 'F%02d-%s-0000001'
        % (
            regie.pk,
            invoice.created_at.strftime('%y-%m'),
        ),
        'has_pdf': True,
        'has_dynamic_pdf': True,
        'has_payments_pdf': True,
        'id': str(invoice.uuid),
        'invoice_label': 'My invoice',
        'is_line': False,
        'label': 'My invoice',
        'online_payment': False,
        'paid': True,
        'pay_limit_date': '',
        'due_date': invoice.date_due.isoformat(),
        'payment_deadline_date': invoice.date_payment_deadline.isoformat(),
        'disabled': True,
    }

    # no matching payer id
    invoice_line_payment.amount = 1
    invoice_line_payment.save()
    invoice.refresh_from_db()
    mock_payer.return_value = 'payer:unknown'
    app.get('/api/regie/foo/invoice/%s/' % str(invoice.uuid), params={'NameID': 'foobar'}, status=404)

    # campaign is not finalized
    mock_payer.return_value = 'payer:1'
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
    invoice.pool = pool
    invoice.save()
    app.get('/api/regie/foo/invoice/%s/' % str(invoice.uuid), params={'NameID': 'foobar'}, status=404)

    # campaign is finalized
    campaign.finalized = True
    campaign.save()
    app.get('/api/regie/foo/invoice/%s/' % str(invoice.uuid), params={'NameID': 'foobar'}, status=200)

    # invoice is cancelled
    invoice.cancelled_at = now()
    invoice.save()
    app.get('/api/regie/foo/invoice/%s/' % str(invoice.uuid), params={'NameID': 'foobar'}, status=404)

    # invoice is collected
    collection = CollectionDocket.objects.create(regie=regie, date_end=now().date(), draft=True)
    invoice.cancelled_at = None
    invoice.collection = collection
    invoice.save()
    resp = app.get('/api/regie/foo/invoice/%s/' % str(invoice.uuid), params={'NameID': 'foobar'}, status=200)
    assert resp.json['err'] == 0
    assert resp.json['data'] == {
        'amount': 0,
        'total_amount': 42,
        'remaining_amount': 0,
        'collected_amount': 41,
        'created': now().date().isoformat(),
        'display_id': 'F%02d-%s-0000001'
        % (
            regie.pk,
            invoice.created_at.strftime('%y-%m'),
        ),
        'has_pdf': True,
        'has_dynamic_pdf': False,
        'has_payments_pdf': False,
        'id': str(invoice.uuid),
        'invoice_label': 'My invoice',
        'is_line': False,
        'label': 'My invoice',
        'online_payment': False,
        'paid': False,
        'pay_limit_date': '',
        'due_date': invoice.date_due.isoformat(),
        'payment_deadline_date': invoice.date_payment_deadline.isoformat(),
        'collection_date': now().date().isoformat(),
        'disabled': False,
    }

    payment_type, dummy = PaymentType.objects.get_or_create(regie=regie, slug='collect')
    Payment.make_payment(
        regie=regie,
        invoices=[invoice],
        amount=41,
        payment_type=payment_type,
    )
    resp = app.get('/api/regie/foo/invoice/%s/' % str(invoice.uuid), params={'NameID': 'foobar'}, status=200)
    assert resp.json['err'] == 0
    assert resp.json['data'] == {
        'amount': 0,
        'total_amount': 42,
        'remaining_amount': 0,
        'collected_amount': 41,
        'created': now().date().isoformat(),
        'display_id': 'F%02d-%s-0000001'
        % (
            regie.pk,
            invoice.created_at.strftime('%y-%m'),
        ),
        'has_pdf': True,
        'has_dynamic_pdf': False,
        'has_payments_pdf': False,
        'id': str(invoice.uuid),
        'invoice_label': 'My invoice',
        'is_line': False,
        'label': 'My invoice',
        'online_payment': False,
        'paid': False,
        'pay_limit_date': '',
        'due_date': invoice.date_due.isoformat(),
        'payment_deadline_date': invoice.date_payment_deadline.isoformat(),
        'collection_date': now().date().isoformat(),
        'disabled': True,
    }


def test_detail_invoice_for_payer(app, user):
    app.get('/api/regie/foo/invoice/%s/' % str(uuid.uuid4()), status=403)
    app.authorization = ('Basic', ('john.doe', 'password'))

    app.get('/api/regie/foo/invoice/%s/' % str(uuid.uuid4()), status=404)

    regie = Regie.objects.create(label='Foo')
    PaymentBackend.objects.create(label='Test', service='dummy', regie=regie)
    PaymentType.create_defaults(regie)
    app.get('/api/regie/foo/invoice/%s/' % str(uuid.uuid4()), status=404)

    app.get(
        '/api/regie/foo/invoice/%s/' % str(uuid.uuid4()), params={'payer_external_id': 'payer:1'}, status=404
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

    resp = app.get('/api/regie/foo/invoice/%s/' % str(invoice.uuid), params={'payer_external_id': 'payer:1'})
    assert resp.json['err'] == 0
    assert resp.json['data'] == {
        'amount': 42,
        'total_amount': 42,
        'remaining_amount': 42,
        'created': now().date().isoformat(),
        'display_id': 'F%02d-%s-0000001'
        % (
            regie.pk,
            invoice.created_at.strftime('%y-%m'),
        ),
        'has_pdf': True,
        'has_dynamic_pdf': False,
        'has_payments_pdf': False,
        'id': str(invoice.uuid),
        'invoice_label': 'My invoice',
        'is_line': False,
        'label': 'My invoice',
        'online_payment': False,
        'paid': False,
        'pay_limit_date': now().date().isoformat(),
        'due_date': invoice.date_due.isoformat(),
        'payment_deadline_date': invoice.date_payment_deadline.isoformat(),
        'disabled': False,
    }
    invoice.date_invoicing = datetime.date(2022, 9, 1)
    invoice.save()
    resp = app.get('/api/regie/foo/invoice/%s/' % str(invoice.uuid), params={'payer_external_id': 'payer:1'})
    assert resp.json['data']['created'] == '2022-09-01'
    assert resp.json['data']['real_created'] == now().date().isoformat()
    invoice.date_invoicing = None
    invoice.save()

    # publication date is in the future
    invoice.date_publication = now().date() + datetime.timedelta(days=1)
    invoice.save()
    app.get(
        '/api/regie/foo/invoice/%s/' % str(invoice.uuid), params={'payer_external_id': 'payer:1'}, status=404
    )

    # other regie
    other_regie = Regie.objects.create(label='Other Foo')
    invoice.date_publication = now().date()
    invoice.regie = other_regie
    invoice.save()
    app.get(
        '/api/regie/foo/invoice/%s/' % str(invoice.uuid), params={'payer_external_id': 'payer:1'}, status=404
    )

    # part of amount was already paid
    invoice.regie = regie
    invoice.save()
    payment = Payment.objects.create(
        regie=regie,
        amount=1,
        payment_type=PaymentType.objects.get(regie=regie, slug='cash'),
    )
    invoice_line_payment = InvoiceLinePayment.objects.create(
        payment=payment,
        line=line,
        amount=1,
    )
    invoice.refresh_from_db()
    resp = app.get('/api/regie/foo/invoice/%s/' % str(invoice.uuid), params={'payer_external_id': 'payer:1'})
    assert resp.json['err'] == 0
    assert resp.json['data'] == {
        'amount': 41,
        'total_amount': 42,
        'remaining_amount': 41,
        'created': now().date().isoformat(),
        'display_id': 'F%02d-%s-0000001'
        % (
            regie.pk,
            invoice.created_at.strftime('%y-%m'),
        ),
        'has_pdf': True,
        'has_dynamic_pdf': True,
        'has_payments_pdf': False,
        'id': str(invoice.uuid),
        'invoice_label': 'My invoice',
        'is_line': False,
        'label': 'My invoice',
        'online_payment': False,
        'paid': False,
        'pay_limit_date': now().date().isoformat(),
        'due_date': invoice.date_due.isoformat(),
        'payment_deadline_date': invoice.date_payment_deadline.isoformat(),
        'disabled': False,
    }

    # invoice is paid
    invoice_line_payment.amount = 42
    invoice_line_payment.save()
    invoice.refresh_from_db()
    resp = app.get('/api/regie/foo/invoice/%s/' % str(invoice.uuid), params={'payer_external_id': 'payer:1'})
    assert resp.json['err'] == 0
    assert resp.json['data'] == {
        'amount': 0,
        'total_amount': 42,
        'remaining_amount': 0,
        'created': now().date().isoformat(),
        'display_id': 'F%02d-%s-0000001'
        % (
            regie.pk,
            invoice.created_at.strftime('%y-%m'),
        ),
        'has_pdf': True,
        'has_dynamic_pdf': True,
        'has_payments_pdf': True,
        'id': str(invoice.uuid),
        'invoice_label': 'My invoice',
        'is_line': False,
        'label': 'My invoice',
        'online_payment': False,
        'paid': True,
        'pay_limit_date': '',
        'due_date': invoice.date_due.isoformat(),
        'payment_deadline_date': invoice.date_payment_deadline.isoformat(),
        'disabled': True,
    }

    # campaign is not finalized
    invoice_line_payment.amount = 1
    invoice_line_payment.save()
    invoice.refresh_from_db()
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
    invoice.pool = pool
    invoice.save()
    app.get(
        '/api/regie/foo/invoice/%s/' % str(invoice.uuid), params={'payer_external_id': 'payer:1'}, status=404
    )

    # campaign is finalized
    campaign.finalized = True
    campaign.save()
    app.get(
        '/api/regie/foo/invoice/%s/' % str(invoice.uuid), params={'payer_external_id': 'payer:1'}, status=200
    )

    # invoice is cancelled
    invoice.cancelled_at = now()
    invoice.save()
    app.get(
        '/api/regie/foo/invoice/%s/' % str(invoice.uuid), params={'payer_external_id': 'payer:1'}, status=404
    )

    # invoice is collected
    collection = CollectionDocket.objects.create(regie=regie, date_end=now().date(), draft=True)
    invoice.cancelled_at = None
    invoice.collection = collection
    invoice.save()
    resp = app.get(
        '/api/regie/foo/invoice/%s/' % str(invoice.uuid), params={'payer_external_id': 'payer:1'}, status=200
    )
    assert resp.json['err'] == 0
    assert resp.json['data'] == {
        'amount': 0,
        'total_amount': 42,
        'remaining_amount': 0,
        'collected_amount': 41,
        'created': now().date().isoformat(),
        'display_id': 'F%02d-%s-0000001'
        % (
            regie.pk,
            invoice.created_at.strftime('%y-%m'),
        ),
        'has_pdf': True,
        'has_dynamic_pdf': False,
        'has_payments_pdf': False,
        'id': str(invoice.uuid),
        'invoice_label': 'My invoice',
        'is_line': False,
        'label': 'My invoice',
        'online_payment': False,
        'paid': False,
        'pay_limit_date': '',
        'due_date': invoice.date_due.isoformat(),
        'payment_deadline_date': invoice.date_payment_deadline.isoformat(),
        'collection_date': now().date().isoformat(),
        'disabled': False,
    }

    payment_type, dummy = PaymentType.objects.get_or_create(regie=regie, slug='collect')
    Payment.make_payment(
        regie=regie,
        invoices=[invoice],
        amount=41,
        payment_type=payment_type,
    )
    resp = app.get(
        '/api/regie/foo/invoice/%s/' % str(invoice.uuid), params={'payer_external_id': 'payer:1'}, status=200
    )
    assert resp.json['err'] == 0
    assert resp.json['data'] == {
        'amount': 0,
        'total_amount': 42,
        'remaining_amount': 0,
        'collected_amount': 41,
        'created': now().date().isoformat(),
        'display_id': 'F%02d-%s-0000001'
        % (
            regie.pk,
            invoice.created_at.strftime('%y-%m'),
        ),
        'has_pdf': True,
        'has_dynamic_pdf': False,
        'has_payments_pdf': False,
        'id': str(invoice.uuid),
        'invoice_label': 'My invoice',
        'is_line': False,
        'label': 'My invoice',
        'online_payment': False,
        'paid': False,
        'pay_limit_date': '',
        'due_date': invoice.date_due.isoformat(),
        'payment_deadline_date': invoice.date_payment_deadline.isoformat(),
        'collection_date': now().date().isoformat(),
        'disabled': True,
    }


@mock.patch.object(Regie, 'get_payer_external_id_from_nameid', autospec=True)
@pytest.mark.parametrize('dynamic', [True, False])
def test_pdf_invoice(mock_payer, app, user, dynamic):
    url = '/api/regie/foo/invoice/%s/'
    if dynamic:
        url += 'dynamic/'
    url += 'pdf/'

    app.get(url % str(uuid.uuid4()), status=403)
    app.authorization = ('Basic', ('john.doe', 'password'))

    app.get(url % str(uuid.uuid4()), status=404)

    regie = Regie.objects.create(label='Foo')
    app.get(url % str(uuid.uuid4()), status=404)

    app.get(url % str(uuid.uuid4()), params={'NameID': 'foobar'}, status=404)

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
    InvoiceLine.objects.create(
        event_date=now().date(),
        invoice=invoice,
        quantity=1,
        unit_amount=42,
    )
    invoice.refresh_from_db()

    mock_payer.return_value = 'payer:1'
    resp = app.get(url % str(invoice.uuid), params={'NameID': 'foobar'})
    assert resp.headers['Content-Disposition'] == 'attachment; filename="%s.pdf"' % invoice.formatted_number

    # publication date is in the future
    invoice.date_publication = now().date() + datetime.timedelta(days=1)
    invoice.save()
    app.get(url % str(invoice.uuid), params={'NameID': 'foobar'}, status=404)

    # other regie
    other_regie = Regie.objects.create(label='Other Foo')
    invoice.date_publication = now().date()
    invoice.regie = other_regie
    invoice.save()
    app.get(url % str(invoice.uuid), params={'NameID': 'foobar'}, status=404)

    # no matching payer id
    invoice.regie = regie
    invoice.save()
    mock_payer.return_value = 'payer:unknown'
    app.get(url % str(invoice.uuid), params={'NameID': 'foobar'}, status=404)

    # payer error
    mock_payer.side_effect = PayerError
    app.get(url % str(invoice.uuid), params={'NameID': 'foobar'}, status=404)

    # campaign is not finalized
    mock_payer.side_effect = None
    mock_payer.return_value = 'payer:1'
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
    invoice.pool = pool
    invoice.save()
    app.get(url % str(invoice.uuid), params={'NameID': 'foobar'}, status=404)

    # campaign is finalized
    campaign.finalized = True
    campaign.save()
    app.get(url % str(invoice.uuid), params={'NameID': 'foobar'}, status=200)

    # invoice is cancelled
    invoice.cancelled_at = now()
    invoice.save()
    app.get(url % str(invoice.uuid), params={'NameID': 'foobar'}, status=404)


@pytest.mark.parametrize('dynamic', [True, False])
def test_pdf_invoice_for_payer(app, user, dynamic):
    url = '/api/regie/foo/invoice/%s/'
    if dynamic:
        url += 'dynamic/'
    url += 'pdf/'

    app.get(url % str(uuid.uuid4()), status=403)
    app.authorization = ('Basic', ('john.doe', 'password'))

    app.get(url % str(uuid.uuid4()), status=404)

    regie = Regie.objects.create(label='Foo')
    app.get(url % str(uuid.uuid4()), status=404)

    app.get(
        url % str(uuid.uuid4()),
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
    InvoiceLine.objects.create(
        event_date=now().date(),
        invoice=invoice,
        quantity=1,
        unit_amount=42,
    )
    invoice.refresh_from_db()

    resp = app.get(url % str(invoice.uuid), params={'payer_external_id': 'payer:1'})
    assert resp.headers['Content-Disposition'] == 'attachment; filename="%s.pdf"' % invoice.formatted_number

    # publication date is in the future
    invoice.date_publication = now().date() + datetime.timedelta(days=1)
    invoice.save()
    app.get(
        url % str(invoice.uuid),
        params={'payer_external_id': 'payer:1'},
        status=404,
    )

    # other regie
    other_regie = Regie.objects.create(label='Other Foo')
    invoice.date_publication = now().date()
    invoice.regie = other_regie
    invoice.save()
    app.get(
        url % str(invoice.uuid),
        params={'payer_external_id': 'payer:1'},
        status=404,
    )

    # campaign is not finalized
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
    invoice.regie = regie
    invoice.pool = pool
    invoice.save()
    app.get(
        url % str(invoice.uuid),
        params={'payer_external_id': 'payer:1'},
        status=404,
    )

    # campaign is finalized
    campaign.finalized = True
    campaign.save()
    app.get(
        url % str(invoice.uuid),
        params={'payer_external_id': 'payer:1'},
        status=200,
    )

    # invoice is cancelled
    invoice.cancelled_at = now()
    invoice.save()
    app.get(
        url % str(invoice.uuid),
        params={'payer_external_id': 'payer:1'},
        status=404,
    )


@mock.patch.object(Regie, 'get_payer_external_id_from_nameid', autospec=True)
def test_pdf_invoice_payments(mock_payer, app, user):
    app.get('/api/regie/foo/invoice/%s/payments/pdf/' % str(uuid.uuid4()), status=403)
    app.authorization = ('Basic', ('john.doe', 'password'))

    app.get('/api/regie/foo/invoice/%s/payments/pdf/' % str(uuid.uuid4()), status=404)

    regie = Regie.objects.create(label='Foo')
    PaymentType.create_defaults(regie)
    app.get('/api/regie/foo/invoice/%s/payments/pdf/' % str(uuid.uuid4()), status=404)

    app.get(
        '/api/regie/foo/invoice/%s/payments/pdf/' % str(uuid.uuid4()), params={'NameID': 'foobar'}, status=404
    )

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
    invoice.refresh_from_db()
    assert invoice.remaining_amount == 0

    mock_payer.return_value = 'payer:1'
    resp = app.get('/api/regie/foo/invoice/%s/payments/pdf/' % str(invoice.uuid), params={'NameID': 'foobar'})
    assert resp.headers['Content-Disposition'] == 'attachment; filename="A-%s.pdf"' % invoice.formatted_number

    # publication date is in the future
    invoice.date_publication = now().date() + datetime.timedelta(days=1)
    invoice.save()
    app.get(
        '/api/regie/foo/invoice/%s/payments/pdf/' % str(invoice.uuid), params={'NameID': 'foobar'}, status=404
    )

    # other regie
    other_regie = Regie.objects.create(label='Other Foo')
    invoice.date_publication = now().date()
    invoice.regie = other_regie
    invoice.save()
    app.get(
        '/api/regie/foo/invoice/%s/payments/pdf/' % str(invoice.uuid), params={'NameID': 'foobar'}, status=404
    )

    # no matching payer id
    invoice.regie = regie
    invoice.save()
    mock_payer.return_value = 'payer:unknown'
    app.get(
        '/api/regie/foo/invoice/%s/payments/pdf/' % str(invoice.uuid), params={'NameID': 'foobar'}, status=404
    )

    # payer error
    mock_payer.side_effect = PayerError
    app.get(
        '/api/regie/foo/invoice/%s/payments/pdf/' % str(invoice.uuid), params={'NameID': 'foobar'}, status=404
    )

    # campaign is not finalized
    mock_payer.side_effect = None
    mock_payer.return_value = 'payer:1'
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
    invoice.pool = pool
    invoice.save()
    app.get(
        '/api/regie/foo/invoice/%s/payments/pdf/' % str(invoice.uuid), params={'NameID': 'foobar'}, status=404
    )

    # campaign is finalized
    campaign.finalized = True
    campaign.save()
    app.get(
        '/api/regie/foo/invoice/%s/payments/pdf/' % str(invoice.uuid), params={'NameID': 'foobar'}, status=200
    )

    # invoice has remaining_amount
    line = InvoiceLine.objects.create(
        event_date=now().date(),
        invoice=invoice,
        quantity=1,
        unit_amount=42,
    )
    invoice.refresh_from_db()
    assert invoice.remaining_amount > 0
    app.get(
        '/api/regie/foo/invoice/%s/payments/pdf/' % str(invoice.uuid), params={'NameID': 'foobar'}, status=404
    )

    # invoice is cancelled
    line.delete()
    invoice.refresh_from_db()
    invoice.cancelled_at = now()
    invoice.save()
    app.get(
        '/api/regie/foo/invoice/%s/payments/pdf/' % str(invoice.uuid), params={'NameID': 'foobar'}, status=404
    )

    # invoice is collected
    collection = CollectionDocket.objects.create(regie=regie, date_end=now().date(), draft=True)
    invoice.cancelled_at = None
    invoice.collection = collection
    invoice.save()
    app.get(
        '/api/regie/foo/invoice/%s/payments/pdf/' % str(invoice.uuid), params={'NameID': 'foobar'}, status=404
    )


def test_pdf_invoice_payments_for_payer(app, user):
    app.get('/api/regie/foo/invoice/%s/payments/pdf/' % str(uuid.uuid4()), status=403)
    app.authorization = ('Basic', ('john.doe', 'password'))

    app.get('/api/regie/foo/invoice/%s/payments/pdf/' % str(uuid.uuid4()), status=404)

    regie = Regie.objects.create(label='Foo')
    PaymentType.create_defaults(regie)
    app.get('/api/regie/foo/invoice/%s/payments/pdf/' % str(uuid.uuid4()), status=404)

    app.get(
        '/api/regie/foo/invoice/%s/payments/pdf/' % str(uuid.uuid4()),
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
    invoice.refresh_from_db()
    assert invoice.remaining_amount == 0

    resp = app.get(
        '/api/regie/foo/invoice/%s/payments/pdf/' % str(invoice.uuid), params={'payer_external_id': 'payer:1'}
    )
    assert resp.headers['Content-Disposition'] == 'attachment; filename="A-%s.pdf"' % invoice.formatted_number

    # publication date is in the future
    invoice.date_publication = now().date() + datetime.timedelta(days=1)
    invoice.save()
    app.get(
        '/api/regie/foo/invoice/%s/payments/pdf/' % str(invoice.uuid),
        params={'payer_external_id': 'payer:1'},
        status=404,
    )

    # other regie
    other_regie = Regie.objects.create(label='Other Foo')
    invoice.date_publication = now().date()
    invoice.regie = other_regie
    invoice.save()
    app.get(
        '/api/regie/foo/invoice/%s/payments/pdf/' % str(invoice.uuid),
        params={'payer_external_id': 'payer:1'},
        status=404,
    )

    # campaign is not finalized
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
    invoice.regie = regie
    invoice.pool = pool
    invoice.save()
    app.get(
        '/api/regie/foo/invoice/%s/payments/pdf/' % str(invoice.uuid),
        params={'payer_external_id': 'payer:1'},
        status=404,
    )

    # campaign is finalized
    campaign.finalized = True
    campaign.save()
    app.get(
        '/api/regie/foo/invoice/%s/payments/pdf/' % str(invoice.uuid),
        params={'payer_external_id': 'payer:1'},
        status=200,
    )

    # invoice has remaining_amount
    line = InvoiceLine.objects.create(
        event_date=now().date(),
        invoice=invoice,
        quantity=1,
        unit_amount=42,
    )
    invoice.refresh_from_db()
    assert invoice.remaining_amount > 0
    app.get(
        '/api/regie/foo/invoice/%s/payments/pdf/' % str(invoice.uuid),
        params={'payer_external_id': 'payer:1'},
        status=404,
    )

    # invoice is cancelled
    line.delete()
    invoice.refresh_from_db()
    invoice.cancelled_at = now()
    invoice.save()
    app.get(
        '/api/regie/foo/invoice/%s/payments/pdf/' % str(invoice.uuid),
        params={'payer_external_id': 'payer:1'},
        status=404,
    )

    # invoice is collected
    collection = CollectionDocket.objects.create(regie=regie, date_end=now().date(), draft=True)
    invoice.cancelled_at = None
    invoice.collection = collection
    invoice.save()
    app.get(
        '/api/regie/foo/invoice/%s/payments/pdf/' % str(invoice.uuid),
        params={'payer_external_id': 'payer:1'},
        status=404,
    )


def test_pay_invoice(app, user):
    app.post('/api/regie/foo/invoice/%s/pay/' % str(uuid.uuid4()), status=404)

    regie = Regie.objects.create(label='Foo')
    app.post('/api/regie/foo/invoice/%s/pay/' % str(uuid.uuid4()), status=404)

    invoice = Invoice.objects.create(
        label='My invoice',
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=now().date(),
        date_due=datetime.date(2022, 10, 31),
        regie=regie,
        payer_external_id='payer:1',
        payer_first_name='Foo',
        payer_last_name='Bar',
        payer_address='41 rue des kangourous\n99999 Kangourou Ville',
        payer_email='email1',
        payer_phone='phone1',
    )
    app.post('/api/regie/foo/invoice/%s/pay/' % str(invoice.uuid), status=404)

    line1 = InvoiceLine.objects.create(
        event_date=now().date(),
        invoice=invoice,
        quantity=1,
        unit_amount=84,
    )
    line2 = InvoiceLine.objects.create(
        event_date=now().date(),
        invoice=invoice,
        quantity=-1,
        unit_amount=42,
    )
    invoice.refresh_from_db()

    # no payment type 'online'
    app.post('/api/regie/foo/invoice/%s/pay/' % str(invoice.uuid), status=404)

    # no payment type 'online' for the regie
    other_regie = Regie.objects.create(label='Bar')
    PaymentType.create_defaults(other_regie)
    app.post('/api/regie/foo/invoice/%s/pay/' % str(invoice.uuid), status=404)

    PaymentType.create_defaults(regie)  # create default payment types
    resp = app.post('/api/regie/foo/invoice/%s/pay/' % str(invoice.uuid))
    assert Payment.objects.count() == 1
    assert InvoiceLinePayment.objects.count() == 2
    payment = Payment.objects.latest('pk')
    assert resp.json == {'data': {'id': str(payment.uuid)}, 'err': 0}
    assert payment.regie == regie
    assert payment.payment_type.slug == 'online'
    assert payment.payment_type.regie == regie
    assert payment.transaction_id is None
    assert payment.transaction_date is None
    assert payment.order_id is None
    assert payment.bank_transaction_id is None
    assert payment.bank_transaction_date is None
    assert payment.bank_data == {}
    assert payment.amount == 42
    assert payment.number == 1
    assert payment.formatted_number == 'R%02d-%s-0000001' % (
        regie.pk,
        now().strftime('%y-%m'),
    )
    (
        invoice_line_payment1,
        invoice_line_payment2,
    ) = payment.invoicelinepayment_set.order_by('pk')
    assert invoice_line_payment1.amount == -42
    assert invoice_line_payment1.line == line2
    assert invoice_line_payment2.amount == 84
    assert invoice_line_payment2.line == line1
    invoice.refresh_from_db()
    assert invoice.remaining_amount == 0
    assert invoice.paid_amount == 42

    InvoiceLinePayment.objects.all().delete()
    Payment.objects.all().delete()
    invoice.refresh_from_db()
    invoice.payment_callback_url = 'http://payment.com'
    invoice.save()
    with mock.patch('lingo.utils.requests_wrapper.RequestsSession.send') as mock_send:
        resp = app.post(
            '/api/regie/foo/invoice/%s/pay/' % str(invoice.uuid),
            params={'transaction_id': 'foobar', 'transaction_date': 'foobaz', 'amount': 'foobar'},
        )
    assert [x[0][0].url for x in mock_send.call_args_list] == ['http://payment.com/']
    assert Payment.objects.count() == 1
    assert InvoiceLinePayment.objects.count() == 2
    payment = Payment.objects.latest('pk')
    assert resp.json == {'data': {'id': str(payment.uuid)}, 'err': 0}
    assert payment.regie == regie
    assert payment.payment_type.slug == 'online'
    assert payment.payment_type.regie == regie
    assert payment.transaction_id == 'foobar'
    assert payment.transaction_date is None
    assert payment.order_id is None
    assert payment.bank_transaction_id is None
    assert payment.bank_transaction_date is None
    assert payment.bank_data == {}
    assert payment.amount == 42
    assert payment.number == 2
    assert payment.formatted_number == 'R%02d-%s-0000002' % (
        regie.pk,
        now().strftime('%y-%m'),
    )
    assert payment.payer_external_id == 'payer:1'
    assert payment.payer_first_name == 'Foo'
    assert payment.payer_last_name == 'Bar'
    assert payment.payer_address == '41 rue des kangourous\n99999 Kangourou Ville'
    assert payment.payer_email == 'email1'
    assert payment.payer_phone == 'phone1'
    (invoice_line_payment1, invoice_line_payment2) = payment.invoicelinepayment_set.order_by('pk')
    assert invoice_line_payment1.amount == -42
    assert invoice_line_payment1.line == line2
    assert invoice_line_payment2.amount == 84
    assert invoice_line_payment2.line == line1
    invoice.refresh_from_db()
    assert invoice.remaining_amount == 0
    assert invoice.paid_amount == 42

    InvoiceLinePayment.objects.all().delete()
    Payment.objects.all().delete()
    resp = app.post_json(
        '/api/regie/foo/invoice/%s/pay/' % str(invoice.uuid),
        params={
            'transaction_id': 'foobar1',
            'transaction_date': '2023-04-13T16:06:42',
            'order_id': 'foobar2',
            'bank_transaction_id': 'foobar3',
            'bank_transaction_date': '2023-04-13T10:00:00',
            'bank_data': {
                'foo': 'bar',
            },
            'amount': '41.5',
        },
    )
    assert Payment.objects.count() == 1
    assert InvoiceLinePayment.objects.count() == 2
    payment = Payment.objects.latest('pk')
    assert resp.json == {'data': {'id': str(payment.uuid)}, 'err': 0}
    assert payment.regie == regie
    assert payment.payment_type.slug == 'online'
    assert payment.payment_type.regie == regie
    assert payment.transaction_id == 'foobar1'
    assert payment.transaction_date == make_aware(datetime.datetime(2023, 4, 13, 18, 6, 42))
    assert payment.order_id == 'foobar2'
    assert payment.bank_transaction_id == 'foobar3'
    assert payment.bank_transaction_date == make_aware(datetime.datetime(2023, 4, 13, 12, 0, 0))
    assert payment.bank_data == {
        'foo': 'bar',
    }
    assert payment.amount == decimal.Decimal('41.5')
    assert payment.number == 3
    assert payment.formatted_number == 'R%02d-%s-0000003' % (
        regie.pk,
        now().strftime('%y-%m'),
    )
    assert payment.payer_external_id == 'payer:1'
    assert payment.payer_first_name == 'Foo'
    assert payment.payer_last_name == 'Bar'
    assert payment.payer_address == '41 rue des kangourous\n99999 Kangourou Ville'
    assert payment.payer_email == 'email1'
    assert payment.payer_phone == 'phone1'
    (invoice_line_payment1, invoice_line_payment2) = payment.invoicelinepayment_set.order_by('pk')
    assert invoice_line_payment1.amount == -42
    assert invoice_line_payment1.line == line2
    assert invoice_line_payment2.amount == decimal.Decimal('83.5')
    assert invoice_line_payment2.line == line1
    invoice.refresh_from_db()
    assert invoice.remaining_amount == decimal.Decimal('0.5')
    assert invoice.paid_amount == decimal.Decimal('41.5')

    # campaign is not finalized
    InvoiceLinePayment.objects.all().delete()
    Payment.objects.all().delete()
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
    invoice.refresh_from_db()
    invoice.pool = pool
    invoice.save()
    app.post(
        '/api/regie/foo/invoice/%s/pay/' % str(invoice.uuid),
        params={'transaction_id': 'foobar', 'transaction_date': '2023-04-13T16:06:42'},
        status=404,
    )

    # campaign is finalized
    campaign.finalized = True
    campaign.save()
    app.post(
        '/api/regie/foo/invoice/%s/pay/' % str(invoice.uuid),
        params={'transaction_id': 'foobar', 'transaction_date': '2023-04-13T16:06:42'},
        status=200,
    )

    # invoice is cancelled
    InvoiceLinePayment.objects.all().delete()
    Payment.objects.all().delete()
    invoice.refresh_from_db()
    invoice.cancelled_at = now()
    invoice.save()
    app.post(
        '/api/regie/foo/invoice/%s/pay/' % str(invoice.uuid),
        params={'transaction_id': 'foobar', 'transaction_date': '2023-04-13T16:06:42'},
        status=404,
    )

    # invoice is collected
    collection = CollectionDocket.objects.create(regie=regie, date_end=now().date(), draft=True)
    InvoiceLinePayment.objects.all().delete()
    Payment.objects.all().delete()
    invoice.refresh_from_db()
    invoice.cancelled_at = None
    invoice.collection = collection
    invoice.save()
    app.post(
        '/api/regie/foo/invoice/%s/pay/' % str(invoice.uuid),
        params={'transaction_id': 'foobar', 'transaction_date': '2023-04-13T16:06:42'},
        status=404,
    )


def test_cancel_invoice(app, user, simple_user):
    app.post('/api/regie/foo/invoice/%s/cancel/' % str(uuid.uuid4()), status=403)
    app.authorization = ('Basic', ('john.doe', 'password'))

    app.post('/api/regie/foo/invoice/%s/cancel/' % str(uuid.uuid4()), status=404)

    regie = Regie.objects.create(label='Foo')
    PaymentType.create_defaults(regie)
    app.post('/api/regie/foo/invoice/%s/cancel/' % str(uuid.uuid4()), status=404)

    invoice = Invoice.objects.create(
        label='My invoice',
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=now().date(),
        date_due=datetime.date(2022, 10, 31),
        regie=regie,
        payer_external_id='payer:1',
        payer_first_name='Foo',
        payer_last_name='Bar',
        payer_address='41 rue des kangourous\n99999 Kangourou Ville',
    )
    line = InvoiceLine.objects.create(
        event_date=now().date(),
        invoice=invoice,
        quantity=1,
        unit_amount=40,
    )
    line = InvoiceLine.objects.create(
        event_date=now().date(),
        invoice=invoice,
        quantity=1,
        unit_amount=2,
    )
    resp = app.post('/api/regie/foo/invoice/%s/cancel/' % str(invoice.uuid), status=400)
    assert resp.json['err']
    assert resp.json['errors'] == {
        'cancellation_reason': ['This field is required.'],
    }

    params = {
        'cancellation_reason': 'foo',  # unknown cancellation reason
        'cancellation_description': 'foo bar',
        'user_uuid': str(uuid.uuid4()),  # unknown user
    }
    resp = app.post('/api/regie/foo/invoice/%s/cancel/' % str(invoice.uuid), params=params, status=400)
    assert resp.json['err']
    assert resp.json['errors'] == {
        'cancellation_reason': ['Object with slug=foo does not exist.'],
        'user_uuid': ['User not found.'],
    }

    InvoiceCancellationReason.objects.create(slug='foo', label='Foo', disabled=True)
    resp = app.post('/api/regie/foo/invoice/%s/cancel/' % str(invoice.uuid), params=params, status=400)
    assert resp.json['err']
    assert resp.json['errors'] == {
        'cancellation_reason': ['Object with slug=foo does not exist.'],
        'user_uuid': ['User not found.'],
    }

    params.pop('user_uuid')
    InvoiceCancellationReason.objects.update(disabled=False)
    with mock.patch('lingo.utils.requests_wrapper.RequestsSession.send') as mock_send:
        resp = app.post('/api/regie/foo/invoice/%s/cancel/' % str(invoice.uuid), params=params)
    assert [x[0][0].url for x in mock_send.call_args_list] == []
    invoice.refresh_from_db()
    assert invoice.cancelled_at is not None
    assert invoice.cancellation_reason.slug == 'foo'
    assert invoice.cancellation_description == 'foo bar'
    assert invoice.cancelled_by is None

    # invoice is already cancelled
    app.post('/api/regie/foo/invoice/%s/cancel/' % str(invoice.uuid), params=params, status=404)

    # check notifications
    invoice.cancelled_at = None
    invoice.cancellation_reason = None
    invoice.cancellation_description = ''
    invoice.cancelled_by = None
    invoice.cancel_callback_url = 'http://cancel.com'
    invoice.save()
    params['user_uuid'] = 'ab' * 16
    with mock.patch('lingo.utils.requests_wrapper.RequestsSession.send') as mock_send:
        resp = app.post('/api/regie/foo/invoice/%s/cancel/' % str(invoice.uuid), params=params)
    assert [x[0][0].url for x in mock_send.call_args_list] == ['http://cancel.com/']
    invoice.refresh_from_db()
    assert invoice.cancelled_at is not None
    assert invoice.cancellation_reason.slug == 'foo'
    assert invoice.cancellation_description == 'foo bar'
    assert invoice.cancelled_by == simple_user

    params['notify'] = False
    invoice.cancelled_at = None
    invoice.cancellation_reason = None
    invoice.cancellation_description = ''
    invoice.cancelled_by = None
    invoice.save()
    with mock.patch('lingo.utils.requests_wrapper.RequestsSession.send') as mock_send:
        resp = app.post('/api/regie/foo/invoice/%s/cancel/' % str(invoice.uuid), params=params)
    assert [x[0][0].url for x in mock_send.call_args_list] == []
    invoice.refresh_from_db()
    assert invoice.cancelled_at is not None
    assert invoice.cancellation_reason.slug == 'foo'
    assert invoice.cancellation_description == 'foo bar'
    assert invoice.cancelled_by == simple_user

    # invoice has payments
    invoice.cancelled_at = None
    invoice.cancellation_reason = None
    invoice.cancellation_description = ''
    invoice.cancelled_by = None
    invoice.save()
    payment = Payment.objects.create(
        regie=regie,
        amount=1,
        payment_type=PaymentType.objects.get(regie=regie, slug='cash'),
    )
    InvoiceLinePayment.objects.create(
        payment=payment,
        line=line,
        amount=1,
    )
    app.post('/api/regie/foo/invoice/%s/cancel/' % str(invoice.uuid), params=params, status=404)

    # campaign is not finalized
    InvoiceLinePayment.objects.all().delete()
    Payment.objects.all().delete()
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
    invoice.pool = pool
    invoice.save()
    app.post('/api/regie/foo/invoice/%s/cancel/' % str(invoice.uuid), params=params, status=404)

    # campaign is finalized
    campaign.finalized = True
    campaign.save()
    app.post('/api/regie/foo/invoice/%s/cancel/' % str(invoice.uuid), params=params)
    invoice.refresh_from_db()
    assert invoice.cancelled_at is not None
    assert invoice.cancellation_reason.slug == 'foo'
    assert invoice.cancellation_description == 'foo bar'
    assert invoice.cancelled_by == simple_user

    # invoice is collected
    collection = CollectionDocket.objects.create(regie=regie, date_end=now().date(), draft=True)
    invoice.cancelled_at = None
    invoice.collection = collection
    invoice.save()
    app.post('/api/regie/foo/invoice/%s/cancel/' % str(invoice.uuid), params=params, status=404)


def test_add_draft_invoice(app, user):
    app.post('/api/regie/foo/draft-invoices/', status=403)
    app.authorization = ('Basic', ('john.doe', 'password'))

    resp = app.post('/api/regie/foo/draft-invoices/', status=404)

    regie = Regie.objects.create(label='Foo')
    other_regie = Regie.objects.create(label='Other Foo')
    resp = app.post('/api/regie/foo/draft-invoices/', status=400)
    assert resp.json['err']
    assert resp.json['errors'] == {
        'date_due': ['This field is required.'],
        'date_payment_deadline': ['This field is required.'],
        'date_publication': ['This field is required.'],
        'label': ['This field is required.'],
        'payer_external_id': ['This field is required.'],
        'payer_first_name': ['This field is required.'],
        'payer_last_name': ['This field is required.'],
        'payer_address': ['This field is required.'],
    }

    params = {
        'date_due': '2023-04-23',
        'date_payment_deadline': '2023-04-22',
        'date_publication': '2023-04-21',
        'label': 'Foo Bar',
        'payer_external_id': 'payer:1',
        'payer_first_name': 'First',
        'payer_last_name': 'Last',
        'payer_address': '41 rue des kangourous\n99999 Kangourou Ville',
        'payer_email': 'email1',
        'payer_phone': 'phone1',
        'payment_callback_url': 'http://payment.com',
        'cancel_callback_url': 'http://cancel.com',
    }
    if random.choice((True, False)) is True:
        params['previous_invoice'] = ''
    resp = app.post('/api/regie/foo/draft-invoices/', params=params)
    assert resp.json['err'] == 0
    invoice = DraftInvoice.objects.latest('pk')
    assert resp.json['data'] == {'draft_invoice_id': str(invoice.uuid)}
    assert invoice.label == 'Foo Bar'
    assert invoice.total_amount == 0
    assert invoice.date_publication == datetime.date(2023, 4, 21)
    assert invoice.date_payment_deadline_displayed is None
    assert invoice.date_payment_deadline == datetime.date(2023, 4, 22)
    assert invoice.date_due == datetime.date(2023, 4, 23)
    assert invoice.date_debit is None
    assert invoice.date_invoicing is None
    assert invoice.regie == regie
    assert invoice.payer_external_id == 'payer:1'
    assert invoice.payer_first_name == 'First'
    assert invoice.payer_last_name == 'Last'
    assert invoice.payer_address == '41 rue des kangourous\n99999 Kangourou Ville'
    assert invoice.payer_email == 'email1'
    assert invoice.payer_phone == 'phone1'
    assert invoice.payer_direct_debit is False
    assert invoice.pool is None
    assert invoice.payment_callback_url == 'http://payment.com'
    assert invoice.cancel_callback_url == 'http://cancel.com'
    assert invoice.previous_invoice is None
    assert invoice.origin == 'api'
    assert invoice.lines.count() == 0

    previous_invoice = Invoice.objects.create(
        label='My invoice',
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=now().date(),
        date_due=datetime.date(2022, 10, 31),
        regie=other_regie,
        payer_external_id='payer:1',
        payer_first_name='Foo',
        payer_last_name='Bar',
        payer_address='41 rue des kangourous\n99999 Kangourou Ville',
    )

    params['previous_invoice'] = uuid.uuid4()
    params['date_invoicing'] = '2022-11-06'
    params['date_payment_deadline_displayed'] = '2023-04-15'
    resp = app.post('/api/regie/foo/draft-invoices/', params=params, status=400)
    assert resp.json['err']
    assert resp.json['errors'] == {
        'previous_invoice': ['Unknown invoice.'],
    }

    params['previous_invoice'] = previous_invoice.uuid
    resp = app.post('/api/regie/foo/draft-invoices/', params=params, status=400)
    assert resp.json['err']
    assert resp.json['errors'] == {
        'previous_invoice': ['Unknown invoice.'],
    }

    previous_invoice.regie = regie
    previous_invoice.save()
    resp = app.post('/api/regie/foo/draft-invoices/', params=params)
    assert resp.json['err'] == 0
    invoice = DraftInvoice.objects.latest('pk')
    assert invoice.previous_invoice == previous_invoice
    assert invoice.date_invoicing == datetime.date(2022, 11, 6)
    assert invoice.date_payment_deadline_displayed == datetime.date(2023, 4, 15)


def test_add_draft_invoice_line(app, user):
    app.post('/api/regie/foo/draft-invoice/%s/lines/' % str(uuid.uuid4()), status=403)
    app.authorization = ('Basic', ('john.doe', 'password'))

    app.post('/api/regie/foo/draft-invoice/%s/lines/' % str(uuid.uuid4()), status=404)

    regie = Regie.objects.create(label='Foo')
    app.post('/api/regie/foo/draft-invoice/%s/lines/' % str(uuid.uuid4()), status=404)

    invoice = DraftInvoice.objects.create(
        date_publication=datetime.date(2023, 4, 21),
        date_payment_deadline=datetime.date(2023, 4, 22),
        date_due=datetime.date(2023, 4, 23),
        regie=regie,
        payer_external_id=random.choice(['payer:1', 'payer:2']),
        payer_first_name=random.choice(['First', 'Fiirst']),
        payer_last_name=random.choice(['Last', 'Laast']),
        payer_address=random.choice(
            [
                '41 rue des kangourous\n99999 Kangourou Ville',
                '42 rue des kangourous\n99999 Kangourou Ville',
            ]
        ),
        payer_direct_debit=random.choice([True, False]),
    )

    app.post('/api/regie/fooooo/draft-invoice/%s/lines/' % str(invoice.uuid), status=404)

    resp = app.post('/api/regie/foo/draft-invoice/%s/lines/' % str(invoice.uuid), status=400)
    assert resp.json['err']
    assert resp.json['errors'] == {
        'event_date': ['This field is required.'],
        'label': ['This field is required.'],
        'quantity': ['This field is required.'],
        'slug': ['This field is required.'],
        'unit_amount': ['This field is required.'],
        'user_external_id': ['This field is required.'],
        'user_first_name': ['This field is required.'],
        'user_last_name': ['This field is required.'],
    }

    params = {
        'event_date': '2023-04-21',
        'label': 'Bar Foo',
        'quantity': '2',
        'slug': 'bar-foo',
        'unit_amount': '21',
        'user_external_id': 'user:1',
        'user_first_name': 'First1',
        'user_last_name': 'Last1',
        'form_url': 'http://form.com/1/',
    }
    resp = app.post('/api/regie/foo/draft-invoice/%s/lines/' % str(invoice.uuid), params=params)
    assert resp.json['err'] == 0
    line1 = DraftInvoiceLine.objects.latest('pk')
    assert resp.json['data'] == {'draft_line_id': line1.pk}
    assert line1.invoice == invoice
    assert line1.event_date == datetime.date(2023, 4, 21)
    assert line1.label == 'Bar Foo'
    assert line1.quantity == 2
    assert line1.unit_amount == 21
    assert line1.total_amount == 42
    assert line1.accounting_code == ''
    assert line1.user_external_id == 'user:1'
    assert line1.user_first_name == 'First1'
    assert line1.user_last_name == 'Last1'
    assert line1.details == {'dates': ['2023-04-21']}
    assert line1.event_slug == 'bar-foo'
    assert line1.agenda_slug == ''
    assert line1.activity_label == ''
    assert line1.description == ''
    assert line1.form_url == 'http://form.com/1/'
    assert line1.pool is None
    invoice.refresh_from_db()
    assert invoice.total_amount == 42

    params = {
        'event_date': '2023-04-21',
        'label': 'Bar Foo',
        'quantity': '2',
        'slug': 'agenda@bar-foo',
        'activity_label': 'Activity Label !',
        'description': 'A description !',
        'unit_amount': '21',
        'accounting_code': '424242',
        'user_external_id': 'user:1',
        'user_first_name': 'First1',
        'user_last_name': 'Last1',
        'form_url': 'http://form.com/2/',
    }
    resp = app.post('/api/regie/foo/draft-invoice/%s/lines/' % str(invoice.uuid), params=params)
    assert resp.json['err'] == 0
    line2 = DraftInvoiceLine.objects.latest('pk')
    assert resp.json['data'] == {'draft_line_id': line2.pk}
    assert line2.invoice == invoice
    assert line2.event_date == datetime.date(2023, 4, 21)
    assert line2.label == 'Bar Foo'
    assert line2.quantity == 2
    assert line2.unit_amount == 21
    assert line2.total_amount == 42
    assert line2.accounting_code == '424242'
    assert line2.user_external_id == 'user:1'
    assert line2.user_first_name == 'First1'
    assert line2.user_last_name == 'Last1'
    assert line2.details == {'dates': ['2023-04-21']}
    assert line2.event_slug == 'agenda@bar-foo'
    assert line2.event_label == 'Bar Foo'
    assert line2.agenda_slug == 'agenda'
    assert line2.activity_label == 'Activity Label !'
    assert line2.description == 'A description !'
    assert line2.form_url == 'http://form.com/2/'
    assert line2.pool is None
    invoice.refresh_from_db()
    assert invoice.total_amount == 84

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
    invoice.pool = pool
    invoice.save()
    app.post('/api/regie/foo/draft-invoice/%s/lines/' % str(invoice.uuid), status=404)

    invoice.pool = None
    invoice.save()

    # test merge feature

    # missing agenda_slug, event_slug, subject
    params = {
        'event_date': '2023-04-21',
        'label': 'Bar Foo',
        'quantity': '2',
        'slug': 'bar-foo',
        'unit_amount': '21',
        'user_external_id': 'user:1',
        'user_first_name': 'First1',
        'user_last_name': 'Last1',
        'form_url': 'http://form.com/3/',
    }
    resp = app.post('/api/regie/foo/draft-invoice/%s/lines/' % str(invoice.uuid), params=params)
    assert resp.json['err'] == 0
    line3 = DraftInvoiceLine.objects.latest('pk')
    assert line3.form_url == 'http://form.com/3/'
    assert resp.json['data'] == {'draft_line_id': line3.pk}
    invoice.refresh_from_db()
    assert invoice.total_amount == 126

    # same params, but no subject
    params = {
        'event_date': '2023-04-21',
        'label': 'Bar Foo',
        'quantity': '1',
        'slug': 'agenda@bar-foo',
        'activity_label': 'Activity Label !',
        'description': 'A new description !',
        'unit_amount': '21',
        'accounting_code': '424242',
        'user_external_id': 'user:1',
        'user_first_name': 'First1',
        'user_last_name': 'Last1',
        'form_url': 'http://form.com/4/',
        'merge_lines': True,
    }
    resp = app.post('/api/regie/foo/draft-invoice/%s/lines/' % str(invoice.uuid), params=params)
    line4 = DraftInvoiceLine.objects.latest('pk')
    assert resp.json['data'] == {'draft_line_id': line4.pk}
    assert line4.invoice == invoice
    assert line4.event_date == datetime.date(2023, 4, 21)
    assert line4.label == 'Bar Foo'
    assert line4.quantity == 1
    assert line4.unit_amount == 21
    assert line4.total_amount == 21
    assert line4.accounting_code == '424242'
    assert line4.user_external_id == 'user:1'
    assert line4.user_first_name == 'First1'
    assert line4.user_last_name == 'Last1'
    assert line4.details == {'dates': ['2023-04-21']}
    assert line4.event_slug == 'agenda@bar-foo'
    assert line4.event_label == 'Bar Foo'
    assert line4.agenda_slug == 'agenda'
    assert line4.activity_label == 'Activity Label !'
    assert line4.form_url == 'http://form.com/4/'
    assert line4.description == 'A new description !'
    assert line4.pool is None
    invoice.refresh_from_db()
    assert invoice.total_amount == 147

    # same params with subject
    params = {
        'event_date': '2023-04-21',
        'label': 'Bar Foo',
        'quantity': '1',
        'slug': 'agenda@bar-foo',
        'activity_label': 'Activity Label !',
        'description': 'Another description !',
        'unit_amount': '21',
        'accounting_code': '424242',
        'user_external_id': 'user:1',
        'user_first_name': 'First1',
        'user_last_name': 'Last1',
        'form_url': 'http://form.com/5/',
        'merge_lines': True,
        'subject': 'FooBar',
    }
    resp = app.post('/api/regie/foo/draft-invoice/%s/lines/' % str(invoice.uuid), params=params)
    line5 = DraftInvoiceLine.objects.latest('pk')
    assert resp.json['data'] == {'draft_line_id': line5.pk}
    assert line5.invoice == invoice
    assert line5.event_date == datetime.date(2023, 4, 21)
    assert line5.label == 'Bar Foo'
    assert line5.quantity == 1
    assert line5.unit_amount == 21
    assert line5.total_amount == 21
    assert line5.accounting_code == '424242'
    assert line5.user_external_id == 'user:1'
    assert line5.user_first_name == 'First1'
    assert line5.user_last_name == 'Last1'
    assert line5.details == {'dates': ['2023-04-21']}
    assert line5.event_slug == 'agenda@bar-foo'
    assert line5.event_label == 'Bar Foo'
    assert line5.agenda_slug == 'agenda'
    assert line5.activity_label == 'Activity Label !'
    assert line5.description == 'FooBar Another description !'
    assert line5.form_url == 'http://form.com/5/'
    assert line5.pool is None
    invoice.refresh_from_db()
    assert invoice.total_amount == 168

    # again
    params['description'] = 'Again !'
    params['event_date'] = '2023-04-22'
    resp = app.post('/api/regie/foo/draft-invoice/%s/lines/' % str(invoice.uuid), params=params)
    line5.refresh_from_db()
    assert resp.json['data'] == {'draft_line_id': line5.pk}
    assert line5.event_date == datetime.date(2023, 4, 21)
    assert line5.quantity == 2
    assert line5.unit_amount == 21
    assert line5.total_amount == 42
    assert line5.description == 'FooBar Another description !, Again !'
    assert line5.form_url == 'http://form.com/5/'
    assert line5.details == {'dates': ['2023-04-21', '2023-04-22']}
    invoice.refresh_from_db()
    assert invoice.total_amount == 189

    # change params
    params['event_date'] = '2023-04-21'
    values = [
        ('label', 'Bar Fooo'),
        ('slug', 'agendaa@bar-foo'),  # change agenda
        ('slug', 'agenda@bar-fooo'),  # change event
        ('activity_label', ''),
        ('activity_label', 'Activity Label !!'),
        ('unit_amount', 20),
        ('accounting_code', ''),
        ('accounting_code', '424243'),
        ('user_external_id', 'user:2'),
        ('form_url', 'http://form.com/xx/'),
    ]
    for key, value in values:
        new_params = params.copy()
        new_params[key] = value
        resp = app.post('/api/regie/foo/draft-invoice/%s/lines/' % str(invoice.uuid), params=new_params)
        assert resp.json['err'] == 0
        line = DraftInvoiceLine.objects.latest('pk')
        assert resp.json['data'] == {'draft_line_id': line.pk}
        assert line.invoice == invoice
        assert line.event_date == datetime.date(2023, 4, 21)
        assert line.label == ('Bar Foo' if key != 'label' else value)
        assert line.quantity == 1
        assert line.unit_amount == (21 if key != 'unit_amount' else value)
        assert line.accounting_code == ('424242' if key != 'accounting_code' else value)
        assert line.user_external_id == ('user:1' if key != 'user_external_id' else value)
        assert line.user_first_name == 'First1'
        assert line.user_last_name == 'Last1'
        assert line.details == {'dates': ['2023-04-21']}
        assert line.event_slug == ('agenda@bar-foo' if key != 'slug' else value)
        assert line.event_label == ('Bar Foo' if key != 'label' else value)
        assert line.agenda_slug == ('agenda' if key != 'slug' else value.split('@', maxsplit=1)[0])
        assert line.activity_label == ('Activity Label !' if key != 'activity_label' else value)
        assert line.description == 'FooBar Again !'
        assert line.form_url == ('http://form.com/5/' if key != 'form_url' else value)

    # change subject, other line
    params['subject'] = 'Other subject'
    del params['form_url']
    resp = app.post('/api/regie/foo/draft-invoice/%s/lines/' % str(invoice.uuid), params=params)
    line6 = DraftInvoiceLine.objects.latest('pk')
    assert resp.json['data'] == {'draft_line_id': line6.pk}
    assert line6.invoice == invoice
    assert line6.event_date == datetime.date(2023, 4, 21)
    assert line6.label == 'Bar Foo'
    assert line6.quantity == 1
    assert line6.unit_amount == 21
    assert line6.total_amount == 21
    assert line6.accounting_code == '424242'
    assert line6.user_external_id == 'user:1'
    assert line6.user_first_name == 'First1'
    assert line6.user_last_name == 'Last1'
    assert line6.details == {'dates': ['2023-04-21']}
    assert line6.event_slug == 'agenda@bar-foo'
    assert line6.event_label == 'Bar Foo'
    assert line6.agenda_slug == 'agenda'
    assert line6.activity_label == 'Activity Label !'
    assert line6.description == 'Other subject Again !'
    assert line6.form_url == ''
    assert line6.pool is None


def test_close_draft_invoice(app, user):
    app.post('/api/regie/foo/draft-invoice/%s/close/' % str(uuid.uuid4()), status=403)
    app.authorization = ('Basic', ('john.doe', 'password'))

    app.post('/api/regie/foo/draft-invoice/%s/close/' % str(uuid.uuid4()), status=404)

    regie = Regie.objects.create(label='Foo')
    app.post('/api/regie/foo/draft-invoice/%s/close/' % str(uuid.uuid4()), status=404)

    previous_invoice = Invoice.objects.create(
        label='My invoice',
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=now().date(),
        date_due=datetime.date(2022, 10, 31),
        regie=regie,
        payer_external_id='payer:1',
        payer_first_name='Foo',
        payer_last_name='Bar',
        payer_address='41 rue des kangourous\n99999 Kangourou Ville',
    )
    invoice = DraftInvoice.objects.create(
        date_publication=datetime.date(2023, 4, 21),
        date_payment_deadline=datetime.date(2023, 4, 22),
        date_due=datetime.date(2023, 4, 23),
        date_invoicing=datetime.date(2022, 11, 6),
        regie=regie,
        label='Foo Bar',
        payer_external_id=random.choice(['payer:1', 'payer:2']),
        payer_first_name=random.choice(['First', 'Fiirst']),
        payer_last_name=random.choice(['Last', 'Laast']),
        payer_address=random.choice(
            [
                '41 rue des kangourous\n99999 Kangourou Ville',
                '42 rue des kangourous\n99999 Kangourou Ville',
            ]
        ),
        payer_email=random.choice(['email1', 'email2']),
        payer_phone=random.choice(['phone1', 'phone2']),
        payer_direct_debit=random.choice([True, False]),
        payment_callback_url='http://payment.com',
        cancel_callback_url='http://cancel.com',
        previous_invoice=previous_invoice,
        origin='api',
    )
    line = DraftInvoiceLine.objects.create(
        event_date=datetime.date(2023, 4, 21),
        quantity=-1,
        unit_amount=42,
        invoice=invoice,
        form_url='http://form.com',
    )
    line.refresh_from_db()
    invoice.refresh_from_db()

    app.post('/api/regie/fooooo/draft-invoice/%s/close/' % str(invoice.uuid), status=404)

    resp = app.post('/api/regie/foo/draft-invoice/%s/close/' % str(invoice.uuid), status=400)
    assert resp.json['err'] == 1
    assert resp.json['err_class'] == 'can not create invoice from draft invoice with negative amount'
    line.quantity = 1
    line.save()
    line.refresh_from_db()
    invoice.refresh_from_db()

    resp = app.post('/api/regie/foo/draft-invoice/%s/close/' % str(invoice.uuid))
    assert resp.json['err'] == 0
    assert Credit.objects.count() == 0
    assert Invoice.objects.count() == 2
    final_invoice = Invoice.objects.latest('pk')
    assert resp.json['data'] == {
        'invoice_id': str(final_invoice.uuid),
        'invoice': {
            'id': str(final_invoice.uuid),
            'total_amount': 42,
            'remaining_amount': 42,
        },
        'urls': {
            'invoice_in_backoffice': 'http://testserver/manage/invoicing/redirect/invoice/%s/'
            % final_invoice.uuid,
            'invoice_pdf': 'http://testserver/manage/invoicing/redirect/invoice/%s/pdf/' % final_invoice.uuid,
        },
        'api_urls': {
            'invoice_pdf': 'http://testserver/api/regie/foo/invoice/%s/pdf/' % final_invoice.uuid,
        },
    }
    assert final_invoice.date_publication == invoice.date_publication
    assert final_invoice.date_payment_deadline == invoice.date_payment_deadline
    assert final_invoice.date_due == invoice.date_due
    assert final_invoice.date_debit == invoice.date_debit
    assert final_invoice.date_invoicing == invoice.date_invoicing
    assert final_invoice.regie == regie
    assert final_invoice.pool is None
    assert final_invoice.label == 'Foo Bar'
    assert final_invoice.total_amount == invoice.total_amount == 42
    assert final_invoice.number == 1
    assert final_invoice.formatted_number == 'F%02d-22-11-0000001' % regie.pk
    assert final_invoice.payment_callback_url == 'http://payment.com'
    assert final_invoice.cancel_callback_url == 'http://cancel.com'
    assert final_invoice.previous_invoice == previous_invoice
    assert final_invoice.origin == 'api'

    final_line = InvoiceLine.objects.order_by('pk')[0]
    assert final_line.event_date == line.event_date
    assert final_line.label == line.label
    assert final_line.quantity == line.quantity
    assert final_line.unit_amount == line.unit_amount
    assert final_line.total_amount == line.total_amount
    assert final_line.user_external_id == line.user_external_id
    assert final_line.user_first_name == line.user_first_name
    assert final_line.user_last_name == line.user_last_name
    assert final_line.details == line.details
    assert final_line.form_url == line.form_url
    assert final_line.pool is None
    assert final_line.invoice == final_invoice

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
    invoice.pool = pool
    invoice.save()
    app.post('/api/regie/foo/draft-invoice/%s/close/' % str(invoice.uuid), status=404)


def test_close_draft_invoice_with_credits(app, user):
    app.authorization = ('Basic', ('john.doe', 'password'))

    regie = Regie.objects.create(label='Foo')
    other_regie = Regie.objects.create(label='Other Foo')
    invoice = DraftInvoice.objects.create(
        date_publication=datetime.date(2023, 4, 21),
        date_payment_deadline=datetime.date(2023, 4, 22),
        date_due=now().date(),
        regie=regie,
        payer_external_id='payer:1',
        payer_first_name='First',
        payer_last_name='Last',
        payer_address='41 rue des kangourous\n99999 Kangourou Ville',
        payment_callback_url='http://payment.com',
    )
    line = DraftInvoiceLine.objects.create(
        event_date=datetime.date(2023, 4, 21),
        quantity=1,
        unit_amount=42,
        invoice=invoice,
    )
    line.refresh_from_db()
    invoice.refresh_from_db()

    credit1 = Credit.objects.create(
        label='Credit from 01/09/2022',
        date_publication=datetime.date(2022, 10, 1),
        regie=regie,
        payer_external_id='payer:1',
    )
    CreditLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        credit=credit1,
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
    credit2 = Credit.objects.create(
        label='Credit from 01/09/2022',
        date_publication=datetime.date(2022, 10, 1),
        regie=regie,
        payer_external_id='payer:1',
        pool=pool,
    )
    CreditLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        credit=credit2,
        quantity=5,
        unit_amount=1,
    )
    credit3 = Credit.objects.create(
        label='Credit from 01/09/2022',
        date_publication=datetime.date(2022, 10, 1),
        regie=regie,
        payer_external_id='payer:42',  # wrong payer
    )
    CreditLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        credit=credit3,
        quantity=5,
        unit_amount=1,
    )
    other_credit = Credit.objects.create(
        label='Credit from 01/09/2022',
        date_publication=datetime.date(2022, 10, 1),
        regie=other_regie,  # other regie
        payer_external_id='payer:1',
    )
    CreditLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        credit=other_credit,
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
    other_credit = Credit.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        regie=regie,
        payer_external_id='payer:1',
        pool=pool,  # not finalized pool
    )
    CreditLine.objects.create(
        credit=other_credit,
        event_date=datetime.date(2022, 9, 1),
        quantity=5,
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
        quantity=5,
        unit_amount=1,
    )
    other_credit = Credit.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        regie=regie,
        payer_external_id='payer:1',
        usable=False,  # not usable to pay invoices
    )
    CreditLine.objects.create(
        credit=other_credit,
        event_date=datetime.date(2022, 9, 1),
        quantity=5,
        unit_amount=1,
    )

    with mock.patch('lingo.utils.requests_wrapper.RequestsSession.send') as mock_send:
        resp = app.post('/api/regie/foo/draft-invoice/%s/close/' % str(invoice.uuid))
    assert [x[0][0].url for x in mock_send.call_args_list] == []
    assert resp.json['err'] == 0
    final_invoice = Invoice.objects.latest('pk')
    assert final_invoice.total_amount == 42
    assert final_invoice.paid_amount == 10
    assert final_invoice.remaining_amount == 32
    credit1.refresh_from_db()
    assert credit1.remaining_amount == 0
    assert credit1.assigned_amount == 5
    credit2.refresh_from_db()
    assert credit2.remaining_amount == 0
    assert credit2.assigned_amount == 5
    assert Payment.objects.count() == 2
    assert CreditAssignment.objects.count() == 2
    assignment1, assignment2 = CreditAssignment.objects.all().order_by('pk')
    assert assignment1.amount == 5
    assert assignment1.invoice == final_invoice
    assert assignment1.credit == credit1
    assert assignment2.amount == 5
    assert assignment2.invoice == final_invoice
    assert assignment2.credit == credit2
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
    assert invoicelinepayment11.line == final_invoice.lines.get()
    assert invoicelinepayment11.amount == 5
    assert payment2.invoicelinepayment_set.count() == 1
    (invoicelinepayment21,) = payment2.invoicelinepayment_set.order_by('pk')
    assert invoicelinepayment21.line == final_invoice.lines.get()
    assert invoicelinepayment21.amount == 5

    invoice = DraftInvoice.objects.create(
        date_publication=datetime.date(2023, 4, 21),
        date_payment_deadline=datetime.date(2023, 4, 22),
        date_due=now().date(),
        regie=regie,
        payer_external_id='payer:1',
        payer_first_name='First',
        payer_last_name='Last',
        payer_address='41 rue des kangourous\n99999 Kangourou Ville',
        payment_callback_url='http://payment.com',
    )
    line = DraftInvoiceLine.objects.create(
        event_date=datetime.date(2023, 4, 21),
        quantity=1,
        unit_amount=42,
        invoice=invoice,
    )
    line.refresh_from_db()
    invoice.refresh_from_db()

    credit1 = Credit.objects.create(
        label='Credit from 01/09/2022',
        date_publication=datetime.date(2022, 10, 1),
        regie=regie,
        payer_external_id='payer:1',
    )
    CreditLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        credit=credit1,
        quantity=43,
        unit_amount=1,
    )

    # more credit amount than invoice amount to pay
    with mock.patch('lingo.utils.requests_wrapper.RequestsSession.send') as mock_send:
        resp = app.post('/api/regie/foo/draft-invoice/%s/close/' % str(invoice.uuid))
    assert [x[0][0].url for x in mock_send.call_args_list] == ['http://payment.com/']
    assert resp.json['err'] == 0
    final_invoice = Invoice.objects.latest('pk')
    assert final_invoice.total_amount == 42
    assert final_invoice.paid_amount == 42
    assert final_invoice.remaining_amount == 0
    credit1.refresh_from_db()
    assert credit1.remaining_amount == 1
    assert credit1.assigned_amount == 42

    # date_due is past
    invoice = DraftInvoice.objects.create(
        date_publication=datetime.date(2023, 4, 21),
        date_payment_deadline=datetime.date(2023, 4, 22),
        date_due=now().date() - datetime.timedelta(days=1),
        regie=regie,
        payer_external_id='payer:1',
        payer_first_name='First',
        payer_last_name='Last',
        payer_address='41 rue des kangourous\n99999 Kangourou Ville',
        payment_callback_url='http://payment.com',
    )
    line = DraftInvoiceLine.objects.create(
        event_date=datetime.date(2023, 4, 21),
        quantity=1,
        unit_amount=42,
        invoice=invoice,
    )
    line.refresh_from_db()
    invoice.refresh_from_db()

    credit1 = Credit.objects.create(
        label='Credit from 01/09/2022',
        date_publication=datetime.date(2022, 10, 1),
        regie=regie,
        payer_external_id='payer:1',
    )
    CreditLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        credit=credit1,
        quantity=43,
        unit_amount=1,
    )

    resp = app.post('/api/regie/foo/draft-invoice/%s/close/' % str(invoice.uuid))
    assert resp.json['err'] == 0
    final_invoice = Invoice.objects.latest('pk')
    assert final_invoice.total_amount == 42
    assert final_invoice.paid_amount == 0
    assert final_invoice.remaining_amount == 42
