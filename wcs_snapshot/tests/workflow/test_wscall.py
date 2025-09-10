import base64
import io
import json
import os
import urllib
from unittest import mock

import pytest
import responses
from quixote import cleanup, get_publisher

from wcs.blocks import BlockDef
from wcs.fields import BlockField, BoolField, FileField, ItemField, ItemsField, StringField
from wcs.formdef import FormDef
from wcs.logged_errors import LoggedError
from wcs.qommon.afterjobs import AfterJob
from wcs.qommon.http_request import HTTPRequest
from wcs.qommon.substitution import CompatibilityNamesDict
from wcs.wf.wscall import JournalWsCallErrorPart, WebserviceCallStatusItem, WorkflowWsCallEvolutionPart
from wcs.workflows import (
    AbortActionException,
    AttachmentEvolutionPart,
    ContentSnapshotPart,
    Workflow,
    WorkflowBackofficeFieldsFormDef,
)

from ..utilities import MockSubstitutionVariables, clean_temporary_pub, create_temporary_pub


def setup_module(module):
    cleanup()


def teardown_module(module):
    clean_temporary_pub()


@pytest.fixture
def pub(request):
    pub = create_temporary_pub()
    pub.cfg['language'] = {'language': 'en'}
    pub.write_cfg()
    req = HTTPRequest(None, {'SERVER_NAME': 'example.net', 'SCRIPT_NAME': ''})
    req.response.filter = {}
    req._user = None
    pub._set_request(req)
    pub.set_config(req)
    return pub


def test_wscall_record_errors(pub):
    pub.user_class.wipe()
    FormDef.wipe()

    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = []
    formdef.store()
    formdef.data_class().wipe()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()

    wscall = WebserviceCallStatusItem()
    wscall.url = 'http://test/'
    wscall.varname = 'varname'
    wscall.action_on_4xx = ':pass'
    wscall.record_errors = True

    # error with json data (stored as a string)
    with responses.RequestsMock() as rsps:
        rsps.get('http://test', status=404, json={'err': 1})
        wscall.perform(formdata)
        assert len([x for x in formdata.evolution[-1].parts if isinstance(x, JournalWsCallErrorPart)]) == 1
        assert formdata.evolution[-1].parts[-1].get_json_export_dict() == {
            'data': '{"err": 1}',
            'label': None,
            'summary': '404 Not Found',
            'type': 'wscall-error',
        }

    # error with bytes that can be stored as string
    formdata.evolution[-1].parts = []
    formdata.store()
    with responses.RequestsMock() as rsps:
        rsps.get('http://test', status=404, body=b'test bytes')
        wscall.perform(formdata)
        assert len([x for x in formdata.evolution[-1].parts if isinstance(x, JournalWsCallErrorPart)]) == 1
        assert formdata.evolution[-1].parts[-1].get_json_export_dict() == {
            'type': 'wscall-error',
            'summary': '404 Not Found',
            'label': None,
            'data': 'test bytes',
        }

    # error with bytes that cannot be stored as string
    formdata.evolution[-1].parts = []
    formdata.store()
    with responses.RequestsMock() as rsps:
        rsps.get('http://test', status=404, body=b'\xf1')
        wscall.perform(formdata)
        assert len([x for x in formdata.evolution[-1].parts if isinstance(x, JournalWsCallErrorPart)]) == 1
        assert formdata.evolution[-1].parts[-1].get_json_export_dict() == {
            'type': 'wscall-error',
            'summary': '404 Not Found',
            'label': None,
            'data_b64': '8Q==\n',
        }

    # application error
    LoggedError.wipe()
    with responses.RequestsMock() as rsps:
        rsps.get('http://test', status=200, body=b'{"err": 1, "err_desc": "some error"}')
        wscall.perform(formdata)
        assert formdata.evolution[-1].parts[-1].get_json_export_dict() == {
            'data': '{"err": 1, "err_desc": "some error"}',
            'label': None,
            'summary': 'err: 1, err_desc: some error',
            'type': 'wscall-error',
        }
        assert LoggedError.count() == 1
        assert LoggedError.select()[0].summary == 'Webservice action: err: 1, err_desc: some error'
        LoggedError.wipe()

    with responses.RequestsMock() as rsps:
        rsps.get('http://test', status=200, body=b'{"err": 1}')
        wscall.perform(formdata)
        assert formdata.evolution[-1].parts[-1].get_json_export_dict() == {
            'data': '{"err": 1}',
            'label': None,
            'summary': 'err: 1',
            'type': 'wscall-error',
        }
        assert LoggedError.count() == 1
        assert LoggedError.select()[0].summary == 'Webservice action: err: 1'
        LoggedError.wipe()

    with responses.RequestsMock() as rsps:
        rsps.get('http://test', status=200, body=b'xxx', headers={'x-error-code': 'X'})
        wscall.perform(formdata)
        assert formdata.evolution[-1].parts[-1].get_json_export_dict() == {
            'data': 'xxx',
            'label': None,
            'summary': 'err: X',
            'type': 'wscall-error',
        }
        assert LoggedError.count() == 1
        assert LoggedError.select()[0].summary == 'Webservice action: err: X'
        LoggedError.wipe()


def test_wscall_post_formdata(pub):
    pub.user_class.wipe()
    FormDef.wipe()

    workflow = Workflow(name='wf')
    st1 = workflow.add_status('Status1', 'st1')
    jump = st1.add_action('jump')
    jump.status = 'st2'
    st2 = workflow.add_status('Status2', 'st2')
    wscall = st2.add_action('webservice_call')
    wscall.url = 'http://test/'
    wscall.varname = 'varname'
    wscall.action_on_4xx = ':pass'
    wscall.post = True
    wscall.record_errors = True
    workflow.store()

    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [
        StringField(id='1', label='string'),
    ]
    formdef.workflow = workflow
    formdef.store()
    formdef.data_class().wipe()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()

    with responses.RequestsMock() as rsps:
        rsps.post('http://test', status=200, json={'err': 0})
        formdata.perform_workflow()
        assert json.loads(rsps.calls[0].request.body)['workflow']['status']['id'] == 'st2'


def test_wscall_publik_caller_url(pub):
    pub.user_class.wipe()
    FormDef.wipe()
    Workflow.wipe()

    workflow = Workflow(name='wf')
    st1 = workflow.add_status('Status1', 'st1')
    jump = st1.add_action('jump')
    jump.status = 'st2'
    st2 = workflow.add_status('Status2', 'st2')
    wscall = st2.add_action('webservice_call')
    wscall.url = 'https://passerelle.invalid/some-endoint/'
    wscall.varname = 'varname'
    workflow.store()

    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [
        StringField(id='1', label='string'),
    ]
    formdef.workflow = workflow
    formdef.store()
    formdef.data_class().wipe()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()

    with responses.RequestsMock() as rsps:
        rsps.get('https://passerelle.invalid/some-endoint/', status=200, json={'err': 0})
        formdata.perform_workflow()
        assert rsps.calls[0].request.headers['Publik-Caller-URL'] == formdata.get_backoffice_url()


