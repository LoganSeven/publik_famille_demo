import datetime

import pytest
from django.utils.timezone import now

from lingo.agendas.models import Agenda
from lingo.invoicing.models import (
    Campaign,
    Invoice,
    InvoiceLine,
    InvoiceLinePayment,
    Payment,
    PaymentType,
    Pool,
    Regie,
)

pytestmark = pytest.mark.django_db


def test_statistics_list(app, user):
    regie = Regie.objects.create(label='Regie 1')
    Agenda.objects.create(label='Activity 1', regie=regie)

    Regie.objects.create(label='Regie 2')

    Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=now().date(),
        date_due=datetime.date(2022, 10, 31),
        payer_external_id='payer:1',
        payer_first_name='First',
        payer_last_name='Last',
        regie=regie,
    )

    # unauthorized
    app.get('/api/statistics/', status=403)

    app.authorization = ('Basic', ('john.doe', 'password'))
    resp = app.get('/api/statistics/')
    regie_filter = [x for x in resp.json['data'][0]['filters'] if x['id'] == 'regie'][0]
    assert regie_filter['options'] == [
        {'id': '_all', 'label': 'All'},
        {'id': 'regie-1', 'label': 'Regie 1'},
        {'id': 'regie-2', 'label': 'Regie 2'},
    ]

    activity_filter = [x for x in resp.json['data'][0]['filters'] if x['id'] == 'activity'][0]
    assert activity_filter['options'] == [
        {'id': '_all', 'label': 'All'},
        {'id': 'activity-1', 'label': 'Activity 1'},
    ]

    payer_filter = [x for x in resp.json['data'][0]['filters'] if x['id'] == 'payer_external_id'][0]
    assert payer_filter['options'] == [
        {'id': '_all', 'label': 'All'},
        {'id': 'payer:1', 'label': 'First Last'},
    ]


