import io
import xml.etree.ElementTree as ET

import pytest
from webtest import Upload

from wcs import fields
from wcs.formdef import FormDef
from wcs.qommon.http_request import HTTPRequest
from wcs.wscalls import NamedWsCall

from ..utilities import clean_temporary_pub, create_temporary_pub, get_app, login
from .test_all import create_superuser


@pytest.fixture
def pub(request):
    pub = create_temporary_pub()

    req = HTTPRequest(None, {'SCRIPT_NAME': '/', 'SERVER_NAME': 'example.net'})
    pub.set_app_dir(req)
    pub.cfg['identification'] = {'methods': ['password']}
    pub.cfg['language'] = {'language': 'en'}
    pub.write_cfg()

    return pub


def teardown_module(module):
    clean_temporary_pub()


@pytest.fixture
def wscall():
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
    return wscall


@pytest.mark.parametrize('value', [True, False])
def test_wscalls_new(pub, value):
    create_superuser(pub)
    NamedWsCall.wipe()
    app = login(get_app(pub))

    # go to the page and cancel
    resp = app.get('/backoffice/settings/wscalls/')
    resp = resp.click('New webservice call')
    resp = resp.forms[0].submit('cancel')
    assert resp.location == 'http://example.net/backoffice/settings/wscalls/'

    # go to the page and add a webservice call
    resp = app.get('/backoffice/settings/wscalls/')
    resp = resp.click('New webservice call')
    assert resp.form['notify_on_errors'].value is None
    assert resp.form['record_on_errors'].value == 'yes'
    resp.form['name'] = 'a new webservice call'
    resp.form['notify_on_errors'] = value
    resp.form['record_on_errors'] = value
    resp.form['request$url'] = 'http://remote.example.net/json'
    resp = resp.form.submit('submit')
    assert resp.location == 'http://example.net/backoffice/settings/wscalls/'
    resp = resp.follow()
    assert 'a new webservice call' in resp.text
    resp = resp.click('a new webservice call')
    assert 'Webservice Call - a new webservice call' in resp.text
    resp = resp.click('Edit')
    assert 'Edit webservice call' in resp.text

    assert NamedWsCall.get(1).name == 'a new webservice call'
    assert NamedWsCall.get(1).notify_on_errors == value
    assert NamedWsCall.get(1).record_on_errors == value


def test_wscalls_view(pub, wscall):
    create_superuser(pub)

    app = login(get_app(pub))
    resp = app.get('/backoffice/settings/wscalls/%s/' % wscall.id)
    assert 'http://remote.example.net/json' in resp.text
    assert 'Method: POST' in resp.text
    assert 'Query string data:' in resp.text
    assert 'POST data:' in resp.text

    wscall.request['method'] = 'PATCH'
    wscall.store()
    resp = app.get('/backoffice/settings/wscalls/%s/' % wscall.id)
    assert 'Method: PATCH' in resp.text

    wscall.request['method'] = 'GET'
    wscall.store()
    resp = app.get('/backoffice/settings/wscalls/%s/' % wscall.id)
    assert 'Method: GET' in resp.text
    assert 'POST data:' not in resp.text

    # check it's also possible to view the wscall using its slug
    resp = app.get('/backoffice/settings/wscalls/%s/' % wscall.slug)
    assert 'Method: GET' in resp.text
    assert 'POST data:' not in resp.text


def test_wscalls_edit(pub, wscall):
    create_superuser(pub)

    app = login(get_app(pub))

    resp = app.get(f'/backoffice/settings/wscalls/{wscall.id}/')
    resp = resp.click(href='edit')
    assert resp.form['name'].value == 'xxx'
    assert resp.form['notify_on_errors'].value == 'yes'
    assert resp.form['record_on_errors'].value == 'yes'
    assert 'slug' in resp.form.fields
    resp.form['notify_on_errors'] = False
    resp.form['record_on_errors'] = False
    resp = resp.form.submit('submit')
    assert resp.location == f'http://example.net/backoffice/settings/wscalls/{wscall.id}/'
    resp = resp.follow()

    assert NamedWsCall.get(1).notify_on_errors is False
    assert NamedWsCall.get(1).record_on_errors is False

    resp = app.get(f'/backoffice/settings/wscalls/{wscall.id}/')
    resp = resp.click(href='edit')
    assert resp.form['name'].value == 'xxx'
    assert resp.form['notify_on_errors'].value is None
    assert resp.form['record_on_errors'].value is None
    assert 'slug' in resp.form.fields
    resp.form['slug'] = 'yyy'
    resp = resp.form.submit('submit')
    assert resp.location == f'http://example.net/backoffice/settings/wscalls/{wscall.id}/'