def test_webservice_call(http_requests, pub):
    pub.substitutions.feed(MockSubstitutionVariables())

    wf = Workflow(name='wf1')
    st1 = wf.add_status('Status1', 'st1')
    wf.add_status('StatusErr', 'sterr')
    wf.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'baz'
    formdef.fields = []
    formdef.workflow_id = wf.id
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()

    item = WebserviceCallStatusItem()
    item.url = 'http://remote.example.net'
    item.perform(formdata)
    assert http_requests.get_last('url') == 'http://remote.example.net/'
    assert http_requests.get_last('method') == 'GET'

    item = WebserviceCallStatusItem()
    item.url = 'http://remote.example.net'
    item.post = True
    item.perform(formdata)
    assert http_requests.get_last('url') == 'http://remote.example.net/'
    assert http_requests.get_last('method') == 'POST'
    payload = json.loads(http_requests.get_last('body'))
    assert payload['url'] == 'http://example.net/baz/%s/' % formdata.id
    assert payload['display_id'] == formdata.get_display_id()

    item = WebserviceCallStatusItem()
    item.url = 'http://remote.example.net'
    item.post_data = {
        'str': 'abcd',
        'one': '{{ 1 }}',
        'django': '{{ form_number }}',
        'error': '{% if x = y %}a{% endif %}',
    }
    pub.substitutions.feed(formdata)
    item.perform(formdata)
    assert http_requests.get_last('url') == 'http://remote.example.net/'
    assert http_requests.get_last('method') == 'POST'
    payload = json.loads(http_requests.get_last('body'))
    assert payload == {
        'one': 1,
        'str': 'abcd',
        'django': formdata.get_display_id(),
    }

    item = WebserviceCallStatusItem()
    item.url = 'http://remote.example.net'
    item.post = True
    item.post_data = {
        'str': 'abcd',
        'one': '{{ 1 }}',
        'decimal': '{{ 2|decimal }}',
        'error': '{% if x = y %}a{% endif %}',
    }
    pub.substitutions.feed(formdata)
    item.perform(formdata)
    assert http_requests.get_last('url') == 'http://remote.example.net/'
    assert http_requests.get_last('method') == 'POST'
    payload = json.loads(http_requests.get_last('body'))
    assert payload['extra'] == {'one': 1, 'str': 'abcd', 'decimal': '2'}
    assert payload['url'] == 'http://example.net/baz/%s/' % formdata.id
    assert payload['display_id'] == formdata.get_display_id()

    item = WebserviceCallStatusItem()
    item.url = 'http://remote.example.net/json'
    item.varname = 'xxx'
    item.perform(formdata)
    assert formdata.workflow_data['xxx_status'] == 200
    assert formdata.workflow_data['xxx_response'] == {'foo': 'bar'}
    assert formdata.workflow_data.get('xxx_time')

    get_publisher().substitutions.reset()
    get_publisher().substitutions.feed(formdata)
    substvars = get_publisher().substitutions.get_context_variables(mode='lazy')
    assert str(substvars['xxx_status']) == '200'
    assert 'xxx_status' in substvars.get_flat_keys()
    assert str(substvars['xxx_response_foo']) == 'bar'
    assert 'xxx_response_foo' in substvars.get_flat_keys()

    pub.substitutions.reset()
    pub.substitutions.feed(MockSubstitutionVariables())

    formdata.workflow_data = None
    item = WebserviceCallStatusItem()
    item.url = 'http://remote.example.net'
    item.request_signature_key = 'xxx'
    item.perform(formdata)
    assert 'signature=' in http_requests.get_last('url')
    assert http_requests.get_last('method') == 'GET'

    item = WebserviceCallStatusItem()
    item.url = 'http://remote.example.net'
    item.request_signature_key = '{{ doesntexist }}'
    item.perform(formdata)
    assert 'signature=' not in http_requests.get_last('url')

    item = WebserviceCallStatusItem()
    item.url = 'http://remote.example.net'
    item.request_signature_key = '{{ empty }}'
    item.perform(formdata)
    assert 'signature=' not in http_requests.get_last('url')

    item = WebserviceCallStatusItem()
    item.url = 'http://remote.example.net'
    item.request_signature_key = '[empty]'
    item.perform(formdata)
    assert 'signature=' not in http_requests.get_last('url')

    item = WebserviceCallStatusItem()
    item.url = 'http://remote.example.net'
    item.request_signature_key = '{{ bar }}'
    item.perform(formdata)
    assert 'signature=' in http_requests.get_last('url')

    item = WebserviceCallStatusItem()
    item.url = 'http://remote.example.net'
    item.request_signature_key = '[bar]'
    item.perform(formdata)
    assert 'signature=' in http_requests.get_last('url')

    item = WebserviceCallStatusItem()
    item.url = 'http://remote.example.net/204'
    item.varname = 'xxx'
    item.perform(formdata)
    assert formdata.workflow_data.get('xxx_status') == 204
    assert formdata.workflow_data.get('xxx_time')
    assert 'xxx_response' not in formdata.workflow_data
    assert 'xxx_error_response' not in formdata.workflow_data
    formdata.workflow_data = None

    item = WebserviceCallStatusItem()
    item.url = 'http://remote.example.net/404'
    item.varname = 'xxx'
    with pytest.raises(AbortActionException):
        item.perform(formdata)
    assert formdata.workflow_data.get('xxx_status') == 404
    assert formdata.workflow_data.get('xxx_time')
    assert 'xxx_error_response' not in formdata.workflow_data
    formdata.workflow_data = None

    item = WebserviceCallStatusItem()
    item.url = 'http://remote.example.net/404-json'
    item.varname = 'xxx'
    with pytest.raises(AbortActionException):
        item.perform(formdata)
    assert formdata.workflow_data.get('xxx_status') == 404
    assert formdata.workflow_data.get('xxx_error_response') == {'err': 'not-found'}
    assert formdata.workflow_data.get('xxx_time')
    assert 'xxx_response' not in formdata.workflow_data
    formdata.workflow_data = None

    item = WebserviceCallStatusItem()
    item.url = 'http://remote.example.net/404'
    item.action_on_4xx = ':pass'
    item.perform(formdata)

    item = WebserviceCallStatusItem()
    item.url = 'http://remote.example.net/500'
    with pytest.raises(AbortActionException):
        item.perform(formdata)

    item = WebserviceCallStatusItem()
    item.url = 'http://remote.example.net/500'
    item.action_on_5xx = ':pass'
    item.perform(formdata)

    item = WebserviceCallStatusItem()
    item.parent = st1
    assert item.get_jump_label(st1.id) == 'Webservice'
    assert item.get_jump_label('sterr') == 'Error calling webservice'
    item.label = 'Plop'
    assert item.get_jump_label(st1.id) == 'Webservice "Plop"'
    assert item.get_jump_label('sterr') == 'Error calling webservice "Plop"'
    item.url = 'http://remote.example.net/500'
    item.action_on_5xx = 'sterr'  # jump to status
    formdata.status = 'wf-st1'
    formdata.store()
    with pytest.raises(AbortActionException):
        item.perform(formdata)
    assert formdata.status == 'wf-sterr'

    LoggedError.wipe()
    item.action_on_5xx = 'stdeleted'  # removed status
    formdata.status = 'wf-st1'
    formdata.store()
    with pytest.raises(AbortActionException):
        item.perform(formdata)
    assert formdata.status == 'wf-st1'  # unknown status acts like :stop
    assert LoggedError.count() == 1
    error = LoggedError.select()[0]
    assert 'reference-to-invalid-status-stdeleted-in-workflow' in error.tech_id
    assert error.occurences_count == 1

    item = WebserviceCallStatusItem()
    item.url = 'http://remote.example.net/xml'
    item.perform(formdata)

    item = WebserviceCallStatusItem()
    item.url = 'http://remote.example.net/xml'
    item.varname = 'xxx'
    item.action_on_bad_data = ':stop'
    with pytest.raises(AbortActionException):
        item.perform(formdata)
    assert formdata.workflow_data.get('xxx_status') == 200
    assert 'xxx_response' not in formdata.workflow_data
    assert 'xxx_error_response' not in formdata.workflow_data
    formdata.workflow_data = None

    item = WebserviceCallStatusItem()
    item.url = 'http://remote.example.net/404'
    item.record_errors = True
    item.action_on_4xx = ':stop'
    with pytest.raises(AbortActionException):
        item.perform(formdata)
    assert formdata.evolution[-1].parts[-1].summary == '404 Not Found'

    item = WebserviceCallStatusItem()
    item.url = 'http://remote.example.net/xml'
    item.varname = 'xxx'
    item.action_on_bad_data = ':stop'
    item.record_errors = True
    with pytest.raises(AbortActionException):
        item.perform(formdata)
    assert (
        formdata.evolution[-1].parts[-1].summary
        == 'json.decoder.JSONDecodeError: Expecting value: line 1 column 1 (char 0)\n'
    )
    assert formdata.workflow_data.get('xxx_status') == 200
    assert formdata.workflow_data.get('xxx_time')
    assert 'xxx_error_response' not in formdata.workflow_data
    formdata.workflow_data = None

    # check storing response as attachment
    item = WebserviceCallStatusItem()
    item.url = 'http://remote.example.net/xml'
    item.varname = 'xxx'
    item.response_type = 'attachment'
    item.record_errors = True
    item.perform(formdata)
    assert formdata.workflow_data.get('xxx_status') == 200
    assert formdata.workflow_data.get('xxx_content_type') == 'text/xml'
    attachment = formdata.evolution[-1].parts[-1]
    assert isinstance(attachment, AttachmentEvolutionPart)
    assert attachment.base_filename == 'xxx.xml'
    assert attachment.content_type == 'text/xml'
    assert attachment.display_in_history is True
    attachment.fp.seek(0)
    assert attachment.fp.read(5) == b'<?xml'
    formdata.workflow_data = None

    # check storing response as attachment, not displayed in history
    item = WebserviceCallStatusItem()
    item.url = 'http://remote.example.net/xml'
    item.varname = 'xxx'
    item.response_type = 'attachment'
    item.record_errors = True
    item.attach_file_to_history = False
    item.perform(formdata)
    assert formdata.workflow_data.get('xxx_status') == 200
    assert formdata.workflow_data.get('xxx_content_type') == 'text/xml'
    attachment = formdata.evolution[-1].parts[-1]
    assert isinstance(attachment, AttachmentEvolutionPart)
    assert attachment.base_filename == 'xxx.xml'
    assert attachment.content_type == 'text/xml'
    assert attachment.display_in_history is False
    attachment.fp.seek(0)
    assert attachment.fp.read(5) == b'<?xml'
    formdata.workflow_data = None

    item = WebserviceCallStatusItem()
    item.url = 'http://remote.example.net/400-json'
    item.record_errors = True
    item.action_on_4xx = ':stop'
    with pytest.raises(AbortActionException):
        item.perform(formdata)
    assert formdata.evolution[-1].parts[-1].is_hidden()  # not displayed in front
    req = HTTPRequest(None, {'SERVER_NAME': 'example.net', 'SCRIPT_NAME': '/backoffice/'})
    pub._set_request(req)
    assert not formdata.evolution[-1].parts[-1].is_hidden()
    rendered = formdata.evolution[-1].parts[-1].view()
    assert 'Error during webservice call' in str(rendered)
    assert 'Error Code: 1' in str(rendered)
    assert 'Error Description: :(' in str(rendered)

    item.label = 'do that'
    with pytest.raises(AbortActionException):
        item.perform(formdata)
    rendered = formdata.evolution[-1].parts[-1].view()
    assert 'Error during webservice call &quot;do that&quot;' in str(rendered)

    item = WebserviceCallStatusItem()
    item.method = 'GET'
    item.url = 'http://remote.example.net?in_url=1'
    item.qs_data = {
        'str': 'abcd',
        'one': '{{ 1 }}',
        'django': '{{ form_number }}',
        'ezt': '[form_number]',
        'error': '{% if x = y %}a{% endif %}',
        'in_url': '2',
    }
    pub.substitutions.feed(formdata)
    item.perform(formdata)
    assert http_requests.get_last('method') == 'GET'
    qs = urllib.parse.parse_qs(http_requests.get_last('url').split('?')[1])
    assert set(qs.keys()) == {'in_url', 'str', 'one', 'django', 'ezt'}
    assert qs['in_url'] == ['1', '2']
    assert qs['one'] == ['1']
    assert qs['django'] == [formdata.get_display_id()]
    assert qs['ezt'] == [formdata.get_display_id()]
    assert qs['str'] == ['abcd']

    item = WebserviceCallStatusItem()
    item.method = 'DELETE'
    item.post = False
    item.post_data = {'str': 'efgh', 'one': '{{ 3 }}', 'error': '{% if x = y %}a{% endif %}'}
    item.url = 'http://remote.example.net/json'
    pub.substitutions.feed(formdata)
    item.perform(formdata)
    assert http_requests.get_last('url') == 'http://remote.example.net/json'
    assert http_requests.get_last('method') == 'DELETE'
    payload = json.loads(http_requests.get_last('body'))
    assert payload == {'one': 3, 'str': 'efgh'}

    item = WebserviceCallStatusItem()
    item.url = 'http://remote.example.net'
    item.method = 'PUT'
    item.post = False
    item.post_data = {'str': 'efgh', 'one': '{{ 1 }}', 'error': '{% if x = y %}a{% endif %}'}
    pub.substitutions.feed(formdata)
    item.perform(formdata)
    assert http_requests.get_last('url') == 'http://remote.example.net/'
    assert http_requests.get_last('method') == 'PUT'
    payload = json.loads(http_requests.get_last('body'))
    assert payload == {'one': 1, 'str': 'efgh'}

    item = WebserviceCallStatusItem()
    item.url = 'http://remote.example.net'
    item.method = 'PATCH'
    item.post = False
    item.post_data = {'str': 'abcd', 'one': '{{ 1 }}', 'error': '{% if x = y %}a{% endif %}'}
    pub.substitutions.feed(formdata)
    item.perform(formdata)
    assert http_requests.get_last('url') == 'http://remote.example.net/'
    assert http_requests.get_last('method') == 'PATCH'
    payload = json.loads(http_requests.get_last('body'))
    assert payload == {'one': 1, 'str': 'abcd'}


