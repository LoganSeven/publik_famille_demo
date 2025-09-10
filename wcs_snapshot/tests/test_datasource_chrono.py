import os
from unittest import mock

import pytest
import responses

from wcs import fields
from wcs.data_sources import NamedDataSource
from wcs.data_sources_agendas import build_agenda_datasources, collect_agenda_data, translate_url
from wcs.formdef import FormDef
from wcs.qommon.http_request import HTTPRequest
from wcs.qommon.misc import ConnectionError

from .utilities import clean_temporary_pub, create_temporary_pub


def teardown_module(module):
    clean_temporary_pub()


@pytest.fixture
def pub(request):
    pub = create_temporary_pub()
    req = HTTPRequest(None, {'SCRIPT_NAME': '/', 'SERVER_NAME': 'example.net'})
    pub.set_app_dir(req)
    pub._set_request(req)
    pub.load_site_options()

    return pub


AGENDA_EVENTS_DATA = [
    {
        'api': {
            'datetimes_url': 'http://chrono.example.net/api/agenda/events-A/datetimes/',
        },
        'id': 'events-A',
        'kind': 'events',
        'text': 'Events A',
    },
    {
        'api': {
            'datetimes_url': 'http://chrono.example.net/api/agenda/events-B/datetimes/',
        },
        'id': 'events-B',
        'kind': 'events',
        'text': 'Events B',
    },
]


AGENDA_MEETINGS_DATA = [
    {
        'api': {'meetings_url': 'http://chrono.example.net/api/agenda/meetings-A/meetings/'},
        'id': 'meetings-A',
        'kind': 'meetings',
        'text': 'Meetings A',
        'free_range': False,
    },
    {
        'api': {
            'meetings_url': 'http://chrono.example.net/api/agenda/virtual-B/meetings/',
        },
        'id': 'virtual-B',
        'kind': 'virtual',
        'text': 'Virtual B',
        'free_range': False,
    },
]


AGENDA_MEETING_TYPES_DATA = {
    'meetings-A': [
        {
            'api': {
                'datetimes_url': 'http://chrono.example.net/api/agenda/meetings-A/meetings/mt-1/datetimes/'
            },
            'id': 'mt-1',
            'text': 'MT 1',
            'duration': 30,
        },
        {
            'api': {
                'datetimes_url': 'http://chrono.example.net/api/agenda/meetings-A/meetings/mt-2/datetimes/'
            },
            'id': 'mt-2',
            'text': 'MT 2',
            'duration': 60,
        },
    ],
    'virtual-B': [
        {
            'api': {
                'datetimes_url': 'http://chrono.example.net/api/agenda/virtual-B/meetings/mt-3/datetimes/'
            },
            'id': 'mt-3',
            'text': 'MT 3',
            'duration': 60,
        },
    ],
}


AGENDA_MEETINGS_FREE_RANGE_DATA = [
    {
        'api': {
            'resources_url': 'http://chrono.example.net/api/agenda/room-booking/resources/',
            'datetimes_url': 'http://chrono.example.net/api/agenda/room-booking/free-range/datetimes/',
        },
        'id': 'room-booking',
        'kind': 'meetings',
        'text': 'Room Booking',
        'free_range': True,
    },
]


AGENDA_RESOURCES_DATA = [
    {
        'id': 'big-room',
        'text': 'Big room',
    },
    {
        'id': 'small-room',
        'text': 'Small room',
    },
]


def test_translate_url(pub, chrono_url):
    pub.load_site_options()
    if not pub.site_options.has_section('variables'):
        pub.site_options.add_section('variables')
    pub.site_options.set('variables', 'foo_url', 'http://foo.bar')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)
    assert translate_url(pub, 'http://chrono.example.net/foo/bar/') == 'http://chrono.example.net/foo/bar/'

    pub.site_options.set('variables', 'foo_url', 'http://chrono.example.net/')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)
    assert translate_url(pub, 'http://chrono.example.net/foo/bar/') == '{{ foo_url }}foo/bar/'

    pub.site_options.set('variables', 'foo_url', 'http://foo.bar')
    pub.site_options.set('variables', 'agendas_url', 'http://chrono.example.net/')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)
    assert translate_url(pub, 'http://chrono.example.net/foo/bar/') == '{{ agendas_url }}foo/bar/'


