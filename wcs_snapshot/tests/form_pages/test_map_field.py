import os

import pytest
import responses

from wcs import fields
from wcs.admin.settings import UserFieldsFormDef
from wcs.categories import Category
from wcs.data_sources import NamedDataSource

from ..utilities import clean_temporary_pub, create_temporary_pub, get_app, login
from .test_all import create_formdef, create_user


def pytest_generate_tests(metafunc):
    if 'pub' in metafunc.fixturenames:
        metafunc.parametrize('pub', ['sql', 'sql-lazy'], indirect=True)


@pytest.fixture
def pub(request):
    pub = create_temporary_pub(lazy_mode=bool('lazy' in request.param))
    pub.cfg['identification'] = {'methods': ['password']}
    pub.cfg['language'] = {'language': 'en'}
    pub.cfg['users'] = {
        'field_phone': '_phone',
    }
    pub.write_cfg()

    formdef = UserFieldsFormDef(pub)
    formdef.fields = [
        fields.StringField(id='_phone', label='phone', varname='phone', validation={'type': 'phone'})
    ]
    formdef.store()

    Category.wipe()
    cat = Category(name='foobar')
    cat.store()

    return pub


def teardown_module(module):
    clean_temporary_pub()


def test_form_map_field_back_and_submit(pub):
    formdef = create_formdef()
    formdef.fields = [
        fields.MapField(id='0', label='map'),
        fields.StringField(
            id='1', label='street', required='required', prefill={'type': 'geolocation', 'value': 'road'}
        ),
    ]
    formdef.store()
    formdef.data_class().wipe()
    resp = get_app(pub).get('/test/')
    assert 'qommon.map.js' in resp.text
    assert 'qommon.geolocation.js' in resp.text
    assert (
        resp.pyquery('.qommon-map')[0].attrib['data-tile-urltemplate']
        == 'https://tiles.entrouvert.org/hdm/{z}/{x}/{y}.png'
    )
    assert (
        resp.pyquery('.qommon-map')[0].attrib['data-map-attribution']
        == 'Map data &copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
    )

    # with a real user interaction this would get set by javascript
    resp.forms[0]['f0$latlng'].value = '1.234;-1.234'
    assert 'data-geolocation="road"' in resp.text

    # check required field
    resp = resp.forms[0].submit('submit')
    assert 'Check values then click submit.' not in resp.text
    assert 'data-geolocation="road"' in resp.text
    resp.forms[0]['f1'].value = 'bla'

    # check summary page
    resp = resp.forms[0].submit('submit')
    assert 'Check values then click submit.' in resp.text
    assert 'data-init-lng="-1.234"' in resp.text
    assert 'data-init-lat="1.234"' in resp.text

    # get back to the map field
    resp = resp.forms[0].submit('previous')
    # check the field is still marked as holding the road
    assert 'data-geolocation="road"' in resp.text
    assert resp.forms[0]['f0$latlng'].value == '1.234;-1.234'

    # back to summary page
    resp = resp.forms[0].submit('submit')

    # and submitting the form
    resp = resp.forms[0].submit('submit')
    assert resp.status_int == 302
    resp = resp.follow()
    assert 'The form has been recorded' in resp.text
    assert formdef.data_class().count() == 1
    data_id = formdef.data_class().select()[0].id
    data = formdef.data_class().get(data_id)
    assert data.data == {'0': {'lat': 1.234, 'lon': -1.234}, '1': 'bla'}


def test_form_map_initial_zoom_level(pub):
    formdef = create_formdef()
    formdef.fields = [
        fields.MapField(id='0', label='map'),
    ]
    formdef.store()
    resp = get_app(pub).get('/test/')
    assert 'data-initial_zoom="13"' in resp.text
    pub.cfg['misc']['default-zoom-level'] = '16'
    pub.write_cfg()
    resp = get_app(pub).get('/test/')
    assert 'data-initial_zoom="16"' in resp.text

    formdef.fields[0].initial_zoom = '11'
    formdef.store()
    resp = get_app(pub).get('/test/')
    assert 'data-initial_zoom="11"' in resp.text


