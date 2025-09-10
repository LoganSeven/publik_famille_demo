import base64
import copy
import datetime
import json
import os

import pytest
from quixote import cleanup, get_publisher

from wcs import sessions
from wcs.blocks import BlockDef
from wcs.carddef import CardDef
from wcs.fields import (
    BlockField,
    BoolField,
    DateField,
    FileField,
    ItemField,
    ItemsField,
    MapField,
    StringField,
)
from wcs.formdef import FormDef
from wcs.logged_errors import LoggedError
from wcs.qommon.http_request import HTTPRequest
from wcs.qommon.upload_storage import PicklableUpload
from wcs.wf.backoffice_fields import SetBackofficeFieldsWorkflowStatusItem
from wcs.wf.wscall import WebserviceCallStatusItem
from wcs.workflows import AttachmentEvolutionPart, Workflow, WorkflowBackofficeFieldsFormDef

from ..utilities import clean_temporary_pub, create_temporary_pub


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


def test_set_backoffice_field(http_requests, pub):
    Workflow.wipe()
    FormDef.wipe()
    wf = Workflow(name='xxx')
    wf.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(wf)
    wf.backoffice_fields_formdef.fields = [
        StringField(id='bo1', label='1st backoffice field', varname='backoffice_blah'),
    ]
    st1 = wf.add_status('Status1')
    wf.store()

    formdef = FormDef()
    formdef.name = 'baz'
    formdef.fields = [
        StringField(id='00', label='String', varname='string'),
        StringField(id='01', label='Other string', varname='other'),
    ]
    formdef.workflow_id = wf.id
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {'00': 'HELLO'}
    formdata.just_created()
    formdata.store()
    pub.substitutions.feed(formdata)

    item = SetBackofficeFieldsWorkflowStatusItem()
    item.parent = st1

    assert item.render_as_line() == 'Backoffice Data'
    assert item.get_jump_label('plop') == 'Backoffice Data'
    item.label = 'label'
    assert item.render_as_line() == 'Backoffice Data (label)'
    assert item.get_jump_label('plop') == 'Backoffice Data "label"'

    item.perform(formdata)
    formdata = formdef.data_class().get(formdata.id)
    assert formdata.data.get('bo1') is None

    item.fields = [{'field_id': 'bo1', 'value': '{{ form_var_string }} WORLD'}]
    item.perform(formdata)
    formdata = formdef.data_class().get(formdata.id)
    assert formdata.data['bo1'] == 'HELLO WORLD'

    item.fields = [{'field_id': 'bo1', 'value': '[form_var_string] GOODBYE'}]
    item.perform(formdata)
    formdata = formdef.data_class().get(formdata.id)
    assert formdata.data['bo1'] == 'HELLO GOODBYE'

    item.fields = [{'field_id': 'bo1', 'value': '{{ form.var.string }} LAZY'}]
    item.perform(formdata)
    formdata = formdef.data_class().get(formdata.id)
    assert formdata.data['bo1'] == 'HELLO LAZY'

    item.fields = [{'field_id': 'bo1', 'value': None}]
    item.perform(formdata)
    formdata = formdef.data_class().get(formdata.id)
    assert formdata.data['bo1'] is None

    item.fields = [{'field_id': 'bo1', 'value': ''}]
    item.perform(formdata)
    formdata = formdef.data_class().get(formdata.id)
    assert formdata.data['bo1'] is None

    # check a value computed as the empty string is stored as an empty string, not None
    item.fields = [{'field_id': 'bo1', 'value': '{{ does_not_exist }}'}]
    item.perform(formdata)
    formdata = formdef.data_class().get(formdata.id)
    assert formdata.data['bo1'] == ''

    LoggedError.wipe()
    item.fields = [{'field_id': 'bo1', 'value': '{% if bad django %}'}]
    item.perform(formdata)
    formdata = formdef.data_class().get(formdata.id)
    assert LoggedError.count() == 1
    logged_error = LoggedError.select()[0]
    assert logged_error.summary == 'Failed to compute template'
    assert logged_error.formdata_id == str(formdata.id)
    assert logged_error.expression == '{% if bad django %}'
    assert logged_error.expression_type == 'template'
    assert logged_error.exception_class == 'TemplateError'
    assert logged_error.exception_message.startswith('syntax error in Django template')


def test_set_backoffice_field_map(http_requests, pub):
    Workflow.wipe()
    FormDef.wipe()
    wf = Workflow(name='xxx')
    wf.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(wf)
    wf.backoffice_fields_formdef.fields = [
        MapField(id='bo1', label='1st backoffice field', varname='backoffice_blah'),
    ]
    st1 = wf.add_status('Status1')
    wf.store()

    formdef = FormDef()
    formdef.name = 'baz'
    formdef.fields = [
        MapField(id='1', label='Map1', varname='map1'),
        MapField(id='2', label='Map2', varname='map2'),
    ]
    formdef.workflow_id = wf.id
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {'1': {'lat': 42, 'lon': 10}, '2': None}
    formdata.just_created()
    formdata.store()
    pub.substitutions.feed(formdata)

    item = SetBackofficeFieldsWorkflowStatusItem()
    item.parent = st1

    item.fields = [{'field_id': 'bo1', 'value': '{{ form_var_map1|default:"" }}'}]
    item.perform(formdata)
    formdata = formdef.data_class().get(formdata.id)
    assert formdata.data.get('bo1') == {'lat': 42, 'lon': 10}

    item.fields = [{'field_id': 'bo1', 'value': '43;9'}]
    item.perform(formdata)
    formdata = formdef.data_class().get(formdata.id)
    assert formdata.data.get('bo1') == {'lat': 43, 'lon': 9}

    item.fields = [{'field_id': 'bo1', 'value': '{{ form_var_map2|default:"" }}'}]
    item.perform(formdata)
    formdata = formdef.data_class().get(formdata.id)
    assert formdata.data.get('bo1') is None

    assert LoggedError.count() == 0

    item.fields = [{'field_id': 'bo1', 'value': 'invalid value'}]
    item.perform(formdata)
    formdata = formdef.data_class().get(formdata.id)
    assert LoggedError.count() == 1
    logged_error = LoggedError.select()[0]
    assert (
        logged_error.summary == "Failed to set Map field (bo1), error: invalid coordinates 'invalid value' "
        '(missing ;) (field id: bo1)'
    )
    assert logged_error.formdata_id == str(formdata.id)
    assert logged_error.exception_class == 'SetValueError'
    assert logged_error.exception_message == "invalid coordinates 'invalid value' (missing ;) (field id: bo1)"
    LoggedError.wipe()

    item.fields = [{'field_id': 'bo1', 'value': 'XXX;YYY'}]
    item.perform(formdata)
    formdata = formdef.data_class().get(formdata.id)
    assert LoggedError.count() == 1
    logged_error = LoggedError.select()[0]
    assert (
        logged_error.summary
        == "Failed to set Map field (bo1), error: invalid coordinates 'XXX;YYY' (field id: bo1)"
    )
    assert logged_error.formdata_id == str(formdata.id)
    assert logged_error.exception_class == 'SetValueError'
    assert logged_error.exception_message == "invalid coordinates 'XXX;YYY' (field id: bo1)"
    LoggedError.wipe()


