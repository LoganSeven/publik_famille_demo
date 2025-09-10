import datetime
import io
import json
import os
import shutil
import tempfile
import zipfile
from unittest import mock

import django
import psycopg2
import pytest
from django.core.management import CommandError, call_command

from wcs.blocks import BlockDef
from wcs.carddef import CardDef
from wcs.categories import CardDefCategory, Category
from wcs.ctl.management.commands.trigger_jumps import select_and_jump_formdata
from wcs.fields import EmailField, FileField, ItemField, PageField, StringField
from wcs.formdef import FormDef
from wcs.mail_templates import MailTemplate
from wcs.qommon.afterjobs import AfterJob
from wcs.qommon.management.commands.collectstatic import Command as CmdCollectStatic
from wcs.qommon.management.commands.migrate import Command as CmdMigrate
from wcs.qommon.management.commands.migrate_schemas import Command as CmdMigrateSchemas
from wcs.qommon.upload_storage import PicklableUpload
from wcs.sql import cleanup_connection, get_connection_and_cursor
from wcs.wf.create_formdata import Mapping
from wcs.workflows import Workflow, WorkflowBackofficeFieldsFormDef, WorkflowStatusItem
from wcs.wscalls import NamedWsCall

from .utilities import clean_temporary_pub, create_temporary_pub


@pytest.fixture
def pub():
    pub = create_temporary_pub()
    cleanup_connection()
    pub.cfg['language'] = {'language': 'en'}
    pub.write_cfg()
    yield pub
    clean_temporary_pub()


def teardown_module(module):
    clean_temporary_pub()


@pytest.fixture
def alt_tempdir():
    alt_tempdir = tempfile.mkdtemp()
    yield alt_tempdir
    shutil.rmtree(alt_tempdir)


def test_collectstatic(pub, tmp_path):
    CmdCollectStatic.collectstatic(pub)
    assert os.path.exists(os.path.join(pub.app_dir, 'collectstatic', 'css', 'required.png'))
    assert os.path.exists(os.path.join(pub.app_dir, 'collectstatic', 'js', 'qommon.forms.js'))
    assert os.path.exists(os.path.join(pub.app_dir, 'collectstatic', 'css', 'gadjo.css'))
    assert os.path.exists(os.path.join(pub.app_dir, 'collectstatic', 'xstatic', 'jquery.js'))
    CmdCollectStatic.collectstatic(pub, clear=True, link=True)
    assert os.path.islink(os.path.join(pub.app_dir, 'collectstatic', 'css', 'required.png'))

    # create a broken link
    required_tmp = os.path.join(tmp_path, 'required.png')
    required_link = os.path.join(pub.app_dir, 'collectstatic', 'css', 'required.png')
    shutil.copy2(os.path.join(pub.app_dir, 'collectstatic', 'css', 'required.png'), required_tmp)
    os.unlink(required_link)
    os.symlink(required_tmp, required_link)
    os.unlink(required_tmp)
    # check that we have a broken link
    assert os.path.islink(required_link) and not os.path.exists(required_link)
    # still works if broken link exists
    CmdCollectStatic.collectstatic(pub, link=True)
    # link not broken any more
    assert os.path.islink(required_link) and os.path.exists(required_link)


def test_migrate(pub):
    pub.cleanup()
    CmdMigrate().handle()


def test_migrate_schemas(pub):
    pub.cleanup()
    CmdMigrateSchemas().handle()


@pytest.mark.parametrize('object_type', ['form', 'card'])
def test_wipe_data(pub, object_type):
    if object_type == 'form':
        category_class = Category
        object_class = FormDef
    elif object_type == 'card':
        category_class = CardDefCategory
        object_class = CardDef

    category_class.wipe()
    object_class.wipe()

    category = category_class(name='cat')
    category.store()

    form_1 = object_class()
    form_1.name = 'example'
    form_1.category = category
    form_1.fields = [StringField(id='0', label='Your Name'), EmailField(id='1', label='Email')]
    form_1.store()
    form_1.data_class().wipe()
    formdata_1 = form_1.data_class()()

    formdata_1.data = {'0': 'John Doe', '1': 'john@example.net'}
    formdata_1.store()

    assert form_1.data_class().count() == 1

    form_2 = object_class()
    form_2.name = 'example2'
    form_2.fields = [StringField(id='0', label='First Name'), StringField(id='1', label='Last Name')]
    form_2.store()
    form_2.data_class().wipe()
    formdata_2 = form_2.data_class()()
    formdata_2.data = {'0': 'John', '1': 'Doe'}
    formdata_2.store()
    assert form_2.data_class().count() == 1

    # no support for --all-tenants
    with pytest.raises(CommandError):
        call_command('wipe_data', '--all-tenants')

    # dry-run mode
    output = io.StringIO()
    call_command('wipe_data', '--domain=example.net', '--all', stdout=output)
    assert form_1.data_class().count() == 1
    assert form_2.data_class().count() == 1
    assert (
        output.getvalue()
        == f'''SIMULATION MODE: no actual wiping will happen.
(use --no-simulate after checking results)

{object_type} - example: 1
{object_type} - example2: 1
'''
    )

    # test with no options
    call_command('wipe_data', '--domain=example.net', '--no-simulate')
    assert form_1.data_class().count() == 1
    assert form_2.data_class().count() == 1

    # wipe one form formdatas
    call_command('wipe_data', '--domain=example.net', '--no-simulate', f'--{object_type}s={form_1.url_name}')
    assert form_1.data_class().count() == 0
    assert form_2.data_class().count() == 1

    # wipe all formdatas
    call_command('wipe_data', '--domain=example.net', '--no-simulate', '--all')
    assert form_1.data_class().count() == 0
    assert form_2.data_class().count() == 0

    # exclude some forms
    formdata_1.store()
    formdata_2.store()
    call_command(
        'wipe_data',
        '--domain=example.net',
        '--no-simulate',
        '--all',
        f'--exclude-{object_type}s={form_2.url_name}',
    )
    assert form_1.data_class().count() == 0
    assert form_2.data_class().count() == 1

    # remove forms from a category
    formdata_1.store()
    formdata_2.store()
    call_command(
        'wipe_data',
        '--domain=example.net',
        '--no-simulate',
        f'--{object_type}-categories={category.url_name}',
    )
    assert form_1.data_class().count() == 0
    assert form_2.data_class().count() == 1

    # check --delete-forms
    call_command(
        'wipe_data',
        '--domain=example.net',
        '--no-simulate',
        f'--{object_type}-categories={category.url_name}',
        f'--delete-{object_type}s',
    )
    assert form_1.id not in object_class.keys()
    assert form_2.id in object_class.keys()