def test_wscalls_delete(pub, wscall):
    create_superuser(pub)

    app = login(get_app(pub))

    resp = app.get(f'/backoffice/settings/wscalls/{wscall.id}/')
    resp = resp.click(href='delete')
    resp = resp.form.submit('cancel')
    assert resp.location == 'http://example.net/backoffice/settings/wscalls/'

    resp = app.get(f'/backoffice/settings/wscalls/{wscall.id}/')
    resp = resp.click(href='delete')
    resp = resp.form.submit('submit')
    assert resp.location == 'http://example.net/backoffice/settings/wscalls/'
    assert NamedWsCall.count() == 0


def test_wscalls_export(pub, wscall):
    create_superuser(pub)

    app = login(get_app(pub))
    resp = app.get(f'/backoffice/settings/wscalls/{wscall.id}/')

    resp = resp.click(href='export')
    xml_export = resp.text

    ds = io.StringIO(xml_export)
    wscall2 = NamedWsCall.import_from_xml(ds)
    assert wscall2.name == 'xxx'


def test_wscalls_import(pub, wscall):
    create_superuser(pub)

    wscall.slug = 'foobar'
    wscall.store()
    wscall_xml = ET.tostring(wscall.export_to_xml(include_id=True))
    NamedWsCall.wipe()
    assert NamedWsCall.count() == 0

    app = login(get_app(pub))
    resp = app.get('/backoffice/settings/wscalls/')
    resp = resp.click(href='import')
    resp.forms[0]['file'] = Upload('wscall.wcs', wscall_xml)
    resp = resp.forms[0].submit()
    assert NamedWsCall.count() == 1
    assert {wc.slug for wc in NamedWsCall.select()} == {'foobar'}

    # check slug
    resp = app.get('/backoffice/settings/wscalls/')
    resp = resp.click(href='import')
    resp.forms[0]['file'] = Upload('wscall.wcs', wscall_xml)
    resp = resp.forms[0].submit()
    assert NamedWsCall.count() == 2
    assert {wc.slug for wc in NamedWsCall.select()} == {'foobar', 'xxx'}
    resp = app.get('/backoffice/settings/wscalls/')
    resp = resp.click(href='import')
    resp.forms[0]['file'] = Upload('wscall.wcs', wscall_xml)
    resp = resp.forms[0].submit()
    assert NamedWsCall.count() == 3
    assert {wc.slug for wc in NamedWsCall.select()} == {'foobar', 'xxx', 'xxx_1'}

    # import an invalid file
    resp = app.get('/backoffice/settings/wscalls/')
    resp = resp.click(href='import')
    resp.form['file'] = Upload('wscall.wcs', b'garbage')
    resp = resp.form.submit()
    assert 'Invalid File' in resp.text

    # import an xml of wrong type
    resp = app.get('/backoffice/settings/wscalls/')
    resp = resp.click(href='import')
    resp.form['file'] = Upload('wscall.wcs', b'<hello/>')
    resp = resp.form.submit()
    assert 'Invalid File' in resp.text


def test_wscalls_empty_param_values(pub):
    create_superuser(pub)
    NamedWsCall.wipe()
    app = login(get_app(pub))

    resp = app.get('/backoffice/settings/wscalls/')
    resp = resp.click('New webservice call')
    resp.form['name'] = 'a new webservice call'
    resp.form['request$qs_data$element0key'] = 'foo'
    resp.form['request$post_data$element0key'] = 'bar'
    resp = resp.form.submit('submit').follow()

    wscall = NamedWsCall.get(1)
    assert wscall.request['qs_data'] == {'foo': ''}
    assert wscall.request['post_data'] == {'bar': ''}


def test_wscalls_timeout(pub):
    create_superuser(pub)
    NamedWsCall.wipe()
    app = login(get_app(pub))

    resp = app.get('/backoffice/settings/wscalls/')
    resp = resp.click('New webservice call')
    resp.form['name'] = 'a new webservice call'
    resp.form['request$timeout'] = 'plop'
    resp = resp.form.submit('submit')
    assert resp.pyquery('[data-widget-name="request$timeout"].widget-with-error')
    assert (
        resp.pyquery('[data-widget-name="request$timeout"] .error').text()
        == 'Timeout must be empty or a number.'
    )
    resp.form['request$timeout'] = '10'
    resp = resp.form.submit('submit')

    wscall = NamedWsCall.get(1)
    assert wscall.request['timeout'] == '10'


