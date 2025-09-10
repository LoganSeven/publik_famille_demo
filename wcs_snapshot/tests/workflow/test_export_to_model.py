import io
import os
import time
import zipfile

import pytest
from django.utils.encoding import force_bytes
from PIL import Image
from pyzbar.pyzbar import ZBarSymbol
from pyzbar.pyzbar import decode as zbar_decode_qrcode
from quixote import cleanup
from quixote.http_request import Upload as QuixoteUpload
from webtest import Radio, Upload

from wcs import sessions
from wcs.blocks import BlockDef
from wcs.carddef import CardDef
from wcs.fields import (
    BlockField,
    BoolField,
    DateField,
    EmailField,
    FileField,
    ItemField,
    ItemsField,
    NumericField,
    PageField,
    StringField,
    SubtitleField,
    TableField,
    TextField,
    TitleField,
)
from wcs.formdef import FormDef
from wcs.logged_errors import LoggedError
from wcs.qommon import force_str
from wcs.qommon.form import UploadedFile
from wcs.qommon.http_request import HTTPRequest
from wcs.qommon.upload_storage import PicklableUpload
from wcs.wf.export_to_model import ExportToModel, transform_to_pdf
from wcs.workflows import AttachmentEvolutionPart, Workflow, WorkflowBackofficeFieldsFormDef

from ..admin_pages.test_all import create_superuser
from ..utilities import clean_temporary_pub, create_temporary_pub, get_app, login


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


@pytest.mark.skipif(transform_to_pdf is None, reason='libreoffice not found')
def test_transform_to_pdf():
    with open(os.path.join(os.path.dirname(__file__), '..', 'template.odt'), 'rb') as instream:
        outstream = transform_to_pdf(instream)
        assert outstream is not False
        assert outstream.read(10).startswith(b'%PDF-')


@pytest.mark.parametrize('template_name', ['template-with-image-django-syntax.odt'])
def test_export_to_model_image(pub, template_name):
    formdef = FormDef()
    formdef.name = 'baz'
    formdef.fields = [
        FileField(id='3', label='File', varname='image'),
    ]
    formdef.store()

    upload = PicklableUpload('test.jpeg', 'image/jpeg')
    with open(os.path.join(os.path.dirname(__file__), '..', 'image-with-gps-data.jpeg'), 'rb') as fd:
        image_data = fd.read()
    upload.receive([image_data])

    formdata = formdef.data_class()()
    formdata.data = {'3': upload}
    formdata.just_created()
    formdata.store()
    pub.substitutions.feed(formdata)

    item = ExportToModel()
    item.convert_to_pdf = False
    item.method = 'non-interactive'
    template_filename = os.path.join(os.path.dirname(__file__), '..', template_name)
    with open(template_filename, 'rb') as fd:
        template = fd.read()
    upload = QuixoteUpload('/foo/template.odt', content_type='application/octet-stream')
    upload.fp = io.BytesIO()
    upload.fp.write(template)
    upload.fp.seek(0)
    item.model_file = UploadedFile(pub.app_dir, None, upload)
    item.attach_to_history = True

    item.perform(formdata)

    assert formdata.evolution[-1].parts[-1].base_filename == 'template.odt'
    with zipfile.ZipFile(formdata.evolution[-1].parts[-1].get_file_path(), mode='r') as zfile:
        zinfo = zfile.getinfo('Pictures/10000000000000320000003276E9D46581B55C88.jpg')
    # check the image has been replaced by the one from the formdata
    assert zinfo.file_size == len(image_data)

    # check with missing data or wrong kind of data
    for field_value in (None, 'wrong kind'):
        formdata = formdef.data_class()()
        formdata.data = {'3': field_value}
        formdata.just_created()
        formdata.store()
        pub.substitutions.feed(formdata)

        item.perform(formdata)

        with zipfile.ZipFile(formdata.evolution[-1].parts[-1].get_file_path(), mode='r') as zfile:
            zinfo = zfile.getinfo('Pictures/10000000000000320000003276E9D46581B55C88.jpg')
        # check the original image has been left
        assert zinfo.file_size == 580

    item.filename = 'formulaire-{{form_number}}/2.odt'
    item.perform(formdata)
    assert formdata.evolution[-1].parts[-1].base_filename == 'formulaire-%s-%s-2.odt' % (
        formdef.id,
        formdata.id,
    )


