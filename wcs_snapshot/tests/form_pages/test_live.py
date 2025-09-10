import datetime
import itertools
import json
import os
from unittest import mock

import pyquery
import pytest
import responses
from webtest import Checkbox, Hidden

from wcs import fields
from wcs.blocks import BlockDef
from wcs.carddef import CardDef
from wcs.data_sources import NamedDataSource
from wcs.formdef import FormDef
from wcs.logged_errors import LoggedError
from wcs.qommon import misc
from wcs.qommon.upload_storage import PicklableUpload
from wcs.sql import TransientData
from wcs.wf.form import WorkflowFormFieldsFormDef
from wcs.workflows import Workflow
from wcs.wscalls import NamedWsCall

from ..utilities import clean_temporary_pub, create_temporary_pub, get_app, login
from .test_all import create_user

CHRONO_DATA = [
    {
        'id': '30:2023-10-04-1000',
        'text': '4 octobre 2023 10:00',
        'agendas': [
            {
                'id': '1',
                'text': 'rdv1',
                'api': {
                    'fillslot_url': 'http://chrono.example.net/api/agenda/rdv1/fillslot/30:2023-10-04-1000/'
                },
            },
            {
                'id': '2',
                'text': 'rdv2',
                'api': {
                    'fillslot_url': 'http://chrono.example.net/api/agenda/rdv2/fillslot/30:2023-10-04-1000/'
                },
            },
        ],
    },
    {
        'id': '30:2023-10-04-1030',
        'text': '4 octobre 2023 10:30',
        'agendas': [
            {
                'id': '3',
                'text': 'rdv3',
                'api': {
                    'fillslot_url': 'http://chrono.example.net/api/agenda/rdv3/fillslot/30:2023-10-04-1000/'
                },
            },
            {
                'id': '4',
                'text': 'rdv4',
                'api': {
                    'fillslot_url': 'http://chrono.example.net/api/agenda/rdv4/fillslot/30:2023-10-04-1000/'
                },
            },
        ],
    },
]


def pytest_generate_tests(metafunc):
    if 'pub' in metafunc.fixturenames:
        metafunc.parametrize('pub', ['sql', 'sql-lazy'], indirect=True)


@pytest.fixture
def pub(request):
    pub = create_temporary_pub(
        lazy_mode=bool('lazy' in request.param),
    )
    pub.cfg['identification'] = {'methods': ['password']}
    pub.cfg['language'] = {'language': 'en'}
    pub.write_cfg()

    return pub


def teardown_module(module):
    clean_temporary_pub()


def test_field_live_condition(pub):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'Foo'
    formdef.fields = [
        fields.StringField(id='1', label='Bar', size='40', required='required', varname='bar'),
        fields.StringField(
            id='2',
            label='Foo',
            size='40',
            required='required',
            varname='foo',
            condition={'type': 'django', 'value': 'form_var_bar == "bye"'},
        ),
    ]
    formdef.store()

    app = get_app(pub)
    resp = app.get('/foo/')
    assert 'f1' in resp.form.fields
    assert 'f2' in resp.form.fields
    assert resp.html.find('div', {'data-field-id': '1'}).attrs['data-live-source'] == 'true'
    assert resp.html.find('div', {'data-field-id': '2'}).attrs.get('style') == 'display: none'
    resp.form['f1'] = 'hello'
    live_resp = app.post('/foo/live', params=resp.form.submit_fields())
    assert live_resp.json['result']['1']['visible']
    assert not live_resp.json['result']['2']['visible']
    resp.form['f1'] = 'bye'
    live_resp = app.post('/foo/live', params=resp.form.submit_fields())
    assert live_resp.json['result']['1']['visible']
    assert live_resp.json['result']['2']['visible']
    resp.form['f1'] = 'hello'
    resp = resp.form.submit('submit')
    assert 'Check values then click submit.' in resp.text
    assert 'name="f1"' in resp.text
    assert 'name="f2"' not in resp.text
    resp = resp.form.submit('submit')
    resp = resp.follow()
    assert 'Bar' in [x.text for x in resp.pyquery('p.label')]
    assert 'Foo' not in [x.text for x in resp.pyquery('p.label')]

    resp = get_app(pub).get('/foo/')
    assert 'f1' in resp.form.fields
    assert 'f2' in resp.form.fields
    resp.form['f1'] = 'bye'
    resp = resp.form.submit('submit')
    assert 'There were errors' in resp.text
    assert resp.html.find('div', {'data-field-id': '2'}).attrs.get('style') == ''
    resp.form['f2'] = 'bye'
    resp = resp.form.submit('submit')
    assert 'Check values then click submit.' in resp.text
    assert 'name="f1"' in resp.text
    assert 'name="f2"' in resp.text
    resp = resp.form.submit('submit')
    resp = resp.follow()
    assert 'Bar' in [x.text for x in resp.pyquery('p.label')]
    assert 'Foo' in [x.text for x in resp.pyquery('p.label')]


def test_field_live_condition_on_other_page(pub):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'Foo'
    formdef.fields = [
        fields.PageField(id='0', label='Page1'),
        fields.StringField(id='1', label='Bar', size='40', required='required', varname='bar'),
        fields.PageField(id='2', label='Page2'),
        fields.StringField(
            id='3',
            label='Foo',
            size='40',
            required='required',
            varname='foo',
            condition={'type': 'django', 'value': 'form_var_bar == "bye"'},
        ),
    ]
    formdef.store()

    app = get_app(pub)
    resp = app.get(formdef.get_url())
    assert 'f1' in resp.form.fields
    assert 'data-live-source' not in resp.html.find('div', {'data-field-id': '1'}).attrs


def test_field_live_items_condition(pub):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'Foo'
    formdef.fields = [
        fields.ItemsField(id='1', label='Bar', items=['a', 'b'], varname='bar'),
        fields.StringField(
            id='2',
            label='Foo',
            size='40',
            required='required',
            varname='foo',
            condition={'type': 'django', 'value': '"b" in form_var_bar'},
        ),
    ]
    formdef.store()

    create_user(pub)
    app = login(get_app(pub), username='foo', password='foo')
    resp = app.get('/foo/')
    assert 'f1$elementa' in resp.form.fields
    assert 'f1$elementb' in resp.form.fields
    assert 'f2' in resp.form.fields
    assert resp.html.find('div', {'data-field-id': '1'}).attrs['data-live-source'] == 'true'
    assert resp.html.find('div', {'data-field-id': '2'}).attrs.get('style') == 'display: none'
    resp.form['f1$elementa'].checked = True
    app.post('/foo/autosave', params=resp.form.submit_fields())
    live_resp = app.post('/foo/live', params=resp.form.submit_fields())
    assert live_resp.json['result']['1']['visible']
    assert not live_resp.json['result']['2']['visible']
    resp.form['f1$elementb'].checked = True
    app.post('/foo/autosave', params=resp.form.submit_fields())
    live_resp = app.post('/foo/live', params=resp.form.submit_fields())
    assert live_resp.json['result']['1']['visible']
    assert live_resp.json['result']['2']['visible']
    resp.form['f1$elementa'].checked = False
    resp.form['f1$elementb'].checked = False
    live_resp = app.post('/foo/live', params=resp.form.submit_fields())
    assert live_resp.json['result']['1']['visible']
    assert not live_resp.json['result']['2']['visible']


def test_live_field_condition_on_required_field(pub):
    # from https://dev.entrouvert.org/issues/27247
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'Foo'
    formdef.fields = [
        fields.PageField(id='0', label='1st page'),
        fields.ItemField(
            id='1',
            label='Bar',
            items=['oui', 'non'],
            display_mode='radio',
            required='required',
            varname='bar',
        ),
        fields.ItemField(
            id='2',
            label='Foo',
            size='40',
            required='required',
            hint='---',
            items=['plop'],
            condition={'type': 'django', 'value': 'form_var_bar == "oui"'},
        ),
        fields.PageField(id='3', label='1st page', condition={'type': 'django', 'value': 'True'}),
        fields.CommentField(id='4', label='HELLO!'),
    ]
    formdef.store()

    app = get_app(pub)
    resp = app.get('/foo/')
    assert 'f1' in resp.form.fields
    assert 'f2' in resp.form.fields
    assert resp.html.find('div', {'data-field-id': '1'}).attrs['data-live-source'] == 'true'
    assert resp.html.find('div', {'data-field-id': '2'}).attrs.get('style') == 'display: none'
    resp.form['f1'] = 'non'
    live_resp = app.post('/foo/live', params=resp.form.submit_fields())
    assert live_resp.json['result']['1']['visible']
    assert not live_resp.json['result']['2']['visible']
    resp = resp.form.submit('submit')
    assert 'HELLO' in resp.text
    resp = resp.form.submit('previous')
    resp.form['f1'] = 'oui'
    live_resp = app.post('/foo/live', params=resp.form.submit_fields())
    assert live_resp.json['result']['1']['visible']
    assert live_resp.json['result']['2']['visible']
    resp = resp.form.submit('submit')
    assert resp.pyquery('#form_error_f2').text() == 'required field'
    assert 'HELLO' not in resp.text


def test_field_live_items_condition_next_previous_page(pub):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'Foo'
    formdef.fields = [
        fields.PageField(id='0', label='page1'),
        fields.ItemsField(id='1', label='Bar', items=['a', 'b'], varname='bar', required='optional'),
        fields.StringField(
            id='2',
            label='Foo',
            size='40',
            required='optional',
            varname='foo',
            condition={'type': 'django', 'value': 'form_var_bar'},
        ),
        fields.PageField(id='3', label='page2'),
    ]
    formdef.store()

    create_user(pub)
    app = login(get_app(pub), username='foo', password='foo')
    resp = app.get('/foo/')
    assert 'f1$elementa' in resp.form.fields
    assert 'f1$elementb' in resp.form.fields
    assert 'f2' in resp.form.fields
    assert resp.html.find('div', {'data-field-id': '1'}).attrs['data-live-source'] == 'true'
    assert resp.html.find('div', {'data-field-id': '2'}).attrs.get('style') == 'display: none'
    live_resp = app.post('/foo/live', params=resp.form.submit_fields())
    assert not live_resp.json['result']['2']['visible']
    resp.form['f1$elementa'].checked = True
    app.post('/foo/autosave', params=resp.form.submit_fields())
    live_resp = app.post('/foo/live', params=resp.form.submit_fields())
    assert live_resp.json['result']['2']['visible']

    resp = resp.form.submit('submit')  # to page 2
    resp = resp.form.submit('previous')  # back to page 1
    live_resp = app.post('/foo/live', params=resp.form.submit_fields())
    assert live_resp.json['result']['2']['visible']
    resp.form['f1$elementa'].checked = False
    live_resp = app.post('/foo/live', params=resp.form.submit_fields() + [('modified_field_id[]', '1')])
    assert not live_resp.json['result']['2']['visible']


def test_field_live_condition_multipages(pub):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'Foo'
    formdef.fields = [
        fields.PageField(id='0', label='2nd page'),
        fields.StringField(id='1', label='Bar', size='40', required='required', varname='bar'),
        fields.StringField(
            id='2',
            label='Foo',
            size='40',
            required='required',
            varname='foo',
            condition={'type': 'django', 'value': 'form_var_bar == "bye"'},
        ),
        fields.PageField(id='3', label='1st page'),
        fields.StringField(id='4', label='Baz', size='40', required='required', varname='baz'),
    ]
    formdef.store()

    app = get_app(pub)
    resp = app.get('/foo/')
    assert 'f1' in resp.form.fields
    assert 'f2' in resp.form.fields
    assert resp.html.find('div', {'data-field-id': '1'}).attrs['data-live-source'] == 'true'
    assert resp.html.find('div', {'data-field-id': '2'}).attrs.get('style') == 'display: none'
    resp.form['f1'] = 'hello'
    live_resp = app.post('/foo/live', params=resp.form.submit_fields())
    assert live_resp.json['result']['1']['visible']
    assert not live_resp.json['result']['2']['visible']
    resp.form['f1'] = 'bye'
    live_resp = app.post('/foo/live', params=resp.form.submit_fields())
    assert live_resp.json['result']['1']['visible']
    assert live_resp.json['result']['2']['visible']
    resp.form['f1'] = 'bye'
    resp.form['f2'] = 'bye'
    resp = resp.form.submit('submit')
    resp = resp.form.submit('previous')
    assert resp.html.find('div', {'data-field-id': '2'}).attrs.get('style') == ''
    live_resp = app.post('/foo/live', params=resp.form.submit_fields())
    assert live_resp.json['result']['1']['visible']
    assert live_resp.json['result']['2']['visible']
    resp = resp.form.submit('submit')
    resp.form['f4'] = 'plop'
    resp = resp.form.submit('submit')
    assert 'Check values then click submit.' in resp.text
    assert 'name="f1"' in resp.text
    assert 'name="f2"' in resp.text
    assert 'name="f4"' in resp.text
    resp = resp.form.submit('submit')