def test_set_backoffice_field_decimal(http_requests, pub):
    Workflow.wipe()
    FormDef.wipe()
    wf = Workflow(name='xxx')
    wf.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(wf)
    wf.backoffice_fields_formdef.fields = [
        StringField(id='bo1', label='1st backoffice field', varname='backoffice_blah'),
    ]
    st1 = wf.add_status('Status1')
    wf.store()

    formdef = FormDef()
    formdef.name = 'baz'
    formdef.fields = [
        StringField(id='1', label='String', varname='string'),
    ]
    formdef.workflow_id = wf.id
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {'1': '1000'}
    formdata.just_created()
    formdata.store()
    pub.substitutions.feed(formdata)

    item = SetBackofficeFieldsWorkflowStatusItem()
    item.parent = st1
    item.fields = [{'field_id': 'bo1', 'value': '{{ "100"|decimal }}'}]
    item.perform(formdata)
    assert formdef.data_class().get(formdata.id).data['bo1'] == '100'

    formdata.store()  # reset
    item.fields = [{'field_id': 'bo1', 'value': '{{ form_var_string|decimal }}'}]
    item.perform(formdata)
    assert formdef.data_class().get(formdata.id).data['bo1'] == '1000'


def test_set_backoffice_field_file(http_requests, pub):
    Workflow.wipe()
    FormDef.wipe()
    wf = Workflow(name='xxx')
    wf.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(wf)
    wf.backoffice_fields_formdef.fields = [
        FileField(id='bo1', label='1st backoffice field', varname='backoffice_file'),
        StringField(id='bo2', label='2nd backoffice field'),
    ]
    st1 = wf.add_status('Status1')
    wf.store()

    formdef = FormDef()
    formdef.name = 'baz'
    formdef.fields = [
        FileField(id='00', label='File', varname='file'),
        StringField(id='01', label='Filename', varname='filename'),
    ]
    formdef.workflow_id = wf.id
    formdef.store()

    item = SetBackofficeFieldsWorkflowStatusItem()
    item.parent = st1
    item.fields = [{'field_id': 'bo1', 'value': '{{ form_var_file_raw }}'}]

    # the file does not exist
    formdata = formdef.data_class()()
    formdata.data = {}
    formdata.just_created()
    formdata.store()
    pub.substitutions.feed(formdata)
    item.perform(formdata)
    formdata = formdef.data_class().get(formdata.id)
    assert formdata.data['bo1'] is None

    # store a PicklableUpload
    upload = PicklableUpload('test.jpeg', 'image/jpeg')
    with open(os.path.join(os.path.dirname(__file__), '..', 'image-with-gps-data.jpeg'), 'rb') as fd:
        image_with_gps_data = fd.read()
    upload.receive([image_with_gps_data])

    formdata = formdef.data_class()()
    formdata.data = {'00': upload}
    formdata.just_created()
    formdata.store()

    pub.substitutions.feed(formdata)
    item.perform(formdata)

    formdata = formdef.data_class().get(formdata.id)
    assert formdata.data['bo1'].base_filename == 'test.jpeg'
    assert formdata.data['bo1'].content_type == 'image/jpeg'
    assert formdata.data['bo1'].get_content() == image_with_gps_data
    assert formdata.data['bo1'].get_base64_content() == base64.encodebytes(image_with_gps_data)

    # check with template string
    formdata = formdef.data_class()()
    formdata.data = {'00': upload}
    formdata.just_created()
    formdata.store()

    pub.substitutions.feed(formdata)
    item.fields = [{'field_id': 'bo1', 'value': '{{form_var_file_raw}}'}]
    item.perform(formdata)

    assert formdata.data['bo1'].base_filename == 'test.jpeg'
    assert formdata.data['bo1'].content_type == 'image/jpeg'
    assert formdata.data['bo1'].get_content() == image_with_gps_data

    # check with template string, without _raw
    formdata = formdef.data_class()()
    formdata.data = {'00': upload}
    formdata.just_created()
    formdata.store()

    pub.substitutions.feed(formdata)
    item.fields = [{'field_id': 'bo1', 'value': '{{form_var_file}}'}]
    item.perform(formdata)

    assert formdata.data['bo1'].base_filename == 'test.jpeg'
    assert formdata.data['bo1'].content_type == 'image/jpeg'
    assert formdata.data['bo1'].get_content() == image_with_gps_data

    # check |strip_metadata filter
    formdata = formdef.data_class()()
    formdata.data = {'00': upload}
    formdata.just_created()
    formdata.store()

    pub.substitutions.feed(formdata)
    item.fields = [{'field_id': 'bo1', 'value': '{{form_var_file|strip_metadata}}'}]
    item.perform(formdata)

    assert formdata.data['bo1'].base_filename == 'test.jpeg'
    assert formdata.data['bo1'].content_type == 'image/jpeg'
    assert b'JFIF' in formdata.data['bo1'].get_content()
    assert b'<exif:XResolution>' not in formdata.data['bo1'].get_content()

    # check |rename_file filter
    formdata = formdef.data_class()()
    formdata.data = {'00': upload}
    formdata.just_created()
    formdata.store()

    pub.substitutions.feed(formdata)
    item.fields = [{'field_id': 'bo1', 'value': '{{form_var_file|rename_file:"foobar.jpeg"}}'}]
    item.perform(formdata)

    assert formdata.data['bo1'].base_filename == 'foobar.jpeg'
    assert formdata.data['bo1'].content_type == 'image/jpeg'
    assert formdata.data['bo1'].get_content() == image_with_gps_data

    formdata = formdef.data_class()()
    formdata.data = {'00': upload}
    formdata.just_created()
    formdata.store()

    pub.substitutions.feed(formdata)
    item.fields = [{'field_id': 'bo1', 'value': '{{form_var_file|rename_file:"foobar.$ext"}}'}]
    item.perform(formdata)

    assert formdata.data['bo1'].base_filename == 'foobar.jpeg'
    assert formdata.data['bo1'].content_type == 'image/jpeg'
    assert formdata.data['bo1'].get_content() == image_with_gps_data

    # rename_file with a lazy argument
    formdata = formdef.data_class()()
    formdata.data = {'00': upload, '01': 'lazyvalue.$ext'}
    formdata.just_created()
    formdata.store()

    pub.substitutions.feed(formdata)
    item.fields = [{'field_id': 'bo1', 'value': '{{form_var_file|rename_file:form_var_filename}}'}]
    item.perform(formdata)

    assert formdata.data['bo1'].base_filename == 'lazyvalue.jpeg'
    assert formdata.data['bo1'].content_type == 'image/jpeg'
    assert formdata.data['bo1'].get_content() == image_with_gps_data

    # rename_file with None
    formdata = formdef.data_class()()
    formdata.data = {'00': upload}
    formdata.just_created()
    formdata.store()

    assert LoggedError.count() == 0
    pub.substitutions.feed(formdata)
    item.fields = [{'field_id': 'bo1', 'value': '{{form_var_file|rename_file:None}}'}]
    item.perform(formdata)
    assert LoggedError.count() == 1
    assert LoggedError.select()[0].summary == '|rename_file called with empty new name'

    # rename_file with invalid characters
    formdata = formdef.data_class()()
    formdata.data = {'00': upload}
    formdata.just_created()
    formdata.store()

    pub.substitutions.feed(formdata)
    item.fields = [{'field_id': 'bo1', 'value': '{{form_var_file|rename_file:"testé 2025/01.jpeg"}}'}]
    item.perform(formdata)

    assert formdata.data['bo1'].base_filename == 'testé 2025_01.jpeg'
    assert formdata.data['bo1'].content_type == 'image/jpeg'
    assert formdata.data['bo1'].get_content() == image_with_gps_data

    # check |rename_file with invalid input
    formdata = formdef.data_class()()
    formdata.data = {'00': upload}
    formdata.just_created()
    formdata.store()

    pub.substitutions.feed(formdata)

    item.fields = [{'field_id': 'bo1', 'value': '{{"xxx"|rename_file:"foobar.jpeg"}}'}]
    item.perform(formdata)

    assert 'bo1' not in formdata.data

    # check with a template string, into a string field
    pub.substitutions.feed(formdata)
    item.fields = [{'field_id': 'bo2', 'value': '{{form_var_file}}'}]
    item.perform(formdata)

    assert formdata.data['bo2'] == 'test.jpeg'

    # check with template string and missing file
    formdata = formdef.data_class()()
    formdata.data = {'00': None}
    formdata.just_created()
    formdata.store()

    assert formdata.data.get('bo1') is None

    # check |rename_file with missing filename
    formdata.data['00'] = copy.copy(upload)
    formdata.data['00'].orig_filename = None
    formdata.data['00'].base_filename = None
    pub.substitutions.feed(formdata)
    item.fields = [{'field_id': 'bo1', 'value': '{{form_var_file|rename_file:"foobar.$ext"}}'}]
    item.perform(formdata)

    assert formdata.data['bo1'].base_filename == 'foobar'
    assert formdata.data['bo1'].get_content() == image_with_gps_data

    # check storing response as attachment
    pub.substitutions.feed(formdata)
    item = WebserviceCallStatusItem()
    item.url = 'http://remote.example.net/xml'
    item.varname = 'xxx'
    item.response_type = 'attachment'
    item.record_errors = True
    item.perform(formdata)
    attachment = formdata.evolution[-1].parts[-1]
    assert isinstance(attachment, AttachmentEvolutionPart)
    assert attachment.base_filename == 'xxx.xml'
    assert attachment.content_type == 'text/xml'

    formdata = formdef.data_class().get(formdata.id)
    pub.substitutions.feed(formdata)
    item = SetBackofficeFieldsWorkflowStatusItem()
    item.parent = st1
    item.fields = [{'field_id': 'bo1', 'value': '{{ form_attachments_xxx }}'}]
    item.perform(formdata)

    formdata = formdef.data_class().get(formdata.id)
    assert formdata.data['bo1'].base_filename == 'xxx.xml'

    # check resetting a value
    for value in ('', None):
        item = SetBackofficeFieldsWorkflowStatusItem()
        item.parent = st1
        item.fields = [{'field_id': 'bo1', 'value': value}]
        item.perform(formdata)

        formdata = formdef.data_class().get(formdata.id)
        assert formdata.data['bo1'] is None

    # set back to xxx.xml
    formdata = formdef.data_class().get(formdata.id)
    pub.substitutions.feed(formdata)
    item = SetBackofficeFieldsWorkflowStatusItem()
    item.parent = st1
    item.fields = [{'field_id': 'bo1', 'value': '{{ form_attachments_xxx }}'}]
    item.perform(formdata)

    formdata = formdef.data_class().get(formdata.id)
    assert formdata.data['bo1'].base_filename == 'xxx.xml'

    hello_world = formdata.data['bo1']
    # check wrong value
    for value in ('BAD',):
        formdata.data['bo1'] = hello_world
        formdata.store()

        item = SetBackofficeFieldsWorkflowStatusItem()
        item.parent = st1
        item.fields = [{'field_id': 'bo1', 'value': value}]

        LoggedError.wipe()
        item.perform(formdata)

        formdata = formdef.data_class().get(formdata.id)
        assert formdata.data['bo1'].base_filename == 'xxx.xml'
        assert LoggedError.count() == 1
        logged_error = LoggedError.select()[0]
        assert logged_error.summary.startswith('Failed to convert')
        assert logged_error.formdata_id == str(formdata.id)
        assert logged_error.exception_class == 'ValueError'


