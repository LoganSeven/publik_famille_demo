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

import datetime
import fnmatch
import io
import itertools
import json
import os
import pickle
import re
import shutil
import sys
import traceback
import zipfile
import zoneinfo
from contextlib import ExitStack, contextmanager

from django.conf import settings
from django.utils import timezone
from django.utils.timezone import localtime

from . import custom_views, sessions
from .admin import RootDirectory as AdminRootDirectory
from .backoffice import RootDirectory as BackofficeRootDirectory
from .Defaults import *  # noqa pylint: disable=wildcard-import
from .deprecations import DEPRECATIONS_METADATA
from .qommon import _, errors
from .qommon.cron import CronJob
from .qommon.publisher import QommonPublisher, get_request, set_publisher_class
from .qommon.tokens import Token
from .roles import Role
from .root import RootDirectory
from .users import User

try:
    from .wcs_cfg import *  # noqa pylint: disable=wildcard-import
except ImportError:
    pass


class UnpicklerClass(pickle.Unpickler):
    def find_class(self, module, name):
        if module == 'qommon.form':
            module = 'wcs.qommon.form'
        elif module in ('formdata', 'formdef', 'roles', 'users', 'workflows'):
            module = 'wcs.%s' % module
        module_moves = {
            # workflow classes moved to their own module
            ('wcs.workflows', 'ChoiceWorkflowStatusItem'): 'wcs.wf.choice',
            ('wcs.workflows', 'CommentableWorkflowStatusItem'): 'wcs.wf.comment',
            ('wcs.workflows', 'DisplayMessageWorkflowStatusItem'): 'wcs.wf.display_message',
            ('wcs.workflows', 'EditableWorkflowStatusItem'): 'wcs.wf.editable',
            ('wcs.workflows', 'ExportToModel'): 'wcs.wf.export_to_model',
            ('wcs.workflows', 'JumpOnSubmitWorkflowStatusItem'): 'wcs.wf.jump_on_submit',
            ('wcs.workflows', 'SendmailWorkflowStatusItem'): 'wcs.wf.sendmail',
            ('wcs.workflows', 'SendSMSWorkflowStatusItem'): 'wcs.wf.sms',
            ('wcs.workflows', 'WorkflowCommentPart'): 'wcs.wf.comment',
            # criteria classes moved to be sql only (2023-05-15)
            ('wcs.qommon.storage', 'GreaterOrEqual'): 'wcs.sql',
            ('wcs.qommon.storage', 'NotEqual'): 'wcs.sql',
            ('wcs.qommon.storage', 'StrictNotEqual'): 'wcs.sql',
            ('wcs.qommon.storage', 'LessOrEqual'): 'wcs.sql',
            ('wcs.qommon.storage', 'Between'): 'wcs.sql',
            ('wcs.qommon.storage', 'NotContains'): 'wcs.sql',
            ('wcs.qommon.storage', 'ILike'): 'wcs.sql',
            ('wcs.qommon.storage', 'FtsMatch'): 'wcs.sql',
            ('wcs.qommon.storage', 'NotNull'): 'wcs.sql',
            ('wcs.qommon.storage', 'Null'): 'wcs.sql',
            ('wcs.qommon.storage', 'ElementEqual'): 'wcs.sql',
            ('wcs.qommon.storage', 'ElementILike'): 'wcs.sql',
            ('wcs.qommon.storage', 'ElementIntersects'): 'wcs.sql',
            ('wcs.qommon.storage', 'Nothing'): 'wcs.sql',
            ('wcs.qommon.storage', 'Distance'): 'wcs.sql',
            # filter field classes moved to their own file (2024-04-12)
            ('wcs.backoffice.management', 'RelatedField'): 'wcs.backoffice.filter_fields',
            ('wcs.backoffice.management', 'UserRelatedField'): 'wcs.backoffice.filter_fields',
            ('wcs.backoffice.management', 'UserLabelRelatedField'): 'wcs.backoffice.filter_fields',
            # job classes moved to wcs.formdef_base, then wcs.formdef_jobs
            ('wcs.formdef', 'UpdateDigestAfterJob'): 'wcs.formdef_jobs',
            ('wcs.formdef', 'UpdateStatisticsDataAfterJob'): 'wcs.formdef_jobs',
            ('wcs.formdef_base', 'UpdateDigestAfterJob'): 'wcs.formdef_jobs',
            ('wcs.formdef_base', 'UpdateStatisticsDataAfterJob'): 'wcs.formdef_jobs',
            # job class moved from wcs.carddata to wcs.formdef_jobs
            ('wcs.carddata', 'UpdateRelationsAfterJob'): 'wcs.formdef_jobs',
            # job class moved from wcs.data_sources to wcs.data_sources_agendas
            ('wcs.data_sources', 'RefreshAgendas'): 'wcs.data_sources_agendas',
            # removed actions
            ('wcs.wf.redirect_to_status', 'RedirectToStatusWorkflowStatusItem'): 'NoLongerAvailableAction',
            ('wcs.workflows', 'RedirectToStatusWorkflowStatusItem'): 'NoLongerAvailableAction',
            # removed actions from auquotidien
            (
                'modules.abelium_domino_workflow',
                'AbeliumDominoRegisterFamilyWorkflowStatusItem',
            ): 'NoLongerAvailableAction',
            ('modules.payments', 'PaymentWorkflowStatusItem'): 'NoLongerAvailableAction',
            ('modules.payments', 'PaymentCancelWorkflowStatusItem'): 'NoLongerAvailableAction',
            ('modules.payments', 'PaymentValidationWorkflowStatusItem'): 'NoLongerAvailableAction',
            # removed workflow part class from auquotidien
            ('modules.payments', 'InvoiceEvolutionPart'): 'NoLongerAvailablePart',
        }
        module = module_moves.get((module, name), module)
        if module is object:
            return object
        if module in ('NoLongerAvailableAction', 'NoLongerAvailablePart'):
            module, name = 'wcs.workflows', module
        __import__(module)
        mod = sys.modules[module]
        if (
            module == 'wcs.formdef'
            and name not in ('FormDef', 'FileFormDef', 'UpdateDigestAfterJob', 'UpdateStatisticsDataAfterJob')
            and not name.startswith('_wcs_')
        ):
            name = '_wcs_%s' % name
        elif (
            module == 'wcs.carddef'
            and name not in ('CardDef', 'FileCardDef')
            and not name.startswith('_wcs_')
        ):
            name = '_wcs_%s' % name
        klass = getattr(mod, name)
        return klass