def test_export_to_model_qrcode(pub):
    FormDef.wipe()

    formdef = FormDef()
    formdef.name = 'baz'
    formdef.fields = []
    formdef.store()

    formdef.data_class().wipe()
    formdata = formdef.data_class()()
    formdata.data = {}
    formdata.just_created()
    formdata.store()
    pub.substitutions.feed(formdata)

    item = ExportToModel()
    item.convert_to_pdf = False
    item.method = 'non-interactive'
    template_filename = os.path.join(os.path.dirname(__file__), '..', 'template-with-qrcode.odt')
    with open(template_filename, 'rb') as fd:
        template = fd.read()
    upload = QuixoteUpload('/foo/template.odt', content_type='application/octet-stream')
    upload.fp = io.BytesIO()
    upload.fp.write(template)
    upload.fp.seek(0)
    item.model_file = UploadedFile(pub.app_dir, None, upload)
    item.attach_to_history = True

    item.perform(formdata)

    assert formdata.evolution[-1].parts[-1].base_filename == 'template.odt'
    with zipfile.ZipFile(formdata.evolution[-1].parts[-1].get_file_path(), mode='r') as zfile:
        # base template use a jpg images and export_to_model does not rename it
        # event when content is PNG, but it still works inside LibreOffice
        # which ignores the filename extension.
        image_filename = [name for name in zfile.namelist() if name.endswith('.jpg')][0]
        with zfile.open(image_filename, 'r') as image_fd:
            img = Image.open(image_fd)
            assert (
                zbar_decode_qrcode(img, symbols=[ZBarSymbol.QRCODE])[0].data.decode()
                == 'http://example.net/backoffice/management/baz/1/'
            )


def test_export_to_model_backoffice_field(pub):
    wf = Workflow(name='email with attachments')
    wf.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(wf)
    wf.backoffice_fields_formdef.fields = [
        FileField(id='bo1', label='bo field 1', varname='backoffice_file1'),
    ]
    st1 = wf.add_status('Status1')
    wf.store()
    formdef = FormDef()
    formdef.name = 'foo-export-to-bofile'
    formdef.fields = [
        StringField(id='1', label='String', varname='string'),
    ]
    formdef.workflow_id = wf.id
    formdef.store()
    formdata = formdef.data_class()()
    formdata.data = {}
    formdata.just_created()
    formdata.store()
    pub.substitutions.feed(formdata)

    item = ExportToModel()
    item.method = 'non-interactive'
    item.convert_to_pdf = False
    template_filename = os.path.join(os.path.dirname(__file__), '..', 'template.odt')
    with open(template_filename, 'rb') as fd:
        template = fd.read()
    upload = QuixoteUpload('/foo/template.odt', content_type='application/octet-stream')
    upload.fp = io.BytesIO()
    upload.fp.write(template)
    upload.fp.seek(0)
    item.model_file = UploadedFile(pub.app_dir, None, upload)
    item.parent = st1
    item.backoffice_filefield_id = 'bo1'
    item.perform(formdata)

    assert 'bo1' in formdata.data
    fbo1 = formdata.data['bo1']
    assert fbo1.base_filename == 'template.odt'
    assert fbo1.content_type == 'application/octet-stream'
    with zipfile.ZipFile(fbo1.get_file()) as zfile:
        assert b'foo-export-to-bofile' in zfile.read('content.xml')

    # no more 'bo1' backoffice field: do nothing
    formdata = formdef.data_class()()
    formdata.data = {}
    formdata.just_created()
    formdata.store()
    pub.substitutions.feed(formdata)
    # id is not bo1:
    wf.backoffice_fields_formdef.fields = [
        FileField(id='bo2', label='bo field 2'),
    ]
    item.perform(formdata)
    assert formdata.data == {}
    # field is not a field file:
    wf.backoffice_fields_formdef.fields = [
        StringField(id='bo1', label='bo field 1'),
    ]
    item.perform(formdata)
    assert formdata.data == {}
    # no field at all:
    wf.backoffice_fields_formdef.fields = []
    item.perform(formdata)
    assert formdata.data == {}


