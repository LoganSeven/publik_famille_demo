import datetime
import uuid

import pytest

from lingo.agendas.models import Agenda
from lingo.invoicing.models import Credit, Invoice, Payment, PaymentType, Refund, Regie
from tests.utils import login

pytestmark = pytest.mark.django_db


def test_redirect_invoice(app, admin_user):
    app = login(app)
    app.get('/manage/invoicing/redirect/invoice/%s/' % uuid.uuid4(), status=404)

    regie = Regie.objects.create(
        label='Foo',
    )
    invoice = Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        regie=regie,
    )
    invoice.set_number()
    invoice.save()

    resp = app.get('/manage/invoicing/redirect/invoice/%s/' % invoice.uuid)
    assert resp.location.endswith(
        '/manage/invoicing/regie/%s/invoices/?number=%s' % (regie.pk, invoice.formatted_number)
    )
    resp.follow()


def test_redirect_invoice_pdf(app, admin_user):
    app = login(app)
    app.get('/manage/invoicing/redirect/invoice/%s/pdf/' % uuid.uuid4(), status=404)

    regie = Regie.objects.create(
        label='Foo',
    )
    invoice = Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        regie=regie,
    )
    invoice.set_number()
    invoice.save()

    resp = app.get('/manage/invoicing/redirect/invoice/%s/pdf/' % invoice.uuid)
    assert resp.location.endswith('/manage/invoicing/regie/%s/invoice/%s/pdf/' % (regie.pk, invoice.pk))
    resp.follow()


def test_redirect_credit(app, admin_user):
    app = login(app)
    app.get('/manage/invoicing/redirect/credit/%s/' % uuid.uuid4(), status=404)

    regie = Regie.objects.create(
        label='Foo',
    )
    credit = Credit.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        regie=regie,
    )
    credit.set_number()
    credit.save()

    resp = app.get('/manage/invoicing/redirect/credit/%s/' % credit.uuid)
    assert resp.location.endswith(
        '/manage/invoicing/regie/%s/credits/?number=%s' % (regie.pk, credit.formatted_number)
    )
    resp.follow()


def test_redirect_credit_pdf(app, admin_user):
    app = login(app)
    app.get('/manage/invoicing/redirect/credit/%s/pdf/' % uuid.uuid4(), status=404)

    regie = Regie.objects.create(
        label='Foo',
    )
    credit = Credit.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        regie=regie,
    )
    credit.set_number()
    credit.save()

    resp = app.get('/manage/invoicing/redirect/credit/%s/pdf/' % credit.uuid)
    assert resp.location.endswith('/manage/invoicing/regie/%s/credit/%s/pdf/' % (regie.pk, credit.pk))
    resp.follow()


def test_redirect_payment(app, admin_user):
    app = login(app)
    app.get('/manage/invoicing/redirect/payment/%s/' % uuid.uuid4(), status=404)

    regie = Regie.objects.create(
        label='Foo',
    )
    payment = Payment.objects.create(
        amount=42,
        payment_type=PaymentType.objects.create(regie=regie, label='foo'),
        regie=regie,
    )
    payment.set_number()
    payment.save()

    resp = app.get('/manage/invoicing/redirect/payment/%s/' % payment.uuid)
    assert resp.location.endswith(
        '/manage/invoicing/regie/%s/payments/?number=%s' % (regie.pk, payment.formatted_number)
    )
    resp.follow()


def test_redirect_payment_pdf(app, admin_user):
    app = login(app)
    app.get('/manage/invoicing/redirect/payment/%s/pdf/' % uuid.uuid4(), status=404)

    regie = Regie.objects.create(
        label='Foo',
    )
    payment = Payment.objects.create(
        amount=42,
        payment_type=PaymentType.objects.create(regie=regie, label='foo'),
        regie=regie,
    )
    payment.set_number()
    payment.save()

    resp = app.get('/manage/invoicing/redirect/payment/%s/pdf/' % payment.uuid)
    assert resp.location.endswith('/manage/invoicing/regie/%s/payment/%s/pdf/' % (regie.pk, payment.pk))
    resp.follow()


def test_redirect_refund(app, admin_user):
    app = login(app)
    app.get('/manage/invoicing/redirect/refund/%s/' % uuid.uuid4(), status=404)

    regie = Regie.objects.create(
        label='Foo',
    )
    refund = Refund.objects.create(
        amount=42,
        regie=regie,
    )
    refund.set_number()
    refund.save()

    resp = app.get('/manage/invoicing/redirect/refund/%s/' % refund.uuid)
    assert resp.location.endswith(
        '/manage/invoicing/regie/%s/refunds/?number=%s' % (regie.pk, refund.formatted_number)
    )
    resp.follow()


def test_redirect_transactions_for_event(app, admin_user):
    app = login(app)
    app.get('/manage/invoicing/redirect/transactions/', status=404)

    app.get('/manage/invoicing/redirect/transactions/', params={'event_slug': 'a'}, status=404)

    app.get('/manage/invoicing/redirect/transactions/', params={'event_slug': 'a@b'}, status=404)

    agenda = Agenda.objects.create(label='a')
    app.get('/manage/invoicing/redirect/transactions/', params={'event_slug': 'a@b'}, status=404)

    regie = Regie.objects.create(label='Foo')
    agenda.regie = regie
    agenda.save()
    resp = app.get('/manage/invoicing/redirect/transactions/', params={'event_slug': 'a@b', 'foo': 'bar'})
    assert resp.location.endswith(
        '/manage/invoicing/regie/%s/transactions/for-event/?event_slug=a%%40b&foo=bar' % regie.pk
    )
