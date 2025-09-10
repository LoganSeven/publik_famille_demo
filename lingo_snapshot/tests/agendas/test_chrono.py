import datetime
import json
from unittest import mock

import pytest
from django.core.management import call_command
from requests.exceptions import ConnectionError
from requests.models import Response

from lingo.agendas.chrono import (
    ChronoError,
    collect_agenda_data,
    get_check_status,
    get_event,
    get_events,
    get_subscriptions,
    lock_events_check,
    mark_events_invoiced,
    refresh_agendas,
    unlock_events_check,
)
from lingo.agendas.models import Agenda
from lingo.invoicing.models import Campaign, Regie
from lingo.pricing.models import Pricing
from lingo.snapshot.models import AgendaSnapshot

pytestmark = pytest.mark.django_db

AGENDA_DATA = [
    {
        'slug': 'events-a',
        'kind': 'events',
        'text': 'Events A',
        'category': None,
        'category_label': None,
    },
    {
        'slug': 'events-b',
        'kind': 'events',
        'text': 'Events B',
        'category': 'foo',
        'category_label': 'Foo',
        'partial_bookings': True,
    },
    {
        'slug': 'meetings-a',
        'kind': 'meetings',
        'text': 'Meetings A',
        'category': None,
        'category_label': None,
    },
    {
        'slug': 'virtual-b',
        'kind': 'virtual',
        'text': 'Virtual B',
        'category': 'foo',
        'category_label': 'Foo',
    },
]


class MockedRequestResponse(mock.Mock):
    status_code = 200

    def json(self):
        return json.loads(self.content)


def test_collect_agenda_data_no_service(settings):
    settings.KNOWN_SERVICES = {}
    assert collect_agenda_data() is None

    settings.KNOWN_SERVICES = {'other': []}
    assert collect_agenda_data() is None


def test_collect_agenda_data():
    with mock.patch('requests.Session.get') as requests_get:
        requests_get.side_effect = ConnectionError()
        assert collect_agenda_data() is None

    with mock.patch('requests.Session.get') as requests_get:
        mock_resp = Response()
        mock_resp.status_code = 500
        requests_get.return_value = mock_resp
        assert collect_agenda_data() is None

    with mock.patch('requests.Session.get') as requests_get:
        mock_resp = Response()
        mock_resp.status_code = 404
        requests_get.return_value = mock_resp
        assert collect_agenda_data() is None

    with mock.patch('requests.Session.get') as requests_get:
        requests_get.return_value = MockedRequestResponse(content=json.dumps({'foo': 'bar'}))
        assert collect_agenda_data() is None

    data = {'data': []}
    with mock.patch('requests.Session.get') as requests_get:
        requests_get.return_value = MockedRequestResponse(content=json.dumps(data))
        assert collect_agenda_data() == []
        assert requests_get.call_args_list[0][0] == ('api/agenda/',)
        assert requests_get.call_args_list[0][1]['remote_service']['url'] == 'http://chrono.example.org/'

    data = {'data': AGENDA_DATA}
    with mock.patch('requests.Session.get') as requests_get:
        requests_get.return_value = MockedRequestResponse(content=json.dumps(data))
        assert collect_agenda_data() == [
            {
                'category_label': None,
                'category_slug': None,
                'label': 'Events A',
                'slug': 'events-a',
                'partial_bookings': False,
            },
            {
                'category_label': 'Foo',
                'category_slug': 'foo',
                'label': 'Events B',
                'slug': 'events-b',
                'partial_bookings': True,
            },
        ]


