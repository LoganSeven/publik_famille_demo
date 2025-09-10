import datetime
import decimal
import json
import math
import os
import re
import time
from unittest import mock

import pytest
from django.core.cache import cache
from django.utils import translation
from quixote import cleanup

import wcs.api  # workaround against circular dependencies :/
import wcs.qommon.storage
from wcs.admin.settings import FileTypesDirectory
from wcs.backoffice.pagination import pagination_links
from wcs.fields import StringField
from wcs.formdef import FormDef
from wcs.qommon import evalutils, force_str
from wcs.qommon.form import FileSizeWidget
from wcs.qommon.http_request import HTTPRequest
from wcs.qommon.humantime import humanduration2seconds, seconds2humanduration, timewords
from wcs.qommon.misc import (
    _http_request,
    date_format,
    ellipsize,
    format_time,
    get_as_datetime,
    mark_spaces,
    normalize_geolocation,
    parse_decimal,
    parse_isotime,
    simplify,
    validate_phone_fr,
)
from wcs.scripts import Script
from wcs.workflows import Workflow

from .utilities import clean_temporary_pub, create_temporary_pub, get_app


def setup_module(module):
    cleanup()


def teardown_module(module):
    clean_temporary_pub()


@pytest.fixture
def pub():
    pub = create_temporary_pub(lazy_mode=True)
    return pub


def test_parse_file_size():
    assert FileSizeWidget.parse_file_size('17') == 17
    assert FileSizeWidget.parse_file_size('17o') == 17
    assert FileSizeWidget.parse_file_size('17 K') == 17 * 10**3
    assert FileSizeWidget.parse_file_size('17 M') == 17 * 10**6
    assert FileSizeWidget.parse_file_size('17 Mo') == 17 * 10**6
    assert FileSizeWidget.parse_file_size('17 MB') == 17 * 10**6
    assert FileSizeWidget.parse_file_size('17 Kio') == 17 * 2**10
    assert FileSizeWidget.parse_file_size('17 Mio') == 17 * 2**20
    assert FileSizeWidget.parse_file_size('17K') == 17 * 10**3
    assert FileSizeWidget.parse_file_size('17   K') == 17 * 10**3
    assert FileSizeWidget.parse_file_size(' 17   K ') == 17 * 10**3


def test_parse_invalid_file_size():
    for test_value in ('17i', 'hello', '0.4K', '2G'):
        with pytest.raises(ValueError):
            FileSizeWidget.parse_file_size(test_value)


@pytest.mark.parametrize(
    'seconds, expected',
    [
        (1, '1 second'),
        (3, '3 seconds'),
        (100000, '1 day, 3 hours, 46 minutes and 40 seconds'),
        (13, '13 seconds'),
        (60, '1 minute'),
        (3600, '1 hour'),
        (10_000_000, '3 months, 22 days, 17 hours, 46 minutes and 40 seconds'),
        (100_000_000, '3 years, 1 month, 30 days, 15 hours, 46 minutes and 40 seconds'),
    ],
)
def test_humantime(seconds, expected):
    pub = create_temporary_pub()
    pub.ngettext = translation.ngettext
    assert seconds2humanduration(seconds) == expected
    assert humanduration2seconds(seconds2humanduration(seconds)) == seconds


@pytest.mark.parametrize(
    'seconds, expected',
    [
        (120, '2min'),
        (3600, '1h'),
        (3720, '1h02'),
        (100_000, '1 day and 3h46'),
    ],
)
def test_humantime_short(seconds, expected):
    pub = create_temporary_pub()
    pub.ngettext = translation.ngettext
    assert seconds2humanduration(seconds, short=True) == expected


def test_humantime_timewords():
    assert timewords() == ['day(s)', 'hour(s)', 'minute(s)', 'second(s)', 'month(s)', 'year(s)']


def test_parse_mimetypes():
    assert FileTypesDirectory.parse_mimetypes('application/pdf') == ['application/pdf']
    assert FileTypesDirectory.parse_mimetypes('.pdf') == ['application/pdf']
    assert set(FileTypesDirectory.parse_mimetypes('.pdf, .odt')) == {
        'application/pdf',
        'application/vnd.oasis.opendocument.text',
    }


