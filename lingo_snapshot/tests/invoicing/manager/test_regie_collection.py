import datetime

import pytest
from django.utils.timezone import now
from pyquery import PyQuery

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
from tests.utils import login

pytestmark = pytest.mark.django_db


def test_regie_invoices_outside_collections(app, admin_user):
    regie = Regie.objects.create(label='Foo')
    PaymentType.create_defaults(regie)
    collection = CollectionDocket.objects.create(
        regie=regie, date_end=now().date(), minimum_threshold=10, draft=True
    )
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

    invoice1 = Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=now().date() - datetime.timedelta(days=2),
        date_invoicing=datetime.date(2022, 9, 1),
        regie=regie,
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Name1',
        pool=finalized_pool,
    )
    invoice1.set_number()
    invoice1.save()
    InvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        invoice=invoice1,
        quantity=1.2,
        unit_amount=1,
    )

    invoice2 = Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=now().date() - datetime.timedelta(days=1),
        regie=regie,
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Name1',
    )
    invoice2.set_number()
    invoice2.save()
    InvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        invoice=invoice2,
        quantity=5,
        unit_amount=1,
    )

    invoice3 = Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=now().date() - datetime.timedelta(days=1),
        regie=regie,
        payer_external_id='payer:2',
        payer_first_name='First2',
        payer_last_name='Name2',
    )
    invoice3.set_number()
    invoice3.save()
    InvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        invoice=invoice3,
        quantity=5,
        unit_amount=1,
    )

    invoice4 = Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=now().date(),
        regie=regie,
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Name1',
    )
    invoice4.set_number()
    invoice4.save()
    line = InvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        invoice=invoice4,
        quantity=3,
        unit_amount=1,
    )
    # partially paid
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
    invoice4.refresh_from_db()
    assert invoice4.remaining_amount == 2
    assert invoice4.paid_amount == 1

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

    paid_invoice = Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=now().date(),
        regie=regie,
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Name1',
    )
    paid_invoice.set_number()
    paid_invoice.save()
    line = InvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        invoice=paid_invoice,
        quantity=2,
        unit_amount=1,
    )
    payment = Payment.objects.create(
        regie=regie,
        amount=2,
        payment_type=PaymentType.objects.get(regie=regie, slug='cash'),
    )
    InvoiceLinePayment.objects.create(
        payment=payment,
        line=line,
        amount=2,
    )
    paid_invoice.refresh_from_db()
    assert paid_invoice.remaining_amount == 0
    assert paid_invoice.paid_amount == 2

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

    app = login(app)
    resp = app.get('/manage/invoicing/regie/%s/' % regie.pk)
    resp = resp.click('Collections')
    resp = resp.click('Invoices outside collections')
    assert [PyQuery(tr).text() for tr in resp.pyquery('tr')] == [
        'Invoice F%02d-%s-0000001 dated %s addressed to First1 Name1 (Due date %s), remaining amount 5.00€, total for the payer 6.20€'
        % (
            regie.pk,
            invoice2.created_at.strftime('%y-%m'),
            invoice2.created_at.strftime('%d/%m/%Y'),
            invoice2.date_due.strftime('%d/%m/%Y'),
        ),
        'Invoice F%02d-%s-0000001 dated %s (created on %s) addressed to First1 Name1 (Due date %s), remaining amount 1.20€, total for the payer 6.20€'
        % (
            regie.pk,
            invoice1.date_invoicing.strftime('%y-%m'),
            invoice1.date_invoicing.strftime('%d/%m/%Y'),
            invoice1.created_at.strftime('%d/%m/%Y'),
            invoice1.date_due.strftime('%d/%m/%Y'),
        ),
        'Invoice F%02d-%s-0000002 dated %s addressed to First2 Name2 (Due date %s), remaining amount 5.00€, total for the payer 5.00€'
        % (
            regie.pk,
            invoice3.created_at.strftime('%y-%m'),
            invoice3.created_at.strftime('%d/%m/%Y'),
            invoice3.date_due.strftime('%d/%m/%Y'),
        ),
    ]

    resp.form['date_end'] = now().date() + datetime.timedelta(days=1)
    resp = resp.form.submit()
    assert [PyQuery(tr).text() for tr in resp.pyquery('tr')] == [
        'Invoice F%02d-%s-0000003 dated %s addressed to First1 Name1 (Due date %s), remaining amount 2.00€, total for the payer 8.20€'
        % (
            regie.pk,
            invoice4.created_at.strftime('%y-%m'),
            invoice4.created_at.strftime('%d/%m/%Y'),
            invoice4.date_due.strftime('%d/%m/%Y'),
        ),
        'Invoice F%02d-%s-0000001 dated %s addressed to First1 Name1 (Due date %s), remaining amount 5.00€, total for the payer 8.20€'
        % (
            regie.pk,
            invoice2.created_at.strftime('%y-%m'),
            invoice2.created_at.strftime('%d/%m/%Y'),
            invoice2.date_due.strftime('%d/%m/%Y'),
        ),
        'Invoice F%02d-%s-0000001 dated %s (created on %s) addressed to First1 Name1 (Due date %s), remaining amount 1.20€, total for the payer 8.20€'
        % (
            regie.pk,
            invoice1.date_invoicing.strftime('%y-%m'),
            invoice1.date_invoicing.strftime('%d/%m/%Y'),
            invoice1.created_at.strftime('%d/%m/%Y'),
            invoice1.date_due.strftime('%d/%m/%Y'),
        ),
        'Invoice F%02d-%s-0000002 dated %s addressed to First2 Name2 (Due date %s), remaining amount 5.00€, total for the payer 5.00€'
        % (
            regie.pk,
            invoice3.created_at.strftime('%y-%m'),
            invoice3.created_at.strftime('%d/%m/%Y'),
            invoice3.date_due.strftime('%d/%m/%Y'),
        ),
    ]

    resp.form['date_end'] = now().date() + datetime.timedelta(days=1)
    resp.form['minimum_threshold'] = 5.5
    resp = resp.form.submit()
    assert [PyQuery(tr).text() for tr in resp.pyquery('tr')] == [
        'Invoice F%02d-%s-0000003 dated %s addressed to First1 Name1 (Due date %s), remaining amount 2.00€, total for the payer 8.20€'
        % (
            regie.pk,
            invoice4.created_at.strftime('%y-%m'),
            invoice4.created_at.strftime('%d/%m/%Y'),
            invoice4.date_due.strftime('%d/%m/%Y'),
        ),
        'Invoice F%02d-%s-0000001 dated %s addressed to First1 Name1 (Due date %s), remaining amount 5.00€, total for the payer 8.20€'
        % (
            regie.pk,
            invoice2.created_at.strftime('%y-%m'),
            invoice2.created_at.strftime('%d/%m/%Y'),
            invoice2.date_due.strftime('%d/%m/%Y'),
        ),
        'Invoice F%02d-%s-0000001 dated %s (created on %s) addressed to First1 Name1 (Due date %s), remaining amount 1.20€, total for the payer 8.20€'
        % (
            regie.pk,
            invoice1.date_invoicing.strftime('%y-%m'),
            invoice1.date_invoicing.strftime('%d/%m/%Y'),
            invoice1.created_at.strftime('%d/%m/%Y'),
            invoice1.date_due.strftime('%d/%m/%Y'),
        ),
    ]


