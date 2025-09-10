import datetime
import io
import json
import os
import zipfile
from unittest import mock

import pytest
from quixote.http_request import Upload as QuixoteUpload

from wcs import fields
from wcs.backoffice.deprecations import DeprecationsScan
from wcs.blocks import BlockDef
from wcs.carddef import CardDef
from wcs.data_sources import NamedDataSource
from wcs.formdef import FormDef
from wcs.mail_templates import MailTemplate
from wcs.qommon.afterjobs import AfterJob
from wcs.qommon.form import UploadedFile
from wcs.qommon.http_request import HTTPRequest
from wcs.wf.create_formdata import Mapping
from wcs.wf.export_to_model import ExportToModel
from wcs.wf.external_workflow import ExternalWorkflowGlobalAction
from wcs.wf.geolocate import GeolocateWorkflowStatusItem
from wcs.wf.jump import JumpWorkflowStatusItem
from wcs.wf.notification import SendNotificationWorkflowStatusItem
from wcs.wf.redirect_to_url import RedirectToUrlWorkflowStatusItem
from wcs.workflows import Workflow, WorkflowBackofficeFieldsFormDef, WorkflowVariablesFieldsFormDef
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

    if os.path.exists(os.path.join(pub.app_dir, 'deprecations.json')):
        os.remove(os.path.join(pub.app_dir, 'deprecations.json'))

    BlockDef.wipe()
    CardDef.wipe()
    FormDef.wipe()
    MailTemplate.wipe()
    NamedDataSource.wipe()
    NamedWsCall.wipe()
    Workflow.wipe()

    return pub


def teardown_module(module):
    clean_temporary_pub()


def test_no_deprecations(pub):
    create_superuser(pub)
    app = login(get_app(pub))
    # first time, it's a redirect to the scanning job
    resp = app.get('/backoffice/studio/deprecations/', status=302)
    resp = resp.follow()
    resp = resp.click('Go to deprecation report')
    # second time, the page stays on
    resp = app.get('/backoffice/studio/deprecations/', status=200)
    assert 'No deprecated items were found on this site.' in resp.text


