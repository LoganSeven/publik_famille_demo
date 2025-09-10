import datetime
import decimal
from unittest import mock

import pytest
from django.utils.formats import date_format
from django.utils.timezone import localtime, now
from pyquery import PyQuery

from lingo.agendas.models import Agenda
from lingo.invoicing.models import (
    AppearanceSettings,
    Campaign,
    CollectionDocket,
    Credit,
    CreditAssignment,
    CreditLine,
    Invoice,
    InvoiceCancellationReason,
    InvoiceLine,
    InvoiceLinePayment,
    Payment,
    PaymentType,
    Pool,
    Regie,
)
from tests.utils import get_ods_rows, login

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize('orphan', [True, False])
def test_regie_invoices(app, admin_user, orphan):
    regie = Regie.objects.create(label='Foo')
    Agenda.objects.create(label='Agenda A', regie=regie)
    Agenda.objects.create(label='Agenda B', regie=regie)
    PaymentType.create_defaults(regie)
    today = now().date()
    tomorrow = today + datetime.timedelta(days=1)
    yesterday = today - datetime.timedelta(days=1)
    date_invoicing = yesterday if yesterday.month == today.month else tomorrow
    pool1, pool2 = None, None
    if not orphan:
        campaign1 = Campaign.objects.create(
            regie=regie,
            date_start=datetime.date(2022, 9, 1),
            date_end=datetime.date(2022, 10, 1),
            date_publication=datetime.date(2022, 10, 1),
            date_payment_deadline=datetime.date(2022, 10, 31),
            date_due=datetime.date(2022, 10, 31),
            date_debit=datetime.date(2022, 11, 15),
            finalized=True,
        )
        pool1 = Pool.objects.create(
            campaign=campaign1,
            draft=False,
            status='completed',
        )
        campaign2 = Campaign.objects.create(
            regie=regie,
            date_start=datetime.date(2022, 9, 1),
            date_end=datetime.date(2022, 10, 1),
            date_publication=datetime.date(2022, 10, 1),
            date_payment_deadline=datetime.date(2022, 10, 31),
            date_due=datetime.date(2022, 10, 31),
            date_debit=datetime.date(2022, 11, 15),
            finalized=True,
        )
        pool2 = Pool.objects.create(
            campaign=campaign2,
            draft=False,
            status='completed',
        )
    invoice1 = Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline_displayed=datetime.date(2022, 10, 15),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_invoicing=date_invoicing,
        regie=regie,
        label='Invoice from 01/09/2022 to 30/09/2022',
        pool=pool1,
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Name1',
        payer_address='41 rue des kangourous\n99999 Kangourou Ville',
        payer_direct_debit=False,
        origin='api' if orphan else 'campaign',
    )
    invoice1.set_number()
    invoice1.save()
    invoice2 = Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_debit=datetime.date(2022, 11, 15),
        regie=regie,
        pool=pool2,
        payer_external_id='payer:2',
        payer_first_name='First2',
        payer_last_name='Name2',
        payer_address='42 rue des kangourous\n99999 Kangourou Ville',
        payer_direct_debit=True,
        origin='api' if orphan else 'campaign',
    )
    invoice2.set_number()
    invoice2.save()
    invoice1.previous_invoice = invoice2
    invoice1.save()
    collection = CollectionDocket.objects.create(regie=regie, date_end=now().date(), draft=False)
    collection.set_number()
    collection.save()
    invoice3 = Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_debit=datetime.date(2022, 11, 15),
        regie=regie,
        pool=pool2,
        payer_external_id='payer:3',
        payer_first_name='First3',
        payer_last_name='Name3',
        payer_address='43 rue des kangourous\n99999 Kangourou Ville',
        payer_email='email3',
        payer_phone='phone3',
        payer_direct_debit=True,
        collection=collection,
        origin='api' if orphan else 'campaign',
    )
    invoice3.set_number()
    invoice3.save()
    invoice4 = Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        regie=regie,
        payer_external_id='payer:3',
        payer_first_name='First3',
        payer_last_name='Name3',
        cancelled_at=now(),
        cancelled_by=admin_user,
        cancellation_reason=InvoiceCancellationReason.objects.create(label='Final pool deletion'),
        cancellation_description='foo bar\nblah',
        origin='api',
    )
    invoice4.set_number()
    invoice4.save()
    invoice5 = Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        regie=regie,
        payer_external_id='payer:3',
        payer_first_name='First3',
        payer_last_name='Name3',
        origin='api',
    )
    invoice5.set_number()
    invoice5.save()  # zero amount invoice, no line

    invoice_line11 = InvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        invoice=invoice1,
        quantity=1.2,
        unit_amount=1,
        pool=pool1,
        label='Event A',
        accounting_code='424242',
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
        form_url='http://form.com',
    )
    invoice_line12 = InvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 2),
        invoice=invoice1,
        quantity=1,
        unit_amount=2,
        pool=pool1,
        label='Event B',
        accounting_code='424243',
        user_external_id='user:2',
        user_first_name='User2',
        user_last_name='Name2',
        details={
            'agenda': 'agenda-b',
            'primary_event': 'event-b',
            'status': 'presence',
            'check_type': 'foo',
            'check_type_group': 'foobar',
            'check_type_label': 'Foo!',
            'dates': ['2022-09-01', '2022-09-02', '2022-09-03'],
        },
        event_slug='agenda-b@event-b',
        agenda_slug='agenda-b',
        activity_label='Agenda B',
        description='Thu01, Fri02, Sat03',
    )
    invoice_line13 = InvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 3),
        invoice=invoice1,
        quantity=1,
        unit_amount=3,
        pool=pool1,
        label='Event A',
        accounting_code='424242',
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
        details={
            'agenda': 'agenda-a',
            'primary_event': 'event-a',
            'status': 'presence',
            'dates': ['2022-09-04', '2022-09-05'],
        },
        event_slug='agenda-a@event-a',
        agenda_slug='agenda-a',
        activity_label='Agenda A',
        description='Sun04, Mon05',
    )
    payment1 = Payment.objects.create(
        regie=regie,
        amount=1,
        payment_type=PaymentType.objects.get(regie=regie, slug='cash'),
    )
    payment1.set_number()
    payment1.save()
    invoice_payment1 = InvoiceLinePayment.objects.create(
        payment=payment1,
        line=invoice_line11,
        amount=1,
    )
    invoice1.refresh_from_db()
    assert invoice1.remaining_amount == decimal.Decimal('5.2')
    assert invoice1.paid_amount == 1

    invoice_line21 = InvoiceLine.objects.create(
        # non recurring event
        event_date=datetime.date(2022, 9, 1),
        invoice=invoice2,
        quantity=1,
        unit_amount=1,
        pool=pool2,
        label='Event AA',
        event_slug='agenda-a@event-aa',
        agenda_slug='agenda-a',
        activity_label='Agenda A',
        accounting_code='424242',
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
    )
    payment2 = Payment.objects.create(
        regie=regie,
        amount=0.5,
        payment_type=PaymentType.objects.get(regie=regie, slug='cash'),
    )
    payment2.set_number()
    payment2.save()
    invoice_payment2 = InvoiceLinePayment.objects.create(
        payment=payment2,
        line=invoice_line21,
        amount=0.5,
    )
    payment3 = Payment.objects.create(
        regie=regie,
        amount=0.5,
        payment_type=PaymentType.objects.get(regie=regie, slug='credit'),
    )
    payment3.set_number()
    payment3.save()
    invoice_payment3 = InvoiceLinePayment.objects.create(
        payment=payment3,
        line=invoice_line21,
        amount=0.5,
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
        unit_amount=0.10,
        credit=credit1,
    )
    CreditAssignment.objects.create(
        invoice=invoice2,
        payment=payment3,
        credit=credit1,
        amount=0.10,
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
        unit_amount=0.40,
        credit=credit2,
    )
    CreditAssignment.objects.create(
        invoice=invoice2,
        payment=payment3,
        credit=credit2,
        amount=0.40,
    )
    invoice2.refresh_from_db()
    assert invoice2.remaining_amount == 0
    assert invoice2.paid_amount == 1

    invoice_line31 = InvoiceLine.objects.create(
        # from injected line
        event_date=datetime.date(2022, 9, 1),
        invoice=invoice3,
        quantity=1,
        unit_amount=1,
        pool=pool2,
        label='Event A',
        event_slug='injected',
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
    )
    invoice3.refresh_from_db()
    assert invoice3.remaining_amount == 1
    assert invoice3.paid_amount == 0

    InvoiceLine.objects.create(
        # from injected line
        event_date=datetime.date(2022, 9, 1),
        invoice=invoice4,
        quantity=1,
        unit_amount=1,
        label='Event A',
        event_slug='injected',
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
    )
    invoice4.refresh_from_db()
    assert invoice4.remaining_amount == 1
    assert invoice4.paid_amount == 0

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
    resp = app.get('/manage/invoicing/regie/%s/invoices/' % regie.pk)
    assert resp.pyquery('tr[data-invoicing-element-id="%s"]' % invoice1.pk).text() == (
        'Partially paid Invoice F%02d-%s-0000001 dated %s (created on %s) addressed to First1 Name1, '
        'amount 6.20€ - download (initial) - download (dynamic)'
    ) % (
        regie.pk,
        invoice1.date_invoicing.strftime('%y-%m'),
        invoice1.date_invoicing.strftime('%d/%m/%Y'),
        invoice1.created_at.strftime('%d/%m/%Y'),
    )
    assert [
        PyQuery(a).attr('href') for a in resp.pyquery('tr[data-invoicing-element-id="%s"] a' % invoice1.pk)
    ] == [
        '?payer_external_id=payer:1',
        '/manage/invoicing/regie/%s/invoice/%s/pdf/' % (regie.pk, invoice1.pk),
        '/manage/invoicing/regie/%s/invoice/%s/dynamic/pdf/' % (regie.pk, invoice1.pk),
    ]
    lines_url = resp.pyquery('tr[data-invoicing-element-id="%s"]' % invoice1.pk).attr(
        'data-invoicing-element-lines-url'
    )
    assert lines_url == '/manage/invoicing/ajax/regie/%s/invoice/%s/lines/' % (
        regie.pk,
        invoice1.pk,
    )
    lines_resp = app.get(lines_url)
    assert len(lines_resp.pyquery('tr')) == 30
    assert [PyQuery(tr).text() for tr in lines_resp.pyquery('tr')] == [
        'Invoice from 01/09/2022 to 30/09/2022 - Initial invoice number: F%02d-%s-0000002'
        % (
            regie.pk,
            invoice2.created_at.strftime('%y-%m'),
        ),
        'Direct debit: no',
        'Publication date: 01/10/2022',
        'Displayed payment deadline: 15/10/2022',
        'Effective payment deadline: 31/10/2022',
        'Due date: 31/10/2022\nEdit',
    ] + (['Origin: Campaign'] if not orphan else ['Origin: API']) + [
        'User1 Name1',
        'Description\nAccounting code\nAmount\nQuantity\nSubtotal',
        'Agenda A',
        'Event A\nFoo! Thu01, Fri02, Sat03\n424242\n1.00€\n1.2\n1.20€',
        'Payments',
        'Payment\nDate\nType\nAmount',
        'R%02d-%s-0000001\n%s\nCash\n1.00€'
        % (
            regie.pk,
            payment1.created_at.strftime('%y-%m'),
            date_format(localtime(invoice_payment1.created_at), 'DATETIME_FORMAT'),
        ),
        'Event A\nPresence Sun04, Mon05\n424242\n3.00€\n1\n3.00€',
        'Payments',
        'Payment\nDate\nType\nAmount',
        'No payments for this line',
        'User2 Name2',
        'Description\nAccounting code\nAmount\nQuantity\nSubtotal',
        'Agenda B',
        'Event B\nFoo! Thu01, Fri02, Sat03\n424243\n2.00€\n1\n2.00€',
        'Payments',
        'Payment\nDate\nType\nAmount',
        'No payments for this line',
        'Payments',
        'Payment\nDate\nType\nAmount',
        'R%02d-%s-0000001\n%s\nCash\n1.00€'
        % (
            regie.pk,
            payment1.created_at.strftime('%y-%m'),
            date_format(localtime(invoice_payment1.created_at), 'DATETIME_FORMAT'),
        ),
        'Paid amount: 1.00€',
        'Remaining amount: 5.20€',
    ]
    part1, part2 = [
        '/manage/invoicing/regie/%s/invoice/%s/edit/dates/' % (regie.pk, invoice1.pk),
        'http://form.com',
    ], []
    if not orphan:
        part1 = [
            '/manage/invoicing/regie/%s/invoice/%s/edit/dates/' % (regie.pk, invoice1.pk),
            '/manage/invoicing/regie/%s/campaign/%s/pool/%s/?number=%s'
            % (regie.pk, campaign1.pk, pool1.pk, invoice1.formatted_number),
            '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/?user_external_id=user:1'
            % (regie.pk, campaign1.pk, pool1.pk),
            '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/?invoice_line=%s'
            % (regie.pk, campaign1.pk, pool1.pk, invoice_line11.pk),
        ]
        part2 = [
            '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/?invoice_line=%s'
            % (regie.pk, campaign1.pk, pool1.pk, invoice_line13.pk),
            '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/?user_external_id=user:2'
            % (regie.pk, campaign1.pk, pool1.pk),
            '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/?invoice_line=%s'
            % (regie.pk, campaign1.pk, pool1.pk, invoice_line12.pk),
        ]
    assert [PyQuery(a).attr('href') for a in lines_resp.pyquery('tr a')] == part1 + [
        '/manage/invoicing/regie/%s/payments/?number=R%s-%s-0000001'
        % (
            regie.pk,
            regie.pk,
            payment1.created_at.strftime('%y-%m'),
        )
    ] + part2 + [
        '/manage/invoicing/regie/%s/payments/?number=R%s-%s-0000001'
        % (
            regie.pk,
            regie.pk,
            payment1.created_at.strftime('%y-%m'),
        ),
    ]
    assert resp.pyquery(
        'tr[data-invoicing-element-id="%s"]' % invoice2.pk
    ).text() == 'Paid Invoice F%02d-%s-0000002 dated %s addressed to First2 Name2, amount 1.00€ - download (initial) - download (dynamic)' % (
        regie.pk,
        invoice2.created_at.strftime('%y-%m'),
        invoice2.created_at.strftime('%d/%m/%Y'),
    )
    lines_url = resp.pyquery('tr[data-invoicing-element-id="%s"]' % invoice2.pk).attr(
        'data-invoicing-element-lines-url'
    )
    assert lines_url == '/manage/invoicing/ajax/regie/%s/invoice/%s/lines/' % (
        regie.pk,
        invoice2.pk,
    )
    lines_resp = app.get(lines_url)
    assert len(lines_resp.pyquery('tr')) == 20
    assert [PyQuery(tr).text() for tr in lines_resp.pyquery('tr')] == [
        'Direct debit: no',
        'Publication date: 01/10/2022',
        'Effective payment deadline: 31/10/2022',
        'Due date: 31/10/2022\nEdit',
        'Debit date: 15/11/2022',
    ] + (['Origin: Campaign'] if not orphan else ['Origin: API']) + [
        'User1 Name1',
        'Description\nAccounting code\nAmount\nQuantity\nSubtotal',
        'Agenda A',
        'Event AA\n424242\n1.00€\n1\n1.00€',
        'Payments',
        'Payment\nDate\nType\nAmount',
        'R%02d-%s-0000002\n%s\nCash\n0.50€'
        % (
            regie.pk,
            payment2.created_at.strftime('%y-%m'),
            date_format(localtime(invoice_payment2.created_at), 'DATETIME_FORMAT'),
        ),
        'R%02d-%s-0000003\n%s\nCredit\n0.50€'
        % (
            regie.pk,
            payment3.created_at.strftime('%y-%m'),
            date_format(localtime(invoice_payment3.created_at), 'DATETIME_FORMAT'),
        ),
        'Payments',
        'Payment\nDate\nType\nAmount',
        'R%02d-%s-0000002\n%s\nCash\n0.50€'
        % (
            regie.pk,
            payment2.created_at.strftime('%y-%m'),
            date_format(localtime(invoice_payment2.created_at), 'DATETIME_FORMAT'),
        ),
        'R%02d-%s-0000003\n%s\nCredit (%s - %s)\n0.50€'
        % (
            regie.pk,
            payment3.created_at.strftime('%y-%m'),
            date_format(localtime(invoice_payment3.created_at), 'DATETIME_FORMAT'),
            credit1.formatted_number,
            credit2.formatted_number,
        ),
        'Paid amount: 1.00€',
        'Payments certificate: download',
    ]
    part1 = ['/manage/invoicing/regie/%s/invoice/%s/edit/dates/' % (regie.pk, invoice2.pk)]
    if not orphan:
        part1 += [
            '/manage/invoicing/regie/%s/campaign/%s/pool/%s/?number=%s'
            % (regie.pk, campaign2.pk, pool2.pk, invoice2.formatted_number),
            '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/?user_external_id=user:1'
            % (regie.pk, campaign2.pk, pool2.pk),
            '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/?invoice_line=%s'
            % (regie.pk, campaign2.pk, pool2.pk, invoice_line21.pk),
        ]
    assert [PyQuery(a).attr('href') for a in lines_resp.pyquery('tr a')] == part1 + [
        '/manage/invoicing/regie/%s/payments/?number=R%s-%s-0000002'
        % (
            regie.pk,
            regie.pk,
            payment2.created_at.strftime('%y-%m'),
        ),
        '/manage/invoicing/regie/%s/payments/?number=R%s-%s-0000003'
        % (
            regie.pk,
            regie.pk,
            payment3.created_at.strftime('%y-%m'),
        ),
        '/manage/invoicing/regie/%s/payments/?number=R%s-%s-0000002'
        % (
            regie.pk,
            regie.pk,
            payment2.created_at.strftime('%y-%m'),
        ),
        '/manage/invoicing/regie/%s/payments/?number=R%s-%s-0000003'
        % (
            regie.pk,
            regie.pk,
            payment3.created_at.strftime('%y-%m'),
        ),
        '/manage/invoicing/regie/%s/credits/?number=%s'
        % (
            regie.pk,
            credit1.formatted_number,
        ),
        '/manage/invoicing/regie/%s/credits/?number=%s'
        % (
            regie.pk,
            credit2.formatted_number,
        ),
        '/manage/invoicing/regie/%s/invoice/%s/payments/pdf/' % (regie.pk, invoice2.pk),
    ]

    assert resp.pyquery(
        'tr[data-invoicing-element-id="%s"]' % invoice3.pk
    ).text() == 'Collected Invoice F%02d-%s-0000003 dated %s addressed to First3 Name3, amount 1.00€ - download' % (
        regie.pk,
        invoice3.created_at.strftime('%y-%m'),
        invoice3.created_at.strftime('%d/%m/%Y'),
    )
    collection.draft = True
    collection.save()
    resp = app.get('/manage/invoicing/regie/%s/invoices/' % regie.pk)
    assert resp.pyquery(
        'tr[data-invoicing-element-id="%s"]' % invoice3.pk
    ).text() == 'Under collection Invoice F%02d-%s-0000003 dated %s addressed to First3 Name3, amount 1.00€ - download' % (
        regie.pk,
        invoice3.created_at.strftime('%y-%m'),
        invoice3.created_at.strftime('%d/%m/%Y'),
    )
    assert len(resp.pyquery('tr[data-invoicing-element-id="%s"] a' % invoice3.pk)) == 2
    lines_url = resp.pyquery('tr[data-invoicing-element-id="%s"]' % invoice3.pk).attr(
        'data-invoicing-element-lines-url'
    )
    assert lines_url == '/manage/invoicing/ajax/regie/%s/invoice/%s/lines/' % (
        regie.pk,
        invoice3.pk,
    )
    lines_resp = app.get(lines_url)
    assert len(lines_resp.pyquery('tr')) == 17
    assert [PyQuery(tr).text() for tr in lines_resp.pyquery('tr')] == [
        'Direct debit: no',
        'Publication date: 01/10/2022',
        'Effective payment deadline: 31/10/2022',
        'Due date: 31/10/2022',
        'Debit date: 15/11/2022',
    ] + (['Origin: Campaign'] if not orphan else ['Origin: API']) + [
        'User1 Name1',
        'Description\nAccounting code\nAmount\nQuantity\nSubtotal',
        'Event A\n1.00€\n1\n1.00€',
        'Payments',
        'Payment\nDate\nType\nAmount',
        'No payments for this line',
        'Payments',
        'Payment\nDate\nType\nAmount',
        'No payments for this invoice',
        'Collection: TEMPORARY-%s' % collection.pk,
        'Remaining amount: 1.00€',
    ]
    part1 = []
    if not orphan:
        part1 = [
            '/manage/invoicing/regie/%s/campaign/%s/pool/%s/?number=%s'
            % (regie.pk, campaign2.pk, pool2.pk, invoice3.formatted_number),
            '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/?user_external_id=user:1'
            % (regie.pk, campaign2.pk, pool2.pk),
            '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/?invoice_line=%s'
            % (regie.pk, campaign2.pk, pool2.pk, invoice_line31.pk),
        ]
    assert [PyQuery(a).attr('href') for a in lines_resp.pyquery('tr a')] == part1 + [
        '/manage/invoicing/regie/%s/collection/%s/' % (regie.pk, collection.pk),
    ]

    assert resp.pyquery(
        'tr[data-invoicing-element-id="%s"]' % invoice4.pk
    ).text() == 'Cancelled Invoice F%02d-%s-0000004 dated %s addressed to First3 Name3, amount 1.00€ - download' % (
        regie.pk,
        invoice4.created_at.strftime('%y-%m'),
        invoice4.created_at.strftime('%d/%m/%Y'),
    )
    assert len(resp.pyquery('tr[data-invoicing-element-id="%s"] a' % invoice4.pk)) == 2
    lines_url = resp.pyquery('tr[data-invoicing-element-id="%s"]' % invoice4.pk).attr(
        'data-invoicing-element-lines-url'
    )
    assert lines_url == '/manage/invoicing/ajax/regie/%s/invoice/%s/lines/' % (
        regie.pk,
        invoice4.pk,
    )
    lines_resp = app.get(lines_url)
    assert len(lines_resp.pyquery('tr')) == 12
    assert [PyQuery(tr).text() for tr in lines_resp.pyquery('tr')] == [
        'Direct debit: no',
        'Publication date: 01/10/2022',
        'Effective payment deadline: 31/10/2022',
        'Due date: 31/10/2022',
        'Origin: API',
        'User1 Name1',
        'Description\nAccounting code\nAmount\nQuantity\nSubtotal',
        'Event A\n1.00€\n1\n1.00€',
        'Cancelled on: %s' % localtime(invoice4.cancelled_at).strftime('%d/%m/%Y %H:%M'),
        'Cancelled by: admin',
        'Reason: Final pool deletion',
        'Description: foo bar\nblah',
    ]

    assert resp.pyquery(
        'tr[data-invoicing-element-id="%s"]' % invoice5.pk
    ).text() == 'Invoice F%02d-%s-0000005 dated %s addressed to First3 Name3, amount 0.00€ - download (initial)' % (
        regie.pk,
        invoice5.created_at.strftime('%y-%m'),
        invoice5.created_at.strftime('%d/%m/%Y'),
    )
    assert len(resp.pyquery('tr[data-invoicing-element-id="%s"] a' % invoice5.pk)) == 2
    lines_url = resp.pyquery('tr[data-invoicing-element-id="%s"]' % invoice5.pk).attr(
        'data-invoicing-element-lines-url'
    )
    assert lines_url == '/manage/invoicing/ajax/regie/%s/invoice/%s/lines/' % (
        regie.pk,
        invoice5.pk,
    )
    lines_resp = app.get(lines_url)
    assert len(lines_resp.pyquery('tr')) == 10
    assert [PyQuery(tr).text() for tr in lines_resp.pyquery('tr')] == [
        'Direct debit: no',
        'Publication date: 01/10/2022',
        'Effective payment deadline: 31/10/2022',
        'Due date: 31/10/2022\nEdit',
        'Origin: API',
        'Payments',
        'Payment\nDate\nType\nAmount',
        'No payments for this invoice',
        'Remaining amount: 0.00€',
        'Cancel invoice',
    ]

    resp = app.get('/manage/invoicing/regie/%s/invoices/?ods' % regie.pk)
    assert resp.headers['Content-Type'] == 'application/vnd.oasis.opendocument.spreadsheet'
    assert resp.headers['Content-Disposition'] == 'attachment; filename="invoices.ods"'
    rows = list(get_ods_rows(resp))
    assert len(rows) == 6
    assert rows == [
        [
            'Number',
            'Origin',
            'Payer ID',
            'Payer first name',
            'Payer last name',
            'Creation date',
            'Invoicing date',
            'Publication date',
            'Payment deadline',
            'Due date',
            'Direct debit',
            'Total due',
            'Paid amount',
            'Status',
            'Cancelled on',
            'Cancellation reason',
        ],
        [
            'F%02d-%s-0000005' % (regie.pk, invoice5.created_at.strftime('%y-%m')),
            'API',
            'payer:3',
            'First3',
            'Name3',
            invoice5.created_at.strftime('%m/%d/%Y'),
            None,
            '10/01/2022',
            '10/31/2022',
            '10/31/2022',
            'no',
            '0.00',
            '0.00',
            'Paid',
            None,
            None,
        ],
        [
            'F%02d-%s-0000004' % (regie.pk, invoice4.created_at.strftime('%y-%m')),
            'API',
            'payer:3',
            'First3',
            'Name3',
            invoice4.created_at.strftime('%m/%d/%Y'),
            None,
            '10/01/2022',
            '10/31/2022',
            '10/31/2022',
            'no',
            '1.00',
            '0.00',
            'Cancelled',
            invoice4.cancelled_at.strftime('%m/%d/%Y'),
            'Final pool deletion',
        ],
        [
            'F%02d-%s-0000003' % (regie.pk, invoice3.created_at.strftime('%y-%m')),
            'API' if orphan else 'Campaign',
            'payer:3',
            'First3',
            'Name3',
            invoice3.created_at.strftime('%m/%d/%Y'),
            None,
            '10/01/2022',
            '10/31/2022',
            '10/31/2022',
            'yes',
            '1.00',
            '0.00',
            'Not paid',
            None,
            None,
        ],
        [
            'F%02d-%s-0000002' % (regie.pk, invoice2.created_at.strftime('%y-%m')),
            'API' if orphan else 'Campaign',
            'payer:2',
            'First2',
            'Name2',
            invoice2.created_at.strftime('%m/%d/%Y'),
            None,
            '10/01/2022',
            '10/31/2022',
            '10/31/2022',
            'yes',
            '1.00',
            '1.00',
            'Paid',
            None,
            None,
        ],
        [
            'F%02d-%s-0000001' % (regie.pk, invoice1.date_invoicing.strftime('%y-%m')),
            'API' if orphan else 'Campaign',
            'payer:1',
            'First1',
            'Name1',
            invoice1.created_at.strftime('%m/%d/%Y'),
            invoice1.date_invoicing.strftime('%m/%d/%Y'),
            '10/01/2022',
            '10/31/2022',
            '10/31/2022',
            'no',
            '6.20',
            '1.00',
            'Partially paid',
            None,
            None,
        ],
    ]

    resp = app.get('/manage/invoicing/regie/%s/invoices/?ods&full' % regie.pk)
    assert resp.headers['Content-Type'] == 'application/vnd.oasis.opendocument.spreadsheet'
    assert resp.headers['Content-Disposition'] == 'attachment; filename="invoices-full.ods"'
    rows = list(get_ods_rows(resp))
    assert len(rows) == 1 + 6
    assert rows == [
        [
            'Number',
            'Origin',
            'Payer ID',
            'Payer first name',
            'Payer last name',
            'Payer email',
            'Payer phone',
            'Creation date',
            'Invoicing date',
            'Publication date',
            'Payment deadline',
            'Due date',
            'Direct debit',
            'Description',
            'Accounting code',
            'Unit amount',
            'Quantity',
            'Total due',
            'Payment type',
            'Paid amount',
            'Status',
            'Cancelled on',
            'Cancellation reason',
        ],
        [
            'F%02d-%s-0000004' % (regie.pk, invoice4.created_at.strftime('%y-%m')),
            'API',
            'payer:3',
            'First3',
            'Name3',
            None,
            None,
            invoice4.created_at.strftime('%m/%d/%Y'),
            None,
            '10/01/2022',
            '10/31/2022',
            '10/31/2022',
            'no',
            'Event A',
            None,
            '1.00',
            '1.00',
            '1.00',
            None,
            '0.00',
            'Cancelled',
            invoice4.cancelled_at.strftime('%m/%d/%Y'),
            'Final pool deletion',
        ],
        [
            'F%02d-%s-0000003' % (regie.pk, invoice3.created_at.strftime('%y-%m')),
            'API' if orphan else 'Campaign',
            'payer:3',
            'First3',
            'Name3',
            'email3',
            'phone3',
            invoice3.created_at.strftime('%m/%d/%Y'),
            None,
            '10/01/2022',
            '10/31/2022',
            '10/31/2022',
            'yes',
            'Event A',
            None,
            '1.00',
            '1.00',
            '1.00',
            None,
            '0.00',
            'Not paid',
            None,
            None,
        ],
        [
            'F%02d-%s-0000002' % (regie.pk, invoice2.created_at.strftime('%y-%m')),
            'API' if orphan else 'Campaign',
            'payer:2',
            'First2',
            'Name2',
            None,
            None,
            invoice2.created_at.strftime('%m/%d/%Y'),
            None,
            '10/01/2022',
            '10/31/2022',
            '10/31/2022',
            'yes',
            'Event AA',
            '424242',
            '1.00',
            '1.00',
            '1.00',
            'Cash, Credit',
            '1.00',
            'Paid',
            None,
            None,
        ],
        [
            'F%02d-%s-0000001' % (regie.pk, invoice1.date_invoicing.strftime('%y-%m')),
            'API' if orphan else 'Campaign',
            'payer:1',
            'First1',
            'Name1',
            None,
            None,
            invoice1.created_at.strftime('%m/%d/%Y'),
            invoice1.date_invoicing.strftime('%m/%d/%Y'),
            '10/01/2022',
            '10/31/2022',
            '10/31/2022',
            'no',
            'Event A',
            '424242',
            '1.00',
            '1.20',
            '1.20',
            'Cash',
            '1.00',
            'Partially paid',
            None,
            None,
        ],
        [
            'F%02d-%s-0000001' % (regie.pk, invoice1.date_invoicing.strftime('%y-%m')),
            'API' if orphan else 'Campaign',
            'payer:1',
            'First1',
            'Name1',
            None,
            None,
            invoice1.created_at.strftime('%m/%d/%Y'),
            invoice1.date_invoicing.strftime('%m/%d/%Y'),
            '10/01/2022',
            '10/31/2022',
            '10/31/2022',
            'no',
            'Event B',
            '424243',
            '2.00',
            '1.00',
            '2.00',
            None,
            '0.00',
            'Not paid',
            None,
            None,
        ],
        [
            'F%02d-%s-0000001' % (regie.pk, invoice1.date_invoicing.strftime('%y-%m')),
            'API' if orphan else 'Campaign',
            'payer:1',
            'First1',
            'Name1',
            None,
            None,
            invoice1.created_at.strftime('%m/%d/%Y'),
            invoice1.date_invoicing.strftime('%m/%d/%Y'),
            '10/01/2022',
            '10/31/2022',
            '10/31/2022',
            'no',
            'Event A',
            '424242',
            '3.00',
            '1.00',
            '3.00',
            None,
            '0.00',
            'Not paid',
            None,
            None,
        ],
    ]

    # test filters
    params = [
        ({'number': invoice1.formatted_number}, 1),
        ({'number': invoice1.created_at.strftime('%y-%m')}, 5),
        ({'origin': 'api'}, 5 if orphan else 2),
        ({'created_at_after': today.strftime('%Y-%m-%d')}, 5),
        ({'created_at_after': tomorrow.strftime('%Y-%m-%d')}, 0),
        ({'created_at_before': yesterday.strftime('%Y-%m-%d')}, 0),
        ({'created_at_before': today.strftime('%Y-%m-%d')}, 5),
        ({'date_payment_deadline_after': '2022-10-31'}, 5),
        ({'date_payment_deadline_after': '2022-11-01'}, 0),
        ({'date_payment_deadline_before': '2022-10-30'}, 0),
        ({'date_payment_deadline_before': '2022-10-31'}, 5),
        ({'date_due_after': '2022-10-31'}, 5),
        ({'date_due_after': '2022-11-01'}, 0),
        ({'date_due_before': '2022-10-30'}, 0),
        ({'date_due_before': '2022-10-31'}, 5),
        ({'payment_number': payment1.formatted_number}, 1),
        ({'payment_number': payment1.created_at.strftime('%y-%m')}, 2),
        ({'payer_external_id': 'payer:1'}, 1),
        ({'payer_external_id': 'payer:2'}, 1),
        ({'payer_first_name': 'first'}, 5),
        ({'payer_first_name': 'first1'}, 1),
        ({'payer_last_name': 'name'}, 5),
        ({'payer_last_name': 'name1'}, 1),
        ({'payer_direct_debit': True}, 2),
        ({'user_external_id': 'user:1'}, 4),
        ({'user_external_id': 'user:2'}, 1),
        ({'user_first_name': 'user'}, 4),
        ({'user_first_name': 'user2'}, 1),
        ({'user_last_name': 'name'}, 4),
        ({'user_last_name': 'name1'}, 4),
        (
            {
                'total_amount_min': '1',
                'total_amount_min_lookup': 'gt',
            },
            1,
        ),
        (
            {
                'total_amount_min': '1',
                'total_amount_min_lookup': 'gte',
            },
            4,
        ),
        (
            {
                'total_amount_max': '6.2',
                'total_amount_max_lookup': 'lt',
            },
            4,
        ),
        (
            {
                'total_amount_max': '6.2',
                'total_amount_max_lookup': 'lte',
            },
            5,
        ),
        ({'paid': 'yes'}, 1),
        ({'paid': 'partially'}, 1),
        ({'paid': 'no'}, 3),
        ({'agenda': 'agenda-a'}, 2),
        ({'agenda': 'agenda-b'}, 1),
        ({'event': 'agenda-a@event-a'}, 1),
        ({'event': 'agenda-a@event-aa'}, 1),
        ({'event': 'agenda-b@event-b'}, 1),
        ({'accounting_code': '42'}, 0),
        ({'accounting_code': '424242'}, 2),
        ({'accounting_code': '424243'}, 1),
        ({'cancelled': 'yes'}, 1),
        ({'cancelled': 'no'}, 4),
        ({'collected': 'yes'}, 1),
        ({'collected': 'no'}, 4),
    ]
    for param, result in params:
        resp = app.get(
            '/manage/invoicing/regie/%s/invoices/' % regie.pk,
            params=param,
        )
        assert len(resp.pyquery('tr.invoice')) == result
        param['ods'] = True
        resp = app.get(
            '/manage/invoicing/regie/%s/invoices/' % regie.pk,
            params=param,
        )
        rows = list(get_ods_rows(resp))
        assert len(rows) == 1 + result

    app.get(
        '/manage/invoicing/ajax/regie/%s/invoice/%s/lines/' % (regie.pk, non_finalized_invoice.pk), status=404
    )


