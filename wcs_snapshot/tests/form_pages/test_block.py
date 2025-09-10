import datetime
import json

import pytest
import responses
from webtest import Checkbox, Upload

from wcs import fields
from wcs.blocks import BlockDef
from wcs.carddef import CardDef
from wcs.categories import Category
from wcs.data_sources import NamedDataSource
from wcs.formdef import FormDef
from wcs.logged_errors import LoggedError
from wcs.qommon.substitution import CompatibilityNamesDict
from wcs.wf.form import WorkflowFormFieldsFormDef
from wcs.workflows import Workflow

from ..utilities import clean_temporary_pub, create_temporary_pub, get_app, login
from .test_all import create_user, create_user_and_admin


def pytest_generate_tests(metafunc):
    if 'pub' in metafunc.fixturenames:
        metafunc.parametrize('pub', ['sql', 'sql-lazy'], indirect=True)


@pytest.fixture
def pub(request, emails):
    pub = create_temporary_pub(
        lazy_mode=bool('lazy' in request.param),
    )
    pub.cfg['identification'] = {'methods': ['password']}
    pub.cfg['language'] = {'language': 'en'}
    pub.write_cfg()

    if Category.count() == 0:
        cat = Category(name='foobar')
        cat.store()

    return pub


def teardown_module(module):
    clean_temporary_pub()


def test_block_simple(pub):
    FormDef.wipe()
    BlockDef.wipe()

    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.StringField(id='123', required='required', label='Test'),
        fields.StringField(id='234', required='required', label='Test2'),
    ]
    block.store()

    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.BlockField(id='1', label='test', block_slug='foobar', hint='hintblock'),
    ]
    formdef.store()

    app = get_app(pub)
    resp = app.get(formdef.get_url())
    assert resp.text.count('>hintblock<') == 1
    resp.form['f1$element0$f123'] = 'foo'
    resp.form['f1$element0$f234'] = 'bar'
    resp = resp.form.submit('submit')  # -> validation page
    assert resp.form['f1$element0$f123'].attrs['readonly']
    assert resp.form['f1$element0$f123'].value == 'foo'
    assert resp.form['f1$element0$f234'].attrs['readonly']
    assert resp.form['f1$element0$f234'].value == 'bar'
    resp = resp.form.submit('submit')  # -> end page
    resp = resp.follow()
    assert '>foo<' in resp
    assert '>bar<' in resp


def test_block_a11y(pub):
    FormDef.wipe()
    BlockDef.wipe()

    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.StringField(id='123', label='Test'),
        fields.StringField(id='234', label='Test2'),
    ]
    block.store()

    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.BlockField(id='1', label='test', block_slug='foobar'),
    ]
    formdef.store()

    app = get_app(pub)
    resp = app.get(formdef.get_url())
    assert resp.pyquery('.BlockWidget')[0].attrib.get('role') == 'group'
    assert resp.pyquery('.BlockWidget')[0].attrib.get('aria-labelledby')
    assert resp.pyquery('#' + resp.pyquery('.BlockWidget')[0].attrib.get('aria-labelledby'))

    formdef.fields[0].label_display = 'subtitle'
    formdef.store()
    resp = app.get(formdef.get_url())
    assert resp.pyquery('.BlockWidget')[0].attrib.get('role')
    assert resp.pyquery('.BlockWidget')[0].attrib.get('aria-labelledby')
    assert resp.pyquery('#' + resp.pyquery('.BlockWidget')[0].attrib.get('aria-labelledby'))

    formdef.fields[0].label_display = 'hidden'
    formdef.store()
    resp = app.get(formdef.get_url())
    assert not resp.pyquery('.BlockWidget')[0].attrib.get('role')
    assert not resp.pyquery('.BlockWidget')[0].attrib.get('aria-labelledby')


def test_block_required(pub):
    FormDef.wipe()
    BlockDef.wipe()

    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.StringField(id='123', required='required', label='Test'),
        fields.StringField(id='234', required='required', label='Test2'),
    ]
    block.store()

    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.BlockField(id='1', label='test', block_slug='foobar'),
    ]
    formdef.store()

    app = get_app(pub)
    resp = app.get(formdef.get_url())
    resp = resp.form.submit('submit')  # -> error page
    assert 'There were errors processing the form' in resp
    assert len(resp.pyquery('.error:not(#form_error_fieldname)')) == 1
    resp.form['f1$element0$f123'] = 'foo'
    resp.form['f1$element0$f234'] = 'bar'
    resp = resp.form.submit('submit')  # -> validation page
    assert 'Check values then click submit.' in resp.text

    resp = app.get(formdef.get_url())
    resp.form['f1$element0$f123'] = 'foo'
    resp = resp.form.submit('submit')  # -> error page
    assert 'There were errors processing the form' in resp
    assert len(resp.pyquery('.error:not(#form_error_fieldname)')) == 1
    resp.form['f1$element0$f234'] = 'bar'
    resp = resp.form.submit('submit')  # -> validation page
    assert 'Check values then click submit.' in resp.text

    # only one required
    block.fields = [
        fields.StringField(id='123', required='required', label='Test'),
        fields.StringField(id='234', required='optional', label='Test2'),
    ]
    block.store()

    resp = app.get(formdef.get_url())
    resp.form['f1$element0$f123'] = 'foo'
    resp = resp.form.submit('submit')  # -> validation page
    assert 'Check values then click submit.' in resp.text

    # none required, but globally required
    block.fields = [
        fields.StringField(id='123', required='optional', label='Test'),
        fields.StringField(id='234', required='optional', label='Test2'),
    ]
    block.store()

    resp = app.get(formdef.get_url())
    resp = resp.form.submit('submit')  # -> error page
    assert 'There were errors processing the form' in resp
    assert len(resp.pyquery('.error:not(#form_error_fieldname)')) == 1
    resp.form['f1$element0$f234'] = 'bar'
    resp = resp.form.submit('submit')  # -> validation page
    assert 'Check values then click submit.' in resp.text

    # block not required, one subfield required, error on page
    formdef.fields = [
        fields.BlockField(id='1', label='test', block_slug='foobar', required='optional'),
        fields.StringField(id='2', label='Foo', required='required'),
    ]
    formdef.store()
    block.fields = [
        fields.StringField(id='123', required='required', label='Test'),
        fields.StringField(id='234', required='optional', label='Test2'),
    ]
    block.store()
    resp = app.get(formdef.get_url())
    resp = resp.form.submit('submit')  # -> error page
    # block was empty, subfield is not marked as required
    assert resp.pyquery('.widget-with-error label').text() == 'Foo*'

    resp = app.get(formdef.get_url())
    resp.form['f1$element0$f234'] = 'bar'
    resp = resp.form.submit('submit')  # -> error page
    # block was not empty, subfield is also marked as required
    assert resp.pyquery('.widget-with-error label').text() == 'Test* Foo*'


def test_block_required_previous_page(pub):
    FormDef.wipe()
    BlockDef.wipe()

    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.StringField(id='123', required='required', label='Test'),
        fields.StringField(id='234', required='required', label='Test2'),
    ]
    block.store()

    for multipage in (False, True):
        # single block, go to validation page, come back
        formdef = FormDef()
        formdef.name = 'form title'
        if multipage:
            formdef.fields = [
                fields.PageField(id='0', label='1st page'),
                fields.BlockField(id='1', label='test', block_slug='foobar', required='required'),
                fields.PageField(id='2', label='2nd page'),
            ]
        else:
            formdef.fields = [
                fields.BlockField(id='1', label='test', block_slug='foobar', required='required'),
            ]
        formdef.store()
        formdef.data_class().wipe()

        app = get_app(pub)
        resp = app.get(formdef.get_url())
        resp = resp.form.submit('submit')  # -> error page
        assert 'There were errors processing the form' in resp
        assert len(resp.pyquery('.error:not(#form_error_fieldname)')) == 1
        resp.form['f1$element0$f123'] = 'foo'
        resp.form['f1$element0$f234'] = 'bar'
        if multipage:
            resp = resp.form.submit('submit')  # -> 2nd page
        resp = resp.form.submit('submit')  # -> validation page
        assert 'Check values then click submit.' in resp.text

        if multipage:
            resp = resp.form.submit('previous')  # -> 2nd page
        resp = resp.form.submit('previous')  # -> 1st page
        assert len(resp.pyquery('.error:not(#form_error_fieldname)')) == 0
        assert resp.form['f1$element0$f123'].value == 'foo'
        assert resp.form['f1$element0$f234'].value == 'bar'

        if multipage:
            resp = resp.form.submit('submit')  # -> 2nd page
        resp = resp.form.submit('submit')  # -> validation page
        assert 'Check values then click submit.' in resp.text
        resp = resp.form.submit('submit')  # -> submitted

        assert formdef.data_class().count() == 1
        formdata = formdef.data_class().select()[0]
        assert formdata.data['1']['data'] == [{'123': 'foo', '234': 'bar'}]

        # add two blocks, go to validation page, come back
        if multipage:
            formdef.fields[1].max_items = '3'
        else:
            formdef.fields[0].max_items = '3'
        formdef.store()
        formdef.data_class().wipe()

        app = get_app(pub)
        resp = app.get(formdef.get_url())
        resp.form['f1$element0$f123'] = 'foo'
        resp.form['f1$element0$f234'] = 'bar'
        resp = resp.form.submit('f1$add_element')  # -> 1st page
        resp.form['f1$element1$f123'] = 'foo2'
        resp.form['f1$element1$f234'] = 'bar2'
        if multipage:
            resp = resp.form.submit('submit')  # -> 2nd page
        resp = resp.form.submit('submit')  # -> validation page
        assert 'Check values then click submit.' in resp.text

        if multipage:
            resp = resp.form.submit('previous')  # -> 2nd page
        resp = resp.form.submit('previous')  # -> 1st page
        assert len(resp.pyquery('.error:not(#form_error_fieldname)')) == 0
        assert resp.form['f1$element0$f123'].value == 'foo'
        assert resp.form['f1$element0$f234'].value == 'bar'
        assert resp.form['f1$element1$f123'].value == 'foo2'
        assert resp.form['f1$element1$f234'].value == 'bar2'

        if multipage:
            resp = resp.form.submit('submit')  # -> 2nd page
        resp = resp.form.submit('submit')  # -> validation page
        assert 'Check values then click submit.' in resp.text
        resp = resp.form.submit('submit')  # -> submitted

        assert formdef.data_class().count() == 1
        formdata = formdef.data_class().select()[0]
        assert formdata.data['1']['data'] == [{'123': 'foo', '234': 'bar'}, {'123': 'foo2', '234': 'bar2'}]


def test_block_max_items_button_attribute(pub):
    FormDef.wipe()
    BlockDef.wipe()

    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.StringField(id='123', required='required', label='Test'),
        fields.StringField(id='234', required='required', label='Test2'),
    ]
    block.store()

    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.BlockField(id='1', label='test', block_slug='foobar'),
    ]
    formdef.store()

    app = get_app(pub)
    resp = app.get(formdef.get_url())
    assert resp.pyquery('[name="f1$add_element"]').attr.type == 'button'  # no support for "enter" key

    formdef.fields = [
        fields.BlockField(id='1', label='test', block_slug='foobar', max_items='5'),
    ]
    formdef.store()
    resp = app.get(formdef.get_url())
    assert not resp.pyquery('[name="f1$add_element"]').attr.type


def test_block_date(pub):
    FormDef.wipe()
    BlockDef.wipe()

    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.StringField(id='123', required='required', label='Test'),
        fields.DateField(id='234', required='required', label='Test2'),
    ]
    block.store()

    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.BlockField(id='1', label='test', block_slug='foobar'),
    ]
    formdef.store()

    app = get_app(pub)
    resp = app.get(formdef.get_url())
    resp.form['f1$element0$f123'] = 'foo'
    resp.form['f1$element0$f234'] = '2020-06-16'
    resp = resp.form.submit('submit')  # -> validation page
    assert 'Check values then click submit.' in resp.text
    resp = resp.form.submit('submit')  # -> submit
    resp = resp.follow()
    assert '>2020-06-16<' in resp


def test_block_bool(pub):
    FormDef.wipe()
    BlockDef.wipe()

    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.StringField(id='123', required='required', label='Test'),
        fields.BoolField(id='234', required='optional', label='Test2'),
    ]
    block.store()

    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.BlockField(id='1', label='test', block_slug='foobar'),
    ]
    formdef.store()

    for value in (True, False):
        app = get_app(pub)
        resp = app.get(formdef.get_url())
        resp.form['f1$element0$f123'] = 'foo'
        resp.form['f1$element0$f234'].checked = value
        resp = resp.form.submit('submit')  # -> validation page
        assert resp.form['f1$element0$f234disabled'].checked is value
        assert resp.form['f1$element0$f234'].value == str(value)
        assert 'Check values then click submit.' in resp
        resp = resp.form.submit('submit')  # -> submit
        resp = resp.follow()
        if value:
            assert '<p class="value">Yes</p>' in resp
        else:
            assert '<p class="value">No</p>' in resp


def test_block_autocomplete_list(pub):
    FormDef.wipe()
    BlockDef.wipe()

    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.StringField(id='123', required='required', label='Test'),
        fields.ItemField(
            id='234',
            required='required',
            label='Test2',
            display_mode='autocomplete',
            items=['Foo', 'Bar'],
        ),
    ]
    block.store()

    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.BlockField(id='1', label='test', block_slug='foobar'),
    ]
    formdef.store()

    app = get_app(pub)
    resp = app.get(formdef.get_url())
    resp.form['f1$element0$f123'] = 'foo'
    resp.form['f1$element0$f234'] = 'Bar'
    resp = resp.form.submit('submit')  # -> validation page
    assert 'Check values then click submit.' in resp.text
    resp = resp.form.submit('submit')  # -> submit
    resp = resp.follow()
    assert '>Bar<' in resp


def test_block_geoloc_prefill(pub):
    FormDef.wipe()
    BlockDef.wipe()

    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.StringField(
            id='123',
            required='required',
            label='Test',
            prefill={'type': 'geolocation', 'value': 'road'},
        ),
    ]
    block.store()

    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.BlockField(id='1', label='test', block_slug='foobar'),
    ]
    formdef.store()

    app = get_app(pub)
    resp = app.get(formdef.get_url())
    assert 'qommon.geolocation.js' in resp
    assert resp.html.find('div', {'data-geolocation': 'road'})


def test_block_string_prefill(pub):
    FormDef.wipe()
    BlockDef.wipe()

    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.StringField(
            id='123',
            required='required',
            label='Test',
            prefill={'type': 'string', 'value': '{{ form_var_foo }} World'},
        ),
    ]
    block.store()

    block2 = BlockDef()
    block2.name = 'foobar2'
    block2.fields = [
        fields.StringField(id='123', required='required', label='Test'),
    ]
    block2.store()

    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.PageField(id='0', label='1st page'),
        fields.StringField(id='1', label='string', varname='foo'),
        fields.PageField(id='2', label='2nd page'),
        fields.BlockField(id='3', label='test', block_slug='foobar'),
        fields.BlockField(id='4', label='test', block_slug='foobar2'),
    ]
    formdef.store()

    for i in range(2):
        if i == 1:
            # second pass, add another prefilled field in second block
            block2.fields.append(
                fields.StringField(
                    id='124',
                    required='required',
                    label='Test',
                    prefill={'type': 'string', 'value': '{{ form_var_foo }} World'},
                )
            )
            block2.store()

        formdef.data_class().wipe()

        app = get_app(pub)
        resp = app.get(formdef.get_url())
        resp.form['f1'] = 'Hello'
        resp = resp.form.submit('submit')  # -> 2nd page
        assert not resp.pyquery('#form_error_f3')  # not marked as error
        assert not resp.pyquery('#form_error_f4')  # ...
        assert len(resp.pyquery('.error:not(#form_error_fieldname)')) == 0
        assert resp.form['f3$element0$f123'].value == 'Hello World'
        assert resp.form['f4$element0$f123'].value == ''
        resp.form['f4$element0$f123'] = 'plop'
        resp = resp.form.submit('submit')  # -> validation page
        resp = resp.form.submit('submit')  # -> end page
        resp = resp.follow()

        formdata = formdef.data_class().select()[0]
        assert formdata.data['3']['data'][0]['123'] == 'Hello World'
        if i == 1:
            assert formdata.data['4']['data'][0]['124'] == 'Hello World'

        # check unmodified prefilled field
        app = get_app(pub)
        resp = app.get(formdef.get_url())
        resp.form['f1'] = 'Hello'
        resp = resp.form.submit('submit')  # -> 2nd page
        assert resp.form['f3$element0$f123'].value == 'Hello World'
        resp.form['f4$element0$f123'] = 'plop'
        resp = resp.form.submit('submit')  # -> validation page
        resp = resp.form.submit('previous')  # -> 2nd page
        resp = resp.form.submit('previous')  # -> 1st page
        resp.form['f1'] = 'Test'
        resp = resp.form.submit('submit')  # -> 2nd page
        assert resp.form['f3$element0$f123'].value == 'Test World'

        # check modified prefilled field
        app = get_app(pub)
        resp = app.get(formdef.get_url())
        resp.form['f1'] = 'Hello'
        resp = resp.form.submit('submit')  # -> 2nd page
        assert resp.form['f3$element0$f123'].value == 'Hello World'
        resp.form['f3$element0$f123'] = 'Foobar'
        resp.form['f4$element0$f123'] = 'plop'
        resp = resp.form.submit('submit')  # -> validation page
        resp = resp.form.submit('previous')  # -> 2nd page
        resp = resp.form.submit('previous')  # -> 1st page
        resp.form['f1'] = 'Test'
        resp = resp.form.submit('submit')  # -> 2nd page
        assert resp.form['f3$element0$f123'].value == 'Foobar'


def test_block_prefill_and_required(pub):
    FormDef.wipe()
    BlockDef.wipe()

    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.StringField(
            id='123', required='required', label='Test', prefill={'type': 'string', 'value': 'World'}
        ),
    ]
    block.store()

    block2 = BlockDef()
    block2.name = 'foobar2'
    block2.fields = [
        fields.StringField(id='123', required='required', label='Test'),
        fields.BoolField(id='234', required='required', label='Test2'),
    ]
    block2.store()

    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.ItemField(id='1', required='required', label='Test2', items=['Foo', 'Bar']),
        fields.BlockField(id='2', label='test', block_slug='foobar'),
        fields.BlockField(id='3', label='test', block_slug='foobar2'),
    ]
    formdef.store()

    formdef.data_class().wipe()

    app = get_app(pub)
    resp = app.get(formdef.get_url())
    assert len(resp.pyquery('.error:not(#form_error_fieldname)')) == 0
    assert resp.form['f2$element0$f123'].value == 'World'
    resp.form['f3$element0$f123'] = 'Hello'
    resp = resp.form.submit('submit')  # -> same page, error displyed
    assert len(resp.pyquery('.error:not(#form_error_fieldname)')) == 1
    resp.form['f3$element0$f234'].checked = True
    resp = resp.form.submit('submit')  # -> validation page
    resp = resp.form.submit('submit')  # -> end page
    resp = resp.follow()

    formdata = formdef.data_class().select()[0]
    assert formdata.data['2']['data'][0]['123'] == 'World'
    assert formdata.data['3']['data'][0]['123'] == 'Hello'
    assert formdata.data['3']['data'][0]['234'] is True


