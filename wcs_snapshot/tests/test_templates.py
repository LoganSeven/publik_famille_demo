import datetime
import html
import io
import json
import os
import re
import string
import subprocess
import zipfile
from unittest import mock

import pytest
from django.test import override_settings
from django.utils import timezone, translation
from django.utils.timezone import now

try:
    import langdetect  # pylint: disable=unused-import
except ImportError:
    langdetect = None

import PIL
from pyzbar.pyzbar import ZBarSymbol
from pyzbar.pyzbar import decode as zbar_decode_qrcode

from wcs import fields
from wcs.blocks import BlockDef
from wcs.conditions import Condition
from wcs.formdef import FormDef
from wcs.logged_errors import LoggedError
from wcs.qommon.http_request import HTTPRequest
from wcs.qommon.substitution import CompatibilityNamesDict
from wcs.qommon.template import Template, TemplateError
from wcs.qommon.upload_storage import PicklableUpload
from wcs.variables import LazyFormData, LazyList
from wcs.workflows import AttachmentEvolutionPart

from .utilities import clean_temporary_pub, create_temporary_pub


@pytest.fixture
def pub():
    pub = create_temporary_pub()
    pub.substitutions.feed(pub)
    req = HTTPRequest(None, {'SCRIPT_NAME': '/', 'SERVER_NAME': 'example.net'})
    pub.set_app_dir(req)
    pub._set_request(req)
    req.session = pub.session_manager.session_class(id='1')
    pub.site_options.set('options', 'working_day_calendar', '')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)
    return pub


def teardown_module(module):
    clean_temporary_pub()


def test_template(pub):
    tmpl = Template('')
    assert tmpl.render() == ''
    assert tmpl.render({'foo': 'bar'}) == ''
    tmpl = Template('zoo')
    assert tmpl.render() == 'zoo'
    assert tmpl.render({'foo': 'bar'}) == 'zoo'

    # django
    tmpl = Template('{{ foo }}')
    assert tmpl.render() == ''
    assert tmpl.render({'foo': 'bar'}) == 'bar'
    tmpl = Template('{% if foo %}{{ foo }}{% endif %}')
    assert tmpl.render() == ''
    assert tmpl.render({'foo': 'bar'}) == 'bar'

    # ezt
    tmpl = Template('[foo]')
    assert tmpl.render() == '[foo]'
    assert tmpl.render({'foo': 'bar'}) == 'bar'
    tmpl = Template('[if-any foo][foo][end]')
    assert tmpl.render() == ''
    assert tmpl.render({'foo': 'bar'}) == 'bar'

    # mix Django/ezt: Django wins
    tmpl = Template('{% if foo %}{{ foo }}[foo]{% endif %}')
    assert tmpl.render({'foo': 'bar'}) == 'bar[foo]'

    # django syntax error
    with pytest.raises(TemplateError):
        tmpl = Template('{% if foo %}{{ foo }}{% end %}', raises=True)
    tmpl = Template('{% if foo %}{{ foo }}{% end %}')
    assert tmpl.render({'foo': 'bar'}) == '{% if foo %}{{ foo }}{% end %}'

    # ezt syntax error
    with pytest.raises(TemplateError):
        tmpl = Template('[if-any foo][foo][endif]', raises=True)
    tmpl = Template('[if-any foo][foo][endif]')
    assert tmpl.render({'foo': 'bar'}) == '[if-any foo][foo][endif]'


def test_now_and_today_variables(pub):
    # create a today string, verify it contains the year, at least
    today = Template('{{d}}').render({'d': datetime.date.today()})
    assert datetime.date.today().strftime('%Y') in today

    tmpl = Template('{{ today }}')
    for mode in (None, 'lazy'):
        context = pub.substitutions.get_context_variables(mode=mode)
        assert tmpl.render(context) == today
    tmpl = Template('{{ now }}')
    for mode in (None, 'lazy'):
        context = pub.substitutions.get_context_variables(mode=mode)
        assert today in tmpl.render(context)  # contains the date,
        assert tmpl.render(context) != today  # but not only

    # ezt templates (legacy)
    today = Template('[t]').render({'t': datetime.date.today()})
    assert today == datetime.date.today().strftime('%Y-%m-%d')
    now = Template('[n]').render({'n': datetime.datetime.now()})
    assert now == datetime.datetime.now().strftime('%Y-%m-%d %H:%M')

    tmpl = Template('[today]')
    for mode in (None, 'lazy'):
        context = pub.substitutions.get_context_variables(mode=mode)
        assert tmpl.render(context) == today
    tmpl = Template('[now]')
    for mode in (None, 'lazy'):
        context = pub.substitutions.get_context_variables(mode=mode)
        assert tmpl.render(context) == now


def test_template_templatetag(pub):
    # check qommon templatetags are always loaded
    tmpl = Template('{{ date|parse_datetime|date:"Y" }}')
    assert tmpl.render({'date': '2018-06-06'}) == '2018'

    # check other templatetags can be loaded
    tmpl = Template('{% load i18n %}{% trans "hello" %}')
    assert tmpl.render() == 'hello'


def test_startswith_templatetag(pub):
    tmpl = Template('{% if foo|startswith:"bar" %}hello{% endif %}')
    assert tmpl.render() == ''
    assert tmpl.render({'foo': 'bar-baz'}) == 'hello'
    assert tmpl.render({'foo': 'baz-bar'}) == ''


def test_endswith_templatetag(pub):
    tmpl = Template('{% if foo|endswith:"bar" %}hello{% endif %}')
    assert tmpl.render() == ''
    assert tmpl.render({'foo': 'baz-bar'}) == 'hello'
    assert tmpl.render({'foo': 'bar-baz'}) == ''


def test_split_templatetag(pub):
    tmpl = Template('{{ foo|split|last }}')
    assert tmpl.render() == ''
    assert tmpl.render({'foo': 'bar baz'}) == 'baz'
    assert tmpl.render({'foo': 'baz-bar'}) == 'baz-bar'
    assert tmpl.render({'foo': 'baz \n bar'}) == 'bar'

    tmpl = Template('{{ foo|split:"-"|last }}')
    assert tmpl.render() == ''
    assert tmpl.render({'foo': 'bar-baz'}) == 'baz'
    assert tmpl.render({'foo': 'baz-bar'}) == 'bar'


def test_strip_templatetag(pub):
    tmpl = Template('{{ foo|strip:"_" }}')
    assert tmpl.render() == ''
    assert tmpl.render({'foo': None}) == ''
    assert tmpl.render({'foo': '_foo bar'}) == 'foo bar'
    assert tmpl.render({'foo': '_foo bar__'}) == 'foo bar'
    assert tmpl.render({'foo': '_félé_'}) == 'félé'
    tmpl = Template('{{ foo|strip:"XY" }}')
    assert tmpl.render({'foo': 'XXfoo barXYX'}) == 'foo bar'
    assert tmpl.render({'foo': ' foo barXX'}) == 'foo bar'


def test_removeprefix_templatetag(pub):
    tmpl = Template('{{ foo|removeprefix }}')
    assert tmpl.render() == ''
    assert tmpl.render({'foo': None}) == ''
    assert tmpl.render({'foo': 'foo bar'}) == 'foo bar'
    tmpl = Template('{{ foo|removeprefix:"" }}')
    assert tmpl.render({'foo': 'foo bar'}) == 'foo bar'
    tmpl = Template('{{ foo|removeprefix:"XY" }}')
    assert tmpl.render({'foo': 'XYfoo barXY'}) == 'foo barXY'
    assert tmpl.render({'foo': 'foo bar'}) == 'foo bar'
    assert tmpl.render({'foo': 'xyfoo barXY'}) == 'xyfoo barXY'
    assert tmpl.render({'foo': ' XYfoo barXY'}) == 'XYfoo barXY'
    assert tmpl.render({'foo': 'XYXYfoo barXY'}) == 'XYfoo barXY'


def test_removesuffix_templatetag(pub):
    tmpl = Template('{{ foo|removesuffix }}')
    assert tmpl.render() == ''
    assert tmpl.render({'foo': None}) == ''
    assert tmpl.render({'foo': 'foo bar'}) == 'foo bar'
    tmpl = Template('{{ foo|removesuffix:"" }}')
    assert tmpl.render({'foo': 'foo bar'}) == 'foo bar'
    tmpl = Template('{{ foo|removesuffix:"XY" }}')
    assert tmpl.render({'foo': 'XYfoo barXY'}) == 'XYfoo bar'
    assert tmpl.render({'foo': 'foo bar'}) == 'foo bar'
    assert tmpl.render({'foo': 'XYfoo barxy'}) == 'XYfoo barxy'
    assert tmpl.render({'foo': 'XYfoo barXY '}) == 'XYfoo barXY'
    assert tmpl.render({'foo': 'XYfoo barXYXY'}) == 'XYfoo barXY'


def test_strip_emoji_templatetag(pub):
    tmpl = Template('{{ foo|strip_emoji }}')
    assert tmpl.render() == ''
    assert tmpl.render({'foo': 'X🦄Y'}) == 'XY'


def test_urljoin_templatefilter(pub):
    tmpl = Template('{{ foo|urljoin }}')
    assert tmpl.render() == ''
    assert tmpl.render({'foo': None}) == ''
    assert tmpl.render({'foo': ''}) == ''
    assert tmpl.render({'foo': 'http://example.com'}) == 'http://example.com'
    assert tmpl.render({'foo': 'http://example.com/'}) == 'http://example.com/'

    tmpl = Template('{{ foo|urljoin:bar }}')
    assert tmpl.render({'foo': None, 'bar': None}) == ''
    assert tmpl.render({'foo': None, 'bar': 'some/url'}) == 'some/url'
    assert tmpl.render({'foo': 'http://example.com', 'bar': None}) == 'http://example.com'
    assert tmpl.render({'foo': 'http://example.com', 'bar': 'some/url'}) == 'http://example.com/some/url'
    assert tmpl.render({'foo': 'http://example.com/', 'bar': 'some/url'}) == 'http://example.com/some/url'
    assert tmpl.render({'foo': 'http://example.com', 'bar': '/some/url'}) == 'http://example.com/some/url'
    assert tmpl.render({'foo': 'http://example.com/', 'bar': '/some/url'}) == 'http://example.com/some/url'
    assert tmpl.render({'foo': 'http://example.com/', 'bar': '/some/url'}) == 'http://example.com/some/url'


def test_unaccent_templatetag(pub):
    LoggedError.wipe()
    tmpl = Template('{{ foo|unaccent }}')
    assert tmpl.render() == ''
    assert tmpl.render({'foo': None}) == ''
    assert tmpl.render({'foo': 'FOO bar'}) == 'FOO bar'
    assert tmpl.render({'foo': 'félé'}) == 'fele'
    assert tmpl.render({'foo': 42}) == ''
    assert LoggedError.count() == 1
    logged_error = LoggedError.select(order_by='id')[0]
    assert logged_error.summary == 'Failed to apply unaccent filter on value (42)'
    assert tmpl.render({'foo': ['a', 'z']}) == ''
    assert LoggedError.count() == 2
    logged_error = LoggedError.select(order_by='id')[1]
    assert logged_error.summary == "Failed to apply unaccent filter on value (['a', 'z'])"

    # lazy mode
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'lazy'
    formdef.fields = [fields.StringField(id='0', label='string', varname='foo')]
    formdef.store()
    formdata = formdef.data_class()()
    formdata.data = {'0': 'félà'}
    formdata.store()
    context = CompatibilityNamesDict({'form': LazyFormData(formdata)})
    tmpl = Template('{{ form_var_foo|unaccent }}')
    assert tmpl.render(context) == 'fela'
    formdata.data = {'0': None}
    formdata.store()
    assert tmpl.render(context) == ''


def test_template_encoding(pub):
    # django
    tmpl = Template('{{ foo }} à vélo')
    assert tmpl.render() == 'à vélo'
    assert tmpl.render({'foo': 'fou'}) == 'fou à vélo'
    assert tmpl.render({'foo': 'félé'}) == 'félé à vélo'

    tmpl = Template("{% if foo == 'félé' %}à vélo{% endif %}")
    assert tmpl.render() == ''
    assert tmpl.render({'foo': 'fou'}) == ''
    assert tmpl.render({'foo': 'félé'}) == 'à vélo'

    tmpl = Template("{% if foo.bar == 'félé' %}à vélo{% endif %}")
    assert tmpl.render() == ''
    assert tmpl.render({'foo': {'bar': 'fou'}}) == ''
    assert tmpl.render({'foo': {'bar': 'félé'}}) == 'à vélo'

    # ezt
    tmpl = Template('[foo] à vélo')
    assert tmpl.render() == '[foo] à vélo'
    assert tmpl.render({'foo': 'fou'}) == 'fou à vélo'
    assert tmpl.render({'foo': 'félé'}) == 'félé à vélo'


