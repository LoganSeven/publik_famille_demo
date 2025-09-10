import copy
import datetime
import uuid
from unittest import mock

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from lingo.agendas.models import Agenda
from lingo.basket.models import Basket, BasketLine, BasketLineItem
from lingo.invoicing.errors import PayerDataError
from lingo.invoicing.models import DraftInvoice, DraftInvoiceLine, Regie
from lingo.invoicing.utils import Link
from lingo.pricing.errors import PricingError
from lingo.pricing.models import Pricing

pytestmark = pytest.mark.django_db


@mock.patch('lingo.invoicing.models.Regie.get_payer_data')
@mock.patch('lingo.pricing.models.Pricing.get_pricing_data_for_event')
def test_from_bookings_errors(mock_pricing_data_event, mock_payer_data, app, user):
    app.post_json('/api/regie/foo/basket/%s/lines/from-bookings/' % uuid.uuid4(), status=403)
    app.post_json('/api/regie/foo/basket/lines/from-bookings/dry-run/', status=403)
    app.authorization = ('Basic', ('john.doe', 'password'))

    app.post_json('/api/regie/foo/basket/%s/lines/from-bookings/' % uuid.uuid4(), status=404)
    app.post_json('/api/regie/foo/basket/lines/from-bookings/dry-run/', status=404)

    regie = Regie.objects.create(label='Foo')
    app.post_json('/api/regie/foo/basket/%s/lines/from-bookings/' % uuid.uuid4(), status=404)

    invoice = DraftInvoice.objects.create(
        regie=regie,
        date_publication=datetime.date(2023, 4, 21),
        date_payment_deadline=datetime.date(2023, 4, 22),
        date_due=datetime.date(2023, 4, 23),
    )
    basket = Basket.objects.create(regie=regie, draft_invoice=invoice)
    app.post_json('/api/regie/bar/basket/%s/lines/from-bookings/' % basket.uuid, status=404)

    resp = app.post_json('/api/regie/foo/basket/%s/lines/from-bookings/' % basket.uuid, status=400)
    assert resp.json['err'] == 1
    assert resp.json['err_class'] == 'invalid payload'
    assert resp.json['errors'] == {
        'user_external_id': ['This field is required.'],
        'user_first_name': ['This field is required.'],
        'user_last_name': ['This field is required.'],
        'booked_events': ['This field is required.'],
        'cancelled_events': ['This field is required.'],
    }
    resp = app.post_json('/api/regie/foo/basket/lines/from-bookings/dry-run/', status=400)
    assert resp.json['errors'] == {
        'user_external_id': ['This field is required.'],
        'payer_external_id': ['This field is required.'],
        'booked_events': ['This field is required.'],
        'cancelled_events': ['This field is required.'],
    }

    params = {
        'user_external_id': 'user:1',
        'user_first_name': 'First',
        'user_last_name': 'Last',
        'information_message': 'foo baz',
        'cancel_information_message': 'foo bar',
        'form_url': 'http://form.com',
        'validation_callback_url': 'http://validation.com',
        'payment_callback_url': 'http://payment.com',
        'credit_callback_url': 'http://credit.com',
        'cancel_callback_url': 'http://cancel.com',
        'expiration_callback_url': 'http://expiration.com',
        'booked_events': [],
        'cancelled_events': [],
    }
    resp = app.post_json(
        '/api/regie/foo/basket/%s/lines/from-bookings/' % basket.uuid, params=params, status=400
    )
    assert resp.json['err'] == 1
    assert resp.json['err_class'] == 'no changes'
    assert BasketLine.objects.count() == 0
    assert BasketLineItem.objects.count() == 0
    params['payer_external_id'] = 'payer:1'
    resp = app.post_json('/api/regie/foo/basket/lines/from-bookings/dry-run/', params=params, status=400)
    assert resp.json['err'] == 1
    assert resp.json['err_class'] == 'no changes'
    assert BasketLine.objects.count() == 0
    assert BasketLineItem.objects.count() == 0
    params.pop('payer_external_id')

    # check agenda's regie
    params['booked_events'] = [
        {
            'agenda_slug': 'unknown',
            'slug': 'event1',
            'primary_event': 'foo@primary',
            'datetime': '2025-06-10 15:00:00',
            'label': 'Event 1',
        }
    ]
    agenda_unknown = Agenda.objects.create(label='unknown')
    resp = app.post_json(
        '/api/regie/foo/basket/%s/lines/from-bookings/' % basket.uuid, params=params, status=400
    )
    assert resp.json['err'] == 1
    assert resp.json['err_class'] == 'wrong regie for agendas: unknown'
    assert BasketLine.objects.count() == 0
    assert BasketLineItem.objects.count() == 0
    params['payer_external_id'] = 'payer:1'
    resp = app.post_json('/api/regie/foo/basket/lines/from-bookings/dry-run/', params=params, status=400)
    assert resp.json['err'] == 1
    assert resp.json['err_class'] == 'wrong regie for agendas: unknown'
    assert BasketLine.objects.count() == 0
    assert BasketLineItem.objects.count() == 0
    params.pop('payer_external_id')

    agenda_foo = Agenda.objects.create(label='foo', regie=regie)
    other_regie = Regie.objects.create(label='Other Foo')
    agenda_unknown.regie = other_regie
    agenda_unknown.save()
    params['booked_events'] = [
        {
            'agenda_slug': 'foo',
            'slug': 'event1',
            'primary_event': 'foo@primary',
            'datetime': '2025-06-10 15:00:00',
            'label': 'Event 1',
        }
    ]
    params['cancelled_events'] = [
        {
            'agenda_slug': 'unknown',
            'slug': 'event1',
            'primary_event': 'foo@primary',
            'datetime': '2025-06-10 15:00:00',
            'label': 'Event 1',
        },
        {
            'agenda_slug': 'unknown2',
            'slug': 'event1',
            'primary_event': 'foo@primary',
            'datetime': '2025-06-10 15:00:00',
            'label': 'Event 1',
        },
    ]
    resp = app.post_json(
        '/api/regie/foo/basket/%s/lines/from-bookings/' % basket.uuid, params=params, status=400
    )
    assert resp.json['err'] == 1
    assert resp.json['err_class'] == 'wrong regie for agendas: unknown, unknown2'
    assert BasketLine.objects.count() == 0
    assert BasketLineItem.objects.count() == 0
    params['payer_external_id'] = 'payer:1'
    resp = app.post_json('/api/regie/foo/basket/lines/from-bookings/dry-run/', params=params, status=400)
    params.pop('payer_external_id')
    assert resp.json['err'] == 1
    assert resp.json['err_class'] == 'wrong regie for agendas: unknown, unknown2'
    assert BasketLine.objects.count() == 0
    assert BasketLineItem.objects.count() == 0

    # a line already exists for this user
    agenda_unknown.regie = regie
    agenda_unknown.save()
    agenda_unknown2 = Agenda.objects.create(label='unknown2', regie=regie)
    BasketLine.objects.create(basket=basket, user_external_id='user:1')
    resp = app.post_json(
        '/api/regie/foo/basket/%s/lines/from-bookings/' % basket.uuid, params=params, status=400
    )
    assert resp.json['err']
    assert resp.json['errors'] == {
        'user_external_id': ['a line is already opened in basket for this user_external_id']
    }

    # ok for another user
    pricing = Pricing.objects.create(
        date_start=datetime.date(year=2025, month=6, day=1),
        date_end=datetime.date(year=2025, month=7, day=1),
    )
    pricing.agendas.add(agenda_foo, agenda_unknown, agenda_unknown2)
    mock_payer_data.return_value = {
        'first_name': 'First1',
        'last_name': 'Last1',
        'address': '41 rue des kangourous\n99999 Kangourou Ville',
        'email': 'email1',
        'phone': 'phone1',
    }
    mock_pricing_data_event.return_value = {'foo1': 'bar1', 'pricing': 1, 'accounting_code': '414141'}
    params['user_external_id'] = 'another'
    app.post_json('/api/regie/foo/basket/%s/lines/from-bookings/' % basket.uuid, params=params, status=200)
    BasketLineItem.objects.all().delete()
    BasketLine.objects.all().delete()

    # basket wrong status
    params['user_external_id'] = 'user:1'
    for status in ['tobepaid', 'completed', 'cancelled', 'expired']:
        basket.status = status
        basket.save()
        app.post_json('/api/regie/foo/basket/%s/lines/from-bookings/' % basket.uuid, status=404)


@mock.patch('lingo.invoicing.models.Regie.get_payer_data')
@mock.patch('lingo.pricing.models.Pricing.get_pricing_data_for_event')
def test_from_bookings_pricing_error(mock_pricing_data_event, mock_payer_data, app, user):
    app.authorization = ('Basic', ('john.doe', 'password'))
    regie = Regie.objects.create(label='Foo')
    agenda = Agenda.objects.create(label='Foo', regie=regie)
    pricing = Pricing.objects.create(
        date_start=datetime.date(year=2025, month=6, day=1),
        date_end=datetime.date(year=2025, month=7, day=1),
    )
    pricing.agendas.add(agenda)
    invoice = DraftInvoice.objects.create(
        regie=regie,
        date_publication=datetime.date(2023, 4, 21),
        date_payment_deadline=datetime.date(2023, 4, 22),
        date_due=datetime.date(2023, 4, 23),
    )
    basket = Basket.objects.create(regie=regie, draft_invoice=invoice)

    mock_payer_data.return_value = {
        'first_name': 'First1',
        'last_name': 'Last1',
        'address': '41 rue des kangourous\n99999 Kangourou Ville',
        'email': 'email1',
        'phone': 'phone1',
    }
    mock_pricing_data_event.side_effect = [
        PricingError(details={'foo': 'bar'}),
        # dry-run
        PricingError(details={'foo': 'bar'}),
    ]
    params = {
        'user_external_id': 'user:1',
        'user_first_name': 'First',
        'user_last_name': 'Last',
        'information_message': 'foo baz',
        'cancel_information_message': 'foo bar',
        'form_url': 'http://form.com',
        'validation_callback_url': 'http://validation.com',
        'payment_callback_url': 'http://payment.com',
        'credit_callback_url': 'http://credit.com',
        'cancel_callback_url': 'http://cancel.com',
        'expiration_callback_url': 'http://expiration.com',
        'booked_events': [
            {
                'agenda_slug': 'foo',
                'slug': 'event1',
                'primary_event': 'foo@primary',
                'datetime': '2025-06-10 12:00:00',
                'label': 'Event 1',
            },
        ],
        'cancelled_events': [],
    }

    resp = app.post_json(
        '/api/regie/foo/basket/%s/lines/from-bookings/' % basket.uuid, params=params, status=400
    )
    assert resp.json['err'] == 1
    assert resp.json['err_class'] == "error: PricingError, details: {'foo': 'bar'}"
    assert BasketLine.objects.count() == 0
    assert BasketLineItem.objects.count() == 0
    params['payer_external_id'] = 'payer:1'
    resp = app.post_json('/api/regie/foo/basket/lines/from-bookings/dry-run/', params=params, status=400)
    params.pop('payer_external_id')
    assert resp.json['err'] == 1
    assert resp.json['err_class'] == "error: PricingError, details: {'foo': 'bar'}"
    assert BasketLine.objects.count() == 0
    assert BasketLineItem.objects.count() == 0

    mock_pricing_data_event.side_effect = [
        {'foo1': 'bar1', 'pricing': 1},
        PricingError(details={'foo': 'bar'}),
        # dry-run
        {'foo1': 'bar1', 'pricing': 1},
        PricingError(details={'foo': 'bar'}),
    ]
    params['cancelled_events'] = [
        {
            'agenda_slug': 'foo',
            'slug': 'event1',
            'primary_event': 'foo@primary',
            'datetime': '2025-06-10 12:00:00',
            'label': 'Event 1',
        },
    ]

    resp = app.post_json(
        '/api/regie/foo/basket/%s/lines/from-bookings/' % basket.uuid, params=params, status=400
    )
    assert resp.json['err'] == 1
    assert resp.json['err_class'] == "error: PricingError, details: {'foo': 'bar'}"
    assert BasketLine.objects.count() == 0
    assert BasketLineItem.objects.count() == 0
    params['payer_external_id'] = 'payer:1'
    resp = app.post_json('/api/regie/foo/basket/lines/from-bookings/dry-run/', params=params, status=400)
    assert resp.json['err'] == 1
    assert resp.json['err_class'] == "error: PricingError, details: {'foo': 'bar'}"
    assert BasketLine.objects.count() == 0
    assert BasketLineItem.objects.count() == 0


@mock.patch('lingo.invoicing.models.Regie.get_payer_data')
@mock.patch('lingo.pricing.models.Pricing.get_pricing_data_for_event')
def test_from_bookings_get_payer_data_error(mock_pricing_data_event, mock_payer_data, app, user):
    app.authorization = ('Basic', ('john.doe', 'password'))
    regie = Regie.objects.create(label='Foo')
    agenda = Agenda.objects.create(label='Foo', regie=regie)
    pricing = Pricing.objects.create(
        date_start=datetime.date(year=2025, month=6, day=1),
        date_end=datetime.date(year=2025, month=7, day=1),
    )
    pricing.agendas.add(agenda)
    invoice = DraftInvoice.objects.create(
        regie=regie,
        date_publication=datetime.date(2023, 4, 21),
        date_payment_deadline=datetime.date(2023, 4, 22),
        date_due=datetime.date(2023, 4, 23),
    )
    basket = Basket.objects.create(regie=regie, draft_invoice=invoice)

    mock_pricing_data_event.return_value = {'foo1': 'bar1', 'pricing': 1, 'accounting_code': '414141'}
    mock_payer_data.side_effect = PayerDataError(details={'key': 'foobar', 'reason': 'foo'})
    params = {
        'user_external_id': 'user:1',
        'user_first_name': 'First',
        'user_last_name': 'Last',
        'information_message': 'foo baz',
        'cancel_information_message': 'foo bar',
        'form_url': 'http://form.com',
        'validation_callback_url': 'http://validation.com',
        'payment_callback_url': 'http://payment.com',
        'credit_callback_url': 'http://credit.com',
        'cancel_callback_url': 'http://cancel.com',
        'expiration_callback_url': 'http://expiration.com',
        'booked_events': [
            {
                'agenda_slug': 'foo',
                'slug': 'event1',
                'primary_event': 'foo@primary',
                'datetime': '2025-06-10 12:00:00',
                'label': 'Event 1',
            },
        ],
        'cancelled_events': [],
    }

    resp = app.post_json(
        '/api/regie/foo/basket/%s/lines/from-bookings/' % basket.uuid, params=params, status=400
    )
    assert resp.json['err'] == 1
    assert resp.json['err_class'] == "error: PayerDataError, details: {'key': 'foobar', 'reason': 'foo'}"
    assert BasketLine.objects.count() == 0
    assert BasketLineItem.objects.count() == 0
    params['payer_external_id'] = 'payer:1'
    resp = app.post_json('/api/regie/foo/basket/lines/from-bookings/dry-run/', params=params, status=400)
    params.pop('payer_external_id')
    assert resp.json['err'] == 1
    assert resp.json['err_class'] == "error: PayerDataError, details: {'key': 'foobar', 'reason': 'foo'}"
    assert BasketLine.objects.count() == 0
    assert BasketLineItem.objects.count() == 0

    params['booked_events'] = []
    params['cancelled_events'] = [
        {
            'agenda_slug': 'foo',
            'slug': 'event1',
            'primary_event': 'foo@primary',
            'datetime': '2025-06-10 12:00:00',
            'label': 'Event 1',
        },
    ]

    resp = app.post_json(
        '/api/regie/foo/basket/%s/lines/from-bookings/' % basket.uuid, params=params, status=400
    )
    assert resp.json['err'] == 1
    assert resp.json['err_class'] == "error: PayerDataError, details: {'key': 'foobar', 'reason': 'foo'}"
    assert BasketLine.objects.count() == 0
    assert BasketLineItem.objects.count() == 0
    params['payer_external_id'] = 'payer:1'
    resp = app.post_json('/api/regie/foo/basket/lines/from-bookings/dry-run/', params=params, status=400)
    assert resp.json['err'] == 1
    assert resp.json['err_class'] == "error: PayerDataError, details: {'key': 'foobar', 'reason': 'foo'}"
    assert BasketLine.objects.count() == 0
    assert BasketLineItem.objects.count() == 0