@mock.patch('lingo.agendas.chrono.collect_agenda_data')
def test_refresh_agendas(mock_collect):
    Agenda.objects.create(label='foo')

    # error during collect
    mock_collect.return_value = None
    refresh_agendas()
    assert Agenda.objects.count() == 1  # no changes

    # 2 agendas found
    mock_collect.return_value = [
        {'category_label': None, 'category_slug': None, 'label': 'Events A', 'slug': 'events-a'},
        {
            'category_label': 'Foo',
            'category_slug': 'foo',
            'label': 'Events B',
            'slug': 'events-b',
            'partial_bookings': True,
        },
    ]

    # agendas don't exist, create them
    refresh_agendas()
    assert Agenda.objects.count() == 2
    agenda1 = Agenda.objects.all().order_by('pk')[0]
    agenda2 = Agenda.objects.all().order_by('pk')[1]
    assert agenda1.label == 'Events A'
    assert agenda1.slug == 'events-a'
    assert agenda1.category_label is None
    assert agenda1.category_slug is None
    assert agenda1.partial_bookings is False
    assert agenda2.label == 'Events B'
    assert agenda2.slug == 'events-b'
    assert agenda2.category_label == 'Foo'
    assert agenda2.category_slug == 'foo'
    assert agenda2.partial_bookings is True
    assert AgendaSnapshot.objects.count() == 2

    # again, but some attributes are wrong
    agenda1.label = 'Wrong'
    agenda1.category_label = 'Foo'
    agenda1.category_slug = 'foo'
    agenda1.save()
    agenda2.label = 'Wrong'
    agenda2.category_label = None
    agenda2.category_slug = None
    agenda2.save()
    refresh_agendas()
    assert Agenda.objects.count() == 2
    new_agenda1 = Agenda.objects.all().order_by('pk')[0]
    new_agenda2 = Agenda.objects.all().order_by('pk')[1]
    assert new_agenda1.pk == agenda1.pk
    assert new_agenda1.label == 'Events A'
    assert new_agenda1.slug == 'events-a'
    assert new_agenda1.category_label is None
    assert new_agenda1.category_slug is None
    assert new_agenda1.archived is False
    assert new_agenda2.pk == agenda2.pk
    assert new_agenda2.label == 'Events B'
    assert new_agenda2.slug == 'events-b'
    assert new_agenda2.category_label == 'Foo'
    assert new_agenda2.category_slug == 'foo'
    assert new_agenda2.archived is False

    # new_agenda1 used by pricing
    pricing = Pricing.objects.create(
        label='Foo bar',
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2021, month=10, day=1),
    )
    pricing.agendas.add(new_agenda1)
    mock_collect.return_value = []
    refresh_agendas()
    assert Agenda.objects.count() == 1
    assert Agenda.objects.filter(pk=new_agenda1.pk).exists() is True
    assert Agenda.objects.filter(pk=new_agenda2.pk).exists() is False
    new_agenda1.refresh_from_db()
    assert new_agenda1.archived is True

    # new_agenda1 used by campaign
    pricing.agendas.clear()
    regie = Regie.objects.create(label='foo')
    campaign = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_debit=datetime.date(2022, 11, 15),
    )
    campaign.agendas.add(new_agenda1)
    mock_collect.return_value = []
    refresh_agendas()
    assert Agenda.objects.count() == 1
    assert Agenda.objects.filter(pk=new_agenda1.pk).exists() is True
    assert Agenda.objects.filter(pk=new_agenda2.pk).exists() is False
    new_agenda1.refresh_from_db()
    assert new_agenda1.archived is True

    # agenda is not archived
    # 2 agendas found
    mock_collect.return_value = [
        {'category_label': None, 'category_slug': None, 'label': 'Events A', 'slug': 'events-a'},
    ]
    refresh_agendas()
    assert Agenda.objects.count() == 1
    assert Agenda.objects.filter(pk=new_agenda1.pk).exists() is True
    assert Agenda.objects.filter(pk=new_agenda2.pk).exists() is False
    new_agenda1.refresh_from_db()
    assert new_agenda1.archived is False

    # no agenda in chrono
    campaign.agendas.clear()
    mock_collect.return_value = []
    refresh_agendas()
    assert Agenda.objects.count() == 0


@mock.patch('lingo.agendas.chrono.refresh_agendas')
def test_refresh_agendas_cmd(mock_refresh):
    call_command('refresh_agendas')
    assert mock_refresh.call_args_list == [mock.call()]


def test_get_event_no_service(settings):
    settings.KNOWN_SERVICES = {}
    with pytest.raises(ChronoError) as e:
        get_event('foo')
    assert str(e.value) == 'Unable to get event details'

    settings.KNOWN_SERVICES = {'other': []}
    with pytest.raises(ChronoError) as e:
        get_event('foo')
    assert str(e.value) == 'Unable to get event details'