def test_set_backoffice_field_item(pub):
    Workflow.wipe()
    FormDef.wipe()
    wf = Workflow(name='xxx')
    wf.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(wf)
    st1 = wf.add_status('Status1')
    wf.backoffice_fields_formdef.fields = [
        ItemField(
            id='bo1',
            label='1st backoffice field',
            varname='backoffice_item',
            items=['a', 'b', 'c'],
        ),
    ]
    wf.store()

    formdef = FormDef()
    formdef.name = 'baz'
    formdef.fields = []
    formdef.workflow_id = wf.id
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {}
    formdata.just_created()
    formdata.store()

    item = SetBackofficeFieldsWorkflowStatusItem()
    item.parent = st1
    item.fields = [{'field_id': 'bo1', 'value': 'a'}]
    item.perform(formdata)

    formdata = formdef.data_class().get(formdata.id)
    assert formdata.data['bo1'] == 'a'
    assert formdata.data['bo1_display'] == 'a'

    datasource = {
        'type': 'jsonvalue',
        'value': json.dumps(
            [
                {'id': 'a', 'text': 'aa'},
                {'id': 'b', 'text': 'bb'},
                {'id': 'c', 'text': 'cc'},
            ]
        ),
    }

    wf.backoffice_fields_formdef.fields = [
        ItemField(
            id='bo1',
            label='1st backoffice field',
            varname='backoffice_item',
            data_source=datasource,
        ),
    ]
    wf.store()

    item = SetBackofficeFieldsWorkflowStatusItem()
    item.parent = st1
    item.fields = [{'field_id': 'bo1', 'value': 'a'}]
    item.perform(formdata)

    formdata = formdef.data_class().get(formdata.id)
    assert formdata.data['bo1'] == 'a'
    assert formdata.data['bo1_display'] == 'aa'

    datasource = {
        'type': 'jsonvalue',
        'value': json.dumps(
            [{'id': 'a', 'text': 'aa', 'more': 'aaa'}, {'id': 'b', 'text': 'bb', 'more': 'bbb'}]
        ),
    }

    wf.backoffice_fields_formdef.fields = [
        ItemField(
            id='bo1',
            label='1st backoffice field',
            varname='backoffice_item',
            data_source=datasource,
        ),
    ]
    wf.store()

    item = SetBackofficeFieldsWorkflowStatusItem()
    item.parent = st1
    item.fields = [{'field_id': 'bo1', 'value': 'a'}]
    item.perform(formdata)

    formdata = formdef.data_class().get(formdata.id)
    assert formdata.data['bo1'] == 'a'
    assert formdata.data['bo1_display'] == 'aa'
    assert formdata.data['bo1_structured'] == {'id': 'a', 'more': 'aaa', 'text': 'aa'}

    # check when assigning using the display value
    formdata = formdef.data_class()()
    formdata.data = {}
    formdata.just_created()
    formdata.store()

    item = SetBackofficeFieldsWorkflowStatusItem()
    item.parent = st1
    item.fields = [{'field_id': 'bo1', 'value': 'aa'}]
    item.perform(formdata)

    formdata = formdef.data_class().get(formdata.id)
    assert formdata.data['bo1'] == 'a'
    assert formdata.data['bo1_display'] == 'aa'
    assert formdata.data['bo1_structured'] == {'id': 'a', 'more': 'aaa', 'text': 'aa'}

    # check with unknown value
    formdata = formdef.data_class()()
    formdata.data = {}
    formdata.just_created()
    formdata.store()

    item = SetBackofficeFieldsWorkflowStatusItem()
    item.parent = st1
    item.fields = [{'field_id': 'bo1', 'value': 'foobar'}]
    item.perform(formdata)

    formdata = formdef.data_class().get(formdata.id)
    assert formdata.data['bo1'] is None
    assert formdata.data.get('bo1_display') is None
    assert formdata.data.get('bo1_structured') is None

    # check with unknown value but no datasource
    wf.backoffice_fields_formdef.fields[0].data_source = None
    wf.store()
    formdata = formdef.data_class()()
    formdata.data = {}
    formdata.just_created()
    formdata.store()

    item = SetBackofficeFieldsWorkflowStatusItem()
    item.parent = st1
    item.fields = [{'field_id': 'bo1', 'value': 'foobar'}]
    item.perform(formdata)

    formdata = formdef.data_class().get(formdata.id)
    assert formdata.data['bo1'] == 'foobar'
    assert formdata.data.get('bo1_display') == 'foobar'
    assert formdata.data.get('bo1_structured') is None


