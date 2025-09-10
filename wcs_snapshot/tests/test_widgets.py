import copy
import datetime
import decimal
import os
from unittest import mock

import mechanize
import pytest
from pyquery import PyQuery
from quixote import cleanup, get_response
from quixote.html import htmltext
from quixote.http_request import parse_query
from schwifty import IBAN

from wcs.fields.base import CssClassesWidget
from wcs.qommon import sessions
from wcs.qommon.form import (
    CheckboxesWidget,
    CompositeWidget,
    ComputedExpressionWidget,
    ConditionWidget,
    DateWidget,
    EmailWidget,
    FileWithPreviewWidget,
    Form,
    MapWidget,
    MiniRichTextWidget,
    NumericWidget,
    OptGroup,
    PasswordEntryWidget,
    RichTextWidget,
    SingleSelectHintWidget,
    SingleSelectWidget,
    SingleSelectWidgetWithOther,
    StringWidget,
    TableListRowsWidget,
    TableWidget,
    TextWidget,
    WcsExtraStringWidget,
    WidgetDict,
    WysiwygTextWidget,
)
from wcs.qommon.http_request import HTTPRequest
from wcs.wf.profile import ProfileUpdateRowWidget

from .utilities import clean_temporary_pub, create_temporary_pub


def setup_module(module):
    cleanup()
    global pub, req
    pub = create_temporary_pub()

    req = HTTPRequest(None, {'SCRIPT_NAME': '/', 'SERVER_NAME': 'example.net'})
    req.language = None
    pub._set_request(req)


def teardown_module(module):
    clean_temporary_pub()


class MockHtmlForm:
    def __init__(self, widget):
        widget = copy.deepcopy(widget)
        form = Form(method='post', use_tokens=False, enctype='application/x-www-form-urlencoded')
        form.widgets.append(widget)
        self.as_html = str(form.render())
        response = mechanize._response.test_html_response(self.as_html, headers=[], url='')
        factory = mechanize.Browser()
        factory.set_response(response)
        self.factory = factory
        self.form = list(factory.forms())[0]

    def set_form_value(self, name, value):
        self.form.set_value(value, name)

    def set_form_hidden_value(self, name, value):
        self.form.find_control(name).readonly = False
        self.form.set_value(value, name)

    def get_parsed_query(self):
        return parse_query(self.form._request_data()[1], 'utf-8')


def mock_form_submission(req, widget, html_vars=None, click=None, hidden_html_vars=None):
    html_vars = html_vars or {}
    hidden_html_vars = hidden_html_vars or {}
    form = MockHtmlForm(widget)
    for k, v in html_vars.items():
        form.set_form_value(k, v)
    for k, v in hidden_html_vars.items():
        form.set_form_hidden_value(k, v)
    if click is not None:
        request = form.form.click(click)
        req.form = parse_query(request.data, 'utf-8')
    else:
        req.form = form.get_parsed_query()


def test_stringwidget_values():
    widget = StringWidget('test')
    form = MockHtmlForm(widget)
    assert 'name="test"' in form.as_html
    req.form = {}
    assert widget.parse() is None

    widget = StringWidget('test', value='foo')
    req.form = {}
    assert widget.parse() == 'foo'

    widget = StringWidget('test', value='foo')
    mock_form_submission(req, widget, {'test': ''})
    assert widget.parse() is None

    widget = StringWidget('test', value='foo')
    mock_form_submission(req, widget, {'test': 'bar'})
    assert widget.parse() == 'bar'


def test_stringwidget_strip():
    widget = StringWidget('test', value='foo')
    mock_form_submission(req, widget, {'test': ' bar '})
    assert widget.parse() == 'bar'


def test_stringwidget_required():
    widget = StringWidget('test', value='foo', required=True)
    mock_form_submission(req, widget, {'test': ''})
    assert widget.has_error()

    widget = StringWidget('test', value='foo', required=True)
    mock_form_submission(req, widget, {'test': 'bar'})
    assert not widget.has_error()
    assert widget.parse() == 'bar'


def test_stringwidget_readonly():
    widget = StringWidget('test', value='foo', required=True)
    assert 'readonly' not in str(widget.render())
    widget = StringWidget('test', value='foo', required=True, readonly=False)
    assert 'readonly' not in str(widget.render())
    widget = StringWidget('test', value='foo', required=True, readonly=True)
    assert 'readonly="readonly"' in str(widget.render())


def test_aria_hint_error():
    widget = StringWidget('test', value='foo')
    assert 'aria-describedby' not in str(widget.render())
    widget = StringWidget('test', value='foo', hint='hint')
    assert 'aria-describedby="form_hint_test"' in str(widget.render())
    widget.set_error('plop')
    assert 'aria-describedby="form_hint_test form_error_test"' in str(widget.render())
    widget = StringWidget('test', value='foo')
    widget.set_error('plop')
    assert 'aria-describedby="form_error_test"' in str(widget.render())


def test_file_with_preview_aria_hint_error():
    req.session = sessions.Session(id=1)  # needed by FileWithPreviewWidget
    widget = FileWithPreviewWidget('test')
    assert 'aria-describedby' not in str(widget.render())
    widget = FileWithPreviewWidget('test', value='foo', hint='hint')
    assert 'aria-describedby="form_hint_test"' in str(widget.render())
    widget.set_error('plop')
    assert 'aria-describedby="form_hint_test form_error_test"' in str(widget.render())
    widget = FileWithPreviewWidget('test', value='foo')
    widget.set_error('plop')
    assert 'aria-describedby="form_error_test"' in str(widget.render())


def test_table_list_rows():
    widget = TableListRowsWidget('test', columns=['a', 'b', 'c'])
    form = MockHtmlForm(widget)
    req.form = {}
    for row in range(5):
        for col in range(3):
            assert 'test$element%d$col%d' % (row, col) in form.as_html

    widget = TableListRowsWidget('test', columns=['a', 'b', 'c'])
    form = MockHtmlForm(widget)
    mock_form_submission(req, widget, {'test$element0$col0': 'bar', 'test$element1$col1': 'foo'})
    assert widget.parse() == [['bar', None, None], [None, 'foo', None]]


def test_table_list_rows_add_row():
    widget = TableListRowsWidget('test', columns=['a', 'b', 'c'])
    form = MockHtmlForm(widget)
    req.form = {}
    mock_form_submission(req, widget, click='test$add_element')
    widget = TableListRowsWidget('test', columns=['a', 'b', 'c'])
    form = MockHtmlForm(widget)
    for row in range(6):  # one more row
        for col in range(3):
            assert 'test$element%d$col%d' % (row, col) in form.as_html


def test_table_list_rows_required():
    req.form = {}
    widget = TableListRowsWidget('test', columns=['a', 'b', 'c'], required=True)
    mock_form_submission(req, widget)
    req.environ['REQUEST_METHOD'] = 'POST'
    try:
        widget = TableListRowsWidget('test', columns=['a', 'b', 'c'], required=True)
        assert widget.has_error()

        req.form = {}
        widget = TableListRowsWidget('test', columns=['a', 'b', 'c'], required=True)
        mock_form_submission(req, widget, click='test$add_element')
        widget = TableListRowsWidget('test', columns=['a', 'b', 'c'], required=True)
        assert not widget.has_error()
    finally:
        req.environ['REQUEST_METHOD'] = 'GET'


def test_table_list_rows_set_many_values():
    widget = TableListRowsWidget('test', columns=['a', 'b', 'c'])
    form = MockHtmlForm(widget)
    req.form = {}
    widget = TableListRowsWidget('test', columns=['a', 'b', 'c'])
    widget.set_value([(str(x), None, None) for x in range(10)])
    form = MockHtmlForm(widget)
    for row in range(10):
        for col in range(3):
            assert 'test$element%d$col%d' % (row, col) in form.as_html
    assert 'test$element%d$col%d' % (10, 0) not in form.as_html

    mock_form_submission(req, widget, click='test$add_element')
    widget = TableListRowsWidget('test', columns=['a', 'b', 'c'])
    form = MockHtmlForm(widget)
    assert 'test$element%d$col%d' % (10, 0) in form.as_html


