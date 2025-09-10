import collections
import datetime
import io
import json
import os.path
import time
import uuid
from unittest import mock

import pytest
from django.utils import formats
from django.utils.timezone import localtime, make_aware, now
from quixote import get_publisher, get_request
from quixote.http_request import Upload

from wcs import fields, sessions
from wcs.admin.settings import UserFieldsFormDef
from wcs.blocks import BlockDef
from wcs.carddef import CardDef
from wcs.categories import Category
from wcs.conditions import Condition
from wcs.data_sources import NamedDataSource
from wcs.formdata import Evolution
from wcs.formdef import FormDef
from wcs.logged_errors import LoggedError
from wcs.qommon import force_str
from wcs.qommon.http_request import HTTPRequest
from wcs.qommon.misc import file_digest
from wcs.qommon.storage import atomic_write
from wcs.qommon.substitution import CompatibilityNamesDict
from wcs.qommon.template import Template, TemplateError
from wcs.qommon.upload_storage import PicklableUpload
from wcs.sql_criterias import FtsMatch
from wcs.tracking_code import TrackingCode
from wcs.variables import LazyFormData, NoneFieldVar
from wcs.wf.create_formdata import JournalAssignationErrorPart
from wcs.wf.register_comment import JournalEvolutionPart
from wcs.wf.wscall import JournalWsCallErrorPart
from wcs.workflow_traces import WorkflowTrace
from wcs.workflows import (
    AttachmentEvolutionPart,
    Workflow,
    WorkflowBackofficeFieldsFormDef,
    WorkflowCriticalityLevel,
    WorkflowStatusItem,
    WorkflowVariablesFieldsFormDef,
)

from .utilities import clean_temporary_pub, create_temporary_pub


@pytest.fixture
def pub():
    pub = create_temporary_pub()

    req = HTTPRequest(None, {'SCRIPT_NAME': '/', 'SERVER_NAME': 'example.net'})
    pub.set_app_dir(req)
    pub._set_request(req)
    pub.cfg['identification'] = {'methods': ['password']}
    pub.cfg['language'] = {'language': 'en'}
    pub.write_cfg()

    return pub


@pytest.fixture
def formdef(pub):
    FormDef.wipe()

    formdef = FormDef()
    formdef.name = 'foobar'
    formdef.url_name = 'foobar'
    formdef.fields = []
    formdef.store()

    return formdef


def teardown_module(module):
    clean_temporary_pub()


@pytest.fixture
def local_user():
    get_publisher().user_class.wipe()
    user = get_publisher().user_class()
    user.name = 'Jean Darmette'
    user.email = 'jean.darmette@triffouilis.fr'
    user.name_identifiers = ['0123456789']
    user.store()
    return user


def test_basic(pub, formdef):
    Category.wipe()
    cat = Category(name='test category')
    cat.store()
    formdef.category_id = cat.id
    formdef.store()
    formdata = formdef.data_class()()
    substvars = formdata.get_substitution_variables()
    assert substvars.get('form_status') == 'Unknown'
    assert substvars.get('form_name') == 'foobar'
    assert substvars.get('form_slug') == 'foobar'
    assert substvars.get('category_name') == 'test category'


def test_saved(pub, formdef):
    formdef.data_class().wipe()
    formdata = formdef.data_class()()
    formdata.store()
    substvars = formdata.get_substitution_variables()
    assert substvars.get('form_number') == '%s-1' % formdef.id
    assert substvars.get('form_number_raw') == '1'
    assert substvars.get('form_url').endswith('/foobar/1/')
    assert substvars.get('form_url_backoffice').endswith('/backoffice/management/foobar/1/')
    assert substvars.get('form_status_url').endswith('/foobar/1/status')


def test_unsaved(pub, formdef):
    formdata = formdef.data_class()()
    substvars = formdata.get_substitution_variables()
    assert substvars.get('form_url').endswith('/foobar/')
    assert substvars.get('form_url_backoffice').endswith('/backoffice/management/foobar/')
    assert substvars.get('form_status_url') == ''


def test_auto_display_id(pub, formdef):
    formdef.data_class().wipe()
    formdata = formdef.data_class()()
    formdata.store()
    substvars = formdata.get_substitution_variables()
    assert substvars.get('form_number') == '%s-%s' % (formdef.id, formdata.id)
    assert substvars.get('form_number_raw') == str(formdata.id)


def test_manual_display_id(pub, formdef):
    formdef.data_class().wipe()
    formdata = formdef.data_class()()
    formdata.id_display = 'bar'
    formdata.store()
    substvars = formdata.get_substitution_variables()
    assert substvars.get('form_number') == 'bar'
    assert substvars.get('form_number_raw') == str(formdata.id)


def test_submission_context(pub, formdef, local_user):
    formdef.data_class().wipe()
    formdata = formdef.data_class()()
    formdata.backoffice_submission = True
    formdata.submission_channel = 'mail'
    formdata.submission_agent_id = str(local_user.id)
    formdata.submission_context = {
        'mail_url': 'http://www.example.com/test.pdf',
    }
    substvars = formdata.get_substitution_variables()
    assert substvars.get('form_submission_backoffice') is True
    assert substvars.get('form_submission_channel') == 'mail'
    assert substvars.get('form_submission_channel_label') == 'Mail'
    assert substvars.get('form_submission_context_mail_url') == 'http://www.example.com/test.pdf'
    assert substvars.get('form_submission_agent_email') == local_user.email

    formdata = formdef.data_class()()
    substvars = formdata.get_substitution_variables()
    assert substvars.get('form_submission_backoffice') is False
    assert substvars.get('form_submission_channel') is None
    assert substvars.get('form_submission_channel_label') == 'Web'
    assert substvars.get('form_submission_agent_email') is None


def test_just_created(pub, formdef):
    formdef.data_class().wipe()
    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()
    substvars = formdata.get_substitution_variables()
    assert substvars.get('form_status') == 'Just Submitted'
    assert substvars.get('form_status_is_endpoint') is False
    assert substvars.get('form_receipt_date')
    assert substvars.get('form_receipt_time')
    assert substvars.get('form_receipt_datetime')
    assert substvars.get('form_last_update_datetime')
    assert substvars.get('form_evolution')
    assert substvars.get('form_uuid')


def test_field(pub, formdef):
    formdef.data_class().wipe()
    formdef.fields = [fields.StringField(id='0', label='string')]
    formdef.store()
    formdata = formdef.data_class()()
    substvars = formdata.get_substitution_variables()
    assert not substvars.get('form_f0')

    formdata.data = {'0': 'test'}
    substvars = formdata.get_substitution_variables()
    assert substvars.get('form_f0') == 'test'
    assert substvars.get('form_field_string') == 'test'


def test_field_varname(pub, formdef):
    formdef.data_class().wipe()
    formdef.fields = [fields.StringField(id='0', label='string', varname='foo')]
    formdef.store()
    formdata = formdef.data_class()()
    formdata.data = {'0': 'test'}
    substvars = formdata.get_substitution_variables()
    assert substvars.get('form_f0') == 'test'
    assert substvars.get('form_var_foo') == 'test'


def test_file_field(pub, formdef):
    formdef.data_class().wipe()
    formdef.fields = [fields.FileField(id='0', label='file', varname='foo')]
    formdef.store()
    formdata = formdef.data_class()()
    upload = PicklableUpload('test.txt', 'text/plain', 'ascii')
    upload.receive([b'first line', b'second line'])
    formdata.data = {'0': upload}
    formdata.id = 1
    substvars = formdata.get_substitution_variables()
    assert substvars.get('form_var_foo') == 'test.txt'
    assert substvars.get('form_var_foo_url').endswith('/foobar/1/download?f=0')
    assert isinstance(substvars.get('form_var_foo_raw'), Upload)

    pub.substitutions.feed(formdata)
    context = pub.substitutions.get_context_variables(mode='lazy')
    tmpl = Template('{{form_var_foo|length}}')
    assert tmpl.render(context) == '8'

    formdata.data = {'0': None}
    substvars = formdata.get_substitution_variables()
    assert isinstance(substvars['form_var_foo'], NoneFieldVar)
    assert substvars['form_var_foo_raw'] is None
    assert substvars['form_var_foo_url'] is None

    formdata.data = {}
    substvars = formdata.get_substitution_variables()
    assert isinstance(substvars['form_var_foo'], NoneFieldVar)
    assert substvars['form_var_foo_raw'] is None
    assert substvars['form_var_foo_url'] is None


def test_get_submitter_email(pub, formdef):
    BlockDef.wipe()
    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.StringField(id='0', label='email', varname='foo', prefill={'type': 'user', 'value': 'email'})
    ]
    block.store()
    formdef.data_class().wipe()
    formdef.fields = [
        fields.StringField(id='0', label='email', varname='foo', prefill={'type': 'user', 'value': 'email'}),
        fields.BlockField(id='1', label='test', block_slug='foobar', max_items='3', varname='block'),
        fields.StringField(id='2', label='other'),
    ]
    formdef.store()

    formdata = formdef.data_class()()
    assert formdef.get_submitter_email(formdata) is None
    formdata.data = {'0': 'foo@localhost'}
    assert formdef.get_submitter_email(formdata) == 'foo@localhost'

    formdata.data = {
        '1': {
            'data': [{'0': 'baz@localhost'}, {'0': ''}],
            'schema': {'0': 'string'},
        }
    }
    assert formdef.get_submitter_email(formdata) == 'baz@localhost'

    formdata.data = {
        '1': {
            'data': [{'0': 'baz@localhost'}, {'0': 'foo@localhost'}],
            'schema': {'0': 'string'},
        }
    }
    assert formdef.get_submitter_email(formdata) == 'baz@localhost'

    formdata.data = {
        '1': {
            'data': [{'0': ''}, {'0': 'foo@localhost'}],
            'schema': {'0': 'string'},
        }
    }
    assert formdef.get_submitter_email(formdata) == 'foo@localhost'

    formdata.data = {'1': {}}
    assert formdef.get_submitter_email(formdata) is None

    formdata.data = {'2': 'other'}
    assert formdef.get_submitter_email(formdata) is None

    formdata.data = {
        '0': 'foo@localhost',
        '1': {
            'data': [{'0': 'baz@localhost'}, {'0': ''}],
            'schema': {'0': 'string'},
        },
    }
    assert formdef.get_submitter_email(formdata) == 'foo@localhost'

    user = pub.user_class()
    user.email = 'bar@localhost'
    user.store()

    formdata.user_id = user.id
    assert formdef.get_submitter_email(formdata) == 'foo@localhost'

    formdata.data = {}
    assert formdef.get_submitter_email(formdata) == 'bar@localhost'


def test_get_submitter_phone(pub, formdef):
    pub.cfg['identification'] = {'methods': ['password']}
    pub.cfg['language'] = {'language': 'en'}
    pub.cfg['users'] = {'field_phone': '_phone', 'field_mobile': '_mobile'}
    pub.write_cfg()

    user_formdef = UserFieldsFormDef(pub)
    user_formdef.fields = [
        fields.StringField(id='_phone', label='phone', varname='phone', validation={'type': 'phone'}),
        fields.StringField(id='_mobile', label='mobile', varname='mobile', validation={'type': 'phone'}),
    ]
    user_formdef.store()

    BlockDef.wipe()
    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.StringField(id='0', label='phone', varname='foo', prefill={'type': 'user', 'value': '_phone'})
    ]
    block.store()
    formdef.data_class().wipe()
    formdef.fields = [
        fields.StringField(id='0', label='phone', varname='foo', prefill={'type': 'user', 'value': '_phone'}),
        fields.BlockField(id='1', label='test', block_slug='foobar', max_items='3', varname='block'),
        fields.StringField(id='2', label='other'),
        fields.StringField(
            id='3', label='mobile', varname='bar', prefill={'type': 'user', 'value': '_mobile'}
        ),
    ]
    formdef.store()

    formdata = formdef.data_class()()
    assert formdef.get_submitter_phone(formdata) is None
    formdata.data = {'0': '0602030405'}
    assert formdef.get_submitter_phone(formdata) == '0602030405'

    formdata.data = {
        '1': {
            'data': [{'0': '0602030405'}, {'0': ''}],
            'schema': {'0': 'string'},
        }
    }
    assert formdef.get_submitter_phone(formdata) == '0602030405'

    formdata.data = {
        '1': {
            'data': [{'0': '0602030405'}, {'0': '0602030406'}],
            'schema': {'0': 'string'},
        }
    }
    assert formdef.get_submitter_phone(formdata) == '0602030405'

    formdata.data = {
        '1': {
            'data': [{'0': ''}, {'0': '0602030405'}],
            'schema': {'0': 'string'},
        }
    }
    assert formdef.get_submitter_phone(formdata) == '0602030405'

    formdata.data = {'1': {}}
    assert formdef.get_submitter_phone(formdata) is None

    formdata.data = {'2': 'other'}
    assert formdef.get_submitter_phone(formdata) is None

    formdata.data = {
        '0': '0602030405',
        '1': {
            'data': [{'0': '0602030406'}, {'0': ''}],
            'schema': {'0': 'string'},
        },
    }
    assert formdef.get_submitter_phone(formdata) == '0602030405'

    formdata.data = {
        '0': '0602030405',
        '1': {
            'data': [{'0': '0602030406'}, {'0': ''}],
            'schema': {'0': 'string'},
        },
        '3': '0602030408',
    }
    assert formdef.get_submitter_phone(formdata) == '0602030408'

    user = pub.user_class()
    user.form_data = {'_phone': '0602030407'}
    user.store()

    formdata.user_id = user.id
    assert formdef.get_submitter_phone(formdata) == '0602030408'  # prefer mobile from formdata

    del formdata.data['3']
    formdata.store()
    user = pub.user_class()
    user.form_data = {'_mobile': '0602030409'}
    user.store()
    assert formdef.get_submitter_phone(formdata) == '0602030405'  # always prefer formdata

    formdata.data = {}
    assert formdef.get_submitter_phone(formdata) == '06 02 03 04 07'


def test_get_last_update_time(pub, formdef):
    formdef.data_class().wipe()

    formdata = formdef.data_class()()
    assert formdata.last_update_time is None

    formdata.just_created()
    assert formdata.last_update_time == formdata.evolution[-1].time

    time.sleep(1)
    evo = Evolution(formdata=formdata)
    evo.time = localtime()
    evo.status = formdata.status
    evo.comment = 'hello world'
    formdata.evolution.append(evo)
    assert formdata.last_update_time != formdata.receipt_time
    assert formdata.last_update_time == formdata.evolution[-1].time

    # check with missing 'evolution' values
    formdata.evolution = None
    assert formdata.last_update_time == formdata.receipt_time


def test_password_field(pub, formdef):
    formdef.data_class().wipe()
    formdef.fields = [fields.PasswordField(id='0', label='pwd')]
    formdef.store()
    formdata = formdef.data_class()()
    formdata.data = {'0': {'cleartext': 'foo'}}
    formdata.store()

    formdata2 = formdata.get(formdata.id)
    assert formdata2.data == {'0': {'cleartext': 'foo'}}


def test_date_field(pub, formdef):
    formdef.data_class().wipe()
    formdef.fields = [fields.DateField(id='0', label='date')]
    formdef.store()
    formdata = formdef.data_class()()
    value = time.strptime('2015-05-12', '%Y-%m-%d')
    formdata.data = {'0': value}
    formdata.store()

    formdata2 = formdata.get(formdata.id)
    assert formdata2.data == {'0': value}

    assert formdata2.get_substitution_variables()['form_field_date'] == '2015-05-12'
    with pub.with_language('fr'):
        assert formdata2.get_substitution_variables()['form_field_date'] == '12/05/2015'


def test_clean_drafts(pub):
    formdef = FormDef()
    formdef.name = 'foo'
    formdef.fields = []
    formdef.store()
    formdef.data_class().wipe()

    d = formdef.data_class()()
    d.status = 'draft'
    d.receipt_time = localtime()
    d.store()
    d_id1 = d.id

    d = formdef.data_class()()
    d.status = 'draft'
    d.receipt_time = make_aware(datetime.datetime(1970, 1, 1))
    d.store()

    assert formdef.data_class().count() == 2
    from wcs.formdef_jobs import clean_drafts

    clean_drafts(pub)
    assert formdef.data_class().count() == 1
    assert formdef.data_class().select()[0].id == d_id1

    d = formdef.data_class()()
    d.status = 'draft'
    d.receipt_time = localtime() - datetime.timedelta(days=5)
    d.store()
    clean_drafts(pub)
    assert formdef.data_class().count() == 2
    formdef.drafts_lifespan = '3'
    formdef.store()
    clean_drafts(pub)
    assert formdef.data_class().count() == 1


def test_criticality_levels(pub):
    workflow = Workflow(name='criticality')
    workflow.criticality_levels = [
        WorkflowCriticalityLevel(name='green'),
        WorkflowCriticalityLevel(name='yellow'),
        WorkflowCriticalityLevel(name='red'),
    ]
    workflow.store()

    formdef = FormDef()
    formdef.name = 'foo'
    formdef.fields = []
    formdef.workflow_id = workflow.id
    formdef.store()
    formdef.data_class().wipe()

    d = formdef.data_class()()
    assert d.get_criticality_level_object().name == 'green'
    d.increase_criticality_level()
    assert d.get_criticality_level_object().name == 'yellow'
    d.increase_criticality_level()
    assert d.get_criticality_level_object().name == 'red'
    d.increase_criticality_level()
    assert d.get_criticality_level_object().name == 'red'
    d.decrease_criticality_level()
    assert d.get_criticality_level_object().name == 'yellow'
    d.decrease_criticality_level()
    assert d.get_criticality_level_object().name == 'green'
    d.decrease_criticality_level()
    assert d.get_criticality_level_object().name == 'green'
    d.set_criticality_level(1)
    assert d.get_criticality_level_object().name == 'yellow'
    d.set_criticality_level(2)
    assert d.get_criticality_level_object().name == 'red'
    d.set_criticality_level(4)
    assert d.get_criticality_level_object().name == 'red'

    workflow.criticality_levels = [WorkflowCriticalityLevel(name='green')]
    workflow.store()
    formdef = FormDef.get(id=formdef.id)  # reload formdef
    d = formdef.data_class()()
    assert d.get_criticality_level_object().name == 'green'
    d.increase_criticality_level()
    assert d.get_criticality_level_object().name == 'green'

    workflow.criticality_levels = [
        WorkflowCriticalityLevel(name='green'),
        WorkflowCriticalityLevel(name='yellow'),
    ]
    workflow.store()
    formdef = FormDef.get(id=formdef.id)  # reload formdef
    d = formdef.data_class()()
    d.criticality_level = 104
    # set too high, this simulates a workflow being changed to have less
    # levels than before.
    assert d.get_criticality_level_object().name == 'yellow'
    d.increase_criticality_level()
    assert d.get_criticality_level_object().name == 'yellow'
    d.decrease_criticality_level()
    assert d.get_criticality_level_object().name == 'green'

    d.criticality_level = 104
    d.decrease_criticality_level()
    assert d.get_criticality_level_object().name == 'green'

    assert d.get_static_substitution_variables().get('form_criticality_label') == 'green'
    assert d.get_substitution_variables().get('form_criticality_label') == 'green'


def test_field_item_substvars(pub):
    ds = {
        'type': 'jsonvalue',
        'value': '[{"id": "1", "text": "un"}, {"id": "2", "text": "deux"}]',
    }

    formdef = FormDef()
    formdef.name = 'foobar'
    formdef.fields = [fields.ItemField(id='0', label='string', data_source=ds, varname='xxx')]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {'0': '1', '0_display': 'un'}

    variables = formdata.get_substitution_variables()
    assert variables.get('form_var_xxx') == 'un'
    assert variables.get('form_var_xxx_raw') == '1'


def test_get_json_export_dict_evolution(pub, local_user):
    Workflow.wipe()
    workflow = Workflow(name='test')
    st_new = workflow.add_status('New')
    st_finished = workflow.add_status('Finished')
    workflow.store()

    formdef = FormDef()
    formdef.workflow_id = workflow.id
    formdef.name = 'foo'
    formdef.fields = []
    formdef.store()
    formdef.data_class().wipe()

    d = formdef.data_class()()
    d.status = 'wf-%s' % st_new.id
    d.user_id = local_user.id
    d.receipt_time = localtime()
    evo = Evolution(formdata=d)
    evo.time = localtime()
    evo.status = 'wf-%s' % st_new.id
    evo.who = '_submitter'
    d.evolution = [evo]
    d.store()
    evo.add_part(JournalEvolutionPart(d, 'ok', None, None))
    evo.add_part(
        JournalWsCallErrorPart('summary', varname='x', label='label', url='http://test', data='data')
    )
    evo.add_part(JournalAssignationErrorPart('summary', 'label'))
    d.store()
    evo = Evolution(formdata=d)
    evo.time = localtime()
    evo.status = 'wf-%s' % st_finished.id
    evo.who = '_submitter'
    d.evolution.append(evo)
    d.store()

    d.refresh_from_storage()
    export = d.get_json_export_dict()
    assert 'evolution' in export
    assert len(export['evolution']) == 2
    assert export['evolution'][0]['status'] == st_new.id
    assert 'time' in export['evolution'][0]
    assert export['evolution'][0]['who']['id'] == local_user.id
    assert export['evolution'][0]['who']['email'] == local_user.email
    assert export['evolution'][0]['who']['NameID'] == local_user.name_identifiers
    assert 'parts' in export['evolution'][0]
    assert len(export['evolution'][0]['parts']) == 3
    assert export['evolution'][0]['parts'][0]['type'] == 'workflow-comment'
    assert export['evolution'][0]['parts'][0]['content'] == '<p>ok</p>'
    assert export['evolution'][0]['parts'][1]['type'] == 'wscall-error'
    assert export['evolution'][0]['parts'][1]['summary'] == 'summary'
    assert export['evolution'][0]['parts'][1]['label'] == 'label'
    assert export['evolution'][0]['parts'][1]['data'] == 'data'
    assert export['evolution'][0]['parts'][2]['type'] == 'assignation-error'
    assert export['evolution'][0]['parts'][2]['summary'] == 'summary'
    assert export['evolution'][0]['parts'][2]['label'] == 'label'
    assert export['evolution'][1]['status'] == st_finished.id
    assert 'time' in export['evolution'][1]
    assert export['evolution'][1]['who']['id'] == local_user.id
    assert export['evolution'][1]['who']['email'] == local_user.email
    assert export['evolution'][1]['who']['NameID'] == local_user.name_identifiers
    assert 'parts' not in export['evolution'][1]

    export = d.get_json_export_dict(anonymise=True)
    assert 'evolution' in export
    assert len(export['evolution']) == 2
    assert export['evolution'][0]['status'] == st_new.id
    assert 'time' in export['evolution'][0]
    assert 'who' not in export['evolution'][0]
    assert 'parts' in export['evolution'][0]
    assert len(export['evolution'][0]['parts']) == 3
    assert len(export['evolution'][0]['parts'][0]) == 2
    assert export['evolution'][0]['parts'][0]['type'] == 'workflow-comment'
    assert len(export['evolution'][0]['parts'][1]) == 1
    assert export['evolution'][0]['parts'][1]['type'] == 'wscall-error'
    assert len(export['evolution'][0]['parts'][2]) == 1
    assert export['evolution'][0]['parts'][2]['type'] == 'assignation-error'
    assert export['evolution'][1]['status'] == st_finished.id
    assert 'time' in export['evolution'][1]
    assert 'who' not in export['evolution'][0]
    assert 'parts' not in export['evolution'][1]


def test_field_bool_substvars(pub):
    formdef = FormDef()
    formdef.name = 'foobar'
    formdef.fields = [fields.BoolField(id='0', label='checkbox', varname='xxx')]
    formdef.store()

    formdata = formdef.data_class()()

    formdata.data = {'0': False}
    variables = formdata.get_substitution_variables()
    assert variables.get('form_var_xxx') == 'False'
    assert variables.get('form_var_xxx_raw') is False

    formdata.data = {'0': True}
    variables = formdata.get_substitution_variables()
    assert variables.get('form_var_xxx') == 'True'
    assert variables.get('form_var_xxx_raw') is True


def test_backoffice_field_varname(pub, formdef):
    wf = Workflow(name='bo fields')
    wf.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(wf)
    wf.backoffice_fields_formdef.fields = [
        fields.StringField(id='bo1', label='1st backoffice field', varname='backoffice_blah'),
    ]
    wf.add_status('Status1')
    wf.store()

    formdef.workflow_id = wf.id
    formdef.data_class().wipe()
    formdef.fields = [fields.StringField(id='0', label='string', varname='foo')]
    formdef.store()
    formdata = formdef.data_class()()
    formdata.data = {'bo1': 'test'}
    substvars = formdata.get_substitution_variables()
    assert substvars.get('form_var_backoffice_blah') == 'test'


def test_workflow_data_file_url(pub, formdef):
    upload = PicklableUpload('test.txt', 'text/plain', 'ascii')
    upload.receive([b'first line', b'second line'])

    formdata = formdef.data_class()()
    formdata.store()
    # create workflow_data as ordered dict to be sure _url comes last, to
    # trigger #17233.
    formdata.workflow_data = collections.OrderedDict(
        foo_var_file='test.txt',
        foo_var_file_raw=upload,
        foo_var_file_url=None,
    )
    substvars = formdata.get_substitution_variables()
    assert substvars['foo_var_file_url']


def test_workflow_data_invalid_keys(pub, formdef):
    formdata = formdef.data_class()()
    formdata.store()
    formdata.workflow_data = {
        'valid_key': {'invalid key': 'foo', 'valid_key': 'bar'},
        'invalid key': 'baz',
    }
    substvars = formdata.get_substitution_variables()
    assert 'form_workflow_data_valid_key' in substvars
    assert 'form_workflow_data_invalid key' not in substvars
    assert 'form_workflow_data_valid_key_valid_key' in substvars
    assert 'form_workflow_data_valid_key_invalid key' not in substvars
    assert substvars['form_workflow_data_valid_key_valid_key'] == 'bar'
    with pytest.raises(KeyError):
        # noqa pylint: disable=pointless-statement
        substvars['form_workflow_data_invalid key']


def test_evolution_get_status(pub):
    Workflow.wipe()
    workflow = Workflow(name='test')
    st_new = workflow.add_status('New')
    st_finished = workflow.add_status('Finished')
    workflow.store()

    formdef = FormDef()
    formdef.workflow_id = workflow.id
    formdef.name = 'foo'
    formdef.fields = []
    formdef.store()
    formdef.data_class().wipe()

    d = formdef.data_class()()
    d.evolution = []

    evo = Evolution(formdata=d)
    evo.time = localtime()
    evo.status = 'wf-%s' % st_new.id
    d.evolution.append(evo)

    evo = Evolution(formdata=d)
    evo.time = localtime()
    d.evolution.append(evo)

    evo = Evolution(formdata=d)
    evo.time = localtime()
    d.evolution.append(evo)

    evo = Evolution(formdata=d)
    evo.time = localtime()
    evo.status = 'wf-%s' % st_finished.id
    d.evolution.append(evo)

    evo = Evolution(formdata=d)
    evo.time = localtime()
    d.evolution.append(evo)

    d.store()
    d = formdef.data_class().get(d.id)

    assert [x.get_status().id for x in d.evolution] == ['1', '1', '1', '2', '2']


@pytest.fixture
def variable_test_data(pub):
    pub.user_class.wipe()
    user = pub.user_class()
    user.email = 'bar@localhost'
    user.name_identifiers = ['....']
    user.store()

    role = pub.role_class(name='foobar')
    role.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'foobarlazy'
    formdef.fields = [
        fields.StringField(id='0', label='string', varname='foo_foo'),
        fields.BoolField(id='1', label='checkbox', varname='boolfield'),
        fields.BoolField(id='2', label='checkbox', varname='boolfield2'),
        fields.BoolField(id='2b', label='checkbox', varname='boolfield3'),
        fields.DateField(id='3', label='date', varname='datefield'),
        fields.ItemsField(id='4', label='items', items=['aa', 'ab', 'ac'], varname='itemsfield'),
        fields.FileField(id='5', label='file', varname='filefield'),
        fields.StringField(id='6', label='string2', varname='foo_foo_baz_baz'),
        fields.MapField(id='7', label='map', varname='map'),
        fields.DateField(id='8', label='date2', varname='datefield2'),
        fields.StringField(id='9', label='string2', varname='datestring'),
        fields.StringField(id='10', label='number1', varname='term1'),
        fields.StringField(id='11', label='number2', varname='term2'),
        fields.StringField(id='12', label='float1', varname='value'),
        fields.PasswordField(id='13', label='pwd', varname='pwd'),
        fields.EmailField(id='14', label='email', varname='email'),
        fields.ItemField(id='15', label='item', items=['aa', 'bb', 'cc'], varname='itemfield'),
    ]
    formdef.workflow_roles = {'_receiver': role.id}
    formdef.geolocations = {'base': 'Base'}
    formdef.store()
    formdef.data_class().wipe()
    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.user_id = user.id
    formdata.data = {
        '0': 'bar',
        '1': False,
        '2': True,
        '3': time.strptime('2018-07-31', '%Y-%m-%d'),
        '4': ['aa', 'ac'],
        '4_display': 'aa, ac',
        '5': PicklableUpload('test.txt', 'text/plain'),
        '6': 'other',
        '7': {'lat': 2, 'lon': 4},  # map
        '8': time.strptime('2018-08-31', '%Y-%m-%d'),
        '9': '2018-07-31',
        '10': '3',
        '11': '4',
        '12': '3.14',
        '13': {
            'cleartext': 'a',
            'md5': '0cc175b9c0f1b6a831c399e269772661',
            'sha1': '86f7e437faa5a7fce15d1ddcb9eaeaea377667b8',
        },
        '14': 'test@localhost',
    }
    formdata.data['5'].receive([b'hello world'])
    formdata.geolocations = {'base': {'lat': 1, 'lon': 2}}
    formdata.store()
    pub.substitutions.feed(pub)
    pub.substitutions.feed(formdef)
    pub.substitutions.feed(formdata)
    return LazyFormData(formdata)