@responses.activate
def test_collect_agenda_data(pub, chrono_url):
    pub.load_site_options()
    NamedDataSource.wipe()

    responses.get('http://chrono.example.net/api/agenda/', json={'data': []})
    assert collect_agenda_data(pub) == []
    assert len(responses.calls) == 1
    assert responses.calls[-1].request.url == 'http://chrono.example.net/api/agenda/'

    responses.reset()
    responses.get('http://chrono.example.net/api/agenda/', body=ConnectionError('...'))
    assert collect_agenda_data(pub) is None
    assert len(responses.calls) == 1
    assert responses.calls[-1].request.url == 'http://chrono.example.net/api/agenda/'

    # events agenda
    responses.reset()
    responses.get('http://chrono.example.net/api/agenda/', json={'data': AGENDA_EVENTS_DATA})

    assert collect_agenda_data(pub) == [
        {
            'slug': 'agenda-events-events-A',
            'text': 'Events A',
            'url': 'http://chrono.example.net/api/agenda/events-A/datetimes/',
            'qs_data': {'lock_code': '{% firstof form_submission_context_lock_code session_hash_id %}'},
        },
        {
            'slug': 'agenda-events-events-B',
            'text': 'Events B',
            'url': 'http://chrono.example.net/api/agenda/events-B/datetimes/',
            'qs_data': {'lock_code': '{% firstof form_submission_context_lock_code session_hash_id %}'},
        },
    ]
    assert len(responses.calls) == 1
    assert responses.calls[-1].request.url == 'http://chrono.example.net/api/agenda/'

    # meetings agenda
    responses.reset()
    responses.get('http://chrono.example.net/api/agenda/', json={'data': AGENDA_MEETINGS_DATA})
    responses.get(
        'http://chrono.example.net/api/agenda/meetings-A/meetings/',
        json={'data': AGENDA_MEETING_TYPES_DATA['meetings-A']},
    )
    responses.get(
        'http://chrono.example.net/api/agenda/virtual-B/meetings/',
        json={'data': AGENDA_MEETING_TYPES_DATA['virtual-B']},
    )

    assert collect_agenda_data(pub) == [
        {
            'slug': 'agenda-meetings-meetings-A-meetingtypes',
            'text': 'Meetings A - Meeting types',
            'url': 'http://chrono.example.net/api/agenda/meetings-A/meetings/',
        },
        {
            'slug': 'agenda-meetings-meetings-A-mtdynamic',
            'text': 'Meetings A - Slots of type form_var_meeting_type_raw',
            'url': 'http://chrono.example.net/api/agenda/meetings-A/meetings/{{ form_var_meeting_type_raw }}/datetimes/',
            'qs_data': {'lock_code': '{% firstof form_submission_context_lock_code session_hash_id %}'},
        },
        {
            'slug': 'agenda-meetings-meetings-A-mt-mt-1',
            'text': 'Meetings A - Slots of type MT 1 (30 minutes)',
            'url': 'http://chrono.example.net/api/agenda/meetings-A/meetings/mt-1/datetimes/',
            'qs_data': {'lock_code': '{% firstof form_submission_context_lock_code session_hash_id %}'},
        },
        {
            'slug': 'agenda-meetings-meetings-A-mt-mt-2',
            'text': 'Meetings A - Slots of type MT 2 (60 minutes)',
            'url': 'http://chrono.example.net/api/agenda/meetings-A/meetings/mt-2/datetimes/',
            'qs_data': {'lock_code': '{% firstof form_submission_context_lock_code session_hash_id %}'},
        },
        {
            'slug': 'agenda-virtual-virtual-B-meetingtypes',
            'text': 'Virtual B - Meeting types',
            'url': 'http://chrono.example.net/api/agenda/virtual-B/meetings/',
        },
        {
            'slug': 'agenda-virtual-virtual-B-mtdynamic',
            'text': 'Virtual B - Slots of type form_var_meeting_type_raw',
            'url': 'http://chrono.example.net/api/agenda/virtual-B/meetings/{{ form_var_meeting_type_raw }}/datetimes/',
            'qs_data': {'lock_code': '{% firstof form_submission_context_lock_code session_hash_id %}'},
        },
        {
            'slug': 'agenda-virtual-virtual-B-mt-mt-3',
            'text': 'Virtual B - Slots of type MT 3 (60 minutes)',
            'url': 'http://chrono.example.net/api/agenda/virtual-B/meetings/mt-3/datetimes/',
            'qs_data': {'lock_code': '{% firstof form_submission_context_lock_code session_hash_id %}'},
        },
    ]
    assert len(responses.calls) == 3
    assert responses.calls[0].request.url == 'http://chrono.example.net/api/agenda/'
    assert responses.calls[1].request.url == 'http://chrono.example.net/api/agenda/meetings-A/meetings/'
    assert responses.calls[2].request.url == 'http://chrono.example.net/api/agenda/virtual-B/meetings/'
    # if meeting types could not be collected
    responses.reset()
    responses.get('http://chrono.example.net/api/agenda/', json={'data': AGENDA_MEETINGS_DATA})
    responses.get(
        'http://chrono.example.net/api/agenda/meetings-A/meetings/',
        json={'data': AGENDA_MEETING_TYPES_DATA['meetings-A']},
    )
    responses.get('http://chrono.example.net/api/agenda/virtual-B/meetings/', body=ConnectionError('...'))

    assert collect_agenda_data(pub) is None
    assert len(responses.calls) == 3
    assert responses.calls[0].request.url == 'http://chrono.example.net/api/agenda/'
    assert responses.calls[1].request.url == 'http://chrono.example.net/api/agenda/meetings-A/meetings/'
    assert responses.calls[2].request.url == 'http://chrono.example.net/api/agenda/virtual-B/meetings/'

    # partial bookings meetings agenda
    responses.reset()
    responses.get('http://chrono.example.net/api/agenda/', json={'data': AGENDA_MEETINGS_FREE_RANGE_DATA})
    responses.get(
        'http://chrono.example.net/api/agenda/room-booking/resources/',
        json={'data': AGENDA_RESOURCES_DATA},
    )

    assert collect_agenda_data(pub) == [
        {
            'slug': 'agenda-partial-bookings-meetings-room-booking-resources',
            'text': 'Room Booking - Resources',
            'url': 'http://chrono.example.net/api/agenda/room-booking/resources/',
        },
        {
            'slug': 'agenda-partial-bookings-meetings-room-booking-resourcedynamic',
            'text': 'Room Booking - Slots of type form_var_resource_raw',
            'url': 'http://chrono.example.net/api/agenda/room-booking/free-range/datetimes/',
            'qs_data': {
                'lock_code': '{% firstof form_submission_context_lock_code session_hash_id %}',
                'resource': '{{ form_var_resource_raw|default:"" }}',
                'user_external_id': '{{ form_uuid }}',
            },
            'free_range': True,
        },
        {
            'slug': 'agenda-partial-bookings-meetings-room-booking-resource-big-room',
            'text': 'Room Booking - Slots for resource Big room',
            'url': 'http://chrono.example.net/api/agenda/room-booking/free-range/datetimes/',
            'qs_data': {
                'lock_code': '{% firstof form_submission_context_lock_code session_hash_id %}',
                'resource': 'big-room',
                'user_external_id': '{{ form_uuid }}',
            },
            'free_range': True,
        },
        {
            'slug': 'agenda-partial-bookings-meetings-room-booking-resource-small-room',
            'text': 'Room Booking - Slots for resource Small room',
            'url': 'http://chrono.example.net/api/agenda/room-booking/free-range/datetimes/',
            'qs_data': {
                'lock_code': '{% firstof form_submission_context_lock_code session_hash_id %}',
                'resource': 'small-room',
                'user_external_id': '{{ form_uuid }}',
            },
            'free_range': True,
        },
    ]
    assert len(responses.calls) == 2
    assert responses.calls[0].request.url == 'http://chrono.example.net/api/agenda/'
    assert responses.calls[1].request.url == 'http://chrono.example.net/api/agenda/room-booking/resources/'

    # if resources could not be collected
    responses.reset()
    responses.get('http://chrono.example.net/api/agenda/', json={'data': AGENDA_MEETINGS_FREE_RANGE_DATA})
    responses.get('http://chrono.example.net/api/agenda/room-booking/resources/', body=ConnectionError('...'))

    assert collect_agenda_data(pub) is None
    assert len(responses.calls) == 2
    assert responses.calls[0].request.url == 'http://chrono.example.net/api/agenda/'
    assert responses.calls[1].request.url == 'http://chrono.example.net/api/agenda/room-booking/resources/'

    responses.reset()
    responses.get('http://chrono.example.net/api/agenda/', json={'data': AGENDA_MEETINGS_DATA})
    responses.get('http://chrono.example.net/api/agenda/meetings-A/meetings/', body=ConnectionError('...'))

    assert collect_agenda_data(pub) is None
    assert len(responses.calls) == 2
    assert responses.calls[0].request.url == 'http://chrono.example.net/api/agenda/'
    assert responses.calls[1].request.url == 'http://chrono.example.net/api/agenda/meetings-A/meetings/'


