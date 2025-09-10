import datetime
from unittest import mock

import pytest
from django.db import IntegrityError, transaction
from django.template import Context
from django.test.client import RequestFactory
from django.utils.timezone import now
from publik_django_templatetags.wcs.context_processors import Cards

from lingo.invoicing.errors import PayerDataError, PayerError
from lingo.invoicing.models import (
    Campaign,
    Counter,
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
    Refund,
    Regie,
)
from tests.invoicing.utils import mocked_requests_send

pytestmark = pytest.mark.django_db


@pytest.fixture
def context():
    return Context(
        {
            'cards': Cards(),
            'request': RequestFactory().get('/'),
        }
    )


@pytest.mark.parametrize('draft', [True, False])
@pytest.mark.parametrize('orphan', [True, False])
def test_invoiceline_total_amount(draft, orphan):
    regie = Regie.objects.create()
    line_model = DraftInvoiceLine if draft else InvoiceLine

    pool = None
    if not orphan:
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
            draft=draft,
        )

    # create line
    line = line_model.objects.create(
        event_date=now().date(),
        quantity=0,
        unit_amount=0,
        pool=pool,
    )
    line.refresh_from_db()
    assert line.total_amount == 0

    line = line_model.objects.create(
        event_date=now().date(),
        quantity=2,
        unit_amount=5,
        pool=pool,
    )
    line.refresh_from_db()
    assert line.total_amount == 10

    # update line
    line.unit_amount = 10
    line.quantity = 3
    line.save()
    line.refresh_from_db()
    assert line.total_amount == 30

    line.unit_amount = 3
    line.quantity = -2
    line.save()
    line.refresh_from_db()
    assert line.total_amount == -6


@pytest.mark.parametrize('draft', [True, False])
@pytest.mark.parametrize('orphan', [True, False])
def test_invoice_total_amount(draft, orphan):
    regie = Regie.objects.create()
    invoice_model = DraftInvoice if draft else Invoice
    line_model = DraftInvoiceLine if draft else InvoiceLine

    pool = None
    if not orphan:
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
            draft=draft,
        )

    invoice = invoice_model.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        regie=regie,
        pool=pool,
    )
    assert invoice.total_amount == 0
    invoice2 = invoice_model.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        regie=regie,
        pool=pool,
    )
    assert invoice2.total_amount == 0

    line = line_model.objects.create(
        event_date=now().date(),
        invoice=invoice,  # with invoice
        quantity=0,
        unit_amount=0,
        pool=pool,
    )
    invoice.refresh_from_db()
    assert invoice.total_amount == 0
    invoice2.refresh_from_db()
    assert invoice2.total_amount == 0

    # update line
    line.unit_amount = 10
    line.quantity = 1
    line.save()
    invoice.refresh_from_db()
    assert invoice.total_amount == 10
    invoice2.refresh_from_db()
    assert invoice2.total_amount == 0

    # update some amount-related field
    line.unit_amount = 12
    line.save()
    invoice.refresh_from_db()
    assert invoice.total_amount == 12
    invoice2.refresh_from_db()
    assert invoice2.total_amount == 0

    line.quantity = -1
    line.save()
    invoice.refresh_from_db()
    assert invoice.total_amount == -12
    invoice2.refresh_from_db()
    assert invoice2.total_amount == 0

    # create line with invoice
    line2 = line_model.objects.create(
        event_date=now().date(),
        invoice=invoice,
        quantity=1,
        unit_amount=20,
        pool=pool,
    )
    invoice.refresh_from_db()
    assert invoice.total_amount == 8
    invoice2.refresh_from_db()
    assert invoice2.total_amount == 0

    # change invoice
    line2.invoice = invoice2
    line2.save()
    invoice.refresh_from_db()
    assert invoice.total_amount == -12
    invoice2.refresh_from_db()
    assert invoice2.total_amount == 20

    # delete line
    line2.delete()
    invoice.refresh_from_db()
    assert invoice.total_amount == -12
    invoice2.refresh_from_db()
    assert invoice2.total_amount == 0

    # create line without invoice
    line3 = line_model.objects.create(
        event_date=now().date(),
        quantity=1,
        unit_amount=20,
        pool=pool,
    )
    invoice.refresh_from_db()
    assert invoice.total_amount == -12
    invoice2.refresh_from_db()
    assert invoice2.total_amount == 0

    # set invoice
    line3.invoice = invoice
    line3.save()
    invoice.refresh_from_db()
    assert invoice.total_amount == 8
    invoice2.refresh_from_db()
    assert invoice2.total_amount == 0

    # reset invoice
    line3.invoice = None
    line3.save()
    invoice.refresh_from_db()
    assert invoice.total_amount == -12
    invoice2.refresh_from_db()
    assert invoice2.total_amount == 0
    # no changes
    line3.save()
    invoice.refresh_from_db()
    assert invoice.total_amount == -12
    invoice2.refresh_from_db()
    assert invoice2.total_amount == 0

    # delete line
    line3.delete()
    invoice.refresh_from_db()
    assert invoice.total_amount == -12
    invoice2.refresh_from_db()
    assert invoice2.total_amount == 0


