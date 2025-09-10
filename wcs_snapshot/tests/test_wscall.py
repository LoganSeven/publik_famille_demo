import json

import pytest
import responses

from wcs import fields
from wcs.formdef import FormDef
from wcs.logged_errors import LoggedError
from wcs.qommon.http_request import HTTPRequest
from wcs.qommon.template import Template
from wcs.wscalls import NamedWsCall

from .utilities import clean_temporary_pub, create_temporary_pub, get_app


@pytest.fixture
def pub():
    pub = create_temporary_pub()
    req = HTTPRequest(None, {'SCRIPT_NAME': '/', 'SERVER_NAME': 'example.net'})
    pub.set_app_dir(req)
    pub._set_request(req)
    pub.load_site_options()
    return pub


def teardown_module(module):
    clean_temporary_pub()


def test_named_wscall(pub):
    # create object
    NamedWsCall.wipe()
    wscall = NamedWsCall()
    wscall.name = 'Hello'
    wscall.request = {'url': 'http://example.net', 'qs_data': {'a': 'b'}}
    wscall.store()
    assert wscall.slug == 'hello'

    # get object
    wscall = NamedWsCall.get(wscall.id)
    assert wscall.name == 'Hello'
    assert wscall.request.get('url') == 'http://example.net'
    assert wscall.request.get('qs_data') == {'a': 'b'}

    # create with same name, should get a different slug
    wscall = NamedWsCall()
    wscall.name = 'Hello'
    wscall.request = {'url': 'http://example.net'}
    wscall.store()
    assert wscall.slug == 'hello_1'


def test_webservice_substitution_variable(http_requests, pub):
    NamedWsCall.wipe()

    wscall = NamedWsCall()
    wscall.name = 'Hello world'
    wscall.request = {'url': 'http://remote.example.net/json'}
    wscall.store()
    assert wscall.slug == 'hello_world'

    variables = pub.substitutions.get_context_variables()
    assert variables['webservice'].hello_world == {'foo': 'bar'}


def test_webservice_auto_sign(http_requests, pub):
    NamedWsCall.wipe()

    wscall = NamedWsCall()
    wscall.name = 'Hello world'
    wscall.request = {'url': 'http://blah.example.net'}
    try:
        wscall.call()
    except Exception:
        pass
    assert 'signature=' not in http_requests.get_last('url')

    wscall.request = {'url': 'http://idp.example.net'}
    try:
        wscall.call()
    except Exception:
        pass
    assert 'orig=example.net' in http_requests.get_last('url')
    assert 'signature=' in http_requests.get_last('url')

    # erroneous space
    wscall.request = {'url': ' http://idp.example.net'}
    try:
        wscall.call()
    except Exception:
        pass
    assert 'orig=example.net' in http_requests.get_last('url')
    assert 'signature=' in http_requests.get_last('url')

    wscall.request['request_signature_key'] = 'blah'
    try:
        wscall.call()
    except Exception:
        pass
    assert 'orig=example.net' not in http_requests.get_last('url')
    assert 'signature=' in http_requests.get_last('url')

    # do not auto sign if there's http basic authentication
    wscall.request = {'url': 'http://foo:bar@idp.example.net'}
    try:
        wscall.call()
    except Exception:
        pass
    assert 'orig=example.net' not in http_requests.get_last('url')
    assert 'signature=' not in http_requests.get_last('url')


def test_webservice_post_with_no_payload(http_requests, pub):
    NamedWsCall.wipe()

    wscall = NamedWsCall()
    wscall.name = 'Hello world'
    wscall.request = {'method': 'POST', 'url': 'http://remote.example.net/json'}
    wscall.call()
    assert http_requests.get_last('body') is None


def test_wscall_ezt(http_requests, pub):
    NamedWsCall.wipe()

    wscall = NamedWsCall()
    wscall.name = 'Hello world'
    wscall.request = {'url': 'http://remote.example.net/json'}
    wscall.store()
    assert wscall.slug == 'hello_world'

    variables = pub.substitutions.get_context_variables()

    template = Template('<p>{{ webservice.hello_world.foo }}</p>')
    assert template.render(variables) == '<p>bar</p>'

    template = Template('<p>[webservice.hello_world.foo]</p>')
    assert template.render(variables) == '<p>bar</p>'

    # undefined webservice
    template = Template('<p>{{ webservice.hello.foo }}</p>')
    assert template.render(variables) == '<p></p>'
    template = Template('<p>[webservice.hello.foo]</p>')
    assert template.render(variables) == '<p>[webservice.hello.foo]</p>'


