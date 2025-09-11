# authentic2 - versatile identity manager
# Copyright (C) 2010-2020 Entr'ouvert
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import random
from datetime import datetime, timedelta
from unittest import mock

import pytest
import pytz
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db.transaction import atomic
from django.utils.timezone import make_aware, make_naive

from authentic2.a2_rbac.models import OrganizationalUnit as OU
from authentic2.a2_rbac.utils import get_default_ou
from authentic2.apps.journal.forms import JournalForm
from authentic2.apps.journal.journal import Journal, journal
from authentic2.apps.journal.models import (
    Event,
    EventType,
    EventTypeDefinition,
    clean_registry,
    prefetch_events_references,
)
from authentic2.models import Service

User = get_user_model()


@pytest.fixture
def clean_event_types_definition_registry(request):
    '''Protect EventTypeDefinition registry'''
    with clean_registry():
        yield


@pytest.fixture
def some_event_types(clean_event_types_definition_registry):
    class UserRegistrationRequest(EventTypeDefinition):
        name = 'user.registration.request'
        label = 'registration request'

        @classmethod
        def record(cls, *, email):
            return super().record(data={'email': email.lower()})

    class UserRegistration(EventTypeDefinition):
        name = 'user.registration'
        label = 'registration'

        @classmethod
        def record(cls, *, user, session, how):
            return super().record(user=user, session=session, data={'how': how})

    class UserLogin(EventTypeDefinition):
        name = 'user.login'
        label = 'login'

        @classmethod
        def record(cls, *, user, session, how):
            return super().record(user=user, session=session, data={'how': how})

    class UserLogout(EventTypeDefinition):
        name = 'user.logout'
        label = 'logout'

        @classmethod
        def record(cls, *, user, session):
            super().record(user=user, session=session)

    yield locals()


def test_models(db, django_assert_num_queries):
    Event.objects.all().delete()
    service = Service.objects.create(name='service', slug='service')
    service2 = Service.objects.create(name='service2', slug='service2')
    user = User.objects.create(username='john.doe')
    sso_event = EventType.objects.create(name='sso')
    whatever_event = EventType.objects.create(name='whatever')
    ev1 = Event.objects.create(user=user, type=sso_event, data={'method': 'oidc'}, references=[service])
    events = [ev1]
    events.append(Event.objects.create(type=whatever_event, references=[user]))
    for i in range(10):
        events.append(Event.objects.create(type=whatever_event, references=[service if i % 2 else service2]))
    ev2 = events[6]

    # check extended queryset methods
    assert Event.objects.count() == 12
    assert Event.objects.which_references(user).count() == 2
    assert Event.objects.which_references(User).count() == 2
    assert Event.objects.filter(user=user).count() == 1
    assert Event.objects.which_references(service).count() == 6
    assert Event.objects.which_references(Service).count() == 11
    assert Event.objects.from_cursor(ev1.cursor).count() == 12
    assert list(Event.objects.all()[ev2.cursor : 2]) == events[6:8]
    assert list(Event.objects.all()[-4 : ev2.cursor]) == events[3:7]
    assert set(Event.objects.which_references(service)[0].references) == {service}

    # verify type, user and service are prefetched
    with django_assert_num_queries(3):
        events = list(Event.objects.prefetch_references())
        assert len(events) == 12
        event = events[0]
        assert event.type.name == 'sso'
        assert event.user == user
        assert len(event.references) == 1
        assert event.references[0] == service

    # check foreign key constraints are not enforced, log should not change if an object is deleted
    Service.objects.all().delete()
    User.objects.all().delete()
    assert Event.objects.count() == 12
    assert Event.objects.filter(user_id=user.id).count() == 1
    assert Event.objects.which_references(user).count() == 2
    assert Event.objects.which_references(service).count() == 6
    assert list(Event.objects.all())


def test_null_references(db):
    Event.objects.all().delete()
    event_type = EventType.objects.get_for_name('user.login')
    event = Event.objects.create(type=event_type, references=[None])
    assert list(event.get_typed_references(Service)) == [None]

    events = list(Event.objects.all())
    prefetch_events_references(events)