@pytest.mark.parametrize('orphan', [True, False])
def test_invoice_payments(orphan):
    regie = Regie.objects.create()
    PaymentType.create_defaults(regie)
    pool = None
    if not orphan:
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
            draft=False,
        )

    invoice1 = Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        regie=regie,
        pool=pool,
    )
    line1 = InvoiceLine.objects.create(
        event_date=now().date(),
        invoice=invoice1,
        quantity=1,
        unit_amount=42,
        pool=pool,
    )
    invoice2 = Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        regie=regie,
        pool=pool,
    )
    line2 = InvoiceLine.objects.create(
        event_date=now().date(),
        invoice=invoice2,
        quantity=1,
        unit_amount=35,
        pool=pool,
    )
    line3 = InvoiceLine.objects.create(
        event_date=now().date(),
        invoice=invoice2,
        quantity=1,
        unit_amount=-10,
        pool=pool,
    )
    line1.refresh_from_db()
    line2.refresh_from_db()
    line3.refresh_from_db()
    assert line1.paid_amount == 0
    assert line1.remaining_amount == 42
    assert line2.paid_amount == 0
    assert line2.remaining_amount == 35
    assert line3.paid_amount == 0
    assert line3.remaining_amount == -10
    invoice1.refresh_from_db()
    invoice2.refresh_from_db()
    assert invoice1.total_amount == 42
    assert invoice1.paid_amount == 0
    assert invoice1.remaining_amount == 42
    assert invoice2.total_amount == 25
    assert invoice2.paid_amount == 0
    assert invoice2.remaining_amount == 25

    payment1 = Payment.objects.create(
        regie=regie,
        amount=17,
        payment_type=PaymentType.objects.get(regie=regie, slug='cash'),
    )
    InvoiceLinePayment.objects.create(
        payment=payment1,
        amount=7,
        line=line1,
    )
    line1.refresh_from_db()
    line2.refresh_from_db()
    line3.refresh_from_db()
    assert line1.paid_amount == 7
    assert line1.remaining_amount == 35
    assert line2.paid_amount == 0
    assert line2.remaining_amount == 35
    assert line3.paid_amount == 0
    assert line3.remaining_amount == -10
    invoice1.refresh_from_db()
    invoice2.refresh_from_db()
    assert invoice1.total_amount == 42
    assert invoice1.paid_amount == 7
    assert invoice1.remaining_amount == 35
    assert invoice2.total_amount == 25
    assert invoice2.paid_amount == 0
    assert invoice2.remaining_amount == 25

    InvoiceLinePayment.objects.create(
        payment=payment1,
        amount=10,
        line=line2,
    )
    line1.refresh_from_db()
    line2.refresh_from_db()
    line3.refresh_from_db()
    assert line1.paid_amount == 7
    assert line1.remaining_amount == 35
    assert line2.paid_amount == 10
    assert line2.remaining_amount == 25
    assert line3.paid_amount == 0
    assert line3.remaining_amount == -10
    invoice1.refresh_from_db()
    invoice2.refresh_from_db()
    assert invoice1.total_amount == 42
    assert invoice1.paid_amount == 7
    assert invoice1.remaining_amount == 35
    assert invoice2.total_amount == 25
    assert invoice2.paid_amount == 10
    assert invoice2.remaining_amount == 15

    payment2 = Payment.objects.create(
        regie=regie,
        amount=60.01,
        payment_type=PaymentType.objects.get(regie=regie, slug='check'),
    )
    InvoiceLinePayment.objects.create(
        payment=payment2,
        amount=15,
        line=line1,
    )
    line1.refresh_from_db()
    line2.refresh_from_db()
    line3.refresh_from_db()
    assert line1.paid_amount == 22
    assert line1.remaining_amount == 20
    assert line2.paid_amount == 10
    assert line2.remaining_amount == 25
    assert line3.paid_amount == 0
    assert line3.remaining_amount == -10
    invoice1.refresh_from_db()
    invoice2.refresh_from_db()
    assert invoice1.total_amount == 42
    assert invoice1.paid_amount == 22
    assert invoice1.remaining_amount == 20
    assert invoice2.total_amount == 25
    assert invoice2.paid_amount == 10
    assert invoice2.remaining_amount == 15

    invoice_line_payment = InvoiceLinePayment.objects.create(
        payment=payment2,
        amount=20,
        line=line1,
    )
    line1.refresh_from_db()
    line2.refresh_from_db()
    line3.refresh_from_db()
    assert line1.paid_amount == 42
    assert line1.remaining_amount == 0
    assert line2.paid_amount == 10
    assert line2.remaining_amount == 25
    assert line3.paid_amount == 0
    assert line3.remaining_amount == -10
    invoice1.refresh_from_db()
    invoice2.refresh_from_db()
    assert invoice1.total_amount == 42
    assert invoice1.paid_amount == 42
    assert invoice1.remaining_amount == 0
    assert invoice2.total_amount == 25
    assert invoice2.paid_amount == 10
    assert invoice2.remaining_amount == 15

    # raise
    with transaction.atomic():
        with pytest.raises(IntegrityError) as excinfo:
            InvoiceLinePayment.objects.create(
                payment=payment2,
                amount=25.01,
                line=line2,
            )
        assert 'invoicing_invoiceline' in str(excinfo.value)
        assert 'paid_amount_check' in str(excinfo.value)
    line1.refresh_from_db()
    line2.refresh_from_db()
    line3.refresh_from_db()
    assert line1.paid_amount == 42
    assert line1.remaining_amount == 0
    assert line2.paid_amount == 10
    assert line2.remaining_amount == 25
    assert line3.paid_amount == 0
    assert line3.remaining_amount == -10
    invoice1.refresh_from_db()
    invoice2.refresh_from_db()
    assert invoice1.total_amount == 42
    assert invoice1.paid_amount == 42
    assert invoice1.remaining_amount == 0
    assert invoice2.total_amount == 25
    assert invoice2.paid_amount == 10
    assert invoice2.remaining_amount == 15

    invoice_line_payment.line = line2
    invoice_line_payment.amount = 10
    invoice_line_payment.save()
    line1.refresh_from_db()
    line2.refresh_from_db()
    line3.refresh_from_db()
    assert line1.paid_amount == 22
    assert line1.remaining_amount == 20
    assert line2.paid_amount == 20
    assert line2.remaining_amount == 15
    assert line3.paid_amount == 0
    assert line3.remaining_amount == -10
    invoice1.refresh_from_db()
    invoice2.refresh_from_db()
    assert invoice1.total_amount == 42
    assert invoice1.paid_amount == 22
    assert invoice1.remaining_amount == 20
    assert invoice2.total_amount == 25
    assert invoice2.paid_amount == 20
    assert invoice2.remaining_amount == 5

    line2.quantity = 2
    line2.save()
    line1.refresh_from_db()
    line2.refresh_from_db()
    line3.refresh_from_db()
    assert line1.paid_amount == 22
    assert line1.remaining_amount == 20
    assert line2.paid_amount == 20
    assert line2.remaining_amount == 50
    assert line3.paid_amount == 0
    assert line3.remaining_amount == -10
    invoice1.refresh_from_db()
    invoice2.refresh_from_db()
    assert invoice1.total_amount == 42
    assert invoice1.paid_amount == 22
    assert invoice1.remaining_amount == 20
    assert invoice2.total_amount == 60
    assert invoice2.paid_amount == 20
    assert invoice2.remaining_amount == 40

    invoice_line_payment.delete()
    line1.refresh_from_db()
    line2.refresh_from_db()
    line3.refresh_from_db()
    assert line1.paid_amount == 22
    assert line1.remaining_amount == 20
    assert line2.paid_amount == 10
    assert line2.remaining_amount == 60
    assert line3.paid_amount == 0
    assert line3.remaining_amount == -10
    invoice1.refresh_from_db()
    invoice2.refresh_from_db()
    assert invoice1.total_amount == 42
    assert invoice1.paid_amount == 22
    assert invoice1.remaining_amount == 20
    assert invoice2.total_amount == 60
    assert invoice2.paid_amount == 10
    assert invoice2.remaining_amount == 50

    invoice_line_payment = InvoiceLinePayment.objects.create(
        payment=payment2,
        amount=-5,
        line=line3,
    )
    line1.refresh_from_db()
    line2.refresh_from_db()
    line3.refresh_from_db()
    assert line1.paid_amount == 22
    assert line1.remaining_amount == 20
    assert line2.paid_amount == 10
    assert line2.remaining_amount == 60
    assert line3.paid_amount == -5
    assert line3.remaining_amount == -5
    invoice1.refresh_from_db()
    invoice2.refresh_from_db()
    assert invoice1.total_amount == 42
    assert invoice1.paid_amount == 22
    assert invoice1.remaining_amount == 20
    assert invoice2.total_amount == 60
    assert invoice2.paid_amount == 5
    assert invoice2.remaining_amount == 55