def test_get_event():
    with mock.patch('requests.Session.get') as requests_get:
        requests_get.side_effect = ConnectionError()
        with pytest.raises(ChronoError) as e:
            get_event('foo')
        assert str(e.value) == 'Unable to get event details'

    with mock.patch('requests.Session.get') as requests_get:
        mock_resp = Response()
        mock_resp.status_code = 500
        requests_get.return_value = mock_resp
        with pytest.raises(ChronoError) as e:
            get_event('foo')
        assert str(e.value) == 'Unable to get event details'

    with mock.patch('requests.Session.get') as requests_get:
        mock_resp = Response()
        mock_resp.status_code = 404
        requests_get.return_value = mock_resp
        with pytest.raises(ChronoError) as e:
            get_event('foo')
        assert str(e.value) == 'Unable to get event details'

    with mock.patch('requests.Session.get') as requests_get:
        requests_get.return_value = MockedRequestResponse(content=json.dumps({'foo': 'bar'}))
        with pytest.raises(ChronoError) as e:
            get_event('foo')
        assert str(e.value) == 'Unable to get event details'

    data = {'data': []}
    with mock.patch('requests.Session.post') as requests_post:
        requests_post.return_value = MockedRequestResponse(content=json.dumps(data))
        with pytest.raises(ChronoError) as e:
            get_event('foo')
        assert str(e.value) == 'Unable to get event details'
        assert requests_post.call_args_list[0][0] == ('api/agendas/events/',)
        assert requests_post.call_args_list[0][1]['json']['slots'] == ['foo']
        assert requests_post.call_args_list[0][1]['remote_service']['url'] == 'http://chrono.example.org/'

    data = {'data': ['foo']}
    with mock.patch('requests.Session.post') as requests_post:
        requests_post.return_value = MockedRequestResponse(content=json.dumps(data))
        assert get_event('foo') == 'foo'

    data = {'data': ['foo', 'bar']}  # should not happen
    with mock.patch('requests.Session.post') as requests_post:
        requests_post.return_value = MockedRequestResponse(content=json.dumps(data))
        assert get_event('foo') == 'foo'


def test_get_events_no_service(settings):
    settings.KNOWN_SERVICES = {}
    with pytest.raises(ChronoError) as e:
        get_events(['foo', 'bar'])
    assert str(e.value) == 'Unable to get events details'

    settings.KNOWN_SERVICES = {'other': []}
    with pytest.raises(ChronoError) as e:
        get_events(['foo', 'bar'])
    assert str(e.value) == 'Unable to get events details'


def test_get_events():
    with mock.patch('requests.Session.post') as requests_post:
        requests_post.side_effect = ConnectionError()
        with pytest.raises(ChronoError) as e:
            get_events(['foo', 'bar'])
        assert str(e.value) == 'Unable to get events details'

    with mock.patch('requests.Session.post') as requests_post:
        mock_resp = Response()
        mock_resp.status_code = 500
        requests_post.return_value = mock_resp
        with pytest.raises(ChronoError) as e:
            get_events(['foo', 'bar'])
        assert str(e.value) == 'Unable to get events details'

    with mock.patch('requests.Session.post') as requests_post:
        mock_resp = Response()
        mock_resp.status_code = 404
        requests_post.return_value = mock_resp
        with pytest.raises(ChronoError) as e:
            get_events(['foo', 'bar'])
        assert str(e.value) == 'Unable to get events details'

    with mock.patch('requests.Session.post') as requests_post:
        requests_post.return_value = MockedRequestResponse(content=json.dumps({'foo': 'bar'}))
        with pytest.raises(ChronoError) as e:
            get_events(['foo', 'bar'])
        assert str(e.value) == 'Unable to get events details'

    data = {'data': []}
    with mock.patch('requests.Session.post') as requests_post:
        requests_post.return_value = MockedRequestResponse(content=json.dumps(data))
        with pytest.raises(ChronoError) as e:
            get_events(['foo', 'bar'])
        assert str(e.value) == 'Unable to get events details'
        assert requests_post.call_args_list[0][0] == ('api/agendas/events/',)
        assert requests_post.call_args_list[0][1]['json']['slots'] == ['foo', 'bar']
        assert requests_post.call_args_list[0][1]['remote_service']['url'] == 'http://chrono.example.org/'

    data = {'data': ['foo', 'bar']}
    with mock.patch('requests.Session.post') as requests_post:
        requests_post.return_value = MockedRequestResponse(content=json.dumps(data))
        assert get_events(['foo', 'bar']) == ['foo', 'bar']