def test_references(db):
    Event.objects.all().delete()
    user = User.objects.create(username='user')
    service = Service.objects.create(name='service', slug='service')

    event_type = EventType.objects.get_for_name('user.login')
    event = Event.objects.create(type=event_type, references=[user, service], user=user)
    event = Event.objects.get()
    assert list(event.get_typed_references(None, Service)) == [None, service]
    event = Event.objects.get()
    assert list(event.get_typed_references(User, None)) == [user, None]
    event = Event.objects.get()
    assert list(event.get_typed_references(Service, User)) == [None, None]
    assert list(event.get_typed_references(User, Service)) == [user, service]

    user.delete()
    service.delete()

    event = Event.objects.get()
    assert list(event.get_typed_references(None, Service)) == [None, None]
    event = Event.objects.get()
    assert list(event.get_typed_references(User, None)) == [None, None]
    event = Event.objects.get()
    assert list(event.get_typed_references(Service, User)) == [None, None]
    assert event.user is None


def test_event_types(clean_event_types_definition_registry):
    class UserEventTypes(EventTypeDefinition):
        name = 'user'
        label = 'User events'

    class SSO(UserEventTypes):
        name = 'user.sso'
        label = 'Single sign On'

    # user is an abstract type
    assert EventTypeDefinition.get_for_name('user') is UserEventTypes
    assert EventTypeDefinition.get_for_name('user.sso') is SSO

    with pytest.raises(AssertionError, match='already registered'):
        # pylint: disable=unused-variable
        class SSO2(UserEventTypes):
            name = 'user.sso'
            label = 'Single Sign On'


@pytest.mark.urls('tests.test_journal_app.urls')
def test_integration(clean_event_types_definition_registry, app_factory, db, settings):
    Event.objects.all().delete()
    settings.INSTALLED_APPS = [
        'django.contrib.auth',
        'django.contrib.sessions',
        'authentic2.custom_user',
        'authentic2.apps.journal',
        'tests.test_journal_app',
    ]
    app = app_factory()

    # the whole test is in a transaction :/
    app.get('/login/john.doe/')

    assert Event.objects.count() == 1
    event = Event.objects.get()
    assert event.type.name == 'login'
    assert event.user.username == 'john.doe'
    assert event.session_id == app.session.session_key
    assert event.reference_ids is None
    assert event.data is None


@pytest.fixture
def random_events(db):
    Event.objects.all().delete()
    count = 100
    from_date = make_aware(datetime(2000, 1, 1))
    to_date = make_aware(datetime(2010, 1, 1))
    duration = (to_date - from_date).total_seconds() - 1
    events = []
    event_types = []
    for name in 'abcdef':
        event_types.append(EventType.objects.create(name=name))

    for _ in range(count):
        events.append(
            Event(
                type=random.choice(event_types),
                timestamp=from_date + timedelta(seconds=random.randrange(0, duration)),
            )
        )
    Event.objects.bulk_create(events)
    return list(Event.objects.order_by('timestamp', 'id'))


def test_journal_form_date_hierarchy(random_events, rf):
    request = rf.get('/')
    form = JournalForm(data=request.GET)
    assert len(form.years) > 1  # 1 chance on 10**100 of false negative
    assert all(2000 <= year < 2010 for year in form.years)
    assert form.months == []
    assert form.days == []
    assert form.get_queryset().count() == 100

    year = random.choice(form.years)
    request = rf.get('/?year=%s' % year)
    form = JournalForm(data=request.GET)
    assert len(form.years) > 1
    assert all(2000 <= year < 2010 for year in form.years)
    assert len(form.months)
    assert all(1 <= month <= 12 for month in form.months)
    assert form.days == []
    assert form.get_queryset().count() == len(
        [
            # use make_naive() as filter(timestamp__year=..) convert value to local datetime
            # but event.timestamp only return UTC timezoned datetimes.
            event
            for event in random_events
            if make_naive(event.timestamp).year == year
        ]
    )

    month = random.choice(form.months)
    request = rf.get('/?year=%s&month=%s' % (year, month))
    form = JournalForm(data=request.GET)
    assert len(form.years) > 1
    assert all(2000 <= year < 2010 for year in form.years)
    assert len(form.months)
    assert all(1 <= month <= 12 for month in form.months)
    assert len(form.days)
    assert all(1 <= day <= 31 for day in form.days)
    assert form.get_queryset().count() == len(
        [
            # use make_naive() as filter(timestamp__year=..) convert value to local datetime
            # but event.timestamp only return UTC timezoned datetimes.
            event
            for event in random_events
            if make_naive(event.timestamp).year == year and make_naive(event.timestamp).month == month
        ]
    )

    day = random.choice(form.days)
    datetime(year, month, day)
    request = rf.get('/?year=%s&month=%s&day=%s' % (year, month, day))
    form = JournalForm(data=request.GET)
    assert len(form.years) > 1
    assert all(2000 <= year < 2010 for year in form.years)
    assert len(form.months) > 1
    assert all(1 <= month <= 12 for month in form.months)
    assert len(form.days)
    assert all(1 <= day <= 31 for day in form.days)
    assert form.get_queryset().count() == len(
        [
            event
            for event in random_events
            if make_naive(event.timestamp).year == year
            and make_naive(event.timestamp).month == month
            and make_naive(event.timestamp).day == day
        ]
    )


