import io
import json
import os
import re
import xml.etree.ElementTree as ET
from unittest import mock

import pytest
import responses
from webtest import Upload

from wcs import fields
from wcs.admin.settings import UserFieldsFormDef
from wcs.carddef import CardDef
from wcs.categories import Category, DataSourceCategory
from wcs.data_sources import NamedDataSource
from wcs.formdef import FormDef
from wcs.logged_errors import LoggedError
from wcs.qommon.http_request import HTTPRequest
from wcs.workflows import Workflow, WorkflowBackofficeFieldsFormDef

from ..utilities import clean_temporary_pub, create_temporary_pub, get_app, login
from .test_all import create_superuser


@pytest.fixture
def pub(emails):
    pub = create_temporary_pub()

    req = HTTPRequest(None, {'SCRIPT_NAME': '/', 'SERVER_NAME': 'example.net'})
    pub.set_app_dir(req)
    pub.cfg['identification'] = {'methods': ['password']}
    pub.cfg['language'] = {'language': 'en'}
    pub.write_cfg()

    return pub


def teardown_module(module):
    clean_temporary_pub()


def test_data_sources(pub):
    create_superuser(pub)
    app = login(get_app(pub))
    app.get('/backoffice/settings/data-sources/')
    # also check it's accessible from forms and workflows sections
    app.get('/backoffice/forms/data-sources/')
    app.get('/backoffice/workflows/data-sources/')

    # unknown datasource
    app.get('/backoffice/settings/data-sources/42/', status=404)
    app.get('/backoffice/forms/data-sources/42/', status=404)
    app.get('/backoffice/workflows/data-sources/42/', status=404)


def test_data_sources_from_carddefs(pub):
    create_superuser(pub)
    CardDef.wipe()
    pub.custom_view_class.wipe()
    NamedDataSource.wipe()

    app = login(get_app(pub))
    resp = app.get('/backoffice/settings/data-sources/')
    assert 'Data Sources from Card Models' in resp.text
    assert 'There are no data sources from card models.' in resp.text

    carddef = CardDef()
    carddef.name = 'foo'
    carddef.fields = []
    carddef.digest_templates = {'default': 'foo bar'}
    carddef.store()

    custom_view = pub.custom_view_class()
    custom_view.title = 'datasource card view'
    custom_view.formdef = carddef
    custom_view.columns = {'list': [{'id': 'id'}]}
    custom_view.filters = {}
    custom_view.visibility = 'datasource'
    custom_view.store()

    resp = app.get('/backoffice/settings/data-sources/')
    assert 'Data Sources from Card Models' in resp.text
    assert 'There are no data sources from card models.' not in resp.text
    assert resp.pyquery('.section .objects-list li:first-child a').text() == 'foo'
    assert (
        resp.pyquery('.section .objects-list li:first-child a').attr['href']
        == 'http://example.net/backoffice/data/foo/'
    )
    assert resp.pyquery('.section .objects-list li:last-child a').text() == 'foo - datasource card view'
    assert (
        resp.pyquery('.section .objects-list li:last-child a').attr['href']
        == 'http://example.net/backoffice/data/foo/datasource-card-view/'
    )


def test_data_sources_agenda_without_chrono(pub):
    create_superuser(pub)
    NamedDataSource.wipe()

    data_source = NamedDataSource(name='foobar')
    data_source.data_source = {'type': 'json', 'value': 'http://some.url'}
    data_source.external = 'agenda'
    data_source.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/settings/data-sources/')
    assert 'Agendas' not in resp.text
    assert 'There are no data sources from agendas.' not in resp.text
    assert 'sync-agendas' not in resp.text


def test_data_sources_agenda(pub, chrono_url):
    create_superuser(pub)
    NamedDataSource.wipe()
    CardDef.wipe()
    pub.custom_view_class.wipe()

    app = login(get_app(pub))
    resp = app.get('/backoffice/settings/data-sources/')
    assert 'Agendas' in resp.text
    assert 'There are no agendas.' in resp.text
    assert 'sync-agendas' in resp.text

    data_source = NamedDataSource(name='foobar')
    data_source.data_source = {'type': 'json', 'value': 'http://some.url'}
    data_source.store()
    resp = app.get('/backoffice/settings/data-sources/')
    assert 'Agendas' in resp.text
    assert 'There are no agendas.' in resp.text

    data_source.external = 'agenda'
    data_source.store()
    resp = app.get('/backoffice/settings/data-sources/')
    assert 'Agendas' in resp.text
    assert 'There are no agendas.' not in resp.text
    assert resp.pyquery('.section .objects-list li:first-child a').text() == 'foobar (foobar)'
    assert resp.pyquery('.section .objects-list li:first-child a').attr['href'] == data_source.get_admin_url()

    data_source.external_status = 'not-found'
    data_source.store()
    resp = app.get('/backoffice/settings/data-sources/')
    assert resp.pyquery('.section .objects-list li:first-child a').text() == 'foobar (foobar) - not found'
    assert resp.pyquery('.section .objects-list li:first-child a').attr['href'] == data_source.get_admin_url()
    assert resp.pyquery('.section .objects-list li:first-child a span.extra-info').text() == 'not found'