@mock.patch('lingo.invoicing.models.Regie.get_payer_data')
def test_from_bookings_pricing_dates(mock_payer_data, app, user):
    app.authorization = ('Basic', ('john.doe', 'password'))
    regie = Regie.objects.create(label='Foo')
    agenda = Agenda.objects.create(label='Foo', regie=regie)
    pricing1 = Pricing.objects.create(
        date_start=datetime.date(year=2025, month=9, day=1),
        date_end=datetime.date(year=2025, month=10, day=1),
    )
    pricing1.agendas.add(agenda)
    pricing2 = Pricing.objects.create(
        date_start=datetime.date(year=2025, month=10, day=1),
        date_end=datetime.date(year=2025, month=11, day=1),
        flat_fee_schedule=True,
    )
    pricing2.agendas.add(agenda)
    pricing3 = Pricing.objects.create(
        date_start=datetime.date(year=2025, month=10, day=1),
        date_end=datetime.date(year=2025, month=11, day=1),
    )
    pricing3.agendas.add(agenda)
    invoice = DraftInvoice.objects.create(
        regie=regie,
        date_publication=datetime.date(2023, 4, 21),
        date_payment_deadline=datetime.date(2023, 4, 22),
        date_due=datetime.date(2023, 4, 23),
    )
    basket = Basket.objects.create(regie=regie, draft_invoice=invoice)

    pricing_data_event_patch = mock.patch.object(Pricing, 'get_pricing_data_for_event', autospec=True)
    mock_payer_data.return_value = {
        'first_name': 'First1',
        'last_name': 'Last1',
        'address': '41 rue des kangourous\n99999 Kangourou Ville',
        'email': 'email1',
        'phone': 'phone1',
    }

    params = {
        'user_external_id': 'user:1',
        'user_first_name': 'First',
        'user_last_name': 'Last',
        'information_message': 'foo baz',
        'cancel_information_message': 'foo bar',
        'form_url': 'http://form.com',
        'validation_callback_url': 'http://validation.com',
        'payment_callback_url': 'http://payment.com',
        'credit_callback_url': 'http://credit.com',
        'cancel_callback_url': 'http://cancel.com',
        'expiration_callback_url': 'http://expiration.com',
        'booked_events': [],
        'cancelled_events': [],
    }

    # check agenda pricing of september is used
    for event_date in ['2025-09-01 12:00:00', '2025-09-30 12:00:00']:
        BasketLineItem.objects.all().delete()
        BasketLine.objects.all().delete()
        params.update(
            {
                'booked_events': [
                    {
                        'agenda_slug': 'foo',
                        'slug': 'event1',
                        'primary_event': 'foo@primary',
                        'datetime': event_date,
                        'label': 'Event 1',
                    },
                ]
            }
        )
        with pricing_data_event_patch as mock_pricing_data_event:
            mock_pricing_data_event.return_value = {'foo': 'bar', 'pricing': 42}
            resp = app.post_json('/api/regie/foo/basket/%s/lines/from-bookings/' % basket.uuid, params=params)
            assert resp.json['err'] == 0
            assert BasketLine.objects.count() == 1
            assert BasketLineItem.objects.count() == 1
            assert mock_pricing_data_event.call_args_list[0][0][0] == pricing1
            BasketLineItem.objects.all().delete()
            BasketLine.objects.all().delete()
            params['payer_external_id'] = 'payer:1'
            resp = app.post_json('/api/regie/foo/basket/lines/from-bookings/dry-run/', params=params)
            params.pop('payer_external_id')
            assert resp.json['err'] == 0
            assert mock_pricing_data_event.call_args_list[0][0][0] == pricing1

    # check agenda pricing of october is used
    for event_date in ['2025-10-01 12:00:00', '2025-10-31 12:00:00']:
        BasketLineItem.objects.all().delete()
        BasketLine.objects.all().delete()
        params.update(
            {
                'booked_events': [
                    {
                        'agenda_slug': 'foo',
                        'slug': 'event1',
                        'primary_event': 'foo@primary',
                        'datetime': event_date,
                        'label': 'Event 1',
                    },
                ]
            }
        )
        with pricing_data_event_patch as mock_pricing_data_event:
            mock_pricing_data_event.return_value = {'foo': 'bar', 'pricing': 42}
            resp = app.post_json('/api/regie/foo/basket/%s/lines/from-bookings/' % basket.uuid, params=params)
            assert resp.json['err'] == 0
            assert BasketLine.objects.count() == 1
            assert BasketLineItem.objects.count() == 1
            assert mock_pricing_data_event.call_args_list[0][0][0] == pricing3
            BasketLineItem.objects.all().delete()
            BasketLine.objects.all().delete()
            params['payer_external_id'] = 'payer:1'
            resp = app.post_json('/api/regie/foo/basket/lines/from-bookings/dry-run/', params=params)
            params.pop('payer_external_id')
            assert mock_pricing_data_event.call_args_list[0][0][0] == pricing3

    # no matching agenda pricing
    for event_date in ['2025-08-31 12:00:00', '2025-11-01 12:00:00']:
        BasketLineItem.objects.all().delete()
        BasketLine.objects.all().delete()
        params.update(
            {
                'booked_events': [
                    {
                        'agenda_slug': 'foo',
                        'slug': 'event1',
                        'primary_event': 'foo@primary',
                        'datetime': event_date,
                        'label': 'Event 1',
                    },
                ]
            }
        )
        with pricing_data_event_patch as mock_pricing_data_event:
            mock_pricing_data_event.return_value = {'foo': 'bar', 'pricing': 42}
            resp = app.post_json(
                '/api/regie/foo/basket/%s/lines/from-bookings/' % basket.uuid, params=params, status=400
            )
            assert resp.json['err'] == 1
            assert resp.json['err_class'] == 'error: PricingNotFound, details: {}'
            assert mock_pricing_data_event.call_args_list == []
            params['payer_external_id'] = 'payer:1'
            resp = app.post_json(
                '/api/regie/foo/basket/lines/from-bookings/dry-run/',
                params=params,
                status=400,
            )
            assert resp.json['err'] == 1
            assert resp.json['err_class'] == 'error: PricingNotFound, details: {}'
            assert mock_pricing_data_event.call_args_list == []