def test_form_map_geolocation_text_field(pub):
    formdef = create_formdef()
    formdef.fields = [
        fields.MapField(id='0', label='map'),
        fields.TextField(
            id='1', label='street', required='required', prefill={'type': 'geolocation', 'value': 'road'}
        ),
    ]
    formdef.store()
    resp = get_app(pub).get('/test/')
    formdef.data_class().wipe()
    assert 'qommon.map.js' in resp.text
    assert 'qommon.geolocation.js' in resp.text
    assert 'WCS_DEFAULT_GEOCODING_COUNTRY' not in resp.text

    # check page has default geocoding country in a javascript variable if set
    if not pub.site_options.has_section('options'):
        pub.site_options.add_section('options')
    pub.site_options.set('options', 'default-geocoding-country', 'France')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)

    resp = get_app(pub).get('/test/')
    assert 'WCS_DEFAULT_GEOCODING_COUNTRY' in resp.text


def test_form_map_geolocation_select2_field(pub):
    NamedDataSource.wipe()
    data_source = NamedDataSource(name='foobar')
    data_source.data_source = {'type': 'json', 'value': 'http://remote.example.net/json'}
    data_source.query_parameter = 'q'
    data_source.id_parameter = 'id'
    data_source.store()

    formdef = create_formdef()
    formdef.fields = [
        fields.MapField(id='0', label='map'),
        fields.ItemField(
            id='1',
            label='address',
            required='required',
            display_mode='autocomplete',
            prefill={'type': 'geolocation', 'value': 'address-id'},
            data_source={'type': 'foobar'},
        ),
    ]
    formdef.store()
    resp = get_app(pub).get('/test/')
    formdef.data_class().wipe()
    assert 'qommon.map.js' in resp.text
    assert 'qommon.geolocation.js' in resp.text


def test_form_map_multi_page(pub):
    formdef = create_formdef()
    formdef.fields = [
        fields.PageField(id='0', label='1st page'),
        fields.MapField(id='1', label='map'),
        fields.PageField(id='2', label='2nd page'),
        fields.StringField(id='3', label='string 2'),
    ]
    formdef.store()
    resp = get_app(pub).get('/test/')
    formdef.data_class().wipe()
    resp.forms[0]['f1$latlng'] = '1.234;-1.234'
    assert resp.forms[0].fields['submit'][0].value_if_submitted() == 'Next'
    resp = resp.forms[0].submit('submit')
    assert resp.forms[0]['previous']
    resp.forms[0]['f3'] = 'bar'
    resp = resp.forms[0].submit('submit')
    assert resp.forms[0]['f1$latlng'].value == '1.234;-1.234'
    assert 'Check values then click submit.' in resp.text
    resp = resp.forms[0].submit('submit')
    assert resp.status_int == 302
    resp = resp.follow()
    assert 'The form has been recorded' in resp.text
    assert formdef.data_class().count() == 1
    data_id = formdef.data_class().select()[0].id
    data = formdef.data_class().get(data_id)
    assert data.data == {'1': {'lat': 1.234, 'lon': -1.234}, '3': 'bar'}