def test_field_live_select_content(pub, http_requests):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'Foo'
    formdef.fields = [
        fields.StringField(id='1', label='Bar', size='40', required='required', varname='bar'),
        fields.StringField(id='2', label='Bar2', size='40', required='required', varname='bar2'),
        fields.ItemField(
            id='3',
            label='Foo',
            data_source={
                'type': 'json',
                'value': '{% if form_var_bar2 %}http://remote.example.net/json-list?plop={{form_var_bar2}}{% endif %}',
            },
        ),
    ]
    formdef.store()
    formdef.data_class().wipe()

    app = get_app(pub)
    resp = app.get('/foo/')
    assert 'f1' in resp.form.fields
    assert 'f2' in resp.form.fields
    assert resp.html.find('div', {'data-field-id': '2'}).attrs['data-live-source'] == 'true'
    assert resp.html.find('div', {'data-field-id': '3'}).find('select')
    resp.form['f1'] = 'hello'
    live_resp = app.post('/foo/live', params=resp.form.submit_fields())
    assert live_resp.json['result']['1']['visible']
    assert live_resp.json['result']['2']['visible']
    assert live_resp.json['result']['3']['visible']
    assert 'items' not in live_resp.json['result']['3']
    resp.form['f2'] = 'plop'
    live_resp = app.post('/foo/live', params=resp.form.submit_fields() + [('modified_field_id[]', '2')])
    assert live_resp.json['result']['1']['visible']
    assert live_resp.json['result']['2']['visible']
    assert live_resp.json['result']['3']['visible']
    assert 'items' in live_resp.json['result']['3']
    resp.form['f3'].options = []
    for item in live_resp.json['result']['3']['items']:
        # simulate javascript filling the <select>
        resp.form['f3'].options.append((item['id'], False, item['text']))
    resp.form['f3'] = 'a'
    resp = resp.form.submit('submit')
    assert 'Check values then click submit.' in resp.text
    assert 'name="f1"' in resp.text
    assert 'name="f2"' in resp.text
    assert 'name="f3"' in resp.text
    resp = resp.form.submit('submit')
    resp = resp.follow()
    formdata = formdef.data_class().select()[0]
    assert formdata.data['1'] == 'hello'
    assert formdata.data['2'] == 'plop'
    assert formdata.data['3'] == 'a'
    assert formdata.data['3_display'] == 'b'

    # create and use geojson datasource
    NamedDataSource.wipe()
    data_source = NamedDataSource(name='geofoobar')
    data_source.data_source = {
        'type': 'geojson',
        'value': 'http://remote.example.net/geojson?plop={{form_var_bar2}}',
    }
    data_source.id_property = 'id'
    data_source.label_template_property = '{{ text }}'
    data_source.cache_duration = '5'
    data_source.store()
    formdef.fields[2].data_source = {'type': 'geofoobar'}
    formdef.store()
    formdef.data_class().wipe()

    app = get_app(pub)
    resp = app.get('/foo/')
    assert 'f1' in resp.form.fields
    assert 'f2' in resp.form.fields
    assert resp.html.find('div', {'data-field-id': '2'}).attrs['data-live-source'] == 'true'
    assert resp.html.find('div', {'data-field-id': '3'}).find('select')
    resp.form['f1'] = 'hello'
    live_resp = app.post('/foo/live', params=resp.form.submit_fields())
    assert live_resp.json['result']['1']['visible']
    assert live_resp.json['result']['2']['visible']
    assert live_resp.json['result']['3']['visible']
    assert 'items' not in live_resp.json['result']['3']
    resp.form['f2'] = 'plop'
    live_resp = app.post('/foo/live', params=resp.form.submit_fields() + [('modified_field_id[]', '2')])
    assert live_resp.json['result']['1']['visible']
    assert live_resp.json['result']['2']['visible']
    assert live_resp.json['result']['3']['visible']
    assert 'items' in live_resp.json['result']['3']
    resp.form['f3'].options = []
    for item in live_resp.json['result']['3']['items']:
        # simulate javascript filling the <select>
        resp.form['f3'].options.append((item['id'], False, item['text']))
    resp.form['f3'] = '1'
    resp = resp.form.submit('submit')
    assert 'Check values then click submit.' in resp.text
    assert 'name="f1"' in resp.text
    assert 'name="f2"' in resp.text
    assert 'name="f3"' in resp.text
    resp = resp.form.submit('submit')
    resp = resp.follow()
    formdata = formdef.data_class().select()[0]
    assert formdata.data['1'] == 'hello'
    assert formdata.data['2'] == 'plop'
    assert formdata.data['3'] == '1'
    assert formdata.data['3_display'] == 'foo'


def test_field_live_jsonvalue_datasource(pub, http_requests):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'Foo'
    formdef.fields = [
        fields.StringField(id='1', label='Bar', size='40', required='optional', varname='bar'),
        fields.ItemField(
            id='2',
            label='Foo',
            data_source={
                'type': 'jsonvalue',
                'value': '{% if form_var_bar %}[{"id": "1", "text": "one"}, {"id": "2", "text": "two"}]'
                '{% else %}[{"id": "3", "text": "three"}, {"id": "4", "text": "four"}]{% endif %}',
            },
        ),
    ]
    formdef.store()
    formdef.data_class().wipe()

    app = get_app(pub)
    resp = app.get('/foo/')
    assert 'f1' in resp.form.fields
    assert 'f2' in resp.form.fields
    assert resp.html.find('div', {'data-field-id': '1'}).attrs['data-live-source'] == 'true'
    options = resp.html.find('div', {'data-field-id': '2'}).find('select').find_all('option')
    assert len(options) == 2
    assert options[0].text == 'three'
    assert options[1].text == 'four'

    resp.form['f1'] = 'hello'
    live_resp = app.post('/foo/live', params=resp.form.submit_fields())
    assert live_resp.json['result']['1']['visible']
    assert live_resp.json['result']['2']['visible']
    resp.form['f1'] = 'plop'
    live_resp = app.post('/foo/live', params=resp.form.submit_fields() + [('modified_field_id[]', '1')])
    assert live_resp.json['result']['1']['visible']
    assert live_resp.json['result']['2']['visible']
    assert 'items' in live_resp.json['result']['2']
    assert live_resp.json['result']['2']['items'][0] == {'id': '1', 'text': 'one'}
    assert live_resp.json['result']['2']['items'][1] == {'id': '2', 'text': 'two'}


def test_field_live_jsonvalue_datasource_chrono_with_condition(pub, http_requests):
    NamedDataSource.wipe()
    data_source = NamedDataSource(name='foobar')
    data_source.data_source = {
        'type': 'json',
        'value': 'http://chrono.example.net/api/agenda/virt/meetings/30/datetimes/',
    }
    data_source.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'Foo'
    formdef.fields = [
        fields.ItemField(
            id='1',
            label='rdv',
            display_mode='timetable',
            data_source={'type': 'foobar'},
            varname='rdv',
        ),
        fields.ItemField(
            id='2',
            label='Foo',
            data_source={
                'type': 'jsonvalue',
                'value': '{% if form_var_rdv %}{{ form_var_rdv_agendas|json_dumps}}{% endif %}',
            },
        ),
    ]
    formdef.store()
    formdef.data_class().wipe()

    app = get_app(pub)
    with responses.RequestsMock() as rsps:
        data = {'data': CHRONO_DATA}
        rsps.get('http://chrono.example.net/api/agenda/virt/meetings/30/datetimes/', json=data)
        resp = app.get('/foo/')
        assert 'f1' in resp.form.fields
        assert 'f2' in resp.form.fields

        resp.form['f1'] = '30:2023-10-04-1000'
        live_resp = app.post('/foo/live', params=resp.form.submit_fields() + [('modified_field_id[]', '1')])
        assert live_resp.json['result']['2']['visible']
        assert live_resp.json['result']['2']['items'] == CHRONO_DATA[0]['agendas']


def test_field_live_jsonvalue_datasource_chrono_with_varname_in_querystring(pub, http_requests):
    NamedDataSource.wipe()
    data_source = NamedDataSource(name='Room Booking - Slots of type form_var_resource_raw')
    data_source.slug = 'chrono_ds_room_booking'
    data_source.external = 'agenda'
    data_source.data_source = {
        'type': 'json',
        'value': 'http://chrono.example.net/api/agenda/free-range/datetimes/',
    }
    data_source.qs_data = {'resource': '{{ form_var_resource_raw|default:"" }}'}
    data_source.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'Book a room'
    formdef.fields = [
        fields.ItemField(id='1', label='Resource', varname='resource', items=['room-1', 'room-2']),
        fields.ItemField(
            id='2',
            label='Foo',
            display_mode='timetable',
            data_source={'type': 'chrono_ds_room_booking'},
        ),
    ]
    formdef.store()
    formdef.data_class().wipe()

    app = get_app(pub)
    with responses.RequestsMock() as rsps:
        data = {'data': CHRONO_DATA}
        rsps.get('http://chrono.example.net/api/agenda/free-range/datetimes/', json=data)
        resp = app.get('/book-a-room/')

        assert resp.pyquery('[data-widget-name="f1"]').attr['data-live-source'] == 'true'

        assert len(rsps.calls) == 1
        assert rsps.calls[0].request.params == {}

        rsps.reset()
        resp.form['f1'] = 'room-1'
        app.post('/book-a-room/live', params=resp.form.submit_fields())

        assert len(rsps.calls) == 1
        assert rsps.calls[0].request.params['resource'] == 'room-1'


def test_field_live_select_content_on_workflow_form(pub, http_requests):
    create_user(pub)
    wf = Workflow(name='wf-title')
    st1 = wf.add_status('Status1', 'st1')

    # form displayed into the workflow
    display_form = st1.add_action('form', id='_x')
    display_form.by = ['_submitter']
    display_form.varname = 'xxx'
    display_form.hide_submit_button = False
    display_form.formdef = WorkflowFormFieldsFormDef(item=display_form)
    display_form.formdef.fields = [
        fields.StringField(id='1', label='Bar', size='40', required='required', varname='bar'),
        fields.StringField(id='2', label='Bar2', size='40', required='required', varname='bar2'),
        fields.ItemField(
            id='3',
            label='Foo',
            required='optional',
            varname='foo',
            data_source={
                'type': 'json',
                'value': '{% if xxx_var_bar2 %}http://remote.example.net/json-list?plop={{xxx_var_bar2}}{% endif %}',
            },
        ),
    ]
    wf.store()

    # initial empty form
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = []
    formdef.confirmation = False
    formdef.workflow_id = wf.id
    formdef.store()
    formdef.data_class().wipe()

    app = get_app(pub)
    resp = login(app, username='foo', password='foo').get('/test/')
    assert resp.pyquery('title').text() == 'test - Filling'

    resp = resp.form.submit('submit').follow()
    assert 'The form has been recorded' in resp.text

    assert 'data-live-url' in resp.html.find('form').attrs
    assert f'fxxx_{display_form.id}_1' in resp.form.fields
    assert f'fxxx_{display_form.id}_2' in resp.form.fields
    assert (
        resp.html.find('div', {'data-field-id': f'xxx_{display_form.id}_2'}).attrs['data-live-source']
        == 'true'
    )
    assert resp.html.find('div', {'data-field-id': f'xxx_{display_form.id}_3'}).find('select')
    resp = resp.form.submit('submit')  # submit with error, to check <form> attributes
    assert 'data-live-url' in resp.html.find('form').attrs
    assert f'fxxx_{display_form.id}_1' in resp.form.fields
    assert f'fxxx_{display_form.id}_2' in resp.form.fields
    assert (
        resp.html.find('div', {'data-field-id': f'xxx_{display_form.id}_2'}).attrs['data-live-source']
        == 'true'
    )
    assert resp.html.find('div', {'data-field-id': f'xxx_{display_form.id}_3'}).find('select')
    resp.form[f'fxxx_{display_form.id}_1'] = 'hello'
    live_resp = app.post('/test/1/live', params=resp.form.submit_fields())
    assert live_resp.json['result'][f'xxx_{display_form.id}_1']['visible']
    assert live_resp.json['result'][f'xxx_{display_form.id}_2']['visible']
    assert live_resp.json['result'][f'xxx_{display_form.id}_3']['visible']
    assert 'items' not in live_resp.json['result'][f'xxx_{display_form.id}_3']
    resp.form[f'fxxx_{display_form.id}_2'] = 'plop'
    live_resp = app.post(
        '/test/1/live',
        params=resp.form.submit_fields() + [('modified_field_id[]', f'xxx_{display_form.id}_2')],
    )
    assert live_resp.json['result'][f'xxx_{display_form.id}_1']['visible']
    assert live_resp.json['result'][f'xxx_{display_form.id}_2']['visible']
    assert live_resp.json['result'][f'xxx_{display_form.id}_3']['visible']
    assert 'items' in live_resp.json['result'][f'xxx_{display_form.id}_3']
    assert len(live_resp.json['result'][f'xxx_{display_form.id}_3']['items']) > 0

    resp.form[f'fxxx_{display_form.id}_3'].options = []
    for item in live_resp.json['result'][f'xxx_{display_form.id}_3']['items']:
        # simulate javascript filling the <select>
        resp.form[f'fxxx_{display_form.id}_3'].options.append((item['id'], False, item['text']))
    resp.form[f'fxxx_{display_form.id}_3'] = 'a'

    resp = resp.form.submit('submit')
    assert 'invalid value selected' not in resp
    resp = resp.follow()
    formdata = formdef.data_class().select()[0]
    assert formdata.workflow_data['xxx_var_bar'] == 'hello'
    assert formdata.workflow_data['xxx_var_bar2'] == 'plop'
    assert formdata.workflow_data['xxx_var_foo_raw'] == 'a'
    assert formdata.workflow_data['xxx_var_foo'] == 'b'


def test_field_live_select_content_based_on_prefill(pub, http_requests):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'Foo'
    formdef.fields = [
        fields.StringField(
            id='1',
            label='Bar',
            size='40',
            required='required',
            varname='bar',
            prefill={'type': 'string', 'value': 'HELLO WORLD'},
        ),
        fields.ItemField(
            id='2',
            label='Foo',
            data_source={
                'type': 'json',
                'value': '{% if form_var_bar %}http://remote.example.net/json-list?plop={{form_var_bar}}{% endif %}',
            },
        ),
    ]
    formdef.store()
    formdef.data_class().wipe()

    app = get_app(pub)
    resp = app.get('/foo/')
    assert 'f1' in resp.form.fields
    assert 'f2' in resp.form.fields
    assert resp.html.find('div', {'data-field-id': '1'}).attrs['data-live-source'] == 'true'
    assert resp.html.find('div', {'data-field-id': '2'}).find('select')
    assert resp.html.find('option', {'value': 'a'})
    assert http_requests.get_last('url') == 'http://remote.example.net/json-list?plop=HELLO%20WORLD'

    # check with autocomplete and a remote source with id
    NamedDataSource.wipe()
    data_source = NamedDataSource(name='foobar')
    data_source.data_source = {
        'type': 'json',
        'value': 'http://remote.example.net/json?plop={{form_var_bar}}',
    }
    data_source.query_parameter = 'q'
    data_source.id_parameter = 'id'
    data_source.store()

    formdef.fields[1].display_mode = 'autocomplete'
    formdef.fields[1].data_source['type'] = 'foobar'
    formdef.store()
    app = get_app(pub)
    resp = app.get('/foo/')
    assert 'f1' in resp.form.fields
    assert 'f2' in resp.form.fields