def test_regie_collection_list(app, admin_user):
    regie = Regie.objects.create(label='Foo')
    PaymentType.create_defaults(regie)
    collection1 = CollectionDocket.objects.create(
        regie=regie, date_end=now().date() + datetime.timedelta(days=1), draft=False
    )
    collection1.set_number()
    collection1.save()
    collection2 = CollectionDocket.objects.create(
        regie=regie, date_end=now().date() + datetime.timedelta(days=1), minimum_threshold=5, draft=True
    )

    invoice1 = Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=now().date() - datetime.timedelta(days=2),
        regie=regie,
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Name1',
        collection=collection1,
    )
    invoice1.set_number()
    invoice1.save()
    InvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        invoice=invoice1,
        quantity=1.2,
        unit_amount=1,
    )

    invoice2 = Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=now().date() - datetime.timedelta(days=1),
        regie=regie,
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Name1',
        collection=collection1,
    )
    invoice2.set_number()
    invoice2.save()
    InvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        invoice=invoice2,
        quantity=5,
        unit_amount=1,
    )

    invoice3 = Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=now().date() - datetime.timedelta(days=1),
        regie=regie,
        payer_external_id='payer:2',
        payer_first_name='First2',
        payer_last_name='Name2',
        collection=collection2,
    )
    invoice3.set_number()
    invoice3.save()
    InvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        invoice=invoice3,
        quantity=5,
        unit_amount=1,
    )

    invoice4 = Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=now().date(),
        regie=regie,
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Name1',
        collection=collection2,
    )
    invoice4.set_number()
    invoice4.save()
    line = InvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        invoice=invoice4,
        quantity=3,
        unit_amount=1,
    )
    # partially paid
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
    invoice4.refresh_from_db()
    assert invoice4.remaining_amount == 2
    assert invoice4.paid_amount == 1

    app = login(app)
    resp = app.get('/manage/invoicing/regie/%s/' % regie.pk)
    resp = resp.click('Collections')
    assert [PyQuery(tr).text() for tr in resp.pyquery('tr')] == [
        'Number\nNumber of invoices\nEnd date\nMinimal threshold',
        'TEMPORARY-%s\n2 (7.00€)\n%s\n5.00€' % (collection2.pk, collection2.date_end.strftime('%d/%m/%Y')),
        'T%02d-%s-0000001\n2 (6.20€)\n%s\n0.00€'
        % (regie.pk, collection1.created_at.strftime('%y-%m'), collection1.date_end.strftime('%d/%m/%Y')),
    ]


