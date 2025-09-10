import datetime
import glob
import io
import json
import os
from unittest import mock

import pytest

from wcs import fields
from wcs.admin.settings import UserFieldsFormDef
from wcs.applications import Application
from wcs.blocks import BlockDef
from wcs.carddef import CardDef
from wcs.fields import DateField, ItemField, StringField
from wcs.formdef import FormDef
from wcs.formdef_base import get_formdefs_of_all_kinds
from wcs.formdef_jobs import update_storage_all_formdefs
from wcs.qommon.http_request import HTTPRequest
from wcs.qommon.upload_storage import PicklableUpload
from wcs.variables import LazyFormDef
from wcs.wf.form import FormWorkflowStatusItem, WorkflowFormEvolutionPart, WorkflowFormFieldsFormDef
from wcs.workflows import (
    AttachmentEvolutionPart,
    ContentSnapshotPart,
    Workflow,
    WorkflowBackofficeFieldsFormDef,
    WorkflowVariablesFieldsFormDef,
)

from .utilities import clean_temporary_pub, create_temporary_pub


@pytest.fixture
def pub():
    pub = create_temporary_pub()
    req = HTTPRequest(None, {'SCRIPT_NAME': '/', 'SERVER_NAME': 'example.net'})
    pub.set_app_dir(req)
    pub.cfg['language'] = {'language': 'en'}
    pub.write_cfg()

    return pub


def teardown_module(module):
    clean_temporary_pub()


def test_is_disabled(pub):
    formdef = FormDef()
    assert not formdef.is_disabled()

    formdef.disabled = True
    assert formdef.is_disabled()


def test_is_disabled_publication_date(pub):
    formdef = FormDef()

    formdef.publication_date = (
        '%s-%02d-%02d' % (datetime.datetime.today() - datetime.timedelta(1)).timetuple()[:3]
    )
    assert not formdef.is_disabled()

    formdef.publication_date = (
        '%s-%02d-%02d' % (datetime.datetime.today() + datetime.timedelta(1)).timetuple()[:3]
    )
    assert formdef.is_disabled()


def test_is_disabled_expiration_date(pub):
    formdef = FormDef()

    formdef.expiration_date = (
        '%s-%02d-%02d' % (datetime.datetime.today() - datetime.timedelta(1)).timetuple()[:3]
    )
    assert formdef.is_disabled()

    formdef.expiration_date = (
        '%s-%02d-%02d' % (datetime.datetime.today() + datetime.timedelta(1)).timetuple()[:3]
    )
    assert not formdef.is_disabled()


def test_is_disabled_publication_datetime(pub):
    formdef = FormDef()

    formdef.publication_date = (
        '%s-%02d-%02d %02d:%02d' % (datetime.datetime.now() - datetime.timedelta(hours=1)).timetuple()[:5]
    )
    assert not formdef.is_disabled()

    formdef.publication_date = (
        '%s-%02d-%02d %02d:%02d' % (datetime.datetime.now() + datetime.timedelta(hours=1)).timetuple()[:5]
    )
    assert formdef.is_disabled()


def test_is_disabled_expiration_datetime(pub):
    formdef = FormDef()

    formdef.expiration_date = (
        '%s-%02d-%02d %02d:%02d' % (datetime.datetime.now() - datetime.timedelta(hours=1)).timetuple()[:5]
    )
    assert formdef.is_disabled()

    formdef.expiration_date = (
        '%s-%02d-%02d %02d:%02d' % (datetime.datetime.now() + datetime.timedelta(hours=1)).timetuple()[:5]
    )
    assert not formdef.is_disabled()


def test_title_change(pub):
    formdef = FormDef()
    formdef.name = 'foo'
    formdef.store()
    assert FormDef.get(formdef.id).name == 'foo'
    assert FormDef.get(formdef.id).url_name == 'foo'
    assert FormDef.get(formdef.id).table_name == f'formdata_{formdef.id}_foo'

    # makes sure the table_name never changes
    formdef.name = 'bar'
    formdef.store()
    assert FormDef.get(formdef.id).name == 'bar'
    assert FormDef.get(formdef.id).url_name == 'foo'
    assert FormDef.get(formdef.id).table_name == f'formdata_{formdef.id}_foo'  # didn't change

    formdef.data_class()().store()
    formdef.name = 'baz'
    formdef.store()
    assert FormDef.get(formdef.id).name == 'baz'
    assert FormDef.get(formdef.id).table_name == f'formdata_{formdef.id}_foo'  # didn't change


