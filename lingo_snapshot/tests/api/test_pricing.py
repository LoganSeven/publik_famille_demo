import datetime
from unittest import mock

import pytest

from lingo.agendas.chrono import ChronoError
from lingo.agendas.models import Agenda
from lingo.pricing.errors import PricingError
from lingo.pricing.models import Pricing

pytestmark = pytest.mark.django_db


def test_pricing_list(app, user):
    app.authorization = ('Basic', ('john.doe', 'password'))

    pricing1 = Pricing.objects.create(
        label='Foo A',
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2021, month=10, day=1),
        flat_fee_schedule=False,
        subscription_required=True,
    )
    pricing2 = Pricing.objects.create(
        label='Foo B',
        date_start=datetime.date(year=2022, month=9, day=1),
        date_end=datetime.date(year=2022, month=10, day=1),
        flat_fee_schedule=False,
        subscription_required=False,
    )
    pricing3 = Pricing.objects.create(
        label='Foo C',
        date_start=datetime.date(year=2023, month=9, day=1),
        date_end=datetime.date(year=2023, month=10, day=1),
        flat_fee_schedule=True,
        subscription_required=True,
    )
    pricing4 = Pricing.objects.create(
        label='Foo D',
        date_start=datetime.date(year=2024, month=9, day=1),
        date_end=datetime.date(year=2024, month=10, day=1),
        flat_fee_schedule=True,
        subscription_required=False,
    )

    resp = app.get(
        '/api/pricings/',
    )
    assert resp.json['data'] == [
        {
            'id': 'foo-a',
            'text': 'Foo A - From 01/09/2021 to 01/10/2021',
            'slug': 'foo-a',
            'label': 'Foo A',
            'flat_fee_schedule': False,
            'subscription_required': True,
            'date_start': '2021-09-01',
            'date_end': '2021-10-01',
        },
        {
            'id': 'foo-b',
            'text': 'Foo B - From 01/09/2022 to 01/10/2022',
            'slug': 'foo-b',
            'label': 'Foo B',
            'flat_fee_schedule': False,
            'subscription_required': False,
            'date_start': '2022-09-01',
            'date_end': '2022-10-01',
        },
        {
            'id': 'foo-c',
            'text': 'Foo C - From 01/09/2023 to 01/10/2023',
            'slug': 'foo-c',
            'label': 'Foo C',
            'flat_fee_schedule': True,
            'subscription_required': True,
            'date_start': '2023-09-01',
            'date_end': '2023-10-01',
        },
        {
            'id': 'foo-d',
            'text': 'Foo D - From 01/09/2024 to 01/10/2024',
            'slug': 'foo-d',
            'label': 'Foo D',
            'flat_fee_schedule': True,
            'subscription_required': False,
            'date_start': '2024-09-01',
            'date_end': '2024-10-01',
        },
    ]

    resp = app.get('/api/pricings/', params={'flat_fee_schedule': False})
    assert [d['id'] for d in resp.json['data']] == [pricing1.slug, pricing2.slug]
    resp = app.get('/api/pricings/', params={'flat_fee_schedule': True})
    assert [d['id'] for d in resp.json['data']] == [pricing3.slug, pricing4.slug]

    resp = app.get('/api/pricings/', params={'subscription_required': True})
    assert [d['id'] for d in resp.json['data']] == [pricing1.slug, pricing3.slug]
    resp = app.get('/api/pricings/', params={'subscription_required': False})
    assert [d['id'] for d in resp.json['data']] == [pricing2.slug, pricing4.slug]

    resp = app.get('/api/pricings/', params={'flat_fee_schedule': True, 'subscription_required': False})
    assert [d['id'] for d in resp.json['data']] == [pricing4.slug]

    resp = app.get(
        '/api/pricings/',
        params={'date_start': '2021-08-31', 'date_end': '2021-09-01'},
    )
    assert [d['id'] for d in resp.json['data']] == []
    resp = app.get(
        '/api/pricings/',
        params={'date_start': '2021-08-31', 'date_end': '2021-09-02'},
    )
    assert [d['id'] for d in resp.json['data']] == [pricing1.slug]
    resp = app.get(
        '/api/pricings/',
        params={'date_start': '2021-09-02', 'date_end': '2021-09-30'},
    )
    assert [d['id'] for d in resp.json['data']] == [pricing1.slug]
    resp = app.get(
        '/api/pricings/',
        params={'date_start': '2021-09-30', 'date_end': '2021-10-01'},
    )
    assert [d['id'] for d in resp.json['data']] == [pricing1.slug]
    resp = app.get(
        '/api/pricings/',
        params={'date_start': '2021-10-01', 'date_end': '2021-10-02'},
    )
    assert [d['id'] for d in resp.json['data']] == []
    resp = app.get(
        '/api/pricings/',
        params={'date_start': '2021-09-15', 'date_end': '2022-09-15'},
    )
    assert [d['id'] for d in resp.json['data']] == [pricing1.slug, pricing2.slug]

    resp = app.get(
        '/api/pricings/',
        params={'date_start': 'wrong-format', 'date_end': '2021-09-01'},
        status=400,
    )
    assert resp.json['err_class'] == 'invalid filters'
    resp = app.get(
        '/api/pricings/',
        params={'date_start': '2021-08-31', 'date_end': 'wrong-format'},
        status=400,
    )
    assert resp.json['err_class'] == 'invalid filters'
    resp = app.get('/api/pricings/', params={'date_start': '2021-08-31'}, status=400)
    assert resp.json['err_class'] == 'invalid filters'
    assert resp.json['errors']['date_end'] == 'This filter is required when using "date_start" filter.'
    resp = app.get('/api/pricings/', params={'date_end': '2021-09-01'}, status=400)
    assert resp.json['err_class'] == 'invalid filters'
    assert resp.json['errors']['date_start'] == 'This filter is required when using "date_end" filter.'


