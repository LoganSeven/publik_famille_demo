import datetime

import pytest
from django.utils.timezone import now

from lingo.agendas.models import Agenda
from lingo.invoicing.models import (
    Credit,
    CreditAssignment,
    CreditLine,
    Invoice,
    InvoiceLine,
    InvoiceLinePayment,
    Payment,
    PaymentType,
    Regie,
)

pytestmark = pytest.mark.django_db


def test_invoicing_elements_split_params(app, user):
    app.post_json('/api/regie/foo/invoicing-elements/split/', status=403)
    app.authorization = ('Basic', ('john.doe', 'password'))

    app.post_json('/api/regie/foo/invoicing-elements/split/', status=404)

    regie = Regie.objects.create(label='Foo')
    params = {}
    resp = app.post_json('/api/regie/foo/invoicing-elements/split/', params=params, status=400)
    assert resp.json['err_class'] == 'invalid payload'
    assert resp.json['errors'] == {
        'old_agenda': ['This field is required.'],
        'new_agenda': ['This field is required.'],
        'user_external_id': ['This field is required.'],
        'date_start': ['This field is required.'],
        'date_end': ['This field is required.'],
    }

    agenda1 = Agenda.objects.create(label='Agenda 1')
    agenda2 = Agenda.objects.create(label='Agenda 2')
    params = {
        'old_agenda': 'agenda-1',
        'new_agenda': 'agenda-2',
        'user_external_id': 'user:1',
        'date_start': '2022-10-15',
        'date_end': '2022-10-01',
    }
    resp = app.post_json('/api/regie/foo/invoicing-elements/split/', params=params, status=400)
    assert resp.json['err_class'] == 'invalid payload'
    assert resp.json['errors'] == {
        'old_agenda': ['Object with slug=agenda-1 does not exist.'],
        'new_agenda': ['Object with slug=agenda-2 does not exist.'],
    }

    other_regie = Regie.objects.create(label='Other Foo')
    agenda1.regie = regie
    agenda1.save()
    agenda2.regie = other_regie
    agenda2.save()
    resp = app.post_json('/api/regie/foo/invoicing-elements/split/', params=params, status=400)
    assert resp.json['err_class'] == 'invalid payload'
    assert resp.json['errors'] == {
        'new_agenda': ['Object with slug=agenda-2 does not exist.'],
    }

    agenda2.regie = regie
    agenda2.save()
    resp = app.post_json('/api/regie/foo/invoicing-elements/split/', params=params, status=400)
    assert resp.json['err_class'] == 'invalid payload'
    assert resp.json['errors'] == {
        'date_start': ['date_start must be before date_end.'],
    }