def test_creditline_total_amount():
    regie = Regie.objects.create()
    credit = Credit.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        regie=regie,
    )

    # create line
    line = CreditLine.objects.create(
        event_date=now().date(),
        quantity=0,
        unit_amount=0,
        credit=credit,
    )
    line.refresh_from_db()
    assert line.total_amount == 0

    line = CreditLine.objects.create(
        event_date=now().date(),
        quantity=2,
        unit_amount=5,
        credit=credit,
    )
    line.refresh_from_db()
    assert line.total_amount == 10

    # update line
    line.unit_amount = 10
    line.quantity = 3
    line.save()
    line.refresh_from_db()
    assert line.total_amount == 30

    line.unit_amount = 3
    line.quantity = -2
    line.save()
    line.refresh_from_db()
    assert line.total_amount == -6


def test_credit_total_amount():
    regie = Regie.objects.create()

    credit = Credit.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        regie=regie,
    )
    assert credit.total_amount == 0
    credit2 = Credit.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        regie=regie,
    )
    assert credit2.total_amount == 0

    line = CreditLine.objects.create(
        event_date=now().date(),
        credit=credit,
        quantity=0,
        unit_amount=0,
    )
    credit.refresh_from_db()
    assert credit.total_amount == 0
    credit2.refresh_from_db()
    assert credit2.total_amount == 0

    # update line
    line.unit_amount = 10
    line.quantity = 1
    line.save()
    credit.refresh_from_db()
    assert credit.total_amount == 10
    credit2.refresh_from_db()
    assert credit2.total_amount == 0

    # update some amount-related field
    line.unit_amount = 12
    line.save()
    credit.refresh_from_db()
    assert credit.total_amount == 12
    credit2.refresh_from_db()
    assert credit2.total_amount == 0

    line.quantity = -1
    line.save()
    credit.refresh_from_db()
    assert credit.total_amount == -12
    credit2.refresh_from_db()
    assert credit2.total_amount == 0

    # create line
    line2 = CreditLine.objects.create(
        event_date=now().date(),
        credit=credit,
        quantity=1,
        unit_amount=20,
    )
    credit.refresh_from_db()
    assert credit.total_amount == 8
    credit2.refresh_from_db()
    assert credit2.total_amount == 0

    # change credit
    line2.credit = credit2
    line2.save()
    credit.refresh_from_db()
    assert credit.total_amount == -12
    credit2.refresh_from_db()
    assert credit2.total_amount == 20

    # delete line
    line2.delete()
    credit.refresh_from_db()
    assert credit.total_amount == -12
    credit2.refresh_from_db()
    assert credit2.total_amount == 0

    # create line
    line3 = CreditLine.objects.create(
        event_date=now().date(),
        credit=credit,
        quantity=1,
        unit_amount=20,
    )
    credit.refresh_from_db()
    assert credit.total_amount == 8
    credit2.refresh_from_db()
    assert credit2.total_amount == 0

    # no changes
    line3.save()
    credit.refresh_from_db()
    assert credit.total_amount == 8
    credit2.refresh_from_db()
    assert credit2.total_amount == 0

    # delete line
    line3.delete()
    credit.refresh_from_db()
    assert credit.total_amount == -12
    credit2.refresh_from_db()
    assert credit2.total_amount == 0


