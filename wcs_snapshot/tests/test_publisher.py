import datetime
import glob
import io
import json
import os
import pickle
import re
import shutil
import sys
import time
import xml.etree.ElementTree as ET
import zipfile
from unittest import mock

import pytest
from django.core.management import call_command
from django.http import Http404
from django.test import override_settings
from django.utils.timezone import localtime, make_aware
from quixote import cleanup, get_publisher
from quixote.http_request import Upload

from wcs import sql
from wcs.logged_errors import LoggedError
from wcs.qommon import get_publisher_class
from wcs.qommon.afterjobs import AfterJob
from wcs.qommon.cron import CronJob
from wcs.qommon.form import UploadedFile
from wcs.qommon.http_request import HTTPRequest
from wcs.qommon.publisher import MaxSizeDict, Tenant
from wcs.workflows import Workflow

from .utilities import clean_temporary_pub, create_temporary_pub


def setup_module(module):
    cleanup()
    global pub
    pub = create_temporary_pub()
    pub.cfg['language'] = {'language': 'en'}
    pub.write_cfg()


def teardown_module(module):
    clean_temporary_pub()


def get_request():
    return HTTPRequest(
        None,
        {
            'SERVER_NAME': 'www.example.net',
            'SCRIPT_NAME': '',
        },
    )


def test_plaintext_error():
    req = get_request()
    pub._set_request(req)
    exc_type, exc_value, tb = None, None, None
    try:
        raise Exception('foo')
    except Exception:
        exc_type, exc_value, tb = sys.exc_info()
    req.form = {'foo': 'bar'}
    s = pub._generate_plaintext_error(req, None, exc_type, exc_value, tb)
    assert re.findall('^foo.*bar', s, re.MULTILINE)
    assert re.findall('^SERVER_NAME.*www.example.net', s, re.MULTILINE)
    assert re.findall('File.*?line.*?in test_plaintext_error', s)
    assert re.findall(r'^>.*\d+.*s = pub._generate_plaintext_error', s, re.MULTILINE)


def test_finish_failed_request():
    req = get_request()
    pub._set_request(req)
    try:
        raise Exception()
    except Exception:
        body = pub.finish_failed_request()
        assert '<h1>Internal Server Error</h1>' in str(body)

    req = get_request()
    pub._set_request(req)
    req.form = {'format': 'json'}
    try:
        raise Exception('test')
    except Exception:
        body = pub.finish_failed_request()
        assert body == '{"err": 1}'

    req = get_request()
    pub.cfg['debug'] = {'debug_mode': True}
    pub.write_cfg()
    pub.set_config(request=req)
    pub._set_request(req)
    try:
        secret = 'toto'  # noqa pylint: disable=unused-variable
        raise Exception()
    except Exception:
        body = pub.finish_failed_request()
        assert 'Stack trace (most recent call first)' in str(body)
        # split looked up string so its occurence in the stacktrace doesn't count
        assert str('to' + 'to') not in str(body)
        assert str('secret = ' + "'********************'") in str(body)
        assert str('<div ' + 'class="error-page">') not in str(body)


def test_finish_interrupted_request():
    req = HTTPRequest(
        io.StringIO(''),
        {
            'SERVER_NAME': 'example.net',
            'SCRIPT_NAME': '',
            'CONTENT_TYPE': 'application/x-www-form-urlencoded',
            'CONTENT_LENGTH': '1',
        },
    )
    response = pub.process_request(req)
    assert b'Invalid request: unexpected end of request body' in response.getvalue()
    req = HTTPRequest(
        io.StringIO(''),
        {
            'SERVER_NAME': 'example.net',
            'SCRIPT_NAME': '',
            'CONTENT_TYPE': 'multipart/form-data',
            'CONTENT_LENGTH': '1',
        },
    )
    response = pub.process_request(req)
    assert b'Invalid request: multipart/form-data missing boundary' in response.getvalue()
    with pytest.raises(Http404):
        req = HTTPRequest(
            io.StringIO(''),
            {
                'SERVER_NAME': 'example.net',
                'SCRIPT_NAME': '',
                'PATH_INFO': '/gloubiboulga',
            },
        )
        response = pub.process_request(req)