def test_trigger_jumps(pub):
    Workflow.wipe()
    workflow = Workflow(name='test')
    st1 = workflow.add_status('Status1', 'st1')
    jump = st1.add_action('jump')
    jump.trigger = 'goto2'
    jump.mode = 'trigger'
    jump.status = 'st2'
    st2 = workflow.add_status('Status2', 'st2')
    workflow.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [
        StringField(id='0', label='Your Name', varname='name'),
        EmailField(id='1', label='Email', varname='email'),
    ]
    formdef.workflow_id = workflow.id
    formdef.store()

    def run_trigger(trigger, rows):
        formdef.data_class().wipe()
        formdata = formdef.data_class()()
        formdata.id = 1
        formdata.data = {'0': 'Alice', '1': 'alice@example.net'}
        formdata.status = 'wf-%s' % st1.id
        formdata.just_created()
        formdata.store()
        id1 = formdata.id
        formdata = formdef.data_class()()
        formdata.id = 2
        formdata.data = {'0': 'Bob', '1': 'bob@example.net'}
        formdata.status = 'wf-%s' % st1.id
        formdata.just_created()
        formdata.store()
        id2 = formdata.id
        select_and_jump_formdata(formdef, trigger, rows)
        return formdef.data_class().get(id1), formdef.data_class().get(id2)

    f1, f2 = run_trigger('goto2', '__all__')
    assert f1.status == f2.status == 'wf-%s' % st2.id

    # check publisher substitutions vars after the last jump_and_perform (#13964)
    assert pub in pub.substitutions.sources
    assert formdef in pub.substitutions.sources
    # we cannot know which formdata is the last one, test each possibility
    if f1 in pub.substitutions.sources:
        assert f2 not in pub.substitutions.sources
    if f2 in pub.substitutions.sources:
        assert f1 not in pub.substitutions.sources

    f1, f2 = run_trigger('goto2', [{'select': {}}])
    assert f1.status == f2.status == 'wf-%s' % st2.id

    f1, f2 = run_trigger('goto2', [{'select': {'form_number_raw': '1'}}])
    assert f1.status == 'wf-%s' % st2.id
    assert f2.status == 'wf-%s' % st1.id

    f1, f2 = run_trigger('goto2', [{'select': {'form_var_email': 'bob@example.net'}}])
    assert f1.status == 'wf-%s' % st1.id
    assert f2.status == 'wf-%s' % st2.id

    f1, f2 = run_trigger('goto2', [{'select': {}, 'data': {'foo': 'bar'}}])
    assert f1.status == f2.status == 'wf-%s' % st2.id
    assert f1.workflow_data['foo'] == f2.workflow_data['foo'] == 'bar'

    f1, f2 = run_trigger('goto2', [{'select': {'form_number_raw': '1'}, 'data': {'foo': 'bar'}}])
    assert f1.status == 'wf-%s' % st2.id
    assert f1.workflow_data['foo'] == 'bar'
    assert f2.status == 'wf-%s' % st1.id
    assert not f2.workflow_data

    f1, f2 = run_trigger('badtrigger', '__all__')
    assert f1.status == f2.status == 'wf-%s' % st1.id
    assert not f1.workflow_data
    assert not f2.workflow_data


def test_delete_tenant_with_sql(freezer):
    pub = create_temporary_pub()

    assert os.path.isdir(pub.app_dir)

    freezer.move_to('2018-12-01T00:00:00')
    call_command('delete_tenant', '--vhost=example.net')

    assert not os.path.isdir(pub.app_dir)
    parent_dir = os.path.dirname(pub.app_dir)
    if not [filename for filename in os.listdir(parent_dir) if 'removed' in filename]:
        assert False

    conn, cur = get_connection_and_cursor()
    cur.execute(
        """SELECT schema_name
                   FROM information_schema.schemata
                   WHERE schema_name like 'removed_20181201_%%%s'"""
        % pub.cfg['postgresql']['database']
    )

    assert len(cur.fetchall()) == 1

    clean_temporary_pub()
    pub = create_temporary_pub()

    call_command('delete_tenant', '--vhost=example.net', '--force-drop')

    conn, cur = get_connection_and_cursor(new=True)

    assert not os.path.isdir(pub.app_dir)
    cur.execute(
        """SELECT table_name
                   FROM information_schema.tables
                   WHERE table_schema = 'public'
                   AND table_type = 'BASE TABLE'"""
    )

    assert not cur.fetchall()

    cur.execute(
        """SELECT datname
                   FROM pg_database
                   WHERE datname = '%s'"""
        % pub.cfg['postgresql']['database']
    )

    assert cur.fetchall()

    clean_temporary_pub()
    pub = create_temporary_pub()

    pub.cfg['postgresql']['createdb-connection-params'] = {
        'user': pub.cfg['postgresql']['user'],
        'database': 'postgres',
    }
    pub.write_cfg()
    pub.cleanup()
    call_command('delete_tenant', '--vhost=example.net', '--force-drop')

    connect_kwargs = {'dbname': 'postgres', 'user': pub.cfg['postgresql']['user']}
    pgconn = psycopg2.connect(**connect_kwargs)
    cur = pgconn.cursor()

    cur.execute(
        """SELECT datname
                   FROM pg_database
                   WHERE datname = '%s'"""
        % pub.cfg['postgresql']['database']
    )
    assert not cur.fetchall()
    cur.close()
    pgconn.close()

    clean_temporary_pub()
    pub = create_temporary_pub()
    cleanup_connection()

    pub.cfg['postgresql']['createdb-connection-params'] = {
        'user': pub.cfg['postgresql']['user'],
        'database': 'postgres',
    }
    pub.write_cfg()
    call_command('delete_tenant', '--vhost=example.net')
    cleanup_connection()

    pgconn = psycopg2.connect(**connect_kwargs)
    pgconn.set_isolation_level(psycopg2.extensions.ISOLATION_LEVEL_AUTOCOMMIT)
    cur = pgconn.cursor()

    cur.execute(
        """SELECT datname
                   FROM pg_database
                   WHERE datname like 'removed_20181201_%%%s'"""
        % pub.cfg['postgresql']['database']
    )

    result = cur.fetchall()
    assert len(result) == 1

    # clean this db after test
    cur.execute("""DROP DATABASE %s""" % result[0][0])

    cur.execute(
        """SELECT datname
                   FROM pg_database
                   WHERE datname = '%s'"""
        % pub.cfg['postgresql']['database']
    )

    assert not cur.fetchall()
    cur.close()
    conn.close()

    clean_temporary_pub()


