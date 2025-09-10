# w.c.s. - web application for online forms
# Copyright (C) 2005-2012  Entr'ouvert
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

import copy
import datetime
import decimal
import hashlib
import io
import itertools
import json
import os
import pickle
import re
import secrets
import shutil
import time
import uuid
from contextlib import ContextDecorator

import psycopg2
import psycopg2.errors
import psycopg2.extensions
import psycopg2.extras
from django.utils.encoding import force_bytes, force_str
from django.utils.module_loading import import_string
from django.utils.timezone import localtime, make_aware, now
from psycopg2.errors import UndefinedTable  # noqa pylint: disable=no-name-in-module
from psycopg2.sql import SQL, Identifier, Literal
from quixote import get_publisher

import wcs.api_access
import wcs.carddata
import wcs.custom_views
import wcs.formdata
import wcs.qommon.tokens
import wcs.roles
import wcs.snapshots
import wcs.sql_criterias
import wcs.users

from . import qommon
from .qommon import _, get_cfg
from .qommon.misc import JSONEncoder, classproperty, is_ascii_digit, strftime
from .qommon.storage import NothingToUpdate, _take, classonlymethod
from .qommon.storage import parse_clause as parse_storage_clause
from .qommon.substitution import invalidate_substitution_cache
from .qommon.upload_storage import PicklableUpload
from .sql_criterias import *  # noqa pylint: disable=wildcard-import,unused-wildcard-import

# enable psycogp2 unicode mode, this will fetch postgresql varchar/text columns
# as unicode objects
psycopg2.extensions.register_type(psycopg2.extensions.UNICODE)
psycopg2.extensions.register_type(psycopg2.extensions.UNICODEARRAY)

# automatically adapt dictionaries into json fields
psycopg2.extensions.register_adapter(dict, psycopg2.extras.Json)


SQL_TYPE_MAPPING = {
    'title': None,
    'subtitle': None,
    'comment': None,
    'page': None,
    'text': 'text',
    'bool': 'boolean',
    'numeric': 'numeric',
    'file': 'bytea',
    'date': 'date',
    'map': 'jsonb',
    'items': 'text[]',
    'table': 'text[][]',
    'table-select': 'text[][]',
    'tablerows': 'text[][]',
    'time-range': 'jsonb',
    # mapping of dicts
    'ranked-items': 'text[][]',
    'password': 'text[][]',
    # field block
    'block': 'jsonb',
    # computed data field
    'computed': 'jsonb',
}


def _table_exists(cur, table_name):
    cur.execute('SELECT 1 FROM pg_class WHERE relname = %s', (table_name,))
    rows = cur.fetchall()
    return len(rows) > 0


def _column_exists(cur, table_name, column_name):
    cur.execute(
        """SELECT 1 FROM pg_attribute pa JOIN pg_class pc ON pc.oid = pa.attrelid
                WHERE pc.relname = %s and pa.attname = %s""",
        (table_name, column_name),
    )
    rows = cur.fetchall()
    return len(rows) > 0


def _trigger_exists(cur, table_name, trigger_name):
    cur.execute(
        'SELECT 1 FROM pg_trigger WHERE tgrelid = %s::regclass AND tgname = %s', (table_name, trigger_name)
    )
    rows = cur.fetchall()
    return len(rows) > 0


class LoggingCursor(psycopg2.extensions.cursor):
    # keep track of (number of) queries, for tests and cron logging and usage summary.
    queries = None
    queries_count = 0
    queries_log_function = None

    def execute(self, query, vars=None):
        LoggingCursor.queries_count += 1
        if self.queries_log_function:
            self.queries_log_function(query)
        if self.queries is not None:
            self.queries.append(query)
        return super().execute(query, vars)


