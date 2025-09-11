# authentic2 - versatile identity manager
# Copyright (C) 2010-2023 Entr'ouvert
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

import pytest
from django.contrib.auth import get_user_model

from authentic2.a2_rbac.models import OrganizationalUnit as OU
from authentic2.a2_rbac.utils import get_default_ou
from authentic2.apps.journal.models import Event, EventType
from authentic2.models import Service
from authentic2_idp_cas.models import Service as CASService

from ..utils import basic_authorization_header

pytestmark = pytest.mark.django_db

User = get_user_model()


def test_api_statistics_list(app, admin):
    headers = basic_authorization_header(admin)
    resp = app.get('/api/statistics/', headers=headers)
    assert len(resp.json['data']) == 4
    login_new = {
        'filters': [
            {
                'default': 'month',
                'id': 'time_interval',
                'label': 'Time interval',
                'options': [
                    {'id': 'day', 'label': 'Day'},
                    {'id': 'month', 'label': 'Month'},
                    {'id': 'year', 'label': 'Year'},
                ],
                'required': True,
            },
            {
                'has_subfilters': True,
                'id': 'group_by',
                'label': 'Group by',
                'options': [
                    {'id': 'authentication_type', 'label': 'Authentication type'},
                    {'id': 'service', 'label': 'Service'},
                    {'id': 'service_ou', 'label': 'Organizational unit'},
                ],
            },
        ],
        'id': 'login-new',
        'name': 'Login count',
        'url': 'https://testserver/api/statistics/login_new/',
    }
    register_new = {
        'filters': [
            {
                'default': 'month',
                'id': 'time_interval',
                'label': 'Time interval',
                'options': [
                    {'id': 'day', 'label': 'Day'},
                    {'id': 'month', 'label': 'Month'},
                    {'id': 'year', 'label': 'Year'},
                ],
                'required': True,
            },
            {
                'has_subfilters': True,
                'id': 'group_by',
                'label': 'Group by',
                'options': [
                    {'id': 'authentication_type', 'label': 'Authentication type'},
                    {'id': 'service', 'label': 'Service'},
                    {'id': 'service_ou', 'label': 'Organizational unit'},
                ],
            },
        ],
        'id': 'registration-new',
        'name': 'Registration count',
        'url': 'https://testserver/api/statistics/registration_new/',
    }
    assert login_new in resp.json['data']
    assert register_new in resp.json['data']

    assert {
        'name': 'Inactivity alert count',
        'url': 'https://testserver/api/statistics/inactivity_alert/',
        'id': 'inactivity-alert',
        'filters': [
            {
                'id': 'time_interval',
                'label': 'Time interval',
                'options': [
                    {'id': 'day', 'label': 'Day'},
                    {'id': 'month', 'label': 'Month'},
                    {'id': 'year', 'label': 'Year'},
                ],
                'required': True,
                'default': 'month',
            },
        ],
    } in resp.json['data']

    assert {
        'name': 'Deletion for inactivity count',
        'url': 'https://testserver/api/statistics/inactivity_deletion/',
        'id': 'inactivity-deletion',
        'filters': [
            {
                'id': 'time_interval',
                'label': 'Time interval',
                'options': [
                    {'id': 'day', 'label': 'Day'},
                    {'id': 'month', 'label': 'Month'},
                    {'id': 'year', 'label': 'Year'},
                ],
                'required': True,
                'default': 'month',
            },
        ],
    } in resp.json['data']

    actions_id = {elt['id'] for elt in resp.json['data']}
    assert {'login-new', 'registration-new', 'inactivity-alert', 'inactivity-deletion'} == actions_id


def test_api_statistics_list_new(app, admin):
    headers = basic_authorization_header(admin)
    resp = app.get('/api/statistics/', headers=headers)
    login_stat = [x for x in resp.json['data'] if x['id'] == 'login-new'][0]
    assert login_stat == {
        'name': 'Login count',
        'url': 'https://testserver/api/statistics/login_new/',
        'id': 'login-new',
        'filters': [
            {
                'id': 'time_interval',
                'label': 'Time interval',
                'options': [
                    {'id': 'day', 'label': 'Day'},
                    {'id': 'month', 'label': 'Month'},
                    {'id': 'year', 'label': 'Year'},
                ],
                'required': True,
                'default': 'month',
            },
            {
                'id': 'group_by',
                'label': 'Group by',
                'options': [
                    {'id': 'authentication_type', 'label': 'Authentication type'},
                    {'id': 'service', 'label': 'Service'},
                    {'id': 'service_ou', 'label': 'Organizational unit'},
                ],
                'has_subfilters': True,
            },
        ],
    }

    registration_stat = [x for x in resp.json['data'] if x['id'] == 'registration-new'][0]
    assert registration_stat['name'] == 'Registration count'
    assert registration_stat['filters'] == login_stat['filters']


