import datetime
import io
import json
import os
import time
import urllib.parse
import xml.etree.ElementTree as ET
import zipfile

import pytest
from django.utils.timezone import make_aware

from wcs import fields
from wcs.blocks import BlockDef
from wcs.carddef import CardDef
from wcs.data_sources import NamedDataSource
from wcs.formdef import FormDef
from wcs.qommon import ods
from wcs.qommon.afterjobs import AfterJob
from wcs.qommon.http_request import HTTPRequest
from wcs.qommon.upload_storage import PicklableUpload

from ..utilities import clean_temporary_pub, create_temporary_pub, get_app, login
from .test_all import create_superuser


@pytest.fixture
def pub(emails):
    pub = create_temporary_pub()

    req = HTTPRequest(None, {'SCRIPT_NAME': '/', 'SERVER_NAME': 'example.net'})
    pub.set_app_dir(req)
    pub.cfg['identification'] = {'methods': ['password']}
    pub.cfg['language'] = {'language': 'en'}
    pub.write_cfg()
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        fd.write(
            '''
    [api-secrets]
    coucou = 1234
    '''
        )

    return pub


def teardown_module(module):
    clean_temporary_pub()


def test_backoffice_csv(pub):
    AfterJob.wipe()
    create_superuser(pub)

    datasource = {
        'type': 'jsonvalue',
        'value': '[{"id": "A", "text": "aa"}, {"id": "B", "text": "bb"}, {"id": "C", "text": "cc"}]',
    }
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.StringField(
            id='1', label='1st field', display_locations=['validation', 'summary', 'listings']
        ),
        fields.ItemField(
            id='2',
            label='2nd field',
            items=['foo', 'bar', 'baz'],
            display_locations=['validation', 'summary', 'listings'],
        ),
        fields.ItemField(id='3', label='3rd field', data_source=datasource, varname='foo'),
    ]
    formdef.workflow_roles = {'_receiver': 1}
    formdef.store()

    formdef.data_class().wipe()
    for i in range(3):
        formdata = formdef.data_class()()
        formdata.receipt_time = make_aware(datetime.datetime(2015, 1, 1))
        formdata.data = {'1': 'FOO BAR %d' % i}
        if i == 0:
            formdata.data['2'] = 'foo'
            formdata.data['2_display'] = 'foo'
            formdata.data['3'] = 'A'
            formdata.data['3_display'] = 'aa'
        else:
            formdata.data['2'] = 'baz'
            formdata.data['2_display'] = 'baz'
            formdata.data['3'] = 'C'
            formdata.data['3_display'] = 'cc'
        if i < 2:
            formdata.jump_status('new')
        else:
            formdata.status = 'draft'
        formdata.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/')
    resp = resp.click('Export a Spreadsheet')
    resp.form['format'] = 'csv'
    resp = resp.form.submit('submit')
    assert resp.headers['content-type'].startswith('text/')
    assert len(resp.text.splitlines()) == 3  # 3 + header line
    assert len(resp.text.splitlines()[0].split(',')) == 7

    formdef = FormDef.get_by_urlname('form-title')
    formdef.fields[-1].display_locations = ['validation', 'summary', 'listings']
    formdef.store()
    resp = app.get('/backoffice/management/form-title/')
    resp = resp.click('Export a Spreadsheet')
    resp.form['format'] = 'csv'
    resp = resp.form.submit('submit')
    assert len(resp.text.splitlines()[0].split(',')) == 9

    # check item fields with datasources get two columns (id & text)
    assert resp.text.splitlines()[0].split(',')[6] == '"3rd field (identifier)"'
    assert resp.text.splitlines()[0].split(',')[7] == '"3rd field"'
    assert resp.text.splitlines()[1].split(',')[6] == '"C"'
    assert resp.text.splitlines()[1].split(',')[7] == '"cc"'

    resp = app.get('/backoffice/management/form-title/')
    resp.forms['listing-settings']['filter'] = 'all'
    resp = resp.forms['listing-settings'].submit().follow()
    resp_csv = resp.click('Export a Spreadsheet')
    resp_csv.form['format'] = 'csv'
    resp_csv = resp_csv.form.submit('submit')
    assert len(resp_csv.text.splitlines()) == 3

    # test status filter
    resp.forms['listing-settings']['filter'] = 'pending'
    resp.forms['listing-settings']['filter-2'].checked = True
    resp = resp.forms['listing-settings'].submit().follow()
    resp.forms['listing-settings']['filter-2-value'] = 'baz'
    resp = resp.forms['listing-settings'].submit().follow()
    resp_csv = resp.click('Export a Spreadsheet')
    resp_csv.form['format'] = 'csv'
    resp_csv = resp_csv.form.submit('submit')
    assert len(resp_csv.text.splitlines()) == 2

    # test criteria filters
    resp.forms['listing-settings']['filter-start'].checked = True
    resp = resp.forms['listing-settings'].submit().follow()
    resp.forms['listing-settings']['filter-start-value'] = datetime.datetime(2015, 2, 1).strftime('%Y-%m-%d')
    resp = resp.forms['listing-settings'].submit().follow()
    resp_csv = resp.click('Export a Spreadsheet')
    resp_csv.form['format'] = 'csv'
    resp_csv = resp_csv.form.submit('submit')
    assert len(resp_csv.text.splitlines()) == 1

    resp.forms['listing-settings']['filter-start-value'] = datetime.datetime(2014, 2, 1).strftime('%Y-%m-%d')
    resp = resp.forms['listing-settings'].submit().follow()
    resp.forms['listing-settings']['filter-2-value'] = 'baz'
    resp = resp.forms['listing-settings'].submit().follow()
    resp_csv = resp.click('Export a Spreadsheet')
    resp_csv.form['format'] = 'csv'
    resp_csv = resp_csv.form.submit('submit')
    assert len(resp_csv.text.splitlines()) == 2
    assert 'Created' in resp_csv.text.splitlines()[0]

    # test column selection
    resp.forms['listing-settings']['time'].checked = False
    resp = resp.forms['listing-settings'].submit().follow()
    resp_csv = resp.click('Export a Spreadsheet')
    resp_csv.form['format'] = 'csv'
    resp_csv = resp_csv.form.submit('submit')
    assert 'Created' not in resp_csv.text.splitlines()[0]

    # test no quote when exporting a single column
    formdata = formdef.data_class()()
    formdata.data = {
        # check characters commonly used as separators don't break the export
        '1': 'delimiters ,;\t#',
        '2': 'foo',
        '3': 'foo',
    }
    formdata.store()
    formdata.jump_status('new')

    listing_settings = resp.forms['listing-settings']

    # uncheck everything
    for _, field in listing_settings.field_order:
        if field.attrs.get('type', None) == 'checkbox':
            field.checked = False

    # check a single column
    listing_settings['1'].checked = True
    listing_settings['filter'] = 'all'

    resp = listing_settings.submit().follow()
    resp_csv = resp.click('Export a Spreadsheet')
    resp_csv.form['format'] = 'csv'
    resp_csv = resp_csv.form.submit('submit')
    assert sorted(resp_csv.text.splitlines()) == [
        '1st field',
        'FOO BAR 0',
        'FOO BAR 1',
        'delimiters ,;\t#',
    ]

    resp = app.get('/backoffice/management/form-title/')
    listing_settings = resp.forms['listing-settings']

    # uncheck everything
    for _, field in listing_settings.field_order:
        if field.attrs.get('type', None) == 'checkbox':
            field.checked = False

    # check a single "item" field (that will give two columns)
    listing_settings['3'].checked = True
    listing_settings['filter'] = 'all'

    resp = listing_settings.submit().follow()
    resp_csv = resp.click('Export a Spreadsheet')
    resp_csv.form['format'] = 'csv'
    resp_csv = resp_csv.form.submit('submit')
    assert resp_csv.text.splitlines() == [
        '"3rd field (identifier)","3rd field"',
        '"foo",""',
        '"C","cc"',
        '"A","aa"',
    ]
    assert AfterJob.count() == 0


