import json
import os
import time
import urllib

import pytest
import responses
from webtest import Upload

from wcs import fields
from wcs.admin.settings import UserFieldsFormDef
from wcs.blocks import BlockDef
from wcs.carddef import CardDef
from wcs.categories import Category
from wcs.data_sources import NamedDataSource
from wcs.formdef import FormDef
from wcs.logged_errors import LoggedError
from wcs.wf.create_formdata import Mapping
from wcs.wf.form import WorkflowFormFieldsFormDef
from wcs.workflows import Workflow

from ..utilities import clean_temporary_pub, create_temporary_pub, get_app, login
from .test_all import assert_current_page, create_formdef, create_user, get_displayed_tracking_code


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


def test_form_phone_prefill(pub, nocache):
    create_user(pub)
    formdef = create_formdef()
    formdef.data_class().wipe()
    formdef.fields = [fields.StringField(id='0', label='string', prefill={'type': 'user', 'value': 'phone'})]
    formdef.store()

    resp = login(get_app(pub), username='foo', password='foo').get('/test/')
    assert resp.pyquery('input#form_f0').val() == '01 23 45 67 89'
    resp.forms[0]['f0'] = '+33987654321'
    resp = resp.forms[0].submit('submit')
    assert resp.pyquery('button.form-submit').val() == 'Submit'


def test_form_phone_prefill_phone_fr_validation(pub, nocache):
    create_user(pub)
    pub.load_site_options()
    if not pub.site_options.has_section('options'):
        pub.site_options.add_section('options')
    pub.site_options.set('options', 'local-region-code', 'FR')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)
    formdef = create_formdef()
    formdef.data_class().wipe()
    field = fields.StringField(id='0', label='string', prefill={'type': 'user', 'value': 'phone'})
    field.validation = {'type': 'phone-fr'}
    formdef.fields = [field]
    formdef.store()

    resp = login(get_app(pub), username='foo', password='foo').get('/test/')
    assert resp.pyquery('input#form_f0').val() == '01 23 45 67 89'
    resp.forms[0]['f0'] = '0987654321'
    resp = resp.forms[0].submit('submit')
    assert resp.pyquery('button.form-submit').val() == 'Submit'


def test_form_phone_prefill_phone_validation(pub, nocache):
    user = create_user(pub)
    pub.load_site_options()
    if not pub.site_options.has_section('options'):
        pub.site_options.add_section('options')
    pub.site_options.set('options', 'local-region-code', 'BE')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)
    formdef = create_formdef()
    formdef.data_class().wipe()
    field = fields.StringField(id='0', label='string', prefill={'type': 'user', 'value': 'phone'})
    field.validation = {'type': 'phone'}
    formdef.fields = [field]
    formdef.store()

    resp = login(get_app(pub), username='foo', password='foo').get('/test/')
    assert resp.pyquery('input#form_f0').val() == '01 23 45 67 89'
    resp.forms[0]['f0'] = '0987654321'
    resp = resp.forms[0].submit('submit')
    assert resp.pyquery('button.form-submit').val() == 'Submit'

    user.form_data['_phone'] = '+3281000000'
    user.store()

    resp = login(get_app(pub), username='foo', password='foo').get('/test/')
    assert resp.pyquery('input#form_f0').val() == '081 00 00 00'
    resp.forms[0]['f0'] = '0987654321'
    resp = resp.forms[0].submit('submit')
    assert resp.pyquery('button.form-submit').val() == 'Submit'

    user.form_data['_phone'] = '+99981000000'
    user.store()

    resp = login(get_app(pub), username='foo', password='foo').get('/test/')
    assert resp.pyquery('input#form_f0').val() == '+99981000000'
    resp.forms[0]['f0'] = '0987654321'
    resp = resp.forms[0].submit('submit')
    assert resp.pyquery('button.form-submit').val() == 'Submit'


def test_form_tracking_code_prefill(pub, nocache):
    create_user(pub)
    formdef = create_formdef()
    formdef.data_class().wipe()
    formdef.fields = [fields.StringField(id='0', label='string', prefill={'type': 'user', 'value': 'email'})]
    formdef.enable_tracking_codes = True
    formdef.store()

    # first time
    resp = login(get_app(pub), username='foo', password='foo').get('/test/')
    assert '<h3>Tracking code</h3>' in resp.text
    assert 'You already started to fill this form.' not in resp.text
    resp.forms[0]['f0'] = 'foobar'
    resp = resp.forms[0].submit('submit')

    # second time, invitation to load an existing draft
    resp = login(get_app(pub), username='foo', password='foo').get('/test/')
    assert '<h3>Tracking code</h3>' in resp.text
    assert 'You already started to fill this form.' in resp.text


@pytest.mark.parametrize('field_type', ['string', 'item'])
@pytest.mark.parametrize('logged_in', ['anonymous', 'logged-in'])
def test_form_draft_from_prefill(pub, field_type, logged_in):
    create_user(pub)

    formdef = create_formdef()
    if field_type == 'string':
        formdef.fields = [fields.StringField(id='0', label='string')]
    else:
        formdef.fields = [fields.ItemField(id='0', label='item', items=['foo', 'bar', 'hello'])]
    formdef.enable_tracking_codes = True
    formdef.store()
    formdef.data_class().wipe()

    app = get_app(pub)
    if logged_in == 'logged-in':
        login(app, username='foo', password='foo')

    # no draft
    app.get('/test/')
    assert formdef.data_class().count() == 0
    formdef.data_class().wipe()

    # make sure no draft is created on prefilled fields
    formdef.fields[0].prefill = {'type': 'string', 'value': '{{request.GET.test|default:""}}'}
    formdef.store()
    app.get('/test/?test=hello')
    assert formdef.data_class().count() == 0

    # check there's no leftover draft after submission
    for with_tracking_code in (False, True):
        formdef.enable_tracking_codes = with_tracking_code
        formdef.store()
        formdef.data_class().wipe()
        resp = app.get('/test/?test=hello')
        resp = resp.form.submit('submit')  # -> validation
        resp = resp.form.submit('submit')  # -> submit
        assert formdef.data_class().count() == 1


def test_form_page_string_prefill(pub):
    create_user(pub)
    formdef = create_formdef()
    formdef.data_class().wipe()
    formdef.fields = [
        fields.StringField(id='0', label='string', prefill={'type': 'string', 'value': 'HELLO WORLD'})
    ]
    formdef.store()

    resp = get_app(pub).get('/test/')
    assert resp.forms[0]['f0'].value == 'HELLO WORLD'
    assert 'widget-prefilled' in resp.text


def test_form_page_profile_prefill(pub):
    create_user(pub)
    formdef = create_formdef()
    formdef.data_class().wipe()
    formdef.fields = [fields.StringField(id='0', label='string', prefill={'type': 'user', 'value': 'email'})]
    formdef.store()

    resp = get_app(pub).get('/test/')
    assert resp.forms[0]['f0'].value == ''

    resp = login(get_app(pub), username='foo', password='foo').get('/test/')
    assert resp.forms[0]['f0'].value == 'foo@localhost'