def test_set_backoffice_field_card_item(pub):
    CardDef.wipe()
    Workflow.wipe()
    FormDef.wipe()

    carddef = CardDef()
    carddef.name = 'items'
    carddef.digest_templates = {'default': '{{form_var_name}}'}
    carddef.fields = [
        StringField(id='0', label='string', varname='name'),
        StringField(id='1', label='string', varname='attr'),
    ]
    carddef.store()
    carddef.data_class().wipe()
    for i, value in enumerate(['foo', 'bar', 'baz']):
        carddata = carddef.data_class()()
        carddata.data = {
            '0': value,
            '1': 'attr%s' % i,
        }
        carddata.just_created()
        carddata.store()
    latest_carddata = carddata
    latest_carddata_id = carddata.id
    ds = {'type': 'carddef:%s' % carddef.url_name}

    wf = Workflow(name='xxx')
    wf.store()

    formdef = FormDef()
    formdef.name = 'baz'
    formdef.workflow_id = wf.id
    formdef.fields = [ItemField(id='0', label='string', data_source=ds, display_disabled_items=True)]
    formdef.store()

    wf.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(wf)
    st1 = wf.add_status('Status1')
    wf.backoffice_fields_formdef.fields = [
        ItemField(id='bo1', label='1st backoffice field', varname='backoffice_item', data_source=ds),
    ]
    wf.store()

    formdef = FormDef()
    formdef.name = 'baz'
    formdef.fields = []
    formdef.workflow_id = wf.id
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {}
    formdata.just_created()
    formdata.store()

    item = SetBackofficeFieldsWorkflowStatusItem()
    item.parent = st1
    item.fields = [{'field_id': 'bo1', 'value': str(latest_carddata_id)}]
    item.perform(formdata)

    formdata = formdef.data_class().get(formdata.id)
    assert formdata.data['bo1'] == str(latest_carddata_id)
    assert formdata.data['bo1_display'] == 'baz'
    assert formdata.data['bo1_structured']['attr'] == 'attr2'

    # reset, and get by display id value
    formdata.data = {}
    formdata.store()
    item.fields = [{'field_id': 'bo1', 'value': latest_carddata.get_display_id()}]
    item.perform(formdata)
    formdata = formdef.data_class().get(formdata.id)
    assert formdata.data['bo1'] == str(latest_carddata_id)
    assert formdata.data['bo1_display'] == 'baz'
    assert formdata.data['bo1_structured']['attr'] == 'attr2'

    # reset, and get by text value
    formdata.data = {}
    formdata.store()
    item.fields = [{'field_id': 'bo1', 'value': 'bar'}]
    item.perform(formdata)
    formdata = formdef.data_class().get(formdata.id)
    assert formdata.data['bo1'] != str(latest_carddata_id)
    assert formdata.data['bo1_display'] == 'bar'
    assert formdata.data['bo1_structured']['attr'] == 'attr1'

    # reset, with unknown value
    LoggedError.wipe()
    formdata.data = {}
    formdata.store()
    item.fields = [{'field_id': 'bo1', 'value': 'xxx'}]
    item.perform(formdata)
    formdata = formdef.data_class().get(formdata.id)
    assert formdata.data.get('bo1') is None  # invalid value is not stored
    assert formdata.data.get('bo1_display') is None
    assert formdata.data.get('bo1_structured') is None
    assert LoggedError.count() == 1
    logged_error = LoggedError.select()[0]
    assert logged_error.summary == "Failed to assign field (backoffice_item): unknown card value ('xxx')"
    assert logged_error.context['stack'][0] == {
        'field_label': '1st backoffice field',
        'field_url': f'http://example.net/backoffice/workflows/{wf.id}/backoffice-fields/fields/bo1/',
    }

    # reset, and get empty value
    formdata.data = {}
    formdata.store()
    item.fields = [{'field_id': 'bo1', 'value': ''}]
    item.perform(formdata)
    formdata = formdef.data_class().get(formdata.id)
    assert formdata.data['bo1'] is None
    assert formdata.data.get('bo1_display') is None
    assert formdata.data.get('bo1_structured') is None

    # reset, and use invalid type
    for invalid_type_expression in ('{{ cards|objects:"items" }}', '{{ cards|objects:"items"|first }}'):
        LoggedError.wipe()
        formdata.data = {}
        formdata.store()
        item.fields = [{'field_id': 'bo1', 'value': invalid_type_expression}]
        item.perform(formdata)
        formdata = formdef.data_class().get(formdata.id)
        assert formdata.data['bo1'] is None
        assert formdata.data.get('bo1_display') is None
        assert formdata.data.get('bo1_structured') is None
        logged_error = LoggedError.select()[0]
        assert logged_error.summary.startswith('Failed to assign field (backoffice_item): unknown card value')