def test_deprecations(pub):
    create_superuser(pub)

    formdef = FormDef()
    formdef.name = 'foobar'
    formdef.fields = [
        fields.StringField(
            id='3', label='ezt_prefill', prefill={'type': 'string', 'value': '[form_var_test]'}
        ),
        fields.StringField(id='4', label='jsonp_data', data_source={'type': 'jsonp', 'value': 'xxx'}),
        fields.StringField(id='5', label='ezt_in_datasource', data_source={'type': 'json', 'value': '[xxx]'}),
        fields.CommentField(id='6', label='[ezt] in label'),
        fields.TableField(id='8', label='table field'),
        fields.RankedItemsField(id='9', label='ranked field'),
    ]
    formdef.store()

    workflow = Workflow(name='test')
    workflow.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(workflow)
    workflow.backoffice_fields_formdef.fields = [
        fields.TableField(id='bo1', label='table field'),
    ]
    workflow.variables_formdef = WorkflowVariablesFieldsFormDef(workflow)
    workflow.variables_formdef.fields = [
        fields.TableField(id='wfvar1', label='other table field'),
    ]
    st0 = workflow.add_status('Status0', 'st0')

    display = st0.add_action('displaymsg')
    display.message = 'message with [ezt] info'

    wscall = st0.add_action('webservice_call', id='_wscall')
    wscall.varname = 'xxx'
    wscall.url = 'http://remote.example.net/xml'
    wscall.post_data = {'str': 'abcd', 'evalme': '=form_number'}

    sendsms = st0.add_action('sendsms', id='_sendsms')
    sendsms.to = 'xxx'
    sendsms.parent = st0
    st0.items.append(sendsms)

    item = st0.add_action('set-backoffice-fields', id='_item')
    item.fields = [{'field_id': 'bo1', 'value': '=form_var_foo'}]

    create_formdata = st0.add_action('create_formdata', id='_create_formdata')
    create_formdata.varname = 'resubmitted'
    create_formdata.mappings = [
        Mapping(field_id='0', expression='=form_var_toto_string'),
    ]

    item = st0.add_action('update_user_profile', id='_item2')
    item.fields = [{'field_id': '__email', 'value': '=form_var_foo'}]

    sendmail = st0.add_action('sendmail', id='_sendmail')
    sendmail.to = ['=plop']

    export_to = st0.add_action('export_to_model', id='_export_to')
    export_to.convert_to_pdf = False
    export_to.label = 'create doc'
    upload = QuixoteUpload('/foo/test.rtf', content_type='application/rtf')
    upload.fp = io.BytesIO()
    upload.fp.write(b'HELLO WORLD')
    upload.fp.seek(0)
    export_to.model_file = UploadedFile(pub.app_dir, None, upload)
    export_to.by = ['_submitter']

    for klass in (
        ExportToModel,
        ExternalWorkflowGlobalAction,
        GeolocateWorkflowStatusItem,
        JumpWorkflowStatusItem,
        SendNotificationWorkflowStatusItem,
        RedirectToUrlWorkflowStatusItem,
    ):
        action = klass()
        action.parent = st0
        st0.items.append(action)

    st0.add_action('aggregationemail')

    workflow.store()

    data_source = NamedDataSource(name='ds_jsonp')
    data_source.data_source = {'type': 'jsonp', 'value': 'xxx'}
    data_source.store()
    data_source = NamedDataSource(name='ds_csv')
    data_source.data_source = {'type': 'json', 'value': 'http://example.net/csvdatasource/plop/test'}
    data_source.store()

    NamedWsCall.wipe()
    wscall = NamedWsCall()
    wscall.name = 'Hello'
    wscall.request = {'url': 'http://example.net', 'qs_data': {'a': '=1+2'}}
    wscall.store()

    wscall = NamedWsCall()
    wscall.name = 'Hello CSV'
    wscall.request = {'url': 'http://example.net/csvdatasource/plop/test'}
    wscall.store()

    wscall = NamedWsCall()
    wscall.name = 'Hello json data store'
    wscall.request = {'url': 'http://example.net/jsondatastore/plop'}
    wscall.store()

    MailTemplate.wipe()
    mail_template1 = MailTemplate()
    mail_template1.name = 'Hello1'
    mail_template1.subject = 'plop'
    mail_template1.body = 'plop'
    mail_template1.attachments = ['form_attachments.plop']
    mail_template1.store()
    mail_template2 = MailTemplate()
    mail_template2.name = 'Hello2'
    mail_template2.subject = 'plop'
    mail_template2.body = 'plop [ezt] plop'
    mail_template2.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/studio/deprecations/', status=302)
    resp = resp.follow()
    resp = resp.click('Go to deprecation report')

    assert [x.text for x in resp.pyquery('.section--ezt li a')] == [
        'foobar / Field "ezt_prefill"',
        'foobar / Field "ezt_in_datasource"',
        'foobar / Field "[ezt] in label"',
        'test / Alert',
        'Mail Template "Hello2"',
    ]
    assert [x.text for x in resp.pyquery('.section--jsonp li a')] == [
        'foobar / Field "jsonp_data"',
        'Data source "ds_jsonp"',
    ]
    assert [x.text for x in resp.pyquery('.section--rtf li a')] == [
        'test / Document Creation',
    ]
    assert [x.text for x in resp.pyquery('.section--fields li a')] == [
        'foobar / Field "table field"',
        'foobar / Field "ranked field"',
        'Options of workflow "test" / Field "other table field"',
        'Backoffice fields of workflow "test" / Field "table field"',
    ]
    assert [x.text for x in resp.pyquery('.section--action-aggregationemail li a')] == [
        'test / Daily Summary Email',
    ]
    assert [x.text for x in resp.pyquery('.section--csv-connector li a')] == [
        'Data source "ds_csv"',
        'Webservice "Hello CSV"',
    ]
    assert [x.text for x in resp.pyquery('.section--json-data-store li a')] == [
        'Webservice "Hello json data store"',
    ]
    # check all links are ok
    for link in resp.pyquery('.section li a'):
        resp.click(href=link.attrib['href'], index=0)