def test_table_widget():
    req.form = {}
    widget = TableWidget('test', columns=['a', 'b', 'c'], rows=['A', 'B'])
    widget.set_value([['0'], ['1']])
    assert widget.get_widget('c-0-0').value == '0'
    assert widget.get_widget('c-0-1').value is None
    assert widget.get_widget('c-1-0').value == '1'
    assert widget.get_widget('c-1-1').value is None
    form = MockHtmlForm(widget)
    assert 'value="0"' in form.as_html
    assert 'value="1"' in form.as_html

    mock_form_submission(req, widget, {'test$c-0-0': 'X', 'test$c-0-1': 'Y'})
    assert widget.parse() == [['X', 'Y', None], ['1', None, None]]

    # load back incomplete data
    widget = TableWidget(
        'test', columns=['a', 'b', 'c'], rows=['A', 'B'], value=[['a'], ['b']], readonly=True
    )
    form = MockHtmlForm(widget)
    assert [x.attrib.get('value') for x in PyQuery(form.as_html).find('td input')] == [
        'a',
        None,
        None,
        'b',
        None,
        None,
    ]


def test_passwordentry_widget_success():
    widget = PasswordEntryWidget('test')
    form = MockHtmlForm(widget)
    assert 'name="test$pwd1"' in form.as_html
    req.form = {}
    assert widget.parse() is None

    widget = PasswordEntryWidget('test', value={'cleartext': 'foo'}, formats=['cleartext'])
    req.form = {}
    assert widget.parse() == {'cleartext': 'foo'}

    widget = PasswordEntryWidget('test', formats=['cleartext'])
    req.form = {}
    mock_form_submission(req, widget, {'test$pwd1': ''})
    assert widget.parse() is None

    widget = PasswordEntryWidget('test', formats=['cleartext'])
    req.form = {}
    mock_form_submission(req, widget, {'test$pwd1': 'foo', 'test$pwd2': 'foo'})
    assert widget.parse() == {'cleartext': 'foo'}

    widget = PasswordEntryWidget('test', formats=['cleartext'], confirmation=False)
    req.form = {}
    mock_form_submission(req, widget, {'test$pwd1': 'foo'})
    assert widget.parse() == {'cleartext': 'foo'}


def test_passwordentry_widget_errors():
    # mismatch
    widget = PasswordEntryWidget('test', formats=['cleartext'])
    req.form = {}
    mock_form_submission(req, widget, {'test$pwd1': 'foo', 'test$pwd2': 'bar'})
    assert widget.parse() is None
    assert widget.has_error() is True

    # too short
    widget = PasswordEntryWidget('test', formats=['cleartext'], min_length=4)
    req.form = {}
    mock_form_submission(req, widget, {'test$pwd1': 'foo', 'test$pwd2': 'foo'})
    assert widget.parse() is None
    assert widget.has_error() is True

    # uppercases
    widget = PasswordEntryWidget('test', formats=['cleartext'], count_uppercase=1)
    req.form = {}
    mock_form_submission(req, widget, {'test$pwd1': 'foo', 'test$pwd2': 'foo'})
    assert widget.parse() is None
    assert widget.has_error() is True

    # digits
    widget = PasswordEntryWidget('test', formats=['cleartext'], count_digit=1)
    req.form = {}
    mock_form_submission(req, widget, {'test$pwd1': 'foo', 'test$pwd2': 'foo'})
    assert widget.parse() is None
    assert widget.has_error() is True

    # specials
    widget = PasswordEntryWidget('test', formats=['cleartext'], count_special=1)
    req.form = {}
    mock_form_submission(req, widget, {'test$pwd1': 'foo', 'test$pwd2': 'foo'})
    assert widget.parse() is None
    assert widget.has_error() is True


def test_textwidget():
    widget = TextWidget('test')
    form = MockHtmlForm(widget)
    assert 'name="test"' in form.as_html
    req.form = {}
    assert widget.parse() is None

    widget = TextWidget('test', value='foo')
    req.form = {}
    assert widget.parse() == 'foo'

    widget = TextWidget('test', value='foo')
    mock_form_submission(req, widget, {'test': ''})
    assert widget.parse() is None

    widget = TextWidget('test', value='foo')
    mock_form_submission(req, widget, {'test': 'bar'})
    assert widget.parse() == 'bar'

    widget = TextWidget('test', value='foo', maxlength=10)
    mock_form_submission(req, widget, {'test': 'bar'})
    assert not widget.has_error()
    assert widget.parse() == 'bar'

    widget = TextWidget('test', value='foo', maxlength=10)
    mock_form_submission(req, widget, {'test': 'bar' * 10})
    assert widget.has_error()
    assert widget.get_error() == 'too many characters (limit is 10)'


def test_emailwidget():
    pub.cfg = {'emails': {'check_domain_with_dns': True}}
    get_response().javascript_code_parts = []
    widget = EmailWidget('test')
    form = MockHtmlForm(widget)
    assert 'name="test"' in form.as_html
    assert 'WCS_WELL_KNOWN_DOMAINS' in ''.join(get_response().javascript_code_parts)
    req.form = {}
    assert widget.parse() is None

    for good_email in ('foo@localhost', 'foo.bar@localhost', 'foo+bar@localhost', 'foo_bar@localhost'):
        widget = EmailWidget('test')
        mock_form_submission(req, widget, {'test': good_email})
        assert not widget.has_error()
        assert widget.parse() == good_email

    widget = EmailWidget('test')
    mock_form_submission(req, widget, {'test': 'foo'})
    assert widget.has_error()

    widget = EmailWidget('test')
    mock_form_submission(req, widget, {'test': 'foo@localhost@test'})
    assert widget.has_error()

    widget = EmailWidget('test')
    mock_form_submission(req, widget, {'test': 'foo@localhost..localdomain'})
    assert widget.has_error()

    widget = EmailWidget('test')
    mock_form_submission(req, widget, {'test': 'foö@localhost'})
    assert widget.has_error()


def test_date_widget():
    widget = DateWidget('test')
    form = MockHtmlForm(widget)
    assert 'name="test"' in form.as_html
    req.form = {}
    assert widget.parse() is None

    pub.cfg['language'] = {'language': 'en'}
    widget = DateWidget('test')
    mock_form_submission(req, widget, {'test': '2014-1-20'})
    assert not widget.has_error()
    assert widget.parse() == '2014-01-20'

    # check two-digit years
    widget = DateWidget('test')
    mock_form_submission(req, widget, {'test': '14-1-20'})
    assert not widget.has_error()
    assert widget.parse() == '2014-01-20'

    widget = DateWidget('test', maximum_date='1/1/2014')  # accept "fr" format
    mock_form_submission(req, widget, {'test': '2014-1-20'})
    assert widget.has_error()

    with pub.with_language('fr'):
        pub.cfg['language'] = {'language': 'fr'}
        widget = DateWidget('test')
        mock_form_submission(req, widget, {'test': '20/1/2014'})
        assert not widget.has_error()
        assert widget.parse() == '20/01/2014'

        mock_form_submission(req, widget, {'test': '2014-1-20'})
        assert not widget.has_error()
        assert widget.parse() == '20/01/2014'

        # prevent typo in years (too far in the past of future)
        widget = DateWidget('test')
        mock_form_submission(req, widget, {'test': '20/1/2123'})
        assert widget.has_error()

        widget = DateWidget('test')
        mock_form_submission(req, widget, {'test': '20/1/1123'})
        assert widget.has_error()

        widget = DateWidget('test')
        mock_form_submission(req, widget, {'test': '20/1/1789'})
        assert not widget.has_error()
        assert widget.parse() == '20/01/1789'

    widget = DateWidget('test', minimum_date='1/1/2014')
    mock_form_submission(req, widget, {'test': '20/1/2014'})
    assert not widget.has_error()

    widget = DateWidget('test', minimum_date='1/1/2014')
    mock_form_submission(req, widget, {'test': '20/1/2013'})
    assert widget.has_error()

    widget = DateWidget('test', maximum_date='1/1/2014')
    mock_form_submission(req, widget, {'test': '20/1/2013'})
    assert not widget.has_error()

    widget = DateWidget('test', maximum_date='1/1/2014')
    mock_form_submission(req, widget, {'test': '20/1/2014'})
    assert widget.has_error()

    widget = DateWidget('test', maximum_date='2014-1-1')  # accept "C" format
    mock_form_submission(req, widget, {'test': '20/1/2014'})
    assert widget.has_error()

    yesterday = (datetime.date.today() - datetime.timedelta(days=1)).strftime(widget.get_format_string())
    tomorrow = (datetime.date.today() + datetime.timedelta(days=1)).strftime(widget.get_format_string())

    widget = DateWidget('test', minimum_is_future=True)
    mock_form_submission(req, widget, {'test': tomorrow})
    assert not widget.has_error()

    widget = DateWidget('test', minimum_is_future=True)
    mock_form_submission(req, widget, {'test': yesterday})
    assert widget.has_error()

    widget = DateWidget('test', date_in_the_past=True)
    mock_form_submission(req, widget, {'test': tomorrow})
    assert widget.has_error()

    widget = DateWidget('test', date_in_the_past=True)
    mock_form_submission(req, widget, {'test': yesterday})
    assert not widget.has_error()