def test_credit_assignments():
    regie = Regie.objects.create()
    PaymentType.create_defaults(regie)

    credit1 = Credit.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        regie=regie,
    )
    CreditLine.objects.create(
        event_date=now().date(),
        credit=credit1,
        quantity=1,
        unit_amount=42,
    )
    credit2 = Credit.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        regie=regie,
    )
    line2 = CreditLine.objects.create(
        event_date=now().date(),
        credit=credit2,
        quantity=1,
        unit_amount=35,
    )
    CreditLine.objects.create(
        event_date=now().date(),
        credit=credit2,
        quantity=1,
        unit_amount=-10,
    )
    credit1.refresh_from_db()
    credit2.refresh_from_db()
    assert credit1.total_amount == 42
    assert credit1.assigned_amount == 0
    assert credit1.remaining_amount == 42
    assert credit2.total_amount == 25
    assert credit2.assigned_amount == 0
    assert credit2.remaining_amount == 25

    invoice = Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        regie=regie,
    )
    payment1 = Payment.objects.create(
        regie=regie,
        amount=17,
        payment_type=PaymentType.objects.get(regie=regie, slug='credit'),
    )
    CreditAssignment.objects.create(
        invoice=invoice,
        payment=payment1,
        amount=7,
        credit=credit1,
    )
    credit1.refresh_from_db()
    credit2.refresh_from_db()
    assert credit1.total_amount == 42
    assert credit1.assigned_amount == 7
    assert credit1.remaining_amount == 35
    assert credit2.total_amount == 25
    assert credit2.assigned_amount == 0
    assert credit2.remaining_amount == 25

    CreditAssignment.objects.create(
        invoice=invoice,
        payment=payment1,
        amount=10,
        credit=credit2,
    )
    credit1.refresh_from_db()
    credit2.refresh_from_db()
    assert credit1.total_amount == 42
    assert credit1.assigned_amount == 7
    assert credit1.remaining_amount == 35
    assert credit2.total_amount == 25
    assert credit2.assigned_amount == 10
    assert credit2.remaining_amount == 15

    payment2 = Payment.objects.create(
        regie=regie,
        amount=60.01,
        payment_type=PaymentType.objects.get(regie=regie, slug='check'),
    )
    CreditAssignment.objects.create(
        invoice=invoice,
        payment=payment2,
        amount=15,
        credit=credit1,
    )
    credit1.refresh_from_db()
    credit2.refresh_from_db()
    assert credit1.total_amount == 42
    assert credit1.assigned_amount == 22
    assert credit1.remaining_amount == 20
    assert credit2.total_amount == 25
    assert credit2.assigned_amount == 10
    assert credit2.remaining_amount == 15

    credit_assignment = CreditAssignment.objects.create(
        invoice=invoice,
        payment=payment2,
        amount=20,
        credit=credit1,
    )
    credit1.refresh_from_db()
    credit2.refresh_from_db()
    assert credit1.total_amount == 42
    assert credit1.assigned_amount == 42
    assert credit1.remaining_amount == 0
    assert credit2.total_amount == 25
    assert credit2.assigned_amount == 10
    assert credit2.remaining_amount == 15

    # raise
    with transaction.atomic():
        with pytest.raises(IntegrityError) as excinfo:
            CreditAssignment.objects.create(
                invoice=invoice,
                payment=payment2,
                amount=25.01,
                credit=credit2,
            )
        assert 'invoicing_credit' in str(excinfo.value)
        assert 'assigned_amount_check' in str(excinfo.value)
    credit1.refresh_from_db()
    credit2.refresh_from_db()
    assert credit1.total_amount == 42
    assert credit1.assigned_amount == 42
    assert credit1.remaining_amount == 0
    assert credit2.total_amount == 25
    assert credit2.assigned_amount == 10
    assert credit2.remaining_amount == 15

    credit_assignment.credit = credit2
    credit_assignment.amount = 10
    credit_assignment.save()
    credit1.refresh_from_db()
    credit2.refresh_from_db()
    assert credit1.total_amount == 42
    assert credit1.assigned_amount == 22
    assert credit1.remaining_amount == 20
    assert credit2.total_amount == 25
    assert credit2.assigned_amount == 20
    assert credit2.remaining_amount == 5

    line2.quantity = 2
    line2.save()
    credit1.refresh_from_db()
    credit2.refresh_from_db()
    assert credit1.total_amount == 42
    assert credit1.assigned_amount == 22
    assert credit1.remaining_amount == 20
    assert credit2.total_amount == 60
    assert credit2.assigned_amount == 20
    assert credit2.remaining_amount == 40

    credit_assignment.delete()
    credit1.refresh_from_db()
    credit2.refresh_from_db()
    assert credit1.total_amount == 42
    assert credit1.assigned_amount == 22
    assert credit1.remaining_amount == 20
    assert credit2.total_amount == 60
    assert credit2.assigned_amount == 10
    assert credit2.remaining_amount == 50

    credit_assignment = CreditAssignment.objects.create(
        invoice=invoice,
        payment=payment2,
        amount=-5,
        credit=credit2,
    )
    credit1.refresh_from_db()
    credit2.refresh_from_db()
    assert credit1.total_amount == 42
    assert credit1.assigned_amount == 22
    assert credit1.remaining_amount == 20
    assert credit2.total_amount == 60
    assert credit2.assigned_amount == 5
    assert credit2.remaining_amount == 55

    credit_assignment = CreditAssignment.objects.create(
        invoice=invoice,
        payment=payment2,
        amount=-5,
        credit=credit2,
    )
    credit1.refresh_from_db()
    credit2.refresh_from_db()
    assert credit1.total_amount == 42
    assert credit1.assigned_amount == 22
    assert credit1.remaining_amount == 20
    assert credit2.total_amount == 60
    assert credit2.assigned_amount == 0
    assert credit2.remaining_amount == 60