def test_datetime_templatetags(pub):
    tmpl = Template('{{ plop|datetime }}')
    assert tmpl.render({'plop': '2017-12-21 10:32'}) == '2017-12-21 10:32'
    assert tmpl.render({'plop': '21/12/2017 10h32'}) == '2017-12-21 10:32'
    assert tmpl.render({'plop': '2017-12-21'}) == '2017-12-21 00:00'
    assert tmpl.render({'plop': '21/12/2017'}) == '2017-12-21 00:00'
    with override_settings(LANGUAGE_CODE='fr-fr'):
        assert tmpl.render({'plop': '2017-12-21 10:32'}) == '21/12/2017 10:32'
        assert tmpl.render({'plop': '21/12/2017 10h32'}) == '21/12/2017 10:32'
        assert tmpl.render({'plop': '2017-12-21'}) == '21/12/2017 00:00'
        assert tmpl.render({'plop': '21/12/2017'}) == '21/12/2017 00:00'
    assert tmpl.render({'plop': '10h32'}) == ''
    assert tmpl.render({'plop': 'x'}) == ''
    assert tmpl.render({'plop': None}) == ''
    assert tmpl.render({'plop': 3}) == ''
    assert tmpl.render({'plop': {'foo': 'bar'}}) == ''
    assert tmpl.render() == ''

    tmpl = Template('{{ plop|datetime:"d i" }}')
    assert tmpl.render({'plop': '2017-12-21 10:32'}) == '21 32'
    assert tmpl.render({'plop': '2017-12-21 10:32:42'}) == '21 32'
    assert tmpl.render({'plop': '21/12/2017 10:32'}) == '21 32'
    assert tmpl.render({'plop': '21/12/2017 10:32:42'}) == '21 32'
    assert tmpl.render({'plop': '21/12/2017 10h32'}) == '21 32'
    assert tmpl.render({'plop': '21/12/2017'}) == '21 00'
    assert tmpl.render({'plop': '10h32'}) == ''
    assert tmpl.render({'plop': 'x'}) == ''
    assert tmpl.render({'plop': None}) == ''
    assert tmpl.render({'plop': 3}) == ''
    assert tmpl.render({'plop': {'foo': 'bar'}}) == ''
    assert tmpl.render() == ''

    tmpl = Template('{% if d1|datetime > d2|datetime %}d1>d2{% else %}d1<=d2{% endif %}')
    assert tmpl.render({'d1': '2017-12-22', 'd2': '2017-12-21'}) == 'd1>d2'
    assert tmpl.render({'d1': '2017-12-21', 'd2': '2017-12-21'}) == 'd1<=d2'
    assert tmpl.render({'d1': '2017-12-21 10:30', 'd2': '2017-12-21 09:00'}) == 'd1>d2'
    assert tmpl.render({'d1': '2017-12-21 10:30', 'd2': '2017-12-21'}) == 'd1>d2'
    assert tmpl.render({'d1': '2017-12-22'}) == 'd1<=d2'
    assert tmpl.render({'d2': '2017-12-22'}) == 'd1<=d2'

    tmpl = Template('{{ plop|date }}')
    assert tmpl.render({'plop': '2017-12-21'}) == '2017-12-21'
    assert tmpl.render({'plop': '21/12/2017'}) == '2017-12-21'
    assert tmpl.render({'plop': '2017-12-21 10:32'}) == '2017-12-21'
    assert tmpl.render({'plop': '21/12/2017 10:32'}) == '2017-12-21'
    assert tmpl.render({'plop': '21/12/2017 10h32'}) == '2017-12-21'
    assert tmpl.render({'plop': '21/12/2017 10:32:42'}) == '2017-12-21'
    with override_settings(LANGUAGE_CODE='fr-fr'):
        assert tmpl.render({'plop': '2017-12-21'}) == '21/12/2017'
        assert tmpl.render({'plop': '21/12/2017'}) == '21/12/2017'
        assert tmpl.render({'plop': '2017-12-21 10:32'}) == '21/12/2017'
        assert tmpl.render({'plop': '21/12/2017 10:32'}) == '21/12/2017'
        assert tmpl.render({'plop': '21/12/2017 10h32'}) == '21/12/2017'
        assert tmpl.render({'plop': '21/12/2017 10:32:42'}) == '21/12/2017'
    assert tmpl.render({'plop': '10:32'}) == ''
    assert tmpl.render({'plop': 'x'}) == ''
    assert tmpl.render({'plop': None}) == ''
    assert tmpl.render({'plop': 3}) == ''
    assert tmpl.render({'plop': {'foo': 'bar'}}) == ''
    assert tmpl.render() == ''

    tmpl = Template('{{ plop|date:"d" }}')
    assert tmpl.render({'plop': '2017-12-21'}) == '21'
    assert tmpl.render({'plop': '21/12/2017'}) == '21'
    assert tmpl.render({'plop': '2017-12-21 10:32'}) == '21'
    assert tmpl.render({'plop': '10:32'}) == ''
    assert tmpl.render({'plop': 'x'}) == ''
    assert tmpl.render({'plop': None}) == ''
    assert tmpl.render({'plop': 3}) == ''
    assert tmpl.render({'plop': {'foo': 'bar'}}) == ''
    assert tmpl.render() == ''

    tmpl = Template('{{ plop|date:"d B Y" }}')
    # B is not considered a format character
    assert tmpl.render({'plop': '2017-12-21'}) == '21 B 2017'

    tmpl = Template('{% if d1|date > d2|date %}d1>d2{% else %}d1<=d2{% endif %}')
    assert tmpl.render({'d1': '2017-12-22', 'd2': '2017-12-21'}) == 'd1>d2'
    assert tmpl.render({'d1': '2017-12-21', 'd2': '2017-12-21'}) == 'd1<=d2'
    assert tmpl.render({'d1': '2017-12-22'}) == 'd1<=d2'
    assert tmpl.render({'d2': '2017-12-22'}) == 'd1<=d2'

    tmpl = Template('{{ plop|time }}')
    assert tmpl.render({'plop': '10:32'}) == '10:32 a.m.'
    assert tmpl.render({'plop': '2017-12-21 10:32'}) == '10:32 a.m.'
    assert tmpl.render({'plop': '21/12/2017 10h32'}) == '10:32 a.m.'
    assert tmpl.render({'plop': '21/12/2017'}) == 'midnight'
    assert tmpl.render({'plop': 'x'}) == ''
    assert tmpl.render({'plop': None}) == ''
    assert tmpl.render({'plop': 3}) == ''
    assert tmpl.render({'plop': {'foo': 'bar'}}) == ''
    assert tmpl.render() == ''

    tmpl = Template('{{ plop|time:"H i" }}')
    assert tmpl.render({'plop': '10:32'}) == '10 32'
    assert tmpl.render({'plop': '2017-12-21 10:32'}) == '10 32'
    assert tmpl.render({'plop': '21/12/2017 10h32'}) == '10 32'
    assert tmpl.render({'plop': '21/12/2017'}) == '00 00'
    assert tmpl.render({'plop': 'x'}) == ''
    assert tmpl.render({'plop': None}) == ''
    assert tmpl.render({'plop': 3}) == ''
    assert tmpl.render({'plop': {'foo': 'bar'}}) == ''
    assert tmpl.render() == ''

    # old fashion, with parse_*
    tmpl = Template('{{ plop|parse_datetime|date:"d i" }}')
    assert tmpl.render({'plop': '2017-12-21 10:32'}) == '21 32'
    assert tmpl.render({'plop': '2017-12-21 10:32:42'}) == '21 32'
    assert tmpl.render({'plop': '21/12/2017 10:32'}) == '21 32'
    assert tmpl.render({'plop': '21/12/2017 10:32:42'}) == '21 32'
    assert tmpl.render({'plop': '21/12/2017 10h32'}) == '21 32'
    assert tmpl.render({'plop': 'x'}) == ''
    assert tmpl.render({'plop': None}) == ''
    assert tmpl.render({'plop': ()}) == ''
    assert tmpl.render({'plop': []}) == ''
    assert tmpl.render({'plop': 3}) == ''
    assert tmpl.render({'plop': {'foo': 'bar'}}) == ''
    assert tmpl.render() == ''

    tmpl = Template('{{ plop|parse_date|date:"d" }}')
    assert tmpl.render({'plop': '2017-12-21'}) == '21'
    assert tmpl.render({'plop': '21/12/2017'}) == '21'
    assert tmpl.render({'plop': 'x'}) == ''
    assert tmpl.render({'plop': None}) == ''
    assert tmpl.render({'plop': 3}) == ''
    assert tmpl.render({'plop': {'foo': 'bar'}}) == ''
    assert tmpl.render() == ''

    tmpl = Template('{{ plop|parse_time|date:"H i" }}')
    assert tmpl.render({'plop': '10:32'}) == '10 32'
    assert tmpl.render({'plop': 'x'}) == ''
    assert tmpl.render({'plop': None}) == ''
    assert tmpl.render({'plop': 3}) == ''
    assert tmpl.render({'plop': {'foo': 'bar'}}) == ''
    assert tmpl.render() == ''


def test_datetime_templatetags_with_timezone(pub):
    tmpl = Template('{{ plop|datetime }}')
    pub.site_options.set('options', 'timezone', 'Brazil/East')
    pub.setup_timezone()
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)
    assert tmpl.render({'plop': '2017-12-21T10:32:00+00:00'}) == '2017-12-21 07:32'
    assert tmpl.render({'plop': '2017-06-21T10:32:00+00:00'}) == '2017-06-21 08:32'
    timezone.deactivate()


def test_date_maths(pub):
    tmpl = Template('{{ plop|add_days:4 }}')
    assert tmpl.render({'plop': None}) == ''
    tmpl = Template('{{ plop|add_days:4 }}')
    assert tmpl.render({'plop': 2}) == ''  # TypeError
    tmpl = Template('{{ plop|add_days:4 }}')
    assert tmpl.render({'plop': '2017-12-21'}) == '2017-12-25'
    assert tmpl.render({'plop': '2017-12-21 18:00'}) == '2017-12-25 18:00'
    tmpl = Template('{{ plop|date|add_days:4 }}')
    assert tmpl.render({'plop': '2017-12-21'}) == '2017-12-25'
    assert tmpl.render({'plop': '2017-12-21 18:00'}) == '2017-12-25'
    tmpl = Template('{{ plop|datetime|add_days:4 }}')
    assert tmpl.render({'plop': '2017-12-21'}) == '2017-12-25 00:00'
    assert tmpl.render({'plop': '2017-12-21 18:00'}) == '2017-12-25 18:00'
    tmpl = Template('{{ plop|add_days:"-1" }}')
    assert tmpl.render({'plop': '2017-12-21'}) == '2017-12-20'
    assert tmpl.render({'plop': '2017-12-21 18:00'}) == '2017-12-20 18:00'
    tmpl = Template('{{ plop|add_days:1.5 }}')
    assert tmpl.render({'plop': '2017-12-21'}) == '2017-12-22'
    assert tmpl.render({'plop': '2017-12-21 18:00'}) == '2017-12-23 06:00'
    tmpl = Template('{{ plop|add_days:"1.5" }}')
    assert tmpl.render({'plop': '2017-12-21'}) == '2017-12-22'
    assert tmpl.render({'plop': '2017-12-21 18:00'}) == '2017-12-23 06:00'

    tmpl = Template('{{ plop|add_hours:24 }}')
    assert tmpl.render({'plop': '2017-12-21'}) == '2017-12-22 00:00'
    assert tmpl.render({'plop': '2017-12-21 18:00'}) == '2017-12-22 18:00'
    tmpl = Template('{{ plop|add_hours:"12.5" }}')
    assert tmpl.render({'plop': '2017-12-21'}) == '2017-12-21 12:30'
    assert tmpl.render({'plop': '2017-12-21 18:00'}) == '2017-12-22 06:30'

    tmpl = Template('{{ plop|add_minutes:30 }}')
    assert tmpl.render({'plop': '2017-12-21'}) == '2017-12-21 00:30'
    assert tmpl.render({'plop': '2017-12-21 18:00'}) == '2017-12-21 18:30'
    tmpl = Template('{{ plop|add_minutes:"12.5"|date:"Y-m-d H:m:s" }}')
    assert tmpl.render({'plop': '2017-12-21'}) == '2017-12-21 00:12:30'
    assert tmpl.render({'plop': '2017-12-21 18:00'}) == '2017-12-21 18:12:30'


def test_variable_unicode_error_handling(pub):
    tmpl = Template('{{ form_var_éléphant }}')
    assert tmpl.render() == ''


def test_decimal_templatetag(pub):
    tmpl = Template('{{ plop|decimal }}')
    assert tmpl.render({'plop': 'toto'}) == '0'
    assert tmpl.render({'plop': '3.14'}) == '3.14'
    assert tmpl.render({'plop': '3,14'}) == '3.14'
    assert tmpl.render({'plop': 3.14}) == '3.14'
    assert tmpl.render({'plop': 12345.678}) == '12345.678'
    assert tmpl.render({'plop': None}) == '0'
    assert tmpl.render({'plop': 0}) == '0'
    assert tmpl.render({'plop': ['foo', 'bar']}) == '0'
    assert tmpl.render({'plop': ['a', 'b', 'c']}) == '0'

    tmpl = Template('{{ plop|decimal:3 }}')
    assert tmpl.render({'plop': '3.14'}) == '3.140'
    assert tmpl.render({'plop': None}) == '0.000'
    tmpl = Template('{{ plop|decimal:"3" }}')
    assert tmpl.render({'plop': '3.14'}) == '3.140'
    assert tmpl.render({'plop': None}) == '0.000'

    tmpl = Template('{% if plop|decimal > 2 %}hello{% endif %}')
    assert tmpl.render({'plop': 3}) == 'hello'
    assert tmpl.render({'plop': '3'}) == 'hello'
    assert tmpl.render({'plop': 2.001}) == 'hello'
    assert tmpl.render({'plop': '2.001'}) == 'hello'
    assert tmpl.render({'plop': 1}) == ''
    assert tmpl.render({'plop': 1.99}) == ''
    assert tmpl.render({'plop': '1.99'}) == ''
    assert tmpl.render({'plop': 'x'}) == ''
    assert tmpl.render({'plop': None}) == ''
    assert tmpl.render({'plop': 0}) == ''

    tmpl = Template('{% if "3"|decimal == 3 %}hello{% endif %}')
    assert tmpl.render() == 'hello'
    tmpl = Template('{% if "3"|decimal == 3.0 %}hello{% endif %}')
    assert tmpl.render() == 'hello'
    tmpl = Template('{% if 3|decimal == 3 %}hello{% endif %}')
    assert tmpl.render() == 'hello'
    tmpl = Template('{% if 3.0|decimal == 3 %}hello{% endif %}')
    assert tmpl.render() == 'hello'
    tmpl = Template('{% if 3|decimal|decimal == 3 %}hello{% endif %}')
    assert tmpl.render() == 'hello'


