import datetime
import decimal
import io
import os
import pickle
import random
import shutil
import string
import time
import zipfile

import psycopg2
import pytest
from django.utils.timezone import localtime, make_aware
from django.utils.timezone import now as tz_now

import wcs.sql_criterias as st
from wcs import fields, sql
from wcs.applications import ApplicationElement
from wcs.blocks import BlockDef
from wcs.carddef import CardDef
from wcs.categories import CardDefCategory, Category
from wcs.data_sources import NamedDataSource
from wcs.formdata import Evolution
from wcs.formdef import FormDef
from wcs.logged_errors import LoggedError
from wcs.qommon import force_str
from wcs.qommon.afterjobs import AfterJob
from wcs.testdef import TestDef, TestResult, TestResults
from wcs.wf.register_comment import RegisterCommenterWorkflowStatusItem
from wcs.workflow_tests import AssertStatus, WorkflowTests
from wcs.workflow_traces import TestWorkflowTrace, WorkflowTrace
from wcs.workflows import ActionsTracingEvolutionPart, Workflow, WorkflowCriticalityLevel
from wcs.wscalls import NamedWsCall

from .utilities import clean_temporary_pub, create_temporary_pub


@pytest.fixture
def pub(request):
    return create_temporary_pub()


@pytest.fixture
def pub_with_views(pub):
    pub.load_site_options()
    if not pub.site_options.has_section('options'):
        pub.site_options.add_section('options')
    pub.site_options.set('options', 'sql-create-formdef-views', 'true')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)
    return pub


@pytest.fixture
def formdef(pub):
    BlockDef.wipe()
    FormDef.wipe()

    block = BlockDef()
    block.name = 'fooblock'
    block.fields = [
        fields.StringField(id='1', label='string'),
        fields.ItemField(id='2', label='item', items=('boat', 'plane', 'kick scooter')),
    ]
    block.store()

    formdef = FormDef()
    formdef.name = 'tests'
    formdef.fields = [
        fields.StringField(id='0', label='string'),
        fields.EmailField(id='1', label='email'),
        fields.TextField(id='2', label='text'),
        fields.BoolField(id='3', label='bool'),
        fields.ItemField(id='4', label='item', items=('apple', 'pear', 'peach', 'apricot')),
        fields.DateField(id='5', label='date'),
        fields.ItemsField(id='6', label='items', items=('apple', 'pear', 'peach', 'apricot')),
        fields.BlockField(id='7', label='block', block_slug='fooblock'),
        fields.NumericField(id='8', lable='numeric'),
    ]
    formdef.store()
    return formdef


def teardown_module(module):
    clean_temporary_pub()


def test_sql_table_name_invalid_chars(pub):
    test_formdef = FormDef()
    test_formdef.name = 'test-some|char;are better/ignored'
    test_formdef.fields = []
    test_formdef.store()
    assert test_formdef.table_name is not None
    data_class = test_formdef.data_class(mode='sql')
    assert data_class.count() == 0


def test_sql_data_class(formdef):
    formdef.data_class(mode='sql')


def test_sql_count(formdef):
    data_class = formdef.data_class(mode='sql')
    assert data_class.count() == 0


def test_sql_store(formdef):
    data_class = formdef.data_class(mode='sql')
    formdata = data_class()
    formdata.status = 'wf-0'
    formdata.user_id = '5'
    formdata.store()
    assert formdata.id


def test_sql_get(formdef):
    data_class = formdef.data_class(mode='sql')
    formdata = data_class()
    formdata.status = 'wf-0'
    formdata.user_id = '5'
    formdata.store()
    id = formdata.id

    formdata = data_class.get(id)
    assert formdata.user_id == '5'
    assert formdata.status == 'wf-0'

    assert data_class.get('foo', ignore_errors=True) is None
    assert data_class.get(True, ignore_errors=True) is None
    assert data_class.get(False, ignore_errors=True) is None
    assert data_class.get(2**32, ignore_errors=True) is None

    with pytest.raises(KeyError):
        data_class.get(23484557288545099215749635047766)


def test_sql_store_channel(formdef):
    data_class = formdef.data_class(mode='sql')
    formdata = data_class()
    formdata.status = 'wf-0'
    formdata.user_id = '5'
    formdata.submission_channel = 'mail'
    formdata.store()

    assert data_class.get(formdata.id).submission_channel == 'mail'

    formdata.submission_channel = None
    formdata.store()
    assert data_class.get(formdata.id).submission_channel is None


def test_sql_get_missing(formdef):
    data_class = formdef.data_class(mode='sql')
    with pytest.raises(KeyError):
        data_class.get(123456)
    with pytest.raises(KeyError):
        data_class.get('xxx')


def test_sql_get_missing_ignore_errors(formdef):
    data_class = formdef.data_class(mode='sql')
    assert data_class.get(123456, ignore_errors=True) is None
    assert data_class.get('xxx', ignore_errors=True) is None
    assert data_class.get(None, ignore_errors=True) is None


def check_sql_field(formdef, no, value, display=False):
    data_class = formdef.data_class(mode='sql')
    formdata = data_class()
    formdata.data = {no: value}
    if display:
        formdata.data['%s_display' % no] = value
    formdata.store()
    id = formdata.id

    formdata = data_class.get(id)
    assert formdata.data.get(no) == value


def test_sql_field_string(formdef):
    check_sql_field(formdef, '0', 'hello world')
    check_sql_field(formdef, '0', 'élo world')
    check_sql_field(formdef, '0', None)


def test_sql_field_email(formdef):
    check_sql_field(formdef, '1', 'fred@example.com')


def test_sql_field_text(formdef):
    check_sql_field(formdef, '2', 'long text')
    check_sql_field(formdef, '2', 'long tèxt')


def test_sql_field_bool(formdef):
    check_sql_field(formdef, '3', False)
    check_sql_field(formdef, '3', True)


def test_sql_field_numeric(formdef):
    check_sql_field(formdef, '8', 6)
    check_sql_field(formdef, '8', decimal.Decimal('4.5'))
    check_sql_field(formdef, '8', -2)
    assert [x.data['8'] for x in formdef.data_class().select(order_by='f8')] == [
        decimal.Decimal('-2'),
        decimal.Decimal('4.5'),
        decimal.Decimal('6'),
    ]


def test_sql_field_numeric_in_block(pub):
    BlockDef.wipe()
    FormDef.wipe()

    block = BlockDef()
    block.name = 'fooblock'
    block.fields = [
        fields.NumericField(id='1', label='numeric'),
    ]
    block.store()

    formdef = FormDef()
    formdef.name = 'tests'
    formdef.fields = [
        fields.BlockField(id='1', label='block1', block_slug='fooblock'),
        fields.BlockField(id='2', label='block2', block_slug='fooblock'),
    ]
    formdef.store()

    formdef.data_class().wipe()
    formdata = formdef.data_class()()
    formdata.data = {
        '1': {
            'data': [{'1': 123}],
            'schema': {'1': 'numeric'},
        },
        '2': {
            'data': [{'1': decimal.Decimal('4.5')}],
            'schema': {'1': 'numeric'},
        },
    }
    formdata.just_created()
    formdata.store()

    formdata = formdef.data_class().get(formdata.id)
    assert formdata.data == {
        '1': {
            'data': [{'1': decimal.Decimal('123')}],
            'schema': {'1': 'numeric'},
        },
        '1_display': None,
        '2': {
            'data': [{'1': decimal.Decimal('4.5')}],
            'schema': {'1': 'numeric'},
        },
        '2_display': None,
    }


def test_sql_field_item(formdef):
    check_sql_field(formdef, '4', 'apricot', display=True)


def test_sql_field_date(formdef):
    check_sql_field(formdef, '5', datetime.date.today().timetuple())


def test_sql_field_items(formdef):
    check_sql_field(formdef, '6', ['apricot'], display=True)
    check_sql_field(formdef, '6', ['apricot', 'pear'], display=True)
    check_sql_field(formdef, '6', ['pomme', 'poire', 'pêche'], display=True)


def test_sql_block_field_text(formdef):
    check_sql_field(formdef, '7', {'data': [{'1': 'foo'}, {'1': 'bar'}]})


def test_sql_block_field_item(formdef):
    check_sql_field(
        formdef,
        '7',
        {
            'data': [
                {'2': 'boat', '2_display': 'Yacht'},
                {'2': 'plane', '2_display': 'Cessna'},
            ]
        },
    )


def test_sql_item_carddef_index(pub):
    carddef = CardDef()
    carddef.name = 'card'
    carddef.store()

    formdef = FormDef()
    formdef.name = 'form' + 'x' * 80  # long name
    formdef.fields = [
        fields.ItemField(id=formdef.get_new_field_id(), label='item', items=['a', 'b', 'c']),
    ]
    formdef.store()
    field_id = sql.get_field_id(formdef.fields[0])
    _, cur = sql.get_connection_and_cursor()
    assert not index_exists(cur, f'{formdef.table_name}_auto_{field_id}_idx')

    formdef.fields[0].data_source = {'type': 'carddef:card'}
    formdef.store()
    assert index_exists(cur, f'{formdef.table_name}_auto_{field_id}_idx')


def test_sql_geoloc(pub):
    test_formdef = FormDef()
    test_formdef.name = 'geoloc'
    test_formdef.fields = []
    test_formdef.geolocations = {'base': 'Plop'}
    test_formdef.store()
    data_class = test_formdef.data_class(mode='sql')
    formdata = data_class()
    formdata.data = {}
    formdata.store()  # NULL geolocation
    formdata2 = data_class.get(formdata.id)
    assert not formdata2.geolocations

    formdata.geolocations = {'base': {'lat': 12, 'lon': 21}}
    formdata.store()

    formdata2 = data_class.get(formdata.id)
    assert formdata2.geolocations == formdata.geolocations

    formdata.geolocations = {}
    formdata.store()
    formdata2 = data_class.get(formdata.id)
    assert formdata2.geolocations == formdata.geolocations


def test_sql_multi_geoloc(pub):
    test_formdef = FormDef()
    test_formdef.name = 'geoloc'
    test_formdef.fields = []
    test_formdef.geolocations = {'base': 'Plop'}
    test_formdef.store()
    data_class = test_formdef.data_class(mode='sql')
    formdata = data_class()
    formdata.data = {}
    formdata.geolocations = {'base': {'lat': 12, 'lon': 21}}
    formdata.store()

    formdata2 = data_class.get(formdata.id)
    assert formdata2.geolocations == formdata.geolocations

    test_formdef.geolocations = {'base': 'Plop', '2nd': 'XXX'}
    test_formdef.store()
    formdata.geolocations = {'base': {'lat': 12, 'lon': 21}, '2nd': {'lat': -34, 'lon': -12}}
    formdata.store()
    formdata2 = data_class.get(formdata.id)
    assert formdata2.geolocations == formdata.geolocations

    test_formdef.geolocations = {'base': 'Plop'}
    test_formdef.store()
    formdata2 = data_class.get(formdata.id)
    assert formdata2.geolocations == {'base': {'lat': 12, 'lon': 21}}


def test_sql_change(formdef):
    data_class = formdef.data_class(mode='sql')
    formdata = data_class()
    formdata.data = {'0': 'test'}
    formdata.store()
    id = formdata.id

    formdata = data_class.get(id)
    assert formdata.data.get('0') == 'test'

    formdata.data = {'0': 'test2'}
    formdata.store()
    formdata = data_class.get(id)
    assert formdata.data.get('0') == 'test2'


def test_sql_remove(formdef):
    data_class = formdef.data_class(mode='sql')
    formdata = data_class()
    formdata.data = {'0': 'test'}
    formdata.store()
    id = formdata.id

    formdata = data_class.get(id)
    assert formdata.data.get('0') == 'test'

    formdata.remove_self()
    with pytest.raises(KeyError):
        data_class.get(id)


def test_sql_wipe(formdef):
    data_class = formdef.data_class(mode='sql')
    formdata = data_class()
    formdata.store()

    assert data_class.count() != 0
    data_class.wipe()
    assert data_class.count() == 0


def test_sql_evolution(formdef):
    data_class = formdef.data_class(mode='sql')
    formdata = data_class()
    formdata.just_created()
    formdata.store()
    id = formdata.id

    formdata = data_class.get(id)
    assert len(formdata.evolution) == 1

    evo = Evolution(formdata=formdata)
    evo.time = localtime()
    evo.status = formdata.status
    evo.comment = 'hello world'
    formdata.evolution.append(evo)
    formdata.store()

    formdata = data_class.get(id)
    assert len(formdata.evolution) == 2
    assert formdata.evolution[-1].comment == 'hello world'


def test_sql_evolution_change(formdef):
    data_class = formdef.data_class(mode='sql')
    formdata = data_class()
    formdata.just_created()
    formdata.store()
    id = formdata.id

    formdata = data_class.get(id)
    assert len(formdata.evolution) == 1

    evo = Evolution(formdata=formdata)
    evo.time = localtime()
    evo.status = formdata.status
    evo.comment = 'hello world'
    formdata.evolution.append(evo)
    formdata.store()

    formdata = data_class.get(id)
    assert len(formdata.evolution) == 2
    assert formdata.evolution[-1].comment == 'hello world'

    formdata.evolution[-1].comment = 'foobar'
    formdata.store()

    formdata = data_class.get(id)
    assert len(formdata.evolution) == 2
    assert formdata.evolution[-1].comment == 'foobar'


def test_sql_multiple_evolutions(formdef):
    data_class = formdef.data_class(mode='sql')
    for i in range(20):
        formdata = data_class()
        formdata.just_created()
        formdata.store()
        id = formdata.id

        formdata = data_class.get(id)

        evo = Evolution(formdata=formdata)
        evo.time = localtime()
        evo.status = formdata.status
        evo.comment = 'hello world %d' % i
        formdata.evolution.append(evo)
        formdata.store()

    values = data_class.select()
    data_class.load_all_evolutions(values)
    assert [x._evolution for x in values]


def test_sql_get_ids_with_indexed_value(formdef):
    data_class = formdef.data_class(mode='sql')
    data_class.wipe()

    formdata = data_class()
    formdata.store()

    formdata = data_class()
    formdata.user_id = '2'
    formdata.store()
    id2 = formdata.id

    formdata = data_class()
    formdata.user_id = '2'
    formdata.store()
    id3 = formdata.id

    ids = data_class.get_ids_with_indexed_value('user_id', '2')
    assert set(ids) == {id2, id3}


def test_sql_get_ids_from_query(formdef):
    data_class = formdef.data_class(mode='sql')
    data_class.wipe()

    formdata = data_class()
    formdata.data = {'2': 'this is some reasonably long text'}
    formdata.store()
    id1 = formdata.id

    formdata = data_class()
    formdata.data = {'2': 'hello world is still a classical example'}
    formdata.store()
    id2 = formdata.id

    formdata = data_class()
    formdata.data = {'2': 'you would think other ideas of text would emerge'}
    formdata.store()
    id3 = formdata.id

    formdata = data_class()
    formdata.data = {
        '7': {
            'data': [
                {'1': 'some other example having foo', '2': 'boat', '2_display': 'Yatch'},
                {'1': 'bar', '2': 'plane', '2_display': 'Cessna'},
            ]
        }
    }
    formdata.store()
    id4 = formdata.id

    ids = data_class.get_ids_from_query('text')
    assert set(ids) == {id1, id3}

    ids = data_class.get_ids_from_query('classical')
    assert set(ids) == {id2}

    ids = data_class.get_ids_from_query('FOO')
    assert set(ids) == {id4}
    ids = data_class.get_ids_from_query('cessna')
    assert set(ids) == {id4}


def test_sql_fts_index_with_missing_block(formdef):
    data_class = formdef.data_class(mode='sql')
    data_class.wipe()

    formdata = data_class()
    formdata.data = {
        '7': {
            'data': [
                {'1': 'some other example having foo', '2': 'boat', '2_display': 'Yatch'},
                {'1': 'bar', '2': 'plane', '2_display': 'Cessna'},
            ]
        }
    }
    BlockDef.wipe()
    formdata.store()


@pytest.mark.parametrize('settings_mode', ['new', 'legacy'])
def test_sql_fts_index_with_missing_block_and_user_fields_config(pub, formdef, settings_mode):
    from wcs.admin.settings import UserFieldsFormDef

    user_formdef = UserFieldsFormDef(pub)
    user_formdef.fields.append(fields.StringField(id='3', label='first_name', varname='first_name'))
    user_formdef.fields.append(fields.StringField(id='4', label='last_name', varname='last_name'))
    user_formdef.store()
    if settings_mode == 'new':
        pub.cfg['users']['fullname_template'] = '{{ user_var_first_name }} {{ user_var_last_name }}'
    else:
        pub.cfg['users']['field_name'] = ['3', '4']
    pub.write_cfg()

    data_class = formdef.data_class(mode='sql')
    data_class.wipe()

    formdata = data_class()
    formdata.data = {
        '7': {
            'data': [
                {'1': 'some other example having foo', '2': 'boat', '2_display': 'Yatch'},
                {'1': 'bar', '2': 'plane', '2_display': 'Cessna'},
            ]
        }
    }
    BlockDef.wipe()
    formdata.store()