def test_counter():
    regie1 = Regie.objects.create()
    regie2 = Regie.objects.create()

    assert Counter.get_count(regie=regie1, name='foo', kind='invoice') == 1
    assert Counter.objects.count() == 1
    counter1 = Counter.objects.get(regie=regie1, name='foo', kind='invoice')
    assert counter1.value == 1

    assert Counter.get_count(regie=regie1, name='foo', kind='collection') == 1
    assert Counter.objects.count() == 2
    counter1_bis = Counter.objects.get(regie=regie1, name='foo', kind='collection')
    assert counter1_bis.value == 1

    assert Counter.get_count(regie=regie1, name='foo', kind='payment') == 1
    assert Counter.objects.count() == 3
    counter1_bis = Counter.objects.get(regie=regie1, name='foo', kind='payment')
    assert counter1_bis.value == 1

    assert Counter.get_count(regie=regie1, name='foo', kind='docket') == 1
    assert Counter.objects.count() == 4
    counter1_bis = Counter.objects.get(regie=regie1, name='foo', kind='docket')
    assert counter1_bis.value == 1

    assert Counter.get_count(regie=regie1, name='foo', kind='credit') == 1
    assert Counter.objects.count() == 5
    counter1_ter = Counter.objects.get(regie=regie1, name='foo', kind='credit')
    assert counter1_ter.value == 1

    assert Counter.get_count(regie=regie1, name='foo', kind='refund') == 1
    assert Counter.objects.count() == 6
    counter1_ter = Counter.objects.get(regie=regie1, name='foo', kind='refund')
    assert counter1_ter.value == 1

    assert Counter.get_count(regie=regie1, name='foo', kind='invoice') == 2
    counter1.refresh_from_db()
    assert counter1.value == 2
    counter1_bis.refresh_from_db()
    assert counter1_bis.value == 1
    counter1_ter.refresh_from_db()
    assert counter1_ter.value == 1

    assert Counter.get_count(regie=regie1, name='foo', kind='invoice') == 3
    counter1.refresh_from_db()
    assert counter1.value == 3

    assert Counter.get_count(regie=regie2, name='foo', kind='invoice') == 1
    assert Counter.objects.count() == 7
    counter1.refresh_from_db()
    assert counter1.value == 3
    counter2 = Counter.objects.get(regie=regie2, name='foo', kind='invoice')
    assert counter2.value == 1

    assert Counter.get_count(regie=regie2, name='bar', kind='invoice') == 1
    assert Counter.objects.count() == 8
    counter1.refresh_from_db()
    assert counter1.value == 3
    counter2.refresh_from_db()
    assert counter2.value == 1
    counter3 = Counter.objects.get(regie=regie2, name='bar', kind='invoice')
    assert counter3.value == 1


def test_regie_counter_name():
    regie = Regie.objects.create()
    assert regie.counter_name == '{yy}'

    assert regie.get_counter_name(datetime.date(2023, 1, 1)) == '23'
    assert regie.get_counter_name(datetime.date(2024, 1, 1)) == '24'

    regie.counter_name = '{yyyy}'
    regie.save()
    assert regie.get_counter_name(datetime.date(2023, 1, 1)) == '2023'
    assert regie.get_counter_name(datetime.date(2024, 1, 1)) == '2024'

    regie.counter_name = '{yy}-{mm}'
    regie.save()
    assert regie.get_counter_name(datetime.date(2023, 1, 1)) == '23-01'
    assert regie.get_counter_name(datetime.date(2023, 2, 1)) == '23-02'
    assert regie.get_counter_name(datetime.date(2024, 12, 1)) == '24-12'


