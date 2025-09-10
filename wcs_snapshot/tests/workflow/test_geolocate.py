import os
import urllib.parse

import pytest
import responses
from django.test import override_settings
from quixote import cleanup

from wcs import sql
from wcs.fields import FileField, MapField, StringField
from wcs.formdef import FormDef
from wcs.logged_errors import LoggedError
from wcs.qommon.errors import ConnectionError
from wcs.qommon.http_request import HTTPRequest
from wcs.qommon.upload_storage import PicklableUpload
from wcs.wf.geolocate import GeolocateWorkflowStatusItem
from wcs.workflows import Workflow

from ..test_sql import column_exists_in_table
from ..utilities import clean_temporary_pub, create_temporary_pub


def setup_module(module):
    cleanup()


def teardown_module(module):
    clean_temporary_pub()


@pytest.fixture
def pub(request):
    pub = create_temporary_pub()
    pub.cfg['language'] = {'language': 'en'}
    pub.write_cfg()
    req = HTTPRequest(None, {'SERVER_NAME': 'example.net', 'SCRIPT_NAME': ''})
    req.response.filter = {}
    req._user = None
    pub._set_request(req)
    pub.set_config(req)
    return pub


def test_geolocate_action_enable_geolocation(pub):
    # switch to a workflow with geolocation
    formdef = FormDef()
    formdef.name = 'foo'
    formdef.fields = []
    formdef.store()
    formdef.data_class().wipe()

    workflow = Workflow(name='wf')
    st1 = workflow.add_status('Status1', 'st1')
    item = st1.add_action('geolocate')
    item.method = 'address_string'
    item.address_string = '{{form_var_string}}, paris, france'
    workflow.store()

    formdef.change_workflow(workflow)
    assert formdef.geolocations

    _, cur = sql.get_connection_and_cursor()
    assert column_exists_in_table(cur, formdef.table_name, 'geoloc_base')
    cur.close()

    # change to current workflow
    workflow = Workflow(name='wf2')
    st1 = workflow.add_status('Status1', 'st1')
    workflow.store()

    formdef = FormDef()
    formdef.name = 'bar'
    formdef.fields = []
    formdef.workflow = workflow
    formdef.store()
    assert not formdef.geolocations

    item = st1.add_action('geolocate')
    item.method = 'address_string'
    item.address_string = '{{form_var_string}}, paris, france'
    workflow.store()
    pub.process_after_jobs()

    formdef.refresh_from_storage()
    assert formdef.geolocations

    _, cur = sql.get_connection_and_cursor()
    assert column_exists_in_table(cur, formdef.table_name, 'geoloc_base')
    cur.close()