def test_wysiwygwidget():
    widget = WysiwygTextWidget('test')
    form = MockHtmlForm(widget)
    assert 'name="test"' in form.as_html
    req.form = {}
    assert widget.parse() is None

    widget = WysiwygTextWidget('test')
    mock_form_submission(req, widget, {'test': 'bla bla bla'})
    assert not widget.has_error()
    assert widget.parse() == 'bla bla bla'

    widget = WysiwygTextWidget('test')
    mock_form_submission(req, widget, {'test': '<p>bla bla bla</p>'})
    assert not widget.has_error()
    assert widget.parse() == '<p>bla bla bla</p>'

    widget = WysiwygTextWidget('test')
    mock_form_submission(req, widget, {'test': '<a href="#">a</a>'})
    assert not widget.has_error()
    assert widget.parse() == '<a href="#" rel="nofollow">a</a>'

    widget = WysiwygTextWidget('test')
    mock_form_submission(req, widget, {'test': '<a href="javascript:alert()">a</a>'})
    assert not widget.has_error()
    assert widget.parse() == '<a>a</a>'  # javascript: got filtered

    widget = WysiwygTextWidget('test')
    mock_form_submission(req, widget, {'test': 'an email address: test@example.net'})
    assert not widget.has_error()
    assert widget.parse() == 'an email address: <a href="mailto:test@example.net">test@example.net</a>'

    # check comments are kept
    widget = WysiwygTextWidget('test')
    mock_form_submission(req, widget, {'test': '<p>hello</p><!-- world --><p>.</p>'})
    assert not widget.has_error()
    assert widget.parse() == '<p>hello</p><!-- world --><p>.</p>'

    # check <script> are not kept
    widget = WysiwygTextWidget('test')
    mock_form_submission(req, widget, {'test': '<p>hello</p><script>alert("test")</script>'})
    assert not widget.has_error()
    assert widget.parse() == '<p>hello</p>alert("test")'

    # check <style> are not kept
    widget = WysiwygTextWidget('test')
    mock_form_submission(req, widget, {'test': '<p>hello</p><style>p { color: blue; }</style>'})
    assert not widget.has_error()
    assert widget.parse() == '<p>hello</p>p { color: blue; }'

    # check django syntax is kept intact
    widget = WysiwygTextWidget('test')
    mock_form_submission(
        req,
        widget,
        {'test': '<a href="{% if 1 > 2 %}héllo{% endif %}">{% if 2 > 1 %}{{plop|date:"Y"}}{% endif %}</a>'},
    )
    assert not widget.has_error()
    assert (
        widget.parse()
        == '<a href="{% if 1 > 2 %}héllo{% endif %}" rel="nofollow">{% if 2 > 1 %}{{plop|date:"Y"}}{% endif %}</a>'
    )

    # check variables with .id are not interpreted as domains
    widget = WysiwygTextWidget('test')
    mock_form_submission(
        req,
        widget,
        {'test': '{{ webservice.testusers.results.0.id }}'},
    )
    assert not widget.has_error()
    assert widget.parse() == '{{ webservice.testusers.results.0.id }}'

    # make sure it is kept intact even after ckeditor escaped characters
    widget = WysiwygTextWidget('test')
    mock_form_submission(
        req,
        widget,
        {
            'test': '<a href="{% if 1 &gt; 2 %}héllo{% endif %}" rel="nofollow">{% if 2 &gt; 1 %}{{plop|date:&quot;Y&quot;}}{% endif %}</a>'
        },
    )
    assert not widget.has_error()
    assert (
        widget.parse()
        == '<a href="{% if 1 > 2 %}héllo{% endif %}" rel="nofollow">{% if 2 > 1 %}{{plop|date:"Y"}}{% endif %}</a>'
    )

    # check feature flags to allow script and style tags
    pub.load_site_options()
    if not pub.site_options.has_section('options'):
        pub.site_options.add_section('options')
    pub.site_options.set('options', 'ckeditor-allow-style-tag', 'true')
    pub.site_options.set('options', 'ckeditor-allow-script-tag', 'true')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)

    # <script> are kept
    widget = WysiwygTextWidget('test')
    mock_form_submission(req, widget, {'test': '<p>hello</p><script>alert("test")</script>'})
    assert not widget.has_error()
    assert widget.parse() == '<p>hello</p><script>alert("test")</script>'

    # <style> are kept
    widget = WysiwygTextWidget('test')
    mock_form_submission(req, widget, {'test': '<p>hello</p><style>p { color: blue; }</style>'})
    assert not widget.has_error()
    assert widget.parse() == '<p>hello</p><style>p { color: blue; }</style>'


def test_wysiwygwidget_img():
    # check <img> are considered even if there's no text content
    widget = WysiwygTextWidget('test')
    mock_form_submission(req, widget, {'test': '<p><img src="/test/"></p>'})
    assert widget.parse() == '<p><img src="/test/"></p>'


def test_mini_rich_text_widget():
    widget = MiniRichTextWidget('test')
    form = MockHtmlForm(widget)
    assert PyQuery(form.as_html)('godo-editor[schema=basic]')


def test_mini_rich_text_widget_maxlength():
    # check maxlength with markup
    widget = MiniRichTextWidget('test', maxlength=10)
    mock_form_submission(req, widget, {'test': '<p>1234567890</p>'})
    assert widget.parse() == '<p>1234567890</p>'

    widget = MiniRichTextWidget('test', maxlength=10)
    mock_form_submission(req, widget, {'test': '<p>12345678901</p>'})
    assert widget.has_error()
    assert widget.get_error() == 'too many characters (limit is 10)'


def test_rich_text_widget():
    widget = RichTextWidget('test')
    form = MockHtmlForm(widget)
    assert PyQuery(form.as_html)('godo-editor[schema=full]')


def test_select_hint_widget():
    widget = SingleSelectHintWidget(
        'test', options=[('apple', 'Apple', 'apple'), ('pear', 'Pear', 'pear'), ('peach', 'Peach', 'peach')]
    )
    assert widget.has_valid_options()

    widget = SingleSelectHintWidget(
        'test',
        options=[('apple', 'Apple', 'apple'), ('pear', 'Pear', 'pear'), ('peach', 'Peach', 'peach')],
        options_with_attributes=[
            ('apple', 'Apple', 'apple', {}),
            ('pear', 'Pear', 'pear', {'disabled': True}),
            ('peach', 'Peach', 'peach', {}),
        ],
    )
    assert widget.has_valid_options()
    mock_form_submission(req, widget, {'test': ['apple']})
    assert widget.parse() == 'apple'

    with pytest.raises(AttributeError):
        # mechanize will
        #   raise AttributeError(
        #     "insufficient non-disabled items with name %s" % name)
        # as the item is disabled
        mock_form_submission(req, widget, {'test': ['pear']})

    widget = SingleSelectHintWidget(
        'test',
        options=[('apple', 'Apple', 'apple'), ('pear', 'Pear', 'pear'), ('peach', 'Peach', 'peach')],
        options_with_attributes=[
            ('apple', 'Apple', 'apple', {'disabled': True}),
            ('pear', 'Pear', 'pear', {'disabled': True}),
            ('peach', 'Peach', 'peach', {'disabled': True}),
        ],
    )
    assert not widget.has_valid_options()

    # test readonly only includes the selected value
    req.form = {}
    widget = SingleSelectHintWidget(
        'test', options=[('apple', 'Apple', 'apple'), ('pear', 'Pear', 'pear'), ('peach', 'Peach', 'peach')]
    )
    widget.readonly = 'readonly'
    widget.attrs['readonly'] = 'readonly'
    assert 'apple' in str(widget.render()) and 'pear' in str(widget.render())
    widget.set_value('pear')
    assert 'apple' not in str(widget.render()) and 'pear' in str(widget.render())
    assert 'readonly="readonly"' not in str(widget.render())

    # check hint
    widget = SingleSelectHintWidget(
        'test',
        options=[('apple', 'Apple', 'apple')],
        use_hint_as_first_option=True,
        hint='Hint about the field',
    )
    assert '>Hint about the field</option>' in str(widget.render())
    assert 'div id="form_hint_test"' not in str(widget.render())

    widget = SingleSelectHintWidget(
        'test',
        options=[('apple', 'Apple', 'apple')],
        use_hint_as_first_option=True,
        hint='Lorem ipsum ' * 20,  # too long to be in option
    )
    assert 'div id="form_hint_test"' in str(widget.render())

    widget = SingleSelectHintWidget(
        'test',
        options=[('apple', 'Apple', 'apple')],
        use_hint_as_first_option=True,
        hint=htmltext('<p>Hint <b>about</b> the field</p>'),  # markup to be stripped
    )
    assert '>Hint about the field</option>' in str(widget.render())

    widget = SingleSelectHintWidget(
        'test',
        options=[('apple', 'Apple', 'apple')],
        use_hint_as_first_option=False,
        hint=htmltext('<p>Hint <b>about</b> the field</p>'),  # markup not to be stripped
    )
    assert 'div id="form_hint_test" class="hint"><p>Hint <b>about</b> the field</p>' in str(widget.render())

    widget = SingleSelectHintWidget(
        'test',
        options=[('apple', 'Apple', 'apple')],
        use_hint_as_first_option=True,
        hint=htmltext('<p>&gt; Select</p>'),  # markup to be stripped, and unescaped
    )
    assert '>&gt; Select</option>' in str(widget.render())


