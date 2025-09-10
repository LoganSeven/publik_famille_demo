import json
from unittest import mock

import pytest
import responses
from quixote import cleanup

from wcs import sessions
from wcs.admin.settings import UserFieldsFormDef
from wcs.fields import StringField
from wcs.formdef import FormDef
from wcs.logged_errors import LoggedError
from wcs.qommon.http_request import HTTPRequest
from wcs.wf.sms import SendSMSWorkflowStatusItem

from ..utilities import MockSubstitutionVariables, clean_temporary_pub, create_temporary_pub


def setup_module(module):
    cleanup()


def teardown_module(module):
    clean_temporary_pub()


@pytest.fixture
def pub():
    pub = create_temporary_pub()
    pub.cfg['language'] = {'language': 'en'}
    pub.cfg['identification'] = {'methods': ['password']}
    pub.write_cfg()
    req = HTTPRequest(None, {'SERVER_NAME': 'example.net', 'SCRIPT_NAME': ''})
    req.response.filter = {}
    req._user = None
    pub._set_request(req)
    req.session = sessions.BasicSession(id=1)
    pub.set_config(req)
    return pub


def test_sms(pub, sms_mocking):
    pub.cfg['sms'] = {'sender': 'xxx', 'passerelle_url': 'http://passerelle.invalid/'}
    formdef = FormDef()
    formdef.name = 'baz'
    formdef.fields = []
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()

    item = SendSMSWorkflowStatusItem()
    item.perform(formdata)
    assert len(sms_mocking.sms) == 0

    item = SendSMSWorkflowStatusItem()
    item.to_mode = 'submitter'
    item.perform(formdata)
    assert len(sms_mocking.sms) == 0

    item.to_mode = 'other'
    # no recipients
    item.perform(formdata)
    assert len(sms_mocking.sms) == 0
    # body
    item.to = ['000']
    assert len(sms_mocking.sms) == 0
    # action configured
    item.body = 'XXX'
    item.counter_name = 'abc'
    item.perform(formdata)
    assert len(sms_mocking.sms) == 1
    assert sms_mocking.sms[0]['destinations'] == ['000']
    assert sms_mocking.sms[0]['text'] == 'XXX'
    assert sms_mocking.sms[0]['counter'] == 'abc'

    # check None as recipient is not passed to the SMS backend
    item.to = [None]
    item.perform(formdata)  # nothing
    assert len(sms_mocking.sms) == 1

    item.to = ['000', None]
    item.perform(formdata)
    assert len(sms_mocking.sms) == 2
    assert sms_mocking.sms[1]['destinations'] == ['000']
    assert sms_mocking.sms[1]['text'] == 'XXX'

    # check duplicated
    sms_mocking.empty()
    item.to = ['000', '000']
    item.perform(formdata)
    assert len(sms_mocking.sms) == 1
    assert sms_mocking.sms[0]['destinations'] == ['000']
    assert sms_mocking.sms[0]['text'] == 'XXX'

    sms_mocking.empty()
    pub.substitutions.feed(MockSubstitutionVariables())
    item.body = 'dj{{ bar }}'
    item.perform(formdata)
    assert len(sms_mocking.sms) == 1
    assert sms_mocking.sms[0]['destinations'] == ['000']
    assert sms_mocking.sms[0]['text'] == 'djFoobar'

    sms_mocking.empty()
    item.body = 'ezt[bar]'
    item.perform(formdata)
    assert len(sms_mocking.sms) == 1
    assert sms_mocking.sms[0]['destinations'] == ['000']
    assert sms_mocking.sms[0]['text'] == 'eztFoobar'

    # invalid recipient
    sms_mocking.empty()
    item.to = ['{% xx']
    item.body = 'xxx'
    item.perform(formdata)
    assert len(sms_mocking.sms) == 0

    # invalid body
    sms_mocking.empty()
    item.to = ['000']
    item.body = '{% xx'
    item.perform(formdata)
    assert len(sms_mocking.sms) == 0

    # disable SMS system
    sms_mocking.empty()
    pub.cfg['sms'] = {'mode': 'none'}
    item.to = ['000']
    item.body = 'XXX'
    item.perform(formdata)  # nothing
    assert len(sms_mocking.sms) == 0