def test_regie_invoice_pdf(app, admin_user):
    regie = Regie.objects.create(label='Foo', main_colour='#9141ac', invoice_model='middle')
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
    previous_invoice.set_number()
    previous_invoice.save()
    invoice = Invoice.objects.create(
        label='Invoice from 01/09/2022 to 30/09/2022',
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline_displayed=datetime.date(2022, 10, 15),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_invoicing=datetime.date(2022, 9, 1),
        regie=regie,
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Name1',
        payer_address='41 rue des kangourous\n99999 Kangourou Ville',
        payer_direct_debit=False,
        previous_invoice=previous_invoice,
    )
    invoice.set_number()
    invoice.save()

    InvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        invoice=invoice,
        quantity=1.2,
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
    InvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 2),
        invoice=invoice,
        quantity=1,
        unit_amount=2,
        label='Label 12',
        user_external_id='user:2',
        user_first_name='User2',
        user_last_name='Name2',
    )
    InvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 3),
        invoice=invoice,
        quantity=1,
        unit_amount=3,
        label='Label 13',
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
        description='@overtaking@',
    )
    line = InvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        invoice=invoice,
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
    )
    InvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        invoice=invoice,
        quantity=0,
        unit_amount=1,
        label='Absence',
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
        details={
            'agenda': 'agenda-2',
            'primary_event': 'event-1',
            'status': 'absence',
            'partial_bookings': True,
        },
        event_slug='agenda-2@event-1',
        agenda_slug='agenda-2',
        activity_label='Agenda 2',
        description='a description',
    )
    invoice.refresh_from_db()

    app = login(app)
    resp = app.get('/manage/invoicing/regie/%s/invoice/%s/pdf/?html' % (regie.pk, invoice.pk))
    assert 'color: #9141ac;' in resp
    assert resp.pyquery('#document-label').text() == 'Invoice from 01/09/2022 to 30/09/2022'
    assert resp.pyquery('#regie-label').text() == 'Foo'
    assert resp.pyquery('address#to').text() == 'First1 Name1\n41 rue des kangourous\n99999 Kangourou Ville'
    assert resp.pyquery('dl#informations').text() == (
        'Invoice number:\nF%02d-22-09-0000001\nInitial invoice number:\nF%02d-%s-0000001\nDate:\n%s'
        % (
            regie.pk,
            regie.pk,
            previous_invoice.created_at.strftime('%y-%m'),
            date_format(invoice.date_invoicing, 'DATE_FORMAT'),
        )
    )
    assert [PyQuery(tr).text() for tr in resp.pyquery('table#invoice-lines tr')] == [
        '',
        'User1 Name1',
        'Services\nDetails\nUnit amount\nQuantity\nTotal amount',
        'Agenda 1',
        'Label 11\nFoo! Thu01, Fri02, Sat03\n1.00€\n1.2\n1.20€',
        'Agenda 2',
        'Agenda foobar\na description\n1.00€\n15\n15.00€',
        'Absence\na description\n1.00€\n0\n0.00€',
        '',
        'Label 13\n3.00€\n1\n3.00€',
        'Subtotal:\n19.20€',
        '',
        'User2 Name2',
        'Services\nDetails\nUnit amount\nQuantity\nTotal amount',
        'Label 12\n2.00€\n1\n2.00€',
        'Subtotal:\n2.00€',
        'Total amount:\n21.20€',
    ]
    assert resp.pyquery('p.deadline').text() == 'Payment deadline: 15/10/2022'
    assert len(resp.pyquery('p.payments')) == 0
    assert len(resp.pyquery('p.invoice-paid')) == 0
    assert len(resp.pyquery('table#invoice-lines-details')) == 0
    invoice.refresh_from_db()
    invoice.date_payment_deadline_displayed = None
    invoice.date_invoicing = None
    invoice.save()
    resp = app.get('/manage/invoicing/regie/%s/invoice/%s/pdf/?html' % (regie.pk, invoice.pk))
    assert resp.pyquery('dl#informations').text() == (
        'Invoice number:\nF%02d-22-09-0000001\nInitial invoice number:\nF%02d-%s-0000001\nDate:\n%s'
        % (
            regie.pk,
            regie.pk,
            previous_invoice.created_at.strftime('%y-%m'),
            date_format(localtime(invoice.created_at), 'DATE_FORMAT'),
        )
    )
    assert resp.pyquery('p.deadline').text() == 'Payment deadline: 31/10/2022'
    resp = app.get('/manage/invoicing/regie/%s/invoice/%s/dynamic/pdf/?html' % (regie.pk, invoice.pk))
    assert [PyQuery(tr).text() for tr in resp.pyquery('table#invoice-lines tr')] == [
        '',
        'User1 Name1',
        'Services\nDetails\nUnit amount\nQuantity\nTotal amount',
        'Agenda 1',
        'Label 11\nFoo! Thu01, Fri02, Sat03\n1.00€\n1.2\n1.20€',
        'Agenda 2',
        'Agenda foobar\na description\n1.00€\n15\n15.00€',
        'Absence\na description\n1.00€\n0\n0.00€',
        '',
        'Label 13\n3.00€\n1\n3.00€',
        'Subtotal:\n19.20€',
        '',
        'User2 Name2',
        'Services\nDetails\nUnit amount\nQuantity\nTotal amount',
        'Label 12\n2.00€\n1\n2.00€',
        'Subtotal:\n2.00€',
        'Total amount:\n21.20€',
    ]
    assert len(resp.pyquery('p.payments')) == 0
    assert len(resp.pyquery('p.invoice-paid')) == 0
    assert len(resp.pyquery('table#invoice-lines-details')) == 0

    regie.invoice_model = 'basic'
    regie.save()
    resp = app.get('/manage/invoicing/regie/%s/invoice/%s/pdf/?html' % (regie.pk, invoice.pk))
    assert [PyQuery(tr).text() for tr in resp.pyquery('table#invoice-lines tr')] == [
        '',
        'User1 Name1',
        'Services\nDetails\nUnit amount\nQuantity\nTotal amount',
        'Agenda 1',
        'Label 11\nFoo!\n1.00€\n1.2\n1.20€',
        'Agenda 2',
        'Agenda foobar\n1.00€\n15\n15.00€',
        'Absence\n1.00€\n0\n0.00€',
        '',
        'Label 13\n3.00€\n1\n3.00€',
        'Subtotal:\n19.20€',
        '',
        'User2 Name2',
        'Services\nDetails\nUnit amount\nQuantity\nTotal amount',
        'Label 12\n2.00€\n1\n2.00€',
        'Subtotal:\n2.00€',
        'Total amount:\n21.20€',
    ]
    assert len(resp.pyquery('p.payments')) == 0
    assert len(resp.pyquery('p.invoice-paid')) == 0
    assert len(resp.pyquery('table#invoice-lines-details')) == 0

    regie.invoice_model = 'full'
    regie.save()
    resp = app.get('/manage/invoicing/regie/%s/invoice/%s/pdf/?html' % (regie.pk, invoice.pk))
    assert [PyQuery(tr).text() for tr in resp.pyquery('table#invoice-lines tr')] == [
        '',
        'User1 Name1',
        'Services\nDetails\nUnit amount\nQuantity\nTotal amount',
        'Agenda 1',
        'Label 11\nFoo!\n1.00€\n1.2\n1.20€',
        'Agenda 2',
        'Agenda foobar\n1.00€\n15\n15.00€',
        'Absence\n1.00€\n0\n0.00€',
        '',
        'Label 13\n3.00€\n1\n3.00€',
        'Subtotal:\n19.20€',
        '',
        'User2 Name2',
        'Services\nDetails\nUnit amount\nQuantity\nTotal amount',
        'Label 12\n2.00€\n1\n2.00€',
        'Subtotal:\n2.00€',
        'Total amount:\n21.20€',
    ]
    assert len(resp.pyquery('p.payments')) == 0
    assert len(resp.pyquery('p.invoice-paid')) == 0
    assert len(resp.pyquery('table#invoice-lines-details')) == 1
    assert [PyQuery(tr).text() for tr in resp.pyquery('table#invoice-lines-details tr')] == [
        '',
        'User1 Name1',
        'Services\nDetails',
        'Agenda 1',
        'Label 11\nFoo! Thu01, Fri02, Sat03',
        'Agenda 2',
        'Agenda foobar\na description',
        'Absence\na description',
    ]

    app.get('/manage/invoicing/regie/%s/invoice/%s/pdf/?html' % (0, invoice.pk), status=404)
    app.get('/manage/invoicing/regie/%s/invoice/%s/pdf/?html' % (regie.pk, 0), status=404)
    other_regie = Regie.objects.create(label='Foo')
    app.get('/manage/invoicing/regie/%s/invoice/%s/pdf/?html' % (other_regie.pk, invoice.pk), status=404)

    appearance_settings = AppearanceSettings.singleton()
    appearance_settings.address = '<p>Foo bar<br>Streetname</p>'
    appearance_settings.extra_info = '<p>Opening hours...</p>'
    appearance_settings.save()
    resp = app.get('/manage/invoicing/regie/%s/invoice/%s/pdf/?html' % (regie.pk, invoice.pk))
    assert appearance_settings.address in resp.text
    assert appearance_settings.extra_info in resp.text

    regie.custom_address = '<p>Foo bar<br>Other streetname</p>'
    regie.save()
    resp = app.get('/manage/invoicing/regie/%s/invoice/%s/pdf/?html' % (regie.pk, invoice.pk))
    assert regie.custom_address in resp.text
    assert appearance_settings.extra_info in resp.text

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
        status='completed',
    )
    invoice.pool = pool
    invoice.save()
    app.get('/manage/invoicing/regie/%s/invoice/%s/pdf/?html' % (regie.pk, invoice.pk), status=404)
    invoice.pool = None
    invoice.save()
    app.get('/manage/invoicing/regie/%s/invoice/%s/pdf/?html' % (regie.pk, invoice.pk), status=200)
    invoice.cancelled_at = now()
    invoice.save()
    app.get('/manage/invoicing/regie/%s/invoice/%s/pdf/?html' % (regie.pk, invoice.pk), status=200)

    # collected invoice without payment
    collection = CollectionDocket.objects.create(regie=regie, date_end=now().date(), draft=False)
    invoice.cancelled_at = None
    invoice.collection = collection
    invoice.save()
    resp = app.get('/manage/invoicing/regie/%s/invoice/%s/pdf/?html' % (regie.pk, invoice.pk))
    assert [PyQuery(tr).text() for tr in resp.pyquery('table#invoice-lines tr')] == [
        '',
        'User1 Name1',
        'Services\nDetails\nUnit amount\nQuantity\nTotal amount',
        'Agenda 1',
        'Label 11\nFoo!\n1.00€\n1.2\n1.20€',
        'Agenda 2',
        'Agenda foobar\n1.00€\n15\n15.00€',
        'Absence\n1.00€\n0\n0.00€',
        '',
        'Label 13\n3.00€\n1\n3.00€',
        'Subtotal:\n19.20€',
        '',
        'User2 Name2',
        'Services\nDetails\nUnit amount\nQuantity\nTotal amount',
        'Label 12\n2.00€\n1\n2.00€',
        'Subtotal:\n2.00€',
        'Debt amount:\n21.20€',
        'Debt forwarded to the Treasury on:\n%s' % collection.created_at.strftime('%d/%m/%Y'),
    ]
    assert len(resp.pyquery('p.payments')) == 0
    assert len(resp.pyquery('p.invoice-paid')) == 0
    resp = app.get('/manage/invoicing/regie/%s/invoice/%s/dynamic/pdf/?html' % (regie.pk, invoice.pk))
    assert [PyQuery(tr).text() for tr in resp.pyquery('table#invoice-lines tr')] == [
        '',
        'User1 Name1',
        'Services\nDetails\nUnit amount\nQuantity\nTotal amount',
        'Agenda 1',
        'Label 11\nFoo!\n1.00€\n1.2\n1.20€',
        'Agenda 2',
        'Agenda foobar\n1.00€\n15\n15.00€',
        'Absence\n1.00€\n0\n0.00€',
        '',
        'Label 13\n3.00€\n1\n3.00€',
        'Subtotal:\n19.20€',
        '',
        'User2 Name2',
        'Services\nDetails\nUnit amount\nQuantity\nTotal amount',
        'Label 12\n2.00€\n1\n2.00€',
        'Subtotal:\n2.00€',
        'Debt amount:\n21.20€',
        'Debt forwarded to the Treasury on:\n%s' % collection.created_at.strftime('%d/%m/%Y'),
    ]
    assert len(resp.pyquery('p.payments')) == 0
    assert len(resp.pyquery('p.invoice-paid')) == 0

    # collected invoice with partial payment
    PaymentType.create_defaults(regie)
    payment = Payment.objects.create(
        regie=regie,
        amount=5,
        payment_type=PaymentType.objects.get(regie=regie, slug='check'),
    )
    InvoiceLinePayment.objects.create(
        payment=payment,
        line=line,
        amount=5,
    )
    resp = app.get('/manage/invoicing/regie/%s/invoice/%s/pdf/?html' % (regie.pk, invoice.pk))
    assert [PyQuery(tr).text() for tr in resp.pyquery('table#invoice-lines tr')] == [
        '',
        'User1 Name1',
        'Services\nDetails\nUnit amount\nQuantity\nTotal amount',
        'Agenda 1',
        'Label 11\nFoo!\n1.00€\n1.2\n1.20€',
        'Agenda 2',
        'Agenda foobar\n1.00€\n15\n15.00€',
        'Absence\n1.00€\n0\n0.00€',
        '',
        'Label 13\n3.00€\n1\n3.00€',
        'Subtotal:\n19.20€',
        '',
        'User2 Name2',
        'Services\nDetails\nUnit amount\nQuantity\nTotal amount',
        'Label 12\n2.00€\n1\n2.00€',
        'Subtotal:\n2.00€',
        'Total amount:\n21.20€',
        'Paid amount:\n5.00€',
        'Debt amount:\n16.20€',
        'Debt forwarded to the Treasury on:\n%s' % collection.created_at.strftime('%d/%m/%Y'),
    ]
    assert len(resp.pyquery('p.payments')) == 0
    assert len(resp.pyquery('p.invoice-paid')) == 0
    resp = app.get('/manage/invoicing/regie/%s/invoice/%s/dynamic/pdf/?html' % (regie.pk, invoice.pk))
    assert [PyQuery(tr).text() for tr in resp.pyquery('table#invoice-lines tr')] == [
        '',
        'User1 Name1',
        'Services\nDetails\nUnit amount\nQuantity\nTotal amount',
        'Agenda 1',
        'Label 11\nFoo!\n1.00€\n1.2\n1.20€',
        'Agenda 2',
        'Agenda foobar\n1.00€\n15\n15.00€',
        'Absence\n1.00€\n0\n0.00€',
        '',
        'Label 13\n3.00€\n1\n3.00€',
        'Subtotal:\n19.20€',
        '',
        'User2 Name2',
        'Services\nDetails\nUnit amount\nQuantity\nTotal amount',
        'Label 12\n2.00€\n1\n2.00€',
        'Subtotal:\n2.00€',
        'Total amount:\n21.20€',
        'Paid amount:\n5.00€',
        'Debt amount:\n16.20€',
        'Debt forwarded to the Treasury on:\n%s' % collection.created_at.strftime('%d/%m/%Y'),
    ]
    assert len(resp.pyquery('p.payments')) == 0
    assert len(resp.pyquery('p.invoice-paid')) == 0

    # collected invoice, totally paid by the collection
    payment_type, dummy = PaymentType.objects.get_or_create(regie=regie, slug='collect')
    Payment.make_payment(
        regie=regie,
        invoices=[invoice],
        amount=decimal.Decimal('16.20'),
        payment_type=payment_type,
    )
    resp = app.get('/manage/invoicing/regie/%s/invoice/%s/pdf/?html' % (regie.pk, invoice.pk))
    assert [PyQuery(tr).text() for tr in resp.pyquery('table#invoice-lines tr')] == [
        '',
        'User1 Name1',
        'Services\nDetails\nUnit amount\nQuantity\nTotal amount',
        'Agenda 1',
        'Label 11\nFoo!\n1.00€\n1.2\n1.20€',
        'Agenda 2',
        'Agenda foobar\n1.00€\n15\n15.00€',
        'Absence\n1.00€\n0\n0.00€',
        '',
        'Label 13\n3.00€\n1\n3.00€',
        'Subtotal:\n19.20€',
        '',
        'User2 Name2',
        'Services\nDetails\nUnit amount\nQuantity\nTotal amount',
        'Label 12\n2.00€\n1\n2.00€',
        'Subtotal:\n2.00€',
        'Total amount:\n21.20€',
        'Paid amount:\n5.00€',
        'Debt amount:\n16.20€',
        'Debt forwarded to the Treasury on:\n%s' % collection.created_at.strftime('%d/%m/%Y'),
    ]
    assert len(resp.pyquery('p.payments')) == 0
    assert len(resp.pyquery('p.invoice-paid')) == 0
    resp = app.get('/manage/invoicing/regie/%s/invoice/%s/dynamic/pdf/?html' % (regie.pk, invoice.pk))
    assert [PyQuery(tr).text() for tr in resp.pyquery('table#invoice-lines tr')] == [
        '',
        'User1 Name1',
        'Services\nDetails\nUnit amount\nQuantity\nTotal amount',
        'Agenda 1',
        'Label 11\nFoo!\n1.00€\n1.2\n1.20€',
        'Agenda 2',
        'Agenda foobar\n1.00€\n15\n15.00€',
        'Absence\n1.00€\n0\n0.00€',
        '',
        'Label 13\n3.00€\n1\n3.00€',
        'Subtotal:\n19.20€',
        '',
        'User2 Name2',
        'Services\nDetails\nUnit amount\nQuantity\nTotal amount',
        'Label 12\n2.00€\n1\n2.00€',
        'Subtotal:\n2.00€',
        'Total amount:\n21.20€',
        'Paid amount:\n5.00€',
        'Debt amount:\n16.20€',
        'Debt forwarded to the Treasury on:\n%s' % collection.created_at.strftime('%d/%m/%Y'),
    ]
    assert len(resp.pyquery('p.payments')) == 0
    assert len(resp.pyquery('p.invoice-paid')) == 0

    # invoice with partial payment
    InvoiceLinePayment.objects.all().delete()
    Payment.objects.all().delete()
    invoice.collection = None
    invoice.save()
    payment_type, dummy = PaymentType.objects.get_or_create(regie=regie, slug='check')
    payment1 = Payment.make_payment(
        regie=regie,
        invoices=[invoice],
        amount=decimal.Decimal('16.20'),
        payment_type=payment_type,
    )
    resp = app.get('/manage/invoicing/regie/%s/invoice/%s/pdf/?html' % (regie.pk, invoice.pk))
    assert [PyQuery(tr).text() for tr in resp.pyquery('table#invoice-lines tr')] == [
        '',
        'User1 Name1',
        'Services\nDetails\nUnit amount\nQuantity\nTotal amount',
        'Agenda 1',
        'Label 11\nFoo!\n1.00€\n1.2\n1.20€',
        'Agenda 2',
        'Agenda foobar\n1.00€\n15\n15.00€',
        'Absence\n1.00€\n0\n0.00€',
        '',
        'Label 13\n3.00€\n1\n3.00€',
        'Subtotal:\n19.20€',
        '',
        'User2 Name2',
        'Services\nDetails\nUnit amount\nQuantity\nTotal amount',
        'Label 12\n2.00€\n1\n2.00€',
        'Subtotal:\n2.00€',
        'Total amount:\n21.20€',
    ]
    assert len(resp.pyquery('p.payments')) == 0
    assert len(resp.pyquery('p.invoice-paid')) == 0
    resp = app.get('/manage/invoicing/regie/%s/invoice/%s/dynamic/pdf/?html' % (regie.pk, invoice.pk))
    assert [PyQuery(tr).text() for tr in resp.pyquery('table#invoice-lines tr')] == [
        '',
        'User1 Name1',
        'Services\nDetails\nUnit amount\nQuantity\nTotal amount',
        'Agenda 1',
        'Label 11\nFoo!\n1.00€\n1.2\n1.20€',
        'Agenda 2',
        'Agenda foobar\n1.00€\n15\n15.00€',
        'Absence\n1.00€\n0\n0.00€',
        '',
        'Label 13\n3.00€\n1\n3.00€',
        'Subtotal:\n19.20€',
        '',
        'User2 Name2',
        'Services\nDetails\nUnit amount\nQuantity\nTotal amount',
        'Label 12\n2.00€\n1\n2.00€',
        'Subtotal:\n2.00€',
        'Invoiced amount:\n21.20€',
        'Paid amount:\n16.20€',
        'Remaining amount:\n5.00€',
    ]
    assert resp.pyquery('p.payments').text() == (
        'Details of payments recorded on this invoice\n'
        '- %s of %s amount 16.20€ of Check'
        % (payment1.formatted_number, payment1.created_at.strftime('%d/%m/%Y'))
    )
    assert len(resp.pyquery('p.invoice-paid')) == 0

    # invoice totally paid
    payment_type, dummy = PaymentType.objects.get_or_create(regie=regie, slug='credit')
    payment2 = Payment.make_payment(
        regie=regie,
        invoices=[invoice],
        amount=5,
        payment_type=payment_type,
    )
    credit1 = Credit.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        regie=regie,
    )
    credit1.set_number()
    credit1.save()
    CreditLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        quantity=4,
        unit_amount=1,
        credit=credit1,
    )
    CreditAssignment.objects.create(
        invoice=invoice,
        payment=payment2,
        credit=credit1,
        amount=4,
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
        invoice=invoice,
        payment=payment2,
        credit=credit2,
        amount=1,
    )
    resp = app.get('/manage/invoicing/regie/%s/invoice/%s/pdf/?html' % (regie.pk, invoice.pk))
    assert [PyQuery(tr).text() for tr in resp.pyquery('table#invoice-lines tr')] == [
        '',
        'User1 Name1',
        'Services\nDetails\nUnit amount\nQuantity\nTotal amount',
        'Agenda 1',
        'Label 11\nFoo!\n1.00€\n1.2\n1.20€',
        'Agenda 2',
        'Agenda foobar\n1.00€\n15\n15.00€',
        'Absence\n1.00€\n0\n0.00€',
        '',
        'Label 13\n3.00€\n1\n3.00€',
        'Subtotal:\n19.20€',
        '',
        'User2 Name2',
        'Services\nDetails\nUnit amount\nQuantity\nTotal amount',
        'Label 12\n2.00€\n1\n2.00€',
        'Subtotal:\n2.00€',
        'Total amount:\n21.20€',
    ]
    assert len(resp.pyquery('p.payments')) == 0
    assert len(resp.pyquery('p.invoice-paid')) == 0
    resp = app.get('/manage/invoicing/regie/%s/invoice/%s/dynamic/pdf/?html' % (regie.pk, invoice.pk))
    assert [PyQuery(tr).text() for tr in resp.pyquery('table#invoice-lines tr')] == [
        '',
        'User1 Name1',
        'Services\nDetails\nUnit amount\nQuantity\nTotal amount',
        'Agenda 1',
        'Label 11\nFoo!\n1.00€\n1.2\n1.20€',
        'Agenda 2',
        'Agenda foobar\n1.00€\n15\n15.00€',
        'Absence\n1.00€\n0\n0.00€',
        '',
        'Label 13\n3.00€\n1\n3.00€',
        'Subtotal:\n19.20€',
        '',
        'User2 Name2',
        'Services\nDetails\nUnit amount\nQuantity\nTotal amount',
        'Label 12\n2.00€\n1\n2.00€',
        'Subtotal:\n2.00€',
        'Invoiced amount:\n21.20€',
        'Paid amount:\n16.20€',
        'Paid amount with credit:\n5.00€',
        'Remaining amount:\n0.00€',
    ]
    assert resp.pyquery('p.payments').text() == (
        'Details of payments recorded on this invoice\n'
        '- %s of %s amount 16.20€ of Check\n'
        '- %s of %s amount 5.00€ of Credit %s %s'
        % (
            payment1.formatted_number,
            payment1.created_at.strftime('%d/%m/%Y'),
            payment2.formatted_number,
            payment2.created_at.strftime('%d/%m/%Y'),
            credit1.formatted_number,
            credit2.formatted_number,
        )
    )
    assert resp.pyquery('p.invoice-paid').text() == (
        'Total amount paid - Invoice paid*.' '\n*Subject to actual cashing of payments by check.'
    )