def test_sql_rollback_on_error(formdef):
    data_class = formdef.data_class(mode='sql')
    data_class.wipe()
    with pytest.raises(psycopg2.Error):
        # this will raise a psycopg2.ProgrammingError as there's no FOOBAR
        # column in the table.
        data_class.get_ids_with_indexed_value('FOOBAR', '2')
    data_class.wipe()


def test_sql_atomic_rollback():
    import psycopg2.extensions

    conn, cur = sql.get_connection_and_cursor()

    # check the basic behavior first, enter/exit transaction
    assert conn.get_transaction_status() == psycopg2.extensions.TRANSACTION_STATUS_IDLE

    with sql.atomic():
        assert conn.get_transaction_status() == psycopg2.extensions.TRANSACTION_STATUS_IDLE
        cur.execute('SELECT 1;')
        assert conn.get_transaction_status() == psycopg2.extensions.TRANSACTION_STATUS_INTRANS

    assert conn.get_transaction_status() == psycopg2.extensions.TRANSACTION_STATUS_IDLE

    # now we can exit with a failure
    with pytest.raises(psycopg2.Error):
        with sql.atomic():
            cur.execute('SELECT 1/0;')
    assert conn.get_transaction_status() == psycopg2.extensions.TRANSACTION_STATUS_IDLE

    # Do some changes in a sub transaction
    with sql.atomic():
        with sql.atomic():
            cur.execute('CREATE TEMPORARY TABLE atomic_test_temp_ok();')
            cur.execute("SELECT count(*) FROM pg_class WHERE relname = 'atomic_test_temp_ok'")
            assert cur.fetchone()[0] == 1
        cur.execute("SELECT count(*) FROM pg_class WHERE relname = 'atomic_test_temp_ok'")
        assert cur.fetchone()[0] == 1
        try:
            with sql.atomic():
                cur.execute('CREATE TEMPORARY TABLE atomic_test_temp_nok();')
                cur.execute("SELECT count(*) FROM pg_class WHERE relname = 'atomic_test_temp_nok'")
                assert cur.fetchone()[0] == 1
                cur.execute('SELECT 1/0;')
        except psycopg2.Error:
            pass
        # now make sure it's gone
        cur.execute("SELECT count(*) FROM pg_class WHERE relname = 'atomic_test_temp_nok'")
        assert cur.fetchone()[0] == 0
    # and our change from the first part should still be visible
    cur.execute("SELECT count(*) FROM pg_class WHERE relname = 'atomic_test_temp_ok'")
    assert cur.fetchone()[0] == 1


def test_sql_get_ids_with_indexed_value_dict(formdef):
    data_class = formdef.data_class(mode='sql')
    data_class.wipe()

    formdata = data_class()
    formdata.store()

    formdata = data_class()
    formdata.workflow_roles = {'plop': '2'}
    formdata.store()
    id2 = formdata.id

    formdata = data_class()
    formdata.workflow_roles = {'plop': '2'}
    formdata.store()
    id3 = formdata.id

    ids = data_class.get_ids_with_indexed_value('workflow_roles', '2')
    assert set(ids) == {id2, id3}


def test_create_user(pub):
    sql.SqlUser.wipe()

    user = sql.SqlUser()
    user.name = 'Pierre'
    user.store()


def test_get_user(pub):
    sql.SqlUser.wipe()

    user = sql.SqlUser()
    user.name = 'Pierre'
    user.store()

    assert sql.SqlUser.get(user.id) is not None

    with pytest.raises(KeyError):
        sql.SqlUser.get(23484557288545099215749635047766)


def test_get_missing_user(pub):
    sql.SqlUser.wipe()

    with pytest.raises(KeyError):
        sql.SqlUser.get(12345)


def test_get_missing_user_ignore_errors(pub):
    sql.SqlUser.wipe()

    assert sql.SqlUser.get(12345, ignore_errors=True) is None


def test_user_formdef(pub):
    sql.SqlUser.wipe()

    from wcs.admin.settings import UserFieldsFormDef

    formdef = UserFieldsFormDef(pub)
    formdef.fields = [fields.StringField(id='3', label='test')]
    formdef.store()

    user = sql.SqlUser()
    user.name = 'Pierre'
    user.form_data = {'3': 'Papier'}
    user.store()

    assert sql.SqlUser.get(user.id, ignore_errors=True).form_data['3'] == 'Papier'

    del pub.cfg['users']['formdef']
    pub.write_cfg()


def test_get_users_fts(pub):
    sql.SqlUser.wipe()

    user = sql.SqlUser()
    user.name = 'Pierre'
    user.name_identifiers = ['foo']
    user.store()
    user_id = user.id

    user = sql.SqlUser()
    user.name = 'Papier'
    user.store()

    assert len(sql.SqlUser.get_ids_from_query('pierre')) == 1
    assert sql.SqlUser.get(sql.SqlUser.get_ids_from_query('pierre')[0]).id == user_id


def test_get_users_formdef_fts(pub):
    sql.SqlUser.wipe()

    from wcs.admin.settings import UserFieldsFormDef

    formdef = UserFieldsFormDef(pub)
    formdef.fields = [fields.StringField(id='3', label='test')]
    formdef.store()

    user = sql.SqlUser()
    user.name = 'Pierre'
    user.form_data = {'3': 'Papier'}
    user.store()
    user_id = user.id

    assert len(sql.SqlUser.get_ids_from_query('pierre papier')) == 1
    assert sql.SqlUser.get(sql.SqlUser.get_ids_from_query('pierre papier')[0]).id == user_id

    assert len(sql.SqlUser.get_ids_from_query('papier pierre')) == 1
    assert sql.SqlUser.get(sql.SqlUser.get_ids_from_query('papier pierre')[0]).id == user_id

    del pub.cfg['users']['formdef']
    pub.write_cfg()


def test_urlname_change(formdef):
    data_class = formdef.data_class(mode='sql')
    data_class.wipe()
    assert formdef.url_name == 'tests'

    formdef.name = 'tests2'
    formdef.store()
    assert formdef.url_name == 'tests'

    formdef.name = 'tests'
    formdef.store()
    assert formdef.url_name == 'tests'

    data_class = formdef.data_class(mode='sql')

    formdata = data_class()
    formdata.status = 'wf-0'
    formdata.user_id = '5'
    formdata.store()

    formdef.name = 'tests2'
    formdef.store()
    assert formdef.url_name == 'tests'

    assert data_class.count() == 1


def test_sql_table_add_and_remove_fields(pub):
    test_formdef = FormDef()
    test_formdef.name = 'tests and and remove fields'
    test_formdef.fields = []
    test_formdef.store()
    assert test_formdef.table_name is not None
    data_class = test_formdef.data_class(mode='sql')
    assert data_class.count() == 0

    test_formdef.fields = [
        fields.StringField(label='string'),
        fields.EmailField(label='email'),
    ]

    for field in test_formdef.fields:
        if field.id is None:
            field.id = test_formdef.get_new_field_id()
    test_formdef.store()

    test_formdef.fields.append(fields.ItemField(label='item', items=('apple', 'pear', 'peach', 'apricot')))
    test_formdef.fields[-1].id = test_formdef.get_new_field_id()
    test_formdef.store()

    data_class = test_formdef.data_class(mode='sql')
    data_class.select()

    previous_id = test_formdef.fields[-1].id
    test_formdef.fields = test_formdef.fields[:-1]
    test_formdef.store()

    data_class = test_formdef.data_class(mode='sql')
    data_class.select()

    test_formdef.fields.append(fields.StringField(label='item'))
    test_formdef.fields[-1].id = test_formdef.get_new_field_id()
    test_formdef.store()

    assert test_formdef.fields[-1].id != previous_id

    data_class = test_formdef.data_class(mode='sql')
    data_class.select()

    test_formdef.fields = test_formdef.fields[:-1]
    test_formdef.fields.append(fields.ItemField(label='item', items=('apple', 'pear', 'peach', 'apricot')))
    test_formdef.fields[-1].id = test_formdef.get_new_field_id()
    test_formdef.store()

    data_class = test_formdef.data_class(mode='sql')
    data_class.select()


def test_sql_table_wipe_and_drop(pub):
    test_formdef = FormDef()
    test_formdef.name = 'tests wipe and drop'
    test_formdef.fields = []
    test_formdef.store()
    assert test_formdef.table_name is not None
    data_class = test_formdef.data_class(mode='sql')
    assert data_class.count() == 0
    conn, cur = sql.get_connection_and_cursor()
    assert table_exists(cur, test_formdef.table_name)
    conn.commit()
    cur.close()

    data_class.wipe(drop=True)
    conn, cur = sql.get_connection_and_cursor()
    assert not table_exists(cur, test_formdef.table_name)
    assert not table_exists(cur, test_formdef.table_name + '_evolutions')
    conn.commit()
    cur.close()

    test_formdef.store()
    conn, cur = sql.get_connection_and_cursor()
    assert table_exists(cur, test_formdef.table_name)


def test_sql_indexes(pub):
    test_formdef = FormDef()
    test_formdef.name = 'tests indexes'
    test_formdef.fields = []
    test_formdef.store()
    data_class = test_formdef.data_class(mode='sql')
    assert data_class.count() == 0
    conn, cur = sql.get_connection_and_cursor()
    assert index_exists(cur, test_formdef.table_name + '_evolutions_fid')
    conn.commit()
    cur.close()

    data_class.wipe(drop=True)
    conn, cur = sql.get_connection_and_cursor()
    assert not index_exists(cur, test_formdef.table_name + '_evolutions_fid')
    conn.commit()
    cur.close()


def test_sql_table_select(pub):
    test_formdef = FormDef()
    test_formdef.name = 'table select'
    test_formdef.fields = []
    test_formdef.store()
    data_class = test_formdef.data_class(mode='sql')
    assert data_class.count() == 0

    for _ in range(50):
        t = data_class()
        t.store()

    assert data_class.count() == 50
    assert len(data_class.select()) == 50

    assert len(data_class.select(lambda x: x.id < 26)) == 25
    assert len(data_class.select([st.Less('id', 26)])) == 25
    assert len(data_class.select([st.Less('id', 25), st.GreaterOrEqual('id', 10)])) == 15
    assert (
        len(data_class.select([st.Less('id', 25), st.GreaterOrEqual('id', 10), lambda x: x.id >= 15])) == 10
    )
    assert len(data_class.select([st.NotEqual('id', 26)])) == 49

    assert len(data_class.select([st.Contains('id', [])])) == 0
    assert len(data_class.select([st.Contains('id', [24, 25, 26])])) == 3
    assert len(data_class.select([st.Contains('id', [24, 25, 86])])) == 2
    assert len(data_class.select([st.NotContains('id', [24, 25, 86])])) == 48
    assert len(data_class.select([st.NotContains('id', [])])) == 50


def test_sql_table_select_iterator(pub):
    test_formdef = FormDef()
    test_formdef.name = 'table select'
    test_formdef.fields = []
    test_formdef.store()
    data_class = test_formdef.data_class(mode='sql')
    assert data_class.count() == 0

    for _ in range(50):
        t = data_class()
        t.store()

    assert data_class.count() == 50

    with pytest.raises(TypeError):
        assert len(data_class.select(iterator=True)) == 50
        # TypeError: object of type 'generator' has no len()

    assert len(list(data_class.select(iterator=True))) == 50
    assert len(list(data_class.select(lambda x: True, iterator=True))) == 50
    assert len(list(data_class.select(lambda x: x.id < 26, iterator=True))) == 25
    assert len(list(data_class.select([st.Less('id', 26)], iterator=True))) == 25
    assert len(list(data_class.select([st.Less('id', 25), st.GreaterOrEqual('id', 10)], iterator=True))) == 15
    assert (
        len(
            list(
                data_class.select(
                    [st.Less('id', 25), st.GreaterOrEqual('id', 10), lambda x: x.id >= 15], iterator=True
                )
            )
        )
        == 10
    )


def test_sql_table_select_datetime(pub):
    test_formdef = FormDef()
    test_formdef.name = 'table select datetime'
    test_formdef.fields = []
    test_formdef.store()
    data_class = test_formdef.data_class(mode='sql')
    assert data_class.count() == 0

    d = make_aware(datetime.datetime(2014, 1, 1))
    for i in range(50):
        t = data_class()
        t.receipt_time = d + datetime.timedelta(days=i)
        t.store()

    assert data_class.count() == 50
    assert len(data_class.select()) == 50

    assert len(data_class.select(lambda x: x.receipt_time == d)) == 1
    assert len(data_class.select([st.Equal('receipt_time', d)])) == 1
    assert len(data_class.select([st.Less('receipt_time', d + datetime.timedelta(days=20))])) == 20
    assert len(data_class.select([st.Greater('receipt_time', d + datetime.timedelta(days=20))])) == 29
    assert len(data_class.select([st.Equal('receipt_time', datetime.date(1900, 1, 1).timetuple())])) == 0
    assert len(data_class.select([st.Equal('receipt_time', datetime.date(1900, 1, 1))])) == 0
    assert len(data_class.select([st.Greater('receipt_time', datetime.date(1900, 1, 1))])) == 50
    assert len(data_class.select([st.Equal('receipt_time', datetime.date(1900, 1, 1).timetuple())])) == 0
    assert len(data_class.select([st.Greater('receipt_time', datetime.date(1900, 1, 1).timetuple())])) == 50


def test_select_limit_offset(pub):
    test_formdef = FormDef()
    test_formdef.name = 'table select limit offset'
    test_formdef.fields = []
    test_formdef.store()
    data_class = test_formdef.data_class(mode='sql')
    assert data_class.count() == 0

    for _ in range(50):
        t = data_class()
        t.store()

    assert len(data_class.select()) == 50
    for iterator in (False, True):
        for func_clause in (lambda x: True, None):
            assert [x.id for x in data_class.select(func_clause, order_by='id', iterator=iterator)] == list(
                range(1, 51)
            )
            assert [
                x.id for x in data_class.select(func_clause, order_by='id', limit=10, iterator=iterator)
            ] == list(range(1, 11))
            assert [
                x.id
                for x in data_class.select(func_clause, order_by='id', limit=10, offset=10, iterator=iterator)
            ] == list(range(11, 21))
            assert [
                x.id
                for x in data_class.select(func_clause, order_by='id', limit=20, offset=20, iterator=iterator)
            ] == list(range(21, 41))
            assert [
                x.id for x in data_class.select(func_clause, order_by='id', offset=10, iterator=iterator)
            ] == list(range(11, 51))
        assert len([x.id for x in data_class.select(lambda x: x.id > 10, limit=10, iterator=iterator)]) == 10


def test_sorted_ids(pub):
    test_formdef = FormDef()
    test_formdef.name = 'table sorted ids'
    test_formdef.fields = []
    test_formdef.store()
    data_class = test_formdef.data_class()
    assert data_class.count() == 0

    for _ in range(50):
        t = data_class()
        t.store()
    assert data_class.get_sorted_ids(order_by='id') == list(range(1, 51))
    assert data_class.get_sorted_ids(order_by='id', limit=10) == list(range(1, 11))
    assert data_class.get_sorted_ids(order_by='id', offset=10) == list(range(11, 51))
    assert data_class.get_sorted_ids(order_by='id', limit=10, offset=10) == list(range(11, 21))


def test_select_criteria_intersects(formdef):
    data_class = formdef.data_class(mode='sql')
    data_class.wipe()
    formdata = data_class()
    formdata.status = 'wf-0'
    formdata.user_id = '5'
    formdata.data = {'6': ['apricot']}
    formdata.store()

    formdata = data_class()
    formdata.status = 'wf-0'
    formdata.user_id = '5'
    formdata.data = {'6': ['apricot', 'pear']}
    formdata.store()

    formdata = data_class()
    formdata.status = 'wf-0'
    formdata.user_id = '5'
    formdata.data = {'6': []}
    formdata.store()

    assert len(data_class.select([st.Intersects('f6', ['apricot'])])) == 2
    assert len(data_class.select([st.Intersects('f6', ['pear'])])) == 1
    assert len(data_class.select([st.Intersects('f6', ['apple'])])) == 0


def test_count(pub):
    test_formdef = FormDef()
    test_formdef.name = 'table select count'
    test_formdef.fields = []
    test_formdef.store()
    data_class = test_formdef.data_class(mode='sql')
    assert data_class.count() == 0

    for _ in range(50):
        t = data_class()
        t.store()

    assert data_class.count() == 50
    assert data_class.count([st.Less('id', 26)]) == 25


def test_select_criteria_or_and(pub):
    test_formdef = FormDef()
    test_formdef.name = 'table select criteria or and'
    test_formdef.fields = []
    test_formdef.store()
    data_class = test_formdef.data_class(mode='sql')
    assert data_class.count() == 0

    for _ in range(50):
        t = data_class()
        t.store()

    assert [int(x.id) for x in data_class.select([st.Or([])], order_by='id')] == []
    assert [x.id for x in data_class.select([st.Or([st.Less('id', 10)])], order_by='id')] == list(
        range(1, 10)
    )
    assert [
        x.id for x in data_class.select([st.Or([st.Less('id', 10), st.Equal('id', 15)])], order_by='id')
    ] == list(range(1, 10)) + [15]
    assert [
        x.id for x in data_class.select([st.And([st.Less('id', 10), st.Greater('id', 5)])], order_by='id')
    ] == list(range(6, 10))


