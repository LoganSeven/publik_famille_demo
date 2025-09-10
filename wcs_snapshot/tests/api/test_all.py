import json
import os

import pytest
import responses

from wcs.api_utils import sign_url
from wcs.formdef import FormDef
from wcs.qommon.afterjobs import AfterJob
from wcs.qommon.http_request import HTTPRequest
from wcs.sql import ApiAccess
from wcs.tracking_code import TrackingCode

from ..utilities import clean_temporary_pub, create_temporary_pub, get_app, login


@pytest.fixture
def pub(emails):
    pub = create_temporary_pub()

    req = HTTPRequest(None, {'SCRIPT_NAME': '/', 'SERVER_NAME': 'example.net'})
    pub.set_app_dir(req)
    pub.cfg['identification'] = {'methods': ['password']}
    pub.cfg['language'] = {'language': 'en'}
    pub.write_cfg()

    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        fd.write(
            '''\
[api-secrets]
coucou = 1234
'''
        )

    return pub


def teardown_module(module):
    clean_temporary_pub()


@pytest.mark.parametrize('auth', ['signature', 'http-basic'])
def test_tracking_code(pub, auth, admin_user):
    FormDef.wipe()

    app = get_app(pub)

    if auth == 'http-basic':
        ApiAccess.wipe()
        access = ApiAccess()
        access.name = 'test'
        access.access_identifier = 'test'
        access.access_key = '12345'
        access.store()

        def get_url(url, **kwargs):
            app.set_authorization(('Basic', ('test', '12345')))
            return app.get(url, **kwargs)

    else:

        def get_url(url, **kwargs):
            if '?' in url:
                url += '&orig=coucou'
            else:
                url += '?orig=coucou'
            return app.get(sign_url(url, '1234'), **kwargs)

    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = []
    formdef.enable_tracking_codes = True
    formdef.store()

    data_class = formdef.data_class()
    formdata = data_class()
    formdata.store()

    code = TrackingCode()
    code.formdata = formdata
    code.store()

    if auth == 'http-basic':
        # wrong authentication
        app = get_app(pub)
        app.set_authorization(('Basic', ('test', 'xxx')))
        app.get('/api/code/foobar', status=403)

        # authentication with restricted api access
        role = pub.role_class('xxx')
        role.store()
        access.roles = [role]
        access.store()
        get_url('/api/code/foobar', status=403)
        access.roles = []
        access.store()
    else:
        # missing signature
        get_app(pub).get('/api/code/foobar', status=403)

    resp = get_url('/api/code/foobar', status=404)
    assert resp.json['err'] == 1

    resp = get_url('/api/code/%s' % code.id, status=200)
    assert resp.json['err'] == 0
    assert resp.json['url'] == 'http://example.net/test/%s/' % formdata.id
    assert get_app(pub).get(resp.json['load_url']).location == formdata.get_url()

    resp = get_url('/api/code/%s?backoffice=true' % code.id, status=200)
    assert resp.json['err'] == 0
    assert resp.json['url'] == 'http://example.net/backoffice/management/test/%s/' % formdata.id
    app2 = login(get_app(pub))
    resp = app2.get(resp.json['load_url'])
    assert resp.location == formdata.get_backoffice_url()
    resp = resp.follow()
    assert 'This form has been accessed via its tracking code' in resp.text

    formdef.enable_tracking_codes = False
    formdef.store()
    resp = get_url('/api/code/%s' % code.id, status=404)

    formdef.enable_tracking_codes = True
    formdef.store()
    formdata.remove_self()
    resp = get_url('/api/code/%s' % code.id, status=404)


def test_validate_condition(pub):
    resp = get_app(pub).get('/api/validate-condition?type=django&value_django=un+%C3%A9l%C3%A9phant')
    assert resp.json['msg'].startswith("syntax error: Unused 'éléphant'")
    resp = get_app(pub).get('/api/validate-condition?type=django&value_django=~2')
    assert resp.json['msg'].startswith('syntax error')
    resp = get_app(pub).get('/api/validate-condition?type=django&value_django=%22...%22+inf')  # "..." + inf
    assert resp.json['msg'].startswith('syntax error')

    resp = get_app(pub).get('/api/validate-condition?type=unknown&value_unknown=2')
    assert resp.json['msg'] == 'unknown condition type'

    resp = get_app(pub).get('/api/validate-condition?type=django&value_django=today > "2023"')
    assert resp.json == {'msg': ''}
    resp = get_app(pub).get(
        '/api/validate-condition?type=django&value_django=today > "2023"&warn-on-datetime=false'
    )
    assert resp.json == {'msg': ''}
    resp = get_app(pub).get(
        '/api/validate-condition?type=django&value_django=today > "2023"&warn-on-datetime=true'
    )
    assert resp.json['msg'].startswith('Warning: conditions are only evaluated when entering')

    resp = get_app(pub).get(
        '/api/validate-condition?type=django&value_django=x|age_in_days > 10&warn-on-datetime=true'
    )
    assert resp.json['msg'].startswith('Warning: conditions are only evaluated when entering')
    resp = get_app(pub).get(
        '/api/validate-condition?type=django&value_django=x|age_in_days|abs > 10&warn-on-datetime=true'
    )
    assert resp.json['msg'].startswith('Warning: conditions are only evaluated when entering')