def test_regie_invoice_payments_pdf(app, admin_user):
    regie = Regie.objects.create(
        label='Foo',
        controller_name='Le régisseur principal',
        city_name='Kangourou Ville',
        main_colour='#9141ac',
    )
    assert regie.certificate_model == ''
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
        status='completed',
    )
    PaymentType.create_defaults(regie)
    invoice = Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        regie=regie,
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Name1',
        payer_address='41 rue des kangourous\n99999 Kangourou Ville',
        payer_direct_debit=False,
        label='Invoice Label',
    )
    invoice.set_number()
    invoice.save()

    line = InvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        invoice=invoice,
        quantity=1,
        unit_amount=40,
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
        description='Thu01, Fri02, Sat03',
    )

    payment1 = Payment.objects.create(
        regie=regie,
        amount=5,
        payment_type=PaymentType.objects.get(regie=regie, slug='check'),
        date_payment=datetime.date(2022, 9, 1),
    )
    payment1.set_number()
    payment1.save()
    InvoiceLinePayment.objects.create(
        payment=payment1,
        line=line,
        amount=5,
    )
    payment2 = Payment.objects.create(
        regie=regie,
        amount=35,
        payment_type=PaymentType.objects.get(regie=regie, slug='online'),
    )
    payment2.set_number()
    payment2.save()
    InvoiceLinePayment.objects.create(
        payment=payment2,
        line=line,
        amount=35,
    )
    invoice.refresh_from_db()
    assert invoice.remaining_amount == 0

    app = login(app)
    resp = app.get('/manage/invoicing/regie/%s/invoice/%s/payments/pdf/?html' % (regie.pk, invoice.pk))
    assert 'color: #9141ac;' in resp
    assert resp.pyquery('#document-label').text() == 'Payments certificate'
    assert resp.pyquery('.address-to-container').text() == (
        'Invoiced account:\nFirst1 Name1 (1)\nInvoicing address:\n41 rue des kangourous\n99999 Kangourou Ville\n'
        'Invoice number:\nF%02d-%s-0000001\nInvoice object:\nInvoice Label'
    ) % (
        regie.pk,
        invoice.created_at.strftime('%y-%m'),
    )
    assert (
        resp.pyquery('p#informations').text()
        == 'Hereby certifies that I have received the amount of 40.00€ for account First1 Name1 (1) as follows:'
    )
    assert [PyQuery(tr).text() for tr in resp.pyquery('table#payments-lines thead tr')] == [
        'Number\nDate\nPayment type\nAmount',
    ]
    assert [PyQuery(tr).text() for tr in resp.pyquery.find('table#payments-lines tbody tr')] == [
        'R%02d-%s-0000001\n%s\nCheck\n5.00€'
        % (
            regie.pk,
            payment1.date_payment.strftime('%y-%m'),
            payment1.date_payment.strftime('%d/%m/%Y'),
        ),
        'R%02d-%s-0000001\n%s\nOnline\n35.00€'
        % (
            regie.pk,
            payment2.created_at.strftime('%y-%m'),
            payment2.created_at.strftime('%d/%m/%Y'),
        ),
    ]
    assert resp.pyquery(
        '#regie-signature'
    ).text() == 'Le régisseur principal\nKangourou Ville, on %s' % invoice.created_at.strftime('%d/%m/%Y')
    assert len(resp.pyquery('table#invoice-lines')) == 0
    assert len(resp.pyquery('table#invoice-lines-details')) == 0

    regie.city_name = ''
    regie.save()
    resp = app.get('/manage/invoicing/regie/%s/invoice/%s/payments/pdf/?html' % (regie.pk, invoice.pk))
    assert resp.pyquery(
        '#regie-signature'
    ).text() == 'Le régisseur principal\non %s' % invoice.created_at.strftime('%d/%m/%Y')

    regie.certificate_model = 'basic'
    regie.save()
    resp = app.get('/manage/invoicing/regie/%s/invoice/%s/payments/pdf/?html' % (regie.pk, invoice.pk))
    assert [PyQuery(tr).text() for tr in resp.pyquery('table#invoice-lines tr')] == [
        '',
        'User1 Name1',
        'Services\nDetails\nUnit amount\nQuantity\nTotal amount',
        '40.00€\n1\n40.00€',
        'Total amount:\n40.00€',
    ]
    assert len(resp.pyquery('table#invoice-lines-details')) == 0

    regie.certificate_model = 'middle'
    regie.save()
    resp = app.get('/manage/invoicing/regie/%s/invoice/%s/payments/pdf/?html' % (regie.pk, invoice.pk))
    assert [PyQuery(tr).text() for tr in resp.pyquery('table#invoice-lines tr')] == [
        '',
        'User1 Name1',
        'Services\nDetails\nUnit amount\nQuantity\nTotal amount',
        'Thu01, Fri02, Sat03\n40.00€\n1\n40.00€',
        'Total amount:\n40.00€',
    ]
    assert len(resp.pyquery('table#invoice-lines-details')) == 0

    regie.certificate_model = 'full'
    regie.save()
    resp = app.get('/manage/invoicing/regie/%s/invoice/%s/payments/pdf/?html' % (regie.pk, invoice.pk))
    assert [PyQuery(tr).text() for tr in resp.pyquery('table#invoice-lines tr')] == [
        '',
        'User1 Name1',
        'Services\nDetails\nUnit amount\nQuantity\nTotal amount',
        '40.00€\n1\n40.00€',
        'Total amount:\n40.00€',
    ]
    assert len(resp.pyquery('table#invoice-lines-details')) == 1
    assert [PyQuery(tr).text() for tr in resp.pyquery('table#invoice-lines-details tr')] == [
        '',
        'User1 Name1',
        'Services\nDetails',
        'Thu01, Fri02, Sat03',
    ]

    app.get('/manage/invoicing/regie/%s/invoice/%s/payments/pdf/?html' % (0, invoice.pk), status=404)
    app.get('/manage/invoicing/regie/%s/invoice/%s/payments/pdf/?html' % (regie.pk, 0), status=404)
    other_regie = Regie.objects.create(label='Foo')
    app.get(
        '/manage/invoicing/regie/%s/invoice/%s/payments/pdf/?html' % (other_regie.pk, invoice.pk), status=404
    )
    line = InvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        invoice=invoice,
        quantity=1,
        unit_amount=40,
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
    )
    invoice.refresh_from_db()
    assert invoice.remaining_amount > 0
    app.get('/manage/invoicing/regie/%s/invoice/%s/payments/pdf/?html' % (regie.pk, invoice.pk), status=404)

    line.delete()
    invoice.refresh_from_db()
    assert invoice.remaining_amount == 0
    invoice.pool = pool
    invoice.save()
    app.get('/manage/invoicing/regie/%s/invoice/%s/payments/pdf/?html' % (regie.pk, invoice.pk), status=200)
    campaign.finalized = False
    campaign.save()
    app.get('/manage/invoicing/regie/%s/invoice/%s/payments/pdf/?html' % (regie.pk, invoice.pk), status=404)
    campaign.finalized = True
    campaign.save()
    invoice.cancelled_at = now()
    invoice.save()
    app.get('/manage/invoicing/regie/%s/invoice/%s/payments/pdf/?html' % (regie.pk, invoice.pk), status=200)


