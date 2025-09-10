import datetime
import decimal

import pytest
import responses
from django.utils.timezone import make_aware
from pyquery import PyQuery
from webtest import Upload

from wcs import fields
from wcs.blocks import BlockDef
from wcs.carddef import CardDef
from wcs.formdef import FormDef
from wcs.logged_errors import LoggedError
from wcs.qommon.substitution import CompatibilityNamesDict
from wcs.workflows import Workflow
from wcs.wscalls import NamedWsCall

from ..utilities import clean_temporary_pub, create_temporary_pub, get_app, login
from .test_all import create_user


@pytest.fixture
def pub(emails):
    pub = create_temporary_pub()
    pub.cfg['identification'] = {'methods': ['password']}
    pub.cfg['language'] = {'language': 'en'}
    pub.write_cfg()

    return pub


def teardown_module(module):
    clean_temporary_pub()


def test_computed_field_simple(pub):
    create_user(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [
        fields.ComputedField(id='1', label='computed', varname='computed', value_template='{{ "xxx" }}'),
    ]
    formdef.store()
    formdef.data_class().wipe()

    resp = login(get_app(pub), username='foo', password='foo').get('/test/')
    resp = resp.forms[0].submit('submit')  # -> validation
    resp = resp.forms[0].submit('submit').follow()  # -> submit
    assert 'The form has been recorded' in resp.text
    assert formdef.data_class().count() == 1
    formdata = formdef.data_class().select()[0]
    assert formdata.data == {'1': 'xxx'}


def test_computed_field_used_in_prefill(pub):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [
        fields.ComputedField(id='1', label='computed', varname='computed', value_template='xxx'),
        fields.StringField(
            id='2', label='string', prefill={'type': 'string', 'value': '{{ form_var_computed }}'}
        ),
    ]
    formdef.store()
    formdef.data_class().wipe()

    resp = get_app(pub).get('/test/')
    assert resp.forms[0]['f2'].value == 'xxx'
    resp = resp.forms[0].submit('submit')  # -> validation
    resp = resp.forms[0].submit('submit').follow()  # -> submit
    assert 'The form has been recorded' in resp.text
    assert formdef.data_class().count() == 1
    formdata = formdef.data_class().select()[0]
    assert formdata.data == {'1': 'xxx', '2': 'xxx'}


def test_computed_field_used_in_comment(pub):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [
        fields.ComputedField(id='1', label='computed', varname='computed', value_template='xxx'),
        fields.CommentField(id='2', label='X{{ form_var_computed }}Y'),
    ]
    formdef.store()
    formdef.data_class().wipe()

    resp = get_app(pub).get('/test/')
    assert 'XxxxY' in resp.text
    resp = resp.forms[0].submit('submit')  # -> validation
    resp = resp.forms[0].submit('submit').follow()  # -> submit
    assert 'The form has been recorded' in resp.text
    assert formdef.data_class().count() == 1
    formdata = formdef.data_class().select()[0]
    assert formdata.data == {'1': 'xxx'}


def test_computed_field_freeze(pub, freezer):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [
        fields.ComputedField(
            id='1',
            label='computed',
            varname='computed',
            value_template='{% now "H:i" %}',
            freeze_on_initial_value=False,
        ),
    ]
    formdef.store()
    formdef.data_class().wipe()

    freezer.move_to(make_aware(datetime.datetime(2021, 4, 6, 10, 0)))
    resp = get_app(pub).get('/test/')
    freezer.move_to(make_aware(datetime.datetime(2021, 4, 6, 10, 5)))
    resp = resp.forms[0].submit('submit')  # -> validation
    resp = resp.forms[0].submit('submit').follow()  # -> submit
    assert 'The form has been recorded' in resp.text
    assert formdef.data_class().count() == 1
    formdata = formdef.data_class().select()[0]
    assert formdata.data == {'1': '10:05'}

    formdef.data_class().wipe()
    formdef.fields[0].freeze_on_initial_value = True
    formdef.store()

    freezer.move_to(make_aware(datetime.datetime(2021, 4, 6, 10, 0)))
    resp = get_app(pub).get('/test/')
    freezer.move_to(make_aware(datetime.datetime(2021, 4, 6, 10, 5)))
    resp = resp.forms[0].submit('submit')  # -> validation
    resp = resp.forms[0].submit('submit').follow()  # -> submit
    assert 'The form has been recorded' in resp.text
    assert formdef.data_class().count() == 1
    formdata = formdef.data_class().select()[0]
    assert formdata.data == {'1': '10:00'}


def test_computed_field_from_request_get(pub):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [
        fields.ComputedField(
            id='1',
            label='computed',
            varname='computed',
            value_template='{{ request.GET.param }}',
            freeze_on_initial_value=True,
        ),
    ]
    formdef.store()
    formdef.data_class().wipe()

    resp = get_app(pub).get('/test/?param=value')
    resp = resp.forms[0].submit('submit')  # -> validation
    resp = resp.forms[0].submit('submit').follow()  # -> submit
    assert 'The form has been recorded' in resp.text
    assert formdef.data_class().count() == 1
    formdata = formdef.data_class().select()[0]
    assert formdata.data == {'1': 'value'}


def test_computed_field_usage_in_post_condition(pub):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [
        fields.PageField(
            id='0',
            label='1st page',
            post_conditions=[
                {
                    'condition': {
                        'type': 'django',
                        'value': 'form_var_computed == "xxx"',
                    },
                    'error_message': 'You shall not pass.',
                }
            ],
        ),
        fields.ComputedField(
            id='1',
            label='computed',
            varname='computed',
            value_template='{{ request.GET.param }}',
            freeze_on_initial_value=True,
        ),
    ]
    formdef.store()
    formdef.data_class().wipe()

    resp = get_app(pub).get('/test/?param=test')
    resp = resp.forms[0].submit('submit')  # -> validation
    assert 'You shall not pass.' in resp.text
    resp = get_app(pub).get('/test/?param=xxx')
    resp = resp.forms[0].submit('submit')  # -> validation
    resp = resp.forms[0].submit('submit').follow()  # -> submit
    assert 'The form has been recorded' in resp.text

    # change condition to test |is_empty
    formdef.fields[0].post_conditions[0]['condition']['value'] = 'not form_var_computed|is_empty'
    formdef.store()

    resp = get_app(pub).get('/test/')
    resp = resp.forms[0].submit('submit')  # -> validation
    assert 'You shall not pass.' in resp.text

    resp = get_app(pub).get('/test/?param=test')
    resp = resp.forms[0].submit('submit')  # -> validation
    assert 'You shall not pass.' not in resp.text


def test_computed_field_usage_updated_in_post_condition(pub):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [
        fields.PageField(
            id='0',
            label='1st page',
            post_conditions=[
                {
                    'condition': {
                        'type': 'django',
                        'value': 'form_var_computed == "xxx"',
                    },
                    'error_message': 'You shall not pass.',
                }
            ],
        ),
        fields.ComputedField(
            id='1',
            label='computed',
            varname='computed',
            value_template='{{ form_var_field }}',
        ),
        fields.StringField(id='2', label='string', varname='field'),
    ]
    formdef.store()
    formdef.data_class().wipe()

    resp = get_app(pub).get('/test/')
    resp.forms[0]['f2'].value = 'test'
    resp = resp.forms[0].submit('submit')  # -> validation
    assert 'You shall not pass.' in resp.text
    resp.forms[0]['f2'].value = 'xxx'
    resp = resp.forms[0].submit('submit')  # -> validation
    resp = resp.forms[0].submit('submit').follow()  # -> submit
    assert 'The form has been recorded' in resp.text


def test_computed_field_recall_draft(pub):
    create_user(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [
        fields.ComputedField(
            id='1',
            label='computed',
            varname='computed',
            value_template='{{ request.GET.param }}',
            freeze_on_initial_value=True,
        ),
    ]
    formdef.store()
    formdef.data_class().wipe()

    resp = login(get_app(pub), username='foo', password='foo').get('/test/?param=value')
    resp = resp.forms[0].submit('submit')  # -> validation
    assert formdef.data_class().count() == 1
    formdata = formdef.data_class().select()[0]
    assert formdata.is_draft()

    # recall draft
    resp = login(get_app(pub), username='foo', password='foo').get(formdata.get_url()).follow()
    assert 'form-validation' in resp.text
    resp = resp.forms[1].submit('submit').follow()  # -> submit
    assert 'The form has been recorded' in resp.text
    assert formdef.data_class().count() == 1
    formdata = formdef.data_class().select()[0]
    assert formdata.data == {'1': 'value'}

    # retry, moving back to first page
    formdef.data_class().wipe()
    resp = login(get_app(pub), username='foo', password='foo').get('/test/?param=value')
    resp = resp.forms[0].submit('submit')  # -> validation
    assert formdef.data_class().count() == 1
    formdata = formdef.data_class().select()[0]
    assert formdata.is_draft()

    resp = login(get_app(pub), username='foo', password='foo').get(formdata.get_url()).follow()
    assert 'form-validation' in resp.text
    resp = resp.forms[1].submit('previous')  # -> first page
    resp = resp.forms[1].submit('submit')  # -> validation
    resp = resp.forms[1].submit('submit').follow()  # -> submit
    assert 'The form has been recorded' in resp.text
    assert formdef.data_class().count() == 1
    formdata = formdef.data_class().select()[0]
    assert formdata.data == {'1': 'value'}


@pytest.mark.parametrize('http_method', ['get', 'post'])
def test_computed_field_complex_data(pub, http_method):
    FormDef.wipe()
    NamedWsCall.wipe()

    wscall = NamedWsCall()
    wscall.name = 'Hello world'
    wscall.request = {'url': 'http://remote.example.net/json', 'method': http_method.upper()}
    wscall.store()

    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [
        fields.ComputedField(
            id='1',
            label='computed',
            varname='computed',
            value_template='{{ webservice.hello_world }}',
            freeze_on_initial_value=True,
        ),
        fields.CommentField(id='2', label='X{{form_var_computed_foo}}Y'),
    ]
    formdef.store()
    formdef.data_class().wipe()

    # check with a dictionary as response
    with responses.RequestsMock() as rsps:
        getattr(rsps, http_method)('http://remote.example.net/json', json={'foo': 'bar'})
        resp = get_app(pub).get('/test/')
        assert 'XbarY' in resp.text
        resp = resp.forms[0].submit('submit')  # -> validation
        resp = resp.forms[0].submit('submit').follow()  # -> submit
        assert 'The form has been recorded' in resp.text
        assert formdef.data_class().count() == 1
        formdata = formdef.data_class().select()[0]
        assert formdata.data['1'] == {'foo': 'bar'}

    # check with a list as response
    formdef.data_class().wipe()
    with responses.RequestsMock() as rsps:
        formdef.fields[1].label = 'X{{form_var_computed_1_foo}}Y'
        formdef.store()
        getattr(rsps, http_method)('http://remote.example.net/json', json=[{'foo': 'xxx'}, {'foo': 'bar'}])
        resp = get_app(pub).get('/test/')
        assert 'XbarY' in resp.text
        resp = resp.forms[0].submit('submit')  # -> validation
        resp = resp.forms[0].submit('submit').follow()  # -> submit
        assert 'The form has been recorded' in resp.text
        assert formdef.data_class().count() == 1
        formdata = formdef.data_class().select()[0]
        assert formdata.data['1'] == [{'foo': 'xxx'}, {'foo': 'bar'}]

    # check with the computed field extracting a list from the response
    formdef.data_class().wipe()
    with responses.RequestsMock() as rsps:
        formdef.fields[0].value_template = '{{ webservice.hello_world|get:"data" }}'
        formdef.fields[1].label = 'X{{form_var_computed_1_foo}}Y'
        formdef.store()
        getattr(rsps, http_method)(
            'http://remote.example.net/json', json={'data': [{'foo': 'xxx'}, {'foo': 'bar'}]}
        )
        resp = get_app(pub).get('/test/')
        assert 'XbarY' in resp.text
        resp = resp.forms[0].submit('submit')  # -> validation
        resp = resp.forms[0].submit('submit').follow()  # -> submit
        assert 'The form has been recorded' in resp.text
        assert formdef.data_class().count() == 1
        formdata = formdef.data_class().select()[0]
        assert formdata.data['1'] == [{'foo': 'xxx'}, {'foo': 'bar'}]

    if http_method == 'post':
        # check with (complex) post data
        formdef.data_class().wipe()
        with responses.RequestsMock() as rsps:
            wscall.request['post_data'] = {'test': '{{ "test"|qrcode }}'}
            wscall.store()
            formdef.fields[0].value_template = '{{ webservice.hello_world|get:"data" }}'
            formdef.fields[1].label = 'X{{form_var_computed_1_foo}}Y'
            formdef.store()
            getattr(rsps, http_method)('http://remote.example.net/json', json={'data': {'1': {'foo': 'bar'}}})
            resp = get_app(pub).get('/test/')
            assert 'XbarY' in resp.text
            resp = resp.forms[0].submit('submit')  # -> validation
            resp = resp.forms[0].submit('submit').follow()  # -> submit
            assert 'The form has been recorded' in resp.text
            assert formdef.data_class().count() == 1
            formdata = formdef.data_class().select()[0]
            assert formdata.data['1'] == {'1': {'foo': 'bar'}}


def test_computed_field_decimal_data(pub, http_requests):
    FormDef.wipe()

    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [
        fields.ComputedField(
            id='1',
            label='computed',
            varname='computed',
            value_template='{{ "123.45"|decimal }}',
            freeze_on_initial_value=True,
        ),
        fields.CommentField(id='2', label='X{{form_var_computed}}Y'),
    ]
    formdef.store()
    formdef.data_class().wipe()

    resp = get_app(pub).get('/test/')
    assert 'X123.45Y' in resp.text
    resp = resp.forms[0].submit('submit')  # -> validation
    resp = resp.forms[0].submit('submit').follow()  # -> submit
    assert 'The form has been recorded' in resp.text
    assert formdef.data_class().count() == 1
    formdata = formdef.data_class().select()[0]
    # pickle storage keeps the typed data but postgresql storage has to convert
    # it to stringfor json storage, hence the casting here:
    assert decimal.Decimal(formdata.data['1']) == decimal.Decimal('123.45')


def test_computed_field_set_data(pub):
    create_user(pub)
    FormDef.wipe()

    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [
        fields.ComputedField(
            id='1',
            label='computed',
            varname='computed',
            value_template='{{ "ab,cd,cd,ef"|split:","|set }}',
            freeze_on_initial_value=True,
        ),
        fields.StringField(
            id='2',
            label='string',
            prefill={'type': 'string', 'value': '{% if "ab" in form_var_computed %}hello{% endif %}'},
        ),
    ]
    formdef.store()
    formdef.data_class().wipe()

    app = login(get_app(pub), username='foo', password='foo')
    resp = app.get('/test/')
    assert app.post('/test/autosave', params=resp.form.submit_fields()).json == {'result': 'success'}
    assert resp.forms[0]['f2'].value == 'hello'
    resp = resp.forms[0].submit('submit')  # -> validation
    resp = resp.forms[0].submit('submit').follow()  # -> submit
    assert 'The form has been recorded' in resp.text
    assert formdef.data_class().count() == 1
    formdata = formdef.data_class().select()[0]
    assert set(formdata.data['1']) == {'ab', 'cd', 'ef'}


def test_computed_field_usage_in_live_data(pub, http_requests):
    FormDef.wipe()

    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [
        fields.ComputedField(
            id='1',
            label='computed',
            varname='computed',
            value_template='{{ "xxx" }}',
        ),
        fields.StringField(id='0', label='string', varname='string'),
        fields.CommentField(id='2', label='X{{form_var_computed}}Y{{form_var_string}}Z'),
    ]
    formdef.store()
    formdef.data_class().wipe()

    app = get_app(pub)
    resp = app.get('/test/')
    assert 'XxxxYNoneZ' in resp.text
    resp.form['f0'] = 'hello'
    live_resp = app.post('/test/live', params=resp.form.submit_fields())
    assert live_resp.json['result']['2']['content'] == '<p>XxxxYhelloZ</p>'


def test_computed_field_inspect_keys(pub):
    create_user(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [
        fields.ComputedField(id='1', label='computed', varname='computed', value_template='{{ "xxx" }}'),
    ]
    formdef.store()
    formdef.data_class().wipe()

    formdata = formdef.data_class()()
    formdata.data = {'1': 'xxx'}

    substvars = CompatibilityNamesDict()
    substvars.update(formdata.get_substitution_variables())
    assert 'form_var_computed' in substvars.get_flat_keys()
    assert substvars['form_var_computed'] == 'xxx'

    formdata.data = {'1': {'foo': 'bar'}}
    assert 'form_var_computed_foo' in substvars.get_flat_keys()
    assert substvars['form_var_computed_foo'] == 'bar'


def test_computed_field_edit_action(pub):
    create_user(pub)

    workflow = Workflow.get_default_workflow()
    workflow.id = '2'

    st2 = workflow.get_status('new')
    editable = st2.add_action('editable', id='_editable')
    editable.id = '_editable'
    editable.by = ['_submitter']
    workflow.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [
        fields.StringField(id='0', label='field', varname='plop'),
        fields.ComputedField(
            id='1', label='computed', varname='computed', value_template='{{ form_var_plop }}'
        ),
    ]
    formdef.workflow_id = workflow.id
    formdef.store()
    formdef.data_class().wipe()

    resp = login(get_app(pub), username='foo', password='foo').get('/test/')
    resp.forms[0]['f0'] = 'foobar'
    resp = resp.forms[0].submit('submit')  # -> validation
    resp = resp.forms[0].submit('submit').follow()  # -> submit
    assert 'The form has been recorded' in resp.text
    assert formdef.data_class().count() == 1
    formdata = formdef.data_class().select()[0]
    assert formdata.data == {'0': 'foobar', '1': 'foobar'}

    resp = resp.forms[0].submit('button_editable')
    resp = resp.follow()
    resp.forms[0]['f0'] = 'bar'
    resp = resp.forms[0].submit('submit')
    formdata.refresh_from_storage()
    assert formdata.data == {'0': 'bar', '1': 'bar'}

    # freeze value
    formdef.data_class().wipe()
    formdef.fields[1].value_template = '{{ request.GET.foo }}'
    formdef.fields[1].freeze_on_initial_value = True
    formdef.store()

    resp = login(get_app(pub), username='foo', password='foo').get('/test/?foo=PLOP')
    resp.forms[0]['f0'] = 'foobar'
    resp = resp.forms[0].submit('submit')  # -> validation
    resp = resp.forms[0].submit('submit').follow()  # -> submit
    assert 'The form has been recorded' in resp.text
    assert formdef.data_class().count() == 1
    formdata = formdef.data_class().select()[0]
    assert formdata.data == {'0': 'foobar', '1': 'PLOP'}

    resp = resp.forms[0].submit('button_editable')
    resp = resp.follow()
    resp.forms[0]['f0'] = 'bar'
    resp = resp.forms[0].submit('submit')
    formdata.refresh_from_storage()
    assert formdata.data == {'0': 'bar', '1': 'PLOP'}


def test_cascading_computed_fields(pub):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [
        fields.PageField(
            id='0',
            label='1st page',
            post_conditions=[
                {
                    'condition': {
                        'type': 'django',
                        'value': 'form_var_dc == "0abc"',
                    },
                    'error_message': 'You shall not pass.',
                }
            ],
        ),
        fields.ComputedField(id='1', label='computed', varname='a', value_template='a'),
        fields.ComputedField(id='2', label='computed', varname='b', value_template='{{form_var_a}}b'),
        fields.ComputedField(id='3', label='computed', varname='c', value_template='{{form_var_b}}c'),
        fields.CommentField(id='4', label='X{{ form_var_c }}Y'),
        fields.StringField(id='5', label='string', varname='d'),
        fields.ComputedField(id='6', label='computed', varname='da', value_template='{{form_var_d}}a'),
        fields.ComputedField(id='7', label='computed', varname='db', value_template='{{form_var_da}}b'),
        fields.ComputedField(id='8', label='computed', varname='dc', value_template='{{form_var_db}}c'),
    ]
    formdef.store()
    formdef.data_class().wipe()

    resp = get_app(pub).get('/test/')
    assert 'XabcY' in resp.text  # check comment field has the correct value

    # this should produce form_var_dc == "Xabc", and not pass the post condition
    resp.forms[0]['f5'].value = 'X'
    resp = resp.forms[0].submit('submit')  # -> validation
    assert 'You shall not pass.' in resp.text

    # this should produce form_var_dc == "0abc", and be ok for the post condition
    resp = get_app(pub).get('/test/')
    resp.forms[0]['f5'].value = '0'
    resp = resp.forms[0].submit('submit')  # -> validation
    assert 'You shall not pass.' not in resp.text


def test_computed_field_usage_in_criteria(pub):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [
        fields.PageField(
            id='0',
            label='1st page',
        ),
        fields.ComputedField(
            id='1',
            label='computed',
            varname='computed',
            value_template='{{ request.GET.param }}',
            freeze_on_initial_value=True,
        ),
        fields.PageField(
            id='2',
            label='2nd page',
        ),
        fields.CommentField(
            id='3',
            label='<p>count with this value: '
            '{{form_objects|exclude_self|filter_by:"computed"|filter_value:form_var_computed|count}}</p>',
        ),
    ]
    formdef.store()
    formdef.data_class().wipe()

    resp = get_app(pub).get('/test/?param=test')
    resp = resp.forms[0].submit('submit')  # -> 2nd page
    assert 'count with this value: 0' in resp.text
    resp = resp.forms[0].submit('submit')  # -> validation
    resp = resp.forms[0].submit('submit').follow()  # -> submit
    assert 'The form has been recorded' in resp.text

    resp = get_app(pub).get('/test/?param=test')
    resp = resp.forms[0].submit('submit')  # -> 2nd page
    assert 'count with this value: 1' in resp.text
    resp = resp.forms[0].submit('submit')  # -> validation
    resp = resp.forms[0].submit('submit').follow()  # -> submit
    assert 'The form has been recorded' in resp.text

    resp = get_app(pub).get('/test/?param=plop')
    resp = resp.forms[0].submit('submit')  # -> 2nd page
    assert 'count with this value: 0' in resp.text


def test_computed_field_with_data_source(pub):
    CardDef.wipe()
    FormDef.wipe()

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

    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [
        fields.ComputedField(
            id='1',
            label='computed',
            varname='computed',
            value_template='{{ request.GET.param }}',
            freeze_on_initial_value=True,
            data_source=ds,
        ),
        fields.ComputedField(
            id='2', label='computed2', varname='b', value_template='B{{form_var_computed_live_var_name}}B'
        ),
        fields.CommentField(id='3', label='X{{ form_var_computed_live_var_name }}Y'),
        fields.CommentField(id='4', label='X{{ form_var_b }}Y'),
    ]
    formdef.store()
    formdef.data_class().wipe()

    resp = get_app(pub).get('/test/?param=%s' % carddata.id)
    assert 'XbazY' in resp.text
    assert 'XBbazBY' in resp.text

    LoggedError.wipe()
    resp = get_app(pub).get('/test/?param=%s' % 'foo')
    assert 'XY' in resp.text
    assert 'XBBY' in resp.text
    assert LoggedError.count() == 1
    assert LoggedError.select()[0].summary == 'Invalid value "foo" for field "computed"'

    carddef.id_template = '{{form_var_name}}'
    carddef.store()
    for carddata in carddef.data_class().select():
        carddata.store()

    LoggedError.wipe()
    resp = get_app(pub).get('/test/?param=foo')
    assert 'XfooY' in resp.text
    assert 'XBfooBY' in resp.text
    assert LoggedError.count() == 0

    formdef.fields[0].data_source = {'type': 'carddef:broken'}
    formdef.store()
    LoggedError.wipe()
    resp = get_app(pub).get('/test/?param=%s' % 'foo')
    assert 'XY' in resp.text
    assert 'XBBY' in resp.text
    assert LoggedError.count() == 1
    assert LoggedError.select()[0].summary == 'Invalid data source for field "computed"'


def test_computed_field_with_bad_objects_filter_in_prefill(pub):
    LoggedError.wipe()
    CardDef.wipe()
    FormDef.wipe()

    carddef = CardDef()
    carddef.name = 'items'
    carddef.digest_templates = {'default': '{{form_var_name}}'}
    carddef.fields = [
        fields.StringField(id='0', label='string', varname='name'),
    ]
    carddef.store()
    carddata = carddef.data_class()()
    carddata.data = {
        '0': 'foo',
    }
    carddata.just_created()
    carddata.store()

    ds = {'type': 'carddef:%s' % carddef.url_name}

    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [
        fields.ComputedField(
            id='1',
            label='computed',
            varname='computed',
            value_template='{{ cards|objects:"%s"|order_by:"id"|first|get:"form_number_raw"|default:"" }}'
            % carddef.url_name,
            freeze_on_initial_value=True,
            data_source=ds,
        ),
        fields.CommentField(id='2', label='X{{ form_var_computed_live_var_name }}Y'),
    ]
    formdef.store()
    formdef.data_class().wipe()

    resp = get_app(pub).get('/test/')
    assert 'XfooY' in resp.text

    formdef.fields[0].value_template = '{{ cards|objects:"unknown"|first|get:"form_number_raw"|default:"" }}'
    formdef.store()

    LoggedError.wipe()
    resp = get_app(pub).get('/test/')
    assert 'XY' in resp.text
    assert LoggedError.count() == 1
    logged_error = LoggedError.select(order_by='id')[0]
    assert logged_error.summary == '|objects with invalid reference (\'unknown\')'
    assert logged_error.formdef_id == str(formdef.id)


def test_computed_field_with_bad_value_type_in_prefill(pub):
    LoggedError.wipe()
    CardDef.wipe()
    FormDef.wipe()

    carddef = CardDef()
    carddef.name = 'items'
    carddef.digest_templates = {'default': '{{form_var_name}}'}
    carddef.fields = [
        fields.StringField(id='0', label='string', varname='string'),
        fields.BoolField(id='1', label='bool', varname='bool'),
    ]
    carddef.store()
    carddata = carddef.data_class().wipe()
    carddata = carddef.data_class()()
    carddata.data = {
        '0': 'foo',
        '1': True,
    }
    carddata.just_created()
    carddata.store()

    ds = {'type': 'carddef:%s' % carddef.url_name}

    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [
        fields.ComputedField(
            id='1',
            label='computed',
            varname='computed',
            value_template='{{ cards|objects:"%s"|first|get:"form_number_raw"|default:"" }}'
            % carddef.url_name,
            freeze_on_initial_value=True,
            data_source=ds,
        ),
        fields.CommentField(id='2', label='X{{ form_var_computed_live_var_string }}Y'),
    ]
    formdef.store()
    formdef.data_class().wipe()

    resp = get_app(pub).get('/test/')
    assert 'XfooY' in resp.text

    formdef.fields[0].value_template = (
        '{{ cards|objects:"%s"|first|get:"form_var_string"|default:"" }}' % carddef.url_name
    )
    formdef.store()
    resp = get_app(pub).get('/test/')
    assert 'XY' in resp.text
    assert LoggedError.count() == 1
    logged_error = LoggedError.select()[0]
    assert logged_error.summary == 'Invalid value "foo" for field "computed"'
    assert logged_error.formdef_id == str(formdef.id)

    formdef.fields[0].value_template = (
        '{{ cards|objects:"%s"|first|get:"form_var_bool"|default:"" }}' % carddef.url_name
    )
    formdef.store()
    resp = get_app(pub).get('/test/')
    assert 'XY' in resp.text
    assert LoggedError.count() == 2
    logged_error = LoggedError.select(order_by='id')[1]
    assert logged_error.summary == 'Invalid value "True" for field "computed"'
    assert logged_error.formdef_id == str(formdef.id)

    for value_template in [
        '{{ cards|objects:"%s"|get:42|get:"form_number_raw" }}' % carddef.url_name,
        '{{ cards|objects:"%s"|get:42|get:"form_number_raw"|default:"" }}' % carddef.url_name,
    ]:
        formdef.fields[0].value_template = value_template
        formdef.store()
        resp = get_app(pub).get('/test/')
        assert 'XY' in resp.text
        assert LoggedError.count() == 2


def test_computed_field_set_too_long(pub):
    LoggedError.wipe()
    create_user(pub)
    FormDef.wipe()

    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [
        fields.ComputedField(
            id='1',
            label='computed',
            varname='computed',
            value_template='{% token_decimal length=200000 %}',
            freeze_on_initial_value=True,
        ),
    ]
    formdef.store()
    formdef.data_class().wipe()

    app = login(get_app(pub), username='foo', password='foo')
    resp = app.get('/test/')
    resp = resp.forms[0].submit('submit')  # -> validation
    resp = resp.forms[0].submit('submit').follow()  # -> submit
    assert 'The form has been recorded' in resp.text
    assert formdef.data_class().count() == 1
    formdata = formdef.data_class().select()[0]
    assert formdata.data['1'] is None

    assert LoggedError.count() == 1
    logged_error = LoggedError.select()[0]
    assert logged_error.summary.startswith('Value too long')


def test_computed_field_with_non_json_value(pub):
    LoggedError.wipe()
    CardDef.wipe()
    FormDef.wipe()

    carddef = CardDef()
    carddef.name = 'items'
    carddef.digest_templates = {'default': '{{form_var_name}}'}
    carddef.fields = [
        fields.StringField(id='0', label='string', varname='name'),
    ]
    carddef.store()
    carddata = carddef.data_class()()
    carddata.data = {
        '0': 'foo',
    }
    carddata.just_created()
    carddata.store()

    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [
        fields.ComputedField(
            id='1',
            label='computed',
            varname='computed',
            value_template='{{ cards|objects:"%s"|first }}' % carddef.url_name,
            freeze_on_initial_value=True,
        ),
    ]
    formdef.store()
    formdef.data_class().wipe()

    get_app(pub).get('/test/')
    assert LoggedError.count() == 1
    logged_error = LoggedError.select(order_by='id')[0]
    assert logged_error.summary.startswith('Invalid value "<wcs.variables.LazyFormData')
    assert logged_error.summary.endswith('for computed field "computed"')


def test_computed_field_with_list_value(pub):
    LoggedError.wipe()
    CardDef.wipe()
    FormDef.wipe()

    carddef = CardDef()
    carddef.name = 'items'
    carddef.digest_templates = {'default': '{{form_var_name}}'}
    carddef.fields = [
        fields.StringField(id='0', label='string', varname='name'),
    ]
    carddef.store()
    carddata = carddef.data_class()()
    carddata.data = {
        '0': 'foo',
    }
    carddata.just_created()
    carddata.store()

    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [
        fields.ComputedField(
            id='1',
            label='computed',
            varname='computed',
            value_template='{{ cards|objects:"%s"|getlist:"name" }}' % carddef.url_name,
            freeze_on_initial_value=True,
        ),
        fields.CommentField(id='2', label='X{{ form_var_computed|first }}Y'),
    ]
    formdef.store()
    formdef.data_class().wipe()

    resp = get_app(pub).get('/test/')
    assert 'XfooY' in resp.text
    assert LoggedError.count() == 0


def test_computed_field_with_block_file_value(pub):
    LoggedError.wipe()
    CardDef.wipe()
    FormDef.wipe()
    BlockDef.wipe()

    block = BlockDef()
    block.name = 'foobar'
    block.fields = [fields.FileField(id='234', required='required', label='Test')]
    block.store()

    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [
        fields.PageField(id='1', label='1st page'),
        fields.BlockField(id='2', label='test', block_slug='foobar', max_items='3', varname='block'),
        fields.PageField(id='3', label='2nd page'),
        fields.ComputedField(
            id='4',
            label='computed',
            varname='computed',
            value_template='{{ form_var_block_raw }}',
        ),
    ]
    formdef.store()
    formdef.data_class().wipe()

    resp = get_app(pub).get('/test/')
    resp.form['f2$element0$f234$file'] = Upload('test1.txt', b'foobar1', 'text/plain')
    resp = resp.form.submit('submit')  # -> 2nd page
    resp = resp.form.submit('submit')  # -> validation page
    resp = resp.form.submit('submit')  # -> submit
    assert formdef.data_class().select()[0].data['4'] is None
    logged_error = LoggedError.select(order_by='id')[0]
    assert logged_error.summary.startswith('Invalid value "{\'data\': [{\'234\': <PicklableUpload at')
    assert logged_error.summary.endswith('for computed field "computed"')


def test_computed_field_debug(pub):
    user = create_user(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [
        fields.ComputedField(id='1', label='computed', varname='computed', value_template='{{ "xxx" }}'),
    ]
    formdef.store()
    formdef.data_class().wipe()

    resp = login(get_app(pub), username='foo', password='foo').get('/test/')
    assert not resp.pyquery('.debug-information')

    user.is_admin = True
    user.store()
    resp = login(get_app(pub), username='foo', password='foo').get('/test/')
    assert resp.pyquery('.debug-information')
    assert [
        PyQuery(x).text().removesuffix(' open field page') for x in resp.pyquery('.debug-information td')
    ] == ['form_var_computed', 'xxx']

    formdef.fields[0].value_template = '{{ False }}'
    formdef.store()
    resp = login(get_app(pub), username='foo', password='foo').get('/test/')
    assert [
        PyQuery(x).text().removesuffix(' open field page') for x in resp.pyquery('.debug-information td')
    ] == ['form_var_computed', 'False']