def test_data_sources_users(pub):
    create_superuser(pub)
    NamedDataSource.wipe()
    CardDef.wipe()
    FormDef.wipe()
    pub.custom_view_class.wipe()

    app = login(get_app(pub))
    resp = app.get('/backoffice/settings/data-sources/')

    assert 'Users Data Sources' in resp
    assert 'There are no users data sources defined.' in resp
    assert 'new-users' in resp

    data_source = NamedDataSource(name='foobar')
    data_source.data_source = {'type': 'wcs:users'}
    data_source.store()
    resp = app.get('/backoffice/settings/data-sources/')
    assert 'There are no users data sources defined.' not in resp
    assert resp.pyquery('.section .objects-list li:first-child a').text() == 'foobar (foobar)'
    assert resp.pyquery('.section .objects-list li:first-child a').attr['href'] == data_source.get_admin_url()

    resp = app.get(f'/backoffice/settings/data-sources/{data_source.id}/edit')
    assert resp.pyquery('button[role=tab]').text() == 'General Advanced'
    assert [x.attrib['data-widget-name'] for x in resp.pyquery('#panel-advanced .widget')] == ['slug']

    # make it used
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [fields.ItemField(id='0', label='string', data_source={'type': data_source.slug})]
    formdef.store()

    resp = app.get(f'/backoffice/settings/data-sources/{data_source.id}/edit')
    assert resp.pyquery('button[role=tab]').text() == 'General'


def test_data_sources_new(pub):
    create_superuser(pub)
    NamedDataSource.wipe()
    app = login(get_app(pub))

    # go to the page and cancel
    resp = app.get('/backoffice/settings/data-sources/')
    resp = resp.click('New Data Source')
    resp = resp.forms[0].submit('cancel')
    assert resp.location == 'http://example.net/backoffice/settings/data-sources/'

    # go to the page and add a data source
    resp = app.get('/backoffice/settings/data-sources/')
    resp = resp.click('New Data Source')
    resp.forms[0]['name'] = 'a new data source'

    resp.forms[0]['data_source$type'] = 'jsonvalue'
    resp.forms[0]['data_source$value'] = json.dumps([{'id': '1', 'text': 'un'}, {'id': '2', 'text': 'deux'}])
    resp = resp.forms[0].submit('submit')
    assert resp.location == 'http://example.net/backoffice/settings/data-sources/'
    resp = resp.follow()
    assert 'a new data source' in resp.text
    resp = resp.click('a new data source')
    assert 'Data Source - a new data source' in resp.text
    resp = resp.click('Edit')
    assert 'Edit Data Source' in resp.text

    assert NamedDataSource.get(1).name == 'a new data source'

    # add a second one
    resp = app.get('/backoffice/settings/data-sources/')
    resp = resp.click('New Data Source')
    resp.forms[0]['name'] = 'an other data source'
    resp.forms[0]['data_source$type'] = 'jsonvalue'
    resp = resp.forms[0].submit('data_source$apply')
    resp.forms[0]['data_source$value'] = json.dumps([{'id': '1', 'text': 'un'}, {'id': '2', 'text': 'deux'}])
    resp = resp.forms[0].submit('submit')

    assert NamedDataSource.count() == 2


def test_data_sources_users_new(pub):
    create_superuser(pub)
    NamedDataSource.wipe()
    DataSourceCategory.wipe()
    app = login(get_app(pub))

    resp = app.get('/backoffice/settings/data-sources/')
    resp = resp.click('New User Data Source')
    assert 'category_id' not in resp.form.fields

    DataSourceCategory(name='foo').store()
    resp = app.get('/backoffice/settings/data-sources/')
    resp = resp.click('New User Data Source')
    assert 'category_id' not in resp.form.fields


def test_data_sources_type_options(pub):
    create_superuser(pub)

    data_source = NamedDataSource(name='foobar')
    data_source.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/settings/data-sources/%s/edit' % data_source.id)
    assert resp.form['data_source$type'].options == [
        ('None', True, 'None'),
        ('json', False, 'JSON URL'),
        ('jsonp', False, 'JSONP URL'),
        ('geojson', False, 'GeoJSON URL'),
        ('jsonvalue', False, 'JSON Expression'),
    ]


def test_data_sources_type_json(pub):
    create_superuser(pub)
    NamedDataSource.wipe()

    data_source = NamedDataSource(name='foobar')
    data_source.store()
    app = login(get_app(pub))
    resp = app.get('/backoffice/settings/data-sources/%s/edit' % data_source.id)
    resp.form['data_source$type'] = 'json'
    resp.form['data_source$value'] = 'plop'  # invalid URL
    resp = resp.form.submit('submit')
    assert 'Value must be a full URL.' in resp.text
    resp.form['data_source$value'] = 'http://localhost/'
    resp = resp.form.submit('submit')
    assert 'Value must be a full URL.' not in resp.text
    data_source.refresh_from_storage()
    assert data_source.data_source == {'type': 'json', 'value': 'http://localhost/'}

    resp = app.get('/backoffice/settings/data-sources/%s/edit' % data_source.id)
    resp.form['data_source$value'] = '{{ [passerelle_url }}/test'  # invalid template
    resp = resp.form.submit('submit')
    assert 'syntax error in Django template' in resp.text
    resp.form['data_source$value'] = '{{ passerelle_url }}/test'
    resp = resp.form.submit('submit')
    assert 'syntax error in Django template' not in resp.text
    data_source.refresh_from_storage()
    assert data_source.data_source == {'type': 'json', 'value': '{{ passerelle_url }}/test'}