def test_select_criteria_null(pub):
    test_formdef = FormDef()
    test_formdef.name = 'table select criteria null'
    test_formdef.fields = [fields.StringField(id=test_formdef.get_new_field_id(), label='foo')]
    test_formdef.store()
    data_class = test_formdef.data_class(mode='sql')
    assert data_class.count() == 0

    for x in range(50):
        t = data_class()
        if x % 3:
            t.submission_channel = None
            t.data[test_formdef.fields[0].id] = 'xxx'
        else:
            t.submission_channel = 'mail'
        t.store()

    assert len(data_class.select([st.Null('submission_channel')])) == 33
    assert len(data_class.select([st.NotNull('submission_channel')])) == 17
    assert len(data_class.select([st.Null('f%s' % test_formdef.fields[0].id)])) == 17
    assert len(data_class.select([st.NotNull('f%s' % test_formdef.fields[0].id)])) == 33


def test_sql_table_select_bool(pub):
    test_formdef = FormDef()
    test_formdef.name = 'table select bool'
    test_formdef.fields = [fields.BoolField(id='3', label='bool')]
    test_formdef.store()
    data_class = test_formdef.data_class(mode='sql')
    assert data_class.count() == 0

    for _ in range(50):
        t = data_class()
        t.data = {'3': False}
        t.store()
    t.data = {'3': True}
    t.store()

    assert data_class.count() == 50
    assert len(data_class.select()) == 50
    assert len(data_class.select([st.Equal('f3', True)])) == 1
    assert len(data_class.select([st.Equal('f3', False)])) == 49


def test_sql_criteria_ilike(pub):
    test_formdef = FormDef()
    test_formdef.name = 'table select bool'
    test_formdef.fields = [fields.StringField(id='3', label='string')]
    test_formdef.store()
    data_class = test_formdef.data_class(mode='sql')
    assert data_class.count() == 0

    for i in range(50):
        t = data_class()
        if i < 20:
            t.data = {'3': 'foo'}
        else:
            t.data = {'3': 'bar'}
        t.store()
    t.store()

    assert data_class.count() == 50
    assert len(data_class.select()) == 50

    assert [x.id for x in data_class.select([st.ILike('f3', 'bar')], order_by='id')] == list(range(21, 51))
    assert [x.id for x in data_class.select([st.ILike('f3', 'BAR')], order_by='id')] == list(range(21, 51))


@pytest.mark.parametrize('wcs_fts', [True, False])
def test_sql_criteria_fts(pub, wcs_fts):
    pub.load_site_options()
    pub.site_options.set('options', 'enable-new-fts', 'true' if wcs_fts else 'false')

    test_formdef = FormDef()
    test_formdef.name = 'table select fts'
    test_formdef.fields = [fields.StringField(id='3', label='string')]
    test_formdef.store()
    data_class = test_formdef.data_class(mode='sql')
    assert data_class.count() == 0

    for i in range(50):
        t = data_class()
        if i < 20:
            t.data = {'3': 'foo'}
        else:
            t.data = {'3': 'bar'}
        t.just_created()
        t.store()

    assert data_class.count() == 50
    assert len(data_class.select()) == 50

    assert set(data_class.get_ids_from_query('BAR')) == set(range(21, 51))
    assert [x.id for x in data_class.select([st.FtsMatch('BAR')], order_by='id')] == list(range(21, 51))

    # check fts against data in history
    assert len(data_class.select([st.FtsMatch('XXX')])) == 0
    formdata1 = data_class.select([st.FtsMatch('BAR')])[0]
    formdata1.evolution[0].comment = 'XXX'
    formdata1.store()
    assert len(data_class.select([st.FtsMatch('XXX')])) == 1
    assert data_class.select([st.FtsMatch('XXX')])[0].id == formdata1.id

    assert len(data_class.select([st.FtsMatch('yyy')])) == 0
    item = RegisterCommenterWorkflowStatusItem()
    item.comment = '<span>ÿÿÿ</span>'
    item.perform(formdata1)
    assert formdata1.evolution[-1].display_parts()[-1] == '<span>ÿÿÿ</span>'
    formdata1.store()
    assert len(data_class.select([st.FtsMatch('yyy')])) == 1
    assert len(data_class.select([st.FtsMatch('span')])) == 0

    assert data_class.count([st.FtsMatch('Pierre')]) == 0
    sql.SqlUser.wipe()
    user = sql.SqlUser()
    user.name = 'Pierre'
    user.store()
    t.user_id = user.id
    t.store()
    assert data_class.count([st.FtsMatch('Pierre')]) == 1

    # check unaccent
    user = sql.SqlUser()
    user.name = force_str('Frédéric')
    user.store()
    t.user_id = user.id
    t.store()
    assert data_class.count([st.FtsMatch(user.name)]) == 1
    assert data_class.count([st.FtsMatch('Frederic')]) == 1

    # check looking up a display id
    assert len(data_class.get_ids_from_query(formdata1.id_display)) == 1
    assert len(data_class.select([st.FtsMatch(formdata1.id_display)])) == 1
    assert data_class.select([st.FtsMatch(formdata1.id_display)])[0].id_display == formdata1.id_display

    # check behaviour difference between old and new fts
    data_class.wipe()
    formdata = data_class()
    formdata.data = {'3': 'dysfonctionnement'}
    formdata.just_created()
    formdata.store()
    if wcs_fts:
        for n in range(4, len(formdata.data['3'])):
            assert data_class.count([st.ExtendedFtsMatch(formdata.data['3'][:n])]) == 1
    else:
        assert data_class.count([st.FtsMatch(formdata.data['3'][:5])]) == 0
        assert data_class.count([st.FtsMatch(formdata.data['3'])]) == 1

    # check against match with parenthesis
    if wcs_fts:
        data_class.wipe()
        formdata = data_class()
        formdata.data = {'3': '(see www.example.net/test)'}
        formdata.just_created()
        formdata.store()
        assert data_class.count([st.ExtendedFtsMatch('example', test_formdef)]) == 1


def test_search_tokens_purge(pub):
    _, cur = sql.get_connection_and_cursor()

    def token_exists(token):
        cur.execute('SELECT count(*) FROM wcs_search_tokens WHERE token=%s;', (token,))
        return cur.fetchone()[0] == 1

    # purge garbage from other tests
    FormDef.wipe()
    sql.purge_obsolete_search_tokens()

    # make sure the existing situation is clean for the test
    assert not (token_exists('tableselectftstoken'))
    assert not (token_exists('foofortokensofcours'))
    assert not (token_exists('chaussettefortokensofcours'))

    # define a new table
    test_formdef = FormDef()
    test_formdef.name = 'tableSelectFTStokens'
    test_formdef.fields = [fields.StringField(id='3', label='string')]
    test_formdef.store()
    data_class = test_formdef.data_class(mode='sql')

    assert token_exists('tableselectftstoken')

    t = data_class()
    t.data = {'3': 'foofortokensofcourse'}
    t.just_created()
    t.store()

    assert token_exists('foofortokensofcours')

    t.data = {'3': 'chaussettefortokensofcourse'}
    t.store()

    # one additional element
    assert token_exists('foofortokensofcours')
    assert token_exists('chaussettefortokensofcours')

    for i in range(20):
        t = data_class()
        t.data = {'3': 'chaussettefortokensofcourse'}
        t.just_created()
        t.store()

    sql.purge_obsolete_search_tokens(itersize=10)

    assert not (token_exists('foofortokensofcours'))
    assert token_exists('chaussettefortokensofcours')


def test_search_tokens_stopwords(pub):
    _, cur = sql.get_connection_and_cursor()

    def token_exists(token):
        cur.execute('SELECT count(*) FROM wcs_search_tokens WHERE token=%s;', (token,))
        return cur.fetchone()[0] == 1

    # purge garbage from other tests
    FormDef.wipe()
    sql.purge_obsolete_search_tokens()

    # make sure the existing situation is clean for the test
    assert not (token_exists('hotel'))
    assert not (token_exists('vill'))

    # define a new table
    test_formdef = FormDef()
    test_formdef.name = 'tableSelectFTStokens'
    test_formdef.fields = [fields.StringField(id='3', label='string')]
    test_formdef.store()
    data_class = test_formdef.data_class(mode='sql')

    t = data_class()
    t.data = {'3': 'hotel de ville dex'}
    t.just_created()
    t.store()

    assert token_exists('hotel')
    assert token_exists('vill')

    q = 'hotel de ville'
    cur.execute('SELECT plainto_tsquery(%s) = wcs_tsquery(%s);', (q, q))
    assert cur.fetchone()[0]


def table_exists(cur, table_name):
    cur.execute(
        '''SELECT COUNT(*) FROM information_schema.tables
                    WHERE table_name = %s''',
        (table_name,),
    )
    return bool(cur.fetchone()[0] == 1)


def column_exists_in_table(cur, table_name, column_name):
    cur.execute(
        '''SELECT COUNT(*) FROM information_schema.columns
                    WHERE table_name = %s
                      AND column_name = %s''',
        (table_name, column_name),
    )
    return bool(cur.fetchone()[0] == 1)


def index_exists(cur, index_name):
    cur.execute(
        '''SELECT COUNT(*) FROM pg_indexes
                    WHERE schemaname = 'public'
                      AND indexname = %s''',
        (index_name,),
    )
    return bool(cur.fetchone()[0] == 1)


def test_wcs_meta_dates(pub):
    conn, cur = sql.get_connection_and_cursor()

    # reindex flags
    sql.set_reindex('foo', 'bar', conn=conn, cur=cur)
    conn.commit()
    cur.execute('SELECT created_at, updated_at FROM wcs_meta WHERE key = %s', ('reindex_foo',))
    row = cur.fetchone()
    assert row[0] is not None
    assert row[1] is not None
    old_created_at = row[0]
    old_updated_at = row[0]

    sql.set_reindex('foo', 'bar', conn=conn, cur=cur)
    conn.commit()
    cur.execute('SELECT created_at, updated_at FROM wcs_meta WHERE key = %s', ('reindex_foo',))
    row = cur.fetchone()
    assert row[0] == old_created_at
    assert row[1] == old_updated_at

    sql.set_reindex('foo', 'bar-2', conn=conn, cur=cur)
    conn.commit()
    cur.execute('SELECT created_at, updated_at FROM wcs_meta WHERE key = %s', ('reindex_foo',))
    row = cur.fetchone()
    assert row[0] == old_created_at
    assert row[1] != old_updated_at

    # sql_level
    cur.execute('SELECT created_at, updated_at FROM wcs_meta WHERE key = %s', ('sql_level',))
    row = cur.fetchone()
    assert row[0] is not None
    assert row[1] is not None
    old_created_at = row[0]
    old_updated_at = row[0]

    sql.migrate()
    cur.execute('SELECT created_at, updated_at FROM wcs_meta WHERE key = %s', ('sql_level',))
    row = cur.fetchone()
    assert row[0] == old_created_at
    assert row[1] == old_updated_at

    cur.execute('''UPDATE wcs_meta SET value = %s WHERE key = %s''', (str(1), 'sql_level'))
    conn.commit()
    sql.migrate()
    cur.execute('SELECT created_at, updated_at FROM wcs_meta WHERE key = %s', ('sql_level',))
    row = cur.fetchone()
    assert row[0] == old_created_at
    assert row[1] != old_updated_at


def test_sql_level(pub):
    conn, cur = sql.get_connection_and_cursor()
    cur.execute('DROP TABLE wcs_meta')
    assert sql.get_sql_level(conn, cur) == 0
    sql.migrate()
    assert sql.get_sql_level(conn, cur) == sql.SQL_LEVEL[0]

    # insert negative SQL level, to trigger an error, and check it's not
    # changed.
    cur.execute('''UPDATE wcs_meta SET value = %s WHERE key = %s''', (str(-1), 'sql_level'))
    assert sql.get_sql_level(conn, cur) == -1
    with pytest.raises(RuntimeError):
        sql.migrate()
    assert sql.get_sql_level(conn, cur) == -1

    conn.commit()
    cur.close()


def migration_level(cur):
    cur.execute('SELECT value FROM wcs_meta WHERE key = %s', ('sql_level',))
    row = cur.fetchone()
    return int(row[0])


def test_migration_1_tracking_code(pub):
    conn, cur = sql.get_connection_and_cursor()
    cur.execute('DROP TABLE wcs_meta')
    cur.execute('DROP TABLE tracking_codes')
    sql.migrate()
    assert table_exists(cur, 'tracking_codes')
    assert table_exists(cur, 'wcs_meta')
    assert migration_level(cur) >= 1
    conn.commit()
    cur.close()


def test_migration_2_formdef_id_in_views(pub_with_views, formdef):
    conn, cur = sql.get_connection_and_cursor()
    cur.execute('UPDATE wcs_meta SET value = 1 WHERE key = %s', ('sql_level',))
    cur.execute('DROP TABLE wcs_all_forms CASCADE')

    # hack a formdef table the wrong way, to check it is reconstructed
    # properly before the views are created
    formdef.fields[4] = fields.StringField(id='4', label='item')
    cur.execute('DROP VIEW wcs_view_1_tests')
    cur.execute('ALTER TABLE formdata_1_tests DROP COLUMN f4_display')
    sql.redo_views(conn, cur, formdef, rebuild_global_views=False)
    formdef.fields[4] = fields.ItemField(id='4', label='item', items=('apple', 'pear', 'peach', 'apricot'))
    assert table_exists(cur, 'wcs_view_1_tests')
    assert not column_exists_in_table(cur, 'wcs_view_1_tests', 'f4_display')

    sql.migrate()

    assert column_exists_in_table(cur, 'wcs_all_forms', 'formdef_id')
    assert migration_level(cur) >= 2

    conn.commit()
    cur.close()


def test_migration_6_actions_roles(pub_with_views, formdef):
    conn, cur = sql.get_connection_and_cursor()
    cur.execute('UPDATE wcs_meta SET value = 5 WHERE key = %s', ('sql_level',))
    cur.execute('DROP TABLE wcs_all_forms CASCADE')

    # hack a formdef table the wrong way, to check it is reconstructed
    # properly before the views are created
    formdef.fields[4] = fields.StringField(id='4', label='item')
    cur.execute('DROP VIEW wcs_view_1_tests')
    cur.execute('ALTER TABLE formdata_1_tests DROP COLUMN actions_roles_array')
    sql.drop_views(formdef, conn, cur)
    formdef.fields[4] = fields.ItemField(id='4', label='item', items=('apple', 'pear', 'peach', 'apricot'))
    assert not column_exists_in_table(cur, 'formdata_1_tests', 'actions_roles_array')

    sql.migrate()

    assert column_exists_in_table(cur, 'formdata_1_tests', 'actions_roles_array')
    assert column_exists_in_table(cur, 'wcs_view_1_tests', 'actions_roles_array')
    assert column_exists_in_table(cur, 'wcs_all_forms', 'actions_roles_array')
    assert migration_level(cur) >= 6

    conn.commit()
    cur.close()


def test_migration_10_submission_channel(formdef):
    conn, cur = sql.get_connection_and_cursor()
    cur.execute('UPDATE wcs_meta SET value = 9 WHERE key = %s', ('sql_level',))
    cur.execute('DROP TABLE wcs_all_forms CASCADE')

    formdef.fields[4] = fields.StringField(id='4', label='item')
    cur.execute('ALTER TABLE formdata_1_tests DROP COLUMN submission_channel')
    formdef.fields[4] = fields.ItemField(id='4', label='item', items=('apple', 'pear', 'peach', 'apricot'))
    assert not column_exists_in_table(cur, 'formdata_1_tests', 'submission_channel')

    sql.migrate()

    assert column_exists_in_table(cur, 'formdata_1_tests', 'submission_channel')
    assert column_exists_in_table(cur, 'wcs_all_forms', 'submission_channel')
    assert migration_level(cur) >= 10

    conn.commit()
    cur.close()


def test_migration_12_users_fts(pub):
    conn, cur = sql.get_connection_and_cursor()
    cur.execute('UPDATE wcs_meta SET value = 11 WHERE key = %s', ('sql_level',))

    sql.SqlUser.wipe()

    user = sql.SqlUser()
    user.name = 'Pierre'
    user.store()

    # remove the fts column
    cur.execute('ALTER TABLE users DROP COLUMN fts')
    assert not column_exists_in_table(cur, 'users', 'fts')
    sql.migrate()

    assert column_exists_in_table(cur, 'users', 'fts')
    assert migration_level(cur) >= 12

    # no fts, migration only prepare re-index
    assert len(sql.SqlUser.get_ids_from_query('pierre')) == 0

    assert sql.is_reindex_needed('user', conn=conn, cur=cur) is True
    assert sql.is_reindex_needed('formdata', conn=conn, cur=cur) is True
    sql.reindex()
    assert sql.is_reindex_needed('user', conn=conn, cur=cur) is False
    assert sql.is_reindex_needed('formdata', conn=conn, cur=cur) is False

    # make sure the fts is filled after the migration
    assert len(sql.SqlUser.get_ids_from_query('pierre')) == 1

    conn.commit()
    cur.close()


