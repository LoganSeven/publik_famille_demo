import copy
import http.cookies
import json
import os
import random
import shutil
import tempfile
import urllib.parse

import psycopg2
import responses
from django.conf import settings
from django.core import mail
from quixote import cleanup, get_publisher
from webtest import TestApp

import wcs
import wcs.middleware
import wcs.qommon.emails
import wcs.qommon.sms
import wcs.wsgi
from wcs import compat, sessions, sql
from wcs.logged_errors import LoggedError
from wcs.qommon import force_str
from wcs.testdef import TestDef, TestResult, TestResults

# required for Python <3.8
http.cookies.Morsel._reserved.setdefault('samesite', 'SameSite')


class KnownElements:
    sql_app_dir = None
    sql_db_name = None
    lazy_app_dir = None


known_elements_by_prefix = {}


def sql_mark_current_test():
    conn = sql.get_connection()
    cur = conn.cursor()
    cur.execute(
        'INSERT INTO wcs_meta (key, value) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET value = %s',
        (
            'PYTEST_CURRENT_TEST',
            os.environ.get('PYTEST_CURRENT_TEST'),
            os.environ.get('PYTEST_CURRENT_TEST'),
        ),
    )
    sql.cleanup_connection()


def create_temporary_pub(lazy_mode=False):
    prefix = os.environ.get('PYTEST_XDIST_WORKER') or ''
    known_elements = known_elements_by_prefix.setdefault(prefix, KnownElements())
    if get_publisher():
        get_publisher().cleanup()
        cleanup()
    if lazy_mode and known_elements.lazy_app_dir:
        APP_DIR = known_elements.lazy_app_dir
    elif not lazy_mode and known_elements.sql_app_dir:
        APP_DIR = known_elements.sql_app_dir
    else:
        APP_DIR = tempfile.mkdtemp(prefix=f'tmp_{prefix}')
        if lazy_mode:
            known_elements.lazy_app_dir = APP_DIR
        else:
            known_elements.sql_app_dir = APP_DIR

    publisher_class = copy.deepcopy(compat.CompatWcsPublisher)
    publisher_class.APP_DIR = APP_DIR
    publisher_class.DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(wcs.__file__), '..', 'data'))
    publisher_class.cronjobs = None
    pub = publisher_class.create_publisher()
    # allow saving the user
    pub.app_dir = os.path.join(APP_DIR, 'example.net')

    pub.user_class = sql.SqlUser
    pub.test_user_class = sql.TestUser
    pub.role_class = sql.Role
    pub.token_class = sql.Token
    pub.session_class = sql.Session
    pub.custom_view_class = sql.CustomView
    pub.snapshot_class = sql.Snapshot

    pub.session_manager_class = sessions.StorageSessionManager
    pub.session_manager = pub.session_manager_class(session_class=pub.session_class)

    for directory in ('scripts', 'thumbs'):
        if os.path.exists(os.path.join(pub.APP_DIR, directory)):
            shutil.rmtree(os.path.join(pub.APP_DIR, directory))
        if os.path.exists(os.path.join(pub.app_dir, directory)):
            shutil.rmtree(os.path.join(pub.app_dir, directory))

    created = False
    if not os.path.exists(pub.app_dir):
        os.mkdir(pub.app_dir)
        created = True

    # always reset site-options.cfg
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        fd.write('[wscall-secrets]\n')
        fd.write('idp.example.net = BAR\n')
        fd.write('\n')
        fd.write('[options]\n')
        fd.write('formdef-captcha-option = true\n')
        fd.write('formdef-appearance-keywords = true\n')
        fd.write('workflow-resubmit-action = true\n')
        if not lazy_mode:
            fd.write('force-lazy-mode = false\n')

    # make sure site options are not cached
    pub.site_options = None

    pub.cfg = {}
    pub.cfg['misc'] = {
        'charset': 'utf-8',
        'frontoffice-url': 'http://example.net',
    }
    pub.cfg['language'] = {'language': 'en'}
    pub.write_cfg()

    if not created:
        pub.cfg['postgresql'] = {'database': known_elements.sql_db_name, 'user': os.environ['USER']}
        LoggedError.wipe()
        sql.WorkflowTrace.wipe()
        sql.Audit.wipe()
        sql_mark_current_test()
        pub.write_cfg()
        pub.reset_caches()
        return pub

    os.symlink(os.path.join(os.path.dirname(__file__), 'templates'), os.path.join(pub.app_dir, 'templates'))

    conn = psycopg2.connect(user=os.environ['USER'], dbname='postgres')
    conn.set_isolation_level(psycopg2.extensions.ISOLATION_LEVEL_AUTOCOMMIT)
    i = 0
    while True:
        dbname = 'wcstests_%s_%d' % (prefix, random.randint(0, 100000))
        known_elements.sql_db_name = dbname
        try:
            cur = conn.cursor()
            cur.execute('CREATE DATABASE %s' % dbname)
            break
        except psycopg2.Error:
            if i < 5:
                i += 1
                continue
            raise
        finally:
            cur.close()

    pub.cfg['postgresql'] = {'database': dbname, 'user': os.environ['USER']}
    pub.write_cfg()

    sql.do_user_table()
    sql.do_role_table()
    sql.do_tokens_table()
    sql.do_tracking_code_table()
    sql.do_session_table()
    sql.do_transient_data_table()
    sql.do_custom_views_table()
    sql.do_snapshots_table()
    sql.do_loggederrors_table()
    sql.SqlCategory.do_table()
    sql.SqlFormDef.do_table()
    sql.SqlCardDef.do_table()
    sql.SqlBlockDef.do_table()
    sql.SqlWorkflow.do_table()
    sql.SqlAfterJob.do_table()
    sql.SqlDataSource.do_table()
    sql.SqlMailTemplate.do_table()
    sql.SqlCommentTemplate.do_table()
    sql.SqlWsCall.do_table()
    sql.Audit.do_table()
    sql.do_meta_table()
    TestDef.do_table()
    TestResults.do_table()
    TestResult.do_table()
    sql.WorkflowTrace.do_table()
    sql.Application.do_table()
    sql.ApplicationElement.do_table()
    sql.SearchableFormDef.do_table()
    sql.TranslatableMessage.do_table()
    sql.UsedSamlAssertionId.do_table()
    sql.ApiAccess.do_table()
    sql.init_global_table()

    conn.close()

    sql_mark_current_test()

    return pub


