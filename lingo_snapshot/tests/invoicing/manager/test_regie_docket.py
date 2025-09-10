import datetime
import decimal

import pytest
from django.utils.timezone import localtime, make_aware, now
from pyquery import PyQuery

from lingo.invoicing.models import Payment, PaymentDocket, PaymentType, Regie
from tests.utils import get_ods_rows, login

pytestmark = pytest.mark.django_db


def test_regie_payments_outside_dockets(app, admin_user):
    regie = Regie.objects.create(label='Foo')
    PaymentType.create_defaults(regie)
    docket = PaymentDocket.objects.create(regie=regie, date_end=now().date(), draft=True)

    payment1 = Payment.objects.create(
        regie=regie,
        amount=35,
        payment_type=PaymentType.objects.get(regie=regie, slug='cash'),
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Name1',
        date_payment=now() - datetime.timedelta(days=2),
    )
    payment1.set_number()
    payment1.save()

    payment2 = Payment.objects.create(
        regie=regie,
        amount=55,
        payment_type=PaymentType.objects.get(regie=regie, slug='check'),
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Name1',
    )
    payment2.created_at = now() - datetime.timedelta(days=1)
    payment2.set_number()
    payment2.save()

    payment3 = Payment.objects.create(
        regie=regie,
        amount=2,
        payment_type=PaymentType.objects.get(regie=regie, slug='creditcard'),
        payer_external_id='payer:3',
        payer_first_name='First3',
        payer_last_name='Name3',
    )
    payment3.created_at = now() - datetime.timedelta(days=1)
    payment3.set_number()
    payment3.save()

    payment4 = Payment.objects.create(
        regie=regie,
        amount=2,
        payment_type=PaymentType.objects.get(regie=regie, slug='check'),
        payer_external_id='payer:3',
        payer_first_name='First3',
        payer_last_name='Name3',
    )
    payment4.set_number()
    payment4.save()

    payment5 = Payment.objects.create(
        regie=regie,
        amount=2,
        payment_type=PaymentType.objects.get(regie=regie, slug='check'),
        payer_external_id='payer:3',
        payer_first_name='First3',
        payer_last_name='Name3',
        cancelled_at=now(),
    )
    payment5.set_number()
    payment5.save()

    payment6 = Payment.objects.create(
        regie=regie,
        amount=2,
        payment_type=PaymentType.objects.get(regie=regie, slug='check'),
        payer_external_id='payer:3',
        payer_first_name='First3',
        payer_last_name='Name3',
        docket=docket,
    )
    payment6.set_number()
    payment6.save()

    app = login(app)
    resp = app.get('/manage/invoicing/regie/%s/' % regie.pk)
    resp = resp.click('Dockets')
    resp = resp.click('Payments outside dockets')
    assert [PyQuery(tr).text() for tr in resp.pyquery('tr')] == [
        'Payment %s dated %s (created on %s) from First1 Name1, amount 35.00€ (Cash)'
        % (
            payment1.formatted_number,
            payment1.date_payment.strftime('%d/%m/%Y'),
            payment1.created_at.strftime('%d/%m/%Y'),
        ),
        'Payment %s dated %s from First3 Name3, amount 2.00€ (Credit card)'
        % (payment3.formatted_number, payment3.created_at.strftime('%d/%m/%Y')),
        'Payment %s dated %s from First1 Name1, amount 55.00€ (Check)'
        % (payment2.formatted_number, payment2.created_at.strftime('%d/%m/%Y')),
    ]

    resp.form['date_end'] = now().date() + datetime.timedelta(days=1)
    resp = resp.form.submit()
    assert [PyQuery(tr).text() for tr in resp.pyquery('tr')] == [
        'Payment %s dated %s from First3 Name3, amount 2.00€ (Check)'
        % (payment4.formatted_number, payment4.created_at.strftime('%d/%m/%Y')),
        'Payment %s dated %s (created on %s) from First1 Name1, amount 35.00€ (Cash)'
        % (
            payment1.formatted_number,
            payment1.date_payment.strftime('%d/%m/%Y'),
            payment1.created_at.strftime('%d/%m/%Y'),
        ),
        'Payment %s dated %s from First3 Name3, amount 2.00€ (Credit card)'
        % (payment3.formatted_number, payment3.created_at.strftime('%d/%m/%Y')),
        'Payment %s dated %s from First1 Name1, amount 55.00€ (Check)'
        % (payment2.formatted_number, payment2.created_at.strftime('%d/%m/%Y')),
    ]

    resp = app.get('/manage/invoicing/regie/%s/dockets/payments/' % regie.pk)
    resp.form['payment_type'] = [
        PaymentType.objects.get(slug='cash').pk,
        PaymentType.objects.get(slug='check').pk,
    ]
    resp.form['date_end'] = now().date() + datetime.timedelta(days=1)
    resp = resp.form.submit()
    assert [PyQuery(tr).text() for tr in resp.pyquery('tr')] == [
        'Payment %s dated %s from First3 Name3, amount 2.00€ (Check)'
        % (payment4.formatted_number, payment4.created_at.strftime('%d/%m/%Y')),
        'Payment %s dated %s (created on %s) from First1 Name1, amount 35.00€ (Cash)'
        % (
            payment1.formatted_number,
            payment1.date_payment.strftime('%d/%m/%Y'),
            payment1.created_at.strftime('%d/%m/%Y'),
        ),
        'Payment %s dated %s from First1 Name1, amount 55.00€ (Check)'
        % (payment2.formatted_number, payment2.created_at.strftime('%d/%m/%Y')),
    ]