def test_get_subscriptions_no_service(settings):
    settings.KNOWN_SERVICES = {}
    with pytest.raises(ChronoError) as e:
        get_subscriptions(agenda_slug='foo')
    assert str(e.value) == 'Unable to get subscription details'

    settings.KNOWN_SERVICES = {'other': []}
    with pytest.raises(ChronoError) as e:
        get_subscriptions(agenda_slug='foo')
    assert str(e.value) == 'Unable to get subscription details'


def test_get_subscriptions():
    with mock.patch('requests.Session.get') as requests_get:
        requests_get.side_effect = ConnectionError()
        with pytest.raises(ChronoError) as e:
            get_subscriptions(agenda_slug='foo')
        assert str(e.value) == 'Unable to get subscription details'

    with mock.patch('requests.Session.get') as requests_get:
        mock_resp = Response()
        mock_resp.status_code = 500
        requests_get.return_value = mock_resp
        with pytest.raises(ChronoError) as e:
            get_subscriptions(agenda_slug='foo')
        assert str(e.value) == 'Unable to get subscription details'

    with mock.patch('requests.Session.get') as requests_get:
        mock_resp = Response()
        mock_resp.status_code = 404
        requests_get.return_value = mock_resp
        with pytest.raises(ChronoError) as e:
            get_subscriptions(agenda_slug='foo')
        assert str(e.value) == 'Unable to get subscription details'

    with mock.patch('requests.Session.get') as requests_get:
        requests_get.return_value = MockedRequestResponse(content=json.dumps({'foo': 'bar'}))
        with pytest.raises(ChronoError) as e:
            get_subscriptions(agenda_slug='foo')
        assert str(e.value) == 'Unable to get subscription details'

    data = {'data': []}
    with mock.patch('requests.Session.get') as requests_get:
        requests_get.return_value = MockedRequestResponse(content=json.dumps(data))
        assert get_subscriptions(agenda_slug='foo') == []
        assert requests_get.call_args_list[0][0] == ('api/agenda/foo/subscription/',)
        assert requests_get.call_args_list[0][1]['remote_service']['url'] == 'http://chrono.example.org/'

    data = {'data': ['foo', 'bar']}
    with mock.patch('requests.Session.get') as requests_get:
        requests_get.return_value = MockedRequestResponse(content=json.dumps(data))
        assert get_subscriptions(agenda_slug='foo') == ['foo', 'bar']

    data = {'data': []}
    with mock.patch('requests.Session.get') as requests_get:
        requests_get.return_value = MockedRequestResponse(content=json.dumps(data))
        get_subscriptions(agenda_slug='foo', user_external_id='user:1')
        assert requests_get.call_args_list[0][0] == ('api/agenda/foo/subscription/?user_external_id=user:1',)
        assert requests_get.call_args_list[0][1]['remote_service']['url'] == 'http://chrono.example.org/'
        requests_get.reset_mock()
        get_subscriptions(agenda_slug='foo', date_start=datetime.date(2022, 9, 1))
        assert requests_get.call_args_list[0][0] == ('api/agenda/foo/subscription/?date_start=2022-09-01',)
        assert requests_get.call_args_list[0][1]['remote_service']['url'] == 'http://chrono.example.org/'
        requests_get.reset_mock()
        get_subscriptions(agenda_slug='foo', date_end=datetime.date(2022, 10, 1))
        assert requests_get.call_args_list[0][0] == ('api/agenda/foo/subscription/?date_end=2022-10-01',)
        assert requests_get.call_args_list[0][1]['remote_service']['url'] == 'http://chrono.example.org/'
        requests_get.reset_mock()
        get_subscriptions(
            agenda_slug='foo',
            user_external_id='user:1',
            date_start=datetime.date(2022, 9, 1),
            date_end=datetime.date(2022, 10, 1),
        )
        assert requests_get.call_args_list[0][0] == (
            'api/agenda/foo/subscription/?user_external_id=user:1&date_start=2022-09-01&date_end=2022-10-01',
        )
        assert requests_get.call_args_list[0][1]['remote_service']['url'] == 'http://chrono.example.org/'