@pytest.fixture
def threshold():
    from wcs.backoffice.management import FormPage

    FormPage.WCS_SYNC_EXPORT_LIMIT = 1
    yield
    FormPage.WCS_SYNC_EXPORT_LIMIT = 100


def test_backoffice_export_long_listings(pub, threshold):
    create_superuser(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.StringField(
            id='1', label='1st field', display_locations=['validation', 'summary', 'listings']
        ),
    ]
    formdef.workflow_roles = {'_receiver': 1}
    formdef.store()

    formdef.data_class().wipe()
    for i in range(2):
        formdata = formdef.data_class()()
        formdata.receipt_time = make_aware(datetime.datetime(2015, 1, 1))
        formdata.data = {'1': 'BAZ BAZ %d' % i}
        formdata.jump_status('new')
        formdata.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/')
    resp = resp.click('Export a Spreadsheet')
    resp.form['format'] = 'csv'
    resp = resp.form.submit('submit')
    assert resp.location.startswith('http://example.net/backoffice/processing?job=')
    resp = resp.follow()
    assert 'completed' in resp.text
    resp = resp.click('Download Export')
    resp_lines = resp.text.splitlines()
    assert resp_lines[0] == '"Number","Created","Last Modified","User Label","1st field","Status"'
    assert len(resp_lines) == 3
    assert resp_lines[1].split(',')[1].startswith('"' + formdata.receipt_time.strftime('%Y-%m-%d'))
    assert resp_lines[1].split(',')[2].startswith('"' + formdata.last_update_time.strftime('%Y-%m-%d'))

    resp = app.get('/backoffice/management/form-title/')
    resp = resp.click('Export a Spreadsheet')
    resp.form['format'] = 'ods'
    resp = resp.form.submit('submit')
    assert resp.location.startswith('http://example.net/backoffice/processing?job=')
    job_id = urllib.parse.parse_qs(urllib.parse.urlparse(resp.location).query)['job'][0]
    resp = resp.follow()
    assert 'completed' in resp.text
    resp = resp.click('Download Export')
    assert resp.content_type == 'application/vnd.oasis.opendocument.spreadsheet'

    # check afterjob ajax call
    status_resp = app.get('/afterjobs/' + job_id)
    assert status_resp.json == {'status': 'completed', 'message': 'completed 2/2 (100%)'}

    # check error handling
    app.get('/afterjobs/whatever', status=404)