@pytest.mark.parametrize('endpoint', ['login_new', 'registration_new'])
def test_api_statistics_subfilters(app, admin, endpoint):
    service = Service.objects.create(name='Service1', slug='service1', ou=get_default_ou())
    service = Service.objects.create(name='Service2', slug='service2', ou=get_default_ou())

    headers = basic_authorization_header(admin)
    resp = app.get('/api/statistics/%s/' % endpoint, headers=headers)
    assert len(resp.json['data']['subfilters']) == 0

    resp = app.get('/api/statistics/%s/?group_by=authentication_type' % endpoint, headers=headers)
    assert len(resp.json['data']['subfilters']) == 1
    assert resp.json['data']['subfilters'][0] == {
        'id': 'service',
        'label': 'Service',
        'options': [
            {'id': 'service1 default', 'label': 'Service1'},
            {'id': 'service2 default', 'label': 'Service2'},
        ],
    }

    # adding second ou doesn't change anything
    ou = OU.objects.create(name='Second OU', slug='second')
    new_resp = app.get('/api/statistics/%s/?group_by=authentication_type' % endpoint, headers=headers)
    assert new_resp.json == resp.json

    # if there are services in two differents OUs, filter is shown
    service.ou = ou
    service.save()
    resp = app.get('/api/statistics/%s/?group_by=authentication_type' % endpoint, headers=headers)
    assert len(resp.json['data']['subfilters']) == 2
    assert resp.json['data']['subfilters'][1] == {
        'id': 'services_ou',
        'label': 'Services organizational unit',
        'options': [
            {'id': 'default', 'label': 'Default organizational unit'},
            {'id': 'second', 'label': 'Second OU'},
        ],
    }

    # same goes with users
    User.objects.create(username='john.doe', email='john.doe@example.com', ou=ou)
    resp = app.get('/api/statistics/%s/?group_by=authentication_type' % endpoint, headers=headers)
    assert len(resp.json['data']['subfilters']) == 3
    assert resp.json['data']['subfilters'][2] == {
        'id': 'users_ou',
        'label': 'Users organizational unit',
        'options': [
            {'id': 'default', 'label': 'Default organizational unit'},
            {'id': 'second', 'label': 'Second OU'},
        ],
    }

    resp = app.get('/api/statistics/%s/?group_by=service' % endpoint, headers=headers)
    assert len(resp.json['data']['subfilters']) == 0

    resp = app.get('/api/statistics/%s/?group_by=service_ou' % endpoint, headers=headers)
    assert len(resp.json['data']['subfilters']) == 0

    resp = app.get('/api/statistics/%s/' % endpoint, headers=headers)
    assert len(resp.json['data']['subfilters']) == 1
    assert resp.json['data']['subfilters'][0] == {
        'id': 'services_ou',
        'label': 'Services organizational unit',
        'options': [
            {'id': 'default', 'label': 'Default organizational unit'},
            {'id': 'second', 'label': 'Second OU'},
        ],
    }