def test_invoicing_elements_lines_out_of_scope(app, user):
    app.authorization = ('Basic', ('john.doe', 'password'))
    regie = Regie.objects.create(label='Foo')
    Agenda.objects.create(label='Agenda 1', regie=regie)
    Agenda.objects.create(label='Agenda 2', regie=regie)
    params = {
        'old_agenda': 'agenda-1',
        'new_agenda': 'agenda-2',
        'user_external_id': 'user:1',
        'date_start': '2022-09-15',
        'date_end': '2022-10-01',
    }

    # no invoicing elements to split
    resp = app.post_json('/api/regie/foo/invoicing-elements/split/', params=params)
    assert resp.json == {'err': 0, 'invoicing_elements': []}

    # invoicing elements out of period
    invoice = Invoice.objects.create(
        label='My invoice',
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=now().date(),
        regie=regie,
        payer_external_id='payer:1',
    )
    InvoiceLine.objects.create(
        event_date=now().date(),
        label='A line',
        invoice=invoice,
        quantity=1,
        unit_amount=42,
        user_external_id='user:1',
        details={'dates': ['2022-09-14']},
    )
    InvoiceLine.objects.create(
        event_date=now().date(),
        label='A line',
        invoice=invoice,
        quantity=1,
        unit_amount=42,
        user_external_id='user:1',
        details={'dates': ['2022-10-01']},
    )
    credit = Credit.objects.create(
        label='My credit',
        date_publication=datetime.date(2022, 10, 1),
        regie=regie,
        payer_external_id='payer:1',
    )
    CreditLine.objects.create(
        event_date=now().date(),
        label='A line',
        credit=credit,
        quantity=1,
        unit_amount=42,
        user_external_id='user:1',
        agenda_slug='agenda-1',
        event_slug='agenda-1@event-1',
        details={'dates': ['2022-09-14']},
    )
    CreditLine.objects.create(
        event_date=now().date(),
        label='A line',
        credit=credit,
        quantity=1,
        unit_amount=42,
        user_external_id='user:1',
        agenda_slug='agenda-1',
        event_slug='agenda-1@event-1',
        details={'dates': ['2022-10-01']},
    )
    resp = app.post_json('/api/regie/foo/invoicing-elements/split/', params=params)
    assert resp.json == {'err': 0, 'invoicing_elements': []}

    # wrong user_external_id
    InvoiceLine.objects.create(
        event_date=now().date(),
        label='A line',
        invoice=invoice,
        quantity=1,
        unit_amount=42,
        user_external_id='user:2',
        agenda_slug='agenda-1',
        event_slug='agenda-1@event-1',
        details={'dates': ['2022-09-15']},
    )
    CreditLine.objects.create(
        event_date=now().date(),
        label='A line',
        credit=credit,
        quantity=1,
        unit_amount=42,
        user_external_id='user:2',
        agenda_slug='agenda-1',
        event_slug='agenda-1@event-1',
        details={'dates': ['2022-09-15']},
    )
    resp = app.post_json('/api/regie/foo/invoicing-elements/split/', params=params)
    assert resp.json == {'err': 0, 'invoicing_elements': []}

    # wrong regie
    other_regie = Regie.objects.create(label='Other regie')
    other_invoice = Invoice.objects.create(
        label='My invoice',
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=now().date(),
        regie=other_regie,
        payer_external_id='payer:1',
    )
    InvoiceLine.objects.create(
        event_date=now().date(),
        label='A line',
        invoice=other_invoice,
        quantity=1,
        unit_amount=42,
        user_external_id='user:1',
        agenda_slug='agenda-1',
        event_slug='agenda-1@event-1',
        details={'dates': ['2022-09-15']},
    )
    other_credit = Credit.objects.create(
        label='My credit',
        date_publication=datetime.date(2022, 10, 1),
        regie=other_regie,
        payer_external_id='payer:1',
    )
    CreditLine.objects.create(
        event_date=now().date(),
        label='A line',
        credit=other_credit,
        quantity=1,
        unit_amount=42,
        user_external_id='user:1',
        agenda_slug='agenda-1',
        event_slug='agenda-1@event-1',
        details={'dates': ['2022-09-15']},
    )
    resp = app.post_json('/api/regie/foo/invoicing-elements/split/', params=params)
    assert resp.json == {'err': 0, 'invoicing_elements': []}

    # wrong agenda
    InvoiceLine.objects.create(
        event_date=now().date(),
        label='A line',
        invoice=invoice,
        quantity=1,
        unit_amount=42,
        user_external_id='user:1',
        agenda_slug='agenda-2',
        event_slug='agenda-2@event-1',
        details={'dates': ['2022-09-15']},
    )
    CreditLine.objects.create(
        event_date=now().date(),
        label='A line',
        credit=credit,
        quantity=1,
        unit_amount=42,
        user_external_id='user:1',
        agenda_slug='agenda-2',
        event_slug='agenda-2@event-1',
        details={'dates': ['2022-09-15']},
    )
    resp = app.post_json('/api/regie/foo/invoicing-elements/split/', params=params)
    assert resp.json == {'err': 0, 'invoicing_elements': []}
    InvoiceLine.objects.create(
        event_date=now().date(),
        label='A line',
        invoice=invoice,
        quantity=1,
        unit_amount=42,
        user_external_id='user:1',
        agenda_slug='agenda-1',
        event_slug='agenda-2@event-1',
        details={'dates': ['2022-09-15']},
    )
    CreditLine.objects.create(
        event_date=now().date(),
        label='A line',
        credit=credit,
        quantity=1,
        unit_amount=42,
        user_external_id='user:1',
        agenda_slug='agenda-1',
        event_slug='agenda-2@event-1',
        details={'dates': ['2022-09-15']},
    )
    resp = app.post_json('/api/regie/foo/invoicing-elements/split/', params=params)
    assert resp.json == {'err': 0, 'invoicing_elements': []}

    # invoice cancelled
    invoice = Invoice.objects.create(
        label='My invoice',
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=now().date(),
        regie=regie,
        payer_external_id='payer:1',
        cancelled_at=now(),
    )
    InvoiceLine.objects.create(
        event_date=now().date(),
        label='A line',
        invoice=invoice,
        quantity=1,
        unit_amount=42,
        user_external_id='user:1',
        agenda_slug='agenda-1',
        event_slug='agenda-1@event-1',
        details={'dates': ['2022-09-15']},
    )
    resp = app.post_json('/api/regie/foo/invoicing-elements/split/', params=params)
    assert resp.json == {'err': 0, 'invoicing_elements': []}

    # credit cancelled
    credit = Credit.objects.create(
        label='My credit',
        date_publication=datetime.date(2022, 10, 1),
        regie=regie,
        payer_external_id='payer:1',
        cancelled_at=now(),
    )
    CreditLine.objects.create(
        event_date=now().date(),
        label='A line',
        credit=credit,
        quantity=1,
        unit_amount=42,
        user_external_id='user:1',
        agenda_slug='agenda-1',
        event_slug='agenda-1@event-1',
        details={'dates': ['2022-09-15']},
    )
    resp = app.post_json('/api/regie/foo/invoicing-elements/split/', params=params)
    assert resp.json == {'err': 0, 'invoicing_elements': []}


