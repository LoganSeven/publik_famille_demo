import base64
import io
import json
import os
import urllib.parse
import xml.etree.ElementTree as ET
import zipfile
from unittest import mock

import pytest
import responses
from django.utils.encoding import force_bytes
from django.utils.timezone import now
from quixote.http_request import Upload as QuixoteUpload
from webtest import Hidden, Upload

from wcs import fields
from wcs.data_sources import NamedDataSource
from wcs.formdef import FormDef
from wcs.logged_errors import LoggedError
from wcs.qommon.afterjobs import AfterJob
from wcs.qommon.form import UploadedFile
from wcs.qommon.misc import ConnectionError
from wcs.wf.export_to_model import transform_to_pdf
from wcs.wf.form import WorkflowFormFieldsFormDef
from wcs.workflows import (
    AttachmentEvolutionPart,
    ContentSnapshotPart,
    Workflow,
    WorkflowBackofficeFieldsFormDef,
)
from wcs.wscalls import NamedWsCall

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

    return pub


def teardown_module(module):
    clean_temporary_pub()


def assert_equal_zip(stream1, stream2):
    with zipfile.ZipFile(stream1) as z1, zipfile.ZipFile(stream2) as z2:
        assert set(z1.namelist()) == set(z2.namelist())
        for name in z1.namelist():
            if name == 'styles.xml':
                continue
            if name in ['content.xml', 'meta.xml']:
                t1, t2 = ET.tostring(ET.XML(z1.read(name))), ET.tostring(ET.XML(z2.read(name)))
                try:
                    # >= python 3.8: tostring preserves attribute order; use canonicalize to sort them
                    t1, t2 = ET.canonicalize(t1), ET.canonicalize(t2)
                except AttributeError:
                    pass
            else:
                t1, t2 = z1.read(name), z2.read(name)
            assert t1 == t2, 'file "%s" differs' % name


def test_formdata_attachment_download(pub):
    create_user(pub)
    wf = Workflow(name='status')
    st1 = wf.add_status('Status1', 'st1')
    attach = st1.add_action('addattachment', id='_attach')
    attach.by = ['_submitter']
    wf.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.workflow_id = wf.id
    formdef.fields = []
    formdef.store()
    formdef.data_class().wipe()

    app = login(get_app(pub), username='foo', password='foo')
    resp = app.get('/test/')
    resp = resp.forms[0].submit('submit')
    assert 'Check values then click submit.' in resp.text
    resp = resp.forms[0].submit('submit')
    assert resp.status_int == 302
    resp = resp.follow()
    assert 'The form has been recorded' in resp.text

    resp.forms[0]['attachment_attach$file'] = Upload('test.txt', b'foobar', 'text/plain')
    resp = resp.forms[0].submit('button_attach')

    assert formdef.data_class().count() == 1
    formdata = formdef.data_class().select()[0]
    assert formdata.evolution[-1].parts[0].__class__.__name__ == 'AttachmentEvolutionPart'
    attachment = formdata.evolution[-1].parts[0]
    assert attachment.content_type == 'text/plain'
    assert attachment.orig_filename == 'test.txt'

    resp = resp.follow()  # back to form page
    resp = resp.click('test.txt')
    assert resp.location.endswith('/test.txt')
    resp = resp.follow()
    assert resp.content_type == 'text/plain'
    assert resp.text == 'foobar'

    # check there's no crash if filename is None
    attachment.base_filename = None
    attachment.orig_filename = None
    formdata.store()
    resp = app.get(formdata.get_url())
    assert resp.click('None').follow().text == 'foobar'


def test_formdata_attachment_download_with_substitution_variable(pub):
    create_user_and_admin(pub)
    wf = Workflow(name='status')
    st1 = wf.add_status('Status1', 'st1')
    attach = st1.add_action('addattachment', id='_attach')
    attach.varname = 'attached_doc'
    attach.by = ['_submitter']
    wf.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.workflow_id = wf.id
    formdef.fields = []
    formdef.store()
    formdef.data_class().wipe()

    resp = login(get_app(pub), username='foo', password='foo').get('/test/')
    resp = resp.forms[0].submit('submit')
    assert 'Check values then click submit.' in resp.text
    resp = resp.forms[0].submit('submit')
    assert resp.status_int == 302
    resp = resp.follow()
    assert 'The form has been recorded' in resp.text

    resp.forms[0]['attachment_attach$file'] = Upload('test.txt', b'foobar', 'text/plain')
    resp = resp.forms[0].submit('button_attach')

    assert formdef.data_class().count() == 1
    formdata = formdef.data_class().select()[0]
    assert formdata.evolution[-1].parts[0].__class__.__name__ == 'AttachmentEvolutionPart'
    attachment = formdata.evolution[-1].parts[0]
    assert attachment.content_type == 'text/plain'
    assert attachment.orig_filename == 'test.txt'

    resp = resp.follow()  # back to form page
    resp = resp.click('test.txt')
    assert resp.location.endswith('/test.txt')
    resp = resp.follow()
    assert resp.content_type == 'text/plain'
    assert resp.text == 'foobar'

    variables = formdef.data_class().select()[0].get_substitution_variables()
    assert 'attachments' in variables
    attachments = variables['attachments']
    assert attachments is not None
    attachment_variable = attachments.attached_doc

    resp = login(get_app(pub), username='admin', password='admin').get(attachment_variable.url).follow()
    assert attachment_variable.content == resp.body
    assert attachment_variable.b64_content == base64.b64encode(resp.body)
    assert attachment_variable.content_type == resp._headers['content-type'].split(';')[0]
    content_disposition = resp._headers['content-disposition']
    assert len(content_disposition.split(';')) == 2
    assert content_disposition.split(';')[0] == 'attachment'
    assert resp.request.environ['PATH_INFO'].endswith(attachment_variable.filename)


def test_formdata_attachment_download_with_invalid_character(pub):
    create_user(pub)
    wf = Workflow(name='status')
    st1 = wf.add_status('Status1', 'st1')
    attach = st1.add_action('addattachment', id='_attach')
    attach.by = ['_submitter']
    wf.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.workflow_id = wf.id
    formdef.fields = []
    formdef.store()
    formdef.data_class().wipe()

    resp = login(get_app(pub), username='foo', password='foo').get('/test/')
    resp = resp.forms[0].submit('submit')
    resp = resp.forms[0].submit('submit').follow()

    resp.forms[0]['attachment_attach$file'] = Upload('test\n".txt', b'foobar', 'text/plain')
    resp = resp.forms[0].submit('button_attach')

    resp = resp.follow()  # back to form page
    resp = resp.click('test".txt')
    resp = resp.follow()
    assert resp.content_type == 'text/plain'
    assert resp.text == 'foobar'


def test_formdata_attachment_download_to_backoffice_file_field(pub):
    create_user(pub)
    wf = Workflow(name='status')
    wf.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(wf)
    wf.backoffice_fields_formdef.fields = [
        fields.FileField(id='bo1', label='bo field 1'),
    ]
    st1 = wf.add_status('Status1', 'st1')
    attach = st1.add_action('addattachment', id='_attach')
    attach.by = ['_submitter']
    attach.backoffice_filefield_id = 'bo1'
    wf.store()

    assert attach.get_backoffice_filefield_options() == [('bo1', 'bo field 1', 'bo1')]

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.workflow_id = wf.id
    formdef.fields = []
    formdef.store()
    formdef.data_class().wipe()

    resp = login(get_app(pub), username='foo', password='foo').get('/test/')
    resp = resp.forms[0].submit('submit')
    assert 'Check values then click submit.' in resp.text
    resp = resp.forms[0].submit('submit')
    assert resp.status_int == 302
    resp = resp.follow()
    assert 'The form has been recorded' in resp.text

    resp.forms[0]['attachment_attach$file'] = Upload('test.txt', b'foobar', 'text/plain')
    resp = resp.forms[0].submit('button_attach')

    # backoffice file field is set
    assert formdef.data_class().count() == 1
    formdata = formdef.data_class().select()[0]
    assert 'bo1' in formdata.data
    bo1 = formdata.data['bo1']
    assert bo1.base_filename == 'test.txt'
    assert bo1.content_type == 'text/plain'
    assert bo1.get_content() == b'foobar'

    # and file is in history, too
    assert formdata.evolution[-1].parts[0].__class__.__name__ == 'AttachmentEvolutionPart'
    attachment = formdata.evolution[-1].parts[0]
    assert attachment.content_type == 'text/plain'
    assert attachment.orig_filename == 'test.txt'


def test_formdata_attachment_download_to_backoffice_file_field_only(pub):
    create_user(pub)
    wf = Workflow(name='status')
    wf.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(wf)
    wf.backoffice_fields_formdef.fields = [
        fields.FileField(id='bo1', label='bo field 1'),
    ]
    st1 = wf.add_status('Status1', 'st1')
    attach = st1.add_action('addattachment', id='_attach')
    attach.by = ['_submitter']
    attach.backoffice_filefield_id = 'bo1'
    attach.attach_to_history = False  # do not display in history
    wf.store()

    assert attach.get_backoffice_filefield_options() == [('bo1', 'bo field 1', 'bo1')]

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.workflow_id = wf.id
    formdef.fields = []
    formdef.store()
    formdef.data_class().wipe()

    resp = login(get_app(pub), username='foo', password='foo').get('/test/')
    resp = resp.forms[0].submit('submit')
    assert 'Check values then click submit.' in resp.text
    resp = resp.forms[0].submit('submit')
    assert resp.status_int == 302
    resp = resp.follow()
    assert 'The form has been recorded' in resp.text

    resp.forms[0]['attachment_attach$file'] = Upload('test.txt', b'foobar', 'text/plain')
    resp = resp.forms[0].submit('button_attach')

    # backoffice file field is set
    assert formdef.data_class().count() == 1
    formdata = formdef.data_class().select()[0]
    assert 'bo1' in formdata.data
    bo1 = formdata.data['bo1']
    assert bo1.base_filename == 'test.txt'
    assert bo1.content_type == 'text/plain'
    assert bo1.get_content() == b'foobar'

    # nothing displayed in history
    resp = resp.follow()
    assert 'resp.text' not in resp.text
    assert len(formdata.evolution) == 2
    assert len(formdata.evolution[0].parts) == 1
    assert isinstance(formdata.evolution[0].parts[0], ContentSnapshotPart)

    # but attachment stored
    assert isinstance(formdata.evolution[1].parts[0], AttachmentEvolutionPart)