def test_data_sources_type_options_jsonp(pub):
    create_superuser(pub)

    data_source = NamedDataSource(name='foobar')
    data_source.store()

    if not pub.site_options.has_section('options'):
        pub.site_options.add_section('options')
    pub.site_options.set('options', 'disable-jsonp-sources', 'false')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)

    app = login(get_app(pub))
    resp = app.get('/backoffice/settings/data-sources/%s/edit' % data_source.id)
    assert 'jsonp' in [x[0] for x in resp.form['data_source$type'].options]

    pub.site_options.set('options', 'disable-jsonp-sources', 'true')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)

    resp = app.get('/backoffice/settings/data-sources/%s/edit' % data_source.id)
    assert 'jsonp' not in [x[0] for x in resp.form['data_source$type'].options]

    # make sure it's still displayed for sources using it.
    data_source.data_source = {'type': 'jsonp', 'value': 'http://some.url'}
    data_source.store()
    resp = app.get('/backoffice/settings/data-sources/%s/edit' % data_source.id)
    assert 'jsonp' in [x[0] for x in resp.form['data_source$type'].options]


def test_data_sources_category(pub):
    create_superuser(pub)

    DataSourceCategory.wipe()
    NamedDataSource.wipe()

    app = login(get_app(pub))
    resp = app.get('/backoffice/settings/data-sources/new')
    assert 'category_id' not in resp.form.fields

    data_source = NamedDataSource(name='foo')
    data_source.store()

    resp = app.get('/backoffice/settings/data-sources/categories/')
    resp = resp.click('New Category')
    resp.form['name'] = 'a new category'
    resp = resp.form.submit('submit')
    assert DataSourceCategory.count() == 1
    category = DataSourceCategory.select()[0]
    assert category.name == 'a new category'

    resp = app.get('/backoffice/settings/data-sources/new')
    assert 'category_id' in resp.form.fields

    resp = app.get('/backoffice/settings/data-sources/%s/' % data_source.id)
    resp = resp.click(href=re.compile('^edit$'))
    resp.form['category_id'] = str(category.id)
    resp = resp.form.submit('cancel').follow()
    data_source.refresh_from_storage()
    assert data_source.category_id is None

    resp = app.get('/backoffice/settings/data-sources/%s/' % data_source.id)
    resp = resp.click(href=re.compile('^edit$'))
    resp.form['category_id'] = str(category.id)
    resp.form['data_source$type'] = 'jsonvalue'
    resp.form['data_source$value'] = json.dumps([{'id': '1', 'text': 'un'}, {'id': '2', 'text': 'deux'}])
    resp = resp.form.submit('submit').follow()
    data_source.refresh_from_storage()
    assert str(data_source.category_id) == str(category.id)

    resp = app.get('/backoffice/settings/data-sources/%s/' % data_source.id)
    resp = resp.click(href=re.compile('^edit$'))
    assert resp.form['category_id'].value == str(category.id)

    resp = app.get('/backoffice/settings/data-sources/categories/')
    resp = resp.click('New Category')
    resp.form['name'] = 'a second category'
    resp = resp.form.submit('submit')
    assert DataSourceCategory.count() == 2
    category2 = [x for x in DataSourceCategory.select() if x.id != category.id][0]
    assert category2.name == 'a second category'

    app.get(
        '/backoffice/settings/data-sources/categories/update_order?order=%s;%s;' % (category2.id, category.id)
    )
    categories = DataSourceCategory.select()
    DataSourceCategory.sort_by_position(categories)
    assert [x.id for x in categories] == [str(category2.id), str(category.id)]

    app.get(
        '/backoffice/settings/data-sources/categories/update_order?order=%s;%s;0'
        % (category.id, category2.id)
    )
    categories = DataSourceCategory.select()
    DataSourceCategory.sort_by_position(categories)
    assert [x.id for x in categories] == [str(category.id), str(category2.id)]

    resp = app.get('/backoffice/settings/data-sources/categories/')
    resp = resp.click('a new category')
    resp = resp.click('Delete')
    resp = resp.form.submit()
    data_source.refresh_from_storage()
    assert not data_source.category_id


