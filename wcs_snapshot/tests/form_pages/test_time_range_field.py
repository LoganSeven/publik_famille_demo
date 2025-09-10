import json

import pytest
import responses

from wcs import fields
from wcs.data_sources import NamedDataSource
from wcs.formdef import FormDef

from ..utilities import clean_temporary_pub, create_temporary_pub, get_app

CHRONO_DATA = {
    'data': [
        {
            'id': '2025-06-27',
            'text': 'Fri 27 jui 2025',
            'verbose_label': 'Friday 27th June 2025',
            'opening_hours': [
                {'hour': '10:00', 'status': 'free'},
                {'hour': '10:30', 'status': 'free'},
                {'hour': '11:00', 'status': 'free'},
                {'hour': '11:30', 'status': 'closed'},
            ],
            'disabled': False,
        },
        {
            'id': '2025-06-30',
            'text': 'Mon 28 jui 2025',
            'verbose_label': 'Monday 28th June 2025',
            'opening_hours': [
                {'hour': '09:00', 'status': 'booked'},
                {'hour': '09:30', 'status': 'booked'},
                {'hour': '10:00', 'status': 'free'},
                {'hour': '10:30', 'status': 'closed'},
            ],
            'disabled': False,
        },
    ],
    'meta': {
        'minimal_booking_slots': 2,
        'maximal_booking_slots': 10,
        'api': {
            'fillslot_url': 'https://chrono.dev.publik.love/api/agenda/reservation-salle/free-range/fillslot/'
        },
    },
}


@pytest.fixture
def pub(request):
    pub = create_temporary_pub()

    NamedDataSource.wipe()
    FormDef.wipe()
    return pub


def teardown_module(module):
    clean_temporary_pub()


@pytest.fixture
def data_source():
    data_source = NamedDataSource(name='free range agenda')
    data_source.slug = 'chrono_ds_free_range_foobar'
    data_source.external = 'agenda'
    data_source.data_source = {
        'type': 'json',
        'value': 'http://chrono.example.net/api/agenda/free-range/datetimes/',
    }
    data_source.store()

    return data_source


@pytest.fixture
def formdef(data_source):
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [
        fields.TimeRangeField(id='1', label='Slot', data_source={'type': 'chrono_ds_free_range_foobar'})
    ]
    formdef.store()

    return formdef


@responses.activate
def test_form_time_range_field_back_and_submit(pub, formdef):
    responses.get('http://chrono.example.net/api/agenda/free-range/datetimes/', json=CHRONO_DATA)

    app = get_app(pub)
    resp = app.get('/test/')

    assert 'time-range-widget.js' in resp.text
    assert 'time-range-widget.css' in resp.text

    assert resp.pyquery('.TimeRange').attr('data-minimal-booking-slots') == '2'
    assert resp.pyquery('.TimeRange').attr('data-maximal-booking-slots') == '10'

    radio_buttons = resp.pyquery('.TimeRange--days-list input')
    assert radio_buttons[0].attrib['data-verbose-label'] == 'Friday 27th June 2025'
    assert json.loads(radio_buttons[0].attrib['data-opening-hours']) == [
        {'hour': '10:00', 'status': 'free'},
        {'hour': '10:30', 'status': 'free'},
        {'hour': '11:00', 'status': 'free'},
        {'hour': '11:30', 'status': 'closed'},
    ]
    assert radio_buttons[1].attrib['data-verbose-label'] == 'Monday 28th June 2025'
    assert json.loads(radio_buttons[1].attrib['data-opening-hours']) == [
        {'hour': '09:00', 'status': 'booked'},
        {'hour': '09:30', 'status': 'booked'},
        {'hour': '10:00', 'status': 'free'},
        {'hour': '10:30', 'status': 'closed'},
    ]

    assert resp.form['f1$day'].options == [('2025-06-27', False, None), ('2025-06-30', False, None)]
    assert resp.form['f1$start_hour'].options == [('', True, '---')]
    assert resp.form['f1$end_hour'].options == [('', True, '---')]

    # submit without hours
    resp.form['f1$day'] = '2025-06-27'
    resp = resp.form.submit('submit')

    assert resp.pyquery('#field-error-links').text() == 'The following field has an error: Slot'

    assert resp.form['f1$day'].options == [('2025-06-27', True, None), ('2025-06-30', False, None)]

    # day was selected so hours are listed
    assert resp.form['f1$start_hour'].options == [
        ('10:00', False, '10:00'),
        ('10:30', False, '10:30'),
        ('11:00', False, '11:00'),
    ]
    assert resp.form['f1$end_hour'].options == [
        ('10:30', False, '10:30'),
        ('11:00', False, '11:00'),
        ('11:30', False, '11:30'),
    ]

    resp.form['f1$start_hour'] = '10:30'
    resp.form['f1$end_hour'] = '11:30'
    resp = resp.form.submit('submit')

    assert 'Check values then click submit.' in resp.text
    assert 'On 2025-06-27 from 10:30 a.m. until 11:30 a.m.' in resp.text

    # get back to the time range field
    resp = resp.form.submit('previous')

    # check field values are retained
    assert resp.form['f1$day'].options == [('2025-06-27', True, None), ('2025-06-30', False, None)]

    # day was selected so hours are now listed
    assert resp.form['f1$start_hour'].options == [
        ('10:00', False, '10:00'),
        ('10:30', True, '10:30'),
        ('11:00', False, '11:00'),
    ]
    assert resp.form['f1$end_hour'].options == [
        ('10:30', False, '10:30'),
        ('11:00', False, '11:00'),
        ('11:30', True, '11:30'),
    ]

    # back to summary page
    resp = resp.form.submit('submit')
    assert 'On 2025-06-27 from 10:30 a.m. until 11:30 a.m.' in resp.text

    # and submitting the form
    resp = resp.form.submit('submit').follow()
    assert 'The form has been recorded' in resp.text

    formdata = formdef.data_class().select()[0]
    assert formdata.data == {
        '1': {
            'api': {
                'fillslot_url': 'https://chrono.dev.publik.love/api/agenda/reservation-salle/free-range/fillslot/'
            },
            'start_datetime': '2025-06-27 10:30',
            'end_datetime': '2025-06-27 11:30',
        },
        '1_display': 'On 2025-06-27 from 10:30 a.m. until 11:30 a.m.',
    }