@pytest.mark.parametrize(
    'event_type_name,event_name',
    [
        ('user.login', 'login_new'),
        ('user.registration', 'registration_new'),
    ],
)
def test_api_statistics(app, admin, freezer, event_type_name, event_name):
    headers = basic_authorization_header(admin)

    resp = app.get('/api/statistics/login_new/?time_interval=month', headers=headers)
    assert resp.json == {'data': {'series': [], 'x_labels': [], 'subfilters': []}, 'err': 0}

    user = User.objects.create(username='john.doe', email='john.doe@example.com', ou=get_default_ou())
    ou = OU.objects.create(name='Second OU', slug='second')
    portal = Service.objects.create(name='portal', slug='portal', ou=ou)
    agendas = CASService.objects.create(
        name='agendas', slug='agendas', ou=get_default_ou(), urls='https://agenda.example.net'
    )
    agenda_service = Service.objects.get(name='agendas')

    method = {'how': 'password-on-https'}
    method2 = {'how': 'france-connect'}

    event_type = EventType.objects.get_for_name(event_type_name)

    freezer.move_to('2020-02-03 12:00')
    Event.objects.create(type=event_type, references=[portal], data=dict(method, service_name=str(portal)))
    Event.objects.create(
        type=event_type, references=[agendas, user], user=user, data=dict(method, service_name=str(agendas))
    )

    freezer.move_to('2020-03-04 13:00')
    Event.objects.create(
        type=event_type, references=[agenda_service], data=dict(method, service_name=str(agendas))
    )
    Event.objects.create(type=event_type, references=[portal], data=dict(method2, service_name=str(portal)))

    params = {'group_by': 'authentication_type'}
    url = '/api/statistics/%s/' % event_name
    resp = app.get(url, headers=headers, params=params)
    assert resp.json['data']['x_labels'] == ['2020-02', '2020-03']
    assert resp.json['data']['series'] == [
        {'label': 'FranceConnect', 'data': [None, 1]},
        {'label': 'password', 'data': [2, 1]},
    ]

    # default time interval is 'month'
    month_data = resp.json['data']
    resp = app.get(url, headers=headers, params=params)
    assert month_data == resp.json['data']

    resp = app.get(url, headers=headers, params={'services_ou': 'default', **params})
    assert resp.json['data']['x_labels'] == ['2020-02', '2020-03']
    assert resp.json['data']['series'] == [{'label': 'password', 'data': [1, 1]}]

    resp = app.get(
        url, headers=headers, params={'service': 'agendas default', 'users_ou': 'default', **params}
    )
    assert resp.json['data']['x_labels'] == ['2020-02']
    assert resp.json['data']['series'] == [{'label': 'password', 'data': [1]}]

    resp = app.get(url, headers=headers, params={'users_ou': 'default', **params})
    assert resp.json['data']['x_labels'] == ['2020-02']
    assert resp.json['data']['series'] == [{'label': 'password', 'data': [1]}]

    resp = app.get(url, headers=headers, params={'service': 'agendas default', **params})
    assert resp.json['data']['x_labels'] == ['2020-02', '2020-03']
    assert resp.json['data']['series'] == [{'label': 'password', 'data': [1, 1]}]

    resp = app.get(url, headers=headers, params={'start': '2020-03-01T01:01', **params})
    assert resp.json['data']['x_labels'] == ['2020-03']
    assert resp.json['data']['series'] == [
        {'label': 'FranceConnect', 'data': [1]},
        {'label': 'password', 'data': [1]},
    ]

    resp = app.get(url, headers=headers, params={'end': '2020-03-01T01:01', **params})
    assert resp.json['data']['x_labels'] == ['2020-02']
    assert resp.json['data']['series'] == [{'label': 'password', 'data': [2]}]

    resp = app.get(url, headers=headers, params={'end': '2020-03-01', **params})
    assert resp.json['data']['x_labels'] == ['2020-02']
    assert resp.json['data']['series'] == [{'label': 'password', 'data': [2]}]

    resp = app.get(
        url, headers=headers, params={'time_interval': 'year', 'service': 'portal second', **params}
    )
    assert resp.json['data']['x_labels'] == ['2020']
    assert resp.json['data']['series'] == [
        {'label': 'FranceConnect', 'data': [1]},
        {'label': 'password', 'data': [1]},
    ]

    params['group_by'] = 'service'
    resp = app.get(url, headers=headers, params=params)
    assert resp.json['data']['x_labels'] == ['2020-02', '2020-03']
    assert resp.json['data']['series'] == [
        {'data': [1, 1], 'label': 'agendas'},
        {'data': [1, 1], 'label': 'portal'},
    ]

    params['group_by'] = 'service_ou'
    resp = app.get(url, headers=headers, params=params)
    assert resp.json['data']['x_labels'] == ['2020-02', '2020-03']
    assert resp.json['data']['series'] == [
        {'data': [1, 1], 'label': 'Default organizational unit'},
        {'data': [1, 1], 'label': 'Second OU'},
    ]

    # forbidden filter is ignored
    resp = app.get(url, headers=headers, params={'service': 'portal second', **params})
    assert resp.json['data']['x_labels'] == ['2020-02', '2020-03']
    assert resp.json['data']['series'] == [
        {'data': [1, 1], 'label': 'Default organizational unit'},
        {'data': [1, 1], 'label': 'Second OU'},
    ]

    del params['group_by']
    resp = app.get(url, headers=headers, params=params)
    assert resp.json['data']['x_labels'] == ['2020-02', '2020-03']
    assert len(resp.json['data']['series']) == 1
    assert resp.json['data']['series'][0]['data'] == [2, 2]
    assert 'count' in resp.json['data']['series'][0]['label']

    params = {'services_ou': 'second', **params}
    resp = app.get(url, headers=headers, params=params)
    assert resp.json['data']['x_labels'] == ['2020-02', '2020-03']
    assert len(resp.json['data']['series']) == 1
    assert resp.json['data']['series'][0]['data'] == [1, 1]
    assert resp.json['data']['series'][0]['label'].endswith(' count')

    params = {'services_ou': 'default', **params}
    resp = app.get(url, headers=headers, params=params)
    assert resp.json['data']['x_labels'] == ['2020-02', '2020-03']
    assert len(resp.json['data']['series']) == 1
    assert resp.json['data']['series'][0]['data'] == [1, 1]
    assert resp.json['data']['series'][0]['label'].endswith(' count')