def test_formdata_attachment_stored(pub):
    create_user(pub)
    wf = Workflow(name='status')
    st1 = wf.add_status('Status1', 'st1')
    attach = st1.add_action('addattachment', id='_attach')
    attach.by = ['_submitter']
    attach.backoffice_filefield_id = None  # do not store as backoffice field
    attach.attach_to_history = False  # do not display in history
    wf.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.workflow_id = wf.id
    formdef.fields = []
    formdef.store()
    formdef.data_class().wipe()

    resp = login(get_app(pub), username='foo', password='foo').get('/test/')
    resp = resp.forms[0].submit('submit')
    assert 'Check values then click submit.' in resp.text
    resp = resp.forms[0].submit('submit')
    assert resp.status_int == 302
    resp = resp.follow()
    assert 'The form has been recorded' in resp.text

    resp.forms[0]['attachment_attach$file'] = Upload('test.txt', b'foobar', 'text/plain')
    resp = resp.forms[0].submit('button_attach')

    # nothing displayed in history
    resp = resp.follow()
    assert 'resp.text' not in resp.text
    formdata = formdef.data_class().select()[0]
    assert len(formdata.evolution) == 2
    assert len(formdata.evolution[0].parts) == 1
    assert isinstance(formdata.evolution[0].parts[0], ContentSnapshotPart)

    # but attachment stored
    assert isinstance(formdata.evolution[1].parts[0], AttachmentEvolutionPart)


def test_formdata_attachment_file_options(pub):
    create_user(pub)
    wf = Workflow(name='status')
    st1 = wf.add_status('Status1', 'st1')
    attach = st1.add_action('addattachment', id='_attach')
    attach.by = ['_submitter']
    attach.document_type = {'label': 'Fichiers vidéo', 'mimetypes': ['video/*'], 'id': '_video'}
    attach.max_file_size = '3Mo'
    wf.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.workflow_id = wf.id
    formdef.fields = []
    formdef.store()
    formdef.data_class().wipe()

    resp = login(get_app(pub), username='foo', password='foo').get('/test/')
    resp = resp.forms[0].submit('submit')
    assert 'Check values then click submit.' in resp.text
    resp = resp.forms[0].submit('submit')
    assert resp.status_int == 302
    resp = resp.follow()
    assert 'The form has been recorded' in resp.text

    file_input = resp.forms[0]['attachment_attach$file']
    assert file_input.attrs['accept'] == 'video/*'
    assert file_input.attrs['data-max-file-size'] == '3000000'


def test_formdata_attachment_pick_from_portfolio(pub, fargo_url):
    create_user(pub)
    wf = Workflow(name='status')
    st1 = wf.add_status('Status1', 'st1')
    attach = st1.add_action('addattachment', id='_attach')
    attach.by = ['_submitter']
    wf.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.workflow_id = wf.id
    formdef.fields = []
    formdef.store()
    formdef.data_class().wipe()

    app = login(get_app(pub), username='foo', password='foo')
    resp = app.get('/test/')
    resp = resp.forms[0].submit('submit')
    resp = resp.forms[0].submit('submit')
    resp = resp.follow()
    assert 'The form has been recorded' in resp.text

    assert 'use-file-from-fargo' not in resp.text
    resp.forms[0]['attachment_attach$file'] = Upload('test.txt', b'foobar', 'text/plain')
    resp = resp.forms[0].submit('button_attach')

    attach.allow_portfolio_picking = True
    wf.store()
    resp = app.get(resp.request.url)
    assert 'use-file-from-fargo' in resp.text


def test_formdata_attachment_clamd(pub):
    pub.load_site_options()
    if not pub.site_options.has_section('options'):
        pub.site_options.add_section('options')
    pub.site_options.set('options', 'enable-clamd', 'true')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)
    pub.load_site_options()

    create_user(pub)
    wf = Workflow(name='status')
    st1 = wf.add_status('Status1', 'st1')
    attach = st1.add_action('addattachment', id='_attach')
    attach.by = ['_submitter']
    wf.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.workflow_id = wf.id
    formdef.fields = []
    formdef.store()
    formdef.data_class().wipe()

    resp = login(get_app(pub), username='foo', password='foo').get('/test/')
    resp = resp.forms[0].submit('submit')
    assert 'Check values then click submit.' in resp.text
    resp = resp.forms[0].submit('submit')
    assert resp.status_int == 302
    resp = resp.follow()
    assert 'The form has been recorded' in resp.text

    resp.forms[0]['attachment_attach$file'] = Upload('test.txt', b'foobar', 'text/plain')
    with mock.patch('wcs.clamd.subprocess') as subp:
        attrs = {'run.return_value': mock.Mock(returncode=0, stdout='stdout')}
        subp.configure_mock(**attrs)

        resp = resp.forms[0].submit('button_attach')
        assert formdef.data_class().count() == 1
        formdata = formdef.data_class().select()[0]
        assert formdata.evolution[-1].parts[0].__class__.__name__ == 'AttachmentEvolutionPart'
        attachment = formdata.evolution[-1].parts[0]
        subp.run.assert_called_once_with(
            ['clamdscan', '--fdpass', attachment.get_file_path()], check=False, capture_output=True, text=True
        )
        assert attachment.has_been_scanned()
        assert attachment.clamd['returncode'] == 0


@pytest.mark.parametrize('clamd_returncode', [0, 1])
def test_formdata_attachment_clamd_download(pub, clamd_returncode):
    pub.load_site_options()
    if not pub.site_options.has_section('options'):
        pub.site_options.add_section('options')
    pub.site_options.set('options', 'enable-clamd', 'true')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)
    pub.load_site_options()

    create_user(pub)
    wf = Workflow(name='status')
    st1 = wf.add_status('Status1', 'st1')
    attach = st1.add_action('addattachment', id='_attach')
    attach.by = ['_submitter']
    wf.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.workflow_id = wf.id
    formdef.fields = []
    formdef.store()
    formdef.data_class().wipe()

    resp = login(get_app(pub), username='foo', password='foo').get('/test/')
    resp = resp.forms[0].submit('submit')
    assert 'Check values then click submit.' in resp.text
    resp = resp.forms[0].submit('submit')
    assert resp.status_int == 302
    resp = resp.follow()
    assert 'The form has been recorded' in resp.text

    resp.forms[0]['attachment_attach$file'] = Upload('test.txt', b'foobar', 'text/plain')
    with mock.patch('wcs.clamd.subprocess') as subp:
        attrs = {'run.return_value': mock.Mock(returncode=clamd_returncode, stdout='stdout')}
        subp.configure_mock(**attrs)

        resp = resp.forms[0].submit('button_attach')
        assert formdef.data_class().count() == 1
        formdata = formdef.data_class().select()[0]
        assert formdata.evolution[-1].parts[0].__class__.__name__ == 'AttachmentEvolutionPart'
        attachment = formdata.evolution[-1].parts[0]
        subp.run.assert_called_once_with(
            ['clamdscan', '--fdpass', attachment.get_file_path()], check=False, capture_output=True, text=True
        )
        assert attachment.has_been_scanned()
        assert attachment.clamd['returncode'] == clamd_returncode

    resp = resp.follow()  # back to form page

    if clamd_returncode == 1:
        assert 'A malware was found in this file.' in resp.pyquery('.malware-file').text()
    href = resp.pyquery('p.wf-attachment a')[0].attrib['href']
    resp = resp.goto(href, status=302)
    assert resp.location.endswith('/test.txt')
    resp = resp.follow()
    assert resp.content_type == 'text/plain'
    assert resp.text == 'foobar'


def test_formdata_generated_document_download(pub):
    create_user(pub)
    wf = Workflow(name='status')
    st1 = wf.add_status('Status1', 'st1')
    export_to = st1.add_action('export_to_model', id='_export_to')
    export_to.convert_to_pdf = False
    export_to.label = 'create doc'
    export_to.by = ['_submitter']
    wf.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.workflow_id = wf.id
    formdef.fields = []
    formdef.store()
    formdef.data_class().wipe()

    resp = login(get_app(pub), username='foo', password='foo').get('/test/')
    resp = resp.forms[0].submit('submit')
    assert 'Check values then click submit.' in resp.text
    resp = resp.forms[0].submit('submit')
    assert resp.status_int == 302
    form_location = resp.location
    resp = resp.follow()
    assert 'The form has been recorded' in resp.text

    # assert action is not advertised
    assert 'button_export_to' not in resp.text
    # assert document creation url doesn't work
    resp = login(get_app(pub), username='foo', password='foo').get(form_location + 'create_doc/', status=501)
    assert 'No model defined for this action' in resp.text

    # add a file to action
    upload = QuixoteUpload('/foo/test.xml', content_type='application/xml')
    upload.fp = io.BytesIO()
    upload.fp.write(b'<test>HELLO WORLD</test>')
    upload.fp.seek(0)
    export_to.model_file = UploadedFile(pub.app_dir, None, upload)
    wf.store()

    resp = login(get_app(pub), username='foo', password='foo').get(form_location)
    resp = resp.form.submit('button_export_to')
    resp = resp.follow()  # $form/$id/create_doc
    resp = resp.follow()  # $form/$id/create_doc/
    assert resp.text == '<test>HELLO WORLD</test>'

    export_to.attach_to_history = True
    wf.store()

    resp = login(get_app(pub), username='foo', password='foo').get(form_location)
    resp = resp.form.submit('button_export_to')
    assert resp.location == form_location + '#action-zone'
    resp = resp.follow()  # back to form page

    resp = resp.click('test.xml')
    assert resp.location.endswith('/test.xml')
    resp = resp.follow()
    assert resp.content_type == 'application/xml'
    assert resp.text == '<test>HELLO WORLD</test>'

    # change export model to be a new XML file, do the action again on the same form and
    # check that both the old and new .xml files are there and valid.
    upload = QuixoteUpload('/foo/test.xml', content_type='application/xml')
    upload.fp = io.BytesIO()
    upload.fp.write(b'<test>HELLO NEW WORLD</test>')
    upload.fp.seek(0)
    export_to.model_file = UploadedFile(pub.app_dir, None, upload)
    wf.store()

    resp = login(get_app(pub), username='foo', password='foo').get(form_location)
    resp = resp.form.submit('button_export_to')
    assert resp.location == form_location + '#action-zone'
    resp = resp.follow()  # back to form page

    assert resp.click('test.xml', index=0).follow().text == '<test>HELLO WORLD</test>'
    assert resp.click('test.xml', index=1).follow().text == '<test>HELLO NEW WORLD</test>'

    # use substitution variables on rtf: only ezt format is accepted
    pub.site_options.set('options', 'disable-rtf-support', 'false')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)
    upload = QuixoteUpload('/foo/test.rtf', content_type='application/rtf')
    upload.fp = io.BytesIO()
    upload.fp.write(b'HELLO {{DJANGO}} WORLD [form_name]')
    upload.fp.seek(0)
    export_to.model_file = UploadedFile(pub.app_dir, None, upload)
    wf.store()

    resp = login(get_app(pub), username='foo', password='foo').get(form_location)
    resp = resp.form.submit('button_export_to')
    assert resp.location == form_location + '#action-zone'
    resp = resp.follow()

    assert resp.click('test.rtf').follow().text == 'HELLO {{DJANGO}} WORLD {\\uc1{test}}'