class WcsPublisher(QommonPublisher):
    APP_NAME = 'wcs'
    APP_DIR = APP_DIR
    DATA_DIR = DATA_DIR
    ERROR_LOG = ERROR_LOG
    missing_appdir_redirect = REDIRECT_ON_UNKNOWN_VHOST

    supported_languages = ['fr', 'es', 'de']

    root_directory_class = RootDirectory
    backoffice_directory_class = BackofficeRootDirectory
    admin_directory_class = AdminRootDirectory

    sql_application_name = 'wcs'

    session_manager_class = None
    user_class = User
    unpickler_class = UnpicklerClass

    complex_data_cache = None
    logged_http_requests = None

    @classmethod
    def configure(cls, config):
        if config.has_option('main', 'app_dir'):
            cls.APP_DIR = config.get('main', 'app_dir')
        if config.has_option('main', 'data_dir'):
            cls.DATA_DIR = config.get('main', 'data_dir')
        if config.has_option('main', 'error_log'):
            cls.ERROR_LOG = config.get('main', 'error_log')
        if config.has_option('main', 'missing_appdir_redirect'):
            cls.missing_appdir_redirect = config.get('main', 'missing_appdir_redirect')

    def get_site_option_defaults(self):
        defaults = {
            'options': {
                'allow-tracking-code-in-url': 'true',
                'disabled-fields': 'ranked-items, table, table-select, tablerows',
                'disable-rtf-support': 'true',
                'enable-card-identifier-template': 'true',
                'enable-carddata-applification': 'true',
                'enable-compact-dataview': 'true',
                'enable-intermediate-anonymisation': 'true',
                'use-legacy-query-string-in-listings': settings.USE_LEGACY_QUERY_STRING_IN_LISTINGS,
                'use-strict-check-for-verification-fields': settings.USE_STRICT_CHECK_FOR_VERIFICATION_FIELDS,
                'relatable-hosts': '',
                'sync-map-and-address-fields': 'true',
                'unused-files-behaviour': 'remove',
                'rich-text-formdef-description': 'mini-auto-ckeditor',
                'rich-text-wf-displaymsg': 'auto-ckeditor',
                'timezone': 'Europe/Paris',
                'default-old-but-non-anonymised-warning-delay': '365',
                'honeypots': '',
                'disabled-validation-types': settings.DISABLED_VALIDATION_TYPES,
                'force-lazy-mode': 'true',
                'map-tile-urltemplate': settings.MAP_TILE_URLTEMPLATE,
                # no entry for nominatim_url as we want to keep the geocoding
                # settings page empty if settings.NOMINATIM_URL is used.
                # (it may contain a secret token)
                'nominatim_key': settings.NOMINATIM_KEY,
                'nominatim_contact_email': settings.NOMINATIM_CONTACT_EMAIL,
            },
        }
        today = datetime.date.today()
        for deprecated_info in DEPRECATIONS_METADATA.values():
            removal_date = deprecated_info.get('removal_date')
            killswitches = deprecated_info.get('killswitches')
            if removal_date and killswitches and today > removal_date:
                defaults['options'].update({x: 'true' for x in killswitches})
        return defaults

    @classmethod
    def register_cronjobs(cls):
        super().register_cronjobs()
        # every hour: check for global action timeouts
        cls.register_cronjob(
            CronJob(cls.apply_global_action_timeouts, name='evaluate_global_action_timeouts', minutes=[0])
        )
        # every hour: check for stalled formdata
        cls.register_cronjob(CronJob(cls.check_stalled_formdata, name='check_stalled_formdata', minutes=[0]))
        # once a day: update deprecations report
        cls.register_cronjob(
            CronJob(cls.update_deprecations_report, name='update_deprecations_report', hours=[2], minutes=[0])
        )
        # once a week: sync users
        cls.register_cronjob(
            CronJob(User.sync_users, name='sync_users', weekdays=[0], hours=[2], minutes=[0])
        )
        # once a week: send active users to keepalive service
        cls.register_cronjob(
            CronJob(User.keepalive_users, name='keepalive_users', weekdays=[0], hours=[2], minutes=[0])
        )
        # once a week: archive workflow traces
        cls.register_cronjob(
            CronJob(
                cls.archive_workflow_traces,
                name='archive_workflow_traces',
                weekdays=[0],
                hours=[2],
                minutes=[0],
            )
        )
        # once a day delete unreferenced users
        cls.register_cronjob(
            CronJob(cls.clean_deleted_users, name='clean_deleted_users', hours=[3], minutes=[0])
        )

        # more daily cleanup tasks:
        # * clean old audit entries
        # * clean old deleted logged errors
        # * clean old test results
        from .audit import Audit
        from .logged_errors import LoggedError
        from .snapshots import Snapshot
        from .testdef import TestResults

        for kls in (Audit, LoggedError, Snapshot, TestResults):
            cls.register_cronjob(
                CronJob(kls.clean, name=f'clean_{kls.__name__.lower()}', hours=[3], minutes=[0])
            )

        # other jobs
        from wcs import data_sources_agendas, formdef_jobs

        data_sources_agendas.register_cronjob()
        formdef_jobs.register_cronjobs()

    def update_deprecations_report(self, **kwargs):
        from .backoffice.deprecations import DeprecationsScan

        job = DeprecationsScan()
        job.id = job.DO_NOT_STORE
        job.execute()

    def archive_workflow_traces(self, **kwargs):
        from .workflow_traces import WorkflowTrace

        WorkflowTrace.archive()

    def has_postgresql_config(self):
        return bool(self.cfg.get('postgresql', {}))

    def has_user_fullname_config(self):
        users_cfg = self.cfg.get('users') or {}
        return bool(users_cfg.get('field_name') or users_cfg.get('fullname_template'))

    def set_config(self, request=None, skip_sql=False):
        QommonPublisher.set_config(self, request=request)
        self.logged_http_requests = None
        if request:
            request.response.charset = self.site_charset

        # make sure permissions are set using strings
        if self.cfg.get('admin-permissions'):
            for key in self.cfg['admin-permissions'].keys():
                if not self.cfg['admin-permissions'][key]:
                    continue
                self.cfg['admin-permissions'][key] = [str(x) for x in self.cfg['admin-permissions'][key]]

        import wcs.workflows

        wcs.workflows.load_extra()

        if self.has_postgresql_config() and not skip_sql:
            from . import sql

            self.user_class = sql.SqlUser
            self.test_user_class = sql.TestUser
            self.role_class = sql.Role
            self.token_class = sql.Token
            self.session_class = sql.Session
            self.custom_view_class = sql.CustomView
            self.snapshot_class = sql.Snapshot
            sql.get_connection(new=True)
        else:
            self.user_class = User
            self.test_user_class = User
            self.role_class = Role
            self.token_class = Token
            self.session_class = sessions.BasicSession
            self.custom_view_class = custom_views.CustomView
            self.snapshot_class = None

        self.session_manager_class = sessions.StorageSessionManager
        self.set_session_manager(self.session_manager_class(session_class=self.session_class))

    def start_request(self):
        self.setup_timezone()
        super().start_request()

    def setup_timezone(self):
        try:
            timezone.activate(zoneinfo.ZoneInfo(self.get_site_option('timezone')))
        except zoneinfo.ZoneInfoNotFoundError:
            timezone.deactivate()  # use value from django settings

    def get_enabled_languages(self):
        return self.cfg.get('language', {}).get('languages') or []

    def get_phone_local_region_code(self):
        # default-country-code is a legacy setting (despite its name it contained a region code)
        return (
            self.get_site_option('local-region-code') or self.get_site_option('default-country-code') or 'FR'
        )

    def import_zip(self, fd, overwrite_settings=True):
        from wcs.categories import (
            BlockCategory,
            CardDefCategory,
            Category,
            CategoryImportError,
            CommentTemplateCategory,
            DataSourceCategory,
            MailTemplateCategory,
            WorkflowCategory,
        )
        from wcs.comment_templates import CommentTemplate
        from wcs.data_sources import NamedDataSource
        from wcs.mail_templates import MailTemplate
        from wcs.wscalls import NamedWsCall

        xml_exports_directories = {
            'datasources': NamedDataSource,
            'wscalls': NamedWsCall,
            'mail-templates': MailTemplate,
            'comment-templates': CommentTemplate,
            'categories': Category,
            'carddef_categories': CardDefCategory,
            'workflow_categories': WorkflowCategory,
            'block_categories': BlockCategory,
            'mail_template_categories': MailTemplateCategory,
            'comment_template_categories': CommentTemplateCategory,
            'data_source_categories': DataSourceCategory,
        }

        results = {
            'formdefs': 0,
            'carddefs': 0,
            'workflows': 0,
            'categories': 0,
            'carddef_categories': 0,
            'workflow_categories': 0,
            'block_categories': 0,
            'mail_template_categories': 0,
            'comment_template_categories': 0,
            'data_source_categories': 0,
            'roles': 0,
            'settings': 0,
            'datasources': 0,
            'wscalls': 0,
            'mail-templates': 0,
            'comment-templates': 0,
            'blockdefs': 0,
            'apiaccess': 0,
        }

        now = localtime()
        if self.has_site_option('allow-config-pck-in-import'):
            config_filenames = ['config.pck', 'config.json']
        else:
            config_filenames = ['config.json']

        for filename in config_filenames:
            filepath = os.path.join(self.app_dir, filename)
            if os.path.exists(filepath):
                shutil.copyfile(filepath, filepath + '.backup-%s' % now.strftime('%Y%m%d'))

        with zipfile.ZipFile(fd) as z:
            imported_config = None

            # check there's no duplicated id in categories
            seen_categories = set()

            for f in z.namelist():
                if os.path.dirname(f).endswith('categories') and os.path.basename(f):
                    with z.open(f) as fd:
                        obj = xml_exports_directories[os.path.dirname(f)].import_from_xml(
                            fd, include_id=True, check_deprecated=True
                        )
                        if str(obj.id) in seen_categories:
                            raise CategoryImportError(
                                _('Exported site needs to be migrated to SQL categories.')
                            )
                        seen_categories.add(str(obj.id))

            for f in z.namelist():
                if f in ('.indexes', '.max_id'):
                    continue
                if os.path.dirname(f) in (
                    'formdefs_xml',
                    'carddefs_xml',
                    'workflows_xml',
                    'blockdefs_xml',
                    'roles_xml',
                    'datasources',
                    'wscalls',
                    'apiaccess',
                    'mail-templates',
                    'comment-templates',
                    'categories',
                    'carddef_categories',
                    'workflow_categories',
                    'block_categories',
                    'mail_template_categories',
                    'comment_template_categories',
                    'data_source_categories',
                ):
                    continue
                path = os.path.join(self.app_dir, f)
                if not os.path.exists(os.path.dirname(path)):
                    os.mkdir(os.path.dirname(path))
                if not os.path.basename(f):
                    # skip directories
                    continue
                data = z.read(f)
                if f in config_filenames:
                    results['settings'] = 1
                    if f == 'config.pck':
                        imported_config = pickle.loads(data)
                    else:
                        imported_config = json.loads(data)
                    if overwrite_settings:
                        if 'sp' in self.cfg:
                            current_sp = self.cfg['sp']
                        else:
                            current_sp = None
                        self.cfg = imported_config
                        if current_sp:
                            self.cfg['sp'] = current_sp
                        elif 'sp' in self.cfg:
                            del self.cfg['sp']
                    else:
                        # only update a subset of settings, critical system parts such as
                        # authentication and database settings are not overwritten.
                        for section, section_parts in (
                            ('emails', ('email-*',)),
                            ('filetypes', '*'),
                            ('language', '*'),
                            ('misc', ('default-position', 'default-zoom-level')),
                            ('sms', '*'),
                            ('submission-channels', '*'),
                            ('backoffice-submission', '*'),
                            ('texts', '*'),
                            ('users', ('*_template',)),
                        ):
                            if section not in imported_config:
                                continue
                            if section not in self.cfg:
                                self.cfg[section] = {}
                            for key in imported_config[section]:
                                for pattern in section_parts:
                                    if fnmatch.fnmatch(str(key), pattern):
                                        self.cfg[section][key] = imported_config[section][key]
                    self.write_cfg()
                    continue
                with open(path, 'wb') as fd:
                    fd.write(data)
                if os.path.split(f)[0] in results:
                    results[os.path.split(f)[0]] += 1

            # import categories, datasources, wscalls and comment/mail templates

            for f in z.namelist():
                if os.path.dirname(f) in xml_exports_directories and os.path.basename(f):
                    with z.open(f) as fd:
                        obj = xml_exports_directories[os.path.dirname(f)].import_from_xml(
                            fd, include_id=True, check_deprecated=True
                        )
                    obj.store()
                    results[os.path.dirname(f)] += 1

            # second pass, blocks of fields
            from wcs.blocks import BlockDef

            for f in z.namelist():
                if os.path.dirname(f) == 'blockdefs_xml' and os.path.basename(f):
                    with z.open(f) as fd:
                        blockdef = BlockDef.import_from_xml(fd, include_id=True, check_deprecated=True)
                    blockdef.store()
                    results['blockdefs'] += 1

            # third pass, workflows
            from wcs.workflows import Workflow

            for f in z.namelist():
                if os.path.dirname(f) == 'workflows_xml' and os.path.basename(f):
                    with z.open(f) as fd:
                        workflow = Workflow.import_from_xml(
                            fd, include_id=True, check_datasources=False, check_deprecated=True
                        )
                    workflow.store()
                    results['workflows'] += 1

            # fourth pass, forms and cards
            from wcs.carddef import CardDef
            from wcs.formdef import FormDef

            formdefs = []
            carddefs = []
            for f in z.namelist():
                if os.path.dirname(f) == 'formdefs_xml' and os.path.basename(f):
                    with z.open(f) as fd:
                        formdef = FormDef.import_from_xml(
                            fd, include_id=True, check_datasources=False, check_deprecated=True
                        )
                    formdef.store()
                    formdefs.append(formdef)
                    results['formdefs'] += 1
                if os.path.dirname(f) == 'carddefs_xml' and os.path.basename(f):
                    with z.open(f) as fd:
                        carddef = CardDef.import_from_xml(
                            fd, include_id=True, check_datasources=False, check_deprecated=True
                        )
                    carddef.store()
                    carddefs.append(carddef)
                    results['carddefs'] += 1

            if results['formdefs']:
                FormDef.reset_restart_sequence()
            if results['carddefs']:
                CardDef.reset_restart_sequence()

            # sixth pass, roles and apiaccess
            from wcs.sql import ApiAccess

            roles = []
            for f in z.namelist():
                if os.path.dirname(f) == 'roles_xml' and os.path.basename(f):
                    with z.open(f) as fd:
                        role = self.role_class.import_from_xml(fd, include_id=True)
                    role.store()
                    roles.append(role)
                    results['roles'] += 1
                elif os.path.dirname(f) == 'apiaccess' and os.path.basename(f):
                    with z.open(f) as fd:
                        apiaccess = ApiAccess.import_from_xml(fd)
                    apiaccess.store()
                    results['apiaccess'] += 1

            # adjust admin permissions
            if overwrite_settings and self.cfg.get('admin-permissions-export'):
                permissions_export = self.cfg.pop('admin-permissions-export')
                self.cfg['admin-permissions'] = {}
                for key, role_infos in permissions_export.items():
                    roles = [
                        self.role_class.resolve(uuid=x.get('uuid'), slug=x.get('slug'), name=x.get('name'))
                        for x in role_infos
                    ]
                    self.cfg['admin-permissions'][key] = [x.id for x in roles if x]
                self.write_cfg()

            # rebuild indexes for imported objects
            for k, v in results.items():
                if k == 'settings':
                    continue
                if v == 0:
                    continue
                klass = None
                if k == 'formdefs':
                    from .formdef import FormDef

                    klass = FormDef
                elif k == 'carddefs':
                    from .carddef import CardDef

                    klass = CardDef
                elif k == 'blockdefs':
                    klass = BlockDef
                elif k == 'categories':
                    from .categories import Category

                    klass = Category
                elif k == 'roles':
                    klass = self.role_class
                elif k == 'workflows':
                    klass = Workflow
                if klass and hasattr(klass, 'rebuild_indexes'):
                    klass.rebuild_indexes()

                if k == 'formdefs':
                    # in case of formdefs, we store them anew in case SQL changes
                    # are required.
                    for formdef in formdefs or FormDef.select():
                        formdef.store()
                elif k == 'carddefs':
                    # ditto for cards
                    for carddef in carddefs or CardDef.select():
                        carddef.store()

        return results

    def initialize_sql(self):
        from . import sql

        sql.get_connection(new=True)
        with sql.atomic():
            sql.do_session_table()
            sql.do_user_table()
            sql.do_role_table()
            sql.do_tracking_code_table()
            sql.do_custom_views_table()
            sql.do_transient_data_table()
            sql.do_snapshots_table()
            sql.do_loggederrors_table()
            sql.do_tokens_table()
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
            sql.WorkflowTrace.do_table()
            sql.Audit.do_table()
            sql.TestDef.do_table()
            sql.TestResults.do_table()
            sql.TestResult.do_table()
            sql.Application.do_table()
            sql.ApplicationElement.do_table()
            sql.SearchableFormDef.do_table()
            sql.TranslatableMessage.do_table()
            sql.UsedSamlAssertionId.do_table()
            sql.ApiAccess.do_table()
            sql.do_meta_table()
            from .carddef import CardDef
            from .formdef import FormDef

            conn, cur = sql.get_connection_and_cursor()
            sql.drop_views(None, conn, cur)
            for _formdef in FormDef.select() + CardDef.select():
                sql.do_formdef_tables(_formdef)
            sql.migrate_global_views(conn, cur)
            sql.init_search_tokens()
            sql.init_functions()
            cur.close()

    def record_deprecated_usage(self, *args, **kwargs):
        return self.record_error(context=_('Deprecation'), deprecated_usage=True, *args, **kwargs)

    @contextmanager
    def disable_logged_errors(self):
        request = get_request()
        if request:
            current_value = getattr(request, 'disable_error_notifications', None)
            request.disable_error_notifications = True
        try:
            yield True
        finally:
            if request:
                request.disable_error_notifications = current_value

    def record_error(
        self,
        error_summary=None,
        context=None,
        exception=None,
        record=True,
        notify=False,
        deprecated_usage=False,
        interrupt_inspect=True,
        *args,
        **kwargs,
    ):
        if not record and not notify:
            return

        if get_request() and getattr(get_request(), 'inspect_mode', False):
            # do not record anything when trying random things in the inspector
            if interrupt_inspect:
                raise errors.InspectException(error_summary)
            return

        if get_request() and getattr(get_request(), 'disable_error_notifications', None) is True:
            # do not record anything if errors are disabled
            return

        if exception is False:
            plain_error_msg = ''
        elif exception is not None:
            exc_type, exc_value, tb = sys.exc_info()
            if not error_summary:
                error_summary = traceback.format_exception_only(exc_type, exc_value)
                error_summary = error_summary[0][0:-1]  # de-listify and strip newline
            plain_error_msg = str(
                self._generate_plaintext_error(get_request(), self, exc_type, exc_value, tb)
            )
        else:
            error_file = io.StringIO()
            error_file.write(self._format_traceback(sys._getframe().f_back))
            if get_request():
                error_file.write('\n')
                error_file.write(get_request().dump())
                error_file.write('\n')
            plain_error_msg = error_file.getvalue()

        if context:
            error_summary = _('%(context)s: %(summary)s') % {'context': context, 'summary': error_summary}
        if error_summary is None:
            return

        error_summary = str(error_summary).replace('\n', ' ')[:400].strip()

        logged_exception = None
        if record:
            from wcs.logged_errors import LoggedError

            kind = 'deprecated_usage' if deprecated_usage else None
            logged_exception = LoggedError.record_error(
                error_summary,
                plain_error_msg,
                publisher=self,
                exception=exception,
                kind=kind,
                *args,
                **kwargs,
            )
        if not notify or logged_exception and logged_exception.occurences_count > 1:
            # notify only first occurence
            return logged_exception
        try:
            self.logger.log_internal_error(
                error_summary, plain_error_msg, logged_exception.tech_id if logged_exception else None
            )
        except OSError:
            # Could happen if there is no mail server available and exceptions
            # were configured to be mailed. (formerly socket.error)
            # Could also could happen on file descriptor exhaustion.
            pass
        return logged_exception

    def get_object_class(self, object_type):
        from wcs.blocks import BlockDef
        from wcs.carddef import CardDef
        from wcs.categories import (
            BlockCategory,
            CardDefCategory,
            Category,
            CommentTemplateCategory,
            DataSourceCategory,
            MailTemplateCategory,
            WorkflowCategory,
        )
        from wcs.comment_templates import CommentTemplate
        from wcs.data_sources import NamedDataSource
        from wcs.formdef import FormDef
        from wcs.mail_templates import MailTemplate
        from wcs.sql import SqlUser
        from wcs.testdef import TestDef
        from wcs.workflows import Workflow
        from wcs.wscalls import NamedWsCall

        for klass in (
            BlockDef,
            CardDef,
            NamedDataSource,
            FormDef,
            Workflow,
            NamedWsCall,
            MailTemplate,
            CommentTemplate,
            Category,
            CardDefCategory,
            WorkflowCategory,
            BlockCategory,
            MailTemplateCategory,
            CommentTemplateCategory,
            DataSourceCategory,
            TestDef,
            SqlUser,
        ):
            if klass.xml_root_node == object_type:
                return klass
        raise KeyError('no class for object type: %s' % object_type)

    def apply_global_action_timeouts(self, **kwargs):
        from wcs.workflows import Workflow, WorkflowGlobalActionTimeoutTrigger

        job = kwargs.pop('job', None)
        for workflow in Workflow.select():
            with (
                job.log_long_job(
                    'workflow %s (%s)' % (workflow.name, workflow.id),
                    record_error_kwargs={
                        'error_summary': _('too much time spent on global actions of "%s"') % workflow.name,
                        'workflow': workflow,
                    },
                )
                if job
                else ExitStack()
            ):
                WorkflowGlobalActionTimeoutTrigger.apply(workflow)

    def check_stalled_formdata(self, **kwargs):
        from wcs.carddef import CardDef
        from wcs.formdef import FormDef

        for formdef in itertools.chain(FormDef.select(), CardDef.select()):
            formdef.data_class().clean_stalled_workflow_processing()

    def migrate_sql(self):
        from . import sql

        sql.migrate()

    def reindex_sql(self, *args, **kwargs):
        from . import sql

        sql.reindex()

    def cleanup(self):
        self._cached_user_fields_formdef = None
        self._update_related_seen = None
        self._error_context = None
        from . import sql

        sql.cleanup_connection()
        timezone.deactivate()

    @contextmanager
    def complex_data(self):
        old_complex_data_cache, self.complex_data_cache = self.complex_data_cache, {}
        try:
            yield True
        finally:
            self.complex_data_cache = old_complex_data_cache

    def cache_complex_data(self, value, rendered_value):
        # Keep a temporary cache of assocations between a complex data value
        # (value) and a string reprensentation (produced by django with
        # django.template.base.render_value_in_context.
        #
        # It ensures string values are unique by appending a private unicode
        # code point, that will be removed in wcs/qommon/template.py.

        if self.complex_data_cache is None:
            # it doesn't do anything unless initialized.
            return value

        str_value = rendered_value.strip() + chr(0xE000 + len(self.complex_data_cache))
        self.complex_data_cache[str_value] = value
        return str_value

    def has_cached_complex_data(self, value):
        return bool(value in (self.complex_data_cache or {}))

    def get_cached_complex_data(self, value, loop_context=False):
        if not isinstance(value, str):
            return value
        value = value.strip()
        if self.complex_data_cache is None:
            return value
        if value not in self.complex_data_cache:
            return re.sub(r'[\uE000-\uF8FF]', '', value)
        value_ = self.complex_data_cache.get(value)
        if loop_context and hasattr(value_, 'get_iterable_value'):
            return value_.get_iterable_value()
        if hasattr(value_, 'get_value'):
            # unlazy variable
            return value_.get_value()
        return value_

    @contextmanager
    def inspect_recurse_skip(self, prefixes):
        self.inspect_recurse_skip_prefixes = prefixes or []
        try:
            yield True
        finally:
            self.inspect_recurse_skip_prefixes = None

    # when parsing block widgets we usually want to skip empty rows, however
    # when evaluating live conditions we must keep all lines to get row indices
    # matching what's in the DOM.
    keep_all_block_rows_mode = False

    @contextmanager
    def keep_all_block_rows(self):
        self.keep_all_block_rows_mode = True
        try:
            yield True
        finally:
            self.keep_all_block_rows_mode = False

    # stacked contexts to include in logged errors
    _error_context = None

    @contextmanager
    def error_context(self, **kwargs):
        if not self._error_context:
            self._error_context = []
        self._error_context.append(kwargs)
        try:
            yield True
        finally:
            self._error_context.pop()

    def log_http_request(self, method, url):
        if not self.logged_http_requests:
            self.logged_http_requests = []
        source_url = None
        source_label = None
        try:
            context = [x for x in self._error_context or [] if 'source_url' in x][0]
            source_url = context.get('source_url')
            source_label = context.get('source_label')
        except IndexError:
            pass
        self.logged_http_requests.append(
            {
                'timestamp': localtime(),
                'method': method,
                'url': url,
                'source_label': source_label,
                'source_url': source_url,
            }
        )

    def get_error_context(self):
        return {'stack': self._error_context} if self._error_context else None

    def add_timing_mark(self, *args, **kwargs):
        request = get_request()
        if request:
            request.add_timing_mark(*args, **kwargs)

    def clean_deleted_users(self, **kwargs):
        for user_id in self.user_class.get_to_delete_ids():
            self.user_class.remove_object(user_id)


set_publisher_class(WcsPublisher)