def test_overlong_slug(pub):
    formdef = FormDef()
    formdef.name = 'foo' + 'a' * 500
    formdef.store()


def test_substitution_variables(pub):
    formdef = FormDef()
    formdef.name = 'foo'
    formdef.store()

    wf = Workflow(name='variables')
    wf.variables_formdef = WorkflowVariablesFieldsFormDef(workflow=wf)
    wf.variables_formdef.fields = [
        StringField(id='1', label='Test', varname='foo'),
        ItemField(id='2', label='Test Liste', varname='bar'),
    ]
    wf.store()
    formdef.workflow_id = wf.id

    assert 'form_name' in formdef.get_substitution_variables()
    assert formdef.get_substitution_variables()['form_name'] == 'foo'
    formdef.workflow_options = {'foo': 'bar'}
    assert 'form_option_foo' in formdef.get_substitution_variables()
    assert formdef.get_substitution_variables()['form_option_foo'] == 'bar'

    formdef.workflow_options = {'bar': 'bar', 'bar_display': 'Bar'}
    assert formdef.get_substitution_variables()['form_option_bar'] == 'Bar'
    assert formdef.get_substitution_variables()['form_option_bar_raw'] == 'bar'


def test_urls(pub):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'foo'
    formdef.store()
    assert formdef.get_url() == 'http://example.net/foo/'
    assert formdef.get_url(backoffice=True) == 'http://example.net/backoffice/management/foo/'
    del pub.cfg['misc']['frontoffice-url']
    assert formdef.get_url() == 'https://example.net/foo/'
    assert formdef.get_url(backoffice=True) == 'https://example.net/backoffice/management/foo/'


def test_schema_with_date_variable(pub):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'foo'
    formdef.store()

    wf = Workflow(name='variables')
    wf.variables_formdef = WorkflowVariablesFieldsFormDef(workflow=wf)
    wf.variables_formdef.fields.append(DateField(label='Test', varname='foo'))
    wf.store()
    formdef.workflow_id = wf.id
    formdef.workflow_options = {'foo': datetime.datetime(2016, 4, 2).timetuple()}
    assert json.loads(formdef.export_to_json())['options']['foo'] == '2016-04-02'


def test_substitution_variables_object(pub):
    formdef = FormDef()
    formdef.name = 'foo'
    formdef.store()
    formdef.data_class().wipe()

    assert 'form_objects' in formdef.get_substitution_variables()
    substs = formdef.get_substitution_variables().get('form_objects')
    assert substs.count == 0
    assert substs.count_status_1 == 0

    d = formdef.data_class()()
    d.status = 'wf-1'
    d.store()
    substs = formdef.get_substitution_variables().get('form_objects')
    assert substs.count == 1
    assert substs.count_status_1 == 1

    with pytest.raises(AttributeError):
        assert substs.foobar

    assert substs.formdef is formdef


