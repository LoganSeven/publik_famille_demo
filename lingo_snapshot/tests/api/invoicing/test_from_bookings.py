import copy
import datetime
from unittest import mock

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils.timezone import now

from lingo.agendas.models import Agenda
from lingo.invoicing.errors import PayerDataError
from lingo.invoicing.models import (
    Campaign,
    Credit,
    CreditAssignment,
    CreditLine,
    DraftInvoice,
    Invoice,
    InvoiceLine,
    Payment,
    Pool,
    Regie,
)
from lingo.invoicing.utils import Link
from lingo.pricing.errors import PricingError
from lingo.pricing.models import Pricing

pytestmark = pytest.mark.django_db


def test_from_bookings_errors(app, user):
    app.post_json('/api/regie/foo/from-bookings/', status=403)
    app.post_json('/api/regie/foo/from-bookings/dry-run/', status=403)
    app.authorization = ('Basic', ('john.doe', 'password'))

    resp = app.post_json('/api/regie/foo/from-bookings/', status=404)
    resp = app.post_json('/api/regie/foo/from-bookings/dry-run/', status=404)
    regie = Regie.objects.create(label='Foo')
    resp = app.post_json('/api/regie/foo/from-bookings/', status=400)
    assert resp.json['err'] == 1
    assert resp.json['err_class'] == 'invalid payload'
    assert resp.json['errors'] == {
        'date_due': ['This field is required.'],
        'date_payment_deadline': ['This field is required.'],
        'date_publication': ['This field is required.'],
        'label': ['This field is required.'],
        'user_external_id': ['This field is required.'],
        'user_first_name': ['This field is required.'],
        'user_last_name': ['This field is required.'],
        'payer_external_id': ['This field is required.'],
        'booked_events': ['This field is required.'],
        'cancelled_events': ['This field is required.'],
    }
    resp = app.post_json('/api/regie/foo/from-bookings/dry-run/', status=400)
    assert resp.json['err'] == 1
    assert resp.json['err_class'] == 'invalid payload'
    assert resp.json['errors'] == {
        'user_external_id': ['This field is required.'],
        'payer_external_id': ['This field is required.'],
        'booked_events': ['This field is required.'],
        'cancelled_events': ['This field is required.'],
    }

    params = {
        'date_due': '2023-04-23',
        'date_payment_deadline': '2023-04-22',
        'date_publication': '2023-04-21',
        'label': 'Foo Bar',
        'user_external_id': 'user:1',
        'user_first_name': 'First',
        'user_last_name': 'Last',
        'payer_external_id': 'payer:1',
        'form_url': 'http://form.com',
        'payment_callback_url': 'http://payment.com',
        'cancel_callback_url': 'http://cancel.com',
        'booked_events': [],
        'cancelled_events': [],
    }
    resp = app.post_json('/api/regie/foo/from-bookings/', params=params, status=400)
    assert resp.json['err'] == 1
    assert resp.json['err_class'] == 'no changes'
    assert Invoice.objects.count() == 0
    assert Credit.objects.count() == 0
    resp = app.post_json('/api/regie/foo/from-bookings/dry-run/', params=params, status=400)
    assert resp.json['err'] == 1
    assert resp.json['err_class'] == 'no changes'

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
    agenda = Agenda.objects.create(label='unknown')
    resp = app.post_json('/api/regie/foo/from-bookings/', params=params, status=400)
    assert resp.json['err'] == 1
    assert resp.json['err_class'] == 'wrong regie for agendas: unknown'
    assert Invoice.objects.count() == 0
    assert Credit.objects.count() == 0
    resp = app.post_json('/api/regie/foo/from-bookings/dry-run/', params=params, status=400)
    assert resp.json['err'] == 1
    assert resp.json['err_class'] == 'wrong regie for agendas: unknown'

    Agenda.objects.create(label='foo', regie=regie)
    other_regie = Regie.objects.create(label='Other Foo')
    agenda.regie = other_regie
    agenda.save()
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
    resp = app.post_json('/api/regie/foo/from-bookings/', params=params, status=400)
    assert resp.json['err'] == 1
    assert resp.json['err_class'] == 'wrong regie for agendas: unknown, unknown2'
    assert Invoice.objects.count() == 0
    assert Credit.objects.count() == 0
    resp = app.post_json('/api/regie/foo/from-bookings/dry-run/', params=params, status=400)
    assert resp.json['err'] == 1
    assert resp.json['err_class'] == 'wrong regie for agendas: unknown, unknown2'


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
        PricingError(details={'foo': 'baz'}),
    ]
    params = {
        'date_due': '2023-04-23',
        'date_payment_deadline': '2023-04-22',
        'date_publication': '2023-04-21',
        'label': 'Foo Bar',
        'user_external_id': 'user:1',
        'user_first_name': 'First',
        'user_last_name': 'Last',
        'payer_external_id': 'payer:1',
        'form_url': 'http://form.com',
        'payment_callback_url': 'http://payment.com',
        'cancel_callback_url': 'http://cancel.com',
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

    resp = app.post_json('/api/regie/foo/from-bookings/', params=params, status=400)
    assert resp.json['err'] == 1
    assert resp.json['err_class'] == "error: PricingError, details: {'foo': 'bar'}"
    assert Invoice.objects.count() == 0
    assert Credit.objects.count() == 0
    resp = app.post_json('/api/regie/foo/from-bookings/dry-run/', params=params, status=400)
    assert resp.json['err'] == 1
    assert resp.json['err_class'] == "error: PricingError, details: {'foo': 'baz'}"

    mock_pricing_data_event.side_effect = [
        {'foo1': 'bar1', 'pricing': 1},
        PricingError(details={'foo': 'bar'}),
        # dry-run
        {'foo1': 'bar1', 'pricing': 1},
        PricingError(details={'foo': 'baz'}),
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

    resp = app.post_json('/api/regie/foo/from-bookings/', params=params, status=400)
    assert resp.json['err'] == 1
    assert resp.json['err_class'] == "error: PricingError, details: {'foo': 'bar'}"
    assert Invoice.objects.count() == 0
    assert Credit.objects.count() == 0
    resp = app.post_json('/api/regie/foo/from-bookings/dry-run/', params=params, status=400)
    assert resp.json['err'] == 1
    assert resp.json['err_class'] == "error: PricingError, details: {'foo': 'baz'}"


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

    mock_pricing_data_event.return_value = {'foo1': 'bar1', 'pricing': 1, 'accounting_code': '414141'}
    mock_payer_data.side_effect = PayerDataError(details={'key': 'foobar', 'reason': 'foo'})
    params = {
        'date_due': '2023-04-23',
        'date_payment_deadline': '2023-04-22',
        'date_publication': '2023-04-21',
        'label': 'Foo Bar',
        'user_external_id': 'user:1',
        'user_first_name': 'First',
        'user_last_name': 'Last',
        'payer_external_id': 'payer:1',
        'form_url': 'http://form.com',
        'payment_callback_url': 'http://payment.com',
        'cancel_callback_url': 'http://cancel.com',
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

    resp = app.post_json('/api/regie/foo/from-bookings/', params=params, status=400)
    assert resp.json['err'] == 1
    assert resp.json['err_class'] == "error: PayerDataError, details: {'key': 'foobar', 'reason': 'foo'}"
    assert Invoice.objects.count() == 0
    assert Credit.objects.count() == 0
    resp = app.post_json('/api/regie/foo/from-bookings/dry-run/', params=params, status=400)
    assert resp.json['err'] == 1
    assert resp.json['err_class'] == "error: PayerDataError, details: {'key': 'foobar', 'reason': 'foo'}"

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

    resp = app.post_json('/api/regie/foo/from-bookings/', params=params, status=400)
    assert resp.json['err'] == 1
    assert resp.json['err_class'] == "error: PayerDataError, details: {'key': 'foobar', 'reason': 'foo'}"
    assert Invoice.objects.count() == 0
    assert Credit.objects.count() == 0
    resp = app.post_json('/api/regie/foo/from-bookings/dry-run/', params=params, status=400)
    assert resp.json['err'] == 1
    assert resp.json['err_class'] == "error: PayerDataError, details: {'key': 'foobar', 'reason': 'foo'}"


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

    pricing_data_event_patch = mock.patch.object(Pricing, 'get_pricing_data_for_event', autospec=True)
    mock_payer_data.return_value = {
        'first_name': 'First1',
        'last_name': 'Last1',
        'address': '41 rue des kangourous\n99999 Kangourou Ville',
        'email': 'email1',
        'phone': 'phone1',
    }

    params = {
        'date_due': '2023-04-23',
        'date_payment_deadline': '2023-04-22',
        'date_publication': '2023-04-21',
        'label': 'Foo Bar',
        'user_external_id': 'user:1',
        'user_first_name': 'First',
        'user_last_name': 'Last',
        'payer_external_id': 'payer:1',
        'form_url': 'http://form.com',
        'payment_callback_url': 'http://payment.com',
        'cancel_callback_url': 'http://cancel.com',
        'booked_events': [],
        'cancelled_events': [],
    }

    # check agenda pricing of september is used
    for event_date in ['2025-09-01 12:00:00', '2025-09-30 12:00:00']:
        InvoiceLine.objects.all().delete()
        Invoice.objects.all().delete()
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
            resp = app.post_json('/api/regie/foo/from-bookings/', params=params)
            assert resp.json['err'] == 0
            assert Invoice.objects.count() == 1
            assert InvoiceLine.objects.count() == 1
            assert mock_pricing_data_event.call_args_list[0][0][0] == pricing1
            resp = app.post_json('/api/regie/foo/from-bookings/dry-run/', params=params)
            assert resp.json['err'] == 0
            assert mock_pricing_data_event.call_args_list[0][0][0] == pricing1

    # check agenda pricing of october is used
    for event_date in ['2025-10-01 12:00:00', '2025-10-31 12:00:00']:
        InvoiceLine.objects.all().delete()
        Invoice.objects.all().delete()
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
            resp = app.post_json('/api/regie/foo/from-bookings/', params=params)
            assert resp.json['err'] == 0
            assert Invoice.objects.count() == 1
            assert InvoiceLine.objects.count() == 1
            assert mock_pricing_data_event.call_args_list[0][0][0] == pricing3
            resp = app.post_json('/api/regie/foo/from-bookings/dry-run/', params=params)
            assert resp.json['err'] == 0
            assert mock_pricing_data_event.call_args_list[0][0][0] == pricing3

    # no matching agenda pricing
    for event_date in ['2025-08-31 12:00:00', '2025-11-01 12:00:00']:
        InvoiceLine.objects.all().delete()
        Invoice.objects.all().delete()
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
            resp = app.post_json('/api/regie/foo/from-bookings/', params=params, status=400)
            assert resp.json['err'] == 1
            assert resp.json['err_class'] == 'error: PricingNotFound, details: {}'
            assert mock_pricing_data_event.call_args_list == []
            resp = app.post_json('/api/regie/foo/from-bookings/dry-run/', params=params, status=400)
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
        'date_due': now().date().isoformat(),
        'date_payment_deadline_displayed': '2023-04-20',
        'date_payment_deadline': '2023-04-22',
        'date_publication': '2023-04-21',
        'date_invoicing': '2025-06-01',
        'label': 'Foo Bar',
        'user_external_id': 'user:1',
        'user_first_name': 'First',
        'user_last_name': 'Last',
        'payer_external_id': 'payer:1',
        'form_url': 'http://form.com',
        'payment_callback_url': 'http://payment.com',
        'cancel_callback_url': 'http://cancel.com',
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
        resp = app.post_json('/api/regie/foo/from-bookings/', params=params)
        assert len(ctx.captured_queries) == 31
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
    invoice = Invoice.objects.latest('pk')
    assert resp.json['data'] == {
        'invoice_id': f'{invoice.uuid}',
        'invoice': {
            'id': f'{invoice.uuid}',
            'total_amount': 3,
            'remaining_amount': 3,
        },
        'api_urls': {
            'invoice_pdf': f'http://testserver/api/regie/foo/invoice/{invoice.uuid}/pdf/',
        },
        'urls': {
            'invoice_in_backoffice': f'http://testserver/manage/invoicing/redirect/invoice/{invoice.uuid}/',
            'invoice_pdf': f'http://testserver/manage/invoicing/redirect/invoice/{invoice.uuid}/pdf/',
        },
    }
    assert invoice.label == 'Foo Bar'
    assert invoice.total_amount == 3
    assert invoice.paid_amount == 0
    assert invoice.date_publication == datetime.date(2023, 4, 21)
    assert invoice.date_payment_deadline == datetime.date(2023, 4, 22)
    assert invoice.date_payment_deadline_displayed == datetime.date(2023, 4, 20)
    assert invoice.date_due == now().date()
    assert invoice.date_debit is None
    assert invoice.date_invoicing == datetime.date(2025, 6, 1)
    assert invoice.regie == regie
    assert invoice.payer_external_id == 'payer:1'
    assert invoice.payer_first_name == 'First1'
    assert invoice.payer_last_name == 'Last1'
    assert invoice.payer_address == '41 rue des kangourous\n99999 Kangourou Ville'
    assert invoice.payer_email == 'email1'
    assert invoice.payer_phone == 'phone1'
    assert invoice.payer_direct_debit is False
    assert invoice.pool is None
    assert invoice.payment_callback_url == 'http://payment.com'
    assert invoice.cancel_callback_url == 'http://cancel.com'
    assert invoice.previous_invoice is None
    assert invoice.origin == 'api'
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
    mock_pricing_data_event.reset_mock()
    mock_existing.reset_mock()
    with CaptureQueriesContext(connection) as ctx:
        resp = app.post_json('/api/regie/foo/from-bookings/dry-run/', params=params)
        assert len(ctx.captured_queries) == 7
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
        'invoice': {
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

    params.pop('date_payment_deadline_displayed')
    params.pop('date_invoicing')
    params.pop('form_url')

    # with cancellations but amount > 0 => invoice
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
    resp = app.post_json('/api/regie/foo/from-bookings/', params=params)
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
    invoice = Invoice.objects.latest('pk')
    assert resp.json['data'] == {
        'invoice_id': f'{invoice.uuid}',
        'invoice': {
            'id': f'{invoice.uuid}',
            'total_amount': 1,
            'remaining_amount': 1,
        },
        'api_urls': {
            'invoice_pdf': f'http://testserver/api/regie/foo/invoice/{invoice.uuid}/pdf/',
        },
        'urls': {
            'invoice_in_backoffice': f'http://testserver/manage/invoicing/redirect/invoice/{invoice.uuid}/',
            'invoice_pdf': f'http://testserver/manage/invoicing/redirect/invoice/{invoice.uuid}/pdf/',
        },
    }
    assert invoice.label == 'Foo Bar'
    assert invoice.total_amount == 1
    assert invoice.paid_amount == 0
    assert invoice.date_payment_deadline_displayed is None
    assert invoice.date_invoicing is None
    assert invoice.payer_external_id == 'payer:1'
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
    assert lines[0].form_url == ''
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
    assert lines[1].form_url == ''
    resp = app.post_json('/api/regie/foo/from-bookings/dry-run/', params=params)
    assert resp.json['err'] == 0
    assert resp.json['data'] == {
        'invoice': {
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

    # with cancellations and 0 amount => invoice
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
    with mock.patch('lingo.utils.requests_wrapper.RequestsSession.send') as mock_send:
        resp = app.post_json('/api/regie/foo/from-bookings/', params=params)
    assert [x[0][0].url for x in mock_send.call_args_list] == ['http://payment.com/']
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
    invoice = Invoice.objects.latest('pk')
    assert resp.json['data'] == {
        'invoice_id': f'{invoice.uuid}',
        'invoice': {
            'id': f'{invoice.uuid}',
            'total_amount': 0,
            'remaining_amount': 0,
        },
        'api_urls': {
            'invoice_pdf': f'http://testserver/api/regie/foo/invoice/{invoice.uuid}/pdf/',
        },
        'urls': {
            'invoice_in_backoffice': f'http://testserver/manage/invoicing/redirect/invoice/{invoice.uuid}/',
            'invoice_pdf': f'http://testserver/manage/invoicing/redirect/invoice/{invoice.uuid}/pdf/',
        },
    }
    assert invoice.label == 'Foo Bar'
    assert invoice.total_amount == 0
    assert invoice.paid_amount == 0
    assert invoice.payer_external_id == 'payer:1'
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
    assert lines[0].form_url == ''
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
    assert lines[1].form_url == ''
    resp = app.post_json('/api/regie/foo/from-bookings/dry-run/', params=params)
    assert resp.json['err'] == 0
    assert resp.json['data'] == {
        'invoice': {
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

    # invoice with credits, assignments
    credit1 = Credit.objects.create(
        label='Credit from 01/09/2022',
        date_publication=datetime.date(2022, 10, 1),
        regie=regie,
        payer_external_id='payer:1',
    )
    CreditLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        credit=credit1,
        quantity=2,
        unit_amount=1,
    )
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
    )
    credit2 = Credit.objects.create(
        label='Credit from 01/09/2022',
        date_publication=datetime.date(2022, 10, 1),
        regie=regie,
        payer_external_id='payer:1',
        pool=pool,
    )
    CreditLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        credit=credit2,
        quantity=2,
        unit_amount=1,
    )
    credit3 = Credit.objects.create(
        label='Credit from 01/09/2022',
        date_publication=datetime.date(2022, 10, 1),
        regie=regie,
        payer_external_id='payer:42',  # wrong payer
    )
    CreditLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        credit=credit3,
        quantity=5,
        unit_amount=1,
    )
    other_regie = Regie.objects.create(label='Other Foo')
    other_credit = Credit.objects.create(
        label='Credit from 01/09/2022',
        date_publication=datetime.date(2022, 10, 1),
        regie=other_regie,  # other regie
        payer_external_id='payer:1',
    )
    CreditLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        credit=other_credit,
        quantity=5,
        unit_amount=1,
    )
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
    other_credit = Credit.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        regie=regie,
        payer_external_id='payer:1',
        pool=pool,  # not finalized pool
    )
    CreditLine.objects.create(
        credit=other_credit,
        event_date=datetime.date(2022, 9, 1),
        quantity=5,
        unit_amount=1,
    )
    other_credit = Credit.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        regie=regie,
        payer_external_id='payer:1',
        cancelled_at=now(),  # cancelled
    )
    CreditLine.objects.create(
        credit=other_credit,
        event_date=datetime.date(2022, 9, 1),
        quantity=5,
        unit_amount=1,
    )
    other_credit = Credit.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        regie=regie,
        payer_external_id='payer:1',
        usable=False,  # not usable to pay invoices
    )
    CreditLine.objects.create(
        credit=other_credit,
        event_date=datetime.date(2022, 9, 1),
        quantity=5,
        unit_amount=1,
    )
    mock_pricing_data_event.reset_mock()
    mock_pricing_data_event.side_effect = [
        {'foo1': 'bar1', 'pricing': 1, 'accounting_code': '414141'},
        {'foo2': 'bar2', 'pricing': 2, 'accounting_code': '424242'},
    ]
    mock_existing.return_value = {}
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
                {
                    'agenda_slug': 'bar',
                    'slug': 'event2',
                    'primary_event': None,
                    'datetime': '2025-06-11 15:00:00',
                    'label': 'Event 2',
                },
            ],
            'cancelled_events': [],
        }
    )
    with mock.patch('lingo.utils.requests_wrapper.RequestsSession.send') as mock_send:
        resp = app.post_json('/api/regie/foo/from-bookings/', params=params)
    assert [x[0][0].url for x in mock_send.call_args_list] == ['http://payment.com/']
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
    invoice = Invoice.objects.latest('pk')
    assert resp.json['data'] == {
        'invoice_id': f'{invoice.uuid}',
        'invoice': {
            'id': f'{invoice.uuid}',
            'total_amount': 3,
            'remaining_amount': 0,
        },
        'api_urls': {
            'invoice_pdf': f'http://testserver/api/regie/foo/invoice/{invoice.uuid}/pdf/',
        },
        'urls': {
            'invoice_in_backoffice': f'http://testserver/manage/invoicing/redirect/invoice/{invoice.uuid}/',
            'invoice_pdf': f'http://testserver/manage/invoicing/redirect/invoice/{invoice.uuid}/pdf/',
        },
    }
    assert invoice.label == 'Foo Bar'
    assert invoice.total_amount == 3
    assert invoice.paid_amount == 3
    assert invoice.payer_external_id == 'payer:1'
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
    assert lines[0].form_url == ''
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
    assert lines[1].form_url == ''
    credit1.refresh_from_db()
    assert credit1.remaining_amount == 0
    assert credit1.assigned_amount == 2
    credit2.refresh_from_db()
    assert credit2.remaining_amount == 1
    assert credit2.assigned_amount == 1
    assert CreditAssignment.objects.count() == 2
    assignment1, assignment2 = CreditAssignment.objects.all().order_by('pk')
    assert assignment1.amount == 2
    assert assignment1.invoice == invoice
    assert assignment1.credit == credit1
    assert assignment2.amount == 1
    assert assignment2.invoice == invoice
    assert assignment2.credit == credit2
    assert Payment.objects.count() == 2
    payment1, payment2 = Payment.objects.all().order_by('pk')
    assert payment1.amount == 2
    assert payment1.payment_type.slug == 'credit'
    assert payment2.amount == 1
    assert payment2.payment_type.slug == 'credit'
    assert assignment1.payment == payment1
    assert assignment2.payment == payment2
    assert payment1.invoicelinepayment_set.count() == 2
    (invoicelinepayment11, invoicelinepayment12) = payment1.invoicelinepayment_set.order_by('pk')
    assert invoicelinepayment11.line == invoice.lines.order_by('pk')[0]
    assert invoicelinepayment11.amount == 1
    assert invoicelinepayment12.line == invoice.lines.order_by('pk')[1]
    assert invoicelinepayment12.amount == 1
    assert payment2.invoicelinepayment_set.count() == 1
    (invoicelinepayment21,) = payment2.invoicelinepayment_set.order_by('pk')
    assert invoicelinepayment21.line == invoice.lines.order_by('pk')[1]
    assert invoicelinepayment21.amount == 1

    mock_pricing_data_event.reset_mock()
    mock_pricing_data_event.side_effect = [
        {'foo1': 'bar1', 'pricing': 1, 'accounting_code': '414141'},
        {'foo2': 'bar2', 'pricing': 2, 'accounting_code': '424242'},
    ]
    resp = app.post_json('/api/regie/foo/from-bookings/', params=params)
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
    invoice = Invoice.objects.latest('pk')
    assert resp.json['data'] == {
        'invoice_id': f'{invoice.uuid}',
        'invoice': {
            'id': f'{invoice.uuid}',
            'total_amount': 3,
            'remaining_amount': 2,
        },
        'api_urls': {
            'invoice_pdf': f'http://testserver/api/regie/foo/invoice/{invoice.uuid}/pdf/',
        },
        'urls': {
            'invoice_in_backoffice': f'http://testserver/manage/invoicing/redirect/invoice/{invoice.uuid}/',
            'invoice_pdf': f'http://testserver/manage/invoicing/redirect/invoice/{invoice.uuid}/pdf/',
        },
    }
    assert invoice.label == 'Foo Bar'
    assert invoice.total_amount == 3
    assert invoice.paid_amount == 1
    assert invoice.payer_external_id == 'payer:1'
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
    assert lines[0].form_url == ''
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
    assert lines[1].form_url == ''
    credit2.refresh_from_db()
    assert credit2.remaining_amount == 0
    assert credit2.assigned_amount == 2
    assert CreditAssignment.objects.count() == 3
    assignment3 = CreditAssignment.objects.latest('pk')
    assert assignment3.amount == 1
    assert assignment3.invoice == invoice
    assert assignment3.credit == credit2
    assert Payment.objects.count() == 3
    payment3 = Payment.objects.latest('pk')
    assert payment3.amount == 1
    assert payment3.payment_type.slug == 'credit'
    assert assignment3.payment == payment3
    assert payment3.invoicelinepayment_set.count() == 1
    (invoicelinepayment31,) = payment3.invoicelinepayment_set.order_by('pk')
    assert invoicelinepayment31.line == invoice.lines.order_by('pk')[0]
    assert invoicelinepayment31.amount == 1

    # only cancellations => credit
    Invoice.objects.all().update(
        date_due=datetime.date(2022, 1, 1)
    )  # invoices will be not assigned, date due is past
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
    resp = app.post_json('/api/regie/foo/from-bookings/', params=params)
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
    credit = Credit.objects.latest('pk')
    assert resp.json['data'] == {
        'credit_id': f'{credit.uuid}',
        'credit': {
            'id': f'{credit.uuid}',
            'total_amount': 3,
        },
        'api_urls': {
            'credit_pdf': f'http://testserver/api/regie/foo/credit/{credit.uuid}/pdf/',
        },
        'urls': {
            'credit_in_backoffice': f'http://testserver/manage/invoicing/redirect/credit/{credit.uuid}/',
            'credit_pdf': f'http://testserver/manage/invoicing/redirect/credit/{credit.uuid}/pdf/',
        },
    }
    assert credit.label == 'Credit from %s' % now().date().strftime('%d/%m/%Y')
    assert credit.total_amount == 3
    assert credit.assigned_amount == 0
    assert credit.date_publication == datetime.date(2023, 4, 21)
    assert credit.date_invoicing is None
    assert credit.regie == regie
    assert credit.payer_external_id == 'payer:1'
    assert credit.payer_first_name == 'First1'
    assert credit.payer_last_name == 'Last1'
    assert credit.payer_address == '41 rue des kangourous\n99999 Kangourou Ville'
    assert credit.payer_email == 'email1'
    assert credit.payer_phone == 'phone1'
    assert credit.pool is None
    assert credit.previous_invoice is None
    assert credit.origin == 'api'
    assert credit.lines.count() == 2
    lines = credit.lines.order_by('pk')
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
    assert lines[0].description == 'Cancellation 10/06'
    assert lines[0].accounting_code == '414141'
    assert lines[0].user_external_id == 'user:1'
    assert lines[0].user_first_name == 'First'
    assert lines[0].user_last_name == 'Last'
    assert lines[0].pool is None
    assert lines[0].form_url == ''
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
    assert lines[1].description == 'Cancellation 11/06'
    assert lines[1].accounting_code == '424242'
    assert lines[1].user_external_id == 'user:1'
    assert lines[1].user_first_name == 'First'
    assert lines[1].user_last_name == 'Last'
    assert lines[1].pool is None
    assert lines[1].form_url == ''
    resp = app.post_json('/api/regie/foo/from-bookings/dry-run/', params=params)
    assert resp.json['err'] == 0
    assert resp.json['data'] == {
        'credit': {
            'total_amount': 3,
            'lines': [
                {
                    'label': 'Event 1',
                    'description': 'Cancellation 10/06',
                    'unit_amount': 1,
                    'quantity': 1,
                    'total_amount': 1,
                },
                {
                    'label': 'Event 2',
                    'description': 'Cancellation 11/06',
                    'unit_amount': 2,
                    'quantity': 1,
                    'total_amount': 2,
                },
            ],
            'payer_external_id': 'payer:1',
            'payer_name': 'First1 Last1',
        }
    }

    # bookings and cancellations => credit
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
    resp = app.post_json('/api/regie/foo/from-bookings/', params=params)
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
    credit = Credit.objects.latest('pk')
    assert resp.json['data'] == {
        'credit_id': f'{credit.uuid}',
        'credit': {
            'id': f'{credit.uuid}',
            'total_amount': 1,
        },
        'api_urls': {
            'credit_pdf': f'http://testserver/api/regie/foo/credit/{credit.uuid}/pdf/',
        },
        'urls': {
            'credit_in_backoffice': f'http://testserver/manage/invoicing/redirect/credit/{credit.uuid}/',
            'credit_pdf': f'http://testserver/manage/invoicing/redirect/credit/{credit.uuid}/pdf/',
        },
    }
    assert credit.label == 'Credit from %s' % now().date().strftime('%d/%m/%Y')
    assert credit.total_amount == 1
    assert credit.assigned_amount == 0
    assert credit.date_publication == datetime.date(2023, 4, 21)
    assert credit.date_invoicing is None
    assert credit.regie == regie
    assert credit.payer_external_id == 'payer:1'
    assert credit.payer_first_name == 'First1'
    assert credit.payer_last_name == 'Last1'
    assert credit.payer_address == '41 rue des kangourous\n99999 Kangourou Ville'
    assert credit.payer_email == 'email1'
    assert credit.payer_phone == 'phone1'
    assert credit.pool is None
    assert credit.previous_invoice is None
    assert credit.origin == 'api'
    assert credit.lines.count() == 2
    lines = credit.lines.order_by('pk')
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
    assert lines[0].description == 'Booking 10/06'
    assert lines[0].accounting_code == '414141'
    assert lines[0].user_external_id == 'user:1'
    assert lines[0].user_first_name == 'First'
    assert lines[0].user_last_name == 'Last'
    assert lines[0].pool is None
    assert lines[0].form_url == ''
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
    assert lines[1].description == 'Cancellation 11/06'
    assert lines[1].accounting_code == '424242'
    assert lines[1].user_external_id == 'user:1'
    assert lines[1].user_first_name == 'First'
    assert lines[1].user_last_name == 'Last'
    assert lines[1].pool is None
    assert lines[1].form_url == ''
    resp = app.post_json('/api/regie/foo/from-bookings/dry-run/', params=params)
    assert resp.json['err'] == 0
    assert resp.json['data'] == {
        'credit': {
            'total_amount': 1,
            'lines': [
                {
                    'label': 'Event 1',
                    'description': 'Booking 10/06',
                    'unit_amount': 1,
                    'quantity': -1,
                    'total_amount': -1,
                },
                {
                    'label': 'Event 2',
                    'description': 'Cancellation 11/06',
                    'unit_amount': 2,
                    'quantity': 1,
                    'total_amount': 2,
                },
            ],
            'payer_external_id': 'payer:1',
            'payer_name': 'First1 Last1',
        }
    }

    # credit assigned to invoice
    Invoice.objects.all().update(date_due=now().date())  # invoices will be assigned
    mock_pricing_data_event.reset_mock()
    mock_pricing_data_event.side_effect = [
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
    resp = app.post_json('/api/regie/foo/from-bookings/', params=params)
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
    credit = Credit.objects.latest('pk')
    assert resp.json['data'] == {
        'credit_id': f'{credit.uuid}',
        'credit': {
            'id': f'{credit.uuid}',
            'total_amount': 3,
        },
        'api_urls': {
            'credit_pdf': f'http://testserver/api/regie/foo/credit/{credit.uuid}/pdf/',
        },
        'urls': {
            'credit_in_backoffice': f'http://testserver/manage/invoicing/redirect/credit/{credit.uuid}/',
            'credit_pdf': f'http://testserver/manage/invoicing/redirect/credit/{credit.uuid}/pdf/',
        },
    }
    assert credit.label == 'Credit from %s' % now().date().strftime('%d/%m/%Y')
    assert credit.total_amount == 3
    assert credit.assigned_amount == 3
    assert credit.date_publication == datetime.date(2023, 4, 21)
    assert credit.date_invoicing is None
    assert credit.regie == regie
    assert credit.payer_external_id == 'payer:1'
    assert credit.payer_first_name == 'First1'
    assert credit.payer_last_name == 'Last1'
    assert credit.payer_address == '41 rue des kangourous\n99999 Kangourou Ville'
    assert credit.payer_email == 'email1'
    assert credit.payer_phone == 'phone1'
    assert credit.pool is None
    assert credit.previous_invoice is None
    assert credit.origin == 'api'
    assert credit.lines.count() == 2
    lines = credit.lines.order_by('pk')
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
    assert lines[0].description == 'Cancellation 10/06'
    assert lines[0].accounting_code == '414141'
    assert lines[0].user_external_id == 'user:1'
    assert lines[0].user_first_name == 'First'
    assert lines[0].user_last_name == 'Last'
    assert lines[0].pool is None
    assert lines[0].form_url == ''
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
    assert lines[1].description == 'Cancellation 11/06'
    assert lines[1].accounting_code == '424242'
    assert lines[1].user_external_id == 'user:1'
    assert lines[1].user_first_name == 'First'
    assert lines[1].user_last_name == 'Last'
    assert lines[1].pool is None
    assert lines[1].form_url == ''
    invoice1 = Invoice.objects.order_by('pk')[0]
    assert CreditAssignment.objects.count() == 4
    assignment4 = CreditAssignment.objects.all().order_by('pk')[3]
    assert assignment4.amount == 3
    assert assignment4.invoice == invoice1
    assert assignment4.credit == credit
    assert Payment.objects.count() == 4
    payment4 = Payment.objects.all().order_by('pk')[3]
    assert payment4.amount == 3
    assert payment4.payment_type.slug == 'credit'
    assert assignment4.payment == payment4
    assert payment4.invoicelinepayment_set.count() == 2
    (invoicelinepayment41, invoicelinepayment42) = payment4.invoicelinepayment_set.order_by('pk')
    assert invoicelinepayment41.line == invoice1.lines.order_by('pk')[0]
    assert invoicelinepayment41.amount == 1
    assert invoicelinepayment42.line == invoice1.lines.order_by('pk')[1]
    assert invoicelinepayment42.amount == 2


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
        'date_due': now().date().isoformat(),
        'date_payment_deadline': '2023-04-22',
        'date_publication': '2023-04-21',
        'label': 'Foo Bar',
        'user_external_id': 'user:1',
        'user_first_name': 'First',
        'user_last_name': 'Last',
        'payer_external_id': 'payer:1',
        'form_url': 'http://form.com',
        'payment_callback_url': 'http://payment.com',
        'cancel_callback_url': 'http://cancel.com',
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
            resp = app.post_json('/api/regie/foo/from-bookings/', params=new_params)
            assert resp.json['err'] == 0
            if booked:
                final_object = Invoice.objects.latest('pk')
                assert resp.json['data'] == {
                    'invoice_id': f'{final_object.uuid}',
                    'invoice': {
                        'id': f'{final_object.uuid}',
                        'total_amount': 2,
                        'remaining_amount': 2,
                    },
                    'api_urls': {
                        'invoice_pdf': f'http://testserver/api/regie/foo/invoice/{final_object.uuid}/pdf/',
                    },
                    'urls': {
                        'invoice_in_backoffice': f'http://testserver/manage/invoicing/redirect/invoice/{final_object.uuid}/',
                        'invoice_pdf': f'http://testserver/manage/invoicing/redirect/invoice/{final_object.uuid}/pdf/',
                    },
                }
            else:
                final_object = Credit.objects.latest('pk')
                assert resp.json['data'] == {
                    'credit_id': f'{final_object.uuid}',
                    'credit': {
                        'id': f'{final_object.uuid}',
                        'total_amount': 2,
                    },
                    'api_urls': {
                        'credit_pdf': f'http://testserver/api/regie/foo/credit/{final_object.uuid}/pdf/',
                    },
                    'urls': {
                        'credit_in_backoffice': f'http://testserver/manage/invoicing/redirect/credit/{final_object.uuid}/',
                        'credit_pdf': f'http://testserver/manage/invoicing/redirect/credit/{final_object.uuid}/pdf/',
                    },
                }
            assert final_object.total_amount == 2
            assert final_object.lines.count() == 2
            resp = app.post_json('/api/regie/foo/from-bookings/dry-run/', params=new_params)
            assert resp.json['err'] == 0
            if booked:
                assert resp.json['data']['invoice']['total_amount'] == 2
                assert len(resp.json['data']['invoice']['lines']) == 2
            else:
                assert resp.json['data']['credit']['total_amount'] == 2
                assert len(resp.json['data']['credit']['lines']) == 2

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
        resp = app.post_json('/api/regie/foo/from-bookings/', params=new_params)
        assert resp.json['err'] == 0
        if booked:
            final_object = Invoice.objects.latest('pk')
            assert resp.json['data'] == {
                'invoice_id': f'{final_object.uuid}',
                'invoice': {
                    'id': f'{final_object.uuid}',
                    'total_amount': 3,
                    'remaining_amount': 3,
                },
                'api_urls': {
                    'invoice_pdf': f'http://testserver/api/regie/foo/invoice/{final_object.uuid}/pdf/',
                },
                'urls': {
                    'invoice_in_backoffice': f'http://testserver/manage/invoicing/redirect/invoice/{final_object.uuid}/',
                    'invoice_pdf': f'http://testserver/manage/invoicing/redirect/invoice/{final_object.uuid}/pdf/',
                },
            }
        else:
            final_object = Credit.objects.latest('pk')
            assert resp.json['data'] == {
                'credit_id': f'{final_object.uuid}',
                'credit': {
                    'id': f'{final_object.uuid}',
                    'total_amount': 3,
                },
                'api_urls': {
                    'credit_pdf': f'http://testserver/api/regie/foo/credit/{final_object.uuid}/pdf/',
                },
                'urls': {
                    'credit_in_backoffice': f'http://testserver/manage/invoicing/redirect/credit/{final_object.uuid}/',
                    'credit_pdf': f'http://testserver/manage/invoicing/redirect/credit/{final_object.uuid}/pdf/',
                },
            }
        assert final_object.total_amount == 3
        assert final_object.lines.count() == 2
        resp = app.post_json('/api/regie/foo/from-bookings/dry-run/', params=new_params)
        assert resp.json['err'] == 0
        if booked:
            assert resp.json['data']['invoice']['total_amount'] == 3
            assert len(resp.json['data']['invoice']['lines']) == 2
        else:
            assert resp.json['data']['credit']['total_amount'] == 3
            assert len(resp.json['data']['credit']['lines']) == 2

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
        resp = app.post_json('/api/regie/foo/from-bookings/', params=new_params)
        assert resp.json['err'] == 0
        if booked:
            final_object = Invoice.objects.latest('pk')
            assert resp.json['data'] == {
                'invoice_id': f'{final_object.uuid}',
                'invoice': {
                    'id': f'{final_object.uuid}',
                    'total_amount': 2,
                    'remaining_amount': 2,
                },
                'api_urls': {
                    'invoice_pdf': f'http://testserver/api/regie/foo/invoice/{final_object.uuid}/pdf/',
                },
                'urls': {
                    'invoice_in_backoffice': f'http://testserver/manage/invoicing/redirect/invoice/{final_object.uuid}/',
                    'invoice_pdf': f'http://testserver/manage/invoicing/redirect/invoice/{final_object.uuid}/pdf/',
                },
            }
        else:
            final_object = Credit.objects.latest('pk')
            assert resp.json['data'] == {
                'credit_id': f'{final_object.uuid}',
                'credit': {
                    'id': f'{final_object.uuid}',
                    'total_amount': 2,
                },
                'api_urls': {
                    'credit_pdf': f'http://testserver/api/regie/foo/credit/{final_object.uuid}/pdf/',
                },
                'urls': {
                    'credit_in_backoffice': f'http://testserver/manage/invoicing/redirect/credit/{final_object.uuid}/',
                    'credit_pdf': f'http://testserver/manage/invoicing/redirect/credit/{final_object.uuid}/pdf/',
                },
            }
        assert final_object.total_amount == 2
        assert final_object.lines.count() == 2
        resp = app.post_json('/api/regie/foo/from-bookings/dry-run/', params=new_params)
        assert resp.json['err'] == 0
        if booked:
            assert resp.json['data']['invoice']['total_amount'] == 2
            assert len(resp.json['data']['invoice']['lines']) == 2
        else:
            assert resp.json['data']['credit']['total_amount'] == 2
            assert len(resp.json['data']['credit']['lines']) == 2

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
    resp = app.post_json('/api/regie/foo/from-bookings/', params=params)
    assert resp.json['err'] == 0
    invoice = Invoice.objects.latest('pk')
    assert resp.json['data'] == {
        'invoice_id': f'{invoice.uuid}',
        'invoice': {
            'id': f'{invoice.uuid}',
            'total_amount': 0,
            'remaining_amount': 0,
        },
        'api_urls': {
            'invoice_pdf': f'http://testserver/api/regie/foo/invoice/{invoice.uuid}/pdf/',
        },
        'urls': {
            'invoice_in_backoffice': f'http://testserver/manage/invoicing/redirect/invoice/{invoice.uuid}/',
            'invoice_pdf': f'http://testserver/manage/invoicing/redirect/invoice/{invoice.uuid}/pdf/',
        },
    }
    assert invoice.label == 'Foo Bar'
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
    resp = app.post_json('/api/regie/foo/from-bookings/dry-run/', params=params)
    assert resp.json['err'] == 0
    assert resp.json['data'] == {
        'invoice': {
            'total_amount': 0.0,
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
        {'foo1': 'bar1', 'pricing': 1, 'accounting_code': '414141'},
        {'foo2': 'bar2', 'pricing': 4, 'accounting_code': '424242'},
        {'foo3': 'bar3', 'pricing': 5, 'accounting_code': '434343'},
        # dry-run
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
        'date_due': now().date().isoformat(),
        'date_payment_deadline_displayed': '2023-04-20',
        'date_payment_deadline': '2023-04-22',
        'date_publication': '2023-04-21',
        'date_invoicing': '2025-06-01',
        'label': 'Foo Bar',
        'user_external_id': 'user:1',
        'user_first_name': 'First',
        'user_last_name': 'Last',
        'payer_external_id': 'payer:1',
        'form_url': 'http://form.com',
        'payment_callback_url': 'http://payment.com',
        'cancel_callback_url': 'http://cancel.com',
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
    with payer_data_patch as mock_payer_data:
        mock_payer_data.side_effect = get_payer_data
        resp = app.post_json('/api/regie/foo/from-bookings/', params=params)
    assert resp.json['err'] == 0
    invoice = Invoice.objects.latest('pk')
    draft_invoice_payer2 = DraftInvoice.objects.get(payer_external_id='payer:2')
    draft_invoice_payer3 = DraftInvoice.objects.get(payer_external_id='payer:3')
    assert resp.json['data'] == {
        'invoice_id': f'{invoice.uuid}',
        'invoice': {
            'id': f'{invoice.uuid}',
            'total_amount': 1,
            'remaining_amount': 1,
        },
        'api_urls': {
            'invoice_pdf': f'http://testserver/api/regie/foo/invoice/{invoice.uuid}/pdf/',
        },
        'urls': {
            'invoice_in_backoffice': f'http://testserver/manage/invoicing/redirect/invoice/{invoice.uuid}/',
            'invoice_pdf': f'http://testserver/manage/invoicing/redirect/invoice/{invoice.uuid}/pdf/',
        },
        'other_payer_credit_draft_ids': [str(draft_invoice_payer2.uuid), str(draft_invoice_payer3.uuid)],
    }

    assert invoice.label == 'Foo Bar'
    assert invoice.total_amount == 1
    assert invoice.payer_external_id == 'payer:1'
    assert invoice.payer_first_name == 'First1'
    assert invoice.payer_last_name == 'Last1'
    assert invoice.payer_address == '41 rue des kangourous\n99999 Kangourou Ville'
    assert invoice.payer_email == 'email1'
    assert invoice.payer_phone == 'phone1'
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

    assert draft_invoice_payer2.label == 'Foo Bar'
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

    assert draft_invoice_payer3.label == 'Foo Bar'
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

    with payer_data_patch as mock_payer_data:
        mock_payer_data.side_effect = get_payer_data
        resp = app.post_json('/api/regie/foo/from-bookings/dry-run/', params=params)
    assert resp.json['err'] == 0
    assert resp.json['data'] == {
        'invoice': {
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
        'date_due': now().date().isoformat(),
        'date_payment_deadline_displayed': '2023-04-20',
        'date_payment_deadline': '2023-04-22',
        'date_publication': '2023-04-21',
        'date_invoicing': '2025-06-01',
        'label': 'Foo Bar',
        'user_external_id': 'user:1',
        'user_first_name': 'First',
        'user_last_name': 'Last',
        'payer_external_id': 'payer:1',
        'form_url': 'http://form.com',
        'payment_callback_url': 'http://payment.com',
        'cancel_callback_url': 'http://cancel.com',
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
        resp = app.post_json('/api/regie/foo/from-bookings/', params=params)
    assert resp.json['err'] == 0
    draft_invoice_payer2 = DraftInvoice.objects.get(payer_external_id='payer:2')
    draft_invoice_payer3 = DraftInvoice.objects.get(payer_external_id='payer:3')
    assert resp.json['data'] == {
        'other_payer_credit_draft_ids': [str(draft_invoice_payer2.uuid), str(draft_invoice_payer3.uuid)],
    }

    assert Invoice.objects.count() == 0

    assert draft_invoice_payer2.label == 'Foo Bar'
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

    assert draft_invoice_payer3.label == 'Foo Bar'
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

    with payer_data_patch as mock_payer_data:
        mock_payer_data.side_effect = get_payer_data
        resp = app.post_json('/api/regie/foo/from-bookings/dry-run/', params=params)
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
        'date_due': now().date().isoformat(),
        'date_payment_deadline_displayed': '2023-04-20',
        'date_payment_deadline': '2023-04-22',
        'date_publication': '2023-04-21',
        'date_invoicing': '2025-06-01',
        'label': 'Foo Bar',
        'user_external_id': 'user:1',
        'user_first_name': 'First',
        'user_last_name': 'Last',
        'payer_external_id': 'payer:1',
        'form_url': 'http://form.com',
        'payment_callback_url': 'http://payment.com',
        'cancel_callback_url': 'http://cancel.com',
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
        resp = app.post_json('/api/regie/foo/from-bookings/', params=params)
        assert resp.json['err'] == 0
        invoice = Invoice.objects.latest('pk')
        assert resp.json['data'] == {
            'invoice_id': f'{invoice.uuid}',
            'invoice': {
                'id': f'{invoice.uuid}',
                'total_amount': 33,
                'remaining_amount': 33,
            },
            'api_urls': {
                'invoice_pdf': f'http://testserver/api/regie/foo/invoice/{invoice.uuid}/pdf/',
            },
            'urls': {
                'invoice_in_backoffice': f'http://testserver/manage/invoicing/redirect/invoice/{invoice.uuid}/',
                'invoice_pdf': f'http://testserver/manage/invoicing/redirect/invoice/{invoice.uuid}/pdf/',
            },
        }
        assert invoice.total_amount == 33
        assert invoice.payer_external_id == 'payer:1'
        assert invoice.lines.count() == 1
        lines = invoice.lines.order_by('pk')
        assert lines[0].unit_amount == 33
        assert lines[0].quantity == 1
        assert lines[0].description == 'Booking 10/06'
        assert lines[0].details == {'dates': ['2025-06-10']}

        resp = app.post_json('/api/regie/foo/from-bookings/dry-run/', params=params)
        assert resp.json['err'] == 0
        assert resp.json['data'] == {
            'invoice': {
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
        resp = app.post_json('/api/regie/foo/from-bookings/', params=params)
        assert resp.json['err'] == 0
        invoice = Invoice.objects.latest('pk')
        assert resp.json['data'] == {
            'invoice_id': f'{invoice.uuid}',
            'invoice': {
                'id': f'{invoice.uuid}',
                'total_amount': 33,
                'remaining_amount': 33,
            },
            'api_urls': {
                'invoice_pdf': f'http://testserver/api/regie/foo/invoice/{invoice.uuid}/pdf/',
            },
            'urls': {
                'invoice_in_backoffice': f'http://testserver/manage/invoicing/redirect/invoice/{invoice.uuid}/',
                'invoice_pdf': f'http://testserver/manage/invoicing/redirect/invoice/{invoice.uuid}/pdf/',
            },
        }
        assert invoice.total_amount == 33
        assert invoice.payer_external_id == 'payer:1'
        assert invoice.lines.count() == 1
        lines = invoice.lines.order_by('pk')
        assert lines[0].unit_amount == 33
        assert lines[0].quantity == 1
        assert lines[0].description == 'Booking 10/06'
        assert lines[0].details == {'dates': ['2025-06-10']}

        resp = app.post_json('/api/regie/foo/from-bookings/dry-run/', params=params)
        assert resp.json['err'] == 0
        assert resp.json['data'] == {
            'invoice': {
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
        resp = app.post_json('/api/regie/foo/from-bookings/', params=params)
        assert resp.json['err'] == 0
        invoice = Invoice.objects.latest('pk')
        assert resp.json['data'] == {
            'invoice_id': f'{invoice.uuid}',
            'invoice': {
                'id': f'{invoice.uuid}',
                'total_amount': 11,
                'remaining_amount': 11,
            },
            'api_urls': {
                'invoice_pdf': f'http://testserver/api/regie/foo/invoice/{invoice.uuid}/pdf/',
            },
            'urls': {
                'invoice_in_backoffice': f'http://testserver/manage/invoicing/redirect/invoice/{invoice.uuid}/',
                'invoice_pdf': f'http://testserver/manage/invoicing/redirect/invoice/{invoice.uuid}/pdf/',
            },
        }
        assert invoice.total_amount == 11
        assert invoice.payer_external_id == 'payer:1'
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

        resp = app.post_json('/api/regie/foo/from-bookings/dry-run/', params=params)
        assert resp.json['err'] == 0
        assert resp.json['data'] == {
            'invoice': {
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
        resp = app.post_json('/api/regie/foo/from-bookings/', params=params)
        assert resp.json['err'] == 0
        invoice = Invoice.objects.latest('pk')
        assert resp.json['data'] == {
            'invoice_id': f'{invoice.uuid}',
            'invoice': {
                'id': f'{invoice.uuid}',
                'total_amount': 55,
                'remaining_amount': 55,
            },
            'api_urls': {
                'invoice_pdf': f'http://testserver/api/regie/foo/invoice/{invoice.uuid}/pdf/',
            },
            'urls': {
                'invoice_in_backoffice': f'http://testserver/manage/invoicing/redirect/invoice/{invoice.uuid}/',
                'invoice_pdf': f'http://testserver/manage/invoicing/redirect/invoice/{invoice.uuid}/pdf/',
            },
        }
        assert invoice.total_amount == 55
        assert invoice.payer_external_id == 'payer:1'
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

        resp = app.post_json('/api/regie/foo/from-bookings/dry-run/', params=params)
        assert resp.json['err'] == 0
        assert resp.json['data'] == {
            'invoice': {
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
    resp = app.post_json('/api/regie/foo/from-bookings/', params=params)
    assert resp.json['err'] == 0
    invoice = Invoice.objects.latest('pk')
    assert resp.json['data'] == {
        'invoice_id': f'{invoice.uuid}',
        'invoice': {
            'id': f'{invoice.uuid}',
            'total_amount': 22,
            'remaining_amount': 22,
        },
        'api_urls': {
            'invoice_pdf': f'http://testserver/api/regie/foo/invoice/{invoice.uuid}/pdf/',
        },
        'urls': {
            'invoice_in_backoffice': f'http://testserver/manage/invoicing/redirect/invoice/{invoice.uuid}/',
            'invoice_pdf': f'http://testserver/manage/invoicing/redirect/invoice/{invoice.uuid}/pdf/',
        },
    }
    assert invoice.total_amount == 22
    assert invoice.payer_external_id == 'payer:1'
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

    resp = app.post_json('/api/regie/foo/from-bookings/dry-run/', params=params)
    assert resp.json['err'] == 0
    assert resp.json['data'] == {
        'invoice': {
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
    resp = app.post_json('/api/regie/foo/from-bookings/', params=params)
    assert resp.json['err'] == 0
    invoice = Invoice.objects.latest('pk')
    assert resp.json['data'] == {
        'invoice_id': f'{invoice.uuid}',
        'invoice': {
            'id': f'{invoice.uuid}',
            'total_amount': 44,
            'remaining_amount': 44,
        },
        'api_urls': {
            'invoice_pdf': f'http://testserver/api/regie/foo/invoice/{invoice.uuid}/pdf/',
        },
        'urls': {
            'invoice_in_backoffice': f'http://testserver/manage/invoicing/redirect/invoice/{invoice.uuid}/',
            'invoice_pdf': f'http://testserver/manage/invoicing/redirect/invoice/{invoice.uuid}/pdf/',
        },
    }
    assert invoice.total_amount == 44
    assert invoice.payer_external_id == 'payer:1'
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

    resp = app.post_json('/api/regie/foo/from-bookings/dry-run/', params=params)
    assert resp.json['err'] == 0
    assert resp.json['data'] == {
        'invoice': {
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
        'date_due': now().date().isoformat(),
        'date_payment_deadline_displayed': '2023-04-20',
        'date_payment_deadline': '2023-04-22',
        'date_publication': '2023-04-21',
        'date_invoicing': '2025-06-01',
        'label': 'Foo Bar',
        'user_external_id': 'user:1',
        'user_first_name': 'First',
        'user_last_name': 'Last',
        'payer_external_id': 'payer:1',
        'form_url': 'http://form.com',
        'payment_callback_url': 'http://payment.com',
        'cancel_callback_url': 'http://cancel.com',
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
        resp = app.post_json('/api/regie/foo/from-bookings/', params=params)
        assert resp.json['err'] == 0
        invoice = Invoice.objects.latest('pk')
        assert resp.json['data'] == {
            'invoice_id': f'{invoice.uuid}',
            'invoice': {
                'id': f'{invoice.uuid}',
                'total_amount': 0,
                'remaining_amount': 0,
            },
            'api_urls': {
                'invoice_pdf': f'http://testserver/api/regie/foo/invoice/{invoice.uuid}/pdf/',
            },
            'urls': {
                'invoice_in_backoffice': f'http://testserver/manage/invoicing/redirect/invoice/{invoice.uuid}/',
                'invoice_pdf': f'http://testserver/manage/invoicing/redirect/invoice/{invoice.uuid}/pdf/',
            },
        }
        assert invoice.total_amount == 0
        assert invoice.payer_external_id == 'payer:1'
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

        resp = app.post_json('/api/regie/foo/from-bookings/dry-run/', params=params)
        assert resp.json['err'] == 0
        assert resp.json['data'] == {
            'invoice': {
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
        resp = app.post_json('/api/regie/foo/from-bookings/', params=params)
        assert resp.json['err'] == 0
        credit = Credit.objects.latest('pk')
        assert resp.json['data'] == {
            'credit_id': f'{credit.uuid}',
            'credit': {
                'id': f'{credit.uuid}',
                'total_amount': 22,
            },
            'api_urls': {
                'credit_pdf': f'http://testserver/api/regie/foo/credit/{credit.uuid}/pdf/',
            },
            'urls': {
                'credit_in_backoffice': f'http://testserver/manage/invoicing/redirect/credit/{credit.uuid}/',
                'credit_pdf': f'http://testserver/manage/invoicing/redirect/credit/{credit.uuid}/pdf/',
            },
        }
        assert credit.total_amount == 22
        assert credit.payer_external_id == 'payer:1'
        assert credit.lines.count() == 1
        lines = credit.lines.order_by('pk')
        assert lines[0].unit_amount == 22
        assert lines[0].quantity == 1
        assert lines[0].description == 'Cancellation 10/06'
        assert lines[0].details == {'dates': ['2025-06-10']}

        resp = app.post_json('/api/regie/foo/from-bookings/dry-run/', params=params)
        assert resp.json['err'] == 0
        assert resp.json['data'] == {
            'credit': {
                'total_amount': 22,
                'lines': [
                    {
                        'label': 'Event 1',
                        'description': 'Cancellation 10/06',
                        'unit_amount': 22,
                        'quantity': 1,
                        'total_amount': 22,
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
        resp = app.post_json('/api/regie/foo/from-bookings/', params=params)
        assert resp.json['err'] == 0
        invoice = Invoice.objects.latest('pk')
        assert resp.json['data'] == {
            'invoice_id': f'{invoice.uuid}',
            'invoice': {
                'id': f'{invoice.uuid}',
                'total_amount': 0,
                'remaining_amount': 0,
            },
            'api_urls': {
                'invoice_pdf': f'http://testserver/api/regie/foo/invoice/{invoice.uuid}/pdf/',
            },
            'urls': {
                'invoice_in_backoffice': f'http://testserver/manage/invoicing/redirect/invoice/{invoice.uuid}/',
                'invoice_pdf': f'http://testserver/manage/invoicing/redirect/invoice/{invoice.uuid}/pdf/',
            },
        }
        assert invoice.total_amount == 0
        assert invoice.payer_external_id == 'payer:1'
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

        resp = app.post_json('/api/regie/foo/from-bookings/dry-run/', params=params)
        assert resp.json['err'] == 0
        assert resp.json['data'] == {
            'invoice': {
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
    resp = app.post_json('/api/regie/foo/from-bookings/', params=params)
    assert resp.json['err'] == 0
    credit = Credit.objects.latest('pk')
    assert resp.json['data'] == {
        'credit_id': f'{credit.uuid}',
        'credit': {
            'id': f'{credit.uuid}',
            'total_amount': 33,
        },
        'api_urls': {
            'credit_pdf': f'http://testserver/api/regie/foo/credit/{credit.uuid}/pdf/',
        },
        'urls': {
            'credit_in_backoffice': f'http://testserver/manage/invoicing/redirect/credit/{credit.uuid}/',
            'credit_pdf': f'http://testserver/manage/invoicing/redirect/credit/{credit.uuid}/pdf/',
        },
    }
    assert credit.total_amount == 33
    assert credit.payer_external_id == 'payer:1'
    assert credit.lines.count() == 2
    lines = credit.lines.order_by('pk')
    assert lines[0].unit_amount == 11
    assert lines[0].quantity == -1
    assert lines[0].description == 'Booking (regularization) 10/06'
    assert lines[0].details == {
        'dates': ['2025-06-10'],
        'adjustment': {
            'reason': 'missing-booking',
            '2025-06-10': {'after': 'A-0001'},
        },
    }
    assert lines[1].unit_amount == 44
    assert lines[1].quantity == 1
    assert lines[1].description == 'Cancellation 10/06'
    assert lines[1].details == {'dates': ['2025-06-10']}

    resp = app.post_json('/api/regie/foo/from-bookings/dry-run/', params=params)
    assert resp.json['err'] == 0
    assert resp.json['data'] == {
        'credit': {
            'total_amount': 33,
            'lines': [
                {
                    'label': 'Event 1',
                    'description': 'Booking (regularization) 10/06',
                    'unit_amount': 11,
                    'quantity': -1,
                    'total_amount': -11,
                },
                {
                    'label': 'Event 1',
                    'description': 'Cancellation 10/06',
                    'unit_amount': 44,
                    'quantity': 1,
                    'total_amount': 44,
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
    resp = app.post_json('/api/regie/foo/from-bookings/', params=params)
    assert resp.json['err'] == 0
    credit = Credit.objects.latest('pk')
    assert resp.json['data'] == {
        'credit_id': f'{credit.uuid}',
        'credit': {
            'id': f'{credit.uuid}',
            'total_amount': 33,
        },
        'api_urls': {
            'credit_pdf': f'http://testserver/api/regie/foo/credit/{credit.uuid}/pdf/',
        },
        'urls': {
            'credit_in_backoffice': f'http://testserver/manage/invoicing/redirect/credit/{credit.uuid}/',
            'credit_pdf': f'http://testserver/manage/invoicing/redirect/credit/{credit.uuid}/pdf/',
        },
    }
    assert credit.total_amount == 33
    assert credit.payer_external_id == 'payer:1'
    assert credit.lines.count() == 2
    lines = credit.lines.order_by('pk')
    assert lines[0].unit_amount == 11
    assert lines[0].quantity == 1
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
    assert lines[1].quantity == 1
    assert lines[1].description == 'Cancellation 10/06'
    assert lines[1].details == {'dates': ['2025-06-10']}

    resp = app.post_json('/api/regie/foo/from-bookings/dry-run/', params=params)
    assert resp.json['err'] == 0
    assert resp.json['data'] == {
        'credit': {
            'total_amount': 33,
            'lines': [
                {
                    'label': 'Event 1',
                    'description': 'Cancellation (regularization) 10/06',
                    'unit_amount': 11,
                    'quantity': 1,
                    'total_amount': 11,
                },
                {
                    'label': 'Event 1',
                    'description': 'Cancellation 10/06',
                    'unit_amount': 22,
                    'quantity': 1,
                    'total_amount': 22,
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
    resp = app.post_json('/api/regie/foo/from-bookings/', params=params)
    assert resp.json['err'] == 0
    credit = Credit.objects.latest('pk')
    assert resp.json['data'] == {
        'credit_id': f'{credit.uuid}',
        'credit': {
            'id': f'{credit.uuid}',
            'total_amount': 22,
        },
        'api_urls': {
            'credit_pdf': f'http://testserver/api/regie/foo/credit/{credit.uuid}/pdf/',
        },
        'urls': {
            'credit_in_backoffice': f'http://testserver/manage/invoicing/redirect/credit/{credit.uuid}/',
            'credit_pdf': f'http://testserver/manage/invoicing/redirect/credit/{credit.uuid}/pdf/',
        },
    }
    assert credit.total_amount == 22
    assert credit.payer_external_id == 'payer:1'
    assert credit.lines.count() == 3
    lines = credit.lines.order_by('pk')
    assert lines[0].unit_amount == 33
    assert lines[0].quantity == -1
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
    assert lines[1].quantity == 1
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
    assert lines[2].quantity == 1
    assert lines[2].description == 'Cancellation 10/06'
    assert lines[2].details == {'dates': ['2025-06-10']}

    resp = app.post_json('/api/regie/foo/from-bookings/dry-run/', params=params)
    assert resp.json['err'] == 0
    assert resp.json['data'] == {
        'credit': {
            'total_amount': 22,
            'lines': [
                {
                    'label': 'Event 1',
                    'description': 'Booking (regularization) 10/06',
                    'unit_amount': 33,
                    'quantity': -1,
                    'total_amount': -33,
                },
                {
                    'label': 'Event 1',
                    'description': 'Cancellation (regularization) 10/06',
                    'unit_amount': 11,
                    'quantity': 1,
                    'total_amount': 11,
                },
                {
                    'label': 'Event 1',
                    'description': 'Cancellation 10/06',
                    'unit_amount': 44,
                    'quantity': 1,
                    'total_amount': 44,
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
        'date_due': now().date().isoformat(),
        'date_payment_deadline_displayed': '2023-04-20',
        'date_payment_deadline': '2023-04-22',
        'date_publication': '2023-04-21',
        'date_invoicing': '2025-06-01',
        'label': 'Foo Bar',
        'user_external_id': 'user:1',
        'user_first_name': 'First',
        'user_last_name': 'Last',
        'payer_external_id': 'payer:1',
        'form_url': 'http://form.com',
        'payment_callback_url': 'http://payment.com',
        'cancel_callback_url': 'http://cancel.com',
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
        resp = app.post_json('/api/regie/foo/from-bookings/', params=params)
        assert resp.json['err'] == 0
        invoice = Invoice.objects.latest('pk')
        draft_invoice_payer2 = DraftInvoice.objects.get(payer_external_id='payer:2')
        assert resp.json['data'] == {
            'invoice_id': f'{invoice.uuid}',
            'invoice': {
                'id': f'{invoice.uuid}',
                'total_amount': 44,
                'remaining_amount': 44,
            },
            'api_urls': {
                'invoice_pdf': f'http://testserver/api/regie/foo/invoice/{invoice.uuid}/pdf/',
            },
            'urls': {
                'invoice_in_backoffice': f'http://testserver/manage/invoicing/redirect/invoice/{invoice.uuid}/',
                'invoice_pdf': f'http://testserver/manage/invoicing/redirect/invoice/{invoice.uuid}/pdf/',
            },
            'other_payer_credit_draft_ids': [str(draft_invoice_payer2.uuid)],
        }
        assert invoice.total_amount == 44
        assert invoice.payer_external_id == 'payer:1'
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

        resp = app.post_json('/api/regie/foo/from-bookings/dry-run/', params=params)
        assert resp.json['err'] == 0
        assert resp.json['data'] == {
            'invoice': {
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
        'date_due': now().date().isoformat(),
        'date_payment_deadline_displayed': '2023-04-20',
        'date_payment_deadline': '2023-04-22',
        'date_publication': '2023-04-21',
        'date_invoicing': '2025-06-01',
        'label': 'Foo Bar',
        'user_external_id': 'user:1',
        'user_first_name': 'First',
        'user_last_name': 'Last',
        'payer_external_id': 'payer:1',
        'form_url': 'http://form.com',
        'payment_callback_url': 'http://payment.com',
        'cancel_callback_url': 'http://cancel.com',
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
        resp = app.post_json('/api/regie/foo/from-bookings/', params=params)
        assert resp.json['err'] == 0
        invoice = Invoice.objects.latest('pk')
        assert resp.json['data'] == {
            'invoice_id': f'{invoice.uuid}',
            'invoice': {
                'id': f'{invoice.uuid}',
                'total_amount': 22,
                'remaining_amount': 22,
            },
            'api_urls': {
                'invoice_pdf': f'http://testserver/api/regie/foo/invoice/{invoice.uuid}/pdf/',
            },
            'urls': {
                'invoice_in_backoffice': f'http://testserver/manage/invoicing/redirect/invoice/{invoice.uuid}/',
                'invoice_pdf': f'http://testserver/manage/invoicing/redirect/invoice/{invoice.uuid}/pdf/',
            },
        }
        assert invoice.total_amount == 22
        assert invoice.payer_external_id == 'payer:1'
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

        resp = app.post_json('/api/regie/foo/from-bookings/dry-run/', params=params)
        assert resp.json['err'] == 0
        assert resp.json['data'] == {
            'invoice': {
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
