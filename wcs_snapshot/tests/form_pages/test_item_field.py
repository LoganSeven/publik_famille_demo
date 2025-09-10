import json
import os
from unittest import mock

import pytest
import responses
from django.core.cache import cache
from webtest import Hidden

import wcs.qommon.storage as st
from wcs import fields
from wcs.admin.settings import UserFieldsFormDef
from wcs.carddef import CardDef
from wcs.categories import Category
from wcs.data_sources import NamedDataSource
from wcs.formdef import FormDef
from wcs.logged_errors import LoggedError
from wcs.qommon.misc import ConnectionError
from wcs.qommon.upload_storage import PicklableUpload
from wcs.sql import TransientData

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


@pytest.fixture
def error_email(pub):
    pub.cfg['debug'] = {'error_email': 'errors@localhost.invalid'}
    pub.write_cfg()
    pub.set_config()


def test_form_item_data_source_field_submit(pub):
    def submit_item_data_source_field(ds):
        formdef = create_formdef()
        formdef.fields = [fields.ItemField(id='0', label='string', data_source=ds)]
        formdef.store()
        resp = get_app(pub).get('/test/')
        formdef.data_class().wipe()
        resp.forms[0]['f0'] = '1'
        resp = resp.forms[0].submit('submit')
        assert 'Check values then click submit.' in resp.text
        resp = resp.forms[0].submit('submit')
        assert resp.status_int == 302
        resp = resp.follow()
        assert 'The form has been recorded' in resp.text
        assert formdef.data_class().count() == 1
        data_id = formdef.data_class().select()[0].id
        return formdef.data_class().get(data_id).data

    ds = {
        'type': 'jsonvalue',
        'value': '[{"id": "1", "text": "un"}, {"id": "2", "text": "deux"}]',
    }
    assert submit_item_data_source_field(ds) == {'0': '1', '0_display': 'un'}

    ds['value'] = json.dumps([{'id': '1', 'text': 'un'}, {'id': '2', 'text': 'deux'}])
    assert submit_item_data_source_field(ds) == {'0': '1', '0_display': 'un'}

    ds['value'] = json.dumps(
        [{'id': '1', 'text': 'un', 'more': 'foo'}, {'id': '2', 'text': 'deux', 'more': 'bar'}]
    )
    assert submit_item_data_source_field(ds) == {
        '0': '1',
        '0_display': 'un',
        '0_structured': {'id': '1', 'text': 'un', 'more': 'foo'},
    }

    # numeric identifiers
    ds['value'] = json.dumps([{'id': 1, 'text': 'un'}, {'id': 2, 'text': 'deux'}])
    assert submit_item_data_source_field(ds) == {'0': '1', '0_display': 'un'}

    # json source
    ds = {
        'type': 'json',
        'value': 'http://www.example.net/plop',
    }

    with responses.RequestsMock() as rsps:
        rsps.get(
            'http://www.example.net/plop',
            json={'data': [{'id': '1', 'text': 'un'}, {'id': '2', 'text': 'deux'}]},
        )
        assert submit_item_data_source_field(ds) == {'0': '1', '0_display': 'un'}

    # numeric identifiers
    with responses.RequestsMock() as rsps:
        rsps.get(
            'http://www.example.net/plop', json={'data': [{'id': 1, 'text': 'un'}, {'id': 2, 'text': 'deux'}]}
        )
        assert submit_item_data_source_field(ds) == {'0': '1', '0_display': 'un'}


@pytest.mark.parametrize('fail_after_count_page', range(2, 8))
@pytest.mark.parametrize('fail_after_count_validation', range(0, 2))
@responses.activate
def test_form_item_data_source_error(pub, monkeypatch, fail_after_count_page, fail_after_count_validation):
    NamedDataSource.wipe()
    data_source = NamedDataSource(name='foobar')
    data_source.data_source = {'type': 'json', 'value': 'http://www.example.net/plop'}
    data_source.id_parameter = 'id'
    data_source.store()

    normal_get_structured_value = NamedDataSource.get_structured_value

    class failing_get_structured_value:
        def __init__(self, fail_after_count):
            self.fail_after_count = fail_after_count
            self.count = 0

        def __call__(self, *args):
            import inspect

            for frame in inspect.stack():
                if frame.function in ['store_display_value', 'store_structured_value']:
                    count = self.count
                    self.count += 1
                    if count >= self.fail_after_count:
                        return None
            return normal_get_structured_value(*args)

        @property
        def method(self):
            def f(*args):
                return self(*args)

            return f

    responses.get(
        'http://www.example.net/plop', json={'data': [{'id': '1', 'text': 'un'}, {'id': '2', 'text': 'deux'}]}
    )

    formdef = create_formdef()
    formdef.enable_tracking_codes = True
    formdef.fields = [
        fields.PageField(id='0', label='1st page'),
        fields.ItemField(id='1', label='string', data_source={'type': 'foobar'}),
    ]

    formdef.store()
    resp = get_app(pub).get('/test/')
    formdef.data_class().wipe()
    resp.forms[0]['f1'] = '1'

    # fail in get_structured_value
    monkeypatch.setattr(
        NamedDataSource, 'get_structured_value', failing_get_structured_value(fail_after_count_page).method
    )
    resp = resp.forms[0].submit('submit')
    assert resp.pyquery('.global-errors summary').text() == 'Technical error, please try again.'
    assert resp.pyquery('.global-errors p').text() == 'datasource is unavailable (field id: 1)'

    # fix transient failure
    monkeypatch.setattr(NamedDataSource, 'get_structured_value', normal_get_structured_value)
    resp = resp.forms[0].submit('submit')
    assert 'Check values then click submit.' in resp.text

    # fail in get_structured_value
    monkeypatch.setattr(
        NamedDataSource,
        'get_structured_value',
        failing_get_structured_value(fail_after_count_validation).method,
    )
    resp = resp.forms[0].submit('submit')
    assert formdef.data_class().count([st.NotEqual('status', 'draft')]) == 0
    assert resp.pyquery('.global-errors summary').text() == 'Technical error, please try again.'
    assert resp.pyquery('.global-errors p').text() == 'datasource is unavailable (field id: 1)'

    # fix transient failure
    monkeypatch.setattr(NamedDataSource, 'get_structured_value', normal_get_structured_value)
    resp = resp.forms[0].submit('submit')
    assert resp.status_int == 302
    resp = resp.follow()
    assert 'The form has been recorded' in resp.text
    assert formdef.data_class().count([st.NotEqual('status', 'draft')]) == 1
    assert formdef.data_class().count([st.Equal('status', 'draft')]) == 0
    data_id = formdef.data_class().select()[0].id
    assert formdef.data_class().get(data_id).data


@pytest.mark.parametrize('fail_after_count_page', range(2, 8))
@responses.activate
def test_form_item_data_source_error_no_confirmation(pub, monkeypatch, fail_after_count_page):
    NamedDataSource.wipe()
    data_source = NamedDataSource(name='foobar')
    data_source.data_source = {'type': 'json', 'value': 'http://www.example.net/plop'}
    data_source.id_parameter = 'id'
    data_source.store()

    normal_get_structured_value = NamedDataSource.get_structured_value

    class failing_get_structured_value:
        def __init__(self, fail_after_count):
            self.fail_after_count = fail_after_count
            self.count = 0

        def __call__(self, *args):
            import inspect

            for frame in inspect.stack():
                if frame.function in ['store_display_value', 'store_structured_value']:
                    count = self.count
                    self.count += 1
                    if count >= self.fail_after_count:
                        return None
            return normal_get_structured_value(*args)

        @property
        def method(self):
            def f(*args):
                return self(*args)

            return f

    responses.get(
        'http://www.example.net/plop', json={'data': [{'id': '1', 'text': 'un'}, {'id': '2', 'text': 'deux'}]}
    )

    formdef = create_formdef()
    formdef.confirmation = False
    formdef.fields = [
        fields.PageField(id='0', label='1st page'),
        fields.ItemField(id='1', label='string', data_source={'type': 'foobar'}),
    ]

    formdef.store()
    resp = get_app(pub).get('/test/')
    formdef.data_class().wipe()
    resp.forms[0]['f1'] = '1'

    # fail in get_structured_value
    monkeypatch.setattr(
        NamedDataSource, 'get_structured_value', failing_get_structured_value(fail_after_count_page).method
    )
    resp = resp.forms[0].submit('submit')
    assert resp.pyquery('.global-errors summary').text() == 'Technical error, please try again.'
    assert resp.pyquery('.global-errors p').text() == 'datasource is unavailable (field id: 1)'

    # fix transient failure
    monkeypatch.setattr(NamedDataSource, 'get_structured_value', normal_get_structured_value)
    resp = resp.forms[0].submit('submit')
    assert resp.status_int == 302
    resp = resp.follow()
    assert 'The form has been recorded' in resp.text
    assert formdef.data_class().count() == 1
    data_id = formdef.data_class().select()[0].id
    assert formdef.data_class().get(data_id).data