def test_select_widget():
    # test with optgroups
    widget = SingleSelectWidget(
        'test',
        options=[
            OptGroup('foo'),
            ('apple', 'Apple', 'apple'),
            OptGroup('bar'),
            ('pear', 'Pear', 'pear'),
            ('peach', 'Peach', 'peach'),
        ],
    )
    assert widget.get_allowed_values() == ['apple', 'pear', 'peach']
    assert (
        '<optgroup label="foo">'
        '<option value="apple">Apple</option>'
        '</optgroup>'
        '<optgroup label="bar">'
        '<option value="pear">Pear</option>'
        '<option value="peach">Peach</option>'
        '</optgroup>'
    ) in ''.join(p.strip() for p in str(widget.render()).split('\n'))

    # first option is not optgroup
    widget = SingleSelectWidget(
        'test',
        options=[
            ('apple', 'Apple', 'apple'),
            OptGroup('bar'),
            ('pear', 'Pear', 'pear'),
            ('peach', 'Peach', 'peach'),
        ],
    )
    assert widget.get_allowed_values() == ['apple', 'pear', 'peach']
    assert (
        '<option value="apple">Apple</option>'
        '<optgroup label="bar">'
        '<option value="pear">Pear</option>'
        '<option value="peach">Peach</option>'
        '</optgroup>'
    ) in ''.join(p.strip() for p in str(widget.render()).split('\n'))

    # only one optgroup and no options
    widget = SingleSelectWidget(
        'test',
        options=[
            OptGroup('bar'),
        ],
    )
    assert widget.get_allowed_values() == []
    assert ('<optgroup label="bar">' '</optgroup>') in ''.join(
        p.strip() for p in str(widget.render()).split('\n')
    )


def test_select_or_other_widget():
    widget = SingleSelectWidgetWithOther(
        'test', options=[('apple', 'Apple'), ('pear', 'Pear'), ('peach', 'Peach')]
    )
    form = MockHtmlForm(widget)
    assert '__other' in form.as_html
    assert 'Other:' in form.as_html
    assert widget.parse() is None

    widget = SingleSelectWidgetWithOther(
        'test', options=[('apple', 'Apple'), ('pear', 'Pear'), ('peach', 'Peach')], other_label='Alternative:'
    )
    form = MockHtmlForm(widget)
    assert '__other' in form.as_html
    assert 'Alternative:' in form.as_html

    widget = SingleSelectWidgetWithOther(
        'test', options=[('apple', 'Apple'), ('pear', 'Pear'), ('peach', 'Peach')]
    )
    mock_form_submission(req, widget, {'test$choice': ['apple']})
    assert widget.parse() == 'apple'

    widget = SingleSelectWidgetWithOther(
        'test', options=[('apple', 'Apple'), ('pear', 'Pear'), ('peach', 'Peach')]
    )
    mock_form_submission(req, widget, {'test$choice': ['__other'], 'test$other': 'Apricot'})
    assert widget.parse() == 'Apricot'


def test_checkboxes_widget():
    widget = CheckboxesWidget(
        'test', options=[('apple', 'Apple', 'apple'), ('pear', 'Pear', 'pear'), ('peach', 'Peach', 'peach')]
    )
    mock_form_submission(req, widget, {'test$elementpeach': ['yes'], 'test$elementpear': ['yes']})
    assert widget.parse() == ['pear', 'peach']


def test_composite_widget():
    widget = CompositeWidget('compotest')
    widget.add(StringWidget, name='str1')
    widget.add(StringWidget, name='str2', required=True)
    req.form = {'compotest$str1': 'foo1', 'compotest$str2': 'foo2'}
    assert not widget.has_error()
    assert len(widget.widgets) == 2
    assert widget.widgets[0].parse() == 'foo1'
    assert widget.widgets[1].parse() == 'foo2'

    widget = CompositeWidget('compotest')
    widget.add(StringWidget, name='str1')
    widget.add(StringWidget, name='str2', required=True)
    req.form = {'compotest$str1': 'alone'}
    assert widget.has_error()
    assert not widget.widgets[0].has_error()
    assert widget.widgets[0].parse() == 'alone'
    assert widget.widgets[1].has_error()
    assert 'required' in widget.widgets[1].get_error()

    req.session = sessions.Session(id=1)  # needed by FileWithPreviewWidget
    widget = CompositeWidget('compotest')
    widget.add(StringWidget, name='str1')
    widget.add(FileWithPreviewWidget, name='')
    assert 'class="FileWithPreviewWidget widget file-upload-widget"' in str(
        widget.render_content_as_tr()
    )  # extra_css_class is present


def test_computed_expression_widget_templates():
    widget = ComputedExpressionWidget('test')
    mock_form_submission(req, widget, {'test$value_template': 'hello world'})
    assert widget.parse() == 'hello world'
    assert not widget.has_error()

    widget = ComputedExpressionWidget('test')
    mock_form_submission(req, widget, {'test$value_template': ''})
    assert widget.parse() is None
    assert not widget.has_error()

    # templates,
    widget = ComputedExpressionWidget('test')
    mock_form_submission(req, widget, {'test$value_template': '{{ form_var_xxx }}'})
    assert not widget.has_error()

    # invalid values
    widget = ComputedExpressionWidget('test')
    mock_form_submission(req, widget, {'test$value_template': '{% if True %}'})
    assert widget.has_error()
    assert widget.get_error().startswith('syntax error in Django template')

    # ezt
    widget = ComputedExpressionWidget('test')
    mock_form_submission(req, widget, {'test$value_template': '[form_var_xxx]'})
    assert not widget.has_error()

    widget = ComputedExpressionWidget('test')
    mock_form_submission(req, widget, {'test$value_template': '[end]'})
    assert widget.has_error()
    assert widget.get_error().startswith('syntax error in ezt template')


def test_wcsextrastringwidget():
    widget = WcsExtraStringWidget('test', value='foo', required=True)
    mock_form_submission(req, widget, {'test': ''})
    assert widget.has_error()

    widget = WcsExtraStringWidget('test', value='foo', required=True)
    mock_form_submission(req, widget, {'test': 'bar'})
    assert not widget.has_error()
    assert widget.parse() == 'bar'


