import os
import re
import urllib.parse
from unittest import mock

import pytest
import responses
from webtest import Upload

from wcs import fields
from wcs.blocks import BlockDef
from wcs.categories import Category
from wcs.clamd import scan_formdata
from wcs.formdef import FormDef
from wcs.logged_errors import LoggedError
from wcs.qommon.errors import ConnectionError
from wcs.wscalls import NamedWsCall

from ..utilities import clean_temporary_pub, create_temporary_pub, get_app, login
from .test_all import create_user


def pytest_generate_tests(metafunc):
    if 'pub' in metafunc.fixturenames:
        metafunc.parametrize('pub', ['sql', 'sql-lazy'], indirect=True)


@pytest.fixture
def pub(request):
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


def test_form_file_field_with_fargo(pub, fargo_url):
    create_user(pub)
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    file_field = fields.FileField(id='0', label='file')
    assert file_field.allow_portfolio_picking is False
    file_field.allow_portfolio_picking = True
    formdef.fields = [file_field]
    formdef.store()
    formdef.data_class().wipe()

    assert file_field.allow_portfolio_picking is True

    resp = get_app(pub).get('/test/')
    assert 'f0$file' in resp.text
    assert 'fargo.js' not in resp.text
    assert 'use-file-from-fargo' not in resp.text

    app = get_app(pub)
    login(app, username='foo', password='foo')
    resp = app.get('/test/')
    assert 'f0$file' in resp.text
    assert 'fargo.js' in resp.text
    assert 'use-file-from-fargo' in resp.text

    fargo_resp = app.get('/fargo/pick')  # display file picker
    assert fargo_resp.location == 'http://fargo.example.net/pick/?pick=http%3A//example.net/fargo/pick'
    # check loading a random URL doesn't work
    fargo_resp = app.get('/fargo/pick?url=http://www.example.org/whatever', status=403)
    with responses.RequestsMock() as rsps:
        rsps.get('http://fargo.example.net/...', body=ConnectionError('plop'))
        fargo_resp = app.get('/fargo/pick?url=http://fargo.example.net/...', status=404)
    with responses.RequestsMock() as rsps:
        rsps.get('http://fargo.example.net/...', body=b'...')
        fargo_resp = app.get('/fargo/pick?url=http://fargo.example.net/...')
        assert 'window.top.document.fargo_set_token' in fargo_resp.text
    resp.form['f0$file'] = None
    resp.form['f0$token'] = re.findall(r'fargo_set_token\("(.*?)"', fargo_resp.text)[0]
    resp = resp.form.submit('submit')
    assert 'Check values then click submit.' in resp.text
    resp = resp.form.submit('submit')
    assert formdef.data_class().count() == 1
    formdata = formdef.data_class().select()[0]
    assert formdata.data['0'].get_content() == b'...'

    file_field.allow_portfolio_picking = False
    formdef.store()

    resp = login(get_app(pub), username='foo', password='foo').get('/test/')
    assert 'f0$file' in resp.text
    assert 'fargo.js' not in resp.text
    assert 'use-file-from-fargo' not in resp.text


def test_form_file_field_without_fargo(pub):
    create_user(pub)
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    file_field = fields.FileField(id='0', label='file')
    file_field.allow_portfolio_picking = True
    formdef.fields = [file_field]
    formdef.store()
    formdef.data_class().wipe()

    assert file_field.allow_portfolio_picking is True

    resp = get_app(pub).get('/test/')
    assert 'f0$file' in resp.text
    assert 'fargo.js' not in resp.text
    assert 'use-file-from-fargo' not in resp.text

    resp = login(get_app(pub), username='foo', password='foo').get('/test/')
    assert 'f0$file' in resp.text
    assert 'fargo.js' not in resp.text
    assert 'use-file-from-fargo' not in resp.text


def test_form_file_field_submit(pub):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [fields.FileField(id='0', label='file')]
    formdef.store()
    formdef.data_class().wipe()

    upload = Upload('test.txt', b'foobar', 'text/plain')

    resp = get_app(pub).get('/test/')
    resp.forms[0]['f0$file'] = upload
    resp = resp.forms[0].submit('submit')
    assert 'Check values then click submit.' in resp.text
    resp = resp.forms[0].submit('submit')
    assert resp.status_int == 302
    resp = resp.follow()
    assert 'The form has been recorded' in resp.text
    resp = resp.click('test.txt')
    assert resp.location.endswith('/test.txt')
    resp = resp.follow()
    assert resp.content_type == 'text/plain'
    assert resp.text == 'foobar'


