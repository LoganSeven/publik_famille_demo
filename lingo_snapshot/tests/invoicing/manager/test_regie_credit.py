import datetime
import decimal

import pytest
from django.utils.formats import date_format
from django.utils.timezone import localtime, now
from pyquery import PyQuery

from lingo.agendas.models import Agenda
from lingo.invoicing.models import (
    AppearanceSettings,
    Campaign,
    Credit,
    CreditAssignment,
    CreditCancellationReason,
    CreditLine,
    Invoice,
    Payment,
    PaymentType,
    Pool,
    Refund,
    Regie,
)
from tests.utils import login

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize('orphan', [True, False])
def test_regie_credits(app, admin_user, orphan):
    regie = Regie.objects.create(label='Foo')
    Agenda.objects.create(label='Agenda A', regie=regie)
    Agenda.objects.create(label='Agenda B', regie=regie)
    PaymentType.create_defaults(regie)
    today = now().date()
    tomorrow = today + datetime.timedelta(days=1)
    yesterday = today - datetime.timedelta(days=1)
    date_invoicing = yesterday if yesterday.month == today.month else tomorrow
    invoice = Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        regie=regie,
    )
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
    credit1 = Credit.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_invoicing=date_invoicing,
        regie=regie,
        label='Credit from 01/09/2022',
        pool=pool1,
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Name1',
        payer_address='41 rue des kangourous\n99999 Kangourou Ville',
        previous_invoice=previous_invoice,
        origin='api' if orphan else 'campaign',
    )
    credit1.set_number()
    credit1.save()
    credit2 = Credit.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        regie=regie,
        pool=pool2,
        payer_external_id='payer:2',
        payer_first_name='First2',
        payer_last_name='Name2',
        payer_address='42 rue des kangourous\n99999 Kangourou Ville',
        origin='api' if orphan else 'campaign',
    )
    credit2.set_number()
    credit2.save()
    credit3 = Credit.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        regie=regie,
        pool=pool2,
        payer_external_id='payer:3',
        payer_first_name='First3',
        payer_last_name='Name3',
        payer_address='43 rue des kangourous\n99999 Kangourou Ville',
        origin='api' if orphan else 'campaign',
    )
    credit3.set_number()
    credit3.save()
    credit4 = Credit.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        regie=regie,
        payer_external_id='payer:3',
        payer_first_name='First3',
        payer_last_name='Name3',
        payer_address='43 rue des kangourous\n99999 Kangourou Ville',
        cancelled_at=now(),
        cancelled_by=admin_user,
        cancellation_reason=CreditCancellationReason.objects.create(label='Final pool deletion'),
        cancellation_description='foo bar\nblah',
        origin='api',
    )
    credit4.set_number()
    credit4.save()
    credit5 = Credit.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        regie=regie,
        payer_external_id='payer:3',
        payer_first_name='First3',
        payer_last_name='Name3',
        payer_address='43 rue des kangourous\n99999 Kangourou Ville',
        origin='api',
    )
    credit5.set_number()
    credit5.save()  # zero amount invoice, no line

    credit_line11 = CreditLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        credit=credit1,
        quantity=1.2,
        unit_amount=1,
        label='Event A',
        description='A description',
        accounting_code='424242',
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
        event_slug='agenda-a@event-a',
        agenda_slug='agenda-a',
        activity_label='Agenda A',
        form_url='http://form.com',
    )
    credit_line12 = CreditLine.objects.create(
        event_date=datetime.date(2022, 9, 2),
        credit=credit1,
        quantity=1,
        unit_amount=2,
        label='Event B',
        accounting_code='424243',
        user_external_id='user:2',
        user_first_name='User2',
        user_last_name='Name2',
        event_slug='agenda-b@event-b',
        agenda_slug='agenda-b',
        activity_label='Agenda B',
    )
    credit_line13 = CreditLine.objects.create(
        event_date=datetime.date(2022, 9, 3),
        credit=credit1,
        quantity=1,
        unit_amount=3,
        label='Event A',
        accounting_code='424242',
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
        event_slug='agenda-a@event-a',
        agenda_slug='agenda-a',
        activity_label='Agenda A',
    )
    payment1 = Payment.objects.create(
        regie=regie,
        amount=1,
        payment_type=PaymentType.objects.get(regie=regie, slug='credit'),
    )
    payment1.set_number()
    payment1.save()
    credit_assignment1 = CreditAssignment.objects.create(
        invoice=invoice,
        payment=payment1,
        credit=credit1,
        amount=1,
    )
    refund = Refund.objects.create(
        regie=regie,
        amount=5.2,
    )
    refund.set_number()
    refund.save()
    credit_assignment2 = CreditAssignment.objects.create(
        refund=refund,
        credit=credit1,
        amount=5.2,
    )
    credit1.refresh_from_db()
    assert credit1.remaining_amount == 0
    assert credit1.assigned_amount == decimal.Decimal('6.2')

    credit_line21 = CreditLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        credit=credit2,
        quantity=1,
        unit_amount=1,
        label='Event AA',
        accounting_code='424242',
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
        event_slug='agenda-a@event-aa',
        agenda_slug='agenda-a',
        activity_label='Agenda A',
    )
    CreditAssignment.objects.create(
        invoice=invoice,
        credit=credit2,
        amount=0.5,
    )
    credit2.refresh_from_db()
    assert credit2.remaining_amount == 0.5
    assert credit2.assigned_amount == 0.5

    CreditLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        credit=credit3,
        quantity=1,
        unit_amount=1,
        label='Event A',
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
    )
    credit3.refresh_from_db()
    assert credit3.remaining_amount == 1
    assert credit3.assigned_amount == 0

    CreditLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        credit=credit4,
        quantity=1,
        unit_amount=1,
        label='Event A',
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
    )
    credit4.refresh_from_db()
    assert credit4.remaining_amount == 1
    assert credit4.assigned_amount == 0

    app = login(app)
    resp = app.get('/manage/invoicing/regie/%s/credits/' % regie.pk)
    assert resp.pyquery(
        'tr[data-invoicing-element-id="%s"]' % credit1.pk
    ).text() == 'Assigned Credit A%02d-%s-0000001 dated %s (created on %s) for First1 Name1, amount 6.20€ - download' % (
        regie.pk,
        credit1.date_invoicing.strftime('%y-%m'),
        credit1.date_invoicing.strftime('%d/%m/%Y'),
        credit1.created_at.strftime('%d/%m/%Y'),
    )
    assert len(resp.pyquery('tr[data-invoicing-element-id="%s"] a' % credit1.pk)) == 2
    lines_url = resp.pyquery('tr[data-invoicing-element-id="%s"]' % credit1.pk).attr(
        'data-invoicing-element-lines-url'
    )
    assert lines_url == '/manage/invoicing/ajax/regie/%s/credit/%s/lines/' % (
        regie.pk,
        credit1.pk,
    )
    lines_resp = app.get(lines_url)
    assert len(lines_resp.pyquery('tr')) == 17
    assert [PyQuery(tr).text() for tr in lines_resp.pyquery('tr')] == [
        'Credit from 01/09/2022 - Initial invoice number: F%02d-%s-0000001'
        % (
            regie.pk,
            previous_invoice.created_at.strftime('%y-%m'),
        ),
        'Publication date: 01/10/2022',
    ] + (['Origin: Campaign'] if not orphan else ['Origin: API']) + [
        'User1 Name1',
        'Description\nAccounting code\nAmount\nQuantity\nSubtotal',
        'Agenda A',
        'Event A\nA description\n424242\n1.00€\n1.2\n1.20€',
        'Event A\n424242\n3.00€\n1\n3.00€',
        'User2 Name2',
        'Description\nAccounting code\nAmount\nQuantity\nSubtotal',
        'Agenda B',
        'Event B\n424243\n2.00€\n1\n2.00€',
        'Assignments',
        'Payment\nDate\nAmount',
        'R%02d-%s-0000001\n%s\n1.00€'
        % (
            regie.pk,
            payment1.created_at.strftime('%y-%m'),
            date_format(localtime(credit_assignment1.created_at), 'DATETIME_FORMAT'),
        ),
        'V%02d-%s-0000001 (Refund)\n%s\n5.20€'
        % (
            regie.pk,
            refund.created_at.strftime('%y-%m'),
            date_format(localtime(credit_assignment2.created_at), 'DATETIME_FORMAT'),
        ),
        'Assigned amount: 6.20€',
    ]
    part = ['http://form.com']
    if not orphan:
        part = [
            '/manage/invoicing/regie/%s/campaign/%s/pool/%s/?credits&number=%s'
            % (regie.pk, campaign1.pk, pool1.pk, credit1.formatted_number),
            '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/?user_external_id=user:1'
            % (regie.pk, campaign1.pk, pool1.pk),
            '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/?credit_line=%s'
            % (regie.pk, campaign1.pk, pool1.pk, credit_line11.pk),
            '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/?credit_line=%s'
            % (regie.pk, campaign1.pk, pool1.pk, credit_line13.pk),
            '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/?user_external_id=user:2'
            % (regie.pk, campaign1.pk, pool1.pk),
            '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/?credit_line=%s'
            % (regie.pk, campaign1.pk, pool1.pk, credit_line12.pk),
        ]
    assert [PyQuery(a).attr('href') for a in lines_resp.pyquery('tr a')] == part + [
        '/manage/invoicing/regie/%s/payments/?number=R%02d-%s-0000001'
        % (
            regie.pk,
            regie.pk,
            payment1.created_at.strftime('%y-%m'),
        ),
        '/manage/invoicing/regie/%s/refunds/?number=V%02d-%s-0000001'
        % (
            regie.pk,
            regie.pk,
            refund.created_at.strftime('%y-%m'),
        ),
    ]
    assert len(resp.pyquery('tr[data-invoicing-element-id="%s"] a' % credit2.pk)) == 2
    assert resp.pyquery(
        'tr[data-invoicing-element-id="%s"]' % credit2.pk
    ).text() == 'Partially assigned Credit A%02d-%s-0000002 dated %s for First2 Name2, amount 1.00€ - download' % (
        regie.pk,
        credit2.created_at.strftime('%y-%m'),
        credit2.created_at.strftime('%d/%m/%Y'),
    )
    lines_url = resp.pyquery('tr[data-invoicing-element-id="%s"]' % credit2.pk).attr(
        'data-invoicing-element-lines-url'
    )
    assert lines_url == '/manage/invoicing/ajax/regie/%s/credit/%s/lines/' % (
        regie.pk,
        credit2.pk,
    )
    lines_resp = app.get(lines_url)
    assert len(lines_resp.pyquery('tr')) == 11
    assert [PyQuery(tr).text() for tr in lines_resp.pyquery('tr')] == [
        'Publication date: 01/10/2022',
    ] + (['Origin: Campaign'] if not orphan else ['Origin: API']) + [
        'User1 Name1',
        'Description\nAccounting code\nAmount\nQuantity\nSubtotal',
        'Agenda A',
        'Event AA\n424242\n1.00€\n1\n1.00€',
        'Assignments',
        'Payment\nDate\nAmount',
        'Pending...\n0.50€',
        'Assigned amount: 0.50€',
        'Remaining amount to assign: 0.50€',
    ]
    part = []
    if not orphan:
        part = [
            '/manage/invoicing/regie/%s/campaign/%s/pool/%s/?credits&number=%s'
            % (regie.pk, campaign2.pk, pool2.pk, credit2.formatted_number),
            '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/?user_external_id=user:1'
            % (regie.pk, campaign2.pk, pool2.pk),
            '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/?credit_line=%s'
            % (regie.pk, campaign2.pk, pool2.pk, credit_line21.pk),
        ]
    assert [PyQuery(a).attr('href') for a in lines_resp.pyquery('tr a')] == part

    assert resp.pyquery(
        'tr[data-invoicing-element-id="%s"]' % credit3.pk
    ).text() == 'Credit A%02d-%s-0000003 dated %s for First3 Name3, amount 1.00€ - download' % (
        regie.pk,
        credit3.created_at.strftime('%y-%m'),
        credit3.created_at.strftime('%d/%m/%Y'),
    )
    assert len(resp.pyquery('tr[data-invoicing-element-id="%s"] a' % credit3.pk)) == 2
    lines_url = resp.pyquery('tr[data-invoicing-element-id="%s"]' % credit3.pk).attr(
        'data-invoicing-element-lines-url'
    )
    assert lines_url == '/manage/invoicing/ajax/regie/%s/credit/%s/lines/' % (
        regie.pk,
        credit3.pk,
    )
    lines_resp = app.get(lines_url)
    assert len(lines_resp.pyquery('tr')) == 10
    assert [PyQuery(tr).text() for tr in lines_resp.pyquery('tr')] == [
        'Publication date: 01/10/2022',
    ] + (['Origin: Campaign'] if not orphan else ['Origin: API']) + [
        'User1 Name1',
        'Description\nAccounting code\nAmount\nQuantity\nSubtotal',
        'Event A\n1.00€\n1\n1.00€',
        'Assignments',
        'Payment\nDate\nAmount',
        'No assignments for this credit',
        'Remaining amount to assign: 1.00€',
        'Cancel credit',
    ]

    assert resp.pyquery(
        'tr[data-invoicing-element-id="%s"]' % credit4.pk
    ).text() == 'Cancelled Credit A%02d-%s-0000004 dated %s for First3 Name3, amount 1.00€ - download' % (
        regie.pk,
        credit4.created_at.strftime('%y-%m'),
        credit4.created_at.strftime('%d/%m/%Y'),
    )
    assert len(resp.pyquery('tr[data-invoicing-element-id="%s"] a' % credit4.pk)) == 2
    lines_url = resp.pyquery('tr[data-invoicing-element-id="%s"]' % credit4.pk).attr(
        'data-invoicing-element-lines-url'
    )
    assert lines_url == '/manage/invoicing/ajax/regie/%s/credit/%s/lines/' % (
        regie.pk,
        credit4.pk,
    )
    lines_resp = app.get(lines_url)
    assert len(lines_resp.pyquery('tr')) == 9
    assert [PyQuery(tr).text() for tr in lines_resp.pyquery('tr')] == [
        'Publication date: 01/10/2022',
        'Origin: API',
        'User1 Name1',
        'Description\nAccounting code\nAmount\nQuantity\nSubtotal',
        'Event A\n1.00€\n1\n1.00€',
        'Cancelled on: %s' % localtime(credit4.cancelled_at).strftime('%d/%m/%Y %H:%M'),
        'Cancelled by: admin',
        'Reason: Final pool deletion',
        'Description: foo bar\nblah',
    ]

    assert resp.pyquery(
        'tr[data-invoicing-element-id="%s"]' % credit5.pk
    ).text() == 'Credit A%02d-%s-0000005 dated %s for First3 Name3, amount 0.00€ - download' % (
        regie.pk,
        credit5.created_at.strftime('%y-%m'),
        credit5.created_at.strftime('%d/%m/%Y'),
    )
    assert len(resp.pyquery('tr[data-invoicing-element-id="%s"] a' % credit5.pk)) == 2
    lines_url = resp.pyquery('tr[data-invoicing-element-id="%s"]' % credit5.pk).attr(
        'data-invoicing-element-lines-url'
    )
    assert lines_url == '/manage/invoicing/ajax/regie/%s/credit/%s/lines/' % (
        regie.pk,
        credit5.pk,
    )
    lines_resp = app.get(lines_url)
    assert len(lines_resp.pyquery('tr')) == 5
    assert [PyQuery(tr).text() for tr in lines_resp.pyquery('tr')] == [
        'Publication date: 01/10/2022',
        'Origin: API',
        'Assignments',
        'Payment\nDate\nAmount',
        'No assignments for this credit',
    ]

    # test filters
    params = [
        ({'number': credit1.formatted_number}, 1),
        ({'number': credit1.created_at.strftime('%y-%m')}, 5),
        ({'origin': 'api'}, 5 if orphan else 2),
        ({'created_at_after': today.strftime('%Y-%m-%d')}, 5),
        ({'created_at_after': tomorrow.strftime('%Y-%m-%d')}, 0),
        ({'created_at_before': yesterday.strftime('%Y-%m-%d')}, 0),
        ({'created_at_before': today.strftime('%Y-%m-%d')}, 5),
        ({'payment_number': payment1.formatted_number}, 1),
        ({'payment_number': payment1.created_at.strftime('%y-%m')}, 1),
        ({'payer_external_id': 'payer:1'}, 1),
        ({'payer_external_id': 'payer:2'}, 1),
        ({'payer_first_name': 'first'}, 5),
        ({'payer_first_name': 'first1'}, 1),
        ({'payer_last_name': 'name'}, 5),
        ({'payer_last_name': 'name1'}, 1),
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
        ({'assigned': 'yes'}, 1),
        ({'assigned': 'partially'}, 1),
        ({'assigned': 'no'}, 3),
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
    ]
    for param, result in params:
        resp = app.get(
            '/manage/invoicing/regie/%s/credits/' % regie.pk,
            params=param,
        )
        assert len(resp.pyquery('tr.credit')) == result


