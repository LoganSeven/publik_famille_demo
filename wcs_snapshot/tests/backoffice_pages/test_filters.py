import datetime
import json
import os
import time
import uuid

import pytest

from wcs import fields
from wcs.blocks import BlockDef
from wcs.carddef import CardDef
from wcs.data_sources import NamedDataSource
from wcs.formdef import FormDef
from wcs.qommon.http_request import HTTPRequest
from wcs.wf.criticality import MODE_INC
from wcs.workflows import Workflow, WorkflowBackofficeFieldsFormDef, WorkflowCriticalityLevel

from ..utilities import clean_temporary_pub, create_temporary_pub, get_app, login
from .test_all import create_environment, create_superuser


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
            '''
    [api-secrets]
    coucou = 1234
    '''
        )

    return pub


def teardown_module(module):
    clean_temporary_pub()


def test_backoffice_filter(pub):
    pub.user_class.wipe()
    create_superuser(pub)
    create_environment(pub)
    formdef = FormDef.get_by_urlname('form-title')
    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/')
    assert resp.forms['listing-settings']['filter-status'].checked is True
    resp.forms['listing-settings']['filter-status'].checked = False
    resp.forms['listing-settings']['filter-%s' % formdef.fields[1].id].checked = True
    resp = resp.forms['listing-settings'].submit().follow()
    assert '<select name="filter">' not in resp.text

    resp.forms['listing-settings']['filter-%s-value' % formdef.fields[1].id] = 'baz'
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('<td>baz</td>') == 8
    assert resp.text.count('<td>foo</td>') == 0
    assert resp.text.count('<td>bar</td>') == 0

    resp.forms['listing-settings']['filter-start'].checked = True
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('>You should enter a valid date.<') == 1  # only the one from <template>
    resp.forms['listing-settings']['filter-start-value'] = datetime.datetime(2015, 2, 1).strftime('%Y-%m-%d')
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('<td>baz</td>') == 0
    resp.forms['listing-settings']['filter-start-value'] = datetime.datetime(2014, 2, 1).strftime('%Y-%m-%d')
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('<td>baz</td>') == 8

    # check there's no crash on invalid date values
    resp.forms['listing-settings']['filter-start-value'] = 'whatever'
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('<td>baz</td>') == 8

    # check two-digit years are handled correctly
    resp.forms['listing-settings']['filter-start-value'] = datetime.datetime(2014, 2, 1).strftime('%y-%m-%d')
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('<td>baz</td>') == 8

    # and dates being typed in are properly ignored
    resp.forms['listing-settings']['filter-start-value'] = '0020-02-01'
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('<td>baz</td>') == 8

    # check it's also ok for end filter
    resp.forms['listing-settings']['filter-end'].checked = True
    resp = resp.forms['listing-settings'].submit().follow()
    resp.forms['listing-settings']['filter-end-value'] = datetime.datetime(2014, 2, 2).strftime('%y-%m-%d')
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('<td>baz</td>') == 0


def test_backoffice_status_filter(pub):
    pub.user_class.wipe()
    create_superuser(pub)
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form-title'
    formdef.fields = []
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.store()

    data_class = formdef.data_class()
    data_class.wipe()
    for i in range(10):
        formdata = data_class()
        formdata.data = {}
        formdata.just_created()
        if i % 3 == 0:
            formdata.jump_status('new')
        elif i % 3 == 1:
            formdata.jump_status('just_submitted')
        else:
            formdata.jump_status('finished')
        formdata.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/')
    assert resp.forms['listing-settings']['filter-status'].checked is True
    assert resp.forms['listing-settings']['filter'].value == 'waiting'
    assert resp.forms['listing-settings']['filter-operator'].value == 'eq'
    assert [x[0] for x in resp.forms['listing-settings']['filter-operator'].options] == [
        'eq',
        'ne',
        'in',
        'not_in',
    ]
    assert resp.text.count('<tr') == 5

    resp.forms['listing-settings']['filter-operator'].value = 'ne'
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('<tr') == 7

    # invalid operator
    resp = app.get('/backoffice/management/form-title/?filter-operator=xxx', status=400)
    assert 'Invalid operator "xxx" for "filter-operator"' in resp.pyquery('.pk-error').text()

    resp = app.get('/backoffice/management/form-title/?filter=done')
    assert resp.text.count('<tr') == 4
    resp = app.get('/backoffice/management/form-title/?filter=done&filter-operator=eq')
    assert resp.text.count('<tr') == 4
    resp = app.get('/backoffice/management/form-title/?filter=done&filter-operator=ne')
    assert resp.text.count('<tr') == 8
    resp = app.get('/backoffice/management/form-title/?filter=done&filter-operator=in')
    assert resp.text.count('<tr') == 4
    resp = app.get('/backoffice/management/form-title/?filter=done&filter-operator=not_in')
    assert resp.text.count('<tr') == 8
    resp = app.get('/backoffice/management/form-title/?filter=new|just_submitted&filter-operator=in')
    assert resp.text.count('<tr') == 8
    resp = app.get('/backoffice/management/form-title/?filter=all|just_submitted&filter-operator=in')
    assert resp.text.count('<tr') == 11
    resp = app.get('/backoffice/management/form-title/?filter=waiting|just_submitted&filter-operator=in')
    assert resp.text.count('<tr') == 1
    resp = app.get('/backoffice/management/form-title/?filter=new|just_submitted&filter-operator=not_in')
    assert resp.text.count('<tr') == 4
    resp = app.get('/backoffice/management/form-title/?filter=all|just_submitted&filter-operator=not_in')
    assert resp.text.count('<tr') == 1
    resp = app.get('/backoffice/management/form-title/?filter=waiting|just_submitted&filter-operator=not_in')
    assert resp.text.count('<tr') == 1
    resp = app.get('/backoffice/management/form-title/?filter=all')
    assert resp.text.count('<tr') == 11
    resp = app.get('/backoffice/management/form-title/?filter=waiting&filter-operator=eq')
    assert resp.text.count('<tr') == 5
    resp = app.get('/backoffice/management/form-title/?filter=waiting&filter-operator=ne')
    assert resp.text.count('<tr') == 7

    # check multi values are correctly displayed
    resp = app.get(
        '/backoffice/management/form-title/?filter=new|accepted&filter-operator=in&filter-status=on'
    )
    assert resp.pyquery('select[name="filter"]').attr['data-multi-values'] == 'new|accepted'
    assert resp.pyquery('select[name="filter-operator"]').val() == 'in'


def test_backoffice_default_filter(pub):
    pub.user_class.wipe()
    create_superuser(pub)
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form-title'
    formdef.fields = [
        fields.ItemField(
            id='2',
            label='2nd field',
            items=['foo', 'bar', 'baz'],
            display_locations=['validation', 'summary', 'listings'],
        ),
    ]
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/')
    assert 'filter-2-value' not in resp.forms['listing-settings'].fields

    formdef = FormDef.get_by_urlname('form-title')
    formdef.fields[0].in_filters = True
    formdef.store()
    resp = app.get('/backoffice/management/form-title/')
    assert 'filter-2-value' in resp.forms['listing-settings'].fields

    # same check for items field
    formdef.fields.append(fields.ItemsField(id='4', label='4th field', items=['foo', 'bar', 'baz']))
    formdef.store()
    resp = app.get('/backoffice/management/form-title/')
    assert 'filter-4-value' not in resp.forms['listing-settings'].fields

    formdef.fields[1].in_filters = True
    formdef.store()
    resp = app.get('/backoffice/management/form-title/')
    assert 'filter-4-value' in resp.forms['listing-settings'].fields