def test_runscript(pub):
    with pytest.raises(CommandError):
        call_command('runscript')
    with pytest.raises(CommandError):
        call_command('runscript', '--domain=a', '--all-tenants')
    with open(os.path.join(pub.app_dir, 'test2.py'), 'w') as fd:
        fd.write(
            '''
import os
from quixote import get_publisher
open(os.path.join(get_publisher().app_dir, 'runscript.test'), 'w').close()
'''
        )
    call_command('runscript', '--domain=example.net', os.path.join(pub.app_dir, 'test2.py'))
    assert os.path.exists(os.path.join(pub.app_dir, 'runscript.test'))

    os.unlink(os.path.join(pub.app_dir, 'runscript.test'))
    call_command('runscript', '--all-tenants', os.path.join(pub.app_dir, 'test2.py'))
    assert os.path.exists(os.path.join(pub.app_dir, 'runscript.test'))

    os.unlink(os.path.join(pub.app_dir, 'runscript.test'))
    call_command(
        'runscript', '--all-tenants', '--exclude-tenants=example.net', os.path.join(pub.app_dir, 'test2.py')
    )
    assert not os.path.exists(os.path.join(pub.app_dir, 'runscript.test'))

    call_command(
        'runscript', '--all-tenants', '--exclude-tenants=example2.net', os.path.join(pub.app_dir, 'test2.py')
    )
    assert os.path.exists(os.path.join(pub.app_dir, 'runscript.test'))


def test_import_site():
    with pytest.raises(CommandError):
        call_command('import_site')
    create_temporary_pub()
    FormDef.wipe()
    Workflow.wipe()
    assert FormDef.count() == 0
    assert Workflow.count() == 0
    site_zip_path = os.path.join(os.path.dirname(__file__), 'site.zip')
    call_command('import_site', '--domain=example.net', site_zip_path)
    assert FormDef.count() == 1
    assert Workflow.count() == 1

    formdef = FormDef()
    formdef.name = 'test sequence'
    formdef.store()
    assert formdef.id == 2

    FormDef.wipe()
    assert FormDef.count() == 0
    assert Workflow.count() == 1
    call_command('import_site', '--domain=example.net', '--if-empty', site_zip_path)
    assert FormDef.count() == 0
    assert Workflow.count() == 1

    site_zip_path = os.path.join(os.path.dirname(__file__), 'missing_file.zip')
    with pytest.raises(CommandError, match='missing file:'):
        call_command('import_site', '--domain=example.net', site_zip_path)


def test_export_site(tmp_path):
    pub = create_temporary_pub()
    Workflow.wipe()
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = []
    formdef.store()

    site_zip_path = os.path.join(tmp_path, 'site.zip')
    call_command('export_site', '--domain=example.net', f'--output={site_zip_path}')
    with zipfile.ZipFile(site_zip_path, mode='r') as zfile:
        assert set(zfile.namelist()) == {'formdefs_xml/1', 'config.json'}
        assert 'postgresql' in pub.cfg
        assert 'postgresql' not in json.loads(zfile.read('config.json'))


def test_shell():
    with pytest.raises(CommandError):
        call_command('shell')  # missing tenant name


class ForTestAfterJob(AfterJob):
    def execute(self):
        self.test_result = WorkflowStatusItem().compute('{{ global_title|default:"FAIL" }}')
        self.l10n_month = WorkflowStatusItem().compute('{{ "10/10/2010"|date:"F" }}')
        self.store()


class JobForTestWithExceptionAfterJob(AfterJob):
    def execute(self):
        raise ZeroDivisionError()


class ForWatchTestAfterJob(AfterJob):
    @classmethod
    def get(cls, id):
        # mock method, it will be called during the refresh loop, this allows checking
        # state change and interrupt.
        obj = cls.singleton
        obj.call_count += 1
        if obj.call_count == 1:
            obj.status = 'running'
        elif obj.call_count == 4:
            obj.status = 'completed'
        elif obj.call_count == 5:
            raise KeyboardInterrupt()
        return obj