@pytest.mark.parametrize('method', ['get', 'post_json'])
def test_pricing_compute_params(app, user, method):
    app.authorization = ('Basic', ('john.doe', 'password'))

    Agenda.objects.create(label='Foo')
    Pricing.objects.create(
        label='Foo',
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2021, month=10, day=1),
        flat_fee_schedule=True,
    )

    http_method = getattr(app, method)

    # missing slots, agenda, pricing
    resp = http_method(
        '/api/pricing/compute/',
        params={'user_external_id': 'user:1', 'payer_external_id': 'payer:1'},
        status=400,
    )
    assert resp.json['err'] == 1
    assert resp.json['err_desc'] == 'invalid payload'
    assert resp.json['errors'] == {
        'non_field_errors': ['Either "slots", "agenda" or "pricing" parameter is required.']
    }

    # missing start_date
    resp = http_method(
        '/api/pricing/compute/',
        params={'agenda': 'foo', 'user_external_id': 'user:1', 'payer_external_id': 'payer:1'},
        status=400,
    )
    assert resp.json['err'] == 1
    assert resp.json['err_desc'] == 'invalid payload'
    assert resp.json['errors']['start_date'] == ['This field is required when using "agenda" parameter.']
    resp = http_method(
        '/api/pricing/compute/',
        params={'pricing': 'foo', 'user_external_id': 'user:1', 'payer_external_id': 'payer:1'},
        status=400,
    )
    assert resp.json['err'] == 1
    assert resp.json['err_desc'] == 'invalid payload'
    assert resp.json['errors']['start_date'] == ['This field is required when using "pricing" parameter.']

    params = [
        {'slots': 'foo@foo'},
        {'agenda': 'foo', 'start_date': '2021-09-01'},
        {'pricing': 'foo', 'start_date': '2021-09-01'},
    ]
    for param in params:
        # missing user_external_id
        _param = param.copy()
        _param.update({'payer_external_id': 'payer:1'})
        resp = http_method(
            '/api/pricing/compute/',
            params=_param,
            status=400,
        )
        assert resp.json['err'] == 1
        assert resp.json['err_desc'] == 'invalid payload'
        assert resp.json['errors']['user_external_id'] == ['This field is required.']

        # missing payer_external_id
        _param = param.copy()
        _param.update({'user_external_id': 'user:1'})
        resp = http_method(
            '/api/pricing/compute/',
            params=_param,
            status=400,
        )
        assert resp.json['err'] == 1
        assert resp.json['err_desc'] == 'invalid payload'
        assert resp.json['errors']['payer_external_id'] == ['This field is required.']