def test_field_live_select_content_on_other_default_select_option(pub, http_requests):
    create_user(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'Foo'
    formdef.fields = [
        fields.ItemField(
            id='2',
            label='Foo',
            varname='bar2',
            data_source={'type': 'json', 'value': 'http://remote.example.net/json-list'},
        ),
        fields.ItemField(
            id='3',
            label='Foo',
            data_source={
                'type': 'json',
                'value': '{% if form_var_bar2 %}http://remote.example.net/json-list?plop={{form_var_bar2}}{% endif %}',
            },
        ),
    ]
    formdef.store()
    formdef.data_class().wipe()

    app = get_app(pub)
    resp = app.get('/foo/')
    assert 'f2' in resp.form.fields
    assert 'f3' in resp.form.fields
    assert resp.html.find('div', {'data-field-id': '2'}).attrs['data-live-source'] == 'true'
    assert resp.html.find('div', {'data-field-id': '3'}).find('select')
    # javascript will make an initial call with modified_field_id=init parameter,
    # simulate.
    live_resp = app.post('/foo/live', params=resp.form.submit_fields() + [('modified_field_id[]', 'init')])
    assert 'items' in live_resp.json['result']['3']
    resp.form['f3'].options = []
    for item in live_resp.json['result']['3']['items']:
        # simulate javascript filling the <select>
        resp.form['f3'].options.append((item['id'], False, item['text']))
    resp.form['f3'] = 'a'
    resp = resp.form.submit('submit')
    assert 'Check values then click submit.' in resp.text
    assert 'name="f2"' in resp.text
    assert 'name="f3"' in resp.text
    resp = resp.form.submit('submit')
    resp = resp.follow()
    formdata = formdef.data_class().select()[0]
    assert formdata.data['2'] == 'a'
    assert formdata.data['3'] == 'a'


def test_field_live_select_autocomplete_jsonvalue_prefill(pub, http_requests):
    FormDef.wipe()
    NamedDataSource.wipe()

    data_source = NamedDataSource(name='foobar')
    data_source.data_source = {
        'type': 'jsonvalue',
        'value': json.dumps([{'id': '1', 'text': 'foo'}, {'id': '2', 'text': 'bar'}]),
    }
    data_source.store()

    formdef = FormDef()
    formdef.name = 'Foo'
    formdef.fields = [
        fields.StringField(id='1', label='Text', varname='text'),
        fields.ItemField(
            id='2',
            label='foo',
            varname='foo',
            data_source={'type': 'foobar'},
            display_mode='autocomplete',
            prefill={'type': 'string', 'value': '{{ form_var_text|default:"" }}'},
        ),
    ]
    formdef.store()
    formdef.data_class().wipe()

    app = get_app(pub)
    resp = app.get('/foo/')
    assert resp.pyquery('[data-field-id="1"][data-live-source]')


@pytest.mark.skipif('JOB_NAME' in os.environ, reason='jenkins python segfault')
def test_field_live_select(pub, http_requests):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'Foo'
    formdef.fields = [
        fields.StringField(id='2', label='Bar2', size='40', required='required', varname='bar2'),
        fields.ItemField(
            id='3',
            label='Foo',
            data_source={
                'type': 'json',
                'value': '{% if form_var_bar2 %}http://remote.example.net/json-list-extra-with-disabled?plop={{form_var_bar2}}{% endif %}',
            },
            display_disabled_items=True,
        ),
    ]
    formdef.store()
    formdef.data_class().wipe()

    app = get_app(pub)
    resp = app.get('/foo/')
    assert resp.html.find('div', {'data-field-id': '2'}).attrs['data-live-source'] == 'true'
    assert resp.html.find('div', {'data-field-id': '3'}).find('select')
    resp.form['f2'] = 'plop'
    live_resp = app.post('/foo/live', params=resp.form.submit_fields() + [('modified_field_id[]', '2')])
    assert len(live_resp.json['result']['3']['items']) == 2
    assert live_resp.json['result']['3']['items'][1]['disabled'] is True

    formdef.fields[1].display_disabled_items = False
    formdef.store()
    resp = app.get('/foo/')
    resp.form['f2'] = 'plop'
    live_resp = app.post('/foo/live', params=resp.form.submit_fields() + [('modified_field_id[]', '2')])
    assert len(live_resp.json['result']['3']['items']) == 1


def test_field_live_select_autocomplete_card_prefill(pub, sql_queries):
    CardDef.wipe()
    FormDef.wipe()

    carddef = CardDef()
    carddef.name = 'foo'
    carddef.digest_templates = {'default': 'value: {{ form_var_foo }}'}
    carddef.fields = [fields.StringField(id='1', label='string', varname='foo')]
    carddef.store()
    carddef.data_class().wipe()

    for value in ('foo', 'bar', 'bar'):
        carddata = carddef.data_class()()
        carddata.data = {'1': value}
        carddata.just_created()
        carddata.store()

    formdef = FormDef()
    formdef.name = 'Foo'
    formdef.fields = [
        fields.StringField(id='1', label='Text', varname='text'),
        fields.ItemField(
            id='2',
            label='foo',
            varname='foo',
            data_source={'type': 'carddef:foo'},
            display_mode='autocomplete',
            prefill={'type': 'string', 'value': '{{ form_var_text }}'},
        ),
    ]
    formdef.store()
    formdef.data_class().wipe()

    app = get_app(pub)
    resp = app.get('/foo/')
    resp.form['f1'] = '2'
    # prefilled with id
    sql_queries_idx = len(sql_queries)
    live_resp = app.post(
        '/foo/live', params=resp.form.submit_fields() + [('modified_field_id[]', '2'), ('prefilled_2', 'on')]
    )
    assert live_resp.json['result']['2']['content'] == '2'
    assert live_resp.json['result']['2']['display_value'] == 'value: bar'
    global_card_queries = [
        x
        for x in sql_queries[sql_queries_idx:]
        if f'FROM carddata_{carddef.id}_foo' in x
        and 'AND id =' not in x
        and "AND digests->>'default' =" not in x
    ]
    assert not (global_card_queries)

    # prefilled with text
    resp.form['f1'] = 'value: foo'
    live_resp = app.post(
        '/foo/live', params=resp.form.submit_fields() + [('modified_field_id[]', '1'), ('prefilled_2', 'on')]
    )
    assert live_resp.json['result']['2']['content'] == '1'
    assert live_resp.json['result']['2']['display_value'] == 'value: foo'


def test_field_live_items_checkboxes(pub, http_requests):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'Foo'
    formdef.fields = [
        fields.StringField(id='2', label='Bar2', size='40', required='required', varname='bar2'),
        fields.ItemsField(
            id='3',
            label='Foo',
            data_source={
                'type': 'json',
                'value': '{% if form_var_bar2 %}http://remote.example.net/json-list?plop={{form_var_bar2}}{% endif %}',
            },
        ),
    ]
    formdef.store()
    formdef.data_class().wipe()

    app = get_app(pub)
    resp = app.get('/foo/')
    assert resp.html.find('div', {'data-field-id': '2'}).attrs['data-live-source'] == 'true'
    assert resp.html.find('div', {'data-field-id': '3'}).find('ul')
    assert not resp.html.find('div', {'data-field-id': '3'}).find('ul li')
    resp.form['f2'] = 'plop'
    live_resp = app.post('/foo/live', params=resp.form.submit_fields() + [('modified_field_id[]', '2')])
    assert len(live_resp.json['result']['3']['items']) == 1
    # simulate js, add relevant checkboxes
    for option in live_resp.json['result']['3']['items']:
        checkbox_name = '%s$element%s' % (
            resp.html.find('div', {'data-field-id': '3'}).attrs['data-widget-name'],
            option['id'],
        )
        resp.form.fields[checkbox_name] = Checkbox(
            form=resp.form, name=checkbox_name, tag='input', value='yes', pos=10
        )
        resp.form.field_order.append((checkbox_name, resp.form.fields[checkbox_name]))

    assert http_requests.count() == 1
    url = resp.pyquery('form[data-live-validation-url]').attr('data-live-validation-url')
    live_resp = app.post(url + '?field=f3', params=resp.form.submit_fields())
    assert live_resp.json == {'err': 1, 'msg': 'required field', 'errorType': 'valueMissing'}
    resp.form.fields[checkbox_name].checked = True
    live_resp = app.post(url + '?field=f3', params=resp.form.submit_fields())
    assert live_resp.json == {'err': 0}
    assert http_requests.count() == 1

    resp = resp.form.submit('submit')  # -> validation
    assert resp.pyquery('.CheckboxesWidget li label').text() == 'b'
    resp = resp.form.submit('submit')  # -> submitted
    assert formdef.data_class().select()[0].data['3'] == ['a']
    assert formdef.data_class().select()[0].data['3_display'] == 'b'


def test_field_live_items_content_based_on_prefill(pub):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'Foo'
    formdef.fields = [
        fields.StringField(
            id='1',
            label='Bar',
            size='40',
            required='required',
            varname='bar',
            prefill={'type': 'string', 'value': 'hello'},
        ),
        fields.ItemsField(
            id='2',
            label='Foo',
            data_source={
                'type': 'json',
                'value': '{% if form_var_bar %}http://example.net/{{form_var_bar}}{% endif %}',
            },
        ),
    ]
    formdef.store()
    formdef.data_class().wipe()

    app = get_app(pub)
    with responses.RequestsMock() as rsps:
        rsps.get(
            'http://example.net/hello',
            json={'data': [{'id': '123', 'text': 'hello'}, {'id': '234', 'text': 'foo'}]},
        )
        rsps.get('http://example.net/world', json={'data': [{'id': '345', 'text': 'world'}]})
        resp = app.get('/foo/')
        assert resp.pyquery('[data-field-id="1"][data-live-source="true"]')
        assert not resp.pyquery('.widget-with-error')
        assert [x.attrib['name'] for x in resp.pyquery('[data-field-id="2"] input[type="checkbox"]')] == [
            'f2$element123',
            'f2$element234',
        ]
        formdef.fields[0].prefill['value'] = 'world'
        formdef.store()
        resp = app.get('/foo/')
        assert [x.attrib['name'] for x in resp.pyquery('[data-field-id="2"] input[type="checkbox"]')] == [
            'f2$element345'
        ]
        assert not resp.pyquery('.widget-with-error')


def test_field_live_items_prefill_and_simple_options(pub):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'Foo'
    formdef.fields = [
        fields.StringField(
            id='1',
            label='Bar',
            size='40',
            required='required',
            varname='bar',
            prefill={'type': 'string', 'value': 'hello'},
        ),
        fields.ItemsField(
            id='2',
            label='Foo',
            condition={'type': 'django', 'value': 'form_var_bar == "hello"'},
            display_mode='autocomplete',
            items=['foo', 'bar', 'baz'],
        ),
    ]
    formdef.store()
    formdef.data_class().wipe()

    app = get_app(pub)
    resp = app.get('/foo/')
    assert [x[0] for x in resp.form['f2[]'].options] == ['foo', 'bar', 'baz']


def test_field_live_items_select_multiple(pub, http_requests):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'Foo'
    formdef.fields = [
        fields.StringField(id='2', label='Bar2', size='40', required='required', varname='bar2'),
        fields.ItemsField(
            display_mode='autocomplete',
            id='3',
            label='Foo',
            data_source={
                'type': 'json',
                'value': '{% if form_var_bar2 %}http://remote.example.net/json-list?plop={{form_var_bar2}}{% endif %}',
            },
        ),
    ]
    formdef.store()
    formdef.data_class().wipe()

    app = get_app(pub)
    resp = app.get('/foo/')
    assert resp.html.find('div', {'data-field-id': '2'}).attrs['data-live-source'] == 'true'
    assert resp.html.find('div', {'data-field-id': '3'}).find('select')
    assert not resp.html.find('div', {'data-field-id': '3'}).find('select option')
    resp.form['f2'] = 'plop'
    live_resp = app.post('/foo/live', params=resp.form.submit_fields() + [('modified_field_id[]', '2')])
    assert len(live_resp.json['result']['3']['items']) == 1
    # simulate js, add relevant options
    resp.form['f3[]'].options = [(x['id'], False, x['text']) for x in live_resp.json['result']['3']['items']]
    resp.form['f3[]'].select_multiple(['a'])
    resp = resp.form.submit('submit')  # -> validation
    assert resp.pyquery('select option[selected]').text() == 'b'
    resp = resp.form.submit('submit')  # -> submitted
    assert formdef.data_class().select()[0].data['3'] == ['a']
    assert formdef.data_class().select()[0].data['3_display'] == 'b'


def test_field_live_template_content(pub, http_requests):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'Foo'
    formdef.fields = [
        fields.StringField(id='2', label='Bar2', size='40', required='required', varname='bar2'),
        fields.ItemField(
            id='3',
            label='Foo',
            extra_css_class='template-whatever',
            data_source={
                'type': 'json',
                'value': '{% if form_var_bar2 %}http://remote.example.net/json-list-extra-with-disabled?plop={{form_var_bar2}}{% endif %}',
            },
            display_disabled_items=True,
        ),
    ]
    formdef.store()
    formdef.data_class().wipe()

    app = get_app(pub)
    resp = app.get('/foo/')
    assert resp.html.find('div', {'data-field-id': '2'}).attrs['data-live-source'] == 'true'
    assert resp.html.find('div', {'data-field-id': '3'}).find('select')
    resp.form['f2'] = 'plop'
    live_resp = app.post('/foo/live', params=resp.form.submit_fields() + [('modified_field_id[]', '2')])
    assert live_resp.json['result']['3']['items'][0]['foo'] == 'bar'
    assert len(live_resp.json['result']['3']['items']) == 2
    assert live_resp.json['result']['3']['items'][1]['disabled'] is True

    formdef.fields[1].display_disabled_items = False
    formdef.store()
    resp = app.get('/foo/')
    resp.form['f2'] = 'plop'
    live_resp = app.post('/foo/live', params=resp.form.submit_fields() + [('modified_field_id[]', '2')])
    assert len(live_resp.json['result']['3']['items']) == 1


def test_field_live_timetable_select(pub, http_requests):
    NamedDataSource.wipe()
    data_source = NamedDataSource(name='foobar')
    data_source.data_source = {
        'type': 'json',
        'value': 'http://remote.example.net/api/datetimes{% if form_var_bar2 %}?{% endif %}',
    }
    data_source.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'Foo'
    formdef.fields = [
        fields.StringField(id='2', label='Bar2', size='40', required='required', varname='bar2'),
        fields.ItemField(
            id='3',
            label='datetime',
            display_mode='timetable',
            data_source={'type': 'foobar'},
            display_disabled_items=True,
        ),
    ]
    formdef.store()
    formdef.data_class().wipe()

    app = get_app(pub)

    with responses.RequestsMock() as rsps:
        data = {
            'data': [
                {'id': '1', 'datetime': '2021-01-12 10:00:00', 'text': 'event 1', 'api': {}},
                {'id': '2', 'datetime': '2021-01-13 10:20:00', 'text': 'event 2', 'api': {}},
                {
                    'id': '3',
                    'datetime': '2021-01-14 10:40:00',
                    'text': 'event 3',
                    'api': {},
                    'disabled': True,
                },
            ]
        }
        rsps.get('http://remote.example.net/api/datetimes', json=data)

        resp = app.get('/foo/')
        assert resp.html.find('div', {'data-field-id': '2'}).attrs['data-live-source'] == 'true'
        assert resp.html.find('div', {'data-field-id': '3'}).find('select')
        resp.form['f2'] = 'plop'
        live_resp = app.post('/foo/live', params=resp.form.submit_fields() + [('modified_field_id[]', '2')])
        assert 'datetime' in live_resp.json['result']['3']['items'][0]
        assert 'api' not in live_resp.json['result']['3']['items'][0]
        assert len(live_resp.json['result']['3']['items']) == 3
        assert live_resp.json['result']['3']['items'][2]['disabled'] is True

        formdef.fields[1].display_disabled_items = False
        formdef.store()
        resp = app.get('/foo/')
        assert resp.html.find('div', {'data-field-id': '2'}).attrs['data-live-source'] == 'true'
        assert resp.html.find('div', {'data-field-id': '3'}).find('select')
        resp.form['f2'] = 'plop'
        live_resp = app.post('/foo/live', params=resp.form.submit_fields() + [('modified_field_id[]', '2')])
        assert 'datetime' in live_resp.json['result']['3']['items'][0]
        assert 'api' not in live_resp.json['result']['3']['items'][0]
        assert len(live_resp.json['result']['3']['items']) == 2


def test_field_live_comment_content(pub, http_requests):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'Foo'
    formdef.fields = [
        fields.StringField(id='1', label='Bar', size='40', required='required', varname='bar'),
        fields.StringField(id='2', label='Baz', size='40'),
        fields.CommentField(id='5', label='bla {{form_var_bar}} bla'),
        fields.StringField(id='6', label='Bar2', size='40', required='required', varname='bar2'),
        fields.CommentField(id='7', label='bla {{form_var_bar2}} bla'),
    ]
    formdef.store()
    formdef.data_class().wipe()

    app = get_app(pub)
    resp = app.get('/foo/')
    assert 'f1' in resp.form.fields
    assert resp.html.find('div', {'data-field-id': '1'}).attrs['data-live-source'] == 'true'
    resp.form['f1'] = 'hello'
    live_resp = app.post('/foo/live', params=resp.form.submit_fields())
    assert live_resp.json['result']['5']['content'] == '<p>bla hello bla</p>'
    resp.form['f1'] = 'toto'
    live_resp = app.post('/foo/live', params=resp.form.submit_fields() + [('modified_field_id[]', '1')])
    assert live_resp.json['result']['5']['content'] == '<p>bla toto bla</p>'
    live_resp = app.post('/foo/live', params=resp.form.submit_fields() + [('modified_field_id[]', '2')])
    assert live_resp.json['result']['5']['content'] == '<p>bla toto bla</p>'

    # check evaluation of later fields
    # <https://dev.entrouvert.org/issues/31922>
    resp = app.get('/foo/')
    resp.form['f1'] = 'hello'
    live_resp = app.post('/foo/live', params=resp.form.submit_fields())
    assert live_resp.json['result']['5']['content'] == '<p>bla hello bla</p>'
    resp.form['f6'] = 'hello'
    live_resp = app.post('/foo/live', params=resp.form.submit_fields())
    assert live_resp.json['result']['7']['content'] == '<p>bla hello bla</p>'


def test_field_live_comment_content_from_structured_item_data(pub, http_requests):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'Foo'
    formdef.fields = [
        fields.ItemField(
            id='1',
            label='Foo',
            varname='bar',
            data_source={'type': 'json', 'value': 'http://remote.example.net/json-list-extra'},
        ),
        fields.CommentField(id='7', label='bla {{form_var_bar_structured_foo}} bla'),
    ]
    formdef.store()
    formdef.data_class().wipe()

    app = get_app(pub)
    resp = app.get('/foo/')
    assert 'f1' in resp.form.fields
    assert resp.html.find('div', {'data-field-id': '1'}).attrs['data-live-source'] == 'true'
    resp.form['f1'] = 'a'
    live_resp = app.post('/foo/live', params=resp.form.submit_fields())
    assert live_resp.json['result']['7']['content'] == '<p>bla bar bla</p>'


def test_field_live_comment_content_from_block_subfield_data(pub):
    FormDef.wipe()
    BlockDef.wipe()

    block = BlockDef()
    block.name = 'foobar'
    block.fields = [fields.StringField(id='123', required='required', label='Test', varname='test')]
    block.store()

    formdef = FormDef()
    formdef.name = 'Foo'
    formdef.fields = [
        fields.BlockField(id='1', label='test', block_slug='foobar', varname='bar'),
        fields.CommentField(id='7', label='bla {{ form_var_bar_var_test }} bla'),
    ]
    formdef.store()
    formdef.data_class().wipe()

    app = get_app(pub)
    resp = app.get('/foo/')
    assert resp.pyquery('[data-live-source]').attr['data-field-id'] == '1'
    resp.form['f1$element0$f123'] = 'bar'
    live_resp = app.post('/foo/live', params=resp.form.submit_fields())
    assert live_resp.json['result']['7']['content'] == '<p>bla bar bla</p>'


def test_field_live_string_prefill(pub, http_requests):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'Foo'
    formdef.fields = [
        fields.StringField(id='1', label='Bar', size='40', required='required', varname='bar'),
        fields.StringField(
            id='2',
            label='Bar2',
            size='40',
            required='required',
            varname='bar2',
            prefill={'type': 'string', 'value': '{{form_var_bar|default:""}}'},
        ),
    ]
    formdef.store()
    formdef.data_class().wipe()

    app = get_app(pub)
    resp = app.get('/foo/')
    assert resp.html.find('div', {'data-field-id': '1'}).attrs['data-live-source'] == 'true'
    assert resp.pyquery('#var_bar2.widget-prefilled')  # second field is marked as prefilled
    assert resp.form['f2'].value == ''
    resp.form['f1'] = 'hello'
    live_resp = app.post(
        '/foo/live', params=resp.form.submit_fields() + [('modified_field_id[]', '1'), ('prefilled_2', 'on')]
    )
    assert live_resp.json['result']['2'] == {'visible': True, 'locked': False, 'content': 'hello'}

    resp.form['f2'] = 'other'  # manually changed -> widget-prefilled class will be removed
    resp.form['f1'] = 'xxx'
    live_resp = app.post('/foo/live', params=resp.form.submit_fields() + [('modified_field_id[]', '1')])
    assert live_resp.json['result']['2'] == {'visible': True}

    # check it's not possible to declare user change from frontoffice
    live_resp = app.post(
        '/foo/live', params=resp.form.submit_fields() + [('modified_field_id[]', 'user')], status=403
    )


def test_field_live_string_prefill_locked(pub, http_requests):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'Foo'
    formdef.fields = [
        fields.StringField(id='1', label='Bar', size='40', required='required', varname='bar'),
        fields.StringField(
            id='2',
            label='Bar2',
            size='40',
            required='required',
            varname='bar2',
            prefill={'type': 'string', 'value': '{{form_var_bar|default:""}}', 'locked': True},
        ),
    ]
    formdef.store()
    formdef.data_class().wipe()

    app = get_app(pub)
    resp = app.get('/foo/')
    resp.form['f1'] = 'hello'
    live_resp = app.post(
        '/foo/live', params=resp.form.submit_fields() + [('modified_field_id[]', '1'), ('prefilled_2', 'on')]
    )
    assert live_resp.json['result']['2'] == {'visible': True, 'locked': True, 'content': 'hello'}


def test_field_live_bool_prefill(pub, http_requests):
    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'foo'
    carddef.digest_templates = {'default': '{{ form_var_foo }}'}
    carddef.fields = [
        fields.StringField(id='1', label='string', varname='foo'),
        fields.BoolField(
            id='2',
            varname='bool',
        ),
    ]
    carddef.store()
    carddef.data_class().wipe()
    carddata1 = carddef.data_class()()
    carddata1.data = {
        '1': 'bar',
        '2': True,
    }
    carddata1.just_created()
    carddata1.store()
    carddata2 = carddef.data_class()()
    carddata2.data = {
        '1': 'baz',
        '2': False,
    }
    carddata2.just_created()
    carddata2.store()
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'Foo'
    formdef.fields = [
        fields.ItemField(id='1', label='foo', varname='foo', data_source={'type': 'carddef:foo'}),
        fields.BoolField(
            id='2',
            label='bool',
            varname='bool',
            prefill={'type': 'string', 'value': '{{ form_var_foo_live_var_bool }}'},
        ),
    ]
    formdef.store()
    formdef.data_class().wipe()

    app = get_app(pub)
    resp = app.get('/foo/')
    assert resp.html.find('div', {'data-field-id': '1'}).attrs['data-live-source'] == 'true'
    assert resp.pyquery('#var_bool.widget-prefilled')  # second field is marked as prefilled
    assert resp.form['f2'].value is None
    resp.form['f1'] = str(carddata1.id)
    live_resp = app.post(
        '/foo/live', params=resp.form.submit_fields() + [('modified_field_id[]', '1'), ('prefilled_2', 'on')]
    )
    assert live_resp.json['result']['2'] == {'visible': True, 'locked': False, 'content': True}

    resp.form['f2'] = False  # manually changed -> widget-prefilled class will be removed
    resp.form['f1'] = str(carddata2.id)
    live_resp = app.post('/foo/live', params=resp.form.submit_fields() + [('modified_field_id[]', '1')])
    assert live_resp.json['result']['2'] == {'visible': True}


def test_field_live_date_prefill(pub, http_requests):
    pub.cfg['language'] = {'language': 'fr'}
    pub.write_cfg()
    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'foo'
    carddef.digest_templates = {'default': '{{ form_var_foo }}'}
    carddef.fields = [
        fields.StringField(id='1', label='string', varname='foo'),
        fields.DateField(
            id='2',
            varname='date',
        ),
    ]
    carddef.store()
    carddef.data_class().wipe()
    carddata1 = carddef.data_class()()
    carddata1.data = {
        '1': 'bar',
        '2': datetime.date(2021, 10, 1).timetuple(),
    }
    carddata1.just_created()
    carddata1.store()
    carddata2 = carddef.data_class()()
    carddata2.data = {
        '1': 'baz',
        '2': datetime.date(2021, 10, 30).timetuple(),
    }
    carddata2.just_created()
    carddata2.store()
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'Foo'
    formdef.fields = [
        fields.ItemField(id='1', label='foo', varname='foo', data_source={'type': 'carddef:foo'}),
        fields.DateField(
            id='2',
            label='date',
            varname='date',
            prefill={'type': 'string', 'value': '{{ form_var_foo_live_var_date }}'},
        ),
    ]
    formdef.store()
    formdef.data_class().wipe()

    app = get_app(pub)
    resp = app.get('/foo/')
    assert resp.html.find('div', {'data-field-id': '1'}).attrs['data-live-source'] == 'true'
    assert resp.pyquery('#var_date.widget-prefilled')  # second field is marked as prefilled
    assert resp.form['f2'].value == ''
    resp.form['f1'] = str(carddata1.id)
    live_resp = app.post(
        '/foo/live', params=resp.form.submit_fields() + [('modified_field_id[]', '1'), ('prefilled_2', 'on')]
    )
    assert live_resp.json['result']['2'] == {
        'visible': True,
        'locked': False,
        'content': '2021-10-01',
        'text_content': '01/10/2021',
    }

    resp.form['f2'] = '2021-10-30'  # manually changed -> widget-prefilled class will be removed
    resp.form['f1'] = str(carddata2.id)
    live_resp = app.post('/foo/live', params=resp.form.submit_fields() + [('modified_field_id[]', '1')])
    assert live_resp.json['result']['2'] == {'visible': True}


def test_field_live_item_datasource_carddef_prefill(pub, http_requests):
    CardDef.wipe()

    carddef_related = CardDef()
    carddef_related.name = 'bar'
    carddef_related.digest_templates = {'default': '{{ form_var_bar }}'}
    carddef_related.fields = [
        fields.StringField(id='1', label='string', varname='bar'),
    ]
    carddef_related.store()
    carddef_related.data_class().wipe()
    for value in ['A', 'B', 'C']:
        carddata = carddef_related.data_class()()
        carddata.data = {
            '1': value,
        }
        carddata.just_created()
        carddata.store()

    carddef = CardDef()
    carddef.name = 'foo'
    carddef.digest_templates = {'default': '{{ form_var_foo }}'}
    carddef.fields = [
        fields.StringField(id='1', label='string', varname='foo'),
        fields.ItemField(
            id='2',
            varname='item',
            data_source={'type': 'carddef:bar'},
        ),
    ]
    carddef.store()
    carddef.data_class().wipe()
    carddata1 = carddef.data_class()()
    carddata1.data = {
        '1': 'bar',
        '2': '1',
        '2_display': 'A',
    }
    carddata1.just_created()
    carddata1.store()
    carddata2 = carddef.data_class()()
    carddata2.data = {
        '1': 'baz',
        '2': '2',
        '2_display': 'B',
    }
    carddata2.just_created()
    carddata2.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'Foo'
    formdef.fields = [
        fields.ItemField(id='1', label='foo', varname='foo', data_source={'type': 'carddef:foo'}),
        fields.ItemField(
            id='2',
            label='item',
            varname='item',
            prefill={'type': 'string', 'value': '{{ form_var_foo_live_var_item }}'},
            data_source={'type': 'carddef:bar'},
        ),
    ]
    formdef.store()
    formdef.data_class().wipe()

    app = get_app(pub)
    resp = app.get('/foo/')
    assert resp.html.find('div', {'data-field-id': '1'}).attrs['data-live-source'] == 'true'
    assert resp.pyquery('#var_item.widget-prefilled')  # second field is marked as prefilled
    assert resp.form['f2'].value == '1'
    resp.form['f1'] = str(carddata2.id)
    live_resp = app.post(
        '/foo/live', params=resp.form.submit_fields() + [('modified_field_id[]', '1'), ('prefilled_2', 'on')]
    )
    assert live_resp.json['result']['2'] == {'visible': True, 'locked': False, 'content': '2'}

    resp.form['f2'] = '3'  # manually changed -> widget-prefilled class will be removed
    resp.form['f1'] = str(carddata1.id)
    live_resp = app.post('/foo/live', params=resp.form.submit_fields() + [('modified_field_id[]', '1')])
    assert live_resp.json['result']['2'] == {'visible': True}


@responses.activate
def test_field_live_item_datasource_prefill_with_request(pub):
    data = {'data': [{'id': '1', 'text': 'un'}, {'id': '2', 'text': 'deux', 'x': 'bye'}]}
    responses.get('http://remote.example.net/plop', json=data)
    ds = {'type': 'json', 'value': 'http://remote.example.net/plop'}

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'Foo'
    formdef.fields = [
        fields.ItemField(
            id='1',
            label='item',
            data_source=ds,
            varname='foo',
            prefill={'type': 'string', 'value': '{{ request.GET.plop }}'},
        ),
        fields.ItemField(
            id='2',
            label='item',
            varname='item',
            prefill={'type': 'string', 'value': '{{ form_var_foo }}'},
            data_source=ds,
        ),
    ]
    formdef.store()

    app = get_app(pub)
    resp = app.get('/foo/?plop=2')
    assert resp.html.find('div', {'data-field-id': '1'}).attrs['data-live-source'] == 'true'
    assert resp.pyquery('#var_item.widget-prefilled')  # second field is marked as prefilled
    assert resp.form['f1'].value == '2'
    live_resp = app.post(
        '/foo/live',
        params=resp.form.submit_fields()
        + [('modified_field_id[]', 'init'), ('prefilled_1', 'on'), ('prefilled_2', 'on')],
    )
    assert live_resp.json['result'] == {'2': {'visible': True, 'locked': False, 'content': '2'}}


@responses.activate
def test_field_live_item_datasource_prefill_with_request_with_q(pub):
    NamedDataSource.wipe()
    data_source = NamedDataSource(name='foobar')
    data_source.data_source = {
        'type': 'json',
        'value': 'http://remote.example.net/json?plop={{form_var_bar}}',
    }
    data_source.query_parameter = 'q'
    data_source.id_parameter = 'id'
    data_source.store()

    data = {'data': [{'id': '1', 'text': 'un'}, {'id': '2', 'text': 'deux', 'x': 'bye'}]}
    responses.get('http://remote.example.net/json', json=data)
    ds = {'type': 'foobar'}

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'Foo'
    formdef.fields = [
        fields.ItemField(
            id='1',
            label='item',
            data_source=ds,
            varname='foo',
            prefill={'type': 'string', 'value': '{{ request.GET.plop }}'},
        ),
        fields.ItemField(
            id='2',
            label='item',
            varname='item',
            prefill={'type': 'string', 'value': '{{ form_var_foo }}'},
            data_source=ds,
        ),
    ]
    formdef.store()

    app = get_app(pub)
    resp = app.get('/foo/?plop=2')
    assert resp.html.find('div', {'data-field-id': '1'}).attrs['data-live-source'] == 'true'
    assert resp.pyquery('#var_item.widget-prefilled')  # second field is marked as prefilled
    assert resp.form['f1'].value == '2'
    live_resp = app.post(
        '/foo/live',
        params=resp.form.submit_fields()
        + [('modified_field_id[]', 'init'), ('prefilled_1', 'on'), ('prefilled_2', 'on')],
    )
    assert live_resp.json['result'] == {'2': {'visible': True, 'locked': False, 'content': '2'}}
    # check it has ?q=
    assert responses.calls[-1].request.url == 'http://remote.example.net/json?plop=&q=deux'

    responses.get('http://remote.example.net/json', body=misc.ConnectionError('...'))
    # no error
    app.post(
        '/foo/live',
        params=resp.form.submit_fields()
        + [('modified_field_id[]', 'init'), ('prefilled_1', 'on'), ('prefilled_2', 'on')],
        status=200,
    )


@responses.activate
def test_field_live_item_datasource_prefill_with_invalid_data(pub):
    NamedDataSource.wipe()
    data_source = NamedDataSource(name='foobar')
    data_source.data_source = {
        'type': 'json',
        'value': 'http://remote.example.net/json',
        'qs_data': {'plop': '{{ form_var_foo }}'},
    }
    data_source.query_parameter = 'q'
    data_source.id_parameter = 'id'
    data_source.store()

    data = {'data': [{'id': '1', 'text': 'un'}, {'id': '2', 'text': 'deux', 'x': 'bye'}]}
    responses.get('http://remote.example.net/json', json=data)
    ds = {'type': 'foobar'}

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'Foo'
    formdef.fields = [
        fields.ItemField(
            id='1',
            label='item',
            data_source=ds,
            varname='foo',
            prefill={'type': 'string', 'value': '2'},
        ),
        fields.ItemField(
            id='2',
            label='item',
            varname='item',
            prefill={'type': 'string', 'value': '{{ form_var_foo_structured_raw }}'},  # nope
            data_source=ds,
        ),
    ]
    formdef.store()

    app = get_app(pub)
    resp = app.get('/foo/')
    LoggedError.wipe()
    app.post(
        '/foo/live',
        params=resp.form.submit_fields()
        + [('modified_field_id[]', 'init'), ('prefilled_1', 'on'), ('prefilled_2', 'on')],
    )
    assert (
        LoggedError.select()[0].summary
        == "Invalid type for item lookup ({'id': '2', 'text': 'deux', 'x': 'bye'})"
    )


def test_field_live_block_string_prefill(pub, http_requests):
    FormDef.wipe()
    BlockDef.wipe()

    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.StringField(
            id='123',
            required='required',
            label='Test',
            prefill={'type': 'string', 'value': '{{form_var_foo|default:""}}'},
        ),
    ]
    block.store()

    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.StringField(id='1', label='test', varname='foo'),
        fields.BlockField(id='2', label='test', block_slug='foobar'),
    ]
    formdef.store()
    formdef.data_class().wipe()

    app = get_app(pub)
    resp = app.get(formdef.get_url())
    live_url = resp.html.find('form').attrs['data-live-url']
    assert resp.html.find('div', {'data-field-id': '1'}).attrs['data-live-source'] == 'true'
    assert resp.pyquery('[data-field-id="123"].widget-prefilled')  # block/string
    assert resp.form['f2$element0$f123'].value == ''
    resp.form['f1'] = 'hello'
    live_resp = app.post(
        live_url,
        params=resp.form.submit_fields() + [('modified_field_id[]', '1'), ('prefilled_2-123-element0', 'on')],
    )
    assert live_resp.json['result'] == {
        '1': {'visible': True},
        '2': {'visible': True},
        '2-123-0': {
            'block_id': '2',
            'block_row': 'element0',
            'row': 0,
            'content': 'hello',
            'field_id': '123',
            'visible': True,
            'locked': False,
        },
    }