def test_journal_form_pagination(random_events, rf):
    request = rf.get('/')
    page = JournalForm(data=request.GET).page
    assert not page.is_first_page
    assert page.is_last_page
    assert not page.next_page_url
    assert page.previous_page_url
    assert page.events == random_events[-page.limit :]

    request = rf.get('/' + page.previous_page_url)
    page = JournalForm(data=request.GET).page
    assert not page.is_first_page
    assert not page.is_last_page
    assert page.next_page_url
    assert page.previous_page_url
    assert page.events == random_events[-2 * page.limit : -page.limit]

    request = rf.get('/' + page.previous_page_url)
    page = JournalForm(data=request.GET).page
    assert not page.is_first_page
    assert not page.is_last_page
    assert page.next_page_url
    assert page.previous_page_url
    assert page.events == random_events[-3 * page.limit : -2 * page.limit]

    request = rf.get('/' + page.next_page_url)
    form = JournalForm(data=request.GET)
    page = form.page
    assert not page.is_first_page
    assert not page.is_last_page
    assert page.next_page_url
    assert page.previous_page_url
    assert page.events == random_events[-2 * page.limit : -page.limit]

    event_after_the_first_page = random_events[page.limit]
    request = rf.get('/' + form.make_url('before_cursor', event_after_the_first_page.cursor))
    form = JournalForm(data=request.GET)
    page = form.page
    assert page.is_first_page
    assert not page.is_last_page
    assert page.next_page_url
    assert not page.previous_page_url
    assert page.events == random_events[: page.limit]

    # Test cursors out of queryset range
    request = rf.get('/?' + form.make_url('after_cursor', random_events[0].cursor))
    form = JournalForm(
        queryset=Event.objects.filter(
            timestamp__range=[random_events[1].timestamp, random_events[20].timestamp]
        ),
        data=request.GET,
    )
    page = form.page
    assert page.is_first_page
    assert page.is_last_page
    assert not page.previous_page_url
    assert not page.next_page_url
    assert page.events == random_events[1:21]

    request = rf.get('/' + form.make_url('before_cursor', random_events[21].cursor))
    page = JournalForm(
        queryset=Event.objects.filter(
            timestamp__range=[random_events[1].timestamp, random_events[20].timestamp]
        ),
        data=request.GET,
    ).page
    assert page.is_first_page
    assert page.is_last_page
    assert not page.previous_page_url
    assert not page.next_page_url
    assert page.events == random_events[1:21]


@pytest.fixture
def user_events(db, some_event_types):
    user = User.objects.create(username='john.doe', email='john.doe@example.com')

    journal = Journal(user=user)
    count = 100

    journal.record('user.registration.request', email=user.email)
    journal.record('user.registration', how='france-connect')
    journal.record('user.logout')

    for _ in range(count):
        journal.record('user.login', how='france-connect')
        journal.record('user.logout')

    return list(Event.objects.order_by('timestamp', 'id'))


def test_journal_form_search(user_events, rf):
    request = rf.get('/')
    form = JournalForm(data=request.GET)
    assert form.get_queryset().count() == len(user_events)

    request = rf.get('/', data={'search': 'email:jane.doe@example.com'})
    form = JournalForm(data=request.GET)
    assert form.get_queryset().count() == 0

    request = rf.get('/', data={'search': 'email:john.doe@example.com event:registration'})
    form = JournalForm(data=request.GET)
    assert form.get_queryset().count() == 2

    request = rf.get('/', data={'search': 'email:@example.com event:registration'})
    form = JournalForm(data=request.GET)
    assert form.get_queryset().count() == 2

    User.objects.update(username='john doe')

    request = rf.get('/', data={'search': 'username:"john doe" event:registration'})
    form = JournalForm(data=request.GET)
    assert form.get_queryset().count() == 1

    # unhandled lexems make the queryset empty
    request = rf.get('/', data={'search': 'john doe event:registration'})
    form = JournalForm(data=request.GET)
    assert form.get_queryset().count() == 0

    # unhandled prefix make unhandled lexems
    request = rf.get('/', data={'search': 'test:john'})
    form = JournalForm(data=request.GET)
    assert form.get_queryset().count() == 0