@mock.patch('lingo.api.serializers.get_events')
def test_pricing_compute_slots(mock_events, app, user):
    app.authorization = ('Basic', ('john.doe', 'password'))

    # bad slot format
    resp = app.get(
        '/api/pricing/compute/',
        params={
            'slots': 'event-bar-slug',
            'user_external_id': 'user:1',
            'payer_external_id': 'payer:1',
        },
        status=400,
    )
    assert resp.json['err'] == 1
    assert resp.json['err_desc'] == 'invalid payload'
    assert resp.json['errors']['slots'] == ['Invalid format for slot event-bar-slug']
    resp = app.get(
        '/api/pricing/compute/',
        params={
            'slots': '@event-bar-slug',
            'user_external_id': 'user:1',
            'payer_external_id': 'payer:1',
        },
        status=400,
    )
    assert resp.json['err'] == 1
    assert resp.json['err_desc'] == 'invalid payload'
    assert resp.json['errors']['slots'] == ['Missing agenda slug in slot @event-bar-slug']
    resp = app.get(
        '/api/pricing/compute/',
        params={
            'slots': 'agenda@',
            'user_external_id': 'user:1',
            'payer_external_id': 'payer:1',
        },
        status=400,
    )
    assert resp.json['err'] == 1
    assert resp.json['err_desc'] == 'invalid payload'
    assert resp.json['errors']['slots'] == ['Missing event slug in slot agenda@']

    # unknown agenda
    resp = app.get(
        '/api/pricing/compute/',
        params={
            'slots': 'agenda@event-bar-slug, agenda2@event-bar-slug',
            'user_external_id': 'user:1',
            'payer_external_id': 'payer:1',
        },
        status=400,
    )
    assert resp.json['err'] == 1
    assert resp.json['err_desc'] == 'invalid payload'
    assert resp.json['errors']['slots'] == ['Unknown agendas: agenda, agenda2']

    # empty slots
    mock_events.reset_mock()
    resp = app.get(
        '/api/pricing/compute/',
        params={
            'slots': '',
            'user_external_id': 'user:1',
            'payer_external_id': 'payer:1',
        },
    )
    assert resp.json['err'] == 0
    assert resp.json['data'] == []
    assert mock_events.call_args_list == []
    mock_events.reset_mock()
    resp = app.post_json(
        '/api/pricing/compute/',
        params={
            'slots': '',
            'user_external_id': 'user:1',
            'payer_external_id': 'payer:1',
        },
    )
    assert resp.json['err'] == 0
    assert resp.json['data'] == []
    assert mock_events.call_args_list == []


def test_pricing_compute_agenda(app, user):
    app.authorization = ('Basic', ('john.doe', 'password'))

    # unknown agenda
    resp = app.get(
        '/api/pricing/compute/',
        params={
            'agenda': 'agenda',
            'start_date': '2021-09-01',
            'user_external_id': 'user:1',
            'payer_external_id': 'payer:1',
        },
        status=400,
    )
    assert resp.json['err'] == 1
    assert resp.json['err_desc'] == 'invalid payload'
    assert resp.json['errors']['agenda'] == ['Unknown agenda: agenda']