def test_field_live_condition_unknown_page_id(pub):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'Foo'
    formdef.fields = [
        fields.PageField(id='0', label='2nd page'),
        fields.StringField(id='1', label='Bar', size='40', required='required', varname='bar'),
        fields.StringField(
            id='2',
            label='Foo',
            size='40',
            required='required',
            varname='foo',
            condition={'type': 'django', 'value': 'form_var_bar == "bye"'},
        ),
        fields.PageField(id='3', label='1st page'),
        fields.StringField(id='4', label='Baz', size='40', required='required', varname='baz'),
    ]
    formdef.store()

    app = get_app(pub)
    resp = app.get('/foo/')
    assert 'f1' in resp.form.fields
    assert 'f2' in resp.form.fields
    assert resp.html.find('div', {'data-field-id': '1'}).attrs['data-live-source'] == 'true'
    assert resp.html.find('div', {'data-field-id': '2'}).attrs.get('style') == 'display: none'
    resp.form['f1'] = 'hello'
    params = resp.form.submit_fields()
    params = [(key, value if key != 'page_id' else 'eiuiu') for key, value in params]
    app.post('/foo/live', params=params)


def test_field_live_locked_prefilled_field(pub, http_requests):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'Foo'
    formdef.fields = [
        fields.StringField(id='1', label='Bar', size='40', required='required', varname='bar'),
        fields.StringField(
            id='2',
            label='locked',
            size='40',
            required='required',
            prefill={'type': 'string', 'value': 'bla {{form_var_bar}} bla', 'locked': True},
        ),
    ]
    formdef.store()
    formdef.data_class().wipe()

    app = get_app(pub)
    resp = app.get('/foo/')
    assert 'f1' in resp.form.fields
    assert resp.html.find('div', {'data-field-id': '1'}).attrs['data-live-source'] == 'true'
    resp.form['f1'] = 'hello'
    live_resp = app.post('/foo/live', params=resp.form.submit_fields() + [('prefilled_2', 'on')])
    assert live_resp.json['result']['2']['content'] == 'bla hello bla'
    resp.form['f1'] = 'toto'
    live_resp = app.post(
        '/foo/live', params=resp.form.submit_fields() + [('modified_field_id[]', '1'), ('prefilled_2', 'on')]
    )
    assert live_resp.json['result']['2']['content'] == 'bla toto bla'