def test_invoicing_elements_dates_only_in_period_invoice_without_payment(app, user):
    app.authorization = ('Basic', ('john.doe', 'password'))
    regie = Regie.objects.create(label='Foo')
    Agenda.objects.create(label='Agenda 1', regie=regie)
    Agenda.objects.create(label='Agenda 2', regie=regie)
    params = {
        'old_agenda': 'agenda-1',
        'new_agenda': 'agenda-2',
        'user_external_id': 'user:1',
        'date_start': '2022-09-15',
        'date_end': '2022-10-01',
    }

    invoice = Invoice.objects.create(
        label='My invoice',
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=now().date(),
        regie=regie,
        payer_external_id='payer:1',
    )
    line = InvoiceLine.objects.create(
        event_date=now().date(),
        label='A line',
        invoice=invoice,
        quantity=2,
        unit_amount=4,
        user_external_id='user:1',
        agenda_slug='agenda-1',
        activity_label='Agenda 1',
        event_slug='agenda-1@event-1',
        description='Blablabla 15/09, 16/09',
        details={'dates': ['2022-09-15', '2022-09-16']},
    )
    resp = app.post_json('/api/regie/foo/invoicing-elements/split/', params=params)
    assert resp.json == {
        'err': 0,
        'invoicing_elements': [
            {'invoice_id': str(invoice.uuid), 'line_id': str(line.uuid), 'action': 'moved'}
        ],
    }
    line.refresh_from_db()
    assert line.quantity == 2
    assert line.agenda_slug == 'agenda-2'
    assert line.activity_label == 'Agenda 2'
    assert line.event_slug == 'agenda-2@event-1'
    assert line.description == 'Blablabla 15/09, 16/09'
    assert line.details == {'dates': ['2022-09-15', '2022-09-16']}


def test_invoicing_elements_dates_only_in_period_credit_without_assignment(app, user):
    app.authorization = ('Basic', ('john.doe', 'password'))
    regie = Regie.objects.create(label='Foo')
    Agenda.objects.create(label='Agenda 1', regie=regie)
    Agenda.objects.create(label='Agenda 2', regie=regie)
    params = {
        'old_agenda': 'agenda-1',
        'new_agenda': 'agenda-2',
        'user_external_id': 'user:1',
        'date_start': '2022-09-15',
        'date_end': '2022-10-01',
    }

    credit = Credit.objects.create(
        label='My credit',
        date_publication=datetime.date(2022, 10, 1),
        regie=regie,
        payer_external_id='payer:1',
    )
    line = CreditLine.objects.create(
        event_date=now().date(),
        label='A line',
        credit=credit,
        quantity=-2,
        unit_amount=4,
        user_external_id='user:1',
        agenda_slug='agenda-1',
        activity_label='Agenda 1',
        event_slug='agenda-1@event-1',
        description='Blablabla 15/09, 16/09',
        details={'dates': ['2022-09-15', '2022-09-16']},
    )
    resp = app.post_json('/api/regie/foo/invoicing-elements/split/', params=params)
    assert resp.json == {
        'err': 0,
        'invoicing_elements': [{'credit_id': str(credit.uuid), 'line_id': str(line.uuid), 'action': 'moved'}],
    }
    line.refresh_from_db()
    assert line.quantity == -2
    assert line.agenda_slug == 'agenda-2'
    assert line.activity_label == 'Agenda 2'
    assert line.event_slug == 'agenda-2@event-1'
    assert line.description == 'Blablabla 15/09, 16/09'
    assert line.details == {'dates': ['2022-09-15', '2022-09-16']}


def test_invoicing_elements_dates_only_in_period_invoice_with_payment(app, user):
    app.authorization = ('Basic', ('john.doe', 'password'))
    regie = Regie.objects.create(label='Foo')
    PaymentType.create_defaults(regie)
    Agenda.objects.create(label='Agenda 1', regie=regie)
    Agenda.objects.create(label='Agenda 2', regie=regie)
    params = {
        'old_agenda': 'agenda-1',
        'new_agenda': 'agenda-2',
        'user_external_id': 'user:1',
        'date_start': '2022-09-15',
        'date_end': '2022-10-01',
    }

    invoice = Invoice.objects.create(
        label='My invoice',
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=now().date(),
        regie=regie,
        payer_external_id='payer:1',
    )
    line = InvoiceLine.objects.create(
        event_date=now().date(),
        label='A line',
        invoice=invoice,
        quantity=-2,
        unit_amount=4,
        user_external_id='user:1',
        agenda_slug='agenda-1',
        activity_label='Agenda 1',
        event_slug='agenda-1@event-1',
        description='Blablabla 15/09, 16/09',
        details={'dates': ['2022-09-15', '2022-09-16']},
    )
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
    resp = app.post_json('/api/regie/foo/invoicing-elements/split/', params=params)
    assert resp.json == {
        'err': 0,
        'invoicing_elements': [
            {'invoice_id': str(invoice.uuid), 'line_id': str(line.uuid), 'action': 'moved'}
        ],
    }
    line.refresh_from_db()
    assert line.quantity == -2
    assert line.agenda_slug == 'agenda-2'
    assert line.activity_label == 'Agenda 2'
    assert line.event_slug == 'agenda-2@event-1'
    assert line.description == 'Blablabla 15/09, 16/09'
    assert line.details == {'dates': ['2022-09-15', '2022-09-16']}
    invoice_line_payment.refresh_from_db()
    assert invoice_line_payment.line == line