def test_wcsextrastringwidget_regex_validation():
    # check regex validation
    class FakeField:
        pass

    fakefield = FakeField()
    fakefield.validation = {'type': 'regex', 'value': r'\d+'}

    widget = WcsExtraStringWidget('test', value='foo', required=False)
    widget.field = fakefield
    mock_form_submission(req, widget, {'test': '123'})
    assert not widget.has_error()

    widget = WcsExtraStringWidget('test', value='foo', required=False)
    widget.field = fakefield
    mock_form_submission(req, widget, {'test': '123 ab'})
    assert widget.has_error()

    widget = WcsExtraStringWidget('test', value='foo', required=False)
    widget.field = fakefield
    mock_form_submission(req, widget, {'test': 'cdab 12'})
    assert widget.has_error()

    fakefield.validation = {'type': 'regex', 'value': r'\d+(\.\d{1,2})?'}
    widget = WcsExtraStringWidget('test', value='foo', required=False)
    widget.field = fakefield
    mock_form_submission(req, widget, {'test': '12'})
    assert not widget.has_error()

    widget = WcsExtraStringWidget('test', value='foo', required=False)
    widget.field = fakefield
    mock_form_submission(req, widget, {'test': '12.34'})
    assert not widget.has_error()

    widget = WcsExtraStringWidget('test', value='foo', required=False)
    widget.field = fakefield
    mock_form_submission(req, widget, {'test': '12,34'})
    assert widget.has_error()
    assert widget.error == 'invalid value'

    widget = WcsExtraStringWidget('test', value='foo', required=False)
    fakefield.validation = {
        'type': 'regex',
        'value': r'\d+(\.\d{1,2})?',
        'error_message': 'Foo Bar Custom Error',
    }
    widget.field = fakefield
    mock_form_submission(req, widget, {'test': '12,34'})
    assert widget.has_error()
    assert widget.error == 'Foo Bar Custom Error'


def test_wcsextrastringwidget_builtin_validation():
    class FakeField:
        pass

    fakefield = FakeField()

    fakefield.validation = {'type': 'digits'}
    widget = WcsExtraStringWidget('test', value='foo', required=False)
    widget.field = fakefield
    mock_form_submission(req, widget, {'test': '12'})
    assert not widget.has_error()

    widget = WcsExtraStringWidget('test', value='foo', required=False)
    widget.field = fakefield
    mock_form_submission(req, widget, {'test': 'az'})
    assert widget.has_error()
    assert widget.error == 'You should enter digits only, for example: 123.'

    fakefield.validation = {'type': 'zipcode-fr'}
    widget = WcsExtraStringWidget('test', value='foo', required=False)
    widget.field = fakefield
    mock_form_submission(req, widget, {'test': '12345'})
    assert not widget.has_error()

    # and check it gets a special HTML inputmode
    widget = WcsExtraStringWidget('test', value='foo', required=False)
    widget.field = fakefield
    assert 'inputmode="numeric"' in str(widget.render())

    widget = WcsExtraStringWidget('test', value='foo', required=False)
    widget.field = fakefield
    mock_form_submission(req, widget, {'test': '1234'})
    assert widget.has_error()
    assert widget.error == 'You should enter a 5-digits zip code, for example 75014.'


def test_wcsextrastringwidget_maxlength():
    widget = WcsExtraStringWidget('test', value='foo', required=False, maxlength=10)
    mock_form_submission(req, widget, {'test': '123'})
    assert not widget.has_error()

    widget = WcsExtraStringWidget('test', value='foo', required=False, maxlength=10)
    mock_form_submission(req, widget, {'test': '1234567890abcdef'})
    assert widget.has_error()

    form = MockHtmlForm(widget)
    assert 'maxlength="10"' in form.as_html
    assert 'error_test_tooLong' in form.as_html

    widget = WcsExtraStringWidget('test', value='foo', required=False, maxlength='invalid')
    mock_form_submission(req, widget, {'test': '1234567890abcdef'})
    assert not widget.has_error()
    form = MockHtmlForm(widget)
    assert 'maxlength' not in form.as_html
    assert 'error_test_tooLong' not in form.as_html


def test_wcsextrastringwidget_phone():
    class FakeField:
        pass

    fakefield = FakeField()
    fakefield.validation = {'type': 'phone'}

    # check validation
    for valid_case in ('0123456789', '+321234566', '02/123.45.67', '+33(0)2 34 56 78 90'):
        widget = WcsExtraStringWidget('test', value='foo', required=False)
        widget.field = fakefield
        mock_form_submission(req, widget, {'test': valid_case})
        assert not widget.has_error()

    for invalid_case in ('az', '123 az', 'aZ 1234'):
        widget = WcsExtraStringWidget('test', value='foo', required=False)
        widget.field = fakefield
        mock_form_submission(req, widget, {'test': invalid_case})
        assert widget.has_error()
        assert widget.error == 'You should enter a valid phone number.'

    # and check it gets a special HTML input type
    widget = WcsExtraStringWidget('test', value='foo', required=False)
    widget.field = fakefield
    assert 'type="tel"' in str(widget.render())


def test_wcsextrastringwidget_phone_fr():
    class FakeField:
        pass

    fakefield = FakeField()

    # check validation
    fakefield.validation = {'type': 'phone-fr'}
    widget = WcsExtraStringWidget('test', value='foo', required=False)
    widget.field = fakefield
    mock_form_submission(req, widget, {'test': '0123456789'})
    assert not widget.has_error()

    widget = WcsExtraStringWidget('test', value='foo', required=False)
    widget.field = fakefield
    mock_form_submission(req, widget, {'test': '01 23 45 67 89'})
    assert not widget.has_error()

    # refuse numbers with both international prefix and local prefix
    for number in ('+33 01 23 45 67 89', '+262 0692123456'):
        widget = WcsExtraStringWidget('test', value='foo', required=False)
        widget.field = fakefield
        mock_form_submission(req, widget, {'test': number})
        assert widget.has_error()

    # refuse numbers from other countries
    widget = WcsExtraStringWidget('test', value='foo', required=False)
    widget.field = fakefield
    mock_form_submission(req, widget, {'test': '+321234566'})
    assert widget.has_error()

    widget = WcsExtraStringWidget('test', value='foo', required=False)
    widget.field = fakefield
    mock_form_submission(req, widget, {'test': 'az'})
    assert widget.has_error()
    assert widget.error == 'You should enter a valid 10-digits phone number, for example 06 39 98 89 93.'

    widget = WcsExtraStringWidget('test', value='foo', required=False)
    widget.field = fakefield
    mock_form_submission(req, widget, {'test': '0123'})
    assert widget.has_error()

    widget = WcsExtraStringWidget('test', value='foo', required=False)
    widget.field = fakefield
    mock_form_submission(req, widget, {'test': '01234567890123'})
    assert widget.has_error()

    # and check it gets a special HTML input type
    assert 'type="tel"' in str(widget.render())


def test_wcsextrastringwidget_mobile_local():
    class FakeField:
        pass

    fakefield = FakeField()

    # check validation
    fakefield.validation = {'type': 'mobile-local'}
    widget = WcsExtraStringWidget('test', value='foo', required=False)
    widget.field = fakefield
    mock_form_submission(req, widget, {'test': '06 30 98 67 89'})
    assert not widget.has_error()

    widget = WcsExtraStringWidget('test', value='foo', required=False)
    widget.field = fakefield
    mock_form_submission(req, widget, {'test': '01 23 45 67 89'})
    assert widget.has_error()
    assert widget.error == 'You should enter a valid mobile phone number.'

    # extra characters
    widget = WcsExtraStringWidget('test', value='foo', required=False)
    widget.field = fakefield
    mock_form_submission(req, widget, {'test': '06 30 98 67 89 abc'})
    assert widget.has_error()
    assert widget.error == 'You should enter a valid mobile phone number.'

    pub.load_site_options()
    if not pub.site_options.has_section('options'):
        pub.site_options.add_section('options')
    pub.site_options.set('options', 'local-region-code', 'BE')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)

    fakefield.validation = {'type': 'mobile-local'}
    widget = WcsExtraStringWidget('test', value='foo', required=False)
    widget.field = fakefield
    mock_form_submission(req, widget, {'test': '06 30 98 67 89'})
    assert widget.has_error()

    fakefield.validation = {'type': 'mobile-local'}
    widget = WcsExtraStringWidget('test', value='foo', required=False)
    widget.field = fakefield
    mock_form_submission(req, widget, {'test': '04 75 98 67 89'})
    assert not widget.has_error()

    # and check it gets a special HTML input type
    assert 'type="tel"' in str(widget.render())