def test_integer_templatetag(pub):
    with pub.complex_data():
        tmpl = Template('{{ plop|integer }}')
        assert pub.get_cached_complex_data(tmpl.render({'plop': '42.1', 'allow_complex': True})) == 42
        assert pub.get_cached_complex_data(tmpl.render({'plop': '-42.1', 'allow_complex': True})) == -42
        assert pub.get_cached_complex_data(tmpl.render({'plop': 42.1, 'allow_complex': True})) == 42
        assert pub.get_cached_complex_data(tmpl.render({'plop': None, 'allow_complex': True})) == 0

    tmpl = Template('{{ plop|integer }}')
    assert tmpl.render({'plop': 'toto'}) == '0'
    assert tmpl.render({'plop': '3.14'}) == '3'
    assert tmpl.render({'plop': '3,14'}) == '3'
    assert tmpl.render({'plop': 3.14}) == '3'
    assert tmpl.render({'plop': 12345.678}) == '12345'
    assert tmpl.render({'plop': None}) == '0'
    assert tmpl.render({'plop': 0}) == '0'
    assert tmpl.render({'plop': ['foo', 'bar']}) == '0'
    assert tmpl.render({'plop': ['a', 'b', 'c']}) == '0'
    assert tmpl.render({'plop': '2.99'}) == '2'
    assert tmpl.render({'plop': '2.9999999999999999999999999'}) == '3'

    tmpl = Template('{% if plop|integer > 2 %}hello{% endif %}')
    assert tmpl.render({'plop': 3}) == 'hello'
    assert tmpl.render({'plop': '3'}) == 'hello'
    assert tmpl.render({'plop': 2.001}) == ''
    assert tmpl.render({'plop': '2.001'}) == ''
    assert tmpl.render({'plop': 1}) == ''
    assert tmpl.render({'plop': 1.99}) == ''
    assert tmpl.render({'plop': '1.99'}) == ''
    assert tmpl.render({'plop': 'x'}) == ''
    assert tmpl.render({'plop': None}) == ''
    assert tmpl.render({'plop': 0}) == ''

    tmpl = Template('{% if "3"|integer == 3 %}hello{% endif %}')
    assert tmpl.render() == 'hello'
    tmpl = Template('{% if "3"|integer == 3.0 %}hello{% endif %}')
    assert tmpl.render() == 'hello'
    tmpl = Template('{% if "3.01"|integer == 3.0 %}hello{% endif %}')
    assert tmpl.render() == 'hello'
    tmpl = Template('{% if 3|integer == 3 %}hello{% endif %}')
    assert tmpl.render() == 'hello'
    tmpl = Template('{% if 3.0|integer == 3 %}hello{% endif %}')
    assert tmpl.render() == 'hello'
    tmpl = Template('{% if 3|decimal|integer == 3 %}hello{% endif %}')
    assert tmpl.render() == 'hello'
    tmpl = Template('{% if 3|integer|integer == 3 %}hello{% endif %}')
    assert tmpl.render() == 'hello'


def test_float_templatetag(pub):
    with pub.complex_data():
        tmpl = Template('{{ plop|float }}')
        assert pub.get_cached_complex_data(tmpl.render({'plop': '42.1', 'allow_complex': True})) == 42.1
        assert pub.get_cached_complex_data(tmpl.render({'plop': '-42.1', 'allow_complex': True})) == -42.1
        assert pub.get_cached_complex_data(tmpl.render({'plop': 42.1, 'allow_complex': True})) == 42.1
        assert pub.get_cached_complex_data(tmpl.render({'plop': None, 'allow_complex': True})) == 0

    tmpl = Template('{{ plop|float }}')
    assert tmpl.render({'plop': 'toto'}) == '0.0'
    assert tmpl.render({'plop': '3.14'}) == '3.14'
    assert tmpl.render({'plop': '3,14'}) == '3.14'


def test_boolean_templatetag(pub):
    with pub.complex_data():
        tmpl = Template('{{ plop|boolean }}')
        assert pub.get_cached_complex_data(tmpl.render({'plop': None, 'allow_complex': True})) is False
        assert pub.get_cached_complex_data(tmpl.render({'plop': '', 'allow_complex': True})) is False
        assert pub.get_cached_complex_data(tmpl.render({'plop': 'false', 'allow_complex': True})) is False
        assert pub.get_cached_complex_data(tmpl.render({'plop': False, 'allow_complex': True})) is False
        assert pub.get_cached_complex_data(tmpl.render({'plop': 0, 'allow_complex': True})) is False
        assert pub.get_cached_complex_data(tmpl.render({'plop': 'a', 'allow_complex': True})) is True
        assert pub.get_cached_complex_data(tmpl.render({'plop': True, 'allow_complex': True})) is True
        assert pub.get_cached_complex_data(tmpl.render({'plop': 1, 'allow_complex': True})) is True

    tmpl = Template('{{ plop|boolean }}')
    assert tmpl.render({'plop': True}) == 'True'
    assert tmpl.render({'plop': False}) == 'False'


def test_mathematics_templatetag(pub):
    tmpl = Template('{{ term1|add:term2 }}')

    # using strings
    assert tmpl.render({'term1': '1.1', 'term2': 0}) == '1.1'
    assert tmpl.render({'term1': 'not a number', 'term2': 1.2}) == ''
    assert tmpl.render({'term1': 0.3, 'term2': '1'}) == '1.3'
    assert tmpl.render({'term1': 1.4, 'term2': 'not a number'}) == ''

    # add
    assert tmpl.render({'term1': 4, 'term2': -0.9}) == '3.1'
    assert tmpl.render({'term1': '4', 'term2': -0.8}) == '3.2'
    assert tmpl.render({'term1': 4, 'term2': '-0.7'}) == '3.3'
    assert tmpl.render({'term1': '4', 'term2': '-0.6'}) == '3.4'
    assert tmpl.render({'term1': '', 'term2': 3.5}) == '3.5'
    assert tmpl.render({'term1': None, 'term2': 3.5}) == '3.5'
    assert tmpl.render({'term1': 3.6, 'term2': ''}) == '3.6'
    assert tmpl.render({'term1': '', 'term2': ''}) == ''
    assert tmpl.render({'term1': 3.6, 'term2': None}) == '3.6'
    assert tmpl.render({'term1': 0, 'term2': ''}) == '0'
    assert tmpl.render({'term1': '', 'term2': 0}) == '0'
    assert tmpl.render({'term1': 0, 'term2': 0}) == '0'

    # add using ',' instead of '.' decimal separator
    assert tmpl.render({'term1': '1,1', 'term2': '2,2'}) == '3.3'
    assert tmpl.render({'term1': '1,1', 'term2': '2.2'}) == '3.3'
    assert tmpl.render({'term1': '1,1', 'term2': 2.2}) == '3.3'
    assert tmpl.render({'term1': '1,1', 'term2': 0}) == '1.1'
    assert tmpl.render({'term1': '1,1', 'term2': ''}) == '1.1'
    assert tmpl.render({'term1': '1,1', 'term2': None}) == '1.1'
    assert tmpl.render({'term1': '1.1', 'term2': '2,2'}) == '3.3'
    assert tmpl.render({'term1': 1.1, 'term2': '2,2'}) == '3.3'
    assert tmpl.render({'term1': 0, 'term2': '2,2'}) == '2.2'
    assert tmpl.render({'term1': '', 'term2': '2,2'}) == '2.2'
    assert tmpl.render({'term1': None, 'term2': '2,2'}) == '2.2'

    # fallback to Django native add filter
    assert tmpl.render({'term1': 'foo', 'term2': 'bar'}) == 'foobar'
    assert tmpl.render({'term1': 'foo', 'term2': ''}) == 'foo'
    assert tmpl.render({'term1': 'foo', 'term2': None}) == 'foo'
    assert tmpl.render({'term1': '', 'term2': 'bar'}) == 'bar'
    assert tmpl.render({'term1': '', 'term2': None}) == ''
    assert tmpl.render({'term1': None, 'term2': 'bar'}) == 'bar'
    assert tmpl.render({'term1': None, 'term2': ''}) == ''
    assert tmpl.render({'term1': None, 'term2': None}) == ''
    assert tmpl.render({'term1': [1, 2], 'term2': [3, 4]}) == '[1, 2, 3, 4]'

    # subtract
    tmpl = Template('{{ term1|subtract:term2 }}')
    assert tmpl.render({'term1': 5.1, 'term2': 1}) == '4.1'
    assert tmpl.render({'term1': '5.2', 'term2': 1}) == '4.2'
    assert tmpl.render({'term1': 5.3, 'term2': '1'}) == '4.3'
    assert tmpl.render({'term1': '5.4', 'term2': '1'}) == '4.4'
    assert tmpl.render({'term1': '', 'term2': -4.5}) == '4.5'
    assert tmpl.render({'term1': 4.6, 'term2': ''}) == '4.6'
    assert tmpl.render({'term1': '', 'term2': ''}) == '0'
    assert tmpl.render({'term1': 0, 'term2': ''}) == '0'
    assert tmpl.render({'term1': '', 'term2': 0}) == '0'
    assert tmpl.render({'term1': 0, 'term2': 0}) == '0'

    # multiply
    tmpl = Template('{{ term1|multiply:term2 }}')
    assert tmpl.render({'term1': '3', 'term2': '2'}) == '6'
    assert tmpl.render({'term1': 2.5, 'term2': 2}) == '5.0'
    assert tmpl.render({'term1': '2.5', 'term2': 2}) == '5.0'
    assert tmpl.render({'term1': 2.5, 'term2': '2'}) == '5.0'
    assert tmpl.render({'term1': '2.5', 'term2': '2'}) == '5.0'
    assert tmpl.render({'term1': '', 'term2': '2'}) == '0'
    assert tmpl.render({'term1': 2.5, 'term2': ''}) == '0.0'
    assert tmpl.render({'term1': '', 'term2': ''}) == '0'
    assert tmpl.render({'term1': 0, 'term2': ''}) == '0'
    assert tmpl.render({'term1': '', 'term2': 0}) == '0'
    assert tmpl.render({'term1': 0, 'term2': 0}) == '0'
    assert tmpl.render({'term1': 780, 'term2': 0.000463}) == '0.361140'

    # divide
    tmpl = Template('{{ term1|divide:term2 }}')
    assert tmpl.render({'term1': 16, 'term2': 2}) == '8'
    assert tmpl.render({'term1': 6, 'term2': 0.75}) == '8'
    assert tmpl.render({'term1': '6', 'term2': 0.75}) == '8'
    assert tmpl.render({'term1': 6, 'term2': '0.75'}) == '8'
    assert tmpl.render({'term1': '6', 'term2': '0.75'}) == '8'
    assert tmpl.render({'term1': '', 'term2': '2'}) == '0'
    assert tmpl.render({'term1': 6, 'term2': ''}) == ''
    assert tmpl.render({'term1': '', 'term2': ''}) == ''
    assert tmpl.render({'term1': 0, 'term2': ''}) == ''
    assert tmpl.render({'term1': '', 'term2': 0}) == ''
    assert tmpl.render({'term1': 0, 'term2': 0}) == ''
    tmpl = Template('{{ term1|divide:term2|decimal:2 }}')
    assert tmpl.render({'term1': 2, 'term2': 3}) == '0.67'

    # modulo
    tmpl = Template('{{ term1|modulo:term2 }}')
    assert tmpl.render({'term1': 16, 'term2': 2}) == '0'
    assert tmpl.render({'term1': 16, 'term2': 3}) == '1'
    assert tmpl.render({'term1': 16, 'term2': 7}) == '2'
    assert tmpl.render({'term1': 6, 'term2': 0.75}) == '0.00'
    assert tmpl.render({'term1': 6, 'term2': 0.85}) == '0.05'
    assert tmpl.render({'term1': '6', 'term2': 0.75}) == '0.00'
    assert tmpl.render({'term1': 6, 'term2': '0.75'}) == '0.00'
    assert tmpl.render({'term1': '6', 'term2': '0.75'}) == '0.00'
    assert tmpl.render({'term1': '', 'term2': '2'}) == '0'
    assert tmpl.render({'term1': 6, 'term2': ''}) == ''
    assert tmpl.render({'term1': '', 'term2': ''}) == ''
    assert tmpl.render({'term1': 0, 'term2': ''}) == ''
    assert tmpl.render({'term1': '', 'term2': 0}) == ''
    assert tmpl.render({'term1': 0, 'term2': 0}) == ''
    assert tmpl.render({'term1': 'a', 'term2': 2}) == '0'
    assert tmpl.render({'term1': 2, 'term2': 'b'}) == ''
    tmpl = Template('{{ term1|modulo:term2|decimal:2 }}')
    assert tmpl.render({'term1': 2, 'term2': 3}) == '2.00'


def test_rounding_templatetag(pub):
    # ceil
    tmpl = Template('{{ value|ceil }}')
    assert tmpl.render({'value': 3.14}) == '4'
    assert tmpl.render({'value': 3.99}) == '4'
    assert tmpl.render({'value': -3.14}) == '-3'
    assert tmpl.render({'value': -3.99}) == '-3'
    assert tmpl.render({'value': 0}) == '0'
    assert tmpl.render({'value': '3.14'}) == '4'
    assert tmpl.render({'value': '3.99'}) == '4'
    assert tmpl.render({'value': '-3.14'}) == '-3'
    assert tmpl.render({'value': '-3.99'}) == '-3'
    assert tmpl.render({'value': '0'}) == '0'
    assert tmpl.render({'value': 'not a number'}) == '0'
    assert tmpl.render({'value': ''}) == '0'
    assert tmpl.render({'value': None}) == '0'

    # floor
    tmpl = Template('{{ value|floor }}')
    assert tmpl.render({'value': 3.14}) == '3'
    assert tmpl.render({'value': 3.99}) == '3'
    assert tmpl.render({'value': -3.14}) == '-4'
    assert tmpl.render({'value': -3.99}) == '-4'
    assert tmpl.render({'value': 0}) == '0'
    assert tmpl.render({'value': '3.14'}) == '3'
    assert tmpl.render({'value': '3.99'}) == '3'
    assert tmpl.render({'value': '-3.14'}) == '-4'
    assert tmpl.render({'value': '-3.99'}) == '-4'
    assert tmpl.render({'value': '0'}) == '0'
    assert tmpl.render({'value': 'not a number'}) == '0'
    assert tmpl.render({'value': ''}) == '0'
    assert tmpl.render({'value': None}) == '0'


def test_abs_templatetag(pub):
    tmpl = Template('{{ value|abs }}')
    assert tmpl.render({'value': 3.14}) == '3.14'
    assert tmpl.render({'value': -3.14}) == '3.14'
    assert tmpl.render({'value': 0}) == '0'
    assert tmpl.render({'value': '3.14'}) == '3.14'
    assert tmpl.render({'value': '-3.14'}) == '3.14'
    assert tmpl.render({'value': '0'}) == '0'
    assert tmpl.render({'value': 'not a number'}) == '0'
    assert tmpl.render({'value': ''}) == '0'
    assert tmpl.render({'value': None}) == '0'