def test_format_mimetypes():
    assert FileTypesDirectory.format_mimetypes(['application/pdf']) == 'application/pdf (.pdf)'
    assert (
        FileTypesDirectory.format_mimetypes(['application/pdf', 'text/rtf'])
        == 'application/pdf (.pdf), text/rtf'
    )
    assert FileTypesDirectory.format_mimetypes(['application/pdf', 'application/msword']) in (
        'application/pdf (.pdf), application/msword (.doc)',
        'application/pdf (.pdf), application/msword (.dot)',
        'application/pdf (.pdf), application/msword (.wiz)',
    )
    assert (
        FileTypesDirectory.format_mimetypes(
            [
                'application/pdf',
                'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                'application/msword',
            ]
        )
        == 'application/pdf (.pdf), '
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document (.docx)'
        '...'
    )


def test_simplify_unchanged():
    assert simplify('test') == 'test'
    assert simplify('another-test') == 'another-test'
    assert simplify('another_test', '_') == 'another_test'


def test_simplify_space():
    assert simplify('test again') == 'test-again'
    assert simplify('  test  again  ') == 'test-again'
    assert simplify('test again', '_') == 'test_again'
    assert simplify('  test  again  ', '_') == 'test_again'


def test_simplify_apostrophes():
    assert simplify('test\'again') == 'test-again'
    assert simplify('test\'\'\'again') == 'test-again'


def test_simplify_dashes_and_underscores():
    assert simplify('8100-03_PT') == '8100-03-pt'
    assert simplify('8100-03_PT', ' ') == '8100 03 pt'
    assert simplify('8100-03_PT', '_') == '8100_03_pt'


def test_simplify_accented():
    assert simplify('cliché') == 'cliche'


def test_simplify_remove():
    assert simplify('this is: (a) "test"') == 'this-is-a-test'
    assert simplify('a test; again?') == 'a-test-again'


def test_simplify_mix():
    assert simplify('  this is: (a) "cliché" ') == 'this-is-a-cliche'
    assert simplify('  À "cliché"; again? ') == 'a-cliche-again'


def test_simplify_prefix_suffix():
    assert simplify('-hello world ') == 'hello-world'


def test_json_str_decoder():
    json_str = json.dumps({'lst': [{'a': 'b'}, 1, 2], 'bla': 'éléphant'})

    assert isinstance(list(json.loads(json_str).keys())[0], str)
    assert isinstance(json.loads(json_str)['lst'][0]['a'], str)
    assert isinstance(json.loads(json_str)['bla'], str)
    assert json.loads(json_str)['bla'] == force_str('éléphant')


def test_format_time():
    assert format_time(None, '%(month_name)s') == '?'
    assert format_time(1500000000, '%(month_name)s') == 'July'
    assert format_time(1500000000, '%(month_name)s', gmtime=True) == 'July'
    assert format_time(1500000000, '%(hour)s') == '4'
    assert format_time(1500000000, '%(hour)s', gmtime=True) == '2'
    assert format_time((2016, 8), '%(month)s') == '8'
    assert format_time((2016, 8, 2), '%(month)s') == '8'
    assert (
        format_time(
            time.localtime(
                1500000000,
            ),
            '%(month)s',
        )
        == '7'
    )
    assert (
        format_time(
            time.localtime(
                1500000000,
            ),
            '%(weekday_name)s',
        )
        == 'Friday'
    )


def test_parse_isotime():
    assert parse_isotime('2015-01-01T10:10:19Z') == 1420107019
    assert parse_isotime('2015-01-01T10:10:19+00:00Z') == 1420107019
    with pytest.raises(ValueError):
        parse_isotime('2015-01-01T10:10:19')
    with pytest.raises(ValueError):
        parse_isotime('2015-01-0110:10:19Z')