def test_deprecations_choice_label(pub):
    MailTemplate.wipe()

    # check choice labels are not considered as EZT
    workflow = Workflow(name='test')
    st0 = workflow.add_status('Status0', 'st0')

    accept = st0.add_action('choice', id='_choice')
    accept.label = '[test] action'

    job = DeprecationsScan()
    job.execute()
    assert not job.report_lines


def test_deprecations_skip_invalid_ezt(pub):
    workflow = Workflow(name='test')
    st0 = workflow.add_status('Status0', 'st0')

    display = st0.add_action('displaymsg')
    display.message = 'message with invalid [if-any] ezt'

    job = DeprecationsScan()
    job.execute()
    assert not job.report_lines


def test_deprecations_ignore_ezt_looking_tag(pub):
    workflow = Workflow(name='test')
    st0 = workflow.add_status('Status0', 'st0')
    sendmail = st0.add_action('sendmail')
    sendmail.subject = '[REMINDER] your appointment'
    workflow.store()
    job = DeprecationsScan()
    job.execute()
    assert not job.report_lines

    sendmail.subject = '[reminder]'
    workflow.store()
    job = DeprecationsScan()
    job.execute()
    assert job.report_lines

    sendmail.subject = '[if-any plop]test[end]'
    workflow.store()
    job = DeprecationsScan()
    job.execute()
    assert job.report_lines


def test_deprecations_field_limits(pub):
    formdef = FormDef()
    formdef.name = 'foobar'
    formdef.fields = [fields.StringField(id=str(x), label=f'field{x}') for x in range(450)]
    formdef.store()

    job = DeprecationsScan()
    job.execute()
    assert len(job.report_lines) == 1
    assert job.report_lines[0]['category'] == 'field-limits'


def test_deprecations_cronjob(pub):
    AfterJob.wipe()
    assert not os.path.exists(os.path.join(pub.app_dir, 'deprecations.json'))
    pub.update_deprecations_report()
    assert os.path.exists(os.path.join(pub.app_dir, 'deprecations.json'))
    assert AfterJob.count() == 0


def test_deprecations_document_models(pub):
    create_superuser(pub)

    workflow = Workflow(name='test')
    st0 = workflow.add_status('Status0', 'st0')
    export_to = st0.add_action('export_to_model')
    export_to.convert_to_pdf = False
    export_to.label = 'create doc'
    upload = QuixoteUpload('test.rtf', content_type='text/rtf')
    upload.fp = io.BytesIO()
    upload.fp.write(b'{\\rtf foo [form_var_plop] bar')
    upload.fp.seek(0)
    export_to.model_file = UploadedFile(pub.app_dir, None, upload)
    export_to.by = ['_submitter']

    export_to2 = st0.add_action('export_to_model')
    export_to2.convert_to_pdf = False
    export_to2.label = 'create doc2'
    upload = QuixoteUpload('test.odt', content_type='application/vnd.oasis.opendocument.text')
    upload.fp = io.BytesIO()
    with zipfile.ZipFile(upload.fp, mode='w') as zout:
        content = '''<?xml version="1.0" encoding="UTF-8"?>
<office:document-content
    xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
    xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"
    xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0"
    xmlns:svg="urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0"
    xmlns:xlink="http://www.w3.org/1999/xlink"
    office:version="1.2">
  <office:body>
    <office:text>
      <text:sequence-decls>
        <text:sequence-decl text:display-outline-level="0" text:name="Illustration"/>
        <text:sequence-decl text:display-outline-level="0" text:name="Table"/>
        <text:sequence-decl text:display-outline-level="0" text:name="Text"/>
        <text:sequence-decl text:display-outline-level="0" text:name="Drawing"/>
      </text:sequence-decls>
      <text:user-field-decls>
        <text:user-field-decl office:value-type="string" office:string-value="{{ form_name }}"/>
      </text:user-field-decls>
      <text:p text:style-name="P1">Hello.</text:p>
      <text:p text:style-name="P2">
        <draw:frame draw:style-name="fr1" draw:name="=form_var_image_raw"
                    text:anchor-type="paragraph" svg:width="1.764cm" svg:height="1.764cm" draw:z-index="0">
          <draw:image xlink:href="Pictures/10000000000000320000003276E9D46581B55C88.jpg"
                      xlink:type="simple" xlink:show="embed" xlink:actuate="onLoad"/>
        </draw:frame>
      </text:p>
    </office:text>
  </office:body>
</office:document-content>
'''
        zout.writestr('content.xml', content)
    upload.fp.seek(0)
    export_to2.model_file = UploadedFile(pub.app_dir, None, upload)
    export_to2.by = ['_submitter']

    export_to3 = st0.add_action('export_to_model')
    export_to3.convert_to_pdf = False
    export_to3.label = 'create doc3'
    upload = QuixoteUpload('test2.odt', content_type='application/vnd.oasis.opendocument.text')
    upload.fp = io.BytesIO()
    with zipfile.ZipFile(upload.fp, mode='w') as zout:
        content = '''<?xml version="1.0" encoding="UTF-8"?>
<office:document-content
    xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
    xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"
    xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0"
    xmlns:svg="urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0"
    xmlns:xlink="http://www.w3.org/1999/xlink"
    office:version="1.2">
  <office:body>
    <office:text>
      <text:p text:style-name="P1">a <text:span>= b</text:span></text:p>
    </office:text>
  </office:body>
</office:document-content>
'''
        zout.writestr('content.xml', content)
    upload.fp.seek(0)
    export_to3.model_file = UploadedFile(pub.app_dir, None, upload)
    export_to3.by = ['_submitter']

    workflow.store()

    job = DeprecationsScan()
    job.execute()
    assert job.report_lines == [
        {
            'category': 'ezt',
            'location_label': 'test / Document Creation',
            'source': 'workflow:1',
            'url': 'http://example.net/backoffice/workflows/1/status/st0/items/1/',
        },
        {
            'category': 'rtf',
            'location_label': 'test / Document Creation',
            'source': 'workflow:1',
            'url': 'http://example.net/backoffice/workflows/1/status/st0/items/1/',
        },
    ]