def test_export_to_model_django_template(pub):
    formdef = FormDef()
    formdef.name = 'foo-export-to-template-with-django'
    formdef.fields = [
        StringField(id='1', label='String', varname='string'),
    ]
    formdef.store()
    formdata = formdef.data_class()()
    formdata.data = {}
    formdata.just_created()
    formdata.store()
    pub.substitutions.feed(formdata)

    item = ExportToModel()
    item.method = 'non-interactive'
    item.attach_to_history = True
    template_filename = os.path.join(os.path.dirname(__file__), '..', 'template-django.odt')
    with open(template_filename, 'rb') as fd:
        template = fd.read()
    upload = QuixoteUpload('/foo/template-django.odt', content_type='application/octet-stream')
    upload.fp = io.BytesIO()
    upload.fp.write(template)
    upload.fp.seek(0)
    item.model_file = UploadedFile(pub.app_dir, None, upload)
    item.convert_to_pdf = False
    item.perform(formdata)

    with open(formdata.evolution[0].parts[-1].get_file_path(), 'rb') as fd:
        with zipfile.ZipFile(fd) as zout:
            new_content = zout.read('content.xml')
    assert b'>foo-export-to-template-with-django<' in new_content

    formdef.name = 'Name with a \' simple quote'
    formdef.store()
    item.perform(formdata)

    with open(formdata.evolution[0].parts[-1].get_file_path(), 'rb') as fd:
        with zipfile.ZipFile(fd) as zout:
            new_content = zout.read('content.xml')
    assert b'>Name with a \' simple quote<' in new_content

    formdef.name = 'A <> name'
    formdef.store()
    item.perform(formdata)

    with open(formdata.evolution[0].parts[-1].get_file_path(), 'rb') as fd:
        with zipfile.ZipFile(fd) as zout:
            new_content = zout.read('content.xml')
    assert b'>A &lt;&gt; name<' in new_content


def test_export_to_model_xml(pub):
    formdef = FormDef()
    formdef.name = 'foo-export-to-template-with-django'
    formdef.fields = [
        StringField(id='1', label='String', varname='string'),
    ]
    formdef.store()
    formdata = formdef.data_class()()
    formdata.data = {'1': 'écho'}
    formdata.just_created()
    formdata.store()

    # good XML
    item = ExportToModel()
    item.method = 'non-interactive'
    item.attach_to_history = True

    def run(template, filename='/foo/template.xml', content_type='application/xml'):
        formdata.evolution[-1].parts = None
        formdata.store()
        LoggedError.wipe()
        upload = QuixoteUpload(filename, content_type=content_type)
        upload.fp = io.BytesIO()
        upload.fp.write(force_bytes(template))
        upload.fp.seek(0)
        item.model_file = UploadedFile(pub.app_dir, None, upload)
        item.convert_to_pdf = False
        pub.substitutions.reset()
        pub.substitutions.feed(formdata)
        item.perform(formdata)
        if formdata.evolution[0].parts:
            with open(formdata.evolution[0].parts[-1].get_file_path()) as fd:
                return fd.read()

    # good XML
    assert run(template='<a>{{ form_var_string }}</a>') == '<a>écho</a>'
    assert (
        run(template='<a>{{ form_var_string }}</a>', content_type='application/octet-stream') == '<a>écho</a>'
    )
    assert run(template='<a>{{ form_var_string }}</a>', filename='/foo/template.svg') == '<a>écho</a>'

    # unknown file format
    assert not run(
        template='<a>{{ form_var_string }}</a>',
        filename='/foo/template.txt',
        content_type='application/octet-stream',
    )
    assert LoggedError.count() == 1
    assert LoggedError.select()[0].summary == 'Only OpenDocument and XML files can be used.'

    # invalid UTF-8
    assert not run(template=b'<name>test \xe0 {{form_var_string}}</name>')
    assert LoggedError.count() == 1
    assert LoggedError.select()[0].summary == 'XML model files must be UTF-8.'

    # malformed XML
    assert run(template='<a>{{ form_var_string }}<a>') == '<a>écho<a>'
    # on error in the XML correctness no exception is raised but an error is logged
    assert LoggedError.count() == 1
    assert LoggedError.select()[0].summary == 'The rendered template is not a valid XML document.'


