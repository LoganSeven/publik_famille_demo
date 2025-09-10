import itertools
import json

import pytest
import responses

from wcs import fields
from wcs.admin.settings import UserFieldsFormDef
from wcs.carddef import CardDef
from wcs.categories import Category
from wcs.data_sources import NamedDataSource
from wcs.formdef import FormDef

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


def test_form_items_submit(pub):
    formdef = create_formdef()
    formdef.fields = [
        fields.ItemsField(
            id='0',
            label='List of items',
            required='required',
            varname='foo',
            items=['Foo', 'Bar', 'Three', 'Four', 'Five', 'Six'],
        )
    ]
    formdef.store()
    formdef.data_class().wipe()

    page = get_app(pub).get('/test/')
    assert 'List of items' in page.text
    next_page = page.forms[0].submit('submit')  # but the field is required
    assert next_page.pyquery('#form_error_f0').text() == 'required field'
    next_page.forms[0]['f0$elementfoo'].checked = True
    next_page.forms[0]['f0$elementbar'].checked = True
    next_page = next_page.forms[0].submit('submit')
    assert 'Check values then click submit.' in next_page.text
    next_page = next_page.forms[0].submit('submit')
    assert next_page.status_int == 302
    next_page = next_page.follow()
    assert 'The form has been recorded' in next_page.text
    assert formdef.data_class().count() == 1
    data_id = formdef.data_class().select()[0].id
    data = formdef.data_class().get(data_id)
    assert data.data['0'] == ['Foo', 'Bar']
    assert data.data['0_display'] == 'Foo, Bar'

    formdef.fields[0].min_choices = 2
    formdef.fields[0].max_choices = 5
    formdef.store()

    page = get_app(pub).get('/test/')
    page.forms[0]['f0$elementfoo'].checked = True
    page = page.forms[0].submit('submit')
    assert page.pyquery('#form_error_f0').text() == 'You must select at least 2 answers.'
    page.forms[0]['f0$elementbar'].checked = True
    page.forms[0]['f0$elementthree'].checked = True
    page.forms[0]['f0$elementfour'].checked = True
    page.forms[0]['f0$elementfive'].checked = True
    page.forms[0]['f0$elementsix'].checked = True
    page = page.forms[0].submit('submit')
    assert page.pyquery('#form_error_f0').text() == 'You must select at most 5 answers.'
    page.forms[0]['f0$elementsix'].checked = False
    page = next_page.forms[0].submit('submit').follow()
    assert 'The form has been recorded' in page.text


def test_form_items_autocomplete(pub):
    formdef = create_formdef()
    formdef.fields = [
        fields.ItemsField(
            id='0',
            label='List of items',
            required='required',
            varname='foo',
            display_mode='autocomplete',
            items=['Foo', 'Bar', 'Three', 'Four', 'Five', 'Six'],
        )
    ]
    formdef.store()
    formdef.data_class().wipe()

    resp = get_app(pub).get('/test/')
    assert 'select2.min.js' in resp.text
    resp = resp.forms[0].submit('submit')  # but the field is required
    assert resp.pyquery('#form_error_f0').text() == 'required field'
    resp.forms[0]['f0[]'].select_multiple(['foo', 'bar'])
    resp = resp.forms[0].submit('submit')
    assert resp.pyquery('[name="f0[]"] option[selected]').text() == 'Foo Bar'
    assert resp.pyquery('#form_f0[readonly]').val() == 'Foo, Bar'
    assert 'Check values then click submit.' in resp.text
    resp = resp.forms[0].submit('submit').follow()
    assert 'The form has been recorded' in resp.text
    assert formdef.data_class().count() == 1
    data_id = formdef.data_class().select()[0].id
    data = formdef.data_class().get(data_id)
    assert data.data['0'] == ['Foo', 'Bar']
    assert data.data['0_display'] == 'Foo, Bar'

    # check empty value
    formdef.data_class().wipe()
    formdef.fields[0].required = False
    formdef.store()
    resp = get_app(pub).get('/test/')
    resp = resp.forms[0].submit('submit')
    resp = resp.forms[0].submit('submit').follow()
    formdata = formdef.data_class().select()[0]
    assert formdata.data['0'] is None
    assert formdata.data['0_display'] is None

    # check min/max choices
    formdef.data_class().wipe()

    formdef.fields[0].min_choices = 3
    formdef.fields[0].max_choices = 4
    formdef.store()

    resp = get_app(pub).get('/test/')
    resp.forms[0]['f0[]'].select_multiple(['foo', 'bar'])
    resp = resp.forms[0].submit('submit')
    assert resp.pyquery('#form_error_f0').text() == 'You must select at least 3 choices.'
    assert resp.forms[0]['f0[]'].value == ['foo', 'bar']
    resp.forms[0]['f0[]'].select_multiple(['foo', 'bar', 'three', 'four', 'five'])
    resp = resp.forms[0].submit('submit')
    assert resp.pyquery('#form_error_f0').text() == 'You must select at most 4 choices.'
    assert resp.forms[0]['f0[]'].value == ['foo', 'bar', 'three', 'four', 'five']
    resp.forms[0]['f0[]'].select_multiple(['foo', 'bar', 'three'])
    resp = resp.forms[0].submit('submit')  # -> validation
    resp = resp.forms[0].submit('submit').follow()

    data_id = formdef.data_class().select()[0].id
    data = formdef.data_class().get(data_id)
    assert data.data['0'] == ['Foo', 'Bar', 'Three']

    # check empty options do not crash the widget
    formdef.fields[0].items = []
    formdef.store()
    resp = get_app(pub).get('/test/')