def test_form_file_with_space_field_submit(pub):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [fields.FileField(id='0', label='file')]
    formdef.store()
    formdef.data_class().wipe()

    upload = Upload('  test.txt', b'foobar', 'text/plain')

    resp = get_app(pub).get('/test/')
    resp.forms[0]['f0$file'] = upload
    resp = resp.forms[0].submit('submit')
    assert 'Check values then click submit.' in resp.text
    resp = resp.forms[0].submit('submit')
    assert resp.status_int == 302
    resp = resp.follow()
    assert 'The form has been recorded' in resp.text
    resp = resp.click('test.txt')
    assert resp.location.endswith('/test.txt')
    resp = resp.follow()
    assert resp.content_type == 'text/plain'
    assert resp.text == 'foobar'


def test_form_preupload_file_field_submit(pub):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [fields.FileField(id='0', label='file')]
    formdef.store()
    formdef.data_class().wipe()

    app = get_app(pub)

    resp = app.get('/test/')
    upload = Upload('test.txt', b'foobar', 'text/plain')
    resp.form['f0$file'] = upload

    # this part is actually done in javascript
    upload_url = resp.form['f0$file'].attrs['data-url']
    upload_resp = app.post(upload_url, params=resp.form.submit_fields())
    resp.form['f0$file'] = None
    resp.form['f0$token'] = upload_resp.json[0]['token']

    resp = resp.forms[0].submit('submit')
    assert 'Check values then click submit.' in resp.text
    resp = resp.forms[0].submit('submit')
    assert resp.status_int == 302
    resp = resp.follow()
    assert 'The form has been recorded' in resp.text
    resp = resp.click('test.txt')
    assert resp.location.endswith('/test.txt')
    resp = resp.follow()
    assert resp.content_type == 'text/plain'
    assert resp.text == 'foobar'

    # upload error if file storage is unknown (out of order)
    formdef.fields[0].storage = 'unknown-storage'
    formdef.store()
    resp = app.get('/test/')
    resp.form['f0$file'] = upload
    # javascript simulation
    upload_url = resp.form['f0$file'].attrs['data-url']
    upload_resp = app.post(upload_url, params=resp.form.submit_fields())
    assert upload_resp.json == [{'error': 'failed to store file (system error)'}]
    # try to post the form anyway (with file in f0$file, i.e. "no javascript")
    resp = resp.forms[0].submit('submit')
    assert 'Check values then click submit.' not in resp.text
    assert 'failed to store file (system error)' in resp.text


def test_form_file_field_image_submit(pub):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [fields.FileField(id='0', label='file')]
    formdef.store()
    formdef.data_class().wipe()

    with open(os.path.join(os.path.dirname(__file__), '..', 'image-with-gps-data.jpeg'), 'rb') as fd:
        image_content = fd.read()
    upload = Upload('test.jpg', image_content, 'image/jpeg')

    app = get_app(pub)
    resp = app.get('/test/')
    resp.forms[0]['f0$file'] = upload
    resp = resp.forms[0].submit('submit')
    assert 'Check values then click submit.' in resp.text
    assert '<img alt="" src="tempfile?' in resp.text
    tempfile_id = resp.pyquery('.fileinfo .filename a').attr.href.split('=')[1]

    resp_tempfile = app.get('/test/tempfile?t=%s' % tempfile_id)
    assert resp_tempfile.body == image_content

    # check thumbnailing of image in validation page
    resp_thumbnail = app.get('/test/tempfile?t=%s&thumbnail=1' % tempfile_id)
    assert resp_thumbnail.content_type == 'image/png'

    resp = resp.form.submit('submit').follow()
    assert '<img ' in resp.text
    assert 'download?f=0&thumbnail=1' in resp.text
    resp = resp.goto('download?f=0&thumbnail=1')
    assert '/thumbnail/' in resp.location
    resp = resp.follow()

    # check thumbnailing of image in submitted form
    assert resp.content_type == 'image/png'

    # check a fake image is not sent back
    upload = Upload('test.jpg', b'<script>evil javascript</script>', 'image/jpeg')
    app = get_app(pub)
    resp = app.get('/test/')
    resp.forms[0]['f0$file'] = upload
    resp = resp.forms[0].submit('submit')
    assert '<img alt="" src="tempfile?' not in resp.text