def test_invoicing_elements_dates_only_in_period_credit_with_assignment(app, user):
    app.authorization = ('Basic', ('john.doe', 'password'))
    regie = Regie.objects.create(label='Foo')
    PaymentType.create_defaults(regie)
    Agenda.objects.create(label='Agenda 1', regie=regie)
    Agenda.objects.create(label='Agenda 2', regie=regie)
    params = {
        'old_agenda': 'agenda-1',
        'new_agenda': 'agenda-2',
        'user_external_id': 'user:1',
        'date_start': '2022-09-15',
        'date_end': '2022-10-01',
    }

    invoice = Invoice.objects.create(
        label='My invoice',
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=now().date(),
        regie=regie,
        payer_external_id='payer:1',
    )
    credit = Credit.objects.create(
        label='My credit',
        date_publication=datetime.date(2022, 10, 1),
        regie=regie,
        payer_external_id='payer:1',
    )
    line = CreditLine.objects.create(
        event_date=now().date(),
        label='A line',
        credit=credit,
        quantity=2,
        unit_amount=4,
        user_external_id='user:1',
        agenda_slug='agenda-1',
        activity_label='Agenda 1',
        event_slug='agenda-1@event-1',
        description='Blablabla 15/09, 16/09',
        details={'dates': ['2022-09-15', '2022-09-16']},
    )
    payment = Payment.objects.create(
        regie=regie,
        amount=1,
        payment_type=PaymentType.objects.get(regie=regie, slug='credit'),
    )
    CreditAssignment.objects.create(
        invoice=invoice,
        payment=payment,
        credit=credit,
        amount=1,
    )
    resp = app.post_json('/api/regie/foo/invoicing-elements/split/', params=params)
    assert resp.json == {
        'err': 0,
        'invoicing_elements': [{'credit_id': str(credit.uuid), 'line_id': str(line.uuid), 'action': 'moved'}],
    }
    line.refresh_from_db()
    assert line.quantity == 2
    assert line.agenda_slug == 'agenda-2'
    assert line.activity_label == 'Agenda 2'
    assert line.event_slug == 'agenda-2@event-1'
    assert line.description == 'Blablabla 15/09, 16/09'
    assert line.details == {'dates': ['2022-09-15', '2022-09-16']}


