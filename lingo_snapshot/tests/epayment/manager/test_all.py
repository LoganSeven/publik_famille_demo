import datetime

import eopayment
import pytest
from django.contrib.auth.models import Group
from django.urls import reverse

from lingo.epayment.models import PaymentBackend, Transaction
from lingo.invoicing.models import Invoice, InvoiceLine, InvoiceLinePayment, Payment, PaymentType, Regie
from tests.utils import login

pytestmark = pytest.mark.django_db


def test_manager_epayment_transaction_list_empty(app, admin_user):
    app = login(app)
    resp = app.get(reverse('lingo-manager-epayment-transaction-list'))
    h2 = resp.pyquery('div#appbar h2')
    assert h2.text() == 'E-Payment'
    assert 'There are no transactions yet.'


def test_manager_epayment_transaction_list_status_filter(app, admin_user):
    regie = Regie.objects.create(label='Bar')

    Transaction.objects.create(
        order_id='1234', bank_transaction_id='2345', status=eopayment.WAITING, amount=20
    )
    Transaction.objects.create(order_id='abcd', bank_transaction_id='bcde', status=eopayment.PAID, amount=20)
    Transaction.objects.create(order_id='ABCD', bank_transaction_id='BCDE', status=eopayment.ERROR, amount=20)

    invoice = Invoice.objects.create(
        regie=regie,
        date_publication=datetime.date(2023, 4, 21),
        date_payment_deadline=datetime.date(2023, 4, 22),
        date_due=datetime.date(2023, 4, 23),
    )
    invoice.set_number()
    invoice.save()
    transaction = Transaction.objects.create(
        order_id='xxxx', bank_transaction_id='xxx', status=eopayment.PAID, amount=20, invoice=invoice
    )

    app = login(app)
    resp = app.get(reverse('lingo-manager-epayment-transaction-list'))
    assert resp.pyquery('tbody tr').length == 4

    resp.form['status'] = 'running'
    resp = resp.form.submit()
    assert resp.pyquery('tbody tr').length == 1
    assert '1234' in resp.pyquery('tbody tr').text()

    resp.form['status'] = 'paid'
    resp = resp.form.submit()
    assert resp.pyquery('tbody tr').length == 2
    assert 'abcd' in resp.pyquery('tbody tr').text()

    resp.form['status'] = 'others'
    resp = resp.form.submit()
    assert resp.pyquery('tbody tr').length == 1
    assert 'ABCD' in resp.pyquery('tbody tr').text()

    resp.form['status'] = ''
    resp = resp.form.submit()
    assert resp.pyquery('tbody tr').length == 4

    resp.form['invoice'] = invoice.formatted_number
    resp = resp.form.submit()
    assert resp.pyquery('tbody tr').length == 1
    resp = resp.click(invoice.formatted_number)
    assert resp.pyquery('.invoicing-element-list tr').length == 1

    resp = app.get(reverse('lingo-manager-epayment-transaction-list'))
    resp.form['payment'] = 'foo'
    resp = resp.form.submit()
    assert resp.pyquery('tbody tr').length == 0

    invoice_line = InvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        invoice=invoice,
        quantity=1,
        unit_amount=35,
    )
    PaymentType.create_defaults(regie)
    payment = Payment.objects.create(
        regie=regie,
        amount=35,
        payment_type=PaymentType.objects.get(regie=regie, slug='online'),
    )
    payment.set_number()
    payment.save()
    InvoiceLinePayment.objects.create(
        payment=payment,
        line=invoice_line,
        amount=35,
    )
    resp.form['payment'] = payment.formatted_number
    resp = resp.form.submit()
    assert resp.pyquery('tbody tr').length == 0

    payment.order_id = transaction.order_id
    payment.save()
    resp = resp.form.submit()
    assert resp.pyquery('tbody tr').length == 1
    resp = resp.click(payment.formatted_number)
    assert resp.pyquery('.invoicing-element-list tr.payment').length == 1


def test_manager_epayment_backend_list_empty(app, admin_user):
    app = login(app)
    resp = app.get(reverse('lingo-manager-epayment-transaction-list'))
    resp = resp.click('Configure payment backends')
    h2 = resp.pyquery('div#appbar h2')
    assert h2.text() == 'Payment backends'
    assert 'There are no payment backend yet.' in resp.text


def test_manager_epayment_backend_add(app, admin_user):
    regie = Regie.objects.create(label='Bar')
    app = login(app)
    resp = app.get(reverse('lingo-manager-epayment-backend-list'))
    resp = resp.click('New payment backend')
    resp.form.set('label', 'Test')
    resp.form.set('service', 'dummy')
    resp = resp.form.submit()
    backend = PaymentBackend.objects.all().first()
    assert resp.location.endswith('/manage/epayment/backend/%s/' % backend.pk)
    resp = resp.follow()
    assert 'Please fill additional backend parameters.' in resp.text
    resp = resp.click('Edit')
    resp.form.set('regie', regie.id)
    resp.form.set('origin', 'blah')
    resp = resp.form.submit()
    assert resp.location.endswith('/manage/epayment/backend/%s/' % backend.pk)
    resp = app.get(reverse('lingo-manager-epayment-backend-list'))
    assert resp.pyquery('.objects-list li').length == 1
    backend.refresh_from_db()
    assert backend.slug == 'test'
    assert backend.service_options['origin'] == 'blah'


def test_manager_epayment_backend_edit(app, admin_user):
    regie = Regie.objects.create(label='Bar')
    group_foo = Group.objects.create(name='role-foo')
    group_bar = Group.objects.create(name='role-bar')
    backend = PaymentBackend.objects.create(
        label='Test',
        slug='test',
        service='dummy',
        service_options={'origin': 'Blah'},
        regie=regie,
        edit_role=group_foo,
        view_role=group_foo,
    )
    app = login(app)
    resp = app.get(reverse('lingo-manager-epayment-backend-edit', kwargs={'pk': backend.pk}))
    resp.form.set('edit_role', group_bar.pk)
    resp.form.set('view_role', group_bar.pk)
    resp.form.set('origin', 'change')
    resp = resp.form.submit()
    assert resp.location.endswith('/manage/epayment/backend/%s/' % backend.pk)
    backend.refresh_from_db()
    assert backend.service_options['origin'] == 'change'
    assert backend.edit_role == group_bar
    assert backend.view_role == group_bar


def test_manager_epayment_backend_delete(app, admin_user):
    backend = PaymentBackend.objects.create(label='Test', slug='test', service='dummy')
    app = login(app)
    resp = app.get(reverse('lingo-manager-epayment-backend-delete', kwargs={'pk': backend.pk}))
    resp = resp.form.submit().follow()
    assert PaymentBackend.objects.all().count() == 0