def test_clamp_templatetag(pub):
    tmpl = Template('{{ value|clamp:"3.5 5.5" }}')
    assert tmpl.render({'value': 4}) == '4'
    assert tmpl.render({'value': 6}) == '5.5'
    assert tmpl.render({'value': 3}) == '3.5'
    assert tmpl.render({'value': 'abc'}) == ''
    assert tmpl.render({'value': None}) == ''

    tmpl = Template('{{ value|clamp:"3.5 5.5 7.5" }}')
    assert tmpl.render({'value': 4}) == ''

    tmpl = Template('{{ value|clamp:"a b" }}')
    assert tmpl.render({'value': 4}) == ''


def test_limit_templatetags(pub):
    for v in (3.5, '"3.5"', 'xxx'):
        tmpl = Template('{{ value|limit_low:%s }}' % v)
        assert tmpl.render({'value': 4, 'xxx': 3.5}) == '4'
        assert tmpl.render({'value': 3, 'xxx': 3.5}) == '3.5'
        assert tmpl.render({'value': 'abc', 'xxx': 3.5}) == ''
        assert tmpl.render({'value': None, 'xxx': 3.5}) == ''
        if v == 'xxx':
            assert tmpl.render({'value': 3, 'xxx': 'plop'}) == ''

        tmpl = Template('{{ value|limit_high:%s }}' % v)
        assert tmpl.render({'value': 4, 'xxx': 3.5}) == '3.5'
        assert tmpl.render({'value': 3, 'xxx': 3.5}) == '3'
        assert tmpl.render({'value': 'abc', 'xxx': 3.5}) == ''
        assert tmpl.render({'value': None, 'xxx': 3.5}) == ''
        if v == 'xxx':
            assert tmpl.render({'value': 3, 'xxx': 'plop'}) == ''


def test_token_decimal(pub):
    tokens = [Template('{% token_decimal 4 %}').render() for i in range(100)]
    assert all(len(token) == 4 for token in tokens)
    assert all(token.isdigit() for token in tokens)
    # check randomness, i.e. duplicates are rare
    assert len(set(tokens)) > 70
    t = Template('{% if token1|token_check:token2 %}ok{% endif %}')
    assert t.render({'token1': tokens[0] + ' ', 'token2': tokens[0].lower()}) == 'ok'
    t = Template('{% if "é"|token_check:"è" %}ok{% endif %}')
    assert t.render({'token1': tokens[0] + ' ', 'token2': tokens[0].lower()}) == ''


def test_token_alphanum(pub):
    tokens = [Template('{% token_alphanum 4 %}').render() for i in range(100)]
    assert all(len(token) == 4 for token in tokens)
    assert all(token.upper() == token for token in tokens)
    assert all(token.isalnum() for token in tokens)
    # check randomness, i.e. duplicates are rare
    assert len(set(tokens)) > 90
    # check there are letters and digits
    assert any(set(token) & set(string.ascii_uppercase) for token in tokens)
    assert any(set(token) & set(string.digits) for token in tokens)
    # no look-alike characters
    assert not any(set(token) & set('01IiOo') for token in tokens)
    t = Template('{% if token1|token_check:token2 %}ok{% endif %}')
    assert t.render({'token1': tokens[0] + ' ', 'token2': tokens[0].lower()}) == 'ok'


def test_distance(pub):
    t = Template(
        '{{ "48;2"|distance:"48.1;2.1"|floatformat }}',
    )
    assert t.render() == '13387.2'
    t = Template(
        '{{ coords|distance:"48.1;2.1"|floatformat }}',
    )
    assert t.render({'coords': '48;2'}) == '13387.2'
    t = Template(
        '{{ "48;2"|distance:coords|floatformat }}',
    )
    assert t.render({'coords': '48.1;2.1'}) == '13387.2'
    t = Template(
        '{{ c1|distance:c2|floatformat }}',
    )
    assert t.render({'c1': '48;2', 'c2': '48.1;2.1'}) == '13387.2'
    assert t.render({'c1': {'lat': '48', 'lon': '2'}, 'c2': {'lat': '48.1', 'lng': '2.1'}}) == '13387.2'
    assert t.render({'c1': {'lat': '48', 'lng': '2'}, 'c2': {'lat': '48.1', 'lng': '2.1'}}) == '13387.2'

    class MockFormData:
        formdef = None
        geolocations = {'base': {'lat': 48, 'lon': 2}}

    lazy_formdata = LazyFormData(MockFormData())
    for tpl in ('{{ formdata|distance:coords|floatformat }}', '{{ coords|distance:formdata|floatformat }}'):
        t = Template(
            tpl,
        )
        assert t.render({'formdata': lazy_formdata, 'coords': '48.1;2.1'}) == '13387.2'
        assert t.render({'formdata': lazy_formdata, 'coords': '49.1;3.1'}) == '146821.9'
        assert t.render({'formdata': lazy_formdata, 'coords': 'abc;def'}) == ''
        assert t.render({'formdata': lazy_formdata, 'coords': '42'}) == ''
        assert t.render({'formdata': lazy_formdata, 'coords': ''}) == ''
    MockFormData.geolocations = {}
    for tpl in ('{{ formdata|distance:coords|floatformat }}', '{{ coords|distance:formdata|floatformat }}'):
        t = Template(
            tpl,
        )
        assert t.render({'formdata': lazy_formdata, 'coords': '49.1;3.1'}) == ''


def test_get_filter():
    tmpl = Template('{{ foo|get:"bar" }}')
    assert tmpl.render({'foo': {'bar': 'baz'}}) == 'baz'

    tmpl = Template('{{ foo|get:0 }}')
    assert tmpl.render({'foo': ['bar', 'baz']}) == 'bar'

    tmpl = Template('{{ foo|get:0|default_if_none:"" }}')
    assert tmpl.render({'foo': ''}) == ''
    assert tmpl.render({'foo': None}) == ''
    assert tmpl.render({'foo': 23}) == ''


def test_get_on_lazy_var(pub):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'lazy'
    formdef.fields = [
        fields.StringField(id='0', label='string', varname='foo'),
        fields.StringField(id='1', label='string', varname='bar'),
    ]
    formdef.store()
    formdef.data_class().wipe()
    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.data = {
        '0': 'bar',
        '1': '1',
    }
    formdata.store()

    context = CompatibilityNamesDict(
        {
            'form': LazyFormData(formdata),
            'foo_dict': {'bar': 'baz'},
            'foo_array': ['bar', 'baz'],
        }
    )
    tmpl = Template('{{ foo_dict|get:form_var_foo }}')
    assert tmpl.render(context) == 'baz'

    tmpl = Template('{{ foo_array|get:form_var_bar }}')
    assert tmpl.render(context) == 'baz'


def test_reproj(pub):
    class MockFormData:
        formdef = None
        geolocations = {'base': {'lat': 48, 'lon': 2}}

    lazy_formdata = LazyFormData(MockFormData())
    tmpl = Template('{% with form_geoloc_base|reproj:"EPSG:3946" as c %}{{c.0}}/{{c.1}}{% endwith %}')
    coords = tmpl.render(CompatibilityNamesDict({'form': lazy_formdata})).split('/')
    assert int(float(coords[0])) == 1625337
    assert int(float(coords[1])) == 5422836


def test_phonenumber_fr(pub):
    t = Template('{{ number|phonenumber_fr }}')
    assert t.render({'number': '01 23 45 67 89'}) == '01 23 45 67 89'
    assert t.render({'number': '0 1 23 45 67 89'}) == '01 23 45 67 89'
    assert t.render({'number': '0123456789'}) == '01 23 45 67 89'
    assert t.render({'number': '01.23.45.67.89'}) == '01 23 45 67 89'
    assert t.render({'number': '01 23 45 67 89'}) == '01 23 45 67 89'

    assert t.render({'number': '00 33 1 23 45 67 89'}) == '00 33 1 23 45 67 89'
    assert t.render({'number': '00 33 1 23 45 67 89'}) == '00 33 1 23 45 67 89'
    assert t.render({'number': '+33 1 23 45 67 89'}) == '+33 1 23 45 67 89'
    assert t.render({'number': '+33 (0)1 23 45 67 89'}) == '+33 1 23 45 67 89'

    # drom
    assert t.render({'number': '02 62 11 22 33'}) == '02 62 11 22 33'
    assert t.render({'number': '00 262 11 22 33'}) == '00 262 11 22 33'
    assert t.render({'number': '+262 112233'}) == '+262 11 22 33'

    t = Template('{{ number|phonenumber_fr:"." }}')
    assert t.render({'number': '01 23 45 67 89'}) == '01.23.45.67.89'

    # unknown
    assert t.render({'number': '12 3'}) == '12 3'
    assert t.render({'number': 'bla bla'}) == 'bla bla'
    assert t.render({'number': None}) == 'None'
    t = Template('{{ number|decimal|phonenumber_fr }}')
    assert t.render({'number': '1,33'}) == '1.33'

    # lazy mode
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'lazy'
    formdef.fields = [fields.StringField(id='0', label='string', varname='phone')]
    formdef.store()
    formdata = formdef.data_class()()
    formdata.data = {'0': '0143350135'}
    formdata.store()
    context = CompatibilityNamesDict({'form': LazyFormData(formdata)})
    tmpl = Template('{{ form_var_phone|phonenumber_fr:"-" }}')
    assert tmpl.render(context) == '01-43-35-01-35'


def test_is_french_mobile_phone_number(pub):
    t = Template('{{ number|is_french_mobile_phone_number }}')

    assert t.render({'number': '01 23 45 67 8989'}) == 'False'
    assert t.render({'number': '06 23 45 67 89 89'}) == 'False'
    assert t.render({'number': '06 23 45 89'}) == 'False'
    assert t.render({'number': '0 6 2 3 45 89'}) == 'False'
    assert t.render({'number': '07 23 45 67 89'}) == 'False'  # invalid number

    assert t.render({'number': '06 23 45 67 89'}) == 'True'
    assert t.render({'number': '07 50 55 53 25'}) == 'True'
    assert t.render({'number': '06.23.45.67.89'}) == 'True'
    assert t.render({'number': '0 6 2 3 45 67 89'}) == 'True'

    assert t.render({'number': '+33 6 23 45 67 89'}) == 'True'
    assert t.render({'number': '+262 6 92 11 22 33'}) == 'True'

    t = Template('{% if number|is_french_mobile_phone_number %}ok{% endif %}')
    assert t.render({'number': '06 23 45 67 8989'}) == ''
    assert t.render({'number': '06 23 45 67 89'}) == 'ok'

    with pub.complex_data():
        t = Template('{{ number|is_french_mobile_phone_number }}')
        assert (
            pub.get_cached_complex_data(t.render({'number': '06 23 45 67 8989', 'allow_complex': True}))
            is False
        )
        assert (
            pub.get_cached_complex_data(t.render({'number': '06 23 45 67 89', 'allow_complex': True})) is True
        )

    condition = Condition({'value': '"06 23 45 67 8989"|is_french_mobile_phone_number', 'type': 'django'})
    assert condition.unsafe_evaluate() is False
    condition = Condition({'value': '"06 23 45 67 89"|is_french_mobile_phone_number', 'type': 'django'})
    assert condition.unsafe_evaluate() is True


@pytest.mark.skipif('langdetect is None')
def test_language_detect(pub):
    t = Template('{{ plop|language_detect }}')
    assert t.render({'plop': 'Good morning world'}) == 'en'
    assert t.render({'plop': 'Bonjour tout le monde'}) == 'fr'
    assert t.render({'plop': '2132133'}) == ''


@pytest.mark.parametrize(
    'value, expected',
    [
        (None, False),
        ('', False),
        ('foobar', False),
        (42, False),
        ('1970-06-15T12:01:03', True),
        ('2500-06-15T12:01:02', False),
        ('1970-01-01 02:03', True),
        ('2500-01-01 02:03', False),
        ('01/01/1970 02h03', True),
        ('01/01/2500 02h03', False),
        ('1970-01-01', True),
        ('2500-01-01', False),
        ('01/01/1970', True),
        ('01/01/2500', False),
        (datetime.datetime(1970, 6, 15, 12, 1, 3), True),
        (datetime.datetime(2500, 6, 15, 12, 1, 2), False),
        (datetime.date(1970, 6, 15), True),
        (datetime.date(2500, 6, 15), False),
        (datetime.datetime.now(), True),
        (datetime.datetime.now() + datetime.timedelta(hours=1), False),
        (now(), True),
        (now() + datetime.timedelta(hours=1), False),
        (datetime.date.today(), True),
        (datetime.date.today() + datetime.timedelta(days=1), False),
    ],
)
def test_datetime_in_past(pub, value, expected):
    t = Template('{{ value|datetime_in_past }}')
    assert t.render({'value': value}) == str(expected)


def test_is_working_day_settings(settings, pub):
    settings.WORKING_DAY_CALENDAR = None
    t = Template('{{ value|is_working_day }}')
    assert t.render({'value': '2020-07-15'}) == 'False'
    t = Template('{{ value|is_working_day_with_saturday }}')
    assert t.render({'value': '2020-07-15'}) == 'False'

    settings.WORKING_DAY_CALENDAR = ''
    t = Template('{{ value|is_working_day }}')
    assert t.render({'value': '2020-07-15'}) == 'False'
    t = Template('{{ value|is_working_day_with_saturday }}')
    assert t.render({'value': '2020-07-15'}) == 'False'

    settings.WORKING_DAY_CALENDAR = 'foobar'
    t = Template('{{ value|is_working_day }}')
    assert t.render({'value': '2020-07-15'}) == 'False'
    t = Template('{{ value|is_working_day_with_saturday }}')
    assert t.render({'value': '2020-07-15'}) == 'False'

    settings.WORKING_DAY_CALENDAR = 'workalendar.europe.France'
    t = Template('{{ value|is_working_day }}')
    assert t.render({'value': '2020-07-15'}) == 'True'
    t = Template('{{ value|is_working_day_with_saturday }}')
    assert t.render({'value': '2020-07-15'}) == 'True'

    pub.site_options.set('options', 'working_day_calendar', 'foobar')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)
    t = Template('{{ value|is_working_day }}')
    assert t.render({'value': '2020-07-15'}) == 'False'
    t = Template('{{ value|is_working_day_with_saturday }}')
    assert t.render({'value': '2020-07-15'}) == 'False'

    settings.WORKING_DAY_CALENDAR = 'foobar'
    pub.site_options.set('options', 'working_day_calendar', 'workalendar.europe.France')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)
    t = Template('{{ value|is_working_day }}')
    assert t.render({'value': '2020-07-15'}) == 'True'
    t = Template('{{ value|is_working_day_with_saturday }}')
    assert t.render({'value': '2020-07-15'}) == 'True'