def test_webservice_with_unflattened_payload_keys(http_requests, pub):
    wf = Workflow(name='wf1')
    wf.add_status('Status1', 'st1')
    wf.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'baz'
    formdef.fields = []
    formdef.workflow_id = wf.id
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()

    item = WebserviceCallStatusItem()
    item.url = 'http://remote.example.net'
    item.post_data = {
        'foo/0': 'first',
        'foo/1': 'second',
        'foo/2': '{{ form_name }}',
        'bar': 'example',
        'form//name': '{{ form_name }}',
    }
    pub.substitutions.feed(formdata)
    item.perform(formdata)
    assert http_requests.count() == 1
    assert http_requests.get_last('url') == 'http://remote.example.net/'
    assert http_requests.get_last('method') == 'POST'
    payload = json.loads(http_requests.get_last('body'))
    assert payload == {'foo': ['first', 'second', 'baz'], 'bar': 'example', 'form/name': 'baz'}

    http_requests.empty()
    LoggedError.wipe()
    item = WebserviceCallStatusItem()
    item.url = 'http://remote.example.net'
    item.record_on_errors = True
    item.post_data = {'foo/1': 'first', 'foo/2': 'second'}

    item.perform(formdata)
    assert http_requests.count() == 0
    assert LoggedError.count() == 1
    assert (
        LoggedError.select()[0].summary
        == 'Webservice action: unable to unflatten payload keys (incomplete array before key "foo/1")'
    )

    http_requests.empty()
    LoggedError.wipe()
    item = WebserviceCallStatusItem()
    item.url = 'http://remote.example.net'
    item.record_on_errors = True
    item.post_data = {'0/foo': 'value', '1/bar': 'value', 'name': '{{ form_name }}'}

    item.perform(formdata)
    assert (
        LoggedError.select()[0].summary
        == 'Webservice action: unable to unflatten payload keys (there is a mix between lists and dicts)'
    )

    http_requests.empty()
    LoggedError.wipe()
    item = WebserviceCallStatusItem()
    item.url = 'http://remote.example.net'
    item.record_on_errors = True
    item.post_data = {'0/foo': 'value', '1/bar': 'value'}

    pub.substitutions.feed(formdata)
    item.perform(formdata)

    assert http_requests.count() == 1
    payload = json.loads(http_requests.get_last('body'))
    assert payload == [{'foo': 'value'}, {'bar': 'value'}]