def test_wcsextrastringwidget_siren_validation():
    class FakeField:
        pass

    fakefield = FakeField()
    fakefield.validation = {'type': 'siren-fr'}

    widget = WcsExtraStringWidget('test', value='foo', required=False)
    widget.field = fakefield
    mock_form_submission(req, widget, {'test': '443170139'})
    assert not widget.has_error()
    assert widget.value == '443170139'

    widget = WcsExtraStringWidget('test', value='foo', required=False)
    widget.field = fakefield
    mock_form_submission(req, widget, {'test': ' 443 170  139'})
    assert not widget.has_error()
    assert widget.value == '443170139'

    widget = WcsExtraStringWidget('test', value='foo', required=False)
    widget.field = fakefield
    mock_form_submission(req, widget, {'test': '443170130'})
    assert widget.has_error()
    assert widget.error == 'You should enter a valid 9-digits SIREN code, for example 443170139.'
    assert widget.value == '443170130'

    widget = WcsExtraStringWidget('test', value='foo', required=False)
    widget.field = fakefield
    mock_form_submission(req, widget, {'test': '44317013900036'})
    assert widget.has_error()
    assert widget.value == '44317013900036'

    widget = WcsExtraStringWidget('test', value='foo', required=False)
    widget.field = fakefield
    mock_form_submission(req, widget, {'test': 'XXX170130'})
    assert widget.has_error()
    assert widget.value == 'XXX170130'

    widget = WcsExtraStringWidget('test', value='foo', required=False)
    widget.field = fakefield
    mock_form_submission(req, widget, {'test': 'XXX 170 130'})
    assert widget.has_error()
    assert widget.value == 'XXX 170 130'  # do not normalize invalid value

    widget = WcsExtraStringWidget('test', value='foo', required=False)
    widget.field = fakefield
    mock_form_submission(req, widget, {'test': '443170¹39'})
    assert widget.has_error()
    assert widget.value == '443170¹39'


def test_wcsextrastringwidget_siret_validation():
    class FakeField:
        pass

    fakefield = FakeField()
    fakefield.validation = {'type': 'siret-fr'}

    # regular case
    widget = WcsExtraStringWidget('test', value='foo', required=False)
    widget.field = fakefield
    mock_form_submission(req, widget, {'test': '44317013900036'})
    assert not widget.has_error()
    assert widget.value == '44317013900036'

    widget = WcsExtraStringWidget('test', value='foo', required=False)
    widget.field = fakefield
    mock_form_submission(req, widget, {'test': '443 170 139  00036'})
    assert not widget.has_error()
    assert widget.value == '44317013900036'  # normalized

    assert not widget.has_error()
    # special case la poste
    widget = WcsExtraStringWidget('test', value='foo', required=False)
    widget.field = fakefield
    mock_form_submission(req, widget, {'test': '35600000000048'})
    assert not widget.has_error()
    assert widget.value == '35600000000048'

    # failing cases
    widget = WcsExtraStringWidget('test', value='foo', required=False)
    widget.field = fakefield
    mock_form_submission(req, widget, {'test': '44317013900037'})
    assert widget.has_error()
    assert widget.error == 'You should enter a valid 14-digits SIRET code, for example 44317013900036.'
    assert widget.value == '44317013900037'

    widget = WcsExtraStringWidget('test', value='foo', required=False)
    widget.field = fakefield
    mock_form_submission(req, widget, {'test': 'ABC17013900037'})
    assert widget.has_error()
    assert widget.value == 'ABC17013900037'

    widget = WcsExtraStringWidget('test', value='foo', required=False)
    widget.field = fakefield
    mock_form_submission(req, widget, {'test': '443170139'})
    assert widget.has_error()
    assert widget.value == '443170139'

    widget = WcsExtraStringWidget('test', value='foo', required=False)
    widget.field = fakefield
    mock_form_submission(req, widget, {'test': '443 170 139'})
    assert widget.has_error()
    assert widget.value == '443 170 139'  # do not normalize invalid value


def test_wcsextrastringwidget_nir_validation():
    class FakeField:
        pass

    fakefield = FakeField()
    fakefield.validation = {'type': 'nir-fr'}

    # regular cases
    widget = WcsExtraStringWidget('test', value='foo', required=False)
    widget.field = fakefield
    # https://fr.wikipedia.org/wiki/Num%C3%A9ro_de_s%C3%A9curit%C3%A9_sociale_en_France#/media/Fichier:CarteVitale2.jpg
    mock_form_submission(req, widget, {'test': '269054958815780'})
    assert not widget.has_error()
    assert widget.value == '269054958815780'

    # corsica
    widget = WcsExtraStringWidget('test', value='foo', required=False)
    widget.field = fakefield
    mock_form_submission(req, widget, {'test': '269052A58815717'})
    assert not widget.has_error()
    assert widget.value == '269052A58815717'
    widget = WcsExtraStringWidget('test', value='foo', required=False)
    widget.field = fakefield
    mock_form_submission(req, widget, {'test': '269052B58815744'})
    assert not widget.has_error()
    assert widget.value == '269052B58815744'

    # make sure inputmode is not set as android on screen keyboard won't let users switch to letters.
    form = MockHtmlForm(widget)
    assert 'inputmode="numeric"' not in form.as_html

    # accept spaces, but remove them
    widget = WcsExtraStringWidget('test', value='foo', required=False)
    widget.field = fakefield
    mock_form_submission(req, widget, {'test': '2 69 05 2B588 157  44'})
    assert not widget.has_error()
    assert widget.value == '269052B58815744'

    # failing cases
    widget = WcsExtraStringWidget('test', value='foo', required=False)
    widget.field = fakefield
    mock_form_submission(req, widget, {'test': '42'})
    assert widget.has_error()
    assert (
        widget.error
        == 'You should enter a valid 15-digits social security number, for example 294037512000522.'
    )
    assert widget.value == '42'

    widget = WcsExtraStringWidget('test', value='foo', required=False)
    widget.field = fakefield
    mock_form_submission(req, widget, {'test': '269054958815700'})
    assert widget.has_error()
    assert widget.value == '269054958815700'

    widget = WcsExtraStringWidget('test', value='foo', required=False)
    widget.field = fakefield
    mock_form_submission(req, widget, {'test': 'hello 789012345'})
    assert widget.has_error()
    assert widget.value == 'hello 789012345'

    widget = WcsExtraStringWidget('test', value='foo', required=False)
    widget.field = fakefield
    mock_form_submission(req, widget, {'test': '069054958815780'})
    assert widget.has_error()
    assert widget.value == '069054958815780'

    widget = WcsExtraStringWidget('test', value='foo', required=False)
    widget.field = fakefield
    mock_form_submission(req, widget, {'test': '269004958815780'})
    assert widget.has_error()
    assert widget.value == '269004958815780'

    widget = WcsExtraStringWidget('test', value='foo', required=False)
    widget.field = fakefield
    mock_form_submission(req, widget, {'test': '269054900015780'})
    assert widget.has_error()
    assert widget.value == '269054900015780'

    widget = WcsExtraStringWidget('test', value='foo', required=False)
    widget.field = fakefield
    mock_form_submission(req, widget, {'test': '269054958800080'})
    assert widget.has_error()
    assert widget.value == '269054958800080'


def test_wcsextrastringwidget_belgian_nrn_validation():
    class FakeField:
        pass

    fakefield = FakeField()
    fakefield.validation = {'type': 'nrn-be'}

    # regular cases
    for value in ('85073003328', '17073003384', '40000095579', '00000100364', '40000100133'):
        widget = WcsExtraStringWidget('test', value='foo', required=False)
        widget.field = fakefield
        mock_form_submission(req, widget, {'test': value})
        assert not widget.has_error()

    # failing cases
    for value in (
        '8507300332',  # too short
        '850730033281',  # too long
        '8507300332A',  # not just digits
        '85143003377',  # invalid month
        '85073203365',  # invalid day
        '85073003329',  # invalid checksum (<2000)
        '17073003385',  # invalid checksum (≥2000)
    ):
        widget = WcsExtraStringWidget('test', value='foo', required=False)
        widget.field = fakefield
        mock_form_submission(req, widget, {'test': value})
        assert widget.has_error()