def test_get_check_status_no_service(settings):
    settings.KNOWN_SERVICES = {}
    with pytest.raises(ChronoError) as e:
        get_check_status(
            agenda_slugs=['foo'],
            user_external_id='user:1',
            date_start=datetime.date(2022, 9, 1),
            date_end=datetime.date(2022, 10, 1),
        )
    assert str(e.value) == 'Unable to get check status'

    settings.KNOWN_SERVICES = {'other': []}
    with pytest.raises(ChronoError) as e:
        get_check_status(
            agenda_slugs=['foo'],
            user_external_id='user:1',
            date_start=datetime.date(2022, 9, 1),
            date_end=datetime.date(2022, 10, 1),
        )
    assert str(e.value) == 'Unable to get check status'


def test_get_check_status():
    with mock.patch('requests.Session.post') as requests_post:
        requests_post.side_effect = ConnectionError()
        with pytest.raises(ChronoError) as e:
            get_check_status(
                agenda_slugs=['foo', 'bar'],
                user_external_id='user:1',
                date_start=datetime.date(2022, 9, 1),
                date_end=datetime.date(2022, 10, 1),
            )
        assert str(e.value) == 'Unable to get check status'

    with mock.patch('requests.Session.post') as requests_post:
        mock_resp = Response()
        mock_resp.status_code = 500
        requests_post.return_value = mock_resp
        with pytest.raises(ChronoError) as e:
            get_check_status(
                agenda_slugs=['foo', 'bar'],
                user_external_id='user:1',
                date_start=datetime.date(2022, 9, 1),
                date_end=datetime.date(2022, 10, 1),
            )
        assert str(e.value) == 'Unable to get check status'

    with mock.patch('requests.Session.post') as requests_post:
        mock_resp = Response()
        mock_resp.status_code = 404
        requests_post.return_value = mock_resp
        with pytest.raises(ChronoError) as e:
            get_check_status(
                agenda_slugs=['foo', 'bar'],
                user_external_id='user:1',
                date_start=datetime.date(2022, 9, 1),
                date_end=datetime.date(2022, 10, 1),
            )
        assert str(e.value) == 'Unable to get check status'

    with mock.patch('requests.Session.post') as requests_post:
        requests_post.return_value = MockedRequestResponse(content=json.dumps({'foo': 'bar'}))
        with pytest.raises(ChronoError) as e:
            get_check_status(
                agenda_slugs=['foo', 'bar'],
                user_external_id='user:1',
                date_start=datetime.date(2022, 9, 1),
                date_end=datetime.date(2022, 10, 1),
            )
        assert str(e.value) == 'Unable to get check status'

    data = {'data': []}
    with mock.patch('requests.Session.post') as requests_post:
        requests_post.return_value = MockedRequestResponse(content=json.dumps(data))
        assert (
            get_check_status(
                agenda_slugs=['foo', 'bar'],
                user_external_id='user:1',
                date_start=datetime.date(2022, 9, 1),
                date_end=datetime.date(2022, 10, 1),
            )
            == []
        )
        assert requests_post.call_args_list[0][0] == ('api/agendas/events/check-status/',)
        assert requests_post.call_args_list[0][1]['json'] == {
            'user_external_id': 'user:1',
            'agendas': 'foo,bar',
            'date_start': '2022-09-01',
            'date_end': '2022-10-01',
        }
        assert requests_post.call_args_list[0][1]['remote_service']['url'] == 'http://chrono.example.org/'

    data = {'data': ['foo', 'bar']}
    with mock.patch('requests.Session.post') as requests_post:
        requests_post.return_value = MockedRequestResponse(content=json.dumps(data))
        assert get_check_status(
            agenda_slugs=['foo', 'bar'],
            user_external_id='user:1',
            date_start=datetime.date(2022, 9, 1),
            date_end=datetime.date(2022, 10, 1),
        ) == ['foo', 'bar']