@pytest.mark.parametrize(
    'value, expected',
    [
        (None, False),
        ('', False),
        ('foobar', False),
        (42, False),
        ('2020-07-14T12:01:03', False),
        ('2020-07-15T12:01:03', True),
        ('2020-07-14 02:03', False),
        ('2020-07-15 02:03', True),
        ('14/07/2020 02h03', False),
        ('15/07/2020 02h03', True),
        ('2020-07-14', False),
        ('2020-07-15', True),
        ('14/07/2020', False),
        ('15/07/2020', True),
        (datetime.datetime(2020, 7, 14, 12, 1, 3), False),
        (datetime.datetime(2020, 7, 15, 12, 1, 3), True),
        (datetime.date(2020, 7, 14), False),
        (datetime.date(2020, 7, 15), True),
    ],
)
def test_is_working_day(settings, pub, value, expected):
    settings.WORKING_DAY_CALENDAR = 'workalendar.europe.France'
    t = Template('{{ value|is_working_day }}')
    assert t.render({'value': value}) == str(expected)
    t = Template('{{ value|is_working_day_with_saturday }}')
    assert t.render({'value': value}) == str(expected)


def test_is_working_day_weekend(settings, pub):
    settings.WORKING_DAY_CALENDAR = 'workalendar.europe.France'
    # check saturday
    t = Template('{{ value|is_working_day }}')
    assert t.render({'value': '2020-06-20'}) == 'False'
    t = Template('{{ value|is_working_day_with_saturday }}')
    assert t.render({'value': '2020-06-20'}) == 'True'
    # check sunday
    t = Template('{{ value|is_working_day }}')
    assert t.render({'value': '2020-06-21'}) == 'False'
    t = Template('{{ value|is_working_day_with_saturday }}')
    assert t.render({'value': '2020-06-21'}) == 'False'


def test_add_working_days_settings(settings, pub):
    settings.WORKING_DAY_CALENDAR = None
    t = Template('{{ value|add_working_days:1 }}')
    assert t.render({'value': '2020-07-13'}) == ''
    t = Template('{{ value|add_working_days_with_saturday:1 }}')
    assert t.render({'value': '2020-07-13'}) == ''

    settings.WORKING_DAY_CALENDAR = ''
    t = Template('{{ value|add_working_days:1 }}')
    assert t.render({'value': '2020-07-13'}) == ''
    t = Template('{{ value|add_working_days_with_saturday:1 }}')
    assert t.render({'value': '2020-07-13'}) == ''

    settings.WORKING_DAY_CALENDAR = 'foobar'
    t = Template('{{ value|add_working_days:1 }}')
    assert t.render({'value': '2020-07-13'}) == ''
    t = Template('{{ value|add_working_days_with_saturday:1 }}')
    assert t.render({'value': '2020-07-13'}) == ''

    settings.WORKING_DAY_CALENDAR = 'workalendar.europe.France'
    t = Template('{{ value|add_working_days:1 }}')
    assert t.render({'value': '2020-07-13'}) == '2020-07-15'
    t = Template('{{ value|add_working_days_with_saturday:1 }}')
    assert t.render({'value': '2020-07-13'}) == '2020-07-15'

    pub.site_options.set('options', 'working_day_calendar', 'foobar')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)
    t = Template('{{ value|add_working_days:1 }}')
    assert t.render({'value': '2020-07-13'}) == ''
    t = Template('{{ value|add_working_days_with_saturday:1 }}')
    assert t.render({'value': '2020-07-13'}) == ''

    settings.WORKING_DAY_CALENDAR = 'foobar'
    pub.site_options.set('options', 'working_day_calendar', 'workalendar.europe.France')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)
    t = Template('{{ value|add_working_days:1 }}')
    assert t.render({'value': '2020-07-13'}) == '2020-07-15'
    t = Template('{{ value|add_working_days_with_saturday:1 }}')
    assert t.render({'value': '2020-07-13'}) == '2020-07-15'


def test_add_working_days_arg(settings, pub):
    settings.WORKING_DAY_CALENDAR = 'workalendar.europe.France'
    t = Template('{{ value|add_working_days:"foobar" }}')
    assert t.render({'value': '2020-07-13'}) == ''
    t = Template('{{ value|add_working_days_with_saturday:"foobar" }}')
    assert t.render({'value': '2020-07-13'}) == ''
    t = Template('{{ value|add_working_days:2 }}')
    assert t.render({'value': '2020-07-13'}) == '2020-07-16'
    t = Template('{{ value|add_working_days_with_saturday:2 }}')
    assert t.render({'value': '2020-07-13'}) == '2020-07-16'


@pytest.mark.parametrize(
    'value, expected',
    [
        (None, ''),
        ('', ''),
        ('foobar', ''),
        (42, ''),
        ('2020-07-13T12:01:03', '2020-07-15'),
        ('2020-07-13 02:03', '2020-07-15'),
        ('13/07/2020 02h03', '2020-07-15'),
        ('2020-07-13', '2020-07-15'),
        ('13/07/2020', '2020-07-15'),
        (datetime.datetime(2020, 7, 13, 12, 1, 3), '2020-07-15'),
        (datetime.date(2020, 7, 13), '2020-07-15'),
    ],
)
def test_add_working_days(settings, pub, value, expected):
    settings.WORKING_DAY_CALENDAR = 'workalendar.europe.France'
    t = Template('{{ value|add_working_days:1 }}')
    assert t.render({'value': value}) == str(expected)
    t = Template('{{ value|add_working_days_with_saturday:1 }}')
    assert t.render({'value': value}) == str(expected)


def test_add_working_days_weekend(settings, pub):
    settings.WORKING_DAY_CALENDAR = 'workalendar.europe.France'
    t = Template('{{ value|add_working_days:1 }}')
    assert t.render({'value': '2020-06-19'}) == '2020-06-22'
    t = Template('{{ value|add_working_days_with_saturday:1 }}')
    assert t.render({'value': '2020-06-19'}) == '2020-06-20'


def test_adjust_to_working_day_settings(settings, pub):
    settings.WORKING_DAY_CALENDAR = None
    t = Template('{{ value|adjust_to_working_day }}')
    assert t.render({'value': '2020-07-13'}) == ''
    t = Template('{{ value|adjust_to_working_day_with_saturday }}')
    assert t.render({'value': '2020-07-13'}) == ''

    settings.WORKING_DAY_CALENDAR = ''
    t = Template('{{ value|adjust_to_working_day }}')
    assert t.render({'value': '2020-07-13'}) == ''
    t = Template('{{ value|adjust_to_working_day_with_saturday }}')
    assert t.render({'value': '2020-07-13'}) == ''

    settings.WORKING_DAY_CALENDAR = 'foobar'
    t = Template('{{ value|adjust_to_working_day }}')
    assert t.render({'value': '2020-07-13'}) == ''
    t = Template('{{ value|adjust_to_working_day_with_saturday }}')
    assert t.render({'value': '2020-07-13'}) == ''

    settings.WORKING_DAY_CALENDAR = 'workalendar.europe.France'
    t = Template('{{ value|adjust_to_working_day }}')
    assert t.render({'value': '2020-07-14'}) == '2020-07-15'
    t = Template('{{ value|adjust_to_working_day_with_saturday }}')
    assert t.render({'value': '2020-07-14'}) == '2020-07-15'

    pub.site_options.set('options', 'working_day_calendar', 'foobar')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)
    t = Template('{{ value|adjust_to_working_day }}')
    assert t.render({'value': '2020-07-13'}) == ''
    t = Template('{{ value|adjust_to_working_day_with_saturday }}')
    assert t.render({'value': '2020-07-13'}) == ''

    settings.WORKING_DAY_CALENDAR = 'foobar'
    pub.site_options.set('options', 'working_day_calendar', 'workalendar.europe.France')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)
    t = Template('{{ value|adjust_to_working_day }}')
    assert t.render({'value': '2020-07-14'}) == '2020-07-15'
    t = Template('{{ value|adjust_to_working_day_with_saturday }}')
    assert t.render({'value': '2020-07-14'}) == '2020-07-15'


@pytest.mark.parametrize(
    'value, expected',
    [
        (None, ''),
        ('', ''),
        ('foobar', ''),
        (42, ''),
        ('2020-07-14T12:01:03', '2020-07-15'),
        ('2020-07-14 02:03', '2020-07-15'),
        ('14/07/2020 02h03', '2020-07-15'),
        ('2020-07-14', '2020-07-15'),
        ('14/07/2020', '2020-07-15'),
        (datetime.datetime(2020, 7, 14, 12, 1, 3), '2020-07-15'),
        (datetime.date(2020, 7, 14), '2020-07-15'),
        (datetime.date(2020, 7, 15), '2020-07-15'),
    ],
)
def test_adjust_to_working_day(settings, pub, value, expected):
    settings.WORKING_DAY_CALENDAR = 'workalendar.europe.France'
    t = Template('{{ value|adjust_to_working_day }}')
    assert t.render({'value': value}) == str(expected)
    t = Template('{{ value|adjust_to_working_day_with_saturday }}')
    assert t.render({'value': value}) == str(expected)


def test_adjust_to_working_day_weekend(settings, pub):
    settings.WORKING_DAY_CALENDAR = 'workalendar.europe.France'
    t = Template('{{ value|adjust_to_working_day }}')
    assert t.render({'value': '2020-06-20'}) == '2020-06-22'
    t = Template('{{ value|adjust_to_working_day_with_saturday }}')
    assert t.render({'value': '2020-06-20'}) == '2020-06-20'


def test_age_in_working_days_settings(settings, pub, freezer):
    freezer.move_to('2020-07-01T00:00:00')
    settings.WORKING_DAY_CALENDAR = None
    t = Template('{{ value|age_in_working_days:"2020-07-15" }}')
    assert t.render({'value': '2020-07-12'}) == ''
    t = Template('{{ value|age_in_working_days_with_saturday:"2020-07-15" }}')
    assert t.render({'value': '2020-07-12'}) == ''

    settings.WORKING_DAY_CALENDAR = ''
    t = Template('{{ value|age_in_working_days:"2020-07-15" }}')
    assert t.render({'value': '2020-07-12'}) == ''
    t = Template('{{ value|age_in_working_days_with_saturday:"2020-07-15" }}')
    assert t.render({'value': '2020-07-12'}) == ''

    settings.WORKING_DAY_CALENDAR = 'foobar'
    t = Template('{{ value|age_in_working_days:"2020-07-15" }}')
    assert t.render({'value': '2020-07-12'}) == ''
    t = Template('{{ value|age_in_working_days_with_saturday:"2020-07-15" }}')
    assert t.render({'value': '2020-07-12'}) == ''

    settings.WORKING_DAY_CALENDAR = 'workalendar.europe.France'
    t = Template('{{ value|age_in_working_days:"2020-07-15" }}')
    assert t.render({'value': '2020-07-12'}) == '2'
    t = Template('{{ value|age_in_working_days_with_saturday:"2020-07-15" }}')
    assert t.render({'value': '2020-07-12'}) == '2'
    settings.WORKING_DAY_CALENDAR = 'workalendar.europe.France'
    t = Template('{{ value|age_in_working_days }}')
    assert t.render({'value': '2020-07-12'}) == '-7'
    t = Template('{{ value|age_in_working_days_with_saturday }}')
    assert t.render({'value': '2020-07-12'}) == '-9'

    pub.site_options.set('options', 'working_day_calendar', 'foobar')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)
    t = Template('{{ value|age_in_working_days:"2020-07-15" }}')
    assert t.render({'value': '2020-07-12'}) == ''
    t = Template('{{ value|age_in_working_days_with_saturday:"2020-07-15" }}')
    assert t.render({'value': '2020-07-12'}) == ''

    settings.WORKING_DAY_CALENDAR = 'foobar'
    pub.site_options.set('options', 'working_day_calendar', 'workalendar.europe.France')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)
    t = Template('{{ value|age_in_working_days:"2020-07-15" }}')
    assert t.render({'value': '2020-07-12'}) == '2'
    t = Template('{{ value|age_in_working_days_with_saturday:"2020-07-15" }}')
    assert t.render({'value': '2020-07-12'}) == '2'


def test_age_in_working_days_arg(settings, pub):
    settings.WORKING_DAY_CALENDAR = 'workalendar.europe.France'
    t = Template('{{ value|age_in_working_days:"foobar" }}')
    assert t.render({'value': '2020-07-12'}) == ''
    t = Template('{{ value|age_in_working_days_with_saturday:"foobar" }}')
    assert t.render({'value': '2020-07-12'}) == ''
    t = Template('{{ value|age_in_working_days:"2020-07-15" }}')
    assert t.render({'value': '2020-07-12'}) == '2'
    t = Template('{{ value|age_in_working_days_with_saturday:"2020-07-15" }}')
    assert t.render({'value': '2020-07-12'}) == '2'


@pytest.mark.parametrize(
    'value, expected',
    [
        (None, ''),
        ('', ''),
        ('foobar', ''),
        (42, ''),
        ('2020-07-14T12:01:03', '2'),
        ('2020-07-14 02:03', '2'),
        ('14/07/2020 02h03', '2'),
        ('2020-07-14', '2'),
        ('14/07/2020', '2'),
        (datetime.datetime(2020, 7, 14, 12, 1, 3), '2'),
        (datetime.date(2020, 7, 14), '2'),
        (datetime.date(2020, 7, 15), '1'),
    ],
)
def test_age_in_working_days(settings, pub, value, expected):
    settings.WORKING_DAY_CALENDAR = 'workalendar.europe.France'
    t = Template('{{ value|age_in_working_days:"2020-07-16" }}')
    assert t.render({'value': value}) == str(expected)
    t = Template('{{ value|age_in_working_days_with_saturday:"2020-07-16" }}')
    assert t.render({'value': value}) == str(expected)


def test_age_in_working_days_weekend(settings, pub):
    settings.WORKING_DAY_CALENDAR = 'workalendar.europe.France'
    t = Template('{{ value|age_in_working_days:"2020-06-22" }}')
    assert t.render({'value': '2020-06-19'}) == '1'
    t = Template('{{ value|age_in_working_days_with_saturday:"2020-06-22" }}')
    assert t.render({'value': '2020-06-19'}) == '2'