def test_export_to_model_disabled_rtf(pub):
    formdef = FormDef()
    formdef.name = 'foo-export-to-template-with-django'
    formdef.fields = [
        StringField(id='1', label='String', varname='string'),
    ]
    formdef.store()
    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()

    item = ExportToModel()
    item.method = 'non-interactive'
    item.attach_to_history = True
    upload = QuixoteUpload('test.rtf', content_type='application/rtf')
    upload.fp = io.BytesIO()
    upload.fp.write(b'{\\rtf...')
    upload.fp.seek(0)
    item.model_file = UploadedFile(pub.app_dir, None, upload)
    item.convert_to_pdf = False
    pub.substitutions.reset()
    pub.substitutions.feed(formdata)

    LoggedError.wipe()
    item.perform(formdata)
    assert LoggedError.count() == 1
    assert LoggedError.select()[0].summary == 'Only OpenDocument and XML files can be used.'


@pytest.mark.parametrize('filename', ['template-form-details.odt', 'template-form-details-no-styles.odt'])
def test_export_to_model_form_details_section(pub, filename):
    BlockDef.wipe()
    FormDef.wipe()

    block = BlockDef()
    block.name = 'foobar'
    block.digest_template = 'X{{foobar_var_foo}}Y'
    block.fields = [
        StringField(id='123', required='required', label='Test', varname='foo'),
        StringField(id='234', required='required', label='Test2', varname='bar'),
    ]
    block.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'foo-export-details'
    formdef.fields = [
        PageField(id='1', label='Page 1'),
        TitleField(id='2', label='Title'),
        SubtitleField(id='3', label='Subtitle'),
        StringField(id='4', label='String', varname='string'),
        EmailField(id='5', label='Email'),
        TextField(id='6', label='Text'),
        BoolField(id='8', label='Bool'),
        FileField(id='9', label='File'),
        DateField(id='10', label='Date'),
        ItemField(id='11', label='Item', items=['foo', 'bar']),
        TableField(id='12', label='Table', columns=['a', 'b'], rows=['c', 'd']),
        PageField(id='13', label='Empty Page'),
        TitleField(id='14', label='Empty Title'),
        StringField(id='15', label='Empty String', varname='invisiblestr'),
        BlockField(id='16', label='Block Field', block_slug='foobar'),
        ItemsField(id='17', label='Items', items=['foo', 'bar']),
        NumericField(id='18', label='Numeric Field'),
    ]
    formdef.store()
    formdef.data_class().wipe()
    upload = PicklableUpload('test.jpeg', 'image/jpeg')
    with open(os.path.join(os.path.dirname(__file__), '..', 'image-with-gps-data.jpeg'), 'rb') as fd:
        upload.receive([fd.read()])
    formdata = formdef.data_class()()
    formdata.data = {
        '4': 'string',
        '5': 'foo@localhost',
        '6': 'para1\npara2',
        '8': False,
        '9': upload,
        '10': time.strptime('2015-05-12', '%Y-%m-%d'),
        '11': 'foo',
        '12': [['1', '2'], ['3', '4']],
        # value from test_block_digest in tests/form_pages/test_block.py
        '16': {
            'data': [{'123': 'foo', '234': 'bar'}, {'123': 'foo2', '234': 'bar2'}],
            'schema': {'123': 'string', '234': 'string'},
        },
        '16_display': 'XfooY, Xfoo2Y',
        '18': 14.4,
    }
    formdata.just_created()
    formdata.store()
    pub.substitutions.feed(formdata)

    item = ExportToModel()
    item.method = 'non-interactive'
    item.attach_to_history = True
    template_filename = os.path.join(os.path.dirname(__file__), '..', filename)
    with open(template_filename, 'rb') as fd:
        template = fd.read()
    upload = QuixoteUpload(filename, content_type='application/octet-stream')
    upload.fp = io.BytesIO()
    upload.fp.write(template)
    upload.fp.seek(0)
    item.model_file = UploadedFile(pub.app_dir, None, upload)
    item.convert_to_pdf = False
    item.perform(formdata)

    with open(formdata.evolution[0].parts[-1].get_file_path(), 'rb') as fd:
        with zipfile.ZipFile(fd) as zout:
            new_content = force_str(zout.read('content.xml'))
    # section content has been removed
    assert 'Titre de page' not in new_content
    assert 'Titre' not in new_content
    assert 'Libell&#233; de champ' not in new_content
    assert 'Valeur de champ' not in new_content
    # and new content has been inserted
    assert '>Page 1<' in new_content
    assert '>Title<' in new_content
    assert '>Subtitle<' in new_content
    assert '<text:span>string</text:span>' in new_content
    assert '>para1<' in new_content
    assert '>para2<' in new_content
    assert '<text:span>No</text:span>' in new_content
    assert 'xlink:href="http://example.net/foo-export-details/1/download?f=9"' in new_content
    assert '>test.jpeg</text:a' in new_content
    assert '>2015-05-12<' in new_content
    assert 'Invisible' not in new_content
    assert new_content.count('/table:table-cell') == 8
    assert '>14.4<' in new_content
    # block sub fields
    assert '>foo<' in new_content
    assert '>bar<' in new_content
    assert '>foo2<' in new_content
    assert '>bar2<' in new_content

    if filename == 'template-form-details-no-styles.odt':
        with open(formdata.evolution[0].parts[-1].get_file_path(), 'rb') as fd:
            with zipfile.ZipFile(fd) as zout:
                new_styles = force_str(zout.read('styles.xml'))
        assert 'Field_20_Label' in new_styles