def test_backoffice_csv_export_channel(pub):
    create_superuser(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = []
    formdef.workflow_roles = {'_receiver': 1}
    formdef.store()

    formdef.data_class().wipe()
    formdata = formdef.data_class()()
    formdata.jump_status('new')
    formdata.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/')
    resp_csv = resp.click('Export a Spreadsheet')
    resp_csv.form['format'] = 'csv'
    resp_csv = resp_csv.form.submit('submit')
    assert 'Channel' not in resp_csv.text.splitlines()[0]

    # add submission channel column
    resp.forms['listing-settings']['submission_channel'].checked = True
    resp = resp.forms['listing-settings'].submit().follow()
    resp_csv = resp.click('Export a Spreadsheet')
    resp_csv.form['format'] = 'csv'
    resp_csv = resp_csv.form.submit('submit')
    assert resp_csv.text.splitlines()[0].split(',')[-1] == '"Channel"'
    assert resp_csv.text.splitlines()[1].split(',')[-1] == '"Web"'


def test_backoffice_csv_export_anonymised(pub):
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)

    create_superuser(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = []
    formdef.workflow_roles = {'_receiver': 1}
    formdef.store()

    formdef.data_class().wipe()
    formdata = formdef.data_class()()
    formdata.jump_status('new')
    formdata.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/')
    resp_csv = resp.click('Export a Spreadsheet')
    resp_csv.form['format'] = 'csv'
    resp_csv = resp_csv.form.submit('submit')
    assert resp_csv.text.splitlines()[0].split(',')[-1] != '"Anonymised"'

    # add anonymised column
    resp.forms['listing-settings']['anonymised'].checked = True
    resp = resp.forms['listing-settings'].submit().follow()
    resp_csv = resp.click('Export a Spreadsheet')
    resp_csv.form['format'] = 'csv'
    resp_csv = resp_csv.form.submit('submit')
    assert resp_csv.text.splitlines()[0].split(',')[-1] == '"Anonymised"'
    assert resp_csv.text.splitlines()[1].split(',')[-1] == '"No"'


def test_backoffice_csv_export_fields(pub):
    create_superuser(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.StringField(id='123', required='required', label='Test', varname='foo'),
        fields.StringField(id='234', required='required', label='Test2', varname='bar'),
        fields.EmailField(id='345', required='required', label='Test3', varname='email'),
        fields.DateField(id='456', required='required', label='Test4', varname='date'),
        fields.FileField(id='567', required='required', label='Test5', varname='file'),
        fields.BoolField(id='678', required='required', label='Test6', varname='bool'),
        fields.NumericField(id='890', label='Test7', varname='numeric'),
    ]
    formdef.workflow_roles = {'_receiver': 1}
    formdef.store()

    upload = PicklableUpload('test.jpeg', 'image/jpeg')
    with open(os.path.join(os.path.dirname(__file__), '..', 'image-with-gps-data.jpeg'), 'rb') as fd:
        upload.receive([fd.read()])

    formdef.data_class().wipe()
    formdata = formdef.data_class()()
    formdata.data = {
        '123': 'foo',
        '234': 'bar',
        '345': 'blah@example.invalid',
        '456': time.strptime('2020-04-24', '%Y-%m-%d'),
        '567': upload,
        '678': True,
        '890': 5.5,
    }
    formdata.just_created()
    formdata.jump_status('new')
    formdata.store()
    formdata = formdef.data_class()()
    formdata.data = {
        '123': 'foo2',
        '234': 'bar2',
        '345': 'blah2@example.invalid',
        '456': time.strptime('2020-04-25', '%Y-%m-%d'),
        '567': upload,
        '678': False,
        '890': 2.5,
    }
    formdata.just_created()
    formdata.jump_status('new')
    formdata.store()

    # add an extra empty formdata
    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.jump_status('new')
    formdata.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/')
    resp_csv = resp.click('Export a Spreadsheet')
    resp_csv.form['format'] = 'csv'
    resp_csv = resp_csv.form.submit('submit')
    resp.forms['listing-settings']['345'].checked = True
    resp.forms['listing-settings']['456'].checked = True
    resp.forms['listing-settings']['567'].checked = True
    resp.forms['listing-settings']['678'].checked = True
    resp.forms['listing-settings']['890'].checked = True
    resp.forms['listing-settings']['order_by'] = 'id'
    resp = resp.forms['listing-settings'].submit().follow()
    resp_csv = resp.click('Export a Spreadsheet')
    resp_csv.form['format'] = 'csv'
    resp_csv = resp_csv.form.submit('submit')
    assert resp_csv.text.splitlines()[0].split(',')[-5:] == [
        '"Test3"',
        '"Test4"',
        '"Test5"',
        '"Test6"',
        '"Test7"',
    ]
    line1 = resp_csv.text.splitlines()[1].split(',')[-5:]
    line2 = resp_csv.text.splitlines()[2].split(',')[-5:]
    line3 = resp_csv.text.splitlines()[3].split(',')[-5:]
    assert line1 == [
        '"blah@example.invalid"',
        '"2020-04-24"',
        '"test.jpeg"',
        '"Yes"',
        '"5.5"',
    ]
    assert line2 == [
        '"blah2@example.invalid"',
        '"2020-04-25"',
        '"test.jpeg"',
        '"No"',
        '"2.5"',
    ]
    assert line3 == ['""', '""', '""', '""', '""']

    # export as ods
    resp_csv = resp.click('Export a Spreadsheet')
    resp_csv = resp_csv.form.submit('submit')  # no error


def test_backoffice_csv_export_block(pub):
    create_superuser(pub)

    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.StringField(id='123', required='required', label='Test', varname='foo'),
        fields.StringField(id='234', required='required', label='Test2', varname='bar'),
        fields.EmailField(id='345', required='required', label='Test3', varname='email'),
        fields.DateField(id='456', required='required', label='Test4', varname='date'),
        fields.FileField(id='567', required='required', label='Test5', varname='file'),
        fields.BoolField(id='678', required='required', label='Test6', varname='bool'),
    ]
    block.digest_template = 'X{{foobar_var_foo}}Y'
    block.store()

    empty_block = BlockDef()
    empty_block.name = 'empty_block'
    empty_block.fields = [
        fields.StringField(
            id='910', required='required', label='Empty Block Field', varname='empty_block_field'
        ),
    ]
    empty_block.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.BlockField(id='2', label='Empty Block', block_slug='empty_block', max_items='1'),
        fields.BlockField(id='1', label='test', block_slug='foobar', max_items='3'),
    ]
    formdef.workflow_roles = {'_receiver': 1}
    formdef.store()

    upload = PicklableUpload('test.jpeg', 'image/jpeg')
    with open(os.path.join(os.path.dirname(__file__), '..', 'image-with-gps-data.jpeg'), 'rb') as fd:
        upload.receive([fd.read()])

    formdef.data_class().wipe()
    formdata = formdef.data_class()()
    formdata.data = {
        '1': {
            'data': [
                {
                    '123': 'foo',
                    '234': 'bar',
                    '345': 'blah@example.invalid',
                    '456': time.strptime('2020-04-24', '%Y-%m-%d'),
                    '567': upload,
                    '678': True,
                },
                {
                    '123': 'foo2',
                    '234': 'bar2',
                    '345': 'blah2@example.invalid',
                    '456': time.strptime('2020-04-25', '%Y-%m-%d'),
                    '567': upload,
                    '678': False,
                },
            ],
            'schema': {
                '123': 'string',
                '234': 'string',
                '345': 'email',
                '456': 'date',
                '567': 'file',
                '678': 'bool',
            },
        },
    }
    formdata.just_created()
    formdata.jump_status('new')
    formdata.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/')
    resp_csv = resp.click('Export a Spreadsheet')
    resp_csv.form['format'] = 'csv'
    resp_csv = resp_csv.form.submit('submit')
    resp.forms['listing-settings']['1'].checked = True
    resp.forms['listing-settings']['1-345'].checked = True
    resp.forms['listing-settings']['1-456'].checked = True
    resp.forms['listing-settings']['1-567'].checked = True
    resp.forms['listing-settings']['1-678'].checked = True
    resp.forms['listing-settings']['2-910'].checked = True
    resp = resp.forms['listing-settings'].submit().follow()
    resp_csv = resp.click('Export a Spreadsheet')
    resp_csv.form['format'] = 'csv'
    resp_csv = resp_csv.form.submit('submit')
    assert resp_csv.text.splitlines()[0].split(',')[-16:] == [
        '"Empty Block - Empty Block Field"',
        '"test - 1"',
        '"test - 2"',
        '"test - 3"',
        '"test - Test3 - 1"',
        '"test - Test3 - 2"',
        '"test - Test3 - 3"',
        '"test - Test4 - 1"',
        '"test - Test4 - 2"',
        '"test - Test4 - 3"',
        '"test - Test5 - 1"',
        '"test - Test5 - 2"',
        '"test - Test5 - 3"',
        '"test - Test6 - 1"',
        '"test - Test6 - 2"',
        '"test - Test6 - 3"',
    ]
    assert resp_csv.text.splitlines()[1].split(',')[-16:] == [
        '""',
        '"XfooY"',
        '"Xfoo2Y"',
        '""',
        '"blah@example.invalid"',
        '"blah2@example.invalid"',
        '""',
        '"2020-04-24"',
        '"2020-04-25"',
        '""',
        '"test.jpeg"',
        '"test.jpeg"',
        '""',
        '"Yes"',
        '"No"',
        '""',
    ]

    # export as ods
    resp_csv = resp.click('Export a Spreadsheet')
    resp_csv = resp_csv.form.submit('submit')  # no error