def test_get_tenants():
    pub = create_temporary_pub()
    with open(os.path.join(pub.APP_DIR, 'xxx'), 'w'):
        pass  # create empty file
    os.mkdir(os.path.join(pub.APP_DIR, 'plop.invalid'))
    hostnames = [x.hostname for x in pub.__class__.get_tenants()]
    assert 'example.net' in hostnames
    assert 'xxx' not in hostnames
    assert 'plop.invalid' not in hostnames

    os.mkdir(os.path.join(pub.APP_DIR, 'example.org'))
    assert {x.hostname for x in pub.__class__.get_tenants()} == {'example.net', 'example.org'}

    # empty site-options
    with open(os.path.join(pub.APP_DIR, 'example.org', 'site-options.cfg'), 'w') as fd:
        pass
    assert {x.hostname for x in pub.__class__.get_tenants()} == {'example.net', 'example.org'}

    # site-options with appropriate hostname
    with open(os.path.join(pub.APP_DIR, 'example.org', 'site-options.cfg'), 'w') as fd:
        fd.write('[options]\nallowed_hostname = example.org\n')
    assert {x.hostname for x in pub.__class__.get_tenants()} == {'example.net', 'example.org'}

    # site-options with inappropriate hostname
    with open(os.path.join(pub.APP_DIR, 'example.org', 'site-options.cfg'), 'w') as fd:
        fd.write('[options]\nallowed_hostname = another-example.org\n')
    assert {x.hostname for x in pub.__class__.get_tenants()} == {'example.net'}


def test_register_cronjobs():
    pub.register_cronjobs()
    # noqa pylint: disable=not-an-iterable
    assert 'apply_global_action_timeouts' in [x.function.__name__ for x in pub.cronjobs]
    # noqa pylint: disable=not-an-iterable
    assert 'clean_sessions' in [x.function.__name__ for x in pub.cronjobs]
    # noqa pylint: disable=not-an-iterable
    assert 'clean_afterjobs' in [x.function.__name__ for x in pub.cronjobs]
    # noqa pylint: disable=not-an-iterable
    assert 'clean_tempfiles' in [x.function.__name__ for x in pub.cronjobs]
    # noqa pylint: disable=not-an-iterable
    assert 'clean_models' in [x.function.__name__ for x in pub.cronjobs]
    # noqa pylint: disable=not-an-iterable
    assert 'clean_thumbnails' in [x.function.__name__ for x in pub.cronjobs]
    # noqa pylint: disable=not-an-iterable
    assert 'clean_loggederrors' in [x.function.__name__ for x in pub.cronjobs]
    # noqa pylint: disable=not-an-iterable
    assert 'evaluate_jumps' in [x.name for x in pub.cronjobs]
    # noqa pylint: disable=not-an-iterable
    assert 'clean_saml_assertions' in [x.function.__name__ for x in pub.cronjobs]
    # noqa pylint: disable=not-an-iterable
    assert 'update_storage_all_formdefs' in [x.function.__name__ for x in pub.cronjobs]
    # noqa pylint: disable=not-an-iterable
    assert 'archive_workflow_traces' in [x.function.__name__ for x in pub.cronjobs]


def test_get_default_position():
    assert pub.get_default_position() == {'lat': 50.84, 'lon': 4.36}


def test_import_config_zip():
    pub = create_temporary_pub()
    pub.cfg['sp'] = {'what': 'ever'}
    pub.write_cfg()

    c = io.BytesIO()
    with zipfile.ZipFile(c, 'w') as z:
        z.writestr('config.json', json.dumps({'language': {'language': 'fr'}, 'whatever': ['a', 'b', 'c']}))
    c.seek(0)

    pub.import_zip(c)
    assert pub.cfg['language'] == {'language': 'fr'}
    assert pub.cfg['whatever'] == ['a', 'b', 'c']
    assert pub.cfg['sp'] == {'what': 'ever'}

    c = io.BytesIO()
    with zipfile.ZipFile(c, 'w') as z:
        z.writestr(
            'config.pck', pickle.dumps({'language': {'language': 'en'}, 'whatever2': ['a', 'b', {'c': 'd'}]})
        )
    c.seek(0)

    pub.import_zip(c)
    assert pub.cfg['language'] == {'language': 'fr'}  # pck is ignored by default

    pub.site_options.set('options', 'allow-config-pck-in-import', 'true')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)

    pub.import_zip(c)
    assert pub.cfg['language'] == {'language': 'en'}
    assert pub.cfg['sp'] == {'what': 'ever'}