def test_runjob(pub):
    with pytest.raises(CommandError):
        call_command('runjob')
    with pytest.raises(CommandError):
        call_command('runjob', '--domain=example.net', '--job-id=%s' % 'invalid')

    pub.load_site_options()
    if not pub.site_options.has_section('variables'):
        pub.site_options.add_section('variables')
    pub.site_options.set('variables', 'global_title', 'HELLO')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)

    job = ForTestAfterJob(label='test')
    job.store()
    assert AfterJob.get(job.id).status == 'registered'
    call_command('runjob', '--domain=example.net', '--job-id=%s' % job.id)
    assert AfterJob.get(job.id).status == 'completed'
    assert AfterJob.get(job.id).test_result == 'HELLO'
    assert AfterJob.get(job.id).l10n_month == 'October'

    pub.cfg['language'] = {'language': 'fr'}
    pub.write_cfg()
    job = ForTestAfterJob(label='test2')
    job.store()
    assert AfterJob.get(job.id).status == 'registered'
    call_command('runjob', '--domain=example.net', '--job-id=%s' % job.id)
    assert AfterJob.get(job.id).status == 'completed'
    assert AfterJob.get(job.id).l10n_month == 'octobre'
    completion_time = AfterJob.get(job.id).completion_time

    # running again the job will skip it
    call_command('runjob', '--domain=example.net', '--job-id=%s' % job.id)
    assert AfterJob.get(job.id).completion_time == completion_time

    # --force-replay will force the job to run again
    call_command('runjob', '--domain=example.net', '--job-id=%s' % job.id, '--force-replay')
    assert AfterJob.get(job.id).completion_time != completion_time

    # test exception handling
    job = JobForTestWithExceptionAfterJob(label='test3')
    job.store()
    assert AfterJob.get(job.id).status == 'registered'
    call_command('runjob', '--domain=example.net', '--job-id=%s' % job.id)
    assert AfterJob.get(job.id).status == 'failed'
    assert 'ZeroDivisionError' in AfterJob.get(job.id).exception

    # check --raise
    with pytest.raises(ZeroDivisionError):
        call_command('runjob', '--domain=example.net', '--job-id=%s' % job.id, '--force-replay', '--raise')


def test_list_jobs(pub):
    job1 = ForTestAfterJob(label='test')
    job1.store()

    job2 = ForTestAfterJob(label='test')
    job2.status = 'running'
    job2.current_count = 5
    job2.total_count = 17
    job2.store()

    job3 = ForTestAfterJob(label='test')
    job3.status = 'failed'
    job3.store()

    job4 = ForTestAfterJob(label='test')
    job4.status = 'completed'
    job4.current_count = 17
    job4.total_count = 17
    job4.completion_time = job4.creation_time + datetime.timedelta(seconds=123)
    job4.store()

    job5 = ForTestAfterJob(label='test')
    job5.store()

    output = io.StringIO()
    call_command('list_jobs', stdout=output)
    lines = output.getvalue().splitlines()
    assert lines[0].startswith('example.net')  # tenant
    assert len(lines) == 1
    assert job2.id in lines[0]
    assert 'ForTest' in lines[0]
    assert 'ForTestAfterJob' not in lines[0]  # suffix is removed
    assert 'running' in lines[0]
    assert '5/17 (29%)' in lines[0]

    output = io.StringIO()
    call_command('list_jobs', '--domain=example.net', stdout=output)
    lines = output.getvalue().splitlines()
    assert not lines[0].startswith('example.net')  # no tenant as --domain was given

    output = io.StringIO()
    call_command('list_jobs', '--status=completed', stdout=output)
    lines = output.getvalue().splitlines()
    assert len(lines) == 1

    output = io.StringIO()
    call_command('list_jobs', '--status=all', stdout=output)
    lines = output.getvalue().splitlines()
    assert len(lines) == 5

    output = io.StringIO()
    call_command('list_jobs', '--status=running,completed', stdout=output)
    lines = output.getvalue().splitlines()
    assert len(lines) == 2

    output = io.StringIO()
    call_command('list_jobs', '--status=all', '--sort=completion_time', stdout=output)
    lines = output.getvalue().splitlines()
    assert 'completed' in lines[-1]

    output = io.StringIO()
    call_command('list_jobs', '--status=all', '--sort=completion_time', '--reverse', stdout=output)
    lines = output.getvalue().splitlines()
    assert 'completed' in lines[0]

    output = io.StringIO()
    call_command('list_jobs', '--job-id=%s' % job3.id, stdout=output)
    lines = output.getvalue().splitlines()
    assert len(lines) == 1
    assert 'failed' in lines[0]

    AfterJob.wipe()

    job = ForWatchTestAfterJob(label='test')
    job.store()
    ForWatchTestAfterJob.singleton = job
    job.call_count = 0

    output = io.StringIO()

    from wcs.ctl.management.commands.list_jobs import Command

    Command.watch_delay = 0.01
    call_command('list_jobs', '--watch', '--job-id=%s' % job.id, stdout=output)
    assert len(output.getvalue().splitlines()) == 5
    assert output.getvalue().count('\7') == 2  # 2 bells


def test_dbshell(pub):
    with pytest.raises(CommandError):
        call_command('dbshell')  # missing tenant name

    with mock.patch('subprocess.call' if django.VERSION < (3, 2) else 'subprocess.run') as call:
        call.side_effect = lambda *args, **kwargs: 0
        call_command('dbshell', '--domain', 'example.net')
        assert call.call_args[0][-1][0] == 'psql'
        assert call.call_args[0][-1][-1] == pub.cfg['postgresql']['database']


def test_makemessages(pub):
    # just make sure it loads correctly
    with pytest.raises(SystemExit):
        call_command('makemessages', '--help')