def test_cleanup(user_events, some_event_types, freezer, monkeypatch):
    monkeypatch.setattr(some_event_types['UserRegistration'], 'retention_days', 0)

    count = Event.objects.count()
    freezer.move_to(timedelta(days=365 - 1))
    call_command('cleanupauthentic')
    assert Event.objects.count() == count
    freezer.move_to(timedelta(days=2))
    call_command('cleanupauthentic')
    assert Event.objects.count() == 1


def test_record_exception_handling(db, some_event_types, caplog):
    journal = Journal()
    journal.record('user.registration.request', email='john.doe@example.com')
    assert len(caplog.records) == 0
    with mock.patch.object(
        some_event_types['UserRegistrationRequest'], 'record', side_effect=Exception('boum')
    ):
        journal.record('user.registration.request', email='john.doe@example.com')
    assert len(caplog.records) == 1
    assert caplog.records[0].levelname == 'ERROR'
    assert caplog.records[0].message == 'failure to record event "user.registration.request"'


def test_message_in_context_exception_handling(db, some_event_types, caplog):
    Event.objects.all().delete()
    user = User.objects.create(username='john.doe', email='john.doe@example.com')
    journal = Journal()
    journal.record('user.login', user=user, how='password')
    event = Event.objects.get()

    assert event.message
    assert not (caplog.records)

    caplog.clear()
    with mock.patch.object(some_event_types['UserLogin'], 'get_message', side_effect=Exception('boum')):
        assert event.message
    assert len(caplog.records) == 1
    assert caplog.records[0].levelname == 'ERROR'
    assert caplog.records[0].message == 'could not render message of event type "user.login"'

    caplog.clear()
    with mock.patch.object(some_event_types['UserLogin'], 'get_message', side_effect=Exception('boum')):
        assert event.message_in_context(None)
    assert len(caplog.records) == 1
    assert caplog.records[0].levelname == 'ERROR'
    assert caplog.records[0].message == 'could not render message of event type "user.login"'