def test_import_config_zip_no_overwrite():
    pub = create_temporary_pub()
    pub.cfg['emails'] = {'smtp_server': 'xxx'}
    pub.cfg['misc'] = {'sitename': 'xxx'}
    pub.write_cfg()

    c = io.BytesIO()
    with zipfile.ZipFile(c, 'w') as z:
        z.writestr(
            'config.json',
            json.dumps(
                {
                    'language': {'language': 'fr'},
                    'emails': {'smtp_server': 'yyy', 'email-tracking-code-reminder': 'Hello!'},
                    'misc': {'sitename': 'yyy', 'default-zoom-level': '13'},
                    'filetypes': {'1': {'mimetypes': ['application/pdf'], 'label': 'Documents PDF'}},
                }
            ),
        )
    c.seek(0)

    pub.import_zip(c, overwrite_settings=False)
    assert pub.cfg['language'] == {'language': 'fr'}
    assert pub.cfg['emails'] == {'smtp_server': 'xxx', 'email-tracking-code-reminder': 'Hello!'}
    assert pub.cfg['misc'] == {'sitename': 'xxx', 'default-zoom-level': '13'}
    assert pub.cfg['filetypes'] == {'1': {'mimetypes': ['application/pdf'], 'label': 'Documents PDF'}}


def clear_log_files():
    shutil.rmtree(os.path.join(get_publisher().APP_DIR, 'cron-logs'), ignore_errors=True)
    for log_dir in glob.glob(os.path.join(get_publisher().APP_DIR, '*', 'cron-logs')):
        shutil.rmtree(log_dir, ignore_errors=True)


def get_logs(hostname=None, ignore_sql=False):
    pub = get_publisher()
    now = localtime()
    if hostname:
        base_dir = os.path.join(pub.APP_DIR, hostname)
    else:
        base_dir = pub.APP_DIR
    with open(
        os.path.join(base_dir, 'cron-logs', now.strftime('%Y'), 'cron.log-%s' % now.strftime('%Y%m%d'))
    ) as fd:
        lines = fd.readlines()
    # split on ] to get what follows the PID
    lines = [line.split(']', 1)[1].strip() for line in lines]
    if ignore_sql:
        lines = [x for x in lines if not x.startswith('SQL:')]
    # remove resource usage details
    clean_usage_line_re = re.compile('(resource usage summary).*')
    lines = [clean_usage_line_re.sub(r'\1', x) for x in lines]
    return lines


def get_sql_cron_statuses():
    _, cur = sql.get_connection_and_cursor()
    cur.execute("SELECT key, value FROM wcs_meta WHERE key LIKE 'cron-status-%'")
    rows = cur.fetchall()
    cur.close()
    return dict(rows)