def test_form_file_field_html_submit(pub):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [fields.FileField(id='0', label='file')]
    formdef.store()
    formdef.data_class().wipe()

    html_content = b'<html><body>hello</body></html>'
    upload = Upload('test.html', html_content, 'text/html')

    app = get_app(pub)
    resp = app.get('/test/')
    resp.forms[0]['f0$file'] = upload
    resp = resp.forms[0].submit('submit')
    assert 'Check values then click submit.' in resp.text
    tempfile_id = resp.pyquery('.fileinfo .filename a').attr.href.split('=')[1]

    resp_tempfile = app.get('/test/tempfile?t=%s' % tempfile_id)
    assert resp_tempfile.body == html_content

    resp = resp.form.submit('submit').follow()
    assert resp.click('test.html').follow().content_type == 'text/html'
    assert resp.click('test.html').follow().body == html_content

    # check it's also served raw from backoffice
    user = create_user(pub)
    user.is_admin = True
    user.store()
    app = get_app(pub)
    login(app, username='foo', password='foo')
    resp = app.get(formdef.data_class().select()[0].get_backoffice_url())
    assert resp.click('test.html').follow().content_type == 'text/html'
    assert resp.click('test.html').follow().body == html_content


def test_form_file_field_submit_document_type(pub):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [
        fields.FileField(
            id='0',
            label='file',
            document_type={
                'id': 1,
                'mimetypes': ['image/*'],
                'label': 'Image files',
            },
        )
    ]
    formdef.store()
    formdef.data_class().wipe()

    upload = Upload('test.txt', b'foobar', 'application/force-download')

    resp = get_app(pub).get('/test/')
    resp.form['f0$file'] = upload
    resp = resp.form.submit('submit')
    assert 'invalid file type' in resp.text

    with open(os.path.join(os.path.dirname(__file__), '..', 'image-with-gps-data.jpeg'), 'rb') as fd:
        image_content = fd.read()
    upload = Upload('test.jpg', image_content, 'image/jpeg')
    resp = get_app(pub).get('/test/')
    resp.form['f0$file'] = upload
    resp = resp.form.submit('submit')
    assert 'Check values then click submit.' in resp.text


def test_form_file_field_submit_default_document_type(pub):
    pub.cfg['filetypes'] = {
        0: {'label': 'Text files', 'mimetypes': ['text/plain']},
        1: {'label': 'PNG files', 'mimetypes': ['image/png']},
    }
    pub.write_cfg()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [fields.FileField(id='0', label='file')]
    formdef.store()

    resp = get_app(pub).get('/test/')
    assert 'accept' not in resp.form['f0$file'].attrs

    pub.cfg['misc']['default_file_type'] = 1
    pub.write_cfg()
    resp = get_app(pub).get('/test/')
    assert resp.form['f0$file'].attrs.get('accept') == 'image/png'
    resp.form['f0$file'] = Upload('test.txt', b'foobar', 'text/plain')
    resp = resp.form.submit('submit')
    assert resp.pyquery('.widget-with-error').attr['data-field-id'] == '0'

    # check with invalid default file type
    pub.cfg['misc']['default_file_type'] = 123
    pub.write_cfg()
    resp = get_app(pub).get('/test/')
    assert 'accept' not in resp.form['f0$file'].attrs
    resp.form['f0$file'] = Upload('test.txt', b'foobar', 'text/plain')
    resp = resp.form.submit('submit')
    assert 'Check values then click submit.' in resp.text


def test_form_file_field_submit_max_file_size(pub):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [fields.FileField(id='0', label='file', max_file_size='1ko')]
    formdef.store()
    formdef.data_class().wipe()

    upload = Upload('test.txt', b'foobar' * 1000, 'application/force-download')
    resp = get_app(pub).get('/test/')
    resp.form['f0$file'] = upload
    resp = resp.form.submit('submit')
    assert 'over file size limit (1ko)' in resp.text

    upload = Upload('test.txt', b'foobar' * 100, 'application/force-download')
    resp = get_app(pub).get('/test/')
    resp.form['f0$file'] = upload
    resp = resp.form.submit('submit')
    assert 'Check values then click submit.' in resp.text