def test_unused_file_removal_job(pub):
    from wcs.formdef_jobs import clean_unused_files

    BlockDef.wipe()
    FormDef.wipe()

    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.FileField(id='234', required='required', label='Test2'),
    ]
    block.store()

    formdef = FormDef()
    formdef.name = 'removal job'
    formdef.fields = [
        fields.FileField(id='5', label='file', varname='filefield'),
        fields.BlockField(id='6', label='test', block_slug='foobar', max_items='3'),
    ]
    formdef.store()
    formdef.data_class().wipe()

    if not pub.site_options.has_section('options'):
        pub.site_options.add_section('options')
    pub.site_options.add_section('storage-remote')
    pub.site_options.set('storage-remote', 'label', 'remote')
    pub.site_options.set('storage-remote', 'class', 'wcs.qommon.upload_storage.RemoteOpaqueUploadStorage')
    pub.site_options.set('storage-remote', 'ws', 'https://crypto.example.net/')

    for behaviour in (None, 'move', 'remove'):
        if behaviour:
            pub.site_options.set('options', 'unused-files-behaviour', behaviour)

        formdata = formdef.data_class()()
        formdata.data = {
            '5': PicklableUpload('test.txt', 'text/plain'),
        }
        formdata.data['5'].receive([b'hello world'])
        formdata.just_created()
        formdata.store()
        assert isinstance(formdata.evolution[0].parts[0], ContentSnapshotPart)
        assert formdata.evolution[0].parts[0].new_data['5'].qfilename == formdata.data['5'].qfilename

        assert formdata.data['5'].qfilename in os.listdir(os.path.join(pub.app_dir, 'uploads'))
        clean_unused_files(pub)
        assert os.listdir(os.path.join(pub.app_dir, 'uploads')) == [formdata.data['5'].qfilename]
        formdata.anonymise()
        clean_unused_files(pub)
        assert os.listdir(os.path.join(pub.app_dir, 'uploads')) == []

        for _ in range(5):
            formdata = formdef.data_class()()
            formdata.data = {
                '5': PicklableUpload('test.txt', 'text/plain'),
            }
            formdata.data['5'].receive([b'hello world'])
            formdata.just_created()
            formdata.store()
            assert isinstance(formdata.evolution[0].parts[0], ContentSnapshotPart)
            assert formdata.evolution[0].parts[0].new_data['5'].qfilename == formdata.data['5'].qfilename

        # same file, deduplicated
        assert os.listdir(os.path.join(pub.app_dir, 'uploads')) == [formdata.data['5'].qfilename]
        formdata.anonymise()
        clean_unused_files(pub)
        assert len(os.listdir(os.path.join(pub.app_dir, 'uploads'))) == 1
        for formdata in formdef.data_class().select():
            formdata.anonymise()
        assert len(os.listdir(os.path.join(pub.app_dir, 'uploads'))) == 1
        clean_unused_files(pub)
        assert os.listdir(os.path.join(pub.app_dir, 'uploads')) == []

        # file referenced in formdata history, but not in formdata's data
        formdata = formdef.data_class()()
        formdata.data = {
            '5': PicklableUpload('test.txt', 'text/plain'),
        }
        formdata.data['5'].receive([b'hello world'])
        formdata.just_created()
        formdata.store()
        assert isinstance(formdata.evolution[0].parts[0], ContentSnapshotPart)
        assert formdata.evolution[0].parts[0].new_data['5'].qfilename == formdata.data['5'].qfilename
        qfilename = formdata.data['5'].qfilename
        formdata.data['5'] = None
        formdata.store()
        assert formdata.evolution[0].parts[0].new_data['5'].qfilename == qfilename
        assert os.listdir(os.path.join(pub.app_dir, 'uploads')) == [qfilename]
        clean_unused_files(pub)
        assert len(os.listdir(os.path.join(pub.app_dir, 'uploads'))) == 1
        formdata.anonymise()
        assert len(formdata.evolution) == 1
        assert formdata.evolution[0].parts is None
        clean_unused_files(pub)
        assert os.listdir(os.path.join(pub.app_dir, 'uploads')) == []

        # file referenced in formdef option
        workflow = Workflow(name='variables')

        workflow.variables_formdef = WorkflowVariablesFieldsFormDef(workflow=workflow)
        workflow.variables_formdef.fields.append(fields.FileField(id='1', label='Test'))
        workflow.add_status('Status1', 'st1')
        workflow.store()
        formdef.workflow = workflow
        formdef.workflow_options = {'1': PicklableUpload('test.txt', 'text/plain')}
        formdef.workflow_options['1'].receive([b'hello world'])
        formdef.store()

        formdata = formdef.data_class()()
        formdata.data = {
            '5': PicklableUpload('test.txt', 'text/plain'),
        }
        formdata.data['5'].receive([b'hello world'])
        formdata.just_created()
        formdata.store()

        assert len(os.listdir(os.path.join(pub.app_dir, 'uploads'))) == 1
        clean_unused_files(pub)
        formdata.remove_self()
        clean_unused_files(pub)
        assert len(os.listdir(os.path.join(pub.app_dir, 'uploads'))) == 1

        formdef.workflow_options = {}
        formdef.store()
        clean_unused_files(pub)
        assert len(os.listdir(os.path.join(pub.app_dir, 'uploads'))) == 0

        # file in block field
        formdata = formdef.data_class()()
        formdata.data = {
            '6': {
                'data': [
                    {'234': PicklableUpload('test.txt', 'text/plain')},
                    {'234': PicklableUpload('test2.txt', 'text/plain')},
                ],
                'schema': {'234': 'file'},
            },
        }
        formdata.data['6']['data'][0]['234'].receive([b'hello world'])
        formdata.data['6']['data'][1]['234'].receive([b'hello world block'])
        formdata.workflow_data = {'wscall': {'data': ['not', 'a', 'block'], 'err': 0}}
        formdata.just_created()
        formdata.store()
        assert isinstance(formdata.evolution[0].parts[0], ContentSnapshotPart)
        assert (
            formdata.evolution[0].parts[0].new_data['6']['data'][0]['234'].qfilename
            == formdata.data['6']['data'][0]['234'].qfilename
        )
        assert (
            formdata.evolution[0].parts[0].new_data['6']['data'][1]['234'].qfilename
            == formdata.data['6']['data'][1]['234'].qfilename
        )
        assert len(os.listdir(os.path.join(pub.app_dir, 'uploads'))) == 2
        clean_unused_files(pub)
        assert len(os.listdir(os.path.join(pub.app_dir, 'uploads'))) == 2
        formdata.remove_self()
        clean_unused_files(pub)
        assert len(os.listdir(os.path.join(pub.app_dir, 'uploads'))) == 0

        # block field: file referenced in formdata history, but not in formdata's data
        formdata = formdef.data_class()()
        formdata.data = {
            '6': {
                'data': [
                    {'234': PicklableUpload('test.txt', 'text/plain')},
                    {'234': PicklableUpload('test2.txt', 'text/plain')},
                ],
                'schema': {'234': 'file'},
            },
        }
        formdata.data['6']['data'][0]['234'].receive([b'hello world'])
        formdata.data['6']['data'][1]['234'].receive([b'hello world block'])
        formdata.just_created()
        formdata.store()
        assert isinstance(formdata.evolution[0].parts[0], ContentSnapshotPart)
        assert (
            formdata.evolution[0].parts[0].new_data['6']['data'][0]['234'].qfilename
            == formdata.data['6']['data'][0]['234'].qfilename
        )
        assert (
            formdata.evolution[0].parts[0].new_data['6']['data'][1]['234'].qfilename
            == formdata.data['6']['data'][1]['234'].qfilename
        )
        qfilename0 = formdata.data['6']['data'][0]['234'].qfilename
        qfilename1 = formdata.data['6']['data'][1]['234'].qfilename
        formdata.data['6'] = {}
        formdata.store()
        assert formdata.evolution[0].parts[0].new_data['6']['data'][0]['234'].qfilename == qfilename0
        assert formdata.evolution[0].parts[0].new_data['6']['data'][1]['234'].qfilename == qfilename1
        assert set(os.listdir(os.path.join(pub.app_dir, 'uploads'))) == {qfilename0, qfilename1}
        clean_unused_files(pub)
        assert len(os.listdir(os.path.join(pub.app_dir, 'uploads'))) == 2
        assert set(os.listdir(os.path.join(pub.app_dir, 'uploads'))) == {qfilename0, qfilename1}
        formdata.anonymise()
        assert len(formdata.evolution) == 1
        assert formdata.evolution[0].parts is None
        clean_unused_files(pub)
        assert len(os.listdir(os.path.join(pub.app_dir, 'uploads'))) == 0

        # non local storage: nothing happens
        formdata = formdef.data_class()()
        formdata.data = {
            '5': PicklableUpload('test.txt', 'text/plain'),
        }
        formdata.data['5'].receive([b'hello world'])
        formdata.data['5'].storage = 'remote'
        formdata.data['5'].storage_attrs = {'redirect_url': 'https://crypto.example.net/1234'}
        formdata.just_created()
        formdata.store()
        assert len(os.listdir(os.path.join(pub.app_dir, 'uploads'))) == 0
        clean_unused_files(pub)
        assert len(os.listdir(os.path.join(pub.app_dir, 'uploads'))) == 0

        # workflow attachment
        formdata = formdef.data_class()()
        formdata.data = {}
        formdata.just_created()
        formdata.store()

        formdata.evolution[-1].parts = [
            AttachmentEvolutionPart('hello.txt', fp=io.BytesIO(b'hello world'), varname='testfile')
        ]
        formdata.store()
        assert len(formdata.evolution) == 1
        assert formdata.evolution[0].parts is not None
        assert len(glob.glob(os.path.join(pub.app_dir, 'attachments', '*/*'))) == 1
        clean_unused_files(pub)
        assert len(glob.glob(os.path.join(pub.app_dir, 'attachments', '*/*'))) == 1
        formdata.anonymise()
        formdata.refresh_from_storage()
        assert len(formdata.evolution) == 1
        assert formdata.evolution[0].parts is None
        clean_unused_files(pub)
        assert len(glob.glob(os.path.join(pub.app_dir, 'attachments', '*/*'))) == 0

        # files in user profile
        user_formdef = UserFieldsFormDef(pub)
        user_formdef.fields.append(fields.FileField(id='3', label='test'))
        user_formdef.store()

        user = pub.user_class()
        user.email = 'bar@localhost'
        user.form_data = {'3': PicklableUpload('test.txt', 'text/plain')}
        user.form_data['3'].receive([b'hello world 2'])
        user.store()

        assert len(os.listdir(os.path.join(pub.app_dir, 'uploads'))) == 1
        clean_unused_files(pub)
        assert len(os.listdir(os.path.join(pub.app_dir, 'uploads'))) == 1
        user.remove_self()
        assert len(os.listdir(os.path.join(pub.app_dir, 'uploads'))) == 1
        clean_unused_files(pub)
        assert len(os.listdir(os.path.join(pub.app_dir, 'uploads'))) == 0

        # file from workflow form
        formdata = formdef.data_class()()
        formdata.data = {}
        formdata.just_created()
        formdata.store()

        display_form = FormWorkflowStatusItem()
        display_form.varname = 'blah'
        display_form.formdef = WorkflowFormFieldsFormDef(item=display_form)
        display_form.formdef.fields = []

        data = {'1': PicklableUpload('test.txt', 'text/plain')}
        data['1'].receive([b'hello world wf form'])
        formdata.evolution[-1].parts = [
            WorkflowFormEvolutionPart(display_form, data),
        ]

        count = len(os.listdir(os.path.join(pub.app_dir, 'uploads')))
        formdata.store()
        assert len(os.listdir(os.path.join(pub.app_dir, 'uploads'))) == count + 1
        clean_unused_files(pub)
        assert len(os.listdir(os.path.join(pub.app_dir, 'uploads'))) == count + 1

        formdata.evolution[-1].parts = []
        formdata.store()
        clean_unused_files(pub)
        assert len(os.listdir(os.path.join(pub.app_dir, 'uploads'))) == count

        if behaviour == 'move':
            # 4 files ("hello world" + "hello world 2" + "hello world block" + "hello world wf form")
            assert len(os.listdir(os.path.join(pub.app_dir, 'unused-files/uploads/'))) == 4
            # 1 attachment
            assert len(glob.glob(os.path.join(pub.app_dir, 'unused-files/attachments/*/*'))) == 1

    application = Application()
    application.name = 'App 1'
    application.slug = 'app-1'
    application.icon = PicklableUpload('icon.png', 'image/png')
    application.icon.receive([b'foobar'])
    application.version_number = '1'
    application.store()
    assert application.icon.qfilename in os.listdir(os.path.join(pub.app_dir, 'uploads'))
    clean_unused_files(pub)
    assert application.icon.qfilename in os.listdir(os.path.join(pub.app_dir, 'uploads'))

    Application.remove_object(application.id)
    clean_unused_files(pub)
    assert application.icon.qfilename not in os.listdir(os.path.join(pub.app_dir, 'uploads'))

    # unknown unused-files-behaviour: do nothing
    pub.site_options.set('options', 'unused-files-behaviour', 'foo')
    formdata = formdef.data_class()()
    formdata.data = {
        '5': PicklableUpload('test-no-remove.txt', 'text/plain'),
    }
    formdata.data['5'].receive([b'hello world'])
    formdata.just_created()
    formdata.store()

    assert formdata.data['5'].qfilename in os.listdir(os.path.join(pub.app_dir, 'uploads'))
    clean_unused_files(pub)
    assert os.listdir(os.path.join(pub.app_dir, 'uploads')) == [formdata.data['5'].qfilename]
    formdata.anonymise()
    clean_unused_files(pub)
    assert len(os.listdir(os.path.join(pub.app_dir, 'uploads'))) == 1  # file is not removed