def test_cron_command(settings):
    pub = create_temporary_pub()
    offset = ord(settings.SECRET_KEY[-1]) % 60

    # make sure there's a job to execute
    _, cur = sql.get_connection_and_cursor()
    cur.execute('''DELETE FROM wcs_meta WHERE key LIKE 'reindex%' OR key LIKE 'cron%' ''')
    sql.set_reindex('test_cron_command', 'needed')
    sql.cleanup_connection()

    hostnames = ['example.net', 'foo.bar', 'something.com']
    for hostname in hostnames:
        if not os.path.exists(os.path.join(pub.APP_DIR, hostname)):
            os.mkdir(os.path.join(pub.APP_DIR, hostname))
            # add a config.pck with postgresql configuration
            with open(os.path.join(pub.APP_DIR, hostname, 'config.pck'), 'wb') as fd:
                pickle.dump(pub.cfg, file=fd)

    with mock.patch('wcs.qommon.management.commands.cron.cron_worker') as cron_worker:
        with mock.patch('wcs.qommon.publisher.QommonPublisher.get_tenants') as mock_tenants:
            mock_tenants.return_value = [
                Tenant(os.path.join(pub.app_dir, x)) for x in ('example.net', 'foo.bar', 'something.com')
            ]
            clear_log_files()
            call_command('cron')
            assert cron_worker.call_count == 3
            assert get_logs() == [
                'starting cron (minutes offset is %s)' % offset,
            ]
            cron_worker.reset_mock()
            clear_log_files()
            call_command('cron', domain='example.net')
            assert cron_worker.call_count == 1
            assert get_logs() == [
                'starting cron (minutes offset is %s)' % offset,
            ]
            cron_worker.reset_mock()

            # check we're still running them all
            call_command('cron')
            assert cron_worker.call_count == 3
            cron_worker.reset_mock()

            assert get_sql_cron_statuses() == {
                'cron-status-example.net': 'done',
                'cron-status-foo.bar': 'done',
                'cron-status-something.com': 'done',
            }

            # disable cron on something.com
            site_options_path = os.path.join(pub.APP_DIR, 'something.com', 'site-options.cfg')
            with open(site_options_path, 'w') as fd:
                fd.write(
                    '''\
                    [variables]
                    disable_cron_jobs = True
                    '''
                )

            clear_log_files()
            call_command('cron')
            assert cron_worker.call_count == 2
            assert get_logs() == [
                'starting cron (minutes offset is %s)' % offset,
            ]
            os.unlink(site_options_path)

            assert get_sql_cron_statuses() == {
                'cron-status-example.net': 'done',
                'cron-status-foo.bar': 'done',
                'cron-status-something.com': 'done',
            }

            # simulate a running cron
            cron_worker.reset_mock()
            settings.CRON_WORKERS = 1
            get_publisher().set_tenant_by_hostname('example.net')
            sql.mark_cron_status('running')
            call_command('cron')
            assert cron_worker.call_count == 0

            assert get_sql_cron_statuses() == {
                'cron-status-example.net': 'running',
                'cron-status-foo.bar': 'done',
                'cron-status-something.com': 'done',
            }

            # with one more worker, the two other tenants can be run sequentially
            cron_worker.reset_mock()
            settings.CRON_WORKERS = 2
            call_command('cron')
            assert cron_worker.call_count == 2

            assert get_sql_cron_statuses() == {
                'cron-status-example.net': 'running',
                'cron-status-foo.bar': 'done',
                'cron-status-something.com': 'done',
            }

            get_publisher().set_tenant_by_hostname('example.net')
            sql.mark_cron_status('running')
            get_publisher().set_tenant_by_hostname('foo.bar')
            sql.mark_cron_status('running')
            get_publisher().set_tenant_by_hostname('something.com')
            sql.mark_cron_status('done')

            assert get_sql_cron_statuses() == {
                'cron-status-example.net': 'running',
                'cron-status-foo.bar': 'running',
                'cron-status-something.com': 'done',
            }
            cron_worker.reset_mock()
            call_command('cron')
            assert cron_worker.call_count == 0

            shutil.rmtree(os.path.join(pub.APP_DIR, 'foo.bar'))
            shutil.rmtree(os.path.join(pub.APP_DIR, 'something.com'))

    get_publisher().set_tenant_by_hostname('example.net')
    sql.mark_cron_status('done')
    # simulate a cron crash
    with mock.patch('wcs.qommon.management.commands.cron.cron_worker') as cron_worker:
        cron_worker.side_effect = NotImplementedError
        with pytest.raises(NotImplementedError):
            call_command('cron')
        assert cron_worker.call_count == 1

    # verify that the job is not marked as running
    assert get_sql_cron_statuses().get('cron-status-example.net') == 'done'

    # disable cron system
    with override_settings(DISABLE_CRON_JOBS=True):
        with mock.patch('wcs.qommon.management.commands.cron.cron_worker') as cron_worker:
            call_command('cron', domain='example.net')
            assert cron_worker.call_count == 0


def test_cron_command_no_jobs(settings):
    create_temporary_pub()

    # make sure there are no leftover jobs to execute
    _, cur = sql.get_connection_and_cursor()
    cur.execute('''DELETE FROM wcs_meta WHERE key LIKE 'reindex%' OR key LIKE 'cron%' ''')
    sql.cleanup_connection()

    with (
        mock.patch('wcs.qommon.management.commands.cron.cron_worker') as cron_worker,
        mock.patch('wcs.qommon.management.commands.cron.get_jobs_since') as get_jobs_since,
    ):
        get_jobs_since.side_effect = lambda *args: set()
        call_command('cron')
        # first run was called as the database had be initialized
        assert cron_worker.call_count == 1

        # second run was not called as there was no job
        call_command('cron')
        assert cron_worker.call_count == 1