def test_migration_21_users_ascii_name(pub):
    conn, cur = sql.get_connection_and_cursor()
    cur.execute('UPDATE wcs_meta SET value = 11 WHERE key = %s', ('sql_level',))

    sql.SqlUser.wipe()

    user = sql.SqlUser()
    user.name = 'Jean Sénisme'
    user.store()

    # remove the ascii_name column
    cur.execute('ALTER TABLE users DROP COLUMN ascii_name')
    assert not column_exists_in_table(cur, 'users', 'ascii_name')
    sql.migrate()

    assert column_exists_in_table(cur, 'users', 'ascii_name')
    assert migration_level(cur) >= 21

    # no fts, migration only prepare re-index
    assert sql.SqlUser.count([st.Equal('ascii_name', 'jean senisme')]) == 0

    assert sql.is_reindex_needed('user', conn=conn, cur=cur) is True
    assert sql.is_reindex_needed('formdata', conn=conn, cur=cur) is True
    sql.reindex()
    assert sql.is_reindex_needed('user', conn=conn, cur=cur) is False
    assert sql.is_reindex_needed('formdata', conn=conn, cur=cur) is False

    # make sure the ascii_name is filled after the migration
    assert sql.SqlUser.count([st.Equal('ascii_name', 'jean senisme')]) == 1

    conn.commit()
    cur.close()


def test_migration_24_evolution_index(pub):
    FormDef.wipe()

    formdef = FormDef()
    formdef.name = 'tests migration 24'
    formdef.fields = []
    formdef.store()

    conn, cur = sql.get_connection_and_cursor()
    cur.execute('DROP INDEX %s_evolutions_fid' % formdef.table_name)
    cur.execute('UPDATE wcs_meta SET value = 23 WHERE key = %s', ('sql_level',))
    conn.commit()
    cur.close()

    conn, cur = sql.get_connection_and_cursor()
    assert not index_exists(cur, formdef.table_name + '_evolutions_fid')
    conn.commit()
    cur.close()

    sql.migrate()

    conn, cur = sql.get_connection_and_cursor()
    assert index_exists(cur, formdef.table_name + '_evolutions_fid')

    conn.commit()
    cur.close()


def test_migration_38_user_deleted(pub):
    conn, cur = sql.get_connection_and_cursor()
    cur.execute('UPDATE wcs_meta SET value = 37 WHERE key = %s', ('sql_level',))
    conn.commit()
    cur.close()

    sql.SqlUser.wipe()
    user = sql.SqlUser()
    user.name = 'Jean Sénisme'
    user.store()
    assert sql.SqlUser.count() == 1

    conn, cur = sql.get_connection_and_cursor()
    cur.execute('ALTER TABLE users DROP COLUMN deleted_timestamp')
    assert not column_exists_in_table(cur, 'users', 'deleted_timestamp')
    sql.migrate()
    assert column_exists_in_table(cur, 'users', 'ascii_name')
    assert migration_level(cur) >= 38

    assert sql.SqlUser.count() == 1
    assert not sql.SqlUser.get(id=user.id).deleted_timestamp


def drop_formdef_tables():
    FormDef.wipe()


def test_is_at_endpoint(pub):
    drop_formdef_tables()
    _, cur = sql.get_connection_and_cursor()

    wf = Workflow(name='test endpoint')
    st1 = wf.add_status('Status1', 'st1')
    wf.add_status('Status2', 'st2')

    commentable = st1.add_action('commentable', id='_commentable')
    commentable.by = ['_submitter', '_receiver']

    wf.store()
    assert [x.id for x in wf.get_endpoint_status()] == ['st2']

    formdef = FormDef()
    formdef.name = 'test endpoint'
    formdef.fields = []
    formdef.workflow = wf
    formdef.store()

    data_class = formdef.data_class(mode='sql')
    formdata = data_class()
    formdata.status = 'wf-st1'
    formdata.store()
    formdata = data_class()
    formdata.status = 'wf-st2'
    formdata.store()

    cur.execute('''SELECT COUNT(*) FROM wcs_all_forms''')
    assert bool(cur.fetchone()[0] == 2)

    cur.execute('''SELECT COUNT(*) FROM wcs_all_forms WHERE status = 'wf-st1' ''')
    assert bool(cur.fetchone()[0] == 1)

    cur.execute('''SELECT COUNT(*) FROM wcs_all_forms WHERE is_at_endpoint = true''')
    assert bool(cur.fetchone()[0] == 1)

    # check a change to workflow is reflected in the database
    st1.forced_endpoint = True
    wf.store()
    assert [x.id for x in wf.get_endpoint_status()] == ['st1', 'st2']
    cur.execute('''SELECT COUNT(*) FROM wcs_all_forms WHERE is_at_endpoint = true''')
    assert bool(cur.fetchone()[0] == 2)


def test_all_forms_user_name_change(pub, formdef):
    sql.SqlUser.wipe()

    user = sql.SqlUser()
    user.name = 'Foo'
    user.store()

    formdata = formdef.data_class()()
    formdata.user_id = user.id
    formdata.store()

    objects = sql.AnyFormData.select()
    assert len(objects) == 1
    assert objects[0].user_id == str(user.id)

    conn, cur = sql.get_connection_and_cursor()
    cur.execute('SELECT user_name FROM wcs_all_forms')
    row = cur.fetchone()
    assert row[0] == 'Foo'

    user.refresh_from_storage()
    user.name = 'Foo Bar'
    user.store()
    cur.execute('SELECT user_name FROM wcs_all_forms')
    row = cur.fetchone()
    assert row[0] == 'Foo Bar'
    cur.close()
    conn.commit()


def test_all_forms_category_change(pub, formdef):
    Category.wipe()
    FormDef.wipe()

    formdef = FormDef()
    formdef.name = 'test'
    formdef.store()

    formdata = formdef.data_class()()
    formdata.store()

    conn, cur = sql.get_connection_and_cursor()
    cur.execute('SELECT category_id FROM wcs_all_forms WHERE formdef_id = %s', (formdef.id,))
    row = cur.fetchone()
    assert row[0] is None

    category = Category()
    category.name = 'Test'
    category.store()

    formdef.category_id = category.id
    formdef.store()
    cur.execute('SELECT category_id FROM wcs_all_forms WHERE formdef_id = %s', (formdef.id,))
    row = cur.fetchone()
    assert row[0] == int(category.id)

    category2 = Category()
    category2.name = 'Test2'
    category2.store()
    formdef.category_id = category2.id
    formdef.store()
    cur.execute('SELECT category_id FROM wcs_all_forms WHERE formdef_id = %s', (formdef.id,))
    row = cur.fetchone()
    assert row[0] == int(category2.id)

    formdef.category_id = None
    formdef.store()
    cur.execute('SELECT category_id FROM wcs_all_forms WHERE formdef_id = %s', (formdef.id,))
    row = cur.fetchone()
    assert row[0] is None

    cur.close()
    conn.commit()


def test_views_fts(pub):
    drop_formdef_tables()
    _, cur = sql.get_connection_and_cursor()

    formdef = FormDef()
    formdef.name = 'test fts'
    formdef.fields = [
        fields.StringField(id='0', label='string'),
    ]
    formdef.store()

    data_class = formdef.data_class(mode='sql')
    formdata1 = data_class()
    formdata1.data = {'0': 'foo bar'}
    formdata1.store()

    formdata2 = data_class()
    formdata2.data = {'0': 'foo'}
    formdata2.store()

    cur.execute('''SELECT COUNT(*) FROM wcs_all_forms WHERE fts @@ plainto_tsquery(%s)''', ('foo',))
    assert bool(cur.fetchone()[0] == 2)

    cur.execute('''SELECT COUNT(*) FROM wcs_all_forms WHERE fts @@ plainto_tsquery(%s)''', ('bar',))
    assert bool(cur.fetchone()[0] == 1)


def test_select_any_formdata(pub):
    drop_formdef_tables()

    now = localtime()

    cnt = 0
    for i in range(5):
        formdef = FormDef()
        formdef.name = 'test any %d' % i
        formdef.fields = []
        formdef.store()

        data_class = formdef.data_class(mode='sql')
        for j in range(20):
            formdata = data_class()
            formdata.just_created()
            formdata.user_id = '%s' % ((i + j) % 11)
            # set receipt_time to make sure all entries are unique.
            formdata.receipt_time = now + datetime.timedelta(seconds=cnt)
            formdata.status = ['wf-new', 'wf-accepted', 'wf-rejected', 'wf-finished'][(i + j) % 4]
            if j < 5:
                formdata.submission_channel = 'mail'
            formdata.store()
            cnt += 1

    # test generic select
    objects = sql.AnyFormData.select()
    assert len(objects) == 100

    # make sure valid formdefs are used
    assert len([x for x in objects if x.formdef.name == 'test any 0']) == 20
    assert len([x for x in objects if x.formdef.name == 'test any 1']) == 20

    # test sorting
    objects = sql.AnyFormData.select(order_by='receipt_time')
    assert len(objects) == 100

    objects2 = sql.AnyFormData.select(order_by='-receipt_time')
    assert [(x.formdef_id, x.id) for x in objects2] == list(reversed([(x.formdef_id, x.id) for x in objects]))

    # test clauses
    objects2 = sql.AnyFormData.select([st.Equal('user_id', '0')])
    assert len(objects2) == len([x for x in objects if x.user_id == '0'])

    objects2 = sql.AnyFormData.select([st.Equal('is_at_endpoint', True)])
    assert len(objects2) == len([x for x in objects if x.status in ('wf-rejected', 'wf-finished')])

    objects2 = sql.AnyFormData.select([st.Equal('submission_channel', 'mail')])
    assert len(objects2) == len([x for x in objects if x.submission_channel == 'mail'])
    assert objects2[0].submission_channel == 'mail'

    # test offset/limit
    objects2 = sql.AnyFormData.select(order_by='receipt_time', limit=10, offset=0)
    assert [(x.formdef_id, x.id) for x in objects2] == [(x.formdef_id, x.id) for x in objects][:10]

    objects2 = sql.AnyFormData.select(order_by='receipt_time', limit=10, offset=20)
    assert [(x.formdef_id, x.id) for x in objects2] == [(x.formdef_id, x.id) for x in objects][20:30]


def test_load_all_evolutions_on_any_formdata(pub):
    drop_formdef_tables()

    now = localtime()

    cnt = 0
    for i in range(5):
        formdef = FormDef()
        formdef.name = 'test any %d' % i
        formdef.fields = []
        formdef.store()

        data_class = formdef.data_class(mode='sql')
        for j in range(20):
            formdata = data_class()
            formdata.just_created()
            formdata.user_id = '%s' % ((i + j) % 11)
            # set receipt_time to make sure all entries are unique.
            formdata.receipt_time = now + datetime.timedelta(seconds=cnt)
            formdata.status = ['wf-new', 'wf-accepted', 'wf-rejected', 'wf-finished'][(i + j) % 4]
            formdata.store()
            cnt += 1

    objects = sql.AnyFormData.select()
    assert len(objects) == 100
    assert len([x for x in objects if x._evolution is None]) == 100
    sql.AnyFormData.load_all_evolutions(objects)
    assert len([x for x in objects if x._evolution is not None]) == 100


def test_store_on_any_formdata(pub):
    drop_formdef_tables()

    formdef = FormDef()
    formdef.name = 'test any store'
    formdef.fields = []
    formdef.store()

    data_class = formdef.data_class(mode='sql')
    formdata = data_class()
    formdata.just_created()
    formdata.receipt_time = localtime()
    formdata.store()

    objects = sql.AnyFormData.select()
    assert len(objects) == 1
    with pytest.raises(TypeError):
        objects[0].store()


def test_geoloc_in_global_view(pub):
    drop_formdef_tables()

    formdef = FormDef()
    formdef.name = 'test no geoloc'
    formdef.fields = []
    formdef.store()

    formdef2 = FormDef()
    formdef2.name = 'test with geoloc'
    formdef2.fields = []
    formdef2.geolocations = {'base': 'Plop'}
    formdef2.store()

    data_class = formdef.data_class(mode='sql')
    formdata = data_class()
    formdata.just_created()
    formdata.store()

    data_class = formdef2.data_class(mode='sql')
    formdata = data_class()
    formdata.just_created()
    formdata.geolocations = {'base': {'lat': 12, 'lon': 21}}
    formdata.store()

    # test generic select
    objects = sql.AnyFormData.select()
    assert len(objects) == 2

    # test clauses
    objects2 = sql.AnyFormData.select([st.Null('geoloc_base_x')])
    assert len(objects2) == 1
    assert not objects2[0].geolocations

    objects2 = sql.AnyFormData.select([st.NotNull('geoloc_base_x')])
    assert len(objects2) == 1
    assert int(objects2[0].geolocations['base']['lat']) == formdata.geolocations['base']['lat']
    assert int(objects2[0].geolocations['base']['lon']) == formdata.geolocations['base']['lon']


def test_order_by_formdef_name_in_global_view(pub):
    drop_formdef_tables()

    formdef = FormDef()
    formdef.name = 'test A'
    formdef.store()

    formdef2 = FormDef()
    formdef2.name = 'test B'
    formdef2.store()

    data_class = formdef.data_class(mode='sql')
    formdata1 = data_class()
    formdata1.just_created()
    formdata1.store()

    data_class = formdef2.data_class(mode='sql')
    formdata2 = data_class()
    formdata2.just_created()
    formdata2.store()

    objects = sql.AnyFormData.select(order_by='formdef_name')
    assert [x.id_display for x in objects] == [formdata1.id_display, formdata2.id_display]
    objects = sql.AnyFormData.select(order_by='-formdef_name')
    assert [x.id_display for x in objects] == [formdata2.id_display, formdata1.id_display]


def test_actions_roles(pub):
    drop_formdef_tables()
    _, cur = sql.get_connection_and_cursor()

    wf = Workflow(name='test endpoint')
    st1 = wf.add_status('Status1', 'st1')
    wf.add_status('Status2', 'st2')

    commentable = st1.add_action('commentable', id='_commentable')
    commentable.by = ['_submitter', '1']

    wf.store()
    assert [x.id for x in wf.get_endpoint_status()] == ['st2']

    formdef = FormDef()
    formdef.name = 'test actions roles'
    formdef.fields = []
    formdef.workflow = wf
    formdef.store()

    data_class = formdef.data_class(mode='sql')
    formdata = data_class()
    formdata.status = 'wf-st1'
    formdata.store()
    formdata_id = formdata.id
    formdata = data_class()
    formdata.status = 'wf-st2'
    formdata.store()

    cur.execute('''SELECT COUNT(*) FROM wcs_all_forms''')
    assert bool(cur.fetchone()[0] == 2)

    cur.execute(
        '''SELECT COUNT(*) FROM wcs_all_forms
                    WHERE actions_roles_array && ARRAY['5', '1', '4']'''
    )
    assert bool(cur.fetchone()[0] == 1)

    # check a change to workflow is reflected in the database
    st1.items[0].by = ['2', '3']
    wf.store()

    cur.execute(
        '''SELECT COUNT(*) FROM wcs_all_forms
                    WHERE actions_roles_array && ARRAY['5', '1', '4']'''
    )
    assert bool(cur.fetchone()[0] == 0)

    cur.execute(
        '''SELECT COUNT(*) FROM wcs_all_forms
                    WHERE actions_roles_array && ARRAY['2', '3']'''
    )
    assert bool(cur.fetchone()[0] == 1)

    # using criterias
    criterias = [st.Intersects('actions_roles_array', ['2', '3'])]
    total_count = sql.AnyFormData.count(criterias)
    formdatas = sql.AnyFormData.select(criterias)
    assert total_count == 1
    assert formdatas[0].id == formdata_id


def test_last_update_time(pub):
    drop_formdef_tables()
    _, cur = sql.get_connection_and_cursor()

    wf = Workflow(name='test last update time')
    st1 = wf.add_status('Status1', 'st1')

    commentable = st1.add_action('commentable', id='_commentable')
    commentable.by = ['_submitter', '_receiver']
    wf.store()

    formdef = FormDef()
    formdef.name = 'test last update time'
    formdef.fields = []
    formdef.workflow = wf
    formdef.store()

    data_class = formdef.data_class(mode='sql')
    formdata1 = data_class()
    formdata1.status = 'wf-st1'
    formdata1.just_created()
    formdata1.evolution[0].comment = 'comment'
    formdata1.jump_status('st1')  # will add another evolution entry
    formdata1.evolution[0].time = make_aware(datetime.datetime(2015, 1, 1, 0, 0, 0))
    formdata1.evolution[1].time = make_aware(datetime.datetime(2015, 1, 2, 0, 0, 0))
    formdata1.store()

    formdata2 = data_class()
    formdata2.status = 'wf-st1'
    formdata2.just_created()
    formdata2.evolution[0].comment = 'comment'
    formdata2.jump_status('st1')  # will add another evolution entry
    formdata2.evolution[0].time = make_aware(datetime.datetime(2015, 1, 3, 0, 0, 0))
    formdata2.evolution[1].time = make_aware(datetime.datetime(2015, 1, 4, 0, 0, 0))
    formdata2.store()

    cur.execute('''SELECT COUNT(*) FROM wcs_all_forms''')
    assert bool(cur.fetchone()[0] == 2)

    cur.execute('''SELECT id FROM wcs_all_forms WHERE last_update_time = '2015-01-02 00:00' ''')
    assert bool(cur.fetchone()[0] == formdata1.id)

    cur.execute('''SELECT id FROM wcs_all_forms WHERE last_update_time = '2015-01-04 00:00' ''')
    assert bool(cur.fetchone()[0] == formdata2.id)