def test_set_backoffice_field_items(pub):
    Workflow.wipe()
    FormDef.wipe()
    wf = Workflow(name='xxx')
    wf.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(wf)
    st1 = wf.add_status('Status1')
    wf.backoffice_fields_formdef.fields = [
        ItemsField(
            id='bo1',
            label='1st backoffice field',
            varname='backoffice_item',
            items=['a', 'b', 'c'],
        ),
    ]
    wf.store()

    formdef = FormDef()
    formdef.name = 'baz'
    formdef.fields = []
    formdef.workflow_id = wf.id
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {}
    formdata.just_created()
    formdata.store()

    item = SetBackofficeFieldsWorkflowStatusItem()
    item.parent = st1
    item.fields = [{'field_id': 'bo1', 'value': 'a|b'}]
    item.perform(formdata)

    formdata = formdef.data_class().get(formdata.id)
    assert formdata.data['bo1'] == ['a', 'b']
    assert formdata.data['bo1_display'] == 'a, b'

    datasource = {
        'type': 'jsonvalue',
        'value': json.dumps(
            [
                {'id': 'a', 'text': 'aa'},
                {'id': 'b', 'text': 'bb'},
                {'id': 'c', 'text': 'cc'},
            ]
        ),
    }

    wf.backoffice_fields_formdef.fields = [
        ItemsField(
            id='bo1',
            label='1st backoffice field',
            varname='backoffice_item',
            data_source=datasource,
        ),
    ]
    wf.store()

    item = SetBackofficeFieldsWorkflowStatusItem()
    item.parent = st1
    item.fields = [{'field_id': 'bo1', 'value': 'a|b'}]
    item.perform(formdata)

    formdata = formdef.data_class().get(formdata.id)
    assert formdata.data['bo1'] == ['a', 'b']
    assert formdata.data['bo1_display'] == 'aa, bb'

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

    wf.backoffice_fields_formdef.fields = [
        ItemsField(
            id='bo1',
            label='1st backoffice field',
            varname='backoffice_item',
            data_source=datasource,
        ),
    ]
    wf.store()

    item = SetBackofficeFieldsWorkflowStatusItem()
    item.parent = st1
    item.fields = [{'field_id': 'bo1', 'value': 'a|c'}]
    item.perform(formdata)

    formdata = formdef.data_class().get(formdata.id)
    assert formdata.data['bo1'] == ['a', 'c']
    assert formdata.data['bo1_display'] == 'aa, cc'
    assert len(formdata.data['bo1_structured']) == 2
    assert {'id': 'a', 'more': 'aaa', 'text': 'aa'} in formdata.data['bo1_structured']
    assert {'id': 'c', 'more': 'ccc', 'text': 'cc'} in formdata.data['bo1_structured']

    # from formdata field
    formdef.fields = [
        ItemsField(id='1', label='field', varname='items', data_source=datasource),
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {'1': ['a', 'c']}
    formdata.data['1_display'] = formdef.fields[0].store_display_value(formdata.data, '1')
    formdata.data['1_structured'] = formdef.fields[0].store_structured_value(formdata.data, '1')
    formdata.just_created()
    formdata.store()
    pub.substitutions.feed(formdata)

    # with a template
    formdata = formdef.data_class()()
    formdata.data = {'1': ['a', 'c']}
    formdata.data['1_display'] = formdef.fields[0].store_display_value(formdata.data, '1')
    formdata.data['1_structured'] = formdef.fields[0].store_structured_value(formdata.data, '1')
    formdata.just_created()
    formdata.store()
    pub.substitutions.reset()
    pub.substitutions.feed(formdata)

    item.fields = [{'field_id': 'bo1', 'value': '{{form_var_items_raw}}'}]
    item.perform(formdata)

    # using a single int
    datasource = {
        'type': 'jsonvalue',
        'value': json.dumps(
            [
                {'id': 1, 'text': 'aa', 'more': 'aaa'},
                {'id': 2, 'text': 'bb', 'more': 'bbb'},
                {'id': 3, 'text': 'cc', 'more': 'ccc'},
            ]
        ),
    }

    wf.backoffice_fields_formdef.fields = [
        ItemsField(
            id='bo1',
            label='1st backoffice field',
            varname='backoffice_item',
            data_source=datasource,
        ),
    ]
    wf.store()

    item = SetBackofficeFieldsWorkflowStatusItem()
    item.parent = st1
    item.fields = [{'field_id': 'bo1', 'value': '2'}]
    item.perform(formdata)

    formdata = formdef.data_class().get(formdata.id)
    assert formdata.data['bo1'] == ['2']
    assert formdata.data['bo1_display'] == 'bb'
    assert len(formdata.data['bo1_structured']) == 1

    # using an invalid value
    formdata.data = {}
    formdata.store()
    LoggedError.wipe()
    item = SetBackofficeFieldsWorkflowStatusItem()
    item.parent = st1
    item.fields = [{'field_id': 'bo1', 'value': '{% now %}'}]
    item.perform(formdata)
    assert LoggedError.count() == 1
    logged_error = LoggedError.select()[0]
    assert 'Failed to compute template' in logged_error.summary

    # using a string with multiple values
    item = SetBackofficeFieldsWorkflowStatusItem()
    item.parent = st1
    item.fields = [{'field_id': 'bo1', 'value': '1|3'}]
    item.perform(formdata)

    formdata = formdef.data_class().get(formdata.id)
    assert formdata.data['bo1'] == ['1', '3']
    assert formdata.data['bo1_display'] == 'aa, cc'
    assert len(formdata.data['bo1_structured']) == 2
    assert {'id': 1, 'more': 'aaa', 'text': 'aa'} in formdata.data['bo1_structured']
    assert {'id': 3, 'more': 'ccc', 'text': 'cc'} in formdata.data['bo1_structured']

    # using a string with an unrelated value
    item = SetBackofficeFieldsWorkflowStatusItem()
    item.parent = st1
    item.fields = [{'field_id': 'bo1', 'value': 'plop'}]
    item.perform(formdata)

    formdata = formdef.data_class().get(formdata.id)
    assert formdata.data['bo1'] == ['plop']
    assert not formdata.data.get('bo1_display')
    assert not formdata.data.get('bo1_structured')

    # reset, and use invalid type
    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'items'
    carddef.digest_templates = {'default': '{{form_var_name}}'}
    carddef.fields = [StringField(id='0', label='string', varname='name')]
    carddef.store()
    carddef.data_class().wipe()
    for i in range(2):
        carddata = carddef.data_class()()
        carddata.data = {'0': f'test {i}'}
        carddata.just_created()
        carddata.store()

    for invalid_type_expression in ('{{ cards|objects:"items" }}', '{{ cards|objects:"items"|first }}'):
        LoggedError.wipe()
        formdata.data = {}
        formdata.store()
        item.fields = [{'field_id': 'bo1', 'value': invalid_type_expression}]
        item.perform(formdata)
        formdata = formdef.data_class().get(formdata.id)
        assert formdata.data['bo1'] is None
        assert formdata.data.get('bo1_display') is None
        assert formdata.data.get('bo1_structured') is None
        logged_error = LoggedError.select()[0]
        assert logged_error.summary.startswith("Failed to convert <class 'wcs.variables.LazyForm")


def test_set_backoffice_field_date(pub):
    Workflow.wipe()
    FormDef.wipe()
    wf = Workflow(name='xxx')
    wf.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(wf)
    st1 = wf.add_status('Status1')
    wf.backoffice_fields_formdef.fields = [
        DateField(id='bo1', label='1st backoffice field', varname='backoffice_date'),
    ]
    wf.store()

    formdef = FormDef()
    formdef.name = 'baz'
    formdef.fields = []
    formdef.workflow_id = wf.id
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {}
    formdata.just_created()
    formdata.store()

    formdata.data['bo1'] = None
    formdata.store()
    item = SetBackofficeFieldsWorkflowStatusItem()
    item.parent = st1
    item.fields = [{'field_id': 'bo1', 'value': '{% now "j/n/Y" %}'}]
    item.perform(formdata)

    formdata = formdef.data_class().get(formdata.id)
    assert datetime.date(*formdata.data['bo1'][:3]) == datetime.date.today()

    item = SetBackofficeFieldsWorkflowStatusItem()
    item.parent = st1
    item.fields = [{'field_id': 'bo1', 'value': '23/3/2017'}]
    item.perform(formdata)

    formdata = formdef.data_class().get(formdata.id)
    assert datetime.date(*formdata.data['bo1'][:3]) == datetime.date(2017, 3, 23)

    # invalid values => do nothing
    assert LoggedError.count() == 0
    for value in ('plop', '={}', '=[]'):
        item = SetBackofficeFieldsWorkflowStatusItem()
        item.parent = st1
        item.fields = [{'field_id': 'bo1', 'value': value}]

        LoggedError.wipe()
        item.perform(formdata)
        formdata = formdef.data_class().get(formdata.id)
        assert datetime.date(*formdata.data['bo1'][:3]) == datetime.date(2017, 3, 23)
        assert LoggedError.count() == 1
        assert LoggedError.select()[0].summary.startswith('Failed to convert')

    # None : empty date
    item = SetBackofficeFieldsWorkflowStatusItem()
    item.parent = st1
    item.fields = [{'field_id': 'bo1', 'value': None}]
    item.perform(formdata)

    formdata = formdef.data_class().get(formdata.id)
    assert formdata.data['bo1'] is None


def test_set_backoffice_field_boolean(pub):
    Workflow.wipe()
    FormDef.wipe()
    wf = Workflow(name='xxx')
    wf.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(wf)
    st1 = wf.add_status('Status1')
    wf.backoffice_fields_formdef.fields = [
        BoolField(id='bo1', label='1st backoffice field', varname='backoffice_bool'),
    ]
    wf.store()

    formdef = FormDef()
    formdef.name = 'baz'
    formdef.fields = [BoolField(id='1', label='field', varname='foo')]
    formdef.workflow_id = wf.id
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {'1': True}
    formdata.just_created()
    formdata.store()
    get_publisher().substitutions.feed(formdata)

    for value in ('{{ form_var_foo_raw }}', 'True', 'Yes', 'true', 'yes'):
        item = SetBackofficeFieldsWorkflowStatusItem()
        item.parent = st1
        item.fields = [{'field_id': 'bo1', 'value': value}]
        item.perform(formdata)
        formdata = formdef.data_class().get(formdata.id)
        assert formdata.data['bo1'] is True
        formdata.data['bo1'] = None
        formdata.store()

    for value in ('False', 'plop', ''):
        item = SetBackofficeFieldsWorkflowStatusItem()
        item.parent = st1
        item.fields = [{'field_id': 'bo1', 'value': value}]
        item.perform(formdata)
        formdata = formdef.data_class().get(formdata.id)
        assert formdata.data['bo1'] is False
        formdata.data['bo1'] = None
        formdata.store()


def test_set_backoffice_field_str_time_filter(pub):
    Workflow.wipe()
    FormDef.wipe()
    wf = Workflow(name='xxx')
    wf.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(wf)
    st1 = wf.add_status('Status1')
    wf.backoffice_fields_formdef.fields = [
        StringField(id='bo1', label='1st backoffice field', varname='backoffice_str'),
    ]
    wf.store()

    formdef = FormDef()
    formdef.name = 'baz'
    formdef.fields = [StringField(id='1', label='field', varname='foo')]
    formdef.workflow_id = wf.id
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {'1': '09:00'}
    formdata.just_created()
    formdata.store()
    get_publisher().substitutions.feed(formdata)

    item = SetBackofficeFieldsWorkflowStatusItem()
    item.parent = st1
    item.fields = [{'field_id': 'bo1', 'value': '{{ form_var_foo|time:"H:i:s" }}'}]
    item.perform(formdata)
    formdata = formdef.data_class().get(formdata.id)
    assert formdata.data['bo1'] == '09:00:00'
    formdata.data['bo1'] = None
    formdata.store()

    # |time will yield the default django reprentation
    for lang, value in (('en', '9 a.m.'), ('fr', '09:00')):
        with pub.with_language(lang):
            item = SetBackofficeFieldsWorkflowStatusItem()
            item.parent = st1
            item.fields = [{'field_id': 'bo1', 'value': '{{ form_var_foo|time }}'}]
            item.perform(formdata)
            formdata = formdef.data_class().get(formdata.id)
            assert formdata.data['bo1'] == value
            formdata.data['bo1'] = None
            formdata.store()


def test_set_backoffice_field_block(pub):
    BlockDef.wipe()
    Workflow.wipe()
    FormDef.wipe()

    block = BlockDef()
    block.name = 'foobar'
    block.digest_template = 'X{{foobar_var_foo}}Y'
    block.fields = [
        StringField(id='123', required='required', label='Test', varname='foo'),
        StringField(id='234', required='required', label='Test2', varname='bar'),
    ]
    block.store()

    wf = Workflow(name='xxx')
    wf.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(wf)
    st1 = wf.add_status('Status1')
    wf.backoffice_fields_formdef.fields = [
        BlockField(id='bo1', label='1st backoffice field', block_slug='foobar'),
        StringField(id='bo2', label='2nd backoffice field'),
    ]
    wf.store()

    formdef = FormDef()
    formdef.name = 'baz'
    formdef.fields = [
        BlockField(id='1', label='test', block_slug='foobar', max_items='3', varname='foo'),
    ]
    formdef.workflow_id = wf.id
    formdef.store()

    formdata = formdef.data_class()()
    # value from test_block_digest in tests/test_form_pages.py
    formdata.data = {
        '1': {
            'data': [{'123': 'foo', '234': 'bar'}, {'123': 'foo2', '234': 'bar2'}],
            'digests': ['XfooY', 'Xfoo2Y'],
            'schema': {'123': 'string', '234': 'string'},
        },
        '1_display': 'XfooY, Xfoo2Y',
    }
    formdata.just_created()
    formdata.store()
    get_publisher().substitutions.feed(formdata)

    item = SetBackofficeFieldsWorkflowStatusItem()
    item.parent = st1
    item.fields = [{'field_id': 'bo1', 'value': '{{form_var_foo_raw}}'}]
    item.perform(formdata)
    formdata = formdef.data_class().get(formdata.id)
    assert formdata.data['bo1'] == formdata.data['1']
    assert formdata.data['bo1_display'] == formdata.data['1_display']

    # without _raw suffix
    formdata = formdef.data_class()()
    # value from test_block_digest in tests/test_form_pages.py
    formdata.data = {
        '1': {
            'data': [{'123': 'foo', '234': 'bar'}, {'123': 'foo2', '234': 'bar2'}],
            'digests': ['XfooY', 'Xfoo2Y'],
            'schema': {'123': 'string', '234': 'string'},
        },
        '1_display': 'XfooY, Xfoo2Y',
    }
    formdata.just_created()
    formdata.store()
    get_publisher().substitutions.reset()
    get_publisher().substitutions.feed(formdata)

    item = SetBackofficeFieldsWorkflowStatusItem()
    item.parent = st1
    item.fields = [
        {'field_id': 'bo1', 'value': '{{form_var_foo}}'},
        {'field_id': 'bo2', 'value': '{{form_var_foo}}'},
    ]
    item.perform(formdata)
    formdata = formdef.data_class().get(formdata.id)
    assert formdata.data['bo1'] == formdata.data['1']
    assert formdata.data['bo1_display'] == formdata.data['1_display']
    assert formdata.data['bo2'] == formdata.data['1_display']


def test_set_backoffice_field_block_template_tag(pub):
    BlockDef.wipe()
    Workflow.wipe()
    FormDef.wipe()

    block = BlockDef()
    block.name = 'foobar'
    block.digest_template = 'X{{foobar_var_foo}}Y'
    block.fields = [
        StringField(id='123', required='required', label='Test', varname='foo'),
        StringField(id='234', required='required', label='Test2', varname='bar'),
    ]
    block.store()

    wf = Workflow(name='xxx')
    wf.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(wf)
    st1 = wf.add_status('Status1')
    wf.backoffice_fields_formdef.fields = [
        BlockField(id='bo1', label='1st backoffice field', max_items='3', block_slug='foobar'),
    ]
    wf.store()

    formdef = FormDef()
    formdef.name = 'baz'
    formdef.fields = [
        StringField(id='1', label='test', varname='foo'),
    ]
    formdef.workflow_id = wf.id
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {'1': 'plop'}
    formdata.just_created()
    formdata.store()
    get_publisher().substitutions.feed(formdata)

    item = SetBackofficeFieldsWorkflowStatusItem()
    item.parent = st1
    item.fields = [{'field_id': 'bo1', 'value': '{% block_value foo=form_var_foo bar="xxx" %}'}]
    item.perform(formdata)
    formdata = formdef.data_class().get(formdata.id)
    assert formdata.data['bo1'] == {
        'data': [{'123': 'plop', '234': 'xxx'}],
        'digests': ['XplopY'],
        'schema': {'123': 'string', '234': 'string'},
    }
    assert formdata.data['bo1_display'] == 'XplopY'

    # override
    item = SetBackofficeFieldsWorkflowStatusItem()
    item.parent = st1
    item.fields = [{'field_id': 'bo1', 'value': '{% block_value foo=form_var_foo bar="yyy" %}'}]
    item.perform(formdata)
    formdata = formdef.data_class().get(formdata.id)
    assert formdata.data['bo1'] == {
        'data': [{'123': 'plop', '234': 'yyy'}],
        'digests': ['XplopY'],
        'schema': {'123': 'string', '234': 'string'},
    }
    assert formdata.data['bo1_display'] == 'XplopY'

    # append
    item = SetBackofficeFieldsWorkflowStatusItem()
    item.parent = st1
    item.fields = [{'field_id': 'bo1', 'value': '{% block_value append=True foo="zzz" bar=form_var_foo %}'}]
    item.perform(formdata)
    formdata = formdef.data_class().get(formdata.id)
    assert formdata.data['bo1'] == {
        'data': [{'123': 'plop', '234': 'yyy'}, {'123': 'zzz', '234': 'plop'}],
        'digests': ['XplopY', 'XzzzY'],
        'schema': {'123': 'string', '234': 'string'},
    }
    assert formdata.data['bo1_display'] == 'XplopY, XzzzY'

    # merge (into last row)
    item = SetBackofficeFieldsWorkflowStatusItem()
    item.parent = st1
    item.fields = [{'field_id': 'bo1', 'value': '{% block_value merge=True foo="AAA" %}'}]
    item.perform(formdata)
    formdata = formdef.data_class().get(formdata.id)
    assert formdata.data['bo1'] == {
        'data': [{'123': 'plop', '234': 'yyy'}, {'123': 'AAA', '234': 'plop'}],
        'digests': ['XplopY', 'XAAAY'],
        'schema': {'123': 'string', '234': 'string'},
    }
    assert formdata.data['bo1_display'] == 'XplopY, XAAAY'

    # merge (into given row)
    item = SetBackofficeFieldsWorkflowStatusItem()
    item.parent = st1
    item.fields = [{'field_id': 'bo1', 'value': '{% block_value merge=0 foo="BBB" %}'}]
    item.perform(formdata)
    formdata = formdef.data_class().get(formdata.id)
    assert formdata.data['bo1'] == {
        'data': [{'123': 'BBB', '234': 'yyy'}, {'123': 'AAA', '234': 'plop'}],
        'digests': ['XBBBY', 'XAAAY'],
        'schema': {'123': 'string', '234': 'string'},
    }
    assert formdata.data['bo1_display'] == 'XBBBY, XAAAY'

    # merge with indexerror (ignored)
    item = SetBackofficeFieldsWorkflowStatusItem()
    item.parent = st1
    item.fields = [{'field_id': 'bo1', 'value': '{% block_value merge=50 foo="CCC" %}'}]
    item.perform(formdata)
    formdata = formdef.data_class().get(formdata.id)
    assert formdata.data['bo1'] == {
        'data': [{'123': 'BBB', '234': 'yyy'}, {'123': 'AAA', '234': 'plop'}],
        'digests': ['XBBBY', 'XAAAY'],
        'schema': {'123': 'string', '234': 'string'},
    }
    assert formdata.data['bo1_display'] == 'XBBBY, XAAAY'

    # "item" subfield, make sure raw and display and structured values are stored
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
    block.fields.append(ItemField(id='345', label='Test3', varname='item', data_source=datasource))
    block.store()
    wf.backoffice_fields_formdef.fields[0]._block = None  # remove cache

    item = SetBackofficeFieldsWorkflowStatusItem()
    item.parent = st1
    item.fields = [{'field_id': 'bo1', 'value': '{% block_value item="b" %}'}]
    item.perform(formdata)
    formdata = formdef.data_class().get(formdata.id)
    assert formdata.data['bo1'] == {
        'data': [
            {'345': 'b', '345_display': 'bb', '345_structured': {'id': 'b', 'text': 'bb', 'more': 'bbb'}}
        ],
        'digests': ['XNoneY'],
        'schema': {'123': 'string', '234': 'string', '345': 'item'},
    }
    assert formdata.data['bo1_display'] == 'XNoneY'

    # append to invalid existing value (should not happen)
    formdata.data['bo1'] = {'invalid': 'value'}
    item = SetBackofficeFieldsWorkflowStatusItem()
    item.parent = st1
    item.fields = [{'field_id': 'bo1', 'value': '{% block_value item="b" append=True %}'}]
    item.perform(formdata)
    formdata = formdef.data_class().get(formdata.id)
    assert formdata.data['bo1'] == {
        'data': [
            {'345': 'b', '345_display': 'bb', '345_structured': {'id': 'b', 'text': 'bb', 'more': 'bbb'}}
        ],
        'digests': ['XNoneY'],
        'schema': {'123': 'string', '234': 'string', '345': 'item'},
    }
    assert formdata.data['bo1_display'] == 'XNoneY'


def test_set_backoffice_field_invalid_block_value(pub):
    BlockDef.wipe()
    Workflow.wipe()
    FormDef.wipe()

    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        StringField(id='123', required='required', label='Test', varname='foo'),
        FileField(id='234', label='File', varname='file'),
    ]
    block.store()

    wf = Workflow(name='xxx')
    wf.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(wf)
    st1 = wf.add_status('Status1')
    wf.backoffice_fields_formdef.fields = [
        BlockField(id='bo1', label='1st backoffice field', max_items='3', block_slug='foobar'),
    ]
    wf.store()

    formdef = FormDef()
    formdef.name = 'baz'
    formdef.fields = []
    formdef.workflow_id = wf.id
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()
    get_publisher().substitutions.feed(formdata)

    item = SetBackofficeFieldsWorkflowStatusItem()
    item.parent = st1
    item.fields = [{'field_id': 'bo1', 'value': 'xxx'}]

    LoggedError.wipe()
    item.perform(formdata)
    assert LoggedError.count() == 1
    logged_error = LoggedError.select()[0]
    assert (
        logged_error.summary
        == 'Failed to set Block of fields (foobar) field (bo1), error: invalid value for block (field id: bo1)'
    )

    formdata = formdef.data_class().get(formdata.id)
    assert not formdata.data.get('bo1')

    LoggedError.wipe()
    item.fields = [{'field_id': 'bo1', 'value': '{% block_value foo="xxx" file="yyy" %}'}]
    item.perform(formdata)
    assert LoggedError.count() == 1
    logged_error = LoggedError.select()[0]
    assert logged_error.summary == "invalid value when creating block: invalid data for file type ('yyy')"