def test_form_items_autocomplete_with_multiple_pages_no_confirmation(pub):
    formdef = create_formdef()
    formdef.confirmation = False
    formdef.fields = [
        fields.PageField(id='0', label='1st page'),
        fields.ItemsField(
            id='1',
            label='List of items',
            required='required',
            varname='foo',
            display_mode='autocomplete',
            items=['Foo', 'Bar', 'Three', 'Four', 'Five', 'Six'],
        ),
        fields.PageField(id='2', label='2nd page'),
    ]
    formdef.store()
    formdef.data_class().wipe()

    resp = get_app(pub).get('/test/')
    resp.forms[0]['f1[]'].select_multiple(['foo', 'bar'])
    resp = resp.forms[0].submit('submit')  # -> 2nd page
    resp = resp.forms[0].submit('submit')  # -> submit

    assert formdef.data_class().count() == 1
    data_id = formdef.data_class().select()[0].id
    data = formdef.data_class().get(data_id)
    assert data.data['1'] == ['Foo', 'Bar']
    assert data.data['1_display'] == 'Foo, Bar'


def test_form_items_data_source_field_submit(pub):
    def submit_items_data_source_field(ds):
        formdef = create_formdef()
        formdef.fields = [fields.ItemsField(id='0', label='string', data_source=ds)]
        formdef.store()
        resp = get_app(pub).get('/test/')
        formdef.data_class().wipe()
        resp.forms[0]['f0$element1'].checked = True
        resp.forms[0]['f0$element3'].checked = True
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
        'value': '[{"id": "1", "text": "un"}, {"id": "2", "text": "deux"}, {"id": "3", "text": "trois"}]',
    }
    assert submit_items_data_source_field(ds) == {
        '0': ['1', '3'],
        '0_display': 'un, trois',
        '0_structured': [{'id': '1', 'text': 'un'}, {'id': '3', 'text': 'trois'}],
    }

    ds['value'] = json.dumps(
        [{'id': '1', 'text': 'un'}, {'id': '2', 'text': 'deux'}, {'id': '3', 'text': 'trois'}]
    )
    assert submit_items_data_source_field(ds) == {
        '0': ['1', '3'],
        '0_display': 'un, trois',
        '0_structured': [{'id': '1', 'text': 'un'}, {'id': '3', 'text': 'trois'}],
    }

    ds['value'] = json.dumps(
        [
            {'id': '1', 'text': 'un', 'more': 'foo'},
            {'id': '2', 'text': 'deux', 'more': 'bar'},
            {'id': '3', 'text': 'trois', 'more': 'baz'},
        ]
    )
    assert submit_items_data_source_field(ds) == {
        '0': ['1', '3'],
        '0_display': 'un, trois',
        '0_structured': [
            {'id': '1', 'more': 'foo', 'text': 'un'},
            {'id': '3', 'more': 'baz', 'text': 'trois'},
        ],
    }