def test_regie_invoice_cancel(app, admin_user):
    regie = Regie.objects.create(
        label='Foo',
    )
    PaymentType.create_defaults(regie)
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
        date_due=datetime.date(2022, 10, 31),
        regie=regie,
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Name1',
        payer_address='41 rue des kangourous\n99999 Kangourou Ville',
        payer_direct_debit=False,
        cancel_callback_url='http://cancel.com',
        pool=finalized_pool,
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

    InvoiceLine.objects.create(
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
        amount=1,
        payment_type=PaymentType.objects.get(regie=regie, slug='cash'),
    )
    payment.set_number()
    payment.save()
    InvoiceLinePayment.objects.create(
        payment=payment,
        line=invoice_line2,
        amount=1,
    )
    invoice2.refresh_from_db()
    assert invoice2.remaining_amount == 49
    assert invoice2.paid_amount == 1

    cancellation_reason = InvoiceCancellationReason.objects.create(label='Mistake')
    InvoiceCancellationReason.objects.create(label='Disabled', disabled=True)

    app = login(app)
    resp = app.get('/manage/invoicing/ajax/regie/%s/invoice/%s/lines/' % (regie.pk, invoice1.pk))
    resp = resp.click(href='/manage/invoicing/regie/%s/invoice/%s/cancel/' % (regie.pk, invoice1.pk))
    assert resp.form['cancellation_reason'].options == [
        ('', True, '---------'),
        (str(cancellation_reason.pk), False, 'Mistake'),
    ]
    resp.form['cancellation_reason'] = cancellation_reason.pk
    resp.form['cancellation_description'] = 'foo bar blah'
    with mock.patch('lingo.utils.requests_wrapper.RequestsSession.send') as mock_send:
        resp = resp.form.submit()
    assert [x[0][0].url for x in mock_send.call_args_list] == [
        'http://cancel.com/',
    ]
    assert resp.location.endswith(
        '/manage/invoicing/regie/%s/invoices/?number=%s' % (regie.pk, invoice1.formatted_number)
    )
    invoice1.refresh_from_db()
    assert invoice1.cancelled_at is not None
    assert invoice1.cancelled_by == admin_user
    assert invoice1.cancellation_reason == cancellation_reason
    assert invoice1.cancellation_description == 'foo bar blah'
    assert invoice1.lines.count() == 1
    invoice2.refresh_from_db()
    assert invoice2.cancelled_at is None

    # already cancelled
    app.get('/manage/invoicing/regie/%s/invoice/%s/cancel/' % (regie.pk, invoice1.pk), status=404)
    invoice1.cancelled_at = None
    invoice1.save()

    # other regie
    other_regie = Regie.objects.create(label='Foo')
    app.get('/manage/invoicing/regie/%s/invoice/%s/cancel/' % (other_regie.pk, invoice1.pk), status=404)

    # invoice with payment
    app.get('/manage/invoicing/regie/%s/invoice/%s/cancel/' % (regie.pk, invoice2.pk), status=404)

    # non finalized campaign
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
    invoice1.pool = non_finalized_pool
    invoice1.save()
    app.get('/manage/invoicing/regie/%s/invoice/%s/cancel/' % (regie.pk, invoice1.pk), status=404)

    # collected invoice
    collection = CollectionDocket.objects.create(regie=regie, date_end=now().date(), draft=False)
    invoice1.pool = None
    invoice1.collection = collection
    invoice1.save()
    app.get('/manage/invoicing/regie/%s/invoice/%s/cancel/' % (regie.pk, invoice1.pk), status=404)