def test_regie_docket_list(app, admin_user):
    regie = Regie.objects.create(label='Foo')
    PaymentType.create_defaults(regie)
    docket1 = PaymentDocket.objects.create(
        regie=regie, date_end=now().date() + datetime.timedelta(days=1), draft=False
    )
    docket1.payment_types.set(PaymentType.objects.filter(slug__in=['cash', 'check']))
    docket1.set_number()
    docket1.save()
    docket2 = PaymentDocket.objects.create(
        regie=regie, date_end=now().date() + datetime.timedelta(days=2), draft=False
    )
    docket2.payment_types.set(PaymentType.objects.filter(slug__in=['check']))
    docket2.set_number()
    docket2.save()
    docket3 = PaymentDocket.objects.create(
        regie=regie, date_end=now().date() + datetime.timedelta(days=3), draft=True
    )
    docket3.payment_types.set(PaymentType.objects.filter(slug__in=['check', 'creditcard']))

    payment1 = Payment.objects.create(
        regie=regie,
        amount=decimal.Decimal('35.5'),
        payment_type=PaymentType.objects.get(regie=regie, slug='cash'),
        docket=docket1,
    )
    payment1.set_number()
    payment1.save()
    payment2 = Payment.objects.create(
        regie=regie,
        amount=42,
        payment_type=PaymentType.objects.get(regie=regie, slug='check'),
        docket=docket1,
    )
    payment2.set_number()
    payment2.save()
    payment3 = Payment.objects.create(
        regie=regie,
        amount=42,
        payment_type=PaymentType.objects.get(regie=regie, slug='check'),
        docket=docket2,
    )
    payment3.set_number()
    payment3.save()
    payment4 = Payment.objects.create(
        regie=regie,
        amount=43,
        payment_type=PaymentType.objects.get(regie=regie, slug='check'),
        docket=docket3,
    )
    payment4.set_number()
    payment4.save()
    payment5 = Payment.objects.create(
        regie=regie,
        amount=decimal.Decimal('44.5'),
        payment_type=PaymentType.objects.get(regie=regie, slug='creditcard'),
        docket=docket3,
        cancelled_at=now(),
    )
    payment5.set_number()
    payment5.save()

    app = login(app)
    resp = app.get('/manage/invoicing/regie/%s/' % regie.pk)
    resp = resp.click('Dockets')
    assert [PyQuery(tr).text() for tr in resp.pyquery('tr')] == [
        'Number\nPayment types\nNumber of payments\nStop date',
        'TEMPORARY-%s\nCheck, Credit card\n1 (43.00€) 1 (44.50€)\n%s'
        % (docket3.pk, docket3.date_end.strftime('%d/%m/%Y')),
        'B%02d-%s-0000002\nCheck\n1 (42.00€)\n%s'
        % (regie.pk, docket2.created_at.strftime('%y-%m'), docket2.date_end.strftime('%d/%m/%Y')),
        'B%02d-%s-0000001\nCash, Check\n2 (77.50€)\n%s'
        % (regie.pk, docket1.created_at.strftime('%y-%m'), docket1.date_end.strftime('%d/%m/%Y')),
    ]

    # ods synthesis
    after = now().date() + datetime.timedelta(days=1)
    before = now().date() + datetime.timedelta(days=3)
    resp = app.get('/manage/invoicing/regie/%s/dockets/?ods' % regie.pk)
    assert resp.headers['Content-Type'] == 'application/vnd.oasis.opendocument.spreadsheet'
    assert resp.headers['Content-Disposition'] == 'attachment; filename="dockets.ods"'
    resp = app.get(
        '/manage/invoicing/regie/%s/dockets/?ods&date_end_after=%s' % (regie.pk, after.strftime('%Y-%m-%d'))
    )
    assert resp.headers['Content-Disposition'] == 'attachment; filename="dockets-%s.ods"' % after.strftime(
        '%Y%m%d'
    )
    resp = app.get(
        '/manage/invoicing/regie/%s/dockets/?ods&date_end_before=%s' % (regie.pk, before.strftime('%Y-%m-%d'))
    )
    assert resp.headers['Content-Disposition'] == 'attachment; filename="dockets-%s.ods"' % before.strftime(
        '%Y%m%d'
    )
    resp = app.get(
        '/manage/invoicing/regie/%s/dockets/?ods&date_end_after=%s&date_end_before=%s'
        % (regie.pk, after, before)
    )
    assert resp.headers['Content-Disposition'] == 'attachment; filename="dockets-%s-%s.ods"' % (
        after.strftime('%Y%m%d'),
        before.strftime('%Y%m%d'),
    )
    rows = list(get_ods_rows(resp))
    assert len(rows) == 16
    assert rows == [
        ['Initial amount', 'Cancelled amount', 'Final amount'],
        ['207.00', '44.50', '162.50'],
        ['Number of payments', 'Total amount', 'Payment type'],
        ['1', '35.50', 'Cash'],
        [
            'Docket',
            'Number',
            'Date',
            'Payer ID',
            'Payer first name',
            'Payer last name',
            'Payment type',
            'Total amount',
            'Check issuer',
            'Check bank/organism',
            'Check number',
            'Bank transfer number',
            'Reference',
        ],
        [
            str(docket1),
            payment1.formatted_number,
            payment1.created_at.strftime('%m/%d/%Y'),
            None,
            None,
            None,
            'Cash',
            '35.50',
            None,
            None,
            None,
            None,
            None,
        ],
        ['Number of payments', 'Total amount', 'Payment type'],
        ['3', '127.00', 'Check'],
        [
            'Docket',
            'Number',
            'Date',
            'Payer ID',
            'Payer first name',
            'Payer last name',
            'Payment type',
            'Total amount',
            'Check issuer',
            'Check bank/organism',
            'Check number',
            'Bank transfer number',
            'Reference',
        ],
        [
            str(docket3),
            payment4.formatted_number,
            payment4.created_at.strftime('%m/%d/%Y'),
            None,
            None,
            None,
            'Check',
            '43.00',
            None,
            None,
            None,
            None,
            None,
        ],
        [
            str(docket2),
            payment3.formatted_number,
            payment3.created_at.strftime('%m/%d/%Y'),
            None,
            None,
            None,
            'Check',
            '42.00',
            None,
            None,
            None,
            None,
            None,
        ],
        [
            str(docket1),
            payment2.formatted_number,
            payment2.created_at.strftime('%m/%d/%Y'),
            None,
            None,
            None,
            'Check',
            '42.00',
            None,
            None,
            None,
            None,
            None,
        ],
        ['Number of payments', 'Total amount', 'Cancelled payments'],
        ['1', '44.50'],
        [
            'Docket',
            'Number',
            'Date',
            'Payer ID',
            'Payer first name',
            'Payer last name',
            'Payment type',
            'Total amount',
            'Check issuer',
            'Check bank/organism',
            'Check number',
            'Bank transfer number',
            'Reference',
            'Cancelled on',
            'Cancellation reason',
        ],
        [
            str(docket3),
            payment5.formatted_number,
            payment5.created_at.strftime('%m/%d/%Y'),
            None,
            None,
            None,
            'Credit card',
            '44.50',
            None,
            None,
            None,
            None,
            None,
            payment5.cancelled_at.strftime('%m/%d/%Y'),
            None,
        ],
    ]

    # pdf synthesis
    resp = app.get('/manage/invoicing/regie/%s/dockets/?pdf' % regie.pk)
    assert resp.headers['Content-Disposition'] == 'attachment; filename="dockets.pdf"'
    resp = app.get('/manage/invoicing/regie/%s/dockets/?pdf&html' % regie.pk)
    assert [PyQuery(h1).text() for h1 in resp.pyquery('h1')] == ['Foo - List of the payments of all dockets']
    resp = app.get(
        '/manage/invoicing/regie/%s/dockets/?pdf&date_end_after=%s' % (regie.pk, after.strftime('%Y-%m-%d'))
    )
    assert resp.headers['Content-Disposition'] == 'attachment; filename="dockets-%s.pdf"' % after.strftime(
        '%Y%m%d'
    )
    resp = app.get(
        '/manage/invoicing/regie/%s/dockets/?pdf&html&date_end_after=%s'
        % (regie.pk, after.strftime('%Y-%m-%d'))
    )
    assert [PyQuery(h1).text() for h1 in resp.pyquery('h1')] == [
        'Foo - List of the payments of dockets from %s' % after.strftime('%d/%m/%Y')
    ]
    resp = app.get(
        '/manage/invoicing/regie/%s/dockets/?pdf&date_end_before=%s' % (regie.pk, before.strftime('%Y-%m-%d'))
    )
    assert resp.headers['Content-Disposition'] == 'attachment; filename="dockets-%s.pdf"' % before.strftime(
        '%Y%m%d'
    )
    resp = app.get(
        '/manage/invoicing/regie/%s/dockets/?pdf&html&date_end_before=%s'
        % (regie.pk, before.strftime('%Y-%m-%d'))
    )
    assert [PyQuery(h1).text() for h1 in resp.pyquery('h1')] == [
        'Foo - List of the payments of dockets to %s' % before.strftime('%d/%m/%Y')
    ]
    resp = app.get(
        '/manage/invoicing/regie/%s/dockets/?pdf&date_end_after=%s&date_end_before=%s'
        % (regie.pk, after.strftime('%Y-%m-%d'), before.strftime('%Y-%m-%d'))
    )
    assert resp.headers['Content-Disposition'] == 'attachment; filename="dockets-%s-%s.pdf"' % (
        after.strftime('%Y%m%d'),
        before.strftime('%Y%m%d'),
    )
    resp = app.get(
        '/manage/invoicing/regie/%s/dockets/?pdf&html&date_end_after=%s&date_end_before=%s'
        % (regie.pk, after.strftime('%Y-%m-%d'), before.strftime('%Y-%m-%d'))
    )
    assert [PyQuery(h1).text() for h1 in resp.pyquery('h1')] == [
        'Foo - List of the payments of dockets from %s to %s'
        % (after.strftime('%d/%m/%Y'), before.strftime('%d/%m/%Y'))
    ]
    assert [PyQuery(h2).text() for h2 in resp.pyquery('h2')] == [
        'Cash',
        'Check',
        'Cancelled payments',
    ]
    assert [PyQuery(li).text() for li in resp.pyquery('li')] == [
        'Initial amount: 207.00€',
        'Cancelled amount: 44.50€',
        'Final amount: 162.50€',
    ]
    assert len(resp.pyquery('table')) == 3
    assert [PyQuery(tr).text() for tr in PyQuery(resp.pyquery('table')[0]).find('tr')] == [
        'Docket\nNumber\nDate\nPayer ID\nPayer first name\nPayer last name\nTotal amount\n'
        'Check issuer\nCheck bank/organism\nCheck number\nBank transfer number\nReference',
        '%s\n%s\n%s\n35.50€'
        % (str(docket1), payment1.formatted_number, payment1.created_at.strftime('%d/%m/%Y')),
    ]
    assert [PyQuery(tr).text() for tr in PyQuery(resp.pyquery('table')[1]).find('tr')] == [
        'Docket\nNumber\nDate\nPayer ID\nPayer first name\nPayer last name\nTotal amount\n'
        'Check issuer\nCheck bank/organism\nCheck number\nBank transfer number\nReference',
        '%s\n%s\n%s\n43.00€'
        % (str(docket3), payment4.formatted_number, payment4.created_at.strftime('%d/%m/%Y')),
        '%s\n%s\n%s\n42.00€'
        % (str(docket2), payment3.formatted_number, payment3.created_at.strftime('%d/%m/%Y')),
        '%s\n%s\n%s\n42.00€'
        % (str(docket1), payment2.formatted_number, payment2.created_at.strftime('%d/%m/%Y')),
    ]
    assert [PyQuery(tr).text() for tr in PyQuery(resp.pyquery('table')[2]).find('tr')] == [
        'Docket\nNumber\nDate\nPayer ID\nPayer first name\nPayer last name\nTotal amount\n'
        'Check issuer\nCheck bank/organism\nCheck number\nBank transfer number\nReference\n'
        'Cancelled on\nCancellation reason',
        '%s\n%s\n%s\n44.50€\n%s'
        % (
            str(docket3),
            payment5.formatted_number,
            payment5.created_at.strftime('%d/%m/%Y'),
            payment5.cancelled_at.strftime('%d/%m/%Y'),
        ),
    ]
    assert len(resp.pyquery('p')) == 3
    assert PyQuery(resp.pyquery('p')[0]).text() == 'Number of payments: 1\nTotal amount: 35.50€'
    assert PyQuery(resp.pyquery('p')[1]).text() == 'Number of payments: 3\nTotal amount: 127.00€'
    assert PyQuery(resp.pyquery('p')[2]).text() == 'Number of payments: 1\nTotal amount: 44.50€'

    # test filters
    params = [
        ({'number': docket1.formatted_number}, 1, 10),
        ({'number': docket1.created_at.strftime('%y-%m')}, 2, 11),
        ({'date_end_after': (now().date() + datetime.timedelta(days=1)).strftime('%Y-%m-%d')}, 3, 16),
        ({'date_end_after': (now().date() + datetime.timedelta(days=2)).strftime('%Y-%m-%d')}, 2, 11),
        ({'date_end_before': (now().date() + datetime.timedelta(days=2)).strftime('%Y-%m-%d')}, 2, 11),
        ({'date_end_before': (now().date() + datetime.timedelta(days=3)).strftime('%Y-%m-%d')}, 3, 16),
    ]
    for param, result, ods_result in params:
        resp = app.get(
            '/manage/invoicing/regie/%s/dockets/' % regie.pk,
            params=param,
        )
        assert len(resp.pyquery('tr.docket')) == result
        param['ods'] = True
        resp = app.get(
            '/manage/invoicing/regie/%s/dockets/' % regie.pk,
            params=param,
        )
        rows = list(get_ods_rows(resp))
        assert len(rows) == ods_result