def test_block_locked_prefill(pub):
    FormDef.wipe()
    BlockDef.wipe()

    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.StringField(
            id='123',
            required='required',
            label='Test',
            prefill={'type': 'string', 'value': '{{ form_var_foo }} World', 'locked': True},
        ),
    ]
    block.store()

    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.PageField(id='0', label='1st page'),
        fields.StringField(id='1', label='string', varname='foo'),
        fields.PageField(id='2', label='2nd page'),
        fields.BlockField(id='3', label='test', block_slug='foobar'),
    ]
    formdef.store()
    formdef.data_class().wipe()

    app = get_app(pub)
    resp = app.get(formdef.get_url())
    resp.form['f1'] = 'Hello'
    resp = resp.form.submit('submit')  # -> 2nd page
    assert resp.form['f3$element0$f123'].value == 'Hello World'
    assert 'readonly' in resp.form['f3$element0$f123'].attrs
    resp.form['f3$element0$f123'].value = 'Hello'  # try changing the value
    resp = resp.form.submit('submit')  # -> validation page
    resp = resp.form.submit('submit')  # -> end page
    resp = resp.follow()

    formdata = formdef.data_class().select()[0]
    assert formdata.data['3']['data'][0]['123'] == 'Hello World'  # value got reverted


def test_block_multi_string_prefill(pub):
    FormDef.wipe()
    BlockDef.wipe()

    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.StringField(
            id='123',
            required='required',
            label='Test',
            prefill={'type': 'string', 'value': '{{ form_var_foo }} World'},
        ),
    ]
    block.store()

    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.PageField(id='0', label='1st page'),
        fields.StringField(id='1', label='string', varname='foo'),
        fields.PageField(id='2', label='2nd page'),
        fields.BlockField(id='3', label='test', block_slug='foobar', max_items='5'),
    ]
    formdef.store()
    formdef.data_class().wipe()

    app = get_app(pub)
    resp = app.get(formdef.get_url())
    resp.form['f1'] = 'Hello'
    resp = resp.form.submit('submit')  # -> 2nd page
    assert not resp.pyquery('#form_error_f3')  # not marked as error
    assert resp.form['f3$element0$f123'].value == 'Hello World'
    resp = resp.form.submit('f3$add_element')  # add second row
    assert resp.form['f3$element1$f123'].value == 'Hello World'
    resp.form['f3$element1$f123'].value = 'Something else'
    resp = resp.form.submit('f3$add_element')  # add third row
    assert resp.form['f3$element0$f123'].value == 'Hello World'
    assert resp.form['f3$element1$f123'].value == 'Something else'  # unchanged
    assert resp.form['f3$element2$f123'].value == 'Hello World'
    resp = resp.form.submit('submit')  # -> validation page
    resp = resp.form.submit('submit')  # -> end page
    resp = resp.follow()

    formdata = formdef.data_class().select()[0]
    assert formdata.data['3']['data'][0]['123'] == 'Hello World'
    assert formdata.data['3']['data'][1]['123'] == 'Something else'
    assert formdata.data['3']['data'][2]['123'] == 'Hello World'


def test_block_multi_string_modify_prefill(pub):
    FormDef.wipe()
    BlockDef.wipe()

    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.StringField(
            id='123',
            required='required',
            label='Test',
            prefill={'type': 'string', 'value': '{{ form_var_foo }} World'},
        ),
    ]
    block.store()

    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.PageField(id='0', label='1st page'),
        fields.StringField(id='1', label='string', varname='foo'),
        fields.PageField(id='2', label='2nd page'),
        fields.BlockField(id='3', label='test', block_slug='foobar', max_items='5'),
    ]
    formdef.store()
    formdef.data_class().wipe()

    app = get_app(pub)

    formdef.data_class().wipe()
    resp = app.get(formdef.get_url())
    resp.form['f1'] = 'Hello'
    resp = resp.form.submit('submit')  # -> 2nd page
    assert not resp.pyquery('#form_error_f3')  # not marked as error
    assert resp.form['f3$element0$f123'].value == 'Hello World'
    resp = resp.form.submit('f3$add_element')  # add second row
    assert resp.form['f3$element1$f123'].value == 'Hello World'
    resp.form['f3$element1$f123'].value = 'Something else'
    resp = resp.form.submit('f3$add_element')  # add third row
    assert resp.form['f3$element0$f123'].value == 'Hello World'
    assert resp.form['f3$element1$f123'].value == 'Something else'  # unchanged
    assert resp.form['f3$element2$f123'].value == 'Hello World'
    resp = resp.form.submit('previous')  # -> 1st page
    resp.form['f1'] = 'Bye'
    resp = resp.form.submit('submit')  # -> 2nd page
    assert resp.form['f3$element0$f123'].value == 'Bye World'  # updated
    assert resp.form['f3$element1$f123'].value == 'Something else'  # unchanged
    assert resp.form['f3$element2$f123'].value == 'Bye World'  # updated


def test_block_string_prefill_and_items(pub):
    FormDef.wipe()
    BlockDef.wipe()

    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.StringField(
            id='123',
            required='required',
            label='Test',
            prefill={'type': 'string', 'value': '{{ form_var_foo }} World'},
        ),
        fields.ItemsField(
            id='234',
            required='optional',
            label='Items',
            items=['Pomme', 'Poire', 'Pêche', 'Abricot'],
        ),
    ]
    block.store()

    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.PageField(id='0', label='1st page'),
        fields.StringField(id='1', label='string', varname='foo'),
        fields.PageField(id='2', label='2nd page'),
        fields.BlockField(id='3', label='test', block_slug='foobar', max_items='5'),
    ]
    formdef.store()
    formdef.data_class().wipe()

    app = get_app(pub)
    resp = app.get(formdef.get_url())
    resp.form['f1'] = 'Hello'
    resp = resp.form.submit('submit')  # -> 2nd page
    assert resp.form['f3$element0$f123'].value == 'Hello World'
    resp = resp.form.submit('f3$add_element')  # add second row
    assert resp.form['f3$element1$f123'].value == 'Hello World'
    resp = resp.form.submit('f3$add_element')  # add third row
    assert resp.form['f3$element2$f123'].value == 'Hello World'
    resp = resp.form.submit('submit')  # -> validation page
    resp = resp.form.submit('submit')  # -> end page
    resp = resp.follow()

    formdata = formdef.data_class().select()[0]
    assert formdata.data['3']['data'][0]['123'] == 'Hello World'
    assert formdata.data['3']['data'][1]['123'] == 'Hello World'
    assert formdata.data['3']['data'][2]['123'] == 'Hello World'


def test_workflow_form_block_prefill(pub):
    FormDef.wipe()
    BlockDef.wipe()
    create_user(pub)

    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.StringField(
            id='123', required='required', label='Test', prefill={'type': 'user', 'value': 'email'}
        ),
    ]
    block.store()

    wf = Workflow(name='status')
    st1 = wf.add_status('Status1', 'st1')

    display_form = st1.add_action('form', id='_x')
    display_form.by = ['_submitter']
    display_form.varname = 'xxx'
    display_form.formdef = WorkflowFormFieldsFormDef(item=display_form)
    display_form.formdef.fields.append(fields.BlockField(id='3', label='test', block_slug='foobar'))

    wf.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = []
    formdef.workflow_id = wf.id
    formdef.store()
    formdef.data_class().wipe()

    resp = login(get_app(pub), username='foo', password='foo').get('/test/')
    resp = resp.form.submit('submit')  # -> validation
    resp = resp.form.submit('submit').follow()
    assert 'The form has been recorded' in resp
    assert resp.form[f'fxxx_{display_form.id}_3$element0$f123'].value == 'foo@localhost'


def test_block_title_and_comment(pub):
    FormDef.wipe()
    BlockDef.wipe()

    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.StringField(id='123', required='required', label='Test'),
        fields.TitleField(id='234', label='Blah'),
        fields.CommentField(id='345', label='Blah'),
    ]
    block.store()

    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.BlockField(id='1', label='test', block_slug='foobar'),
    ]
    formdef.store()

    app = get_app(pub)
    resp = app.get(formdef.get_url())
    resp.form['f1$element0$f123'] = 'foo'
    resp = resp.form.submit('submit')  # -> validation page
    assert 'Check values then click submit.' in resp.text
    resp = resp.form.submit('submit')  # -> submit
    resp = resp.follow()


def test_block_label(pub):
    FormDef.wipe()
    BlockDef.wipe()

    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.StringField(id='123', required='required', label='Test'),
    ]
    block.store()

    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.BlockField(
            id='1',
            label='Block Label',
            block_slug='foobar',
            hint='',
            required='optional',
            label_display='normal',
        ),
    ]
    formdef.store()
    app = get_app(pub)
    resp = app.get(formdef.get_url())
    assert resp.pyquery.find('div.title label.field--label#form_label_f1').text() == 'Block Label'

    formdef.fields[0].label_display = 'subtitle'
    formdef.fields[0].hint = 'foo bar !'
    formdef.fields[0].required = 'required'
    formdef.store()
    resp = app.get(formdef.get_url())
    assert resp.pyquery.find('h4').text() == 'Block Label*'
    resp.form['f1$element0$f123'] = 'something'
    resp = resp.form.submit('submit')  # -> validation page
    assert 'Check values then click submit.' in resp.text
    assert resp.pyquery.find('h4').text() == 'Block Label'
    resp = resp.form.submit('submit').follow()  # -> submit page
    assert resp.pyquery.find('h4').text() == 'Block Label'

    resp = app.get(formdef.get_url())
    formdef.fields[0].label_display = 'hidden'
    formdef.store()
    resp = app.get(formdef.get_url())
    assert 'Block Label' not in resp.text
    resp.form['f1$element0$f123'] = 'something'
    resp = resp.form.submit('submit')  # -> validation page
    assert 'Check values then click submit.' in resp.text
    assert 'Block Label' not in resp.text
    resp = resp.form.submit('submit').follow()  # -> submit page
    assert 'Block Label' not in resp.text


def test_block_multipage(pub):
    FormDef.wipe()
    BlockDef.wipe()

    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.StringField(id='123', required='required', label='Test'),
        fields.StringField(id='234', required='required', label='Test2'),
    ]
    block.store()

    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.PageField(id='0', label='1st page'),
        fields.BlockField(id='1', label='test', block_slug='foobar'),
        fields.PageField(id='2', label='2nd page'),
    ]
    formdef.store()

    app = get_app(pub)
    resp = app.get(formdef.get_url())
    resp.form['f1$element0$f123'] = 'foo'
    resp.form['f1$element0$f234'] = 'bar'
    resp = resp.form.submit('submit')  # -> 2nd page
    resp = resp.form.submit('submit')  # -> validation page
    assert resp.form['f1$element0$f123'].attrs['readonly']
    assert resp.form['f1$element0$f123'].value == 'foo'
    resp = resp.form.submit('previous')  # -> 2nd page
    resp = resp.form.submit('previous')  # -> 1st page
    assert 'readonly' not in resp.form['f1$element0$f123'].attrs
    assert resp.form['f1$element0$f123'].value == 'foo'
    resp = resp.form.submit('submit')  # -> 2nd page
    resp = resp.form.submit('submit')  # -> validation page
    resp = resp.form.submit('submit')  # -> submit
    resp = resp.follow()
    assert '>foo<' in resp
    assert '>bar<' in resp


def test_block_repeated(pub):
    FormDef.wipe()
    BlockDef.wipe()

    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.StringField(id='123', required='required', label='Test'),
        fields.StringField(id='234', required='required', label='Test2'),
    ]
    block.store()

    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.PageField(id='0', label='1st page'),
        fields.BlockField(id='1', label='test', block_slug='foobar', max_items='3', hint='hintblock'),
        fields.PageField(id='2', label='2nd page'),
    ]
    formdef.store()

    app = get_app(pub)
    resp = app.get(formdef.get_url())
    assert resp.text.count('>Test<') == 1
    assert resp.text.count('>hintblock<') == 1
    assert 'wcs-block-add-clicked' not in resp
    assert 'Add another' in resp
    assert resp.html.find('div', {'class': 'list-add'})
    resp = resp.form.submit('f1$add_element')
    assert resp.text.count('>Test<') == 2
    assert resp.text.count('>hintblock<') == 1
    assert 'wcs-block-add-clicked' in resp
    resp = resp.form.submit('f1$add_element')
    assert resp.text.count('>Test<') == 3
    assert resp.text.count('>hintblock<') == 1
    assert resp.pyquery('.list-add').attr['style'] == 'display: none'

    # fill items (1st and 3rd row)
    resp.form['f1$element0$f123'] = 'foo'
    resp.form['f1$element0$f234'] = 'bar'
    resp.form['f1$element2$f123'] = 'foo2'
    resp.form['f1$element2$f234'] = 'bar2'

    resp = resp.form.submit('submit')  # -> 2nd page
    resp = resp.form.submit('submit')  # -> validation page
    assert 'Check values then click submit.' in resp.text
    assert resp.form['f1$element0$f123'].value == 'foo'
    assert resp.form['f1$element0$f234'].value == 'bar'
    assert resp.form['f1$element1$f123'].value == 'foo2'
    assert resp.form['f1$element1$f234'].value == 'bar2'

    resp = resp.form.submit('previous')  # -> 2nd page
    resp = resp.form.submit('previous')  # -> 1st page
    assert 'wcs-block-add-clicked' not in resp
    assert 'readonly' not in resp.form['f1$element0$f123'].attrs
    assert resp.form['f1$element0$f123'].value == 'foo'

    resp = resp.form.submit('submit')  # -> 2nd page
    resp = resp.form.submit('submit')  # -> validation page
    resp = resp.form.submit('submit')  # -> submit
    resp = resp.follow()
    assert '>foo<' in resp
    assert '>bar<' in resp
    assert '>foo2<' in resp
    assert '>bar2<' in resp


def test_block_repeated_with_default(pub):
    FormDef.wipe()
    BlockDef.wipe()

    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.StringField(id='123', required='required', label='Test'),
        fields.StringField(id='234', required='required', label='Test2'),
    ]
    block.store()

    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.PageField(id='0', label='1st page'),
        fields.BlockField(
            id='1',
            label='test',
            block_slug='foobar',
            default_items_count='2',
            max_items='3',
            hint='hintblock',
        ),
        fields.PageField(id='2', label='2nd page'),
    ]
    formdef.store()

    app = get_app(pub)
    resp = app.get(formdef.get_url())
    assert resp.text.count('>Test<') == 2
    assert resp.text.count('>hintblock<') == 1
    assert 'wcs-block-add-clicked' not in resp
    assert 'Add another' in resp
    assert resp.html.find('div', {'class': 'list-add'})
    resp = resp.form.submit('f1$add_element')
    assert resp.text.count('>Test<') == 3
    assert resp.text.count('>hintblock<') == 1
    assert resp.pyquery('.list-add').attr['style'] == 'display: none'

    formdef.fields[1].default_items_count = '3'
    formdef.store()

    app = get_app(pub)
    resp = app.get(formdef.get_url())
    assert resp.text.count('>Test<') == 3
    assert resp.text.count('>hintblock<') == 1
    assert resp.pyquery('.list-add').attr['style'] == 'display: none'

    formdef.fields[1].default_items_count = '4'
    formdef.store()
    app = get_app(pub)
    resp = app.get(formdef.get_url())
    assert resp.text.count('>Test<') == 3


def test_block_repeated_over_limit(pub):
    FormDef.wipe()
    BlockDef.wipe()

    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.StringField(id='123', required='required', label='Test'),
        fields.StringField(id='234', required='required', label='Test2'),
    ]
    block.store()

    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.PageField(id='0', label='1st page'),
        fields.BlockField(id='1', label='test', block_slug='foobar', max_items='3'),
        fields.PageField(id='2', label='2nd page'),
    ]
    formdef.store()

    app = get_app(pub)
    resp = app.get(formdef.get_url())
    assert resp.text.count('>Test<') == 1
    assert 'Add another' in resp
    resp = resp.form.submit('f1$add_element')
    assert resp.text.count('>Test<') == 2
    resp = resp.form.submit('f1$add_element')
    assert resp.text.count('>Test<') == 3
    assert resp.pyquery('.list-add').attr['style'] == 'display: none'

    # fill items
    resp.form['f1$element0$f123'] = 'foo'
    resp.form['f1$element0$f234'] = 'bar'
    resp.form['f1$element1$f123'] = 'foo1'
    resp.form['f1$element1$f234'] = 'bar1'
    resp.form['f1$element2$f123'] = 'foo2'
    resp.form['f1$element2$f234'] = 'bar2'

    # (modify formdef to only allow 2)
    formdef.fields[1].max_items = '2'
    formdef.store()

    # submit form
    resp = resp.form.submit('submit')
    assert 'Too many elements (maximum: 2)' in resp


def test_block_repeated_under_default(pub):
    FormDef.wipe()
    BlockDef.wipe()

    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.StringField(id='123', required='required', label='Test'),
        fields.StringField(id='234', required='required', label='Test2'),
    ]
    block.store()

    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.PageField(id='0', label='1st page'),
        fields.BlockField(
            id='1',
            label='test',
            block_slug='foobar',
            default_items_count='2',
            max_items='2',
            remove_button=True,
        ),
        fields.PageField(id='2', label='2nd page'),
        fields.BlockField(
            id='3',
            label='test',
            block_slug='foobar',
            default_items_count='2',
            max_items='2',
            remove_button=True,
        ),
    ]
    formdef.store()
    formdef.data_class().wipe()

    app = get_app(pub)
    resp = app.get(formdef.get_url())
    assert resp.text.count('>Test<') == 2

    # fill items
    resp.form['f1$element0$f123'] = 'foo1'
    resp.form['f1$element0$f234'] = 'bar1'
    resp.form['f1$element1$f123'] = 'foo2'
    resp.form['f1$element1$f234'] = 'bar2'

    resp = resp.form.submit('submit')  # -> 2nd page
    resp.form['f3$element0$f123'] = 'fooo1'
    resp.form['f3$element0$f234'] = 'barr1'
    resp.form['f3$element1$f123'] = 'fooo2'
    resp.form['f3$element1$f234'] = 'barr2'
    resp = resp.form.submit('submit')  # -> validation page
    assert 'Check values then click submit.' in resp.text
    assert resp.form['f1$element0$f123'].value == 'foo1'
    assert resp.form['f1$element0$f234'].value == 'bar1'
    assert resp.form['f1$element1$f123'].value == 'foo2'
    assert resp.form['f1$element1$f234'].value == 'bar2'
    assert resp.form['f3$element0$f123'].value == 'fooo1'
    assert resp.form['f3$element0$f234'].value == 'barr1'
    assert resp.form['f3$element1$f123'].value == 'fooo2'
    assert resp.form['f3$element1$f234'].value == 'barr2'

    resp = resp.form.submit('previous')  # -> 2nd page
    # simulate javascript removing of block elements from DOM
    resp.form.field_order.remove(('f3$element0$f123', resp.form.fields['f3$element0$f123'][0]))
    del resp.form.fields['f3$element0$f123']
    resp.form.field_order.remove(('f3$element0$f234', resp.form.fields['f3$element0$f234'][0]))
    del resp.form.fields['f3$element0$f234']
    resp = resp.form.submit('previous')  # -> 1st page
    # simulate javascript removing of block elements from DOM
    resp.form.field_order.remove(('f1$element0$f123', resp.form.fields['f1$element0$f123'][0]))
    del resp.form.fields['f1$element0$f123']
    resp.form.field_order.remove(('f1$element0$f234', resp.form.fields['f1$element0$f234'][0]))
    del resp.form.fields['f1$element0$f234']

    resp = resp.form.submit('submit')  # -> 2nd page
    assert resp.form['f3$element0$f123'].value == 'fooo2'
    assert resp.form['f3$element0$f234'].value == 'barr2'
    assert 'f3$element1$f123' in resp.form.fields
    assert 'f3$element1$f234' in resp.form.fields
    resp = resp.form.submit('previous')  # -> 1st page
    assert resp.form['f1$element0$f123'].value == 'foo2'
    assert resp.form['f1$element0$f234'].value == 'bar2'
    assert 'f1$element1$f123' in resp.form.fields
    assert 'f1$element1$f234' in resp.form.fields

    resp = resp.form.submit('submit')  # -> 2nd page
    resp = resp.form.submit('submit')  # -> validation page
    assert resp.form['f1$element0$f123'].value == 'foo2'
    assert resp.form['f1$element0$f234'].value == 'bar2'
    assert 'f1$element1$f123' not in resp.form.fields
    assert 'f1$element1$f234' not in resp.form.fields
    assert resp.form['f3$element0$f123'].value == 'fooo2'
    assert resp.form['f3$element0$f234'].value == 'barr2'
    assert 'f3$element1$f123' not in resp.form.fields
    assert 'f3$element1$f234' not in resp.form.fields

    resp = resp.form.submit('submit')  # -> submit
    assert len(formdef.data_class().select()[0].data['1']['data']) == 1
    assert len(formdef.data_class().select()[0].data['3']['data']) == 1