def test_lazy_formdata(pub, variable_test_data):
    formdef = FormDef.select()[0]
    formdata = formdef.data_class().select()[0]
    lazy_formdata = LazyFormData(formdata)
    assert lazy_formdata.receipt_date == formdata.receipt_time.strftime('%Y-%m-%d')
    assert lazy_formdata.receipt_time == formats.time_format(formdata.receipt_time)
    assert lazy_formdata.last_update_datetime.timetuple()[:6] == formdata.last_update_time.timetuple()[:6]
    assert lazy_formdata.internal_id == formdata.id
    assert lazy_formdata.name == 'foobarlazy'
    assert lazy_formdata.display_name == 'foobarlazy #%s' % formdata.get_display_id()
    assert lazy_formdata.page_no == 0
    assert lazy_formdata.url.endswith('/foobarlazy/%s/' % formdata.id)
    assert lazy_formdata.url_backoffice.endswith('/backoffice/management/foobarlazy/%s/' % formdata.id)
    assert lazy_formdata.backoffice_url == lazy_formdata.url_backoffice
    assert lazy_formdata.backoffice_submission_url == formdef.get_backoffice_submission_url()
    assert lazy_formdata.frontoffice_submission_url == formdef.get_url()
    assert lazy_formdata.api_url == formdata.get_api_url()
    assert lazy_formdata.short_url == formdata.get_short_url()
    assert lazy_formdata.attachments
    assert lazy_formdata.geoloc['base'] == {'lat': 1, 'lon': 2}
    assert lazy_formdata.geoloc['base_lon'] == 2
    assert lazy_formdata.uuid == formdata.uuid
    static_vars = formdata.get_static_substitution_variables()
    for attribute in (
        'name',
        'receipt_date',
        # 'receipt_time',  # lazy value is localized
        'previous_status',
        'uri',
        'status_changed',
        'comment',
        'evolution',
        'details',
        'criticality_level',
        'digest',
    ):
        assert getattr(lazy_formdata, attribute) == static_vars['form_' + attribute]

    assert lazy_formdata.user.email == 'bar@localhost'
    assert lazy_formdata.var.foo_foo == 'bar'
    assert lazy_formdata.var.boolfield == 'False'
    assert bool(lazy_formdata.var.boolfield) is False
    assert lazy_formdata.var.boolfield.raw is False
    assert lazy_formdata.var.boolfield2 == 'True'
    assert lazy_formdata.var.boolfield2.raw is True
    assert bool(lazy_formdata.var.boolfield2) is True
    assert lazy_formdata.var.boolfield3.raw is False
    assert lazy_formdata.var.datefield.raw == time.strptime('2018-07-31', '%Y-%m-%d')
    assert lazy_formdata.var.datefield.tm_year == 2018
    assert lazy_formdata.var.datefield.tm_mon == 7
    assert lazy_formdata.var.datefield.tm_mday == 31
    for attr in ('tm_year', 'tm_mon', 'tm_mday', 'tm_hour', 'tm_min', 'tm_sec', 'tm_wday', 'tm_yday'):
        getattr(lazy_formdata.var.datefield, attr)

    # flexible date comparison
    assert lazy_formdata.var.datefield == lazy_formdata.var.datefield
    assert not lazy_formdata.var.datefield == lazy_formdata.var.datefield2
    assert not lazy_formdata.var.datefield2 == lazy_formdata.var.datefield
    assert lazy_formdata.var.datefield != lazy_formdata.var.datefield2
    assert lazy_formdata.var.datefield2 != lazy_formdata.var.datefield
    assert not lazy_formdata.var.datefield != lazy_formdata.var.datefield
    assert lazy_formdata.var.datefield < lazy_formdata.var.datefield2
    assert lazy_formdata.var.datefield <= lazy_formdata.var.datefield2
    assert not lazy_formdata.var.datefield > lazy_formdata.var.datefield2
    assert not lazy_formdata.var.datefield >= lazy_formdata.var.datefield2
    assert lazy_formdata.var.datefield2 > lazy_formdata.var.datefield
    assert lazy_formdata.var.datefield2 >= lazy_formdata.var.datefield
    assert not lazy_formdata.var.datefield2 < lazy_formdata.var.datefield
    assert not lazy_formdata.var.datefield2 <= lazy_formdata.var.datefield

    assert lazy_formdata.var.datefield == lazy_formdata.var.datestring
    assert lazy_formdata.var.datestring == lazy_formdata.var.datefield
    assert lazy_formdata.var.datefield >= lazy_formdata.var.datestring
    assert lazy_formdata.var.datestring >= lazy_formdata.var.datefield
    assert lazy_formdata.var.datefield <= lazy_formdata.var.datestring
    assert lazy_formdata.var.datestring <= lazy_formdata.var.datefield
    assert not lazy_formdata.var.datefield != lazy_formdata.var.datestring
    assert not lazy_formdata.var.datestring != lazy_formdata.var.datefield
    assert not lazy_formdata.var.datefield < lazy_formdata.var.datestring
    assert not lazy_formdata.var.datestring < lazy_formdata.var.datefield
    assert not lazy_formdata.var.datefield > lazy_formdata.var.datestring
    assert not lazy_formdata.var.datestring > lazy_formdata.var.datefield

    for date in (
        '2018-07-31',
        '2018-07-31 00:00',
        '2018-07-31 00:00:00',
        '31/07/2018',
        '31/07/2018 00h00',
        '31/07/2018 00:00:00',
        datetime.date(2018, 7, 31),
        datetime.datetime(2018, 7, 31, 0, 0),
        time.strptime('2018-07-31', '%Y-%m-%d'),
    ):
        assert lazy_formdata.var.datefield == date
        assert lazy_formdata.var.datefield >= date
        assert lazy_formdata.var.datefield <= date
        assert date == lazy_formdata.var.datefield
        assert date <= lazy_formdata.var.datefield
        assert date >= lazy_formdata.var.datefield
        assert not lazy_formdata.var.datefield != date
        assert not date != lazy_formdata.var.datefield
        assert not lazy_formdata.var.datefield > date
        assert not lazy_formdata.var.datefield < date
        assert not date < lazy_formdata.var.datefield
        assert not date > lazy_formdata.var.datefield

    for date in (
        '2018-08-31',
        '2018-07-31 01:00',
        '2018-07-31 01:00:00',
        '31/08/2018',
        '31/07/2018 01h00',
        '31/07/2018 01:00:00',
        datetime.date(2018, 8, 31),
        datetime.datetime(2018, 8, 31, 0, 0),
        time.strptime('2018-08-31', '%Y-%m-%d'),
    ):
        assert lazy_formdata.var.datefield != date
        assert date != lazy_formdata.var.datefield
        assert not lazy_formdata.var.datefield == date
        assert not date == lazy_formdata.var.datefield
        assert lazy_formdata.var.datefield < date
        assert not lazy_formdata.var.datefield >= date
        assert lazy_formdata.var.datefield <= date
        assert not lazy_formdata.var.datefield > date
        assert date > lazy_formdata.var.datefield
        assert not date <= lazy_formdata.var.datefield
        assert date >= lazy_formdata.var.datefield
        assert not date < lazy_formdata.var.datefield

    assert lazy_formdata.var.itemsfield == 'aa, ac'
    assert 'aa' in lazy_formdata.var.itemsfield  # taken as a list
    assert 'aa,' not in lazy_formdata.var.itemsfield  # not as a string
    assert lazy_formdata.var.filefield == 'test.txt'
    assert lazy_formdata.var.filefield.raw.base_filename == 'test.txt'
    assert lazy_formdata.var.filefield.raw.content_type == 'text/plain'
    assert lazy_formdata.var.filefield.file_size == 11

    context = pub.substitutions.get_context_variables(mode='lazy')
    tmpl = Template('{{form_var_filefield_file_size}}')
    assert tmpl.render(context) == '11'

    formdata = FormDef.select()[0].data_class()
    lazy_formdata = LazyFormData(formdata)
    assert lazy_formdata.tracking_code is None
    formdata.data = {'future_tracking_code': 'CDCBGWQX'}
    assert lazy_formdata.tracking_code == 'CDCBGWQX'

    formdata = FormDef.select()[0].data_class().select()[0]
    lazy_formdata = LazyFormData(formdata)
    assert lazy_formdata.tracking_code is None

    tracking_code = TrackingCode()
    tracking_code.formdata = formdata
    tracking_code.store()
    formdata = FormDef.select()[0].data_class().get(formdata.id)
    lazy_formdata = LazyFormData(formdata)
    assert lazy_formdata.tracking_code == tracking_code.id


def test_lazy_formdata_duplicated_varname(pub, variable_test_data):
    formdef = FormDef.select()[0]
    formdata = FormDef.select()[0].data_class().select()[0]
    lazy_formdata = LazyFormData(formdata)
    assert lazy_formdata.var.foo_foo == 'bar'

    formdef.fields.append(fields.StringField(id='100', label='string', varname='foo_foo'))
    formdef.store()
    formdata = FormDef.select()[0].data_class().select()[0]
    lazy_formdata = LazyFormData(formdata)
    assert lazy_formdata.var.foo_foo == 'bar'

    # add a value to 2nd field with foo_foo as varname
    formdata.data['100'] = 'baz'
    lazy_formdata = LazyFormData(formdata)
    assert lazy_formdata.var.foo_foo == 'bar'  # 1st value

    # remove value from 1st field with foo_foo as varname
    formdata.data['0'] = None
    lazy_formdata = LazyFormData(formdata)
    assert lazy_formdata.var.foo_foo == 'baz'  # 2nd value


def test_lazy_formdata_workflow_data(pub):
    formdef = FormDef()
    formdef.name = 'foobar'
    formdef.fields = []
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {}
    formdata.workflow_data = {
        '_markers_stack': [{'status_id': '1'}, {'status_id': '2'}, {'status_id': '3'}],
        'foo_bar': 'plop',
        'other': {'test': 'foobar'},
    }
    pub.substitutions.feed(pub)
    pub.substitutions.feed(formdef)
    pub.substitutions.feed(formdata)

    context = pub.substitutions.get_context_variables(mode='lazy')
    assert 'form_workflow_data_foo_bar' in context.get_flat_keys()
    assert context['form_workflow_data_foo_bar'] == 'plop'
    assert 'form_workflow_data_other_test' in context.get_flat_keys()
    assert context['form_workflow_data_other_test'] == 'foobar'

    assert 'form_workflow_data__markers_stack' not in context.get_flat_keys()
    with pytest.raises(KeyError):
        # noqa pylint: disable=pointless-statement
        context['form_workflow_data__markers_stack']