@pytest.mark.parametrize('prefill_type', ['not-locked', 'locked'])
def test_form_page_profile_first_name_prefill(pub, prefill_type):
    user = create_user(pub)

    user_formdef = UserFieldsFormDef(pub)
    user_formdef.fields = [
        fields.StringField(id='_first_name', label='first name', extra_css_class='autocomplete-given-name'),
        fields.StringField(id='_city', label='city', extra_css_class='autocomplete-address-level2'),
        fields.StringField(id='_plop', label='plop', extra_css_class='xxx'),
    ]
    user_formdef.store()
    user.form_data = {'_first_name': 'plop', '_city': 'mytown'}
    user.set_attributes_from_formdata(user.form_data)
    user.store()

    formdef = create_formdef()
    formdef.data_class().wipe()
    formdef.fields = [
        fields.StringField(
            id='0',
            label='string',
            prefill={'type': 'user', 'value': '_first_name', 'locked': bool(prefill_type == 'locked')},
        ),
        fields.StringField(
            id='1',
            label='string',
            prefill={'type': 'user', 'value': '_city', 'locked': bool(prefill_type == 'locked')},
        ),
        fields.StringField(
            id='2',
            label='string',
            prefill={'type': 'user', 'value': '_plop', 'locked': bool(prefill_type == 'locked')},
        ),
    ]
    formdef.store()

    resp = get_app(pub).get('/test/')
    assert resp.forms[0]['f0'].value == ''
    assert resp.forms[0]['f0'].attrs['autocomplete'] == 'given-name'  # html5
    assert not resp.forms[0]['f0'].attrs.get('readonly')
    assert resp.forms[0]['f1'].value == ''
    assert resp.forms[0]['f1'].attrs['autocomplete'] == 'address-level2'  # html5
    assert not resp.forms[0]['f1'].attrs.get('readonly')
    assert resp.forms[0]['f2'].value == ''
    assert not resp.forms[0]['f2'].attrs.get('readonly')

    resp = login(get_app(pub), username='foo', password='foo').get('/test/')
    assert resp.forms[0]['f0'].value == 'plop'
    assert bool(resp.forms[0]['f0'].attrs.get('readonly')) is bool(prefill_type == 'locked')
    assert resp.forms[0]['f1'].value == 'mytown'
    assert bool(resp.forms[0]['f1'].attrs.get('readonly')) is bool(prefill_type == 'locked')
    assert resp.forms[0]['f2'].value == ''
    assert bool(resp.forms[0]['f2'].attrs.get('readonly')) is bool(prefill_type == 'locked')


def test_form_page_template_prefill(pub):
    create_user(pub)
    formdef = create_formdef()
    formdef.data_class().wipe()
    formdef.fields = [
        fields.StringField(
            id='0', label='string', prefill={'type': 'string', 'value': '{{session_user_display_name}}'}
        )
    ]
    formdef.store()

    app = login(get_app(pub), username='foo', password='foo')
    resp = app.get('/test/')
    assert resp.form['f0'].value == 'User Name'
    assert 'widget-prefilled' in resp.text

    # erroneous prefill
    formdef.fields = [
        fields.StringField(
            id='0',
            label='string',
            prefill={'type': 'string', 'value': '{{session_user_display_name|unknown}}'},
        )
    ]
    formdef.store()

    resp = app.get('/test/')
    assert resp.form['f0'].value == ''
    # still marked with a css class, in case of live changes.
    assert 'widget-prefilled' in resp.text


def test_form_page_session_var_prefill(pub):
    create_user(pub)
    formdef = create_formdef()
    formdef.data_class().wipe()
    formdef.fields = [
        fields.StringField(id='0', label='string', prefill={'type': 'string', 'value': '{{session_var_foo}}'})
    ]
    formdef.store()

    # check it's empty if it doesn't exist
    resp = get_app(pub).get('/test/')
    assert resp.forms[0]['f0'].value == ''

    # check it's not set if it's not whitelisted
    resp = get_app(pub).get('/?session_var_foo=hello')
    assert urllib.parse.urlparse(resp.location).path == '/'
    resp = resp.follow()
    resp = resp.click('test')
    assert resp.forms[0]['f0'].value == ''

    # check it works
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        fd.write(
            '''[options]
query_string_allowed_vars = foo,bar
'''
        )

    resp = get_app(pub).get('/?session_var_foo=hello')
    assert urllib.parse.urlparse(resp.location).path == '/'
    resp = resp.follow()
    resp = resp.click('test')
    assert resp.forms[0]['f0'].value == 'hello'

    # check it survives a login
    resp = get_app(pub).get('/?session_var_foo=hello2')
    assert urllib.parse.urlparse(resp.location).path == '/'
    resp = resp.follow()
    resp = resp.click('Login')
    resp = resp.follow()
    resp.forms[0]['username'] = 'foo'
    resp.forms[0]['password'] = 'foo'
    resp = resp.forms[0].submit()
    resp = resp.follow()
    resp = resp.click('test')
    assert resp.forms[0]['f0'].value == 'hello2'

    # check repeated options are ignored
    resp = get_app(pub).get('/?session_var_foo=hello&session_var_foo=hello2')
    assert urllib.parse.urlparse(resp.location).path == '/'
    resp = resp.follow()
    resp = resp.click('test')
    assert resp.forms[0]['f0'].value == ''

    # check extra query string parameters are not lost
    resp = get_app(pub).get('/?session_var_foo=hello&foo=bar')
    assert urllib.parse.urlparse(resp.location).path == '/'
    assert urllib.parse.urlparse(resp.location).query == 'foo=bar'

    os.unlink(os.path.join(pub.app_dir, 'site-options.cfg'))


def test_form_page_template_list_prefill(pub):
    formdef = create_formdef()
    formdef.data_class().wipe()
    formdef.fields = [
        fields.ItemField(
            id='1',
            label='item',
            varname='item',
            required='required',
            items=['Foo', 'Bar'],
            prefill={'type': 'string', 'value': 'Foo'},
        )
    ]
    formdef.store()

    resp = get_app(pub).get('/test/')
    assert resp.form['f1'].value == 'Foo'

    formdef.fields[0].prefill['value'] = 'Bar'
    formdef.store()
    resp = get_app(pub).get('/test/')
    assert resp.form['f1'].value == 'Bar'

    formdef.fields[0].prefill['value'] = 'Baz'
    formdef.store()
    resp = get_app(pub).get('/test/')
    assert 'invalid value selected' in resp.text

    formdef.fields[0].prefill['value'] = '{{plop|default:""}}'
    formdef.store()
    resp = get_app(pub).get('/test/')
    assert 'invalid value selected' not in resp.text


def test_form_page_template_list_prefill_by_text(pub):
    NamedDataSource.wipe()
    data_source = NamedDataSource(name='foobar')
    data_source.data_source = {
        'type': 'jsonvalue',
        'value': '[{"id": 1, "text": "foo"}, {"id": 2, "text": "bar"}]',
    }
    data_source.store()

    formdef = create_formdef()
    formdef.data_class().wipe()
    formdef.fields = [
        fields.ItemField(
            id='1',
            label='item',
            varname='item',
            required='required',
            data_source={'type': data_source.slug},
            prefill={'type': 'string', 'value': 'bar'},
        )
    ]
    formdef.store()

    resp = get_app(pub).get('/test/')
    assert resp.form['f1'].value == '2'
    assert 'invalid value selected' not in resp.text

    # check with card data source
    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'Test'
    carddef.fields = [
        fields.StringField(id='0', label='blah', varname='blah'),
    ]
    carddef.digest_templates = {'default': '{{ form_var_blah }}'}
    carddef.store()
    carddef.data_class().wipe()

    carddata1 = carddef.data_class()()
    carddata1.data = {'0': 'foo'}
    carddata1.just_created()
    carddata1.store()

    carddata2 = carddef.data_class()()
    carddata2.data = {'0': 'bar'}
    carddata2.just_created()
    carddata2.store()

    formdef.data_source = {'type': 'carddef:test'}
    formdef.store()

    resp = get_app(pub).get('/test/')
    assert resp.form['f1'].value == str(carddata2.id)
    assert 'invalid value selected' not in resp.text

    formdef.fields[0].prefill = {'type': 'string', 'value': '{{ %s }}' % carddata2.id}
    formdef.enable_tracking_codes = True
    formdef.store()
    app = get_app(pub)
    resp = app.get('/test/')
    assert resp.form['f1'].value == str(carddata2.id)
    assert 'invalid value selected' not in resp.text
    assert app.post('/test/autosave', params=resp.form.submit_fields()).json == {'result': 'success'}