def test_regie_collection_add(app, admin_user):
    regie = Regie.objects.create(label='Foo')
    PaymentType.create_defaults(regie)
    collection = CollectionDocket.objects.create(regie=regie, date_end=now().date(), draft=False)
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

    invoice1 = Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=now().date() - datetime.timedelta(days=2),
        regie=regie,
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Name1',
        pool=finalized_pool,
    )
    invoice1.set_number()
    invoice1.save()
    InvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        invoice=invoice1,
        quantity=1.2,
        unit_amount=1,
    )

    invoice2 = Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=now().date() - datetime.timedelta(days=1),
        regie=regie,
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Name1',
    )
    invoice2.set_number()
    invoice2.save()
    InvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        invoice=invoice2,
        quantity=5,
        unit_amount=1,
    )

    invoice3 = Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=now().date() - datetime.timedelta(days=1),
        regie=regie,
        payer_external_id='payer:2',
        payer_first_name='First2',
        payer_last_name='Name2',
    )
    invoice3.set_number()
    invoice3.save()
    InvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        invoice=invoice3,
        quantity=5,
        unit_amount=1,
    )

    invoice4 = Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=now().date(),
        regie=regie,
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Name1',
    )
    invoice4.set_number()
    invoice4.save()
    line = InvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        invoice=invoice4,
        quantity=3,
        unit_amount=1,
    )
    # partially paid
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
    invoice4.refresh_from_db()
    assert invoice4.remaining_amount == 2
    assert invoice4.paid_amount == 1

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

    paid_invoice = Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=now().date(),
        regie=regie,
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Name1',
    )
    paid_invoice.set_number()
    paid_invoice.save()
    line = InvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        invoice=paid_invoice,
        quantity=2,
        unit_amount=1,
    )
    payment = Payment.objects.create(
        regie=regie,
        amount=2,
        payment_type=PaymentType.objects.get(regie=regie, slug='cash'),
    )
    InvoiceLinePayment.objects.create(
        payment=payment,
        line=line,
        amount=2,
    )
    paid_invoice.refresh_from_db()
    assert paid_invoice.remaining_amount == 0
    assert paid_invoice.paid_amount == 2

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

    app = login(app)
    resp = app.get('/manage/invoicing/regie/%s/collections/invoices/' % regie.pk)
    resp = resp.click('New collection')
    collection = CollectionDocket.objects.latest('pk')
    assert resp.location.endswith('/manage/invoicing/regie/%s/collection/%s/' % (regie.pk, collection.pk))
    assert collection.regie == regie
    assert collection.draft is True
    assert collection.date_end == now().date()
    assert collection.minimum_threshold == 0
    assert collection.formatted_number == ''
    assert list(collection.invoice_set.all().order_by('-pk')) == [invoice3, invoice2, invoice1]

    resp = app.get('/manage/invoicing/regie/%s/collections/invoices/' % regie.pk)
    assert 'New collection' not in resp
    app.get('/manage/invoicing/regie/%s/collection/add/' % regie.pk, status=404)
    Invoice.objects.filter(collection=collection).update(collection=None)
    collection.delete()

    resp = app.get('/manage/invoicing/regie/%s/collections/invoices/' % regie.pk)
    resp.form['date_end'] = now().date() + datetime.timedelta(days=1)
    resp = resp.form.submit()
    resp = resp.click('New collection')
    collection = CollectionDocket.objects.latest('pk')
    assert resp.location.endswith('/manage/invoicing/regie/%s/collection/%s/' % (regie.pk, collection.pk))
    assert collection.regie == regie
    assert collection.draft is True
    assert collection.date_end == now().date() + datetime.timedelta(days=1)
    assert collection.minimum_threshold == 0
    assert list(collection.invoice_set.all().order_by('-pk')) == [invoice4, invoice3, invoice2, invoice1]

    resp = app.get('/manage/invoicing/regie/%s/collections/invoices/' % regie.pk)
    assert 'New collection' not in resp
    Invoice.objects.filter(collection=collection).update(collection=None)
    collection.delete()

    resp = app.get('/manage/invoicing/regie/%s/collections/invoices/' % regie.pk)
    resp.form['date_end'] = now().date() + datetime.timedelta(days=1)
    resp.form['minimum_threshold'] = 5.5
    resp = resp.form.submit()
    resp = resp.click('New collection')
    collection = CollectionDocket.objects.latest('pk')
    assert resp.location.endswith('/manage/invoicing/regie/%s/collection/%s/' % (regie.pk, collection.pk))
    assert collection.regie == regie
    assert collection.draft is True
    assert collection.date_end == now().date() + datetime.timedelta(days=1)
    assert collection.minimum_threshold == 5.5
    assert list(collection.invoice_set.all().order_by('-pk')) == [invoice4, invoice2, invoice1]

    collection.draft = False
    collection.save()
    resp = app.get('/manage/invoicing/regie/%s/collections/invoices/' % regie.pk)
    assert 'New collection' in resp
    app.get('/manage/invoicing/regie/%s/collection/add/' % regie.pk, status=302)