def test_data_sources_view(pub):
    create_superuser(pub)
    NamedDataSource.wipe()

    data_source = NamedDataSource(name='foobar')
    app = login(get_app(pub))

    data_source.data_source = {'type': 'jsonvalue', 'value': '[]'}
    data_source.store()
    resp = app.get('/backoffice/settings/data-sources/%s/' % data_source.id)
    assert 'This data source is readonly.' not in resp
    assert 'href="edit"' in resp
    assert 'href="delete"' in resp
    assert 'href="duplicate"' in resp
    assert 'Type of source: JSON Expression' in resp.text
    assert 'JSON Expression' in resp.text

    resp = app.get(resp.pyquery('[data-async-url]').attr['data-async-url'])
    assert not resp.text

    data_source.data_source = {
        'type': 'jsonvalue',
        'value': json.dumps(
            [
                {'id': 'AAA', 'text': 'AAA'},
                {'id': 'BBB', 'text': 'BBB'},
            ]
        ),
    }
    data_source.store()
    resp = app.get('/backoffice/settings/data-sources/%s/' % data_source.id)
    assert resp.text.count('AAA') == 2  # expression (id, text)
    resp = app.get(resp.pyquery('[data-async-url]').attr['data-async-url'])
    assert resp.text.count('AAA') == 2

    # check json
    data_source.data_source = {'type': 'json', 'value': 'https://example.net'}
    data_source.store()
    resp = app.get('/backoffice/settings/data-sources/%s/' % data_source.id)
    with responses.RequestsMock() as rsps:
        rsps.get(
            'https://example.net', json={'data': [{'id': '1', 'text': 'foo'}, {'id': '2', 'text': 'bar'}]}
        )
        resp = app.get(resp.pyquery('[data-async-url]').attr['data-async-url'])
    assert 'foo' in resp.text

    # with other attributes
    data_source.data_attribute = 'results'
    data_source.id_attribute = 'pk'
    data_source.text_attribute = 'label'
    data_source.store()
    resp = app.get('/backoffice/settings/data-sources/%s/' % data_source.id)
    with responses.RequestsMock() as rsps:
        rsps.get('https://example.net', json={'results': [{'pk': '1', 'label': 'foo'}, {'pk': '2'}]})
        resp = app.get(resp.pyquery('[data-async-url]').attr['data-async-url'])
    assert '<tt>1</tt>: foo</li>' in resp.text
    assert '<tt>2</tt>: 2</li>' in resp.text
    assert '<p>Additional keys are available: label, pk</p>' in resp.text

    # variadic url
    data_source.data_attribute = None
    data_source.data_source = {'type': 'json', 'value': '{{ site_url }}/foo/bar'}
    data_source.store()
    resp = app.get('/backoffice/settings/data-sources/%s/' % data_source.id)
    assert '<a href="http://example.net/foo/bar"' in resp.text
    resp = app.get(resp.pyquery('[data-async-url]').attr['data-async-url'])
    assert not resp.text

    # errors
    data_source.data_source = {'type': 'json', 'value': 'http://remote.example.net/404'}
    data_source.notify_on_errors = True
    data_source.record_on_errors = True
    data_source.store()
    resp = app.get('/backoffice/settings/data-sources/%s/' % data_source.id)
    with responses.RequestsMock() as rsps:
        rsps.get('http://remote.example.net/404', status=404)
        resp = app.get(resp.pyquery('[data-async-url]').attr['data-async-url'])
    assert LoggedError.count() == 0  # error not recorded

    # check geojson
    data_source.data_source = {'type': 'geojson', 'value': 'https://example.net'}
    data_source.store()
    resp = app.get('/backoffice/settings/data-sources/%s/' % data_source.id)
    with responses.RequestsMock() as rsps:
        rsps.get(
            'https://example.net',
            json={
                'features': [
                    {'properties': {'id': '1', 'text': 'foo', 'label': 'foo'}},
                    {'properties': {'id': '2', 'text': 'bar', 'label': 'bar'}},
                ]
            },
        )
        resp = app.get(resp.pyquery('[data-async-url]').attr['data-async-url'])
    assert 'foo' in resp.text
    assert 'bar' in resp.text
    assert 'Additional keys are available: geometry_coordinates, geometry_type, properties_label' in resp.text

    data_source.data_source = {
        'type': 'jsonvalue',
        'value': json.dumps([{'id': str(x), 'text': str(x)} for x in range(100)]),
    }
    data_source.store()
    resp = app.get('/backoffice/settings/data-sources/%s/' % data_source.id)
    resp = app.get(resp.pyquery('[data-async-url]').attr['data-async-url'])
    assert resp.text.count('<li>') < 100
    assert '<li>...</li>' in resp.text

    data_source.data_source = {
        'type': 'jsonvalue',
        'value': json.dumps(
            [
                {'id': 'a', 'text': 'BBB', 'foo': 'bar1'},
                {'id': 'b', 'text': 'BBB', 'foo': 'bar2'},
            ]
        ),
    }
    data_source.store()
    resp = app.get('/backoffice/settings/data-sources/%s/' % data_source.id)
    resp = app.get(resp.pyquery('[data-async-url]').attr['data-async-url'])
    assert 'Additional keys are available: foo' in resp.text

    # check formdef listing
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test data source'
    formdef.fields = [fields.ItemField(id='1', label='item', data_source={'type': data_source.slug})]
    formdef.store()

    resp = app.get('/backoffice/settings/data-sources/%s/' % data_source.id)
    assert 'Usage in forms' in resp.text
    assert [x.attrib['href'] for x in resp.pyquery('.usage-in-forms a')] == [
        'http://example.net/backoffice/forms/1/fields/1/'
    ]

    # additional formdef types
    user_formdef = UserFieldsFormDef(pub)
    user_formdef.fields.append(fields.ItemField(id='1', label='item', data_source={'type': data_source.slug}))
    user_formdef.store()

    from wcs.workflows import WorkflowVariablesFieldsFormDef

    Workflow.wipe()
    workflow = Workflow(name='Workflow One')
    workflow.variables_formdef = WorkflowVariablesFieldsFormDef(workflow=workflow)
    workflow.variables_formdef.fields.append(
        fields.ItemField(id='1', label='item', data_source={'type': data_source.slug})
    )
    workflow.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(workflow)
    workflow.backoffice_fields_formdef.fields.append(
        fields.ItemField(id='1', label='item', data_source={'type': data_source.slug})
    )

    from wcs.wf.form import WorkflowFormFieldsFormDef

    baz_status = workflow.add_status(name='baz')
    display_form = baz_status.add_action('form', id='_x')
    display_form.id = '_x'
    display_form.formdef = WorkflowFormFieldsFormDef(item=display_form)
    display_form.formdef.fields.append(
        fields.ItemField(id='1', label='item', data_source={'type': data_source.slug})
    )

    ac = workflow.add_global_action('Foobar')
    form = ac.add_action('form', id='_y')
    form.formdef = WorkflowFormFieldsFormDef(item=form)
    form.formdef.fields = [fields.ItemField(id='3', label='Test', data_source={'type': data_source.slug})]
    form.formdef.store()
    form.hide_submit_button = False

    workflow.store()

    carddef = CardDef()
    carddef.name = 'Baz'
    carddef.fields = [fields.ItemField(id='1', label='item', data_source={'type': data_source.slug})]
    carddef.store()

    resp = app.get('/backoffice/settings/data-sources/%s/' % data_source.id)
    assert 'Usage in forms' in resp.text
    assert sorted([x.attrib['href'] for x in resp.pyquery('.usage-in-forms a')]) == [
        f'http://example.net/backoffice/cards/{carddef.id}/fields/1/',
        f'http://example.net/backoffice/forms/{formdef.id}/fields/1/',
        'http://example.net/backoffice/settings/users/fields/fields/1/',
        f'http://example.net/backoffice/workflows/{workflow.id}/backoffice-fields/fields/1/',
        f'http://example.net/backoffice/workflows/{workflow.id}/global-actions/1/items/_y/fields/3/',
        f'http://example.net/backoffice/workflows/{workflow.id}/status/1/items/_x/fields/1/',
        f'http://example.net/backoffice/workflows/{workflow.id}/variables/fields/1/',
    ]

    # cleanup
    user_formdef = UserFieldsFormDef(pub)
    user_formdef.fields = []
    user_formdef.store()
    Workflow.wipe()
    CardDef.wipe()