def test_block_repeated_files(pub):
    FormDef.wipe()
    BlockDef.wipe()

    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.StringField(id='123', required='required', label='Test'),
        fields.FileField(id='234', required='required', label='Test2', varname='test2'),
    ]
    block.store()

    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.PageField(id='0', label='1st page'),
        fields.BlockField(id='1', label='test', block_slug='foobar', max_items='3', varname='block'),
        fields.PageField(id='2', label='2nd page'),
    ]
    formdef.store()
    formdef.data_class().wipe()

    app = get_app(pub)
    resp = app.get(formdef.get_url())
    assert resp.text.count('>Test<') == 1
    assert 'Add another' in resp
    resp = resp.form.submit('f1$add_element')
    assert resp.text.count('>Test<') == 2
    resp = resp.form.submit('f1$add_element')
    assert resp.text.count('>Test<') == 3
    assert resp.pyquery('.list-add').attr['style'] == 'display: none'

    # fill items (1st and 3rd row)
    resp.form['f1$element0$f123'] = 'foo'
    resp.form['f1$element0$f234$file'] = Upload('test1.txt', b'foobar1', 'text/plain')
    resp.form['f1$element2$f123'] = 'foo2'
    resp.form['f1$element2$f234$file'] = Upload('test2.txt', b'foobar2', 'text/plain')

    resp = resp.form.submit('submit')  # -> 2nd page
    resp = resp.form.submit('submit')  # -> validation page
    assert 'Check values then click submit.' in resp.text
    assert resp.form['f1$element0$f123'].value == 'foo'
    assert 'test1.txt' in resp
    assert resp.form['f1$element1$f123'].value == 'foo2'
    assert 'test2.txt' in resp

    resp = resp.form.submit('previous')  # -> 2nd page
    resp = resp.form.submit('previous')  # -> 1st page
    resp = resp.form.submit('submit')  # -> 2nd page
    resp = resp.form.submit('submit')  # -> validation page
    resp = resp.form.submit('submit')  # -> submit
    resp = resp.follow()
    assert '>foo<' in resp
    assert 'test1.txt' in resp
    assert '>foo2<' in resp
    assert 'test2.txt' in resp

    # check they appear in API
    create_user_and_admin(pub)
    app = login(get_app(pub), username='admin', password='admin')
    resp = app.get(formdef.get_api_url() + 'list?full=on')
    assert app.get(resp.json[0]['fields']['block_raw'][0]['test2']['url']).follow().body == b'foobar1'
    assert app.get(resp.json[0]['fields']['block_raw'][1]['test2']['url']).follow().body == b'foobar2'


@pytest.mark.parametrize('removed_line', [0, 1, 2])
def test_block_repeated_remove_line(pub, removed_line):
    FormDef.wipe()
    BlockDef.wipe()

    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.StringField(id='123', required='required', label='Test'),
        fields.StringField(id='234', required='required', label='Test2'),
    ]
    block.store()

    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.PageField(id='0', label='1st page'),
        fields.BlockField(
            id='1', label='test', block_slug='foobar', max_items='5', hint='hintblock', remove_button=True
        ),
        fields.PageField(id='2', label='2nd page'),
    ]
    formdef.store()
    formdef.data_class().wipe()

    app = get_app(pub)
    resp = app.get(formdef.get_url())
    assert resp.text.count('>Test<') == 1
    resp = resp.form.submit('f1$add_element')
    assert resp.text.count('>Test<') == 2
    resp = resp.form.submit('f1$add_element')
    assert resp.text.count('>Test<') == 3

    # fill items on three rows
    resp.form['f1$element0$f123'] = 'foo1'
    resp.form['f1$element0$f234'] = 'bar1'
    resp.form['f1$element1$f123'] = 'foo2'
    resp.form['f1$element1$f234'] = 'bar2'
    resp.form['f1$element2$f123'] = 'foo3'
    resp.form['f1$element2$f234'] = 'bar3'

    resp = resp.form.submit('submit')  # -> 2nd page
    resp = resp.form.submit('submit')  # -> validation page
    assert 'Check values then click submit.' in resp.text
    assert resp.form['f1$element0$f123'].value == 'foo1'
    assert resp.form['f1$element0$f234'].value == 'bar1'
    assert resp.form['f1$element1$f123'].value == 'foo2'
    assert resp.form['f1$element1$f234'].value == 'bar2'
    assert resp.form['f1$element2$f123'].value == 'foo3'
    assert resp.form['f1$element2$f234'].value == 'bar3'

    resp = resp.form.submit('previous')  # -> 2nd page
    resp = resp.form.submit('previous')  # -> 1st page
    # simulate javascript removing of block elements from DOM
    resp.form.field_order.remove(
        ('f1$element%s$f123' % removed_line, resp.form.fields['f1$element%s$f123' % removed_line][0])
    )
    del resp.form.fields['f1$element%s$f123' % removed_line]
    resp.form.field_order.remove(
        ('f1$element%s$f234' % removed_line, resp.form.fields['f1$element%s$f234' % removed_line][0])
    )
    del resp.form.fields['f1$element%s$f234' % removed_line]

    resp = resp.form.submit('submit')  # -> 2nd page
    resp = resp.form.submit('submit')  # -> validation page
    values = ['1', '2', '3']
    del values[removed_line]
    assert resp.form['f1$element0$f123'].value == 'foo%s' % values[0]
    assert resp.form['f1$element0$f234'].value == 'bar%s' % values[0]
    assert resp.form['f1$element1$f123'].value == 'foo%s' % values[1]
    assert resp.form['f1$element1$f234'].value == 'bar%s' % values[1]
    assert 'f1$element2$f123' not in resp.form.fields
    assert 'f1$element2$f234' not in resp.form.fields

    resp = resp.form.submit('submit')  # -> submit
    assert len(formdef.data_class().select()[0].data['1']['data']) == 2


def test_block_items_count_as_templates(pub):
    FormDef.wipe()
    BlockDef.wipe()

    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.StringField(id='123', required='required', label='Test'),
    ]
    block.store()

    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.PageField(id='0', label='1st page'),
        fields.StringField(id='1', label='default', varname='default_items', required=False),
        fields.StringField(id='2', label='max', varname='max_items', required=False),
        fields.PageField(id='3', label='2nd page'),
        fields.BlockField(
            id='4',
            label='test',
            block_slug='foobar',
            default_items_count='{{ form_var_default_items }}',
            max_items='{{ form_var_max_items }}',
        ),
    ]
    formdef.store()

    app = get_app(pub)
    resp = app.get(formdef.get_url())
    resp = resp.form.submit('submit')  # -> page 2
    assert resp.text.count('>Test<') == 1
    assert 'Add another' in resp
    resp = resp.form.submit('previous')  # -> page 1
    resp.form['f1'] = '2'
    resp.form['f2'] = '4'
    resp = resp.form.submit('submit')  # -> page 2

    assert resp.text.count('>Test<') == 2
    resp = resp.form.submit('f4$add_element')
    assert resp.text.count('>Test<') == 3
    resp = resp.form.submit('f4$add_element')
    assert resp.text.count('>Test<') == 4
    assert resp.pyquery('.list-add').attr['style'] == 'display: none'

    # fill items
    resp.form['f4$element0$f123'] = 'foo'
    resp.form['f4$element1$f123'] = 'foo1'
    resp.form['f4$element2$f123'] = 'foo2'

    resp = resp.form.submit('submit')  # -> validation
    assert resp.pyquery('.wcs-step.current .wcs-step--label-text').text() == 'Validating'
    resp = resp.form.submit('submit').follow()  # -> submit
    assert formdef.data_class().select()[0].data == {
        '1': '2',
        '2': '4',
        '4': {'data': [{'123': 'foo'}, {'123': 'foo1'}, {'123': 'foo2'}], 'schema': {'123': 'string'}},
        '4_display': 'foobar, foobar, foobar',
    }


@pytest.mark.parametrize('block_name', ['foobar', 'Foo bar'])
def test_block_digest(pub, block_name):
    FormDef.wipe()
    BlockDef.wipe()

    block = BlockDef()
    block.name = block_name
    block.fields = [
        fields.StringField(id='123', required='required', label='Test', varname='foo'),
        fields.StringField(id='234', required='required', label='Test2', varname='bar'),
    ]
    block.store()

    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.BlockField(id='1', label='test', block_slug='%s' % block.slug, max_items='3'),
    ]
    formdef.store()
    formdef.data_class().wipe()

    app = get_app(pub)
    resp = app.get(formdef.get_url())
    resp.form['f1$element0$f123'] = 'foo'
    resp.form['f1$element0$f234'] = 'bar'
    resp = resp.form.submit('f1$add_element')
    resp.form['f1$element1$f123'] = 'foo2'
    resp.form['f1$element1$f234'] = 'bar2'

    resp = resp.form.submit('submit')  # -> validation page
    resp = resp.form.submit('submit')  # -> submit

    assert formdef.data_class().select()[0].data['1']['data'] == [
        {'123': 'foo', '234': 'bar'},
        {'123': 'foo2', '234': 'bar2'},
    ]
    # by default it gets the type of object
    assert formdef.data_class().select()[0].data['1_display'] == '%s, %s' % (block.name, block.name)

    # set a digest template
    formdef.data_class().wipe()

    # legacy, <slug>_var_
    block.digest_template = 'X{{%s_var_foo}}Y' % block.slug.replace('-', '_')
    block.store()

    resp = app.get(formdef.get_url())
    resp.form['f1$element0$f123'] = 'foo'
    resp.form['f1$element0$f234'] = 'bar'
    resp = resp.form.submit('f1$add_element')
    resp.form['f1$element1$f123'] = 'foo2'
    resp.form['f1$element1$f234'] = 'bar2'

    resp = resp.form.submit('submit')  # -> validation page
    resp = resp.form.submit('submit')  # -> submit
    assert formdef.data_class().select()[0].data['1']['data'] == [
        {'123': 'foo', '234': 'bar'},
        {'123': 'foo2', '234': 'bar2'},
    ]
    assert formdef.data_class().select()[0].data['1_display'] == 'XfooY, Xfoo2Y'

    # non-legacy, block_var_
    formdef.data_class().wipe()
    block.digest_template = 'X{{block_var_foo}}Y'
    block.store()

    resp = app.get(formdef.get_url())
    resp.form['f1$element0$f123'] = 'foo'
    resp.form['f1$element0$f234'] = 'bar'
    resp = resp.form.submit('f1$add_element')
    resp.form['f1$element1$f123'] = 'foo2'
    resp.form['f1$element1$f234'] = 'bar2'

    resp = resp.form.submit('submit')  # -> validation page
    resp = resp.form.submit('submit')  # -> submit
    assert formdef.data_class().select()[0].data['1']['data'] == [
        {'123': 'foo', '234': 'bar'},
        {'123': 'foo2', '234': 'bar2'},
    ]
    assert formdef.data_class().select()[0].data['1_display'] == 'XfooY, Xfoo2Y'


def test_block_variable_no_raw_access(pub):
    FormDef.wipe()
    BlockDef.wipe()

    block = BlockDef()
    block.name = 'xxx'
    block.fields = [
        fields.StringField(id='123', required='required', label='Test', varname='foo'),
        fields.StringField(id='234', required='required', label='Test2', varname='bar'),
    ]
    block.store()

    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.PageField(id='1', label='page1'),
        fields.BlockField(id='2', label='test', varname='block', block_slug=block.slug, max_items='3'),
        fields.PageField(id='3', label='page2'),
        fields.CommentField(id='4', label='X{{ form_var_block_raw }}Y'),
    ]
    formdef.store()
    formdef.data_class().wipe()

    app = get_app(pub)
    resp = app.get(formdef.get_url())
    resp.form['f2$element0$f123'] = 'foo'
    resp.form['f2$element0$f234'] = 'bar'
    resp = resp.form.submit('submit')  # -> page 2
    assert resp.pyquery('.comment-field').text() == 'XY'


def test_block_empty_digest(pub):
    FormDef.wipe()
    BlockDef.wipe()

    block = BlockDef()
    block.name = 'foobar'
    block.fields = [fields.StringField(id='123', required='required', label='Test')]
    block.digest_template = '{{ "" }}'
    block.store()

    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.BlockField(id='1', label='test', block_slug='foobar'),
    ]
    formdef.store()

    app = get_app(pub)
    resp = app.get(formdef.get_url())
    resp.form['f1$element0$f123'] = 'foo'
    resp = resp.form.submit('submit')  # -> validation page
    resp = resp.form.submit('submit')  # -> end page
    resp = resp.follow()
    assert '>foo<' in resp


def test_block_digest_item(pub):
    FormDef.wipe()
    BlockDef.wipe()
    NamedDataSource.wipe()

    # add a named data source
    data_source = NamedDataSource(name='foobar')
    data_source.data_source = {
        'type': 'jsonvalue',
        'value': json.dumps([{'id': '1', 'text': 'un'}, {'id': '2', 'text': 'deux'}]),
    }
    data_source.store()

    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.StringField(id='123', required='required', label='Test', varname='foo'),
        fields.ItemField(
            id='234', required='required', label='Test2', varname='bar', data_source={'type': 'foobar'}
        ),
    ]
    block.store()

    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.BlockField(id='1', label='test', block_slug='foobar', max_items='3'),
    ]
    formdef.store()
    formdef.data_class().wipe()

    app = get_app(pub)
    resp = app.get(formdef.get_url())
    resp.form['f1$element0$f123'] = 'foo'
    resp.form['f1$element0$f234'] = '1'
    resp = resp.form.submit('f1$add_element')
    resp.form['f1$element1$f123'] = 'foo2'
    resp.form['f1$element1$f234'] = '2'

    resp = resp.form.submit('submit')  # -> validation page
    resp = resp.form.submit('submit')  # -> submit

    assert formdef.data_class().select()[0].data['1']['data'] == [
        {'123': 'foo', '234': '1', '234_display': 'un', '234_structured': None},
        {'123': 'foo2', '234': '2', '234_display': 'deux', '234_structured': None},
    ]
    # by default it gets the type of object
    assert formdef.data_class().select()[0].data['1_display'] == 'foobar, foobar'

    # set a digest template
    formdef.data_class().wipe()

    block.digest_template = 'X{{block_var_bar}}Y'
    block.store()

    resp = app.get(formdef.get_url())
    resp.form['f1$element0$f123'] = 'foo'
    resp.form['f1$element0$f234'] = '1'
    resp = resp.form.submit('f1$add_element')
    resp.form['f1$element1$f123'] = 'foo2'
    resp.form['f1$element1$f234'] = '2'

    resp = resp.form.submit('submit')  # -> validation page
    resp = resp.form.submit('submit')  # -> submit
    assert formdef.data_class().select()[0].data['1']['data'] == [
        {'123': 'foo', '234': '1', '234_display': 'un', '234_structured': None},
        {'123': 'foo2', '234': '2', '234_display': 'deux', '234_structured': None},
    ]
    assert formdef.data_class().select()[0].data['1_display'] == 'XunY, XdeuxY'


def test_block_post_condition_on_2nd_page(pub):
    FormDef.wipe()
    BlockDef.wipe()

    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.StringField(id='123', required='required', label='Test', varname='foo'),
    ]
    block.store()

    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.PageField(id='0', label='1st page'),
        fields.PageField(
            id='1',
            label='2nd page',
            post_conditions=[
                {
                    'condition': {'type': 'django', 'value': 'form_var_blockfoo|getlist:"foo"|sum == 5'},
                    'error_message': 'You shall not pass.',
                }
            ],
        ),
        fields.BlockField(id='2', label='test', block_slug='foobar', max_items='3', varname='blockfoo'),
    ]
    formdef.store()

    app = get_app(pub)
    resp = app.get(formdef.get_url())
    resp = resp.form.submit('submit')  # -> second page
    resp.form['f2$element0$f123'] = 2
    resp = resp.form.submit('f2$add_element')
    resp.form['f2$element1$f123'] = 3
    resp = resp.form.submit('submit')  # -> validation page
    assert 'You shall not pass.' not in resp.text
    resp = resp.form.submit('submit')  # -> submit
    resp = resp.follow()