def test_backoffice_csv_export_block_with_item(pub):
    create_superuser(pub)
    BlockDef.wipe()
    NamedDataSource.wipe()

    data_source = NamedDataSource(name='foo')
    data_source.data_source = {
        'type': 'jsonvalue',
        'value': json.dumps(
            [
                {'id': '1', 'text': 'one'},
                {'id': '2', 'text': 'two'},
            ]
        ),
    }
    data_source.store()

    block = BlockDef()
    block.name = 'foobar'
    block.fields = [fields.ItemField(id='123', label='Item', data_source={'type': 'foo'}, varname='foo')]
    block.digest_template = 'X{{block_var_foo}}Y'
    block.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.BlockField(id='1', label='Block', block_slug='foobar', max_items='1'),
    ]
    formdef.workflow_roles = {'_receiver': 1}
    formdef.store()
    formdef.data_class().wipe()
    formdata = formdef.data_class()()
    formdata.data = {
        '1': {'data': [{'123': '1', '123_display': 'one'}], 'schema': {'123': 'item'}},
    }
    formdata.just_created()
    formdata.jump_status('new')
    formdata.store()

    app = login(get_app(pub))

    def get_csv_response():
        resp = app.get('/backoffice/management/form-title/')
        resp_csv = resp.click('Export a Spreadsheet')
        resp_csv.form['format'] = 'csv'
        resp_csv = resp_csv.form.submit('submit')
        resp.forms['listing-settings']['1'].checked = True
        resp.forms['listing-settings']['1-123'].checked = True
        resp = resp.forms['listing-settings'].submit().follow()
        resp_csv = resp.click('Export a Spreadsheet')
        resp_csv.form['format'] = 'csv'
        return resp_csv.form.submit('submit')

    resp_csv = get_csv_response()
    assert resp_csv.text.splitlines()[0].split(',') == [
        '"Number"',
        '"Created"',
        '"Last Modified"',
        '"User Label"',
        '"Status"',
        '"Block"',
        '"Block - Item (identifier)"',
        '"Block - Item"',
    ]
    assert resp_csv.text.splitlines()[1].split(',')[-3:] == ['"XoneY"', '"1"', '"one"']

    # allow multiple rows in block
    formdef.fields[0].max_items = '3'
    formdef.store()

    resp_csv = get_csv_response()
    assert resp_csv.text.splitlines()[0].split(',') == [
        '"Number"',
        '"Created"',
        '"Last Modified"',
        '"User Label"',
        '"Status"',
        '"Block - 1"',
        '"Block - 2"',
        '"Block - 3"',
        '"Block - Item (identifier) - 1"',
        '"Block - Item - 1"',
        '"Block - Item (identifier) - 2"',
        '"Block - Item - 2"',
        '"Block - Item (identifier) - 3"',
        '"Block - Item - 3"',
    ]
    assert (
        resp_csv.text.splitlines()[1].split(',')[-9:] == ['"XoneY"', '""', '""', '"1"', '"one"'] + ['""'] * 4
    )

    formdata.data = {
        '1': {
            'data': [{'123': '1', '123_display': 'one'}, {'123': '2', '123_display': 'two'}],
            'schema': {'123': 'item'},
        },
    }
    formdata.store()

    resp_csv = get_csv_response()
    assert (
        resp_csv.text.splitlines()[1].split(',')[-9:]
        == ['"XoneY"', '"XtwoY"', '""', '"1"', '"one"', '"2"', '"two"'] + ['""'] * 2
    )