def test_regie_format_number():
    regie = Regie.objects.create()
    assert regie.invoice_number_format == 'F{regie_id:02d}-{yy}-{mm}-{number:07d}'
    assert regie.collection_number_format == 'T{regie_id:02d}-{yy}-{mm}-{number:07d}'
    assert regie.payment_number_format == 'R{regie_id:02d}-{yy}-{mm}-{number:07d}'
    assert regie.docket_number_format == 'B{regie_id:02d}-{yy}-{mm}-{number:07d}'
    assert regie.credit_number_format == 'A{regie_id:02d}-{yy}-{mm}-{number:07d}'
    assert regie.refund_number_format == 'V{regie_id:02d}-{yy}-{mm}-{number:07d}'

    assert regie.format_number(datetime.date(2023, 2, 15), 42, 'invoice') == 'F%02d-23-02-0000042' % regie.pk
    assert (
        regie.format_number(datetime.date(2024, 12, 15), 42000000, 'invoice')
        == 'F%02d-24-12-42000000' % regie.pk
    )

    regie.invoice_number_format = 'Ffoobar-{yyyy}-{number:08d}'
    regie.save()
    assert regie.format_number(datetime.date(2023, 2, 15), 42, 'invoice') == 'Ffoobar-2023-00000042'
    assert regie.format_number(datetime.date(2024, 12, 15), 42000000, 'invoice') == 'Ffoobar-2024-42000000'

    assert (
        regie.format_number(datetime.date(2023, 2, 15), 42, 'collection') == 'T%02d-23-02-0000042' % regie.pk
    )
    assert (
        regie.format_number(datetime.date(2024, 12, 15), 42000000, 'collection')
        == 'T%02d-24-12-42000000' % regie.pk
    )

    regie.collection_number_format = 'Tfoobar-{yyyy}-{number:08d}'
    regie.save()
    assert regie.format_number(datetime.date(2023, 2, 15), 42, 'collection') == 'Tfoobar-2023-00000042'
    assert regie.format_number(datetime.date(2024, 12, 15), 42000000, 'collection') == 'Tfoobar-2024-42000000'

    assert regie.format_number(datetime.date(2023, 2, 15), 42, 'payment') == 'R%02d-23-02-0000042' % regie.pk
    assert (
        regie.format_number(datetime.date(2024, 12, 15), 42000000, 'payment')
        == 'R%02d-24-12-42000000' % regie.pk
    )

    regie.payment_number_format = 'Rfoobar-{yyyy}-{number:08d}'
    regie.save()
    assert regie.format_number(datetime.date(2023, 2, 15), 42, 'payment') == 'Rfoobar-2023-00000042'
    assert regie.format_number(datetime.date(2024, 12, 15), 42000000, 'payment') == 'Rfoobar-2024-42000000'

    assert regie.format_number(datetime.date(2023, 2, 15), 42, 'docket') == 'B%02d-23-02-0000042' % regie.pk
    assert (
        regie.format_number(datetime.date(2024, 12, 15), 42000000, 'docket')
        == 'B%02d-24-12-42000000' % regie.pk
    )

    regie.docket_number_format = 'Bfoobar-{yyyy}-{number:08d}'
    regie.save()
    assert regie.format_number(datetime.date(2023, 2, 15), 42, 'docket') == 'Bfoobar-2023-00000042'
    assert regie.format_number(datetime.date(2024, 12, 15), 42000000, 'docket') == 'Bfoobar-2024-42000000'

    assert regie.format_number(datetime.date(2023, 2, 15), 42, 'credit') == 'A%02d-23-02-0000042' % regie.pk
    assert (
        regie.format_number(datetime.date(2024, 12, 15), 42000000, 'credit')
        == 'A%02d-24-12-42000000' % regie.pk
    )

    regie.credit_number_format = 'Afoobar-{yyyy}-{number:08d}'
    regie.save()
    assert regie.format_number(datetime.date(2023, 2, 15), 42, 'credit') == 'Afoobar-2023-00000042'
    assert regie.format_number(datetime.date(2024, 12, 15), 42000000, 'credit') == 'Afoobar-2024-42000000'

    assert regie.format_number(datetime.date(2023, 2, 15), 42, 'refund') == 'V%02d-23-02-0000042' % regie.pk
    assert (
        regie.format_number(datetime.date(2024, 12, 15), 42000000, 'refund')
        == 'V%02d-24-12-42000000' % regie.pk
    )

    regie.refund_number_format = 'Vfoobar-{yyyy}-{number:08d}'
    regie.save()
    assert regie.format_number(datetime.date(2023, 2, 15), 42, 'refund') == 'Vfoobar-2023-00000042'
    assert regie.format_number(datetime.date(2024, 12, 15), 42000000, 'refund') == 'Vfoobar-2024-42000000'