def test_cron_command_reindex_db(settings):
    create_temporary_pub()

    _, cur = sql.get_connection_and_cursor()
    cur.execute('''DELETE FROM wcs_meta WHERE key LIKE 'reindex%' OR key LIKE 'cron%' ''')
    sql.cleanup_connection()

    with mock.patch('wcs.sql.reindex') as reindex:
        call_command('cron')  # 1st run, always
        assert reindex.call_count == 0

        # force second run as there's a delayed migration to run
        sql.set_reindex('test_cron_command', 'needed')
        call_command('cron')
        assert reindex.call_count == 1


def test_cron_command_jobs(settings):
    create_temporary_pub()

    # run a specific job
    jobs = []

    def job1(pub, job=None):
        jobs.append('job1')

    def job2(pub, job=None):
        jobs.append('job2')

    def job3(pub, job=None):
        jobs.append('job3')
        for key in ['foo', 'bar', 'blah']:
            with job.log_long_job(key):
                pass

    @classmethod
    def register_test_cronjobs(cls):
        cls.register_cronjob(CronJob(job1, name='job1', days=[10]))
        cls.register_cronjob(CronJob(job2, name='job2', days=[10]))
        cls.register_cronjob(CronJob(job3, name='job3', days=[10]))

    get_publisher().set_tenant_by_hostname('example.net')
    sql.mark_cron_status('done')

    with mock.patch('wcs.publisher.WcsPublisher.register_cronjobs', register_test_cronjobs):
        get_publisher_class().cronjobs = []
        call_command('cron', job_name='job1', domain='example.net')
        assert jobs == ['job1']
        jobs = []
        get_publisher_class().cronjobs = []
        clear_log_files()
        call_command('cron', job_name='job2', domain='example.net')
        assert jobs == ['job2']
        assert get_logs('example.net') == ['start', "running jobs: ['job2']", 'resource usage summary']
        get_publisher_class().cronjobs = []
        jobs = []
        clear_log_files()
        with mock.patch('wcs.qommon.cron.CronJob.LONG_JOB_DURATION', 0):
            call_command('cron', job_name='job2', domain='example.net')
        assert get_logs('example.net') == [
            'start',
            "running jobs: ['job2']",
            'long job: job2 (took 0 minutes, 0 CPU minutes)',
            'resource usage summary',
        ]
        assert jobs == ['job2']
        get_publisher_class().cronjobs = []
        jobs = []
        clear_log_files()
        with mock.patch('wcs.qommon.cron.CronJob.LONG_JOB_DURATION', 0):
            call_command('cron', job_name='job3', domain='example.net')
        assert get_logs('example.net') == [
            'start',
            "running jobs: ['job3']",
            'job3: running on "foo" took 0 minutes, 0 CPU minutes',
            'job3: running on "bar" took 0 minutes, 0 CPU minutes',
            'job3: running on "blah" took 0 minutes, 0 CPU minutes',
            'long job: job3 (took 0 minutes, 0 CPU minutes)',
            'resource usage summary',
        ]
        assert jobs == ['job3']


def test_cron_command_rewind_jobs(settings, freezer):
    pub = create_temporary_pub()
    pub.set_tenant_by_hostname('example.net')

    offset = ord(settings.SECRET_KEY[-1]) % 60

    jobs = []

    def job1(pub, job=None):
        jobs.append('job1')

    def job2(pub, job=None):
        jobs.append('job2')

    def job3(pub, job=None):
        jobs.append('job3')

    _, cur = sql.get_connection_and_cursor()
    cur.execute("DELETE FROM wcs_meta WHERE key LIKE 'cron%%'")
    cur.close()

    @classmethod
    def register_test_cronjobs(cls):
        cls.register_cronjob(CronJob(job1, name='job1', minutes=[0, 3]))
        cls.register_cronjob(CronJob(job2, name='job2', hours=[0], minutes=[2]))
        cls.register_cronjob(CronJob(job3, name='job3', minutes=[10]))

    start_time = datetime.datetime(2021, 4, 6, 4, offset - 1)
    freezer.move_to(start_time)
    with mock.patch('wcs.publisher.WcsPublisher.register_cronjobs', register_test_cronjobs):
        # first run, not on offset, nothing is run
        get_publisher_class().cronjobs = []
        call_command('cron')
        assert jobs == []

        # write down a past datetime in database
        _, cur = sql.get_connection_and_cursor()
        cur.execute(
            "UPDATE wcs_meta SET created_at = %s, updated_at = %s WHERE key LIKE 'cron%%'",
            (localtime() - datetime.timedelta(hours=1), localtime() - datetime.timedelta(hours=1)),
        )
        cur.close()

        call_command('cron')
        assert sorted(jobs) == ['job1', 'job3']

        # since past day
        _, cur = sql.get_connection_and_cursor()
        cur.execute(
            "UPDATE wcs_meta SET created_at = %s, updated_at = %s WHERE key LIKE 'cron%%'",
            (localtime() - datetime.timedelta(days=1), localtime() - datetime.timedelta(days=1)),
        )
        cur.close()
        jobs = []
        call_command('cron')
        assert sorted(jobs) == ['job1', 'job2', 'job3']