@pytest.mark.parametrize('id_type', [int, str])
def test_form_page_template_list_prefill_by_number(pub, id_type):
    NamedDataSource.wipe()
    data_source = NamedDataSource(name='foobar')
    data_source.data_source = {
        'type': 'jsonvalue',
        'value': json.dumps([{'id': id_type(1), 'text': 'foo'}, {'id': id_type(2), 'text': 'bar'}]),
    }
    data_source.store()

    formdef = create_formdef()
    formdef.data_class().wipe()
    formdef.enable_tracking_codes = True
    formdef.fields = [
        fields.PageField(id='1', label='1st page'),
        fields.ItemField(
            id='2',
            label='item',
            varname='item',
            required='required',
            data_source={'type': data_source.slug},
            prefill={'type': 'string', 'value': '{{ 2 }}'},
        ),
        fields.PageField(id='3', label='2nd page'),
        fields.ItemField(
            id='4',
            label='item',
            varname='item',
            required='required',
            data_source={'type': data_source.slug},
            prefill={'type': 'string', 'value': '{{ 3 }}'},  # invalid value
        ),
    ]
    formdef.store()

    app = get_app(pub)
    resp = app.get('/test/')
    assert resp.form['f2'].value == '2'
    assert 'invalid value selected' not in resp.text
    assert app.post('/test/autosave', params=resp.form.submit_fields()).json == {'result': 'success'}
    resp = resp.form.submit('submit')
    assert 'invalid value selected' in resp.text


def test_form_page_query_string_list_prefill(pub):
    create_user(pub)
    formdef = create_formdef()
    formdef.data_class().wipe()
    formdef.fields = [
        fields.ItemField(
            id='1',
            label='item',
            varname='item',
            required='optional',
            data_source={'type': 'foobar'},
            prefill={'type': 'string', 'value': '{{request.GET.preselect}}'},
        )
    ]
    formdef.store()

    NamedDataSource.wipe()
    data_source = NamedDataSource(name='foobar')
    data_source.data_source = {
        'type': 'jsonvalue',
        'value': json.dumps(
            [
                {'id': '1', 'text': 'un'},
                {'id': '2', 'text': 'deux'},
                {'id': '3', 'text': 'trois'},
                {'id': '4', 'text': 'quatre'},
            ]
        ),
    }
    data_source.store()

    resp = get_app(pub).get('/test/')
    assert resp.form['f1'].value == '1'

    resp = get_app(pub).get('/test/?preselect=2')
    assert resp.form['f1'].value == '2'
    resp = resp.form.submit('submit')
    resp = resp.form.submit('submit').follow()
    assert 'deux' in resp.text


def test_form_page_profile_prefill_list(pub):
    create_user(pub)
    formdef = create_formdef()
    formdef.data_class().wipe()
    formdef.fields = [
        fields.ItemField(
            id='0',
            label='item',
            items=['', 'bar@localhost', 'foo@localhost'],
            required='optional',
            prefill={'type': 'user', 'value': 'email'},
        )
    ]
    formdef.store()

    resp = get_app(pub).get('/test/')
    assert resp.forms[0]['f0'].value == ''

    resp = login(get_app(pub), username='foo', password='foo').get('/test/')
    assert resp.forms[0]['f0'].value == 'foo@localhost'

    # invalid value
    formdef.fields = [
        fields.ItemField(
            id='0',
            label='item',
            items=['', 'bar@localhost'],
            required='optional',
            prefill={'type': 'user', 'value': 'email'},
        )
    ]
    formdef.store()
    resp = login(get_app(pub), username='foo', password='foo').get('/test/')
    assert 'invalid value selected' in resp.text
    assert resp.forms[0]['f0'].value == ''


def test_form_page_item_with_variable_data_source_prefill(pub):
    create_user(pub)
    formdef = create_formdef()
    formdef.data_class().wipe()
    formdef.fields = [
        fields.StringField(
            id='1', label='string', varname='string', prefill={'type': 'string', 'value': 'foobar'}
        ),
        fields.ItemField(
            id='2',
            label='item',
            varname='item',
            required='optional',
            data_source={'type': 'foobar'},
            prefill={'type': 'string', 'value': '4'},
        ),
    ]
    formdef.store()

    NamedDataSource.wipe()
    data_source = NamedDataSource(name='foobar')
    data_source.data_source = {
        'type': 'json',
        'value': 'http://example.net/{{form_var_string}}',
    }
    data_source.store()

    with responses.RequestsMock() as rsps:
        rsps.get('http://example.net/None', json={'data': [{'id': '1', 'text': 'hello'}]})
        rsps.get(
            'http://example.net/foobar',
            json={'data': [{'id': '1', 'text': 'hello'}, {'id': '4', 'text': 'world'}]},
        )
        resp = get_app(pub).get('/test/')
        assert len(rsps.calls) == 2
        assert rsps.calls[0].request.url == 'http://example.net/None'
        assert rsps.calls[1].request.url == 'http://example.net/foobar'
        assert [x.attrib['value'] for x in resp.pyquery('#form_f2 option')] == ['1', '4']
        assert resp.form['f2'].value == '4'
        assert not resp.pyquery('#form_error_f2').text()


def test_form_page_item_with_card_with_custom_id_prefill(pub):
    create_user(pub)
    CardDef.wipe()
    FormDef.wipe()

    carddef = CardDef()
    carddef.name = 'Test'
    carddef.fields = [
        fields.StringField(id='0', label='blah', varname='blah'),
    ]
    carddef.digest_templates = {'default': '{{ form_var_blah|upper }}'}
    carddef.id_template = '{{ form_var_blah }}'
    carddef.store()
    carddef.data_class().wipe()

    carddata1 = carddef.data_class()()
    carddata1.data = {'0': 'bar'}
    carddata1.just_created()
    carddata1.store()

    carddata2 = carddef.data_class()()
    carddata2.data = {'0': 'foo'}
    carddata2.just_created()
    carddata2.store()

    formdef = create_formdef()
    formdef.data_class().wipe()
    formdef.fields = [
        fields.ItemField(
            id='2',
            label='item',
            varname='item',
            required='optional',
            data_source={'type': 'carddef:test'},
            prefill={'type': 'string', 'value': 'foo'},
        ),
    ]
    formdef.store()

    resp = get_app(pub).get('/test/')
    assert [x.attrib['value'] for x in resp.pyquery('#form_f2 option')] == ['bar', 'foo']
    assert resp.form['f2'].value == 'foo'
    assert not resp.pyquery('#form_error_f2').text()


def test_form_page_block_with_item_with_card_with_custom_id_prefill(pub):
    create_user(pub)
    CardDef.wipe()
    FormDef.wipe()
    LoggedError.wipe()

    carddef = CardDef()
    carddef.name = 'Test'
    carddef.fields = [
        fields.StringField(id='0', label='blah', varname='blah'),
    ]
    carddef.digest_templates = {'default': 'card {{ form_var_blah }}'}
    carddef.id_template = '{{ form_var_blah }}'
    carddef.store()
    carddef.data_class().wipe()

    carddata1 = carddef.data_class()()
    carddata1.data = {'0': 'bar'}
    carddata1.just_created()
    carddata1.store()

    carddata2 = carddef.data_class()()
    carddata2.data = {'0': 'foo'}
    carddata2.just_created()
    carddata2.store()

    BlockDef.wipe()
    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.ItemField(
            id='123',
            label='item',
            varname='item',
            required='optional',
            data_source={'type': 'carddef:test'},
        ),
    ]
    block.store()

    formdef = create_formdef()
    formdef.data_class().wipe()
    formdef.fields = [
        fields.BlockField(
            id='2',
            label='test',
            block_slug='foobar',
            varname='foobar',
            prefill={'type': 'string', 'value': '{% block_value item="foo" %}'},
        ),
    ]
    formdef.store()

    resp = get_app(pub).get('/test/')
    assert [x.attrib['value'] for x in resp.pyquery('#form_f2__element0__f123 option')] == ['bar', 'foo']
    assert resp.form['f2$element0$f123'].value == 'foo'
    assert not resp.pyquery('.widget-with-error')
    resp = resp.form.submit('submit')  # -> validation
    resp = resp.form.submit('submit').follow()  # -> submit
    assert LoggedError.count() == 0