def test_form_file_field_submit_wrong_mimetype(pub):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [fields.FileField(id='0', label='file')]
    formdef.store()
    formdef.data_class().wipe()

    upload = Upload('test.txt', b'foobar', 'application/force-download')

    resp = get_app(pub).get('/test/')
    resp.forms[0]['f0$file'] = upload
    resp = resp.forms[0].submit('submit')
    assert 'Check values then click submit.' in resp.text
    resp = resp.forms[0].submit('submit')
    assert resp.status_int == 302
    resp = resp.follow()
    assert 'The form has been recorded' in resp.text
    resp = resp.click('test.txt')
    assert resp.location.endswith('/test.txt')
    resp = resp.follow()
    assert resp.content_type == 'text/plain'
    assert resp.text == 'foobar'

    upload = Upload('test.pdf', b'%PDF-1.4 ...', 'application/force-download')

    resp = get_app(pub).get('/test/')
    resp.forms[0]['f0$file'] = upload
    resp = resp.forms[0].submit('submit')
    assert 'Check values then click submit.' in resp.text
    resp = resp.forms[0].submit('submit')
    assert resp.status_int == 302
    resp = resp.follow()
    assert 'The form has been recorded' in resp.text
    resp = resp.click('test.pdf')
    assert resp.location.endswith('/test.pdf')
    resp = resp.follow()
    assert resp.content_type == 'application/pdf'
    assert resp.text == '%PDF-1.4 ...'


def test_form_file_field_submit_garbage_pdf(pub):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [
        fields.FileField(
            id='0',
            label='file',
            document_type={
                'id': 1,
                'mimetypes': ['application/pdf'],
                'label': 'PDF files',
            },
        )
    ]
    formdef.store()
    formdef.data_class().wipe()

    upload = Upload('test.pdf', b'x' * 500, 'application/pdf')
    resp = get_app(pub).get('/test/')
    resp.forms[0]['f0$file'] = upload
    resp = resp.forms[0].submit('submit')
    assert resp.pyquery('#form_error_f0').text() == 'invalid file type'

    upload = Upload('test.pdf', b'x' * 500 + b'%PDF-1.4 ...', 'application/pdf')
    resp = get_app(pub).get('/test/')
    resp.forms[0]['f0$file'] = upload
    resp = resp.forms[0].submit('submit')
    assert 'Check values then click submit.' in resp.text
    resp = resp.forms[0].submit('submit').follow()
    assert 'The form has been recorded' in resp.text
    resp = resp.click('test.pdf')
    assert resp.location.endswith('/test.pdf')
    resp = resp.follow()
    assert resp.content_type == 'application/pdf'
    assert '%PDF-1.4' in resp.text


def test_form_file_field_submit_blacklist(pub):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [fields.FileField(id='0', label='file')]
    formdef.store()
    formdef.data_class().wipe()

    # application/x-ms-dos-executable
    upload = Upload('test.exe', b'MZ...', 'application/force-download')
    resp = get_app(pub).get('/test/')
    resp.forms[0]['f0$file'] = upload
    resp = resp.forms[0].submit('submit')
    assert 'forbidden file type' in resp.text

    # define custom blacklist
    pub.load_site_options()
    if not pub.site_options.has_section('options'):
        pub.site_options.add_section('options')
    pub.site_options.set('options', 'blacklisted-file-types', 'application/pdf')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)

    # check against mime type
    upload = Upload('test.pdf', b'%PDF-1.4 ...', 'application/force-download')
    resp = get_app(pub).get('/test/')
    resp.forms[0]['f0$file'] = upload
    resp = resp.forms[0].submit('submit')
    assert 'forbidden file type' in resp.text

    # check against extension
    pub.site_options.set('options', 'blacklisted-file-types', '.pdf')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)

    upload = Upload('test.pdf', b'%PDF-1.4 ...', 'application/force-download')
    resp = get_app(pub).get('/test/')
    resp.forms[0]['f0$file'] = upload
    resp = resp.forms[0].submit('submit')
    assert 'forbidden file type' in resp.text


def test_form_file_field_with_wrong_value(pub):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [
        fields.FileField(id='0', label='file', prefill={'type': 'string', 'value': 'foo bar wrong value'})
    ]
    formdef.store()

    get_app(pub).get('/test/')
    assert LoggedError.count() == 1
    logged_error = LoggedError.select()[0]
    assert logged_error.formdef_id == str(formdef.id)
    assert logged_error.summary == 'Failed to convert value for field "file"'
    assert logged_error.exception_class == 'ValueError'
    assert logged_error.exception_message == "invalid data for file type ('foo bar wrong value')"