def test_regie_collection_detail(app, admin_user):
    regie = Regie.objects.create(label='Foo')
    PaymentType.create_defaults(regie)
    collection = CollectionDocket.objects.create(
        regie=regie,
        date_end=now().date() + datetime.timedelta(days=1),
        minimum_threshold=5,
        pay_invoices=True,
        draft=False,
    )
    collection.set_number()
    collection.save()

    invoice1 = Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=now().date() - datetime.timedelta(days=2),
        date_invoicing=datetime.date(2022, 9, 1),
        regie=regie,
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Name1',
        collection=collection,
    )
    invoice1.set_number()
    invoice1.save()
    InvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        invoice=invoice1,
        quantity=1.2,
        unit_amount=1,
    )

    invoice2 = Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=now().date() - datetime.timedelta(days=1),
        regie=regie,
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Name1',
        collection=collection,
    )
    invoice2.set_number()
    invoice2.save()
    InvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        invoice=invoice2,
        quantity=5,
        unit_amount=1,
    )

    invoice3 = Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=now().date() - datetime.timedelta(days=1),
        regie=regie,
        payer_external_id='payer:2',
        payer_first_name='First2',
        payer_last_name='Name2',
        collection=collection,
    )
    invoice3.set_number()
    invoice3.save()
    InvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        invoice=invoice3,
        quantity=5,
        unit_amount=1,
    )

    invoice4 = Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=now().date(),
        regie=regie,
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Name1',
        collection=collection,
    )
    invoice4.set_number()
    invoice4.save()
    line = InvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        invoice=invoice4,
        quantity=3,
        unit_amount=1,
    )
    # partially paid
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
    invoice4.refresh_from_db()
    assert invoice4.remaining_amount == 2
    assert invoice4.paid_amount == 1

    app = login(app)
    resp = app.get('/manage/invoicing/regie/%s/' % regie.pk)
    resp = resp.click('Collections')
    resp = resp.click(collection.formatted_number)
    assert [PyQuery(li).text() for li in resp.pyquery('#main-content li')] == [
        'Total amount: 13.20€',
        'Number of invoices: 4',
        'Minimal threshold: 5.00€',
        'Pay invoices: yes',
    ]
    assert [PyQuery(tr).text() for tr in PyQuery(resp.pyquery('table')).find('tr')] == [
        'Invoice F%02d-%s-0000003 dated %s addressed to First1 Name1 (Due date %s), collected amount 2.00€, total for the payer 8.20€'
        % (
            regie.pk,
            invoice4.created_at.strftime('%y-%m'),
            invoice4.created_at.strftime('%d/%m/%Y'),
            invoice4.date_due.strftime('%d/%m/%Y'),
        ),
        'Invoice F%02d-%s-0000001 dated %s addressed to First1 Name1 (Due date %s), collected amount 5.00€, total for the payer 8.20€'
        % (
            regie.pk,
            invoice2.created_at.strftime('%y-%m'),
            invoice2.created_at.strftime('%d/%m/%Y'),
            invoice2.date_due.strftime('%d/%m/%Y'),
        ),
        'Invoice F%02d-%s-0000001 dated %s (created on %s) addressed to First1 Name1 (Due date %s), collected amount 1.20€, total for the payer 8.20€'
        % (
            regie.pk,
            invoice1.date_invoicing.strftime('%y-%m'),
            invoice1.date_invoicing.strftime('%d/%m/%Y'),
            invoice1.created_at.strftime('%d/%m/%Y'),
            invoice1.date_due.strftime('%d/%m/%Y'),
        ),
        'Invoice F%02d-%s-0000002 dated %s addressed to First2 Name2 (Due date %s), collected amount 5.00€, total for the payer 5.00€'
        % (
            regie.pk,
            invoice3.created_at.strftime('%y-%m'),
            invoice3.created_at.strftime('%d/%m/%Y'),
            invoice3.date_due.strftime('%d/%m/%Y'),
        ),
    ]