def test_form_page_block_and_no_confirmation_page(pub):
    create_user(pub)
    CardDef.wipe()
    FormDef.wipe()
    LoggedError.wipe()

    BlockDef.wipe()
    block = BlockDef()
    block.name = 'foobar'
    block.fields = [fields.StringField(id='123', label='foo', varname='foo', required='optional')]
    block.store()

    formdef = create_formdef()
    formdef.data_class().wipe()
    formdef.fields = [
        fields.BlockField(
            id='1',
            label='test',
            block_slug='foobar',
            varname='foobar',
            prefill={'type': 'string', 'value': '{% block_value foo="foo" %}'},
        ),
    ]
    formdef.confirmation = False
    formdef.store()

    resp = get_app(pub).get('/test/')
    assert resp.form['f1$element0$f123'].value == 'foo'
    assert not resp.pyquery('.widget-with-error')
    resp = resp.form.submit('submit').follow()  # -> submit
    assert LoggedError.count() == 0


def test_form_page_item_with_computed_field_variable_data_source_prefill(pub):
    create_user(pub)
    formdef = create_formdef()
    formdef.data_class().wipe()
    formdef.fields = [
        fields.ComputedField(
            id='1',
            label='string',
            varname='string',
            value_template='foobar',
        ),
        fields.ItemField(
            id='2',
            label='item',
            varname='item',
            required='optional',
            data_source={'type': 'foobar'},
            prefill={'type': 'string', 'value': '4'},
        ),
    ]
    formdef.store()

    NamedDataSource.wipe()
    data_source = NamedDataSource(name='foobar')
    data_source.data_source = {
        'type': 'json',
        'value': 'http://example.net/{{form_var_string}}',
    }
    data_source.store()

    with responses.RequestsMock() as rsps:
        rsps.get(
            'http://example.net/foobar',
            json={'data': [{'id': '1', 'text': 'hello'}, {'id': '4', 'text': 'world'}]},
        )
        resp = get_app(pub).get('/test/')
        assert [x.attrib['value'] for x in resp.pyquery('#form_f2 option')] == ['1', '4']
        assert resp.form['f2'].value == '4'
        assert not resp.pyquery('#form_error_f2').text()


def test_form_page_checkbox_prefill(pub):
    create_user(pub)
    formdef = create_formdef()
    formdef.data_class().wipe()
    formdef.fields = [
        fields.BoolField(id='0', label='check', prefill={'type': 'string', 'value': '{{ True }}'})
    ]
    formdef.store()

    resp = get_app(pub).get('/test/')
    assert resp.forms[0]['f0'].checked
    resp = resp.forms[0].submit('submit')  # -> validation
    assert resp.forms[0]['f0'].value == 'True'
    assert resp.forms[0]['f0disabled'].checked
    assert resp.forms[0]['f0disabled'].attrs['disabled']

    resp = get_app(pub).get('/test/')
    resp.forms[0]['f0'].checked = False
    resp = resp.forms[0].submit('submit')  # -> validation
    assert resp.forms[0]['f0'].value == 'False'
    assert not resp.forms[0]['f0disabled'].checked
    assert resp.forms[0]['f0disabled'].attrs['disabled']

    # check with locked value
    formdef.fields[0].prefill['locked'] = True
    formdef.store()

    resp = get_app(pub).get('/test/')
    assert resp.forms[0]['f0'].attrs['onclick']
    assert resp.forms[0]['f0'].checked
    resp.forms[0]['f0'].checked = False  # alter value while it's not allowed
    resp = resp.forms[0].submit('submit')  # -> validation
    assert resp.forms[0]['f0'].value == 'True'
    assert resp.forms[0]['f0disabled'].checked
    assert resp.forms[0]['f0disabled'].attrs['disabled']


def test_form_page_date_prefill(pub):
    create_user(pub)
    formdef = create_formdef()
    formdef.data_class().wipe()
    # check simple date and with a template with extraneous space
    for value in ('2023-07-07', '{{ "  2023-07-07" }}'):
        formdef.fields = [fields.DateField(id='0', label='date', prefill={'type': 'string', 'value': value})]
        formdef.store()

        resp = get_app(pub).get('/test/')
        assert resp.forms[0]['f0'].value == '2023-07-07'
        resp = resp.forms[0].submit('submit')  # -> validation
        assert resp.forms[0]['f0'].value == '2023-07-07'


def test_form_page_date_prefill_invalid_value(pub):
    create_user(pub)
    formdef = create_formdef()
    formdef.data_class().wipe()
    # check simple date and with a template with extraneous space
    formdef.fields = [
        fields.PageField(id='0', label='page1'),
        fields.DateField(id='1', label='date'),
        fields.PageField(id='2', label='page2'),
        fields.DateField(id='3', label='date', prefill={'type': 'string', 'value': 'None'}),
    ]
    formdef.store()

    resp = get_app(pub).get('/test/')
    resp.forms[0]['f1'].value = '2024-05-27'
    resp = resp.forms[0].submit('submit')
    resp = resp.forms[0].submit('previous')
    resp = resp.forms[0].submit('submit')


@pytest.mark.parametrize('prefill_value', ['foo,baz', 'foo|baz'])
def test_form_page_template_prefill_items_field_checkboxes(pub, prefill_value):
    # prefill value should be given as foo|baz but foo,baz has been used for a while
    # and must be kept working (even if it worked by chance).
    BlockDef.wipe()
    create_user(pub)

    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.StringField(id='123', required='required', label='Test', varname='test'),
    ]
    block.store()

    formdef = create_formdef()
    formdef.data_class().wipe()
    formdef.fields = [
        fields.ItemsField(
            id='0',
            label='items',
            items=['foo', 'bar', 'baz'],
            prefill={'type': 'string', 'value': prefill_value},
        ),
        fields.FileField(id='1', label='file', varname='file'),
        fields.BlockField(id='2', label='test', block_slug='foobar', varname='foobar'),
    ]
    formdef.store()

    resp = get_app(pub).get('/test/')
    assert resp.form['f0$elementfoo'].checked
    assert not resp.form['f0$elementbar'].checked
    assert resp.form['f0$elementbaz'].checked
    # this selection will be reused in the complex data test
    resp.form['f0$elementbar'].checked = True
    resp.form['f0$elementbaz'].checked = False
    assert 'widget-prefilled' in resp.text
    resp.form['f1$file'] = Upload('test.txt', b'foobar', 'text/plain')
    resp.form['f2$element0$f123'] = 'plop'
    resp = resp.form.submit('submit')
    resp = resp.form.submit('submit')
    assert formdef.data_class().count() == 1

    # check with remote json
    ds = {'type': 'json', 'value': 'http://remote.example.net/json'}
    formdef.fields[0] = fields.ItemsField(
        id='0',
        label='items',
        data_source=ds,
        display_disabled_items=True,
        prefill={'type': 'string', 'value': prefill_value},
    )
    formdef.store()

    with responses.RequestsMock() as rsps:
        rsps.get(
            'http://remote.example.net/json',
            json={
                'data': [
                    {'id': 'foo', 'text': 'hello'},
                    {'id': 'bar', 'text': 'world'},
                    {'id': 'baz', 'text': '!'},
                ]
            },
        )

        resp = get_app(pub).get('/test/')
        assert resp.form['f0$elementfoo'].checked
        assert not resp.form['f0$elementbar'].checked
        assert resp.form['f0$elementbaz'].checked

        # check with template returning a complex data
        formdef.fields[0] = fields.ItemsField(
            id='0',
            varname='items',
            label='items',
            data_source=ds,
            display_disabled_items=True,
            prefill={'type': 'string', 'value': '{{form_objects|first|get:"form_var_items_raw"}}'},
        )
        formdef.store()

        # it will use foo,bar as selected in the first part of this test
        resp = get_app(pub).get('/test/')
        assert resp.form['f0$elementfoo'].checked
        assert resp.form['f0$elementbar'].checked
        assert not resp.form['f0$elementbaz'].checked

        # check with complex data of wrong type
        for invalid_prefill_value in [
            {'type': 'string', 'value': '{{form_objects|first|get:"form_var_file_raw"}}'},
            {'type': 'string', 'value': '{{form_objects|first|get:"form_var_foobar"}}'},
        ]:
            formdef.fields[0] = fields.ItemsField(
                id='0',
                varname='items',
                label='items',
                data_source=ds,
                display_disabled_items=True,
                prefill=invalid_prefill_value,
            )
            formdef.store()

            resp = get_app(pub).get('/test/')
            assert not resp.form['f0$elementfoo'].checked
            assert not resp.form['f0$elementbar'].checked
            assert not resp.form['f0$elementbaz'].checked

            assert LoggedError.count() == 1
            logged_error = LoggedError.select()[0]
            assert logged_error.summary == 'Invalid value for items prefill on field "items"'
            LoggedError.wipe()

        # check with a "none" explicit prefill, or a None value
        for none_prefill_value in [
            {},
            {'type': 'none'},
            {'type': 'string', 'value': '{{ None }}'},
        ]:
            formdef.fields[0] = fields.ItemsField(
                id='0',
                varname='items',
                label='items',
                data_source=ds,
                display_disabled_items=True,
                prefill=none_prefill_value,
            )
            formdef.store()

            # all checkboxes will be left unchecked
            resp = get_app(pub).get('/test/')
            assert not resp.form['f0$elementfoo'].checked
            assert not resp.form['f0$elementbar'].checked
            assert not resp.form['f0$elementbaz'].checked
            assert LoggedError.count() == 0