def test_cron_command_catch_up_jobs(settings, freezer):
    pub = create_temporary_pub()
    pub.cronjobs = []
    pub.set_tenant_by_hostname('example.net')

    offset = ord(settings.SECRET_KEY[-1]) % 60

    jobs = []

    def job1(pub, job=None):
        jobs.append('job1')
        freezer.tick(datetime.timedelta(minutes=30))

    def job2(pub, job=None):
        jobs.append('job2')

    def job3(pub, job=None):
        jobs.append('job3')

    _, cur = sql.get_connection_and_cursor()
    cur.execute("DELETE FROM wcs_meta WHERE key LIKE 'cron%%'")
    cur.close()

    @classmethod
    def register_test_cronjobs(cls):
        cls.register_cronjob(CronJob(job1, name='job1', minutes=[10, 40]))
        cls.register_cronjob(CronJob(job2, name='job2', hours=[0], minutes=[2]))
        cls.register_cronjob(CronJob(job3, name='job3', minutes=[30]))

    start_time = make_aware(datetime.datetime(2021, 4, 6, 4, offset))
    freezer.move_to(start_time)
    with mock.patch('wcs.publisher.WcsPublisher.register_cronjobs', register_test_cronjobs):
        # first run, not on offset, nothing is run
        get_publisher_class().cronjobs = []
        call_command('cron')
        assert jobs == []

        # write down datetime in database (as NOW in database queries is not affected
        # by frozen time)
        _, cur = sql.get_connection_and_cursor()
        cur.execute(
            "UPDATE wcs_meta SET created_at = %s, updated_at = %s WHERE key LIKE 'cron%%'",
            (start_time, start_time),
        )
        cur.close()

        start_time = make_aware(datetime.datetime(2021, 4, 6, 4, offset) + datetime.timedelta(minutes=10))
        freezer.move_to(start_time)

        call_command('cron')
        assert set(jobs) == {'job1', 'job3'}
        get_publisher_class().cronjobs = []
        assert "running more jobs: ['job3']" in get_logs('example.net')


def test_cron_command_job_exception(settings):
    create_temporary_pub()

    def job1(pub, job=None):
        raise Exception('Error')

    @classmethod
    def register_test_cronjobs(cls):
        cls.register_cronjob(CronJob(job1, name='job1', days=[10]))

    get_publisher().set_tenant_by_hostname('example.net')
    sql.mark_cron_status('done')

    with mock.patch('wcs.publisher.WcsPublisher.register_cronjobs', register_test_cronjobs):
        get_publisher_class().cronjobs = []
        clear_log_files()
        call_command('cron', job_name='job1', domain='example.net')
        assert get_logs('example.net') == [
            'start',
            "running jobs: ['job1']",
            'exception running job job1: Error',
            'resource usage summary',
        ]

    clean_temporary_pub()


def test_cron_command_job_log(settings):
    pub = create_temporary_pub()

    def job1(pub, job=None):
        job.log('hello')
        job.log_debug('debug')

    @classmethod
    def register_test_cronjobs(cls):
        cls.register_cronjob(CronJob(job1, name='job1', days=[10]))

    get_publisher().set_tenant_by_hostname('example.net')
    sql.mark_cron_status('done')

    with mock.patch('wcs.publisher.WcsPublisher.register_cronjobs', register_test_cronjobs):
        get_publisher_class().cronjobs = []
        clear_log_files()
        call_command('cron', job_name='job1', domain='example.net')
        assert get_logs('example.net') == [
            'start',
            "running jobs: ['job1']",
            'hello',
            'resource usage summary',
        ]

        pub.load_site_options()
        pub.site_options.set('options', 'cron-log-level', 'debug')
        with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
            pub.site_options.write(fd)

        clear_log_files()
        call_command('cron', job_name='job1', domain='example.net')
        assert get_logs('example.net')[:3] == ['start', "running jobs: ['job1']", 'hello']
        assert re.match(r'\(mem: .*\) debug', get_logs('example.net')[3])

    clean_temporary_pub()