def test_webservice_post_put_patch(http_requests, pub):
    NamedWsCall.wipe()

    for method in ('POST', 'PUT', 'PATCH'):
        wscall = NamedWsCall()
        wscall.name = 'Hello world'
        wscall.request = {
            'method': method,
            'post_data': {'toto': 'coin'},
            'url': 'http://remote.example.net/json',
        }
        try:
            wscall.call()
        except Exception:
            pass
        assert http_requests.get_last('url') == wscall.request['url']
        assert http_requests.get_last('method') == wscall.request['method']
        assert json.loads(http_requests.get_last('body')) == wscall.request['post_data']


def test_webservice_delete(http_requests, pub):
    NamedWsCall.wipe()

    wscall = NamedWsCall()
    wscall.name = 'Hello world'
    wscall.request = {
        'method': 'DELETE',
        'post_data': {'toto': 'coin'},
        'url': 'http://remote.example.net/json',
    }
    try:
        wscall.call()
    except Exception:
        pass
    assert http_requests.get_last('url') == wscall.request['url']
    assert http_requests.get_last('method') == 'DELETE'


@pytest.mark.parametrize('notify_on_errors', [True, False])
@pytest.mark.parametrize('record_on_errors', [True, False])
def test_webservice_on_error(http_requests, emails, notify_on_errors, record_on_errors):
    pub = create_temporary_pub()
    pub.cfg['debug'] = {'error_email': 'errors@localhost.invalid'}
    pub.write_cfg()

    NamedWsCall.wipe()
    FormDef.wipe()

    wscall = NamedWsCall()
    wscall.name = 'Hello world'
    wscall.notify_on_errors = notify_on_errors
    wscall.record_on_errors = record_on_errors
    wscall.store()
    assert wscall.slug == 'hello_world'

    formdef = FormDef()
    formdef.name = 'foobar'
    formdef.fields = [
        fields.CommentField(id='0', label='Foo Bar {{ webservice.hello_world }}'),
    ]
    formdef.store()

    for url_part in ['json', 'json-err0', 'json-errheader0']:
        wscall.request = {'url': 'http://remote.example.net/%s' % url_part}
        wscall.store()
        resp = get_app(pub).get('/foobar/')
        assert 'Foo Bar ' in resp.text
        assert emails.count() == 0
        assert LoggedError.count() == 0

    for url_part in [
        '400',
        '400-json',
        '404',
        '404-json',
        '500',
        'json-err0',
        'json-err0int',
        'json-err1',
        'json-err1int',
        'json-err1-with-desc',
        'json-errstr',
        'json-errheader1',
        'json-errheaderstr',
    ]:
        msg_mapping = {
            '400': '400 Bad Request',
            '400-json': '400 Bad Request (err_desc: :(, err_class: foo_bar)',
            '404': '404 Not Found',
            '404-json': '404 Not Found',
            '500': '500 Internal Server Error',
            'json-err0': None,
            'json-err0int': None,
            'json-err1': None,
            'json-err1int': None,
            'json-err1-with-desc': None,
            'json-errstr': None,
            'json-errheader1': None,
            'json-errheaderstr': None,
        }
        wscall.request = {'url': 'http://remote.example.net/%s' % url_part}
        wscall.store()
        resp = get_app(pub).get('/foobar/')
        assert 'Foo Bar ' in resp.text
        msg = msg_mapping[url_part]
        if notify_on_errors and msg is not None:
            assert emails.count() == 1
            assert (
                emails.get_latest('subject') == '[ERROR] Webservice call (Hello world, hello_world): %s' % msg
            )
            emails.empty()
        else:
            assert emails.count() == 0
        if record_on_errors and msg is not None:
            assert LoggedError.count() == 1
            logged_error = LoggedError.select()[0]
            assert logged_error.summary == 'Webservice call (Hello world, hello_world): %s' % msg
            LoggedError.wipe()
        else:
            assert LoggedError.count() == 0


def test_webservice_empty_param_values(http_requests, pub):
    NamedWsCall.wipe()

    wscall = NamedWsCall()
    wscall.name = 'Hello world'
    wscall.request = {
        'method': 'POST',
        'url': 'http://remote.example.net/json',
        'post_data': {'toto': ''},
        'qs_data': {'titi': ''},
    }
    wscall.store()

    wscall = NamedWsCall.get(wscall.id)
    assert wscall.request['post_data'] == {'toto': ''}
    assert wscall.request['qs_data'] == {'titi': ''}
    wscall.call()
    assert http_requests.get_last('url') == 'http://remote.example.net/json?titi='
    assert http_requests.get_last('body') == '{"toto": ""}'


def test_webservice_empty_param_values_with_signature(pub):
    NamedWsCall.wipe()

    wscall = NamedWsCall()
    wscall.name = 'Hello world'
    wscall.request = {
        'method': 'POST',
        'url': 'http://idp.example.net/json',
        'post_data': {'toto': ''},
        'qs_data': {'titi': ''},
    }
    wscall.store()

    wscall = NamedWsCall.get(wscall.id)
    assert wscall.request['post_data'] == {'toto': ''}
    assert wscall.request['qs_data'] == {'titi': ''}
    with responses.RequestsMock() as rsps:
        rsps.post('http://idp.example.net/json', json={'err': 0})
        wscall.call()
        assert 'signature=' in rsps.calls[-1].request.url
        assert 'titi=' in rsps.calls[-1].request.url