@mock.patch('wcs.data_sources._get_structured_items')
def test_data_sources_view_with_exception_in_preview(mock_get_structured_items, pub):
    # all inner exceptions should be caught and displayed as empty result
    mock_get_structured_items.side_effect = Exception
    create_superuser(pub)
    NamedDataSource.wipe()

    data_source = NamedDataSource(name='foobar')
    app = login(get_app(pub))

    data_source.data_source = {'type': 'json', 'value': 'xxx'}
    data_source.store()
    resp = app.get('/backoffice/settings/data-sources/%s/' % data_source.id)
    resp = app.get(resp.pyquery('[data-async-url]').attr['data-async-url'])
    assert 'Unexpected fatal error getting items for preview' in resp.text


def test_data_sources_duplicate(pub):
    create_superuser(pub)
    NamedDataSource.wipe()

    data_source = NamedDataSource(name='foobar')
    data_source.data_source = {'type': 'json', 'value': '{{data_source.foobar}}'}
    data_source.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/settings/data-sources/%s/duplicate' % data_source.id)
    resp = resp.click(href='duplicate')
    resp = resp.forms[0].submit()
    assert NamedDataSource.count() == 2
    new_data_source = NamedDataSource.select(order_by='id')[1]
    assert resp.location == 'http://example.net/backoffice/settings/data-sources/%s' % new_data_source.id
    assert new_data_source.data_source == {'type': 'json', 'value': '{{data_source.foobar}}'}
    assert new_data_source.external is None


def test_data_sources_agenda_view(pub):
    create_superuser(pub)
    NamedDataSource.wipe()

    data_source = NamedDataSource(name='foobar')
    data_source.data_source = {'type': 'json', 'value': 'http://some.url'}
    data_source.external = 'agenda'
    data_source.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/settings/data-sources/%s/' % data_source.id)
    assert 'This data source is readonly.' in resp
    assert 'href="edit"' not in resp
    assert 'href="delete"' not in resp
    assert 'href="duplicate"' in resp


def test_data_sources_agenda_duplicate(pub):
    create_superuser(pub)
    NamedDataSource.wipe()

    data_source = NamedDataSource(name='foobar')
    data_source.data_source = {'type': 'json', 'value': 'http://some.url'}
    data_source.external = 'agenda'
    data_source.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/settings/data-sources/%s/duplicate' % data_source.id)
    resp = resp.forms[0].submit('cancel')
    assert resp.location == 'http://example.net/backoffice/settings/data-sources/%s/' % data_source.id
    assert NamedDataSource.count() == 1

    resp = app.get('/backoffice/settings/data-sources/%s/duplicate' % data_source.id)
    resp = resp.click(href='duplicate')
    resp = resp.forms[0].submit()
    assert NamedDataSource.count() == 2
    new_data_source = NamedDataSource.select(order_by='id')[1]
    assert resp.location == 'http://example.net/backoffice/settings/data-sources/%s' % new_data_source.id
    assert new_data_source.data_source == {'type': 'json', 'value': 'http://some.url'}
    assert new_data_source.external == 'agenda_manual'
    assert new_data_source.qs_data is None


def test_data_sources_agenda_duplicate_external_type(pub):
    create_superuser(pub)
    NamedDataSource.wipe()

    data_source = NamedDataSource(name='foobar')
    data_source.data_source = {'type': 'json', 'value': 'http://some.url'}
    data_source.external = 'agenda'
    data_source.external_type = 'free_range'
    data_source.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/settings/data-sources/%s/duplicate' % data_source.id)
    resp = resp.click(href='duplicate')
    resp.forms[0].submit().follow()
    assert NamedDataSource.count() == 2
    new_data_source = NamedDataSource.select(order_by='id')[1]
    assert new_data_source.data_source == {
        'type': 'json',
        'value': 'http://some.url',
    }
    assert new_data_source.external == 'agenda_manual'
    assert new_data_source.external_type == 'free_range'
    assert new_data_source.qs_data is None


def test_data_sources_agenda_manual_view(pub):
    create_superuser(pub)
    NamedDataSource.wipe()

    data_source = NamedDataSource(name='foobar')
    data_source.data_source = {'type': 'json', 'value': 'http://some.url'}
    data_source.external = 'agenda_manual'
    data_source.qs_data = {'var1': 'value1', 'var2': 'value2'}
    data_source.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/settings/data-sources/%s/' % data_source.id)
    assert 'This data source is readonly.' not in resp
    assert 'href="edit"' in resp
    assert 'href="delete"' in resp
    assert 'href="duplicate"' in resp
    assert 'Type of source: Agenda data' in resp
    assert 'Copy of' not in resp
    assert 'Extra query string data' in resp
    assert '<li>var1: value1</li>' in resp
    assert '<li>var2: value2</li>' in resp

    data_source.qs_data = None
    data_source.store()
    resp = app.get('/backoffice/settings/data-sources/%s/' % data_source.id)
    assert 'Extra Query string data' not in resp

    data_source2 = NamedDataSource(name='foobar')
    data_source2.data_source = {'type': 'json', 'value': 'http://some.url'}
    data_source2.external = 'agenda'
    data_source2.store()
    resp = app.get('/backoffice/settings/data-sources/%s/' % data_source.id)
    assert (
        'Copy of: <a href="http://example.net/backoffice/settings/data-sources/%s/">foobar</a>'
        % data_source2.id
        in resp
    )