def test_backoffice_csv_export_block_with_file(pub):
    create_superuser(pub)
    BlockDef.wipe()

    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.FileField(id='123', required='required', label='Test'),
    ]
    block.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.BlockField(id='1', label='Block', block_slug='foobar', max_items='1'),
    ]
    formdef.workflow_roles = {'_receiver': 1}
    formdef.store()
    formdef.data_class().wipe()

    upload = PicklableUpload('test.txt', 'text/plain')
    upload.receive([b'text'])

    formdata = formdef.data_class()()
    formdata.data = {
        '1': {'data': [{'123': upload}], 'schema': {'123': 'file'}},
    }
    formdata.just_created()
    formdata.jump_status('new')
    formdata.store()

    formdata2 = formdef.data_class()()
    formdata2.data = {
        '1': {'data': [{'123': None}], 'schema': {'123': 'file'}},
    }
    formdata2.just_created()
    formdata2.jump_status('new')
    formdata2.store()

    app = login(get_app(pub))

    def get_csv_response():
        resp = app.get('/backoffice/management/form-title/?order_by=id')
        resp_csv = resp.click('Export a Spreadsheet')
        resp_csv.form['format'] = 'csv'
        resp_csv = resp_csv.form.submit('submit')
        resp.forms['listing-settings']['1'].checked = True
        resp.forms['listing-settings']['1-123'].checked = True
        resp = resp.forms['listing-settings'].submit().follow()
        resp_csv = resp.click('Export a Spreadsheet')
        resp_csv.form['format'] = 'csv'
        return resp_csv.form.submit('submit')

    resp_csv = get_csv_response()
    assert resp_csv.text.splitlines()[0].split(',') == [
        '"Number"',
        '"Created"',
        '"Last Modified"',
        '"User Label"',
        '"Status"',
        '"Block"',
        '"Block - Test"',
    ]
    assert [x.split(',')[0] for x in resp_csv.text.splitlines()] == [
        '"Number"',
        f'"{formdata.get_display_id()}"',
        f'"{formdata2.get_display_id()}"',
    ]
    assert resp_csv.text.splitlines()[1].split(',')[-1] == '"test.txt"'
    assert resp_csv.text.splitlines()[2].split(',')[-1] == '""'