def test_regie_docket_add(app, admin_user):
    regie = Regie.objects.create(label='Foo')
    PaymentType.create_defaults(regie)
    docket = PaymentDocket.objects.create(regie=regie, date_end=now().date(), draft=False)

    payment1 = Payment.objects.create(
        regie=regie,
        amount=35,
        payment_type=PaymentType.objects.get(regie=regie, slug='cash'),
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Name1',
    )
    payment1.created_at = now() - datetime.timedelta(days=2)
    payment1.set_number()
    payment1.save()

    payment2 = Payment.objects.create(
        regie=regie,
        amount=55,
        payment_type=PaymentType.objects.get(regie=regie, slug='check'),
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Name1',
    )
    payment2.created_at = now() - datetime.timedelta(days=1)
    payment2.set_number()
    payment2.save()

    payment3 = Payment.objects.create(
        regie=regie,
        amount=2,
        payment_type=PaymentType.objects.get(regie=regie, slug='creditcard'),
        payer_external_id='payer:3',
        payer_first_name='First3',
        payer_last_name='Name3',
    )
    payment3.created_at = now() - datetime.timedelta(days=1)
    payment3.set_number()
    payment3.save()

    payment4 = Payment.objects.create(
        regie=regie,
        amount=2,
        payment_type=PaymentType.objects.get(regie=regie, slug='check'),
        payer_external_id='payer:3',
        payer_first_name='First3',
        payer_last_name='Name3',
    )
    payment4.set_number()
    payment4.save()

    payment5 = Payment.objects.create(
        regie=regie,
        amount=2,
        payment_type=PaymentType.objects.get(regie=regie, slug='check'),
        payer_external_id='payer:3',
        payer_first_name='First3',
        payer_last_name='Name3',
        cancelled_at=now(),
    )
    payment5.set_number()
    payment5.save()

    payment6 = Payment.objects.create(
        regie=regie,
        amount=2,
        payment_type=PaymentType.objects.get(regie=regie, slug='check'),
        payer_external_id='payer:3',
        payer_first_name='First3',
        payer_last_name='Name3',
        docket=docket,
    )
    payment6.set_number()
    payment6.save()

    app = login(app)
    resp = app.get('/manage/invoicing/regie/%s/dockets/payments/' % regie.pk)
    resp = resp.click('New docket')
    docket = PaymentDocket.objects.latest('pk')
    assert resp.location.endswith('/manage/invoicing/regie/%s/docket/%s/' % (regie.pk, docket.pk))
    assert docket.regie == regie
    assert docket.draft is True
    assert docket.date_end == now().date()
    assert docket.formatted_number == ''
    assert list(docket.payment_types.all()) == list(PaymentType.objects.all())
    assert list(docket.payment_set.all().order_by('-pk')) == [payment3, payment2, payment1]

    resp = app.get('/manage/invoicing/regie/%s/dockets/payments/' % regie.pk)
    assert 'New docket' not in resp
    app.get('/manage/invoicing/regie/%s/docket/add/' % regie.pk, status=404)
    Payment.objects.filter(docket=docket).update(docket=None)
    docket.delete()

    resp = app.get('/manage/invoicing/regie/%s/dockets/payments/' % regie.pk)
    resp.form['payment_type'] = [p.pk for p in PaymentType.objects.all()]
    resp.form['date_end'] = now().date() + datetime.timedelta(days=1)
    resp = resp.form.submit()
    resp = resp.click('New docket')
    docket = PaymentDocket.objects.latest('pk')
    assert resp.location.endswith('/manage/invoicing/regie/%s/docket/%s/' % (regie.pk, docket.pk))
    assert docket.regie == regie
    assert docket.draft is True
    assert docket.date_end == now().date() + datetime.timedelta(days=1)
    assert list(docket.payment_types.all()) == list(PaymentType.objects.all())
    assert list(docket.payment_set.all().order_by('-pk')) == [payment4, payment3, payment2, payment1]

    resp = app.get('/manage/invoicing/regie/%s/dockets/payments/' % regie.pk)
    assert 'New docket' not in resp
    Payment.objects.filter(docket=docket).update(docket=None)
    docket.delete()

    resp = app.get('/manage/invoicing/regie/%s/dockets/payments/' % regie.pk)
    resp.form['payment_type'] = [
        PaymentType.objects.get(slug='cash').pk,
        PaymentType.objects.get(slug='check').pk,
    ]
    resp.form['date_end'] = now().date() + datetime.timedelta(days=1)
    resp = resp.form.submit()
    resp = resp.click('New docket')
    docket = PaymentDocket.objects.latest('pk')
    assert resp.location.endswith('/manage/invoicing/regie/%s/docket/%s/' % (regie.pk, docket.pk))
    assert docket.regie == regie
    assert docket.draft is True
    assert docket.date_end == now().date() + datetime.timedelta(days=1)
    assert list(docket.payment_types.all()) == list(PaymentType.objects.filter(slug__in=['cash', 'check']))
    assert list(docket.payment_set.all().order_by('-pk')) == [payment4, payment2, payment1]

    docket.draft = False
    docket.save()
    resp = app.get('/manage/invoicing/regie/%s/dockets/payments/' % regie.pk)
    assert 'New docket' in resp
    app.get('/manage/invoicing/regie/%s/docket/add/' % regie.pk, status=302)


