import datetime

import pytest
from django.utils.timezone import now
from pyquery import PyQuery

from lingo.agendas.models import Agenda
from lingo.invoicing.models import Credit, CreditLine, Invoice, InvoiceLine, Regie
from tests.utils import get_ods_rows, login

pytestmark = pytest.mark.django_db


def test_regie_payers(app, admin_user):
    regie = Regie.objects.create(label='Foo')
    other_regie = Regie.objects.create(label='Bar')

    Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        regie=regie,
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Name1',
    )
    Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        regie=regie,
        payer_external_id='payer:1',
        payer_first_name='Other First1',
        payer_last_name='Name1',
    )
    Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        regie=regie,
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Other Name1',
    )
    Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        regie=other_regie,
        payer_external_id='payer:2',
        payer_first_name='Other First2',
        payer_last_name='Name2',
    )
    Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        regie=other_regie,
        payer_external_id='payer:3',
        payer_first_name='First3',
        payer_last_name='Name3',
    )
    Credit.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        regie=regie,
        payer_external_id='payer:2',
        payer_first_name='First2',
        payer_last_name='Name2',
    )

    app = login(app)
    resp = app.get('/manage/invoicing/regie/%s/payers/' % regie.pk)
    assert resp.pyquery('tr').text() == (
        'payer:1\nFirst1 Name1 '
        'payer:1\nOther First1 Name1 '
        'payer:1\nFirst1 Other Name1 '
        'payer:2\nFirst2 Name2'
    )
    assert resp.text.count('/manage/invoicing/regie/%s/payer/payer:1/transactions/' % regie.pk) == 3
    assert resp.text.count('/manage/invoicing/regie/%s/payer/payer:2/transactions/' % regie.pk) == 1

    # test filters
    params = [
        ({'payer_external_id': 'payer:1'}, 3),
        ({'payer_external_id': 'payer:2'}, 1),
        ({'payer_external_id': 'payer:42'}, 0),
        ({'payer_first_name': 'first'}, 4),
        ({'payer_first_name': 'first1'}, 3),
        ({'payer_last_name': 'name'}, 4),
        ({'payer_last_name': 'name1'}, 3),
    ]
    for param, result in params:
        resp = app.get(
            '/manage/invoicing/regie/%s/payers/' % regie.pk,
            params=param,
        )
        assert len(resp.pyquery('tr')) == result


