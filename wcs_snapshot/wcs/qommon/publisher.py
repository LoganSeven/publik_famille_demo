# w.c.s. - web application for online forms
# Copyright (C) 2005-2010  Entr'ouvert
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, see <http://www.gnu.org/licenses/>.

import builtins
import codecs
import collections
import configparser
import datetime
import hashlib
import html
import inspect
import io
import json
import linecache
import locale
import logging
import os
import pickle
import random
import re
import sys
import time
import traceback
import urllib.parse
from contextlib import contextmanager
from decimal import Decimal

from django.conf import settings
from django.http import Http404
from django.utils import translation
from django.utils.encoding import force_bytes, force_str
from django.views.debug import SafeExceptionReporterFilter
from quixote.publish import Publisher, get_publisher, get_request, get_response

from wcs.qommon.storage import Less

from . import _, errors, force_str, logger, storage, template
from .cron import CronJob
from .http_request import HTTPRequest
from .http_response import HTTPResponse
from .substitution import CompatibilityNamesDict, Substitutions

try:
    import sentry_sdk
except ImportError:
    sentry_sdk = None


class MaxSizeDict(collections.OrderedDict):
    # dictionary that will store at most 128 items, least recently used items are removed first.

    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        self.move_to_end(key, last=False)
        if len(self) > 128:
            self.popitem(last=True)

    def __getitem__(self, key):
        if key in self:
            self.move_to_end(key, last=False)
        return super().__getitem__(key)

    def get(self, key, default=None):
        # native get() doesn't use __getitem__
        return self[key] if key in self else default


class ImmediateRedirectException(Exception):
    def __init__(self, location):
        self.location = location


class UnknownTenantError(Exception):
    pass


class Tenant:
    def __init__(self, directory):
        self.directory = directory
        self.hostname = os.path.basename(directory)


class SiteOptionsBoolean:
    # support class for values from site-options [variables] section that
    # can be used as if strings as well as booleans
    true_strings = ('yes', 'true', 'on')
    false_strings = ('no', 'false', 'off')

    def __init__(self, value):
        if isinstance(value, str):
            self.as_str = value
            self.value = bool(value.lower() in self.true_strings)
        else:
            self.value = bool(value)
            self.as_str = str(value)

    def __bool__(self):
        return self.value

    def __eq__(self, other):
        return bool(self) is bool(SiteOptionsBoolean(other))

    def __str__(self):
        return self.as_str