def test_field_live_locked_error_prefilled_field(pub, http_requests):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'Foo'
    formdef.fields = [
        fields.StringField(
            id='2',
            label='locked',
            size='40',
            required='required',
            prefill={'type': 'string', 'value': 'bla {% if foo %}{{ foo }}{% end %}', 'locked': True},
        ),
    ]
    formdef.store()
    formdef.data_class().wipe()

    app = get_app(pub)
    resp = app.get('/foo/')
    assert 'readonly' in resp.form['f2'].attrs
    assert not resp.form['f2'].attrs.get('value')


@pytest.mark.parametrize('field_type', ['item', 'string', 'email'])
def test_dynamic_item_field_from_custom_view_on_cards(pub, field_type):
    pub.role_class.wipe()
    pub.custom_view_class.wipe()

    user = create_user(pub)
    role = pub.role_class(name='xxx')
    role.store()
    user.roles = [role.id]
    user.is_admin = True
    user.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = []
    formdef.store()
    formdef.data_class().wipe()

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'items'
    carddef.digest_templates = {'default': '{{form_var_attr}}'}
    carddef.workflow_roles = {'_editor': user.roles[0]}
    carddef.fields = [
        fields.StringField(id='1', label='string', varname='attr'),
    ]
    if field_type == 'item':
        carddef.fields.append(
            fields.ItemField(id='0', label='item', varname='item', items=['foo', 'bar', 'baz'])
        )
    elif field_type == 'string':
        carddef.fields.append(fields.StringField(id='0', label='string', varname='item'))
    elif field_type == 'email':
        carddef.fields.append(fields.EmailField(id='0', label='email', varname='item'))
    carddef.store()
    carddef.data_class().wipe()
    baz_ids = set()
    for i, value in enumerate(['foo', 'bar', 'baz'] * 10):
        carddata = carddef.data_class()()
        carddata.data = {
            '0': value,
            '1': 'attr%s' % i,
        }
        if field_type == 'item':
            carddata.data['0_display'] = value
        carddata.just_created()
        carddata.store()
        if value == 'baz':
            baz_ids.add(str(carddata.id))

    # create custom view
    app = login(get_app(pub), username='foo', password='foo')

    resp = app.get('/backoffice/data/items/')
    assert resp.text.count('<tr') == 21  # thead + 20 items (max per page)
    resp.forms['listing-settings']['filter-0'].checked = True
    resp.forms['listing-settings']['filter-status'].checked = True
    resp = resp.forms['listing-settings'].submit().follow()

    resp.forms['listing-settings']['filter'].value = 'recorded'
    resp = resp.forms['listing-settings'].submit().follow()

    resp.forms['save-custom-view']['title'] = 'as data source'
    resp.forms['save-custom-view']['visibility'] = 'datasource'
    resp = resp.forms['save-custom-view'].submit().follow()

    if field_type == 'item':
        # some javascript to get a text input for filter value
        assert resp.forms['listing-settings']['filter-0-value'].attrs['data-allow-template']
        assert 'custom value' in [x[2] for x in resp.forms['listing-settings']['filter-0-value'].options]
        resp.forms['listing-settings']['filter-0-value'].force_value('{{ form_var_blah }}')
    else:
        resp.forms['listing-settings']['filter-0-value'] = '{{ form_var_blah }}'

    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.forms['listing-settings']['filter-0-value'].value == '{{ form_var_blah }}'
    assert resp.text.count('<tr') == 1  # thead only

    # save custom view with filter
    resp = resp.forms['save-custom-view'].submit().follow()

    custom_view = pub.custom_view_class.select()[0]

    # use custom view as source
    ds = {'type': 'carddef:%s:%s' % (carddef.url_name, custom_view.slug)}
    formdef.fields = [
        fields.PageField(id='2', label='1st page'),
        fields.ItemField(id='0', label='item', varname='blah', items=['foo', 'bar', 'baz']),
        fields.ItemField(id='3', label='item', varname='blah2', items=['foo', 'bar', 'baz']),
        fields.ItemField(id='1', label='string', data_source=ds, display_disabled_items=True),
    ]
    formdef.store()

    def test(app):
        formdef.fields[3].display_mode = 'list'
        formdef.store()

        resp = get_app(pub).get('/test/')
        assert resp.form['f1'].options == [('', False, '---')]
        resp.form['f0'] = 'baz'
        resp.form['f3'] = 'foo'
        live_resp = app.post('/test/live', params=resp.form.submit_fields() + [('modified_field_id[]', '0')])
        assert len(live_resp.json['result']['1']['items']) == 10
        assert {str(x['id']) for x in live_resp.json['result']['1']['items']} == baz_ids

        resp.form['f1'].options = []
        for item in live_resp.json['result']['1']['items']:
            # simulate javascript filling the <select>
            resp.form['f1'].options.append((str(item['id']), False, item['text']))

        resp.form['f1'] = resp.form['f1'].options[0][0]
        resp = resp.form.submit('submit')  # -> validation page
        assert 'Technical error' not in resp.text
        resp = resp.form.submit('submit')  # -> submit
        assert formdef.data_class().select()[0].data['1'] in baz_ids
        assert formdef.data_class().select()[0].data['1_structured']['item'] == 'baz'

        # same in autocomplete mode
        formdef.fields[3].display_mode = 'autocomplete'
        formdef.store()
        app = get_app(pub)
        resp = app.get('/test/')
        # simulate select2 mode, with qommon.forms.js adding an extra hidden widget
        resp.form.fields['f1_display'] = Hidden(form=resp.form, tag='input', name='f1_display', pos=10)
        select2_url = resp.pyquery('select:last').attr['data-select2-url']
        resp_json = app.get(select2_url + '?q=')
        assert len(resp_json.json['data']) == 0
        resp.form['f0'] = 'baz'
        resp.form['f3'] = 'foo'

        live_resp = app.post('/test/live', params=resp.form.submit_fields() + [('modified_field_id[]', '0')])
        new_select2_url = live_resp.json['result']['1']['source_url']
        resp_json = app.get(new_select2_url + '?q=')
        assert len(resp_json.json['data']) == 10
        assert {str(x['id']) for x in resp_json.json['data']} == baz_ids

        resp.form['f1'].force_value(str(resp_json.json['data'][0]['id']))
        resp.form.fields['f1_display'].force_value(resp_json.json['data'][0]['text'])

        resp = resp.form.submit('submit')  # -> validation page
        resp = resp.form.submit('submit')  # -> submit
        assert formdef.data_class().select()[0].data['1'] in baz_ids
        assert formdef.data_class().select()[0].data['1_structured']['item'] == 'baz'

    test(app)

    # operator with multi values - IN
    resp = app.get('/backoffice/data/items/%s/' % custom_view.slug)
    resp.forms['listing-settings']['filter-0-operator'] = 'in'
    resp = resp.forms['listing-settings'].submit().follow()
    if field_type == 'item':
        # some javascript to get a text input for filter value
        assert resp.forms['listing-settings']['filter-0-value'].attrs['data-allow-template']
        assert 'custom value' in [x[2] for x in resp.forms['listing-settings']['filter-0-value'].options]
        resp.forms['listing-settings']['filter-0-value'].force_value(
            '{{ form_var_blah }}|{{ form_var_blah }}'
        )
    else:
        resp.forms['listing-settings']['filter-0-value'] = '{{ form_var_blah }}|{{ form_var_blah }}'
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.forms['listing-settings']['filter-0-value'].value == '{{ form_var_blah }}|{{ form_var_blah }}'
    assert resp.text.count('<tr') == 1  # thead only

    # save custom view with filter
    resp = resp.forms['save-custom-view'].submit().follow()

    test(app)

    if field_type != 'email':
        # operator with multi values - BETWEEN
        resp = app.get('/backoffice/data/items/%s/' % custom_view.slug)
        resp.forms['listing-settings']['filter-0-operator'] = 'between'
        resp = resp.forms['listing-settings'].submit().follow()
        if field_type == 'item':
            # some javascript to get a text input for filter value
            assert resp.forms['listing-settings']['filter-0-value'].attrs['data-allow-template']
            assert 'custom value' in [x[2] for x in resp.forms['listing-settings']['filter-0-value'].options]
            resp.forms['listing-settings']['filter-0-value'].force_value(
                '{{ form_var_blah }}|{{ form_var_blah2 }}'
            )
        else:
            resp.forms['listing-settings']['filter-0-value'] = '{{ form_var_blah }}|{{ form_var_blah2 }}'
        resp = resp.forms['listing-settings'].submit().follow()
        assert (
            resp.forms['listing-settings']['filter-0-value'].value
            == '{{ form_var_blah }}|{{ form_var_blah2 }}'
        )
        assert resp.text.count('<tr') == 1  # thead only

        # save custom view with filter
        resp = resp.forms['save-custom-view'].submit().follow()

        test(app)

    # delete custom view
    LoggedError.wipe()
    custom_view.remove_self()
    resp = get_app(pub).get('/test/')
    assert resp.form['f1'].options == []
    assert LoggedError.count() == 1
    logged_error = LoggedError.select()[0]
    assert logged_error.formdef_id == str(formdef.id)
    assert logged_error.summary == 'Data source: Unknown custom view "as-data-source" for CardDef "items"'