def test_lock_events_check_no_service(settings):
    settings.KNOWN_SERVICES = {}
    with pytest.raises(ChronoError) as e:
        lock_events_check(
            agenda_slugs=['foo'],
            date_start=datetime.date(2022, 9, 1),
            date_end=datetime.date(2022, 10, 1),
        )
    assert str(e.value) == 'Unable to lock events check'

    settings.KNOWN_SERVICES = {'other': []}
    with pytest.raises(ChronoError) as e:
        lock_events_check(
            agenda_slugs=['foo'],
            date_start=datetime.date(2022, 9, 1),
            date_end=datetime.date(2022, 10, 1),
        )
    assert str(e.value) == 'Unable to lock events check'


def test_lock_events_check_status():
    with mock.patch('requests.Session.post') as requests_post:
        requests_post.side_effect = ConnectionError()
        with pytest.raises(ChronoError) as e:
            lock_events_check(
                agenda_slugs=['foo', 'bar'],
                date_start=datetime.date(2022, 9, 1),
                date_end=datetime.date(2022, 10, 1),
            )
        assert str(e.value) == 'Unable to lock events check'

    with mock.patch('requests.Session.post') as requests_post:
        mock_resp = Response()
        mock_resp.status_code = 500
        requests_post.return_value = mock_resp
        with pytest.raises(ChronoError) as e:
            lock_events_check(
                agenda_slugs=['foo', 'bar'],
                date_start=datetime.date(2022, 9, 1),
                date_end=datetime.date(2022, 10, 1),
            )
        assert str(e.value) == 'Unable to lock events check'

    with mock.patch('requests.Session.post') as requests_post:
        mock_resp = Response()
        mock_resp.status_code = 404
        requests_post.return_value = mock_resp
        with pytest.raises(ChronoError) as e:
            lock_events_check(
                agenda_slugs=['foo', 'bar'],
                date_start=datetime.date(2022, 9, 1),
                date_end=datetime.date(2022, 10, 1),
            )
        assert str(e.value) == 'Unable to lock events check'

    with mock.patch('requests.Session.post') as requests_post:
        requests_post.return_value = MockedRequestResponse(content=json.dumps({'foo': 'bar'}))
        lock_events_check(
            agenda_slugs=['foo', 'bar'],
            date_start=datetime.date(2022, 9, 1),
            date_end=datetime.date(2022, 10, 1),
        )
        assert requests_post.call_args_list[0][0] == ('/api/agendas/events/check-lock/',)
        assert requests_post.call_args_list[0][1]['json'] == {
            'check_locked': True,
            'agendas': 'foo,bar',
            'date_start': '2022-09-01',
            'date_end': '2022-10-01',
        }
        assert requests_post.call_args_list[0][1]['remote_service']['url'] == 'http://chrono.example.org/'


def test_unlock_events_check_no_service(settings):
    settings.KNOWN_SERVICES = {}
    with pytest.raises(ChronoError) as e:
        unlock_events_check(
            agenda_slugs=['foo'],
            date_start=datetime.date(2022, 9, 1),
            date_end=datetime.date(2022, 10, 1),
        )
    assert str(e.value) == 'Unable to unlock events check'

    settings.KNOWN_SERVICES = {'other': []}
    with pytest.raises(ChronoError) as e:
        unlock_events_check(
            agenda_slugs=['foo'],
            date_start=datetime.date(2022, 9, 1),
            date_end=datetime.date(2022, 10, 1),
        )
    assert str(e.value) == 'Unable to unlock events check'