def test_script_substitution_variable():
    pub = create_temporary_pub()
    pub.substitutions.feed(pub)

    formdef = FormDef()
    formdef.name = 'test'
    formdef.store()
    pub.substitutions.feed(formdef)

    variables = pub.substitutions.get_context_variables()
    with pytest.raises(AttributeError):
        assert variables['script'].hello_world()

    os.mkdir(os.path.join(pub.app_dir, 'scripts'))
    with open(os.path.join(pub.app_dir, 'scripts', 'hello_world.py'), 'w') as fd:
        fd.write('"""docstring"""\nresult = "hello world"')
    assert variables['script'].hello_world() == 'hello world'

    assert Script('hello_world').__doc__ == 'docstring'

    os.mkdir(os.path.join(pub.APP_DIR, 'scripts'))
    with open(os.path.join(pub.APP_DIR, 'scripts', 'hello_world.py'), 'w') as fd:
        fd.write('result = "hello global world"')
    assert variables['script'].hello_world() == 'hello world'

    os.unlink(os.path.join(pub.app_dir, 'scripts', 'hello_world.py'))
    assert variables['script'].hello_world() == 'hello global world'

    with open(os.path.join(pub.app_dir, 'scripts', 'hello_world.py'), 'w') as fd:
        fd.write('result = site_url')
    assert variables['script'].hello_world() == 'http://example.net'

    with open(os.path.join(pub.app_dir, 'scripts', 'hello_world.py'), 'w') as fd:
        fd.write('assert form_objects is not None\nresult = "ok"')
    assert variables['script'].hello_world() == 'ok'


def test_default_charset():
    pub = create_temporary_pub()
    resp = get_app(pub).get('/')
    assert 'utf-8' in resp.headers['Content-Type']


def test_age_in_years():
    create_temporary_pub()
    assert evalutils.age_in_years('2000-01-01', '2016-05-26') == 16
    assert evalutils.age_in_years(datetime.date(2000, 1, 1), '2016-05-26') == 16
    assert evalutils.age_in_years(time.struct_time((2000, 1, 1, 0, 0, 0, 0, 0, 0)), '2016-05-26') == 16
    assert evalutils.age_in_years('2000-06-01', '2016-05-26') == 15
    assert evalutils.age_in_years('2000-02-29', '2016-02-29') == 16
    assert evalutils.age_in_years('2000-02-28', '2016-02-29') == 16
    assert evalutils.age_in_years('2000-03-01', '2016-02-29') == 15
    assert evalutils.age_in_years('2000-01-01') >= 16


def test_age_in_years_and_months():
    create_temporary_pub()
    assert evalutils.age_in_years_and_months('2000-01-01', '2016-05-26') == (16, 4)
    assert evalutils.age_in_years_and_months('2000-01-01', datetime.date(2016, 5, 26)) == (16, 4)
    assert evalutils.age_in_years_and_months(datetime.date(2000, 1, 1), '2016-05-26') == (16, 4)
    assert evalutils.age_in_years_and_months(
        time.struct_time((2000, 1, 1, 0, 0, 0, 0, 0, 0)), '2016-05-26'
    ) == (16, 4)
    assert evalutils.age_in_years_and_months('2000-06-01', '2016-05-26') == (15, 11)
    assert evalutils.age_in_years_and_months('2000-02-29', '2016-02-29') == (16, 0)
    assert evalutils.age_in_years_and_months('2000-02-28', '2016-02-29') == (16, 0)
    assert evalutils.age_in_years_and_months('2000-03-01', '2016-02-29') == (15, 11)
    assert evalutils.age_in_years_and_months('2000-01-01') >= (16, 0)


def test_age_in_days():
    assert evalutils.age_in_days('2000-01-01', '2001-01-01') == 366
    assert evalutils.age_in_days(datetime.date(2000, 1, 1), '2001-01-01') == 366
    assert evalutils.age_in_days(time.struct_time((2000, 1, 1, 0, 0, 0, 0, 0, 0)), '2001-01-01') == 366
    assert evalutils.age_in_days('2001-01-01', '2002-01-01') == 365


