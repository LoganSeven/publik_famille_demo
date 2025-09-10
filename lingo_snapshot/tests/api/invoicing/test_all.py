import datetime

import pytest

from lingo.invoicing.models import InjectedLine, InvoiceCancellationReason, PaymentType, Regie

pytestmark = pytest.mark.django_db


def test_invoice_cancellation_reasons(app, user):
    InvoiceCancellationReason.objects.create(label='Foo')
    InvoiceCancellationReason.objects.create(label='Bar')
    InvoiceCancellationReason.objects.create(label='Baz')
    InvoiceCancellationReason.objects.create(label='Disabled', disabled=True)

    app.get('/api/invoice-cancellation-reasons/', status=403)
    app.authorization = ('Basic', ('john.doe', 'password'))

    resp = app.get('/api/invoice-cancellation-reasons/')
    data = resp.json
    assert data['data'] == [
        {'id': 'bar', 'text': 'Bar', 'slug': 'bar'},
        {'id': 'baz', 'text': 'Baz', 'slug': 'baz'},
        {'id': 'foo', 'text': 'Foo', 'slug': 'foo'},
    ]


def test_regies_empty(app, user):
    app.authorization = ('Basic', ('john.doe', 'password'))
    resp = app.get('/api/regies/')
    data = resp.json
    assert data['data'] == []


def test_regies(app, user):
    app.authorization = ('Basic', ('john.doe', 'password'))
    Regie.objects.create(label='Bar')
    Regie.objects.create(label='Foo')
    resp = app.get('/api/regies/')
    data = resp.json
    assert data['data'] == [
        {'id': 'bar', 'text': 'Bar', 'slug': 'bar'},
        {'id': 'foo', 'text': 'Foo', 'slug': 'foo'},
    ]


def test_payment_types(app, user):
    regie = Regie.objects.create(label='Bar')
    PaymentType.create_defaults(regie)
    PaymentType.objects.create(regie=regie, label='Foo')
    PaymentType.objects.create(regie=regie, label='Disabled', disabled=True)

    app.get('/api/regie/%s/payment-types/' % regie.slug, status=403)
    app.authorization = ('Basic', ('john.doe', 'password'))

    resp = app.get('/api/regie/%s/payment-types/' % regie.slug)
    data = resp.json
    assert data['data'] == [
        {'id': 'cash', 'text': 'Cash', 'slug': 'cash'},
        {'id': 'cesu', 'text': 'CESU', 'slug': 'cesu'},
        {'id': 'check', 'text': 'Check', 'slug': 'check'},
        {'id': 'creditcard', 'text': 'Credit card', 'slug': 'creditcard'},
        {'id': 'directdebit', 'text': 'Direct debit', 'slug': 'directdebit'},
        {'id': 'foo', 'text': 'Foo', 'slug': 'foo'},
        {'id': 'holidaycheck', 'text': 'Holiday check', 'slug': 'holidaycheck'},
        {'id': 'online', 'text': 'Online', 'slug': 'online'},
    ]


def test_add_injected_line(app, user):
    app.post('/api/regie/foo/injected-lines/', status=403)
    app.authorization = ('Basic', ('john.doe', 'password'))

    app.post('/api/regie/foo/injected-lines/', status=404)

    regie = Regie.objects.create(slug='foo')
    resp = app.post('/api/regie/foo/injected-lines/', status=400)
    assert resp.json['err']
    assert resp.json['errors'] == {
        'event_date': ['This field is required.'],
        'slug': ['This field is required.'],
        'label': ['This field is required.'],
        'amount': ['This field is required.'],
        'user_external_id': ['This field is required.'],
        'payer_external_id': ['This field is required.'],
    }

    params = {
        'event_date': '2023-01-17',
        'slug': 'foobar',
        'label': 'Foo Bar',
        'amount': 2,
        'user_external_id': 'user:1',
        'payer_external_id': 'payer:1',
    }
    resp = app.post('/api/regie/foo/injected-lines/', params=params)
    assert resp.json['err'] == 0
    injected_line = InjectedLine.objects.get(pk=resp.json['id'])
    assert injected_line.event_date == datetime.date(2023, 1, 17)
    assert injected_line.slug == 'foobar'
    assert injected_line.label == 'Foo Bar'
    assert injected_line.amount == 2
    assert injected_line.user_external_id == 'user:1'
    assert injected_line.payer_external_id == 'payer:1'
    assert injected_line.regie == regie

    params.update(
        {
            'amount': -70,
        }
    )
    resp = app.post('/api/regie/foo/injected-lines/', params=params)
    assert resp.json['err'] == 0
    injected_line = InjectedLine.objects.get(pk=resp.json['id'])
    assert injected_line.event_date == datetime.date(2023, 1, 17)
    assert injected_line.slug == 'foobar'
    assert injected_line.label == 'Foo Bar'
    assert injected_line.amount == -70
    assert injected_line.user_external_id == 'user:1'
    assert injected_line.payer_external_id == 'payer:1'
    assert injected_line.regie == regie