@pytest.fixture(params=['template.odt', 'template-django.odt'])
def odt_template(request):
    return request.param


def test_formdata_generated_document_odt_download(pub, odt_template):
    create_user(pub)
    wf = Workflow(name='status')
    st1 = wf.add_status('Status1', 'st1')
    export_to = st1.add_action('export_to_model', id='_export_to')
    export_to.convert_to_pdf = False
    export_to.label = 'create doc'
    template_filename = os.path.join(os.path.dirname(__file__), '..', odt_template)
    with open(template_filename, 'rb') as fd:
        template = fd.read()
    upload = QuixoteUpload('/foo/' + odt_template, content_type='application/octet-stream')
    upload.fp = io.BytesIO()
    upload.fp.write(template)
    upload.fp.seek(0)
    export_to.model_file = UploadedFile(pub.app_dir, None, upload)
    export_to.by = ['_submitter']
    wf.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.workflow_id = wf.id
    formdef.fields = [fields.TextField(id='0', label='comment', varname='comment')]
    formdef.store()
    formdef.data_class().wipe()

    resp = login(get_app(pub), username='foo', password='foo').get('/test/')
    resp.form['f0'] = 'Hello\n\nWorld.'
    resp = resp.forms[0].submit('submit')
    assert 'Check values then click submit.' in resp.text
    resp = resp.forms[0].submit('submit')
    assert resp.status_int == 302
    form_location = resp.location
    resp = resp.follow()
    assert 'The form has been recorded' in resp.text

    resp = resp.form.submit('button_export_to')

    resp = resp.follow()  # $form/$id/create_doc
    resp = resp.follow()  # $form/$id/create_doc/
    with open(os.path.join(os.path.dirname(__file__), '..', 'template-out.odt'), 'rb') as f:
        assert_equal_zip(io.BytesIO(resp.body), f)
        with io.BytesIO(resp.body) as generated_odt, zipfile.ZipFile(generated_odt) as z_odt:
            for name in z_odt.namelist():
                if name != 'content.xml':
                    continue
                content_bytes = z_odt.read(name)
                # check extra namespace is present
                assert b' xmlns:ooow="http://openoffice.org/2004/writer" ' in content_bytes
                # check it's valid XML
                ET.tostring(ET.XML(content_bytes))

    resp = login(get_app(pub), username='foo', password='foo').get(form_location)
    resp = resp.form.submit('button_export_to')
    resp = resp.follow()  # $form/$id/create_doc
    with mock.patch('wcs.wf.export_to_model.get_formdata_template_context') as get_context_1:
        with mock.patch('wcs.workflows.get_formdata_template_context') as get_context_never:
            get_context_1.return_value = {}
            get_context_never.return_value = {}
            resp = resp.follow()  # $form/$id/create_doc/
            # substitution variables are computed only one :
            assert get_context_1.call_count == 1
            assert get_context_never.call_count == 0

    export_to.attach_to_history = True
    wf.store()

    resp = login(get_app(pub), username='foo', password='foo').get(form_location)
    resp = resp.form.submit('button_export_to')
    assert resp.location == form_location + '#action-zone'
    resp = resp.follow()  # back to form page

    resp = resp.click(odt_template)
    assert resp.location.endswith('/' + odt_template)
    resp = resp.follow()
    assert resp.content_type == 'application/octet-stream'
    with open(os.path.join(os.path.dirname(__file__), '..', 'template-out.odt'), 'rb') as f:
        assert_equal_zip(io.BytesIO(resp.body), f)

    # change file content
    upload = QuixoteUpload('/foo/test.xml', content_type='application/xml')
    upload.fp = io.BytesIO()
    upload.fp.write(b'<t>HELLO NEW WORLD</t>')
    upload.fp.seek(0)
    export_to.model_file = UploadedFile(pub.app_dir, None, upload)
    wf.store()

    resp = login(get_app(pub), username='foo', password='foo').get(form_location)
    resp = resp.form.submit('button_export_to')
    assert resp.location == form_location + '#action-zone'
    resp = resp.follow()  # back to form page

    with open(os.path.join(os.path.dirname(__file__), '..', 'template-out.odt'), 'rb') as f:
        body = resp.click(odt_template, index=0).follow().body
        assert_equal_zip(io.BytesIO(body), f)
    assert resp.click('test.xml', index=0).follow().body == b'<t>HELLO NEW WORLD</t>'


def test_formdata_generated_document_odt_download_with_substitution_variable(pub):
    create_user_and_admin(pub)
    wf = Workflow(name='status')
    st1 = wf.add_status('Status1', 'st1')
    export_to = st1.add_action('export_to_model', id='_export_to')
    export_to.convert_to_pdf = False
    export_to.label = 'create doc'
    export_to.varname = 'created_doc'
    template_filename = os.path.join(os.path.dirname(__file__), '..', 'template.odt')
    with open(template_filename, 'rb') as fd:
        template = fd.read()
    upload = QuixoteUpload('/foo/template.odt', content_type='application/octet-stream')
    upload.fp = io.BytesIO()
    upload.fp.write(template)
    upload.fp.seek(0)
    export_to.model_file = UploadedFile(pub.app_dir, None, upload)
    export_to.by = ['_submitter']
    wf.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.workflow_id = wf.id
    formdef.fields = [fields.TextField(id='0', label='comment', varname='comment')]
    formdef.store()
    formdef.data_class().wipe()

    resp = login(get_app(pub), username='foo', password='foo').get('/test/')
    resp.form['f0'] = 'Hello\n\nWorld.'
    resp = resp.forms[0].submit('submit')
    assert 'Check values then click submit.' in resp.text
    resp = resp.forms[0].submit('submit')
    assert resp.status_int == 302
    form_location = resp.location
    resp = resp.follow()
    assert 'The form has been recorded' in resp.text

    resp = resp.form.submit('button_export_to')

    resp = resp.follow()  # $form/$id/create_doc
    resp = resp.follow()  # $form/$id/create_doc/
    with open(os.path.join(os.path.dirname(__file__), '..', 'template-out.odt'), 'rb') as f:
        assert_equal_zip(io.BytesIO(resp.body), f)

    export_to.attach_to_history = True
    wf.store()

    resp = login(get_app(pub), username='foo', password='foo').get(form_location)
    resp = resp.form.submit('button_export_to')
    assert resp.location == form_location + '#action-zone'
    resp = resp.follow()  # back to form page

    resp = resp.click('template.odt')
    assert resp.location.endswith('/template.odt')
    response1 = resp = resp.follow()
    assert resp.content_type == 'application/octet-stream'
    with open(os.path.join(os.path.dirname(__file__), '..', 'template-out.odt'), 'rb') as f:
        assert_equal_zip(io.BytesIO(resp.body), f)

    # change file
    upload = QuixoteUpload('/foo/test.xml', content_type='application/xml')
    upload.fp = io.BytesIO()
    upload.fp.write(b'<t>HELLO NEW WORLD</t>')
    upload.fp.seek(0)
    export_to.model_file = UploadedFile(pub.app_dir, None, upload)
    wf.store()

    resp = login(get_app(pub), username='foo', password='foo').get(form_location)
    resp = resp.form.submit('button_export_to')
    assert resp.location == form_location + '#action-zone'
    resp = resp.follow()  # back to form page

    with open(os.path.join(os.path.dirname(__file__), '..', 'template-out.odt'), 'rb') as f:
        body = resp.click('template.odt', index=0).follow().body
        assert_equal_zip(io.BytesIO(body), f)
    response2 = resp.click('test.xml', index=0).follow()
    assert response2.body == b'<t>HELLO NEW WORLD</t>'
    # Test attachment substitution variables
    variables = formdef.data_class().select()[0].get_substitution_variables()
    assert 'attachments' in variables
    attachments = variables['attachments']
    assert attachments is not None
    file1 = attachments.created_doc
    assert file1.content == response2.body
    assert file1.b64_content == base64.b64encode(response2.body)
    assert file1.content_type == response2._headers['content-type']
    content_disposition = response2._headers['content-disposition']
    assert len(content_disposition.split(';')) == 2
    assert content_disposition.split(';')[0] == 'attachment'
    assert response2.request.environ['PATH_INFO'].endswith(file1.filename)

    resp = login(get_app(pub), username='admin', password='admin').get(file1.url).follow()
    assert file1.content == resp.body
    assert file1.b64_content == base64.b64encode(resp.body)
    assert file1.content_type == resp._headers['content-type']
    content_disposition = resp._headers['content-disposition']
    assert len(content_disposition.split(';')) == 2
    assert content_disposition.split(';')[0] == 'attachment'
    assert resp.request.environ['PATH_INFO'].endswith(file1.filename)

    file2 = attachments.created_doc[0]
    assert file2.content == response1.body
    assert file2.b64_content == base64.b64encode(response1.body)
    assert file2.content_type == response1._headers['content-type']
    content_disposition = response1._headers['content-disposition']
    assert len(content_disposition.split(';')) == 2
    assert content_disposition.split(';')[0] == 'attachment'
    assert response1.request.environ['PATH_INFO'].endswith(file2.filename)

    resp = login(get_app(pub), username='admin', password='admin').get(file2.url).follow()
    assert file2.content == resp.body
    assert file2.b64_content == base64.b64encode(resp.body)
    assert file2.content_type == resp._headers['content-type']
    content_disposition = resp._headers['content-disposition']
    assert len(content_disposition.split(';')) == 2
    assert content_disposition.split(';')[0] == 'attachment'
    assert resp.request.environ['PATH_INFO'].endswith(file2.filename)