def test_sum(pub):
    tmpl = Template('{{ "2 29.5 9,5 .5"|split|sum }}')
    assert tmpl.render({}) == '41.5'
    tmpl = Template('{{ list|sum }}')
    assert tmpl.render({'list': [1, 2, '3']}) == '6'
    assert tmpl.render({'list': [1, 2, 'x']}) == '3'
    assert tmpl.render({'list': [None, 2.0, 'x']}) == '2'
    assert tmpl.render({'list': []}) == '0'
    assert tmpl.render({'list': None}) == ''  # list is not iterable
    assert tmpl.render({'list': '123'}) == ''  # consider string as not iterable
    assert tmpl.render({}) == ''


def test_getlist(pub):
    class FakeBlock:
        def getlist(self, key):
            return {'foo': ['foo1', 'foo2'], 'bar': ['bar1', 'bar2']}[key]

    tmpl = Template('{% for x in egg|getlist:coin %}{{x}}{% endfor %}')
    assert tmpl.render({'egg': FakeBlock(), 'coin': 'foo'}) == 'foo1foo2'
    assert tmpl.render({'egg': FakeBlock(), 'coin': 'bar'}) == 'bar1bar2'
    tmpl = Template('{{ egg|getlist:"foo"|length }}')
    assert tmpl.render({'egg': FakeBlock()}) == '2'
    assert tmpl.render({}) == '0'
    LoggedError.wipe()
    assert tmpl.render({'egg': None}) == '0'
    assert LoggedError.count() == 0
    for invalid_value in ('spam', 42):
        LoggedError.wipe()
        assert tmpl.render({'egg': invalid_value}) == '0'
        assert LoggedError.count() == 1
        assert LoggedError.select()[0].summary == '|getlist on unsupported value'


def test_getlist_file_digest(pub):
    FormDef.wipe()

    upload = PicklableUpload('test.txt', 'text/plain', 'ascii')
    upload.receive([b'test'])
    upload2 = PicklableUpload('test.txt', 'text/plain', 'ascii')
    upload2.receive([b'test2'])

    formdef = FormDef()
    formdef.name = 'lazy'
    formdef.fields = [
        fields.FileField(id='0', label='file', varname='foo'),
        fields.FileField(id='1', label='file', varname='bar'),
    ]
    formdef.store()
    formdata = formdef.data_class()()
    formdata.data = {'0': upload, '1': upload2}
    formdata.store()
    formdata.just_created()
    formdata.evolution[-1].parts = [
        AttachmentEvolutionPart(
            'hello.txt', fp=io.BytesIO(b'test'), content_type='text/plain', varname='testfile'
        )
    ]
    formdata.store()

    context = CompatibilityNamesDict({'form': LazyFormData(formdata)})
    tmpl = Template(
        '{{ form_var_foo|list|add:form_var_bar|add:form_attachments_testfile|getlist:"file_digest"|safe }}'
    )
    assert (
        tmpl.render(context) == "['9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08', "
        "'60303ae22b998861bce3b28f33eec1be758a213c86c93c076dbe9f558c11c752', "
        "'9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08']"
    )


def test_getlistdict(pub):
    class FakeBlock:
        def getlistdict(self, keys):
            data = [
                {'foo': 'foo1', 'bar': 'bar1'},
                {'foo': 'foo2', 'bar': 'bar2'},
            ]
            return [{k: v for k, v in d.items() if k in keys} for d in data]

    tmpl = Template('{{ egg|getlistdict:coin }}', autoescape=False)
    assert tmpl.render({'egg': FakeBlock(), 'coin': 'foo'}) == "[{'foo': 'foo1'}, {'foo': 'foo2'}]"
    assert (
        tmpl.render({'egg': FakeBlock(), 'coin': 'bar : test, foo:hop, ,,'})
        == "[{'hop': 'foo1', 'test': 'bar1'}, {'hop': 'foo2', 'test': 'bar2'}]"
    )
    tmpl = Template('{{ egg|getlistdict:"foo"|length }}')
    assert tmpl.render({'egg': FakeBlock()}) == '2'
    assert tmpl.render({}) == '0'
    assert tmpl.render({'egg': None}) == '0'
    assert tmpl.render({'egg': 'spam'}) == '0'
    assert tmpl.render({'egg': 42}) == '0'


def test_getlistdict_regroup_as_dict(pub):
    class FakeBlock:
        def getlistdict(self, keys):
            data = [
                {'foo': 'foo1', 'bar': 'bar1'},
                {'foo': 'foo2', 'bar': 'bar2'},
            ]
            return [{k: v for k, v in d.items() if k in keys} for d in data]

    tmpl = Template('{{ egg|getlistdict:"foo,bar"|regroup_as_dict:"foo" }}', autoescape=False)
    assert tmpl.render({'egg': FakeBlock()}) == "{'foo1': {'bar': 'bar1'}, 'foo2': {'bar': 'bar2'}}"


def test_get_table_column(pub):
    tmpl = Template('{{ table|get_table_column:2|sum }}')
    assert tmpl.render({'table': [[1, 2], [3, 4]]}) == '6'

    tmpl = Template('{{ table|get_table_column:5|sum }}')
    assert tmpl.render({'table': [[1, 2], [3, 4]]}) == '0'

    tmpl = Template('{{ table|get_table_column:2|sum }}')
    assert tmpl.render({'table': [[1, '1.5'], [3, '4.5'], [5, 'x'], ['7', None]]}) == '6.0'

    LoggedError.wipe()
    tmpl = Template('{{ table|get_table_column:2|sum }}')
    assert tmpl.render({'table': 'test'}) == '0'
    assert LoggedError.count() == 1
    logged_error = LoggedError.select()[0]
    assert logged_error.summary == '|get_table_column on invalid value'

    LoggedError.wipe()
    tmpl = Template('{{ table|get_table_column:table|sum }}')
    assert tmpl.render({'table': [[1, 2], [3, 4]]}) == '0'
    assert LoggedError.count() == 1
    logged_error = LoggedError.select()[0]
    assert logged_error.summary == '|get_table_column with invalid column number'

    LoggedError.wipe()
    tmpl = Template('{{ table|get_table_column:0|sum }}')
    assert tmpl.render({'table': [[1, 2], [3, 4]]}) == '0'
    assert LoggedError.count() == 1
    logged_error = LoggedError.select()[0]
    assert logged_error.summary == '|get_table_column with invalid column number'


def test_get_table_row(pub):
    tmpl = Template('{{ table|get_table_row:2|sum }}')
    assert tmpl.render({'table': [[1, 2], [3, 4]]}) == '7'

    tmpl = Template('{{ table|get_table_row:5|sum }}')
    assert tmpl.render({'table': [[1, 2], [3, 4]]}) == '0'

    tmpl = Template('{{ table|get_table_row:2|sum }}')
    assert tmpl.render({'table': [[1, '1.5'], [3, '4.5'], [5, 'x'], ['7', None]]}) == '7.5'

    LoggedError.wipe()
    tmpl = Template('{{ table|get_table_row:2|sum }}')
    assert tmpl.render({'table': 'test'}) == '0'
    assert LoggedError.count() == 1
    logged_error = LoggedError.select()[0]
    assert logged_error.summary == '|get_table_row on invalid value'

    LoggedError.wipe()
    tmpl = Template('{{ table|get_table_row:table|sum }}')
    assert tmpl.render({'table': [[1, 2], [3, 4]]}) == '0'
    assert LoggedError.count() == 1
    logged_error = LoggedError.select()[0]
    assert logged_error.summary == '|get_table_row with invalid row number'

    LoggedError.wipe()
    tmpl = Template('{{ table|get_table_row:0|sum }}')
    assert tmpl.render({'table': [[1, 2], [3, 4]]}) == '0'
    assert LoggedError.count() == 1
    logged_error = LoggedError.select()[0]
    assert logged_error.summary == '|get_table_row with invalid row number'


def test_django_contrib_humanize_filters(pub):
    tmpl = Template('{{ foo|intcomma }}')
    assert tmpl.render({'foo': 10000}) == '10,000'
    assert tmpl.render({'foo': '10000'}) == '10,000'
    with override_settings(LANGUAGE_CODE='fr-fr'):
        assert tmpl.render({'foo': 10000}) == '10 000'
        assert tmpl.render({'foo': '10000'}) == '10 000'


def test_is_empty(pub):
    tmpl = Template('{{ foo|is_empty }}')
    assert tmpl.render({}) == 'True'
    assert tmpl.render({'foo': ''}) == 'True'
    assert tmpl.render({'foo': None}) == 'True'
    assert tmpl.render({'foo': 'foo'}) == 'False'
    assert tmpl.render({'foo': 42}) == 'False'
    assert tmpl.render({'foo': []}) == 'True'
    assert tmpl.render({'foo': ['foo']}) == 'False'
    assert tmpl.render({'foo': {}}) == 'True'
    assert tmpl.render({'foo': {'foo': 42}}) == 'False'


def test_first(pub):
    class MockFormData:
        formdef = None

    lazy_formdata = LazyFormData(MockFormData())

    tmpl = Template('{{ foo|first }}')
    assert tmpl.render({'foo': ['foo']}) == 'foo'
    assert tmpl.render({'foo': 'foo'}) == 'f'
    assert tmpl.render({'foo': ''}) == ''
    assert tmpl.render({'foo': None}) == ''
    assert tmpl.render({'foo': lazy_formdata}) == ''
    assert tmpl.render({'foo': {'bar': 'baz'}}) == ''


def test_last(pub):
    class MockFormData:
        formdef = None

    lazy_formdata = LazyFormData(MockFormData())

    tmpl = Template('{{ foo|last }}')
    assert tmpl.render({'foo': ['foo']}) == 'foo'
    assert tmpl.render({'foo': 'foo'}) == 'o'
    assert tmpl.render({'foo': ''}) == ''
    assert tmpl.render({'foo': None}) == ''
    assert tmpl.render({'foo': lazy_formdata}) == ''
    assert tmpl.render({'foo': {'bar': 'baz'}}) == ''


def test_random(pub):
    class MockFormData:
        formdef = None

    lazy_formdata = LazyFormData(MockFormData())

    tmpl = Template('{{ foo|random }}')
    assert tmpl.render({'foo': ['foo']}) == 'foo'
    assert tmpl.render({'foo': 'foo'}) in 'fo'
    assert tmpl.render({'foo': ''}) == ''
    assert tmpl.render({'foo': None}) == ''
    assert tmpl.render({'foo': lazy_formdata}) == ''
    assert tmpl.render({'foo': {'bar': 'baz'}}) == ''


def test_templatetag_repeat(pub):
    context = pub.substitutions.get_context_variables()
    context.update({'l': ['a', 'b'], 'd': {'a': 1, 2: 'b'}, 't': (1, 2, 'a')})
    assert Template('{{ "x"|repeat:5 }}').render(context) == 'xxxxx'
    assert Template('{{ "x"|repeat:5.0 }}').render(context) == 'xxxxx'
    assert Template('{{ "aBc"|repeat:3 }}').render(context) == 'aBcaBcaBc'
    assert Template('{{ "ab"|repeat:"3" }}').render(context) == 'ababab'
    assert Template('{{ "ab"|repeat:"3.0" }}').render(context) == 'ababab'
    assert Template('{{ 42|repeat:2 }}').render(context) == '4242'
    assert html.unescape(Template('{{ l|repeat:2 }}').render(context)) == "['a', 'b', 'a', 'b']"
    assert html.unescape(Template('{{ t|repeat:2 }}').render(context)) == "(1, 2, 'a', 1, 2, 'a')"

    assert Template('{{ "x"|repeat:0 }}').render(context) == ''
    assert Template('{{ "x"|repeat:True }}').render(context) == ''

    assert Template('{{ False|repeat:2 }}').render(context) == ''
    logged_error = LoggedError.select(order_by='id')[-1]
    assert logged_error.summary == 'Cannot repeat something that is not a string or a list (False)'
    assert Template('{{ True|repeat:2 }}').render(context) == ''
    logged_error = LoggedError.select(order_by='id')[-1]
    assert logged_error.summary == 'Cannot repeat something that is not a string or a list (True)'
    assert Template('{{ true|repeat:2 }}').render(context) == ''
    logged_error = LoggedError.select(order_by='id')[-1]
    assert logged_error.summary == 'Cannot repeat something that is not a string or a list (True)'
    assert html.unescape(Template('{{ d|repeat:2 }}').render(context)) == ''
    logged_error = LoggedError.select(order_by='id')[-1]
    assert logged_error.summary == "Cannot repeat something that is not a string or a list ({'a': 1, 2: 'b'})"
    assert Template('{{ None|repeat:2 }}').render(context) == ''
    logged_error = LoggedError.select(order_by='id')[-1]
    assert logged_error.summary == 'Cannot repeat something that is not a string or a list (None)'

    assert Template('{{ "x"|repeat:-2 }}').render(context) == ''
    logged_error = LoggedError.select(order_by='id')[-1]
    assert logged_error.summary == 'Repetition count (-2) is negative'
    assert Template('{{ "x"|repeat:2.99 }}').render(context) == ''
    logged_error = LoggedError.select(order_by='id')[-1]
    assert logged_error.summary == 'Repetition count (2.99) have non-zero decimal part'
    assert Template('{{ "x"|repeat:"4.2" }}').render(context) == ''
    logged_error = LoggedError.select(order_by='id')[-1]
    assert logged_error.summary == 'Repetition count (4.2) have non-zero decimal part'
    assert Template('{{ "x"|repeat:"a" }}').render(context) == ''
    logged_error = LoggedError.select(order_by='id')[-1]
    assert logged_error.summary == "Repetition count 'a' is not a number"
    assert Template('{{ "x"|repeat:l }}').render(context) == ''
    logged_error = LoggedError.select(order_by='id')[-1]
    assert logged_error.summary == "Repetition count ['a', 'b'] is not a number"


def test_convert_as_list(pub):
    tmpl = Template('{{ foo|list|first }}')
    assert tmpl.render({'foo': ['foo']}) == 'foo'

    def list_generator():
        yield from range(5)

    assert tmpl.render({'foo': list_generator}) == '0'

    def list_range():
        return range(5)

    assert tmpl.render({'foo': list_range}) == '0'