@mock.patch('requests.Session.send', side_effect=mocked_requests_send)
def test_get_payer_external_id(mock_send, context, nocache):
    regie = Regie.objects.create(label='Regie')

    values = [
        ('bar', 'bar'),
        ('{{ 40|add:2 }}', '42'),
        ('{{ cards|objects:"card_model_1"|first|get:"id" }}', '42'),
    ]
    for value, result in values:
        regie.payer_external_id_prefix = ''
        regie.payer_external_id_template = value
        regie.save()
        assert regie.get_payer_external_id(request=context['request'], user_external_id='child:42') == result
        regie.payer_external_id_prefix = 'prefix:'
        regie.save()
        assert (
            regie.get_payer_external_id(request=context['request'], user_external_id='child:42')
            == 'prefix:%s' % result
        )

    values = [
        ('', 'empty-template'),
        ('{{ "" }}', 'empty-result'),
        ('{% for %}', 'syntax-error'),
        ('{{ "foo"|add:user.email }}', 'variable-error'),
    ]
    for value, error in values:
        regie.payer_external_id_template = value
        regie.save()
        with pytest.raises(PayerError) as e:
            regie.get_payer_external_id(request=context['request'], user_external_id='child:42')
        assert e.value.details == {'reason': error}

    # user_external_id can be used in variables
    regie.payer_external_id_template = (
        '{{ cards|objects:"qf"|filter_by:"foo"|filter_value:user_external_id|first|get:"id" }}'
    )
    regie.save()
    mock_send.reset_mock()
    regie.get_payer_external_id(request=context['request'], user_external_id='child:42')
    assert 'filter-foo=child%3A42&' in mock_send.call_args_list[0][0][0].url
    regie.payer_external_id_template = (
        '{{ cards|objects:"qf"|filter_by:"foo"|filter_value:user_external_raw_id|first|get:"id" }}',
    )
    regie.save()
    mock_send.reset_mock()
    regie.get_payer_external_id(request=context['request'], user_external_id='child:42')
    assert 'filter-foo=42&' in mock_send.call_args_list[0][0][0].url

    # serialized booking can be used if in parameters
    regie.payer_external_id_template = '{% if data.booking and data.booking.extra_data.payer_id %}{{ data.booking.extra_data.payer_id }}{% else %}35{% endif %}'
    regie.save()
    mock_send.reset_mock()
    assert regie.get_payer_external_id(request=context['request'], user_external_id='child:42') == 'prefix:35'
    assert (
        regie.get_payer_external_id(
            request=context['request'], user_external_id='child:42', booking={'extra_data': {}}
        )
        == 'prefix:35'
    )
    assert (
        regie.get_payer_external_id(
            request=context['request'],
            user_external_id='child:42',
            booking={'extra_data': {'payer_id': '42'}},
        )
        == 'prefix:42'
    )


@mock.patch('requests.Session.send', side_effect=mocked_requests_send)
def test_get_payer_external_id_from_nameid(mock_send, context, nocache):
    regie = Regie.objects.create(label='Regie')

    values = [
        ('bar', 'bar'),
        ('{{ 40|add:2 }}', '42'),
        ('{{ cards|objects:"card_model_1"|first|get:"id" }}', '42'),
    ]
    for value, result in values:
        regie.payer_external_id_prefix = ''
        regie.payer_external_id_from_nameid_template = value
        regie.save()
        assert regie.get_payer_external_id_from_nameid(request=context['request'], nameid='foobar') == result
        regie.payer_external_id_prefix = 'prefix:'
        regie.save()
        assert (
            regie.get_payer_external_id_from_nameid(request=context['request'], nameid='foobar')
            == 'prefix:%s' % result
        )

    values = [
        ('', 'empty-template'),
        ('{{ "" }}', 'empty-result'),
        ('{% for %}', 'syntax-error'),
        ('{{ "foo"|add:user.email }}', 'variable-error'),
    ]
    for value, error in values:
        regie.payer_external_id_from_nameid_template = value
        regie.save()
        with pytest.raises(PayerError) as e:
            regie.get_payer_external_id_from_nameid(request=context['request'], nameid='foobar')
        assert e.value.details == {'reason': error}


@mock.patch('requests.Session.send', side_effect=mocked_requests_send)
def test_get_payer_data(mock_send, context, nocache):
    regie = Regie.objects.create(label='Regie')

    with pytest.raises(PayerError) as e:
        regie.get_payer_data(request=context['request'], payer_external_id='payer:42')
    assert e.value.details == {'reason': 'missing-card-model'}

    regie.payer_carddef_reference = 'default:card_model_1'
    regie.save()

    original_variables = {
        'first_name': 'fielda',
        'last_name': 'fielda',
        'address': 'fielda',
        'email': 'fielda',
        'phone': 'fielda',
        'direct_debit': 'fieldb',
    }
    payer_data = {
        'first_name': 'foo',
        'last_name': 'foo',
        'address': 'foo',
        'email': 'foo',
        'phone': 'foo',
        'direct_debit': True,
    }
    regie.payer_user_fields_mapping = original_variables.copy()
    regie.save()
    assert regie.get_payer_data(request=context['request'], payer_external_id='payer:42') == payer_data
    assert '/api/cards/card_model_1/list?' in mock_send.call_args_list[-1][0][0].url
    assert (
        '&filter-internal-id=42&filter-internal-id-operator=eq&include-fields=on'
        in mock_send.call_args_list[-1][0][0].url
    )

    for key in ['first_name', 'last_name', 'address']:
        regie.payer_user_fields_mapping = original_variables.copy()
        regie.payer_user_fields_mapping[key] = ''
        regie.save()
        with pytest.raises(PayerDataError) as e:
            regie.get_payer_data(request=context['request'], payer_external_id='payer:42')
        assert e.value.details == {'key': key, 'reason': 'not-defined'}

    for key in ['email', 'phone']:
        regie.payer_user_fields_mapping = original_variables.copy()
        regie.payer_user_fields_mapping[key] = ''
        regie.save()
        assert regie.get_payer_data(request=context['request'], payer_external_id='payer:42')[key] == ''

    for key in ['direct_debit']:
        regie.payer_carddef_reference = 'default:card_model_1'
        regie.payer_user_fields_mapping = original_variables.copy()
        regie.payer_user_fields_mapping[key] = ''
        regie.save()
        assert regie.get_payer_data(request=context['request'], payer_external_id='payer:42')[key] is False

        regie.payer_user_fields_mapping = original_variables.copy()
        regie.payer_user_fields_mapping[key] = 'fielda'
        regie.save()
        with pytest.raises(PayerDataError) as e:
            regie.get_payer_data(request=context['request'], payer_external_id='payer:42')
        assert e.value.details == {'key': key, 'reason': 'not-a-boolean'}

        regie.payer_carddef_reference = 'default:card_model_2'  # with False as value
        regie.payer_user_fields_mapping = original_variables.copy()
        regie.save()
        assert regie.get_payer_data(request=context['request'], payer_external_id='payer:42')[key] is False

    # check quotes
    regie.payer_carddef_reference = 'default:card_model_3'  # foo'bar as name
    regie.payer_user_fields_mapping = original_variables.copy()
    regie.save()
    assert (
        regie.get_payer_data(request=context['request'], payer_external_id='payer:42')['first_name']
        == 'foo\'bar'
    )