def clean_temporary_pub():
    prefix = os.environ.get('PYTEST_XDIST_WORKER') or ''
    known_elements = known_elements_by_prefix.setdefault(prefix, KnownElements())
    if get_publisher():
        get_publisher().cleanup()
    if known_elements.sql_app_dir and os.path.exists(known_elements.sql_app_dir):
        shutil.rmtree(known_elements.sql_app_dir)
        known_elements.sql_app_dir = None
    if known_elements.sql_db_name:
        conn = psycopg2.connect(user=os.environ['USER'], dbname='postgres')
        conn.set_isolation_level(psycopg2.extensions.ISOLATION_LEVEL_AUTOCOMMIT)
        try:
            cur = conn.cursor()
            cur.execute('DROP DATABASE %s' % known_elements.sql_db_name)
            cur.close()
        except psycopg2.Error as e:
            print(e)
        known_elements.sql_db_name = None


def get_app(pub, https=False):
    extra_environ = {'HTTP_HOST': 'example.net', 'REMOTE_ADDR': '127.0.0.1'}
    if https:
        settings.SECURE_PROXY_SSL_HEADER = ('HTTPS', 'on')
        extra_environ['HTTPS'] = 'on'
    else:
        extra_environ['HTTPS'] = 'off'
    return TestApp(wcs.wsgi.application, extra_environ=extra_environ)


def login(app, username='admin', password='admin'):
    login_page = app.get('/login/')
    login_form = login_page.forms['login-form']
    login_form['username'] = username
    login_form['password'] = password
    resp = login_form.submit()
    assert resp.status_int == 302
    return app


class Email:
    def __init__(self, email):
        self.email = email

    @property
    def msg(self):
        return self.email.message()

    @property
    def email_rcpt(self):
        return self.email.recipients()

    @property
    def payload(self):
        return force_str(self.payloads[0])

    @property
    def payloads(self):
        if self.msg.is_multipart():
            return [x.get_payload(decode=True) for x in self.msg.get_payload()]
        return [self.msg.get_payload(decode=True)]

    @property
    def to(self):
        return self.email.message()['To']

    def get(self, key):
        return getattr(self.email, key)

    def __getitem__(self, key):
        if key in ['msg', 'email_rcpt', 'payload', 'payloads', 'to']:
            return getattr(self, key)
        if key == 'from':
            key = 'from_email'
        return getattr(self.email, key)