@pytest.mark.parametrize(
    'event_type_name,event_name,event_description',
    [
        (
            'user.notification.inactivity',
            'inactivity_alert',
            'Inactivity alert count',
        ),
        (
            'user.deletion.inactivity',
            'inactivity_deletion',
            'Deletion for inactivity count',
        ),
    ],
)
def test_api_statistics_inactivity_events(
    app, admin, freezer, event_type_name, event_name, event_description
):
    headers = basic_authorization_header(admin)

    event_type = EventType.objects.get_for_name(event_type_name)

    freezer.move_to('2020-02-03 12:00')
    Event.objects.create(
        type=event_type, data=dict(days_of_inactivity=3, identifier='john.doe', days_to_deletion=5)
    )
    Event.objects.create(
        type=event_type, data=dict(days_of_inactivity=5, identifier='john.doe', days_to_deletion=8)
    )

    freezer.move_to('2020-03-04 13:00')
    Event.objects.create(
        type=event_type, data=dict(days_of_inactivity=3, identifier='john.doe', days_to_deletion=5)
    )
    Event.objects.create(
        type=event_type, data=dict(days_of_inactivity=5, identifier='john.doe', days_to_deletion=8)
    )

    params = {}
    url = '/api/statistics/%s/' % event_name
    resp = app.get(url, headers=headers, params=params)
    assert resp.json['data']['x_labels'] == ['2020-02', '2020-03']
    assert resp.json['data']['series'] == [
        {'label': event_description, 'data': [2, 2]},
    ]

    # default time interval is 'month'
    month_data = resp.json['data']
    resp = app.get(url, headers=headers, params=params)
    assert month_data == resp.json['data']

    resp = app.get(url, headers=headers, params=params)
    assert resp.json['data']['x_labels'] == ['2020-02', '2020-03']
    assert resp.json['data']['series'] == [{'label': event_description, 'data': [2, 2]}]

    resp = app.get(url, headers=headers, params={'users_ou': 'default', **params})
    assert resp.json['data']['x_labels'] == ['2020-02', '2020-03']
    assert resp.json['data']['series'] == [{'label': event_description, 'data': [2, 2]}]

    resp = app.get(url, headers=headers, params={'start': '2020-03-01T01:01', **params})
    assert resp.json['data']['x_labels'] == ['2020-03']
    assert resp.json['data']['series'] == [{'label': event_description, 'data': [2]}]

    resp = app.get(url, headers=headers, params={'end': '2020-03-01T01:01', **params})
    assert resp.json['data']['x_labels'] == ['2020-02']
    assert resp.json['data']['series'] == [{'label': event_description, 'data': [2]}]

    resp = app.get(url, headers=headers, params={'end': '2020-03-01', **params})
    assert resp.json['data']['x_labels'] == ['2020-02']
    assert resp.json['data']['series'] == [{'label': event_description, 'data': [2]}]

    resp = app.get(
        url, headers=headers, params={'time_interval': 'year', 'service': 'portal second', **params}
    )
    assert resp.json['data']['x_labels'] == ['2020']
    assert resp.json['data']['series'] == [{'label': event_description, 'data': [4]}]

    # forbidden filter is ignored
    resp = app.get(url, headers=headers, params={'service': 'portal second', **params})
    assert resp.json['data']['x_labels'] == ['2020-02', '2020-03']
    assert resp.json['data']['series'] == [{'label': event_description, 'data': [2, 2]}]