def test_deprecations_inspect_pages(pub):
    formdef = FormDef()
    formdef.name = 'foobar'
    formdef.fields = [
        fields.CommentField(id='1', label='test [ezt]'),
    ]
    formdef.store()

    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.CommentField(id='1', label='test [ezt]'),
    ]
    block.store()

    workflow = Workflow(name='test')
    workflow.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(workflow)
    workflow.backoffice_fields_formdef.fields = [
        fields.TableField(id='bo1', label='table field'),
    ]
    st0 = workflow.add_status('Status0', 'st0')
    display = st0.add_action('displaymsg')
    display.message = 'message with [ezt] info'
    workflow.store()

    job = DeprecationsScan()
    job.execute()

    create_superuser(pub)
    app = login(get_app(pub))

    resp = app.get(formdef.get_admin_url() + 'inspect')
    assert 'Deprecations' in resp.text
    assert resp.pyquery('#inspect-deprecations h4:first-child').text() == 'EZT text'

    resp = app.get(block.get_admin_url() + 'inspect')
    assert 'Deprecations' in resp.text
    assert resp.pyquery('#inspect-deprecations h4:first-child').text() == 'EZT text'

    resp = app.get(workflow.get_admin_url() + 'inspect')
    assert 'Deprecations' in resp.text
    assert resp.pyquery('#inspect-deprecations h4:first-child').text() == 'EZT text'

    # check there's no deprecation tab in snapshots
    snapshot = pub.snapshot_class.get_latest('formdef', formdef.id)
    resp = app.get(formdef.get_admin_url() + f'history/{snapshot.id}/inspect')
    assert 'Deprecations' not in resp.text

    snapshot = pub.snapshot_class.get_latest('block', block.id)
    resp = app.get(block.get_admin_url() + f'history/{snapshot.id}/inspect')
    assert 'Deprecations' not in resp.text

    snapshot = pub.snapshot_class.get_latest('workflow', workflow.id)
    resp = app.get(workflow.get_admin_url() + f'history/{snapshot.id}/inspect')
    assert 'Deprecations' not in resp.text

    # check there's no deprecation tab if there's nothing deprecated
    formdef.fields[0].label = 'test'
    formdef.store()

    block.fields[0].label = 'test'
    block.store()

    workflow.backoffice_fields_formdef = None
    display.message = 'message with {{django}} info'
    workflow.store()

    job = DeprecationsScan()
    job.execute()

    resp = app.get(formdef.get_admin_url() + 'inspect')
    assert 'Deprecations' not in resp.text

    resp = app.get(block.get_admin_url() + 'inspect')
    assert 'Deprecations' not in resp.text

    resp = app.get(workflow.get_admin_url() + 'inspect')
    assert 'Deprecations' not in resp.text