@responses.activate
def test_block_with_dynamic_item_field(pub):
    responses.get('http://whatever/data-source?q=foo', json={'data': [{'id': '1', 'text': 'foo'}]})
    responses.get('http://whatever/data-source?q=bar', json={'data': [{'id': '2', 'text': 'bar'}]})

    FormDef.wipe()
    BlockDef.wipe()

    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.StringField(id='123', required='required', label='Test', varname='foo'),
    ]
    block.store()

    formdef = FormDef()
    formdef.name = 'foo'
    formdef.fields = [
        fields.StringField(id='1', label='field 1', varname='foo'),
        fields.ItemField(
            id='2',
            label='field 2',
            varname='bar',
            data_source={
                'type': 'json',
                'value': 'http://whatever/data-source?q={{form_var_foo|default:""}}',
            },
        ),
        fields.BlockField(id='3', label='block', block_slug='foobar', max_items='3'),
    ]
    formdef.store()

    app = get_app(pub)

    resp = app.get(formdef.get_url())
    # select first field
    resp.form['f1'] = 'foo'
    live_resp = app.post('/foo/live', params=resp.form.submit_fields() + [('modified_field_id[]', '1')])
    assert live_resp.json['result']['2']['items'] == [{'id': '1', 'text': 'foo'}]

    resp.form['f2'].options = []
    for item in live_resp.json['result']['2']['items']:
        # simulate javascript filling the <select>
        resp.form['f2'].options.append((item['id'], False, item['text']))

    # select second field
    resp.form['f2'] = '1'

    # add block
    resp = resp.form.submit('f3$add_element')
    # second field value is kept
    assert resp.form['f2'].value == '1'

    resp = app.get(formdef.get_url())
    # select first field
    resp.form['f1'] = 'foo'
    live_resp = app.post('/foo/live', params=resp.form.submit_fields() + [('modified_field_id[]', '1')])
    assert live_resp.json['result']['2']['items'] == [{'id': '1', 'text': 'foo'}]

    resp.form['f2'].options = []
    for item in live_resp.json['result']['2']['items']:
        # simulate javascript filling the <select>
        resp.form['f2'].options.append((item['id'], False, item['text']))

    # select second field
    resp.form['f2'] = '1'

    # submit form with empty value in block
    resp = resp.form.submit()
    # second field value is kept
    assert resp.form['f2'].value == '1'


def test_block_used_in_later_prefill(pub):
    FormDef.wipe()
    BlockDef.wipe()

    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.StringField(id='123', required='required', label='Amount', varname='amount'),
    ]
    block.store()

    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.PageField(id='0', label='1st page'),
        fields.BlockField(
            id='1', label='test', block_slug='foobar', varname='data', max_items='3', hint='hintblock'
        ),
        fields.PageField(id='2', label='2nd page'),
        fields.StringField(
            id='3', label='sum', prefill={'type': 'string', 'value': '{{form_var_data|getlist:"amount"|sum}}'}
        ),
    ]
    formdef.store()

    app = get_app(pub)
    resp = app.get(formdef.get_url())
    resp.form['f1$element0$f123'] = '5'
    resp = resp.form.submit('f1$add_element')
    resp.form['f1$element1$f123'] = '3'
    resp = resp.form.submit('f1$add_element')
    resp.form['f1$element2$f123'] = '2'

    resp = resp.form.submit('submit')  # -> 2nd page
    assert resp.form['f3'].value == '10'

    resp = resp.form.submit('previous')  # -> 1st page
    resp.form['f1$element2$f123'] = '1'
    resp = resp.form.submit('submit')  # -> 2nd page
    assert resp.form['f3'].value == '9'


def test_block_add_and_locked_field(pub):
    FormDef.wipe()
    BlockDef.wipe()

    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.StringField(id='123', required='required', label='Amount', varname='amount'),
    ]
    block.store()

    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.BlockField(id='1', label='test', block_slug='foobar', varname='data', max_items='3'),
        fields.StringField(id='2', label='foo', prefill={'type': 'string', 'value': 'Foo', 'locked': True}),
    ]
    formdef.store()

    app = get_app(pub)
    resp = app.get(formdef.get_url())
    assert resp.form['f2'].value == 'Foo'
    assert 'readonly' in resp.form['f2'].attrs
    resp.form['f1$element0$f123'] = 'a'
    resp = resp.form.submit('f1$add_element')
    assert resp.form['f2'].value == 'Foo'
    assert 'readonly' in resp.form['f2'].attrs
    resp.form['f1$element1$f123'] = 'b'
    resp = resp.form.submit('f1$add_element')
    resp.form['f1$element2$f123'] = 'c'
    resp = resp.form.submit('submit')  # -> validation page
    resp = resp.form.submit('submit')  # -> submit


def test_block_subfields_display_locations(pub):
    FormDef.wipe()
    BlockDef.wipe()

    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.TitleField(id='234', label='Blah Title'),
        fields.SubtitleField(id='345', label='Blah Subtitle'),
        fields.CommentField(id='456', label='Blah Comment'),
        fields.StringField(id='123', required='required', label='Test'),
    ]
    block.store()

    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.BlockField(id='1', label='test', block_slug='foobar'),
    ]
    formdef.store()

    # default mode

    app = get_app(pub)
    resp = app.get(formdef.get_url())
    resp.form['f1$element0$f123'] = 'Blah Field'
    resp = resp.form.submit('submit')  # -> validation page
    assert 'Check values then click submit.' in resp.text
    assert not resp.pyquery('[style="display: none"] [data-field-id="123"]')
    assert resp.pyquery('[data-field-id="234"]')
    assert not resp.pyquery('[style="display: none"] [data-field-id="234"]')
    assert resp.pyquery('[data-field-id="345"]')
    assert not resp.pyquery('[style="display: none"] [data-field-id="345"]')

    resp = resp.form.submit('submit').follow()  # -> submitted
    assert 'Blah Field' in resp.text
    assert 'Blah Title' in resp.text
    assert 'Blah Subtitle' in resp.text
    assert 'Blah Comment' not in resp.text

    # all on validation page
    for field in block.fields:
        field.display_locations = ['validation']
    block.store()

    app = get_app(pub)
    resp = app.get(formdef.get_url())
    resp.form['f1$element0$f123'] = 'Blah Field'
    resp = resp.form.submit('submit')  # -> validation page
    assert 'Check values then click submit.' in resp.text
    assert not resp.pyquery('[style="display: none"] [data-field-id="123"]')
    assert resp.pyquery('[data-field-id="234"]')
    assert not resp.pyquery('[style="display: none"] [data-field-id="234"]')
    assert resp.pyquery('[data-field-id="345"]')
    assert not resp.pyquery('[style="display: none"] [data-field-id="345"]')
    assert resp.pyquery('[data-field-id="456"]')
    assert not resp.pyquery('[style="display: none"] [data-field-id="456"]')

    # none on validation page
    for field in block.fields:
        field.display_locations = []
    block.store()

    app = get_app(pub)
    resp = app.get(formdef.get_url())
    resp.form['f1$element0$f123'] = 'Blah Field'
    resp = resp.form.submit('submit')  # -> validation page
    assert 'Check values then click submit.' in resp.text
    assert resp.pyquery('[style="display: none"] [data-field-id="123"]')
    assert resp.pyquery('[style="display: none"] [data-field-id="234"]')
    assert resp.pyquery('[style="display: none"] [data-field-id="345"]')
    assert not resp.pyquery('[data-field-id="456"]')

    # all on summary page
    for field in block.fields:
        field.display_locations = ['summary']
    block.store()

    app = get_app(pub)
    resp = app.get(formdef.get_url())
    resp.form['f1$element0$f123'] = 'Blah Field'
    resp = resp.form.submit('submit')  # -> validation page
    assert 'Check values then click submit.' in resp.text
    resp = resp.form.submit('submit').follow()  # -> submitted

    assert 'Blah Field' in resp.text
    assert 'Blah Title' in resp.text
    assert 'Blah Subtitle' in resp.text
    assert 'Blah Comment' in resp.text

    # none on summary page
    for field in block.fields:
        field.display_locations = []
    block.store()

    app = get_app(pub)
    resp = app.get(formdef.get_url())
    resp.form['f1$element0$f123'] = 'Blah Field'
    resp = resp.form.submit('submit')  # -> validation page
    assert 'Check values then click submit.' in resp.text
    resp = resp.form.submit('submit').follow()  # -> submitted

    assert 'Blah Field' not in resp.text
    assert 'Blah Title' not in resp.text
    assert 'Blah Subtitle' not in resp.text
    assert 'Blah Comment' not in resp.text


def test_block_block_counter(pub):
    FormDef.wipe()
    BlockDef.wipe()

    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.TitleField(
            id='234', label='Blah Title #{{ block_counter.index }} #{{ block_counter.index0 }}'
        ),
        fields.SubtitleField(
            id='345', label='Blah Subtitle #{{ block_counter_index }} #{{ block_counter_index0 }}'
        ),
        fields.CommentField(
            id='456', label='Blah Comment #{{ block_counter.index }} #{{ block_counter.index0 }}'
        ),
        fields.StringField(id='123', required='required', label='Test'),
    ]
    block.store()

    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.BlockField(id='1', label='test', block_slug='foobar', max_items='3'),
    ]
    formdef.store()

    app = get_app(pub)
    resp = app.get(formdef.get_url())
    resp.form['f1$element0$f123'] = 'Blah Field 1'
    assert 'Blah Title #1 #0' in resp.text
    assert 'Blah Subtitle #1 #0' in resp.text
    assert 'Blah Comment #1 #0' in resp.text
    resp = resp.form.submit('f1$add_element')
    assert 'Blah Title #1 #0' in resp.text
    assert 'Blah Subtitle #1 #0' in resp.text
    assert 'Blah Comment #1 #0' in resp.text
    resp.form['f1$element1$f123'] = 'Blah Field 2'
    assert 'Blah Title #2 #1' in resp.text
    assert 'Blah Subtitle #2 #1' in resp.text
    assert 'Blah Comment #2 #1' in resp.text
    resp = resp.form.submit('submit')  # -> validation page
    assert 'Blah Field 1' in resp.text
    assert 'Blah Title #1 #0' in resp.text
    assert 'Blah Subtitle #1 #0' in resp.text
    assert 'Blah Field 2' in resp.text
    assert 'Blah Title #2 #1' in resp.text
    assert 'Blah Subtitle #2 #1' in resp.text

    resp = resp.form.submit('submit').follow()  # -> submitted
    assert 'Blah Field 1' in resp.text
    assert 'Blah Title #1 #0' in resp.text
    assert 'Blah Subtitle #1 #0' in resp.text
    assert 'Blah Field 2' in resp.text
    assert 'Blah Title #2 #1' in resp.text
    assert 'Blah Subtitle #2 #1' in resp.text


def test_workflow_display_form_with_block_add(pub):
    user = create_user(pub)

    block = BlockDef()
    block.name = 'foobar2'
    block.fields = [
        fields.StringField(id='123', required='required', label='Test'),
    ]
    block.store()

    wf = Workflow.get_default_workflow()
    wf.id = '2'
    wf.store()
    wf = Workflow.get(wf.id)
    status = wf.get_status('new')
    display_form = status.add_action('form', id='_display_form')
    display_form.by = ['_submitter']
    display_form.varname = 'blah'
    display_form.formdef = WorkflowFormFieldsFormDef(item=display_form)
    display_form.formdef.fields = [
        fields.StringField(id='1', label='Test', varname='str', required='required'),
        fields.BlockField(id='2', label='Blocks', block_slug='foobar2', varname='data', max_items='3'),
    ]

    jump = status.add_action('jumponsubmit', id='_jump')
    jump.status = 'accepted'

    wf.store()

    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = []
    formdef.workflow_id = wf.id
    formdef.store()

    formdef.data_class().wipe()
    formdata = formdef.data_class()()
    formdata.user_id = user.id
    formdata.just_created()
    formdata.status = 'wf-new'
    formdata.store()

    resp = login(get_app(pub), username='foo', password='foo').get(formdata.get_url())
    resp.form[f'fblah_{display_form.id}_1'] = 'blah'
    resp.form[f'fblah_{display_form.id}_2$element0$f123'] = 'foo'
    resp = resp.form.submit(f'fblah_{display_form.id}_2$add_element')
    resp.form[f'fblah_{display_form.id}_2$element1$f123'] = 'bar'
    resp = resp.form.submit('submit')

    assert formdef.data_class().get(formdata.id).workflow_data == {
        'blah_var_data': 'foobar2, foobar2',
        'blah_var_data_raw': {'data': [{'123': 'foo'}, {'123': 'bar'}], 'schema': {'123': 'string'}},
        'blah_var_str': 'blah',
    }


def test_removed_block_in_form_page(pub):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.BlockField(id='0', label='test', block_slug='removed'),
    ]
    formdef.store()

    resp = get_app(pub).get(formdef.get_url(), status=500)
    assert 'A fatal error happened.' in resp.text

    assert LoggedError.count() == 1
    logged_error = LoggedError.select()[0]
    assert '(id:%s)' % logged_error.id in resp.text
    resp = get_app(pub).get(formdef.get_url(), status=500)
    assert '(id:%s)' % logged_error.id in resp.text
    logged_error = LoggedError.select()[0]
    assert logged_error.occurences_count == 2


def test_block_with_static_condition(pub):
    BlockDef.wipe()
    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.StringField(id='123', required='required', label='One', varname='one'),
        fields.StringField(
            id='234',
            required='required',
            label='Two',
            condition={'type': 'django', 'value': 'False'},
        ),
        fields.StringField(
            id='345',
            required='required',
            label='Three',
            condition={'type': 'django', 'value': 'True'},
        ),
        fields.CommentField(
            id='456',
            label='comment',
            condition={'type': 'django', 'value': 'False'},
        ),
        fields.CommentField(
            id='567',
            label='comment',
            condition={'type': 'django', 'value': 'True'},
        ),
    ]
    block.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.BlockField(id='1', label='test', block_slug='foobar'),
    ]
    formdef.store()
    formdef.data_class().wipe()

    resp = get_app(pub).get(formdef.get_url())
    assert 'f1$element0$f123' in resp.form.fields
    assert 'f1$element0$f234' in resp.form.fields
    assert resp.pyquery('[data-widget-name="f1$element0$f234"]').attr.style == 'display: none'
    assert 'f1$element0$f345' in resp.form.fields
    assert resp.pyquery('[data-widget-name="f1$element0$f345"]').attr.style == ''
    assert resp.pyquery('[data-field-id="456"]').attr.style == 'display: none'
    assert resp.pyquery('[data-field-id="567"]').attr.style is None

    resp.form['f1$element0$f123'] = 'foo'
    resp.form['f1$element0$f345'] = 'bar'
    resp = resp.form.submit('submit')  # -> validation page
    assert resp.form['f1$element0$f123'].attrs['readonly']
    assert resp.form['f1$element0$f123'].value == 'foo'
    assert resp.form['f1$element0$f234'].value == ''
    assert resp.pyquery('[data-widget-name="f1$element0$f234"]').attr.style == 'display: none'
    assert resp.form['f1$element0$f345'].value == 'bar'
    resp = resp.form.submit('submit')  # -> end page
    formdata = formdef.data_class().select()[0]
    assert formdata.data == {
        '1': {
            'data': [{'123': 'foo', '345': 'bar'}],
            'schema': {'123': 'string', '234': 'string', '345': 'string', '456': 'comment', '567': 'comment'},
        },
        '1_display': 'foobar',
    }


def test_block_with_block_field_condition(pub):
    BlockDef.wipe()
    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.StringField(id='123', required='required', label='One', varname='one'),
        fields.StringField(
            id='234',
            required='required',
            label='Two',
            condition={'type': 'django', 'value': 'block_var_one == "test"'},
        ),
        fields.CommentField(
            id='345',
            label='comment',
            condition={'type': 'django', 'value': 'block_var_one == "test"'},
        ),
    ]
    block.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.BlockField(id='1', label='test', block_slug='foobar'),
    ]
    formdef.store()
    formdef.data_class().wipe()

    app = get_app(pub)
    resp = app.get(formdef.get_url())
    assert resp.pyquery('[data-widget-name="f1$element0$f123"]').attr['data-live-source'] == 'true'
    assert resp.pyquery('[data-widget-name="f1$element0$f234"]').attr.style == 'display: none'
    resp.form['f1$element0$f123'] = 'foo'
    live_resp = app.post(
        formdef.get_url() + 'live',
        params=resp.form.submit_fields() + [('modified_field_id[]', '123')],
    )
    assert live_resp.json['result']['1-123-0']['visible'] is True
    assert live_resp.json['result']['1-234-0']['visible'] is False
    assert live_resp.json['result']['1-345-0']['visible'] is False
    resp.form['f1$element0$f123'] = 'test'
    live_resp = app.post(
        formdef.get_url() + 'live',
        params=resp.form.submit_fields() + [('modified_field_id[]', '123')],
    )
    assert live_resp.json['result']['1-123-0']['visible'] is True
    assert live_resp.json['result']['1-234-0']['visible'] is True
    assert live_resp.json['result']['1-345-0']['visible'] is True
    resp = resp.form.submit('submit')  # -> error as 1-234-0 is required
    assert 'There were errors processing the form' in resp

    resp.form['f1$element0$f234'] = 'test'
    resp = resp.form.submit('submit')  # validation
    assert 'There were errors processing the form' not in resp

    resp = resp.form.submit('previous')  # -> 1st page
    resp.form['f1$element0$f123'] = 'foo'
    resp.form['f1$element0$f234'] = ''
    resp = resp.form.submit('submit')  # validation
    assert 'There were errors processing the form' not in resp
    resp = resp.form.submit('submit')  # -> end page
    formdata = formdef.data_class().select()[0]
    assert formdata.data == {
        '1': {
            'data': [{'123': 'foo'}],
            'schema': {'123': 'string', '234': 'string', '345': 'comment'},
        },
        '1_display': 'foobar',
    }
    formdef.data_class().wipe()

    # check with repetition
    formdef.fields[0].max_items = '3'
    formdef.store()

    resp = app.get(formdef.get_url())
    resp = resp.form.submit('f1$add_element')
    assert 'There were errors processing the form' not in resp
    assert resp.pyquery('[data-widget-name="f1$element0$f234"]').attr.style == 'display: none'
    assert resp.pyquery('[data-widget-name="f1$element1$f234"]').attr.style == 'display: none'
    resp.form['f1$element0$f123'] = 'test'
    live_resp = app.post(
        formdef.get_url() + 'live',
        params=resp.form.submit_fields() + [('modified_field_id[]', '123')],
    )
    assert live_resp.json['result']['1-123-0']['visible'] is True
    assert live_resp.json['result']['1-234-0']['visible'] is True
    assert live_resp.json['result']['1-123-1']['visible'] is True
    assert live_resp.json['result']['1-234-1']['visible'] is False

    resp.form['f1$element0$f234'] = 'foo'
    resp.form['f1$element1$f123'] = 'xxx'

    resp = resp.form.submit('submit')  # validation
    resp = resp.form.submit('submit')  # -> end page
    formdata = formdef.data_class().select()[0]
    assert formdata.data == {
        '1': {
            'data': [{'123': 'test', '234': 'foo'}, {'123': 'xxx'}],
            'schema': {'123': 'string', '234': 'string', '345': 'comment'},
        },
        '1_display': 'foobar, foobar',
    }


