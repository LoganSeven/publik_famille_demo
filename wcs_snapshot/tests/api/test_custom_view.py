import io
import os
import xml.etree.ElementTree as ET
import zipfile

import pytest

from wcs import fields
from wcs.carddef import CardDef
from wcs.formdef import FormDef
from wcs.qommon import ods
from wcs.qommon.http_request import HTTPRequest
from wcs.workflows import Workflow

from ..utilities import clean_temporary_pub, create_temporary_pub, get_app
from .utils import sign_uri


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


@pytest.fixture
def test_api_custom_view_access(pub, local_user):
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()
    local_user.roles = [role.id]
    local_user.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.fields = [fields.StringField(id='0', label='foobar', varname='foobar')]
    formdef.geolocations = {'base': 'Location'}
    formdef.store()

    carddef = CardDef()
    carddef.name = 'test'
    carddef.fields = [fields.StringField(id='0', label='foobar', varname='foo')]
    carddef.workflow_roles = {'_viewer': role.id}
    carddef.digest_templates = {'default': 'bla {{ form_var_foo }} xxx'}
    carddef.geolocations = {'base': 'Location'}
    carddef.store()

    pub.custom_view_class.wipe()
    custom_view = pub.custom_view_class()
    custom_view.title = 'shared formdef custom view'
    custom_view.formdef = formdef
    custom_view.columns = {'list': [{'id': '0'}]}
    custom_view.filters = {}
    custom_view.visibility = 'any'
    custom_view.store()

    custom_view = pub.custom_view_class()
    custom_view.title = 'private formdef custom view'
    custom_view.formdef = formdef
    custom_view.columns = {'list': [{'id': '0'}]}
    custom_view.filters = {}
    custom_view.visibility = 'owner'
    custom_view.user = local_user
    custom_view.store()

    custom_view = pub.custom_view_class()
    custom_view.title = 'shared carddef custom view'
    custom_view.formdef = carddef
    custom_view.columns = {'list': [{'id': '0'}]}
    custom_view.filters = {}
    custom_view.visibility = 'any'
    custom_view.store()

    custom_view = pub.custom_view_class()
    custom_view.title = 'private carddef custom view'
    custom_view.formdef = carddef
    custom_view.columns = {'list': [{'id': '0'}]}
    custom_view.filters = {}
    custom_view.visibility = 'owner'
    custom_view.user = local_user
    custom_view.store()

    custom_view = pub.custom_view_class()
    custom_view.title = 'datasource carddef custom view'
    custom_view.formdef = carddef
    custom_view.columns = {'list': [{'id': '0'}]}
    custom_view.filters = {}
    custom_view.visibility = 'datasource'
    custom_view.store()

    get_app(pub).get(sign_uri('/api/forms/test/list/shared-formdef-custom-view', user=local_user), status=200)
    get_app(pub).get(sign_uri('/api/forms/test/ods/shared-formdef-custom-view', user=local_user), status=200)
    get_app(pub).get(
        sign_uri('/api/forms/test/geojson/shared-formdef-custom-view', user=local_user), status=200
    )
    get_app(pub).get(
        sign_uri('/api/forms/test/list/private-formdef-custom-view', user=local_user), status=404
    )
    get_app(pub).get(sign_uri('/api/forms/test/ods/private-formdef-custom-view', user=local_user), status=404)
    get_app(pub).get(
        sign_uri('/api/forms/test/geojson/private-formdef-custom-view', user=local_user), status=404
    )

    get_app(pub).get(sign_uri('/api/cards/test/list/shared-carddef-custom-view', user=local_user), status=200)
    get_app(pub).get(sign_uri('/api/cards/test/ods/shared-carddef-custom-view', user=local_user), status=200)
    get_app(pub).get(
        sign_uri('/api/cards/test/geojson/shared-carddef-custom-view', user=local_user), status=200
    )
    get_app(pub).get(
        sign_uri('/api/cards/test/list/private-carddef-custom-view', user=local_user), status=404
    )
    get_app(pub).get(sign_uri('/api/cards/test/ods/private-carddef-custom-view', user=local_user), status=404)
    get_app(pub).get(
        sign_uri('/api/cards/test/geojson/private-carddef-custom-view', user=local_user), status=404
    )
    get_app(pub).get(
        sign_uri('/api/cards/test/list/datasource-carddef-custom-view', user=local_user), status=200
    )
    get_app(pub).get(
        sign_uri('/api/cards/test/ods/datasource-carddef-custom-view', user=local_user), status=200
    )
    get_app(pub).get(
        sign_uri('/api/cards/test/geojson/datasource-carddef-custom-view', user=local_user), status=200
    )