def test_formdata_generated_document_ods_download(pub):
    create_user(pub)
    wf = Workflow(name='status')
    st1 = wf.add_status('Status1', 'st1')
    export_to = st1.add_action('export_to_model', id='_export_to')
    export_to.convert_to_pdf = False
    export_to.label = 'create doc'
    template_filename = os.path.join(os.path.dirname(__file__), '..', 'template.ods')
    with open(template_filename, 'rb') as fd:
        template = fd.read()
    upload = QuixoteUpload('/foo/template.ods', content_type='application/octet-stream')
    upload.fp = io.BytesIO()
    upload.fp.write(template)
    upload.fp.seek(0)
    export_to.model_file = UploadedFile(pub.app_dir, None, upload)
    export_to.by = ['_submitter']
    wf.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test_formdata_generated_document_ods_download'
    formdef.url_name = 'test'
    formdef.workflow_id = wf.id
    formdef.fields = []
    formdef.store()
    formdef.data_class().wipe()

    resp = login(get_app(pub), username='foo', password='foo').get('/test/')
    resp = resp.forms[0].submit('submit')  # -> validation
    resp = resp.forms[0].submit('submit')  # -> submit
    resp = resp.follow()

    resp = resp.form.submit('button_export_to')

    resp = resp.follow()  # $form/$id/create_doc
    resp = resp.follow()  # $form/$id/create_doc/
    with io.BytesIO(resp.body) as generated_ods:
        with zipfile.ZipFile(generated_ods) as z_ods:
            for name in z_ods.namelist():
                if name != 'content.xml':
                    continue
                content_bytes = z_ods.read(name)
                assert b' xmlns:of="urn:oasis:names:tc:opendocument:xmlns:of:1.2" ' in content_bytes
                assert b'>test_formdata_generated_document_ods_download<' in content_bytes
                # check it's valid XML
                ET.tostring(ET.XML(content_bytes))


@pytest.mark.skipif(transform_to_pdf is None, reason='libreoffice not found')
def test_formdata_generated_document_odt_to_pdf_download(pub):
    create_user(pub)
    wf = Workflow(name='status')
    st1 = wf.add_status('Status1', 'st1')
    export_to = st1.add_action('export_to_model', id='_export_to')
    export_to.label = 'create doc'
    export_to.varname = 'created_doc'
    template_filename = os.path.join(os.path.dirname(__file__), '..', 'template.odt')
    with open(template_filename, 'rb') as fd:
        template = fd.read()
    upload = QuixoteUpload('/foo/template.odt', content_type='application/octet-stream')
    upload.fp = io.BytesIO()
    upload.fp.write(template)
    upload.fp.seek(0)
    export_to.model_file = UploadedFile(pub.app_dir, None, upload)
    export_to.by = ['_submitter']
    export_to.convert_to_pdf = True
    wf.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.workflow_id = wf.id
    formdef.fields = []
    formdef.store()
    formdef.data_class().wipe()

    resp = login(get_app(pub), username='foo', password='foo').get('/test/')
    resp = resp.forms[0].submit('submit')
    assert 'Check values then click submit.' in resp.text
    resp = resp.forms[0].submit('submit')
    assert resp.status_int == 302
    form_location = resp.location
    resp = resp.follow()
    assert 'The form has been recorded' in resp.text

    resp = resp.form.submit('button_export_to')

    resp = resp.follow()  # $form/$id/create_doc
    resp = resp.follow()  # $form/$id/create_doc/
    assert resp.content_type == 'application/pdf'
    assert b'PDF' in resp.body

    export_to.attach_to_history = True
    wf.store()

    resp = login(get_app(pub), username='foo', password='foo').get(form_location)
    resp = resp.form.submit('button_export_to')
    assert resp.location == form_location + '#action-zone'
    resp = resp.follow()  # back to form page

    resp = resp.click('template.pdf')
    assert resp.location.endswith('/template.pdf')
    resp = resp.follow()
    assert resp.content_type == 'application/pdf'
    content_disposition = resp._headers['content-disposition']
    assert len(content_disposition.split(';')) == 2
    assert content_disposition.split(';')[0] == 'inline'
    assert resp.body.startswith(b'%PDF-')


@pytest.mark.skipif(transform_to_pdf is None, reason='libreoffice not found')
def test_formdata_generated_document_odt_to_pdf_download_push_to_portfolio(
    pub, fargo_url, fargo_secret, caplog
):
    user = create_user(pub)
    user.name = 'Foo Baré'
    user.store()

    pub.cfg['debug'] = {}
    pub.write_cfg()
    wf = Workflow(name='status')
    st1 = wf.add_status('Status1', 'st1')
    export_to = st1.add_action('export_to_model', id='_export_to')
    export_to.label = 'create doc'
    export_to.varname = 'created_doc'
    template_filename = os.path.join(os.path.dirname(__file__), '..', 'template.odt')
    with open(template_filename, 'rb') as fd:
        template = fd.read()
    upload = QuixoteUpload('/foo/template.odt', content_type='application/octet-stream')
    upload.fp = io.BytesIO()
    upload.fp.write(template)
    upload.fp.seek(0)
    export_to.model_file = UploadedFile(pub.app_dir, None, upload)
    export_to.by = ['_submitter']
    export_to.convert_to_pdf = True
    export_to.push_to_portfolio = True
    wf.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.workflow_id = wf.id
    formdef.fields = []
    formdef.store()
    formdef.data_class().wipe()

    resp = login(get_app(pub), username='foo', password='foo').get('/test/')
    resp = resp.forms[0].submit('submit')
    assert 'Check values then click submit.' in resp.text
    resp = resp.forms[0].submit('submit')
    assert resp.status_int == 302
    form_location = resp.location
    resp = resp.follow()
    assert 'The form has been recorded' in resp.text

    with responses.RequestsMock() as rsps:
        rsps.post('http://fargo.example.net/api/documents/push/', body='null')
        resp = resp.form.submit('button_export_to')
        assert len(rsps.calls) == 1

    resp = resp.follow()  # $form/$id/create_doc
    resp = resp.follow()  # $form/$id/create_doc/
    assert resp.content_type == 'application/pdf'
    assert b'PDF' in resp.body

    resp = login(get_app(pub), username='foo', password='foo').get(form_location)
    with responses.RequestsMock() as rsps:
        rsps.post(
            'http://fargo.example.net/api/documents/push/', status=400, json={'code': 'document-exists'}
        )
        resp = resp.form.submit('button_export_to')
        assert len(rsps.calls) == 1
        error = LoggedError.select()[0]
        assert error.summary.startswith("file 'template.pdf' failed to be pushed to portfolio of 'Foo")
        assert 'status: 400' in error.summary
        assert "payload: {'code': 'document-exists'}" in error.summary

    # failed to push to portfolio, but document is here
    resp = resp.follow()  # $form/$id/create_doc
    resp = resp.follow()  # $form/$id/create_doc/
    assert resp.content_type == 'application/pdf'
    assert b'PDF' in resp.body

    export_to.attach_to_history = True
    wf.store()

    resp = login(get_app(pub), username='foo', password='foo').get(form_location)
    with responses.RequestsMock() as rsps:
        rsps.post('http://fargo.example.net/api/documents/push/', body='null')
        resp = resp.form.submit('button_export_to')
        assert len(rsps.calls) == 1
        assert rsps.calls[0].request.url.startswith('http://fargo.example.net/api/documents/push/')
        payload = json.loads(rsps.calls[0].request.body)
        assert payload['file_name'] == 'template.pdf'
        assert payload['user_email'] == 'foo@localhost'
        assert payload['origin'] == 'example.net'
        assert base64.decodebytes(force_bytes(payload['file_b64_content'])).startswith(b'%PDF')
    assert resp.location == form_location + '#action-zone'
    resp = resp.follow()  # back to form page

    resp = resp.click('template.pdf')
    assert resp.location.endswith('/template.pdf')
    resp = resp.follow()
    assert resp.content_type == 'application/pdf'
    assert resp.body.startswith(b'%PDF-')


def test_formdata_generated_document_non_interactive(pub):
    create_user(pub)
    wf = Workflow(name='status')
    st1 = wf.add_status('Status1', 'st1')
    export_to = st1.add_action('export_to_model', id='_export_to')
    export_to.convert_to_pdf = False
    export_to.method = 'non-interactive'
    template_filename = os.path.join(os.path.dirname(__file__), '..', 'template.odt')
    with open(template_filename, 'rb') as fd:
        template = fd.read()
    upload = QuixoteUpload('/foo/template.odt', content_type='application/octet-stream')
    upload.fp = io.BytesIO()
    upload.fp.write(template)
    upload.fp.seek(0)
    export_to.model_file = UploadedFile(pub.app_dir, None, upload)
    export_to.attach_to_history = True

    jump = st1.add_action('jump')
    jump.status = 'st2'

    wf.add_status('Status2', 'st2')

    wf.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.workflow_id = wf.id
    formdef.fields = [fields.TextField(id='0', label='comment', varname='comment')]
    formdef.store()
    formdef.data_class().wipe()

    resp = login(get_app(pub), username='foo', password='foo').get('/test/')
    resp.form['f0'] = 'Hello\n\nWorld.'
    resp = resp.forms[0].submit('submit')
    assert 'Check values then click submit.' in resp.text
    resp = resp.forms[0].submit('submit')
    assert resp.status_int == 302
    resp = resp.follow()
    assert 'The form has been recorded' in resp.text

    resp = resp.click('template.odt')
    assert resp.location.endswith('/template.odt')
    resp = resp.follow()
    assert resp.content_type == 'application/octet-stream'
    with open(os.path.join(os.path.dirname(__file__), '..', 'template-out.odt'), 'rb') as f:
        assert_equal_zip(io.BytesIO(resp.body), f)

    assert formdef.data_class().count() == 1
    assert formdef.data_class().select()[0].status == 'wf-st2'


def test_formdata_generated_document_to_backoffice_field(pub):
    create_user_and_admin(pub)
    wf = Workflow(name='status')
    wf.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(wf)
    wf.backoffice_fields_formdef.fields = [
        fields.FileField(id='bo1', label='bo field 1'),
        fields.StringField(id='bo2', label='bo field 2'),
    ]

    st1 = wf.add_status('Status1', 'st1')
    export_to = st1.add_action('export_to_model', id='_export_to')
    export_to.convert_to_pdf = False
    export_to.method = 'non-interactive'
    template_filename = os.path.join(os.path.dirname(__file__), '..', 'template.odt')
    with open(template_filename, 'rb') as fd:
        template = fd.read()
    upload = QuixoteUpload('/foo/template.odt', content_type='application/octet-stream')
    upload.fp = io.BytesIO()
    upload.fp.write(template)
    upload.fp.seek(0)
    export_to.model_file = UploadedFile(pub.app_dir, None, upload)
    export_to.attach_to_history = True
    export_to.backoffice_filefield_id = 'bo1'

    assert export_to.get_backoffice_filefield_options() == [('bo1', 'bo field 1', 'bo1')]

    jump = st1.add_action('jump')
    jump.status = 'st2'
    wf.add_status('Status2', 'st2')
    wf.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.workflow_id = wf.id
    formdef.fields = [fields.TextField(id='0', label='comment', varname='comment')]
    formdef.store()
    formdef.data_class().wipe()

    resp = login(get_app(pub), username='foo', password='foo').get('/test/')
    resp.form['f0'] = 'Hello\n\nWorld.'
    resp = resp.forms[0].submit('submit')
    assert 'Check values then click submit.' in resp.text
    resp = resp.forms[0].submit('submit')
    assert resp.status_int == 302
    resp = resp.follow()
    assert 'The form has been recorded' in resp.text

    # get the two generated files from backoffice: in backoffice fields
    # (export_to.backoffice_filefield_id), and in history (export_to.attach_to_history)
    for index in (0, 1):
        resp = login(get_app(pub), username='admin', password='admin').get('/backoffice/management/test/1/')
        resp = resp.click('template.odt', index=index)
        assert resp.location.endswith('/template.odt')
        resp = resp.follow()
        assert resp.content_type == 'application/octet-stream'
        with open(os.path.join(os.path.dirname(__file__), '..', 'template-out.odt'), 'rb') as f:
            assert_equal_zip(io.BytesIO(resp.body), f)

    assert formdef.data_class().count() == 1
    assert formdef.data_class().select()[0].status == 'wf-st2'