def test_grep(pub):
    FormDef.wipe()
    Workflow.wipe()
    NamedWsCall.wipe()
    MailTemplate.wipe()

    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [StringField(id='1', label='Your Name'), EmailField(id='2', label='Email')]
    formdef.options = {'x': 'Name'}
    formdef.store()

    workflow = Workflow()
    workflow.name = 'test'
    st = workflow.add_status('status')
    st.add_action('aggregationemail')
    workflow.store()

    wscall = NamedWsCall()
    wscall.name = 'Hello'
    wscall.request = {'url': 'http://example.org/api/test', 'qs_data': {'a': 'b'}}
    wscall.store()

    mail_template = MailTemplate(name='test mail template')
    mail_template.subject = 'test subject'
    mail_template.body = 'test body'
    mail_template.attachments = ['form_var_file1_raw']
    mail_template.store()

    with pytest.raises(CommandError):
        call_command('grep')

    with pytest.raises(CommandError):
        call_command('grep', 'xxx')

    with pytest.raises(CommandError):
        call_command('grep', '--all-tenants', '--domain', 'example.net', 'xxx')

    with pytest.raises(CommandError):
        call_command('grep', '--domain', 'example.net', '--type', 'foo', 'xxx')

    with mock.patch('wcs.ctl.management.commands.grep.Command.print_hit') as print_hit:
        call_command('grep', '--domain', 'example.net', '--type', 'action-types', 'email')
        assert print_hit.call_args[0] == ('http://example.net/backoffice/workflows/1/status/1/items/1/',)
        print_hit.reset_mock()

        call_command('grep', '--domain', 'example.net', '--type', 'field-types', 'email')
        assert print_hit.call_args[0] == ('http://example.net/backoffice/forms/1/fields/2/',)
        print_hit.reset_mock()

        call_command('grep', '--domain', 'example.net', 'Name')
        assert print_hit.call_count == 2
        assert print_hit.call_args_list[0].args == (
            'http://example.net/backoffice/forms/1/fields/1/',
            'Your Name',
        )
        assert print_hit.call_args_list[1].args == (
            'http://example.net/backoffice/forms/1/workflow-variables',
            'Name',
        )
        print_hit.reset_mock()

        call_command('grep', '--domain', 'example.net', '/api/test')
        assert print_hit.call_args[0] == (
            'http://example.net/backoffice/settings/wscalls/1/',
            'http://example.org/api/test',
        )
        print_hit.reset_mock()

        call_command('grep', '--domain', 'example.net', 'form_var_file1_raw')
        assert print_hit.call_args[0] == (
            'http://example.net/backoffice/workflows/mail-templates/1/',
            'form_var_file1_raw',
        )
        print_hit.reset_mock()

        call_command('grep', '--domain', 'example.net', 'xxx')
        assert print_hit.call_count == 0
        print_hit.reset_mock()


def test_grep_prefill(pub):
    FormDef.wipe()
    Workflow.wipe()
    NamedWsCall.wipe()
    MailTemplate.wipe()

    formdef = FormDef()
    formdef.name = 'test'
    # template prefill
    formdef.fields = [
        StringField(
            id='1', label='Your Name', prefill={'type': 'string', 'value': 'a{{foo.prefill_string}}b'}
        )
    ]
    formdef.store()

    with mock.patch('wcs.ctl.management.commands.grep.Command.print_hit') as print_hit:
        call_command('grep', '--domain', 'example.net', 'prefill_string')
        assert print_hit.call_args[0] == (
            'http://example.net/backoffice/forms/1/fields/1/',
            'a{{foo.prefill_string}}b',
        )


@pytest.mark.parametrize('data_source_type', ['json', 'jsonp'])
def test_grep_data_source(pub, data_source_type):
    FormDef.wipe()
    Workflow.wipe()
    NamedWsCall.wipe()
    MailTemplate.wipe()

    formdef = FormDef()
    formdef.name = 'test'
    # template prefill
    formdef.fields = [
        ItemField(
            id='1',
            label='Your Name',
            data_source={'type': data_source_type, 'value': '{{ machin_url }}/data-source/x/'},
        )
    ]
    formdef.store()

    with mock.patch('wcs.ctl.management.commands.grep.Command.print_hit') as print_hit:
        call_command('grep', '--domain', 'example.net', 'data-source/x')
        assert print_hit.call_args[0] == (
            'http://example.net/backoffice/forms/1/fields/1/',
            '{{ machin_url }}/data-source/x/',
        )


def test_grep_data_source_custom_view(pub):
    FormDef.wipe()

    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [
        ItemField(
            id='1',
            label='Your Name',
            data_source={'type': 'carddef:slug:custom-view-slug'},
        )
    ]
    formdef.store()

    with mock.patch('wcs.ctl.management.commands.grep.Command.print_hit') as print_hit:
        call_command('grep', '--domain', 'example.net', 'custom-view-slug')
        assert print_hit.call_args[0] == (
            'http://example.net/backoffice/forms/1/fields/1/',
            'custom-view-slug',
        )


def test_grep_create_carddata(pub):
    CardDef.wipe()
    FormDef.wipe()
    Workflow.wipe()

    carddef = CardDef()
    carddef.name = 'My card'
    carddef.fields = [
        StringField(id='1', label='string'),
    ]
    carddef.store()

    wf = Workflow(name='create-carddata')
    wf.possible_status = Workflow.get_default_workflow().possible_status[:]
    create = wf.possible_status[1].add_action('create_carddata', id='_create', prepend=True)
    create.label = 'Create CardDef'
    create.varname = 'mycard'
    create.formdef_slug = carddef.url_name
    create.mappings = [
        Mapping(field_id='1', expression='{{ foo_bar }}'),
    ]
    wf.store()

    with mock.patch('wcs.ctl.management.commands.grep.Command.print_hit') as print_hit:
        call_command('grep', '--domain', 'example.net', 'foo_bar')
        assert print_hit.call_args[0] == (
            'http://example.net/backoffice/workflows/1/status/new/items/_create/',
            '{{ foo_bar }}',
        )