def test_webservice_waitpoint(pub):
    item = WebserviceCallStatusItem()
    assert item.waitpoint
    item.action_on_app_error = ':pass'
    item.action_on_4xx = ':pass'
    item.action_on_5xx = ':pass'
    item.action_on_bad_data = ':pass'
    item.action_on_network_errors = ':pass'
    assert not item.waitpoint
    item.action_on_network_errors = ':stop'
    assert item.waitpoint


def test_webservice_call_error_handling(http_requests, pub):
    pub.substitutions.feed(MockSubstitutionVariables())

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'baz'
    formdef.fields = []
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()

    item = WebserviceCallStatusItem()
    item.url = 'http://remote.example.net/json-err1'
    item.action_on_app_error = ':stop'
    item.action_on_4xx = ':pass'
    item.action_on_5xx = ':pass'
    item.action_on_network_errors = ':pass'
    with pytest.raises(AbortActionException):
        item.perform(formdata)

    item = WebserviceCallStatusItem()
    item.url = 'http://remote.example.net/json-errheader1'
    item.action_on_app_error = ':stop'
    item.action_on_4xx = ':pass'
    item.action_on_5xx = ':pass'
    item.action_on_network_errors = ':pass'
    with pytest.raises(AbortActionException):
        item.perform(formdata)

    item = WebserviceCallStatusItem()
    item.url = 'http://remote.example.net/json-errheaderstr'
    item.action_on_app_error = ':stop'
    item.action_on_4xx = ':pass'
    item.action_on_5xx = ':pass'
    item.action_on_network_errors = ':pass'
    with pytest.raises(AbortActionException):
        item.perform(formdata)

    item = WebserviceCallStatusItem()
    item.url = 'http://remote.example.net/json-err0'
    item.varname = 'xxx'
    item.perform(formdata)
    assert formdata.workflow_data['xxx_status'] == 200
    assert formdata.workflow_data['xxx_app_error_code'] == 0
    assert formdata.workflow_data['xxx_response'] == {'data': 'foo', 'err': 0}
    assert formdata.workflow_data.get('xxx_time')
    formdata.workflow_data = None

    item = WebserviceCallStatusItem()
    item.url = 'http://remote.example.net/json-err0'
    item.varname = 'xxx'
    item.action_on_app_error = ':stop'
    item.perform(formdata)
    assert formdata.workflow_data['xxx_status'] == 200
    assert formdata.workflow_data['xxx_app_error_code'] == 0
    assert formdata.workflow_data['xxx_response'] == {'data': 'foo', 'err': 0}
    assert formdata.workflow_data.get('xxx_time')
    formdata.workflow_data = None

    item = WebserviceCallStatusItem()
    item.url = 'http://remote.example.net/json-err1'
    item.varname = 'xxx'
    item.perform(formdata)
    assert formdata.workflow_data['xxx_status'] == 200
    assert formdata.workflow_data['xxx_app_error_code'] == 1
    assert 'xxx_response' not in formdata.workflow_data
    assert formdata.workflow_data.get('xxx_time')
    formdata.workflow_data = None

    item = WebserviceCallStatusItem()
    item.url = 'http://remote.example.net/json-errstr'
    item.varname = 'xxx'
    item.perform(formdata)
    assert formdata.workflow_data['xxx_status'] == 200
    assert formdata.workflow_data['xxx_app_error_code'] == 'bug'
    assert 'xxx_response' not in formdata.workflow_data
    assert formdata.workflow_data.get('xxx_time')
    formdata.workflow_data = None

    item = WebserviceCallStatusItem()
    item.url = 'http://remote.example.net/json-err1'
    item.varname = 'xxx'
    item.action_on_app_error = ':stop'
    with pytest.raises(AbortActionException):
        item.perform(formdata)
    assert formdata.workflow_data['xxx_status'] == 200
    assert formdata.workflow_data['xxx_app_error_code'] == 1
    assert formdata.workflow_data['xxx_error_response'] == {'data': '', 'err': 1}
    assert 'xxx_response' not in formdata.workflow_data
    assert formdata.workflow_data.get('xxx_time')
    formdata.workflow_data = None

    item = WebserviceCallStatusItem()
    item.url = 'http://remote.example.net/json-errheader0'
    item.varname = 'xxx'
    item.perform(formdata)
    assert formdata.workflow_data['xxx_status'] == 200
    assert formdata.workflow_data['xxx_app_error_code'] == 0
    assert formdata.workflow_data['xxx_app_error_header'] == '0'
    assert formdata.workflow_data['xxx_response'] == {'foo': 'bar'}
    assert formdata.workflow_data.get('xxx_time')
    formdata.workflow_data = None

    item = WebserviceCallStatusItem()
    item.url = 'http://remote.example.net/json-errheader1'
    item.varname = 'xxx'
    item.perform(formdata)
    assert formdata.workflow_data['xxx_status'] == 200
    assert formdata.workflow_data['xxx_app_error_code'] == 1
    assert formdata.workflow_data['xxx_app_error_header'] == '1'
    assert formdata.workflow_data['xxx_error_response'] == {'foo': 'bar'}
    assert 'xxx_response' not in formdata.workflow_data
    assert formdata.workflow_data.get('xxx_time')
    formdata.workflow_data = None

    item = WebserviceCallStatusItem()
    item.url = 'http://remote.example.net/json-errheaderstr'
    item.varname = 'xxx'
    item.perform(formdata)
    assert formdata.workflow_data['xxx_status'] == 200
    assert formdata.workflow_data['xxx_app_error_code'] == 'bug'
    assert formdata.workflow_data['xxx_app_error_header'] == 'bug'
    assert formdata.workflow_data['xxx_error_response'] == {'foo': 'bar'}
    assert 'xxx_response' not in formdata.workflow_data
    assert formdata.workflow_data.get('xxx_time')
    formdata.workflow_data = None

    item = WebserviceCallStatusItem()
    item.url = 'http://remote.example.net/json-errheader1'
    item.varname = 'xxx'
    item.action_on_app_error = ':stop'
    with pytest.raises(AbortActionException):
        item.perform(formdata)
    assert formdata.workflow_data['xxx_status'] == 200
    assert formdata.workflow_data['xxx_app_error_code'] == 1
    assert formdata.workflow_data['xxx_app_error_header'] == '1'
    assert formdata.workflow_data['xxx_error_response'] == {'foo': 'bar'}
    assert 'xxx_response' not in formdata.workflow_data
    assert formdata.workflow_data.get('xxx_time')
    formdata.workflow_data = None

    item = WebserviceCallStatusItem()
    item.url = 'http://remote.example.net/xml-errheader'
    item.varname = 'xxx'
    item.response_type = 'attachment'
    item.record_errors = True
    item.perform(formdata)
    assert formdata.workflow_data.get('xxx_status') == 200
    assert formdata.workflow_data.get('xxx_app_error_code') == 1
    assert formdata.workflow_data.get('xxx_app_error_header') == '1'
    assert 'xxx_response' not in formdata.workflow_data
    formdata.workflow_data = None

    item = WebserviceCallStatusItem()
    item.url = 'http://remote.example.net/xml-errheader'
    item.varname = 'xxx'
    item.response_type = 'attachment'
    item.record_errors = True
    item.action_on_app_error = ':stop'
    with pytest.raises(AbortActionException):
        item.perform(formdata)
    assert formdata.workflow_data.get('xxx_status') == 200
    assert formdata.workflow_data.get('xxx_app_error_code') == 1
    assert formdata.workflow_data.get('xxx_app_error_header') == '1'
    assert 'xxx_response' not in formdata.workflow_data
    formdata.workflow_data = None

    item = WebserviceCallStatusItem()
    item.url = 'http://remote.example.net/xml-errheader'
    item.varname = 'xxx'
    item.response_type = 'json'  # wait for json but receive xml
    item.record_errors = True
    item.perform(formdata)
    assert formdata.workflow_data.get('xxx_status') == 200
    assert formdata.workflow_data.get('xxx_app_error_code') == 1
    assert formdata.workflow_data.get('xxx_app_error_header') == '1'
    assert 'xxx_response' not in formdata.workflow_data
    formdata.workflow_data = None

    item = WebserviceCallStatusItem()
    item.url = 'http://remote.example.net/json-err1'
    item.action_on_app_error = ':stop'
    item.response_type = 'attachment'  # err value is not an error
    item.perform(formdata)  # so, everything is "ok" here
    formdata.workflow_data = None

    item = WebserviceCallStatusItem()
    item.url = 'http://remote.example.net/json-errheaderstr'
    item.action_on_app_error = ':stop'
    item.response_type = 'attachment'
    with pytest.raises(AbortActionException):
        item.perform(formdata)
    formdata.workflow_data = None

    # xml instead of json is not a app_error
    item = WebserviceCallStatusItem()
    item.url = 'http://remote.example.net/xml'
    item.varname = 'xxx'
    item.action_on_app_error = ':stop'
    item.action_on_4xx = ':pass'
    item.action_on_5xx = ':pass'
    item.action_on_network_errors = ':pass'
    item.action_on_bad_data = ':pass'
    item.perform(formdata)
    formdata.workflow_data = None

    # connection error
    item = WebserviceCallStatusItem()
    item.url = 'http://remote.example.net/connection-error'
    item.record_errors = True
    item.action_on_network_errors = ':pass'
    item.perform(formdata)
    assert not formdata.workflow_data

    # connection error, with varname
    item = WebserviceCallStatusItem()
    item.url = 'http://remote.example.net/connection-error'
    item.varname = 'plop'
    item.record_errors = True
    item.action_on_network_errors = ':pass'
    item.perform(formdata)
    assert 'ConnectionError: error' in formdata.evolution[-1].parts[-1].summary
    assert formdata.workflow_data['plop_connection_error'].startswith('error')