def test_formdata_generated_document_in_private_history(pub):
    user = create_user(pub)

    pub.role_class.wipe()
    role = pub.role_class(name='xxx')
    role.allows_backoffice_access = True
    role.store()

    user.roles = [role.id]
    user.store()

    wf = Workflow(name='status')
    st0 = wf.add_status('Status0', 'st0')
    st1 = wf.add_status('Status1', 'st1')
    export_to = st1.add_action('export_to_model', id='_export_to')
    export_to.label = 'create doc'
    upload = QuixoteUpload('/foo/test.xml', content_type='application/xml')
    upload.fp = io.BytesIO()
    upload.fp.write(b'<t>HELLO WORLD</t>')
    upload.fp.seek(0)
    export_to.model_file = UploadedFile(pub.app_dir, None, upload)
    export_to.attach_to_history = True
    export_to.by = ['_submitter']

    st2 = wf.add_status('Status2', 'st2')

    jump1 = st0.add_action('choice', id='_jump1')
    jump1.label = 'Jump 1'
    jump1.by = ['_receiver']
    jump1.status = st1.id

    jump2 = st1.add_action('choice', id='_jump2')
    jump2.label = 'Jump 2'
    jump2.by = ['_receiver']
    jump2.status = st2.id

    wf.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.workflow_id = wf.id
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.fields = []
    formdef.store()
    formdef.data_class().wipe()

    resp = login(get_app(pub), username='foo', password='foo').get('/test/')
    resp = resp.forms[0].submit('submit')
    assert 'Check values then click submit.' in resp.text
    resp = resp.forms[0].submit('submit')
    assert resp.status_int == 302
    resp = resp.follow()
    assert 'The form has been recorded' in resp.text

    resp = resp.form.submit('button_jump1')
    resp = resp.follow()

    resp = resp.form.submit('button_export_to')
    resp = resp.follow()
    assert 'Form exported in a model' in resp.text

    resp = resp.form.submit('button_jump2')
    resp = resp.follow()

    # limit visibility of status with document
    st1.visibility = ['_receiver']
    wf.store()

    formdata = formdef.data_class().select()[0]
    resp = login(get_app(pub), username='foo', password='foo').get(formdata.get_url())
    assert 'Form exported in a model' not in resp.text

    # check status is visible in backoffice
    resp = login(get_app(pub), username='foo', password='foo').get(formdata.get_url(backoffice=True))
    assert 'visibility-off' in resp.text
    assert 'Form exported in a model' in resp.text


def test_formdata_empty_form_action(pub):
    create_user(pub)
    wf = Workflow(name='status')
    st1 = wf.add_status('Status1', 'st1')
    wf.add_status('Status2', 'st2')

    display_form = st1.add_action('form', id='_x')
    display_form.by = ['_submitter']
    display_form.varname = 'xxx'

    jump1 = st1.add_action('choice', id='_jump1')
    jump1.label = 'Jump 1'
    jump1.by = ['_submitter']
    jump1.status = 'st2'

    wf.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.workflow_id = wf.id
    formdef.fields = []
    formdef.store()
    formdef.data_class().wipe()

    resp = login(get_app(pub), username='foo', password='foo').get('/test/')
    resp = resp.forms[0].submit('submit')
    assert 'Check values then click submit.' in resp.text
    resp = resp.forms[0].submit('submit')
    assert resp.status_int == 302
    resp = resp.follow()
    assert 'The form has been recorded' in resp.text

    resp = resp.form.submit('button_jump1')
    assert formdef.data_class().select()[0].status == 'wf-st2'


def test_formdata_form_file_download(pub):
    create_user(pub)
    wf = Workflow(name='status')
    st1 = wf.add_status('Status1', 'st1')

    display_form = st1.add_action('form', id='_x')
    display_form.by = ['_submitter']
    display_form.varname = 'xxx'
    display_form.formdef = WorkflowFormFieldsFormDef(item=display_form)
    display_form.formdef.fields.append(fields.FileField(id='1', label='File', varname='yyy'))

    wf.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.workflow_id = wf.id
    formdef.fields = []
    formdef.store()
    formdef.data_class().wipe()

    resp = login(get_app(pub), username='foo', password='foo').get('/test/')
    resp = resp.forms[0].submit('submit')
    assert 'Check values then click submit.' in resp.text
    resp = resp.forms[0].submit('submit')
    assert resp.status_int == 302
    resp = resp.follow()
    assert 'The form has been recorded' in resp.text

    assert 'qommon.fileupload.js' in resp.text
    resp.forms[0][f'fxxx_{display_form.id}_1$file'] = Upload('test.txt', b'foobar', 'text/plain')
    resp = resp.forms[0].submit('submit')

    assert formdef.data_class().count() == 1
    formdata = formdef.data_class().select()[0]
    assert 'xxx_var_yyy_raw' in formdata.workflow_data

    download = resp.test_app.get(urllib.parse.urljoin(resp.location, 'files/form-xxx-yyy/test.txt'))
    assert download.content_type == 'text/plain'
    assert download.body == b'foobar'

    # check there's no error on a formdata without workflow_data
    formdata.workflow_data = None
    formdata.store()
    resp.test_app.get(urllib.parse.urljoin(resp.location, 'files/form-xxx-yyy/test.txt'), status=404)

    # go back to the status page, this will exercise the substitution variables
    # codepath.
    resp = resp.follow()


@pytest.mark.parametrize('clamd_returncode', [0, 1])
def test_formdata_form_file_download_clamd(pub, clamd_returncode):
    pub.load_site_options()
    if not pub.site_options.has_section('options'):
        pub.site_options.add_section('options')
    pub.site_options.set('options', 'enable-clamd', 'true')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)
    pub.load_site_options()
    create_user(pub)

    wf = Workflow(name='status')
    wf.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(wf)
    wf.backoffice_fields_formdef.fields = [
        fields.FileField(id='bo1', label='bo field 1'),
    ]
    st1 = wf.add_status('Status1', 'st1')
    st2 = wf.add_status('Status2', 'st2')
    display_form = st1.add_action('form', id='_x')
    display_form.by = ['_submitter']
    display_form.varname = 'xxx'
    display_form.formdef = WorkflowFormFieldsFormDef(item=display_form)
    display_form.formdef.fields.append(fields.FileField(id='1', label='File', varname='yyy'))
    choice = st1.add_action('choice')
    choice.by = ['_submitter']
    choice.status = st2.id
    choice.label = 'Jump 1'
    comment = st2.add_action('register-comment', id='_comment')
    comment.comment = 'Foo'
    comment.attachments = ['{{xxx_var_yyy_raw}}']
    wf.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.workflow_id = wf.id
    formdef.fields = []
    formdef.store()
    formdef.data_class().wipe()

    resp = login(get_app(pub), username='foo', password='foo').get('/test/')
    resp = resp.forms[0].submit('submit')
    assert 'Check values then click submit.' in resp.text
    resp = resp.forms[0].submit('submit')
    assert resp.status_int == 302
    resp = resp.follow()
    assert 'The form has been recorded' in resp.text

    resp.forms[0][f'fxxx_{display_form.id}_1$file'] = Upload('test.txt', b'foobar', 'text/plain')
    with mock.patch('wcs.clamd.subprocess') as subp:
        attrs = {'run.return_value': mock.Mock(returncode=clamd_returncode, stdout='stdout')}
        subp.configure_mock(**attrs)
        resp = resp.forms[0].submit('button0')

    resp = resp.follow()
    assert resp.status_code == 200
    if clamd_returncode == 1:
        assert 'A malware was found in this file.' in resp.pyquery('.malware-file').text()

    assert len(resp.pyquery('.wf-attachment a')) == 1
    anchor = resp.pyquery('.wf-attachment a')[0]

    assert formdef.data_class().count() == 1
    formdata = formdef.data_class().select()[0]
    url = urllib.parse.urljoin(formdata.get_url(), anchor.attrib['href'])
    resp = login(get_app(pub), username='foo', password='foo').get(url, status=302)
    resp = resp.follow()
    assert resp.content_type == 'text/plain'
    assert resp.body == b'foobar'


def test_formdata_workflow_form_prefill(pub):
    create_user(pub)
    wf = Workflow(name='status')
    st1 = wf.add_status('Status1', 'st1')

    display_form = st1.add_action('form', id='_x')
    display_form.by = ['_submitter']
    display_form.varname = 'xxx'
    display_form.formdef = WorkflowFormFieldsFormDef(item=display_form)
    display_form.formdef.fields.append(
        fields.StringField(id='1', label='blah', varname='yyy', prefill={'type': 'user', 'value': 'email'})
    )

    wf.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.workflow_id = wf.id
    formdef.fields = []
    formdef.store()
    formdef.data_class().wipe()

    resp = login(get_app(pub), username='foo', password='foo').get('/test/')
    resp = resp.forms[0].submit('submit')
    assert 'Check values then click submit.' in resp.text
    resp = resp.forms[0].submit('submit')
    assert resp.status_int == 302
    resp = resp.follow()
    assert 'The form has been recorded' in resp.text
    assert resp.forms[0][f'fxxx_{display_form.id}_1'].value == 'foo@localhost'