def test_age_in_seconds():
    assert evalutils.age_in_seconds('2000-01-01 00:00', '2000-01-01 01:00') == 3600
    assert evalutils.age_in_seconds('2000-01-01', '2000-01-01 01:00') == 3600
    assert evalutils.age_in_seconds(datetime.date(2000, 1, 1), '2000-01-01 01:00') == 3600
    assert (
        evalutils.age_in_seconds(time.struct_time((2000, 1, 1, 0, 0, 0, 0, 0, 0)), '2000-01-01 01:00') == 3600
    )


def test_date_format():
    pub = create_temporary_pub()
    pub.cfg['language'] = {}
    pub.write_cfg()
    assert date_format() == '%Y-%m-%d'
    with pub.with_language('fr'):
        assert date_format() == '%d/%m/%Y'


def test_get_as_datetime():
    create_temporary_pub()
    datetime_value = datetime.datetime(2017, 4, 25, 12, 0)
    assert get_as_datetime('2017-04-25 12:00') == datetime_value
    assert get_as_datetime('2017-04-25 12:00:00') == datetime_value
    assert get_as_datetime('2017-04-25T12:00:00Z') == datetime_value
    assert get_as_datetime('2017-04-25T12:00:00') == datetime_value
    assert get_as_datetime('25/04/2017 12:00') == datetime_value


def test_pagination():
    pub = create_temporary_pub()
    req = HTTPRequest(None, {'SERVER_NAME': 'example.net', 'SCRIPT_NAME': ''})
    req.response.filter = {}
    pub.form = {'ajax': 'true'}
    pub._set_request(req)

    def get_texts(s):
        return [x for x in re.findall(r'>(.*?)<', str(s)) if x.strip()]

    assert get_texts(pagination_links(0, 10, 0)) == ['1', '(0-0/0)', 'Per page: ', '10']
    assert get_texts(pagination_links(0, 10, 10)) == ['1', '(1-10/10)', 'Per page: ', '10']
    assert get_texts(pagination_links(0, 10, 20)) == ['1', '2', '(1-10/20)', 'Per page: ', '10', '20']
    assert get_texts(pagination_links(10, 10, 20)) == ['1', '2', '(11-20/20)', 'Per page: ', '10', '20']
    assert get_texts(pagination_links(10, 10, 50)) == [
        '1',
        '2',
        '3',
        '4',
        '5',
        '(11-20/50)',
        'Per page: ',
        '10',
        '20',
        '50',
    ]
    assert get_texts(pagination_links(10, 10, 500)) == [
        '1',
        '2',
        '3',
        '4',
        '5',
        '6',
        '7',
        '&#8230;',
        '50',
        '(11-20/500)',
        'Per page: ',
        '10',
        '20',
        '50',
        '100',
    ]
    assert get_texts(pagination_links(100, 10, 500)) == [
        '1',
        '&#8230;',
        '8',
        '9',
        '10',
        '11',
        '12',
        '13',
        '14',
        '&#8230;',
        '50',
        '(101-110/500)',
        'Per page: ',
        '10',
        '20',
        '50',
        '100',
    ]
    assert get_texts(pagination_links(100, 20, 500)) == [
        '1',
        '&#8230;',
        '3',
        '4',
        '5',
        '6',
        '7',
        '8',
        '9',
        '&#8230;',
        '25',
        '(101-120/500)',
        'Per page: ',
        '10',
        '20',
        '50',
        '100',
    ]

    # check limit
    assert '(1-10/1000)' in get_texts(pagination_links(0, 10, 1000))
    assert '(1-100/1000)' in get_texts(pagination_links(0, 100, 1000))
    assert '(1-100/1000)' in get_texts(pagination_links(0, 101, 1000))  # 100 is the max

    # new default pagination, more than 100
    if not pub.site_options.has_section('options'):
        pub.site_options.add_section('options')
    pub.site_options.set('options', 'default-page-size', '500')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)
    assert '(1-101/1000)' in get_texts(pagination_links(0, 101, 1000))
    assert '(1-500/1000)' in get_texts(pagination_links(0, 500, 1000))
    assert '(1-500/1000)' in get_texts(pagination_links(0, 501, 1000))  # 500 is the max


def test_cache():
    cache.set('hello', 'world')
    assert cache.get('hello') == 'world'