def test_block_with_block_counter_condition(pub):
    BlockDef.wipe()
    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.StringField(id='123', required='required', label='One', varname='one'),
        fields.StringField(
            id='234',
            required='required',
            label='Two',
            condition={'type': 'django', 'value': 'block_counter.index == 1'},
        ),
    ]
    block.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.BlockField(id='1', label='test', block_slug='foobar', max_items='3'),
    ]
    formdef.store()
    formdef.data_class().wipe()

    app = get_app(pub)
    resp = app.get(formdef.get_url())
    assert resp.pyquery('[data-widget-name="f1$element0$f234"]').attr.style == ''
    resp = resp.form.submit('f1$add_element')
    assert resp.pyquery('[data-widget-name="f1$element1$f234"]').attr.style == 'display: none'
    resp = resp.form.submit('f1$add_element')
    assert resp.pyquery('[data-widget-name="f1$element2$f234"]').attr.style == 'display: none'

    resp.form['f1$element0$f123'] = 'foo'
    resp.form['f1$element0$f234'] = 'foo'
    resp.form['f1$element1$f123'] = 'bar'
    live_resp = app.post(
        formdef.get_url() + 'live',
        params=resp.form.submit_fields(),
    )
    assert live_resp.json['result']['1-123-0']['visible'] is True
    assert live_resp.json['result']['1-234-0']['visible'] is True
    assert live_resp.json['result']['1-123-1']['visible'] is True
    assert live_resp.json['result']['1-234-1']['visible'] is False
    assert live_resp.json['result']['1-123-2']['visible'] is True
    assert live_resp.json['result']['1-234-2']['visible'] is False
    resp = resp.form.submit('submit')  # validation
    resp = resp.form.submit('submit')  # -> end page
    formdata = formdef.data_class().select()[0]
    assert formdata.data == {
        '1': {
            'data': [{'123': 'foo', '234': 'foo'}, {'123': 'bar'}],
            'schema': {'123': 'string', '234': 'string'},
        },
        '1_display': 'foobar, foobar',
    }
    formdef.data_class().wipe()


def test_block_with_block_item_field_condition_and_prefill(pub):
    BlockDef.wipe()
    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.StringField(
            id='123',
            required='required',
            label='One',
            prefill={'type': 'string', 'value': '{{ "plop" }}'},
        ),
        fields.ItemField(
            id='234',
            required='required',
            label='Test2',
            varname='item',
            items=['Foo', 'Bar'],
            prefill={'type': 'none'},  # not a real prefill
        ),
        fields.StringField(
            id='345',
            required='required',
            label='Two',
            condition={'type': 'django', 'value': 'block_var_item == "Foo"'},
        ),
    ]
    block.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.BlockField(id='1', label='test', block_slug='foobar'),
    ]
    formdef.store()
    formdef.data_class().wipe()

    app = get_app(pub)
    resp = app.get(formdef.get_url())
    assert resp.form['f1$element0$f123'].value == 'plop'
    assert resp.form['f1$element0$f234'].value == 'Foo'
    assert resp.pyquery('[data-widget-name="f1$element0$f234"]').attr['data-live-source'] == 'true'
    assert resp.pyquery('[data-widget-name="f1$element0$f345"]').attr.style == 'display: none'
    live_resp = app.post(
        formdef.get_url() + 'live',
        params=resp.form.submit_fields() + [('modified_field_id[]', '123')],
    )
    assert live_resp.json['result']['1-123-0']['visible'] is True
    assert live_resp.json['result']['1-234-0']['visible'] is True
    assert live_resp.json['result']['1-345-0']['visible'] is True
    resp.form['f1$element0$f345'] = 'test'
    resp = resp.form.submit('submit')  # validation

    # check with real prefill
    block.fields[1].prefill = {'type': 'string', 'value': '{{ "Bar" }}'}
    block.store()
    resp = app.get(formdef.get_url())
    assert resp.form['f1$element0$f123'].value == 'plop'
    assert resp.form['f1$element0$f234'].value == 'Bar'


def test_block_with_block_empty_row_and_condition(pub):
    BlockDef.wipe()
    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.StringField(id='123', required='required', label='One', varname='one'),
        fields.StringField(
            id='234',
            required='required',
            label='Two',
            condition={'type': 'django', 'value': 'block_var_one'},
        ),
    ]
    block.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.BlockField(id='1', label='test', block_slug='foobar', max_items='3'),
    ]
    formdef.store()
    formdef.data_class().wipe()

    app = get_app(pub)
    resp = app.get(formdef.get_url())
    resp.form['f1$element0$f123'] = 'foo'
    resp = resp.form.submit('f1$add_element')
    resp.form['f1$element1$f123'] = 'bar'
    resp = resp.form.submit('f1$add_element')
    resp.form['f1$element1$f123'] = ''
    resp.form['f1$element2$f123'] = 'baz'

    live_resp = app.post(
        formdef.get_url() + 'live',
        params=resp.form.submit_fields(),
    )
    assert live_resp.json['result']['1-234-0']['visible'] is True
    assert live_resp.json['result']['1-234-1']['visible'] is False
    assert live_resp.json['result']['1-234-2']['visible'] is True

    resp.form['f1$element0$f234'] = 'foo2'
    resp.form['f1$element2$f234'] = 'baz2'
    resp = resp.form.submit('submit')  # validation
    resp = resp.form.submit('submit')  # -> end page
    formdata = formdef.data_class().select()[0]
    assert formdata.data == {
        '1': {
            'data': [{'123': 'foo', '234': 'foo2'}, {'123': 'baz', '234': 'baz2'}],
            'schema': {'123': 'string', '234': 'string'},
        },
        '1_display': 'foobar, foobar',
    }


def test_formdata_page_with_block_bad_value(pub):
    BlockDef.wipe()
    user = create_user(pub)

    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.StringField(id='123', required='required', label='Test', varname='test'),
    ]
    block.store()

    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [
        fields.BlockField(id='1', label='test', block_slug='foobar', varname='foobar'),
    ]
    formdef.store()

    formdef.data_class().wipe()
    formdata = formdef.data_class()()
    formdata.data = {
        '1': None,
        '1_display': 'hello',
    }
    formdata.just_created()
    formdata.user_id = user.id
    formdata.store()

    app = get_app(pub)
    login(app, username='foo', password='foo')
    resp = app.get(formdata.get_url())
    assert resp.pyquery('div.field-type-block div.value').text() == ''


def test_block_prefill_full_block(pub):
    FormDef.wipe()
    BlockDef.wipe()
    CardDef.wipe()

    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.StringField(id='123', required='required', label='Test', varname='plop'),
    ]
    block.digest_template = '{{block_var_plop}}'
    block.store()

    carddef = CardDef()
    carddef.name = 'card title'
    carddef.fields = [
        fields.BlockField(id='2', label='test', block_slug='foobar', varname='foo', max_items='5'),
    ]
    carddef.store()
    carddef.data_class().wipe()

    carddata = carddef.data_class()()
    carddata.data = {
        '2': {
            'data': [{'123': 'foo'}, {'123': 'bar'}],
            'schema': {'123': 'string'},
        },
        '2_display': 'foo, bar',
    }
    carddata.just_created()
    carddata.store()

    # get block value from an existing block value
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.BlockField(
            id='1',
            label='test',
            block_slug='foobar',
            max_items='5',
            prefill={'type': 'string', 'value': '{{cards|objects:"card-title"|first|get:"form_var_foo"}}'},
        )
    ]
    formdef.store()
    formdef.data_class().wipe()

    app = get_app(pub)
    resp = app.get(formdef.get_url())
    assert resp.form['f1$element0$f123'].value == 'foo'
    assert resp.form['f1$element1$f123'].value == 'bar'
    resp = resp.form.submit('submit')  # validation
    resp = resp.form.submit('submit')  # done
    assert formdef.data_class().select()[0].data == {
        '1': {
            'data': [{'123': 'foo'}, {'123': 'bar'}],
            'digests': ['foo', 'bar'],
            'schema': {'123': 'string'},
        },
        '1_display': 'foo, bar',
    }

    # create a new block value
    formdef.data_class().wipe()
    formdef.fields[0].prefill['value'] = '{% block_value plop="toto" %}'
    formdef.store()

    resp = app.get(formdef.get_url())
    assert resp.form['f1$element0$f123'].value == 'toto'
    resp = resp.form.submit('submit')  # validation
    resp = resp.form.submit('submit')  # done
    assert formdef.data_class().select()[0].data == {
        '1': {
            'data': [{'123': 'toto'}],
            'digests': ['toto'],
            'schema': {'123': 'string'},
        },
        '1_display': 'toto',
    }

    # check the field is not included in /live
    resp = app.get(formdef.get_url())
    live_resp = app.post(
        formdef.get_url() + 'live',
        params=resp.form.submit_fields() + [('modified_field_id[]', 'init'), ('prefilled_1', 'on')],
    )
    assert 'content' not in live_resp.json['result']['1']

    # invalid value (None)
    formdef.data_class().wipe()
    formdef.fields[0].prefill['value'] = '{{ None }}'
    formdef.store()

    resp = app.get(formdef.get_url())
    assert resp.form['f1$element0$f123'].value == ''

    # invalid value (text)
    formdef.data_class().wipe()
    formdef.fields[0].prefill['value'] = 'xxx'
    formdef.store()

    resp = app.get(formdef.get_url())
    assert resp.form['f1$element0$f123'].value == ''


def test_block_prefill_full_block_multiple_rows(pub):
    FormDef.wipe()
    BlockDef.wipe()

    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.StringField(id='123', required='required', label='Test', varname='plop'),
    ]
    block.digest_template = '{{block_var_plop}}'
    block.store()

    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.BlockField(
            id='1',
            label='test',
            block_slug='foobar',
            max_items='5',
            prefill={
                'type': 'string',
                'value': '{% block_value init=True as foobar %}'
                '{% for a in "ABC" %}{% block_value plop=a append=foobar as foobar %}'
                '{% endfor %}'
                '{% block_value output=foobar %}',
            },
        )
    ]
    formdef.store()
    formdef.data_class().wipe()

    app = get_app(pub)
    resp = app.get(formdef.get_url())
    assert resp.form['f1$element0$f123'].value == 'A'
    assert resp.form['f1$element1$f123'].value == 'B'
    assert resp.form['f1$element2$f123'].value == 'C'
    resp = resp.form.submit('submit')  # validation
    resp = resp.form.submit('submit')  # done
    assert formdef.data_class().select()[0].data == {
        '1': {
            'data': [{'123': 'A'}, {'123': 'B'}, {'123': 'C'}],
            'digests': ['A', 'B', 'C'],
            'schema': {'123': 'string'},
        },
        '1_display': 'A, B, C',
    }


def test_block_empty_value_with_default_list_choice(pub):
    FormDef.wipe()
    Workflow.wipe()
    BlockDef.wipe()

    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.CommentField(id='0', label='Blah'),
        fields.StringField(id='123', required='optional', label='Test1', varname='t1'),
        fields.ItemField(
            id='345',
            required='optional',
            label='Test3',
            varname='t3',
            items=['abc', 'def', 'ghi'],
            use_hint_as_first_option=False,
        ),
    ]
    block.store()

    workflow = Workflow(name='test')
    workflow.add_status('new')
    workflow.store()

    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [
        fields.BlockField(id='1', label='Blocks', block_slug='foobar', max_items='3'),
    ]
    formdef.workflow_id = workflow.id
    formdef.store()

    formdef.data_class().wipe()
    resp = get_app(pub).get(formdef.get_url())
    resp.form['f1$element0$f123'] = 'foo'
    resp = resp.form.submit('f1$add_element')
    # second row left empty
    assert resp.form['f1$element1$f123'].value == ''
    assert resp.form['f1$element1$f345'].value == 'abc'  # (default value)
    resp = resp.form.submit('submit')  # -> validation
    resp = resp.form.submit('submit').follow()
    formdata_id = resp.request.url.strip('/').split('/')[-1]
    # check the second row is ignored, as it is empty
    assert resp.pyquery('.field-type-block .value .value').text() == 'foo abc'
    formdata = formdef.data_class().get(formdata_id)
    assert len(formdata.data['1']['data']) == 1

    # check with only item fields
    block.fields = [
        fields.CommentField(id='0', label='Blah', type='comment'),
        fields.ItemField(
            id='123',
            required='optional',
            label='Test1',
            varname='t1',
            items=['foo', 'bar', 'baz'],
            use_hint_as_first_option=False,
        ),
        fields.ItemField(
            id='345',
            required='optional',
            label='Test3',
            varname='t3',
            items=['abc', 'def', 'ghi'],
            use_hint_as_first_option=False,
        ),
    ]
    block.store()

    formdef.data_class().wipe()
    resp = get_app(pub).get(formdef.get_url())
    resp.form['f1$element0$f123'] = 'foo'
    resp = resp.form.submit('f1$add_element')
    # second row left empty
    assert resp.form['f1$element1$f123'].value == 'foo'  # (default value)
    assert resp.form['f1$element1$f345'].value == 'abc'  # (default value)
    resp = resp.form.submit('submit')  # -> validation
    resp = resp.form.submit('submit').follow()
    formdata_id = resp.request.url.strip('/').split('/')[-1]
    # check the second row is not ignored, as it's only lists
    assert resp.pyquery('.field-type-block .value .value').text() == 'foo abc foo abc'
    formdata = formdef.data_class().get(formdata_id)
    assert len(formdata.data['1']['data']) == 2


def test_block_empty_value_with_checkbox(pub):
    FormDef.wipe()
    Workflow.wipe()
    BlockDef.wipe()

    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.CommentField(id='0', label='Blah'),
        fields.StringField(id='123', required='required', label='Test1'),
        fields.BoolField(id='345', required='optional', label='Test3'),
    ]
    block.store()

    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [
        fields.BlockField(id='1', label='Blocks', block_slug='foobar', required='optional'),
    ]
    formdef.store()

    formdef.data_class().wipe()
    resp = get_app(pub).get(formdef.get_url())
    resp = resp.form.submit('submit')  # -> validation
    assert 'Check values then click submit.' in resp.text
    resp = resp.form.submit('submit').follow()
    formdata_id = resp.request.url.strip('/').split('/')[-1]
    formdata = formdef.data_class().get(formdata_id)
    assert formdata.data == {'1': None, '1_display': None}

    # check with required checkbox field
    block.fields[1].required = False
    block.fields[2].required = True
    block.store()

    formdef.data_class().wipe()
    resp = get_app(pub).get(formdef.get_url())
    resp = resp.form.submit('submit')  # -> validation
    assert resp.pyquery('.CheckboxWidget.widget-with-error')
    resp.form['f1$element0$f345'].checked = True
    resp = resp.form.submit('submit')  # -> validation
    resp = resp.form.submit('submit').follow()
    formdata_id = resp.request.url.strip('/').split('/')[-1]
    # check the second row is not ignored, as it's only lists
    formdata = formdef.data_class().get(formdata_id)
    assert formdata.data['1']['data'] == [{'123': None, '345': True}]


def test_block_prefill_full_block_date_format(pub):
    FormDef.wipe()
    BlockDef.wipe()
    create_user(pub)

    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.DateField(id='123', required='required', label='Test', varname='plop'),
    ]
    block.digest_template = '{{block_var_plop}}'
    block.store()

    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.PageField(id='0', label='1st page'),
        fields.PageField(id='2', label='2nd page'),
        fields.BlockField(
            id='1',
            label='test',
            block_slug='foobar',
            max_items='5',
            prefill={
                'type': 'string',
                'value': '{% block_value plop="2023-05-23" %}',
            },
        ),
    ]
    formdef.store()
    formdef.data_class().wipe()

    app = get_app(pub)
    login(app, username='foo', password='foo')
    resp = app.get(formdef.get_url())
    resp = resp.form.submit('submit')  # -> page 2
    assert resp.form['f1$element0$f123'].value == '2023-05-23'
    resp = resp.form.submit('submit')  # validation
    resp = resp.form.submit('submit')  # done
    assert formdef.data_class().select()[0].data == {
        '1': {
            'data': [{'123': datetime.date(2023, 5, 23).timetuple()}],
            'digests': ['2023-05-23'],
            'schema': {'123': 'date'},
        },
        '1_display': '2023-05-23',
    }


def test_block_prefill_full_block_email(pub):
    FormDef.wipe()
    BlockDef.wipe()
    create_user(pub)

    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.EmailField(id='123', required='required', label='Test', varname='plop'),
    ]
    block.digest_template = '{{block_var_plop}}'
    block.store()

    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.PageField(id='0', label='1st page'),
        fields.PageField(id='2', label='2nd page'),
        fields.BlockField(
            id='1',
            label='test',
            block_slug='foobar',
            max_items='5',
            prefill={
                'type': 'string',
                'value': '{% block_value plop="foo@example.net" %}',
            },
        ),
    ]
    formdef.store()
    formdef.data_class().wipe()

    app = get_app(pub)
    login(app, username='foo', password='foo')
    resp = app.get(formdef.get_url())
    resp = resp.form.submit('submit')  # -> page 2
    assert resp.form['f1$element0$f123'].value == 'foo@example.net'
    resp = resp.form.submit('submit')  # validation
    resp = resp.form.submit('submit')  # done
    assert formdef.data_class().select()[0].data == {
        '1': {
            'data': [{'123': 'foo@example.net'}],
            'digests': ['foo@example.net'],
            'schema': {'123': 'email'},
        },
        '1_display': 'foo@example.net',
    }


def test_block_prefill_full_block_card_item(pub):
    FormDef.wipe()
    BlockDef.wipe()
    CardDef.wipe()
    create_user(pub)

    carddef = CardDef()
    carddef.name = 'Test'
    carddef.fields = [
        fields.StringField(id='0', label='blah', varname='blah'),
    ]
    carddef.digest_templates = {'default': '{{ form_var_blah|upper }}'}
    carddef.store()
    carddef.data_class().wipe()

    carddata1 = carddef.data_class()()
    carddata1.data = {'0': 'bar'}
    carddata1.just_created()
    carddata1.store()

    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.ItemField(
            id='123',
            required='optional',
            hint='-----',
            label='Test',
            varname='plop',
            data_source={'type': 'carddef:test'},
        ),
    ]
    block.digest_template = '{{block_var_plop}}'
    block.store()

    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.PageField(id='0', label='1st page'),
        fields.PageField(id='2', label='2nd page'),
        fields.BlockField(
            id='1',
            label='test',
            block_slug='foobar',
            max_items='5',
            prefill={
                'type': 'string',
                'value': '{% block_value plop="1" %}',
            },
        ),
    ]
    formdef.store()
    formdef.data_class().wipe()

    app = get_app(pub)
    login(app, username='foo', password='foo')
    resp = app.get(formdef.get_url())
    resp = resp.form.submit('submit')  # -> page 2
    assert resp.form['f1$element0$f123'].value == '1'
    resp = resp.form.submit('submit')  # validation
    resp = resp.form.submit('submit')  # done
    assert formdef.data_class().select()[0].data == {
        '1': {
            'data': [
                {'123': '1', '123_display': 'BAR', '123_structured': {'blah': 'bar', 'id': 1, 'text': 'BAR'}}
            ],
            'digests': ['BAR'],
            'schema': {'123': 'item'},
        },
        '1_display': 'BAR',
    }

    # prefill with unknown value
    LoggedError.wipe()
    formdef.fields[2].prefill['value'] = '{% block_value plop="123" %}'
    formdef.store()
    formdef.data_class().wipe()
    resp = app.get(formdef.get_url())
    resp = resp.form.submit('submit')  # -> page 2
    assert not resp.form['f1$element0$f123'].value
    assert LoggedError.count() == 1
    assert (
        LoggedError.select()[0].summary == 'invalid value when creating block: unknown card value (\'123\')'
    )