def test_convert_as_list_with_add(pub):
    tmpl = Template('{{ foo|list|add:bar|join:", " }}')
    assert tmpl.render({'foo': [1, 2], 'bar': ['a', 'b']}) == '1, 2, a, b'
    assert tmpl.render({'foo': [1, 2], 'bar': 'ab'}) == '1, 2, ab'
    assert tmpl.render({'foo': 12, 'bar': ['a', 'b']}) == '12, a, b'
    assert tmpl.render({'foo': 12, 'bar': 'ab'}) == '12, ab'
    assert html.unescape(tmpl.render({'foo': [1, 2], 'bar': {'a': 'b'}})) == "1, 2, {'a': 'b'}"
    assert html.unescape(tmpl.render({'foo': {'a': 'b'}, 'bar': ['a', 'b']})) == "{'a': 'b'}, a, b"
    tmpl = Template('{{ foo|list|add:bar|join:", " }}')
    assert tmpl.render({'foo': [1, 2], 'bar': ['a', 'b']}) == '1, 2, a, b'
    assert tmpl.render({'foo': [1, 2], 'bar': None}) == '1, 2'


def test_adjust_to_week_monday(pub):
    t = Template('{{ value|adjust_to_week_monday }}')
    assert t.render({'value': '2021-06-13'}) == '2021-06-07'
    t = Template('{{ value|adjust_to_week_monday }}')
    assert t.render({'value': '2021-06-14'}) == '2021-06-14'
    t = Template('{{ value|adjust_to_week_monday }}')
    assert t.render({'value': datetime.datetime(2021, 6, 14, 0, 0)}) == '2021-06-14'


def test_convert_as_set(pub):
    tmpl = Template('{{ foo|set|join:","}}')

    def render(value):
        return set(tmpl.render({'foo': value}).split(','))

    assert render(['foo', 'foo', 'bar']) == {'foo', 'bar'}

    def list_generator():
        yield from range(5)

    assert render(list_generator) == set(map(str, range(5)))

    def list_range():
        return range(5)

    assert render(list_range) == set(map(str, range(5)))


def test_iterate_days_until(pub):
    t = Template(
        '{% for day in value|iterate_days_until:value2 %}{{ day }}{% if not forloop.last %}, {% endif %}{% endfor %}'
    )
    assert (
        t.render({'value': '2021-06-13', 'value2': '2021-06-16'})
        == '2021-06-13, 2021-06-14, 2021-06-15, 2021-06-16'
    )

    assert t.render({'value': 'error1', 'value2': 'error2'}) == ''


def test_qrcode(pub):
    with pub.complex_data():
        img = Template('{{ url|qrcode }}').render({'url': 'http://example.com/', 'allow_complex': True})
        assert pub.has_cached_complex_data(img)
        value = pub.get_cached_complex_data(img)
        assert value.orig_filename == 'qrcode.png'
        assert value.content_type == 'image/png'
        with value.get_file_pointer() as fp:
            img = PIL.Image.open(fp)
            assert img.size == (330, 330)
            assert (
                zbar_decode_qrcode(img, symbols=[ZBarSymbol.QRCODE])[0].data.decode() == 'http://example.com/'
            )

        img = Template('{{ url|qrcode:"qrcode2.png" }}').render(
            {'url': 'http://example.com/', 'allow_complex': True}
        )
        value = pub.get_cached_complex_data(img)
        assert value.orig_filename == 'qrcode2.png'

        img = Template('{{ url|qrcode }}').render({'url': 1, 'allow_complex': True})
        assert img == ''

        FormDef.wipe()
        formdef = FormDef()
        formdef.name = 'lazy'
        formdef.fields = [fields.StringField(id='0', label='string', varname='foo')]
        formdef.store()
        formdata = formdef.data_class()()
        formdata.data = {'0': 'http://example.com/'}
        formdata.store()

        img = Template('{{ url|qrcode }}').render({'url': 'http://example.com/', 'allow_complex': True})
        value = pub.get_cached_complex_data(img)
        assert value.orig_filename == 'qrcode.png'
        assert value.content_type == 'image/png'


def test_complex_with_spaces(pub):
    # create an object with its string value starting with a space
    class ComplexObject:
        def __init__(self, i=0):
            self.i = i

        def __str__(self):
            return ' test %s' % self.i

        def plop(self):
            return ComplexObject(i=self.i + 1)

    with pub.complex_data():
        img = Template('{{ o.plop }}').render({'o': ComplexObject(), 'allow_complex': True})
        value = pub.get_cached_complex_data(img)
        # value should be a complex object
        assert value.i == 1


def test_site_options_booleans(pub):
    if not pub.site_options.has_section('variables'):
        pub.site_options.add_section('variables')
    pub.site_options.set('variables', 'something_true', 'true')
    pub.site_options.set('variables', 'something_false', 'false')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)

    pub.load_site_options()

    context = pub.substitutions.get_context_variables()
    assert Template('{{ something_true }}').render(context) == 'true'
    assert Template('{% if something_true %}hello{% endif %}').render(context) == 'hello'
    assert Template('{% if something_true == "true" %}hello{% endif %}').render(context) == 'hello'
    assert Template('{% if something_true == True %}hello{% endif %}').render(context) == 'hello'
    assert Template('{% if something_true == "false" %}hello{% endif %}').render(context) == ''
    assert Template('{% if something_true == False %}hello{% endif %}').render(context) == ''

    assert Template('{{ something_false }}').render(context) == 'false'
    assert Template('{% if something_false %}hello{% endif %}').render(context) == ''
    assert Template('{% if something_false == False %}hello{% endif %}').render(context) == 'hello'
    assert Template('{% if something_false == "false" %}hello{% endif %}').render(context) == 'hello'
    assert Template('{% if something_false == True %}hello{% endif %}').render(context) == ''
    assert Template('{% if something_false == "true" %}hello{% endif %}').render(context) == ''


def test_site_options_json(pub):
    if not pub.site_options.has_section('variables'):
        pub.site_options.add_section('variables')
    pub.site_options.set('variables', 'a_dict__json', json.dumps({'a': 1, 'b': 2}))
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)

    pub.load_site_options()

    context = pub.substitutions.get_context_variables()
    assert Template('{{ a_dict__json|get:"a" }}').render(context) == '1'


def test_newline(pub):
    context = pub.substitutions.get_context_variables()
    assert Template('a{% newline %}b').render(context) == 'a\nb'
    assert Template('a{% newline windows=True %}b').render(context) == 'a\r\nb'


def test_duration(pub):
    pub.ngettext = translation.ngettext

    context = {'value': 80}
    assert Template('{{ value|duration }}').render(context) == '1h20'
    assert Template('{{ value|duration:"long" }}').render(context) == '1 hour and 20 minutes'

    context = {'value': 40}
    assert Template('{{ value|duration }}').render(context) == '40min'
    assert Template('{{ value|duration:"long" }}').render(context) == '40 minutes'

    context = {'value': 120}
    assert Template('{{ value|duration }}').render(context) == '2h'
    assert Template('{{ value|duration:"long" }}').render(context) == '2 hours'

    context = {'value': 1510}
    assert Template('{{ value|duration }}').render(context) == '1 day and 1h10'
    assert Template('{{ value|duration:"long" }}').render(context) == '1 day, 1 hour and 10 minutes'

    context = {'value': 61}
    assert Template('{{ value|duration }}').render(context) == '1h01'

    context = {'value': 'xx'}
    assert Template('{{ value|duration }}').render(context) == ''
    assert Template('{{ value|duration:"long" }}').render(context) == ''


def test_null_true_false(pub):
    for mode in (None, 'lazy'):
        context = pub.substitutions.get_context_variables(mode=mode)
        tmpl = Template('{{ null }}')
        assert tmpl.render(context) == 'None'
        assert tmpl.render({'null': 'bar'}) == 'bar'
        tmpl = Template('{% if foo is null %}foo is None{% endif %}')
        assert tmpl.render(context) == 'foo is None'
        assert tmpl.render({'foo': None}) == 'foo is None'
        assert tmpl.render({'foo': 42}) == ''
        tmpl = Template('{{ true }} {{ false }}')
        assert tmpl.render(context) == 'True False'
        tmpl = Template('{% if true %}OK{% endif %} {% if not false %}OK{% endif %}')
        assert tmpl.render(context) == 'OK OK'

        context['allow_complex'] = True
        with pub.complex_data():
            img = Template('{{ null }}').render(context)
            assert pub.has_cached_complex_data(img)
            value = pub.get_cached_complex_data(img)
            assert value is None
            img = Template('{{ true }}').render(context)
            assert pub.has_cached_complex_data(img)
            value = pub.get_cached_complex_data(img)
            assert value is True
            img = Template('{{ false }}').render(context)
            assert pub.has_cached_complex_data(img)
            value = pub.get_cached_complex_data(img)
            assert value is False


def test_as_template(pub):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'foobar'
    formdef.fields = [fields.StringField(id='1', label='Test', varname='foo')]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {'1': 'Foo Bar'}
    formdata.store()
    pub.substitutions.feed(formdata)
    context = {'template': 'Hello {{ form_var_foo }}'}
    assert Template('{{ template }}').render(context) == 'Hello {{ form_var_foo }}'
    assert Template('{{ template|as_template }}').render(context) == 'Hello Foo Bar'


def test_stripsometags(pub):
    context = {
        'value': (
            '<h1>title 1</h1>'
            '<script>my-script</script><p style="text-align:center"><em>foo</em></p><a href="link" other-attr="foobar">link</a><br />'
            '<strong>strong</strong>'
            '<ul><li>li 1</li><li>li 2</li></ul>'
        )
    }

    assert Template('{{ value|stripsometags }}').render(context) == 'title 1my-scriptfoolinkstrongli 1li 2'
    assert (
        Template('{{ value|stripsometags:"p,br" }}').render(context)
        == 'title 1my-script<p>foo</p>link<br />strongli 1li 2'
    )
    assert (
        Template('{{ value|stripsometags:"strong,em" }}').render(context)
        == 'title 1my-script<em>foo</em>link<strong>strong</strong>li 1li 2'
    )
    assert Template('{{ value|stripsometags:"p,br,h1,ul,li" }}').render(context) == (
        '<h1>title 1</h1>my-script<p>foo</p>link<br />strong<ul><li>li 1</li><li>li 2</li></ul>'
    )


def test_intcomma(pub):
    context = {'value': '20345.20'}
    assert Template('{{ value|intcomma }}').render(context) == '20,345.2'
    with override_settings(LANGUAGE_CODE='fr-fr'):
        assert Template('{{ value|intcomma }}').render(context) == '20 345,2'


def test_json_dumps(pub):
    context = {'value': [{'id': '1', 'text': 'un'}, {'id': '2', 'text': 'deux'}]}
    assert (
        Template('{{ value|json_dumps }}').render(context)
        == '[{"id": "1", "text": "un"}, {"id": "2", "text": "deux"}]'
    )


def test_make_public_url(pub):
    # empty value
    context = {'value': None}
    assert Template('{% make_public_url url=value %}').render(context) == ''

    # lazy value
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'lazy'
    formdef.fields = [fields.StringField(id='0', label='string', varname='foo')]
    formdef.store()
    formdata = formdef.data_class()()
    formdata.data = {'0': 'https://example.net'}
    formdata.store()
    context = CompatibilityNamesDict({'form': LazyFormData(formdata)})
    assert (
        Template('{% make_public_url url=form_var_foo %}').render(context).startswith('/api/sign-url-token/')
    )


def test_with_auth(pub):
    context = {'service_url': 'https://www.example.net/api/whatever?x=y'}
    assert (
        Template('{{ service_url|with_auth:"username:password" }}').render(context)
        == 'https://username:password@www.example.net/api/whatever?x=y'
    )

    context = {'service_url': 'https://a:b@www.example.net/api/whatever?x=y'}
    assert (
        Template('{{ service_url|with_auth:"username:password" }}').render(context)
        == 'https://username:password@www.example.net/api/whatever?x=y'
    )

    # lazy mode
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'lazy'
    formdef.fields = [fields.StringField(id='0', label='string', varname='foo')]
    formdef.store()
    formdata = formdef.data_class()()
    formdata.data = {'0': 'https://www.example.net/api/whatever?x=y'}
    formdata.store()
    context = CompatibilityNamesDict({'form': LazyFormData(formdata)})
    tmpl = Template('{{ form_var_foo|with_auth:"username:password" }}')
    assert tmpl.render(context) == 'https://username:password@www.example.net/api/whatever?x=y'
    formdata.data = {'0': None}
    formdata.store()
    assert tmpl.render(context) == ''


def test_check_no_duplicates(pub):
    LoggedError.wipe()
    context = {'value1': ['a', 'b', 'c'], 'value2': ['a', 'a', 'b', 'c'], 'value3': None, 'value4': '12'}
    assert Template('{% if value1|check_no_duplicates %}ok{% else %}nok{% endif %}').render(context) == 'ok'
    assert Template('{% if value2|check_no_duplicates %}ok{% else %}nok{% endif %}').render(context) == 'nok'
    assert Template('{% if value3|check_no_duplicates %}ok{% else %}nok{% endif %}').render(context) == 'ok'
    assert LoggedError.count() == 0
    assert Template('{% if value4|check_no_duplicates %}ok{% else %}nok{% endif %}').render(context) == 'nok'
    assert LoggedError.count() == 1
    logged_error = LoggedError.select()[0]
    assert logged_error.summary == '|check_no_duplicates not used on a list (12)'


def test_details_format(pub):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'foo-details'
    formdef.fields = [fields.StringField(id='1', label='String')]
    formdef.store()
    formdef.data_class().wipe()
    formdata = formdef.data_class()()
    formdata.data = {'1': 'foo'}
    formdata.just_created()
    formdata.store()

    pub.substitutions.feed(formdata)
    context = pub.substitutions.get_context_variables(mode='lazy')
    tmpl = Template('{{ form_details|details_format }}')
    LoggedError.wipe()
    assert tmpl.render(context) == ''
    logged_error = LoggedError.select()[0]
    assert logged_error.summary == '|details_format called without specifying a format'

    tmpl = Template('{{ form_details|details_format:"xxx" }}')
    LoggedError.wipe()
    assert tmpl.render(context) == ''
    logged_error = LoggedError.select()[0]
    assert logged_error.summary == '|details_format called with unknown format (xxx)'

    tmpl = Template('{{ form_details|details_format:"text" }}')
    LoggedError.wipe()
    assert tmpl.render(context) == 'String:\n  foo'
    assert LoggedError.count() == 0