@mock.patch('lingo.api.views.utils.get_existing_lines_for_user')
@mock.patch('lingo.invoicing.models.Regie.get_payer_data')
@mock.patch('lingo.pricing.models.Pricing.get_pricing_data_for_event')
def test_from_bookings_current_payer_only(mock_pricing_data_event, mock_payer_data, mock_existing, app, user):
    app.authorization = ('Basic', ('john.doe', 'password'))
    regie = Regie.objects.create(label='Foo')
    agenda1 = Agenda.objects.create(label='Foo', regie=regie)
    agenda2 = Agenda.objects.create(label='Bar', regie=regie)
    pricing = Pricing.objects.create(
        date_start=datetime.date(year=2025, month=6, day=1),
        date_end=datetime.date(year=2025, month=7, day=1),
    )
    pricing.agendas.add(agenda1, agenda2)
    invoice = DraftInvoice.objects.create(
        regie=regie,
        date_publication=datetime.date(2023, 4, 21),
        date_payment_deadline=datetime.date(2023, 4, 22),
        date_due=datetime.date(2023, 4, 23),
    )
    basket = Basket.objects.create(regie=regie, draft_invoice=invoice, payer_external_id='payer:1')
    old_expiry_at = basket.expiry_at

    mock_pricing_data_event.side_effect = [
        {'foo1': 'bar1', 'pricing': 1, 'accounting_code': '414141'},
        {'foo2': 'bar2', 'pricing': 2, 'accounting_code': '424242'},
        # dry-run
        {'foo1': 'bar1', 'pricing': 1, 'accounting_code': '414141'},
        {'foo2': 'bar2', 'pricing': 2, 'accounting_code': '424242'},
    ]
    mock_payer_data.return_value = {
        'first_name': 'First1',
        'last_name': 'Last1',
        'address': '41 rue des kangourous\n99999 Kangourou Ville',
        'email': 'email1',
        'phone': 'phone1',
        'direct_debit': False,
    }

    params = {
        'user_external_id': 'user:1',
        'user_first_name': 'First',
        'user_last_name': 'Last',
        'information_message': 'foo baz',
        'cancel_information_message': 'foo bar',
        'form_url': 'http://form.com',
        'validation_callback_url': 'http://validation.com',
        'payment_callback_url': 'http://payment.com',
        'credit_callback_url': 'http://credit.com',
        'cancel_callback_url': 'http://cancel.com',
        'expiration_callback_url': 'http://expiration.com',
        'booked_events': [],
        'cancelled_events': [],
    }

    # only bookings, result is an invoice
    mock_existing.return_value = {
        'foo@primary': {
            '2025-06-10': [
                Link(
                    payer_external_id='payer:2',
                    unit_amount=11,
                    booked=True,
                    invoicing_element_number='F-0001',
                ),
                Link(
                    payer_external_id='payer:2',
                    unit_amount=11,
                    booked=False,
                    invoicing_element_number='A-0002',
                ),
            ]
        }
    }
    params.update(
        {
            'booked_events': [
                {
                    'agenda_slug': 'foo',
                    'slug': 'event1',
                    'primary_event': 'foo@primary',
                    'datetime': '2025-06-10 15:00:00',
                    'label': 'Event 1',
                    'custom_field_foo': 'bar',
                },
                {
                    'agenda_slug': 'bar',
                    'slug': 'event2',
                    'primary_event': None,
                    'datetime': '2025-06-11 15:00:00',
                    'label': 'Event 2',
                },
            ]
        }
    )
    with CaptureQueriesContext(connection) as ctx:
        resp = app.post_json('/api/regie/foo/basket/%s/lines/from-bookings/' % basket.uuid, params=params)
        assert len(ctx.captured_queries) == 18
    assert mock_pricing_data_event.call_args_list == [
        mock.call(
            request=mock.ANY,
            agenda=agenda1,
            event={
                'agenda': 'foo',
                'slug': 'event1',
                'primary_event': 'primary',
                'start_datetime': '2025-06-10T15:00:00+02:00',
                'label': 'Event 1',
                'custom_field_foo': 'bar',
            },
            check_status={'status': 'presence', 'check_type': None},
            user_external_id='user:1',
            payer_external_id='payer:1',
        ),
        mock.call(
            request=mock.ANY,
            agenda=agenda2,
            event={
                'agenda': 'bar',
                'slug': 'event2',
                'primary_event': None,
                'start_datetime': '2025-06-11T15:00:00+02:00',
                'label': 'Event 2',
            },
            check_status={'status': 'presence', 'check_type': None},
            user_external_id='user:1',
            payer_external_id='payer:1',
        ),
    ]
    assert mock_existing.call_args_list == [
        mock.call(
            regie=regie,
            date_min=datetime.date(2025, 6, 10),
            date_max=datetime.date(2025, 6, 12),
            user_external_id='user:1',
            serialized_events=[
                {
                    'agenda': 'foo',
                    'slug': 'event1',
                    'primary_event': 'primary',
                    'start_datetime': '2025-06-10T15:00:00+02:00',
                    'label': 'Event 1',
                    'custom_field_foo': 'bar',
                },
                {
                    'agenda': 'bar',
                    'slug': 'event2',
                    'primary_event': None,
                    'start_datetime': '2025-06-11T15:00:00+02:00',
                    'label': 'Event 2',
                },
            ],
        )
    ]
    assert resp.json['err'] == 0
    basket.refresh_from_db()
    assert basket.expiry_at > old_expiry_at
    invoice.refresh_from_db()
    basket_line = basket.basketline_set.get()
    assert resp.json['data'] == {
        'line_id': str(basket_line.uuid),
        'closed': True,
        'line_total_amount': 3,
        'basket_total_amount': 3,
    }
    assert invoice.total_amount == 3
    assert invoice.lines.count() == 2
    lines = invoice.lines.order_by('pk')
    assert lines[0].event_date == datetime.date(2025, 6, 10)
    assert lines[0].label == 'Event 1'
    assert lines[0].quantity == 1
    assert lines[0].unit_amount == 1
    assert lines[0].details == {
        'dates': ['2025-06-10'],
    }
    assert lines[0].event_slug == 'foo@primary'
    assert lines[0].event_label == 'Event 1'
    assert lines[0].agenda_slug == 'foo'
    assert lines[0].activity_label == 'Foo'
    assert lines[0].description == 'Booking 10/06'
    assert lines[0].accounting_code == '414141'
    assert lines[0].user_external_id == 'user:1'
    assert lines[0].user_first_name == 'First'
    assert lines[0].user_last_name == 'Last'
    assert lines[0].pool is None
    assert lines[0].form_url == 'http://form.com'
    assert lines[1].event_date == datetime.date(2025, 6, 11)
    assert lines[1].label == 'Event 2'
    assert lines[1].quantity == 1
    assert lines[1].unit_amount == 2
    assert lines[1].details == {
        'dates': ['2025-06-11'],
    }
    assert lines[1].event_slug == 'bar@event2'
    assert lines[1].event_label == 'Event 2'
    assert lines[1].agenda_slug == 'bar'
    assert lines[1].activity_label == 'Bar'
    assert lines[1].description == 'Booking 11/06'
    assert lines[1].accounting_code == '424242'
    assert lines[1].user_external_id == 'user:1'
    assert lines[1].user_first_name == 'First'
    assert lines[1].user_last_name == 'Last'
    assert lines[1].pool is None
    assert lines[1].form_url == 'http://form.com'
    assert basket_line.basket == basket
    assert basket_line.user_external_id == 'user:1'
    assert basket_line.user_first_name == 'First'
    assert basket_line.user_last_name == 'Last'
    assert basket_line.information_message == 'foo baz'
    assert basket_line.cancel_information_message == 'foo bar'
    assert basket_line.group_items is False
    assert basket_line.form_url == 'http://form.com'
    assert basket_line.validation_callback_url == 'http://validation.com'
    assert basket_line.payment_callback_url == 'http://payment.com'
    assert basket_line.credit_callback_url == 'http://credit.com'
    assert basket_line.cancel_callback_url == 'http://cancel.com'
    assert basket_line.expiration_callback_url == 'http://expiration.com'
    assert basket_line.closed is True
    assert basket_line.items.count() == 2
    items = basket_line.items.order_by('pk')
    assert items[0].line == basket_line
    assert items[0].event_date == datetime.date(2025, 6, 10)
    assert items[0].label == 'Event 1'
    assert items[0].subject == 'Booking 10/06'
    assert items[0].details == ''
    assert items[0].quantity == 1
    assert items[0].unit_amount == 1
    assert items[0].event_slug == 'foo@primary'
    assert items[0].event_label == 'Event 1'
    assert items[0].agenda_slug == 'foo'
    assert items[0].activity_label == 'Foo'
    assert items[0].accounting_code == '414141'
    assert items[1].line == basket_line
    assert items[1].event_date == datetime.date(2025, 6, 11)
    assert items[1].label == 'Event 2'
    assert items[1].subject == 'Booking 11/06'
    assert items[1].details == ''
    assert items[1].quantity == 1
    assert items[1].unit_amount == 2
    assert items[1].event_slug == 'bar@event2'
    assert items[1].event_label == 'Event 2'
    assert items[1].agenda_slug == 'bar'
    assert items[1].activity_label == 'Bar'
    assert items[1].accounting_code == '424242'
    basket_line.items.all().delete()
    basket_line.delete()
    DraftInvoiceLine.objects.all().delete()
    mock_pricing_data_event.reset_mock()
    mock_existing.reset_mock()
    params['payer_external_id'] = 'payer:1'
    with CaptureQueriesContext(connection) as ctx:
        resp = app.post_json('/api/regie/foo/basket/lines/from-bookings/dry-run/', params=params)
        assert len(ctx.captured_queries) == 7
    params.pop('payer_external_id')
    assert mock_pricing_data_event.call_args_list == [
        mock.call(
            request=mock.ANY,
            agenda=agenda1,
            event={
                'agenda': 'foo',
                'slug': 'event1',
                'primary_event': 'primary',
                'start_datetime': '2025-06-10T15:00:00+02:00',
                'label': 'Event 1',
                'custom_field_foo': 'bar',
            },
            check_status={'status': 'presence', 'check_type': None},
            user_external_id='user:1',
            payer_external_id='payer:1',
        ),
        mock.call(
            request=mock.ANY,
            agenda=agenda2,
            event={
                'agenda': 'bar',
                'slug': 'event2',
                'primary_event': None,
                'start_datetime': '2025-06-11T15:00:00+02:00',
                'label': 'Event 2',
            },
            check_status={'status': 'presence', 'check_type': None},
            user_external_id='user:1',
            payer_external_id='payer:1',
        ),
    ]
    assert mock_existing.call_args_list == [
        mock.call(
            regie=regie,
            date_min=datetime.date(2025, 6, 10),
            date_max=datetime.date(2025, 6, 12),
            user_external_id='user:1',
            serialized_events=[
                {
                    'agenda': 'foo',
                    'slug': 'event1',
                    'primary_event': 'primary',
                    'start_datetime': '2025-06-10T15:00:00+02:00',
                    'label': 'Event 1',
                    'custom_field_foo': 'bar',
                },
                {
                    'agenda': 'bar',
                    'slug': 'event2',
                    'primary_event': None,
                    'start_datetime': '2025-06-11T15:00:00+02:00',
                    'label': 'Event 2',
                },
            ],
        )
    ]
    assert resp.json['err'] == 0
    assert resp.json['data'] == {
        'basket': {
            'total_amount': 3,
            'lines': [
                {
                    'label': 'Event 1',
                    'description': 'Booking 10/06',
                    'unit_amount': 1,
                    'quantity': 1,
                    'total_amount': 1,
                },
                {
                    'label': 'Event 2',
                    'description': 'Booking 11/06',
                    'unit_amount': 2,
                    'quantity': 1,
                    'total_amount': 2,
                },
            ],
            'payer_external_id': 'payer:1',
            'payer_name': 'First1 Last1',
        },
    }

    # with cancellations but amount > 0
    mock_pricing_data_event.reset_mock()
    mock_pricing_data_event.side_effect = [
        {'foo2': 'bar2', 'pricing': 2, 'accounting_code': '424242'},
        {'foo1': 'bar1', 'pricing': 3, 'accounting_code': '414141'},
        # dry-run
        {'foo2': 'bar2', 'pricing': 2, 'accounting_code': '424242'},
        {'foo1': 'bar1', 'pricing': 3, 'accounting_code': '414141'},
    ]
    mock_existing.return_value = {
        'bar@event2': {
            '2025-06-11': [
                Link(
                    payer_external_id='payer:2',
                    unit_amount=11,
                    booked=True,
                    invoicing_element_number='F-0001',
                ),
                Link(
                    payer_external_id='payer:2',
                    unit_amount=11,
                    booked=False,
                    invoicing_element_number='A-0002',
                ),
                Link(
                    payer_external_id='payer:1',
                    unit_amount=1,
                    booked=True,
                    invoicing_element_number='F-0002',
                ),
            ]
        }
    }
    params.update(
        {
            'booked_events': [
                {
                    'agenda_slug': 'foo',
                    'slug': 'event1',
                    'primary_event': 'foo@primary',
                    'datetime': '2025-06-10 15:00:00',
                    'label': 'Event 1',
                },
            ],
            'cancelled_events': [
                {
                    'agenda_slug': 'bar',
                    'slug': 'event2',
                    'primary_event': None,
                    'datetime': '2025-06-11 15:00:00',
                    'label': 'Event 2',
                    'custom_field_foo': 'bar',
                },
            ],
        }
    )
    resp = app.post_json('/api/regie/foo/basket/%s/lines/from-bookings/' % basket.uuid, params=params)
    assert mock_pricing_data_event.call_args_list == [
        mock.call(
            request=mock.ANY,
            agenda=agenda1,
            event={
                'agenda': 'foo',
                'slug': 'event1',
                'primary_event': 'primary',
                'start_datetime': '2025-06-10T15:00:00+02:00',
                'label': 'Event 1',
            },
            check_status={'status': 'presence', 'check_type': None},
            user_external_id='user:1',
            payer_external_id='payer:1',
        ),
        mock.call(
            request=mock.ANY,
            agenda=agenda2,
            event={
                'agenda': 'bar',
                'slug': 'event2',
                'primary_event': None,
                'start_datetime': '2025-06-11T15:00:00+02:00',
                'label': 'Event 2',
                'custom_field_foo': 'bar',
            },
            check_status={'status': 'presence', 'check_type': None},
            user_external_id='user:1',
            payer_external_id='payer:1',
        ),
    ]
    assert resp.json['err'] == 0
    invoice.refresh_from_db()
    basket_line = basket.basketline_set.get()
    assert resp.json['data'] == {
        'line_id': str(basket_line.uuid),
        'closed': True,
        'line_total_amount': 1,
        'basket_total_amount': 1,
    }
    assert invoice.total_amount == 1
    assert invoice.lines.count() == 2
    lines = invoice.lines.order_by('pk')
    assert lines[0].event_date == datetime.date(2025, 6, 10)
    assert lines[0].label == 'Event 1'
    assert lines[0].quantity == 1
    assert lines[0].unit_amount == 2
    assert lines[0].details == {
        'dates': ['2025-06-10'],
    }
    assert lines[0].event_slug == 'foo@primary'
    assert lines[0].event_label == 'Event 1'
    assert lines[0].agenda_slug == 'foo'
    assert lines[0].activity_label == 'Foo'
    assert lines[0].description == 'Booking 10/06'
    assert lines[0].accounting_code == '424242'
    assert lines[0].user_external_id == 'user:1'
    assert lines[0].user_first_name == 'First'
    assert lines[0].user_last_name == 'Last'
    assert lines[0].pool is None
    assert lines[0].form_url == 'http://form.com'
    assert lines[1].event_date == datetime.date(2025, 6, 11)
    assert lines[1].label == 'Event 2'
    assert lines[1].quantity == -1
    assert lines[1].unit_amount == 1
    assert lines[1].details == {
        'dates': ['2025-06-11'],
    }
    assert lines[1].event_slug == 'bar@event2'
    assert lines[1].event_label == 'Event 2'
    assert lines[1].agenda_slug == 'bar'
    assert lines[1].activity_label == 'Bar'
    assert lines[1].description == 'Cancellation 11/06'
    assert lines[1].accounting_code == '414141'
    assert lines[1].user_external_id == 'user:1'
    assert lines[1].user_first_name == 'First'
    assert lines[1].user_last_name == 'Last'
    assert lines[1].pool is None
    assert lines[1].form_url == 'http://form.com'
    basket_line.items.all().delete()
    basket_line.delete()
    DraftInvoiceLine.objects.all().delete()
    params['payer_external_id'] = 'payer:1'
    resp = app.post_json('/api/regie/foo/basket/lines/from-bookings/dry-run/', params=params)
    params.pop('payer_external_id')
    assert resp.json['err'] == 0
    assert resp.json['data'] == {
        'basket': {
            'total_amount': 1,
            'lines': [
                {
                    'label': 'Event 1',
                    'description': 'Booking 10/06',
                    'unit_amount': 2,
                    'quantity': 1,
                    'total_amount': 2,
                },
                {
                    'label': 'Event 2',
                    'description': 'Cancellation 11/06',
                    'unit_amount': 1,
                    'quantity': -1,
                    'total_amount': -1,
                },
            ],
            'payer_external_id': 'payer:1',
            'payer_name': 'First1 Last1',
        }
    }

    # with cancellations and 0 amount
    mock_pricing_data_event.reset_mock()
    mock_pricing_data_event.side_effect = None
    mock_pricing_data_event.return_value = {'foo1': 'bar1', 'pricing': 1, 'accounting_code': '414141'}
    mock_existing.return_value = {
        'bar@event2': {
            '2025-06-11': [
                Link(
                    payer_external_id='payer:1',
                    unit_amount=1,
                    booked=True,
                    invoicing_element_number='F-0001',
                ),
            ]
        }
    }
    params.update(
        {
            'booked_events': [
                {
                    'agenda_slug': 'foo',
                    'slug': 'event1',
                    'primary_event': 'foo@primary',
                    'datetime': '2025-06-10 15:00:00',
                    'label': 'Event 1',
                },
            ],
            'cancelled_events': [
                {
                    'agenda_slug': 'bar',
                    'slug': 'event2',
                    'primary_event': None,
                    'datetime': '2025-06-11 15:00:00',
                    'label': 'Event 2',
                },
            ],
        }
    )
    resp = app.post_json('/api/regie/foo/basket/%s/lines/from-bookings/' % basket.uuid, params=params)
    assert mock_pricing_data_event.call_args_list == [
        mock.call(
            request=mock.ANY,
            agenda=agenda1,
            event={
                'agenda': 'foo',
                'slug': 'event1',
                'primary_event': 'primary',
                'start_datetime': '2025-06-10T15:00:00+02:00',
                'label': 'Event 1',
            },
            check_status={'status': 'presence', 'check_type': None},
            user_external_id='user:1',
            payer_external_id='payer:1',
        ),
        mock.call(
            request=mock.ANY,
            agenda=agenda2,
            event={
                'agenda': 'bar',
                'slug': 'event2',
                'primary_event': None,
                'start_datetime': '2025-06-11T15:00:00+02:00',
                'label': 'Event 2',
            },
            check_status={'status': 'presence', 'check_type': None},
            user_external_id='user:1',
            payer_external_id='payer:1',
        ),
    ]
    assert resp.json['err'] == 0
    invoice.refresh_from_db()
    basket_line = basket.basketline_set.get()
    assert resp.json['data'] == {
        'line_id': str(basket_line.uuid),
        'closed': True,
        'line_total_amount': 0,
        'basket_total_amount': 0,
    }
    assert invoice.total_amount == 0
    assert invoice.lines.count() == 2
    lines = invoice.lines.order_by('pk')
    assert lines[0].event_date == datetime.date(2025, 6, 10)
    assert lines[0].label == 'Event 1'
    assert lines[0].quantity == 1
    assert lines[0].unit_amount == 1
    assert lines[0].details == {
        'dates': ['2025-06-10'],
    }
    assert lines[0].event_slug == 'foo@primary'
    assert lines[0].event_label == 'Event 1'
    assert lines[0].agenda_slug == 'foo'
    assert lines[0].activity_label == 'Foo'
    assert lines[0].description == 'Booking 10/06'
    assert lines[0].accounting_code == '414141'
    assert lines[0].user_external_id == 'user:1'
    assert lines[0].user_first_name == 'First'
    assert lines[0].user_last_name == 'Last'
    assert lines[0].pool is None
    assert lines[0].form_url == 'http://form.com'
    assert lines[1].event_date == datetime.date(2025, 6, 11)
    assert lines[1].label == 'Event 2'
    assert lines[1].quantity == -1
    assert lines[1].unit_amount == 1
    assert lines[1].details == {
        'dates': ['2025-06-11'],
    }
    assert lines[1].event_slug == 'bar@event2'
    assert lines[1].event_label == 'Event 2'
    assert lines[1].agenda_slug == 'bar'
    assert lines[1].activity_label == 'Bar'
    assert lines[1].description == 'Cancellation 11/06'
    assert lines[1].accounting_code == '414141'
    assert lines[1].user_external_id == 'user:1'
    assert lines[1].user_first_name == 'First'
    assert lines[1].user_last_name == 'Last'
    assert lines[1].pool is None
    assert lines[1].form_url == 'http://form.com'
    basket_line.items.all().delete()
    basket_line.delete()
    DraftInvoiceLine.objects.all().delete()
    params['payer_external_id'] = 'payer:1'
    resp = app.post_json('/api/regie/foo/basket/lines/from-bookings/dry-run/', params=params)
    params.pop('payer_external_id')
    assert resp.json['err'] == 0
    assert resp.json['data'] == {
        'basket': {
            'total_amount': 0,
            'lines': [
                {
                    'label': 'Event 1',
                    'description': 'Booking 10/06',
                    'unit_amount': 1,
                    'quantity': 1,
                    'total_amount': 1,
                },
                {
                    'label': 'Event 2',
                    'description': 'Cancellation 11/06',
                    'unit_amount': 1,
                    'quantity': -1,
                    'total_amount': -1,
                },
            ],
            'payer_external_id': 'payer:1',
            'payer_name': 'First1 Last1',
        }
    }

    # only cancellations
    mock_pricing_data_event.reset_mock()
    mock_pricing_data_event.side_effect = [
        {'foo1': 'bar1', 'pricing': 3, 'accounting_code': '414141'},
        {'foo2': 'bar2', 'pricing': 4, 'accounting_code': '424242'},
        # dry-run
        {'foo1': 'bar1', 'pricing': 3, 'accounting_code': '414141'},
        {'foo2': 'bar2', 'pricing': 4, 'accounting_code': '424242'},
    ]
    mock_existing.return_value = {
        'foo@primary': {
            '2025-06-10': [
                Link(
                    payer_external_id='payer:1',
                    unit_amount=1,
                    booked=True,
                    invoicing_element_number='F-0001',
                ),
            ]
        },
        'bar@event2': {
            '2025-06-11': [
                Link(
                    payer_external_id='payer:1',
                    unit_amount=2,
                    booked=True,
                    invoicing_element_number='F-0002',
                ),
            ]
        },
    }
    params.update(
        {
            'booked_events': [],
            'cancelled_events': [
                {
                    'agenda_slug': 'foo',
                    'slug': 'event1',
                    'primary_event': 'foo@primary',
                    'datetime': '2025-06-10 15:00:00',
                    'label': 'Event 1',
                },
                {
                    'agenda_slug': 'bar',
                    'slug': 'event2',
                    'primary_event': None,
                    'datetime': '2025-06-11 15:00:00',
                    'label': 'Event 2',
                },
            ],
        }
    )
    resp = app.post_json('/api/regie/foo/basket/%s/lines/from-bookings/' % basket.uuid, params=params)
    assert mock_pricing_data_event.call_args_list == [
        mock.call(
            request=mock.ANY,
            agenda=agenda1,
            event={
                'agenda': 'foo',
                'slug': 'event1',
                'primary_event': 'primary',
                'start_datetime': '2025-06-10T15:00:00+02:00',
                'label': 'Event 1',
            },
            check_status={'status': 'presence', 'check_type': None},
            user_external_id='user:1',
            payer_external_id='payer:1',
        ),
        mock.call(
            request=mock.ANY,
            agenda=agenda2,
            event={
                'agenda': 'bar',
                'slug': 'event2',
                'primary_event': None,
                'start_datetime': '2025-06-11T15:00:00+02:00',
                'label': 'Event 2',
            },
            check_status={'status': 'presence', 'check_type': None},
            user_external_id='user:1',
            payer_external_id='payer:1',
        ),
    ]
    assert resp.json['err'] == 0
    invoice.refresh_from_db()
    basket_line = basket.basketline_set.get()
    assert resp.json['data'] == {
        'line_id': str(basket_line.uuid),
        'closed': True,
        'line_total_amount': -3,
        'basket_total_amount': -3,
    }
    assert invoice.total_amount == -3
    assert invoice.lines.count() == 2
    lines = invoice.lines.order_by('pk')
    assert lines[0].event_date == datetime.date(2025, 6, 10)
    assert lines[0].label == 'Event 1'
    assert lines[0].quantity == -1
    assert lines[0].unit_amount == 1
    assert lines[0].details == {
        'dates': ['2025-06-10'],
    }
    assert lines[0].event_slug == 'foo@primary'
    assert lines[0].event_label == 'Event 1'
    assert lines[0].agenda_slug == 'foo'
    assert lines[0].activity_label == 'Foo'
    assert lines[0].description == 'Cancellation 10/06'
    assert lines[0].accounting_code == '414141'
    assert lines[0].user_external_id == 'user:1'
    assert lines[0].user_first_name == 'First'
    assert lines[0].user_last_name == 'Last'
    assert lines[0].pool is None
    assert lines[0].form_url == 'http://form.com'
    assert lines[1].event_date == datetime.date(2025, 6, 11)
    assert lines[1].label == 'Event 2'
    assert lines[1].quantity == -1
    assert lines[1].unit_amount == 2
    assert lines[1].details == {
        'dates': ['2025-06-11'],
    }
    assert lines[1].event_slug == 'bar@event2'
    assert lines[1].event_label == 'Event 2'
    assert lines[1].agenda_slug == 'bar'
    assert lines[1].activity_label == 'Bar'
    assert lines[1].description == 'Cancellation 11/06'
    assert lines[1].accounting_code == '424242'
    assert lines[1].user_external_id == 'user:1'
    assert lines[1].user_first_name == 'First'
    assert lines[1].user_last_name == 'Last'
    assert lines[1].pool is None
    assert lines[1].form_url == 'http://form.com'
    basket_line.items.all().delete()
    basket_line.delete()
    DraftInvoiceLine.objects.all().delete()
    params['payer_external_id'] = 'payer:1'
    resp = app.post_json('/api/regie/foo/basket/lines/from-bookings/dry-run/', params=params)
    params.pop('payer_external_id')
    assert resp.json['err'] == 0
    assert resp.json['data'] == {
        'basket': {
            'total_amount': -3,
            'lines': [
                {
                    'label': 'Event 1',
                    'description': 'Cancellation 10/06',
                    'unit_amount': 1,
                    'quantity': -1,
                    'total_amount': -1,
                },
                {
                    'label': 'Event 2',
                    'description': 'Cancellation 11/06',
                    'unit_amount': 2,
                    'quantity': -1,
                    'total_amount': -2,
                },
            ],
            'payer_external_id': 'payer:1',
            'payer_name': 'First1 Last1',
        }
    }

    # bookings and cancellations
    mock_pricing_data_event.reset_mock()
    mock_pricing_data_event.side_effect = [
        {'foo1': 'bar1', 'pricing': 1, 'accounting_code': '414141'},
        {'foo2': 'bar2', 'pricing': 3, 'accounting_code': '424242'},
        # dry-run
        {'foo1': 'bar1', 'pricing': 1, 'accounting_code': '414141'},
        {'foo2': 'bar2', 'pricing': 3, 'accounting_code': '424242'},
    ]
    mock_existing.return_value = {
        'bar@event2': {
            '2025-06-11': [
                Link(
                    payer_external_id='payer:1',
                    unit_amount=2,
                    booked=True,
                    invoicing_element_number='F-0001',
                ),
            ]
        }
    }
    params.update(
        {
            'booked_events': [
                {
                    'agenda_slug': 'foo',
                    'slug': 'event1',
                    'primary_event': 'foo@primary',
                    'datetime': '2025-06-10 15:00:00',
                    'label': 'Event 1',
                },
            ],
            'cancelled_events': [
                {
                    'agenda_slug': 'bar',
                    'slug': 'event2',
                    'primary_event': None,
                    'datetime': '2025-06-11 15:00:00',
                    'label': 'Event 2',
                },
            ],
        }
    )
    resp = app.post_json('/api/regie/foo/basket/%s/lines/from-bookings/' % basket.uuid, params=params)
    assert mock_pricing_data_event.call_args_list == [
        mock.call(
            request=mock.ANY,
            agenda=agenda1,
            event={
                'agenda': 'foo',
                'slug': 'event1',
                'primary_event': 'primary',
                'start_datetime': '2025-06-10T15:00:00+02:00',
                'label': 'Event 1',
            },
            check_status={'status': 'presence', 'check_type': None},
            user_external_id='user:1',
            payer_external_id='payer:1',
        ),
        mock.call(
            request=mock.ANY,
            agenda=agenda2,
            event={
                'agenda': 'bar',
                'slug': 'event2',
                'primary_event': None,
                'start_datetime': '2025-06-11T15:00:00+02:00',
                'label': 'Event 2',
            },
            check_status={'status': 'presence', 'check_type': None},
            user_external_id='user:1',
            payer_external_id='payer:1',
        ),
    ]
    assert resp.json['err'] == 0
    invoice.refresh_from_db()
    basket_line = basket.basketline_set.get()
    assert resp.json['data'] == {
        'line_id': str(basket_line.uuid),
        'closed': True,
        'line_total_amount': -1,
        'basket_total_amount': -1,
    }
    assert invoice.total_amount == -1
    assert invoice.lines.count() == 2
    lines = invoice.lines.order_by('pk')
    assert lines[0].event_date == datetime.date(2025, 6, 10)
    assert lines[0].label == 'Event 1'
    assert lines[0].quantity == 1
    assert lines[0].unit_amount == 1
    assert lines[0].details == {
        'dates': ['2025-06-10'],
    }
    assert lines[0].event_slug == 'foo@primary'
    assert lines[0].event_label == 'Event 1'
    assert lines[0].agenda_slug == 'foo'
    assert lines[0].activity_label == 'Foo'
    assert lines[0].description == 'Booking 10/06'
    assert lines[0].accounting_code == '414141'
    assert lines[0].user_external_id == 'user:1'
    assert lines[0].user_first_name == 'First'
    assert lines[0].user_last_name == 'Last'
    assert lines[0].pool is None
    assert lines[0].form_url == 'http://form.com'
    assert lines[1].event_date == datetime.date(2025, 6, 11)
    assert lines[1].label == 'Event 2'
    assert lines[1].quantity == -1
    assert lines[1].unit_amount == 2
    assert lines[1].details == {
        'dates': ['2025-06-11'],
    }
    assert lines[1].event_slug == 'bar@event2'
    assert lines[1].event_label == 'Event 2'
    assert lines[1].agenda_slug == 'bar'
    assert lines[1].activity_label == 'Bar'
    assert lines[1].description == 'Cancellation 11/06'
    assert lines[1].accounting_code == '424242'
    assert lines[1].user_external_id == 'user:1'
    assert lines[1].user_first_name == 'First'
    assert lines[1].user_last_name == 'Last'
    assert lines[1].pool is None
    assert lines[1].form_url == 'http://form.com'
    basket_line.items.all().delete()
    basket_line.delete()
    DraftInvoiceLine.objects.all().delete()
    params['payer_external_id'] = 'payer:1'
    resp = app.post_json('/api/regie/foo/basket/lines/from-bookings/dry-run/', params=params)
    params.pop('payer_external_id')
    assert resp.json['err'] == 0
    assert resp.json['data'] == {
        'basket': {
            'total_amount': -1,
            'lines': [
                {
                    'label': 'Event 1',
                    'description': 'Booking 10/06',
                    'unit_amount': 1,
                    'quantity': 1,
                    'total_amount': 1,
                },
                {
                    'label': 'Event 2',
                    'description': 'Cancellation 11/06',
                    'unit_amount': 2,
                    'quantity': -1,
                    'total_amount': -2,
                },
            ],
            'payer_external_id': 'payer:1',
            'payer_name': 'First1 Last1',
        }
    }