def test_block_titles_and_empty_block_on_summary_page(pub, emails):
    FormDef.wipe()
    BlockDef.wipe()
    create_user(pub)

    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.StringField(id='123', label='Test', required='optional', varname='foo'),
    ]
    block.digest_template = '{{block_var_foo}}'
    block.store()

    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.PageField(id='1', label='Form Page'),
        fields.PageField(id='3', label='Hidden Page', condition={'type': 'django', 'value': 'False'}),
        fields.TitleField(id='4', label='Second Form Title'),
        fields.BlockField(id='5', label='Second Block Test', required='optional', block_slug='foobar'),
        fields.PageField(id='6', label='Form Page'),
        fields.TitleField(id='7', label='Form Title'),
        fields.BlockField(id='8', label='Block Test', required='optional', block_slug='foobar'),
    ]
    formdef.store()

    # filled
    app = login(get_app(pub), username='foo', password='foo')
    resp = app.get(formdef.get_url())
    resp = resp.form.submit('submit')  # -> second page
    resp.form['f8$element0$f123'] = 'Blah Field'
    resp = resp.form.submit('submit')  # -> validation page
    resp = resp.form.submit('submit').follow()  # -> submitted
    assert 'Form Page' in resp.text
    assert 'Form Title' in resp.text
    assert 'Blah Field' in resp.text
    assert 'Form Page' in emails.get('New form (form title)')['msg'].get_payload()[0].get_payload()
    assert 'Form Title' in emails.get('New form (form title)')['msg'].get_payload()[0].get_payload()
    assert 'Blah Field' in emails.get('New form (form title)')['msg'].get_payload()[0].get_payload()

    # empty
    emails.empty()
    app = login(get_app(pub), username='foo', password='foo')
    resp = app.get(formdef.get_url())
    resp = resp.form.submit('submit')  # -> second page
    resp.form['f8$element0$f123'] = ''  # left empty
    resp = resp.form.submit('submit')  # -> validation page
    resp = resp.form.submit('submit').follow()  # -> submitted
    assert 'Form Page' not in resp.text
    assert 'Form Title' not in resp.text
    assert 'Form Page' not in emails.get('New form (form title)')['msg'].get_payload()[0].get_payload()
    assert 'Form Title' not in emails.get('New form (form title)')['msg'].get_payload()[0].get_payload()


@pytest.mark.parametrize('logged_user', ['logged', 'anonymous'])
@pytest.mark.parametrize('tracking_code', ['with-tracking-code', 'without-tracking-code'])
def test_block_multiple_rows_single_draft(pub, logged_user, tracking_code):
    create_user(pub)
    FormDef.wipe()
    BlockDef.wipe()

    block = BlockDef()
    block.name = 'foobar'
    block.fields = [fields.StringField(id='123', required='required', label='Test')]
    block.store()

    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.BlockField(id='1', label='test', block_slug='foobar', max_items='5'),
    ]
    formdef.enable_tracking_codes = bool(tracking_code == 'with-tracking-code')
    formdef.store()
    formdef.data_class().wipe()

    app = get_app(pub)
    if logged_user == 'logged':
        login(app, username='foo', password='foo')
    resp = app.get(formdef.get_url())
    resp.form['f1$element0$f123'].value = 'Hello World'
    resp = resp.form.submit('f1$add_element')  # add second row

    if logged_user == 'logged' or formdef.enable_tracking_codes:
        assert formdef.data_class().count() == 1
        assert formdef.data_class().select()[0].status == 'draft'
    else:
        assert formdef.data_class().count() == 0

    resp.form['f1$element1$f123'].value = 'Something else'
    resp = resp.form.submit('f1$add_element')  # add third row

    if logged_user == 'logged' or formdef.enable_tracking_codes:
        assert formdef.data_class().count() == 1
        assert formdef.data_class().select()[0].status == 'draft'
    else:
        assert formdef.data_class().count() == 0

    resp.form['f1$element2$f123'].value = 'Something else'
    resp = resp.form.submit('submit')  # -> validation page
    resp = resp.form.submit('submit')  # -> end page
    resp = resp.follow()

    assert formdef.data_class().count() == 1
    assert formdef.data_class().select()[0].status == 'wf-new'


def test_block_field_post_condition(pub):
    FormDef.wipe()
    BlockDef.wipe()

    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.StringField(id='123', label='Foo', varname='foo'),
        fields.StringField(id='234', label='Bar', varname='bar'),
    ]
    block.post_conditions = [
        {
            'condition': {'type': 'django', 'value': 'block_var_foo|startswith:"b"'},
            'error_message': 'foo must start with a b.',
        },
        {
            'condition': {'type': 'django', 'value': 'block_var_foo == block_var_bar'},
            'error_message': 'foo and bar must be identical.',
        },
    ]

    block.store()

    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.BlockField(id='1', label='test', block_slug='foobar'),
    ]
    formdef.store()

    app = get_app(pub)
    resp = app.get(formdef.get_url())
    resp.form['f1$element0$f123'] = 'foo'
    resp.form['f1$element0$f234'] = 'bar'
    resp = resp.form.submit('submit')  # -> error
    assert (
        resp.pyquery('.widget-with-error .error').text()
        == 'foo must start with a b. foo and bar must be identical.'
    )

    resp.form['f1$element0$f123'] = 'baz'
    resp.form['f1$element0$f234'] = 'bar'
    resp = resp.form.submit('submit')  # -> error
    assert resp.pyquery('.widget-with-error .error').text() == 'foo and bar must be identical.'

    resp.form['f1$element0$f123'] = 'baz'
    resp.form['f1$element0$f234'] = 'baz'
    resp = resp.form.submit('submit')  # -> validation page

    assert resp.form['f1$element0$f123'].attrs['readonly']
    resp = resp.form.submit('submit')  # -> end page

    formdata = formdef.data_class().select()[0]
    assert formdata.status == 'wf-new'
    assert formdata.data == {
        '1': {'data': [{'123': 'baz', '234': 'baz'}], 'schema': {'123': 'string', '234': 'string'}},
        '1_display': 'foobar',
    }

    # multiple rows
    formdef.fields[0].max_items = '3'
    formdef.store()
    formdef.data_class().wipe()
    resp = app.get(formdef.get_url())
    resp.form['f1$element0$f123'] = 'baz'
    resp.form['f1$element0$f234'] = 'bar'
    resp = resp.form.submit('f1$add_element')
    assert not resp.pyquery('.widget-with-error')

    resp.form['f1$element1$f123'] = 'bar'
    resp.form['f1$element1$f234'] = 'bar'
    resp = resp.form.submit('submit')  # -> error
    assert (
        resp.pyquery('.widget-with-error[data-block-row="element0"] .error').text()
        == 'foo and bar must be identical.'
    )
    assert resp.pyquery('.widget-with-error[data-block-row="element1"] .error').text() == ''

    resp.form['f1$element1$f234'] = 'baz'
    resp = resp.form.submit('submit')  # -> error
    assert (
        resp.pyquery('.widget-with-error[data-block-row="element0"] .error').text()
        == 'foo and bar must be identical.'
    )
    assert (
        resp.pyquery('.widget-with-error[data-block-row="element1"] .error').text()
        == 'foo and bar must be identical.'
    )

    block.post_conditions = [
        {
            'condition': {'type': 'django', 'value': 'block_var_foo|startswith:"b"'},
            'error_message': 'foo must start with a b.',
        },
        {
            'condition': {'type': 'django', 'value': 'block_var_foo == block_var_bar'},
            'error_message': 'foo and bar must be identical ({{block_var_foo}} != {{block_var_bar}}).',
        },
    ]
    block.store()
    resp.form['f1$element1$f234'] = 'baz'
    resp = resp.form.submit('submit')  # -> error
    assert (
        resp.pyquery('.widget-with-error[data-block-row="element0"] .error').text()
        == 'foo and bar must be identical (baz != bar).'
    )
    assert (
        resp.pyquery('.widget-with-error[data-block-row="element1"] .error').text()
        == 'foo and bar must be identical (bar != baz).'
    )

    resp.form['f1$element0$f123'] = 'bar'
    resp.form['f1$element1$f234'] = 'bar'
    resp = resp.form.submit('submit')  # -> validation page
    assert resp.form['f1$element0$f123'].attrs['readonly']
    resp = resp.form.submit('submit')  # -> end page
    formdata = formdef.data_class().select()[0]
    assert formdata.status == 'wf-new'
    assert formdata.data == {
        '1': {
            'data': [{'123': 'bar', '234': 'bar'}, {'123': 'bar', '234': 'bar'}],
            'schema': {'123': 'string', '234': 'string'},
        },
        '1_display': 'foobar, foobar',
    }


def test_block_field_post_condition_on_empty_content(pub):
    FormDef.wipe()
    BlockDef.wipe()
    LoggedError.wipe()

    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.FileField(id='123', label='foo', varname='foo'),
    ]
    block.post_conditions = [
        {
            'condition': {'type': 'django', 'value': 'block_var_foo|convert_image_format:"jpeg"'},
            'error_message': 'block validation error',
        },
    ]
    block.store()

    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.BlockField(id='1', label='test', block_slug='foobar', required='optional'),
    ]
    formdef.store()

    app = get_app(pub)
    resp = app.get(formdef.get_url())
    resp = resp.form.submit('submit')  # -> no error
    assert not resp.pyquery('.widget-with-error')
    assert not LoggedError.count()


def test_block_field_post_condition_on_no_data_content(pub):
    FormDef.wipe()
    BlockDef.wipe()

    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.CommentField(id='123', label='What are you doing here?'),
    ]
    block.post_conditions = [
        {
            'condition': {'type': 'django', 'value': 'form_user_email|endswith:"@fbi.gov"'},
            'error_message': 'This form is strictly restricted to bureau agents.',
        },
    ]

    block.store()

    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.BlockField(id='1', label='test', block_slug='foobar'),
        fields.TextField(id='2', label='Text', varname='text'),
    ]
    formdef.store()

    app = get_app(pub)
    resp = app.get(formdef.get_url())
    resp.form['f2'] = 'blah'
    resp = resp.form.submit('submit')  # -> error
    assert (
        resp.pyquery('.widget-with-error .error').text()
        == 'This form is strictly restricted to bureau agents.'
    )


def test_block_with_block_field_live_comment(pub):
    BlockDef.wipe()
    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.StringField(id='123', required='required', label='One', varname='one'),
        fields.CommentField(
            id='234',
            label='test x{{ block_var_one }}y',
        ),
    ]
    block.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [fields.BlockField(id='1', label='test', block_slug='foobar', max_items='3')]
    formdef.store()
    formdef.data_class().wipe()

    app = get_app(pub)
    resp = app.get(formdef.get_url())
    assert resp.pyquery('[data-widget-name="f1$element0$f123"]').attr['data-live-source'] == 'true'
    resp.form['f1$element0$f123'] = 'foo'
    live_resp = app.post(
        formdef.get_url() + 'live',
        params=resp.form.submit_fields() + [('modified_field_id[]', '123')],
    )
    assert live_resp.json['result']['1-234-0']['content'] == '<p>test xfooy</p>'

    # add second line
    resp = resp.form.submit('f1$add_element')
    resp.form['f1$element1$f123'] = 'bar'
    live_resp = app.post(
        formdef.get_url() + 'live',
        params=resp.form.submit_fields() + [('modified_field_id[]', '123')],
    )
    assert live_resp.json['result']['1-234-0']['content'] == '<p>test xfooy</p>'
    assert live_resp.json['result']['1-234-1']['content'] == '<p>test xbary</p>'


def test_block_with_block_field_live_select_options(pub):
    BlockDef.wipe()
    CardDef.wipe()
    FormDef.wipe()

    carddef = CardDef()
    carddef.name = 'foo'
    carddef.digest_templates = {'default': '{{form_var_attr}}'}
    carddef.fields = [
        fields.StringField(id='1', label='string', varname='attr'),
        fields.ItemField(id='2', label='item', varname='item', items=['foo', 'bar', 'baz']),
    ]
    carddef.store()

    for i, value in enumerate(['foo'] * 3 + ['bar'] * 2 + ['baz']):
        carddata = carddef.data_class()()
        carddata.data = {
            '1': f'x {value} {i}',
            '2': value,
            '2_display': value,
        }
        carddata.just_created()
        carddata.store()

    custom_view = pub.custom_view_class()
    custom_view.formdef = carddef
    custom_view.visibility = 'datasource'
    custom_view.slug = 'as-data-source'
    custom_view.title = 'as data source'
    custom_view.filters = {
        'filter-2': 'on',
        'filter-2-operator': 'eq',
        'filter-2-value': '{{ block_var_blah }}',
    }
    custom_view.store()

    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.ItemField(id='123', label='item', varname='blah', items=['foo', 'bar', 'baz']),
        fields.ItemField(id='234', label='card', data_source={'type': 'carddef:foo:as-data-source'}),
    ]
    block.store()

    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [fields.BlockField(id='1', label='test', block_slug='foobar', max_items='3')]
    formdef.store()
    formdef.data_class().wipe()

    app = get_app(pub)
    resp = app.get(formdef.get_url())
    assert resp.pyquery('[data-widget-name="f1$element0$f123"]').attr['data-live-source'] == 'true'
    resp.form['f1$element0$f123'] = 'bar'
    live_resp = app.post(
        formdef.get_url() + 'live',
        params=resp.form.submit_fields()
        + [
            ('modified_field_id[]', '1'),
            ('modified_field_id[]', '1 123 element0'),
        ],
    )
    assert [x['text'] for x in live_resp.json['result']['1-234-0']['items']] == ['x bar 3', 'x bar 4']
    resp.form['f1$element0$f234'].force_value(str(live_resp.json['result']['1-234-0']['items'][0]['id']))

    # add second line
    resp = resp.form.submit('f1$add_element')
    resp.form['f1$element1$f123'] = 'baz'
    live_resp = app.post(
        formdef.get_url() + 'live',
        params=resp.form.submit_fields()
        + [
            ('modified_field_id[]', '1'),
            ('modified_field_id[]', '1 123 element1'),
        ],
    )
    assert [x['text'] for x in live_resp.json['result']['1-234-1']['items']] == ['x baz 5']
    resp.form['f1$element1$f234'].force_value(str(live_resp.json['result']['1-234-1']['items'][0]['id']))

    resp = resp.form.submit('submit')  # -> validation
    assert not resp.pyquery('.widget-with-error')
    assert resp.pyquery('[name="f1$element0$f234_label"]').attr.value == 'x bar 3'
    assert resp.pyquery('[name="f1$element1$f234_label"]').attr.value == 'x baz 5'
    resp = resp.form.submit('previous')  # -> back to form page
    assert resp.form['f1$element0$f234'].value == '4'
    assert resp.form['f1$element1$f234'].value == '6'
    resp = resp.form.submit('submit')  # -> validation
    resp = resp.form.submit('submit')  # -> submit
    assert formdef.data_class().select()[0].data['1']['data'][0]['234'] == '4'
    assert formdef.data_class().select()[0].data['1']['data'][0]['234_display'] == 'x bar 3'
    assert formdef.data_class().select()[0].data['1']['data'][1]['234'] == '6'
    assert formdef.data_class().select()[0].data['1']['data'][1]['234_display'] == 'x baz 5'


def test_block_with_block_field_live_select2_options(pub):
    BlockDef.wipe()
    CardDef.wipe()
    FormDef.wipe()

    carddef = CardDef()
    carddef.name = 'foo'
    carddef.digest_templates = {'default': '{{form_var_attr}}'}
    carddef.fields = [
        fields.StringField(id='1', label='string', varname='attr'),
        fields.ItemField(id='2', label='item', varname='item', items=['foo', 'bar', 'baz']),
    ]
    carddef.store()

    for i, value in enumerate(['foo'] * 3 + ['bar'] * 2 + ['baz']):
        carddata = carddef.data_class()()
        carddata.data = {
            '1': f'x {value} {i}',
            '2': value,
            '2_display': value,
        }
        carddata.just_created()
        carddata.store()

    custom_view = pub.custom_view_class()
    custom_view.formdef = carddef
    custom_view.visibility = 'datasource'
    custom_view.slug = 'as-data-source'
    custom_view.title = 'as data source'
    custom_view.filters = {
        'filter-2': 'on',
        'filter-2-operator': 'eq',
        'filter-2-value': '{{ block_var_blah }}',
    }
    custom_view.store()

    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.ItemField(id='123', label='item', varname='blah', items=['foo', 'bar', 'baz']),
        fields.ItemField(
            id='234',
            label='card',
            display_mode='autocomplete',
            data_source={'type': 'carddef:foo:as-data-source'},
        ),
    ]
    block.store()

    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [fields.BlockField(id='1', label='test', block_slug='foobar', max_items='3')]
    formdef.store()
    formdef.data_class().wipe()

    app = get_app(pub)
    resp = app.get(formdef.get_url())
    assert resp.pyquery('[data-widget-name="f1$element0$f123"]').attr['data-live-source'] == 'true'
    resp.form['f1$element0$f123'] = 'bar'
    live_resp = app.post(
        formdef.get_url() + 'live',
        params=resp.form.submit_fields()
        + [
            ('modified_field_id[]', '1'),
            ('modified_field_id[]', '1 123 element0'),
        ],
    )
    row0_autocomplete_url = live_resp.json['result']['1-234-0']['source_url']
    autocomplete_json = app.get(row0_autocomplete_url).json
    assert [x['text'] for x in autocomplete_json['data']] == ['x bar 3', 'x bar 4']
    resp.form['f1$element0$f234'].force_value(str(autocomplete_json['data'][0]['id']))

    # add second line
    resp = resp.form.submit('f1$add_element')

    # check first line as kept selected value, as data attributes
    resp.form['f1$element0$f234'].force_value(resp.form['f1$element0$f234'].attrs['data-value'])
    assert resp.form['f1$element0$f234'].attrs['data-initial-display-value'] == 'x bar 3'
    assert resp.form['f1$element0$f234'].attrs['data-select2-url'] == row0_autocomplete_url

    # fill second line
    resp.form['f1$element1$f123'] = 'baz'
    live_resp = app.post(
        formdef.get_url() + 'live',
        params=resp.form.submit_fields()
        + [
            ('modified_field_id[]', '1'),
            ('modified_field_id[]', '1 123 element1'),
        ],
    )
    row1_autocomplete_url = live_resp.json['result']['1-234-1']['source_url']
    assert row0_autocomplete_url != row1_autocomplete_url
    autocomplete_json = app.get(row1_autocomplete_url).json
    assert [x['text'] for x in autocomplete_json['data']] == ['x baz 5']
    resp.form['f1$element1$f234'].force_value(str(autocomplete_json['data'][0]['id']))

    resp = resp.form.submit('submit')  # -> validation
    assert not resp.pyquery('.widget-with-error')
    assert resp.pyquery('[name="f1$element0$f234_label"]').attr.value == 'x bar 3'
    assert resp.pyquery('[name="f1$element1$f234_label"]').attr.value == 'x baz 5'
    resp = resp.form.submit('previous')  # -> back to form page
    resp.form['f1$element0$f234'].force_value(resp.form['f1$element0$f234'].attrs['data-value'])
    resp.form['f1$element1$f234'].force_value(resp.form['f1$element1$f234'].attrs['data-value'])
    assert resp.form['f1$element0$f234'].value == '4'
    assert resp.form['f1$element1$f234'].value == '6'
    resp = resp.form.submit('submit')  # -> validation
    resp = resp.form.submit('submit')  # -> submit
    assert formdef.data_class().select()[0].data['1']['data'][0]['234'] == '4'
    assert formdef.data_class().select()[0].data['1']['data'][0]['234_display'] == 'x bar 3'
    assert formdef.data_class().select()[0].data['1']['data'][1]['234'] == '6'
    assert formdef.data_class().select()[0].data['1']['data'][1]['234_display'] == 'x baz 5'