@pytest.mark.parametrize('image_format', ['jpeg', 'png', 'pdf'])
def test_convert_image_format(pub, image_format):
    with pub.complex_data():
        img = Template('{{ url|qrcode|convert_image_format:"%s" }}' % image_format).render(
            {'url': 'http://example.com/', 'allow_complex': True}
        )
        assert pub.has_cached_complex_data(img)
        value = pub.get_cached_complex_data(img)
        assert value.orig_filename == 'qrcode.%s' % image_format
        assert value.content_type == {'jpeg': 'image/jpeg', 'png': 'image/png', 'pdf': 'application/pdf'}.get(
            image_format
        )
        with value.get_file_pointer() as fp:
            if image_format in ('jpeg', 'png'):
                img = PIL.Image.open(fp)
                assert img.format == image_format.upper()
                assert img.size == (330, 330)
                assert (
                    zbar_decode_qrcode(img, symbols=[ZBarSymbol.QRCODE])[0].data.decode()
                    == 'http://example.com/'
                )
            else:
                assert b'%PDF-' in fp.read()[:200]


def test_convert_image_format_no_name(pub):
    upload = PicklableUpload('test.jpeg', 'image/jpeg')
    with open(os.path.join(os.path.dirname(__file__), 'image-with-gps-data.jpeg'), 'rb') as jpg:
        upload.receive([jpg.read()])
    upload.base_filename = None
    upload.orig_filename = None

    formdef = FormDef()
    formdef.name = 'lazy'
    formdef.fields = [
        fields.FileField(id='0', label='file', varname='foo'),
    ]
    formdef.store()
    formdata = formdef.data_class()()
    formdata.data = {'0': upload}
    formdata.store()
    formdata.just_created()
    pub.substitutions.feed(formdata)

    context = pub.substitutions.get_context_variables(mode='lazy')
    context['allow_complex'] = True
    with pub.complex_data():
        img = Template('{{ form_var_foo|convert_image_format:"jpeg" }}').render(context)
        assert pub.has_cached_complex_data(img)
        value = pub.get_cached_complex_data(img)
        assert value.orig_filename == 'file.jpeg'


def test_convert_image_format_errors(pub):
    LoggedError.wipe()
    with pub.complex_data():
        img = Template('{{ "xxx"|convert_image_format:"gif" }}').render({'allow_complex': True})
        assert pub.has_cached_complex_data(img)
        assert pub.get_cached_complex_data(img) is None
    assert LoggedError.count() == 1
    assert (
        LoggedError.select()[0].summary
        == '|convert_image_format: unknown format (must be one of jpeg, pdf, png)'
    )

    LoggedError.wipe()
    with pub.complex_data():
        img = Template('{{ "xxx"|convert_image_format:"jpeg" }}').render({'allow_complex': True})
        assert pub.has_cached_complex_data(img)
        assert pub.get_cached_complex_data(img) is None
    assert LoggedError.count() == 1
    assert LoggedError.select()[0].summary == '|convert_image_format: missing input'

    LoggedError.wipe()
    with mock.patch('subprocess.run', side_effect=FileNotFoundError()):
        with pub.complex_data():
            img = Template('{{ url|qrcode|convert_image_format:"jpeg" }}').render(
                {'url': 'http://example.com/', 'allow_complex': True}
            )
            assert pub.has_cached_complex_data(img)
            assert pub.get_cached_complex_data(img) is None
    assert LoggedError.count() == 1
    assert LoggedError.select()[0].summary == '|convert_image_format: not supported'

    LoggedError.wipe()
    with mock.patch(
        'subprocess.run', side_effect=subprocess.CalledProcessError(returncode=-1, cmd='xx', stderr=b'xxx')
    ):
        with pub.complex_data():
            img = Template('{{ url|qrcode|convert_image_format:"jpeg" }}').render(
                {'url': 'http://example.com/', 'allow_complex': True}
            )
            assert pub.has_cached_complex_data(img)
            assert pub.get_cached_complex_data(img) is None
    assert LoggedError.count() == 1
    assert LoggedError.select()[0].summary == '|convert_image_format: conversion error (xxx)'


def test_temporary_access_url(pub):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'foobar'
    formdef.fields = [fields.StringField(id='1', label='Test', varname='foo')]
    formdef.store()

    # no formdata
    context = pub.substitutions.get_context_variables(mode='lazy')
    assert Template('{% temporary_access_url %}').render(context) == ''

    # formdata
    formdata = formdef.data_class()()
    formdata.data = {'1': 'Foo Bar'}
    formdata.store()
    pub.substitutions.feed(formdata)
    context = pub.substitutions.get_context_variables(mode='lazy')
    assert Template('{% temporary_access_url %}').render(context).startswith('http://example.net/code/')

    # removed formdata
    formdata.remove_self()
    assert Template('{% temporary_access_url %}').render(context) == ''


def test_housenumber_templatefilters(pub):
    assert Template('{{ "42"|housenumber_number }}').render() == '42'
    assert Template('{{ "42"|housenumber_btq }}').render() == ''
    assert Template('{{ "42bis"|housenumber_number }}').render() == '42'
    assert Template('{{ "42bis"|housenumber_btq }}').render() == 'bis'
    assert Template('{{ "  42  bis  "|housenumber_number }}').render() == '42'
    assert Template('{{ "  42  bis  "|housenumber_btq }}').render() == 'bis'
    assert Template('{{ "42 3 t "|housenumber_number }}').render() == '42'
    assert Template('{{ "42 3 t "|housenumber_btq }}').render() == '3 t'
    assert Template('{{ " bis "|housenumber_number }}').render() == ''
    assert Template('{{ " bis "|housenumber_btq }}').render() == ''
    assert Template('{{ 42|housenumber_number }}').render() == '42'
    assert Template('{{ 42|housenumber_btq }}').render() == ''
    assert Template('{{ ""|housenumber_number }}').render() == ''
    assert Template('{{ ""|housenumber_btq }}').render() == ''
    assert Template('{{ null|housenumber_number }}').render({'null': None}) == ''
    assert Template('{{ null|housenumber_btq }}').render({'null': None}) == ''


def test_template_no_reverse_match(pub):
    # django syntax error
    with pytest.raises(TemplateError):
        Template('{% url "xxx" %}', raises=True).render()


def test_strip_metadata_filter(pub):
    upload = PicklableUpload('test.jpeg', 'image/jpeg')
    with open(os.path.join(os.path.dirname(__file__), 'image-with-gps-data.jpeg'), 'rb') as fd:
        upload.receive([fd.read()])

    with pub.complex_data():
        tmpl = Template('{{ foo|strip_metadata }}')
        image_file = pub.get_cached_complex_data(tmpl.render({'foo': upload, 'allow_complex': True}))
        image = PIL.Image.open(io.BytesIO(image_file.get_content()))
        assert not image.getexif()

    # check palette of indexed images is kept
    upload = PicklableUpload('test.png', 'image/png')
    rc = subprocess.run(
        [
            'gm',
            'convert',
            os.path.join(os.path.dirname(__file__), 'image-with-gps-data.jpeg'),
            '-colors',
            '235',
            'PNG:-',
        ],
        capture_output=True,
        check=True,
    )
    upload.receive([rc.stdout])

    with pub.complex_data():
        tmpl = Template('{{ foo|strip_metadata }}')
        image_file = pub.get_cached_complex_data(tmpl.render({'foo': upload, 'allow_complex': True}))
        image = PIL.Image.open(io.BytesIO(image_file.get_content()))
        assert image.mode == 'P'
        assert image.getpalette()


def test_zip(pub):
    FormDef.wipe()
    BlockDef.wipe()

    block = BlockDef()
    block.name = 'foobar'
    block.fields = [
        fields.FileField(id='1', required='required', label='Test2', varname='file'),
    ]
    block.store()

    formdef = FormDef()
    formdef.name = 'form title'
    formdef.fields = [
        fields.FileField(id='0', required='required', label='Test2', varname='file'),
        fields.BlockField(id='1', label='test', block_slug='foobar', max_items='3', varname='block'),
        fields.FileField(id='2', required='required', label='Test2', varname='file2'),
        fields.StringField(id='3', label='filename', varname='filename'),
    ]
    formdef.store()
    formdef.data_class().wipe()

    uploads = [
        PicklableUpload('test1.jpeg', 'image/jpeg'),
        PicklableUpload('test2.pdf', 'application/pdf'),
        PicklableUpload('test3.jpeg', 'image/jpeg'),
        PicklableUpload('test4.jpeg', 'application/pdf'),
    ]
    for i in range(4):
        uploads[i].receive([str(i).encode()])

    formdata = formdef.data_class()()
    formdata.data = {
        '0': uploads[0],
        '1': {
            'data': [
                {
                    '1': uploads[1],
                },
                {
                    '1': 'xxx',
                },
                {'1': uploads[2]},
            ]
        },
        '2': uploads[3],
        '3': 'testname.zip',
    }
    LoggedError.wipe()
    lazy_list = LazyList([{'fichier': upload} for upload in uploads], 'fichier')

    context = CompatibilityNamesDict(
        {'form': LazyFormData(formdata), 'allow_complex': True, 'lazy_list': lazy_list}
    )
    with pub.complex_data():
        archive = Template(
            '{% zip "archive.zip"|upper dir1/file1.pdf=form_var_file file2.pdf=form_var_file7 '
            'dir2/piece-jointe.pdf=form_var_block|getlist:"file" dir3/=form_var_file '
            'dir3/noname3=form_var_file =form_var_file2 dir4/=form_var_block|getlist:"file" lazy/lazy.pdf=lazy_list  %}'
        ).render(context)
        assert LoggedError.count() == 0, LoggedError.select()[0].summary
        assert pub.has_cached_complex_data(archive)
        value = pub.get_cached_complex_data(archive)
        assert archive[:-1] == 'ARCHIVE.ZIP'
        assert value.orig_filename == 'ARCHIVE.ZIP'
        assert value.content_type == 'application/x-zip'
        content = {}
        with value.get_file_pointer() as fd:
            with zipfile.ZipFile(fd) as zip_archive:
                for name in zip_archive.namelist():
                    content[name] = zip_archive.read(name)
        assert sorted(content.items()) == [
            # dirname and basename are kept from declaration, extension is fixed
            (
                'dir1/file1.jpg',
                b'0',
            ),
            # file2.pdf is ignored, as form_var_file7 does not exist
            # all non empty file fields from the block instances are used and a
            # counter is appended to the basename
            (
                'dir2/piece-jointe-1.pdf',
                b'1',
            ),
            (
                'dir2/piece-jointe-2.jpg',
                b'2',
            ),
            # missing extension is fixed
            (
                'dir3/noname3.jpg',
                b'0',
            ),
            # as the given path has no basename the original filename is kept
            (
                'dir3/test1.jpg',
                b'0',
            ),
            (
                'dir4/test2.pdf',
                b'1',
            ),
            (
                'dir4/test3.jpg',
                b'2',
            ),
            (
                'lazy/lazy-1.jpg',
                b'0',
            ),
            (
                'lazy/lazy-2.pdf',
                b'1',
            ),
            (
                'lazy/lazy-3.jpg',
                b'2',
            ),
            (
                'lazy/lazy-4.pdf',
                b'3',
            ),
            # original filename was kept but the extension was fixed to match the mime-type
            (
                'test4.pdf',
                b'3',
            ),
        ]
        # check {% .. as foobar %} support
        archive = Template('{% zip "archive.zip"|upper dir1/file1.pdf=form_var_file as foobar %}').render(
            context
        )
        assert not pub.has_cached_complex_data(archive)
        archive = Template(
            '{% zip "archive.zip"|upper dir1/file1.pdf=form_var_file as foobar %}{{ foobar }}'
        ).render(context)
        assert pub.has_cached_complex_data(archive)
        value = pub.get_cached_complex_data(archive)
        assert archive[:-1] == 'ARCHIVE.ZIP'
        assert value.orig_filename == 'ARCHIVE.ZIP'

        # check with lazy variable as filename
        archive = Template(
            '{% zip form_var_filename dir1/file1.pdf=form_var_file as foobar %}{{ foobar }}'
        ).render(context)
        assert pub.has_cached_complex_data(archive)
        value = pub.get_cached_complex_data(archive)
        assert archive[:-1] == 'testname.zip'
        assert value.orig_filename == 'testname.zip'


@pytest.mark.parametrize(
    'template,message',
    [
        ('{% zip %}', '{%% zip %%} missing zip filename'),
        ('{% zip $#$ %}', '{% zip %} invalid zip filename expression "$#$"'),
        ('{% zip "archive.zip" foobar %}', '{% zip %} invalid content descriptor "foobar"'),
        ('{% zip "archive.zip" foobar#pdf=foobar %}', '{% zip %} invalid content filename "foobar#pdf"'),
        ('{% zip "archive.zip" foobar.pdf=foobar# %}', '{% zip %} invalid content expression "foobar#"'),
    ],
    ids=[
        'missing filename',
        'invalid filename expression',
        'invalid content descriptor',
        'invalid content filename',
        'invalid content expression',
    ],
)
def test_zip_error_zip_syntax_error(pub, template, message):
    with pytest.raises(TemplateError, match=re.escape('syntax error in Django template: ' + message)):
        Template(template, raises=True)


@pytest.mark.parametrize(
    'template,message',
    [
        ('{% zip "" %}', '{% zip %} invalid zip filename "" reverting to "archive.zip"'),
        ('{% zip "foo#.zip" %}', '{% zip %} invalid zip filename "foo#.zip" reverting to "archive.zip"'),
    ],
    ids=[
        'empty filename',
        'invalid filename',
    ],
)
def test_zip_error_zip_logged_error(pub, template, message):
    LoggedError.wipe()
    with pub.complex_data():
        archive = Template(template).render({'allow_complex': True})
        assert pub.has_cached_complex_data(archive)
        assert archive[:-1] == 'archive.zip'
    assert LoggedError.count() == 1
    assert LoggedError.select()[0].summary == message


def test_sha256_templatetag(pub):
    tmpl = Template('{{ "foobar"|sha256 }}')
    # hashlib.sha256(b'foobar').hexdigest()
    assert tmpl.render() == 'c3ab8ff13720e8ad9047dd39466b3c8974e592c2fa383d4a3960714caef0c4f2'

    tmpl = Template('{{ "foo"|sha256:"bar" }}')
    # hashlib.sha256(b'foobar').hexdigest()
    assert tmpl.render() == 'c3ab8ff13720e8ad9047dd39466b3c8974e592c2fa383d4a3960714caef0c4f2'