def test_regie_credit_pdf(app, admin_user):
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
    credit = Credit.objects.create(
        label='Credit from 01/09/2022',
        date_publication=datetime.date(2022, 10, 1),
        date_invoicing=datetime.date(2022, 9, 1),
        regie=regie,
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Name1',
        payer_address='41 rue des kangourous\n99999 Kangourou Ville',
        previous_invoice=previous_invoice,
    )
    credit.set_number()
    credit.save()

    CreditLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        credit=credit,
        quantity=1.2,
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
    CreditLine.objects.create(
        event_date=datetime.date(2022, 9, 2),
        credit=credit,
        quantity=1,
        unit_amount=2,
        label='Label 12',
        user_external_id='user:2',
        user_first_name='User2',
        user_last_name='Name2',
        details={
            'agenda': 'agenda-a',
            'primary_event': 'event-a',
            'status': 'presence',
            'partial_bookings': True,
        },
        event_slug='agenda-a@event-a',
        agenda_slug='agenda-a',
        activity_label='Agenda A',
        description='a description',
    )
    CreditLine.objects.create(
        event_date=datetime.date(2022, 9, 3),
        credit=credit,
        quantity=1,
        unit_amount=3,
        label='Label 13',
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
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
    credit.refresh_from_db()

    app = login(app)
    resp = app.get('/manage/invoicing/regie/%s/credit/%s/pdf/?html' % (regie.pk, credit.pk))
    assert 'color: #9141ac;' in resp
    assert resp.pyquery('#document-label').text() == 'Credit from 01/09/2022'
    assert resp.pyquery('#regie-label').text() == 'Foo'
    assert resp.pyquery('address#to').text() == 'First1 Name1\n41 rue des kangourous\n99999 Kangourou Ville'
    assert resp.pyquery('dl#informations').text() == (
        'Credit number:\nA%02d-22-09-0000001\nInitial invoice number:\nF%02d-%s-0000001\nDate:\n%s'
        % (
            regie.pk,
            regie.pk,
            previous_invoice.created_at.strftime('%y-%m'),
            date_format(credit.date_invoicing, 'DATE_FORMAT'),
        )
    )
    assert [PyQuery(tr).text() for tr in resp.pyquery('table#lines tr')] == [
        '',
        'User1 Name1',
        'Services\nDetails\nUnit amount\nQuantity\nTotal amount',
        'Agenda A',
        'Label 11\nFoo! Thu01, Fri02, Sat03\n1.00€\n1.2\n1.20€',
        'Agenda B',
        'Label 13\na description\n3.00€\n1\n3.00€',
        'Subtotal:\n4.20€',
        '',
        'User2 Name2',
        'Services\nDetails\nUnit amount\nQuantity\nTotal amount',
        'Agenda A',
        'Label 12\na description\n2.00€\n1\n2.00€',
        'Subtotal:\n2.00€',
        'Total amount:\n6.20€',
    ]

    credit.date_invoicing = None
    credit.save()
    regie.invoice_model = 'basic'
    regie.save()
    resp = app.get('/manage/invoicing/regie/%s/credit/%s/pdf/?html' % (regie.pk, credit.pk))
    assert resp.pyquery('dl#informations').text() == (
        'Credit number:\nA%02d-22-09-0000001\nInitial invoice number:\nF%02d-%s-0000001\nDate:\n%s'
        % (
            regie.pk,
            regie.pk,
            previous_invoice.created_at.strftime('%y-%m'),
            date_format(localtime(credit.created_at), 'DATE_FORMAT'),
        )
    )
    assert [PyQuery(tr).text() for tr in resp.pyquery('table#lines tr')] == [
        '',
        'User1 Name1',
        'Services\nDetails\nUnit amount\nQuantity\nTotal amount',
        'Agenda A',
        'Label 11\nFoo!\n1.00€\n1.2\n1.20€',
        'Agenda B',
        'Label 13\n3.00€\n1\n3.00€',
        'Subtotal:\n4.20€',
        '',
        'User2 Name2',
        'Services\nDetails\nUnit amount\nQuantity\nTotal amount',
        'Agenda A',
        'Label 12\n2.00€\n1\n2.00€',
        'Subtotal:\n2.00€',
        'Total amount:\n6.20€',
    ]
    assert len(resp.pyquery('table#lines-details')) == 0

    regie.invoice_model = 'full'
    regie.save()
    resp = app.get('/manage/invoicing/regie/%s/credit/%s/pdf/?html' % (regie.pk, credit.pk))
    assert [PyQuery(tr).text() for tr in resp.pyquery('table#lines tr')] == [
        '',
        'User1 Name1',
        'Services\nDetails\nUnit amount\nQuantity\nTotal amount',
        'Agenda A',
        'Label 11\nFoo!\n1.00€\n1.2\n1.20€',
        'Agenda B',
        'Label 13\n3.00€\n1\n3.00€',
        'Subtotal:\n4.20€',
        '',
        'User2 Name2',
        'Services\nDetails\nUnit amount\nQuantity\nTotal amount',
        'Agenda A',
        'Label 12\n2.00€\n1\n2.00€',
        'Subtotal:\n2.00€',
        'Total amount:\n6.20€',
    ]
    assert len(resp.pyquery('table#lines-details')) == 1
    assert [PyQuery(tr).text() for tr in resp.pyquery('table#lines-details tr')] == [
        '',
        'User1 Name1',
        'Services\nDetails',
        'Agenda A',
        'Label 11\nFoo! Thu01, Fri02, Sat03',
        'Agenda B',
        'Label 13\na description',
        '',
        'User2 Name2',
        'Services\nDetails',
        'Agenda A',
        'Label 12\na description',
    ]

    app.get('/manage/invoicing/regie/%s/credit/%s/pdf/?html' % (0, credit.pk), status=404)
    app.get('/manage/invoicing/regie/%s/credit/%s/pdf/?html' % (regie.pk, 0), status=404)
    other_regie = Regie.objects.create(label='Foo')
    app.get('/manage/invoicing/regie/%s/credit/%s/pdf/?html' % (other_regie.pk, credit.pk), status=404)

    appearance_settings = AppearanceSettings.singleton()
    appearance_settings.address = '<p>Foo bar<br>Streetname</p>'
    appearance_settings.extra_info = '<p>Opening hours...</p>'
    appearance_settings.save()
    resp = app.get('/manage/invoicing/regie/%s/credit/%s/pdf/?html' % (regie.pk, credit.pk))
    assert appearance_settings.address in resp.text
    assert appearance_settings.extra_info in resp.text


def test_regie_credit_cancel(app, admin_user):
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
    credit1 = Credit.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        regie=regie,
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Name1',
        payer_address='41 rue des kangourous\n99999 Kangourou Ville',
        pool=finalized_pool,
    )
    credit1.set_number()
    credit1.save()
    credit2 = Credit.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        regie=regie,
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Name1',
        payer_address='41 rue des kangourous\n99999 Kangourou Ville',
    )
    credit2.set_number()
    credit2.save()

    CreditLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        credit=credit1,
        quantity=1,
        unit_amount=40,
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
    )
    CreditLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        credit=credit2,
        quantity=1,
        unit_amount=50,
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
    )

    payment = Payment.objects.create(
        regie=regie,
        amount=1,
        payment_type=PaymentType.objects.get(regie=regie, slug='credit'),
    )
    payment.set_number()
    payment.save()
    invoice = Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        regie=regie,
    )
    CreditAssignment.objects.create(
        invoice=invoice,
        payment=payment,
        credit=credit2,
        amount=1,
    )
    credit2.refresh_from_db()
    assert credit2.remaining_amount == 49
    assert credit2.assigned_amount == 1

    cancellation_reason = CreditCancellationReason.objects.create(label='Mistake')
    CreditCancellationReason.objects.create(label='Disabled', disabled=True)

    app = login(app)
    resp = app.get('/manage/invoicing/ajax/regie/%s/credit/%s/lines/' % (regie.pk, credit1.pk))
    resp = resp.click(href='/manage/invoicing/regie/%s/credit/%s/cancel/' % (regie.pk, credit1.pk))
    assert resp.form['cancellation_reason'].options == [
        ('', True, '---------'),
        (str(cancellation_reason.pk), False, 'Mistake'),
    ]
    resp.form['cancellation_reason'] = cancellation_reason.pk
    resp.form['cancellation_description'] = 'foo bar blah'
    resp = resp.form.submit()
    assert resp.location.endswith(
        '/manage/invoicing/regie/%s/credits/?number=%s' % (regie.pk, credit1.formatted_number)
    )
    credit1.refresh_from_db()
    assert credit1.cancelled_at is not None
    assert credit1.cancelled_by == admin_user
    assert credit1.cancellation_reason == cancellation_reason
    assert credit1.cancellation_description == 'foo bar blah'
    assert credit1.lines.count() == 1
    credit2.refresh_from_db()
    assert credit2.cancelled_at is None

    # already cancelled
    app.get('/manage/invoicing/regie/%s/credit/%s/cancel/' % (regie.pk, credit1.pk), status=404)
    credit1.cancelled_at = None
    credit1.save()

    # other regie
    other_regie = Regie.objects.create(label='Foo')
    app.get('/manage/invoicing/regie/%s/credit/%s/cancel/' % (other_regie.pk, credit1.pk), status=404)

    # credit with assignment
    app.get('/manage/invoicing/regie/%s/credit/%s/cancel/' % (regie.pk, credit2.pk), status=404)

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
    credit1.pool = non_finalized_pool
    credit1.save()
    app.get('/manage/invoicing/regie/%s/credit/%s/cancel/' % (regie.pk, credit1.pk), status=404)