@mock.patch('lingo.api.views.utils.get_existing_lines_for_user')
@mock.patch('lingo.invoicing.models.Regie.get_payer_data')
@mock.patch('lingo.pricing.models.Pricing.get_pricing_data_for_event')
def test_from_bookings_aggregate_lines(mock_pricing_data_event, mock_payer_data, mock_existing, app, user):
    app.authorization = ('Basic', ('john.doe', 'password'))
    regie = Regie.objects.create(label='Foo')
    agenda1 = Agenda.objects.create(label='Foo', regie=regie)
    agenda2 = Agenda.objects.create(label='Bar', regie=regie)
    pricing = Pricing.objects.create(
        date_start=datetime.date(year=2025, month=6, day=1),
        date_end=datetime.date(year=2025, month=7, day=1),
    )
    pricing.agendas.add(agenda1, agenda2)
    invoice = DraftInvoice.objects.create(
        regie=regie,
        date_publication=datetime.date(2023, 4, 21),
        date_payment_deadline=datetime.date(2023, 4, 22),
        date_due=datetime.date(2023, 4, 23),
    )
    basket = Basket.objects.create(regie=regie, draft_invoice=invoice, payer_external_id='payer:1')

    mock_pricing_data_event.return_value = {'foo1': 'bar1', 'pricing': 1, 'accounting_code': '414141'}
    mock_payer_data.return_value = {
        'first_name': 'First1',
        'last_name': 'Last1',
        'address': '41 rue des kangourous\n99999 Kangourou Ville',
        'email': 'email1',
        'phone': 'phone1',
        'direct_debit': False,
    }

    params = {
        'user_external_id': 'user:1',
        'user_first_name': 'First',
        'user_last_name': 'Last',
        'information_message': 'foo baz',
        'cancel_information_message': 'foo bar',
        'form_url': 'http://form.com',
        'validation_callback_url': 'http://validation.com',
        'payment_callback_url': 'http://payment.com',
        'credit_callback_url': 'http://credit.com',
        'cancel_callback_url': 'http://cancel.com',
        'expiration_callback_url': 'http://expiration.com',
        'booked_events': [],
        'cancelled_events': [],
    }

    # test aggregation
    event = {
        'agenda_slug': 'foo',
        'slug': 'event1',
        'primary_event': None,
        'label': 'Event 1',
    }
    for booked in [True, False]:
        if not booked:
            mock_existing.return_value = {
                key: {
                    '2025-06-10': [
                        Link(
                            payer_external_id='payer:1',
                            unit_amount=1,
                            booked=True,
                            invoicing_element_number='F-0001',
                        ),
                    ],
                    '2025-06-11': [
                        Link(
                            payer_external_id='payer:1',
                            unit_amount=1,
                            booked=True,
                            invoicing_element_number='F-0001',
                        ),
                    ],
                }
                for key in ['foo@event1', 'bar@event1', 'foo@event2']
            }
        else:
            mock_existing.return_value = {}
        key = 'booked_events' if booked else 'cancelled_events'
        values = [
            {'agenda_slug': 'bar'},  # other agenda
            {'slug': 'event2'},  # other event
            {'primary_event': 'bar@event1', 'agenda_slug': 'bar'},  # other agenda
            {'primary_event': 'foo@event2'},  # other event
        ]
        for new_values in values:
            new_params = copy.deepcopy(params)
            new_event = copy.deepcopy(event)
            new_event['datetime'] = '2025-06-10 15:00:00'
            new_params[key].append(new_event)
            new_event = copy.deepcopy(event)
            new_event['datetime'] = '2025-06-11 15:00:00'
            new_event.update(new_values)
            new_params[key].append(new_event)
            resp = app.post_json(
                '/api/regie/foo/basket/%s/lines/from-bookings/' % basket.uuid, params=new_params
            )
            assert resp.json['err'] == 0
            invoice.refresh_from_db()
            basket_line = basket.basketline_set.get()
            assert resp.json['data'] == {
                'line_id': str(basket_line.uuid),
                'closed': True,
                'line_total_amount': 2 if booked else -2,
                'basket_total_amount': 2 if booked else -2,
            }
            if booked:
                assert invoice.total_amount == 2
            else:
                assert invoice.total_amount == -2
            assert invoice.lines.count() == 2
            basket_line.items.all().delete()
            basket_line.delete()
            DraftInvoiceLine.objects.all().delete()

            new_params['payer_external_id'] = 'payer:1'
            resp = app.post_json('/api/regie/foo/basket/lines/from-bookings/dry-run/', params=new_params)
            new_params.pop('payer_external_id')
            assert resp.json['err'] == 0
            if booked:
                assert resp.json['data']['basket']['total_amount'] == 2
            else:
                assert resp.json['data']['basket']['total_amount'] == -2
            assert len(resp.json['data']['basket']['lines']) == 2

    # other pricing
    for booked in [True, False]:
        if not booked:
            mock_existing.return_value = {
                'foo@event1': {
                    '2025-06-10': [
                        Link(
                            payer_external_id='payer:1',
                            unit_amount=1,
                            booked=True,
                            invoicing_element_number='F-0001',
                        ),
                    ],
                    '2025-06-11': [
                        Link(
                            payer_external_id='payer:1',
                            unit_amount=2,
                            booked=True,
                            invoicing_element_number='F-0002',
                        ),
                    ],
                }
            }
        else:
            mock_existing.return_value = {}
        key = 'booked_events' if booked else 'cancelled_events'
        new_params = copy.deepcopy(params)
        new_event = copy.deepcopy(event)
        new_event['datetime'] = '2025-06-10 15:00:00'
        new_params[key].append(new_event)
        new_event = copy.deepcopy(event)
        new_event['datetime'] = '2025-06-11 15:00:00'
        new_params[key].append(new_event)
        mock_pricing_data_event.side_effect = [
            {'foo1': 'bar1', 'pricing': 1, 'accounting_code': '414141'},
            {'foo1': 'bar1', 'pricing': 2, 'accounting_code': '414141'},
            # dry-run
            {'foo1': 'bar1', 'pricing': 1, 'accounting_code': '414141'},
            {'foo1': 'bar1', 'pricing': 2, 'accounting_code': '414141'},
        ]
        resp = app.post_json('/api/regie/foo/basket/%s/lines/from-bookings/' % basket.uuid, params=new_params)
        assert resp.json['err'] == 0
        invoice.refresh_from_db()
        basket_line = basket.basketline_set.get()
        assert resp.json['data'] == {
            'line_id': str(basket_line.uuid),
            'closed': True,
            'line_total_amount': 3 if booked else -3,
            'basket_total_amount': 3 if booked else -3,
        }
        if booked:
            assert invoice.total_amount == 3
        else:
            assert invoice.total_amount == -3
        assert invoice.lines.count() == 2
        basket_line.items.all().delete()
        basket_line.delete()
        DraftInvoiceLine.objects.all().delete()

        new_params['payer_external_id'] = 'payer:1'
        resp = app.post_json('/api/regie/foo/basket/lines/from-bookings/dry-run/', params=new_params)
        new_params.pop('payer_external_id')
        assert resp.json['err'] == 0
        if booked:
            assert resp.json['data']['basket']['total_amount'] == 3
        else:
            assert resp.json['data']['basket']['total_amount'] == -3
        assert len(resp.json['data']['basket']['lines']) == 2

    # other accounting_code
    for booked in [True, False]:
        if not booked:
            mock_existing.return_value = {
                'foo@event1': {
                    '2025-06-10': [
                        Link(
                            payer_external_id='payer:1',
                            unit_amount=1,
                            booked=True,
                            invoicing_element_number='F-0001',
                        ),
                    ],
                    '2025-06-11': [
                        Link(
                            payer_external_id='payer:1',
                            unit_amount=1,
                            booked=True,
                            invoicing_element_number='F-0002',
                        ),
                    ],
                }
            }
        else:
            mock_existing.return_value = {}
        key = 'booked_events' if booked else 'cancelled_events'
        new_params = copy.deepcopy(params)
        new_event = copy.deepcopy(event)
        new_event['datetime'] = '2025-06-10 15:00:00'
        new_params[key].append(new_event)
        new_event = copy.deepcopy(event)
        new_event['datetime'] = '2025-06-11 15:00:00'
        new_params[key].append(new_event)
        mock_pricing_data_event.side_effect = [
            {'foo1': 'bar1', 'pricing': 1, 'accounting_code': '414141'},
            {'foo1': 'bar1', 'pricing': 1, 'accounting_code': '414142'},
            # dry-run
            {'foo1': 'bar1', 'pricing': 1, 'accounting_code': '414141'},
            {'foo1': 'bar1', 'pricing': 1, 'accounting_code': '414142'},
        ]
        resp = app.post_json('/api/regie/foo/basket/%s/lines/from-bookings/' % basket.uuid, params=new_params)
        assert resp.json['err'] == 0
        invoice.refresh_from_db()
        basket_line = basket.basketline_set.get()
        assert resp.json['data'] == {
            'line_id': str(basket_line.uuid),
            'closed': True,
            'line_total_amount': 2 if booked else -2,
            'basket_total_amount': 2 if booked else -2,
        }
        if booked:
            assert invoice.total_amount == 2
        else:
            assert invoice.total_amount == -2
        assert invoice.lines.count() == 2
        basket_line.items.all().delete()
        basket_line.delete()
        DraftInvoiceLine.objects.all().delete()

        new_params['payer_external_id'] = 'payer:1'
        resp = app.post_json('/api/regie/foo/basket/lines/from-bookings/dry-run/', params=new_params)
        assert resp.json['err'] == 0
        if booked:
            assert resp.json['data']['basket']['total_amount'] == 2
        else:
            assert resp.json['data']['basket']['total_amount'] == -2
        assert len(resp.json['data']['basket']['lines']) == 2
        new_params.pop('payer_external_id')

    # aggregate lines
    mock_pricing_data_event.side_effect = None
    mock_existing.return_value = {
        'foo@event1': {
            '2025-06-12': [
                Link(
                    payer_external_id='payer:1',
                    unit_amount=1,
                    booked=True,
                    invoicing_element_number='F-0001',
                ),
            ],
            '2025-06-20': [
                Link(
                    payer_external_id='payer:1',
                    unit_amount=1,
                    booked=True,
                    invoicing_element_number='F-0002',
                ),
            ],
        }
    }
    params.update(
        {
            'booked_events': [
                {
                    'agenda_slug': 'foo',
                    'slug': 'event1',
                    'primary_event': None,
                    'datetime': '2025-06-10 15:00:00',
                    'label': 'Event 1',
                },
                {
                    'agenda_slug': 'foo',
                    'slug': 'event1',
                    'primary_event': None,
                    'datetime': '2025-06-11 15:00:00',
                    'label': 'Event 1',
                },
            ],
            'cancelled_events': [
                {
                    'agenda_slug': 'foo',
                    'slug': 'event1',
                    'primary_event': None,
                    'datetime': '2025-06-12 15:00:00',
                    'label': 'Event 1',
                },
                {
                    'agenda_slug': 'foo',
                    'slug': 'event1',
                    'primary_event': None,
                    'datetime': '2025-06-20 15:00:00',
                    'label': 'Event 1',
                },
            ],
        }
    )
    resp = app.post_json('/api/regie/foo/basket/%s/lines/from-bookings/' % basket.uuid, params=params)
    assert resp.json['err'] == 0
    invoice.refresh_from_db()
    basket_line = basket.basketline_set.get()
    assert resp.json['data'] == {
        'line_id': str(basket_line.uuid),
        'closed': True,
        'line_total_amount': 0,
        'basket_total_amount': 0,
    }
    assert invoice.total_amount == 0
    assert invoice.lines.count() == 2
    lines = invoice.lines.order_by('pk')
    assert lines[0].event_date == datetime.date(2025, 6, 10)
    assert lines[0].label == 'Event 1'
    assert lines[0].quantity == 2
    assert lines[0].unit_amount == 1
    assert lines[0].details == {
        'dates': ['2025-06-10', '2025-06-11'],
    }
    assert lines[0].event_slug == 'foo@event1'
    assert lines[0].event_label == 'Event 1'
    assert lines[0].agenda_slug == 'foo'
    assert lines[0].activity_label == 'Foo'
    assert lines[0].description == 'Booking 10/06, 11/06'
    assert lines[0].accounting_code == '414141'
    assert lines[0].user_external_id == 'user:1'
    assert lines[0].user_first_name == 'First'
    assert lines[0].user_last_name == 'Last'
    assert lines[0].pool is None
    assert lines[0].form_url == 'http://form.com'
    assert lines[1].event_date == datetime.date(2025, 6, 12)
    assert lines[1].label == 'Event 1'
    assert lines[1].quantity == -2
    assert lines[1].unit_amount == 1
    assert lines[1].details == {
        'dates': ['2025-06-12', '2025-06-20'],
    }
    assert lines[1].event_slug == 'foo@event1'
    assert lines[1].event_label == 'Event 1'
    assert lines[1].agenda_slug == 'foo'
    assert lines[1].activity_label == 'Foo'
    assert lines[1].description == 'Cancellation 12/06, 20/06'
    assert lines[1].accounting_code == '414141'
    assert lines[1].user_external_id == 'user:1'
    assert lines[1].user_first_name == 'First'
    assert lines[1].user_last_name == 'Last'
    assert lines[1].pool is None
    assert lines[1].form_url == 'http://form.com'
    basket_line.items.all().delete()
    basket_line.delete()
    DraftInvoiceLine.objects.all().delete()
    params['payer_external_id'] = 'payer:1'
    resp = app.post_json('/api/regie/foo/basket/lines/from-bookings/dry-run/', params=params)
    params.pop('payer_external_id')
    assert resp.json['err'] == 0
    assert resp.json['data'] == {
        'basket': {
            'total_amount': 0,
            'lines': [
                {
                    'label': 'Event 1',
                    'description': 'Booking 10/06, 11/06',
                    'unit_amount': 1,
                    'quantity': 2,
                    'total_amount': 2,
                },
                {
                    'label': 'Event 1',
                    'description': 'Cancellation 12/06, 20/06',
                    'unit_amount': 1,
                    'quantity': -2,
                    'total_amount': -2,
                },
            ],
            'payer_external_id': 'payer:1',
            'payer_name': 'First1 Last1',
        }
    }