def test_normalize_geolocation():
    assert normalize_geolocation({'lat': 10.0, 'lon': 0.0}) == {'lat': 10.0, 'lon': 0.0}
    assert normalize_geolocation({'lat': -10.0, 'lon': 0.0}) == {'lat': -10.0, 'lon': 0.0}
    assert normalize_geolocation({'lat': 100.0, 'lon': 0.0}) == {'lat': -80.0, 'lon': 0.0}
    assert normalize_geolocation({'lat': -100.0, 'lon': 0.0}) == {'lat': 80.0, 'lon': 0.0}
    assert normalize_geolocation({'lat': 180.0, 'lon': 0.0}) == {'lat': 0.0, 'lon': 0.0}
    assert normalize_geolocation({'lat': -180.0, 'lon': 0.0}) == {'lat': 0.0, 'lon': 0.0}
    assert normalize_geolocation({'lat': 200.0, 'lon': 0.0}) == {'lat': 20.0, 'lon': 0.0}
    assert normalize_geolocation({'lat': -200.0, 'lon': 0.0}) == {'lat': -20.0, 'lon': 0.0}

    assert normalize_geolocation({'lat': 0.0, 'lon': 10.0}) == {'lat': 0.0, 'lon': 10.0}
    assert normalize_geolocation({'lat': 0.0, 'lon': -10.0}) == {'lat': 0.0, 'lon': -10.0}
    assert normalize_geolocation({'lat': 0.0, 'lon': 200.0}) == {'lat': 0.0, 'lon': -160.0}
    assert normalize_geolocation({'lat': 0.0, 'lon': -200.0}) == {'lat': 0.0, 'lon': 160.0}
    assert normalize_geolocation({'lat': 0.0, 'lon': 360.0}) == {'lat': 0.0, 'lon': 0.0}
    assert normalize_geolocation({'lat': 0.0, 'lon': -360.0}) == {'lat': 0.0, 'lon': 0.0}
    assert normalize_geolocation({'lat': 0.0, 'lon': 400.0}) == {'lat': 0.0, 'lon': 40.0}
    assert normalize_geolocation({'lat': 0.0, 'lon': -400.0}) == {'lat': 0.0, 'lon': -40.0}
    assert normalize_geolocation({'lat': math.nan, 'lon': -400.0}) is None
    assert normalize_geolocation({'lat': math.inf, 'lon': -400.0}) is None
    assert normalize_geolocation({'lat': 'foobar', 'lon': 0.0}) is None
    assert normalize_geolocation({'lat': 0.0, 'lon': 'foobar'}) is None


def test_objects_repr():
    workflow = Workflow(name='wf')
    st1 = workflow.add_status('Status1', 'st1')
    jump = st1.add_action('jump', id='_jump')

    assert 'st1' in repr(st1)
    assert '_jump' in repr(jump)

    field = StringField()
    assert repr(field) == '<StringField None None>'
    field.id = '1'
    field.label = 'test'
    assert repr(field) == "<StringField 1 'test'>"


@pytest.mark.parametrize(
    'value, length, expected',
    [
        ('', 30, ''),
        (None, 30, 'None'),
        ('foo bar', 30, 'foo bar'),
        ('01234567890123456789012345678', 30, '01234567890123456789012345678'),
        ('012345678901234567890123456789', 30, '012345678901234567890123456789'),
        ('0123456789012345678901234567890', 30, '012345678901234567890123456(…)'),
        ('foo bar', 4, 'f(…)'),
        ('foo bar', 3, 'foo'),
        ('foo bar', 2, 'fo'),
    ],
)
def test_ellipsize(value, length, expected):
    create_temporary_pub()
    assert ellipsize(value, length=length) == expected


def test_criteria_repr():
    criteria = wcs.qommon.storage.Less('foo', 'bar')
    assert 'Less' in repr(criteria)
    assert 'foo' in repr(criteria)
    assert 'bar' in repr(criteria)

    criteria = wcs.qommon.storage.Less('foo', datetime.datetime.now().timetuple())
    assert 'tm_year=' in repr(criteria)