def test_backoffice_unknown_filter(pub):
    pub.user_class.wipe()
    create_superuser(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form-title'
    formdef.fields = []
    formdef.workflow_roles = {'_receiver': 1}
    formdef.store()

    data_class = formdef.data_class()
    data_class.wipe()
    for i in range(1):
        formdata = data_class()
        formdata.just_created()
        formdata.jump_status('new')
        formdata.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/')
    assert resp.text.count('<tr') == 2
    resp = app.get('/backoffice/management/form-title/?filter-foobar=42')
    assert resp.text.count('<tr') == 1
    assert 'Invalid filter &quot;foobar&quot;' in resp
    resp = app.get('/backoffice/management/form-title/?filter-42=on&filter-42-value=foobar')
    assert resp.text.count('<tr') == 1
    assert 'Invalid filter &quot;42&quot;' in resp

    resp = app.get('/backoffice/management/form-title/?filter-foobar=42&filter-baz=35')
    assert resp.text.count('<tr') == 1
    assert 'Invalid filters &quot;baz&quot;, &quot;foobar&quot;' in resp
    resp = app.get('/backoffice/management/form-title/?filter-42=on&filter-42-value=foobar&filter-baz=35')
    assert resp.text.count('<tr') == 1
    assert 'Invalid filters &quot;42&quot;, &quot;baz&quot;' in resp


def test_backoffice_bool_filter(pub):
    pub.user_class.wipe()
    create_superuser(pub)
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form-title'
    formdef.fields = [
        fields.BoolField(
            id=str(uuid.uuid4()), label='4th field', display_locations=['validation', 'summary', 'listings']
        )
    ]
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.store()
    field = formdef.fields[0]

    data_class = formdef.data_class()
    data_class.wipe()

    for i in range(0, 2):
        formdata = data_class()
        formdata.data = {field.id: bool(i % 2)}
        formdata.just_created()
        formdata.jump_status('new')
        formdata.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/')
    resp.forms['listing-settings']['filter-%s' % field.id].checked = True
    resp = resp.forms['listing-settings'].submit().follow()

    assert resp.forms['listing-settings']['filter-%s-value' % field.id].value == ''
    assert resp.forms['listing-settings']['filter-%s-operator' % field.id].value == 'eq'
    assert [x[0] for x in resp.forms['listing-settings']['filter-%s-operator' % field.id].options] == [
        'eq',
        'ne',
        'absent',
        'existing',
    ]

    resp.forms['listing-settings']['filter-%s-value' % field.id].value = 'true'
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('<td>Yes</td>') > 0
    assert resp.text.count('<td>No</td>') == 0

    resp.forms['listing-settings']['filter-%s-value' % field.id].value = 'false'
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('<td>Yes</td>') == 0
    assert resp.text.count('<td>No</td>') > 0

    resp.forms['listing-settings']['filter-%s-value' % field.id].value = 'false'
    resp.forms['listing-settings']['filter-%s-operator' % field.id].value = 'ne'
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('<td>Yes</td>') > 0
    assert resp.text.count('<td>No</td>') == 0


def test_backoffice_item_filter(pub):
    pub.user_class.wipe()
    create_superuser(pub)
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    data_source = {
        'type': 'jsonvalue',
        'value': json.dumps([{'id': x, 'text': x} for x in ['â', 'b', 'c', 'd']]),
    }

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form-title'
    formdef.fields = [
        fields.ItemField(
            id='4',
            label='4th field',
            data_source=data_source,
            display_locations=['validation', 'summary', 'listings'],
        )
    ]
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.store()

    data_class = formdef.data_class()
    data_class.wipe()

    for i in range(0, 12):
        formdata = data_class()
        formdata.data = {}
        if i % 4 == 0:
            formdata.data['4'] = 'â'
            formdata.data['4_display'] = 'â'
        elif i % 4 == 1:
            formdata.data['4'] = 'b'
            formdata.data['4_display'] = 'b'
        elif i % 4 == 2:
            formdata.data['4'] = 'd'
            formdata.data['4_display'] = 'd'
        else:
            formdata.data['4'] = ''
            formdata.data['4_display'] = ''
        formdata.just_created()
        if i % 3 == 0:
            formdata.jump_status('new')
        else:
            formdata.jump_status('finished')
        formdata.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/')
    resp.forms['listing-settings']['filter-4'].checked = True
    resp = resp.forms['listing-settings'].submit().follow()

    assert resp.forms['listing-settings']['filter-4-value'].value == ''
    assert resp.forms['listing-settings']['filter-4-operator'].value == 'eq'
    assert [x[0] for x in resp.forms['listing-settings']['filter-4-operator'].options] == [
        'eq',
        'ne',
        'lt',
        'lte',
        'gt',
        'gte',
        'between',
        'in',
        'not_in',
        'absent',
        'existing',
    ]

    resp.forms['listing-settings']['filter-4-value'].value = 'â'
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('<td>â</td>') > 0
    assert resp.text.count('<td>b</td>') == 0
    assert resp.text.count('<td>d</td>') == 0

    resp.forms['listing-settings']['filter-4-value'].value = 'b'
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('<td>â</td>') == 0
    assert resp.text.count('<td>b</td>') > 0
    assert resp.text.count('<td>d</td>') == 0

    resp.forms['listing-settings']['filter-4-value'].value = 'b'
    resp.forms['listing-settings']['filter-4-operator'].value = 'ne'
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('<td>â</td>') > 0
    assert resp.text.count('<td>b</td>') == 0
    assert resp.text.count('<td>d</td>') > 0

    # in postgresql, option 'c' is never used so not even listed
    with pytest.raises(ValueError):
        resp.forms['listing-settings']['filter-4-value'].value = 'c'

    # check json view used to fill select filters from javascript
    resp2 = app.get(resp.request.path + 'filter-options?filter_field_id=4&' + resp.request.query_string)
    assert [x['id'] for x in resp2.json['data']] == ['â', 'b', 'd']
    resp2 = app.get(
        resp.request.path + 'filter-options?filter_field_id=4&_search=d&' + resp.request.query_string
    )
    assert [x['id'] for x in resp2.json['data']] == ['d']
    resp2 = app.get(
        resp.request.path + 'filter-options?filter_field_id=7&' + resp.request.query_string, status=404
    )

    for status in ('all', 'waiting', 'pending', 'done', 'accepted'):
        resp.forms['listing-settings']['filter'] = status
        resp = resp.forms['listing-settings'].submit().follow()
        resp2 = app.get(resp.request.path + 'filter-options?filter_field_id=4&' + resp.request.query_string)
        if status == 'accepted':
            assert [x['id'] for x in resp2.json['data']] == []
        else:
            assert [x['id'] for x in resp2.json['data']] == ['â', 'b', 'd']

    # item field in autocomplete mode, check that label is displayed in option
    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'foo'
    carddef.fields = [
        fields.StringField(id='1', label='Test', type='string', varname='foo'),
    ]
    carddef.digest_templates = {'default': 'card {{ form_var_foo }}'}
    carddef.store()

    card_ids = {}
    for label in ('foo', 'bar', 'baz', 'foo, bar'):
        card = carddef.data_class()()
        card.data = {'1': label}
        card.just_created()
        card.store()
        card_ids[label] = str(card.id)
    formdef.fields[0].display_mode = 'autocomplete'
    formdef.fields[0].data_source = {'type': 'carddef:foo', 'value': ''}
    formdef.store()

    for i, formdata in enumerate(formdef.data_class().select()):
        if i % 2:
            formdata.data['4'] = card_ids['bar']
            formdata.data['4_display'] = 'card bar'
        else:
            formdata.data['4'] = card_ids['baz']
            formdata.data['4_display'] = 'card baz'
        formdata.store()

    resp.forms['listing-settings']['filter-4-value'].force_value(card_ids['baz'])
    resp = resp.forms['listing-settings'].submit().follow()
    assert [x[2] for x in resp.forms['listing-settings']['filter-4-value'].options] == ['card baz']

    # between operator
    resp.forms['listing-settings']['filter'].value = 'all'
    resp.forms['listing-settings']['filter-4-operator'].value = 'between'
    for value in [
        card_ids['baz'],
        '%s|%s|%s' % (card_ids['baz'], card_ids['bar'], card_ids['foo']),
        '|',
        '%s|' % card_ids['baz'],
        '|%s' % card_ids['baz'],
    ]:
        resp.forms['listing-settings']['filter-4-value'].force_value(value)
        resp = resp.forms['listing-settings'].submit().follow()
        assert '<li class="warning">Invalid value' not in resp
        assert resp.text.count('data-link') == 0
        # check possible text values are made available to javascript, it is limited to
        # values actually used in the forms
        item_values = [x for x in value.split('|') if x in (card_ids['bar'], card_ids['baz'])]
        expected_dict = {str(x): carddef.data_class().get(x).default_digest for x in item_values}
        assert json.loads(resp.pyquery('#filter-options-filter-4-value').text()) == expected_dict


def test_backoffice_item_double_filter(pub):
    pub.user_class.wipe()
    create_superuser(pub)
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    data_source = {
        'type': 'jsonvalue',
        'value': json.dumps([{'id': x, 'text': x} for x in ['â', 'b', 'c', 'd']]),
    }
    data_source2 = {
        'type': 'jsonvalue',
        'value': json.dumps([{'id': x, 'text': x} for x in ['E', 'F', 'G', 'H']]),
    }

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form-title'
    formdef.fields = [
        fields.ItemField(
            id='4',
            label='4th field',
            data_source=data_source,
            display_locations=['validation', 'summary', 'listings'],
        ),
        fields.ItemField(
            id='5',
            label='5th field',
            data_source=data_source2,
            display_locations=['validation', 'summary', 'listings'],
        ),
    ]
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.store()

    data_class = formdef.data_class()
    data_class.wipe()

    for i in range(0, 4):
        formdata = data_class()
        formdata.data = {}
        if i % 4 == 0:
            formdata.data['4'] = 'a'
            formdata.data['4_display'] = 'a'
            formdata.data['5'] = 'E'
            formdata.data['5_display'] = 'E'
        elif i % 4 == 1:
            formdata.data['4'] = 'a'
            formdata.data['4_display'] = 'a'
            formdata.data['5'] = 'F'
            formdata.data['5_display'] = 'F'
        elif i % 4 == 2:
            formdata.data['4'] = 'a'
            formdata.data['4_display'] = 'a'
            formdata.data['5'] = 'G'
            formdata.data['5_display'] = 'G'
        elif i % 4 == 3:
            formdata.data['4'] = 'b'
            formdata.data['4_display'] = 'b'
            formdata.data['5'] = 'F'
            formdata.data['5_display'] = 'F'
        formdata.just_created()
        formdata.jump_status('new')
        formdata.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/')
    resp.forms['listing-settings']['filter-4'].checked = True
    resp.forms['listing-settings']['filter-5'].checked = True
    resp = resp.forms['listing-settings'].submit().follow()

    assert resp.forms['listing-settings']['filter-4-value'].value == ''
    assert resp.forms['listing-settings']['filter-5-value'].value == ''
    assert [x[0] for x in resp.forms['listing-settings']['filter-4-value'].options] == ['', 'a', 'b']
    assert [x[0] for x in resp.forms['listing-settings']['filter-5-value'].options] == ['', 'E', 'F', 'G']

    resp.forms['listing-settings']['filter-4-value'].value = 'a'
    resp = resp.forms['listing-settings'].submit().follow()
    assert [x[0] for x in resp.forms['listing-settings']['filter-4-value'].options] == ['', 'a', 'b']
    assert [x[0] for x in resp.forms['listing-settings']['filter-5-value'].options] == ['', 'E', 'F', 'G']

    resp.forms['listing-settings']['filter-4-value'].value = 'b'
    resp = resp.forms['listing-settings'].submit().follow()
    assert [x[0] for x in resp.forms['listing-settings']['filter-4-value'].options] == ['', 'a', 'b']
    assert [x[0] for x in resp.forms['listing-settings']['filter-5-value'].options] == ['', 'F']

    resp.forms['listing-settings']['filter-5-value'].value = 'F'
    resp = resp.forms['listing-settings'].submit().follow()
    assert [x[0] for x in resp.forms['listing-settings']['filter-4-value'].options] == ['', 'a', 'b']
    assert [x[0] for x in resp.forms['listing-settings']['filter-5-value'].options] == ['', 'F']

    resp.forms['listing-settings']['filter-4-value'].value = ''
    resp = resp.forms['listing-settings'].submit().follow()
    assert [x[0] for x in resp.forms['listing-settings']['filter-4-value'].options] == ['', 'a', 'b']
    assert [x[0] for x in resp.forms['listing-settings']['filter-5-value'].options] == ['', 'E', 'F', 'G']


def test_backoffice_bofield_item_filter(pub):
    pub.user_class.wipe()
    create_superuser(pub)
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    data_source = {
        'type': 'jsonvalue',
        'value': json.dumps([{'id': x, 'text': x} for x in ['â', 'b', 'c', 'd']]),
    }

    workflow = Workflow.get_default_workflow()
    workflow.id = '2'
    workflow.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(workflow)
    workflow.backoffice_fields_formdef.fields = [
        fields.ItemField(
            id='bo0-1',
            label='4th field',
            data_source=data_source,
            display_locations=['validation', 'summary', 'listings'],
        )
    ]
    workflow.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form-title'
    formdef.fields = []
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.workflow_id = workflow.id
    formdef.store()

    data_class = formdef.data_class()
    data_class.wipe()

    for i in range(0, 12):
        formdata = data_class()
        formdata.data = {}
        if i % 4 == 0:
            formdata.data['bo0-1'] = 'â'
            formdata.data['bo0-1_display'] = 'â'
        elif i % 4 == 1:
            formdata.data['bo0-1'] = 'b'
            formdata.data['bo0-1_display'] = 'b'
        elif i % 4 == 2:
            formdata.data['bo0-1'] = 'd'
            formdata.data['bo0-1_display'] = 'd'
        formdata.just_created()
        if i % 3 == 0:
            formdata.jump_status('new')
        else:
            formdata.jump_status('finished')
        formdata.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/')
    resp.forms['listing-settings']['filter-bo0-1'].checked = True
    resp = resp.forms['listing-settings'].submit().follow()

    assert resp.forms['listing-settings']['filter-bo0-1-value'].value == ''
    assert resp.forms['listing-settings']['filter-bo0-1-operator'].value == 'eq'
    assert [x[0] for x in resp.forms['listing-settings']['filter-bo0-1-operator'].options] == [
        'eq',
        'ne',
        'lt',
        'lte',
        'gt',
        'gte',
        'between',
        'in',
        'not_in',
        'absent',
        'existing',
    ]

    resp.forms['listing-settings']['filter-bo0-1-value'].value = 'â'
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('<td>â</td>') > 0
    assert resp.text.count('<td>b</td>') == 0
    assert resp.text.count('<td>d</td>') == 0

    resp.forms['listing-settings']['filter-bo0-1-value'].value = 'b'
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('<td>â</td>') == 0
    assert resp.text.count('<td>b</td>') > 0
    assert resp.text.count('<td>d</td>') == 0

    resp.forms['listing-settings']['filter-bo0-1-value'].value = 'b'
    resp.forms['listing-settings']['filter-bo0-1-operator'].value = 'ne'
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('<td>â</td>') > 0
    assert resp.text.count('<td>b</td>') == 0
    assert resp.text.count('<td>d</td>') > 0

    # in postgresql, option 'c' is never used so not even listed
    with pytest.raises(ValueError):
        resp.forms['listing-settings']['filter-bo0-1-value'].value = 'c'

    # check json view used to fill select filters from javascript
    resp2 = app.get(resp.request.path + 'filter-options?filter_field_id=bo0-1&' + resp.request.query_string)
    assert [x['id'] for x in resp2.json['data']] == ['â', 'b', 'd']
    resp2 = app.get(
        resp.request.path + 'filter-options?filter_field_id=bo0-1&_search=d&' + resp.request.query_string
    )
    assert [x['id'] for x in resp2.json['data']] == ['d']

    for status in ('all', 'waiting', 'pending', 'done', 'accepted'):
        resp.forms['listing-settings']['filter'] = status
        resp = resp.forms['listing-settings'].submit().follow()
        resp2 = app.get(
            resp.request.path + 'filter-options?filter_field_id=bo0-1&' + resp.request.query_string
        )
        if status == 'accepted':
            assert [x['id'] for x in resp2.json['data']] == []
        else:
            assert [x['id'] for x in resp2.json['data']] == ['â', 'b', 'd']


def test_backoffice_items_filter(pub):
    pub.user_class.wipe()
    create_superuser(pub)
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    data_source = {
        'type': 'jsonvalue',
        'value': json.dumps([{'id': x, 'text': x} for x in ['â', 'b', 'c', 'd']]),
    }

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form-title'
    formdef.fields = [
        fields.ItemsField(
            id='4',
            label='4th field',
            data_source=data_source,
            display_locations=['validation', 'summary', 'listings'],
        )
    ]
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.store()

    data_class = formdef.data_class()
    data_class.wipe()

    for i in range(0, 4):
        formdata = data_class()
        formdata.data = {}
        if i % 4 == 0:
            formdata.data['4'] = ['â', 'b']
            formdata.data['4_display'] = 'â, b'
        elif i % 4 == 1:
            formdata.data['4'] = ['b', 'd']
            formdata.data['4_display'] = 'b, d'
        elif i % 4 == 2:
            formdata.data['4'] = ['â']
            formdata.data['4_display'] = 'â'
        else:
            formdata.data['4'] = []
            formdata.data['4_display'] = None
        formdata.just_created()
        formdata.jump_status('new')
        formdata.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/')
    resp.forms['listing-settings']['filter-4'].checked = True
    resp = resp.forms['listing-settings'].submit().follow()

    assert resp.forms['listing-settings']['filter-4-value'].value == ''
    assert resp.forms['listing-settings']['filter-4-operator'].value == 'eq'
    assert [x[0] for x in resp.forms['listing-settings']['filter-4-operator'].options] == [
        'eq',
        'ne',
        'lt',
        'lte',
        'gt',
        'gte',
        'between',
        'in',
        'not_in',
        'absent',
        'existing',
    ]

    assert [x[2] for x in resp.forms['listing-settings']['filter-4-value'].options] == ['', 'â', 'b', 'd']
    resp.forms['listing-settings']['filter-4-value'].value = 'â'
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('<td>â, b</td>') > 0
    assert resp.text.count('<td>â</td>') > 0
    assert resp.text.count('<td>b, d</td>') == 0

    resp.forms['listing-settings']['filter-4-value'].value = 'b'
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('<td>â, b</td>') > 0
    assert resp.text.count('<td>â</td>') == 0
    assert resp.text.count('<td>b, d</td>') > 0

    resp.forms['listing-settings']['filter-4-value'].value = 'b'
    resp.forms['listing-settings']['filter-4-operator'].value = 'ne'
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('<td>â, b</td>') == 0
    assert resp.text.count('<td>â</td>') > 0
    assert resp.text.count('<td>b, d</td>') == 0

    # option 'c' is never used so not even listed
    with pytest.raises(ValueError):
        resp.forms['listing-settings']['filter-4-value'].value = 'c'


def test_backoffice_item_cards_filter(pub):
    pub.user_class.wipe()
    create_superuser(pub)
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'foo'
    carddef.fields = [
        fields.StringField(id='1', label='Test', varname='foo'),
    ]
    carddef.digest_templates = {'default': 'card {{ form_var_foo }}'}
    carddef.store()

    card_ids = {}
    for label in ('foo', 'bar', 'baz'):
        card = carddef.data_class()()
        card.data = {'1': label}
        card.just_created()
        card.store()
        card_ids[label] = str(card.id)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form-title'
    formdef.fields = [
        fields.ItemField(
            id='1',
            label='item',
            data_source={'type': 'carddef:foo', 'value': ''},
        )
    ]
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.store()

    data_class = formdef.data_class()
    data_class.wipe()

    formdata = data_class()
    formdata.data = {'1': card_ids['foo']}
    formdata.data['1_display'] = formdef.fields[0].store_display_value(formdata.data, '1')
    formdata.data['1_structured'] = formdef.fields[0].store_structured_value(formdata.data, '1')
    formdata.just_created()
    formdata.jump_status('new')
    formdata.store()

    formdata = data_class()
    formdata.data = {'1': card_ids['baz']}
    formdata.data['1_display'] = formdef.fields[0].store_display_value(formdata.data, '1')
    formdata.data['1_structured'] = formdef.fields[0].store_structured_value(formdata.data, '1')
    formdata.just_created()
    formdata.jump_status('new')
    formdata.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/%s/' % formdef.url_name)
    assert resp.pyquery('tbody tr').length == 2
    resp.forms['listing-settings']['filter-1'].checked = True
    resp = resp.forms['listing-settings'].submit().follow()

    # option 'bar' is never used so not even listed
    assert [x[2] for x in resp.forms['listing-settings']['filter-1-value'].options] == [
        '',
        'card baz',
        'card foo',
    ]

    resp.forms['listing-settings']['filter-1-value'].value = card_ids['foo']
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.pyquery('tbody tr').length == 1

    resp.forms['listing-settings']['filter-1-value'].value = card_ids['baz']
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.pyquery('tbody tr').length == 1

    # check a change to a card display label is applied to filter options
    card = carddef.data_class().get(card_ids['foo'])
    card.data = {'1': 'Foo'}
    card.store()

    resp = app.get('/backoffice/management/%s/' % formdef.url_name)
    resp.forms['listing-settings']['filter-1'].checked = True
    resp = resp.forms['listing-settings'].submit().follow()
    assert [x[2] for x in resp.forms['listing-settings']['filter-1-value'].options] == [
        '',
        'card baz',
        'card Foo',
    ]


def test_backoffice_item_cards_custom_id_filter(pub):
    pub.user_class.wipe()
    create_superuser(pub)
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'foo'
    carddef.fields = [
        fields.StringField(id='1', label='Test', varname='foo'),
    ]
    carddef.digest_templates = {'default': 'card {{ form_var_foo }}'}
    carddef.id_template = 'card-{{ form_var_foo }}'
    carddef.store()

    card_ids = {}
    for label in ('foo', 'bar', 'baz'):
        card = carddef.data_class()()
        card.data = {'1': label}
        card.just_created()
        card.store()
        card_ids[label] = str(card.id)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form-title'
    formdef.fields = [
        fields.ItemField(
            id='1',
            label='item',
            data_source={'type': 'carddef:foo', 'value': ''},
        )
    ]
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.store()

    data_class = formdef.data_class()
    data_class.wipe()

    formdata = data_class()
    formdata.data = {'1': 'card-foo'}
    formdata.data['1_display'] = formdef.fields[0].store_display_value(formdata.data, '1')
    formdata.data['1_structured'] = formdef.fields[0].store_structured_value(formdata.data, '1')
    formdata.just_created()
    formdata.jump_status('new')
    formdata.store()

    formdata = data_class()
    formdata.data = {'1': 'card-baz'}
    formdata.data['1_display'] = formdef.fields[0].store_display_value(formdata.data, '1')
    formdata.data['1_structured'] = formdef.fields[0].store_structured_value(formdata.data, '1')
    formdata.just_created()
    formdata.jump_status('new')
    formdata.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/%s/' % formdef.url_name)
    assert resp.pyquery('tbody tr').length == 2
    resp.forms['listing-settings']['filter-1'].checked = True
    resp = resp.forms['listing-settings'].submit().follow()

    assert [(x[0], x[2]) for x in resp.forms['listing-settings']['filter-1-value'].options] == [
        ('', ''),
        ('card-baz', 'card baz'),
        ('card-foo', 'card foo'),
    ]

    resp.forms['listing-settings']['filter-1-value'].value = 'card-foo'
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.pyquery('tbody tr').length == 1

    resp.forms['listing-settings']['filter-1-value'].value = 'card-baz'
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.pyquery('tbody tr').length == 1


def test_backoffice_items_cards_filter(pub):
    pub.user_class.wipe()
    create_superuser(pub)
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'foo'
    carddef.fields = [
        fields.StringField(id='1', label='Test', varname='foo'),
    ]
    carddef.digest_templates = {'default': 'card {{ form_var_foo }}'}
    carddef.store()

    card_ids = {}
    for label in ('foo', 'bar', 'baz', 'foo, bar'):
        card = carddef.data_class()()
        card.data = {'1': label}
        card.just_created()
        card.store()
        card_ids[label] = str(card.id)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form-title'
    formdef.fields = [
        fields.ItemsField(
            id='1',
            label='items',
            data_source={'type': 'carddef:foo', 'value': ''},
        )
    ]
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.store()

    data_class = formdef.data_class()
    data_class.wipe()

    formdata = data_class()
    formdata.data = {'1': [card_ids['foo']]}
    formdata.data['1_display'] = formdef.fields[0].store_display_value(formdata.data, '1')
    formdata.data['1_structured'] = formdef.fields[0].store_structured_value(formdata.data, '1')
    formdata.just_created()
    formdata.jump_status('new')
    formdata.store()

    formdata = data_class()
    formdata.data = {'1': [card_ids['foo'], card_ids['baz']]}
    formdata.data['1_display'] = formdef.fields[0].store_display_value(formdata.data, '1')
    formdata.data['1_structured'] = formdef.fields[0].store_structured_value(formdata.data, '1')
    formdata.just_created()
    formdata.jump_status('new')
    formdata.store()

    formdata = data_class()
    formdata.data = {'1': [card_ids['foo, bar']]}
    formdata.data['1_display'] = formdef.fields[0].store_display_value(formdata.data, '1')
    formdata.data['1_structured'] = formdef.fields[0].store_structured_value(formdata.data, '1')
    formdata.just_created()
    formdata.jump_status('new')
    formdata.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/%s/' % formdef.url_name)
    assert resp.pyquery('tbody tr').length == 3
    resp.forms['listing-settings']['filter-1'].checked = True
    resp = resp.forms['listing-settings'].submit().follow()

    # option 'bar' is never used so not even listed
    assert [x[2] for x in resp.forms['listing-settings']['filter-1-value'].options] == [
        '',
        'card baz',
        'card foo',
        'card foo, bar',
    ]

    resp.forms['listing-settings']['filter-1-value'].value = card_ids['foo']
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.pyquery('tbody tr').length == 2

    resp.forms['listing-settings']['filter-1-value'].value = card_ids['baz']
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.pyquery('tbody tr').length == 1


def test_backoffice_string_filter(pub):
    pub.user_class.wipe()
    create_superuser(pub)
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form-title'
    formdef.fields = [
        fields.StringField(
            id='4', label='4th field', display_locations=['validation', 'summary', 'listings']
        ),
        fields.StringField(
            id='5', label='5th field', display_locations=['validation', 'summary', 'listings']
        ),
    ]
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.store()

    data_class = formdef.data_class()
    data_class.wipe()

    for i in range(0, 2):
        formdata = data_class()
        formdata.data = {}
        formdata.data['4'] = 'a' if bool(i % 2) else 'b'
        formdata.data['5'] = '[a]' if bool(i % 2) else '[b]'
        formdata.just_created()
        formdata.jump_status('new')
        formdata.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/')
    resp.forms['listing-settings']['filter-4'].checked = True
    resp = resp.forms['listing-settings'].submit().follow()

    assert resp.forms['listing-settings']['filter-4-value'].value == ''
    assert resp.forms['listing-settings']['filter-4-operator'].value == 'eq'
    assert [x[0] for x in resp.forms['listing-settings']['filter-4-operator'].options] == [
        'eq',
        'ne',
        'lt',
        'lte',
        'gt',
        'gte',
        'between',
        'in',
        'not_in',
        'absent',
        'existing',
        'icontains',
        'ieq',
    ]

    resp.forms['listing-settings']['filter-4-value'].value = 'a'
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('<td>a</td>') > 0
    assert resp.text.count('<td>b</td>') == 0

    resp.forms['listing-settings']['filter-4-value'].value = 'b'
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('<td>a</td>') == 0
    assert resp.text.count('<td>b</td>') > 0

    resp.forms['listing-settings']['filter-4-value'].value = 'b'
    resp.forms['listing-settings']['filter-4-operator'].value = 'ne'
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('<td>a</td>') > 0
    assert resp.text.count('<td>b</td>') == 0

    resp.forms['listing-settings']['filter-4-value'].value = 'B'
    resp.forms['listing-settings']['filter-4-operator'].value = 'ieq'
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('<td>a</td>') == 0
    assert resp.text.count('<td>b</td>') > 0

    # filter on something looking like an ezt template
    resp.forms['listing-settings']['filter-4'].checked = False
    resp.forms['listing-settings']['filter-5'].checked = True
    resp = resp.forms['listing-settings'].submit().follow()
    resp.forms['listing-settings']['filter-5-value'].value = '[b]'
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('<td>[a]</td>') == 0
    assert resp.text.count('<td>[b]</td>') > 0


def test_backoffice_string_filter_int_value(pub):
    pub.user_class.wipe()
    create_superuser(pub)
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form-title'
    formdef.fields = [
        fields.StringField(id='4', label='4th field', display_locations=['validation', 'summary', 'listings'])
    ]
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.store()

    data_class = formdef.data_class()
    data_class.wipe()

    for i in range(0, 2):
        formdata = data_class()
        formdata.data = {}
        formdata.data['4'] = '123' if bool(i % 2) else '315610000204'
        formdata.just_created()
        formdata.jump_status('new')
        formdata.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/')
    resp.forms['listing-settings']['filter-4'].checked = True
    resp = resp.forms['listing-settings'].submit().follow()

    assert resp.forms['listing-settings']['filter-4-value'].value == ''

    resp.forms['listing-settings']['filter-4-value'].value = '123'
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('<td>123</td>') > 0
    assert resp.text.count('<td>315610000204</td>') == 0

    resp.forms['listing-settings']['filter-4-value'].value = '315610000204'
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('<td>123</td>') == 0
    assert resp.text.count('<td>315610000204</td>') > 0


def test_backoffice_text_filter(pub):
    pub.user_class.wipe()
    create_superuser(pub)
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form-title'
    formdef.fields = [
        fields.TextField(id='4', label='4th field', display_locations=['validation', 'summary', 'listings']),
    ]
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.store()

    data_class = formdef.data_class()
    data_class.wipe()

    for i in range(0, 2):
        formdata = data_class()
        formdata.data = {}
        formdata.data['4'] = 'a' if bool(i % 2) else 'b'
        formdata.just_created()
        formdata.jump_status('new')
        formdata.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/')
    resp.forms['listing-settings']['filter-4'].checked = True
    resp = resp.forms['listing-settings'].submit().follow()

    assert resp.forms['listing-settings']['filter-4-value'].value == ''
    assert resp.forms['listing-settings']['filter-4-operator'].value == 'eq'
    assert [x[0] for x in resp.forms['listing-settings']['filter-4-operator'].options] == [
        'eq',
        'ne',
        'lt',
        'lte',
        'gt',
        'gte',
        'between',
        'in',
        'not_in',
        'absent',
        'existing',
        'icontains',
        'ieq',
    ]

    resp.forms['listing-settings']['filter-4-value'].value = 'a'
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('<td>a</td>') > 0
    assert resp.text.count('<td>b</td>') == 0

    resp.forms['listing-settings']['filter-4-value'].value = 'b'
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('<td>a</td>') == 0
    assert resp.text.count('<td>b</td>') > 0

    resp.forms['listing-settings']['filter-4-value'].value = 'b'
    resp.forms['listing-settings']['filter-4-operator'].value = 'ne'
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('<td>a</td>') > 0
    assert resp.text.count('<td>b</td>') == 0


def test_backoffice_email_filter(pub):
    pub.user_class.wipe()
    create_superuser(pub)
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form-title'
    formdef.fields = [
        fields.EmailField(id='4', label='4th field', display_locations=['validation', 'summary', 'listings'])
    ]
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.store()

    data_class = formdef.data_class()
    data_class.wipe()

    for i in range(0, 2):
        formdata = data_class()
        formdata.data = {}
        formdata.data['4'] = 'a@localhost' if bool(i % 2) else 'b@localhost'
        formdata.just_created()
        formdata.jump_status('new')
        formdata.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/')
    resp.forms['listing-settings']['filter-4'].checked = True
    resp = resp.forms['listing-settings'].submit().follow()

    assert resp.forms['listing-settings']['filter-4-value'].value == ''
    assert resp.forms['listing-settings']['filter-4-operator'].value == 'eq'
    assert [x[0] for x in resp.forms['listing-settings']['filter-4-operator'].options] == [
        'eq',
        'ne',
        'in',
        'not_in',
        'absent',
        'existing',
        'icontains',
        'ieq',
    ]

    resp.forms['listing-settings']['filter-4-value'].value = 'a@localhost'
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('>a@localhost</') > 0
    assert resp.text.count('>b@localhost</') == 0

    resp.forms['listing-settings']['filter-4-value'].value = 'b@localhost'
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('>a@localhost</') == 0
    assert resp.text.count('>b@localhost</') > 0

    resp.forms['listing-settings']['filter-4-value'].value = 'b@localhost'
    resp.forms['listing-settings']['filter-4-operator'].value = 'ne'
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('>a@localhost</') > 0
    assert resp.text.count('>b@localhost</') == 0

    resp.forms['listing-settings']['filter-4-value'].value = 'a@local'
    resp.forms['listing-settings']['filter-4-operator'].value = 'icontains'
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('>a@localhost</') > 0
    assert resp.text.count('>b@localhost</') == 0


def test_backoffice_date_filter(pub, freezer):
    pub.user_class.wipe()
    create_superuser(pub)
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form-title'
    formdef.fields = [
        fields.DateField(id='4', label='4th field', display_locations=['validation', 'summary', 'listings'])
    ]
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.store()

    data_class = formdef.data_class()
    data_class.wipe()

    for i in range(0, 2):
        formdata = data_class()
        formdata.data = {}
        formdata.data['4'] = time.strptime('2020-04-24' if bool(i % 2) else '2015-05-12', '%Y-%m-%d')
        formdata.just_created()
        formdata.jump_status('new')
        formdata.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/')
    resp.forms['listing-settings']['filter-4'].checked = True
    resp = resp.forms['listing-settings'].submit().follow()

    assert resp.forms['listing-settings']['filter-4-value'].value == ''
    assert resp.forms['listing-settings']['filter-4-operator'].value == 'eq'
    assert [x[0] for x in resp.forms['listing-settings']['filter-4-operator'].options] == [
        'eq',
        'ne',
        'lt',
        'lte',
        'gt',
        'gte',
        'between',
        'in',
        'not_in',
        'absent',
        'existing',
        'is_today',
        'is_tomorrow',
        'is_yesterday',
        'is_this_week',
        'is_future',
        'is_past',
        'is_today_or_future',
        'is_today_or_past',
    ]

    resp.forms['listing-settings']['filter-4-value'].value = '2020-04-24'
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('<td>2020-04-24</td>') > 0
    assert resp.text.count('<td>2015-05-12</td>') == 0

    resp.forms['listing-settings']['filter-4-value'].value = '2015-05-12'
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('<td>2020-04-24</td>') == 0
    assert resp.text.count('<td>2015-05-12</td>') > 0

    resp.forms['listing-settings']['filter-4-value'].value = '2015-05-12'
    resp.forms['listing-settings']['filter-4-operator'].value = 'ne'
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('<td>2020-04-24</td>') > 0
    assert resp.text.count('<td>2015-05-12</td>') == 0

    # date in a different format
    resp.forms['listing-settings']['filter-4-value'].value = '12/05/2015'
    resp.forms['listing-settings']['filter-4-operator'].value = 'eq'
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('<td>2020-04-24</td>') == 0
    assert resp.text.count('<td>2015-05-12</td>') > 0

    # special date filters
    freezer.move_to(datetime.datetime(2020, 4, 25, 12, 0))
    resp = app.get('/backoffice/management/form-title/')
    resp.forms['listing-settings']['filter-4'].checked = True
    resp = resp.forms['listing-settings'].submit().follow()
    resp.forms['listing-settings']['filter-4-value'].value = 'on'
    resp.forms['listing-settings']['filter-4-operator'].value = 'is_today'
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('<td>2020-04-24</td>') == 0
    assert resp.text.count('<td>2015-05-12</td>') == 0

    resp.forms['listing-settings']['filter-4-operator'].value = 'is_yesterday'
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('<td>2020-04-24</td>') == 1
    assert resp.text.count('<td>2015-05-12</td>') == 0

    resp.forms['listing-settings']['filter-4-operator'].value = 'is_this_week'
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('<td>2020-04-24</td>') == 1
    assert resp.text.count('<td>2015-05-12</td>') == 0

    freezer.move_to(datetime.date(2020, 4, 24))
    resp.forms['listing-settings']['filter-4-operator'].value = 'is_today'
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('<td>2020-04-24</td>') == 1
    assert resp.text.count('<td>2015-05-12</td>') == 0

    freezer.move_to(datetime.date(2020, 4, 23))
    resp.forms['listing-settings']['filter-4-operator'].value = 'is_tomorrow'
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('<td>2020-04-24</td>') == 1
    assert resp.text.count('<td>2015-05-12</td>') == 0

    freezer.move_to(datetime.date(2020, 4, 23))
    resp.forms['listing-settings']['filter-4-operator'].value = 'is_future'
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('<td>2020-04-24</td>') == 1
    assert resp.text.count('<td>2015-05-12</td>') == 0

    freezer.move_to(datetime.date(2020, 4, 23))
    resp.forms['listing-settings']['filter-4-operator'].value = 'is_today_or_future'
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('<td>2020-04-24</td>') == 1
    assert resp.text.count('<td>2015-05-12</td>') == 0

    freezer.move_to(datetime.date(2020, 4, 24))
    resp.forms['listing-settings']['filter-4-operator'].value = 'is_today_or_future'
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('<td>2020-04-24</td>') == 1
    assert resp.text.count('<td>2015-05-12</td>') == 0

    freezer.move_to(datetime.date(2020, 4, 24))
    resp.forms['listing-settings']['filter-4-operator'].value = 'is_past'
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('<td>2020-04-24</td>') == 0
    assert resp.text.count('<td>2015-05-12</td>') == 1

    freezer.move_to(datetime.date(2020, 4, 24))
    resp.forms['listing-settings']['filter-4-operator'].value = 'is_today_or_past'
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('<td>2020-04-24</td>') == 1
    assert resp.text.count('<td>2015-05-12</td>') == 1


def test_backoffice_user_filter(pub):
    pub.user_class.wipe()
    create_superuser(pub)
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form-title'
    formdef.fields = []
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.store()

    user1 = pub.user_class(name='userA')
    user1.store()
    user2 = pub.user_class(name='userB')
    user2.store()

    data_class = formdef.data_class()
    data_class.wipe()

    for i in range(0, 2):
        formdata = data_class()
        formdata.data = {}
        formdata.user_id = user1.id if bool(i % 2) else user2.id
        formdata.just_created()
        formdata.jump_status('new')
        formdata.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/')

    resp = app.get('/backoffice/management/form-title/?filter-user=on&filter-user-value=%s' % user1.id)
    assert resp.text.count('>userA<') > 0
    assert resp.text.count('>userB<') == 0
    assert '<option value="%s" selected="selected">userA</option>' % user1.id in resp
    # check it persits on filter changes
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('>userA<') > 0
    assert resp.text.count('>userB<') == 0

    resp = app.get('/backoffice/management/form-title/?filter-user=on&filter-user-value=%s' % user2.id)
    assert resp.text.count('>userA<') == 0
    assert resp.text.count('>userB<') > 0

    # filter on uuid
    user1.name_identifiers = ['0123456789']
    user1.store()
    resp = app.get('/backoffice/management/form-title/?filter-user-uuid=0123456789')
    assert resp.text.count('>userA<') > 0
    assert resp.text.count('>userB<') == 0
    assert '<option value="%s" selected="selected">userA</option>' % user1.id in resp
    # check it persists on filter changes
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('>userA<') > 0
    assert resp.text.count('>userB<') == 0

    # check with unknown uuid
    resp = app.get('/backoffice/management/form-title/?filter-user-uuid=XXX')
    assert resp.text.count('>userA<') == 0
    assert resp.text.count('>userB<') == 0


def test_backoffice_submission_agent_filter(pub):
    pub.user_class.wipe()
    user = create_superuser(pub)
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form-title'
    formdef.fields = []
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.backoffice_submission_roles = user.roles
    formdef.store()

    user1 = pub.user_class(name='userA')
    user1.store()
    user2 = pub.user_class(name='userB')
    user2.store()

    data_class = formdef.data_class()
    data_class.wipe()

    for i in range(0, 2):
        formdata = data_class()
        formdata.data = {}
        formdata.submission_agent_id = str(user1.id if bool(i % 2) else user2.id)
        formdata.just_created()
        formdata.jump_status('new')
        formdata.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/?limit=100')
    # enable submission-agent column
    resp.forms['listing-settings']['submission-agent'].checked = True
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('>userA</td>') > 0
    assert resp.text.count('>userB</td>') > 0

    # enable submission-agent filter
    resp.forms['listing-settings']['filter-submission-agent'].checked = True
    resp = resp.forms['listing-settings'].submit().follow()

    # check everything is still displayed
    assert resp.forms['listing-settings']['filter-submission-agent-value'].value == ''
    assert resp.text.count('>userA</td>') > 0
    assert resp.text.count('>userB</td>') > 0

    # check available filter values
    assert [x.text for x in resp.pyquery('select[name="filter-submission-agent-value"] option')] == [
        None,
        'Current user',
        'admin',
    ]

    # add userA and userB to role for backoffice submission
    user1.roles = user.roles
    user1.store()
    user2.roles = user.roles
    user2.store()

    # refresh
    resp = resp.forms['listing-settings'].submit().follow()
    assert [x.text for x in resp.pyquery('select[name="filter-submission-agent-value"] option')] == [
        None,
        'Current user',
        'admin',
        'userA',
        'userB',
    ]

    resp.forms['listing-settings']['filter-submission-agent-value'].value = str(user1.id)
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('>userA</td>') > 0
    assert resp.text.count('>userB</td>') == 0
    assert resp.pyquery('tbody tr').length == 1
    # check it persists on filter changes
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('>userA</td>') > 0
    assert resp.text.count('>userB</td>') == 0
    assert resp.pyquery('tbody tr').length == 1

    resp.forms['listing-settings']['filter-submission-agent-value'].value = str(user2.id)
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('>userA</td>') == 0
    assert resp.text.count('>userB</td>') > 0
    assert resp.pyquery('tbody tr').length == 1

    # filter on current user
    resp.forms['listing-settings']['filter-submission-agent-value'].value = '__current__'
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('>userA</td>') == 0
    assert resp.text.count('>userB</td>') == 0
    assert resp.pyquery('tbody tr').length == 0

    old_formdata_agent_id, formdata.submission_agent_id = formdata.submission_agent_id, user.id
    formdata.store()

    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('>userA</td>') == 0
    assert resp.text.count('>admin</td>') == 1
    assert resp.pyquery('tbody tr').length == 1

    # restore second formadata user
    formdata.submission_agent_id = old_formdata_agent_id
    formdata.store()

    # filter on uuid
    user1.name_identifiers = ['0123456789']
    user1.store()
    resp = app.get(
        '/backoffice/management/form-title/?filter-submission-agent-uuid=0123456789&submission-agent=on'
    )
    assert resp.forms['listing-settings']['filter-submission-agent-value'].value == str(user1.id)
    assert resp.text.count('>userA</td>') > 0
    assert resp.text.count('>userB</td>') == 0
    # check it persists on filter changes
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('>userA</td>') > 0
    assert resp.text.count('>userB</td>') == 0

    # check with unknown uuid
    resp = app.get('/backoffice/management/form-title/?filter-submission-agent-uuid=XXX&submission-agent=on')
    assert resp.forms['listing-settings']['filter-submission-agent-value'].value == '-1'
    assert resp.text.count('>userA</td>') == 0
    assert resp.text.count('>userB</td>') == 0
    # check it persists on submits
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('>userA</td>') == 0
    assert resp.text.count('>userB</td>') == 0


def test_workflow_function_filter(pub):
    pub.user_class.wipe()
    user = create_superuser(pub)
    user.name_identifiers = ['0123456789']
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    workflow = Workflow.get_default_workflow()
    workflow.id = '2'
    workflow.roles['_foobar'] = 'Foobar'
    workflow.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form-title'
    formdef.fields = []
    formdef.workflow = workflow
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.store()

    user1 = pub.user_class(name='userA')
    user1.name_identifiers = ['56789']
    user1.store()
    user2 = pub.user_class(name='userB')
    user2.name_identifiers = ['98765']
    user2.store()

    data_class = formdef.data_class()
    data_class.wipe()

    formdatas = []
    for i in range(3):
        formdatas.append(data_class())

    formdatas[0].workflow_roles = {'_foobar': ['_user:%s' % user.id]}
    formdatas[1].workflow_roles = {'_foobar': ['_user:%s' % user1.id]}
    formdatas[2].workflow_roles = {'_foobar': ['_user:%s' % user1.id, '_user:%s' % user2.id]}

    for formdata in formdatas:
        formdata.just_created()
        formdata.jump_status('new')
        formdata.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/')
    # enable user-function column
    resp.forms['listing-settings']['filter-user-function'].checked = True
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('<tr') == 4

    # set a value in the select field
    resp.forms['listing-settings']['filter-user-function-value'].value = '_foobar'
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('<tr') == 2


def test_backoffice_internal_id_filter(pub):
    pub.user_class.wipe()
    create_superuser(pub)
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form-title'
    formdef.fields = []
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.store()

    data_class = formdef.data_class()
    data_class.wipe()

    for i in range(3):
        formdata = data_class()
        formdata.data = {}
        formdata.just_created()
        formdata.jump_status('new')
        formdata.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/')
    resp.forms['listing-settings']['filter-internal-id'].checked = True
    resp = resp.forms['listing-settings'].submit().follow()

    assert resp.forms['listing-settings']['filter-internal-id-value'].value == ''
    assert resp.forms['listing-settings']['filter-internal-id-operator'].value == 'eq'
    assert [x[0] for x in resp.forms['listing-settings']['filter-internal-id-operator'].options] == [
        'eq',
        'ne',
        'lt',
        'lte',
        'gt',
        'gte',
        'in',
        'not_in',
    ]

    resp.forms['listing-settings']['filter-internal-id-value'].value = '1'
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('>1-1<') > 0
    assert resp.text.count('>1-2<') == 0
    assert resp.text.count('>1-3<') == 0
    assert resp.pyquery.find('input[value="1"]')  # displayed in sidebar
    # check it persists on filter changes
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('>1-1<') > 0
    assert resp.text.count('>1-2<') == 0
    assert resp.text.count('>1-3<') == 0

    resp.forms['listing-settings']['filter-internal-id-value'].value = '2'
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('>1-1<') == 0
    assert resp.text.count('>1-2<') > 0
    assert resp.text.count('>1-3<') == 0
    resp.forms['listing-settings']['filter-internal-id-operator'].value = 'ne'
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('>1-1<') > 0
    assert resp.text.count('>1-2<') == 0
    assert resp.text.count('>1-3<') > 0

    # invalid value
    resp.forms['listing-settings']['filter-internal-id-value'].value = 'foobar'
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('<tr') == 1
    assert 'Invalid value &quot;foobar&quot; for &quot;filter-internal-id-value&quot;' in resp


def test_backoffice_table_varname_filter(pub):
    pub.user_class.wipe()
    create_superuser(pub)
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    datasource = {
        'type': 'jsonvalue',
        'value': '[{"id": "A", "text": "aa"}, {"id": "B", "text": "bb"}, {"id": "C", "text": "cc"}]',
    }
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form-title'
    formdef.fields = [
        fields.ItemField(id='3', label='3rd field', data_source=datasource, varname='foo'),
    ]
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.store()

    data_class = formdef.data_class()
    data_class.wipe()

    for i in range(0, 3):
        formdata = data_class()
        formdata.data = {}
        formdata.data['3'] = 'A' if bool(i % 2) else 'B'
        formdata.data['3_display'] = 'aa' if bool(i % 2) else 'bb'
        formdata.just_created()
        formdata.jump_status('new')
        formdata.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/?filter-foo=A')
    # check filter is applied
    assert resp.text.count('<tr') == 2
    # and kept in parameters
    assert resp.forms['listing-settings']['filter-3'].checked
    assert resp.forms['listing-settings']['filter-3-value'].value == 'A'
    assert resp.forms['listing-settings']['filter-3-operator'].value == 'eq'
    assert [x[0] for x in resp.forms['listing-settings']['filter-3-operator'].options] == [
        'eq',
        'ne',
        'lt',
        'lte',
        'gt',
        'gte',
        'between',
        'in',
        'not_in',
        'absent',
        'existing',
    ]

    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('<tr') == 2

    resp = app.get('/backoffice/management/form-title/?filter-foo=A&filter-foo-operator=ne')
    # check filter is applied
    assert resp.text.count('<tr') == 3
    # and kept in parameters
    assert resp.forms['listing-settings']['filter-3'].checked
    assert resp.forms['listing-settings']['filter-3-value'].value == 'A'
    assert resp.forms['listing-settings']['filter-3-operator'].value == 'ne'

    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('<tr') == 3


def test_backoffice_block_field_filter(pub):
    pub.user_class.wipe()
    create_superuser(pub)
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    NamedDataSource.wipe()
    data_source = NamedDataSource(name='foobar')
    data_source.data_source = {
        'type': 'jsonvalue',
        'value': '[{"id": "1", "text": "foo", "more": "XXX"}, {"id": "2", "text": "bar", "more": "YYY"}]',
    }
    data_source.store()

    BlockDef.wipe()
    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.StringField(id='1', label='String', varname='string'),
        fields.ItemField(id='2', label='Item', data_source={'type': 'foobar'}, varname='item'),
        fields.BoolField(id='3', label='Bool', varname='bool'),
        fields.DateField(id='4', label='Date', varname='date'),
        fields.EmailField(id='5', label='Email', varname='email'),
        fields.StringField(id='6', label='String', varname='casestring'),
    ]
    block.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form-title'
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.fields = [
        fields.BlockField(
            id='0', label='Block Data', varname='blockdata', block_slug='foobar', max_items='3'
        ),
    ]
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.store()

    data_class = formdef.data_class()
    data_class.wipe()

    for i in range(10):
        formdata = data_class()
        formdata.data = {
            '0': {
                'data': [
                    {
                        '1': 'plop%s' % i,
                        '2': '1' if i % 2 else '2',
                        '2_display': 'foo' if i % 2 else 'bar',
                        '2_structured': 'XXX' if i % 2 else 'YYY',
                        '3': bool(i % 2),
                        '4': '2021-06-%02d' % (i + 1),
                        '5': 'a@localhost' if i % 2 else 'b@localhost',
                        '6': 'pLop%s' % (i % 3),
                    },
                ],
                'schema': {},  # not important here
            },
            '0_display': 'hello',
        }
        if i == 0:
            formdata.data['0']['data'].append(
                {
                    '1': 'plop%s' % i,
                    '2': '1',
                    '2_display': 'foo',
                    '2_structured': 'XXX',
                    '3': True,
                    '4': '2021-06-02',
                    '5': 'a@localhost',
                    '6': 'pLop%s' % (i % 3),
                },
            )
        formdata.just_created()
        formdata.jump_status('new')
        formdata.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/')
    assert resp.pyquery('[type="checkbox"][name="0-1"]').parent().text() == 'Block Data / String'
    assert resp.pyquery('[type="checkbox"][name="0-2"]').parent().text() == 'Block Data / Item'
    assert resp.pyquery('[type="checkbox"][name="0-3"]').parent().text() == 'Block Data / Bool'
    assert resp.pyquery('[type="checkbox"][name="0-4"]').parent().text() == 'Block Data / Date'
    assert resp.pyquery('[type="checkbox"][name="0-5"]').parent().text() == 'Block Data / Email'

    # string
    resp = app.get('/backoffice/management/form-title/')
    resp.forms['listing-settings']['filter-0-1'].checked = True
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.forms['listing-settings']['filter-0-1-value'].value == ''
    assert resp.forms['listing-settings']['filter-0-1-operator'].value == 'eq'
    assert [x[0] for x in resp.forms['listing-settings']['filter-0-1-operator'].options] == [
        'eq',
        'ne',
        'lt',
        'lte',
        'gt',
        'gte',
        'between',
        'in',
        'not_in',
        'absent',
        'existing',
        'icontains',
        'ieq',
    ]
    resp.forms['listing-settings']['filter-0-1-value'].value = 'plop0'
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('<tr') == 1 + 1
    resp.forms['listing-settings']['filter-0-1-value'].value = 'plop2'
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('<tr') == 1 + 1
    resp.forms['listing-settings']['filter-0-1-value'].value = 'plop10'
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('<tr') == 1 + 0
    resp.forms['listing-settings']['filter-0-1-value'].value = 'plop2'
    resp.forms['listing-settings']['filter-0-1-operator'].value = 'ne'
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('<tr') == 1 + 9

    # case insensitive string
    resp = app.get('/backoffice/management/form-title/')
    resp.forms['listing-settings']['filter-0-6'].checked = True
    resp = resp.forms['listing-settings'].submit().follow()
    resp.forms['listing-settings']['filter-0-6-value'].value = 'pLop2'
    resp.forms['listing-settings']['filter-0-6-operator'].value = 'ieq'
    resp = resp.forms['listing-settings'].submit().follow()
    assert len(resp.pyquery('tbody tr')) == 3

    # item
    resp = app.get('/backoffice/management/form-title/')
    resp.forms['listing-settings']['filter-0-2'].checked = True
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.forms['listing-settings']['filter-0-2-value'].value == ''
    assert resp.forms['listing-settings']['filter-0-2-operator'].value == 'eq'
    assert [x[0] for x in resp.forms['listing-settings']['filter-0-2-operator'].options] == [
        'eq',
        'ne',
        'lt',
        'lte',
        'gt',
        'gte',
        'between',
        'in',
        'not_in',
        'absent',
        'existing',
    ]
    resp.forms['listing-settings']['filter-0-2-value'].value = '1'
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('<tr') == 1 + 6
    resp.forms['listing-settings']['filter-0-2-value'].value = '2'
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('<tr') == 1 + 5
    resp2 = app.get(resp.request.path + 'filter-options?filter_field_id=0-2&_search=foo')
    assert [x['id'] for x in resp2.json['data']] == ['1']
    resp2 = app.get(resp.request.path + 'filter-options?filter_field_id=0-2&_search=bar')
    assert [x['id'] for x in resp2.json['data']] == ['2']
    resp.forms['listing-settings']['filter-0-2-value'].value = '1'
    resp.forms['listing-settings']['filter-0-2-operator'].value = 'ne'
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('<tr') == 1 + 4

    # bool
    resp = app.get('/backoffice/management/form-title/')
    resp.forms['listing-settings']['filter-0-3'].checked = True
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.forms['listing-settings']['filter-0-3-value'].value == ''
    assert resp.forms['listing-settings']['filter-0-3-operator'].value == 'eq'
    assert [x[0] for x in resp.forms['listing-settings']['filter-0-3-operator'].options] == [
        'eq',
        'ne',
        'absent',
        'existing',
    ]
    resp.forms['listing-settings']['filter-0-3-value'].value = 'true'
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('<tr') == 1 + 6
    resp.forms['listing-settings']['filter-0-3-value'].value = 'false'
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('<tr') == 1 + 5
    resp.forms['listing-settings']['filter-0-3-value'].value = 'false'
    resp.forms['listing-settings']['filter-0-3-operator'].value = 'ne'
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('<tr') == 1 + 5

    # date
    resp = app.get('/backoffice/management/form-title/')
    resp.forms['listing-settings']['filter-0-4'].checked = True
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.forms['listing-settings']['filter-0-4-value'].value == ''
    assert resp.forms['listing-settings']['filter-0-4-operator'].value == 'eq'
    assert [x[0] for x in resp.forms['listing-settings']['filter-0-4-operator'].options] == [
        'eq',
        'ne',
        'lt',
        'lte',
        'gt',
        'gte',
        'between',
        'in',
        'not_in',
        'absent',
        'existing',
        'is_today',
        'is_tomorrow',
        'is_yesterday',
        'is_this_week',
        'is_future',
        'is_past',
        'is_today_or_future',
        'is_today_or_past',
    ]
    resp.forms['listing-settings']['filter-0-4-value'].value = '2021-06-01'
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('<tr') == 1 + 1
    resp.forms['listing-settings']['filter-0-4-value'].value = '2021-06-02'
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('<tr') == 1 + 2
    resp.forms['listing-settings']['filter-0-4-value'].value = '02/06/2021'
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('<tr') == 1 + 2
    resp.forms['listing-settings']['filter-0-4-value'].value = '2021-06-02'
    resp.forms['listing-settings']['filter-0-4-operator'].value = 'ne'
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('<tr') == 1 + 8

    # email
    resp = app.get('/backoffice/management/form-title/')
    resp.forms['listing-settings']['filter-0-5'].checked = True
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.forms['listing-settings']['filter-0-5-value'].value == ''
    assert resp.forms['listing-settings']['filter-0-5-operator'].value == 'eq'
    assert [x[0] for x in resp.forms['listing-settings']['filter-0-5-operator'].options] == [
        'eq',
        'ne',
        'in',
        'not_in',
        'absent',
        'existing',
        'icontains',
        'ieq',
    ]
    resp.forms['listing-settings']['filter-0-5-value'].value = 'a@localhost'
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('<tr') == 1 + 6
    resp.forms['listing-settings']['filter-0-5-value'].value = 'b@localhost'
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('<tr') == 1 + 5
    resp.forms['listing-settings']['filter-0-5-value'].value = 'c@localhost'
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('<tr') == 1 + 0
    resp.forms['listing-settings']['filter-0-5-value'].value = 'a@localhost'
    resp.forms['listing-settings']['filter-0-5-operator'].value = 'ne'
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('<tr') == 1 + 4
    resp.forms['listing-settings']['filter-0-5-value'].value = '@localhost'
    resp.forms['listing-settings']['filter-0-5-operator'].value = 'icontains'
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('<tr') == 1 + 10

    # mix
    resp = app.get('/backoffice/management/form-title/')
    resp.forms['listing-settings']['filter-0-1'].checked = True
    resp.forms['listing-settings']['filter-0-2'].checked = True
    resp = resp.forms['listing-settings'].submit().follow()
    resp.forms['listing-settings']['filter-0-1-value'].value = 'plop1'
    resp.forms['listing-settings']['filter-0-2-value'].value = '1'
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('<tr') == 1 + 1
    resp.forms['listing-settings']['filter-0-1-value'].value = 'plop0'
    resp.forms['listing-settings']['filter-0-2-value'].value = '1'
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('<tr') == 1 + 1
    resp.forms['listing-settings']['filter-0-1-value'].value = 'plop0'
    resp.forms['listing-settings']['filter-0-2-value'].value = '2'
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('<tr') == 1 + 1