def test_regie_collection_edit(app, admin_user):
    regie = Regie.objects.create(label='Foo')
    PaymentType.create_defaults(regie)
    other_collection = CollectionDocket.objects.create(regie=regie, date_end=now().date(), draft=False)
    collection = CollectionDocket.objects.create(regie=regie, date_end=now().date(), draft=True)
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

    invoice1 = Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=now().date() - datetime.timedelta(days=2),
        regie=regie,
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Name1',
        pool=finalized_pool,
    )
    invoice1.set_number()
    invoice1.save()
    InvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        invoice=invoice1,
        quantity=1.2,
        unit_amount=1,
    )

    invoice2 = Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=now().date() - datetime.timedelta(days=1),
        regie=regie,
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Name1',
    )
    invoice2.set_number()
    invoice2.save()
    InvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        invoice=invoice2,
        quantity=5,
        unit_amount=1,
    )

    invoice3 = Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=now().date() - datetime.timedelta(days=1),
        regie=regie,
        payer_external_id='payer:2',
        payer_first_name='First2',
        payer_last_name='Name2',
    )
    invoice3.set_number()
    invoice3.save()
    InvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        invoice=invoice3,
        quantity=5,
        unit_amount=1,
    )

    invoice4 = Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=now().date(),
        regie=regie,
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Name1',
    )
    invoice4.set_number()
    invoice4.save()
    line = InvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        invoice=invoice4,
        quantity=3,
        unit_amount=1,
    )
    # partially paid
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
    invoice4.refresh_from_db()
    assert invoice4.remaining_amount == 2
    assert invoice4.paid_amount == 1

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

    collected_invoice = Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=now().date(),
        regie=regie,
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Name1',
        collection=other_collection,
    )
    collected_invoice.set_number()
    collected_invoice.save()
    InvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        invoice=collected_invoice,
        quantity=2,
        unit_amount=1,
    )

    paid_invoice = Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=now().date(),
        regie=regie,
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Name1',
    )
    paid_invoice.set_number()
    paid_invoice.save()
    line = InvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        invoice=paid_invoice,
        quantity=2,
        unit_amount=1,
    )
    payment = Payment.objects.create(
        regie=regie,
        amount=2,
        payment_type=PaymentType.objects.get(regie=regie, slug='cash'),
    )
    InvoiceLinePayment.objects.create(
        payment=payment,
        line=line,
        amount=2,
    )
    paid_invoice.refresh_from_db()
    assert paid_invoice.remaining_amount == 0
    assert paid_invoice.paid_amount == 2

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

    app = login(app)
    resp = app.get('/manage/invoicing/regie/%s/collection/%s/' % (regie.pk, collection.pk))
    resp = resp.click('Edit')
    resp.form['date_end'] = now().date()
    resp.form['minimum_threshold'] = 0
    resp = resp.form.submit()
    assert resp.location.endswith('/manage/invoicing/regie/%s/collection/%s/' % (regie.pk, collection.pk))
    collection.refresh_from_db()
    assert collection.draft is True
    assert collection.date_end == now().date()
    assert collection.minimum_threshold == 0
    assert collection.pay_invoices is False
    assert collection.formatted_number == ''
    assert list(collection.invoice_set.all().order_by('-pk')) == [invoice3, invoice2, invoice1]

    resp = app.get('/manage/invoicing/regie/%s/collection/%s/edit/' % (regie.pk, collection.pk))
    resp.form['date_end'] = now().date() + datetime.timedelta(days=1)
    resp.form['minimum_threshold'] = 5
    resp.form['pay_invoices'] = True
    resp = resp.form.submit()
    assert resp.location.endswith('/manage/invoicing/regie/%s/collection/%s/' % (regie.pk, collection.pk))
    collection.refresh_from_db()
    assert collection.draft is True
    assert collection.date_end == now().date() + datetime.timedelta(days=1)
    assert collection.minimum_threshold == 5
    assert collection.pay_invoices is True
    assert collection.formatted_number == ''
    assert list(collection.invoice_set.all().order_by('-pk')) == [invoice4, invoice3, invoice2, invoice1]

    collection.draft = False
    collection.save()
    app.get('/manage/invoicing/regie/%s/collection/%s/edit/' % (regie.pk, collection.pk), status=404)