def test_clean_afterjobs():
    pub = create_temporary_pub()

    job1 = AfterJob()
    job1.status = 'completed'
    job1.creation_time = localtime() - datetime.timedelta(days=4)
    job1.completion_time = localtime() - datetime.timedelta(days=4)
    job1.store()

    job2 = AfterJob()
    job2.status = 'failed'
    job2.creation_time = localtime()
    job2.completion_time = localtime()
    job2.store()

    job3 = AfterJob()
    job3.status = 'running'
    job3.creation_time = localtime() - datetime.timedelta(days=4)
    job3.store()

    pub.clean_afterjobs()
    assert AfterJob.count() == 2
    assert [x.id for x in AfterJob.select(order_by='creation_time')] == [job3.id, job2.id]

    # after 5 days all jobs are deleted
    job3.creation_time = localtime() - datetime.timedelta(days=6)
    job3.store()

    pub.clean_afterjobs()
    assert AfterJob.count() == 1
    assert AfterJob.select(order_by='id')[0].id == job2.id


def test_clean_tempfiles():
    pub = create_temporary_pub()
    pub.clean_tempfiles()

    dirname = os.path.join(pub.app_dir, 'tempfiles')
    if not os.path.exists(dirname):
        os.mkdir(dirname)

    with open(os.path.join(dirname, 'a'), 'w') as fd:
        fd.write('a')

    with open(os.path.join(dirname, 'b'), 'w') as fd:
        os.utime(fd.fileno(), times=(time.time() - 40 * 86400, time.time() - 40 * 86400))

    pub.clean_tempfiles()
    assert os.listdir(dirname) == ['a']


def test_clean_saml_assertions():
    pub = create_temporary_pub()
    pub.clean_saml_assertions()

    from wcs.sql import UsedSamlAssertionId

    assert UsedSamlAssertionId.consume_assertion_id('a', datetime.datetime.now())
    assert UsedSamlAssertionId.consume_assertion_id('b', datetime.datetime.now() + datetime.timedelta(days=1))

    pub.clean_saml_assertions()

    assert UsedSamlAssertionId.consume_assertion_id('a', datetime.datetime.now())
    assert not UsedSamlAssertionId.consume_assertion_id('b', datetime.datetime.now())


def test_clean_models():
    pub = create_temporary_pub()
    pub.clean_models()

    Workflow.wipe()
    if os.path.exists(os.path.join(pub.app_dir, 'models')):
        shutil.rmtree(os.path.join(pub.app_dir, 'models'))

    def make_wf():
        workflow = Workflow(name='test')
        st1 = workflow.add_status('Status1', 'st1')
        export_to = st1.add_action('export_to_model')
        export_to.label = 'test'
        upload = Upload('/foo/bar', content_type='application/vnd.oasis.opendocument.text')
        file_content = b'''PK\x03\x04\x14\x00\x00\x08\x00\x00\'l\x8eG^\xc62\x0c\'\x00'''
        upload.fp = io.BytesIO()
        upload.fp.write(file_content)
        upload.fp.seek(0)
        export_to.model_file = UploadedFile('models', 'a', upload)
        st1.add_action('export_to_model')  # empty model
        st1.add_action('sendmail')  # other item
        # export/import to get models stored in the expected way
        workflow.store()
        workflow = Workflow.import_from_xml_tree(
            ET.fromstring(ET.tostring(workflow.export_to_xml(include_id=True))), include_id=True
        )
        workflow.store()

    make_wf()
    make_wf()

    dirname = os.path.join(pub.app_dir, 'models')
    assert len(os.listdir(dirname)) == 3
    assert set(os.listdir(dirname)) == {
        'a',
        'export_to_model-1-st1-1.upload',
        'export_to_model-2-st1-1.upload',
    }

    for filename in ['export_to_model-2-st1-1.upload', 'b']:
        with open(os.path.join(dirname, filename), 'w') as fd:
            os.utime(fd.fileno(), times=(time.time() - 2 * 86400 - 1, time.time() - 2 * 86400 - 1))
    assert len(os.listdir(dirname)) == 4
    assert set(os.listdir(dirname)) == {
        'a',
        'b',
        'export_to_model-1-st1-1.upload',
        'export_to_model-2-st1-1.upload',
    }

    pub.clean_models()
    assert len(os.listdir(dirname)) == 3
    assert set(os.listdir(dirname)) == {
        # too soon
        'a',
        # filename is used
        'export_to_model-1-st1-1.upload',
        'export_to_model-2-st1-1.upload',
    }