def test_backoffice_numeric_filter(pub):
    pub.user_class.wipe()
    create_superuser(pub)
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form-title'
    formdef.fields = [
        fields.NumericField(
            id='4', label='4th field', display_locations=['validation', 'summary', 'listings']
        )
    ]
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.store()

    data_class = formdef.data_class()
    data_class.wipe()

    for i in range(0, 2):
        formdata = data_class()
        formdata.data = {}
        formdata.data['4'] = '123.4' if bool(i % 2) else '315'
        formdata.just_created()
        formdata.jump_status('new')
        formdata.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/')
    resp.forms['listing-settings']['filter-4'].checked = True
    resp = resp.forms['listing-settings'].submit().follow()

    assert resp.forms['listing-settings']['filter-4-value'].value == ''

    resp.forms['listing-settings']['filter-4-value'].value = '123.4'
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('<td>123.4</td>') > 0
    assert resp.text.count('<td>315</td>') == 0

    resp.forms['listing-settings']['filter-4-value'].value = '123,4'
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('<td>123.4</td>') > 0
    assert resp.text.count('<td>315</td>') == 0

    resp.forms['listing-settings']['filter-4-value'].value = '315'
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('<td>123.4</td>') == 0
    assert resp.text.count('<td>315</td>') > 0

    resp.forms['listing-settings']['filter-4-value'].value = '123.4'
    resp.forms['listing-settings']['filter-4-operator'].value = 'gte'
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('<td>123.4</td>') > 0
    assert resp.text.count('<td>315</td>') > 0

    resp.forms['listing-settings']['filter-4-value'].value = '123.4'
    resp.forms['listing-settings']['filter-4-operator'].value = 'gt'
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('<td>123.4</td>') == 0
    assert resp.text.count('<td>315</td>') > 0