def test_grep_edit_carddata(pub):
    CardDef.wipe()
    FormDef.wipe()
    Workflow.wipe()

    carddef = CardDef()
    carddef.name = 'My card'
    carddef.fields = [
        StringField(id='1', label='string'),
    ]
    carddef.store()

    wf = Workflow(name='edit-carddata')
    wf.possible_status = Workflow.get_default_workflow().possible_status[:]
    edit = wf.possible_status[1].add_action('edit_carddata', id='edit', prepend=True)
    edit.label = 'Edit CardDef'
    edit.varname = 'mycard'
    edit.formdef_slug = carddef.url_name
    edit.mappings = [
        Mapping(field_id='1', expression='{{ foo_bar }}'),
    ]
    wf.store()

    with mock.patch('wcs.ctl.management.commands.grep.Command.print_hit') as print_hit:
        call_command('grep', '--domain', 'example.net', 'foo_bar')
        assert print_hit.call_args[0] == (
            'http://example.net/backoffice/workflows/1/status/new/items/edit/',
            '{{ foo_bar }}',
        )


def test_grep_backoffice_fields(pub):
    Workflow.wipe()

    wf = Workflow(name='test-backoffice-fields')
    wf.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(wf)
    wf.backoffice_fields_formdef.fields = [
        StringField(
            id='bo1',
            label='field',
            varname='blah',
            prefill={'type': 'string', 'value': 'a{{foo.prefill_string}}b'},
        ),
    ]
    wf.store()

    with mock.patch('wcs.ctl.management.commands.grep.Command.print_hit') as print_hit:
        call_command('grep', '--domain', 'example.net', 'prefill_string')
        assert print_hit.call_args[0] == (
            'http://example.net/backoffice/workflows/1/backoffice-fields/fields/bo1/',
            'a{{foo.prefill_string}}b',
        )


def test_grep_webservice_call(pub):
    FormDef.wipe()
    Workflow.wipe()

    wf = Workflow(name='webservice-call')
    wf.possible_status = Workflow.get_default_workflow().possible_status[:]
    webservice_call = wf.possible_status[1].add_action('webservice_call', id='webservice-call', prepend=True)
    webservice_call.url = 'http://remote.example.net'
    webservice_call.qs_data = {
        'param1': '{{ form_number }}',
    }

    webservice_call.post_data = {
        'param2': '{{ foo_bar }}',
    }
    wf.store()

    with mock.patch('wcs.ctl.management.commands.grep.Command.print_hit') as print_hit:
        call_command('grep', '--domain', 'example.net', 'form_number')
        assert print_hit.call_args[0] == (
            'http://example.net/backoffice/workflows/1/status/new/items/webservice-call/',
            '{{ form_number }}',
        )

    with mock.patch('wcs.ctl.management.commands.grep.Command.print_hit') as print_hit:
        call_command('grep', '--domain', 'example.net', 'foo_bar')
        assert print_hit.call_args[0] == (
            'http://example.net/backoffice/workflows/1/status/new/items/webservice-call/',
            '{{ foo_bar }}',
        )

    with mock.patch('wcs.ctl.management.commands.grep.Command.print_hit') as print_hit:
        call_command('grep', '--domain', 'example.net', 'param1')
        assert print_hit.call_args[0] == (
            'http://example.net/backoffice/workflows/1/status/new/items/webservice-call/',
            'param1',
        )

    with mock.patch('wcs.ctl.management.commands.grep.Command.print_hit') as print_hit:
        call_command('grep', '--domain', 'example.net', 'param2')
        assert print_hit.call_args[0] == (
            'http://example.net/backoffice/workflows/1/status/new/items/webservice-call/',
            'param2',
        )


def test_grep_set_backoffice_fields_action(pub):
    FormDef.wipe()
    Workflow.wipe()

    wf = Workflow(name='webservice-call')
    wf.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(wf)
    wf.backoffice_fields_formdef.fields = [
        StringField(id='bo1', label='bo field 1', varname='plop'),
    ]

    wf.possible_status = Workflow.get_default_workflow().possible_status[:]
    set_backoffice_fields = wf.possible_status[1].add_action(
        'set-backoffice-fields', id='set-backoffice-fields', prepend=True
    )
    set_backoffice_fields.fields = [{'field_id': 'bo1', 'value': '{{ foo_bar }}'}]
    wf.store()

    with mock.patch('wcs.ctl.management.commands.grep.Command.print_hit') as print_hit:
        call_command('grep', '--domain', 'example.net', 'foo_bar')
        assert print_hit.call_args[0] == (
            'http://example.net/backoffice/workflows/1/status/new/items/set-backoffice-fields/',
            '{{ foo_bar }}',
        )


def test_grep_action_condition(pub):
    Workflow.wipe()
    workflow = Workflow.get_default_workflow()
    workflow.id = '2'
    workflow.store()
    workflow.possible_status[0].items[2].condition = {'type': 'django', 'value': 'foo_bar'}
    workflow.store()

    with mock.patch('wcs.ctl.management.commands.grep.Command.print_hit') as print_hit:
        call_command('grep', '--domain', 'example.net', 'foo_bar')
        assert print_hit.call_args[0] == (
            'http://example.net/backoffice/workflows/2/status/just_submitted/items/_jump_to_new/',
            'foo_bar',
        )


def test_grep_field_condition(pub):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'Foo'
    formdef.fields = [
        StringField(
            id='1',
            label='Bar',
            size='40',
            required='required',
            condition={'type': 'django', 'value': 'foo_bar'},
        )
    ]
    formdef.store()

    with mock.patch('wcs.ctl.management.commands.grep.Command.print_hit') as print_hit:
        call_command('grep', '--domain', 'example.net', 'foo_bar')
        assert print_hit.call_args[0] == (
            f'http://example.net/backoffice/forms/{formdef.id}/fields/1/',
            'foo_bar',
        )


def test_grep_page_condition(pub):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = []
    formdef.store()
    formdef.fields = [
        PageField(
            id='0',
            label='1st page',
            condition={'type': 'django', 'value': 'foo_bar'},
            post_conditions=[
                {
                    'condition': {'type': 'django', 'value': 'form_xx'},
                    'error_message': 'You shall not pass.',
                }
            ],
        ),
    ]
    formdef.store()

    with mock.patch('wcs.ctl.management.commands.grep.Command.print_hit') as print_hit:
        call_command('grep', '--domain', 'example.net', 'foo_bar')
        assert print_hit.call_args[0] == (
            f'http://example.net/backoffice/forms/{formdef.id}/fields/0/',
            'foo_bar',
        )

    with mock.patch('wcs.ctl.management.commands.grep.Command.print_hit') as print_hit:
        call_command('grep', '--domain', 'example.net', 'form_xx')
        assert print_hit.call_args[0] == (
            f'http://example.net/backoffice/forms/{formdef.id}/fields/0/',
            'form_xx',
        )