def test_wcsextrastringwidget_ants_predemand_fr_validation():
    class FakeField:
        pass

    fakefield = FakeField()
    fakefield.validation = {'type': 'ants-predemand-fr'}

    # regular cases
    for value in ('MLCE4EC23X', 'mlce4ec23x', '  mlce4ec23x'):
        widget = WcsExtraStringWidget('test', value='', required=False)
        widget.field = fakefield
        mock_form_submission(req, widget, {'test': value})
        assert not widget.has_error()
        assert widget.parse() == 'MLCE4EC23X'


def test_wcsextrastringwidget_fiscal_number_fr_validation():
    class FakeField:
        pass

    fakefield = FakeField()
    fakefield.validation = {'type': 'tax-assessment-fiscal-number-fr'}

    # regular cases
    for value in ('12 34 567 891 234', '1234567891234'):
        widget = WcsExtraStringWidget('test', value='', required=False)
        widget.field = fakefield
        mock_form_submission(req, widget, {'test': value})
        assert not widget.has_error()
        assert widget.parse() == '1234567891234'

    # error cases
    for value in ('1234567', 'blah'):
        widget = WcsExtraStringWidget('test', value='', required=False)
        widget.field = fakefield
        mock_form_submission(req, widget, {'test': value})
        assert widget.has_error()


def test_wcsextrastringwidget_rna_number_fr_validation():
    class FakeField:
        pass

    fakefield = FakeField()
    fakefield.validation = {'type': 'rna-number-fr'}

    # regular cases
    for value in ('W743525491', 'w743525491', 'W2B1234567', 'W9X1234567'):
        widget = WcsExtraStringWidget('test', value='', required=False)
        widget.field = fakefield
        mock_form_submission(req, widget, {'test': value})
        assert not widget.has_error()

    # error cases
    for value in ('1234567', 'blah', 'W1234567890', 'W12345', 'W1B1234567'):
        widget = WcsExtraStringWidget('test', value='', required=False)
        widget.field = fakefield
        mock_form_submission(req, widget, {'test': value})
        assert widget.has_error()


@pytest.mark.parametrize('iban_class', [None, IBAN])
def test_wcsextrastringwidget_iban_validation(iban_class):
    class FakeField:
        pass

    with mock.patch('wcs.qommon.misc.IBAN', iban_class):
        fakefield = FakeField()
        fakefield.validation = {'type': 'iban'}

        # regular cases
        for iban in [
            'BE71 0961 2345 6769',  # Belgium
            'be71 0961 2345 6769',  # Lowercase
            ' BE71 0961 2345 6769  ',  # Extra padding
            'FR76 3000 6000 0112 3456 7890 189',  # France
            'FR27 2004 1000 0101 2345 6Z02 068',  # France (having letter)
            'DE91 1000 0000 0123 4567 89',  # Germany
            'GR96 0810 0010 0000 0123 4567 890',  # Greece
            'RO09 BCYP 0000 0012 3456 7890',  # Romania
            'SA44 2000 0001 2345 6789 1234',  # Saudi Arabia
            'ES79 2100 0813 6101 2345 6789',  # Spain
            'CH56 0483 5012 3456 7800 9',  # Switzerland
            'GB98 MIDL 0700 9312 3456 78',  # United Kingdom
        ]:
            widget = WcsExtraStringWidget('test', value='foo', required=False)
            widget.field = fakefield
            mock_form_submission(req, widget, {'test': iban.replace(' ', '')})
            assert not widget.has_error()
            widget._parse(req)
            assert widget.value == iban.upper().replace(' ', '').strip()

        # failing cases
        for iban in [
            '42',
            'FR76 2004 1000 0101 2345 6Z02 068',
            'FR76 2004 1000 0101 2345 6%02 068',
            'FR76 hello 234 6789 1234 6789 123',
            'FRxx 2004 1000 0101 2345 6Z02 068',
            'FR76 3000 6000 011² 3456 7890 189',  # ²
            'XX12',
            'XX12 0000 00',
            'FR76',
            'FR76 0000 0000 0000 0000 0000 000',
            'FR76 1234 4567',
        ]:
            widget = WcsExtraStringWidget('test', value='foo', required=False)
            widget.field = fakefield
            mock_form_submission(req, widget, {'test': iban.replace(' ', '')})
            assert widget.has_error()
            assert (
                widget.error
                == 'You should enter a valid IBAN code, it should have between 14 and 34 characters, '
                'for example FR7600001000010000000000101.'
            )


def test_wcsextrastringwidget_time():
    class FakeField:
        pass

    fakefield = FakeField()
    fakefield.validation = {'type': 'time'}

    # check validation
    for valid_case in ('00:00', '23:59'):
        widget = WcsExtraStringWidget('test', value='foo', required=False)
        widget.field = fakefield
        mock_form_submission(req, widget, {'test': valid_case})
        assert not widget.has_error()

    for invalid_case in ('az', '0000', '24:00', '23:60', 'a00:00', '00:00a'):
        widget = WcsExtraStringWidget('test', value='foo', required=False)
        widget.field = fakefield
        mock_form_submission(req, widget, {'test': invalid_case})
        assert widget.has_error()
        assert widget.error == 'You should enter a valid time, between 00:00 and 23:59.'

    # and check it gets a special HTML input type
    widget = WcsExtraStringWidget('test', value='foo', required=False)
    widget.field = fakefield
    assert 'type="time"' in str(widget.render())


def test_wcsextrastringwidget_url_validation():
    class FakeField:
        pass

    fakefield = FakeField()
    fakefield.validation = {'type': 'url'}

    # check validation
    for valid_case in ('https://www.example.com/plop?foo=bar', 'http://www.example.net'):
        widget = WcsExtraStringWidget('test', required=False)
        widget.field = fakefield
        mock_form_submission(req, widget, {'test': valid_case})
        assert not widget.has_error()

    for invalid_case in ('file:///test/', 'xyz'):
        widget = WcsExtraStringWidget('test', required=False)
        widget.field = fakefield
        mock_form_submission(req, widget, {'test': invalid_case})
        assert widget.has_error()
        assert widget.error == 'You should enter a valid URL, starting with http:// or https://.'

    # and check it gets a special HTML input type
    widget = WcsExtraStringWidget('test', value='foo', required=False)
    widget.field = fakefield
    assert 'type="url"' in str(widget.render())


def test_wcsextrastringwidget_django_validation():
    class FakeField:
        pass

    fakefield = FakeField()

    fakefield.validation = {'type': 'django', 'value': 'value|decimal and value|decimal < 20'}
    widget = WcsExtraStringWidget('test', value='foo', required=False)
    widget.field = fakefield
    mock_form_submission(req, widget, {'test': '12'})
    assert not widget.has_error()

    widget = WcsExtraStringWidget('test', value='foo', required=False)
    widget.field = fakefield
    mock_form_submission(req, widget, {'test': '35'})
    assert widget.has_error()

    widget = WcsExtraStringWidget('test', value='foo', required=False)
    widget.field = fakefield
    mock_form_submission(req, widget, {'test': 'az'})
    assert widget.has_error()
    assert widget.error == 'invalid value'

    widget = WcsExtraStringWidget('test', value='foo', required=False)
    fakefield.validation = {
        'type': 'django',
        'value': 'value|decimal and value|decimal < 20',
        'error_message': 'Foo Bar Custom Error',
    }
    widget.field = fakefield
    mock_form_submission(req, widget, {'test': 'az'})
    assert widget.has_error()
    assert widget.error == 'Foo Bar Custom Error'

    pub.substitutions.feed(pub)
    fakefield.validation = {'type': 'django', 'value': 'value|decimal == today.year'}
    widget = WcsExtraStringWidget('test', value='foo', required=False)
    widget.field = fakefield
    mock_form_submission(req, widget, {'test': str(datetime.date.today().year)})
    assert not widget.has_error()


def test_widgetdict_widget():
    widget = WidgetDict('test', value={'a': None, 'b': None, 'c': None})
    mock_form_submission(
        req,
        widget,
        {
            'test$element0key': 'a',
            'test$element0value': 'value-a',
            'test$element1key': 'c',
            'test$element1value': 'value-c',
            'test$element2key': 'b',
            'test$element2value': 'value-b',
        },
    )
    assert widget.parse() == {'a': 'value-a', 'b': 'value-b', 'c': 'value-c'}

    # on rendering, elements are ordered by their key name
    html_frags = str(widget.render_content()).split()
    assert (
        html_frags.index('name="test$element0key"')
        < html_frags.index('name="test$element2key"')  # a
        < html_frags.index('name="test$element1key"')  # b
    )  # c