@mock.patch('lingo.api.views.utils.get_existing_lines_for_user')
@mock.patch('lingo.pricing.models.Pricing.get_pricing_data_for_event')
def test_from_bookings_current_payer_and_others(mock_pricing_data_event, mock_existing, app, user):
    app.authorization = ('Basic', ('john.doe', 'password'))
    regie = Regie.objects.create(label='Foo')
    agenda1 = Agenda.objects.create(label='Foo', regie=regie)
    agenda2 = Agenda.objects.create(label='Bar', regie=regie)
    pricing = Pricing.objects.create(
        date_start=datetime.date(year=2025, month=6, day=1),
        date_end=datetime.date(year=2025, month=7, day=1),
    )
    pricing.agendas.add(agenda1, agenda2)
    invoice = DraftInvoice.objects.create(
        regie=regie,
        label='My invoice',
        date_publication=datetime.date(2023, 4, 21),
        date_payment_deadline=datetime.date(2023, 4, 22),
        date_due=datetime.date(2023, 4, 23),
    )
    basket = Basket.objects.create(regie=regie, draft_invoice=invoice, payer_external_id='payer:1')

    def get_payer_data(ap, r, payer_external_id):
        return {
            'payer:1': {
                'first_name': 'First1',
                'last_name': 'Last1',
                'address': '41 rue des kangourous\n99999 Kangourou Ville',
                'email': 'email1',
                'phone': 'phone1',
                'direct_debit': False,
            },
            'payer:2': {
                'first_name': 'First2',
                'last_name': 'Last2',
                'address': '42 rue des kangourous\n99999 Kangourou Ville',
                'email': 'email2',
                'phone': 'phone2',
                'direct_debit': True,
            },
            'payer:3': {
                'first_name': 'First3',
                'last_name': 'Last3',
                'address': '43 rue des kangourous\n99999 Kangourou Ville',
                'email': 'email3',
                'phone': 'phone3',
                'direct_debit': True,
            },
        }.get(payer_external_id)

    mock_pricing_data_event.side_effect = [
        # dry-run
        {'foo1': 'bar1', 'pricing': 1, 'accounting_code': '414141'},
        {'foo2': 'bar2', 'pricing': 4, 'accounting_code': '424242'},
        {'foo3': 'bar3', 'pricing': 5, 'accounting_code': '434343'},
        # for user:1
        {'foo1': 'bar1', 'pricing': 1, 'accounting_code': '414141'},
        {'foo2': 'bar2', 'pricing': 4, 'accounting_code': '424242'},
        {'foo3': 'bar3', 'pricing': 5, 'accounting_code': '434343'},
        # for user:2
        {'foo1': 'bar1', 'pricing': 1, 'accounting_code': '414141'},
        {'foo2': 'bar2', 'pricing': 4, 'accounting_code': '424242'},
        {'foo3': 'bar3', 'pricing': 5, 'accounting_code': '434343'},
    ]

    mock_existing.return_value = {
        'foo@event1': {
            '2025-06-11': [
                Link(
                    payer_external_id='payer:2',
                    unit_amount=2,
                    booked=True,
                    invoicing_element_number='F-0001',
                ),
            ],
            '2025-06-12': [
                Link(
                    payer_external_id='payer:3',
                    unit_amount=3,
                    booked=True,
                    invoicing_element_number='F-0002',
                ),
            ],
        }
    }
    params = {
        'user_external_id': 'user:1',
        'user_first_name': 'First',
        'user_last_name': 'Last',
        'information_message': 'foo baz',
        'cancel_information_message': 'foo bar',
        'form_url': 'http://form.com',
        'validation_callback_url': 'http://validation.com',
        'payment_callback_url': 'http://payment.com',
        'credit_callback_url': 'http://credit.com',
        'cancel_callback_url': 'http://cancel.com',
        'expiration_callback_url': 'http://expiration.com',
        'booked_events': [
            {
                'agenda_slug': 'foo',
                'slug': 'event1',
                'primary_event': None,
                'datetime': '2025-06-10 15:00:00',
                'label': 'Event 1',
            },
        ],
        'cancelled_events': [
            {
                'agenda_slug': 'foo',
                'slug': 'event1',
                'primary_event': None,
                'datetime': '2025-06-11 15:00:00',
                'label': 'Event 1',
            },
            {
                'agenda_slug': 'foo',
                'slug': 'event1',
                'primary_event': None,
                'datetime': '2025-06-12 15:00:00',
                'label': 'Event 1',
            },
        ],
    }

    payer_data_patch = mock.patch.object(Regie, 'get_payer_data', autospec=True)
    params['payer_external_id'] = 'payer:1'
    with payer_data_patch as mock_payer_data:
        mock_payer_data.side_effect = get_payer_data
        resp = app.post_json('/api/regie/foo/basket/lines/from-bookings/dry-run/', params=params)
    params.pop('payer_external_id')
    assert resp.json['err'] == 0
    assert resp.json['data'] == {
        'basket': {
            'total_amount': 1,
            'lines': [
                {
                    'label': 'Event 1',
                    'description': 'Booking 10/06',
                    'unit_amount': 1,
                    'quantity': 1,
                    'total_amount': 1,
                }
            ],
            'payer_external_id': 'payer:1',
            'payer_name': 'First1 Last1',
        },
        'other_payer_credit_drafts': [
            {
                'total_amount': 2,
                'lines': [
                    {
                        'label': 'Event 1',
                        'description': 'Cancellation 11/06',
                        'unit_amount': 2,
                        'quantity': 1,
                        'total_amount': 2,
                    }
                ],
                'payer_external_id': 'payer:2',
                'payer_name': 'First2 Last2',
            },
            {
                'total_amount': 3,
                'lines': [
                    {
                        'label': 'Event 1',
                        'description': 'Cancellation 12/06',
                        'unit_amount': 3,
                        'quantity': 1,
                        'total_amount': 3,
                    }
                ],
                'payer_external_id': 'payer:3',
                'payer_name': 'First3 Last3',
            },
        ],
    }

    with payer_data_patch as mock_payer_data:
        mock_payer_data.side_effect = get_payer_data
        resp = app.post_json('/api/regie/foo/basket/%s/lines/from-bookings/' % basket.uuid, params=params)
    assert resp.json['err'] == 0
    invoice.refresh_from_db()
    assert basket.basketline_set.count() == 1
    basket_line = basket.basketline_set.latest('pk')
    assert basket_line.items.count() == 1
    assert DraftInvoice.objects.filter(payer_external_id='payer:2').count() == 1
    assert DraftInvoice.objects.filter(payer_external_id='payer:3').count() == 1
    draft_invoice_payer2 = DraftInvoice.objects.get(payer_external_id='payer:2')
    draft_invoice_payer3 = DraftInvoice.objects.get(payer_external_id='payer:3')
    assert list(basket.other_payer_credits_draft.all().order_by('pk')) == [
        draft_invoice_payer2,
        draft_invoice_payer3,
    ]
    assert resp.json['data'] == {
        'line_id': str(basket_line.uuid),
        'closed': True,
        'line_total_amount': 1,
        'basket_total_amount': 1,
        'other_payer_credit_draft_ids': [str(draft_invoice_payer2.uuid), str(draft_invoice_payer3.uuid)],
    }

    assert invoice.total_amount == 1
    assert invoice.lines.count() == 1
    lines = invoice.lines.order_by('pk')
    assert lines[0].event_date == datetime.date(2025, 6, 10)
    assert lines[0].label == 'Event 1'
    assert lines[0].quantity == 1
    assert lines[0].unit_amount == 1
    assert lines[0].details == {
        'dates': ['2025-06-10'],
    }
    assert lines[0].event_slug == 'foo@event1'
    assert lines[0].event_label == 'Event 1'
    assert lines[0].agenda_slug == 'foo'
    assert lines[0].activity_label == 'Foo'
    assert lines[0].description == 'Booking 10/06'
    assert lines[0].accounting_code == '414141'
    assert lines[0].user_external_id == 'user:1'
    assert lines[0].user_first_name == 'First'
    assert lines[0].user_last_name == 'Last'
    assert lines[0].pool is None
    assert lines[0].form_url == 'http://form.com'

    assert draft_invoice_payer2.label == 'My invoice'
    assert draft_invoice_payer2.total_amount == -2
    assert draft_invoice_payer2.payer_external_id == 'payer:2'
    assert draft_invoice_payer2.payer_first_name == 'First2'
    assert draft_invoice_payer2.payer_last_name == 'Last2'
    assert draft_invoice_payer2.payer_address == '42 rue des kangourous\n99999 Kangourou Ville'
    assert draft_invoice_payer2.payer_email == 'email2'
    assert draft_invoice_payer2.payer_phone == 'phone2'
    assert draft_invoice_payer2.lines.count() == 1
    lines = draft_invoice_payer2.lines.order_by('pk')
    assert lines[0].event_date == datetime.date(2025, 6, 11)
    assert lines[0].label == 'Event 1'
    assert lines[0].quantity == -1
    assert lines[0].unit_amount == 2
    assert lines[0].details == {
        'dates': ['2025-06-11'],
    }
    assert lines[0].event_slug == 'foo@event1'
    assert lines[0].event_label == 'Event 1'
    assert lines[0].agenda_slug == 'foo'
    assert lines[0].activity_label == 'Foo'
    assert lines[0].description == 'Cancellation 11/06'
    assert lines[0].accounting_code == '424242'
    assert lines[0].user_external_id == 'user:1'
    assert lines[0].user_first_name == 'First'
    assert lines[0].user_last_name == 'Last'
    assert lines[0].pool is None
    assert lines[0].form_url == 'http://form.com'

    assert draft_invoice_payer3.label == 'My invoice'
    assert draft_invoice_payer3.total_amount == -3
    assert draft_invoice_payer3.payer_external_id == 'payer:3'
    assert draft_invoice_payer3.payer_first_name == 'First3'
    assert draft_invoice_payer3.payer_last_name == 'Last3'
    assert draft_invoice_payer3.payer_address == '43 rue des kangourous\n99999 Kangourou Ville'
    assert draft_invoice_payer3.payer_email == 'email3'
    assert draft_invoice_payer3.payer_phone == 'phone3'
    assert draft_invoice_payer3.lines.count() == 1
    lines = draft_invoice_payer3.lines.order_by('pk')
    assert lines[0].event_date == datetime.date(2025, 6, 12)
    assert lines[0].label == 'Event 1'
    assert lines[0].quantity == -1
    assert lines[0].unit_amount == 3
    assert lines[0].details == {
        'dates': ['2025-06-12'],
    }
    assert lines[0].event_slug == 'foo@event1'
    assert lines[0].event_label == 'Event 1'
    assert lines[0].agenda_slug == 'foo'
    assert lines[0].activity_label == 'Foo'
    assert lines[0].description == 'Cancellation 12/06'
    assert lines[0].accounting_code == '434343'
    assert lines[0].user_external_id == 'user:1'
    assert lines[0].user_first_name == 'First'
    assert lines[0].user_last_name == 'Last'
    assert lines[0].pool is None
    assert lines[0].form_url == 'http://form.com'

    # same for another user: line is added to basket, new credits are linked to basket
    params.update({'user_external_id': 'user:2'})
    with payer_data_patch as mock_payer_data:
        mock_payer_data.side_effect = get_payer_data
        resp = app.post_json('/api/regie/foo/basket/%s/lines/from-bookings/' % basket.uuid, params=params)
    assert resp.json['err'] == 0
    invoice.refresh_from_db()
    assert basket.basketline_set.count() == 2
    basket_line = basket.basketline_set.latest('pk')
    assert basket_line.items.count() == 1
    assert DraftInvoice.objects.filter(payer_external_id='payer:2').count() == 2
    assert DraftInvoice.objects.filter(payer_external_id='payer:3').count() == 2
    draft_invoice_payer4 = DraftInvoice.objects.filter(payer_external_id='payer:2').latest('pk')
    draft_invoice_payer5 = DraftInvoice.objects.filter(payer_external_id='payer:3').latest('pk')
    assert list(basket.other_payer_credits_draft.all().order_by('pk')) == [
        draft_invoice_payer2,
        draft_invoice_payer3,
        draft_invoice_payer4,
        draft_invoice_payer5,
    ]
    assert resp.json['data'] == {
        'line_id': str(basket_line.uuid),
        'closed': True,
        'line_total_amount': 1,
        'basket_total_amount': 2,
        'other_payer_credit_draft_ids': [str(draft_invoice_payer4.uuid), str(draft_invoice_payer5.uuid)],
    }


@mock.patch('lingo.api.views.utils.get_existing_lines_for_user')
@mock.patch('lingo.pricing.models.Pricing.get_pricing_data_for_event')
def test_from_bookings_other_payers_only(mock_pricing_data_event, mock_existing, app, user):
    app.authorization = ('Basic', ('john.doe', 'password'))
    regie = Regie.objects.create(label='Foo')
    agenda1 = Agenda.objects.create(label='Foo', regie=regie)
    agenda2 = Agenda.objects.create(label='Bar', regie=regie)
    pricing = Pricing.objects.create(
        date_start=datetime.date(year=2025, month=6, day=1),
        date_end=datetime.date(year=2025, month=7, day=1),
    )
    pricing.agendas.add(agenda1, agenda2)
    invoice = DraftInvoice.objects.create(
        regie=regie,
        label='My invoice',
        date_publication=datetime.date(2023, 4, 21),
        date_payment_deadline=datetime.date(2023, 4, 22),
        date_due=datetime.date(2023, 4, 23),
    )
    basket = Basket.objects.create(regie=regie, draft_invoice=invoice, payer_external_id='payer:1')

    def get_payer_data(ap, r, payer_external_id):
        return {
            'payer:2': {
                'first_name': 'First2',
                'last_name': 'Last2',
                'address': '42 rue des kangourous\n99999 Kangourou Ville',
                'email': 'email2',
                'phone': 'phone2',
                'direct_debit': True,
            },
            'payer:3': {
                'first_name': 'First3',
                'last_name': 'Last3',
                'address': '43 rue des kangourous\n99999 Kangourou Ville',
                'email': 'email3',
                'phone': 'phone3',
                'direct_debit': True,
            },
        }.get(payer_external_id)

    mock_pricing_data_event.side_effect = [
        {'foo2': 'bar2', 'pricing': 4, 'accounting_code': '424242'},
        {'foo3': 'bar3', 'pricing': 5, 'accounting_code': '434343'},
        # dry-run
        {'foo2': 'bar2', 'pricing': 4, 'accounting_code': '424242'},
        {'foo3': 'bar3', 'pricing': 5, 'accounting_code': '434343'},
    ]

    mock_existing.return_value = {
        'foo@event1': {
            '2025-06-11': [
                Link(
                    payer_external_id='payer:2',
                    unit_amount=2,
                    booked=True,
                    invoicing_element_number='F-0001',
                ),
            ],
            '2025-06-12': [
                Link(
                    payer_external_id='payer:3',
                    unit_amount=3,
                    booked=True,
                    invoicing_element_number='F-0002',
                ),
            ],
        }
    }
    params = {
        'user_external_id': 'user:1',
        'user_first_name': 'First',
        'user_last_name': 'Last',
        'information_message': 'foo baz',
        'cancel_information_message': 'foo bar',
        'form_url': 'http://form.com',
        'validation_callback_url': 'http://validation.com',
        'payment_callback_url': 'http://payment.com',
        'credit_callback_url': 'http://credit.com',
        'cancel_callback_url': 'http://cancel.com',
        'expiration_callback_url': 'http://expiration.com',
        'booked_events': [],
        'cancelled_events': [
            {
                'agenda_slug': 'foo',
                'slug': 'event1',
                'primary_event': None,
                'datetime': '2025-06-11 15:00:00',
                'label': 'Event 1',
            },
            {
                'agenda_slug': 'foo',
                'slug': 'event1',
                'primary_event': None,
                'datetime': '2025-06-12 15:00:00',
                'label': 'Event 1',
            },
        ],
    }

    payer_data_patch = mock.patch.object(Regie, 'get_payer_data', autospec=True)
    with payer_data_patch as mock_payer_data:
        mock_payer_data.side_effect = get_payer_data
        resp = app.post_json('/api/regie/foo/basket/%s/lines/from-bookings/' % basket.uuid, params=params)
    assert resp.json['err'] == 0
    invoice.refresh_from_db()
    draft_invoice_payer2 = DraftInvoice.objects.get(payer_external_id='payer:2')
    draft_invoice_payer3 = DraftInvoice.objects.get(payer_external_id='payer:3')
    assert list(basket.other_payer_credits_draft.all().order_by('pk')) == [
        draft_invoice_payer2,
        draft_invoice_payer3,
    ]
    assert resp.json['data'] == {
        'other_payer_credit_draft_ids': [str(draft_invoice_payer2.uuid), str(draft_invoice_payer3.uuid)],
    }
    assert basket.basketline_set.count() == 0
    assert invoice.total_amount == 0
    assert invoice.lines.count() == 0

    assert draft_invoice_payer2.label == 'My invoice'
    assert draft_invoice_payer2.total_amount == -2
    assert draft_invoice_payer2.payer_external_id == 'payer:2'
    assert draft_invoice_payer2.payer_first_name == 'First2'
    assert draft_invoice_payer2.payer_last_name == 'Last2'
    assert draft_invoice_payer2.payer_address == '42 rue des kangourous\n99999 Kangourou Ville'
    assert draft_invoice_payer2.payer_email == 'email2'
    assert draft_invoice_payer2.payer_phone == 'phone2'
    assert draft_invoice_payer2.lines.count() == 1
    lines = draft_invoice_payer2.lines.order_by('pk')
    assert lines[0].event_date == datetime.date(2025, 6, 11)
    assert lines[0].label == 'Event 1'
    assert lines[0].quantity == -1
    assert lines[0].unit_amount == 2
    assert lines[0].details == {
        'dates': ['2025-06-11'],
    }
    assert lines[0].event_slug == 'foo@event1'
    assert lines[0].event_label == 'Event 1'
    assert lines[0].agenda_slug == 'foo'
    assert lines[0].activity_label == 'Foo'
    assert lines[0].description == 'Cancellation 11/06'
    assert lines[0].accounting_code == '424242'
    assert lines[0].user_external_id == 'user:1'
    assert lines[0].user_first_name == 'First'
    assert lines[0].user_last_name == 'Last'
    assert lines[0].pool is None

    assert draft_invoice_payer3.label == 'My invoice'
    assert draft_invoice_payer3.total_amount == -3
    assert draft_invoice_payer3.payer_external_id == 'payer:3'
    assert draft_invoice_payer3.payer_first_name == 'First3'
    assert draft_invoice_payer3.payer_last_name == 'Last3'
    assert draft_invoice_payer3.payer_address == '43 rue des kangourous\n99999 Kangourou Ville'
    assert draft_invoice_payer3.payer_email == 'email3'
    assert draft_invoice_payer3.payer_phone == 'phone3'
    assert draft_invoice_payer3.lines.count() == 1
    lines = draft_invoice_payer3.lines.order_by('pk')
    assert lines[0].event_date == datetime.date(2025, 6, 12)
    assert lines[0].label == 'Event 1'
    assert lines[0].quantity == -1
    assert lines[0].unit_amount == 3
    assert lines[0].details == {
        'dates': ['2025-06-12'],
    }
    assert lines[0].event_slug == 'foo@event1'
    assert lines[0].event_label == 'Event 1'
    assert lines[0].agenda_slug == 'foo'
    assert lines[0].activity_label == 'Foo'
    assert lines[0].description == 'Cancellation 12/06'
    assert lines[0].accounting_code == '434343'
    assert lines[0].user_external_id == 'user:1'
    assert lines[0].user_first_name == 'First'
    assert lines[0].user_last_name == 'Last'
    assert lines[0].pool is None

    params['payer_external_id'] = 'payer:1'
    with payer_data_patch as mock_payer_data:
        mock_payer_data.side_effect = get_payer_data
        resp = app.post_json('/api/regie/foo/basket/lines/from-bookings/dry-run/', params=params)
    assert resp.json['err'] == 0
    assert resp.json['data'] == {
        'other_payer_credit_drafts': [
            {
                'total_amount': 2,
                'lines': [
                    {
                        'label': 'Event 1',
                        'description': 'Cancellation 11/06',
                        'unit_amount': 2,
                        'quantity': 1,
                        'total_amount': 2,
                    }
                ],
                'payer_external_id': 'payer:2',
                'payer_name': 'First2 Last2',
            },
            {
                'total_amount': 3,
                'lines': [
                    {
                        'label': 'Event 1',
                        'description': 'Cancellation 12/06',
                        'unit_amount': 3,
                        'quantity': 1,
                        'total_amount': 3,
                    }
                ],
                'payer_external_id': 'payer:3',
                'payer_name': 'First3 Last3',
            },
        ]
    }