def test_regie_docket_detail(app, admin_user):
    regie = Regie.objects.create(label='Foo')
    PaymentType.create_defaults(regie)
    docket = PaymentDocket.objects.create(
        regie=regie,
        date_end=now().date() + datetime.timedelta(days=1),
        draft=False,
        payment_types_info={
            'cash': 'foo bar\nblah',
            'check': 'foo bar',
        },
    )
    docket.payment_types.set(PaymentType.objects.all())
    docket.set_number()
    docket.save()

    payment1 = Payment.objects.create(
        regie=regie,
        amount=35,
        payment_type=PaymentType.objects.get(regie=regie, slug='cash'),
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Name1',
        docket=docket,
        date_payment=datetime.date(2022, 10, 1),
    )
    payment1.set_number()
    payment1.save()
    payment2 = Payment.objects.create(
        regie=regie,
        amount=42,
        payment_type=PaymentType.objects.get(regie=regie, slug='check'),
        payer_external_id='payer:2',
        payer_first_name='First2',
        payer_last_name='Name2',
        docket=docket,
    )
    payment2.set_number()
    payment2.save()
    payment3 = Payment.objects.create(
        regie=regie,
        amount=43,
        payment_type=PaymentType.objects.get(regie=regie, slug='check'),
        payer_external_id='payer:3',
        payer_first_name='First3',
        payer_last_name='Name3',
        docket=docket,
        cancelled_at=now(),
    )
    payment3.set_number()
    payment3.save()
    payment4 = Payment.objects.create(
        regie=regie,
        amount=23,
        payment_type=PaymentType.objects.get(regie=regie, slug='check'),
        payer_external_id='payer:3',
        payer_first_name='First3',
        payer_last_name='Name3',
        docket=docket,
    )
    payment4.set_number()
    payment4.save()
    Payment.objects.create(
        regie=regie,
        amount=44,
        payment_type=PaymentType.objects.get(regie=regie, slug='creditcard'),
    )

    app = login(app)
    resp = app.get('/manage/invoicing/regie/%s/' % regie.pk)
    resp = resp.click('Dockets')
    resp = resp.click(docket.formatted_number)
    assert [PyQuery(h3).text() for h3 in resp.pyquery('#main-content h3')] == [
        'Cash',
        'Check',
        'Cancelled payments',
    ]
    assert [PyQuery(li).text() for li in resp.pyquery('#main-content li')] == [
        'Initial docket amount: 143.00€',
        'Cancelled docket amount: 43.00€',
        'Final docket amount: 100.00€',
    ]
    assert len(resp.pyquery('table')) == 3
    assert [PyQuery(tr).text() for tr in PyQuery(resp.pyquery('table')[0]).find('tr')] == [
        'Payment R%02d-%s-0000001 dated %s (created on %s) from First1 Name1, amount 35.00€ (Cash)'
        % (
            regie.pk,
            payment1.date_payment.strftime('%y-%m'),
            payment1.date_payment.strftime('%d/%m/%Y'),
            payment1.created_at.strftime('%d/%m/%Y'),
        ),
    ]
    assert [PyQuery(tr).text() for tr in PyQuery(resp.pyquery('table')[1]).find('tr')] == [
        'Payment R%02d-%s-0000003 dated %s from First3 Name3, amount 23.00€ (Check)'
        % (regie.pk, payment4.created_at.strftime('%y-%m'), payment4.created_at.strftime('%d/%m/%Y')),
        'Payment R%02d-%s-0000001 dated %s from First2 Name2, amount 42.00€ (Check)'
        % (regie.pk, payment2.created_at.strftime('%y-%m'), payment2.created_at.strftime('%d/%m/%Y')),
    ]
    assert [PyQuery(tr).text() for tr in PyQuery(resp.pyquery('table')[2]).find('tr')] == [
        'Payment R%02d-%s-0000002 dated %s from First3 Name3, amount 43.00€ (Check)'
        % (regie.pk, payment3.created_at.strftime('%y-%m'), payment3.created_at.strftime('%d/%m/%Y')),
    ]
    assert len(resp.pyquery('p')) == 3
    assert (
        PyQuery(resp.pyquery('p')[0]).text()
        == 'Number of payments: 1\nTotal amount: 35.00€\nAdditional information:\nfoo bar\nblah'
    )
    assert (
        PyQuery(resp.pyquery('p')[1]).text()
        == 'Number of payments: 2\nTotal amount: 65.00€\nAdditional information:\nfoo bar'
    )
    assert PyQuery(resp.pyquery('p')[2]).text() == 'Number of payments: 1\nTotal amount: 43.00€'