def test_formdata_workflow_form_prefill_conditional_field(pub):
    create_user(pub)
    wf = Workflow(name='status')
    st1 = wf.add_status('Status1', 'st1')

    display_form = st1.add_action('form', id='_x')
    display_form.by = ['_submitter']
    display_form.varname = 'xxx'
    display_form.formdef = WorkflowFormFieldsFormDef(item=display_form)
    display_form.formdef.fields.append(
        fields.StringField(
            id='1',
            label='blah1',
            varname='yyy1',
            prefill={'type': 'user', 'value': 'email'},
            condition={'type': 'django', 'value': '0'},
        )
    )
    display_form.formdef.fields.append(
        fields.StringField(
            id='2',
            label='blah2',
            varname='yyy2',
            prefill={'type': 'user', 'value': 'email'},
            condition={'type': 'django', 'value': '1'},
        )
    )

    wf.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.workflow_id = wf.id
    formdef.fields = []
    formdef.store()
    formdef.data_class().wipe()

    resp = login(get_app(pub), username='foo', password='foo').get('/test/')
    resp = resp.forms[0].submit('submit')
    assert 'Check values then click submit.' in resp.text
    resp = resp.forms[0].submit('submit')
    assert resp.status_int == 302
    resp = resp.follow()
    assert 'The form has been recorded' in resp.text
    assert resp.forms[0][f'fxxx_{display_form.id}_2'].value == 'foo@localhost'


def test_formdata_workflow_form_prefill_checkbox(pub):
    create_user(pub)
    wf = Workflow(name='status')
    st1 = wf.add_status('Status1', 'st1')

    display_form = st1.add_action('form', id='_x')
    display_form.by = ['_submitter']
    display_form.varname = 'xxx'
    display_form.formdef = WorkflowFormFieldsFormDef(item=display_form)
    display_form.formdef.fields.append(
        fields.BoolField(
            id='1', label='blah', varname='yyy', prefill={'type': 'string', 'value': '{{ True }}'}
        )
    )
    display_form.formdef.fields.append(
        fields.BoolField(
            id='2', label='blah2', varname='zzz', prefill={'type': 'string', 'value': '{{ True }}'}
        )
    )

    wf.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.workflow_id = wf.id
    formdef.fields = []
    formdef.store()
    formdef.data_class().wipe()

    resp = login(get_app(pub), username='foo', password='foo').get('/test/')
    resp = resp.form.submit('submit')
    assert 'Check values then click submit.' in resp
    resp = resp.form.submit('submit').follow()
    assert 'The form has been recorded' in resp
    assert resp.form[f'fxxx_{display_form.id}_1'].checked is True
    assert resp.form[f'fxxx_{display_form.id}_2'].checked is True
    resp.form[f'fxxx_{display_form.id}_1'].checked = False
    resp = resp.form.submit('submit')

    formdata = formdef.data_class().select()[0]
    assert formdata.workflow_data['xxx_var_yyy_raw'] is False
    assert formdata.workflow_data['xxx_var_zzz_raw'] is True


def test_formdata_workflow_form_prefill_autocomplete(pub):
    create_user(pub)

    NamedDataSource.wipe()
    data_source = NamedDataSource(name='foobar')
    data_source.data_source = {'type': 'json', 'value': 'http://local-mock/test'}
    data_source.query_parameter = 'q'
    data_source.id_parameter = 'id'
    data_source.store()

    wf = Workflow(name='status')
    st1 = wf.add_status('Status1', 'st1')

    display_form = st1.add_action('form', id='_x')
    display_form.by = ['_submitter']
    display_form.varname = 'xxx'
    display_form.formdef = WorkflowFormFieldsFormDef(item=display_form)
    display_form.formdef.fields = [
        fields.ItemField(
            id='4',
            label='string',
            data_source={'type': 'foobar'},
            required='optional',
            display_mode='autocomplete',
            prefill={'type': 'string', 'value': '{{ form_var_foo_raw }}'},
        ),
    ]

    wf.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.workflow_id = wf.id
    formdef.fields = [
        fields.ItemField(
            id='0',
            label='string',
            data_source={'type': 'foobar'},
            display_mode='autocomplete',
            required='optional',
            varname='foo',
        ),
    ]
    formdef.store()
    formdef.data_class().wipe()

    resp = login(get_app(pub), username='foo', password='foo').get('/test/')
    with responses.RequestsMock() as rsps:
        data = {'data': [{'id': '1', 'text': 'hello', 'extra': 'foo'}]}
        rsps.get('http://local-mock/test', json=data)

        assert 'data-select2-url=' in resp
        # simulate select2
        resp.form.fields['f0_display'] = Hidden(form=resp.form, tag='input', name='f0_display', pos=10)
        resp.form['f0'].force_value('1')
        resp.form.fields['f0_display'].force_value('foo')
        resp = resp.form.submit('submit')
        assert 'Check values then click submit.' in resp
        resp = resp.form.submit('submit').follow()
        assert 'The form has been recorded' in resp

        # check display value is in form action widget
        assert resp.form[f'fxxx_{display_form.id}_4'].attrs['data-value'] == '1'
        assert resp.form[f'fxxx_{display_form.id}_4'].attrs['data-initial-display-value'] == 'hello'

        # check it is also displayed in a fresh session
        resp = login(get_app(pub), username='foo', password='foo').get(resp.request.url)
        assert resp.form[f'fxxx_{display_form.id}_4'].attrs['data-value'] == '1'
        assert resp.form[f'fxxx_{display_form.id}_4'].attrs['data-initial-display-value'] == 'hello'


def test_formdata_workflow_many_forms(pub):
    create_user(pub)
    wf = Workflow(name='status')
    st1 = wf.add_status('Status1', 'st1')

    # first form
    display_form1 = st1.add_action('form')
    display_form1.by = ['_submitter']
    display_form1.varname = 'xxx'
    display_form1.formdef = WorkflowFormFieldsFormDef(item=display_form1)
    display_form1.formdef.fields = [fields.StringField(id='1', label='blah1')]
    display_form1.hide_submit_button = False

    # second form with same varname
    display_form2 = st1.add_action('form')
    display_form2.by = ['_submitter']
    display_form2.varname = 'xxx'
    display_form2.formdef = WorkflowFormFieldsFormDef(item=display_form2)
    display_form2.formdef.fields = [fields.StringField(id='1', label='blah1')]
    display_form2.hide_submit_button = False

    # third form with live condition
    display_form3 = st1.add_action('form')
    display_form3.by = ['_submitter']
    display_form3.varname = 'yyy'
    display_form3.formdef = WorkflowFormFieldsFormDef(item=display_form3)
    display_form3.hide_submit_button = False
    display_form3.formdef.fields = [
        fields.StringField(id='1', label='Test', varname='str', required='required'),
        fields.StringField(
            id='2',
            label='Test2',
            varname='str2',
            required='required',
            condition={'type': 'django', 'value': 'yyy_var_str == "xxx"'},
        ),
    ]

    wf.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.workflow_id = wf.id
    formdef.fields = [fields.StringField(id='1', label='blah')]
    formdef.store()
    formdef.data_class().wipe()

    app = login(get_app(pub), username='foo', password='foo')
    resp = app.get('/test/')
    resp.form['f1'] = 'foobar'
    resp = resp.forms[0].submit('submit')
    assert 'Check values then click submit.' in resp.text
    resp = resp.forms[0].submit('submit')
    assert resp.status_int == 302
    resp = resp.follow()
    assert 'The form has been recorded' in resp.text
    assert f'fxxx_{display_form1.id}_1' in resp.form.fields
    assert f'fxxx_{display_form2.id}_1' in resp.form.fields
    assert f'fyyy_{display_form3.id}_1' in resp.form.fields
    assert f'fyyy_{display_form3.id}_2' in resp.form.fields
    assert 'submit' in resp.form.fields  # only one submit button

    assert (
        resp.html.find('div', {'data-field-id': f'yyy_{display_form3.id}_1'}).attrs['data-live-source']
        == 'true'
    )
    assert (
        resp.html.find('div', {'data-field-id': f'yyy_{display_form3.id}_2'}).attrs.get('style')
        == 'display: none'
    )
    live_url = resp.html.find('form').attrs['data-live-url']
    resp.form[f'fyyy_{display_form3.id}_1'] = ''
    live_resp = app.post(live_url, params=resp.form.submit_fields())
    assert live_resp.json['result'][f'xxx_{display_form1.id}_1']['visible']
    assert live_resp.json['result'][f'xxx_{display_form2.id}_1']['visible']
    assert live_resp.json['result'][f'yyy_{display_form3.id}_1']['visible']
    assert not live_resp.json['result'][f'yyy_{display_form3.id}_2']['visible']
    resp.form[f'fyyy_{display_form3.id}_1'] = 'xxx'
    live_resp = app.post(live_url, params=resp.form.submit_fields())
    assert live_resp.json['result'][f'xxx_{display_form1.id}_1']['visible']
    assert live_resp.json['result'][f'xxx_{display_form2.id}_1']['visible']
    assert live_resp.json['result'][f'yyy_{display_form3.id}_1']['visible']
    assert live_resp.json['result'][f'yyy_{display_form3.id}_2']['visible']


def test_formdata_named_wscall(http_requests, pub):
    create_user(pub)
    NamedWsCall.wipe()

    wscall = NamedWsCall()
    wscall.name = 'Hello world'
    wscall.request = {'url': 'http://remote.example.net/json'}
    wscall.store()
    assert wscall.slug == 'hello_world'

    wf = Workflow(name='status')
    st1 = wf.add_status('Status1', 'st1')
    comment = st1.add_action('register-comment', id='_comment')
    comment.comment = 'Hello [webservice.hello_world.foo] World'

    display = st1.add_action('displaymsg')
    display.message = 'The form has been recorded and: X[webservice.hello_world.foo]Y'
    display.to = []

    wf.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.workflow_id = wf.id
    formdef.fields = []
    formdef.store()
    formdef.data_class().wipe()

    resp = login(get_app(pub), username='foo', password='foo').get('/test/')
    resp = resp.forms[0].submit('submit')
    assert 'Check values then click submit.' in resp.text
    resp = resp.forms[0].submit('submit')
    assert resp.status_int == 302
    resp = resp.follow()
    assert 'The form has been recorded and: XbarY' in resp.text

    formdata = formdef.data_class().select()[0]
    assert formdata.evolution[0].parts[1].content == '<p>Hello bar World</p>'

    # check with publisher variable in named webservice call
    if not pub.site_options.has_section('variables'):
        pub.site_options.add_section('variables')
    pub.site_options.set('variables', 'example_url', 'http://remote.example.net/')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)

    wscall = NamedWsCall()
    wscall.name = 'Hello world'
    wscall.request = {'url': '[example_url]json'}
    wscall.store()

    formdef.data_class().wipe()

    resp = login(get_app(pub), username='foo', password='foo').get('/test/')
    resp = resp.forms[0].submit('submit')
    assert 'Check values then click submit.' in resp.text
    resp = resp.forms[0].submit('submit')
    assert resp.status_int == 302
    resp = resp.follow()
    assert 'The form has been recorded and: XbarY' in resp.text

    formdata = formdef.data_class().select()[0]
    assert formdata.evolution[0].parts[1].content == '<p>Hello bar World</p>'