def test_pricing_compute_pricing(app, user):
    app.authorization = ('Basic', ('john.doe', 'password'))

    def test():
        resp = app.get(
            '/api/pricing/compute/',
            params={
                'pricing': 'baz',
                'start_date': '2021-09-01',
                'user_external_id': 'user:1',
                'payer_external_id': 'payer:1',
            },
            status=400,
        )
        assert resp.json['err'] == 1
        assert resp.json['err_desc'] == 'invalid payload'
        assert resp.json['errors']['pricing'] == ['Unknown pricing: baz']

    # unknown pricing
    test()

    # bad dates
    pricing = Pricing.objects.create(
        label='Baz',
        date_start=datetime.date(year=2021, month=8, day=1),
        date_end=datetime.date(year=2021, month=9, day=1),
        flat_fee_schedule=True,
        subscription_required=False,
    )
    test()
    pricing.date_start = datetime.date(year=2021, month=9, day=3)
    pricing.date_end = datetime.date(year=2021, month=10, day=1)
    pricing.save()
    test()

    # wrong flat_fee_schedule value
    pricing.flat_fee_schedule = False
    pricing.save()
    test()

    # wrong subscription_required value
    pricing.flat_fee_schedule = True
    pricing.subscription_required = True
    pricing.save()
    test()


@mock.patch('lingo.api.serializers.get_events')
def test_pricing_compute_events_error(mock_events, app, user):
    Agenda.objects.create(label='Agenda')
    app.authorization = ('Basic', ('john.doe', 'password'))

    mock_events.side_effect = ChronoError('foo bar')
    resp = app.get(
        '/api/pricing/compute/',
        params={
            'slots': 'agenda@event-bar-slug',
            'user_external_id': 'user:1',
            'payer_external_id': 'payer:1',
        },
        status=400,
    )
    assert resp.json['err'] == 1
    assert resp.json['err_desc'] == 'invalid payload'
    assert resp.json['errors']['slots'] == ['foo bar']