def test_regie_docket_ods(app, admin_user):
    regie = Regie.objects.create(label='Foo')
    PaymentType.create_defaults(regie)
    docket = PaymentDocket.objects.create(
        regie=regie,
        date_end=now().date() + datetime.timedelta(days=1),
        draft=True,
        payment_types_info={
            'cash': 'foo bar\nblah',
            'check': 'foo bar',
        },
    )
    docket.payment_types.set(PaymentType.objects.all())

    payment1 = Payment.objects.create(
        regie=regie,
        amount=35,
        payment_type=PaymentType.objects.get(regie=regie, slug='cash'),
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Name1',
        docket=docket,
    )
    payment1.created_at = make_aware(datetime.datetime(2024, 10, 13, 1, 12))
    payment1.set_number()
    payment1.save()
    payment2 = Payment.objects.create(
        regie=regie,
        amount=42,
        payment_type=PaymentType.objects.get(regie=regie, slug='check'),
        payer_external_id='payer:2',
        payer_first_name='First2',
        payer_last_name='Name2',
        docket=docket,
    )
    payment2.set_number()
    payment2.save()
    payment3 = Payment.objects.create(
        regie=regie,
        amount=43,
        payment_type=PaymentType.objects.get(regie=regie, slug='check'),
        payer_external_id='payer:3',
        payer_first_name='First3',
        payer_last_name='Name3',
        docket=docket,
        cancelled_at=now(),
    )
    payment3.set_number()
    payment3.save()
    payment4 = Payment.objects.create(
        regie=regie,
        amount=23,
        payment_type=PaymentType.objects.get(regie=regie, slug='check'),
        payer_external_id='payer:3',
        payer_first_name='First3',
        payer_last_name='Name3',
        docket=docket,
    )
    payment4.set_number()
    payment4.save()

    app = login(app)
    resp = app.get('/manage/invoicing/regie/%s/docket/%s/' % (regie.pk, docket.pk))
    resp = resp.click('ODS export')
    assert resp.headers['Content-Type'] == 'application/vnd.oasis.opendocument.spreadsheet'
    assert resp.headers['Content-Disposition'] == 'attachment; filename="docket-TEMPORARY-%s.ods"' % docket.pk
    rows = list(get_ods_rows(resp))
    assert len(rows) == 15
    assert rows == [
        ['Initial docket amount', 'Cancelled docket amount', 'Final docket amount'],
        ['143.00', '43.00', '100.00'],
        ['Number of payments', 'Total amount', 'Payment type', 'Additional information'],
        ['1', '35.00', 'Cash', 'foo bar\nblah'],
        [
            'Number',
            'Date',
            'Payer ID',
            'Payer first name',
            'Payer last name',
            'Payment type',
            'Total amount',
            'Check issuer',
            'Check bank/organism',
            'Check number',
            'Bank transfer number',
            'Reference',
        ],
        [
            payment1.formatted_number,
            '10/13/2024',
            'payer:1',
            'First1',
            'Name1',
            'Cash',
            '35.00',
            None,
            None,
            None,
            None,
            None,
        ],
        ['Number of payments', 'Total amount', 'Payment type', 'Additional information'],
        ['2', '65.00', 'Check', 'foo bar'],
        [
            'Number',
            'Date',
            'Payer ID',
            'Payer first name',
            'Payer last name',
            'Payment type',
            'Total amount',
            'Check issuer',
            'Check bank/organism',
            'Check number',
            'Bank transfer number',
            'Reference',
        ],
        [
            payment4.formatted_number,
            payment4.created_at.strftime('%m/%d/%Y'),
            'payer:3',
            'First3',
            'Name3',
            'Check',
            '23.00',
            None,
            None,
            None,
            None,
            None,
        ],
        [
            payment2.formatted_number,
            payment2.created_at.strftime('%m/%d/%Y'),
            'payer:2',
            'First2',
            'Name2',
            'Check',
            '42.00',
            None,
            None,
            None,
            None,
            None,
        ],
        ['Number of payments', 'Total amount', 'Cancelled payments'],
        ['1', '43.00'],
        [
            'Number',
            'Date',
            'Payer ID',
            'Payer first name',
            'Payer last name',
            'Payment type',
            'Total amount',
            'Check issuer',
            'Check bank/organism',
            'Check number',
            'Bank transfer number',
            'Reference',
            'Cancelled on',
            'Cancellation reason',
        ],
        [
            payment3.formatted_number,
            payment3.created_at.strftime('%m/%d/%Y'),
            'payer:3',
            'First3',
            'Name3',
            'Check',
            '43.00',
            None,
            None,
            None,
            None,
            None,
            payment3.cancelled_at.strftime('%m/%d/%Y'),
            None,
        ],
    ]

    docket.draft = False
    docket.set_number()
    docket.save()
    resp = app.get('/manage/invoicing/regie/%s/docket/%s/export/ods/' % (regie.pk, docket.pk))
    assert resp.headers['Content-Type'] == 'application/vnd.oasis.opendocument.spreadsheet'
    assert resp.headers['Content-Disposition'] == 'attachment; filename="docket-B%02d-%s-0000001.ods"' % (
        regie.pk,
        docket.created_at.strftime('%y-%m'),
    )