def test_sms_many_recipients(pub, sms_mocking):
    pub.cfg['sms'] = {'sender': 'xxx', 'passerelle_url': 'http://passerelle.invalid/'}

    formdef = FormDef()
    formdef.name = 'foo'
    formdef.fields = [StringField(id='1', label='number', varname='number')]
    formdef.store()

    formdef.data_class().wipe()

    formdata = formdef.data_class()()
    formdata.data = {'1': '000'}
    formdata.just_created()
    formdata.store()

    formdata = formdef.data_class()()
    formdata.data = {'1': '001'}
    formdata.just_created()
    formdata.store()

    item = SendSMSWorkflowStatusItem()
    item.body = 'XXX'

    for recipient in [
        '000,001',
        '000,001,',
        '{% for obj in forms|objects:"foo" %}{{ obj|get:"form_var_number" }},{% endfor %}',
        '{{ forms|objects:"foo"|getlist:"form_var_number" }}',
        '{{ forms|objects:"foo"|getlist:"form_var_number"|list }}',
    ]:
        item.to = [recipient]
        sms_mocking.empty()
        item.perform(formdata)
        pub.process_after_jobs()
        assert len(sms_mocking.sms) == 1
        assert set(sms_mocking.sms[0]['destinations']) == {'000', '001'}
        assert sms_mocking.sms[0]['text'] == 'XXX'


def test_sms_with_passerelle(pub):
    LoggedError.wipe()
    pub.cfg['sms'] = {
        'mode': 'passerelle',
        'passerelle_url': 'http://passerelle.example.com/send?nostop=1',
        'sender': 'Passerelle',
    }
    pub.write_cfg()
    formdef = FormDef()
    formdef.name = 'baz'
    formdef.fields = [
        StringField(id='1', label='String', varname='quotes'),
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.data = {'1': 'with "quotes"'}
    formdata.store()
    pub.substitutions.feed(formdata)

    item = SendSMSWorkflowStatusItem()
    item.to = ['1234']
    item.body = 'my "message" {{ form_var_quotes }}'
    item.counter_name = '{{ form_name }}'
    with mock.patch('wcs.wscalls.get_secret_and_orig') as mocked_secret_and_orig:
        mocked_secret_and_orig.return_value = ('secret', 'localhost')
        with responses.RequestsMock() as rsps:
            rsps.post('http://passerelle.example.com/send', body='data')
            item.perform(formdata)
            url = rsps.calls[-1].request.url
            payload = rsps.calls[-1].request.body
            assert 'http://passerelle.example.com' in url
            assert '?nostop=1' in url
            assert 'orig=localhost' in url
            assert 'signature=' in url
            json_payload = json.loads(payload)
            assert 'message' in json_payload
            assert json_payload['message'] == 'my "message" with "quotes"'
            assert json_payload['to'] == ['1234']
            assert json_payload['from'] == 'Passerelle'
            assert json_payload['counter'] == 'baz'
        assert LoggedError.count() == 0

    with mock.patch('wcs.wscalls.get_secret_and_orig') as mocked_secret_and_orig:
        mocked_secret_and_orig.return_value = ('secret', 'localhost')
        with responses.RequestsMock() as rsps:
            rsps.post('http://passerelle.example.com/send', status=400, json={'err': 1})
            item.perform(formdata)
            assert LoggedError.count() == 1
            assert LoggedError.select()[0].summary == 'Could not send SMS'


def test_sms_to_user(pub, sms_mocking):
    pub.cfg['sms'] = {'sender': 'xxx', 'passerelle_url': 'http://passerelle.invalid/'}
    pub.cfg['users'] = {'field_phone': '_phone'}
    pub.write_cfg()

    user_formdef = UserFieldsFormDef(pub)
    user_formdef.fields = [
        StringField(id='_phone', label='phone', varname='phone', validation={'type': 'phone'})
    ]
    user_formdef.store()

    formdef = FormDef()
    formdef.name = 'baz'
    formdef.fields = [
        StringField(id='1', label='Phone', varname='foo', prefill={'type': 'user', 'value': '_phone'})
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.data = {'1': '0601020304'}
    formdata.store()
    pub.substitutions.feed(formdata)

    item = SendSMSWorkflowStatusItem()
    item.to_mode = 'submitter'
    item.body = 'message'
    item.perform(formdata)
    assert len(sms_mocking.sms) == 1
    assert sms_mocking.sms[0]['destinations'] == ['0601020304']
    assert sms_mocking.sms[0]['counter'] is None