@mock.patch('wcs.data_sources_agendas.collect_agenda_data')
def test_build_agenda_datasources_without_chrono(mock_collect, pub):
    NamedDataSource.wipe()
    build_agenda_datasources(pub)
    assert mock_collect.call_args_list == []
    assert NamedDataSource.count() == 0


@mock.patch('wcs.data_sources_agendas.collect_agenda_data')
def test_build_agenda_datasources(mock_collect, pub, chrono_url):
    pub.load_site_options()
    if not pub.site_options.has_section('variables'):
        pub.site_options.add_section('variables')
    pub.site_options.set('variables', 'agendas_url', 'http://chrono.example.net/')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)
    NamedDataSource.wipe()

    # create some datasource, with same urls, but external != 'agenda'
    ds = NamedDataSource(name='Foo A')
    ds.data_source = {'type': 'json', 'value': '{{ agendas_url }}api/agenda/events-A/datetimes/'}
    ds.store()
    ds = NamedDataSource(name='Foo B')
    ds.data_source = {'type': 'json', 'value': '{{ agendas_url }}api/agenda/events-B/datetimes/'}
    ds.store()
    ds = NamedDataSource(name='Foo A')
    ds.data_source = {'type': 'json', 'value': '{{ agendas_url }}api/agenda/events-A/datetimes/'}
    ds.external = 'agenda_manual'
    ds.store()
    ds = NamedDataSource(name='Foo B')
    ds.external = 'agenda_manual'
    ds.data_source = {'type': 'json', 'value': '{{ agendas_url }}api/agenda/events-B/datetimes/'}
    ds.store()

    # error during collect
    mock_collect.return_value = None
    build_agenda_datasources(pub)
    assert NamedDataSource.count() == 4  # no changes

    # no agenda datasource found in chrono
    mock_collect.return_value = []
    build_agenda_datasources(pub)
    assert NamedDataSource.count() == 4  # no changes

    # 2 agenda datasources found
    mock_collect.return_value = [
        {
            'slug': 'slug-A',
            'text': 'Events A',
            'url': 'http://chrono.example.net/api/agenda/events-A/datetimes/',
        },
        {
            'slug': 'slug-B',
            'text': 'Events B',
            'url': 'http://chrono.example.net/api/agenda/events-B/datetimes/',
        },
    ]

    # agenda datasources does not exist, create them
    build_agenda_datasources(pub)
    assert NamedDataSource.count() == 4 + 2
    datasource1 = NamedDataSource.get(4 + 1)
    datasource2 = NamedDataSource.get(4 + 2)
    assert datasource1.name == 'Events A'
    assert datasource1.slug == 'chrono_ds_slug_a'
    assert datasource1.external == 'agenda'
    assert datasource1.external_status is None
    assert datasource1.data_source == {
        'type': 'json',
        'value': '{{ agendas_url }}api/agenda/events-A/datetimes/',
    }
    assert datasource2.name == 'Events B'
    assert datasource2.slug == 'chrono_ds_slug_b'
    assert datasource2.external == 'agenda'
    assert datasource2.external_status is None
    assert datasource2.data_source == {
        'type': 'json',
        'value': '{{ agendas_url }}api/agenda/events-B/datetimes/',
    }

    # again, datasources already exist, but name is wrong => change it
    datasource1.name = 'wrong'
    datasource1.slug = 'wrong'
    datasource1.store()
    datasource2.name = 'wrong again'
    datasource2.slug = 'wrong_again'
    datasource2.store()
    build_agenda_datasources(pub)
    assert NamedDataSource.count() == 4 + 2
    datasource1 = NamedDataSource.get(4 + 1)
    datasource2 = NamedDataSource.get(4 + 2)
    assert datasource1.name == 'Events A'
    assert datasource1.slug == 'wrong'
    assert datasource2.name == 'Events B'
    assert datasource2.slug == 'wrong_again'

    # all datasources does not exist, one is unknown
    datasource1.data_source['value'] = '{{ agendas_url }}api/agenda/events-FOOBAR/datetimes/'
    datasource1.store()

    build_agenda_datasources(pub)
    assert NamedDataSource.count() == 4 + 2
    # first datasource was deleted, because not found and not used
    datasource2 = NamedDataSource.get(4 + 2)
    datasource3 = NamedDataSource.get(4 + 3)
    assert datasource2.name == 'Events B'
    assert datasource2.external == 'agenda'
    assert datasource2.external_status is None
    assert datasource2.data_source == {
        'type': 'json',
        'value': '{{ agendas_url }}api/agenda/events-B/datetimes/',
    }
    assert datasource3.name == 'Events A'
    assert datasource3.external == 'agenda'
    assert datasource3.external_status is None
    assert datasource3.data_source == {
        'type': 'json',
        'value': '{{ agendas_url }}api/agenda/events-A/datetimes/',
    }

    # all datasources does not exist, one is unknown but used
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'foobar'
    formdef.fields = [
        fields.ItemField(id='0', label='string', data_source={'type': datasource3.slug}),
    ]
    formdef.store()
    assert any(datasource3.usage_in_formdef(formdef))
    datasource3.data_source['value'] = '{{ agendas_url }}api/agenda/events-FOOBAR/datetimes/'
    datasource3.store()
    build_agenda_datasources(pub)
    assert NamedDataSource.count() == 4 + 3
    datasource2 = NamedDataSource.get(4 + 2)
    datasource3 = NamedDataSource.get(4 + 3)
    datasource4 = NamedDataSource.get(4 + 4)
    assert datasource2.name == 'Events B'
    assert datasource2.slug == 'wrong_again'
    assert datasource2.external == 'agenda'
    assert datasource2.external_status is None
    assert datasource2.data_source == {
        'type': 'json',
        'value': '{{ agendas_url }}api/agenda/events-B/datetimes/',
    }
    assert datasource3.name == 'Events A'
    assert datasource3.slug == 'chrono_ds_slug_a'
    assert datasource3.external == 'agenda'
    assert datasource3.external_status == 'not-found'
    assert datasource3.data_source == {
        'type': 'json',
        'value': '{{ agendas_url }}api/agenda/events-FOOBAR/datetimes/',
    }
    assert datasource4.name == 'Events A'
    assert datasource4.slug == 'chrono_ds_slug_a_1'
    assert datasource4.external == 'agenda'
    assert datasource4.external_status is None
    assert datasource4.data_source == {
        'type': 'json',
        'value': '{{ agendas_url }}api/agenda/events-A/datetimes/',
    }

    # a datasource was marked as unknown
    datasource4.external_status = 'not-found'
    datasource4.store()
    build_agenda_datasources(pub)
    assert NamedDataSource.count() == 4 + 3
    datasource4 = NamedDataSource.get(4 + 4)
    assert datasource4.external_status is None