def test_grep_workflow_options(pub):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = []
    formdef.workflow_options = {'a': 'foo_bar'}
    formdef.store()

    with mock.patch('wcs.ctl.management.commands.grep.Command.print_hit') as print_hit:
        call_command('grep', '--domain', 'example.net', 'foo_bar')
        assert print_hit.call_args[0] == (
            'http://example.net/backoffice/forms/1/workflow-variables',
            'foo_bar',
        )


def test_grep_block(pub):
    FormDef.wipe()
    BlockDef.wipe()

    blockdef = BlockDef()
    blockdef.name = 'Foo'
    blockdef.fields = [
        StringField(
            id='1',
            label='bar',
            size='40',
            required='required',
            post_conditions=[
                {
                    'condition': {'type': 'django', 'value': 'webservice.world == "test"'},
                    'error_message': 'error',
                },
            ],
        )
    ]
    blockdef.store()

    with mock.patch('wcs.ctl.management.commands.grep.Command.print_hit') as print_hit:
        call_command('grep', '--domain', 'example.net', 'bar')
        assert print_hit.call_args[0] == (
            'http://example.net/backoffice/forms/blocks/%s/1/' % blockdef.id,
            'bar',
        )

        call_command('grep', '--domain', 'example.net', 'webservice.world')
        assert print_hit.call_args[0] == (
            'http://example.net/backoffice/forms/blocks/%s/1/' % blockdef.id,
            'bar',
        )


def test_grep_workflow_multiple(pub):
    FormDef.wipe()
    BlockDef.wipe()
    Workflow.wipe()

    workflow = Workflow(name='test')
    st1 = workflow.add_status('Status1', 'st1')
    choice = st1.add_action('choice')
    choice.label = '{{foo_bar}}'
    workflow.store()

    with mock.patch('wcs.ctl.management.commands.grep.Command.print_unique_hit') as print_unique_hit:
        call_command('grep', '--domain', 'example.net', 'foo_bar')
        assert print_unique_hit.call_args[0] == (
            'http://example.net/backoffice/workflows/1/status/st1/items/1/',
            '{{foo_bar}}',
        )
        assert print_unique_hit.call_count == 1

    with mock.patch('wcs.ctl.management.commands.grep.Command.print_unique_hit') as print_unique_hit:
        call_command('grep', '--domain', 'example.net', '--urls', 'foo_bar')
        assert print_unique_hit.call_args[0] == (
            'http://example.net/backoffice/workflows/1/status/st1/items/1/',
        )
        assert print_unique_hit.call_count == 1


def test_grep_workflow_global_action_trigger(pub):
    FormDef.wipe()
    BlockDef.wipe()
    Workflow.wipe()

    workflow = Workflow(name='test')
    action = workflow.add_global_action('FOOBAR')
    trigger1 = action.append_trigger('timeout')
    trigger1.anchor = 'creation'
    trigger1.timeout = '{{ form_var_foo_bar }}'
    trigger2 = action.append_trigger('webservice')
    trigger2.identifier = 'delete'
    workflow.store()

    with mock.patch('wcs.ctl.management.commands.grep.Command.print_hit') as print_hit:
        call_command('grep', '--domain', 'example.net', 'foo_bar')
        assert print_hit.call_args[0] == (
            f'http://example.net/backoffice/workflows/{workflow.id}/global-actions/{action.id}/triggers/{trigger1.id}/',
            '{{ form_var_foo_bar }}',
        )


def test_grep_workflow_status_loop_template(pub):
    FormDef.wipe()
    BlockDef.wipe()
    Workflow.wipe()

    workflow = Workflow(name='test')
    status = workflow.add_status('st1')
    status.loop_items_template = '{{ form_var_foo_bar }}'
    workflow.store()

    with mock.patch('wcs.ctl.management.commands.grep.Command.print_hit') as print_hit:
        call_command('grep', '--domain', 'example.net', 'foo_bar')
        assert print_hit.call_args[0] == (
            f'http://example.net/backoffice/workflows/{workflow.id}/status/{status.id}/',
            '{{ form_var_foo_bar }}',
        )


def test_grep_workflow_status_name(pub):
    FormDef.wipe()
    BlockDef.wipe()
    Workflow.wipe()

    workflow = Workflow(name='test')
    status = workflow.add_status('st1')
    workflow.store()

    workflow2 = Workflow(name='test2')
    workflow2.add_status('st2')
    workflow2.store()

    with mock.patch('wcs.ctl.management.commands.grep.Command.print_hit') as print_hit:
        call_command('grep', '--domain', 'example.net', 'st1')
        assert print_hit.call_count == 1
        assert print_hit.call_args[0] == (
            f'http://example.net/backoffice/workflows/{workflow.id}/status/{status.id}/',
            'st1',
        )


def test_grep_workflow_global_action_name(pub):
    FormDef.wipe()
    BlockDef.wipe()
    Workflow.wipe()

    workflow = Workflow(name='test')
    global_action = workflow.add_global_action('action1')
    workflow.store()

    workflow2 = Workflow(name='test2')
    workflow2.add_global_action('action2')
    workflow2.store()

    with mock.patch('wcs.ctl.management.commands.grep.Command.print_hit') as print_hit:
        call_command('grep', '--domain', 'example.net', 'action1')
        assert print_hit.call_count == 1
        assert print_hit.call_args[0] == (
            f'http://example.net/backoffice/workflows/{workflow.id}/global-actions/{global_action.id}/',
            'action1',
        )