@mock.patch('lingo.api.serializers.get_events')
@mock.patch('lingo.pricing.models.Pricing.get_pricing_data_for_event')
def test_pricing_compute_for_event(mock_pricing_data_event, mock_events, app, user):
    agenda = Agenda.objects.create(label='Agenda')
    agenda2 = Agenda.objects.create(label='Agenda2')
    app.authorization = ('Basic', ('john.doe', 'password'))

    mock_events.return_value = [
        {'start_datetime': '2021-09-02T12:00:00+02:00', 'agenda': 'agenda', 'slug': 'event-bar-slug'},
        {
            'start_datetime': '2021-09-01T12:00:00+02:00',
            'agenda': 'agenda2',
            'slug': 'recurring-event-baz-slug',
            'recurrence_days': [1],
        },
    ]

    # no pricing found
    mock_pricing_data_event.side_effect = [
        {'foo': 'baz'},
        {'foo': 'bar'},
    ]
    resp = app.get(
        '/api/pricing/compute/',
        params={
            'slots': 'agenda@event-bar-slug, agenda2@recurring-event-baz-slug, agenda2@recurring-event-baz-slug:1',
            'user_external_id': 'user:1',
            'payer_external_id': 'payer:1',
        },
    )
    assert resp.json['data'] == [
        {
            'event': 'agenda2@recurring-event-baz-slug',
            'error': 'No agenda pricing found for event agenda2@recurring-event-baz-slug',
        },
        {
            'event': 'agenda@event-bar-slug',
            'error': 'No agenda pricing found for event agenda@event-bar-slug',
        },
    ]
    assert mock_pricing_data_event.call_args_list == []

    pricing = Pricing.objects.create(
        label='Foo',
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2021, month=10, day=1),
        flat_fee_schedule=True,  # wrong config
    )
    pricing.agendas.add(agenda, agenda2)
    resp = app.get(
        '/api/pricing/compute/',
        params={
            'slots': 'agenda@event-bar-slug, agenda2@recurring-event-baz-slug, agenda2@recurring-event-baz-slug:1',
            'user_external_id': 'user:1',
            'payer_external_id': 'payer:1',
        },
    )
    assert resp.json['data'] == [
        {
            'event': 'agenda2@recurring-event-baz-slug',
            'error': 'No agenda pricing found for event agenda2@recurring-event-baz-slug',
        },
        {
            'event': 'agenda@event-bar-slug',
            'error': 'No agenda pricing found for event agenda@event-bar-slug',
        },
    ]

    # ok
    pricing.flat_fee_schedule = False
    pricing.save()
    resp = app.get(
        '/api/pricing/compute/',
        params={
            'slots': 'agenda@event-bar-slug, agenda2@recurring-event-baz-slug, agenda2@recurring-event-baz-slug:1',
            'user_external_id': 'user:1',
            'payer_external_id': 'payer:1',
            'extra_variable_foo': 'bar',
            'foo': 'baz',
        },
    )
    assert resp.json['data'] == [
        {'event': 'agenda2@recurring-event-baz-slug', 'pricing_data': {'foo': 'baz'}},
        {'event': 'agenda@event-bar-slug', 'pricing_data': {'foo': 'bar'}},
    ]
    assert mock_pricing_data_event.call_args_list == [
        mock.call(
            request=mock.ANY,
            agenda=agenda2,
            event={
                'start_datetime': '2021-09-01T12:00:00+02:00',
                'agenda': 'agenda2',
                'slug': 'recurring-event-baz-slug',
                'recurrence_days': [1],
            },
            check_status={'status': 'presence', 'check_type': None},
            user_external_id='user:1',
            payer_external_id='payer:1',
            bypass_extra_variables={'foo': 'bar'},
        ),
        mock.call(
            request=mock.ANY,
            agenda=agenda,
            event={
                'start_datetime': '2021-09-02T12:00:00+02:00',
                'agenda': 'agenda',
                'slug': 'event-bar-slug',
            },
            check_status={'status': 'presence', 'check_type': None},
            user_external_id='user:1',
            payer_external_id='payer:1',
            bypass_extra_variables={'foo': 'bar'},
        ),
    ]
    # with a start_date
    mock_pricing_data_event.reset_mock()
    mock_pricing_data_event.side_effect = [
        {'foo': 'baz'},
        {'foo': 'bar'},
    ]
    resp = app.get(
        '/api/pricing/compute/',
        params={
            'slots': 'agenda@event-bar-slug, agenda2@recurring-event-baz-slug, agenda2@recurring-event-baz-slug:1',
            'user_external_id': 'user:1',
            'payer_external_id': 'payer:1',
            'start_date': '2021-09-06',
        },
    )
    assert resp.json['data'] == [
        {'event': 'agenda2@recurring-event-baz-slug', 'pricing_data': {'foo': 'baz'}},
        {'event': 'agenda@event-bar-slug', 'pricing_data': {'foo': 'bar'}},
    ]
    assert mock_pricing_data_event.call_args_list == [
        mock.call(
            request=mock.ANY,
            agenda=agenda2,
            event={
                'start_datetime': '2021-09-06T00:00:00+02:00',
                'agenda': 'agenda2',
                'slug': 'recurring-event-baz-slug',
                'recurrence_days': [1],
            },
            check_status={'status': 'presence', 'check_type': None},
            user_external_id='user:1',
            payer_external_id='payer:1',
            bypass_extra_variables={},
        ),
        mock.call(
            request=mock.ANY,
            agenda=agenda,
            event={
                'start_datetime': '2021-09-02T12:00:00+02:00',
                'agenda': 'agenda',
                'slug': 'event-bar-slug',
            },
            check_status={'status': 'presence', 'check_type': None},
            user_external_id='user:1',
            payer_external_id='payer:1',
            bypass_extra_variables={},
        ),
    ]

    # get_pricing_data_for_event with error
    mock_pricing_data_event.side_effect = [
        PricingError(details={'foo': 'error'}),
        {'foo': 'bar'},
    ]
    resp = app.get(
        '/api/pricing/compute/',
        params={
            'slots': 'agenda@event-bar-slug, agenda2@recurring-event-baz-slug, agenda2@recurring-event-baz-slug:1',
            'user_external_id': 'user:1',
            'payer_external_id': 'payer:1',
        },
    )
    assert resp.json['data'] == [
        {
            'event': 'agenda2@recurring-event-baz-slug',
            'error': 'PricingError',
            'error_details': {'foo': 'error'},
        },
        {'event': 'agenda@event-bar-slug', 'pricing_data': {'foo': 'bar'}},
    ]