def test_regie_docket_pdf(app, admin_user):
    regie = Regie.objects.create(label='Foo')
    PaymentType.create_defaults(regie)
    docket = PaymentDocket.objects.create(
        regie=regie,
        date_end=now().date() + datetime.timedelta(days=1),
        draft=True,
        payment_types_info={
            'cash': 'foo bar\nblah',
            'check': 'foo bar',
        },
    )
    docket.payment_types.set(PaymentType.objects.all())

    payment1 = Payment.objects.create(
        regie=regie,
        amount=35,
        payment_type=PaymentType.objects.get(regie=regie, slug='cash'),
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Name1',
        docket=docket,
    )
    payment1.created_at = make_aware(datetime.datetime(2024, 10, 13, 1, 12))
    payment1.set_number()
    payment1.save()
    payment2 = Payment.objects.create(
        regie=regie,
        amount=42,
        payment_type=PaymentType.objects.get(regie=regie, slug='check'),
        payer_external_id='payer:2',
        payer_first_name='First2',
        payer_last_name='Name2',
        docket=docket,
    )
    payment2.set_number()
    payment2.save()
    payment3 = Payment.objects.create(
        regie=regie,
        amount=43,
        payment_type=PaymentType.objects.get(regie=regie, slug='check'),
        payer_external_id='payer:3',
        payer_first_name='First3',
        payer_last_name='Name3',
        docket=docket,
        cancelled_at=now(),
    )
    payment3.created_at = make_aware(datetime.datetime(2024, 10, 13, 1, 11))
    payment3.set_number()
    payment3.save()
    payment4 = Payment.objects.create(
        regie=regie,
        amount=23,
        payment_type=PaymentType.objects.get(regie=regie, slug='check'),
        payer_external_id='payer:3',
        payer_first_name='First3',
        payer_last_name='Name3',
        docket=docket,
    )
    payment4.set_number()
    payment4.save()

    app = login(app)
    resp = app.get('/manage/invoicing/regie/%s/docket/%s/' % (regie.pk, docket.pk))
    assert '/manage/invoicing/regie/%s/docket/%s/export/pdf/' % (regie.pk, docket.pk) in resp
    resp = app.get('/manage/invoicing/regie/%s/docket/%s/export/pdf/?html' % (regie.pk, docket.pk))
    assert [PyQuery(h2).text() for h2 in resp.pyquery('h2')] == [
        'Cash',
        'Check',
        'Cancelled payments',
    ]
    assert [PyQuery(li).text() for li in resp.pyquery('li')] == [
        'Initial docket amount: 143.00€',
        'Cancelled docket amount: 43.00€',
        'Final docket amount: 100.00€',
    ]
    assert len(resp.pyquery('table')) == 3
    assert [PyQuery(tr).text() for tr in PyQuery(resp.pyquery('table')[0]).find('tr')] == [
        'Number\nDate\nPayer ID\nPayer first name\nPayer last name\nTotal amount\n'
        'Check issuer\nCheck bank/organism\nCheck number\nBank transfer number\nReference',
        '%s\n13/10/2024\npayer:1\nFirst1\nName1\n35.00€' % (payment1.formatted_number),
    ]
    assert [PyQuery(tr).text() for tr in PyQuery(resp.pyquery('table')[1]).find('tr')] == [
        'Number\nDate\nPayer ID\nPayer first name\nPayer last name\nTotal amount\n'
        'Check issuer\nCheck bank/organism\nCheck number\nBank transfer number\nReference',
        '%s\n%s\npayer:3\nFirst3\nName3\n23.00€'
        % (payment4.formatted_number, payment4.created_at.strftime('%d/%m/%Y')),
        '%s\n%s\npayer:2\nFirst2\nName2\n42.00€'
        % (payment2.formatted_number, payment2.created_at.strftime('%d/%m/%Y')),
    ]
    assert [PyQuery(tr).text() for tr in PyQuery(resp.pyquery('table')[2]).find('tr')] == [
        'Number\nDate\nPayer ID\nPayer first name\nPayer last name\nTotal amount\n'
        'Check issuer\nCheck bank/organism\nCheck number\nBank transfer number\nReference\nCancelled on\nCancellation reason',
        '%s\n%s\npayer:3\nFirst3\nName3\n43.00€\n%s'
        % (
            payment3.formatted_number,
            payment3.created_at.strftime('%d/%m/%Y'),
            localtime(payment3.cancelled_at).strftime('%d/%m/%Y'),
        ),
    ]
    assert len(resp.pyquery('p')) == 3
    assert (
        PyQuery(resp.pyquery('p')[0]).text()
        == 'Number of payments: 1\nTotal amount: 35.00€\nAdditional information:\nfoo bar\nblah'
    )
    assert (
        PyQuery(resp.pyquery('p')[1]).text()
        == 'Number of payments: 2\nTotal amount: 65.00€\nAdditional information:\nfoo bar'
    )
    assert PyQuery(resp.pyquery('p')[2]).text() == 'Number of payments: 1\nTotal amount: 43.00€'