def test_interactive_create_doc_and_jump_on_submit(pub):
    wf = Workflow(name='create doc and jump on submit')
    st0 = wf.add_status('Status0')
    st1 = wf.add_status('Status1')
    st2 = wf.add_status('Status2')
    button = st0.add_action('choice')
    button.by = ['_submitter', '_receiver']
    button.label = 'jump'
    button.status = st1.id
    jump = st1.add_action('jumponsubmit', id='_jump')
    jump.status = st2.id
    export_to_model = st1.add_action('export_to_model', id='_export_to_model')
    export_to_model.by = ['_submitter', '_receiver']
    export_to_model.method = 'interactive'
    export_to_model.convert_to_pdf = False
    template_filename = os.path.join(os.path.dirname(__file__), '..', 'template.odt')
    with open(template_filename, 'rb') as fd:
        template = fd.read()
    upload = QuixoteUpload('/foo/template.odt', content_type='application/octet-stream')
    upload.fp = io.BytesIO()
    upload.fp.write(template)
    upload.fp.seek(0)
    export_to_model.model_file = UploadedFile(pub.app_dir, None, upload)
    wf.store()

    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [StringField(id='1', label='string', varname='toto')]
    formdef.workflow_id = wf.id
    formdef.store()

    resp = get_app(pub).get(formdef.get_url())
    resp.form['f1'] = 'test'
    resp = resp.form.submit('submit')
    resp = resp.form.submit('submit').follow()

    resp = resp.form.submit(f'button{button.id}').follow()
    assert formdef.data_class().select()[0].status == f'wf-{st1.id}'

    resp = resp.form.submit(f'button{export_to_model.id}').follow().follow()
    assert resp.content_type != 'text/html'
    assert resp.body.startswith(b'PK')  # odt
    assert formdef.data_class().select()[0].status == f'wf-{st1.id}'  # no change


def test_interactive_create_doc_update_ts(pub):
    wf = Workflow(name='create doc')
    st0 = wf.add_status('Status0')
    export_to_model = st0.add_action('export_to_model', id='_export_to_model')
    export_to_model.by = ['_submitter', '_receiver']
    export_to_model.method = 'interactive'
    export_to_model.convert_to_pdf = False
    template_filename = os.path.join(os.path.dirname(__file__), '..', 'template.odt')
    with open(template_filename, 'rb') as fd:
        template = fd.read()
    upload = QuixoteUpload('/foo/template.odt', content_type='application/octet-stream')
    upload.fp = io.BytesIO()
    upload.fp.write(template)
    upload.fp.seek(0)
    export_to_model.model_file = UploadedFile(pub.app_dir, None, upload)
    wf.store()

    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [StringField(id='1', label='string', varname='toto')]
    formdef.workflow_id = wf.id
    formdef.store()
    formdef.data_class().wipe()

    app = get_app(pub)
    resp = app.get(formdef.get_url())
    resp.form['f1'] = 'test'
    resp = resp.form.submit('submit')
    resp = resp.form.submit('submit').follow()

    resp2 = resp.form.submit(f'button{export_to_model.id}').follow().follow()
    assert resp2.body.startswith(b'PK')  # odt

    # emulate js that will update workflow form ts field
    resp_js = app.get(resp.request.path + 'tsupdate')
    formdata = formdef.data_class().select()[0]
    assert resp_js.json['ts'] != resp.forms['wf-actions']['_ts']
    assert str(formdata.last_update_time.timestamp()) == resp_js.json['ts']