@mock.patch('lingo.pricing.models.Pricing.get_pricing_data')
def test_pricing_compute_for_flat_fee_schedule_with_subscription(mock_pricing_data, app, user):
    agenda = Agenda.objects.create(label='Foo bar')
    app.authorization = ('Basic', ('john.doe', 'password'))

    # no pricing found
    pricing = Pricing.objects.create(
        # bad dates
        date_start=datetime.date(year=2021, month=8, day=1),
        date_end=datetime.date(year=2021, month=9, day=1),
    )
    pricing.agendas.add(agenda)
    pricing = Pricing.objects.create(
        # bad dates
        date_start=datetime.date(year=2021, month=9, day=3),
        date_end=datetime.date(year=2021, month=10, day=1),
    )
    pricing.agendas.add(agenda)
    mock_pricing_data.return_value = {'foo': 'bar'}
    resp = app.get(
        '/api/pricing/compute/',
        params={
            'agenda': 'foo-bar',
            'start_date': '2021-09-02',
            'user_external_id': 'user:1',
            'payer_external_id': 'payer:1',
        },
    )
    assert resp.json['data'] == {
        'agenda': 'foo-bar',
        'error': 'No agenda pricing found for agenda foo-bar',
    }
    assert mock_pricing_data.call_args_list == []

    pricing.delete()
    pricing = Pricing.objects.create(
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2021, month=10, day=1),
        flat_fee_schedule=False,  # wrong config
        subscription_required=True,
    )
    pricing.agendas.add(agenda)
    resp = app.get(
        '/api/pricing/compute/',
        params={
            'agenda': 'foo-bar',
            'start_date': '2021-09-02',
            'user_external_id': 'user:1',
            'payer_external_id': 'payer:1',
        },
    )
    assert resp.json['data'] == {
        'agenda': 'foo-bar',
        'error': 'No agenda pricing found for agenda foo-bar',
    }
    assert mock_pricing_data.call_args_list == []

    pricing.flat_fee_schedule = True
    pricing.subscription_required = False  # wrong config
    pricing.save()
    resp = app.get(
        '/api/pricing/compute/',
        params={
            'agenda': 'foo-bar',
            'start_date': '2021-09-02',
            'user_external_id': 'user:1',
            'payer_external_id': 'payer:1',
        },
    )
    assert resp.json['data'] == {
        'agenda': 'foo-bar',
        'error': 'No agenda pricing found for agenda foo-bar',
    }
    assert mock_pricing_data.call_args_list == []

    # ok
    pricing.subscription_required = True
    pricing.save()
    resp = app.get(
        '/api/pricing/compute/',
        params={
            'agenda': 'foo-bar',
            'start_date': '2021-09-02',
            'user_external_id': 'user:1',
            'payer_external_id': 'payer:1',
            'extra_variable_foo': 'bar',
            'foo': 'baz',
        },
    )
    assert resp.json['data'] == {'agenda': 'foo-bar', 'pricing_data': {'foo': 'bar'}}
    assert mock_pricing_data.call_args_list == [
        mock.call(
            request=mock.ANY,
            pricing_date=datetime.date(2021, 9, 1),
            user_external_id='user:1',
            payer_external_id='payer:1',
            bypass_extra_variables={'foo': 'bar'},
        ),
    ]

    # get_pricing_data with error
    mock_pricing_data.side_effect = PricingError(details={'foo': 'error'})
    resp = app.get(
        '/api/pricing/compute/',
        params={
            'agenda': 'foo-bar',
            'start_date': '2021-09-02',
            'user_external_id': 'user:1',
            'payer_external_id': 'payer:1',
        },
    )
    assert resp.json['data'] == {
        'agenda': 'foo-bar',
        'error': 'PricingError',
        'error_details': {'foo': 'error'},
    }

    # check with billing dates
    mock_pricing_data.return_value = {'foo': 'bar'}
    pricing.billingdates.create(
        date_start=datetime.date(2021, 9, 1),
        label='Foo 1',
    )
    pricing.billingdates.create(
        date_start=datetime.date(2021, 9, 15),
        label='Foo 2',
    )
    mock_pricing_data.reset_mock()
    resp = app.get(
        '/api/pricing/compute/',
        params={
            'agenda': 'foo-bar',
            'start_date': '2021-09-16',
            'user_external_id': 'user:1',
            'payer_external_id': 'payer:1',
        },
    )
    assert mock_pricing_data.call_args_list == [
        mock.call(
            request=mock.ANY,
            pricing_date=datetime.date(2021, 9, 15),
            user_external_id='user:1',
            payer_external_id='payer:1',
            bypass_extra_variables={},
        ),
    ]