def test_geolocate_address(pub):
    formdef = FormDef()
    formdef.geolocations = {'base': 'bla'}
    formdef.name = 'baz'
    formdef.fields = [
        StringField(id='1', label='String', varname='string'),
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {'1': '169 rue du chateau'}
    formdata.just_created()
    formdata.store()
    pub.substitutions.feed(formdata)

    item = GeolocateWorkflowStatusItem()
    item.method = 'address_string'
    item.address_string = '[form_var_string], paris, france'

    with responses.RequestsMock() as rsps:
        rsps.get(
            'https://nominatim.openstreetmap.org/search', json=[{'lat': '48.8337085', 'lon': '2.3233693'}]
        )
        item.perform(formdata)
        assert 'https://nominatim.openstreetmap.org/search' in rsps.calls[-1].request.url
        assert urllib.parse.quote('169 rue du chateau, paris') in rsps.calls[-1].request.url
        assert int(formdata.geolocations['base']['lat']) == 48
        assert int(formdata.geolocations['base']['lon']) == 2

    pub.load_site_options()
    pub.site_options.set('options', 'nominatim_key', 'KEY')
    with responses.RequestsMock() as rsps:
        rsps.get(
            'https://nominatim.openstreetmap.org/search', json=[{'lat': '48.8337085', 'lon': '2.3233693'}]
        )
        item.perform(formdata)
        assert 'https://nominatim.openstreetmap.org/search' in rsps.calls[-1].request.url
        assert urllib.parse.quote('169 rue du chateau, paris') in rsps.calls[-1].request.url
        assert 'key=KEY' in rsps.calls[-1].request.url
        assert int(formdata.geolocations['base']['lat']) == 48
        assert int(formdata.geolocations['base']['lon']) == 2

    pub.load_site_options()
    pub.site_options.set('options', 'geocoding_service_url', 'http://example.net/')
    with responses.RequestsMock() as rsps:
        rsps.get('http://example.net/', json=[{'lat': '48.8337085', 'lon': '2.3233693'}])
        item.perform(formdata)
        assert 'http://example.net/?q=' in rsps.calls[-1].request.url

    pub.site_options.set('options', 'geocoding_service_url', 'http://example.net/?param=value')
    with responses.RequestsMock() as rsps:
        rsps.get('http://example.net/', json=[{'lat': '48.8337085', 'lon': '2.3233693'}])
        item.perform(formdata)
        assert 'http://example.net/?param=value&' in rsps.calls[-1].request.url

    # check for invalid ezt
    item.address_string = '[if-any], paris, france'
    formdata.geolocations = None
    item.perform(formdata)
    assert formdata.geolocations == {}
    assert LoggedError.count() == 1
    logged_error = LoggedError.select()[0]
    assert (
        logged_error.summary
        == 'error in template for address string [syntax error in ezt template: unclosed block at line 1 and column 24]'
    )
    assert logged_error.formdata_id == str(formdata.id)
    assert logged_error.exception_class == 'TemplateError'
    assert (
        logged_error.exception_message
        == 'syntax error in ezt template: unclosed block at line 1 and column 24'
    )
    LoggedError.wipe()

    # check for None
    item.address_string = '=None'
    formdata.geolocations = None
    item.perform(formdata)
    assert formdata.geolocations == {}

    # check for nominatim returning an empty result set
    item.address_string = '[form_var_string], paris, france'
    formdata.geolocations = None
    with responses.RequestsMock() as rsps:
        rsps.get('http://example.net/', json=[])
        item.perform(formdata)
        assert formdata.geolocations == {}

    # check for nominatim bad json
    formdata.geolocations = None
    with responses.RequestsMock() as rsps:
        rsps.get('http://example.net/', body=b'bad json')
        item.perform(formdata)
        assert formdata.geolocations == {}

    # check for nominatim connection error
    pub.site_options.remove_option('options', 'geocoding_service_url')
    with override_settings(NOMINATIM_URL='https://nominatim.example.org'):
        formdata.geolocations = None
        with responses.RequestsMock() as rsps:
            rsps.get(
                'https://nominatim.example.org/search',
                body=ConnectionError('some error connecting to https://nominatim.example.org'),
            )
            item.perform(formdata)
            assert formdata.geolocations == {}
        assert LoggedError.count() == 1
        logged_error = LoggedError.select()[0]
        assert (
            logged_error.summary
            == 'error calling geocoding service [some error connecting to (nominatim URL)]'
        )
        assert logged_error.formdata_id == str(formdata.id)
        assert logged_error.exception_class is None
        assert logged_error.exception_message is None


def test_geolocate_image(pub):
    formdef = FormDef()
    formdef.name = 'baz'
    formdef.geolocations = {'base': 'bla'}
    formdef.fields = [
        FileField(id='3', label='File', varname='file'),
    ]
    formdef.store()

    upload = PicklableUpload('test.jpeg', 'image/jpeg')
    with open(os.path.join(os.path.dirname(__file__), '..', 'image-with-gps-data.jpeg'), 'rb') as fd:
        upload.receive([fd.read()])

    formdata = formdef.data_class()()
    formdata.data = {'3': upload}
    formdata.just_created()
    formdata.store()
    pub.substitutions.feed(formdata)

    item = GeolocateWorkflowStatusItem()
    item.method = 'photo_variable'

    for expression in ('{{ form_var_file }}', '{{ form_var_file_raw }}'):
        formdata.geolocations = None
        item.photo_variable = expression
        item.perform(formdata)
        assert int(formdata.geolocations['base']['lat']) == -1
        assert int(formdata.geolocations['base']['lon']) == 6

    # invalid expression
    formdata.geolocations = None
    item.photo_variable = '=1/0'
    item.perform(formdata)
    assert formdata.geolocations == {}

    # invalid type
    formdata.geolocations = None
    item.photo_variable = '="bla"'
    item.perform(formdata)
    assert formdata.geolocations == {}

    # invalid photo
    upload = PicklableUpload('test.jpeg', 'image/jpeg')
    with open(os.path.join(os.path.dirname(__file__), '..', 'template.odt'), 'rb') as fd:
        upload.receive([fd.read()])
    formdata.data = {'3': upload}
    formdata.geolocations = None
    item.perform(formdata)
    assert formdata.geolocations == {}


def test_geolocate_map(pub):
    formdef = FormDef()
    formdef.name = 'baz'
    formdef.geolocations = {'base': 'bla'}
    formdef.fields = [
        MapField(id='2', label='Map', varname='map'),
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {'2': {'lat': 48.8337085, 'lon': 2.3233693}}
    formdata.just_created()
    formdata.store()
    pub.substitutions.feed(formdata)

    item = GeolocateWorkflowStatusItem()
    item.method = 'map_variable'
    item.map_variable = '{{ form_var_map }}'

    item.perform(formdata)
    assert int(formdata.geolocations['base']['lat']) == 48
    assert int(formdata.geolocations['base']['lon']) == 2


def test_geolocate_overwrite(pub):
    formdef = FormDef()
    formdef.name = 'baz'
    formdef.geolocations = {'base': 'bla'}
    formdef.fields = [
        MapField(id='2', label='Map', varname='map'),
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {'2': {'lat': 48.8337085, 'lon': 2.3233693}}
    formdata.just_created()
    formdata.store()
    pub.substitutions.feed(formdata)

    item = GeolocateWorkflowStatusItem()
    item.method = 'map_variable'
    item.map_variable = '{{ form_var_map }}'

    item.perform(formdata)
    assert int(formdata.geolocations['base']['lat']) == 48
    assert int(formdata.geolocations['base']['lon']) == 2

    formdata.data = {'2': {'lat': 48.8337085, 'lon': 3.3233693}}
    item.perform(formdata)
    assert int(formdata.geolocations['base']['lat']) == 48
    assert int(formdata.geolocations['base']['lon']) == 3

    formdata.data = {'2': {'lat': 48.8337085, 'lon': 4.3233693}}
    item.overwrite = False
    item.perform(formdata)
    assert int(formdata.geolocations['base']['lat']) == 48
    assert int(formdata.geolocations['base']['lon']) == 3