def test_form_items_datasource(pub):
    formdef = create_formdef()
    formdef.fields = [
        fields.ItemsField(
            id='1', label='items', varname='items', required='optional', data_source={'type': 'foobar'}
        )
    ]
    formdef.store()

    data_class = formdef.data_class()
    data_class.wipe()

    # add the named data source
    NamedDataSource.wipe()
    data_source = NamedDataSource(name='foobar')
    data_source.data_source = {'type': 'jsonvalue', 'value': '[]'}
    data_source.store()

    resp = get_app(pub).get('/test/')
    formdef.data_class().wipe()
    resp = resp.forms[0].submit('submit')  # should go straight to validation
    assert 'Check values then click submit.' in resp.text
    assert resp.forms[0]['previous']
    resp = resp.forms[0].submit('previous')

    # replace the named data source with one with items
    NamedDataSource.wipe()
    data_source = NamedDataSource(name='foobar')
    data_source.data_source = {
        'type': 'jsonvalue',
        'value': '[{"id": "un", "text": "un"}, {"id": "deux", "text": "deux"}]',
    }
    data_source.store()

    resp = get_app(pub).get('/test/')
    assert 'f1$elementun' in resp.form.fields
    assert 'f1$elementdeux' in resp.form.fields
    resp.form['f1$elementun'].checked = True
    resp.form['f1$elementdeux'].checked = True
    resp = resp.form.submit('submit')
    assert 'Check values then click submit.' in resp.text
    resp = resp.form.submit('submit')
    resp = resp.follow()

    assert data_class.select()[0].data == {
        '1': ['un', 'deux'],
        '1_display': 'un, deux',
        '1_structured': [{'id': 'un', 'text': 'un'}, {'id': 'deux', 'text': 'deux'}],
    }

    data_source.data_source = {
        'type': 'jsonvalue',
        'value': json.dumps([{'id': '1', 'text': 'un'}, {'id': '2', 'text': 'deux'}]),
    }
    data_source.store()

    data_class.wipe()
    resp = get_app(pub).get('/test/')
    assert 'f1$element1' in resp.form.fields
    assert 'f1$element2' in resp.form.fields
    resp.form['f1$element1'].checked = True
    resp.form['f1$element2'].checked = True
    resp = resp.form.submit('submit')
    assert 'Check values then click submit.' in resp.text
    resp = resp.form.submit('submit')
    resp = resp.follow()

    assert data_class.select()[0].data == {
        '1': ['1', '2'],
        '1_display': 'un, deux',
        '1_structured': [{'id': '1', 'text': 'un'}, {'id': '2', 'text': 'deux'}],
    }

    data_source.data_source = {
        'type': 'jsonvalue',
        'value': json.dumps(
            [{'id': '1', 'text': 'un', 'foo': 'bar1'}, {'id': '2', 'text': 'deux', 'foo': 'bar2'}]
        ),
    }
    data_source.store()

    data_class.wipe()
    resp = get_app(pub).get('/test/')
    assert 'f1$element1' in resp.form.fields
    assert 'f1$element2' in resp.form.fields
    resp.form['f1$element1'].checked = True
    resp.form['f1$element2'].checked = True
    resp = resp.form.submit('submit')
    assert 'Check values then click submit.' in resp.text
    resp = resp.form.submit('submit')
    resp = resp.follow()
    assert data_class.select()[0].data == {
        '1': ['1', '2'],
        '1_structured': [
            {'text': 'un', 'foo': 'bar1', 'id': '1'},
            {'text': 'deux', 'foo': 'bar2', 'id': '2'},
        ],
        '1_display': 'un, deux',
    }
    # check substitution variables
    substvars = data_class.select()[0].get_substitution_variables()
    assert substvars['form_var_items'] == 'un, deux'
    assert substvars['form_var_items_raw'] == ['1', '2']
    assert substvars['form_var_items_0_foo'] == 'bar1'
    assert substvars['form_var_items_1_foo'] == 'bar2'

    # check keys that would produce same flattened keys
    data_source.data_source = {
        'type': 'jsonvalue',
        'value': json.dumps(
            [
                {'id': '1', 'text': 'un', 'foo_bar': 'bar1', 'foo': {'bar': 'bar1'}},
                {'id': '2', 'text': 'deux', 'foo_bar': 'bar2', 'foo': {'bar': 'bar2'}},
            ]
        ),
    }
    data_source.store()

    data_class.wipe()
    resp = get_app(pub).get('/test/')
    assert 'f1$element1' in resp.form.fields
    assert 'f1$element2' in resp.form.fields
    resp.form['f1$element1'].checked = True
    resp.form['f1$element2'].checked = True
    resp = resp.form.submit('submit')
    assert 'Check values then click submit.' in resp.text
    resp = resp.form.submit('submit')
    resp = resp.follow()
    assert data_class.select()[0].data == {
        '1': ['1', '2'],
        '1_display': 'un, deux',
        '1_structured': [
            {'foo': {'bar': 'bar1'}, 'foo_bar': 'bar1', 'id': '1', 'text': 'un'},
            {'foo': {'bar': 'bar2'}, 'foo_bar': 'bar2', 'id': '2', 'text': 'deux'},
        ],
    }

    # check substitution variables
    substvars = data_class.select()[0].get_substitution_variables()
    assert substvars['form_var_items'] == 'un, deux'
    assert substvars['form_var_items_raw'] == ['1', '2']
    assert substvars['form_var_items_0_foo'] == {'bar': 'bar1'}
    assert substvars['form_var_items_1_foo'] == {'bar': 'bar2'}
    assert {x for x in substvars.get_flat_keys() if x.startswith('form_var_items')} == {
        'form_var_items',
        'form_var_items_0_foo_bar',
        'form_var_items_0_id',
        'form_var_items_0_text',
        'form_var_items_1_foo_bar',
        'form_var_items_1_id',
        'form_var_items_1_text',
        'form_var_items_raw',
        'form_var_items_structured',
    }