@mock.patch('lingo.api.views.utils.get_existing_lines_for_user')
@mock.patch('lingo.invoicing.models.Regie.get_payer_data')
@mock.patch('lingo.pricing.models.Pricing.get_pricing_data_for_event')
def test_from_bookings_with_adjustment_for_current_payer_booked_event(
    mock_pricing_data_event, mock_payer_data, mock_existing, app, user
):
    app.authorization = ('Basic', ('john.doe', 'password'))
    regie = Regie.objects.create(label='Foo')
    agenda1 = Agenda.objects.create(label='Foo', regie=regie)
    agenda2 = Agenda.objects.create(label='Bar', regie=regie)
    pricing = Pricing.objects.create(
        date_start=datetime.date(year=2025, month=6, day=1),
        date_end=datetime.date(year=2025, month=7, day=1),
    )
    pricing.agendas.add(agenda1, agenda2)
    invoice = DraftInvoice.objects.create(
        regie=regie,
        label='My invoice',
        date_publication=datetime.date(2023, 4, 21),
        date_payment_deadline=datetime.date(2023, 4, 22),
        date_due=datetime.date(2023, 4, 23),
    )
    basket = Basket.objects.create(regie=regie, draft_invoice=invoice, payer_external_id='payer:1')

    mock_payer_data.return_value = {
        'first_name': 'First1',
        'last_name': 'Last1',
        'address': '41 rue des kangourous\n99999 Kangourou Ville',
        'email': 'email1',
        'phone': 'phone1',
        'direct_debit': False,
    }

    mock_pricing_data_event.return_value = {'pricing': 33, 'accounting_code': '414141'}

    params = {
        'user_external_id': 'user:1',
        'user_first_name': 'First',
        'user_last_name': 'Last',
        'information_message': 'foo baz',
        'cancel_information_message': 'foo bar',
        'form_url': 'http://form.com',
        'validation_callback_url': 'http://validation.com',
        'payment_callback_url': 'http://payment.com',
        'credit_callback_url': 'http://credit.com',
        'cancel_callback_url': 'http://cancel.com',
        'expiration_callback_url': 'http://expiration.com',
        'booked_events': [
            {
                'agenda_slug': 'foo',
                'slug': 'event1',
                'primary_event': None,
                'datetime': '2025-06-10 12:00:00',
                'label': 'Event 1',
            },
        ],
        'cancelled_events': [],
    }

    # chain is empty
    existing = [
        {},
        {'foo@event1': {}},
        {'foo@event1': {'2025-06-10': []}},
    ]
    for value in existing:
        mock_existing.return_value = value
        resp = app.post_json('/api/regie/foo/basket/%s/lines/from-bookings/' % basket.uuid, params=params)
        assert resp.json['err'] == 0
        invoice.refresh_from_db()
        basket_line = basket.basketline_set.get()
        assert resp.json['data'] == {
            'line_id': str(basket_line.uuid),
            'closed': True,
            'line_total_amount': 33,
            'basket_total_amount': 33,
        }
        assert invoice.total_amount == 33
        assert invoice.lines.count() == 1
        lines = invoice.lines.order_by('pk')
        assert lines[0].unit_amount == 33
        assert lines[0].quantity == 1
        assert lines[0].description == 'Booking 10/06'
        assert lines[0].details == {'dates': ['2025-06-10']}
        basket_line.items.all().delete()
        basket_line.delete()
        DraftInvoiceLine.objects.all().delete()

    # chain is complete, and it ends with a cancellation
    existing = [
        {
            'foo@event1': {
                '2025-06-10': [
                    Link(
                        payer_external_id='payer:1',
                        unit_amount=11,
                        booked=True,
                        invoicing_element_number='F-0001',
                    ),
                    Link(
                        payer_external_id='payer:1',
                        unit_amount=11,
                        booked=False,
                        invoicing_element_number='A-0001',
                    ),
                ]
            }
        },
        {
            'foo@event1': {
                '2025-06-10': [
                    # not ok, but adjustements cancel each other
                    Link(
                        payer_external_id='payer:1',
                        unit_amount=22,
                        booked=False,
                        invoicing_element_number='A-0002',
                    ),
                    Link(
                        payer_external_id='payer:1',
                        unit_amount=22,
                        booked=True,
                        invoicing_element_number='F-0002',
                    ),
                    # not ok, but adjustements cancel each other
                    Link(
                        payer_external_id='payer:2',
                        unit_amount=33,
                        booked=False,
                        invoicing_element_number='A-0003',
                    ),
                    Link(
                        payer_external_id='payer:2',
                        unit_amount=33,
                        booked=True,
                        invoicing_element_number='F-0003',
                    ),
                    # complete
                    Link(
                        payer_external_id='payer:1',
                        unit_amount=11,
                        booked=True,
                        invoicing_element_number='F-0001',
                    ),
                    Link(
                        payer_external_id='payer:1',
                        unit_amount=11,
                        booked=False,
                        invoicing_element_number='A-0001',
                    ),
                ]
            }
        },
    ]
    for value in existing:
        mock_existing.return_value = value
        resp = app.post_json('/api/regie/foo/basket/%s/lines/from-bookings/' % basket.uuid, params=params)
        assert resp.json['err'] == 0
        invoice.refresh_from_db()
        basket_line = basket.basketline_set.get()
        assert resp.json['data'] == {
            'line_id': str(basket_line.uuid),
            'closed': True,
            'line_total_amount': 33,
            'basket_total_amount': 33,
        }
        assert invoice.total_amount == 33
        assert invoice.lines.count() == 1
        lines = invoice.lines.order_by('pk')
        assert lines[0].unit_amount == 33
        assert lines[0].quantity == 1
        assert lines[0].description == 'Booking 10/06'
        assert lines[0].details == {'dates': ['2025-06-10']}
        basket_line.items.all().delete()
        basket_line.delete()
        DraftInvoiceLine.objects.all().delete()

        params['payer_external_id'] = 'payer:1'
        resp = app.post_json('/api/regie/foo/basket/lines/from-bookings/dry-run/', params=params)
        params.pop('payer_external_id')
        assert resp.json['err'] == 0
        assert resp.json['data'] == {
            'basket': {
                'total_amount': 33,
                'lines': [
                    {
                        'label': 'Event 1',
                        'description': 'Booking 10/06',
                        'unit_amount': 33,
                        'quantity': 1,
                        'total_amount': 33,
                    }
                ],
                'payer_external_id': 'payer:1',
                'payer_name': 'First1 Last1',
            }
        }

    # chain is complete, but ends with a booking
    existing = [
        {
            'foo@event1': {
                '2025-06-10': [
                    Link(
                        payer_external_id='payer:1',
                        unit_amount=22,
                        booked=True,
                        invoicing_element_number='F-0002',
                    ),
                ]
            }
        },
        {
            'foo@event1': {
                '2025-06-10': [
                    Link(
                        payer_external_id='payer:1',
                        unit_amount=11,
                        booked=True,
                        invoicing_element_number='F-0001',
                    ),
                    Link(
                        payer_external_id='payer:1',
                        unit_amount=11,
                        booked=False,
                        invoicing_element_number='A-0001',
                    ),
                    Link(
                        payer_external_id='payer:1',
                        unit_amount=22,
                        booked=True,
                        invoicing_element_number='F-0002',
                    ),
                ]
            }
        },
    ]
    for value in existing:
        mock_existing.return_value = value
        resp = app.post_json('/api/regie/foo/basket/%s/lines/from-bookings/' % basket.uuid, params=params)
        assert resp.json['err'] == 0
        invoice.refresh_from_db()
        basket_line = basket.basketline_set.get()
        assert resp.json['data'] == {
            'line_id': str(basket_line.uuid),
            'closed': True,
            'line_total_amount': 11,
            'basket_total_amount': 11,
        }
        assert invoice.total_amount == 11
        assert invoice.lines.count() == 2
        lines = invoice.lines.order_by('pk')
        assert lines[0].unit_amount == 33
        assert lines[0].quantity == 1
        assert lines[0].description == 'Booking 10/06'
        assert lines[0].details == {'dates': ['2025-06-10']}
        assert lines[1].unit_amount == 22
        assert lines[1].quantity == -1
        assert lines[1].description == 'Cancellation (regularization) 10/06'
        assert lines[1].details == {
            'dates': ['2025-06-10'],
            'adjustment': {
                'reason': 'missing-cancellation',
                '2025-06-10': {
                    'before': 'F-0002',
                },
            },
        }
        basket_line.items.all().delete()
        basket_line.delete()
        DraftInvoiceLine.objects.all().delete()

        params['payer_external_id'] = 'payer:1'
        resp = app.post_json('/api/regie/foo/basket/lines/from-bookings/dry-run/', params=params)
        params.pop('payer_external_id')
        assert resp.json['err'] == 0
        assert resp.json['data'] == {
            'basket': {
                'total_amount': 11,
                'lines': [
                    {
                        'label': 'Event 1',
                        'description': 'Booking 10/06',
                        'unit_amount': 33,
                        'quantity': 1,
                        'total_amount': 33,
                    },
                    {
                        'label': 'Event 1',
                        'description': 'Cancellation (regularization) 10/06',
                        'unit_amount': 22,
                        'quantity': -1,
                        'total_amount': -22,
                    },
                ],
                'payer_external_id': 'payer:1',
                'payer_name': 'First1 Last1',
            }
        }

    # chain is not complete, missing booking
    existing = [
        {
            'foo@event1': {
                '2025-06-10': [
                    Link(
                        payer_external_id='payer:1',
                        unit_amount=22,
                        booked=False,
                        invoicing_element_number='A-0002',
                    ),
                ]
            }
        },
        {
            'foo@event1': {
                '2025-06-10': [
                    Link(
                        payer_external_id='payer:1',
                        unit_amount=11,
                        booked=True,
                        invoicing_element_number='F-0001',
                    ),
                    Link(
                        payer_external_id='payer:1',
                        unit_amount=11,
                        booked=False,
                        invoicing_element_number='A-0001',
                    ),
                    Link(
                        payer_external_id='payer:1',
                        unit_amount=22,
                        booked=False,
                        invoicing_element_number='A-0002',
                    ),
                ]
            }
        },
    ]
    for value in existing:
        mock_existing.return_value = value
        resp = app.post_json('/api/regie/foo/basket/%s/lines/from-bookings/' % basket.uuid, params=params)
        assert resp.json['err'] == 0
        invoice.refresh_from_db()
        basket_line = basket.basketline_set.get()
        assert resp.json['data'] == {
            'line_id': str(basket_line.uuid),
            'closed': True,
            'line_total_amount': 55,
            'basket_total_amount': 55,
        }
        assert invoice.total_amount == 55
        assert invoice.lines.count() == 2
        lines = invoice.lines.order_by('pk')
        assert lines[0].unit_amount == 22
        assert lines[0].quantity == 1
        assert lines[0].description == 'Booking (regularization) 10/06'
        if len(value['foo@event1']['2025-06-10']) == 3:
            assert lines[0].details == {
                'dates': ['2025-06-10'],
                'adjustment': {
                    'reason': 'missing-booking',
                    '2025-06-10': {
                        'before': 'A-0001',
                        'after': 'A-0002',
                    },
                },
            }
        else:
            assert lines[0].details == {
                'dates': ['2025-06-10'],
                'adjustment': {
                    'reason': 'missing-booking',
                    '2025-06-10': {
                        'after': 'A-0002',
                    },
                },
            }
        assert lines[1].unit_amount == 33
        assert lines[1].quantity == 1
        assert lines[1].description == 'Booking 10/06'
        assert lines[1].details == {'dates': ['2025-06-10']}
        basket_line.items.all().delete()
        basket_line.delete()
        DraftInvoiceLine.objects.all().delete()

        params['payer_external_id'] = 'payer:1'
        resp = app.post_json('/api/regie/foo/basket/lines/from-bookings/dry-run/', params=params)
        params.pop('payer_external_id')
        assert resp.json['err'] == 0
        assert resp.json['data'] == {
            'basket': {
                'total_amount': 55,
                'lines': [
                    {
                        'label': 'Event 1',
                        'description': 'Booking (regularization) 10/06',
                        'unit_amount': 22,
                        'quantity': 1,
                        'total_amount': 22,
                    },
                    {
                        'label': 'Event 1',
                        'description': 'Booking 10/06',
                        'unit_amount': 33,
                        'quantity': 1,
                        'total_amount': 33,
                    },
                ],
                'payer_external_id': 'payer:1',
                'payer_name': 'First1 Last1',
            }
        }

    # chain is not complete, missing cancellation
    mock_existing.return_value = {
        'foo@event1': {
            '2025-06-10': [
                Link(
                    payer_external_id='payer:1',
                    unit_amount=11,
                    booked=True,
                    invoicing_element_number='F-0001',
                ),
                Link(
                    payer_external_id='payer:1',
                    unit_amount=22,
                    booked=True,
                    invoicing_element_number='F-0002',
                ),
                Link(
                    payer_external_id='payer:1',
                    unit_amount=22,
                    booked=False,
                    invoicing_element_number='A-0002',
                ),
            ]
        }
    }
    resp = app.post_json('/api/regie/foo/basket/%s/lines/from-bookings/' % basket.uuid, params=params)
    assert resp.json['err'] == 0
    invoice.refresh_from_db()
    basket_line = basket.basketline_set.get()
    assert resp.json['data'] == {
        'line_id': str(basket_line.uuid),
        'closed': True,
        'line_total_amount': 22,
        'basket_total_amount': 22,
    }
    assert invoice.total_amount == 22
    assert invoice.lines.count() == 2
    lines = invoice.lines.order_by('pk')
    assert lines[0].unit_amount == 33
    assert lines[0].quantity == 1
    assert lines[0].description == 'Booking 10/06'
    assert lines[0].details == {'dates': ['2025-06-10']}
    assert lines[1].unit_amount == 11
    assert lines[1].quantity == -1
    assert lines[1].description == 'Cancellation (regularization) 10/06'
    assert lines[1].details == {
        'dates': ['2025-06-10'],
        'adjustment': {
            'reason': 'missing-cancellation',
            '2025-06-10': {
                'before': 'F-0001',
                'after': 'F-0002',
            },
        },
    }
    basket_line.items.all().delete()
    basket_line.delete()
    DraftInvoiceLine.objects.all().delete()

    params['payer_external_id'] = 'payer:1'
    resp = app.post_json('/api/regie/foo/basket/lines/from-bookings/dry-run/', params=params)
    params.pop('payer_external_id')
    assert resp.json['err'] == 0
    assert resp.json['data'] == {
        'basket': {
            'total_amount': 22,
            'lines': [
                {
                    'label': 'Event 1',
                    'description': 'Booking 10/06',
                    'unit_amount': 33,
                    'quantity': 1,
                    'total_amount': 33,
                },
                {
                    'label': 'Event 1',
                    'description': 'Cancellation (regularization) 10/06',
                    'unit_amount': 11,
                    'quantity': -1,
                    'total_amount': -11,
                },
            ],
            'payer_external_id': 'payer:1',
            'payer_name': 'First1 Last1',
        }
    }

    # chain is not complete, amount inconsistancy
    mock_existing.return_value = {
        'foo@event1': {
            '2025-06-10': [
                Link(
                    payer_external_id='payer:1',
                    unit_amount=11,
                    booked=True,
                    invoicing_element_number='F-0001',
                ),
                Link(
                    payer_external_id='payer:1',
                    unit_amount=22,
                    booked=False,
                    invoicing_element_number='A-0001',
                ),
            ]
        }
    }
    resp = app.post_json('/api/regie/foo/basket/%s/lines/from-bookings/' % basket.uuid, params=params)
    assert resp.json['err'] == 0
    invoice.refresh_from_db()
    basket_line = basket.basketline_set.get()
    assert resp.json['data'] == {
        'line_id': str(basket_line.uuid),
        'closed': True,
        'line_total_amount': 44,
        'basket_total_amount': 44,
    }
    assert invoice.total_amount == 44
    assert invoice.lines.count() == 3
    lines = invoice.lines.order_by('pk')
    assert lines[0].unit_amount == 22
    assert lines[0].quantity == 1
    assert lines[0].description == 'Booking (regularization) 10/06'
    assert lines[0].details == {
        'dates': ['2025-06-10'],
        'adjustment': {
            'reason': 'missing-booking',
            '2025-06-10': {
                'before': 'F-0001',
                'after': 'A-0001',
            },
        },
    }
    assert lines[1].unit_amount == 33
    assert lines[1].quantity == 1
    assert lines[1].description == 'Booking 10/06'
    assert lines[1].details == {'dates': ['2025-06-10']}
    assert lines[2].unit_amount == 11
    assert lines[2].quantity == -1
    assert lines[2].description == 'Cancellation (regularization) 10/06'
    assert lines[2].details == {
        'dates': ['2025-06-10'],
        'adjustment': {
            'reason': 'missing-cancellation',
            '2025-06-10': {
                'before': 'F-0001',
                'after': 'A-0001',
            },
        },
    }
    basket_line.items.all().delete()
    basket_line.delete()
    DraftInvoiceLine.objects.all().delete()

    params['payer_external_id'] = 'payer:1'
    resp = app.post_json('/api/regie/foo/basket/lines/from-bookings/dry-run/', params=params)
    params.pop('payer_external_id')
    assert resp.json['err'] == 0
    assert resp.json['data'] == {
        'basket': {
            'total_amount': 44,
            'lines': [
                {
                    'label': 'Event 1',
                    'description': 'Booking (regularization) 10/06',
                    'unit_amount': 22,
                    'quantity': 1,
                    'total_amount': 22,
                },
                {
                    'label': 'Event 1',
                    'description': 'Booking 10/06',
                    'unit_amount': 33,
                    'quantity': 1,
                    'total_amount': 33,
                },
                {
                    'label': 'Event 1',
                    'description': 'Cancellation (regularization) 10/06',
                    'unit_amount': 11,
                    'quantity': -1,
                    'total_amount': -11,
                },
            ],
            'payer_external_id': 'payer:1',
            'payer_name': 'First1 Last1',
        }
    }