def test_form_page_template_prefill_items_field_autocomplete(pub):
    BlockDef.wipe()
    create_user(pub)

    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.StringField(id='123', required='required', label='Test', varname='test'),
    ]
    block.store()

    formdef = create_formdef()
    formdef.data_class().wipe()
    formdef.fields = [
        fields.ItemsField(
            id='0',
            label='items',
            items=['foo', 'bar', 'baz'],
            prefill={'type': 'string', 'value': 'foo|baz'},
            display_mode='autocomplete',
        ),
        fields.FileField(id='1', label='file', varname='file'),
        fields.BlockField(id='2', label='test', block_slug='foobar', varname='foobar'),
    ]
    formdef.store()

    resp = get_app(pub).get('/test/')
    assert resp.form['f0[]'].value == ['foo', 'baz']
    # this selection will be reused in the complex data test
    resp.form['f0[]'].value = ['foo', 'bar']
    assert 'widget-prefilled' in resp.text
    resp.form['f1$file'] = Upload('test.txt', b'foobar', 'text/plain')
    resp.form['f2$element0$f123'] = 'plop'
    resp = resp.form.submit('submit')
    resp = resp.form.submit('submit')
    assert formdef.data_class().count() == 1

    # check with remote json
    ds = {'type': 'json', 'value': 'http://remote.example.net/json'}
    formdef.fields[0] = fields.ItemsField(
        id='0',
        label='items',
        data_source=ds,
        display_disabled_items=True,
        prefill={'type': 'string', 'value': 'foo|baz'},
        display_mode='autocomplete',
    )
    formdef.store()

    with responses.RequestsMock() as rsps:
        rsps.get(
            'http://remote.example.net/json',
            json={
                'data': [
                    {'id': 'foo', 'text': 'hello'},
                    {'id': 'bar', 'text': 'world'},
                    {'id': 'baz', 'text': '!'},
                ]
            },
        )

        resp = get_app(pub).get('/test/')
        assert resp.form['f0[]'].value == ['foo', 'baz']

        # check with template returning a complex data
        formdef.fields[0] = fields.ItemsField(
            id='0',
            varname='items',
            label='items',
            data_source=ds,
            display_disabled_items=True,
            prefill={'type': 'string', 'value': '{{form_objects|first|get:"form_var_items_raw"}}'},
            display_mode='autocomplete',
        )
        formdef.store()

        # it will use foo,bar as selected in the first part of this test
        resp = get_app(pub).get('/test/')
        assert resp.form['f0[]'].value == ['foo', 'bar']

        # check with complex data of wrong type
        for invalid_prefill_value in [
            {'type': 'string', 'value': '{{form_objects|first|get:"form_var_file_raw"}}'},
            {'type': 'string', 'value': '{{form_objects|first|get:"form_var_foobar"}}'},
        ]:
            formdef.fields[0] = fields.ItemsField(
                id='0',
                varname='items',
                label='items',
                data_source=ds,
                display_disabled_items=True,
                prefill=invalid_prefill_value,
                display_mode='autocomplete',
            )
            formdef.store()

            resp = get_app(pub).get('/test/')
            assert resp.form['f0[]'].value is None

            assert LoggedError.count() == 1
            logged_error = LoggedError.select()[0]
            assert logged_error.summary == 'Invalid value for items prefill on field "items"'
            LoggedError.wipe()

        # check with a "none" explicit prefill, or a None value
        for none_prefill_value in [
            {},
            {'type': 'none'},
            {'type': 'string', 'value': '{{ None }}'},
        ]:
            formdef.fields[0] = fields.ItemsField(
                id='0',
                varname='items',
                label='items',
                data_source=ds,
                display_disabled_items=True,
                prefill=none_prefill_value,
                display_mode='autocomplete',
            )
            formdef.store()

            # all checkboxes will be left unchecked
            resp = get_app(pub).get('/test/')
            assert resp.form['f0[]'].value is None
            assert LoggedError.count() == 0


def test_form_page_changing_prefill(pub):
    formdef = create_formdef()
    formdef.data_class().wipe()
    formdef.fields = [
        fields.PageField(id='0', label='1st page'),
        fields.StringField(id='1', label='string', varname='foo'),
        fields.PageField(id='2', label='2nd page'),
        fields.StringField(
            id='3', label='string 2', prefill={'type': 'string', 'value': '{{ form_var_foo }} World'}
        ),
        fields.PageField(id='4', label='3rd page'),
    ]
    formdef.store()

    resp = get_app(pub).get('/test/')
    resp.form['f1'] = 'Hello'
    resp = resp.form.submit('submit')  # -> 2nd page
    assert resp.form['f3'].value == 'Hello World'
    resp = resp.form.submit('submit')  # -> 3rd page
    resp = resp.form.submit('previous')  # back to 2nd page
    assert resp.form['f3'].value == 'Hello World'
    resp = resp.form.submit('previous')  # back to 1st page
    assert resp.form['f1'].value == 'Hello'
    resp.form['f1'] = 'Goodbye Cruel'
    resp = resp.form.submit('submit')  # -> 2nd page
    assert resp.form['f3'].value == 'Goodbye Cruel World'
    resp = resp.form.submit('submit')  # -> 3rd page
    resp = resp.form.submit('previous')  # back to 2nd page
    resp.form['f3'].value = 'Changed value'
    resp = resp.form.submit('previous')  # back to 1st page
    resp = resp.form.submit('submit')  # -> 2nd page
    assert resp.form['f3'].value == 'Changed value'
    resp = resp.form.submit('submit')  # -> 3rd page
    resp = resp.form.submit('submit')  # -> 2nd page
    assert resp.form['f3'].value == 'Changed value'


def test_form_page_changing_prefill_draft(pub):
    formdef = create_formdef()
    formdef.data_class().wipe()
    formdef.fields = [
        fields.PageField(id='0', label='1st page'),
        fields.StringField(id='1', label='string', varname='foo'),
        fields.PageField(id='2', label='2nd page'),
        fields.StringField(
            id='3', label='string 2', prefill={'type': 'string', 'value': '{{ form_var_foo }} World'}
        ),
        fields.PageField(id='4', label='3rd page'),
    ]
    formdef.enable_tracking_codes = True
    formdef.store()

    resp = get_app(pub).get('/test/')
    resp.form['f1'] = 'Hello'
    resp = resp.form.submit('submit')  # -> 2nd page
    assert resp.form['f3'].value == 'Hello World'
    resp = resp.form.submit('submit')  # -> 3rd page
    resp = resp.form.submit('previous')  # back to 2nd page
    assert resp.form['f3'].value == 'Hello World'
    resp = resp.form.submit('submit')  # -> 3rd page
    tracking_code = get_displayed_tracking_code(resp)

    # start with a new session and restore draft using the tracking code
    resp = get_app(pub).get('/')
    resp.form['code'] = tracking_code
    resp = resp.form.submit().follow().follow().follow()
    assert_current_page(resp, '3rd page')
    resp = resp.forms[1].submit('previous')  # back to 2nd page
    assert resp.forms[1]['f3'].value == 'Hello World'
    resp = resp.forms[1].submit('previous')  # back to 1st page
    assert resp.forms[1]['f1'].value == 'Hello'
    resp.forms[1]['f1'] = 'Goodbye Cruel'
    resp = resp.forms[1].submit('submit')  # -> 2nd page
    assert resp.forms[1]['f3'].value == 'Goodbye Cruel World'