def test_block_with_block_field_live_items_options(pub):
    BlockDef.wipe()
    CardDef.wipe()
    FormDef.wipe()

    carddef = CardDef()
    carddef.name = 'foo'
    carddef.digest_templates = {'default': '{{form_var_attr}}'}
    carddef.fields = [
        fields.StringField(id='1', label='string', varname='attr'),
        fields.ItemField(id='2', label='item', varname='item', items=['foo', 'bar', 'baz']),
    ]
    carddef.store()

    for i, value in enumerate(['foo'] * 3 + ['bar'] * 3 + ['baz']):
        carddata = carddef.data_class()()
        carddata.data = {
            '1': f'x {value} {i}',
            '2': value,
            '2_display': value,
        }
        carddata.just_created()
        carddata.store()

    custom_view = pub.custom_view_class()
    custom_view.formdef = carddef
    custom_view.visibility = 'datasource'
    custom_view.slug = 'as-data-source'
    custom_view.title = 'as data source'
    custom_view.filters = {
        'filter-2': 'on',
        'filter-2-operator': 'eq',
        'filter-2-value': '{{ block_var_blah }}',
    }
    custom_view.store()

    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.ItemField(id='123', label='item', varname='blah', items=['foo', 'bar', 'baz']),
        fields.ItemsField(id='234', label='card', data_source={'type': 'carddef:foo:as-data-source'}),
    ]
    block.store()

    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [fields.BlockField(id='1', label='test', block_slug='foobar', max_items='3')]
    formdef.store()
    formdef.data_class().wipe()

    app = get_app(pub)
    resp = app.get(formdef.get_url())
    assert resp.pyquery('[data-widget-name="f1$element0$f123"]').attr['data-live-source'] == 'true'
    assert not resp.pyquery('[data-field-id="234"] input')
    resp.form['f1$element0$f123'] = 'bar'
    live_resp = app.post(
        formdef.get_url() + 'live',
        params=resp.form.submit_fields()
        + [
            ('modified_field_id[]', '1'),
            ('modified_field_id[]', '1 123 element0'),
        ],
    )
    assert [x['text'] for x in live_resp.json['result']['1-234-0']['items']] == [
        'x bar 3',
        'x bar 4',
        'x bar 5',
    ]
    # simulate js, add relevant checkboxes
    for option in live_resp.json['result']['1-234-0']['items']:
        checkbox_name = '%s$element%s' % (
            resp.pyquery('[data-field-id="234"]')[0].attrib['data-widget-name'],
            option['id'],
        )
        resp.form.fields[checkbox_name] = Checkbox(
            form=resp.form, name=checkbox_name, tag='input', value='yes', pos=10
        )
        resp.form.field_order.append((checkbox_name, resp.form.fields[checkbox_name]))
        if option['text'] != 'x bar 4':
            resp.form.fields[checkbox_name].checked = True

    # add second line
    resp = resp.form.submit('f1$add_element')
    resp.form['f1$element1$f123'] = 'baz'
    live_resp = app.post(
        formdef.get_url() + 'live',
        params=resp.form.submit_fields()
        + [
            ('modified_field_id[]', '1'),
            ('modified_field_id[]', '1 123 element1'),
        ],
    )
    assert [x['text'] for x in live_resp.json['result']['1-234-1']['items']] == ['x baz 6']
    # simulate js, add relevant checkboxes
    for option in live_resp.json['result']['1-234-1']['items']:
        checkbox_name = '%s$element%s' % (
            resp.pyquery('[data-field-id="234"]')[1].attrib['data-widget-name'],
            option['id'],
        )
        resp.form.fields[checkbox_name] = Checkbox(
            form=resp.form, name=checkbox_name, tag='input', value='yes', pos=10
        )
        resp.form.field_order.append((checkbox_name, resp.form.fields[checkbox_name]))
        resp.form.fields[checkbox_name].checked = True

    resp = resp.form.submit('submit')  # -> validation
    assert not resp.pyquery('.widget-with-error')
    assert [x.text for x in resp.pyquery('[data-widget-name="f1$element0$f234"] input[checked] + span')] == [
        'x bar 3',
        'x bar 5',
    ]
    assert [x.text for x in resp.pyquery('[data-widget-name="f1$element1$f234"] input[checked] + span')] == [
        'x baz 6'
    ]
    resp = resp.form.submit('submit')  # -> submit
    assert formdef.data_class().select()[0].data['1']['data'][0]['234'] == ['4', '6']
    assert formdef.data_class().select()[0].data['1']['data'][0]['234_display'] == 'x bar 3, x bar 5'
    assert formdef.data_class().select()[0].data['1']['data'][0]['234_structured']
    assert formdef.data_class().select()[0].data['1']['data'][1]['234'] == ['7']
    assert formdef.data_class().select()[0].data['1']['data'][1]['234_display'] == 'x baz 6'
    assert formdef.data_class().select()[0].data['1']['data'][1]['234_structured']
    resp = resp.follow()
    assert resp.pyquery('#form-field-label-f1-r0-s234 + div').text() == 'x bar 3\nx bar 5'
    assert resp.pyquery('#form-field-label-f1-r1-s234 + div').text() == 'x baz 6'


def test_block_with_block_field_live_prefill(pub):
    BlockDef.wipe()
    FormDef.wipe()

    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.StringField(id='123', label='a', varname='a'),
        fields.StringField(id='234', label='b', varname='b'),
        fields.StringField(
            id='345',
            label='ab',
            prefill={'type': 'string', 'value': 'X{{block_var_a|default:""}} {{block_var_b|default:""}}'},
        ),
    ]
    block.store()

    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [fields.BlockField(id='1', label='test', block_slug='foobar', max_items='3')]
    formdef.store()
    formdef.data_class().wipe()

    app = get_app(pub)
    resp = app.get(formdef.get_url())
    assert resp.pyquery('[data-widget-name="f1$element0$f123"]').attr['data-live-source'] == 'true'
    resp.form['f1$element0$f123'] = 'bar'
    resp.form['f1$element0$f234'] = 'baz'
    live_resp = app.post(
        formdef.get_url() + 'live',
        params=resp.form.submit_fields()
        + [
            ('modified_field_id[]', '1'),
            ('modified_field_id[]', '1 123 element0'),
            ('modified_field_id[]', '1 234 element0'),
            ('prefilled_1-345-element0', 'on'),
        ],
    )
    assert live_resp.json['result']['1-345-0']['content'] == 'Xbar baz'
    resp.form['f1$element0$f345'] = live_resp.json['result']['1-345-0']['content']

    # add second line
    resp = resp.form.submit('f1$add_element')
    resp.form['f1$element1$f123'] = 'baz'
    resp.form['f1$element1$f234'] = 'foo'
    live_resp = app.post(
        formdef.get_url() + 'live',
        params=resp.form.submit_fields()
        + [
            ('modified_field_id[]', '1'),
            ('modified_field_id[]', '1 123 element1'),
            ('modified_field_id[]', '1 234 element1'),
            ('prefilled_1-345-element0', 'on'),
            ('prefilled_1-345-element1', 'on'),
        ],
    )
    assert live_resp.json['result']['1-345-0']['content'] == 'Xbar baz'
    assert live_resp.json['result']['1-345-1']['content'] == 'Xbaz foo'
    resp.form['f1$element0$f345'] = live_resp.json['result']['1-345-0']['content']
    resp.form['f1$element1$f345'] = live_resp.json['result']['1-345-1']['content']

    resp = resp.form.submit('submit')  # -> validation
    resp = resp.form.submit('submit')  # -> submit
    assert formdef.data_class().select()[0].data['1']['data'][0]['345'] == 'Xbar baz'
    assert formdef.data_class().select()[0].data['1']['data'][1]['345'] == 'Xbaz foo'


def test_block_with_block_field_live_prefill_locked(pub):
    BlockDef.wipe()
    FormDef.wipe()

    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.StringField(id='123', label='a', varname='a'),
        fields.StringField(
            id='345',
            label='ab',
            prefill={'type': 'string', 'value': 'X{{block_var_a|default:""}}Y', 'locked': True},
        ),
    ]
    block.store()

    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.StringField(id='0', label='test', required='required'),
        fields.BlockField(id='1', label='test', block_slug='foobar', max_items='3'),
    ]
    formdef.store()
    formdef.data_class().wipe()

    app = get_app(pub)
    resp = app.get(formdef.get_url())
    assert resp.pyquery('[data-widget-name="f1$element0$f123"]').attr['data-live-source'] == 'true'
    assert resp.pyquery('[data-widget-name="f1$element0$f345"].widget-prefilled.widget-readonly')
    assert resp.pyquery('#form_f1__element0__f345').attr.readonly == 'readonly'
    resp.form['f1$element0$f123'] = 'bar'
    live_resp = app.post(
        formdef.get_url() + 'live',
        params=resp.form.submit_fields()
        + [
            ('modified_field_id[]', '1'),
            ('modified_field_id[]', '1 123 element0'),
            ('prefilled_1-345-element0', 'on'),
        ],
    )
    assert live_resp.json['result']['1-345-0']['content'] == 'XbarY'
    resp.form['f1$element0$f345'] = live_resp.json['result']['1-345-0']['content']

    # add second line
    resp = resp.form.submit('f1$add_element')
    assert resp.pyquery('[data-widget-name="f1$element0$f345"].widget-prefilled.widget-readonly')
    assert resp.pyquery('#form_f1__element0__f345').attr.readonly == 'readonly'
    assert resp.pyquery('[data-widget-name="f1$element1$f345"].widget-prefilled.widget-readonly')
    assert resp.pyquery('#form_f1__element1__f345').attr.readonly == 'readonly'
    resp.form['f1$element1$f123'] = 'baz'
    live_resp = app.post(
        formdef.get_url() + 'live',
        params=resp.form.submit_fields()
        + [
            ('modified_field_id[]', '1'),
            ('modified_field_id[]', '1 123 element1'),
            ('prefilled_1-345-element0', 'on'),
            ('prefilled_1-345-element1', 'on'),
        ],
    )
    assert live_resp.json['result']['1-345-0']['content'] == 'XbarY'
    assert live_resp.json['result']['1-345-1']['content'] == 'XbazY'
    resp.form['f1$element0$f345'] = live_resp.json['result']['1-345-0']['content']
    resp.form['f1$element1$f345'] = live_resp.json['result']['1-345-1']['content']

    resp = resp.form.submit('submit')  # -> error on field 0
    resp.form['f0'] = 'test'
    assert resp.form['f1$element0$f345'].value == 'XbarY'
    assert resp.pyquery('[data-widget-name="f1$element0$f345"].widget-prefilled.widget-readonly')
    assert resp.pyquery('#form_f1__element0__f345').attr.readonly == 'readonly'
    assert resp.form['f1$element1$f345'].value == 'XbazY'
    assert resp.pyquery('[data-widget-name="f1$element1$f345"].widget-prefilled.widget-readonly')
    assert resp.pyquery('#form_f1__element1__f345').attr.readonly == 'readonly'

    resp = resp.form.submit('submit')  # -> validation
    resp = resp.form.submit('submit')  # -> submit
    assert formdef.data_class().select()[0].data['1']['data'][0]['345'] == 'XbarY'
    assert formdef.data_class().select()[0].data['1']['data'][1]['345'] == 'XbazY'


@responses.activate
def test_block_digest_item_id(pub):
    responses.get('http://whatever/data-source?id=1', json={'data': [{'id': '1', 'text': 'foo'}]})
    responses.get('http://whatever/data-source?id=foo', json={'err': 1})
    FormDef.wipe()
    BlockDef.wipe()
    NamedDataSource.wipe()

    # add a named data source
    data_source = NamedDataSource(name='foobar')
    data_source.data_source = {'type': 'json', 'value': 'http://whatever/data-source'}
    data_source.id_parameter = 'id'
    data_source.query_parameter = 'q'
    data_source.record_on_errors = True
    data_source.store()

    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.ItemField(
            id='234',
            required='required',
            label='Test2',
            varname='bar',
            data_source={'type': 'foobar'},
            display_mode='autocomplete',
        ),
    ]
    block.store()

    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.BlockField(id='1', label='test', block_slug='foobar', max_items='3'),
    ]
    formdef.store()
    formdef.data_class().wipe()

    app = get_app(pub)
    resp = app.get(formdef.get_url())
    resp.form['f1$element0$f234'].force_value('1')
    resp = resp.form.submit('submit')  # -> validation page
    resp = resp.form.submit('submit')  # -> submit

    assert formdef.data_class().select()[0].data['1']['data'] == [
        {'234': '1', '234_display': 'foo', '234_structured': None}
    ]
    assert LoggedError.count() == 0


def test_block_remove_button_label(pub):
    FormDef.wipe()
    BlockDef.wipe()

    block = BlockDef()
    block.name = 'foobar'
    block.fields = [fields.StringField(id='123', required='required', label='Test')]
    block.store()

    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.BlockField(id='1', label='test', block_slug='foobar', max_items='5', remove_button=True),
    ]
    formdef.store()
    formdef.data_class().wipe()

    app = get_app(pub)
    resp = app.get(formdef.get_url())
    assert resp.pyquery('.remove-button').attr.title == 'Remove'

    formdef.fields[0].remove_element_label = 'Remove Test'
    formdef.store()
    resp = app.get(formdef.get_url())
    assert resp.pyquery('.remove-button').attr.title == 'Remove Test'