def test_clean_thumbnails():
    pub = create_temporary_pub()
    pub.clean_thumbnails()

    dirname = os.path.join(pub.app_dir, 'thumbs')
    if not os.path.exists(dirname):
        os.mkdir(dirname)

    with open(os.path.join(dirname, 'a'), 'w') as fd:
        fd.write('a')

    with open(os.path.join(dirname, 'b'), 'w') as fd:
        os.utime(fd.fileno(), times=(time.time() - 40 * 86400, time.time() - 40 * 86400))

    pub.clean_thumbnails()
    assert os.listdir(dirname) == ['a']


def test_clean_loggederrors():
    pub = create_temporary_pub()

    error1 = LoggedError()
    error1.first_occurence_timestamp = datetime.datetime.now() - datetime.timedelta(seconds=1)
    error1.latest_occurence_timestamp = datetime.datetime.now() - datetime.timedelta(seconds=1)
    error1.store()

    error2 = LoggedError()
    error2.first_occurence_timestamp = datetime.datetime.now() - datetime.timedelta(days=30, seconds=1)
    error2.latest_occurence_timestamp = datetime.datetime.now() - datetime.timedelta(seconds=1)
    error2.store()

    error3 = LoggedError()
    error3.first_occurence_timestamp = datetime.datetime.now() - datetime.timedelta(days=30, seconds=1)
    error3.latest_occurence_timestamp = datetime.datetime.now() - datetime.timedelta(days=30, seconds=1)
    error3.store()

    error4 = LoggedError()
    error4.first_occurence_timestamp = datetime.datetime.now() - datetime.timedelta(days=30, seconds=1)
    error4.latest_occurence_timestamp = datetime.datetime.now() - datetime.timedelta(days=29, seconds=1)
    error4.store()

    pub.clean_loggederrors()

    # error3 was deleted
    assert LoggedError.count() == 3
    assert LoggedError.get(error1.id)
    assert LoggedError.get(error2.id)
    assert LoggedError.get(error4.id)


def test_get_site_language():
    pub = create_temporary_pub()

    # no request
    pub.cfg['language'] = {'language': 'en'}
    assert pub.get_site_language() == 'en'

    pub.cfg['language'] = {'language': 'HTTP'}
    assert pub.get_site_language() is None

    req = get_request()
    pub._set_request(req)

    pub.cfg['language'] = {'language': 'en'}
    assert pub.get_site_language() == 'en'

    pub.cfg['language'] = {'language': 'fr'}
    assert pub.get_site_language() == 'fr'

    pub.cfg['language'] = {'language': 'HTTP'}
    assert pub.get_site_language() is None

    pub.cfg['language']['languages'] = ['en', 'fr']
    req.environ['HTTP_ACCEPT_LANGUAGE'] = 'fr,en;q=0.7,es;q=0.3'
    assert pub.get_site_language() == 'fr'

    req.environ['HTTP_ACCEPT_LANGUAGE'] = 'xy'  # non-existing
    assert pub.get_site_language() is None

    req.environ['HTTP_ACCEPT_LANGUAGE'] = 'xy,fr,en;q=0.7,es;q=0.3'
    assert pub.get_site_language() == 'fr'


def test_maxsize_dict():
    d = MaxSizeDict()
    with pytest.raises(KeyError):
        d['a']  # noqa pylint: disable=pointless-statement
    for i in range(256):
        d[str(i)] = f'i : {i}'
        try:
            assert d['10']  # keep accessing low value
        except KeyError:
            pass
    # kept keys are the recently added one + '10' that we kept accessing
    assert set(d.keys()) == set(['10'] + [str(x) for x in range(129, 256)])