def test_form_file_field_prefill(pub):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [
        fields.FileField(
            id='0',
            label='file',
            prefill={'type': 'string', 'value': '{{ "test"|qrcode }}'},
        )
    ]
    formdef.store()

    resp = get_app(pub).get('/test/')
    assert resp.pyquery('.file-button').attr.style.startswith(
        '--image-preview-url: url(http://example.net/test/tempfile?'
    )
    assert resp.form['f0$token']
    assert resp.click('qrcode.png').content_type == 'image/png'
    resp = resp.form.submit('submit')  # -> validation
    resp = resp.form.submit('submit')  # -> submit
    formdata = formdef.data_class().select()[0]
    assert formdata.data['0'].base_filename == 'qrcode.png'
    assert formdata.data['0'].get_content().startswith(b'\x89PNG')


@responses.activate
def test_form_file_field_dict_prefill(pub):
    NamedWsCall.wipe()
    wscall = NamedWsCall()
    wscall.name = 'Hello'
    wscall.request = {'url': 'http://example.net'}
    wscall.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [
        fields.FileField(
            id='0',
            label='file',
            prefill={'type': 'string', 'value': '{{ webservice.hello }}'},
        )
    ]
    formdef.store()

    responses.get(
        'http://example.net',
        json={'b64_content': 'aGVsbG8K', 'filename': 'hello.txt', 'content_type': 'text/plain'},
    )
    resp = get_app(pub).get('/test/')
    assert resp.form['f0$token']
    assert resp.click('hello.txt').content_type == 'text/plain'
    resp = resp.form.submit('submit')  # -> validation
    resp = resp.form.submit('submit')  # -> submit
    formdata = formdef.data_class().select()[0]
    assert formdata.data['0'].base_filename == 'hello.txt'
    assert formdata.data['0'].get_content() == b'hello\n'


@responses.activate
def test_form_file_field_url_prefill(pub):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [
        fields.FileField(
            id='0',
            label='file',
            prefill={'type': 'string', 'value': 'http://example.net/hello.txt'},
        )
    ]
    formdef.store()

    responses.get('http://example.net/hello.txt', body=b'Hello\n', content_type='text/plain')
    resp = get_app(pub).get('/test/')
    assert resp.form['f0$token'].value
    assert resp.pyquery('.filename').text() == 'hello.txt'
    assert resp.click('hello.txt').content_type == 'text/plain'
    resp = resp.form.submit('submit')  # -> validation
    resp = resp.form.submit('submit')  # -> submit
    formdata = formdef.data_class().select()[0]
    assert formdata.data['0'].base_filename == 'hello.txt'
    assert formdata.data['0'].get_content() == b'Hello\n'

    LoggedError.wipe()
    responses.get('http://example.net/hello.txt', status=404)
    resp = get_app(pub).get('/test/')
    assert not resp.form['f0$token'].value
    assert not resp.pyquery('.filename').text()
    assert [x.summary for x in LoggedError.select()] == ['Failed to convert value for field "file"']


SVG_CONTENT = b'''<?xml version="1.0" encoding="utf-8"?>
<svg version="1.1" id="Calque_1" xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" x="0px" y="0px"
viewBox="0 0 63.72 64.25" style="enable-background:new 0 0 63.72 64.25;" xml:space="preserve"> <g> </g> </svg>'''


def test_form_file_svg_thumbnail(pub):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [fields.FileField(id='0', label='file')]
    formdef.store()
    formdef.data_class().wipe()

    upload = Upload('test.svg', SVG_CONTENT, 'image/svg+xml')

    app = get_app(pub)
    resp = app.get('/test/')
    resp.forms[0]['f0$file'] = upload
    resp = resp.forms[0].submit('submit')
    thumbnail_url = resp.pyquery('.fileinfo.thumbnail img')[0].attrib['src']
    svg_resp = app.get(urllib.parse.urljoin(resp.request.url, thumbnail_url))
    assert svg_resp.body == SVG_CONTENT
    assert svg_resp.headers['Content-Type'] == 'image/svg+xml'
    resp = resp.forms[0].submit('submit')
    assert resp.status_int == 302
    resp = resp.follow()
    assert 'The form has been recorded' in resp.text
    thumbnail_url = resp.pyquery('.file-field img').attr('src')
    svg_resp = app.get(urllib.parse.urljoin(resp.request.url, thumbnail_url))
    svg_resp = svg_resp.follow()
    assert svg_resp.body == SVG_CONTENT
    assert svg_resp.headers['Content-Type'] == 'image/svg+xml'