def test_webservice_call_store_in_backoffice_filefield(http_requests, pub):
    wf = Workflow(name='wscall to backoffice file field')
    wf.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(wf)
    wf.backoffice_fields_formdef.fields = [
        FileField(id='bo1', label='bo field 1', varname='backoffice_file1'),
    ]
    st1 = wf.add_status('Status1')
    wf.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'baz'
    formdef.fields = []
    formdef.workflow_id = wf.id
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.data = {}
    formdata.store()

    # check storing response in backoffice file field
    item = WebserviceCallStatusItem()
    item.parent = st1
    item.backoffice_filefield_id = 'bo1'
    item.url = 'http://remote.example.net/xml'
    item.response_type = 'attachment'
    item.record_errors = True
    item.perform(formdata)

    assert 'bo1' in formdata.data
    fbo1 = formdata.data['bo1']
    assert fbo1.base_filename == 'file-bo1.xml'
    assert fbo1.content_type == 'text/xml'
    assert fbo1.get_content().startswith(b'<?xml')
    # nothing else is stored
    assert formdata.workflow_data is None
    assert isinstance(formdata.evolution[-1].parts[0], ContentSnapshotPart)

    # store in backoffice file field + varname
    formdata = formdef.data_class()()
    formdata.data = {}
    formdata.just_created()
    formdata.store()
    item.varname = 'xxx'
    item.perform(formdata)
    # backoffice file field
    assert 'bo1' in formdata.data
    fbo1 = formdata.data['bo1']
    assert fbo1.base_filename == 'xxx.xml'
    assert fbo1.content_type == 'text/xml'
    assert fbo1.get_content().startswith(b'<?xml')
    # varname => workflow_data and AttachmentEvolutionPart
    assert formdata.workflow_data.get('xxx_status') == 200
    assert formdata.workflow_data.get('xxx_content_type') == 'text/xml'
    attachment = formdata.evolution[-1].parts[-1]
    assert isinstance(attachment, AttachmentEvolutionPart)
    assert attachment.base_filename == 'xxx.xml'
    assert attachment.content_type == 'text/xml'
    attachment.fp.seek(0)
    assert attachment.fp.read(5) == b'<?xml'

    # no more 'bo1' backoffice field: do nothing
    formdata = formdef.data_class()()
    formdata.data = {}
    formdata.just_created()
    formdata.store()
    wf.backoffice_fields_formdef.fields = [
        FileField(id='bo2', label='bo field 2'),  # id != 'bo1'
    ]
    item.perform(formdata)
    assert formdata.data == {}
    # backoffice field is not a field file:
    wf.backoffice_fields_formdef.fields = [
        StringField(id='bo1', label='bo field 1'),
    ]
    item.perform(formdata)
    assert formdata.data == {}
    # no field at all:
    wf.backoffice_fields_formdef.fields = []
    item.perform(formdata)
    assert formdata.data == {}