def test_form_page_changing_prefill_date(pub):
    formdef = create_formdef()
    formdef.data_class().wipe()
    formdef.fields = [
        fields.PageField(id='0', label='1st page'),
        fields.DateField(id='1', label='date', varname='foo'),
        fields.PageField(id='2', label='2nd page'),
        fields.DateField(
            id='3', label='date2', prefill={'type': 'string', 'value': '{{ form_var_foo|add_days:1 }}'}
        ),
        fields.PageField(id='4', label='3rd page'),
    ]
    formdef.store()

    resp = get_app(pub).get('/test/')
    resp.form['f1'] = '2024-05-10'
    resp = resp.form.submit('submit')  # -> 2nd page
    assert resp.form['f3'].value == '2024-05-11'
    resp = resp.form.submit('submit')  # -> 3rd page
    resp = resp.form.submit('previous')  # back to 2nd page
    assert resp.form['f3'].value == '2024-05-11'
    resp = resp.form.submit('previous')  # back to 1st page
    assert resp.form['f1'].value == '2024-05-10'
    resp.form['f1'] = '2024-05-20'
    resp = resp.form.submit('submit')  # -> 2nd page
    assert resp.form['f3'].value == '2024-05-21'
    resp = resp.form.submit('submit')  # -> 3rd page
    resp = resp.form.submit('previous')  # back to 2nd page
    resp.form['f3'].value = '2024-05-30'
    resp = resp.form.submit('previous')  # back to 1st page
    resp = resp.form.submit('submit')  # -> 2nd page
    assert resp.form['f3'].value == '2024-05-30'
    resp = resp.form.submit('submit')  # -> 3rd page
    resp = resp.form.submit('submit')  # -> 2nd page
    assert resp.form['f3'].value == '2024-05-30'


def test_prefill_query_parameter(pub):
    formdef = create_formdef()
    formdef.data_class().wipe()
    formdef.fields = [
        fields.PageField(id='0', label='1st page'),
        fields.StringField(
            id='1',
            label='str',
            varname='foo',
            required='optional',
            prefill={'type': 'string', 'value': '{{request.GET.prefill}}'},
        ),
        fields.PageField(id='2', label='2nd page'),
    ]
    formdef.store()

    resp = get_app(pub).get('/test/?prefill=Hello')
    assert resp.form['f1'].value == 'Hello'
    resp = resp.form.submit('submit')  # -> 2nd page
    resp = resp.form.submit('previous')  # back to 1st page
    # check it has not be reset to the empty string (as there's no request.GET
    # anymore)
    assert resp.form['f1'].value == 'Hello'


def test_form_table_rows_field_and_prefill(pub, emails):
    formdef = create_formdef()
    formdef.fields = [
        fields.TableRowsField(id='0', label='table', columns=['a', 'b'], required='required'),
        fields.StringField(id='1', label='string', prefill={'type': 'string', 'value': 'HELLO WORLD'}),
    ]
    formdef.store()
    formdef.data_class().wipe()

    get_app(pub).get('/test/')


def test_form_map_field_prefill_address(pub):
    formdef = create_formdef()
    formdef.fields = [
        fields.PageField(id='0', label='1st page'),
        fields.StringField(id='1', label='address', required='required', varname='address'),
        fields.PageField(id='2', label='2nd page'),
        fields.MapField(id='3', label='map', prefill={'type': 'string', 'value': '{{ form_var_address }}'}),
    ]
    formdef.store()
    resp = get_app(pub).get('/test/')
    formdef.data_class().wipe()
    resp.form['f1'] = '169 rue du chateau, paris'
    with responses.RequestsMock() as rsps:
        rsps.get(
            'https://nominatim.openstreetmap.org/search', json=[{'lat': '48.8337085', 'lon': '2.3233693'}]
        )
        resp = resp.form.submit('submit')
        assert resp.form['f3$latlng'].value == '48.8337085;2.3233693'
        assert 'chateau' in rsps.calls[0].request.url


def test_form_map_field_prefill_coords(pub):
    formdef = create_formdef()
    formdef.fields = [
        fields.PageField(id='0', label='1st page'),
        fields.MapField(id='1', label='map', varname='map1'),
        fields.PageField(id='2', label='2nd page'),
        fields.MapField(id='3', label='map', prefill={'type': 'string', 'value': '{{ form_var_map1 }}'}),
    ]
    formdef.store()
    resp = get_app(pub).get('/test/')
    formdef.data_class().wipe()
    resp.form['f1$latlng'] = '1.234;-1.234'
    resp = resp.form.submit('submit')
    assert resp.form['f3$latlng'].value == '1.234;-1.234'


def test_form_page_profile_verified_prefill(pub):
    user = create_user(pub)
    formdef = create_formdef()
    formdef.data_class().wipe()
    formdef.fields = [fields.StringField(id='0', label='string', prefill={'type': 'user', 'value': 'email'})]
    formdef.store()

    resp = get_app(pub).get('/test/')
    assert resp.form['f0'].value == ''

    resp = login(get_app(pub), username='foo', password='foo').get('/test/')
    assert resp.form['f0'].value == 'foo@localhost'
    assert 'readonly' not in resp.form['f0'].attrs
    resp.form['f0'].value = 'Hello'
    resp = resp.form.submit('submit')
    assert 'Check values then click submit.' in resp.text
    assert resp.form['f0'].value == 'Hello'

    user.verified_fields = ['email']
    user.store()

    for prefill_settings in (
        {'type': 'user', 'value': 'email'},  # verified profile
        {'type': 'string', 'value': 'foo@localhost', 'locked': True},  # locked value
    ):
        formdef.confirmation = True
        formdef.fields[0].prefill = prefill_settings
        formdef.store()
        formdef.data_class().wipe()
        resp = login(get_app(pub), username='foo', password='foo').get('/test/')
        assert resp.form['f0'].value == 'foo@localhost'
        assert 'readonly' in resp.form['f0'].attrs

        resp.form['f0'].value = 'Hello'  # try changing the value
        resp = resp.form.submit('submit')
        assert 'Check values then click submit.' in resp.text
        assert resp.form['f0'].value == 'foo@localhost'  # it is reverted

        resp.form['f0'].value = 'Hello'  # try again changing the value
        resp = resp.form.submit('submit')

        formdatas = [x for x in formdef.data_class().select() if not x.is_draft()]
        assert len(formdatas) == 1
        assert formdatas[0].data['0'] == 'foo@localhost'

        resp = login(get_app(pub), username='foo', password='foo').get('/test/')
        assert resp.form['f0'].value == 'foo@localhost'
        resp = resp.form.submit('submit')
        assert 'Check values then click submit.' in resp.text
        resp.form['f0'].value = 'Hello'  # try changing
        resp = resp.form.submit('previous')
        assert 'readonly' in resp.form['f0'].attrs
        assert 'Check values then click submit.' not in resp.text
        assert resp.form['f0'].value == 'foo@localhost'

        # try it without validation page
        formdef.confirmation = False
        formdef.store()
        formdef.data_class().wipe()

        resp = login(get_app(pub), username='foo', password='foo').get('/test/')
        assert resp.form['f0'].value == 'foo@localhost'
        assert 'readonly' in resp.form['f0'].attrs

        resp.form['f0'].value = 'Hello'  # try changing the value
        resp = resp.form.submit('submit')

        formdatas = [x for x in formdef.data_class().select() if not x.is_draft()]
        assert len(formdatas) == 1
        assert formdatas[0].data['0'] == 'foo@localhost'