def test_view_formdef_name(pub):
    drop_formdef_tables()
    _, cur = sql.get_connection_and_cursor()

    formdef1 = FormDef()
    formdef1.name = 'test formdef name 1'
    formdef1.fields = []
    formdef1.store()

    data_class = formdef1.data_class()
    formdata1 = data_class()
    formdata1.just_created()
    formdata1.store()

    formdef2 = FormDef()
    formdef2.name = 'test formdef name 2'
    formdef2.fields = []
    formdef2.store()

    data_class = formdef2.data_class()
    formdata2 = data_class()
    formdata2.just_created()
    formdata2.store()

    cur.execute('''SELECT COUNT(*) FROM wcs_all_forms''')
    assert bool(cur.fetchone()[0] == 2)

    cur.execute('''SELECT formdef_id FROM wcs_all_forms WHERE formdef_name = 'test formdef name 1' ''')
    assert bool(str(cur.fetchone()[0]) == str(formdef1.id))

    cur.execute('''SELECT formdef_id FROM wcs_all_forms WHERE formdef_name = 'test formdef name 2' ''')
    assert bool(str(cur.fetchone()[0]) == str(formdef2.id))


def test_view_user_name(pub):
    drop_formdef_tables()
    _, cur = sql.get_connection_and_cursor()

    formdef1 = FormDef()
    formdef1.name = 'test user name'
    formdef1.fields = []
    formdef1.store()

    sql.SqlUser.wipe()
    user = sql.SqlUser()
    user.name = 'Foobar'
    user.store()

    data_class = formdef1.data_class()
    formdata1 = data_class()
    formdata1.just_created()
    formdata1.store()

    data_class = formdef1.data_class()
    formdata2 = data_class()
    formdata2.user_id = user.id
    formdata2.just_created()
    formdata2.store()

    cur.execute('''SELECT user_name FROM wcs_all_forms WHERE id = %s ''', (formdata1.id,))
    assert bool(cur.fetchone()[0] is None)

    cur.execute('''SELECT user_name FROM wcs_all_forms WHERE id = %s ''', (formdata2.id,))
    assert bool(cur.fetchone()[0] == user.name)


def test_select_formdata_after_formdef_removal(pub):
    drop_formdef_tables()

    for _ in range(2):
        formdef = FormDef()
        formdef.name = 'test formdef removal'
        formdef.fields = []
        formdef.store()

        data_class = formdef.data_class(mode='sql')
        formdata = data_class()
        formdata.just_created()
        formdata.store()

    # test generic select
    objects = sql.AnyFormData.select()
    assert len(objects) == 2

    formdef.remove_self()

    objects = sql.AnyFormData.select()
    assert len(objects) == 1


def test_views_submission_info(pub):
    drop_formdef_tables()
    _, cur = sql.get_connection_and_cursor()

    formdef = FormDef()
    formdef.name = 'test backoffice submission'
    formdef.fields = []
    formdef.store()

    data_class = formdef.data_class(mode='sql')
    formdata1 = data_class()
    formdata1.submission_channel = 'mail'
    formdata1.backoffice_submission = True
    formdata1.store()

    formdata2 = data_class()
    formdata2.backoffice_submission = False
    formdata2.store()

    cur.execute('''SELECT COUNT(*) FROM wcs_all_forms WHERE backoffice_submission IS TRUE''')
    assert bool(cur.fetchone()[0] == 1)

    cur.execute('''SELECT COUNT(*) FROM wcs_all_forms WHERE backoffice_submission IS FALSE''')
    assert bool(cur.fetchone()[0] == 1)

    cur.execute('''SELECT COUNT(*) FROM wcs_all_forms WHERE submission_channel = %s''', ('mail',))
    assert bool(cur.fetchone()[0] == 1)


def test_get_formdef_new_id(pub):
    test1_formdef = FormDef()
    test1_formdef.name = 'new formdef'
    test1_formdef.fields = []
    test1_formdef.store()
    test1_id = test1_formdef.id
    test1_table_name = test1_formdef.table_name
    test1_formdef.remove_self()

    test2_formdef = FormDef()
    test2_formdef.name = 'new formdef'
    test2_formdef.fields = []
    test2_formdef.store()
    assert test1_id != test2_formdef.id
    assert test1_table_name != test2_formdef.table_name


def test_criticality_levels(pub):
    drop_formdef_tables()

    workflow1 = Workflow(name='criticality1')
    workflow1.criticality_levels = [
        WorkflowCriticalityLevel(name='green'),
        WorkflowCriticalityLevel(name='yellow'),
        WorkflowCriticalityLevel(name='red'),
        WorkflowCriticalityLevel(name='redder'),
        WorkflowCriticalityLevel(name='reddest'),
    ]
    workflow1.store()

    workflow2 = Workflow(name='criticality2')
    workflow2.criticality_levels = [
        WorkflowCriticalityLevel(name='green'),
        WorkflowCriticalityLevel(name='reddest'),
    ]
    workflow2.store()

    formdef1 = FormDef()
    formdef1.name = 'test criticality levels 1'
    formdef1.fields = []
    formdef1.workflow_id = workflow1.id
    formdef1.store()

    formdef2 = FormDef()
    formdef2.name = 'test criticality levels 2'
    formdef2.fields = []
    formdef2.workflow_id = workflow2.id
    formdef2.store()

    data_class = formdef1.data_class(mode='sql')
    for i in range(5):
        formdata = data_class()
        formdata.set_criticality_level(i)
        formdata.store()

    data_class = formdef2.data_class(mode='sql')
    for i in range(2):
        formdata = data_class()
        formdata.set_criticality_level(i)
        formdata.store()

    objects = sql.AnyFormData.select(order_by='-criticality_level')
    # make sure first two formdata are the highest priority ones, and the last
    # two formdata are the lowest priority ones.
    assert objects[0].get_criticality_level_object().name == 'reddest'
    assert objects[1].get_criticality_level_object().name == 'reddest'
    assert objects[-1].get_criticality_level_object().name == 'green'
    assert objects[-2].get_criticality_level_object().name == 'green'


def test_view_performances(pub):
    pytest.skip('takes too much time')

    drop_formdef_tables()
    nb_users = 1000
    nb_roles = 10
    nb_workflows = 5
    nb_formdefs = 10
    nb_fields = 10
    nb_formdatas = 1000

    nb_users = 10
    nb_formdatas = 10000

    random.seed('foobar')

    # create users
    sql.SqlUser.wipe()
    users = []
    for i in range(nb_users):
        user = sql.SqlUser()
        user.name = 'user %s' % i
        user.store()
        users.append(user)

    # create roles
    roles = []
    for i in range(nb_roles):
        role = pub.role_class(name='role%s' % i)
        role.store()
        roles.append(role)

    # create workflows
    workflows = []
    for i in range(nb_workflows):
        workflow = Workflow(name='test perf wf %s' % i)
        for j in range(5):
            status = workflow.add_status('Status %d' % j, 'st%s' % j)
            commentable = status.add_action('commentable', id='_commentable%s' % j)
            commentable.by = [random.choice(roles).id, random.choice(roles).id]
            if j != 4:
                jump = status.add_action('jump', id='_jump%s' % j)
                jump.by = []
                jump.timeout = 5
                jump.mode = 'timeout'
                jump.status = 'st%s' % (j + 1)
        workflow.store()
        workflows.append(workflow)

    # create formdefs
    formdefs = []
    for i in range(nb_formdefs):
        formdef = FormDef()
        formdef.name = 'test performance view %s' % i
        formdef.fields = []
        for j in range(nb_fields):
            formdef.fields.append(fields.StringField(id=str(j + 1), label='string'))
        formdef.workflow_id = workflows[i % 5].id
        formdef.store()
        formdefs.append(formdef)

    print('create formdatas')
    # create formdatas
    for i in range(nb_formdatas):
        data_class = random.choice(formdefs).data_class()
        formdata = data_class()
        formdata.data = {}
        for j in range(10):
            formdata.data[str(j + 1)] = ''.join(
                [random.choice(string.letters) for x in range(random.randint(10, 30))]
            )
        formdata.user_id = random.choice(users).id
        formdata.status = 'wf-st1'
        formdata.just_created()
        for j in range(5):
            formdata.jump_status('st%s' % (j + 2))
            if random.random() < 0.5:
                break
    print('done')

    t0 = time.time()
    user_roles = [random.choice(roles).id, random.choice(roles).id]
    criterias = []
    criterias.append(st.NotEqual('status', 'draft'))
    criterias.append(st.Equal('is_at_endpoint', False))
    criterias.append(st.Intersects('actions_roles_array', user_roles))
    sql.AnyFormData.select(criterias, order_by='receipt_time', limit=20, offset=0)
    print(time.time() - t0)
    assert (time.time() - t0) < 0.5


def test_migration_30_anonymize_evo_who(pub):
    formdef = FormDef()
    formdef.name = 'tests migration 24'
    formdef.fields = []
    formdef.store()

    user = sql.SqlUser()
    user.name = 'JohnDoe'
    user.store()

    klass = formdef.data_class()
    formdata = klass()
    formdata.evolution = []
    formdata.anonymised = datetime.datetime.now()
    evo = Evolution(formdata)
    evo.who = user.id
    evo.time = localtime()
    formdata.evolution.append(evo)
    formdata.store()

    conn, cur = sql.get_connection_and_cursor()
    cur.execute('UPDATE wcs_meta SET value = 29 WHERE key = %s', ('sql_level',))
    conn.commit()
    cur.close()

    conn, cur = sql.get_connection_and_cursor()
    cur.execute('SELECT COUNT(*) FROM %s_evolutions WHERE who IS NULL' % formdef.table_name)
    assert cur.fetchone() == (0,)
    cur.execute('SELECT COUNT(*) FROM wcs_meta WHERE key = %s AND value::integer > 29', ('sql_level',))
    assert cur.fetchone() == (0,)
    conn.commit()
    cur.close()

    sql.migrate()

    conn, cur = sql.get_connection_and_cursor()
    cur.execute('SELECT COUNT(*) FROM %s_evolutions WHERE who IS NULL' % formdef.table_name)
    assert cur.fetchone() == (1,)
    cur.execute('SELECT COUNT(*) FROM wcs_meta WHERE key = %s AND value::integer > 29', ('sql_level',))
    assert cur.fetchone() == (1,)
    conn.commit()
    cur.close()


def test_migration_31_user_label(formdef):
    conn, cur = sql.get_connection_and_cursor()
    cur.execute('UPDATE wcs_meta SET value = 30 WHERE key = %s', ('sql_level',))
    cur.execute('DROP TABLE wcs_all_forms CASCADE')

    cur.execute('ALTER TABLE formdata_1_tests DROP COLUMN user_label')
    sql.drop_views(formdef, conn, cur)
    assert not column_exists_in_table(cur, 'formdata_1_tests', 'user_label')

    sql.migrate()

    assert column_exists_in_table(cur, 'formdata_1_tests', 'user_label')
    assert column_exists_in_table(cur, 'wcs_all_forms', 'user_label')
    assert migration_level(cur) >= 31

    conn.commit()
    cur.close()


def test_migration_38_submission_agent_id(pub):
    for formdef in FormDef.select():
        formdef.data_class().wipe()
    data_class = formdef.data_class(mode='sql')
    formdata = data_class()
    formdata.data = {}
    formdata.status = 'wf-0'
    formdata.submission_context = {'agent_id': 12}
    formdata.store()

    conn, cur = sql.get_connection_and_cursor()
    cur.execute('UPDATE wcs_meta SET value = 37 WHERE key = %s', ('sql_level',))
    cur.execute('DROP TABLE wcs_all_forms CASCADE')

    cur.execute('ALTER TABLE formdata_1_tests DROP COLUMN submission_agent_id')
    sql.drop_views(formdef, conn, cur)
    assert not column_exists_in_table(cur, 'formdata_1_tests', 'submission_agent_id')

    sql.migrate()

    assert sql.is_reindex_needed('formdata', conn=conn, cur=cur) is True
    assert column_exists_in_table(cur, 'formdata_1_tests', 'submission_agent_id')
    assert column_exists_in_table(cur, 'wcs_all_forms', 'submission_agent_id')
    assert migration_level(cur) >= 38

    sql.reindex()

    cur.execute('''SELECT submission_agent_id FROM wcs_all_forms WHERE id = %s ''', (formdata.id,))
    assert cur.fetchone()[0] == '12'

    conn.commit()
    cur.close()


def test_migration_40_user_is_active(pub):
    conn, cur = sql.get_connection_and_cursor()
    cur.execute('UPDATE wcs_meta SET value = 39 WHERE key = %s', ('sql_level',))
    conn.commit()
    cur.close()

    sql.SqlUser.wipe()
    user = sql.SqlUser()
    user.name = 'Jean Sénisme'
    user.deleted_timestamp = datetime.datetime.now()
    user.store()

    user2 = sql.SqlUser()
    user2.name = 'Jean II'
    user2.store()
    assert sql.SqlUser.count() == 2

    conn, cur = sql.get_connection_and_cursor()
    cur.execute('ALTER TABLE users DROP COLUMN is_active')
    assert not column_exists_in_table(cur, 'users', 'is_active')
    sql.migrate()
    assert column_exists_in_table(cur, 'users', 'is_active')
    assert migration_level(cur) >= 40

    assert sql.SqlUser.count() == 2
    assert sql.SqlUser.get(id=user.id).is_active is False
    assert sql.SqlUser.get(id=user2.id).is_active is True


def test_migration_58_workflow_roles_dict(pub):
    FormDef.wipe()

    formdef = FormDef()
    formdef.name = 'tests migration 58'
    formdef.fields = []
    formdef.workflow_roles = {'_receiver': '123'}
    formdef.store()

    formdata = formdef.data_class()()
    formdata.store()

    formdata = formdef.data_class()()
    formdata.workflow_roles = {'_receiver': ['_user:123', '_user:456']}
    formdata.store()

    conn, cur = sql.get_connection_and_cursor()
    cur.execute('UPDATE wcs_meta SET value = 57 WHERE key = %s', ('sql_level',))
    conn.commit()
    cur.close()

    conn, cur = sql.get_connection_and_cursor()
    cur.execute('ALTER TABLE %s DROP COLUMN workflow_merged_roles_dict' % formdef.table_name)
    sql.migrate()
    assert column_exists_in_table(cur, formdef.table_name, 'workflow_merged_roles_dict')
    assert migration_level(cur) >= 58
    assert sql.is_reindex_needed('formdata', conn=conn, cur=cur) is True
    assert formdef.data_class().count([st.Null('workflow_merged_roles_dict')]) == 2
    sql.reindex()
    assert formdef.data_class().count([st.Null('workflow_merged_roles_dict')]) == 0


def test_workflow_roles_dict_change(pub):
    FormDef.wipe()

    formdef = FormDef()
    formdef.name = 'test_workflow_roles_dict_change'
    formdef.fields = []
    formdef.workflow_roles = {'_receiver': '123'}
    formdef.store()

    formdata = formdef.data_class()()
    formdata.workflow_roles = {}
    formdata.store()

    conn, cur = sql.get_connection_and_cursor()
    cur.execute('SELECT workflow_merged_roles_dict FROM %s WHERE id = %s' % (formdef.table_name, formdata.id))
    merged_roles_dict = cur.fetchone()[0]
    assert merged_roles_dict == {'_receiver': ['123']}
    conn.commit()
    cur.close()

    formdata = formdef.data_class()()
    formdata.workflow_roles = {'_receiver': ['_user:123', '_user:456']}
    formdata.store()

    conn, cur = sql.get_connection_and_cursor()
    cur.execute('SELECT workflow_merged_roles_dict FROM %s WHERE id = %s' % (formdef.table_name, formdata.id))
    merged_roles_dict = cur.fetchone()[0]
    assert merged_roles_dict == {'_receiver': ['_user:123', '_user:456']}
    conn.commit()
    cur.close()

    formdef.workflow_roles = {'_receiver': '234'}
    formdef.store()
    formdef.data_class().rebuild_security()

    conn, cur = sql.get_connection_and_cursor()
    cur.execute('SELECT workflow_merged_roles_dict FROM %s WHERE id = %s' % (formdef.table_name, formdata.id))
    merged_roles_dict = cur.fetchone()[0]
    assert merged_roles_dict == {'_receiver': ['_user:123', '_user:456']}
    conn.commit()
    cur.close()