def test_reverse_geocoding(pub):
    with responses.RequestsMock() as rsps:
        rsps.get('https://nominatim.openstreetmap.org/reverse', json={'address': 'xxx'})
        get_app(pub).get('/api/reverse-geocoding', status=400)
        resp = get_app(pub).get('/api/reverse-geocoding?lat=0&lon=0')
        assert resp.content_type == 'application/json'
        assert resp.text == json.dumps({'address': 'xxx'})
        assert (
            rsps.calls[-1].request.url
            == 'https://nominatim.openstreetmap.org/reverse?zoom=18&format=json&addressdetails=1&lat=0&lon=0&accept-language=en'
        )

        pub.site_options.add_section('options')
        pub.site_options.set('options', 'nominatim_reverse_zoom_level', '16')
        with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
            pub.site_options.write(fd)
        resp = get_app(pub).get('/api/reverse-geocoding?lat=0&lon=0')
        assert (
            rsps.calls[-1].request.url
            == 'https://nominatim.openstreetmap.org/reverse?zoom=16&format=json&addressdetails=1&lat=0&lon=0&accept-language=en'
        )

        pub.site_options.set('options', 'nominatim_key', 'KEY')
        with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
            pub.site_options.write(fd)
        resp = get_app(pub).get('/api/reverse-geocoding?lat=0&lon=0')
        assert (
            rsps.calls[-1].request.url
            == 'https://nominatim.openstreetmap.org/reverse?zoom=16&key=KEY&format=json&addressdetails=1&lat=0&lon=0&accept-language=en'
        )

        pub.site_options.set('options', 'nominatim_contact_email', 'test@example.net')
        with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
            pub.site_options.write(fd)
        resp = get_app(pub).get('/api/reverse-geocoding?lat=0&lon=0')
        assert (
            rsps.calls[-1].request.url
            == 'https://nominatim.openstreetmap.org/reverse?zoom=16&key=KEY&email=test%40example.net&'
            'format=json&addressdetails=1&lat=0&lon=0&accept-language=en'
        )

        pub.site_options.set(
            'options', 'reverse_geocoding_service_url', 'http://reverse.example.net/?param=value'
        )
        rsps.get('http://reverse.example.net/', json={'address': 'xxx'})
        with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
            pub.site_options.write(fd)
        resp = get_app(pub).get('/api/reverse-geocoding?lat=0&lon=0')
        assert (
            rsps.calls[-1].request.url
            == 'http://reverse.example.net/?param=value&format=json&addressdetails=1&lat=0&lon=0&accept-language=en'
        )


def test_geocoding(pub):
    with responses.RequestsMock() as rsps:
        rsps.get('https://nominatim.openstreetmap.org/search', json=[{'lat': 0, 'lon': 0}])
        get_app(pub).get('/api/geocoding', status=400)
        resp = get_app(pub).get('/api/geocoding?q=test')
        assert resp.content_type == 'application/json'
        assert resp.text == json.dumps([{'lat': 0, 'lon': 0}])
        assert (
            rsps.calls[-1].request.url
            == 'https://nominatim.openstreetmap.org/search?format=json&q=test&accept-language=en'
        )

        pub.site_options.add_section('options')
        pub.site_options.set('options', 'map-bounds-top-left', '1.23;2.34')
        pub.site_options.set('options', 'map-bounds-bottom-right', '2.34;3.45')
        with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
            pub.site_options.write(fd)
        resp = get_app(pub).get('/api/geocoding?q=test')
        assert rsps.calls[-1].request.url == (
            'https://nominatim.openstreetmap.org/search?viewbox=2.34%2C1.23%2C3.45%2C2.34&bounded=1&'
            'format=json&q=test&accept-language=en'
        )

        pub.site_options.set('options', 'nominatim_key', 'KEY')
        pub.site_options.set('options', 'map-bounds-top-left', '')
        pub.site_options.set('options', 'map-bounds-bottom-right', '')
        with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
            pub.site_options.write(fd)
        resp = get_app(pub).get('/api/geocoding?q=test')
        assert (
            rsps.calls[-1].request.url
            == 'https://nominatim.openstreetmap.org/search?key=KEY&format=json&q=test&accept-language=en'
        )

        pub.site_options.set('options', 'map-bounds-top-left', '1.23;2.34')
        pub.site_options.set('options', 'map-bounds-bottom-right', '2.34;3.45')
        with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
            pub.site_options.write(fd)
        resp = get_app(pub).get('/api/geocoding?q=test')
        assert rsps.calls[-1].request.url == (
            'https://nominatim.openstreetmap.org/search?key=KEY&viewbox=2.34%2C1.23%2C3.45%2C2.34&bounded=1&'
            'format=json&q=test&accept-language=en'
        )

        pub.site_options.set('options', 'geocoding_service_url', 'http://reverse.example.net/?param=value')
        with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
            pub.site_options.write(fd)
        rsps.get('http://reverse.example.net/', json=[{'lat': 0, 'lon': 0}])
        resp = get_app(pub).get('/api/geocoding?q=test')
        assert (
            rsps.calls[-1].request.url
            == 'http://reverse.example.net/?param=value&format=json&q=test&accept-language=en'
        )

        pub.site_options.set('options', 'geocoding_service_url', '{{passerelle_url}}/base-adresse/test')
        pub.site_options.add_section('variables')
        pub.site_options.set('variables', 'passerelle_url', 'https://passerelle')
        with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
            pub.site_options.write(fd)
        rsps.get('https://passerelle/base-adresse/test', json=[{'lat': 0, 'lon': 0}])
        resp = get_app(pub).get('/api/geocoding?q=test')
        assert (
            rsps.calls[-1].request.url
            == 'https://passerelle/base-adresse/test?format=json&q=test&accept-language=en'
        )