def test_form_page_verified_prefill_error_page(pub):
    user = create_user(pub)
    user.verified_fields = ['email']
    user.store()
    formdef = create_formdef()
    formdef.data_class().wipe()
    formdef.fields = [
        fields.StringField(id='0', label='string', required='optional'),
        fields.StringField(id='1', label='string2', required='required'),
    ]
    formdef.store()

    for prefill_settings in (
        {'type': 'user', 'value': 'email'},  # verified profile
        {'type': 'string', 'value': 'foo@localhost', 'locked': True},  # locked value
    ):
        formdef.fields[0].prefill = prefill_settings
        formdef.store()
        formdef.data_class().wipe()
        resp = login(get_app(pub), username='foo', password='foo').get('/test/')
        assert resp.form['f0'].value == 'foo@localhost'
        assert 'readonly' in resp.form['f0'].attrs

        resp = resp.form.submit('submit')
        assert 'There were errors processing the form' in resp.text
        assert 'readonly' in resp.form['f0'].attrs


def test_form_page_profile_verified_date_prefill(pub):
    user = create_user(pub)

    user_formdef = UserFieldsFormDef(pub)
    user_formdef.fields.append(fields.DateField(id='_date', label='date'))
    user_formdef.store()
    user.form_data = {'_date': time.strptime('2018-09-27', '%Y-%m-%d')}
    user.set_attributes_from_formdata(user.form_data)
    user.store()

    formdef = create_formdef()
    formdef.data_class().wipe()
    formdef.fields = [fields.DateField(id='0', label='date', prefill={'type': 'user', 'value': '_date'})]
    formdef.store()

    resp = get_app(pub).get('/test/')
    assert resp.form['f0'].value == ''

    resp = login(get_app(pub), username='foo', password='foo').get('/test/')
    assert resp.form['f0'].value == '2018-09-27'
    assert 'readonly' not in resp.form['f0'].attrs
    assert resp.form['f0'].attrs['type'] == 'date'
    resp.form['f0'].value = '2018-09-27'
    resp = resp.form.submit('submit')
    assert 'Check values then click submit.' in resp.text
    assert resp.form['f0'].value == '2018-09-27'

    user.verified_fields = ['_date']
    user.store()

    resp = login(get_app(pub), username='foo', password='foo').get('/test/')
    assert resp.form['f0'].value == '2018-09-27'
    # for readonly values there is a <input type=hidden> with the real value
    # then an unnamed <input type=text> with the formatted value.
    assert resp.form['f0'].attrs['type'] == 'hidden'
    assert resp.pyquery('input#form_f0[type=text][readonly=readonly]')

    resp.form['f0'].value = '2018-09-24'  # try changing the value
    resp = resp.form.submit('submit')
    assert 'Check values then click submit.' in resp.text
    assert resp.form['f0'].value == '2018-09-27'  # it is reverted

    resp.form['f0'].value = '2018-09-24'  # try again changing the value
    resp = resp.form.submit('submit')

    formdatas = [x for x in formdef.data_class().select() if not x.is_draft()]
    assert len(formdatas) == 1
    assert time.strftime('%Y-%m-%d', formdatas[0].data['0']) == '2018-09-27'


def test_form_page_profile_date_as_locked_string_prefill(pub):
    user = create_user(pub)

    user_formdef = UserFieldsFormDef(pub)
    user_formdef.fields.append(fields.DateField(id='_date', label='date'))
    user_formdef.store()
    user.form_data = {'_date': time.strptime('2018-09-27', '%Y-%m-%d')}
    user.set_attributes_from_formdata(user.form_data)
    user.store()

    formdef = create_formdef()
    formdef.data_class().wipe()
    formdef.fields = [
        fields.PageField(id='1', label='1st page'),
        fields.PageField(id='2', label='2nd page'),
        fields.StringField(id='0', label='date', prefill={'type': 'user', 'value': '_date', 'locked': True}),
    ]
    formdef.store()

    resp = login(get_app(pub), username='foo', password='foo').get('/test/')
    resp = resp.form.submit('submit')  # -> second page
    assert resp.form['f0'].value == '2018-09-27'
    assert 'readonly' in resp.form['f0'].attrs
    resp.form['f0'].value = '2015-09-27'
    resp = resp.form.submit('submit')
    assert 'Check values then click submit.' in resp.text
    assert resp.form['f0'].value == '2018-09-27'


def test_form_page_profile_verified_radio_item_prefill(pub):
    user = create_user(pub)
    formdef = create_formdef()
    formdef.data_class().wipe()
    formdef.fields = [
        fields.ItemField(
            id='0',
            label='item',
            items=['bar@localhost', 'foo@localhost', 'baz@localhost'],
            display_mode='radio',
            prefill={'type': 'user', 'value': 'email'},
        )
    ]
    formdef.store()

    resp = get_app(pub).get('/test/')
    assert resp.form['f0'].value is None

    user.verified_fields = ['email']
    user.store()

    resp = login(get_app(pub), username='foo', password='foo').get('/test/')
    assert resp.form['f0'].value == 'foo@localhost'
    assert 'disabled' in resp.form['f0'].attrs
    for radio in resp.html.findAll('input'):
        if radio['name'] == 'f0':
            if radio['value'] == 'foo@localhost':
                assert radio.attrs.get('checked')
                assert not radio.attrs.get('disabled')
            else:
                assert not radio.attrs.get('checked')
                assert radio.attrs.get('disabled')

    resp.form['f0'].value = 'baz@localhost'  # try changing the value
    resp = resp.form.submit('submit')
    assert 'Check values then click submit.' in resp.text
    assert resp.form['f0'].value == 'foo@localhost'  # it is reverted


def test_file_prefill_on_edit(pub, http_requests):
    user = create_user(pub)
    formdef = create_formdef()
    formdef.fields = [fields.FileField(id='0', label='file', varname='foo_file')]
    formdef.store()
    formdef.data_class().wipe()

    workflow = Workflow(name='test')
    st1 = workflow.add_status('New', 'st1')
    st2 = workflow.add_status('CreateFormdata')

    editable = st1.add_action('editable', id='_editable')
    editable.by = ['_submitter', '_receiver']

    jump = st1.add_action('choice', id='_resubmit')
    jump.label = 'Resubmit'
    jump.by = ['_submitter']
    jump.status = st2.id

    create_formdata = st2.add_action('create_formdata', id='_create_formdata')
    create_formdata.formdef_slug = formdef.url_name
    create_formdata.mappings = [
        Mapping(field_id='0', expression='{{form_var_foo_file}}'),
    ]

    workflow.store()
    formdef.workflow_id = workflow.id
    formdef.store()

    upload = Upload('test.txt', b'foobar', 'text/plain')
    resp = get_app(pub).get('/test/')
    resp.forms[0]['f0$file'] = upload
    resp = resp.form.submit('submit')  # -> validation page
    resp = resp.form.submit('submit')  # -> submit

    assert resp.status_int == 302
    resp = resp.follow()
    assert 'The form has been recorded' in resp.text
    resp = resp.click('test.txt')
    assert resp.location.endswith('/test.txt')
    resp = resp.follow()
    assert resp.content_type == 'text/plain'
    assert resp.text == 'foobar'

    formdata = formdef.data_class().select()[0]
    formdata.user_id = user.id
    formdata.store()

    app = login(get_app(pub), username='foo', password='foo')
    resp = app.get('/test/%s/' % formdata.id)
    assert 'button_editable-button' in resp.text

    # go to edition page
    resp = resp.form.submit('button_editable').follow()
    # file is "prefilled"
    assert 'test.txt' in resp.text

    # go back to form page and trigger formdata creation
    resp = app.get('/test/%s/' % formdata.id)
    resp = resp.form.submit('button_resubmit')
    assert resp.status == '303 See Other'
    resp = resp.follow()

    assert formdef.data_class().count() == 2
    new_formdata = formdef.data_class().select(lambda x: str(x.id) != str(formdata.id))[0]
    assert new_formdata.data['0'].orig_filename == 'test.txt'
    assert new_formdata.data['0'].get_content() == b'foobar'

    resp = app.get('/test/%s/' % new_formdata.id)
    assert 'button_editable-button' in resp.text
    # go to edition page
    resp = resp.form.submit('button_editable').follow()
    # file is "prefilled"
    assert 'test.txt' in resp.text
    # and persist after being saved again
    resp = resp.form.submit('submit').follow()
    assert '<span>test.txt</span>' in resp.text