def test_backoffice_criticality_filter(pub):
    pub.user_class.wipe()
    create_superuser(pub)
    pub.role_class.wipe()
    role = pub.role_class(name='test')
    role.store()

    Workflow.wipe()
    workflow = Workflow(name='test')
    workflow.criticality_levels = [
        WorkflowCriticalityLevel(name='green'),
        WorkflowCriticalityLevel(name='yellow'),
        WorkflowCriticalityLevel(name='red'),
        WorkflowCriticalityLevel(name='black'),
    ]
    workflow.add_status('st1')
    st2 = workflow.add_status('st2')
    action = st2.add_action('modify_criticality')
    action.mode = MODE_INC
    st3 = workflow.add_status('st3')
    action = st3.add_action('modify_criticality')
    action.mode = MODE_INC
    action = st3.add_action('modify_criticality')
    action.mode = MODE_INC
    workflow.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form-title'
    formdef.fields = [
        fields.StringField(id='1', label='Test', type='string', display_locations=['listings']),
    ]
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.workflow = workflow
    formdef.store()

    data_class = formdef.data_class()
    data_class.wipe()

    for i in range(3):
        formdata = data_class()
        formdata.data = {'1': f'baz{i}'}
        formdata.just_created()
        formdata.store()
        if i == 0:
            formdata.jump_status(st2.id)
        else:
            formdata.jump_status(st3.id)
        formdata.perform_workflow()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/')
    resp.forms['listing-settings']['filter'] = 'all'
    resp.forms['listing-settings']['filter-criticality-level'].checked = True
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('<td>baz') == 3
    resp.forms['listing-settings']['filter-criticality-level-value'] = '0'
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('<td>baz') == 0
    resp.forms['listing-settings']['filter-criticality-level-value'] = '1'
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('<td>baz') == 1
    resp.forms['listing-settings']['filter-criticality-level-value'] = '2'
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('<td>baz') == 2
    resp.forms['listing-settings']['filter-criticality-level-value'] = '3'
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('<td>baz') == 0
    resp.forms['listing-settings']['filter-criticality-level-value'] = ''
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('<td>baz') == 3