def test_invoicing_elements_dates_not_only_in_period_invoice_without_payment(app, user):
    app.authorization = ('Basic', ('john.doe', 'password'))
    regie = Regie.objects.create(label='Foo')
    PaymentType.create_defaults(regie)
    Agenda.objects.create(label='Agenda 1', regie=regie)
    Agenda.objects.create(label='Agenda 2', regie=regie)
    params = {
        'old_agenda': 'agenda-1',
        'new_agenda': 'agenda-2',
        'user_external_id': 'user:1',
        'date_start': '2022-09-15',
        'date_end': '2022-10-01',
    }

    invoice = Invoice.objects.create(
        label='My invoice',
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=now().date(),
        regie=regie,
        payer_external_id='payer:1',
    )
    line1 = InvoiceLine.objects.create(
        event_date=now().date(),
        label='A line 1',
        invoice=invoice,
        quantity=5,
        unit_amount=4,
        user_external_id='user:1',
        agenda_slug='agenda-1',
        activity_label='Agenda 1',
        event_slug='agenda-1@event-1',
        description='Blablabla 14/09, 15/09, 16/09, 16/09, 01/10',
        details={'dates': ['2022-09-14', '2022-09-15', '2022-09-16', '2022-09-16', '2022-10-01']},
    )
    line2 = InvoiceLine.objects.create(
        event_date=now().date(),
        label='A line 2',
        invoice=invoice,
        quantity=2,
        unit_amount=4,
        user_external_id='user:1',
        agenda_slug='agenda-1',
        activity_label='Agenda 1',
        event_slug='agenda-1@event-1',
        description='Blablabla no dates',
        details={'dates': ['2022-09-14', '2022-09-15']},
    )
    line3 = InvoiceLine.objects.create(
        event_date=now().date(),
        label='A line 3',
        invoice=invoice,
        quantity=2,
        unit_amount=4,
        user_external_id='user:1',
        agenda_slug='agenda-1',
        activity_label='Agenda 1',
        event_slug='agenda-1@event-1',
        description='14/09, 15/09',
        details={'dates': ['2022-09-14', '2022-09-15']},
    )
    line4 = InvoiceLine.objects.create(
        event_date=now().date(),
        label='A line 4',
        invoice=invoice,
        quantity=2,
        unit_amount=4,
        user_external_id='user:1',
        agenda_slug='agenda-1',
        activity_label='Agenda 1',
        event_slug='agenda-1@event-1',
        description='30/09, 01/10',
        details={'dates': ['2022-09-30', '2022-10-01']},
    )
    resp = app.post_json('/api/regie/foo/invoicing-elements/split/', params=params)
    new_line1 = InvoiceLine.objects.filter(label='A line 1').latest('pk')
    new_line2 = InvoiceLine.objects.filter(label='A line 2').latest('pk')
    new_line3 = InvoiceLine.objects.filter(label='A line 3').latest('pk')
    new_line4 = InvoiceLine.objects.filter(label='A line 4').latest('pk')
    assert resp.json == {
        'err': 0,
        'invoicing_elements': [
            {'invoice_id': str(invoice.uuid), 'line_id': str(line1.uuid), 'action': 'updated'},
            {'invoice_id': str(invoice.uuid), 'line_id': str(new_line1.uuid), 'action': 'created'},
            {'invoice_id': str(invoice.uuid), 'line_id': str(line2.uuid), 'action': 'updated'},
            {'invoice_id': str(invoice.uuid), 'line_id': str(new_line2.uuid), 'action': 'created'},
            {'invoice_id': str(invoice.uuid), 'line_id': str(line3.uuid), 'action': 'updated'},
            {'invoice_id': str(invoice.uuid), 'line_id': str(new_line3.uuid), 'action': 'created'},
            {'invoice_id': str(invoice.uuid), 'line_id': str(line4.uuid), 'action': 'updated'},
            {'invoice_id': str(invoice.uuid), 'line_id': str(new_line4.uuid), 'action': 'created'},
        ],
    }
    line1.refresh_from_db()
    line2.refresh_from_db()
    line3.refresh_from_db()
    line4.refresh_from_db()
    assert line1.quantity == 2
    assert line1.agenda_slug == 'agenda-1'
    assert line1.activity_label == 'Agenda 1'
    assert line1.event_slug == 'agenda-1@event-1'
    assert line1.description == 'Blablabla 14/09, 01/10'
    assert line1.details == {'dates': ['2022-09-14', '2022-10-01']}
    assert new_line1.quantity == 3
    assert new_line1.agenda_slug == 'agenda-2'
    assert new_line1.activity_label == 'Agenda 2'
    assert new_line1.event_slug == 'agenda-2@event-1'
    assert new_line1.description == 'Blablabla 15/09, 16/09, 16/09'
    assert new_line1.details == {'dates': ['2022-09-15', '2022-09-16', '2022-09-16']}
    assert line2.quantity == 1
    assert line2.agenda_slug == 'agenda-1'
    assert line2.activity_label == 'Agenda 1'
    assert line2.event_slug == 'agenda-1@event-1'
    assert line2.description == 'Blablabla no dates'
    assert line2.details == {'dates': ['2022-09-14']}
    assert new_line2.quantity == 1
    assert new_line2.agenda_slug == 'agenda-2'
    assert new_line2.activity_label == 'Agenda 2'
    assert new_line2.event_slug == 'agenda-2@event-1'
    assert new_line2.description == 'Blablabla no dates'
    assert new_line2.details == {'dates': ['2022-09-15']}
    assert line3.quantity == 1
    assert line3.agenda_slug == 'agenda-1'
    assert line3.activity_label == 'Agenda 1'
    assert line3.event_slug == 'agenda-1@event-1'
    assert line3.description == '14/09'
    assert line3.details == {'dates': ['2022-09-14']}
    assert new_line3.quantity == 1
    assert new_line3.agenda_slug == 'agenda-2'
    assert new_line3.activity_label == 'Agenda 2'
    assert new_line3.event_slug == 'agenda-2@event-1'
    assert new_line3.description == '15/09'
    assert new_line3.details == {'dates': ['2022-09-15']}
    assert line4.quantity == 1
    assert line4.agenda_slug == 'agenda-1'
    assert line4.activity_label == 'Agenda 1'
    assert line4.event_slug == 'agenda-1@event-1'
    assert line4.description == '01/10'
    assert line4.details == {'dates': ['2022-10-01']}
    assert new_line4.quantity == 1
    assert new_line4.agenda_slug == 'agenda-2'
    assert new_line4.activity_label == 'Agenda 2'
    assert new_line4.event_slug == 'agenda-2@event-1'
    assert new_line4.description == '30/09'
    assert new_line4.details == {'dates': ['2022-09-30']}


def test_invoicing_elements_dates_not_only_in_period_credit_without_payment(app, user):
    app.authorization = ('Basic', ('john.doe', 'password'))
    regie = Regie.objects.create(label='Foo')
    PaymentType.create_defaults(regie)
    Agenda.objects.create(label='Agenda 1', regie=regie)
    Agenda.objects.create(label='Agenda 2', regie=regie)
    params = {
        'old_agenda': 'agenda-1',
        'new_agenda': 'agenda-2',
        'user_external_id': 'user:1',
        'date_start': '2022-09-15',
        'date_end': '2022-10-01',
    }

    credit = Credit.objects.create(
        label='My credit',
        date_publication=datetime.date(2022, 10, 1),
        regie=regie,
        payer_external_id='payer:1',
    )
    line = CreditLine.objects.create(
        event_date=now().date(),
        label='A line',
        credit=credit,
        quantity=-5,
        unit_amount=4,
        user_external_id='user:1',
        agenda_slug='agenda-1',
        activity_label='Agenda 1',
        event_slug='agenda-1@event-1',
        description='Blablabla 14/09, 15/09, 16/09, 16/09, 01/10',
        details={'dates': ['2022-09-14', '2022-09-15', '2022-09-16', '2022-09-16', '2022-10-01']},
    )
    resp = app.post_json('/api/regie/foo/invoicing-elements/split/', params=params)
    new_line = CreditLine.objects.latest('pk')
    assert resp.json == {
        'err': 0,
        'invoicing_elements': [
            {'credit_id': str(credit.uuid), 'line_id': str(line.uuid), 'action': 'updated'},
            {'credit_id': str(credit.uuid), 'line_id': str(new_line.uuid), 'action': 'created'},
        ],
    }
    line.refresh_from_db()
    assert line.quantity == -2
    assert line.agenda_slug == 'agenda-1'
    assert line.activity_label == 'Agenda 1'
    assert line.event_slug == 'agenda-1@event-1'
    assert line.description == 'Blablabla 14/09, 01/10'
    assert line.details == {'dates': ['2022-09-14', '2022-10-01']}
    assert new_line.quantity == -3
    assert new_line.agenda_slug == 'agenda-2'
    assert new_line.activity_label == 'Agenda 2'
    assert new_line.event_slug == 'agenda-2@event-1'
    assert new_line.description == 'Blablabla 15/09, 16/09, 16/09'
    assert new_line.details == {'dates': ['2022-09-15', '2022-09-16', '2022-09-16']}