def test_form_page_prefill_and_table_field(pub):
    create_user(pub)
    formdef = create_formdef()
    formdef.data_class().wipe()
    formdef.fields = [
        fields.StringField(id='1', label='string', prefill={'type': 'string', 'value': 'HELLO WORLD'}),
        fields.TableField(id='2', label='table', rows=['A', 'B'], columns=['a', 'b', 'c']),
    ]
    formdef.store()

    resp = get_app(pub).get('/test/')
    assert resp.forms[0]['f1'].value == 'HELLO WORLD'
    assert not resp.pyquery('.widget-with-error')

    # check it also works on second page
    formdef.fields = [
        fields.PageField(id='0', label='page1'),
        fields.PageField(id='3', label='page2'),
        fields.StringField(id='1', label='string', prefill={'type': 'string', 'value': 'HELLO WORLD'}),
        fields.TableField(id='2', label='table', rows=['A', 'B'], columns=['a', 'b', 'c']),
    ]
    formdef.store()

    resp = get_app(pub).get('/test/')
    resp = resp.form.submit('submit')
    assert resp.forms[0]['f1'].value == 'HELLO WORLD'
    assert not resp.pyquery('.widget-with-error')


def test_form_page_prefill_and_tablerows_field(pub):
    create_user(pub)
    formdef = create_formdef()
    formdef.data_class().wipe()
    formdef.fields = [
        fields.StringField(id='1', label='string', prefill={'type': 'string', 'value': 'HELLO WORLD'}),
        fields.TableRowsField(id='2', label='table', columns=['a', 'b', 'c']),
    ]
    formdef.store()

    resp = get_app(pub).get('/test/')
    assert resp.forms[0]['f1'].value == 'HELLO WORLD'
    assert not resp.pyquery('.widget-with-error')

    # check it also works on second page
    formdef.fields = [
        fields.PageField(id='0', label='page1'),
        fields.PageField(id='3', label='page2'),
        fields.StringField(id='1', label='string', prefill={'type': 'string', 'value': 'HELLO WORLD'}),
        fields.TableRowsField(id='2', label='table', columns=['a', 'b', 'c']),
    ]
    formdef.store()

    resp = get_app(pub).get('/test/')
    resp = resp.form.submit('submit')
    assert resp.forms[0]['f1'].value == 'HELLO WORLD'
    assert not resp.pyquery('.widget-with-error')


def test_form_page_user_data_source(pub):
    user = create_user(pub)

    NamedDataSource.wipe()
    data_source = NamedDataSource(name='foobar')
    data_source.data_source = {'type': 'wcs:users'}
    data_source.store()

    formdef = create_formdef()
    formdef.data_class().wipe()

    for prefill_value in ('{{ session_user }}', '{{ session_user_id }}'):
        formdef.fields = [
            fields.ItemField(
                id='1',
                label='item',
                varname='item',
                hint='help text',
                required='optional',
                data_source={'type': data_source.slug},
                prefill={'type': 'string', 'value': prefill_value},
            )
        ]
        formdef.store()

        resp = get_app(pub).get('/test/')
        assert resp.form['f1'].value == ''
        assert 'invalid value selected' not in resp.text

        resp = login(get_app(pub), username='foo', password='foo').get('/test/')
        assert resp.form['f1'].value == str(user.id)
        assert 'invalid value selected' not in resp.text


def test_form_page_template_block_rows_prefilled_with_form_data(pub):
    BlockDef.wipe()
    create_user(pub)

    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.StringField(
            id='123',
            required='required',
            label='Test',
            varname='test',
            prefill={'type': 'string', 'value': '{{ form_var_foo }}'},
        ),
    ]
    block.store()

    formdef = create_formdef()
    formdef.data_class().wipe()
    formdef.fields = [
        fields.PageField(id='1', label='page1'),
        fields.StringField(id='2', label='text', varname='foo'),
        fields.PageField(id='3', label='page2'),
        fields.BlockField(id='4', label='test', block_slug='foobar', max_items='3'),
    ]
    formdef.store()

    resp = get_app(pub).get('/test/')
    resp.form['f2'] = 'foo'
    resp = resp.form.submit('submit')  # -> second page
    assert resp.form['f4$element0$f123'].value == 'foo'
    resp = resp.form.submit('f4$add_element')
    assert resp.form['f4$element1$f123'].value == 'foo'
    resp.form['f4$element0$f123'] = 'bar'
    resp = resp.form.submit('previous')  # -> first page
    resp.form['f2'] = 'baz'
    resp = resp.form.submit('submit')  # -> second page
    assert resp.form['f4$element0$f123'].value == 'bar'  # not changed
    assert resp.form['f4$element1$f123'].value == 'baz'  # updated


def test_prefill_locked_unless_empty(pub):
    create_user(pub)
    formdef = create_formdef()
    formdef.data_class().wipe()
    formdef.fields = [
        fields.PageField(id='1', label='page'),
        fields.StringField(id='2', label='string', varname='string', required='optional'),
        fields.PageField(id='3', label='page2'),
        fields.StringField(
            id='4',
            label='string',
            prefill={
                'type': 'string',
                'value': '{{form_var_string|default:""}}',
                'locked': True,
                'locked-unless-empty': True,
            },
        ),
    ]
    formdef.store()

    resp = get_app(pub).get('/test/')
    resp.form['f2'] = 'hello'
    resp = resp.form.submit('submit')
    assert resp.form['f4'].value == 'hello'
    assert resp.form['f4'].attrs.get('readonly')

    resp = get_app(pub).get('/test/')
    resp.form['f2'] = ''
    resp = resp.form.submit('submit')
    assert resp.form['f4'].value == ''
    assert not resp.form['f4'].attrs.get('readonly')


def test_prefill_logged_error(pub):
    LoggedError.wipe()
    user = create_user(pub)

    formdef = create_formdef()
    formdef.data_class().wipe()
    formdef.fields = [
        fields.StringField(
            id='0',
            label='test',
            prefill={'type': 'string', 'value': '{% token length="abc" %}'},
        )
    ]
    formdef.store()

    login(get_app(pub), username='foo', password='foo').get('/test/')
    assert LoggedError.count() == 1
    assert LoggedError.select()[0].exception_class == 'TemplateError'
    assert LoggedError.select()[0].summary == 'Failed to evaluate prefill on field "test"'
    assert LoggedError.select()[0].formdef_id == str(formdef.id)
    LoggedError.wipe()

    wf = Workflow(name='test')
    status = wf.add_status('new')
    display_form = status.add_action('form')
    display_form.by = ['_submitter']
    display_form.varname = 'blah'
    display_form.formdef = WorkflowFormFieldsFormDef(item=display_form)
    display_form.formdef.fields = [
        fields.StringField(
            id='1',
            label='Test',
            prefill={'type': 'string', 'value': '{% token length="abc" %}'},
        )
    ]
    wf.store()
    formdef.workflow = wf
    formdef.store()

    formdata = formdef.data_class()()
    formdata.user_id = user.id
    formdata.just_created()
    formdata.store()
    login(get_app(pub), username='foo', password='foo').get(formdata.get_url())
    assert LoggedError.count() == 1
    assert LoggedError.select()[0].exception_class == 'TemplateError'
    assert LoggedError.select()[0].summary == 'Failed to evaluate prefill on field "Test"'
    assert LoggedError.select()[0].formdef_id == str(formdef.id)
    assert LoggedError.select()[0].formdata_id == str(formdata.id)