def test_block_with_block_field_live_item_items_multilevel(pub):
    BlockDef.wipe()
    CardDef.wipe()
    FormDef.wipe()

    # Three level of cards
    carddef1 = CardDef()
    carddef1.name = 'card1'
    carddef1.digest_templates = {'default': '{{form_var_attr}}'}
    carddef1.fields = [
        fields.StringField(id='1', label='string', varname='attr'),
    ]
    carddef1.store()

    for i, value in enumerate(['foo', 'bar', 'baz']):
        carddata = carddef1.data_class()()
        carddata.id = i + 1
        carddata.data = {
            '1': f'card1 {value} {i}',
        }
        carddata.just_created()
        carddata.store()

    carddef2 = CardDef()
    carddef2.name = 'card2'
    carddef2.digest_templates = {'default': '{{form_var_attr}}'}
    carddef2.fields = [
        fields.StringField(id='1', label='string', varname='attr'),
        fields.ItemField(id='2', label='card', data_source={'type': 'carddef:card1'}),
    ]
    carddef2.store()

    i = 1
    for card in carddef1.data_class().select(order_by='id'):
        for value in ['foo', 'bar', 'baz']:
            carddata = carddef2.data_class()()
            carddata.id = i
            carddata.data = {
                '1': f'card2 {value} {i}',
                '2': str(card.id),
            }
            carddata.just_created()
            carddata.store()
            i += 1

    carddef3 = CardDef()
    carddef3.name = 'card3'
    carddef3.digest_templates = {'default': '{{form_var_attr}}'}
    carddef3.fields = [
        fields.StringField(id='1', label='string', varname='attr'),
        fields.ItemField(id='2', label='card', data_source={'type': 'carddef:card2'}),
    ]
    carddef3.store()

    i = 1
    for card in carddef2.data_class().select(order_by='id'):
        for value in ['foo', 'bar', 'baz']:
            carddata = carddef3.data_class()()
            carddata.id = i
            carddata.data = {
                '1': f'card3 {value} {i}',
                '2': str(card.id),
            }
            carddata.just_created()
            carddata.store()
            i += 1

    custom_view = pub.custom_view_class()
    custom_view.formdef = carddef2
    custom_view.order_by = 'id'
    custom_view.visibility = 'datasource'
    custom_view.slug = 'as-data-source'
    custom_view.title = 'as data source'
    custom_view.filters = {
        'filter-2': 'on',
        'filter-2-operator': 'eq',
        'filter-2-value': '{{ block_var_card1_raw }}',
    }
    custom_view.store()

    custom_view = pub.custom_view_class()
    custom_view.formdef = carddef3
    custom_view.order_by = 'id'
    custom_view.visibility = 'datasource'
    custom_view.slug = 'as-data-source'
    custom_view.title = 'as data source'
    custom_view.filters = {
        'filter-2': 'on',
        'filter-2-operator': 'eq',
        'filter-2-value': '{{ block_var_card2_raw }}',
    }
    custom_view.store()

    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.ItemField(id='1', varname='card1', label='card', data_source={'type': 'carddef:card1'}),
        fields.ItemField(
            id='2',
            varname='card2',
            label='card',
            display_mode='radio',
            data_source={'type': 'carddef:card2:as-data-source'},
        ),
        fields.ItemsField(
            id='3', varname='card3', label='card', data_source={'type': 'carddef:card3:as-data-source'}
        ),
    ]
    block.store()

    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [fields.BlockField(id='1', label='test', block_slug='foobar', max_items='3')]
    formdef.store()
    formdef.data_class().wipe()

    # filling
    app = get_app(pub)
    resp = app.get(formdef.get_url())
    assert resp.pyquery('[data-widget-name="f1$element0$f1"]').attr['data-live-source'] == 'true'
    resp.form['f1$element0$f1'] = '1'
    live_resp = app.post(
        formdef.get_url() + 'live',
        params=resp.form.submit_fields()
        + [
            ('modified_field_id[]', '1'),
            ('modified_field_id[]', '1 1 element0'),
        ],
    )
    assert live_resp.json['result'] == {
        '1': {
            'visible': True,
        },
        '1-1-0': {
            'block_id': '1',
            'block_row': 'element0',
            'field_id': '1',
            'row': 0,
            'visible': True,
        },
        '1-2-0': {
            'block_id': '1',
            'block_row': 'element0',
            'field_id': '2',
            'items': [
                {
                    'id': 1,
                    'attr': 'card2 foo 1',
                    'text': 'card2 foo 1',
                },
                {
                    'id': 2,
                    'attr': 'card2 bar 2',
                    'text': 'card2 bar 2',
                },
                {
                    'id': 3,
                    'attr': 'card2 baz 3',
                    'text': 'card2 baz 3',
                },
            ],
            'row': 0,
            'visible': True,
        },
        '1-3-0': {
            'block_id': '1',
            'block_row': 'element0',
            'field_id': '3',
            'row': 0,
            'visible': True,
        },
    }

    resp.form['f1$element0$f2'].force_value('1')

    live_resp = app.post(
        formdef.get_url() + 'live',
        params=resp.form.submit_fields()
        + [
            ('modified_field_id[]', '1'),
            ('modified_field_id[]', '1 2 element0'),
        ],
    )

    assert live_resp.json['result'] == {
        '1': {
            'visible': True,
        },
        '1-1-0': {
            'block_id': '1',
            'block_row': 'element0',
            'field_id': '1',
            'row': 0,
            'visible': True,
        },
        '1-2-0': {
            'block_id': '1',
            'block_row': 'element0',
            'field_id': '2',
            'row': 0,
            'visible': True,
        },
        '1-3-0': {
            'block_id': '1',
            'block_row': 'element0',
            'field_id': '3',
            'items': [
                {
                    'id': 1,
                    'attr': 'card3 foo 1',
                    'text': 'card3 foo 1',
                },
                {
                    'id': 2,
                    'attr': 'card3 bar 2',
                    'text': 'card3 bar 2',
                },
                {
                    'id': 3,
                    'attr': 'card3 baz 3',
                    'text': 'card3 baz 3',
                },
            ],
            'row': 0,
            'visible': True,
        },
    }

    # simulate js, add relevant checkboxes
    for option in live_resp.json['result']['1-3-0']['items']:
        checkbox_name = 'f1$element0$f3$element%s' % option['id']
        widget = Checkbox(form=resp.form, name=checkbox_name, tag='input', value='yes', pos=10)
        resp.form.fields[checkbox_name] = [widget]
        resp.form.field_order.append((checkbox_name, widget))

    resp.form['f1$element0$f3$element1'].checked = True

    live_resp = app.post(
        formdef.get_url() + 'live',
        params=resp.form.submit_fields()
        + [
            ('modified_field_id[]', '1'),
            ('modified_field_id[]', '1 3 element0'),
        ],
    )

    assert live_resp.json['result'] == {
        '1': {
            'visible': True,
        },
        '1-1-0': {
            'block_id': '1',
            'block_row': 'element0',
            'field_id': '1',
            'row': 0,
            'visible': True,
        },
        '1-2-0': {
            'block_id': '1',
            'block_row': 'element0',
            'field_id': '2',
            'row': 0,
            'visible': True,
        },
        '1-3-0': {
            'block_id': '1',
            'block_row': 'element0',
            'field_id': '3',
            'row': 0,
            'visible': True,
        },
    }

    # add second line
    resp = resp.form.submit('f1$add_element')
    resp.form['f1$element1$f1'] = '3'
    live_resp = app.post(
        formdef.get_url() + 'live',
        params=resp.form.submit_fields()
        + [
            ('modified_field_id[]', '1'),
            ('modified_field_id[]', '1 1 element1'),
        ],
    )

    assert live_resp.json['result'] == {
        '1': {
            'visible': True,
        },
        '1-1-0': {
            'block_id': '1',
            'block_row': 'element0',
            'field_id': '1',
            'row': 0,
            'visible': True,
        },
        '1-1-1': {
            'block_id': '1',
            'block_row': 'element1',
            'field_id': '1',
            'row': 1,
            'visible': True,
        },
        '1-2-0': {
            'block_id': '1',
            'block_row': 'element0',
            'field_id': '2',
            'row': 0,
            'visible': True,
        },
        '1-2-1': {
            'block_id': '1',
            'block_row': 'element1',
            'field_id': '2',
            'items': [
                {
                    'id': 7,
                    'attr': 'card2 foo 7',
                    'text': 'card2 foo 7',
                },
                {
                    'id': 8,
                    'attr': 'card2 bar 8',
                    'text': 'card2 bar 8',
                },
                {
                    'id': 9,
                    'attr': 'card2 baz 9',
                    'text': 'card2 baz 9',
                },
            ],
            'row': 1,
            'visible': True,
        },
        '1-3-0': {
            'block_id': '1',
            'block_row': 'element0',
            'field_id': '3',
            'row': 0,
            'visible': True,
        },
        '1-3-1': {
            'block_id': '1',
            'block_row': 'element1',
            'field_id': '3',
            'row': 1,
            'visible': True,
        },
    }

    resp.form['f1$element1$f2'].force_value('9')

    live_resp = app.post(
        formdef.get_url() + 'live',
        params=resp.form.submit_fields()
        + [
            ('modified_field_id[]', '1'),
            ('modified_field_id[]', '1 2 element1'),
        ],
    )

    assert live_resp.json['result'] == {
        '1': {
            'visible': True,
        },
        '1-1-0': {
            'block_id': '1',
            'block_row': 'element0',
            'field_id': '1',
            'row': 0,
            'visible': True,
        },
        '1-1-1': {
            'block_id': '1',
            'block_row': 'element1',
            'field_id': '1',
            'row': 1,
            'visible': True,
        },
        '1-2-0': {
            'block_id': '1',
            'block_row': 'element0',
            'field_id': '2',
            'row': 0,
            'visible': True,
        },
        '1-2-1': {
            'block_id': '1',
            'block_row': 'element1',
            'field_id': '2',
            'row': 1,
            'visible': True,
        },
        '1-3-0': {
            'block_id': '1',
            'block_row': 'element0',
            'field_id': '3',
            'row': 0,
            'visible': True,
        },
        '1-3-1': {
            'block_id': '1',
            'block_row': 'element1',
            'field_id': '3',
            'items': [
                {
                    'id': 25,
                    'attr': 'card3 foo 25',
                    'text': 'card3 foo 25',
                },
                {
                    'id': 26,
                    'attr': 'card3 bar 26',
                    'text': 'card3 bar 26',
                },
                {
                    'id': 27,
                    'attr': 'card3 baz 27',
                    'text': 'card3 baz 27',
                },
            ],
            'row': 1,
            'visible': True,
        },
    }

    # simulate js, add relevant checkboxes
    for option in live_resp.json['result']['1-3-1']['items']:
        checkbox_name = 'f1$element1$f3$element%s' % option['id']
        widget = Checkbox(form=resp.form, name=checkbox_name, tag='input', value='yes', pos=10)
        resp.form.fields[checkbox_name] = [widget]
        resp.form.field_order.append((checkbox_name, widget))

    resp.form['f1$element1$f3$element27'].checked = True

    resp = resp.form.submit('submit')  # -> validation

    assert not resp.pyquery('.widget-with-error')
    resp = resp.form.submit('submit')  # -> submit
    assert formdef.data_class().select()[0].data == {
        '1': {
            'data': [
                {
                    '1': '1',
                    '1_display': 'card1 foo 0',
                    '1_structured': {
                        'attr': 'card1 foo 0',
                        'id': 1,
                        'text': 'card1 foo 0',
                    },
                    '2': '1',
                    '2_display': 'card2 foo 1',
                    '2_structured': {
                        'attr': 'card2 foo 1',
                        'id': 1,
                        'text': 'card2 foo 1',
                    },
                    '3': [
                        '1',
                    ],
                    '3_display': 'card3 foo 1',
                    '3_structured': [
                        {
                            'attr': 'card3 foo 1',
                            'id': 1,
                            'text': 'card3 foo 1',
                        }
                    ],
                },
                {
                    '1': '3',
                    '1_display': 'card1 baz 2',
                    '1_structured': {
                        'attr': 'card1 baz 2',
                        'id': 3,
                        'text': 'card1 baz 2',
                    },
                    '2': '9',
                    '2_display': 'card2 baz 9',
                    '2_structured': {
                        'attr': 'card2 baz 9',
                        'id': 9,
                        'text': 'card2 baz 9',
                    },
                    '3': [
                        '27',
                    ],
                    '3_display': 'card3 baz 27',
                    '3_structured': [
                        {
                            'attr': 'card3 baz 27',
                            'id': 27,
                            'text': 'card3 baz 27',
                        }
                    ],
                },
            ],
            'schema': {
                '1': 'item',
                '2': 'item',
                '3': 'items',
            },
        },
        '1_display': 'foobar, foobar',
    }


def test_block_with_block_field_live_item_items_multilevel_json(pub, http_requests):
    BlockDef.wipe()
    CardDef.wipe()
    FormDef.wipe()
    NamedDataSource.wipe()

    # Three level of JSON ds
    ds1 = NamedDataSource(name='ds1')
    ds1.data_source = {'type': 'json', 'value': 'http://remote.example.net/json-with-filter?parent=0'}
    ds1.id_parameter = 'id'
    ds1.store()

    ds2 = NamedDataSource(name='ds2')
    ds2.data_source = {
        'type': 'json',
        'value': '{% if block_var_ds1_raw %}http://remote.example.net/json-with-filter?parent={{ block_var_ds1_raw }}{% endif %}',
    }
    ds2.id_parameter = 'id'
    ds2.store()

    ds3 = NamedDataSource(name='ds3')
    ds3.data_source = {
        'type': 'json',
        'value': '{% if block_var_ds2_raw %}http://remote.example.net/json-with-filter?parent={{ block_var_ds2_raw }}{% endif %}',
    }
    ds3.id_parameter = 'id'
    ds3.store()

    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.ItemField(id='1', varname='ds1', label='ds1', data_source={'type': 'ds1'}),
        fields.ItemField(
            id='2',
            label='ds2',
            varname='ds2',
            display_mode='radio',
            data_source={'type': 'ds2'},
        ),
        fields.ItemsField(id='3', varname='ds3', label='ds3', data_source={'type': 'ds3'}),
    ]
    block.store()

    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [fields.BlockField(id='1', label='test', block_slug='foobar', max_items='3')]
    formdef.store()
    formdef.data_class().wipe()

    # filling
    app = get_app(pub)
    resp = app.get(formdef.get_url())
    assert resp.pyquery('[data-widget-name="f1$element0$f1"]').attr['data-live-source'] == 'true'
    resp.form['f1$element0$f1'] = '1'
    live_resp = app.post(
        formdef.get_url() + 'live',
        params=resp.form.submit_fields()
        + [
            ('modified_field_id[]', '1'),
            ('modified_field_id[]', '1 1 element0'),
        ],
    )
    assert live_resp.json['result'] == {
        '1': {
            'visible': True,
        },
        '1-1-0': {
            'block_id': '1',
            'block_row': 'element0',
            'field_id': '1',
            'row': 0,
            'visible': True,
        },
        '1-2-0': {
            'block_id': '1',
            'block_row': 'element0',
            'field_id': '2',
            'items': [
                {
                    'id': '4',
                    'parent': '1',
                    'text': 'foo',
                },
                {
                    'id': '5',
                    'parent': '1',
                    'text': 'bar',
                },
                {
                    'id': '6',
                    'parent': '1',
                    'text': 'baz',
                },
            ],
            'row': 0,
            'visible': True,
        },
        '1-3-0': {
            'block_id': '1',
            'block_row': 'element0',
            'field_id': '3',
            'row': 0,
            'visible': True,
        },
    }

    resp.form['f1$element0$f2'].force_value('4')

    live_resp = app.post(
        formdef.get_url() + 'live',
        params=resp.form.submit_fields()
        + [
            ('modified_field_id[]', '1'),
            ('modified_field_id[]', '1 2 element0'),
        ],
    )

    assert live_resp.json['result'] == {
        '1': {
            'visible': True,
        },
        '1-1-0': {
            'block_id': '1',
            'block_row': 'element0',
            'field_id': '1',
            'row': 0,
            'visible': True,
        },
        '1-2-0': {
            'block_id': '1',
            'block_row': 'element0',
            'field_id': '2',
            'row': 0,
            'visible': True,
        },
        '1-3-0': {
            'block_id': '1',
            'block_row': 'element0',
            'field_id': '3',
            'items': [
                {
                    'id': '13',
                    'parent': '4',
                    'text': 'foo',
                },
                {
                    'id': '14',
                    'parent': '4',
                    'text': 'bar',
                },
                {
                    'id': '15',
                    'parent': '4',
                    'text': 'baz',
                },
            ],
            'row': 0,
            'visible': True,
        },
    }

    # simulate js, add relevant checkboxes
    for option in live_resp.json['result']['1-3-0']['items']:
        checkbox_name = 'f1$element0$f3$element%s' % option['id']
        widget = Checkbox(form=resp.form, name=checkbox_name, tag='input', value='yes', pos=10)
        resp.form.fields[checkbox_name] = [widget]
        resp.form.field_order.append((checkbox_name, widget))

    resp.form['f1$element0$f3$element14'].checked = True

    live_resp = app.post(
        formdef.get_url() + 'live',
        params=resp.form.submit_fields()
        + [
            ('modified_field_id[]', '1'),
            ('modified_field_id[]', '1 3 element0'),
        ],
    )

    assert live_resp.json['result'] == {
        '1': {
            'visible': True,
        },
        '1-1-0': {
            'block_id': '1',
            'block_row': 'element0',
            'field_id': '1',
            'row': 0,
            'visible': True,
        },
        '1-2-0': {
            'block_id': '1',
            'block_row': 'element0',
            'field_id': '2',
            'row': 0,
            'visible': True,
        },
        '1-3-0': {
            'block_id': '1',
            'block_row': 'element0',
            'field_id': '3',
            'row': 0,
            'visible': True,
        },
    }

    # add second line
    resp = resp.form.submit('f1$add_element')
    resp.form['f1$element1$f1'] = '3'
    live_resp = app.post(
        formdef.get_url() + 'live',
        params=resp.form.submit_fields()
        + [
            ('modified_field_id[]', '1'),
            ('modified_field_id[]', '1 1 element1'),
        ],
    )

    assert live_resp.json['result'] == {
        '1': {
            'visible': True,
        },
        '1-1-0': {
            'block_id': '1',
            'block_row': 'element0',
            'field_id': '1',
            'row': 0,
            'visible': True,
        },
        '1-1-1': {
            'block_id': '1',
            'block_row': 'element1',
            'field_id': '1',
            'row': 1,
            'visible': True,
        },
        '1-2-0': {
            'block_id': '1',
            'block_row': 'element0',
            'field_id': '2',
            'row': 0,
            'visible': True,
        },
        '1-2-1': {
            'block_id': '1',
            'block_row': 'element1',
            'field_id': '2',
            'items': [
                {
                    'id': '10',
                    'parent': '3',
                    'text': 'foo',
                },
                {
                    'id': '11',
                    'parent': '3',
                    'text': 'bar',
                },
                {
                    'id': '12',
                    'parent': '3',
                    'text': 'baz',
                },
            ],
            'row': 1,
            'visible': True,
        },
        '1-3-0': {
            'block_id': '1',
            'block_row': 'element0',
            'field_id': '3',
            'row': 0,
            'visible': True,
        },
        '1-3-1': {
            'block_id': '1',
            'block_row': 'element1',
            'field_id': '3',
            'row': 1,
            'visible': True,
        },
    }

    resp.form['f1$element1$f2'].force_value('12')

    live_resp = app.post(
        formdef.get_url() + 'live',
        params=resp.form.submit_fields()
        + [
            ('modified_field_id[]', '1'),
            ('modified_field_id[]', '1 2 element1'),
        ],
    )

    assert live_resp.json['result'] == {
        '1': {
            'visible': True,
        },
        '1-1-0': {
            'block_id': '1',
            'block_row': 'element0',
            'field_id': '1',
            'row': 0,
            'visible': True,
        },
        '1-1-1': {
            'block_id': '1',
            'block_row': 'element1',
            'field_id': '1',
            'row': 1,
            'visible': True,
        },
        '1-2-0': {
            'block_id': '1',
            'block_row': 'element0',
            'field_id': '2',
            'row': 0,
            'visible': True,
        },
        '1-2-1': {
            'block_id': '1',
            'block_row': 'element1',
            'field_id': '2',
            'row': 1,
            'visible': True,
        },
        '1-3-0': {
            'block_id': '1',
            'block_row': 'element0',
            'field_id': '3',
            'row': 0,
            'visible': True,
        },
        '1-3-1': {
            'block_id': '1',
            'block_row': 'element1',
            'field_id': '3',
            'items': [
                {
                    'id': '37',
                    'parent': '12',
                    'text': 'foo',
                },
                {
                    'id': '38',
                    'parent': '12',
                    'text': 'bar',
                },
                {
                    'id': '39',
                    'parent': '12',
                    'text': 'baz',
                },
            ],
            'row': 1,
            'visible': True,
        },
    }

    # simulate js, add relevant checkboxes
    for option in live_resp.json['result']['1-3-1']['items']:
        checkbox_name = 'f1$element1$f3$element%s' % option['id']
        widget = Checkbox(form=resp.form, name=checkbox_name, tag='input', value='yes', pos=10)
        resp.form.fields[checkbox_name] = [widget]
        resp.form.field_order.append((checkbox_name, widget))

    resp.form['f1$element1$f3$element39'].checked = True

    resp = resp.form.submit('submit')  # -> validation

    assert not resp.pyquery('.widget-with-error')
    resp = resp.form.submit('submit')  # -> submit
    assert formdef.data_class().select()[0].data == {
        '1': {
            'data': [
                {
                    '1': '1',
                    '1_display': 'foo',
                    '1_structured': {
                        'id': '1',
                        'text': 'foo',
                        'parent': '0',
                    },
                    '2': '4',
                    '2_display': 'foo',
                    '2_structured': {
                        'id': '4',
                        'text': 'foo',
                        'parent': '1',
                    },
                    '3': [
                        '14',
                    ],
                    '3_display': 'bar',
                    '3_structured': [
                        {
                            'id': '14',
                            'text': 'bar',
                            'parent': '4',
                        }
                    ],
                },
                {
                    '1': '3',
                    '1_display': 'baz',
                    '1_structured': {
                        'id': '3',
                        'text': 'baz',
                        'parent': '0',
                    },
                    '2': '12',
                    '2_display': 'baz',
                    '2_structured': {
                        'id': '12',
                        'text': 'baz',
                        'parent': '3',
                    },
                    '3': [
                        '39',
                    ],
                    '3_display': 'baz',
                    '3_structured': [
                        {
                            'id': '39',
                            'text': 'baz',
                            'parent': '12',
                        }
                    ],
                },
            ],
            'schema': {
                '1': 'item',
                '2': 'item',
                '3': 'items',
            },
        },
        '1_display': 'foobar, foobar',
    }


def test_block_field_inspect_keys(pub):
    create_user(pub)

    BlockDef.wipe()
    block = BlockDef()
    block.name = 'foobar'
    block.fields = [fields.StringField(id='123', required='required', label='Test', varname='sub')]
    block.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [
        fields.BlockField(id='1', label='test', block_slug='foobar', varname='test', max_items='1'),
    ]
    formdef.store()
    formdef.data_class().wipe()

    formdata = formdef.data_class()()
    formdata.data = {'1': {'data': [{'123': 'plop'}], 'schema': {'123': 'string'}}}
    formdata.store()

    substvars = CompatibilityNamesDict()
    substvars.update(formdata.get_substitution_variables())
    assert 'form_var_test_var_sub' in substvars.get_flat_keys()
    assert substvars['form_var_test_var_sub'] == 'plop'

    formdef.fields[0].max_items = '{{ count }}'
    assert 'form_var_test_var_sub' not in substvars.get_flat_keys()
    assert 'form_var_test_0_sub' in substvars.get_flat_keys()
    assert substvars['form_var_test_0_sub'] == 'plop'

    formdef.fields[0].max_items = '5'
    assert 'form_var_test_var_sub' not in substvars.get_flat_keys()
    assert 'form_var_test_0_sub' in substvars.get_flat_keys()
    assert substvars['form_var_test_0_sub'] == 'plop'