def test_statistics_invoice(app, user):
    app.get('/api/statistics/invoice/', status=403)

    app.authorization = ('Basic', ('john.doe', 'password'))
    resp = app.get('/api/statistics/invoice/', status=200)
    assert resp.json['data']['series'] == []

    regie = Regie.objects.create(label='Regie 1')
    Agenda.objects.create(label='Activity 1', regie=regie)
    PaymentType.create_defaults(regie)
    payment = Payment.objects.create(
        regie=regie,
        amount=2,
        payment_type=PaymentType.objects.get(regie=regie, slug='cash'),
    )

    regie2 = Regie.objects.create(label='Regie 2')

    for i in range(1, 4):
        invoice = Invoice.objects.create(
            date_publication=datetime.date(2022, 10, i),
            date_payment_deadline=now().date(),
            date_due=datetime.date(2022, 10, 31),
            regie=regie if i == 1 else regie2,
            payer_external_id='payer:1' if i == 1 else 'payer:2',
        )

        for j in range(1, 5):
            line = InvoiceLine.objects.create(
                event_date=now().date(),
                invoice=invoice,
                quantity=i,
                unit_amount=j,
                agenda_slug='activity-1' if i == 1 else '',
            )
            if i == 1:
                InvoiceLinePayment.objects.create(
                    payment=payment,
                    amount=1,
                    line=line,
                )

    resp = app.get('/api/statistics/invoice/')
    assert resp.json['data']['x_labels'] == ['2022-10-01', '2022-10-02', '2022-10-03']
    assert resp.json['data']['series'] == [{'data': [10, 20, 30], 'label': 'Total amount'}]

    # period filter
    resp = app.get('/api/statistics/invoice/?start=2022-10-02&end=2022-10-02')
    assert resp.json['data']['x_labels'] == ['2022-10-02']
    assert resp.json['data']['series'][0]['data'] == [20]

    # regie filter
    resp = app.get('/api/statistics/invoice/?regie=regie-1')
    assert resp.json['data']['x_labels'] == ['2022-10-01']
    assert resp.json['data']['series'][0]['data'] == [10]

    resp = app.get('/api/statistics/invoice/?regie=regie-2')
    assert resp.json['data']['x_labels'] == ['2022-10-02', '2022-10-03']
    assert resp.json['data']['series'][0]['data'] == [20, 30]

    resp = app.get('/api/statistics/invoice/?regie=unknown')
    assert resp.json['data']['series'] == []

    # activity filter
    resp = app.get('/api/statistics/invoice/?activity=activity-1')
    assert resp.json['data']['x_labels'] == ['2022-10-01']
    assert resp.json['data']['series'][0]['data'] == [10]

    resp = app.get('/api/statistics/invoice/?activity=unknown')
    assert resp.json['data']['series'] == []

    # payer_external_id filter
    resp = app.get('/api/statistics/invoice/?payer_external_id=payer:1')
    assert resp.json['data']['x_labels'] == ['2022-10-01']
    assert resp.json['data']['series'][0]['data'] == [10]

    resp = app.get('/api/statistics/invoice/?payer_external_id=unknown')
    assert resp.json['data']['series'] == []

    # count measure
    invoice = Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 2),
        date_payment_deadline=now().date(),
        date_due=datetime.date(2022, 10, 31),
        regie=regie,
    )

    resp = app.get('/api/statistics/invoice/?measures=count')
    assert resp.json['data']['x_labels'] == ['2022-10-01', '2022-10-02', '2022-10-03']
    assert resp.json['data']['series'] == [{'data': [1, 2, 1], 'label': 'Invoice count'}]

    # remaining amount measure
    resp = app.get('/api/statistics/invoice/?measures=remaining_amount')
    assert resp.json['data']['x_labels'] == ['2022-10-01', '2022-10-02', '2022-10-03']
    assert resp.json['data']['series'] == [{'data': [6, 20, 30], 'label': 'Remaining amount'}]

    # paid amount measure
    resp = app.get('/api/statistics/invoice/?measures=paid_amount')
    assert resp.json['data']['x_labels'] == ['2022-10-01', '2022-10-02', '2022-10-03']
    assert resp.json['data']['series'] == [{'data': [4, 0, 0], 'label': 'Paid amount'}]

    # multiple measures
    resp = app.get('/api/statistics/invoice/?measures=total_amount&measures=count')
    assert resp.json['data']['x_labels'] == ['2022-10-01', '2022-10-02', '2022-10-03']
    assert resp.json['data']['series'] == [
        {'data': [10, 20, 30], 'label': 'Total amount'},
        {'data': [1, 2, 1], 'label': 'Invoice count'},
    ]

    # invalid measures choice
    resp = app.get('/api/statistics/invoice/?measures=unknown', status=400)
    assert resp.json['err'] == 1
    assert 'measures' in resp.json['errors']

    # campaign is not finalized
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
    )
    invoice = Invoice.objects.get(date_publication=datetime.date(2022, 10, 3))
    invoice.pool = pool
    invoice.save()
    resp = app.get('/api/statistics/invoice/')
    assert resp.json['data']['x_labels'] == ['2022-10-01', '2022-10-02']
    assert resp.json['data']['series'] == [{'data': [10, 20], 'label': 'Total amount'}]

    # campaign is finalized
    campaign.finalized = True
    campaign.save()
    resp = app.get('/api/statistics/invoice/')
    assert resp.json['data']['x_labels'] == ['2022-10-01', '2022-10-02', '2022-10-03']
    assert resp.json['data']['series'] == [{'data': [10, 20, 30], 'label': 'Total amount'}]

    # invoice is cancelled
    invoice.cancelled_at = now()
    invoice.save()
    resp = app.get('/api/statistics/invoice/')
    assert resp.json['data']['x_labels'] == ['2022-10-01', '2022-10-02']
    assert resp.json['data']['series'] == [{'data': [10, 20], 'label': 'Total amount'}]