def test_related_field_repr():
    from wcs.backoffice.filter_fields import RelatedField

    related_field = RelatedField(None, field=StringField(label='foo'), parent_field=StringField(label='bar'))
    assert 'foo' in repr(related_field)
    assert 'bar' in repr(related_field)


def test_find_vc_version():
    import wcs.qommon.admin.menu

    def mocked_popen(*args, **kwargs):
        class Process:
            returncode = 0

            def communicate(self):
                return (
                    b'''Desired=Unknown/Install/Remove/Purge/Hold
| Status=Not/Inst/Conf-files/Unpacked/halF-conf/Half-inst/trig-aWait/Trig-pend
|/ Err?=(none)/Reinst-required (Status,Err: uppercase=bad)
||/ Name           Version         Architecture Description
+++-==============-===============-============-=================================================
ii  wcs            5.71-1~eob100+1 all          web application to design and set up online forms
''',
                    '',
                )

        return Process()

    with mock.patch('os.path.exists') as os_path_exists, mock.patch('subprocess.Popen') as popen:

        def mocked_os_path_exists(path):
            return bool(not path.endswith('setup.py'))

        os_path_exists.side_effect = mocked_os_path_exists

        handle = mock.MagicMock()
        handle.__enter__.side_effect = mocked_popen
        popen.return_value = handle

        version = wcs.qommon.admin.menu._find_vc_version()
        assert version == 'wcs 5.71-1~eob100+1 (Debian)'


def test_uwsgi_spooler_import():
    with pytest.raises(ImportError):
        import wcs.qommon.spooler  # noqa pylint: disable=unused-import


@mock.patch('requests.Session.request')
def test_http_request_global_settings(mock_request, pub):
    response = {'err': 0, 'data': []}
    mock_json = mock.Mock(status_code=200)
    mock_json.json.return_value = response
    mock_request.return_value = mock_json
    from django.conf import settings

    _http_request('https://example.com/')
    mock_request.assert_called_once_with(
        'GET',
        'https://example.com/',
        cert=None,
        data=None,
        headers={'Publik-Caller-URL': ''},
        proxies=None,
        timeout=28,
    )

    settings.REQUESTS_TIMEOUT = 42
    mock_request.reset_mock()
    _http_request('https://example.com/')
    mock_request.assert_called_once_with(
        'GET',
        'https://example.com/',
        cert=None,
        data=None,
        headers={'Publik-Caller-URL': ''},
        proxies=None,
        timeout=42,
    )

    settings.REQUESTS_PROXIES = {
        'http': 'http://10.10.1.10:3128',
        'https': 'http://10.10.1.10:1080',
    }
    mock_request.reset_mock()
    _http_request('https://example.com/')
    mock_request.assert_called_once_with(
        'GET',
        'https://example.com/',
        cert=None,
        data=None,
        headers={'Publik-Caller-URL': ''},
        proxies=settings.REQUESTS_PROXIES,
        timeout=42,
    )
    settings.REQUESTS_PROXIES = None

    settings.REQUESTS_CERT = {
        'https://example.com/ssl': '/path/client.pem',
    }
    mock_request.reset_mock()
    _http_request('https://example.com/ssl/')
    mock_request.assert_called_once_with(
        'GET',
        'https://example.com/ssl/',
        cert='/path/client.pem',
        data=None,
        headers={'Publik-Caller-URL': ''},
        proxies=None,
        timeout=42,
    )
    mock_request.reset_mock()
    _http_request('https://example.com/')
    mock_request.assert_called_once_with(
        'GET',
        'https://example.com/',
        cert=None,
        data=None,
        headers={'Publik-Caller-URL': ''},
        proxies=None,
        timeout=42,
    )