def test_backoffice_csv_export_table(pub):
    create_superuser(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.TableField(
            id='1',
            label='table field',
            rows=['row1', 'row2'],
            columns=['col1', 'col2'],
            display_locations=['validation', 'summary', 'listings'],
        )
    ]
    formdef.workflow_roles = {'_receiver': 1}
    formdef.store()

    formdef.data_class().wipe()
    formdata = formdef.data_class()()
    formdata.data = {'1': [['a', 'b'], ['c', 'd']]}
    formdata.just_created()
    formdata.jump_status('new')
    formdata.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/')
    resp_csv = resp.click('Export a Spreadsheet')
    resp_csv.form['format'] = 'csv'
    resp_csv = resp_csv.form.submit('submit')
    assert resp_csv.text.splitlines()[0].split(',')[-5:] == [
        '"table field - col1 / row1"',
        '"col1 / row2"',
        '"col2 / row1"',
        '"col2 / row2"',
        '"Status"',
    ]
    assert resp_csv.text.splitlines()[1].split(',')[-5:] == [
        '"a"',
        '"c"',
        '"b"',
        '"d"',
        '"New"',
    ]

    # export as ods
    resp_csv = resp.click('Export a Spreadsheet')
    resp_csv = resp_csv.form.submit('submit')  # no error


def test_backoffice_csv_export_ordering(pub):
    create_superuser(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.ItemField(
            id='1',
            label='field 1',
            items=['foo', 'bar', 'baz'],
            display_locations=['validation', 'summary', 'listings'],
        ),
    ]
    formdef.workflow_roles = {'_receiver': 1}
    formdef.store()

    formdef.data_class().wipe()
    formdata = formdef.data_class()()
    formdata.data = {'1': 'foo', '1_display': 'foo'}
    formdata.jump_status('new')
    formdata.store()
    formdata = formdef.data_class()()
    formdata.data = {'1': 'bar', '1_display': 'bar'}
    formdata.jump_status('new')
    formdata.store()

    app = login(get_app(pub))
    resp_csv = app.get('/backoffice/management/form-title/csv')
    assert resp_csv.text.splitlines()[1].split(',')[-3:] == ['"-"', '"bar"', '"New"']
    assert resp_csv.text.splitlines()[2].split(',')[-3:] == ['"-"', '"foo"', '"New"']
    resp_csv = app.get('/backoffice/management/form-title/csv?order_by=id')
    assert resp_csv.text.splitlines()[1].split(',')[-3:] == ['"-"', '"foo"', '"New"']
    assert resp_csv.text.splitlines()[2].split(',')[-3:] == ['"-"', '"bar"', '"New"']