class Emails:
    def __contains__(self, value):
        return self[value] is not None

    def __getitem__(self, key):
        for em in mail.outbox:
            if em.subject == key:
                return Email(em)


class EmailsMocking:
    def get(self, subject):
        return self.emails[subject]

    def get_latest(self, part=None):
        email = Email(mail.outbox[-1])
        if part:
            return email.get(part) if email else None
        return email

    def empty(self):
        mail.outbox = []

    def count(self):
        return len(mail.outbox)

    @property
    def latest_subject(self):
        return mail.outbox[-1].subject

    @property
    def emails(self):
        return Emails()

    def get_subjects(self):
        for em in mail.outbox:
            yield em.subject

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, tb):
        pass


class MockSubstitutionVariables:
    def get_substitution_variables(self):
        return {'bar': 'Foobar', 'foo': '1 < 3', 'email': 'sub@localhost', 'empty': ''}


class HttpRequestsMocking:
    def __init__(self):
        self.requests = []

    def __enter__(self):
        self.requests_mock = responses.RequestsMock(assert_all_requests_are_fired=False)
        self.requests_mock.get('http://remote.example.net/')
        self.requests_mock.post('http://remote.example.net/')
        self.requests_mock.put('http://remote.example.net/')
        self.requests_mock.patch('http://remote.example.net/')
        self.requests_mock.get('http://remote.example.net/204', status=204)
        self.requests_mock.get('http://remote.example.net/400', status=400, body='bad request')
        self.requests_mock.get(
            'http://remote.example.net/400-json',
            status=400,
            json={
                'err': 1,
                'err_desc': ':(',
                'err_class': 'foo_bar',
            },
        )
        self.requests_mock.get('http://remote.example.net/404', status=404, body='page not found')
        self.requests_mock.get('http://remote.example.net/404-json', status=404, json={'err': 'not-found'})
        self.requests_mock.get('http://remote.example.net/500', status=500, body='internal server error')
        self.requests_mock.get('http://remote.example.net/json', json={'foo': 'bar'})
        self.requests_mock.post('http://remote.example.net/json', json={'foo': 'bar'})
        self.requests_mock.delete('http://remote.example.net/json', json={'foo': 'bar'})
        self.requests_mock.get(
            'http://remote.example.net/json-list', json={'data': [{'id': 'a', 'text': 'b'}]}
        )
        self.requests_mock.get(
            'http://remote.example.net/json-list-extra',
            json={'data': [{'id': 'a', 'text': 'b', 'foo': 'bar'}]},
        )
        self.requests_mock.get(
            'http://remote.example.net/json-list-extra-with-disabled',
            json={
                'data': [
                    {'id': 'a', 'text': 'b', 'foo': 'bar'},
                    {'id': 'c', 'text': 'd', 'foo': 'baz', 'disabled': True},
                ]
            },
        )
        self.requests_mock.get(
            'http://remote.example.net/xml', body='<?xml version="1.0"><foo/>', content_type='text/xml'
        )
        self.requests_mock.get(
            'http://remote.example.net/xml-errheader',
            body='<?xml version="1.0"><foo/>',
            content_type='text/xml',
            headers={'x-error-code': '1'},
        )
        self.requests_mock.get('http://remote.example.net/json-err0', json={'data': 'foo', 'err': 0})
        self.requests_mock.get('http://remote.example.net/json-err0int', json={'data': 'foo', 'err': '0'})
        self.requests_mock.get('http://remote.example.net/json-err1', json={'data': '', 'err': 1})
        self.requests_mock.get('http://remote.example.net/json-err1int', json={'data': '', 'err': '1'})
        self.requests_mock.get(
            'http://remote.example.net/json-err1-with-desc', json={'data': '', 'err': 1, 'err_desc': ':('}
        )
        self.requests_mock.get('http://remote.example.net/json-errstr', json={'data': '', 'err': 'bug'})
        self.requests_mock.get(
            'http://remote.example.net/json-list-err1', json={'data': [{'id': 'a', 'text': 'b'}], 'err': 1}
        )
        self.requests_mock.get(
            'http://remote.example.net/json-list-err1bis',
            json={
                'data': [{'id': 'a', 'text': 'b'}],
                'err': 1,
                'err_desc': ':(',
            },
        )
        self.requests_mock.get(
            'http://remote.example.net/json-list-errstr',
            json={
                'data': [{'id': 'a', 'text': 'b'}],
                'err': 'bug',
                'err_desc': ':(',
                'err_class': 'foo_bar',
            },
        )
        self.requests_mock.get('http://remote.example.net/json-errstr', json={'data': '', 'err': 'bug'})
        self.requests_mock.get(
            'http://remote.example.net/json-errheader0', json={'foo': 'bar'}, headers={'x-error-code': '0'}
        )
        self.requests_mock.get(
            'http://remote.example.net/json-errheader1', json={'foo': 'bar'}, headers={'x-error-code': '1'}
        )
        self.requests_mock.get(
            'http://remote.example.net/json-errheaderstr',
            json={'foo': 'bar'},
            headers={'x-error-code': 'bug'},
        )
        self.requests_mock.get(
            'http://remote.example.net/geojson',
            json={
                'features': [
                    {
                        'properties': {'id': '1', 'text': 'foo'},
                        'geometry': {'type': 'Point', 'coordinates': [1, 2]},
                    },
                    {
                        'properties': {'id': '2', 'text': 'bar'},
                        'geometry': {'type': 'Point', 'coordinates': [3, 4]},
                    },
                    {
                        'properties': {'text': 'bar'},  # no 'id', it will be ignored
                        'geometry': {'type': 'Point', 'coordinates': [3, 4]},
                    },
                ]
            },
        )

        def json_with_filter_cb(request):
            data = []
            base_id = 1
            for level in range(1, 4):
                for i in range(3**level):
                    text = ['foo', 'bar', 'baz'][i % 3]
                    structured_value = {'id': str(base_id + i), 'text': text}
                    structured_value['parent'] = (
                        str(base_id - 3 ** (level - 1) + (i // 3)) if base_id > 1 else '0'
                    )
                    data.append(structured_value)
                base_id += 3**level
            p = urllib.parse.urlparse(request.url)
            q = urllib.parse.parse_qs(p.query)
            if 'parent' in q:
                data = [x for x in data if q['parent'] and x['parent'] == q['parent'][0]]
            if 'id' in q:
                data = [x for x in data if q['id'] and x['id'] == q['id'][0]]
            return (200, {}, json.dumps({'data': data}))

        self.requests_mock.add_callback(
            responses.GET,
            'http://remote.example.net/json-with-filter',
            callback=json_with_filter_cb,
            content_type='application/json',
        )
        self.requests_mock.post('https://portal/api/notification/add/', json={})
        self.requests_mock.post('https://interco-portal/api/notification/add/', json={})

        with open(os.path.join(os.path.dirname(__file__), 'idp_metadata.xml')) as fd:
            self.requests_mock.get('http://authentic.example.net/idp/saml2/metadata', body=fd.read())
        with open(os.path.join(os.path.dirname(__file__), 'idp2_metadata.xml')) as fd:
            self.requests_mock.get('http://authentic2.example.net/idp/saml2/metadata', body=fd.read())
        self.requests_mock.start()
        return self

    def __exit__(self, exc_type, exc_value, tb):
        self.requests_mock.stop()

    def get_last(self, attribute):
        if attribute in ('timeout', 'verify'):
            return self.requests_mock.calls[-1].request.req_kwargs[attribute]
        return getattr(self.requests_mock.calls[-1].request, attribute)

    def empty(self):
        self.requests_mock.calls.reset()

    def count(self):
        return len(self.requests_mock.calls)


class SMSMocking(wcs.qommon.sms.PasserelleSMS):
    def get_sms_class(self):
        sms_cfg = get_publisher().cfg.get('sms', {})
        if sms_cfg.get('sender') and sms_cfg.get('passerelle_url'):
            return self
        return None

    def empty(self):
        self.sms = []

    def send(self, sender, destinations, text, counter_name):
        self.sms.append(
            {'sender': sender, 'destinations': destinations, 'text': text, 'counter': counter_name}
        )

    def __enter__(self):
        self.sms = []
        self.wcs_get_sms_class = wcs.qommon.sms.SMS.get_sms_class
        wcs.qommon.sms.SMS.get_sms_class = self.get_sms_class
        return self

    def __exit__(self, exc_type, exc_value, tb):
        del self.sms
        wcs.qommon.sms.SMS.get_sms_class = self.wcs_get_sms_class