def test_form_autosave_with_items_field(pub):
    formdef = create_formdef()
    formdef.data_class().wipe()
    formdef.fields = [
        fields.PageField(id='0', label='1st page'),
        fields.StringField(id='1', label='string', varname='foo'),
        fields.PageField(id='2', label='2nd page'),
        fields.ItemsField(
            id='3',
            label='items',
            items=['pomme', 'poire', 'pêche', 'abricot'],
        ),
    ]
    formdef.enable_tracking_codes = True
    formdef.store()

    app = get_app(pub)
    resp = app.get('/test/')
    resp.form['f1'] = 'bar'

    assert app.post('/test/autosave', params=resp.form.submit_fields()).json == {'result': 'success'}
    assert formdef.data_class().count() == 1
    formdata = formdef.data_class().select()[0]
    assert formdata.status == 'draft'
    assert formdef.data_class().select()[0].data['1'] == 'bar'
    assert formdef.data_class().select()[0].data.get('3') is None

    resp = resp.forms[0].submit('submit')
    resp.form['f3$elementpoire'].checked = True
    resp.form['f3$elementabricot'].checked = True
    assert app.post('/test/autosave', params=resp.form.submit_fields()).json == {'result': 'success'}
    assert formdef.data_class().count() == 1
    assert formdef.data_class().select()[0].data['1'] == 'bar'
    assert formdef.data_class().select()[0].data['3'] == ['poire', 'abricot']


def test_items_field_from_cards_in_comment(pub):
    FormDef.wipe()
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

    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [
        fields.PageField(id='1', label='1st page'),
        fields.ItemsField(
            id='0', label='items', data_source=ds, display_disabled_items=True, varname='items'
        ),
        fields.PageField(id='2', label='2nd page'),
        fields.CommentField(
            id='3',
            label='<p>XX{{ form_var_items|getlist:"name"|join:", " }}XX</p>',
        ),
    ]
    formdef.store()

    app = get_app(pub)
    resp = app.get(formdef.get_url())
    resp.forms[0]['f0$element1'].checked = True
    resp.forms[0]['f0$element3'].checked = True
    resp = resp.form.submit('submit')  # -> second page
    assert '<p>XXbaz, fooXX</p>' in resp.text
    resp = resp.form.submit('submit')  # -> validation page
    resp = resp.form.submit('submit')  # -> submit
    formdata = formdef.data_class().select()[0]
    assert 'attr' in formdata.data['0_structured'][0]
    assert 'name' in formdata.data['0_structured'][0]