class QommonPublisher(Publisher):
    # noqa pylint: disable=too-many-public-methods
    APP_NAME = None
    APP_DIR = None
    DATA_DIR = None
    ERROR_LOG = None

    root_directory_class = None
    backoffice_directory_class = None
    admin_directory_class = None

    session_manager_class = None
    user_class = None
    unpickler_class = None

    after_login_url = ''
    qommon_static_dir = 'static/'
    qommon_admin_css = 'css/dc2/admin.css'
    default_theme = 'django'

    site_options = None
    site_charset = 'utf-8'
    missing_appdir_redirect = None

    gettext = lambda self, message: message
    ngettext = lambda self, msgid1, msgid2, n: msgid1
    pgettext = lambda self, context, message: message

    app_dir = None
    _i18n_catalog = None

    sql_application_name = None

    is_running_test = False
    allowed_test_result_ids = None
    test_formdefs = None
    workflow_options_forced_value = None

    def get_root_url(self):
        if self.get_request():
            return self.get_request().environ['SCRIPT_NAME'] + '/'
        return '/'

    def get_application_static_files_root_url(self):
        # Typical applications will have their static files under the same root
        # directory as themselves; this method allows others to host them under
        # some other path, or even on some totally different hostname.
        return self.get_root_url()

    def get_frontoffice_url(self, without_script_name=False):
        frontoffice_url = get_cfg('misc', {}).get('frontoffice-url', None)
        if frontoffice_url:
            return frontoffice_url
        req = self.get_request()
        if req:
            if without_script_name:
                return '%s://%s' % (req.get_scheme(), req.get_server())
            return '%s://%s%s' % (
                req.get_scheme(),
                req.get_server(),
                urllib.parse.quote(req.environ.get('SCRIPT_NAME')),
            )
        return 'https://%s' % os.path.basename(get_publisher().app_dir)

    def get_backoffice_url(self):
        return urllib.parse.urljoin(self.get_frontoffice_url(), '/backoffice')

    def get_global_eval_dict(self):
        from . import evalutils as utils

        def compat_locals():
            frame = inspect.getouterframes(inspect.currentframe())[1][0]
            x = CompatibilityNamesDict(frame.f_locals)
            return x

        return {
            'datetime': datetime,
            'Decimal': Decimal,
            'codecs': codecs,
            'force_bytes': force_bytes,
            'force_str': force_str,
            'force_text': force_str,
            'locals': compat_locals,
            'vars': compat_locals,
            'random': random.SystemRandom(),
            're': re,
            'date': utils.date,
            'days': utils.days,
            'utils': utils,
        }

    def format_publish_error(self, exc):
        get_response().filter = {}
        if isinstance(exc, errors.PublishError) and hasattr(exc, 'render'):
            return exc.render()
        return errors.format_publish_error(exc)

    def finish_interrupted_request(self, exc):
        # it is exactly the same as in the base class, but using our own
        # HTTPResponse class
        if not self.config.display_exceptions and exc.private_msg:
            exc.private_msg = None  # hide it
        request = get_request()
        request.response = HTTPResponse(status=exc.status_code)
        if exc.status_code == 401:
            # include WWW-Authenticate header
            request.response.headers['WWW-Authenticate'] = 'Basic realm="%s"' % exc.realm
        if request.is_json():
            request.response.set_content_type('application/json')
            return json.dumps(
                {
                    'err': 1,
                    'err_class': str(exc.title),
                    'err_code': exc.err_code,
                    'err_desc': str(exc.public_msg) if exc.public_msg else None,
                }
            )
        request.response.set_robots_no_index()
        if isinstance(exc, errors.TraversalError):
            raise Http404()
        output = self.format_publish_error(exc)
        self.session_manager.finish_successful_request()
        return output

    def _generate_plaintext_error(self, request, original_response, exc_type, exc_value, tb, limit=None):
        error_file = io.StringIO()
        if limit is None:
            if hasattr(sys, 'tracebacklimit'):
                limit = sys.tracebacklimit
        print('Exception:', file=error_file)
        print("  type = '%s', value = '%s'" % (exc_type, exc_value), file=error_file)
        print('', file=error_file)

        while tb.tb_next:
            tb = tb.tb_next
        frame = tb.tb_frame
        error_file.write(self._format_traceback(frame, limit=limit))

        # include request and response dumps
        if request:
            error_file.write('\n')
            error_file.write(request.dump())
            error_file.write('\n')

        return error_file.getvalue()

    def _format_traceback(self, frame, limit=None):
        # format the traceback

        safe_filter = SafeExceptionReporterFilter()
        safe_filter.hidden_settings = re.compile(
            '|'.join(['domains', 'TOKEN', 'SECRET', 'PASS', 'SIGNATURE', 'HOST', 'PGCONN']), flags=re.I
        )

        error_file = io.StringIO()
        print('Stack trace (most recent call first):', file=error_file)
        n = 0
        while frame and (limit is None or n < limit):
            function = frame.f_code.co_name
            filename = frame.f_code.co_filename
            exclineno = frame.f_lineno
            locals = sorted(frame.f_locals.items(), key=lambda item: item[0])

            print('  File "%s", line %s, in %s' % (filename, exclineno, function), file=error_file)
            linecache.checkcache(filename)
            for lineno in range(exclineno - 2, exclineno + 3):
                line = linecache.getline(filename, lineno, frame.f_globals)
                if line:
                    if lineno == exclineno:
                        print('>%5s %s' % (lineno, line.rstrip()), file=error_file)
                    else:
                        print(' %5s %s' % (lineno, line.rstrip()), file=error_file)
            print('', file=error_file)
            if locals:
                print('  locals: ', file=error_file)
                for key, value in locals:
                    print('     %s =' % key, end=' ', file=error_file)
                    value = safe_filter.cleanse_setting(key, value)
                    try:
                        repr_value = repr(value)
                        if len(repr_value) > 10000:
                            repr_value = repr_value[:10000] + ' [...]'
                        print(repr_value, file=error_file)
                    except Exception:
                        print('<ERROR WHILE PRINTING VALUE>', file=error_file)
            print('', file=error_file)
            frame = frame.f_back
            n = n + 1
        return error_file.getvalue()

    def finish_successful_request(self):
        if not self.get_request().ignore_session:
            self.session_manager.finish_successful_request()

    def capture_exception(self, exc_info):
        if sentry_sdk and sentry_sdk.Hub.current.client:
            sentry_sdk.capture_exception(exc_info)

    def finish_failed_request(self):
        # duplicate at lot from parent class, just to use our own HTTPResponse
        request = get_request()
        original_response = request.response
        request.response = HTTPResponse()
        request.response.set_robots_no_index()

        (exc_type, exc_value, tb) = sys.exc_info()

        if exc_type is NotImplementedError:
            get_response().set_header('Content-Type', 'text/html')  # set back content-type
            return template.error_page(_('This feature is not yet implemented.'), error_title=_('Sorry'))

        self.capture_exception(sys.exc_info())

        error_summary = traceback.format_exception_only(exc_type, exc_value)
        error_summary = error_summary[0][0:-1]  # de-listify and strip newline

        plain_error_msg = self._generate_plaintext_error(request, original_response, exc_type, exc_value, tb)

        if request.is_json():
            request.response.set_content_type('application/json')
            d = {'err': 1}
            if self.config.display_exceptions:
                d['err_class'] = exc_type.__name__
                d['err_desc'] = error_summary
            error_page = json.dumps(d)
        else:
            request.response.set_header('Content-Type', 'text/html')
            if not self.config.display_exceptions:
                # DISPLAY_EXCEPTIONS is false, so return the most
                # secure (and cryptic) page.
                error_page = self._generate_internal_error(request)
            else:
                # Generate a plaintext page containing the traceback
                request.response.set_header('Content-Type', 'text/plain')
                error_page = plain_error_msg

        try:
            self.logger.log_internal_error(error_summary, plain_error_msg)
        except OSError:
            # wilr happen if there is no mail server available and exceptions
            # were configured to be mailed.
            pass

        if exc_type is SystemExit:
            raise exc_type

        request.response.set_status(500)
        self.session_manager.finish_failed_request()

        return error_page

    def has_i18n_enabled(self):
        return bool(self.cfg.get('language', {}).get('multilinguism'))

    def install_lang(self, request=None):
        if request:
            lang = request.language
        else:
            lang = self.get_site_language(request)
        if lang is None or lang not in (self.cfg.get('language', {}).get('languages') or []):
            lang = self.get_default_language()
        self.activate_language(lang, request=request)

    def activate_language(self, lang, request=None):
        translation.activate(lang)
        self.gettext = translation.gettext
        self.ngettext = translation.ngettext
        self.pgettext = translation.pgettext
        self.current_language = lang
        if request:
            request.LANGUAGE_CODE = lang
            if self.has_i18n_enabled() and self.cfg.get('language', {}).get('language', lang) != lang:
                from wcs.i18n import TranslatableMessage

                if lang not in self._i18n_catalog:
                    self._i18n_catalog[lang] = TranslatableMessage.load_as_catalog(lang)

    @contextmanager
    def with_language(self, language):
        if language == 'default':
            language = self.cfg.get('language', {}).get('language')
        if language is None or language == self.current_language:
            yield
        else:
            current_language = self.current_language
            self.activate_language(language, request=self.get_request())
            yield
            self.activate_language(current_language, request=self.get_request())

    def is_using_default_language(self):
        if not (self.has_i18n_enabled() and self.current_language):
            return True
        return bool(self.cfg.get('language', {}).get('language') == self.current_language)

    def translate(self, string, context=None, register=False):
        if type(string).__name__ == '__proxy__':  # lazy gettext
            return str(string)
        if not self.has_i18n_enabled():
            return string
        string = str(string)  # unlazy
        if not self.is_using_default_language():
            from wcs.i18n import TranslatableMessage

            string = string.strip()
            if self.current_language not in self._i18n_catalog:
                self._i18n_catalog[self.current_language] = TranslatableMessage.load_as_catalog(
                    self.current_language
                )
            catalog_string = self._i18n_catalog[self.current_language].get((context, string))
            if register and not catalog_string:
                from wcs.sql import Equal, Null

                criteria = [Equal('string', string)]
                if context:
                    criteria.append(Equal('context', context))
                else:
                    criteria.append(Null('context'))
                msgs = TranslatableMessage.exists(criteria)
                if not msgs:
                    msg = TranslatableMessage()
                    msg.context = context
                    msg.string = string
                    msg.locations = []
                    msg.store()
            return catalog_string or string
        return string

    def load_site_options(self):
        self.site_options = configparser.ConfigParser(interpolation=None)
        site_options_filename = os.path.join(self.app_dir, 'site-options.cfg')
        if not os.path.exists(site_options_filename):
            return
        self.site_options_exception = None
        try:
            self.site_options.read(site_options_filename, encoding='utf-8')
        except Exception as e:
            self.get_app_logger().error('failed to read site options file')
            # keep track of exception, to be raised later
            self.site_options_exception = e

    def has_site_option(self, option):
        return self.get_site_option(option) in (True, 'true')

    def get_site_option_defaults(self):
        return {}

    def get_site_option(self, option, section='options'):
        if self.site_options is None:
            self.load_site_options()
        try:
            return self.site_options.get(section, option)
        except (configparser.NoSectionError, configparser.NoOptionError):
            return self.get_site_option_defaults().get(section, {}).get(option)

    def get_site_options(self, section='options'):
        if self.site_options is None:
            self.load_site_options()
        try:
            return dict(self.site_options.items(section, raw=True))
        except configparser.NoSectionError:
            return {}

    def get_site_storages(self):
        if self.site_options is None:
            self.load_site_options()
        storages = {}
        for section, definition in self.site_options.items():
            if section.startswith('storage-') and 'label' in definition and 'class' in definition:
                storage_id = section[8:]
                storages[storage_id] = dict(definition)
                storages[storage_id]['id'] = storage_id
        return storages

    def set_config(self, request=None):
        self.reload_cfg()
        self.site_options = None  # reset at the beginning of a request
        self.after_jobs = []
        self.reset_caches()
        debug_cfg = self.cfg.get('debug', {})
        self.logger.error_email = debug_cfg.get('error_email')
        self.logger.error_email_from = self.cfg.get('emails', {}).get('from')
        self.config.display_exceptions = debug_cfg.get('debug_mode')
        self.config.form_tokens = True
        self.config.session_cookie_httponly = True
        self.config.allowed_methods = ['GET', 'HEAD', 'POST', 'PUT']

        if request:
            if request.get_scheme() == 'https':
                self.config.session_cookie_secure = True

        md5_hash = hashlib.md5()
        md5_hash.update(force_bytes(self.app_dir))
        self.config.session_cookie_name = 'sessionid-%s-%s' % (self.APP_NAME, md5_hash.hexdigest()[:6])
        self.config.session_cookie_path = '/'
        if not self._i18n_catalog:
            self._i18n_catalog = {}
        self.current_language = self.get_default_language()
        self._app_logger = self.get_app_logger(force=True)

    def reset_caches(self):
        self._cached_user_fields_formdef = None
        self._cached_objects = collections.defaultdict(MaxSizeDict)

    def set_app_dir(self, request):
        """
        Set the application directory, creating it if possible and authorized.
        """
        self.site_options = None  # reset at the beginning of a request
        canonical_hostname = request.get_server().lower().split(':')[0].rstrip('.')

        try:
            self.set_tenant_by_hostname(canonical_hostname, request=request)
        except UnknownTenantError:
            if self.missing_appdir_redirect:
                raise ImmediateRedirectException(self.missing_appdir_redirect)
            raise Http404()

    def init_publish(self, request):
        self.set_app_dir(request)

        self._http_adapter = None
        self._i18n_catalog = {}

        self.init_publisher_substitutions(request)

    def start_request(self):
        super().start_request()
        self.get_request().language = self.get_site_language(self.get_request())
        self.install_lang(self.get_request())

    def init_publisher_substitutions(self, request):
        self.substitutions = Substitutions()
        self.reset_formdata_state()
        self.substitutions.feed(request)

    def get_default_language(self):
        return self.cfg.get('language', {}).get('language', 'en')

    def get_site_language(self, request=None):
        if request is None:
            request = self.get_request()
        lang = self.cfg.get('language', {}).get('language', None)
        if lang == 'HTTP':
            # migrate to new configuration
            lang = None
            lang = self.cfg.get('language', {})['language'] = None
            self.cfg['language']['default_site_language'] = 'http'
        if self.cfg.get('language', {}).get('default_site_language') == 'http':
            if request is None:
                return None
            lang = None
            accepted_languages = request.get_header('Accept-Language')
            if accepted_languages:
                accepted_languages = [x.strip() for x in accepted_languages.split(',')]
                # forget about subtag and quality value
                accepted_languages = [x.split('-')[0].split(';')[0] for x in accepted_languages]
                known_languages = self.cfg.get('language', {}).get('languages') or []
                for lang in accepted_languages:
                    if lang in known_languages:
                        return lang
                lang = None
        if lang is None:
            default_locale = locale.getdefaultlocale()  # noqa pylint: disable=deprecated-method
            if default_locale and default_locale[0]:
                lang = default_locale[0].split('_')[0]
        return lang

    def get_backoffice_root(self):
        try:
            return self.root_directory.backoffice
        except AttributeError:
            return None

    ident_methods = None

    def register_ident_methods(self):
        try:
            import lasso
        except ImportError:
            lasso = None
        classes = []
        if lasso:
            from .ident import idp

            classes.append(idp.IdPAuthMethod)
        from .ident import franceconnect

        classes.append(franceconnect.FCAuthMethod)
        from .ident import password

        classes.append(password.PasswordAuthMethod)

        self.ident_methods = {}
        for klass in classes:
            self.ident_methods[klass.key] = klass
            klass.register()

    cronjobs = None

    @classmethod
    def register_cronjob(cls, cronjob):
        if not cls.cronjobs:
            cls.cronjobs = []
        # noqa pylint: disable=not-an-iterable
        if cronjob.name and any(x for x in cls.cronjobs if x.name == cronjob.name):
            # already registered
            return
        cls.cronjobs.append(cronjob)

    def clean_sessions(self, **kwargs):
        from wcs.sql import Session

        Session.clean()

    def clean_afterjobs(self, **kwargs):
        from .afterjobs import AfterJob

        AfterJob.clean()

    def clean_tokens(self, **kwargs):
        token_class = getattr(self, 'token_class', None)
        if token_class:
            token_class.clean()

    def _clean_files(self, limit, dirname, check_method=None):
        if not os.path.exists(dirname):
            return
        for filename in os.listdir(dirname):
            if os.stat(os.path.join(dirname, filename))[8] < limit:
                if check_method is not None and not check_method(filename):
                    continue
                try:
                    os.unlink(os.path.join(dirname, filename))
                except OSError:
                    pass

    def clean_saml_assertions(self, **kwargs):
        from wcs import sql

        sql.UsedSamlAssertionId.wipe(clause=[Less('expiration_time', datetime.datetime.now())])

        # Clean older files, should be removed in a future release (~ v12.90)
        now = time.time()
        one_month_ago = now - 30 * 86400
        self._clean_files(one_month_ago, os.path.join(self.app_dir, 'assertions'))

    def clean_tempfiles(self, **kwargs):
        now = time.time()
        one_month_ago = now - 30 * 86400
        self._clean_files(one_month_ago, os.path.join(self.app_dir, 'tempfiles'))

    def clean_models(self, **kwargs):
        from wcs.workflows import Workflow

        now = time.time()
        two_days_ago = now - 2 * 86400

        filenames_used = set()
        for workflow in Workflow.select(ignore_errors=True):
            for item in workflow.get_all_items():
                if item.key != 'export_to_model':
                    continue
                if not item.model_file:
                    continue
                filenames_used.add(item.model_file.filename)

        self._clean_files(
            two_days_ago, os.path.join(self.app_dir, 'models'), check_method=lambda x: x not in filenames_used
        )

    def clean_thumbnails(self, **kwargs):
        now = time.time()
        one_month_ago = now - 30 * 86400
        self._clean_files(one_month_ago, os.path.join(self.app_dir, 'thumbs'))

    def clean_loggederrors(self, **kwargs):
        from wcs.logged_errors import LoggedError

        clauses = [
            Less(
                'latest_occurence_timestamp',
                (datetime.datetime.now() - datetime.timedelta(days=30)).timetuple(),
            )
        ]
        for error in LoggedError.select(clause=clauses):
            LoggedError.remove_object(error.id)

    def clean_search_tokens(self, **kwargs):
        from wcs import sql

        if get_publisher().has_site_option('enable-purge-obsolete-search-tokens'):
            sql.purge_obsolete_search_tokens()

    @classmethod
    def register_cronjobs(cls):
        cls.register_cronjob(CronJob(cls.clean_sessions, minutes=[0], name='clean_sessions'))
        cls.register_cronjob(CronJob(cls.clean_afterjobs, minutes=[0], name='clean_afterjobs'))
        cls.register_cronjob(CronJob(cls.clean_tokens, minutes=[0], name='clean_tokens'))
        cls.register_cronjob(CronJob(cls.clean_tempfiles, minutes=[0], name='clean_tempfiles'))
        cls.register_cronjob(CronJob(cls.clean_saml_assertions, minutes=[0], name='clean_saml_assertions'))
        cls.register_cronjob(CronJob(cls.clean_models, minutes=[0], name='clean_models'))
        cls.register_cronjob(CronJob(cls.clean_thumbnails, minutes=[0], name='clean_thumbnails'))
        cls.register_cronjob(
            CronJob(cls.clean_loggederrors, hours=[3], minutes=[0], name='clean_loggederrors')
        )
        cls.register_cronjob(
            CronJob(cls.clean_search_tokens, weekdays=[0], hours=[1], minutes=[0], name='clean_search_tokens')
        )

    _initialized = False

    @classmethod
    def init_publisher_class(cls):
        if cls._initialized:
            return
        cls._initialized = True

    @classmethod
    def create_publisher(cls, **kwargs):
        publisher = cls(
            cls.root_directory_class(),
            session_cookie_name=cls.APP_NAME,
            session_cookie_path='/',
            logger=logger.ApplicationLogger(),
        )
        publisher.substitutions = Substitutions()
        publisher.app_dir = cls.APP_DIR
        publisher.data_dir = cls.DATA_DIR
        if not os.path.exists(publisher.app_dir):
            os.mkdir(publisher.app_dir)

        publisher.register_ident_methods()
        publisher.set_config()
        return publisher

    def detach(self):
        # reset structures that would otherwise be shared between threads
        self.pgconn = None
        self._app_logger = None
        self.init_publisher_substitutions(self.get_request())

    def set_sql_application_name(self, name):
        if name != self.sql_application_name:
            from wcs.sql import get_connection

            self.sql_application_name = name
            get_connection(new=True)

    def reset_formdata_state(self):
        # reset parameters that may have been altered by running a workflow on
        # a formdata. required be run before performing actions on another formdata.
        self.substitutions.reset()
        self.substitutions.feed(self)

    supported_languages = None
    cfg = None

    def write_cfg(self):
        s = pickle.dumps(self.cfg, protocol=2)
        filename = os.path.join(self.app_dir, 'config.pck')
        storage.atomic_write(filename, s)

    def reload_cfg(self):
        filename = os.path.join(self.app_dir, 'config.pck')
        try:
            with open(filename, 'rb') as fd:
                self.cfg = pickle.load(fd, encoding='utf-8')
        except Exception:
            self.cfg = {}

    def process(self, stdin, env):
        request = HTTPRequest(stdin, env)
        self.response = self.process_request(request)
        return self.response

    _app_logger = None

    def get_app_logger(self, force=False):
        if self._app_logger and not force:
            return self._app_logger

        self._app_logger = logging.getLogger(self.APP_NAME + self.app_dir)
        if not self._app_logger.handlers:
            hdlr = logging.StreamHandler()  # -> sys.stderr
            # do not include date/time as they will be automatically added by journald
            formatter = logger.Formatter('({levelname:.1s}) {tenant} {address} {path} - {message}', style='{')
            hdlr.setFormatter(formatter)
            self._app_logger.addHandler(hdlr)

        if self.cfg.get('debug', {}).get('debug_mode', False):
            self._app_logger.setLevel(logging.DEBUG)
        else:
            self._app_logger.setLevel(logging.INFO)

        return self._app_logger

    def get_default_position(self):
        default_position = self.cfg.get('misc', {}).get('default-position', None)
        if not default_position:
            default_position = self.get_site_option('default_position') or '50.84;4.36'

        if isinstance(default_position, str):
            default_position = {
                'lat': float(default_position.split(';')[0]),
                'lon': float(default_position.split(';')[1]),
            }

        return default_position

    def get_default_zoom_level(self):
        return self.cfg.get('misc', {}).get('default-zoom-level', '13')

    def get_map_attributes(self):
        attrs = {}
        default_position = self.get_default_position()
        attrs['data-def-lat'] = default_position['lat']
        attrs['data-def-lng'] = default_position['lon']
        if self.get_site_option('map-bounds-top-left'):
            attrs['data-max-bounds-lat1'], attrs['data-max-bounds-lng1'] = self.get_site_option(
                'map-bounds-top-left'
            ).split(';')
            attrs['data-max-bounds-lat2'], attrs['data-max-bounds-lng2'] = self.get_site_option(
                'map-bounds-bottom-right'
            ).split(';')
        attrs['data-map-attribution'] = html.escape(
            self.get_site_option('map-attribution')
            or _('Map data &copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>')
        )
        attrs['data-tile-urltemplate'] = self.get_site_option('map-tile-urltemplate')
        return attrs

    def get_nominatim_extra_params(self):
        params = {}
        for option_name, query_name in (('nominatim_key', 'key'), ('nominatim_contact_email', 'email')):
            value = self.get_site_option(option_name)
            if value:
                params[query_name] = value
        return params

    def get_reverse_geocoding_service_url(self):
        url = self.get_site_option('reverse_geocoding_service_url')
        if url:
            return url
        url = (
            get_cfg('misc', {}).get('geocoding-services-base-url')
            or self.get_site_option('nominatim_url')
            or settings.NOMINATIM_URL
        )
        url += '/reverse'
        reverse_zoom_level = self.get_site_option('nominatim_reverse_zoom_level') or 18
        params = {'zoom': reverse_zoom_level}
        params.update(self.get_nominatim_extra_params())
        return urllib.parse.urljoin(url, '?' + urllib.parse.urlencode(params))

    def get_geocoding_service_url(self):
        url = self.get_site_option('geocoding_service_url')
        if url:
            return url
        url = (
            get_cfg('misc', {}).get('geocoding-services-base-url')
            or self.get_site_option('nominatim_url')
            or settings.NOMINATIM_URL
        )
        url += '/search'
        params = self.get_nominatim_extra_params()
        if self.get_site_option('map-bounds-top-left'):
            top, left = self.get_site_option('map-bounds-top-left').split(';')
            bottom, right = self.get_site_option('map-bounds-bottom-right').split(';')
            params['viewbox'] = f'{left},{top},{right},{bottom}'
            params['bounded'] = 1
        return urllib.parse.urljoin(url, '?' + urllib.parse.urlencode(params))

    def get_working_day_calendar(self):
        return self.get_site_option('working_day_calendar') or settings.WORKING_DAY_CALENDAR

    def get_supported_authentication_contexts(self):
        contexts = collections.OrderedDict()
        labels = {
            'fedict': _('Belgian eID'),
            'franceconnect': _('FranceConnect'),
        }
        if self.get_site_option('auth-contexts'):
            for context in self.get_site_option('auth-contexts').split(','):
                context = context.strip()
                contexts[context] = labels[context]
        return contexts

    def get_authentication_saml_contexts(self, context):
        return {
            'fedict': [
                # custom context, provided by authentic fedict plugin:
                'urn:oasis:names:tc:SAML:2.0:ac:classes:SmartcardPKI',
                # native fedict contexts:
                'urn:be:fedict:iam:fas:citizen:eid',
                'urn:be:fedict:iam:fas:citizen:token',
                'urn:be:fedict:iam:fas:enterprise:eid',
                'urn:be:fedict:iam:fas:enterprise:token',
            ],
            'franceconnect': [
                'urn:oasis:names:tc:SAML:2.0:ac:classes:PasswordProtectedTransport',
            ],
        }[context]

    def get_lazy_variables_modes(self):
        # possible modes:
        # * django-condition: used to evaluate django conditions
        # * lazy: used to force lazy mode in tests and in context processor
        modes = self.get_site_option('lazy-variables-modes')
        if modes:
            return [x.strip() for x in modes.split(',')]
        return ['lazy', 'django-condition']

    def get_email_well_known_domains(self):
        emails_cfg = get_cfg('emails', {})
        well_known_domains = emails_cfg.get('well_known_domains')
        if not well_known_domains:
            well_known_domains = [
                'gmail.com',
                'msn.com',
                'hotmail.com',
                'hotmail.fr',
                'wanadoo.fr',
                'free.fr',
                'yahoo.fr',
                'numericable.fr',
                'laposte.net',
                'orange.fr',
                'yahoo.com',
            ]
        return well_known_domains

    def get_email_valid_known_domains(self):
        emails_cfg = get_cfg('emails', {})
        valid_known_domains = emails_cfg.get('valid_known_domains')
        if not valid_known_domains:
            valid_known_domains = ['yopmail.com', 'laposte.fr', 'sfr.fr']
        return valid_known_domains

    def get_substitution_variables(self):
        from wcs.variables import flexible_date

        d = {
            'site_name': get_cfg('misc', {}).get('sitename', None),
            'site_theme': get_cfg('branding', {}).get('theme', self.default_theme),
            'site_url': self.get_frontoffice_url(),
            'site_url_backoffice': self.get_backoffice_url(),
            'site_lang': (get_request() and hasattr(get_request(), 'language') and get_request().language)
            or 'en',
            'today': flexible_date(datetime.date.today()),
            'now': flexible_date(datetime.datetime.now()),
            'is_in_backoffice': (self.get_request() and self.get_request().is_in_backoffice()),
            'null': None,
            'true': True,
            'false': False,
        }
        if self.site_options is None:
            self.load_site_options()
        try:
            site_options_vars = dict(self.site_options.items('variables', raw=True))
        except configparser.NoSectionError:
            site_options_vars = {}
        for k, v in site_options_vars.items():
            if v.lower() in SiteOptionsBoolean.true_strings + SiteOptionsBoolean.false_strings:
                site_options_vars[k] = SiteOptionsBoolean(v)
            if k.endswith('__json'):
                site_options_vars[k] = json.loads(v)
        d.update(site_options_vars)
        d['manager_homepage_url'] = d.get('portal_agent_url')
        d['manager_homepage_title'] = d.get('portal_agent_title')
        return d

    def is_relatable_url(self, url):
        try:
            parsed_url = urllib.parse.urlparse(url)
        except ValueError:
            return False
        if parsed_url.scheme not in ('', 'http', 'https'):
            return False
        if not parsed_url.netloc:
            return True
        if parsed_url.netloc == urllib.parse.urlparse(self.get_frontoffice_url()).netloc:
            return True
        if parsed_url.netloc == urllib.parse.urlparse(self.get_backoffice_url()).netloc:
            return True
        if parsed_url.netloc in [x.strip() for x in self.get_site_option('relatable-hosts').split(',')]:
            return True
        try:
            if parsed_url.netloc in self.site_options.options('api-secrets'):
                return True
        except configparser.NoSectionError:
            pass
        return False

    def set_tenant(self, tenant, **kwargs):
        self.tenant = tenant
        self.app_dir = tenant.directory
        self.set_config(**kwargs)

    def set_tenant_by_hostname(self, hostname, **kwargs):
        for base_dir in (os.path.join(self.APP_DIR, 'tenants'), self.APP_DIR):
            tenant_dir = os.path.join(base_dir, hostname)
            if os.path.exists(tenant_dir):
                self.set_tenant(Tenant(tenant_dir), **kwargs)
                allowed_hostname = self.get_site_option('allowed_hostname')
                if allowed_hostname and hostname != allowed_hostname:
                    raise UnknownTenantError(hostname)
                break
        else:
            raise UnknownTenantError(hostname)

    @classmethod
    def get_tenants(cls):
        seen = set()
        for base_dir in (os.path.join(cls.APP_DIR, 'tenants'), cls.APP_DIR):
            if not os.path.exists(base_dir):
                continue
            for tenant in sorted(os.listdir(base_dir)):
                if tenant[0] in ('.', '_'):
                    continue
                if tenant in ('collectstatic', 'cron-logs', 'scripts', 'skeletons', 'spooler', 'tenants'):
                    continue
                if tenant.endswith('.invalid'):
                    continue
                tenant_dir = os.path.join(base_dir, tenant)
                if not os.path.isdir(tenant_dir):
                    continue
                if not os.access(tenant_dir, os.W_OK):
                    continue
                if tenant in seen:
                    # avoid going twice over same tenants, in case of a tenants/ symlink to
                    # /var/lib/wcs/.
                    continue
                # check it's not a tenant erroneously renamed
                config = configparser.RawConfigParser()
                site_options_filepath = os.path.join(tenant_dir, 'site-options.cfg')
                if os.path.exists(site_options_filepath):
                    config.read(site_options_filepath)
                try:
                    allowed_hostname = config.get('options', 'allowed_hostname')
                    if tenant != allowed_hostname:
                        continue
                except (configparser.NoOptionError, configparser.NoSectionError):
                    pass  # legacy
                seen.add(tenant)
                yield Tenant(tenant_dir)

    def add_after_job(self, job, force_async=False, **kwargs):
        from .afterjobs import AfterJob

        if not self.after_jobs:
            self.after_jobs = []
        assert isinstance(job, AfterJob), 'job must be instance of AfterJob'
        if get_response() or force_async:
            self.after_jobs.append(job)
        else:
            job.run(publisher=self, spool=False)
        return job

    def process_after_jobs(self, spool=True):
        seen = set()
        while [x for x in self.after_jobs if not id(x) in seen]:
            for job in (self.after_jobs or [])[:]:
                seen.add(id(job))
                with self.substitutions.freeze():
                    job.run(publisher=self, spool=spool)


def get_cfg(key, default=None):
    r = get_publisher().cfg.get(key, default)
    if not r:
        return {}
    return r


def get_logger():
    return get_publisher().get_app_logger()


def set_publisher_class(klass):
    builtins.__dict__['__publisher_class'] = klass


def get_publisher_class():
    return builtins.__dict__.get('__publisher_class')


Substitutions.register('site_name', category=_('General'), comment=_('Site Name'))
Substitutions.register('site_url', category=_('General'), comment=_('Site URL'))
Substitutions.register('site_url_backoffice', category=_('General'), comment=_('Site URL (backoffice)'))
Substitutions.register('today', category=_('General'), comment=_('Current Date'))
Substitutions.register('now', category=_('General'), comment=_('Current Date & Time'))