def test_regie_payer_transactions(app, admin_user):
    regie = Regie.objects.create(label='Foo')
    other_regie = Regie.objects.create(label='Bar')
    Agenda.objects.create(label='Agenda A', regie=regie)
    Agenda.objects.create(label='Agenda B', regie=regie)
    Agenda.objects.create(label='Agenda 1', regie=regie)
    Agenda.objects.create(label='Agenda 2', regie=regie)

    today = now().date()
    yesterday = today - datetime.timedelta(days=1)
    invoice1 = Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_invoicing=yesterday,
        regie=regie,
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Name1',
        origin='basket',
    )
    invoice1.set_number()
    invoice1.save()
    InvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        invoice=invoice1,
        quantity=3,
        unit_amount=1,
        label='Label 11',
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
        details={
            'agenda': 'agenda-1',
            'primary_event': 'event-1',
            'status': 'presence',
            'check_type': 'foo',
            'check_type_group': 'foobar',
            'check_type_label': 'Foo!',
            'dates': ['2022-09-01', '2022-09-02', '2022-09-03'],
        },
        event_slug='agenda-1@event-1',
        agenda_slug='agenda-1',
        activity_label='Agenda 1',
        description='Thu01, Fri02, Sat03',
        accounting_code='424242',
    )
    invoice2 = Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        regie=regie,
        payer_external_id='payer:1',
        payer_first_name='Other First1',
        payer_last_name='Name1',
        origin='api',
    )
    invoice2.set_number()
    invoice2.save()
    InvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        invoice=invoice2,
        quantity=15,
        unit_amount=1,
        label='Agenda foobar',
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
        details={
            'agenda': 'agenda-2',
            'primary_event': 'event-1',
            'status': 'presence',
            'partial_bookings': True,
        },
        event_slug='agenda-2@event-1',
        agenda_slug='agenda-2',
        activity_label='Agenda 2',
        description='a description',
        accounting_code='424242',
    )
    InvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        invoice=invoice2,
        quantity=0,
        unit_amount=1,
        label='Absence',
        user_external_id='user:2',
        user_first_name='User2',
        user_last_name='Name2',
        details={
            'agenda': 'agenda-2',
            'primary_event': 'event-1',
            'status': 'absence',
            'partial_bookings': True,
            'dates': ['2022-09-02', '2022-09-03'],
        },
        event_slug='agenda-2@event-1',
        agenda_slug='agenda-2',
        activity_label='Agenda 2',
        description='a description',
        accounting_code='424243',
    )
    cancelled = Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        regie=regie,
        payer_external_id='payer:1',
        payer_first_name='Other First1',
        payer_last_name='Name1',
        cancelled_at=now(),
    )
    cancelled.set_number()
    cancelled.save()
    InvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        invoice=cancelled,
        quantity=15,
        unit_amount=1,
        label='Agenda foobar',
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
        details={
            'agenda': 'agenda-2',
            'primary_event': 'event-1',
            'status': 'presence',
            'partial_bookings': True,
        },
        event_slug='agenda-2@event-1',
        agenda_slug='agenda-2',
        activity_label='Agenda 2',
        description='a description',
        accounting_code='424242',
    )

    credit1 = Credit.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_invoicing=yesterday,
        regie=regie,
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Name1',
        origin='campaign',
    )
    credit1.set_number()
    credit1.save()
    CreditLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        credit=credit1,
        quantity=4,
        unit_amount=1,
        label='Label 11',
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
        details={
            'agenda': 'agenda-a',
            'primary_event': 'event-a',
            'status': 'presence',
            'check_type': 'foo',
            'check_type_group': 'foobar',
            'check_type_label': 'Foo!',
            'dates': ['2022-09-01', '2022-09-02'],
        },
        event_slug='agenda-a@event-a',
        agenda_slug='agenda-a',
        activity_label='Agenda A',
        description='Thu01, Fri02',
        accounting_code='424242',
    )
    CreditLine.objects.create(
        event_date=datetime.date(2022, 9, 2),
        credit=credit1,
        quantity=3,
        unit_amount=2,
        label='Label 12',
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
        details={
            'agenda': 'agenda-a',
            'primary_event': 'event-a',
            'status': 'presence',
            'partial_bookings': True,
            'dates': ['2022-09-01', '2022-09-02', '2022-09-03'],
        },
        event_slug='agenda-a@event-a',
        agenda_slug='agenda-a',
        activity_label='Agenda A',
        description='a description',
    )
    credit2 = Credit.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        regie=regie,
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Other Name1',
        origin='api',
    )
    credit2.set_number()
    credit2.save()
    CreditLine.objects.create(
        event_date=datetime.date(2022, 9, 3),
        credit=credit2,
        quantity=2,
        unit_amount=3,
        label='Label 13',
        user_external_id='user:2',
        user_first_name='User2',
        user_last_name='Name2',
        details={
            'agenda': 'agenda-b',
            'primary_event': 'event-b',
            'status': 'absence',
            'partial_bookings': True,
        },
        event_slug='agenda-b@event-b',
        agenda_slug='agenda-b',
        activity_label='Agenda B',
        description='a description',
    )
    cancelled = Credit.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        regie=regie,
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Other Name1',
        cancelled_at=now(),
    )
    cancelled.set_number()
    cancelled.save()
    CreditLine.objects.create(
        event_date=datetime.date(2022, 9, 3),
        credit=cancelled,
        quantity=2,
        unit_amount=3,
        label='Label 13',
        user_external_id='user:2',
        user_first_name='User2',
        user_last_name='Name2',
        details={
            'agenda': 'agenda-b',
            'primary_event': 'event-b',
            'status': 'absence',
            'partial_bookings': True,
        },
        event_slug='agenda-b@event-b',
        agenda_slug='agenda-b',
        activity_label='Agenda B',
        description='a description',
    )

    invoice_other_payer = Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        regie=regie,
        payer_external_id='payer:2',
        payer_first_name='First2',
        payer_last_name='Name2',
    )
    invoice_other_payer.set_number()
    invoice_other_payer.save()
    InvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        invoice=invoice_other_payer,
        quantity=3,
        unit_amount=1,
        label='Label 11',
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
        details={
            'agenda': 'agenda-1',
            'primary_event': 'event-1',
            'status': 'presence',
            'check_type': 'foo',
            'check_type_group': 'foobar',
            'check_type_label': 'Foo!',
            'dates': ['2022-09-01', '2022-09-02', '2022-09-03'],
        },
        event_slug='agenda-1@event-1',
        agenda_slug='agenda-1',
        activity_label='Agenda 1',
        description='Thu01, Fri02, Sat03',
    )
    credit_other_payer = Credit.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        regie=regie,
        payer_external_id='payer:2',
        payer_first_name='First2',
        payer_last_name='Name2',
    )
    credit_other_payer.set_number()
    credit_other_payer.save()
    CreditLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        credit=credit_other_payer,
        quantity=3,
        unit_amount=1,
        label='Label 11',
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
        details={
            'agenda': 'agenda-a',
            'primary_event': 'event-a',
            'status': 'presence',
            'check_type': 'foo',
            'check_type_group': 'foobar',
            'check_type_label': 'Foo!',
            'dates': ['2022-09-01', '2022-09-02', '2022-09-03'],
        },
        event_slug='agenda-a@event-a',
        agenda_slug='agenda-a',
        activity_label='Agenda A',
        description='Thu01, Fri02, Sat03',
    )

    other_invoice = Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        regie=other_regie,
        payer_external_id='payer:2',
        payer_first_name='First2',
        payer_last_name='Name2',
    )
    other_invoice.set_number()
    other_invoice.save()
    InvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        invoice=other_invoice,
        quantity=3,
        unit_amount=1,
        label='Label 11',
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
        details={
            'agenda': 'agenda-1',
            'primary_event': 'event-1',
            'status': 'presence',
            'check_type': 'foo',
            'check_type_group': 'foobar',
            'check_type_label': 'Foo!',
            'dates': ['2022-09-01', '2022-09-02', '2022-09-03'],
        },
        event_slug='agenda-1@event-1',
        agenda_slug='agenda-1',
        activity_label='Agenda 1',
        description='Thu01, Fri02, Sat03',
    )
    other_credit = Credit.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        regie=other_regie,
        payer_external_id='payer:2',
        payer_first_name='First2',
        payer_last_name='Name2',
    )
    other_credit.set_number()
    other_credit.save()
    CreditLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        credit=other_credit,
        quantity=3,
        unit_amount=1,
        label='Label 11',
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
        details={
            'agenda': 'agenda-a',
            'primary_event': 'event-a',
            'status': 'presence',
            'check_type': 'foo',
            'check_type_group': 'foobar',
            'check_type_label': 'Foo!',
            'dates': ['2022-09-01', '2022-09-02', '2022-09-03'],
        },
        event_slug='agenda-a@event-a',
        agenda_slug='agenda-a',
        activity_label='Agenda A',
        description='Thu01, Fri02, Sat03',
    )

    app = login(app)
    resp = app.get('/manage/invoicing/regie/%s/payer/payer:1/transactions/' % regie.pk)
    assert [PyQuery(tr).text() for tr in resp.pyquery('tr')] == [
        'Invoicing object\nOrigin\nCreation date\nPayer\nUser\nActivity\nEvent\nAccounting code\nDescription\nEvent date\nUnit amount\nQuantity\nTotal amount',
        '%s\nAPI\n%s\nFirst1 Other Name1\nUser2 Name2 (user:2)\nAgenda B\n(agenda-b)\nLabel 13\n(event-b)\na description\n3.00€\n-2\n-6.00€'
        % (credit2.formatted_number, credit2.created_at.strftime('%d/%m/%Y')),
        '%s\nCampaign\n%s (%s)\nFirst1 Name1\nUser1 Name1 (user:1)\nAgenda A\n(agenda-a)\nLabel 11\n(event-a)\n'
        '424242\nFoo!\nThu01, Fri02\n01/09/2022, 02/09/2022\n1.00€\n-4\n-4.00€'
        % (
            credit1.formatted_number,
            credit1.created_at.strftime('%d/%m/%Y'),
            credit1.date_invoicing.strftime('%d/%m/%Y'),
        ),
        '%s\nCampaign\n%s (%s)\nFirst1 Name1\nUser1 Name1 (user:1)\nAgenda A\n(agenda-a)\nLabel 12\n(event-a)\n'
        'a description\n01/09/2022, 02/09/2022, 03/09/2022\n2.00€\n-3\n-6.00€'
        % (
            credit1.formatted_number,
            credit1.created_at.strftime('%d/%m/%Y'),
            credit1.date_invoicing.strftime('%d/%m/%Y'),
        ),
        '%s\nAPI\n%s\nOther First1 Name1\nUser1 Name1 (user:1)\nAgenda 2\n(agenda-2)\nAgenda foobar\n(event-1)\n'
        '424242\na description\n1.00€\n15\n15.00€'
        % (invoice2.formatted_number, invoice2.created_at.strftime('%d/%m/%Y')),
        '%s\nAPI\n%s\nOther First1 Name1\nUser2 Name2 (user:2)\nAgenda 2\n(agenda-2)\nAbsence\n(event-1)\n'
        '424243\na description\n02/09/2022, 03/09/2022\n1.00€\n0\n0.00€'
        % (invoice2.formatted_number, invoice2.created_at.strftime('%d/%m/%Y')),
        '%s\nBasket\n%s (%s)\nFirst1 Name1\nUser1 Name1 (user:1)\nAgenda 1\n(agenda-1)\nLabel 11\n(event-1)\n'
        '424242\nFoo!\nThu01, Fri02, Sat03\n01/09/2022, 02/09/2022, 03/09/2022\n1.00€\n3\n3.00€'
        % (
            invoice1.formatted_number,
            invoice1.created_at.strftime('%d/%m/%Y'),
            invoice1.date_invoicing.strftime('%d/%m/%Y'),
        ),
    ]

    resp = app.get('/manage/invoicing/regie/%s/payer/payer:1/transactions/?ods' % regie.pk)
    assert resp.headers['Content-Type'] == 'application/vnd.oasis.opendocument.spreadsheet'
    assert resp.headers['Content-Disposition'] == 'attachment; filename="payer-transactions.ods"'
    rows = list(get_ods_rows(resp))
    assert len(rows) == 1 + 6
    assert rows == [
        [
            'Invoicing object',
            'Origin',
            'Creation date',
            'Invoicing date',
            'Payer ID',
            'Payer first name',
            'Payer last name',
            'User ID',
            'User first name',
            'User last name',
            'Activity',
            'Agenda slug',
            'Event',
            'Event slug',
            'Accounting code',
            'Description',
            'Details',
            'Unit amount',
            'Quantity',
            'Total amount',
        ],
        [
            credit2.formatted_number,
            'API',
            credit2.created_at.strftime('%m/%d/%Y'),
            None,
            'payer:1',
            'First1',
            'Other Name1',
            'user:2',
            'User2',
            'Name2',
            'Agenda B',
            'agenda-b',
            'Label 13',
            'event-b',
            None,
            None,
            'a description',
            '3.00',
            '-2.00',
            '-6.00',
        ],
        [
            credit1.formatted_number,
            'Campaign',
            credit1.created_at.strftime('%m/%d/%Y'),
            credit1.date_invoicing.strftime('%m/%d/%Y'),
            'payer:1',
            'First1',
            'Name1',
            'user:1',
            'User1',
            'Name1',
            'Agenda A',
            'agenda-a',
            'Label 11',
            'event-a',
            '424242',
            'Foo!',
            'Thu01, Fri02',
            '1.00',
            '-4.00',
            '-4.00',
        ],
        [
            credit1.formatted_number,
            'Campaign',
            credit1.created_at.strftime('%m/%d/%Y'),
            credit1.date_invoicing.strftime('%m/%d/%Y'),
            'payer:1',
            'First1',
            'Name1',
            'user:1',
            'User1',
            'Name1',
            'Agenda A',
            'agenda-a',
            'Label 12',
            'event-a',
            None,
            None,
            'a description',
            '2.00',
            '-3.00',
            '-6.00',
        ],
        [
            invoice2.formatted_number,
            'API',
            invoice2.created_at.strftime('%m/%d/%Y'),
            None,
            'payer:1',
            'Other First1',
            'Name1',
            'user:1',
            'User1',
            'Name1',
            'Agenda 2',
            'agenda-2',
            'Agenda foobar',
            'event-1',
            '424242',
            None,
            'a description',
            '1.00',
            '15.00',
            '15.00',
        ],
        [
            invoice2.formatted_number,
            'API',
            invoice2.created_at.strftime('%m/%d/%Y'),
            None,
            'payer:1',
            'Other First1',
            'Name1',
            'user:2',
            'User2',
            'Name2',
            'Agenda 2',
            'agenda-2',
            'Absence',
            'event-1',
            '424243',
            None,
            'a description',
            '1.00',
            '0.00',
            '0.00',
        ],
        [
            invoice1.formatted_number,
            'Basket',
            invoice1.created_at.strftime('%m/%d/%Y'),
            invoice1.date_invoicing.strftime('%m/%d/%Y'),
            'payer:1',
            'First1',
            'Name1',
            'user:1',
            'User1',
            'Name1',
            'Agenda 1',
            'agenda-1',
            'Label 11',
            'event-1',
            '424242',
            'Foo!',
            'Thu01, Fri02, Sat03',
            '1.00',
            '3.00',
            '3.00',
        ],
    ]

    resp = app.get('/manage/invoicing/regie/%s/payer/payer:1/transactions/?ods&full' % regie.pk)
    assert resp.headers['Content-Type'] == 'application/vnd.oasis.opendocument.spreadsheet'
    assert resp.headers['Content-Disposition'] == 'attachment; filename="payer-transactions-full.ods"'
    rows = list(get_ods_rows(resp))
    assert len(rows) == 1 + 10
    assert rows == [
        [
            'Invoicing object',
            'Origin',
            'Creation date',
            'Invoicing date',
            'Payer ID',
            'Payer first name',
            'Payer last name',
            'User ID',
            'User first name',
            'User last name',
            'Activity',
            'Agenda slug',
            'Event',
            'Event slug',
            'Accounting code',
            'Description',
            'Details',
            'Event date',
            'Unit amount',
            'Quantity',
            'Total amount',
        ],
        [
            credit2.formatted_number,
            'API',
            credit2.created_at.strftime('%m/%d/%Y'),
            None,
            'payer:1',
            'First1',
            'Other Name1',
            'user:2',
            'User2',
            'Name2',
            'Agenda B',
            'agenda-b',
            'Label 13',
            'event-b',
            None,
            None,
            'a description',
            None,
            '3.00',
            '-2.00',
            '-6.00',
        ],
        [
            credit1.formatted_number,
            'Campaign',
            credit1.created_at.strftime('%m/%d/%Y'),
            credit1.date_invoicing.strftime('%m/%d/%Y'),
            'payer:1',
            'First1',
            'Name1',
            'user:1',
            'User1',
            'Name1',
            'Agenda A',
            'agenda-a',
            'Label 11',
            'event-a',
            '424242',
            'Foo!',
            'Thu01, Fri02',
            None,
            '1.00',
            '-4.00',
            '-4.00',
        ],
        [
            credit1.formatted_number,
            'Campaign',
            credit1.created_at.strftime('%m/%d/%Y'),
            credit1.date_invoicing.strftime('%m/%d/%Y'),
            'payer:1',
            'First1',
            'Name1',
            'user:1',
            'User1',
            'Name1',
            'Agenda A',
            'agenda-a',
            'Label 12',
            'event-a',
            None,
            None,
            'a description',
            '09/01/2022',
            '2.00',
            '-1.00',
            '-2.00',
        ],
        [
            credit1.formatted_number,
            'Campaign',
            credit1.created_at.strftime('%m/%d/%Y'),
            credit1.date_invoicing.strftime('%m/%d/%Y'),
            'payer:1',
            'First1',
            'Name1',
            'user:1',
            'User1',
            'Name1',
            'Agenda A',
            'agenda-a',
            'Label 12',
            'event-a',
            None,
            None,
            'a description',
            '09/02/2022',
            '2.00',
            '-1.00',
            '-2.00',
        ],
        [
            credit1.formatted_number,
            'Campaign',
            credit1.created_at.strftime('%m/%d/%Y'),
            credit1.date_invoicing.strftime('%m/%d/%Y'),
            'payer:1',
            'First1',
            'Name1',
            'user:1',
            'User1',
            'Name1',
            'Agenda A',
            'agenda-a',
            'Label 12',
            'event-a',
            None,
            None,
            'a description',
            '09/03/2022',
            '2.00',
            '-1.00',
            '-2.00',
        ],
        [
            invoice2.formatted_number,
            'API',
            invoice2.created_at.strftime('%m/%d/%Y'),
            None,
            'payer:1',
            'Other First1',
            'Name1',
            'user:1',
            'User1',
            'Name1',
            'Agenda 2',
            'agenda-2',
            'Agenda foobar',
            'event-1',
            '424242',
            None,
            'a description',
            None,
            '1.00',
            '15.00',
            '15.00',
        ],
        [
            invoice2.formatted_number,
            'API',
            invoice2.created_at.strftime('%m/%d/%Y'),
            None,
            'payer:1',
            'Other First1',
            'Name1',
            'user:2',
            'User2',
            'Name2',
            'Agenda 2',
            'agenda-2',
            'Absence',
            'event-1',
            '424243',
            None,
            'a description',
            None,
            '1.00',
            '0.00',
            '0.00',
        ],
        [
            invoice1.formatted_number,
            'Basket',
            invoice1.created_at.strftime('%m/%d/%Y'),
            invoice1.date_invoicing.strftime('%m/%d/%Y'),
            'payer:1',
            'First1',
            'Name1',
            'user:1',
            'User1',
            'Name1',
            'Agenda 1',
            'agenda-1',
            'Label 11',
            'event-1',
            '424242',
            'Foo!',
            'Thu01, Fri02, Sat03',
            '09/01/2022',
            '1.00',
            '1.00',
            '1.00',
        ],
        [
            invoice1.formatted_number,
            'Basket',
            invoice1.created_at.strftime('%m/%d/%Y'),
            invoice1.date_invoicing.strftime('%m/%d/%Y'),
            'payer:1',
            'First1',
            'Name1',
            'user:1',
            'User1',
            'Name1',
            'Agenda 1',
            'agenda-1',
            'Label 11',
            'event-1',
            '424242',
            'Foo!',
            'Thu01, Fri02, Sat03',
            '09/02/2022',
            '1.00',
            '1.00',
            '1.00',
        ],
        [
            invoice1.formatted_number,
            'Basket',
            invoice1.created_at.strftime('%m/%d/%Y'),
            invoice1.date_invoicing.strftime('%m/%d/%Y'),
            'payer:1',
            'First1',
            'Name1',
            'user:1',
            'User1',
            'Name1',
            'Agenda 1',
            'agenda-1',
            'Label 11',
            'event-1',
            '424242',
            'Foo!',
            'Thu01, Fri02, Sat03',
            '09/03/2022',
            '1.00',
            '1.00',
            '1.00',
        ],
    ]

    # test filters
    params = [
        ({'number': invoice1.formatted_number}, 1),
        ({'number': credit2.formatted_number}, 1),
        ({'origin': 'api'}, 3),
        ({'user_external_id': 'user:1'}, 4),
        ({'user_external_id': 'user:2'}, 2),
        ({'user_first_name': 'user'}, 6),
        ({'user_first_name': 'user2'}, 2),
        ({'user_last_name': 'name'}, 6),
        ({'user_last_name': 'name1'}, 4),
        ({'agenda': 'agenda-a'}, 2),
        ({'agenda': 'agenda-b'}, 1),
        ({'event': 'agenda-a@event-a'}, 2),
        ({'event': 'agenda-2@event-1'}, 2),
        ({'event_date_after': '2022-09-01'}, 4),
        ({'event_date_after': '2022-09-02'}, 4),
        ({'event_date_after': '2022-09-03'}, 3),
        ({'event_date_before': '2022-09-01'}, 3),
        ({'event_date_before': '2022-09-02'}, 4),
        ({'event_date_before': '2022-09-03'}, 4),
        ({'accounting_code': '42'}, 0),
        ({'accounting_code': '424242'}, 3),
        ({'accounting_code': '424243'}, 1),
    ]
    for param, result in params:
        resp = app.get('/manage/invoicing/regie/%s/payer/payer:1/transactions/' % regie.pk, params=param)
        assert len(resp.pyquery('tr.line')) == result
        param['ods'] = True
        resp = app.get('/manage/invoicing/regie/%s/payer/payer:1/transactions/' % regie.pk, params=param)
        rows = list(get_ods_rows(resp))
        assert len(rows) == 1 + result