@pytest.mark.parametrize('req', [True, False])
def test_webservice_call_store_in_backoffice_filefield_clamd(http_requests, pub, req):
    if req is False:
        pub._set_request(None)

    pub.load_site_options()
    if not pub.site_options.has_section('options'):
        pub.site_options.add_section('options')
    pub.site_options.set('options', 'enable-clamd', 'true')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)

    wf = Workflow(name='wscall to backoffice file field')
    wf.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(wf)
    wf.backoffice_fields_formdef.fields = [
        FileField(id='bo1', label='bo field 1', varname='backoffice_file1'),
    ]
    st1 = wf.add_status('Status1')
    wf.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'baz'
    formdef.fields = []
    formdef.workflow_id = wf.id
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.data = {}
    formdata.store()

    AfterJob.wipe()
    item = WebserviceCallStatusItem()
    item.parent = st1
    item.backoffice_filefield_id = 'bo1'
    item.url = 'http://remote.example.net/xml'
    item.response_type = 'attachment'
    item.record_errors = False
    item.varname = 'xxx'

    with mock.patch('subprocess.run') as run:
        run.side_effect = lambda *args, **kwargs: mock.Mock(returncode=0, stdout='stdout')

        item.perform(formdata)

        if req:
            assert not formdata.data['bo1'].has_been_scanned()
            assert not formdata.evolution[-1].parts[-1].has_been_scanned()
            pub.process_after_jobs(spool=False)

        formdata.refresh_from_storage()
        assert formdata.data['bo1'].has_been_scanned()
        assert formdata.evolution[-1].parts[-1].has_been_scanned()
        assert formdata.data['bo1'].clamd['returncode'] == 0
        assert formdata.evolution[-1].parts[-1].clamd['returncode'] == 0


def test_webservice_target_status(pub):
    wf = Workflow(name='boo')
    status1 = wf.add_status('Status1', 'st1')
    status2 = wf.add_status('Status2', 'st2')
    wf.store()

    item = WebserviceCallStatusItem()
    item.parent = status1
    assert item.get_target_status() == [status1.id]

    item.action_on_app_error = status1.id
    item.action_on_4xx = status2.id
    item.action_on_5xx = status2.id
    targets = item.get_target_status()
    assert len(item.get_target_status()) == 4
    assert targets.count(status1) == 2
    assert targets.count(status2) == 2

    item.action_on_bad_data = 'st3'  # doesn't exist
    targets = item.get_target_status()
    assert len(item.get_target_status()) == 4
    assert targets.count(status1) == 2
    assert targets.count(status2) == 2


def test_webservice_with_complex_data_in_payload(http_requests, pub):
    pub.substitutions.feed(MockSubstitutionVariables())

    wf = Workflow(name='wf1')
    wf.add_status('Status1', 'st1')
    wf.add_status('StatusErr', 'sterr')
    wf.store()

    datasource = {
        'type': 'jsonvalue',
        'value': json.dumps(
            [
                {'id': 'a', 'text': 'aa', 'more': 'aaa'},
                {'id': 'b', 'text': 'bb', 'more': 'bbb'},
                {'id': 'c', 'text': 'cc', 'more': 'ccc'},
            ]
        ),
    }

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'baz'
    formdef.fields = [
        ItemField(id='1', label='1st field', varname='item', data_source=datasource),
        ItemsField(id='2', label='2nd field', varname='items', data_source=datasource),
        StringField(id='3', label='3rd field', varname='str'),
        StringField(id='4', label='4th field', varname='empty_str'),
        StringField(id='5', label='5th field', varname='none'),
        BoolField(id='6', label='6th field', varname='bool'),
    ]
    formdef.workflow_id = wf.id
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {}
    formdata.data['1'] = 'a'
    formdata.data['1_display'] = 'aa'
    formdata.data['1_structured'] = formdef.fields[0].store_structured_value(formdata.data, '1')
    formdata.data['2'] = ['a', 'b']
    formdata.data['2_display'] = 'aa, bb'
    formdata.data['2_structured'] = formdef.fields[1].store_structured_value(formdata.data, '2')
    formdata.data['3'] = 'tutuche'
    formdata.data['4'] = 'empty_str'
    formdata.data['5'] = None
    formdata.data['6'] = False
    formdata.just_created()
    formdata.store()

    attachment_content = b'hello'
    formdata.evolution[-1].parts = [
        AttachmentEvolutionPart('hello.txt', fp=io.BytesIO(attachment_content), varname='testfile')
    ]
    formdata.store()

    item = WebserviceCallStatusItem()
    item.method = 'POST'
    item.url = 'http://remote.example.net'
    item.post_data = {
        'item': '{{ form_var_item }}',
        'ezt_item': '[form_var_item]',
        'items': '{{ form_var_items }}',
        'ezt_items': '[form_var_items]',
        'item_raw': '{{ form_var_item_raw }}',
        'ezt_item_raw': '[form_var_item_raw]',
        'items_raw': '{{ form_var_items_raw }}',
        'with_items_raw': '{% with x=form_var_items_raw %}{{ x }}{% endwith %}',
        'with_items_upper': '{% with x=form_var_items_raw %}{{ x.1|upper }}{% endwith %}',
        'ezt_items_raw': '[form_var_items_raw]',
        'joined_items_raw': '{{ form_var_items_raw|join:"|" }}',
        'forloop_items_raw': '{% for item in form_var_items_raw %}{{item}}|{% endfor %}',
        'str': '{{ form_var_str }}',
        'str_mod': '{{ form_var_str }}--plop',
        'decimal': '{{ "1000"|decimal }}',
        'decimal2': '{{ "1000.1"|decimal }}',
        'empty_string': '{{ form_var_empty }}',
        'none': '{{ form_var_none }}',
        'bool': '{{ form_var_bool_raw }}',
        'attachment': '{{ form_attachments_testfile }}',
        'time': '{{ "13:12"|time }}',
    }
    pub.substitutions.feed(formdata)
    with get_publisher().complex_data():
        item.perform(formdata)
    assert http_requests.get_last('url') == 'http://remote.example.net/'
    assert http_requests.get_last('method') == 'POST'
    payload = json.loads(http_requests.get_last('body'))
    assert payload == {
        'item': 'aa',
        'ezt_item': 'aa',
        'items': 'aa, bb',
        'ezt_items': 'aa, bb',
        'item_raw': 'a',
        'ezt_item_raw': 'a',
        'items_raw': ['a', 'b'],
        'with_items_raw': ['a', 'b'],
        'with_items_upper': 'B',
        'ezt_items_raw': repr(['a', 'b']),
        'joined_items_raw': 'a|b',
        'forloop_items_raw': 'a|b|',
        'str': 'tutuche',
        'str_mod': 'tutuche--plop',
        'decimal': '1000',
        'decimal2': '1000.1',
        'empty_string': '',
        'none': None,
        'bool': False,
        'attachment': {
            'filename': 'hello.txt',
            'content_type': 'application/octet-stream',
            'content': base64.b64encode(attachment_content).decode(),
            'content_is_base64': True,
        },
        'time': '13:12:00',
    }

    # check an empty boolean field is sent as False
    del formdata.data['6']
    with get_publisher().complex_data():
        item.perform(formdata)
    assert http_requests.get_last('url') == 'http://remote.example.net/'
    assert http_requests.get_last('method') == 'POST'
    payload = json.loads(http_requests.get_last('body'))
    assert payload['bool'] is False