@mock.patch('requests.Session.request')
def test_http_request_url_switch(mock_request, pub):
    response = {'err': 0, 'data': []}
    mock_json = mock.Mock(status_code=200)
    mock_json.json.return_value = response
    mock_request.return_value = mock_json
    pub = create_temporary_pub()
    pub.load_site_options()
    from django.conf import settings

    settings.REQUESTS_TIMEOUT = 28

    _http_request('https://example.com/')
    mock_request.assert_called_once_with(
        'GET',
        'https://example.com/',
        cert=None,
        data=None,
        headers={'Publik-Caller-URL': ''},
        proxies=None,
        timeout=28,
    )
    mock_request.reset_mock()

    pub.site_options.add_section('legacy-urls')
    pub.site_options.set('legacy-urls', 'new.example.com', 'example.com,old.example.com')
    pub.site_options.set('legacy-urls', 'old.example.com', 'new.example.com')
    pub.site_options.set(
        'legacy-urls',
        'example.com',
        'new.example.com',
    )

    _http_request('https://example.com/')
    mock_request.assert_called_once_with(
        'GET',
        'https://new.example.com/',
        cert=None,
        data=None,
        headers={'Publik-Caller-URL': ''},
        proxies=None,
        timeout=28,
    )
    mock_request.reset_mock()

    _http_request('https://old.example.com/')
    mock_request.assert_called_once_with(
        'GET',
        'https://new.example.com/',
        cert=None,
        data=None,
        headers={'Publik-Caller-URL': ''},
        proxies=None,
        timeout=28,
    )
    mock_request.reset_mock()


def test_http_request_error_url(pub, http_requests):
    with pytest.raises(match=r'net/404\?signature'):
        _http_request('http://remote.example.net/404?signature=xyz', raise_on_http_errors=True)

    with pytest.raises(match=r'net/404 '):
        _http_request(
            'http://remote.example.net/404?signature=xyz',
            raise_on_http_errors=True,
            error_url='http://remote.example.net/404',
        )


def test_validate_phone_fr(pub):
    valid = [
        '0123456789',
        '+33123456789',
        '+590690000102',
        '06 92 32 00 00',  # valid number in (+262) but not in (+33)
    ]
    invalid = [
        '1234559',
        '+32123456789',
        '01+23+45+67+89',
        'tel 0143350133',
    ]

    assert all(validate_phone_fr(pn) for pn in valid)
    assert all(not validate_phone_fr(pn) for pn in invalid)


@pytest.mark.parametrize(
    'value, expected',
    [
        ('1.3', decimal.Decimal('1.3')),
        ('1,5', decimal.Decimal(1.5)),
        (True, decimal.Decimal(0)),
        (False, decimal.Decimal(0)),
        (None, 0),
        ('', 0),
    ],
    ids=['1.3', '1,5', 'True', 'False', 'None', 'empty-string'],
)
def test_parse_decimal_base(value, expected):
    assert parse_decimal(value) == expected


@pytest.mark.parametrize(
    'value, expected',
    [
        ('1.3', decimal.Decimal('1.3')),
        ('1,5', decimal.Decimal(1.5)),
        (True, decimal.Decimal(0)),
        (False, decimal.Decimal(0)),
        (None, None),
        ('', None),
    ],
    ids=['1.3', '1,5', 'True', 'False', 'None', 'empty-string'],
)
def test_parse_decimal_keep_none(value, expected):
    assert parse_decimal(value, keep_none=True) == expected


@pytest.mark.parametrize('value', [None, '', 'xyz'], ids=['None', 'empty-string', 'alpha'])
def test_parse_decimal_do_raise(value):
    with pytest.raises(ValueError):
        parse_decimal(value, do_raise=True)


def test_mark_spaces():
    assert mark_spaces('test') == 'test'
    assert str(mark_spaces('<b>test</b>')) == '&lt;b&gt;test&lt;/b&gt;'

    button_code = (
        '<button class="toggle-escape-button" role="button" '
        'title="This line contains invisible characters."></button>'
    )
    space = '<span class="escaped-code-point" data-escaped="[U+0020]"><span class="char">&nbsp;</span></span>'
    tab = '<span class="escaped-code-point" data-escaped="[U+0009]"><span class="char">&nbsp;</span></span>'
    assert str(mark_spaces(' test ')) == button_code + space + 'test' + space
    assert str(mark_spaces(' test  ')) == button_code + space + 'test' + space + space
    assert str(mark_spaces('test\t ')) == button_code + 'test' + tab + space
    assert str(mark_spaces(' <b>test</b>')) == button_code + space + '&lt;b&gt;test&lt;/b&gt;'