def test_form_jsonp_item_field(http_requests, pub):
    formdef = create_formdef()
    formdef.fields = [
        fields.ItemField(
            id='1',
            label='string',
            data_source={'type': 'jsonp', 'value': 'http://remote.example.net/jsonp'},
        ),
    ]
    formdef.store()
    formdef.data_class().wipe()

    resp = get_app(pub).get('/test/')
    assert 'data-select2-url="http://remote.example.net/jsonp"' in resp.text
    assert 'select2.min.js' in resp.text


@responses.activate
def test_form_autosave_item_field_data_source_error(pub):
    ds = {'type': 'json', 'value': 'http://www.example.net/plop'}
    formdef = create_formdef()
    formdef.fields = [
        fields.ItemField(id='1', label='string', data_source=ds),
    ]
    formdef.enable_tracking_codes = True
    formdef.store()

    responses.get(
        'http://www.example.net/plop', json={'data': [{'id': '1', 'text': 'un'}, {'id': '2', 'text': 'deux'}]}
    )

    formdef.data_class().wipe()
    app = get_app(pub)
    resp = app.get('/test/')
    resp.form['f1'] = '1'  # not a valid email

    # make the ds fails
    with mock.patch.object(NamedDataSource, 'get_structured_value', lambda *args: None):
        autosave_resp = app.post('/test/autosave', params=resp.form.submit_fields())
    assert autosave_resp.json == {
        'reason': 'form deserialization failed: no matching value in datasource (field id: 1, value: \'1\')',
        'result': 'error',
    }

    autosave_resp = app.post('/test/autosave', params=resp.form.submit_fields())
    assert autosave_resp.json['result'] == 'success'


def test_item_field_from_cards(pub):
    create_user(pub)
    formdef = create_formdef()
    formdef.data_class().wipe()

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
    formdef.fields = [fields.ItemField(id='0', label='string', data_source=ds, display_disabled_items=True)]
    formdef.store()

    resp = get_app(pub).get('/test/')
    assert resp.form['f0'].options == [
        ('2', False, 'bar'),
        ('3', False, 'baz'),
        ('1', False, 'foo'),
    ]
    resp.form['f0'] = '2'
    resp = resp.form.submit('submit')  # -> validation page
    resp = resp.form.submit('submit')  # -> submit
    assert formdef.data_class().select()[0].data['0'] == '2'
    assert formdef.data_class().select()[0].data['0_display'] == 'bar'
    assert formdef.data_class().select()[0].data['0_structured']['name'] == 'bar'