def test_form_map_field_default_position(pub):
    formdef = create_formdef()
    formdef.fields = [
        fields.PageField(id='0', label='1st page'),
        fields.StringField(id='1', label='address', required='required', varname='address'),
        fields.PageField(id='2', label='2nd page'),
        fields.MapField(id='3', label='map'),
    ]
    formdef.store()
    formdef.data_class().wipe()

    resp = get_app(pub).get('/test/')
    resp.form['f1'] = '169 rue du chateau, paris'
    resp = resp.form.submit('submit')
    assert resp.pyquery('.qommon-map').attr('data-def-lat') == '50.84'

    formdef.fields[3].initial_position = 'point'
    formdef.fields[3].default_position = '13;12'
    formdef.store()
    resp = get_app(pub).get('/test/')
    resp.form['f1'] = '169 rue du chateau, paris'
    resp = resp.form.submit('submit')
    assert resp.pyquery('.qommon-map').attr('data-def-lat') == '13'

    formdef.fields[3].initial_position = 'point'
    formdef.fields[3].default_position = {'lat': 13, 'lon': 12}
    formdef.store()
    resp = get_app(pub).get('/test/')
    resp.form['f1'] = '169 rue du chateau, paris'
    resp = resp.form.submit('submit')
    assert resp.pyquery('.qommon-map').attr('data-def-lat') == '13'

    formdef.fields[3].initial_position = 'geoloc'
    formdef.store()
    resp = get_app(pub).get('/test/')
    resp.form['f1'] = '169 rue du chateau, paris'
    resp = resp.form.submit('submit')
    assert resp.pyquery('.qommon-map').attr('data-init_with_geoloc')

    formdef.fields[3].initial_position = 'template'
    formdef.fields[3].position_template = '13;12'
    formdef.store()

    resp = get_app(pub).get('/test/')
    resp.form['f1'] = '169 rue du chateau, paris'
    resp = resp.form.submit('submit')
    assert resp.pyquery('.qommon-map').attr('data-def-lat') == '13'
    assert resp.pyquery('.qommon-map').attr('data-def-template')

    formdef.fields[3].initial_position = 'template'
    formdef.fields[3].position_template = '{{ form_var_address }}'
    formdef.store()

    resp = get_app(pub).get('/test/')
    resp.form['f1'] = '169 rue du chateau, paris'
    with responses.RequestsMock() as rsps:
        rsps.get(
            'https://nominatim.openstreetmap.org/search', json=[{'lat': '48.8337085', 'lon': '2.3233693'}]
        )
        resp = resp.form.submit('submit')
        assert resp.pyquery('.qommon-map').attr('data-def-lat') == '48.83370850'
        assert resp.pyquery('.qommon-map').attr('data-def-template')

    formdef.fields[3].initial_position = 'geoloc-front-only'
    formdef.store()
    resp = get_app(pub).get('/test/')
    resp.form['f1'] = '169 rue du chateau, paris'
    resp = resp.form.submit('submit')
    assert resp.pyquery('.qommon-map').attr('data-init_with_geoloc')

    user = create_user(pub)
    role = pub.role_class(name='xxx')
    role.store()
    user.roles = [role.id]
    user.is_admin = True
    user.store()

    formdef.backoffice_submission_roles = [role.id]
    formdef.store()

    app = login(get_app(pub), username='foo', password='foo')
    resp = app.get('/backoffice/submission/test/')
    resp.form['f1'] = '169 rue du chateau, paris'
    resp = resp.form.submit('submit')
    assert not resp.pyquery('.qommon-map').attr('data-init_with_geoloc')


def test_form_map_field_prefill_position(pub):
    user = create_user(pub)
    role = pub.role_class(name='xxx')
    role.store()
    user.roles = [role.id]
    user.is_admin = True
    user.store()

    formdef = create_formdef()
    formdef.fields = [
        fields.MapField(id='3', label='map', prefill={'type': 'geolocation', 'value': 'position'}),
    ]
    formdef.backoffice_submission_roles = [role.id]
    formdef.store()
    formdef.data_class().wipe()

    resp = get_app(pub).get('/test/')
    assert resp.pyquery('.MapWidget').attr('data-geolocation') == 'position'

    app = login(get_app(pub), username='foo', password='foo')
    resp = app.get('/backoffice/submission/test/')
    assert resp.pyquery('.MapWidget').attr('data-geolocation') == 'position'

    formdef.fields[0].prefill = {'type': 'geolocation', 'value': 'position-front-only'}
    formdef.store()

    resp = get_app(pub).get('/test/')
    assert resp.pyquery('.MapWidget').attr('data-geolocation') == 'position'

    resp = app.get('/backoffice/submission/test/')
    assert not resp.pyquery('.MapWidget').attr('data-geolocation')


def test_form_map_field_mapbox_gl(pub):
    formdef = create_formdef()
    formdef.fields = [fields.MapField(id='3', label='map')]
    formdef.store()
    formdef.data_class().wipe()

    resp = get_app(pub).get('/test/')
    assert 'mapbox' not in resp.text

    if not pub.site_options.has_section('options'):
        pub.site_options.add_section('options')
    pub.site_options.set('options', 'map-tile-urltemplate', 'https://example.net/tiles.json')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)

    resp = get_app(pub).get('/test/')
    assert resp.pyquery('.qommon-map').attr('data-tile-urltemplate') == 'https://example.net/tiles.json'
    assert 'mapbox' in resp.text

    pub.site_options.set('options', 'map-tile-urltemplate', 'https://example.net/{z}/{x}/{y}.png')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)

    resp = get_app(pub).get('/test/')
    assert resp.pyquery('.qommon-map').attr('data-tile-urltemplate') == 'https://example.net/{z}/{x}/{y}.png'
    assert 'mapbox' not in resp.text