def test_data_sources_agenda_manual_duplicate(pub):
    create_superuser(pub)
    NamedDataSource.wipe()

    data_source = NamedDataSource(name='foobar')
    data_source.data_source = {'type': 'json', 'value': 'http://some.url'}
    data_source.external = 'agenda_manual'
    data_source.qs_data = {'var1': 'value1', 'var2': 'value2'}
    data_source.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/settings/data-sources/%s/duplicate' % data_source.id)
    resp = resp.click(href='duplicate')
    resp = resp.forms[0].submit()
    assert NamedDataSource.count() == 2
    new_data_source = NamedDataSource.select(order_by='id')[1]
    assert resp.location == 'http://example.net/backoffice/settings/data-sources/%s' % new_data_source.id
    assert new_data_source.data_source == {'type': 'json', 'value': 'http://some.url'}
    assert new_data_source.external == 'agenda_manual'
    assert new_data_source.qs_data == {'var1': 'value1', 'var2': 'value2'}


def test_data_sources_user_view(pub):
    create_superuser(pub)
    NamedDataSource.wipe()

    data_source = NamedDataSource(name='foobar')
    data_source.data_source = {'type': 'wcs:users'}
    data_source.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/settings/data-sources/%s/' % data_source.id)
    assert 'This data source is readonly.' not in resp
    assert 'href="edit"' in resp
    assert 'href="delete"' in resp
    assert 'href="duplicate"' in resp


def test_data_sources_user_duplicate(pub):
    create_superuser(pub)
    NamedDataSource.wipe()

    data_source = NamedDataSource(name='foobar')
    data_source.data_source = {'type': 'wcs:users'}
    data_source.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/settings/data-sources/%s/duplicate' % data_source.id)
    resp = resp.click(href='duplicate')
    resp = resp.forms[0].submit()
    assert NamedDataSource.count() == 2
    new_data_source = NamedDataSource.select(order_by='id')[1]
    assert resp.location == 'http://example.net/backoffice/settings/data-sources/%s' % new_data_source.id
    assert new_data_source.data_source == {'type': 'wcs:users', 'value': ''}
    assert new_data_source.external is None


def test_data_sources_edit(pub):
    create_superuser(pub)
    NamedDataSource.wipe()
    data_source = NamedDataSource(name='foobar')
    data_source.data_source = {'type': 'json', 'value': 'http://some.url'}
    data_source.store()

    FormDef.wipe()
    app = login(get_app(pub))
    resp = app.get('/backoffice/settings/data-sources/1/')

    resp = resp.click(href='edit')
    assert resp.forms[0]['name'].value == 'foobar'
    resp = resp.forms[0].submit('submit')
    assert resp.location == 'http://example.net/backoffice/settings/data-sources/1/'
    resp = resp.follow()

    resp = app.get('/backoffice/settings/data-sources/1/edit')
    assert '>Data Attribute</label>' in resp.text
    assert '>Id Attribute</label>' in resp.text
    assert '>Text Attribute</label>' in resp.text


def test_data_sources_edit_duplicate_name(pub):
    create_superuser(pub)
    NamedDataSource.wipe()
    data_source = NamedDataSource(name='foobar')
    data_source.data_source = {'type': 'jsonvalue', 'value': '[]'}
    data_source.store()
    data_source = NamedDataSource(name='foobar2')
    data_source.data_source = {'type': 'jsonvalue', 'value': '[]'}
    data_source.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/settings/data-sources/1/')
    resp = resp.click(href='edit')
    assert resp.forms[0]['name'].value == 'foobar'
    resp.forms[0]['name'] = 'foobar2'
    resp = resp.forms[0].submit('submit')
    assert 'This name is already used' in resp.text

    resp = resp.forms[0].submit('cancel')
    assert resp.location == 'http://example.net/backoffice/settings/data-sources/1/'


def test_data_sources_agenda_manual_edit(pub):
    create_superuser(pub)
    NamedDataSource.wipe()

    data_source = NamedDataSource(name='foobar')
    data_source.external = 'agenda_manual'
    data_source.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/settings/data-sources/%s/edit' % data_source.id)
    resp.forms[0]['qs_data$element0key'] = 'arg1'
    resp.forms[0]['qs_data$element0value$value_template'] = '{{ foobar }}'
    resp = resp.forms[0].submit('submit')

    data_source = NamedDataSource.get(data_source.id)
    assert data_source.qs_data == {'arg1': '{{ foobar }}'}


def test_data_sources_delete(pub):
    create_superuser(pub)
    NamedDataSource.wipe()
    category = NamedDataSource(name='foobar')
    category.store()

    FormDef.wipe()
    app = login(get_app(pub))
    resp = app.get('/backoffice/settings/data-sources/1/')

    resp = resp.click(href='delete')
    resp = resp.forms[0].submit('cancel')
    assert resp.location == 'http://example.net/backoffice/settings/data-sources/'
    assert NamedDataSource.count() == 1

    resp = app.get('/backoffice/settings/data-sources/1/')
    resp = resp.click(href='delete')
    resp = resp.forms[0].submit()
    assert resp.location == 'http://example.net/backoffice/settings/data-sources/'
    resp = resp.follow()
    assert NamedDataSource.count() == 0