def test_dynamic_date_field_from_custom_view_on_cards(pub):
    pub.role_class.wipe()
    pub.custom_view_class.wipe()

    user = create_user(pub)
    role = pub.role_class(name='xxx')
    role.store()
    user.roles = [role.id]
    user.is_admin = True
    user.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = []
    formdef.store()
    formdef.data_class().wipe()

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'items'
    carddef.digest_templates = {'default': '{{form_var_attr}}'}
    carddef.workflow_roles = {'_editor': user.roles[0]}
    carddef.fields = [
        fields.StringField(id='1', label='string', varname='attr'),
        fields.DateField(id='2', label='date'),
    ]
    carddef.store()
    carddef.data_class().wipe()
    for i, value in enumerate(['foo', 'bar', 'baz']):
        carddata = carddef.data_class()()
        carddata.data = {
            '1': value,
            '2': datetime.date(2024, 1, 1 + i).timetuple(),
        }
        carddata.just_created()
        carddata.store()

    # create custom view
    app = login(get_app(pub), username='foo', password='foo')

    resp = app.get('/backoffice/data/items/')
    resp.forms['listing-settings']['filter-2'].checked = True
    resp.forms['listing-settings']['filter-status'].checked = True
    resp = resp.forms['listing-settings'].submit().follow()

    resp.forms['listing-settings']['filter'].value = 'recorded'
    resp = resp.forms['listing-settings'].submit().follow()

    resp.forms['save-custom-view']['title'] = 'as data source'
    resp.forms['save-custom-view']['visibility'] = 'datasource'
    resp = resp.forms['save-custom-view'].submit().follow()

    # make sure <input type=date> is not used, so a template can be entered
    assert resp.pyquery('[name="filter-2-value"]')[0].attrib['type'] == 'text'
    resp.forms['listing-settings']['filter-2-value'] = '{{ form_var_blah }}'
    resp.forms['listing-settings']['filter-2-operator'].value = 'gte'

    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.forms['listing-settings']['filter-2-value'].value == '{{ form_var_blah }}'
    assert resp.text.count('<tr') == 1  # thead only

    # save custom view with filter
    resp = resp.forms['save-custom-view'].submit().follow()

    custom_view = pub.custom_view_class.select()[0]

    # use custom view as source
    ds = {'type': 'carddef:%s:%s' % (carddef.url_name, custom_view.slug)}
    formdef.fields = [
        fields.PageField(id='2', label='1st page'),
        fields.ItemField(
            id='0', label='item', varname='blah', items=['2023-01-02', '2024-01-02', '2025-01-02']
        ),
        fields.ItemField(id='1', label='string', data_source=ds, display_disabled_items=True),
    ]
    formdef.store()

    resp = get_app(pub).get('/test/')
    assert resp.form['f1'].options == [('', False, '---')]
    resp.form['f0'] = '2024-01-02'
    live_resp = app.post('/test/live', params=resp.form.submit_fields() + [('modified_field_id[]', '0')])
    assert live_resp.json['result']['1']['items'] == [
        {'attr': 'bar', 'id': 2, 'text': 'bar'},
        {'attr': 'baz', 'id': 3, 'text': 'baz'},
    ]

    resp.form['f0'] = '2023-01-02'
    live_resp = app.post('/test/live', params=resp.form.submit_fields() + [('modified_field_id[]', '0')])
    assert len(live_resp.json['result']['1']['items']) == 3

    resp.form['f0'] = '2025-01-02'
    live_resp = app.post('/test/live', params=resp.form.submit_fields() + [('modified_field_id[]', '0')])
    assert len(live_resp.json['result']['1']['items']) == 0


def test_dynamic_item_fields_from_custom_view_on_cards(pub):
    pub.role_class.wipe()
    pub.custom_view_class.wipe()

    user = create_user(pub)
    role = pub.role_class(name='xxx')
    role.store()
    user.roles = [role.id]
    user.is_admin = True
    user.store()

    CardDef.wipe()
    carddef1 = CardDef()
    carddef1.name = 'Card'
    carddef1.digest_templates = {'default': '{{form_var_foo}}'}
    carddef1.workflow_roles = {'_editor': user.roles[0]}
    carddef1.fields = [
        fields.StringField(id='1', label='string', varname='foo'),
    ]
    carddef1.store()
    carddef1.data_class().wipe()

    carddef2 = CardDef()
    carddef2.name = 'Subcard'
    carddef2.digest_templates = {'default': '{{form_var_bar}}'}
    carddef2.workflow_roles = {'_editor': user.roles[0]}
    carddef2.fields = [
        fields.StringField(id='1', label='string', varname='bar'),
        fields.ItemField(
            id='2', label='Card', varname='card', data_source={'type': 'carddef:%s' % carddef1.url_name}
        ),
        fields.ItemField(
            id='3',
            label='Card bis',
            varname='card_bis',
            data_source={'type': 'carddef:%s' % carddef1.url_name},
        ),
    ]
    carddef2.store()
    carddef2.data_class().wipe()

    carddata11 = carddef1.data_class()()
    carddata11.data = {
        '1': 'Foo 1',
    }
    carddata11.just_created()
    carddata11.store()
    carddata12 = carddef1.data_class()()
    carddata12.data = {
        '1': 'Foo 2',
    }
    carddata12.just_created()
    carddata12.store()

    carddata21 = carddef2.data_class()()
    carddata21.data = {
        '1': 'Bar 1',
        '2': str(carddata11.id),
        '2_display': 'Foo 1',
        '3': str(carddata11.id),
        '3_display': 'Foo 1',
    }
    carddata21.just_created()
    carddata21.store()
    carddata22 = carddef2.data_class()()
    carddata22.data = {
        '1': 'Bar 1',
        '2': str(carddata12.id),
        '2_display': 'Foo 2',
        '3': str(carddata11.id),
        '3_display': 'Foo 1',
    }
    carddata22.just_created()
    carddata22.store()

    custom_view = pub.custom_view_class()
    custom_view.title = 'view'
    custom_view.formdef = carddef2
    custom_view.columns = {'list': [{'id': 'id'}]}
    custom_view.filters = {'filter-2': 'on', 'filter-2-value': '{{ form_var_card }}'}
    custom_view.visibility = 'datasource'
    custom_view.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [
        fields.ItemField(
            id='1', label='Card', varname='card', data_source={'type': 'carddef:%s' % carddef1.url_name}
        ),
        fields.ItemField(
            id='2',
            label='Subcard',
            varname='subcard',
            data_source={'type': 'carddef:%s:%s' % (carddef2.url_name, custom_view.slug)},
        ),
    ]
    formdef.store()
    formdef.data_class().wipe()

    resp = get_app(pub).get('/test/', status=200)
    # just check options
    assert resp.form['f1'].options == [('1', False, 'Foo 1'), ('2', False, 'Foo 2')]
    assert resp.form['f2'].options == [('', False, '---')]
    assert LoggedError.count() == 0
    assert '<li class="warning">Invalid value' not in resp

    # add a filter on third field
    # EQ operator
    custom_view.filters = {
        'filter-2': 'on',
        'filter-2-value': '{{ form_var_card }}',
        'filter-3': 'on',
        'filter-3-value': str(carddata11.id),
        'filter-3-operator': 'eq',
    }
    custom_view.store()
    resp = get_app(pub).get('/test/', status=200)
    # just check options
    assert resp.form['f1'].options == [('1', False, 'Foo 1'), ('2', False, 'Foo 2')]
    assert resp.form['f2'].options == [('', False, '---')]
    assert LoggedError.count() == 0
    assert '<li class="warning">Invalid value' not in resp

    # IN operator
    custom_view.filters.update(
        {
            'filter-3-operator': 'in',
        }
    )
    custom_view.store()
    resp = get_app(pub).get('/test/', status=200)

    # BETWEEN operator
    custom_view.filters.update(
        {
            'filter-3-operator': 'in',
        }
    )
    custom_view.store()
    resp = get_app(pub).get('/test/', status=200)
    custom_view.filters.update(
        {
            'filter-3-operator': 'in',
            'filter-3-value': '%s|%s' % (carddata11.id, carddata12.id),
        }
    )
    custom_view.store()
    resp = get_app(pub).get('/test/', status=200)