def test_formdata_named_wscall_in_conditions(http_requests, pub):
    create_user(pub)
    NamedWsCall.wipe()

    wscall = NamedWsCall()
    wscall.name = 'Hello world'
    wscall.request = {'url': 'http://remote.example.net/json', 'method': 'GET'}
    wscall.store()
    assert wscall.slug == 'hello_world'

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [
        fields.PageField(id='0', label='1st page'),
        fields.PageField(
            id='1',
            label='2nd page',
            condition={'type': 'django', 'value': 'webservice.hello_world.foo == "bar"'},
        ),
        fields.PageField(
            id='2',
            label='3rd page',
            condition={'type': 'django', 'value': 'webservice.hello_world.foo != "bar"'},
        ),
        fields.PageField(
            id='3',
            label='4th page',
            condition={'type': 'django', 'value': 'webservice.hello_world.foo == "bar"'},
        ),
    ]
    formdef.store()
    formdef.data_class().wipe()

    resp = login(get_app(pub), username='foo', password='foo').get('/test/')
    assert '>1st page<' in resp.text
    assert '>2nd page<' in resp.text
    assert '>3rd page<' not in resp.text
    assert '>4th page<' in resp.text
    assert http_requests.count() == 1


def test_formdata_named_wscall_in_comment(pub):
    create_user(pub)
    NamedWsCall.wipe()

    wscall = NamedWsCall()
    wscall.name = 'Hello world'
    wscall.request = {'url': 'http://remote.example.net/json'}
    wscall.store()
    assert wscall.slug == 'hello_world'

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [
        fields.CommentField(id='0', label='Hello X{{ webservice.hello_world.foo }}Y.'),
    ]
    formdef.store()
    formdef.data_class().wipe()

    with responses.RequestsMock() as rsps:
        data = {'foo': 'bar'}
        rsps.get('http://remote.example.net/json', json=data, headers={'WWW-Authenticate': 'headers'})
        resp = get_app(pub).get('/test/')
        assert 'Hello XbarY.' in resp.text

    with responses.RequestsMock() as rsps:
        rsps.get('http://remote.example.net/json', body=ConnectionError('...'))
        resp = get_app(pub).get('/test/')
        assert 'Hello XY.' in resp.text
        assert LoggedError.count() == 0

        wscall.record_on_errors = True
        wscall.store()

        resp = get_app(pub).get('/test/')
        assert 'Hello XY.' in resp.text
        assert LoggedError.count() == 1


def test_formdata_evolution_register_comment_to(pub):
    user = create_user(pub)

    pub.role_class.wipe()
    role1 = pub.role_class(name='role the user does not have')
    role1.store()
    role2 = pub.role_class(name='role the user does have')
    role2.store()
    user.roles = [role2.id]
    user.store()

    wf = Workflow(name='status')
    st1 = wf.add_status('Status1', 'st1')
    comment = st1.add_action('register-comment', id='_comment')
    comment.comment = 'Hello World'
    comment.to = None

    wf.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.workflow_id = wf.id
    formdef.fields = []
    formdef.store()
    formdef.data_class().wipe()

    # register comment to all users
    app = login(get_app(pub), username='foo', password='foo')
    resp = app.get('/test/')
    resp = resp.forms[0].submit('submit')
    assert 'Check values then click submit.' in resp.text
    resp = resp.forms[0].submit('submit')
    assert resp.status_int == 302
    resp = resp.follow()
    assert 'The form has been recorded' in resp.text

    formdata = formdef.data_class().select()[0]
    assert formdata.evolution[0].parts[1].content == '<p>Hello World</p>'
    assert formdata.evolution[0].parts[1].to is None
    resp = app.get('/test/%s/' % formdata.id)
    resp.status_int = 200
    assert resp.html.find('div', {'id': 'evolution-log'}).find('p').text == 'Hello World'

    # register comment to other users
    formdef.data_class().wipe()
    comment.to = [role1.id]
    wf.store()

    resp = app.get('/test/')
    resp = resp.forms[0].submit('submit')
    assert 'Check values then click submit.' in resp.text
    resp = resp.forms[0].submit('submit')
    assert resp.status_int == 302
    resp = resp.follow()
    assert 'The form has been recorded' in resp.text

    formdata = formdef.data_class().select()[0]
    assert formdata.evolution[0].parts[1].content == '<p>Hello World</p>'
    assert formdata.evolution[0].parts[1].to == [role1.id]
    resp = app.get('/test/%s/' % formdata.id)
    resp.status_int = 200
    assert not resp.html.find('div', {'id': 'evolution-log'}).find('p')

    # register comment to this user
    formdef.data_class().wipe()
    comment.to = [role2.id]
    wf.store()

    resp = app.get('/test/')
    resp = resp.forms[0].submit('submit')
    assert 'Check values then click submit.' in resp.text
    resp = resp.forms[0].submit('submit')
    assert resp.status_int == 302
    resp = resp.follow()
    assert 'The form has been recorded' in resp.text

    formdata = formdef.data_class().select()[0]
    assert formdata.evolution[0].parts[1].content == '<p>Hello World</p>'
    assert formdata.evolution[0].parts[1].to == [role2.id]
    resp = app.get('/test/%s/' % formdata.id)
    resp.status_int = 200
    assert resp.html.find('div', {'id': 'evolution-log'}).find('p').text == 'Hello World'


def test_formdata_evolution_register_comment_to_with_attachment(pub):
    user = create_user(pub)

    pub.role_class.wipe()
    role1 = pub.role_class(name='role the user does not have')
    role1.store()
    role2 = pub.role_class(name='role the user does have')
    role2.store()
    user.roles = [role2.id]
    user.store()

    wf = Workflow(name='status')
    st1 = wf.add_status('Status1', 'st1')

    comment = st1.add_action('register-comment', id='1')
    comment.comment = 'Hello all'
    comment.attachments = ['{{form_var_file1_raw}}']
    comment.to = None

    comment = st1.add_action('register-comment', id='2')
    comment.comment = 'Hello role1'
    comment.attachments = ['{{form_var_file2_raw}}']
    comment.to = [role1.id]

    comment = st1.add_action('register-comment', id='3')
    comment.comment = 'Hello role2'
    comment.attachments = ['{{form_var_file3_raw}}']
    comment.to = [role2.id]

    wf.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.workflow_id = wf.id
    formdef.fields = [
        fields.FileField(id='1', label='File1', varname='file1'),
        fields.FileField(id='2', label='File2', varname='file2'),
        fields.FileField(id='3', label='File3', varname='file3'),
    ]
    formdef.store()
    formdef.data_class().wipe()

    app = login(get_app(pub), username='foo', password='foo')
    resp = app.get('/test/')
    resp.forms[0]['f1$file'] = Upload('to-all.txt', b'foobar', 'text/plain')
    resp.forms[0]['f2$file'] = Upload('to-role1.txt', b'foobar', 'text/plain')
    resp.forms[0]['f3$file'] = Upload('to-role2.txt', b'foobar', 'text/plain')
    resp = resp.forms[0].submit('submit')
    assert 'Check values then click submit.' in resp.text
    resp = resp.forms[0].submit('submit')
    assert resp.status_int == 302
    resp = resp.follow()
    assert 'The form has been recorded' in resp.text

    formdata = formdef.data_class().select()[0]

    resp = app.get('/test/%s/' % formdata.id)
    resp.status_int = 200
    assert [x.a.text for x in resp.html.find_all('p', {'class': 'wf-attachment'})] == [
        'to-all.txt',
        'to-role2.txt',
    ]


def test_include_authors_in_form_history(pub):
    user, admin = create_user_and_admin(pub)
    pub.role_class.wipe()
    role = pub.role_class(name='xxx')
    role.store()
    admin.roles = [role.id]
    admin.store()

    wf = Workflow(name='status')
    st1 = wf.add_status('Status1')
    st2 = wf.add_status('Status2')
    jump = st1.add_action('choice')
    jump.label = 'Jump'
    jump.by = ['_receiver']
    jump.status = st2.id
    wf.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.workflow_id = wf.id
    formdef.fields = []
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.store()
    formdef.data_class().wipe()

    formdata = formdef.data_class()()
    formdata.user_id = user.id
    formdata.just_created()
    formdata.store()

    resp = login(get_app(pub), username='admin', password='admin').get(formdata.get_backoffice_url())
    resp = resp.forms['wf-actions'].submit('button1').follow()
    assert [x.text.strip() for x in resp.pyquery('#evolutions .user')] == ['foo@localhost', 'admin@localhost']

    resp = login(get_app(pub), username='foo', password='foo').get(formdata.get_url())
    assert [x.text.strip() for x in resp.pyquery('#evolutions .user')] == ['foo@localhost', 'admin@localhost']

    if not pub.site_options.has_section('variables'):
        pub.site_options.add_section('variables')

    pub.site_options.set('variables', 'include_authors_in_form_history', 'False')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)

    resp = login(get_app(pub), username='foo', password='foo').get(formdata.get_url())
    assert [x.text.strip() for x in resp.pyquery('#evolutions .user')] == []

    resp = login(get_app(pub), username='admin', password='admin').get(formdata.get_backoffice_url())
    assert [x.text.strip() for x in resp.pyquery('#evolutions .user')] == ['foo@localhost', 'admin@localhost']