@pytest.mark.parametrize('pay_invoices', [True, False])
def test_regie_collection_validate(app, admin_user, pay_invoices):
    regie = Regie.objects.create(label='Foo')
    PaymentType.create_defaults(regie)
    collection = CollectionDocket.objects.create(
        regie=regie, date_end=now().date(), pay_invoices=pay_invoices, draft=True
    )

    invoice1 = Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=now().date() - datetime.timedelta(days=1),
        regie=regie,
        payer_external_id='payer:2',
        payer_first_name='First2',
        payer_last_name='Name2',
        collection=collection,
    )
    invoice1.set_number()
    invoice1.save()
    line1 = InvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        invoice=invoice1,
        quantity=5,
        unit_amount=1,
    )

    invoice2 = Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=now().date(),
        regie=regie,
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Name1',
        collection=collection,
    )
    invoice2.set_number()
    invoice2.save()
    line2 = InvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        invoice=invoice2,
        quantity=3,
        unit_amount=1,
    )
    # partially paid
    payment = Payment.objects.create(
        regie=regie,
        amount=1,
        payment_type=PaymentType.objects.get(regie=regie, slug='cash'),
    )
    InvoiceLinePayment.objects.create(
        payment=payment,
        line=line2,
        amount=1,
    )
    invoice2.refresh_from_db()
    assert invoice2.remaining_amount == 2
    assert invoice2.paid_amount == 1

    app = login(app)
    resp = app.get('/manage/invoicing/regie/%s/collection/%s/' % (regie.pk, collection.pk))
    resp = resp.click('Validate')
    resp = resp.form.submit()
    assert resp.location.endswith('/manage/invoicing/regie/%s/collection/%s/' % (regie.pk, collection.pk))
    collection.refresh_from_db()
    assert collection.draft is False
    assert collection.formatted_number == 'T%02d-%s-0000001' % (
        regie.pk,
        collection.created_at.strftime('%y-%m'),
    )

    if pay_invoices:
        invoice1.refresh_from_db()
        assert invoice1.remaining_amount == 0
        assert invoice1.paid_amount == 5
        invoice2.refresh_from_db()
        assert invoice2.remaining_amount == 0
        assert invoice2.paid_amount == 3
        assert Payment.objects.count() == 2
        assert InvoiceLinePayment.objects.count() == 3
        payment = Payment.objects.latest('pk')
        assert payment.regie == regie
        assert payment.amount == 7
        assert payment.payment_type.slug == 'collect'
        assert payment.payment_type.regie == regie
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
            payment.created_at.strftime('%y-%m'),
        )
        assert payment.payment_info == {}
        (
            invoice_line_payment1,
            invoice_line_payment2,
        ) = payment.invoicelinepayment_set.order_by('pk')
        assert invoice_line_payment1.amount == 5
        assert invoice_line_payment1.line == line1
        assert invoice_line_payment2.amount == 2
        assert invoice_line_payment2.line == line2
    else:
        invoice1.refresh_from_db()
        assert invoice1.remaining_amount == 5
        assert invoice1.paid_amount == 0
        invoice2.refresh_from_db()
        assert invoice2.remaining_amount == 2
        assert invoice2.paid_amount == 1

    # already validated
    app.get('/manage/invoicing/regie/%s/collection/%s/validate/' % (regie.pk, collection.pk), status=404)

    # invoices are always displayed in detail, even if invoice are paid
    resp = app.get('/manage/invoicing/regie/%s/collection/%s/' % (regie.pk, collection.pk))
    assert [PyQuery(li).text() for li in resp.pyquery('#main-content li')] == [
        'Total amount: 7.00€',
        'Number of invoices: 2',
        'Minimal threshold: 0.00€',
        'Pay invoices: %s' % ('yes' if pay_invoices else 'no'),
    ]
    assert [PyQuery(tr).text() for tr in PyQuery(resp.pyquery('table')).find('tr')] == [
        'Invoice F%02d-%s-0000002 dated %s addressed to First1 Name1 (Due date %s), collected amount 2.00€, total for the payer 2.00€'
        % (
            regie.pk,
            invoice2.created_at.strftime('%y-%m'),
            invoice2.created_at.strftime('%d/%m/%Y'),
            invoice2.date_due.strftime('%d/%m/%Y'),
        ),
        'Invoice F%02d-%s-0000001 dated %s addressed to First2 Name2 (Due date %s), collected amount 5.00€, total for the payer 5.00€'
        % (
            regie.pk,
            invoice1.created_at.strftime('%y-%m'),
            invoice1.created_at.strftime('%d/%m/%Y'),
            invoice1.date_due.strftime('%d/%m/%Y'),
        ),
    ]
    # and amounts are ok in collection listing
    resp = app.get('/manage/invoicing/regie/%s/collections/' % regie.pk)
    assert [PyQuery(tr).text() for tr in resp.pyquery('tr')] == [
        'Number\nNumber of invoices\nEnd date\nMinimal threshold',
        'T%02d-%s-0000001\n2 (7.00€)\n%s\n0.00€'
        % (regie.pk, collection.created_at.strftime('%y-%m'), collection.date_end.strftime('%d/%m/%Y')),
    ]


