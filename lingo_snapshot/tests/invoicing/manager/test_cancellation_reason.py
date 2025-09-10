import datetime

import pytest

from lingo.invoicing.models import (
    Credit,
    CreditCancellationReason,
    Invoice,
    InvoiceCancellationReason,
    Payment,
    PaymentCancellationReason,
    PaymentType,
    Regie,
)
from tests.utils import login

pytestmark = pytest.mark.django_db


def test_add_invoice_reason(app, admin_user):
    app = login(app)
    resp = app.get('/manage/invoicing/regies/')
    resp = resp.click('Cancellation reasons')
    resp = resp.click('New invoice cancellation reason')
    resp.form['label'] = 'Foo bar'
    assert 'slug' not in resp.context['form'].fields
    assert 'disabled' not in resp.context['form'].fields
    resp = resp.form.submit()
    invoice_reason = InvoiceCancellationReason.objects.latest('pk')
    assert resp.location.endswith('/manage/invoicing/cancellation-reasons/#open:invoice')
    assert invoice_reason.label == 'Foo bar'
    assert invoice_reason.slug == 'foo-bar'
    assert invoice_reason.disabled is False

    resp = app.get('/manage/invoicing/cancellation-reason/invoice/add/')
    resp.form['label'] = 'Foo bar'
    resp = resp.form.submit()
    invoice_reason = InvoiceCancellationReason.objects.latest('pk')
    assert resp.location.endswith('/manage/invoicing/cancellation-reasons/#open:invoice')
    assert invoice_reason.label == 'Foo bar'
    assert invoice_reason.slug == 'foo-bar-1'
    assert invoice_reason.disabled is False


def test_edit_invoice_reason(app, admin_user):
    invoice_reason = InvoiceCancellationReason.objects.create(label='Foo')
    invoice_reason2 = InvoiceCancellationReason.objects.create(label='Baz')

    app = login(app)
    resp = app.get('/manage/invoicing/cancellation-reasons/')
    resp = resp.click(href='/manage/invoicing/cancellation-reason/invoice/%s/edit/' % (invoice_reason.pk))
    resp.form['label'] = 'Foo bar'
    resp.form['slug'] = invoice_reason2.slug
    resp.form['disabled'] = True
    resp = resp.form.submit()
    assert resp.context['form'].errors['slug'] == [
        'Another invoice cancellation reason exists with the same identifier.'
    ]

    resp.form['slug'] = 'foo-bar'
    resp = resp.form.submit()
    assert resp.location.endswith('/manage/invoicing/cancellation-reasons/#open:invoice')
    invoice_reason.refresh_from_db()
    assert invoice_reason.label == 'Foo bar'
    assert invoice_reason.slug == 'foo-bar'
    assert invoice_reason.disabled is True


def test_delete_invoice_reason(app, admin_user):
    regie = Regie.objects.create(label='Foo')
    invoice_reason = InvoiceCancellationReason.objects.create(label='Foo')
    invoice = Invoice.objects.create(
        regie=regie,
        date_publication=datetime.date(2023, 4, 21),
        date_payment_deadline=datetime.date(2023, 4, 22),
        date_due=datetime.date(2023, 4, 23),
        cancellation_reason=invoice_reason,
    )

    app = login(app)
    resp = app.get('/manage/invoicing/cancellation-reasons/')
    assert '/manage/invoicing/cancellation-reason/invoice/%s/delete/' % invoice_reason.pk not in resp
    app.get('/manage/invoicing/cancellation-reason/invoice/%s/delete/' % invoice_reason.pk, status=404)

    invoice.delete()

    resp = app.get('/manage/invoicing/cancellation-reasons/')
    resp = resp.click(href='/manage/invoicing/cancellation-reason/invoice/%s/delete/' % invoice_reason.pk)
    resp = resp.form.submit()
    assert resp.location.endswith('/manage/invoicing/cancellation-reasons/#open:invoice')


def test_add_credit_reason(app, admin_user):
    app = login(app)
    resp = app.get('/manage/invoicing/regies/')
    resp = resp.click('Cancellation reasons')
    resp = resp.click('New credit cancellation reason')
    resp.form['label'] = 'Foo bar'
    assert 'slug' not in resp.context['form'].fields
    assert 'disabled' not in resp.context['form'].fields
    resp = resp.form.submit()
    credit_reason = CreditCancellationReason.objects.latest('pk')
    assert resp.location.endswith('/manage/invoicing/cancellation-reasons/#open:credit')
    assert credit_reason.label == 'Foo bar'
    assert credit_reason.slug == 'foo-bar'
    assert credit_reason.disabled is False

    resp = app.get('/manage/invoicing/cancellation-reason/credit/add/')
    resp.form['label'] = 'Foo bar'
    resp = resp.form.submit()
    credit_reason = CreditCancellationReason.objects.latest('pk')
    assert resp.location.endswith('/manage/invoicing/cancellation-reasons/#open:credit')
    assert credit_reason.label == 'Foo bar'
    assert credit_reason.slug == 'foo-bar-1'
    assert credit_reason.disabled is False