def test_map_widget():
    widget = MapWidget('test', title='Map')
    form = MockHtmlForm(widget)
    assert 'name="test$latlng"' in form.as_html
    req.form = {}
    assert widget.parse() is None

    widget = MapWidget('test', title='Map')
    mock_form_submission(req, widget, hidden_html_vars={'test$latlng': '1.23;2.34'})
    assert not widget.has_error()
    assert widget.parse() == {'lat': 1.23, 'lon': 2.34}

    assert '<label' in str(widget.render())
    assert '<label ' not in str(widget.render_widget_content())

    widget = MapWidget('test', title='Map')
    mock_form_submission(req, widget, hidden_html_vars={'test$latlng': 'blah'})
    assert widget.has_error()

    pub.load_site_options()
    pub.site_options.set('options', 'map-bounds-top-left', '1.23;2.34')
    pub.site_options.set('options', 'map-bounds-bottom-right', '2.34;3.45')
    widget = MapWidget('test', title='Map')
    assert 'data-max-bounds-lat1=' in str(widget.render())
    assert 'data-max-bounds-lat2=' in str(widget.render())
    assert 'data-max-bounds-lng1=' in str(widget.render())
    assert 'data-max-bounds-lng2=' in str(widget.render())


def test_profile_fields_sorting():
    widget = ProfileUpdateRowWidget('profile')
    assert [f[1] for f in widget.get_widgets()[0].options] == ['', 'Email', 'Name']


def test_computed_expression_widget():
    widget = ComputedExpressionWidget('test')
    form = Form(method='post', use_tokens=False, enctype='application/x-www-form-urlencoded')
    form.widgets.append(widget)
    assert '$type' not in str(form.render())

    widget = ComputedExpressionWidget('test')
    mock_form_submission(req, widget, {'test$value_template': 'hello world'})
    assert widget.parse() == 'hello world'
    assert not widget.has_error()

    widget = ComputedExpressionWidget('test')
    mock_form_submission(req, widget, {'test$value_template': ''})
    assert widget.parse() is None
    assert not widget.has_error()

    widget = ComputedExpressionWidget('test')
    mock_form_submission(req, widget, {'test$value_template': '{{ form_var_xxx }}'})
    assert not widget.has_error()

    widget = ComputedExpressionWidget('test')
    mock_form_submission(req, widget, {'test$value_template': '{% if True %}'})
    assert widget.has_error()
    assert widget.get_error().startswith('syntax error in Django template')


def test_condition_widget():
    widget = ConditionWidget('test')
    form = Form(method='post', use_tokens=False, enctype='application/x-www-form-urlencoded')
    form.widgets.append(widget)
    assert PyQuery(str(form.render()))('[name="test$type"]').val() == 'django'
    assert PyQuery(str(form.render()))('[name="test$type"]').attr.type == 'hidden'

    widget = ConditionWidget('test')
    mock_form_submission(req, widget, {'test$value_django': 'hello == 1'})
    assert widget.parse() == {'type': 'django', 'value': 'hello == 1'}
    assert not widget.has_error()

    widget = ConditionWidget('test')
    mock_form_submission(req, widget, {'test$value_django': ''})
    assert widget.parse() is None
    assert not widget.has_error()

    widget = ConditionWidget('test')
    mock_form_submission(req, widget, {'test$value_django': '{{ form_var_xxx }}'})
    assert widget.has_error()
    assert widget.get_error() == "syntax error: Could not parse the remainder: '{{' from '{{'"


def test_emoji_button():
    # textual button
    form = Form(use_tokens=False)
    form.add_submit('submit', 'Submit')
    assert PyQuery(str(form.render()))('button').attr.name == 'submit'
    assert not PyQuery(str(form.render()))('button').attr['aria-label']
    assert PyQuery(str(form.render()))('button').text() == 'Submit'

    # emoji + text
    form = Form(use_tokens=False)
    form.add_submit('submit', '✅ Submit')
    assert PyQuery(str(form.render()))('button').attr.name == 'submit'
    assert PyQuery(str(form.render()))('button').attr['aria-label'] == 'Submit'
    assert PyQuery(str(form.render()))('button').text() == '✅ Submit'

    # single emoji (do not do this) (no empty aria-label)
    form = Form(use_tokens=False)
    form.add_submit('submit', '✅')
    assert PyQuery(str(form.render()))('button').attr.name == 'submit'
    assert not PyQuery(str(form.render()))('button').attr['aria-label']
    assert PyQuery(str(form.render()))('button').text() == '✅'


def test_error_templates():
    widget = TextWidget('test', value='foo', maxlength=10)
    widget_html = str(widget.render())
    assert 'data-use-live-server-validation=' not in widget_html
    assert PyQuery(widget_html)('#error_test_valueMissing').text() == 'required field'
    assert PyQuery(widget_html)('#error_test_tooLong').text() == 'too many characters (limit is 10)'

    widget.use_live_server_validation = True
    widget_html = str(widget.render())
    assert 'data-use-live-server-validation=' in widget_html
    assert PyQuery(widget_html)('#error_test_valueMissing').text() == 'required field'
    assert PyQuery(widget_html)('#error_test_tooLong').text() == 'too many characters (limit is 10)'


def test_numeric_widget():
    widget = NumericWidget('test')
    assert 'inputmode="decimal"' in str(widget.render_content())
    mock_form_submission(req, widget, {'test': ' 5 '})
    assert not widget.has_error()
    assert widget.parse() == decimal.Decimal(5)

    widget = NumericWidget('test')
    mock_form_submission(req, widget, {'test': ' 2.5 '})
    assert not widget.has_error()
    assert widget.parse() == decimal.Decimal('2.5')

    widget = NumericWidget('test')
    mock_form_submission(req, widget, {'test': ' 2,5 '})
    assert not widget.has_error()
    assert widget.parse() == decimal.Decimal('2.5')

    widget = NumericWidget('test')
    mock_form_submission(req, widget, {'test': 'xxx'})
    assert widget.has_error()
    assert widget.get_error() == 'You should enter a number, for example: 123.'

    widget = NumericWidget('test', min_value=decimal.Decimal('1E+1'))
    mock_form_submission(req, widget, {'test': '0'})
    assert widget.has_error()
    assert widget.get_error() == 'You should enter a number greater than or equal to 10.'

    widget = NumericWidget('test', min_value=decimal.Decimal('1E+1'))
    mock_form_submission(req, widget, {'test': '5'})
    assert widget.has_error()
    assert widget.get_error() == 'You should enter a number greater than or equal to 10.'

    widget = NumericWidget('test', max_value=10)
    mock_form_submission(req, widget, {'test': '15'})
    assert widget.has_error()
    assert widget.get_error() == 'You should enter a number less than or equal to 10.'

    widget = NumericWidget('test', restrict_to_integers=True)
    assert 'inputmode="numeric"' in str(widget.render_content())
    mock_form_submission(req, widget, {'test': '5.5'})
    assert widget.has_error()
    assert widget.get_error() == 'You should enter a number without a decimal separator.'

    widget = NumericWidget('test', restrict_to_integers=True)
    mock_form_submission(req, widget, {'test': 'abc'})
    assert widget.has_error()
    assert widget.get_error() == 'You should enter digits only, for example: 123.'

    # existing invalid values
    widget = NumericWidget('test', max_value=10)
    widget.set_value('01.02.03')
    mock_form_submission(req, widget, {'test': '01.02.03'})
    assert widget.has_error()
    assert widget.get_error() == 'You should enter a number, for example: 123.'

    widget = NumericWidget('test', max_value=10)
    widget.value = 'error'
    mock_form_submission(req, widget, {'test': 'error'})
    assert widget.has_error()
    assert widget.get_error() == 'You should enter a number, for example: 123.'


def test_css_classes_widget():
    for value, result, has_error in (
        ('', None, False),
        ('foo', 'foo', False),
        ('  foo  ', 'foo', False),
        ('foo bar', 'foo bar', False),
        ('foo Bar foo-bar foo_2bar', 'foo Bar foo-bar foo_2bar', False),
        ('{% newline %}', None, True),
    ):
        widget = CssClassesWidget('test', required=False)
        mock_form_submission(req, widget, {'test': value})
        assert widget.has_error() is has_error
        if not has_error:
            assert widget.parse() == result