@pytest.mark.parametrize('event_type_name', ['user.login', 'user.registration'])
def test_statistics(db, event_type_name, freezer):
    Event.objects.all().delete()
    user = User.objects.create(username='john.doe', email='john.doe@example.com', ou=get_default_ou())
    ou = OU.objects.create(name='Second OU')
    user2 = User.objects.create(username='jane.doe', email='jane.doe@example.com', ou=ou)

    portal = Service.objects.create(name='portal', slug='portal', ou=ou)
    agendas = Service.objects.create(name='agendas', slug='agendas', ou=get_default_ou())
    forms = Service.objects.create(name='forms', slug='forms', ou=get_default_ou())

    method = {'how': 'password-on-https'}
    method2 = {'how': 'france-connect'}

    event_type = EventType.objects.get_for_name(event_type_name)
    event_type_definition = event_type.definition

    stats = event_type_definition.get_method_statistics('timestamp')
    assert stats == {'series': [], 'x_labels': []}

    stats = event_type_definition.get_method_statistics('month')
    assert stats == {'series': [], 'x_labels': []}

    def create_event(user, service=None, data=None):
        data = (data or {}).copy()
        references = [user]
        if service:
            references.append(service)
            data['service_name'] = str(service)
        Event.objects.create(type=event_type, references=references, user=user, data=data)

    freezer.move_to('2020-02-03 12:00')
    create_event(user, portal, method)
    create_event(user2, portal, method)

    freezer.move_to('2020-02-03 13:00')
    create_event(user, portal, method2)
    create_event(user2, portal, method2)

    freezer.move_to('2020-03-03 12:00')
    create_event(user, portal, method)
    create_event(user, agendas, method)
    create_event(user, forms, method)
    create_event(user)

    stats = event_type_definition.get_method_statistics('timestamp')
    assert stats == {
        'x_labels': ['2020-02-03T12:00:00+00:00', '2020-02-03T13:00:00+00:00', '2020-03-03T12:00:00+00:00'],
        'series': [
            {'label': 'None', 'data': [None, None, 1]},
            {'label': 'FranceConnect', 'data': [None, 2, None]},
            {'label': 'password', 'data': [2, None, 3]},
        ],
    }

    start = datetime(year=2020, month=2, day=3, hour=12, minute=30, tzinfo=pytz.UTC)
    end = datetime(year=2020, month=2, day=3, hour=13, minute=30, tzinfo=pytz.UTC)
    stats = event_type_definition.get_method_statistics('timestamp', start=start, end=end)
    assert stats == {
        'x_labels': ['2020-02-03T13:00:00+00:00'],
        'series': [
            {'label': 'FranceConnect', 'data': [2]},
        ],
    }

    stats = event_type_definition.get_method_statistics('month')
    assert stats == {
        'x_labels': ['2020-02', '2020-03'],
        'series': [
            {'data': [None, 1], 'label': 'None'},
            {'data': [2, None], 'label': 'FranceConnect'},
            {'data': [2, 3], 'label': 'password'},
        ],
    }

    stats = event_type_definition.get_method_statistics('month', services_ou=get_default_ou())
    assert stats == {
        'x_labels': ['2020-03'],
        'series': [
            {'label': 'password', 'data': [2]},
        ],
    }

    stats = event_type_definition.get_method_statistics('month', services_ou=ou)
    assert stats == {
        'x_labels': ['2020-02', '2020-03'],
        'series': [
            {'label': 'FranceConnect', 'data': [2, None]},
            {'label': 'password', 'data': [2, 1]},
        ],
    }

    stats = event_type_definition.get_method_statistics('month', users_ou=ou)
    assert stats == {
        'x_labels': ['2020-02'],
        'series': [
            {'data': [1], 'label': 'FranceConnect'},
            {'data': [1], 'label': 'password'},
        ],
    }

    stats = event_type_definition.get_method_statistics('month', service=portal)
    assert stats == {
        'x_labels': ['2020-02', '2020-03'],
        'series': [
            {'label': 'FranceConnect', 'data': [2, None]},
            {'label': 'password', 'data': [2, 1]},
        ],
    }

    stats = event_type_definition.get_method_statistics('month', service=agendas, users_ou=get_default_ou())
    assert stats == {
        'x_labels': ['2020-03'],
        'series': [{'label': 'password', 'data': [1]}],
    }

    stats = event_type_definition.get_method_statistics('year')
    assert stats == {
        'x_labels': ['2020'],
        'series': [
            {'data': [1], 'label': 'None'},
            {'data': [2], 'label': 'FranceConnect'},
            {'data': [5], 'label': 'password'},
        ],
    }

    stats = event_type_definition.get_service_statistics('month')
    assert stats == {
        'x_labels': ['2020-02', '2020-03'],
        'series': [
            {'data': [None, 1], 'label': 'None'},
            {'data': [None, 1], 'label': 'agendas'},
            {'data': [None, 1], 'label': 'forms'},
            {'data': [4, 1], 'label': 'portal'},
        ],
    }

    stats = event_type_definition.get_service_ou_statistics('month')
    assert stats == {
        'x_labels': ['2020-02', '2020-03'],
        'series': [
            {'data': [None, 1], 'label': 'None'},
            {'data': [None, 2], 'label': 'Default organizational unit'},
            {'data': [4, 1], 'label': 'Second OU'},
        ],
    }


def test_statistics_fill_date_gaps(db, freezer):
    Event.objects.all().delete()
    User.objects.create(username='john.doe', email='john.doe@example.com')
    method = {'how': 'password-on-https'}
    event_type = EventType.objects.get_for_name('user.login')

    freezer.move_to('2020-12-29 12:00')
    Event.objects.create(type=event_type, data=method)
    freezer.move_to('2021-01-02 13:00')
    Event.objects.create(type=event_type, data=method)

    event_type_definition = event_type.definition

    stats = event_type_definition.get_method_statistics('day')
    assert stats == {
        'x_labels': ['2020-12-29', '2020-12-30', '2020-12-31', '2021-01-01', '2021-01-02'],
        'series': [{'label': 'password', 'data': [1, None, None, None, 1]}],
    }

    Event.objects.all().delete()
    freezer.move_to('2020-11-29 12:00')
    Event.objects.create(type=event_type, data=method)
    freezer.move_to('2022-02-02 13:00')
    Event.objects.create(type=event_type, data=method)
    stats = event_type_definition.get_method_statistics('month')
    assert stats == {
        'x_labels': ['2020-11', '2020-12'] + ['2021-%02d' % i for i in range(1, 13)] + ['2022-01', '2022-02'],
        'series': [{'label': 'password', 'data': [1] + [None] * 14 + [1]}],
    }

    Event.objects.all().delete()
    freezer.move_to('2020-11-29 12:00')
    Event.objects.create(type=event_type, data=method)
    freezer.move_to('2025-02-02 13:00')
    Event.objects.create(type=event_type, data=method)
    stats = event_type_definition.get_method_statistics('year')
    assert stats == {
        'x_labels': ['2020', '2021', '2022', '2023', '2024', '2025'],
        'series': [{'label': 'password', 'data': [1, None, None, None, None, 1]}],
    }