@responses.activate
def test_form_time_range_field_submit_no_validation_page(pub, formdef):
    responses.get('http://chrono.example.net/api/agenda/free-range/datetimes/', json=CHRONO_DATA)

    formdef.confirmation = False
    formdef.store()

    app = get_app(pub)
    resp = app.get('/test/')

    resp.form['f1$day'] = '2025-06-27'
    resp.form['f1$start_hour'].force_value('10:30')
    resp.form['f1$end_hour'].force_value('11:30')

    resp = resp.form.submit('submit').follow()
    assert 'On 2025-06-27 from 10:30 a.m. until 11:30 a.m.' in resp.text


@responses.activate
def test_form_time_range_field_no_datasource(pub, formdef):
    formdef.fields[0].data_source = None

    app = get_app(pub)
    resp = app.get('/test/')

    assert resp.form['f1$day'].options == [('', True, None)]


@responses.activate
def test_form_time_range_field_empty(pub, formdef):
    responses.get('http://chrono.example.net/api/agenda/free-range/datetimes/', json={'data': []})

    app = get_app(pub)
    resp = app.get('/test/')

    assert resp.form['f1$day'].options == [('', True, None)]


@responses.activate
def test_form_time_range_field_required(pub, formdef):
    responses.get('http://chrono.example.net/api/agenda/free-range/datetimes/', json=CHRONO_DATA)

    app = get_app(pub)
    resp = app.get('/test/')

    resp = resp.form.submit('submit')
    assert resp.pyquery('#field-error-links').text() == 'The following field has an error: Slot'

    formdef.fields[0].required = False
    formdef.store()

    resp = resp.form.submit('submit')
    assert 'Check values then click submit.' in resp.text


@responses.activate
def test_form_time_range_field_hours_validation(pub, formdef):
    responses.get('http://chrono.example.net/api/agenda/free-range/datetimes/', json=CHRONO_DATA)

    app = get_app(pub)
    resp = app.get('/test/')

    # valid day
    resp.form['f1$day'] = '2025-06-27'
    # hours not in datasource
    resp.form['f1$start_hour'].force_value('13:30')
    resp.form['f1$end_hour'].force_value('15:00')
    resp = resp.form.submit('submit')

    assert resp.pyquery('#form_error_f1__start_hour').text() == 'invalid value selected'
    assert resp.pyquery('#form_error_f1__end_hour').text() == 'invalid value selected'

    # valid day
    resp.form['f1$day'] = '2025-06-30'
    # hours in datasource, but booked
    resp.form['f1$start_hour'].force_value('09:30')
    resp.form['f1$end_hour'].force_value('10:00')
    resp = resp.form.submit('submit')

    assert resp.pyquery('#form_error_f1__start_hour').text() == 'invalid value selected'
    assert resp.pyquery('#form_error_f1__end_hour').text() == 'invalid value selected'


@responses.activate
def test_form_time_range_field_live(pub, data_source, formdef):
    responses.get('http://chrono.example.net/api/agenda/free-range/datetimes/', json=CHRONO_DATA)

    data_source.qs_data = {'resource': '{{ form_var_resource_raw }}'}
    data_source.store()

    formdef.fields.insert(
        0, fields.ItemField(id='0', label='Resource', varname='resource', items=['room-1', 'room-2'])
    )
    formdef.store()

    app = get_app(pub)
    resp = app.get('/test/')

    resp.form['f0'] = 'room-1'
    live_resp = app.post('/test/live', params=resp.form.submit_fields())
    assert 'items' not in live_resp.json['result']['1']

    live_resp = app.post('/test/live', params=resp.form.submit_fields() + [('modified_field_id[]', '0')])
    assert len(live_resp.json['result']['1']['items']) == 2
    assert len(live_resp.json['result']['1']['items'][0]['attributes']['opening_hours']) == 4
    assert live_resp.json['result']['1']['items'][0]['id'] == '2025-06-27'
    assert live_resp.json['result']['1']['items'][0]['text'] == 'Fri 27 jui 2025'
    assert live_resp.json['result']['1']['items'][0]['disabled'] is False