class WcsPgConnection(psycopg2.extensions.connection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cursor_factory = LoggingCursor
        self._wcs_in_transaction = False
        self._wcs_savepoints = []


class Atomic(ContextDecorator):
    """
    Inspired by django Atomic
    """

    def __init__(self):
        pass

    def start_transaction(self):
        # get the conn
        conn = get_connection()
        cursor = conn.cursor()

        # if already in txn, start a savepoint
        if conn._wcs_in_transaction:
            import _thread

            savepoint_name = '%s_%s' % (_thread.get_ident(), len(conn._wcs_savepoints))
            cursor.execute("SAVEPOINT \"%s\";" % savepoint_name)
            conn._wcs_savepoints.append(savepoint_name)
        else:
            conn._wcs_in_transaction = True
            conn.autocommit = False

    def rollback(self):
        conn = get_connection()
        cursor = conn.cursor()
        # rollback transaction, or rollback savepoint (and release the savepoint, it won't be used anymore)
        if len(conn._wcs_savepoints) == 0:
            conn.rollback()
            conn._wcs_in_transaction = False
            conn.autocommit = True
        else:
            last_savepoint = conn._wcs_savepoints.pop()
            cursor.execute("ROLLBACK TO SAVEPOINT \"%s\";" % last_savepoint)
            cursor.execute("RELEASE SAVEPOINT \"%s\";" % last_savepoint)

    def commit(self):
        conn = get_connection()
        cursor = conn.cursor()

        # commit transaction, or release savepoint
        if len(conn._wcs_savepoints) == 0:
            conn.commit()
            conn._wcs_in_transaction = False
            conn.autocommit = True
        else:
            last_savepoint = conn._wcs_savepoints.pop()
            cursor.execute("RELEASE SAVEPOINT \"%s\";" % last_savepoint)

    def __enter__(self):
        self.start_transaction()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if exc_type is None:
            self.commit()
        else:
            self.rollback()

    def partial_commit(self):
        self.commit()
        self.start_transaction()

    @classmethod
    def transaction_in_progress(cls):
        conn = get_connection()
        return conn._wcs_in_transaction


def atomic(f=None):
    return Atomic() if f is None else Atomic()(f)


class LazyEvolutionList(list):
    def __init__(self, dump):
        self.dump = dump

    def _load(self):
        try:
            dump = super().__getattribute__('dump')
        except AttributeError:
            pass
        else:
            super().__setitem__(slice(0), get_publisher().unpickler_class(io.BytesIO(dump)).load())
            del self.dump

    def __getattribute__(self, name):
        super().__getattribute__('_load')()
        return super().__getattribute__(name)

    def __bool__(self):
        self._load()
        return bool(len(self))

    def __iter__(self):
        self._load()
        return super().__iter__()

    def __len__(self):
        self._load()
        return super().__len__()

    def __reversed__(self):
        self._load()
        return super().__reversed__()

    def __str__(self):
        self._load()
        return super().__str__()

    def __getitem__(self, index):
        self._load()
        return super().__getitem__(index)

    def __setitem__(self, index, value):
        self._load()
        return super().__setitem__(index, value)

    def __delitem__(self, index):
        self._load()
        return super().__delitem__(index)

    def __iadd__(self, values):
        self._load()
        return super().__add__(values)

    def __repr__(self):
        self._load()
        return super().__repr__()

    def __contains__(self, value):
        self._load()
        return super().__contains__(value)

    def __reduce__(self):
        return (list, (), None, iter(self))


def pickle_loads(value):
    if hasattr(value, 'tobytes'):
        value = value.tobytes()
    from wcs.publisher import UnpicklerClass

    return UnpicklerClass(io.BytesIO(force_bytes(value))).load()


def get_name_as_sql_identifier(name):
    name = qommon.misc.simplify(name)
    for char in '<>|{}!?^*+/=\'':  # forbidden chars
        name = name.replace(char, '')
    name = name.replace('-', '_')
    return name


def get_connection(new=False):
    if new:
        cleanup_connection()

    publisher = get_publisher()
    if not getattr(publisher, 'pgconn', None):
        postgresql_cfg = {}
        for param in ('database', 'user', 'password', 'host', 'port'):
            value = get_cfg('postgresql', {}).get(param)
            if value:
                postgresql_cfg[param] = value
        if 'database' in postgresql_cfg:
            postgresql_cfg['dbname'] = postgresql_cfg.pop('database')
        postgresql_cfg['application_name'] = getattr(publisher, 'sql_application_name', None)
        try:
            pgconn = psycopg2.connect(connection_factory=WcsPgConnection, **postgresql_cfg)
            pgconn.autocommit = True
        except psycopg2.Error:
            if new:
                raise
            pgconn = None

        publisher.pgconn = pgconn

    return publisher.pgconn


def cleanup_connection():
    if hasattr(get_publisher(), 'pgconn') and get_publisher().pgconn is not None:
        get_publisher().pgconn.close()
        get_publisher().pgconn = None


def get_connection_and_cursor(new=False):
    conn = get_connection(new=new)
    try:
        cur = conn.cursor()
    except psycopg2.InterfaceError:
        # may be postgresql was restarted in between
        conn = get_connection(new=True)
        cur = conn.cursor()
    return (conn, cur)


def get_formdef_table_name(formdef):
    # PostgreSQL limits identifier length to 63 bytes
    #
    #   The system uses no more than NAMEDATALEN-1 bytes of an identifier;
    #   longer names can be written in commands, but they will be truncated.
    #   By default, NAMEDATALEN is 64 so the maximum identifier length is
    #   63 bytes. If this limit is problematic, it can be raised by changing
    #   the NAMEDATALEN constant in src/include/pg_config_manual.h.
    #
    # as we have to know our table names, we crop the names here, and to an
    # extent that allows suffixes (like _evolution) to be added.
    assert formdef.id is not None
    if hasattr(formdef, 'table_name') and formdef.table_name:
        return formdef.table_name
    formdef.table_name = '%s_%s_%s' % (
        formdef.data_sql_prefix,
        formdef.id,
        get_name_as_sql_identifier(formdef.url_name)[:30],
    )
    if not formdef.is_readonly():
        formdef.store(object_only=True)
    return formdef.table_name


def get_formdef_test_table_name(formdef):
    table_name = get_formdef_table_name(formdef)
    return '%s_%s' % ('test', table_name)


def get_formdef_trigger_function_name(formdef):
    assert formdef.id is not None
    return '%s_%s_trigger_fn' % (formdef.data_sql_prefix, formdef.id)


def get_formdef_trigger_name(formdef):
    assert formdef.id is not None
    return '%s_%s_trigger' % (formdef.data_sql_prefix, formdef.id)


def get_formdef_view_name(formdef):
    prefix = 'wcs_view'
    if formdef.data_sql_prefix != 'formdata':
        prefix = 'wcs_%s_view' % formdef.data_sql_prefix
    return '%s_%s_%s' % (prefix, formdef.id, get_name_as_sql_identifier(formdef.url_name)[:40])


def do_formdef_tables(formdef, conn=None, cur=None, rebuild_views=False, rebuild_global_views=True):
    if formdef.id is None:
        return []

    if getattr(formdef, 'fields', None) is Ellipsis:
        # don't touch tables for lightweight objects
        return []

    if getattr(formdef, 'snapshot_object', None):
        # don't touch tables for snapshot objects
        return []

    own_conn = False
    if not conn:
        own_conn = True
        conn, cur = get_connection_and_cursor()

    table_name = get_formdef_table_name(formdef)
    test_table_name = get_formdef_test_table_name(formdef)

    actions = create_formdef_tables(formdef, conn, cur, rebuild_views, rebuild_global_views, table_name)
    create_formdef_tables(formdef, conn, cur, rebuild_views, rebuild_global_views, test_table_name)

    if own_conn:
        cur.close()

    return actions


def create_formdef_tables(formdef, conn, cur, rebuild_views, rebuild_global_views, table_name):
    with atomic():
        cur.execute(
            '''SELECT COUNT(*) FROM information_schema.tables
                        WHERE table_schema = 'public'
                          AND table_name = %s''',
            (table_name,),
        )
        if cur.fetchone()[0] == 0:
            cur.execute(
                '''CREATE TABLE %s (id serial PRIMARY KEY,
                                        user_id varchar,
                                        receipt_time timestamptz,
                                        anonymised timestamptz,
                                        status varchar,
                                        page_no varchar,
                                        workflow_data bytea,
                                        id_display varchar
                                        )'''
                % table_name
            )
            cur.execute(
                '''CREATE TABLE %s_evolutions (id serial PRIMARY KEY,
                                        who varchar,
                                        status varchar,
                                        time timestamptz,
                                        last_jump_datetime timestamptz,
                                        comment text,
                                        parts bytea,
                                        formdata_id integer REFERENCES %s (id) ON DELETE CASCADE)'''
                % (table_name, table_name)
            )

        # make sure the table will not be changed while we work on it
        cur.execute('LOCK TABLE %s;' % table_name)

        cur.execute(
            '''SELECT column_name, data_type FROM information_schema.columns
                        WHERE table_schema = 'public'
                          AND table_name = %s''',
            (table_name,),
        )
        existing_field_types = {x[0]: x[1] for x in cur.fetchall()}
        existing_fields = set(existing_field_types.keys())

        needed_fields = {x[0] for x in formdef.data_class()._table_static_fields}
        needed_fields.add('fts')

        # migrations
        if 'fts' not in existing_fields:
            # full text search, column and index
            cur.execute('''ALTER TABLE %s ADD COLUMN fts tsvector''' % table_name)

        if 'criticality_level' not in existing_fields:
            # criticality leve, with default value
            existing_fields.add('criticality_level')
            cur.execute(
                '''ALTER TABLE %s ADD COLUMN criticality_level integer NOT NULL DEFAULT(0)''' % table_name
            )

        if 'test_result_id' not in existing_fields:
            existing_fields.add('test_result_id')
            cur.execute(
                '''ALTER TABLE %s ADD COLUMN test_result_id integer REFERENCES test_result(id) ON DELETE CASCADE'''
                % table_name
            )

        # generic migration for new columns
        for field_name, field_type in formdef.data_class()._table_static_fields:
            if field_name not in existing_fields:
                cur.execute('''ALTER TABLE %s ADD COLUMN %s %s''' % (table_name, field_name, field_type))

        # store datetimes with timezone
        if existing_field_types.get('receipt_time') not in (None, 'timestamp with time zone'):
            cur.execute(f'ALTER TABLE {table_name} ALTER COLUMN receipt_time SET DATA TYPE timestamptz')
        if existing_field_types.get('last_update_time') not in (None, 'timestamp with time zone'):
            cur.execute(f'ALTER TABLE {table_name} ALTER COLUMN last_update_time SET DATA TYPE timestamptz')

        # add new fields
        field_integrity_errors = {}
        for field in formdef.get_all_fields():
            assert field.id is not None
            sql_type = SQL_TYPE_MAPPING.get(field.key, 'varchar')
            if sql_type is None:
                continue
            needed_fields.add(get_field_id(field))
            if get_field_id(field) not in existing_fields:
                cur.execute(
                    '''ALTER TABLE %s ADD COLUMN %s %s''' % (table_name, get_field_id(field), sql_type)
                )
            else:
                existing_type = existing_field_types.get(get_field_id(field))
                # map to names returned in data_type column
                expected_type = {
                    'varchar': 'character varying',
                    'text[]': 'ARRAY',
                    'text[][]': 'ARRAY',
                }.get(sql_type) or sql_type
                if existing_type != expected_type:
                    field_integrity_errors[str(field.id)] = {'got': existing_type, 'expected': expected_type}
            if field.store_display_value:
                needed_fields.add('%s_display' % get_field_id(field))
                if '%s_display' % get_field_id(field) not in existing_fields:
                    cur.execute(
                        '''ALTER TABLE %s ADD COLUMN %s varchar'''
                        % (table_name, '%s_display' % get_field_id(field))
                    )
            if field.store_structured_value:
                needed_fields.add('%s_structured' % get_field_id(field))
                if '%s_structured' % get_field_id(field) not in existing_fields:
                    cur.execute(
                        '''ALTER TABLE %s ADD COLUMN %s bytea'''
                        % (table_name, '%s_structured' % get_field_id(field))
                    )

        if (field_integrity_errors or None) != formdef.sql_integrity_errors:
            formdef.sql_integrity_errors = field_integrity_errors
            formdef.store(object_only=True)

        for field in (formdef.geolocations or {}).keys():
            column_name = 'geoloc_%s' % field
            needed_fields.add(column_name)
            if column_name not in existing_fields:
                cur.execute('ALTER TABLE %s ADD COLUMN %s %s' '' % (table_name, column_name, 'POINT'))

        # delete obsolete fields
        for field in existing_fields - needed_fields:
            cur.execute('''ALTER TABLE %s DROP COLUMN %s CASCADE''' % (table_name, field))

        init_workflow_trace_delete_triggers(cur, formdef, table_name)
        if not table_name.startswith('test_'):
            if formdef.data_sql_prefix == 'formdata':
                recreate_trigger(formdef, cur, conn)
            elif formdef.data_sql_prefix == 'carddata':
                init_search_tokens_triggers_carddef(cur, formdef)

    with atomic():
        # migrations on _evolutions table
        cur.execute(
            '''SELECT column_name, data_type FROM information_schema.columns
                        WHERE table_schema = 'public'
                          AND table_name = '%s_evolutions'
                    '''
            % table_name
        )
        evo_existing_fields = {x[0]: x[1] for x in cur.fetchall()}
        if 'last_jump_datetime' not in evo_existing_fields:
            cur.execute(
                '''ALTER TABLE %s_evolutions ADD COLUMN last_jump_datetime timestamptz''' % table_name
            )

        if evo_existing_fields.get('time') not in (None, 'timestamp with time zone'):
            cur.execute(f'ALTER TABLE {table_name}_evolutions ALTER COLUMN time SET DATA TYPE timestamptz')
        if evo_existing_fields.get('last_jump_datetime') not in (None, 'timestamp with time zone'):
            cur.execute(
                f'ALTER TABLE {table_name}_evolutions ALTER COLUMN last_jump_datetime SET DATA TYPE timestamptz'
            )

    if table_name.startswith('test_'):
        return

    if rebuild_views or len(existing_fields - needed_fields):
        # views may have been dropped when dropping columns, so we recreate
        # them even if not asked to.
        redo_views(conn, cur, formdef, rebuild_global_views=rebuild_global_views)

    do_formdef_indexes(formdef, cur=cur)

    actions = []
    if 'concerned_roles_array' not in existing_fields:
        actions.append('rebuild_security')
    elif 'actions_roles_array' not in existing_fields:
        actions.append('rebuild_security')
    if 'tracking_code' not in existing_fields:
        # if tracking code has just been added to the table we need to make
        # sure the tracking code table does exist.
        actions.append('do_tracking_code_table')

    return actions


def recreate_trigger(formdef, cur, conn):
    # recreate the trigger function, just so it's uptodate
    table_name = get_formdef_table_name(formdef)
    category_value = formdef.category_id
    geoloc_base_x_query = 'NULL'
    geoloc_base_y_query = 'NULL'
    if formdef.geolocations and 'base' in formdef.geolocations:
        # default geolocation is in the 'base' key; we have to unstructure the
        # field is the POINT type of postgresql cannot be used directly as it
        # doesn't have an equality operator.
        geoloc_base_x_query = 'NEW.geoloc_base[0]'
        geoloc_base_y_query = 'NEW.geoloc_base[1]'
    if formdef.category_id is None:
        category_value = 'NULL'
    criticality_levels = len(formdef.workflow.criticality_levels or [0])
    endpoint_status = formdef.workflow.get_endpoint_status()
    endpoint_status_filter = ', '.join(["'wf-%s'" % x.id for x in endpoint_status])
    if endpoint_status_filter == '':
        # not the prettiest in town, but will do fine for now.
        endpoint_status_filter = "'xxxx'"
    formed_name_quotedstring = psycopg2.extensions.QuotedString(formdef.name)
    formed_name_quotedstring.encoding = 'utf8'
    formdef_name = formed_name_quotedstring.getquoted().decode()
    trigger_code = '''
BEGIN
    IF TG_OP = 'DELETE' THEN
        DELETE FROM wcs_all_forms WHERE formdef_id = {formdef_id} AND id = OLD.id;
        RETURN OLD;
    ELSEIF TG_OP = 'INSERT' AND NEW.test_result_id IS NULL THEN
        INSERT INTO wcs_all_forms VALUES (
            {category_id},
            {formdef_id},
            NEW.id,
            NEW.user_id,
            NEW.receipt_time,
            NEW.status,
            NEW.id_display,
            NEW.submission_agent_id,
            NEW.submission_channel,
            NEW.backoffice_submission,
            NEW.last_update_time,
            NEW.digests,
            NEW.user_label,
            NEW.concerned_roles_array,
            NEW.actions_roles_array,
            NEW.fts,
            NEW.status IN ({endpoint_status}),
            {formdef_name},
            (SELECT name FROM users WHERE users.id = CAST(NEW.user_id AS INTEGER)),
            NEW.criticality_level - {criticality_levels},
            {geoloc_base_x},
            {geoloc_base_y},
            NEW.anonymised,
            NEW.statistics_data,
            NEW.relations_data);
        RETURN NEW;
    ELSE
        UPDATE wcs_all_forms SET
                user_id = NEW.user_id,
                receipt_time = NEW.receipt_time,
                status = NEW.status,
                id_display = NEW.id_display,
                submission_agent_id = NEW.submission_agent_id,
                submission_channel = NEW.submission_channel,
                backoffice_submission = NEW.backoffice_submission,
                last_update_time = NEW.last_update_time,
                digests = NEW.digests,
                user_label = NEW.user_label,
                concerned_roles_array = NEW.concerned_roles_array,
                actions_roles_array = NEW.actions_roles_array,
                fts = NEW.fts,
                is_at_endpoint = NEW.status IN ({endpoint_status}),
                formdef_name = {formdef_name},
                user_name = (SELECT name FROM users WHERE users.id = CAST(NEW.user_id AS INTEGER)),
                criticality_level = NEW.criticality_level - {criticality_levels},
                geoloc_base_x = {geoloc_base_x},
                geoloc_base_y = {geoloc_base_y},
                anonymised = NEW.anonymised,
                statistics_data = NEW.statistics_data,
                relations_data = NEW.relations_data
            WHERE formdef_id = {formdef_id} AND id = OLD.id;
        RETURN NEW;
    END IF;
END;
'''.format(
        category_id=category_value,  # always valued ? need to handle null otherwise.
        formdef_id=formdef.id,
        geoloc_base_x=geoloc_base_x_query,
        geoloc_base_y=geoloc_base_y_query,
        formdef_name=formdef_name,
        criticality_levels=criticality_levels,
        endpoint_status=endpoint_status_filter,
    )
    cur.execute(
        '''SELECT prosrc FROM pg_proc
            WHERE proname = '%s'
        '''
        % get_formdef_trigger_function_name(formdef)
    )
    function_row = cur.fetchone()
    if function_row is None or function_row[0] != trigger_code:
        cur.execute(
            '''
CREATE OR REPLACE FUNCTION {trg_fn_name}()
RETURNS trigger
LANGUAGE plpgsql
AS $${code}$$;
        '''.format(
                trg_fn_name=get_formdef_trigger_function_name(formdef),
                code=trigger_code,
            )
        )

    trg_name = get_formdef_trigger_name(formdef)
    cur.execute(
        '''SELECT 1 FROM pg_trigger
            WHERE tgrelid = '%s'::regclass
              AND tgname = '%s'
        '''
        % (table_name, trg_name)
    )
    if len(cur.fetchall()) == 0:
        # compatibility note: to support postgresql<11 we use PROCEDURE and not FUNCTION
        cur.execute(
            '''CREATE TRIGGER {trg_name} AFTER INSERT OR UPDATE OR DELETE
                ON {table_name}
                FOR EACH ROW EXECUTE PROCEDURE {trg_fn_name}();
                '''.format(
                trg_fn_name=get_formdef_trigger_function_name(formdef),
                table_name=table_name,
                trg_name=trg_name,
            )
        )


def do_formdef_indexes(formdef, cur, concurrently=False):
    from wcs.carddef import CardDef

    table_name = get_formdef_table_name(formdef)
    evolutions_table_name = table_name + '_evolutions'

    SqlMixin.do_table_indexes(
        cur,
        evolutions_table_name,
        [f'{evolutions_table_name}_fid ON {evolutions_table_name} (formdata_id, id)'],
        concurrently=concurrently,
    )

    table_indexes = [f'{table_name}_fts ON {table_name} USING gin(fts)']

    attrs = ['receipt_time', 'anonymised', 'user_id', 'status']
    if isinstance(formdef, CardDef):
        attrs.append('id_display')
    for attr in attrs:
        table_indexes.append(f'{table_name}_{attr}_idx ON {table_name} ({attr})')
    for attr in ('concerned_roles_array', 'actions_roles_array', 'workflow_roles_array'):
        idx_name = 'idx_' + attr + '_' + table_name
        table_indexes.append(f'{idx_name} ON {table_name} USING gin ({attr})')
    table_indexes.append(
        f'''idx_workflow_processing_timestamp_{table_name}
            ON {table_name} (workflow_processing_timestamp)
            WHERE (workflow_processing_timestamp IS NOT NULL)'''
    )

    if isinstance(formdef, CardDef):
        idx_name = f'{table_name}_digests_default_idx'
        table_indexes.append(
            f'{table_name}_digests_default_idx ON {table_name} '
            "USING gist((digests->>'default') gist_trgm_ops)"
        )

    for field in formdef.get_all_fields():
        if (
            field.key == 'item'
            and field.data_source
            and field.data_source.get('type', '').startswith('carddef:')
        ):
            field_id = get_field_id(field)
            idx_name = f'{table_name}_auto_{field_id}'
            table_indexes.append(f'{table_name}_auto_{field_id}_idx ON {table_name} ({field_id})')

    SqlMixin.do_table_indexes(cur, table_name, table_indexes, concurrently=concurrently)


def do_user_table():
    _, cur = get_connection_and_cursor()
    table_name = 'users'

    cur.execute(
        '''SELECT COUNT(*) FROM information_schema.tables
                    WHERE table_schema = 'public'
                      AND table_name = %s''',
        (table_name,),
    )
    if cur.fetchone()[0] == 0:
        cur.execute(
            '''CREATE TABLE %s (id serial PRIMARY KEY,
                                    name varchar,
                                    ascii_name varchar,
                                    email varchar,
                                    roles text[],
                                    is_active bool,
                                    is_admin bool,
                                    verified_fields text[],
                                    name_identifiers text[],
                                    lasso_dump text,
                                    last_seen timestamp,
                                    deleted_timestamp timestamp,
                                    preferences jsonb,
                                    test_uuid varchar
                                    )'''
            % table_name
        )
    cur.execute(
        '''SELECT column_name FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = %s''',
        (table_name,),
    )
    existing_fields = {x[0] for x in cur.fetchall()}

    needed_fields = {
        'id',
        'name',
        'email',
        'roles',
        'is_admin',
        'name_identifiers',
        'verified_fields',
        'lasso_dump',
        'last_seen',
        'fts',
        'ascii_name',
        'deleted_timestamp',
        'is_active',
        'preferences',
        'test_uuid',
    }

    from wcs.admin.settings import UserFieldsFormDef

    formdef = UserFieldsFormDef()

    for field in formdef.get_all_fields():
        sql_type = SQL_TYPE_MAPPING.get(field.key, 'varchar')
        if sql_type is None:
            continue
        needed_fields.add(get_field_id(field))
        if get_field_id(field) not in existing_fields:
            cur.execute('''ALTER TABLE %s ADD COLUMN %s %s''' % (table_name, get_field_id(field), sql_type))
        if field.store_display_value:
            needed_fields.add('%s_display' % get_field_id(field))
            if '%s_display' % get_field_id(field) not in existing_fields:
                cur.execute(
                    '''ALTER TABLE %s ADD COLUMN %s varchar'''
                    % (table_name, '%s_display' % get_field_id(field))
                )
        if field.store_structured_value:
            needed_fields.add('%s_structured' % get_field_id(field))
            if '%s_structured' % get_field_id(field) not in existing_fields:
                cur.execute(
                    '''ALTER TABLE %s ADD COLUMN %s bytea'''
                    % (table_name, '%s_structured' % get_field_id(field))
                )

    # migrations
    if 'fts' not in existing_fields:
        # full text search
        cur.execute('''ALTER TABLE %s ADD COLUMN fts tsvector''' % table_name)

    if 'verified_fields' not in existing_fields:
        cur.execute('ALTER TABLE %s ADD COLUMN verified_fields text[]' % table_name)

    if 'ascii_name' not in existing_fields:
        cur.execute('ALTER TABLE %s ADD COLUMN ascii_name varchar' % table_name)

    if 'deleted_timestamp' not in existing_fields:
        cur.execute('ALTER TABLE %s ADD COLUMN deleted_timestamp timestamp' % table_name)

    if 'is_active' not in existing_fields:
        cur.execute('ALTER TABLE %s ADD COLUMN is_active bool DEFAULT TRUE' % table_name)
        cur.execute('UPDATE %s SET is_active = FALSE WHERE deleted_timestamp IS NOT NULL' % table_name)

    if 'preferences' not in existing_fields:
        cur.execute('ALTER TABLE %s ADD COLUMN preferences jsonb' % table_name)

    if 'test_uuid' not in existing_fields:
        cur.execute('ALTER TABLE %s ADD COLUMN test_uuid varchar' % table_name)

    # delete obsolete fields
    for field in existing_fields - needed_fields:
        cur.execute('''ALTER TABLE %s DROP COLUMN %s''' % (table_name, field))

    SqlUser.do_indexes(cur)
    cur.close()


def do_role_table():
    _, cur = get_connection_and_cursor()
    table_name = 'roles'

    cur.execute(
        '''SELECT COUNT(*) FROM information_schema.tables
                    WHERE table_schema = 'public'
                      AND table_name = %s''',
        (table_name,),
    )
    if cur.fetchone()[0] == 0:
        cur.execute(
            '''CREATE TABLE %s (id VARCHAR PRIMARY KEY,
                                name VARCHAR,
                                uuid UUID,
                                slug VARCHAR UNIQUE,
                                internal BOOLEAN,
                                details VARCHAR,
                                emails VARCHAR[],
                                emails_to_members BOOLEAN,
                                allows_backoffice_access BOOLEAN)'''
            % table_name
        )
    cur.execute('ALTER TABLE roles ALTER COLUMN uuid TYPE VARCHAR')
    cur.execute(
        '''SELECT column_name FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = %s''',
        (table_name,),
    )
    existing_fields = {x[0] for x in cur.fetchall()}

    needed_fields = {x[0] for x in Role._table_static_fields}

    # delete obsolete fields
    for field in existing_fields - needed_fields:
        cur.execute('''ALTER TABLE %s DROP COLUMN %s''' % (table_name, field))

    cur.close()


def migrate_legacy_roles():
    # store old pickle roles in SQL
    for role_id in wcs.roles.Role.keys():
        role = wcs.roles.Role.get(role_id)
        role.__class__ = Role
        role.store()


def do_tracking_code_table():
    _, cur = get_connection_and_cursor()
    table_name = 'tracking_codes'

    cur.execute(
        '''SELECT COUNT(*) FROM information_schema.tables
                    WHERE table_schema = 'public'
                      AND table_name = %s''',
        (table_name,),
    )
    if cur.fetchone()[0] == 0:
        cur.execute(
            '''CREATE TABLE %s (id varchar PRIMARY KEY,
                                    formdef_id varchar,
                                    formdata_id varchar)'''
            % table_name
        )
    cur.execute(
        '''SELECT column_name FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = %s''',
        (table_name,),
    )
    existing_fields = {x[0] for x in cur.fetchall()}

    needed_fields = {'id', 'formdef_id', 'formdata_id'}

    # delete obsolete fields
    for field in existing_fields - needed_fields:
        cur.execute('''ALTER TABLE %s DROP COLUMN %s''' % (table_name, field))

    cur.close()


def do_session_table():
    _, cur = get_connection_and_cursor()
    table_name = 'sessions'

    cur.execute(
        '''SELECT COUNT(*) FROM information_schema.tables
                    WHERE table_schema = 'public'
                      AND table_name = %s''',
        (table_name,),
    )
    if cur.fetchone()[0] == 0:
        cur.execute(
            '''CREATE TABLE %s (id varchar PRIMARY KEY,
                                session_data bytea,
                                name_identifier varchar,
                                visiting_objects_keys varchar[],
                                last_update_time timestamp,
                                creation_time timestamp,
                                access_time timestamp,
                                remote_address inet
                                )'''
            % table_name
        )
    cur.execute(
        '''SELECT column_name FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = %s''',
        (table_name,),
    )
    existing_fields = {x[0] for x in cur.fetchall()}

    needed_fields = {x[0] for x in Session._table_static_fields} | {
        'name_identifier',
        'visiting_objects_keys',
        'last_update_time',
    }

    # migrations
    if 'last_update_time' not in existing_fields:
        cur.execute('''ALTER TABLE %s ADD COLUMN last_update_time timestamp DEFAULT NOW()''' % table_name)
    if 'creation_time' not in existing_fields:
        cur.execute('''ALTER TABLE %s ADD COLUMN creation_time timestamp''' % table_name)
    if 'access_time' not in existing_fields:
        cur.execute('''ALTER TABLE %s ADD COLUMN access_time timestamp''' % table_name)
    if 'remote_address' not in existing_fields:
        cur.execute('''ALTER TABLE %s ADD COLUMN remote_address inet''' % table_name)

    # delete obsolete fields
    for field in existing_fields - needed_fields:
        cur.execute('''ALTER TABLE %s DROP COLUMN %s''' % (table_name, field))

    Session.do_indexes(cur)
    cur.close()


def do_transient_data_table():
    _, cur = get_connection_and_cursor()
    table_name = TransientData._table_name

    cur.execute(
        '''SELECT COUNT(*) FROM information_schema.tables
                    WHERE table_schema = 'public'
                      AND table_name = %s''',
        (table_name,),
    )
    if cur.fetchone()[0] == 0:
        cur.execute(
            '''CREATE TABLE %s (id varchar PRIMARY KEY,
                                session_id VARCHAR REFERENCES sessions(id) ON DELETE CASCADE,
                                data bytea,
                                last_update_time timestamptz
                                )'''
            % table_name
        )
    cur.execute(
        '''SELECT column_name FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = %s''',
        (table_name,),
    )
    existing_fields = {x[0] for x in cur.fetchall()}
    needed_fields = {x[0] for x in TransientData._table_static_fields}

    # delete obsolete fields
    for field in existing_fields - needed_fields:
        cur.execute('''ALTER TABLE %s DROP COLUMN %s''' % (table_name, field))

    cur.close()


def do_custom_views_table():
    _, cur = get_connection_and_cursor()
    table_name = 'custom_views'

    cur.execute(
        '''SELECT COUNT(*) FROM information_schema.tables
                    WHERE table_schema = 'public'
                      AND table_name = %s''',
        (table_name,),
    )
    if cur.fetchone()[0] == 0:
        cur.execute(
            '''CREATE TABLE %s (id SERIAL PRIMARY KEY,
                                        title varchar,
                                        slug varchar,
                                        user_id varchar,
                                        author_id varchar,
                                        visibility varchar,
                                        formdef_type varchar,
                                        formdef_id varchar,
                                        is_default boolean,
                                        order_by varchar,
                                        group_by varchar,
                                        columns jsonb,
                                        filters jsonb
                                        )'''
            % table_name
        )
    cur.execute(
        '''SELECT column_name, data_type FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = %s''',
        (table_name,),
    )
    existing_field_types = {x[0]: x[1] for x in cur.fetchall()}
    existing_fields = set(existing_field_types.keys())

    needed_fields = {x[0] for x in CustomView._table_static_fields}

    # migrations
    if 'is_default' not in existing_fields:
        cur.execute('''ALTER TABLE %s ADD COLUMN is_default boolean DEFAULT FALSE''' % table_name)
    for column in ('role_id', 'group_by', 'author_id'):
        if column not in existing_fields:
            cur.execute(f'ALTER TABLE {table_name} ADD COLUMN {column} VARCHAR')

    if existing_field_types.get('id') == 'character varying':
        cur.execute(
            '''ALTER TABLE custom_views
                      ALTER COLUMN id TYPE INTEGER USING (id::integer),
                      ALTER COLUMN id SET NOT NULL'''
        )
        cur.execute('SELECT MAX(id) FROM custom_views')
        row = cur.fetchone()
        highest_id = (row[0] or 0) + 1
        cur.execute(
            f'''CREATE SEQUENCE IF NOT EXISTS custom_views_id_seq AS integer
                               OWNED BY custom_views.id
                             START WITH {highest_id}'''
        )
        cur.execute(
            '''ALTER TABLE custom_views
              ALTER COLUMN id SET DEFAULT nextval('custom_views_id_seq')'''
        )

    # delete obsolete fields
    for field in existing_fields - needed_fields:
        cur.execute('''ALTER TABLE %s DROP COLUMN %s''' % (table_name, field))

    CustomView.do_indexes(cur)
    cur.close()


def do_snapshots_table():
    _, cur = get_connection_and_cursor()
    table_name = 'snapshots'

    cur.execute(
        '''SELECT COUNT(*) FROM information_schema.tables
                    WHERE table_schema = 'public'
                      AND table_name = %s''',
        (table_name,),
    )
    if cur.fetchone()[0] == 0:
        cur.execute(
            '''CREATE TABLE %s (id SERIAL PRIMARY KEY,
                                        object_type VARCHAR,
                                        object_id VARCHAR,
                                        timestamp TIMESTAMP WITH TIME ZONE,
                                        user_id VARCHAR,
                                        comment TEXT,
                                        serialization TEXT,
                                        patch TEXT,
                                        label VARCHAR,
                                        test_results_id INTEGER,
                                        application_slug VARCHAR,
                                        application_version VARCHAR,
                                        application_ignore_change BOOLEAN DEFAULT FALSE,
                                        deleted_object BOOLEAN DEFAULT FALSE
                                        )'''
            % table_name
        )
    cur.execute(
        '''SELECT column_name FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = %s''',
        (table_name,),
    )
    existing_fields = {x[0] for x in cur.fetchall()}

    if 'test_result_id' in existing_fields:
        cur.execute('ALTER TABLE %s RENAME COLUMN test_result_id TO test_results_id' % table_name)
        existing_fields.remove('test_result_id')
        existing_fields.add('test_results_id')

    if 'application_ignore_change' not in existing_fields:
        cur.execute('ALTER TABLE %s ADD COLUMN application_ignore_change BOOLEAN DEFAULT FALSE' % table_name)
        existing_fields.add('application_ignore_change')

    if 'deleted_object' not in existing_fields:
        cur.execute('ALTER TABLE %s ADD COLUMN deleted_object BOOLEAN DEFAULT FALSE' % table_name)
        existing_fields.add('deleted_object')

    # generic migration for new columns
    for field_name, field_type in Snapshot._table_static_fields:
        if field_name not in existing_fields:
            cur.execute('''ALTER TABLE %s ADD COLUMN %s %s''' % (table_name, field_name, field_type))

    needed_fields = {x[0] for x in Snapshot._table_static_fields}

    # delete obsolete fields
    for field in existing_fields - needed_fields:
        cur.execute('''ALTER TABLE %s DROP COLUMN %s''' % (table_name, field))

    Snapshot.do_indexes(cur)
    cur.close()


def do_loggederrors_table():
    _, cur = get_connection_and_cursor()
    table_name = 'loggederrors'

    cur.execute(
        '''SELECT COUNT(*) FROM information_schema.tables
                    WHERE table_schema = 'public'
                      AND table_name = %s''',
        (table_name,),
    )
    if cur.fetchone()[0] == 0:
        cur.execute(
            '''CREATE TABLE %s (id SERIAL PRIMARY KEY,
                                kind VARCHAR,
                                tech_id VARCHAR UNIQUE,
                                summary VARCHAR,
                                formdef_class VARCHAR,
                                formdata_id VARCHAR,
                                formdef_id VARCHAR,
                                workflow_id VARCHAR,
                                status_id VARCHAR,
                                status_item_id VARCHAR,
                                expression VARCHAR,
                                expression_type VARCHAR,
                                context JSONB,
                                traceback TEXT,
                                exception_class VARCHAR,
                                exception_message VARCHAR,
                                occurences_count INTEGER,
                                first_occurence_timestamp TIMESTAMP WITH TIME ZONE,
                                latest_occurence_timestamp TIMESTAMP WITH TIME ZONE,
                                deleted_timestamp TIMESTAMP WITH TIME ZONE,
                                documentation TEXT
                                )'''
            % table_name
        )
    cur.execute(
        '''SELECT column_name FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = %s''',
        (table_name,),
    )
    existing_fields = {x[0] for x in cur.fetchall()}

    needed_fields = {x[0] for x in LoggedError._table_static_fields}

    # migrations
    if 'kind' not in existing_fields:
        cur.execute('''ALTER TABLE %s ADD COLUMN kind VARCHAR''' % table_name)
    if 'context' not in existing_fields:
        cur.execute('''ALTER TABLE %s ADD COLUMN context JSONB''' % table_name)
    if 'deleted_timestamp' not in existing_fields:
        cur.execute('ALTER TABLE %s ADD COLUMN deleted_timestamp TIMESTAMPTZ' % table_name)
    if 'documentation' not in existing_fields:
        cur.execute('ALTER TABLE %s ADD COLUMN documentation TEXT' % table_name)

    # delete obsolete fields
    for field in existing_fields - needed_fields:
        cur.execute('''ALTER TABLE %s DROP COLUMN %s''' % (table_name, field))

    LoggedError.do_indexes(cur)
    cur.close()


def do_tokens_table():
    _, cur = get_connection_and_cursor()
    table_name = Token._table_name

    cur.execute(
        '''SELECT COUNT(*) FROM information_schema.tables
                    WHERE table_schema = 'public'
                      AND table_name = %s''',
        (table_name,),
    )
    if cur.fetchone()[0] == 0:
        cur.execute(
            '''CREATE TABLE %s (id VARCHAR PRIMARY KEY,
                                type VARCHAR,
                                expiration TIMESTAMPTZ,
                                context JSONB
                               )'''
            % table_name
        )
    cur.execute(
        '''SELECT column_name FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = %s''',
        (table_name,),
    )
    existing_fields = {x[0] for x in cur.fetchall()}

    needed_fields = {x[0] for x in Token._table_static_fields}

    # delete obsolete fields
    for field in existing_fields - needed_fields:
        cur.execute('''ALTER TABLE %s DROP COLUMN %s''' % (table_name, field))

    cur.close()


def migrate_legacy_tokens():
    # store old pickle tokens in SQL
    for token_id in wcs.qommon.tokens.Token.keys():
        try:
            token = wcs.qommon.tokens.Token.get(token_id)
        except KeyError:
            continue
        except AttributeError:
            # old python2 tokens:
            # AttributeError: module 'builtins' has no attribute 'unicode'
            wcs.qommon.tokens.Token.remove_object(token_id)
            continue
        token.__class__ = Token
        token.store()


def do_meta_table(conn=None, cur=None, insert_current_sql_level=True):
    own_conn = False
    if not conn:
        own_conn = True
        conn, cur = get_connection_and_cursor()

    cur.execute(
        '''CREATE TABLE IF NOT EXISTS wcs_meta (id serial PRIMARY KEY,
                                key varchar,
                                value varchar,
                                created_at timestamptz DEFAULT NOW(),
                                updated_at timestamptz DEFAULT NOW())'''
    )
    cur.execute('CREATE UNIQUE INDEX IF NOT EXISTS wcs_meta_key ON wcs_meta (key)')

    if insert_current_sql_level:
        sql_level = SQL_LEVEL[0]
    else:
        sql_level = 0
    cur.execute(
        '''INSERT INTO wcs_meta (id, key, value)
                   VALUES (DEFAULT, %s, %s) ON CONFLICT (key) DO NOTHING''',
        ('sql_level', str(sql_level)),
    )

    # The table may not have been created above, check all columns are here
    cur.execute(
        'SELECT attname FROM pg_attribute WHERE attrelid = %s::regclass AND attnum > 0;',
        ('wcs_meta',),
    )
    existing_fields = {x[0] for x in cur.fetchall()}
    if 'created_at' not in existing_fields:
        cur.execute('''ALTER TABLE wcs_meta ADD COLUMN created_at timestamptz DEFAULT NOW()''')
    if 'updated_at' not in existing_fields:
        cur.execute('''ALTER TABLE wcs_meta ADD COLUMN updated_at timestamptz DEFAULT NOW()''')

    if own_conn:
        cur.close()


def redo_views(conn, cur, formdef, rebuild_global_views=False):
    if formdef.id is None:
        return

    if get_publisher().has_site_option('sql-create-formdef-views'):
        drop_views(formdef, conn, cur)
        do_views(formdef, conn, cur, rebuild_global_views=rebuild_global_views)


def drop_views(formdef, conn, cur):
    # remove the global views
    drop_global_views(conn, cur)

    view_names = []
    if formdef:
        # remove the form view itself
        view_prefix = 'wcs\\_view\\_%s\\_%%' % formdef.id
        if formdef.data_sql_prefix != 'formdata':
            view_prefix = 'wcs\\_%s\\_view\\_%s\\_%%' % (formdef.data_sql_prefix, formdef.id)
        cur.execute(
            '''SELECT table_name FROM information_schema.views
                        WHERE table_schema = 'public'
                          AND table_name LIKE %s''',
            (view_prefix,),
        )
    else:
        # if there's no formdef specified, remove all form & card views
        cur.execute(
            '''SELECT table_name FROM information_schema.views
                        WHERE table_schema = 'public'
                          AND table_name LIKE %s''',
            ('wcs\\_view\\_%',),
        )
        while True:
            row = cur.fetchone()
            if row is None:
                break
            view_names.append(row[0])

        cur.execute(
            '''SELECT table_name FROM information_schema.views
                        WHERE table_schema = 'public'
                          AND table_name LIKE %s''',
            ('wcs\\_carddata\\_view\\_%',),
        )

    while True:
        row = cur.fetchone()
        if row is None:
            break
        view_names.append(row[0])

    for view_name in view_names:
        cur.execute('''DROP VIEW IF EXISTS %s''' % view_name)


def get_view_fields(formdef):
    view_fields = []
    view_fields.append(("int '%s'" % (formdef.category_id or 0), 'category_id'))
    view_fields.append(("int '%s'" % (formdef.id or 0), 'formdef_id'))
    for field in (
        'id',
        'user_id',
        'receipt_time',
        'status',
        'id_display',
        'submission_agent_id',
        'submission_channel',
        'backoffice_submission',
        'last_update_time',
        'digests',
        'user_label',
    ):
        view_fields.append((field, field))
    return view_fields


def do_views(formdef, conn, cur, rebuild_global_views=True):
    # create new view
    table_name = get_formdef_table_name(formdef)
    view_name = get_formdef_view_name(formdef)
    view_fields = get_view_fields(formdef)

    column_names = {}
    for field in formdef.get_all_fields():
        field_key = get_field_id(field)
        if field.is_no_data_field:
            continue
        if field.varname:
            # the variable should be fine as is but we pass it through
            # get_name_as_sql_identifier nevertheless, to be extra sure it
            # doesn't contain invalid characters.
            field_name = 'f_%s' % get_name_as_sql_identifier(field.varname)[:50]
        else:
            field_name = '%s_%s' % (get_field_id(field), get_name_as_sql_identifier(field.label))
            field_name = field_name[:50]
        if field_name in column_names:
            # it may happen that the same varname is used on multiple fields
            # (for example in the case of conditional pages), in that situation
            # we suffix the field name with an index count
            while field_name in column_names:
                column_names[field_name] += 1
                field_name = '%s_%s' % (field_name, column_names[field_name])
        column_names[field_name] = 1
        view_fields.append((field_key, field_name))
        if field.store_display_value:
            field_key = '%s_display' % get_field_id(field)
            view_fields.append((field_key, field_name + '_display'))

    view_fields.append(
        (
            '''ARRAY(SELECT status FROM %s_evolutions '''
            '''      WHERE %s.id = %s_evolutions.formdata_id'''
            '''      ORDER BY %s_evolutions.time)''' % ((table_name,) * 4),
            'status_history',
        )
    )

    # add a is_at_endpoint column, dynamically created againt the endpoint status.
    endpoint_status = formdef.workflow.get_endpoint_status()
    view_fields.append(
        (
            '''(SELECT status = ANY(ARRAY[[%s]]::text[]))'''
            % ', '.join(["'wf-%s'" % x.id for x in endpoint_status]),
            '''is_at_endpoint''',
        )
    )

    # [CRITICALITY_1] Add criticality_level, computed relative to levels in
    # the given workflow, so all higher criticalites are sorted first. This is
    # reverted when loading the formdata back, in [CRITICALITY_2]
    levels = len(formdef.workflow.criticality_levels or [0])
    view_fields.append(('''(criticality_level - %d)''' % levels, '''criticality_level'''))

    view_fields.append((cur.mogrify('(SELECT text %s)', (formdef.name,)), 'formdef_name'))

    view_fields.append(
        (
            '''(SELECT name FROM users
                             WHERE users.id = CAST(user_id AS INTEGER))''',
            'user_name',
        )
    )

    view_fields.append(('concerned_roles_array', 'concerned_roles_array'))
    view_fields.append(('actions_roles_array', 'actions_roles_array'))
    view_fields.append(('fts', 'fts'))

    if formdef.geolocations and 'base' in formdef.geolocations:
        # default geolocation is in the 'base' key; we have to unstructure the
        # field is the POINT type of postgresql cannot be used directly as it
        # doesn't have an equality operator.
        view_fields.append(('geoloc_base[0]', 'geoloc_base_x'))
        view_fields.append(('geoloc_base[1]', 'geoloc_base_y'))
    else:
        view_fields.append(('NULL::real', 'geoloc_base_x'))
        view_fields.append(('NULL::real', 'geoloc_base_y'))
    view_fields.append(('anonymised', 'anonymised'))

    fields_list = ', '.join(['%s AS %s' % (force_str(x), force_str(y)) for (x, y) in view_fields])

    cur.execute('''CREATE VIEW %s AS SELECT %s FROM %s''' % (view_name, fields_list, table_name))

    if rebuild_global_views:
        do_global_views(conn, cur)  # recreate global views


def drop_global_views(conn, cur):
    cur.execute(
        '''SELECT table_name FROM information_schema.views
                    WHERE table_schema = 'public'
                      AND table_name LIKE %s''',
        ('wcs\\_category\\_%',),
    )
    view_names = []
    while True:
        row = cur.fetchone()
        if row is None:
            break
        view_names.append(row[0])

    for view_name in view_names:
        cur.execute('''DROP VIEW IF EXISTS %s''' % view_name)


def update_global_view_formdef_category(formdef):
    _, cur = get_connection_and_cursor()
    with cur:
        cur.execute(
            '''UPDATE wcs_all_forms set category_id = %s WHERE formdef_id = %s''',
            (formdef.category_id, formdef.id),
        )


def do_global_views(conn, cur):
    # recreate global views
    # XXX TODO: make me dynamic, please ?
    cur.execute(
        """CREATE TABLE IF NOT EXISTS wcs_all_forms (
        category_id integer,
        formdef_id integer NOT NULL,
        id integer NOT NULL,
        user_id character varying,
        receipt_time timestamp with time zone,
        status character varying,
        id_display character varying,
        submission_agent_id character varying,
        submission_channel character varying,
        backoffice_submission boolean,
        last_update_time timestamp with time zone,
        digests jsonb,
        user_label character varying,
        concerned_roles_array text[],
        actions_roles_array text[],
        fts tsvector,
        is_at_endpoint boolean,
        formdef_name text,
        user_name character varying,
        criticality_level integer,
        geoloc_base_x double precision,
        geoloc_base_y double precision,
        anonymised timestamp with time zone,
        statistics_data jsonb,
        relations_data jsonb
        , PRIMARY KEY(formdef_id, id)
    )"""
    )
    indexes = []
    for attr in ('receipt_time', 'anonymised', 'user_id', 'status', 'category_id'):
        indexes.append(f'wcs_all_forms_{attr} ON wcs_all_forms ({attr})')
    for attr in ('fts', 'concerned_roles_array', 'actions_roles_array'):
        indexes.append(f'wcs_all_forms_{attr} ON wcs_all_forms USING gin({attr})')
    indexes.append(
        '''wcs_all_forms_actions_roles_live ON wcs_all_forms
           USING gin(actions_roles_array) WHERE (anonymised IS NULL AND is_at_endpoint = false)'''
    )

    SqlMixin.do_table_indexes(cur, 'wcs_all_forms', indexes)

    # make sure the table will not be changed while we work on it
    with atomic():
        cur.execute('LOCK TABLE wcs_all_forms;')

        cur.execute(
            '''SELECT column_name, data_type FROM information_schema.columns
                        WHERE table_schema = 'public'
                          AND table_name = %s''',
            ('wcs_all_forms',),
        )
        existing_fields = {x[0]: x[1] for x in cur.fetchall()}
        if 'statistics_data' not in existing_fields:
            cur.execute('ALTER TABLE wcs_all_forms ADD COLUMN statistics_data jsonb')
        if 'relations_data' not in existing_fields:
            cur.execute('ALTER TABLE wcs_all_forms ADD COLUMN relations_data jsonb')

        if existing_fields.get('receipt_time') not in (None, 'timestamp with time zone'):
            cur.execute('ALTER TABLE wcs_all_forms ALTER COLUMN receipt_time SET DATA TYPE timestamptz')
        if existing_fields.get('last_update_time') not in (None, 'timestamp with time zone'):
            cur.execute('ALTER TABLE wcs_all_forms ALTER COLUMN last_update_time SET DATA TYPE timestamptz')

        clean_global_views(conn, cur)

        import wcs.categories

        for category in wcs.categories.Category.select():
            name = get_name_as_sql_identifier(category.url_name)[:40]
            cur.execute(
                '''CREATE OR REPLACE VIEW wcs_category_%s AS SELECT * from wcs_all_forms
                            WHERE category_id = %s'''
                % (name, category.id)
            )

    init_search_tokens_triggers(cur)


def clean_global_views(conn, cur):
    # Purge of any dead data
    from wcs.formdef import FormDef

    valid_ids = [int(i) for i in FormDef.keys()]
    if valid_ids:
        cur.execute('DELETE FROM wcs_all_forms WHERE NOT formdef_id = ANY(%s)', (valid_ids,))
    else:
        cur.execute('TRUNCATE wcs_all_forms')


def init_global_table(conn=None, cur=None):
    from wcs.formdef import FormDef

    own_conn = False
    if not conn:
        own_conn = True
        conn, cur = get_connection_and_cursor()

    cur.execute("SELECT relkind FROM pg_class WHERE relname = 'wcs_all_forms';")
    rows = cur.fetchall()
    if len(rows) != 0:
        if rows[0][0] == 'v':
            # force wcs_all_forms table creation
            cur.execute('DROP VIEW IF EXISTS wcs_all_forms CASCADE;')
        else:
            assert rows[0][0] == 'r'
            cur.execute('DROP TABLE wcs_all_forms CASCADE;')

    do_global_views(conn, cur)

    # now copy all data into the table
    for formdef in FormDef.select():
        category_value = formdef.category_id
        if formdef.category_id is None:
            category_value = 'NULL'
        geoloc_base_x_query = 'NULL'
        geoloc_base_y_query = 'NULL'
        if formdef.geolocations and 'base' in formdef.geolocations:
            # default geolocation is in the 'base' key; we have to unstructure the
            # field is the POINT type of postgresql cannot be used directly as it
            # doesn't have an equality operator.
            geoloc_base_x_query = 'geoloc_base[0]'
            geoloc_base_y_query = 'geoloc_base[1]'
        criticality_levels = len(formdef.workflow.criticality_levels or [0])
        endpoint_status = formdef.workflow.get_endpoint_status()
        endpoint_status_filter = ', '.join(["'wf-%s'" % x.id for x in endpoint_status])
        if endpoint_status_filter == '':
            # not the prettiest in town, but will do fine for now.
            endpoint_status_filter = "'xxxx'"
        formed_name_quotedstring = psycopg2.extensions.QuotedString(formdef.name)
        formed_name_quotedstring.encoding = 'utf8'
        formdef_name = formed_name_quotedstring.getquoted().decode()
        cur.execute(
            """
            INSERT INTO wcs_all_forms
            SELECT
                {category_id},
                {formdef_id},
                id,
                user_id,
                receipt_time,
                status,
                id_display,
                submission_agent_id,
                submission_channel,
                backoffice_submission,
                last_update_time,
                digests,
                user_label,
                concerned_roles_array,
                actions_roles_array,
                fts,
                status IN ({endpoint_status}),
                {formdef_name},
                (SELECT name FROM users WHERE users.id = CAST(user_id AS INTEGER)),
                criticality_level - {criticality_levels},
                {geoloc_base_x},
                {geoloc_base_y},
                anonymised,
                statistics_data,
                relations_data
            FROM {table_name}
            ON CONFLICT DO NOTHING;
                """.format(
                table_name=get_formdef_table_name(formdef),
                category_id=category_value,  # always valued ? need to handle null otherwise.
                formdef_id=formdef.id,
                geoloc_base_x=geoloc_base_x_query,
                geoloc_base_y=geoloc_base_y_query,
                formdef_name=formdef_name,
                criticality_levels=criticality_levels,
                endpoint_status=endpoint_status_filter,
            )
        )
        set_reindex('init_search_tokens_data', 'needed', conn=conn, cur=cur)

    if own_conn:
        cur.close()


def init_search_tokens(conn=None, cur=None):
    """Initialize the search_tokens mechanism.

    It's based on three parts:
    - a token table
    - triggers to feed this table from the tsvectors used in the database
    - a search function that will leverage these tokens to extend the search query.

    So far, the sources used are wcs_all_forms, searchable_formdefs and carddatas.

    Example: let's say the sources texts are "Tarif d'école" and "La cantine".
    This gives the following tsvectors: ('tarif', 'écol') and ('cantin')
    Our tokens table will have these three words.
    When the search function is launched, it splits the search query and will
    replace unavailable tokens by those close, if available.
    The search query 'tari' will be expanded to 'tarif'.
    The search query 'collège' will remain unchanged (and return nothing)
    If several tokens match or are close enough, the query will be expanded to
    an OR.
    """

    own_cur = False
    if cur is None:
        own_cur = True
        conn, cur = get_connection_and_cursor()

    if _table_exists(cur, 'wcs_search_tokens'):
        if not _column_exists(cur, 'wcs_search_tokens', 'context'):
            # Simply drop and recreate, this will speed up things
            cur.execute('DROP FUNCTION wcs_search_tokens_trigger_fn() CASCADE')
            cur.execute('DROP TABLE wcs_search_tokens')

    # Create table
    cur.execute(
        'CREATE TABLE IF NOT EXISTS wcs_search_tokens(token TEXT NOT NULL, context TEXT NOT NULL, PRIMARY KEY(context, token))'
    )

    # Create triggers
    init_search_tokens_triggers(cur)

    # Fill table
    set_reindex('init_search_tokens_data', 'needed', conn=conn, cur=cur)

    # Index at the end, small performance trick... not that useful, but it's free...
    cur.execute('CREATE EXTENSION IF NOT EXISTS pg_trgm')
    SqlMixin.do_table_indexes(
        cur,
        'wcs_search_tokens',
        ['wcs_search_tokens_trgm ON wcs_search_tokens USING gin(token gin_trgm_ops)'],
    )

    # And last: functions to use this brand new table
    # These two aggregates make the search query far simpler to write, allowing writing an OR/AND of search terms
    # directly as an SQL aggregation.
    # They use the tsquery_or and tsquery_and functions that are included in PostgreSQL since 8.3, but documented
    # under their operator names || and &&.
    cur.execute('CREATE OR REPLACE AGGREGATE tsquery_agg_or  (tsquery) (sfunc=tsquery_or,  stype=tsquery)')
    cur.execute('CREATE OR REPLACE AGGREGATE tsquery_agg_and (tsquery) (sfunc=tsquery_and, stype=tsquery)')
    # TODO: DROP the single-parameter one
    cur.execute(
        r"""CREATE OR REPLACE FUNCTION public.wcs_tsquery(text)
 RETURNS tsquery
 LANGUAGE sql
 IMMUTABLE
AS $function$
WITH
        tokenized AS (SELECT unnest(tsvector_to_array(coalesce(nullif(to_tsvector($1), ''), to_tsvector('simple', $1)))) word),
        super_tokenized AS (
            -- perfect: tokens that are found as is in table, thus no OR required
            -- partial: tokens found using distance search on tokens table (note: numbers are excluded here)
            --          distance search is done using pg_trgm, https://www.postgresql.org/docs/current/pgtrgm.html
            -- otherwise: token as is and likely no search result later
            SELECT word,
                coalesce((SELECT tsquery_agg_or(token) FROM (SELECT plainto_tsquery('simple', token) AS token FROM (
                            SELECT DISTINCT token FROM wcs_search_tokens partial
                            WHERE partial.token % word AND word not similar to '%[0-9]{2,}%'
                          ) foo ORDER BY word <-> foo.token LIMIT 5) bar),
                         plainto_tsquery('simple', word)
                        ) AS tokens
            FROM tokenized)
SELECT tsquery_agg_and(tokens) FROM super_tokenized;
$function$"""
    )
    cur.execute(
        r"""CREATE OR REPLACE FUNCTION public.wcs_tsquery(query text, context text)
 RETURNS tsquery
 LANGUAGE sql
 IMMUTABLE
AS $function$
WITH
        tokenized AS (SELECT unnest(tsvector_to_array(coalesce(nullif(to_tsvector($1), ''), to_tsvector('simple', $1)))) word),
        super_tokenized AS (
            -- perfect: tokens that are found as is in table, thus no OR required
            -- partial: tokens found using distance search on tokens table (note: numbers are excluded here)
            --          distance search is done using pg_trgm, https://www.postgresql.org/docs/current/pgtrgm.html
            -- otherwise: token as is and likely no search result later
            SELECT word,
                coalesce((SELECT tsquery_agg_or(token) FROM (SELECT plainto_tsquery('simple', token) AS token FROM (
                            SELECT DISTINCT token FROM wcs_search_tokens partial
                            WHERE partial.context = $2 AND partial.token % word AND word not similar to '%[0-9]{2,}%'
                          ) foo ORDER BY word <-> foo.token LIMIT 5) bar),
                         plainto_tsquery('simple', word)
                        ) AS tokens
            FROM tokenized)
SELECT tsquery_agg_and(tokens) FROM super_tokenized;
$function$"""
    )

    if own_cur:
        cur.close()


def init_search_tokens_triggers_carddef(cur, carddef):
    if not (_table_exists(cur, 'wcs_search_tokens')):
        # abort trigger creation if tokens table doesn't exist yet
        return

    data_class = carddef.data_class()
    table = data_class._table_name
    trigger_prefix = table[:40]
    context = '%s_%s' % (carddef.data_sql_prefix, carddef.id)
    if not _trigger_exists(cur, table, trigger_prefix + '__search_tokens_trg_ins'):
        cur.execute(
            """CREATE TRIGGER %s__search_tokens_trg_ins
            AFTER INSERT ON %s
            FOR EACH ROW WHEN (NEW.fts IS NOT NULL)
            EXECUTE PROCEDURE wcs_search_tokens_trigger_fn(%%s)"""
            % (trigger_prefix, table),
            (context,),
        )
        cur.execute(
            """CREATE TRIGGER %s__search_tokens_trg_upd
            AFTER UPDATE OF fts ON %s
            FOR EACH ROW WHEN (NEW.fts IS NOT NULL)
            EXECUTE PROCEDURE wcs_search_tokens_trigger_fn(%%s)"""
            % (trigger_prefix, table),
            (context,),
        )


def init_search_tokens_triggers(cur):
    from wcs.carddef import CardDef

    # We define only appending triggers, ie on INSERT and UPDATE.
    # It would be far heavier to maintain deletions here, and having extra data has
    # no or marginal side effect on search performances, and absolutely no impact
    # on search results.
    # Instead, a weekly cron job will delete obsolete entries, thus making it sure no
    # personal data is kept uselessly.
    # First part: the appending function
    cur.execute(
        """CREATE OR REPLACE FUNCTION wcs_search_tokens_trigger_fn ()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
DECLARE
    trg_context TEXT;
BEGIN
    IF TG_NARGS <> 1 THEN
        RAISE EXCEPTION 'Missing context for wcs_search_tokens trigger';
    END IF;
    trg_context := TG_ARGV[0];
    IF right(trg_context, 1) = '_' THEN
        trg_context := trg_context || NEW.formdef_id;
    END IF;
    INSERT INTO wcs_search_tokens
        SELECT token, trg_context FROM
            (SELECT unnest(tsvector_to_array(NEW.fts)) token) tokens
        WHERE token not similar to '%[0-9]{2,}%'
        ON CONFLICT(token, context) DO NOTHING;
    RETURN NEW;
END;
$function$"""
    )

    if not (_table_exists(cur, 'wcs_search_tokens')):
        # abort trigger creation if tokens table doesn't exist yet
        return

    if _table_exists(cur, 'wcs_all_forms') and not _trigger_exists(
        cur, 'wcs_all_forms', 'wcs_all_forms_fts_trg_upd'
    ):
        # Second part: insert and update triggers for wcs_all_forms
        cur.execute(
            """CREATE TRIGGER wcs_all_forms_fts_trg_ins
            AFTER INSERT ON wcs_all_forms
            FOR EACH ROW WHEN (NEW.fts IS NOT NULL)
            EXECUTE PROCEDURE wcs_search_tokens_trigger_fn('formdata_')"""
        )
        cur.execute(
            """CREATE TRIGGER wcs_all_forms_fts_trg_upd
            AFTER UPDATE OF fts ON wcs_all_forms
            FOR EACH ROW WHEN (NEW.fts IS NOT NULL)
            EXECUTE PROCEDURE wcs_search_tokens_trigger_fn('formdata_')"""
        )

    if _table_exists(cur, 'searchable_formdefs') and not _trigger_exists(
        cur, 'searchable_formdefs', 'searchable_formdefs_fts_trg_upd'
    ):
        # Third part: insert and update triggers for searchable_formdefs
        cur.execute(
            """CREATE TRIGGER searchable_formdefs_fts_trg_ins
            AFTER INSERT ON searchable_formdefs
            FOR EACH ROW WHEN (NEW.fts IS NOT NULL)
            EXECUTE PROCEDURE wcs_search_tokens_trigger_fn('formdefs')"""
        )
        cur.execute(
            """CREATE TRIGGER searchable_formdefs_fts_trg_upd
            AFTER UPDATE OF fts ON searchable_formdefs
            FOR EACH ROW WHEN (NEW.fts IS NOT NULL)
            EXECUTE PROCEDURE wcs_search_tokens_trigger_fn('formdefs')"""
        )
    for carddef in CardDef.select():
        init_search_tokens_triggers_carddef(cur, carddef)


def init_search_tokens_data(cur):
    from wcs.carddef import CardDef

    if not (_table_exists(cur, 'wcs_search_tokens')):
        # abort table data initialization if tokens table doesn't exist yet
        return

    if _table_exists(cur, 'wcs_all_forms'):
        cur.execute(
            """INSERT INTO wcs_search_tokens
            SELECT token, context FROM
                (SELECT unnest(tsvector_to_array(fts)) AS token,
                        'formdata_' || formdef_id AS context
                FROM wcs_all_forms) tokens
            WHERE token not similar to '%[0-9]{2,}%'
            ON CONFLICT(token, context) DO NOTHING"""
        )
    if _table_exists(cur, 'searchable_formdefs'):
        cur.execute(
            """INSERT INTO wcs_search_tokens
            SELECT token, context FROM
                (SELECT unnest(tsvector_to_array(fts)) AS token,
                        'formdefs' AS context
                FROM searchable_formdefs) tokens
            WHERE token not similar to '%[0-9]{2,}%'
            ON CONFLICT(token, context) DO NOTHING"""
        )
    for carddef in CardDef.select():
        data_class = carddef.data_class()
        context = '%s_%s' % (carddef.data_sql_prefix, carddef.id)
        cur.execute(
            f"""INSERT INTO wcs_search_tokens
            SELECT token, %s FROM
                (SELECT unnest(tsvector_to_array(fts)) AS token
                FROM {data_class._table_name}) tokens
            WHERE token not similar to %s
            ON CONFLICT(token, context) DO NOTHING""",
            (context, '%[0-9]{2,}%'),
        )


def purge_obsolete_search_tokens_in_context(cur, context, fts_table, itersize=1000):
    anchor = ''
    while True:
        cur.execute(
            'SELECT token FROM wcs_search_tokens WHERE token >= %s AND context = %s ORDER BY token OFFSET %s LIMIT 1',
            (anchor, context, itersize),
        )
        try:
            new_anchor = cur.fetchone()[0]
        except TypeError:
            new_anchor = ''

        sql = f"""\
DELETE FROM wcs_search_tokens AS wst WHERE
    context = %s
    AND token >= %s
    AND NOT EXISTS(SELECT id FROM {fts_table} WHERE fts @@ plainto_tsquery('simple', token))"""

        if not new_anchor:
            cur.execute(
                sql,
                (
                    context,
                    anchor,
                ),
            )
            break

        sql += 'AND token < %s'
        cur.execute(sql, (context, anchor, new_anchor))
        anchor = new_anchor


def purge_obsolete_search_tokens(cur=None, itersize=1000):
    from wcs.carddef import CardDef
    from wcs.formdef import FormDef

    own_cur = False
    if cur is None:
        own_cur = True
        _, cur = get_connection_and_cursor()

    targets = [('formdefs', 'searchable_formdefs')]

    for objectdef in itertools.chain(FormDef.select(ignore_errors=True), CardDef.select(ignore_errors=True)):
        context = '%s_%s' % (objectdef.data_sql_prefix, objectdef.id)
        data_class = objectdef.data_class()
        targets.append((context, data_class._table_name))

    # remove tokens from deleted data tables
    cur.execute(
        'DELETE FROM wcs_search_tokens WHERE context NOT IN %s', (tuple(context for context, _ in targets),)
    )
    for context, fts_table in targets:
        purge_obsolete_search_tokens_in_context(cur, context=context, fts_table=fts_table, itersize=itersize)

    if own_cur:
        cur.close()


def init_functions(cur=None):
    own_cur = False
    if cur is None:
        own_cur = True
        _, cur = get_connection_and_cursor()

    # cast_to_int(text, integer) -> integer
    cur.execute(
        r"""CREATE OR REPLACE FUNCTION cast_to_int(TEXT) RETURNS INTEGER AS $function$
BEGIN
    RETURN CAST($1 AS INTEGER);
EXCEPTION
    WHEN invalid_text_representation THEN
        RETURN NULL;
END;
$function$ LANGUAGE PLPGSQL IMMUTABLE;"""
    )

    if own_cur:
        cur.close()


def init_workflow_trace_delete_triggers(cur, formdef, table):
    if table.startswith('test_'):
        workflow_traces_table = WorkflowTrace._test_table_name
        function_name = 'wcs_remove_test_workflow_traces'
    else:
        workflow_traces_table = WorkflowTrace._table_name
        function_name = 'wcs_remove_workflow_traces'

    function_code = f'''
DECLARE
    trg_formdef_type varchar := TG_ARGV[0];
    trg_formdef_id integer := TG_ARGV[1];
BEGIN
    DELETE FROM {workflow_traces_table}_archive
    WHERE
        formdef_type = trg_formdef_type
        AND formdef_id = trg_formdef_id
        AND formdata_id = OLD.id;
    DELETE FROM {workflow_traces_table}
    WHERE
        formdef_type = trg_formdef_type
        AND formdef_id = trg_formdef_id
        AND formdata_id = OLD.id;
    RETURN OLD;
END;
'''

    cur.execute(
        'SELECT count(*) FROM pg_proc WHERE proname = %s AND prosrc = %s',
        (function_name, function_code),
    )
    if cur.fetchone()[0] != 1:
        cur.execute(
            '''
CREATE OR REPLACE FUNCTION %s () RETURNS trigger
LANGUAGE plpgsql
AS $function$%s$function$'''
            % (function_name, function_code)
        )

    trigger_prefix = table[:40]
    if not _trigger_exists(cur, table, trigger_prefix + '__workflow_traces_trg_del'):
        cur.execute(
            """CREATE TRIGGER %s__workflow_traces_trg_del
            BEFORE DELETE ON %s
            FOR EACH ROW
            EXECUTE PROCEDURE %s(%%s, %%s)"""
            % (trigger_prefix, table, function_name),
            (formdef.xml_root_node, formdef.id),
        )


class SqlMixin:
    _table_name = None
    _numerical_id = True
    _table_select_skipped_fields = []
    _has_id = True
    _sql_indexes = None
    _use_upsert = False
    _prevent_spurious_update = False

    @staticmethod
    def do_table_indexes(cur, table_name, indexes, concurrently=False):
        if not indexes:
            return
        if concurrently:
            create_index = 'CREATE INDEX CONCURRENTLY IF NOT EXISTS'
        else:
            create_index = 'CREATE INDEX IF NOT EXISTS'
        cur.execute('SELECT indexname FROM pg_indexes WHERE tablename = %s', (table_name,))
        known_indexes = [x[0] for x in cur.fetchall()]
        for index in indexes:
            if index.split()[0] not in known_indexes:
                cur.execute(f'{create_index} {index}')

    @classmethod
    def do_indexes(cls, cur, concurrently=False):
        cls.do_table_indexes(cur, cls._table_name, cls.get_sql_indexes(), concurrently=concurrently)

    @classmethod
    def get_sql_indexes(cls):
        return cls._sql_indexes or []

    @classmethod
    def parse_clause(cls, clause):
        # returns a three-elements tuple with:
        #  - a list of SQL 'WHERE' clauses
        #  - a dict for query parameters
        #  - a callable, or None if all clauses have been successfully translated

        if clause is None:
            clause = cls.get_static_criterias()
        elif callable(clause):  # already a callable
            return ([], {}, clause)
        else:
            clause = clause + cls.get_static_criterias()

        # create 'WHERE' clauses
        func_clauses = []
        where_clauses = []
        parameters = {}
        for i, element in enumerate(clause):
            if callable(element):
                func_clauses.append(element)
            else:
                sql_class = getattr(wcs.sql_criterias, element.__class__.__name__)
                if sql_class:
                    if isinstance(element, wcs.sql_criterias.Criteria):
                        # already SQL
                        sql_element = element
                    else:
                        # criteria from wcs.qommon.storage, replace it with its SQL variant
                        sql_element = sql_class(**element.__dict__)
                        clause[i] = sql_element
                    where_clauses.append(sql_element.as_sql())
                    parameters.update(sql_element.as_sql_param())
                else:
                    func_clauses.append(element.build_lambda())

        if func_clauses:
            return (where_clauses, parameters, parse_storage_clause(func_clauses))

        return (where_clauses, parameters, None)

    @classmethod
    def keys(cls, clause=None):
        _, cur = get_connection_and_cursor()
        where_clauses, parameters, func_clause = cls.parse_clause(clause)
        assert not func_clause
        sql_statement = 'SELECT id FROM %s' % cls._table_name
        if where_clauses:
            sql_statement += ' WHERE ' + ' AND '.join(where_clauses)
        cur.execute(sql_statement, parameters)
        ids = [x[0] for x in cur.fetchall()]
        cur.close()
        return ids

    @classmethod
    def count(cls, clause=None):
        where_clauses, parameters, func_clause = cls.parse_clause(clause)
        if func_clause:
            # fallback to counting the result of a select()
            return len(cls.select(clause))
        sql_statement = 'SELECT count(*) FROM %s' % cls._table_name
        if where_clauses:
            sql_statement += ' WHERE ' + ' AND '.join(where_clauses)
        _, cur = get_connection_and_cursor()
        cur.execute(sql_statement, parameters)
        count = cur.fetchone()[0]
        cur.close()
        return count

    @classmethod
    def exists(cls, clause=None):
        where_clauses, parameters, func_clause = cls.parse_clause(clause)
        if func_clause:
            # fallback to counting the result of a select()
            return len(cls.select(clause))
        sql_statement = 'SELECT 1 FROM %s' % cls._table_name
        if where_clauses:
            sql_statement += ' WHERE ' + ' AND '.join(where_clauses)
        sql_statement += ' LIMIT 1'
        _, cur = get_connection_and_cursor()
        try:
            cur.execute(sql_statement, parameters)
        except UndefinedTable:
            result = False
        else:
            check = cur.fetchone()
            result = check is not None
        cur.close()
        return result

    @classmethod
    def get_ids_from_query(cls, query):
        _, cur = get_connection_and_cursor()

        sql_statement = (
            '''SELECT id FROM %s
                            WHERE fts @@ plainto_tsquery(%%(value)s)'''
            % cls._table_name
        )
        cur.execute(sql_statement, {'value': ExtendedFtsMatch.get_fts_value(query)})
        all_ids = [x[0] for x in cur.fetchall()]
        cur.close()
        return all_ids

    @classmethod
    def get_static_criterias(cls):
        return []

    @classmethod
    def get(cls, id, ignore_errors=False, ignore_migration=False, column=None):
        if column is None and (cls._numerical_id or id is None):
            try:
                if not (0 < int(str(id)) < 2**31) or not is_ascii_digit(str(id)):
                    # avoid NumericValueOutOfRange and _ in digits
                    raise TypeError()
            except (TypeError, ValueError):
                if ignore_errors and (cls._numerical_id or id is None):
                    return None
                raise KeyError()
        _, cur = get_connection_and_cursor()

        where_clauses, parameters, dummy = cls.parse_clause(None)
        sql_statement = '''SELECT %s
                             FROM %s
                            WHERE %s = %%(value)s''' % (
            ', '.join([x[0] for x in cls._table_static_fields] + cls.get_sql_data_fields()),
            cls._table_name,
            column or 'id',
        )
        if where_clauses:
            sql_statement += ' AND ' + ' AND '.join(where_clauses)
        parameters['value'] = str(id)
        cur.execute(sql_statement, parameters)
        row = cur.fetchone()
        if row is None:
            cur.close()
            if ignore_errors:
                return None
            raise KeyError()
        cur.close()
        ob = cls._row2ob(row)
        if not ignore_migration and hasattr(cls, 'migrate'):
            ob.migrate()
        return ob

    @classmethod
    def get_on_index(cls, value, index, ignore_errors=False, use_cache=False, **kwargs):
        if use_cache:
            sql_statement = f'SELECT id FROM {cls._table_name} WHERE {index} = %(value)s LIMIT 1'
            _, cur = get_connection_and_cursor()
            cur.execute(sql_statement, {'value': value})
            row = cur.fetchone()
            cur.close()
            if row is not None:
                return cls.cached_get(row[0], ignore_errors=ignore_errors, **kwargs)
            if ignore_errors:
                return None
            raise KeyError(value)
        ob = cls.get(value, ignore_errors=ignore_errors, column=index, **kwargs)
        return ob

    @classmethod
    def get_ids(cls, ids, ignore_errors=False, keep_order=False, fields=None, order_by=None):
        if not ids:
            return []
        tables = [cls._table_name]
        columns = [
            '%s.%s' % (cls._table_name, column_name)
            for column_name in [x[0] for x in cls._table_static_fields] + cls.get_sql_data_fields()
        ]
        extra_fields = []
        if fields:
            # look for relations
            for field in fields:
                if not getattr(field, 'is_related_field', False):
                    continue
                if field.parent_field_id == 'user-label':
                    # relation to user table
                    carddef_table_alias = 'users'
                    carddef_table_decl = (
                        'LEFT JOIN users ON (CAST(%s.user_id AS INTEGER) = users.id)' % cls._table_name
                    )
                else:
                    carddef_data_table_name = get_formdef_table_name(field.carddef)
                    carddef_table_alias = 't%s' % id(field.carddef)
                    if field.carddef.id_template:
                        carddef_table_decl = 'LEFT JOIN %s AS %s ON (%s.%s = %s.id_display)' % (
                            carddef_data_table_name,
                            carddef_table_alias,
                            cls._table_name,
                            get_field_id(field.parent_field),
                            carddef_table_alias,
                        )
                    else:
                        carddef_table_decl = 'LEFT JOIN %s AS %s ON (cast_to_int(%s.%s) = %s.id)' % (
                            carddef_data_table_name,
                            carddef_table_alias,
                            cls._table_name,
                            get_field_id(field.parent_field),
                            carddef_table_alias,
                        )

                if carddef_table_decl not in tables:
                    tables.append(carddef_table_decl)

                column_field_id = field.get_column_field_id()
                columns.append('%s.%s' % (carddef_table_alias, column_field_id))
                if field.store_display_value:
                    columns.append('%s.%s_display' % (carddef_table_alias, column_field_id))
                if field.store_structured_value:
                    columns.append('%s.%s_structured' % (carddef_table_alias, column_field_id))
                extra_fields.append(field)

        _, cur = get_connection_and_cursor()
        if cls._numerical_id:
            ids_str = ', '.join([str(x) for x in ids])
        else:
            ids_str = ', '.join(["'%s'" % x for x in ids])
        sql_statement = '''SELECT %s
                             FROM %s
                            WHERE %s.id IN (%s)''' % (
            ', '.join(columns),
            ' '.join(tables),
            cls._table_name,
            ids_str,
        )
        sql_statement += cls.get_order_by_clause(order_by)
        cur.execute(sql_statement)
        objects = cls.get_objects(cur, extra_fields=extra_fields)
        cur.close()
        if ignore_errors:
            objects = (x for x in objects if x is not None)
        if keep_order:
            objects_dict = {}
            for object in objects:
                objects_dict[object.id] = object
            objects = [objects_dict[x] for x in ids if objects_dict.get(x)]
        return list(objects)

    @classmethod
    def get_ids_iterator(cls, ids, ignore_errors=False, keep_order=False, fields=None, itersize=None):
        if not ids:
            return []
        itersize = max(itersize or 0, 200)
        i = 0
        while ids[i : i + itersize]:
            yield from cls.get_ids(
                ids[i : i + itersize],
                ignore_errors=ignore_errors,
                keep_order=keep_order,
                fields=fields,
                order_by=None,  # order is computed before
            )
            i += itersize

    @classmethod
    def get_objects_iterator(cls, cur, ignore_errors=False, extra_fields=None):
        while True:
            row = cur.fetchone()
            if row is None:
                break
            yield cls._row2ob(row, extra_fields=extra_fields)

    @classmethod
    def get_objects(cls, cur, ignore_errors=False, iterator=False, extra_fields=None):
        generator = cls.get_objects_iterator(cur=cur, ignore_errors=ignore_errors, extra_fields=extra_fields)
        if iterator:
            return generator
        return list(generator)

    @classmethod
    def get_order_by_clause(cls, order_by):
        if not order_by:
            return ''

        def _get_order_by_part(part):
            # [SEC_ORDER] security note: it is not possible to use
            # prepared statements for ORDER BY clauses, therefore input
            # is controlled beforehand (see misc.get_order_by_or_400).
            direction = 'ASC'
            if part.startswith('-'):
                part = part[1:]
                direction = 'DESC'
            if '->' in part:
                # sort on field of block field: f42->'data'->0->>'bf13e4d8a8-fb08-4808-b5ae-02d6247949b9'
                # or on digest (digests->>'default'); make sure all parts have their
                # dashes changed to underscores
                parts = part.split('->')
                part = '%s->%s' % (parts[0].replace('-', '_'), '->'.join(parts[1:]))
            else:
                part = part.replace('-', '_')

            fields = ['formdef_name', 'user_name']  # global view fields
            fields.extend([x[0] for x in cls._table_static_fields])
            fields.extend(cls.get_sql_data_fields())
            if part.split('->')[0] not in fields:
                # for a sort on field of block field, just check the existence of the block field
                return None, None
            return part, direction

        if not isinstance(order_by, list):
            order_by = [order_by]

        ordering = []
        for part in order_by:
            order, direction = _get_order_by_part(part)
            if order is None:
                continue
            ordering.append(f'{order} {direction}')

        if not ordering:
            return ''

        return ' ORDER BY %s' % ', '.join(ordering)

    @classmethod
    def has_key(cls, id):
        _, cur = get_connection_and_cursor()
        sql_statement = 'SELECT EXISTS(SELECT 1 FROM %s WHERE id = %%s)' % cls._table_name
        with cur:
            cur.execute(sql_statement, (id,))
            result = cur.fetchall()[0][0]
        return result

    @classmethod
    def select_iterator(
        cls,
        clause=None,
        order_by=None,
        ignore_errors=False,
        limit=None,
        offset=None,
        itersize=None,
    ):
        table_static_fields = [
            x[0] if x[0] not in cls._table_select_skipped_fields else 'NULL AS %s' % x[0]
            for x in cls._table_static_fields
        ]

        def retrieve():
            for object in cls.get_objects(cur, iterator=True):
                if object is None:
                    continue
                if func_clause and not func_clause(object):
                    continue
                yield object

        if itersize and cls._has_id:
            # this case concerns almost all data tables: formdata, card, users, roles
            sql_statement = '''SELECT id FROM %s''' % cls._table_name
        else:
            # this case concerns aggregated views like wcs_all_forms (class
            # AnyFormData) which does not have a surrogate key id column
            sql_statement = '''SELECT %s FROM %s''' % (
                ', '.join(table_static_fields + cls.get_sql_data_fields()),
                cls._table_name,
            )
        where_clauses, parameters, func_clause = cls.parse_clause(clause)
        if where_clauses:
            sql_statement += ' WHERE ' + ' AND '.join(where_clauses)

        sql_statement += cls.get_order_by_clause(order_by)

        if not func_clause:
            if limit:
                sql_statement += ' LIMIT %(limit)s'
                parameters['limit'] = limit
            if offset:
                sql_statement += ' OFFSET %(offset)s'
                parameters['offset'] = offset

        _, cur = get_connection_and_cursor()
        with cur:
            cur.execute(sql_statement, parameters)
            if itersize and cls._has_id:
                sql_id_statement = '''SELECT %s FROM %s WHERE ''' % (
                    ', '.join(table_static_fields + cls.get_sql_data_fields()),
                    cls._table_name,
                )
                sql_id_statement += ' AND '.join(['id IN %(ids)s'] + (where_clauses or []))
                sql_id_statement += cls.get_order_by_clause(order_by)
                ids = [row[0] for row in cur]
                while ids:
                    parameters['ids'] = tuple(ids[:itersize])
                    cur.execute(sql_id_statement, parameters)
                    yield from retrieve()
                    ids = ids[itersize:]
            else:
                yield from retrieve()

    @classmethod
    def select(
        cls,
        clause=None,
        order_by=None,
        ignore_errors=False,
        limit=None,
        offset=None,
        iterator=False,
        itersize=None,
        ignore_migration=True,  # always ignore migrations
    ):
        if iterator and not itersize:
            itersize = 200
        objects = cls.select_iterator(
            clause=clause,
            order_by=order_by,
            ignore_errors=ignore_errors,
            limit=limit,
            offset=offset,
            itersize=itersize,
        )
        func_clause = cls.parse_clause(clause)[2]
        if func_clause and (limit or offset):
            objects = _take(objects, limit, offset)
        if iterator:
            return objects
        return list(objects)

    @classmethod
    def select_distinct(cls, columns, clause=None, first_field_alias=None):
        # do note this method returns unicode strings.
        column0 = columns[0]
        if first_field_alias:
            column0 = '%s as %s' % (column0, first_field_alias)
        _, cur = get_connection_and_cursor()
        sql_statement = 'SELECT DISTINCT ON (%s) %s FROM %s' % (
            columns[0],
            ', '.join([column0] + columns[1:]),
            cls._table_name,
        )
        where_clauses, parameters, func_clause = cls.parse_clause(clause)
        assert not func_clause
        if where_clauses:
            sql_statement += ' WHERE ' + ' AND '.join(where_clauses)
        sql_statement += ' ORDER BY %s' % (first_field_alias or columns[0])
        cur.execute(sql_statement, parameters)
        values = [x for x in cur.fetchall()]
        cur.close()
        return values

    def get_sql_dict_from_data(self, data, formdef):
        sql_dict = {}
        for field in formdef.get_all_fields():
            sql_type = SQL_TYPE_MAPPING.get(field.key, 'varchar')
            if sql_type is None:
                continue
            value = data.get(field.id)
            if value is not None:
                if field.key in ('ranked-items', 'password'):
                    # turn {'poire': 2, 'abricot': 1, 'pomme': 3} into an array
                    value = [[force_str(x), force_str(y)] for x, y in value.items()]
                elif field.key == 'computed':
                    if value is not None:
                        # embed value in a dict, so it's never necessary to cast the
                        # value for postgresql
                        value = {'data': json.loads(JSONEncoder().encode(value)), '@type': 'computed-data'}
                elif sql_type == 'varchar':
                    assert isinstance(value, str)
                elif sql_type == 'date':
                    assert isinstance(value, time.struct_time)
                    value = datetime.datetime(value.tm_year, value.tm_mon, value.tm_mday)
                elif sql_type == 'bytea':
                    value = bytearray(pickle.dumps(value, protocol=2))
                elif sql_type == 'jsonb' and isinstance(value, dict) and value.get('schema'):
                    # block field, adapt date/field values
                    value = copy.deepcopy(value)
                    for field_id, field_type in value.get('schema').items():
                        if field_type not in ('date', 'file', 'numeric'):
                            continue
                        for entry in value.get('data') or []:
                            subvalue = entry.get(field_id)
                            if subvalue and field_type == 'date':
                                entry[field_id] = strftime('%Y-%m-%d', subvalue)
                            elif subvalue and field_type == 'file':
                                entry[field_id] = subvalue.__getstate__()
                            elif subvalue is not None and field_type == 'numeric':
                                entry[field_id] = str(subvalue)
                elif sql_type == 'boolean':
                    pass
            sql_dict[get_field_id(field)] = value
            if field.store_display_value:
                sql_dict['%s_display' % get_field_id(field)] = data.get('%s_display' % field.id)
            if field.store_structured_value:
                sql_dict['%s_structured' % get_field_id(field)] = bytearray(
                    pickle.dumps(data.get('%s_structured' % field.id), protocol=2)
                )
        return sql_dict

    @classmethod
    def _col2obdata(cls, row, i, field):
        obdata = {}
        field_key = field.key
        if field_key == 'related-field':
            field_key = field.related_field.key
        sql_type = SQL_TYPE_MAPPING.get(field_key, 'varchar')
        if sql_type is None:
            return ({}, i)
        value = row[i]
        if value is not None:
            if field.key == 'ranked-items':
                d = {}
                for data, rank in value:
                    try:
                        d[data] = int(rank)
                    except ValueError:
                        d[data] = rank
                value = d
            elif field.key == 'password':
                d = {}
                for fmt, val in value:
                    d[fmt] = force_str(val)
                value = d
            elif field.key == 'computed':
                if not isinstance(value, dict):
                    raise ValueError(
                        'bad data %s (type %s) in computed field %s' % (value, type(value), field.id)
                    )
                if value.get('@type') == 'computed-data':
                    value = value.get('data')
            if sql_type == 'date':
                value = value.timetuple()
            elif sql_type == 'bytea':
                value = pickle_loads(value)
            elif sql_type == 'jsonb' and isinstance(value, dict) and value.get('schema'):
                # block field, adapt some types
                for field_id, field_type in value.get('schema').items():
                    if field_type not in ('date', 'file', 'numeric', 'map'):
                        continue
                    for entry in value.get('data') or []:
                        subvalue = entry.get(field_id)
                        if subvalue and field_type == 'date':
                            entry[field_id] = time.strptime(subvalue, '%Y-%m-%d')
                        elif subvalue and field_type == 'file':
                            entry[field_id] = PicklableUpload.__new__(PicklableUpload)
                            entry[field_id].__setstate__(subvalue)
                        elif subvalue and field_type == 'numeric':
                            entry[field_id] = decimal.Decimal(subvalue)
                        elif subvalue and field_type == 'map' and isinstance(subvalue, str):
                            # legacy storage of map data
                            lat, lon = subvalue.split(';')
                            entry[field_id] = {'lat': lat, 'lon': lon}

        obdata[field.id] = value
        i += 1
        if field.store_display_value:
            value = row[i]
            obdata['%s_display' % field.id] = value
            i += 1
        if field.store_structured_value:
            value = row[i]
            if value is not None:
                obdata['%s_structured' % field.id] = pickle_loads(value)
                if obdata['%s_structured' % field.id] is None:
                    del obdata['%s_structured' % field.id]
            i += 1
        return (obdata, i)

    @classmethod
    def _row2obdata(cls, row, formdef):
        obdata = {}
        i = len(cls._table_static_fields)
        if formdef.geolocations:
            i += len(formdef.geolocations.keys())
        for field in formdef.get_all_fields():
            coldata, i = cls._col2obdata(row, i, field)
            obdata.update(coldata)
        return obdata

    @classmethod
    def remove_object(cls, id):
        _, cur = get_connection_and_cursor()
        sql_statement = (
            '''DELETE FROM %s
                              WHERE id = %%(id)s'''
            % cls._table_name
        )
        cur.execute(sql_statement, {'id': str(id)})
        cur.close()

    @classonlymethod
    def wipe(cls, drop=False, clause=None, restart_sequence=False):
        _, cur = get_connection_and_cursor()
        sql_statement = '''DELETE FROM %s''' % cls._table_name
        parameters = {}
        where_clauses, parameters, dummy = cls.parse_clause(clause)
        if where_clauses:
            sql_statement += ' WHERE ' + ' AND '.join(where_clauses)

        cur.execute(sql_statement, parameters)

        if restart_sequence:
            # find out if there are sequences, and restart them if needed
            cur.execute(
                '''SELECT s.relname AS sequence_name
                            FROM pg_class t
                            JOIN pg_depend d ON t.oid = d.refobjid
                            JOIN pg_class s ON d.objid = s.oid and s.relkind = 'S'
                            WHERE t.relkind IN ('p', 'r') AND t.relname = %(tablename)s;''',
                {'tablename': cls._table_name},
            )
            for (sequence_name,) in cur.fetchall():
                cur.execute('ALTER SEQUENCE %s RESTART' % sequence_name)
        if not Atomic.transaction_in_progress():
            cur.execute('VACUUM %s' % cls._table_name)
        cur.close()

    @classmethod
    def reset_restart_sequence(cls):
        _, cur = get_connection_and_cursor()
        cur.execute(f'SELECT MAX(id) FROM {cls._table_name}')
        row = cur.fetchone()
        highest_id = (row[0] or 0) + 1
        cur.execute(f'ALTER SEQUENCE {cls._table_name}_id_seq RESTART WITH {highest_id}')
        cur.close()

    @classmethod
    def get_sorted_ids(cls, order_by, clause=None, offset=None, limit=None):
        _, cur = get_connection_and_cursor()
        sql_statement = 'SELECT id FROM %s' % cls._table_name
        where_clauses, parameters, func_clause = cls.parse_clause(clause)
        assert not func_clause
        if where_clauses:
            sql_statement += ' WHERE ' + ' AND '.join(where_clauses)
        if order_by == 'rank':
            try:
                fts = [
                    x
                    for x in clause
                    if not callable(x) and x.__class__.__name__ in ('ExtendedFtsMatch', 'FtsMatch')
                ][0]
            except IndexError:
                pass
            else:
                sql_statement += ' ORDER BY %s DESC' % fts.rank_sql()
        else:
            sql_statement += cls.get_order_by_clause(order_by)
        if limit is not None:
            sql_statement += ' LIMIT %(limit)s'
            parameters['limit'] = limit
        if offset is not None:
            sql_statement += ' OFFSET %(offset)s'
            parameters['offset'] = offset
        cur.execute(sql_statement, parameters)
        ids = [x[0] for x in cur.fetchall()]
        cur.close()
        return ids

    @classmethod
    def get_sql_data_fields(cls):
        return []

    @classmethod
    def _row2ob(cls, row, **kwargs):
        o = cls.__new__(cls)
        for attr, value in zip([x[0] for x in cls._table_static_fields], row):
            setattr(o, attr, value)
        return o

    def get_sql_dict(self):
        return {x[0]: getattr(self, x[0], None) for x in self._table_static_fields if x[0] != 'id'}

    def store(self):
        sql_dict = self.get_sql_dict()

        _, cur = get_connection_and_cursor()
        column_names = list(sql_dict.keys())
        if not self.id:
            sql_statement = '''INSERT INTO %s (id, %s)
                               VALUES (DEFAULT, %s)
                               RETURNING id''' % (
                self._table_name,
                ', '.join(column_names),
                ', '.join(['%%(%s)s' % x for x in column_names]),
            )
            cur.execute(sql_statement, sql_dict)
            self.id = cur.fetchone()[0]
        elif self._use_upsert:
            sql_dict['id'] = self.id
            column_names = list(sql_dict.keys())
            sql_statement = '''INSERT INTO %s (%s) VALUES (%s) ON CONFLICT(id) DO UPDATE SET %s''' % (
                self._table_name,
                ', '.join(column_names),
                ', '.join(['%%(%s)s' % x for x in column_names]),
                ', '.join(['%s = excluded.%s' % (x, x) for x in column_names]),
            )
            cur.execute(sql_statement, sql_dict)
        elif self._prevent_spurious_update:
            sql_dict['id'] = self.id
            fields_to_update = {
                (name, column_type) for (name, column_type) in self._table_static_fields if name != 'id'
            }
            sql_statement = '''UPDATE %s SET %s WHERE id = %%(id)s AND (%s) IS DISTINCT FROM (%s)''' % (
                self._table_name,
                ', '.join(['%s = %%(%s)s' % (x, x) for x in column_names]),
                ', '.join(['%s::%s' % (name, column_type) for (name, column_type) in fields_to_update]),
                ', '.join(['%%(%s)s::%s' % (name, column_type) for (name, column_type) in fields_to_update]),
            )
            cur.execute(sql_statement, sql_dict)

        else:
            sql_dict['id'] = self.id
            sql_statement = '''UPDATE %s SET %s WHERE id = %%(id)s RETURNING id''' % (
                self._table_name,
                ', '.join(['%s = %%(%s)s' % (x, x) for x in column_names]),
            )
            cur.execute(sql_statement, sql_dict)

        cur.close()

    def __repr__(self):
        return '<%s id:%s>' % (self.__class__.__name__, self.id)


class SqlDataMixin(SqlMixin):
    _names = None  # make sure StorableObject methods fail
    _formdef = None

    _table_static_fields = [
        ('id', 'serial'),
        ('user_id', 'varchar'),
        ('receipt_time', 'timestamptz'),
        ('status', 'varchar'),
        ('page_no', 'varchar'),
        ('page_id', 'varchar'),
        ('anonymised', 'timestamptz'),
        ('workflow_data', 'bytea'),
        ('prefilling_data', 'bytea'),
        ('id_display', 'varchar'),
        ('workflow_roles', 'bytea'),
        # workflow_merged_roles_dict combines workflow_roles from formdef and
        # formdata and is used to filter on function assignment.
        ('workflow_merged_roles_dict', 'jsonb'),
        # workflow_roles_array is created from workflow_roles to be used in
        # get_ids_with_indexed_value
        ('workflow_roles_array', 'text[]'),
        ('concerned_roles_array', 'text[]'),
        ('actions_roles_array', 'text[]'),
        ('tracking_code', 'varchar'),
        ('backoffice_submission', 'boolean'),
        ('submission_context', 'bytea'),
        ('submission_agent_id', 'varchar'),
        ('submission_channel', 'varchar'),
        ('criticality_level', 'int'),
        ('last_update_time', 'timestamptz'),
        ('digests', 'jsonb'),
        ('user_label', 'varchar'),
        ('auto_geoloc', 'point'),
        ('statistics_data', 'jsonb'),
        ('relations_data', 'jsonb'),
        ('test_result_id', 'integer'),
        ('workflow_processing_timestamp', 'timestamptz'),
        ('workflow_processing_afterjob_id', 'varchar'),
    ]

    def __init__(self, id=None):
        self.id = id
        self.data = {}
        self._has_changed_digest = False

    _evolution = None

    def get_evolution(self):
        if self._evolution is not None:
            return self._evolution
        if not self.id:
            self._evolution = []
            return self._evolution
        _, cur = get_connection_and_cursor()
        sql_statement = (
            '''SELECT id, who, status, time, last_jump_datetime,
                                  comment, parts FROM %s_evolutions
                            WHERE formdata_id = %%(id)s
                         ORDER BY id'''
            % self._table_name
        )
        cur.execute(sql_statement, {'id': self.id})
        self._evolution = []
        while True:
            row = cur.fetchone()
            if row is None:
                break
            self._evolution.append(self._row2evo(row, formdata=self))
        cur.close()
        return self._evolution

    @classmethod
    def _row2evo(cls, row, formdata):
        o = wcs.formdata.Evolution(formdata)
        o._sql_id, o.who, o.status, o.time, o.last_jump_datetime, o.comment = (x for x in tuple(row[:6]))
        if row[6]:
            o.parts = LazyEvolutionList(row[6])
        return o

    def set_evolution(self, value):
        self._evolution = value

    evolution = property(get_evolution, set_evolution)

    @classmethod
    def load_all_evolutions(cls, values, include_parts=True):
        # Typically formdata.evolution is loaded on-demand (see above
        # property()) and this is fine to minimize queries, especially when
        # dealing with a single formdata.  However in some places (to compute
        # statistics for example) it is sometimes useful to access .evolution
        # on a serie of formdata and in that case, it's more efficient to
        # optimize the process loading all evolutions in a single batch query.
        object_dict = {x.id: x for x in values if x.id and x._evolution is None}
        if not object_dict:
            return
        _, cur = get_connection_and_cursor()
        parts_sql = 'parts' if include_parts else 'NULL'
        sql_statement = '''SELECT id, who, status, time, last_jump_datetime,
                                  comment, %s, formdata_id
                             FROM %s_evolutions''' % (
            parts_sql,
            cls._table_name,
        )
        sql_statement += ''' WHERE formdata_id IN %(object_ids)s ORDER BY id'''
        cur.execute(sql_statement, {'object_ids': tuple(object_dict.keys())})

        for value in values:
            value._evolution = []

        while True:
            row = cur.fetchone()
            if row is None:
                break
            formdata_id = tuple(row[:8])[7]
            formdata = object_dict.get(formdata_id)
            if not formdata:
                continue
            formdata._evolution.append(formdata._row2evo(row, formdata))

        cur.close()

    @classmethod
    def chunked(cls, iterator, itersize):
        iterator = iter(iterator)
        chunk = list(itertools.islice(iterator, itersize))
        while chunk:
            yield chunk
            chunk = list(itertools.islice(iterator, itersize))

    @classmethod
    def prefetch_evolutions(cls, iterator, itersize=200, include_parts=True):
        for items in cls.chunked(iterator, itersize):
            cls.load_all_evolutions(items, include_parts=include_parts)
            yield from items

    @classmethod
    def prefetch_users(cls, iterator, itersize=200):
        prefetched_users = {}

        def gen():
            for items in cls.chunked(iterator, itersize):
                user_ids = set(
                    [str(x.user_id) for x in items if x.user_id]
                    + [str(x.submission_agent_id) for x in items if x.submission_agent_id]
                )
                for item in items:
                    for evo in item.evolution or []:
                        if not evo.who or evo.who.startswith('_'):
                            continue
                        user_ids.add(str(evo.who))
                user_ids = [
                    user_id for user_id in user_ids if user_id not in prefetched_users if user_id is not None
                ]
                prefetched_users.update(
                    (str(x.id), x)
                    for x in get_publisher().user_class.get_ids(user_ids, ignore_errors=True)
                    if x is not None
                )
                yield from items

        return gen(), prefetched_users

    @classmethod
    def prefetch_roles(cls, iterator, itersize=200):
        prefetched_roles = {}

        def update_prefetched_roles(role_ids):
            role_ids = set(map(str, role_ids))
            role_ids = [role_id for role_id in role_ids if role_id not in prefetched_roles]
            prefetched_roles.update(
                (str(x.id), x)
                for x in get_publisher().role_class.get_ids(role_ids, ignore_errors=True)
                if x is not None
            )

        update_prefetched_roles((cls._formdef.workflow_roles or {}).values())

        def gen():
            for items in cls.chunked(iterator, itersize):
                role_ids = set()
                for formdata in items:
                    if formdata.workflow_roles:
                        for value in formdata.workflow_roles.values():
                            if isinstance(value, list):
                                role_ids.update(value)
                            else:
                                role_ids.add(value)
                update_prefetched_roles(role_ids)
                yield from items

        return gen(), prefetched_roles

    @classmethod
    def get_resolution_times(
        cls,
        start_status,
        end_statuses,
        period_start=None,
        period_end=None,
        group_by=None,
        criterias=None,
        prefix_criterias=True,
    ):
        criterias = criterias or []
        if prefix_criterias:
            for criteria in criterias:
                criteria.attribute = 'f.%s' % criteria.attribute

        if period_start:
            criterias.append(GreaterOrEqual('f.receipt_time', period_start))
        if period_end:
            criterias.append(Less('f.receipt_time', period_end))

        where_clauses, params, dummy = cls.parse_clause(criterias)

        params.update(
            {
                'start_status': start_status,
                'end_statuses': tuple(end_statuses),
            }
        )

        table_name = cls._table_name
        group_by_column = group_by or 'NULL'
        sql_statement = f'''
            SELECT
            f.id,
            MIN(end_evo.time) - MIN(start_evo.time) as res_time,
            {group_by_column}
            FROM {table_name} f
            JOIN {table_name}_evolutions start_evo ON start_evo.formdata_id = f.id AND start_evo.status = %(start_status)s
            JOIN {table_name}_evolutions end_evo ON end_evo.formdata_id = f.id AND end_evo.status IN %(end_statuses)s
            WHERE {' AND '.join(where_clauses)}
            GROUP BY f.id
            ORDER BY res_time
            '''

        _, cur = get_connection_and_cursor()
        with cur:
            cur.execute(sql_statement, params)
            results = cur.fetchall()

        # row[1] will have the resolution time as computed by postgresql
        return [(row[1].total_seconds(), row[2]) for row in results if row[1].total_seconds() >= 0]

    def _set_auto_fields(self, cur):
        changed_auto_fields = self.set_auto_fields()
        if changed_auto_fields:
            self._has_changed_digest = bool('digests' in changed_auto_fields)
            sql_statement = (
                '''UPDATE %s
                                  SET id_display = %%(id_display)s,
                                      digests = %%(digests)s,
                                      user_label = %%(user_label)s,
                                      statistics_data = %%(statistics_data)s,
                                      relations_data = %%(relations_data)s
                                WHERE id = %%(id)s'''
                % self._table_name
            )
            cur.execute(
                sql_statement,
                {
                    'id': self.id,
                    'id_display': self.id_display,
                    'digests': self.digests,
                    'user_label': self.user_label,
                    'statistics_data': self.statistics_data,
                    'relations_data': self.relations_data,
                },
            )

    def update_column(self, column):
        sql_statement = f'UPDATE {self._table_name} SET {column} = %(value)s WHERE id = %(id)s'
        _, cur = get_connection_and_cursor()
        with cur:
            cur.execute(sql_statement, {'id': self.id, 'value': getattr(self, column)})

    def store_last_jump(self):
        evo = self._evolution[-1]
        if not hasattr(evo, '_sql_id'):
            return self.store()
        _, cur = get_connection_and_cursor()
        cur.execute(
            f'''UPDATE {self._table_name}_evolutions
                           SET last_jump_datetime = %s
                         WHERE id = %s''',
            (evo.last_jump_datetime, evo._sql_id),
        )
        cur.execute(
            f'''UPDATE {self._table_name}
                           SET last_update_time = %s
                         WHERE id = %s''',
            (evo.last_jump_datetime, self.id),
        )
        cur.close()

    @invalidate_substitution_cache
    @atomic
    def store(self, where=None):
        if self.uuid is None:
            self.uuid = str(uuid.uuid4())

        sql_dict = {
            'uuid': self.uuid,
            'user_id': self.user_id,
            'status': self.status,
            'page_no': self.page_no,
            'workflow_data': self.workflow_data,
            'id_display': self.id_display,
            'anonymised': self.anonymised,
            'tracking_code': self.tracking_code,
            'backoffice_submission': self.backoffice_submission,
            'submission_context': self.submission_context,
            'prefilling_data': self.prefilling_data,
            'submission_agent_id': self.submission_agent_id,
            'submission_channel': self.submission_channel,
            'criticality_level': self.criticality_level,
            'workflow_merged_roles_dict': self.workflow_merged_roles_dict,
            'statistics_data': self.statistics_data or {},
            'relations_data': self.relations_data or {},
            'test_result_id': self.test_result_id,
            'workflow_processing_timestamp': self.workflow_processing_timestamp,
            'workflow_processing_afterjob_id': self.workflow_processing_afterjob_id,
        }
        if self._evolution is not None and hasattr(self, '_last_update_time'):
            # if evolution was loaded it may have been been modified, and last update time
            # should then be refreshed.
            delattr(self, '_last_update_time')
        sql_dict['last_update_time'] = self.last_update_time
        sql_dict['receipt_time'] = self.receipt_time
        if self.workflow_roles:
            sql_dict['workflow_roles_array'] = []
            for x in self.workflow_roles.values():
                if isinstance(x, list):
                    sql_dict['workflow_roles_array'].extend(x)
                elif x:
                    sql_dict['workflow_roles_array'].append(str(x))
        else:
            sql_dict['workflow_roles_array'] = None
        if hasattr(self, 'page_id'):
            sql_dict['page_id'] = self.page_id
        for attr in ('workflow_data', 'workflow_roles', 'submission_context', 'prefilling_data'):
            if getattr(self, attr):
                sql_dict[attr] = bytearray(pickle.dumps(getattr(self, attr), protocol=2))
            else:
                sql_dict[attr] = None

        for field in (self._formdef.geolocations or {}).keys():
            value = (self.geolocations or {}).get(field)
            if value:
                value = '(%.6f, %.6f)' % (value.get('lon'), value.get('lat'))
            sql_dict['geoloc_%s' % field] = value

        sql_dict['concerned_roles_array'] = [str(x) for x in self.concerned_roles if x]
        sql_dict['actions_roles_array'] = [str(x) for x in self.actions_roles if x]
        auto_geoloc_value = self.get_auto_geoloc()
        if auto_geoloc_value:
            auto_geoloc_value = '(%.6f, %.6f)' % (auto_geoloc_value.get('lon'), auto_geoloc_value.get('lat'))
        sql_dict['auto_geoloc'] = auto_geoloc_value

        sql_dict.update(self.get_sql_dict_from_data(self.data, self._formdef))
        _, cur = get_connection_and_cursor()
        if not self.id:
            column_names = sql_dict.keys()
            sql_statement = '''INSERT INTO %s (id, %s)
                               VALUES (DEFAULT, %s)
                               RETURNING id''' % (
                self._table_name,
                ', '.join(column_names),
                ', '.join(['%%(%s)s' % x for x in column_names]),
            )
            cur.execute(sql_statement, sql_dict)
            self.id = cur.fetchone()[0]
        else:
            if not where:
                where = []
            where.append(Equal('id', self.id))
            where_clauses, parameters, dummy = SqlMixin.parse_clause(where)
            column_names = list(sql_dict.keys())
            sql_dict.update(parameters)
            sql_statement = '''UPDATE %s SET %s WHERE %s RETURNING id''' % (
                self._table_name,
                ', '.join(['%s = %%(%s)s' % (x, x) for x in column_names]),
                ' AND '.join(where_clauses),
            )
            cur.execute(sql_statement, sql_dict)
            if cur.fetchone() is None:
                if len(where) > 1:
                    # abort if nothing was modified and there were extra where clauses
                    raise NothingToUpdate()
                # this has been a request to save a new line with a preset id (for example
                # for data migration)
                sql_dict['id'] = self.id
                column_names.append('id')
                sql_statement = '''INSERT INTO %s (%s) VALUES (%s) RETURNING id''' % (
                    self._table_name,
                    ', '.join(column_names),
                    ', '.join(['%%(%s)s' % x for x in column_names]),
                )
                cur.execute(sql_statement, sql_dict)
                self.id = cur.fetchone()[0]

        self._set_auto_fields(cur)
        self.clean_live_evolution_items()

        if self._evolution:
            idx = 0
            if not getattr(self, '_store_all_evolution', False):
                # skip all the evolution that already have an _sql_id
                # it's still possible for debugging purpose and special needs
                # to store them all using formdata._store_all_evolution = True
                for idx, evo in enumerate(self._evolution):
                    if not hasattr(evo, '_sql_id'):
                        break
            # now we can save all after this idx
            for evo in self._evolution[idx:]:
                sql_dict = {}
                if hasattr(evo, '_sql_id'):
                    sql_dict.update({'id': evo._sql_id})
                    sql_statement = (
                        '''UPDATE %s_evolutions SET
                                        who = %%(who)s,
                                        time = %%(time)s,
                                        last_jump_datetime = %%(last_jump_datetime)s,
                                        status = %%(status)s,
                                        comment = %%(comment)s,
                                        parts = %%(parts)s
                                        WHERE id = %%(id)s
                                            AND (who, time, last_jump_datetime,
                                                status, comment, parts)
                                                    IS DISTINCT FROM
                                                (%%(who)s::text, %%(time)s, %%(last_jump_datetime)s,
                                                %%(status)s, %%(comment)s, %%(parts)s)
                                        RETURNING id'''
                        % self._table_name
                    )
                else:
                    sql_statement = (
                        '''INSERT INTO %s_evolutions (
                                               id, who, status,
                                               time, last_jump_datetime,
                                               comment, parts,
                                               formdata_id)
                                        VALUES (DEFAULT, %%(who)s, %%(status)s,
                                                %%(time)s, %%(last_jump_datetime)s,
                                                %%(comment)s,
                                                %%(parts)s, %%(formdata_id)s)
                                     RETURNING id'''
                        % self._table_name
                    )
                sql_dict.update(
                    {
                        'who': evo.who,
                        'status': evo.status,
                        'time': evo.time,
                        'last_jump_datetime': evo.last_jump_datetime,
                        'comment': evo.comment,
                        'formdata_id': self.id,
                    }
                )
                if evo.parts:
                    sql_dict['parts'] = bytearray(pickle.dumps(evo.parts, protocol=2))
                else:
                    sql_dict['parts'] = None
                cur.execute(sql_statement, sql_dict)
                row_result = cur.fetchone()
                if row_result is not None:
                    evo._sql_id = row_result[0]

        fts_strings = {'A': set(), 'B': set(), 'C': set(), 'D': set()}
        fts_strings['A'].add(str(self.id))
        fts_strings['A'].add(self.get_display_id())
        fts_strings['C'].add(self._formdef.name)

        def get_all_fields():
            for field in self._formdef.get_all_fields():
                if field.key == 'block' and self.data.get(field.id):
                    for data in self.data[field.id].get('data'):
                        try:
                            for subfield in field.block.fields:
                                yield subfield, data
                        except KeyError:
                            # block doesn't exist anymore
                            break
                else:
                    data = self.data
                    yield field, self.data

        for field, data in get_all_fields():
            if not data.get(field.id):
                continue
            value = None
            if field.key in ('string', 'text', 'email', 'item', 'items'):
                value = field.get_fts_value(data)
            if value:
                weight = 'C'
                if field.include_in_listing:
                    weight = 'B'
                if isinstance(value, str) and len(value) < 10000:
                    # avoid overlong strings, typically base64-encoded values
                    fts_strings[weight].add(value)
                    # normalize values looking like phonenumbers, because
                    # phonenumbers are normalized by the FTS criteria
                    if len(value) < 30 and value != normalize_phone_number_for_fts_if_needed(value):
                        # use weight 'D' to give preference to fields with the phonenumber validation
                        fts_strings['D'].add(normalize_phone_number_for_fts_if_needed(value))
                elif type(value) in (tuple, list):
                    for val in value:
                        fts_strings[weight].add(val)
        if self._evolution:
            for evo in self._evolution:
                if evo.comment:
                    fts_strings['D'].add(evo.comment)
                for part in evo.parts or []:
                    fts_strings['D'].add(part.render_for_fts() if part.render_for_fts else '')
        user = self.get_user()
        if user:
            fts_strings['A'].add(user.get_display_name())

        fts_parts = []
        parameters = {'id': self.id}
        for weight, strings in fts_strings.items():
            # assemble strings
            value = ' '.join([force_str(x) for x in strings if x])
            fts_parts.append("setweight(to_tsvector(%%(fts%s)s), '%s')" % (weight, weight))
            parameters['fts%s' % weight] = FtsMatch.get_fts_value(str(value))
        sql_statement = '''UPDATE %s SET fts = %s
                            WHERE id = %%(id)s''' % (
            self._table_name,
            ' || '.join(fts_parts) or "''",
        )
        cur.execute(sql_statement, parameters)

        cur.close()

    @classmethod
    def _row2ob(cls, row, extra_fields=None):
        o = cls()
        for static_field, value in zip(cls._table_static_fields, tuple(row[: len(cls._table_static_fields)])):
            setattr(o, static_field[0], value)
        for attr in ('workflow_data', 'workflow_roles', 'submission_context', 'prefilling_data'):
            if getattr(o, attr):
                setattr(o, attr, pickle_loads(getattr(o, attr)))

        o.geolocations = {}
        for i, field in enumerate((cls._formdef.geolocations or {}).keys()):
            value = row[len(cls._table_static_fields) + i]
            if not value:
                continue
            m = re.match(r'\(([^)]+),([^)]+)\)', value)
            o.geolocations[field] = {'lon': float(m.group(1)), 'lat': float(m.group(2))}

        o.data = cls._row2obdata(row, cls._formdef)
        if extra_fields:
            # extra fields are tuck at the end
            # count number of columns
            count = (
                len(extra_fields)
                + len([x for x in extra_fields if x.store_display_value])
                + len([x for x in extra_fields if x.store_structured_value])
            )
            i = len(row) - count
            for field in extra_fields:
                coldata, i = cls._col2obdata(row, i, field)
                o.data.update(coldata)
        return o

    @classmethod
    def get_sql_data_fields(cls):
        data_fields = ['geoloc_%s' % x for x in (cls._formdef.geolocations or {}).keys()]
        for field in cls._formdef.get_all_fields():
            sql_type = SQL_TYPE_MAPPING.get(field.key, 'varchar')
            if sql_type is None:
                continue
            data_fields.append(get_field_id(field))
            if field.store_display_value:
                data_fields.append('%s_display' % get_field_id(field))
            if field.store_structured_value:
                data_fields.append('%s_structured' % get_field_id(field))
        return data_fields

    @atomic
    def store_processing_change(self):
        _, cur = get_connection_and_cursor()
        sql_statement = f'''UPDATE {self._table_name}
                               SET workflow_processing_timestamp = %(timestamp)s,
                                   workflow_processing_afterjob_id = %(job_id)s
                             WHERE id = %(id)s'''
        cur.execute(
            sql_statement,
            {
                'id': self.id,
                'timestamp': self.workflow_processing_timestamp,
                'job_id': self.workflow_processing_afterjob_id,
            },
        )
        cur.close()

    @classmethod
    def get(cls, id, ignore_errors=False, ignore_migration=False):
        try:
            if not (0 < int(str(id)) < 2**31) or not is_ascii_digit(str(id)):
                # avoid NumericValueOutOfRange and _ in digits
                raise TypeError()
        except (TypeError, ValueError):
            if ignore_errors:
                return None
            raise KeyError()
        _, cur = get_connection_and_cursor()

        fields = cls.get_sql_data_fields()

        potential_comma = ', '
        if not fields:
            potential_comma = ''

        sql_statement = '''SELECT %s
                                  %s
                                  %s
                             FROM %s
                            WHERE id = %%(id)s''' % (
            ', '.join([x[0] for x in cls._table_static_fields]),
            potential_comma,
            ', '.join(fields),
            cls._table_name,
        )
        cur.execute(sql_statement, {'id': str(id)})
        row = cur.fetchone()
        if row is None:
            cur.close()
            if ignore_errors:
                return None
            raise KeyError()
        cur.close()
        return cls._row2ob(row)

    @classmethod
    def get_ids_with_indexed_value(cls, index, value, auto_fallback=True, clause=None):
        _, cur = get_connection_and_cursor()

        where_clauses, parameters, func_clause = cls.parse_clause(clause)
        assert not func_clause

        if isinstance(value, int):
            value = str(value)

        if '%s_array' % index in [x[0] for x in cls._table_static_fields]:
            sql_statement = '''SELECT id FROM %s WHERE %s_array @> ARRAY[%%(value)s]''' % (
                cls._table_name,
                index,
            )
        else:
            sql_statement = '''SELECT id FROM %s WHERE %s = %%(value)s''' % (cls._table_name, index)

        if where_clauses:
            sql_statement += ' AND ' + ' AND '.join(where_clauses)
        else:
            parameters = {}

        parameters.update({'value': value})
        cur.execute(sql_statement, parameters)
        all_ids = [x[0] for x in cur.fetchall()]
        cur.close()
        return all_ids

    @classmethod
    def get_order_by_clause(cls, order_by):
        if hasattr(order_by, 'id'):
            # form field, convert to its column name
            attribute = order_by
            order_by = get_field_id(attribute)
            if attribute.store_display_value:
                order_by = order_by + '_display'
        return super().get_order_by_clause(order_by)

    @classmethod
    def rebuild_security(cls, update_all=False, increment=None):
        formdatas = cls.select(order_by='id', iterator=True)
        _, cur = get_connection_and_cursor()
        with atomic() as atomic_context:
            for i, formdata in enumerate(formdatas):
                # don't update all formdata before commiting
                # this will make us hold locks for much longer than required
                if i % 100 == 0:
                    atomic_context.partial_commit()

                if not update_all:
                    sql_statement = (
                        '''UPDATE %s
                              SET concerned_roles_array = %%(roles)s,
                                  actions_roles_array = %%(actions_roles)s,
                                  workflow_merged_roles_dict = %%(workflow_merged_roles_dict)s
                            WHERE id = %%(id)s
                              AND (concerned_roles_array <> %%(roles)s OR
                                  actions_roles_array <> %%(actions_roles)s OR
                                  workflow_merged_roles_dict <> %%(workflow_merged_roles_dict)s)'''
                        % cls._table_name
                    )
                else:
                    sql_statement = (
                        '''UPDATE %s
                              SET concerned_roles_array = %%(roles)s,
                                  actions_roles_array = %%(actions_roles)s,
                                  workflow_merged_roles_dict = %%(workflow_merged_roles_dict)s
                            WHERE id = %%(id)s'''
                        % cls._table_name
                    )
                with get_publisher().substitutions.temporary_feed(formdata):
                    # formdata is already added to sources list in individual
                    # {concerned,actions}_roles but adding it first here will
                    # allow cached values to be reused between the properties.
                    cur.execute(
                        sql_statement,
                        {
                            'id': formdata.id,
                            'roles': [str(x) for x in formdata.concerned_roles if x],
                            'actions_roles': [str(x) for x in formdata.actions_roles if x],
                            'workflow_merged_roles_dict': formdata.workflow_merged_roles_dict,
                        },
                    )
                if increment:
                    increment()
        cur.close()

    @classonlymethod
    def wipe(cls, drop=False):
        _, cur = get_connection_and_cursor()
        if drop:
            cur.execute('''DROP TABLE %s_evolutions CASCADE''' % cls._table_name)
            cur.execute('''DELETE FROM %s''' % cls._table_name)  # force trigger execution first.
            cur.execute('''DROP TABLE %s CASCADE''' % cls._table_name)
        else:
            cur.execute('''DELETE FROM %s_evolutions''' % cls._table_name)
            cur.execute('''DELETE FROM %s''' % cls._table_name)
        cur.close()

    @classmethod
    def do_tracking_code_table(cls):
        do_tracking_code_table()

    @classmethod
    def get_static_criterias(cls):
        if cls._formdef.use_test_data_class:
            return [Contains('test_result_id', get_publisher().allowed_test_result_ids or [])]
        return []

    @classmethod
    def clean_stalled_workflow_processing(cls):
        dummy, cur = get_connection_and_cursor()

        # get list of stalled card/form data
        cur.execute(
            f'''SELECT id, status
                  FROM {cls._table_name}
                 WHERE workflow_processing_timestamp < %(timestamp)s''',
            {'timestamp': now() - datetime.timedelta(hours=1)},
        )
        stalled = cur.fetchall()
        if stalled:
            for id, status_id in stalled:
                trace = WorkflowTrace()
                trace.formdef_type = cls._formdef.xml_root_node
                trace.formdef_id = cls._formdef.id
                trace.formdata_id = id
                trace.status_id = status_id
                trace.event = 'unstall'
                trace.store()
                get_publisher().record_error(
                    _('Stalled processing'), formdata=cls._formdef.data_class().get(id)
                )

            # unstall items
            cur.execute(
                f'''UPDATE {cls._table_name}
                               SET workflow_processing_timestamp = NULL,
                                   workflow_processing_afterjob_id = NULL
                             WHERE id IN %(ids)s''',
                {'ids': tuple(x[0] for x in stalled)},
            )

        cur.close()


class SqlFormData(SqlDataMixin, wcs.formdata.FormData):
    _table_static_fields = SqlDataMixin._table_static_fields + [('uuid', 'uuid UNIQUE')]


class SqlCardData(SqlDataMixin, wcs.carddata.CardData):
    _table_static_fields = SqlDataMixin._table_static_fields + [
        ('uuid', 'uuid UNIQUE NOT NULL DEFAULT gen_random_uuid()')
    ]

    def store(self, *args, **kwargs):
        is_new_card = bool(not self.id)
        super().store(*args, **kwargs)
        if self._has_changed_digest and not is_new_card:
            self.update_related()

    @classmethod
    def select_as_items(cls, digest_key, clause=None, order_by=None, limit=None):
        sql_statement = '''SELECT %s, digests->'%s' FROM %s''' % (
            'id_display' if cls._formdef.id_template else 'id',
            digest_key,
            cls._table_name,
        )
        where_clauses, parameters, func_clause = cls.parse_clause(clause)
        assert not func_clause, 'func clauses not supported'
        if where_clauses:
            sql_statement += ' WHERE ' + ' AND '.join(where_clauses)
        sql_statement += cls.get_order_by_clause(order_by)
        if limit:
            sql_statement += ' LIMIT %(limit)s'
            parameters['limit'] = limit
        _, cur = get_connection_and_cursor()
        with cur:
            cur.execute(sql_statement, parameters)
            return [{'id': x[0], 'text': x[1] or ''} for x in cur.fetchall()]


class SqlUser(SqlMixin, wcs.users.User):
    _table_name = 'users'
    _table_static_fields = [
        ('id', 'serial'),
        ('name', 'varchar'),
        ('email', 'varchar'),
        ('roles', 'varchar[]'),
        ('is_admin', 'bool'),
        ('name_identifiers', 'varchar[]'),
        ('verified_fields', 'varchar[]'),
        ('lasso_dump', 'text'),
        ('last_seen', 'timestamp'),
        ('ascii_name', 'varchar'),
        ('deleted_timestamp', 'timestamp'),
        ('is_active', 'bool'),
        ('preferences', 'jsonb'),
        ('test_uuid', 'varchar'),
    ]
    _sql_indexes = [
        'users_name_idx ON users (name)',
        'users_name_identifiers_idx ON users USING gin(name_identifiers)',
        'users_fts ON users USING gin(fts)',
        'users_roles_idx ON users USING gin(roles)',
        'users_email_idx ON users (email)',
        'users_email_lower_idx ON users (LOWER(email))',
    ]

    id = None

    def __init__(self, name=None):
        self.name = name
        self.name_identifiers = []
        self.verified_fields = []
        self.roles = []

    @classmethod
    def get_static_criterias(cls):
        return [Null('test_uuid')]

    @invalidate_substitution_cache
    def store(self, comment=None, application=None):
        sql_dict = {
            'name': self.name,
            'ascii_name': self.ascii_name,
            'email': self.email,
            'roles': self.roles,
            'is_admin': self.is_admin,
            'name_identifiers': self.name_identifiers,
            'verified_fields': self.verified_fields,
            'lasso_dump': self.lasso_dump,
            'last_seen': None,
            'deleted_timestamp': self.deleted_timestamp,
            'is_active': self.is_active,
            'preferences': self.preferences,
            'test_uuid': self.test_uuid,
        }
        if self.last_seen:
            sql_dict['last_seen'] = (datetime.datetime.fromtimestamp(self.last_seen),)

        user_formdef = self.get_formdef()
        if not self.form_data:
            self.form_data = {}
        sql_dict.update(self.get_sql_dict_from_data(self.form_data, user_formdef))

        _, cur = get_connection_and_cursor()
        if not self.id:
            column_names = sql_dict.keys()
            sql_statement = '''INSERT INTO %s (id, %s)
                               VALUES (DEFAULT, %s)
                               RETURNING id''' % (
                self._table_name,
                ', '.join(column_names),
                ', '.join(['%%(%s)s' % x for x in column_names]),
            )
            cur.execute(sql_statement, sql_dict)
            self.id = cur.fetchone()[0]
        else:
            column_names = sql_dict.keys()
            sql_dict['id'] = self.id
            sql_statement = '''UPDATE %s SET %s WHERE id = %%(id)s RETURNING id''' % (
                self._table_name,
                ', '.join(['%s = %%(%s)s' % (x, x) for x in column_names]),
            )
            cur.execute(sql_statement, sql_dict)
            if cur.fetchone() is None:
                column_names = sql_dict.keys()
                sql_statement = '''INSERT INTO %s (%s) VALUES (%s)''' % (
                    self._table_name,
                    ', '.join(column_names),
                    ', '.join(['%%(%s)s' % x for x in column_names]),
                )
                cur.execute(sql_statement, sql_dict)

        fts_strings = []
        if self.name:
            fts_strings.append(('A', self.name))
            fts_strings.append(('A', self.ascii_name))
        if self.email:
            fts_strings.append(('B', self.email))
        if user_formdef and user_formdef.fields:
            for field in user_formdef.fields:
                if not self.form_data.get(field.id):
                    continue
                value = None
                if field.key in ('string', 'text', 'email'):
                    value = self.form_data.get(field.id)
                elif field.key in ('item', 'items'):
                    value = self.form_data.get('%s_display' % field.id)
                if value:
                    if isinstance(value, str):
                        fts_strings.append(('B', value))
                    elif type(value) in (tuple, list):
                        for val in value:
                            fts_strings.append(('B', val))

        fts_parts = []
        parameters = {'id': self.id}
        for i, (weight, value) in enumerate(fts_strings):
            fts_parts.append("setweight(to_tsvector(%%(fts%s)s), '%s')" % (i, weight))
            parameters['fts%s' % i] = FtsMatch.get_fts_value(value)
        sql_statement = '''UPDATE %s SET fts = %s
                            WHERE id = %%(id)s''' % (
            self._table_name,
            ' || '.join(fts_parts) or "''",
        )
        cur.execute(sql_statement, parameters)

        if hasattr(self, '_name_in_db') and self._name_in_db != self.name:
            # update wcs_all_forms rows with name change
            sql_statement = 'UPDATE wcs_all_forms SET user_name = %(user_name)s WHERE user_id = %(user_id)s'
            cur.execute(sql_statement, {'user_id': str(self.id), 'user_name': self.name})

        cur.close()

        if self.test_uuid and get_publisher().snapshot_class:
            get_publisher().snapshot_class.snap(instance=self, comment=comment, application=application)

    @classmethod
    def _row2ob(cls, row, **kwargs):
        o = cls()
        (
            o.id,
            o.name,
            o.email,
            o.roles,
            o.is_admin,
            o.name_identifiers,
            o.verified_fields,
            o.lasso_dump,
            o.last_seen,
            ascii_name,  # XXX what's this ? pylint: disable=unused-variable
            o.deleted_timestamp,
            o.is_active,
            o.preferences,
            o.test_uuid,
        ) = row[: len(cls._table_static_fields)]
        if o.last_seen:
            o.last_seen = time.mktime(o.last_seen.timetuple())
        if o.roles:
            o.roles = [str(x) for x in o.roles]
        o.form_data = cls._row2obdata(row, cls.get_formdef())
        o._name_in_db = o.name  # keep track of stored name
        return o

    @classmethod
    def get_sql_data_fields(cls):
        data_fields = []
        for field in cls.get_formdef().get_all_fields():
            sql_type = SQL_TYPE_MAPPING.get(field.key, 'varchar')
            if sql_type is None:
                continue
            data_fields.append(get_field_id(field))
            if field.store_display_value:
                data_fields.append('%s_display' % get_field_id(field))
            if field.store_structured_value:
                data_fields.append('%s_structured' % get_field_id(field))
        return data_fields

    @classmethod
    def get_user_uuids(cls):
        _, cur = get_connection_and_cursor()

        sql_statement = '''SELECT name_identifiers
                             FROM users
                            WHERE deleted_timestamp IS NULL
                              AND name_identifiers IS NOT NULL
                              AND test_uuid IS NULL
                        '''
        cur.execute(sql_statement)
        uuids = []
        for row in cur.fetchall():
            uuids.extend(row[0])
        cur.close()
        return uuids

    @classmethod
    def get_formdef_keepalive_user_uuids(cls):
        _, cur = get_connection_and_cursor()

        sql_statement = '''SELECT name_identifiers
                             FROM users
                            WHERE deleted_timestamp IS NULL
                              AND name_identifiers IS NOT NULL
                              AND CAST(users.id AS VARCHAR) IN (
                                  SELECT user_id
                                    FROM wcs_all_forms
                                   WHERE is_at_endpoint = false)
                        '''
        cur.execute(sql_statement)
        uuids = []
        for row in cur.fetchall():
            uuids.extend(row[0])
        cur.close()
        return uuids

    @classmethod
    def get_reference_ids(cls):
        '''Retrieve ids of users reference in some carddata or formdata.'''
        from wcs.carddef import CardDef
        from wcs.formdef import FormDef

        referenced_ids = set()

        _, cur = get_connection_and_cursor()

        for objectdef in CardDef.select() + FormDef.select():
            data_class = objectdef.data_class()

            # referenced in form/card data.user_id
            sql_statement = (
                'SELECT CAST(data.user_id AS INTEGER) FROM %(table)s AS data WHERE data.user_id IS NOT NULL'
                % {
                    'table': data_class._table_name,
                }
            )
            cur.execute(sql_statement)
            referenced_ids.update(user_id for user_id, in cur.fetchall())

            # referenced in form/card data_evolution.who
            sql_statement = '''SELECT CAST(evolution.who AS INTEGER)
                                 FROM %(table)s AS evolution
                                WHERE evolution.who != '_submitter'
                            ''' % {
                'table': '%s_evolutions' % data_class._table_name,
            }
            cur.execute(sql_statement)
            referenced_ids.update(user_id for user_id, in cur.fetchall())

            # referenced in form/card data.workflow_roles_array
            sql_statement = '''SELECT CAST(SUBSTRING(workflow_role.workflow_role FROM 7) AS INTEGER)
                                 FROM %(table)s AS data, UNNEST(data.workflow_roles_array) AS workflow_role
                                 WHERE SUBSTRING(workflow_role.workflow_role FROM 1 FOR 6) = '_user:' ''' % {
                # users will be referenced as "_user:<user id>" entries in
                # workflow_roles_array, filter on values starting with "_user:"
                # (FROM 1 FOR 6) and extract the id part (FROM 7).
                'table': data_class._table_name,
            }
            cur.execute(sql_statement)
            referenced_ids.update(user_id for user_id, in cur.fetchall())
        cur.close()
        return referenced_ids

    @classmethod
    def get_to_delete_ids(cls):
        '''Retrieve ids of users which are deleted on the IdP and are no more referenced by any form or card.'''

        # fetch marked as deleted users
        _, cur = get_connection_and_cursor()
        sql_statement = 'SELECT users.id FROM users WHERE users.deleted_timestamp IS NOT NULL'
        cur.execute(sql_statement)
        deleted_ids = {user_id for user_id, in cur.fetchall()}
        cur.close()

        to_delete_ids = deleted_ids.difference(cls.get_reference_ids())
        return to_delete_ids


class TestUser(SqlUser):
    @classmethod
    def get_static_criterias(cls):
        return [NotNull('test_uuid')]

    @classmethod
    def migrate_legacy(cls):
        for user in cls.select():
            if not user.name_identifiers:
                user.name_identifiers = [uuid.uuid4().hex]
                user.store()


class Role(SqlMixin, wcs.roles.Role):
    _table_name = 'roles'
    _table_static_fields = [
        ('id', 'varchar'),
        ('name', 'varchar'),
        ('uuid', 'varchar'),
        ('slug', 'varchar'),
        ('internal', 'boolean'),
        ('details', 'varchar'),
        ('emails', 'varchar[]'),
        ('emails_to_members', 'boolean'),
        ('allows_backoffice_access', 'boolean'),
    ]

    _numerical_id = False

    @classmethod
    def get(cls, id, ignore_errors=False, ignore_migration=False, column=None):
        o = super().get(id, ignore_errors=ignore_errors, ignore_migration=ignore_migration, column=column)
        if o and not ignore_migration:
            if o.migrate():
                o.store()
        return o

    def store(self):
        if self.slug is None:
            # set slug if it's not yet there
            self.slug = self.get_new_slug()

        sql_dict = {
            'id': self.id,
            'name': self.name,
            'uuid': self.uuid,
            'slug': self.slug,
            'internal': self.internal,
            'details': self.details,
            'emails': self.emails,
            'emails_to_members': self.emails_to_members,
            'allows_backoffice_access': self.allows_backoffice_access,
        }

        conn, cur = get_connection_and_cursor()
        column_names = sql_dict.keys()

        if not self.id:
            sql_dict['id'] = self.get_new_id()
            sql_statement = '''INSERT INTO %s (%s)
                               VALUES (%s)
                               RETURNING id''' % (
                self._table_name,
                ', '.join(column_names),
                ', '.join(['%%(%s)s' % x for x in column_names]),
            )
            while True:
                try:
                    cur.execute(sql_statement, sql_dict)
                except psycopg2.IntegrityError:
                    conn.rollback()
                    sql_dict['id'] = self.get_new_id()
                else:
                    break
            self.id = cur.fetchone()[0]
        else:
            sql_statement = '''INSERT INTO %s (%s) VALUES (%s) ON CONFLICT(id) DO UPDATE SET %s''' % (
                self._table_name,
                ', '.join(column_names),
                ', '.join(['%%(%s)s' % x for x in column_names]),
                ', '.join(['%s = excluded.%s' % (x, x) for x in column_names]),
            )

            cur.execute(sql_statement, sql_dict)

        cur.close()

        self.adjust_permissions()


class TransientData(SqlMixin):
    # table to keep some transient submission data and form tokens out of session object
    _table_name = 'transient_data'
    _table_static_fields = [
        ('id', 'varchar'),
        ('session_id', 'varchar'),
        ('data', 'bytea'),
        ('last_update_time', 'timestamptz'),
    ]
    _numerical_id = False
    _sql_indexes = [
        'transient_data_session_idx ON transient_data (session_id)',
    ]

    def __init__(self, id, session_id, data):
        self.id = id
        self.session_id = session_id
        self.data = data

    def store(self):
        sql_dict = {
            'id': self.id,
            'session_id': self.session_id,
            'data': bytearray(pickle.dumps(self.data, protocol=2)) if self.data is not None else None,
            'last_update_time': now(),
        }

        _, cur = get_connection_and_cursor()
        column_names = sql_dict.keys()
        sql_statement = '''INSERT INTO %s (%s) VALUES (%s)
            ON CONFLICT(id) DO UPDATE SET %s''' % (
            self._table_name,
            ', '.join(column_names),
            ', '.join(['%%(%s)s' % x for x in column_names]),
            ', '.join(['%s = excluded.%s' % (x, x) for x in column_names]),
        )
        try:
            cur.execute(sql_statement, sql_dict)
        except psycopg2.IntegrityError as e:
            if 'transient_data_session_id_fkey' not in str(e):
                raise

        cur.close()

    @classmethod
    def _row2ob(cls, row, **kwargs):
        o = cls.__new__(cls)
        o.id = row[0]
        o.session_id = row[1]
        o.data = pickle_loads(row[2]) if row[2] else None
        return o


class Session(SqlMixin, wcs.sessions.BasicSession):
    # noqa pylint: disable=too-many-ancestors
    _table_name = 'sessions'
    _table_static_fields = [
        ('id', 'varchar'),
        ('session_data', 'bytea'),
        ('creation_time', 'timestamp'),
        ('access_time', 'timestamp'),
        ('remote_address', 'inet'),
    ]
    _numerical_id = False
    _sql_indexes = [
        'sessions_ts ON sessions (last_update_time)',
    ]

    @classmethod
    def select_recent_with_visits(cls, seconds=30 * 60, **kwargs):
        clause = [
            GreaterOrEqual('last_update_time', datetime.datetime.now() - datetime.timedelta(seconds=seconds)),
            NotNull('visiting_objects_keys'),
        ]
        return cls.select(clause=clause, **kwargs)

    @classmethod
    def clean(cls):
        last_usage_limit = datetime.datetime.now() - datetime.timedelta(days=3)
        creation_limit = datetime.datetime.now() - datetime.timedelta(days=30)
        last_update_limit = datetime.datetime.now() - datetime.timedelta(days=3)

        cls.wipe(
            clause=[
                Less('last_update_time', last_update_limit),
                Or(
                    [
                        Less('access_time', last_usage_limit),
                        Less('creation_time', creation_limit),
                        Null('access_time'),  # to remove legacy sessions where attribute was not stored
                    ]
                ),
            ]
        )

    def store(self):
        # store transient data
        for v in (self.magictokens or {}).values():
            v.store()

        # force to be empty, to make sure there's no leftover direct usage
        session_data = copy.copy(self.__dict__)
        session_data['magictokens'] = None
        del session_data['_access_time']
        del session_data['_creation_time']
        del session_data['_remote_address']

        sql_dict = {
            'id': self.id,
            'session_data': bytearray(pickle.dumps(session_data, protocol=2)),
            # the other fields are stored to run optimized SELECT() against the
            # table, they are ignored when loading the data.
            'name_identifier': self.name_identifier,
            'visiting_objects_keys': (
                list(self.visiting_objects.keys()) if getattr(self, 'visiting_objects') else None
            ),
            'last_update_time': datetime.datetime.now(),
            'access_time': datetime.datetime.fromtimestamp(self._access_time),
            'creation_time': datetime.datetime.fromtimestamp(self._creation_time),
            'remote_address': self._remote_address,
        }

        _, cur = get_connection_and_cursor()
        column_names = sql_dict.keys()
        sql_statement = '''INSERT INTO %s (%s) VALUES (%s)
                           ON CONFLICT (id) DO UPDATE SET %s''' % (
            self._table_name,
            ', '.join(column_names),
            ', '.join(['%%(%s)s' % x for x in column_names]),
            ', '.join(['%s = excluded.%s' % (x, x) for x in column_names]),
        )
        cur.execute(sql_statement, sql_dict)

        cur.close()

    @classmethod
    def _row2ob(cls, row, **kwargs):
        o = cls.__new__(cls)
        o.id = row[0]
        session_data = pickle_loads(row[1])
        for k, v in session_data.items():
            setattr(o, k, v)
        o._creation_time = (
            (time.mktime(row[2].timetuple()) + row[2].microsecond / 1e6) if row[2] else time.time()
        )
        o._access_time = (
            (time.mktime(row[3].timetuple()) + row[3].microsecond / 1e6) if row[3] else time.time()
        )
        o._remote_address = row[4] if row[4] else None
        if o.magictokens:
            # migration, obsolete storage of magictokens in session
            for k, v in o.magictokens.items():
                o.add_magictoken(k, v)
            o.magictokens = None
            o.store()
        return o

    @classmethod
    def get_sessions_for_saml(cls, name_identifier=Ellipsis, *args, **kwargs):
        _, cur = get_connection_and_cursor()

        sql_statement = '''SELECT %s
                             FROM %s
                            WHERE name_identifier = %%(value)s''' % (
            ', '.join([x[0] for x in cls._table_static_fields]),
            cls._table_name,
        )
        cur.execute(sql_statement, {'value': name_identifier})
        objects = cls.get_objects(cur)
        cur.close()

        return objects

    @classmethod
    def get_sessions_with_visited_object(cls, object_key):
        _, cur = get_connection_and_cursor()

        sql_statement = '''SELECT %s
                             FROM %s
                            WHERE %%(value)s = ANY(visiting_objects_keys)
                              AND last_update_time > (now() - interval '30 minutes')
                        ''' % (
            ', '.join([x[0] for x in cls._table_static_fields]),
            cls._table_name,
        )
        cur.execute(sql_statement, {'value': object_key})
        objects = cls.get_objects(cur)
        cur.close()

        return objects

    def add_magictoken(self, token, data):
        assert self.id
        super().add_magictoken(token, data)
        self.magictokens[token] = TransientData(id=token, session_id=self.id, data=data)
        self.magictokens[token].store()

    def get_by_magictoken(self, token, default=None):
        if not self.magictokens:
            self.magictokens = {}
        try:
            if token not in self.magictokens:
                self.magictokens[token] = TransientData.select(
                    [Equal('session_id', self.id), Equal('id', token)]
                )[0]
            return self.magictokens[token].data
        except IndexError:
            return default

    def remove_magictoken(self, token):
        super().remove_magictoken(token)
        TransientData.remove_object(token)

    def create_form_token(self):
        token = TransientData(id=secrets.token_urlsafe(16), session_id=self.id, data=None)
        token.store()
        return token.id

    def has_form_token(self, token):
        return TransientData.exists([Equal('id', token)])

    def remove_form_token(self, token):
        TransientData.remove_object(token)

    def create_token(self, usage, context):
        context['session_id'] = self.id
        context['usage'] = usage
        token_id = hashlib.sha1(repr(context).encode()).hexdigest()
        try:
            token = self.get_token(usage, token_id)
        except KeyError:
            token = TransientData(id=token_id, session_id=self.id, data=context)
            token.store()
        return token

    def get_token(self, usage, token_id):
        tokens = TransientData.select([Equal('id', token_id), Equal('session_id', self.id)])
        if not tokens or tokens[0].data.get('usage') != usage:  # missing or misusage
            raise KeyError(token_id)
        return tokens[0]


class TrackingCode(SqlMixin):
    _table_name = 'tracking_codes'
    _table_static_fields = [
        ('id', 'varchar'),
        ('formdef_id', 'varchar'),
        ('formdata_id', 'varchar'),
    ]
    _numerical_id = False

    id = None

    @classmethod
    def get(cls, id, **kwargs):
        return super().get(id.upper(), **kwargs)

    @invalidate_substitution_cache
    def store(self):
        sql_dict = {'id': self.id, 'formdef_id': self.formdef_id, 'formdata_id': self.formdata_id}

        conn, cur = get_connection_and_cursor()
        if not self.id:
            column_names = sql_dict.keys()
            sql_dict['id'] = self.get_new_id()
            sql_statement = '''INSERT INTO %s (%s)
                               VALUES (%s)
                               RETURNING id''' % (
                self._table_name,
                ', '.join(column_names),
                ', '.join(['%%(%s)s' % x for x in column_names]),
            )
            while True:
                try:
                    cur.execute(sql_statement, sql_dict)
                except psycopg2.IntegrityError:
                    conn.rollback()
                    sql_dict['id'] = self.get_new_id()
                else:
                    break
            self.id = cur.fetchone()[0]
        else:
            column_names = sql_dict.keys()
            sql_dict['id'] = self.id
            sql_statement = '''INSERT INTO %s (%s)
                               VALUES (%s)
                               ON CONFLICT ON CONSTRAINT tracking_codes_pkey
                               DO UPDATE
                               SET %s
                               RETURNING id''' % (
                self._table_name,
                ', '.join(column_names),
                ', '.join(['%%(%s)s' % x for x in column_names]),
                ', '.join(['%s = excluded.%s' % (x, x) for x in column_names]),
            )
            cur.execute(sql_statement, sql_dict)
            if cur.fetchone() is None:
                raise AssertionError()
        cur.close()


class CustomView(SqlMixin, wcs.custom_views.CustomView):
    _table_name = 'custom_views'
    _table_static_fields = [
        ('id', 'serial'),
        ('title', 'varchar'),
        ('slug', 'varchar'),
        ('author_id', 'varchar'),
        ('user_id', 'varchar'),
        ('role_id', 'varchar'),
        ('visibility', 'varchar'),
        ('formdef_type', 'varchar'),
        ('formdef_id', 'varchar'),
        ('is_default', 'boolean'),
        ('order_by', 'varchar'),
        ('group_by', 'varchar'),
        ('columns', 'jsonb'),
        ('filters', 'jsonb'),
    ]
    _sql_indexes = [
        'custom_views_formdef_type_id ON custom_views (formdef_type, formdef_id)',
        'custom_views_visibility_idx ON custom_views (visibility)',
    ]

    @invalidate_substitution_cache
    def store(self):
        self.ensure_slug()
        sql_dict = {
            'title': self.title,
            'slug': self.slug,
            'author_id': self.author_id,
            'user_id': self.user_id,
            'role_id': self.role_id,
            'visibility': self.visibility,
            'formdef_type': self.formdef_type,
            'formdef_id': self.formdef_id,
            'is_default': self.is_default,
            'order_by': self.order_by,
            'group_by': self.group_by,
            'columns': self.columns,
            'filters': self.filters,
        }

        _, cur = get_connection_and_cursor()
        if not self.id:
            column_names = sql_dict.keys()
            sql_statement = '''INSERT INTO %s (id, %s)
                               VALUES (DEFAULT, %s)
                               RETURNING id''' % (
                self._table_name,
                ', '.join(column_names),
                ', '.join(['%%(%s)s' % x for x in column_names]),
            )
            cur.execute(sql_statement, sql_dict)
            self.id = cur.fetchone()[0]
        else:
            sql_dict['id'] = self.id
            column_names = sql_dict.keys()
            sql_statement = '''UPDATE %s SET %s WHERE id = %%(id)s RETURNING id''' % (
                self._table_name,
                ', '.join(['%s = %%(%s)s' % (x, x) for x in column_names]),
            )
            cur.execute(sql_statement, sql_dict)
            if cur.fetchone() is None:
                raise AssertionError()

        cur.close()


class ApiAccess(SqlMixin, wcs.api_access.ApiAccess):
    _table_name = 'apiaccess'
    _table_static_fields = [
        ('id', 'serial'),
        ('name', 'varchar'),
        ('description', 'varchar'),
        ('access_identifier', 'varchar'),
        ('access_key', 'varchar'),
        ('restrict_to_anonymised_data', 'bool'),
        ('roles', 'text[]'),
        ('idp_api_client', 'bool'),
    ]
    _prevent_spurious_update = True

    def __init__(self):
        super().__init__()
        self.id = None

    @classmethod
    def do_table(cls):
        _, cur = get_connection_and_cursor()
        table_name = cls._table_name

        if not _table_exists(cur, table_name):
            cur.execute(
                '''CREATE TABLE %s (id SERIAL PRIMARY KEY,
                                    name varchar UNIQUE,
                                    description varchar,
                                    access_identifier varchar UNIQUE,
                                    access_key varchar,
                                    restrict_to_anonymised_data boolean,
                                    roles varchar[],
                                    idp_api_client boolean
                                   )'''
                % table_name
            )

    @classmethod
    def _row2ob(cls, row, **kwargs):
        o = cls.__new__(cls)
        for attr, value in zip([x[0] for x in cls._table_static_fields], row):
            if attr == 'roles':
                if value is not None and value != []:
                    value = [get_publisher().role_class.get(x, ignore_errors=True) for x in value]
            setattr(o, attr, value)
        return o

    def get_sql_dict(self):
        base_dict = super().get_sql_dict()
        if base_dict['roles'] is not None:
            new_roles = [x.id for x in base_dict['roles']]
            base_dict['roles'] = new_roles
        return base_dict

    def remove_self(self):
        ApiAccess.remove_object(self.id)

    @classonlymethod
    def xml_storage_class(cls):
        from wcs.qommon.xml_storage import XmlStorableObject

        # class mixing in XmlStorableObject so all works together
        class OldApiAccessXml(XmlStorableObject, wcs.api_access.ApiAccess):
            # declarations for serialization
            XML_NODES = wcs.api_access.ApiAccess.XML_NODES

        return OldApiAccessXml

    @classonlymethod
    def import_from_xml(cls, fd):
        old_api_access = cls.xml_storage_class().import_from_xml(fd)
        new_api_access = cls()
        for field, _ in cls._table_static_fields:
            if field == 'id':
                continue
            setattr(new_api_access, field, getattr(old_api_access, field))
        return new_api_access

    @classonlymethod
    def migrate_from_files(cls):
        for old_api_access in cls.xml_storage_class().select():
            new_api_access = cls()
            for field, _ in cls._table_static_fields:
                if field == 'id':
                    continue
                setattr(new_api_access, field, getattr(old_api_access, field))
            try:
                new_api_access.store()
            except psycopg2.errors.UniqueViolation:
                # too bad
                continue
            os.unlink(old_api_access.get_object_filename())

    def export_to_xml(self, include_id=False):
        old_api_access = ApiAccess.xml_storage_class()()
        for field, _ in ApiAccess._table_static_fields:
            setattr(old_api_access, field, getattr(self, field))
        return old_api_access.export_to_xml(include_id)


class Snapshot(SqlMixin, wcs.snapshots.Snapshot):
    _table_name = 'snapshots'
    _table_static_fields = [
        ('id', 'serial'),
        ('object_type', 'varchar'),
        ('object_id', 'varchar'),
        ('timestamp', 'timestamptz'),
        ('user_id', 'varchar'),
        ('comment', 'text'),
        ('serialization', 'text'),
        ('patch', 'text'),
        ('label', 'varchar'),
        ('test_results_id', 'integer'),
        ('application_slug', 'varchar'),
        ('application_version', 'varchar'),
        ('application_ignore_change', 'bool'),
        ('deleted_object', 'bool'),
    ]
    _table_select_skipped_fields = ['serialization', 'patch']
    _sql_indexes = [
        'snapshots_object_by_date ON snapshots (object_type, object_id, timestamp DESC)',
        'deleted_object ON snapshots (deleted_object)',
    ]
    _retention = '30d'

    @invalidate_substitution_cache
    def store(self):
        super().store()

    @classmethod
    def select_object_history(cls, obj, clause=None):
        return cls.select(
            [Equal('object_type', obj.xml_root_node), Equal('object_id', str(obj.id))] + (clause or []),
            order_by='-timestamp',
        )

    def is_from_object(self, obj):
        return self.object_type == obj.xml_root_node and self.object_id == str(obj.id)

    @classmethod
    def get_latest(
        cls,
        object_type,
        object_id,
        complete=False,
        max_timestamp=None,
        application=None,
        include_deleted=False,
    ):
        _, cur = get_connection_and_cursor()
        sql_statement = '''SELECT id FROM snapshots
                            WHERE object_type = %%(object_type)s
                              AND object_id = %%(object_id)s
                              %s
                              %s
                              %s
                              %s
                         ORDER BY timestamp DESC
                            LIMIT 1''' % (
            'AND deleted_object = false' if not include_deleted else '',
            'AND serialization IS NOT NULL' if complete else '',
            'AND timestamp <= %(max_timestamp)s' if max_timestamp else '',
            'AND application_slug = %(application_slug)s' if application else '',
        )
        cur.execute(
            sql_statement,
            {
                'object_type': object_type,
                'object_id': str(object_id),
                'max_timestamp': max_timestamp,
                'application_slug': application.slug if application else None,
            },
        )
        row = cur.fetchone()
        cur.close()
        if row is None:
            return None
        return cls.get(row[0])

    @classmethod
    def mark_deleted_object(cls, object_type, object_id):
        _, cur = get_connection_and_cursor()
        sql_statement = '''UPDATE snapshots
                              SET deleted_object = true
                            WHERE object_type = %s
                              AND object_id = %s'''
        cur.execute(sql_statement, (object_type, object_id))
        cur.close()

    @classmethod
    def unmark_deleted_object(cls, object_type, object_id):
        _, cur = get_connection_and_cursor()
        sql_statement = '''UPDATE snapshots
                              SET deleted_object = false
                            WHERE object_type = %s
                              AND object_id = %s'''
        cur.execute(sql_statement, (object_type, object_id))
        cur.close()

    @classmethod
    def mark_deleted_objects(cls, existing_objects):
        _, cur = get_connection_and_cursor()
        if existing_objects:
            sql_statement = '''UPDATE snapshots
                                  SET deleted_object = true
                                WHERE (object_type, object_id) NOT IN %s
                                  AND deleted_object = false'''
            cur.execute(sql_statement, (tuple(existing_objects),))
        else:
            sql_statement = '''UPDATE snapshots
                                  SET deleted_object = true
                                WHERE deleted_object = false'''
            cur.execute(sql_statement)
        cur.close()

    @classmethod
    def select_old_objects_and_count(cls, clause, include_retention=False):
        _, cur = get_connection_and_cursor()
        where_clauses, parameters, func_clause = cls.parse_clause(clause)
        assert not func_clause
        if include_retention:
            where_clauses.append(f'''timestamp < NOW() - interval '{cls._retention}' ''')
        sql_statement = '''SELECT object_type, object_id, count(*)
                             FROM snapshots
                            WHERE serialization IS NOT NULL
                              AND deleted_object = true
                              AND %s
                         GROUP BY (object_type, object_id)''' % ' AND '.join(
            where_clauses
        )
        cur.execute(sql_statement, parameters)
        result = cur.fetchall()
        cur.close()
        return result

    @classmethod
    def delete_all_but_latest(cls, object_type, object_id):
        _, cur = get_connection_and_cursor()
        sql_statement = f'''DELETE FROM snapshots
                            WHERE object_type = %(object_type)s
                              AND object_id = %(object_id)s
                              AND timestamp < NOW() - interval '{cls._retention}'
                              AND timestamp < (SELECT timestamp
                                                 FROM snapshots
                                                WHERE object_type = %(object_type)s
                                                  AND object_id = %(object_id)s
                                                  AND serialization IS NOT NULL
                                             ORDER BY timestamp DESC LIMIT 1)'''
        cur.execute(sql_statement, {'object_type': object_type, 'object_id': object_id})
        cur.close()

    @classmethod
    def _get_recent_changes(cls, object_types, user=None, limit=5, offset=0):
        _, cur = get_connection_and_cursor()
        clause = [Contains('object_type', object_types), Equal('deleted_object', False)]
        if user is not None:
            clause.append(Equal('user_id', str(user.id)))
        where_clauses, parameters, dummy = cls.parse_clause(clause)

        sql_statement = 'SELECT object_type, object_id, MAX(timestamp) AS m FROM snapshots'
        sql_statement += ' WHERE ' + ' AND '.join(where_clauses)
        sql_statement += ' GROUP BY object_type, object_id ORDER BY m DESC'

        if limit:
            sql_statement += ' LIMIT %(limit)s'
            parameters['limit'] = limit
        if offset:
            sql_statement += ' OFFSET %(offset)s'
            parameters['offset'] = offset

        cur.execute(sql_statement, parameters)
        result = cur.fetchall()
        cur.close()
        return result

    @classmethod
    def count_recent_changes(cls, object_types):
        _, cur = get_connection_and_cursor()

        clause = [Contains('object_type', object_types), Equal('deleted_object', False)]
        where_clauses, parameters, dummy = cls.parse_clause(clause)
        sql_statement = 'SELECT COUNT(*) FROM (SELECT object_type, object_id FROM snapshots'
        sql_statement += ' WHERE ' + ' AND '.join(where_clauses)
        sql_statement += ' GROUP BY object_type, object_id) AS s'

        cur.execute(sql_statement, parameters)
        count = cur.fetchone()[0]
        cur.close()
        return count

    @classmethod
    def delete_broken_snapshots(cls):
        _, cur = get_connection_and_cursor()
        cur.execute('DELETE FROM snapshots WHERE serialization IS NULL AND patch IS NULL')
        cur.close()


class LoggedError(SqlMixin):
    _table_name = 'loggederrors'
    _table_static_fields = [
        ('id', 'serial'),
        ('kind', 'varchar'),
        ('tech_id', 'varchar'),
        ('summary', 'varchar'),
        ('formdef_class', 'varchar'),
        ('formdata_id', 'varchar'),
        ('formdef_id', 'varchar'),
        ('workflow_id', 'varchar'),
        ('status_id', 'varchar'),
        ('status_item_id', 'varchar'),
        ('expression', 'varchar'),
        ('expression_type', 'varchar'),
        ('context', 'jsonb'),
        ('traceback', 'text'),
        ('exception_class', 'varchar'),
        ('exception_message', 'varchar'),
        ('occurences_count', 'integer'),
        ('first_occurence_timestamp', 'timestamptz'),
        ('latest_occurence_timestamp', 'timestamptz'),
        ('deleted_timestamp', 'timestamptz'),
        ('documentation', 'text'),
    ]
    _sql_indexes = [
        'loggederrors_formdef_id_idx ON loggederrors (formdef_id)',
        'loggederrors_workflow_id_idx ON loggederrors (workflow_id)',
    ]

    @invalidate_substitution_cache
    def store(self, comment=None):
        sql_dict = {x: getattr(self, x) for x, y in self._table_static_fields}

        conn, cur = get_connection_and_cursor()
        error = self
        if not self.id:
            existing_errors = list(self.select([Equal('tech_id', self.tech_id)]))
            if not existing_errors:
                column_names = [x for x in sql_dict.keys() if x != 'id']
                sql_statement = '''INSERT INTO %s (%s)
                                   VALUES (%s)
                                   RETURNING id''' % (
                    self._table_name,
                    ', '.join(column_names),
                    ', '.join(['%%(%s)s' % x for x in column_names]),
                )
                try:
                    cur.execute(sql_statement, sql_dict)
                    self.id = cur.fetchone()[0]
                except psycopg2.IntegrityError:
                    # tech_id already used ?
                    conn.rollback()
                    existing_errors = list(self.select([Equal('tech_id', self.tech_id)]))
            if existing_errors:
                error = existing_errors[0]
                error.record_new_occurence(self)
        else:
            column_names = sql_dict.keys()
            sql_statement = '''UPDATE %s SET %s WHERE id = %%(id)s RETURNING id''' % (
                self._table_name,
                ', '.join(['%s = %%(%s)s' % (x, x) for x in column_names]),
            )
            cur.execute(sql_statement, sql_dict)
            assert cur.fetchone() is not None, 'LoggedError id not found'
        cur.close()
        return error

    @classmethod
    def mark_for_deletion(cls, clause):
        where_clauses, parameters, dummy = cls.parse_clause(clause)
        sql_statement = f'''UPDATE {cls._table_name} SET deleted_timestamp = NOW()
                             WHERE {" AND ".join(where_clauses)}'''
        _, cur = get_connection_and_cursor()
        with cur:
            cur.execute(sql_statement, parameters)


class SqlCardFormDefMixin(SqlMixin):
    _table_static_fields = [
        ('id', 'serial'),
        ('slug', 'varchar'),
        ('name', 'varchar'),
        ('category_id', 'varchar'),
        ('workflow_id', 'varchar'),
        ('backoffice_submission_roles', 'text[]'),
        ('params', 'bytea'),
        ('fields', 'bytea'),
    ]
    id = None
    _use_upsert = True

    @classmethod
    def do_table(cls, conn=None, cur=None):
        conn, cur = get_connection_and_cursor()
        table_name = cls._table_name

        cur.execute(
            '''SELECT COUNT(*) FROM information_schema.tables
                        WHERE table_schema = 'public'
                          AND table_name = %s''',
            (table_name,),
        )
        if cur.fetchone()[0] == 0:
            cur.execute(
                f'''CREATE TABLE {table_name} (
                                    id SERIAL PRIMARY KEY,
                                    slug VARCHAR,
                                    name VARCHAR,
                                    category_id VARCHAR,
                                    workflow_id VARCHAR,
                                    backoffice_submission_roles TEXT[],
                                    params BYTEA,
                                    fields BYTEA
                                   )'''
            )
        cls.do_indexes(cur)
        cur.close()

    def get_sql_dict(self):
        return {
            'slug': self.slug,
            'name': self.name,
            'category_id': self.category_id,
            'workflow_id': getattr(self, 'workflow_id', None),
            'backoffice_submission_roles': getattr(self, 'backoffice_submission_roles', None),
            'params': pickle.dumps(self, protocol=2),
            'fields': pickle.dumps(self.fields, protocol=2),
        }

    @classmethod
    def _row2ob(cls, row, **kwargs):
        o = cls.__new__(cls)
        table_params_column_no = cls._table_static_fields.index(('params', 'bytea'))
        object_params = pickle_loads(row[table_params_column_no])  # get all attributes from params column
        for k, v in object_params.__dict__.items():
            setattr(o, k, v)
        o.id = row[0]
        o.slug = row[1]
        o.category_id = row[3]
        if len(row) == len(SqlCardFormDefMixin._table_static_fields):
            o.fields = pickle_loads(row[table_params_column_no + 1])
            for field in o.fields or []:
                field._formdef = o  # keep formdef reference
        else:
            # lightweight object
            o.fields = Ellipsis
        return o

    @classmethod
    def get_ids(cls, ids, lightweight=False, **kwargs):
        if lightweight:
            return globals()[f'SqlLightweight{cls.__name__}'].get_ids(ids, **kwargs)

        return super().get_ids(ids, **kwargs)

    @classmethod
    def get(cls, id, **kwargs):
        if kwargs.pop('lightweight', False):
            return globals()[f'SqlLightweight{cls.__name__}'].get(id, **kwargs)
        if cls.__name__.startswith('Lightweight'):
            return globals()[cls.__name__.removeprefix('Lightweight')].get(id, **kwargs)
        return super().get(id, **kwargs)

    @classmethod
    def select(cls, *args, ignore_migration=True, **kwargs):
        if kwargs.pop('lightweight', False):
            return globals()[f'SqlLightweight{cls.__name__}'].select(*args, **kwargs)
        return super().select(*args, **kwargs)

    @classmethod
    def remove_related_testdefs(cls, id):
        # user wcs.testdef.TestDef for its custom remove_object method
        import wcs.testdef

        for testdef in wcs.testdef.TestDef.select(
            [Equal('object_type', cls.get_table_name()), Equal('object_id', str(id))]
        ):
            wcs.testdef.TestDef.remove_object(testdef.id)

        for results in wcs.testdef.TestResults.select(
            [Equal('object_type', cls.get_table_name()), Equal('object_id', str(id))]
        ):
            wcs.testdef.TestResults.remove_object(results.id)

    @classmethod
    def wipe_related_testdefs(cls):
        import wcs.testdef

        for testdef in wcs.testdef.TestDef.select([Equal('object_type', cls.get_table_name())]):
            wcs.testdef.TestDef.remove_object(testdef.id)

        for results in wcs.testdef.TestResults.select([Equal('object_type', cls.get_table_name())]):
            wcs.testdef.TestResults.remove_object(results.id)

    @classonlymethod
    def wipe(cls):
        _, cur = get_connection_and_cursor()
        cur.execute(f'DELETE FROM {cls._table_name}')
        cur.execute(f'ALTER SEQUENCE {cls._table_name}_id_seq RESTART WITH 1')
        cur.close()

    @classmethod
    def migrate_from_files(cls):
        file_object_class = import_string(cls.file_object_class)
        for formdef in file_object_class.select(ignore_errors=True, ignore_migration=True):
            formdef.__class__ = cls
            formdef.store(object_only=True)
        cls.reset_restart_sequence()


class SqlFormDef(SqlCardFormDefMixin):
    _table_name = 'formdefs'
    file_object_class = 'wcs.formdef.FileFormDef'

    def get_sql_dict(self):
        sql_dict = super().get_sql_dict()
        sql_dict['workflow_id'] = self.workflow_id or '_default'
        return sql_dict

    @classmethod
    def remove_object(cls, id):
        super().remove_object(id)
        SearchableFormDef.update(removed_obj_type=cls.xml_root_node, removed_obj_id=str(id))
        conn, cur = get_connection_and_cursor()
        with atomic():
            clean_global_views(conn, cur)
        cur.close()
        cls.remove_related_testdefs(id)

    @classonlymethod
    def wipe(cls):
        _, cur = get_connection_and_cursor()
        cur.execute(
            '''SELECT table_name FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND table_name SIMILAR TO %s''',
            ('(test_)?formdata\\_%%\\_%%',),
        )
        for table_name in [x[0] for x in cur.fetchall()]:
            cur.execute('DELETE FROM %s' % table_name)  # Force trigger execution
            cur.execute('DROP TABLE %s CASCADE' % table_name)
        cur.execute("SELECT relkind FROM pg_class WHERE relname = 'wcs_all_forms'")
        row = cur.fetchone()
        # only do the delete if wcs_all_forms is a table and not still a view
        if row is not None and row[0] == 'r':
            cur.execute('TRUNCATE wcs_all_forms')
        cur.close()
        super().wipe()
        cls.wipe_related_testdefs()


class SqlLightweightFormDef(SqlFormDef):
    _table_static_fields = [x for x in SqlFormDef._table_static_fields if x[0] != 'fields']
    store = None  # unallowed

    @classmethod
    def _row2ob(cls, row, **kwargs):
        from wcs.formdef import FormDef

        o = super()._row2ob(row, **kwargs)
        o.fields = Ellipsis
        o.__class__ = FormDef
        return o


class SqlCardDef(SqlCardFormDefMixin):
    _table_name = 'carddefs'
    file_object_class = 'wcs.carddef.FileCardDef'

    def get_sql_dict(self):
        sql_dict = super().get_sql_dict()
        sql_dict['workflow_id'] = self.workflow_id or '_carddef_default'
        return sql_dict

    @classmethod
    def remove_object(cls, id):
        super().remove_object(id)
        cls.remove_related_testdefs(id)

    @classonlymethod
    def wipe(cls):
        _, cur = get_connection_and_cursor()
        cur.execute(
            '''SELECT table_name FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND table_name SIMILAR TO %s''',
            ('(test_)?carddata\\_%%\\_%%',),
        )
        for table_name in [x[0] for x in cur.fetchall()]:
            cur.execute('DELETE FROM %s' % table_name)  # Force trigger execution
            cur.execute('DROP TABLE %s CASCADE' % table_name)
        cur.close()
        super().wipe()
        cls.wipe_related_testdefs()


class SqlLightweightCardDef(SqlCardDef):
    _table_static_fields = [x for x in SqlFormDef._table_static_fields if x[0] != 'fields']
    store = None  # unallowed

    @classmethod
    def _row2ob(cls, row, **kwargs):
        from wcs.carddef import CardDef

        o = super()._row2ob(row, **kwargs)
        o.fields = Ellipsis
        o.__class__ = CardDef
        return o


class SqlBlockDef(SqlCardFormDefMixin):
    _table_name = 'blockdefs'
    file_object_class = 'wcs.blocks.FileBlockDef'


class SqlWorkflow(SqlMixin):
    _table_name = 'workflows'
    file_object_class = 'wcs.workflows.FileWorkflow'
    _table_static_fields = [
        ('id', 'serial'),
        ('slug', 'varchar'),
        ('name', 'varchar'),
        ('category_id', 'varchar'),
        ('params', 'bytea'),
    ]
    _use_upsert = True

    @classmethod
    def do_table(cls, conn=None, cur=None):
        conn, cur = get_connection_and_cursor()
        table_name = cls._table_name

        cur.execute(
            '''SELECT COUNT(*) FROM information_schema.tables
                        WHERE table_schema = 'public'
                          AND table_name = %s''',
            (table_name,),
        )
        if cur.fetchone()[0] == 0:
            cur.execute(
                f'''CREATE TABLE {table_name} (
                                    id SERIAL PRIMARY KEY,
                                    slug VARCHAR,
                                    name VARCHAR,
                                    category_id VARCHAR,
                                    params BYTEA
                                   )'''
            )
        cls.do_indexes(cur)
        cur.close()

    def get_sql_dict(self):
        return {
            'slug': self.slug,
            'name': self.name,
            'category_id': self.category_id,
            'params': pickle.dumps(self, protocol=2),
        }

    @classmethod
    def select(cls, *args, ignore_migration=True, **kwargs):
        # migrations are always ignored
        return super().select(*args, **kwargs)

    @classmethod
    def _row2ob(cls, row, **kwargs):
        o = cls.__new__(cls)
        table_params_column_no = cls._table_static_fields.index(('params', 'bytea'))
        object_params = pickle_loads(row[table_params_column_no])  # get all attributes from params column
        for k, v in object_params.__dict__.items():
            setattr(o, k, v)
        o.id = row[0]
        o.slug = row[1]
        o.category_id = row[3]
        o.__setstate__({})  # set parent attributes
        return o

    @classonlymethod
    def wipe(cls):
        _, cur = get_connection_and_cursor()
        cur.execute(f'DELETE FROM {cls._table_name}')
        cur.execute(f'ALTER SEQUENCE {cls._table_name}_id_seq RESTART WITH 1')
        cur.close()

    @classmethod
    def migrate_from_files(cls):
        file_object_class = import_string(cls.file_object_class)
        for obj in file_object_class.select(ignore_errors=True, ignore_migration=True):
            if str(obj.id).startswith('_'):
                # ignore _default, _carddef_default files that would erroneously exist.
                continue
            obj.__class__ = cls
            obj.store(object_only=True)
        cls.reset_restart_sequence()


class Token(SqlMixin, wcs.qommon.tokens.Token):
    _table_name = 'tokens'
    _table_static_fields = [
        ('id', 'varchar'),
        ('type', 'varchar'),
        ('expiration', 'timestamptz'),
        ('context', 'jsonb'),
    ]

    _numerical_id = False

    def store(self):
        sql_dict = {
            'id': self.id,
            'type': self.type,
            'expiration': self.expiration,
            'context': self.context,
        }

        conn, cur = get_connection_and_cursor()
        column_names = sql_dict.keys()

        if not self.id:
            sql_dict['id'] = self.get_new_id()
            sql_statement = '''INSERT INTO %s (%s)
                               VALUES (%s)
                               RETURNING id''' % (
                self._table_name,
                ', '.join(column_names),
                ', '.join(['%%(%s)s' % x for x in column_names]),
            )
            while True:
                try:
                    cur.execute(sql_statement, sql_dict)
                except psycopg2.IntegrityError:
                    conn.rollback()
                    sql_dict['id'] = self.get_new_id()
                else:
                    break
            self.id = cur.fetchone()[0]
        else:
            sql_statement = '''UPDATE %s SET %s WHERE id = %%(id)s RETURNING id''' % (
                self._table_name,
                ', '.join(['%s = %%(%s)s' % (x, x) for x in column_names]),
            )
            cur.execute(sql_statement, sql_dict)
            if cur.fetchone() is None:
                sql_statement = '''INSERT INTO %s (%s) VALUES (%s)''' % (
                    self._table_name,
                    ', '.join(column_names),
                    ', '.join(['%%(%s)s' % x for x in column_names]),
                )
                cur.execute(sql_statement, sql_dict)

        cur.close()

    @classmethod
    def _row2ob(cls, *args, **kwargs):
        o = super()._row2ob(*args, **kwargs)
        o.expiration_check()
        return o


class TranslatableMessage(SqlMixin):
    _table_name = 'translatable_messages'
    _table_static_fields = [
        ('id', 'serial'),
        ('string', 'varchar'),
        ('context', 'varchar'),
        ('locations', 'varchar[]'),
        ('last_update_time', 'timestamptz'),
        ('translatable', 'boolean'),
    ]
    _sql_indexes = [
        'translatable_messages_fts ON translatable_messages USING gin(fts)',
    ]

    id = None

    @classmethod
    def do_table(cls, conn=None, cur=None):
        conn, cur = get_connection_and_cursor()
        table_name = cls._table_name

        cur.execute(
            '''SELECT COUNT(*) FROM information_schema.tables
                        WHERE table_schema = 'public'
                          AND table_name = %s''',
            (table_name,),
        )
        if cur.fetchone()[0] == 0:
            cur.execute(
                '''CREATE TABLE %s (id SERIAL,
                                    string VARCHAR,
                                    context VARCHAR,
                                    locations VARCHAR[],
                                    last_update_time TIMESTAMPTZ,
                                    translatable BOOLEAN DEFAULT TRUE,
                                    fts TSVECTOR
                                   )'''
                % table_name
            )
        cur.execute(
            '''SELECT column_name FROM information_schema.columns
                        WHERE table_schema = 'public'
                          AND table_name = %s''',
            (table_name,),
        )
        existing_fields = {x[0] for x in cur.fetchall()}

        if 'translatable' not in existing_fields:
            cur.execute('''ALTER TABLE %s ADD COLUMN translatable BOOLEAN DEFAULT(TRUE)''' % table_name)

        # add columns for translations
        for field in cls.get_sql_data_fields():
            if field not in existing_fields:
                cur.execute('ALTER TABLE %s ADD COLUMN %s VARCHAR' % (table_name, field))

        cls.do_indexes(cur)
        cur.close()

    @classmethod
    def get_sql_data_fields(cls):
        languages = get_cfg('language', {}).get('languages') or []
        return ['string_%s' % x for x in languages]

    def store(self):
        sql_dict = {x[0]: getattr(self, x[0], None) for x in self._table_static_fields if x[0] != 'id'}
        sql_dict.update({x: getattr(self, x) for x in self.get_sql_data_fields() if hasattr(self, x)})

        _, cur = get_connection_and_cursor()
        column_names = list(sql_dict.keys())
        sql_dict['fts'] = FtsMatch.get_fts_value(self.string)
        if not self.id:
            sql_statement = '''INSERT INTO %s (id, %s, fts)
                               VALUES (DEFAULT, %s, TO_TSVECTOR(%%(fts)s))
                               RETURNING id''' % (
                self._table_name,
                ', '.join(column_names),
                ', '.join(['%%(%s)s' % x for x in column_names]),
            )
            cur.execute(sql_statement, sql_dict)
            self.id = cur.fetchone()[0]
        else:
            sql_dict['id'] = self.id
            sql_statement = '''UPDATE %s SET %s, fts = TO_TSVECTOR(%%(fts)s)
                                WHERE id = %%(id)s RETURNING id''' % (
                self._table_name,
                ', '.join(['%s = %%(%s)s' % (x, x) for x in column_names]),
            )
            cur.execute(sql_statement, sql_dict)

        cur.close()

    @classmethod
    def _row2ob(cls, row, **kwargs):
        o = cls.__new__(cls)
        for attr, value in zip([x[0] for x in cls._table_static_fields] + cls.get_sql_data_fields(), row):
            setattr(o, attr, value)
        return o

    @classmethod
    def load_as_catalog(cls, language):
        _, cur = get_connection_and_cursor()
        sql_statement = 'SELECT context, string, string_%s FROM %s WHERE translatable = TRUE' % (
            language,
            cls._table_name,
        )
        cur.execute(sql_statement)
        catalog = {(x[0], x[1]): x[2] for x in cur.fetchall()}
        cur.close()
        return catalog


class TestDef(SqlMixin):
    _table_name = 'testdef'
    _table_static_fields = [
        ('id', 'serial'),
        ('uuid', 'varchar'),
        ('name', 'varchar'),
        ('object_type', 'varchar'),
        ('object_id', 'varchar'),
        ('data', 'jsonb'),
        ('query_parameters', 'jsonb'),
        ('is_in_backoffice', 'boolean'),
        ('expected_error', 'varchar'),
        ('user_uuid', 'varchar'),
        ('submission_agent_uuid', 'varchar'),
        ('agent_id', 'varchar'),
        ('frozen_submission_datetime', 'timestamptz'),
        ('dependencies', 'text[]'),
        ('workflow_options', 'jsonb'),
    ]

    id = None

    @classmethod
    def do_table(cls, conn=None, cur=None):
        conn, cur = get_connection_and_cursor()
        table_name = cls._table_name

        cur.execute(
            '''SELECT COUNT(*) FROM information_schema.tables
                        WHERE table_schema = 'public'
                          AND table_name = %s''',
            (table_name,),
        )
        if cur.fetchone()[0] == 0:
            cur.execute(
                '''CREATE TABLE %s (id SERIAL PRIMARY KEY,
                                            uuid varchar,
                                            name varchar,
                                            object_type varchar NOT NULL,
                                            object_id varchar NOT NULL,
                                            data jsonb,
                                            is_in_backoffice boolean NOT NULL DEFAULT FALSE,
                                            query_parameters jsonb NOT NULL DEFAULT '{}'::jsonb,
                                            expected_error varchar,
                                            user_uuid varchar,
                                            submission_agent_uuid varchar,
                                            agent_id varchar,
                                            frozen_submission_datetime timestamptz,
                                            dependencies text[] NOT NULL DEFAULT '{}',
                                            workflow_options jsonb NOT NULL DEFAULT '{}'::jsonb
                                            )'''
                % table_name
            )
        cur.execute(
            '''SELECT column_name FROM information_schema.columns
                        WHERE table_schema = 'public'
                          AND table_name = %s''',
            (table_name,),
        )
        existing_fields = {x[0] for x in cur.fetchall()}

        if 'is_in_backoffice' not in existing_fields:
            cur.execute(
                '''ALTER TABLE %s ADD COLUMN is_in_backoffice boolean NOT NULL DEFAULT FALSE''' % table_name
            )
            existing_fields.add('is_in_backoffice')

        if 'query_parameters' not in existing_fields:
            cur.execute(
                '''ALTER TABLE %s ADD COLUMN query_parameters jsonb NOT NULL DEFAULT '{}'::jsonb'''
                % table_name
            )
            existing_fields.add('query_parameters')

        if 'dependencies' not in existing_fields:
            cur.execute("ALTER TABLE %s ADD COLUMN dependencies text[] NOT NULL DEFAULT '{}'" % table_name)
            existing_fields.add('dependencies')

        if 'workflow_options' not in existing_fields:
            cur.execute(
                '''ALTER TABLE %s ADD COLUMN workflow_options jsonb NOT NULL DEFAULT '{}'::jsonb'''
                % table_name
            )
            existing_fields.add('workflow_options')

        # generic migration for new columns
        for field_name, field_type in cls._table_static_fields:
            if field_name not in existing_fields:
                cur.execute('''ALTER TABLE %s ADD COLUMN %s %s''' % (table_name, field_name, field_type))

        # delete obsolete fields
        needed_fields = {x[0] for x in TestDef._table_static_fields}
        for field in existing_fields - needed_fields:
            cur.execute('''ALTER TABLE %s DROP COLUMN %s''' % (table_name, field))

        cur.close()

    @classmethod
    def migrate_legacy(cls):
        seen_uuids = set()
        for testdef in TestDef.select(order_by='name'):
            if testdef.data and 'expected_error' in testdef.data:
                testdef.expected_error = testdef.data['expected_error']
                del testdef.data['expected_error']
                testdef.store()
            if testdef.data.get('user'):
                cls.create_and_link_test_users(testdef)
            if not testdef.uuid:
                testdef.uuid = str(uuid.uuid4())
                testdef.store()

            cls.fix_duplicated_action_uuid(testdef)
            cls.remove_if_orphan(testdef)

            if testdef.uuid in seen_uuids:
                testdef.uuid = str(uuid.uuid4())
                testdef.store()
            else:
                seen_uuids.add(testdef.uuid)

    @staticmethod
    def create_and_link_test_users(testdef):
        from wcs.testdef import TestDef

        try:
            user = get_publisher().user_class.get(testdef.data['user']['id'])
        except KeyError:
            return

        user, _ = TestDef.get_or_create_test_user(user)
        testdef.user_uuid = user.test_uuid
        del testdef.data['user']
        testdef.store()

    @staticmethod
    def fix_duplicated_action_uuid(testdef):
        from wcs.qommon.storage import Equal as XmlEqual
        from wcs.workflow_tests import WorkflowTests

        workflow_tests_list = WorkflowTests.select([XmlEqual('testdef_id', testdef.id)])
        if not workflow_tests_list:
            return

        workflow_tests = workflow_tests_list[0]

        store = False
        seen_uuids = set()
        for action in workflow_tests.actions:
            if action.uuid in seen_uuids:
                action.uuid = str(uuid.uuid4())
                store = True
                continue

            seen_uuids.add(action.uuid)

        if store:
            workflow_tests.store()

    @staticmethod
    def remove_if_orphan(testdef):
        from wcs.carddef import CardDef
        from wcs.formdef import FormDef
        from wcs.testdef import TestDef

        klass = FormDef if testdef.object_type == 'formdefs' else CardDef
        try:
            klass.get(testdef.object_id)
        except KeyError:
            TestDef.remove_object(testdef.id)


class TestResults(SqlMixin):
    _table_name = 'test_results'
    _table_static_fields = [
        ('id', 'serial'),
        ('object_type', 'varchar'),
        ('object_id', 'varchar'),
        ('timestamp', 'timestamptz'),
        ('success', 'boolean'),
        ('reason', 'varchar'),
        ('coverage', 'jsonb'),
    ]

    id = None

    @classmethod
    def do_table(cls, conn=None, cur=None):
        conn, cur = get_connection_and_cursor()
        table_name = cls._table_name

        cur.execute(
            '''SELECT COUNT(*) FROM information_schema.tables
                        WHERE table_schema = 'public'
                          AND table_name = %s''',
            (table_name,),
        )
        if cur.fetchone()[0] == 0:
            old_table_name = 'test_result'
            cur.execute(
                '''SELECT COUNT(*) FROM information_schema.tables
                            WHERE table_schema = 'public'
                              AND table_name = %s''',
                (old_table_name,),
            )
            if cur.fetchone()[0] == 0:
                cur.execute(
                    '''CREATE TABLE %s (id SERIAL PRIMARY KEY,
                                                object_type varchar NOT NULL,
                                                object_id varchar NOT NULL,
                                                timestamp timestamptz,
                                                success boolean,
                                                reason varchar NOT NULL,
                                                coverage jsonb NOT NULL DEFAULT '{}'::jsonb
                                                )'''
                    % table_name
                )
            else:
                cur.execute('ALTER TABLE %s RENAME TO %s' % (old_table_name, table_name))

        cur.execute('ALTER TABLE %s ALTER COLUMN success DROP NOT NULL' % table_name)

        cur.execute(
            '''SELECT column_name FROM information_schema.columns
                        WHERE table_schema = 'public'
                          AND table_name = %s''',
            (table_name,),
        )
        existing_fields = {x[0] for x in cur.fetchall()}

        if 'coverage' not in existing_fields:
            cur.execute(
                '''ALTER TABLE %s ADD COLUMN coverage jsonb NOT NULL DEFAULT '{}'::jsonb''' % table_name
            )
            existing_fields.add('coverage')

        # delete obsolete fields
        needed_fields = {x[0] for x in TestResults._table_static_fields}
        for field in existing_fields - needed_fields:
            cur.execute('''ALTER TABLE %s DROP COLUMN %s''' % (table_name, field))

        cur.close()

    @classmethod
    def migrate_legacy(cls):
        for results in cls.select():
            cls.remove_if_orphan(results)

    @classmethod
    def remove_if_orphan(cls, results):
        from wcs.carddef import CardDef
        from wcs.formdef import FormDef

        klass = FormDef if results.object_type == 'formdefs' else CardDef
        try:
            klass.get(results.object_id)
        except KeyError:
            cls.remove_object(results.id)


class TestResult(SqlMixin):
    _table_name = 'test_result'
    _table_static_fields = [
        ('id', 'serial'),
        ('test_results_id', 'integer'),
        ('test_id', 'integer'),
        ('test_name', 'varchar'),
        ('error', 'varchar'),
        ('formdata_id', 'integer'),
        ('recorded_errors', 'text[]'),
        ('missing_required_fields', 'text[]'),
        ('sent_requests', 'jsonb[]'),
        ('workflow_test_action_uuid', 'varchar'),
        ('error_details', 'text[]'),
        ('error_field_id', 'varchar'),
        ('dependency_uuid', 'varchar'),
    ]

    id = None

    @classmethod
    def do_table(cls, conn=None, cur=None):
        conn, cur = get_connection_and_cursor()
        table_name = cls._table_name

        cur.execute(
            '''SELECT COUNT(*) FROM information_schema.tables
                        WHERE table_schema = 'public'
                          AND table_name = %s''',
            (table_name,),
        )
        if cur.fetchone()[0] == 0:
            cur.execute(
                '''CREATE TABLE %s (id SERIAL PRIMARY KEY,
                                    test_results_id integer REFERENCES test_results(id) ON DELETE CASCADE,
                                    test_id integer NOT NULL,
                                    test_name varchar NOT NULL,
                                    error varchar NOT NULL,
                                    formdata_id integer,
                                    recorded_errors text[] NOT NULL,
                                    missing_required_fields text[] NOT NULL,
                                    sent_requests jsonb[] NOT NULL,
                                    workflow_test_action_uuid varchar NOT NULL,
                                    error_details text[] NOT NULL,
                                    error_field_id varchar NOT NULL,
                                    dependency_uuid varchar NOT NULL
                                    )'''
                % table_name
            )

        cur.execute(
            '''SELECT column_name FROM information_schema.columns
                        WHERE table_schema = 'public'
                          AND table_name = %s''',
            (table_name,),
        )
        existing_fields = {x[0] for x in cur.fetchall()}

        # generic migration for new columns
        for field_name, field_type in cls._table_static_fields:
            if field_name not in existing_fields:
                cur.execute('''ALTER TABLE %s ADD COLUMN %s %s''' % (table_name, field_name, field_type))

        # delete obsolete fields
        needed_fields = {x[0] for x in TestResult._table_static_fields}
        for field in existing_fields - needed_fields:
            cur.execute('''ALTER TABLE %s DROP COLUMN %s''' % (table_name, field))

        cur.close()

    def store(self):
        sql_dict = {x[0]: getattr(self, x[0], None) for x in self._table_static_fields if x[0] != 'id'}

        _, cur = get_connection_and_cursor()
        column_names = list(sql_dict.keys())

        column_values = []
        for name in column_names:
            value = '%%(%s)s' % name
            if name == 'sent_requests':
                value += '::jsonb[]'
            column_values.append(value)

        if not self.id:
            sql_statement = '''INSERT INTO %s (id, %s)
                               VALUES (DEFAULT, %s)
                               RETURNING id''' % (
                self._table_name,
                ', '.join(column_names),
                ', '.join(column_values),
            )
            cur.execute(sql_statement, sql_dict)
            self.id = cur.fetchone()[0]
        else:
            sql_dict['id'] = self.id
            sql_statement = '''UPDATE %s SET %s WHERE id = %%(id)s RETURNING id''' % (
                self._table_name,
                ', '.join(['%s = %s' % (x, y) for x, y in zip(column_names, column_values)]),
            )
            cur.execute(sql_statement, sql_dict)

        cur.close()

    @classmethod
    @atomic
    def migrate_legacy(cls):
        cls.migrate_result_formdata_to_test_tables()
        cls.migrate_remaining_formdata_to_test_tables()

    @classmethod
    def migrate_result_formdata_to_test_tables(cls):
        from wcs.carddef import CardDef
        from wcs.formdef import FormDef

        formdef_by_test_id = {}
        for result in cls.select([NotNull('formdata_id')]):
            if result.test_id not in formdef_by_test_id:
                test_results = TestResults.get(result.test_results_id)

                klass = FormDef if test_results.object_type == 'formdefs' else CardDef
                formdef = klass.get(test_results.object_id)
                formdef.test_table_name = get_formdef_test_table_name(formdef)

                formdef_by_test_id[result.test_id] = formdef
            else:
                formdef = formdef_by_test_id[result.test_id]

            try:
                formdata = formdef.data_class().get(result.formdata_id)
            except KeyError:
                # should only happen for formdata already in test table
                continue

            if formdata.test_result_id != result.id:
                continue

            formdata = cls.migrate_formdata_to_test_table(formdata, formdef)

            result.formdata_id = formdata.id
            result.store()

    @classmethod
    def migrate_remaining_formdata_to_test_tables(cls):
        from wcs.carddef import CardDef
        from wcs.formdef import FormDef

        for formdef in FormDef.select() + CardDef.select():
            formdef.test_table_name = get_formdef_test_table_name(formdef)
            for formdata in formdef.data_class().select([NotNull('test_result_id')]):
                cls.migrate_formdata_to_test_table(formdata, formdef)

    @staticmethod
    def migrate_formdata_to_test_table(formdata, formdef):
        evolution = formdata.evolution
        traces = formdata.get_workflow_traces()
        formdef.data_class().remove_object(formdata.id)

        for evo in evolution:
            del evo._sql_id

        formdata.id = None
        formdata._table_name = formdef.test_table_name
        formdata.evolution = evolution
        formdata.store()

        for trace in traces:
            trace.id = None
            trace._table_name = WorkflowTrace._test_table_name
            trace.formdata_id = formdata.id
            trace.store()

        return formdata


class WorkflowTrace(SqlMixin):
    _table_name = 'workflow_traces'
    _test_table_name = 'test_workflow_traces'
    _table_static_fields = [
        ('id', 'serial'),
        ('formdef_type', 'varchar'),
        ('formdef_id', 'integer'),
        ('formdata_id', 'integer'),
        ('status_id', 'varchar'),
        ('event', 'varchar'),
        ('event_args', 'jsonb'),
        ('timestamp', 'timestamptz'),
        ('action_item_key', 'varchar'),
        ('action_item_id', 'varchar'),
    ]
    _sql_indexes = [
        'workflow_traces_idx ON workflow_traces (formdef_type, formdef_id, formdata_id)',
    ]

    id = None
    formdef_type = None
    formdef_id = None
    formdata_id = None
    status_id = None
    timestamp = None
    event = None
    event_args = None
    action_item_key = None
    action_item_id = None

    def __init__(self, formdata=None, event=None, event_args=None, action=None):
        self.timestamp = localtime()
        if formdata:
            self.formdef_type = formdata.formdef.xml_root_node
            self.formdef_id = formdata.formdef.id
            self.formdata_id = formdata.id
            self.status_id = formdata.status
        self.event = event
        self.event_args = event_args
        if action:
            self.action_item_key = action.key
            self.action_item_id = action.id

    @classmethod
    def do_table(cls, conn=None, cur=None):
        conn, cur = get_connection_and_cursor()

        for table_name in (cls._table_name, cls._test_table_name):
            cls.create_table(cur, table_name)

        cur.close()

    @classmethod
    def create_table(cls, cur, table_name):
        base_fields = [
            'id SERIAL PRIMARY KEY',
            'formdef_type varchar NOT NULL',
            'formdef_id integer NOT NULL',
            'formdata_id integer NOT NULL',
        ]
        event_fields = [
            'status_id varchar',
            'event varchar',
            'event_args jsonb',
            'timestamp timestamptz',
            'action_item_key varchar',
            'action_item_id varchar',
        ]

        cur.execute(
            f'''CREATE TABLE IF NOT EXISTS {table_name} (
                    {','.join(base_fields)},
                    {','.join(event_fields)})'''
        )

        cur.execute(
            '''SELECT 1 FROM pg_type
                        WHERE typname = 'workflow_trace_event'
                          AND typnamespace = (SELECT oid FROM pg_namespace WHERE nspname = 'public')'''
        )
        if not cur.fetchall():
            cur.execute(f'''CREATE TYPE workflow_trace_event AS ({','.join(event_fields)})''')

        cur.execute(
            f'''CREATE TABLE IF NOT EXISTS {table_name}_archive (
                    {','.join(base_fields)},
                    traces workflow_trace_event[])'''
        )

        cur.execute(
            f'''CREATE UNIQUE INDEX IF NOT EXISTS {table_name}_archive_formdata_idx
                ON {table_name}_archive (formdef_type, formdef_id, formdata_id)'''
        )

        cls.do_indexes(cur)

    @classmethod
    def select_for_formdata(cls, formdata):
        _, cur = get_connection_and_cursor()
        cur.execute(
            f'''SELECT formdef_type, formdef_id, formdata_id, (trace.*)
                          FROM {cls._table_name}_archive
                          JOIN LATERAL unnest(traces) trace ON true
                         WHERE formdef_type = %(formdef_type)s
                           AND formdef_id = %(formdef_id)s
                           AND formdata_id = %(formdata_id)s
                      ORDER BY timestamp''',
            {
                'formdef_type': formdata.formdef.xml_root_node,
                'formdef_id': formdata.formdef.id,
                'formdata_id': formdata.id,
            },
        )

        def archived_row2ob(row):
            o = cls()
            (
                o.formdef_type,
                o.formdef_id,
                o.formdata_id,
                o.status_id,
                o.event,
                o.event_args,
                o.timestamp,
                o.action_item_key,
                o.action_item_id,
            ) = row
            return o

        archived_traces = [archived_row2ob(x) for x in cur.fetchall()]

        recent_traces = cls.select(
            [
                Equal('formdef_type', formdata.formdef.xml_root_node),
                Equal('formdef_id', formdata.formdef.id),
                Equal('formdata_id', formdata.id),
            ],
            order_by='timestamp',
        )
        return archived_traces + recent_traces

    @classmethod
    def archive(cls, vacuum_full=False, **kwargs):
        archive_time = localtime() - datetime.timedelta(days=7)

        _, cur = get_connection_and_cursor()

        for table_name in (cls._table_name, cls._test_table_name):
            cur.execute(f'SELECT DISTINCT formdef_type, formdef_id FROM {table_name}')
            for formdef_type, formdef_id in cur.fetchall():
                cur.execute(
                    f'''
                    WITH traces_to_archive AS (
                         DELETE FROM {table_name}
                               WHERE formdef_type = %(formdef_type)s
                                 AND formdef_id = %(formdef_id)s
                                 AND timestamp < %(archive_time)s
                         RETURNING *
                    )
                    INSERT INTO {table_name}_archive (formdef_type, formdef_id, formdata_id, traces)
                        SELECT formdef_type, formdef_id, formdata_id,
                               ARRAY_AGG((status_id, event, event_args, timestamp,
                                          action_item_key, action_item_id)::workflow_trace_event)
                         FROM traces_to_archive
                     GROUP BY formdef_type, formdef_id, formdata_id
                    ON CONFLICT (formdef_type, formdef_id, formdata_id)
                    DO UPDATE SET traces = {table_name}_archive.traces || excluded.traces;
                            ''',
                    {
                        'formdef_type': formdef_type,
                        'formdef_id': formdef_id,
                        'archive_time': archive_time,
                    },
                )
            cur.execute(f'VACUUM {"FULL" if vacuum_full else ""} {table_name}')

    @classmethod
    def migrate_legacy(cls):
        from wcs.carddef import CardDef
        from wcs.formdef import FormDef
        from wcs.workflows import ActionsTracingEvolutionPart

        criterias = [StrictNotEqual('status', 'draft')]

        for formdef in itertools.chain(FormDef.select(), CardDef.select()):
            for formdata in formdef.data_class().select_iterator(criterias, itersize=200):
                status_id = None
                changed = False
                for evo in formdata.evolution or []:
                    status_id = evo.status or status_id
                    for part in evo.parts or []:
                        if not isinstance(part, ActionsTracingEvolutionPart):
                            continue
                        changed = True
                        trace = cls(formdata=formdata)
                        trace.event = part.event
                        trace.event_args = {}
                        if part.external_workflow_id:
                            trace.event_args = {
                                'external_workflow_id': part.external_workflow_id,
                                'external_status_id': part.external_status_id,
                                'external_item_id': part.external_item_id,
                            }
                        if part.event_args:
                            if trace.event in ('api-post-edit-action', 'edit-action', 'timeout-jump'):
                                trace.event_args = {'action_item_id': part.event_args[0]}
                            elif trace.event in (
                                'global-api-trigger',
                                'global-external-workflow',
                                'global-interactive-action',
                            ):
                                trace.event_args = {'global_action_id': part.event_args[0]}
                            elif trace.event in ('global-action-timeout',):
                                if isinstance(part.event_args[0], tuple):
                                    # adapt for some old bug
                                    part.event_args = part.event_args[0]
                                trace.event_args = {
                                    'global_action_id': part.event_args[0],
                                    'global_trigger_id': part.event_args[1],
                                }
                            elif trace.event in ('workflow-created',):
                                trace.event_args['display_id'] = part.event_args[0]
                        trace.status_id = status_id
                        trace.timestamp = evo.time
                        trace.store()
                        for action in part.actions or []:
                            trace = cls(formdata=formdata)
                            trace.timestamp = make_aware(action[0], is_dst=True)
                            trace.status_id = status_id
                            trace.action_item_key = action[1]
                            trace.action_item_id = action[2]
                            trace.store()

                    if changed and evo.parts:
                        evo.parts = [x for x in evo.parts if not isinstance(x, ActionsTracingEvolutionPart)]

                if changed:
                    formdata._store_all_evolution = True
                    formdata.store()


class Audit(SqlMixin):
    _table_name = 'audit'
    _table_static_fields = [
        ('id', 'bigserial'),
        ('timestamp', 'timestamptz'),
        ('action', 'varchar'),
        ('url', 'varchar'),
        ('user_id', 'varchar'),
        ('object_type', 'varchar'),
        ('object_id', 'varchar'),
        ('data_id', 'int'),
        ('extra_data', 'jsonb'),
        ('frozen', 'jsonb'),  # plain copy of user email, object name and slug
    ]
    _sql_indexes = [
        'audit_id_idx ON audit USING btree (id)',
    ]

    id = None

    @classmethod
    def do_table(cls):
        _, cur = get_connection_and_cursor()
        table_name = cls._table_name

        cur.execute(
            '''SELECT COUNT(*) FROM information_schema.tables
                        WHERE table_schema = 'public'
                          AND table_name = %s''',
            (table_name,),
        )
        if cur.fetchone()[0] == 0:
            cur.execute(
                '''CREATE TABLE %s (id BIGSERIAL,
                                    timestamp TIMESTAMP WITH TIME ZONE,
                                    action VARCHAR,
                                    url VARCHAR,
                                    user_id VARCHAR,
                                    user_email VARCHAR,
                                    object_type VARCHAR,
                                    object_id VARCHAR,
                                    data_id INTEGER,
                                    extra_data JSONB,
                                    frozen JSONB
                                   )'''
                % table_name
            )
        cur.execute(
            '''SELECT column_name FROM information_schema.columns
                        WHERE table_schema = 'public'
                          AND table_name = %s''',
            (table_name,),
        )
        existing_fields = {x[0] for x in cur.fetchall()}

        needed_fields = {x[0] for x in Audit._table_static_fields}

        # delete obsolete fields
        for field in existing_fields - needed_fields:
            cur.execute('''ALTER TABLE %s DROP COLUMN %s''' % (table_name, field))

        cls.do_indexes(cur)
        cur.close()

    def store(self):
        if self.id:
            # do not allow updates
            raise AssertionError()

        super().store()

    @classmethod
    def get_first_id(cls, clause=None):
        _, cur = get_connection_and_cursor()
        sql_statement = 'SELECT id FROM audit'
        where_clauses, parameters, func_clause = cls.parse_clause(clause)
        assert not func_clause
        if where_clauses:
            sql_statement += ' WHERE ' + ' AND '.join(where_clauses)
        sql_statement += ' ORDER BY id LIMIT 1'
        cur.execute(sql_statement, parameters)
        try:
            first_id = cur.fetchall()[0][0]
        except IndexError:
            first_id = 0
        cur.close()
        return first_id


class Application(SqlMixin):
    _table_name = 'applications'
    _table_static_fields = [
        ('id', 'serial'),
        ('slug', 'varchar'),
        ('name', 'varchar'),
        ('description', 'text'),
        ('documentation_url', 'varchar'),
        ('icon', 'bytea'),
        ('version_number', 'varchar'),
        ('version_notes', 'text'),
        ('editable', 'boolean'),
        ('visible', 'boolean'),
        ('created_at', 'timestamptz'),
        ('updated_at', 'timestamptz'),
    ]
    _sql_indexes = [
        'applications_slug ON applications (slug)',
    ]

    id = None

    @classmethod
    def do_table(cls):
        _, cur = get_connection_and_cursor()
        table_name = cls._table_name

        cur.execute(
            '''SELECT COUNT(*) FROM information_schema.tables
                        WHERE table_schema = 'public'
                          AND table_name = %s''',
            (table_name,),
        )
        if cur.fetchone()[0] == 0:
            cur.execute(
                '''CREATE TABLE %s (id SERIAL PRIMARY KEY,
                                    slug VARCHAR NOT NULL,
                                    name VARCHAR NOT NULL,
                                    description TEXT,
                                    documentation_url VARCHAR,
                                    icon BYTEA,
                                    version_number VARCHAR NOT NULL,
                                    version_notes TEXT,
                                    editable BOOLEAN,
                                    visible BOOLEAN,
                                    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
                                    updated_at TIMESTAMP WITH TIME ZONE NOT NULL
                                   )'''
                % table_name
            )
        cls.do_indexes(cur)
        cur.close()

    def get_sql_dict(self):
        sql_dict = super().get_sql_dict()

        sql_dict['updated_at'] = localtime()
        if not self.id:
            sql_dict['created_at'] = sql_dict['updated_at']
        if self.icon:
            sql_dict['icon'] = bytearray(pickle.dumps(self.icon, protocol=2))

        return sql_dict

    @classmethod
    def _row2ob(cls, row, **kwargs):
        o = cls.__new__(cls)
        for field, value in zip(cls._table_static_fields, tuple(row)):
            if value and field[1] in ('bytea'):
                value = pickle_loads(value)
            setattr(o, field[0], value)
        return o


class ApplicationElement(SqlMixin):
    _table_name = 'application_elements'
    _table_static_fields = [
        ('id', 'serial'),
        ('application_id', 'integer'),
        ('object_type', 'varchar'),
        ('object_id', 'varchar'),
        ('created_at', 'timestamptz'),
        ('updated_at', 'timestamptz'),
    ]
    _sql_indexes = [
        'application_elements_object_idx ON application_elements (object_type, object_id)',
    ]

    id = None

    @classmethod
    def do_table(cls):
        _, cur = get_connection_and_cursor()
        table_name = cls._table_name

        cur.execute(
            '''SELECT COUNT(*) FROM information_schema.tables
                        WHERE table_schema = 'public'
                          AND table_name = %s''',
            (table_name,),
        )
        if cur.fetchone()[0] == 0:
            cur.execute(
                '''CREATE TABLE %s (id SERIAL PRIMARY KEY,
                                    application_id INTEGER NOT NULL,
                                    object_type varchar NOT NULL,
                                    object_id varchar NOT NULL,
                                    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
                                    updated_at TIMESTAMP WITH TIME ZONE NOT NULL
                                   )'''
                % table_name
            )

        cls.do_indexes(cur)
        cls.do_add_constraint(cur)
        cur.close()

    @classmethod
    def do_add_constraint(cls, cur):
        table_name = cls._table_name
        cur.execute(
            '''SELECT COUNT(*) FROM information_schema.constraint_column_usage
                        WHERE table_name = %s
                          AND constraint_name=%s''',
            (table_name, '%s_unique' % table_name),
        )
        if cur.fetchone()[0] == 0:
            cur.execute(
                'ALTER TABLE %s ADD CONSTRAINT %s_unique UNIQUE (application_id, object_type, object_id)'
                % (table_name, table_name)
            )

    @classmethod
    def do_drop_constraint(cls, cur):
        table_name = cls._table_name
        cur.execute(
            '''SELECT COUNT(*) FROM information_schema.constraint_column_usage
                        WHERE table_name = %s
                          AND constraint_name=%s''',
            (table_name, '%s_unique' % table_name),
        )
        if cur.fetchone()[0] != 0:
            cur.execute('ALTER TABLE %s DROP CONSTRAINT %s_unique' % (table_name, table_name))

    def get_sql_dict(self):
        sql_dict = super().get_sql_dict()

        sql_dict['updated_at'] = localtime()
        if not self.id:
            sql_dict['created_at'] = sql_dict['updated_at']

        return sql_dict


class SqlAfterJob(SqlMixin):
    _table_name = 'afterjobs'
    _table_static_fields = [
        ('id', 'uuid'),
        ('class_name', 'varchar'),
        ('status', 'varchar'),
        ('creation_time', 'timestamptz'),
        ('completion_time', 'timestamptz'),
        ('current_count', 'integer'),
        ('total_count', 'integer'),
        ('abort_requested', 'boolean'),
        ('data', 'bytea'),
    ]
    _numerical_id = False
    _sql_indexes = []
    _use_upsert = True

    DO_NOT_STORE = Ellipsis

    def __init__(self, id=None):
        self.id = id

    @classmethod
    def do_table(cls):
        _, cur = get_connection_and_cursor()
        table_name = cls._table_name

        cur.execute(
            '''SELECT COUNT(*) FROM information_schema.tables
                        WHERE table_schema = 'public'
                          AND table_name = %s''',
            (table_name,),
        )
        if cur.fetchone()[0] == 0:
            cur.execute(
                '''CREATE TABLE %s (id UUID PRIMARY KEY,
                                    class_name VARCHAR,
                                    status VARCHAR,
                                    creation_time TIMESTAMP WITH TIME ZONE,
                                    completion_time TIMESTAMP WITH TIME ZONE,
                                    current_count INTEGER,
                                    total_count INTEGER,
                                    abort_requested BOOLEAN DEFAULT FALSE,
                                    data BYTEA
                                   )'''
                % table_name
            )

        cur.execute(
            '''SELECT column_name FROM information_schema.columns
                        WHERE table_schema = 'public'
                          AND table_name = %s''',
            (table_name,),
        )
        existing_columns = {x[0] for x in cur.fetchall()}
        if 'abort_requested' not in existing_columns:
            cur.execute(f'ALTER TABLE {table_name} ADD COLUMN abort_requested BOOLEAN DEFAULT FALSE')

        cls.do_indexes(cur)
        cur.close()

    def store(self, *args, **kwargs):
        if self.id is self.DO_NOT_STORE:
            return
        if self.id:
            self._last_store_time = time.time()
        super().store(*args, **kwargs)

    def refresh_column(self, column_name):
        if self.id and self.id is not self.DO_NOT_STORE:
            _, cur = get_connection_and_cursor()
            table_name = self._table_name
            cur.execute(f'SELECT {column_name} FROM {table_name} WHERE id = %s', (self.id,))
            setattr(self, column_name, cur.fetchone()[0])

    def store_column(self, column_name):
        if self.id is self.DO_NOT_STORE:
            return
        _, cur = get_connection_and_cursor()
        table_name = self._table_name
        value = getattr(self, column_name)
        cur.execute(f'UPDATE {table_name} SET {column_name} = %s WHERE id = %s', (value, self.id))
        if cur.rowcount == 0:
            # if by any chance store_count was called on a never stored afterjob
            # store it fully
            self.store()
        cur.close()

    def store_count(self):
        self.store_column('current_count')

    def store_status(self):
        if self.id is self.DO_NOT_STORE:
            return
        _, cur = get_connection_and_cursor()
        table_name = self._table_name
        cur.execute(
            f'UPDATE {table_name} SET status=%s, completion_time=%s WHERE id=%s',
            (self.status, self.completion_time, self.id),
        )
        if cur.rowcount == 0:
            # if by any chance store_status was called on a never stored afterjob
            # store it fully
            self.store()
        cur.close()

    def get_sql_dict(self):
        return {
            'class_name': self.__class__.__name__,
            'status': self.status,
            'creation_time': self.creation_time,
            'completion_time': self.completion_time,
            'current_count': self.current_count,
            'total_count': self.total_count,
            'abort_requested': self.abort_requested,
            'data': pickle.dumps(self, protocol=2),
        }

    @classmethod
    def _row2ob(cls, row, **kwargs):
        o = pickle_loads(row[-1])  # get full object from data column
        o.status, o.creation_time, o.completion_time, o.current_count, o.total_count, o.abort_requested = (
            tuple(row[2:-1])
        )
        return o

    @classmethod
    def migrate_from_files(cls):
        from wcs.qommon.afterjobs import FileAfterJob

        for afterjob in FileAfterJob.select(ignore_errors=True, ignore_migration=True):
            if len(str(afterjob.id)) != 36:
                # ignore very old jobs
                continue
            # afterjob class will already be the correct one (as it has _reset_class as False),
            # switch timestamps to timezone-aware timestamps and save
            if afterjob.creation_time:
                afterjob.creation_time = make_aware(datetime.datetime.fromtimestamp(afterjob.creation_time))
            if afterjob.completion_time:
                afterjob.completion_time = make_aware(
                    datetime.datetime.fromtimestamp(afterjob.completion_time)
                )
            afterjob.store()


class SqlDataSource(SqlMixin):
    _table_name = 'datasources'
    _table_static_fields = [
        ('id', 'serial'),
        ('slug', 'varchar'),
        ('name', 'varchar'),
        ('documentation', 'text'),
        ('category_id', 'varchar'),
        ('data_source', 'jsonb'),
        ('last_update_time', 'timestamptz'),
        ('cache_duration', 'varchar'),
        ('query_parameter', 'varchar'),
        ('id_parameter', 'varchar'),
        ('data_attribute', 'varchar'),
        ('id_attribute', 'varchar'),
        ('text_attribute', 'varchar'),
        ('id_property', 'varchar'),
        ('qs_data', 'jsonb'),
        ('label_template_property', 'varchar'),
        ('external', 'varchar'),
        ('external_type', 'varchar'),
        ('external_status', 'varchar'),
        ('notify_on_errors', 'boolean'),
        ('record_on_errors', 'boolean'),
        ('users_included_roles', 'text[]'),
        ('users_excluded_roles', 'text[]'),
        ('include_disabled_users', 'boolean'),
    ]
    _sql_indexes = [
        'datasources_slug ON datasources (slug)',
    ]
    _use_upsert = True

    def __init__(self, id=None):
        self.id = id

    @classmethod
    def do_table(cls):
        _, cur = get_connection_and_cursor()
        table_name = cls._table_name

        cur.execute(
            '''SELECT COUNT(*) FROM information_schema.tables
                        WHERE table_schema = 'public'
                          AND table_name = %s''',
            (table_name,),
        )
        if cur.fetchone()[0] == 0:
            cur.execute(
                '''CREATE TABLE %s (id SERIAL PRIMARY KEY,
                                    slug VARCHAR,
                                    name VARCHAR,
                                    documentation TEXT,
                                    category_id VARCHAR,
                                    last_update_time TIMESTAMP WITH TIME ZONE,
                                    data_source JSONB,
                                    cache_duration VARCHAR,
                                    query_parameter VARCHAR,
                                    id_parameter VARCHAR,
                                    data_attribute VARCHAR,
                                    id_attribute VARCHAR,
                                    text_attribute VARCHAR,
                                    id_property VARCHAR,
                                    qs_data JSONB,
                                    label_template_property VARCHAR,
                                    external VARCHAR,
                                    external_type VARCHAR,
                                    external_status VARCHAR,
                                    notify_on_errors BOOLEAN,
                                    record_on_errors BOOLEAN,
                                    users_included_roles TEXT[],
                                    users_excluded_roles TEXT[],
                                    include_disabled_users BOOLEAN
                                   )'''
                % table_name
            )

        cur.execute(
            '''SELECT column_name FROM information_schema.columns
                        WHERE table_schema = 'public'
                          AND table_name = %s''',
            (table_name,),
        )
        existing_fields = {x[0] for x in cur.fetchall()}

        # generic migration for new columns
        for field_name, field_type in cls._table_static_fields:
            if field_name not in existing_fields:
                cur.execute('''ALTER TABLE %s ADD COLUMN %s %s''' % (table_name, field_name, field_type))

        cls.do_indexes(cur)
        cur.close()

    def get_sql_dict(self):
        data = super().get_sql_dict()
        self.last_update_time = data['last_update_time'] = now()
        return data

    @classonlymethod
    def migrate_from_files(cls):
        from wcs.qommon.storage import StorableObject

        class OldNamedDataSourceXml(StorableObject, wcs.data_sources.NamedDataSource):
            _reset_class = False
            XML_NODES = wcs.data_sources.NamedDataSource.XML_NODES

            @classmethod
            def storage_load(cls, fd):
                return cls.import_from_xml(fd, include_id=True, check_deprecated=False)

            def migrate(self):
                pass

        for named_data_source in OldNamedDataSourceXml.select():
            named_data_source.__class__ = cls
            named_data_source.store()
        cls.reset_restart_sequence()

    @classonlymethod
    def wipe(cls, drop=False, clause=None, restart_sequence=True):
        super().wipe(drop=drop, clause=clause, restart_sequence=restart_sequence)


class SqlMailTemplate(SqlMixin):
    _table_name = 'mail_templates'
    _table_static_fields = [
        ('id', 'serial'),
        ('slug', 'varchar'),
        ('name', 'varchar'),
        ('documentation', 'text'),
        ('category_id', 'varchar'),
        ('subject', 'varchar'),
        ('body', 'varchar'),
        ('attachments', 'text[]'),
    ]
    _sql_indexes = [
        'mail_templates_slug ON mail_templates (slug)',
    ]
    _use_upsert = True

    def __init__(self, id=None):
        self.id = id

    @classmethod
    def do_table(cls):
        _, cur = get_connection_and_cursor()
        table_name = cls._table_name

        cur.execute(
            '''SELECT COUNT(*) FROM information_schema.tables
                        WHERE table_schema = 'public'
                          AND table_name = %s''',
            (table_name,),
        )
        if cur.fetchone()[0] == 0:
            cur.execute(
                '''CREATE TABLE %s (id SERIAL PRIMARY KEY,
                                    slug VARCHAR,
                                    name VARCHAR,
                                    documentation TEXT,
                                    category_id VARCHAR,
                                    subject VARCHAR,
                                    body VARCHAR,
                                    attachments TEXT[]
                                   )'''
                % table_name
            )

        cls.do_indexes(cur)
        cur.close()

    @classonlymethod
    def migrate_from_files(cls):
        from wcs.qommon.storage import StorableObject

        class OldMailTemplateXml(StorableObject, wcs.mail_templates.MailTemplate):
            _reset_class = False
            XML_NODES = wcs.mail_templates.MailTemplate.XML_NODES

            @classmethod
            def storage_load(cls, fd):
                return cls.import_from_xml(fd, include_id=True, check_deprecated=False)

            def migrate(self):
                pass

        for mail_template in OldMailTemplateXml.select():
            mail_template.__class__ = cls
            mail_template.store()
        cls.reset_restart_sequence()

    @classonlymethod
    def wipe(cls, drop=False, clause=None, restart_sequence=True):
        super().wipe(drop=drop, clause=clause, restart_sequence=restart_sequence)


class SqlCommentTemplate(SqlMixin):
    _table_name = 'comment_templates'
    _table_static_fields = [
        ('id', 'serial'),
        ('slug', 'varchar'),
        ('name', 'varchar'),
        ('documentation', 'text'),
        ('category_id', 'varchar'),
        ('comment', 'varchar'),
        ('attachments', 'text[]'),
    ]
    _sql_indexes = [
        'comment_templates_slug ON comment_templates (slug)',
    ]
    _use_upsert = True

    def __init__(self, id=None):
        self.id = id

    @classmethod
    def do_table(cls):
        _, cur = get_connection_and_cursor()
        table_name = cls._table_name

        cur.execute(
            '''SELECT COUNT(*) FROM information_schema.tables
                        WHERE table_schema = 'public'
                          AND table_name = %s''',
            (table_name,),
        )
        if cur.fetchone()[0] == 0:
            cur.execute(
                '''CREATE TABLE %s (id SERIAL PRIMARY KEY,
                                    slug VARCHAR,
                                    name VARCHAR,
                                    documentation TEXT,
                                    category_id VARCHAR,
                                    comment VARCHAR,
                                    attachments TEXT[]
                                   )'''
                % table_name
            )

        cls.do_indexes(cur)
        cur.close()

    @classonlymethod
    def migrate_from_files(cls):
        from wcs.qommon.storage import StorableObject

        class OldCommentTemplateXml(StorableObject, wcs.comment_templates.CommentTemplate):
            _reset_class = False
            XML_NODES = wcs.comment_templates.CommentTemplate.XML_NODES

            @classmethod
            def storage_load(cls, fd):
                return cls.import_from_xml(fd, include_id=True, check_deprecated=False)

            def migrate(self):
                pass

        for comment_template in OldCommentTemplateXml.select():
            comment_template.__class__ = cls
            comment_template.store()
        cls.reset_restart_sequence()

    @classonlymethod
    def wipe(cls, drop=False, clause=None, restart_sequence=True):
        super().wipe(drop=drop, clause=clause, restart_sequence=restart_sequence)


class SqlWsCall(SqlMixin):
    _table_name = 'wscalls'
    _table_static_fields = [
        ('id', 'serial'),
        ('slug', 'varchar'),
        ('name', 'varchar'),
        ('documentation', 'text'),
        ('request', 'jsonb'),
        ('notify_on_errors', 'boolean'),
        ('record_on_errors', 'boolean'),
    ]
    _sql_indexes = [
        'wscalls_slug ON wscalls (slug)',
    ]
    _use_upsert = True

    @classmethod
    def do_table(cls):
        _, cur = get_connection_and_cursor()
        table_name = cls._table_name

        cur.execute(
            '''SELECT COUNT(*) FROM information_schema.tables
                        WHERE table_schema = 'public'
                          AND table_name = %s''',
            (table_name,),
        )
        if cur.fetchone()[0] == 0:
            cur.execute(
                '''CREATE TABLE %s (id SERIAL PRIMARY KEY,
                                    slug VARCHAR,
                                    name VARCHAR,
                                    documentation TEXT,
                                    request JSONB,
                                    notify_on_errors BOOLEAN,
                                    record_on_errors BOOLEAN
                                   )'''
                % table_name
            )

        cls.do_indexes(cur)
        cur.close()

    @classonlymethod
    def migrate_from_files(cls):
        from wcs.qommon.storage import StorableObject

        class OldWsCallXml(StorableObject, wcs.wscalls.NamedWsCall):
            _reset_class = False
            XML_NODES = wcs.wscalls.NamedWsCall.XML_NODES

            @classmethod
            def storage_load(cls, fd):
                return cls.import_from_xml(fd, include_id=True, check_deprecated=False)

            def migrate(self):
                pass

        for wscall in OldWsCallXml.select():
            wscall.__class__ = cls
            wscall.id = None
            wscall.store()

    @classonlymethod
    def wipe(cls, drop=False, clause=None, restart_sequence=True):
        super().wipe(drop=drop, clause=clause, restart_sequence=restart_sequence)

    @classmethod
    def migrate_identifiers(cls):
        from wcs.wscalls import NamedWsCall

        for kls in (ApplicationElement, Snapshot):
            for related in kls.select([Equal('object_type', 'wscall')]):
                wscall = NamedWsCall.get_by_slug(related.object_id, ignore_errors=True)
                if wscall:
                    related.object_id = str(wscall.id)
                    related.store()
                else:
                    related.remove_object(related.id)


class SqlCategory(SqlMixin):
    _table_name = 'categories'
    _table_static_fields = [
        ('id', 'serial'),
        ('objects_type', 'varchar'),
        ('slug', 'varchar'),
        ('name', 'varchar'),
        ('description', 'text'),
        ('position', 'int'),
        ('redirect_url', 'varchar'),  # forms category only
        ('export_role_ids', 'text[]'),
        ('statistics_role_ids', 'text[]'),
        ('management_role_ids', 'text[]'),
    ]
    _sql_indexes = [
        'categories_slug ON categories (slug)',
        'categories_objects_type ON categories (objects_type)',
    ]
    _use_upsert = True

    @classmethod
    def do_table(cls):
        _, cur = get_connection_and_cursor()
        table_name = cls._table_name

        cur.execute(
            '''SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'public' AND table_name = %s''',
            (table_name,),
        )
        if cur.fetchone()[0] == 0:
            cur.execute(
                f'''CREATE TABLE {table_name} (
                        id SERIAL PRIMARY KEY,
                        objects_type VARCHAR,
                        slug VARCHAR,
                        name VARCHAR,
                        description TEXT,
                        position INTEGER,
                        redirect_url VARCHAR,
                        export_role_ids TEXT[],
                        statistics_role_ids TEXT[],
                        management_role_ids TEXT[]
                    )'''
            )

        cls.do_indexes(cur)
        cur.close()

    @classmethod
    def get_static_criterias(cls):
        return [Equal('objects_type', cls.objects_type)] if hasattr(cls, 'objects_type') else []

    @classmethod
    def _row2ob(cls, row, **kwargs):
        o = super()._row2ob(row, **kwargs)
        o.id = str(o.id)
        if o.export_role_ids:
            o.export_roles = lambda: get_publisher().role_class.select(
                [Contains('id', o.export_role_ids)], order_by='name'
            )
        else:
            o.export_roles = []
        if o.management_role_ids:
            o.management_roles = lambda: get_publisher().role_class.select(
                [Contains('id', o.management_role_ids)], order_by='name'
            )
        else:
            o.management_roles = []
        if o.statistics_role_ids:
            o.statistics_roles = lambda: get_publisher().role_class.select(
                [Contains('id', o.statistics_role_ids)], order_by='name'
            )
        else:
            o.statistics_roles = []
        return o

    def store(self):
        super().store()
        self.id = str(self.id)

    def get_sql_dict(self):
        base_dict = super().get_sql_dict()
        base_dict['export_role_ids'] = [x.id for x in self.export_roles or []]
        base_dict['management_role_ids'] = [x.id for x in self.management_roles or []]
        base_dict['statistics_role_ids'] = [x.id for x in self.statistics_roles or []]
        return base_dict

    @classonlymethod
    @atomic
    def migrate_from_files(cls):
        import wcs.categories
        from wcs.qommon.storage import StorableObject

        category_types = [
            wcs.categories.Category,
            wcs.categories.CardDefCategory,
            wcs.categories.WorkflowCategory,
            wcs.categories.BlockCategory,
            wcs.categories.MailTemplateCategory,
            wcs.categories.CommentTemplateCategory,
            wcs.categories.DataSourceCategory,
        ]

        cls.wipe()  # leftovers

        conn, cur = get_connection_and_cursor()
        ApplicationElement.do_drop_constraint(cur)

        for table_class in category_types:

            class OldCategory(StorableObject, table_class):
                # noqa pylint: disable=too-many-ancestors
                _names = table_class._names
                _reset_class = False

                @classmethod
                def storage_load(cls, fd):
                    return cls.import_from_xml(fd, include_id=True, check_deprecated=False)

                def migrate(self):
                    pass

            # remap
            id_mappings = {}
            for category in OldCategory.select(order_by='id'):
                old_id = category.id
                category.__class__ = table_class
                category.id = None
                category.slug = category.url_name
                category.export_role_ids = [x.id for x in category.export_roles or []]
                category.management_role_ids = [x.id for x in category.management_roles or []]
                category.statistics_role_ids = [x.id for x in category.statistics_roles or []]
                category.store()
                id_mappings[old_id] = category.id

            if id_mappings:
                category_cases = []
                for old_id, new_id in sorted(id_mappings.items(), key=lambda x: int(x[0]), reverse=True):
                    category_cases.append(f'''WHEN category_id = '{old_id}' THEN '{new_id}' ''')
                case_expression = '''(CASE WHEN category_id IS NULL THEN NULL
                                      %s
                                      ELSE CONCAT('-', category_id) END)''' % ' '.join(
                    category_cases
                )

                cur.execute(
                    f'UPDATE {table_class.get_object_class()._table_name} SET category_id = {case_expression}'
                )

                # update application elements
                cur.execute(
                    f'''DELETE FROM application_elements
                         WHERE object_type = '{table_class.xml_root_node}'
                           AND object_id NOT IN %s''',
                    (tuple(id_mappings.keys()),),
                )
                case_expression = case_expression.replace('category_id', 'object_id')
                cur.execute(
                    f'''UPDATE application_elements
                                   SET object_id = {case_expression}
                                 WHERE object_type = '{table_class.xml_root_node}' '''
                )

        ApplicationElement.do_add_constraint(cur)

        from wcs.formdef import FormDef

        for formdef in FormDef.select():
            if formdef.category_id:
                recreate_trigger(formdef, cur, conn)
                cur.execute(
                    'UPDATE wcs_all_forms SET category_id = %s WHERE formdef_id = %s',
                    (int(formdef.category_id), int(formdef.id)),
                )


class AnyFormData(SqlMixin):
    _table_name = 'wcs_all_forms'
    _formdef_cache = {}
    _has_id = False

    @classproperty
    def _table_static_fields(self):
        from wcs.formdef import FormDef

        if not hasattr(self, '__table_static_fields'):
            fake_formdef = FormDef()
            common_fields = get_view_fields(fake_formdef)
            self.__table_static_fields = [(x[1], x[0]) for x in common_fields]
            self.__table_static_fields.append(('criticality_level', 'criticality_level'))
            self.__table_static_fields.append(('geoloc_base_x', 'geoloc_base_x'))
            self.__table_static_fields.append(('geoloc_base_y', 'geoloc_base_y'))
            self.__table_static_fields.append(('concerned_roles_array', 'concerned_roles_array'))
            self.__table_static_fields.append(('anonymised', 'anonymised'))
        return self.__table_static_fields

    @classmethod
    def get_objects(cls, *args, **kwargs):
        cls._formdef_cache = {}
        return super().get_objects(*args, **kwargs)

    @classmethod
    def _row2ob(cls, row, **kwargs):
        from wcs.formdef import FormDef

        formdef_id = row[1]
        formdef = cls._formdef_cache.setdefault(formdef_id, FormDef.get(formdef_id))
        o = formdef.data_class()()
        for static_field, value in zip(cls._table_static_fields, tuple(row[: len(cls._table_static_fields)])):
            setattr(o, static_field[0], value)
        # [CRITICALITY_2] transform criticality_level back to the expected
        # range (see [CRITICALITY_1])
        levels = len(formdef.workflow.criticality_levels or [0])
        o.criticality_level = levels + o.criticality_level
        # convert back unstructured geolocation to the 'native' formdata format.
        if o.geoloc_base_x is not None:
            o.geolocations = {'base': {'lon': o.geoloc_base_x, 'lat': o.geoloc_base_y}}
        # do not allow storing those partial objects
        o.store = None
        return o

    @classmethod
    def load_all_evolutions(cls, formdatas):
        classes = {}
        for formdata in formdatas:
            if formdata._table_name not in classes:
                classes[formdata._table_name] = []
            classes[formdata._table_name].append(formdata)
        for formdatas in classes.values():
            formdatas[0].load_all_evolutions(formdatas)

    @classmethod
    def counts(cls, clause):
        _, cur = get_connection_and_cursor()
        where_clauses, parameters, dummy = cls.parse_clause(clause)
        sql_statement = f'''SELECT formdef_id, count(*)
                              FROM wcs_all_forms
                             WHERE {' AND '.join(where_clauses)}
                          GROUP BY formdef_id'''
        cur.execute(sql_statement, parameters)
        result = {x[0]: x[1] for x in cur.fetchall()}
        cur.close()
        return result


def get_resolution_times(
    period_start=None,
    period_end=None,
    criterias=None,
    group_by=None,
):
    criterias = criterias or []
    for criteria in criterias:
        criteria.attribute = 'f.%s' % criteria.attribute

    criterias.append(NotNull("f.statistics_data->>'done-datetime'"))
    if period_start:
        criterias.append(GreaterOrEqual('f.receipt_time', period_start))
    if period_end:
        criterias.append(Less('f.receipt_time', period_end))

    where_clauses, params, dummy = SqlMixin.parse_clause(criterias)

    table_name = 'wcs_all_forms'
    group_by_column = group_by or 'NULL'
    sql_statement = f'''
        SELECT
        f.id,
        (f.statistics_data->>'done-datetime')::timestamptz - f.receipt_time as res_time,
        {group_by_column}
        FROM {table_name} f
        WHERE {' AND '.join(where_clauses)}
        ORDER BY res_time
        '''

    _, cur = get_connection_and_cursor()
    with cur:
        cur.execute(sql_statement, params)
        results = cur.fetchall()

    # row[1] will have the resolution time as computed by postgresql
    return [(row[1].total_seconds(), row[2]) for row in results if row[1].total_seconds() >= 0]


def get_period_query(
    period_start=None, include_start=True, period_end=None, include_end=True, criterias=None, parameters=None
):
    from wcs.formdef import FormDef

    clause = [NotNull('receipt_time')]
    table_name = 'wcs_all_forms'
    if criterias:
        formdef_class = FormDef
        for criteria in criterias:
            if criteria.__class__.__name__ == 'Equal' and criteria.attribute == 'formdef_klass':
                formdef_class = criteria.value
                continue

            if (
                formdef_class
                and criteria.__class__.__name__ == 'Equal'
                and criteria.attribute == 'formdef_id'
            ):
                # if there's a formdef_id specified, switch to using the
                # specific table so we have access to all fields
                table_name = get_formdef_table_name(formdef_class.get(criteria.value))
                continue
            clause.append(criteria)
    if period_start:
        if include_start:
            clause.append(GreaterOrEqual('receipt_time', period_start))
        else:
            clause.append(Greater('receipt_time', period_start))
    if period_end:
        if include_end:
            clause.append(LessOrEqual('receipt_time', period_end))
        else:
            clause.append(Less('receipt_time', period_end))
    where_clauses, params, dummy = SqlMixin.parse_clause(clause)
    parameters.update(params)
    statement = ' FROM %s ' % table_name
    statement += ' WHERE ' + ' AND '.join(where_clauses)
    return statement


class SearchableFormDef(SqlMixin):
    _table_name = 'searchable_formdefs'
    _sql_indexes = [
        'searchable_formdefs_fts ON searchable_formdefs USING gin(fts)',
    ]

    @classmethod
    @atomic
    def do_table(cls):
        _, cur = get_connection_and_cursor()

        cur.execute(
            '''SELECT COUNT(*) FROM information_schema.tables
                        WHERE table_schema = 'public'
                          AND table_name = %s''',
            (cls._table_name,),
        )
        if cur.fetchone()[0] == 0:
            cur.execute(
                '''CREATE TABLE %s (id SERIAL PRIMARY KEY,
                                    object_type VARCHAR,
                                    object_id VARCHAR,
                                    timestamp TIMESTAMPTZ DEFAULT NOW(),
                                    fts TSVECTOR)
                '''
                % cls._table_name
            )
            cur.execute(
                'ALTER TABLE %s ADD CONSTRAINT %s_unique UNIQUE (object_type, object_id)'
                % (cls._table_name, cls._table_name)
            )
        cls.do_indexes(cur)

        from wcs.carddef import CardDef
        from wcs.formdef import FormDef

        for objectdef in itertools.chain(
            CardDef.select(ignore_errors=True), FormDef.select(ignore_errors=True)
        ):
            cls.update(obj=objectdef)
        init_search_tokens(cur)
        init_functions()
        cur.close()

    @classmethod
    def update(cls, obj=None, removed_obj_type=None, removed_obj_id=None):
        _, cur = get_connection_and_cursor()

        if removed_obj_id:
            cur.execute(
                'DELETE FROM searchable_formdefs WHERE object_type = %s AND object_id = %s',
                (removed_obj_type, removed_obj_id),
            )
        else:
            cur.execute(
                '''INSERT INTO searchable_formdefs (object_type, object_id, fts)
                                VALUES (%(object_type)s, %(object_id)s,
                                        setweight(to_tsvector(%(fts_a)s), 'A') ||
                                        setweight(to_tsvector(%(fts_b)s), 'B') ||
                                        setweight(to_tsvector(%(fts_c)s), 'C'))
                           ON CONFLICT(object_type, object_id) DO UPDATE
                              SET fts = excluded.fts, timestamp = NOW()
                        ''',
                {
                    'object_type': obj.xml_root_node,
                    'object_id': obj.id,
                    'fts_a': FtsMatch.get_fts_value(obj.name),
                    'fts_b': FtsMatch.get_fts_value(obj.description or ''),
                    'fts_c': FtsMatch.get_fts_value(obj.keywords or ''),
                },
            )
        cur.close()

    @classmethod
    def search(cls, obj_type, string):
        _, cur = get_connection_and_cursor()
        cur.execute(
            'SELECT object_id FROM searchable_formdefs WHERE fts @@ wcs_tsquery(%s, %s)',
            (
                FtsMatch.get_fts_value(string),
                'formdefs',
            ),
        )
        ids = [x[0] for x in cur.fetchall()]
        cur.close()
        return ids


class UsedSamlAssertionId(SqlMixin):
    _table_name = 'used_saml_assertion_id'

    @classmethod
    @atomic
    def do_table(cls):
        _, cur = get_connection_and_cursor()
        if not _table_exists(cur, cls._table_name):
            cur.execute(
                '''
                CREATE TABLE %s(
                    id character varying(256) PRIMARY KEY,
                    expiration_time TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
                )
            '''
                % cls._table_name
            )
        cur.close()

    @classmethod
    def consume_assertion_id(cls, assertion_id, expiration_time):
        _, cur = get_connection_and_cursor()
        cur.execute(
            '''INSERT INTO used_saml_assertion_id (id, expiration_time)
                    VALUES (%s, %s)
                    ON CONFLICT(id) DO NOTHING''',
            (assertion_id, expiration_time),
        )
        consumed = cur.rowcount == 1
        cur.close()
        return consumed


def get_time_aggregate_query(time_interval, query, group_by, function='DATE_TRUNC', group_by_clause=None):
    statement = f"SELECT {function}('{time_interval}', receipt_time) AS {time_interval}, "
    if group_by:
        if group_by_clause:
            statement += group_by_clause
        else:
            statement += '%s, ' % group_by
    statement += 'COUNT(*) '
    statement += query

    aggregate_fields = time_interval
    if group_by:
        aggregate_fields += ', %s' % group_by
    statement += f' GROUP BY {aggregate_fields} ORDER BY {aggregate_fields}'
    return statement


def get_actionable_counts(user_roles):
    _, cur = get_connection_and_cursor()
    criterias = [
        Equal('is_at_endpoint', False),
        Intersects('actions_roles_array', user_roles),
        Null('anonymised'),
    ]
    where_clauses, parameters, dummy = SqlMixin.parse_clause(criterias)
    statement = '''SELECT formdef_id, COUNT(*)
                     FROM wcs_all_forms
                    WHERE %s
                 GROUP BY formdef_id''' % ' AND '.join(
        where_clauses
    )
    cur.execute(statement, parameters)
    counts = {str(x): y for x, y in cur.fetchall()}
    cur.close()
    return counts


def get_total_counts(user_roles):
    _, cur = get_connection_and_cursor()
    criterias = [
        Intersects('concerned_roles_array', user_roles),
        Null('anonymised'),
    ]
    where_clauses, parameters, dummy = SqlMixin.parse_clause(criterias)
    statement = '''SELECT formdef_id, COUNT(*)
                     FROM wcs_all_forms
                    WHERE %s
                 GROUP BY formdef_id''' % ' AND '.join(
        where_clauses
    )
    cur.execute(statement, parameters)
    counts = {str(x): y for x, y in cur.fetchall()}
    cur.close()
    return counts


def get_weekday_totals(
    period_start=None, period_end=None, criterias=None, group_by=None, group_by_clause=None
):
    __, cur = get_connection_and_cursor()
    parameters = {}
    statement = get_period_query(
        period_start=period_start, period_end=period_end, criterias=criterias, parameters=parameters
    )
    statement = get_time_aggregate_query(
        'dow', statement, group_by, function='DATE_PART', group_by_clause=group_by_clause
    )
    cur.execute(statement, parameters)

    result = cur.fetchall()
    result = [(int(x[0]), *x[1:]) for x in result]
    coverage = [x[0] for x in result]
    for weekday in range(7):
        if weekday not in coverage:
            result.append((weekday, 0))
    result.sort(key=lambda x: x[0])

    # add labels,
    weekday_names = [
        _('Sunday'),
        _('Monday'),
        _('Tuesday'),
        _('Wednesday'),
        _('Thursday'),
        _('Friday'),
        _('Saturday'),
    ]
    result = [(weekday_names[x[0]], *x[1:]) for x in result]
    # and move Sunday last
    result = result[1:] + [result[0]]

    cur.close()

    return result


def get_formdef_totals(period_start=None, period_end=None, criterias=None):
    _, cur = get_connection_and_cursor()
    statement = '''SELECT formdef_id, COUNT(*)'''
    parameters = {}
    statement += get_period_query(
        period_start=period_start, period_end=period_end, criterias=criterias, parameters=parameters
    )
    statement += ' GROUP BY formdef_id'
    cur.execute(statement, parameters)

    result = cur.fetchall()
    result = [(int(x), y) for x, y in result]

    cur.close()

    return result


def get_global_totals(
    period_start=None, period_end=None, criterias=None, group_by=None, group_by_clause=None
):
    _, cur = get_connection_and_cursor()
    statement = 'SELECT '
    if group_by:
        if group_by_clause:
            statement += group_by_clause
        else:
            statement += f'{group_by}, '
    statement += 'COUNT(*) '

    parameters = {}
    statement += get_period_query(
        period_start=period_start, period_end=period_end, criterias=criterias, parameters=parameters
    )

    if group_by:
        statement += f' GROUP BY {group_by} ORDER BY {group_by}'
    cur.execute(statement, parameters)

    result = cur.fetchall()
    if not group_by:
        result = [('', result[0][0])]
    cur.close()

    return result


def get_hour_totals(period_start=None, period_end=None, criterias=None, group_by=None, group_by_clause=None):
    _, cur = get_connection_and_cursor()
    parameters = {}
    statement = get_period_query(
        period_start=period_start, period_end=period_end, criterias=criterias, parameters=parameters
    )
    statement = get_time_aggregate_query(
        'hour', statement, group_by, function='DATE_PART', group_by_clause=group_by_clause
    )
    cur.execute(statement, parameters)

    result = cur.fetchall()
    result = [(int(x[0]), *x[1:]) for x in result]

    coverage = [x[0] for x in result]
    for hour in range(24):
        if hour not in coverage:
            result.append((hour, 0))
    result.sort(key=lambda x: x[0])

    cur.close()

    return result


def get_daily_totals(
    period_start=None,
    period_end=None,
    criterias=None,
    group_by=None,
    group_by_clause=None,
):
    _, cur = get_connection_and_cursor()
    parameters = {}
    statement = get_period_query(
        period_start=period_start, period_end=period_end, criterias=criterias, parameters=parameters
    )
    statement = get_time_aggregate_query('day', statement, group_by, group_by_clause=group_by_clause)
    cur.execute(statement, parameters)

    raw_result = cur.fetchall()
    result = [(x[0].strftime('%Y-%m-%d'), *x[1:]) for x in raw_result]
    cur.close()

    return result


def get_monthly_totals(
    period_start=None,
    period_end=None,
    criterias=None,
    group_by=None,
    group_by_clause=None,
):
    _, cur = get_connection_and_cursor()
    parameters = {}
    statement = get_period_query(
        period_start=period_start, period_end=period_end, criterias=criterias, parameters=parameters
    )
    statement = get_time_aggregate_query('month', statement, group_by, group_by_clause=group_by_clause)
    cur.execute(statement, parameters)

    raw_result = cur.fetchall()
    result = [('%d-%02d' % x[0].timetuple()[:2], *x[1:]) for x in raw_result]
    if result:
        coverage = [x[0] for x in result]
        current_month = raw_result[0][0]
        last_month = raw_result[-1][0]
        while current_month < last_month:
            label = '%d-%02d' % current_month.timetuple()[:2]
            if label not in coverage:
                result.append((label, 0))
            current_month = current_month + datetime.timedelta(days=31)
            current_month = current_month - datetime.timedelta(days=current_month.day - 1)
        result.sort(key=lambda x: x[0])

    cur.close()

    return result


def get_yearly_totals(
    period_start=None, period_end=None, criterias=None, group_by=None, group_by_clause=None
):
    _, cur = get_connection_and_cursor()
    parameters = {}
    statement = get_period_query(
        period_start=period_start, period_end=period_end, criterias=criterias, parameters=parameters
    )
    statement = get_time_aggregate_query('year', statement, group_by, group_by_clause=group_by_clause)
    cur.execute(statement, parameters)

    raw_result = cur.fetchall()
    result = [(str(x[0].year), *x[1:]) for x in raw_result]
    if result:
        coverage = [x[0] for x in result]
        current_year = raw_result[0][0]
        last_year = raw_result[-1][0]
        while current_year < last_year:
            label = str(current_year.year)
            if label not in coverage:
                result.append((label, 0))
            current_year = current_year + datetime.timedelta(days=366)
        result.sort(key=lambda x: x[0])

    cur.close()

    return result


def get_period_total(
    period_start=None, include_start=True, period_end=None, include_end=True, criterias=None
):
    _, cur = get_connection_and_cursor()
    statement = '''SELECT COUNT(*)'''
    parameters = {}
    statement += get_period_query(
        period_start=period_start,
        include_start=include_start,
        period_end=period_end,
        include_end=include_end,
        criterias=criterias,
        parameters=parameters,
    )
    cur.execute(statement, parameters)

    result = int(cur.fetchone()[0])

    cur.close()

    return result


# latest migration, number + description (description is not used
# programmaticaly but will make sure git conflicts if two migrations are
# separately added with the same number)
SQL_LEVEL = (168, 'add uuid to formdata')


@atomic
def migrate_map_data_type():
    conn, cur = get_connection_and_cursor()

    from wcs.carddef import CardDef
    from wcs.formdef import FormDef

    had_changes = False
    for formdef in FormDef.select() + CardDef.select():
        table_name = get_formdef_table_name(formdef)
        cur.execute(
            '''SELECT column_name, udt_name FROM information_schema.columns
                        WHERE table_schema = 'public'
                          AND table_name = %s''',
            (table_name,),
        )
        existing_fields = {x[0]: x[1] for x in cur.fetchall()}

        for field in formdef.get_all_fields():
            if field.key != 'map':
                continue
            database_field_id = get_field_id(field)
            if existing_fields.get(database_field_id) == 'jsonb':
                # already ok
                continue
            sql_type = SQL_TYPE_MAPPING.get(field.key, 'varchar')
            cur.execute(
                '''ALTER TABLE %s ADD COLUMN %s %s''' % (table_name, 'tmp_' + database_field_id, sql_type)
            )
            cur.execute(
                '''UPDATE %(table_name)s
                              SET tmp_%(column)s = jsonb_build_object(
                                        'lat', split_part(%(column)s, ';', 1)::float,
                                        'lon', split_part(%(column)s, ';', 2)::float)
                              WHERE %(column)s IS NOT NULL
                                AND %(column)s != ''
                                AND %(column)s SIMILAR TO '[0-9]+(.[0-9]+)?;[0-9]+(.[0-9]+)?'
                                '''
                % {'table_name': table_name, 'column': database_field_id}
            )
            cur.execute('''ALTER TABLE %s DROP COLUMN %s CASCADE''' % (table_name, database_field_id))
            cur.execute(
                '''ALTER TABLE %s RENAME COLUMN tmp_%s TO %s'''
                % (table_name, database_field_id, database_field_id)
            )
            had_changes = True

    if had_changes:
        # views have to be recreated
        migrate_views(conn, cur)

    conn.commit()
    cur.close()


def migrate_global_views(conn, cur):
    drop_global_views(conn, cur)
    do_global_views(conn, cur)


def get_sql_level(conn, cur):
    do_meta_table(conn, cur, insert_current_sql_level=False)
    cur.execute('''SELECT value FROM wcs_meta WHERE key = %s''', ('sql_level',))
    sql_level = int(cur.fetchone()[0])
    return sql_level


def has_needed_reindex():
    _, cur = get_connection_and_cursor()
    cur.execute('''SELECT 1 FROM wcs_meta WHERE key LIKE 'reindex_%' AND value = 'needed' LIMIT 1''')
    row = cur.fetchone()
    cur.close()
    return row is not None


def get_cron_status():
    _, cur = get_connection_and_cursor()
    key = 'cron-status-%s' % get_publisher().tenant.hostname
    cur.execute('SELECT value, updated_at FROM wcs_meta WHERE key = %s', (key,))
    row = cur.fetchone()
    cur.close()
    return tuple(row) if row else (None, None)


@atomic
def get_and_update_cron_status():
    _, cur = get_connection_and_cursor()
    key = 'cron-status-%s' % get_publisher().tenant.hostname
    cur.execute('SELECT value, created_at FROM wcs_meta WHERE key = %s FOR UPDATE', (key,))
    row = cur.fetchone()
    timestamp = now()
    if row is None:
        cur.execute("INSERT INTO wcs_meta (key, value) VALUES (%s, 'running') ON CONFLICT DO NOTHING", (key,))
        if cur.rowcount != 1:
            # since we could not insert, it means somebody else did meanwhile, and thus we can assume it's running
            status = 'running'
        else:
            status = 'done'
    elif row[0] in ('done', 'needed'):  # (needed is legacy)
        cur.execute(
            """UPDATE wcs_meta
                  SET value = 'running', created_at = NOW(), updated_at = NOW()
                WHERE key = %s""",
            (key,),
        )
        status, timestamp = 'done', row[1]
    else:
        status, timestamp = row
    cur.close()
    return (status, timestamp)


def mark_cron_status(status):
    _, cur = get_connection_and_cursor()
    key = 'cron-status-%s' % get_publisher().tenant.hostname
    cur.execute('UPDATE wcs_meta SET value = %s, updated_at = NOW() WHERE key = %s', (status, key))
    cur.close()


def is_reindex_needed(index, conn, cur):
    key_name = 'reindex_%s' % index
    cur.execute('''SELECT value FROM wcs_meta WHERE key = %s''', (key_name,))
    row = cur.fetchone()
    if row is None:
        cur.execute(
            '''INSERT INTO wcs_meta (id, key, value)
                       VALUES (DEFAULT, %s, %s)''',
            (key_name, 'no'),
        )
        return False
    return row[0] == 'needed'


def set_reindex(index, value, conn=None, cur=None):
    own_conn = False
    if not conn:
        own_conn = True
        conn, cur = get_connection_and_cursor()
    do_meta_table(conn, cur, insert_current_sql_level=False)
    key_name = 'reindex_%s' % index
    cur.execute('''SELECT value FROM wcs_meta WHERE key = %s''', (key_name,))
    row = cur.fetchone()
    if row is None:
        cur.execute(
            '''INSERT INTO wcs_meta (id, key, value)
                       VALUES (DEFAULT, %s, %s)''',
            (key_name, value),
        )
    else:
        if row[0] != value:
            cur.execute(
                '''UPDATE wcs_meta SET value = %s, updated_at = NOW() WHERE key = %s''', (value, key_name)
            )
    if own_conn:
        cur.close()


def migrate_views(conn, cur):
    from wcs.carddef import CardDef
    from wcs.formdef import FormDef

    drop_views(None, conn, cur)
    for formdef in FormDef.select() + CardDef.select():
        # make sure all formdefs have up-to-date views
        do_formdef_tables(formdef, conn=conn, cur=cur, rebuild_views=True, rebuild_global_views=False)
    migrate_global_views(conn, cur)


def migrate():
    from wcs.blocks import BlockDef
    from wcs.carddef import CardDef
    from wcs.formdef import FormDef
    from wcs.workflows import Workflow

    conn, cur = get_connection_and_cursor()
    sql_level = get_sql_level(conn, cur)
    if sql_level < 0:
        # fake code to help in testing the error code path.
        raise RuntimeError()
    if sql_level < 160:
        # 160: add cast_to_int function
        init_functions(cur)
    if sql_level < 1:  # 1: introduction of tracking_code table
        do_tracking_code_table()
    if sql_level < 167:
        # 133: move afterjobs to postgresql
        # 167: add abort_requested column to after jobs
        SqlAfterJob.do_table()
        SqlAfterJob.migrate_from_files()
    if sql_level < 134:
        # 134: move data sources to postgresql
        SqlDataSource.do_table()
        SqlDataSource.migrate_from_files()
    if sql_level < 166:
        # 166: add external_type column to data source
        SqlDataSource.do_table()
    if sql_level < 135:
        # 135: move mail templates to postgresql
        SqlMailTemplate.do_table()
        SqlMailTemplate.migrate_from_files()
    if sql_level < 136:
        # 136: move comment templates to postgresql
        SqlCommentTemplate.do_table()
        SqlCommentTemplate.migrate_from_files()
    if sql_level < 137:
        # 137: move wscalls to postgresql
        SqlWsCall.do_table()
        SqlWsCall.migrate_from_files()
    if sql_level < 153:
        # 42: create snapshots table
        # 54: add patch column
        # 63: add index
        # 83: add test_result table
        # 93: add application columns in snapshot table
        # 122: rename test_result table to test_results
        # 126: add application_ignore_change column to snapshots
        # 153: add deleted_object column to snapshots
        do_snapshots_table()
    if sql_level < 50:
        # 49: store Role in SQL
        # 50: switch role uuid column to varchar
        do_role_table()
        migrate_legacy_roles()
    if sql_level < 132:
        # 131: move carddef/formdef/blockdef to postgresql
        # 132: move workflows to postgresql
        for klass in (FormDef, CardDef, BlockDef, Workflow):
            klass.do_table()
            klass.migrate_from_files()
    if sql_level < 165:
        # 165: migrate categories to database
        SqlCategory.do_table()
    if sql_level < 119:
        # 47: store LoggedErrors in SQL
        # 48: remove acked attribute from LoggedError
        # 53: add kind column to logged_errors table
        # 106: add context column to logged_errors table
        # 116: add deleted_timestamp to logged errors table
        # 119: add documentation column to logged errors table
        do_loggederrors_table()
    if sql_level < 109:
        # 3: introduction of _structured for user fields
        # 4: removal of identification_token
        # 12: (first part) add fts to users
        # 16: add verified_fields to users
        # 21: (first part) add ascii_name to users
        # 39: add deleted_timestamp
        # 40: add is_active to users
        # 65: index users(name_identifiers)
        # 85: remove anonymous column
        # 94: add preferences column to users table
        # 107: add test_uuid column to users table
        # 109: add various indexes
        do_user_table()
    if sql_level < 121:
        # 25: create session_table
        # 32: add last_update_time column to session table
        # 121: add more attributes as db columns for sessions
        do_session_table()
    if sql_level < 64:
        # 64: add transient data table
        do_transient_data_table()
    if sql_level < 120:
        # 120: add index to transient data table
        TransientData.do_indexes(cur)
    if sql_level < 155:
        # 37: create custom_views table
        # 44: add is_default column to custom_views table
        # 66: index the formdef_id column
        # 90: add role_id to custom views
        # 92: add group_by column to custom views
        # 109: add various indexes
        # 141: migrate custom views to use a serial id
        # 155: add author_id column to custom views
        do_custom_views_table()
    if sql_level < 67:
        # 57: store tokens in SQL
        # 67: re-migrate legacy tokens
        do_tokens_table()
        migrate_legacy_tokens()
    if sql_level < 100:
        # 68: multilinguism
        # 79: add translatable column to TranslatableMessage table
        # 100: always create translation messages table
        TranslatableMessage.do_table()
    if sql_level < 159:
        # 72: add testdef table
        # 87: add testdef is_in_backoffice column
        # 88: add testdef expected_error column
        # 103: drop testdef slug column
        # 104: add testdef agent_id column
        # 107: add test_uuid column to users table
        # 112: add query_parameters column to TestDef table
        # 113: add frozen_submission_datetime column to TestDef table
        # 128: add links between testdefs
        # 143: add workflow_options column to testdef
        # 159: add testdef submission_agent_uuid column
        TestDef.do_table()
    if sql_level < 95:
        # 95: add a searchable_formdefs table
        SearchableFormDef.do_table()
    if sql_level < 149:
        # 88: add testdef expected_error column
        # 107: add test_uuid column to users table
        # 115: fix duplicated test action uuid
        # 127: remove orphan tests
        # 128: add links between testdefs
        # 149: fix duplicated testdef uuids
        set_reindex('testdef', 'needed', conn=conn, cur=cur)
    if sql_level < 150:
        # 75: migrate to dedicated workflow traces table
        # 76: add index to workflow traces table
        # 142: move test formdata to new tables
        # 150: create workflow_traces_archive table
        WorkflowTrace.do_table()
    if sql_level < 78:
        # 78: add audit table
        Audit.do_table()
    if sql_level < 161:
        # 83: add test_result table
        # 89: rerun creation of test results table
        # 122: rename test_result table to test_results
        # 124: allow null values in TestResult.success column
        # 138: remove old test json columns
        # 161: add test results coverage column
        TestResults.do_table()
    if sql_level < 123:
        # 123: create and populate test_result table
        TestResult.do_table()
        TestResults.do_table()
        set_reindex('test_results', 'needed', conn=conn, cur=cur)
    if sql_level < 138:
        # 125: add test_result/formdata links
        # 128: add links between testdefs
        # 138: remove old test json columns
        TestResult.do_table()
    if sql_level < 156:
        # 142: move test formdata to new tables
        # 144: move test formdata from deleted tests to test tables
        # 146: move test formdata created in workflow to test tables
        # 156: move test formdata created by cascade in workflow to test tables
        set_reindex('test_result', 'needed', conn=conn, cur=cur)
    if sql_level < 145:
        # 105: change test result json structure
        # 145: remove orphan test results
        set_reindex('test_results', 'needed', conn=conn, cur=cur)
    if sql_level < 164:
        # 164: set test user name identifier
        set_reindex('test_user', 'needed', conn=conn, cur=cur)
    if sql_level < 84:
        # 84: add application tables
        Application.do_table()
        ApplicationElement.do_table()
    if sql_level < 118:
        # 118: change map columns to jsonb
        migrate_map_data_type()
    if sql_level < 52:
        # 2: introduction of formdef_id in views
        # 5: add concerned_roles_array, is_at_endpoint and fts to views
        # 7: add backoffice_submission to tables
        # 8: add submission_context to tables
        # 9: add last_update_time to views
        # 10: add submission_channel to tables
        # 11: add formdef_name and user_name to views
        # 13: add backoffice_submission to views
        # 14: add criticality_level to tables & views
        # 15: add geolocation to formdata
        # 19: add geolocation to views
        # 20: remove user hash stuff
        # 22: rebuild views
        # 26: add digest to formdata
        # 27: add last_jump_datetime in evolutions tables
        # 31: add user_label to formdata
        # 33: add anonymised field to global view
        # 38: extract submission_agent_id to its own column
        # 43: add prefilling_data to formdata
        # 52: store digests on formdata and carddata
        migrate_views(conn, cur)
    if sql_level < 6:
        # 6: add actions_roles_array to tables and views
        migrate_views(conn, cur)
        for formdef in FormDef.select():
            formdef.data_class().rebuild_security()
    if sql_level < 62:
        # 12: (second part), store fts in existing rows
        # 21: (second part), store ascii_name of users
        # 23: (first part), use misc.simplify() over full text queries
        # 61: use setweight on formdata & user indexation
        # 62: use setweight on formdata & user indexation (reapply)
        set_reindex('user', 'needed', conn=conn, cur=cur)
    if sql_level < 162:
        # 17: store last_update_time in tables
        # 18: add user name to full-text search index
        # 21: (third part), add user ascii_names to full-text index
        # 23: (second part) use misc.simplify() over full text queries
        # 28: add display id and formdef name to full-text index
        # 29: add evolution parts to full-text index
        # 31: add user_label to formdata
        # 38: extract submission_agent_id to its own column
        # 41: update full text normalization
        # 51: add index on formdata blockdef fields
        # 55: update full text normalisation (switch to unidecode)
        # 58: add workflow_merged_roles_dict as a jsonb column with
        #     combined formdef and formdata value.
        # 61: use setweight on formdata & user indexation
        # 62: use setweight on formdata & user indexation (reapply)
        # 96: change to fts normalization
        # 114: update digests missing after import
        # 154: reindex data to remove content snapshot texts
        # 162: set done datetime in statistics data
        set_reindex('formdata', 'needed', conn=conn, cur=cur)
    if sql_level < 157:
        # 24: add index on evolution(formdata_id)
        # 35: add indexes on formdata(receipt_time) and formdata(anonymised)
        # 36: add index on formdata(user_id)
        # 45 & 46: add index on formdata(status)
        # 56: add GIN indexes to concerned_roles_array and actions_roles_array
        # 74: (late migration) change evolution index to be on (fomdata_id, id)
        # 97&98: add index on carddata/id_display
        # 99: add more indexes
        # 111: add digests->default index on cards
        # 147: add workflow_processing_timestamp index on cards/forms
        # 157: add index to item fields using carddefs
        set_reindex('sqlindexes', 'needed', conn=conn, cur=cur)
    if sql_level < 168:
        # 168: add uuid to formdata
        set_reindex('form_uuid', 'needed', conn=conn, cur=cur)
    if sql_level < 30:
        # 30: actually remove evo.who on anonymised formdatas
        for formdef in FormDef.select():
            for formdata in formdef.data_class().select_iterator(clause=[NotNull('anonymised')]):
                if formdata.evolution:
                    for evo in formdata.evolution:
                        evo.who = None
                    formdata.store()
    if sql_level < 52:
        # 52: store digests on formdata and carddata
        for formdef in FormDef.select() + CardDef.select():
            if not formdef.digest_templates:
                continue
            for formdata in formdef.data_class().select_iterator():
                formdata._set_auto_fields(cur)  # build digests
    if sql_level < 140:
        # 58: add workflow_merged_roles_dict as a jsonb column with
        #     combined formdef and formdata value.
        # 69: add auto_geoloc field to form/card tables
        # 80: add jsonb column to hold statistics data
        # 91: add jsonb column to hold relations data
        # 102: switch formdata datetime columns to timestamptz
        # 140: add workflow_processing_{afterjob_id,timestamp} to card/form data
        drop_views(None, conn, cur)
        for formdef in FormDef.select() + CardDef.select():
            do_formdef_tables(formdef, rebuild_views=False, rebuild_global_views=False)
        migrate_views(conn, cur)
    if sql_level < 109:
        # 81: add statistics data column to wcs_all_forms
        # 82: add statistics data column to wcs_all_forms, for real
        # 99: add more indexes
        # 102: switch formdata datetime columns to timestamptz
        # 109: add various indexes
        migrate_global_views(conn, cur)
    if sql_level < 60:
        # 59: switch wcs_all_forms to a trigger-maintained table
        # 60: rebuild triggers
        init_global_table(conn, cur)
        for formdef in FormDef.select():
            do_formdef_tables(formdef, rebuild_views=False, rebuild_global_views=False)

    if sql_level < 73:
        # 73: form tokens to db
        # it uses the existing tokens table, this "migration" is just to remove old files.
        form_tokens_dir = os.path.join(get_publisher().app_dir, 'form_tokens')
        if os.path.exists(form_tokens_dir):
            shutil.rmtree(form_tokens_dir, ignore_errors=True)

    if sql_level < 75:
        # 75 (part 2): migrate to dedicated workflow traces table
        set_reindex('workflow_traces_migration', 'needed', conn=conn, cur=cur)

    if sql_level < 77:
        # 77: use token table for nonces
        # it uses the existing tokens table, this "migration" is just to remove old files.
        nonces_dir = os.path.join(get_publisher().app_dir, 'nonces')
        if os.path.exists(nonces_dir):
            shutil.rmtree(nonces_dir, ignore_errors=True)

    if sql_level < 86:
        # 86: add uuid to cards
        for formdef in CardDef.select():
            do_formdef_tables(formdef, rebuild_views=False, rebuild_global_views=False)

    if sql_level < 168:
        # 168: add uuid to formdata
        for formdef in FormDef.select():
            do_formdef_tables(formdef, rebuild_views=False, rebuild_global_views=False)

    if sql_level < 142:
        # 101: add page_id to formdatas
        # 125: add test_result/formdata links
        # 142: move test formdata to new tables
        for formdef in FormDef.select() + CardDef.select():
            do_formdef_tables(formdef, rebuild_views=False, rebuild_global_views=False)

    if sql_level < 163:
        # 108: new fts mechanism with tokens table
        # 110: add cards to fts mechanism
        # 117: add context to tokens table
        # 158: remove exact match case from wcs_tsquery
        # 163: make wcs_tsquery() immutable
        init_search_tokens()

    if sql_level < 117:
        # 108: new fts mechanism with tokens table
        # 110: add cards to fts mechanism
        # 117: add context to tokens table
        set_reindex('init_search_tokens_data', 'needed', conn=conn, cur=cur)

    if sql_level < 129:
        # 129: create saml_assertion table
        UsedSamlAssertionId.do_table()

    if sql_level < 130:
        # 130: create apiaccess table
        ApiAccess.do_table()
        ApiAccess.migrate_from_files()

    if sql_level < 139:
        # 139: migrate wscall identifiers in related objects
        SqlWsCall.migrate_identifiers()

    if sql_level < 148:
        # 148: delete broken snapshots
        Snapshot.delete_broken_snapshots()

    if sql_level < 152:
        # 150 (part 2): create workflow_traces_archive table
        # 151: run workflow_traces_to_archive again
        # 152: run workflow_traces_to_archive again (bis)
        set_reindex('workflow_traces_to_archive', 'needed', conn=conn, cur=cur)

    if sql_level < 165:
        # 165: migrate categories to database
        SqlCategory.migrate_from_files()

    if sql_level != SQL_LEVEL[0]:
        cur.execute(
            '''UPDATE wcs_meta SET value = %s, updated_at=NOW() WHERE key = %s''',
            (str(SQL_LEVEL[0]), 'sql_level'),
        )

    cur.close()


def reindex():
    from wcs.carddef import CardDef
    from wcs.formdef import FormDef

    conn, cur = get_connection_and_cursor()

    if is_reindex_needed('sqlindexes', conn=conn, cur=cur):
        cur.execute('CREATE EXTENSION IF NOT EXISTS pg_trgm')
        for klass in (
            SqlUser,
            Session,
            CustomView,
            Snapshot,
            LoggedError,
            TranslatableMessage,
            WorkflowTrace,
            Audit,
            Application,
            ApplicationElement,
        ):
            klass.do_indexes(cur, concurrently=True)
        for formdef in FormDef.select() + CardDef.select():
            do_formdef_indexes(formdef, cur=cur, concurrently=True)
        set_reindex('sqlindexes', 'done', conn=conn, cur=cur)

    if is_reindex_needed('user', conn=conn, cur=cur):
        for user in SqlUser.select(iterator=True):
            user.store()
        set_reindex('user', 'done', conn=conn, cur=cur)

    if is_reindex_needed('formdata', conn=conn, cur=cur):
        # load and store all formdatas
        for formdef in FormDef.select() + CardDef.select():
            for formdata in formdef.data_class().select(iterator=True):
                try:
                    formdata.migrate()
                    formdata.store()
                except Exception as e:
                    print('error reindexing %s (%r)' % (formdata, e))
        set_reindex('formdata', 'done', conn=conn, cur=cur)

    if is_reindex_needed('workflow_traces_migration', conn=conn, cur=cur):
        WorkflowTrace.migrate_legacy()
        set_reindex('workflow_traces_migration', 'done', conn=conn, cur=cur)

    if is_reindex_needed('workflow_traces_to_archive', conn=conn, cur=cur):
        WorkflowTrace.archive(vacuum_full=True)
        set_reindex('workflow_traces_to_archive', 'done', conn=conn, cur=cur)

    if is_reindex_needed('testdef', conn=conn, cur=cur):
        TestDef.migrate_legacy()
        set_reindex('testdef', 'done', conn=conn, cur=cur)

    if is_reindex_needed('test_results', conn=conn, cur=cur):
        TestResults.migrate_legacy()
        set_reindex('test_results', 'done', conn=conn, cur=cur)

    if is_reindex_needed('test_result', conn=conn, cur=cur):
        TestResult.migrate_legacy()
        set_reindex('test_result', 'done', conn=conn, cur=cur)

    if is_reindex_needed('test_user', conn=conn, cur=cur):
        TestUser.migrate_legacy()
        set_reindex('test_user', 'done', conn=conn, cur=cur)

    if is_reindex_needed('init_search_tokens_data', conn=conn, cur=cur):
        init_search_tokens_data(cur)
        set_reindex('init_search_tokens_data', 'done', conn=conn, cur=cur)

    if is_reindex_needed('form_uuid', conn=conn, cur=cur):
        for formdef in FormDef.select():
            cur.execute(
                'UPDATE %s SET uuid = gen_random_uuid() WHERE uuid IS NULL' % get_formdef_table_name(formdef)
            )
        set_reindex('form_uuid', 'done', conn=conn, cur=cur)

    cur.close()


def formdef_remap_statuses(formdef, mapping):
    table_name = get_formdef_table_name(formdef)
    evolutions_table_name = table_name + '_evolutions'
    unmapped_status_suffix = str(formdef.workflow_id or 'default')

    # build the case expression
    status_cases = []
    for old_id, new_id in mapping.items():
        status_cases.append(
            SQL('WHEN status = {old_status} THEN {new_status}').format(
                old_status=Literal(old_id), new_status=Literal(new_id)
            )
        )
    case_expression = SQL(
        '(CASE WHEN status IS NULL THEN NULL '
        '{status_cases} '
        # keep status alread marked as invalid
        'WHEN status LIKE {pattern} THEN status '
        # mark unknown statuses as invalid
        'ELSE (status || {suffix}) END)'
    ).format(
        status_cases=SQL('').join(status_cases),
        pattern=Literal('%-invalid-%'),
        suffix=Literal('-invalid-' + unmapped_status_suffix),
    )

    _, cur = get_connection_and_cursor()
    # update formdatas statuses
    cur.execute(
        SQL('UPDATE {table_name} SET status = {case_expression} WHERE status <> {draft_status}').format(
            table_name=Identifier(table_name), case_expression=case_expression, draft_status=Literal('draft')
        )
    )
    # update evolutions statuses
    cur.execute(
        SQL('UPDATE {table_name} SET status = {case_expression}').format(
            table_name=Identifier(evolutions_table_name), case_expression=case_expression
        )
    )
    cur.close()