def test_lazy_formdata_live_item(pub):
    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'items'
    carddef.digest_templates = {'default': '{{form_var_name}}'}
    carddef.fields = [
        fields.StringField(id='0', label='string', varname='name'),
        fields.StringField(id='1', label='string', varname='attr'),
    ]
    carddef.store()
    for i, value in enumerate(['foo', 'bar', 'baz']):
        carddata = carddef.data_class()()
        carddata.data = {
            '0': value,
            '1': 'attr%s' % i,
        }
        carddata.just_created()
        carddata.store()

    ds = {'type': 'carddef:%s' % carddef.url_name}
    formdef = FormDef()
    formdef.name = 'foobar'
    formdef.fields = [
        fields.ItemField(id='0', label='string', varname='foo', data_source=ds, display_disabled_items=True)
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {
        '0': str(carddata.id),
    }
    formdata.data['0_display'] = formdef.fields[0].store_display_value(formdata.data, '0')
    formdata.data['0_structured'] = formdef.fields[0].store_structured_value(formdata.data, '0')
    pub.substitutions.feed(pub)
    pub.substitutions.feed(formdef)
    pub.substitutions.feed(formdata)

    context = pub.substitutions.get_context_variables(mode='lazy')
    assert context['form_var_foo_live_name'] == 'items'
    assert context['form_var_foo_live_number'] == carddata.get_display_id()
    assert context['form_var_foo_live_var_name'] == 'baz'
    assert context['form_var_foo_live_var_attr'] == 'attr2'
    assert 'form_var_foo_live_var_attr' in context.get_flat_keys()

    # check it also works with custom views
    ds = {'type': 'carddef:%s:xxx' % carddef.url_name}
    formdef.fields = [
        fields.ItemField(id='0', label='string', varname='foo', data_source=ds, display_disabled_items=True)
    ]
    formdef.store()
    pub.substitutions.reset()
    pub.substitutions.feed(pub)
    pub.substitutions.feed(formdef)
    pub.substitutions.feed(formdata)
    pub.get_request().live_card_cache = {}
    assert 'form_var_foo_live_var_attr' in context.get_flat_keys()
    assert context['form_var_foo_live_var_name'] == 'baz'

    # mock missing carddef to get call count
    pub.get_request().live_card_cache = {}
    context = pub.substitutions.get_context_variables(mode='lazy')
    with mock.patch('wcs.carddef.CardDef.get_by_urlname') as get_by_urlname:
        get_by_urlname.side_effect = KeyError
        assert context['form_var_foo_live'] is None

        with pytest.raises(KeyError):
            # noqa pylint: disable=pointless-statement
            context['form_var_foo_live_name']
        assert get_by_urlname.call_count == 1
        with pytest.raises(KeyError):  # repeated access, will go through cache
            # noqa pylint: disable=pointless-statement
            context['form_var_foo_live_name']
            assert context['form_var_foo_live'] is None
        assert get_by_urlname.call_count == 1
        assert 'form_var_foo_live_var_attr' not in context.get_flat_keys()


def test_lazy_formdata_live_user_item(pub, local_user):
    NamedDataSource.wipe()
    datasource = NamedDataSource(name='foo')
    datasource.data_source = {'type': 'wcs:users'}
    datasource.store()

    formdef = FormDef()
    formdef.name = 'foobar'
    formdef.fields = [
        fields.ItemField(
            id='0',
            label='string',
            varname='foo',
            data_source={'type': 'foo'},
        )
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {
        '0': str(local_user.id),
    }
    formdata.data['0_display'] = formdef.fields[0].store_display_value(formdata.data, '0')
    formdata.data['0_structured'] = formdef.fields[0].store_structured_value(formdata.data, '0')
    pub.substitutions.feed(pub)
    pub.substitutions.feed(formdef)
    pub.substitutions.feed(formdata)

    context = pub.substitutions.get_context_variables(mode='lazy')
    assert context['form_var_foo_live']
    assert context['form_var_foo_live_name'] == local_user.name
    assert context['form_var_foo_live_email'] == local_user.email
    assert 'form_var_foo_live_email' in context.get_flat_keys()

    # check with invalid value, live value will be None.
    formdata.data = {
        '0': 'abc',
    }
    context = pub.substitutions.get_context_variables(mode='lazy')
    assert context['form_var_foo_live'] is None


def test_lazy_formdata_card_live_items(pub):
    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'items'
    carddef.digest_templates = {'default': '{{form_var_name}}'}
    carddef.fields = [
        fields.StringField(id='0', label='string', varname='name'),
        fields.StringField(id='1', label='string', varname='attr'),
    ]
    carddef.store()
    carddatas = []
    for i, value in enumerate(['foo', 'bar', 'baz']):
        carddata = carddef.data_class()()
        carddata.data = {
            '0': value,
            '1': 'attr%s' % i,
        }
        carddata.just_created()
        carddata.store()
        carddatas.append(carddata)

    ds = {'type': 'carddef:%s' % carddef.url_name}
    formdef = FormDef()
    formdef.name = 'foobar'
    formdef.fields = [
        fields.ItemsField(id='0', label='items', varname='foo', data_source=ds, display_disabled_items=True)
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {
        '0': [str(carddatas[-1].id), str(carddatas[0].id)],
    }
    formdata.data['0_display'] = formdef.fields[0].store_display_value(formdata.data, '0')
    formdata.data['0_structured'] = formdef.fields[0].store_structured_value(formdata.data, '0')
    pub.substitutions.feed(pub)
    pub.substitutions.feed(formdef)
    pub.substitutions.feed(formdata)

    context = pub.substitutions.get_context_variables(mode='lazy')
    assert 'form_var_foo_live_0_name' in context.get_flat_keys()
    assert 'form_var_foo_live_1_name' in context.get_flat_keys()
    assert [context[k] for k in context.get_flat_keys()]
    assert context['form_var_foo_live_0_name'] == 'items'
    assert context['form_var_foo_live_0_number'] == carddata.get_display_id()
    assert context['form_var_foo_live_0_var_name'] == 'baz'
    assert context['form_var_foo_live_0_var_attr'] == 'attr2'
    with pytest.raises(KeyError):
        assert context['form_var_foo_live_2_var_attr']

    # check |getlist
    tmpl = Template('{% for v in form_var_foo_live|getlist:"var_name" %}{{ v }},{% endfor %}')
    assert tmpl.render(context) == 'baz,foo,'
    tmpl = Template('{% for v in form_var_foo_live|getlist:"var_name_x" %}{{ v }},{% endfor %}')
    assert tmpl.render(context) == 'None,None,'

    delattr(get_request(), 'live_card_cache')
    carddatas[-1].remove_self()
    context = pub.substitutions.get_context_variables(mode='lazy')
    assert 'form_var_foo_live_0_name' not in context.get_flat_keys()
    assert 'form_var_foo_live_1_name' in context.get_flat_keys()
    assert [context[k] for k in context.get_flat_keys()]


def test_lazy_formdata_user_live_items(pub, local_user):
    NamedDataSource.wipe()
    datasource = NamedDataSource(name='foo')
    datasource.data_source = {'type': 'wcs:users'}
    datasource.store()

    user2 = get_publisher().user_class()
    user2.name = 'Second User'
    user2.email = 'second.user@example.com'
    user2.store()

    formdef = FormDef()
    formdef.name = 'foobar'
    formdef.fields = [
        fields.ItemsField(
            id='0',
            label='users',
            varname='foo',
            data_source={'type': 'foo'},
        )
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {
        '0': [str(local_user.id), str(user2.id)],
    }
    formdata.data['0_display'] = formdef.fields[0].store_display_value(formdata.data, '0')
    formdata.data['0_structured'] = formdef.fields[0].store_structured_value(formdata.data, '0')
    assert formdata.data['0_display'] == 'Jean Darmette, Second User'

    pub.substitutions.feed(pub)
    pub.substitutions.feed(formdef)
    pub.substitutions.feed(formdata)

    context = pub.substitutions.get_context_variables(mode='lazy')
    assert [context[k] for k in context.get_flat_keys()]
    assert context['form_var_foo_live_0_email'] == local_user.email
    assert context['form_var_foo_live_1_email'] == user2.email


def test_lazy_formdata_queryset(pub, variable_test_data):
    lazy_formdata = variable_test_data
    data_class = lazy_formdata._formdef.data_class()
    for _ in range(6):
        formdata = data_class()
        formdata.just_created()
        formdata.store()
    for _ in range(4):
        formdata = data_class()
        formdata.just_created()
        formdata.jump_status('finished')
        formdata.store()

    formdata = data_class()
    formdata.status = 'draft'
    formdata.store()

    assert lazy_formdata.objects.count == 11
    assert lazy_formdata.objects.drafts().count == 1
    assert lazy_formdata.objects.pending().count == 7
    assert lazy_formdata.objects.done().count == 4
    assert lazy_formdata.objects.drafts().count == 1
    # check __len__
    assert len(lazy_formdata.objects.drafts()) == 1

    assert lazy_formdata.objects.current_user().count == 0
    pub.get_request()._user = ()  # reset cache
    pub.get_request().session = sessions.BasicSession(id=1)
    pub.get_request().session.set_user(pub.user_class.select()[0].id)
    assert lazy_formdata.objects.current_user().count == 1

    qs = lazy_formdata.objects.drafts()
    # check __iter__
    for draft in qs:
        assert draft.internal_id == formdata.id
    # check __getitem__
    assert qs[0].internal_id == formdata.id
    # check against for cached resultset
    assert qs[0].internal_id == formdata.id
    # check __iter__ with cached resultset
    for draft in qs:
        assert draft.internal_id == formdata.id

    # check __iter__ creates a cached resultset
    qs = lazy_formdata.objects.drafts()
    for formdata1, formdata2 in zip(list(qs), list(qs)):
        assert formdata1 is formdata2

    # check ordering
    qs = lazy_formdata.objects.pending().order_by('id')
    assert qs.count == 7
    assert [x.number for x in qs] == ['1-1', '1-2', '1-3', '1-4', '1-5', '1-6', '1-7']

    # check ordering with invalid value
    LoggedError.wipe()
    qs = lazy_formdata.objects.pending().order_by(datetime.date(2022, 8, 2))
    assert qs.count == 0
    assert LoggedError.count() == 1
    logged_error = LoggedError.select()[0]
    assert logged_error.summary == 'Invalid value datetime.date(2022, 8, 2) for "order_by"'

    # Check accessing an non-numeric attribute doesn't try to cache things
    # (see code for explanation)
    manager = lazy_formdata.objects
    with pytest.raises(TypeError):
        # noqa pylint: disable=pointless-statement
        manager['drafts']
    assert manager._cached_resultset is None


def test_objects_filter(pub):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form'
    formdef.fields = []
    formdef.store()
    formdata_class = formdef.data_class()
    formdata_class.wipe()

    formdata = formdata_class()
    formdata.just_created()
    formdata.store()

    context = pub.substitutions.get_context_variables(mode='lazy')
    tmpl = Template('{{forms|objects:"form"|count}}')
    assert tmpl.render(context) == '1'

    LoggedError.wipe()
    context = pub.substitutions.get_context_variables(mode='lazy')
    tmpl = Template('{{forms|objects:"form"|first|count}}')
    assert tmpl.render(context) == '0'
    assert LoggedError.count() == 1
    logged_error = LoggedError.select()[0]
    assert logged_error.summary == '|count used on uncountable value'

    # called on invalid object
    LoggedError.wipe()
    tmpl = Template('{{xxx|objects:"form"|count}}')
    assert tmpl.render(context) == '0'
    assert LoggedError.count() == 1
    logged_error = LoggedError.select()[0]
    assert logged_error.summary == '|objects with invalid source (\'\')'

    # called with missing source
    LoggedError.wipe()
    tmpl = Template('{{forms|objects:"bla bla bla"|count}}')
    assert tmpl.render(context) == '0'
    assert LoggedError.count() == 1
    logged_error = LoggedError.select()[0]
    assert logged_error.summary == '|objects with invalid reference (\'bla bla bla\')'


def test_lazy_formdata_queryset_distance(pub, variable_test_data):
    # Form
    lazy_formdata = variable_test_data
    formdef = lazy_formdata._formdef
    formdef.geolocations = {'base': 'Base'}
    formdef.store()
    data_class = lazy_formdata._formdef.data_class()
    # Card
    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'items'
    carddef.fields = []
    carddef.geolocations = {'base': 'Base'}
    carddef.store()
    carddata_class = carddef.data_class()
    carddata_class.wipe()

    # create initial carddata like lazy_formdata, with same geolocations
    carddata = carddata_class()
    carddata.geolocations = {'base': {'lat': 1, 'lon': 2}}
    carddata.just_created()
    carddata.store()
    lazy_carddata = LazyFormData(carddata)

    # create objects
    for i in range(6):
        for dclass in [data_class, carddata_class]:
            data = dclass()
            data.geolocations = {'base': {'lat': i, 'lon': i}}
            data.just_created()
            data.store()
    for i in range(4):
        for dclass in [data_class, carddata_class]:
            data = dclass()
            data.geolocations = {'base': {'lat': i + 0.5, 'lon': i + 0.5}}
            data.just_created()
            data.jump_status('finished')
            data.store()

    # drafts
    formdata = data_class()
    formdata.status = 'draft'
    formdata.geolocations = {'base': {'lat': 1, 'lon': 2}}
    formdata.store()
    carddata = carddata_class()
    carddata.status = 'draft'
    carddata.geolocations = {'base': {'lat': 1, 'lon': 2}}
    carddata.store()

    # compute distance against map field of lazy formdata
    nearby = lazy_formdata.objects.filter_by_distance(200000)
    assert len(nearby) == 6
    assert {x.number for x in nearby} == {'1-1', '1-3', '1-4', '1-8', '1-9', '1-10'}
    nearby = lazy_carddata.objects.set_geo_center(lazy_formdata).filter_by_distance(200000)
    assert len(nearby) == 6
    assert {x.number for x in nearby} == {'1-1', '1-3', '1-4', '1-8', '1-9', '1-10'}

    # compute distance against geolocation
    lazy_formdata._formdata.geolocations = {'base': {'lat': 2, 'lon': 2.5}}
    lazy_formdata._formdata.store()
    nearby = lazy_formdata.objects.filter_by_distance(200000)
    assert len(nearby) == 5
    assert {x.number for x in nearby} == {'1-1', '1-4', '1-5', '1-9', '1-10'}
    assert bool(nearby) is True
    nearby = lazy_carddata.objects.set_geo_center(lazy_formdata).filter_by_distance(200000)
    assert len(nearby) == 5
    assert {x.number for x in nearby} == {'1-1', '1-4', '1-5', '1-9', '1-10'}
    assert bool(nearby) is True

    context = pub.substitutions.get_context_variables(mode='lazy')
    tmpl = Template('{{form_objects|filter_by_distance:200000|count}}')
    assert tmpl.render(context) == '5'
    tmpl = Template('{{cards|objects:"items"|set_geo_center:form|filter_by_distance:200000|count}}')
    assert tmpl.render(context) == '5'
    # using generic filter_by
    tmpl = Template('{{form_objects|filter_by:"distance"|filter_value:200000|count}}')
    assert tmpl.render(context) == '5'
    # backward compatibility with old filter name
    tmpl = Template('{{form_objects|distance_filter:200000|count}}')
    assert tmpl.render(context) == '5'
    # error handling, invalid distance
    LoggedError.wipe()
    tmpl = Template('{{form_objects|filter_by_distance:"plop"|count}}')
    assert tmpl.render(context) == '0'
    assert LoggedError.count() == 1
    logged_error = LoggedError.select()[0]
    assert logged_error.summary == 'invalid value for distance (\'plop\')'

    lazy_formdata._formdata.geolocations = {'base': {'lat': 7, 'lon': 7.5}}
    nearby = lazy_formdata.objects.filter_by_distance(200000)
    assert bool(nearby) is False
    assert len(nearby) == 0
    nearby = lazy_carddata.objects.set_geo_center(lazy_formdata).filter_by_distance(200000)
    assert bool(nearby) is False
    assert len(nearby) == 0

    tmpl = Template('{{form_objects|filter_by_distance:200000|count}}')
    assert tmpl.render(context) == '0'
    tmpl = Template('{{cards|objects:"items"|set_geo_center:form|filter_by_distance:200000|count}}')
    assert tmpl.render(context) == '0'

    formdef.fields = []
    formdef.store()
    lazy_formdata._formdata.geolocations = None
    lazy_formdata._formdata.store()
    nearby = lazy_formdata.objects.filter_by_distance(200000)
    assert len(nearby) == 0


def test_lazy_formdata_queryset_filter(pub, variable_test_data):
    local_user_id = variable_test_data.user.id

    wf = Workflow.get_default_workflow()
    wf.id = None
    wf.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(wf)
    wf.backoffice_fields_formdef.fields = [
        fields.StringField(id='bo1', label='1st backoffice field', varname='backoffice_blah'),
    ]
    wf.store()

    lazy_formdata = variable_test_data
    formdef = lazy_formdata._formdef
    formdef.workflow = wf
    formdef.store()
    data_class = lazy_formdata._formdef.data_class()
    for i in range(6):
        formdata = data_class()
        formdata.data = {'0': 'bar', '1': True, 'bo1': 'plop1', '10': '3'}
        if i == 5:
            formdata.data['3'] = datetime.date(2018, 8, 31).timetuple()
        formdata.just_created()
        formdata.store()
    for _ in range(4):
        formdata = data_class()
        formdata.data = {
            '0': 'foo',
            '1': False,
            '3': datetime.date(2018, 7, 31).timetuple(),
            'bo1': 'plop2',
            '10': '4',
        }
        formdata.just_created()
        formdata.jump_status('finished')
        formdata.store()

    finished_formdata = formdata

    formdata = data_class()
    formdata.data = {'0': 'bar', 'bo1': 'plop1'}
    formdata.status = 'draft'
    formdata.store()

    formdata = data_class()
    formdata.just_created()
    formdata.data = {'0': 'bar'}
    formdata.anonymise()

    # filter function
    queryset = lazy_formdata.objects.filter_by('foo_foo').apply_filter_value('bar')
    assert queryset.count == 7
    queryset = lazy_formdata.objects.filter_by('foo_foo').apply_filter_value('foo')
    assert queryset.count == 4
    queryset = lazy_formdata.objects.filter_by('foo_foo').apply_filter_value('X')
    assert queryset.count == 0
    LoggedError.wipe()
    queryset = lazy_formdata.objects.filter_by('unknown').apply_filter_value('X')
    assert queryset.count == 0
    assert LoggedError.count() == 1
    logged_error = LoggedError.select()[0]
    assert logged_error.summary == 'Invalid filter "unknown"'

    queryset = lazy_formdata.objects.filter_by('datefield').apply_filter_value(
        datetime.date(2018, 7, 31).timetuple()
    )
    assert queryset.count == 5
    queryset = lazy_formdata.objects.filter_by('datefield').apply_filter_value(datetime.date(2018, 7, 31))
    assert queryset.count == 5
    queryset = lazy_formdata.objects.filter_by('datefield').apply_filter_value(datetime.datetime(2018, 7, 31))
    assert queryset.count == 5
    queryset = lazy_formdata.objects.filter_by('datefield').apply_filter_value('2018-07-31')
    assert queryset.count == 5
    queryset = lazy_formdata.objects.filter_by('datefield').apply_filter_value(None)
    assert queryset.count == 5
    queryset = lazy_formdata.objects.filter_by('datefield').apply_filter_value('not a date')
    assert queryset.count == 0
    assert LoggedError.count() == 2
    logged_error = LoggedError.select(order_by='id')[1]
    assert logged_error.summary == 'Invalid value "not a date" for filter "datefield"'

    queryset = lazy_formdata.objects.filter_by('datefield').apply_exclude_value(
        datetime.date(2018, 7, 31).timetuple()
    )
    assert queryset.count == 6  # 1 + 5 null
    queryset = lazy_formdata.objects.filter_by('datefield').apply_exclude_value(datetime.date(2018, 7, 31))
    assert queryset.count == 6
    queryset = lazy_formdata.objects.filter_by('datefield').apply_exclude_value(
        datetime.datetime(2018, 7, 31)
    )
    assert queryset.count == 6
    queryset = lazy_formdata.objects.filter_by('datefield').apply_exclude_value('2018-07-31')
    assert queryset.count == 6
    queryset = lazy_formdata.objects.filter_by('datefield').apply_exclude_value(None)
    assert queryset.count == 6
    queryset = lazy_formdata.objects.filter_by('datefield').apply_exclude_value('still not a date')
    assert queryset.count == 0
    assert LoggedError.count() == 3
    logged_error = LoggedError.select(order_by='id')[2]
    assert logged_error.summary == 'Invalid value "still not a date" for filter "datefield"'

    queryset = lazy_formdata.objects.filter_by('boolfield').apply_filter_value(True)
    assert queryset.count == 6
    queryset = lazy_formdata.objects.filter_by('boolfield').apply_filter_value(1)
    assert queryset.count == 6
    queryset = lazy_formdata.objects.filter_by('boolfield').apply_filter_value('yes')
    assert queryset.count == 6
    queryset = lazy_formdata.objects.filter_by('boolfield').apply_filter_value(False)
    assert queryset.count == 5
    queryset = lazy_formdata.objects.filter_by('boolfield').apply_filter_value(0)
    assert queryset.count == 5
    queryset = lazy_formdata.objects.filter_by('boolfield').apply_exclude_value(0)
    assert queryset.count == 6

    queryset = lazy_formdata.objects.filter_by('term1').apply_filter_value('3')
    assert queryset.count == 7
    queryset = lazy_formdata.objects.filter_by('term1').apply_filter_value(3)
    assert queryset.count == 7
    queryset = lazy_formdata.objects.filter_by('term1').apply_filter_value('foobar')
    assert queryset.count == 0
    queryset = lazy_formdata.objects.filter_by('term1').apply_exclude_value('3')
    assert queryset.count == 4
    queryset = lazy_formdata.objects.filter_by('term1').apply_exclude_value('foobar')
    assert queryset.count == 11

    queryset = lazy_formdata.objects.filter_by('email').apply_filter_value('bar')
    assert queryset.count == 0
    queryset = lazy_formdata.objects.filter_by('email').apply_filter_value('test@localhost')
    assert queryset.count == 1

    # filter function on backoffice field
    queryset = lazy_formdata.objects.filter_by('backoffice_blah').apply_filter_value('plop1')
    assert queryset.count == 6
    queryset = lazy_formdata.objects.filter_by('backoffice_blah').apply_filter_value('plop2')
    assert queryset.count == 4
    queryset = lazy_formdata.objects.filter_by('backoffice_blah').apply_filter_value('X')
    assert queryset.count == 0

    # filter using attribute name
    queryset = lazy_formdata.objects.filter_by_foo_foo().apply_filter_value('bar')
    assert queryset.count == 7

    # filter + exclude current formdata
    queryset = lazy_formdata.objects.exclude_self().filter_by('foo_foo').apply_filter_value('bar')
    assert queryset.count == 6

    queryset = lazy_formdata.objects.exclude_self().filter_by('foo_foo').apply_filter_value('foo')
    assert queryset.count == 4

    # filter + limit to same user
    queryset = lazy_formdata.objects.exclude_self().same_user().filter_by('foo_foo').apply_filter_value('foo')
    assert queryset.count == 0

    for lazy in lazy_formdata.objects.filter_by('foo_foo').apply_filter_value('foo')[:2]:
        lazy._formdata.user_id = variable_test_data._formdata.user_id
        lazy._formdata.store()

    queryset = lazy_formdata.objects.exclude_self().same_user().filter_by('foo_foo').apply_filter_value('foo')
    assert queryset.count == 2

    # + exclude self (lazy being set from the for loop)
    queryset = lazy.objects.exclude_self().same_user().filter_by('foo_foo').apply_filter_value('foo')
    assert queryset.count == 1

    # and with anonymous user
    lazy._formdata.user_id = None
    lazy._formdata.store()
    queryset = lazy.objects.same_user().filter_by('foo_foo').apply_filter_value('foo')
    assert queryset.count == 4

    # filter with anonymised
    queryset = lazy_formdata.objects.with_anonymised().filter_by('foo_foo').apply_filter_value('bar')
    assert queryset.count == 8

    # template tags
    context = pub.substitutions.get_context_variables(mode='lazy')
    tmpl = Template('{{form_objects|filter_by:"foo_foo"|filter_value:"bar"|count}}')
    assert tmpl.render(context) == '7'
    tmpl = Template('{{form_objects|filter_by:"foo_foo"|filter_value:"bar"|length}}')
    assert tmpl.render(context) == '7'
    tmpl = Template('{{form_objects|filter_by:"foo_foo"|exclude_value:"bar"|count}}')
    assert tmpl.render(context) == '4'
    tmpl = Template('{{form_objects|filter_by:"foo_foo"|exclude_value:"bar"|length}}')
    assert tmpl.render(context) == '4'

    pub.substitutions.feed(formdata)
    tmpl = Template('{{form_objects|filter_by:"foo_foo"|filter_value:form_var_foo_foo|count}}')
    assert tmpl.render(context) == '7'
    tmpl = Template('{{form.objects|exclude_self|filter_by:"foo_foo"|filter_value:form_var_foo_foo|count}}')
    assert tmpl.render(context) == '6'

    # errors
    LoggedError.wipe()
    tmpl = Template('{{form.objects|filter_by:""|filter_value:form_var_foo_foo|count}}')
    assert tmpl.render(context) == '0'
    assert LoggedError.count() == 1
    logged_error = LoggedError.select()[0]
    assert logged_error.summary == '|filter_value called without attribute (check |filter_by parameter)'

    LoggedError.wipe()
    tmpl = Template('{{form.objects|filter_value:form_var_foo_foo|count}}')
    assert tmpl.render(context) == '0'
    assert LoggedError.count() == 1
    logged_error = LoggedError.select()[0]
    assert logged_error.summary == '|filter_value called without |filter_by'

    # attach a user to context form to get form_user variable
    formdata.user_id = local_user_id
    formdata.store()

    # test |filter_by_user
    context = pub.substitutions.get_context_variables(mode='lazy')
    for tpl in ['filter_by_user', 'filter_by:"user"|filter_value']:
        tmpl = Template('{{form_objects|%s:form_user|filter_by:"foo_foo"|filter_value:"foo"|count}}' % tpl)
        assert tmpl.render(context) == '1'
        tmpl = Template(
            '{{form_objects|%s:form_user_email|filter_by:"foo_foo"|filter_value:"foo"|count}}' % tpl
        )
        assert tmpl.render(context) == '1'
        tmpl = Template(
            '{{form_objects|%s:form_user_nameid|filter_by:"foo_foo"|filter_value:"foo"|count}}' % tpl
        )
        assert tmpl.render(context) == '1'
        tmpl = Template('{{form_objects|%s:"foo@bar"|filter_by:"foo_foo"|filter_value:"foo"|count}}' % tpl)
        assert tmpl.render(context) == '0'
    assert (
        LazyFormData(formdata)
        .objects.filter_by_user(lazy_formdata.user)
        .filter_by('foo_foo')
        .apply_filter_value('foo')
        .count
        == 1
    )
    assert (
        LazyFormData(formdata)
        .objects.filter_by('user')
        .apply_filter_value(lazy_formdata.user)
        .filter_by('foo_foo')
        .apply_filter_value('foo')
        .count
        == 1
    )
    tmpl = Template(
        '{{form_objects|filter_by:"user"|equal|filter_value:form_user|filter_by:"foo_foo"|filter_value:"foo"|count}}'
    )
    assert tmpl.render(context) == '1'
    for operator in ['not_equal', 'less_than', 'less_than_or_equal', 'greater_than', 'greater_than_or_equal']:
        LoggedError.wipe()
        tmpl = Template('{{form_objects|filter_by:"user"|%s|filter_value:"foo"|count}}' % operator)
        assert tmpl.render(context) == '0'
        assert LoggedError.count() == 1
        logged_error = LoggedError.select(order_by='id')[0]
        assert logged_error.summary == 'Invalid operator "%s" for filter "user"' % operator

    # test |current_user
    pub.get_request()._user = ()  # reset cache
    pub.get_request().session = sessions.BasicSession(id=1)
    pub.get_request().session.set_user(local_user_id)
    tmpl = Template('{{form_objects|current_user|filter_by:"foo_foo"|filter_value:"foo"|count}}')
    assert tmpl.render(context) == '1'

    # test |filter_by_status
    for tpl in ['filter_by_status', 'filter_by:"status"|filter_value']:
        context = pub.substitutions.get_context_variables(mode='lazy')
        tmpl = Template('{{form_objects|%s:"Just Submitted"|count}}' % tpl)
        assert tmpl.render(context) == '7'
        tmpl = Template(
            '{{form_objects|%s:"Just Submitted"|filter_by:"foo_foo"|filter_value:"foo"|count}}' % tpl
        )
        assert tmpl.render(context) == '0'
    assert LazyFormData(formdata).objects.filter_by_status('Just Submitted').count == 7
    assert LazyFormData(formdata).objects.filter_by('status').apply_filter_value('Just Submitted').count == 7
    assert (
        LazyFormData(formdata)
        .objects.filter_by('status')
        .apply_filter_value('Just Submitted')
        .filter_by('foo_foo')
        .apply_filter_value('foo')
        .count
        == 0
    )
    assert LazyFormData(formdata).objects.filter_by('status').apply_filter_value('Finished').count == 4
    assert LazyFormData(formdata).objects.filter_by('status').apply_filter_value('Unknown').count == 0

    # test |pending
    context = pub.substitutions.get_context_variables(mode='lazy')
    tmpl = Template('{{form_objects|pending|count}}')
    assert tmpl.render(context) == '7'
    tmpl = Template('{{form_objects|pending|filter_by:"foo_foo"|filter_value:"foo"|count}}')
    assert tmpl.render(context) == '0'
    assert LazyFormData(formdata).objects.pending().count == 7
    assert LazyFormData(formdata).objects.pending().filter_by('foo_foo').apply_filter_value('foo').count == 0

    # test |done
    context = pub.substitutions.get_context_variables(mode='lazy')
    tmpl = Template('{{form_objects|done|count}}')
    assert tmpl.render(context) == '4'

    # test |with_drafts
    context = pub.substitutions.get_context_variables(mode='lazy')
    tmpl = Template('{{form_objects|with_drafts|count}}')
    assert tmpl.render(context) == '12'

    # test |filter_by_internal_id & |filter_by_identifier
    context = pub.substitutions.get_context_variables(mode='lazy')
    for tpl in [
        'filter_by_internal_id',
        'filter_by:"internal_id"|filter_value',
        'filter_by_identifier',
        'filter_by:"identifier"|filter_value',
    ]:
        LoggedError.wipe()
        tmpl = Template('{{form_objects|%s:"%s"|count}}' % (tpl, finished_formdata.id))
        assert tmpl.render(context) == '1'
        tmpl = Template('{{form_objects|%s:"%s"|count}}' % (tpl, '0'))
        assert tmpl.render(context) == '0'
        tmpl = Template('{{form_objects|%s:"%s"|count}}' % (tpl, 'invalid value'))
        assert tmpl.render(context) == '0'
        if 'internal_id' in tpl:
            assert LoggedError.count() == 1
            logged_error = LoggedError.select(order_by='id')[0]
            assert logged_error.summary == 'Invalid value "invalid value" for filter "internal_id"'
        elif 'identifier' in tpl:
            assert LoggedError.count() == 0
    LoggedError.wipe()
    queryset = lazy_formdata.objects.filter_by_internal_id(None)
    assert LoggedError.count() == 1
    logged_error = LoggedError.select(order_by='id')[0]
    assert logged_error.summary == 'Invalid value "None" for filter "internal_id"'

    # test |filter_by_number
    for tpl in ['filter_by_number', 'filter_by:"number"|filter_value']:
        tmpl = Template('{{form_objects|%s:"%s"|count}}' % (tpl, finished_formdata.get_display_id()))
        assert tmpl.render(context) == '1'
        tmpl = Template('{{form_objects|%s:"%s"|count}}' % (tpl, 'invalid value'))
        assert tmpl.render(context) == '0'
    tmpl = Template(
        '{{form_objects|filter_by:"number"|equal|filter_value:"%s"|count}}'
        % finished_formdata.get_display_id()
    )
    assert tmpl.render(context) == '1'
    for operator in ['not_equal', 'less_than', 'less_than_or_equal', 'greater_than', 'greater_than_or_equal']:
        LoggedError.wipe()
        tmpl = Template(
            '{{form_objects|filter_by:"number"|%s|filter_value:"%s"|count}}'
            % (operator, finished_formdata.get_display_id())
        )
        assert tmpl.render(context) == '0'
        assert LoggedError.count() == 1
        logged_error = LoggedError.select(order_by='id')[0]
        assert logged_error.summary == 'Invalid operator "%s" for filter "number"' % operator

    # test |is_empty
    tmpl = Template('{{form_objects|pending|is_empty}}')
    assert tmpl.render(context) == 'False'
    tmpl = Template('{{form_objects|pending|filter_by:"foo_foo"|filter_value:"foo"|is_empty}}')
    assert tmpl.render(context) == 'True'

    # test |getlist
    tmpl = Template('{% for v in form_objects|order_by:"id"|getlist:"foo_foo" %}{{ v }},{% endfor %}')
    assert tmpl.render(context) == 'bar,bar,bar,bar,bar,bar,bar,foo,foo,foo,foo,'
    tmpl = Template('{% for v in form_objects|order_by:"id"|getlist:"datefield" %}{{ v|date }},{% endfor %}')
    assert tmpl.render(context) == '2018-07-31,,,,,,2018-08-31,2018-07-31,2018-07-31,2018-07-31,2018-07-31,'
    tmpl = Template('{% if "foo" in form_objects|getlist:"foo_foo" %}OK{% else %}KO{% endif%}')
    assert tmpl.render(context) == 'OK'
    tmpl = Template('{% if "fooooooooooooooo" in form_objects|getlist:"foo_foo" %}OK{% else %}KO{% endif%}')
    assert tmpl.render(context) == 'KO'
    tmpl = Template('{{ form_objects|order_by:"id"|getlist:"foo_foo"|count }}')
    assert tmpl.render(context) == '11'
    assert LazyFormData(formdata).objects.order_by('id').getlist('foo_foo') == [
        'bar',
        'bar',
        'bar',
        'bar',
        'bar',
        'bar',
        'bar',
        'foo',
        'foo',
        'foo',
        'foo',
    ]
    assert LazyFormData(formdata).objects.order_by('id').getlist('form_var_foo_foo') == [
        'bar',
        'bar',
        'bar',
        'bar',
        'bar',
        'bar',
        'bar',
        'foo',
        'foo',
        'foo',
        'foo',
    ]
    assert set(LazyFormData(formdata).objects.getlist('unknown')) == {None}
    tmpl = Template('{{form_objects|pending|getlist:"foo_foo"|is_empty}}')
    assert tmpl.render(context) == 'False'
    tmpl = Template('{{form_objects|filter_by:"foo_foo"|filter_value:"foo"|getlist:"foo_foo"|is_empty}}')
    assert tmpl.render(context) == 'False'
    tmpl = Template(
        '{{form_objects|pending|filter_by:"foo_foo"|filter_value:"foo"|getlist:"foo_foo"|is_empty}}'
    )
    assert tmpl.render(context) == 'True'

    # test with cache populated
    for value in [
        datetime.date(2018, 7, 31).timetuple(),
        datetime.date(2018, 7, 31),
        datetime.datetime(2018, 7, 31),
        '2018-07-31',
    ]:
        assert value in LazyFormData(formdata).objects.getlist('datefield')
        tmpl = Template(
            '''{% with form_objects|getlist:"datefield" as objects %}
        {% for v in objects %}{% endfor %}{% if value in objects %}OK{% else %}KO{% endif%}
        {% endwith %}'''
        )
        context['value'] = value
        assert tmpl.render(context) == 'OK'
    assert 'not a date' not in LazyFormData(formdata).objects.getlist('datefield')

    # test |getlistdict
    tmpl = Template('{{ form_objects|order_by:"id"|getlistdict:"foo_foo" }}', autoescape=False)
    assert tmpl.render(context) == str(
        [
            {'foo_foo': 'bar'},
            {'foo_foo': 'bar'},
            {'foo_foo': 'bar'},
            {'foo_foo': 'bar'},
            {'foo_foo': 'bar'},
            {'foo_foo': 'bar'},
            {'foo_foo': 'bar'},
            {'foo_foo': 'foo'},
            {'foo_foo': 'foo'},
            {'foo_foo': 'foo'},
            {'foo_foo': 'foo'},
        ]
    )
    tmpl = Template(
        '{{ form_objects|order_by:"id"|getlistdict:"foo_foo:test, boolfield, unknown" }}', autoescape=False
    )
    assert tmpl.render(context) == str(
        [
            {'test': 'bar', 'boolfield': False, 'unknown': None},
            {'test': 'bar', 'boolfield': True, 'unknown': None},
            {'test': 'bar', 'boolfield': True, 'unknown': None},
            {'test': 'bar', 'boolfield': True, 'unknown': None},
            {'test': 'bar', 'boolfield': True, 'unknown': None},
            {'test': 'bar', 'boolfield': True, 'unknown': None},
            {'test': 'bar', 'boolfield': True, 'unknown': None},
            {'test': 'foo', 'boolfield': False, 'unknown': None},
            {'test': 'foo', 'boolfield': False, 'unknown': None},
            {'test': 'foo', 'boolfield': False, 'unknown': None},
            {'test': 'foo', 'boolfield': False, 'unknown': None},
        ]
    )
    tmpl = Template(
        '{{ form_objects|order_by:"id"|getlistdict:"datefield"|first|get:"datefield"|date }}',
        autoescape=False,
    )
    assert tmpl.render(context) == '2018-07-31'

    # template tag called on invalid object
    LoggedError.wipe()
    context = pub.substitutions.get_context_variables(mode='lazy')
    tmpl = Template('{{""|pending}}')
    assert tmpl.render(context) == 'None'
    assert LoggedError.count() == 1
    logged_error = LoggedError.select()[0]
    assert logged_error.summary == '|pending used on something else than a queryset (\'\')'

    LoggedError.wipe()
    tmpl = Template('{{""|filter_value:"foo"}}')
    assert tmpl.render(context) == 'None'
    assert LoggedError.count() == 1
    logged_error = LoggedError.select()[0]
    assert logged_error.summary == '|filter_value used on something else than a queryset (\'\')'


def test_lazy_formdata_queryset_filter_non_unique_varname(pub, variable_test_data):
    lazy_formdata = variable_test_data
    formdef = lazy_formdata._formdef
    # modify fields to have foo_foo as varname for both fields[0] and fields[7]
    assert formdef.fields[7].label == 'string2'
    formdef.fields[7].varname = 'foo_foo'
    formdef.store()

    data_class = lazy_formdata._formdef.data_class()
    for i in range(6):
        formdata = data_class()
        formdata.data = {'0': 'bar', '6': 'baz'}
        if i == 5:
            formdata.data['3'] = datetime.date(2018, 8, 31).timetuple()
        formdata.just_created()
        formdata.store()
    formdatas = []
    for _ in range(4):
        formdata = data_class()
        formdata.data = {
            '0': 'foo',
        }
        formdata.just_created()
        formdata.jump_status('finished')
        formdata.store()
        formdatas.append(formdata)

    context = pub.substitutions.get_context_variables(mode='lazy')
    tmpl = Template('{{form_objects|filter_by:"foo_foo"|filter_value:"bar"|count}}')
    assert tmpl.render(context) == '7'
    tmpl = Template('{{form_objects|filter_by:"foo_foo"|filter_value:"other"|count}}')
    assert tmpl.render(context) == '1'
    tmpl = Template('{{form_objects|filter_by:"foo_foo"|filter_value:"baz"|count}}')
    assert tmpl.render(context) == '6'
    tmpl = Template('{{form_objects|filter_by:"foo_foo"|exclude_value:"bar"|count}}')
    assert tmpl.render(context) == '4'  # 11 - 7

    for formdata in formdatas[:-1]:
        formdata.data['6'] = 'bar'
        formdata.store()

    tmpl = Template('{{form_objects|filter_by:"foo_foo"|exclude_value:"bar"|count}}')
    assert tmpl.render(context) == '1'


def test_filter_on_unknown_card_value(pub):
    LoggedError.wipe()
    CardDef.wipe()
    FormDef.wipe()

    carddef = CardDef()
    carddef.name = 'card'
    carddef.digest_templates = {'default': '{{form_var_name}}'}
    carddef.fields = [fields.StringField(id='0', label='string', varname='name')]
    carddef.store()

    carddata = carddef.data_class()()
    carddata.data = {'0': 'name'}
    carddata.just_created()
    carddata.store()

    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [
        fields.ItemField(id='0', label='item', varname='foo', data_source={'type': 'carddef:card'})
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {
        '0': str(carddata.id),
    }
    formdata.data['0_display'] = formdef.fields[0].store_display_value(formdata.data, '0')
    formdata.data['0_structured'] = formdef.fields[0].store_structured_value(formdata.data, '0')
    formdata.just_created()
    formdata.store()

    context = pub.substitutions.get_context_variables(mode='lazy')

    tmpl = Template('{{forms|objects:"test"|filter_by:"foo"|filter_value:%s|count}}' % carddata.id)
    tmpl.render(context)
    assert tmpl.render(context) == '1'
    assert LoggedError.count() == 0

    for unknown_value in (0, 'xx'):
        tmpl = Template(
            '{{forms|objects:"test"|filter_by:"foo"|filter_value:%s|count}}' % repr(unknown_value)
        )
        tmpl.render(context)
        assert tmpl.render(context) == '0'
        assert LoggedError.count() == 0


def test_filter_on_page_field(pub):
    LoggedError.wipe()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [
        fields.PageField(id='1', label='Page', varname='page'),
    ]
    formdef.store()

    data_class = formdef.data_class()
    formdata = data_class()
    formdata.just_created()
    formdata.store()

    context = pub.substitutions.get_context_variables(mode='lazy')

    tmpl = Template('{{forms|objects:"test"|filter_by:"page"|filter_value:"100"}}')
    tmpl.render(context)
    assert LoggedError.count() == 1
    logged_error = LoggedError.select()[0]
    assert logged_error.summary == 'Invalid filter "page"'


def test_numeric_filter_on_string(pub):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [
        fields.StringField(id='1', label='String', varname='string'),
    ]
    formdef.store()

    data_class = formdef.data_class()
    for i in range(10):
        formdata = data_class()
        formdata.data = {'1': str(100 + i)}
        formdata.just_created()
        formdata.store()

    context = pub.substitutions.get_context_variables(mode='lazy')

    # equality
    tmpl = Template('{{forms|objects:"test"|filter_by:"string"|filter_value:"100"|count}}')
    assert tmpl.render(context) == '1'

    # less than
    tmpl = Template('{{forms|objects:"test"|filter_by:"string"|less_than|filter_value:"105"|count}}')
    assert tmpl.render(context) == '5'

    # avoid conversions on strings with a leading 0
    formdata = data_class()
    formdata.data = {'1': '01234567890'}
    formdata.just_created()
    formdata.store()

    tmpl = Template('{{forms|objects:"test"|filter_by:"string"|filter_value:"1234567890"|count}}')
    assert tmpl.render(context) == '0'

    tmpl = Template('{{forms|objects:"test"|filter_by:"string"|filter_value:"01234567890"|count}}')
    assert tmpl.render(context) == '1'

    # but zero is still ok
    tmpl = Template('{{forms|objects:"test"|filter_by:"string"|greater_than|filter_value:"0"|count}}')
    assert tmpl.render(context) == '10'

    # avoid conversions on strings with underscores
    formdata = data_class()
    formdata.data = {'1': '13_12'}
    formdata.just_created()
    formdata.store()

    tmpl = Template('{{forms|objects:"test"|filter_by:"string"|filter_value:"13_12"|count}}')
    assert tmpl.render(context) == '1'
    tmpl = Template('{{forms|objects:"test"|filter_by:"string"|filter_value:"1312"|count}}')
    assert tmpl.render(context) == '0'


def test_numeric_filter_on_numbers(pub):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [
        fields.NumericField(id='1', label='Number', varname='numeric'),
    ]
    formdef.store()

    data_class = formdef.data_class()
    for i in range(10):
        formdata = data_class()
        formdata.data = {'1': 100 + i}
        formdata.just_created()
        formdata.store()

    context = pub.substitutions.get_context_variables(mode='lazy')

    # equality
    tmpl = Template('{{forms|objects:"test"|filter_by:"numeric"|filter_value:100|count}}')
    assert tmpl.render(context) == '1'

    # less than
    tmpl = Template('{{forms|objects:"test"|filter_by:"numeric"|less_than|filter_value:"105"|count}}')
    assert tmpl.render(context) == '5'

    # zero is ok
    tmpl = Template('{{forms|objects:"test"|filter_by:"numeric"|greater_than|filter_value:"0"|count}}')
    assert tmpl.render(context) == '10'


def test_numeric_field_comparisons(pub):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [
        fields.NumericField(id='1', label='Number1', varname='numeric1'),
        fields.NumericField(id='2', label='Number2', varname='numeric2'),
        fields.NumericField(id='3', label='Number3', varname='numeric3'),
        fields.NumericField(id='4', label='Number4', varname='numeric4'),
        fields.StringField(id='5', label='String5', varname='string5'),
    ]
    formdef.store()

    data_class = formdef.data_class()
    formdata = data_class()
    formdata.data = {'1': 12, '2': 2, '3': 2, '4': 0, '5': '12'}
    formdata.just_created()
    formdata.store()

    pub.substitutions.feed(formdata)

    for condition_string, expected_result in [
        ('form_var_numeric1 == form_var_numeric2', False),
        ('form_var_numeric2 == form_var_numeric3', True),
        ('form_var_numeric1 != form_var_numeric2', True),
        ('form_var_numeric2 != form_var_numeric3', False),
        ('form_var_numeric1 > form_var_numeric2', True),
        ('form_var_numeric1 >= form_var_numeric2', True),
        ('form_var_numeric1 < form_var_numeric2', False),
        ('form_var_numeric1 <= form_var_numeric2', False),
        ('form_var_numeric3', True),
        ('form_var_numeric4', False),
        ('form_var_numeric1 == 12', True),
        ('form_var_numeric1 == "12"', True),
        ('form_var_numeric1 == "abc"', False),
        ('form_var_numeric1 == form_var_string5', True),
        ('form_var_numeric1 != form_var_string5', False),
        ('form_var_numeric2 == form_var_string5', False),
    ]:
        condition = Condition({'type': 'django', 'value': condition_string})
        assert condition.evaluate() is expected_result


def test_lazy_formdata_queryset_get_from_first(pub, variable_test_data):
    context = pub.substitutions.get_context_variables(mode='lazy')
    del context['form']  # remove actual form from context
    # direct attribute access
    tmpl = Template('{{forms|objects:"foobarlazy"|first|get:"foo_foo"}}')
    assert tmpl.render(context) == 'bar'
    # indirect, with full names
    tmpl = Template('{{forms|objects:"foobarlazy"|first|get:"form_var_foo_foo"}}')
    assert tmpl.render(context) == 'bar'


def test_lazy_formdata_get_error_report(pub, variable_test_data):
    context = pub.substitutions.get_context_variables(mode='lazy')
    del context['form']  # remove actual form from context
    LoggedError.wipe()
    tmpl = Template('{{forms|objects:"foobarlazy"|first|get:0}}')
    assert tmpl.render(context) == 'None'
    assert LoggedError.count() == 1
    logged_error = LoggedError.select()[0]
    assert logged_error.summary == '|get called with invalid key (0)'


def test_lazy_formdata_queryset_order_by(pub, variable_test_data):
    lazy_formdata = variable_test_data
    data_class = lazy_formdata._formdef.data_class()
    for i in range(6):
        formdata = data_class()
        formdata.data = {
            '0': 'foo%s' % i,
            '1': True,
            'bo1': 'plop1',
            '10': '3',
            '3': datetime.date(2019, 7, 2 + i).timetuple(),
        }
        formdata.just_created()
        formdata.store()
    for i in range(4):
        formdata = data_class()
        formdata.data = {
            '0': 'bar%s' % i,
            '1': False,
            '3': datetime.date(2020, 7, 30 - i).timetuple(),
            'bo1': 'plop2',
            '10': '4',
        }
        formdata.just_created()
        formdata.jump_status('finished')
        formdata.store()

    context = pub.substitutions.get_context_variables(mode='lazy')
    tmpl = Template('{% for v in form_objects|order_by:"foo_foo"|getlist:"foo_foo" %}{{ v }},{% endfor %}')
    assert tmpl.render(context) == 'bar,bar0,bar1,bar2,bar3,foo0,foo1,foo2,foo3,foo4,foo5,'

    tmpl = Template('{% for v in form_objects|order_by:"datefield"|getlist:"foo_foo" %}{{ v }},{% endfor %}')
    assert tmpl.render(context) == 'bar,foo0,foo1,foo2,foo3,foo4,foo5,bar3,bar2,bar1,bar0,'

    LoggedError.wipe()
    tmpl = Template(
        '{% for v in form_objects|order_by:"form_var_datefield"|getlist:"foo_foo" %}{{ v }},{% endfor %}'
    )
    assert tmpl.render(context) == ''
    assert LoggedError.count() == 1
    logged_error = LoggedError.select()[0]
    assert logged_error.summary == 'Invalid value "form_var_datefield" for "order_by"'


def test_lazy_formdata_queryset_slice(pub, formdef):
    formdef.fields = [fields.StringField(id='0', label='string', varname='foo')]
    formdef.store()

    data_class = formdef.data_class()
    for i in range(10):
        formdata = data_class()
        formdata.data = {'0': f'foo{i}'}
        formdata.just_created()
        formdata.store()

    values = [f'foo{x}' for x in range(10)]

    context = pub.substitutions.get_context_variables(mode='lazy')
    tmpl = Template('{{ forms|objects:"foobar"|order_by:"id"|getlist:"form_var_foo"|join:" "}}')
    assert tmpl.render(context) == ' '.join(values)

    tmpl = Template('{{ forms|objects:"foobar"|order_by:"id"|slice:":3"|getlist:"form_var_foo"|join:" "}}')
    assert tmpl.render(context) == ' '.join(values[:3])
    tmpl = Template('{{ forms|objects:"foobar"|order_by:"id"|getlist:"form_var_foo"|slice:":3"|join:" "}}')
    assert tmpl.render(context) == ' '.join(values[:3])

    tmpl = Template('{{ forms|objects:"foobar"|order_by:"id"|slice:"1:3"|getlist:"form_var_foo"|join:" "}}')
    assert tmpl.render(context) == ' '.join(values[1:3])
    tmpl = Template('{{ forms|objects:"foobar"|order_by:"id"|getlist:"form_var_foo"|slice:"1:3"|join:" "}}')
    assert tmpl.render(context) == ' '.join(values[1:3])

    tmpl = Template('{{ forms|objects:"foobar"|order_by:"id"|slice:"-2:"|getlist:"form_var_foo"|join:" "}}')
    assert tmpl.render(context) == ' '.join(values[-2:])
    tmpl = Template('{{ forms|objects:"foobar"|order_by:"id"|getlist:"form_var_foo"|slice:"-2:"|join:" "}}')
    assert tmpl.render(context) == ' '.join(values[-2:])

    tmpl = Template('{{ forms|objects:"foobar"|order_by:"id"|slice:":-2"|getlist:"form_var_foo"|join:" "}}')
    assert tmpl.render(context) == ' '.join(values[:-2])
    tmpl = Template('{{ forms|objects:"foobar"|order_by:"id"|getlist:"form_var_foo"|slice:":-2"|join:" "}}')
    assert tmpl.render(context) == ' '.join(values[:-2])

    tmpl = Template('{{ forms|objects:"foobar"|order_by:"id"|slice:"2:-3"|getlist:"form_var_foo"|join:" "}}')
    assert tmpl.render(context) == ' '.join(values[2:-3])
    tmpl = Template('{{ forms|objects:"foobar"|order_by:"id"|getlist:"form_var_foo"|slice:"2:-3"|join:" "}}')
    assert tmpl.render(context) == ' '.join(values[2:-3])

    # multiple slices
    tmpl = Template(
        '{{ forms|objects:"foobar"|order_by:"id"|slice:"2:"|slice:":-3"|getlist:"form_var_foo"|join:" "}}'
    )
    assert tmpl.render(context) == ' '.join(values[2:-3])
    tmpl = Template(
        '{{ forms|objects:"foobar"|order_by:"id"|getlist:"form_var_foo"|slice:"2:"|slice:":-3"|join:" "}}'
    )
    assert tmpl.render(context) == ' '.join(values[2:-3])

    # length
    tmpl = Template('{{ forms|objects:"foobar"|order_by:"id"|slice:"2:-3"|count}}')
    assert tmpl.render(context) == str(len(values[2:-3]))


def test_lazy_global_forms(pub):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'foobarlazy'
    formdef.fields = [
        fields.StringField(id='1', label='Test', varname='foo'),
        fields.StringField(id='2', label='key', varname='key'),
        fields.ItemsField(id='3', label='Items', varname='items'),
    ]
    formdef.store()
    formdef.data_class().wipe()
    for i in range(6):
        formdata = formdef.data_class()()
        formdata.data = {'1': 'bar', '2': str(i), '3': [str(i % 2), str(i % 3)]}
        formdata.just_created()
        formdata.store()
    for i in range(4):
        formdata = formdef.data_class()()
        formdata.data = {'1': 'foo', '2': str(i + 6), '3': [str(i)]}
        formdata.just_created()
        formdata.jump_status('finished')
        formdata.store()

    formdata = formdef.data_class()()
    formdata.data = {'1': 'bar'}
    formdata.status = 'draft'
    formdata.store()

    # template tags
    context = pub.substitutions.get_context_variables(mode='lazy')
    tmpl = Template('{{forms.foobarlazy.slug}}')
    assert tmpl.render(context) == 'foobarlazy'

    pub.custom_view_class.wipe()

    custom_view1 = pub.custom_view_class()
    custom_view1.title = 'datasource form view'
    custom_view1.formdef = formdef
    custom_view1.columns = {'list': [{'id': 'id'}, {'id': 'time'}, {'id': 'status'}]}
    custom_view1.filters = {'filter-1': 'on', 'filter-1-value': 'bar'}
    custom_view1.visibility = 'datasource'
    custom_view1.order_by = '-f2'
    custom_view1.store()

    custom_view2 = pub.custom_view_class()
    custom_view2.title = 'shared form view'
    custom_view2.formdef = formdef
    custom_view2.columns = {'list': [{'id': 'id'}, {'id': 'time'}, {'id': 'status'}]}
    custom_view2.filters = {'filter-1': 'on', 'filter-1-value': 'foo'}
    custom_view2.visibility = 'any'
    custom_view2.order_by = 'f2'
    custom_view2.store()

    custom_view3 = pub.custom_view_class()
    custom_view3.title = 'private form view'
    custom_view3.formdef = formdef
    custom_view3.columns = {'list': [{'id': 'id'}]}
    custom_view3.filters = {}
    custom_view3.visibility = 'owner'
    custom_view3.usier_id = 42
    custom_view3.order_by = 'id'
    custom_view3.store()

    tmpl = Template('{{forms|objects:"foobarlazy"|with_custom_view:"datasource-form-view"|count}}')
    assert tmpl.render(context) == '6'
    tmpl = Template(
        '{% for data in forms|objects:"foobarlazy"|with_custom_view:"datasource-form-view" %}{{ data.internal_id }},{% endfor %}'
    )
    assert tmpl.render(context) == '6,5,4,3,2,1,'

    tmpl = Template('{{forms|objects:"foobarlazy"|with_custom_view:"shared-form-view"|count}}')
    assert tmpl.render(context) == '4'
    tmpl = Template(
        '{% for data in forms|objects:"foobarlazy"|with_custom_view:"shared-form-view" %}{{ data.internal_id }},{% endfor %}'
    )
    assert tmpl.render(context) == '7,8,9,10,'

    LoggedError.wipe()
    tmpl = Template('{{forms|objects:"foobarlazy"|with_custom_view:"private-form-view"|count}}')
    assert tmpl.render(context) == '0'
    assert [x.summary for x in LoggedError.select()] == ['Unknown custom view "private-form-view"']

    LoggedError.wipe()
    tmpl = Template('{{forms|objects:"foobarlazy"|with_custom_view:"unknown"|count}}')
    assert tmpl.render(context) == '0'
    assert [x.summary for x in LoggedError.select()] == ['Unknown custom view "unknown"']

    custom_view4 = pub.custom_view_class()
    custom_view4.title = 'unknown filter'
    custom_view4.formdef = formdef
    custom_view4.columns = {'list': [{'id': 'id'}]}
    custom_view4.filters = {'filter-42': 'on', 'filter-42-value': 'foo'}
    custom_view4.visibility = 'any'
    custom_view4.store()

    LoggedError.wipe()
    tmpl = Template('{{forms|objects:"foobarlazy"|with_custom_view:"unknown-filter"|count}}')
    assert tmpl.render(context) == '0'
    assert LoggedError.count() == 1
    logged_error = LoggedError.select(order_by='id')[0]
    assert logged_error.summary == 'Invalid filter "42".'
    assert logged_error.formdef_id == str(formdef.id)


def test_lazy_variables(pub, variable_test_data):
    formdata = FormDef.select()[0].data_class().select()[0]
    for mode in (None, 'lazy'):
        context = pub.substitutions.get_context_variables(mode=mode)
        assert context['form_number'] == formdata.get_display_id()
        assert context['form_display_name'] == formdata.get_display_name()
        assert context['form_var_foo_foo'] == 'bar'
        with pytest.raises(KeyError):
            # noqa pylint: disable=pointless-statement
            context['form_var_xxx']
        assert 'bar' in context['form_var_foo_foo']
        assert context['form_var_foo_foo'] + 'ab' == 'barab'
        for item in enumerate(context['form_var_foo_foo']):
            assert item in [(0, 'b'), (1, 'a'), (2, 'r')]
        assert context['form_var_foo_foo_baz_baz'] == 'other'
        assert context['form_var_pwd_cleartext'] == 'a'


def test_lazy_variables_missing(pub, variable_test_data):
    formdef = FormDef.select()[0]
    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.data = {
        '0': 'bar',
    }
    pub.substitutions.reset()
    pub.substitutions.feed(formdef)
    pub.substitutions.feed(formdata)
    for mode in (None, 'lazy'):
        context = pub.substitutions.get_context_variables(mode=mode)
        if mode == 'lazy':
            assert isinstance(context['form_var_foo_foo_baz_baz'], NoneFieldVar)
        else:
            assert context['form_var_foo_foo_baz_baz'] is None
        assert context['form_var_foo_foo'] == 'bar'
        with pytest.raises(KeyError):
            assert context['form_var_foo_foo_xxx'] == 'bar'

        tmpl = Template('{{form_var_foo_foo_baz_baz|default_if_none:"XXX"}}')
        assert tmpl.render(context) == 'XXX'


def test_lazy_variables_length(pub, variable_test_data):
    formdef = FormDef.select()[0]
    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.data = {
        '0': 'bar',
        '4': ['aa', 'ac'],
        '4_display': 'aa, ac',
        '6': '3',
    }
    pub.substitutions.reset()
    pub.substitutions.feed(formdef)
    pub.substitutions.feed(formdata)
    assert WorkflowStatusItem.compute('{{ form_var_foo_foo|length }}') == '3'
    assert WorkflowStatusItem.compute('{% if form_var_foo_foo|length_is:3 %}ok{% endif %}') == 'ok'
    assert (
        WorkflowStatusItem.compute(
            '{% if form_var_foo_foo|length_is:form_var_foo_foo_baz_baz %}ok{% endif %}'
        )
        == 'ok'
    )
    assert WorkflowStatusItem.compute('{{ form_var_itemsfield|length }}') == '2'
    assert WorkflowStatusItem.compute('{{ form_var_itemsfield_raw|length }}') == '2'
    del formdata.data['4']
    del formdata.data['4_display']
    assert WorkflowStatusItem.compute('{{ form_var_itemsfield|length }}') == '0'
    assert WorkflowStatusItem.compute('{{ form_var_itemsfield_raw|length }}') == '0'


def test_lazy_map_variable(pub, variable_test_data):
    formdef = FormDef.select()[0]
    formdata = formdef.data_class().select()[0]
    for mode in ('lazy', None, 'lazy'):
        pub.substitutions.reset()
        pub.substitutions.feed(formdef)
        with pub.substitutions.temporary_feed(formdata, force_mode=mode):
            assert WorkflowStatusItem.compute('{{ form_var_map }}', raises=True) == '2;4'
            assert WorkflowStatusItem.compute('{{ form_var_map|split:";"|first }}', raises=True) == '2'
            assert WorkflowStatusItem.compute('{{ form_var_map_lat }}', raises=True) == '2'
            assert WorkflowStatusItem.compute('{{ form_var_map_lon }}', raises=True) == '4'

            assert (
                WorkflowStatusItem.compute(
                    '{{ form_var_map|distance:form_var_map|floatformat }}', raises=True
                )
                == '0'
            )
            assert (
                WorkflowStatusItem.compute('{{ form_var_map|distance:"2.1;4.1"|floatformat }}', raises=True)
                == '15685.4'
            )
            assert (
                WorkflowStatusItem.compute('{{ "2.1;4.1"|distance:form_var_map|floatformat }}', raises=True)
                == '15685.4'
            )
            assert WorkflowStatusItem.compute('{{ form|distance:"1;2"|floatformat }}', raises=True) == '0'
            assert (
                WorkflowStatusItem.compute('{{ form|distance:"1.1;2.1"|floatformat }}', raises=True)
                == '15689.1'
            )
            assert (
                WorkflowStatusItem.compute('{{ "1.1;2.1"|distance:form|floatformat }}', raises=True)
                == '15689.1'
            )
            assert (
                WorkflowStatusItem.compute('{{ form|distance:form_var_map|floatformat }}', raises=True)
                == '248515.5'
            )
            assert (
                WorkflowStatusItem.compute('{{ form_var_map|distance:form|floatformat }}', raises=True)
                == '248515.5'
            )

    formdata.data['7'] = None
    formdata.store()
    pub.substitutions.reset()
    pub.substitutions.feed(formdef)
    pub.substitutions.feed(formdata)
    for mode in (None, 'lazy'):
        pub.substitutions.reset()
        pub.substitutions.feed(formdef)
        with pub.substitutions.temporary_feed(formdata, force_mode=mode):
            assert (
                WorkflowStatusItem.compute('{{ form_var_map|distance:"1;2"|floatformat }}', raises=True) == ''
            )
            assert (
                WorkflowStatusItem.compute('{{ "1;2"|distance:form_var_map|floatformat }}', raises=True) == ''
            )


def test_lazy_default_workflow_option(pub):
    Workflow.wipe()
    FormDef.wipe()

    wf = Workflow(name='variables')
    wf.variables_formdef = WorkflowVariablesFieldsFormDef(workflow=wf)
    wf.variables_formdef.fields = [
        fields.StringField(id='1', label='Test1', varname='foo', default_value='123'),
        fields.StringField(id='2', label='Test2', varname='bar', default_value=None),
        fields.StringField(id='3', label='Test3', varname='baz'),
        fields.NumericField(id='4', label='Test4', varname='num', default_value=123),
    ]
    wf.store()

    formdef = FormDef()
    formdef.name = 'foo'
    formdef.store()
    formdef.workflow_id = wf.id
    formdef.store()

    formdata = formdef.data_class()()
    for mode in (None, 'lazy'):
        pub.substitutions.reset()
        pub.substitutions.feed(formdef)
        with pub.substitutions.temporary_feed(formdata, force_mode=mode):
            assert WorkflowStatusItem.compute('{{ form_option_foo }}') == '123'
            assert WorkflowStatusItem.compute('{{ form_option_bar }}') == 'None'
            assert WorkflowStatusItem.compute('{{ form_option_baz }}') == 'None'
            assert WorkflowStatusItem.compute('{{ form_option_num }}') == '123'

    formdef.workflow_options = {'foo': '234', 'bar': '345'}
    formdef.store()
    formdata = formdef.data_class()()
    for mode in (None, 'lazy'):
        pub.substitutions.reset()
        pub.substitutions.feed(formdef)
        with pub.substitutions.temporary_feed(formdata, force_mode=mode):
            assert WorkflowStatusItem.compute('{{ form_option_foo }}') == '234'
            assert WorkflowStatusItem.compute('{{ form_option_bar }}') == '345'
            assert WorkflowStatusItem.compute('{{ form_option_baz }}') == 'None'


def test_lazy_file_workflow_option(pub):
    Workflow.wipe()
    FormDef.wipe()

    wf = Workflow(name='variables')
    wf.variables_formdef = WorkflowVariablesFieldsFormDef(workflow=wf)
    wf.variables_formdef.fields = [
        fields.StringField(id='1', label='Test', varname='foo'),
        fields.FileField(id='2', label='File Test', varname='bar'),
    ]
    wf.store()

    formdef = FormDef()
    formdef.name = 'foo'
    formdef.store()
    formdef.workflow_id = wf.id
    formdef.workflow_options = {'foo': 'bar', 'bar': PicklableUpload('test.txt', 'text/plain')}
    formdef.workflow_options['bar'].receive([b'hello world'])
    formdef.store()

    formdata = formdef.data_class()()
    substvars = CompatibilityNamesDict()
    substvars.update(formdata.get_substitution_variables())
    assert 'form_option_foo' in substvars.get_flat_keys()
    assert 'form_option_bar' in substvars.get_flat_keys()
    assert 'form_option_bar_url' not in substvars.get_flat_keys()


def test_lazy_strip_method(pub, variable_test_data):
    formdef = FormDef.select()[0]
    formdata = formdef.data_class().select()[0]
    formdata.data['0'] = ' bar '
    for mode in (None, 'lazy'):
        pub.substitutions.reset()
        pub.substitutions.feed(formdef)
        with pub.substitutions.temporary_feed(formdata, force_mode=mode):
            assert WorkflowStatusItem.compute('{{ form_var_foo_foo }}', raises=True) == 'bar'
            assert WorkflowStatusItem.compute('{{ form_var_foo_foo.strip }}', raises=True) == 'bar'


def test_lazy_conditions(pub, variable_test_data):
    condition = Condition({'type': 'django', 'value': 'form_var_foo_foo == "bar"'})
    assert condition.evaluate() is True

    condition = Condition({'type': 'django', 'value': 'form_var_foo_foo|startswith:"ba"'})
    assert condition.evaluate() is True

    condition = Condition({'type': 'django', 'value': 'form_var_foo_foo|startswith:"fo"'})
    assert condition.evaluate() is False

    condition = Condition({'type': 'django', 'value': 'form_var_foo_foo|slice:":2" == "ba"'})
    assert condition.evaluate() is True

    condition = Condition({'type': 'django', 'value': 'form_var_foo_foo|slice:":2" == "fo"'})
    assert condition.evaluate() is False

    condition = Condition({'type': 'django', 'value': 'form.var.foo_foo == "bar"'})
    assert condition.evaluate() is True

    condition = Condition({'type': 'django', 'value': 'form_field_string == "bar"'})
    assert condition.evaluate() is True

    condition = Condition({'type': 'django', 'value': 'form_role_receiver_name == "foobar"'})
    assert condition.evaluate() is True

    condition = Condition({'type': 'django', 'value': 'form_user_email == "bar@localhost"'})
    assert condition.evaluate() is True

    condition = Condition({'type': 'django', 'value': 'form_var_filefield_raw_content_type == "text/plain"'})
    assert condition.evaluate() is True

    condition = Condition({'type': 'django', 'value': 'form_user_admin_access'})
    assert condition.evaluate() is False

    condition = Condition({'type': 'django', 'value': 'form_user_backoffice_access'})
    assert condition.evaluate() is False

    user = pub.user_class.select()[0]
    user.is_admin = True
    user.store()

    condition = Condition({'type': 'django', 'value': 'form_user_admin_access'})
    assert condition.evaluate() is True

    condition = Condition({'type': 'django', 'value': 'form_user_backoffice_access'})
    assert condition.evaluate() is True

    condition = Condition({'type': 'django', 'value': 'form_var_datefield == "2018-07-31"'})
    assert condition.evaluate() is True
    condition = Condition({'type': 'django', 'value': 'form_var_datefield != "2018-07-31"'})
    assert condition.evaluate() is False
    condition = Condition({'type': 'django', 'value': 'form_var_datefield == "31/07/2018"'})
    assert condition.evaluate() is True
    condition = Condition({'type': 'django', 'value': 'form_var_datefield != "31/07/2018"'})
    assert condition.evaluate() is False
    condition = Condition({'type': 'django', 'value': 'form_var_datefield >= "31/07/2018"'})
    assert condition.evaluate() is True
    condition = Condition({'type': 'django', 'value': 'form_var_datefield <= "31/07/2018"'})
    assert condition.evaluate() is True
    condition = Condition({'type': 'django', 'value': 'form_var_datefield > "31/07/2018"'})
    assert condition.evaluate() is False
    condition = Condition({'type': 'django', 'value': 'form_var_datefield < "31/07/2018"'})
    assert condition.evaluate() is False
    condition = Condition({'type': 'django', 'value': '"2018-07-31" == form_var_datefield'})
    assert condition.evaluate() is True
    condition = Condition({'type': 'django', 'value': '"31/07/2018" == form_var_datefield'})
    assert condition.evaluate() is True
    condition = Condition({'type': 'django', 'value': 'form_var_datefield != form_var_datefield2'})
    assert condition.evaluate() is True
    condition = Condition({'type': 'django', 'value': 'form_var_datefield == form_var_datefield2'})
    assert condition.evaluate() is False
    condition = Condition({'type': 'django', 'value': 'form_var_datefield < form_var_datefield2'})
    assert condition.evaluate() is True
    condition = Condition({'type': 'django', 'value': 'form_var_datefield > form_var_datefield2'})
    assert condition.evaluate() is False
    condition = Condition({'type': 'django', 'value': 'form_var_datefield <= form_var_datefield2'})
    assert condition.evaluate() is True
    condition = Condition({'type': 'django', 'value': 'form_var_datefield >= form_var_datefield2'})
    assert condition.evaluate() is False
    # compare with string var representing a date
    condition = Condition({'type': 'django', 'value': 'form_var_datefield == form_var_datestring'})
    assert condition.evaluate() is True
    condition = Condition({'type': 'django', 'value': 'form_var_datestring == "2018-07-31"'})
    assert condition.evaluate() is True
    condition = Condition({'type': 'django', 'value': 'form_var_datestring == "31/07/2018"'})
    assert condition.evaluate() is False  # datestring is not a date !
    # non existing form_var
    condition = Condition({'type': 'django', 'value': 'form_var_datefield == form_var_barbarbar'})
    assert condition.evaluate() is False
    condition = Condition({'type': 'django', 'value': 'not form_var_datefield == form_var_barbarbar'})
    assert condition.evaluate() is True


def test_lazy_conditions_in(pub, variable_test_data):
    condition = Condition({'type': 'django', 'value': 'form_var_foo_foo in "foo, bar, baz"'})
    assert condition.evaluate() is True

    condition = Condition({'type': 'django', 'value': 'form_var_foo_foo not in "foo, bar, baz"'})
    assert condition.evaluate() is False

    condition = Condition({'type': 'django', 'value': '"b" in form_var_foo_foo'})
    assert condition.evaluate() is True

    condition = Condition({'type': 'django', 'value': '"b" not in form_var_foo_foo'})
    assert condition.evaluate() is False


def test_lazy_conditions_rich_comparison(pub, variable_test_data):
    condition = Condition({'type': 'django', 'value': 'form_var_foo_foo < "bar"'})
    assert condition.evaluate() is False

    condition = Condition({'type': 'django', 'value': 'form_var_foo_foo <= "bar"'})
    assert condition.evaluate() is True

    condition = Condition({'type': 'django', 'value': 'form_var_foo_foo >= "bar"'})
    assert condition.evaluate() is True

    condition = Condition({'type': 'django', 'value': 'form_var_foo_foo > "bar"'})
    assert condition.evaluate() is False


def test_has_role_templatefilter(pub, variable_test_data):
    condition = Condition({'type': 'django', 'value': 'form_user|has_role:"foobar"'})
    assert condition.evaluate() is False
    condition = Condition({'type': 'django', 'value': 'form_user|has_role:form_role_receiver_name'})
    assert condition.evaluate() is False

    role = pub.role_class.select()[0]
    user = pub.user_class.select()[0]
    user.roles = [role.id, '42']  # role.id 42 does not exist
    user.store()

    condition = Condition({'type': 'django', 'value': 'form_user|has_role:"foobar"'})
    assert condition.evaluate() is True
    condition = Condition({'type': 'django', 'value': 'form_user|has_role:form_role_receiver_name'})
    assert condition.evaluate() is True
    condition = Condition({'type': 'django', 'value': 'form_user|has_role:"barfoo"'})
    assert condition.evaluate() is False
    condition = Condition({'type': 'django', 'value': 'form_user|has_role:form_var_foo_foo'})
    assert condition.evaluate() is False

    # non-user object
    condition = Condition({'type': 'django', 'value': 'form_var_foo_foo|has_role:"foobar"'})
    assert condition.evaluate() is False
    condition = Condition({'type': 'django', 'value': 'xxx|has_role:"foobar"'})
    assert condition.evaluate() is False


def test_roles_templatefilter(pub, variable_test_data):
    condition = Condition({'type': 'django', 'value': '"foobar" in form_user|roles'})
    assert condition.evaluate() is False
    condition = Condition({'type': 'django', 'value': 'form_role_receiver_name in form_user|roles'})
    assert condition.evaluate() is False

    role = pub.role_class.select()[0]
    user = pub.user_class.select()[0]
    user.roles = [role.id, '42']  # role.id 42 does not exist
    user.store()

    condition = Condition({'type': 'django', 'value': '"foobar" in form_user|roles'})
    assert condition.evaluate() is True
    condition = Condition({'type': 'django', 'value': 'form_role_receiver_name in form_user|roles'})
    assert condition.evaluate() is True
    condition = Condition({'type': 'django', 'value': '"barfoo" in form_user|roles'})
    assert condition.evaluate() is False
    condition = Condition({'type': 'django', 'value': 'form_var_foo_foo in form_user|roles'})
    assert condition.evaluate() is False

    # non-user object
    condition = Condition({'type': 'django', 'value': '"foobar" in form_var_foo_foo|roles'})
    assert condition.evaluate() is False
    condition = Condition({'type': 'django', 'value': '"foobar" in xxx|roles'})
    assert condition.evaluate() is False


@mock.patch('wcs.wscalls.call_webservice')
def test_user_id_for_service_templatefilter(callws, pub, variable_test_data):
    pub.load_site_options()
    if not pub.site_options.has_section('variables'):
        pub.site_options.add_section('variables')
    pub.site_options.set('variables', 'idp_api_url', 'https://authentic.example.org/api/')

    context = pub.substitutions.get_context_variables(mode='lazy')
    callws.return_value = (None, 200, '{"err":0,"data":{"user":{"id":"4242"}}}')

    # OK tests
    tmpl = Template('{{"nameiduser"|user_id_for_service:"slugservice"}}')
    assert tmpl.render(context) == '4242'
    assert callws.call_count == 1
    assert callws.call_args[0][0] == 'https://authentic.example.org/api/users/nameiduser/service/slugservice/'
    assert callws.call_args[1]['method'] == 'GET'
    assert callws.call_args[1]['timeout'] == 5

    tmpl = Template('{{form_user|user_id_for_service:"slugservice"}}')
    assert tmpl.render(context) == '4242'
    assert callws.call_count == 2
    assert callws.call_args[0][0] == 'https://authentic.example.org/api/users/..../service/slugservice/'

    # KO tests
    callws.reset_mock()

    callws.return_value = (None, 200, '{"err":0,"data":{"user":{"id":"4343"}}}')
    tmpl = Template('{{""|user_id_for_service:"slugservice"}}')  # empty nameid
    assert tmpl.render(context) == ''
    assert callws.call_count == 0

    callws.return_value = (None, 200, '{"err":0,"data":{"user":{"id":"4343"}}}')
    tmpl = Template('{{42|user_id_for_service:"slugservice"}}')  # not a user object
    assert tmpl.render(context) == ''
    assert callws.call_count == 0

    callws.return_value = (None, 404, '{"err":1}')  # 404 from authentic
    tmpl = Template('{{"nameid"|user_id_for_service:"slug"}}')
    assert tmpl.render(context) == ''
    assert callws.call_count == 1
    assert callws.call_args[0][0] == 'https://authentic.example.org/api/users/nameid/service/slug/'

    callws.return_value = (None, 200, '<html>crash</html>')  # non-JSON response
    tmpl = Template('{{"nameid"|user_id_for_service:"slug"}}')
    assert tmpl.render(context) == ''
    assert callws.call_count == 2
    assert callws.call_args[0][0] == 'https://authentic.example.org/api/users/nameid/service/slug/'

    callws.return_value = (None, 200, '{"err":0,"data":{}}')  # empty response
    tmpl = Template('{{"nameid"|user_id_for_service:"slug"}}')
    assert tmpl.render(context) == ''
    assert callws.call_count == 3
    assert callws.call_args[0][0] == 'https://authentic.example.org/api/users/nameid/service/slug/'

    pub.site_options.set('variables', 'idp_api_url', '')  # no API available
    callws.reset_mock()
    callws.return_value = (None, 200, '{"err":0,"data":{"user":{"id":"4242"}}}')
    tmpl = Template('{{"nameid"|user_id_for_service:"slug"}}')
    assert tmpl.render(context) == ''
    assert callws.call_count == 0


def test_function_roles(pub):
    formdef = FormDef()
    formdef.name = 'foobar'
    formdef.url_name = 'foobar'
    formdef.fields = []
    formdef.store()

    pub.role_class.wipe()
    role = pub.role_class('test role')
    role.store()
    role2 = pub.role_class('second role')
    role2.store()

    formdata = formdef.data_class()()
    formdata.workflow_roles = {'_receiver': [str(role.id), str(role2.id)]}
    static_vars = formdata.get_static_substitution_variables()
    assert static_vars['form_role_receiver_name'] == 'test role, second role'
    assert static_vars['form_role_receiver_names'] == ['test role', 'second role']
    assert static_vars['form_role_receiver_role_slugs'] == ['test-role', 'second-role']

    # missing role id; ignored
    formdata.workflow_roles = {'_receiver': ['12345', str(role2.id)]}
    static_vars = formdata.get_static_substitution_variables()
    assert static_vars['form_role_receiver_name'] == 'second role'
    assert static_vars['form_role_receiver_names'] == ['second role']
    assert static_vars['form_role_receiver_role_slugs'] == ['second-role']


def test_function_users(pub):
    user1 = pub.user_class(name='userA')
    user1.store()
    user2 = pub.user_class(name='userB')
    user2.store()

    formdef = FormDef()
    formdef.name = 'foobar'
    formdef.url_name = 'foobar'
    formdef.fields = []
    formdef.store()

    formdata = formdef.data_class()()
    formdata.workflow_roles = {'_receiver': '_user:%s' % user1.id}
    formdata.store()
    pub.substitutions.feed(formdata)
    condition = Condition({'type': 'django', 'value': 'form_role_receiver_name == "userA"'})
    assert condition.evaluate() is True
    static_vars = formdata.get_static_substitution_variables()
    assert static_vars['form_role_receiver_name'] == 'userA'

    formdata.workflow_roles = {'_receiver': ['_user:%s' % user1.id, '_user:%s' % user2.id]}
    formdata.store()
    pub.substitutions.feed(formdata)
    condition = Condition({'type': 'django', 'value': 'form_role_receiver_name == "userA, userB"'})
    assert condition.evaluate() is True
    static_vars = formdata.get_static_substitution_variables()
    assert static_vars['form_role_receiver_name'] == 'userA, userB'
    assert set(static_vars['form_role_receiver_names']) == {'userA', 'userB'}

    # combined roles and users
    pub.role_class.wipe()
    role = pub.role_class('test combined')
    role.store()
    role2 = pub.role_class('second role')
    role2.store()
    formdata.workflow_roles = {
        '_receiver': [str(role.id), str(role2.id), '_user:%s' % user1.id, '_user:%s' % user2.id]
    }
    formdata.store()
    pub.substitutions.feed(formdata)
    condition = Condition(
        {'type': 'django', 'value': 'form_role_receiver_name == "test combined, second role, userA, userB"'}
    )
    assert condition.evaluate() is True
    static_vars = formdata.get_static_substitution_variables()
    assert static_vars['form_role_receiver_name'] == 'test combined, second role, userA, userB'
    assert set(static_vars['form_role_receiver_names']) == {'test combined', 'second role', 'userA', 'userB'}
    assert static_vars['form_role_receiver_role_slugs'] == ['test-combined', 'second-role']

    formdata.workflow_roles = {'_receiver': ['_user:%s' % user1.id, '_user:%s' % user2.id, str(role.id)]}
    formdata.store()
    pub.substitutions.feed(formdata)
    condition = Condition(
        {'type': 'django', 'value': 'form_role_receiver_name == "userA, userB, test combined"'}
    )
    assert condition.evaluate() is True
    static_vars = formdata.get_static_substitution_variables()
    assert static_vars['form_role_receiver_name'] == 'userA, userB, test combined'

    # missing role id; ignored
    formdata.workflow_roles = {
        '_receiver': ['12345', str(role2.id), '_user:%s' % user1.id, '_user:%s' % user2.id]
    }
    formdata.store()
    static_vars = formdata.get_static_substitution_variables()
    assert static_vars['form_role_receiver_name'] == 'second role, userA, userB'

    # missing user id; ignored
    formdata.workflow_roles = {'_receiver': ['12345', '_user:%s' % user1.id, '_user:12345']}
    formdata.store()
    static_vars = formdata.get_static_substitution_variables()
    assert static_vars['form_role_receiver_name'] == 'userA'


def test_lazy_now_and_today(pub, variable_test_data):
    for condition_value in (
        'now > "1970-01-01"',
        '"1970-01-01" < now',
        'now < "2100-01-01"',
        '"2100-01-01" > now',
        'not now < "1970-01-01"',
        'not "1970-01-01" > now',
        'not now > "2100-01-01"',
        'not "2100-01-01" < now',
        # form_var_datefield is in 2018, we hope now is after 2019
        'form_var_datefield < now',
        'not form_var_datefield > now',
        'form_var_datefield <= now',
        'not form_var_datefield >= now',
        'form_var_datefield != now',
        'not form_var_datefield == now',
    ):
        condition = Condition({'type': 'django', 'value': condition_value})
        assert condition.evaluate() is True
        condition = Condition({'type': 'django', 'value': condition_value.replace('now', 'today')})
        assert condition.evaluate() is True


def test_lazy_date_templatetags(pub, variable_test_data, freezer):
    for condition_value in (
        '"2017-10-10"|date == "2017-10-10"',
        '"2017-10-10"|date == "2017-10-10 00:00"',
        '"2017-10-10 00:00"|date == "2017-10-10 00:00"',
        '"2017-10-10"|date == "10/10/2017"',
        '"2017-10-10"|date == "10/10/2017 00:00"',
        '"2017-10-10"|date == "10/10/2017 00h00"',
        'not "2017-10-10"|date == "11/10/2017"',
        '"2017-10-10"|date != "11/10/2017"',
        'not "2017-10-10"|date != "10/10/2017"',
        '"2017-10-10"|date > "09/10/2017"',
        'not "2017-10-10"|date > "10/10/2017"',
        '"2017-10-10"|date < "11/10/2017"',
        'not "2017-10-10"|date < "10/10/2017"',
        '"2017-10-10"|date >= "09/10/2017"',
        '"2017-10-10"|date <= "10/10/2017"',
        '"2017-10-10"|date >= "10/10/2017"',
    ):
        condition = Condition({'type': 'django', 'value': condition_value})
        assert condition.evaluate() is True
        condition = Condition({'type': 'django', 'value': condition_value.replace('date', 'datetime')})
        assert condition.evaluate() is True
        # |date on the right member
        condition = Condition({'type': 'django', 'value': condition_value.replace('|date', '') + '|date'})
        assert condition.evaluate() is True
        condition = Condition({'type': 'django', 'value': condition_value.replace('|date', '') + '|datetime'})
        assert condition.evaluate() is True

    for condition_value in (
        '"2017-10-11 12:13"|datetime == "2017-10-11 12:13"',
        '"2017-10-11 12:13:14"|datetime == "2017-10-11 12:13:14"',
        '"2017-10-11 12:13"|datetime != "2017-10-11 12:13:14"',
        'not "2017-10-11 12:13"|datetime == "2017-10-11 12:13:14"',
        '"2017-10-11 12:13"|datetime < "2017-10-11 12:13:14"',
        '"2017-10-11 12:13"|datetime <= "2017-10-11 12:13:14"',
        '"2017-10-11 12:13:14"|datetime <= "2017-10-11 12:13:14"',
        '"2017-10-11 12:13:14"|datetime > "2017-10-11 12:13"',
        '"2017-10-11 12:13:14"|datetime >= "2017-10-11 12:13"',
        '"2017-10-11 12:13:14"|datetime >= "2017-10-11 12:13:14"',
    ):
        condition = Condition({'type': 'django', 'value': condition_value})
        assert condition.evaluate() is True
        # |datetime on the right member
        condition = Condition(
            {'type': 'django', 'value': condition_value.replace('|datetime', '') + '|datetime'}
        )
        assert condition.evaluate() is True

    condition = Condition({'type': 'django', 'value': '"2017-10-11 00:00"|datetime == "2017-10-11"|date'})
    assert condition.evaluate() is True
    condition = Condition({'type': 'django', 'value': '"2017-10-11 12:13"|date == "2017-10-11"'})
    assert condition.evaluate() is True
    condition = Condition(
        {'type': 'django', 'value': '"2017-10-11 12:13"|date == "2017-10-11 00:00"|datetime'}
    )
    assert condition.evaluate() is True
    condition = Condition({'type': 'django', 'value': '"2017-10-11 12:13"|date == "2017-10-11 14:15"|date'})
    assert condition.evaluate() is True
    condition = Condition({'type': 'django', 'value': '"2017-10-11 01:00"|datetime|date == "2017-10-11"'})
    assert condition.evaluate() is True
    condition = Condition(
        {'type': 'django', 'value': '"2018-09-30T00:00:00.000+02:00"|datetime|date == "2018-09-30"'}
    )
    assert condition.evaluate() is True
    condition = Condition(
        {'type': 'django', 'value': '"2018-09-30T00:00:00+02:00"|datetime|date == "2018-09-30"'}
    )
    assert condition.evaluate() is True
    condition = Condition(
        {'type': 'django', 'value': '"2018-09-30T00:00:00.000"|datetime|date == "2018-09-30"'}
    )
    assert condition.evaluate() is True

    condition = Condition({'type': 'django', 'value': 'now|date == today'})
    assert condition.evaluate() is True
    condition = Condition({'type': 'django', 'value': 'today == now|date'})
    assert condition.evaluate() is True

    freezer.move_to('2024-08-06')
    pub.substitutions.invalidate_cache()
    condition = Condition({'type': 'django', 'value': 'now|datetime:"O" == "+0200"'})
    assert condition.evaluate() is True


def test_lazy_date_with_maths(pub, variable_test_data):
    # form_var_datefield  : 2018-07-31
    # form_var_datefield2 : 2018-08-31
    # form_var_datestring : "2018-07-31"
    for condition_value in (
        'form_var_datefield|add_days:0 <= "2019-01-01"',
        'form_var_datefield|add_days:0 >= "1980-01-01"',
        'form_var_datefield|add_days:0 == "2018-07-31"',
        'form_var_datefield|add_days:0 == "31/07/2018"',
        'form_var_datefield|add_days:5 == "2018-08-05"',
        'form_var_datefield|add_days:5 <= "2018-08-05"',
        'form_var_datefield|add_days:5 >= "2018-08-05"',
        'form_var_datefield|add_days:-5 == "2018-07-26"',
        'form_var_datefield|add_days:36500 > "2100-01-01"',  # 2118
        'form_var_datefield|add_days:-36500 < "1950-01-01"',  # 1918
        'form_var_datefield|add_days:31 == form_var_datefield2',
        'form_var_datefield|add_days:5|add_days:-5 == form_var_datestring',
        'form_var_datestring|add_days:31 == form_var_datefield2',
        'form_var_datestring|add_days:32 > form_var_datefield2',
        'form_var_datestring|add_days:30 < form_var_datefield2',
    ):
        condition = Condition({'type': 'django', 'value': condition_value})
        assert condition.evaluate() is True

    # form_var_datefield is in 2018, we hope today/now is after 2019
    for condition_value in (
        'form_var_datefield <= today',
        '"1970-01-01"|add_days:0 < today',
        '"2100-12-31"|add_days:0 > today',
        'form_var_datefield|add_days:0 <= today',
        'form_var_datefield|add_days:10 < today',
        'form_var_datefield|add_days:36500 > today',
        'form_var_datefield|add_days:36500 >= today',
        'form_var_datefield <= today|add_days:0',
        'form_var_datefield < today|add_days:-10',
        'form_var_datefield > today|add_days:-36500',
        'form_var_datefield >= today|add_days:-36500',
    ):
        condition = Condition({'type': 'django', 'value': condition_value})
        assert condition.evaluate() is True
        condition = Condition({'type': 'django', 'value': condition_value.replace('today', 'now')})
        assert condition.evaluate() is True

    for condition_value in (
        '"1970-01-01"|add_hours:0 < today',
        '"2100-12-31"|add_hours:0 > today',
        'form_var_datefield|add_hours:0 <= today',
        'form_var_datefield|add_hours:10 < today',
        'form_var_datefield|add_hours:876000 > today',  # + 100 years
        'form_var_datefield|add_hours:876000 >= today',
    ):
        condition = Condition({'type': 'django', 'value': condition_value})
        assert condition.evaluate() is True
        condition = Condition({'type': 'django', 'value': condition_value.replace('today', 'now')})
        assert condition.evaluate() is True

    for condition_value in (
        '"1970-01-01"|add_minutes:0 < today',
        '"2100-12-31"|add_minutes:0 > today',
        'form_var_datefield|add_minutes:0 <= today',
        'form_var_datefield|add_minutes:10 < today',
        'form_var_datefield|add_minutes:21024000 > today',  # + 100 years
        'form_var_datefield|add_minutes:21024000 >= today',
    ):
        condition = Condition({'type': 'django', 'value': condition_value})
        assert condition.evaluate() is True
        condition = Condition({'type': 'django', 'value': condition_value.replace('today', 'now')})
        assert condition.evaluate() is True

    for condition_value in (
        'today|add_days:0 == today',
        'today|add_hours:0 == today',
        'today|add_minutes:0 == today',
        'now|add_hours:0 == now',
        'now|add_minutes:0 == now',
    ):
        condition = Condition({'type': 'django', 'value': condition_value})
        assert condition.evaluate() is True

    for condition_value in (
        'today|add_days:2 >= today',
        'today|add_days:2 > today',
        'today|add_days:2 != today',
        'not today|add_days:2 == today',
        'not today|add_days:2 <= today',
        'not today|add_days:2 < today',
        'today|add_days:-2 <= today',
        'today|add_days:-2 < today',
        'today|add_days:-2 != today',
        'not today|add_days:-2 >= today',
        'not today|add_days:-2 > today',
        'not today|add_days:-2 == today',
    ):
        condition = Condition({'type': 'django', 'value': condition_value})
        assert condition.evaluate() is True
        condition = Condition({'type': 'django', 'value': condition_value.replace('today', 'now')})
        assert condition.evaluate() is True


def test_lazy_templates(pub, variable_test_data):
    context = pub.substitutions.get_context_variables(mode='lazy')
    tmpl = Template('{{form_var_foo_foo}}')
    assert tmpl.render(context) == 'bar'

    tmpl = Template('[form_var_foo_foo]')
    assert tmpl.render(context) == 'bar'

    tmpl = Template('[form_name]')
    assert tmpl.render(context) == 'foobarlazy'

    tmpl = Template('[form_user_email]')
    assert tmpl.render(context) == 'bar@localhost'

    tmpl = Template('{{form_user_name_identifier_0}}')
    assert tmpl.render(context) == pub.user_class.select()[0].name_identifiers[0]

    tmpl = Template('{% if form_user_email == "bar@localhost" %}HELLO{% endif %}')
    assert tmpl.render(context) == 'HELLO'


def test_lazy_ezt_templates(pub, variable_test_data):
    context = pub.substitutions.get_context_variables(mode='lazy')
    tmpl = Template('[form_var_foo_foo]')
    assert tmpl.render(context) == 'bar'

    tmpl = Template('[is form_var_foo_foo "bar"]HELLO[else]BYE[end]')
    assert tmpl.render(context) == 'HELLO'

    tmpl = Template('[form_user_name_identifier_0]')
    assert tmpl.render(context) == pub.user_class.select()[0].name_identifiers[0]

    tmpl = Template('[form_var_itemfield]')
    assert tmpl.render(context) == ''


def test_lazy_formdata_fields(pub):
    formdef = FormDef()
    formdef.name = 'foobar'
    formdef.url_name = 'foobar'
    formdef.fields = [
        fields.StringField(id='0', label='string', varname='string'),
        fields.ItemField(id='1', label='item', varname='item', items=['Foo', 'Bar']),
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {'0': 'Foo', '1': 'Foo', '1_display': 'Foo'}
    formdata.store()

    pub.substitutions.feed(formdata)
    context = pub.substitutions.get_context_variables(mode='lazy')

    tmpl = Template('{% if form_var_string == "Foo" %}HELLO{% endif %}')
    assert tmpl.render(context) == 'HELLO'
    tmpl = Template('{% if form_var_item == "Foo" %}HELLO{% endif %}')
    assert tmpl.render(context) == 'HELLO'

    tmpl = Template('{% if form_var_string != "Foo" %}HELLO{% endif %}')
    assert tmpl.render(context) == ''
    tmpl = Template('{% if form_var_item != "Foo" %}HELLO{% endif %}')
    assert tmpl.render(context) == ''


def test_date_conditions_django(pub, variable_test_data):
    for condition_value in (  # hope date is > 2018
        # age_in_days
        '"1970-01-01"|age_in_days > 0',
        '"01/01/1970"|age_in_days > 0',
        '"2500-01-01"|age_in_days < 0',
        '"01/01/2500"|age_in_days < 0',
        'form_var_datefield|age_in_days > 50',
        'form_var_datefield|age_in_days:form_var_datestring == 0',
        'form_var_datefield|age_in_days:form_var_datefield2 == 31',
        'form_var_datefield2|age_in_days:form_var_datefield == -31',
        'form_var_datefield|age_in_days:form_var_datefield == 0',
        'form_var_datestring|age_in_days:form_var_datefield == 0',
        'form_var_datestring|age_in_days:form_var_datestring == 0',
        'today|add_days:-5|age_in_days == 5',
        'today|add_days:5|age_in_days == -5',
        'today|age_in_days == 0',
        # with datetimes
        '"1970-01-01 02:03"|age_in_days > 0',
        '"01/01/1970 02h03"|age_in_days > 0',
        '"2500-01-01 02:03"|age_in_days < 0',
        '"01/01/2500 02h03"|age_in_days < 0',
        'now|age_in_days == 0',
        'now|add_hours:-24|age_in_days == 1',
        'now|add_hours:24|age_in_days == -1',
        '"2010-11-12 13:14"|age_in_days:"2010-11-12 13:14" == 0',
        '"2010-11-12 13:14"|age_in_days:"2010-11-12 12:14" == 0',
        '"2010-11-12 13:14"|age_in_days:"2010-11-12 14:14" == 0',
        '"2010-11-12 13:14"|age_in_days:"2010-11-13 13:13" == 1',
        '"2010-11-12 13:14"|age_in_days:"2010-11-13 13:15" == 1',
        # age_in_hours
        'now|add_hours:-5|age_in_hours == 5',
        'now|add_hours:25|age_in_hours == -24',
        'now|age_in_hours == 0',
        '"2010-11-12 13:14"|age_in_hours:"2010-11-12 13:14" == 0',
        '"2010-11-12 13:14"|age_in_hours:"2010-11-12 12:14" == -1',
        '"2010-11-12 13:14"|age_in_hours:"2010-11-12 14:14" == 1',
        '"2010-11-12 13:14"|age_in_hours:"2010-11-13 13:13" == 23',
        '"2010-11-12 13:14"|age_in_hours:"2010-11-13 13:15" == 24',
        '"1970-01-01 02:03"|age_in_hours > 0',
        '"01/01/1970 02h03"|age_in_hours > 0',
        '"2500-01-01 02:03"|age_in_hours < 0',
        '"01/01/2500 02h03"|age_in_hours < 0',
        # with dates
        '"1970-01-01"|age_in_hours > 0',
        '"01/01/1970"|age_in_hours > 0',
        '"2500-01-01"|age_in_hours < 0',
        '"01/01/2500"|age_in_hours < 0',
        'form_var_datefield|age_in_hours > 1200',
        'form_var_datefield|age_in_hours:form_var_datestring == 0',
        'form_var_datefield|age_in_hours:form_var_datefield2 == 744',  # 31*24
        'form_var_datefield2|age_in_hours:form_var_datefield == -744',
        'form_var_datefield|age_in_hours:form_var_datefield == 0',
        'form_var_datestring|age_in_hours:form_var_datefield == 0',
        'form_var_datestring|age_in_hours:form_var_datestring == 0',
        'today|add_days:-1|age_in_hours >= 24',
        'today|add_days:1|age_in_hours <= -0',
        'today|add_days:1|age_in_hours >= -24',
        'today|age_in_hours >= 0',
        # age_in_years
        '"1970-01-01"|age_in_years > 0',
        '"01/01/1970"|age_in_years > 0',
        '"2500-01-01"|age_in_years < 0',
        '"01/01/2500"|age_in_years < 0',
        'form_var_datefield|age_in_years:"2019-07-31" == 1',
        'form_var_datefield|age_in_years:"2019-09-20" == 1',
        'form_var_datefield|age_in_years:"2020-07-30" == 1',
        'form_var_datefield|age_in_years:"2020-07-31" == 2',
        'form_var_datestring|age_in_years:"2019-07-31" == 1',
        'today|age_in_years == 0',
        'today|add_days:-500|age_in_years == 1',
        'today|add_days:-300|age_in_years == 0',
        'today|add_days:300|age_in_years == -1',
        'now|age_in_years == 0',
        'now|add_days:-500|age_in_years == 1',
        'now|add_days:-300|age_in_years == 0',
        'now|add_days:300|age_in_years == -1',
        '"1970-01-01 02:03"|age_in_years > 0',
        '"2500-01-01 02:03"|age_in_years < 0',
        # age_in_months
        'form_var_datefield|age_in_months:form_var_datefield2 == 1',
        'form_var_datefield2|age_in_months:form_var_datefield == -1',
        'form_var_datefield|age_in_months:"2019-07-31" == 12',
        'form_var_datefield|age_in_months:"2019-08-20" == 12',
        'form_var_datefield|age_in_months:"2019-09-20" == 13',
        'form_var_datestring|age_in_months:"2019-09-20" == 13',
        '"1970-01-01"|age_in_months > 0',
        '"01/01/1970"|age_in_months > 0',
        '"2500-01-01"|age_in_months < 0',
        '"01/01/2500"|age_in_months < 0',
        '"1970-01-01 02:03"|age_in_months > 0',
        '"2500-01-01 02:03"|age_in_months < 0',
        # fail produce empty string
        'foobar|age_in_days == ""',
        '"foobar"|age_in_days == ""',
        '"1970-01-01"|age_in_days:"foobar" == ""',
        'foobar|age_in_hours == ""',
        '"foobar"|age_in_hours == ""',
        '"1970-01-01"|age_in_hours:"foobar" == ""',
        'foobar|age_in_years == ""',
        '"foobar"|age_in_years == ""',
        '"1970-01-01"|age_in_years:"foobar" == ""',
        'foobar|age_in_months == ""',
        '"foobar"|age_in_months == ""',
        '"1970-01-01"|age_in_months:"foobar" == ""',
    ):
        condition = Condition({'type': 'django', 'value': condition_value})
        assert condition.evaluate() is True


def test_form_digest_date(pub):
    formdef = FormDef()
    formdef.name = 'foobar'
    formdef.url_name = 'foobar'
    formdef.fields = [fields.DateField(id='0', label='date', varname='date')]
    formdef.digest_templates = {'default': 'plop {{ form_var_date }} plop'}
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {'0': time.strptime('2015-05-12', '%Y-%m-%d')}
    formdata.store()
    assert formdef.data_class().get(formdata.id).digests['default'] == 'plop 2015-05-12 plop'

    with pub.with_language('fr'):
        formdata = formdef.data_class()()
        formdata.data = {'0': time.strptime('2015-05-12', '%Y-%m-%d')}
        formdata.store()
        assert formdef.data_class().get(formdata.id).digests['default'] == 'plop 12/05/2015 plop'

    formdef.digest_templates = {'default': 'plop {{ form_var_date|date:"Y" }} plop'}
    formdef.store()
    formdata = formdef.data_class()()
    formdata.data = {'0': time.strptime('2015-05-12', '%Y-%m-%d')}
    formdata.store()
    assert formdef.data_class().get(formdata.id).digests['default'] == 'plop 2015 plop'

    formdef.digest_templates = {'default': 'plop {{ form_var_date_raw|date:"Y" }} plop'}
    formdef.store()
    formdata = formdef.data_class()()
    formdata.data = {'0': time.strptime('2015-05-12', '%Y-%m-%d')}
    formdata.store()
    assert formdef.data_class().get(formdata.id).digests['default'] == 'plop 2015 plop'

    formdef.digest_templates = {'default': 'plop {{ form_var_date|date:"Y" }} plop'}
    formdef.store()
    formdata = formdef.data_class()()
    formdata.data = {'0': None}
    formdata.store()
    assert formdef.data_class().get(formdata.id).digests['default'] == 'plop  plop'

    # check there's no crash when an invalid variable is given
    formdef.digest_templates = {'default': 'plop {{ blah|date:"Y" }} plop'}
    formdef.store()
    formdata = formdef.data_class()()
    formdata.data = {'0': time.strptime('2015-05-12', '%Y-%m-%d')}
    formdata.store()
    assert formdef.data_class().get(formdata.id).digests['default'] == 'plop  plop'


def test_form_digest_error(pub):
    FormDef.wipe()
    pub.custom_view_class.wipe()

    formdef = FormDef()
    formdef.name = 'foobar'
    formdef.url_name = 'foobar'
    formdef.fields = [fields.DateField(id='0', label='date', varname='date')]
    formdef.digest_templates = {'default': 'plop {{ form_var_date|reproj:"coin"}} plop'}
    formdef.store()

    formdata = formdef.data_class()()
    formdata.store()
    assert formdef.data_class().get(formdata.id).digests['default'] == 'ERROR'

    assert LoggedError.count() == 1
    logged_error = LoggedError.select()[0]
    assert logged_error.summary.startswith('Could not render digest (default)')
    assert logged_error.formdata_id == str(formdata.id)

    formdef.digest_templates = {
        'default': 'plop plop',
        'custom-view:foobar': 'plop {{ form_var_date|reproj:"coin" }} plop',
    }
    formdef.store()

    formdata = formdef.data_class()()
    formdata.store()
    assert formdef.data_class().get(formdata.id).digests['custom-view:foobar'] == 'ERROR'

    assert LoggedError.count() == 2
    logged_error = LoggedError.select(order_by='id')[1]
    assert logged_error.summary.startswith('Could not render digest (custom view "foobar")')
    assert logged_error.formdata_id == str(formdata.id)


def test_lazy_formdata_decimal_filter(pub):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'foobar'
    formdef.url_name = 'foobar'
    formdef.fields = [
        fields.StringField(id='0', label='value', varname='value'),
        fields.StringField(id='1', label='arg', varname='arg'),
    ]
    formdef.store()
    formdata = formdef.data_class()()
    formdata.data = {'0': '3.14', '1': '3'}
    formdata.store()
    pub.substitutions.feed(formdata)
    for mode in (None, 'lazy'):
        context = pub.substitutions.get_context_variables(mode=mode)

        tmpl = Template('{{ form_var_value|decimal }}')
        assert tmpl.render(context) == '3.14'

        tmpl = Template('{{ form_var_value|decimal:1 }}')
        assert tmpl.render(context) == '3.1'

        tmpl = Template('{{ form_var_value|decimal:form_var_arg }}')
        assert tmpl.render(context) == '3.140'

        tmpl = Template('{{ 4.12|decimal:form_var_arg }}')
        assert tmpl.render(context) == '4.120'


def test_lazy_formdata_timesince_filter(pub):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'foobar'
    formdef.url_name = 'foobar'
    formdef.fields = [
        fields.DateField(id='0', label='value', varname='value'),
    ]
    formdef.store()
    formdata = formdef.data_class()()
    formdata.data = {'0': time.strptime('2015-05-12', '%Y-%m-%d')}
    formdata.store()
    pub.substitutions.feed(formdata)
    for mode in (None, 'lazy'):
        context = pub.substitutions.get_context_variables(mode=mode)
        context['refdate'] = datetime.date(2015, 5, 22)

        tmpl = Template('{{ form_var_value|date|timesince:refdate }}')
        assert force_str(tmpl.render(context)) == '1 week, 3 days'

    # in lazy mode it's not even necessary to add the |date filter.
    context = pub.substitutions.get_context_variables(mode='lazy')
    context['refdate'] = datetime.date(2015, 5, 22)
    tmpl = Template('{{ form_var_value|timesince:refdate }}')
    assert force_str(tmpl.render(context)) == '1 week, 3 days'


def test_decimal_conditions_django(pub, variable_test_data):
    for condition_value in (
        'form_var_foo_foo|decimal == 0',
        'form_var_boolfield|decimal == 0',
        'form_var_boolfield2|decimal == 0',
        'form_var_datefield|decimal == 0',
        'form_var_datefield|decimal == 0',
        'form_var_filefield|decimal == 0',
        'form_var_foo_foo_baz_baz|decimal == 0',
        'form_var_map|decimal == 0',
        'form_var_datefield2|decimal == 0',
        'form_var_datestring|decimal == 0',
        'form_var_term1|decimal == 3',
        'form_var_term2|decimal == 4',
    ):
        condition = Condition({'type': 'django', 'value': condition_value})
        assert condition.evaluate() is True


def test_lazy_formdata_mathematics_filters(pub):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'foobar'
    formdef.url_name = 'foobar'
    formdef.fields = [
        fields.StringField(id='0', label='term1', varname='term1'),
        fields.StringField(id='1', label='term2', varname='term2'),
    ]
    formdef.store()
    formdata = formdef.data_class()()
    formdata.data = {'0': '3', '1': '4'}
    formdata.store()
    pub.substitutions.feed(formdata)
    for mode in (None, 'lazy'):
        context = pub.substitutions.get_context_variables(mode=mode)

        tmpl = Template('{{ form_var_term1|decimal }}')
        assert tmpl.render(context) == '3'

        tmpl = Template('{{ form_var_term1|add:form_var_term2 }}')
        assert tmpl.render(context) == '7'

        tmpl = Template('{{ form_var_term1|subtract:form_var_term2 }}')
        assert tmpl.render(context) == '-1'

        tmpl = Template('{{ form_var_term1|multiply:form_var_term2 }}')
        assert tmpl.render(context) == '12'

        tmpl = Template('{{ form_var_term1|divide:form_var_term2 }}')
        assert tmpl.render(context) == '0.75'


def test_lazy_formdata_add_filters(pub):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'foobar'
    formdef.url_name = 'foobar'
    formdef.fields = [
        fields.StringField(id='0', label='term0', varname='term0'),
        fields.StringField(id='1', label='term1', varname='term1'),
        fields.StringField(id='2', label='term2', varname='term2'),
        fields.StringField(id='3', label='term3', varname='term3'),
        fields.StringField(id='4', label='term4', varname='term4'),
    ]
    formdef.store()
    formdata = formdef.data_class()()
    formdata.data = {'0': 'foo', '1': '3,14', '2': '', '3': None}
    formdata.store()
    pub.substitutions.feed(formdata)
    for mode in (None, 'lazy'):
        context = pub.substitutions.get_context_variables(mode=mode)

        # add
        tmpl = Template('{{ form_var_term1|add:form_var_term1 }}')
        assert tmpl.render(context) == '6.28'

        tmpl = Template('{{ form_var_term1|add:form_var_term2 }}')
        assert tmpl.render(context) == '3.14'

        tmpl = Template('{{ form_var_term1|add:form_var_term3 }}')
        assert tmpl.render(context) == '3.14'

        tmpl = Template('{{ form_var_term1|add:form_var_term4 }}')
        assert tmpl.render(context) == '3.14'

        tmpl = Template('{{ form_var_term2|add:form_var_term1 }}')
        assert tmpl.render(context) == '3.14'

        tmpl = Template('{{ form_var_term3|add:form_var_term1 }}')
        assert tmpl.render(context) == '3.14'

        tmpl = Template('{{ form_var_term4|add:form_var_term1 }}')
        assert tmpl.render(context) == '3.14'

        # fallback to Django native add filter
        tmpl = Template('{{ form_var_term0|add:form_var_term0 }}')
        assert tmpl.render(context) == 'foofoo'

        tmpl = Template('{{ form_var_term0|add:form_var_term1 }}')
        assert tmpl.render(context) == 'foo3,14'

        tmpl = Template('{{ form_var_term0|add:form_var_term2 }}')
        assert tmpl.render(context) == 'foo'

        tmpl = Template('{{ form_var_term0|add:form_var_term3 }}')
        assert tmpl.render(context) == 'foo'

        tmpl = Template('{{ form_var_term0|add:form_var_term4 }}')
        assert tmpl.render(context) == 'foo'

        tmpl = Template('{{ form_var_term1|add:form_var_term0 }}')
        assert tmpl.render(context) == '3,14foo'

        tmpl = Template('{{ form_var_term2|add:form_var_term0 }}')
        assert tmpl.render(context) == 'foo'

        tmpl = Template('{{ form_var_term2|add:form_var_term2 }}')
        assert tmpl.render(context) == ''

        tmpl = Template('{{ form_var_term2|add:form_var_term3 }}')
        assert tmpl.render(context) == ''

        tmpl = Template('{{ form_var_term2|add:form_var_term4 }}')
        assert tmpl.render(context) == ''

        tmpl = Template('{{ form_var_term3|add:form_var_term0 }}')
        assert tmpl.render(context) == 'foo'

        tmpl = Template('{{ form_var_term3|add:form_var_term2 }}')
        assert tmpl.render(context) == ''

        tmpl = Template('{{ form_var_term3|add:form_var_term3 }}')
        assert tmpl.render(context) == ''

        tmpl = Template('{{ form_var_term3|add:form_var_term4 }}')
        assert tmpl.render(context) == ''

        tmpl = Template('{{ form_var_term4|add:form_var_term0 }}')
        assert tmpl.render(context) == 'foo'

        tmpl = Template('{{ form_var_term4|add:form_var_term2 }}')
        assert tmpl.render(context) == ''

        tmpl = Template('{{ form_var_term4|add:form_var_term3 }}')
        assert tmpl.render(context) == ''

        tmpl = Template('{{ form_var_term4|add:form_var_term4 }}')
        assert tmpl.render(context) == ''


def test_mathematic_conditions_django(pub, variable_test_data):
    for true_condition_value in (
        # reminder
        'form_var_term1 == 3',
        'form_var_term2 == 4',
        # add
        'form_var_term1|add:form_var_term2 == 7',
        'form_var_term1|add:form_var_term2 == 7.0',
        'form_var_term1|add:form_var_term2 == "7"|decimal',
        'form_var_term1|add:form_var_term2 > 6',
        # subtract
        'form_var_term1|subtract:form_var_term2 == -1',
        'form_var_term1|subtract:form_var_term2 == -1.0',
        'form_var_term1|subtract:form_var_term2 == "-1"|decimal',
        'form_var_term1|subtract:form_var_term2 < 0',
        # multiply
        'form_var_term1|multiply:form_var_term2 == 12',
        'form_var_term1|multiply:form_var_term2 == 12.0',
        'form_var_term1|multiply:form_var_term2 == "12"|decimal',
        'form_var_term1|multiply:form_var_term2 > 10',
        # divide
        'form_var_term1|divide:form_var_term2 == 0.75',
        'form_var_term1|divide:form_var_term2 == 0.750',
        'form_var_term1|divide:form_var_term2 == "0.75"|decimal',
        'form_var_term1|divide:form_var_term2 > 0.5',
    ):
        condition = Condition({'type': 'django', 'value': true_condition_value})
        assert condition.evaluate() is True

    for false_condition_value in (
        'form_var_term1|add:form_var_term2 > 8',
        'form_var_term1|subtract:form_var_term2 > 0',
        'form_var_term1|multiply:form_var_term2 > 20',
        'form_var_term1|divide:form_var_term2 > 1',
    ):
        condition = Condition({'type': 'django', 'value': false_condition_value})
        assert condition.evaluate() is False


def test_lazy_formdata_ceil_filter(pub):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'foobar'
    formdef.url_name = 'foobar'
    formdef.fields = [
        fields.StringField(id='0', label='value', varname='value'),
    ]
    formdef.store()
    formdata = formdef.data_class()()
    formdata.data = {'0': '3.14'}
    formdata.store()
    pub.substitutions.feed(formdata)
    for mode in (None, 'lazy'):
        context = pub.substitutions.get_context_variables(mode=mode)
        tmpl = Template('{{ form_var_value|ceil }}')
        assert tmpl.render(context) == '4'
        tmpl = Template('{{ form_var_value|floor }}')
        assert tmpl.render(context) == '3'
        tmpl = Template('{{ form_var_value|abs }}')
        assert tmpl.render(context) == '3.14'


def test_lazy_formdata_count_as_len_filter(pub):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'foobar'
    formdef.url_name = 'foobar'
    formdef.fields = [
        fields.StringField(id='0', label='value', varname='value'),
    ]
    formdef.store()
    formdata = formdef.data_class()()
    formdata.data = {'0': 'coin'}
    formdata.store()
    pub.substitutions.feed(formdata)
    for mode in (None, 'lazy'):
        context = pub.substitutions.get_context_variables(mode=mode)
        tmpl = Template('{{ form_var_value|count }}')
        assert tmpl.render(context) == '4'

    tmpl = Template('{{ form_var_value|count }}')
    assert tmpl.render({}) == '0'
    assert tmpl.render({'form_var_value': None}) == '0'


def test_rounding_and_abs_conditions_django(pub, variable_test_data):
    for true_condition_value in (
        # reminder
        'form_var_value == 3.14',
        # ceil
        'form_var_value|ceil == 4',
        'form_var_value|ceil == 4.0',
        'form_var_value|ceil > 3',
        # floor
        'form_var_value|floor == 3',
        'form_var_value|floor == 3.0',
        'form_var_value|floor >= 3',
        # abs
        'form_var_value|abs == 3.14|decimal',
        'form_var_value|abs == 3.140|decimal',
        'form_var_value|abs >= 3',
    ):
        condition = Condition({'type': 'django', 'value': true_condition_value})
        assert condition.evaluate() is True

    for false_condition_value in (
        'form_var_value|ceil < 4',
        'form_var_value|ceil >= 5',
        'form_var_value|floor < 3',
    ):
        condition = Condition({'type': 'django', 'value': false_condition_value})
        assert condition.evaluate() is False


def test_lazy_url_suffix(pub, variable_test_data):
    ds = {
        'type': 'jsonvalue',
        'value': json.dumps(
            [
                {'id': '1', 'text': 'un', 'more': 'foo', 'url': 'xxx'},
                {'id': '2', 'text': 'deux', 'more': 'bar', 'url': 'yyy'},
            ]
        ),
    }

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'foobar'
    formdef.url_name = 'foobar'
    formdef.fields = [
        fields.ItemField(id='1', label='item', data_source=ds, varname='plop'),
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {'1': '1'}
    formdata.data['1_display'] = formdef.fields[0].store_display_value(formdata.data, '1')
    formdata.data['1_structured'] = formdef.fields[0].store_structured_value(formdata.data, '1')
    formdata.store()

    pub.substitutions.reset()
    pub.substitutions.feed(formdata)
    for mode in (None, 'lazy'):
        context = pub.substitutions.get_context_variables(mode=mode)
        tmpl = Template('{{ form_var_plop_more }}')
        assert tmpl.render(context) == 'foo'
        tmpl = Template('{{ form_var_plop_url }}')
        assert tmpl.render(context) == 'xxx'


def test_lazy_structured_items(pub, variable_test_data):
    ds = {
        'type': 'jsonvalue',
        'value': json.dumps(
            [
                {'id': '1', 'text': 'un', 'more': 'foo', 'url': 'xxx', 'invalid:key': 'xxx'},
                {'id': '2', 'text': 'deux', 'more': 'bar', 'url': 'yyy', 'invalid:key': 'yyy'},
            ]
        ),
    }

    ds2 = {
        'type': 'jsonvalue',
        'value': json.dumps([{'id': '1', 'text': 'un'}, {'id': '2', 'text': 'deux'}]),
    }

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'foobar'
    formdef.url_name = 'foobar'
    formdef.fields = [
        fields.ItemsField(id='1', label='items', data_source=ds, varname='plop'),
        fields.ItemsField(id='2', label='items', data_source=ds2, varname='plop2'),
        fields.ItemsField(id='3', label='items', data_source=ds, varname='plop3'),
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {'1': ['1', '2'], '2': ['1', '2'], '3': None}
    formdata.data['1_display'] = formdef.fields[0].store_display_value(formdata.data, '1')
    formdata.data['1_structured'] = formdef.fields[0].store_structured_value(formdata.data, '1')
    formdata.data['2_display'] = formdef.fields[1].store_display_value(formdata.data, '2')
    formdata.data['2_structured'] = formdef.fields[1].store_structured_value(formdata.data, '2')
    formdata.store()

    pub.substitutions.reset()
    pub.substitutions.feed(formdata)
    for mode in (None, 'lazy'):
        context = pub.substitutions.get_context_variables(mode=mode)
        tmpl = Template('{{ form_var_plop_0_more }}')
        assert tmpl.render(context) == 'foo'
        tmpl = Template('{{ form_var_plop_0_url }}')
        assert tmpl.render(context) == 'xxx'
        tmpl = Template('{{ form_var_plop_1_more }}')
        assert tmpl.render(context) == 'bar'
        tmpl = Template('{{ form_var_plop_1_url }}')
        assert tmpl.render(context) == 'yyy'
        tmpl = Template('{% for x in form_var_plop_structured_raw %}{{x.more}}{% endfor %}')
        assert tmpl.render(context) == 'foobar'
        tmpl = Template('{% for x in form_var_plop_structured %}{{x.more}}{% endfor %}')
        assert tmpl.render(context) == 'foobar'
        # test iterating on None value
        tmpl = Template('a{% for x in form_var_plop3 %}{{x}}{% endfor %}b')
        assert tmpl.render(context) == 'ab'

    flat_keys = pub.substitutions.get_context_variables(mode='lazy').get_flat_keys()
    assert 'form_var_plop_0_url' in flat_keys
    assert 'form_var_plop_1_more' in flat_keys
    assert 'form_var_plop_structured' in flat_keys
    assert 'form_var_plop_raw' in flat_keys
    assert 'form_var_plop_0_invalid:key' not in flat_keys

    assert 'form_var_plop2' in flat_keys
    assert 'form_var_plop2_raw' in flat_keys
    assert 'form_var_plop2_structured' in flat_keys


def test_formdata_user_field(pub, variable_test_data):
    local_user = variable_test_data._formdata.user

    user_formdef = UserFieldsFormDef(pub)
    user_formdef.fields.append(fields.StringField(id='3', label='test', varname='test'))
    user_formdef.store()
    local_user.form_data = {'3': 'nono'}
    local_user.set_attributes_from_formdata(local_user.form_data)
    local_user.store()

    for mode in (None, 'lazy'):
        context = pub.substitutions.get_context_variables(mode=mode)
        tmpl = Template('{{ form_user_var_test }}')
        assert tmpl.render(context) == 'nono'

    condition = Condition({'type': 'django', 'value': 'form_user_var_test'})
    assert condition.evaluate() is True

    local_user.form_data = {'3': ''}
    local_user.set_attributes_from_formdata(local_user.form_data)
    local_user.store()
    condition = Condition({'type': 'django', 'value': 'form_user_var_test'})
    assert condition.evaluate() is False


def test_formdata_user_has_deleted_account(pub, variable_test_data):
    condition = Condition({'type': 'django', 'value': 'form_user_has_deleted_account'})
    assert condition.evaluate() is False

    local_user = variable_test_data._formdata.user
    local_user.set_deleted()
    condition = Condition({'type': 'django', 'value': 'form_user_has_deleted_account'})
    assert condition.evaluate() is True


def test_string_filters(pub, variable_test_data):
    tmpl = Template('{% with form_var_foo_foo|split:"a" as x %}{{x.0}}{% endwith %}', raises=True)
    for mode in (None, 'lazy'):
        context = pub.substitutions.get_context_variables(mode=mode)
        assert tmpl.render(context) == 'b'

    tmpl = Template('{% if form_var_foo_foo|startswith:"b" %}test{% endif %}', raises=True)
    for mode in (None, 'lazy'):
        context = pub.substitutions.get_context_variables(mode=mode)
        assert tmpl.render(context) == 'test'

    tmpl = Template('{% if form_var_foo_foo|startswith:form_var_term1 %}test{% endif %}', raises=True)
    for mode in (None, 'lazy'):
        context = pub.substitutions.get_context_variables(mode=mode)
        assert tmpl.render(context) == ''


@pytest.mark.parametrize('settings_mode', ['new', 'legacy'])
def test_user_label(pub, settings_mode):
    user_formdef = UserFieldsFormDef(pub)
    user_formdef.fields.append(fields.StringField(id='3', label='first_name', varname='first_name'))
    user_formdef.fields.append(fields.StringField(id='4', label='last_name', varname='last_name'))
    user_formdef.store()
    if settings_mode == 'new':
        pub.cfg['users'][
            'fullname_template'
        ] = '{{ user_var_first_name|default:"" }} {{ user_var_last_name|default:"" }}'
    else:
        pub.cfg['users']['field_name'] = ['3', '4']
    pub.write_cfg()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'foobar'
    formdef.url_name = 'foobar'
    formdef.fields = [
        fields.StringField(id='1', label='first_name', prefill={'type': 'user', 'value': '3'}),
        fields.StringField(id='2', label='last_name', prefill={'type': 'user', 'value': '4'}),
    ]
    formdef.store()

    user = pub.user_class()
    user.email = 'bar@localhost'
    user.store()

    formdata = formdef.data_class()()
    formdata.data = {}
    formdata.user_id = user.id
    formdata.store()

    assert str(formdef.data_class().get(formdata.id).user_id) == str(user.id)
    assert formdef.data_class().get(formdata.id).user_label is None
    assert formdef.data_class().get(formdata.id).get_user_label() == 'bar@localhost'

    formdata = formdef.data_class()()
    formdata.data = {}
    formdata.store()

    assert formdef.data_class().get(formdata.id).user_id is None
    assert formdef.data_class().get(formdata.id).user_label == ''
    assert formdef.data_class().get(formdata.id).get_user_label() == ''

    formdata = formdef.data_class()()
    formdata.data = {'1': 'blah'}
    formdata.store()

    assert formdef.data_class().get(formdata.id).user_id is None
    assert formdef.data_class().get(formdata.id).user_label == 'blah'
    assert formdef.data_class().get(formdata.id).get_user_label() == 'blah'

    formdata = formdef.data_class()()
    formdata.data = {'1': 'blah', '2': 'xxx'}
    formdata.store()

    assert formdef.data_class().get(formdata.id).user_id is None
    assert formdef.data_class().get(formdata.id).user_label == 'blah xxx'
    assert formdef.data_class().get(formdata.id).get_user_label() == 'blah xxx'

    # check with multiple prefilled fields
    formdef.fields = [
        fields.StringField(id='1', label='first_name', prefill={'type': 'user', 'value': '3'}),
        fields.StringField(id='2', label='last_name', prefill={'type': 'user', 'value': '4'}),
        fields.StringField(id='4', label='first_name', prefill={'type': 'user', 'value': '3'}),
        fields.StringField(id='5', label='last_name', prefill={'type': 'user', 'value': '4'}),
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {'1': 'blah', '2': 'xxx'}
    formdata.store()

    assert formdef.data_class().get(formdata.id).user_id is None
    assert formdef.data_class().get(formdata.id).user_label == 'blah xxx'
    assert formdef.data_class().get(formdata.id).get_user_label() == 'blah xxx'

    formdata = formdef.data_class()()
    formdata.data = {'4': 'blah', '5': 'xxx'}
    formdata.store()

    assert formdef.data_class().get(formdata.id).user_id is None
    assert formdef.data_class().get(formdata.id).user_label == 'blah xxx'
    assert formdef.data_class().get(formdata.id).get_user_label() == 'blah xxx'

    formdata = formdef.data_class()()
    formdata.data = {'4': 'blah'}
    formdata.store()

    assert formdef.data_class().get(formdata.id).user_id is None
    assert formdef.data_class().get(formdata.id).user_label == 'blah'
    assert formdef.data_class().get(formdata.id).get_user_label() == 'blah'


@pytest.mark.parametrize('settings_mode', ['new', 'legacy'])
def test_user_label_from_block(pub, settings_mode):
    user_formdef = UserFieldsFormDef(pub)
    user_formdef.fields.append(fields.StringField(id='3', label='first_name', varname='first_name'))
    user_formdef.fields.append(fields.StringField(id='4', label='last_name', varname='last_name'))
    user_formdef.store()
    if settings_mode == 'new':
        pub.cfg['users'][
            'fullname_template'
        ] = '{{ user_var_first_name|default:"" }} {{ user_var_last_name|default:"" }}'
    else:
        pub.cfg['users']['field_name'] = ['3', '4']
    pub.write_cfg()

    BlockDef.wipe()
    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.StringField(id='1', label='first_name', prefill={'type': 'user', 'value': '3'}),
        fields.StringField(id='2', label='last_name', prefill={'type': 'user', 'value': '4'}),
    ]
    block.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'foobar'
    formdef.url_name = 'foobar'
    formdef.fields = [
        fields.BlockField(id='1', label='test', block_slug='foobar', max_items='3', varname='block'),
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {}
    formdata.store()
    assert not formdef.data_class().get(formdata.id).user_label

    formdata = formdef.data_class()()
    formdata.data = {
        '1': {
            'data': [{'1': 'first', '2': 'last'}],
            'schema': {'1': 'string', '2': 'string'},
        }
    }
    formdata.store()
    assert formdef.data_class().get(formdata.id).user_label == 'first last'


def test_form_parent(pub):
    formdef = FormDef()
    formdef.name = 'foobar'
    formdef.fields = [fields.StringField(id='0', label='foo', varname='foo')]
    formdef.store()

    parent = formdef.data_class()()
    parent.data = {'0': 'hello'}
    parent.store()

    child = formdef.data_class()()
    child.data = {'0': 'world'}
    child.submission_context = {
        'orig_object_type': 'formdef',
        'orig_formdef_id': formdef.id,
        'orig_formdata_id': parent.id,
    }
    variables = parent.get_substitution_variables()
    assert variables.get('form_var_foo') == 'hello'
    assert variables.get('form_parent') is None

    assert str(variables['form'].var.foo) == 'hello'
    assert variables['form'].parent is None

    variables = child.get_substitution_variables()
    assert variables.get('form_var_foo') == 'world'
    assert variables.get('form_parent_form_var_foo') == 'hello'
    assert variables.get('form_parent') is not None

    assert str(variables['form'].var.foo) == 'world'
    assert str(variables['form'].parent['form'].var.foo) == 'hello'
    assert variables['form'].parent is not None


def test_block_variables(pub):
    BlockDef.wipe()
    FormDef.wipe()

    block = BlockDef()
    block.name = 'foobar'
    block.digest_template = 'X{{foobar_var_foo}}Y'
    block.fields = [
        fields.StringField(id='123', required='required', label='Test', varname='foo'),
        fields.StringField(id='234', required='required', label='Test2', varname='bar'),
        fields.DateField(id='567', required='optional', label='Test3', varname='day'),
    ]
    block.store()

    formdef = FormDef()
    formdef.name = 'testblock'
    formdef.fields = [
        fields.BlockField(id='1', label='test', block_slug='foobar', max_items='3', varname='block'),
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    # value from test_block_digest in tests/test_form_pages.py
    formdata.data = {
        '1': {
            'data': [{'123': 'foo', '234': 'bar'}, {'123': 'foo2', '234': 'bar2'}],
            'schema': {'123': 'string', '234': 'string'},
        },
        '1_display': 'XfooY, Xfoo2Y',
    }
    formdata.store()

    variables = formdata.get_substitution_variables()
    assert 'form_var_block' in variables.get_flat_keys()
    assert 'form_var_block_0' in variables.get_flat_keys()
    assert 'form_var_block_0_foo' in variables.get_flat_keys()
    assert 'form_var_block_0_bar' in variables.get_flat_keys()
    assert 'form_var_block_1' in variables.get_flat_keys()
    assert 'form_var_block_1_foo' in variables.get_flat_keys()
    assert 'form_var_block_1_bar' in variables.get_flat_keys()

    assert variables.get('form_var_block_0_foo') == 'foo'
    assert variables.get('form_var_block_1_foo') == 'foo2'
    assert variables.get('form_var_block_var_foo') == 'foo'  # alias to 1st element
    assert variables.get('form_var_block_2_foo') is None  # out-of-bounds

    pub.substitutions.reset()
    pub.substitutions.feed(formdata)

    context = pub.substitutions.get_context_variables(mode='lazy')
    tmpl = Template('{{ form_var_block }}')
    assert tmpl.render(context) == 'XfooY, Xfoo2Y'

    tmpl = Template('{% for sub in form_var_block %}{{ sub.foo }} {% endfor %}')
    assert tmpl.render(context) == 'foo foo2'

    tmpl = Template('{{ form_var_block|length }}')
    assert tmpl.render(context) == '2'

    tmpl = Template('{{ form_var_block|count }}')
    assert tmpl.render(context) == '2'

    # Check accessing numerical indices on block data doesn't raises
    tmpl = Template('{{ form_var_block_0.0 }}')
    assert tmpl.render(context) == ''

    tmpl = Template('{% for foo in form_var_block|getlist:"foo" %}/{{ foo }}{% endfor %}')
    assert tmpl.render(context) == '/foo/foo2'

    formdata.data = {
        '1': {
            'data': [
                {'123': '2', '234': '2'},
                {'123': '7', '234': 'bla', '567': datetime.date(2024, 8, 22).timetuple()},
            ],
            'schema': {'123': 'string', '234': 'string', '567': 'date'},
        },
    }
    formdata.store()
    tmpl = Template('{{ form_var_block|getlist:"foo"|sum }} {{ form_var_block|getlist:"bar"|sum }}')
    assert tmpl.render(context) == '9 2'

    tmpl = Template('{{ form_var_block|getlistdict:"foo:test, bar, unknown, day" }}', autoescape=False)
    assert (
        tmpl.render(context) == "[{'test': '2', 'bar': '2', 'unknown': None, 'day': None}, "
        "{'test': '7', 'bar': 'bla', 'unknown': None, 'day': datetime.date(2024, 8, 22)}]"
    )

    # check another count of elements
    formdata.data = {
        '1': {
            'data': [
                {'123': 'foo', '234': 'bar'},
                {'123': 'foo2', '234': 'bar2'},
                {'123': 'foo3', '234': 'bar3'},
            ],
            'schema': {'123': 'string', '234': 'string'},
        },
        '1_display': 'XfooY, Xfoo2Y, Xfoo3Z',
    }
    formdata.store()
    tmpl = Template('{{ form_var_block|length }}')
    assert tmpl.render(context) == '3'
    tmpl = Template('{{ form_var_block|count }}')
    assert tmpl.render(context) == '3'

    # check invalid varname are ignored (should not happen)
    block.fields[0].varname = 'foo-bar'
    block.store()
    formdef.refresh_from_storage()
    formdata = formdef.data_class().get(formdata.id)
    variables = formdata.get_substitution_variables()
    assert 'form_var_block_0_foo-bar' not in variables.get_flat_keys()

    # check a deleted block doesn't raise
    block.name = 'foobar'
    block.store()
    formdef.refresh_from_storage()
    formdef.fields[0].max_items = '1'
    formdef.store()
    formdata = formdef.data_class().get(formdata.id)
    variables = formdata.get_substitution_variables()
    substvars = CompatibilityNamesDict()
    substvars.update(variables)
    assert 'form_var_block_var' in substvars.get_flat_keys()
    assert substvars.get('form_var_block_var') is not None
    assert 'form_var_block_var_bar' in substvars.get_flat_keys()
    assert str(substvars['form_var_block_var_bar']) == 'bar'

    block.remove_self()
    formdef.refresh_from_storage()
    formdata = formdef.data_class().get(formdata.id)
    variables = formdata.get_substitution_variables()
    substvars = CompatibilityNamesDict()
    substvars.update(variables)
    assert 'form_var_block_var' in substvars.get_flat_keys()
    assert substvars.get('form_var_block_var') is None
    assert 'form_var_block_var_bar' not in substvars.get_flat_keys()


def test_block_with_empty_digest_variable(pub):
    BlockDef.wipe()
    FormDef.wipe()

    block = BlockDef()
    block.name = 'foobar'
    block.digest_template = '{{foobar_var_foo}}'  # will render as empty string
    block.fields = [
        fields.StringField(id='234', required='required', label='Test2', varname='bar'),
    ]
    block.store()

    formdef = FormDef()
    formdef.name = 'testblock'
    formdef.fields = [
        fields.BlockField(id='1', label='test', block_slug='foobar', max_items='3', varname='block'),
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {}
    formdef.fields[0].set_value(formdata.data, {'data': [{'234': 'bar2'}], 'schema': {'234': 'string'}})
    assert formdata.data.get('1')
    assert formdata.data.get('1_display') is None
    formdata.just_created()
    formdata.store()

    variables = formdata.get_substitution_variables()
    assert 'form_var_block' in variables.get_flat_keys()  # advertised
    assert variables.get('form_var_block') == '---'  # placeholder for empty value


def test_block_set_value_to_empty_string(pub):
    BlockDef.wipe()
    FormDef.wipe()

    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.StringField(id='234', required='required', label='Test2', varname='bar'),
    ]
    block.store()

    formdef = FormDef()
    formdef.name = 'testblock'
    formdef.fields = [
        fields.BlockField(id='1', label='test', block_slug='foobar', max_items='3', varname='block'),
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {}
    formdef.fields[0].set_value(formdata.data, '')
    formdata.store()
    assert formdata.data.get('1') is None
    assert formdata.data.get('1_display') is None


def test_formdata_filtering_on_fields(pub, freezer):
    NamedDataSource.wipe()
    data_source = NamedDataSource(name='foobar')
    data_source.data_source = {
        'type': 'jsonvalue',
        'value': json.dumps([{'id': '1', 'text': 'foo'}, {'id': '2', 'text': 'bar'}]),
    }
    data_source.store()
    data_source2 = NamedDataSource(name='foobar2')
    # use large numbers as identifiers as they are concatenated in SQL and it should
    # not trigger any out-of-bounds SQL checks or Python pre-checks.
    data_source2.data_source = {
        'type': 'jsonvalue',
        'value': json.dumps(
            [{'id': '9000', 'text': 'foo'}, {'id': '10000', 'text': 'bar'}, {'id': '11000', 'text': 'baz'}]
        ),
    }
    data_source2.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [
        fields.StringField(id='1', label='String', varname='string'),
        fields.ItemField(id='2', label='Item', data_source={'type': 'foobar'}, varname='item'),
        fields.ItemsField(id='21', label='Items', data_source={'type': 'foobar2'}, varname='items'),
        fields.ItemsField(id='22', label='Other Item', items=['foo', 'bar', 'baz'], varname='items2'),
        fields.BoolField(id='3', label='Bool', varname='bool'),
        fields.DateField(id='4', label='Date', varname='date'),
        fields.EmailField(id='5', label='Email', varname='email'),
        fields.TextField(id='6', label='Text', varname='text'),
    ]
    formdef.store()

    data_class = formdef.data_class()
    data_class.wipe()

    for i in range(12):
        formdata = data_class()
        formdata.data = {
            '1': 'plop%s' % i,
            '2': '1' if i % 2 else '2',
            '2_display': 'foo' if i % 2 else 'bar',
            '2_structured': 'XXX' if i % 2 else 'YYY',
            '21': ['9000' if i % 2 else '11000', '10000'],
            '22': ['foo' if i % 2 else 'bar', 'baz'],
            '3': bool(i % 2),
            '4': datetime.date(2021, 6, i + 1).timetuple(),
            '5': 'a@localhost' if i % 2 else 'b@localhost',
            '6': 'plop%s' % i,
        }
        if i == 10:
            # empty values
            formdata.data = {
                '1': '',
                '2': '',
                '21': [],
                '22': [],
                '5': '',
                '6': '',
            }
        if i == 11:
            # non values
            formdata.data = {}
        formdata.just_created()
        if i % 3:
            formdata.jump_status('finished')
        formdata.store()

    context = pub.substitutions.get_context_variables(mode='lazy')

    # string
    tmpl = Template('{{forms|objects:"test"|filter_by:"string"|filter_value:"plop0"|count}}')
    assert tmpl.render(context) == '1'
    tmpl = Template('{{forms|objects:"test"|filter_by:"string"|filter_value:"plop1"|count}}')
    assert tmpl.render(context) == '1'
    tmpl = Template('{{forms|objects:"test"|filter_by:"string"|filter_value:"plop10"|count}}')
    assert tmpl.render(context) == '0'
    tmpl = Template('{{forms|objects:"test"|filter_by:"string"|exclude_value:"plop0"|count}}')
    assert tmpl.render(context) == '11'
    tmpl = Template('{{forms|objects:"test"|filter_by:"string"|exclude_value:"plop1"|count}}')
    assert tmpl.render(context) == '11'
    tmpl = Template('{{forms|objects:"test"|filter_by:"string"|exclude_value:"plop10"|count}}')
    assert tmpl.render(context) == '12'
    params = [
        ('equal', 'plop5', '1'),
        ('not_equal', 'plop5', '11'),
        ('not_equal', 'plop1', '11'),
        ('less_than', 'plop5', '6'),
        ('less_than_or_equal', 'plop5', '7'),
        ('greater_than', 'plop5', '4'),
        ('greater_than', '42', '0'),
        ('greater_than_or_equal', 'plop5', '5'),
        ('in', 'plop5', '1'),
        ('in', 'plop5|plop4', '2'),
        ('in', ['plop5', 'plop4'], '2'),
        ('not_in', 'plop5', '10'),
        ('not_in', 'plop5|plop4', '9'),
        ('not_in', ['plop5', 'plop4'], '9'),
        ('absent', '', '2'),
        ('existing', '', '10'),
        ('between', 'plop1|plop5', '4'),
        ('between', 'plop5|plop1', '4'),
        ('between', ['plop1', 'plop5'], '4'),
        ('between', ['plop5', 'plop1'], '4'),
        ('icontains', 'plop', '10'),
        ('icontains', 'PLOP', '10'),
        ('i_equal', 'PLOP5', '1'),
    ]
    for operator, value, result in params:
        context['value'] = None
        if value and isinstance(value, str):
            tmpl = Template(
                '{{forms|objects:"test"|filter_by:"string"|%s|filter_value:"%s"|count}}' % (operator, value)
            )
        elif value:
            tmpl = Template(
                '{{forms|objects:"test"|filter_by:"string"|%s|filter_value:value|count}}' % operator
            )
            context['value'] = value
        else:
            tmpl = Template('{{forms|objects:"test"|filter_by:"string"|%s|count}}' % operator)
        assert tmpl.render(context) == result
    LoggedError.wipe()
    tmpl = Template(
        '{{forms|objects:"test"|filter_by:"string"|between|filter_value:"plop1|plop2|plop3"|count}}'
    )
    assert tmpl.render(context) == '0'
    assert LoggedError.count() == 1
    logged_error = LoggedError.select(order_by='id')[0]
    assert (
        logged_error.summary == 'Invalid value "plop1|plop2|plop3" for operator "between" and filter "string"'
    )
    LoggedError.wipe()
    tmpl = Template('{{forms|objects:"test"|filter_by:"string"|between|filter_value:"plop1"|count}}')
    assert tmpl.render(context) == '0'
    assert LoggedError.count() == 1
    logged_error = LoggedError.select(order_by='id')[0]
    assert logged_error.summary == 'Invalid value "plop1" for operator "between" and filter "string"'
    # item
    tmpl = Template('{{forms|objects:"test"|filter_by:"item"|filter_value:"1"|count}}')
    assert tmpl.render(context) == '5'
    tmpl = Template('{{forms|objects:"test"|filter_by:"item"|filter_value:"2"|count}}')
    assert tmpl.render(context) == '5'
    tmpl = Template('{{forms|objects:"test"|filter_by:"item"|filter_value:"3"|count}}')
    assert tmpl.render(context) == '0'
    tmpl = Template('{{forms|objects:"test"|filter_by:"item"|exclude_value:"1"|count}}')
    assert tmpl.render(context) == '6'
    tmpl = Template('{{forms|objects:"test"|filter_by:"item"|exclude_value:"2"|count}}')
    assert tmpl.render(context) == '6'
    tmpl = Template('{{forms|objects:"test"|filter_by:"item"|exclude_value:"3"|count}}')
    assert tmpl.render(context) == '11'
    params = [
        ('equal', '1', '5'),
        ('not_equal', '1', '6'),
        ('less_than', '2', '5'),
        ('less_than_or_equal', '1', '5'),
        ('greater_than', '1', '5'),
        ('greater_than_or_equal', '2', '5'),
        ('in', '1', '5'),
        ('in', '1|2', '10'),
        ('in', '1|42', '5'),
        ('in', '1|a', '5'),
        ('in', ['1', '42'], '5'),
        ('in', [1, 42], '5'),
        ('in', (1, 42), '5'),
        ('not_in', '1', '5'),
        ('not_in', '1|2', '0'),
        ('not_in', '1|42', '5'),
        ('absent', '', '2'),
        ('existing', '', '10'),
        ('between', '1|2', '5'),
        ('between', '1|3', '10'),
        ('between', '3|1', '10'),
    ]
    for operator, value, result in params:
        context['value'] = None
        if value and isinstance(value, str):
            tmpl = Template(
                '{{forms|objects:"test"|filter_by:"item"|%s|filter_value:"%s"|count}}' % (operator, value)
            )
        elif value:
            tmpl = Template(
                '{{forms|objects:"test"|filter_by:"item"|%s|filter_value:value|count}}' % operator
            )
            context['value'] = value
        else:
            tmpl = Template('{{forms|objects:"test"|filter_by:"item"|%s|count}}' % operator)
        assert tmpl.render(context) == result
    assert tmpl.render(context) == result
    # items
    tmpl = Template('{{forms|objects:"test"|filter_by:"items"|filter_value:"11000"|count}}')
    assert tmpl.render(context) == '5'
    tmpl = Template('{{forms|objects:"test"|filter_by:"items"|filter_value:"11001|9000"|count}}')
    assert tmpl.render(context) == '5'
    tmpl = Template('{{forms|objects:"test"|filter_by:"items"|exclude_value:"11001|9000"|count}}')
    assert tmpl.render(context) == '7'
    params = [
        ('equal', '11000', '5'),
        ('equal', '10000', '10'),
        ('not_equal', '9000', '7'),
        ('not_equal', '10000', '2'),
        ('less_than', '10000', '5'),
        ('less_than_or_equal', '10000', '10'),
        ('greater_than', '10000', '5'),
        ('greater_than', '9000', '10'),
        ('greater_than_or_equal', '11000', '5'),
        ('in', '11000', '5'),
        ('in', '11000|10000', '10'),
        ('in', '11000|9000', '10'),
        ('not_in', '11000', '7'),
        ('not_in', '11000|10000', '2'),
        ('not_in', '11000|9000', '2'),
        ('not_in', '11001|9000', '7'),
        ('not_in', ['11000', '9000'], '2'),
        ('not_in', ['11001', '9000'], '7'),
        ('absent', '', '2'),
        ('existing', '', '10'),
        ('between', '9000|10000', '5'),
        ('between', '9000|9001', '5'),
        ('between', '9001|9000', '5'),
    ]
    for operator, value, result in params:
        context['value'] = None
        if value and isinstance(value, str):
            tmpl = Template(
                '{{forms|objects:"test"|filter_by:"items"|%s|filter_value:"%s"|count}}' % (operator, value)
            )
        elif value:
            tmpl = Template(
                '{{forms|objects:"test"|filter_by:"items"|%s|filter_value:value|count}}' % operator
            )
            context['value'] = value
        else:
            tmpl = Template('{{forms|objects:"test"|filter_by:"items"|%s|count}}' % operator)
        assert tmpl.render(context) == result

    params = [
        ('equal', 'foo', '5'),
        ('not_equal', 'foo', '7'),
        ('less_than', 'foo', '10'),
        ('less_than_or_equal', 'foo', '10'),
        ('greater_than', 'foo', '0'),
        ('greater_than', '42', '0'),
        ('greater_than_or_equal', 'foo', '5'),
        ('in', 'foo', '5'),
        ('in', 'foo|bar', '10'),
        ('not_in', 'foo', '7'),
        ('not_in', 'foo|bar', '2'),
        ('absent', '', '2'),
        ('existing', '', '10'),
        ('between', 'bar|bazz', '10'),
        ('between', 'bazz|bar', '10'),
    ]
    for operator, value, result in params:
        if value:
            tmpl = Template(
                '{{forms|objects:"test"|filter_by:"items2"|%s|filter_value:"%s"|count}}' % (operator, value)
            )
        else:
            tmpl = Template('{{forms|objects:"test"|filter_by:"items2"|%s|count}}' % operator)
        assert tmpl.render(context) == result
    # bool
    tmpl = Template('{{forms|objects:"test"|filter_by:"bool"|filter_value:"true"|count}}')
    assert tmpl.render(context) == '5'
    tmpl = Template('{{forms|objects:"test"|filter_by:"bool"|filter_value:"false"|count}}')
    assert tmpl.render(context) == '5'
    tmpl = Template('{{forms|objects:"test"|filter_by:"bool"|exclude_value:"true"|count}}')
    assert tmpl.render(context) == '7'
    params = [
        ('equal', 'true', '5'),
        ('not_equal', 'true', '7'),
        ('absent', '', '2'),
        ('existing', '', '10'),
    ]
    for operator, value, result in params:
        if value:
            tmpl = Template(
                '{{forms|objects:"test"|filter_by:"bool"|%s|filter_value:"%s"|count}}' % (operator, value)
            )
        else:
            tmpl = Template('{{forms|objects:"test"|filter_by:"bool"|%s|count}}' % operator)
        assert tmpl.render(context) == result
    for operator in [
        'less_than',
        'less_than_or_equal',
        'greater_than',
        'greater_than_or_equal',
        'in',
        'not_in',
        'between',
    ]:
        LoggedError.wipe()
        tmpl = Template('{{forms|objects:"test"|filter_by:"bool"|%s|filter_value:"plop"|count}}' % operator)
        assert tmpl.render(context) == '0'
        assert LoggedError.count() == 1
        logged_error = LoggedError.select(order_by='id')[0]
        assert logged_error.summary == 'Invalid operator "%s" for filter "bool"' % operator
    # date
    tmpl = Template('{{forms|objects:"test"|filter_by:"date"|filter_value:"2021-06-01"|count}}')
    assert tmpl.render(context) == '1'
    tmpl = Template('{{forms|objects:"test"|filter_by:"date"|filter_value:"2021-06-02"|count}}')
    assert tmpl.render(context) == '1'
    tmpl = Template('{{forms|objects:"test"|filter_by:"date"|exclude_value:"2021-06-01"|count}}')
    assert tmpl.render(context) == '11'
    tmpl = Template('{{forms|objects:"test"|filter_by:"date"|exclude_value:"2021-06-02"|count}}')
    assert tmpl.render(context) == '11'
    # check with date in d/m/Y format
    pub.cfg['language'] = {'language': 'fr'}
    tmpl = Template('{{forms|objects:"test"|filter_by:"date"|filter_value:"02/06/2021"|count}}')
    assert tmpl.render(context) == '1'
    pub.cfg['language'] = {'language': 'en'}
    freezer.move_to(datetime.datetime(2021, 6, 1, 12, 0))
    params = [
        ('equal', '2021-06-02', '1'),
        ('not_equal', '2021-06-02', '11'),
        ('less_than', '2021-06-02', '1'),
        ('less_than_or_equal', '2021-06-02', '2'),
        ('greater_than', '2021-06-02', '8'),
        ('greater_than_or_equal', '2021-06-02', '9'),
        ('in', '2021-06-02', '1'),
        ('in', '2021-06-02|2021-06-05', '2'),
        ('in', ('2021-06-02', '2021-06-05'), '2'),
        ('in', (datetime.date(2021, 6, 2), datetime.date(2021, 6, 5)), '2'),
        ('not_in', '2021-06-02', '9'),
        ('not_in', '2021-06-02|2021-06-05', '8'),
        ('absent', '', '2'),
        ('existing', '', '10'),
        ('between', '2021-06-02|2021-06-05', '3'),
        ('between', '2021-06-05|2021-06-02', '3'),
        ('is_today', '', '1'),
        ('is_tomorrow', '', '1'),
        ('is_yesterday', '', '0'),
        ('is_this_week', '', '6'),
    ]
    for operator, value, result in params:
        context['value'] = None
        if value and isinstance(value, str):
            tmpl = Template(
                '{{forms|objects:"test"|filter_by:"date"|%s|filter_value:"%s"|count}}' % (operator, value)
            )
        elif value:
            tmpl = Template(
                '{{forms|objects:"test"|filter_by:"date"|%s|filter_value:value|count}}' % operator
            )
            context['value'] = value
        else:
            tmpl = Template('{{forms|objects:"test"|filter_by:"date"|%s|count}}' % operator)
        assert tmpl.render(context) == result
    # email
    tmpl = Template('{{forms|objects:"test"|filter_by:"email"|filter_value:"a@localhost"|count}}')
    assert tmpl.render(context) == '5'
    tmpl = Template('{{forms|objects:"test"|filter_by:"email"|filter_value:"b@localhost"|count}}')
    assert tmpl.render(context) == '5'
    tmpl = Template('{{forms|objects:"test"|filter_by:"email"|filter_value:"c@localhost"|count}}')
    assert tmpl.render(context) == '0'
    tmpl = Template('{{forms|objects:"test"|filter_by:"email"|exclude_value:"a@localhost"|count}}')
    assert tmpl.render(context) == '7'
    tmpl = Template('{{forms|objects:"test"|filter_by:"email"|exclude_value:"b@localhost"|count}}')
    assert tmpl.render(context) == '7'
    tmpl = Template('{{forms|objects:"test"|filter_by:"email"|exclude_value:"c@localhost"|count}}')
    assert tmpl.render(context) == '12'
    params = [
        ('equal', 'a@localhost', '5'),
        ('not_equal', 'a@localhost', '7'),
        ('in', 'a@localhost', '5'),
        ('in', 'a@localhost|b@localhost', '10'),
        ('not_in', 'a@localhost', '6'),
        ('not_in', 'a@localhost|b@localhost', '1'),
        ('absent', '', '2'),
        ('existing', '', '10'),
        ('icontains', 'A@local', '5'),
        ('icontains', '@LOCAL', '10'),
    ]
    for operator, value, result in params:
        if value:
            tmpl = Template(
                '{{forms|objects:"test"|filter_by:"email"|%s|filter_value:"%s"|count}}' % (operator, value)
            )
        else:
            tmpl = Template('{{forms|objects:"test"|filter_by:"email"|%s|count}}' % operator)
        assert tmpl.render(context) == result
    for operator in ['less_than', 'less_than_or_equal', 'greater_than', 'greater_than_or_equal', 'between']:
        LoggedError.wipe()
        tmpl = Template('{{forms|objects:"test"|filter_by:"email"|%s|filter_value:"plop"|count}}' % operator)
        assert tmpl.render(context) == '0'
        assert LoggedError.count() == 1
        logged_error = LoggedError.select(order_by='id')[0]
        assert logged_error.summary == 'Invalid operator "%s" for filter "email"' % operator
    # text
    tmpl = Template('{{forms|objects:"test"|filter_by:"text"|filter_value:"plop0"|count}}')
    assert tmpl.render(context) == '1'
    tmpl = Template('{{forms|objects:"test"|filter_by:"text"|filter_value:"plop1"|count}}')
    assert tmpl.render(context) == '1'
    tmpl = Template('{{forms|objects:"test"|filter_by:"text"|filter_value:"plop10"|count}}')
    assert tmpl.render(context) == '0'
    tmpl = Template('{{forms|objects:"test"|filter_by:"text"|exclude_value:"plop0"|count}}')
    assert tmpl.render(context) == '11'
    tmpl = Template('{{forms|objects:"test"|filter_by:"text"|exclude_value:"plop1"|count}}')
    assert tmpl.render(context) == '11'
    tmpl = Template('{{forms|objects:"test"|filter_by:"text"|exclude_value:"plop10"|count}}')
    assert tmpl.render(context) == '12'
    params = [
        ('equal', 'plop5', '1'),
        ('not_equal', 'plop5', '11'),
        ('not_equal', 'plop1', '11'),
        ('less_than', 'plop5', '6'),
        ('less_than_or_equal', 'plop5', '7'),
        ('greater_than', 'plop5', '4'),
        ('greater_than_or_equal', 'plop5', '5'),
        ('in', 'plop5', '1'),
        ('in', 'plop5|plop4', '2'),
        ('in', ['plop5', 'plop4'], '2'),
        ('not_in', 'plop5', '10'),
        ('not_in', 'plop5|plop4', '9'),
        ('not_in', ['plop5', 'plop4'], '9'),
        ('absent', '', '2'),
        ('existing', '', '10'),
        ('between', 'plop1|plop5', '4'),
        ('between', 'plop5|plop1', '4'),
        ('between', ['plop1', 'plop5'], '4'),
        ('between', ['plop5', 'plop1'], '4'),
        ('icontains', 'plop', '10'),
        ('icontains', 'PLOP', '10'),
    ]
    for operator, value, result in params:
        context['value'] = None
        if value and isinstance(value, str):
            tmpl = Template(
                '{{forms|objects:"test"|filter_by:"text"|%s|filter_value:"%s"|count}}' % (operator, value)
            )
        elif value:
            tmpl = Template(
                '{{forms|objects:"test"|filter_by:"text"|%s|filter_value:value|count}}' % operator
            )
            context['value'] = value
        else:
            tmpl = Template('{{forms|objects:"test"|filter_by:"text"|%s|count}}' % operator)
        assert tmpl.render(context) == result

    # operators not allowed with exclude_value
    for operator in [
        'equal',
        'not_equal',
        'less_than',
        'less_than_or_equal',
        'greater_than',
        'greater_than_or_equal',
        'in',
        'not_in',
        'between',
    ]:
        LoggedError.wipe()
        tmpl = Template(
            '{{forms|objects:"test"|filter_by:"string"|%s|exclude_value:"plop"|count}}' % operator
        )
        assert tmpl.render(context) == '0'
        assert LoggedError.count() == 1
        logged_error = LoggedError.select(order_by='id')[0]
        assert logged_error.summary == 'Operator filter is not allowed for exclude_value filter'

    # internal_id
    params = [
        ('equal', '1', '1'),
        ('equal', '01', '1'),
        ('not_equal', '1', '11'),
        ('less_than', '1', '0'),
        ('less_than_or_equal', '1', '1'),
        ('greater_than', '1', '11'),
        ('greater_than', '10', '2'),
        ('greater_than_or_equal', '1', '12'),
        ('in', '1|2|4', '3'),
        ('in', '1,2,4', '3'),
        ('not_in', '1|2|4', '9'),
    ]
    for operator, value, result in params:
        tmpl = Template(
            '{{forms|objects:"test"|filter_by:"internal_id"|%s|filter_value:"%s"|count}}' % (operator, value)
        )
        assert tmpl.render(context) == result

    LoggedError.wipe()
    tmpl = Template('{{forms|objects:"test"|filter_by:"internal_id"|in|filter_value:"xx,2"|count}}')
    assert tmpl.render(context) == '0'
    assert LoggedError.count() == 1
    logged_error = LoggedError.select(order_by='id')[0]
    assert logged_error.summary == 'Invalid value "xx,2" for filter "internal_id"'

    for operator in ['absent', 'existing', 'between']:
        LoggedError.wipe()
        if operator in ['absent', 'existing']:
            tmpl = Template('{{forms|objects:"test"|filter_by:"internal_id"|%s|count}}' % operator)
        else:
            tmpl = Template(
                '{{forms|objects:"test"|filter_by:"internal_id"|%s|filter_value:"plop"|count}}' % operator
            )
        assert tmpl.render(context) == '0'
        assert LoggedError.count() == 1
        logged_error = LoggedError.select(order_by='id')[0]
        assert logged_error.summary == 'Invalid operator "%s" for filter "internal_id"' % operator

    # status
    params = [
        ('equal', 'Just Submitted', '4'),
        ('equal', 'Finished', '8'),
        ('equal', 'Unknown', '0'),
        ('not_equal', 'Finished', '4'),
    ]
    for operator, value, result in params:
        tmpl = Template(
            '{{forms|objects:"test"|filter_by:"status"|%s|filter_value:"%s"|count}}' % (operator, value)
        )
        assert tmpl.render(context) == result
    for operator in [
        'less_than',
        'less_than_or_equal',
        'greater_than',
        'greater_than_or_equal',
        'in',
        'not_in',
        'absent',
        'existing',
        'between',
    ]:
        LoggedError.wipe()
        if operator in ['absent', 'existing']:
            tmpl = Template('{{forms|objects:"test"|filter_by:"status"|%s|count}}' % operator)
        else:
            tmpl = Template(
                '{{forms|objects:"test"|filter_by:"status"|%s|filter_value:"plop"|count}}' % operator
            )
        assert tmpl.render(context) == '0'
        assert LoggedError.count() == 1
        logged_error = LoggedError.select(order_by='id')[0]
        assert logged_error.summary == 'Invalid operator "%s" for filter "status"' % operator


def test_formdata_filtering_on_block_fields(pub):
    NamedDataSource.wipe()
    data_source = NamedDataSource(name='foobar')
    data_source.data_source = {
        'type': 'jsonvalue',
        'value': json.dumps([{'id': '1', 'text': 'foo'}, {'id': '2', 'text': 'bar'}]),
    }
    data_source.store()

    BlockDef.wipe()
    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.StringField(id='1', label='String', varname='string'),
        fields.ItemField(id='2', label='Item', data_source={'type': 'foobar'}, varname='item'),
        fields.BoolField(id='3', label='Bool', varname='bool'),
        fields.DateField(id='4', label='Date', varname='date'),
        fields.EmailField(id='5', label='Email', varname='email'),
        fields.TextField(id='6', label='Text', varname='text'),
        fields.FileField(id='7', label='File', varname='file'),
    ]
    block.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [
        fields.BlockField(
            id='0', label='Block Data', varname='blockdata', block_slug='foobar', max_items='3'
        ),
    ]
    formdef.store()

    data_class = formdef.data_class()
    data_class.wipe()

    for i in range(14):
        formdata = data_class()
        formdata.data = {
            '0': {
                'data': [
                    {
                        '1': 'plop%s' % i,
                        '2': '1' if i % 2 else '2',
                        '2_display': 'foo' if i % 2 else 'bar',
                        '2_structured': 'XXX' if i % 2 else 'YYY',
                        '3': bool(i % 2),
                        '4': '2021-06-%02d' % (i + 1),
                        '5': 'a@localhost' if i % 2 else 'b@localhost',
                        '6': 'plop%s' % i,
                        '7': {'base_filename': 'test.txt'},  # other keys are not necessary
                    },
                ],
                'schema': {},  # not important here
            },
            '0_display': 'hello',
        }
        if i == 0:
            # 2 elements with values
            formdata.data['0']['data'].append(
                {
                    '1': 'plop%s' % (i + 1),
                    '2': '1',
                    '2_display': 'foo',
                    '2_structured': 'XXX',
                    '3': True,
                    '4': '2021-06-02',
                    '5': 'a@localhost',
                    '6': 'plop%s' % (i + 1),
                },
            )
        if i == 9:
            formdata.data['0']['data'][0]['1'] = 'fooBAR'
        if i == 10:
            # 2 elements, the second without values
            formdata.data['0']['data'].append(
                {
                    '1': '',
                    '2': '',
                    '4': '',
                    '5': '',
                    '6': '',
                }
            )
        if i == 11:
            # 2 elements, the second with non values
            formdata.data['0']['data'].append({})
        if i == 12:
            # only one element, without values
            formdata.data = {
                '0': {
                    'data': [
                        {
                            '1': '',
                            '2': '',
                            '4': '',
                            '5': '',
                            '6': '',
                        }
                    ]
                }
            }
        if i == 13:
            # no element
            formdata.data = {}
        formdata.just_created()
        formdata.jump_status('new')
        formdata.store()

    context = pub.substitutions.get_context_variables(mode='lazy')
    context['none_value'] = None

    # string
    tmpl = Template('{{forms|objects:"test"|filter_by:"blockdata_string"|filter_value:"plop0"|count}}')
    assert tmpl.render(context) == '1'
    tmpl = Template('{{forms|objects:"test"|filter_by:"blockdata_string"|filter_value:"plop1"|count}}')
    assert tmpl.render(context) == '2'
    tmpl = Template('{{forms|objects:"test"|filter_by:"blockdata_string"|filter_value:"plop12"|count}}')
    assert tmpl.render(context) == '0'
    tmpl = Template('{{forms|objects:"test"|filter_by:"blockdata_string"|exclude_value:"plop0"|count}}')
    assert tmpl.render(context) == '13'
    tmpl = Template('{{forms|objects:"test"|filter_by:"blockdata_string"|exclude_value:"plop1"|count}}')
    assert tmpl.render(context) == '12'
    tmpl = Template('{{forms|objects:"test"|filter_by:"blockdata_string"|exclude_value:"plop12"|count}}')
    assert tmpl.render(context) == '14'
    tmpl = Template('{{forms|objects:"test"|filter_by:"blockdata_string"|filter_value:none_value|count}}')
    assert tmpl.render(context) == '2'
    params = [
        ('equal', 'plop5', '1'),
        ('i_equal', 'FOObar', '1'),
        ('not_equal', 'plop5', '13'),
        ('not_equal', 'plop1', '12'),
        ('less_than', 'plop5', '9'),
        ('less_than_or_equal', 'plop5', '10'),
        ('greater_than', 'plop5', '3'),
        ('greater_than', '42', '0'),
        ('greater_than_or_equal', 'plop5', '4'),
        ('in', 'plop5', '1'),
        ('in', 'plop5|plop4', '2'),
        ('not_in', 'plop5', '13'),
        ('not_in', 'plop5|plop4', '12'),
        ('absent', '', '2'),
        ('existing', '', '12'),
        ('between', 'plop1|plop5', '7'),
        ('between', 'plop5|plop1', '7'),
    ]
    for operator, value, result in params:
        if value:
            tmpl = Template(
                '{{forms|objects:"test"|filter_by:"blockdata_string"|%s|filter_value:"%s"|count}}'
                % (operator, value)
            )
        else:
            tmpl = Template('{{forms|objects:"test"|filter_by:"blockdata_string"|%s|count}}' % operator)
        assert tmpl.render(context) == result
    # item
    tmpl = Template('{{forms|objects:"test"|filter_by:"blockdata_item"|filter_value:"1"|count}}')
    assert tmpl.render(context) == '7'
    tmpl = Template('{{forms|objects:"test"|filter_by:"blockdata_item"|filter_value:"2"|count}}')
    assert tmpl.render(context) == '6'
    tmpl = Template('{{forms|objects:"test"|filter_by:"blockdata_item"|filter_value:"3"|count}}')
    assert tmpl.render(context) == '0'
    tmpl = Template('{{forms|objects:"test"|filter_by:"blockdata_item"|exclude_value:"1"|count}}')
    assert tmpl.render(context) == '7'
    tmpl = Template('{{forms|objects:"test"|filter_by:"blockdata_item"|exclude_value:"2"|count}}')
    assert tmpl.render(context) == '8'
    tmpl = Template('{{forms|objects:"test"|filter_by:"blockdata_item"|exclude_value:"3"|count}}')
    assert tmpl.render(context) == '14'
    tmpl = Template('{{forms|objects:"test"|filter_by:"blockdata_item"|filter_value:none_value|count}}')
    assert tmpl.render(context) == '2'
    params = [
        ('equal', '1', '7'),
        ('not_equal', '1', '7'),
        ('less_than', '2', '7'),
        ('less_than_or_equal', '1', '7'),
        ('greater_than', '1', '6'),
        ('greater_than_or_equal', '2', '6'),
        ('in', '1', '7'),
        ('in', '1|2', '12'),
        ('in', '1|42', '7'),
        ('in', '1|a', '7'),
        ('not_in', '1', '7'),
        ('not_in', '1|2', '2'),
        ('not_in', '1|42', '7'),
        ('absent', '', '2'),
        ('existing', '', '12'),
        ('between', '1|2', '7'),
        ('between', '2|1', '7'),
        ('between', '1|3', '12'),
        ('between', '3|1', '12'),
    ]
    for operator, value, result in params:
        if value:
            tmpl = Template(
                '{{forms|objects:"test"|filter_by:"blockdata_item"|%s|filter_value:"%s"|count}}'
                % (operator, value)
            )
        else:
            tmpl = Template('{{forms|objects:"test"|filter_by:"blockdata_item"|%s|count}}' % operator)
        assert tmpl.render(context) == result
    # bool
    tmpl = Template('{{forms|objects:"test"|filter_by:"blockdata_bool"|filter_value:"true"|count}}')
    assert tmpl.render(context) == '7'
    tmpl = Template('{{forms|objects:"test"|filter_by:"blockdata_bool"|filter_value:"false"|count}}')
    assert tmpl.render(context) == '6'
    tmpl = Template('{{forms|objects:"test"|filter_by:"blockdata_bool"|exclude_value:"true"|count}}')
    assert tmpl.render(context) == '7'
    tmpl = Template('{{forms|objects:"test"|filter_by:"blockdata_bool"|exclude_value:"false"|count}}')
    assert tmpl.render(context) == '8'
    tmpl = Template('{{forms|objects:"test"|filter_by:"blockdata_bool"|filter_value:none_value|count}}')
    assert tmpl.render(context) == '2'
    params = [
        ('equal', 'true', '7'),
        ('not_equal', 'true', '7'),
        ('absent', '', '2'),
        ('existing', '', '12'),
    ]
    for operator, value, result in params:
        if value:
            tmpl = Template(
                '{{forms|objects:"test"|filter_by:"blockdata_bool"|%s|filter_value:"%s"|count}}'
                % (operator, value)
            )
        else:
            tmpl = Template('{{forms|objects:"test"|filter_by:"blockdata_bool"|%s|count}}' % operator)
        assert tmpl.render(context) == result
    for operator in [
        'less_than',
        'less_than_or_equal',
        'greater_than',
        'greater_than_or_equal',
        'in',
        'not_in',
        'between',
    ]:
        LoggedError.wipe()
        tmpl = Template(
            '{{forms|objects:"test"|filter_by:"blockdata_bool"|%s|filter_value:"plop"|count}}' % operator
        )
        assert tmpl.render(context) == '0'
        assert LoggedError.count() == 1
        logged_error = LoggedError.select(order_by='id')[0]
        assert logged_error.summary == 'Invalid operator "%s" for filter "blockdata_bool"' % operator
    # date
    tmpl = Template('{{forms|objects:"test"|filter_by:"blockdata_date"|filter_value:"2021-06-01"|count}}')
    assert tmpl.render(context) == '1'
    tmpl = Template('{{forms|objects:"test"|filter_by:"blockdata_date"|filter_value:"2021-06-02"|count}}')
    assert tmpl.render(context) == '2'
    tmpl = Template('{{forms|objects:"test"|filter_by:"blockdata_date"|exclude_value:"2021-06-01"|count}}')
    assert tmpl.render(context) == '13'
    tmpl = Template('{{forms|objects:"test"|filter_by:"blockdata_date"|exclude_value:"2021-06-02"|count}}')
    assert tmpl.render(context) == '12'
    tmpl = Template('{{forms|objects:"test"|filter_by:"blockdata_date"|filter_value:none_value|count}}')
    assert tmpl.render(context) == '2'
    # check with date in d/m/Y format
    pub.cfg['language'] = {'language': 'fr'}
    tmpl = Template('{{forms|objects:"test"|filter_by:"blockdata_date"|filter_value:"02/06/2021"|count}}')
    assert tmpl.render(context) == '2'
    pub.cfg['language'] = {'language': 'en'}
    params = [
        ('equal', '2021-06-02', '2'),
        ('not_equal', '2021-06-02', '12'),
        ('less_than', '2021-06-02', '3'),
        ('less_than_or_equal', '2021-06-02', '4'),
        ('greater_than', '2021-06-02', '10'),
        ('greater_than_or_equal', '2021-06-02', '12'),
        ('in', '2021-06-02', '2'),
        ('in', '2021-06-02|2021-06-05', '3'),
        ('not_in', '2021-06-02', '12'),
        ('not_in', '2021-06-02|2021-06-05', '11'),
        ('absent', '', '2'),
        ('existing', '', '12'),
        ('between', '2021-06-02|2021-06-05', '4'),
        ('between', '2021-06-05|2021-06-02', '4'),
    ]
    for operator, value, result in params:
        if value:
            tmpl = Template(
                '{{forms|objects:"test"|filter_by:"blockdata_date"|%s|filter_value:"%s"|count}}'
                % (operator, value)
            )
        else:
            tmpl = Template('{{forms|objects:"test"|filter_by:"blockdata_date"|%s|count}}' % operator)
        assert tmpl.render(context) == result
    # email
    tmpl = Template('{{forms|objects:"test"|filter_by:"blockdata_email"|filter_value:"a@localhost"|count}}')
    assert tmpl.render(context) == '7'
    tmpl = Template('{{forms|objects:"test"|filter_by:"blockdata_email"|filter_value:"b@localhost"|count}}')
    assert tmpl.render(context) == '6'
    tmpl = Template('{{forms|objects:"test"|filter_by:"blockdata_email"|filter_value:"c@localhost"|count}}')
    assert tmpl.render(context) == '0'
    tmpl = Template('{{forms|objects:"test"|filter_by:"blockdata_email"|exclude_value:"a@localhost"|count}}')
    assert tmpl.render(context) == '7'
    tmpl = Template('{{forms|objects:"test"|filter_by:"blockdata_email"|exclude_value:"b@localhost"|count}}')
    assert tmpl.render(context) == '8'
    tmpl = Template('{{forms|objects:"test"|filter_by:"blockdata_email"|exclude_value:"c@localhost"|count}}')
    assert tmpl.render(context) == '14'
    tmpl = Template('{{forms|objects:"test"|filter_by:"blockdata_email"|filter_value:none_value|count}}')
    assert tmpl.render(context) == '2'
    params = [
        ('equal', 'a@localhost', '7'),
        ('not_equal', 'a@localhost', '7'),
        ('in', 'a@localhost', '7'),
        ('in', 'a@localhost|b@localhost', '12'),
        ('not_in', 'a@localhost', '7'),
        ('not_in', 'a@localhost|b@localhost', '2'),
        ('absent', '', '2'),
        ('existing', '', '12'),
    ]
    for operator, value, result in params:
        if value:
            tmpl = Template(
                '{{forms|objects:"test"|filter_by:"blockdata_email"|%s|filter_value:"%s"|count}}'
                % (operator, value)
            )
        else:
            tmpl = Template('{{forms|objects:"test"|filter_by:"blockdata_date"|%s|count}}' % operator)
        assert tmpl.render(context) == result
    for operator in ['less_than', 'less_than_or_equal', 'greater_than', 'greater_than_or_equal', 'between']:
        LoggedError.wipe()
        tmpl = Template(
            '{{forms|objects:"test"|filter_by:"blockdata_email"|%s|filter_value:"plop"|count}}' % operator
        )
        assert tmpl.render(context) == '0'
        assert LoggedError.count() == 1
        logged_error = LoggedError.select(order_by='id')[0]
        assert logged_error.summary == 'Invalid operator "%s" for filter "blockdata_email"' % operator
    # text
    tmpl = Template('{{forms|objects:"test"|filter_by:"blockdata_text"|filter_value:"plop0"|count}}')
    assert tmpl.render(context) == '1'
    tmpl = Template('{{forms|objects:"test"|filter_by:"blockdata_text"|filter_value:"plop1"|count}}')
    assert tmpl.render(context) == '2'
    tmpl = Template('{{forms|objects:"test"|filter_by:"blockdata_text"|filter_value:"plop12"|count}}')
    assert tmpl.render(context) == '0'
    tmpl = Template('{{forms|objects:"test"|filter_by:"blockdata_text"|exclude_value:"plop0"|count}}')
    assert tmpl.render(context) == '13'
    tmpl = Template('{{forms|objects:"test"|filter_by:"blockdata_text"|exclude_value:"plop1"|count}}')
    assert tmpl.render(context) == '12'
    tmpl = Template('{{forms|objects:"test"|filter_by:"blockdata_text"|exclude_value:"plop12"|count}}')
    assert tmpl.render(context) == '14'
    tmpl = Template('{{forms|objects:"test"|filter_by:"blockdata_text"|filter_value:none_value|count}}')
    assert tmpl.render(context) == '2'
    params = [
        ('equal', 'plop5', '1'),
        ('not_equal', 'plop5', '13'),
        ('not_equal', 'plop1', '12'),
        ('less_than', 'plop5', '8'),
        ('less_than_or_equal', 'plop5', '9'),
        ('greater_than', 'plop5', '4'),
        ('greater_than_or_equal', 'plop5', '5'),
        ('in', 'plop5', '1'),
        ('in', 'plop5|plop4', '2'),
        ('not_in', 'plop5', '13'),
        ('not_in', 'plop5|plop4', '12'),
        ('absent', '', '2'),
        ('existing', '', '12'),
        ('between', 'plop1|plop5', '7'),
        ('between', 'plop5|plop1', '7'),
    ]
    for operator, value, result in params:
        if value:
            tmpl = Template(
                '{{forms|objects:"test"|filter_by:"blockdata_text"|%s|filter_value:"%s"|count}}'
                % (operator, value)
            )
        else:
            tmpl = Template('{{forms|objects:"test"|filter_by:"blockdata_text"|%s|count}}' % operator)
        assert tmpl.render(context) == result

    # file
    tmpl = Template('{{forms|objects:"test"|filter_by:"blockdata_file"|absent|count}}')
    assert tmpl.render(context) == '2'


def test_formdata_block_fields_absent_existing_filter(pub):
    BlockDef.wipe()
    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.StringField(id='1', label='String', varname='string'),
        fields.BoolField(id='2', label='Bool', varname='bool'),
        fields.DateField(id='3', label='Date', varname='date'),
        fields.NumericField(id='4', label='Num', varname='num'),
    ]
    block.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [
        fields.BlockField(id='0', label='Block Data', varname='bd', block_slug='foobar', max_items='3'),
    ]
    formdef.store()

    data_class = formdef.data_class()
    data_class.wipe()

    formdata1 = data_class()
    formdata1.just_created()
    formdata1.jump_status('new')
    formdata1.store()

    formdata2 = data_class()
    formdata2.data = {'0': {'data': [{}], 'schema': {}}}
    formdata2.just_created()
    formdata2.jump_status('new')
    formdata2.store()

    formdata3 = data_class()
    formdata3.data = {'0': {'data': [{'1': None, '2': None, '3': None, '4': None}], 'schema': {}}}
    formdata3.just_created()
    formdata3.jump_status('new')
    formdata3.store()

    formdata4 = data_class()
    formdata4.data = {'0': {'data': [{'1': '', '2': False, '3': '', '4': 0}], 'schema': {}}}
    formdata4.just_created()
    formdata4.jump_status('new')
    formdata4.store()

    formdata5 = data_class()
    formdata5.data = {'0': {'data': [{'1': '', '2': True, '3': '2024-01-01', '4': 1}], 'schema': {}}}
    formdata5.just_created()
    formdata5.jump_status('new')
    formdata5.store()

    formdata6 = data_class()
    formdata6.data = {'0': {'data': [{'1': 'x', '2': True, '3': '2024-01-01', '4': 2}], 'schema': {}}}
    formdata6.just_created()
    formdata6.jump_status('new')
    formdata6.store()

    formdata7 = data_class()
    formdata7.data = {'0': {'data': [{'1': 'x', '2': True, '3': '2024-01-01', '4': 2}, {}], 'schema': {}}}
    formdata7.just_created()
    formdata7.jump_status('new')
    formdata7.store()

    context = pub.substitutions.get_context_variables(mode='lazy')
    assert Template('{{forms|objects:"test"|filter_by:"bd_string"|existing|count}}').render(context) == '2'
    assert Template('{{forms|objects:"test"|filter_by:"bd_string"|absent|count}}').render(context) == '5'
    assert Template('{{forms|objects:"test"|filter_by:"bd_bool"|existing|count}}').render(context) == '4'
    assert Template('{{forms|objects:"test"|filter_by:"bd_bool"|absent|count}}').render(context) == '3'
    assert Template('{{forms|objects:"test"|filter_by:"bd_date"|existing|count}}').render(context) == '3'
    assert Template('{{forms|objects:"test"|filter_by:"bd_date"|absent|count}}').render(context) == '4'
    assert Template('{{forms|objects:"test"|filter_by:"bd_num"|existing|count}}').render(context) == '4'
    assert Template('{{forms|objects:"test"|filter_by:"bd_num"|absent|count}}').render(context) == '3'


def test_items_field_getlist(pub):
    NamedDataSource.wipe()
    data_source = NamedDataSource(name='foobar')
    data_source.data_source = {
        'type': 'jsonvalue',
        'value': json.dumps(
            [
                {'id': '1', 'text': 'un'},
                {'id': '2', 'text': 'deux'},
                {'id': '3', 'text': 'trois'},
                {'id': '4', 'text': 'quatre'},
            ]
        ),
    }
    data_source.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'foobar'
    formdef.fields = [
        fields.ItemsField(id='1', label='items', items=['aa', 'ab', 'ac'], varname='itemsfield'),
        fields.ItemsField(id='2', label='items2', varname='items2field', data_source={'type': 'foobar'}),
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {}
    formdef.fields[0].set_value(formdata.data, ['aa', 'ab'])
    formdef.fields[1].set_value(formdata.data, ['1', '3'])
    formdata.store()

    pub.substitutions.feed(formdata)
    context = pub.substitutions.get_context_variables(mode='lazy')
    tmpl = Template('{% for x in form_var_itemsfield|getlist:"text" %}{{x}},{% endfor %}')
    assert tmpl.render(context) == 'aa,ab,'

    tmpl = Template('{% for x in form_var_items2field|getlist:"text" %}{{x}},{% endfor %}')
    assert tmpl.render(context) == 'un,trois,'

    tmpl = Template('{{ form_var_items2field|getlistdict:"text, unknown" }}', autoescape=False)
    assert tmpl.render(context) == "[{'text': 'un', 'unknown': None}, {'text': 'trois', 'unknown': None}]"


def test_getlist_of_lazyformdata_field(pub):
    CardDef.wipe()
    carddef1 = CardDef()
    carddef1.name = 'Card 1'
    carddef1.fields = [
        fields.StringField(id='0', label='string', varname='name'),
    ]
    carddef1.store()
    ds = {'type': 'carddef:%s' % carddef1.url_name}
    carddef2 = CardDef()
    carddef2.name = 'Card 2'
    carddef2.fields = [fields.ItemField(id='0', label='card 1', varname='card1', data_source=ds)]
    carddef2.store()
    carddef1.data_class().wipe()
    carddef2.data_class().wipe()

    for i in range(3):
        carddata1 = carddef1.data_class()()
        carddata1.data = {
            '0': 'card1-%s' % i,
        }
        carddata1.just_created()
        carddata1.store()
        carddata2 = carddef2.data_class()()
        carddata2.data = {
            '0': str(carddata1.id),
        }
        carddata2.data['0_display'] = carddef2.fields[0].store_display_value(carddata2.data, '0')
        carddata2.data['0_structured'] = carddef2.fields[0].store_structured_value(carddata2.data, '0')
        carddata2.just_created()
        carddata2.store()

    context = pub.substitutions.get_context_variables(mode='lazy')
    tmpl = Template('{{ cards|objects:"card-2"|order_by:"id"|getlist:"form_var_card1_name"|join:"," }}')
    assert tmpl.render(context) == 'card1-0,card1-1,card1-2'
    tmpl = Template('{{ cards|objects:"card-2"|order_by:"id"|getlist:"form_var_card1_id"|join:"," }}')
    assert tmpl.render(context) == '1,2,3'


def test_getlist_of_relation_in_block_field(pub):
    BlockDef.wipe()
    CardDef.wipe()
    FormDef.wipe()

    carddef = CardDef()
    carddef.name = 'Card'
    carddef.fields = [
        fields.StringField(id='1', label='string', varname='str'),
        fields.NumericField(id='2', label='numeric', varname='num'),
    ]
    carddef.store()
    ds = {'type': 'carddef:%s' % carddef.url_name}

    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.ItemField(id='1', label='card', varname='card', data_source=ds),
    ]
    block.store()

    formdef = FormDef()
    formdef.name = 'test form'
    formdef.fields = [
        fields.BlockField(id='1', label='test', block_slug='foobar', max_items='3', varname='block'),
    ]
    formdef.store()

    carddef.data_class().wipe()
    formdef.data_class().wipe()

    carddatas = []
    for i in range(3):
        carddata = carddef.data_class()()
        carddata.data = {'1': str(i + 1), '2': i + 1}
        carddata.just_created()
        carddata.store()
        carddatas.append(carddata)

    # add empty data
    carddata = carddef.data_class()()
    carddata.data = {}
    carddata.just_created()
    carddata.store()
    carddatas.append(carddata)

    formdata = formdef.data_class()()
    formdata.data = {
        '1': {
            'data': [
                {'1': carddatas[0].id, '1_display': 'card'},
                {'1': carddatas[1].id, '1_display': 'card'},
                {'1': carddatas[2].id, '1_display': 'card'},
                {'1': carddatas[0].id, '1_display': 'card'},
                {'1': carddatas[3].id, '1_display': 'card'},
            ],
            'schema': {'1': 'item'},
        },
    }
    formdata.just_created()
    formdata.store()

    pub.substitutions.feed(formdata)
    context = pub.substitutions.get_context_variables(mode='lazy')
    tmpl = Template('{{ form_var_block|getlist:"card_live_var_str"|sum }}')
    assert tmpl.render(context) == '7'

    tmpl = Template('{{ form_var_block|getlist:"card_live_var_num"|sum }}')
    assert tmpl.render(context) == '7'

    with pytest.raises(TemplateError):
        tmpl = Template('{{ form_var_block|getlist:"card_live_var_plop"|sum }}', raises=True)
        tmpl.render(context)

    # getlist on empty block (NoneFieldVar) -> empty list
    formdata.data = {}
    LoggedError.wipe()
    pub.substitutions.feed(formdata)
    context = pub.substitutions.get_context_variables(mode='lazy')
    tmpl = Template('{{ form_var_block|getlist:"card_live_var_str"|sum }}')
    assert tmpl.render(context) == '0'
    assert LoggedError.count() == 0


def test_items_field_contains(pub):
    NamedDataSource.wipe()
    data_source = NamedDataSource(name='foobar')
    data_source.data_source = {
        'type': 'jsonvalue',
        'value': json.dumps(
            [
                {'id': '1', 'text': 'un'},
                {'id': '2', 'text': 'deux'},
                {'id': '3', 'text': 'trois'},
                {'id': '4', 'text': 'quatre'},
            ]
        ),
    }
    data_source.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'foobar'
    formdef.fields = [
        fields.ItemsField(id='1', label='items', items=['aa', 'ab', 'ac'], varname='itemsfield'),
        fields.ItemsField(id='2', label='items2', varname='items2field', data_source={'type': 'foobar'}),
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {}
    formdef.fields[0].set_value(formdata.data, ['aa', 'ab'])
    formdef.fields[1].set_value(formdata.data, ['1', '3'])
    formdata.store()

    pub.substitutions.feed(formdata)
    context = pub.substitutions.get_context_variables(mode='lazy')
    tmpl = Template('{% if "aa" in form_var_itemsfield %}ok{% endif %}')
    assert tmpl.render(context) == 'ok'
    tmpl = Template('{% if "xa" in form_var_itemsfield %}ok{% endif %}')
    assert tmpl.render(context) == ''

    tmpl = Template('{% if "1" in form_var_items2field %}ok{% endif %}')
    assert tmpl.render(context) == 'ok'
    tmpl = Template('{% if 1 in form_var_items2field %}ok{% endif %}')
    assert tmpl.render(context) == 'ok'
    tmpl = Template('{% if "un" in form_var_items2field %}ok{% endif %}')
    assert tmpl.render(context) == 'ok'
    tmpl = Template('{% if "8" in form_var_items2field %}ok{% endif %}')
    assert tmpl.render(context) == ''
    tmpl = Template('{% if 8 in form_var_items2field %}ok{% endif %}')
    assert tmpl.render(context) == ''
    tmpl = Template('{% if "huit" in form_var_items2field %}ok{% endif %}')
    assert tmpl.render(context) == ''


def test_attachment_part_path_migration(pub):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = []
    formdef.store()

    formdef.data_class().wipe()
    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.status = 'wf-new'
    formdata.evolution[-1].status = 'wf-new'
    formdata.evolution[-1].parts = [
        AttachmentEvolutionPart(
            'hello.txt', fp=io.BytesIO(b'test'), content_type='text/plain', varname='testfile'
        )
    ]
    formdata.store()
    assert formdata.evolution[-1].parts[0].filename.startswith('attachments/')
    assert os.path.exists(formdata.evolution[-1].parts[0].get_file_path())

    # add full path as it was done before
    formdata.evolution[-1].parts[0].filename = os.path.join(
        pub.app_dir, formdata.evolution[-1].parts[0].filename
    )
    formdata.store()

    # check it was converted to relative path
    formdata = formdef.data_class().get(formdata.id)
    assert formdata.evolution[-1].parts[0].filename.startswith('attachments/')

    # More realistic situation :
    # * an absolute path serialized to storage
    # * fetch the formadata
    # * store it again and check that the path is converted to a a relative path

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = []
    formdef.store()

    formdef.data_class().wipe()
    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.status = 'wf-new'
    formdata.evolution[-1].status = 'wf-new'
    attachment_evolution_part = AttachmentEvolutionPart(
        'hello.txt', fp=io.BytesIO(b'test'), content_type='text/plain', varname='testfile'
    )
    formdata.evolution[-1].parts = [attachment_evolution_part]

    # monkeypatch so that the filename is stored as an absolute path
    def get_state():
        odict = attachment_evolution_part.__dict__.copy()
        del odict['__getstate__']
        if not odict.get('fp'):
            if 'filename' not in odict:
                # we need a filename as an identifier: create one from nothing
                # instead of file_digest(self.fp) (see below)
                odict['filename'] = 'uuid-%s' % uuid.uuid4()
                attachment_evolution_part.filename = odict['filename']
            return odict

        del odict['fp']

        # there is no filename, or it was a temporary one: create it
        if 'filename' not in odict or odict['filename'].startswith('uuid-'):
            filename = file_digest(attachment_evolution_part.fp)
            # create subdirectory with digest prefix as name
            dirname = os.path.join('attachments', filename[:4])
            abs_dirname = os.path.join(get_publisher().app_dir, dirname)
            os.makedirs(abs_dirname, exist_ok=True)
            odict['filename'] = os.path.join(abs_dirname, filename)
            attachment_evolution_part.filename = odict['filename']
            attachment_evolution_part.fp.seek(0)
            atomic_write(attachment_evolution_part.get_file_path(), attachment_evolution_part.fp)

        return odict

    attachment_evolution_part.__getstate__ = get_state

    formdata.store()
    # check that the path is absolute
    assert formdata.evolution[-1].parts[0].filename.startswith(pub.APP_DIR)
    assert os.path.exists(formdata.evolution[-1].parts[0].get_file_path())

    # get a fresh instance not monkeypatched
    formdata = formdef.data_class().get(formdata.id)
    formdata.store()

    # check it was converted to relative path
    formdata = formdef.data_class().get(formdata.id)
    assert formdata.evolution[-1].parts[0].filename.startswith('attachments/')


def test_merged_roles_dict_compat(pub, local_user):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = []
    formdef.workflow_roles = {'_receiver': 2}
    formdef.store()

    formdef.data_class().wipe()
    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.workflow_roles = {'_receiver': 2}
    formdata.store()


def test_fts_phone(pub):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [
        fields.StringField(id='1', label='phone', validation={'type': 'phone'}),
        fields.StringField(id='2', label='other'),
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {'1': '01 23 45 67 89', '2': 'foo'}
    formdata.just_created()
    formdata.store()

    formdata = formdef.data_class()()
    formdata.data = {'1': None, '2': '123456789'}
    formdata.just_created()
    formdata.store()

    formdata = formdef.data_class()()
    formdata.data = {'1': None, '2': '0123456789'}
    formdata.just_created()
    formdata.store()

    assert formdef.data_class().count([FtsMatch('01 23 45 67 89')]) == 2
    assert formdef.data_class().count([FtsMatch('0123456789')]) == 2
    assert formdef.data_class().count([FtsMatch('+33123456789')]) == 2
    assert formdef.data_class().count([FtsMatch('+33(0)123456789')]) == 2
    assert formdef.data_class().count([FtsMatch('+33(0)123456789 foo')]) == 1
    assert formdef.data_class().count([FtsMatch('+33(0)123456789 bar')]) == 0
    assert formdef.data_class().count([FtsMatch('foo +33(0)123456789')]) == 1
    assert formdef.data_class().count([FtsMatch('bar +33(0)123456789')]) == 0
    assert formdef.data_class().count([FtsMatch('123456789')]) == 1

    formdata.data = {'1': '+32 2 345 67 89', '2': 'foo'}
    formdata.store()
    assert formdef.data_class().count([FtsMatch('023456789')]) == 0

    pub.load_site_options()
    if not pub.site_options.has_section('options'):
        pub.site_options.add_section('options')
    pub.site_options.set('options', 'local-region-code', 'BE')
    assert formdef.data_class().count([FtsMatch('023456789')]) == 1


def test_fts_display_id(pub):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = []
    formdef.id = '123'
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {}
    formdata.id = '4567'
    formdata.just_created()
    formdata.store()

    assert formdef.data_class().count([FtsMatch('123-4567')]) == 1


def test_get_visible_status(pub, local_user):
    Workflow.wipe()
    workflow = Workflow(name='test')
    st_new = workflow.add_status('New')
    st_finished = workflow.add_status('Finished')
    workflow.store()

    formdef = FormDef()
    formdef.workflow_id = workflow.id
    formdef.name = 'foo'
    formdef.fields = []
    formdef.store()
    formdef.data_class().wipe()

    formdata = formdef.data_class()()
    formdata.evolution = []

    # create evolution [new, empty, finished, empty]
    for status in (st_new, None, st_finished, None):
        evo = Evolution(formdata=formdata)
        evo.time = localtime()
        if status:
            evo.status = 'wf-%s' % status.id
        formdata.evolution.append(evo)

    formdata.store()

    # check visible status is "Finished"
    formdata = FormDef.get(formdef.id).data_class().get(formdata.id)
    assert formdata.get_visible_status(user=None).name == 'Finished'

    # mark finished as not visible
    st_finished.visibility = ['unknown']
    workflow.store()

    # check "New" is now returned as visible status
    formdata = FormDef.get(formdef.id).data_class().get(formdata.id)
    assert formdata.get_visible_status(user=None).name == 'New'
    assert formdata.get_visible_status(user=local_user).name == 'New'

    # check with user from session
    pub._request._user = local_user
    assert formdata.get_visible_status().name == 'New'

    # check from backoffice
    pub._request.environ['PATH_INFO'] = 'backoffice/test/'
    assert formdata.get_visible_status().name == 'New'

    # check admin in backoffice gets the real status
    local_user.is_admin = True
    local_user.store()
    assert formdata.get_visible_status().name == 'Finished'

    # check another user in backoffice gets "New"
    assert formdata.get_visible_status(user=None).name == 'New'

    # check admin in front also gets "New"
    pub._request.environ['PATH_INFO'] = ''
    assert formdata.get_visible_status().name == 'New'


def test_rst_form_details_empty_titles(pub):
    pub._set_request(None)  # like in a cron job

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'foo-export-details'
    formdef.fields = [
        fields.PageField(id='1', label='Page 1'),
        fields.TitleField(id='2', label='Title1'),
        fields.SubtitleField(id='3', label='Subtitle1'),
        fields.StringField(id='4', label='String1'),
        fields.TitleField(id='5', label='Title2'),
        fields.SubtitleField(id='6', label='Subtitle2'),
        fields.StringField(id='7', label='String2'),
        fields.PageField(id='8', label='Page 2'),
        fields.StringField(id='9', label='String3'),
        fields.PageField(id='10', label='Page 3'),
        fields.StringField(id='11', label='String4'),
        fields.PageField(id='12', label='Page 4'),
        fields.TitleField(id='13', label='Title3'),
        fields.CommentField(id='14', label='Comment1', display_locations=['summary']),
        fields.TitleField(id='15', label='Title4'),
        fields.StringField(id='16', label='String5'),
        fields.SubtitleField(id='17', label='Subtitle3'),
    ]
    formdef.store()
    formdef.data_class().wipe()
    formdata = formdef.data_class()()
    formdata.data = {
        '7': 'string2',
        '11': 'string4',
        '16': 'string5',
    }
    formdata.just_created()
    formdata.store()

    rst = formdata.get_rst_summary()
    assert 'Title1' not in rst
    assert 'Subtitle1' not in rst
    assert 'Title2' in rst
    assert 'Subtitle2' in rst
    assert 'string2' in rst
    assert 'Page 2' not in rst
    assert 'Page 3' in rst
    assert 'Page 4' in rst
    assert 'Title3' in rst
    assert 'Title4' in rst
    assert 'Comment1' in rst
    assert 'Subtitle3' not in rst


def test_rst_form_details_html_entities_in_comment(pub):
    pub._set_request(None)  # like in a cron job

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'foo-export-details'
    formdef.fields = [
        fields.StringField(id='7', label='String2'),
        fields.CommentField(id='14', label='<p>&eacute;l&eacute;phant</p>', display_locations=['summary']),
    ]
    formdef.store()
    formdef.data_class().wipe()
    formdata = formdef.data_class()()
    formdata.data = {'7': 'string2'}
    formdata.just_created()
    formdata.store()

    rst = formdata.get_rst_summary()
    assert '\néléphant\n' in rst


def test_rst_form_details_all_fields(pub):
    pub._set_request(None)  # like in a cron job

    BlockDef.wipe()
    FormDef.wipe()

    block = BlockDef()
    block.name = 'foobar'
    block.digest_template = 'X{{foobar_var_foo}}Y'
    block.fields = [
        fields.StringField(id='123', required='required', label='Test', varname='foo'),
        fields.StringField(id='234', required='required', label='Test2', varname='bar'),
    ]
    block.store()

    formdef = FormDef()
    formdef.name = 'foo-export-details'
    formdef.fields = [
        fields.PageField(id='1', label='Page 1'),
        fields.TitleField(id='2', label='Title'),
        fields.SubtitleField(id='3', label='Subtitle'),
        fields.StringField(id='4', label='String', varname='string'),
        fields.EmailField(id='5', label='Email'),
        fields.TextField(id='6', label='Text'),
        fields.MapField(id='7', label='Map'),
        fields.BoolField(id='8', label='Bool'),
        fields.FileField(id='9', label='File'),
        fields.DateField(id='10', label='Date'),
        fields.ItemField(id='11', label='Item', items=['foo', 'bar']),
        fields.TableField(id='12', label='Table', columns=['a', 'b'], rows=['c', 'd']),
        fields.StringField(id='15', label='Empty String', varname='invisiblestr'),
        fields.BlockField(id='16', label='Block Field', block_slug='foobar'),
        fields.PageField(id='13', label='Empty Page'),
        fields.TitleField(id='14', label='Empty Title'),
        fields.ItemsField(id='17', label='Items', items=['foo', 'bar']),
    ]
    formdef.store()
    formdef.data_class().wipe()
    upload = PicklableUpload('test.jpeg', 'image/jpeg')
    with open(os.path.join(os.path.dirname(__file__), 'image-with-gps-data.jpeg'), 'rb') as fd:
        upload.receive([fd.read()])
    formdata = formdef.data_class()()
    formdata.data = {
        '4': 'string',
        '5': 'foo@localhost',
        '6': 'para1\npara2',
        '7': {'lat': 2, 'lon': 4},  # map
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
    }
    formdata.just_created()
    formdata.store()

    rst = formdata.get_rst_summary()
    assert '**Subtitle**\n\n' in rst
    assert 'Text:\n  para1\n  para2' in rst
    assert 'File:\n  test.jpeg' in rst
    assert 'Block Field:\n' in rst
    assert 'Test:\n  foo\n' in rst
    assert 'Test:\n  foo2\n' in rst
    assert 'Map:\n  2;4\n\n' in rst


def test_rst_form_details_label_with_colon(pub):
    pub._set_request(None)  # like in a cron job

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'foo-export-details'
    formdef.fields = [
        fields.StringField(id='4', label='String1:'),
    ]
    formdef.store()
    formdef.data_class().wipe()
    formdata = formdef.data_class()()
    formdata.data = {
        '4': 'string1',
    }
    formdata.just_created()
    formdata.store()

    rst = formdata.get_rst_summary()
    assert 'String1:' in rst  # no double :


def test_form_details_lazy_object(pub):
    pub._set_request(None)  # like in a cron job

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'foo-export-details'
    formdef.fields = [
        fields.StringField(id='4', label='String1:'),
    ]
    formdef.store()
    formdef.data_class().wipe()
    formdata = formdef.data_class()()
    formdata.data = {
        '4': 'string1',
    }
    formdata.just_created()
    formdata.store()

    form_details = formdata.get_form_details()

    # check __nonzero__
    assert bool(form_details) is True

    # check __contains__
    assert 'String1' in form_details

    # check .replace()
    assert 'STRING1' in form_details.replace('String1', 'STRING1')


def test_rst_form_details_required_for_frontoffice(pub):
    pub._set_request(None)  # like in a cron job

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'foo-export-details'
    formdef.fields = [
        fields.StringField(id='4', label='String1', required='frontoffice'),
        fields.StringField(id='5', label='String2'),
    ]
    formdef.store()
    formdef.data_class().wipe()
    formdata = formdef.data_class()()
    formdata.data = {
        '5': 'string2',
    }
    formdata.just_created()
    formdata.store()

    rst = formdata.get_rst_summary()
    assert 'string2' in rst


def test_get_status_datetime(pub, freezer):
    Workflow.wipe()
    workflow = Workflow(name='test')
    st_new = workflow.add_status('New', 'new')
    st_next = workflow.add_status('Next', 'next')
    workflow.store()

    formdef = FormDef()
    formdef.workflow_id = workflow.id
    formdef.name = 'foo'
    formdef.fields = []
    formdef.store()
    formdef.data_class().wipe()

    freezer.move_to(datetime.datetime(2023, 10, 31, 10, 0))
    formdata = formdef.data_class()()
    formdata.just_created()

    assert formdata.get_status_datetime(status=st_new) == formdata.evolution[0].time

    freezer.move_to(datetime.datetime(2023, 10, 31, 11, 0))
    formdata.jump_status('next')
    assert formdata.get_status_datetime(status=st_next) == formdata.evolution[1].time

    freezer.move_to(datetime.datetime(2023, 10, 31, 12, 0))
    evo = Evolution(formdata=formdata)
    evo.time = localtime()
    formdata.evolution.append(evo)
    assert formdata.get_status_datetime(status=st_next) == formdata.evolution[1].time
    assert formdata.get_status_datetime(status=st_next, latest=True) == formdata.evolution[1].time

    freezer.move_to(datetime.datetime(2023, 10, 31, 13, 0))
    formdata.jump_status('new')
    assert formdata.get_status_datetime(status=st_new) == formdata.evolution[0].time
    assert formdata.get_status_datetime(status=st_new, latest=True) == formdata.evolution[-1].time


def test_page_field_var(pub, formdef):
    formdef.fields = [fields.PageField(id='1', label='page', varname='page')]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {}
    formdata.store()

    assert 'form_var_page' not in formdata.get_substitution_variables()
    assert 'page' not in LazyFormData(formdata).var.inspect_keys()


def test_reverse_links(pub):
    CardDef.wipe()
    carddef1 = CardDef()
    carddef1.name = 'Card 1'
    carddef1.digest_templates = {'default': '{{form_var_name1}}'}
    carddef1.fields = [
        fields.StringField(id='0', label='string', varname='name1'),
    ]
    carddef1.store()
    carddata1 = carddef1.data_class()()
    carddata1.data = {
        '0': 'foo1',
    }
    carddata1.just_created()
    carddata1.store()

    ds = {'type': 'carddef:%s' % carddef1.url_name}

    carddef2 = CardDef()
    carddef2.name = 'Card 2'
    carddef2.digest_templates = {'default': '{{form_var_name2}}'}
    carddef2.fields = [
        fields.StringField(id='0', label='string', varname='name2'),
        fields.ItemField(id='1', label='string', varname='foo', data_source=ds),
    ]
    carddef2.store()
    carddata2 = carddef2.data_class()()
    carddata2.data = {
        '0': 'foo2',
        '1': str(carddata1.id),
    }
    carddata2.data['1_display'] = carddef2.fields[1].store_display_value(carddata1.data, '0')
    carddata2.data['1_structured'] = carddef2.fields[1].store_structured_value(carddata1.data, '0')
    carddata2.just_created()
    carddata2.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'foobar'
    formdef.fields = [fields.ItemField(id='0', label='string', varname='bar', data_source=ds)]
    formdef.store()
    formdata1 = formdef.data_class()()
    formdata1.data = {
        '0': str(carddata1.id),
    }
    formdata1.data['0_display'] = formdef.fields[0].store_display_value(carddata1.data, '0')
    formdata1.data['0_structured'] = formdef.fields[0].store_structured_value(carddata1.data, '0')
    formdata1.just_created()
    formdata1.store()
    formdata2 = formdef.data_class()()
    formdata2.data = {
        '0': str(carddata1.id),
    }
    formdata2.data['0_display'] = formdef.fields[0].store_display_value(carddata1.data, '0')
    formdata2.data['0_structured'] = formdef.fields[0].store_structured_value(carddata1.data, '0')
    formdata2.just_created()
    formdata2.store()

    # test reverse relation
    carddef1.store()  # build & store reverse_relations
    pub.reset_caches()
    pub.substitutions.reset()
    pub.substitutions.feed(pub)
    pub.substitutions.feed(carddef1)
    pub.substitutions.feed(carddata1)
    context = pub.substitutions.get_context_variables(mode='lazy')
    assert len(context['form_reverse_links_formdef_foobar_bar']) == 2
    assert context['form_reverse_links_formdef_foobar_bar_0_form_internal_id'] == formdata1.id
    assert context['form_reverse_links_formdef_foobar_bar_1_form_internal_id'] == formdata2.id
    assert len(context['form_reverse_links_carddef_card_2_foo']) == 1
    assert context['form_reverse_links_carddef_card_2_foo_0_form_internal_id'] == carddata2.id

    # test with natural id
    carddef1.id_template = 'X{{ form_var_name1 }}Y'
    carddef1.store()
    pub.reset_caches()
    carddata1.store()
    assert carddata1.id_display == 'Xfoo1Y'
    carddata2.data['1'] = carddata1.get_natural_key()
    carddata2.store()
    formdata1.data['0'] = carddata1.get_natural_key()
    formdata1.just_created()
    formdata1.store()
    formdata2 = formdef.data_class()()
    formdata2.data['0'] = carddata1.get_natural_key()
    formdata2.just_created()
    formdata2.store()

    pub.substitutions.reset()
    pub.substitutions.feed(pub)
    pub.substitutions.feed(carddef1)
    pub.substitutions.feed(carddata1)
    context = pub.substitutions.get_context_variables(mode='lazy')
    assert len(context['form_reverse_links_formdef_foobar_bar']) == 2
    assert context['form_reverse_links_formdef_foobar_bar_0_form_internal_id'] == formdata1.id
    assert context['form_reverse_links_formdef_foobar_bar_1_form_internal_id'] == formdata2.id
    assert len(context['form_reverse_links_carddef_card_2_foo']) == 1
    assert context['form_reverse_links_carddef_card_2_foo_0_form_internal_id'] == carddata2.id


def test_no_short_url(pub):
    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'card'
    carddef.store()
    carddata = carddef.data_class()()
    carddata.just_created()
    carddata.store()

    lazy_carddata = LazyFormData(carddata)
    assert 'short_url' not in lazy_carddata.inspect_keys()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'form'
    formdef.store()
    formdata = formdef.data_class()()

    lazy_formdata = LazyFormData(formdata)
    assert 'short_url' not in lazy_formdata.inspect_keys()

    formdata.just_created()
    formdata.store()
    assert 'short_url' in lazy_formdata.inspect_keys()


def test_refresh_from_storage_if_updated(pub, formdef):
    data_class = formdef.data_class()
    dummy = data_class().store()
    formdata1 = data_class()
    formdata1.just_created()
    formdata1.store()

    assert not formdata1.refresh_from_storage_if_updated()

    formdata2 = data_class.get(formdata1.id)

    assert not formdata2.refresh_from_storage_if_updated()

    formdata2.jump_status('accepted')

    assert formdata1.refresh_from_storage_if_updated()

    formdata1.jump_status('finished')

    assert formdata2.refresh_from_storage_if_updated()

    assert not formdata1.refresh_from_storage_if_updated()
    assert not formdata1.refresh_from_storage_if_updated()


def test_unblock_stalled_formdata(pub, formdef):
    LoggedError.wipe()

    formdata1 = formdef.data_class()()
    formdata1.just_created()
    formdata1.workflow_processing_timestamp = now()
    formdata1.store()

    formdata2 = formdef.data_class()()
    formdata2.just_created()
    formdata2.workflow_processing_timestamp = now() - datetime.timedelta(hours=3)
    formdata2.workflow_processing_afterjob_id = 23
    formdata2.store()

    formdata3 = formdef.data_class()()
    formdata3.just_created()
    formdata3.store()

    pub.check_stalled_formdata()
    formdata1.refresh_from_storage()
    assert formdata1.workflow_processing_timestamp

    formdata2.refresh_from_storage()
    assert not formdata2.workflow_processing_timestamp
    assert not formdata2.workflow_processing_afterjob_id

    traces = WorkflowTrace.select_for_formdata(formdata2)
    assert traces[-1].event == 'unstall'
    assert LoggedError.count() == 1
    assert LoggedError.select()[0].summary == 'Stalled processing'


def test_formdata_evolution_repr(pub, formdef):
    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()

    assert repr(formdata.evolution[0]) == '<Evolution id:1 in status "Just Submitted" (just_submitted)>'