def test_migration_59_all_forms_table(pub):
    FormDef.wipe()
    drop_formdef_tables()

    formdef = FormDef()
    formdef.name = 'tests migration 59'
    formdef.store()

    formdata = formdef.data_class()()
    formdata.store()

    conn, cur = sql.get_connection_and_cursor()
    cur.execute('DROP TABLE wcs_all_forms CASCADE')
    cur.execute(
        'DROP TRIGGER %s ON %s' % (sql.get_formdef_trigger_name(formdef), sql.get_formdef_table_name(formdef))
    )
    cur.execute('UPDATE wcs_meta SET value = 58 WHERE key = %s', ('sql_level',))
    conn.commit()
    cur.close()

    conn, cur = sql.get_connection_and_cursor()
    sql.migrate()

    cur.execute(
        '''SELECT 1 FROM pg_trigger WHERE tgname = '%s' ''' % (sql.get_formdef_trigger_name(formdef),)
    )
    assert len(cur.fetchall()) == 1

    objects = sql.AnyFormData.select()
    assert len(objects) == 1

    formdata = formdef.data_class()()
    formdata.store()
    objects = sql.AnyFormData.select()
    assert len(objects) == 2


def test_migration_82_statistics_data(formdef):
    conn, cur = sql.get_connection_and_cursor()
    cur.execute('UPDATE wcs_meta SET value = 79 WHERE key = %s', ('sql_level',))
    cur.execute('DROP TABLE wcs_all_forms CASCADE')

    cur.execute('ALTER TABLE formdata_1_tests DROP COLUMN statistics_data')
    sql.drop_views(formdef, conn, cur)
    assert not column_exists_in_table(cur, 'formdata_1_tests', 'statistics_data')

    sql.migrate()

    assert column_exists_in_table(cur, 'formdata_1_tests', 'statistics_data')
    assert column_exists_in_table(cur, 'wcs_all_forms', 'statistics_data')
    assert migration_level(cur) >= 82

    conn.commit()
    cur.close()


def test_migration_86_card_uuid(pub):
    CardDef.wipe()

    carddef = CardDef()
    carddef.name = 'tests'
    carddef.fields = [fields.StringField(id='0', label='string')]
    carddef.store()

    carddata = carddef.data_class()()
    carddata.data['0'] = 'blah'
    carddata.store()

    assert carddef.data_class().get(carddata.id).uuid

    conn, cur = sql.get_connection_and_cursor()
    cur.execute('UPDATE wcs_meta SET value = 85 WHERE key = %s', ('sql_level',))

    # drop uuid column
    cur.execute('ALTER TABLE %s DROP COLUMN uuid' % sql.get_formdef_table_name(carddef))
    assert not column_exists_in_table(cur, sql.get_formdef_table_name(carddef), 'uuid')

    sql.migrate()

    assert column_exists_in_table(cur, sql.get_formdef_table_name(carddef), 'uuid')
    assert migration_level(cur) >= 86

    assert carddef.data_class().get(carddata.id).uuid

    conn.commit()
    cur.close()


def test_migration_formdata_uuid(pub):
    FormDef.wipe()

    formdef = FormDef()
    formdef.name = 'tests'
    formdef.fields = [fields.StringField(id='0', label='string')]
    formdef.store()

    formdata1 = formdef.data_class()()
    formdata1.store()

    formdata2 = formdef.data_class()()
    formdata2.store()

    conn, cur = sql.get_connection_and_cursor()
    cur.execute('UPDATE wcs_meta SET value = 167 WHERE key = %s', ('sql_level',))

    # drop uuid column
    cur.execute('ALTER TABLE %s DROP COLUMN uuid' % sql.get_formdef_table_name(formdef))
    assert not column_exists_in_table(cur, sql.get_formdef_table_name(formdef), 'uuid')

    sql.migrate()

    assert column_exists_in_table(cur, sql.get_formdef_table_name(formdef), 'uuid')
    assert migration_level(cur) >= 168

    conn.commit()
    cur.close()

    assert not formdef.data_class().get(formdata1.id).uuid
    assert not formdef.data_class().get(formdata2.id).uuid

    sql.reindex()

    assert formdef.data_class().get(formdata1.id).uuid
    assert formdef.data_class().get(formdata2.id).uuid


def test_migration_formdata_page_id(pub):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'tests migration formdata page_id'
    formdef.fields = []
    formdef.store()

    formdata = formdef.data_class()()
    formdata.page_id = 'xxx'
    formdata.store()

    assert formdef.data_class().get(formdata.id).page_id == 'xxx'

    conn, cur = sql.get_connection_and_cursor()
    cur.execute('UPDATE wcs_meta SET value = 100 WHERE key = %s', ('sql_level',))

    # drop page_id column
    cur.execute('ALTER TABLE %s DROP COLUMN page_id' % sql.get_formdef_table_name(formdef))
    assert not column_exists_in_table(cur, sql.get_formdef_table_name(formdef), 'page_id')

    sql.migrate()

    assert column_exists_in_table(cur, sql.get_formdef_table_name(formdef), 'page_id')
    assert migration_level(cur) >= 101

    conn.commit()
    cur.close()


def test_logged_error_store_without_integrity_error(pub, sql_queries):
    LoggedError.record('there was an error')

    assert len(sql_queries) == 2
    assert 'SELECT' in sql_queries[0]
    assert 'INSERT' in sql_queries[1]
    sql_queries.clear()

    LoggedError.record('there was an error')
    assert len(sql_queries) == 2
    assert 'SELECT' in sql_queries[0]
    assert 'UPDATE' in sql_queries[1]


def test_sql_import_zip_create_tables(pub):
    c = io.BytesIO()
    with zipfile.ZipFile(c, 'w') as z:
        z.writestr(
            'formdefs_xml/123',
            '''<?xml version="1.0"?>
<formdef id="123">
  <name>crash</name>
  <url_name>crash</url_name>
  <internal_identifier>different-identifier</internal_identifier>
  <fields>
  </fields>
</formdef>
''',
        )
    c.seek(0)

    pub.import_zip(c)

    formdef = FormDef.get(123)

    conn, cur = sql.get_connection_and_cursor()
    assert table_exists(cur, formdef.table_name)
    conn.commit()
    cur.close()


def test_lazyevolutionlist():
    dump = pickle.dumps([1, 2])

    lazy = sql.LazyEvolutionList(dump)
    assert len(lazy) == 2

    lazy = sql.LazyEvolutionList(dump)
    assert str(lazy).startswith('[')

    lazy = sql.LazyEvolutionList(dump)
    lazy[0] = 'x'
    assert lazy[0] == 'x'

    lazy = sql.LazyEvolutionList(dump)
    del lazy[0]
    assert len(lazy) == 1

    lazy = sql.LazyEvolutionList(dump)
    lazy += [1]
    assert len(lazy) == 3
    assert lazy[2] == 1

    lazy = sql.LazyEvolutionList(dump)
    assert 1 in lazy

    lazy = sql.LazyEvolutionList(dump)
    assert list(pickle.loads(pickle.dumps(lazy))) == list(lazy)


def test_form_tokens_migration(pub):
    conn, cur = sql.get_connection_and_cursor()
    cur.execute('UPDATE wcs_meta SET value = 70 WHERE key = %s', ('sql_level',))
    conn.commit()
    cur.close()

    form_tokens_dir = os.path.join(pub.app_dir, 'form_tokens')
    if not os.path.exists(form_tokens_dir):
        os.mkdir(form_tokens_dir)
    with open(os.path.join(form_tokens_dir, '1234'), 'w'):
        pass

    assert os.path.exists(os.path.join(form_tokens_dir, '1234'))
    sql.migrate()
    assert not os.path.exists(os.path.join(form_tokens_dir, '1234'))
    assert not os.path.exists(form_tokens_dir)


def test_nonces_migration(pub):
    conn, cur = sql.get_connection_and_cursor()
    cur.execute('UPDATE wcs_meta SET value = 76 WHERE key = %s', ('sql_level',))
    conn.commit()
    cur.close()

    nonces_dir = os.path.join(pub.app_dir, 'nonces')
    if not os.path.exists(nonces_dir):
        os.mkdir(nonces_dir)
    with open(os.path.join(nonces_dir, '1234'), 'w'):
        pass

    assert os.path.exists(os.path.join(nonces_dir, '1234'))
    sql.migrate()
    assert not os.path.exists(os.path.join(nonces_dir, '1234'))
    assert not os.path.exists(nonces_dir)


def test_workflow_traces_initial_migration(pub):
    FormDef.wipe()

    formdef = FormDef()
    formdef.name = 'tests'
    formdef.fields = []
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.evolution[-1].time = localtime() - datetime.timedelta(seconds=11)
    action_part = ActionsTracingEvolutionPart()
    action_part.event = 'frontoffice-created'
    action_part.actions = [
        (datetime.datetime.now() - datetime.timedelta(seconds=10), 'email', '1'),
        (datetime.datetime.now() - datetime.timedelta(seconds=9), 'email', '2'),
    ]
    formdata.evolution[-1].add_part(action_part)
    formdata.evolution.append(Evolution(formdata))
    formdata.evolution[-1].time = localtime() - datetime.timedelta(seconds=8)
    action_part = ActionsTracingEvolutionPart()
    action_part.event = 'timeout-jump'
    action_part.event_args = ('xxx',)
    action_part.actions = [
        (datetime.datetime.now() - datetime.timedelta(seconds=7), 'email', '3'),
    ]
    formdata.evolution[-1].add_part(action_part)
    formdata.evolution.append(Evolution(formdata))
    formdata.evolution[-1].time = localtime() - datetime.timedelta(seconds=6)
    action_part = ActionsTracingEvolutionPart()
    action_part.event = 'global-action-timeout'
    action_part.event_args = ('xxx2', 'xxx3')
    action_part.actions = [
        (datetime.datetime.now() - datetime.timedelta(seconds=5), 'email', '4'),
    ]
    formdata.evolution[-1].add_part(action_part)
    formdata.evolution.append(Evolution(formdata))
    formdata.evolution[-1].time = localtime() - datetime.timedelta(seconds=4)
    action_part = ActionsTracingEvolutionPart()
    action_part.event = 'global-api-trigger'
    action_part.event_args = ('xxx2',)
    action_part.actions = [
        (datetime.datetime.now() - datetime.timedelta(seconds=3), 'email', '5'),
    ]
    formdata.evolution[-1].add_part(action_part)

    formdata.store()

    formdata2 = formdef.data_class()()
    formdata2.just_created()
    formdata2.evolution[-1].time = localtime() - datetime.timedelta(seconds=2)
    action_part = ActionsTracingEvolutionPart()
    action_part.event = 'workflow-created'
    action_part.external_workflow_id = '1'
    action_part.external_status_id = '1'
    action_part.external_item_id = '1'
    action_part.event_args = ('1-1',)
    action_part.actions = [
        (datetime.datetime.now() - datetime.timedelta(seconds=1), 'email', '6'),
    ]
    formdata2.evolution[-1].add_part(action_part)
    formdata2.store()

    conn, cur = sql.get_connection_and_cursor()
    cur.execute('UPDATE wcs_meta SET value = 73 WHERE key = %s', ('sql_level',))

    time_before_migration = tz_now()
    sql.migrate()
    assert sql.is_reindex_needed('workflow_traces_migration', conn=conn, cur=cur) is True
    conn.commit()
    cur.close()
    sql.reindex()

    formdata.refresh_from_storage()
    formdata2.refresh_from_storage()
    assert all(x.timestamp < time_before_migration for x in formdata.get_workflow_traces())
    assert [x.event or x.action_item_key for x in formdata.get_workflow_traces()] == [
        'frontoffice-created',
        'email',
        'email',
        'timeout-jump',
        'email',
        'global-action-timeout',
        'email',
        'global-api-trigger',
        'email',
    ]
    assert [x.event or x.action_item_key for x in formdata2.get_workflow_traces()] == [
        'workflow-created',
        'email',
    ]

    assert not any(isinstance(x, ActionsTracingEvolutionPart) for x in formdata.iter_evolution_parts())
    assert not any(isinstance(x, ActionsTracingEvolutionPart) for x in formdata2.iter_evolution_parts())


def test_migrate_wscall_id_in_related_objects(pub):
    ApplicationElement.wipe()
    pub.snapshot_class.wipe()
    NamedWsCall.wipe()

    ws = NamedWsCall()
    ws.name = 'test'
    ws.store()

    element = ApplicationElement()
    element.application_id = 123
    element.object_type = 'wscall'
    element.object_id = 'test'
    element.store()

    element2 = ApplicationElement()
    element2.application_id = 123
    element2.object_type = 'wscall'
    element2.object_id = 'unknown'
    element2.store()

    snapshot = pub.snapshot_class()
    snapshot.object_type = 'wscall'
    snapshot.object_id = 'test'
    snapshot.store()

    NamedWsCall.migrate_identifiers()
    assert ApplicationElement.get(element.id).object_id == str(ws.id)
    assert ApplicationElement.get(element2.id, ignore_errors=True) is None
    assert pub.snapshot_class.get(snapshot.id).object_id == str(ws.id)


def test_computed_field_bad_content(pub):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [
        fields.ComputedField(
            id='1',
            label='computed',
            varname='computed',
        ),
    ]
    formdef.store()
    formdef.data_class().wipe()

    conn, cur = sql.get_connection_and_cursor()
    sql.drop_views(formdef, conn, cur)
    cur.execute(
        '''ALTER TABLE %s ALTER COLUMN %s TYPE VARCHAR'''
        % (formdef.table_name, sql.get_field_id(formdef.fields[0]))
    )
    conn.commit()
    cur.close()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.data = {'1': 'bad value'}
    formdata.store()

    with pytest.raises(ValueError) as excinfo:
        formdata = formdef.data_class().get(formdata.id)
    assert (
        str(excinfo.value)
        == 'bad data {"data": "bad value", "@type": "computed-data"} (type <class \'str\'>) in computed field 1'
    )


@pytest.mark.parametrize('formdef_class', [FormDef, CardDef])
def test_sql_data_views(pub_with_views, formdef_class):
    FormDef.wipe()
    CardDef.wipe()
    formdef = formdef_class()
    formdef.name = 'test'
    formdef.fields = [
        fields.TitleField(id='1', label='title'),
        fields.StringField(id='2', label='label no varname'),
        fields.StringField(id='3', label='field with varname', varname='foo'),
        fields.StringField(id='4', label='field with duplicated varname (1)', varname='bar'),
        fields.StringField(id='5', label='field with duplicated varname (2)', varname='bar'),
    ]
    formdef.geolocations = {'base': 'xxx'}
    formdef.store()

    if formdef_class is FormDef:
        prefix = f'wcs_view_{formdef.id}'
    elif formdef_class is CardDef:
        prefix = f'wcs_carddata_view_{formdef.id}'

    conn, cur = sql.get_connection_and_cursor()
    assert not column_exists_in_table(cur, f'{prefix}_test', 'f1_title')
    assert column_exists_in_table(cur, f'{prefix}_test', 'f2_label_no_varname')
    assert column_exists_in_table(cur, f'{prefix}_test', 'f_bar')
    assert column_exists_in_table(cur, f'{prefix}_test', 'f_bar_2')
    assert column_exists_in_table(cur, f'{prefix}_test', 'geoloc_base_x')
    conn.commit()
    cur.close()


def test_sql_integrity_errors(pub):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [
        fields.StringField(id='1', label='string'),
    ]
    formdef.store()
    assert not formdef.sql_integrity_errors

    formdef.fields = [
        fields.FileField(id='1', label='string'),
    ]
    formdef.store()
    assert formdef.sql_integrity_errors == {'1': {'got': 'character varying', 'expected': 'bytea'}}


def test_testdef_user_uuid_migration(pub):
    pub.user_class.wipe()

    user = pub.user_class(name='new user')
    user.email = 'new@example.com'
    user.store()

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.user_id = user.id

    testdef = TestDef()
    testdef.name = 'First test'
    testdef.object_type = formdef.get_table_name()
    testdef.object_id = str(formdef.id)
    testdef.data = {
        'data': [],
        'user': formdata.user.get_json_export_dict(),
    }
    testdef.store()

    testdef2 = TestDef()
    testdef2.name = 'First test'
    testdef2.object_type = formdef.get_table_name()
    testdef2.object_id = str(formdef.id)
    testdef2.data = {
        'data': [],
        'user': formdata.user.get_json_export_dict(),
    }
    testdef2.store()

    conn, cur = sql.get_connection_and_cursor()
    cur.execute('UPDATE wcs_meta SET value = 106 WHERE key = %s', ('sql_level',))

    sql.migrate()
    assert sql.is_reindex_needed('testdef', conn=conn, cur=cur) is True
    assert pub.user_class.count() == 1
    assert pub.test_user_class.count() == 0
    conn.commit()
    cur.close()
    sql.reindex()

    assert pub.user_class.count() == 1
    assert pub.test_user_class.count() == 1
    test_user = pub.test_user_class.select()[0]

    testdef = TestDef.get(testdef.id)
    assert not 'user' in testdef.data
    assert testdef.user_uuid == test_user.test_uuid

    testdef2 = TestDef.get(testdef2.id)
    assert not 'user' in testdef2.data
    assert testdef2.user_uuid == test_user.test_uuid