def test_wscalls_usage(pub, wscall):
    create_superuser(pub)

    app = login(get_app(pub))

    resp = app.get(wscall.get_admin_url())
    usage_url = resp.pyquery('[data-async-url]').attr['data-async-url']
    resp = app.get(usage_url)
    assert 'No usage detected.' in resp.text

    formdef = FormDef()
    formdef.name = '<b>foo</b>'  # to check it is properly escaped
    formdef.fields = [
        fields.CommentField(
            id='1', label='hello', condition={'type': 'django', 'value': 'webservice.xxx.data'}
        )
    ]
    formdef.store()

    resp = app.get(usage_url)
    assert resp.pyquery('a').attr.href.startswith(formdef.get_admin_url())
    assert resp.pyquery('a').text() == '<b>foo</b>, field: "hello" (Comment)'

    # check another webservice with same prefix is not found
    formdef.fields = [
        fields.CommentField(
            id='1', label='hello', condition={'type': 'django', 'value': 'webservice.xxx2.data'}
        )
    ]
    formdef.store()
    resp = app.get(usage_url)
    assert 'No usage detected.' in resp.text

    # check usage within webservices
    wscall2 = NamedWsCall(name='yyy')
    wscall2.request = {
        'url': 'http://remote.example.net/json',
        'request_signature_key': 'xxx',
        'qs_data': {'a': '{{ webservice.xxx.data }}'},
        'method': 'POST',
        'post_data': {'c': 'd'},
    }
    wscall2.store()

    resp = app.get(usage_url)
    assert resp.pyquery('a').attr.href.startswith(wscall2.get_admin_url())
    assert resp.pyquery('a').text() == 'yyy'

    wscall2.request = {
        'url': 'http://remote.example.net/json',
        'request_signature_key': 'xxx',
        'qs_data': {'a': '{% webservice "xxx" as t %}{{ data }}'},
        'method': 'POST',
        'post_data': {'c': 'd'},
    }
    wscall2.store()

    resp = app.get(usage_url)
    assert resp.pyquery('a').attr.href.startswith(wscall2.get_admin_url())
    assert resp.pyquery('a').text() == 'yyy'


def test_wscall_documentation(pub):
    create_superuser(pub)

    NamedWsCall.wipe()
    wscall = NamedWsCall(name='foobar')
    wscall.store()

    app = login(get_app(pub))

    resp = app.get(wscall.get_admin_url())
    assert resp.pyquery('.documentation[hidden]')
    resp = app.post_json(wscall.get_admin_url() + 'update-documentation', {'content': '<p>doc</p>'})
    assert resp.json == {'err': 0, 'empty': False, 'changed': True}
    wscall.refresh_from_storage()
    assert wscall.documentation == '<p>doc</p>'
    resp = app.get(wscall.get_admin_url())
    assert resp.pyquery('.documentation:not([hidden])')


def test_wscall_duplicate(pub, wscall):
    create_superuser(pub)

    app = login(get_app(pub))

    resp = app.get(f'/backoffice/settings/wscalls/{wscall.id}/')
    resp = resp.click(href='duplicate')
    assert resp.form['name'].value == 'xxx (copy)'
    resp = resp.form.submit('submit')
    new_wscall = NamedWsCall.get_by_slug('xxx_copy')
    assert resp.location == f'http://example.net/backoffice/settings/wscalls/{new_wscall.id}/'
    resp = resp.follow()

    resp = app.get(f'/backoffice/settings/wscalls/{wscall.id}/')
    resp = resp.click(href='duplicate')
    assert resp.form['name'].value == 'xxx (copy 2)'
    resp = resp.form.submit('submit')
    new_wscall = NamedWsCall.get_by_slug('xxx_copy_2')
    assert resp.location == f'http://example.net/backoffice/settings/wscalls/{new_wscall.id}/'
    resp = resp.follow()

    resp = app.get(f'/backoffice/settings/wscalls/{wscall.id}/')
    resp = resp.click(href='duplicate')
    resp.form['name'].value = 'xxx (copy)'
    resp = resp.form.submit('submit')
    assert resp.pyquery('#form_error_name').text() == 'This name is already used.'
    resp = resp.form.submit('cancel').follow()