def test_api_list_formdata_custom_view(pub, local_user):
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.fields = [
        fields.StringField(id='0', label='foobar', varname='foobar'),
    ]
    formdef.store()

    data_class = formdef.data_class()
    data_class.wipe()

    for i in range(30):
        formdata = data_class()
        formdata.id = 400 + i
        formdata.data = {'0': 'FOO BAR %d' % i}
        formdata.user_id = local_user.id
        formdata.just_created()
        if i % 3 == 0:
            formdata.jump_status('new')
        else:
            formdata.jump_status('finished')
        formdata.store()

    assert data_class.get(404, ignore_errors=True) is not None

    # add proper role to user
    local_user.roles = [role.id]
    local_user.store()

    # check it now gets the data
    resp = get_app(pub).get(sign_uri('/api/forms/test/list', user=local_user))
    assert len(resp.json) == 30

    pub.custom_view_class.wipe()
    custom_view = pub.custom_view_class()
    custom_view.title = 'custom view'
    custom_view.formdef = formdef
    custom_view.columns = {'list': [{'id': '0'}]}
    custom_view.filters = {'filter': 'done', 'filter-status': 'on'}
    custom_view.visibility = 'any'
    custom_view.store()

    resp = get_app(pub).get(sign_uri('/api/forms/test/list/custom-view', user=local_user))
    assert len(resp.json['data']) == 20
    resp = get_app(pub).get(sign_uri('/api/forms/test/list/custom-view/', user=local_user))
    assert len(resp.json['data']) == 20

    resp = get_app(pub).get(sign_uri('/api/forms/test/custom-view/list', user=local_user))
    assert len(resp.json['data']) == 20
    resp = get_app(pub).get(sign_uri('/api/forms/test/custom-view/list/', user=local_user))
    assert len(resp.json['data']) == 20

    custom_view.filters.update({'filter-0': 'on', 'filter-0-value': 'FOO BAR 1'})
    custom_view.store()
    resp = get_app(pub).get(sign_uri('/api/forms/test/custom-view/list/', user=local_user))
    assert len(resp.json['data']) == 1

    custom_view.filters.update({'filter-0': 'on', 'filter-0-value': ''})
    custom_view.store()
    resp = get_app(pub).get(sign_uri('/api/forms/test/custom-view/list/', user=local_user))
    assert len(resp.json['data']) == 20  # empty criterias are ignored

    resp = get_app(pub).get(sign_uri('/api/forms/test/404/', user=local_user), status=200)
    assert resp.json['id'] == '404'
    get_app(pub).get(sign_uri('/api/forms/test/list/unknown/', user=local_user), status=404)
    get_app(pub).get(sign_uri('/api/forms/test/unknown/list/', user=local_user), status=404)


def test_api_ods_formdata_custom_view(pub, local_user):
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.fields = [
        fields.StringField(id='0', label='foobar', varname='foobar'),
    ]
    formdef.store()

    data_class = formdef.data_class()
    data_class.wipe()

    for i in range(30):
        formdata = data_class()
        formdata.data = {'0': 'FOO BAR %d' % i}
        formdata.user_id = local_user.id
        formdata.just_created()
        if i % 3 == 0:
            formdata.jump_status('new')
        else:
            formdata.jump_status('finished')
        formdata.store()

    # add proper role to user
    local_user.roles = [role.id]
    local_user.store()

    # check it now gets the data
    resp = get_app(pub).get(sign_uri('/api/forms/test/ods', user=local_user))
    with zipfile.ZipFile(io.BytesIO(resp.body)) as zipf:
        with zipf.open('content.xml') as fd:
            ods_sheet = ET.parse(fd)
    assert len(ods_sheet.findall('.//{%s}table-row' % ods.NS['table'])) == 11

    pub.custom_view_class.wipe()
    custom_view = pub.custom_view_class()
    custom_view.title = 'custom view'
    custom_view.formdef = formdef
    custom_view.columns = {'list': [{'id': '0'}]}
    custom_view.filters = {'filter': 'done', 'filter-status': 'on'}
    custom_view.visibility = 'any'
    custom_view.store()

    resp = get_app(pub).get(sign_uri('/api/forms/test/ods/custom-view', user=local_user))
    with zipfile.ZipFile(io.BytesIO(resp.body)) as zipf:
        with zipf.open('content.xml') as fd:
            ods_sheet = ET.parse(fd)
    assert len(ods_sheet.findall('.//{%s}table-row' % ods.NS['table'])) == 21