def test_edit_credit_reason(app, admin_user):
    credit_reason = CreditCancellationReason.objects.create(label='Foo')
    credit_reason2 = CreditCancellationReason.objects.create(label='Baz')

    app = login(app)
    resp = app.get('/manage/invoicing/cancellation-reasons/')
    resp = resp.click(href='/manage/invoicing/cancellation-reason/credit/%s/edit/' % (credit_reason.pk))
    resp.form['label'] = 'Foo bar'
    resp.form['slug'] = credit_reason2.slug
    resp.form['disabled'] = True
    resp = resp.form.submit()
    assert resp.context['form'].errors['slug'] == [
        'Another credit cancellation reason exists with the same identifier.'
    ]

    resp.form['slug'] = 'foo-bar'
    resp = resp.form.submit()
    assert resp.location.endswith('/manage/invoicing/cancellation-reasons/#open:credit')
    credit_reason.refresh_from_db()
    assert credit_reason.label == 'Foo bar'
    assert credit_reason.slug == 'foo-bar'
    assert credit_reason.disabled is True


def test_delete_credit_reason(app, admin_user):
    regie = Regie.objects.create(label='Foo')
    credit_reason = CreditCancellationReason.objects.create(label='Foo')
    credit = Credit.objects.create(
        regie=regie,
        date_publication=datetime.date(2023, 4, 21),
        cancellation_reason=credit_reason,
    )

    app = login(app)
    resp = app.get('/manage/invoicing/cancellation-reasons/')
    assert '/manage/invoicing/cancellation-reason/credit/%s/delete/' % credit_reason.pk not in resp
    app.get('/manage/invoicing/cancellation-reason/credit/%s/delete/' % credit_reason.pk, status=404)

    credit.delete()

    resp = app.get('/manage/invoicing/cancellation-reasons/')
    resp = resp.click(href='/manage/invoicing/cancellation-reason/credit/%s/delete/' % credit_reason.pk)
    resp = resp.form.submit()
    assert resp.location.endswith('/manage/invoicing/cancellation-reasons/#open:credit')


def test_add_payment_reason(app, admin_user):
    app = login(app)
    resp = app.get('/manage/invoicing/regies/')
    resp = resp.click('Cancellation reasons')
    resp = resp.click('New payment cancellation reason')
    resp.form['label'] = 'Foo bar'
    assert 'slug' not in resp.context['form'].fields
    assert 'disabled' not in resp.context['form'].fields
    resp = resp.form.submit()
    payment_reason = PaymentCancellationReason.objects.latest('pk')
    assert resp.location.endswith('/manage/invoicing/cancellation-reasons/#open:payment')
    assert payment_reason.label == 'Foo bar'
    assert payment_reason.slug == 'foo-bar'
    assert payment_reason.disabled is False

    resp = app.get('/manage/invoicing/cancellation-reason/payment/add/')
    resp.form['label'] = 'Foo bar'
    resp = resp.form.submit()
    payment_reason = PaymentCancellationReason.objects.latest('pk')
    assert resp.location.endswith('/manage/invoicing/cancellation-reasons/#open:payment')
    assert payment_reason.label == 'Foo bar'
    assert payment_reason.slug == 'foo-bar-1'
    assert payment_reason.disabled is False


def test_edit_payment_reason(app, admin_user):
    payment_reason = PaymentCancellationReason.objects.create(label='Foo')
    payment_reason2 = PaymentCancellationReason.objects.create(label='Baz')

    app = login(app)
    resp = app.get('/manage/invoicing/cancellation-reasons/')
    resp = resp.click(href='/manage/invoicing/cancellation-reason/payment/%s/edit/' % (payment_reason.pk))
    resp.form['label'] = 'Foo bar'
    resp.form['slug'] = payment_reason2.slug
    resp.form['disabled'] = True
    resp = resp.form.submit()
    assert resp.context['form'].errors['slug'] == [
        'Another payment cancellation reason exists with the same identifier.'
    ]

    resp.form['slug'] = 'foo-bar'
    resp = resp.form.submit()
    assert resp.location.endswith('/manage/invoicing/cancellation-reasons/#open:payment')
    payment_reason.refresh_from_db()
    assert payment_reason.label == 'Foo bar'
    assert payment_reason.slug == 'foo-bar'
    assert payment_reason.disabled is True


def test_delete_payment_reason(app, admin_user):
    regie = Regie.objects.create(label='Foo')
    payment_reason = PaymentCancellationReason.objects.create(label='Foo')
    payment = Payment.objects.create(
        regie=regie,
        amount=1,
        payment_type=PaymentType.objects.create(label='Foo', regie=regie),
        cancellation_reason=payment_reason,
    )

    app = login(app)
    resp = app.get('/manage/invoicing/cancellation-reasons/')
    assert '/manage/invoicing/cancellation-reason/payment/%s/delete/' % payment_reason.pk not in resp
    app.get('/manage/invoicing/cancellation-reason/payment/%s/delete/' % payment_reason.pk, status=404)

    payment.delete()

    resp = app.get('/manage/invoicing/cancellation-reasons/')
    resp = resp.click(href='/manage/invoicing/cancellation-reason/payment/%s/delete/' % payment_reason.pk)
    resp = resp.form.submit()
    assert resp.location.endswith('/manage/invoicing/cancellation-reasons/#open:payment')
    assert PaymentCancellationReason.objects.exists() is False
    assert PaymentCancellationReason.objects.exists() is False