def test_migration_108_search_tokens(pub):
    CardDef.wipe()
    FormDef.wipe()

    formdef = FormDef()
    formdef.name = 'foobar'
    formdef.fields = [fields.StringField(id='1')]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {'1': 'blah'}
    formdata.store()

    conn, cur = sql.get_connection_and_cursor()
    cur.execute('DELETE FROM wcs_search_tokens')
    cur.execute('UPDATE wcs_meta SET value = 107 WHERE key = %s', ('sql_level',))
    conn.commit()
    cur.close()

    conn, cur = sql.get_connection_and_cursor()
    sql.migrate()

    assert sql.is_reindex_needed('init_search_tokens_data', conn=conn, cur=cur) is True
    sql.reindex()

    # check it's no longer needed afterwards
    assert sql.is_reindex_needed('init_search_tokens_data', conn=conn, cur=cur) is False
    cur.execute('SELECT count(*) FROM wcs_search_tokens')
    assert cur.fetchone()[0]

    conn.commit()
    cur.close()


def test_testdef_duplicated_action_uuid(pub):
    FormDef.wipe()
    pub.user_class.wipe()

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.store()

    testdef = TestDef()
    testdef.name = 'First test'
    testdef.object_type = formdef.get_table_name()
    testdef.object_id = str(formdef.id)
    testdef.data = {'data': []}

    testdef.workflow_tests.actions = [
        AssertStatus(id='1', uuid='abc'),
        AssertStatus(id='2', uuid='abc'),
        AssertStatus(id='3', uuid='def'),
    ]
    testdef.store()

    conn, cur = sql.get_connection_and_cursor()
    cur.execute('UPDATE wcs_meta SET value = 114 WHERE key = %s', ('sql_level',))

    sql.migrate()
    assert sql.is_reindex_needed('testdef', conn=conn, cur=cur) is True
    conn.commit()
    cur.close()
    sql.reindex()

    testdef = TestDef.get(testdef.id)
    assert testdef.workflow_tests.actions[0].id == '1'
    assert testdef.workflow_tests.actions[0].uuid == 'abc'
    assert testdef.workflow_tests.actions[1].id == '2'
    assert testdef.workflow_tests.actions[1].uuid != 'abc'
    assert testdef.workflow_tests.actions[2].id == '3'
    assert testdef.workflow_tests.actions[2].uuid == 'def'


def test_migration_map_data_type(pub):
    FormDef.wipe()

    formdef = FormDef()
    formdef.name = 'test map migration'
    formdef.fields = [
        fields.MapField(id='1', label='map'),
    ]
    formdef.store()

    formdata1 = formdef.data_class(mode='sql')()
    formdata1.just_created()
    formdata1.store()

    formdata2 = formdef.data_class(mode='sql')()
    formdata2.just_created()
    formdata2.store()

    formdata3 = formdef.data_class(mode='sql')()
    formdata3.just_created()
    formdata3.store()

    formdata4 = formdef.data_class(mode='sql')()
    formdata4.just_created()
    formdata4.store()

    formdata5 = formdef.data_class(mode='sql')()
    formdata5.just_created()
    formdata5.store()

    conn, cur = sql.get_connection_and_cursor()
    cur.execute('UPDATE wcs_meta SET value = 113 WHERE key = %s', ('sql_level',))
    conn.commit()
    cur.close()

    conn, cur = sql.get_connection_and_cursor()
    cur.execute('ALTER TABLE %s DROP COLUMN f1 CASCADE' % sql.get_formdef_table_name(formdef))
    cur.execute('ALTER TABLE %s ADD COLUMN f1 VARCHAR' % sql.get_formdef_table_name(formdef))
    cur.execute(
        'UPDATE ' + sql.get_formdef_table_name(formdef) + ' SET f1 = %s WHERE id = %s', ('1;2', formdata1.id)
    )
    cur.execute(
        'UPDATE ' + sql.get_formdef_table_name(formdef) + ' SET f1 = %s WHERE id = %s',
        ('1.4;2.3', formdata2.id),
    )
    cur.execute(
        'UPDATE ' + sql.get_formdef_table_name(formdef) + ' SET f1 = %s WHERE id = %s', ('', formdata3.id)
    )
    cur.execute(
        'UPDATE ' + sql.get_formdef_table_name(formdef) + ' SET f1 = %s WHERE id = %s', (None, formdata4.id)
    )
    cur.execute(
        'UPDATE ' + sql.get_formdef_table_name(formdef) + ' SET f1 = %s WHERE id = %s',
        ('garbage', formdata5.id),
    )
    conn.commit()
    cur.close()

    sql.migrate()

    conn, cur = sql.get_connection_and_cursor()
    assert migration_level(cur) >= 113
    conn.commit()
    cur.close()

    assert formdef.data_class(mode='sql').get(formdata1.id).data['1'] == {'lat': 1, 'lon': 2}
    assert formdef.data_class(mode='sql').get(formdata2.id).data['1'] == {'lat': 1.4, 'lon': 2.3}
    assert formdef.data_class(mode='sql').get(formdata3.id).data['1'] is None
    assert formdef.data_class(mode='sql').get(formdata4.id).data['1'] is None
    assert formdef.data_class(mode='sql').get(formdata5.id).data['1'] is None


@pytest.mark.parametrize('object_type', ['formdefs', 'carddefs', 'blockdefs'])
def test_migration_objectdefs_to_db(pub, object_type):
    objects_dir = os.path.join(pub.app_dir, object_type)
    shutil.rmtree(objects_dir, ignore_errors=True)
    FormDef.wipe()

    import wcs.blocks
    import wcs.carddef
    import wcs.formdef

    file_klass = {
        'blockdefs': wcs.blocks.FileBlockDef,
        'carddefs': wcs.carddef.FileCardDef,
        'formdefs': wcs.formdef.FileFormDef,
    }.get(object_type)

    db_klass = {
        'blockdefs': BlockDef,
        'carddefs': CardDef,
        'formdefs': FormDef,
    }.get(object_type)

    object1 = file_klass()
    object1.name = 'test1'
    object1.slug = 'test1'
    object1.fields = [fields.StringField(id='1', label='string')]
    object1.store()

    object2 = file_klass()
    object2.name = 'test2'
    object2.slug = 'test2'
    object2.fields = [fields.StringField(id='1', label='string')]
    object2.store()

    assert os.path.exists(os.path.join(objects_dir, str(object1.id)))
    assert os.path.exists(os.path.join(objects_dir, str(object2.id)))

    db_klass.migrate_from_files()
    shutil.rmtree(objects_dir, ignore_errors=True)
    assert db_klass.count() == 2
    assert db_klass.get(1).name == 'test1'
    assert db_klass.get(1).fields

    # check sequence is correct
    object3 = db_klass()
    object3.name = 'test3'
    object3.store()
    assert object3.id == 3


def test_migration_workflows_to_db(pub):
    import wcs.workflows

    objects_dir = os.path.join(pub.app_dir, 'workflows')
    shutil.rmtree(objects_dir, ignore_errors=True)
    Workflow.wipe()

    workflow = wcs.workflows.FileWorkflow()
    workflow.name = 'test'
    workflow.slug = 'test'
    workflow.add_status('test')
    workflow.store()

    assert os.path.exists(os.path.join(objects_dir, str(workflow.id)))

    Workflow.migrate_from_files()
    shutil.rmtree(objects_dir, ignore_errors=True)
    assert Workflow.count() == 1
    assert Workflow.get(1).name == 'test'
    assert Workflow.get(1).possible_status[0].name == 'test'

    # check sequence is correct
    wf2 = Workflow()
    wf2.name = 'test2'
    wf2.slug = 'test2'
    wf2.store()
    assert wf2.id == 2


def test_migration_afterjobs_to_db(pub):
    objects_dir = os.path.join(pub.app_dir, 'afterjobs')
    shutil.rmtree(objects_dir, ignore_errors=True)
    os.mkdir(objects_dir)

    job = AfterJob(label='test')
    job.creation_time = time.time()
    job.completion_time = time.time() + 120
    with open(os.path.join(objects_dir, str(job.id)), 'wb') as fd:
        fd.write(pickle.dumps(job))

    AfterJob.migrate_from_files()
    shutil.rmtree(objects_dir, ignore_errors=True)
    assert AfterJob.count() == 1
    assert AfterJob.get(job.id).label == 'test'


def test_afterjobs_abort_requested_migration(pub):
    _, cur = sql.get_connection_and_cursor()
    cur.execute('UPDATE wcs_meta SET value = 166 WHERE key = %s', ('sql_level',))

    # drop abort_requested column
    cur.execute('ALTER TABLE %s DROP COLUMN abort_requested' % AfterJob._table_name)
    assert not column_exists_in_table(cur, AfterJob._table_name, 'abort_requested')

    sql.migrate()

    assert column_exists_in_table(cur, AfterJob._table_name, 'abort_requested')
    assert migration_level(cur) >= 166


def test_migration_datasource_to_db(pub):
    NamedDataSource.wipe()

    objects_dir = os.path.join(pub.app_dir, 'datasources')
    shutil.rmtree(objects_dir, ignore_errors=True)
    os.mkdir(objects_dir)

    with open(os.path.join(objects_dir, '3'), 'w') as fd:
        fd.write(
            '''<datasource id="3">
  <name>json test</name>
  <slug>json_test</slug>
  <text_attribute>libelle</text_attribute>
  <data_source>
    <type>json</type>
    <value>http://localhost/test.json</value>
  </data_source>
</datasource>'''
        )

    NamedDataSource.migrate_from_files()
    shutil.rmtree(objects_dir, ignore_errors=True)
    assert NamedDataSource.count() == 1
    assert NamedDataSource.get(3).name == 'json test'
    assert NamedDataSource.get(3).data_source == {'type': 'json', 'value': 'http://localhost/test.json'}

    # check sequence is correct
    ds2 = NamedDataSource()
    ds2.name = 'test2'
    ds2.store()
    assert ds2.id == 4


def test_migration_wscalls_to_db(pub):
    NamedWsCall.wipe()

    objects_dir = os.path.join(pub.app_dir, 'wscalls')
    shutil.rmtree(objects_dir, ignore_errors=True)
    os.mkdir(objects_dir)

    with open(os.path.join(objects_dir, 'slug'), 'w') as fd:
        fd.write(
            '''<wscalls id="blah">
  <name>Blah</name>
  <slug>blah</slug>
  <request>
    <url>http://localhost/api/test/</url>
    <request_signature_key />
    <method>GET</method>
    <qs_data>
      <param key="ending_date_search">31/12/2024</param>
    </qs_data>
    <post_data />
  </request>
</wscalls>'''
        )

    NamedWsCall.migrate_from_files()
    shutil.rmtree(objects_dir, ignore_errors=True)
    assert NamedWsCall.count() == 1
    assert NamedWsCall.get(1).name == 'Blah'
    assert NamedWsCall.get(1).slug == 'blah'
    assert NamedWsCall.get(1).request == {
        'url': 'http://localhost/api/test/',
        'method': 'GET',
        'qs_data': {'ending_date_search': '31/12/2024'},
        'timeout': '',
        'post_data': {},
        'post_formdata': False,
        'cache_duration': '',
        'request_signature_key': '',
    }

    # check sequence is correct
    ws2 = NamedWsCall()
    ws2.name = 'test2'
    ws2.store()
    assert ws2.id == 2


def test_migration_custom_view_id(pub):
    pub.custom_view_class.wipe()
    conn, cur = sql.get_connection_and_cursor()
    cur.execute('ALTER TABLE custom_views DROP COLUMN id')
    cur.execute('ALTER TABLE custom_views ADD COLUMN id VARCHAR')
    cur.execute("INSERT INTO custom_views (id, slug, title) VALUES ('3', 'foo', 'Foo')")
    cur.execute("INSERT INTO custom_views (id, slug, title) VALUES ('5', 'bar', 'Bar')")
    cur.execute('UPDATE wcs_meta SET value = 140 WHERE key = %s', ('sql_level',))
    cur.close()
    conn.commit()

    sql.migrate()

    conn, cur = sql.get_connection_and_cursor()
    cur.execute("INSERT INTO custom_views (id, slug, title) VALUES (DEFAULT, 'baz', 'Baz') RETURNING id")
    new_id = cur.fetchone()[0]
    assert new_id == 6
    cur.close()
    conn.commit()


def test_prefetch(pub):
    FormDef.wipe()
    sql.SqlUser.wipe()
    sql.Role.wipe()

    test_formdef = FormDef()
    test_formdef.name = 'test'
    test_formdef.store()
    data_class = test_formdef.data_class(mode='sql')

    users = []
    for i in range(10):
        user = sql.SqlUser()
        user.name = f'user-{i}'
        user.store()
        users.append(user)

    roles = []
    for i in range(20):
        role = sql.Role()
        role.name = f'role-{i}'
        role.store()
        roles.append(role)

    for i in range(10):
        formdata = data_class()
        formdata.user_id = users[i].id
        # test different storage mode, list of int, list of str, str and int
        if i % 2:
            formdata.workflow_roles = {'_foo': roles[2 * i].id, '_bar': [str(roles[2 * i + 1].id)]}
        else:
            formdata.workflow_roles = {'_foo': str(roles[2 * i].id), '_bar': str(roles[2 * i + 1].id)}
        formdata.just_created()
        formdata.store()

    formdatas = data_class.select(iterator=True, itersize=200)
    for formdata in formdatas:
        assert formdata._evolution is None

    formdatas = data_class.select(iterator=True, itersize=200)
    formdatas = data_class.prefetch_evolutions(formdatas)
    for formdata in formdatas:
        assert formdata._evolution is not None

    formdatas = data_class.select(iterator=True, itersize=200)
    formdatas, prefetched_users = data_class.prefetch_users(formdatas)
    for formdata in formdatas:
        pass
    assert set(prefetched_users) == {str(user.id) for user in users}

    formdatas = data_class.select(iterator=True, itersize=200)
    formdatas, prefetched_roles = data_class.prefetch_roles(formdatas)
    for formdata in formdatas:
        pass
    assert set(prefetched_roles) == {str(user.id) for user in roles}


def test_testdef_remove_orphans(pub):
    FormDef.wipe()
    CardDef.wipe()

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.store()

    testdef = TestDef()
    testdef.name = 'First test'
    testdef.object_type = formdef.get_table_name()
    testdef.object_id = str(formdef.id)
    testdef.data = {}
    testdef.store()

    testdef = TestDef()
    testdef.name = 'Orphan test'
    testdef.object_type = formdef.get_table_name()
    testdef.object_id = str(int(formdef.id) + 1)
    testdef.data = {}
    testdef.store()

    assert FormDef.count() == 1
    assert TestDef.count() == 2
    assert WorkflowTests.count() == 2

    conn, cur = sql.get_connection_and_cursor()
    cur.execute('UPDATE wcs_meta SET value = 126 WHERE key = %s', ('sql_level',))

    sql.migrate()
    assert sql.is_reindex_needed('testdef', conn=conn, cur=cur) is True
    conn.commit()
    cur.close()
    sql.reindex()

    assert FormDef.count() == 1
    assert TestDef.count() == 1
    assert WorkflowTests.count() == 1