def test_interactive_create_doc_invalid_model(pub):
    wf = Workflow(name='create doc')
    st0 = wf.add_status('Status0')
    export_to_model = st0.add_action('export_to_model', id='_export_to_model')
    export_to_model.by = ['_submitter', '_receiver']
    export_to_model.method = 'interactive'
    export_to_model.convert_to_pdf = False
    upload = QuixoteUpload('/foo/template.bin', content_type='application/octet-stream')
    upload.fp = io.BytesIO()
    upload.fp.write(b'xxxx')
    upload.fp.seek(0)
    export_to_model.model_file = UploadedFile(pub.app_dir, None, upload)
    wf.store()

    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [StringField(id='1', label='string', varname='toto')]
    formdef.workflow_id = wf.id
    formdef.store()
    formdef.data_class().wipe()

    app = get_app(pub)
    resp = app.get(formdef.get_url())
    resp.form['f1'] = 'test'
    resp = resp.form.submit('submit')
    resp = resp.form.submit('submit').follow()

    LoggedError.wipe()
    resp2 = resp.form.submit(f'button{export_to_model.id}').follow().follow(status=501)
    assert 'Invalid model defined for this action' in resp2.text
    assert LoggedError.count() == 1


def test_workflows_edit_export_to_model_action(pub):
    create_superuser(pub)
    Workflow.wipe()
    workflow = Workflow(name='foo')
    workflow.add_status(name='baz')
    workflow.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/workflows/1/')
    resp = resp.click('baz')

    resp.forms[0]['action-interaction'] = 'Document Creation'
    resp = resp.forms[0].submit()
    resp = resp.follow()

    resp = resp.click('Document Creation')
    with open(os.path.join(os.path.dirname(__file__), '../template.odt'), 'rb') as fd:
        model_content = fd.read()
    resp.form['model_file'] = Upload('test.odt', model_content)
    resp = resp.form.submit('submit')
    resp = resp.follow()
    resp = resp.follow()
    resp = resp.click('Document Creation')
    resp_model_content = resp.click('test.odt')
    assert resp_model_content.body == model_content
    resp = resp.form.submit('submit').follow().follow()
    # check file model is still there
    resp = resp.click('Document Creation')
    resp_model_content = resp.click('test.odt')
    assert resp_model_content.body == model_content

    # check with RTF, disallowed by default
    resp.form['model_file'] = Upload('test.rtf', b'{\\rtf...')
    resp = resp.form.submit('submit')
    assert resp.pyquery('.widget-with-error .error').text() == 'Only OpenDocument and XML files can be used.'

    # allow RTF
    pub.site_options.set('options', 'disable-rtf-support', 'false')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)
    resp.form['model_file'] = Upload('test.rtf', b'{\\rtf...')
    resp = resp.form.submit('submit').follow().follow()
    assert (
        resp.pyquery('.biglistitem--content')
        .text()
        .startswith('Document Creation (with model named test.rtf')
    )


def test_workflows_export_to_model_action_display(pub):
    create_superuser(pub)

    Workflow.wipe()
    workflow = Workflow(name='foo')
    baz_status = workflow.add_status(name='baz')
    export_to = baz_status.add_action('export_to_model')
    export_to.label = 'create doc'
    workflow.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/workflows/1/status/1/')
    assert 'Document Creation (no model set)' in resp

    upload = QuixoteUpload('/foo/test.rtf', content_type='application/rtf')
    upload.fp = io.BytesIO()
    upload.fp.write(b'HELLO WORLD')
    upload.fp.seek(0)
    export_to.model_file = UploadedFile(pub.app_dir, None, upload)
    export_to.id = '_export_to'
    export_to.by = ['_submitter']
    workflow.store()

    resp = app.get('/backoffice/workflows/1/status/1/')
    assert 'Document Creation (with model named test.rtf of 11 bytes)' in resp

    upload.fp.write(b'HELLO WORLD' * 4242)
    upload.fp.seek(0)
    export_to.model_file = UploadedFile(pub.app_dir, None, upload)
    workflow.store()

    resp = app.get('/backoffice/workflows/1/status/1/')
    assert 'Document Creation (with model named test.rtf of 45.6 KB)' in resp

    resp = app.get(export_to.get_admin_url())
    resp.form['method'] = 'Non interactive'
    resp = resp.form.submit('submit')
    workflow.refresh_from_storage()
    assert not workflow.possible_status[0].items[0].by