def test_invoicing_elements_dates_not_only_in_period_invoice_with_payment(app, user):
    app.authorization = ('Basic', ('john.doe', 'password'))
    regie = Regie.objects.create(label='Foo')
    PaymentType.create_defaults(regie)
    Agenda.objects.create(label='Agenda 1', regie=regie)
    Agenda.objects.create(label='Agenda 2', regie=regie)
    params = {
        'old_agenda': 'agenda-1',
        'new_agenda': 'agenda-2',
        'user_external_id': 'user:1',
        'date_start': '2022-09-15',
        'date_end': '2022-10-01',
    }

    # payment line with amount greater than line new amount
    invoice = Invoice.objects.create(
        label='My invoice',
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=now().date(),
        regie=regie,
        payer_external_id='payer:1',
    )
    line = InvoiceLine.objects.create(
        event_date=now().date(),
        label='A line',
        invoice=invoice,
        quantity=5,
        unit_amount=4,
        user_external_id='user:1',
        agenda_slug='agenda-1',
        activity_label='Agenda 1',
        event_slug='agenda-1@event-1',
        description='Blablabla 14/09, 15/09, 16/09, 16/09, 01/10',
        details={'dates': ['2022-09-14', '2022-09-15', '2022-09-16', '2022-09-16', '2022-10-01']},
    )
    payment = Payment.objects.create(
        regie=regie,
        amount=9,
        payment_type=PaymentType.objects.get(regie=regie, slug='cash'),
    )
    invoice_line_payment = InvoiceLinePayment.objects.create(
        payment=payment,
        line=line,
        amount=9,
    )
    resp = app.post_json('/api/regie/foo/invoicing-elements/split/', params=params)
    new_line = InvoiceLine.objects.latest('pk')
    assert resp.json == {
        'err': 0,
        'invoicing_elements': [
            {'invoice_id': str(invoice.uuid), 'line_id': str(line.uuid), 'action': 'updated'},
            {'invoice_id': str(invoice.uuid), 'line_id': str(new_line.uuid), 'action': 'created'},
        ],
    }
    line.refresh_from_db()
    assert line.quantity == 2
    assert line.agenda_slug == 'agenda-1'
    assert line.activity_label == 'Agenda 1'
    assert line.event_slug == 'agenda-1@event-1'
    assert line.description == 'Blablabla 14/09, 01/10'
    assert line.details == {'dates': ['2022-09-14', '2022-10-01']}
    assert new_line.quantity == 3
    assert new_line.agenda_slug == 'agenda-2'
    assert new_line.activity_label == 'Agenda 2'
    assert new_line.event_slug == 'agenda-2@event-1'
    assert new_line.description == 'Blablabla 15/09, 16/09, 16/09'
    assert new_line.details == {'dates': ['2022-09-15', '2022-09-16', '2022-09-16']}
    invoice_line_payment.refresh_from_db()
    assert invoice_line_payment.payment == payment
    assert invoice_line_payment.line == line
    assert invoice_line_payment.amount == 8
    new_invoice_line_payment = InvoiceLinePayment.objects.latest('pk')
    assert new_invoice_line_payment.payment == payment
    assert new_invoice_line_payment.line == new_line
    assert new_invoice_line_payment.amount == 1

    # payment line with amount equal to line future amount
    invoice = Invoice.objects.create(
        label='My invoice',
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=now().date(),
        regie=regie,
        payer_external_id='payer:1',
    )
    line = InvoiceLine.objects.create(
        event_date=now().date(),
        label='A line',
        invoice=invoice,
        quantity=5,
        unit_amount=4,
        user_external_id='user:1',
        agenda_slug='agenda-1',
        activity_label='Agenda 1',
        event_slug='agenda-1@event-1',
        description='Blablabla 14/09, 15/09, 16/09, 16/09, 01/10',
        details={'dates': ['2022-09-14', '2022-09-15', '2022-09-16', '2022-09-16', '2022-10-01']},
    )
    payment = Payment.objects.create(
        regie=regie,
        amount=8,
        payment_type=PaymentType.objects.get(regie=regie, slug='cash'),
    )
    invoice_line_payment = InvoiceLinePayment.objects.create(
        payment=payment,
        line=line,
        amount=8,
    )
    resp = app.post_json('/api/regie/foo/invoicing-elements/split/', params=params)
    new_line = InvoiceLine.objects.latest('pk')
    assert resp.json == {
        'err': 0,
        'invoicing_elements': [
            {'invoice_id': str(invoice.uuid), 'line_id': str(line.uuid), 'action': 'updated'},
            {'invoice_id': str(invoice.uuid), 'line_id': str(new_line.uuid), 'action': 'created'},
        ],
    }
    line.refresh_from_db()
    assert line.quantity == 2
    assert line.agenda_slug == 'agenda-1'
    assert line.activity_label == 'Agenda 1'
    assert line.event_slug == 'agenda-1@event-1'
    assert line.description == 'Blablabla 14/09, 01/10'
    assert line.details == {'dates': ['2022-09-14', '2022-10-01']}
    assert new_line.quantity == 3
    assert new_line.agenda_slug == 'agenda-2'
    assert new_line.activity_label == 'Agenda 2'
    assert new_line.event_slug == 'agenda-2@event-1'
    assert new_line.description == 'Blablabla 15/09, 16/09, 16/09'
    assert new_line.details == {'dates': ['2022-09-15', '2022-09-16', '2022-09-16']}
    invoice_line_payment.refresh_from_db()
    assert invoice_line_payment.line == line
    assert invoice_line_payment.amount == 8
    assert invoice_line_payment.payment == payment

    # payment line with amount less than line new amount
    invoice = Invoice.objects.create(
        label='My invoice',
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=now().date(),
        regie=regie,
        payer_external_id='payer:1',
    )
    line = InvoiceLine.objects.create(
        event_date=now().date(),
        label='A line',
        invoice=invoice,
        quantity=5,
        unit_amount=4,
        user_external_id='user:1',
        agenda_slug='agenda-1',
        activity_label='Agenda 1',
        event_slug='agenda-1@event-1',
        description='Blablabla 14/09, 15/09, 16/09, 16/09, 01/10',
        details={'dates': ['2022-09-14', '2022-09-15', '2022-09-16', '2022-09-16', '2022-10-01']},
    )
    payment = Payment.objects.create(
        regie=regie,
        amount=7,
        payment_type=PaymentType.objects.get(regie=regie, slug='cash'),
    )
    invoice_line_payment = InvoiceLinePayment.objects.create(
        payment=payment,
        line=line,
        amount=7,
    )
    resp = app.post_json('/api/regie/foo/invoicing-elements/split/', params=params)
    new_line = InvoiceLine.objects.latest('pk')
    assert resp.json == {
        'err': 0,
        'invoicing_elements': [
            {'invoice_id': str(invoice.uuid), 'line_id': str(line.uuid), 'action': 'updated'},
            {'invoice_id': str(invoice.uuid), 'line_id': str(new_line.uuid), 'action': 'created'},
        ],
    }
    line.refresh_from_db()
    assert line.quantity == 2
    assert line.agenda_slug == 'agenda-1'
    assert line.activity_label == 'Agenda 1'
    assert line.event_slug == 'agenda-1@event-1'
    assert line.description == 'Blablabla 14/09, 01/10'
    assert line.details == {'dates': ['2022-09-14', '2022-10-01']}
    assert new_line.quantity == 3
    assert new_line.agenda_slug == 'agenda-2'
    assert new_line.activity_label == 'Agenda 2'
    assert new_line.event_slug == 'agenda-2@event-1'
    assert new_line.description == 'Blablabla 15/09, 16/09, 16/09'
    assert new_line.details == {'dates': ['2022-09-15', '2022-09-16', '2022-09-16']}
    invoice_line_payment.refresh_from_db()
    assert invoice_line_payment.line == line
    assert invoice_line_payment.amount == 7
    assert invoice_line_payment.payment == payment

    # several little payments
    invoice = Invoice.objects.create(
        label='My invoice',
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=now().date(),
        regie=regie,
        payer_external_id='payer:1',
    )
    line = InvoiceLine.objects.create(
        event_date=now().date(),
        label='A line',
        invoice=invoice,
        quantity=5,
        unit_amount=4,
        user_external_id='user:1',
        agenda_slug='agenda-1',
        activity_label='Agenda 1',
        event_slug='agenda-1@event-1',
        description='Blablabla 14/09, 15/09, 16/09, 16/09, 01/10',
        details={'dates': ['2022-09-14', '2022-09-15', '2022-09-16', '2022-09-16', '2022-10-01']},
    )
    payment1 = Payment.objects.create(
        regie=regie,
        amount=1,
        payment_type=PaymentType.objects.get(regie=regie, slug='cash'),
    )
    invoice_line_payment1 = InvoiceLinePayment.objects.create(
        payment=payment1,
        line=line,
        amount=1,
    )
    payment2 = Payment.objects.create(
        regie=regie,
        amount=6,
        payment_type=PaymentType.objects.get(regie=regie, slug='cash'),
    )
    invoice_line_payment2 = InvoiceLinePayment.objects.create(
        payment=payment2,
        line=line,
        amount=6,
    )
    payment3 = Payment.objects.create(
        regie=regie,
        amount=8,
        payment_type=PaymentType.objects.get(regie=regie, slug='cash'),
    )
    invoice_line_payment3 = InvoiceLinePayment.objects.create(
        payment=payment3,
        line=line,
        amount=8,
    )
    payment4 = Payment.objects.create(
        regie=regie,
        amount=5,
        payment_type=PaymentType.objects.get(regie=regie, slug='cash'),
    )
    invoice_line_payment4 = InvoiceLinePayment.objects.create(
        payment=payment4,
        line=line,
        amount=5,
    )
    resp = app.post_json('/api/regie/foo/invoicing-elements/split/', params=params)
    new_line = InvoiceLine.objects.latest('pk')
    assert resp.json == {
        'err': 0,
        'invoicing_elements': [
            {'invoice_id': str(invoice.uuid), 'line_id': str(line.uuid), 'action': 'updated'},
            {'invoice_id': str(invoice.uuid), 'line_id': str(new_line.uuid), 'action': 'created'},
        ],
    }
    line.refresh_from_db()
    assert line.quantity == 2
    assert line.agenda_slug == 'agenda-1'
    assert line.activity_label == 'Agenda 1'
    assert line.event_slug == 'agenda-1@event-1'
    assert line.description == 'Blablabla 14/09, 01/10'
    assert line.details == {'dates': ['2022-09-14', '2022-10-01']}
    assert new_line.quantity == 3
    assert new_line.agenda_slug == 'agenda-2'
    assert new_line.activity_label == 'Agenda 2'
    assert new_line.event_slug == 'agenda-2@event-1'
    assert new_line.description == 'Blablabla 15/09, 16/09, 16/09'
    assert new_line.details == {'dates': ['2022-09-15', '2022-09-16', '2022-09-16']}
    invoice_line_payment1.refresh_from_db()
    assert invoice_line_payment1.line == line
    assert invoice_line_payment1.amount == 1
    assert invoice_line_payment1.payment == payment1
    invoice_line_payment2.refresh_from_db()
    assert invoice_line_payment2.line == line
    assert invoice_line_payment2.amount == 6
    assert invoice_line_payment2.payment == payment2
    invoice_line_payment3.refresh_from_db()
    assert invoice_line_payment3.line == line
    assert invoice_line_payment3.amount == 1
    assert invoice_line_payment3.payment == payment3
    invoice_line_payment4.refresh_from_db()
    assert invoice_line_payment4.line == new_line
    assert invoice_line_payment4.amount == 5
    assert invoice_line_payment4.payment == payment4
    new_invoice_line_payment = InvoiceLinePayment.objects.latest('pk')
    assert new_invoice_line_payment.payment == payment3
    assert new_invoice_line_payment.line == new_line
    assert new_invoice_line_payment.amount == 7