def test_testdef_migrate_formdata_to_test_tables(pub):
    FormDef.wipe()

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.fields = [
        fields.StringField(id='1', label='string'),
    ]
    formdef.store()

    def get_testdef():
        testdef = TestDef()
        testdef.name = 'First test'
        testdef.object_type = formdef.get_table_name()
        testdef.object_id = str(formdef.id)
        testdef.data = {}
        testdef.store()
        return testdef

    test_results = TestResults()
    test_results.object_type = formdef.get_table_name()
    test_results.object_id = formdef.id
    test_results.timestamp = tz_now()
    test_results.reason = ''
    test_results.store()

    # first legacy result
    testdef = get_testdef()
    legacy_result = TestResult(testdef)
    legacy_result.test_results_id = test_results.id
    legacy_result.store()

    legacy_test_formdata = formdef.data_class()()
    legacy_test_formdata.just_created()
    legacy_test_formdata.jump_status('wf-new')
    legacy_test_formdata.test_result_id = legacy_result.id
    legacy_test_formdata.data = {'1': '1'}
    legacy_test_formdata.store()
    legacy_test_formdata.record_workflow_event('frontoffice-created')

    assert len(legacy_test_formdata.evolution) == 2
    assert len(legacy_test_formdata.get_workflow_traces()) == 1

    legacy_result.formdata_id = legacy_test_formdata.id
    legacy_result.store()

    # real formdata
    real_formdata = formdef.data_class()()
    real_formdata.just_created()
    real_formdata.store()

    # second legacy result
    legacy_result2 = TestResult(testdef)
    legacy_result2.test_results_id = test_results.id
    legacy_result2.store()

    legacy_test_formdata = formdef.data_class()()
    legacy_test_formdata.just_created()
    legacy_test_formdata.test_result_id = legacy_result2.id
    legacy_test_formdata.data = {'1': '2'}
    legacy_test_formdata.store()

    assert len(legacy_test_formdata.evolution) == 1
    assert len(legacy_test_formdata.get_workflow_traces()) == 0

    legacy_result2.formdata_id = legacy_test_formdata.id
    legacy_result2.store()

    # with additionnal formdata (created by workflow action)
    legacy_test_formdata = formdef.data_class()()
    legacy_test_formdata.just_created()
    legacy_test_formdata.test_result_id = legacy_result2.id
    legacy_test_formdata.data = {'1': '3'}
    legacy_test_formdata.store()

    # new result
    testdef2 = get_testdef()
    new_result = TestResult(testdef2)
    new_result.test_results_id = test_results.id
    new_result.store()

    with testdef.use_test_objects():
        new_test_formdata = formdef.data_class()()
    new_test_formdata.just_created()
    new_test_formdata.test_result_id = new_result.id
    new_test_formdata.data = {'1': '3'}
    new_test_formdata.store()

    TestDef.remove_object(testdef2.id)

    assert len(new_test_formdata.evolution) == 1
    assert len(new_test_formdata.get_workflow_traces()) == 0

    new_result.formdata_id = new_test_formdata.id
    new_result.store()

    # result without formdata
    no_formdata_result = TestResult(get_testdef())
    no_formdata_result.test_results_id = test_results.id
    no_formdata_result.store()

    # result with deleted testdef
    testdef3 = get_testdef()
    deleted_testdef_result = TestResult(testdef3)
    deleted_testdef_result.test_results_id = test_results.id
    deleted_testdef_result.store()
    TestDef.remove_object(testdef3.id)

    assert formdef.data_class().count() == 4  # 3 test formdata + 1 real formdata

    conn, cur = sql.get_connection_and_cursor()
    cur.execute('UPDATE wcs_meta SET value = 139 WHERE key = %s', ('sql_level',))

    sql.migrate()
    assert sql.is_reindex_needed('test_result', conn=conn, cur=cur) is True
    conn.commit()
    cur.close()
    sql.reindex()

    assert formdef.data_class().count() == 1  # 1 real formdata
    assert WorkflowTrace.count() == 0
    assert TestWorkflowTrace.count() == 1

    legacy_result = TestResult.get(legacy_result.id)
    with testdef.use_test_objects(results=[legacy_result]):
        formdata = formdef.data_class().get(legacy_result.formdata_id)
        assert formdata.data['1'] == '1'
        assert len(formdata.evolution) == 2
        assert len(formdata.get_workflow_traces()) == 1

    legacy_result2 = TestResult.get(legacy_result2.id)
    with testdef.use_test_objects(results=[legacy_result2]):
        formdata = formdef.data_class().get(legacy_result2.formdata_id)
        assert formdata.data['1'] == '2'
        assert len(formdata.evolution) == 1
        assert len(formdata.get_workflow_traces()) == 0

        formdata = formdef.data_class().select([st.NotEqual('id', legacy_result2.formdata_id)])
        assert len(formdata) == 1
        formdata = formdata[0]

        assert formdata.data['1'] == '3'
        assert len(formdata.evolution) == 1
        assert len(formdata.get_workflow_traces()) == 0

    new_result = TestResult.get(new_result.id)
    with testdef2.use_test_objects(results=[new_result]):
        formdata = formdef.data_class().get(new_result.formdata_id)
        assert formdata.data['1'] == '3'
        assert len(formdata.evolution) == 1
        assert len(formdata.get_workflow_traces()) == 0

    # run migration again
    conn, cur = sql.get_connection_and_cursor()
    cur.execute('UPDATE wcs_meta SET value = 139 WHERE key = %s', ('sql_level',))

    sql.migrate()
    sql.reindex()

    assert formdef.data_class().count() == 1  # 1 real formdata


def test_test_results_remove_orphans(pub):
    FormDef.wipe()
    CardDef.wipe()

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.store()

    test_results = TestResults()
    test_results.object_type = formdef.get_table_name()
    test_results.object_id = formdef.id
    test_results.timestamp = tz_now()
    test_results.reason = 'Normal results'
    test_results.store()

    test_results = TestResults()
    test_results.object_type = formdef.get_table_name()
    test_results.object_id = str(int(formdef.id) + 1)
    test_results.timestamp = tz_now()
    test_results.reason = 'Orphan result'
    test_results.store()

    assert FormDef.count() == 1
    assert TestResults.count() == 2

    conn, cur = sql.get_connection_and_cursor()
    cur.execute('UPDATE wcs_meta SET value = 144 WHERE key = %s', ('sql_level',))

    sql.migrate()
    assert sql.is_reindex_needed('test_results', conn=conn, cur=cur) is True
    conn.commit()
    cur.close()
    sql.reindex()

    assert FormDef.count() == 1
    assert TestResults.count() == 1


def test_delete_broken_snapshots(pub):
    pub.snapshot_class.wipe()

    snapshot = pub.snapshot_class()
    snapshot.object_type = 'wscall'
    snapshot.object_id = '1'
    snapshot.store()

    snapshot2 = pub.snapshot_class()
    snapshot2.object_type = 'wscall'
    snapshot2.object_id = '1'
    snapshot2.serialization = '<xxx>'
    snapshot2.store()

    pub.snapshot_class.delete_broken_snapshots()

    assert not pub.snapshot_class.has_key(snapshot.id)
    assert pub.snapshot_class.has_key(snapshot2.id)


def test_fix_duplicated_testdef_uuid(pub):
    TestDef.wipe()

    formdef = FormDef()
    formdef.name = 'tests'
    formdef.store()

    testdef = TestDef.create_from_formdata(formdef, formdef.data_class()())
    testdef.name = '1'
    testdef.uuid = 'abc'
    testdef.store()

    testdef2 = TestDef.create_from_formdata(formdef, formdef.data_class()())
    testdef2.name = '2'
    testdef2.uuid = 'abc'
    testdef2.store()

    testdef3 = TestDef.create_from_formdata(formdef, formdef.data_class()())
    testdef3.name = '3'
    testdef3.uuid = 'def'
    testdef3.store()

    TestDef.migrate_legacy()

    assert TestDef.get(testdef.id).uuid == 'abc'
    assert TestDef.get(testdef2.id).uuid != 'abc'
    assert TestDef.get(testdef3.id).uuid == 'def'


def test_workflow_trace_archive(pub):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'tests'
    formdef.store()

    formdef.data_class().wipe()
    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()
    formdata.record_workflow_event('api-trigger', action_item_id='1')
    formdata.record_workflow_event('continuation')

    formdata2 = formdef.data_class()()
    formdata2.just_created()
    formdata2.store()
    formdata2.record_workflow_event('json-import-updated')

    conn, cur = sql.get_connection_and_cursor()
    cur.execute('''UPDATE workflow_traces SET timestamp = (now() - interval '10 days')''')
    cur.execute('UPDATE wcs_meta SET value = 149 WHERE key = %s', ('sql_level',))

    sql.migrate()
    assert sql.is_reindex_needed('workflow_traces_to_archive', conn=conn, cur=cur) is True
    conn.commit()
    cur.close()

    conn, cur = sql.get_connection_and_cursor()
    sql.reindex()
    assert sql.is_reindex_needed('workflow_traces_to_archive', conn=conn, cur=cur) is False

    cur.execute('SELECT count(*) from workflow_traces_archive')
    assert cur.fetchone() == (2,)
    cur.execute('SELECT count(*) from workflow_traces')
    assert cur.fetchone() == (0,)

    formdata2.record_workflow_event('continuation')
    traces = WorkflowTrace.select_for_formdata(formdata2)
    assert [x.event for x in traces] == ['json-import-updated', 'continuation']

    formdata2.remove_self()
    cur.execute('SELECT count(*) from workflow_traces_archive')
    assert cur.fetchone() == (1,)
    cur.execute('SELECT count(*) from workflow_traces')
    assert cur.fetchone() == (0,)

    formdata.record_workflow_event('continuation')
    pub.archive_workflow_traces()


def test_test_users_set_nameid(pub):
    pub.test_user_class.wipe()

    real_user_1 = pub.user_class()
    real_user_1.name_identifiers = ['abc']
    real_user_1.store()

    real_user_2 = pub.user_class()
    real_user_2.name_identifiers = []
    real_user_2.store()

    test_user_1 = pub.test_user_class()
    test_user_1.test_uuid = '1'
    test_user_1.name_identifiers = ['def']
    test_user_1.store()

    test_user_2 = pub.test_user_class()
    test_user_2.test_uuid = '2'
    test_user_2.name_identifiers = []
    test_user_2.store()

    test_user_3 = pub.test_user_class()
    test_user_3.test_uuid = '3'
    test_user_3.name_identifiers = None
    test_user_3.store()

    conn, cur = sql.get_connection_and_cursor()
    cur.execute('UPDATE wcs_meta SET value = 163 WHERE key = %s', ('sql_level',))

    sql.migrate()
    assert sql.is_reindex_needed('test_user', conn=conn, cur=cur) is True
    conn.commit()
    cur.close()
    sql.reindex()

    assert pub.user_class.get(real_user_1.id).name_identifiers[0] == 'abc'
    assert pub.user_class.get(real_user_2.id).name_identifiers == []

    assert pub.test_user_class.get(test_user_1.id).name_identifiers[0] == 'def'
    assert len(pub.test_user_class.get(test_user_2.id).name_identifiers[0]) == 32
    assert len(pub.test_user_class.get(test_user_3.id).name_identifiers[0]) == 32


def test_sql_objects_repr(pub):
    formdef = FormDef()
    formdef.name = 'tests'
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()

    formdata.record_workflow_event('api-trigger', action_item_id='1')

    trace = formdata.get_workflow_traces()[0]

    assert repr(trace) == '<WorkflowTrace id:%s>' % trace.id


def test_migrate_categories_from_files(pub):
    Category.wipe()
    CardDefCategory.wipe()

    conn, cur = sql.get_connection_and_cursor()
    cur.execute('UPDATE wcs_meta SET value = 164 WHERE key = %s', ('sql_level',))
    conn.commit()
    cur.close()

    shutil.rmtree(os.path.join(pub.app_dir, 'categories'), ignore_errors=True)
    if not os.path.exists(os.path.join(pub.app_dir, 'categories')):
        os.mkdir(os.path.join(pub.app_dir, 'categories'))
    shutil.rmtree(os.path.join(pub.app_dir, 'carddef_categories'), ignore_errors=True)
    if not os.path.exists(os.path.join(pub.app_dir, 'carddef_categories')):
        os.mkdir(os.path.join(pub.app_dir, 'carddef_categories'))

    with open(os.path.join(pub.app_dir, 'categories', '11'), 'w') as fd:
        fd.write(
            '''<category id="11">
                      <name>form cat1</name>
                      <url_name>form-cat1</url_name>
                      <position>1</position>
                 </category>'''
        )
    with open(os.path.join(pub.app_dir, 'categories', '12'), 'w') as fd:
        fd.write(
            '''<category id="12">
                      <name>form cat2</name>
                      <url_name>cat2</url_name>
                      <position>2</position>
                 </category>'''
        )
    with open(os.path.join(pub.app_dir, 'carddef_categories', '11'), 'w') as fd:
        fd.write(
            '''<carddef_category id="11">
                      <name>card cat1</name>
                      <url_name>card-cat1</url_name>
                      <position>1</position>
                 </carddef_category>'''
        )
    with open(os.path.join(pub.app_dir, 'carddef_categories', '12'), 'w') as fd:
        fd.write(
            '''<carddef_category id="12">
                      <name>cat2</name>
                      <url_name>cat2</url_name>
                      <position>2</position>
                 </carddef_category>'''
        )

    formdef = FormDef()
    formdef.name = 'tests'
    formdef.category_id = '11'
    formdef.fields = []
    formdef.store()

    formdata = formdef.data_class()()
    formdata.store()

    conn, cur = sql.get_connection_and_cursor()
    cur.execute(
        'SELECT category_id FROM wcs_all_forms WHERE formdef_id = %s AND id = %s', (formdef.id, formdata.id)
    )
    assert cur.fetchone()[0] == 11

    carddef = CardDef()
    carddef.name = 'tests'
    carddef.category_id = '11'
    carddef.fields = []
    carddef.store()

    sql.migrate()
    assert [x.slug for x in Category.select(order_by='id')] == ['form-cat1', 'cat2']
    assert [x.slug for x in CardDefCategory.select(order_by='id')] == ['card-cat1', 'cat2']

    formdef.refresh_from_storage()
    assert formdef.category.slug == 'form-cat1'

    carddef.refresh_from_storage()
    assert carddef.category.slug == 'card-cat1'

    conn, cur = sql.get_connection_and_cursor()
    cur.execute(
        'SELECT category_id FROM wcs_all_forms WHERE formdef_id = %s AND id = %s', (formdef.id, formdata.id)
    )
    assert str(cur.fetchone()[0]) == Category.get_by_slug('form-cat1').id


def test_migrate_categories_from_files_with_missing_category(pub):
    Category.wipe()

    conn, cur = sql.get_connection_and_cursor()
    cur.execute('UPDATE wcs_meta SET value = 164 WHERE key = %s', ('sql_level',))
    conn.commit()
    cur.close()

    shutil.rmtree(os.path.join(pub.app_dir, 'categories'), ignore_errors=True)
    if not os.path.exists(os.path.join(pub.app_dir, 'categories')):
        os.mkdir(os.path.join(pub.app_dir, 'categories'))
    with open(os.path.join(pub.app_dir, 'categories', '1'), 'w') as fd:
        fd.write(
            '''<category id="1">
                      <name>form cat1</name>
                      <url_name>form-cat1</url_name>
                      <position>1</position>
                 </category>'''
        )
    with open(os.path.join(pub.app_dir, 'categories', '2'), 'w') as fd:
        fd.write(
            '''<category id="2">
                      <name>form cat2</name>
                      <url_name>cat2</url_name>
                      <position>2</position>
                 </category>'''
        )

    formdef = FormDef()
    formdef.name = 'tests'
    formdef.category_id = '6'
    formdef.fields = []
    formdef.store()

    sql.migrate()

    formdef.refresh_from_storage()
    assert formdef.category_id == '-6'


def test_migration_data_source_external_type(pub):
    NamedDataSource.wipe()

    conn, cur = sql.get_connection_and_cursor()
    cur.execute('ALTER TABLE datasources DROP COLUMN external_type')
    cur.close()
    conn.commit()

    sql.migrate()

    conn, cur = sql.get_connection_and_cursor()
    cur.execute('UPDATE wcs_meta SET value = 165 WHERE key = %s', ('sql_level',))

    sql.migrate()
    conn.commit()
    cur.close()

    data_source = NamedDataSource(name='agenda')
    data_source.external_type = 'free_range'
    data_source.store()

    data_source = NamedDataSource.get(data_source.id)
    assert data_source.external_type == 'free_range'


def test_migrate_categories_from_files_with_application_elements(pub):
    Category.wipe(restart_sequence=True)
    ApplicationElement.wipe()
    FormDef.wipe()
    CardDef.wipe()

    conn, cur = sql.get_connection_and_cursor()
    cur.execute('UPDATE wcs_meta SET value = 164 WHERE key = %s', ('sql_level',))
    conn.commit()
    cur.close()

    shutil.rmtree(os.path.join(pub.app_dir, 'categories'), ignore_errors=True)
    if not os.path.exists(os.path.join(pub.app_dir, 'categories')):
        os.mkdir(os.path.join(pub.app_dir, 'categories'))
    shutil.rmtree(os.path.join(pub.app_dir, 'carddef_categories'), ignore_errors=True)
    if not os.path.exists(os.path.join(pub.app_dir, 'carddef_categories')):
        os.mkdir(os.path.join(pub.app_dir, 'carddef_categories'))

    for i in range(2):
        with open(os.path.join(pub.app_dir, 'categories', str(i + 1)), 'w') as fd:
            fd.write(
                f'''<category id="{i+1}">
                          <name>form cat{i+1}</name>
                          <url_name>form-cat{i+1}</url_name>
                          <position>{i+1}</position>
                     </category>'''
            )
            element = ApplicationElement()
            element.application_id = 123
            element.object_type = 'category'
            element.object_id = str(i + 1)
            element.store()

    for i in range(3):
        with open(os.path.join(pub.app_dir, 'carddef_categories', str(i + 1)), 'w') as fd:
            fd.write(
                f'''<carddef_category id="{i+1}">
                          <name>card cat{i+1}</name>
                          <url_name>card-cat{i+1}</url_name>
                          <position>1</position>
                     </carddef_category>'''
            )
            element = ApplicationElement()
            element.application_id = 123
            element.object_type = 'carddef_category'
            element.object_id = str(i + 1)
            element.store()

    formdef = FormDef()
    formdef.name = 'tests'
    formdef.category_id = '1'
    formdef.fields = []
    formdef.store()

    carddef = CardDef()
    carddef.name = 'tests'
    carddef.category_id = '1'
    carddef.fields = []
    carddef.store()

    sql.migrate()

    formdef.refresh_from_storage()
    assert formdef.category.slug == 'form-cat1'

    carddef.refresh_from_storage()
    assert carddef.category.slug == 'card-cat1'

    ApplicationElement.select(order_by='id')

    assert [(x.object_type, x.object_id) for x in ApplicationElement.select(order_by='id')] == [
        ('category', str(Category.get_by_slug('form-cat1').id)),
        ('category', str(Category.get_by_slug('form-cat2').id)),
        ('carddef_category', str(CardDefCategory.get_by_slug('card-cat1').id)),
        ('carddef_category', str(CardDefCategory.get_by_slug('card-cat2').id)),
        ('carddef_category', str(CardDefCategory.get_by_slug('card-cat3').id)),
    ]