def test_backoffice_ods(pub):
    create_superuser(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.FileField(id='4', label='file field', display_locations=['validation', 'summary', 'listings']),
        fields.DateField(id='5', label='date field', display_locations=['validation', 'summary', 'listings']),
        fields.StringField(
            id='6',
            label='number field',
            display_locations=['validation', 'summary', 'listings'],
        ),
        fields.StringField(
            id='7',
            label='phone field',
            display_locations=['validation', 'summary', 'listings'],
        ),
        fields.DateField(
            id='8',
            label='very old field',
            display_locations=['validation', 'summary', 'listings'],
        ),
        fields.StringField(
            id='9',
            label='string field',
            display_locations=['validation', 'summary', 'listings'],
        ),
        fields.StringField(
            id='10',
            label='number with comma field',
            display_locations=['validation', 'summary', 'listings'],
        ),
        fields.StringField(
            id='11',
            label='not a number, with underscore',
            display_locations=['validation', 'summary', 'listings'],
        ),
        fields.StringField(
            id='12',
            label='number field with zero',
            display_locations=['validation', 'summary', 'listings'],
        ),
        fields.NumericField(
            id='13',
            label='real numeric field',
            display_locations=['validation', 'summary', 'listings'],
        ),
        fields.StringField(
            id='14',
            label='string with SIRET',
            display_locations=['validation', 'summary', 'listings'],
            validation={'type': 'siret-fr'},
        ),
    ]
    formdef.workflow_roles = {'_receiver': 1}
    formdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/')
    resp = resp.click('Export a Spreadsheet')
    resp = resp.form.submit('submit')
    assert resp.headers['content-type'] == 'application/vnd.oasis.opendocument.spreadsheet'
    assert 'filename=form-title.ods' in resp.headers['content-disposition']
    assert resp.body[:2] == b'PK'  # ods has a zip container

    formdef.data_class().wipe()
    formdata = formdef.data_class()()
    formdata.data = {
        '4': PicklableUpload('/foo/bar', content_type='text/plain'),
        '5': time.strptime('2015-05-12', '%Y-%m-%d'),
        '6': '12345',
        '7': '0102030405',
        '8': time.strptime('1871-03-18', '%Y-%m-%d'),
        '9': 'plop\npl\x1dop',  # with control characters
        '10': ' 123,45',
        '11': '1_000_000',
        '12': '0',
        '13': 234.56,
        '14': '44317013900036',
    }
    formdata.data['4'].receive([b'hello world'])
    formdata.just_created()
    formdata.jump_status('new')
    formdata.store()

    resp = app.get('/backoffice/management/form-title/')
    resp = resp.click('Export a Spreadsheet')
    resp = resp.form.submit('submit')
    assert resp.headers['content-type'] == 'application/vnd.oasis.opendocument.spreadsheet'
    assert 'filename=form-title.ods' in resp.headers['content-disposition']
    assert resp.body[:2] == b'PK'  # ods has a zip container

    with zipfile.ZipFile(io.BytesIO(resp.body)) as zipf:
        with zipf.open('content.xml') as fd:
            ods_sheet = ET.parse(fd)
        with zipf.open('styles.xml') as fd:
            styles_sheet = ET.parse(fd)
    # check the ods contains a link to the document
    elem = ods_sheet.findall('.//{%s}a' % ods.NS['text'])[0]
    assert (
        elem.attrib['{%s}href' % ods.NS['xlink']]
        == 'http://example.net/backoffice/management/form-title/%s/files/4/bar' % formdata.id
    )
    resp = app.get(elem.attrib['{%s}href' % ods.NS['xlink']])
    assert resp.text == 'hello world'

    all_texts = [
        x.text for x in ods_sheet.findall('.//{%s}table-row//{%s}p' % (ods.NS['table'], ods.NS['text']))
    ]
    created_column = all_texts.index('Created')
    date_column = all_texts.index('date field')
    number_column = all_texts.index('number field')
    phone_column = all_texts.index('phone field')
    old_column = all_texts.index('very old field')
    string_column = all_texts.index('string field')
    comma_number_column = all_texts.index('number with comma field')
    not_number_column = all_texts.index('not a number, with underscore')
    zero_number_column = all_texts.index('number field with zero')
    numeric_column = all_texts.index('real numeric field')
    siret_column = all_texts.index('string with SIRET')
    status_column = all_texts.index('Status')

    for row in ods_sheet.findall('.//{%s}table-row' % ods.NS['table']):
        if (
            row.findall('.//{%s}table-cell/{%s}p' % (ods.NS['table'], ods.NS['text']))[0].text
            == formdata.get_display_id()
        ):
            break
    else:
        assert False, 'failed to find data row'

    assert (
        row.findall('.//{%s}table-cell' % ods.NS['table'])[created_column].attrib[
            '{%s}value-type' % ods.NS['office']
        ]
        == 'date'
    )
    assert (
        row.findall('.//{%s}table-cell' % ods.NS['table'])[created_column].attrib[
            '{%s}style-name' % ods.NS['table']
        ]
        == 'DateTime'
    )
    assert (
        row.findall('.//{%s}table-cell' % ods.NS['table'])[date_column].attrib[
            '{%s}value-type' % ods.NS['office']
        ]
        == 'date'
    )
    assert (
        row.findall('.//{%s}table-cell' % ods.NS['table'])[date_column].attrib[
            '{%s}style-name' % ods.NS['table']
        ]
        == 'Date'
    )
    assert (
        row.findall('.//{%s}table-cell' % ods.NS['table'])[number_column].attrib[
            '{%s}value-type' % ods.NS['office']
        ]
        == 'float'
    )
    assert (
        row.findall('.//{%s}table-cell' % ods.NS['table'])[number_column].attrib[
            '{%s}value' % ods.NS['office']
        ]
        == '12345'
    )
    assert (
        row.findall('.//{%s}table-cell' % ods.NS['table'])[phone_column].attrib[
            '{%s}value-type' % ods.NS['office']
        ]
        == 'string'
    )
    assert (
        row.findall('.//{%s}table-cell' % ods.NS['table'])[old_column].attrib[
            '{%s}value-type' % ods.NS['office']
        ]
        == 'date'
    )
    assert (
        row.findall('.//{%s}table-cell' % ods.NS['table'])[old_column].attrib[
            '{%s}date-value' % ods.NS['office']
        ]
        == '1871-03-18'
    )
    assert (
        row.findall('.//{%s}table-cell' % ods.NS['table'])[string_column].find('{%s}p' % ods.NS['text']).text
        == 'plop\nplop'
    )
    assert (
        row.findall('.//{%s}table-cell' % ods.NS['table'])[comma_number_column].attrib[
            '{%s}value' % ods.NS['office']
        ]
        == '123.45'
    )
    assert (
        row.findall('.//{%s}table-cell' % ods.NS['table'])[not_number_column].attrib[
            '{%s}value-type' % ods.NS['office']
        ]
        == 'string'
    )
    assert (
        row.findall('.//{%s}table-cell' % ods.NS['table'])[zero_number_column].attrib[
            '{%s}value' % ods.NS['office']
        ]
        == '0'
    )
    assert (
        row.findall('.//{%s}table-cell' % ods.NS['table'])[numeric_column].attrib[
            '{%s}value-type' % ods.NS['office']
        ]
        == 'float'
    )
    assert (
        row.findall('.//{%s}table-cell' % ods.NS['table'])[numeric_column].attrib[
            '{%s}value' % ods.NS['office']
        ]
        == '234.56'
    )
    assert (
        row.findall('.//{%s}table-cell' % ods.NS['table'])[siret_column].attrib[
            '{%s}value-type' % ods.NS['office']
        ]
        == 'string'
    )
    assert (
        row.findall('.//{%s}table-cell' % ods.NS['table'])[status_column].attrib[
            '{%s}style-name' % ods.NS['table']
        ]
        == 'StatusStyle-new'
    )
    status_style = [
        x
        for x in styles_sheet.findall('.//{%s}style' % ods.NS['style'])
        if x.attrib['{%s}name' % ods.NS['style']] == 'StatusStyle-new'
    ][0]
    cell_props = status_style.find('{%s}table-cell-properties' % ods.NS['style'])
    text_props = status_style.find('{%s}text-properties' % ods.NS['style'])
    assert cell_props.attrib['{%s}background-color' % ods.NS['fo']] == '#66FF00'
    assert text_props.attrib['{%s}color' % ods.NS['fo']] == '#000000'