def test_workflows_export_to_model_in_status(pub):
    create_superuser(pub)

    Workflow.wipe()
    workflow = Workflow(name='foo')
    baz_status = workflow.add_status(name='baz')
    export_to = baz_status.add_action('export_to_model')
    export_to.label = 'create doc'
    workflow.store()

    app = login(get_app(pub))
    resp = app.get(export_to.get_admin_url())
    assert isinstance(resp.form['method'], Radio)
    resp.form['label'] = 'export label'
    resp = resp.form.submit('submit')
    workflow.refresh_from_storage()
    assert workflow.possible_status[0].items[0].method == 'interactive'
    assert workflow.possible_status[0].items[0].label == 'export label'


def test_workflows_export_to_model_in_global_action(pub):
    create_superuser(pub)

    Workflow.wipe()
    workflow = Workflow(name='foo')
    ac1 = workflow.add_global_action('Action', 'ac1')
    export_to = ac1.add_action('export_to_model')
    export_to.label = 'create doc'
    workflow.store()

    app = login(get_app(pub))
    resp = app.get(export_to.get_admin_url())
    assert not isinstance(resp.form['method'], Radio)
    assert 'label' not in resp.form.fields
    resp = resp.form.submit('submit')
    workflow.refresh_from_storage()
    assert workflow.global_actions[0].items[0].method == 'non-interactive'


def test_workflows_edit_export_to_model_action_check_template(pub):
    create_superuser(pub)
    Workflow.wipe()
    workflow = Workflow(name='foo')
    workflow.add_status(name='baz')
    workflow.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/workflows/1/')
    resp = resp.click('baz')

    resp.forms[0]['action-interaction'] = 'Document Creation'
    resp = resp.forms[0].submit()
    resp = resp.follow()

    resp = resp.click('Document Creation')
    zip_out_fp = io.BytesIO()
    with open(os.path.join(os.path.dirname(__file__), '../template.odt'), 'rb') as fd:
        with zipfile.ZipFile(fd, mode='r') as zip_in, zipfile.ZipFile(zip_out_fp, mode='w') as zip_out:
            for filename in zip_in.namelist():
                content = zip_in.read(filename)
                if filename == 'content.xml':
                    assert b'>[form_name]<' in content
                    content = content.replace(b'>[form_name]<', b'>{% if foo %}{{ foo }}{% end %}<')
                zip_out.writestr(filename, content)
    model_content = zip_out_fp.getvalue()
    resp.form['model_file'] = Upload('test.odt', model_content)
    resp = resp.form.submit('submit')
    assert (
        resp.pyquery('#form_error_model_file')
        .text()
        .startswith('syntax error in Django template: Invalid block')
    )

    # error in field declaration
    zip_out_fp = io.BytesIO()
    with open(os.path.join(os.path.dirname(__file__), '../template.odt'), 'rb') as fd:
        with zipfile.ZipFile(fd, mode='r') as zip_in, zipfile.ZipFile(zip_out_fp, mode='w') as zip_out:
            for filename in zip_in.namelist():
                content = zip_in.read(filename)
                if filename == 'content.xml':
                    assert b'"[if-any form_name][form_name][end]"' in content
                    content = content.replace(
                        b'"[if-any form_name][form_name][end]"', b'"{% if foo %}{{ foo }}{% end %}"'
                    )
                zip_out.writestr(filename, content)
    model_content = zip_out_fp.getvalue()
    resp.form['model_file'] = Upload('test.odt', model_content)
    resp = resp.form.submit('submit')
    assert (
        resp.pyquery('#form_error_model_file')
        .text()
        .startswith('syntax error in Django template: Invalid block')
    )

    # error in unused field declaration
    zip_out_fp = io.BytesIO()
    with open(os.path.join(os.path.dirname(__file__), '../template.odt'), 'rb') as fd:
        with zipfile.ZipFile(fd, mode='r') as zip_in, zipfile.ZipFile(zip_out_fp, mode='w') as zip_out:
            for filename in zip_in.namelist():
                content = zip_in.read(filename)
                if filename == 'content.xml':
                    assert b'office:string-value="[if-any form_name][form_name][end]"' in content
                    content = content.replace(
                        b'"[if-any form_name][form_name][end]"', b'"{% if foo %}{{ foo }}{% end %}"'
                    )
                    content = content.replace(
                        b'text:user-field-get text:name="nawak"', b'text:user-field-get text:name="other"'
                    )
                zip_out.writestr(filename, content)
    model_content = zip_out_fp.getvalue()
    resp.form['model_file'] = Upload('test.odt', model_content)
    resp.form.submit('submit').follow()  # success