@pytest.mark.parametrize('filter_value', ['{{ "foo" }}', 'foo'])
def test_items_field_from_custom_view_on_cards(pub, filter_value):
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

    items = ['foo', 'bar', 'baz', 'buz']
    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'items'
    carddef.digest_templates = {'default': '{{form_var_attr}} - {{form_var_item}}'}
    carddef.workflow_roles = {'_editor': user.roles[0]}
    carddef.fields = [
        fields.ItemsField(id='0', label='item', varname='item', items=items),
        fields.StringField(id='1', label='string', varname='attr'),
    ]
    carddef.store()
    carddef.data_class().wipe()
    foo_bar_ids = set()
    for i, (v1, v2) in enumerate([(v1, v2) for (v1, v2) in itertools.product(items, items) if v1 != v2]):
        carddata = carddef.data_class()()
        carddata.data = {
            '0': [v1, v2],
            '0_display': '%s,%s' % (v1, v2),
            '1': 'attr%s' % i,
        }
        carddata.just_created()
        carddata.store()
        if 'foo' in {v1, v2}:
            foo_bar_ids.add(str(carddata.id))

    # create custom view
    app = login(get_app(pub), username='foo', password='foo')

    # we must force the ordering to have a determinist test
    resp = app.get('/backoffice/data/items/?order_by=id')
    assert resp.text.count('<tr') == 13  # thead + 12 items (max per page)
    resp.forms['listing-settings']['filter-0'].checked = True
    resp = resp.forms['listing-settings'].submit().follow()

    resp.forms['listing-settings']['filter-0-value'].force_value(filter_value)
    resp = resp.forms['listing-settings'].submit().follow()

    resp.forms['save-custom-view']['title'] = 'as data source'
    resp.forms['save-custom-view']['visibility'] = 'datasource'
    resp = resp.forms['save-custom-view'].submit()

    custom_view = pub.custom_view_class.select()[0]

    # use custom view as source
    ds = {'type': 'carddef:%s:%s' % (carddef.url_name, custom_view.slug)}
    formdef.fields = [fields.ItemField(id='0', label='string', data_source=ds, display_disabled_items=True)]
    formdef.store()

    resp = get_app(pub).get('/test/')
    assert len(resp.form['f0'].options) == 6  # 12 - ['baz,buz', 'buz,baz']
    assert {x[0] for x in resp.form['f0'].options} == foo_bar_ids
    resp = resp.form.submit('submit')  # -> validation page
    resp = resp.form.submit('submit')  # -> submit
    formdata = formdef.data_class().select()[0]
    assert formdata.data['0'] in foo_bar_ids
    assert formdata.data['0_structured']['text'] == 'attr0 - foo,bar'


def test_items_field_with_disabled_items(http_requests, pub):
    create_user(pub)
    formdef = create_formdef()
    formdef.data_class().wipe()
    ds = {'type': 'json', 'value': 'http://remote.example.net/json'}
    formdef.fields = [fields.ItemsField(id='0', label='string', data_source=ds, display_disabled_items=True)]
    formdef.store()

    with responses.RequestsMock() as rsps:
        rsps.get(
            'http://remote.example.net/json',
            json={'data': [{'id': '1', 'text': 'hello'}, {'id': '2', 'text': 'world'}]},
        )
        resp = get_app(pub).get('/test/')
        resp.form['f0$element1'].checked = True
        resp.form['f0$element2'].checked = True
        resp = resp.form.submit('submit')  # -> validation page
        resp = resp.form.submit('submit')  # -> submit
        assert formdef.data_class().select()[0].data['0'] == ['1', '2']
        assert formdef.data_class().select()[0].data['0_display'] == 'hello, world'

    formdef.data_class().wipe()

    with responses.RequestsMock() as rsps:
        rsps.get(
            'http://remote.example.net/json',
            json={'data': [{'id': '1', 'text': 'hello', 'disabled': True}, {'id': '2', 'text': 'world'}]},
        )
        resp = get_app(pub).get('/test/')
        assert 'disabled' in resp.form['f0$element1'].attrs
        resp.form['f0$element1'].checked = True
        resp.form['f0$element2'].checked = True
        resp = resp.form.submit('submit')  # -> validation page
        resp = resp.form.submit('submit')  # -> submit
        assert formdef.data_class().select()[0].data['0'] == ['2']
        assert formdef.data_class().select()[0].data['0_display'] == 'world'

    formdef.data_class().wipe()
    formdef.fields = [fields.ItemsField(id='0', label='string', data_source=ds, display_disabled_items=False)]
    formdef.store()

    with responses.RequestsMock() as rsps:
        rsps.get(
            'http://remote.example.net/json',
            json={'data': [{'id': '1', 'text': 'hello', 'disabled': True}, {'id': '2', 'text': 'world'}]},
        )
        resp = get_app(pub).get('/test/')
        assert 'f0$element1' not in resp.form.fields
        resp.form['f0$element2'].checked = True
        resp = resp.form.submit('submit')  # -> validation page
        resp = resp.form.submit('submit')  # -> submit
        assert formdef.data_class().select()[0].data['0'] == ['2']
        assert formdef.data_class().select()[0].data['0_display'] == 'world'