def test_regie_docket_edit(app, admin_user):
    regie = Regie.objects.create(label='Foo')
    PaymentType.create_defaults(regie)
    other_docket = PaymentDocket.objects.create(regie=regie, date_end=now().date(), draft=False)
    docket = PaymentDocket.objects.create(regie=regie, date_end=now().date(), draft=True)

    payment1 = Payment.objects.create(
        regie=regie,
        amount=35,
        payment_type=PaymentType.objects.get(regie=regie, slug='cash'),
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Name1',
    )
    payment1.created_at = now() - datetime.timedelta(days=2)
    payment1.set_number()
    payment1.save()

    payment2 = Payment.objects.create(
        regie=regie,
        amount=55,
        payment_type=PaymentType.objects.get(regie=regie, slug='check'),
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Name1',
    )
    payment2.created_at = now() - datetime.timedelta(days=1)
    payment2.set_number()
    payment2.save()

    payment3 = Payment.objects.create(
        regie=regie,
        amount=2,
        payment_type=PaymentType.objects.get(regie=regie, slug='creditcard'),
        payer_external_id='payer:3',
        payer_first_name='First3',
        payer_last_name='Name3',
    )
    payment3.created_at = now() - datetime.timedelta(days=1)
    payment3.set_number()
    payment3.save()

    payment4 = Payment.objects.create(
        regie=regie,
        amount=2,
        payment_type=PaymentType.objects.get(regie=regie, slug='check'),
        payer_external_id='payer:3',
        payer_first_name='First3',
        payer_last_name='Name3',
    )
    payment4.set_number()
    payment4.save()

    payment5 = Payment.objects.create(
        regie=regie,
        amount=2,
        payment_type=PaymentType.objects.get(regie=regie, slug='check'),
        payer_external_id='payer:3',
        payer_first_name='First3',
        payer_last_name='Name3',
        cancelled_at=now(),
    )
    payment5.set_number()
    payment5.save()

    payment6 = Payment.objects.create(
        regie=regie,
        amount=2,
        payment_type=PaymentType.objects.get(regie=regie, slug='check'),
        payer_external_id='payer:3',
        payer_first_name='First3',
        payer_last_name='Name3',
        docket=other_docket,
    )
    payment6.set_number()
    payment6.save()

    app = login(app)
    resp = app.get('/manage/invoicing/regie/%s/docket/%s/' % (regie.pk, docket.pk))
    resp = resp.click('Edit')
    resp.form['payment_types'] = [
        PaymentType.objects.get(slug='cash').pk,
        PaymentType.objects.get(slug='check').pk,
    ]
    resp.form['date_end'] = now().date()
    resp = resp.form.submit()
    assert resp.location.endswith('/manage/invoicing/regie/%s/docket/%s/' % (regie.pk, docket.pk))
    docket.refresh_from_db()
    assert docket.draft is True
    assert docket.date_end == now().date()
    assert docket.formatted_number == ''
    assert list(docket.payment_types.all()) == list(PaymentType.objects.filter(slug__in=['cash', 'check']))
    assert list(docket.payment_set.all().order_by('-pk')) == [payment2, payment1]

    resp = app.get('/manage/invoicing/regie/%s/docket/%s/edit/' % (regie.pk, docket.pk))
    resp.form['payment_types'] = [p.pk for p in PaymentType.objects.all()]
    resp.form['date_end'] = now().date() + datetime.timedelta(days=1)
    resp = resp.form.submit()
    assert resp.location.endswith('/manage/invoicing/regie/%s/docket/%s/' % (regie.pk, docket.pk))
    docket.refresh_from_db()
    assert docket.draft is True
    assert docket.date_end == now().date() + datetime.timedelta(days=1)
    assert list(docket.payment_types.all()) == list(PaymentType.objects.all())
    assert list(docket.payment_set.all().order_by('-pk')) == [payment4, payment3, payment2, payment1]

    resp = app.get('/manage/invoicing/regie/%s/docket/%s/edit/' % (regie.pk, docket.pk))
    resp.form['payment_types'] = [
        PaymentType.objects.get(slug='cash').pk,
        PaymentType.objects.get(slug='check').pk,
    ]
    resp = resp.form.submit()
    assert resp.location.endswith('/manage/invoicing/regie/%s/docket/%s/' % (regie.pk, docket.pk))
    docket.refresh_from_db()
    assert docket.draft is True
    assert docket.date_end == now().date() + datetime.timedelta(days=1)
    assert list(docket.payment_types.all()) == list(PaymentType.objects.filter(slug__in=['cash', 'check']))
    assert list(docket.payment_set.all().order_by('-pk')) == [payment4, payment2, payment1]

    docket.draft = False
    docket.save()
    app.get('/manage/invoicing/regie/%s/docket/%s/edit/' % (regie.pk, docket.pk), status=404)