def test_form_file_field_aria_description(pub):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [fields.FileField(id='0', label='field label')]
    formdef.store()
    formdef.data_class().wipe()

    upload = Upload('test.txt', b'foobar', 'text/plain')

    resp = get_app(pub).get('/test/')
    resp.forms[0]['f0$file'] = upload
    resp = resp.forms[0].submit('submit')  # -> validation
    resp = resp.forms[0].submit('submit')  # -> submit
    resp = resp.follow()
    assert (
        resp.pyquery.find('#' + resp.pyquery('[aria-describedby]').attr['aria-describedby']).text()
        == 'field label'
    )


def test_form_file_field_in_block_aria_description(pub):
    BlockDef.wipe()
    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.FileField(id='234', required='required', label='field label'),
    ]
    block.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [fields.BlockField(id='1', label='test', block_slug='foobar', max_items='3')]
    formdef.store()
    formdef.data_class().wipe()

    upload = Upload('test.txt', b'foobar', 'text/plain')

    resp = get_app(pub).get('/test/')
    resp.forms[0]['f1$element0$f234$file'] = upload
    resp = resp.forms[0].submit('submit')  # -> validation
    resp = resp.forms[0].submit('submit')  # -> submit
    resp = resp.follow()
    assert (
        resp.pyquery.find('#' + resp.pyquery('[aria-describedby]').attr['aria-describedby']).text()
        == 'field label'
    )


def test_file_download_url_on_wrong_field(pub):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [fields.StringField(id='1', label='str1')]
    formdef.store()
    formdef.data_class().wipe()

    create_user(pub)
    app = get_app(pub)
    login(app, username='foo', password='foo')
    resp = app.get(formdef.get_url())
    resp.form['f1'] = 'test'
    resp = resp.form.submit('submit')  # -> validation
    resp = resp.form.submit('submit').follow()  # -> submit
    formdata = formdef.data_class().select()[0]
    app.get(formdata.get_url() + 'files/1/', status=404)


def test_file_auto_convert_heic(pub):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [fields.FileField(id='0', label='field label')]
    formdef.store()
    formdef.data_class().wipe()

    with open(os.path.join(os.path.dirname(__file__), '..', 'image.heic'), 'rb') as fd:
        upload = Upload('image.heic', fd.read(), 'image/heic')

    resp = get_app(pub).get('/test/')
    resp.forms[0]['f0$file'] = upload
    resp = resp.forms[0].submit('submit')  # -> validation
    resp = resp.forms[0].submit('submit')  # -> submit
    resp = resp.follow()
    assert resp.click('image.jpeg').follow().content_type == 'image/jpeg'
    assert b'JFIF' in resp.click('image.jpeg').follow().body


def test_file_auto_convert_heic_removedraft(pub):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.enable_tracking_codes = True
    formdef.fields = [
        fields.FileField(id='0', label='field label', automatic_image_resize=True),
    ]
    formdef.store()
    formdef.data_class().wipe()

    with open(os.path.join(os.path.dirname(__file__), '..', 'image.heic'), 'rb') as fd:
        upload = Upload('image.heic', fd.read(), 'image/heic')

    app = get_app(pub)
    resp = app.get('/test/')
    resp.form['f0$file'] = upload
    resp = resp.form.submit('removedraft')  # -> submit


@pytest.mark.parametrize('enable_tracking_codes', [True, False])
def test_form_file_field_no_clamd(pub, enable_tracking_codes):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.enable_tracking_codes = enable_tracking_codes
    formdef.fields = [
        fields.FileField(
            id='0',
            label='file',
        )
    ]
    formdef.store()
    formdef.data_class().wipe()

    upload = Upload('test.txt', b'foobar', 'text/plain')
    resp = get_app(pub).get('/test/')
    resp.forms[0]['f0$file'] = upload

    with mock.patch('wcs.clamd.subprocess') as subp:
        attrs = {'run.return_value': mock.Mock(returncode=0)}
        subp.configure_mock(**attrs)
        resp = resp.form.submit('submit')  # -> validation
        subp.run.assert_not_called()  # -> no scan
        resp = resp.form.submit('submit')  # -> submit
        subp.run.assert_not_called()  # -> no scan

    formdata = formdef.data_class().select()[0]
    assert formdata.data['0'].clamd == {}