def test_webservice_with_unflattened_payload_keys(http_requests, pub):
    NamedWsCall.wipe()

    wscall = NamedWsCall()
    wscall.name = 'Hello world'
    wscall.request = {
        'method': 'POST',
        'url': 'http://remote.example.net/json',
        'post_data': {'foo/0': 'first', 'foo/1': 'second', 'bar': 'example', 'foo/2': ''},
    }
    wscall.store()

    wscall.call()
    assert http_requests.get_last('url') == 'http://remote.example.net/json'
    assert http_requests.get_last('body') == '{"bar": "example", "foo": ["first", "second", ""]}'
    assert http_requests.count() == 1

    wscall.request = {
        'method': 'POST',
        'url': 'http://remote.example.net/json',
        'post_data': {'foo/0': 'first', 'foo/1': 'second', 'foo/bar': 'example'},
    }
    wscall.store()

    LoggedError.wipe()
    http_requests.empty()
    wscall.call()
    assert http_requests.count() == 0
    assert LoggedError.count() == 0

    wscall.record_on_errors = True
    wscall.store()
    LoggedError.wipe()
    wscall.call()
    assert LoggedError.count() == 1
    assert (
        LoggedError.select()[0].summary == 'Webservice call (Hello world, hello_world): '
        'unable to unflatten payload keys (there is a mix between lists and dicts)'
    )

    wscall.request = {
        'method': 'POST',
        'url': 'http://remote.example.net/json',
        'post_data': {'foo': 'first', 'foo/bar': 'example'},
    }
    wscall.store()
    LoggedError.wipe()
    wscall.call()
    assert LoggedError.count() == 1
    assert (
        LoggedError.select()[0].summary == 'Webservice call (Hello world, hello_world): '
        'unable to unflatten payload keys (key "foo/bar" invalid because key "foo" has value "first")'
    )


def test_webservice_timeout(http_requests, pub):
    NamedWsCall.wipe()

    wscall = NamedWsCall()
    wscall.name = 'Hello world'
    wscall.request = {
        'method': 'GET',
        'url': 'http://remote.example.net/connection-error',
        'timeout': '10',
    }
    try:
        wscall.call()
    except Exception:
        pass
    assert http_requests.get_last('timeout') == 10


def test_webservice_cache(http_requests, pub):
    NamedWsCall.wipe()

    wscall = NamedWsCall()
    wscall.name = 'Hello world'
    wscall.request = {
        'method': 'GET',
        'url': 'http://remote.example.net/json',
        'cache_duration': '120',
    }
    wscall.store()
    wscall = NamedWsCall.get(wscall.id)
    assert wscall.request['cache_duration'] == '120'
    assert wscall.call() == {'foo': 'bar'}
    assert http_requests.count() == 1
    # remove request cache
    pub.get_request().wscalls_cache = {}
    assert wscall.call() == {'foo': 'bar'}
    assert http_requests.count() == 1

    # change cache duration
    pub.get_request().wscalls_cache = {}
    wscall.request['cache_duration'] = '130'
    wscall.store()
    assert wscall.call() == {'foo': 'bar'}
    assert http_requests.count() == 2

    pub.get_request().wscalls_cache = {}
    assert wscall.call() == {'foo': 'bar'}
    assert http_requests.count() == 2

    # make request without cache
    wscall.request = {
        'method': 'GET',
        'url': 'http://remote.example.net/json',
        'cache_duration': None,
    }
    pub.get_request().wscalls_cache = {}
    assert wscall.call() == {'foo': 'bar'}
    assert http_requests.count() == 3
    pub.get_request().wscalls_cache = {}
    assert wscall.call() == {'foo': 'bar'}
    assert http_requests.count() == 4


def test_webservice_dependencies(pub):
    NamedWsCall.wipe()

    wscall = NamedWsCall(name='xxx')
    wscall.notify_on_errors = True
    wscall.record_on_errors = True
    wscall.request = {
        'url': 'http://remote.example.net/json',
        'request_signature_key': 'xxx',
        'qs_data': {'a': 'b'},
        'method': 'POST',
        'post_data': {'c': 'd'},
    }
    wscall.store()

    wscall2 = NamedWsCall(name='yyy')
    wscall2.request = {
        'url': 'http://remote.example.net/json',
        'request_signature_key': 'xxx',
        'qs_data': {'a': '{{ webservice.xxx.data }}'},
        'method': 'POST',
        'post_data': {'c': 'd'},
    }
    wscall2.store()

    assert list(wscall.get_dependencies()) == []
    assert list(wscall2.get_dependencies()) == [wscall]