def test_afterjobs_status(pub):
    job = AfterJob('test')
    job.store()

    # missing signature
    get_app(pub).get('/api/jobs/%s/' % job.id, status=403)
    # unknown id
    resp = get_app(pub).get(sign_url('/api/jobs/not-a-job-id/?orig=coucou', '1234'), status=404)
    assert resp.json['err'] == 1
    # without trailing /
    resp = get_app(pub).get(sign_url('/api/jobs/%s?orig=coucou' % job.id, '1234'), status=404)

    resp = get_app(pub).get(sign_url('/api/jobs/%s/?orig=coucou' % job.id, '1234'), status=200)
    assert resp.json == {
        'err': 0,
        'data': {
            'label': 'test',
            'status': 'registered',
            'creation_time': job.creation_time.isoformat(),
            'completion_time': None,
            'completion_status': '',
        },
    }

    job.status = 'failed'
    job.failure_label = 'an error'
    job.store()
    resp = get_app(pub).get(sign_url('/api/jobs/%s/?orig=coucou' % job.id, '1234'), status=200)
    assert resp.json == {
        'err': 0,
        'data': {
            'label': 'test',
            'status': 'failed',
            'failure_label': 'an error',
            'creation_time': job.creation_time.isoformat(),
            'completion_time': None,
            'completion_status': '',
        },
    }


def test_afterjobs_base_directory(pub):
    # missing signature
    get_app(pub).get('/api/jobs/', status=403)
    # base directory is 404
    get_app(pub).get(sign_url('/api/jobs/?orig=coucou', '1234'), status=404)


def test_preview_payload_structure(pub, admin_user):
    get_app(pub).get('/api/preview-payload-structure', status=403)
    app = login(get_app(pub))
    resp = app.get('/api/preview-payload-structure')

    assert resp.pyquery('div.payload-preview').length == 1
    assert '<h2>Payload structure preview</h2>' in resp.text
    assert resp.pyquery('div.payload-preview').text() == '{}'
    params = {
        'request$post_data$added_elements': 1,
        'request$post_data$element1key': 'user/first_name',
        'request$post_data$element1value$value_template': 'Foo',
        'request$post_data$element2key': 'user/last_name',
        'request$post_data$element2value$value_template': 'Bar',
        'request$post_data$element3key': 'user/0',
    }
    resp = app.get('/api/preview-payload-structure', params=params)
    assert resp.pyquery('div.payload-preview div.errornotice').length == 0
    assert resp.pyquery('div.payload-preview').text() == '{"user": {"first_name": "Foo","last_name": "Bar"}}'
    params.update(
        {
            'request$post_data$element3value$value_template': 'value',
        }
    )
    resp = app.get('/api/preview-payload-structure', params=params)

    assert resp.pyquery('div.payload-preview div.errornotice').length == 1
    assert 'Unable to preview payload' in resp.pyquery('div.payload-preview div.errornotice').text()
    assert (
        'Following error occured: there is a mix between lists and dicts'
        in resp.pyquery('div.payload-preview div.errornotice').text()
    )

    params = {
        'post_data$element1key': '0/0',
        'post_data$element1value$value_template': 'Foo',
        'post_data$element2key': '0/1',
        'post_data$element2value$value_template': '{{ form_name }}',
        'post_data$element3key': '1/0',
        'post_data$element3value$value_template': '',
        'post_data$element10key': '1/1',
        'post_data$element10value$value_template': '10',
        'post_data$element100key': '1/2',
        'post_data$element100value$value_template': '100',
    }
    resp = app.get('/api/preview-payload-structure', params=params)
    assert resp.pyquery('div.payload-preview').text() == '[["Foo",{{ form_name }}],["","10","100"]]'