def test_get_formdefs_of_all_kinds(pub):
    BlockDef.wipe()
    FormDef.wipe()
    Workflow.wipe()
    formdefs = get_formdefs_of_all_kinds()
    assert len(formdefs) == 1
    assert formdefs[0].__class__ == UserFieldsFormDef

    formdef = FormDef()
    formdef.name = 'basic formdef'
    formdef.store()

    carddef = CardDef()
    carddef.name = 'carddef'
    carddef.store()

    wf1 = Workflow(name='workflow with form fields formdef')
    st1 = wf1.add_status('Status1', 'st1')
    display_form1 = st1.add_action('form')
    display_form1.formdef = WorkflowFormFieldsFormDef(item=display_form1)
    st1.add_action('form')  # empty formdef
    wf1.store()

    wf2 = Workflow(name='workflow with variables fields formdef')
    wf2.variables_formdef = WorkflowVariablesFieldsFormDef(workflow=wf2)
    wf2.store()

    wf3 = Workflow(name='workflow with backoffice fields formdef')
    wf3.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(wf3)
    wf3.store()

    formdefs = get_formdefs_of_all_kinds()
    assert len(formdefs) == 6
    assert sorted((f.name, f.__class__) for f in formdefs) == [
        (
            'Backoffice fields of workflow "workflow with backoffice fields formdef"',
            WorkflowBackofficeFieldsFormDef,
        ),
        ('Form action in workflow "workflow with form fields formdef"', WorkflowFormFieldsFormDef),
        ('Options of workflow "workflow with variables fields formdef"', WorkflowVariablesFieldsFormDef),
        ('User Fields', UserFieldsFormDef),
        ('basic formdef', FormDef),
        ('carddef', CardDef),
    ]