def test_regie_collection_delete(app, admin_user):
    regie = Regie.objects.create(label='Foo')
    collection = CollectionDocket.objects.create(
        regie=regie, date_end=now().date() + datetime.timedelta(days=1), minimum_threshold=5, draft=False
    )
    collection.set_number()
    collection.save()

    Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=now().date() - datetime.timedelta(days=2),
        regie=regie,
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Name1',
        collection=collection,
    )
    Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=now().date() - datetime.timedelta(days=2),
        regie=regie,
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Name1',
        collection=collection,
    )

    Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=now().date() - datetime.timedelta(days=2),
        regie=regie,
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Name1',
    )

    assert Invoice.objects.filter(collection__isnull=False).count() == 2
    assert Invoice.objects.count() == 3

    app = login(app)
    resp = app.get('/manage/invoicing/regie/%s/collection/%s/' % (regie.pk, collection.pk))
    assert 'Delete' not in resp
    app.get('/manage/invoicing/regie/%s/collection/%s/delete/' % (regie.pk, collection.pk), status=404)

    collection.draft = True
    collection.save()
    resp = app.get('/manage/invoicing/regie/%s/collection/%s/' % (regie.pk, collection.pk))
    resp = resp.click('Delete')
    resp = resp.form.submit()
    assert resp.location.endswith('/manage/invoicing/regie/%s/collections/' % regie.pk)
    assert CollectionDocket.objects.filter(pk=collection.pk).exists() is False
    assert Invoice.objects.filter(collection__isnull=False).count() == 0
    assert Invoice.objects.count() == 3