def test_data_sources_in_use_delete(pub):
    create_superuser(pub)
    NamedDataSource.wipe()
    category = NamedDataSource(name='foobar')
    category.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.ItemField(id='0', label='string', data_source={'type': 'foobar'}),
    ]
    formdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/settings/data-sources/1/')
    resp = resp.click(href='delete')
    assert 'This datasource is still used, it cannot be deleted.' in resp.text
    assert 'delete-button' not in resp.text

    formdef.fields = []
    formdef.store()
    resp = app.get('/backoffice/settings/data-sources/1/')
    resp = resp.click(href='delete')
    assert 'delete-button' in resp.text


def test_data_sources_export(pub):
    create_superuser(pub)

    NamedDataSource.wipe()
    data_source = NamedDataSource(name='foobar')
    data_source.data_source = {'type': 'jsonvalue', 'value': '[]'}
    data_source.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/settings/data-sources/1/')

    resp = resp.click(href='export')
    xml_export = resp.text

    ds = io.StringIO(xml_export)
    data_source2 = NamedDataSource.import_from_xml(ds)
    assert data_source2.name == 'foobar'


def test_data_sources_import(pub):
    create_superuser(pub)

    NamedDataSource.wipe()
    data_source = NamedDataSource(name='foobar')
    data_source.slug = 'baaaz'
    data_source.data_source = {'type': 'jsonvalue', 'value': '[]'}
    data_source.store()
    data_source_xml = ET.tostring(data_source.export_to_xml(include_id=True))

    NamedDataSource.wipe()
    assert NamedDataSource.count() == 0

    app = login(get_app(pub))
    resp = app.get('/backoffice/settings/data-sources/')
    resp = resp.click(href='import')
    resp.forms[0]['file'] = Upload('datasource.wcs', data_source_xml)
    resp = resp.forms[0].submit()
    assert NamedDataSource.count() == 1
    assert NamedDataSource.get(1).slug == 'baaaz'

    # check slug
    resp = app.get('/backoffice/settings/data-sources/')
    resp = resp.click(href='import')
    resp.forms[0]['file'] = Upload('datasource.wcs', data_source_xml)
    resp = resp.forms[0].submit()
    assert NamedDataSource.count() == 2
    assert NamedDataSource.get(1).slug == 'baaaz'
    assert NamedDataSource.get(2).slug == 'foobar'
    resp = app.get('/backoffice/settings/data-sources/')
    resp = resp.click(href='import')
    resp.forms[0]['file'] = Upload('datasource.wcs', data_source_xml)
    resp = resp.forms[0].submit()
    assert NamedDataSource.count() == 3
    assert NamedDataSource.get(1).slug == 'baaaz'
    assert NamedDataSource.get(2).slug == 'foobar'
    assert NamedDataSource.get(3).slug == 'foobar_1'

    # import an invalid file
    resp = app.get('/backoffice/settings/data-sources/')
    resp = resp.click(href='import')
    resp.form['file'] = Upload('datasource.wcs', b'garbage')
    resp = resp.form.submit()
    assert 'Invalid File' in resp.text


def test_data_sources_edit_slug(pub):
    create_superuser(pub)
    NamedDataSource.wipe()
    data_source = NamedDataSource(name='foobar')
    data_source.data_source = {'type': 'jsonvalue', 'value': '[]'}
    data_source.store()
    assert NamedDataSource.get(1).slug == 'foobar'

    FormDef.wipe()
    app = login(get_app(pub))

    resp = app.get('/backoffice/settings/data-sources/1/')
    resp = resp.click(href='edit')
    assert resp.forms[0]['name'].value == 'foobar'
    resp.forms[0]['slug'] = 'foo_bar'
    resp = resp.forms[0].submit('submit')
    assert resp.location == 'http://example.net/backoffice/settings/data-sources/1/'
    assert NamedDataSource.get(1).slug == 'foo_bar'

    data_source = NamedDataSource(name='barfoo')
    data_source.data_source = {'type': 'jsonvalue', 'value': '[]'}
    data_source.store()

    resp = app.get('/backoffice/settings/data-sources/1/')
    resp = resp.click(href='edit')
    assert resp.forms[0]['name'].value == 'foobar'
    resp.forms[0]['slug'] = 'barfoo'
    resp = resp.forms[0].submit('submit')
    assert 'This value is already used' in resp.text

    resp.forms[0]['slug'] = 'foobar'
    resp = resp.forms[0].submit('submit')
    assert resp.location == 'http://example.net/backoffice/settings/data-sources/1/'


def test_data_sources_in_use_edit_slug(pub):
    create_superuser(pub)
    NamedDataSource.wipe()
    data_source = NamedDataSource(name='foobar')
    data_source.data_source = {'type': 'jsonvalue', 'value': '[]'}
    data_source.store()
    assert NamedDataSource.get(1).slug == 'foobar'

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.ItemField(id='0', label='string', data_source={'type': 'foobar'}),
    ]
    formdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/settings/data-sources/1/')
    resp = resp.click(href='edit')
    assert 'form_slug' not in resp.form.fields
    resp = resp.form.submit('submit')

    formdef.fields = []
    formdef.store()
    resp = app.get('/backoffice/settings/data-sources/1/')
    resp = resp.click(href='edit')
    assert 'form_slug' in resp.text
    resp = resp.form.submit('submit')