def test_wipe_on_object(pub):
    FormDef.wipe()

    formdef = FormDef()
    formdef.name = 'basic formdef'
    formdef.store()

    with pytest.raises(AttributeError):
        formdef.wipe()


def test_update_storage_all_formdefs(pub, capfd):
    CardDef.wipe()
    FormDef.wipe()

    for i in range(5):
        formdef = FormDef()
        formdef.name = f'formdef {i+1}'
        formdef.store()

        carddef = CardDef()
        carddef.name = f'carddef {i+1}'
        carddef.store()

    with mock.patch('wcs.formdef_base.FormDefBase.update_storage') as update_storage:
        update_storage_all_formdefs(pub)
        assert update_storage.call_count == 10

    assert not capfd.readouterr().out

    formdef = FormDef()
    formdef.name = 'broken formdef'
    formdef.fields = [StringField(id='1', label='Test')]
    formdef.store()
    formdef.fields = [DateField(id='1', label='Test')]
    formdef.store()

    update_storage_all_formdefs(pub)
    assert capfd.readouterr().out == '! Integrity errors in %s\n' % formdef.get_admin_url()


def test_lazy_formdef(pub):
    FormDef.wipe()

    formdef = FormDef()
    formdef.name = 'test form'

    lazy_formdef = LazyFormDef(formdef)
    assert lazy_formdef.publication_disabled is False
    assert lazy_formdef.publication_datetime is None
    assert lazy_formdef.publication_expiration_datetime is None

    formdef.disabled = True
    assert lazy_formdef.publication_disabled is True
    assert lazy_formdef.publication_datetime is None
    assert lazy_formdef.publication_expiration_datetime is None

    formdef.disabled = False
    formdef.publication_date = '2000-01-01'
    assert lazy_formdef.publication_disabled is False
    assert lazy_formdef.publication_datetime == datetime.datetime(2000, 1, 1)
    assert lazy_formdef.publication_expiration_datetime is None

    formdef.disabled = False
    formdef.publication_date = '2200-01-01'
    assert lazy_formdef.publication_disabled is True
    assert lazy_formdef.publication_datetime == datetime.datetime(2200, 1, 1)
    assert lazy_formdef.publication_expiration_datetime is None

    formdef.disabled = False
    formdef.publication_date = '2000-01-01'
    formdef.expiration_date = '2000-01-01 10:00'
    assert lazy_formdef.publication_disabled is True
    assert lazy_formdef.publication_datetime == datetime.datetime(2000, 1, 1)
    assert lazy_formdef.publication_expiration_datetime == datetime.datetime(2000, 1, 1, 10, 0)