def test_configdb(pub):
    call_command('configdb', '--domain', 'example.net')

    call_command('configdb', '--domain', 'example.net', '--info')

    database = pub.cfg['postgresql']['database']
    user = pub.cfg['postgresql']['user']
    pub.cfg['postgresql']['database'] = ''
    pub.write_cfg()

    call_command('configdb', '--domain', 'example.net', '--database', database, '--user', user)
    pub.reload_cfg()
    assert pub.cfg['postgresql']['database'] == database


def test_site_option(pub):
    call_command('site_option', '--domain', 'example.net', 'options', 'key', 'value')
    pub.load_site_options()
    assert pub.site_options['options']['key'] == 'value'

    call_command('site_option', '--domain', 'example.net', 'new-section', 'key', 'value')
    pub.load_site_options()
    assert pub.site_options['new-section']['key'] == 'value'

    call_command('site_option', '--domain', 'example.net', '--unset', 'options', 'key')
    pub.load_site_options()
    assert 'key' not in pub.site_options['options']

    # unset missing key
    call_command('site_option', '--domain', 'example.net', '--unset', 'options', 'key')

    with pytest.raises(CommandError):
        # missing value
        call_command('site_option', '--domain', 'example.net', 'section', 'key')

    with pytest.raises(CommandError):
        # value for --unset
        call_command('site_option', '--domain', 'example.net', '--unset', 'section', 'key', 'value')


def create_formdata():
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [
        FileField(
            id='0',
            label='file0',
        ),
        FileField(
            id='1',
            label='file1',
        ),
    ]
    formdef.store()
    formdata = formdef.data_class()()
    upload = PicklableUpload('test.txt', 'text/plain', 'ascii')
    upload.receive([b'test'])
    upload2 = PicklableUpload('test.txt', 'text/plain', 'ascii')
    upload2.receive([b'test'])
    formdata.data = {'0': upload, '1': upload2}
    formdata.just_created()
    formdata.store()
    return formdata


def test_clamdscan_not_enabled(pub, capsys):
    create_formdata()
    # clamd is not enable so nothing happens
    with mock.patch('wcs.clamd.subprocess') as subp:
        attrs = {'run.return_value': mock.Mock(returncode=0, stdout='stdout')}
        subp.configure_mock(**attrs)
        call_command('clamdscan', '--domain', 'example.net')
        subp.run.assert_not_called()
        captured = capsys.readouterr()
        assert 'Ignoring example.net because clamd is not enabled.\n' in captured.out

        # force scan even if clamd is not enabled
        subp.reset_mock()
        call_command('clamdscan', '--domain', 'example.net', '--force')
        subp.run.assert_called()


def test_clamdscan(pub, capsys):
    pub.load_site_options()
    if not pub.site_options.has_section('options'):
        pub.site_options.add_section('options')
    pub.site_options.set('options', 'enable-clamd', 'true')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)
    pub.load_site_options()

    formdata = create_formdata()

    # check that there no clamdscan data
    for file_data in formdata.get_all_file_data(with_history=False):
        assert not file_data.has_been_scanned()

    with mock.patch('wcs.clamd.subprocess') as subp:
        attrs = {'run.return_value': mock.Mock(returncode=0, stdout='stdout')}
        subp.configure_mock(**attrs)
        call_command('clamdscan', '--domain', 'example.net')
    captured = capsys.readouterr()
    assert 'No malware found in example.net.\n' in captured.out

    formdata.refresh_from_storage()
    for file_data in formdata.get_all_file_data(with_history=False):
        assert file_data.has_been_scanned()
        assert file_data.clamd['returncode'] == 0

    # force option not used, files won't be scanned again
    with mock.patch('wcs.clamd.subprocess') as subp:
        attrs = {'run.return_value': mock.Mock(returncode=1, stdout='stdout')}
        subp.configure_mock(**attrs)
        call_command('clamdscan', '--domain', 'example.net')
        subp.run.asset_not_called()

    captured = capsys.readouterr()
    assert 'No malware found in example.net.\n' in captured.out

    formdata.refresh_from_storage()
    for file_data in formdata.get_all_file_data(with_history=False):
        assert file_data.has_been_scanned()
        assert file_data.clamd['returncode'] == 0

    # now force it
    with mock.patch('wcs.clamd.subprocess') as subp:
        attrs = {'run.return_value': mock.Mock(returncode=1, stdout='stdout')}
        subp.configure_mock(**attrs)
        call_command('clamdscan', '--domain', 'example.net', '--rescan')

    captured = capsys.readouterr()
    assert 'Malware found in example.net.\n' in captured.out
    assert formdata.get_backoffice_url() in captured.out

    formdata.refresh_from_storage()
    for file_data in formdata.get_all_file_data(with_history=False):
        assert file_data.has_been_scanned()
        assert file_data.clamd['returncode'] == 1

    # check clamdscan failing to scan
    # as the files were already scanned nothing is saved
    with mock.patch('wcs.clamd.subprocess') as subp:
        attrs = {'run.return_value': mock.Mock(returncode=2, stdout='stdout')}
        subp.configure_mock(**attrs)
        call_command('clamdscan', '--domain', 'example.net', '--rescan')

    formdata.refresh_from_storage()
    for file_data in formdata.get_all_file_data(with_history=False):
        assert file_data.has_been_scanned()
        assert file_data.clamd['returncode'] == 1

    # check clamdscan failing to scan on the first time
    formdata = create_formdata()
    with mock.patch('wcs.clamd.subprocess') as subp:
        attrs = {'run.return_value': mock.Mock(returncode=2, stdout='stdout')}
        subp.configure_mock(**attrs)
        call_command('clamdscan', '--domain', 'example.net', '--rescan')

    formdata.refresh_from_storage()
    for file_data in formdata.get_all_file_data(with_history=False):
        assert file_data.has_been_scanned()
        assert file_data.clamd['returncode'] == 2