def test_unlock_events_check_status():
    with mock.patch('requests.Session.post') as requests_post:
        requests_post.side_effect = ConnectionError()
        with pytest.raises(ChronoError) as e:
            unlock_events_check(
                agenda_slugs=['foo', 'bar'],
                date_start=datetime.date(2022, 9, 1),
                date_end=datetime.date(2022, 10, 1),
            )
        assert str(e.value) == 'Unable to unlock events check'

    with mock.patch('requests.Session.post') as requests_post:
        mock_resp = Response()
        mock_resp.status_code = 500
        requests_post.return_value = mock_resp
        with pytest.raises(ChronoError) as e:
            unlock_events_check(
                agenda_slugs=['foo', 'bar'],
                date_start=datetime.date(2022, 9, 1),
                date_end=datetime.date(2022, 10, 1),
            )
        assert str(e.value) == 'Unable to unlock events check'

    with mock.patch('requests.Session.post') as requests_post:
        mock_resp = Response()
        mock_resp.status_code = 404
        requests_post.return_value = mock_resp
        with pytest.raises(ChronoError) as e:
            unlock_events_check(
                agenda_slugs=['foo', 'bar'],
                date_start=datetime.date(2022, 9, 1),
                date_end=datetime.date(2022, 10, 1),
            )
        assert str(e.value) == 'Unable to unlock events check'

    with mock.patch('requests.Session.post') as requests_post:
        requests_post.return_value = MockedRequestResponse(content=json.dumps({'foo': 'bar'}))
        unlock_events_check(
            agenda_slugs=['foo', 'bar'],
            date_start=datetime.date(2022, 9, 1),
            date_end=datetime.date(2022, 10, 1),
        )
        assert requests_post.call_args_list[0][0] == ('/api/agendas/events/check-lock/',)
        assert requests_post.call_args_list[0][1]['json'] == {
            'check_locked': False,
            'agendas': 'foo,bar',
            'date_start': '2022-09-01',
            'date_end': '2022-10-01',
        }
        assert requests_post.call_args_list[0][1]['remote_service']['url'] == 'http://chrono.example.org/'


def test_mark_events_invoiced_no_service(settings):
    settings.KNOWN_SERVICES = {}
    with pytest.raises(ChronoError) as e:
        mark_events_invoiced(
            agenda_slugs=['foo'],
            date_start=datetime.date(2022, 9, 1),
            date_end=datetime.date(2022, 10, 1),
        )
    assert str(e.value) == 'Unable to mark events as invoiced'

    settings.KNOWN_SERVICES = {'other': []}
    with pytest.raises(ChronoError) as e:
        mark_events_invoiced(
            agenda_slugs=['foo'],
            date_start=datetime.date(2022, 9, 1),
            date_end=datetime.date(2022, 10, 1),
        )
    assert str(e.value) == 'Unable to mark events as invoiced'


def test_mark_events_invoiced_status():
    with mock.patch('requests.Session.post') as requests_post:
        requests_post.side_effect = ConnectionError()
        with pytest.raises(ChronoError) as e:
            mark_events_invoiced(
                agenda_slugs=['foo', 'bar'],
                date_start=datetime.date(2022, 9, 1),
                date_end=datetime.date(2022, 10, 1),
            )
        assert str(e.value) == 'Unable to mark events as invoiced'

    with mock.patch('requests.Session.post') as requests_post:
        mock_resp = Response()
        mock_resp.status_code = 500
        requests_post.return_value = mock_resp
        with pytest.raises(ChronoError) as e:
            mark_events_invoiced(
                agenda_slugs=['foo', 'bar'],
                date_start=datetime.date(2022, 9, 1),
                date_end=datetime.date(2022, 10, 1),
            )
        assert str(e.value) == 'Unable to mark events as invoiced'

    with mock.patch('requests.Session.post') as requests_post:
        mock_resp = Response()
        mock_resp.status_code = 404
        requests_post.return_value = mock_resp
        with pytest.raises(ChronoError) as e:
            mark_events_invoiced(
                agenda_slugs=['foo', 'bar'],
                date_start=datetime.date(2022, 9, 1),
                date_end=datetime.date(2022, 10, 1),
            )
        assert str(e.value) == 'Unable to mark events as invoiced'

    with mock.patch('requests.Session.post') as requests_post:
        requests_post.return_value = MockedRequestResponse(content=json.dumps({'foo': 'bar'}))
        mark_events_invoiced(
            agenda_slugs=['foo', 'bar'],
            date_start=datetime.date(2022, 9, 1),
            date_end=datetime.date(2022, 10, 1),
        )
        assert requests_post.call_args_list[0][0] == ('/api/agendas/events/invoiced/',)
        assert requests_post.call_args_list[0][1]['json'] == {
            'invoiced': True,
            'agendas': 'foo,bar',
            'date_start': '2022-09-01',
            'date_end': '2022-10-01',
        }
        assert requests_post.call_args_list[0][1]['remote_service']['url'] == 'http://chrono.example.org/'