def test_dynamic_items_field_from_custom_view_on_cards(pub):
    pub.role_class.wipe()
    pub.custom_view_class.wipe()
    TransientData.wipe()

    user = create_user(pub)
    role = pub.role_class(name='xxx')
    role.store()
    user.roles = [role.id]
    user.is_admin = True
    user.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = []
    formdef.store()
    formdef.data_class().wipe()

    items = ['foo', 'bar', 'baz', 'buz']
    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'items'
    carddef.digest_templates = {'default': '{{form_var_attr}} - {{form_var_items}}'}
    carddef.workflow_roles = {'_editor': user.roles[0]}
    carddef.fields = [
        fields.ItemsField(id='0', label='items', varname='items', items=items),
        fields.StringField(id='1', label='string', varname='attr'),
        fields.FileField(id='2', label='Image', varname='image'),
    ]
    carddef.store()
    carddef.data_class().wipe()

    with open(os.path.join(os.path.dirname(__file__), '..', 'image-with-gps-data.jpeg'), 'rb') as fd:
        image_content = fd.read()

    foo_bar_ids = set()
    for i, (v1, v2) in enumerate(itertools.product(items, items)):
        if v1 == v2:
            continue
        carddata = carddef.data_class()()
        upload = PicklableUpload('test-%s.jpg' % i, content_type='image/jpeg')
        upload.receive([image_content])
        carddata.data = {'0': [v1, v2], '0_display': '%s,%s' % (v1, v2), '1': 'attr%s' % i, '2': upload}
        carddata.just_created()
        carddata.store()
        if 'foo' in {v1, v2}:
            foo_bar_ids.add(str(carddata.id))

    # create custom view
    app = login(get_app(pub), username='foo', password='foo')

    resp = app.get('/backoffice/data/items/?order_by=id')
    assert resp.text.count('<tr') == 13  # thead + 12 items
    resp.forms['listing-settings']['filter-0'].checked = True
    resp.forms['listing-settings']['filter-status'].checked = True
    resp = resp.forms['listing-settings'].submit().follow()

    resp.forms['listing-settings']['filter'].value = 'recorded'
    resp = resp.forms['listing-settings'].submit().follow()

    resp.forms['save-custom-view']['title'] = 'as data source'
    resp.forms['save-custom-view']['visibility'] = 'datasource'
    resp = resp.forms['save-custom-view'].submit().follow()

    assert resp.forms['listing-settings']['filter-0-value'].attrs['data-allow-template']
    assert 'custom value' in [x[2] for x in resp.forms['listing-settings']['filter-0-value'].options]
    resp.forms['listing-settings']['filter-0-value'].force_value('{{ form_var_blah }}')

    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.forms['listing-settings']['filter-0-value'].value == '{{ form_var_blah }}'
    assert resp.text.count('<tr') == 1  # thead only

    # save custom view with filter
    resp = resp.forms['save-custom-view'].submit().follow()

    custom_view = pub.custom_view_class.select()[0]

    # use custom view as source
    ds = {'type': 'carddef:%s:%s' % (carddef.url_name, custom_view.slug)}
    formdef.fields = [
        fields.PageField(id='2', label='1st page'),
        fields.ItemsField(id='0', label='items', varname='blah', items=items),
        fields.ItemField(id='1', label='string', data_source=ds, display_disabled_items=True),
    ]
    formdef.store()

    resp = get_app(pub).get('/test/')
    assert resp.form['f1'].options == [('', False, '---')]
    resp.form['f0$elementfoo'] = 'foo'
    live_resp = app.post('/test/live', params=resp.form.submit_fields() + [('modified_field_id[]', '0')])
    assert len(live_resp.json['result']['1']['items']) == 6
    assert {str(x['id']) for x in live_resp.json['result']['1']['items']} == foo_bar_ids

    def _get_token_id(item_id):
        for token in TransientData.select():
            if (
                token.data is not None
                and token.data.get('carddef_slug') == 'items'
                and token.data.get('data_id') == item_id
            ):
                return token.id
        assert False

    for item in live_resp.json['result']['1']['items']:
        assert item['image_url'] == '/api/card-file-by-token/%s' % _get_token_id(item['id'])

    resp.form['f1'].options = []
    for item in live_resp.json['result']['1']['items']:
        # simulate javascript filling the <select>
        resp.form['f1'].options.append((str(item['id']), False, item['text']))

    resp.form['f1'] = resp.form['f1'].options[0][0]
    resp = resp.form.submit('submit')  # -> validation page
    assert 'Technical error' not in resp.text
    resp = resp.form.submit('submit')  # -> submit
    assert formdef.data_class().select()[0].data['1'] in foo_bar_ids
    assert formdef.data_class().select()[0].data['1_structured']['text'] == 'attr1 - foo,bar'

    # same in autocomplete mode
    formdef.fields[2].display_mode = 'autocomplete'
    formdef.store()
    app = get_app(pub)
    resp = app.get('/test/')
    # simulate select2 mode, with qommon.forms.js adding an extra hidden widget
    resp.form.fields['f1_display'] = Hidden(form=resp.form, tag='input', name='f1_display', pos=10)
    select2_url = resp.pyquery('select:last').attr['data-select2-url']
    resp_json = app.get(select2_url + '?q=')
    assert len(resp_json.json['data']) == 0
    resp.form['f0$elementfoo'] = 'foo'

    live_resp = app.post('/test/live', params=resp.form.submit_fields() + [('modified_field_id[]', '0')])
    new_select2_url = live_resp.json['result']['1']['source_url']
    resp_json = app.get(new_select2_url + '?q=')
    assert len(resp_json.json['data']) == 6
    assert {str(x['id']) for x in resp_json.json['data']} == foo_bar_ids

    resp.form['f1'].force_value(str(resp_json.json['data'][0]['id']))
    resp.form.fields['f1_display'].force_value(resp_json.json['data'][0]['text'])

    resp = resp.form.submit('submit')  # -> validation page
    resp = resp.form.submit('submit')  # -> submit
    assert formdef.data_class().select()[0].data['1'] in foo_bar_ids
    assert formdef.data_class().select()[0].data['1_structured']['text'] == 'attr1 - foo,bar'

    # delete custom view
    LoggedError.wipe()
    custom_view.remove_self()
    resp = get_app(pub).get('/test/')
    assert resp.form['f1'].options == []
    assert LoggedError.count() == 1
    logged_error = LoggedError.select()[0]
    assert logged_error.formdef_id == str(formdef.id)
    assert logged_error.summary == 'Data source: Unknown custom view "as-data-source" for CardDef "items"'


def test_dynamic_internal_id_from_custom_view_on_cards(pub):
    pub.role_class.wipe()
    pub.custom_view_class.wipe()

    user = create_user(pub)
    role = pub.role_class(name='xxx')
    role.store()
    user.roles = [role.id]
    user.is_admin = True
    user.store()

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'items'
    carddef.digest_templates = {'default': '{{form_var_attr}}'}
    carddef.workflow_roles = {'_editor': user.roles[0]}
    carddef.fields = [
        fields.StringField(id='1', label='string', varname='attr'),
        fields.BoolField(
            id='2',
            varname='bool',
        ),
        fields.StringField(id='3', label='string', varname='attr2'),
    ]
    carddef.store()
    carddef.data_class().wipe()
    for i in range(10):
        carddata = carddef.data_class()()
        carddata.data = {
            '1': 'attr%s' % i,
            '2': bool(i % 2),
            '3': str(i + 1),
        }
        carddata.just_created()
        carddata.store()

    custom_view = pub.custom_view_class()
    custom_view.title = 'as datasource'
    custom_view.formdef = carddef
    custom_view.columns = {'list': [{'id': 'id'}]}
    custom_view.filters = {
        'filter-internal-id': 'on',
        'filter-internal-id-value': '{{ form_var_num }}',
        'filter-internal-id-operator': 'eq',
    }
    custom_view.visibility = 'datasource'
    custom_view.store()

    ds = {'type': 'carddef:%s:%s' % (carddef.url_name, custom_view.slug)}
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [
        fields.StringField(id='0', label='num', varname='num'),
        fields.ItemField(id='1', label='item', data_source=ds, display_disabled_items=True),
    ]
    formdef.store()
    formdef.data_class().wipe()

    app = login(get_app(pub), username='foo', password='foo')

    resp = app.get('/backoffice/data/items/as-datasource/')
    assert resp.text.count('<tr') == 1  # thead only

    resp = get_app(pub).get('/test/')
    assert resp.form['f1'].options == [('', False, '---')]
    resp.form['f0'] = '3'
    live_resp = app.post('/test/live', params=resp.form.submit_fields() + [('modified_field_id[]', '0')])
    assert len(live_resp.json['result']['1']['items']) == 1
    assert {x['id'] for x in live_resp.json['result']['1']['items']} == {3}

    custom_view.filters['filter-internal-id-operator'] = 'ne'
    custom_view.store()
    live_resp = app.post('/test/live', params=resp.form.submit_fields() + [('modified_field_id[]', '0')])
    assert len(live_resp.json['result']['1']['items']) == 9
    assert {x['id'] for x in live_resp.json['result']['1']['items']} == {1, 2, 4, 5, 6, 7, 8, 9, 10}

    custom_view.filters['filter-internal-id-operator'] = 'lte'
    custom_view.store()
    live_resp = app.post('/test/live', params=resp.form.submit_fields() + [('modified_field_id[]', '0')])
    assert len(live_resp.json['result']['1']['items']) == 3
    assert {x['id'] for x in live_resp.json['result']['1']['items']} == {1, 2, 3}

    formdef.fields[0] = fields.ComputedField(
        id='0',
        label='computed',
        varname='num',
        value_template='{{ cards|objects:"items"|filter_by:"bool"|filter_value:True|getlist:"form_internal_id"|list }}',
    )
    formdef.store()

    custom_view.filters['filter-internal-id-operator'] = 'eq'
    custom_view.store()
    resp = get_app(pub).get('/test/')
    assert {int(x[0]) for x in resp.form['f1'].options} == {2, 4, 6, 8, 10}

    custom_view.filters['filter-internal-id-operator'] = 'ne'
    custom_view.store()
    resp = get_app(pub).get('/test/')
    assert {int(x[0]) for x in resp.form['f1'].options} == {1, 3, 5, 7, 9}

    for operator in ['lt', 'lte', 'gt', 'gte']:
        LoggedError.wipe()
        # list of values not allowed with theese operators
        custom_view.filters['filter-internal-id-operator'] = operator
        custom_view.store()
        resp = get_app(pub).get('/test/')
        assert resp.form['f1'].options == [('', False, '---')]
        assert LoggedError.count() == 1
        logged_error = LoggedError.select()[0]
        assert logged_error.formdef_id == str(formdef.id)
        assert (
            logged_error.summary
            == 'Invalid value "[2, 4, 6, 8, 10]" for custom view "as-datasource", CardDef "items", field "internal-id", operator "%s".'
            % operator
        )

    # not integers
    formdef.fields[0].value_template = (
        '{{ cards|objects:"items"|filter_by:"bool"|filter_value:True|getlist:"attr"|list }}'
    )
    formdef.store()
    custom_view.filters['filter-internal-id-operator'] = 'eq'
    custom_view.store()
    LoggedError.wipe()
    resp = get_app(pub).get('/test/')
    assert resp.form['f1'].options == [('', False, '---')]
    assert LoggedError.count() == 1
    logged_error = LoggedError.select()[0]
    assert logged_error.formdef_id == str(formdef.id)
    assert (
        logged_error.summary == 'Invalid value "[\'attr1\', \'attr3\', \'attr5\', \'attr7\', \'attr9\']" '
        'for custom view "as-datasource", CardDef "items", field "internal-id", operator "eq".'
    )
    formdef.fields[0].value_template = (
        '{{ cards|objects:"items"|filter_by:"bool"|filter_value:True|getlist:"unknown"|list }}'
    )
    formdef.store()
    custom_view.filters['filter-internal-id-operator'] = 'eq'
    custom_view.store()
    LoggedError.wipe()
    resp = get_app(pub).get('/test/')
    assert resp.form['f1'].options == [('', False, '---')]
    assert LoggedError.count() == 1
    logged_error = LoggedError.select()[0]
    assert logged_error.formdef_id == str(formdef.id)
    assert (
        logged_error.summary == 'Invalid value "[None, None, None, None, None]" '
        'for custom view "as-datasource", CardDef "items", field "internal-id", operator "eq".'
    )

    # LazyFormDefObjectsManager
    formdef.fields[0].value_template = '{{ cards|objects:"items"|filter_by:"bool"|filter_value:True }}'
    formdef.store()
    LoggedError.wipe()
    resp = get_app(pub).get('/test/')
    assert resp.form['f1'].options == [('', False, '---')]
    assert LoggedError.count() == 1
    logged_error = LoggedError.select()[0]
    assert logged_error.formdef_id == str(formdef.id)
    assert logged_error.summary.startswith('Invalid value "<')
    assert logged_error.summary.endswith('for computed field "num"')

    # empty list
    formdef.fields[0].value_template = (
        '{{ cards|objects:"items"|filter_by:"attr"|filter_value:"unknown"|getlist:"form_internal_id"|list }}'
    )
    formdef.store()
    resp = get_app(pub).get('/test/')
    assert resp.form['f1'].options == [('', False, '---')]
    custom_view.filters['filter-internal-id-operator'] = 'ne'
    custom_view.store()
    resp = get_app(pub).get('/test/')
    assert {int(x[0]) for x in resp.form['f1'].options} == {1, 2, 3, 4, 5, 6, 7, 8, 9, 10}

    # attr2 is filled with integers
    formdef.fields[0].value_template = (
        '{{ cards|objects:"items"|filter_by:"bool"|filter_value:True|getlist:"attr2"|list }}'
    )
    formdef.store()
    resp = get_app(pub).get('/test/')
    assert {int(x[0]) for x in resp.form['f1'].options} == {1, 3, 5, 7, 9}

    # LazyList
    formdef.fields[0].value_template = (
        '{{ cards|objects:"items"|filter_by:"bool"|filter_value:True|getlist:"attr2" }}'
    )
    formdef.store()
    LoggedError.wipe()
    resp = get_app(pub).get('/test/')
    assert {int(x[0]) for x in resp.form['f1'].options} == {1, 3, 5, 7, 9}


def test_item_field_from_cards_check_lazy_live(pub):
    create_user(pub)

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'items'
    carddef.digest_templates = {'default': '{{form_var_name}}'}
    carddef.fields = [
        fields.StringField(id='0', label='string', varname='name'),
        fields.StringField(id='1', label='string', varname='attr'),
    ]
    carddef.store()
    carddef.data_class().wipe()
    for i, value in enumerate(['foo', 'bar', 'baz']):
        carddata = carddef.data_class()()
        carddata.data = {
            '0': value,
            '1': 'attr%s' % i,
        }
        carddata.just_created()
        carddata.store()

    ds = {'type': 'carddef:%s' % carddef.url_name}
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [
        fields.PageField(id='0', label='1st page'),
        fields.ItemField(id='1', label='string', varname='item', data_source=ds),
        fields.PageField(id='2', label='2nd page'),
        fields.CommentField(id='3', label='live value: {{ form_var_item_live_var_attr }}'),
        fields.PageField(id='4', label='3rd page'),
    ]
    formdef.store()
    formdef.data_class().wipe()

    resp = get_app(pub).get('/test/')
    resp.form['f1'] = '2'
    resp = resp.form.submit('submit')  # -> 2nd page
    assert 'live value: attr1' in resp

    # add a field with a condition on first page and third page
    formdef.fields[1:1] = [
        fields.StringField(
            id='5',
            label='field with condition',
            required='optional',
            condition={'type': 'django', 'value': '1'},
        ),
    ]
    formdef.fields.append(
        fields.StringField(
            id='6',
            label='second field with condition',
            required='optional',
            condition={'type': 'django', 'value': '1'},
        )
    )
    formdef.store()
    resp = get_app(pub).get('/test/')
    resp.form['f1'] = '2'
    resp = resp.form.submit('submit')  # -> 2nd page
    assert 'live value: attr1' in resp
    resp = resp.form.submit('submit')  # -> 3rd page
    resp = resp.form.submit('previous')  # -> 2nd page
    assert 'live value: attr1' in resp