def test_regie_invoice_edit_dates(app, admin_user):
    regie = Regie.objects.create(
        label='Foo',
    )
    PaymentType.create_defaults(regie)
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
    invoice = Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        regie=regie,
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Name1',
        payer_address='41 rue des kangourous\n99999 Kangourou Ville',
        payer_direct_debit=False,
        cancel_callback_url='http://cancel.com',
        pool=finalized_pool,
    )
    invoice.set_number()
    invoice.save()

    app = login(app)
    resp = app.get('/manage/invoicing/ajax/regie/%s/invoice/%s/lines/' % (regie.pk, invoice.pk))
    resp = resp.click(href='/manage/invoicing/regie/%s/invoice/%s/edit/dates/' % (regie.pk, invoice.pk))
    assert resp.form['date_publication'].value == '2022-10-01'
    assert resp.form['date_payment_deadline_displayed'].value == ''
    assert resp.form['date_payment_deadline'].value == '2022-10-31'
    assert resp.form['date_due'].value == '2022-10-31'
    resp.form['date_publication'] = '2022-10-31'
    resp.form['date_payment_deadline_displayed'] = '2022-10-20'
    resp.form['date_payment_deadline'] = '2022-10-30'
    resp.form['date_due'] = '2022-10-29'
    resp = resp.form.submit()
    assert resp.context['form'].errors == {
        'date_payment_deadline': ['Payment deadline must be greater than publication date.']
    }

    resp.form['date_publication'] = '2022-10-30'
    resp.form['date_payment_deadline'] = '2022-10-31'
    resp = resp.form.submit()
    assert resp.context['form'].errors == {'date_due': ['Due date must be greater than payment deadline.']}

    resp.form['date_publication'] = '2022-10-29'
    resp.form['date_payment_deadline'] = '2022-10-30'
    resp.form['date_due'] = '2022-10-31'
    resp = resp.form.submit()
    assert resp.location.endswith(
        '/manage/invoicing/regie/%s/invoices/?number=%s' % (regie.pk, invoice.formatted_number)
    )
    invoice.refresh_from_db()
    assert invoice.date_publication == datetime.date(2022, 10, 29)
    assert invoice.date_payment_deadline_displayed == datetime.date(2022, 10, 20)
    assert invoice.date_payment_deadline == datetime.date(2022, 10, 30)
    assert invoice.date_due == datetime.date(2022, 10, 31)

    # invoice cancelled
    invoice.cancelled_at = now()
    invoice.save()
    app.get('/manage/invoicing/regie/%s/invoice/%s/edit/dates/' % (regie.pk, invoice.pk), status=404)

    # other regie
    invoice.cancelled_at = None
    invoice.save()
    other_regie = Regie.objects.create(label='Foo')
    app.get('/manage/invoicing/regie/%s/invoice/%s/edit/dates/' % (other_regie.pk, invoice.pk), status=404)

    # non finalized campaign
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
    invoice.pool = non_finalized_pool
    invoice.save()
    app.get('/manage/invoicing/regie/%s/invoice/%s/edit/dates/' % (regie.pk, invoice.pk), status=404)

    # collected invoice
    collection = CollectionDocket.objects.create(regie=regie, date_end=now().date(), draft=False)
    invoice.pool = None
    invoice.collection = collection
    invoice.save()
    app.get('/manage/invoicing/regie/%s/invoice/%s/edit/dates/' % (regie.pk, invoice.pk), status=404)