@pytest.mark.parametrize('enable_tracking_codes', [True, False])
def test_form_block_file_field_no_clamd(pub, enable_tracking_codes):
    BlockDef.wipe()
    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.FileField(id='234', required='required', label='field label'),
    ]
    block.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.enable_tracking_codes = enable_tracking_codes
    formdef.fields = [fields.BlockField(id='1', label='test', block_slug='foobar', max_items='3')]
    formdef.store()
    formdef.data_class().wipe()

    upload1 = Upload('test1.txt', b'foobar', 'text/plain')
    upload2 = Upload('test2.txt', b'barfoo', 'text/plain')
    resp = get_app(pub).get('/test/')
    resp.forms[0]['f1$element0$f234$file'] = upload1
    resp = resp.form.submit('f1$add_element')
    resp.forms[0]['f1$element1$f234$file'] = upload2

    with mock.patch('wcs.clamd.subprocess') as subp:
        attrs = {'run.return_value': mock.Mock(returncode=0)}
        subp.configure_mock(**attrs)
        resp = resp.form.submit('submit')  # -> validation
        subp.run.assert_not_called()  # -> no scan
        resp = resp.form.submit('submit')  # -> submit
        subp.run.assert_not_called()  # -> no scan

    formdata = formdef.data_class().select()[0]
    assert formdata.data['1']['data'][0]['234'].clamd == {}
    assert formdata.data['1']['data'][1]['234'].clamd == {}


@pytest.mark.parametrize('enable_tracking_codes', [True, False])
@pytest.mark.parametrize(
    'clamd_returncode,clamd_msg,span_class',
    [
        (0, '', ''),
        (1, 'A malware was found in this file', '.malware-file'),
        (2, 'The file could not be checked for malware', '.scan-error-file'),
    ],
)
def test_form_file_field_clamd(pub, clamd_returncode, clamd_msg, span_class, enable_tracking_codes):
    pub.load_site_options()
    if not pub.site_options.has_section('options'):
        pub.site_options.add_section('options')
    pub.site_options.set('options', 'enable-clamd', 'true')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)
    pub.load_site_options()

    pub.role_class.wipe()
    role = pub.role_class(name='foobar')
    role.allows_backoffice_access = True
    role.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.enable_tracking_codes = enable_tracking_codes
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.fields = [
        fields.FileField(
            id='0',
            label='file',
        )
    ]
    formdef.store()
    formdef.data_class().wipe()

    upload = Upload('test.txt', b'foobar', 'text/plain')
    resp = get_app(pub).get('/test/')
    resp.forms[0]['f0$file'] = upload

    with mock.patch('wcs.clamd.subprocess') as subp:
        attrs = {'run.return_value': mock.Mock(returncode=clamd_returncode, stdout='stdout')}
        subp.configure_mock(**attrs)
        resp = resp.form.submit('submit')  # -> validation
        subp.run.assert_not_called()  # -> no scan
        resp = resp.form.submit('submit')  # -> submit
        formdata = formdef.data_class().select()[0]
        subp.run.assert_called_once_with(
            ['clamdscan', '--fdpass', formdata.data['0'].get_fs_filename()],
            check=False,
            capture_output=True,
            text=True,
        )
        assert formdata.data['0'].has_been_scanned()
        assert formdata.data['0'].clamd['returncode'] == clamd_returncode

    resp = resp.follow()

    if clamd_returncode != 0:
        assert clamd_msg in resp.pyquery(span_class).text()

    # check file can be downloaded by user
    resp = resp.click('test.txt').follow()
    assert resp.body == b'foobar'

    # check malware detection applies in backoffice
    user = create_user(pub)
    user.roles = [role.id]
    user.store()
    app = get_app(pub)
    login(app, username='foo', password='foo')
    resp = app.get(formdef.data_class().select()[0].get_backoffice_url())
    if clamd_returncode == 0:
        assert resp.pyquery('.file-field').text() == 'test.txt'
        resp = resp.click('test.txt').follow()
        assert resp.body == b'foobar'
    else:
        assert clamd_msg in resp.pyquery(span_class).text()
        resp = resp.click('test.txt').follow(status=403)
        assert clamd_msg in resp.text
        assert 'Force download' not in resp.text

        # check admin user can force download
        user.is_admin = True
        user.store()
        resp = app.get(formdef.data_class().select()[0].get_backoffice_url())
        resp = resp.click('test.txt').follow(status=403)
        assert clamd_msg in resp.text
        resp = resp.click('Force download')
        assert resp.body == b'foobar'

        # check a non-admin user cannot use the force download uri
        user.is_admin = False
        user.store()
        app.get(resp.request.url, status=403)