def test_regie_docket_payment_type_edit(app, admin_user):
    regie = Regie.objects.create(label='Foo')
    PaymentType.create_defaults(regie)
    docket = PaymentDocket.objects.create(
        regie=regie,
        date_end=now().date() + datetime.timedelta(days=1),
        draft=False,
        payment_types_info={
            'cash': 'foo bar\nblah',
            'check': 'foo bar',
        },
    )
    docket.payment_types.set(PaymentType.objects.all())
    docket.set_number()
    docket.save()

    Payment.objects.create(
        regie=regie,
        amount=35,
        payment_type=PaymentType.objects.get(regie=regie, slug='cash'),
        docket=docket,
    )

    app = login(app)
    resp = app.get('/manage/invoicing/regie/%s/docket/%s/' % (regie.pk, docket.pk))
    assert (
        '/manage/invoicing/regie/%s/docket/%s/payment-type/%s/'
        % (regie.pk, docket.pk, PaymentType.objects.get(regie=regie, slug='cash').pk)
        not in resp
    )
    app.get(
        '/manage/invoicing/regie/%s/docket/%s/payment-type/%s/'
        % (regie.pk, docket.pk, PaymentType.objects.get(regie=regie, slug='cash').pk),
        status=404,
    )

    docket.draft = True
    docket.save()
    resp = app.get('/manage/invoicing/regie/%s/docket/%s/' % (regie.pk, docket.pk))
    resp = resp.click(
        href='/manage/invoicing/regie/%s/docket/%s/payment-type/%s/'
        % (regie.pk, docket.pk, PaymentType.objects.get(regie=regie, slug='cash').pk)
    )
    assert resp.form['additionnal_information'].value == 'foo bar\nblah'
    resp.form['additionnal_information'] = 'baz'
    resp = resp.form.submit()
    assert resp.location.endswith('/manage/invoicing/regie/%s/docket/%s/' % (regie.pk, docket.pk))
    docket.refresh_from_db()
    assert docket.payment_types_info == {
        'cash': 'baz',
        'check': 'foo bar',
    }


def test_regie_docket_validate(app, admin_user):
    regie = Regie.objects.create(label='Foo')
    PaymentType.create_defaults(regie)
    docket = PaymentDocket.objects.create(regie=regie, date_end=now().date(), draft=True)

    app = login(app)
    resp = app.get('/manage/invoicing/regie/%s/docket/%s/' % (regie.pk, docket.pk))
    resp = resp.click('Validate')
    resp = resp.form.submit()
    assert resp.location.endswith('/manage/invoicing/regie/%s/docket/%s/' % (regie.pk, docket.pk))
    docket.refresh_from_db()
    assert docket.draft is False
    assert docket.formatted_number == 'B%02d-%s-0000001' % (regie.pk, docket.created_at.strftime('%y-%m'))

    app.get('/manage/invoicing/regie/%s/docket/%s/validate/' % (regie.pk, docket.pk), status=404)


def test_regie_docket_delete(app, admin_user):
    regie = Regie.objects.create(label='Foo')
    PaymentType.create_defaults(regie)
    docket = PaymentDocket.objects.create(
        regie=regie, date_end=now().date() + datetime.timedelta(days=1), draft=False
    )
    docket.set_number()
    docket.save()

    Payment.objects.create(
        regie=regie,
        amount=35,
        payment_type=PaymentType.objects.get(regie=regie, slug='cash'),
        docket=docket,
    )
    Payment.objects.create(
        regie=regie,
        amount=42,
        payment_type=PaymentType.objects.get(regie=regie, slug='check'),
        docket=docket,
    )
    Payment.objects.create(
        regie=regie,
        amount=43,
        payment_type=PaymentType.objects.get(regie=regie, slug='check'),
        docket=docket,
        cancelled_at=now(),
    )
    Payment.objects.create(
        regie=regie,
        amount=23,
        payment_type=PaymentType.objects.get(regie=regie, slug='check'),
        docket=docket,
    )
    Payment.objects.create(
        regie=regie,
        amount=44,
        payment_type=PaymentType.objects.get(regie=regie, slug='creditcard'),
    )
    assert Payment.objects.filter(docket__isnull=False).count() == 4
    assert Payment.objects.count() == 5

    app = login(app)
    resp = app.get('/manage/invoicing/regie/%s/docket/%s/' % (regie.pk, docket.pk))
    assert 'Delete' not in resp
    app.get('/manage/invoicing/regie/%s/docket/%s/delete/' % (regie.pk, docket.pk), status=404)

    docket.draft = True
    docket.save()
    resp = app.get('/manage/invoicing/regie/%s/docket/%s/' % (regie.pk, docket.pk))
    resp = resp.click('Delete')
    resp = resp.form.submit()
    assert resp.location.endswith('/manage/invoicing/regie/%s/dockets/' % regie.pk)
    assert PaymentDocket.objects.filter(pk=docket.pk).exists() is False
    assert Payment.objects.filter(docket__isnull=False).count() == 0
    assert Payment.objects.count() == 5