def test_set_backoffice_field_immediate_use(http_requests, pub):
    Workflow.wipe()
    FormDef.wipe()
    wf = Workflow(name='xxx')
    wf.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(wf)
    wf.backoffice_fields_formdef.fields = [
        StringField(id='bo1', label='1st backoffice field', varname='backoffice_blah'),
        StringField(id='bo2', label='2nd backoffice field', varname='backoffice_barr'),
    ]
    st1 = wf.add_status('Status1')
    wf.store()

    formdef = FormDef()
    formdef.name = 'baz'
    formdef.fields = [
        StringField(id='00', label='String', varname='string'),
    ]
    formdef.workflow_id = wf.id
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {'00': 'HELLO'}
    formdata.just_created()
    formdata.store()
    item = SetBackofficeFieldsWorkflowStatusItem()
    item.parent = st1

    item.fields = [
        {'field_id': 'bo1', 'value': '{{form_var_string}}'},
    ]
    pub.substitutions.reset()
    pub.substitutions.feed(formdata)
    item.perform(formdata)
    formdata = formdef.data_class().get(formdata.id)
    assert formdata.data.get('bo1') == 'HELLO'

    item.fields = [
        {'field_id': 'bo1', 'value': 'WORLD'},
    ]
    pub.substitutions.reset()
    pub.substitutions.feed(formdata)
    item.perform(formdata)
    formdata = formdef.data_class().get(formdata.id)
    assert formdata.data.get('bo1') == 'WORLD'

    item.fields = [
        {'field_id': 'bo1', 'value': 'X{{form_var_string}}X'},
        {'field_id': 'bo2', 'value': 'Y{{form_var_backoffice_blah}}Y'},
    ]
    pub.substitutions.reset()
    pub.substitutions.feed(formdata)
    item.perform(formdata)
    formdata = formdef.data_class().get(formdata.id)
    assert formdata.data.get('bo1') == 'XHELLOX'
    assert formdata.data.get('bo2') == 'YXHELLOXY'