def test_backoffice_empty_ods(pub):
    create_superuser(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = []
    formdef.workflow_roles = {'_receiver': 1}
    formdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/')
    resp = resp.click('Export a Spreadsheet')
    resp.form['include_header_line'].checked = False
    resp = resp.form.submit('submit')
    assert resp.headers['content-type'] == 'application/vnd.oasis.opendocument.spreadsheet'
    assert resp.body[:2] == b'PK'  # ods has a zip container


def test_backoffice_header_line(pub):
    create_superuser(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.StringField(
            id='1', label='1st field', display_locations=['validation', 'summary', 'listings']
        ),
    ]
    formdef.workflow_roles = {'_receiver': 1}
    formdef.store()

    formdef.data_class().wipe()
    for i in range(3):
        formdata = formdef.data_class()()
        formdata.receipt_time = make_aware(datetime.datetime(2015, 1, 1))
        formdata.data = {'1': 'FOO BAR %d' % i}
        formdata.jump_status('new')
        formdata.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/')
    resp = resp.click('Export a Spreadsheet')
    resp.form['format'] = 'csv'
    assert resp.form['include_header_line'].checked is True
    resp = resp.form.submit('submit')
    assert resp.headers['content-type'].startswith('text/')
    assert len(resp.text.splitlines()) == 4

    resp = app.get('/backoffice/management/form-title/')
    resp = resp.click('Export a Spreadsheet')
    resp.form['format'] = 'csv'
    resp.form['include_header_line'].checked = False
    resp = resp.form.submit('submit')
    assert resp.headers['content-type'].startswith('text/')
    assert len(resp.text.splitlines()) == 3


def test_backoffice_no_json(pub):
    create_superuser(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = []
    formdef.workflow_roles = {'_receiver': 1}
    formdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/management/form-title/')
    resp = resp.click('Export a Spreadsheet')
    assert [x[0] for x in resp.form['format'].options] == ['ods', 'csv']


def test_backoffice_cards_json(pub):
    create_superuser(pub)

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'card title'
    carddef.fields = [
        fields.StringField(id='1', label='1st field'),
    ]
    carddef.workflow_roles = {'_receiver': 1}
    carddef.store()
    carddef.data_class().wipe()

    for i in range(10):
        carddata = carddef.data_class()()
        carddata.data = {'1': 'foo %s' % i}
        carddata.just_created()
        carddata.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/data/card-title/')
    resp = resp.click('Export Data')
    assert [x[0] for x in resp.form['format'].options] == ['ods', 'csv', 'json']
    resp.form['format'] = 'json'
    resp = resp.form.submit('submit')
    parsed_url = urllib.parse.urlparse(resp.location)
    assert parsed_url.path == '/backoffice/processing'
    job_id = urllib.parse.parse_qs(urllib.parse.urlparse(resp.location).query)['job'][0]
    job = AfterJob.get(job_id)
    assert job.completion_time
    json_export = json.loads(job.result_file.get_content())
    assert len(json_export['data']) == 10