@mock.patch('lingo.api.views.utils.get_existing_lines_for_user')
@mock.patch('lingo.invoicing.models.Regie.get_payer_data')
@mock.patch('lingo.pricing.models.Pricing.get_pricing_data_for_event')
def test_from_bookings_with_adjustment_for_current_payer_cancelled_event(
    mock_pricing_data_event, mock_payer_data, mock_existing, app, user
):
    app.authorization = ('Basic', ('john.doe', 'password'))
    regie = Regie.objects.create(label='Foo')
    agenda1 = Agenda.objects.create(label='Foo', regie=regie)
    agenda2 = Agenda.objects.create(label='Bar', regie=regie)
    pricing = Pricing.objects.create(
        date_start=datetime.date(year=2025, month=6, day=1),
        date_end=datetime.date(year=2025, month=7, day=1),
    )
    pricing.agendas.add(agenda1, agenda2)
    invoice = DraftInvoice.objects.create(
        regie=regie,
        label='My invoice',
        date_publication=datetime.date(2023, 4, 21),
        date_payment_deadline=datetime.date(2023, 4, 22),
        date_due=datetime.date(2023, 4, 23),
    )
    basket = Basket.objects.create(regie=regie, draft_invoice=invoice, payer_external_id='payer:1')

    mock_payer_data.return_value = {
        'first_name': 'First1',
        'last_name': 'Last1',
        'address': '41 rue des kangourous\n99999 Kangourou Ville',
        'email': 'email1',
        'phone': 'phone1',
        'direct_debit': False,
    }

    mock_pricing_data_event.return_value = {'pricing': 33}

    params = {
        'user_external_id': 'user:1',
        'user_first_name': 'First',
        'user_last_name': 'Last',
        'information_message': 'foo baz',
        'cancel_information_message': 'foo bar',
        'form_url': 'http://form.com',
        'validation_callback_url': 'http://validation.com',
        'payment_callback_url': 'http://payment.com',
        'credit_callback_url': 'http://credit.com',
        'cancel_callback_url': 'http://cancel.com',
        'expiration_callback_url': 'http://expiration.com',
        'booked_events': [],
        'cancelled_events': [
            {
                'agenda_slug': 'foo',
                'slug': 'event1',
                'primary_event': None,
                'datetime': '2025-06-10 12:00:00',
                'label': 'Event 1',
            },
        ],
    }

    # chain is empty
    existing = [
        {},
        {'foo@event1': {}},
        {'foo@event1': {'2025-06-10': []}},
    ]
    for value in existing:
        mock_existing.return_value = value
        resp = app.post_json('/api/regie/foo/basket/%s/lines/from-bookings/' % basket.uuid, params=params)
        assert resp.json['err'] == 0
        invoice.refresh_from_db()
        basket_line = basket.basketline_set.get()
        assert resp.json['data'] == {
            'line_id': str(basket_line.uuid),
            'closed': True,
            'line_total_amount': 0,
            'basket_total_amount': 0,
        }
        assert invoice.total_amount == 0
        assert invoice.lines.count() == 2
        lines = invoice.lines.order_by('pk')
        assert lines[0].unit_amount == 33
        assert lines[0].quantity == 1
        assert lines[0].description == 'Booking (regularization) 10/06'
        assert lines[0].details == {
            'dates': ['2025-06-10'],
            'adjustment': {'reason': 'missing-booking', '2025-06-10': {}},
        }
        assert lines[1].unit_amount == 33
        assert lines[1].quantity == -1
        assert lines[1].description == 'Cancellation 10/06'
        assert lines[1].details == {'dates': ['2025-06-10']}
        basket_line.items.all().delete()
        basket_line.delete()
        DraftInvoiceLine.objects.all().delete()

        params['payer_external_id'] = 'payer:1'
        resp = app.post_json('/api/regie/foo/basket/lines/from-bookings/dry-run/', params=params)
        params.pop('payer_external_id')
        assert resp.json['err'] == 0
        assert resp.json['data'] == {
            'basket': {
                'total_amount': 0.0,
                'lines': [
                    {
                        'label': 'Event 1',
                        'description': 'Booking (regularization) 10/06',
                        'unit_amount': 33.0,
                        'quantity': 1,
                        'total_amount': 33.0,
                    },
                    {
                        'label': 'Event 1',
                        'description': 'Cancellation 10/06',
                        'unit_amount': 33,
                        'quantity': -1,
                        'total_amount': -33,
                    },
                ],
                'payer_external_id': 'payer:1',
                'payer_name': 'First1 Last1',
            }
        }

    # chain is complete, and ends with a booking
    existing = [
        {
            'foo@event1': {
                '2025-06-10': [
                    Link(
                        payer_external_id='payer:1',
                        unit_amount=22,
                        booked=True,
                        invoicing_element_number='F-0002',
                    ),
                ]
            }
        },
        {
            'foo@event1': {
                '2025-06-10': [
                    Link(
                        payer_external_id='payer:1',
                        unit_amount=11,
                        booked=True,
                        invoicing_element_number='F-0001',
                    ),
                    Link(
                        payer_external_id='payer:1',
                        unit_amount=11,
                        booked=False,
                        invoicing_element_number='A-0001',
                    ),
                    Link(
                        payer_external_id='payer:1',
                        unit_amount=22,
                        booked=True,
                        invoicing_element_number='F-0002',
                    ),
                ]
            }
        },
    ]
    for value in existing:
        mock_existing.return_value = value
        resp = app.post_json('/api/regie/foo/basket/%s/lines/from-bookings/' % basket.uuid, params=params)
        assert resp.json['err'] == 0
        invoice.refresh_from_db()
        basket_line = basket.basketline_set.get()
        assert resp.json['data'] == {
            'line_id': str(basket_line.uuid),
            'closed': True,
            'line_total_amount': -22,
            'basket_total_amount': -22,
        }
        assert invoice.total_amount == -22
        assert invoice.lines.count() == 1
        lines = invoice.lines.order_by('pk')
        assert lines[0].unit_amount == 22
        assert lines[0].quantity == -1
        assert lines[0].description == 'Cancellation 10/06'
        assert lines[0].details == {'dates': ['2025-06-10']}
        basket_line.items.all().delete()
        basket_line.delete()
        DraftInvoiceLine.objects.all().delete()

        params['payer_external_id'] = 'payer:1'
        resp = app.post_json('/api/regie/foo/basket/lines/from-bookings/dry-run/', params=params)
        params.pop('payer_external_id')
        assert resp.json['err'] == 0
        assert resp.json['data'] == {
            'basket': {
                'total_amount': -22,
                'lines': [
                    {
                        'label': 'Event 1',
                        'description': 'Cancellation 10/06',
                        'unit_amount': 22,
                        'quantity': -1,
                        'total_amount': -22,
                    }
                ],
                'payer_external_id': 'payer:1',
                'payer_name': 'First1 Last1',
            }
        }

    # chain is complete, but ends with a cancellation
    existing = [
        {
            'foo@event1': {
                '2025-06-10': [
                    Link(
                        payer_external_id='payer:1',
                        unit_amount=11,
                        booked=True,
                        invoicing_element_number='F-0002',
                    ),
                    Link(
                        payer_external_id='payer:1',
                        unit_amount=11,
                        booked=False,
                        invoicing_element_number='A-0002',
                    ),
                ]
            }
        },
        {
            'foo@event1': {
                '2025-06-10': [
                    Link(
                        payer_external_id='payer:1',
                        unit_amount=11,
                        booked=True,
                        invoicing_element_number='F-0001',
                    ),
                    Link(
                        payer_external_id='payer:1',
                        unit_amount=11,
                        booked=False,
                        invoicing_element_number='A-0001',
                    ),
                    Link(
                        payer_external_id='payer:1',
                        unit_amount=22,
                        booked=True,
                        invoicing_element_number='F-0002',
                    ),
                    Link(
                        payer_external_id='payer:1',
                        unit_amount=22,
                        booked=False,
                        invoicing_element_number='A-0002',
                    ),
                ]
            }
        },
    ]
    for value in existing:
        mock_existing.return_value = value
        resp = app.post_json('/api/regie/foo/basket/%s/lines/from-bookings/' % basket.uuid, params=params)
        assert resp.json['err'] == 0
        invoice.refresh_from_db()
        basket_line = basket.basketline_set.get()
        assert resp.json['data'] == {
            'line_id': str(basket_line.uuid),
            'closed': True,
            'line_total_amount': 0,
            'basket_total_amount': 0,
        }
        assert invoice.total_amount == 0
        assert invoice.lines.count() == 2
        lines = invoice.lines.order_by('pk')
        assert lines[0].unit_amount == 33
        assert lines[0].quantity == 1
        assert lines[0].description == 'Booking (regularization) 10/06'
        assert lines[0].details == {
            'dates': ['2025-06-10'],
            'adjustment': {
                'reason': 'missing-booking',
                '2025-06-10': {'before': 'A-0002'},
            },
        }
        assert lines[1].unit_amount == 33
        assert lines[1].quantity == -1
        assert lines[1].description == 'Cancellation 10/06'
        assert lines[1].details == {'dates': ['2025-06-10']}
        basket_line.items.all().delete()
        basket_line.delete()
        DraftInvoiceLine.objects.all().delete()

        params['payer_external_id'] = 'payer:1'
        resp = app.post_json('/api/regie/foo/basket/lines/from-bookings/dry-run/', params=params)
        params.pop('payer_external_id')
        assert resp.json['err'] == 0
        assert resp.json['data'] == {
            'basket': {
                'total_amount': 0.0,
                'lines': [
                    {
                        'label': 'Event 1',
                        'description': 'Booking (regularization) 10/06',
                        'unit_amount': 33.0,
                        'quantity': 1,
                        'total_amount': 33.0,
                    },
                    {
                        'label': 'Event 1',
                        'description': 'Cancellation 10/06',
                        'unit_amount': 33,
                        'quantity': -1,
                        'total_amount': -33,
                    },
                ],
                'payer_external_id': 'payer:1',
                'payer_name': 'First1 Last1',
            }
        }

    # chain is not complete, a booking is missing
    mock_existing.return_value = {
        'foo@event1': {
            '2025-06-10': [
                Link(
                    payer_external_id='payer:1',
                    unit_amount=11,
                    booked=False,
                    invoicing_element_number='A-0001',
                ),
                Link(
                    payer_external_id='payer:1',
                    unit_amount=44,
                    booked=True,
                    invoicing_element_number='F-0001',
                ),
            ]
        }
    }
    resp = app.post_json('/api/regie/foo/basket/%s/lines/from-bookings/' % basket.uuid, params=params)
    assert resp.json['err'] == 0
    invoice.refresh_from_db()
    basket_line = basket.basketline_set.get()
    assert resp.json['data'] == {
        'line_id': str(basket_line.uuid),
        'closed': True,
        'line_total_amount': -33,
        'basket_total_amount': -33,
    }
    assert invoice.total_amount == -33
    assert invoice.lines.count() == 2
    lines = invoice.lines.order_by('pk')
    assert lines[0].unit_amount == 11
    assert lines[0].quantity == 1
    assert lines[0].description == 'Booking (regularization) 10/06'
    assert lines[0].details == {
        'dates': ['2025-06-10'],
        'adjustment': {
            'reason': 'missing-booking',
            '2025-06-10': {'after': 'A-0001'},
        },
    }
    assert lines[1].unit_amount == 44
    assert lines[1].quantity == -1
    assert lines[1].description == 'Cancellation 10/06'
    assert lines[1].details == {'dates': ['2025-06-10']}
    basket_line.items.all().delete()
    basket_line.delete()
    DraftInvoiceLine.objects.all().delete()

    params['payer_external_id'] = 'payer:1'
    resp = app.post_json('/api/regie/foo/basket/lines/from-bookings/dry-run/', params=params)
    params.pop('payer_external_id')
    assert resp.json['err'] == 0
    assert resp.json['data'] == {
        'basket': {
            'total_amount': -33,
            'lines': [
                {
                    'label': 'Event 1',
                    'description': 'Booking (regularization) 10/06',
                    'unit_amount': 11,
                    'quantity': 1,
                    'total_amount': 11,
                },
                {
                    'label': 'Event 1',
                    'description': 'Cancellation 10/06',
                    'unit_amount': 44,
                    'quantity': -1,
                    'total_amount': -44,
                },
            ],
            'payer_external_id': 'payer:1',
            'payer_name': 'First1 Last1',
        }
    }

    # chain is not complete, a cancellation is missing
    mock_existing.return_value = {
        'foo@event1': {
            '2025-06-10': [
                Link(
                    payer_external_id='payer:1',
                    unit_amount=11,
                    booked=True,
                    invoicing_element_number='F-0001',
                ),
                Link(
                    payer_external_id='payer:1',
                    unit_amount=22,
                    booked=True,
                    invoicing_element_number='F-0002',
                ),
            ]
        }
    }
    resp = app.post_json('/api/regie/foo/basket/%s/lines/from-bookings/' % basket.uuid, params=params)
    assert resp.json['err'] == 0
    invoice.refresh_from_db()
    basket_line = basket.basketline_set.get()
    assert resp.json['data'] == {
        'line_id': str(basket_line.uuid),
        'closed': True,
        'line_total_amount': -33,
        'basket_total_amount': -33,
    }
    assert invoice.total_amount == -33
    assert invoice.lines.count() == 2
    lines = invoice.lines.order_by('pk')
    assert lines[0].unit_amount == 11
    assert lines[0].quantity == -1
    assert lines[0].description == 'Cancellation (regularization) 10/06'
    assert lines[0].details == {
        'dates': ['2025-06-10'],
        'adjustment': {
            'reason': 'missing-cancellation',
            '2025-06-10': {
                'before': 'F-0001',
                'after': 'F-0002',
            },
        },
    }
    assert lines[1].unit_amount == 22
    assert lines[1].quantity == -1
    assert lines[1].description == 'Cancellation 10/06'
    assert lines[1].details == {'dates': ['2025-06-10']}
    basket_line.items.all().delete()
    basket_line.delete()
    DraftInvoiceLine.objects.all().delete()

    params['payer_external_id'] = 'payer:1'
    resp = app.post_json('/api/regie/foo/basket/lines/from-bookings/dry-run/', params=params)
    assert resp.json['err'] == 0
    assert resp.json['data'] == {
        'basket': {
            'total_amount': -33,
            'lines': [
                {
                    'label': 'Event 1',
                    'description': 'Cancellation (regularization) 10/06',
                    'unit_amount': 11,
                    'quantity': -1,
                    'total_amount': -11,
                },
                {
                    'label': 'Event 1',
                    'description': 'Cancellation 10/06',
                    'unit_amount': 22,
                    'quantity': -1,
                    'total_amount': -22,
                },
            ],
            'payer_external_id': 'payer:1',
            'payer_name': 'First1 Last1',
        }
    }

    # chain is not complete, amount inconsistancy
    mock_existing.return_value = {
        'foo@event1': {
            '2025-06-10': [
                Link(
                    payer_external_id='payer:1',
                    unit_amount=11,
                    booked=True,
                    invoicing_element_number='F-0001',
                ),
                Link(
                    payer_external_id='payer:1',
                    unit_amount=33,
                    booked=False,
                    invoicing_element_number='A-0001',
                ),
                Link(
                    payer_external_id='payer:1',
                    unit_amount=44,
                    booked=True,
                    invoicing_element_number='F-0002',
                ),
            ]
        }
    }
    resp = app.post_json('/api/regie/foo/basket/%s/lines/from-bookings/' % basket.uuid, params=params)
    assert resp.json['err'] == 0
    invoice.refresh_from_db()
    basket_line = basket.basketline_set.get()
    assert resp.json['data'] == {
        'line_id': str(basket_line.uuid),
        'closed': True,
        'line_total_amount': -22,
        'basket_total_amount': -22,
    }
    assert invoice.total_amount == -22
    assert invoice.lines.count() == 3
    lines = invoice.lines.order_by('pk')
    assert lines[0].unit_amount == 33
    assert lines[0].quantity == 1
    assert lines[0].description == 'Booking (regularization) 10/06'
    assert lines[0].details == {
        'dates': ['2025-06-10'],
        'adjustment': {
            'reason': 'missing-booking',
            '2025-06-10': {
                'before': 'F-0001',
                'after': 'A-0001',
            },
        },
    }
    assert lines[1].unit_amount == 11
    assert lines[1].quantity == -1
    assert lines[1].description == 'Cancellation (regularization) 10/06'
    assert lines[1].details == {
        'dates': ['2025-06-10'],
        'adjustment': {
            'reason': 'missing-cancellation',
            '2025-06-10': {
                'before': 'F-0001',
                'after': 'A-0001',
            },
        },
    }
    assert lines[2].unit_amount == 44
    assert lines[2].quantity == -1
    assert lines[2].description == 'Cancellation 10/06'
    assert lines[2].details == {'dates': ['2025-06-10']}
    basket_line.items.all().delete()
    basket_line.delete()
    DraftInvoiceLine.objects.all().delete()

    params['payer_external_id'] = 'payer:1'
    resp = app.post_json('/api/regie/foo/basket/lines/from-bookings/dry-run/', params=params)
    assert resp.json['err'] == 0
    assert resp.json['data'] == {
        'basket': {
            'total_amount': -22,
            'lines': [
                {
                    'label': 'Event 1',
                    'description': 'Booking (regularization) 10/06',
                    'unit_amount': 33,
                    'quantity': 1,
                    'total_amount': 33,
                },
                {
                    'label': 'Event 1',
                    'description': 'Cancellation (regularization) 10/06',
                    'unit_amount': 11,
                    'quantity': -1,
                    'total_amount': -11,
                },
                {
                    'label': 'Event 1',
                    'description': 'Cancellation 10/06',
                    'unit_amount': 44,
                    'quantity': -1,
                    'total_amount': -44,
                },
            ],
            'payer_external_id': 'payer:1',
            'payer_name': 'First1 Last1',
        }
    }