def test_statistics_deleted_service(db, freezer):
    Event.objects.all().delete()
    user = User.objects.create(username='john.doe', email='john.doe@example.com')
    ou = OU.objects.create(name='Second OU')
    portal = Service.objects.create(name='portal', slug='portal', ou=ou)

    method = {'how': 'password-on-https'}
    event_type = EventType.objects.get_for_name('user.login')
    event_type_definition = event_type.definition

    freezer.move_to('2020-02-03 12:00')
    Event.objects.create(
        type=event_type, references=[user, portal], user=user, data=dict(method, service_name=str(portal))
    )
    Event.objects.create(type=event_type, references=[user], user=user, data=method)

    stats = event_type_definition.get_service_statistics('month')
    assert stats == {
        'x_labels': ['2020-02'],
        'series': [{'label': 'None', 'data': [1]}, {'label': 'portal', 'data': [1]}],
    }

    portal.delete()
    stats = event_type_definition.get_service_statistics('month')
    assert stats == {
        'x_labels': ['2020-02'],
        'series': [{'data': [1], 'label': 'None'}, {'data': [1], 'label': 'portal'}],
    }


def test_statistics_ou_with_no_service(db, freezer):
    Event.objects.all().delete()
    user = User.objects.create(username='john.doe', email='john.doe@example.com')
    portal = Service.objects.create(name='portal', slug='portal', ou=get_default_ou())

    method = {'how': 'password-on-https'}
    event_type = EventType.objects.get_for_name('user.login')
    event_type_definition = event_type.definition

    Event.objects.create(type=event_type, references=[user, portal], user=user, data=method)

    ou_with_no_service = OU.objects.create(name='Second OU')
    stats = event_type_definition.get_method_statistics('month', services_ou=ou_with_no_service)
    assert stats == {'x_labels': [], 'series': []}


def test_prefetcher(db):
    Event.objects.all().delete()
    event_type = EventType.objects.get_for_name('user.login')
    for _ in range(10):
        user = User.objects.create()
        Event.objects.create(type=event_type, user=user, references=[user])
        Event.objects.create(type=event_type, user=user, references=[user])

    User.objects.all().delete()

    events = list(Event.objects.all())
    prefetch_events_references(events)
    for event in events:
        assert event.user is None
        assert list(event.get_typed_references(User)) == [None]

    def prefetcher(model, pks):
        if not issubclass(model, User):
            return
        for pk in pks:
            yield pk, 'deleted %s' % pk

    events = list(Event.objects.all())
    prefetch_events_references(events, prefetcher=prefetcher)
    for event in events:
        s = 'deleted %s' % event.user_id
        assert event.user == s
        assert list(event.get_typed_references((str, User))) == [s]


def test_atomic_rollback_save(db, caplog, some_event_types):
    Event.objects.all().delete()
    try:
        with atomic():
            journal.record('user.registration.request', email='foo@example.com')
            raise RuntimeError()
    except RuntimeError:
        pass
    assert Event.objects.count() == 0
    journal.record_pending()
    assert Event.objects.count() == 1

    Event.objects.all().delete()
    try:
        with atomic():
            journal.record('user.logout', user=User.objects.create(), session=None)
            raise RuntimeError()
    except RuntimeError:
        pass
    assert Event.objects.count() == 0
    caplog.clear()
    journal.record_pending()
    assert len(journal._pending_records) == 0
    assert Event.objects.count() == 0
    assert len(caplog.records) == 1
    msg = caplog.records[0]
    assert msg.levelname == 'WARNING'
    assert msg.message.endswith(
        " event may have been rollback, but the record method didn't return event instance."
    )
    assert msg.message.startswith('user.logout(')