def test_api_geojson_formdata_custom_view(pub, local_user):
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.fields = [
        fields.StringField(id='0', label='foobar', varname='foobar'),
    ]
    formdef.geolocations = {'base': 'Location'}
    formdef.store()

    data_class = formdef.data_class()
    data_class.wipe()

    for i in range(30):
        formdata = data_class()
        formdata.data = {'0': 'FOO BAR %d' % i}
        formdata.geolocations = {'base': {'lat': 48, 'lon': 2}}
        formdata.user_id = local_user.id
        formdata.just_created()
        if i % 3 == 0:
            formdata.jump_status('new')
        else:
            formdata.jump_status('finished')
        formdata.store()

    # add proper role to user
    local_user.roles = [role.id]
    local_user.store()

    # check it now gets the data
    resp = get_app(pub).get(sign_uri('/api/forms/test/geojson', user=local_user))
    assert len(resp.json['features']) == 10

    pub.custom_view_class.wipe()
    custom_view = pub.custom_view_class()
    custom_view.title = 'custom view'
    custom_view.formdef = formdef
    custom_view.columns = {'list': [{'id': '0'}]}
    custom_view.filters = {'filter': 'done', 'filter-status': 'on'}
    custom_view.visibility = 'any'
    custom_view.store()

    resp = get_app(pub).get(sign_uri('/api/forms/test/geojson/custom-view', user=local_user))
    assert len(resp.json['features']) == 20


def test_api_get_formdata_custom_view(pub, local_user):
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.fields = [
        fields.StringField(id='0', label='foobar', varname='foobar'),
    ]
    formdef.store()

    data_class = formdef.data_class()
    data_class.wipe()

    for i in range(30):
        formdata = data_class()
        formdata.data = {'0': 'FOO BAR %d' % i}
        formdata.user_id = local_user.id
        formdata.just_created()
        formdata.store()
        if i % 3 == 0:
            formdata.jump_status('new')
            new_id = formdata.id
        else:
            formdata.jump_status('finished')
            finished_id = formdata.id
        formdata.store()

    # add proper role to user
    local_user.roles = [role.id]
    local_user.store()

    # create custom view, filter on "done"
    pub.custom_view_class.wipe()
    custom_view = pub.custom_view_class()
    custom_view.title = 'custom view'
    custom_view.formdef = formdef
    custom_view.columns = {'list': [{'id': '0'}]}
    custom_view.filters = {'filter': 'done', 'filter-status': 'on'}
    custom_view.visibility = 'any'
    custom_view.store()

    resp = get_app(pub).get(sign_uri('/api/forms/test/list', user=local_user))
    assert len(resp.json) == 30
    resp = get_app(pub).get(sign_uri('/api/forms/test/custom-view/list', user=local_user))
    assert len(resp.json['data']) == 20

    resp = get_app(pub).get(sign_uri('/api/forms/test/custom-view/%s' % finished_id, user=local_user))
    assert resp.json['fields']['foobar'] == 'FOO BAR 29'
    resp = get_app(pub).get(sign_uri('/api/forms/test/custom-view/%s/' % finished_id, user=local_user))
    assert resp.json['fields']['foobar'] == 'FOO BAR 29'

    get_app(pub).get(sign_uri('/api/forms/test/custom-view/%s' % new_id, user=local_user), status=404)
    get_app(pub).get(sign_uri('/api/forms/test/custom-view/%s/' % new_id, user=local_user), status=404)

    # check additional filters are applied
    get_app(pub).get(
        sign_uri('/api/forms/test/custom-view/%s?filter-foobar=FOO' % finished_id, user=local_user),
        status=404,
    )
    get_app(pub).get(
        sign_uri('/api/forms/test/custom-view/%s/?filter-foobar=FOO' % finished_id, user=local_user),
        status=404,
    )