@mock.patch('lingo.pricing.models.Pricing.get_pricing_data')
def test_pricing_compute_for_flat_fee_schedule_without_subscription(mock_pricing_data, app, user):
    pricing = Pricing.objects.create(
        label='Foo bar pricing',
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2021, month=10, day=1),
        flat_fee_schedule=True,
        subscription_required=False,
    )
    app.authorization = ('Basic', ('john.doe', 'password'))

    mock_pricing_data.return_value = {'foo': 'bar'}
    resp = app.get(
        '/api/pricing/compute/',
        params={
            'pricing': 'foo-bar-pricing',
            'start_date': '2021-09-02',
            'user_external_id': 'user:1',
            'payer_external_id': 'payer:1',
        },
    )
    assert resp.json['data'] == {'pricing': 'foo-bar-pricing', 'pricing_data': {'foo': 'bar'}}
    assert mock_pricing_data.call_args_list == [
        mock.call(
            request=mock.ANY,
            pricing_date=datetime.date(2021, 9, 1),
            user_external_id='user:1',
            payer_external_id='payer:1',
            bypass_extra_variables={},
        ),
    ]

    # get_pricing_data with error
    mock_pricing_data.side_effect = PricingError(details={'foo': 'error'})
    resp = app.get(
        '/api/pricing/compute/',
        params={
            'pricing': 'foo-bar-pricing',
            'start_date': '2021-09-02',
            'user_external_id': 'user:1',
            'payer_external_id': 'payer:1',
        },
    )
    assert resp.json['data'] == {
        'pricing': 'foo-bar-pricing',
        'error': 'PricingError',
        'error_details': {'foo': 'error'},
    }

    # check with billing dates
    mock_pricing_data.return_value = {'foo': 'bar'}
    pricing.billingdates.create(
        date_start=datetime.date(2021, 9, 1),
        label='Foo 1',
    )
    pricing.billingdates.create(
        date_start=datetime.date(2021, 9, 15),
        label='Foo 2',
    )
    mock_pricing_data.reset_mock()
    resp = app.get(
        '/api/pricing/compute/',
        params={
            'pricing': 'foo-bar-pricing',
            'start_date': '2021-09-16',
            'user_external_id': 'user:1',
            'payer_external_id': 'payer:1',
        },
    )
    assert mock_pricing_data.call_args_list == [
        mock.call(
            request=mock.ANY,
            pricing_date=datetime.date(2021, 9, 15),
            user_external_id='user:1',
            payer_external_id='payer:1',
            bypass_extra_variables={},
        ),
    ]