@pytest.mark.parametrize('draft', [True, False])
def test_invoice_model(draft):
    regie = Regie.objects.create()
    invoice_model = DraftInvoice if draft else Invoice
    campaign = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_debit=datetime.date(2022, 11, 15),
        invoice_model='full',
    )
    pool = Pool.objects.create(
        campaign=campaign,
        draft=draft,
    )
    invoice = invoice_model.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        regie=regie,
    )

    assert regie.invoice_model == 'middle'
    assert campaign.invoice_model == 'full'
    assert invoice.invoice_model == 'middle'

    regie.invoice_model = 'basic'
    regie.save()
    assert regie.invoice_model == 'basic'
    assert campaign.invoice_model == 'full'
    assert invoice.invoice_model == 'basic'

    invoice.pool = pool
    invoice.save()
    assert regie.invoice_model == 'basic'
    assert campaign.invoice_model == 'full'
    assert invoice.invoice_model == 'full'


def test_invoice_formatted_number_from_date_invoicing():
    regie = Regie.objects.create()
    invoice = Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        regie=regie,
    )
    invoice.set_number()
    assert invoice.formatted_number == 'F%02d-%s-0000001' % (regie.pk, invoice.created_at.strftime('%y-%m'))

    invoice = Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_invoicing=datetime.date(2022, 11, 6),
        regie=regie,
    )
    invoice.set_number()
    assert invoice.formatted_number == 'F%02d-22-11-0000001' % regie.pk

    invoice = Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_invoicing=datetime.date(2022, 11, 6),
        regie=regie,
    )
    invoice.set_number()
    assert invoice.formatted_number == 'F%02d-22-11-0000002' % regie.pk


def test_credit_formatted_number_from_date_invoicing():
    regie = Regie.objects.create()
    credit = Credit.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        regie=regie,
    )
    credit.set_number()
    assert credit.formatted_number == 'A%02d-%s-0000001' % (regie.pk, credit.created_at.strftime('%y-%m'))

    credit = Credit.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_invoicing=datetime.date(2022, 11, 6),
        regie=regie,
    )
    credit.set_number()
    assert credit.formatted_number == 'A%02d-22-11-0000001' % regie.pk

    credit = Credit.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_invoicing=datetime.date(2022, 11, 6),
        regie=regie,
    )
    credit.set_number()
    assert credit.formatted_number == 'A%02d-22-11-0000002' % regie.pk


def test_refund_formatted_number_from_date_refund():
    regie = Regie.objects.create()
    refund = Refund.objects.create(
        amount=42,
        regie=regie,
    )
    refund.set_number()
    assert refund.formatted_number == 'V%02d-%s-0000001' % (regie.pk, refund.created_at.strftime('%y-%m'))

    refund = Refund.objects.create(
        amount=42,
        date_refund=datetime.date(2022, 11, 6),
        regie=regie,
    )
    refund.set_number()
    assert refund.formatted_number == 'V%02d-22-11-0000001' % regie.pk

    refund = Refund.objects.create(
        amount=42,
        date_refund=datetime.date(2022, 11, 6),
        regie=regie,
    )
    refund.set_number()
    assert refund.formatted_number == 'V%02d-22-11-0000002' % regie.pk


def test_payment_formatted_number_from_date_payment():
    regie = Regie.objects.create()
    PaymentType.create_defaults(regie)
    payment = Payment.objects.create(
        amount=42,
        payment_type=PaymentType.objects.get(regie=regie, slug='check'),
        regie=regie,
    )
    payment.set_number()
    assert payment.formatted_number == 'R%02d-%s-0000001' % (regie.pk, payment.created_at.strftime('%y-%m'))

    payment = Payment.objects.create(
        amount=42,
        payment_type=PaymentType.objects.get(regie=regie, slug='check'),
        date_payment=datetime.date(2022, 11, 6),
        regie=regie,
    )
    payment.set_number()
    assert payment.formatted_number == 'R%02d-22-11-0000001' % regie.pk

    payment = Payment.objects.create(
        amount=42,
        payment_type=PaymentType.objects.get(regie=regie, slug='check'),
        date_payment=datetime.date(2022, 11, 6),
        regie=regie,
    )
    payment.set_number()
    assert payment.formatted_number == 'R%02d-22-11-0000002' % regie.pk