def test_export_to_model_from_template(pub):
    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'card'
    carddef.fields = [
        FileField(id='1', label='File', varname='file'),
        StringField(id='2', label='String', varname='string'),
    ]
    carddef.store()

    template_filename = os.path.join(os.path.dirname(__file__), '..', 'template.odt')
    with open(template_filename, 'rb') as fd:
        template = fd.read()
    upload = QuixoteUpload('/foo/template.odt', content_type='application/octet-stream')
    upload.fp = io.BytesIO()
    upload.fp.write(template)
    upload.fp.seek(0)

    carddata = carddef.data_class()()
    carddata.data = {'1': upload, '2': 'blah'}
    carddata.just_created()
    carddata.store()

    wf = Workflow(name='test_export_to_model_from_template')
    wf.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(wf)
    wf.backoffice_fields_formdef.fields = [
        FileField(id='bo1', label='bo field 1', varname='backoffice_file1'),
    ]
    st1 = wf.add_status('Status1')
    wf.store()

    formdef = FormDef()
    formdef.name = 'foo-export'
    formdef.fields = [
        StringField(id='1', label='String', varname='string'),
    ]
    formdef.workflow_id = wf.id
    formdef.store()
    formdata = formdef.data_class()()
    formdata.data = {}
    formdata.just_created()
    formdata.store()
    pub.substitutions.feed(formdata)

    item = ExportToModel()
    item.method = 'non-interactive'
    item.convert_to_pdf = False
    item.model_file_mode = 'template'
    item.model_file_template = '{{cards|objects:"card"|first|get:"form_var_file" }}'
    item.parent = st1
    item.backoffice_filefield_id = 'bo1'
    item.perform(formdata)

    assert 'bo1' in formdata.data
    fbo1 = formdata.data['bo1']
    assert fbo1.base_filename == 'template.odt'
    assert fbo1.content_type == 'application/octet-stream'
    with zipfile.ZipFile(fbo1.get_file()) as zfile:
        assert b'foo-export' in zfile.read('content.xml')

    LoggedError.wipe()
    item.model_file_template = '{{cards|objects:"card"|first|get:"form_var_string" }}'
    formdata.data = {}
    item.perform(formdata)
    assert 'bo1' not in formdata.data
    assert LoggedError.count() == 1
    assert LoggedError.select()[0].summary == 'Invalid value obtained for model file (\'blah\')'

    LoggedError.wipe()
    item.model_file_template = '{% if foo %}{{ foo }}{% end %}'  # invalid template
    formdata.data = {}
    item.perform(formdata)
    assert 'bo1' not in formdata.data
    assert LoggedError.count() == 1
    assert LoggedError.select()[0].summary == 'Failed to evaluate template for action'


def test_export_to_model_clamd(pub):
    pub.load_site_options()
    if not pub.site_options.has_section('options'):
        pub.site_options.add_section('options')
    pub.site_options.set('options', 'enable-clamd', 'true')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)

    wf = Workflow(name='create doc')
    wf.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(wf)
    wf.backoffice_fields_formdef.fields = [
        FileField(id='bo1', label='bo field 1', varname='backoffice_file1'),
    ]
    st1 = wf.add_status('Status1')
    wf.store()
    formdef = FormDef()
    formdef.name = 'foo'
    formdef.fields = []
    formdef.workflow_id = wf.id
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {}
    formdata.just_created()
    formdata.store()
    pub.substitutions.feed(formdata)

    item = ExportToModel()
    item.method = 'non-interactive'
    item.convert_to_pdf = False
    item.attach_to_history = True
    template_filename = os.path.join(os.path.dirname(__file__), '..', 'template.odt')
    with open(template_filename, 'rb') as fd:
        template = fd.read()
    upload = QuixoteUpload('/foo/template.odt', content_type='application/octet-stream')
    upload.fp = io.BytesIO()
    upload.fp.write(template)
    upload.fp.seek(0)
    item.model_file = UploadedFile(pub.app_dir, None, upload)
    item.parent = st1
    item.backoffice_filefield_id = 'bo1'
    item.perform(formdata)

    assert formdata.data['bo1'].allow_download()
    assert list(formdata.iter_evolution_parts(AttachmentEvolutionPart))[0].allow_download()