@mock.patch('wcs.data_sources_agendas.collect_agenda_data')
def test_data_sources_agenda_refresh(mock_collect, pub, chrono_url):
    create_superuser(pub)
    NamedDataSource.wipe()

    mock_collect.return_value = [
        {
            'slug': 'events-a',
            'text': 'Events A',
            'url': 'http://chrono.example.net/api/agenda/events-A/datetimes/',
        },
        {
            'slug': 'events-b',
            'text': 'Events B',
            'url': 'http://chrono.example.net/api/agenda/events-B/datetimes/',
        },
    ]

    app = login(get_app(pub))
    resp = app.get('/backoffice/settings/data-sources/sync-agendas')
    assert resp.location == 'http://example.net/backoffice/settings/data-sources/'
    resp = resp.follow()
    assert 'Agendas will be updated in the background.' in resp.text
    assert NamedDataSource.count() == 2


def test_datasource_documentation(pub):
    create_superuser(pub)

    NamedDataSource.wipe()
    data_source = NamedDataSource(name='foobar')
    data_source.store()

    app = login(get_app(pub))

    resp = app.get(data_source.get_admin_url())
    assert resp.pyquery('.documentation[hidden]')
    resp = app.post_json(data_source.get_admin_url() + 'update-documentation', {'content': '<p>doc</p>'})
    assert resp.json == {'err': 0, 'empty': False, 'changed': True}
    data_source.refresh_from_storage()
    assert data_source.documentation == '<p>doc</p>'
    resp = app.get(data_source.get_admin_url())
    assert resp.pyquery('.documentation:not([hidden])')


def test_datasource_invalidate_cache(pub):
    create_superuser(pub)

    NamedDataSource.wipe()
    data_source = NamedDataSource(name='foobar')
    data_source.data_source = {'type': 'json', 'value': 'http://some.url'}
    data_source.cache_duration = '120'
    data_source.store()
    timestamp = data_source.extended_data_source['storage_timestamp']
    snap = pub.snapshot_class.select(order_by='-timestamp', limit=1)[0]

    app = login(get_app(pub))

    resp = app.get(data_source.get_admin_url())
    resp = resp.click('Invalidate cache').follow()
    assert 'This datasource cache has been invalidated.' in resp.text
    # no new snapshot
    assert pub.snapshot_class.select(order_by='-timestamp', limit=1)[0].id == snap.id
    data_source.refresh_from_storage()
    assert data_source.extended_data_source['storage_timestamp'] != timestamp


def test_datasource_category_management_roles(pub, backoffice_user, backoffice_role):
    app = login(get_app(pub), username='backoffice-user', password='backoffice-user')
    app.get('/backoffice/forms/', status=403)

    DataSourceCategory.wipe()
    Category.wipe()

    form_cat = Category(name='formcat')
    form_cat.management_roles = [backoffice_role]
    form_cat.store()
    app.get('/backoffice/forms/', status=200)
    app.get('/backoffice/forms/data-sources/', status=403)

    cat = DataSourceCategory(name='Foo')
    cat.store()

    app.get('/backoffice/forms/data-sources/', status=403)

    NamedDataSource.wipe()
    data_source = NamedDataSource(name='data source title')
    data_source.category_id = cat.id
    data_source.store()
    data_source2 = NamedDataSource(name='data2 source title')
    data_source2.store()

    cat = DataSourceCategory(name='Bar')
    cat.management_roles = [backoffice_role]
    cat.store()

    resp = app.get('/backoffice/forms/data-sources/')
    assert 'Foo' not in resp.text  # not a category managed by user
    assert 'data source title' not in resp.text  # data source in that category
    assert 'Bar' not in resp.text  # not yet any data source in this category

    resp = resp.click('New Data Source')
    resp.form['name'] = 'data source in category'
    resp.form['data_source$type'] = 'jsonvalue'
    resp.form['data_source$value'] = '[]'
    assert len(resp.form['category_id'].options) == 1  # single option
    assert resp.form['category_id'].value == cat.id  # the category managed by user
    resp = resp.form.submit('submit').follow()
    ds = NamedDataSource.get_by_slug('data_source_in_category')

    # check category select only let choose one
    resp = app.get(ds.get_admin_url())
    resp = resp.click(href='edit')
    assert len(resp.forms[0]['category_id'].options) == 1  # single option
    assert resp.forms[0]['category_id'].value == cat.id  # the category managed by user

    resp = app.get('/backoffice/forms/data-sources/')
    assert 'Bar' in resp.text  # now there's a data source in this category
    assert 'data source in category' in resp.text

    # no access to subdirectories
    assert 'href="categories/"' not in resp.text
    app.get('/backoffice/forms/data-sources/categories/', status=403)

    # no import into other category
    data_source_xml = ET.tostring(data_source.export_to_xml(include_id=True))
    resp = resp.click(href='import')
    resp.forms[0]['file'] = Upload('data_source.wcs', data_source_xml)
    resp = resp.forms[0].submit()
    assert 'Invalid File (unauthorized category)' in resp.text

    resp = app.get('/backoffice/studio/', status=200)
    resp.click('Forms', index=0)
    resp.click('Data sources', index=0)
    with pytest.raises(IndexError):
        resp.click('Block of fields', index=0)
    with pytest.raises(IndexError):
        resp.click('Mail templates', index=0)
    with pytest.raises(IndexError):
        resp.click('Comment templates', index=0)


def test_datasource_by_slug(pub):
    NamedDataSource.wipe()
    create_superuser(pub)
    app = login(get_app(pub))

    nds = NamedDataSource()
    nds.name = 'source title'
    nds.store()

    assert app.get('/backoffice/forms/data-sources/by-slug/source_title').location == nds.get_admin_url()
    assert app.get('/backoffice/forms/data-sources/by-slug/xxx', status=404)