def test_webservice_with_complex_data_in_query_string(http_requests, pub):
    pub.substitutions.feed(MockSubstitutionVariables())

    wf = Workflow(name='wf1')
    wf.add_status('Status1', 'st1')
    wf.add_status('StatusErr', 'sterr')
    wf.store()

    datasource = {
        'type': 'jsonvalue',
        'value': json.dumps(
            [
                {'id': 'a', 'text': 'aa', 'more': 'aaa'},
                {'id': 'b', 'text': 'bb', 'more': 'bbb'},
                {'id': 'c', 'text': 'cc', 'more': 'ccc'},
            ]
        ),
    }

    BlockDef.wipe()
    block = BlockDef()
    block.name = 'foobar'
    block.fields = [StringField(id='1', label='String', varname='string')]
    block.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'baz'
    formdef.fields = [
        ItemField(id='1', label='1st field', varname='item', data_source=datasource),
        ItemsField(id='2', label='2nd field', varname='items', data_source=datasource),
        StringField(id='3', label='3rd field', varname='str'),
        StringField(id='4', label='4th field', varname='empty_str'),
        StringField(id='5', label='5th field', varname='none'),
        BoolField(id='6', label='6th field', varname='bool'),
        BlockField(id='7', label='7th field', varname='block', block_slug=block.slug, max_items='3'),
    ]
    formdef.workflow_id = wf.id
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {}
    formdata.data['1'] = 'a'
    formdata.data['1_display'] = 'aa'
    formdata.data['1_structured'] = formdef.fields[0].store_structured_value(formdata.data, '1')
    formdata.data['2'] = ['a', 'b']
    formdata.data['2_display'] = 'aa, bb'
    formdata.data['2_structured'] = formdef.fields[1].store_structured_value(formdata.data, '2')
    formdata.data['3'] = 'tutuche'
    formdata.data['4'] = 'empty_str'
    formdata.data['5'] = None
    formdata.data['6'] = False
    formdata.data['7'] = {
        'data': [
            {
                '1': 'plop',
            },
            {
                '1': 'poulpe',
            },
        ],
        'schema': {},
    }
    formdata.just_created()
    formdata.store()

    item = WebserviceCallStatusItem()
    item.method = 'POST'
    item.url = 'http://remote.example.net'
    item.qs_data = {
        'item': '{{ form_var_item }}',
        'items': '{{ form_var_items }}',
        'item_raw': '{{ form_var_item_raw }}',
        'items_raw': '{{ form_var_items_raw }}',
        'with_items_raw': '{% with x=form_var_items_raw %}{{ x }}{% endwith %}',
        'with_items_upper': '{% with x=form_var_items_raw %}{{ x.1|upper }}{% endwith %}',
        'joined_items_raw': '{{ form_var_items_raw|join:"|" }}',
        'forloop_items_raw': '{% for item in form_var_items_raw %}{{item}}|{% endfor %}',
        'str': '{{ form_var_str }}',
        'str_mod': '{{ form_var_str }}--plop',
        'int': '{{ 1000 }}',
        'decimal': '{{ "1000"|decimal }}',
        'decimal2': '{{ "1000.1"|decimal }}',
        'empty_string': '{{ form_var_empty }}',
        'none': '{{ form_var_none }}',
        'bool': '{{ form_var_bool_raw }}',
        'time': '{{ "13:12"|time }}',
        'block_template': '{% for b in form_var_block %}{{ b.string }}{% endfor %}',
    }
    pub.substitutions.feed(formdata)
    item.perform(formdata)
    assert sorted(urllib.parse.parse_qsl(urllib.parse.urlparse(http_requests.get_last('url')).query)) == [
        ('block_template', 'ploppoulpe'),
        ('bool', 'False'),
        ('decimal', '1000'),
        ('decimal2', '1000.1'),
        ('forloop_items_raw', 'a|b|'),
        ('int', '1000'),
        ('item', 'aa'),
        ('item_raw', 'a'),
        ('items', 'aa, bb'),
        ('items_raw', 'a'),
        ('items_raw', 'b'),
        ('joined_items_raw', 'a|b'),
        ('str', 'tutuche'),
        ('str_mod', 'tutuche--plop'),
        ('time', '13:12:00'),
        ('with_items_raw', 'a'),
        ('with_items_raw', 'b'),
        ('with_items_upper', 'B'),
    ]