def test_invoicing_elements_dates_not_only_in_period_credit_with_payment(app, user):
    app.authorization = ('Basic', ('john.doe', 'password'))
    regie = Regie.objects.create(label='Foo')
    PaymentType.create_defaults(regie)
    Agenda.objects.create(label='Agenda 1', regie=regie)
    Agenda.objects.create(label='Agenda 2', regie=regie)
    params = {
        'old_agenda': 'agenda-1',
        'new_agenda': 'agenda-2',
        'user_external_id': 'user:1',
        'date_start': '2022-09-15',
        'date_end': '2022-10-01',
    }

    invoice = Invoice.objects.create(
        label='My invoice',
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=now().date(),
        regie=regie,
        payer_external_id='payer:1',
    )
    credit = Credit.objects.create(
        label='My credit',
        date_publication=datetime.date(2022, 10, 1),
        regie=regie,
        payer_external_id='payer:1',
    )
    line = CreditLine.objects.create(
        event_date=now().date(),
        label='A line',
        credit=credit,
        quantity=5,
        unit_amount=4,
        user_external_id='user:1',
        agenda_slug='agenda-1',
        activity_label='Agenda 1',
        event_slug='agenda-1@event-1',
        description='Blablabla 14/09, 15/09, 16/09, 16/09, 01/10',
        details={'dates': ['2022-09-14', '2022-09-15', '2022-09-16', '2022-09-16', '2022-10-01']},
    )
    payment = Payment.objects.create(
        regie=regie,
        amount=1,
        payment_type=PaymentType.objects.get(regie=regie, slug='credit'),
    )
    CreditAssignment.objects.create(
        invoice=invoice,
        payment=payment,
        credit=credit,
        amount=1,
    )
    resp = app.post_json('/api/regie/foo/invoicing-elements/split/', params=params)
    new_line = CreditLine.objects.latest('pk')
    assert resp.json == {
        'err': 0,
        'invoicing_elements': [
            {'credit_id': str(credit.uuid), 'line_id': str(line.uuid), 'action': 'updated'},
            {'credit_id': str(credit.uuid), 'line_id': str(new_line.uuid), 'action': 'created'},
        ],
    }
    line.refresh_from_db()
    assert line.quantity == 2
    assert line.agenda_slug == 'agenda-1'
    assert line.activity_label == 'Agenda 1'
    assert line.event_slug == 'agenda-1@event-1'
    assert line.description == 'Blablabla 14/09, 01/10'
    assert line.details == {'dates': ['2022-09-14', '2022-10-01']}
    assert new_line.quantity == 3
    assert new_line.agenda_slug == 'agenda-2'
    assert new_line.activity_label == 'Agenda 2'
    assert new_line.event_slug == 'agenda-2@event-1'
    assert new_line.description == 'Blablabla 15/09, 16/09, 16/09'
    assert new_line.details == {'dates': ['2022-09-15', '2022-09-16', '2022-09-16']}