def test_regie_transactions_for_event(app, admin_user):
    regie = Regie.objects.create(label='Foo')
    other_regie = Regie.objects.create(label='Bar')
    Agenda.objects.create(label='Agenda A', regie=regie)
    Agenda.objects.create(label='Agenda B', regie=regie)

    today = now().date()
    yesterday = today - datetime.timedelta(days=1)
    invoice1 = Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_invoicing=yesterday,
        regie=regie,
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Name1',
        origin='basket',
    )
    invoice1.set_number()
    invoice1.save()
    InvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        invoice=invoice1,
        quantity=3,
        unit_amount=1,
        label='Label 11',
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
        details={
            'agenda': 'agenda-a',
            'primary_event': 'event-a',
            'status': 'presence',
            'check_type': 'foo',
            'check_type_group': 'foobar',
            'check_type_label': 'Foo!',
            'dates': ['2022-09-01', '2022-09-02', '2022-09-03'],
        },
        event_slug='agenda-a@event-a',
        agenda_slug='agenda-a',
        activity_label='Agenda A',
        description='Thu01, Fri02, Sat03',
        accounting_code='424242',
    )
    invoice2 = Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        regie=regie,
        payer_external_id='payer:1',
        payer_first_name='Other First1',
        payer_last_name='Name1',
        origin='api',
    )
    invoice2.set_number()
    invoice2.save()
    InvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        invoice=invoice2,
        quantity=15,
        unit_amount=1,
        label='Agenda foobar',
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
        details={
            'agenda': 'agenda-b',
            'primary_event': 'event-a',
            'status': 'presence',
            'partial_bookings': True,
            'dates': ['2022-09-01'],
        },
        event_slug='agenda-b@event-a',
        agenda_slug='agenda-b',
        activity_label='Agenda B',
        description='a description',
        accounting_code='424242',
    )
    InvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        invoice=invoice2,
        quantity=15,
        unit_amount=1,
        label='Agenda foobar',
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
        details={
            'agenda': 'agenda-a',
            'primary_event': 'event-b',
            'status': 'presence',
            'partial_bookings': True,
            'dates': ['2022-09-01'],
        },
        event_slug='agenda-a@event-b',
        agenda_slug='agenda-a',
        activity_label='Agenda A',
        description='a description',
        accounting_code='424242',
    )
    InvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        invoice=invoice2,
        quantity=0,
        unit_amount=1,
        label='Absence',
        user_external_id='user:2',
        user_first_name='User2',
        user_last_name='Name2',
        details={
            'agenda': 'agenda-a',
            'primary_event': 'event-a',
            'status': 'absence',
            'partial_bookings': True,
            'dates': ['2022-09-02', '2022-09-03'],
        },
        event_slug='agenda-a@event-a',
        agenda_slug='agenda-a',
        activity_label='Agenda A',
        description='a description',
        accounting_code='424243',
    )
    cancelled = Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        regie=regie,
        payer_external_id='payer:1',
        payer_first_name='Other First1',
        payer_last_name='Name1',
        cancelled_at=now(),
    )
    cancelled.set_number()
    cancelled.save()
    InvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        invoice=cancelled,
        quantity=15,
        unit_amount=1,
        label='Agenda foobar',
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
        details={
            'agenda': 'agenda-b',
            'primary_event': 'event-a',
            'status': 'presence',
            'partial_bookings': True,
            'dates': ['2022-09-01'],
        },
        event_slug='agenda-b@event-a',
        agenda_slug='agenda-b',
        activity_label='Agenda B',
        description='a description',
        accounting_code='424242',
    )

    credit1 = Credit.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_invoicing=yesterday,
        regie=regie,
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Name1',
        origin='campaign',
    )
    credit1.set_number()
    credit1.save()
    CreditLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        credit=credit1,
        quantity=4,
        unit_amount=1,
        label='Label 11',
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
        details={
            'agenda': 'agenda-a',
            'primary_event': 'event-a',
            'status': 'presence',
            'check_type': 'foo',
            'check_type_group': 'foobar',
            'check_type_label': 'Foo!',
            'dates': ['2022-09-01', '2022-09-02'],
        },
        event_slug='agenda-a@event-a',
        agenda_slug='agenda-a',
        activity_label='Agenda A',
        description='Thu01, Fri02',
        accounting_code='424242',
    )
    CreditLine.objects.create(
        event_date=datetime.date(2022, 9, 2),
        credit=credit1,
        quantity=3,
        unit_amount=2,
        label='Label 12',
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
        details={
            'agenda': 'agenda-a',
            'primary_event': 'event-a',
            'status': 'presence',
            'partial_bookings': True,
            'dates': ['2022-09-01', '2022-09-02', '2022-09-03'],
        },
        event_slug='agenda-a@event-a',
        agenda_slug='agenda-a',
        activity_label='Agenda A',
        description='a description',
    )
    credit2 = Credit.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        regie=regie,
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Other Name1',
        origin='api',
    )
    credit2.set_number()
    credit2.save()
    CreditLine.objects.create(
        event_date=datetime.date(2022, 9, 3),
        credit=credit2,
        quantity=2,
        unit_amount=3,
        label='Label 13',
        user_external_id='user:2',
        user_first_name='User2',
        user_last_name='Name2',
        details={
            'agenda': 'agenda-b',
            'primary_event': 'event-b',
            'status': 'absence',
            'partial_bookings': True,
            'dates': ['2022-09-03', '2022-09-04'],
        },
        event_slug='agenda-b@event-b',
        agenda_slug='agenda-b',
        activity_label='Agenda B',
        description='a description',
    )
    cancelled = Credit.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        regie=regie,
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Other Name1',
        cancelled_at=now(),
    )
    cancelled.set_number()
    cancelled.save()
    CreditLine.objects.create(
        event_date=datetime.date(2022, 9, 3),
        credit=cancelled,
        quantity=2,
        unit_amount=3,
        label='Label 13',
        user_external_id='user:2',
        user_first_name='User2',
        user_last_name='Name2',
        details={
            'agenda': 'agenda-b',
            'primary_event': 'event-b',
            'status': 'absence',
            'partial_bookings': True,
            'dates': ['2022-09-03', '2022-09-04'],
        },
        event_slug='agenda-b@event-b',
        agenda_slug='agenda-b',
        activity_label='Agenda B',
        description='a description',
    )

    invoice_other_payer = Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        regie=regie,
        payer_external_id='payer:2',
        payer_first_name='First2',
        payer_last_name='Name2',
    )
    invoice_other_payer.set_number()
    invoice_other_payer.save()
    InvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        invoice=invoice_other_payer,
        quantity=3,
        unit_amount=1,
        label='Label 11',
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
        details={
            'agenda': 'agenda-a',
            'primary_event': 'event-a',
            'status': 'presence',
            'check_type': 'foo',
            'check_type_group': 'foobar',
            'check_type_label': 'Foo!',
            'dates': ['2022-09-01', '2022-09-02', '2022-09-03'],
        },
        event_slug='agenda-a@event-a',
        agenda_slug='agenda-a',
        activity_label='Agenda A',
        description='Thu01, Fri02, Sat03',
    )
    credit_other_payer = Credit.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        regie=regie,
        payer_external_id='payer:2',
        payer_first_name='First2',
        payer_last_name='Name2',
    )
    credit_other_payer.set_number()
    credit_other_payer.save()
    CreditLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        credit=credit_other_payer,
        quantity=3,
        unit_amount=1,
        label='Label 11',
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
        details={
            'agenda': 'agenda-a',
            'primary_event': 'event-a',
            'status': 'presence',
            'check_type': 'foo',
            'check_type_group': 'foobar',
            'check_type_label': 'Foo!',
            'dates': ['2022-09-01', '2022-09-02', '2022-09-03'],
        },
        event_slug='agenda-a@event-a',
        agenda_slug='agenda-a',
        activity_label='Agenda A',
        description='Thu01, Fri02, Sat03',
    )

    other_invoice = Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        regie=other_regie,
        payer_external_id='payer:2',
        payer_first_name='First2',
        payer_last_name='Name2',
    )
    other_invoice.set_number()
    other_invoice.save()
    InvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        invoice=other_invoice,
        quantity=3,
        unit_amount=1,
        label='Label 11',
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
        details={
            'agenda': 'agenda-a',
            'primary_event': 'event-a',
            'status': 'presence',
            'check_type': 'foo',
            'check_type_group': 'foobar',
            'check_type_label': 'Foo!',
            'dates': ['2022-09-01', '2022-09-02', '2022-09-03'],
        },
        event_slug='agenda-a@event-a',
        agenda_slug='agenda-a',
        activity_label='Agenda A',
        description='Thu01, Fri02, Sat03',
    )
    other_credit = Credit.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        regie=other_regie,
        payer_external_id='payer:2',
        payer_first_name='First2',
        payer_last_name='Name2',
    )
    other_credit.set_number()
    other_credit.save()
    CreditLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        credit=other_credit,
        quantity=3,
        unit_amount=1,
        label='Label 11',
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
        details={
            'agenda': 'agenda-a',
            'primary_event': 'event-a',
            'status': 'presence',
            'check_type': 'foo',
            'check_type_group': 'foobar',
            'check_type_label': 'Foo!',
            'dates': ['2022-09-01', '2022-09-02', '2022-09-03'],
        },
        event_slug='agenda-a@event-a',
        agenda_slug='agenda-a',
        activity_label='Agenda A',
        description='Thu01, Fri02, Sat03',
    )

    app = login(app)
    app.get('/manage/invoicing/regie/%s/transactions/for-event/' % regie.pk, status=404)
    app.get(
        '/manage/invoicing/regie/%s/transactions/for-event/' % regie.pk,
        params={'event_slug': 'agenda-a@event-a', 'event_date': '2022-09-01'},
        status=404,
    )
    app.get(
        '/manage/invoicing/regie/%s/transactions/for-event/' % regie.pk,
        params={'event_slug': 'agenda-a@event-a', 'user_external_id': 'user:1'},
        status=404,
    )
    app.get(
        '/manage/invoicing/regie/%s/transactions/for-event/' % regie.pk,
        params={'event_date': '2022-09-01', 'user_external_id': 'user:1'},
        status=404,
    )
    resp = app.get(
        '/manage/invoicing/regie/%s/transactions/for-event/' % regie.pk,
        params={'event_slug': 'agenda-a@event-a', 'event_date': '2022-09-01', 'user_external_id': 'user:1'},
    )
    assert [PyQuery(tr).text() for tr in resp.pyquery('tr')] == [
        'Invoicing object\nOrigin\nCreation date\nPayer\nUser\nActivity\nEvent\nAccounting code\nDescription\nEvent date\nUnit amount\nQuantity\nTotal amount',
        '%s\n%s\nFirst2 Name2\nUser1 Name1 (user:1)\nAgenda A\n(agenda-a)\nLabel 11\n(event-a)\n'
        'Foo!\nThu01, Fri02, Sat03\n01/09/2022, 02/09/2022, 03/09/2022\n1.00€\n-3\n-3.00€'
        % (credit_other_payer.formatted_number, credit_other_payer.created_at.strftime('%d/%m/%Y')),
        '%s\n%s\nFirst2 Name2\nUser1 Name1 (user:1)\nAgenda A\n(agenda-a)\nLabel 11\n(event-a)\n'
        'Foo!\nThu01, Fri02, Sat03\n01/09/2022, 02/09/2022, 03/09/2022\n1.00€\n3\n3.00€'
        % (invoice_other_payer.formatted_number, invoice_other_payer.created_at.strftime('%d/%m/%Y')),
        '%s\nCampaign\n%s (%s)\nFirst1 Name1\nUser1 Name1 (user:1)\nAgenda A\n(agenda-a)\nLabel 11\n(event-a)\n'
        '424242\nFoo!\nThu01, Fri02\n01/09/2022, 02/09/2022\n1.00€\n-4\n-4.00€'
        % (
            credit1.formatted_number,
            credit1.created_at.strftime('%d/%m/%Y'),
            credit1.date_invoicing.strftime('%d/%m/%Y'),
        ),
        '%s\nCampaign\n%s (%s)\nFirst1 Name1\nUser1 Name1 (user:1)\nAgenda A\n(agenda-a)\nLabel 12\n(event-a)\n'
        'a description\n01/09/2022, 02/09/2022, 03/09/2022\n2.00€\n-3\n-6.00€'
        % (
            credit1.formatted_number,
            credit1.created_at.strftime('%d/%m/%Y'),
            credit1.date_invoicing.strftime('%d/%m/%Y'),
        ),
        '%s\nBasket\n%s (%s)\nFirst1 Name1\nUser1 Name1 (user:1)\nAgenda A\n(agenda-a)\nLabel 11\n(event-a)\n'
        '424242\nFoo!\nThu01, Fri02, Sat03\n01/09/2022, 02/09/2022, 03/09/2022\n1.00€\n3\n3.00€'
        % (
            invoice1.formatted_number,
            invoice1.created_at.strftime('%d/%m/%Y'),
            invoice1.date_invoicing.strftime('%d/%m/%Y'),
        ),
    ]
    assert [PyQuery(a).attr('href') for a in resp.pyquery('tr a')] == [
        '/manage/invoicing/regie/%s/credits/?number=%s' % (regie.pk, credit_other_payer.formatted_number),
        '/manage/invoicing/regie/%s/payer/payer:2/transactions/'
        '?event=agenda-a@event-a&event_date_before=2022-09-01&event_date_after=2022-09-01&user_external_id=user:1'
        % regie.pk,
        '/manage/invoicing/regie/%s/invoices/?number=%s' % (regie.pk, invoice_other_payer.formatted_number),
        '/manage/invoicing/regie/%s/payer/payer:2/transactions/'
        '?event=agenda-a@event-a&event_date_before=2022-09-01&event_date_after=2022-09-01&user_external_id=user:1'
        % regie.pk,
        '/manage/invoicing/regie/%s/credits/?number=%s' % (regie.pk, credit1.formatted_number),
        '/manage/invoicing/regie/%s/payer/payer:1/transactions/'
        '?event=agenda-a@event-a&event_date_before=2022-09-01&event_date_after=2022-09-01&user_external_id=user:1'
        % regie.pk,
        '/manage/invoicing/regie/%s/credits/?number=%s' % (regie.pk, credit1.formatted_number),
        '/manage/invoicing/regie/%s/payer/payer:1/transactions/'
        '?event=agenda-a@event-a&event_date_before=2022-09-01&event_date_after=2022-09-01&user_external_id=user:1'
        % regie.pk,
        '/manage/invoicing/regie/%s/invoices/?number=%s' % (regie.pk, invoice1.formatted_number),
        '/manage/invoicing/regie/%s/payer/payer:1/transactions/'
        '?event=agenda-a@event-a&event_date_before=2022-09-01&event_date_after=2022-09-01&user_external_id=user:1'
        % regie.pk,
    ]

    resp = app.get(
        '/manage/invoicing/regie/%s/transactions/for-event/' % regie.pk,
        params={'event_slug': 'agenda-a@event-a', 'event_date': '2022-09-01', 'user_external_id': 'user:2'},
    )
    assert [PyQuery(tr).text() for tr in resp.pyquery('tr')] == [
        'Invoicing object\nOrigin\nCreation date\nPayer\nUser\nActivity\nEvent\nAccounting code\nDescription\nEvent date\nUnit amount\nQuantity\nTotal amount',
    ]

    resp = app.get(
        '/manage/invoicing/regie/%s/transactions/for-event/' % regie.pk,
        params={'event_slug': 'agenda-a@event-a', 'event_date': '2022-09-03', 'user_external_id': 'user:2'},
    )
    assert [PyQuery(tr).text() for tr in resp.pyquery('tr')] == [
        'Invoicing object\nOrigin\nCreation date\nPayer\nUser\nActivity\nEvent\nAccounting code\nDescription\nEvent date\nUnit amount\nQuantity\nTotal amount',
        '%s\nAPI\n%s\nOther First1 Name1\nUser2 Name2 (user:2)\nAgenda A\n(agenda-a)\nAbsence\n(event-a)\n424243\n'
        'a description\n02/09/2022, 03/09/2022\n1.00€\n0\n0.00€'
        % (invoice2.formatted_number, invoice2.created_at.strftime('%d/%m/%Y')),
    ]

    resp = app.get(
        '/manage/invoicing/regie/%s/transactions/for-event/' % regie.pk,
        params={'event_slug': 'agenda-a@event-b', 'event_date': '2022-09-01', 'user_external_id': 'user:1'},
    )
    assert [PyQuery(tr).text() for tr in resp.pyquery('tr')] == [
        'Invoicing object\nOrigin\nCreation date\nPayer\nUser\nActivity\nEvent\nAccounting code\nDescription\nEvent date\nUnit amount\nQuantity\nTotal amount',
        '%s\nAPI\n%s\nOther First1 Name1\nUser1 Name1 (user:1)\nAgenda A\n(agenda-a)\nAgenda foobar\n(event-b)\n'
        '424242\na description\n01/09/2022\n1.00€\n15\n15.00€'
        % (invoice2.formatted_number, invoice2.created_at.strftime('%d/%m/%Y')),
    ]