def test_item_field_from_cards_id_identifier(pub):
    create_user(pub)
    formdef = create_formdef()
    formdef.data_class().wipe()

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'items'
    carddef.digest_templates = {'default': '{{form_var_name}}'}
    carddef.fields = [
        fields.StringField(id='0', label='string', varname='name'),
        fields.StringField(id='1', label='string', varname='id'),
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
    formdef.fields = [fields.ItemField(id='0', label='string', data_source=ds, display_disabled_items=True)]
    formdef.store()

    resp = get_app(pub).get('/test/')
    assert resp.form['f0'].options == [
        ('2', False, 'bar'),
        ('3', False, 'baz'),
        ('1', False, 'foo'),
    ]
    resp.form['f0'] = '2'
    resp = resp.form.submit('submit')  # -> validation page
    resp = resp.form.submit('submit')  # -> submit
    assert formdef.data_class().select()[0].data['0'] == '2'
    assert formdef.data_class().select()[0].data['0_display'] == 'bar'
    assert formdef.data_class().select()[0].data['0_structured']['name'] == 'bar'


def test_item_field_from_cards_custom_identifier(pub):
    create_user(pub)
    formdef = create_formdef()
    formdef.data_class().wipe()

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'items'
    carddef.digest_templates = {'default': '{{form_var_name}}'}
    carddef.id_template = '{{form_var_custom_id}}'
    carddef.fields = [
        fields.StringField(id='0', label='string', varname='name'),
        fields.StringField(id='1', label='string', varname='custom_id'),
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
    formdef.fields = [fields.ItemField(id='0', label='string', data_source=ds, display_disabled_items=True)]
    formdef.store()

    resp = get_app(pub).get('/test/')
    assert resp.form['f0'].options == [
        ('attr1', False, 'bar'),
        ('attr2', False, 'baz'),
        ('attr0', False, 'foo'),
    ]
    resp.form['f0'] = 'attr1'
    resp = resp.form.submit('submit')  # -> validation page
    resp = resp.form.submit('submit')  # -> submit
    assert formdef.data_class().select()[0].data['0'] == 'attr1'
    assert formdef.data_class().select()[0].data['0_display'] == 'bar'
    assert formdef.data_class().select()[0].data['0_structured']['name'] == 'bar'


def test_item_field_from_cards_then_comment_related_card(pub):
    # https://dev.entrouvert.org/issues/58292
    create_user(pub)
    formdef = create_formdef()
    formdef.data_class().wipe()

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

    carddef2 = CardDef()
    carddef2.name = 'others'
    carddef2.fields = [fields.ItemField(id='0', varname='card1', data_source=ds)]
    carddef2.store()
    carddata2 = carddef2.data_class()()
    carddata2.data = {'0': str(carddata.id), '0_display': 'baz'}
    carddata2.just_created()
    carddata2.store()

    formdef.fields = [
        fields.PageField(id='1', label='1st page'),
        fields.ItemField(id='0', label='string', data_source=ds, display_disabled_items=True, varname='card'),
        fields.PageField(id='2', label='2nd page'),
        fields.CommentField(
            id='3',
            label='<p>card value: {{ cards|objects:"others"|filter_by:"card1"|filter_value:form_var_card|first|get:"form_number" }}</p>',
        ),
    ]
    formdef.store()

    resp = get_app(pub).get('/test/')
    resp.form['f0'] = '3'
    resp = resp.form.submit('submit')  # -> second page
    assert 'card value: %s' % carddata2.get_display_id() in resp.text


def test_item_field_from_custom_view_on_cards(pub):
    pub.role_class.wipe()
    pub.custom_view_class.wipe()

    user = create_user(pub)
    role = pub.role_class(name='xxx')
    role.store()
    user.roles = [role.id]
    user.is_admin = True
    user.store()

    formdef = create_formdef()
    formdef.data_class().wipe()

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'items'
    carddef.digest_templates = {'default': '{{form_var_attr}}'}
    carddef.workflow_roles = {'_editor': user.roles[0]}
    carddef.fields = [
        fields.ItemField(id='0', label='item', varname='item', items=['foo', 'bar', 'baz']),
        fields.StringField(id='1', label='string', varname='attr'),
    ]
    carddef.store()
    carddef.data_class().wipe()
    baz_ids = set()
    for i, value in enumerate(['foo', 'bar', 'baz'] * 10):
        carddata = carddef.data_class()()
        carddata.data = {
            '0': value,
            '0_display': value,
            '1': 'attr%s' % (i + 1),
        }
        carddata.just_created()
        carddata.store()
        if value == 'baz':
            baz_ids.add(str(carddata.id))

    # create custom view
    app = login(get_app(pub), username='foo', password='foo')

    resp = app.get('/backoffice/data/items/')
    assert resp.text.count('<tr') == 21  # thead + 20 items (max per page)
    resp.forms['listing-settings']['filter-0'].checked = True
    resp = resp.forms['listing-settings'].submit().follow()

    resp.forms['listing-settings']['filter-0-value'] = 'baz'
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.text.count('<tr') == 11  # thead + 10 items

    resp.forms['save-custom-view']['title'] = 'as data source'
    resp.forms['save-custom-view']['visibility'] = 'datasource'
    resp = resp.forms['save-custom-view'].submit()

    custom_view = pub.custom_view_class.select()[0]

    # use custom view as source
    ds = {'type': 'carddef:%s:%s' % (carddef.url_name, custom_view.slug)}
    formdef.fields = [fields.ItemField(id='0', label='string', data_source=ds, display_disabled_items=True)]
    formdef.store()

    resp = get_app(pub).get('/test/')
    assert len(resp.form['f0'].options) == 10
    baz_id = list(baz_ids)[0]
    assert {x[0] for x in resp.form['f0'].options} == baz_ids
    resp.form['f0'].value = baz_id
    resp = resp.form.submit('submit')  # -> validation page
    resp = resp.form.submit('submit')  # -> submit
    assert formdef.data_class().select()[0].data['0'] == baz_id
    assert formdef.data_class().select()[0].data['0_display'] == 'attr%s' % baz_id
    assert formdef.data_class().select()[0].data['0_structured']['item'] == 'baz'

    # give custom view it a custom digest
    formdef.data_class().wipe()
    carddef.digest_templates['custom-view:%s' % custom_view.slug] = 'X{{form_var_attr}}Y'
    carddef.store()
    # compute digests
    for carddata in carddef.data_class().select():
        carddata.store()

    app = get_app(pub)
    resp = app.get('/test/')
    assert len(resp.form['f0'].options) == 10
    assert {x[0] for x in resp.form['f0'].options} == baz_ids
    resp.form['f0'].value = baz_id
    resp = resp.form.submit('submit')  # -> validation page
    resp = resp.form.submit('submit')  # -> submit
    assert formdef.data_class().select()[0].data['0'] == baz_id
    assert formdef.data_class().select()[0].data['0_display'] == 'Xattr%sY' % baz_id

    resp = app.get(formdef.data_class().select()[0].get_url())
    assert resp.pyquery('.field-type-item .value').text() == 'Xattr%sY' % baz_id
    assert resp.pyquery('.field-type-item .value a').length == 0  # no link to card

    # check criteria without value are ignored
    custom_view.filters['filter-0-value'] = ''
    custom_view.store()
    formdef.fields = [
        fields.ItemField(
            id='0', label='string', data_source=ds, display_disabled_items=True, display_mode='autocomplete'
        )
    ]
    formdef.store()

    resp = app.get('/test/')
    autocomplete_url = resp.pyquery('[data-select2-url]').attr['data-select2-url']
    assert app.get(autocomplete_url).json['data']

    # and check undefined criterias are ignored
    del custom_view.filters['filter-0-value']
    custom_view.store()
    formdef.fields = [
        fields.ItemField(
            id='0', label='string', data_source=ds, display_disabled_items=True, display_mode='autocomplete'
        )
    ]
    formdef.store()

    resp = app.get('/test/')
    autocomplete_url = resp.pyquery('[data-select2-url]').attr['data-select2-url']
    assert app.get(autocomplete_url).json['data']

    # change digests
    carddef.digest_templates['custom-view:%s' % custom_view.slug] = 'Y{{form_var_attr}}Z'
    carddef.store()
    for carddata in carddef.data_class().select():
        carddata.store()
    pub.process_after_jobs()

    resp = app.get(formdef.data_class().select()[0].get_url())
    assert resp.pyquery('.field-type-item .value').text() == 'Yattr%sZ' % baz_id

    # remove card, the value is still displayed
    carddef.data_class().wipe()
    resp = app.get(formdef.data_class().select()[0].get_url())
    assert resp.pyquery('.field-type-item .value').text() == 'Yattr%sZ' % baz_id


def test_item_field_from_custom_view_on_cards_filter_status(pub):
    pub.role_class.wipe()
    pub.custom_view_class.wipe()

    user = create_user(pub)
    role = pub.role_class(name='xxx')
    role.store()
    user.roles = [role.id]
    user.is_admin = True
    user.store()

    formdef = create_formdef()
    formdef.data_class().wipe()

    card_workflow = CardDef.get_default_workflow()
    st1 = card_workflow.add_status('Status1', 'st1')
    card_workflow.id = None
    card_workflow.store()

    CardDef.wipe()
    carddef = CardDef()
    carddef.workflow_id = card_workflow.id
    carddef.name = 'items'
    carddef.digest_templates = {'default': '{{form_var_attr}}'}
    carddef.workflow_roles = {'_editor': user.roles[0]}
    carddef.fields = [
        fields.ItemField(id='0', label='item', varname='item', items=['foo', 'bar', 'baz']),
        fields.StringField(id='1', label='string', varname='attr'),
    ]
    carddef.store()
    carddef.data_class().wipe()
    for i, value in enumerate(['foo', 'bar', 'baz']):
        carddata = carddef.data_class()()
        carddata.data = {
            '0': value,
            '0_display': value,
            '1': 'attr%s' % (i + 1),
        }
        carddata.just_created()
        carddata.store()

    carddata.jump_status(st1.id)
    carddata.store()

    # create custom view
    app = login(get_app(pub), username='foo', password='foo')

    resp = app.get('/backoffice/data/items/')
    resp.forms['listing-settings']['filter-status'].checked = True
    resp = resp.forms['listing-settings'].submit().follow()
    resp.forms['listing-settings']['filter-operator'].value = 'ne'
    resp.forms['listing-settings']['filter'].value = 'st1'
    resp = resp.forms['listing-settings'].submit().follow()
    assert resp.pyquery('tbody tr').length == 2

    resp.forms['save-custom-view']['title'] = 'as data source'
    resp.forms['save-custom-view']['visibility'] = 'datasource'
    resp = resp.forms['save-custom-view'].submit()

    custom_view = pub.custom_view_class.select()[0]
    assert custom_view.filters == {'filter-operator': 'ne', 'filter': 'st1', 'filter-status': 'on'}

    # use custom view as source
    ds = {'type': 'carddef:%s:%s' % (carddef.url_name, custom_view.slug)}
    formdef.fields = [fields.ItemField(id='0', label='string', data_source=ds, display_disabled_items=True)]
    formdef.store()

    resp = get_app(pub).get('/test/')
    assert len(resp.form['f0'].options) == 2
    assert {x[2] for x in resp.form['f0'].options} == {'attr1', 'attr2'}

    custom_view.filters['filter-operator'] = 'eq'
    custom_view.store()
    resp = get_app(pub).get('/test/')
    assert [x[2] for x in resp.form['f0'].options] == ['attr3']

    custom_view.filters['filter'] = 'all'
    custom_view.store()
    resp = get_app(pub).get('/test/')
    assert len(resp.form['f0'].options) == 3

    custom_view.filters['filter'] = 'all'
    custom_view.filters['filter-operator'] = 'ne'
    custom_view.store()
    resp = get_app(pub).get('/test/')
    assert len(resp.form['f0'].options) == 1
    assert [x[2] for x in resp.form['f0'].options] == ['---']


def test_item_field_with_disabled_items(http_requests, pub):
    create_user(pub)
    formdef = create_formdef()
    formdef.data_class().wipe()
    ds = {'type': 'json', 'value': 'http://remote.example.net/json'}
    formdef.fields = [fields.ItemField(id='0', label='string', data_source=ds, display_disabled_items=True)]
    formdef.store()

    with responses.RequestsMock() as rsps:
        rsps.get(
            'http://remote.example.net/json',
            json={'data': [{'id': '1', 'text': 'hello'}, {'id': '2', 'text': 'world'}]},
        )
        resp = get_app(pub).get('/test/')
        resp.form['f0'] = '1'
        resp.form['f0'] = '2'
        resp = resp.form.submit('submit')  # -> validation page
        resp = resp.form.submit('submit')  # -> submit
        assert formdef.data_class().select()[0].data['0'] == '2'
        assert formdef.data_class().select()[0].data['0_display'] == 'world'

    formdef.data_class().wipe()

    with responses.RequestsMock() as rsps:
        rsps.get(
            'http://remote.example.net/json',
            json={'data': [{'id': '1', 'text': 'hello', 'disabled': True}, {'id': '2', 'text': 'world'}]},
        )
        resp = get_app(pub).get('/test/')
        pq = resp.pyquery.remove_namespaces()
        assert pq('option[disabled=disabled][value="1"]').text() == 'hello'
        resp.form['f0'] = '1'
        resp.form['f0'] = '2'
        resp = resp.form.submit('submit')  # -> validation page
        resp = resp.form.submit('submit')  # -> submit
        assert formdef.data_class().select()[0].data['0'] == '2'
        assert formdef.data_class().select()[0].data['0_display'] == 'world'

        resp = get_app(pub).get('/test/')
        pq = resp.pyquery.remove_namespaces()
        assert pq('option[disabled=disabled][value="1"]').text() == 'hello'
        resp.form['f0'] = '1'
        resp = resp.form.submit('submit')  # -> validation page
        assert 'There were errors processing the form' in resp.text

    formdef.data_class().wipe()
    formdef.fields = [fields.ItemField(id='0', label='string', data_source=ds, display_disabled_items=False)]
    formdef.store()

    with responses.RequestsMock() as rsps:
        rsps.get(
            'http://remote.example.net/json',
            json={'data': [{'id': '1', 'text': 'hello', 'disabled': True}, {'id': '2', 'text': 'world'}]},
        )
        resp = get_app(pub).get('/test/')
        pq = resp.pyquery.remove_namespaces()
        assert len(pq('option[disabled=disabled][value="1"]')) == 0
        resp.form['f0'] = '2'
        resp = resp.form.submit('submit')  # -> validation page
        resp = resp.form.submit('submit')  # -> submit
        assert formdef.data_class().select()[0].data['0'] == '2'
        assert formdef.data_class().select()[0].data['0_display'] == 'world'

    formdef.data_class().wipe()
    formdef.fields = [
        fields.ItemField(
            id='0',
            label='string',
            data_source=ds,
            display_mode='radio',
            display_disabled_items=True,
        )
    ]
    formdef.store()

    with responses.RequestsMock() as rsps:
        rsps.get(
            'http://remote.example.net/json',
            json={'data': [{'id': '1', 'text': 'hello'}, {'id': '2', 'text': 'world'}]},
        )
        resp = get_app(pub).get('/test/')
        resp.form['f0'] = '1'
        resp.form['f0'] = '2'
        resp = resp.form.submit('submit')  # -> validation page
        resp = resp.form.submit('submit')  # -> submit
        assert formdef.data_class().select()[0].data['0'] == '2'
        assert formdef.data_class().select()[0].data['0_display'] == 'world'

    formdef.data_class().wipe()

    with responses.RequestsMock() as rsps:
        rsps.get(
            'http://remote.example.net/json',
            json={'data': [{'id': '1', 'text': 'hello', 'disabled': True}, {'id': '2', 'text': 'world'}]},
        )
        resp = get_app(pub).get('/test/')
        pq = resp.pyquery.remove_namespaces()
        assert len(pq('input[name="f0"][disabled=disabled][value="1"]')) == 1
        resp.form['f0'] = '1'
        resp.form['f0'] = '2'
        resp = resp.form.submit('submit')  # -> validation page
        resp = resp.form.submit('submit')  # -> submit
        assert formdef.data_class().select()[0].data['0'] == '2'
        assert formdef.data_class().select()[0].data['0_display'] == 'world'

        resp = get_app(pub).get('/test/')
        pq = resp.pyquery.remove_namespaces()
        assert len(pq('input[name="f0"][disabled=disabled][value="1"]')) == 1
        resp.form['f0'] = '1'
        resp = resp.form.submit('submit')  # -> validation page
        assert 'There were errors processing the form' in resp.text


def test_item_field_autocomplete_json_source(http_requests, pub, error_email, emails):
    create_user(pub)
    formdef = create_formdef()
    formdef.data_class().wipe()

    NamedDataSource.wipe()
    data_source = NamedDataSource(name='foobar')
    data_source.data_source = {'type': 'json', 'value': 'http://remote.example.net/json'}
    data_source.store()

    formdef.fields = [
        fields.ItemField(
            id='0',
            label='string',
            data_source={'type': 'foobar'},
            display_mode='autocomplete',
        ),
    ]
    formdef.store()

    with responses.RequestsMock() as rsps:
        data = {
            'data': [
                {'id': '1', 'text': 'hello', 'extra': 'foo'},
                {'id': '2', 'text': 'world', 'extra': 'bar'},
            ]
        }
        rsps.get('http://remote.example.net/json', json=data)
        resp = get_app(pub).get('/test/')
        assert 'data-autocomplete="true"' in resp.text
        assert resp.form['f0'].value == '1'
        resp.form['f0'] = '2'
        resp = resp.form.submit('submit')  # -> validation page
        resp = resp.form.submit('submit')  # -> submit
        assert formdef.data_class().select()[0].data['0'] == '2'
        assert formdef.data_class().select()[0].data['0_display'] == 'world'
        assert formdef.data_class().select()[0].data['0_structured'] == data['data'][1]

    # check hint is displayed outside
    formdef.fields[0].hint = 'help text'
    formdef.fields[0].use_hint_as_first_option = False
    formdef.store()
    with responses.RequestsMock() as rsps:
        data = {
            'data': [
                {'id': '1', 'text': 'hello', 'extra': 'foo'},
                {'id': '2', 'text': 'world', 'extra': 'bar'},
            ]
        }
        rsps.get('http://remote.example.net/json', json=data)
        resp = get_app(pub).get('/test/')
        assert 'data-autocomplete="true"' in resp.text
        assert 'data-hint="help text"' not in resp.text
        assert resp.pyquery('[data-field-id="0"] .hint').text() == 'help text'

    # check hint is displayed within
    formdef.fields[0].hint = 'help text'
    formdef.fields[0].use_hint_as_first_option = True
    formdef.store()
    with responses.RequestsMock() as rsps:
        data = {
            'data': [
                {'id': '1', 'text': 'hello', 'extra': 'foo'},
                {'id': '2', 'text': 'world', 'extra': 'bar'},
            ]
        }
        rsps.get('http://remote.example.net/json', json=data)
        resp = get_app(pub).get('/test/')
        assert 'data-autocomplete="true"' in resp.text
        assert 'data-hint="help text"' in resp.text
        assert resp.form['f0'].value == ''
        assert not resp.pyquery('[data-field-id="0"] .hint').text()

    formdef.fields[0].hint = ''
    formdef.store()

    # check with possibility of remote query
    data_source.query_parameter = 'q'
    data_source.id_parameter = 'id'
    data_source.store()

    formdef.data_class().wipe()

    app = get_app(pub)
    with responses.RequestsMock() as rsps:
        data = {
            'data': [
                {'id': '1', 'text': 'hello', 'extra': 'foo'},
                {'id': '2', 'text': 'world', 'extra': 'bar'},
            ]
        }
        resp = app.get('/test/')
        assert len(rsps.calls) == 0
        pq = resp.pyquery.remove_namespaces()
        select2_url = pq('select').attr['data-select2-url']

    with responses.RequestsMock() as rsps:
        data = {'data': [{'id': '1', 'text': 'hello', 'extra': 'foo'}]}
        rsps.get('http://remote.example.net/json', json=data)
        resp2 = app.get(select2_url + '?q=hell')
        assert len(rsps.calls) == 1
        assert rsps.calls[-1].request.url == 'http://remote.example.net/json?q=hell'
        assert resp2.json == dict(data, err=0)

        # check unauthorized access
        resp2 = get_app(pub).get(select2_url + '?q=hell', status=403)

    # check error handling in autocomplete endpoint
    formdef.data_class().wipe()

    app = get_app(pub)
    with responses.RequestsMock() as rsps:
        rsps.get('http://remote.example.net/json', body=ConnectionError('...'))
        resp = app.get('/test/')
        assert len(rsps.calls) == 0
        pq = resp.pyquery.remove_namespaces()
        select2_url = pq('select').attr['data-select2-url']

        assert emails.count() == 0
        resp2 = app.get(select2_url + '?q=hell')
        assert len(rsps.calls) == 1
        assert rsps.calls[-1].request.url == 'http://remote.example.net/json?q=hell'
        assert resp2.json == {'data': [], 'err': 1}
        assert emails.count() == 0

        data_source.notify_on_errors = True
        data_source.record_on_errors = True
        data_source.store()
        resp2 = app.get(select2_url + '?q=hell')
        assert emails.count() == 1
        assert emails.get_latest('subject') == '[ERROR] Data source: Error loading JSON data source (...)'
        assert LoggedError.count() == 1
        logged_error = LoggedError.select()[0]
        assert logged_error.workflow_id is None
        assert logged_error.summary == 'Data source: Error loading JSON data source (...)'

        data_source.notify_on_errors = False
        data_source.store()

    # simulate select2 mode, with qommon.forms.js adding an extra hidden widget
    resp.form.fields['f0_display'] = Hidden(form=resp.form, tag='input', name='f0_display', pos=10)
    resp.form['f0'].force_value('1')
    resp.form.fields['f0_display'].force_value('hello')

    with responses.RequestsMock() as rsps:
        data = {'data': [{'id': '1', 'text': 'hello', 'extra': 'foo'}]}
        rsps.get('http://remote.example.net/json', json=data)
        resp = resp.form.submit('submit')  # -> validation page
        assert len(rsps.calls) == 1
        assert rsps.calls[-1].request.url == 'http://remote.example.net/json?id=1'
        assert resp.form['f0'].value == '1'
        assert resp.form['f0_label'].value == 'hello'

    with responses.RequestsMock() as rsps:
        data = {'data': [{'id': '1', 'text': 'hello', 'extra': 'foo'}]}
        rsps.get('http://remote.example.net/json', json=data)
        resp = resp.form.submit('submit')  # -> submit
        assert len(rsps.calls) == 1
        assert rsps.calls[-1].request.url == 'http://remote.example.net/json?id=1'
        assert formdef.data_class().select()[0].data['0'] == '1'
        assert formdef.data_class().select()[0].data['0_display'] == 'hello'
        assert formdef.data_class().select()[0].data['0_structured'] == data['data'][0]

    # same thing with numeric identifiers
    formdef.data_class().wipe()
    data_source.data_source = {'type': 'json', 'value': 'http://remote.example.net/json-numeric-id'}
    data_source.store()

    app = get_app(pub)
    with responses.RequestsMock() as rsps:
        resp = app.get('/test/')
        assert len(rsps.calls) == 0
        pq = resp.pyquery.remove_namespaces()
        select2_url = pq('select').attr['data-select2-url']

    with responses.RequestsMock() as rsps:
        data = {'data': [{'id': 1, 'text': 'hello', 'extra': 'foo'}]}
        rsps.get('http://remote.example.net/json-numeric-id', json=data)
        resp2 = app.get(select2_url + '?q=hell')
        assert len(rsps.calls) == 1
        assert rsps.calls[-1].request.url == 'http://remote.example.net/json-numeric-id?q=hell'
        assert resp2.json == dict(data, err=0)

        # check unauthorized access
        resp2 = get_app(pub).get(select2_url + '?q=hell', status=403)

    # simulate select2 mode, with qommon.forms.js adding an extra hidden widget
    resp.form.fields['f0_display'] = Hidden(form=resp.form, tag='input', name='f0_display', pos=10)
    resp.form['f0'].force_value('1')
    resp.form.fields['f0_display'].force_value('hello')

    with responses.RequestsMock() as rsps:
        data = {'data': [{'id': 1, 'text': 'hello', 'extra': 'foo'}]}
        rsps.get('http://remote.example.net/json-numeric-id', json=data)
        resp = resp.form.submit('submit')  # -> validation page
        assert len(rsps.calls) == 1
        assert rsps.calls[-1].request.url == 'http://remote.example.net/json-numeric-id?id=1'
        assert resp.form['f0'].value == '1'
        assert resp.form['f0_label'].value == 'hello'

    with responses.RequestsMock() as rsps:
        data = {'data': [{'id': 1, 'text': 'hello', 'extra': 'foo'}]}
        rsps.get('http://remote.example.net/json-numeric-id', json=data)
        resp = resp.form.submit('submit')  # -> submit
        assert len(rsps.calls) == 1
        assert rsps.calls[-1].request.url == 'http://remote.example.net/json-numeric-id?id=1'
        assert formdef.data_class().select()[0].data['0'] == '1'
        assert formdef.data_class().select()[0].data['0_display'] == 'hello'
        assert formdef.data_class().select()[0].data['0_structured'] == data['data'][0]

    # same thing with signed URLs
    data_source.data_source = {'type': 'json', 'value': 'http://remote.example.net/json'}
    data_source.store()
    formdef.data_class().wipe()
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        fd.write(
            '''\
[wscall-secrets]
remote.example.net = 1234
'''
        )

    app = get_app(pub)
    with responses.RequestsMock() as rsps:
        resp = app.get('/test/')
        assert len(rsps.calls) == 0
        pq = resp.pyquery.remove_namespaces()
        select2_url = pq('select').attr['data-select2-url']

    with responses.RequestsMock() as rsps:
        data = {'data': [{'id': '1', 'text': 'hello', 'extra': 'foo'}]}
        rsps.get('http://remote.example.net/json', json=data)
        resp2 = app.get(select2_url + '?q=hell')
        assert len(rsps.calls) == 1
        assert rsps.calls[-1].request.url.startswith(
            'http://remote.example.net/json?q=hell&orig=example.net&'
        )
        assert resp2.json == dict(data, err=0)

    # simulate select2 mode, with qommon.forms.js adding an extra hidden widget
    resp.form.fields['f0_display'] = Hidden(form=resp.form, tag='input', name='f0_display', pos=10)
    resp.form['f0'].force_value('1')
    resp.form.fields['f0_display'].force_value('hello')

    with responses.RequestsMock() as rsps:
        data = {'data': [{'id': '1', 'text': 'hello', 'extra': 'foo'}]}
        rsps.get('http://remote.example.net/json', json=data)
        resp = resp.form.submit('submit')  # -> validation page
        assert len(rsps.calls) == 1
        assert rsps.calls[-1].request.url.startswith('http://remote.example.net/json?id=1&orig=example.net&')
        assert resp.form['f0'].value == '1'
        assert resp.form['f0_label'].value == 'hello'

    with responses.RequestsMock() as rsps:
        data = {'data': [{'id': '1', 'text': 'hello', 'extra': 'foo'}]}
        rsps.get('http://remote.example.net/json', json=data)
        resp = resp.form.submit('submit')  # -> submit
        assert len(rsps.calls) == 1
        assert rsps.calls[-1].request.url.startswith('http://remote.example.net/json?id=1&orig=example.net&')
        assert formdef.data_class().select()[0].data['0'] == '1'
        assert formdef.data_class().select()[0].data['0_display'] == 'hello'
        assert formdef.data_class().select()[0].data['0_structured'] == data['data'][0]

    # check with optional field
    formdef.data_class().wipe()
    formdef.fields[0].required = False
    formdef.store()

    app = get_app(pub)
    with responses.RequestsMock() as rsps:
        data = {
            'data': [
                {'id': '1', 'text': 'hello', 'extra': 'foo'},
                {'id': '2', 'text': 'world', 'extra': 'bar'},
            ]
        }
        rsps.get('http://remote.example.net/json', json=data)
        resp = app.get('/test/')
        pq = resp.pyquery.remove_namespaces()
        select2_url = pq('select').attr['data-select2-url']
        rsps.reset()

    resp = resp.form.submit('submit')  # -> validation page
    assert resp.form['f0'].value == ''
    assert resp.form['f0_label'].value == ''

    resp = resp.form.submit('submit')  # -> submit
    assert formdef.data_class().select()[0].data['0'] is None

    # check there's no crash if url is empty
    data_source.data_source = {'type': 'json', 'value': '{% if 0 %}http://remote.example.net/json{% endif %}'}
    data_source.store()

    app = get_app(pub)
    with responses.RequestsMock() as rsps:
        resp = app.get('/test/')
        assert len(rsps.calls) == 0
        pq = resp.pyquery.remove_namespaces()
        select2_url = pq('select').attr['data-select2-url']

    with responses.RequestsMock() as rsps:
        resp2 = app.get(select2_url + '?q=hell', status=403)
        assert len(rsps.calls) == 0

    # check with data, id and text attribute
    data_source.data_source = {'type': 'json', 'value': 'http://remote.example.net/json'}
    data_source.data_attribute = 'x.results'
    data_source.id_attribute = 'key'
    data_source.text_attribute = 'value'
    data_source.store()

    formdef.data_class().wipe()

    app = get_app(pub)
    with responses.RequestsMock() as rsps:
        resp = app.get('/test/')
        pq = resp.pyquery.remove_namespaces()
        select2_url = pq('select').attr['data-select2-url']

    with responses.RequestsMock() as rsps:
        data = {
            'x': {
                'results': [
                    {'key': '1', 'value': 'hello', 'extra': 'foo'},
                ]
            }
        }
        normalized_data = {
            'data': [{'id': '1', 'text': 'hello', 'extra': 'foo', 'key': '1', 'value': 'hello'}]
        }
        rsps.get('http://remote.example.net/json', json=data)
        resp2 = app.get(select2_url + '?q=hell')
        assert resp2.json == dict(normalized_data, err=0)


def test_item_field_autocomplete_jsonp_source(http_requests, pub):
    create_user(pub)
    formdef = create_formdef()
    formdef.data_class().wipe()

    NamedDataSource.wipe()
    data_source = NamedDataSource(name='foobar')
    data_source.data_source = {'type': 'jsonp', 'value': 'http://remote.example.net/jsonp'}
    data_source.store()

    formdef.fields = [
        fields.ItemField(
            id='0',
            label='string',
            data_source={'type': 'foobar'},
            display_mode='autocomplete',
        ),
    ]
    formdef.store()

    app = get_app(pub)
    with responses.RequestsMock() as rsps:
        resp = app.get('/test/')
        pq = resp.pyquery.remove_namespaces()
        select2_url = pq('select').attr['data-select2-url']
        assert select2_url == 'http://remote.example.net/jsonp'

    # simulate select2 mode, with qommon.forms.js adding an extra hidden widget
    resp.form.fields['f0_display'] = [Hidden(form=resp.form, tag='input', name='f0_display', pos=10)]
    resp.form.field_order.append(('f0_display', resp.form.fields['f0_display'][0]))
    resp.form['f0'].force_value('1')
    resp.form['f0_display'].force_value('hello')

    with responses.RequestsMock() as rsps:
        resp = resp.form.submit('submit')  # -> validation page
        assert len(rsps.calls) == 0
        assert resp.form['f0'].value == '1'
        assert resp.form['f0_label'].value == 'hello'

    with responses.RequestsMock() as rsps:
        resp = resp.form.submit('submit')  # -> submit
        assert len(rsps.calls) == 0
        assert formdef.data_class().select()[0].data['0'] == '1'
        assert formdef.data_class().select()[0].data['0_display'] == 'hello'
        # no _structured data for pure jsonp sources
        assert '0_structured' not in formdef.data_class().select()[0].data

    # check hint is displayed outside
    formdef.fields[0].hint = 'help text'
    formdef.fields[0].use_hint_as_first_option = False
    formdef.store()
    resp = get_app(pub).get('/test/')
    assert 'data-hint="help text"' not in resp.text
    assert resp.pyquery('[data-field-id="0"] .hint').text() == 'help text'

    # check hint is displayed within
    formdef.fields[0].hint = 'help text'
    formdef.fields[0].use_hint_as_first_option = True
    formdef.store()
    resp = get_app(pub).get('/test/')
    assert 'data-hint="help text"' in resp.text
    assert not resp.pyquery('[data-field-id="0"] .hint').text()


def test_item_field_autocomplete_cards_source(pub):
    create_user(pub)
    formdef = create_formdef()
    formdef.data_class().wipe()

    carddef = CardDef()
    carddef.name = 'items'
    carddef.digest_templates = {'default': '{{form_var_name}}'}
    carddef.fields = [
        fields.StringField(id='0', label='string', varname='name'),
        fields.StringField(id='1', label='string', varname='attr'),
    ]
    carddef.store()
    for i, value in enumerate(['foo', 'bar', 'baz']):
        carddata = carddef.data_class()()
        carddata.data = {
            '0': value,
            '1': 'attr%s' % i,
        }
        carddata.just_created()
        carddata.store()

    ds = {'type': 'carddef:%s' % carddef.url_name}
    formdef.fields = [
        fields.ItemField(
            id='0',
            label='string',
            data_source=ds,
            display_mode='autocomplete',
        ),
    ]
    formdef.store()

    app = get_app(pub)
    resp = app.get('/test/')
    select2_url = resp.pyquery('select').attr['data-select2-url']
    resp2 = app.get(select2_url + '?q=ba')
    assert [x['text'] for x in resp2.json['data']] == ['bar', 'baz']
    resp.form['f0'].force_value(str(resp2.json['data'][0]['id']))
    resp = resp.form.submit('submit')  # -> validation page
    resp = resp.form.submit('submit')  # -> submit
    assert formdef.data_class().select()[0].data['0'] == '2'
    assert formdef.data_class().select()[0].data['0_display'] == 'bar'


def test_item_field_autocomplete_ezt_variable_jsonp(http_requests, pub):
    formdef = create_formdef()
    formdef.data_class().wipe()

    formdef.fields = [
        fields.PageField(id='0', label='1st page'),
        fields.ItemField(
            id='1',
            label='foo',
            varname='foo',
            data_source={'type': 'jsonp', 'value': '[site_url]/foo-jsonp'},
        ),
        fields.PageField(id='2', label='2nd page'),
        fields.ItemField(
            id='3',
            label='bar',
            data_source={'type': 'jsonp', 'value': '[site_url]/foo-jsonp?a=[form_var_foo_raw]'},
        ),
    ]
    formdef.store()

    app = get_app(pub)
    with responses.RequestsMock() as rsps:
        resp = app.get('/test/')
        assert len(rsps.calls) == 0
        pq = resp.pyquery.remove_namespaces()
        select2_url = pq('select').attr['data-select2-url']
        assert select2_url == 'http://example.net/foo-jsonp'

    # simulate select2 mode, with qommon.forms.js adding an extra hidden widget
    resp.form.fields['f1_display'] = [Hidden(form=resp.form, tag='input', name='f1_display', pos=10)]
    resp.form.field_order.append(('f1_display', resp.form.fields['f1_display'][0]))
    resp.form['f1'].force_value('1')
    resp.form['f1_display'].force_value('hello')

    with responses.RequestsMock() as rsps:
        resp = resp.form.submit('submit')  # -> 2nd page
        assert len(rsps.calls) == 0
        assert resp.pyquery('select').attr['data-select2-url'] == 'http://example.net/foo-jsonp?a=1'

    # simulate select2 mode, with qommon.forms.js adding an extra hidden widget
    resp.form.fields['f3_display'] = [Hidden(form=resp.form, tag='input', name='f3_display', pos=10)]
    resp.form.field_order.append(('f3_display', resp.form.fields['f3_display'][0]))
    resp.form['f3'].force_value('2')
    resp.form['f3_display'].force_value('hello2')

    with responses.RequestsMock() as rsps:
        resp = resp.form.submit('submit')  # -> validation
        resp = resp.form.submit('submit')  # -> submit
        assert len(rsps.calls) == 0
        assert formdef.data_class().select()[0].data['1'] == '1'
        assert formdef.data_class().select()[0].data['1_display'] == 'hello'
        assert formdef.data_class().select()[0].data['3'] == '2'
        assert formdef.data_class().select()[0].data['3_display'] == 'hello2'
        # no _structured data for pure jsonp sources
        assert '1_structured' not in formdef.data_class().select()[0].data
        assert '3_structured' not in formdef.data_class().select()[0].data


def test_form_item_map_data_source(pub, http_requests):
    NamedDataSource.wipe()
    data_source = NamedDataSource(name='foobar')
    data_source.data_source = {
        'type': 'geojson',
        'value': 'http://remote.example.net/geojson',
    }
    data_source.id_property = 'id'
    data_source.label_template_property = '{{ text }}'
    data_source.cache_duration = '5'
    data_source.store()

    formdef = create_formdef()
    formdef.fields = [
        fields.ItemField(id='1', label='map', display_mode='map'),
    ]
    formdef.store()
    formdef.data_class().wipe()
    app = get_app(pub)
    resp = app.get('/test/')
    assert resp.pyquery('div[data-markers-radio-name]')[0].attrib['data-markers-url'] == ''
    assert resp.pyquery('div[data-markers-radio-name]')[0].attrib['data-markers-radio-name'] == 'f1$marker_id'

    formdef.fields[0].data_source = {'type': 'foobar'}
    formdef.store()
    resp = app.get('/test/')
    assert resp.pyquery('div[data-markers-radio-name]')[0].attrib['data-markers-url'] == '/api/geojson/foobar'
    assert resp.pyquery('div[data-markers-radio-name]')[0].attrib['data-markers-radio-name'] == 'f1$marker_id'
    app.get('/api/geojson/wrong-foobar', status=404)
    resp_geojson = app.get('/api/geojson/foobar')
    assert len(resp_geojson.json['features']) == 2
    assert http_requests.count() == 1
    assert http_requests.get_last('url') == 'http://remote.example.net/geojson'
    resp_geojson = app.get('/api/geojson/foobar')
    assert http_requests.count() == 1  # cache was used
    assert len(resp_geojson.json['features']) == 2

    with responses.RequestsMock() as rsps:
        cache.clear()
        rsps.get(
            'http://remote.example.net/geojson',
            json={
                'features': [
                    {'properties': {'id': 1, 'text': 'fo\'o'}},
                    {'properties': {'id': 2, 'text': 'b<a>r'}},
                ]
            },
        )
        resp_geojson = app.get('/api/geojson/foobar')
        assert resp_geojson.json['features'][0]['properties']['_text'] == 'fo\'o'
        assert resp_geojson.json['features'][1]['properties']['_text'] == 'b<a>r'

    # simulate qommon.map.js that will fill the hidden inputs
    resp.form['f1$marker_id'].value = '1'  # click on marker
    resp.form['f1$latlng'] = '1;2'  # set via js
    resp = resp.form.submit('submit')
    assert 'Check values then click submit.' in resp
    # selected option is displayed as readonly:
    assert resp.pyquery('input[type=text][value=foo][readonly]')

    # check going back keeps item selected
    resp = resp.form.submit('previous')
    assert resp.pyquery('.qommon-map').attr('data-markers-initial-id') == '1'
    resp.form['f1$marker_id'].value = '1'  # click on marker
    resp.form['f1$latlng'] = '1;2'  # set via js
    resp = resp.form.submit('submit')  # to validation page again

    resp = resp.form.submit('submit')
    resp = resp.follow()
    assert 'The form has been recorded' in resp
    assert '<p class="value">foo</p>' in resp
    assert formdef.data_class().count() == 1
    data_id = formdef.data_class().select()[0].id
    formdata = formdef.data_class().get(data_id)
    assert formdata.data['1_structured']['geometry']['coordinates'] == [1, 2]

    data_source.data_source = {
        'type': 'geojson',
        'value': 'http://remote.example.net/500',
    }
    data_source.store()
    resp_geojson = app.get('/api/geojson/foobar', status=400)
    assert resp_geojson.json == {
        'err': 1,
        'err_desc': 'Error retrieving data (Error loading JSON data source '
        '(error in HTTP request to http://remote.example.net/500 (status: 500))).',
        'err_class': 'Invalid request',
        'err_code': 'invalid-request',
    }

    formdef.fields = [
        fields.ItemField(id='1', label='map', display_mode='map', prefill={'type': 'string', 'value': '1;2'}),
    ]
    formdef.store()
    app.get('/test/', status=200)  # no error


def test_form_item_dynamic_map_data_source(pub, http_requests):
    NamedDataSource.wipe()
    data_source = NamedDataSource(name='foobar')
    data_source.data_source = {
        'type': 'geojson',
        'value': 'http://remote.example.net/geojson?{{ form_var_test }}',
    }
    data_source.id_property = 'id'
    data_source.label_template_property = '{{ text }}'
    data_source.cache_duration = '5'
    data_source.store()

    formdef = create_formdef()
    formdef.fields = [
        fields.PageField(id='0', label='1st page'),
        fields.StringField(id='1', label='string', varname='test'),
        fields.PageField(id='2', label='2nd page'),
        fields.ItemField(id='3', label='map', display_mode='map', data_source={'type': 'foobar'}),
    ]
    formdef.store()
    formdef.data_class().wipe()
    app = get_app(pub)
    resp = app.get('/test/')
    resp.form['f1'] = 'plop'
    resp = resp.form.submit('submit')  # -> 2nd page
    markers_url = resp.pyquery('div[data-markers-radio-name]')[0].attrib['data-markers-url']
    assert markers_url.startswith('/api/geojson/')
    resp_geojson = app.get(markers_url)
    assert len(resp_geojson.json['features']) == 2
    assert http_requests.count() == 1
    assert http_requests.get_last('url') == 'http://remote.example.net/geojson?plop'
    resp_geojson = app.get(markers_url)
    assert http_requests.count() == 1  # cache was used
    assert len(resp_geojson.json['features']) == 2


def test_form_item_map_data_source_initial_position(pub, http_requests):
    user = create_user(pub)
    role = pub.role_class(name='xxx')
    role.store()
    user.roles = [role.id]
    user.is_admin = True
    user.store()

    NamedDataSource.wipe()
    data_source = NamedDataSource(name='foobar')
    data_source.data_source = {
        'type': 'geojson',
        'value': 'http://remote.example.net/geojson',
    }
    data_source.id_property = 'id'
    data_source.label_template_property = '{{ text }}'
    data_source.cache_duration = '5'
    data_source.store()

    formdef = create_formdef()
    formdef.fields = [
        fields.ItemField(id='1', label='map', display_mode='map', initial_position='geoloc'),
    ]
    formdef.backoffice_submission_roles = [role.id]
    formdef.store()
    formdef.data_class().wipe()
    app = get_app(pub)
    resp = app.get('/test/')
    assert resp.pyquery('[data-init_with_geoloc="true"]')

    formdef.fields = [
        fields.ItemField(id='1', label='map', display_mode='map', initial_position='geoloc-front-only'),
    ]
    formdef.store()
    app = get_app(pub)
    resp = app.get('/test/')
    assert resp.pyquery('[data-init_with_geoloc="true"]')

    app = login(get_app(pub), username='foo', password='foo')
    resp = app.get('/backoffice/submission/test/')
    assert resp.pyquery('.MapMarkerSelectionWidget')
    assert not resp.pyquery('[data-init_with_geoloc="true"]')


def test_form_item_timetable_data_source(pub, http_requests):
    NamedDataSource.wipe()
    data_source = NamedDataSource(name='foobar')
    data_source.data_source = {
        'type': 'json',
        'value': 'http://remote.example.net/api/datetimes',
    }
    data_source.store()

    formdef = create_formdef()
    formdef.fields = [
        fields.ItemField(id='1', label='datetime', display_mode='timetable', data_source={'type': 'foobar'}),
    ]
    formdef.store()
    formdef.data_class().wipe()
    app = get_app(pub)

    with responses.RequestsMock() as rsps:
        data = {
            'data': [
                {'id': '1', 'datetime': '2021-01-12 10:00:00', 'text': 'event 1'},
                {'id': '2', 'datetime': '2021-01-13 10:20:00', 'text': 'event 2'},
                {'id': '3', 'datetime': '2021-01-14 10:40:00', 'text': 'event 3'},
            ]
        }
        rsps.get('http://remote.example.net/api/datetimes', json=data)

        resp = app.get('/test/')
        assert 'data-date="2021-01-12"' in resp and 'data-time="10:00"' in resp
        assert 'data-date="2021-01-13"' in resp and 'data-time="10:20"' in resp
        assert 'data-date="2021-01-14"' in resp and 'data-time="10:40"' in resp
        resp.form['f1'] = '2'  # would happen via javascript
        resp = resp.form.submit('submit')
        resp = resp.form.submit('submit')

        assert formdef.data_class().count() == 1
        data_id = formdef.data_class().select()[0].id
        formdata = formdef.data_class().get(data_id)
        assert formdata.data == {
            '1': '2',
            '1_display': 'event 2',
            '1_structured': {'id': '2', 'datetime': '2021-01-13 10:20:00', 'text': 'event 2'},
        }

        # check rendering of HTML hint
        formdef.fields[0].hint = '<p>hello <strong>world</strong></p>'
        formdef.store()
        resp = app.get('/test/')
        assert resp.pyquery('[data-field-id="1"] .hint strong').text() == 'world'


def test_form_item_timetable_data_source_with_date_alignment(pub, http_requests):
    NamedDataSource.wipe()
    data_source = NamedDataSource(name='foobar')
    data_source.data_source = {
        'type': 'json',
        'value': 'http://remote.example.net/api/datetimes',
    }
    data_source.store()

    formdef = create_formdef()
    formdef.fields = [
        fields.PageField(id='1', label='page1'),
        fields.DateField(id='2', label='date', varname='date'),
        fields.PageField(id='3', label='page2'),
        fields.ItemField(
            id='4',
            label='datetime',
            display_mode='timetable',
            data_source={'type': 'foobar'},
            initial_date_alignment='{{ form_var_date }}',
        ),
    ]
    formdef.store()
    formdef.data_class().wipe()
    app = get_app(pub)

    with responses.RequestsMock() as rsps:
        data = {
            'data': [
                {'id': '1', 'datetime': '2021-01-12 10:00:00', 'text': 'event 1'},
                {'id': '2', 'datetime': '2021-01-13 10:20:00', 'text': 'event 2'},
                {'id': '3', 'datetime': '2021-01-14 10:40:00', 'text': 'event 3'},
            ]
        }
        rsps.get('http://remote.example.net/api/datetimes', json=data)

        resp = app.get('/test/')
        resp.form['f2'] = '2021-01-14'
        resp = resp.form.submit('submit')  # -> 2nd page

        assert 'var ALIGN_DATE = "2021-01-14";' in resp
        resp.form['f4'] = '2'  # would happen via javascript
        resp = resp.form.submit('submit')
        resp = resp.form.submit('submit')
        assert formdef.data_class().count() == 1


def test_form_item_with_card_image_data_source(pub, http_requests):
    CardDef.wipe()
    FormDef.wipe()
    TransientData.wipe()

    carddef = CardDef()
    carddef.name = 'Images'
    carddef.fields = [
        fields.StringField(id='0', label='Label', varname='label'),
        fields.FileField(id='1', label='Image', varname='image'),
    ]
    carddef.digest_templates = {'default': '{{form_var_label}}'}
    carddef.store()
    carddef.data_class().wipe()

    with open(os.path.join(os.path.dirname(__file__), '..', 'image-with-gps-data.jpeg'), 'rb') as fd:
        image_content = fd.read()

    for i, value in enumerate(['Label 1', 'Label 2', 'Label 3']):
        carddata = carddef.data_class()()
        upload = PicklableUpload('test-%s.jpg' % i, content_type='image/jpeg')
        upload.receive([image_content])
        carddata.data = {'0': value, '1': upload}
        carddata.just_created()
        carddata.store()

    NamedDataSource.wipe()
    data_source = NamedDataSource(name='images')
    data_source.data_source = {'type': 'carddef:images'}
    data_source.store()

    data_source.id_property = 'id'
    data_source.label_template_property = '{{ label }}'
    data_source.cache_duration = '5'
    data_source.store()

    formdef = create_formdef()
    formdef.fields = [
        fields.ItemField(
            id='1', label='Choice', display_mode='images', data_source={'type': 'carddef:images'}
        ),
    ]
    formdef.store()
    formdef.data_class().wipe()
    app = get_app(pub)
    resp = app.get('/test/')

    assert len(resp.pyquery('div.RadiobuttonsWithImagesWidget')) == 1
    assert (
        '--image-desktop-width: 150px;--image-desktop-height: 150px'
        in resp.pyquery('div.RadiobuttonsWithImagesWidget').attr['style']
    )
    assert (
        '--image-mobile-width: 75px;--image-mobile-height: 75px;'
        in resp.pyquery('div.RadiobuttonsWithImagesWidget').attr['style']
    )
    assert len(resp.pyquery(':not(template) > label > img.item-with-image--picture')) == 3

    assert TransientData.count() == 3
    assert [x.text for x in resp.pyquery(':not(template) > label > .item-with-image--label')] == [
        'Label 1',
        'Label 2',
        'Label 3',
    ]
    assert [
        app.get(x.attrib['src']) for x in resp.pyquery(':not(template) > label > .item-with-image--picture')
    ]

    # check invalid token gives a 404
    app.get('/api/card-file-by-token/xxx', status=404)

    # check token cannot be misused
    token = TransientData.select()[0]
    app.get('/api/autocomplete/%s' % token.id, status=403)

    # new session, check a new token is generated
    token = TransientData.select()[0]
    resp = get_app(pub).get('/test/')
    assert '/api/card-file-by-token/%s' % token.id not in resp.text

    resp = app.get('/test/')
    resp.form['f1'] = str(carddata.id)
    resp = resp.form.submit('submit')  # -> validation
    assert len(resp.pyquery(':not(template) > label > img.item-with-image--picture')) == 3
    assert len(resp.pyquery('[checked]')) == 1
    assert len(resp.pyquery('[disabled]')) == 3
    assert len(resp.pyquery('[checked][disabled] + input[type=hidden]')) == 1
    assert len(resp.pyquery(':not(template) > label > input[name=f1]')) == 1
    assert resp.pyquery('[checked][disabled] + input[type=hidden]').attr.value == str(carddata.id)

    FormDef.wipe()

    formdef2 = create_formdef()
    formdef2.fields = [
        fields.ItemsField(
            id='1', label='Choices', display_mode='images', data_source={'type': 'carddef:images'}
        ),
    ]
    formdef2.store()
    formdef2.data_class().wipe()

    app = get_app(pub)
    resp = app.get('/test/')
    assert len(resp.pyquery('div.CheckboxesWithImagesWidget')) == 1
    assert len(resp.pyquery(':not(template) > label > img.item-with-image--picture')) == 3
    assert [x.text for x in resp.pyquery(':not(template) > label > .item-with-image--label')] == [
        'Label 1',
        'Label 2',
        'Label 3',
    ]
    assert [
        app.get(x.attrib['src']) for x in resp.pyquery(':not(template) > label > .item-with-image--picture')
    ]

    # check prefilling
    formdef2.fields[0].prefill = {'type': 'string', 'value': str(carddata.id)}
    formdef2.store()

    resp = app.get('/test/')
    assert resp.pyquery('[checked]').attr.name == 'f1$element3'
    resp = resp.form.submit('submit')  # -> validation
    # all inputs are disabled
    assert len(resp.pyquery(':not(template) > label > input[disabled]')) == 3

    resp = resp.form.submit('submit')  # -> submit
    assert formdef2.data_class().select()[0].data['1'] == [str(carddata.id)]
    formdef2.data_class().wipe()

    # check locked prefilling
    formdef2.fields[0].prefill['locked'] = True
    formdef2.store()

    resp = app.get('/test/')
    assert len(resp.pyquery('[checked]')) == 1
    assert resp.pyquery('[checked]').attr.disabled
    assert resp.pyquery('[checked] + input[type=hidden]').attr.name == 'f1$element3'
    resp = resp.form.submit('submit')  # -> validation
    resp = resp.form.submit('submit')  # -> submit
    assert formdef2.data_class().select()[0].data['1'] == [str(carddata.id)]

    # check it fallbacks to normal select if the source is not appropriate
    carddef.fields[1].varname = 'not-an-image'
    carddef.store()
    resp = app.get('/test/')
    assert len(resp.pyquery('div.CheckboxesWithImagesWidget')) == 0

    formdef2.fields[0].data_source = {'type': 'geojson', 'value': 'http://remote.example.net/geojson'}
    formdef2.store()
    resp = app.get('/test/')
    assert len(resp.pyquery('div.CheckboxesWithImagesWidget')) == 0


def test_form_item_publik_caller_url(pub, http_requests):
    NamedDataSource.wipe()
    data_source = NamedDataSource(name='foobar')
    data_source.data_source = {
        'type': 'json',
        'value': 'https://passerelle.invalid/json/',
    }
    data_source.store()

    formdef = create_formdef()
    formdef.fields = [
        fields.ItemField(
            id='4',
            label='item',
            data_source={'type': 'foobar'},
        ),
    ]
    formdef.store()
    formdef.data_class().wipe()
    app = get_app(pub)

    with responses.RequestsMock() as rsps:
        rsps.get(
            'https://passerelle.invalid/json/',
            json={'data': [{'id': '1', 'text': 'foo'}, {'id': '2', 'text': 'bar'}]},
        )

        app.get('/test/')
        # a form is available but no formdata yet so the formef admin page is sent
        assert rsps.calls[-1].request.headers['Publik-Caller-URL'] == formdef.get_admin_url()


@responses.activate
def test_item_field_debug_http_requests(http_requests, pub):
    user = create_user(pub)
    formdef = create_formdef()
    formdef.data_class().wipe()
    ds = {'type': 'json', 'value': 'http://remote.example.net/json'}
    formdef.fields = [fields.ItemField(id='0', label='string', data_source=ds)]
    formdef.store()

    responses.get(
        'http://remote.example.net/json',
        json={'data': [{'id': '1', 'text': 'hello'}, {'id': '2', 'text': 'world'}]},
    )
    resp = login(get_app(pub), username='foo', password='foo').get('/test/')
    assert not resp.pyquery('.debug-information')

    user.is_admin = True
    user.store()
    resp = login(get_app(pub), username='foo', password='foo').get('/test/')
    assert resp.pyquery('.debug-information')
    assert (
        resp.pyquery('.debug-information--http-requests li').text()
        == 'Field: string - GET http://remote.example.net/json'
    )


def test_item_fields_from_custom_view_on_card_internal_id(pub):
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

    ids = []
    for i in range(5):
        carddata = carddef1.data_class()()
        carddata.data = {'1': f'Foo {i}'}
        carddata.just_created()
        carddata.store()
        ids.append(str(carddata.id))

    custom_view = pub.custom_view_class()
    custom_view.title = 'view'
    custom_view.formdef = carddef1
    custom_view.columns = {'list': [{'id': 'id'}]}
    custom_view.filters = {
        'filter-internal-id': 'on',
        'filter-internal-id-value': '{{ form_var_blah }}',
        'filter-internal-id-operator': 'in',
    }
    custom_view.visibility = 'datasource'
    custom_view.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [
        fields.ComputedField(id='0', label='blah', varname='blah', value_template='|'.join(ids[:2])),
        fields.ItemField(
            id='1',
            label='Card',
            varname='card',
            data_source={'type': f'carddef:{carddef1.url_name}:{custom_view.slug}'},
        ),
    ]
    formdef.store()
    formdef.data_class().wipe()

    resp = get_app(pub).get('/test/', status=200)
    # check options
    assert resp.form['f1'].options == [('1', False, 'Foo 0'), ('2', False, 'Foo 1')]