@responses.activate
def test_field_live_condition_data_source_error(pub):
    ds = {'type': 'json', 'value': 'http://www.example.net/plop'}

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'Foo'
    formdef.fields = [
        fields.ItemField(id='1', label='string', data_source=ds, varname='foo'),
        fields.StringField(
            id='2',
            label='bar',
            required='required',
            varname='bar',
            condition={'type': 'django', 'value': 'form_var_foo_x == "bye"'},
        ),
    ]
    formdef.store()

    data = {'data': [{'id': '1', 'text': 'un'}, {'id': '2', 'text': 'deux', 'x': 'bye'}]}
    responses.get('http://www.example.net/plop', json=data)

    app = get_app(pub)
    resp = app.get('/foo/')
    assert 'f1' in resp.form.fields
    assert 'f2' not in resp.form.fields
    resp.form['f1'] = '2'

    with mock.patch.object(NamedDataSource, 'get_structured_value', lambda *args: None):
        live_resp = app.post('/foo/live', params=resp.form.submit_fields())
        assert live_resp.json == {
            'reason': 'form deserialization failed: no matching value in datasource (field id: 1, value: \'2\')',
            'result': 'error',
        }

    live_resp = app.post('/foo/live', params=resp.form.submit_fields())
    assert live_resp.json == {'result': {'1': {'visible': True}, '2': {'visible': True}}}


def test_comment_from_card_field(pub):
    create_user(pub)

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'Template'
    carddef.digest_templates = {'default': '{{ form_var_template }}'}
    carddef.fields = [
        fields.StringField(id='0', label='string', varname='template'),
    ]
    carddef.store()
    carddef.data_class().wipe()
    for i, value in enumerate(['foo', 'bar']):
        carddata = carddef.data_class()()
        carddata.data = {
            '0': '%s {{ form_var_foo }}' % value,
        }
        carddata.just_created()
        carddata.store()

    ds = {'type': 'carddef:%s' % carddef.url_name}
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [
        fields.StringField(id='0', label='string', varname='foo'),
        fields.ItemField(id='1', label='card', varname='card', data_source=ds),
        fields.CommentField(
            id='3',
            label='X{{ form_var_card_live_var_template }}Y{{ form_var_card_live_var_template|as_template }}Z',
        ),
    ]
    formdef.store()
    formdef.data_class().wipe()

    app = get_app(pub)
    resp = app.get('/test/')
    resp.form['f0'] = 'plop'
    resp.form['f1'] = '1'
    live_resp = app.post('/test/live', params=resp.form.submit_fields())
    assert live_resp.json['result']['3']['content'] == '<p>Xfoo {{ form_var_foo }}Yfoo plopZ</p>'
    resp.form['f1'] = '2'
    live_resp = app.post('/test/live', params=resp.form.submit_fields())
    assert live_resp.json['result']['3']['content'] == '<p>Xbar {{ form_var_foo }}Ybar plopZ</p>'


def test_comment_from_card_field_image_url(pub):
    create_user(pub)

    pub.load_site_options()
    pub.site_options.add_section('api-secrets')
    pub.site_options.set('api-secrets', 'example.net', 'yyy')
    pub.site_options.set('wscall-secrets', 'example.net', 'yyy')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'Images'
    carddef.digest_templates = {'default': '{{ form_var_file }}'}
    carddef.fields = [
        fields.FileField(id='0', label='File', varname='file'),
    ]
    carddef.store()
    carddef.data_class().wipe()

    with open(os.path.join(os.path.dirname(__file__), '..', 'image-with-gps-data.jpeg'), 'rb') as fd:
        image_content = fd.read()

    upload = PicklableUpload('test.jpg', content_type='image/jpeg')
    upload.receive([image_content])

    carddata = carddef.data_class()()
    carddata.data = {'0': upload}
    carddata.just_created()
    carddata.store()

    ds = {'type': 'carddef:%s' % carddef.url_name}
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [
        fields.ItemField(id='1', label='card', varname='card', data_source=ds),
        fields.CommentField(
            id='3',
            label='<img alt="{{ form_var_card }}" src="{% make_public_url url=form_var_card_live_var_file_url %}">',
        ),
    ]
    formdef.store()
    formdef.data_class().wipe()

    app = get_app(pub)
    resp = app.get('/test/')
    resp.form['f1'] = '1'
    assert resp.pyquery('[data-field-id="1"][data-live-source="true"]')
    live_resp = app.post('/test/live', params=resp.form.submit_fields() + [('modified_field_id[]', 'init')])
    assert pyquery.PyQuery(live_resp.json['result']['3']['content']).attr.alt == 'test.jpg'
    img_resp = app.get(pyquery.PyQuery(live_resp.json['result']['3']['content']).attr.src).follow()
    assert img_resp.headers['content-type'] == 'image/jpeg'


def test_live_file_prefill_from_card_field(pub):
    create_user(pub)

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'Template'
    carddef.digest_templates = {'default': '{{ form_var_file }}'}
    carddef.fields = [
        fields.FileField(id='0', label='file', varname='file'),
    ]
    carddef.store()
    carddef.data_class().wipe()
    for value in ('foo', 'bar'):
        carddata = carddef.data_class()()
        upload = PicklableUpload(f'{value}.txt', 'text/plain', 'ascii')
        upload.receive([value.encode()])
        carddata.data = {'0': upload}
        carddata.just_created()
        carddata.store()

    ds = {'type': 'carddef:%s' % carddef.url_name}
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [
        fields.ItemField(id='1', label='card', varname='card', data_source=ds),
        fields.FileField(
            id='2',
            label='file',
            prefill={'type': 'string', 'value': '{{ form_var_card_live_var_file }}'},
        ),
    ]
    formdef.store()
    formdef.data_class().wipe()

    app = get_app(pub)
    resp = app.get('/test/')
    resp.form['f1'] = '1'
    live_resp = app.post(
        '/test/live', params=resp.form.submit_fields() + [('modified_field_id[]', '1'), ('prefilled_2', 'on')]
    )
    assert live_resp.json['result']['2']['content']['name'] == 'foo.txt'
    assert app.get('/test/' + live_resp.json['result']['2']['content']['url']).text == 'foo'
    resp.form['f2$token'] = live_resp.json['result']['2']['content']['token']
    resp = resp.form.submit('submit')  # -> validation
    resp = resp.form.submit('submit')  # -> done
    assert formdef.data_class().count() == 1
    formdata = formdef.data_class().select()[0]
    assert formdata.data['2'].base_filename == 'foo.txt'
    assert formdata.data['2'].get_content() == b'foo'

    # do not get file if storage is opaque remote.
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'a+') as fd:
        fd.write(
            '''
[storage-remote]
label = remote storage
class = wcs.qommon.upload_storage.RemoteOpaqueUploadStorage
ws = https://crypto.example.net/ws1/
'''
        )

    formdef.fields[1].storage = 'remote'
    formdef.store()
    app = get_app(pub)
    resp = app.get('/test/')
    resp.form['f1'] = '1'
    live_resp = app.post(
        '/test/live', params=resp.form.submit_fields() + [('modified_field_id[]', '1'), ('prefilled_2', 'on')]
    )
    assert live_resp.json['result']['2']['content'] is None

    # try prefilling with garbage
    formdef.fields[1].storage = 'default'
    formdef.fields[1].prefill = {'type': 'string', 'value': 'garbage'}
    formdef.store()

    app = get_app(pub)
    resp = app.get('/test/')
    resp.form['f1'] = '1'
    live_resp = app.post(
        '/test/live', params=resp.form.submit_fields() + [('modified_field_id[]', '1'), ('prefilled_2', 'on')]
    )
    assert live_resp.json['result']['2']['content'] is None


def test_field_live_validation(pub):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'Foo'
    formdef.fields = [
        fields.StringField(id='1', label='Bar', required='required', validation={'type': 'digits'}),
    ]
    formdef.store()

    app = get_app(pub)
    resp = app.get('/foo/')
    url = resp.pyquery('form[data-live-validation-url]').attr('data-live-validation-url')
    assert url

    live_resp = get_app(pub).post(url, params=resp.form.submit_fields())
    assert live_resp.json == {'err': 2, 'msg': 'missing session'}

    live_resp = app.post(url, params=resp.form.submit_fields())
    assert live_resp.json == {'err': 2, 'msg': 'missing ?field parameter'}

    live_resp = app.post(url + '?field=xxx', params=resp.form.submit_fields())
    assert live_resp.json == {'err': 2, 'msg': 'unknown field'}

    live_resp = app.post(url + '?field=xxx__yyy', params=resp.form.submit_fields())
    assert live_resp.json == {'err': 2, 'msg': 'invalid ?field parameter'}

    resp.form['f1'] = '1234'
    live_resp = app.post(url + '?field=f1', params=resp.form.submit_fields())
    assert live_resp.json == {'err': 0}

    resp.form['f1'] = ''
    live_resp = app.post(url + '?field=f1', params=resp.form.submit_fields())
    assert live_resp.json == {'err': 1, 'msg': 'required field', 'errorType': 'valueMissing'}

    resp.form['f1'] = 'abc'
    live_resp = app.post(url + '?field=f1', params=resp.form.submit_fields())
    assert live_resp.json == {
        'err': 1,
        'msg': 'You should enter digits only, for example: 123.',
        'errorType': 'badInput',
    }


def test_block_field_live_validation(pub):
    BlockDef.wipe()

    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.StringField(id='1', label='Bar', required='required', validation={'type': 'digits'}),
    ]
    block.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'Foo'
    formdef.fields = [
        fields.BlockField(id='2', label='test', block_slug='foobar'),
    ]
    formdef.store()

    app = get_app(pub)
    resp = app.get('/foo/')
    url = resp.pyquery('form[data-live-validation-url]').attr('data-live-validation-url')
    assert url

    resp.form['f2$element0$f1'] = '1234'
    live_resp = app.post(url + '?field=f2__element0__f1', params=resp.form.submit_fields())
    assert live_resp.json == {'err': 0}

    resp.form['f2$element0$f1'] = ''
    live_resp = app.post(url + '?field=f2__element0__f1', params=resp.form.submit_fields())
    assert live_resp.json == {'err': 1, 'msg': 'required field', 'errorType': 'valueMissing'}

    resp.form['f2$element0$f1'] = 'abc'
    live_resp = app.post(url + '?field=f2__element0__f1', params=resp.form.submit_fields())
    assert live_resp.json == {
        'err': 1,
        'msg': 'You should enter digits only, for example: 123.',
        'errorType': 'badInput',
    }

    live_resp = app.post(url + '?field=f2__element0__fX', params=resp.form.submit_fields())
    assert live_resp.json == {'err': 2, 'msg': 'unknown sub field'}


@responses.activate
def test_field_live_too_long(pub, freezer):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'Foo'
    formdef.fields = [
        fields.StringField(id='1', label='string', varname='foo1'),
        fields.StringField(id='2', label='string', varname='foo2'),
    ]
    formdef.store()

    user = create_user(pub)
    user.is_admin = True
    user.store()

    app = get_app(pub)
    resp = app.get('/foo/')
    resp.form['f1'] = '2'

    def delay(*args):
        freezer.move_to(datetime.timedelta(seconds=10))
        return True

    LoggedError.wipe()
    with mock.patch('wcs.fields.base.WidgetField.is_visible') as is_visible:
        is_visible.side_effect = delay
        live_resp = app.post('/foo/live', params=resp.form.submit_fields())
        assert live_resp.json == {'result': {'1': {'visible': True}, '2': {'visible': True}}}
        assert LoggedError.count() == 1
        logged_error = LoggedError.select()[0]
        assert logged_error.summary == '/live call is taking too long'
        assert 'timings = ' in logged_error.traceback

        # check timings panel
        resp_error = login(get_app(pub), username='foo', password='foo').get(
            f'/backoffice/studio/logged-errors/{logged_error.id}/'
        )
        assert (
            resp_error.pyquery('#panel-timings td a').attr.href
            == 'http://example.net/backoffice/forms/1/fields/1/'
        )

    LoggedError.wipe()
    formdef.fields = [
        fields.StringField(id='1', label='string', varname='foo1'),
        fields.CommentField(id='2', label='comment {{webservice.long}}'),
    ]
    formdef.store()
    NamedWsCall.wipe()
    wscall = NamedWsCall(name='long')
    wscall.request = {'url': 'http://remote.example.net/json'}
    wscall.store()

    with mock.patch('wcs.wscalls.NamedWsCall.call') as call_webservice:
        call_webservice.side_effect = delay
        live_resp = app.post('/foo/live', params=resp.form.submit_fields())
        assert live_resp.json == {
            'result': {'1': {'visible': True}, '2': {'content': '<p>comment True</p>', 'visible': True}}
        }
        assert LoggedError.count() == 1
        logged_error = LoggedError.select()[0]
        assert logged_error.summary == '/live call is taking too long'
        assert 'timings = ' in logged_error.traceback


@responses.activate
def test_field_live_hidden_comment_disabled_errors(pub):
    FormDef.wipe()
    NamedWsCall.wipe()
    LoggedError.wipe()

    wscall = NamedWsCall(name='test')
    wscall.request = {'url': '{% if form_var_foo %}http://remote.example.net/json{% endif %}'}
    wscall.record_on_errors = True
    wscall.store()

    formdef = FormDef()
    formdef.name = 'Foo'
    formdef.fields = [
        fields.StringField(id='1', label='string', varname='foo'),
        fields.CommentField(
            id='2',
            label='comment {{form_var_foo}} {{webservice.test.hello}}',
            condition={'type': 'django', 'value': 'form_var_foo'},
        ),
    ]
    formdef.store()

    responses.get('http://remote.example.net/json', json={'hello': 'world'})
    app = get_app(pub)
    resp = app.get('/foo/')
    assert resp.pyquery('[data-field-id="2"]').text() == 'comment None'
    assert len(responses.calls) == 0
    assert LoggedError.count() == 0
    resp.form['f1'] = '2'

    live_resp = app.post('/foo/live', params=resp.form.submit_fields())
    assert live_resp.json == {
        'result': {'1': {'visible': True}, '2': {'content': '<p>comment 2 world</p>', 'visible': True}}
    }
    assert len(responses.calls) == 1
    assert LoggedError.count() == 0