def test_api_list_user_filter(pub, local_user):
    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'test'
    carddef.fields = [fields.StringField(id='0', label='foobar', varname='foobar')]
    carddef.store()

    data_class = carddef.data_class()
    data_class.wipe()

    carddata = data_class()
    carddata.data = {'0': 'FOO BAR 0'}
    carddata.user_id = local_user.id
    carddata.just_created()
    carddata.jump_status('new')
    carddata.store()

    # check data without custom view
    resp = get_app(pub).get(sign_uri('/api/cards/test/list'))
    assert len(resp.json['data']) == 1

    # check custom view
    pub.custom_view_class.wipe()
    custom_view = pub.custom_view_class()
    custom_view.title = 'custom view'
    custom_view.formdef = carddef
    custom_view.columns = {'list': [{'id': '0'}]}
    custom_view.filters = {'filter-user': 'on', 'filter-user-value': '__current__'}
    custom_view.visibility = 'any'
    custom_view.store()

    resp = get_app(pub).get(sign_uri('/api/cards/test/list/custom-view/'))
    assert len(resp.json['data']) == 0


def test_api_list_user_function_filter(pub, local_user):
    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'test'
    carddef.fields = [fields.StringField(id='0', label='foobar', varname='foobar')]
    carddef.store()

    data_class = carddef.data_class()
    data_class.wipe()

    carddata = data_class()
    carddata.data = {'0': 'FOO BAR 0'}
    carddata.user_id = local_user.id
    carddata.just_created()
    carddata.jump_status('new')
    carddata.store()

    # check data without custom view
    resp = get_app(pub).get(sign_uri('/api/cards/test/list'))
    assert len(resp.json['data']) == 1

    # check custom view
    pub.custom_view_class.wipe()
    custom_view = pub.custom_view_class()
    custom_view.title = 'custom view'
    custom_view.formdef = carddef
    custom_view.columns = {'list': [{'id': '0'}]}
    custom_view.filters = {'filter-user-function': 'on', 'filter-user-function-value': '_receiver'}
    custom_view.visibility = 'any'
    custom_view.store()

    resp = get_app(pub).get(sign_uri('/api/cards/test/list/custom-view/'))
    assert len(resp.json['data']) == 0


def test_api_list_status_filter_different(pub, local_user):
    CardDef.wipe()
    Workflow.wipe()

    workflow = CardDef.get_default_workflow()
    workflow.id = '2'
    workflow.add_status('Foo', 'foo')
    workflow.store()

    carddef = CardDef()
    carddef.name = 'test'
    carddef.fields = [fields.StringField(id='0', label='foobar', varname='foobar')]
    carddef.workflow = workflow
    carddef.store()

    data_class = carddef.data_class()
    data_class.wipe()

    carddata = data_class()
    carddata.data = {'0': 'FOO BAR 0'}
    carddata.user_id = local_user.id
    carddata.just_created()
    carddata.jump_status('new')
    carddata.store()

    carddata2 = data_class()
    carddata2.data = {'0': 'FOO BAR 0'}
    carddata2.user_id = local_user.id
    carddata2.just_created()
    carddata2.jump_status('foo')
    carddata2.store()

    # check custom view
    pub.custom_view_class.wipe()
    custom_view = pub.custom_view_class()
    custom_view.title = 'custom view'
    custom_view.formdef = carddef
    custom_view.columns = {'list': [{'id': '0'}]}
    custom_view.filters = {'filter': 'recorded', 'filter-status': 'on', 'filter-operator': 'ne'}
    custom_view.visibility = 'any'
    custom_view.store()

    resp = get_app(pub).get(sign_uri('/api/cards/test/list/custom-view/'))
    assert len(resp.json['data']) == 1
    assert resp.json['data'][0]['id'] == str(carddata2.id)

    resp = get_app(pub).get(sign_uri('/api/cards/test/custom-view/%s/' % carddata2.id))
    assert resp.json['id'] == str(carddata2.id)

    # check card that is not included in the custom view cannot be retrieved
    get_app(pub).get(sign_uri('/api/cards/test/custom-view/%s/' % carddata.id), status=404)