def test_regie_refunds(app, admin_user):
    regie = Regie.objects.create(label='Foo')
    Agenda.objects.create(label='Agenda A', regie=regie)
    Agenda.objects.create(label='Agenda B', regie=regie)
    PaymentType.create_defaults(regie)
    invoice = Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        regie=regie,
    )
    credit1 = Credit.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        regie=regie,
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Name1',
        payer_address='41 rue des kangourous\n99999 Kangourou Ville',
    )
    credit1.set_number()
    credit1.save()
    credit2 = Credit.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        regie=regie,
        payer_external_id='payer:2',
        payer_first_name='First2',
        payer_last_name='Name2',
        payer_address='42 rue des kangourous\n99999 Kangourou Ville',
    )
    credit2.set_number()
    credit2.save()
    credit3 = Credit.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        regie=regie,
        payer_external_id='payer:3',
        payer_first_name='First3',
        payer_last_name='Name3',
        payer_address='43 rue des kangourous\n99999 Kangourou Ville',
    )
    credit3.set_number()
    credit3.save()

    CreditLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        credit=credit1,
        quantity=1.2,
        unit_amount=1,
        label='Event A',
        description='A description',
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
    )
    CreditLine.objects.create(
        event_date=datetime.date(2022, 9, 2),
        credit=credit1,
        quantity=1,
        unit_amount=2,
        label='Event B',
        user_external_id='user:2',
        user_first_name='User2',
        user_last_name='Name2',
    )
    CreditLine.objects.create(
        event_date=datetime.date(2022, 9, 3),
        credit=credit1,
        quantity=1,
        unit_amount=3,
        label='Event A',
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
    )
    refund1 = Refund.objects.create(
        regie=regie,
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Name1',
        payer_address='41 rue des kangourous\n99999 Kangourou Ville',
        amount=6.2,
        date_refund=datetime.date(2022, 11, 1),
    )
    refund1.set_number()
    refund1.save()
    CreditAssignment.objects.create(
        refund=refund1,
        credit=credit1,
        amount=6.2,
    )
    credit1.refresh_from_db()
    assert credit1.remaining_amount == 0
    assert credit1.assigned_amount == decimal.Decimal('6.2')

    CreditLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        credit=credit2,
        quantity=1,
        unit_amount=1,
        label='Event AA',
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
    )
    payment2 = Payment.objects.create(
        regie=regie,
        amount=0.5,
        payment_type=PaymentType.objects.get(regie=regie, slug='credit'),
    )
    payment2.set_number()
    payment2.save()
    CreditAssignment.objects.create(
        invoice=invoice,
        payment=payment2,
        credit=credit2,
        amount=0.5,
    )
    refund2 = Refund.objects.create(
        regie=regie,
        payer_external_id='payer:2',
        payer_first_name='First2',
        payer_last_name='Name2',
        payer_address='42 rue des kangourous\n99999 Kangourou Ville',
        amount=0.5,
    )
    refund2.set_number()
    refund2.save()
    CreditAssignment.objects.create(
        refund=refund2,
        credit=credit2,
        amount=0.5,
    )
    credit2.refresh_from_db()
    assert credit2.remaining_amount == 0
    assert credit2.assigned_amount == 1

    CreditLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        credit=credit3,
        quantity=1,
        unit_amount=1,
        label='Event A',
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
    )
    refund3 = Refund.objects.create(
        regie=regie,
        payer_external_id='payer:3',
        payer_first_name='First3',
        payer_last_name='Name3',
        payer_address='43 rue des kangourous\n99999 Kangourou Ville',
        amount=1,
    )
    refund3.set_number()
    refund3.save()
    CreditAssignment.objects.create(
        refund=refund3,
        credit=credit3,
        amount=1,
    )
    credit3.refresh_from_db()
    assert credit3.remaining_amount == 0
    assert credit3.assigned_amount == 1

    app = login(app)
    resp = app.get('/manage/invoicing/regie/%s/refunds/' % regie.pk)
    assert resp.pyquery(
        'tr[data-invoicing-element-id="%s"]' % refund1.pk
    ).text() == 'Refund V%02d-%s-0000001 dated %s (created on %s) for First1 Name1, amount 6.20€' % (
        regie.pk,
        refund1.date_refund.strftime('%y-%m'),
        refund1.date_refund.strftime('%d/%m/%Y'),
        refund1.created_at.strftime('%d/%m/%Y'),
    )
    assert [
        PyQuery(tr).text() for tr in resp.pyquery('tr[data-related-invoicing-element-id="%s"]' % refund1.pk)
    ] == [
        'Credit\nDate\nCredit amount\nRefund amount',
        'A%02d-%s-0000001\n%s\n6.20€\n6.20€'
        % (
            regie.pk,
            credit1.created_at.strftime('%y-%m'),
            credit1.created_at.strftime('%d/%m/%Y'),
        ),
    ]

    assert resp.pyquery(
        'tr[data-invoicing-element-id="%s"]' % refund2.pk
    ).text() == 'Refund V%02d-%s-0000001 dated %s for First2 Name2, amount 0.50€' % (
        regie.pk,
        refund2.created_at.strftime('%y-%m'),
        refund2.created_at.strftime('%d/%m/%Y'),
    )
    assert [
        PyQuery(tr).text() for tr in resp.pyquery('tr[data-related-invoicing-element-id="%s"]' % refund2.pk)
    ] == [
        'Credit\nDate\nCredit amount\nRefund amount',
        'A%02d-%s-0000002\n%s\n1.00€\n0.50€'
        % (
            regie.pk,
            credit2.created_at.strftime('%y-%m'),
            credit2.created_at.strftime('%d/%m/%Y'),
        ),
    ]

    assert resp.pyquery(
        'tr[data-invoicing-element-id="%s"]' % refund3.pk
    ).text() == 'Refund V%02d-%s-0000002 dated %s for First3 Name3, amount 1.00€' % (
        regie.pk,
        refund3.created_at.strftime('%y-%m'),
        refund3.created_at.strftime('%d/%m/%Y'),
    )
    assert [
        PyQuery(tr).text() for tr in resp.pyquery('tr[data-related-invoicing-element-id="%s"]' % refund3.pk)
    ] == [
        'Credit\nDate\nCredit amount\nRefund amount',
        'A%02d-%s-0000003\n%s\n1.00€\n1.00€'
        % (
            regie.pk,
            credit3.created_at.strftime('%y-%m'),
            credit3.created_at.strftime('%d/%m/%Y'),
        ),
    ]

    # test filters
    today = now().date()
    tomorrow = today + datetime.timedelta(days=1)
    yesterday = today - datetime.timedelta(days=1)
    params = [
        ({'number': refund1.formatted_number}, 1),
        ({'number': refund1.created_at.strftime('%y-%m')}, 2),
        ({'created_at_after': today.strftime('%Y-%m-%d')}, 3),
        ({'created_at_after': tomorrow.strftime('%Y-%m-%d')}, 0),
        ({'created_at_before': yesterday.strftime('%Y-%m-%d')}, 0),
        ({'created_at_before': today.strftime('%Y-%m-%d')}, 3),
        ({'credit_number': credit1.formatted_number}, 1),
        ({'credit_number': credit1.created_at.strftime('%y-%m')}, 3),
        ({'payer_external_id': 'payer:1'}, 1),
        ({'payer_external_id': 'payer:2'}, 1),
        ({'payer_first_name': 'first'}, 3),
        ({'payer_first_name': 'first1'}, 1),
        ({'payer_last_name': 'name'}, 3),
        ({'payer_last_name': 'name1'}, 1),
        (
            {
                'amount_min': '1',
                'amount_min_lookup': 'gt',
            },
            1,
        ),
        (
            {
                'amount_min': '1',
                'amount_min_lookup': 'gte',
            },
            2,
        ),
        (
            {
                'amount_max': '6.2',
                'amount_max_lookup': 'lt',
            },
            2,
        ),
        (
            {
                'amount_max': '6.2',
                'amount_max_lookup': 'lte',
            },
            3,
        ),
    ]
    for param, result in params:
        resp = app.get(
            '/manage/invoicing/regie/%s/refunds/' % regie.pk,
            params=param,
        )
        assert len(resp.pyquery('tr.refund')) == result