@mock.patch('wcs.data_sources_agendas.collect_agenda_data')
def test_build_agenda_datasources_same_url_different_querystring(mock_collect, pub, chrono_url):
    pub.load_site_options()
    if not pub.site_options.has_section('variables'):
        pub.site_options.add_section('variables')
    pub.site_options.set('variables', 'agendas_url', 'http://chrono.example.net/')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)
    NamedDataSource.wipe()

    # 2 agenda datasources with same url but different url parameters
    mock_collect.return_value = [
        {
            'slug': 'agenda',
            'text': 'Agenda',
            'url': 'http://chrono.example.net/api/agenda/free-range/datetimes/',
            'qs_data': {'resource': '1', 'lock_code': 'xxx'},
            'free_range': True,
        },
        {
            'slug': 'agenda2',
            'text': 'Agenda 2',
            'url': 'http://chrono.example.net/api/agenda/free-range/datetimes/',
            'qs_data': {'resource': '2'},
            'free_range': True,
        },
    ]

    # agenda datasources does not exist, create them
    build_agenda_datasources(pub)
    assert NamedDataSource.count() == 2

    datasource1 = NamedDataSource.get(1)
    assert datasource1.name == 'Agenda'
    assert datasource1.slug == 'chrono_ds_agenda'
    assert datasource1.external == 'agenda'
    assert datasource1.external_type == 'free_range'
    assert datasource1.external_status is None
    assert datasource1.data_source == {
        'type': 'json',
        'value': '{{ agendas_url }}api/agenda/free-range/datetimes/',
    }
    assert datasource1.qs_data == {'resource': '1', 'lock_code': 'xxx'}

    datasource2 = NamedDataSource.get(2)
    assert datasource2.name == 'Agenda 2'
    assert datasource2.slug == 'chrono_ds_agenda2'
    assert datasource2.external == 'agenda'
    assert datasource1.external_type == 'free_range'
    assert datasource2.external_status is None
    assert datasource2.data_source == {
        'type': 'json',
        'value': '{{ agendas_url }}api/agenda/free-range/datetimes/',
    }
    assert datasource2.qs_data == {'resource': '2'}

    # agenda datasources already exist, do not create them
    build_agenda_datasources(pub)
    assert NamedDataSource.count() == 2


def test_agenda_datasources_migration(pub, chrono_url):
    pub.load_site_options()
    if not pub.site_options.has_section('variables'):
        pub.site_options.add_section('variables')
    pub.site_options.set('variables', 'agendas_url', 'http://chrono.example.net/')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)
    NamedDataSource.wipe()

    ds = NamedDataSource(name='Foo A')
    ds.data_source = {'type': 'json', 'value': 'http://chrono.example.net/api/agenda/events-A/datetimes/'}
    ds.external = 'agenda'
    ds.store()
    ds = NamedDataSource.get(ds.id)
    assert ds.data_source['value'] == '{{ agendas_url }}api/agenda/events-A/datetimes/'
    ds = NamedDataSource(name='Foo A')
    ds.data_source = {'type': 'json', 'value': 'http://chrono.example.net/api/agenda/events-A/datetimes/'}
    ds.external = 'agenda_manual'
    ds.store()
    ds = NamedDataSource.get(ds.id)
    assert ds.data_source['value'] == '{{ agendas_url }}api/agenda/events-A/datetimes/'