def test_include_authors_in_form_history_silent_action(pub):
    user, admin = create_user_and_admin(pub)
    pub.role_class.wipe()
    role = pub.role_class(name='xxx')
    role.store()
    admin.roles = [role.id]
    admin.store()

    wf = Workflow(name='status')
    wf.add_status('Status1')
    global_action = wf.add_global_action('create formdata')
    display_form = global_action.add_action('form')
    display_form.by = ['_receiver']
    display_form.varname = 'blah'
    display_form.hide_submit_button = False
    display_form.formdef = WorkflowFormFieldsFormDef(item=display_form)
    display_form.formdef.fields = [
        fields.StringField(id='1', label='Test', varname='str', required='required'),
    ]
    message = global_action.add_action('register-comment')
    message.comment = 'MESSAGE {{ form_workflow_form_blah_var_str }}'
    message.to = ['_receiver']
    global_action.triggers[0].roles = ['_receiver']
    wf.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.workflow_id = wf.id
    formdef.fields = []
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.store()
    formdef.data_class().wipe()

    formdata = formdef.data_class()()
    formdata.user_id = user.id
    formdata.just_created()
    formdata.store()

    resp = login(get_app(pub), username='admin', password='admin').get(formdata.get_backoffice_url())
    resp = resp.forms['wf-actions'].submit('button-action-1').follow()
    resp.forms['wf-actions'][f'fblah_{display_form.id}_1'] = 'test'
    resp = resp.forms['wf-actions'].submit('submit').follow()
    assert 'MESSAGE test' in resp.text

    # user sees the admin did something
    resp = login(get_app(pub), username='foo', password='foo').get(formdata.get_url())
    assert resp.pyquery('#evolutions li').length == 2
    assert [x.text.strip() for x in resp.pyquery('#evolutions .user')] == ['foo@localhost', 'admin@localhost']
    assert 'MESSAGE test' not in resp.text

    if not pub.site_options.has_section('variables'):
        pub.site_options.add_section('variables')

    # hide action authors, the user shouldn't see an entry for the admin action
    pub.site_options.set('variables', 'include_authors_in_form_history', 'False')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)

    resp = login(get_app(pub), username='foo', password='foo').get(formdata.get_url())
    assert resp.pyquery('#evolutions li').length == 1
    assert 'MESSAGE test' not in resp.text


def test_buzy_object(pub):
    create_user(pub)

    wf = Workflow(name='status')
    wf.add_status('Status1', 'st1')
    wf.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.workflow_id = wf.id
    formdef.fields = []
    formdef.store()
    formdef.data_class().wipe()

    app = login(get_app(pub), username='foo', password='foo')
    resp = app.get('/test/')
    resp = resp.forms[0].submit('submit')
    assert 'Check values then click submit.' in resp.text
    resp = resp.forms[0].submit('submit').follow()
    assert 'The form has been recorded' in resp.text

    formdata = formdef.data_class().select()[0]
    formdata.workflow_processing_timestamp = now()
    formdata.store()
    resp = app.get(formdata.get_url())
    assert resp.pyquery('[data-workflow-processing="true"]')
    assert resp.pyquery('.busy-processing').text() == 'Processing...'

    assert app.get(formdata.get_url() + 'check-workflow-progress').json == {'err': 0, 'status': 'processing'}

    formdata.workflow_processing_timestamp = None
    formdata.store()
    assert app.get(formdata.get_url() + 'check-workflow-progress').json == {'err': 0, 'status': 'idle'}
    resp = app.get(formdata.get_url())
    assert not resp.pyquery('[data-workflow-processing="true"]')


def test_submit_async_workflow_processing(pub):
    pub.load_site_options()
    if not pub.site_options.has_section('options'):
        pub.site_options.add_section('options')
    pub.site_options.set('options', 'perform-workflow-as-job', 'true')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)

    create_user(pub)

    wf = Workflow(name='status')
    wf.add_status('Status1', 'st1')
    wf.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.workflow_id = wf.id
    formdef.store()
    formdef.data_class().wipe()

    app = login(get_app(pub), username='foo', password='foo')
    resp = app.get('/test/')
    resp = resp.forms[0].submit('submit')
    assert 'Check values then click submit.' in resp.text
    AfterJob.wipe()
    resp = resp.forms[0].submit('submit').follow()
    assert 'The form has been recorded' in resp.text
    assert AfterJob.count() == 1
    assert AfterJob.select()[0].label == 'Processing'
    assert AfterJob.select()[0].status == 'completed'
    assert not resp.pyquery('[data-workflow-processing="true"]')
    formdef.data_class().wipe()

    # do not let tests run afterjobs synchronously
    with mock.patch('wcs.qommon.publisher.QommonPublisher.process_after_jobs'):
        resp = app.get('/test/')
        resp = resp.forms[0].submit('submit')
        assert 'Check values then click submit.' in resp.text
        AfterJob.wipe()
        resp = resp.forms[0].submit('submit').follow()
        assert 'The form has been recorded' in resp.text
        assert AfterJob.count() == 1
        afterjob = AfterJob.select()[0]
        assert afterjob.label == 'Processing'
        assert afterjob.status == 'registered'
        assert resp.pyquery('[data-workflow-processing="true"]')
        afterjob_id = resp.pyquery('[data-workflow-processing-afterjob-id]').attr[
            'data-workflow-processing-afterjob-id'
        ]
        assert afterjob.id == afterjob_id
        assert resp.pyquery('.busy-processing').text() == 'Processing...'

    formdata = formdef.data_class().select()[0]

    assert app.get(formdata.get_url() + 'check-workflow-progress?job=' + afterjob_id).json == {
        'err': 0,
        'status': 'processing',
        'job': {'status': 'registered', 'url': f'http://example.net/api/jobs/{afterjob.id}/'},
        'url': None,
    }
    pub.after_jobs.append(afterjob)
    pub.process_after_jobs()
    assert app.get(formdata.get_url() + 'check-workflow-progress?job=' + afterjob_id).json == {
        'err': 0,
        'status': 'idle',
        'job': {'status': 'completed', 'url': f'http://example.net/api/jobs/{afterjob.id}/'},
        'url': None,
    }
    resp = app.get(formdata.get_url())
    assert not resp.pyquery('[data-workflow-processing="true"]')


def test_submit_async_workflow_processing_then_redirect(pub):
    pub.load_site_options()
    if not pub.site_options.has_section('options'):
        pub.site_options.add_section('options')
    pub.site_options.set('options', 'perform-workflow-as-job', 'true')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)

    create_user(pub)

    wf = Workflow(name='status')
    st = wf.add_status('Status1', 'st1')
    redirect = st.add_action('redirect_to_url')
    redirect.url = 'https://www.example.org'
    wf.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.workflow_id = wf.id
    formdef.store()
    formdef.data_class().wipe()

    app = login(get_app(pub), username='foo', password='foo')
    resp = app.get('/test/')
    resp = resp.forms[0].submit('submit')
    assert 'Check values then click submit.' in resp.text
    AfterJob.wipe()
    resp = resp.forms[0].submit('submit').follow()
    assert 'The form has been recorded' in resp.text
    assert AfterJob.count() == 1
    assert AfterJob.select()[0].label == 'Processing'
    assert AfterJob.select()[0].status == 'completed'
    assert not resp.pyquery('[data-workflow-processing="true"]')
    formdef.data_class().wipe()

    # do not let tests run afterjobs synchronously
    with mock.patch('wcs.qommon.publisher.QommonPublisher.process_after_jobs'):
        resp = app.get('/test/')
        resp = resp.forms[0].submit('submit')
        assert 'Check values then click submit.' in resp.text
        AfterJob.wipe()
        resp = resp.forms[0].submit('submit').follow()
        assert 'The form has been recorded' in resp.text
        assert AfterJob.count() == 1
        afterjob = AfterJob.select()[0]
        assert afterjob.label == 'Processing'
        assert afterjob.status == 'registered'
        assert resp.pyquery('[data-workflow-processing="true"]')
        afterjob_id = resp.pyquery('[data-workflow-processing-afterjob-id]').attr[
            'data-workflow-processing-afterjob-id'
        ]
        assert afterjob.id == afterjob_id
        assert resp.pyquery('.busy-processing').text() == 'Processing...'

    formdata = formdef.data_class().select()[0]

    assert app.get(formdata.get_url() + 'check-workflow-progress?job=' + afterjob_id).json == {
        'err': 0,
        'status': 'processing',
        'job': {'status': 'registered', 'url': f'http://example.net/api/jobs/{afterjob.id}/'},
        'url': None,
    }
    pub.after_jobs.append(afterjob)
    pub.process_after_jobs()
    assert app.get(formdata.get_url() + 'check-workflow-progress?job=' + afterjob_id).json == {
        'err': 0,
        'status': 'idle',
        'job': {'status': 'completed', 'url': f'http://example.net/api/jobs/{afterjob.id}/'},
        'url': 'https://www.example.org',
    }


def test_submit_async_workflow_action_processing(pub):
    pub.load_site_options()
    if not pub.site_options.has_section('options'):
        pub.site_options.add_section('options')
    pub.site_options.set('options', 'perform-workflow-as-job', 'true')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)

    create_user(pub)

    wf = Workflow(name='status')
    st = wf.add_status('Status1', 'st1')
    st2 = wf.add_status('Status2', 'st2')
    st3 = wf.add_status('Status3', 'st3')
    choice = st.add_action('choice')
    choice.label = 'Jump'
    choice.by = ['_submitter']
    choice.status = st2.id
    jump = st2.add_action('jump')
    jump.status = st3.id
    wf.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.workflow = wf
    formdef.store()
    formdef.data_class().wipe()

    AfterJob.wipe()
    app = login(get_app(pub), username='foo', password='foo')
    resp = app.get('/test/')
    resp = resp.forms[0].submit('submit')
    assert 'Check values then click submit.' in resp.text
    resp = resp.forms[0].submit('submit').follow()
    assert 'The form has been recorded' in resp.text
    assert AfterJob.count() == 1
    assert AfterJob.select()[0].label == 'Processing'
    assert AfterJob.select()[0].status == 'completed'

    # do not let tests run afterjobs synchronously
    AfterJob.wipe()
    with mock.patch('wcs.qommon.publisher.QommonPublisher.process_after_jobs'):
        formdata = formdef.data_class().select()[0]
        assert formdata.status == f'wf-{st.id}'
        resp = resp.form.submit('button1').follow()
        formdata.refresh_from_storage()
        assert formdata.status == f'wf-{st2.id}'
        afterjob = AfterJob.select()[0]
        assert afterjob.label == 'Processing'
        assert afterjob.status == 'registered'
        assert resp.pyquery('[data-workflow-processing="true"]')
        afterjob_id = resp.pyquery('[data-workflow-processing-afterjob-id]').attr[
            'data-workflow-processing-afterjob-id'
        ]
        assert afterjob.id == afterjob_id
        assert resp.pyquery('.busy-processing').text() == 'Processing...'

    assert app.get(formdata.get_url() + 'check-workflow-progress?job=' + afterjob_id).json == {
        'err': 0,
        'status': 'processing',
        'job': {'status': 'registered', 'url': f'http://example.net/api/jobs/{afterjob.id}/'},
        'url': None,
    }
    pub.after_jobs.append(afterjob)
    pub.process_after_jobs()
    assert app.get(formdata.get_url() + 'check-workflow-progress?job=' + afterjob_id).json == {
        'err': 0,
        'status': 'idle',
        'job': {'status': 'completed', 'url': f'http://example.net/api/jobs/{afterjob.id}/'},
        'url': None,
    }
    resp = app.get(formdata.get_url())
    formdata.refresh_from_storage()
    assert formdata.status == f'wf-{st3.id}'