def test_wscall_record_responses(pub):
    pub.user_class.wipe()
    FormDef.wipe()

    workflow = Workflow(name='wf')
    st1 = workflow.add_status('Status1', 'st1')
    jump = st1.add_action('jump')
    jump.status = 'st2'
    st2 = workflow.add_status('Status2', 'st2')
    wscall = st2.add_action('webservice_call')
    wscall.url = 'http://test/'
    wscall.varname = 'varname'
    workflow.store()

    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [
        StringField(id='1', label='string'),
    ]
    formdef.workflow = workflow
    formdef.store()
    formdef.data_class().wipe()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()

    with responses.RequestsMock() as rsps:
        rsps.get('http://test', status=200, json={'err': 0, 'data': 'hello'})
        formdata.perform_workflow()
        assert len(list(formdata.iter_evolution_parts(klass=WorkflowWsCallEvolutionPart))) == 1

        substvars = CompatibilityNamesDict()
        substvars.update(formdata.get_substitution_variables())
        assert len(substvars['form_workflow_wscall_varname']) == 1
        assert substvars['form_workflow_wscall_varname_status'] == 200
        assert substvars['form_workflow_wscall_varname_success'] is True
        assert substvars['form_workflow_wscall_varname_url'] == 'http://test/'
        assert substvars['form_workflow_wscall_varname_response_err'] == 0
        assert substvars['form_workflow_wscall_varname_response_data'] == 'hello'
        with pytest.raises(KeyError):
            assert substvars['form_workflow_wscall_varname_xxx']

        # second call
        rsps.get('http://test', status=200, json={'err': 0, 'data': 'hello2'})
        formdata.perform_workflow()
        assert len(list(formdata.iter_evolution_parts(klass=WorkflowWsCallEvolutionPart))) == 1
        substvars = CompatibilityNamesDict()
        substvars.update(formdata.get_substitution_variables())
        assert substvars['form_workflow_wscall_varname_status'] == 200
        assert substvars['form_workflow_wscall_varname_success'] is True
        assert substvars['form_workflow_wscall_varname_datetime']
        assert substvars['form_workflow_wscall_varname_url'] == 'http://test/'
        assert substvars['form_workflow_wscall_varname_response_err'] == 0
        assert substvars['form_workflow_wscall_varname_response_data'] == 'hello2'

        wscall.store_all_responses = True
        workflow.store()
        rsps.get('http://test', status=200, json={'err': 0, 'data': 'hello3'})
        formdata.perform_workflow()
        assert len(list(formdata.iter_evolution_parts(klass=WorkflowWsCallEvolutionPart))) == 2
        substvars = CompatibilityNamesDict()
        substvars.update(formdata.get_substitution_variables())
        assert len(substvars['form_workflow_wscall_varname']) == 2
        assert [x for x in substvars['form_workflow_wscall_varname']]  # noqa pylint: disable=not-an-iterable
        assert 'form_workflow_wscall_varname_status' in substvars.get_flat_keys()
        assert substvars['form_workflow_wscall_varname_status'] == 200
        assert substvars['form_workflow_wscall_varname_success'] is True
        assert substvars['form_workflow_wscall_varname_url'] == 'http://test/'
        assert substvars['form_workflow_wscall_varname_response_err'] == 0
        assert substvars['form_workflow_wscall_varname_response_data'] == 'hello3'

        assert 'form_workflow_wscall_varname_0_status' in substvars.get_flat_keys()
        assert substvars['form_workflow_wscall_varname_0_status'] == 200
        assert substvars['form_workflow_wscall_varname_0_success'] is True
        assert substvars['form_workflow_wscall_varname_0_url'] == 'http://test/'
        assert substvars['form_workflow_wscall_varname_0_response_err'] == 0
        assert substvars['form_workflow_wscall_varname_0_response_data'] == 'hello2'

        assert 'form_workflow_wscall_varname_1_status' in substvars.get_flat_keys()
        assert substvars['form_workflow_wscall_varname_1_status'] == 200
        assert substvars['form_workflow_wscall_varname_1_success'] is True
        assert substvars['form_workflow_wscall_varname_1_url'] == 'http://test/'
        assert substvars['form_workflow_wscall_varname_1_response_err'] == 0
        assert substvars['form_workflow_wscall_varname_1_response_data'] == 'hello3'

        wscall.store_all_responses = False
        wscall.varname = 'othervar'
        workflow.store()
        rsps.get('http://test', status=200, json={'err': 0, 'data': 'hello4'})
        formdata.perform_workflow()
        assert len(list(formdata.iter_evolution_parts(klass=WorkflowWsCallEvolutionPart))) == 3

        substvars = CompatibilityNamesDict()
        substvars.update(formdata.get_substitution_variables())
        assert substvars['form_workflow_wscall_varname_status'] == 200
        assert substvars['form_workflow_wscall_varname_success'] is True
        assert substvars['form_workflow_wscall_varname_url'] == 'http://test/'
        assert substvars['form_workflow_wscall_varname_response_err'] == 0
        assert substvars['form_workflow_wscall_varname_response_data'] == 'hello3'

        assert substvars['form_workflow_wscall_othervar_status'] == 200
        assert substvars['form_workflow_wscall_othervar_success'] is True
        assert substvars['form_workflow_wscall_othervar_url'] == 'http://test/'
        assert substvars['form_workflow_wscall_othervar_response_err'] == 0
        assert substvars['form_workflow_wscall_othervar_response_data'] == 'hello4'

        # store invalid json
        wscall.store_all_responses = False
        wscall.varname = 'errorvar'
        workflow.store()
        rsps.get('http://test', status=200, body=b'invalid json')
        formdata.perform_workflow()
        assert len(list(formdata.iter_evolution_parts(klass=WorkflowWsCallEvolutionPart))) == 4
        assert substvars['form_workflow_wscall_errorvar_response'] == '<invalid>'

        # store error
        wscall.store_all_responses = False
        wscall.varname = 'errorvar'
        workflow.store()
        rsps.get('http://test', status=404, json={'err': 1})
        formdata.perform_workflow()
        assert len(list(formdata.iter_evolution_parts(klass=WorkflowWsCallEvolutionPart))) == 4
        assert substvars['form_workflow_wscall_errorvar_status'] == 404
        assert substvars['form_workflow_wscall_errorvar_success'] is False

        # response with status 2xx are ok
        wscall.store_all_responses = False
        wscall.varname = 'created'
        workflow.store()
        rsps.get('http://test', status=201, body=b'')
        formdata.perform_workflow()
        assert substvars['form_workflow_wscall_created_status'] == 201
        assert substvars['form_workflow_wscall_created_success'] is True


@pytest.mark.parametrize(
    'filename, expected_filename',
    [
        ('Realname.pdf', 'realname.pdf'),
        ('realname.pdf', 'realname.pdf'),
        ('"realname.pdf"', 'realname.pdf'),
        ('real name.pdf', 'real-name.pdf'),
        ('^real;name$.pdf', 'realname.pdf'),
        ('real name.txt', 'real-name.txt.pdf'),
        ('C:\\realname.pdf', 'crealname.pdf'),
        ('^-.-$', 'varname.pdf'),
        ('', 'varname.pdf'),
    ],
)
def test_get_attachement_data(pub, filename, expected_filename):
    pub.user_class.wipe()
    FormDef.wipe()

    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = []
    formdef.store()
    formdef.data_class().wipe()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()

    wscall = WebserviceCallStatusItem()
    wscall.url = 'http://test/'
    wscall.varname = 'varname'
    wscall.response_type = 'attachment'

    # error with json data (stored as a string)
    with responses.RequestsMock() as rsps:
        rsps.get(
            'http://test',
            status=200,
            headers={
                'Content-Type': 'application/pdf',
                'Content-Disposition': f'attachment; filename={filename}',
            },
            body=b'pdf',
        )
        wscall.perform(formdata)
        assert formdata.evolution[-1].parts[-1].get_json_export_dict()['filename'] == expected_filename