def test_deprecations_on_import(pub):
    mail_template = MailTemplate()  # no python expression in mail templates
    mail_template.name = 'Hello2'
    mail_template.subject = 'plop'
    mail_template.body = 'plop [ezt] plop'
    mail_template.store()

    job = DeprecationsScan()
    job.check_deprecated_elements_in_object(mail_template)
    mail_template_xml = mail_template.export_to_xml()
    MailTemplate.import_from_xml_tree(mail_template_xml)

    job = DeprecationsScan()
    job.check_deprecated_elements_in_object(mail_template)
    MailTemplate.import_from_xml_tree(mail_template_xml)

    # check that DeprecationsScan is not run on object load
    with mock.patch(
        'wcs.backoffice.deprecations.DeprecationsScan.check_deprecated_elements_in_object'
    ) as check:
        MailTemplate(mail_template.id)
        assert check.call_args_list == []


def test_deprecations_ignore_double_equal(pub):
    workflow = Workflow(name='test')
    st0 = workflow.add_status('Status0', 'st0')
    sendmail = st0.add_action('sendmail')
    sendmail.subject = '== your appointment =='
    workflow.store()
    job = DeprecationsScan()
    job.execute()
    assert not job.report_lines


def test_deprecations_dates(pub, freezer):
    freezer.move_to(datetime.date(2021, 1, 1))

    formdef = FormDef()
    formdef.name = 'foobar'
    formdef.fields = [
        fields.CommentField(id='1', label='test [ezt]'),
    ]
    formdef.store()

    DeprecationsScan().execute()

    create_superuser(pub)
    app = login(get_app(pub))
    resp = app.get('/backoffice/studio/deprecations/', status=200)
    assert (
        resp.pyquery('.section--ezt p:not(.soon):last-child').text()
        == 'Support will be removed on 2025-10-31.'
    )
    resp = app.get('/backoffice/forms/', status=200)
    assert not resp.pyquery('.deprecations-banner')

    freezer.move_to(datetime.date(2025, 10, 15))
    resp = app.get('/backoffice/studio/deprecations/', status=200)
    assert resp.pyquery('.section--ezt p.soon:last-child').text() == 'Support will be removed on 2025-10-31.'
    resp = app.get('/backoffice/forms/', status=200)
    assert resp.pyquery('.deprecations-banner')


def test_deprecations_killswitch(pub, freezer):
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        # empty file, to get default values
        pass

    freezer.move_to(datetime.date(2021, 1, 1))

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [fields.CommentField(id='1', label='test [today]')]
    formdef.store()

    create_superuser(pub)
    app = login(get_app(pub))
    resp = app.get(formdef.get_url())
    assert resp.pyquery('.comment-field').text() == 'test 2021-01-01'

    freezer.move_to(datetime.date(2025, 11, 1))
    resp = app.get(formdef.get_url())
    assert resp.pyquery('.comment-field').text() == 'test [today]'

    # set explicit value, to avoid killswitch
    if not pub.site_options.has_section('options'):
        pub.site_options.add_section('options')
    pub.site_options.set('options', 'disable-ezt-support', 'false')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)
    resp = app.get(formdef.get_url())
    assert resp.pyquery('.comment-field').text() == 'test 2025-11-01'