def test_form_file_field_clamd_ajax_scan(pub):
    pub.load_site_options()
    if not pub.site_options.has_section('options'):
        pub.site_options.add_section('options')
    pub.site_options.set('options', 'enable-clamd', 'true')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)
    pub.load_site_options()

    pub.role_class.wipe()
    role = pub.role_class(name='foobar')
    role.allows_backoffice_access = True
    role.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.fields = [
        fields.FileField(
            id='0',
            label='file',
        )
    ]
    formdef.store()
    formdef.data_class().wipe()

    upload = Upload('test.txt', b'foobar', 'text/plain')
    app = get_app(pub)
    resp = app.get('/test/')
    resp.forms[0]['f0$file'] = upload

    with mock.patch('wcs.clamd.scan_formdata'):
        resp = resp.form.submit('submit')  # -> validation
        resp = resp.form.submit('submit')  # -> submit
        formdata = formdef.data_class().select()[0]

    resp = resp.follow()

    file_data = list(formdata.get_all_file_data(with_history=False))[0]
    digest = file_data.file_digest()

    assert resp.pyquery('.waiting-for-scan-file')
    assert resp.pyquery('.waiting-for-scan-file').text() == 'The file is waiting to be checked for malware.'
    assert resp.pyquery('.waiting-for-scan-file')[0].attrib['data-clamd-digest'] == digest

    ajax_resp = app.get(formdata.get_url() + 'scan')
    assert ajax_resp.json == {
        'err': 0,
        'data': [
            {
                'digest': digest,
                'span_class': 'waiting-for-scan-file',
                'span_msg': 'The file is waiting to be checked for malware.',
            }
        ],
    }

    with mock.patch('wcs.clamd.subprocess') as subp:
        attrs = {'run.return_value': mock.Mock(returncode=1, stdout='stdout')}
        subp.configure_mock(**attrs)
        scan_formdata(formdata)

    ajax_resp = app.get(formdata.get_url() + 'scan')
    assert ajax_resp.json == {
        'err': 0,
        'data': [
            {
                'digest': digest,
                'span_class': 'malware-file',
                'span_msg': 'A malware was found in this file.',
            }
        ],
    }


@pytest.mark.parametrize('enable_tracking_codes', [True, False])
def test_form_block_file_field_clamd(pub, enable_tracking_codes):
    pub.load_site_options()
    if not pub.site_options.has_section('options'):
        pub.site_options.add_section('options')
    pub.site_options.set('options', 'enable-clamd', 'true')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)
    pub.load_site_options()

    BlockDef.wipe()
    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.FileField(id='234', required='required', label='field label'),
    ]
    block.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.enable_tracking_codes = enable_tracking_codes
    formdef.fields = [fields.BlockField(id='1', label='test', block_slug='foobar', max_items='3')]
    formdef.store()
    formdef.data_class().wipe()

    upload1 = Upload('test1.txt', b'foobar', 'text/plain')
    upload2 = Upload('test2.txt', b'barfoo', 'text/plain')
    resp = get_app(pub).get('/test/')
    resp.forms[0]['f1$element0$f234$file'] = upload1
    resp = resp.form.submit('f1$add_element')
    resp.forms[0]['f1$element1$f234$file'] = upload2

    with mock.patch('wcs.clamd.subprocess') as subp:
        attrs = {'run.return_value': mock.Mock(returncode=0, stdout='stdout')}
        subp.configure_mock(**attrs)
        resp = resp.form.submit('submit')  # -> validation
        subp.run.assert_not_called()  # -> no scan
        resp = resp.form.submit('submit')  # -> submit
        formdata = formdef.data_class().select()[0]
        assert subp.run.call_count == 2
        for file_data in formdata.get_all_file_data(with_history=False):
            assert file_data.has_been_scanned()
            assert file_data.clamd['returncode'] == 0
            subp.run.assert_any_call(
                ['clamdscan', '--fdpass', file_data.get_fs_filename()],
                check=False,
                capture_output=True,
                text=True,
            )