@mock.patch('lingo.api.views.utils.get_existing_lines_for_user')
@mock.patch('lingo.pricing.models.Pricing.get_pricing_data_for_event')
def test_from_bookings_with_adjustment_credits_for_other_payers(
    mock_pricing_data_event, mock_existing, app, user
):
    app.authorization = ('Basic', ('john.doe', 'password'))
    regie = Regie.objects.create(label='Foo')
    agenda1 = Agenda.objects.create(label='Foo', regie=regie)
    agenda2 = Agenda.objects.create(label='Bar', regie=regie)
    pricing = Pricing.objects.create(
        date_start=datetime.date(year=2025, month=6, day=1),
        date_end=datetime.date(year=2025, month=7, day=1),
    )
    pricing.agendas.add(agenda1, agenda2)
    invoice = DraftInvoice.objects.create(
        regie=regie,
        label='My invoice',
        date_publication=datetime.date(2023, 4, 21),
        date_payment_deadline=datetime.date(2023, 4, 22),
        date_due=datetime.date(2023, 4, 23),
    )
    basket = Basket.objects.create(regie=regie, draft_invoice=invoice, payer_external_id='payer:1')

    def get_payer_data(ap, r, payer_external_id):
        return {
            'payer:1': {
                'first_name': 'First1',
                'last_name': 'Last1',
                'address': '41 rue des kangourous\n99999 Kangourou Ville',
                'email': 'email1',
                'phone': 'phone1',
                'direct_debit': False,
            },
            'payer:2': {
                'first_name': 'First2',
                'last_name': 'Last2',
                'address': '42 rue des kangourous\n99999 Kangourou Ville',
                'email': 'email2',
                'phone': 'phone2',
                'direct_debit': True,
            },
        }.get(payer_external_id)

    mock_pricing_data_event.return_value = {'pricing': 33}

    params = {
        'user_external_id': 'user:1',
        'user_first_name': 'First',
        'user_last_name': 'Last',
        'information_message': 'foo baz',
        'cancel_information_message': 'foo bar',
        'form_url': 'http://form.com',
        'validation_callback_url': 'http://validation.com',
        'payment_callback_url': 'http://payment.com',
        'credit_callback_url': 'http://credit.com',
        'cancel_callback_url': 'http://cancel.com',
        'expiration_callback_url': 'http://expiration.com',
        'booked_events': [
            {
                'agenda_slug': 'foo',
                'slug': 'event1',
                'primary_event': None,
                'datetime': '2025-06-10 12:00:00',
                'label': 'Event 1',
            },
        ],
        'cancelled_events': [],
    }

    # chain is not complete, payer inconsistancy
    mock_existing.return_value = {
        'foo@event1': {
            '2025-06-10': [
                Link(
                    payer_external_id='payer:2',
                    unit_amount=11,
                    booked=True,
                    invoicing_element_number='F-0001',
                ),
                Link(
                    payer_external_id='payer:1',
                    unit_amount=11,
                    booked=False,
                    invoicing_element_number='A-0001',
                ),
            ]
        }
    }

    payer_data_patch = mock.patch.object(Regie, 'get_payer_data', autospec=True)
    with payer_data_patch as mock_payer_data:
        mock_payer_data.side_effect = get_payer_data
        resp = app.post_json('/api/regie/foo/basket/%s/lines/from-bookings/' % basket.uuid, params=params)
        assert resp.json['err'] == 0
        draft_invoice_payer2 = DraftInvoice.objects.get(payer_external_id='payer:2')
        assert list(basket.other_payer_credits_draft.all()) == [draft_invoice_payer2]
        invoice.refresh_from_db()
        basket_line = basket.basketline_set.get()
        assert resp.json['data'] == {
            'line_id': str(basket_line.uuid),
            'closed': True,
            'line_total_amount': 44,
            'basket_total_amount': 44,
            'other_payer_credit_draft_ids': [str(draft_invoice_payer2.uuid)],
        }
        assert invoice.total_amount == 44
        assert invoice.lines.count() == 2
        lines = invoice.lines.order_by('pk')
        assert lines[0].unit_amount == 11
        assert lines[0].quantity == 1
        assert lines[0].description == 'Booking (regularization) 10/06'
        assert lines[0].details == {
            'dates': ['2025-06-10'],
            'adjustment': {
                'reason': 'missing-booking',
                '2025-06-10': {
                    'before': 'F-0001',
                    'after': 'A-0001',
                },
            },
        }
        assert lines[1].unit_amount == 33
        assert lines[1].quantity == 1
        assert lines[1].description == 'Booking 10/06'
        assert lines[1].details == {'dates': ['2025-06-10']}

        assert draft_invoice_payer2.total_amount == -11
        assert draft_invoice_payer2.payer_external_id == 'payer:2'
        assert draft_invoice_payer2.lines.count() == 1
        lines = draft_invoice_payer2.lines.order_by('pk')
        assert lines[0].quantity == -1
        assert lines[0].unit_amount == 11
        assert lines[0].description == 'Cancellation (regularization) 10/06'
        assert lines[0].details == {
            'dates': ['2025-06-10'],
            'adjustment': {
                'reason': 'missing-cancellation',
                '2025-06-10': {
                    'before': 'F-0001',
                    'after': 'A-0001',
                },
            },
        }
        basket_line.items.all().delete()
        basket_line.delete()
        DraftInvoiceLine.objects.all().delete()

        params['payer_external_id'] = 'payer:1'
        resp = app.post_json('/api/regie/foo/basket/lines/from-bookings/dry-run/', params=params)
        params.pop('payer_external_id')
        assert resp.json['err'] == 0
        assert resp.json['data'] == {
            'basket': {
                'total_amount': 44,
                'lines': [
                    {
                        'label': 'Event 1',
                        'description': 'Booking (regularization) 10/06',
                        'unit_amount': 11,
                        'quantity': 1,
                        'total_amount': 11,
                    },
                    {
                        'label': 'Event 1',
                        'description': 'Booking 10/06',
                        'unit_amount': 33,
                        'quantity': 1,
                        'total_amount': 33,
                    },
                ],
                'payer_external_id': 'payer:1',
                'payer_name': 'First1 Last1',
            },
            'other_payer_credit_drafts': [
                {
                    'total_amount': 11,
                    'lines': [
                        {
                            'label': 'Event 1',
                            'description': 'Cancellation (regularization) 10/06',
                            'unit_amount': 11,
                            'quantity': 1,
                            'total_amount': 11,
                        }
                    ],
                    'payer_external_id': 'payer:2',
                    'payer_name': 'First2 Last2',
                }
            ],
        }


@mock.patch('lingo.api.views.utils.get_existing_lines_for_user')
@mock.patch('lingo.pricing.models.Pricing.get_pricing_data_for_event')
def test_from_bookings_with_adjustment_invoice_for_other_payers(
    mock_pricing_data_event, mock_existing, app, user
):
    app.authorization = ('Basic', ('john.doe', 'password'))
    regie = Regie.objects.create(label='Foo')
    agenda1 = Agenda.objects.create(label='Foo', regie=regie)
    agenda2 = Agenda.objects.create(label='Bar', regie=regie)
    pricing = Pricing.objects.create(
        date_start=datetime.date(year=2025, month=6, day=1),
        date_end=datetime.date(year=2025, month=7, day=1),
    )
    pricing.agendas.add(agenda1, agenda2)
    invoice = DraftInvoice.objects.create(
        regie=regie,
        label='My invoice',
        date_publication=datetime.date(2023, 4, 21),
        date_payment_deadline=datetime.date(2023, 4, 22),
        date_due=datetime.date(2023, 4, 23),
    )
    basket = Basket.objects.create(regie=regie, draft_invoice=invoice, payer_external_id='payer:1')

    def get_payer_data(ap, r, payer_external_id):
        return {
            'payer:1': {
                'first_name': 'First1',
                'last_name': 'Last1',
                'address': '41 rue des kangourous\n99999 Kangourou Ville',
                'email': 'email1',
                'phone': 'phone1',
                'direct_debit': False,
            },
            'payer:2': {
                'first_name': 'First2',
                'last_name': 'Last2',
                'address': '42 rue des kangourous\n99999 Kangourou Ville',
                'email': 'email2',
                'phone': 'phone2',
                'direct_debit': True,
            },
        }.get(payer_external_id)

    mock_pricing_data_event.return_value = {'pricing': 33}

    params = {
        'user_external_id': 'user:1',
        'user_first_name': 'First',
        'user_last_name': 'Last',
        'information_message': 'foo baz',
        'cancel_information_message': 'foo bar',
        'form_url': 'http://form.com',
        'validation_callback_url': 'http://validation.com',
        'payment_callback_url': 'http://payment.com',
        'credit_callback_url': 'http://credit.com',
        'cancel_callback_url': 'http://cancel.com',
        'expiration_callback_url': 'http://expiration.com',
        'booked_events': [
            {
                'agenda_slug': 'foo',
                'slug': 'event1',
                'primary_event': None,
                'datetime': '2025-06-10 12:00:00',
                'label': 'Event 1',
            },
        ],
        'cancelled_events': [],
    }

    # chain is not complete, payer inconsistancy
    mock_existing.return_value = {
        'foo@event1': {
            '2025-06-10': [
                Link(
                    payer_external_id='payer:1',
                    unit_amount=11,
                    booked=True,
                    invoicing_element_number='F-0001',
                ),
                Link(
                    payer_external_id='payer:2',
                    unit_amount=11,
                    booked=False,
                    invoicing_element_number='A-0001',
                ),
            ]
        }
    }

    payer_data_patch = mock.patch.object(Regie, 'get_payer_data', autospec=True)
    with payer_data_patch as mock_payer_data:
        mock_payer_data.side_effect = get_payer_data
        resp = app.post_json('/api/regie/foo/basket/%s/lines/from-bookings/' % basket.uuid, params=params)
        assert resp.json['err'] == 0
        invoice.refresh_from_db()
        basket_line = basket.basketline_set.get()
        assert resp.json['data'] == {
            'line_id': str(basket_line.uuid),
            'closed': True,
            'line_total_amount': 22,
            'basket_total_amount': 22,
        }
        assert invoice.total_amount == 22
        assert invoice.lines.count() == 2
        lines = invoice.lines.order_by('pk')
        assert lines[0].unit_amount == 33
        assert lines[0].quantity == 1
        assert lines[0].description == 'Booking 10/06'
        assert lines[0].details == {'dates': ['2025-06-10']}
        assert lines[1].unit_amount == 11
        assert lines[1].quantity == -1
        assert lines[1].description == 'Cancellation (regularization) 10/06'
        assert lines[1].details == {
            'dates': ['2025-06-10'],
            'adjustment': {
                'reason': 'missing-cancellation',
                '2025-06-10': {
                    'before': 'F-0001',
                    'after': 'A-0001',
                },
            },
        }
        basket_line.items.all().delete()
        basket_line.delete()
        DraftInvoiceLine.objects.all().delete()

        params['payer_external_id'] = 'payer:1'
        resp = app.post_json('/api/regie/foo/basket/lines/from-bookings/dry-run/', params=params)
        assert resp.json['err'] == 0
        assert resp.json['data'] == {
            'basket': {
                'total_amount': 22,
                'lines': [
                    {
                        'label': 'Event 1',
                        'description': 'Booking 10/06',
                        'unit_amount': 33,
                        'quantity': 1,
                        'total_amount': 33,
                    },
                    {
                        'label': 'Event 1',
                        'description': 'Cancellation (regularization) 10/06',
                        'unit_amount': 11,
                        'quantity': -1,
                        'total_amount': -11,
                    },
                ],
                'payer_external_id': 'payer:1',
                'payer_name': 'First1 Last1',
            }
        }