def test_sendmail_attachments(pub):
    workflow = Workflow(name='test')
    st0 = workflow.add_status('Status0', 'st0')
    sendmail = st0.add_action('sendmail')
    workflow.store()

    for goodvalue in ('{{ hello }}', 'https://www.example.com/test.pdf'):
        sendmail.attachments = [goodvalue]
        workflow.store()

        DeprecationsScan().execute()

        with open(os.path.join(pub.app_dir, 'deprecations.json')) as f:
            deprecations_json = json.loads(f.read())
            assert len(deprecations_json['report_lines']) == 0


def test_fargo_options(pub):
    formdef = FormDef()
    formdef.name = 'test form'
    formdef.fields = [fields.FileField(id='1', label='file', allow_portfolio_picking=True)]
    formdef.store()

    workflow = Workflow(name='test')
    st0 = workflow.add_status('Status0', 'st0')
    attachment = st0.add_action('addattachment')
    attachment.push_to_portfolio = True
    workflow.store()

    # without fargo installed
    DeprecationsScan().execute()
    with open(os.path.join(pub.app_dir, 'deprecations.json')) as f:
        deprecations_json = json.loads(f.read())
        assert len(deprecations_json['report_lines']) == 0

    # with fargo installed
    pub.site_options.set('options', 'fargo_url', 'XXX')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)
    DeprecationsScan().execute()

    with open(os.path.join(pub.app_dir, 'deprecations.json')) as f:
        deprecations_json = json.loads(f.read())
        assert len(deprecations_json['report_lines']) == 2
        assert deprecations_json['report_lines'][0]['location_label'] == 'test form / Field "file"'
        assert deprecations_json['report_lines'][0]['category'] == 'fargo'
        assert deprecations_json['report_lines'][1]['location_label'] == 'test / Attachment'
        assert deprecations_json['report_lines'][1]['category'] == 'fargo'


def test_legacy_wf_form_variables(pub):
    FormDef.wipe()
    Workflow.wipe()

    formdef = FormDef()
    formdef.name = 'test form'
    formdef.store()

    workflow = Workflow(name='test')
    st0 = workflow.add_status('Status0', 'st0')
    display = st0.add_action('displaymsg')
    display.message = 'message with {{ test_var_foo }}'  # unprefixed
    workflow.store()

    # with no matching form action
    DeprecationsScan().execute()
    with open(os.path.join(pub.app_dir, 'deprecations.json')) as f:
        deprecations_json = json.loads(f.read())
        assert len(deprecations_json['report_lines']) == 0

    # with form action
    wf_form = st0.add_action('form')
    wf_form.varname = 'test'
    workflow.store()

    DeprecationsScan().execute()
    with open(os.path.join(pub.app_dir, 'deprecations.json')) as f:
        deprecations_json = json.loads(f.read())
        assert len(deprecations_json['report_lines']) == 1
        assert deprecations_json['report_lines'][0]['location_label'] == 'test / Alert'
        assert deprecations_json['report_lines'][0]['category'] == 'legacy_wf_form_variables'

    # with legacy prefix
    display.message = 'message with {{ form_workflow_data_test_var_foo }}'
    workflow.store()

    DeprecationsScan().execute()
    with open(os.path.join(pub.app_dir, 'deprecations.json')) as f:
        deprecations_json = json.loads(f.read())
        assert len(deprecations_json['report_lines']) == 1
        assert deprecations_json['report_lines'][0]['location_label'] == 'test / Alert'
        assert deprecations_json['report_lines'][0]['category'] == 'legacy_wf_form_variables'

    # with new format
    display.message = 'message with {{ form_workflow_form_test_var_foo }}'
    workflow.store()

    DeprecationsScan().execute()
    with open(os.path.join(pub.app_dir, 'deprecations.json')) as f:
        deprecations_json = json.loads(f.read())
        assert len(deprecations_json['report_lines']) == 0

    # with storing in workflow data disabled
    display.message = 'message with {{ form_workflow_data_test_var_foo }}'
    workflow.store()

    pub.site_options.set('options', 'disable-workflow-form-to-workflow-data', 'true')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)

    DeprecationsScan().execute()
    with open(os.path.join(pub.app_dir, 'deprecations.json')) as f:
        deprecations_json = json.loads(f.read())
        assert len(deprecations_json['report_lines']) == 0
